from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb


@pytest.fixture
def kanban_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def test_block_kind_is_required_only_while_blocked(kanban_home: Path) -> None:
    with kb.connect_closing() as conn:
        task_id = kb.create_task(conn, title="typed blocker")
        with pytest.raises(ValueError, match="block kind"):
            kb.block_task(conn, task_id, reason="need a decision")

        assert kb.block_task(conn, task_id, reason="need a decision", kind="human_input")
        assert kb.get_task(conn, task_id).block_kind == "human_input"
        assert kb.unblock_task(conn, task_id)
        assert kb.get_task(conn, task_id).block_kind is None


def test_initial_blocked_task_requires_canonical_kind(kanban_home: Path) -> None:
    with kb.connect_closing() as conn:
        with pytest.raises(ValueError, match="block_kind"):
            kb.create_task(conn, title="invalid", initial_status="blocked")
        task_id = kb.create_task(
            conn, title="external wait", initial_status="blocked", block_kind="external"
        )
        assert kb.get_task(conn, task_id).block_kind == "external"


def test_sqlite_constraint_rejects_null_block_kind_for_blocked_task(kanban_home: Path) -> None:
    with kb.connect_closing() as conn:
        task_id = kb.create_task(conn, title="constraint")
        with pytest.raises(Exception):
            conn.execute("UPDATE tasks SET status = 'blocked' WHERE id = ?", (task_id,))


def test_init_db_rebuilds_legacy_tasks_with_the_strict_constraint(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    task_schema = kb.SCHEMA_SQL[
        kb.SCHEMA_SQL.index("CREATE TABLE IF NOT EXISTS tasks ("):
        kb.SCHEMA_SQL.index("CREATE TABLE IF NOT EXISTS task_links (")
    ].replace("block_kind IS NOT NULL\n            AND ", "")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(task_schema)
        conn.execute(
            "INSERT INTO tasks (id, title, status, created_at) "
            "VALUES ('legacy', 'legacy blocker', 'blocked', 0)"
        )
    kb.init_db(db_path)
    with kb.connect_closing(db_path) as conn:
        task = kb.get_task(conn, "legacy")
        assert task is not None
        assert task.block_kind == "human_input"
        with pytest.raises(Exception):
            conn.execute("UPDATE tasks SET status = 'blocked', block_kind = NULL WHERE id = 'legacy'")


def test_nonblocked_transitions_clear_block_kind(kanban_home: Path) -> None:
    with kb.connect_closing() as conn:
        task_id = kb.create_task(conn, title="clear kind")
        assert kb.block_task(conn, task_id, reason="wait", kind="external")
        assert kb.schedule_task(conn, task_id)
        assert kb.get_task(conn, task_id).block_kind is None

        assert kb.unblock_task(conn, task_id)
        assert kb.block_task(conn, task_id, reason="wait", kind="dependency")
        ok, error = kb.promote_task(conn, task_id, actor="tester")
        assert (ok, error) == (True, None)
        assert kb.get_task(conn, task_id).block_kind is None


def test_failure_circuit_breaker_uses_external_block_kind(kanban_home: Path) -> None:
    with kb.connect_closing() as conn:
        task_id = kb.create_task(conn, title="failure kind")
        assert kb._record_task_failure(
            conn, task_id, "worker unavailable", outcome="crashed", failure_limit=1
        )
        task = kb.get_task(conn, task_id)
        assert task is not None
        assert task.status == "blocked"
        assert task.block_kind == "external"
