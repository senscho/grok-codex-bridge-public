"""One-command local demo; durable request keys prevent duplicate demo turns."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from .cli import load_config

PROMPT = ('Create calculator.py with a small Python add(a, b) function and '
          'test_calculator.py using unittest. Run python -m unittest discover. '
          'Work only inside this demo repository. Do not commit.')
ACTIVE = {'starting', 'dispatching', 'running', 'approval'}


def write_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(data, stream, indent=2)
    os.replace(temporary, path)


def update_config(path: Path, changes: dict[str, str]) -> None:
    lines = path.read_text().splitlines()
    lines = [line for line in lines if line.partition('=')[0].strip() not in changes]
    lines.extend(key + '=' + shlex.quote(value) for key, value in changes.items())
    temporary = path.with_suffix('.demo-tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write('\n'.join(lines) + '\n')
    os.replace(temporary, path)
    os.environ.update(changes)


class DemoRunner:
    def __init__(self, client: httpx.Client, token: str, state_path: Path):
        self.client, self.token, self.state_path = client, token, state_path
        self.state = json.loads(state_path.read_text()) if state_path.exists() else {}

    def save(self) -> None:
        write_json(self.state_path, self.state)

    def api(self, tool: str, body: dict | None = None, *, key: str | None = None) -> dict:
        headers = {'Authorization': 'Bearer ' + self.token}
        if key:
            headers['Idempotency-Key'] = key
        path = tool if tool.startswith('/') else '/tools/' + tool
        response = self.client.post(path, json=body or {}, headers=headers)
        if response.is_error:
            # Do not echo token-bearing request headers or arbitrary response bodies.
            try:
                detail = response.json().get('detail', 'Request failed')
            except ValueError:
                detail = 'This service is not the bridge API or returned an invalid response'
            raise RuntimeError(f'HTTP {response.status_code}: {detail}')
        result = response.json()
        if result.get('status') in {'uncertain', 'rejected'}:
            raise RuntimeError('The previous request was rejected or its result is uncertain. Stopping to avoid duplicate work. Inspect status.')
        return result

    def session(self, repo: Path, account: str) -> str:
        body = {'repo': str(repo), 'account': account, 'prompt': PROMPT}
        if self.state and self.state.get('request') != body:
            raise RuntimeError('The saved demo uses a different repository/account. Use a separate --state-file.')
        if self.state.get('session_id'):
            sid = self.state['session_id']
            self.api('codex_status', {'session_id': sid})
            return sid
        if not self.state.get('request_key'):
            matching = [s for s in self.api('codex_list')['sessions']
                        if Path(s['repo']).resolve() == repo and s['account'] == account and s['status'] != 'stopped']
            if len(matching) > 1:
                raise RuntimeError('Multiple sessions exist for the demo path. Inspect status first.')
            if matching:
                old = self.api('codex_status', {'session_id': matching[0]['id']})
                # Existing manual attempts may have failed before Codex even launched.
                # Stop only a proven launch failure, never an ambiguous live turn.
                events = self.api('codex_logs', {'session_id': old['id'], 'cursor': 0, 'limit': 1000})['events']
                no_launch = any(e['kind'] == 'bridge/error' and e['data'].get('error_type') in {'FileNotFoundError', 'LaunchError'} for e in events)
                if no_launch and not old['thread_id'] and not old['pid'] and old['status'] in {'error', 'interrupted'}:
                    if old['owner'] != 'grok':
                        raise RuntimeError('A human owns the existing failed session. Return control before rerunning.')
                    self.api('codex_stop', {'session_id': old['id']}, key=str(uuid4()))
                else:
                    self.state = {'request': body, 'session_id': old['id'], 'adopted': True}
                    self.save()
                    return old['id']
            self.state = {'request': body, 'request_key': str(uuid4())}
            self.save()  # Save BEFORE dispatch, including before network failures.
        result = self.api('codex_start', self.state['request'], key=self.state['request_key'])
        self.state.update(session_id=result['session_id'], job_id=result['job_id'])
        self.save()
        return result['session_id']

    def recover_auth_failure(self, sid: str, *, login_verified: bool) -> bool:
        """Retry once only after a verified login and a failed, tool-free auth turn.

        stop/resume reloads credentials in a fresh App Server while preserving the
        same bridge session and Codex thread. Every phase has a durable request key.
        """
        if not login_verified:
            return False
        retry = self.state.get('auth_retry')
        if retry and retry.get('job_id'):
            return False  # One automatic retry total, including across reruns.
        if not retry:
            status = self.api('codex_status', {'session_id': sid})
            jobs = status.get('jobs', [])
            job = jobs[0] if jobs else None
            if (status.get('owner') != 'grok' or status['status'] not in {'idle', 'failed', 'error'}
                    or not status.get('thread_id') or status.get('approvals')
                    or not job or job['status'] != 'failed' or not job.get('turn_id')):
                return False
            events = []
            cursor = 0
            while True:
                page = self.api('codex_logs', {'session_id': sid, 'cursor': cursor, 'limit': 1000})
                events.extend(page['events'])
                next_cursor = page.get('cursor', cursor)
                if len(page['events']) < 1000 or next_cursor <= cursor:
                    break
                cursor = next_cursor
            starts = [i for i, e in enumerate(events) if e['kind'] == 'turn/started'
                      and e['data'].get('turn_id') == job['turn_id']]
            if not starts:
                return False  # Missing history cannot prove there were no tool effects.
            tail = events[starts[-1]:]
            completions = [i for i, e in enumerate(tail) if e['kind'] == 'turn/completed'
                           and e['data'].get('turn_id') == job['turn_id']]
            if not completions:
                return False
            after = tail[completions[-1] + 1:]
            if any(e['kind'].startswith(('item/', 'turn/', 'approval/', 'request/', 'process/')) for e in after):
                return False
            tail = tail[:completions[-1] + 1]
            if (tail[-1]['data'].get('status') != 'failed'
                    or not any(e['kind'] == 'account/authentication_required' for e in tail)):
                return False
            safe_items = {'userMessage', 'agentMessage', 'reasoning'}
            safe_item_events = {'item/started', 'item/completed', 'item/agentMessage/delta'}
            for event in tail:
                kind = event['kind']
                if kind in {'item/started', 'item/completed'} and event['data'].get('type') not in safe_items:
                    return False
                if kind.startswith('item/') and kind not in safe_item_events and not kind.startswith('item/reasoning/'):
                    return False
                if kind.startswith(('approval/', 'request/')) or kind in {'process/lost', 'bridge/error'}:
                    return False
                if kind == 'turn/started' and event['data'].get('turn_id') != job['turn_id']:
                    return False
            retry = {'failed_job_id': job['id'], 'thread_id': status['thread_id'],
                     'stop_key': str(uuid4()), 'resume_key': str(uuid4()), 'send_key': str(uuid4()),
                     'message': self.state['request']['prompt']}
            self.state.update(auth_retry=retry, cursor=tail[-1]['seq'])
            self.save()  # Journal before stopping, resuming, or sending anything.
        print('Confirmed authentication failure before login. Retrying once in the same thread, before any tool execution.', flush=True)
        if not retry.get('resumed'):
            self.api('codex_stop', {'session_id': sid}, key=retry['stop_key'])
            resumed = self.api('codex_resume', {'session_id': sid}, key=retry['resume_key'])
            if resumed.get('thread_id') != retry['thread_id']:
                raise RuntimeError('The resumed thread ID changed. No work was sent.')
            retry['resumed'] = True
            self.save()
        result = self.api('codex_send', {'session_id': sid, 'message': retry['message']}, key=retry['send_key'])
        retry['job_id'] = result['job_id']
        self.state['job_id'] = result['job_id']
        self.save()
        return True

    def wait(self, sid: str, timeout: float, *, approve=None) -> dict:
        deadline = time.monotonic() + timeout
        cursor = self.state.get('cursor', 0)
        last_status = None
        while time.monotonic() < deadline:
            status = self.api('codex_status', {'session_id': sid})
            if status['status'] != last_status:
                print('Status:', status['status'], flush=True)
                last_status = status['status']
            events = self.api('codex_logs', {'session_id': sid, 'cursor': cursor, 'limit': 200})
            for event in events['events']:
                if event['kind'] in {'bridge/error', 'item/completed', 'turn/completed', 'approval/required', 'account/authentication_required'}:
                    print(json.dumps({'event': event['kind'], 'data': event['data']}, ensure_ascii=True), flush=True)
            cursor = events['cursor']
            self.state['cursor'] = cursor
            self.save()
            pending = [a for a in status['approvals'] if a['status'] == 'pending']
            for approval in pending:
                if approve is None:
                    raise RuntimeError('Explicit approval required. Rerun this script in a terminal. Approvals are never automatic.')
                decision = approve(approval)
                if decision not in {'accept', 'decline', 'cancel'}:
                    raise ValueError('Invalid approval decision')
                self.api('codex_approve', {'session_id': sid, 'request_id': approval['id'], 'decision': decision}, key=str(uuid4()))
            jobs = status.get('jobs', [])
            job = next((j for j in jobs if j['id'] == self.state.get('job_id')), jobs[0] if jobs else None)
            if job and job['status'] in {'uncertain', 'failed', 'interrupted'}:
                raise RuntimeError(job.get('detail') or 'Work failed/interrupted. It will not be retried automatically.')
            if status['status'] == 'idle':
                if not job or job['status'] != 'completed':
                    raise RuntimeError('History loaded, but demo completion is unconfirmed. Interrupted instructions are not resent automatically.')
                self.state['completed'] = True
                self.save()
                return status
            if status['status'] not in ACTIVE:
                raise RuntimeError(f"Session status is {status['status']}. Inspect history. No new work is created automatically.")
            time.sleep(0.5)
        raise RuntimeError('Wait timed out. The agent may still be running. Rerun the same command to inspect the existing session.')


def approval_prompt(approval: dict) -> str:
    print('Approval request:', approval['method'])
    answer = input('Review the request above. Accept [a] / decline [d, default] / cancel [c]: ').strip().lower()
    return {'a': 'accept', 'c': 'cancel'}.get(answer, 'decline')


def ensure_daemon(root: Path, config: Path, client: httpx.Client, token: str, port: int, log: Path) -> None:
    headers = {'Authorization': 'Bearer ' + token}
    try:
        result = client.post('/tools/codex_accounts', json={}, headers=headers, timeout=2)
    except httpx.TransportError:
        result = None
    if result is not None:
        if result.status_code != 200 or 'accounts' not in result.json():
            raise RuntimeError('The service on this port is not the configured bridge. Existing processes will not be terminated.')
        print('Using the running bridge.', flush=True)
        return
    with log.open('ab') as output:
        log.chmod(0o600)
        process = subprocess.Popen([sys.executable, '-m', 'grok_codex.cli', '--config', str(config), 'daemon', '--port', str(port)],
                                   cwd=root, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError(f'Daemon startup failed. Diagnostic log: {log}. Check whether another port uses the same state directory.')
        try:
            r = client.post('/tools/codex_accounts', json={}, headers=headers, timeout=1)
            if r.status_code == 200 and 'accounts' in r.json():
                print('Started the bridge in the background.', flush=True)
                return
        except httpx.TransportError:
            pass
        time.sleep(.1)
    raise RuntimeError(f'Daemon readiness timed out. Log: {log}')


def main() -> None:
    parser = argparse.ArgumentParser(description='One-command local Codex demo')
    parser.add_argument('--repo', type=Path, default=Path.home() / 'dev/bridge-demo')
    parser.add_argument('--account', default='personal')
    parser.add_argument('--config', type=Path, default=Path('.env'))
    parser.add_argument('--state-file', type=Path, default=Path('.bridge/demo.json'))
    parser.add_argument('--timeout', type=float, default=600)
    parser.add_argument('--attach', action='store_true', help='Attach to the same thread in tmux after completion')
    args = parser.parse_args()
    root = Path.cwd()
    config, state_file, repo = args.config.resolve(), args.state_file.resolve(), args.repo.expanduser().resolve()
    state_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_file.parent.chmod(0o700)
    with state_file.with_suffix('.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('Another demo runner is active.') from exc
        if not config.exists():
            subprocess.run([sys.executable, '-m', 'grok_codex.cli', '--config', str(config), 'init'], check=True)
        load_config(config)
        binary = shutil.which(os.path.expanduser(os.getenv('BRIDGE_CODEX_BINARY', 'codex'))) or shutil.which('codex')
        if not binary:
            raise RuntimeError('Codex CLI is missing. Run brew install --cask codex, then retry.')
        url = os.getenv('BRIDGE_URL', 'http://127.0.0.1:48765')
        parsed = urlsplit(url)
        if parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost'} or not parsed.port:
            raise RuntimeError('The demo requires a local HTTP BRIDGE_URL with an explicit port.')
        roots = [Path(p).expanduser().resolve() for p in os.getenv('BRIDGE_REPO_ROOTS', str(root)).split(os.pathsep)]
        if not any(repo.is_relative_to(p) for p in roots):
            roots.append(repo)
        update_config(config, {'BRIDGE_CODEX_BINARY': binary, 'BRIDGE_REPO_ROOTS': os.pathsep.join(map(str, roots))})
        human_token = os.environ['BRIDGE_HUMAN_TOKEN']
        with httpx.Client(base_url=url.rstrip('/'), timeout=60, trust_env=False) as client:
            ensure_daemon(root, config, client, human_token, parsed.port, state_file.parent / 'demo-daemon.log')
            human = DemoRunner(client, human_token, state_file)
            account = human.api('/accounts/register', {'account_id': args.account}, key=str(uuid4()))
            env = {k: v for k, v in os.environ.items() if not k.startswith('BRIDGE_') and k not in {'OPENAI_API_KEY', 'CODEX_API_KEY'}}
            env['CODEX_HOME'] = account['home']
            auth = subprocess.run([binary, 'login', 'status'], env=env, capture_output=True)
            if auth.returncode:
                if not sys.stdin.isatty():
                    raise RuntimeError('Codex login required. Rerun the same command in an interactive terminal.')
                print('Initial account login required. Complete the official Codex login flow.', flush=True)
                subprocess.run([binary, 'login'], env=env, check=True)
                if subprocess.run([binary, 'login', 'status'], env=env, capture_output=True).returncode:
                    raise RuntimeError('Login could not be verified. Coding work will not start automatically.')
            repo.mkdir(parents=True, exist_ok=True)
            if not (repo / '.git').exists():
                subprocess.run(['git', '-C', str(repo), 'init', '-b', 'main'], check=True)
            # Grok credentials submit work; the separate human credential handles approvals.
            grok = DemoRunner(client, os.environ['BRIDGE_GROK_TOKEN'], state_file)
            sid = grok.session(repo, args.account)
            grok.recover_auth_failure(sid, login_verified=True)
            human.state = grok.state
            print('SESSION_ID=' + sid, flush=True)
            if grok.state.get('job_id'):
                print('JOB_ID=' + grok.state['job_id'], flush=True)
            print('IDs are saved automatically: ' + str(state_file), flush=True)
            status = human.wait(sid, args.timeout, approve=approval_prompt if sys.stdin.isatty() else None)
            if not (repo / 'calculator.py').is_file() or not any(repo.glob('test*.py')):
                raise RuntimeError('Demo files were not created. Inspect the saved session history.')
            subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(repo)], cwd=repo, check=True, timeout=60)
            print('Demo succeeded. Codex thread: ' + status['thread_id'], flush=True)
            print('Rerunning inspects the existing result without creating new work.', flush=True)
            attach = [sys.executable, '-m', 'grok_codex.cli', '--config', str(config), 'attach', sid]
            print('Human terminal: ' + shlex.join(['uv', 'run', '--locked', 'grok-codex', '--config', str(config), 'attach', sid]))
            if args.attach:
                subprocess.run(attach, check=True)


def entrypoint() -> None:
    try:
        main()
    except (RuntimeError, ValueError, OSError, httpx.HTTPError, subprocess.SubprocessError, KeyError) as exc:
        print('Demo stopped: ' + str(exc), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print('\nStopped viewing. The daemon/agent remains running. Rerun the same command to view it.', file=sys.stderr)
        sys.exit(130)


if __name__ == '__main__':
    entrypoint()
