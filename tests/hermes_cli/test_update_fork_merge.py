"""Fork updater integration keeps opt-in merge and non-destructive refusal contracts."""
from __future__ import annotations

import subprocess

import pytest

from hermes_cli import config, update_cmd, update_custody
from hermes_cli.update_cmd_git import _sync_with_upstream_if_needed


def run_sync(monkeypatch, tmp_path, *, enabled=False, merge_rc=0, push_rc=0, dirty="", branch="main"):
    calls = []

    def fake_git(git_cmd, args, cwd, **kwargs):
        calls.append([*git_cmd, *args])
        rc, out = 0, ""
        if args[0] == "rev-parse":
            out = branch if "--abbrev-ref" in args else "a" * 40
        elif args[0] == "status":
            out = dirty
        elif args[:2] == ["merge", "--no-edit"]:
            rc = merge_rc
        elif "--diff-filter=U" in args:
            out = "hermes_cli/main.py\n" if merge_rc else ""
        elif args[0] == "push":
            rc = push_rc
        return subprocess.CompletedProcess([*git_cmd, *args], rc, stdout=out, stderr="synthetic")

    monkeypatch.setattr(update_cmd, "_git_run", fake_git)
    monkeypatch.setattr(update_cmd, "_has_upstream_remote", lambda *args: True)
    monkeypatch.setattr(update_cmd, "_count_commits_between", lambda *args: 2)
    monkeypatch.setattr(config, "load_config", lambda: {"updates": {"merge_upstream": enabled}})
    monkeypatch.setattr(update_custody, "run_git",
                        lambda git_cmd, args, **kwargs: fake_git(git_cmd, args, kwargs["cwd"]))
    result = _sync_with_upstream_if_needed(["git"], tmp_path)
    return calls, result


def test_fork_ahead_skips_merge_by_default(monkeypatch, tmp_path, capsys):
    calls, result = run_sync(monkeypatch, tmp_path)
    assert "Skipping upstream sync to preserve your changes" in capsys.readouterr().out
    assert not any(call[1] in {"merge", "push"} for call in calls)
    assert result is False


def test_fork_ahead_merges_when_opted_in(monkeypatch, tmp_path, capsys):
    calls, result = run_sync(monkeypatch, tmp_path, enabled=True)
    assert ["git", "merge", "--no-edit", "upstream/main"] in calls
    assert ["git", "push", "origin", "main"] in calls
    assert "Pushed to origin/main" in capsys.readouterr().out
    assert result is True


def test_fork_merge_conflict_aborts_and_continues(monkeypatch, tmp_path, capsys):
    calls, result = run_sync(monkeypatch, tmp_path, enabled=True, merge_rc=1)
    output = capsys.readouterr().out
    assert ["git", "merge", "--abort"] in calls
    assert "hermes_cli/main.py" in output and "git merge upstream/main" in output
    assert not any(call[1] == "push" for call in calls)
    assert result is False


@pytest.mark.parametrize("dirty,branch", [(" M hermes_cli/main.py\n", "main"), ("", "feature")])
def test_fork_merge_refuses_dirty_or_non_main_checkout(monkeypatch, tmp_path, dirty, branch):
    calls, result = run_sync(monkeypatch, tmp_path, enabled=True, dirty=dirty, branch=branch)
    assert not any(call[1] in {"merge", "push"} for call in calls)
    assert result is False


def test_fork_merge_push_failure_is_a_warning_only(monkeypatch, tmp_path, capsys):
    _, result = run_sync(monkeypatch, tmp_path, enabled=True, push_rc=1)
    output = capsys.readouterr().out
    assert "Could not push the merge to origin" in output and "git push origin main" in output
    assert result is True
