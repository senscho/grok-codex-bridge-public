# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27,<1", "mcp>=1.12,<2"]
# ///
# MCP adapter for the public Grok–Codex bridge.
import os
import uuid
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import httpx
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("codex")


def base():
    url = os.environ.get("CODEX_TUNNEL_URL", "").strip()
    if not url:
        filename = os.environ.get("CODEX_TUNNEL_URL_FILE")
        if not filename:
            raise ValueError("Set CODEX_TUNNEL_URL or CODEX_TUNNEL_URL_FILE")
        url = Path(filename).expanduser().read_text().strip()
    parts = urlsplit(url)
    if (not parts.hostname or parts.username or parts.password or parts.query
            or parts.fragment or parts.path not in ("", "/")
            or (parts.scheme != "https" and not
                (parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1", "::1"}))):
        raise ValueError("Tunnel URL must be an HTTPS origin (HTTP is allowed for loopback testing)")
    prefix = os.environ.get("BRIDGE_PATH_PREFIX", "/codex").rstrip("/")
    if prefix and (not prefix.startswith("/") or any(
            segment in {".", ".."} or not segment.replace("-", "").replace("_", "").isalnum()
            for segment in prefix[1:].split("/"))):
        raise ValueError("BRIDGE_PATH_PREFIX must be a plain absolute path prefix")
    return url.rstrip("/") + prefix


def allowed_repo(repo):
    root = os.environ.get("CODEX_ALLOWED_ROOT", "")
    path, allowed = PurePosixPath(repo), PurePosixPath(root)
    if (not root or not path.is_absolute() or not allowed.is_absolute()
            or ".." in path.parts or ".." in allowed.parts
            or not path.is_relative_to(allowed)):
        raise ValueError("Repository must be an absolute Mac path under CODEX_ALLOWED_ROOT")
    return str(path)


def call(name, body, key=None, mutate=False):
    request_key = (key or str(uuid.uuid4())) if mutate else None
    try:
        endpoint = base()
        token = os.environ["BRIDGE_GROK_TOKEN"]
        if len(token) < 32:
            raise ValueError("BRIDGE_GROK_TOKEN must have at least 32 characters")
    except (OSError, KeyError, ValueError):
        return {"error": "Invalid connector configuration; check URL, prefix, token and URL file"}
    h = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    if mutate:
        h["Idempotency-Key"] = request_key
    try:
        r = httpx.post(f"{endpoint}/tools/{name}", headers=h, json=body,
                       timeout=180, follow_redirects=False, trust_env=False)
    except httpx.HTTPError as exc:
        result = {"error": "Bridge unreachable; inspect status before retrying",
                  "detail": type(exc).__name__}
        if mutate:
            result["idempotency_key"] = request_key
        return result
    try: data = r.json()
    except Exception: data = {"text": r.text[:2000]}
    if mutate: data = {"idempotency_key": h["Idempotency-Key"], "http": r.status_code, **(data if isinstance(data, dict) else {"result": data})}
    elif r.status_code >= 400: data = {"http": r.status_code, **(data if isinstance(data, dict) else {"result": data})}
    return data

@mcp.tool()
def codex_start(repo: str, prompt: str, account: str = "personal", sandbox: str = "workspace-write", approval_policy: str = "on-request", model: str | None = None, idempotency_key: str | None = None) -> dict:
    """Start one persistent coding session on the bridge host (absolute repo path under CODEX_ALLOWED_ROOT). Returns session_id + job_id immediately; poll codex_status/codex_logs. Reuse the session with codex_send for follow-ups instead of starting new ones. On a transport failure retry with the SAME idempotency_key."""
    try:
        repo = allowed_repo(repo)
    except ValueError as exc:
        return {"error": str(exc)}
    opts = {"sandbox": sandbox, "approval_policy": approval_policy}
    if model: opts["model"] = model
    return call("codex_start", {"repo": repo, "account": account, "prompt": prompt, "options": opts}, idempotency_key, True)

@mcp.tool()
def codex_send(session_id: str, message: str, steer: bool = False, idempotency_key: str | None = None) -> dict:
    """Send a follow-up instruction to an existing session (new turn when idle; steer=true to steer the active turn)."""
    return call("codex_send", {"session_id": session_id, "message": message, "steer": steer}, idempotency_key, True)

@mcp.tool()
def codex_status(session_id: str) -> dict:
    """Session state: process, owner, active turn, pending approvals (acceptance waits for the human in the local terminal), recent jobs."""
    return call("codex_status", {"session_id": session_id})

@mcp.tool()
def codex_logs(session_id: str, cursor: int = 0, limit: int = 100) -> dict:
    """Redacted events for a session from cursor; returns next cursor."""
    return call("codex_logs", {"session_id": session_id, "cursor": cursor, "limit": limit})

@mcp.tool()
def codex_list() -> dict:
    """All saved sessions."""
    return call("codex_list", {})

@mcp.tool()
def codex_accounts() -> dict:
    """Registered Codex accounts (no credentials)."""
    return call("codex_accounts", {})

@mcp.tool()
def codex_interrupt(session_id: str, idempotency_key: str | None = None) -> dict:
    """Request interruption of the current turn."""
    return call("codex_interrupt", {"session_id": session_id}, idempotency_key, True)

@mcp.tool()
def codex_resume(session_id: str, idempotency_key: str | None = None) -> dict:
    """Restore a session's history (no prompt replay)."""
    return call("codex_resume", {"session_id": session_id}, idempotency_key, True)

@mcp.tool()
def codex_stop(session_id: str, idempotency_key: str | None = None) -> dict:
    """Stop a session's process, keeping history."""
    return call("codex_stop", {"session_id": session_id}, idempotency_key, True)

@mcp.tool()
def codex_attach_info(session_id: str) -> dict:
    """tmux/terminal attach info for the human to watch or approve on the bridge host."""
    return call("codex_attach_info", {"session_id": session_id})

@mcp.tool()
def codex_decline(session_id: str, request_id: str, decision: str = "decline", idempotency_key: str | None = None) -> dict:
    """Decline or cancel a pending approval (decision: decline|cancel). Accepting is human-only on the Mac."""
    if decision not in ("decline", "cancel"): return {"error": "only decline/cancel allowed"}
    return call("codex_approve", {"session_id": session_id, "request_id": request_id, "decision": decision}, idempotency_key, True)

if __name__ == "__main__":
    mcp.run()
