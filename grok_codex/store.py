from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any
from uuid import uuid4


def uid() -> str:
    return str(uuid4())


class Store:
    """Single event-loop connection; transactions never span awaits."""

    def __init__(self, path: Path):
        self.db = sqlite3.connect(path)
        path.chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=FULL;
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY, home TEXT UNIQUE NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(
          id TEXT PRIMARY KEY, thread_id TEXT, repo TEXT NOT NULL, account TEXT NOT NULL,
          status TEXT NOT NULL, owner TEXT NOT NULL DEFAULT 'grok', turn_id TEXT,
          pid INTEGER, process_started REAL, options TEXT NOT NULL, created REAL NOT NULL,
          FOREIGN KEY(account) REFERENCES accounts(id));
        CREATE TABLE IF NOT EXISTS jobs(
          id TEXT PRIMARY KEY, session_id TEXT NOT NULL, operation TEXT NOT NULL,
          status TEXT NOT NULL, turn_id TEXT, created REAL NOT NULL, detail TEXT);
        CREATE TABLE IF NOT EXISTS requests(
          actor TEXT NOT NULL, key TEXT NOT NULL, fingerprint TEXT NOT NULL,
          result TEXT NOT NULL, PRIMARY KEY(actor,key));
        CREATE TABLE IF NOT EXISTS events(
          seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
          session_id TEXT NOT NULL, kind TEXT NOT NULL, time REAL NOT NULL,
          data TEXT NOT NULL, urgent INTEGER NOT NULL, batched INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS approvals(
          id TEXT PRIMARY KEY, session_id TEXT NOT NULL, rpc_id TEXT NOT NULL,
          method TEXT NOT NULL, status TEXT NOT NULL, decision TEXT, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS usage_state(account TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS usage_alerts(key TEXT PRIMARY KEY);
        CREATE INDEX IF NOT EXISTS events_outbox ON events(batched,kind,seq);
        CREATE TABLE IF NOT EXISTS deliveries(
          id TEXT PRIMARY KEY, payload TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
          due REAL NOT NULL, status TEXT NOT NULL DEFAULT 'pending');
        ''')

    def execute(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        c = self.db.execute(sql, args)
        self.db.commit()
        return c

    def all(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def one(self, sql: str, args: tuple = ()) -> dict[str, Any] | None:
        rows = self.all(sql, args)
        return rows[0] if rows else None

    def session(self, sid: str) -> dict[str, Any]:
        r = self.one('SELECT * FROM sessions WHERE id=?', (sid,))
        if r is None:
            raise KeyError('Unknown session')
        r['options'] = json.loads(r['options'])
        return r

    def update(self, sid: str, **fields: Any) -> None:
        allowed = {'thread_id','account','status','owner','turn_id','pid','process_started'}
        assert fields.keys() <= allowed
        self.execute('UPDATE sessions SET '+','.join(k+'=?' for k in fields)+' WHERE id=?', (*fields.values(),sid))

    def event(self, sid: str, kind: str, data: dict | None = None, urgent: bool = False) -> int:
        return self.execute('INSERT INTO events(id,session_id,kind,time,data,urgent) VALUES(?,?,?,?,?,?)',
                            (uid(), sid, kind, time.time(), json.dumps(data or {}), int(urgent))).lastrowid

    def logs(self, sid: str, cursor: int, limit: int) -> dict:
        rows = self.all('SELECT * FROM events WHERE session_id=? AND seq>? ORDER BY seq LIMIT ?', (sid,cursor,limit))
        for r in rows:
            r['data'] = json.loads(r['data'])
        return {'events': rows, 'cursor': rows[-1]['seq'] if rows else cursor}

    def job(self, sid: str, operation: str) -> str:
        jid = uid()
        self.execute('INSERT INTO jobs(id,session_id,operation,status,created) VALUES(?,?,?,?,?)', (jid,sid,operation,'queued',time.time()))
        return jid
