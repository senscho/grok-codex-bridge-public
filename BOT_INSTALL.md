# Bot install runbook

This file is written for a **Grok Bot or other AI agent** that was given this
repository URL and asked to install it. Follow the stages **in order**. Each
stage ends with a **Verify** step; do not continue until it passes. Lines marked
**ASK THE USER** are mandatory stop points. Humans can read the
[README](README.md) and [Mac setup guide](docs/setup-en.html) instead.

## What you are installing

```text
Bot (box) ── stdio MCP connector ── HTTPS tunnel ── /codex proxy ── bridge daemon ── Codex CLI
  Linux, uv      connectors/grok-mcp     (Mac)          (Mac, optional)  (Mac, 127.0.0.1:48765)
```

- **Mac**: the bridge daemon, Codex accounts, the private `.env`, the tunnel.
  You reach it only through machine-targeted Shell; **the user approves every
  command**. Group commands so the user approves a few, not dozens.
- **Box** (bot host): a clone of this repo, the MCP connector, a tunnel URL file
  and one secret, `BRIDGE_GROK_TOKEN`.

## Ground rules (apply to every stage)

1. **Never print, log or paste tokens or secrets.** Do not `cat`, `grep`, `echo` or
   `source` the Mac `.env`; read it only through the provided check script, which
   prints no values. Never ask for passwords, the Mac login, `sudo` or a Codex/ChatGPT
   login. Never put a token into a prompt, chat message, commit or file on the box
   other than the platform's secret store.
2. **The HUMAN token never leaves the Mac.** Only the Grok token goes to the box,
   via the bot platform's secret-request flow, entered by the user.
3. **Approvals are human-only.** Never run `/approve`, `grok-codex call codex_approve`
   or `scripts/auto-approve.sh`. The connector has no accept tool by design. The
   optional YOLO helper (see [YOLO](#optional-yolo-helper)) is something the user
   runs themselves, per session, after explicitly choosing it.
4. **Human-only steps** are done by the user in their own Terminal or browser:
   `codex login`, Cloudflare/tunnel account login, Homebrew installation, pasting
   secrets. Explain the step, then wait.
5. Do not commit or push anything to this repository. The tunnel URL is private
   runtime configuration; keep it out of repositories and public chats.

## Variables to collect

| Name | Where it is used | How to get it |
|---|---|---|
| `MAC_REPO` | Mac checkout path | **ASK THE USER**; default `~/grok-codex-bridge-public` |
| `PROJECT_ROOT` | `BRIDGE_REPO_ROOTS` (Mac) and `CODEX_ALLOWED_ROOT` (box) | **ASK THE USER** for the absolute Mac folder that contains their code repos, e.g. `/Users/NAME/dev` |
| `BOX_REPO` | box checkout path | a persistent directory on the box, e.g. `$HOME/grok-codex-bridge-public` |
| `TUNNEL_ORIGIN` | box URL file | stage 5 output, `https://HOST` with no path |
| `BRIDGE_PATH_PREFIX` | box connector env | `/codex` with the Caddy proxy (default), `""` for a tunnel straight to the daemon |

Commands below use the default `~/grok-codex-bridge-public` for `MAC_REPO`; substitute
the user's choice. Replace the placeholders `PROJECT_ROOT` and `TUNNEL_ORIGIN` with real values.

---

## Stage 0: Prerequisites check

**0a. Box.** On the box:

```sh
git clone https://github.com/senscho/grok-codex-bridge-public.git "$BOX_REPO"
bash "$BOX_REPO/scripts/check_box.sh"
```

**0b. Mac.** Tell the user you will run a **read-only** check on their Mac. The
repo is not on the Mac yet, so fetch only the script to a temp file and run it:

```sh
curl -fsSL https://raw.githubusercontent.com/senscho/grok-codex-bridge-public/main/scripts/check_prereqs.sh -o /tmp/grok-codex-check.sh && bash /tmp/grok-codex-check.sh --repo "$HOME/grok-codex-bridge-public"
```

(After stage 1, run `bash scripts/check_prereqs.sh` from `MAC_REPO` instead. The script
is read-only and prints no secret values; you may also show it to the user first.)

Checks performed (each prints `PASS`, `FAIL`, `WARN` or `INFO`; exit 1 on any FAIL):

| Check | Script | On FAIL / WARN, tell the user |
|---|---|---|
| macOS version | Mac | Needs macOS; 13+ recommended. |
| Homebrew | Mac (WARN) | Install from https://brew.sh in their own Terminal (it asks for their password; never relay it). |
| `uv` | Mac + box | `brew install uv` (Mac). Box: install uv per https://docs.astral.sh/uv/. |
| `git`, `curl` | Mac + box | `xcode-select --install` (Mac). |
| Codex CLI installed + version | Mac | `brew install --cask codex`, or `brew upgrade --cask codex` if older than the recorded protocol version 0.163.0. |
| Codex login (`codex login status`, per registered account `CODEX_HOME`) | Mac | Human-only: the user runs the printed `CODEX_HOME=... codex login` and completes the browser sign-in. Before stage 4 no accounts exist; that is INFO, not FAIL. |
| Account dirs (`BRIDGE_STATE_DIR/accounts/*`, mode 700) | Mac | `chmod 700` the directory named. |
| `.env` present, mode 600, tokens set and distinct, repo roots exist | Mac (after stage 2) | `chmod 600 .env`; edit `BRIDGE_REPO_ROOTS` (stage 2). |
| Bridge port 48765 and proxy port 48766 free (or already serving the bridge) | Mac | Another program owns the port: ask the user to stop it, or pick another port and use `--port` consistently. |
| Tunnel tools (`cloudflared`, `caddy`, `tailscale`) | Mac (WARN) | `brew install cloudflared caddy`, or confirm they have their own HTTPS reverse proxy. |
| `tmux` | Mac (WARN, optional) | `brew install tmux` for `grok-codex attach`; without it, approvals use `grok-codex terminal SESSION_ID`. |
| Python 3.12+ via uv, connector deps, outbound HTTPS | box | Fix box network or uv; `uv python install 3.12`. |
| Tunnel health, Grok token (after stage 5/6) | box | See [Troubleshooting](#troubleshooting). |

**Verify:** both scripts end with `0 FAIL`. WARN lines are acceptable once the
user has acknowledged them. Re-run the Mac check after the user fixes anything.

## Stage 1: Clone and install on the Mac

**ASK THE USER** for `MAC_REPO` and `PROJECT_ROOT` if not yet known. Then on the Mac:

```sh
git clone https://github.com/senscho/grok-codex-bridge-public.git ~/grok-codex-bridge-public
cd ~/grok-codex-bridge-public && uv sync --locked && uv run --locked pytest -q
```

**Verify:** pytest ends with `passed` and no failures; `.venv/` exists.

## Stage 2: Private configuration on the Mac

Tokens are generated **on the Mac** by `init`, which writes `.env` with mode 600
and refuses to overwrite an existing file. `.env.example` is a reference for the
available settings only; do not copy it over `.env` or fill in tokens by hand.

```sh
cd ~/grok-codex-bridge-public && uv run --locked grok-codex init
```

`init` sets `BRIDGE_REPO_ROOTS` to the current directory (the bridge checkout),
which is wrong for real use. Replace it with `PROJECT_ROOT` (macOS `sed`; prints nothing):

```sh
cd ~/grok-codex-bridge-public && sed -i '' "s|^BRIDGE_REPO_ROOTS=.*|BRIDGE_REPO_ROOTS='PROJECT_ROOT'|" .env
```

Multiple roots are colon-separated. Keep `BRIDGE_STATE_DIR` (default
`~/.local/state/grok-codex`) outside every project root.

**Copy the Grok token to the box.** Start the platform's secret-request flow for a
secret named `BRIDGE_GROK_TOKEN`. **ASK THE USER** to run this in their own Mac
Terminal; it copies the token to the clipboard without displaying it:

```sh
cd ~/grok-codex-bridge-public && sed -n "s/^BRIDGE_GROK_TOKEN=//p" .env | tr -d "'\n" | pbcopy
```

They paste it into the secret prompt, then clear the clipboard (e.g. copy any
other text). Never ask for `BRIDGE_HUMAN_TOKEN`, `BRIDGE_WEBHOOK_SECRET` or the whole file.

**Verify:** `bash scripts/check_prereqs.sh` shows `PASS env-file`, `PASS tokens`
and `PASS repo-roots`. On the box, the secret `BRIDGE_GROK_TOKEN` exists (check
presence only, e.g. `[ -n "${BRIDGE_GROK_TOKEN:-}" ] && echo present`).

## Stage 3: Run the daemon (launchd)

`render_launchd.sh` only prints a plist; it changes nothing. Install it as a
per-user LaunchAgent (no `sudo`):

```sh
cd ~/grok-codex-bridge-public && mkdir -p ~/Library/LaunchAgents ~/Library/Logs/grok-codex && bash scripts/render_launchd.sh bridge > ~/Library/LaunchAgents/com.grok-codex-bridge.daemon.plist && plutil -lint ~/Library/LaunchAgents/com.grok-codex-bridge.daemon.plist && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.grok-codex-bridge.daemon.plist
```

Restart after any `.env` change: `launchctl kickstart -k gui/$(id -u)/com.grok-codex-bridge.daemon`.
Alternative without launchd: the user runs `uv run --locked grok-codex daemon` in
a Terminal (or tmux) they keep open.

**Verify:**

```sh
sleep 3; curl -fsS http://127.0.0.1:48765/healthz; echo; tail -n 5 ~/Library/Logs/grok-codex/bridge.log
```

Expect `{"status":"ok"}`. The log contains JSON status lines, not secrets; still,
summarize rather than paste it.

## Stage 4: Register a Codex account

Run on the Mac (the daemon must be running):

```sh
cd ~/grok-codex-bridge-public && uv run --locked grok-codex register-account personal
```

It prints a `login` command of the form `CODEX_HOME=/…/accounts/personal codex login`.
**ASK THE USER** to run exactly that command in their own Terminal and finish the
ChatGPT browser sign-in. You never run `codex login` and never handle credentials.

**Verify:** `bash scripts/check_prereqs.sh` shows `PASS codex-login: account 'personal' logged in using ChatGPT`
(the script calls `codex login status` with that account's `CODEX_HOME`).

## Stage 5: Expose the bridge through an HTTPS tunnel

**ASK THE USER** which option they want:

| Option | Prefix | Notes |
|---|---|---|
| A. Caddy `/codex` proxy + Cloudflare **named** tunnel (recommended) | `/codex` | Stable hostname on a domain they control. |
| B. Caddy proxy + Cloudflare **quick** tunnel | `/codex` | Testing only; the URL changes on every restart. |
| C. Their own HTTPS reverse proxy / private network | their choice | Must strip the prefix before forwarding to `127.0.0.1:48765`. |

Never expose the Codex app-server or bind the bridge to a non-loopback address.

**5a. Caddy proxy (options A and B).** Uses [examples/Caddyfile](examples/Caddyfile)
(`127.0.0.1:48766/codex/*` → `127.0.0.1:48765/*`):

```sh
cd ~/grok-codex-bridge-public && bash scripts/render_launchd.sh caddy > ~/Library/LaunchAgents/com.grok-codex-bridge.caddy.plist && plutil -lint ~/Library/LaunchAgents/com.grok-codex-bridge.caddy.plist && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.grok-codex-bridge.caddy.plist
sleep 3; curl -fsS http://127.0.0.1:48766/codex/healthz
```

**5b. Tunnel.**

- Option A: the Cloudflare account steps are **human-only** (`cloudflared tunnel login`
  opens a browser). Ask the user to create a named tunnel whose public hostname
  points at `http://127.0.0.1:48766`, then give you the hostname.
- Option B: `cloudflared tunnel --url http://127.0.0.1:48766` prints a temporary
  `https://…` origin. It must keep running (a terminal or tmux the user leaves open).
- Option C: the user gives you the HTTPS origin and the prefix their proxy strips.

**Verify (box):**

```sh
(umask 077 && mkdir -p ~/.config/grok-codex && printf '%s\n' 'TUNNEL_ORIGIN' > ~/.config/grok-codex/tunnel_url)
bash "$BOX_REPO/scripts/check_box.sh" --url-file ~/.config/grok-codex/tunnel_url --prefix /codex
```

Expect `PASS tunnel-health` and, when the secret is in your environment,
`PASS grok-token`.

## Stage 6: Install the MCP connector on the box

The connector is a single-file **stdio** MCP server with inline uv dependencies;
there is no hosted MCP endpoint. Register it with the platform's stdio MCP
connector settings:

| Field | Value |
|---|---|
| name | `codex` |
| command | `uv` (absolute path from `command -v uv` if the launcher needs it) |
| args | `run`, `--quiet`, `--script`, `BOX_REPO/connectors/grok-mcp/server.py` (absolute) |
| env `CODEX_TUNNEL_URL_FILE` | absolute path of `~/.config/grok-codex/tunnel_url` (reread on every call, so a changed URL needs only a file edit) |
| env `BRIDGE_PATH_PREFIX` | `/codex` (or `""` for a direct tunnel) |
| env `CODEX_ALLOWED_ROOT` | `PROJECT_ROOT` (the **Mac** path; with several roots, their common parent) |
| secret `BRIDGE_GROK_TOKEN` | the secret from stage 2, injected as an environment variable |

Alternative without uv script mode: create a venv in `connectors/grok-mcp`,
`pip install -r requirements.txt` and use that Python with `server.py`. Restart the
MCP server after changing env or secrets. See [connectors/grok-mcp/README.md](connectors/grok-mcp/README.md).

**Verify:** the platform lists 11 tools (`codex_start`, `codex_send`, `codex_status`,
`codex_logs`, `codex_list`, `codex_accounts`, `codex_interrupt`, `codex_resume`,
`codex_stop`, `codex_attach_info`, `codex_decline`). `codex_accounts` returns
`personal` and `codex_list` returns without an error.

## Stage 7: Install the skills

Import each folder into the bot's skill library:

- [codex-delegate](skills/codex-delegate/SKILL.md): how to delegate work and follow sessions.
- [codex-approval-alert](skills/codex-approval-alert/SKILL.md): body for the webhook routine.
- [codex-session-watchdog](skills/codex-session-watchdog/SKILL.md): body for the 10-minute watchdog.

**Verify:** the three skills appear in the skill list with their frontmatter names.

## Stage 8 (optional, recommended): Alert routine, watchdog and webhooks

1. **ASK THE USER** which bot owns Codex work (normally you). Only that bot's
   routine wakes; see [Which bot wakes](#which-bot-wakes).
2. Create a **webhook-triggered** routine on that bot whose body is the
   codex-approval-alert skill, and a **scheduled** routine every 10 minutes with
   the codex-session-watchdog skill. Give both, and the delegating bot, the same
   private durable report ledger (see [Skills and backup monitoring](#skills-and-backup-monitoring)).
   On first run, baseline historical completions instead of reporting them.
3. Show the user the routine's webhook URL and **exact Authorization header**
   through the platform's secure configuration handoff. **ASK THE USER** to edit
   the Mac `.env` themselves and set
   `BRIDGE_WEBHOOK_URL='…'` and `BRIDGE_WEBHOOK_AUTH_HEADER='Authorization: RECEIVER_VALUE'`.
   That header is the receiver's credential, not a bridge token. Keep the
   `BRIDGE_WEBHOOK_SECRET` that `init` generated; the routine may use it to verify
   `X-Bridge-Signature` (`sha256=` HMAC-SHA256 over `X-Bridge-Timestamp + "." + raw body`).
4. Restart the daemon: `launchctl kickstart -k gui/$(id -u)/com.grok-codex-bridge.daemon`.

**Verify:** `check_prereqs.sh` shows `PASS webhook`, and the smoke test below
produces exactly one `turn/completed` routine invocation.

## Stage 9: Smoke test

1. **ASK THE USER** which repo to use, or offer a scratch repo (Mac):
   `mkdir -p PROJECT_ROOT/grok-codex-smoke && cd PROJECT_ROOT/grok-codex-smoke && git init -q`.
2. **ASK THE USER** normal mode (human approvals, recommended) or YOLO for this
   session, as the codex-delegate skill requires.
3. Call `codex_start` with `repo` = the absolute Mac path, `account` = `personal`,
   a harmless prompt such as `Reply with the single word READY. Do not run commands or edit files.`,
   and a stable `idempotency_key` such as `smoke-YYYYMMDD-1`. Save the session and job IDs.
   If the response is lost, retry with the **same** key and arguments.
4. Poll `codex_status` until the turn finishes, then read `codex_logs` with a cursor.
5. Explain the approval flow to the user: when Codex asks to run a command or edit a
   file, the session reports `approval/required`. The user approves on the Mac:
   `cd ~/grok-codex-bridge-public && uv run --locked grok-codex attach SESSION_ID`
   (or `grok-codex terminal SESSION_ID` without tmux), reviews the request, and types
   `/approve REQUEST_ID accept` (or `decline` / `cancel`). You may call `codex_decline`;
   you never accept.

**Verify:** status shows the turn `completed`, logs contain `READY`, and (if stage 8
was done) one alert/wrap-up arrived. Report the result to the user.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `Invalid connector configuration` | URL file missing/unreadable, URL has a path, prefix invalid, or token under 32 chars/missing | Fix `~/.config/grok-codex/tunnel_url` (origin only), `BRIDGE_PATH_PREFIX`, and the secret; restart the MCP server. |
| `Bridge unreachable` | Daemon, Caddy or tunnel down; Mac asleep | Mac: `curl -fsS http://127.0.0.1:48765/healthz` and `:48766/codex/healthz`; `launchctl print gui/$(id -u)/com.grok-codex-bridge.daemon`; restart the tunnel. |
| `tunnel-health` returns 404 | Prefix mismatch | `/codex` with Caddy; `""` for a tunnel straight to 48765. |
| HTTP 401 | Wrong Grok token on the box, or a `BRIDGE_GROK_TOKEN` already in the daemon's environment overrides `.env` (environment wins) | Re-run the stage 2 copy; start the daemon via launchd, not from a shell exporting bridge variables. |
| HTTP 403 | Human-only operation (approve, register, takeover) attempted with the Grok token | Expected; the user does it on the Mac. |
| Start fails: repo not allowed | Repo outside `BRIDGE_REPO_ROOTS` or `CODEX_ALLOWED_ROOT` | Use an absolute Mac path under both; fix `.env` and restart. |
| `account/authentication_required` / `_expired` | Codex login missing or expired | User runs `CODEX_HOME=… codex login` (from `register-account` output). |
| Daemon exits at start | `.env` invalid (tokens, webhook header) | `tail ~/Library/Logs/grok-codex/bridge.log`; the error names the setting, never its value. |
| launchd `Bootstrap failed: 5` | Agent already loaded | `launchctl bootout gui/$(id -u)/LABEL`, then bootstrap again. |
| Quick-tunnel URL stopped working | Quick tunnels change on restart | Update the box URL file; prefer a named tunnel. |
| `recovery_blocked` session | A recorded Codex process still exists | User inspects locally; the bridge never kills a possibly reused PID. |
| Interrupted/uncertain turn | Daemon restart or lost response | Inspect status/logs; never resend automatically. |

## Uninstall

Box: remove the `codex` MCP connector, the `BRIDGE_GROK_TOKEN` secret, the two
routines, the three skills, `~/.config/grok-codex/` and `BOX_REPO`.

Mac (user-approved):

```sh
launchctl bootout gui/$(id -u)/com.grok-codex-bridge.caddy 2>/dev/null; launchctl bootout gui/$(id -u)/com.grok-codex-bridge.daemon 2>/dev/null; rm -f ~/Library/LaunchAgents/com.grok-codex-bridge.caddy.plist ~/Library/LaunchAgents/com.grok-codex-bridge.daemon.plist
```

Then **ASK THE USER** before deleting data: the checkout (`MAC_REPO`, includes `.env`),
logs (`~/Library/Logs/grok-codex`) and the state directory
(`~/.local/state/grok-codex`, which holds session history and Codex account
credentials). They also remove any Cloudflare tunnel they created.

---

# Reference

## Which bot wakes

`BRIDGE_WEBHOOK_URL` + `BRIDGE_WEBHOOK_AUTH_HEADER` point at **one bot's webhook
routine**. Only that bot wakes when the bridge posts events. Installing the MCP
connector on several bots does not subscribe them all to webhooks. Other bots
learn only via an explicit relay from the receiving bot. Do not substitute another
bot's routine or an invented URL, and do not relay secrets or raw logs.

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

The bridge's internal `urgent` classification covers several state changes; use
the alert template to classify them. Acknowledge duplicate deliveries successfully
and use receiver authentication even when signature verification is unavailable.

Ordinary notifications older than `BRIDGE_WEBHOOK_MAX_EVENT_AGE_SEC` (default 15
minutes from source event time) expire before delivery/retry, including across
restart. Unresolved approval/required and approval/held notifications are exempt.
Poll status after a long outage. Idle sessions can resume automatically; in-flight
turns are interrupted and never automatically replayed. Explicit bridge-terminal
exit (`/quit`, `/detach`, EOF or Ctrl-C) emits `session/detached`; it does not
transfer ownership or mean the Codex process stopped.

## Skills and backup monitoring

Use the alert skill as the webhook routine body and the watchdog skill as a
routine **every 10 minutes**, on the same owning bot. Webhooks can fail or expire;
the watchdog independently lists sessions and polls status/logs. It alerts once
for approvals older than 5 minutes, unreported finished turns, active sessions
without events for over 20 minutes and current errors, and stays silent otherwise.

Give both routines and the delegating bot the same private durable report ledger:
per-session cursors, reported terminal job/turn IDs, active incident fingerprints
and processed webhook IDs. Atomically claim each report before sending so the
routines do not duplicate it. Importing skills does not create triggers, schedules
or storage; configure those with the bot platform's routine tools. If shared
storage is unavailable, report that gap instead of promising deduplication.

## Optional YOLO helper

The delegate skill asks normal versus YOLO before a new session. This connector
has no accept capability, so chat consent never lets the bot accept. If the owner
chooses YOLO, **the owner** runs on the Mac:

```sh
cd ~/grok-codex-bridge-public && bash scripts/auto-approve.sh SESSION_ID --enable-yolo
```

It uses local human credentials and weakens human review; deny rules are heuristic
and cannot uncover all hidden side effects. It leaves denied/unknown requests
pending (reported as `approval/held`), exits on idle or Ctrl-C, and needs a new
opt-in for later turns. See [the README](README.md#optional-per-session-yolo-helper).
Neither notification routine ever accepts approvals, even in YOLO sessions.

## Quiet webhook configuration

`BRIDGE_WEBHOOK_PROGRESS=0` is the default. Only kinds in `BRIDGE_WEBHOOK_EVENTS`
cause routine deliveries; the default covers approvals, YOLO holds, finished
turns/errors, authentication problems, lost processes/uncertain jobs, explicit
terminal detach and near/exhausted usage limits. See the
[README](README.md#notable-only-delivery-by-default) and [.env.example](.env.example).
Changes require a daemon restart. Do not set `BRIDGE_WEBHOOK_PROGRESS=1` unless
the routine can handle interval-based wakes. `BRIDGE_USAGE_ALERT_PERCENT=90`
controls the near-limit threshold; exhaustion reports use known reset times only.
Neither routine spends credits, switches accounts or accepts approvals.
