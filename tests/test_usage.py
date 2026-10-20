import json
import time

import pytest

from grok_codex.usage import UsageAlerts
from tests.test_sessions import start


def usage_events(bridge, kind):
    return [json.loads(row['data']) for row in bridge.store.all('SELECT data FROM events WHERE kind=?',(kind,))]


async def test_limit_near_once_per_account_window_sparse_and_restart(bridge, settings):
    sid = await start(bridge,settings)
    other = await start(bridge,settings,'other')
    reset = int(time.time()) + 3600
    async def update(session, snapshot):
        await bridge.notification(session, {'method':'account/rateLimits/updated','params':{'rateLimits':snapshot}})
    await update(sid, {'limitId':'codex','primary':{'usedPercent':89,'resetsAt':reset,'windowDurationMins':300}})
    assert not usage_events(bridge,'usage/limitNear')
    await update(sid, {'limitId':'codex','primary':{'usedPercent':90}})
    await update(other, {'limitId':'codex','primary':{'usedPercent':95,'resetsAt':None}})
    bridge.usage = UsageAlerts(bridge.store,settings,bridge.event)
    await update(other, {'primary':{'usedPercent':89}})
    await update(sid, {'primary':{'usedPercent':91}})
    alerts = usage_events(bridge,'usage/limitNear')
    assert len(alerts) == 1 and alerts[0]['resets_at'] == reset
    await update(sid, {'primary':{'usedPercent':92,'resetsAt':reset+3600}})
    assert len(usage_events(bridge,'usage/limitNear')) == 2
    await bridge.dispatcher.client.aclose()


@pytest.mark.parametrize('code', ['usageLimitExceeded','rateLimitExceeded',{'httpConnectionFailed':{'httpStatusCode':429}}])
async def test_exhaustion_error_then_failed_turn_is_one_alert(bridge, settings, code):
    sid = await start(bridge,settings)
    tid = bridge.status(sid)['turn_id']
    error = {'codexErrorInfo':code,'message':'PRIVATE provider detail'}
    await bridge.notification(sid, {'method':'error','params':{'threadId':'thread','turnId':tid,'willRetry':False,'error':error}})
    await bridge.notification(sid, {'method':'turn/completed','params':{'turn':{'id':tid,'status':'failed','error':error}}})
    alerts = usage_events(bridge,'usage/limitReached')
    assert len(alerts) == 1 and 'PRIVATE' not in str(alerts)
    finished = bridge.store.one("SELECT * FROM events WHERE kind='turn/completed'")
    assert finished['session_id'] == sid and finished['urgent'] == 1
    assert json.loads(finished['data'])['status'] == 'failed'
    await bridge.dispatcher.client.aclose()


async def test_exhausted_snapshot_known_reset_and_credits_flags(bridge, settings):
    sid = await start(bridge,settings)
    reset = int(time.time())+3600
    await bridge.notification(sid, {'method':'account/rateLimits/updated','params':{'rateLimits':{
        'credits':{'hasCredits':False,'unlimited':False,'balance':'0'}}}})
    assert not usage_events(bridge,'usage/limitReached')
    snapshot = {'limitId':'codex','primary':{'usedPercent':100,'resetsAt':reset},
                'rateLimitReachedType':'workspace_owner_credits_depleted'}
    for _ in range(2):
        await bridge.notification(sid, {'method':'account/rateLimits/updated','params':{'rateLimits':snapshot}})
    assert len(usage_events(bridge,'usage/limitReached')) == 1
    assert usage_events(bridge,'usage/limitReached')[0]['resets_at'] == reset
    tid = bridge.status(sid)['turn_id']
    await bridge.notification(sid, {'method':'error','params':{'turnId':tid,'willRetry':False,
        'error':{'codexErrorInfo':'usageLimitExceeded','message':'Usage limit exceeded'}}})
    assert len(usage_events(bridge,'usage/limitReached')) == 1
    await bridge.dispatcher.client.aclose()


@pytest.mark.parametrize('snapshot', [
    {'rateLimitReachedType':'workspace_member_usage_limit_reached'},
    {'spendControlReached':True,'individualLimit':{'limit':'100','used':'100','remainingPercent':0,'resetsAt':int(time.time())+3600}},
])
async def test_explicit_limit_exhaustion_without_window(bridge, settings, snapshot):
    sid = await start(bridge,settings)
    for _ in range(2):
        await bridge.notification(sid, {'method':'account/rateLimits/updated','params':{'rateLimits':snapshot}})
    assert len(usage_events(bridge,'usage/limitReached')) == 1
    await bridge.dispatcher.client.aclose()


async def test_limit_error_message_fallback_no_false_context_window_alert(bridge,settings):
    sid = await start(bridge,settings)
    for message in ['context window exceeded','internal failure']:
        assert not bridge.usage.reached(sid,'one',message,{'message':message})
    assert bridge.usage.reached(sid,'one','usage-failure',{'message':'Your usage limit has been exceeded'})
    assert len(usage_events(bridge,'usage/limitReached')) == 1
    await bridge.dispatcher.client.aclose()


async def test_unknown_window_near_does_not_repeat_on_jitter(bridge,settings):
    sid = await start(bridge,settings)
    settings.usage_alert_percent = 80
    for used in [79,80,81,70,85]:
        await bridge.notification(sid,{'method':'account/rateLimits/updated','params':{
            'rateLimits':{'primary':{'usedPercent':used,'windowDurationMins':300}}}})
    assert len(usage_events(bridge,'usage/limitNear')) == 1
    await bridge.dispatcher.client.aclose()


@pytest.mark.parametrize('status',['completed','failed','interrupted'])
async def test_finished_turn_always_notable_with_status(bridge,settings,status):
    sid = await start(bridge,settings)
    tid = bridge.status(sid)['turn_id']
    await bridge.notification(sid,{'method':'turn/completed','params':{'turn':{'id':tid,'status':status}}})
    row = bridge.store.one("SELECT * FROM events WHERE kind='turn/completed'")
    assert row['session_id'] == sid and row['urgent']
    assert json.loads(row['data']) == {'turn_id':tid,'status':status}
    await bridge.dispatcher.client.aclose()


async def test_auth_expiry_notable(bridge,settings):
    sid = await start(bridge,settings)
    await bridge.notification(sid,{'method':'error','params':{'willRetry':False,'error':{
        'codexErrorInfo':'unauthorized','message':'Login expired'}}})
    assert bridge.store.one("SELECT urgent FROM events WHERE kind='account/authentication_expired'")['urgent'] == 1
    await bridge.dispatcher.client.aclose()


async def test_error_reports_known_resets_without_guessing_exhausted_window(bridge,settings):
    sid = await start(bridge,settings)
    reset = int(time.time())+3600
    await bridge.notification(sid,{'method':'account/rateLimits/updated','params':{
        'rateLimits':{'primary':{'usedPercent':95,'resetsAt':reset}}}})
    bridge.usage.reached(sid,'one','failed-turn',{'codexErrorInfo':'usageLimitExceeded'})
    data = usage_events(bridge,'usage/limitReached')[0]
    assert data['known_window_resets'] == [{'window':'primary','resets_at':reset}]
    assert 'resets_at' not in data
    await bridge.dispatcher.client.aclose()
