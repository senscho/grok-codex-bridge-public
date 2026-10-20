"""Demo receiver: BRIDGE_WEBHOOK_SECRET=... uv run --locked uvicorn examples.webhook_receiver:app --port 9900.

Production: commit event_id in your bot's durable database before acknowledging,
then process asynchronously. Deduplicate contained event IDs/sequence numbers on
replay as replay has a new delivery envelope ID. This demo uses memory only.
"""
import hashlib
import hmac
import json
import os
import time

from fastapi import FastAPI, HTTPException, Request

app=FastAPI()
seen=set()


@app.post('/callback')
async def callback(request: Request):
    body=await request.body()
    timestamp=request.headers.get('x-bridge-timestamp','')
    try:
        if abs(time.time()-int(timestamp))>300: raise ValueError()
    except ValueError:
        raise HTTPException(401,'Expired or invalid timestamp')
    secret=os.environ['BRIDGE_WEBHOOK_SECRET']
    if len(secret)<32: raise RuntimeError('Configure a strong webhook secret')
    expected='sha256='+hmac.new(secret.encode(),timestamp.encode()+b'.'+body,hashlib.sha256).hexdigest()
    if not hmac.compare_digest(request.headers.get('x-bridge-signature',''),expected):
        raise HTTPException(401,'Invalid signature')
    payload=json.loads(body)
    event_id=payload['event_id']
    if event_id not in seen:
        seen.add(event_id)
        print(json.dumps({'event_id':event_id,'session_id':payload['session_id'],'summary':payload['summary']}),flush=True)
    return {'accepted':event_id}
