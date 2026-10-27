"""YOLO follows launch authority, not target profiles or mutable environment flags."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from tools import approval
from tools.approval_context import (
    get_current_session_key, reset_current_session_key, set_current_session_key,
)
from tools.environments import local

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = Path(__file__).with_name("fixtures") / "herm_prof.py"


@pytest.fixture
def authority():
    token = set_current_session_key("yolo-parent-a")
    assert not approval._YOLO_MODE_FROZEN
    try:
        yield "yolo-parent-a"
    finally:
        approval.clear_session("yolo-parent-a")
        approval.clear_session("yolo-parent-b")
        reset_current_session_key(token)


def _env(surface, base):
    builders = {
        "foreground": lambda: local._make_run_env(base),
        "background-pty": lambda: local._sanitize_subprocess_env(base),
        "nonterminal": lambda: local.hermes_subprocess_env(base_env=base),
        "factory": lambda: local.build_subprocess_env(base),
        "unscrubbed": lambda: local.build_subprocess_env(base, scrub_secrets=False),
        "served": lambda: local.served_profile_child_env(base=base),
    }
    return builders[surface]()


@pytest.mark.parametrize("surface", ["foreground", "background-pty", "nonterminal", "factory", "unscrubbed", "served"])
@pytest.mark.parametrize("late", [None, "", "0", "false", "1"])
def test_runtime_toggle_bridges_authority_only(authority, monkeypatch, surface, late):
    monkeypatch.delenv("HERMES_YOLO_MODE", raising=False)
    base = {"PATH": os.defpath}
    if late is not None:
        monkeypatch.setenv("HERMES_YOLO_MODE", late)
        base["HERMES_YOLO_MODE"] = late
    before = os.environ.get("HERMES_YOLO_MODE")
    approval.enable_session_yolo(authority)
    assert _env(surface, base).get("HERMES_YOLO_MODE") == "1"
    approval.disable_session_yolo(authority)
    assert "HERMES_YOLO_MODE" not in _env(surface, base)
    assert os.environ.get("HERMES_YOLO_MODE") == before


@pytest.mark.parametrize("startup", [None, "false", "0", "1"])
def test_real_frozen_startup_authority(tmp_path, startup):
    env = {"PATH": os.defpath, "HOME": str(tmp_path), "HERMES_HOME": str(tmp_path / "home")}
    if startup is not None:
        env["HERMES_YOLO_MODE"] = startup
    code = (
        "import os,json; from tools import approval; from tools.environments.local import served_profile_child_env; "
        "os.environ['HERMES_YOLO_MODE']='1' if not approval._YOLO_MODE_FROZEN else '0'; "
        "e=served_profile_child_env(base={},target_home=os.environ['HERMES_HOME']); "
        "print(json.dumps([approval._YOLO_MODE_FROZEN,e.get('HERMES_YOLO_MODE')]))"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=30, check=True)
    assert json.loads(result.stdout) == [startup == "1", "1" if startup == "1" else None]


def test_concurrent_profiles_a_b_a_real_processes(authority, tmp_path, monkeypatch):
    from agent.secret_scope import set_multiplex_active
    from gateway.run import _profile_runtime_scope
    a, b = tmp_path / "a", tmp_path / "b"
    for home in (a, b):
        home.mkdir()
        (home / ".env").write_text("OPENAI_API_KEY=synthetic-only\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(a))
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-parent-only")
    monkeypatch.setenv("HERMES_MODEL", "parent-only-model")
    approval.enable_session_yolo(authority)
    probe = (
        "import os,json; from tools import approval; "
        "print(json.dumps([approval._yolo_active(),os.environ.get('HERMES_YOLO_MODE'),"
        "'OPENAI_API_KEY' in os.environ,os.environ.get('HERMES_HOME')]))"
    )

    def launch(home, session, enabled):
        token = set_current_session_key(session)
        try:
            with _profile_runtime_scope(home, hydrate_secrets=False):
                base = local.hermes_subprocess_env(base_env={
                    "PATH": os.defpath, "HERMES_YOLO_MODE": "1", "OPENAI_API_KEY": "synthetic",
                })
                env = local.served_profile_child_env(base=base, target_home=home)
                result = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, env=env, capture_output=True, text=True, timeout=30, check=True)
                assert json.loads(result.stdout) == [enabled, "1" if enabled else None, False, str(home)]
        finally:
            reset_current_session_key(token)

    set_multiplex_active(True)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(launch, home, session, on) for home, session, on in (
                (a, authority, True), (b, "yolo-parent-b", False), (a, authority, True),
            )]
            for future in futures:
                future.result(timeout=40)
        assert approval.is_session_yolo_enabled(authority)
        assert not approval.is_session_yolo_enabled("yolo-parent-b")
        assert os.environ["HERMES_MODEL"] == "parent-only-model"
    finally:
        set_multiplex_active(False)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("raises", [False, True])
def test_native_worker_child_identity_descendants_and_cleanup(authority, enabled, raises):
    from tools.delegate_tool_child_run import _ChildRun
    child_id = "yolo-passive-child"
    if enabled:
        approval.enable_session_yolo(authority)
    seen = []

    class PassiveChild:
        session_id = child_id

        def run_conversation(self, **kwargs):
            seen.append((get_current_session_key(), approval.is_current_session_yolo_enabled(),
                         local.build_subprocess_env({}).get("HERMES_YOLO_MODE")))
            if raises:
                raise ValueError("passive probe failure")
            return {"completed": True, "final_response": "passive probe", "messages": [], "api_calls": 0}

        def get_activity_summary(self):
            return {"api_call_count": 0}

    run = _ChildRun(PassiveChild(), None, 0, "passive probe", None, None, child_task_id="passive-task")
    _result, failure, deferred = run.await_child()
    assert seen == [(child_id, enabled, "1" if enabled else None)]
    assert bool(failure) == raises and deferred is False
    assert get_current_session_key() == authority
    assert approval.is_session_yolo_enabled(authority) == enabled
    assert not approval.is_session_yolo_enabled(child_id)
    assert not approval.is_session_yolo_enabled("yolo-parent-b")


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("profile", ["a", "b"])
def test_clean_profile_wrapper_preserves_only_launch_mode(authority, monkeypatch, enabled, profile):
    spec = importlib.util.spec_from_file_location("herm_prof_fixture", PLUGIN)
    assert spec is not None and spec.loader is not None
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    monkeypatch.setenv("HERMES_YOLO_MODE", "1")  # late forged flag must not grant authority
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-parent-only")
    monkeypatch.setenv("HERMES_MODEL", "parent-only-model")
    if enabled:
        approval.enable_session_yolo(authority)
    captured = []
    monkeypatch.setattr(plugin.os, "execv", lambda exe, argv: captured.append((exe, argv)))
    plugin._run(SimpleNamespace(profile=profile, args=["version", "literal argument"]))
    exe, argv = captured[0]
    assert exe == "/usr/bin/env" and argv[:2] == ["env", "-i"]
    offset = argv.index("hermes")
    env = dict(item.split("=", 1) for item in argv[2:offset])
    assert set(env) == {"PATH", "HOME", "LANG", "TERM", "TMPDIR"} | ({"HERMES_YOLO_MODE"} if enabled else set())
    assert env["TMPDIR"] == "/var/tmp"
    assert env.get("HERMES_YOLO_MODE") == ("1" if enabled else None)
    assert argv[offset:] == ["hermes", "-p", profile, "version", "literal argument"]
    # Exercise the real env -i boundary with a passive import, never inference.
    probe = (
        "import json,os; from tools import approval; "
        "print(json.dumps([approval._yolo_active(), 'OPENAI_API_KEY' in os.environ, "
        "'HERMES_MODEL' in os.environ]))"
    )
    result = subprocess.run([*argv[:offset], sys.executable, "-c", probe],
                            cwd=ROOT, capture_output=True, text=True, timeout=30, check=True)
    assert json.loads(result.stdout) == [enabled, False, False]
