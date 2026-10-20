from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import random
import time
from collections import Counter

import httpx

from .config import Settings
from .store import Store, uid

logger = logging.getLogger(__name__)


class Dispatcher:
    def __init__(self, store: Store, settings: Settings, client: httpx.AsyncClient | None = None):
        self.store, self.settings = store, settings
        self.client = client or httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False)
        self.last_progress = 0.0
        self.wake = asyncio.Event()
        self.expire()

    def expire(self) -> None:
        """Suppress stale notifications, retaining local events for inspection.

        Legacy outboxes did not record source age; resolve it from their sequence
        range so a freshly batched historical event cannot acquire a fresh TTL.
        """
        cutoff = time.time() - self.settings.webhook_max_event_age_sec
        with self.store.db:
            self.store.db.execute('UPDATE events SET batched=1 WHERE batched=0 AND time<?', (cutoff,))
            for row in self.store.all("SELECT id,payload FROM deliveries WHERE status='pending'"):
                payload = json.loads(row['payload'])
                event_time = payload.get('event_time')
                if event_time is None:
                    source = self.store.one(
                        'SELECT MAX(time) AS time FROM events WHERE session_id=? AND seq BETWEEN ? AND ?',
                        (payload.get('session_id'), payload.get('from_seq'), payload.get('to_seq')))
                    event_time = source['time'] if source and source['time'] is not None else payload['timestamp']
                if event_time < cutoff:
                    self.store.db.execute("UPDATE deliveries SET status='expired' WHERE id=?", (row['id'],))

    def batch(self, force: bool = False) -> None:
        """Atomically move events into a durable outbox. Metadata only leaves host."""
        self.expire()
        now = time.time()
        progress = force or now - self.last_progress >= self.settings.progress_interval
        rows = self.store.all('SELECT * FROM events WHERE batched=0' + ('' if progress else ' AND urgent=1') + ' ORDER BY seq LIMIT 1000')
        if progress:
            self.last_progress = now
        if not rows:
            return
        groups: dict[str, list[dict]] = {}
        for r in rows:
            # An urgent event is its own delivery; progress is grouped by session.
            key = r['id'] if r['urgent'] else r['session_id']
            groups.setdefault(key, []).append(r)
        with self.store.db:
            for group in groups.values():
                did = uid()
                payload = {'event_id': did, 'session_id': group[0]['session_id'], 'timestamp': now,
                           'event_time': max(r['time'] for r in group),
                           'from_seq': group[0]['seq'], 'to_seq': group[-1]['seq'],
                           'events': [{'id': r['id'], 'seq': r['seq'], 'kind': r['kind'], 'time': r['time']} for r in group if r['urgent']],
                           'summary': dict(Counter(r['kind'] for r in group))}
                self.store.db.execute('INSERT INTO deliveries(id,payload,due) VALUES(?,?,?)', (did,json.dumps(payload),now))
                self.store.db.executemany('UPDATE events SET batched=1 WHERE seq=?', [(r['seq'],) for r in group])

    async def tick(self) -> None:
        if not self.settings.webhook_url:
            return
        self.batch()
        rows = self.store.all("SELECT * FROM deliveries WHERE status='pending' AND due<=? ORDER BY due LIMIT 20", (time.time(),))
        for r in rows:
            timestamp = str(int(time.time()))
            body = r['payload'].encode()
            signature = hmac.new(self.settings.webhook_secret.encode(), timestamp.encode()+b'.'+body, hashlib.sha256).hexdigest()
            headers = {
                'Content-Type': 'application/json', 'X-Bridge-Event-ID': r['id'],
                'X-Bridge-Timestamp': timestamp, 'X-Bridge-Signature': 'sha256='+signature,
            }
            if auth := self.settings.webhook_auth:
                headers[auth[0]] = auth[1]
            ok = False
            try:
                response = await self.client.post(self.settings.webhook_url, content=body, headers=headers)
                ok = 200 <= response.status_code < 300
            except httpx.HTTPError:
                pass
            attempts = r['attempts'] + 1
            status = 'delivered' if ok else ('dead' if attempts >= self.settings.webhook_attempts else 'pending')
            due = time.time() + min(3600, 2 ** min(attempts, 12)) * random.uniform(0.8, 1.2)
            self.store.execute('UPDATE deliveries SET attempts=?,status=?,due=? WHERE id=?', (attempts,status,due,r['id']))
            logger.info('webhook_delivery', extra={'event_id': r['id'], 'delivery_status': status})

    async def run(self) -> None:
        while True:
            await self.tick()
            self.wake.clear()
            try:
                await asyncio.wait_for(self.wake.wait(), 0.25)
            except asyncio.TimeoutError:
                pass

    def replay(self, sid: str, cursor: int) -> int:
        return self.store.execute('UPDATE events SET batched=0 WHERE session_id=? AND seq>?', (sid,cursor)).rowcount
