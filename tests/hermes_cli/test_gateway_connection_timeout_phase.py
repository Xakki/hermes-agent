"""Timeout phase attribution through the classic CLI's actual connection consumer."""
import asyncio
from contextlib import contextmanager
import json
import socket
import threading

import pytest
from websockets.asyncio.server import serve

from hermes_cli import gateway_client, gateway_runtime, gateway_runtime_discovery
from hermes_cli.gateway_connection import GatewayConnection


@pytest.mark.asyncio
@pytest.mark.parametrize("provided", [False, True], ids=["discovered", "provided"])
@pytest.mark.parametrize("responsive", [True, False], ids=["responsive", "ticket-timeout"])
async def test_native_ticket_timeout_names_its_phase(monkeypatch, provided, responsive):
    """A real control read times out before any handshake; responsive control reaches RPC."""
    received = threading.Event()
    finished = threading.Event()
    peers = []
    handshakes = []

    async def websocket_peer(ws):
        handshakes.append(True)
        async for raw in ws:
            request = json.loads(raw)
            await ws.send(json.dumps({"id": request["id"], "result": {"responsive": True}}))

    async with serve(websocket_peer, "127.0.0.1", 0, ping_interval=None,
                     subprotocols=[gateway_client.GATEWAY_WS_PROTOCOL]) as server:
        port = server.sockets[0].getsockname()[1]
        endpoint = gateway_runtime.GatewayEndpoint("owned-profile", "owned-instance", 1, 1,
            f"http://127.0.0.1:{port}", "none", frozenset())
        monkeypatch.delenv("HERMES_TUI_GATEWAY_URL", raising=False)
        monkeypatch.setattr(gateway_runtime, "gateway_profile_home", lambda home: home)
        monkeypatch.setattr(gateway_runtime, "ensure_gateway_runtime", lambda *args, **kwargs:
                            gateway_runtime.GatewayDiscovery("ready", endpoint))

        @contextmanager
        def private_peer(home, timeout):
            client, owner = socket.socketpair()

            def answer():
                try:
                    request = json.loads(owner.recv(65536))
                    assert request["verb"] == "session-ticket"
                    received.set()
                    if responsive:
                        owner.sendall(json.dumps({"protocol": 1, "id": 1, "ok": True, "result": {
                            "profile_id": endpoint.profile_id, "instance_id": endpoint.instance_id,
                            "runtime_protocol": 1, "ticket": "owned-fixture"}}).encode() + b"\n")
                    else:
                        finished.wait(5)
                finally:
                    owner.close()

            thread = threading.Thread(target=answer, daemon=True)
            peers.append(thread)
            thread.start()

            class BoundedPeer:
                # Scale only this owned socket's deadline; production read/timeout stays real.
                def settimeout(self, budget):
                    client.settimeout(min(budget, 0.05))

                def sendall(self, data):
                    client.sendall(data)

                def recv(self, size):
                    return client.recv(size)

            try:
                yield BoundedPeer()
            finally:
                finished.set()
                client.close()
                thread.join(2)

        monkeypatch.setattr(gateway_runtime_discovery, "connect_private", private_peer)
        connect = (lambda: gateway_client.connect_gateway(endpoint=endpoint)) if provided else None
        connection = GatewayConnection(connect=connect)
        try:
            if responsive:
                await asyncio.to_thread(connection.start, 5)
                assert await asyncio.to_thread(connection.call, "fixture.probe", timeout=2) == {"responsive": True}
                assert handshakes == [True]
            else:
                with pytest.raises(gateway_client.GatewayClientError) as caught:
                    await asyncio.to_thread(connection.start, 5)
                assert received.is_set() and handshakes == []
                # Generic connection failure loses the deciding pre-WebSocket control phase.
                assert str(caught.value) == "Gateway session ticket timed out; no local fallback"
                assert connection.epoch == 0 and connection.client is None
            assert received.is_set()
        finally:
            finished.set()
            await asyncio.to_thread(connection.close)
        assert all(not peer.is_alive() for peer in peers)
        assert not connection._thread.is_alive()


@pytest.mark.asyncio
async def test_handshake_timeout_is_not_the_raw_control_timeout(monkeypatch):
    from websockets.asyncio.client import connect as real_connect

    reached = asyncio.Event()
    release = asyncio.Event()

    async def hold_upgrade(ws, request):
        reached.set()
        await release.wait()

    async def peer(ws):
        await ws.wait_closed()

    async with serve(peer, "127.0.0.1", 0, process_request=hold_upgrade) as server:
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setenv("HERMES_TUI_GATEWAY_URL", f"ws://127.0.0.1:{port}/api/ws")

        def dial(url, **kwargs):
            assert kwargs["open_timeout"] == 10
            kwargs["open_timeout"] = 0.05
            return real_connect(url, **kwargs)

        monkeypatch.setattr("websockets.asyncio.client.connect", dial)
        connection = GatewayConnection()
        try:
            with pytest.raises(gateway_client.GatewayClientError) as caught:
                await asyncio.to_thread(connection.start, 5)
            assert reached.is_set()
            assert str(caught.value) == "Gateway connection failed; no local fallback"
            assert isinstance(caught.value.__cause__, TimeoutError)
        finally:
            release.set()
            await asyncio.to_thread(connection.close)


@pytest.mark.asyncio
async def test_resume_rpc_timeout_is_after_handshake_and_next_call_redials(monkeypatch):
    peers = []
    requests = []

    async def peer(ws):
        life = len(peers)
        peers.append(ws)
        async for raw in ws:
            request = json.loads(raw)
            requests.append((life, request["method"]))
            if life == 1 and request["method"] == "session.resume":
                continue
            await ws.send(json.dumps({"id": request["id"], "result": {
                "subscription_id": f"owned-{life}", "responsive": True}}))

    async with serve(peer, "127.0.0.1", 0, ping_interval=None) as server:
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setenv("HERMES_TUI_GATEWAY_URL", f"ws://127.0.0.1:{port}/api/ws")
        monkeypatch.setattr(gateway_client, "DEFAULT_RPC_TIMEOUT", 0.05)
        connection = GatewayConnection()
        try:
            await asyncio.to_thread(connection.start, 5)
            snapshot = await asyncio.to_thread(connection.call, "session.resume", session_id="owned", timeout=2)
            connection.state("owned").adopt_snapshot(snapshot)
            old_thread = connection._thread
            await peers[0].close()
            await asyncio.to_thread(old_thread.join, 2)
            assert not old_thread.is_alive()
            with pytest.raises(gateway_client.GatewayClientError) as caught:
                await asyncio.to_thread(connection.call, "fixture.probe", timeout=2)
            assert str(caught.value) == "Gateway connection failed; no local fallback"
            assert requests == [(0, "session.resume"), (1, "session.resume")]
            result = await asyncio.to_thread(connection.call, "fixture.probe", timeout=2)
            assert result["responsive"] is True
            assert requests[-2:] == [(2, "session.resume"), (2, "fixture.probe")]
        finally:
            await asyncio.to_thread(connection.close)


@pytest.mark.asyncio
async def test_discovery_timeout_has_a_typed_verdict_before_ticket(monkeypatch):
    monkeypatch.delenv("HERMES_TUI_GATEWAY_URL", raising=False)
    monkeypatch.setattr(gateway_runtime, "gateway_profile_home", lambda home: home)
    monkeypatch.setattr(gateway_runtime, "ensure_gateway_runtime", lambda *args, **kwargs:
                        gateway_runtime.GatewayDiscovery("inaccessible", reason_code="control_timeout"))
    monkeypatch.setattr(gateway_client, "_session_ticket", lambda *args, **kwargs: pytest.fail("ticket reached"))
    connection = GatewayConnection()
    try:
        with pytest.raises(gateway_client.GatewayUnavailableError, match="Gateway inaccessible: control_timeout"):
            await asyncio.to_thread(connection.start, 5)
    finally:
        await asyncio.to_thread(connection.close)
