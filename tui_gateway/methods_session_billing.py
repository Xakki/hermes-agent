"""Billing result-envelope helpers, rebound onto the TUI server's namespace."""
from .method_ctx import bind_module


def _billing_call(rid, fn, extra: dict | None = None) -> dict:
    """Portal call → ok; BillingError → serialized envelope, else generic; ``extra`` rides both ERROR envelopes."""
    from hermes_cli.nous_billing import BillingError
    try:
        return _ok(rid, fn())
    except BillingError as exc:
        return _ok(rid, {**_serialize_billing_error(exc), **(extra or {})})
    except Exception as exc:
        return _ok(rid, {"ok": False, "error": "error", "message": str(exc), **(extra or {})})


def _billing_invalid(rid, message: str, error: str = "invalid_request") -> dict:
    return _ok(rid, {"ok": False, "error": error, "message": message})


def _billing_pick(result: dict, **fields) -> dict:
    """``{"ok": True, <snake>: result[<camel>], ...}`` in ``fields`` order."""
    return {"ok": True, **{key: result.get(src) for key, src in fields.items()}}


def _billing_pending_change(result: dict) -> dict:
    return {"ok": True, "message": result.get("message"), "payload": result}


def register(server) -> None:
    bind_module(globals(), server)
