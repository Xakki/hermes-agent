import pytest


def test_tui_bootstrap_ignores_caller_project_root(monkeypatch):
    pytest.importorskip("dotenv")
    from tui_gateway import server

    monkeypatch.setattr(server, "_git_common_repo_root_for_cwd", lambda path: "/trusted")
    monkeypatch.chdir("/")
    assert server._active_project_root({"project_root": "/attacker"}, None) == "/trusted"


def test_tui_current_session_id_requires_transport_bound_session(monkeypatch):
    from tui_gateway import server
    from tui_gateway.transport import bind_transport, reset_transport

    class _DB:
        def get_session(self, session_id):
            return {"project_root": "/attacker"} if session_id == "spoofed" else None

    transport = object()
    previous = dict(server._sessions)
    server._sessions.clear()
    server._sessions["real"] = {"project_root": "/trusted", "transport": transport}
    token = bind_transport(transport)
    try:
        assert server._active_project_root({"current_session_id": "spoofed"}, _DB()) is None
        assert server._active_project_root({"current_session_id": "real"}, _DB()) == "/trusted"
    finally:
        reset_transport(token)
        server._sessions.clear()
        server._sessions.update(previous)


def test_session_empty_aggregates_are_project_scoped(tmp_path):
    from hermes_state import SessionDB

    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        for session_id, root in (("owned", "/trusted"), ("foreign", "/other")):
            db.create_session(session_id, source="cli", project_root=root)
            db.end_session(session_id, end_reason="done")
        assert db.count_empty_sessions(project_root="/trusted") == 1
        assert db.delete_empty_sessions(project_root="/trusted") == 1
        assert db.get_session("owned") is None
        assert db.get_session("foreign") is not None
    finally:
        db.close()


def test_scoped_import_rebinds_caller_selected_project(tmp_path):
    from hermes_state import SessionDB

    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        result = db.import_sessions(
            [{
                "id": "imported",
                "project_root": "/attacker",
                "messages": [{"role": "user", "content": "secret"}],
            }],
            project_root="/trusted",
        )
        assert result["imported"] == 1
        assert db.get_session("imported")["project_root"] == "/trusted"
    finally:
        db.close()
