"""Exercise the post-settlement offer on the real local gateway and model path."""
import asyncio
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import threading

import pytest

from tests.gateway.fixtures.local_recovery_probe import Model, child_env, daemon, rpc, websocket


@pytest.mark.platforms('linux')
def test_local_turn_offers_a_custom_action_and_executes_it_as_text(tmp_path):
    root = Path(__file__).resolve().parents[2]
    home, user = tmp_path / 'state', tmp_path / 'user'
    home.mkdir(mode=0o700)
    user.mkdir()
    prompt = 'Summarize this task without running /diff.'
    with ThreadingHTTPServer(('127.0.0.1', 0), Model) as peer:
        peer.requests = []
        server = threading.Thread(target=peer.serve_forever, daemon=True)
        server.start()
        url = f'http://127.0.0.1:{peer.server_port}/v1'
        (home / 'config.yaml').write_text(json.dumps({
            'gateway': {'multiplex_profiles': False},
            'model': {'provider': 'custom', 'default': 'fixture', 'base_url': url},
            'auxiliary': {'title_generation': {'enabled': False}},
            'interaction': {'menu': [{'id': 'summarize', 'label': 'Summarize', 'handler': 'prompt', 'prompt': prompt}]},
        }))
        env = child_env()
        env.update(HOME=str(user), USERPROFILE=str(user), HERMES_HOME=str(home), PYTHONPATH=str(root),
                   OPENAI_API_KEY='loopback-only', OPENAI_BASE_URL=url)

        async def event(ws, kind, sid):
            async with asyncio.timeout(20):
                while True:
                    frame = json.loads(await ws.recv()).get('params', {})
                    if frame.get('type') == kind and frame.get('session_id') == sid:
                        return frame['payload']

        async def probe(desc):
            async with websocket(home, desc) as ws:
                async def create(key):
                    result = await rpc(ws, 'session.create', request_id=key, source='tui', cwd=str(home), toolsets=[])
                    return result['result']['session_id']
                sid, other = await create('menu'), await create('other')
                accepted = await rpc(ws, 'prompt.submit', session_id=sid, input_id='first', text='FIRST_MENU_TURN',
                                     clarify_batch=True)
                assert accepted['result']['status'] == 'queued'
                offer = await event(ws, 'interaction.next_action', sid)
                assert 'summarize' in [action['id'] for action in offer['actions']]
                args = {'offer_id': offer['id'], 'action_id': 'summarize'}
                refused = await rpc(ws, 'interaction.next_action.respond', session_id=other, **args)
                assert refused['error']['message'] == 'stale_generation'
                chosen = await rpc(ws, 'interaction.next_action.respond', session_id=sid, **args)
                assert 'result' in chosen, chosen
                final = await event(ws, 'message.complete', sid)
                assert final['status'] == 'complete' and prompt in final['text'], final
                again = await rpc(ws, 'interaction.next_action.respond', session_id=sid, **args)
                assert again['error']['message'] == 'stale_generation'

        try:
            with daemon(root, home, env, barrier=False) as (_, desc):
                asyncio.run(probe(desc))
            assert sum(next((msg['content'] for msg in reversed(body['messages'])
                             if msg['role'] == 'user'), None) == prompt for body in peer.requests) == 1
        finally:
            peer.shutdown()
            server.join(timeout=5)
