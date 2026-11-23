"""Workspace identity and layout behavior in the CLI status bar."""

from datetime import datetime, timedelta

import pytest

from cli import HermesCLI
from hermes_cli import status_bar_git
from hermes_cli.gateway_connection import GatewayConnection
from hermes_cli.gateway_session_store import GatewaySessionStore
from hermes_cli.status_bar_git import current_git_branch


def _make_cli():
    cli_obj = HermesCLI.__new__(HermesCLI)
    cli_obj.model = "anthropic/claude-sonnet-4-20250514"
    cli_obj.session_start = datetime.now() - timedelta(minutes=3)
    cli_obj.conversation_history = [{"role": "user", "content": "hi"}]
    cli_obj.agent = None
    return cli_obj


def test_current_git_branch_reads_head_and_worktree_pointer(tmp_path):
    """Branch resolves from .git/HEAD in a plain repo AND through a gitdir: pointer
    (worktree layout); a non-repo dir yields ''."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/minimax-inspired/status-bar-git-branch\n")
    status_bar_git._cache.clear()
    assert current_git_branch(str(repo)) == "minimax-inspired/status-bar-git-branch"

    # Worktree: .git is a file pointing at a private git dir with its own HEAD.
    private = tmp_path / "gitdir-store"
    private.mkdir()
    (private / "HEAD").write_text("ref: refs/heads/feature-x\n")
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {private}\n")
    status_bar_git._cache.clear()
    assert current_git_branch(str(wt)) == "feature-x"

    plain = tmp_path / "plain"
    plain.mkdir()
    status_bar_git._cache.clear()
    assert current_git_branch(str(plain)) == ""


@pytest.mark.parametrize("modern", [False, True])
def test_status_bar_uses_session_cwd_and_preserves_fields(monkeypatch, tmp_path, modern):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/session-branch\n")
    nested = repo / "session-subdir"
    nested.mkdir()
    launch = tmp_path / "launch-folder"
    launch.mkdir()
    monkeypatch.chdir(launch)
    monkeypatch.setenv("TERMINAL_CWD", str(launch))

    cli_obj = _make_cli()
    cli_obj._status_bar_visible = True
    cli_obj._modern_layout = modern
    cli_obj._status_bar_field_set_cache = None
    cli_obj.session_id = "session"
    cli_obj._pending_title = "named session"
    connection = GatewayConnection()
    cli_obj._session_db = GatewaySessionStore(connection)
    connection.state(cli_obj.session_id).adopt_snapshot({"info": {"cwd": str(nested), "terminal_backend": "local"}})

    def render(width):
        monkeypatch.setattr(cli_obj, "_get_tui_terminal_width", lambda: width)
        return "".join(text for _, text in cli_obj._get_status_bar_fragments())

    wide = render(160)
    assert "session-subdir (session-branch)" in wide
    assert "launch-folder" not in wide
    assert "claude-sonnet" in wide
    narrow = render(32)
    assert "claude-sonnet" in narrow
    assert "session-branch" not in narrow
    assert cli_obj._status_bar_display_width(narrow) <= 32

    cli_obj._status_bar_field_set_cache = frozenset({"model", "git_branch"})
    branch_only = render(160)
    assert "⎇ session-branch" in branch_only
    assert "session-subdir" not in branch_only


def test_branch_refreshes_and_omits_detached_head(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    head = tmp_path / ".git" / "HEAD"
    head.write_text("ref: refs/heads/first\n")
    now = 100.0
    monkeypatch.setattr(status_bar_git.time, "monotonic", lambda: now)
    assert current_git_branch(str(tmp_path)) == "first"
    head.write_text("a" * 40 + "\n")
    assert current_git_branch(str(tmp_path)) == "first"
    now += 6.0
    assert current_git_branch(str(tmp_path)) == ""
