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
