from __future__ import annotations

import argparse
import json
import os
import secrets
import shlex
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from uuid import uuid4

import httpx


def load_config(path: Path) -> None:
    if path.exists():
        for line in path.read_text().splitlines():
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            key, sep, value = line.partition('=')
            if not sep or not key.strip().replace('_','').isalnum():
                raise ValueError('Configuration must contain KEY=value assignments')
            parts = shlex.split(value,comments=True)
            os.environ.setdefault(key.strip(),parts[0] if parts else '')


def call(path: str, body: dict | None = None, *, human: bool = True, key: str | None = None) -> dict:
    token = os.getenv('BRIDGE_HUMAN_TOKEN' if human else 'BRIDGE_GROK_TOKEN','')
    url = os.getenv('BRIDGE_URL','http://127.0.0.1:48765')
    headers = {'Authorization':'Bearer '+token,'Idempotency-Key':key or str(uuid4())}
    with httpx.Client(timeout=60,trust_env=False) as client:
        r = client.post(url+path,json=body or {},headers=headers)
        if r.is_error:
            raise RuntimeError(f'HTTP {r.status_code}: {r.text}')
        return r.json()


def terminal(sid: str) -> None:
    state = call('/tools/codex_status',{'session_id':sid})
    print(f"Session {sid}\nCodex thread: {state['thread_id']}\nOwner: {state['owner']}")
    print('Commands: /takeover /grok /status /interrupt /resume /approve ID accept|decline|cancel /steer TEXT /quit /detach')
    print('Ordinary text sends a message. Takeover is explicit. Stored history follows:')
    stop = threading.Event()

    def poll() -> None:
        cursor = 0
        while not stop.is_set():
            try:
                r = call('/tools/codex_logs',{'session_id':sid,'cursor':cursor,'limit':200})
                for event in r['events']:
                    # JSON escaping prevents terminal control injection from model/tool text.
                    print('\r'+json.dumps({k:event[k] for k in ('seq','kind','data')},ensure_ascii=True),flush=True)
                cursor = r['cursor']
            except Exception as e:
                print(json.dumps({'connection_error':str(e)}),flush=True)
            stop.wait(0.5)

    thread = threading.Thread(target=poll,daemon=True)
    thread.start()
    try:
        while True:
            try:
                text = input('bridge> ').strip()
            except (EOFError,KeyboardInterrupt):
                break
            if not text:
                continue
            if text in {'/quit','/detach'}:
                break
            body = {'session_id':sid}
            tool = 'codex_send'
            path = ''
            if text in {'/takeover','/grok'}:
                path = '/sessions/control'
                body['owner'] = 'human' if text=='/takeover' else 'grok'
            elif text in {'/status','/interrupt','/resume'}:
                tool = 'codex_'+text[1:]
            elif text.startswith('/approve '):
                args = text.split()
                if len(args)!=3:
                    print('Usage: /approve REQUEST_ID accept|decline|cancel')
                    continue
                tool = 'codex_approve'
                body.update(request_id=args[1],decision=args[2])
            else:
                body.update(message=text.removeprefix('/steer '),steer=text.startswith('/steer '))
            try:
                print(json.dumps(call(path or '/tools/'+tool,body),indent=2))
            except Exception as e:
                print(str(e))
    finally:
        stop.set()
        thread.join(timeout=2)
        try:
            call('/sessions/detach', {'session_id':sid})
        except Exception:
            print('Could not record terminal detach; inspect connectivity locally.')


def main() -> None:
    parser = argparse.ArgumentParser(description='Local persistent Grok–Codex bridge')
    parser.add_argument('--config',type=Path,default=Path('.env'))
    sub = parser.add_subparsers(dest='command',required=True)
    sub.add_parser('init',help='Create private configuration with random bearer tokens')
    daemon = sub.add_parser('daemon')
    daemon.add_argument('--port',type=int,default=48765)
    sub.add_parser('list')
    p = sub.add_parser('status'); p.add_argument('session_id')
    p = sub.add_parser('register-account'); p.add_argument('account_id')
    sub.add_parser('accounts')
    for name in ['attach','terminal']:
        p = sub.add_parser(name); p.add_argument('session_id')
    p = sub.add_parser('call'); p.add_argument('tool'); p.add_argument('json',nargs='?',default='{}'); p.add_argument('--grok',action='store_true'); p.add_argument('--key')
    sub.add_parser('openapi')
    args = parser.parse_args()
    if args.command=='init':
        from .config import DEFAULT_WEBHOOK_EVENTS
        args.config.parent.mkdir(parents=True,exist_ok=True)
        state = Path('~/.local/state/grok-codex').expanduser()
        values = {'BRIDGE_STATE_DIR':str(state),'BRIDGE_REPO_ROOTS':str(Path.cwd()),
                  'BRIDGE_GROK_TOKEN':secrets.token_urlsafe(32),'BRIDGE_HUMAN_TOKEN':secrets.token_urlsafe(32),
                  'BRIDGE_WEBHOOK_URL':'','BRIDGE_WEBHOOK_SECRET':secrets.token_urlsafe(32),
                  'BRIDGE_WEBHOOK_AUTH_HEADER':'','BRIDGE_WEBHOOK_MAX_EVENT_AGE_SEC':'900',
                  'BRIDGE_WEBHOOK_PROGRESS':'0','BRIDGE_USAGE_ALERT_PERCENT':'90',
                  'BRIDGE_WEBHOOK_EVENTS':','.join(DEFAULT_WEBHOOK_EVENTS),
                  'BRIDGE_PROGRESS_INTERVAL':'10','BRIDGE_URL':'http://127.0.0.1:48765'}
        fd = os.open(args.config,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:
            f.write(''.join(k+'='+shlex.quote(v)+'\n' for k,v in values.items()))
        print(f'Created {args.config}; keep it private. Start with: uv run --locked grok-codex daemon')
        return
    load_config(args.config)
    if args.command in {'daemon','openapi'}:
        from .api import create_app
        if args.command=='openapi':
            print(json.dumps(create_app().openapi(),indent=2))
        else:
            import logging
            import uvicorn
            class JsonLog(logging.Formatter):
                def format(self, record):
                    return json.dumps({'level':record.levelname,'message':record.getMessage(),
                                       **{k:getattr(record,k) for k in ('event_id','delivery_status') if hasattr(record,k)}})
            handler = logging.StreamHandler()
            handler.setFormatter(JsonLog())
            logging.basicConfig(level=logging.INFO,handlers=[handler],force=True)
            uvicorn.run(create_app(),host='127.0.0.1',port=args.port,workers=1,access_log=False,log_config=None)
    elif args.command=='terminal':
        terminal(args.session_id)
    elif args.command=='attach':
        call('/tools/codex_status',{'session_id':args.session_id})
        if not shutil.which('tmux'):
            raise RuntimeError('Install tmux (brew install tmux), or use uv run --locked grok-codex terminal SESSION_ID')
        name = 'grok-codex-'+args.session_id
        command = shlex.join([sys.executable,'-m','grok_codex.cli','--config',str(args.config.resolve()),'terminal',args.session_id])
        exists = subprocess.run(['tmux','has-session','-t','='+name],capture_output=True).returncode==0
        if not exists:
            subprocess.run(['tmux','new-session','-d','-s',name,command],check=True)
        os.execvp('tmux',['tmux','attach-session','-t','='+name])
    else:
        mapping = {'list':('codex_list',{}),'accounts':('codex_accounts',{}),
                   'status':('codex_status',{'session_id':getattr(args,'session_id','')})}
        if args.command=='register-account':
            result = call('/accounts/register',{'account_id':args.account_id})
            # Quote login instructions for paths with spaces.
            result['login'] = 'CODEX_HOME='+shlex.quote(result['home'])+' codex login'
        else:
            tool, body = (args.tool,json.loads(args.json)) if args.command=='call' else mapping[args.command]
            path = tool if tool.startswith('/') else '/tools/'+tool
            result = call(path,body,human=not getattr(args,'grok',False),key=getattr(args,'key',None))
        print(json.dumps(result,indent=2))


if __name__=='__main__':
    try:
        main()
    except (ValueError,RuntimeError,OSError,httpx.HTTPError) as e:
        print(f'Error: {e}',file=sys.stderr)
        sys.exit(1)
