# Validation

Run the bridge and connector contract checks with the locked bridge environment:

```sh
uv run --locked pytest -q
```

The optional installed-Codex lifecycle test is disabled by default. It uses an
isolated, unauthenticated `CODEX_HOME` and does not read existing credentials:

```sh
RUN_CODEX_INTEGRATION=1 uv run --locked pytest -q -m integration
```

The suite covers durable sessions, account isolation, ownership, approvals,
steering, interruptions, restart reconciliation without prompt replay, HTTP
schema/auth/idempotency, webhook signatures/auth/coalescing/retries and expiry,
and demo recovery. Connector tests stub only MCP registration and exercise real
configuration, path checks and HTTP handling with mocked transport.

The backlog regression covers old unbatched events, persisted retries, legacy
outbox payloads with misleading batching timestamps, explicit replay, expiry
while running, retained local history and delivery of fresh urgent events.
Nonpositive and nonfinite expiry configuration is rejected.

Other useful checks:

```sh
shellcheck -s bash scripts/*.sh
bash scripts/check_prereqs.sh   # Mac, read-only
bash scripts/check_box.sh       # bot host, read-only
uv run --locked python scripts/export_contracts.py
uv run --locked python -m compileall -q grok_codex examples connectors
```

Automated tests do not establish successful model execution, live bot platform
routine creation, production tunnel connectivity or authenticated coding. The
Mac operator should complete a harmless task, review an approval locally, verify
the chosen bot's webhook delivery and inspect recovery before using real work.

Native viewing was verified separately: `codex resume THREAD_ID` with the same
`CODEX_HOME` opens an app-server-held thread read-only ("opened elsewhere"). The
bridge terminal remains the approval/control surface. Full history API support
is version dependent; retained bridge events are the fallback.

The optional local approval helper has tests for deny rules, quoted commands,
separate removal flags, sensitive paths, symlink escapes, complete file metadata,
per-session isolation, held/stale requests, idle exit and API failure. Approval
metadata remains redacted and Grok credentials still cannot accept approvals.

Notable-only dispatch tests cover every default kind, suppression of legacy
progress queues, custom filters, progress opt-in, source-time expiry, unresolved
approval preservation and priority. Usage tests use retained schema field names
and cover account/window deduplication across sessions/restart, sparse updates,
quota-error/failed-turn correlation and known reset times. Held approvals have
helper, human-only endpoint, redaction, one-event-per-request and unchanged
pending-status coverage. During this follow-up only targeted tests run before
one final locked full-suite run.
