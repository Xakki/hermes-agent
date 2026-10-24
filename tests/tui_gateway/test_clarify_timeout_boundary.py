"""Legacy clarify lower waits must survive the real bounded native request boundary."""
from types import SimpleNamespace

import pytest

from tui_gateway import server
from tui_gateway import server_requests as requests


@pytest.fixture
def registry(monkeypatch):
    requests.reset_for_tests()
    clock = [1000.0]
    monkeypatch.setattr(requests, "time", SimpleNamespace(time=lambda: clock[0], monotonic=lambda: clock[0]))
    monkeypatch.setattr(requests, "_answerable", lambda sid: True)
    monkeypatch.setattr(requests, "_emit", lambda *args: None)
    monkeypatch.setattr(requests, "_observe", lambda *args: None)
    yield clock
    requests.reset_for_tests()


@pytest.mark.parametrize(("config", "expected"), [
    ({"agent": {"clarify_timeout": 42}}, 42),
    ({"clarify": {"timeout": 31}, "agent": {"clarify_timeout": 42}}, 31),
    ({"agent": {"clarify_timeout": 0}}, 600),
    ({"agent": {"clarify_timeout": -1}}, 600),
    ({"agent": {"clarify_timeout": 3600}}, 600),
    ({}, 600),
])
def test_configured_clarify_wait_reaches_real_native_registry(monkeypatch, registry, config, expected):
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: config)
    deadlines = []

    def answer(frame):
        request = requests._open[frame["id"]]
        deadlines.append(request.deadline - registry[0])
        assert requests.resolve_response({"id": frame["id"], "result": {
            "answers": {"q0": "a"}, "outcome": "submitted"}})

    monkeypatch.setattr(requests, "_write", answer)
    result = server._agent_cbs("bounded-clarify")["clarify_callback"]([
        {"qid": "q0", "question": "Fixture?", "choices": ["a", "b"], "multi_select": False}])
    assert result == {"answers": {"q0": "a"}, "outcome": "submitted"}
    assert deadlines == [expected]
    assert requests.open_request_count() == 0


@pytest.mark.parametrize(("timeout", "limit"), [(None, 600), (3600, 600), (42, 42), (35, 35)])
@pytest.mark.parametrize("expired", [False, True])
def test_native_human_cap_and_lower_deadline_keep_single_settlement(monkeypatch, registry, timeout, limit, expired):
    monkeypatch.setattr(requests, "_write", lambda frame: None)
    settlements = []
    request = requests.ServerRequest("cap", "clarify", {"question": "Fixture?"},
                                     timeout=timeout, on_result=settlements.append)
    assert request.deadline == registry[0] + limit
    assert requests._register(request)
    registry[0] += limit if expired else limit - 1
    response = {"id": request.id, "result": {"value": "fixture"}}
    assert requests.resolve_response(response) is not expired
    if expired:
        assert requests.cancel_request(request.id, reason="timeout")
    assert not requests.resolve_response(response)
    assert settlements == ([None] if expired else [{"value": "fixture"}])
    assert request.event.is_set()
    assert requests.open_request_count() == 0
