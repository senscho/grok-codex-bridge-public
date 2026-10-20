
import asyncio
import fcntl
import hashlib
import json
import logging
import secrets
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from .config import Settings
from .protocol import LaunchError, RpcError
from .service import Bridge, Conflict


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Options(Model):
    sandbox: Literal['workspace-write','read-only'] = 'workspace-write'
    approval_policy: Literal['on-request','untrusted'] = 'on-request'
    model: str | None = Field(default=None,max_length=100)


class Start(Model):
    repo: str = Field(min_length=1,max_length=4096)
    account: str = Field(min_length=1,max_length=48)
    prompt: str = Field(min_length=1,max_length=100_000)
    options: Options = Field(default_factory=Options)


class Session(Model):
    session_id: str = Field(pattern=r'^[A-Za-z0-9-]{1,64}$')


class Send(Session):
    message: str = Field(min_length=1,max_length=100_000)
    steer: bool = False


class Logs(Session):
    cursor: int = Field(default=0,ge=0)
    limit: int = Field(default=100,ge=1,le=1000)


class Approve(Session):
    request_id: str = Field(min_length=1,max_length=64)
    decision: Literal['accept','decline','cancel']


class Switch(Session):
    account_id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,47}$')


class Control(Session):
    owner: Literal['human','grok']


class Register(Model):
    account_id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,47}$')


class Replay(Session):
    cursor: int = Field(default=0,ge=0)


security = HTTPBearer(auto_error=False)


def create_app(settings: Settings | None = None, bridge: Bridge | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.validate()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        with (settings.state_dir / 'daemon.lock').open('a') as lockfile:
            try:
                fcntl.flock(lockfile,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError as e:
                raise RuntimeError('Another daemon is using this state directory; run exactly one worker') from e
            b = bridge or Bridge(settings)
            app.state.bridge = b
            await b.startup()
            try:
                yield
            finally:
                await b.shutdown()

    app = FastAPI(title='Grok–Codex Local Bridge',version='0.1.0',lifespan=lifespan,
                  description='Persistent Codex threads. Use distinct Grok and human bearer tokens. Mutations require Idempotency-Key. Poll jobs/status or consume signed metadata callbacks.')

    async def actor(request: Request, auth: Annotated[HTTPAuthorizationCredentials | None,Depends(security)]) -> str:
        token = auth.credentials if auth else ''
        if secrets.compare_digest(token,settings.human_token):
            return 'human'
        if secrets.compare_digest(token,settings.grok_token):
            return 'grok'
        raise HTTPException(401,'Invalid bearer token',headers={'WWW-Authenticate':'Bearer'})

    Actor = Annotated[str,Depends(actor)]
    Key = Annotated[str | None,Header(alias='Idempotency-Key',max_length=128)]

    def b() -> Bridge:
        return app.state.bridge

    async def mutate(who: str, key: str | None, operation: str, body: BaseModel, call):
        if not key:
            raise HTTPException(400,'Idempotency-Key required for mutations; use a stable UUID per logical request')
        fingerprint = hashlib.sha256((operation+body.model_dump_json()).encode()).hexdigest()
        async with b().idempotency_lock:
            prev = b().store.one('SELECT * FROM requests WHERE actor=? AND key=?',(who,key))
            if prev:
                if prev['fingerprint'] != fingerprint:
                    raise Conflict('Idempotency key was reused with different arguments')
                return json.loads(prev['result'])
            # Reserve before dispatch; a crash cannot cause the same key to rerun work.
            placeholder = {'status':'uncertain','reason':'Dispatch result unavailable; inspect sessions/jobs before submitting another request'}
            b().store.execute('INSERT INTO requests(actor,key,fingerprint,result) VALUES(?,?,?,?)',(who,key,fingerprint,json.dumps(placeholder)))
            try:
                result = await call()
            except (ValueError,KeyError,PermissionError) as e:
                # Retain the reservation for operations that may have had side effects.
                result = {'status':'rejected','reason':str(e)}
                b().store.execute('UPDATE requests SET result=? WHERE actor=? AND key=?',(json.dumps(result),who,key))
                raise
            b().store.execute('UPDATE requests SET result=? WHERE actor=? AND key=?',(json.dumps(result),who,key))
            return result

    @app.exception_handler(Conflict)
    async def conflict_handler(request, exc):
        return JSONResponse(status_code=409,content={'detail':str(exc)})

    @app.exception_handler(KeyError)
    async def key_handler(request, exc):
        return JSONResponse(status_code=404,content={'detail':str(exc)})

    @app.exception_handler(PermissionError)
    async def permission_handler(request, exc):
        return JSONResponse(status_code=403,content={'detail':str(exc)})

    @app.exception_handler(ValueError)
    async def value_handler(request, exc):
        return JSONResponse(status_code=400,content={'detail':str(exc)})

    @app.exception_handler(LaunchError)
    async def launch_handler(request, exc):
        return JSONResponse(status_code=503,content={'detail':exc.detail})

    @app.exception_handler(RpcError)
    async def rpc_handler(request, exc):
        return JSONResponse(status_code=502,content={'detail':'Codex rejected the protocol request; inspect events and account authentication','rpc_code':exc.code})

    @app.exception_handler(ConnectionError)
    @app.exception_handler(TimeoutError)
    async def connection_handler(request, exc):
        return JSONResponse(status_code=503,content={'detail':'Codex connection unavailable or dispatch uncertain; inspect session before retrying'})

    @app.get('/healthz')
    async def health():
        return {'status':'ok'}

    @app.post('/tools/codex_start',operation_id='codex_start',status_code=202)
    async def start(body: Start, who: Actor, key: Key = None):
        return await mutate(who,key,'start',body,lambda: b().start(who,body.repo,body.account,body.prompt,body.options.model_dump()))

    @app.post('/tools/codex_send',operation_id='codex_send',status_code=202)
    async def send(body: Send, who: Actor, key: Key = None):
        return await mutate(who,key,'send',body,lambda: b().send(body.session_id,who,body.message,body.steer))

    @app.post('/tools/codex_status',operation_id='codex_status')
    async def status(body: Session, who: Actor):
        return b().status(body.session_id)

    @app.post('/tools/codex_logs',operation_id='codex_logs')
    async def logs(body: Logs, who: Actor):
        b().store.session(body.session_id)
        return b().store.logs(body.session_id,body.cursor,body.limit)

    @app.post('/tools/codex_list',operation_id='codex_list')
    async def listing(who: Actor):
        return {'sessions':b().store.all('SELECT id,thread_id,repo,account,status,owner,turn_id,pid FROM sessions ORDER BY created DESC')}

    @app.post('/tools/codex_accounts',operation_id='codex_accounts')
    async def accounts(who: Actor):
        return {'accounts':b().accounts()}

    @app.post('/accounts/register',operation_id='register_account')
    async def register(body: Register, who: Actor, key: Key = None):
        async def action():
            if who != 'human':
                raise PermissionError('Register accounts with human credentials')
            return b().register(body.account_id)
        return await mutate(who,key,'register',body,action)

    @app.post('/tools/codex_approve',operation_id='codex_approve')
    async def approve(body: Approve, who: Actor, key: Key = None):
        return await mutate(who,key,'approve',body,lambda: b().approve(body.session_id,who,body.request_id,body.decision))

    @app.post('/tools/codex_interrupt',operation_id='codex_interrupt')
    async def interrupt(body: Session, who: Actor, key: Key = None):
        return await mutate(who,key,'interrupt',body,lambda: b().interrupt(body.session_id,who))

    @app.post('/tools/codex_resume',operation_id='codex_resume')
    async def resume(body: Session, who: Actor, key: Key = None):
        return await mutate(who,key,'resume',body,lambda: b().resume(body.session_id,who))

    @app.post('/tools/codex_stop',operation_id='codex_stop')
    async def stop(body: Session, who: Actor, key: Key = None):
        return await mutate(who,key,'stop',body,lambda: b().stop(body.session_id,who))

    @app.post('/tools/codex_switch_account',operation_id='codex_switch_account')
    async def switch(body: Switch, who: Actor, key: Key = None):
        return await mutate(who,key,'switch',body,lambda: b().switch(body.session_id,who,body.account_id))

    @app.post('/tools/codex_attach_info',operation_id='codex_attach_info')
    async def attach_info(body: Session, who: Actor):
        s = b().store.session(body.session_id)
        return {'session_id':s['id'],'thread_id':s['thread_id'],'mode':'bridge-terminal',
                'tmux_session':'grok-codex-'+s['id'], 'command':['uv','run','--locked','grok-codex','attach',s['id']],
                'native_remote_tui':'CLI supports --remote; not enabled for managed stdio sessions because it bypasses ownership'}

    @app.post('/sessions/control',operation_id='codex_control')
    async def control(body: Control, who: Actor, key: Key = None):
        return await mutate(who,key,'control',body,lambda: b().control(body.session_id,who,body.owner))

    @app.post('/events/replay',operation_id='codex_replay')
    async def replay(body: Replay, who: Actor, key: Key = None):
        async def action():
            b().store.session(body.session_id)
            return {'replayed_events':b().dispatcher.replay(body.session_id,body.cursor)}
        return await mutate(who,key,'replay',body,action)

    @app.get('/sessions/{session_id}/events',description='Authenticated SSE; event IDs are SQLite sequence cursors. Reconnect with cursor query parameter.')
    async def stream(session_id: str, request: Request, who: Actor, cursor: int = 0):
        if cursor < 0:
            raise ValueError('cursor must be nonnegative')
        b().store.session(session_id)
        async def generate():
            position = cursor
            while not await request.is_disconnected():
                rows = b().store.logs(session_id,position,200)
                for event in rows['events']:
                    yield 'id: '+str(event['seq'])+'\ndata: '+json.dumps(event)+'\n\n'
                position = rows['cursor']
                if not rows['events']:
                    yield ': heartbeat\n\n'
                    await asyncio.sleep(1)
        return StreamingResponse(generate(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

    @app.get('/jobs/{job_id}')
    async def job(job_id: str, who: Actor):
        row = b().store.one('SELECT * FROM jobs WHERE id=?',(job_id,))
        if not row:
            raise KeyError('Unknown job')
        return row

    @app.get('/deliveries')
    async def deliveries(who: Actor):
        return {'deliveries':b().store.all('SELECT id,attempts,due,status FROM deliveries ORDER BY due DESC LIMIT 100')}

    @app.get('/dashboard',response_class=HTMLResponse)
    async def dashboard():
        return '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Codex Bridge</title>
<style>body{font:16px system-ui;max-width:1000px;margin:3rem auto;padding:1rem;background:#101820;color:#eef}input,button{padding:.7rem}pre{white-space:pre-wrap}</style>
<h1>Local Codex bridge</h1><p>Enter a bearer token to inspect sessions. It stays in this page's memory.</p>
<input id="token" type="password" placeholder="Bearer token"><button id="refresh">Refresh</button><pre id="output"></pre>
<script>document.getElementById('refresh').onclick=async()=>{let r=await fetch('/tools/codex_list',{method:'POST',headers:{Authorization:'Bearer '+document.getElementById('token').value}});document.getElementById('output').textContent=JSON.stringify(await r.json(),null,2)}</script></html>'''

    return app
