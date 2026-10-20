import hashlib
import hmac
import json

import httpx

from grok_codex.api import create_app
from tests.fakes import settle


async def test_vertical_tool_thread_event_webhook_terminal_inspection(bridge, settings):
    repo = settings.repo_roots[0]/'demo'
    repo.mkdir()
    settings.webhook_url = 'http://127.0.0.1:9999/callback'
    settings.webhook_secret = 's'*40
    received = []
    async def receive(request):
        received.append(request)
        return httpx.Response(204)
    await bridge.dispatcher.client.aclose()
    bridge.dispatcher.client = httpx.AsyncClient(transport=httpx.MockTransport(receive))
    # API is the Grok tool entry point and the terminal's inspection entry point.
    app = create_app(settings,bridge)
    app.state.bridge = bridge
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://test') as client:
        r = await client.post('/tools/codex_start',headers={'Authorization':'Bearer '+settings.grok_token,'Idempotency-Key':'start-1'},json={'repo':str(repo),'account':'one','prompt':'Fix a bug'})
        assert r.status_code==202, r.text
        sid = r.json()['session_id']
        await settle(bridge)
        server = bridge.servers[sid]
        thread_id = bridge.status(sid)['thread_id']
        await server.callback({'method':'item/completed','params':{'item':{'id':'i','type':'agentMessage','text':'Done; token=supersecret'}}})
        await server.complete(turn_id=bridge.status(sid)['turn_id'])
        await bridge.dispatcher.tick()
        assert received
        body = received[-1].content
        signature = hmac.new(settings.webhook_secret.encode(),received[-1].headers['x-bridge-timestamp'].encode()+b'.'+body,hashlib.sha256).hexdigest()
        assert received[-1].headers['x-bridge-signature']=='sha256='+signature
        assert b'supersecret' not in body and b'Fix a bug' not in body
        inspect = await client.post('/tools/codex_status',headers={'Authorization':'Bearer '+settings.human_token},json={'session_id':sid})
        assert inspect.json()['thread_id']==thread_id
        logs = await client.post('/tools/codex_logs',headers={'Authorization':'Bearer '+settings.human_token},json={'session_id':sid})
        assert any(e['kind']=='turn/completed' for e in logs.json()['events'])
        assert 'supersecret' not in logs.text
    await bridge.dispatcher.client.aclose()
