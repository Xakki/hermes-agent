"""Session-local prompt rebuild and workspace scope contracts."""
import threading

from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from tui_gateway import server


def test_persist_live_session_system_prompt_uses_profile_home(monkeypatch, tmp_path):
    """Issue #50233: _persist_live_session_system_prompt must re-bind
    HERMES_HOME to the session's profile before rebuilding the system
    prompt. Without this, a /model switch rebuilds the prompt with the
    root profile's SOUL.md and skills instead of the session's profile.
    """
    profile_home = tmp_path / "profile-work"
    profile_home.mkdir()
    (profile_home / "SOUL.md").write_text(
        "# Work persona\nYou are a work agent.", encoding="utf-8"
    )
    built_homes = []

    class FakeAgent:
        model = "test-model"
        provider = "test"
        _cached_system_prompt = None
        _session_db = None

        def _build_system_prompt(self, system_message=None):
            from hermes_constants import get_hermes_home
            home = get_hermes_home()
            built_homes.append(str(home))
            soul = (
                (home / "SOUL.md").read_text(encoding="utf-8-sig")
                if (home / "SOUL.md").exists()
                else ""
            )
            return f"System prompt from {home}\n{soul}"

    class FakeDB:
        def update_system_prompt(self, session_id, prompt):
            pass

    agent = FakeAgent()
    agent._session_db = FakeDB()
    session = {"agent": agent, "session_key": "test-key", "profile_home": str(profile_home)}
    server._persist_live_session_system_prompt(session)
    assert len(built_homes) == 1, f"expected 1 build, got {built_homes}"
    assert str(profile_home) in built_homes[0], f"system prompt built with wrong home: {built_homes[0]}"
    assert "Work persona" in agent._cached_system_prompt
    from hermes_constants import get_hermes_home_override
    assert get_hermes_home_override() is None


def test_persist_live_session_system_prompt_no_profile_is_unchanged(monkeypatch):
    """Sessions without a profile_home must not set/clear any override."""
    class FakeAgent:
        model = "test"
        _cached_system_prompt = None
        _session_db = None

        def _build_system_prompt(self, system_message=None):
            return "plain prompt"

    class FakeDB:
        def update_system_prompt(self, session_id, prompt):
            pass

    agent = FakeAgent()
    agent._session_db = FakeDB()
    session = {"agent": agent, "session_key": "test-key", "profile_home": None}
    server._persist_live_session_system_prompt(session)
    assert agent._cached_system_prompt == "plain prompt"


def test_persist_live_session_system_prompt_restores_pre_existing_override(tmp_path):
    """A nested prompt rebuild must restore the caller's ContextVar override."""
    from hermes_constants import get_hermes_home_override

    outer_home = tmp_path / "profile-outer"
    outer_home.mkdir()
    inner_home = tmp_path / "profile-inner"
    inner_home.mkdir()
    (inner_home / "SOUL.md").write_text(
        "# Inner persona\nYou are the inner agent.", encoding="utf-8"
    )
    built_homes = []

    class FakeAgent:
        model = "test-model"
        provider = "test"
        _cached_system_prompt = None
        _session_db = None

        def _build_system_prompt(self, system_message=None):
            from hermes_constants import get_hermes_home
            built_homes.append(str(get_hermes_home()))
            return "inner prompt"

    class FakeDB:
        def update_system_prompt(self, session_id, prompt):
            pass

    agent = FakeAgent()
    agent._session_db = FakeDB()
    session = {"agent": agent, "session_key": "test-key", "profile_home": str(inner_home)}
    outer_token = set_hermes_home_override(outer_home)
    try:
        server._persist_live_session_system_prompt(session)
        assert built_homes == [str(inner_home)]
        assert get_hermes_home_override() == str(outer_home)
    finally:
        reset_hermes_home_override(outer_token)
    assert get_hermes_home_override() is None


def test_persist_live_session_system_prompt_binds_session_cwd(monkeypatch, tmp_path):
    """A prompt rebuild on an RPC dispatcher binds the session cwd, not TERMINAL_CWD."""
    session_cwd = tmp_path / "project"
    session_cwd.mkdir()
    process_cwd = tmp_path / "home-fallback"
    process_cwd.mkdir()
    monkeypatch.setenv("TERMINAL_CWD", str(process_cwd))
    persisted = {}

    class FakeAgent:
        model = "test-model"
        provider = "test"
        session_id = "cwd-test-session"
        _cached_system_prompt = None
        _session_db = None

        def _build_system_prompt(self, system_message=None):
            from agent.runtime_cwd import resolve_agent_cwd
            return f"Current working directory: {resolve_agent_cwd()}"

    class FakeDB:
        def update_system_prompt(self, session_id, prompt):
            persisted["prompt"] = prompt

    agent = FakeAgent()
    agent._session_db = FakeDB()
    session = {"agent": agent, "session_key": "cwd-test-session", "cwd": str(session_cwd),
               "explicit_cwd": True, "profile_home": None}
    result = {}

    def dispatcher_thread():
        server._persist_live_session_system_prompt(session)
        result["cached"] = agent._cached_system_prompt

    thread = threading.Thread(target=dispatcher_thread)
    thread.start()
    thread.join()
    expected = f"Current working directory: {session_cwd}"
    assert result["cached"] == expected, result["cached"]
    assert persisted["prompt"] == expected, persisted["prompt"]


def test_workspace_move_rehomes_running_session(monkeypatch, tmp_path):
    """An explicit Move-to-project re-anchors the stored row and running session together."""
    target = "stored-running-session"
    new_cwd = tmp_path / "dest-project"
    new_cwd.mkdir()
    captured = {}

    class FakeDB:
        def get_session(self, session_id):
            return {"id": session_id}

        def update_session_cwd(self, session_id, cwd, branch=None, root=None, replace_git_meta=True):
            captured["row_update"] = (session_id, cwd)

        def close(self):
            pass

    import contextlib

    @contextlib.contextmanager
    def _fake_db(_params, *, writer=False):
        yield FakeDB()

    monkeypatch.setattr(server, "_profile_db", _fake_db)
    monkeypatch.setattr(server.git_probe, "branch", lambda cwd: "main")
    monkeypatch.setattr(server.git_probe, "common_repo_root", lambda cwd: str(new_cwd))
    live = {"session_key": target, "running": True, "cwd": str(tmp_path / "old-project")}
    monkeypatch.setitem(server._sessions, "live-sid", live)
    monkeypatch.setattr(server, "_register_session_cwd", lambda _session: None)
    response = server._methods["session.workspace.move"]("rid", {"session_key": target, "cwd": str(new_cwd)})
    assert "error" not in response, response
    assert captured["row_update"] == (target, str(new_cwd))
    assert live["cwd"] == str(new_cwd)
    assert live.get("explicit_cwd") is True
