"""Project authority survives upstream bulk dashboard query paths."""
import pytest


@pytest.mark.parametrize("surface", ["single", "profiles"])
def test_bulk_dashboard_lists_only_owned_project(monkeypatch, tmp_path, surface):
    from hermes_state import SessionDB
    from hermes_cli.web_routers import profiles, sessions

    db = SessionDB(db_path=tmp_path / "state.db")
    for sid, root in (("owned", "/owned"), ("foreign", "/foreign")):
        db.create_session(sid, source="cli", project_root=root)
    monkeypatch.setattr(sessions, "_active_project_root", lambda profile: "/owned", raising=False)
    monkeypatch.setattr(sessions, "_open_session_db_for_profile", lambda *a, **kw: db)
    monkeypatch.setattr(profiles, "_trusted_active_project_root", lambda: "/owned", raising=False)
    monkeypatch.setattr(profiles, "_profile_targets", lambda *a: [("default", tmp_path)])
    monkeypatch.setattr(profiles, "_read_profile_db", lambda name, home, errors, read: read(db))
    try:
        result = (sessions.get_sessions(limit=20, offset=0, full=True) if surface == "single"
                  else profiles.get_profiles_sessions(limit=20, offset=0, full=True))
        assert [row["id"] for row in result["sessions"]] == ["owned"]
        assert result["total"] == 1
    finally:
        db.close()


def test_profile_pr_scan_never_reads_foreign_transcripts(monkeypatch, tmp_path):
    from hermes_cli.web_routers import profiles

    scanned = []

    class Store:
        def get_session(self, sid):
            return {"project_root": "/owned" if sid == "owned" else "/foreign"}

        def find_pr_url_messages(self, ids):
            scanned.extend(ids)
            return []

    monkeypatch.setattr(profiles, "_trusted_active_project_root", lambda: "/owned", raising=False)
    monkeypatch.setattr(profiles, "_scoped_profile_targets", lambda *a: [("default", tmp_path)])
    monkeypatch.setattr(profiles, "_read_profile_db", lambda name, home, errors, read: read(Store()))
    profiles.post_profiles_sessions_pull_requests(profiles.SessionPrScanBody(ids=["owned", "foreign"]))
    assert scanned == ["owned"]
