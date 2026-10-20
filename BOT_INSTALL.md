# Install for a Grok Bot

These steps run on the bot's host, with the Mac operator supplying connection
configuration. Do not request a human token, account login or the complete Mac
`.env`. The bot cannot obtain the Mac operator's secrets or create a routine for
another bot without access to that bot's environment.

1. Clone `https://github.com/senscho/grok-codex-bridge-public` and open
   `connectors/grok-mcp`. Create a Python 3.12+ virtual environment and run
   `python -m pip install -r requirements.txt` in it.
2. Register a **stdio MCP server** using that environment's absolute Python path
   and the absolute `server.py` path. There is no hosted MCP endpoint to register.
   Supply nonsecret launch variables `CODEX_TUNNEL_URL` (HTTPS origin) or
   `CODEX_TUNNEL_URL_FILE` (a private file on this bot host), `BRIDGE_PATH_PREFIX`
   (default `/codex`) and `CODEX_ALLOWED_ROOT` (the operator's absolute Mac project
   root). A direct tunnel without path rewriting needs an empty prefix.
3. Store the operator-provided **Grok token** as the `BRIDGE_GROK_TOKEN` secret and
   inject it into the MCP process environment. Never print it, embed it in prompts,
   or ask for `BRIDGE_HUMAN_TOKEN`. Restart the MCP server after changing launcher
   variables. URL-file contents are reread on each call.
4. Import the three [bot skills](#skills-and-backup-monitoring) and create a
   webhook-triggered alert routine **on the bot that owns Codex work** using
   the bot platform's routine management interface. Configure the routine to
   accept bridge JSON metadata (notable-only by default), deduplicate `event_id`, examine `events` and
   `summary`, and fetch current state using `codex_status`/`codex_logs`.
   Verify `X-Bridge-Signature` and timestamp freshness if the receiver supports it:
   the signature is `sha256=` plus HMAC-SHA256 over
   `X-Bridge-Timestamp + "." + raw_request_body`, using the shared webhook signing
   secret. Acknowledge duplicate deliveries successfully. Use receiver
   authentication even when signature verification is unavailable.
5. Show the user that routine's webhook URL and **exact Authorization header**
   through the platform's secure configuration handoff. Ask the user to paste
   them into the Mac's private `.env` as `BRIDGE_WEBHOOK_URL` and
   `BRIDGE_WEBHOOK_AUTH_HEADER='Authorization: RECEIVER_VALUE'`. The value is the
   receiver's credential, not either bridge bearer token. The Mac operator also
   configures `BRIDGE_WEBHOOK_SECRET` and restarts the daemon. Never commit those
   values. Do not substitute another bot's routine or an invented URL.
6. Test with `codex_accounts` and `codex_list`. Start one harmless task in an
   allowed repo using an explicit stable idempotency key. Save returned IDs;
   follow up with `codex_send`, reusing the session. Verify that the chosen bot
   receives the routine invocation and that approval acceptance stays local.

**Which bot wakes?** `BRIDGE_WEBHOOK_URL` + `BRIDGE_WEBHOOK_AUTH_HEADER` point at
**ONE bot's webhook routine**. Only that bot wakes when the bridge posts events.
Installing the MCP connector on several bots does not subscribe them all to
webhooks. Other bots learn only via an explicit relay from the receiving bot.
The user should pick the bot that owns Codex work and paste **that routine's** URL
and Authorization header into the Mac `.env`. Do not relay secrets or raw logs.

Suggested routine behavior:

```text
Deduplicate event_id. Fetch current session state.
Alert for approval/required, approval/held, account/authentication_required,
account/authentication_expired, process/lost or job/uncertain.
For approval/held, fetch logs and say what was held and the deny/unknown reason.
For usage/limitNear or usage/limitReached, report once with reset time if known.
Send a short wrap-up for turn/completed or current failed turns/errors.
Ignore resolved/stale conditions after checking current state.
Ignore progress-only and ordinary deliveries older than 15 minutes from source
event_time. For approval/required or approval/held, check current state first:
a still-unresolved approval is exempt from expiry.
Never accept an approval or request the human token.
Never automatically resend interrupted/uncertain work.
```

The bridge's internal `urgent` classification covers several state changes.
Use the alert template to classify them; “urgent delivery” alone is not a reason
to alert a human. Approval decisions happen on the Mac via
`grok-codex attach SESSION_ID` and `/approve REQUEST_ID accept|decline|cancel`.

Ordinary notifications older than `BRIDGE_WEBHOOK_MAX_EVENT_AGE_SEC` (default 15
minutes from source event time) expire before delivery/retry, including across
restart. Unresolved approval/required and approval/held notifications are exempt. Poll status after a long
outage. Idle sessions can resume automatically; in-flight turns are interrupted
and never automatically replayed. A web viewer and automatic tmux-detach detection are **planned**. Explicit
bridge-terminal exit (`/quit`, `/detach`, EOF or Ctrl-C) emits `session/detached`;
it does not transfer ownership or mean the Codex process stopped.

## Skills and backup monitoring

Import each folder into your bot's skill library:

- [codex-delegate](skills/codex-delegate/SKILL.md)
- [codex-approval-alert](skills/codex-approval-alert/SKILL.md)
- [codex-session-watchdog](skills/codex-session-watchdog/SKILL.md)

Use the alert skill as the webhook routine body. Also create a scheduled routine
**every 10 minutes** with the watchdog body, on the same owning bot. Webhooks can
fail or expire; the watchdog independently lists sessions and polls status/logs.
It alerts once for approvals older than 5 minutes, unreported finished turns,
active sessions without events for over 20 minutes and current errors. It remains
silent when there is nothing new to report. Baseline historical completions on
first installation.

Give both routines and the delegating bot the same private durable report ledger:
per-session cursors, reported terminal job/turn IDs, active incident fingerprints
and processed webhook IDs. Atomically claim each report before sending so the
routines do not duplicate it. Importing skills does not create triggers, schedules
or storage; configure those with the bot platform's routine tools. If shared
storage is unavailable, report that gap instead of promising deduplication.

The delegate skill asks normal versus YOLO before a new session. A bot may accept
only with explicit owner chat authorization for that session; this connector has
no accept capability, so consent does not enable bot acceptance here. Never ask
for the human token. An owner choosing YOLO runs
[`scripts/auto-approve.sh SESSION_ID --enable-yolo`](scripts/auto-approve.sh)
locally on the Mac. It uses local human credentials and weakens human review;
deny rules are heuristic and cannot uncover all hidden side effects. It leaves
denied/unknown requests pending, exits on idle or Ctrl-C, and needs a new opt-in
for later turns. See the [risks and usage](README.md#optional-per-session-yolo-helper).
Neither notification routine ever accepts approvals, even in YOLO sessions.

## Quiet webhook configuration

On the Mac, `BRIDGE_WEBHOOK_PROGRESS=0` is the default. Only kinds selected by
`BRIDGE_WEBHOOK_EVENTS` cause routine deliveries; the default includes approvals,
YOLO holds, finished turns/errors, authentication expiry/requirements, lost
processes/uncertain jobs, explicit terminal detach and near/exhausted usage limits.
See the full list and protocol mapping in the [README](README.md#notable-only-delivery-by-default)
and [.env.example](.env.example). Changes require a daemon restart. Legacy queued
progress-only deliveries are suppressed when progress is off. Do not opt into
`BRIDGE_WEBHOOK_PROGRESS=1` unless the routine can handle interval-based wakes.
`BRIDGE_PROGRESS_INTERVAL` applies only to that optional progress batching.

`BRIDGE_USAGE_ALERT_PERCENT=90` controls the near-limit threshold. Near alerts are
deduplicated per account/window across sessions and restart. Exhaustion reports
use known reset times; missing reset metadata is not a reason to invent a time or
retry work. Neither notification routine spends credits, switches accounts or
accepts approvals. A YOLO hold is a notification and leaves the request pending;
only the owner resolves it locally.
