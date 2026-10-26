"""An authenticated new explicit id takes normal API admission; existing scope stays enforced."""
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.session_results import execution_result
from hermes_state_runtime import list_session_admissions


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path, monkeypatch):
    from hermes_cli import _early_recovery
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_early_recovery, 'interrupted_pull_marker', lambda root: tmp_path / 'pull-marker')


@pytest.mark.asyncio
async def test_explicit_id_creates_then_continues_and_rejects_foreign_targets(api, owner, tmp_path, monkeypatch):
    root, neighbor = tmp_path / 'project', tmp_path / 'neighbor'
    root.mkdir()
    neighbor.mkdir()
    api._trusted_project_root = str(root)
    api._api_key = 'synthetic-api-key-long-enough'
    seen = []

    async def handle(event):
        sid = event.source.chat_id
        seen.append((sid, event.text))
        owner.db.append_message(sid, 'user', event.text)
        owner.db.append_message(sid, 'assistant', 'fixture reply')
        execution_result.get()['result'] = {'final_response': 'fixture reply', 'messages': [], 'completed': True}
        return 'fixture reply'

    owner.runner._handle_message = handle

    @web.middleware
    async def auth(request, handler):
        error = api._check_auth(request)
        return error if error is not None else await handler(request)

    app = web.Application(middlewares=[auth])
    app.router.add_post('/v1/chat/completions', api._handle_chat_completions)
    app.router.add_get('/api/sessions', api._handle_list_sessions)
    headers = {'Authorization': 'Bearer ' + api._api_key, 'X-Hermes-Session-Id': 'new-id'}
    async with TestClient(TestServer(app)) as client:
        async def post(text, **changes):
            return await client.post('/v1/chat/completions', headers={**headers, **changes},
                json={'messages': [{'role': 'user', 'content': text}]})
        response = await post('first')
        assert response.status == 200, await response.text()
        assert response.headers['X-Hermes-Session-Id'] == 'new-id'
        row = owner.db.get_session('new-id')
        assert row['source'] == 'api_server'
        assert row['project_root'] == str(root)
        listing = await client.get('/api/sessions', headers=headers)
        assert listing.status == 200, await listing.text()
        assert 'new-id' in [session['id'] for session in (await listing.json())['data']]
        response = await post('second')
        assert response.status == 200, await response.text()
        assert seen == [('new-id', 'first'), ('new-id', 'second')]
        assert [r['payload']['text'] for r in list_session_admissions(owner.db, session_id='new-id', pending_only=False)] == ['first', 'second']
        assert [m['content'] for m in owner.db.get_messages_as_conversation('new-id')] == ['first', 'fixture reply', 'second', 'fixture reply']
        assert owner.db.get_session('new-id')['session_key'] == row['session_key']
        for sid, source, project, session_key in (
                ('non-api', 'cli', root, None), ('wrong-project', 'api_server', neighbor, None),
                ('foreign-owner', 'api_server', root, 'foreign-conversation')):
            owner.db.create_session(sid, source=source, cwd=str(project), project_root=str(project), session_key=session_key)
            before = owner.db.get_session(sid)
            response = await post('must not run', **{'X-Hermes-Session-Id': sid})
            assert response.status in (403, 409), (sid, response.status, await response.text())
            assert owner.db.get_session(sid) == before
            assert list_session_admissions(owner.db, session_id=sid, pending_only=False) == []
        response = await post('unauthorized', Authorization='Bearer wrong')
        assert response.status == 401
        assert len(seen) == 2
