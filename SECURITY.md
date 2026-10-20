# Security

The bridge runs coding tools with the permissions of a local user. Restrict it to
repositories and accounts you intend to expose. Keep the daemon on loopback and
use an HTTPS tunnel or private network gateway. Never expose Codex app-server
stdio or an unauthenticated listener to the Internet.

- Keep distinct random Grok and human tokens, at least 32 characters each. The
  human token stays on the Mac; only the Grok token goes in the bot's secret store.
- Human acceptance is enforced server-side. Bots can request work and decline or
  cancel approvals, but cannot accept them, register accounts or take ownership.
  Sandbox and approval settings still determine which operations require review.
- Restrict `BRIDGE_REPO_ROOTS`; canonical path checks reject escapes via symlinks.
  Keep bridge state/account homes outside coding workspaces. The connector's
  `CODEX_ALLOWED_ROOT` is an additional lexical guard, not the security boundary.
- Keep `.env`, account credentials, state databases, logs, captures and tunnel
  configuration private. Generated config/database files use mode `0600` and state
  directories `0700`. Do not put credentials in command arguments or Git.
- The Grok token grants access to session logs and work in allowed repositories.
  Redaction is best effort, not a data-loss prevention system. Bot hosts and MCP
  launchers handling this token are trusted infrastructure.
- Use the webhook routine's private receiver header and a separate HMAC secret.
  Check timestamp freshness and deduplicate event IDs. Webhooks contain metadata;
  treat session identifiers and workflow metadata as private operational data.
  Receiver URLs/auth headers select one bot; relaying is that bot's responsibility.
- Use stable idempotency keys. Inspect uncertain jobs and interrupted turns before
  asking for new work. Expired webhook notifications do not cancel pending work.
- Use one daemon/worker per state directory. Review subprocess recovery locally
  when a recorded PID is still alive. Native viewing relies on Codex's built-in
  read-only protection while the bridge holds the thread.

Rotate exposed bearer tokens and webhook credentials in both endpoints, restart
processes that load them, and revoke a leaked tunnel endpoint. Avoid posting live
secrets, logs or databases in issues. Report vulnerabilities through a private
GitHub security advisory on the public repository when available; otherwise ask
for a private reporting channel without disclosing exploit details publicly.

The optional `scripts/auto-approve.sh` helper weakens the default human-approval
guarantee. The owner enables it locally for exactly one session until idle. It
uses human credentials locally and leaves denied/incomplete requests pending.
Its lexical deny list is heuristic: indirect execution and file content can hide
side effects. It is not a sandbox or a substitute for trusted work and review.
Chat authorization never gives the MCP connector an accept capability. Keep the
human token on the Mac. Alert and watchdog routines never accept approvals.

Webhook delivery is notable-only by default. Commands and deny reasons for
`approval/held` stay in redacted local logs; the signed webhook includes only
allowlisted metadata. Unresolved approval notifications are exempt from age
expiry, but filters and delivery attempt limits still apply. Keep the scheduled
watchdog backup and receiver deduplication enabled.
