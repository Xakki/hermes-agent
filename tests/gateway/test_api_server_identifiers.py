"""Deterministic project/profile conversation identifiers and outbound error redaction."""
import hashlib
from unittest.mock import patch

from gateway.platforms.api_server import _derive_chat_session_id, _redact_api_error_text


class TestRedactApiErrorText:
    def test_masks_secret_value_but_preserves_structure(self):
        secret = "sk-api-server-leak-1234567890"
        out = _redact_api_error_text(Exception(f"auth failed OPENAI_API_KEY={secret}"))
        assert secret not in out
        assert "OPENAI_API_KEY=" in out

    def test_redacts_regardless_of_global_redaction_setting(self):
        # force=True must mask even when global redaction is disabled.
        secret = "sk-forced-redaction-0987654321"
        with patch("agent.redact._REDACT_ENABLED", False):
            out = _redact_api_error_text(Exception(f"boom AWS_SECRET_ACCESS_KEY={secret}"))
        assert secret not in out

    def test_limit_truncates_after_redaction(self):
        assert len(_redact_api_error_text("x" * 500, limit=50)) == 50


class TestDeriveChatSessionId:
    def test_deterministic(self):
        """Same inputs always produce the same session ID."""
        a = _derive_chat_session_id("sys", "hello")
        b = _derive_chat_session_id("sys", "hello")
        assert a == b

    def test_different_system_prompt(self):
        a = _derive_chat_session_id("You are a pirate.", "Hello")
        b = _derive_chat_session_id("You are a robot.", "Hello")
        assert a != b

    def test_routed_profile_namespaces_the_id_without_moving_default(self):
        """Two header-less conversations opening with identical text on different profiles must
        not share one session/sandbox key (#123989); default/standalone ids stay byte-identical
        so live conversations survive the upgrade."""
        legacy = "api-" + hashlib.sha256(b"sys\nhello").hexdigest()[:16]
        assert _derive_chat_session_id("sys", "hello") == legacy
        assert _derive_chat_session_id("sys", "hello", "default") == legacy
        research = _derive_chat_session_id("sys", "hello", "research")
        assert research != legacy
        assert research == _derive_chat_session_id("sys", "hello", "research")

    def test_launch_profile_prefix_keeps_the_unprefixed_id(self, monkeypatch, tmp_path):
        """A gateway launched as ``work`` serves ``/p/work/`` and the bare route as ONE profile:
        both must derive one id, or the same conversation forks by URL."""
        import hermes_constants
        from hermes_cli import profiles

        work = tmp_path / "profiles" / "work"
        work.mkdir(parents=True)
        monkeypatch.setattr(hermes_constants, "get_routing_process_hermes_home", lambda: work)
        monkeypatch.setattr(profiles, "get_profile_dir", lambda name: tmp_path / "profiles" / name)
        assert _derive_chat_session_id("sys", "hello", "work") == _derive_chat_session_id("sys", "hello")
        assert _derive_chat_session_id("sys", "hello", "research") != _derive_chat_session_id("sys", "hello")
