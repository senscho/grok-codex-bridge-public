import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from grok_codex.api import create_app
from grok_codex.config import Settings
from tests.fakes import settle


@pytest.fixture
async def client(bridge,settings):
    app=create_app(settings,bridge); app.state.bridge=bridge
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://bridge') as c:
        yield c
    await bridge.dispatcher.client.aclose()


def headers(settings,who='grok',key='key'):
    return {'Authorization':'Bearer '+getattr(settings,who+'_token'),'Idempotency-Key':key}


async def test_auth_and_schema_validation(client,settings):
    assert (await client.post('/tools/codex_list')).status_code==401
    assert (await client.post('/tools/codex_list',headers={'Authorization':'Bearer invalid'})).status_code==401
    assert (await client.post('/tools/codex_status',json={'session_id':'../../foo'},headers=headers(settings))).status_code==422
    repo=settings.repo_roots[0]/'repo'; repo.mkdir()
    body={'repo':str(repo),'account':'one','prompt':'test'}
    assert (await client.post('/tools/codex_start',json=body,headers={'Authorization':'Bearer '+settings.grok_token})).status_code==400
    body['options']={'sandbox':'danger-full-access'}
    assert (await client.post('/tools/codex_start',json=body,headers=headers(settings))).status_code==422
    body['options']={'approval_policy':'never'}
    assert (await client.post('/tools/codex_start',json=body,headers=headers(settings))).status_code==422
    r=await client.post('/accounts/register',json={'account_id':'bad'},headers=headers(settings))
    assert r.status_code==403
    assert (await client.post('/accounts/register',json={'account_id':'../bad'},headers=headers(settings,'human'))).status_code==422
    assert (await client.post('/tools/codex_status',json={'session_id':'unknown'},headers=headers(settings))).status_code==404


async def test_request_deduplication(client,settings,bridge):
    repo=settings.repo_roots[0]/'repo'; repo.mkdir()
    body={'repo':str(repo),'account':'one','prompt':'test'}
    results=await asyncio.gather(*(client.post('/tools/codex_start',json=body,headers=headers(settings)) for _ in range(8)))
    assert all(r.status_code==202 for r in results)
    assert len({r.json()['session_id'] for r in results})==1
    await settle(bridge)
    assert len(bridge.store.all('SELECT id FROM sessions'))==1
    assert len([c for c in bridge.servers[results[0].json()['session_id']].calls if c[0]=='turn/start'])==1
    body['prompt']='different'
    assert (await client.post('/tools/codex_start',json=body,headers=headers(settings))).status_code==409


async def test_path_traversal_symlink_and_state_overlap(client,settings):
    outside=settings.state_dir.parent/'outside'; outside.mkdir()
    link=settings.repo_roots[0]/'link'; link.symlink_to(outside,target_is_directory=True)
    for repo in [outside,link,settings.repo_roots[0]/'..']:
        r=await client.post('/tools/codex_start',json={'repo':str(repo),'account':'one','prompt':'x'},headers=headers(settings,key=hashlib.sha256(str(repo).encode()).hexdigest()))
        assert r.status_code==400
    settings.repo_roots=(settings.state_dir.parent,)
    with pytest.raises(ValueError,match='overlap'):
        settings.repository(str(settings.state_dir.parent))


async def test_openapi_all_tools_and_dashboard(client,settings):
    schema=(await client.get('/openapi.json')).json()
    for tool in ['start','send','status','logs','list','approve','interrupt','resume','stop','accounts','switch_account','attach_info']:
        assert '/tools/codex_'+tool in schema['paths']
    assert 'HTTPBearer' in schema['components']['securitySchemes']
    assert (await client.get('/dashboard')).status_code==200
    assert (await client.get('/healthz')).json()=={'status':'ok'}


def test_settings_require_secure_configuration(tmp_path):
    with pytest.raises(ValueError,match='distinct'):
        Settings(state_dir=tmp_path,grok_token='x',human_token='x').validate()
    for url in ['http://example.com','https://user:pass@example.com','https://example.com?token=bad']:
        with pytest.raises(ValueError):
            Settings(state_dir=tmp_path,grok_token='g'*40,human_token='h'*40,webhook_url=url,webhook_secret='s'*40).validate()


async def test_human_held_and_detach_endpoints_are_idempotent(client,bridge,settings):
    from tests.test_sessions import start
    sid = await start(bridge,settings)
    await bridge.notification(sid,{'id':21,'method':'item/commandExecution/requestApproval',
                                  'params':{'command':'git push'}})
    aid = bridge.status(sid)['approvals'][0]['id']
    body = {'session_id':sid,'request_id':aid,'reason':'push rule'}
    assert (await client.post('/sessions/hold',json=body,headers=headers(settings,key='grok-hold'))).status_code == 403
    for _ in range(2):
        assert (await client.post('/sessions/hold',json=body,headers=headers(settings,'human','held-key'))).status_code == 200
    assert len(bridge.store.all("SELECT * FROM events WHERE kind='approval/held'")) == 1
    assert (await client.post('/sessions/detach',json={'session_id':sid},headers=headers(settings,key='grok-detach'))).status_code == 403
    for _ in range(2):
        assert (await client.post('/sessions/detach',json={'session_id':sid},headers=headers(settings,'human','detach-key'))).status_code == 200
    assert len(bridge.store.all("SELECT * FROM events WHERE kind='session/detached'")) == 1
    assert bridge.status(sid)['owner'] == 'grok'  # Detach does not change ownership.
