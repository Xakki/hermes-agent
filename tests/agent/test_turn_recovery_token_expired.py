"""Regression coverage for stale Codex reasoning recovery after OAuth refresh."""

from types import SimpleNamespace

import pytest

from agent.error_classifier import FailoverReason
from agent.turn_recovery import recover_after_classification
from agent.turn_retry_state import TurnRetryState


class _RecoveryAgent:
    api_mode = "codex_responses"
    provider = "openai-codex"
    log_prefix = ""
    model = "gpt-5"
    base_url = "https://chatgpt.com/backend-api/codex"
    codex_responses_native_compaction = False

    def __init__(self):
        self._codex_reasoning_replay_enabled = True
        self.refresh_calls = 0
        self.replay_disable_calls = 0

    def _recover_with_credential_pool(self, **_kwargs):
        return False, False

    def _try_refresh_codex_client_credentials(self, *, force):
        assert force is True
        self.refresh_calls += 1
        return False

    def _disable_codex_reasoning_replay(self, messages):
        self.replay_disable_calls += 1
        item_count = sum(len(message.get("codex_reasoning_items", [])) for message in messages)
        message_count = sum("codex_reasoning_items" in message for message in messages)
        for message in messages:
            message.pop("codex_reasoning_items", None)
        self._codex_reasoning_replay_enabled = False
        return {"items": item_count, "messages": message_count}

    def _buffer_vprint(self, _message):
        pass

    def _vprint(self, _message, *, force):
        assert force is True


def _cached_reasoning_messages():
    return [
        {"role": "user", "content": "first"},
        {
            "role": "assistant",
            "content": "answer",
            "codex_reasoning_items": [{"type": "reasoning", "encrypted_content": "stale"}],
        },
        {"role": "user", "content": "next"},
    ]


def _classified(reason=FailoverReason.auth):
    return SimpleNamespace(reason=reason, billing_unverified=False)


def _recover(agent, retry, messages, *, status_code=401, error_context=None, reason=FailoverReason.auth):
    return recover_after_classification(
        agent,
        RuntimeError("provider rejected request"),
        _classified(reason),
        retry,
        status_code=status_code,
        error_context=error_context or {},
        messages=messages,
        api_messages=[],
    )


def test_token_expired_strips_cached_reasoning_only_after_codex_auth_retry():
    agent = _RecoveryAgent()
    retry = TurnRetryState(codex_auth_retry_attempted=True)
    messages = _cached_reasoning_messages()

    recovered, recovered_with_pool = _recover(
        agent, retry, messages, error_context={"reason": "token_expired"}
    )

    assert recovered is True
    assert recovered_with_pool is False
    assert agent.refresh_calls == 0
    assert agent.replay_disable_calls == 1
    assert retry.invalid_encrypted_content_retry_attempted is True
    assert agent._codex_reasoning_replay_enabled is False
    assert all("codex_reasoning_items" not in message for message in messages)

    agent._codex_reasoning_replay_enabled = True
    messages[1]["codex_reasoning_items"] = [{"type": "reasoning", "encrypted_content": "stale"}]
    recovered, _ = _recover(agent, retry, messages, error_context={"reason": "token_expired"})
    assert recovered is False
    assert agent.replay_disable_calls == 1


def test_token_expired_does_not_strip_before_codex_auth_refresh_attempt():
    agent = _RecoveryAgent()
    retry = TurnRetryState()
    messages = _cached_reasoning_messages()

    recovered, _ = _recover(agent, retry, messages, error_context={"reason": "token_expired"})

    assert recovered is False
    assert retry.codex_auth_retry_attempted is True
    assert agent.refresh_calls == 1
    assert agent.replay_disable_calls == 0
    assert messages[1]["codex_reasoning_items"]


@pytest.mark.parametrize("error_context", [{}, {"reason": "invalid_api_key"}])
def test_generic_401_does_not_strip_cached_reasoning_after_codex_auth_retry(error_context):
    agent = _RecoveryAgent()
    retry = TurnRetryState(codex_auth_retry_attempted=True)
    messages = _cached_reasoning_messages()

    recovered, _ = _recover(agent, retry, messages, error_context=error_context)

    assert recovered is False
    assert agent.replay_disable_calls == 0
    assert messages[1]["codex_reasoning_items"]


def test_invalid_encrypted_content_400_still_strips_cached_reasoning():
    agent = _RecoveryAgent()
    retry = TurnRetryState()
    messages = _cached_reasoning_messages()

    recovered, _ = _recover(
        agent,
        retry,
        messages,
        status_code=400,
        reason=FailoverReason.invalid_encrypted_content,
    )

    assert recovered is True
    assert agent.replay_disable_calls == 1
    assert all("codex_reasoning_items" not in message for message in messages)
