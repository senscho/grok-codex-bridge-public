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
`event_id`. Use source `event_time` (or the event times, with body `timestamp` as a legacy
fallback) to ignore ordinary deliveries older than 900 seconds. A newly signed
retry is not a fresh event. Exception: for `approval/required` and `approval/held`,
fetch current state before rejecting an old delivery; an unresolved approval must
still reach the human. Ignore implausible future timestamps and progress-only deliveries. Inspect both
`events[].kind` and `summary` keys. Stale deliveries may be acknowledged without
reporting; the scheduled watchdog recovers missed state.

For a relevant event, fetch current `codex_status` and consume `codex_logs` from
the shared per-session cursor, paging until caught up. Only current conditions
merit an alert:

- `approval/required`: an approval is still pending. Give session, request ID,
  brief redacted reason and the Mac attach command. Never accept it.
- `approval/held`: the local YOLO helper left a denied or unknown request pending.
  Say **what was held and why**, using the redacted command and hold reason from
  `codex_logs`; never quote secrets or raw output. Use the request ID to correlate
  with its approval/required alert, sending at most one hold update per request.
  If missing from the saved-cursor cache, page retained logs to find the hold.
- `account/authentication_required` / `account/authentication_expired`: login expired/failed; ask the owner to log
  in locally with that account's `CODEX_HOME`. Never ask for credentials.
- `process/lost` / `job/uncertain`: the process is disconnected; report interruption and recommend
  inspecting state before explicit resume. Never replay the task.
- `turn/completed` / `turn/failed`: send a short wrap-up keyed by turn/job ID, including the actual
  completed/failed/interrupted status, meaningful result and checks when known.
- `error`: report a current failure/blocker. A recovered transient error does not
  merit a new failure alert. Correlate it with the job/turn to avoid duplicate
  failed-turn and error reports.

- `usage/limitNear`: one account/window warning that usage crossed the configured
  percent (default 90). Report percent and reset time when provided, without
  repeating the warning for other repo sessions on the same account/window.
- `usage/limitReached`: exhausted usage/rate/credit allowance. Explain the blocker
  and reset time if known. Treat `known_window_resets` as observations, not a
  guarantee that a particular reset clears this error. Never invent a reset time, spend credits, switch
  accounts or automatically retry failed work.
- `session/detached`: the bridge terminal exited; record this once, without
  assuming ownership returned to the bot or that the process stopped. Automatic
  tmux `Ctrl-b d` detection is not implemented.

The bridge defaults to `BRIDGE_WEBHOOK_PROGRESS=0`, and only selected
`BRIDGE_WEBHOOK_EVENTS` are delivered. Keep both approval kinds enabled. No
progress webhook is needed for ordinary observation; use cursors/watchdog.

Persist shared notification state in the bot platform's private durable storage:
processed delivery IDs, per-session cursors, reported terminal job/turn IDs and
active alert fingerprints (approval request ID plus required/held phase, usage
window, auth incident, disconnection or
error). Atomically claim a report before sending; share the same ledger with
`codex-session-watchdog` and the delegating bot. Clear an active alert on recovery
so a later incident can be reported. If shared durable state is unavailable,
report the setup problem once rather than claiming duplicate suppression works.

No progress chatter. No secrets in notifications. No approval acceptance, session
mutation, automatic task restart, mode changes or human-token access, including
for sessions whose owner enabled the local YOLO helper.
