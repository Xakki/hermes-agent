"""Local, host-owned grants and choices. This API never authorizes a command."""
from __future__ import annotations

import threading
import json
import uuid
from contextlib import nullcontext
from typing import Callable

from hermes_cli.interaction_lifecycle import InteractionSession


class InteractionHost:
    def __init__(self, session_id: str, owner_profile: str, *, record=None, settings=None, auto_enabled=None, enable_auto=None, cancel_owned=None, phase_sink=None, native_controls_only=False):
        self._authority = object()
        self.lifecycle = InteractionSession(session_id, owner_profile, self._authority, record=record, settings=settings)
        self.settings = self.lifecycle.settings
        self._auto_enabled = auto_enabled or (lambda: False)
        self._enable_auto = enable_auto
        self._cancel_owned = cancel_owned
        self._phase_sink = phase_sink or (lambda phase, token: None)
        self.lifecycle._auto_enabled = self._auto_enabled
        self.accepted_task = ""
        self.stopped = False
        self.pending = None
        self._lock = threading.RLock()
        self._completion = None
        self._control_claim = None
        self._grant_preview = None
        self._deliver: Callable[[str | None], object] | None = None
        self._withdraw: Callable[[], object] | None = None
        self._submit_control: Callable[[str, int, str], bool] | None = None
        self._native_controls_only = native_controls_only

    def invalidate(self, *, actor="host"):
        with self._lock:
            self.lifecycle.revoke(self._authority, actor=actor)
            self._control_claim = None
            if self.pending is not None:
                self.lifecycle.cancel(self.pending.request_id, "host_invalidated")
            self.pending = None
            self._deliver = None
            self._submit_control = None
            withdraw, self._withdraw = self._withdraw, None
        if withdraw is not None:
            withdraw()

    def invalidate_pending(self):
        with self._lock:
            self._control_claim = None
            self.lifecycle.generation += 1
            self.lifecycle._tokens.clear()
            if self.pending is not None:
                self.lifecycle.cancel(self.pending.request_id, "input_admitted")
            self.pending = None
            self._deliver = None
            self._submit_control = None
            withdraw, self._withdraw = self._withdraw, None
        if withdraw is not None:
            withdraw()

    def accept_task(self, text):
        self.invalidate_pending()
        self.accepted_task = text.strip()[:4096]
        self.stopped = False

    def stop(self):
        self.stopped = True
        self.invalidate(actor="user")
        if self._cancel_owned is not None:
            self._cancel_owned()

    def action_prompt(self, action_id):
        entry = next((entry for entry in self.settings.menu if entry.id == action_id and entry.enabled), None)
        if entry is None or entry.handler == "stop":
            return None
        if entry.handler == "discover_task":
            return entry.prompt + " Source order: " + ", ".join(self.settings.task_sources) + ". Discovery is not promotion or authorization."
        return entry.prompt

    def command(self, arg: str, accepted_goal: str, *, ask=None) -> str:
        parts = arg.split()
        if not parts or parts == ["status"]:
            request = self.pending
            return (f"Mode: {self.lifecycle.mode}. Pending: "
                    f"{request.request_id if request else 'none'}. Generation: {self.lifecycle.generation}.")
        if parts == ["off"]:
            self.invalidate(actor="user")
            return "Workflow authority revoked. Native /yolo controls AUTO; process-locked YOLO remains on."
        if parts[0] == "grant":
            if len(parts) != 4 or not accepted_goal:
                if ask is not None:
                    ask("An existing accepted /goal and an explicit grant are required. "
                        "Use /autonomy grant <profile> <seconds 1..900> <decisions 1..10>.", ())
                return "Missing accepted goal/profile/finite budget; no grant created."
            try:
                granted = self.lifecycle.grant(self._authority, profile=parts[1], scope=accepted_goal,
                                               seconds=float(parts[2]), decisions=int(parts[3]))
            except ValueError:
                return "Invalid named profile or finite grant budget; no grant created."
            return "Operational grant granted; mode remains assisted." if granted else "Grant rejected."
        if len(parts) == 3 and parts[0] in {"once", "on"}:
            try:
                generation = int(parts[2])
            except ValueError:
                return "Invalid generation."
            with self._lock:
                submit = self._submit_control
            if submit is not None:
                return ("Operational control submitted to the current chooser; not command approval."
                        if submit(parts[1], generation, parts[0]) else "Stale or unavailable operational control.")
            if self._native_controls_only:
                return "Stale or unavailable operational control."
            return self.action(parts[1], generation, parts[0], ask=ask)
        return "Usage: /autonomy status|off|grant PROFILE SECONDS DECISIONS|once REQUEST GENERATION|on REQUEST GENERATION"

    def action(self, request_id: str, generation: int, action: str, *, ask=None, automatic=False, claim=None) -> str:
        with self._lock:
            request = self.pending
            if (action not in {"once", "on"} or request is None or request.request_id != request_id
                    or generation != self.lifecycle.generation or (self._control_claim is not None and claim is not self._control_claim)):
                return "Stale or unavailable operational control."
            if request.kind != "next_action" or self.stopped:
                return "Nondelegable or stopped request."
            if action == "on" and not self._auto_enabled() and self._enable_auto is None:
                return "Native user AUTO control unavailable."
            if claim is not None:
                token = claim[3]
                if self.lifecycle._current_token(token) is not request:
                    return "Stale or unavailable operational control."
            else:
                token = self.lifecycle.delegate(request_id, self._authority, automatic=automatic, configured=True)
            if token is not None and action == "on" and not self._auto_enabled() and self._enable_auto is not None:
                self._enable_auto()
        if token is None:

            if ask is not None:
                ask("No current finite operational grant. Choose locally or grant an existing /goal first.", ())
            return "No current grant; human decision required."
        from hermes_cli.interaction_decider import decide
        self._phase_sink("solver", token)
        decided = decide(self.lifecycle, token)
        if self.pending is request and token.generation == self.lifecycle.generation:
            self._phase_sink("settled" if decided else "blocked", token)
        if not decided:
            return "Configured solver blocked or failed; human wait ended. No choice applied."
        with self._lock:
            deliver = self._deliver if self.pending is request else None
        if deliver is not None:
            deliver(request.choice)
        return "Operational choice computed by named profile; not command approval."

    def claim_control(self, request, answer, *, settled_at=None):
        with self._lock:
            action = {"Once": "once", "Always (YOLO; all commands auto-approved)": "on"}.get(answer)
            if (self.pending is not request or self._control_claim is not None
                    or action is None or self.stopped
                    or (action == "on" and not self._auto_enabled() and self._enable_auto is None)):
                return None
            token = self.lifecycle.delegate(request.request_id, self._authority, configured=True, settled_at=settled_at)
            if token is None:
                return None
            # Reserve the finite handoff now; only interact's chooser executes it.
            self._control_claim = (request, answer, self.lifecycle.generation, token)
            return self._control_claim

    def interact(self, request, ask, grant_input=None):
        """Configured request-scoped delegation; native mode is never toggled blindly."""
        controls = {"Once": "once", "Always (YOLO; all commands auto-approved)": "on"} if request.kind == "next_action" and request.scope else {}
        while request.state == "pending" and self.lifecycle._clock() < request.deadline:
            answer = ask(request, (*request.choices, *controls))
            if answer not in controls:
                return answer
            claim = self._control_claim or self.claim_control(request, answer)
            if claim is None or claim[0] is not request or claim[1] != answer or claim[2] != self.lifecycle.generation:
                return None

            self.action(request.request_id, self.lifecycle.generation, controls[answer], claim=claim)
            self._control_claim = None
            if request.state == "answered":
                return request.choice
        return None

    def consent_text(self, request):
        budget = f"Profile: {self.settings.decision_profile}. Solver budget: {self.settings.decision_timeout_seconds}s. Once leaves YOLO unchanged. Always enables command auto-approval."
        return f"Accepted scope: {request.scope or 'NONE — no delegation authority'}. {budget} No command/security approval."

    def accept(self, request, answer: str, *, settled_at=None) -> bool:
        if answer == "stop" and request.kind == "next_action":
            if self.pending is not request:
                return False
            self.stop()
            return True
        with self._lock:
            if self.pending is not request or self._control_claim is not None:
                return False
            if request.state == "answered":
                return request.choice == answer
            return self.lifecycle.answer(request.request_id, answer, self._authority, settled_at=settled_at)

    def completion(self, key, scope: str, ask, *, admission_lock=None, eligible=lambda: True) -> str | None:
        with (admission_lock or nullcontext()), self._lock:
            if self.stopped or not eligible() or self._completion == key or self.pending is not None:
                return None
            self._completion = key
            self.lifecycle._eligible = eligible
            request = self.lifecycle.open("next_action", tuple(entry.id for entry in self.settings.menu if entry.enabled), scope=scope or self.accepted_task)
            self.pending = request
        try:
            if self._auto_enabled():
                self.action(request.request_id, self.lifecycle.generation, "on", automatic=True)
            answer = request.choice if request.state == "answered" else ask(request) if request.state == "pending" and not self._auto_enabled() else None
            if isinstance(answer, str):
                self.accept(request, answer)
            if answer == "stop" and self.stopped:
                return "stop"
            if request.state != "answered":
                self.lifecycle._pending(request.request_id)
                self.lifecycle.cancel(request.request_id, "dismissed")
                return None
            return request.choice
        finally:
            with self._lock:
                if self.pending is request:
                    self.pending = None
                    self._deliver = None
                    self._withdraw = None
                    self._submit_control = None
                self._control_claim = None


def cli_host(cli) -> InteractionHost:
    from hermes_constants import get_hermes_home
    host = getattr(cli, "_interaction_host", None)
    if host is None or host.lifecycle.session_id != cli.session_id:
        def record(row):
            db = getattr(cli, "_session_db", None)
            if db is not None:
                db.set_meta(f"interaction:{cli.session_id}:{uuid.uuid4().hex}", json.dumps(row))
        from hermes_cli.interaction_config import resolve_interaction
        host = cli._interaction_host = InteractionHost(cli.session_id, str(get_hermes_home()), record=record,
            settings=resolve_interaction(), auto_enabled=lambda: effective_auto(cli.session_id),
            enable_auto=lambda: cli._toggle_yolo() if not effective_auto(cli.session_id) else None,
            cancel_owned=lambda: stop_cli_owned(cli))
    return host


def effective_auto(session_key):
    from tools import approval
    return approval._YOLO_MODE_FROZEN or approval.is_session_yolo_enabled(session_key)


def stop_cli_owned(cli):
    agent = getattr(cli, "agent", None)
    if agent is not None:
        from agent.interrupt_compat import request_hard_interrupt
        request_hard_interrupt(agent)
    parent = str(getattr(agent, "session_id", "") or cli.session_id)
    from tools.async_delegation import interrupt_for_session
    from tools.process_registry import process_registry
    interrupt_for_session(parent_session_id=parent, reason="interaction_stop")
    process_registry.kill_all(parent, source="cli.stop", consume_output=True)


def invalidate_cli(cli):
    if (host := getattr(cli, "_interaction_host", None)) is not None:
        host.invalidate()


def cli_completion(cli):
    if (getattr(cli, "_should_exit", False) or getattr(cli, "_last_turn_interrupted", False)
            or getattr(cli, "_pet_turn_error", False) or not getattr(cli, "_app", None)
            or getattr(cli, "_voice_mode", False) or not cli._pending_input.empty()):
        return
    if (getattr(cli, "_interaction_seeded_turn", False)
            or getattr(cli, "_cron_mode", False) or getattr(cli, "_single_query_mode", False)):
        return
    from hermes_cli.goals import count_active_delegations, gather_background_processes
    if (count_active_delegations(getattr(cli.agent, "session_id", None))
            or gather_background_processes(owner_task_id=cli.session_id)):
        return
    manager = cli._get_goal_manager()
    if manager and manager.is_active():
        return
    loop = cli._get_loop_manager() if hasattr(cli, "_get_loop_manager") else None
    if loop and loop.is_active():
        return
    history = cli.conversation_history
    if not history or history[-1].get("role") != "assistant":
        return
    host = cli_host(cli)
    scope = manager.state.goal if manager and manager.state else ""
    def ask(request):
        cli._interaction_wait_request = request
        def prompt(request, offered):
            metadata = {entry.id: entry for entry in host.settings.menu}
            choices = [(choice, metadata[choice].label if choice in metadata else choice,
                        metadata[choice].description if choice in metadata else "") for choice in offered]
            raw = cli._prompt_text_input_modal(title="Task complete — next action", detail=host.consent_text(request),
                choices=choices, timeout=min(600, max(0, request.deadline - host.lifecycle._clock())))
            return cli._normalize_slash_confirm_choice(raw, choices)
        def grant_input(request):
            return cli._clarify_callback(host.consent_text(request) + " Enter PROFILE SECONDS DECISIONS. Cancel to stay assisted.",
                                         [], deadline=request.deadline)
        try:
            return host.interact(request, prompt, grant_input)
        finally:
            cli._interaction_wait_request = None
    key = (len(history), id(history[-1]))
    choice = host.completion(key, scope, ask, eligible=lambda: not (
        getattr(cli, "_should_exit", False) or not cli._pending_input.empty()
        or not cli.conversation_history or key != (len(cli.conversation_history), id(cli.conversation_history[-1]))))
    if choice and (prompt := host.action_prompt(choice)):
        from cli import _SeededQueryMessage
        cli._pending_input.put(_SeededQueryMessage(prompt))
