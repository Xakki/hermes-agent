"""Host-owned decision grants never become command-security consent."""
import pytest

from hermes_cli.interaction_lifecycle import InteractionSession


@pytest.mark.parametrize("kind", ["clarify", "permission", "next_action"])
def test_deadline_reminder_and_first_valid_actor(kind):
    now = [0.0]
    authority = object()
    session = InteractionSession("s", "executor", authority, clock=lambda: now[0])
    request = session.open(kind, ("stop", "continue"), scope="accepted goal")
    assert request.deadline == 600
    now[0] = 299
    assert session.reminder(request.request_id) is None
    now[0] = 300
    assert session.reminder(request.request_id)["request_id"] == request.request_id
    assert session.reminder(request.request_id) is None
    assert not session.answer(request.request_id, "continue", object())
    assert session.answer(request.request_id, "stop", authority)
    assert not session.answer(request.request_id, "continue", authority)
    assert session.reminder(request.request_id) is None
    assert session.journal[-1]["actor"] == "user"


@pytest.mark.parametrize("persistent", [False, True])
def test_delegation_fences_and_failure_end_human_wait(persistent):
    now = [0.0]
    authority = object()
    session = InteractionSession("s", "executor", authority, clock=lambda: now[0])
    session.grant(authority, profile="decider", scope="accepted goal", seconds=900, decisions=2)
    request = session.open("next_action", ("stop", "continue"), scope="accepted goal")
    assert session.delegate(request.request_id, object(), persistent=persistent) is None
    token = session.delegate(request.request_id, authority, persistent=persistent)
    assert token is not None
    assert session.mode == "assisted"  # Only native effective YOLO enables AUTO.
    assert token.deadline == 600
    assert not session.answer(request.request_id, "stop", authority)
    now[0] = 450
    assert session.machine_failed(token)
    assert request.deadline == 600
    assert request.state == "blocked" and request.reminder_cancelled
    assert session.reminder(request.request_id) is None
    token = session.delegate(request.request_id, authority, persistent=False)
    assert token is None
    session.revoke(authority)
    assert not session.answer(request.request_id, "continue", authority)
    assert session.mode == "assisted"
    security = session.open("permission", ("once", "deny"), scope="accepted goal")
    session.grant(authority, profile="decider", scope="accepted goal", seconds=900, decisions=2)
    assert session.delegate(security.request_id, authority) is None
    now[0] = 1100
    assert not session.answer(security.request_id, "once", authority)
    assert security.state == "expired"


def test_persistence_failure_is_observable_but_cannot_change_human_settlement(caplog):
    authority = object()
    def broken_record(row):
        raise OSError("synthetic-private-payload")
    session = InteractionSession("s", "executor", authority, record=broken_record)
    request = session.open("permission", ("deny", "once"))
    assert session.answer(request.request_id, "deny", authority)
    assert not session.answer(request.request_id, "once", authority)
    assert session.journal[-1]["actor"] == "user"
    assert "OSError" in caplog.text and "synthetic-private-payload" not in caplog.text
