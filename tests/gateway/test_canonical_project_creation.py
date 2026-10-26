"""Canonical creation must carry trusted project ownership into its atomic row."""
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_cli_create_retry_resume_and_project_listing(tmp_path, monkeypatch):
    from hermes_cli import _early_recovery
    # A linked source checkout stores its update marker in the parent install's .git.
    # Bootstrap recovery is unrelated to this test and must probe only its fixture.
    monkeypatch.setattr(_early_recovery, 'interrupted_pull_marker', lambda root: tmp_path / 'pull-marker')
    from gateway import run
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.session_authority import initialize_session_authority
    from gateway.session_controls import AuthorityConnection

    home = tmp_path / 'home'
    home.mkdir()
    (home / 'config.yaml').write_text('model: {default: fixture, provider: custom}\n')
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(run, '_hermes_home', home)
    store = SessionStore(tmp_path / 'sessions', GatewayConfig())
    runner = SimpleNamespace(session_store=store, _session_db=store._db, adapters={}, _draining=False,
                             _cached_agent_for=lambda route: None)
    runner._adapter_for_source = lambda source: runner.adapters.get(source.platform)
    authority = await initialize_session_authority(runner, profile_id=str(home), instance_id='first')
    connection = AuthorityConnection(authority, object(), {'user_id': 'human'})

    async def call(method, **params):
        reply = await connection.dispatch({'id': 1, 'method': method, 'params': params})
        assert 'result' in reply, reply
        return reply['result']

    a, b = tmp_path / 'project-a', tmp_path / 'project-b'
    a.mkdir()
    b.mkdir()
    try:
        request = dict(request_id='a', source='cli', cwd=str(a / '..' / a.name), row_cwd='')
        first = await call('session.create', **request)
        other = await call('session.create', request_id='b', source='cli', cwd=str(b))
        sid = first['session_id']
        # row_cwd is display-only and may be empty; execution policy still owns project A.
        row = authority.db.get_session(sid)
        assert row['cwd'] is None
        assert row['project_root'] == str(a.resolve())
        assert (await call('session.create', **request))['session_id'] == sid
        assert (await call('session.resume', session_id=sid))['session_id'] == sid
        assert authority.db.get_session(sid)['project_root'] == str(a.resolve())
        authority.db.create_session('unowned', source='cli')
        ids = {r['id'] for r in authority.db.list_sessions_rich(project_root=str(a.resolve()))}
        assert ids == {sid}, (ids, other)
        assert authority.db.session_count(project_root=str(a.resolve())) == 1
    finally:
        await connection.close()
        store._db.close()
