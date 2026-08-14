from pathlib import Path

from agent.claude_project_permissions import load_claude_project_permissions


def _enable(monkeypatch, snapshots):
    monkeypatch.setattr(
        "hermes_cli.config.load_config_readonly",
        lambda: {
            "permissions": {
                "claude_project_snapshot": True,
                "claude_project_denies": snapshots,
            }
        },
    )


def test_snapshot_is_scoped_from_cwd_to_git_root(tmp_path, monkeypatch):
    project = tmp_path / "project"
    nested = project / "src" / "pkg"
    nested.mkdir(parents=True)
    (project / ".git").mkdir()
    _enable(monkeypatch, {str(project.resolve()): ["sudo *", "curl *"]})

    allowed, denied = load_claude_project_permissions(nested)

    assert allowed == set()
    assert denied == {"sudo *", "curl *"}


def test_snapshot_never_grants_project_allow(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    _enable(
        monkeypatch,
        {
            str(project.resolve()): {
                "allow": ["pytest *"],
                "deny": ["sudo *"],
            }
        },
    )

    assert load_claude_project_permissions(project) == (set(), set())


def test_no_git_uses_only_exact_cwd_snapshot(tmp_path, monkeypatch):
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    _enable(monkeypatch, {str(parent.resolve()): ["parent-only *"]})

    assert load_claude_project_permissions(child) == (set(), set())


def test_snapshot_switching_a_b_a_has_no_cross_session_cache(tmp_path, monkeypatch):
    project_a = tmp_path / "a"
    project_b = tmp_path / "b"
    project_a.mkdir()
    project_b.mkdir()
    _enable(
        monkeypatch,
        {
            str(project_a.resolve()): ["deny-a *"],
            str(project_b.resolve()): ["deny-b *"],
        },
    )

    assert load_claude_project_permissions(project_a)[1] == {"deny-a *"}
    assert load_claude_project_permissions(project_b)[1] == {"deny-b *"}
    assert load_claude_project_permissions(project_a)[1] == {"deny-a *"}


def test_snapshot_rejects_relative_keys_and_non_string_patterns(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    _enable(
        monkeypatch,
        {
            "relative/project": ["bad *"],
            str(project.resolve()): ["safe *", 123, "", None],
        },
    )

    assert load_claude_project_permissions(project) == (set(), {"safe *"})


def test_snapshot_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr("hermes_cli.config.load_config_readonly", lambda: {})
    assert load_claude_project_permissions(Path(tmp_path)) == (set(), set())
