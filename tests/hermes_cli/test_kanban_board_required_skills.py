from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb


@pytest.fixture
def fresh_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    for var in (
        "HERMES_KANBAN_DB",
        "HERMES_KANBAN_WORKSPACES_ROOT",
        "HERMES_KANBAN_HOME",
        "HERMES_KANBAN_BOARD",
    ):
        monkeypatch.delenv(var, raising=False)
    kb._INITIALIZED_PATHS.clear()
    return home


def test_board_required_skills_round_trip(fresh_home: Path) -> None:
    kb.create_board("compact")
    meta = kb.write_board_metadata(
        "compact",
        required_skills=["kanban-comment-compaction", "ai-agents-skills:abbreviations"],
    )

    assert meta["required_skills"] == [
        "kanban-comment-compaction",
        "ai-agents-skills:abbreviations",
    ]
    assert kb.read_board_metadata("compact")["required_skills"] == meta["required_skills"]


def test_board_required_skills_are_force_loaded_for_existing_task(
    fresh_home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kb.create_board("compact")
    kb.write_board_metadata(
        "compact",
        required_skills=["kanban-comment-compaction", "ai-agents-skills:abbreviations"],
    )
    with kb.connect_closing(board="compact") as conn:
        task_id = kb.create_task(conn, title="existing", assignee="worker", board="compact")
        task = kb.get_task(conn, task_id)
    assert task is not None
    assert task.skills is None

    captured: dict[str, list[str]] = {}

    class FakeProc:
        pid = 42

    def fake_popen(cmd: list[str], **_kwargs: object) -> FakeProc:
        captured["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(kb, "_resolve_hermes_argv", lambda: ["hermes"])
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    assert kb._default_spawn(task, str(workspace), board="compact") == 42
    pairs = [
        captured["cmd"][idx + 1]
        for idx, value in enumerate(captured["cmd"][:-1])
        if value == "--skills"
    ]
    assert pairs == ["kanban-comment-compaction", "ai-agents-skills:abbreviations"]


def test_task_skills_extend_board_required_skills_without_duplicates(
    fresh_home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kb.create_board("compact")
    kb.write_board_metadata("compact", required_skills=["kanban-comment-compaction"])
    with kb.connect_closing(board="compact") as conn:
        task_id = kb.create_task(
            conn,
            title="task skills",
            assignee="worker",
            skills=["kanban-comment-compaction", "ai-agents-skills:abbreviations"],
            board="compact",
        )
        task = kb.get_task(conn, task_id)
    assert task is not None

    captured: dict[str, list[str]] = {}

    class FakeProc:
        pid = 43

    def fake_popen(cmd: list[str], **_kwargs: object) -> FakeProc:
        captured["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(kb, "_resolve_hermes_argv", lambda: ["hermes"])
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    assert kb._default_spawn(task, str(workspace), board="compact") == 43
    pairs = [
        captured["cmd"][idx + 1]
        for idx, value in enumerate(captured["cmd"][:-1])
        if value == "--skills"
    ]
    assert pairs == ["kanban-comment-compaction", "ai-agents-skills:abbreviations"]
