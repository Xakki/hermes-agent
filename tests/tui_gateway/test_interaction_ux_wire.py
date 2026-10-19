"""Display metadata follows the strict native wire and live replay deadline."""
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tui_gateway import interaction_host as entry
from tui_gateway import server_requests as requests
from tui_gateway.contracts.server_requests import ClarifyRequestParams


@pytest.mark.parametrize("choice", ["audit", "continue", "review_changes", "new_task", "stop", "Once", "AUTO", "Once-blocked"])
def test_post_turn_menu_emits_strict_metadata_and_stable_prompt_choice(monkeypatch, tmp_path, choice):
    from tui_gateway import server
    from hermes_state import SessionDB
    from hermes_cli.config import save_config
    from hermes_cli.interaction_defaults import INTERACTION_DEFAULTS
    from hermes_cli import goals
    requests.reset_for_tests()
    save_config({"interaction": {
        "show_countdown": False,
        "menu": [{"id": "audit", "label": "Custom first", "prompt": "Read current changes only."}, *INTERACTION_DEFAULTS["menu"]],
    }})
    db = SessionDB(db_path=tmp_path / "wire.db")
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "_schedule_agent_build", lambda sid: None)
    monkeypatch.setattr(server, "_schedule_session_cap_enforcement", lambda: None)
    monkeypatch.setattr(server, "_register_session_cwd", lambda session: None)
    monkeypatch.setattr(server, "_session_has_active_delegations", lambda *args: False)
    monkeypatch.setattr(goals, "gather_background_processes", lambda **kwargs: [])
    owner = SimpleNamespace(auth_identity={"provider": "basic", "user_id": "fixture-owner"}, write=lambda frame: True)
    transport = server.bind_transport(owner)
    created = server.handle_request({"id": "create", "method": "session.create", "params": {"cols": 96}})
    sid = created["result"]["session_id"]
    session = server._sessions[sid]
    session["history"] = [{"role": "user", "content": "task"}, {"role": "assistant", "content": "done"}]
    host = entry.get_host(sid, session)
    host.accept_task("Review current work")
    from hermes_cli import interaction_decider
    from tools import approval
    monkeypatch.setattr(approval, "_YOLO_MODE_FROZEN", choice == "AUTO")
    events = []
    monkeypatch.setattr(server, "_emit", lambda kind, sid, payload: events.append((kind, payload)))
    def decide(lifecycle, token):
        assert lifecycle._current_token(token).state == "delegating"
        assert token.deadline - lifecycle._clock() > 599
        assert lifecycle.reminder(token.request_id) is None
        assert requests.open_requests(sid) == []
        if choice == "Once-blocked":
            lifecycle.machine_failed(token)
            return False
        return lifecycle.machine_result(token, "review_changes", {"model": "offline-fixture"})
    monkeypatch.setattr(interaction_decider, "decide", decide)
    frames = []
    followups = []
    def deliver(frame):
        frames.append(frame)
        validated = ClarifyRequestParams.model_validate(frame["params"])
        assert validated.interaction.phase == "human"
        assert not validated.interaction.show_countdown
        assert 598 <= validated.interaction.remaining_seconds <= 600
        assert 298 <= validated.interaction.reminder_remaining_seconds <= 300
        assert validated.choices[0] == "audit"
        assert any(row.id == "review_changes" and row.label == "Проверить изменения" for row in validated.action_metadata)
        assert requests.resolve_response({"id": frame["id"], "result": {"answer": "Once" if choice == "Once-blocked" else choice}})
    monkeypatch.setattr(server, "_run_prompt_submit", lambda *args, **kwargs: followups.append((args, kwargs)))
    requests.bind_sinks(deliver, lambda *args: None, lambda sid: True)
    try:
        entry.maybe_completion(sid, session, {"final_response": "done"})
        session["interaction_chooser_thread"].join(10)
        assert not session["interaction_chooser_thread"].is_alive()
        assert len(frames) == (0 if choice == "AUTO" else 1)
        if choice in {"Once", "AUTO", "Once-blocked"}:
            statuses = [payload["text"] for kind, payload in events if kind == "status.update" and payload.get("kind") == "interaction"]
            assert len(statuses) == 2 and "600s from handoff" in statuses[0]
            assert "human wait ended" in statuses[0]
            assert ("blocked" in statuses[1]) == (choice == "Once-blocked")
        if choice == "Once-blocked":
            assert not followups
        elif choice == "stop":
            assert host.stopped and not followups
            assert host.completion("late", "task", lambda req: pytest.fail("Stop cannot reopen queued work")) is None
        else:
            assert len(followups) == 1
            selected = "review_changes" if choice in {"Once", "AUTO"} else choice
            assert followups[0][0] == (None, sid, session, host.action_prompt(selected))
            assert "queued_prompt_generation" in followups[0][1]
            assert session["_interaction_followup"]
            assert session["history"] == [{"role": "user", "content": "task"}, {"role": "assistant", "content": "done"}]
            if selected == "new_task":
                prompt = followups[0][0][3]
                assert "todo, grooming, freeze" in prompt
                assert "Discovery is not promotion or authorization" in prompt
                assert "Never silently unfreeze" in prompt
            # Exercise the real turn admission boundary, without calling a model.
            session["agent"] = SimpleNamespace(clear_interrupt=lambda: None)
            generation = followups[0][1]["queued_prompt_generation"]
            prompt = followups[0][0][3]
            admitted = server._admit_prompt_turn(sid, session, prompt, None, generation, None, None)
            assert admitted is not None and admitted[1] is session["agent"]
            assert host.accepted_task == "Review current work"
            session["_queued_prompt_generation"] = generation + 1
            assert server._admit_prompt_turn(sid, session, prompt, None, generation, None, None) is None
    finally:
        requests.reset_for_tests()
        server._sessions.pop(sid, None)
        server.reset_transport(transport)
        db.close()


def test_reconnect_snapshot_counts_remaining_wait_without_relaxing_ordinary_or_batch_contract(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(requests.time, "monotonic", lambda: now[0])
    req = requests.ServerRequest("wire", "clarify", {"question": "choose", "choices": ["stop"],
        "interaction": {"remaining_seconds": 600, "reminder_remaining_seconds": 300,
                        "reminded": False, "show_countdown": True, "phase": "human"}}, timeout=600)
    now[0] = 300
    req.reminded = True
    replay = ClarifyRequestParams.model_validate(req.snapshot()["params"])
    assert replay.interaction.remaining_seconds == 300
    assert replay.interaction.reminder_remaining_seconds == 0 and replay.interaction.reminded
    now[0] = 600
    assert ClarifyRequestParams.model_validate(req.frame()["params"]).interaction.remaining_seconds == 0
    assert ClarifyRequestParams.model_validate({"session_id": "wire", "question": "ordinary", "choices": ["A"]}).interaction is None
    assert ClarifyRequestParams.model_validate({"session_id": "wire", "questions": [{"qid": "a", "question": "batch"}], "answers": {"a": "typed"}}).answers == {"a": "typed"}
    with pytest.raises(ValidationError):
        ClarifyRequestParams.model_validate({"session_id": "wire", "question": "ordinary", "unexpected": True})
    with pytest.raises(ValidationError):
        ClarifyRequestParams.model_validate({"session_id": "wire", "question": "ordinary", "interaction": {**req.params["interaction"], "execute_shell": "never"}})


@pytest.mark.parametrize('control', ['once', 'on'])
@pytest.mark.parametrize('pause', ['none', 'chooser_return', 'committed_callback', 'publication'])
def test_direct_control_settles_native_wait_before_separate_solver_deadline(monkeypatch, tmp_path, control, pause):
    import threading
    from agent.memory_provider import spawn_context_thread
    from hermes_cli import goals, interaction_decider
    from hermes_cli.config import save_config
    from hermes_state import SessionDB
    from tools import approval
    from tui_gateway import server

    requests.reset_for_tests()
    save_config({})
    db = SessionDB(db_path=tmp_path / 'direct-control.db')
    monkeypatch.setattr(server, '_get_db', lambda: db)
    monkeypatch.setattr(server, '_schedule_agent_build', lambda sid: None)
    monkeypatch.setattr(server, '_schedule_session_cap_enforcement', lambda: None)
    monkeypatch.setattr(server, '_register_session_cwd', lambda session: None)
    monkeypatch.setattr(server, '_session_has_active_delegations', lambda *args: False)
    monkeypatch.setattr(goals, 'gather_background_processes', lambda **kwargs: [])
    monkeypatch.setattr(approval, '_YOLO_MODE_FROZEN', False)
    class OwnerTransport:
        def __init__(self, user_id):
            self.auth_identity = {'provider': 'basic', 'user_id': user_id}
        def write(self, obj: dict) -> bool:
            return True
        def close(self) -> None:
            pass
    owner = OwnerTransport('direct-owner')
    transport = server.bind_transport(owner)
    sid = server.handle_request({'id': 'create', 'method': 'session.create', 'params': {'cols': 96}})['result']['session_id']
    session = server._sessions[sid]
    session['history'] = [{'role': 'user', 'content': 'task'}, {'role': 'assistant', 'content': 'done'}]
    host = entry.get_host(sid, session)
    host.accept_task('Review current work')
    approval.disable_session_yolo(session['session_key'])
    now = [0.0]
    host.lifecycle._clock = lambda: now[0]
    monkeypatch.setattr(requests.time, 'monotonic', lambda: now[0])
    opened, computing, release = threading.Event(), threading.Event(), threading.Event()
    paused, resume = threading.Event(), threading.Event()
    frames, tokens, followups, replies = [], [], [], []
    native_send = requests.send
    def barrier_send(*args, **kwargs):
        if pause == 'publication':
            paused.set()
            assert resume.wait(5)
        if pause == 'committed_callback':
            settled = kwargs['on_result']
            def delayed_settlement(result):
                paused.set()
                assert resume.wait(5)
                settled(result)
            kwargs['on_result'] = delayed_settlement
        result = native_send(*args, **kwargs)
        if pause == 'chooser_return':
            paused.set()
            assert resume.wait(5)
        return result
    monkeypatch.setattr(requests, 'send', barrier_send)
    def deliver(frame):
        frames.append(frame)
        opened.set()
    def decide(lifecycle, token):
        tokens.append(token)
        assert threading.current_thread() is session['interaction_chooser_thread']
        computing.set()
        assert release.wait(5)
        return lifecycle.machine_result(token, 'review_changes', {'model': 'offline-direct-fixture'})
    monkeypatch.setattr(interaction_decider, 'decide', decide)
    monkeypatch.setattr(server, '_run_prompt_submit', lambda *args, **kwargs: followups.append((args, kwargs)))
    monkeypatch.setattr(server, '_emit', lambda *args: None)
    requests.bind_sinks(deliver, lambda *args: None, lambda sid: True)
    command_thread = None
    try:
        entry.maybe_completion(sid, session, {'final_response': 'done'})
        if pause == 'publication':
            assert paused.wait(5)
            request = host.pending
            assert request is not None and host._submit_control is None
            command_thread = spawn_context_thread(lambda: replies.append(entry.command(sid, session, f'{control} {request.request_id} {host.lifecycle.generation}')), name='offline-unpublished-control')
            command_thread.start()
            command_thread.join(1)
            assert not computing.is_set(), 'Unpublished TUI control must not dispatch a solver'
            assert not approval.is_session_yolo_enabled(session['session_key']), 'Unpublished Always must not enable native YOLO'
            assert not command_thread.is_alive() and len(replies) == 1 and 'Stale' in replies.pop()
            resume.set()
        assert opened.wait(5) and len(frames) == 1
        with requests._lock:
            native = requests._open[frames[0]['id']]
        request = host.pending
        assert request is not None
        for invalid in (f'{control} wrong-request {host.lifecycle.generation}',
                        f'{control} {request.request_id} {host.lifecycle.generation + 1}'):
            assert 'Stale' in entry.command(sid, session, invalid)
            assert len(requests.open_requests(sid)) == 1 and request.state == 'pending'
            assert not approval.is_session_yolo_enabled(session['session_key']) and not computing.is_set()
        foreign = server.bind_transport(OwnerTransport('other-owner'))
        try:
            assert 'Only the live session owner' in entry.command(sid, session, f'on {request.request_id} {host.lifecycle.generation}')
            assert len(requests.open_requests(sid)) == 1 and request.state == 'pending'
        finally:
            server.reset_transport(foreign)
        now[0] = 590
        command_thread = spawn_context_thread(lambda: replies.append(entry.command(sid, session, f'{control} {request.request_id} {host.lifecycle.generation}')), name='offline-direct-control')
        command_thread.start()
        if pause in {'chooser_return', 'committed_callback'}:
            assert paused.wait(5)
            assert requests.open_requests(sid) == [] and native.answered
            assert not computing.is_set(), 'Only the resumed chooser may execute the reserved token'
            assert not approval.is_session_yolo_enabled(session['session_key'])
            now[0] = 601
            # Expiry observation in the callback gap must not erase a response
            # already committed at 590 by the native registry.
            assert host.lifecycle.reminder(request.request_id) is None
            resume.set()
        assert computing.wait(5)
        now[0] = 601
        assert requests.open_requests(sid) == []
        assert native.answered and native.event.is_set()
        assert native.reminder_timer is None or native.reminder_timer.finished.is_set()
        assert request.reminder_cancelled and host.lifecycle.reminder(request.request_id) is None
        assert tokens[0].deadline == 1190 and host.lifecycle._current_token(tokens[0]) is request
        assert 'Stale' in entry.command(sid, session, f'on {request.request_id} {host.lifecycle.generation}')
        assert approval.is_session_yolo_enabled(session['session_key']) == (control == 'on')
        command_thread.join(1)
        assert not command_thread.is_alive(), 'The direct command must submit control, not own solver execution'
        release.set()
        session['interaction_chooser_thread'].join(5)
        assert not session['interaction_chooser_thread'].is_alive()
        assert len(replies) == 1 and len(followups) == 1
        assert followups[0][0][3] == host.action_prompt('review_changes')
        assert approval.is_session_yolo_enabled(session['session_key']) == (control == 'on')
    finally:
        resume.set()
        release.set()
        requests.reset_for_tests()
        if command_thread is not None:
            command_thread.join(5)
        if session.get('interaction_chooser_thread') is not None:
            session['interaction_chooser_thread'].join(5)
        approval.disable_session_yolo(session['session_key'])
        server._sessions.pop(sid, None)
        server.reset_transport(transport)
        db.close()
