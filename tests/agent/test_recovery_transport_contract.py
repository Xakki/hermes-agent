"""Real cached Responses transport and replay-disable contract at recovery boundaries."""
from types import SimpleNamespace

from agent.error_classifier import FailoverReason
from agent.transports.codex import ResponsesApiTransport
from agent.turn_recovery import recover_after_classification
from agent.turn_retry_state import TurnRetryState
from run_agent import AIAgent


def test_real_transport_drops_unverified_then_full_strip_preserves_replay():
    transport = ResponsesApiTransport()
    own = {'type': 'reasoning', 'encrypted_content': 'OWN',
           '_issuer_kind': 'other:https://api.openai.com/v1', '_issuer_model': 'gpt-5.5'}
    legacy = {'type': 'reasoning', 'encrypted_content': 'LEGACY'}
    messages = [{'role': 'user', 'content': 'question'},
                {'role': 'assistant', 'content': 'answer', 'codex_reasoning_items': [own, legacy]}]
    transport.build_kwargs(model='gpt-5.5', messages=messages, base_url='https://api.openai.com/v1',
                           reasoning_config={'enabled': True, 'effort': 'low'}, is_codex_backend=False)
    disabled = []
    agent = SimpleNamespace(api_mode='codex_responses', provider='custom', base_url='https://api.openai.com/v1',
                            model='gpt-5.5', log_prefix='', _codex_reasoning_replay_enabled=True,
                            _get_transport=lambda: transport, _recover_with_credential_pool=lambda **kw: (False, False),
                            _vprint=lambda *a, **kw: None, _buffer_vprint=lambda *a: None)

    def disable(rows, *, keep_replay=False):
        disabled.append(keep_replay)
        return AIAgent._disable_codex_reasoning_replay(agent, rows, keep_replay=keep_replay)

    agent._disable_codex_reasoning_replay = disable
    retry = TurnRetryState()
    classified = SimpleNamespace(reason=FailoverReason.invalid_encrypted_content, billing_unverified=False)

    def recover():
        return recover_after_classification(agent, RuntimeError('invalid encrypted content'), classified, retry,
                                            status_code=400, error_context={}, messages=messages, api_messages=[])

    assert recover() == (True, False)
    assert messages[1]['codex_reasoning_items'] == [own]
    assert disabled == []
    assert retry.invalid_encrypted_content_retry_attempted is False
    assert agent._codex_reasoning_replay_enabled is True
    assert recover() == (True, False)
    assert 'codex_reasoning_items' not in messages[1]
    assert disabled == [True]
    assert retry.invalid_encrypted_content_retry_attempted is True
    assert agent._codex_reasoning_replay_enabled is True
