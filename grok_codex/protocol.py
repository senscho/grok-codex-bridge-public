from __future__ import annotations

import asyncio
import json
import os
import signal
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


class RpcError(RuntimeError):
    def __init__(self, code: int, message: str):
        self.code = code
        super().__init__(message)


class LaunchError(RuntimeError):
    """Known pre-dispatch failure; messages are safe for local status/events."""

    def __init__(self, reason: str):
        self.detail = {
            'not_found': 'Codex executable not found. Install Codex CLI or set BRIDGE_CODEX_BINARY to its absolute executable path, then restart the bridge.',
            'permission_denied': 'Codex executable cannot be launched: permission denied. Check BRIDGE_CODEX_BINARY and executable permissions, then restart the bridge.',
        }[reason]
        super().__init__(self.detail)


class AppServer:
    """Private JSONL transport. Requests are never retried after an ambiguous write."""

    def __init__(self, binary: str, home: Path, callback: EventHandler, timeout: float = 45):
        self.binary, self.home, self.callback, self.timeout = binary, home, callback, timeout
        self.process: asyncio.subprocess.Process | None = None
        self.pending: dict[int, asyncio.Future] = {}
        self.counter = 0
        self.writer_lock = asyncio.Lock()
        self.tasks: list[asyncio.Task] = []
        self.closing = False

    @property
    def pid(self) -> int | None:
        return self.process.pid if self.process else None

    async def start(self) -> None:
        # Do not inherit provider keys from the daemon: each identity authenticates
        # using its own CODEX_HOME. Preserve network proxy/CA environment settings.
        env = {k: v for k, v in os.environ.items() if not (
            k.startswith('BRIDGE_') or k in {'OPENAI_API_KEY','OPENAI_BASE_URL','CODEX_API_KEY','ANTHROPIC_API_KEY','XAI_API_KEY'}
        )}
        env['CODEX_HOME'] = str(self.home)
        try:
            self.process = await asyncio.create_subprocess_exec(
                os.path.expanduser(self.binary), 'app-server', '--listen', 'stdio://', env=env,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024,
                start_new_session=True)
        except FileNotFoundError as exc:
            raise LaunchError('not_found') from exc
        except PermissionError as exc:
            raise LaunchError('permission_denied') from exc
        await self.callback({'method':'bridge/processStarted','params':{'pid':self.process.pid}})
        self.tasks = [asyncio.create_task(self._read()), asyncio.create_task(self._drain_stderr())]
        await self.request('initialize', {'clientInfo': {'name': 'grok_codex_bridge', 'version': '0.1.0'},
                                          'capabilities': {'experimentalApi': False}})
        await self.write({'method': 'initialized'})

    async def write(self, value: dict) -> None:
        async with self.writer_lock:
            if not self.process or not self.process.stdin or self.process.returncode is not None:
                raise ConnectionError('App Server is not running')
            self.process.stdin.write((json.dumps(value, separators=(',', ':')) + '\n').encode())
            await self.process.stdin.drain()

    async def request(self, method: str, params: dict) -> dict:
        self.counter += 1
        rid = self.counter
        future = asyncio.get_running_loop().create_future()
        self.pending[rid] = future
        try:
            await self.write({'id': rid, 'method': method, 'params': params})
            return await asyncio.wait_for(future, self.timeout)
        finally:
            self.pending.pop(rid, None)

    async def respond(self, request_id: str | int, result: dict) -> None:
        await self.write({'id': request_id, 'result': result})

    async def _read(self) -> None:
        assert self.process and self.process.stdout
        try:
            while line := await self.process.stdout.readline():
                msg = json.loads(line)
                if 'method' in msg:
                    await self.callback(msg)
                else:
                    f = self.pending.get(msg.get('id'))
                    if f and not f.done():
                        if 'error' in msg:
                            e = msg['error']
                            f.set_exception(RpcError(e['code'], e['message']))
                        else:
                            f.set_result(msg.get('result', {}))
        except asyncio.CancelledError:
            raise
        except Exception:
            # No raw protocol payloads or exception messages in process logs.
            pass
        finally:
            for f in list(self.pending.values()):
                if not f.done():
                    f.set_exception(ConnectionError('App Server connection lost'))
            if not self.closing:
                await self.callback({'method': 'bridge/processLost', 'params': {}})

    async def _drain_stderr(self) -> None:
        assert self.process and self.process.stderr
        while await self.process.stderr.read(65536):
            pass  # Untrusted stderr may contain credentials; never persist it.

    async def close(self) -> None:
        self.closing = True
        p = self.process
        if p and p.returncode is None:
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(p.wait(), 5)
            except asyncio.TimeoutError:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await p.wait()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
