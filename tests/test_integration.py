"""Opt-in, isolated home; authenticates nowhere and never reads an existing login."""
import asyncio
import os
import shutil

import pytest

from grok_codex.protocol import AppServer, RpcError

pytestmark = [pytest.mark.integration,pytest.mark.skipif(os.getenv('RUN_CODEX_INTEGRATION')!='1' or not shutil.which('codex'),reason='Set RUN_CODEX_INTEGRATION=1 with Codex installed')]


async def test_installed_codex_lifecycle(tmp_path):
    home=tmp_path/'home'; home.mkdir(mode=0o700)
    repo=tmp_path/'repo'; repo.mkdir()
    events=[]
    async def on_event(msg):
        events.append(msg)
    server=AppServer('codex',home,on_event,20)
    try:
        await server.start()
        thread=await server.request('thread/start',{'cwd':str(repo),'sandbox':'read-only','approvalPolicy':'on-request','approvalsReviewer':'user','ephemeral':False})
        tid=thread['thread']['id']
        metadata=await server.request('thread/read',{'threadId':tid,'includeTurns':False})
        assert metadata['thread']['id']==tid
        # This test permits a failed authentication turn: it verifies materialization
        # and restart/resume without requiring or consuming a user's credentials.
        try:
            await server.request('turn/start',{'threadId':tid,'input':[{'type':'text','text':'Reply OK. Do not run tools.'}]})
        except RpcError:
            pass
        for _ in range(100):
            if any(e['method']=='turn/completed' for e in events): break
            await asyncio.sleep(.1)
    finally:
        await server.close()
    resumed=AppServer('codex',home,on_event,20)
    try:
        await resumed.start()
        response=await resumed.request('thread/resume',{'threadId':tid,'excludeTurns':True,'cwd':str(repo),'sandbox':'read-only','approvalPolicy':'on-request','approvalsReviewer':'user'})
        assert response['thread']['id']==tid
        assert any(e['method']=='turn/started' for e in events)
    finally:
        await resumed.close()
