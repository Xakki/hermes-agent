"""Real session.create and after-turn paths; only external transports/builds are fixtures."""
from types import SimpleNamespace

import pytest

from hermes_state import SessionDB
from tui_gateway import server, server_requests
from tui_gateway import interaction_host


@pytest.fixture
def native_session(tmp_path, monkeypatch):
    db = SessionDB(db_path=tmp_path / "entry.db")
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "_schedule_agent_build", lambda sid: None)
    monkeypatch.setattr(server, "_schedule_session_cap_enforcement", lambda: None)
    monkeypatch.setattr(server, "_register_session_cwd", lambda session: None)
    owner = SimpleNamespace(auth_identity={"provider": "basic", "user_id": "fixture-owner"}, write=lambda frame: True)
    token = server.bind_transport(owner)
    try:
        created = server.handle_request({"id": "create", "method": "session.create",
                                        "params": {"cols": 96, "source": "desktop"}})
        assert "result" in created, created
        sid = created["result"]["session_id"]
        session = server._sessions[sid]
        session["history"] = [{"role": "user", "content": "task"}, {"role": "assistant", "content": "done"}]
        session["agent"] = SimpleNamespace(session_id=session["session_key"], _cached_system_prompt="stable")
        yield sid, session, owner
    finally:
        if "sid" in locals():
            host = session.get("interaction_host")
            if host:
                host.invalidate()
            thread = session.get("interaction_chooser_thread")
            if thread:
                thread.join(10)
                assert not thread.is_alive()
            server._sessions.pop(sid, None)
        server_requests.reset_for_tests()
        server.reset_transport(token)
        db.close()


def test_created_session_post_turn_waits_for_actual_goal_loop_and_native_dependency_drain(native_session, monkeypatch):
    from hermes_cli.goals import GoalManager
    from hermes_cli.loops import LoopManager
    from hermes_cli import goals
    sid, session, _owner = native_session
    background = []
    delegated = [True]
    monkeypatch.setattr(server, "_session_has_active_delegations", lambda *args: delegated[0])
    monkeypatch.setattr(goals, "gather_background_processes", lambda **kwargs: background)
    monkeypatch.setattr(server, "_drain_queued_prompt", lambda *args: False)
    monkeypatch.setattr(server, "_session_owns_notification_event", lambda *args: False)
    result = {"final_response": "done"}
    def follow():
        server._run_post_turn_followups(1, sid, session, result, None)
    follow()
    delegated[0] = False
    background.append("fixture-process")
    follow()
    background.clear()
    with server._session_profile_runtime_scope(session, hydrate_secrets=False):
        goal = GoalManager(session_id=session["session_key"])
        goal.set("accepted")
        follow()
        goal.pause(reason="fixture human pause")
        loop = LoopManager(session_id=session["session_key"])
        loop.set("repeat", interval_seconds=60)
        follow()
        loop.pause()
    assert "interaction_host" not in session
    frames = []
    def answer(frame):
        frames.append(frame)
        assert server_requests.resolve_response({"id": frame["id"], "result": {"answer": "stop"}})
    server_requests.bind_sinks(answer, lambda *args: None, lambda sid: True)
    follow()
    thread = session["interaction_chooser_thread"]
    thread.join(10)
    assert not thread.is_alive()
    follow()
    assert len(frames) == 1
    assert session["history"] == [{"role": "user", "content": "task"}, {"role": "assistant", "content": "done"}]
    assert session["agent"]._cached_system_prompt == "stable"
    session["_closing"] = True
    session["history_version"] += 1
    follow()
    assert len(frames) == 1


@pytest.mark.parametrize("revoke", [False, True, "creator"])
def test_created_session_native_once_authenticates_owner_and_cancels_stale_request(native_session, monkeypatch, revoke):
    from hermes_cli.goals import GoalManager
    from hermes_cli import interaction_decider
    sid, session, owner = native_session
    monkeypatch.setattr(server, "_session_has_active_delegations", lambda *args: False)
    from hermes_cli import goals
    monkeypatch.setattr(goals, "gather_background_processes", lambda **kwargs: [])
    with server._session_profile_runtime_scope(session, hydrate_secrets=False):
        manager = GoalManager(session_id=session["session_key"])
        manager.set("accepted")
        manager.pause(reason="fixture human pause")
    host = interaction_host.get_host(sid, session)
    monkeypatch.setattr(interaction_decider, "decide", lambda lifecycle, token: lifecycle.machine_result(token, "continue", {"actor": "user", "provider": "fixture"}))
    submitted = []
    monkeypatch.setattr(server, "_run_prompt_submit", lambda *args, **kwargs: submitted.append((args, kwargs)))
    frames = []
    logical = []
    def answer(frame):
        frames.append(frame)
        native = server_requests._open[frame["id"]]
        logical.append((native.logical_request, native.reminder_at, native.deadline))
        choices = frame["params"]["choices"]
        assert "Once" in choices
        value = "Once"
        foreign = SimpleNamespace(auth_identity={"provider": "basic", "user_id": "foreign"})
        foreign_token = server.bind_transport(foreign)
        try:
            assert not server_requests.resolve_response({"id": frame["id"], "result": {"answer": value, "actor": "user"}})
        finally:
            server.reset_transport(foreign_token)
        if revoke:
            generation = host.lifecycle.generation
            if revoke == "creator":
                owner.auth_identity = {"provider": "basic", "user_id": "replacement"}
                session["auth_user_id"] = server._transport_auth_user_id(owner)
                assert not server_requests.resolve_response({"id": frame["id"], "result": {"answer": value}})
            host.invalidate()
            assert not server_requests.resolve_response({"id": frame["id"], "result": {"answer": value}})
            assert host.lifecycle.generation > generation
        else:
            assert all(part in frame["params"]["question"] for part in ("accepted", "solver", "600s"))
            assert server_requests.resolve_response({"id": frame["id"], "result": {"answer": value}})
            assert not server_requests.resolve_response({"id": frame["id"], "result": {"answer": value}})

    server_requests.bind_sinks(answer, lambda *args: None, lambda sid: True)
    interaction_host.maybe_completion(sid, session, {"final_response": "done"})
    thread = session["interaction_chooser_thread"]
    thread.join(10)
    assert not thread.is_alive()
    assert len({row[0] for row in logical}) == 1 and len({row[1] for row in logical}) == 1
    assert max(row[2] for row in logical) <= logical[0][2] + 0.1
    assert host.pending is None and host.lifecycle.mode == "assisted"
    if revoke:
        assert host.lifecycle._grant is None and not any(row["actor"] == "agent" for row in host.lifecycle.journal)
        assert len(frames) == 1 and not submitted
    else:
        assert host.lifecycle._grant is None and len(submitted) == 1
        assert submitted[0][0][3] == host.action_prompt("continue")
        assert submitted[0][1] == {"queued_prompt_generation": session.get("_queued_prompt_generation", 0)}
        assert any(row["event"] == "decision" and row["actor"] == "agent" for row in host.lifecycle.journal)
    assert not server_requests.open_requests(sid)


@pytest.mark.parametrize("computed", [False, True])
def test_created_session_new_task_uses_native_auto_and_preserves_discovery_governance(native_session, monkeypatch, computed):
    from hermes_cli.goals import GoalManager
    from hermes_cli import goals, interaction_decider
    sid, session, _owner = native_session
    monkeypatch.setattr(server, "_session_has_active_delegations", lambda *args: False)
    monkeypatch.setattr(goals, "gather_background_processes", lambda **kwargs: [])
    with server._session_profile_runtime_scope(session, hydrate_secrets=False):
        manager = GoalManager(session_id=session["session_key"])
        manager.set("accepted")
        manager.pause(reason="fixture human pause")
    host = interaction_host.get_host(sid, session)
    decisions = []
    def decide(lifecycle, token):
        decisions.append(token)
        if computed:
            return lifecycle.machine_result(token, "new_task", {"provider": "synthetic"})
        # A failed bounded computation must not fabricate a choice or ask again.
        return lifecycle.machine_failed(token) and False
    monkeypatch.setattr(interaction_decider, "decide", decide)
    from tools import approval
    monkeypatch.setattr(approval, "_YOLO_MODE_FROZEN", False)
    approval.enable_session_yolo(session["session_key"])
    frames, handoffs = [], []
    def answer(frame):
        frames.append(frame)
        pytest.fail("AUTO must not ask")
    server_requests.bind_sinks(answer, lambda *args: None, lambda sid: True)
    monkeypatch.setattr(server, "_run_prompt_submit", lambda *args, **kwargs: handoffs.append((args[3], kwargs)))
    def complete():
        interaction_host.maybe_completion(sid, session, {"final_response": "done"})
        thread = session["interaction_chooser_thread"]
        thread.join(10)
        assert not thread.is_alive()
    try:
        complete()
        assert len(decisions) == 1 and not frames
        assert host.lifecycle._grant is None and host.lifecycle.mode == "autonomous"
        if computed:
            assert handoffs == [(host.action_prompt("new_task"), {"queued_prompt_generation": session.get("_queued_prompt_generation", 0)})]
            assert "todo, grooming, freeze" in handoffs[0][0]
            assert "not promotion or authorization" in handoffs[0][0]
        else:
            assert not handoffs
            assert any(row["event"] == "decision_failed" for row in host.lifecycle.journal)
        assert host.pending is None and not server_requests.open_requests(sid)
        assert session["agent"]._cached_system_prompt == "stable"
    finally:
        approval.disable_session_yolo(session["session_key"])
