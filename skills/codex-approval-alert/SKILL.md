---
name: codex-approval-alert
description: Webhook routine template for Codex approval, authentication, disconnection and turn completion notifications; never accepts approvals.
---

# Webhook routine: Codex alerts

Create a webhook-triggered routine on the bot that owns Codex work, with the MCP
connector available. Importing this skill does not create the routine. The Mac's
`BRIDGE_WEBHOOK_URL` and `BRIDGE_WEBHOOK_AUTH_HEADER` target this ONE bot's routine;
other bots learn only through an explicit relay. Never relay secrets or raw logs.

Authenticate the receiver, verify signatures where supported and deduplicate
`event_id`. Ignore deliveries whose body `timestamp` is more than 900 seconds old
(use `event_time` too when present); a newly signed retry is not a fresh event.
Ignore implausible future timestamps and progress-only deliveries. Inspect both
`events[].kind` and `summary` keys. Stale deliveries may be acknowledged without
reporting; the scheduled watchdog recovers missed state.

For a relevant event, fetch current `codex_status` and consume `codex_logs` from
the shared per-session cursor, paging until caught up. Only current conditions
merit an alert:

- `approval/required`: an approval is still pending. Give session, request ID,
  brief redacted reason and the Mac attach command. Never accept it.
- `account/authentication_required`: login expired/failed; ask the owner to log
  in locally with that account's `CODEX_HOME`. Never ask for credentials.
- `process/lost`: the process is disconnected; report interruption and recommend
  inspecting state before explicit resume. Never replay the task.
- `turn/completed`: send a short wrap-up keyed by turn/job ID, including the actual
  completed/failed/interrupted status, meaningful result and checks when known.
- `error`: report a current failure/blocker. A recovered transient error does not
  merit a new failure alert. Correlate it with the job/turn to avoid duplicate
  failed-turn and error reports.

Persist shared notification state in the bot platform's private durable storage:
processed delivery IDs, per-session cursors, reported terminal job/turn IDs and
active alert fingerprints (approval request ID, auth incident, disconnection or
error). Atomically claim a report before sending; share the same ledger with
`codex-session-watchdog` and the delegating bot. Clear an active alert on recovery
so a later incident can be reported. If shared durable state is unavailable,
report the setup problem once rather than claiming duplicate suppression works.

No progress chatter. No secrets in notifications. No approval acceptance, session
mutation, automatic task restart, mode changes or human-token access, including
for sessions whose owner enabled the local YOLO helper.
