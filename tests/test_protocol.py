import asyncio
import os
import sys
from pathlib import Path

import pytest

from grok_codex.protocol import AppServer, RpcError


async def test_real_subprocess_jsonl_events_errors_eof_and_timeout(tmp_path):
    launcher=tmp_path/'codex-mock'
    fixture=Path(__file__).with_name('stdio_fixture.py')
    launcher.write_text('#!'+sys.executable+'\nimport runpy\nrunpy.run_path('+repr(str(fixture))+')\n')
    launcher.chmod(0o700)
    events=[]
    async def event(m): events.append(m)
    # Interpreter startup can exceed 200ms on macOS, especially in a new venv.
    server=AppServer(str(launcher),tmp_path,event,5)
    await server.start()
    assert (await server.request('fixture/event',{}))['ok']
    assert any(e['method']=='turn/started' for e in events)
    with pytest.raises(RpcError):
        await server.request('fixture/error',{})
    server.timeout = .2
    with pytest.raises(TimeoutError):
        await server.request('fixture/hang',{})
    with pytest.raises(ConnectionError):
        await server.request('fixture/exit',{})
    await server.close()
    assert any(e['method']=='bridge/processLost' for e in events)
    assert not server.pending


@pytest.mark.parametrize('reason', ['not_found', 'permission_denied'])
async def test_known_launch_failure_has_actionable_error_without_process(tmp_path, reason):
    from grok_codex.protocol import LaunchError
    binary=tmp_path/'missing-codex'
    if reason=='permission_denied':
        binary.write_text('#!/bin/sh\nexit 0\n')
        binary.chmod(0o600)
    async def event(m):
        raise AssertionError('No process should start')
    server=AppServer(str(binary),tmp_path,event,.2)
    with pytest.raises(LaunchError,match='BRIDGE_CODEX_BINARY'):
        await server.start()
    assert server.pid is None and not server.tasks
    await server.close()
