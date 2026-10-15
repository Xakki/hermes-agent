"""Real host entry points; native UI and inference transports are explicit fixtures."""
import threading
from types import SimpleNamespace

from tui_gateway import server, server_requests


def test_live_slash_grant_is_owner_only_and_interrupt_revokes(tmp_path, monkeypatch):
    from tui_gateway.transport import current_transport
    owner, foreign = object(), object()
    session = {"session_key": "fixture", "profile_home": str(tmp_path), "transport": owner,
               "history_lock": threading.RLock(), "running": False,
               "history": []}
    from hermes_cli.goals import GoalManager
    with server._session_profile_runtime_scope(session, hydrate_secrets=False):
        GoalManager(session_id="fixture").set("accepted goal")
    monkeypatch.setitem(server._sessions, "fixture", session)
    monkeypatch.setattr(server, "current_transport", lambda: foreign)
    denied = server._methods["slash.exec"](1, {"session_id": "fixture", "command": "/autonomy grant a 60 2"})
    assert "owner" in denied["result"]["output"]
    monkeypatch.setattr(server, "current_transport", lambda: owner)
    granted = server._methods["slash.exec"](2, {"session_id": "fixture", "command": "/autonomy grant a 60 2"})
    assert "granted" in granted["result"]["output"]
    host = session["interaction_host"]
    assert host.lifecycle.mode == "assisted"
    monkeypatch.setattr(server, "_clear_pending", lambda sid: None)
    server._interrupt_session_turn("fixture", session)
    assert host.lifecycle._grant is None
    assert current_transport() is None


def test_real_post_followups_native_chooser_once_and_input_priority(tmp_path, monkeypatch):
    from tui_gateway import interaction_host
    owner = SimpleNamespace(write=lambda frame: True)
    session = {"session_key": "fixture", "profile_home": str(tmp_path), "transport": owner,
               "history_lock": threading.RLock(), "running": False, "history_version": 3,
               "history": [{"role": "user", "content": "task"}, {"role": "assistant", "content": "done"}]}
    monkeypatch.setitem(server._sessions, "fixture", session)
    monkeypatch.setattr(interaction_host, "dependencies_pending", lambda *args: False)
    monkeypatch.setattr(server, "current_transport", lambda: session["transport"])
    monkeypatch.setattr(server, "_drain_queued_prompt", lambda *args: True)
    monkeypatch.setattr(server, "_session_owns_notification_event", lambda *args: False)
    server._run_post_turn_followups(1, "fixture", session, {"final_response": "done"}, None)
    assert "interaction_host" not in session
    monkeypatch.setattr(server, "_drain_queued_prompt", lambda *args: False)
    frames = []
    delivered = threading.Event()
    def answer(frame):
        frames.append(frame)
        server_requests.resolve_response({"id": frame["id"], "result": {"answer": "stop"}})
        delivered.set()
    server_requests.bind_sinks(answer, lambda *args: None, lambda sid: True)
    try:
        server._run_post_turn_followups(1, "fixture", session, {"final_response": "done"}, None)
        assert delivered.wait(30), "native chooser was not delivered"
        session["interaction_chooser_thread"].join(30)
        assert not session["interaction_chooser_thread"].is_alive()
        server._run_post_turn_followups(1, "fixture", session, {"final_response": "done"}, None)
        assert len(frames) == 1
        assert session["history"][-1]["content"] == "done"
        host = session["interaction_host"]
        assert host.stopped and host.pending is None and session["_turn_cancel_requested"]
        assert any(row["actor"] == "user" and row["event"] == "revoked" for row in host.lifecycle.journal)
    finally:
        server_requests.reset_for_tests()


def test_native_pending_once_and_actor_persistence_and_admission(tmp_path, monkeypatch):
    import json
    from tui_gateway import interaction_host
    from hermes_cli import interaction_decider
    owner = SimpleNamespace(write=lambda frame: True)
    session = {"session_key": "modal", "profile_home": str(tmp_path), "transport": owner,
               "history_lock": threading.RLock(), "running": False, "history_version": 1,
               "history": [{"role": "assistant", "content": "done"}]}
    monkeypatch.setitem(server._sessions, "modal", session)
    monkeypatch.setattr(server, "current_transport", lambda: session["transport"])
    monkeypatch.setattr(interaction_host, "dependencies_pending", lambda *args: False)
    monkeypatch.setattr(interaction_host, "accepted_goal", lambda session: "accepted")
    host = interaction_host.get_host("modal", session)
    def synthetic_decision(lifecycle, token):
        return lifecycle.machine_result(token, "continue", {"model": "synthetic", "provider": "fixture", "actor": "user"})
    monkeypatch.setattr(interaction_decider, "decide", synthetic_decision)
    submitted = []
    monkeypatch.setattr(server, "_run_prompt_submit", lambda *args, **kwargs: submitted.append((args, kwargs)))
    frames, deadlines = [], []
    def native_answer(frame):
        frames.append(frame)
        native = server_requests._open[frame["id"]]
        deadlines.append(native.deadline)
        choices = frame["params"]["choices"]
        assert "Once" in choices
        assert all(value in frame["params"]["question"] for value in ("accepted", "solver", "600s"))
        answer = "Once"
        assert server_requests.resolve_response({"id": frame["id"], "result": {"answer": answer}})
        assert not server_requests.resolve_response({"id": frame["id"], "result": {"answer": "stop"}})
    server_requests.bind_sinks(native_answer, lambda *args: None, lambda sid: True)
    try:
        interaction_host.maybe_completion("modal", session, {"final_response": "done"})
        session["interaction_chooser_thread"].join(30)
        assert not session["interaction_chooser_thread"].is_alive()
        assert len(frames) == 1 and len(deadlines) == 1 and len(submitted) == 1
        assert submitted[0][0][3] == host.action_prompt("continue")
        assert submitted[0][1] == {"queued_prompt_generation": session.get("_queued_prompt_generation", 0)}
        assert host.lifecycle.mode == "assisted" and host.lifecycle._grant is None
        with server._session_db(session) as db:
            rows = [json.loads(value) for _, value in db.list_meta_prefix("interaction:modal:")]
        assert any(row["actor"] == "user" and row["event"] == "delegated" for row in rows)
        assert any(row["actor"] == "agent" and row["event"] == "decision" for row in rows)
        request = host.lifecycle.open("next_action", ("stop",), scope="accepted")
        host.pending = request
        generation = host.lifecycle.generation
        interaction_host.invalidate_pending(session)
        assert request.state == "cancelled" and host.lifecycle.generation > generation
        assert host.action(request.request_id, generation, "once").startswith("Stale")
        assert session["history"] == [{"role": "assistant", "content": "done"}]
    finally:
        server_requests.reset_for_tests()
