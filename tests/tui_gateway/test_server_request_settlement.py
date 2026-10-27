"""Host callbacks must not run under the registry lock or revive a losing response."""
import threading

import pytest

from tui_gateway import server_requests as requests


@pytest.mark.parametrize('winner', ['request_cancel', 'session_cancel', 'response', 'expiry'])
def test_qualification_and_competing_settlement_have_one_winner(monkeypatch, winner):
    requests.reset_for_tests()
    now = [0.0]
    monkeypatch.setattr(requests.time, 'monotonic', lambda: now[0])
    requests.bind_sinks(lambda frame: None, lambda *args: None, lambda sid: True)
    callbacks, decisions = [], []
    entered, release = threading.Event(), threading.Event()
    req = requests.ServerRequest('race', 'clarify', {'question': 'fixture'}, timeout=600,
                                 on_result=lambda value: callbacks.append(value))
    def qualify(value):
        # Reentry must be safe, even before the competing settlement arrives.
        assert requests.open_request_count() == 1
        assert req.settled_at is None
        entered.set()
        assert release.wait(5)
        return True
    req.accept_result = qualify
    requests._register(req)
    response = {'id': req.id, 'result': {'answer': 'continue'}}
    worker = threading.Thread(target=lambda: decisions.append(requests.resolve_response(response)), daemon=True)
    worker.start()
    try:
        assert entered.wait(3)
        if winner == 'request_cancel':
            assert requests.cancel_request(req.id)
        elif winner == 'session_cancel':
            assert requests.cancel('race') == 1
        elif winner == 'response':
            # The competing valid response does not need to wait on the first predicate.
            req.accept_result = lambda result: True
            assert requests.resolve_response(response)
        else:
            now[0] = 600
        release.set()
        worker.join(5)
        assert not worker.is_alive() and decisions == [False]
        if winner == 'expiry':
            assert requests.cancel_request(req.id, 'timeout')
        assert callbacks == ([{'answer': 'continue'}] if winner == 'response' else [None])
        assert req.settled_at == (0.0 if winner == 'response' else None)
        assert requests.open_requests('race') == []
        assert not requests.resolve_response(response)
    finally:
        release.set()
        worker.join(5)
        requests.reset_for_tests()


@pytest.mark.parametrize('revoke_registration', [False, True])
def test_admission_and_winning_effect_can_reenter_without_registry_host_lock_inversion(revoke_registration):
    requests.reset_for_tests()
    requests.bind_sinks(lambda frame: None, lambda *args: None, lambda sid: True)
    seen = []
    req = requests.ServerRequest('owned', 'clarify', {'question': 'fixture'}, timeout=600)
    def admitted():
        count = requests.open_request_count()
        seen.append(count)
        return not (revoke_registration and count)
    req.admit = admitted
    def settled(result):
        # The registry outcome was already committed before owned cancellation.
        assert not requests.cancel_request(req.id)
        assert requests.cancel('owned') == 0
        seen.append(result)
    req.on_result = settled
    try:
        registered = requests._register(req)
        if revoke_registration:
            assert not registered and seen[:2] == [0, 1]
            assert req.event.is_set() and not req.answered
        else:
            assert registered
            assert requests.resolve_response({'id': req.id, 'result': {'answer': 'stop'}})
            assert seen == [0, 1, {'answer': 'stop'}]
            assert req.event.is_set() and req.answered
        assert requests.open_requests('owned') == []
    finally:
        requests.reset_for_tests()
