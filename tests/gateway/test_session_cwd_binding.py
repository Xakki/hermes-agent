from types import SimpleNamespace

from agent.runtime_cwd import resolve_agent_cwd
from gateway.run import GatewayRunner
from gateway.session import Platform, SessionContext, SessionSource


def test_gateway_binds_persisted_cwd_to_session_context(tmp_path):
    runner = object.__new__(GatewayRunner)
    runner.adapters = {}
    runner._session_db = SimpleNamespace(
        _db=SimpleNamespace(get_session=lambda _session_id: {"cwd": str(tmp_path)})
    )
    context = SessionContext(
        source=SessionSource(platform=Platform.TELEGRAM, chat_id="123"),
        connected_platforms=[],
        home_channels={},
        session_key="telegram:123",
        session_id="session-1",
    )

    tokens = runner._set_session_env(context)
    try:
        assert resolve_agent_cwd() == tmp_path.resolve()
    finally:
        runner._clear_session_env(tokens)