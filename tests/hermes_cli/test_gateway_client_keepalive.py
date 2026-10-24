"""Native-local viewers don't generate keepalive pings; explicit/tunnel peers still do."""
import asyncio
import json


import pytest
from websockets.asyncio.client import ClientConnection, connect as real_connect
from websockets.asyncio.server import ServerConnection, serve
from websockets.frames import OP_PONG

from hermes_cli import gateway_client, gateway_runtime


def _native(monkeypatch, origin):
    endpoint = gateway_runtime.GatewayEndpoint("fixture", "owned", 1, 1, origin, "none", frozenset())
    monkeypatch.delenv("HERMES_TUI_GATEWAY_URL", raising=False)
    monkeypatch.setattr(gateway_client, "_session_ticket", lambda *args, **kwargs: "owned-fixture")
    monkeypatch.setattr(gateway_runtime, "gateway_profile_home", lambda home: home)
    monkeypatch.setattr(gateway_runtime, "ensure_gateway_runtime", lambda *args, **kwargs:
                        gateway_runtime.GatewayDiscovery("ready", endpoint))
    return endpoint


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,host,disabled", [
    ("discovered", "127.0.0.1", True),
    ("provided", "127.0.0.1", True),
    ("provided", "localhost", True),
    ("provided", "[::1]", True),
    ("provided", "192.0.2.1", False),
    ("provided", "owner.example", False),
    ("explicit", "127.0.0.1", False),
    ("explicit", "[::1]", False),
    ("explicit", "owner.example", False),
])
async def test_connect_gateway_keepalive_policy(monkeypatch, mode, host, disabled):
    """Only transport routing is redirected; the real constructor consumes production options."""
    async def peer(ws):
        await ws.wait_closed()

    async with serve(peer, "127.0.0.1", 0, ping_interval=None,
                     select_subprotocol=lambda ws, offered:
                     gateway_client.GATEWAY_WS_PROTOCOL if offered else None,
                     subprotocols=[gateway_client.GATEWAY_WS_PROTOCOL]) as server:
        port = server.sockets[0].getsockname()[1]
        origin = f"http://{host}:{port}"
        endpoint = _native(monkeypatch, origin)
        if mode == "explicit":
            monkeypatch.setenv("HERMES_TUI_GATEWAY_URL", f"ws://{host}:{port}/api/ws")
        seen = []

        def dial(url, **kwargs):
            seen.append((url, kwargs.copy()))
            # Non-loopback names are policy controls, never real network destinations.
            return real_connect(f"ws://127.0.0.1:{port}/api/ws", **kwargs)

        monkeypatch.setattr("websockets.asyncio.client.connect", dial)
        async with gateway_client.connect_gateway(endpoint if mode == "provided" else None) as client:
            assert client.websocket.ping_timeout == 20
            assert client.websocket.ping_interval == (None if disabled else 20)
            assert (client.websocket.keepalive_task is None) == disabled
        url, options = seen[0]
        assert url == f"ws://{host}:{port}/api/ws"
        assert options["proxy"] is None and options["open_timeout"] == 10
        assert options["subprotocols"] == (None if mode == "explicit" else [
            gateway_client.GATEWAY_WS_PROTOCOL, "hermes-gateway-ticket.owned-fixture"])


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit,withhold_pong", [(False, True), (True, True), (True, False)])
async def test_owned_peer_missing_pong_disconnect_mechanism(monkeypatch, explicit, withhold_pong):
    """Scale actual constructor defaults, then withhold protocol pong (not an application sleep).

    This proves the false-disconnect mechanism, not the cause of any historical missing pong.
    The responsive explicit control proves yielding application work doesn't suppress pong.
    """
    pong_attempted = asyncio.Event()

    class OwnedPeer(ServerConnection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            send_frame = self.protocol.send_frame

            def frame(frame):
                if frame.opcode == OP_PONG:
                    pong_attempted.set()
                    if withhold_pong:
                        return
                send_frame(frame)

            self.protocol.send_frame = frame

    async def peer(ws):
        async for raw in ws:
            request = json.loads(raw)
            await ws.send(json.dumps({"id": request["id"], "result": {"alive": True}}))

    calibrated = []

    def scaled_connection(*args, **kwargs):
        connection = ClientConnection(*args, **kwargs)
        calibrated.append((connection.ping_interval, connection.ping_timeout))
        # Calibration observes the real defaults before scaling; explicit None stays disabled.
        if connection.ping_interval is not None:
            connection.ping_interval /= 100
        connection.ping_timeout /= 100
        return connection

    async with serve(peer, "127.0.0.1", 0, ping_interval=None, create_connection=OwnedPeer,
                     select_subprotocol=lambda ws, offered:
                     gateway_client.GATEWAY_WS_PROTOCOL if offered else None,
                     subprotocols=[gateway_client.GATEWAY_WS_PROTOCOL]) as server:
        port = server.sockets[0].getsockname()[1]
        _native(monkeypatch, f"http://127.0.0.1:{port}")
        if explicit:
            monkeypatch.setenv("HERMES_TUI_GATEWAY_URL", f"ws://127.0.0.1:{port}/api/ws")

        def dial(url, **kwargs):
            return real_connect(url, create_connection=scaled_connection, **kwargs)

        monkeypatch.setattr("websockets.asyncio.client.connect", dial)
        async with gateway_client.connect_gateway() as client:
            assert await client.rpc("fixture.probe", _timeout=2) == {"alive": True}
            if explicit and withhold_pong:
                await asyncio.wait_for(pong_attempted.wait(), 2)
                await asyncio.wait_for(client.websocket.wait_closed(), 2)
                assert client.websocket.protocol.close_sent.code == 1011
                assert client.websocket.protocol.close_sent.reason == "keepalive ping timeout"
                with pytest.raises(gateway_client.GatewayClientError, match="outcome is unknown"):
                    await client.rpc("fixture.probe", _timeout=2)
            else:
                elapsed = asyncio.Event()
                timer = asyncio.get_running_loop().call_later(0.8, elapsed.set)
                try:
                    await asyncio.wait_for(elapsed.wait(), 2)
                    # On the unfixed native path this reaches the accepted send-error normalizer.
                    assert await client.rpc("fixture.probe", _timeout=2) == {"alive": True}
                finally:
                    timer.cancel()
                assert pong_attempted.is_set() == explicit
        assert calibrated == [(20 if explicit else None, 20)]
