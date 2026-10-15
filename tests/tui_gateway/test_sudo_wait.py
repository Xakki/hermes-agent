"""Sudo input has a longer bounded window without extending other human approvals."""

import pytest

from tests.tui_gateway.test_secret_capture_session import _gateway


@pytest.mark.parametrize("elapsed,answer,expected", [
    (1799, "fixture-answer", "fixture-answer"),
    (1799, "", ""),
    (1800, "fixture-answer", ""),
])
def test_wired_sudo_callback_obeys_extended_absolute_deadline(monkeypatch, elapsed, answer, expected):
    from tools import terminal_tool

    server, requests, _ = _gateway(monkeypatch)
    requests.reset_for_tests()
    now = [100.0]
    monkeypatch.setattr(requests.time, "monotonic", lambda: now[0])
    emitted = []
    admitted = []

    def respond(frame):
        assert frame["method"] == "sudo"
        assert frame["params"]["session_id"] == "sudo-fixture"
        now[0] += elapsed
        admitted.append(requests.resolve_response({"id": frame["id"], "result": {"value": answer}}))
        return True

    monkeypatch.setattr(requests, "_write", respond)
    monkeypatch.setattr(requests, "_emit", lambda *args: emitted.append(args))
    monkeypatch.setattr(requests, "_answerable", lambda sid: True)
    server._wire_callbacks("sudo-fixture")
    try:
        callback = terminal_tool._get_sudo_password_callback()
        assert callback is not None
        assert callback() == expected
        assert admitted == [elapsed < 1800]
        assert requests.open_requests("sudo-fixture") == []
        assert bool(emitted) is (elapsed >= 1800)
        if emitted:
            assert emitted[-1][2]["reason"] == "timeout"
    finally:
        requests.reset_for_tests()


@pytest.mark.parametrize("method,params", [
    ("approval", {"request_id": "fixture", "choices": ["deny"]}),
    ("secret", {"env_var": "FIXTURE", "prompt": "fixture"}),
    ("vault.code", {"site": "fixture"}),
])
def test_long_sudo_window_does_not_extend_other_native_prompts(monkeypatch, method, params):
    server, requests, _ = _gateway(monkeypatch)
    requests.reset_for_tests()
    now = [100.0]
    monkeypatch.setattr(requests.time, "monotonic", lambda: now[0])
    admitted = []

    def respond(frame):
        now[0] += 601
        admitted.append(requests.resolve_response({"id": frame["id"], "result": {"value": "fixture"}}))
        return True

    monkeypatch.setattr(requests, "_write", respond)
    monkeypatch.setattr(requests, "_emit", lambda *args: None)
    monkeypatch.setattr(requests, "_answerable", lambda sid: True)
    try:
        assert server._ask(method, "other-fixture", params, timeout=1800) == ""
        assert admitted == [False]
        assert requests.open_requests("other-fixture") == []
    finally:
        requests.reset_for_tests()


@pytest.mark.parametrize("mode,requested,expected", [
    ("sync", None, 1800),
    ("sync", 3600, 1800),
    ("sync", 5, 5),
    ("async", None, 1800),
])
def test_sudo_transport_bounds_sync_and_async_waits(monkeypatch, mode, requested, expected):
    from tui_gateway import server_requests as requests

    requests.reset_for_tests()
    now = [100.0]
    monkeypatch.setattr(requests.time, "monotonic", lambda: now[0])
    admitted = []
    cancellations = []

    def respond(frame):
        req = requests._open[frame["id"]]
        assert req.deadline == now[0] + expected
        now[0] += expected
        admitted.append(requests.resolve_response({"id": frame["id"], "result": {"value": "late"}}))

    monkeypatch.setattr(requests, "_write", respond)
    monkeypatch.setattr(requests, "_emit", lambda *args: cancellations.append(args))
    monkeypatch.setattr(requests, "_answerable", lambda sid: True)
    try:
        if mode == "sync":
            assert requests.send("sudo", "transport-fixture", {}, timeout=requested) is None
        else:
            settled = []
            cancel = requests.send_async("sudo", "transport-fixture", {}, settled.append)
            cancel("timeout")
            assert settled == []
        assert admitted == [False]
        assert requests.open_requests("transport-fixture") == []
        assert cancellations[-1][2]["reason"] == "timeout"
    finally:
        requests.reset_for_tests()
