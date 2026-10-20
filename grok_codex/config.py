from __future__ import annotations

import os
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


@dataclass
class Settings:
    state_dir: Path = field(default_factory=lambda: Path(os.getenv('BRIDGE_STATE_DIR', '~/.local/state/grok-codex')).expanduser())
    repo_roots: tuple[Path, ...] = field(default_factory=lambda: tuple(Path(p).expanduser().resolve() for p in os.getenv('BRIDGE_REPO_ROOTS', os.getcwd()).split(os.pathsep)))
    grok_token: str = field(default_factory=lambda: os.getenv('BRIDGE_GROK_TOKEN', ''))
    human_token: str = field(default_factory=lambda: os.getenv('BRIDGE_HUMAN_TOKEN', ''))
    codex_binary: str = field(default_factory=lambda: os.getenv('BRIDGE_CODEX_BINARY', 'codex'))
    webhook_url: str = field(default_factory=lambda: os.getenv('BRIDGE_WEBHOOK_URL', ''))
    webhook_secret: str = field(default_factory=lambda: os.getenv('BRIDGE_WEBHOOK_SECRET', ''))
    webhook_auth_header: str = field(default_factory=lambda: os.getenv('BRIDGE_WEBHOOK_AUTH_HEADER', ''), repr=False)
    progress_interval: float = field(default_factory=lambda: float(os.getenv('BRIDGE_PROGRESS_INTERVAL', '10')))
    webhook_max_event_age_sec: float = field(default_factory=lambda: float(os.getenv('BRIDGE_WEBHOOK_MAX_EVENT_AGE_SEC', '900')))
    rpc_timeout: float = 45
    max_sessions: int = 16
    webhook_attempts: int = 10

    @property
    def webhook_auth(self) -> tuple[str, str] | None:
        """Parse private receiver credentials without including them in errors."""
        raw = self.webhook_auth_header
        if not raw:
            return None
        name, separator, value = raw.partition(':')
        value = value.strip(' \t')
        if (not separator or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name)
                or any(c in raw for c in '\r\n') or not value
                or any((ord(c) < 32 and c != '\t') or ord(c) > 126 for c in value)):
            raise ValueError('BRIDGE_WEBHOOK_AUTH_HEADER must be one Name: value header with a valid name and nonempty ASCII value, without CR/LF or control characters')
        if name.lower() in {
            'content-type', 'x-bridge-event-id', 'x-bridge-timestamp', 'x-bridge-signature',
        }:
            raise ValueError('BRIDGE_WEBHOOK_AUTH_HEADER must not override bridge delivery headers')
        return name, value

    def validate(self) -> None:
        # Validate even when delivery is disabled, before starting background tasks.
        _ = self.webhook_auth
        if min(len(self.grok_token), len(self.human_token)) < 32 or self.grok_token == self.human_token:
            raise ValueError('Set distinct BRIDGE_GROK_TOKEN and BRIDGE_HUMAN_TOKEN (32+ characters each)')
        if not math.isfinite(self.webhook_max_event_age_sec) or self.webhook_max_event_age_sec <= 0:
            raise ValueError('BRIDGE_WEBHOOK_MAX_EVENT_AGE_SEC must be finite and positive')
        if self.progress_interval < 0.1:
            raise ValueError('progress interval must be >= 0.1 seconds')
        if self.webhook_url:
            u = urlsplit(self.webhook_url)
            if u.username or u.password or u.fragment or u.query:
                raise ValueError('Webhook URL must not contain credentials, query or fragment')
            if u.scheme != 'https' and not (u.scheme == 'http' and u.hostname in {'localhost', '127.0.0.1', '::1'}):
                raise ValueError('Webhook requires HTTPS except on localhost')
            if len(self.webhook_secret) < 32:
                raise ValueError('BRIDGE_WEBHOOK_SECRET must be 32+ characters')
        self.state_dir = self.state_dir.expanduser().resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_dir.chmod(0o700)

    def repository(self, value: str) -> str:
        try:
            p = Path(value).expanduser().resolve(strict=True)
        except (OSError,RuntimeError) as e:
            raise ValueError('Repository does not exist or cannot be resolved') from e
        if not p.is_dir() or not any(p.is_relative_to(root) for root in self.repo_roots):
            raise ValueError('Repository must be a directory inside BRIDGE_REPO_ROOTS')
        # Never allow Codex to edit credentials or bridge state as its workspace.
        if p.is_relative_to(self.state_dir) or self.state_dir.is_relative_to(p):
            raise ValueError('Repository must not overlap bridge state/accounts')
        return str(p)
