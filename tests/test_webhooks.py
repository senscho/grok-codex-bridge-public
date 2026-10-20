import hashlib
import hmac
import json
import logging
import time

import httpx
import pytest

from grok_codex.webhooks import Dispatcher


@pytest.mark.parametrize('auth', ['', 'Authorization: per-routine-private-key', 'X-Receiver-Key: private:key'])
async def test_retries_durable_ids_replay_and_coalescing(bridge,settings,auth):
    settings.webhook_auth_header = auth
    settings.webhook_url = 'https://callback.example/events'
    settings.webhook_secret = 's'*40
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(500 if len(calls)==1 else 204)
    await bridge.dispatcher.client.aclose()
    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    d = Dispatcher(bridge.store,settings,client)
    for _ in range(20):
        bridge.store.event('s','item/agentMessage/delta',{'text':'PRIVATE CODE'})
    bridge.store.event('s','approval/required',{'command':'SECRET'},True)
    await d.tick()
    assert len(calls)==2 # one progress batch and one urgent event
    first = bridge.store.one("SELECT * FROM deliveries WHERE status='pending'")
    assert first['attempts']==1 and first['due']>time.time()
    assert b'PRIVATE' not in calls[0].content and b'SECRET' not in calls[1].content
    bridge.store.execute('UPDATE deliveries SET due=0 WHERE id=?',(first['id'],))
    # A new dispatcher after restart picks up the durable delivery.
    restored = Dispatcher(bridge.store,settings,client)
    await restored.tick()
    assert calls[-1].headers['x-bridge-event-id']==first['id']
    assert bridge.store.one('SELECT status FROM deliveries WHERE id=?',(first['id'],))['status']=='delivered'
    assert d.replay('s',0)==21
    d.batch(force=True)
    assert len(bridge.store.all('SELECT * FROM deliveries'))==4
    await restored.tick()
    for request in calls:
        if auth:
            name, value = auth.split(': ', 1)
            assert request.headers[name] == value
        else:
            assert 'Authorization' not in request.headers
            assert 'X-Receiver-Key' not in request.headers
        assert request.headers['Content-Type'] == 'application/json'
        expected = hmac.new(settings.webhook_secret.encode(),
                            request.headers['X-Bridge-Timestamp'].encode()+b'.'+request.content,
                            hashlib.sha256).hexdigest()
        assert request.headers['X-Bridge-Signature'] == 'sha256='+expected
    await client.aclose()


async def test_urgent_bypasses_progress_interval_and_dead_letter(bridge,settings):
    settings.webhook_url='https://callback.example'; settings.webhook_secret='s'*40
    settings.webhook_attempts=1
    await bridge.dispatcher.client.aclose()
    client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(403)))
    d=Dispatcher(bridge.store,settings,client); d.last_progress=time.time()
    bridge.store.event('s','progress')
    bridge.store.event('s','error',urgent=True)
    await d.tick()
    assert bridge.store.one('SELECT status FROM deliveries')['status']=='dead'
    assert bridge.store.one("SELECT batched FROM events WHERE kind='progress'")['batched']==0
    await client.aclose()


@pytest.mark.parametrize('header', [
    'private-key-without-colon', 'Authorization:', 'Authorization:   ',
    ': private-key', 'Bad Name: private-key', 'Bad(Name): private-key',
    'Authorization : private-key', 'Auth\tName: private-key',
    'Authorization: private-key\rX-Injected: yes',
    'Authorization: private-key\nX-Injected: yes',
    'Authorization: private-key\r\nX-Injected: yes',
    'Authorization: private-key\x00', 'Authorization: private-key\x7f',
    'Authorization: private-key\x1f', 'Authorization: private-keyé',
    'cOnTeNt-TyPe: private-key', 'X-bRiDgE-EvEnT-Id: private-key',
    'x-bridge-timestamp: private-key', 'X-BRIDGE-SIGNATURE: private-key',
])
def test_invalid_receiver_auth_rejected_at_startup(settings, header):
    from grok_codex.api import create_app

    settings.webhook_auth_header = header
    # Also validates while webhook delivery is disabled.
    with pytest.raises(ValueError, match='BRIDGE_WEBHOOK_AUTH_HEADER') as exc:
        create_app(settings)
    assert 'private-key' not in str(exc.value)
    assert 'private-key' not in repr(settings)


@pytest.mark.parametrize('header, expected', [
    ('', None), ('Authorization: per-routine-key', ('Authorization', 'per-routine-key')),
    ("X!#$%&'*+-.^_`|~Key:  key:with:colons \t", ("X!#$%&'*+-.^_`|~Key", 'key:with:colons')),
])
def test_receiver_auth_loaded_from_env_and_dotenv(monkeypatch, tmp_path, header, expected):
    from grok_codex.cli import load_config
    from grok_codex.config import Settings

    config = tmp_path/'receiver.env'
    config.write_text('BRIDGE_WEBHOOK_AUTH_HEADER="'+header+'"\n')
    # Track load_config's environment change so monkeypatch restores it afterwards.
    monkeypatch.setenv('BRIDGE_WEBHOOK_AUTH_HEADER', '')
    monkeypatch.delenv('BRIDGE_WEBHOOK_AUTH_HEADER')
    load_config(config)
    assert Settings().webhook_auth == expected
    monkeypatch.setenv('BRIDGE_WEBHOOK_AUTH_HEADER', 'X-Override: env-key')
    load_config(config)
    assert Settings().webhook_auth == ('X-Override', 'env-key')


async def test_receiver_auth_not_exposed_on_success_or_failure(bridge, settings, caplog):
    from grok_codex.api import create_app

    secret = 'unique-private-receiver-credential'
    settings.webhook_url = 'https://callback.example/events'
    settings.webhook_secret = 's'*40
    settings.webhook_auth_header = 'Authorization: '+secret
    settings.validate()
    requests = []

    def transport(request):
        requests.append(request)
        assert request.headers['Authorization'] == secret
        if len(requests) == 1:
            raise httpx.ConnectError('receiver echoed '+secret, request=request)
        return httpx.Response(403 if len(requests) == 2 else 204, text=secret)

    await bridge.dispatcher.client.aclose()
    caplog.set_level(logging.DEBUG)
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        dispatcher = Dispatcher(bridge.store, settings, client)
        bridge.store.event('s', 'error', urgent=True)
        for expected in ['pending', 'pending', 'delivered']:
            bridge.store.execute('UPDATE deliveries SET due=0')
            await dispatcher.tick()
            assert bridge.store.one('SELECT status FROM deliveries')['status'] == expected
        for request in requests:
            assert secret not in request.content.decode()

    app = create_app(settings, bridge)
    app.state.bridge = bridge
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://bridge') as client:
        for path in ['/deliveries', '/dashboard', '/openapi.json', '/healthz']:
            response = await client.get(path, headers={'Authorization': 'Bearer '+settings.human_token})
            assert response.status_code == 200
            assert secret not in response.text
    assert secret not in repr(settings)
    assert secret not in caplog.text
    assert secret not in json.dumps(bridge.store.all('SELECT * FROM events'))
    assert secret not in json.dumps(bridge.store.all('SELECT * FROM deliveries'))


async def test_stale_backlog_expires_on_restart_and_redelivery(bridge, settings, monkeypatch):
    settings.webhook_url = 'https://callback.example/events'
    settings.webhook_secret = 's'*40
    settings.webhook_max_event_age_sec = 900
    clock = [time.time()]
    monkeypatch.setattr('grok_codex.webhooks.time.time', lambda: clock[0])
    requests = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: requests.append(request) or httpx.Response(503))) as client:
        dispatcher = Dispatcher(bridge.store, settings, client)
        bridge.store.event('s', 'approval/required', urgent=True)
        bridge.store.event('s', 'progress')
        await dispatcher.tick()
        assert len(requests) == 2
        # Include a legacy payload without source time, with a misleadingly fresh
        # batching timestamp. Its original sequence range still establishes age.
        row = bridge.store.one('SELECT * FROM deliveries LIMIT 1')
        payload = json.loads(row['payload'])
        payload.pop('event_time')
        payload['timestamp'] = clock[0] + 901
        bridge.store.execute('UPDATE deliveries SET payload=? WHERE id=?', (json.dumps(payload), row['id']))
        bridge.store.event('s', 'unbatched-old', urgent=True)
        clock[0] += 901
        restored = Dispatcher(bridge.store, settings, client)
        assert all(r['status'] == 'expired' for r in bridge.store.all('SELECT status FROM deliveries'))
        assert bridge.store.one("SELECT batched FROM events WHERE kind='unbatched-old'")['batched'] == 1
        # Replay retains local logs but cannot re-alert stale events.
        assert restored.replay('s', 0) == 3
        await restored.tick()
        assert len(requests) == 2
        bridge.store.event('s', 'process/lost', urgent=True)
        await restored.tick()
        assert len(requests) == 3
        clock[0] += 901
        bridge.store.execute("UPDATE deliveries SET due=0 WHERE status='pending'")
        await restored.tick()
        assert len(requests) == 3
        assert len(bridge.store.logs('s', 0, 100)['events']) == 4


@pytest.mark.parametrize('value', ['0', '-1', 'nan', 'inf'])
def test_invalid_webhook_max_age(settings, monkeypatch, value):
    from grok_codex.config import Settings
    monkeypatch.setenv('BRIDGE_WEBHOOK_MAX_EVENT_AGE_SEC', value)
    configured = Settings(state_dir=settings.state_dir, grok_token='g'*40, human_token='h'*40)
    with pytest.raises(ValueError, match='BRIDGE_WEBHOOK_MAX_EVENT_AGE_SEC'):
        configured.validate()
