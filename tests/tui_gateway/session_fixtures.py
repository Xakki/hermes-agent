"""Session data fixtures for RPC consumer tests."""
import threading
import types

from hermes_state import SessionDB


class StoredSessionDB:
    """Use the real JSON metadata reader over the fake's explicit stored rows."""

    get_session_model_config_value = SessionDB.get_session_model_config_value


def session_data(agent=None, **extra):
    cwd = extra.get("cwd")
    extra.setdefault("project_root", cwd)
    return {
        "agent": agent if agent is not None else types.SimpleNamespace(),
        "session_key": "session-key",
        "history": [],
        "history_lock": threading.Lock(),
        "history_version": 0,
        "running": False,
        "attached_images": [],
        "image_counter": 0,
        "cols": 80,
        "slash_worker": None,
        "show_reasoning": False,
        "tool_progress_mode": "all",
        **extra,
    }
