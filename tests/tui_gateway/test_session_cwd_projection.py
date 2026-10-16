"""Recorded cwd survives legacy picker projections without ambient fallback."""
import time

import pytest


@pytest.mark.parametrize('cwd', [None, '', '/stored/work', r'C:\Users\dev\workspace'])
def test_stored_summary_uses_tip_cwd(cwd):
    from tui_gateway.methods_session import _session_row_summary
    from tui_gateway.contracts.sessions import SessionListRow

    root = {'id': 'root', 'cwd': '/root/work'}
    tip = {'id': 'tip', 'cwd': cwd}
    result = _session_row_summary(root, tip_row=tip, resolved_id='tip')
    assert result['cwd'] == cwd
    assert SessionListRow.model_validate(result).cwd == cwd
    assert _session_row_summary({'id': 'unknown'})['cwd'] is None


@pytest.mark.parametrize('cwd', [None, '', '/live/work', r'D:\projects\live'])
def test_live_item_uses_only_its_session_cwd(cwd, monkeypatch):
    from tui_gateway import session_registry as registry
    from tui_gateway.contracts.sessions import SessionActiveItem

    # These functions are normally bound into server globals. Keep the projection
    # seam isolated from the unrelated server bootstrap and model resolution.
    dependencies = {
        'time': time,
        '_session_lookup_key': lambda session, **kwargs: 'stored-key',
        '_session_live_status': lambda sid, session: 'idle',
        '_inflight_snapshot': lambda session: None,
        '_queued_prompt_snapshot': lambda session: None,
        '_resolve_model': lambda: 'fixture-model',
        '_session_live_title': lambda session, key: 'Fixture',
    }
    for name, value in dependencies.items():
        monkeypatch.setattr(registry, name, value, raising=False)
    session = {'cwd': cwd, 'created_at': 1}
    result = registry._session_live_item('live', session)
    assert result['cwd'] == cwd
    assert SessionActiveItem.model_validate(result).cwd == cwd
    assert registry._session_live_item('unknown', {'created_at': 1})['cwd'] is None


@pytest.mark.parametrize('resolved_id', [None, 'tip'])
def test_bound_summary_keeps_server_live_count_patch_seam(resolved_id, monkeypatch):
    from tui_gateway import server

    database = object()
    calls = []

    def live_count(db, session_id):
        calls.append((db, session_id))
        return {'live_message_count': 7}

    monkeypatch.setattr(server, '_live_count_field', live_count)
    result = server._session_row_summary(
        {'id': 'root', 'cwd': '/root'}, tip_row={'cwd': '/tip'},
        resolved_id=resolved_id, db=database)
    assert calls == [(database, resolved_id or 'root')]
    assert result['live_message_count'] == 7
    assert result['cwd'] == '/tip'
