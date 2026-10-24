"""Native human transport honors absolute deadlines, not waiter scheduling."""
import pytest
from tui_gateway import server_requests as requests


@pytest.mark.parametrize("batch", [False, True])
def test_late_response_cannot_win_expired_human_wait(monkeypatch, batch):
    requests.reset_for_tests()
    now = [10.0]
    monkeypatch.setattr(requests.time, "monotonic", lambda: now[0])
    rejected = []

    def deliver(frame):
        if batch:
            assert requests.lock_answer(frame["id"], "a", "first") == ["b"]
        now[0] = 12.0
        rejected.append(requests.resolve_response({"id": frame["id"], "result": {"answer": "late"}}))

    requests.bind_sinks(deliver, lambda *args: None, lambda sid: True)
    result = requests.send("clarify", "s", {"question": "choose", "choices": ["first"]},
                           timeout=1, qids=["a", "b"] if batch else None)
    assert rejected == [False]
    assert result == ({"answers": {"a": "first"}, "outcome": "timed_out"} if batch else None)
    assert requests.open_requests("s") == []


@pytest.mark.parametrize("settlement", ["answer", "cancel", "timeout"])
def test_reminder_is_once_pending_only_and_has_no_secret_payload(monkeypatch, settlement):
    requests.reset_for_tests()
    now = [0.0]
    monkeypatch.setattr(requests.time, "monotonic", lambda: now[0])
    emitted = []
    requests.bind_sinks(lambda frame: None, lambda *args: emitted.append(args), lambda sid: True)
    req = requests.ServerRequest("s", "sudo", {"command": "private"}, timeout=600)
    requests._register(req)
    now[0] = 300
    requests._remind_pending(req)
    requests._remind_pending(req)
    reminders = [event for event in emitted if event[0] == "status.update"]
    assert len(reminders) == 1
    assert "private" not in str(reminders)
    if settlement == "answer":
        assert requests.resolve_response({"id": req.id, "result": {"value": "yes"}})
    elif settlement == "cancel":
        requests.cancel("s")
    else:
        now[0] = 600
        assert not requests.resolve_response({"id": req.id, "result": {"value": "late"}})
        requests.cancel("s")
    requests._remind_pending(req)
    assert len([event for event in emitted if event[0] == "status.update"]) == 1


@pytest.mark.parametrize("settlement", ["answer", "cancel", "timeout"])
@pytest.mark.parametrize("method,params,answer", [
    ("sudo", {}, {"value": "synthetic-answer"}),
    ("clarify", {"question": "fixture"}, {"answer": "fixture"}),
    ("approval", {"request_id": "fixture", "choices": ["deny"]}, {"choice": "deny"}),
    ("secret", {"env_var": "FIXTURE", "prompt": "fixture"}, {"value": "synthetic-answer"}),
    ("vault.unlock_prompt", {"backend": "fixture", "display_name": "fixture"}, {"value": "synthetic-answer"}),
    ("vault.save_login", {"origin": "https://fixture.invalid", "site": "fixture"}, {"value": ""}),
    ("vault.code", {"site": "fixture"}, {"value": ""}),
])
def test_observer_failure_never_wedges_native_delivery_or_settlement(monkeypatch, caplog, settlement, method, params, answer):
    requests.reset_for_tests()
    now = [0.0]
    monkeypatch.setattr(requests.time, "monotonic", lambda: now[0])
    frames = []
    observations = []
    def broken_observer(req, actor, event):
        observations.append((actor, event))
        raise RuntimeError("synthetic-private-payload")
    def deliver(frame):
        frames.append(frame)
        if settlement == "answer":
            assert requests.resolve_response({"id": frame["id"], "result": answer})
        elif settlement == "cancel":
            requests.cancel("observer")
        else:
            deadline = requests._open[frame["id"]].deadline
            assert deadline is not None
            now[0] = deadline
    requests.bind_sinks(deliver, lambda *args: None, lambda sid: True, broken_observer)
    try:
        result = requests.send(method, "observer", params, timeout=None)
        assert result == (answer if settlement == "answer" else None)
        assert len(frames) == 1 and not requests.open_requests("observer")
        assert observations[-1][0] == ("user" if settlement == "answer" else "host")
        assert "RuntimeError" in caplog.text and "synthetic-private-payload" not in caplog.text
    finally:
        requests.reset_for_tests()
