"""Lifecycle-hook payloads for ``AIAgent`` API requests.

JSON-safe coercion, secret-key redaction, size caps, and the ``api_request_error`` hook dispatch.
Extracted from ``run_agent.py``; every method resolves through ``AIAgent``'s MRO unchanged.
"""
import json
import os
import re
import time
from contextlib import suppress
from types import SimpleNamespace
from typing import Any, Dict, Optional

from agent.usage_pricing import normalize_usage

_SENSITIVE_HOOK_KEYS = {
    "apikey", "authorization", "proxyauthorization", "cookie", "cookies", "setcookie",
    "headers", "extraheaders", "requestheaders", "httpheaders",
    "token", "accesstoken", "refreshtoken", "oauthtoken", "idtoken", "sessiontoken",
    "clientsecret", "password", "passwd", "secret", "secretkey", "secretaccesskey",
    "privatekey", "credentials", "passwords", "secrets", "privatekeys",
    "secretkeys", "apikeys", "apitokens", "accesstokens", "refreshtokens",
    "authtokens", "oauthtokens", "bearertokens", "sessiontokens", "idtokens",
    "clientsecrets",
}
_REQUEST_DETAIL_MAX_CHARS = 128_000
_AUTH_SCHEME_VALUE_RE = re.compile(r"(?i)\b(Bearer|Basic)\s+([^\s\"']+)")


def _model_dump(value: Any) -> Any:
    """``value.model_dump(mode="json")`` with graceful degradation for older pydantic signatures.

    warnings=False: pydantic UserWarnings on generic-union SDK models would leak to the terminal.
    """
    try:
        return value.model_dump(mode="json", warnings=False)
    except TypeError:
        try:
            return value.model_dump(mode="json")
        except TypeError:
            return value.model_dump()


class ApiRequestHooksMixin:
    """Hook payload sanitising + ``api_request_error`` dispatch (see module docstring)."""

    def _credential_identity_for_api_request(self) -> Dict[str, str]:
        """Return a locked snapshot for the exact selected pool entry, never a token/key."""
        pool = getattr(self, "_credential_pool", None)
        entry_id = getattr(self, "_credential_pool_entry_id", None)
        lookup = getattr(pool, "identity_for_entry_id", None)
        if not callable(lookup):
            return {}
        try:
            snapshot = lookup(entry_id, api_key_hint=getattr(self, "api_key", None))
        except Exception:
            return {}
        if not isinstance(snapshot, dict):
            return {}
        return {
            key: snapshot[key]
            for key in ("credential_id", "account_name")
            if isinstance(snapshot.get(key), str)
        }

    def _usage_summary_for_api_request_hook(self, response: Any) -> Optional[Dict[str, Any]]:
        """Token buckets for ``post_api_request`` plugins (no raw ``response`` object)."""
        if response is None:
            return None
        raw_usage = getattr(response, "usage", None)
        if not raw_usage:
            return None
        from dataclasses import asdict

        cu = normalize_usage(raw_usage, provider=self.provider, api_mode=self.api_mode)
        summary = asdict(cu)
        summary.pop("raw_usage", None)
        summary["prompt_tokens"] = cu.prompt_tokens
        summary["total_tokens"] = cu.total_tokens
        return summary

    @staticmethod
    def _hook_payload_max_chars() -> int:
        raw = os.getenv("HERMES_PLUGIN_PAYLOAD_MAX_CHARS", "50000")
        try:
            return max(1000, int(raw))
        except (TypeError, ValueError):
            return 50000

    @staticmethod
    def _is_sensitive_hook_key(key: Any, value: Any = None) -> bool:
        if not isinstance(key, str):
            return False
        normalized = "".join(char for char in key.lower() if char.isalnum())
        if normalized in _SENSITIVE_HOOK_KEYS or normalized.endswith((
            "apikey", "apikeys", "token", "auth", "secret", "secrets", "secretkey",
            "secretkeys", "privatekeys", "password", "passwords", "passwd",
            "credential", "credentials",
        )):
            return True
        # Numeric usage/max_tokens are counts; plural token fields containing
        # strings or structures can hold credential values instead.
        return normalized.endswith("tokens") and not (
            value is None or type(value) in (int, float)
        )

    @staticmethod
    def _redact_hook_value(value: str) -> str:
        """Scrub recognizable credential values before a hook sees request options."""
        try:
            from agent.redact import redact_sensitive_text
            safe = redact_sensitive_text(value, force=True, redact_url_credentials=True)
            return _AUTH_SCHEME_VALUE_RE.sub(r"\1 <redacted>", safe)
        except Exception:
            return "<redaction failed>"

    @classmethod
    def _hook_jsonable(
        cls, value: Any, *, depth: int = 0, max_depth: int = 8, max_string: int = 8000,
        max_sequence: int = 200, redact_values: bool = False,
    ) -> Any:
        if depth > max_depth:
            return f"<{type(value).__name__} depth limit>"
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            safe = cls._redact_hook_value(value) if redact_values else value
            if len(safe) > max_string:
                return safe[:max_string] + f"...[truncated {len(safe) - max_string} chars]"
            return safe
        if isinstance(value, (bytes, bytearray)):
            return f"<{len(value)} bytes>"

        def recurse(item):
            return cls._hook_jsonable(
                item, depth=depth + 1, max_depth=max_depth, max_string=max_string,
                max_sequence=max_sequence, redact_values=redact_values,
            )

        if isinstance(value, dict):
            out: Dict[str, Any] = {}
            for idx, (key, item) in enumerate(value.items()):
                if idx >= max_sequence:
                    out["_truncated_items"] = len(value) - max_sequence
                    break
                str_key = cls._redact_hook_value(str(key)) if redact_values else str(key)
                out[str_key] = "<redacted>" if cls._is_sensitive_hook_key(str_key, item) else recurse(item)
            return out
        if isinstance(value, (list, tuple, set)):
            seq = list(value)
            out = [recurse(item) for item in seq[:max_sequence]]
            if len(seq) > max_sequence:
                out.append({"_truncated_items": len(seq) - max_sequence})
            return out
        with suppress(Exception):
            if hasattr(value, "model_dump"):
                return recurse(_model_dump(value))
        with suppress(Exception):
            from dataclasses import asdict, is_dataclass
            if is_dataclass(value):
                return recurse(asdict(value))
        if isinstance(value, SimpleNamespace):
            return recurse(vars(value))
        if hasattr(value, "__dict__"):
            with suppress(Exception):
                return recurse({k: v for k, v in vars(value).items() if not str(k).startswith("_")})
        text = str(value)
        return (cls._redact_hook_value(text) if redact_values else text)[:max_string]

    @classmethod
    def _sanitize_hook_payload(
        cls, value: Any, *, max_chars: Optional[int] = None,
        max_string: int = 8000, max_sequence: int = 200, max_depth: int = 8,
        reduce_large: bool = True, redact_values: bool = False,
    ) -> Any:
        """JSON-able payload under the size cap; mark oversized views as truncated."""
        limit = max_chars if max_chars is not None else cls._hook_payload_max_chars()
        encoded = ""
        attempts = ({"max_string": max_string, "max_sequence": max_sequence,
                     "max_depth": max_depth, "redact_values": redact_values},)
        if reduce_large:
            attempts += ({"max_string": 1000, "max_sequence": 50,
                          "redact_values": redact_values},)
        for caps in attempts:
            payload = cls._hook_jsonable(value, **caps)
            try:
                encoded = json.dumps(payload, ensure_ascii=False, default=str)
            except Exception:
                return "<serialization failed>" if redact_values else str(payload)[:limit]
            if len(encoded) <= limit:
                return payload
        return {
            "_truncated": True, "original_type": type(value).__name__, "preview": encoded[:limit]
        }

    def _api_request_payload_for_hook(self, api_kwargs: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        body = {
            key: value
            for key, value in (api_kwargs or {}).items()
            if key not in {"timeout", "http_client"}
        }
        return self._sanitize_hook_payload({"method": "POST", "body": body}, redact_values=True)

    def _api_request_options_for_hook(self, api_kwargs: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Bounded redacted non-message options for full request observability.

        The ordinary ``request`` observer view retains its small legacy caps.
        Headers never belong in this larger view, even when no credential-like
        key is present inside them. Message/system content is already supplied
        separately by the pre-request hook.
        """
        excluded = {
            "timeout", "http_client", "messages", "input", "system", "instructions",
            "headers", "extra_headers", "request_headers", "http_headers",
        }
        options = {
            key: value for key, value in (api_kwargs or {}).items()
            if str(key).lower().replace("-", "_") not in excluded
        }
        return self._sanitize_hook_payload(
            options, max_chars=_REQUEST_DETAIL_MAX_CHARS,
            max_string=_REQUEST_DETAIL_MAX_CHARS, max_sequence=_REQUEST_DETAIL_MAX_CHARS,
            max_depth=30, reduce_large=False, redact_values=True,
        )

    def _api_response_payload_for_hook(
        self, response: Any, assistant_message: Any, *, finish_reason: Optional[str]
    ) -> Dict[str, Any]:
        # Raw provider SDK tool_call objects are handed to the sanitizer on purpose; `_hook_jsonable` must
        # keep normalising them (model_dump / __dict__ / dataclass) or subscribers get str() blobs.
        tool_calls = getattr(assistant_message, "tool_calls", None) or []
        return self._sanitize_hook_payload(
            {
                "model": getattr(response, "model", None),
                "finish_reason": finish_reason,
                "assistant_message": {
                    "role": getattr(assistant_message, "role", "assistant"),
                    "content": getattr(assistant_message, "content", None),
                    "tool_calls": tool_calls,
                },
                "usage": self._usage_summary_for_api_request_hook(response),
            },
            redact_values=True,
        )

    def _invoke_api_request_error_hook(
        self, *, task_id: str, turn_id: str, api_request_id: str, api_call_count: int,
        api_start_time: float, api_kwargs: Optional[Dict[str, Any]], error_type: str,
        error_message: str, status_code: Optional[int] = None, retry_count: Optional[int] = None,
        max_retries: Optional[int] = None, retryable: Optional[bool] = None,
        reason: Optional[str] = None, credential_identity: Optional[Dict[str, str]] = None,
        api_mode: Optional[str] = None,
    ) -> None:
        # Lazy module import (not from-import) so tests can replace lifecycle dispatch at this call site.
        with suppress(Exception):
            from hermes_cli import lifecycle as _lifecycle
            if not _lifecycle.has_hook("api_request_error"):
                return
            ended_at = time.time()
            _lifecycle.invoke_hook(
                "api_request_error",
                task_id=task_id,
                turn_id=turn_id,
                api_request_id=api_request_id,
                session_id=self.session_id or "",
                platform=self.platform or "",
                model=self.model,
                provider=self.provider,
                base_url=self.base_url,
                api_mode=api_mode if api_mode is not None else getattr(self, "api_mode", None),
                **(credential_identity or {}),
                api_call_count=api_call_count,
                api_duration=ended_at - api_start_time,
                started_at=api_start_time,
                ended_at=ended_at,
                status_code=status_code,
                retry_count=retry_count,
                max_retries=max_retries,
                retryable=retryable,
                reason=reason,
                error={"type": error_type, "message": error_message},
                request=self._api_request_payload_for_hook(api_kwargs),
            )
