"""Optional lock-module absence must not prevent a tool-free named-profile model decision."""
import builtins
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_cli.interaction_decider import decide
from hermes_cli.interaction_lifecycle import InteractionSession


def _model_decision(tmp_path, monkeypatch, *, without_fcntl=False):
    from hermes_cli import _early_recovery
    monkeypatch.setattr(_early_recovery, 'interrupted_pull_marker', lambda root: tmp_path / 'pull-marker')
    from tui_gateway import server
    from agent import auxiliary_client
    from hermes_constants import get_hermes_home

    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    root = tmp_path / '.hermes'
    home = root / 'profiles' / 'decision'
    home.mkdir(parents=True)
    (home / 'config.yaml').write_text('model: {default: fixture, provider: custom}\n')
    monkeypatch.setenv('HERMES_HOME', str(root))
    seen = []
    monkeypatch.setattr(server, '_resolve_runtime_with_fallback', lambda params: SimpleNamespace(
        selected_model='fixture', runtime={'provider': 'custom', 'api_mode': 'chat_completions'}))

    def model(**kwargs):
        seen.append((get_hermes_home(), kwargs['model'], kwargs['tools']))
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"choice":"stop"}'))])

    monkeypatch.setattr(auxiliary_client, 'call_llm', model)
    if without_fcntl:
        original = builtins.__import__

        def optional_import(name, *args, **kwargs):
            if name == 'fcntl':
                raise ModuleNotFoundError('synthetic optional dependency unavailable', name='fcntl')
            return original(name, *args, **kwargs)

        monkeypatch.setattr(builtins, '__import__', optional_import)
    authority = object()
    session = InteractionSession('s', 'owner', authority)
    session.grant(authority, profile='decision', scope='accepted', seconds=60, decisions=1)
    request = session.open('next_action', ('stop',), scope='accepted')
    assert decide(session, session.delegate(request.request_id, authority))
    assert seen == [(home, 'fixture', [])]
    assert request.choice == 'stop'
    assert session.journal[-1]['actor'] == 'agent'
    assert get_hermes_home() == root


@pytest.mark.parametrize('without_fcntl', [False, True])
def test_portable_model_path_with_optional_lock_dependency(tmp_path, monkeypatch, without_fcntl):
    _model_decision(tmp_path, monkeypatch, without_fcntl=without_fcntl)


@pytest.mark.platforms('windows')
def test_native_windows_named_profile_reaches_model_and_applies_choice(tmp_path, monkeypatch):
    _model_decision(tmp_path, monkeypatch)
