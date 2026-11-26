"""An ordinary greeting on the native TUI route does not open a decision card."""
import asyncio
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import threading

import pytest

from tests.gateway.fixtures.local_recovery_probe import Model, child_env, daemon, rpc, websocket


@pytest.mark.platforms('linux')
def test_local_greeting_does_not_offer_an_idle_action(tmp_path):
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
                sid = await create('greeting')
                accepted = await rpc(ws, 'prompt.submit', session_id=sid, input_id='first', text='привет',
                                     clarify_batch=True)
                assert accepted['result']['status'] == 'queued'
                final = await event(ws, 'message.complete', sid)
                assert final['status'] == 'complete' and 'привет' in final['text'], final
                with pytest.raises(TimeoutError):
                    async with asyncio.timeout(2):
                        await event(ws, 'interaction.next_action', sid)
                snapshot = await rpc(ws, 'session.events.since', session_id=sid, last_seen=0)
                assert snapshot['result']['open_requests'] == []

        try:
            with daemon(root, home, env, barrier=False) as (_, desc):
                asyncio.run(probe(desc))
            assert len(peer.requests) == 1
        finally:
            peer.shutdown()
            server.join(timeout=5)
