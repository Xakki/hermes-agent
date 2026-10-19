"""Inline turn-worker fixtures keep independent post-turn scheduling out of synchronous controls."""
from unittest.mock import Mock


class ImmediateThread:
    """Complete the selected turn target before the test's wire/order assertions."""

    def __init__(self, target=None, daemon=None, **_thread_options):
        self._target = target

    def start(self):
        self._target()


def inline_prompt_threads(monkeypatch, thread_type):
    """Isolate the independent chooser, whose real thread acquires the admission lock later."""
    from tui_gateway import server

    completion = Mock()
    monkeypatch.setattr(server.threading, "Thread", thread_type)
    monkeypatch.setattr("tui_gateway.interaction_host.maybe_completion", completion)
    return completion


def no_model_prompt(monkeypatch, server):
    """Stub the real consumer rather than only the turn-admission bookkeeping."""
    run_turn = Mock()
    monkeypatch.setattr(server, "_run_prompt_submit", run_turn)
    return run_turn


def finish_no_model_prompt(session, run_turn):
    """Reap the real submit continuation before its consumer stub is restored."""
    worker = session.get("_run_thread")
    assert worker is not None
    worker.join(timeout=5)
    assert not worker.is_alive()
    run_turn.assert_called_once()
    assert run_turn.call_args.args[2] is session
