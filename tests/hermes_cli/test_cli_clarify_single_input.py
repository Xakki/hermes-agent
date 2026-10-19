"""Scalar clarify callbacks must work with the batch-oriented input bindings."""

import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from prompt_toolkit.buffer import Buffer

from cli import HermesCLI


def _shell():
    shell = HermesCLI.__new__(HermesCLI)
    for name in ("_connection_state", "_sudo_state", "_secret_state", "_approval_state",
                 "_slash_confirm_state", "_model_picker_state", "_clarify_state",
                 "_clarify_deadline", "_clarify_multi_base"):
        setattr(shell, name, None)
    shell._clarify_freetext = False
    shell._clarify_prefill = ""
    shell._paint_now = MagicMock()
    shell._persist_prompt_summary = MagicMock()
    shell._ring_bell = MagicMock()
    return shell


@contextmanager
def _prompt(shell, callback, *args, **kwargs):
    ready = threading.Event()
    shell._paint_now.side_effect = ready.set
    result = {}

    def run():
        result["value"] = callback(*args, **kwargs)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        assert ready.wait(3), "The actual callback must publish its input state"
        yield result, thread
    finally:
        if thread.is_alive() and shell._clarify_state:
            shell._clarify_state["response_queue"].put(None)
        thread.join(3)
        assert not thread.is_alive()


def _event(text=""):
    buf = Buffer()
    buf.text = text
    return SimpleNamespace(app=SimpleNamespace(current_buffer=buf, invalidate=MagicMock()))


@pytest.mark.parametrize("entrypoint", ["_clarify_callback", "_interaction_clarify_callback"])
@pytest.mark.parametrize("mode,expected", [
    ("open", "custom answer"),
    ("empty", None),
    ("choice", "blue"),
    ("number", "blue"),
    ("other", "custom answer"),
    ("multi", '["red", "blue"]'),
    ("multi_other", '["red", "custom answer"]'),
])
def test_scalar_callback_accepts_real_input_bindings(entrypoint, mode, expected):
    shell = _shell()
    choices = [] if mode in {"open", "empty"} else ["red", "blue"]
    with _prompt(shell, getattr(shell, entrypoint), "Color?", choices,
                 multi_select=mode.startswith("multi")) as (result, thread):
        state = shell._clarify_state
        assert state is not None
        event = _event()
        if mode == "number":
            shell._tui_make_clarify_number_handler(1)(event)
        else:
            if mode in {"multi", "multi_other"}:
                state["selected_indices"].update({0, 1 if mode == "multi" else 2})
            elif mode in {"choice", "other"}:
                state["selected"] = 1 if mode == "choice" else 2
            if mode not in {"open", "empty"}:
                shell._tui_handle_enter(event)
            if mode in {"open", "other", "multi_other", "empty"}:
                event.app.current_buffer.text = "" if mode == "empty" else "custom answer"
                shell._tui_handle_enter(event)
        thread.join(3)
        assert not thread.is_alive(), "Enter must deliver the answer to the waiting callback"
        assert result["value"] == expected
        assert shell._clarify_state is None
        assert shell._clarify_freetext is False
        assert shell._clarify_deadline is None
        if mode != "number":
            assert event.app.current_buffer.text == ""


def test_batch_callback_keeps_mapping_result_through_enter():
    shell = _shell()
    questions = [{"qid": "q0", "question": "Color?", "choices": None, "multi_select": False}]
    with _prompt(shell, shell._clarify_callback, questions) as (result, thread):
        shell._tui_handle_enter(_event("custom answer"))
        thread.join(3)
        assert result["value"] == {"answers": {"q0": "custom answer"}, "outcome": "submitted"}


@pytest.mark.parametrize("entrypoint", ["_clarify_callback", "_interaction_clarify_callback"])
def test_scalar_callback_expired_deadline_does_not_grant_permission(entrypoint):
    shell = _shell()
    result = getattr(shell, entrypoint)("Color?", [], deadline=time.monotonic() - 1)
    assert result == (
        "No human answer. Pause any action requiring this clarification; "
        "no new scope or permission is granted."
    )
    assert shell._clarify_state is None
    assert shell._clarify_deadline is None


def test_autonomy_missing_goal_prompt_accepts_enter_without_creating_grant():
    from hermes_cli.interaction_host import cli_host

    shell = _shell()
    shell.session_id = "clarify-input-fixture"
    shell._get_goal_manager = lambda: None
    with _prompt(shell, shell._handle_autonomy_command,
                 "/autonomy grant default 900 10") as (result, thread):
        shell._tui_handle_enter(_event("continue"))
        thread.join(3)
        assert not thread.is_alive()
        assert result["value"] is None
        assert shell._clarify_state is None
        host = cli_host(shell)
        assert host.lifecycle._grant is None
        assert host.lifecycle.mode == "assisted"


@pytest.mark.parametrize(("config", "deadline", "expected"), [
    ({"agent": {"clarify_timeout": 42}}, None, 42),
    ({"clarify": {"timeout": 31}, "agent": {"clarify_timeout": 42}}, None, 31),
    ({"agent": {"clarify_timeout": 0}}, None, 600),
    ({"agent": {"clarify_timeout": -1}}, None, 600),
    ({}, None, 600),
    ({"agent": {"clarify_timeout": 42}}, 1035, 35),
])
def test_clarify_lower_config_preserves_native_cap_and_owner_deadline(monkeypatch, config, deadline, expected):
    shell = _shell()
    monkeypatch.setattr("cli.CLI_CONFIG", config)
    monkeypatch.setattr("hermes_cli.cli_modal_mixin._time", SimpleNamespace(monotonic=lambda: 1000.0))
    captured = []

    def answer(_queue, _deadline_attr):
        captured.append(shell._clarify_deadline)
        shell._clarify_state["answers"]["q0"] = "fixture"
        return {"q0": "fixture"}

    shell._poll_modal_queue = answer
    result = shell._clarify_callback([
        {"qid": "q0", "question": "Fixture?", "choices": None, "multi_select": False}], deadline=deadline)
    assert captured == [1000.0 + expected]
    assert result == {"answers": {"q0": "fixture"}, "outcome": "submitted"}
    assert shell._clarify_deadline is None
