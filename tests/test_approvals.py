import pytest

from grok_codex.service import Conflict
from tests.test_sessions import start


async def test_approval_explicit_human_and_original_rpc_id(bridge,settings):
    sid = await start(bridge,settings)
    await bridge.notification(sid,{'id':123,'method':'item/commandExecution/requestApproval','params':{'command':'echo token=secret','reason':'test'}})
    pending = bridge.status(sid)['approvals']
    assert len(pending)==1 and bridge.status(sid)['status']=='approval'
    aid = pending[0]['id']
    with pytest.raises(PermissionError):
        await bridge.approve(sid,'grok',aid,'accept')
    r = await bridge.approve(sid,'human',aid,'accept')
    assert r['status']=='resolved'
    assert bridge.servers[sid].calls[-1]==('response',{'id':123,'decision':'accept'})
    before = len(bridge.servers[sid].calls)
    await bridge.approve(sid,'human',aid,'accept')
    assert len(bridge.servers[sid].calls)==before
    with pytest.raises(Conflict):
        await bridge.approve(sid,'human',aid,'decline')
    assert 'token=secret' not in str(bridge.store.logs(sid,0,100))
    await bridge.dispatcher.client.aclose()


async def test_unsupported_server_request_fails_closed(bridge,settings):
    sid = await start(bridge,settings)
    await bridge.notification(sid,{'id':'x','method':'item/tool/requestUserInput','params':{'secret':'xxx'}})
    assert bridge.servers[sid].calls[-1][1]['error']['code']==-32601
    assert any(x['kind']=='request/unsupported' for x in bridge.store.logs(sid,0,100)['events'])
    await bridge.dispatcher.client.aclose()


async def test_held_event_once_per_request_redacted_human_only(bridge, settings):
    import json
    from grok_codex.service import Conflict
    sid = await start(bridge, settings)
    await bridge.notification(sid, {'id':20,'method':'item/commandExecution/requestApproval',
        'params':{'command':'git push; echo token=private','cwd':bridge.status(sid)['repo']}})
    aid = bridge.status(sid)['approvals'][0]['id']
    with pytest.raises(PermissionError):
        await bridge.hold(sid,'grok',aid,'blocked')
    await bridge.hold(sid,'human',aid,'deny rule; secret=private')
    await bridge.hold(sid,'human',aid,'another reason')
    held = bridge.store.all("SELECT * FROM events WHERE kind='approval/held'")
    assert len(held) == 1 and held[0]['urgent'] == 1
    data = json.loads(held[0]['data'])
    assert data['request_id'] == aid and data['status'] == 'pending'
    assert 'git push' in data['command'] and 'private' not in str(data)
    assert bridge.status(sid)['approvals'][0]['status'] == 'pending'
    # No auto-accept RPC was issued.
    assert not any(call[0] == 'response' for call in bridge.servers[sid].calls)
    await bridge.approve(sid,'human',aid,'decline')
    with pytest.raises(Conflict):
        await bridge.hold(sid,'human',aid,'blocked')
    await bridge.dispatcher.client.aclose()
