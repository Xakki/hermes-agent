"""Actual classic constructor/application; fixture inference and dependency liveness only."""
import asyncio
from types import SimpleNamespace

from hermes_cli.interaction_host import InteractionHost, cli_completion, cli_host


def test_constructor_running_application_after_turn_drains_before_exactly_one_native_choice(monkeypatch):
    from cli import HermesCLI
    from hermes_cli import goals
    from prompt_toolkit.application import Application
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.output import DummyOutput
    from tools import process_registry as processes
    from tools.process_registry_notifications import ProcessNotificationBatch
    registry = processes.ProcessRegistry()
    monkeypatch.setattr(processes, "process_registry", registry)
    monkeypatch.setenv("HERMES_DEFER_AGENT_STARTUP", "1")
    shell = HermesCLI(model="fixture", provider="openai-compat", api_key="synthetic",
                      base_url="http://127.0.0.1:1/v1")
    # The fixture exercises the shadow/local CLI; canonical attachment has separate tests.
    from hermes_state import SessionDB
    shell._session_db = SessionDB()
    shell._tui_init_run_state()
    shell._input_area = shell._tui_build_input_area()
    shell.agent = SimpleNamespace(session_id=shell.session_id, _cached_system_prompt="stable", tool_schemas=[{"name": "fixture"}])
    shell.conversation_history = [{"role": "user", "content": "task"}, {"role": "assistant", "content": "done"}]
    baseline = [dict(row) for row in shell.conversation_history]
    dependency = {"delegated": 0, "background": []}
    gather_background_processes = goals.gather_background_processes
    monkeypatch.setattr(goals, "count_active_delegations", lambda sid: dependency["delegated"])
    monkeypatch.setattr(goals, "gather_background_processes", lambda **kwargs: dependency["background"])
    frames = []
    native = shell._prompt_text_input_modal
    def observed_modal(**kwargs):
        assert registry.completion_queue.empty(), "completion chooser opened before owned notification drain"
        frames.append(kwargs)
        return native(**kwargs)
    monkeypatch.setattr(shell, "_prompt_text_input_modal", observed_modal)
    monkeypatch.setattr(shell, "_pet_react_turn_end", lambda: None)
    monkeypatch.setattr(shell, "_turn_summary_emit", lambda: None)

    async def exercise():
        with create_pipe_input() as pipe:
            app = Application(layout=Layout(shell._input_area), key_bindings=shell._tui_build_key_bindings(),
                              input=pipe, output=DummyOutput())
            shell._app = app
            painted, modal = asyncio.Event(), asyncio.Event()
            def render(_):
                painted.set()
                if shell._slash_confirm_state:
                    modal.set()
            app.after_render += render
            task = asyncio.create_task(app.run_async())
            await asyncio.wait_for(painted.wait(), 5)
            try:
                for flag in ("_interaction_seeded_turn", "_single_query_mode", "_cron_mode", "_should_exit"):
                    setattr(shell, flag, True)
                    cli_completion(shell)
                    setattr(shell, flag, False)
                dependency["delegated"] = 1
                cli_completion(shell)
                dependency["delegated"] = 0
                dependency["background"] = ["fixture-running-process"]
                cli_completion(shell)
                dependency["background"] = []
                goal = shell._get_goal_manager()
                assert goal is not None
                goal.set("accepted task")
                cli_completion(shell)
                goal.pause(reason="fixture human pause")
                loop = shell._get_loop_manager()
                assert loop is not None
                loop.set("accepted repeat", interval_seconds=60)
                cli_completion(shell)
                loop.pause()
                shell._pending_input.put("human input wins")
                goal.set("accepted task")
                loop.set("accepted repeat", interval_seconds=60)
                await asyncio.to_thread(shell._tui_after_turn)
                assert shell._pending_input.get_nowait() == "human input wins"
                assert goal.is_active() and loop.is_active()
                goal.pause(reason="fixture human pause")
                loop.pause()
                assert not frames
                # Synthetic exited process; real producer, registry, claim/ack and CLI drain.
                completed = processes.ProcessSession(id="proc_000000000001", command="synthetic completed task",
                    task_id=shell.session_id, session_key=shell.session_id, notify_on_complete=True,
                    output_buffer="synthetic completed output")
                registry._running[completed.id] = completed
                registry._finish_exited(completed, 0)
                monkeypatch.setattr(goals, "gather_background_processes", gather_background_processes)
                assert not goals.gather_background_processes(owner_task_id=shell.session_id)
                assert shell._pending_input.empty() and not registry.completion_queue.empty()
                await asyncio.to_thread(shell._tui_after_turn)
                assert not frames and registry.completion_queue.empty()
                continuation = shell._pending_input.get_nowait()
                assert isinstance(continuation, ProcessNotificationBatch)
                rendered = continuation.render(registry)
                assert rendered is not None and "synthetic completed output" in rendered
                assert shell._pending_input.empty()
                # With no remaining event/input, normal completion reaches the native modal.
                worker = asyncio.create_task(asyncio.to_thread(shell._tui_after_turn))
                await asyncio.wait_for(modal.wait(), 5)
                shell._pending_input.put("concurrent human input wins")
                shell._submit_slash_confirm_response("continue")
                await asyncio.wait_for(worker, 10)
                await asyncio.to_thread(shell._tui_after_turn)
                assert shell._pending_input.get_nowait() == "concurrent human input wins"
                assert not any(row["event"] == "answered" for row in cli_host(shell).lifecycle.journal)
                assert len(frames) == 1 and cli_host(shell).pending is None
                assert shell.conversation_history == baseline
                assert shell.agent._cached_system_prompt == "stable"
                assert shell.agent.tool_schemas == [{"name": "fixture"}]
                assert [row["role"] for row in shell.conversation_history] == ["user", "assistant"]
            finally:
                cli_host(shell).invalidate()
                app.exit()
                await task
    asyncio.run(exercise())


def test_configured_once_failure_and_revocation_end_one_logical_wait(monkeypatch):
    from hermes_cli import interaction_decider
    host = InteractionHost("native-config", "fixture-owner")
    now = [0.0]
    host.lifecycle._clock = lambda: now[0]
    request = host.lifecycle.open("next_action", ("continue", "stop"), scope="accepted")
    host.pending = request
    calls = []
    def failed(lifecycle, token):
        now[0] = 450
        return lifecycle.machine_failed(token) and False
    monkeypatch.setattr(interaction_decider, "decide", failed)
    def ask(req, offered):
        calls.append(offered)
        assert req is request and req.deadline == 600 and req.reminder_at == 300
        assert "Once" in offered
        return "Once"
    assert host.interact(request, ask) is None
    assert host.lifecycle.mode == "assisted" and len(calls) == 1
    assert request.state == "blocked" and request.reminder_cancelled
    assert host.lifecycle.reminder(request.request_id) is None
    assert host.lifecycle._grant is None
    second = host.lifecycle.open("next_action", ("stop",), scope="accepted")
    host.pending = second
    def cancelled_input(req, choices):
        host.invalidate()
        return "Once"
    assert host.interact(second, cancelled_input) is None
    assert second.state == "cancelled" and host.lifecycle._grant is None and host._control_claim is None
    assert host.lifecycle.journal[-1]["actor"] == "host"
