import asyncio
import json

import pytest

from grok_codex.service import Bridge, Conflict
from tests.fakes import FakeServer, settle


async def start(bridge, settings, name='repo', actor='grok'):
    repo = settings.repo_roots[0]/name
    repo.mkdir(exist_ok=True)
    r = await bridge.start(actor,str(repo),'one','first prompt',{})
    await settle(bridge)
    return r['session_id']


async def complete(bridge,sid):
    await bridge.servers[sid].complete(turn_id=bridge.status(sid)['turn_id'])


async def test_persistent_send_resume_and_multiple_sessions(bridge,settings):
    a = await start(bridge,settings,'a')
    b = await start(bridge,settings,'b')
    thread = bridge.status(a)['thread_id']
    assert thread != bridge.status(b)['thread_id']
    await complete(bridge,a)
    await bridge.send(a,'grok','second prompt')
    await settle(bridge)
    await complete(bridge,a)
    await bridge.stop(a,'grok')
    await bridge.resume(a,'grok')
    assert bridge.status(a)['thread_id']==thread
    assert bridge.status(b)['status']=='running'
    assert len(FakeServer.histories[(str(bridge.servers[a].home),thread)])==2
    assert bridge.status(a)['status']=='idle'
    assert all(m!='turn/start' for m,p in bridge.servers[a].calls)
    await bridge.dispatcher.client.aclose()


async def test_concurrent_human_grok_and_turn_coordination(bridge,settings):
    sid = await start(bridge,settings)
    await complete(bridge,sid)
    results = await asyncio.gather(bridge.send(sid,'grok','grok'),bridge.send(sid,'human','human'),return_exceptions=True)
    assert sum(isinstance(r,Conflict) for r in results)==1
    await settle(bridge)
    with pytest.raises(Conflict):
        await bridge.send(sid,'grok','busy')
    await bridge.control(sid,'human','human')
    with pytest.raises(Conflict):
        await bridge.send(sid,'grok','blocked')
    with pytest.raises(PermissionError):
        await bridge.control(sid,'grok','grok')
    await bridge.send(sid,'human','steer message',steer=True)
    await settle(bridge)
    method, params = bridge.servers[sid].calls[-1]
    assert method=='turn/steer' and params['expectedTurnId']
    await bridge.interrupt(sid,'human')
    assert bridge.status(sid)['status']=='interrupted'
    await bridge.resume(sid,'human')
    await bridge.control(sid,'human','grok')
    await bridge.dispatcher.client.aclose()


async def test_two_simultaneous_grok_messages_only_one_accepted(bridge,settings):
    sid = await start(bridge,settings)
    await complete(bridge,sid)
    results = await asyncio.gather(*(bridge.send(sid,'grok',str(i)) for i in range(10)),return_exceptions=True)
    await settle(bridge)
    assert sum(isinstance(r,dict) for r in results)==1
    await bridge.dispatcher.client.aclose()


async def test_accounts_isolate_process_homes_and_safe_switch(bridge,settings):
    sid = await start(bridge,settings,actor='human')
    with pytest.raises(Conflict):
        await bridge.switch(sid,'human','two')
    await complete(bridge,sid)
    old_thread = bridge.status(sid)['thread_id']
    with pytest.raises(Conflict,match='unavailable'):
        await bridge.switch(sid,'human','two')
    assert bridge.status(sid)['account']=='one'
    assert bridge.status(sid)['thread_id']==old_thread
    assert bridge.status(sid)['status']=='idle'
    homes = [str(x.home) for x in FakeServer.instances[-3:]]
    assert homes[-2].endswith('/two') and homes[-1].endswith('/one')
    # Simulate explicitly available thread in target identity, without copying auth.
    FakeServer.histories[(str(settings.state_dir/'accounts/two'),old_thread)] = []
    await bridge.switch(sid,'human','two')
    assert bridge.status(sid)['account']=='two'
    assert bridge.servers[sid].home==settings.state_dir/'accounts/two'
    await bridge.dispatcher.client.aclose()


async def test_restart_recovery_never_replays_task(bridge,settings):
    sid = await start(bridge,settings)
    tid = bridge.status(sid)['thread_id']
    bridge.store.update(sid,pid=None)
    bridge.store.execute("INSERT INTO approvals VALUES(?,?,?,?,?,?,?)",('approval',sid,'1','item/fileChange/requestApproval','pending',None,1))
    # Reopen the same SQLite registry as a new daemon after a simulated crash.
    recovered = Bridge(settings,FakeServer)
    await recovered.startup()
    await recovered.recovery_task
    assert recovered.status(sid)['thread_id']==tid
    assert recovered.status(sid)['status']=='idle'
    assert all(m!='turn/start' for m,p in recovered.servers[sid].calls)
    assert recovered.store.one('SELECT status FROM approvals WHERE id=?',('approval',))['status']=='stale'
    assert recovered.status(sid)['jobs'][0]['status']=='uncertain'
    await recovered.shutdown()
    await bridge.dispatcher.client.aclose()


async def test_process_loss_and_stale_approval(bridge,settings):
    sid = await start(bridge,settings)
    await bridge.notification(sid,{'id':'a','method':'item/fileChange/requestApproval','params':{}})
    aid = bridge.status(sid)['approvals'][0]['id']
    await bridge.notification(sid,{'method':'bridge/processLost'})
    assert bridge.status(sid)['status']=='interrupted'
    with pytest.raises(Conflict):
        await bridge.approve(sid,'human',aid,'accept')
    await bridge.resume(sid,'grok')
    assert bridge.status(sid)['status']=='idle'
    await bridge.dispatcher.client.aclose()


async def test_overlapping_repositories_rejected(bridge,settings):
    sid = await start(bridge,settings)
    repo = settings.repo_roots[0]/'repo'
    child = repo/'child'; child.mkdir()
    with pytest.raises(Conflict):
        await bridge.start('grok',str(child),'one','x',{})
    await bridge.stop(sid,'grok')
    await bridge.start('grok',str(child),'one','x',{})
    await settle(bridge)
    await bridge.dispatcher.client.aclose()


async def test_missing_codex_is_failed_before_thread_dispatch(bridge,settings):
    from grok_codex.protocol import AppServer
    bridge.factory=AppServer
    settings.codex_binary=str(settings.state_dir/'missing-codex')
    sid=await start(bridge,settings)
    status=bridge.status(sid)
    assert status['thread_id'] is None and status['pid'] is None
    assert status['status']=='error'
    assert status['jobs'][0]['status']=='failed'
    assert 'BRIDGE_CODEX_BINARY' in status['jobs'][0]['detail']
    assert not bridge.servers
    await bridge.dispatcher.client.aclose()
