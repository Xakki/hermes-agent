"""Native TUI actions use the live authenticated transport, never caller identity fields."""
from __future__ import annotations

from hermes_cli.interaction_host import InteractionHost, effective_auto
from tui_gateway import server_requests


def get_host(sid: str, session: dict) -> InteractionHost:
    from hermes_constants import get_hermes_home
    from tui_gateway import server
    with session["history_lock"]:
        creator = server._session_auth_user_id(session)
        host = session.get("interaction_host")
        if host is None:
            from hermes_cli.interaction_config import resolve_interaction
            with server._session_profile_runtime_scope(session, hydrate_secrets=False):
                settings = resolve_interaction()
            def phase_sink(phase, token):
                if host is None or host.pending is None or host.pending.request_id != token.request_id or token.generation != host.lifecycle.generation:
                    return
                texts = {
                    "solver": f"Solver decision: up to {settings.decision_timeout_seconds:g}s from handoff; human wait ended. Ctrl+C stops owned work.",
                    "settled": "Solver decision settled; human wait ended.",
                    "blocked": "Solver blocked; no action selected and no human timer restarted.",
                }
                server._emit("status.update", sid, {"kind": "interaction", "text": texts[phase]})
            host = InteractionHost(sid, str(session.get("profile_home") or get_hermes_home()), settings=settings,
                auto_enabled=lambda: effective_auto(session["session_key"]),
                enable_auto=lambda: server._set_yolo(None, {"session_id": sid, "scope": "session"}, "yolo", "on", session),
                cancel_owned=lambda: server._interrupt_session_turn(sid, session), phase_sink=phase_sink,
                native_controls_only=True)
            def record(row):
                import json
                import uuid
                with server._session_profile_runtime_scope(session, hydrate_secrets=False):
                    with server._session_db(session) as db:
                        if db is not None:
                            db.set_meta(f"interaction:{session['session_key']}:{uuid.uuid4().hex}", json.dumps(row))
            host.lifecycle._record_sink = record
            session["interaction_host"] = host
            session["interaction_creator"] = creator
        elif session.get("interaction_creator") != creator:
            host.invalidate()
            session["interaction_creator"] = creator
        return host


def accepted_goal(session: dict) -> str:
    from tui_gateway import server
    from hermes_cli.goals import GoalManager
    with server._session_profile_runtime_scope(session, hydrate_secrets=False):
        state = GoalManager(session_id=session["session_key"]).state
        return state.goal if state is not None else ""


def observe_native(req, actor, event):
    from tui_gateway import server
    session = server._sessions.get(req.sid)
    if session is not None and req.method in server_requests._HUMAN_METHODS:
        get_host(req.sid, session).lifecycle._record(actor, event, req.id, purpose=req.method)


def command(sid: str, session: dict, arg: str) -> str:
    from tui_gateway import server
    transport = server.current_transport()
    contains = server._session_transport_contains
    owner = server._session_auth_user_id(session)
    caller = server._transport_auth_user_id(transport)
    if (transport is None or not contains(session, transport) or session.get("_closing")
            or owner != caller):
        return "Only the live session owner transport may control autonomy."
    host = get_host(sid, session)
    def ask(question, choices):
        return server_requests.send("clarify", sid, {"question": question, "choices": list(choices)}, timeout=600)
    return host.command(arg, accepted_goal(session), ask=ask)


def invalidate(session: dict):
    if (host := session.get("interaction_host")) is not None:
        host.invalidate()


def invalidate_pending(session: dict):
    if (host := session.get("interaction_host")) is not None:
        host.invalidate_pending()


def admit_task(sid, session, text, *, host=None):
    host = host if host is not None else get_host(sid, session)
    if session.get("_submit_user_row") is not None and isinstance(text, str):
        host.accept_task(text)
    else:
        host.invalidate_pending()


def dependencies_pending(sid: str, session: dict) -> bool:
    from tui_gateway import server
    if server._session_has_active_delegations(sid, session):
        return True
    from hermes_cli.goals import gather_background_processes
    if gather_background_processes(owner_task_id=session.get("session_key")):
        return True
    with server._session_profile_runtime_scope(session, hydrate_secrets=False):
        from hermes_cli.loops import LoopManager
        loop = LoopManager(session_id=session.get("session_key", ""))
        return server._active_goal_manager(session) is not None or loop.is_active()


def maybe_completion(sid: str, session: dict, result):
    if session.pop("_interaction_followup", False):
        return
    if (not isinstance(result, dict) or not str(result.get("final_response") or "").strip()
            or result.get("error") or result.get("interrupted") or session.get("_turn_cancel_requested")
            or session.get("_closing") or session.get("running") or session.get("queued_prompt")
            or session.get("queued_prompts") or dependencies_pending(sid, session)):
        return
    from tui_gateway import server
    from agent.memory_provider import spawn_context_thread
    if not server_requests._answerable(sid):
        return
    host = get_host(sid, session)
    key = (session.get("history_version", 0), len(session.get("history", ())))
    creator = server._session_auth_user_id(session)
    def run():
        def send(request, offered, *, configuration=False):
            generation = host.lifecycle.generation
            native_request = None
            def current():
                return (host.pending is request and generation == host.lifecycle.generation
                        and not host.stopped
                        and not session.get("_closing") and not session.get("running")
                        and creator == server._session_auth_user_id(session))
            def admitted():
                return current() and host.lifecycle._pending(request.request_id) is request
            def opened(native):
                nonlocal native_request
                native_request = native
                native.reminder_at = request.reminder_at
                native.logical_request = request.request_id
                native.logical_reminder = lambda: host.lifecycle.reminder(request.request_id) is not None
                native.admit = admitted
                host._withdraw = lambda: server_requests.cancel_request(native.id, reason="revoked")
                host._deliver = lambda choice: server_requests.resolve_response(
                    {"id": native.id, "result": {"answer": choice}}, actor="agent")
                def submit_control(request_id, control_generation, action):
                    answer = "Once" if action == "once" else "Always (YOLO; all commands auto-approved)"
                    with session["history_lock"], host._lock:
                        if (request_id != request.request_id or control_generation != generation
                                or request.kind != "next_action" or not request.scope.strip()
                                or host.stopped or answer not in offered
                                or host._control_claim is not None or not admitted()):
                            return False
                    return server_requests.resolve_response(
                        {"id": native.id, "result": {"answer": answer}}, actor="user")
                host._submit_control = submit_control
            def accept(result):
                if not isinstance(result, dict) or not admitted():
                    return False
                transport = server.current_transport()
                if transport is None or (
                    not server._session_transport_contains(session, transport)
                    or creator != server._transport_auth_user_id(transport)):
                    return False
                if configuration:
                    return True
                if not result.get("answer"):
                    return True
                if host._grant_preview is not None:
                    return result["answer"] in offered and host._control_claim is not None
                if result["answer"] in {"Once", "Always (YOLO; all commands auto-approved)"}:
                    return host._control_claim is None
                return result["answer"] in request.choices and host._control_claim is None
            def settled(result):
                # Native settlement won before any cancellation/control effects.
                # Input/owner/generation changes can still revoke the host action.
                if not isinstance(result, dict) or not current() or configuration:
                    return
                answer = result.get("answer")
                if not answer:
                    host.lifecycle.cancel(request.request_id, "dismissed")
                elif answer in {"Once", "Always (YOLO; all commands auto-approved)"}:
                    with session["history_lock"], host._lock:
                        if current():
                            host.claim_control(request, answer, settled_at=native_request.settled_at)
                else:
                    host.accept(request, answer, settled_at=native_request.settled_at)
            response = server_requests.send("clarify", sid,
                {"question": host.consent_text(request) + (" Enter PROFILE SECONDS DECISIONS." if configuration else " Choose next action."),
                 "choices": list(offered),
                 "interaction": {"remaining_seconds": max(0, request.deadline - host.lifecycle._clock()),
                                 "reminder_remaining_seconds": max(0, request.reminder_at - host.lifecycle._clock()),
                                 "reminded": request.reminded, "show_countdown": host.settings.show_countdown,
                                 "phase": "human"},
                 "action_metadata": [{"id": entry.id, "label": entry.label, "description": entry.description}
                                     for entry in host.settings.menu if entry.id in offered]}, timeout=max(0, request.deadline - host.lifecycle._clock()),
                on_open=opened, accept_result=accept, on_result=settled)
            return response.get("answer") if isinstance(response, dict) else None
        def ask(request):
            return host.interact(request, send, lambda req: send(req, (), configuration=True))
        choice = host.completion(key, accepted_goal(session), ask, admission_lock=session["history_lock"],
            eligible=lambda: not (session.get("running") or session.get("_closing") or session.get("queued_prompt")
                or session.get("queued_prompts") or server._sessions.get(sid) is not session
                or creator != server._session_auth_user_id(session)
                or key != (session.get("history_version", 0), len(session.get("history", ())))))
        if creator != server._session_auth_user_id(session) or server._sessions.get(sid) is not session:
            host.invalidate()
            return
        if choice and (prompt := host.action_prompt(choice)):
            with session["history_lock"]:
                if host.stopped or session.get("running") or session.get("_closing") or session.get("queued_prompt"):
                    return
                session["running"] = True
                session["_interaction_followup"] = True
                generation = int(session.get("_queued_prompt_generation", 0))
            server._run_prompt_submit(None, sid, session, prompt, queued_prompt_generation=generation)
    with session["history_lock"]:
        if session.get("running") or session.get("_closing") or session.get("queued_prompt") or session.get("queued_prompts"):
            return
        previous = session.get("interaction_chooser_thread")
        if previous is not None and previous.is_alive():
            return
        thread = spawn_context_thread(run, name=f"interaction-chooser-{sid}")
        session["interaction_chooser_thread"] = thread
        thread.start()
