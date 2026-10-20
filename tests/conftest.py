import pytest

from grok_codex.config import Settings
from grok_codex.service import Bridge
from tests.fakes import FakeServer


@pytest.fixture
def settings(tmp_path):
    repo = tmp_path/'repos'
    repo.mkdir()
    return Settings(state_dir=tmp_path/'state',repo_roots=(repo,),grok_token='g'*40,human_token='h'*40,progress_interval=.1)


@pytest.fixture
def bridge(settings):
    b = Bridge(settings,FakeServer)
    b.register('one')
    b.register('two')
    yield b
    b.store.db.close()
