"""Session-owned human waits and bounded operational decisions, not security authorization.

Authority is an opaque host capability, never a caller-supplied user id or boolean.
Adapters must authenticate and correlate the caller before handing it to these methods.
No model tool exposes this API; no grant is persisted or changes an executor prompt.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
import threading
import time
import uuid

from hermes_cli.interaction_defaults import INTERACTION_MAX_OUTPUT_TOKENS
from agent.exception_logging import exception_type_info


@dataclass
class HumanRequest:
    request_id: str
    kind: str
    choices: tuple[str, ...]
    scope: str
    deadline: float
    reminder_at: float
    state: str = "pending"
    reminded: bool = False
    reminder_cancelled: bool = False
    choice: str | None = None
    policy_fingerprint: str = ""


@dataclass(frozen=True)
class DecisionToken:
    request_id: str
    session_id: str
    owner_profile: str
    decision_profile: str
    scope: str
    generation: int
    deadline: float
    choices: tuple[str, ...]
    nonce: str
    decision_prompt: str = ""
    max_attempts: int = 3
    max_output_tokens: int = INTERACTION_MAX_OUTPUT_TOKENS


class InteractionSession:
    """One interactive session; all settlement and grant races share the same lock.

    A host only marks a request ``next_action`` when its finite choices are already
    authorized operations. Arbitrary model clarify text is human-owned by default.
    """

    def __init__(self, session_id: str, owner_profile: str, authority: object, *, clock=time.monotonic, record=None, settings=None):
        if not session_id or not owner_profile or authority is None:
            raise ValueError("session, owner profile and authenticated authority are required")
        self.session_id, self.owner_profile = session_id, owner_profile
        self._authority, self._clock = authority, clock
        from hermes_cli.interaction_config import resolve_interaction
        self.settings = settings or resolve_interaction({})
        self._lock = threading.RLock()
        self._auto_enabled = lambda: False
        self.generation = 0
        self._grant: dict | None = None
        self._requests: dict[str, HumanRequest] = {}
        self._tokens: dict[str, DecisionToken] = {}
        self.journal: list[dict] = []
        self._record_sink = record
        self._eligible: Callable[[], bool] | None = None

    @property
    def mode(self):
        return "autonomous" if self._auto_enabled() else "assisted"

    def _record(self, actor: str, event: str, request_id: str = "", **metadata):
        self.journal.append({"actor": actor, "event": event, "request_id": request_id,
                             "session_id": self.session_id, "owner_profile": self.owner_profile,
                             "generation": self.generation, **metadata})
        if self._record_sink is not None:
            try:
                self._record_sink(dict(self.journal[-1]))
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning("Interaction persistence failed: %s", type(exc).__name__,
                                                    exc_info=exception_type_info(exc))

    def open(self, kind: str, choices=(), *, scope: str = "", timeout: float | None = None) -> HumanRequest:
        if kind not in {"clarify", "permission", "next_action"}:
            raise ValueError("not a human interaction purpose")
        timeout = self.settings.wait_timeout_seconds if timeout is None else timeout
        if not 0 <= timeout <= 600:
            raise ValueError("human wait must be finite and at most 600 seconds")
        choices = tuple(choices)
        if len(choices) > 8 or any(not isinstance(c, str) or not c or len(c) > 256 for c in choices):
            raise ValueError("choices must be a bounded finite enum")
        with self._lock:
            now = self._clock()
            request = HumanRequest(uuid.uuid4().hex, kind, choices, scope, now + timeout,
                                   now + self.settings.reminder_after_seconds,
                                   policy_fingerprint=self.settings.fingerprint)
            self._requests[request.request_id] = request
            self._record("host", "opened", request.request_id, purpose=kind)
            return request

    def _pending(self, request_id: str, *, state="pending", settled_at=None) -> HumanRequest | None:
        request = self._requests.get(request_id)
        if request is not None and request.kind == "next_action" and self._eligible is not None and not self._eligible():
            self.cancel(request_id, "input_or_closing")
            return None
        if settled_at is not None and state == "pending":
            # The registry's winning timestamp survives delayed callbacks, but
            # cannot revive revoked/cancelled work or a late human response.
            return request if (request is not None and request.state in {"pending", "expired"}
                               and 0 <= settled_at < request.deadline) else None
        if request is not None and request.state == "pending" and self._clock() >= request.deadline:
            request.state = "expired"
            self._tokens.pop(request_id, None)
            self._record("host", "expired", request_id)
        return request if request is not None and request.state == state else None

    def reminder(self, request_id: str) -> dict | None:
        with self._lock:
            request = self._pending(request_id)
            if request is None or request.reminded or request.reminder_cancelled or self._clock() < request.reminder_at:
                return None
            request.reminded = True
            self._record("host", "reminder", request_id)
            return {"request_id": request_id, "session_id": self.session_id,
                    "owner_profile": self.owner_profile, "purpose": request.kind}

    def answer(self, request_id: str, choice: str, authority: object, *, settled_at=None) -> bool:
        with self._lock:
            request = self._pending(request_id, settled_at=settled_at)
            if authority is not self._authority or request is None or (request.choices and choice not in request.choices):
                return False
            request.state, request.choice = "answered", choice
            self._tokens.pop(request_id, None)
            self._record("user", "answered", request_id)
            return True

    def cancel(self, request_id: str, reason="cancelled") -> bool:
        with self._lock:
            request = self._requests.get(request_id)
            if request is None or request.state not in {"pending", "delegating", "blocked"}:
                return False
            request.state = "cancelled"
            self._tokens.pop(request_id, None)
            self._record("host", reason, request_id)
            return True

    def grant(self, authority: object, *, profile: str, scope: str, seconds: float, decisions: int, request=None) -> bool:
        from hermes_cli.profiles import validate_profile_name
        validate_profile_name(profile)
        if not scope.strip() or len(scope) > 4096 or not 0 < seconds <= 900 or type(decisions) is not int or not 0 < decisions <= 10:
            raise ValueError("an accepted scope and finite time/decision budget are required")
        with self._lock:
            if authority is not self._authority:
                return False
            if request is not None and self._pending(request.request_id) is not request:
                return False
            self.revoke(authority, preserve=request)
            self._grant = {"profile": profile, "scope": scope, "deadline": self._clock() + seconds,
                           "remaining": decisions}
            self._record("user", "grant", decision_profile=profile)
            return True

    def revoke(self, authority: object, *, preserve=None, actor="user") -> bool:
        with self._lock:
            if authority is not self._authority:
                return False
            self.generation += 1
            self._grant = None
            for request_id, request in tuple(self._requests.items()):
                if request.kind == "next_action" and request is not preserve:
                    self.cancel(request_id, "revoked")
            self._tokens.clear()
            self._record(actor, "revoked")
            return True

    def delegate(self, request_id: str, authority: object, *, persistent=False, automatic=False, configured=False, settled_at=None) -> DecisionToken | None:
        with self._lock:
            request = self._pending(request_id, settled_at=settled_at)
            handoff = self._clock() if settled_at is None else settled_at
            grant = self._grant
            # Neither security approval nor unclassified clarify is operational discretion.
            if (authority is not self._authority or request is None or request.kind != "next_action"
                    or not request.choices or not request.scope.strip()
                    or self._clock() >= handoff + self.settings.decision_timeout_seconds
                    or (not configured and (not grant or grant["scope"] != request.scope
                    or self._clock() >= grant["deadline"] or grant["remaining"] <= 0))):
                return None
            if not configured:
                grant["remaining"] -= 1
            token = DecisionToken(request_id, self.session_id, self.owner_profile,
                                  self.settings.decision_profile if configured else grant["profile"],
                                  request.scope, self.generation,
                                  handoff + self.settings.decision_timeout_seconds,
                                  request.choices, uuid.uuid4().hex, self.settings.decision_prompt,
                                  self.settings.max_attempts, self.settings.max_output_tokens)
            self._tokens[request_id] = token
            request.state, request.reminder_cancelled = "delegating", True

            self._record("host" if automatic else "user", "delegated", request_id, decision_profile=token.decision_profile)
            return token

    def _current_token(self, token: DecisionToken) -> HumanRequest | None:
        if (self._tokens.get(token.request_id) is not token or token.generation != self.generation
                or self._clock() >= token.deadline):
            return None
        return self._pending(token.request_id, state="delegating")

    def machine_result(self, token: DecisionToken, choice: str, metadata: dict) -> bool:
        with self._lock:
            request = self._current_token(token)
            if request is None or choice not in token.choices:
                return False
            request.state, request.choice = "answered", choice
            self._tokens.pop(token.request_id, None)
            # Never persist provider objects, credentials or raw exception/response text.
            exposed: dict = {key: str(metadata.get(key, "UNKNOWN"))[:256]
                       for key in ("model", "provider", "reasoning", "fallback")}
            attempts = metadata.get("attempts")
            if isinstance(attempts, list):
                exposed["attempts"] = [{key: row[key] for key in (
                    "attempt", "provider", "model", "status", "input_bytes", "output_cap", "output_tokens", "response_model")
                    if key in row} for row in attempts[:3] if isinstance(row, dict)]
            self._record("agent", "decision", token.request_id, decision_profile=token.decision_profile, **exposed)
            return True

    def machine_failed(self, token: DecisionToken) -> bool:
        with self._lock:
            if self._tokens.get(token.request_id) is not token or token.generation != self.generation:
                return False
            self._tokens.pop(token.request_id, None)
            request = self._pending(token.request_id, state="delegating")
            if request is None:
                return False
            request.state = "blocked"
            request.reminder_cancelled = True
            self._record("host", "decision_failed", token.request_id)
            return True
