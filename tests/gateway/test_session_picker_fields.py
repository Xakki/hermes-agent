"""``session.list`` carries what a session picker shows, and filters by folder with ``hermes -c``'s rule (#150091)."""
import asyncio
import subprocess
from types import SimpleNamespace

import pytest

from tests.fixtures.storage_read import session_row
from tests.fixtures.storage_seed import seed_messages, seed_session
from tests.gateway.test_session_mutation_model_busy import _owner


@pytest.mark.asyncio
async def test_live_rows_carry_picker_fields_and_filter_by_workspace(tmp_path, monkeypatch):
    from gateway.session_local_recovery import transcript_target

    async def handle(event):
        return 'ok'
    store, db, authority, owner, ref = _owner(tmp_path, monkeypatch, handle)
    try:
        target = transcript_target(authority, ref)
        from agent.context_compressor import SUMMARY_PREFIX
        seed_messages(db, target, {'role': 'user', 'content': 'where is the bug?'},
                      {'role': 'assistant', 'content': 'in the parser'},
                      {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'c1', 'type': 'function',
                       'function': {'name': 'read_file', 'arguments': '{}'}}]},
                      {'role': 'assistant', 'content': f'{SUMMARY_PREFIX}\n## Goal\nnot a reply'})

        async def listing(**params):
            reply = await owner.dispatch({'id': 1, 'method': 'session.list', 'params': params})
            assert 'result' in reply, reply
            return reply['result']['sessions']

        stamp = session_row(db, target)['started_at'] + 60
        db.sessions.touch_activity(target, at=stamp)
        [bare] = await listing()
        assert 'preview' not in bare and 'last_reply' not in bare  # a live-list poll pays for none of it
        [row] = await listing(picker=True)
        assert row['preview'] == 'where is the bug?'
        assert row['last_reply'] == 'in the parser'
        assert (row['cwd'], row['workspace'], row['pinned']) == (str(tmp_path), str(tmp_path), False)
        assert row['last_activity_at'] == stamp
        assert row['last_active_at'] >= session_row(db, target)['started_at']
        assert row['git_repo_root'] is None
        assert await listing(workspace_cwd=str(tmp_path.parent)) == []
        assert [r['id'] for r in await listing(workspace_cwd=str(tmp_path))] == [ref.session_id]
        assert [r['session_id'] for r in await listing(workspace=str(tmp_path), picker=True)] == [ref.session_id]
        assert await listing(workspace=str(tmp_path / 'elsewhere')) == []
        assert 'error' in await owner.dispatch({'id': 2, 'method': 'session.list',
                                                'params': {'title': 'x', 'workspace': str(tmp_path)}})
    finally:
        await owner.close()
        store.close_all_db_handles()


@pytest.mark.asyncio
async def test_stored_rows_carry_picker_fields_and_filter_by_repo_root(tmp_path, monkeypatch):
    from gateway import run
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.session_authority import initialize_session_authority
    from gateway.session_controls import AuthorityConnection

    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(run, '_load_gateway_config', lambda: {'platform_toolsets': {'cli': []}})
    store = SessionStore(tmp_path / 'sessions', GatewayConfig())
    db = store.persistence
    repo = tmp_path / 'repo'
    for sid, cwd in (('in-repo', repo / 'pkg'), ('elsewhere', tmp_path / 'other'),
                     ('elsewhere2', tmp_path / 'other'), ('nested', tmp_path / 'other' / 'nested'),
                     ('git-at-cwd', tmp_path / 'other')):
        seed_session(db, sid, 'cli', model='m', cwd=str(cwd))
        seed_messages(db, sid, {'role': 'user', 'content': f'{sid} question'},
                      {'role': 'assistant', 'content': f'{sid} answer'})
        db.sessions.end(sid, 'cli_exit')
    db.sessions.update_cwd('in-repo', str(repo / 'pkg'), git_branch='main', git_repo_root=str(repo))
    db.sessions.update_cwd('git-at-cwd', str(tmp_path / 'other'), git_branch='main', git_repo_root=str(tmp_path))
    runner = SimpleNamespace(session_store=store, _session_db=db, adapters={}, _draining=False,
                             _cached_agent_for=lambda route: None)
    runner._adapter_for_source = lambda source: runner.adapters.get(source.platform)
    authority = await initialize_session_authority(runner, profile_id=str(tmp_path), instance_id='owner')
    native = AuthorityConnection(authority, object(), {'provider': 'local', 'user_id': 'native',
        'profile_id': str(tmp_path), 'instance_id': 'owner'})
    try:
        reply = await native.dispatch({'id': 1, 'method': 'session.list',
                                       'params': {'scope': 'stored', 'workspace': str(repo), 'picker': True}})
        assert 'result' in reply, reply
        [row] = reply['result']['sessions']
        assert (row['id'], row['workspace'], row['git_branch'], row['cwd']) == (
            'in-repo', str(repo), 'main', str(repo / 'pkg'))
        exact = await native.dispatch({'id': 3, 'method': 'session.list', 'params': {
            'scope': 'stored', 'workspace_cwd': str(tmp_path / 'other'), 'picker': True}})
        assert 'result' in exact, exact
        assert {r['id'] for r in exact['result']['sessions']} == {'elsewhere', 'elsewhere2'}
        assert exact['result']['sessions'][0]['git_repo_root'] is None
        assert row['git_repo_root'] == str(repo)
        assert (row['preview'], row['last_reply']) == ('in-repo question', 'in-repo answer')
        ambiguous = await native.dispatch({'id': 4, 'method': 'session.list', 'params': {
            'workspace': str(repo), 'workspace_cwd': str(tmp_path / 'other')}})
        assert 'error' in ambiguous
        page = await native.dispatch({'id': 5, 'method': 'session.list', 'params': {
            'scope': 'stored', 'workspace_cwd': str(tmp_path / 'other'), 'query': 'elsewhere', 'limit': 1}})
        assert 'result' in page, page
        cursor = page['result']['next_cursor']
        assert cursor
        next_page = await native.dispatch({'id': 7, 'method': 'session.list', 'params': {
            'scope': 'stored', 'query': 'elsewhere', 'workspace_cwd': str(tmp_path / 'other'), 'cursor': cursor}})
        assert 'result' in next_page, next_page
        assert {r['id'] for r in page['result']['sessions'] + next_page['result']['sessions']} == {
            'elsewhere', 'elsewhere2'}
        assert next_page['result']['next_cursor'] is None
        wrong_scope = await native.dispatch({'id': 6, 'method': 'session.list', 'params': {
            'scope': 'stored', 'query': 'elsewhere', 'workspace_cwd': str(repo), 'cursor': cursor}})
        assert 'error' in wrong_scope
        everything = await native.dispatch({'id': 2, 'method': 'session.list', 'params': {'scope': 'stored'}})
        assert {r['id'] for r in everything['result']['sessions']} == {'in-repo', 'elsewhere', 'elsewhere2', 'nested', 'git-at-cwd'}
    finally:
        await native.close()
        store.close_all_db_handles()


@pytest.mark.asyncio
async def test_session_info_reports_the_gateway_session_workspace(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    cwd = repo / 'pkg'
    cwd.mkdir(parents=True)
    await asyncio.to_thread(subprocess.run, ['git', 'init', '-q', str(repo)], check=True)

    async def handle(event):
        return 'ok'

    store, _, _, owner, ref = _owner(cwd, monkeypatch, handle)
    try:
        info = await owner.dispatch({'id': 1, 'method': 'session.info', 'params': {'session_id': ref.session_id}})
        assert 'result' in info, info
        assert (info['result']['cwd'], info['result']['git_repo_root']) == (str(cwd), str(repo))
    finally:
        await owner.close()
        store.close_all_db_handles()
