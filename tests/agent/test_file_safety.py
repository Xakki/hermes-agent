"""Tests for the owner-selected project read policy and independent home guards.

Run with: scripts/run_tests.sh tests/agent/test_file_safety.py -v
"""

from unittest.mock import patch

import pytest

from agent.file_safety import get_read_block_error


# ---------------------------------------------------------------------------
# Project-local environment files follow the owner's exact-basename policy.
# ---------------------------------------------------------------------------


class TestEnvFileReadBlocking:
    """Only .secret is denied by the project-local basename rule."""

    @pytest.mark.parametrize("basename", [
        ".env",
        ".env.local",
        ".env.development",
        ".env.production",
        ".env.test",
        ".env.staging",
        ".envrc",
        ".ENV",
        ".Env.Local",
        ".ENV.PRODUCTION",
        ".ENVRC",
        ".env.example",
        ".secret.local",
    ])
    def test_project_env_basenames_are_readable(self, tmp_path, basename):
        assert get_read_block_error(str(tmp_path / "project" / basename)) is None


    @pytest.mark.parametrize("basename", [
        ".secret",
        ".SECRET",
        ".Secret",
    ])
    def test_secret_basename_is_blocked_case_insensitively(self, tmp_path, basename):
        error = get_read_block_error(str(tmp_path / "project" / basename))
        assert error is not None, f"{basename} should be blocked"
        assert "Access denied" in error
        assert "environment file" in error.lower()


    def test_symlink_to_secret_stays_blocked(self, tmp_path):
        target = tmp_path / ".secret"
        target.write_text("synthetic", encoding="utf-8")
        alias = tmp_path / "public.txt"
        alias.symlink_to(target)
        assert get_read_block_error(str(alias)) is not None






# ---------------------------------------------------------------------------
# Existing cache-file blocking (regression — must still work)
# ---------------------------------------------------------------------------


class TestCacheFileReadBlocking:
    """Internal Hermes cache files must remain blocked."""

    def test_hub_index_cache_blocked(self, tmp_path):
        """Hub index-cache reads are blocked."""
        hermes_home = tmp_path / ".hermes"
        cache = hermes_home / "skills" / ".hub" / "index-cache" / "data.json"
        cache.parent.mkdir(parents=True)
        cache.write_text("{}")

        with patch("agent.file_safety._hermes_home_path", return_value=hermes_home):
            error = get_read_block_error(str(cache))
            assert error is not None
            assert "internal Hermes cache" in error

    def test_hub_directory_blocked(self, tmp_path):
        """Hub directory reads are blocked."""
        hermes_home = tmp_path / ".hermes"
        hub = hermes_home / "skills" / ".hub" / "metadata.json"
        hub.parent.mkdir(parents=True)
        hub.write_text("{}")

        with patch("agent.file_safety._hermes_home_path", return_value=hermes_home):
            error = get_read_block_error(str(hub))
            assert error is not None


# ---------------------------------------------------------------------------
# Combined: env guard + cache guard don't interfere
# ---------------------------------------------------------------------------


class TestCombinedGuards:
    """Both guards should work independently without interference."""

    def test_env_guard_works_regardless_of_hermes_home(self, tmp_path):
        """The env basename guard does not depend on HERMES_HOME resolution."""
        hermes_home = tmp_path / ".hermes"
        hermes_home.mkdir()

        with patch("agent.file_safety._hermes_home_path", return_value=hermes_home):
            error = get_read_block_error(str(tmp_path / "project" / ".secret"))
            assert error is not None

            error = get_read_block_error(str(tmp_path / "project" / ".env"))
            assert error is None

    @pytest.mark.parametrize("relative", [
        ".env", "auth.json", ".anthropic_oauth.json", "auth/google_oauth.json",
        "mcp-tokens/token.json", "vault/vault.key", "browser-profile/Default/Cookies",
    ])
    def test_profile_and_root_credentials_remain_read_denied(self, tmp_path, monkeypatch, relative):
        import agent.file_safety as fs

        root = tmp_path / "hermes"
        profile = root / "profiles" / "synthetic"
        profile.mkdir(parents=True)
        monkeypatch.setattr(fs, "_hermes_root_path", lambda: root)
        monkeypatch.setattr(fs, "_hermes_home_path", lambda: profile)
        for index, home in enumerate((root, profile)):
            target = home / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("synthetic", encoding="utf-8")
            alias = tmp_path / f"alias-{index}"
            alias.symlink_to(target)
            assert get_read_block_error(str(target)) is not None
            assert get_read_block_error(str(alias)) is not None

    def test_cache_guard_still_works_with_env_guard(self, tmp_path):
        """Cache file blocking still works when env guard is active."""
        hermes_home = tmp_path / ".hermes"
        cache = hermes_home / "skills" / ".hub" / "index-cache" / "x"
        cache.parent.mkdir(parents=True)
        cache.write_text("")

        with patch("agent.file_safety._hermes_home_path", return_value=hermes_home):
            error = get_read_block_error(str(cache))
            assert error is not None
            assert "internal Hermes cache" in error
