# Public release audit

This release copies only source working-tree application, test, script, example,
protocol and build files. Source Git metadata, credentials, runtime configuration,
account homes, databases, logs, caches and environments were not copied. The
input connector is excluded from Git. No remotes were added and nothing was pushed.

## Changes from the private source

- Replaced the private README with public installation, architecture, token,
  ownership, approval, tunnel, viewing, restart and known-issue documentation.
- Dropped the Korean setup page. Added the English setup/flow pages and rewrote
  the Mac tutorial around the public repository URL.
- Translated demo output and corresponding assertions. Replaced example session,
  event and attachment identifiers with placeholders in documentation/examples.
- Replaced the downloaded official documentation web snapshot with a concise
  reference summary; retained protocol schemas, upstream reference and CLI help (with trailing
  whitespace normalized).
- Added an environment-configured MCP connector with a Mac-root guard, explicit
  URL-file support, safer HTTP settings and mutation keys retained on failures.
- Added bot installation/routine instructions, security guidance and MIT licensing.
- Added configurable webhook notification expiry at dispatcher startup, batching
  and retry, including legacy persisted outboxes and replay. Local history stays.
  Default expiry is 900 seconds. Added expiry and connector regression tests.
- Extended generated local configuration with webhook auth and expiry settings;
  extended Git exclusions for private configuration and the supplied input.

## Follow-up: skills, backup routines and local YOLO

Added three importable skill folders: delegation, webhook alerts and a ten-minute
watchdog backup. Templates specify trigger setup, saved cursors, a shared private
deduplication ledger, session-scoped chat consent and no routine acceptance.
They are importable templates; live bot triggers/storage are not provisioned by
this repository change. The connector still has no approval-accept tool.

Added an executable shell launcher and Python implementation for the owner's
optional one-session local YOLO helper. It polls structured events, keeps human
credentials on loopback, accepts inspectable command/file requests and leaves
denied or incomplete requests pending, ignoring stale requests. Its deny rules are heuristic, not
a security boundary. It exits on idle/inactive state, Ctrl-C or API failure.
Added redacted command working-directory/completeness, permission-scope and file
item/path completeness metadata so the helper can make conservative decisions.
Default server-side authorization and bot approval restrictions are unchanged.

Updated the README, bot installation, security, English Mac guides, visual flow
and test scope. Added helper regressions and fixed an inherited path-validation
test whose temporary-path idempotency key could exceed the API length limit.
The test now hashes that key so it continues to exercise path validation.

## Follow-up: quiet notable webhooks and held approvals

- Added `BRIDGE_WEBHOOK_EVENTS` with twelve default notable kinds (including
  required/held approvals), `BRIDGE_WEBHOOK_PROGRESS=0` and
  `BRIDGE_USAGE_ALERT_PERCENT=90`. Centralized urgency classification; normal
  progress and administrative events remain local. Legacy queued progress-only
  deliveries are suppressed when progress is off. Approval events get batch and
  delivery priority over other backlog.
- Notification age is measured from source time, including legacy payloads.
  Unresolved required/held approvals are exempt from age expiry; after resolution
  or staleness the normal cutoff applies. Filters/retry-attempt limits still apply.
- Added account/window usage state and durable alert markers. Sparse updates use
  the retained `RateLimitSnapshot` / `RateLimitWindow` field names: `usedPercent`,
  `resetsAt`, `windowDurationMins`, `rateLimitReachedType`, `spendControlReached`
  and individual-limit metadata. Near warnings deduplicate across sessions and
  restarts. Errors use `codexErrorInfo` variants from `TurnError`; no reset field
  is invented. Known but uncorrelated window resets are labeled observations.
- Added the human-only `/sessions/hold` endpoint and helper integration. Denied or
  unknown requests produce one `approval/held` event per request, with a redacted
  command and reason in local logs, leaving the approval pending. The webhook
  carries only allowlisted metadata, so the bot fetches held details via logs.
- Added human-only explicit terminal-exit notifications via `/sessions/detach`;
  `/quit`, `/detach`, EOF and Ctrl-C record `session/detached` without transferring
  ownership. Automatic tmux detach detection remains planned.
- Added a newly authored, credential-free `.env.example` and a narrow Git-ignore
  exception for it. Updated generated config, OpenAPI, docs and all routine skills.
  No private configuration was copied. Only targeted tests were run during work;
  one final locked full-suite run is recorded below.

## Current follow-up files

27 files added or changed in the notable-webhook follow-up:

- `.env.example`
- `.gitignore`
- `BOT_INSTALL.md`
- `README.md`
- `RELEASE_AUDIT.md`
- `SECURITY.md`
- `docs/TESTING.md`
- `docs/flow.html`
- `docs/mac-tutorial.html`
- `docs/openapi.json`
- `docs/setup-en.html`
- `grok_codex/api.py`
- `grok_codex/auto_approve.py`
- `grok_codex/cli.py`
- `grok_codex/config.py`
- `grok_codex/service.py`
- `grok_codex/store.py`
- `grok_codex/usage.py`
- `grok_codex/webhooks.py`
- `skills/codex-approval-alert/SKILL.md`
- `skills/codex-delegate/SKILL.md`
- `skills/codex-session-watchdog/SKILL.md`
- `tests/test_api.py`
- `tests/test_approvals.py`
- `tests/test_auto_approve.py`
- `tests/test_usage.py`
- `tests/test_webhooks.py`

## Validation

- Current follow-up: `uv run --locked pytest -q` ran **exactly once** at the end:
  **169 passed, 1 skipped** in 2.00 seconds. The skipped test is the optional
  installed-Codex lifecycle check. Targeted checks were used during development.
- Previous follow-up: **130 passed, 1 skipped**. The skipped check is the
  opt-in installed-Codex lifecycle integration test; no authenticated coding run
  or live bot webhook was performed for this release.
- Unchanged connector: initial-release real MCP runtime smoke check registered
  all **11 tools** successfully.
- Contract export: OpenAPI and **12** direct bridge function definitions generated;
  both match the inherited contracts (the MCP adapter exposes a smaller surface).
- Application (including the helper), examples and connector compile successfully.
- All three new skills pass the skill-creator frontmatter/content validator.
- The helper shell launcher passes `bash -n`; its CLI help and explicit opt-in /
  loopback-only credential checks are validated. Helper tests cover command deny
  rules, file checks/symlinks, metadata redaction, per-session scope, stale/held
  requests, idle exit and API failure. No live approvals were accepted in testing.
- All retained JSON files parse. HTML local links resolve; the flow page has no
  external assets. The initial desktop flow layout was rendered and visually inspected in
  headless Chrome; the follow-up section uses the existing styles. Responsive
  and light/dark styles are inline. Updated HTML links/assets were checked.

## Privacy sweep

The requested case-insensitive expression, a UUID expression and the full Unicode
Hangul property were run with hidden files enabled, excluding Git metadata and
input. Git ignores also exclude generated environments/caches; the release file
set below was checked. There are **13 remaining matching lines**, all intentional
code/protocol text, listed individually here. There are **zero UUID matches** and
**zero Hangul matches**. No personal names, home paths, private hostnames, tunnel
addresses, personal emails or real credentials were found. Generic loopback
addresses and reserved example domains remain for runnable examples/tests.

| File | Line | Classification |
|---|---:|---|
| `docs/protocol/app-server-help.txt` | 18 | Upstream CLI help flag or sandbox option; substring false positive. |
| `docs/protocol/cli-help.txt` | 49 | Upstream CLI help flag or sandbox option; substring false positive. |
| `docs/protocol/cli-help.txt` | 112 | Upstream CLI help flag or sandbox option; substring false positive. |
| `docs/protocol/resume-help.txt` | 19 | Upstream CLI help flag or sandbox option; substring false positive. |
| `docs/protocol/resume-help.txt` | 91 | Upstream CLI help flag or sandbox option; substring false positive. |
| `docs/protocol/schema/ClientRequest.json` | 82 | Protocol description of a risk assessment framework; substring false positive. |
| `docs/protocol/schema/ServerNotification.json` | 468 | Protocol description of a risk assessment framework; substring false positive. |
| `docs/protocol/schema/v2-ThreadResumeParams.json` | 53 | Protocol description of a risk assessment framework; substring false positive. |
| `docs/protocol/schema/v2-ThreadResumeResponse.json` | 38 | Protocol description of a risk assessment framework; substring false positive. |
| `docs/protocol/schema/v2-ThreadStartParams.json` | 9 | Protocol description of a risk assessment framework; substring false positive. |
| `docs/protocol/schema/v2-TurnStartParams.json` | 31 | Protocol description of a risk assessment framework; substring false positive. |
| `grok_codex/service.py` | 25 | Intentional secret-redaction regular expression. |
| `grok_codex/service.py` | 27 | Intentional secret-redaction regular expression. |

## Complete release file list

77 files; this audit is included.

- `.env.example`
- `.gitignore`
- `.python-version`
- `BOT_INSTALL.md`
- `LICENSE`
- `README.md`
- `RELEASE_AUDIT.md`
- `SECURITY.md`
- `connectors/grok-mcp/README.md`
- `connectors/grok-mcp/requirements.txt`
- `connectors/grok-mcp/server.py`
- `docs/ADR-001.md`
- `docs/TESTING.md`
- `docs/flow.html`
- `docs/mac-tutorial.html`
- `docs/openapi.json`
- `docs/protocol/app-server-help.txt`
- `docs/protocol/cli-help.txt`
- `docs/protocol/official-app-server.txt`
- `docs/protocol/resume-help.txt`
- `docs/protocol/schema/ClientRequest.json`
- `docs/protocol/schema/CommandExecutionRequestApprovalParams.json`
- `docs/protocol/schema/CommandExecutionRequestApprovalResponse.json`
- `docs/protocol/schema/FileChangeRequestApprovalParams.json`
- `docs/protocol/schema/FileChangeRequestApprovalResponse.json`
- `docs/protocol/schema/ServerNotification.json`
- `docs/protocol/schema/ServerRequest.json`
- `docs/protocol/schema/v1-InitializeParams.json`
- `docs/protocol/schema/v2-ThreadItemsListParams.json`
- `docs/protocol/schema/v2-ThreadReadParams.json`
- `docs/protocol/schema/v2-ThreadResumeParams.json`
- `docs/protocol/schema/v2-ThreadResumeResponse.json`
- `docs/protocol/schema/v2-ThreadStartParams.json`
- `docs/protocol/schema/v2-ThreadTurnsListParams.json`
- `docs/protocol/schema/v2-TurnInterruptParams.json`
- `docs/protocol/schema/v2-TurnStartParams.json`
- `docs/protocol/schema/v2-TurnSteerParams.json`
- `docs/protocol/upstream-app-server.md`
- `docs/setup-en.html`
- `examples/grok-tools.json`
- `examples/webhook.json`
- `examples/webhook_receiver.py`
- `grok_codex/__init__.py`
- `grok_codex/api.py`
- `grok_codex/auto_approve.py`
- `grok_codex/cli.py`
- `grok_codex/config.py`
- `grok_codex/demo.py`
- `grok_codex/protocol.py`
- `grok_codex/service.py`
- `grok_codex/store.py`
- `grok_codex/usage.py`
- `grok_codex/webhooks.py`
- `pyproject.toml`
- `scripts/auto-approve.sh`
- `scripts/bootstrap.sh`
- `scripts/demo.sh`
- `scripts/export_contracts.py`
- `skills/codex-approval-alert/SKILL.md`
- `skills/codex-delegate/SKILL.md`
- `skills/codex-session-watchdog/SKILL.md`
- `tests/__init__.py`
- `tests/conftest.py`
- `tests/fakes.py`
- `tests/stdio_fixture.py`
- `tests/test_api.py`
- `tests/test_approvals.py`
- `tests/test_auto_approve.py`
- `tests/test_connector.py`
- `tests/test_demo.py`
- `tests/test_integration.py`
- `tests/test_protocol.py`
- `tests/test_sessions.py`
- `tests/test_usage.py`
- `tests/test_vertical.py`
- `tests/test_webhooks.py`
- `uv.lock`

## Follow-up: bot install hardening

Rewrote BOT_INSTALL.md as an ordered runbook with a verify step per stage and
explicit user stop points. Added read-only `scripts/check_prereqs.sh` (Mac, bash
3.2 compatible) and `scripts/check_box.sh` (bot host); both print PASS/FAIL/WARN
lines and never print token, webhook or tunnel values. Added
`scripts/render_launchd.sh` (prints a per-user LaunchAgent plist, writes nothing),
`examples/Caddyfile` and inline uv script metadata for the connector. No runtime
behavior changed; approvals remain human-only.
