"""HTTP session scope follows the profile's registered selected project, never a query path."""
import httpx
import pytest
from fastapi import FastAPI

from hermes_state import SessionDB


@pytest.mark.asyncio
async def test_registered_project_selection_and_profile_isolation(tmp_path, monkeypatch):
    from hermes_cli import _early_recovery, projects_db
    monkeypatch.setattr(_early_recovery, 'interrupted_pull_marker', lambda root: tmp_path / 'pull-marker')
    from hermes_cli.web_routers import profiles, sessions

    a, b, forged = (tmp_path / name for name in ('server-a', 'selected-b', 'forged'))
    for path in (a, b, forged):
        path.mkdir()
    monkeypatch.chdir(a)
    homes = {p: tmp_path / p for p in ('one', 'two')}
    projects = {}
    for profile, home in homes.items():
        home.mkdir()
        with projects_db.connect_closing(home / 'projects.db') as conn:
            projects[profile] = projects_db.create_project(conn, name=profile, folders=[str(b if profile == 'one' else a)])
            projects_db.set_active(conn, projects[profile])
        with SessionDB(home / 'state.db') as db:
            for suffix, path in (('a', a), ('b', b), ('forged', forged)):
                db.create_session(f'{profile}-{suffix}', source='cli', cwd=str(path), project_root=str(path))
    monkeypatch.setattr(sessions, '_cron_profile_home', lambda p: (p, homes[p]))
    monkeypatch.setattr(sessions, '_open_session_db_for_profile',
                        lambda p, read_only: SessionDB(homes[p] / 'state.db', read_only=read_only))
    monkeypatch.setattr(profiles, '_cron_profile_home', lambda p: (p, homes[p]))
    monkeypatch.setattr(profiles, '_profile_targets', lambda *args: list(homes.items()))
    app = FastAPI()
    app.include_router(sessions.list_router)
    app.include_router(profiles.sessions_router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
        response = await client.get('/api/sessions', params={'profile': 'one', 'cwd_prefix': str(b)})
        assert response.status_code == 200, response.text
        assert response.json()['total'] == 1
        assert [r['id'] for r in response.json()['sessions']] == ['one-b']
        for path in (a, forged, tmp_path / 'unknown'):
            response = await client.get('/api/sessions', params={'profile': 'one', 'cwd_prefix': str(path),
                                                               'project_root': str(path)})
            assert response.status_code == 200, response.text
            assert response.json()['sessions'] == []
            assert response.json()['total'] == 0
        response = await client.get('/api/sessions', params={'profile': 'two'})
        assert [r['id'] for r in response.json()['sessions']] == ['two-a']
        response = await client.get('/api/profiles/sessions', params={'profile': 'two'})
        assert response.status_code == 200, response.text
        assert [r['id'] for r in response.json()['sessions']] == ['two-a']
        response = await client.get('/api/profiles/sessions', params={'profile': 'all'})
        assert response.status_code == 200, response.text
        assert {r['id'] for r in response.json()['sessions']} == {'one-b', 'two-a'}
        with projects_db.connect_closing(homes['one'] / 'projects.db') as conn:
            projects_db.set_active(conn, 'not-registered')
        response = await client.get('/api/sessions', params={'profile': 'one', 'cwd_prefix': str(forged)})
        assert response.status_code == 400, response.text
