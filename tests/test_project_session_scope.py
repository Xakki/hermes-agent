"""Project ownership is durable and filters session surfaces."""

from pathlib import Path


def test_project_root_is_persisted_and_filters_lists(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from hermes_state import SessionDB

    db = SessionDB(db_path=Path(tmp_path) / "state.db")
    try:
        db.create_session("a", source="cli", cwd="/work/a", project_root="/work/a")
        db.create_session("b", source="cli", cwd="/work/b", project_root="/work/b")
        db.create_session("legacy", source="cli", cwd="/work/a")
        with db._lock:
            db._conn.execute("UPDATE sessions SET project_root = NULL WHERE id = 'legacy'")
            db._conn.commit()

        assert db.get_session("a")["project_root"] == "/work/a"
        assert [row["id"] for row in db.list_sessions_rich(project_root="/work/a")] == ["a"]
        assert [row["id"] for row in db.search_sessions(project_root="/work/a")] == ["a"]
    finally:
        db.close()


def test_parent_session_inherits_project_root(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from hermes_state import SessionDB

    db = SessionDB(db_path=Path(tmp_path) / "state.db")
    try:
        db.create_session("parent", source="cli", project_root="/work/a")
        db.create_session("child", source="cli", parent_session_id="parent")
        assert db.get_session("child")["project_root"] == "/work/a"
    finally:
        db.close()


def test_existing_session_project_root_cannot_be_overridden(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from hermes_state import SessionDB

    db = SessionDB(db_path=Path(tmp_path) / "state.db")
    try:
        db.create_session("existing", source="cli", project_root="/work/a")
        db.create_session("existing", source="cli", cwd="/work/b", project_root="/work/b")
        assert db.get_session("existing")["project_root"] == "/work/a"
    finally:
        db.close()


def test_legacy_import_without_project_root_stays_unowned(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from hermes_state import SessionDB

    db = SessionDB(db_path=Path(tmp_path) / "state.db")
    try:
        result = db.import_sessions([{
            "id": "legacy-import",
            "source": "cli",
            "cwd": "/work/a",
            "git_repo_root": "/work/a",
            "messages": [],
        }])
        assert result["imported"] == 1
        assert db.get_session("legacy-import")["project_root"] is None
    finally:
        db.close()
