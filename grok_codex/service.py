from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shlex
import time
from pathlib import Path
from typing import Any, Callable

from .config import Settings
from .protocol import AppServer, LaunchError, RpcError
from .store import Store, uid
from .webhooks import Dispatcher


class Conflict(ValueError):
    pass


def redact(value: str) -> str:
    value = re.sub(r'-----BEGIN .*?PRIVATE KEY-----.*?-----END .*?PRIVATE KEY-----', '[REDACTED KEY]', value, flags=re.S)
    value = re.sub(r'(?i)(bearer\s+|(?:api[_-]?key|token|password|secret)\s*[=:]\s*)[^\s,;"\'}]+', r'\1[REDACTED]', value)
    value = re.sub(r'\b(?:sk-|xai-)[A-Za-z0-9_-]{12,}', '[REDACTED]', value)
    return value[:8000]


class Bridge:
    def __init__(self, settings: Settings, factory: Callable = AppServer):
        settings.validate()
        self.settings = settings
        self.store = Store(settings.state_dir / 'bridge.sqlite3')
        self.factory = factory
        self.servers: dict[str, AppServer] = {}
        self.locks: dict[str, asyncio.Lock] = {}
        self.tasks: set[asyncio.Task] = set()
        self.dispatcher = Dispatcher(self.store, settings)
        self.dispatch_task: asyncio.Task | None = None
        self.recovery_task: asyncio.Task | None = None
        self.idempotency_lock = asyncio.Lock()
        self.closing = False
        self.disconnected: set[str] = set()

    def lock(self, sid: str) -> asyncio.Lock:
        return self.locks.setdefault(sid, asyncio.Lock())

    def event(self, sid: str, kind: str, data: dict | None = None, urgent: bool = False) -> None:
        self.store.event(sid, kind, data, urgent)
        self.dispatcher.wake.set()

    def spawn(self, coroutine: Any) -> None:
        t = asyncio.create_task(coroutine)
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)

    def own(self, sid: str, actor: str) -> dict:
        s = self.store.session(sid)
        if s['owner'] != actor:
            raise Conflict(f"Control is owned by {s['owner']}; human can explicitly take over")
        return s

    async def startup(self) -> None:
        self.dispatch_task = asyncio.create_task(self.dispatcher.run())
        # Reconcile jobs before any new work is accepted. No prompt replay.
        self.store.execute("UPDATE jobs SET status='uncertain',detail='Daemon restarted; inspect history before resubmitting' WHERE status IN ('queued','dispatching','running')")
        self.store.execute("UPDATE approvals SET status='stale' WHERE status IN ('pending','responding')")
        self.recovery_task = asyncio.create_task(self._recover_all())

    async def _recover_all(self) -> None:
        for s in self.store.all("SELECT * FROM sessions WHERE status!='stopped'"):
            async with self.lock(s['id']):
                if self._pid_alive(s['pid']):
                    self.store.update(s['id'], status='recovery_blocked')
                    self.event(s['id'], 'recovery/blocked', {'reason': 'Recorded process still exists; verify and terminate it before resume'}, True)
                    continue
                self.store.update(s['id'], status='interrupted', pid=None, turn_id=None)
                if s['thread_id']:
                    try:
                        await self._resume(s['id'], recovery=True)
                    except Exception as e:
                        self._failure(s['id'], None, e)
                else:
                    self.event(s['id'], 'recovery/interrupted', {'reason': 'Thread creation was interrupted; original prompt is not replayed'}, True)

    @staticmethod
    def _pid_alive(pid: int | None) -> bool:
        if not pid:
            return False
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    async def shutdown(self) -> None:
        self.closing = True
        if self.recovery_task:
            self.recovery_task.cancel()
            await asyncio.gather(self.recovery_task, return_exceptions=True)
        for t in list(self.tasks):
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        for sid, server in list(self.servers.items()):
            await server.close()
            s = self.store.session(sid)
            self.store.update(sid, pid=None, turn_id=None, status='interrupted' if s['status'] in {'running','dispatching','approval'} else s['status'])
        self.store.execute("UPDATE approvals SET status='stale' WHERE status IN ('pending','responding')")
        self.store.execute("UPDATE jobs SET status='uncertain' WHERE status IN ('queued','dispatching','running')")
        if self.dispatch_task:
            self.dispatch_task.cancel()
            await asyncio.gather(self.dispatch_task, return_exceptions=True)
        await self.dispatcher.client.aclose()
        self.store.db.close()

    def register(self, account_id: str) -> dict:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,47}', account_id):
            raise ValueError('Invalid account ID')
        home = self.settings.state_dir / 'accounts' / account_id
        if home.is_symlink() or home.parent.is_symlink():
            raise ValueError('Account directories must not be symlinks')
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        home.chmod(0o700)
        self.store.execute('INSERT OR IGNORE INTO accounts(id,home) VALUES(?,?)', (account_id,str(home)))
        return {'id':account_id, 'home':str(home), 'login':f'CODEX_HOME={shlex.quote(str(home))} codex login'}

    def accounts(self) -> list[dict]:
        # Never open auth.json, never report credential contents.
        return self.store.all('SELECT id,home FROM accounts ORDER BY id')

    def account(self, aid: str) -> dict:
        a = self.store.one('SELECT * FROM accounts WHERE id=?', (aid,))
        if not a:
            raise ValueError('Account is not registered; register and login locally first')
        return a

    async def new_server(self, sid: str, account: str | None = None) -> AppServer:
        s = self.store.session(sid)
        a = self.account(account or s['account'])
        server = self.factory(self.settings.codex_binary, Path(a['home']), lambda msg: self.notification(sid,msg), self.settings.rpc_timeout)
        self.servers[sid] = server
        try:
            await server.start()
            self.store.update(sid, pid=server.pid, process_started=time.time())
        except BaseException:
            await server.close()
            self.servers.pop(sid,None)
            self.store.update(sid, pid=None)
            raise
        return server

    async def start(self, actor: str, repo: str, account: str, prompt: str, options: dict) -> dict:
        repo = self.settings.repository(repo)
        self.account(account)
        if len(self.store.all("SELECT id FROM sessions WHERE status!='stopped'")) >= self.settings.max_sessions:
            raise Conflict('Maximum active sessions reached')
        # Different conversations must not write the same working tree concurrently.
        for other in self.store.all("SELECT repo FROM sessions WHERE status!='stopped'"):
            p, q = Path(repo), Path(other['repo'])
            if p.is_relative_to(q) or q.is_relative_to(p):
                raise Conflict('Repository already has a managed session; reuse it or create a separate Git worktree')
        sid = uid()
        self.store.execute('INSERT INTO sessions(id,repo,account,status,owner,options,created) VALUES(?,?,?,?,?,?,?)',
                           (sid,repo,account,'starting',actor,json.dumps(options),time.time()))
        jid = self.store.job(sid, 'start')
        self.spawn(self._create(sid,jid,prompt))
        return {'session_id':sid,'job_id':jid,'status':'queued'}

    def _params(self, s: dict) -> dict:
        return {'cwd':s['repo'],'sandbox':s['options'].get('sandbox','workspace-write'),
                'approvalPolicy':s['options'].get('approval_policy','on-request'),'approvalsReviewer':'user',
                **({'model':s['options']['model']} if s['options'].get('model') else {})}

    async def _create(self, sid: str, jid: str, prompt: str) -> None:
        async with self.lock(sid):
            try:
                self.store.execute("UPDATE jobs SET status='dispatching' WHERE id=?",(jid,))
                server = await self.new_server(sid)
                r = await server.request('thread/start', {**self._params(self.store.session(sid)), 'ephemeral':False})
                self.store.update(sid, thread_id=r['thread']['id'], status='idle')
                self.event(sid,'session/started',{'thread_id':r['thread']['id']})
                await self._turn(sid,jid,prompt,False)
            except Exception as e:
                self._failure(sid,jid,e)

    async def send(self, sid: str, actor: str, message: str, steer: bool = False) -> dict:
        async with self.lock(sid):
            s = self.own(sid,actor)
            if s['status'] != 'idle' and not (steer and s['status']=='running' and s['turn_id']):
                raise Conflict('Session must be idle, or explicitly steer a running turn')
            jid = self.store.job(sid,'steer' if steer else 'send')
            self.store.update(sid,status='dispatching')
            self.spawn(self._send(sid,jid,message,steer))
            return {'session_id':sid,'job_id':jid,'status':'queued'}

    async def _send(self, sid: str, jid: str, message: str, steer: bool) -> None:
        async with self.lock(sid):
            try:
                await self._turn(sid,jid,message,steer)
            except Exception as e:
                self._failure(sid,jid,e)

    async def _turn(self, sid: str, jid: str, message: str, steer: bool) -> None:
        s = self.store.session(sid)
        self.store.execute("UPDATE jobs SET status='dispatching' WHERE id=?", (jid,))
        self.store.update(sid,status='dispatching')
        self.event(sid,'conversation/user',{'text':redact(message)})
        params = {'threadId':s['thread_id'],'input':[{'type':'text','text':message}], 'clientUserMessageId':jid}
        if steer:
            params['expectedTurnId'] = s['turn_id']
        r = await self.servers[sid].request('turn/steer' if steer else 'turn/start',params)
        turn_id = r.get('turn',{}).get('id') or r.get('turnId') or s['turn_id']
        self.store.execute("UPDATE jobs SET status='running',turn_id=? WHERE id=? AND status='dispatching'",(turn_id,jid))
        if self.store.session(sid)['status']=='dispatching':
            self.store.update(sid,status='running',turn_id=turn_id)

    def _failure(self, sid: str, jid: str | None, error: Exception) -> None:
        uncertain = not isinstance(error, (RpcError, LaunchError))
        # Error messages may contain code or secrets; return a safe category.
        detail = 'App Server rejected request; check authentication and installed protocol' if not uncertain else 'Connection/dispatch uncertain; inspect history before resubmitting'
        if isinstance(error, LaunchError):
            detail = error.detail
        self.store.update(sid,status='interrupted' if uncertain else 'error')
        if jid:
            self.store.execute('UPDATE jobs SET status=?,detail=? WHERE id=?',('uncertain' if uncertain else 'failed',detail,jid))
        self.event(sid,'bridge/error',{'reason':detail,'error_type':type(error).__name__},True)
        if isinstance(error,RpcError) and re.search(r'auth|401|403|login|token',str(error),re.I):
            self.event(sid,'account/authentication_required',{},True)

    async def _resume(self, sid: str, recovery: bool = False) -> dict:
        s = self.store.session(sid)
        if not s['thread_id']:
            raise Conflict('No persisted thread ID; creation did not complete')
        if s['pid'] and sid not in self.servers and self._pid_alive(s['pid']):
            raise Conflict('Old process still exists. Verify its identity and terminate it locally before resume')
        for other in self.store.all("SELECT id,repo FROM sessions WHERE id!=? AND status!='stopped'",(sid,)):
            if Path(s['repo']).is_relative_to(Path(other['repo'])) or Path(other['repo']).is_relative_to(Path(s['repo'])):
                raise Conflict('Another managed session overlaps this repository; stop it before resuming')
        if sid in self.servers and (sid in self.disconnected or s['status'] not in {'idle','stopped'}):
            await self.servers.pop(sid).close()
            self.disconnected.discard(sid)
            self.store.update(sid,pid=None)
            self.store.execute("UPDATE approvals SET status='stale' WHERE session_id=? AND status IN ('pending','responding')",(sid,))
            self.store.execute("UPDATE jobs SET status='uncertain' WHERE session_id=? AND status IN ('queued','dispatching','running')",(sid,))
        server = self.servers.get(sid) or await self.new_server(sid)
        r = await server.request('thread/resume', {**self._params(s),'threadId':s['thread_id'],'excludeTurns':True})
        status = r['thread'].get('status',{}).get('type')
        # A freshly launched server cannot continue an interrupted execution.
        # Do not infer that old inProgress records are live turns.
        self.store.update(sid,status='idle',turn_id=None)
        self.event(sid,'recovery/resumed' if recovery else 'session/resumed',{'thread_id':s['thread_id'],'reported_status':status})
        return self.status(sid)

    async def resume(self, sid: str, actor: str) -> dict:
        async with self.lock(sid):
            s = self.own(sid,actor)
            if s['status'] in {'running','dispatching','approval','starting'}:
                raise Conflict('Cannot resume while a turn or creation is active')
            try:
                return await self._resume(sid)
            except Exception as e:
                self._failure(sid,None,e)
                raise

    async def interrupt(self, sid: str, actor: str) -> dict:
        async with self.lock(sid):
            s = self.own(sid,actor)
            if not s['turn_id'] or sid not in self.servers or s['status'] not in {'running','approval'}:
                raise Conflict('No active turn to interrupt')
            await self.servers[sid].request('turn/interrupt',{'threadId':s['thread_id'],'turnId':s['turn_id']})
            self.event(sid,'turn/interrupt_requested',{},True)
            return self.status(sid)

    async def stop(self, sid: str, actor: str) -> dict:
        async with self.lock(sid):
            self.own(sid,actor)
            if sid in self.servers:
                await self.servers.pop(sid).close()
            elif self._pid_alive(self.store.session(sid)['pid']):
                raise Conflict('Unowned process exists; terminate it locally after verifying identity')
            self.store.update(sid,status='stopped',turn_id=None,pid=None)
            self.store.execute("UPDATE approvals SET status='stale' WHERE session_id=? AND status='pending'",(sid,))
            self.store.execute("UPDATE jobs SET status='interrupted' WHERE session_id=? AND status IN ('queued','dispatching','running')",(sid,))
            self.event(sid,'session/stopped',{},True)
            return self.status(sid)

    async def control(self, sid: str, actor: str, owner: str) -> dict:
        if actor != 'human':
            raise PermissionError('Only human credentials can change control ownership')
        async with self.lock(sid):
            self.store.session(sid)
            self.store.update(sid,owner=owner)
            self.event(sid,'control/changed',{'owner':owner},True)
            return self.status(sid)

    async def switch(self, sid: str, actor: str, account_id: str) -> dict:
        if actor != 'human':
            raise PermissionError('Account switches require human credentials')
        async with self.lock(sid):
            s = self.own(sid,actor)
            if s['status'] != 'idle':
                raise Conflict('Account switch requires an idle turn boundary')
            self.account(account_id)
            if account_id == s['account']:
                return self.status(sid)
            if sid in self.servers:
                await self.servers.pop(sid).close()
            self.store.update(sid,pid=None)
            try:
                server = await self.new_server(sid,account_id)
                await server.request('thread/resume',{**self._params(s),'threadId':s['thread_id'],'excludeTurns':True})
            except Exception:
                if sid in self.servers:
                    await self.servers.pop(sid).close()
                self.store.update(sid,pid=None,status='interrupted')
                self.event(sid,'account/switch_unavailable',{'account_id':account_id},True)
                try:
                    await self._resume(sid)
                except Exception as e:
                    self._failure(sid,None,e)
                raise Conflict('Thread is unavailable in target CODEX_HOME; original account retained. Use explicit handoff to a new session')
            self.store.update(sid,account=account_id,status='idle')
            self.event(sid,'account/switched',{'account_id':account_id},True)
            return self.status(sid)

    async def approve(self, sid: str, actor: str, request_id: str, decision: str) -> dict:
        async with self.lock(sid):
            self.store.session(sid)
            if actor != 'human' and decision=='accept':
                raise PermissionError('Accepting sensitive operations requires human credentials')
            if actor != 'human':
                self.own(sid,actor)
            a = self.store.one('SELECT * FROM approvals WHERE id=? AND session_id=?',(request_id,sid))
            if not a:
                raise KeyError('Unknown approval request')
            if a['status']=='resolved' and a['decision']==decision:
                return {'request_id':request_id,'status':'resolved'}
            if a['status'] != 'pending' or sid not in self.servers:
                raise Conflict('Approval is stale, already resolved or delivery is uncertain')
            self.store.execute("UPDATE approvals SET status='responding',decision=? WHERE id=?",(decision,request_id))
            try:
                await self.servers[sid].respond(json.loads(a['rpc_id']),{'decision':decision})
            except Exception:
                self.store.execute("UPDATE approvals SET status='uncertain' WHERE id=?",(request_id,))
                raise Conflict('Approval response delivery uncertain; do not retry automatically')
            self.store.execute("UPDATE approvals SET status='resolved' WHERE id=?",(request_id,))
            if self.store.session(sid)['status']=='approval' and not self.store.one("SELECT id FROM approvals WHERE session_id=? AND status='pending'",(sid,)):
                self.store.update(sid,status='running')
            self.event(sid,'approval/responded',{'request_id':request_id,'decision':decision},True)
            return {'request_id':request_id,'status':'resolved'}

    def status(self, sid: str) -> dict:
        s = self.store.session(sid)
        s['approvals'] = self.store.all('SELECT id,method,status,created FROM approvals WHERE session_id=? AND status IN (\'pending\',\'uncertain\')',(sid,))
        s['jobs'] = self.store.all('SELECT * FROM jobs WHERE session_id=? ORDER BY created DESC LIMIT 20',(sid,))
        return s

    async def notification(self, sid: str, msg: dict) -> None:
        method, p = msg['method'], msg.get('params',{})
        if method=='bridge/processStarted':
            self.store.update(sid,pid=p['pid'],process_started=time.time())
            return
        if 'id' in msg:
            if method in {'item/commandExecution/requestApproval','item/fileChange/requestApproval'}:
                aid = uid()
                self.store.execute('INSERT INTO approvals(id,session_id,rpc_id,method,status,created) VALUES(?,?,?,?,?,?)',
                                   (aid,sid,json.dumps(msg['id']),method,'pending',time.time()))
                self.store.update(sid,status='approval')
                self.event(sid,'approval/required',{'request_id':aid,'method':method,
                           'command':redact(str(p.get('command',''))),'reason':redact(str(p.get('reason','')))},True)
            else:
                self.event(sid,'request/unsupported',{'method':method},True)
                await self.servers[sid].write({'id':msg['id'],'error':{'code':-32601,'message':'Bridge does not support this request; denied'}})
            return
        if method=='bridge/processLost':
            self.disconnected.add(sid)
            self.store.update(sid,status='interrupted',turn_id=None)
            self.store.execute("UPDATE approvals SET status='stale' WHERE session_id=? AND status IN ('pending','responding')",(sid,))
            self.store.execute("UPDATE jobs SET status='uncertain' WHERE session_id=? AND status IN ('running','dispatching')",(sid,))
            self.event(sid,'process/lost',{},True)
            # Reconnect is explicit: do not race an old process or active task.
            return
        data: dict[str, Any] = {}
        turn = p.get('turn',{})
        if method=='turn/started':
            self.store.update(sid,status='running',turn_id=turn['id'])
            self.store.execute("UPDATE jobs SET turn_id=? WHERE session_id=? AND status='dispatching'",(turn['id'],sid))
            data = {'turn_id':turn['id']}
        elif method=='turn/completed':
            status = turn.get('status','completed')
            self.store.update(sid,status='idle' if status=='completed' else status,turn_id=None)
            self.store.execute("UPDATE jobs SET status=?,turn_id=? WHERE session_id=? AND status IN ('running','dispatching')",(status,turn['id'],sid))
            self.store.execute("UPDATE approvals SET status='stale' WHERE session_id=? AND status='pending'",(sid,))
            data = {'turn_id':turn['id'],'status':status}
            error = turn.get('error')
            if error and re.search(r'auth|401|403|login|token',json.dumps(error),re.I):
                self.event(sid,'account/authentication_required',{},True)
        elif method in {'item/started','item/completed'}:
            item = p.get('item',{})
            data = {'item_id':item.get('id'),'type':item.get('type'),'status':item.get('status')}
            if item.get('type')=='commandExecution':
                data.update(command=redact(str(item.get('command',''))),exit_code=item.get('exitCode'))
            if item.get('type')=='fileChange':
                data['files'] = [redact(str(c.get('path',''))) for c in item.get('changes',[])][:100]
            if item.get('type')=='agentMessage' and method=='item/completed':
                data['text'] = redact(item.get('text',''))
        elif method=='item/agentMessage/delta':
            data = {'text':redact(p.get('delta','')),'item_id':p.get('itemId')}
        elif method=='serverRequest/resolved':
            self.store.execute("UPDATE approvals SET status='resolved' WHERE session_id=? AND rpc_id=? AND status='pending'",(sid,json.dumps(p.get('requestId'))))
        elif method=='error':
            # Credential errors are recognized locally without exporting raw content.
            text = json.dumps(p)
            if re.search(r'auth|401|403|login|token',text,re.I):
                self.event(sid,'account/authentication_required',{},True)
            data = {'reason':'Codex reported an error','will_retry':p.get('willRetry',False)}
        # Persist structured metadata for all events; never raw command/source payloads.
        self.event(sid,method,data,method in {'turn/completed','error','serverRequest/resolved'} or 'authRecovery' in method)
