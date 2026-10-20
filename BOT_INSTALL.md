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
4. Create a webhook-triggered routine **on the bot that owns Codex work** using
   the bot platform's routine management interface. Configure the routine to
   accept bridge JSON metadata, deduplicate `event_id`, examine `events` and
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
Alert the human only for approval/required, account/authentication_required,
or process/lost (approval pending, login expired, process disconnected).
Ignore resolved/stale conditions after checking current state.
For progress/completion, update internal task state quietly; relay only as needed.
Never accept an approval or request the human token.
Never automatically resend interrupted/uncertain work.
```

The bridge's internal `urgent` classification also covers completion and other
state changes. Filter for the three conditions above; “urgent delivery” is not
itself a reason to alert a human. Approval decisions happen on the Mac via
`grok-codex attach SESSION_ID` and `/approve REQUEST_ID accept|decline|cancel`.

Notifications older than `BRIDGE_WEBHOOK_MAX_EVENT_AGE_SEC` (default 15 minutes)
expire before delivery/retry, including across restart. Poll status after a long
outage. Idle sessions can resume automatically; in-flight turns are interrupted
and never automatically replayed. A web viewer and detach webhook event are
**planned**, so do not build routine logic that expects either today.
