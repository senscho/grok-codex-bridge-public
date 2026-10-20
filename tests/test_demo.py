import json
from pathlib import Path

import httpx
import pytest

from grok_codex.demo import DemoRunner, ensure_daemon, update_config


def response(data, code=200):
    return httpx.Response(code, json=data)


def session_status(status='idle', job='completed', **kwargs):
    return {'id':'session', 'thread_id':'thread', 'pid':12, 'status':status, 'owner':'grok',
            'approvals':[], 'jobs':[{'id':'job','status':job}], **kwargs}


def test_demo_saves_ids_and_reuses_completed_session(tmp_path):
    calls=[]
    def transport(request):
        calls.append(request)
        if request.url.path.endswith('codex_list'):
            return response({'sessions':[]})
        if request.url.path.endswith('codex_start'):
            assert (tmp_path/'state.json').exists()  # key saved BEFORE network write
            return response({'session_id':'session','job_id':'job','status':'queued'})
        if request.url.path.endswith('codex_status'):
            return response(session_status())
        return response({'events':[],'cursor':0})
    with httpx.Client(base_url='http://localhost',transport=httpx.MockTransport(transport)) as client:
        runner=DemoRunner(client,'grok-token',tmp_path/'state.json')
        assert runner.session(tmp_path,'personal')=='session'
        assert runner.wait('session',1)['thread_id']=='thread'
        restored=DemoRunner(client,'grok-token',tmp_path/'state.json')
        assert restored.session(tmp_path,'personal')=='session'
        restored.wait('session',1)
        assert sum(r.url.path.endswith('codex_start') for r in calls)==1
        assert restored.state['completed']
        assert (tmp_path/'state.json').stat().st_mode & 0o777 == 0o600


def test_network_retry_reuses_same_key_and_body(tmp_path):
    starts=[]
    def transport(request):
        if request.url.path.endswith('codex_list'):
            return response({'sessions':[]})
        starts.append(request)
        if len(starts)==1:
            raise httpx.ReadTimeout('lost response',request=request)
        return response({'session_id':'session','job_id':'job','status':'queued'})
    with httpx.Client(base_url='http://localhost',transport=httpx.MockTransport(transport)) as client:
        runner=DemoRunner(client,'token',tmp_path/'state.json')
        with pytest.raises(httpx.ReadTimeout):
            runner.session(tmp_path,'personal')
        restored=DemoRunner(client,'token',tmp_path/'state.json')
        restored.session(tmp_path,'personal')
        assert starts[0].headers['Idempotency-Key']==starts[1].headers['Idempotency-Key']
        assert starts[0].content==starts[1].content


def test_existing_manual_session_is_adopted_without_new_turn(tmp_path):
    calls=[]
    def transport(request):
        calls.append(request.url.path)
        if request.url.path.endswith('codex_list'):
            return response({'sessions':[{'id':'session','account':'personal','repo':str(tmp_path),'status':'running'}]})
        if request.url.path.endswith('codex_status'):
            return response(session_status('running','running'))
        if request.url.path.endswith('codex_logs'):
            return response({'events':[],'cursor':0})
        raise AssertionError('Unexpected mutation '+request.url.path)
    with httpx.Client(base_url='http://localhost',transport=httpx.MockTransport(transport)) as client:
        runner=DemoRunner(client,'token',tmp_path/'state.json')
        assert runner.session(tmp_path,'personal')=='session'
        assert runner.state['adopted']
        assert not any('codex_start' in p or 'codex_stop' in p for p in calls)


def test_proven_missing_binary_attempt_can_be_cleaned_up(tmp_path):
    calls=[]
    def transport(request):
        calls.append(request.url.path)
        if request.url.path.endswith('codex_list'):
            return response({'sessions':[{'id':'old','account':'personal','repo':str(tmp_path),'status':'interrupted'}]})
        if request.url.path.endswith('codex_status'):
            return response(session_status('interrupted','uncertain',id='old',thread_id=None,pid=None))
        if request.url.path.endswith('codex_logs'):
            return response({'events':[{'kind':'bridge/error','data':{'error_type':'FileNotFoundError'}}]})
        if request.url.path.endswith('codex_stop'):
            return response({'status':'stopped'})
        if request.url.path.endswith('codex_start'):
            return response({'session_id':'session','job_id':'job','status':'queued'})
        raise AssertionError(request.url.path)
    with httpx.Client(base_url='http://localhost',transport=httpx.MockTransport(transport)) as client:
        runner=DemoRunner(client,'token',tmp_path/'state.json')
        assert runner.session(tmp_path,'personal')=='session'
        assert calls.index('/tools/codex_stop')<calls.index('/tools/codex_start')


def test_uncertain_execution_is_not_treated_as_demo_success(tmp_path):
    def transport(request):
        if request.url.path.endswith('codex_status'):
            return response(session_status('idle','uncertain'))
        return response({'events':[],'cursor':0})
    with httpx.Client(base_url='http://localhost',transport=httpx.MockTransport(transport)) as client:
        runner=DemoRunner(client,'token',tmp_path/'state.json')
        with pytest.raises(RuntimeError,match='failed/interrupted'):
            runner.wait('session',1)
        assert not runner.state.get('completed')


def test_approval_requires_explicit_decision_and_injects_request_id(tmp_path,monkeypatch):
    monkeypatch.setattr('grok_codex.demo.time.sleep',lambda _:None)
    decisions=[]
    def transport(request):
        if request.url.path.endswith('codex_status'):
            pending=[] if decisions else [{'id':'approval-123','status':'pending','method':'item/fileChange/requestApproval'}]
            return response(session_status('idle' if decisions else 'approval', 'completed' if decisions else 'running',approvals=pending))
        if request.url.path.endswith('codex_approve'):
            decisions.append(json.loads(request.content))
            assert request.headers['Authorization']=='Bearer human-token'
            return response({'status':'resolved'})
        return response({'events':[],'cursor':0})
    with httpx.Client(base_url='http://localhost',transport=httpx.MockTransport(transport)) as client:
        runner=DemoRunner(client,'human-token',tmp_path/'state.json')
        with pytest.raises(RuntimeError,match='Explicit approval'):
            runner.wait('session',1)
        assert not decisions
        runner.wait('session',1,approve=lambda request:'decline')
        assert decisions==[{'session_id':'session','request_id':'approval-123','decision':'decline'}]


def test_configuration_update_preserves_tokens_and_unrelated_options(tmp_path,monkeypatch):
    monkeypatch.setenv('BRIDGE_CODEX_BINARY','old')
    p=tmp_path/'.env';p.write_text("# private\nBRIDGE_GROK_TOKEN=private-value\nBRIDGE_CODEX_BINARY=old\nBRIDGE_PROGRESS_INTERVAL=10\n")
    update_config(p,{'BRIDGE_CODEX_BINARY':'/path with spaces/codex'})
    assert 'BRIDGE_GROK_TOKEN=private-value' in p.read_text()
    assert p.read_text().count('BRIDGE_CODEX_BINARY=')==1
    assert "'/path with spaces/codex'" in p.read_text()
    assert p.stat().st_mode & 0o777 == 0o600


def test_live_daemon_reused_without_spawning_or_killing(tmp_path,monkeypatch):
    monkeypatch.setattr('grok_codex.demo.subprocess.Popen',lambda *a,**k:pytest.fail('Existing daemon must be reused'))
    with httpx.Client(base_url='http://localhost',transport=httpx.MockTransport(lambda r:response({'accounts':[]}))) as client:
        ensure_daemon(tmp_path,tmp_path/'.env',client,'token',48765,tmp_path/'daemon.log')


def test_complete_one_command_flow_without_credentials_or_manual_ids(tmp_path,monkeypatch):
    import subprocess
    import sys
    from grok_codex import demo
    config=tmp_path/'.env'
    config.write_text('BRIDGE_HUMAN_TOKEN='+('h'*40)+'\nBRIDGE_GROK_TOKEN='+('g'*40)+'\nBRIDGE_URL=http://127.0.0.1:48765\nBRIDGE_REPO_ROOTS='+str(tmp_path)+'\n')
    repo=tmp_path/'demo-repo';state_file=tmp_path/'state/demo.json'
    calls=[]
    def transport(request):
        calls.append(request.url.path)
        if request.url.path.endswith('codex_accounts'):
            return response({'accounts':[]})
        if request.url.path=='/accounts/register':
            return response({'home':str(tmp_path/'codex-home')})
        if request.url.path.endswith('codex_list'):
            return response({'sessions':[]})
        if request.url.path.endswith('codex_start'):
            assert request.headers['Authorization']=='Bearer '+('g'*40)
            (repo/'calculator.py').write_text('def add(a,b): return a+b\n')
            (repo/'test_calculator.py').write_text('import unittest\nfrom calculator import add\nclass TestAdd(unittest.TestCase):\n def test_add(self): self.assertEqual(add(1,2),3)\n')
            return response({'session_id':'session','job_id':'job','status':'queued'})
        if request.url.path.endswith('codex_status'):
            return response(session_status())
        return response({'events':[],'cursor':0})
    original_client=httpx.Client
    monkeypatch.setattr(demo.httpx,'Client',lambda *a,**k:original_client(*a,**k,transport=httpx.MockTransport(transport)))
    monkeypatch.setattr(demo.shutil,'which',lambda binary:'/fixture/codex')
    original_run=subprocess.run
    def run(args,**kwargs):
        if args[0]=='/fixture/codex':
            assert args[1:]==['login','status']
            assert kwargs['env']['CODEX_HOME']==str(tmp_path/'codex-home')
            assert 'BRIDGE_HUMAN_TOKEN' not in kwargs['env']
            return subprocess.CompletedProcess(args,0)
        return original_run(args,**kwargs)
    monkeypatch.setattr(demo.subprocess,'run',run)
    monkeypatch.chdir(tmp_path)
    for key,value in {'BRIDGE_HUMAN_TOKEN':'h'*40,'BRIDGE_GROK_TOKEN':'g'*40,'BRIDGE_URL':'http://127.0.0.1:48765','BRIDGE_REPO_ROOTS':str(tmp_path),'BRIDGE_CODEX_BINARY':'codex'}.items():
        monkeypatch.setenv(key,value)
    monkeypatch.setattr(sys,'argv',['demo','--repo',str(repo),'--config',str(config),'--state-file',str(state_file),'--timeout','2'])
    demo.main()
    assert json.loads(state_file.read_text())['completed']
    assert (repo/'.git').exists()
    demo.main()
    assert sum(p.endswith('codex_start') for p in calls)==1


def auth_history(*, tool_type=None, auth=True, complete=True):
    events=[{'seq':1,'kind':'turn/started','data':{'turn_id':'failed-turn'}},
            {'seq':2,'kind':'item/completed','data':{'type':'userMessage'}}]
    if tool_type:
        events.append({'seq':3,'kind':'item/started','data':{'type':tool_type}})
    if auth:
        events.append({'seq':4,'kind':'account/authentication_required','data':{}})
    if complete:
        events.append({'seq':5,'kind':'turn/completed','data':{'turn_id':'failed-turn','status':'failed'}})
    return events


def test_verified_login_retries_tool_free_failed_auth_in_same_thread_once(tmp_path):
    calls=[]
    def transport(request):
        name=request.url.path.rsplit('/',1)[-1]
        calls.append((name,request.headers.get('Idempotency-Key'),json.loads(request.content)))
        if name=='codex_status':
            return response(session_status('idle','failed',jobs=[{'id':'old-job','status':'failed','turn_id':'failed-turn'}]))
        if name=='codex_logs':
            return response({'events':auth_history()+[{'seq':6,'kind':'thread/status/changed','data':{}}],'cursor':6})
        if name=='codex_stop':
            return response({'status':'stopped'})
        if name=='codex_resume':
            return response({'thread_id':'thread','status':'idle'})
        if name=='codex_send':
            return response({'session_id':'session','job_id':'retry-job','status':'queued'})
        raise AssertionError(name)
    with httpx.Client(base_url='http://localhost',transport=httpx.MockTransport(transport)) as client:
        runner=DemoRunner(client,'grok-token',tmp_path/'state.json')
        runner.state={'session_id':'session','request':{'prompt':'demo'}}
        assert runner.recover_auth_failure('session',login_verified=True)
        assert [c[0] for c in calls][-3:]==['codex_stop','codex_resume','codex_send']
        assert all(c[2]['session_id']=='session' for c in calls[-3:])
        assert runner.state['job_id']=='retry-job' and runner.state['cursor']==5
        restored=DemoRunner(client,'grok-token',tmp_path/'state.json')
        assert not restored.recover_auth_failure('session',login_verified=True)
        assert sum(c[0]=='codex_send' for c in calls)==1


@pytest.mark.parametrize('job_state,tool_type,auth,complete,verified',[
    ('uncertain',None,True,True,True),
    ('interrupted',None,True,True,True),
    ('failed','commandExecution',True,True,True),
    ('failed','fileChange',True,True,True),
    ('failed','mcpToolCall',True,True,True),
    ('failed',None,False,True,True),
    ('failed',None,True,False,True),
    ('failed',None,True,True,False),
])
def test_auth_retry_rejects_uncertainty_tools_and_unverified_login(tmp_path,job_state,tool_type,auth,complete,verified):
    def transport(request):
        name=request.url.path.rsplit('/',1)[-1]
        if name=='codex_status':
            return response(session_status('idle',job_state,jobs=[{'id':'old-job','status':job_state,'turn_id':'failed-turn'}]))
        if name=='codex_logs':
            return response({'events':auth_history(tool_type=tool_type,auth=auth,complete=complete),'cursor':5})
        pytest.fail('No mutation is allowed for unsafe or unproven retry')
    with httpx.Client(base_url='http://localhost',transport=httpx.MockTransport(transport)) as client:
        runner=DemoRunner(client,'token',tmp_path/'state.json')
        runner.state={'request':{'prompt':'demo'}}
        assert not runner.recover_auth_failure('session',login_verified=verified)


def test_lost_auth_retry_response_reuses_journaled_send_key(tmp_path):
    send_keys=[]
    def transport(request):
        name=request.url.path.rsplit('/',1)[-1]
        if name=='codex_status':
            return response(session_status('idle','failed',jobs=[{'id':'old-job','status':'failed','turn_id':'failed-turn'}]))
        if name=='codex_logs':
            return response({'events':auth_history(),'cursor':5})
        if name=='codex_stop':return response({'status':'stopped'})
        if name=='codex_resume':return response({'thread_id':'thread','status':'idle'})
        if name=='codex_send':
            send_keys.append(request.headers['Idempotency-Key'])
            if len(send_keys)==1:raise httpx.ReadTimeout('lost response',request=request)
            return response({'job_id':'retry-job','status':'queued'})
        raise AssertionError(name)
    with httpx.Client(base_url='http://localhost',transport=httpx.MockTransport(transport)) as client:
        runner=DemoRunner(client,'token',tmp_path/'state.json')
        runner.state={'request':{'prompt':'demo'}}
        with pytest.raises(httpx.ReadTimeout):runner.recover_auth_failure('session',login_verified=True)
        restored=DemoRunner(client,'token',tmp_path/'state.json')
        assert restored.recover_auth_failure('session',login_verified=True)
        assert len(send_keys)==2 and send_keys[0]==send_keys[1]
