"""Native RPC session metadata resolves the same recall file as classic CLI."""
import asyncio

import pytest

from tests.gateway.fixtures.local_recovery_probe import rpc, websocket
from tests.gateway.test_session_busy_controls import owner


@pytest.mark.platforms("linux")
def test_native_create_info_and_resume_keep_input_history_bound_to_project(tmp_path, monkeypatch):
    from hermes_cli.input_history import project_history_file

    project_a, project_b = tmp_path / "project-a", tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()
    with owner(tmp_path) as (home, peer, desc):
        monkeypatch.setenv("HERMES_HOME", str(home))

        async def probe():
            async with websocket(home, desc) as ws:
                sessions = []
                for request, cwd in (("a-1", project_a), ("b-1", project_b), ("a-2", project_a)):
                    response = await rpc(ws, "session.create", request_id=request, source="tui", cwd=str(cwd), toolsets=[])
                    assert "result" in response, response
                    result = response["result"]
                    key = result["info"]["input_history_key"]
                    assert key == project_history_file(str(cwd)).name
                    sessions.append((result["session_id"], key))
                assert sessions[0][1] == sessions[2][1]
                assert sessions[0][1] != sessions[1][1]

                for session_id, key in (sessions[0], sessions[1], sessions[2], sessions[0]):
                    resumed = await rpc(ws, "session.resume", session_id=session_id, display_projection=True)
                    assert resumed["result"]["info"]["input_history_key"] == key
                    info = await rpc(ws, "session.info", session_id=session_id)
                    assert info["result"]["input_history_key"] == key

        asyncio.run(probe())
        assert peer.requests == [], "Metadata-only project selection must not start inference"
