"""The canonical approval timer observes the real native wait without answering it."""
from types import SimpleNamespace

import pytest

from gateway import run
from gateway.session_events import SessionEvents
from gateway.session_pending_controls import PendingControls
from tools import approval_gateway_wait


def test_replayed_deadline_starts_after_notification_and_expiry_never_allows(monkeypatch):
    # The projection test needs no command content or installation manifest.
    monkeypatch.setattr(run, '_redact_approval_command', lambda _: '')
    clock = [100.0]
    monkeypatch.setattr(approval_gateway_wait, 'time', SimpleNamespace(monotonic=lambda: clock[0],
                                                                       time=lambda: 1000.0 + clock[0]))
    monkeypatch.setattr(approval_gateway_wait._ctx, '_get_approval_timeout', lambda: 5)
    events = SessionEvents()
    controls = PendingControls(events)
    before, replay, projected = [], [], []

    def observe(frame):
        if frame['params']['type'] == 'approval.deadline':
            projected.append(frame['params']['payload'])
            replay.extend(controls.snapshot('session', 2))
            clock[0] += 5

    def notify(data):
        controls.register('session', 'route', 2, data, timeout_s=5)
        before.extend(controls.snapshot('session', 2))
        clock[0] += 20

    events.observers.add(observe)
    result = approval_gateway_wait._await_gateway_decision('route', notify, {'request_id': 'approval-fixture'})
    assert len(before) == 1 and 'deadline_ms' not in before[0]
    assert projected == [{'prompt_id': 'approval-fixture', 'execution_generation': 2, 'deadline_ms': 1125000}]
    assert len(replay) == 1 and replay[0]['deadline_ms'] == projected[0]['deadline_ms']
    assert result['resolved'] is False and result['choice'] is None
    assert controls.snapshot('session', 2) == ()


@pytest.mark.parametrize('method', ['sudo', 'secret', 'vault.unlock_prompt', 'vault.save_login', 'vault.code'])
def test_masked_owner_replays_only_timing_and_cancel(monkeypatch, method):
    from gateway import session_server_requests as requests

    events = SessionEvents()
    first = []
    class Peer:
        def write(self, obj):
            return True
        def close(self):
            pass

    events.fanout.attach(Peer())
    monkeypatch.setattr(requests, 'time', SimpleNamespace(time=lambda: 100.0))
    def observe(frame):
        if frame['params']['type'] == 'request.deadline':
            first.append((frame['params']['payload'], requests.open_requests(events, 's1')))
    events.observers.add(observe)
    assert requests.ask_viewer(events, 's1', method, {}, timeout=5, cancelled=lambda: True) == ''
    [(metadata, [replayed])] = first
    assert metadata == {'id': replayed['id'], 'method': method, 'deadline_ms': 105000}
    assert replayed['params'] == {'session_id': 's1', 'deadline_ms': 105000}
    assert requests.open_requests(events, 's1') == []


def test_single_clarify_projects_the_existing_deadline(monkeypatch):
    from tools import clarify_gateway

    monkeypatch.setattr(run, '_redact_approval_command', lambda text: text)
    controls = PendingControls(SessionEvents())
    clock = [100.0]
    monkeypatch.setattr(clarify_gateway, 'time', SimpleNamespace(monotonic=lambda: clock[0], time=lambda: clock[0]))
    entry = clarify_gateway.register('clarify-fixture', 'route', 'Format?', ['Text', 'Table'])
    controls.register_clarify('s1', 2, entry, timeout_s=5)
    snapshots = []
    def observe(frame):
        if frame['params']['type'] == 'clarify.deadline':
            snapshots.append(controls.snapshot('s1', 2))
            clock[0] += 5
    controls.events.observers.add(observe)
    assert clarify_gateway.wait_for_response('clarify-fixture', 5) is None
    assert snapshots[0][0]['deadline_ms'] == 105000
    assert controls.snapshot('s1', 2) == ()


def test_managed_deadline_updates_only_the_matching_pending_worker_prompt(monkeypatch):
    from gateway.session_managed_worker import _prompt_frame

    monkeypatch.setattr(run, '_redact_approval_command', lambda text: text)
    controls = PendingControls(SessionEvents())
    live = SimpleNamespace(controls=controls, event_stream=controls.events, route='route')
    authority = SimpleNamespace(sessions={'s1': live}, register_approval=lambda sid, gen, route, data: controls.register(sid, route, gen, data))
    ref, row = SimpleNamespace(session_id='s1'), {'generation': 2}
    replied = []
    worker = SimpleNamespace(respond=lambda *args: replied.append(args))
    pending = {'type': 'approval', 'data': {'request_id': 'worker-fixture'}}
    assert _prompt_frame(authority, ref, row, worker, pending)
    deadline = {'type': 'prompt_deadline', 'prompt_id': 'worker-fixture', 'deadline_ms': 105000}
    assert _prompt_frame(authority, ref, {'generation': 1}, worker, deadline)
    assert 'deadline_ms' not in controls.snapshot('s1', 2)[0]
    assert _prompt_frame(authority, ref, row, worker, deadline)
    assert controls.snapshot('s1', 2)[0]['deadline_ms'] == 105000
    assert replied == []
    assert _prompt_frame(authority, ref, row, worker, {'type': 'prompt_settled', 'prompt_id': 'worker-fixture'})
    assert controls.snapshot('s1', 2) == ()


def test_sidecar_projects_at_wait_start_and_reconciles_existing_replay(monkeypatch):
    from tui_gateway import server_requests

    frames, events = [], []
    monkeypatch.setattr(server_requests, '_answerable', lambda _: True)
    monkeypatch.setattr(server_requests, '_write', frames.append)
    monkeypatch.setattr(server_requests, 'time', SimpleNamespace(time=lambda: 100.0))
    def emit(kind, sid, payload):
        events.append((kind, payload))
        if kind == 'request.deadline':
            [request] = server_requests.open_requests(sid)
            assert request['params']['deadline_ms'] == 105000
            # An existing cancellation path, not any credential-bearing reply.
            server_requests.cancel(sid, reason='interrupted')
    monkeypatch.setattr(server_requests, '_emit', emit)
    assert server_requests.send('sudo', 's1', {}, timeout=5) is None
    assert frames[0]['params'] == {'session_id': 's1'}
    assert [kind for kind, _ in events] == ['request.deadline', 'request.cancel']
    assert server_requests.open_requests('s1') == []


def test_compute_host_replay_updates_metadata_without_resurrecting_a_closed_prompt(monkeypatch):
    from tui_gateway import server

    session = {}
    monkeypatch.setitem(server._sessions, 's1', session)
    monkeypatch.setattr(server, 'write_json', lambda msg: True)
    assert server._relay_compute_host_rpc({
        'id': 'srq-fixture', 'method': 'sudo', 'params': {'session_id': 's1'}})
    def event(kind, **payload):
        return server._relay_compute_host_rpc({'method': 'event', 'params': {
            'session_id': 's1', 'type': kind, 'payload': {'id': 'srq-fixture', 'method': 'sudo', **payload}}})
    assert event('request.deadline', deadline_ms=105000)
    assert session['_compute_host_open_request']['params'] == {'session_id': 's1', 'deadline_ms': 105000}
    assert event('request.cancel', reason='timeout')
    assert event('request.deadline', deadline_ms=105000)
    assert '_compute_host_open_request' not in session
