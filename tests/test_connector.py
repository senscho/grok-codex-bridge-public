"""Connector contract checks without requiring the optional MCP runtime in the bridge."""
import importlib.util
import sys
import types
from pathlib import Path

import httpx
import pytest


@pytest.fixture
def connector(monkeypatch):
    # Only stub the registration decorator; exercise real adapter HTTP/config code.
    class FastMCP:
        def __init__(self, name):
            self.name = name

        def tool(self):
            return lambda function: function

    for name in ['mcp', 'mcp.server', 'mcp.server.fastmcp']:
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    sys.modules['mcp.server.fastmcp'].FastMCP = FastMCP
    path = Path(__file__).resolve().parents[1] / 'connectors/grok-mcp/server.py'
    spec = importlib.util.spec_from_file_location('public_connector', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ['CODEX_TUNNEL_URL', 'CODEX_TUNNEL_URL_FILE', 'BRIDGE_PATH_PREFIX', 'CODEX_ALLOWED_ROOT', 'BRIDGE_GROK_TOKEN']:
        monkeypatch.delenv(name, raising=False)
    return module


def test_url_file_is_reread_with_env_precedence(connector, monkeypatch, tmp_path):
    filename = tmp_path / 'origin'
    filename.write_text('https://first.example\n')
    monkeypatch.setenv('CODEX_TUNNEL_URL_FILE', str(filename))
    assert connector.base() == 'https://first.example/codex'
    filename.write_text('https://second.example')
    assert connector.base() == 'https://second.example/codex'
    monkeypatch.setenv('CODEX_TUNNEL_URL', 'https://override.example/')
    monkeypatch.setenv('BRIDGE_PATH_PREFIX', '/bridge/v1/')
    assert connector.base() == 'https://override.example/bridge/v1'
    monkeypatch.setenv('BRIDGE_PATH_PREFIX', '')
    assert connector.base() == 'https://override.example'


@pytest.mark.parametrize('origin', [
    'http://remote.example', 'https://user:pass@example.com',
    'https://example.com/path', 'https://example.com?secret=value',
    'https://example.com#fragment', 'file:///private/config',
])
def test_invalid_origins(connector, monkeypatch, origin):
    monkeypatch.setenv('CODEX_TUNNEL_URL', origin)
    with pytest.raises(ValueError):
        connector.base()


@pytest.mark.parametrize('prefix', ['codex', '/..', '/codex/../tools', '/codex?x=1', '//codex'])
def test_invalid_prefix(connector, monkeypatch, prefix):
    monkeypatch.setenv('CODEX_TUNNEL_URL', 'https://tunnel.example')
    monkeypatch.setenv('BRIDGE_PATH_PREFIX', prefix)
    with pytest.raises(ValueError):
        connector.base()


@pytest.mark.parametrize('repo', ['/work/projects-other/repo', '/work/projects/../outside', 'relative/repo'])
def test_repo_guard_prevents_http(connector, monkeypatch, repo):
    monkeypatch.setenv('CODEX_ALLOWED_ROOT', '/work/projects')
    monkeypatch.setattr(connector.httpx, 'post', lambda *args, **kwargs: pytest.fail('unexpected network call'))
    assert 'error' in connector.codex_start(repo, 'task')


def test_transport_failure_retains_key_for_safe_retry(connector, monkeypatch):
    monkeypatch.setenv('CODEX_ALLOWED_ROOT', '/work/projects')
    monkeypatch.setenv('CODEX_TUNNEL_URL', 'https://tunnel.example')
    monkeypatch.setenv('BRIDGE_GROK_TOKEN', 'g'*40)
    requests = []

    def post(url, **kwargs):
        requests.append((url, kwargs))
        if len(requests) == 1:
            raise httpx.ReadTimeout('response lost')
        return httpx.Response(202, json={'session_id': 'example-session', 'job_id': 'example-job'})

    monkeypatch.setattr(connector.httpx, 'post', post)
    first = connector.codex_start('/work/projects/repo', 'task')
    assert 'error' in first and first['idempotency_key']
    second = connector.codex_start('/work/projects/repo', 'task', idempotency_key=first['idempotency_key'])
    assert second['http'] == 202
    assert requests[0] == requests[1]
    url, options = requests[0]
    assert url == 'https://tunnel.example/codex/tools/codex_start'
    assert options['follow_redirects'] is False and options['trust_env'] is False
    assert options['headers']['Authorization'] == 'Bearer '+'g'*40


def test_missing_configuration_and_human_only_acceptance(connector):
    assert 'error' in connector.codex_list()
    assert 'error' in connector.codex_start('/work/projects/repo', 'task')
    assert 'error' in connector.codex_decline('example-session', 'example-request', 'accept')
