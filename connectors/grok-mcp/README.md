# Grok MCP connector

A stdio MCP server forwarding eleven tools to a bridge you operate. Python 3.12+
is required. Install in an isolated environment:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python server.py
```

Configure the bot's MCP launcher to run the absolute path to that Python and
`server.py`. Keep protocol stdout reserved for MCP; supply configuration through
the launcher's environment and secret store:

| Variable | Meaning |
|---|---|
| `CODEX_TUNNEL_URL` | HTTPS origin of your tunnel; takes precedence over the file. No path/query/credentials. |
| `CODEX_TUNNEL_URL_FILE` | Optional private UTF-8 file containing the origin. Reread for every call. No default filename. |
| `BRIDGE_PATH_PREFIX` | External reverse proxy prefix, default `/codex`; empty for a tunnel directly to the bridge root. |
| `BRIDGE_GROK_TOKEN` | Grok bearer token supplied by the Mac operator, from the bot's secret store. |
| `CODEX_ALLOWED_ROOT` | Absolute **Mac** project root, e.g. `/work/projects`; not a directory on the bot host. |

The connector rejects paths outside that root lexically. The bridge independently
canonicalizes paths, checks symlinks and enforces `BRIDGE_REPO_ROOTS`; this is the
security boundary. Only loopback testing permits HTTP. Redirects and environment
HTTP proxies are disabled. The external prefix must be stripped by the proxy;
it is not a bridge route. See the [Mac setup](../../docs/setup-en.html).

Tools: `codex_start`, `codex_send`, `codex_status`, `codex_logs`, `codex_list`,
`codex_accounts`, `codex_interrupt`, `codex_resume`, `codex_stop`,
`codex_attach_info`, `codex_decline`. There is no tool for accepting approvals.

Reuse sessions for follow-ups. Supply a stable `idempotency_key` for each mutation;
reuse that same key and arguments after a lost response. Generated keys are also
returned on transport failure. Never blindly resend an uncertain turn with a new
key. Poll status/logs for results; tool calls return jobs rather than blocking
until coding completes. The `personal` default account is a generic local account
label, not an identity or a credential.

Never configure the human token in this process. See [BOT_INSTALL.md](../../BOT_INSTALL.md)
for the webhook routine and the distinction between the receiving bot and relays.
