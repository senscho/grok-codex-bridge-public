"""Account-level quota notifications using retained app-server schema fields."""
from __future__ import annotations

import json
import re
import time


class UsageAlerts:
    def __init__(self, store, settings, emit):
        self.store, self.settings, self.emit = store, settings, emit

    def state(self, account: str) -> dict:
        row = self.store.one('SELECT data FROM usage_state WHERE account=?', (account,))
        return json.loads(row['data']) if row else {'limits': {}, 'last': 'default'}

    def once(self, key: list, sid: str, kind: str, data: dict) -> None:
        # Store.event commits the marker and event together on the same connection.
        inserted = self.store.db.execute('INSERT OR IGNORE INTO usage_alerts(key) VALUES(?)', (json.dumps(key),)).rowcount
        if inserted:
            self.emit(sid, kind, data)
        else:
            self.store.db.commit()

    def update(self, sid: str, account: str, snapshot: dict) -> None:
        state = self.state(account)
        limit_id = snapshot.get('limitId') or state['last']
        state['last'] = limit_id
        current = state['limits'].setdefault(limit_id, {})
        for name, value in snapshot.items():
            if value is None:
                continue  # Sparse metadata does not clear prior observations.
            if name in {'primary', 'secondary'} and isinstance(value, dict):
                previous = current.get(name, {})
                merged = {**previous, **{key: val for key, val in value.items() if val is not None}}
                current[name] = merged
            else:
                current[name] = value
        for name in ('primary', 'secondary'):
            window = current.get(name)
            if not isinstance(window, dict):
                continue
            used = window['usedPercent']
            window_id = window.get('resetsAt') or ['unknown', window.get('windowDurationMins')]
            metadata = {'window':name, 'used_percent':used}
            if window.get('resetsAt') is not None:
                metadata['resets_at'] = window['resetsAt']
            if window.get('windowDurationMins') is not None:
                metadata['window_duration_mins'] = window['windowDurationMins']
            if used >= self.settings.usage_alert_percent:
                self.once([account,limit_id,name,window_id,'near'], sid, 'usage/limitNear', metadata)
            if used >= 100:
                self.once([account,limit_id,name,window_id,'reached'], sid, 'usage/limitReached', {**metadata,'status':'exhausted'})
        self.store.execute('INSERT OR REPLACE INTO usage_state(account,data) VALUES(?,?)', (account,json.dumps(state)))
        # Explicit spend-control/credits depletion enum values are meaningful;
        # credits.hasCredits=false alone is normal on plans without extra credits.
        if (snapshot.get('rateLimitReachedType') or snapshot.get('spendControlReached') is True
                or (snapshot.get('individualLimit') or {}).get('remainingPercent',100) <= 0) and not any(
                (current.get(name) or {}).get('usedPercent',0) >= 100 for name in ('primary','secondary')):
            self.reached(sid, account, 'snapshot', force=True)

    def reached(self, sid: str, account: str, incident: str, error: dict | str | None = None, *, force=False) -> bool:
        info = error.get('codexErrorInfo') if isinstance(error, dict) else None
        structured = (isinstance(info, str) and info in {'usageLimitExceeded','rateLimitExceeded'}) or (
            isinstance(info, dict) and any(isinstance(value,dict) and value.get('httpStatusCode') == 429 for value in info.values()))
        text = json.dumps(error)
        exhausted = structured or bool(re.search(
            r'(?:usage|rate)[ _-]*limit.{0,80}(?:exceed|reach|exhaust)|rate[ _-]*limited|credits?.{0,60}(?:depleted|exhausted)', text, re.I))
        if not force and not exhausted:
            return False
        state = self.state(account)
        reset_candidates, window_keys, known_resets = [], [], []
        for limit_id, limit in state['limits'].items():
            for name in ('primary','secondary','individualLimit'):
                window = limit.get(name) or {}
                reset = window.get('resetsAt')
                if isinstance(reset,int) and reset > time.time():
                    known_resets.append({'window':name,'resets_at':reset})
                exhausted_window = window.get('usedPercent',0) >= 100 or window.get('remainingPercent',100) <= 0
                if exhausted_window and (reset is None or reset > time.time()):
                    if isinstance(reset,int):
                        reset_candidates.append(reset)
                    if name in {'primary','secondary'}:
                        window_keys.append([account,limit_id,name,reset or ['unknown',window.get('windowDurationMins')],'reached'])
        data = {'status':'exhausted'}
        if reset_candidates:
            data['resets_at'] = min(reset_candidates)
        elif known_resets:
            # The error does not identify a window. Report observed resets without
            # inventing which one cleared the exhausted allowance.
            data['known_window_resets'] = known_resets
        for key in window_keys or [[account,'exhausted',incident,data.get('resets_at')]]:
            self.once(key, sid, 'usage/limitReached', data)
        return True
