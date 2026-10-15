"""Classic native CLI controls do not mutate the executor prompt or transcript."""
import queue
from types import SimpleNamespace

import pytest

from hermes_cli.cli_modal_mixin import CLIModalMixin, _TIMED_OUT
from hermes_cli.interaction_host import cli_completion, cli_host


def test_native_control_and_completion_do_not_reenter_or_mutate_history(monkeypatch):
    from hermes_cli import goals
    monkeypatch.setattr(goals, "count_active_delegations", lambda sid: 0)
    monkeypatch.setattr(goals, "gather_background_processes", lambda **kwargs: [])
    history = [{"role": "user", "content": "task"}, {"role": "assistant", "content": "done"}]
    frames = []
    manager = SimpleNamespace(state=SimpleNamespace(goal="accepted"), is_active=lambda: False)
    cli = SimpleNamespace(session_id="fixture", agent=SimpleNamespace(session_id="fixture", _cached_system_prompt="stable"),
        _app=object(), _pending_input=queue.Queue(), conversation_history=history,
        _get_goal_manager=lambda: manager, _normalize_slash_confirm_choice=lambda raw, choices: raw,
        _prompt_text_input_modal=lambda **kwargs: frames.append(kwargs) or "continue")
    CLIModalMixin._handle_autonomy_command(cli, "/autonomy grant decider 60 2")
    assert cli_host(cli).lifecycle.mode == "assisted"
    cli._pending_input.put("user wins")
    cli_completion(cli)
    assert not frames
    cli._pending_input.get()
    cli_completion(cli)
    cli_completion(cli)
    assert len(frames) == 1 and 0 < frames[0]["timeout"] <= 600
    assert history == cli.conversation_history and cli.agent._cached_system_prompt == "stable"
    CLIModalMixin._handle_autonomy_command(cli, "/autonomy off")
    assert cli_host(cli).lifecycle._grant is None


def test_native_modal_rejects_already_queued_late_permission(monkeypatch):
    from hermes_cli import cli_modal_mixin
    now = [10.0]
    monkeypatch.setattr(cli_modal_mixin._time, "monotonic", lambda: now[0])
    response = queue.Queue()
    response.put("once")
    cli = SimpleNamespace(_approval_deadline=10.0, _paint_now=lambda: None)
    assert CLIModalMixin._poll_modal_queue(cli, response, "_approval_deadline") is _TIMED_OUT
    assert response.get_nowait() == "once"


def test_pending_native_once_and_yolo_share_configured_decisions(monkeypatch):
    from cli import _SeededQueryMessage
    from hermes_cli import interaction_decider, goals
    monkeypatch.setattr(goals, "count_active_delegations", lambda sid: 0)
    monkeypatch.setattr(goals, "gather_background_processes", lambda **kwargs: [])
    frames = []
    manager = SimpleNamespace(state=SimpleNamespace(goal="accepted"), is_active=lambda: False)
    cli = SimpleNamespace(session_id="controls", agent=SimpleNamespace(session_id="controls"),
        _app=object(), _pending_input=queue.Queue(), conversation_history=[{"role": "assistant", "content": "done"}],
        _get_goal_manager=lambda: manager, _normalize_slash_confirm_choice=lambda raw, choices: raw)
    def modal(**kwargs):
        frames.append(kwargs)
        assert "Once" in [row[0] for row in kwargs["choices"]]
        assert "Always (YOLO; all commands auto-approved)" in [row[0] for row in kwargs["choices"]]
        return "Once" if len(frames) == 1 else "Always (YOLO; all commands auto-approved)"
    cli._prompt_text_input_modal = modal
    from tools import approval
    monkeypatch.setattr(approval, "_YOLO_MODE_FROZEN", False)
    approval.disable_session_yolo(cli.session_id)
    cli._toggle_yolo = lambda: approval.enable_session_yolo(cli.session_id)
    host = cli_host(cli)
    host.command("grant decider 60 2", "accepted")
    def synthetic_decision(session, token):
        return session.machine_result(token, "continue", {"model": "synthetic", "provider": "fixture"})
    monkeypatch.setattr(interaction_decider, "decide", synthetic_decision)
    cli_completion(cli)
    assert host.lifecycle.mode == "assisted"
    queued = cli._pending_input.get_nowait()
    assert isinstance(queued, _SeededQueryMessage) and queued.text == host.action_prompt("continue")
    cli.conversation_history.append({"role": "assistant", "content": "next completion"})
    cli_completion(cli)
    assert len(frames) == 2 and host.lifecycle._grant["remaining"] == 2
    assert host.lifecycle.mode == "autonomous"
    queued = cli._pending_input.get_nowait()
    assert isinstance(queued, _SeededQueryMessage) and queued.text == host.action_prompt("continue")
    assert [row["actor"] for row in host.lifecycle.journal if row["event"] == "decision"] == ["agent", "agent"]
    # Native YOLO, not a separate grant state, controls subsequent AUTO decisions.
    cli._prompt_text_input_modal = lambda **kwargs: pytest.fail("AUTO must not ask")
    cli.conversation_history.append({"role": "assistant", "content": "exhausted completion"})
    cli_completion(cli)
    assert len(frames) == 2 and host.lifecycle.mode == "autonomous"
    assert len([row["event"] for row in host.lifecycle.journal if row["event"] == "decision"]) == 3
    queued = cli._pending_input.get_nowait()
    assert isinstance(queued, _SeededQueryMessage) and queued.text == host.action_prompt("continue")
    approval.disable_session_yolo(cli.session_id)


def test_real_cli_after_turn_drain_and_interactive_entry(monkeypatch):
    from cli import HermesCLI
    from prompt_toolkit.application import Application
    from prompt_toolkit.input import DummyInput
    from prompt_toolkit.output import DummyOutput
    from hermes_cli import goals
    from hermes_cli.cli_tui_runtime_mixin import CLITuiRuntimeMixin
    monkeypatch.setattr(goals, "count_active_delegations", lambda sid: 0)
    monkeypatch.setattr(goals, "gather_background_processes", lambda **kwargs: [])
    shell = object.__new__(HermesCLI)
    shell.session_id = "entry"
    shell.agent = SimpleNamespace(session_id="entry", _cached_system_prompt="stable")
    shell._app = Application(input=DummyInput(), output=DummyOutput())
    shell._pending_input = queue.Queue()
    shell.conversation_history = [{"role": "assistant", "content": "done"}]
    shell._pending_tool_info = {}
    shell._last_turn_interrupted = shell._pet_turn_error = shell._should_exit = False
    shell._voice_mode = shell._voice_continuous = shell._voice_recording = False
    shell._interaction_seeded_turn = False
    shell._get_goal_manager = shell._get_loop_manager = lambda: None
    shell._pet_react_turn_end = shell._turn_summary_emit = lambda: None
    shell._maybe_complete_loop_tick_after_turn = lambda: None
    shell._drain_process_notifications = lambda *args: None
    frames = []
    shell._prompt_text_input_modal = lambda **kwargs: frames.append(kwargs) or "continue"
    shell._drain_interrupt_queue_to_pending_input = lambda: shell._pending_input.put("queued input")
    shell._maybe_continue_goal_after_turn = lambda: None
    CLITuiRuntimeMixin._tui_after_turn(shell)
    assert not frames
    shell._pending_input.get()
    shell._drain_interrupt_queue_to_pending_input = lambda: None
    CLITuiRuntimeMixin._tui_after_turn(shell)
    assert len(frames) == 1
    shell._interaction_seeded_turn = True
    shell.conversation_history.append({"role": "assistant", "content": "seeded completion"})
    CLITuiRuntimeMixin._tui_after_turn(shell)
    assert len(frames) == 1 and shell.agent._cached_system_prompt == "stable"


@pytest.mark.parametrize("raw", ["decider 0 1", "decider nan 1", "decider inf 1", "decider 60 0",
                                 "decider 60 11", "../foreign 60 1", "decider 60", "", None,
                                 "expired", "scope-mismatch"])
def test_native_completion_invalid_or_missing_finite_grant_returns_to_human(monkeypatch, raw):
    from hermes_cli import goals, interaction_decider
    monkeypatch.setattr(goals, "count_active_delegations", lambda sid: 0)
    monkeypatch.setattr(goals, "gather_background_processes", lambda **kwargs: [])
    def unexpected_decision(*args):
        pytest.fail("invalid consent reached the decision runner")
    monkeypatch.setattr(interaction_decider, "decide", unexpected_decision)
    frames = []
    questions = []
    shell = SimpleNamespace(session_id="invalid-grant", agent=SimpleNamespace(session_id="invalid-grant"),
        _app=object(), _pending_input=queue.Queue(), conversation_history=[{"role": "assistant", "content": "done"}],
        _get_goal_manager=lambda: SimpleNamespace(state=SimpleNamespace(goal="accepted"), is_active=lambda: False),
        _normalize_slash_confirm_choice=lambda answer, choices: answer,
        _prompt_text_input_modal=lambda **kwargs: frames.append(kwargs) or "stop",
        _clarify_callback=lambda *args, **kwargs: questions.append((args, kwargs)) or raw)
    host = cli_host(shell)
    seeded = raw in {"expired", "scope-mismatch"}
    if seeded:
        now = [0.0]
        host.lifecycle._clock = lambda: now[0]
        host.command("grant decider 1 1", "other accepted goal" if raw == "scope-mismatch" else "accepted")
        now[0] = 2.0 if raw == "expired" else 0.0
        request = host.lifecycle.open("next_action", ("continue", "stop"), scope="accepted")
        assert host.lifecycle.delegate(request.request_id, host._authority) is None
        host.lifecycle.cancel(request.request_id)
    else:
        host.command("grant " + (raw or ""), "accepted")
        assert host.lifecycle._grant is None
    cli_completion(shell)
    assert len(frames) == 1 and not questions
    assert host.pending is None and host.lifecycle.mode == "assisted"
    assert len([row for row in host.lifecycle.journal if row["event"] == "grant"]) == int(seeded)
    assert not any(row["event"] == "decision" for row in host.lifecycle.journal)
    assert host.lifecycle._grant is None and host.stopped
