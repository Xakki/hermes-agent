"""One canonical project identity for CLI files and TUI metadata."""
from hashlib import sha256
import subprocess

import pytest

from hermes_cli.input_history import project_history_file
from tui_gateway import git_probe


def test_real_git_repository_and_nested_launch_share_a_key_but_other_projects_do_not(tmp_path, monkeypatch):
    root, other = tmp_path / "repo", tmp_path / "other"
    root.mkdir()
    other.mkdir()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True, capture_output=True)
    assert (root / ".git").is_dir()
    nested = root / "src" / "nested"
    nested.mkdir(parents=True)
    key = git_probe.input_history_key(str(root))
    assert key == sha256(str(root.resolve()).encode("utf-8")).hexdigest()
    assert git_probe.input_history_key(str(nested)) == key
    assert git_probe.input_history_key(str(other)) != key
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    assert project_history_file(str(nested)) == home / "input-history" / key
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "other-home"))
    assert project_history_file(str(root)) != home / "input-history" / key


@pytest.mark.platforms("posix")
def test_symlinks_share_identity_and_non_git_directories_remain_separate(tmp_path):
    root, other = tmp_path / "folder", tmp_path / "other"
    root.mkdir()
    other.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    assert git_probe.input_history_key(str(alias)) == git_probe.input_history_key(str(root))
    assert git_probe.input_history_key(str(other)) != git_probe.input_history_key(str(root))
    assert git_probe.input_history_key(None) is None
    assert git_probe.input_history_key("") is None


def test_cli_file_is_readable_by_tui_and_preserves_human_marker_format(tmp_path, monkeypatch):
    from prompt_toolkit.history import FileHistory

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    file = project_history_file(str(tmp_path))
    file.write_text("\n# synthetic timestamp\n# hermes-input-source: human\n+TUI first line\n+\n+TUI last line\n")
    history = FileHistory(str(file))
    assert list(history.load_history_strings()) == ["TUI first line\n\nTUI last line"]
    history.append_string("CLI first line\n\nCLI last line")
    reloaded = FileHistory(str(file))
    assert list(reloaded.load_history_strings()) == ["CLI first line\n\nCLI last line", "TUI first line\n\nTUI last line"]
