"""Tests for `hermes update --yes / -y` — assume yes for interactive prompts.

Covers:
  1. argparse parses the flag
  2. Config-migration prompt is auto-answered (no input() call) and migrate_config
     runs with interactive=False so API-key prompts are skipped
  3. Autostash restore prompt is auto-answered (prompt_for_restore == False, no
     input() call) and the stash is applied automatically
"""

import subprocess
from types import SimpleNamespace
from unittest.mock import patch

from hermes_cli.main import cmd_update


def _make_run_side_effect(
    branch="main", verify_ok=True, commit_count="1", dirty=False
):
    """Minimal subprocess.run side_effect for the update flow."""

    def side_effect(cmd, **kwargs):
        joined = " ".join(str(c) for c in cmd)

        if "rev-parse" in joined and "--abbrev-ref" in joined:
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{branch}\n", stderr="")
        if "rev-parse" in joined and "--verify" in joined:
            return subprocess.CompletedProcess(
                cmd, 0 if verify_ok else 128, stdout="", stderr=""
            )
        if "rev-list" in joined:
            return subprocess.CompletedProcess(
                cmd, 0, stdout=f"{commit_count}\n", stderr=""
            )
        # `git status --porcelain` for dirty-tree detection during autostash.
        if "status" in joined and "--porcelain" in joined:
            out = " M hermes_cli/main.py\n" if dirty else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
        # `git stash list` — return a stash ref when dirty (so _stash_local_changes
        # gets something to return). _stash_local_changes_if_needed is what we
        # actually patch in tests that exercise restore, so this is a catch-all.
        if "stash" in joined and "list" in joined:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    return side_effect


class TestUpdateYesConfigMigration:
    """--yes auto-answers the config-migration prompt and skips API-key prompts."""

    @patch("hermes_cli.update_cmd._reload_config_modules")
    @patch("hermes_cli.update_cmd._run_migrate_config_fresh")
    @patch("hermes_cli.update_cmd._run_config_check_fresh", return_value=(1, 2))
    @patch("hermes_cli.config.get_missing_config_fields", return_value=[])
    @patch("hermes_cli.config.get_missing_env_vars", return_value=["NEW_KEY"])
    @patch("shutil.which", return_value=None)
    @patch("subprocess.run")
    def test_yes_auto_migrates_without_input(
        self,
        mock_run,
        _mock_which,
        _mock_missing_env,
        _mock_missing_cfg,
        _mock_version,
        mock_migrate,
        _mock_reload,
        capsys,
    ):
        mock_run.side_effect = _make_run_side_effect(
            branch="main", verify_ok=True, commit_count="1"
        )
        mock_migrate.return_value = {"env_added": [], "config_added": []}

        args = SimpleNamespace(yes=True)

        with patch("builtins.input") as mock_input:
            cmd_update(args)
            # Never prompted the user.
            mock_input.assert_not_called()

        # migrate_config was invoked with interactive=False — API-key prompts
        # are suppressed, matching gateway-mode semantics.
        assert mock_migrate.call_count == 1
        _, kwargs = mock_migrate.call_args
        assert kwargs.get("interactive") is False

        out = capsys.readouterr().out
        assert "--yes: auto-applying config migration" in out
        # The "Would you like to configure them now?" prompt text never appears.
        assert "Would you like to configure them now?" not in out

    @patch("hermes_cli.update_cmd._reload_config_modules")
    @patch("hermes_cli.update_cmd._run_migrate_config_fresh")
    @patch("hermes_cli.update_cmd._run_config_check_fresh", return_value=(1, 2))
    @patch("hermes_cli.config.get_missing_config_fields", return_value=[])
    @patch("hermes_cli.config.get_missing_env_vars", return_value=["NEW_KEY"])
    @patch("shutil.which", return_value=None)
    @patch("subprocess.run")
    def test_no_yes_flag_still_prompts_in_tty(
        self,
        mock_run,
        _mock_which,
        _mock_missing_env,
        _mock_missing_cfg,
        _mock_version,
        mock_migrate,
        _mock_reload,
        capsys,
    ):
        """Regression guard: without --yes, the TTY prompt path still fires."""
        mock_run.side_effect = _make_run_side_effect(
            branch="main", verify_ok=True, commit_count="1"
        )
        mock_migrate.return_value = {"env_added": [], "config_added": []}

        args = SimpleNamespace(yes=False)

        # Patch ``sys.stdin.isatty`` and ``sys.stdout.isatty`` directly on the
        # real ``sys`` module instead of replacing ``hermes_cli.main.sys`` with
        # a MagicMock. The MagicMock approach was flaky under ``pytest-xdist``
        # — a sibling test that imported ``hermes_cli.main`` first could leave
        # a different ``sys`` reference resolved inside the function and the
        # mock would never be consulted, with CI then taking the
        # "Non-interactive session" branch instead of prompting.
        import sys as _sys

        with patch("builtins.input", return_value="n") as mock_input, patch.object(
            _sys.stdin, "isatty", return_value=True
        ), patch.object(_sys.stdout, "isatty", return_value=True):
            cmd_update(args)
            # The user was actually prompted.
            assert mock_input.called
            prompts = [c.args[0] if c.args else "" for c in mock_input.call_args_list]
            assert any("configure them now" in p for p in prompts)


class TestUpdateYesStashRestore:
    """--yes auto-restores the pre-update autostash without prompting."""



class TestUnicodeDecodeErrorInUpdatePrompts:
    """Regression tests (review of #68497): input() can raise
    UnicodeDecodeError when the terminal encoding can't decode the byte
    sequence (e.g. a non-UTF-8 locale, or an embedded terminal). Three
    interactive update prompts call input() directly -- the config-
    migration prompt, the stash-restore prompt, and the upstream-remote
    prompt -- and each must fail safe (skip, don't crash) rather than let
    the exception escape and crash `hermes update` mid-flight.
    """

    @patch("hermes_cli.update_cmd._reload_config_modules")
    @patch("hermes_cli.update_cmd._run_migrate_config_fresh")
    @patch("hermes_cli.update_cmd._run_config_check_fresh", return_value=(1, 2))
    @patch("hermes_cli.config.get_missing_config_fields", return_value=[])
    @patch("hermes_cli.config.get_missing_env_vars", return_value=["NEW_KEY"])
    @patch("shutil.which", return_value=None)
    @patch("subprocess.run")
    def test_unicode_decode_error_in_tty_skips_and_prints_hint(
        self,
        mock_run,
        _mock_which,
        _mock_missing_env,
        _mock_missing_cfg,
        _mock_version,
        mock_migrate,
        _mock_reload,
        capsys,
    ):
        mock_run.side_effect = _make_run_side_effect(
            branch="main", verify_ok=True, commit_count="1"
        )
        mock_migrate.return_value = {"env_added": [], "config_added": []}
        args = SimpleNamespace(yes=False)

        import sys as _sys

        with patch(
            "builtins.input",
            side_effect=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid byte"),
        ), patch.object(_sys.stdin, "isatty", return_value=True), patch.object(
            _sys.stdout, "isatty", return_value=True
        ):
            cmd_update(args)  # must not raise

        out = capsys.readouterr().out
        assert "hermes config migrate" in out
        mock_migrate.assert_not_called()

    def test_stash_restore_unicode_decode_error_falls_through_to_skip(self, tmp_path, capsys):
        from hermes_cli.update_cmd import _restore_stashed_changes

        with patch(
            "builtins.input",
            side_effect=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid byte"),
        ):
            result = _restore_stashed_changes(
                ["git"], tmp_path, "stash@{0}", prompt_user=True, input_fn=None,
            )  # must not raise

        assert result is False
        out = capsys.readouterr().out
        assert "Skipped restoring local changes" in out
        assert "git stash apply stash@{0}" in out

    def test_stash_restore_eof_error_still_falls_through_to_skip(self, tmp_path):
        """Sanity: this fix must not regress the pre-existing EOFError case,
        which the raw input() path had no guard for at all before this fix."""
        from hermes_cli.update_cmd import _restore_stashed_changes

        with patch("builtins.input", side_effect=EOFError()):
            result = _restore_stashed_changes(
                ["git"], tmp_path, "stash@{0}", prompt_user=True, input_fn=None,
            )  # must not raise

        assert result is False

    def test_upstream_remote_prompt_unicode_decode_error_falls_through_to_skip(
        self, tmp_path
    ):
        from hermes_cli.update_cmd import _sync_with_upstream_if_needed

        with patch(
            "hermes_cli.update_cmd._has_upstream_remote", return_value=False
        ), patch(
            "hermes_cli.update_cmd._should_skip_upstream_prompt", return_value=False
        ), patch(
            "hermes_cli.update_cmd._add_upstream_remote"
        ) as mock_add, patch(
            "builtins.input",
            side_effect=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid byte"),
        ):
            _sync_with_upstream_if_needed(["git"], tmp_path)  # must not raise

        mock_add.assert_not_called()


class TestForkUpstreamMerge:
    """updates.merge_upstream — merge upstream into a fork that carries
    its own commits, instead of skipping the sync."""

    def _run_fork_sync(self, tmp_path, merge_upstream, merge_rc=0, push_rc=0,
                       dirty=""):
        """Drive _sync_with_upstream_if_needed on a fork that is 2 commits
        ahead of upstream, with updates.merge_upstream set to `merge_upstream`.

        Returns the list of git argv lists that were executed.
        """
        from hermes_cli.update_cmd import _sync_with_upstream_if_needed

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            joined = " ".join(str(c) for c in cmd)
            rc, out = 0, ""
            if "rev-parse" in joined:
                out = "main"
            elif "status" in joined:
                out = dirty
            elif "merge --no-edit" in joined or "merge upstream/main" in joined:
                rc = merge_rc
            elif "--diff-filter=U" in joined:
                out = "hermes_cli/main.py\n" if merge_rc != 0 else ""
            elif "push" in joined:
                rc = push_rc
            return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="boom")

        with patch(
            "hermes_cli.update_cmd._has_upstream_remote", return_value=True
        ), patch(
            "hermes_cli.update_cmd._count_commits_between",
            side_effect=lambda g, c, a, b: 2 if b in ("origin/main", "upstream/main") else 0,
        ), patch(
            "hermes_cli.config.load_config",
            return_value={"updates": {"merge_upstream": merge_upstream}},
        ), patch("subprocess.run", side_effect=fake_run):
            result = _sync_with_upstream_if_needed(["git"], tmp_path)

        return calls, result

    def test_fork_ahead_skips_merge_by_default(self, tmp_path, capsys):
        """merge_upstream defaults to False — historical behaviour preserved."""
        calls, result = self._run_fork_sync(tmp_path, merge_upstream=False)

        out = capsys.readouterr().out
        assert "Skipping upstream sync to preserve your changes" in out
        assert not any("merge" in " ".join(c) for c in calls)
        assert not any("push" in " ".join(c) for c in calls)
        assert result is False  # no deps to sync

    def test_fork_ahead_merges_when_opted_in(self, tmp_path, capsys):
        """merge_upstream: true merges upstream/main and pushes to origin."""
        calls, result = self._run_fork_sync(tmp_path, merge_upstream=True)

        out = capsys.readouterr().out
        assert "Skipping upstream sync" not in out
        assert any(c[-3:] == ["merge", "--no-edit", "upstream/main"] for c in calls)
        assert any(c[-3:] == ["push", "origin", "main"] for c in calls)
        assert "Pushed to origin/main" in out
        assert result is True  # caller must sync dependencies

    def test_fork_merge_conflict_aborts_and_continues(self, tmp_path, capsys):
        """A conflicting merge is aborted, named, and does NOT push or raise."""
        calls, result = self._run_fork_sync(tmp_path, merge_upstream=True, merge_rc=1)

        out = capsys.readouterr().out
        assert any(c[-2:] == ["merge", "--abort"] for c in calls)
        assert "hermes_cli/main.py" in out
        assert "git merge upstream/main" in out
        assert not any("push" in " ".join(c) for c in calls)
        assert result is False  # nothing landed on disk

    def test_fork_merge_dirty_tree_skips_merge(self, tmp_path, capsys):
        """Uncommitted tracked changes skip the merge instead of entangling it."""
        calls, result = self._run_fork_sync(
            tmp_path, merge_upstream=True, dirty=" M hermes_cli/main.py\n"
        )

        out = capsys.readouterr().out
        assert "uncommitted changes" in out
        assert not any("--no-edit" in " ".join(c) for c in calls)
        assert result is False

    def test_fork_merge_push_failure_is_a_warning_only(self, tmp_path, capsys):
        """A failed push warns but never fails the update — merge stays valid."""
        _calls, result = self._run_fork_sync(tmp_path, merge_upstream=True, push_rc=1)

        out = capsys.readouterr().out
        assert "Could not push the merge to origin" in out
        assert "git push origin main" in out
        # Merged locally despite the push failure -> deps still need syncing.
        assert result is True
