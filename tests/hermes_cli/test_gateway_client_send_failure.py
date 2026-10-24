"""Socket-send failures must use the gateway outcome contract, never model-error copy."""
import asyncio
from contextlib import asynccontextmanager
import json

import pytest
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK
from websockets.frames import Close

from hermes_cli.cli_chat_error_copy import chat_error_response
from hermes_cli.gateway_agent import GatewayAgent
from hermes_cli.gateway_client import GatewayClient, GatewayClientError
from hermes_cli.gateway_connection import GatewayConnection


def _keepalive_error():
    return ConnectionClosedError(None, Close(1011, "keepalive ping timeout"))


class _Socket:
    def __init__(self, error=None):
        self.error = error
        self.sent = []
        self.client: GatewayClient | None = None
        self.inbox = asyncio.Queue()

    async def send(self, raw):
        request = json.loads(raw)
        self.sent.append(request)
        if self.error is not None:
            raise self.error
        assert self.client is not None
        self.client.pending[request["id"]].set_result({"status": "accepted"})

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.inbox.get()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    pytest.param(_keepalive_error(), id="keepalive-timeout"),
    pytest.param(ConnectionClosedOK(Close(1000, ""), None), id="normal-close"),
    pytest.param(OSError("transport unavailable"), id="os-error"),
])
async def test_send_failure_is_a_gateway_failure_without_replaying_work(error):
    socket = _Socket(error)
    client = GatewayClient(socket)
    with pytest.raises(GatewayClientError, match="outcome is unknown") as failure:
        await client.rpc("prompt.submit", session_id="local-test", text="synthetic")
    assert "do not resend" in str(failure.value)
    assert failure.value.__cause__ is error
    assert len(socket.sent) == 1
    assert client.pending == {}


@pytest.mark.asyncio
async def test_successful_send_returns_the_owner_answer():
    socket = _Socket()
    socket.client = client = GatewayClient(socket)
    assert await client.rpc("status") == {"status": "accepted"}
    assert len(socket.sent) == 1
    assert client.pending == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [TypeError("invalid sender"), asyncio.CancelledError()])
async def test_programming_errors_and_cancellation_are_not_transport_failures(error):
    socket = _Socket(error)
    client = GatewayClient(socket)
    with pytest.raises(type(error)):
        await client.rpc("status")
    assert len(socket.sent) == 1
    assert client.pending == {}


def test_classic_cli_reports_gateway_failure_instead_of_switch_model():
    socket = _Socket()

    @asynccontextmanager
    async def dial():
        async with GatewayClient(socket) as client:
            socket.client = client
            yield client

    connection = GatewayConnection(connect=dial)
    try:
        agent = GatewayAgent(connection=connection, session_id="local-test")
        socket.error = _keepalive_error()
        result = agent.run_conversation("synthetic")
    finally:
        connection.close()
    assert result["failed"] is True
    assert result["failure_reason"] == "gateway_connection"
    rendered = chat_error_response(result["error"], failure_reason=result["failure_reason"])
    assert "outcome is unknown" in rendered
    assert "do not resend" in rendered
    assert "/model" not in rendered
    assert sum(request["method"] == "prompt.submit" for request in socket.sent) == 1
