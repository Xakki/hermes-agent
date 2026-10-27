"""Request-scope and header policy shared by the API server's route handlers."""
from __future__ import annotations

import os
import re
from typing import Any, Optional


class APIServerRequestPolicyMixin:
    def _draining_response(self):
        """Return a retryable response while the gateway drains existing work."""
        from gateway.platforms.api_server import _error_response

        if not self._gateway_is_draining():
            return None
        return _error_response(
            "Gateway is draining existing work; retry shortly.", 503, code="gateway_draining",
            headers={"Retry-After": "1"})

    @staticmethod
    def _resolve_trusted_project_root(configured: Any = None) -> Optional[str]:
        try:
            from tui_gateway.git_probe import canonical_project_root
            return canonical_project_root(configured or os.getcwd())
        except (ImportError, OSError, ValueError, RuntimeError):
            return None

    def _session_scope_error(self, session: dict[str, Any]):
        from gateway.platforms.api_server import web, _openai_error

        active_root = getattr(self, "_trusted_project_root", None)
        if not active_root:
            return web.json_response(_openai_error("project context required", code="project_context_required"), status=403)
        try:
            from tui_gateway.git_probe import canonical_project_root
            session_root = canonical_project_root(
                session.get("project_root") or session.get("cwd") or os.getcwd()
            )
        except (ImportError, OSError, ValueError, RuntimeError):
            session_root = None
        if session_root != active_root:
            return web.json_response(_openai_error("Session is outside the active project", code="session_outside_project"), status=403)
        return None

    @staticmethod
    def _parse_cors_origins(value: Any) -> tuple[str, ...]:
        """Normalize configured CORS origins into a stable tuple."""
        if not value:
            return ()
        items = (
            value.split(",") if isinstance(value, str)
            else value if isinstance(value, (list, tuple, set)) else [str(value)])
        return tuple(str(item).strip() for item in items if str(item).strip())

    def _chat_session_header_error(self, session_id: str):
        from gateway.platforms.api_server import logger, _error_response, _invalid_request
        from gateway.session import _is_path_unsafe

        if not self._api_key:
            logger.warning(
                "Session continuation via X-Hermes-Session-Id rejected: "
                "no API key configured.  Set API_SERVER_KEY to enable session continuity.")
            return _error_response("Session continuation requires API key authentication. "
                                   "Configure API_SERVER_KEY to enable this feature.", 403)
        if re.search(r'[\r\n\x00]', session_id) or _is_path_unsafe(session_id):
            return _invalid_request("Invalid session ID")
        if len(session_id) > self._MAX_SESSION_HEADER_LEN:
            return _invalid_request("Session ID too long")
        return None
