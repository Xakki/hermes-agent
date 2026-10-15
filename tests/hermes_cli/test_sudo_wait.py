"""Classic sudo input keeps the same bounded extended window as the native TUI."""

import pytest

from hermes_cli import cli_modal_mixin as modal
from tests.hermes_cli.test_cli_approval_ui import _make_cli_stub


@pytest.mark.parametrize("elapsed,answer,expected", [
    (1799, "fixture-answer", "fixture-answer"),
    (1799, "", ""),
    (1800, "fixture-answer", ""),
])
def test_sudo_callback_keeps_input_window_bounded_and_restores_draft(monkeypatch, elapsed, answer, expected):
    import cli as cli_module

    cli = _make_cli_stub()
    cli._app.current_buffer.text = "draft"
    cli._app.current_buffer.cursor_position = 3
    now = [100.0]
    monkeypatch.setattr(modal._time, "monotonic", lambda: now[0])
    monkeypatch.setattr(cli, "_ring_bell", lambda **kwargs: None)
    monkeypatch.setattr(cli, "_paint_now", lambda: None)
    monkeypatch.setattr(cli_module, "_cprint", lambda *args: None)
    real_poll = cli._poll_modal_queue

    def respond(response_queue, deadline_attr, **kwargs):
        now[0] += elapsed
        response_queue.put(answer)
        return real_poll(response_queue, deadline_attr, **kwargs)

    monkeypatch.setattr(cli, "_poll_modal_queue", respond)
    assert cli._sudo_password_callback() == expected
    assert cli._sudo_state is None
    assert cli._sudo_deadline == 0
    assert cli._app.current_buffer.text == "draft"
    assert cli._app.current_buffer.cursor_position == 3
