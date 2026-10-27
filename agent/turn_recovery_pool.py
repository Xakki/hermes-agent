"""Credential-pool and paid-entitlement recovery helpers for the turn recovery ladder.

Split from ``agent/turn_recovery.py`` (code-health file-size ratchet): what the
credential pool sees about a failure, and the Nous paid-entitlement refresh rung.
"""
from __future__ import annotations

import logging
from typing import Any

from agent.error_classifier import FailoverReason
from agent.turn_retry_state import TurnRetryState

logger = logging.getLogger("agent.conversation_loop")


def _pool_error_context(agent, classified: Any, error_context: Any) -> Any:
    """What the credential pool sees: a plan limit (usage-window 402) benches only the refused model on the
    credential, until its reset; every other failure passes the error context through unchanged."""
    if not (getattr(classified, "error_context", None) or {}).get("usage_window"):
        return error_context
    return {**(error_context if isinstance(error_context, dict) else {}), "usage_window": True,
            **_usage_window_reset_context(agent)}


def _usage_window_reset_context(agent) -> dict[str, float]:
    """``{"reset_at": epoch s}`` for a usage-window 402: when the refused allowance frees up, from the usage
    headers the 402 carried (the API's error body has no reset). Empty when the state names none, so the
    pool keeps its default bench."""
    try:
        from agent.credits_tracker import window_stop_resets_at_ms
        from agent.plan_limits import current_model_is_top_tier
        state = getattr(agent, "_credits_state", None)
        resets_ms = (window_stop_resets_at_ms(state, model_is_top_tier=current_model_is_top_tier(agent))
                     if state is not None else None)
    except Exception:
        logger.debug("usage-window reset unavailable for the pool", exc_info=True)
        return {}
    return {"reset_at": resets_ms / 1000} if isinstance(resets_ms, (int, float)) and resets_ms > 0 else {}


def _refresh_paid_entitlement(agent: Any, classified: Any, retry: TurnRetryState) -> bool:
    """Refresh Nous paid-entitlement credentials on a billing 402. A usage-window 402 is the
    plan's own limit: refreshing credentials cannot lift it, so it never triggers a refresh."""
    from agent.conversation_loop import _is_nous_inference_route
    from agent.turn_recovery import _try_refresh_nous_paid_entitlement_credentials, _vlines

    if (classified.reason != FailoverReason.billing
            or (getattr(classified, "error_context", None) or {}).get("usage_window")
            or not _is_nous_inference_route(getattr(agent, "provider", "") or "", getattr(agent, "base_url", "") or "")
            or retry.nous_paid_entitlement_refresh_attempted):
        return False
    retry.nous_paid_entitlement_refresh_attempted = True
    if not _try_refresh_nous_paid_entitlement_credentials(agent):
        return False
    _vlines(agent, "🔐 Nous paid access verified — refreshed runtime credentials and retrying request...")
    return True
