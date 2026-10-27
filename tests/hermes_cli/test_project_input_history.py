"""Project isolation through the real classic input area and resume lifecycle."""
from prompt_toolkit.history import FileHistory
import pytest

import cli as cli_module
from cli import HermesCLI


def _make_cli(monkeypatch):
    cli = HermesCLI.__new__(HermesCLI)
    cli._init_session_store = lambda: None
    cli._init_ui_state = lambda: None
    cli._write_terminal_breadcrumb = lambda: None
    cli._command_blocks_input = False
    cli._tui_on_text_changed = lambda buf: None
    cli._tui_placeholder_text = lambda: ""
    monkeypatch.setattr(cli_module, "get_skill_commands", dict)
    monkeypatch.setattr(cli_module, "get_skill_bundles", dict)
    cli._init_runtime_state(None)
    area = cli._tui_build_input_area()
    return cli, area.buffer


async def _load(buffer):
    buffer.load_history_if_not_yet_loaded()
    await buffer._load_history_task


@pytest.mark.asyncio
async def test_startup_ignores_mixed_legacy_and_new_sessions_share_only_their_project(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(cli_module, "_hermes_home", home)
    legacy = home / ".hermes_history"
    original = "\n# synthetic timestamp\n+foreign legacy input\n"
    legacy.write_text(original)
    project_a, project_b = tmp_path / "a", tmp_path / "b"
    project_a.mkdir()
    project_b.mkdir()
    monkeypatch.chdir(project_a)
    cli_a, buffer_a = _make_cli(monkeypatch)
    await _load(buffer_a)
    buffer_a.history_backward()
    assert buffer_a.text == ""
    buffer_a.text = "project A first line\n\nlast line"
    buffer_a.append_to_history()

    same_cli, same_buffer = _make_cli(monkeypatch)
    await _load(same_buffer)
    same_buffer.history_backward()
    assert same_buffer.text == "project A first line\n\nlast line"
    assert same_cli._history_file == cli_a._history_file

    monkeypatch.chdir(project_b)
    cli_b, buffer_b = _make_cli(monkeypatch)
    await _load(buffer_b)
    buffer_b.history_backward()
    assert buffer_b.text == ""
    assert cli_b._history_file != cli_a._history_file
    assert legacy.read_text() == original


@pytest.mark.asyncio
async def test_resume_rebinds_the_mounted_input_buffer_and_resets_navigation(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(cli_module, "_hermes_home", home)
    home.mkdir()
    project_a, project_b = tmp_path / "a", tmp_path / "b"
    project_a.mkdir()
    project_b.mkdir()
    monkeypatch.chdir(project_a)
    cli, buffer = _make_cli(monkeypatch)
    file_a = cli._history_file
    buffer.text = "project A input"
    buffer.append_to_history()
    await _load(buffer)
    buffer.reset()
    await _load(buffer)
    buffer.history_backward()
    assert buffer.text == "project A input"

    cli._restore_session_cwd({"cwd": str(project_b)}, quiet=True)
    assert cli._history_file != file_a
    assert isinstance(buffer.history, FileHistory)
    assert str(buffer.history.filename) == str(cli._history_file)
    assert buffer.text == ""
    await _load(buffer)
    buffer.history_backward()
    assert buffer.text == ""
    buffer.text = "project B input"
    buffer.append_to_history()

    cli._restore_session_cwd({"cwd": str(project_a)}, quiet=True)
    await _load(buffer)
    buffer.history_backward()
    assert buffer.text == "project A input"
    buffer.history_forward()
    assert buffer.text == ""
    old_history = buffer.history
    buffer.text = "same-project draft"
    cli._restore_session_cwd({"cwd": str(project_a)}, quiet=True)
    assert buffer.history is old_history
    assert buffer.text == "same-project draft"
