"""Profile resolution is real; only the paid inference transport is a fixture."""
from pathlib import Path
from types import SimpleNamespace
import pytest

from hermes_cli.config import atomic_config_write
from hermes_cli.interaction_lifecycle import InteractionSession
from hermes_cli.interaction_decider import decide


def test_native_decision_runtime_imports():
    from tui_gateway import server
    assert callable(server._session_profile_runtime_scope)
    assert callable(server._resolve_runtime_with_fallback)


def test_named_profile_decisions_restore_home_a_b_a(tmp_path, monkeypatch):
    import json
    import httpx
    from hermes_constants import get_hermes_home
    from agent import auxiliary_client, secret_scope
    from hermes_cli import profiles
    from hermes_cli.interaction_config import resolve_interaction
    from hermes_cli.config_effective import load_user_config_effective
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / ".hermes"
    root.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(root))
    previous = secret_scope.is_multiplex_active()
    secret_scope.set_multiplex_active(True)
    seen = []
    for name in ("a", "b"):
        home = root / "profiles" / name
        home.mkdir(parents=True)
        (home / ".env").write_text("CUSTOM_API_KEY=synthetic-fixture\n")
        atomic_config_write(home / "config.yaml", {"model": {"provider": "custom", "default": f"fixture-{name}",
            "base_url": "http://localhost:8080/v1", "key_env": "CUSTOM_API_KEY"},
            "interaction": {'max_output_tokens': 1024} if name == 'b' else {}})
    def fixture_http(request):
        body = json.loads(request.content)
        seen.append((get_hermes_home(), body["model"], body.get("tools", []), body["max_tokens"]))
        return httpx.Response(200, json={"id": "fixture", "object": "chat.completion", "model": body["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": '{"choice":"stop"}'}, "finish_reason": "stop"}],
            "usage": {"completion_tokens": 8, "prompt_tokens": 1, "total_tokens": 9}})
    monkeypatch.setattr(auxiliary_client, "_openai_http_client_kwargs", lambda *args, **kwargs: {
        "http_client": httpx.Client(transport=httpx.MockTransport(fixture_http))})
    try:
        for name in ("a", "b", "a"):
            authority = object()
            settings = resolve_interaction(load_user_config_effective(profiles.get_profile_dir(name) / 'config.yaml'))
            session = InteractionSession("s", "executor", authority, settings=settings)
            session.grant(authority, profile=name, scope="accepted", seconds=60, decisions=1)
            request = session.open("next_action", ("stop",), scope="accepted")
            token = session.delegate(request.request_id, authority)
            assert token is not None
            assert token.max_output_tokens == (1024 if name == 'b' else 150000)
            assert decide(session, token)
            assert request.choice == "stop"
            assert session.journal[-1]["actor"] == "agent"
        assert [row[0] for row in seen] == [profiles.get_profile_dir(name) for name in ("a", "b", "a")]
        assert [row[1] for row in seen] == ["fixture-a", "fixture-b", "fixture-a"]
        assert all(row[2] == [] for row in seen)
        assert [row[3] for row in seen] == [150000, 1024, 150000]
    finally:
        secret_scope.set_multiplex_active(previous)


def test_missing_profile_pauses_same_request_without_deadline_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    authority = object()
    session = InteractionSession("s", "executor", authority)
    session.grant(authority, profile="absent", scope="accepted", seconds=60, decisions=1)
    request = session.open("next_action", ("stop",), scope="accepted")
    original = request.deadline
    token = session.delegate(request.request_id, authority)
    assert not decide(session, token)
    assert request.state == "blocked" and request.deadline == original
    assert request.reminder_cancelled
    assert session.mode == "assisted"


def test_native_synthetic_secrets_and_configured_fallback_a_b_a(tmp_path, monkeypatch):
    import json
    import os
    import httpx
    from agent import auxiliary_client, secret_scope
    from hermes_constants import get_hermes_home
    from hermes_cli.config_effective import load_user_config_effective
    from hermes_cli.interaction_config import resolve_interaction
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / ".hermes"
    root.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(root))
    launch_env = dict(os.environ)
    seen = []
    for name in ("a", "b"):
        home = root / "profiles" / name
        home.mkdir(parents=True)
        (home / ".env").write_text(f"CUSTOM_API_KEY=synthetic-{name}\nONLY_{name.upper()}=synthetic-scope\n")
        atomic_config_write(home / "config.yaml", {
            "model": {"provider": "custom", "default": f"fixture-{name}", "base_url": "https://primary.invalid/v1", "key_env": "CUSTOM_API_KEY"},
            "fallback_providers": [{"provider": "custom", "model": f"reserve-{name}", "base_url": "https://reserve.invalid/v1", "key_env": "CUSTOM_API_KEY"}],
            "auxiliary": {"transient_retries": 0}})
    def synthetic_http(request):
        body = json.loads(request.content)
        seen.append((get_hermes_home().name, request.url.host, body["model"]))
        assert request.headers["authorization"] == f"Bearer synthetic-{get_hermes_home().name}"
        name = get_hermes_home().name
        assert secret_scope.get_secret(f"ONLY_{name.upper()}") == "synthetic-scope"
        assert secret_scope.get_secret(f"ONLY_{'B' if name == 'a' else 'A'}") is None

        assert load_user_config_effective()["fallback_providers"][0]["model"] == f"reserve-{name}"
        if request.url.host == "primary.invalid":
            return httpx.Response(402, json={"error": {"message": "synthetic credits exhausted", "type": "payment_error"}})
        return httpx.Response(200, json={"id": "synthetic", "object": "chat.completion", "model": body["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": '{"choice":"stop"}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 8, "total_tokens": 108}})
    monkeypatch.setattr(auxiliary_client, "_openai_http_client_kwargs", lambda *args, **kwargs: {
        "http_client": httpx.Client(transport=httpx.MockTransport(synthetic_http))})
    previous = secret_scope.is_multiplex_active()
    secret_scope.set_multiplex_active(True)
    try:
        for name in ("a", "b", "a"):
            authority = object()
            session = InteractionSession("native", "owner", authority,
                settings=resolve_interaction({'interaction': {'max_output_tokens': 256}}))
            from tui_gateway import server
            from hermes_cli.config import load_config_readonly
            with server._session_profile_runtime_scope({'profile_home': str(root / 'profiles' / name)}):
                entries = load_config_readonly()['fallback_providers']
                assert entries[0]['model'] == f'reserve-{name}'
                assert Path(auxiliary_client.__file__).resolve().is_relative_to(Path(__file__).resolve().parents[2]), auxiliary_client.__file__
                assert auxiliary_client._resolve_fallback_entry(entries[0])[0] is not None
            session.grant(authority, profile=name, scope="accepted", seconds=60, decisions=1)
            request = session.open("next_action", ("stop",), scope="accepted")
            assert decide(session, session.delegate(request.request_id, authority)), seen
            record = session.journal[-1]
            assert record["actor"] == "agent" and record["model"] == f"reserve-{name}"
            assert len(record["attempts"]) <= 3
            assert record["attempts"][-1]["status"] == 200
            assert record["attempts"][0]["status"] == 402 or record["fallback"] != "primary"
        assert [row[0] for row in seen if row[1] == "reserve.invalid"] == ["a", "b", "a"]
        assert os.environ == launch_env
    finally:
        secret_scope.set_multiplex_active(previous)


@pytest.mark.platforms("posix")
def test_runner_crossprocess_home_ownership_and_inflight_revocation(tmp_path, monkeypatch):
    import subprocess
    import sys
    import threading

    from agent import auxiliary_client
    from tools.bot_relay import turn_lock_path
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    home = tmp_path / ".hermes" / "profiles" / "a"
    home.mkdir(parents=True)
    atomic_config_write(home / "config.yaml", {"model": {"provider": "custom", "default": "fixture", "base_url": "http://localhost:8080/v1"}})
    path = turn_lock_path(home, "interaction-decision")
    path.parent.mkdir(parents=True)
    holder = subprocess.Popen([sys.executable, "-c", "import fcntl,sys; f=open(sys.argv[1],'a'); fcntl.flock(f,fcntl.LOCK_EX); print('held',flush=True); sys.stdin.read()", str(path)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    entered, release = threading.Event(), threading.Event()
    def synthetic_call(**kwargs):
        entered.set()
        assert release.wait(30)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"choice":"stop"}'))])
    monkeypatch.setattr(auxiliary_client, "call_llm", synthetic_call)
    def pending():
        authority = object()
        session = InteractionSession("cancel", "owner", authority)
        session.grant(authority, profile="a", scope="accepted", seconds=60, decisions=1)
        request = session.open("next_action", ("stop",), scope="accepted")
        return session, authority, request, session.delegate(request.request_id, authority)
    try:
        assert holder.stdout.readline().strip() == "held"
        session, authority, request, token = pending()
        assert not decide(session, token) and not entered.is_set()
    finally:
        holder.communicate(timeout=30)
    session, authority, request, token = pending()
    result = []
    worker = threading.Thread(target=lambda: result.append(decide(session, token)))
    worker.start()
    assert entered.wait(30)
    session.revoke(authority)
    worker.join(30)
    assert result == [False] and request.choice is None
    release.set()
    assert not session.machine_result(token, "stop", {"model": "forged", "actor": "user"})
    from hermes_cli.interaction_decider import _profile_locks
    lease = _profile_locks[str(home.resolve())]
    assert lease.acquire(timeout=30)
    lease.release()


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("boundary", ["revoke", "deadline"])
def test_real_sdk_controlled_http_io_cannot_commit_after_revocation_or_deadline(tmp_path, monkeypatch, boundary):
    """Loopback provider is synthetic; SDK, HTTP transport, profile and runner are real."""
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import httpx
    from agent import auxiliary_client
    from hermes_cli.interaction_decider import _profile_locks
    from hermes_cli.interaction_config import resolve_interaction

    entered, release, closed, replied = (threading.Event() for _ in range(4))
    wire, fixture_errors = [], []
    payload = json.dumps({"id": "fixture", "object": "chat.completion", "model": "fixture",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": '{"choice":"stop"}'}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 8, "total_tokens": 9}}).encode()
    class Provider(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass
        def do_POST(self):
            wire.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            entered.set()
            if not release.wait(30):
                fixture_errors.append("controlled provider was never released")
                return
            try:
                self.wfile.write(payload)  # genuine late HTTP completion after the boundary
            except OSError:
                # A physically closed client socket may reject this deliberately late write.
                pass
            finally:
                replied.set()
    class ObservedStream(httpx.SyncByteStream):
        def __init__(self, stream):
            self.stream = stream
        def __iter__(self):
            yield from self.stream
        def close(self):
            self.stream.close()
            closed.set()
    class ObservedHTTPTransport(httpx.HTTPTransport):
        def handle_request(self, request):
            response = super().handle_request(request)
            response.stream = ObservedStream(response.stream)
            return response

    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    serving = threading.Thread(target=provider.serve_forever, daemon=True)
    serving.start()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    home = tmp_path / ".hermes" / "profiles" / "io"
    home.mkdir(parents=True)
    (home / ".env").write_text("CUSTOM_API_KEY=[REDACTED]\n")
    atomic_config_write(home / "config.yaml", {
        "model": {"provider": "custom", "default": "fixture", "key_env": "CUSTOM_API_KEY",
                  "base_url": f"http://127.0.0.1:{provider.server_port}/v1"},
        "auxiliary": {"transient_retries": 0}})
    monkeypatch.setattr(auxiliary_client, "_openai_http_client_kwargs", lambda *args, **kwargs: {
        "http_client": httpx.Client(transport=ObservedHTTPTransport())})
    authority = object()
    settings = resolve_interaction({'interaction': {'decision_timeout_seconds': 10}})
    session = InteractionSession("native-io", "owner", authority, settings=settings)
    session.grant(authority, profile="io", scope="accepted", seconds=60, decisions=1)
    request = session.open("next_action", ("stop",), scope="accepted")
    original_deadline = request.deadline
    token = session.delegate(request.request_id, authority)
    assert token is not None
    results = []
    runner = threading.Thread(target=lambda: results.append(decide(session, token)))
    runner.start()
    try:
        assert entered.wait(20), "native SDK never reached controlled HTTP I/O"
        if boundary == "revoke":
            session.revoke(authority)
        assert closed.wait(20), "actual HTTP response stream did not close at the boundary"
        runner.join(20)
        assert not runner.is_alive() and results == [False]
        release.set()
        assert replied.wait(10), "controlled provider never attempted its late completion"
        lease = _profile_locks[str(home.resolve())]
        assert lease.acquire(timeout=20), "native worker retained ownership after I/O exited"
        lease.release()
        assert request.choice is None and request.deadline == original_deadline
        assert request.state == ("cancelled" if boundary == "revoke" else "blocked")
        assert session.mode == "assisted" and not any(row["event"] == "decision" for row in session.journal)
        assert not session.machine_result(token, "stop", {"provider": "fixture"})
        assert len(wire) == 1 and wire[0]["max_tokens"] == settings.max_output_tokens and not wire[0].get("tools")
        assert not fixture_errors
    finally:
        release.set()
        runner.join(20)
        provider.shutdown()
        provider.server_close()
        serving.join(10)
        assert not runner.is_alive() and not serving.is_alive()
