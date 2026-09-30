"""RPC-level tests for the generic hidden-session surface (tui_gateway).

Covers the two seams Bot Mode's "sessions are always hidden" policy leans on:

* ``session.set_hidden`` resolves a DURABLE stored session id when no live
  runtime session matches — plugins reconciling sessions they own (Bot Mode's
  hide sweep) hold stored ids for chats that aren't live right now. The old
  live-only lookup failed those with 4001 and the sweep silently no-opped.
* ``session.list`` honors ``include_hidden`` so owning surfaces (the Bots
  pane's per-profile browser) can still enumerate the rows they hid, while
  every default caller keeps the hidden rows dropped.
"""

import logging

import pytest

import tui_gateway.server as srv
import tui_gateway.methods_session  # noqa: F401  (registers the RPC methods)
from hermes_state import SessionDB


@pytest.fixture
def db(tmp_path, monkeypatch):
    database = SessionDB(tmp_path / "state.db")
    project_root = str(tmp_path / "synthetic-project")
    monkeypatch.setattr(srv, "_active_project_root", lambda _params, _db: project_root)
    monkeypatch.setattr(srv, "_get_db", lambda: database)
    try:
        yield database
    finally:
        database.close()


def _call(method: str, params: dict) -> dict:
    return srv._methods[method](1, params)


def _seed(db, sid: str) -> None:
    db.create_session(sid, source="desktop")
    db._conn.execute("UPDATE sessions SET message_count = 1, project_root = ? WHERE id = ?",
                     (str(db.db_path.parent / "synthetic-project"), sid))
    db._conn.commit()


def test_set_hidden_resolves_stored_id_without_live_session(db):
    """A stored (non-live) session id must be hideable — the sweep path."""
    _seed(db, "stored-chat")
    assert srv._find_live_session_by_key("stored-chat") is None

    envelope = _call("session.set_hidden", {"session_id": "stored-chat", "hidden": True})
    assert "error" not in envelope, envelope
    assert envelope["result"]["hidden"] is True
    assert db.get_session("stored-chat")["hidden"] == 1

    # And back — unhide through the same durable path.
    envelope = _call("session.set_hidden", {"session_id": "stored-chat", "hidden": False})
    assert "error" not in envelope, envelope
    assert db.get_session("stored-chat")["hidden"] == 0


def test_set_hidden_unknown_id_still_errors(db):
    envelope = _call("session.set_hidden", {"session_id": "no-such-session", "hidden": True})
    assert envelope.get("error"), envelope




def test_session_list_emits_safe_filter_counts(db, caplog, monkeypatch, tmp_path):
    caplog.set_level(logging.DEBUG, logger="tui_gateway.server")
    root = str(tmp_path / "synthetic-project")
    for sid in ("tui-one", "tui-two", "tui-three"):
        db.create_session(sid, source="tui")
        db.set_session_title(sid, f"PRIVATE_TITLE_{sid}")
        db._conn.execute("UPDATE sessions SET message_count = 1, project_root = ? WHERE id = ?", (root, sid))
    db._conn.commit()
    monkeypatch.setattr(srv, "_active_project_root", lambda params, _db: root)
    envelope = _call("session.list", {"project_root": root})
    assert "error" not in envelope
    assert {row["id"] for row in envelope["result"]["sessions"]} == {"tui-one", "tui-two", "tui-three"}
    record = next((r for r in caplog.records if "session.list diagnostics" in r.getMessage()), None)
    assert record is not None
    assert "raw_count=3" in record.getMessage()
    assert "visible_count=3" in record.getMessage()
    assert "source_counts={'tui': 3}" in record.getMessage()
    assert "PRIVATE_TITLE" not in record.getMessage()
    assert record.levelno == logging.DEBUG
    info = next((r for r in caplog.records if "session.list completed" in r.getMessage()), None)
    assert info is not None
    assert info.levelno == logging.INFO
    assert "output_count=3" in info.getMessage()


def test_session_list_missing_project_authority_logs_reason(db, caplog, monkeypatch):
    caplog.set_level(logging.WARNING, logger="tui_gateway.server")
    monkeypatch.setattr(srv, "_active_project_root", lambda _params, _db: None)

    envelope = _call("session.list", {})

    assert envelope["error"]["code"] == 4008
    assert any("reason=project_context_missing" in r.getMessage() for r in caplog.records)


def test_session_list_include_hidden(db):
    _seed(db, "plain-chat")
    _seed(db, "bot-chat")
    assert db.set_session_hidden("bot-chat", True) is True

    default_rows = _call("session.list", {})["result"]["sessions"]
    assert {s["id"] for s in default_rows} == {"plain-chat"}

    all_rows = _call("session.list", {"include_hidden": True})["result"]["sessions"]
    assert {s["id"] for s in all_rows} == {"plain-chat", "bot-chat"}


def test_session_list_dispatch_accepts_current_session_authority(db):
    """The TUI sends its runtime session id so the server can resolve project authority."""
    response = getattr(srv, "handle_request")({
        "id": "resume-list",
        "method": "session.list",
        "params": {
            "limit": 200,
            "current_session_id": "new-runtime-session",
            "project_root": "/home/user",
        },
    })

    assert isinstance(response, dict)
    assert "error" not in response, response


@pytest.mark.parametrize("source", ["oneshot", "kanban", "tool"])
def test_session_list_hides_internal_sources(db, source):
    """Finite one-shot runs (`hermes -z`, `chat -q`) and other non-conversation rows never reach the
    human picker; interactive rows stay (#112550)."""
    _seed(db, "plain-chat")
    db.create_session("internal-run", source=source)
    db._conn.execute("UPDATE sessions SET message_count = 1, project_root = ? WHERE id = ?",
                     (str(db.db_path.parent / "synthetic-project"), "internal-run"))
    db._conn.commit()

    rows = _call("session.list", {})["result"]["sessions"]
    assert {s["id"] for s in rows} == {"plain-chat"}
