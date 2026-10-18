"""Pre-rotation recovery preserves short-circuit order and ignores unrelated transports."""
from types import SimpleNamespace

import pytest

from agent import turn_recovery
from agent.turn_retry_state import TurnRetryState


@pytest.mark.parametrize('status,message', [
    (401, 'token_expired'),
    (400, 'invalid encrypted content'),
    (500, 'reasoning.context is unsupported'),
    (None, 'provider rejected request'),
])
def test_unrelated_error_does_not_resolve_reasoning_transport(status, message):
    calls = []
    agent = SimpleNamespace(_get_transport=lambda: calls.append('transport'))
    error = RuntimeError(message)
    error.status_code = status
    assert turn_recovery._reject_all_turns(agent, error) is False
    assert calls == []


@pytest.mark.parametrize('message', ['reasoning.context unsupported', 'ALL_TURNS unsupported'])
def test_matching_error_delegates_to_the_cached_transport(message):
    calls = []
    error = RuntimeError(message)
    error.status_code = 400
    transport = SimpleNamespace(reject_all_turns=lambda received: calls.append(received) or True)
    agent = SimpleNamespace(_get_transport=lambda: transport)
    assert turn_recovery._reject_all_turns(agent, error) is True
    assert calls == [error]


@pytest.mark.parametrize('welcome,clamp,expected', [
    (True, False, ['welcome']),
    (False, True, ['welcome', 'clamp']),
    (False, False, ['welcome', 'clamp', 'reasoning']),
])
def test_initial_recovery_preserves_pre_rotation_order(monkeypatch, welcome, clamp, expected):
    calls = []
    monkeypatch.setattr(turn_recovery, '_recover_welcome_tier', lambda *args: calls.append('welcome') or welcome)
    monkeypatch.setattr(turn_recovery, '_clamp_to_affordable_budget', lambda *args: calls.append('clamp') or clamp)
    monkeypatch.setattr(turn_recovery, '_reject_all_turns', lambda *args: calls.append('reasoning') or True)
    agent = SimpleNamespace(api_mode='codex_responses')
    assert turn_recovery._recover_initial_request_policy(agent, RuntimeError(), object(), TurnRetryState()) is True
    assert calls == expected
