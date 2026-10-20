import asyncio
from uuid import uuid4

from grok_codex.protocol import RpcError


class FakeServer:
    histories = {}
    instances = []

    def __init__(self, binary, home, callback, timeout):
        self.home, self.callback = home, callback
        self.pid = None
        self.closed = False
        self.calls = []
        self.thread = None
        self.instances.append(self)

    async def start(self):
        pass

    async def request(self, method, params):
        self.calls.append((method,params))
        if method=='thread/start':
            self.thread = str(uuid4())
            self.histories[(str(self.home),self.thread)] = []
            return {'thread':{'id':self.thread,'status':{'type':'idle'}}}
        if method=='thread/resume':
            self.thread = params['threadId']
            if (str(self.home),self.thread) not in self.histories:
                raise RpcError(-32600,'no rollout found')
            return {'thread':{'id':self.thread,'status':{'type':'idle'}}}
        if method in {'turn/start','turn/steer'}:
            tid = params.get('expectedTurnId') or str(uuid4())
            self.histories[(str(self.home),params['threadId'])].extend(params['input'])
            await self.callback({'method':'turn/started','params':{'threadId':params['threadId'],'turn':{'id':tid}}})
            return {'turn':{'id':tid}}
        if method=='turn/interrupt':
            await self.complete('interrupted',params['turnId'])
            return {}
        raise AssertionError(method)

    async def complete(self, status='completed', turn_id=None):
        if not turn_id:
            for method,params in reversed(self.calls):
                if method in {'turn/start','turn/steer'}:
                    turn_id = params.get('expectedTurnId')
                    break
        # Tests supply actual active turn ID when relevant.
        await self.callback({'method':'turn/completed','params':{'turn':{'id':turn_id or 'turn','status':status}}})

    async def respond(self, rid, result):
        self.calls.append(('response',{'id':rid,**result}))

    async def write(self, value):
        self.calls.append(('write',value))

    async def close(self):
        self.closed = True


async def settle(bridge):
    if bridge.tasks:
        await asyncio.gather(*list(bridge.tasks))
