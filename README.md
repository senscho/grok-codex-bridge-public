> **Grok Bot / AI agents: follow [BOT_INSTALL.md](BOT_INSTALL.md).**

# Grok–Codex Bridge

Run persistent Codex coding sessions on your Mac from a Grok Bot. The bridge keeps
repo sessions, jobs, ownership, approvals and event delivery durable across daemon
restarts. This is a community bridge, not an official Grok or OpenAI integration.

[Visual flow](docs/flow.html) · [Mac setup](docs/setup-en.html) ·
[Bot installation](BOT_INSTALL.md) · [Security](SECURITY.md)

```text
Grok Bot → stdio MCP connector → HTTPS tunnel /codex → bridge on Mac
                                                        ↕
                                              Codex app-server (stdio)
                                              per session / repo
                                                        ↓
Bot routine ← signed metadata webhook ← durable event outbox
Human terminal → attach → review /approve → takeover / return control
```

Each session has a persistent Codex thread and its own child app-server process.
Several sessions may target a repo; avoid concurrent edits to the same files.
SQLite records jobs, retained events and an outbox. Mutations require stable
idempotency keys; an uncertain response never authorizes automatic prompt replay.
Each registered account gets its own `CODEX_HOME`, including credentials/history.

## Install on a Mac

Install Homebrew first if needed. `bash scripts/check_prereqs.sh` (read-only)
reports what is missing. Then:

```sh
brew install uv tmux
brew install --cask codex
git clone https://github.com/senscho/grok-codex-bridge-public.git
cd grok-codex-bridge-public
bash scripts/bootstrap.sh
uv run --locked grok-codex register-account personal
```

Bootstrap installs locked dependencies, generates a private `.env` and runs tests.
Run the exact `CODEX_HOME=... codex login` command printed by registration. Open
`.env` locally; set `BRIDGE_REPO_ROOTS` to your project root (multiple roots use
colon separators on macOS). Keep `BRIDGE_STATE_DIR` outside coding repositories.
Run one daemon, in a terminal or as a LaunchAgent
(`bash scripts/render_launchd.sh bridge --repo "$PWD"` prints the plist; install only
one bridge LaunchAgent, see BOT_INSTALL stage 3):

```sh
uv run --locked grok-codex daemon
```

The default listener is `127.0.0.1:48765`. CLI commands load the same `.env` from
the current directory; use `--config /absolute/path/to/private-config` before the
subcommand when launching elsewhere. Keep configuration permissioned `0600`.
The demo is optional: `bash scripts/demo.sh --help`.

## Tunnel and bot connection

Use an HTTPS tunnel to loopback, never a public app-server listener. Options:

- A stable named Cloudflare tunnel with a domain you control.
- A temporary tunnel for testing; update the connector URL/file when it changes.
- An authenticated private network or SSH forwarding with an HTTPS gateway.

The connector defaults to `/codex`. A path-stripping proxy must translate
`/codex/tools/...` to the bridge's `/tools/...`; a direct tunnel to the daemon
instead needs `BRIDGE_PATH_PREFIX=""`. The [setup guide](docs/setup-en.html)
and [examples/Caddyfile](examples/Caddyfile) provide a Caddy configuration for the default path. Tunnel URLs are private
runtime configuration and never belong in this repository.

Install [connectors/grok-mcp](connectors/grok-mcp/README.md) on the bot host. Set
`CODEX_TUNNEL_URL` (or `CODEX_TUNNEL_URL_FILE`), `BRIDGE_PATH_PREFIX`,
`CODEX_ALLOWED_ROOT` and the `BRIDGE_GROK_TOKEN` secret. The root is the Mac path;
the bridge separately enforces canonical paths under `BRIDGE_REPO_ROOTS`.

## Tokens and approvals

`init` creates two distinct random bearer tokens. The **HUMAN token stays on the
Mac and is never given to bots**. Only the **Grok token** goes to the bot's secret
store. It permits coding requests and logs, not accepting approvals, registering
accounts or taking human ownership. Do not share your whole `.env`.

The defaults are `workspace-write`, `on-request` and the human approval reviewer.
The API accepts only `workspace-write`/`read-only` and `on-request`/`untrusted`;
it offers no unrestricted sandbox or automatic-approval mode. A bot can decline
or cancel a pending approval. By default, a human accepts it explicitly in the local terminal.
An optional owner-run YOLO helper can accept on that human's behalf for one session;
see its risks below.
Human approval applies to operations Codex flags; it is not a promise to ask
before every edit or every permitted command.

## Work, attach and take over

Have the bot call `codex_start` once for a repo/task, then `codex_send` for
follow-ups. Save session/job IDs and poll `codex_status`/`codex_logs`. Supply a
stable idempotency key for mutations and reuse it after a lost response.

On the Mac:

```sh
uv run --locked grok-codex list
uv run --locked grok-codex attach SESSION_ID
# Without tmux:
uv run --locked grok-codex terminal SESSION_ID
```

Attach watches retained/live bridge events. It does not automatically take
ownership. The terminal supports:

- `/status`, `/interrupt`, `/resume` to inspect or manage the current session.
- `/approve REQUEST_ID accept` (or `decline` / `cancel`) after reviewing the request.
- `/takeover` for explicit human ownership; ordinary text sends follow-ups.
- `/steer TEXT` to steer an active turn; ordinary sends reject busy sessions.
- `/grok` to return ownership to the bot; `/quit` to leave the terminal.

In tmux, `Ctrl-b d` detaches the viewer and keeps the bridge/agent alive. Returning
ownership and detaching are separate actions. Exiting the bridge terminal with
`/quit`, `/detach`, EOF or Ctrl-C emits `session/detached` (best effort if the bridge
is unreachable). Automatic detection of `Ctrl-b d` is still **planned**.

## Viewing sessions safely

Use `codex resume THREAD_ID` with the **same `CODEX_HOME`** as that session's
registered account. Get the thread ID from bridge status and the home from local
account registration/listing. Example:

```sh
CODEX_HOME=/path/to/registered/account codex resume THREAD_ID
```

Verified behavior: Codex's built-in protection opens a thread currently held by
the bridge's app-server as **READ-ONLY** (“opened elsewhere”). Parallel viewing
is safe under that protection. Keep bridge attach as the approval/control surface;
other Codex versions or transports may behave differently.

For a tmux live view, configure a large history limit **before creating the
session/window**, since existing pane history is not enlarged retroactively:

```sh
# Add this to ~/.tmux.conf before creating the viewer window:
set-option -g history-limit 100000
```

After creating the bridge attach window, resize it to the viewer's columns/rows:

```sh
tmux resize-window -t grok-codex-SESSION_ID -x C -y R
tmux capture-pane -t grok-codex-SESSION_ID -p -e -J -S - -E -
```

Replace `C`/`R` with numeric dimensions. tmux renders at its own window size;
resizing only the viewer does not reflow tmux output. The capture preserves ANSI
colors (`-e`), joins wrapped lines (`-J`) and includes all retained history. Treat
captures as private logs. A built-in web session viewer is **planned**. The current
`/dashboard` is only an authenticated metadata table, not a live transcript viewer.

## Webhooks and the receiving bot

Set `BRIDGE_WEBHOOK_URL` and `BRIDGE_WEBHOOK_AUTH_HEADER` in the **Mac's** `.env`
to **one bot's webhook routine**. Only that bot wakes. Other bots learn about work
only if the receiving bot relays it. Pick the bot that owns Codex work, then paste
that routine's URL and exact Authorization header into the Mac configuration.
Restart the daemon after configuration changes. See [BOT_INSTALL.md](BOT_INSTALL.md).

Deliveries contain event IDs, source sequence/time, kinds and safe status/usage
metadata, not prompts, source or command output. `BRIDGE_WEBHOOK_SECRET` signs the
body via HMAC; the routine header authenticates to the receiver and is separate.
Deduplicate event IDs and verify signatures/timestamp freshness where supported.
Retries keep the same event ID; delivery is at least once, not exactly once.

### Notable-only delivery by default

`BRIDGE_WEBHOOK_PROGRESS=0` stops progress-only webhooks, including queued legacy
progress deliveries after upgrade. Routine wake-ups now correspond to configured
notable events. Set `BRIDGE_WEBHOOK_EVENTS` to a comma-separated replacement list
(empty disables notable events). Defaults are:

```text
approval/required,approval/held,turn/completed,turn/failed,error,
account/authentication_required,account/authentication_expired,
process/lost,job/uncertain,session/detached,usage/limitReached,usage/limitNear
```

The bridge emits `turn/completed` with a `status` of `completed`, `failed` or
`interrupted` for every finished turn. `turn/failed` is an accepted filter kind
for producers that emit it; this bridge uses the completion/status form.
Administrative ownership changes, approval resolutions, token/output deltas and
ordinary session activity stay in local logs without waking the bot. Notable
events bypass the progress interval, and approval deliveries get priority.

To opt back into progress batching, set `BRIDGE_WEBHOOK_PROGRESS=1`;
`BRIDGE_PROGRESS_INTERVAL` then controls its interval (default 10 seconds).
This can wake the routine frequently and recreate receiver backlogs. Prefer
`codex_status`/`codex_logs` for progress and the ten-minute watchdog for backup.
The new settings are also shown in the credential-free [.env.example](.env.example).

### Usage and credits

`BRIDGE_USAGE_ALERT_PERCENT` defaults to `90`. The bridge reads
`account/rateLimits/updated.params.rateLimits`, merging sparse observations.
`primary`/`secondary` windows use the retained protocol's `usedPercent`, `resetsAt`
and `windowDurationMins` fields. `usage/limitNear` is recorded once per account,
limit ID and reset window, shared across repo sessions and daemon restarts. With
no reset timestamp, it suppresses repeat warnings for that unknown window until
a distinct window can be identified; a percent dip alone does not rearm it.

`usage/limitReached` recognizes `usageLimitExceeded`/`rateLimitExceeded`, provider
429 error variants and explicit exhausted-usage/credits errors. Exhausted windows,
`rateLimitReachedType` or `spendControlReached` also signal depletion. Reset times
come from known affected windows (`resetsAt`, exported as `resets_at`), never an
invented error field. If an error does not identify the exhausted window,
`known_window_resets` lists observed resets without claiming which one resolves
the error. `credits.hasCredits=false` alone does not mean exhaustion.
Report near-limit warnings once; on exhaustion, report the reset time if known
and ask the owner to inspect account limits. Never switch accounts or retry work
automatically. The bridge does not estimate billing credit balances.

### Expiry and unresolved approvals

`BRIDGE_WEBHOOK_MAX_EVENT_AGE_SEC` defaults to `900` (15 minutes), measured from
**source event time**, not batch, signature or retry time. Old ordinary events
and deliveries expire; logs remain. An `approval/required` or `approval/held`
event linked to an unresolved approval is exempt, even across dispatcher restart.
Once resolved/stale, the normal age limit applies. The delivery attempt limit
still applies; the watchdog covers failed/dead deliveries. An explicit event
filter can disable approval notifications, so keep both approval kinds enabled.

Routines check current state before reporting. Ignore ordinary stale/progress
notifications, but check unresolved approval/held requests before discarding an
old notification. Alert on approval/held with **what was held and why**: fetch
its redacted command/reason from logs (the webhook carries only metadata). Send
short finished/failed-turn wrap-ups and deduplicate usage/errors/incidents.
Install both the alert routine and the independent watchdog backup below.

## Importable bot skills and backup monitoring

Import these folders into the bot platform's skill library; each includes a
`SKILL.md` with name/description frontmatter. Use their instructions as routine
bodies when creating triggers. Importing files alone does not install a webhook
or schedule:

- [codex-delegate](skills/codex-delegate/SKILL.md): check reachability, ask the owner
  about normal versus YOLO **before session creation**, keep one managed session
  per repo, reuse idle sessions and follow logs with saved cursors.
- [codex-approval-alert](skills/codex-approval-alert/SKILL.md): webhook-triggered
  approval/auth/process alerts and short finished/failed-turn wrap-ups. Never accepts.
- [codex-session-watchdog](skills/codex-session-watchdog/SKILL.md): schedule every
  **10 minutes** as a backup for failed, dropped or expired webhook delivery.
  Detect approvals pending over 5 minutes, unreported finished turns, sessions
  without events for over 20 minutes and errors. Stay silent otherwise.

Install both routines on the bot that owns Codex work. They share a private,
durable ledger with the delegating bot: cursors, terminal job/turn IDs and incident
fingerprints. Atomic report claims avoid repeats when webhook and watchdog race.
The watchdog polls independently; it is not another webhook subscriber. At first
installation, baseline historical completions rather than flooding old reports.

## Optional per-session YOLO helper

Normal mode keeps human review. A bot may accept only after explicit owner
chat authorization **for that session**; this is necessary permission, not a tool
capability. The current connector cannot accept approvals even with chat consent.
Never provide the bot with the human token. For this release, the owner runs the
[local helper](scripts/auto-approve.sh) on the Mac:

```sh
bash scripts/auto-approve.sh SESSION_ID --enable-yolo
# Optional: append --config /absolute/path/to/private-config
```

This **weakens the human-approval guarantee**. It uses the Mac's human token and
watches only the selected session's structured event stream; tmux need not be
open. It accepts inspectable command/file approvals, holding requests for push,
repository/release/PR creation or merge, recursive forced removal, privilege,
download-to-shell pipelines, sensitive config/credentials, publication/deployment,
expanded permission scopes and incomplete metadata. Held request IDs and reasons
are printed once for human review via attach. The helper also records
`approval/held` through the human-only `/sessions/hold` endpoint, once per request
even across helper restarts; its redacted command and deny/unknown reason remain
in local logs, and a notable webhook wakes the bot. The helper never accepts
that held request. File approvals require complete
paths correlated to their item; older events without metadata are held.

Deny rules are conservative **heuristics, not a security boundary**. Scripts,
interpreters or indirection can hide side effects; file content is not inspected.
Use this only for trusted work in a constrained repo, keep sandbox defaults and
review changes. The helper exits when the session becomes idle or inactive, on
Ctrl-C or on an API error; it never starts a turn. A later turn needs a new opt-in.
Stopping the helper does not stop Codex or undo approvals already sent. Neither
alert nor watchdog ever accepts, including for YOLO sessions.

## Restart behavior and known issues

Idle materialized sessions return after restart. An in-flight turn becomes
interrupted/uncertain; its prompt is **not replayed**. Inspect work before giving
new instructions. Pending in-memory approvals become stale after process loss.
`recovery_blocked` means a recorded process still exists; inspect it locally.
The bridge deliberately avoids killing a possibly reused PID.

The retained protocol contract targets Codex CLI 0.163.0. Some full-history APIs
were unsupported there; bridge attach uses its retained event log and metadata-only
resume. Native read-only viewing was verified separately. Cross-account history
migration is not guaranteed; prefer an explicit summary handoff to a new thread.
A failed webhook becomes a dead letter after the configured attempt limit.
A tunnel outage or sleeping Mac delays delivery, and expiring notifications do
not resolve the underlying job. Secret redaction is best effort: keep the state
directory, logs and account homes private. No built-in web viewer or automatic tmux-detach
detection exists yet; explicit bridge-terminal exits do emit `session/detached`.

## Development

```sh
uv run --locked pytest -q
# Optional, isolated unauthenticated installed-binary test:
RUN_CODEX_INTEGRATION=1 uv run --locked pytest -q -m integration
uv run --locked python scripts/export_contracts.py
```

See [test scope](docs/TESTING.md), [protocol decisions](docs/ADR-001.md),
[SECURITY.md](SECURITY.md) and [MIT license](LICENSE).
