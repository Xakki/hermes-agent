"""Decision-only limits at physical HTTP dispatch; ordinary auxiliary users are untouched.

Budget exhaustion is not a transport failure and must not enter native recovery ladders.
Unsupported transports fail closed to the original human request. No SDK/shared-client
mutation, route rewrites, credential persistence or observer-hook authorization.
"""
from __future__ import annotations

import contextlib
import contextvars
import copy
import json
import threading
import time
from types import SimpleNamespace

import httpx

from hermes_cli.interaction_defaults import INTERACTION_MAX_OUTPUT_TOKENS


class DecisionBudgetExceeded(BaseException):
    pass


_current = contextvars.ContextVar("auxiliary_decision_budget", default=None)


class DecisionBudget:
    def __init__(self, deadline, cancelled, *, attempts=3, max_output_tokens=INTERACTION_MAX_OUTPUT_TOKENS):
        if type(max_output_tokens) is not int or not 0 < max_output_tokens <= INTERACTION_MAX_OUTPUT_TOKENS:
            raise DecisionBudgetExceeded("invalid decision output cap")
        self.deadline, self.cancelled = deadline, cancelled
        self.limit = attempts
        self.max_output_tokens = max_output_tokens
        self.attempts: list[dict] = []
        self.input_bytes = 0
        self.output_reserved = 0
        self._lock = threading.RLock()

    def check(self):
        if self.cancelled() or time.monotonic() >= self.deadline:
            raise DecisionBudgetExceeded("decision cancelled or deadline exhausted")

    def start(self, request, provider):
        with self._lock:
            self.check()
            body = request.read()
            try:
                payload = json.loads(body)
            except (ValueError, TypeError):
                raise DecisionBudgetExceeded("unsupported decision wire body") from None
            if not isinstance(payload, dict):
                raise DecisionBudgetExceeded("unsupported decision wire body")
            caps = [payload[key] for key in ("max_tokens", "max_completion_tokens", "max_output_tokens") if key in payload]
            cap = caps[0] if len(caps) == 1 else None
            from agent.codex_responses_adapter import classify_responses_route
            uncapped_codex = classify_responses_route(SimpleNamespace(
                provider=provider, base_url=str(request.url))).is_codex_backend
            if (len(self.attempts) >= self.limit or len(body) > 8192
                    or self.input_bytes + len(body) > 24576 or payload.get("tools")
                    or (not uncapped_codex and (type(cap) is not int or not 0 < cap <= self.max_output_tokens
                    or self.output_reserved + cap > INTERACTION_MAX_OUTPUT_TOKENS))):
                raise DecisionBudgetExceeded("physical attempt/input/output budget exhausted")
            self.input_bytes += len(body)
            row = {"attempt": len(self.attempts) + 1, "provider": provider or "UNKNOWN",
                   "model": str(payload.get("model") or "UNKNOWN"), "status": "UNKNOWN",
                   "input_bytes": len(body), "output_cap": None if uncapped_codex else cap,
                   "response_model": "UNKNOWN"}
            if not uncapped_codex:
                self.output_reserved += cap  # Failed/unknown usage is charged conservatively, never refunded.
            self.attempts.append(row)
            return row

    def finish(self, row, payload):
        self.check()
        if not isinstance(payload, dict):
            return
        response = payload.get("response")
        if isinstance(response, dict):
            row["response_model"] = str(payload.get("model") or response.get("model") or "UNKNOWN")[:256]
        else:
            row["response_model"] = str(payload.get("model") or "UNKNOWN")[:256]
        usage = payload.get("usage") or (response.get("usage") if isinstance(response, dict) else None) or {}
        if isinstance(usage, dict):
            output = usage.get("completion_tokens", usage.get("output_tokens"))
            if type(output) is int:
                row["output_tokens"] = output
                if output < 0 or (row["output_cap"] is not None and
                                  (output > row["output_cap"] or output > INTERACTION_MAX_OUTPUT_TOKENS)):
                    raise DecisionBudgetExceeded("provider exceeded requested output cap")


def decision_budget_active():
    return _current.get() is not None


@contextlib.contextmanager
def decision_budget(budget):
    token = _current.set(budget)
    try:
        yield budget
    finally:
        _current.reset(token)


class _DecisionStream(httpx.SyncByteStream):
    def __init__(self, stream, budget, row, *, streaming=False):
        self.stream, self.budget = stream, budget
        self.row, self.streaming = row, streaming
        # Small explicit caps retain their original allowance; larger caps include SSE framing.
        self.byte_limit = 65536 if row["output_cap"] is None or row["output_cap"] <= 256 else 65536 + row["output_cap"] * 512
        self._line = bytearray()
        self._event_data = bytearray()
        self._skip_lf = False

    def _check_event(self, data):
        try:
            payload = json.loads(data)
        except ValueError:
            return  # The SDK owns non-JSON event parsing, including [DONE].
        self.budget.finish(self.row, payload)

    def _feed_sse(self, chunk):
        # Assemble data at event boundaries; JSON may span multiple data lines.
        # Both buffers are bounded by the checked total response byte allowance.
        for byte in chunk:
            if self._skip_lf and byte == 10:
                self._skip_lf = False
                continue
            self._skip_lf = byte == 13
            if byte not in (10, 13):
                self._line.append(byte)
                continue
            if not self._line:
                if self._event_data:
                    self._check_event(self._event_data[:-1])
                    self._event_data.clear()
            else:
                field, _, value = self._line.partition(b":")
                if field == b"data":
                    if value.startswith(b" "):
                        value = value[1:]
                    self._event_data.extend(value)
                    self._event_data.append(10)
                self._line.clear()

    def __iter__(self):
        size = 0
        done = threading.Event()
        def close_at_boundary():
            while not done.wait(min(0.1, max(0.001, self.budget.deadline - time.monotonic()))):
                try:
                    self.budget.check()
                except DecisionBudgetExceeded:
                    self.stream.close()
                    return
        watcher = threading.Thread(target=close_at_boundary, name="interaction-http-boundary", daemon=True)
        watcher.start()
        try:
            for chunk in self.stream:
                self.budget.check()
                size += len(chunk)
                if size > self.byte_limit:
                    raise DecisionBudgetExceeded("decision response byte cap exhausted")
                if self.streaming:
                    self._feed_sse(chunk)
                yield chunk
            self.budget.check()
        finally:
            done.set()
            self.stream.close()

    def close(self):
        self.stream.close()


class _DecisionHTTPClient(httpx.Client):
    def __init__(self, original, budget, provider):
        super().__init__()
        self.original, self.budget, self.provider = original, budget, provider

    def send(self, request, **kwargs):
        row = self.budget.start(request, self.provider)
        # SDK retries are disabled on a private copy; redirects cannot create hidden paid attempts.
        kwargs["follow_redirects"] = False
        remaining = max(0.001, self.budget.deadline - time.monotonic())
        configured = request.extensions.get("timeout", {})
        request.extensions["timeout"] = {key: min(remaining, configured[key]) if isinstance(configured.get(key), (int, float))
                                          else remaining for key in ("connect", "read", "write", "pool")}
        stream = kwargs.pop("stream", False)
        response = self.original.send(request, stream=True, **kwargs)
        row["status"] = response.status_code
        if 300 <= response.status_code < 400:
            response.close()
            raise DecisionBudgetExceeded("decision redirect not supported")
        response.stream = _DecisionStream(response.stream, self.budget, row,
            streaming="text/event-stream" in response.headers.get("content-type", ""))
        if not stream:
            try:
                response.read()
                self.budget.finish(row, response.json())
            except ValueError:
                pass  # SDK owns non-JSON provider failures; no result can be committed.
        return response


def bounded_create(client, kwargs):
    budget = _current.get()
    if budget is None:
        return client.chat.completions.create(**kwargs)
    budget.check()
    from agent.auxiliary_client import _RELAY_AUX_CALL_CONTEXT
    route = _RELAY_AUX_CALL_CONTEXT.get()
    provider = route.get("provider", "UNKNOWN") if route else "UNKNOWN"
    kwargs = dict(kwargs)
    from agent.auxiliary_client import _CodexCompletionsAdapter
    from agent.codex_responses_adapter import classify_responses_route
    real = getattr(client, "_real_client", client)
    completions = client.chat.completions
    route_flags = classify_responses_route(SimpleNamespace(
        provider=provider or getattr(real, "_hermes_aux_effective_provider", None),
        base_url=str(getattr(real, "base_url", "") or "")))
    uncapped_codex = isinstance(completions, _CodexCompletionsAdapter) and route_flags.is_codex_backend
    cap_key = "max_completion_tokens" if "max_completion_tokens" in kwargs else "max_tokens"
    if uncapped_codex:
        kwargs.pop("max_tokens", None)
        kwargs.pop("max_completion_tokens", None)
        kwargs.pop("max_output_tokens", None)
        cap = None
    else:
        cap = kwargs.get(cap_key, budget.max_output_tokens)
    if not uncapped_codex and (type(cap) is not int or cap <= 0):
        raise DecisionBudgetExceeded("invalid decision output cap")
    remaining = INTERACTION_MAX_OUTPUT_TOKENS - budget.output_reserved
    if not uncapped_codex and remaining <= 0:
        raise DecisionBudgetExceeded("physical output budget exhausted")
    if not uncapped_codex:
        kwargs[cap_key] = min(budget.max_output_tokens, remaining, cap)
    if not callable(getattr(real, "with_options", None)) or not isinstance(getattr(real, "_client", None), httpx.Client):
        raise DecisionBudgetExceeded("selected transport has no bounded native HTTP boundary")
    http = _DecisionHTTPClient(real._client, budget, provider)
    # Native adapters retain translation but use the private SDK retry/HTTP boundary.
    sdk = real.with_options(max_retries=0, http_client=http)
    bounded = sdk
    if real is not client:
        bounded = copy.copy(client)
        bounded._real_client = sdk
        bounded.chat = copy.copy(client.chat)
        bounded.chat.completions = copy.copy(client.chat.completions)
        bounded.chat.completions._client = sdk
    try:
        result = bounded.chat.completions.create(**kwargs)
        usage = getattr(result, "usage", None)
        output = getattr(usage, "completion_tokens", None)
        if type(output) is int and not uncapped_codex and output > kwargs[cap_key]:
            raise DecisionBudgetExceeded("provider exceeded requested output cap")
        return result
    finally:
        http.close()  # Does not close the original pooled client.
