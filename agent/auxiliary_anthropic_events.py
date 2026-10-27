"""Anthropic auxiliary stream progress and host-deadline callbacks."""
from __future__ import annotations

from typing import Any, Callable


def event_has_content(event: Any) -> bool:
    from agent.auxiliary_client import _field

    event_type = _field(event, "type")
    if event_type == "content_block_delta":
        delta = _field(event, "delta")
        return any(bool(_field(delta, f)) for f in ("text", "thinking", "partial_json", "signature", "citation"))
    if event_type == "content_block_start":
        block = _field(event, "content_block")
        return _field(block, "type") == "tool_use" and any(bool(_field(block, f)) for f in ("id", "name"))
    return False


def stream_event_hook() -> Callable[[Any], None]:
    """Keepalives cannot prolong a stalled summary; cancellation and host deadline still win."""
    from agent import auxiliary_client as auxiliary

    host_deadline = auxiliary._current_aux_stream_deadline()
    started = auxiliary.time.monotonic()

    def on_event(event: Any) -> None:
        if event_has_content(event):
            auxiliary._notify_aux_provider_response()
        else:
            auxiliary._notify_aux_timing_response()
        if auxiliary._aux_interrupt_cancel_requested():
            raise auxiliary.AuxiliaryExplicitCancellation()
        if host_deadline is not None and auxiliary.time.monotonic() >= host_deadline:
            raise TimeoutError(
                "Anthropic auxiliary stream timed out at the host compression "
                f"deadline after {auxiliary.time.monotonic() - started:.0f}s (the caller already stopped waiting)")

    return on_event
