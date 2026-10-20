"""Owner-run, per-invocation approval automation. Deny rules are not a sandbox."""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import time
from pathlib import Path
from urllib.parse import urlsplit

from .cli import call, load_config

SENSITIVE = re.compile(r'\.env|token|keychain|credential|secret|password|private.?key|\.ssh|\.aws', re.I)
DENIED = re.compile(r'\b(?:sudo|publish|deploy)\b|\bgit\b[^;\n]*\bpush\b|\bgh\b[^;\n]*\b(?:repo|release|pr)\b[^;\n]*\b(?:create|merge)\b', re.I)
ACTIVE = {'running', 'approval', 'dispatching', 'starting'}


def command_denial(command: str) -> str | None:
    """Conservative lexical filter, including quoted words and split rm flags."""
    if not command or len(command) > 8000 or '[REDACTED' in command:
        return 'missing, truncated or redacted command'
    if SENSITIVE.search(command):
        return 'sensitive configuration or credential reference'
    # Hold substitutions, scripts, interpreters and shell syntax we cannot inspect.
    if any(c in command for c in '$`\\\n\r') or any(ord(c) < 32 for c in command):
        return 'dynamic or multiline shell syntax'
    try:
        words = list(shlex.shlex(command, posix=True, punctuation_chars=True))
    except ValueError:
        return 'unparseable command'
    normalized = ' '.join(words).lower()
    # Normalize executable paths so /usr/bin/git and quoted words are covered.
    normalized = re.sub(r'(?:/[\w.~-]+)+/([\w.-]+)', r'\1', normalized)
    if DENIED.search(normalized):
        return 'push, repository/release/PR mutation, privilege or publication rule'
    if re.search(r'\brm\b', normalized) and (re.search(r'\s-\w*r\w*', normalized) or '--recursive' in words) and (re.search(r'\s-\w*f\w*', normalized) or '--force' in words):
        return 'recursive forced removal'
    if any(w in {'sh', 'bash', 'zsh', 'fish', 'eval', 'exec', 'source', '.', 'osascript'} for w in normalized.split()):
        return 'shell/script execution requires human inspection'
    if ('curl' in normalized or 'wget' in normalized) and ('|' in normalized or '>' in normalized):
        return 'download pipeline or redirect'
    return None


def within_repo(value: str, repo: str) -> bool:
    try:
        path = Path(value)
        root = Path(repo).resolve(strict=True)
        if not path.is_absolute():
            path = root / path
        return path.resolve().is_relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return False


def denial(detail: dict, files: dict, repo: str) -> str | None:
    if detail.get('extra_scope'):
        return 'expanded permission/root request'
    if SENSITIVE.search(str(detail.get('reason', ''))) or SENSITIVE.search(str(detail.get('cwd', ''))):
        return 'sensitive approval reason'
    method = detail.get('method')
    if method == 'item/commandExecution/requestApproval':
        if not detail.get('review_complete') or not detail.get('cwd'):
            return 'command metadata unavailable or incomplete'
        if not within_repo(detail['cwd'], repo):
            return 'command working directory outside the session repo'
        return command_denial(detail.get('command', ''))
    if method == 'item/fileChange/requestApproval':
        change = files.get(detail.get('item_id'), {})
        paths = change.get('files', [])
        if not change.get('files_complete') or not paths:
            return 'file paths unavailable or incomplete'
        if any(SENSITIVE.search(path) or not within_repo(path, repo) for path in paths):
            return 'sensitive file or path outside the session repo'
        return None
    return 'unsupported approval type'


def watch(sid: str, *, interval: float = 1, api=call, pause=time.sleep, emit=print) -> None:
    cursor = 0
    details, files = {}, {}
    handled = set()
    while True:
        status = api('/tools/codex_status', {'session_id': sid})
        if status['status'] not in ACTIVE:
            emit('Session is no longer active; helper stopped. Later turns need a new opt-in.')
            return
        while True:
            page = api('/tools/codex_logs', {'session_id': sid, 'cursor': cursor, 'limit': 500})
            for event in page['events']:
                data = event['data']
                if event['kind'] == 'approval/required':
                    details[data['request_id']] = data
                elif data.get('type') == 'fileChange' and data.get('item_id'):
                    files[data['item_id']] = data
            next_cursor = page['cursor']
            if next_cursor < cursor or (page['events'] and next_cursor <= cursor):
                raise ValueError('Invalid event cursor')
            cursor = next_cursor
            if len(page['events']) < 500:
                break
        # Recheck after catching up: never approve a resolved/stale request.
        status = api('/tools/codex_status', {'session_id': sid})
        if status['status'] not in ACTIVE:
            emit('Session is no longer active; helper stopped.')
            return
        for approval in status['approvals']:
            aid = approval['id']
            if approval['status'] != 'pending' or aid in handled:
                continue
            reason = denial(details.get(aid, {}), files, status['repo'])
            handled.add(aid)
            if reason:
                # Never print raw command/model text; JSON escapes terminal controls.
                emit(json.dumps({'pending_for_human': aid, 'reason': reason, 'session': sid}))
                continue
            # Stable per-request key; a lost response must not become another action.
            api('/tools/codex_approve', {'session_id': sid, 'request_id': aid, 'decision': 'accept'},
                key='local-yolo-'+aid)
            emit(json.dumps({'accepted': aid, 'session': sid}))
        pause(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description='Optional local YOLO helper: weakens human review for ONE session until idle. Deny rules are heuristic, not a security boundary.')
    parser.add_argument('session_id')
    parser.add_argument('--enable-yolo', action='store_true', required=True, help='Explicitly opt into local auto-acceptance for this invocation')
    parser.add_argument('--config', type=Path, default=Path('.env'))
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9-]{1,64}', args.session_id):
        parser.error('Invalid session ID')
    load_config(args.config)
    url = urlsplit(os.getenv('BRIDGE_URL', 'http://127.0.0.1:48765'))
    if (url.scheme != 'http' or url.hostname not in {'localhost', '127.0.0.1', '::1'}
            or url.username or url.password or url.path not in {'', '/'} or url.query or url.fragment):
        parser.error('The helper requires a local loopback HTTP BRIDGE_URL')
    if len(os.getenv('BRIDGE_HUMAN_TOKEN', '')) < 32:
        parser.error('Local human credentials are required; never give them to the bot')
    print('YOLO enabled for this session only. Denied/unknown requests stay pending. Ctrl-C stops the helper.')
    try:
        watch(args.session_id)
    except KeyboardInterrupt:
        print('\nHelper stopped. Further approvals require human review.')
    except Exception:
        # HTTP errors can contain private response text; fail closed without echoing.
        raise SystemExit('Helper stopped on API/state error. Inspect locally; no automatic retry.')


if __name__ == '__main__':
    main()
