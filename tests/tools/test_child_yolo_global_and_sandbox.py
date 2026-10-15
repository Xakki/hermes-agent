"""Real launch authority includes approvals.mode=off and execute-code descendants."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tools import approval
from tools.approval_context import get_current_session_key, reset_current_session_key, set_current_session_key
from tools.code_execution_env import _build_child_env, _scrub_child_env
from tools.environments import local

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = Path(__file__).with_name("fixtures") / "herm_prof.py"


def _scope(home):
    from gateway.run import _profile_runtime_scope
    return _profile_runtime_scope(home, hydrate_secrets=False)


@pytest.fixture
def homes(tmp_path, monkeypatch):
    from agent.secret_scope import set_multiplex_active
    profiles = {}
    for mode in ("manual", "smart", "off"):
        home = tmp_path / mode
        home.mkdir()
        (home / "config.yaml").write_text(f"approvals:\n  mode: {mode}\n  deny: ['printf DENIED']\n", encoding="utf-8")
        profiles[mode] = home
    monkeypatch.setenv("HERMES_HOME", str(profiles["manual"]))
    token = set_current_session_key("retry1-parent")
    assert not approval._YOLO_MODE_FROZEN
    set_multiplex_active(True)
    try:
        yield profiles
    finally:
        set_multiplex_active(False)
        for session in ("retry1-parent", "retry1-sibling", "retry1-child"):
            approval.clear_session(session)
        reset_current_session_key(token)


def _build(tmp_path):
    return _build_child_env(rpc_endpoint=str(tmp_path / "rpc.sock"), rpc_token="synthetic-rpc",
                            tmpdir=str(tmp_path), child_python=sys.executable)


def _probe(env):
    # Import the native agent entry point without instantiation or inference.
    code = (
        "import json,os; from run_agent import AIAgent; from tools import approval; "
        "from tools.environments.local import build_subprocess_env; "
        "print(json.dumps([approval._YOLO_MODE_FROZEN,build_subprocess_env({}).get('HERMES_YOLO_MODE'),"
        "'OPENAI_API_KEY' in os.environ,'HERMES_MODEL' in os.environ]))"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr.splitlines()[-12:]
    return json.loads(result.stdout)


@pytest.mark.parametrize("mode", ["manual", "smart", "off"])
@pytest.mark.parametrize("session_on", [False, True])
def test_sandbox_scrub_and_builder_real_agent_descendant(homes, monkeypatch, tmp_path, mode, session_on):
    from agent.delegation_context import delegated_child_context
    monkeypatch.setenv("HERMES_YOLO_MODE", "1")  # Forged after approval import.
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-parent")
    monkeypatch.setenv("HERMES_MODEL", "parent-model")
    monkeypatch.setenv("HERMES_KANBAN_TASK", "parent-task")
    if session_on:
        approval.enable_session_yolo("retry1-parent")
    enabled = session_on or mode == "off"
    with _scope(homes[mode]), delegated_child_context("retry1-child"):
        scrubbed = _scrub_child_env({"HERMES_YOLO_MODE": "1", "hermes_yolo_mode": "1",
                                    "OPENAI_API_KEY": "synthetic", "HERMES_KANBAN_TASK": "parent-task"})
        assert scrubbed.get("HERMES_YOLO_MODE") == ("1" if enabled else None)
        assert "hermes_yolo_mode" not in scrubbed and "OPENAI_API_KEY" not in scrubbed
        env = _build(tmp_path)
        assert env.get("HERMES_YOLO_MODE") == ("1" if enabled else None)
        assert env["HERMES_RPC_SOCKET"] == str(tmp_path / "rpc.sock")
        assert env["HERMES_RPC_TOKEN"] == "synthetic-rpc"
        assert env["HERMES_DELEGATED_CHILD_CONTEXT"]
        assert "HERMES_KANBAN_TASK" not in env
        assert env["HERMES_HOME"] == str(homes[mode])
        assert _probe(env) == [enabled, "1" if enabled else None, False, False]
    assert os.environ["HERMES_YOLO_MODE"] == "1"
    assert approval.is_session_yolo_enabled("retry1-parent") == session_on
    assert not approval.is_session_yolo_enabled("retry1-sibling")


@pytest.mark.parametrize("startup", [None, "0", "false", "1"])
def test_sandbox_frozen_authority_survives_late_mutation(tmp_path, startup):
    env = {"PATH": os.defpath, "HOME": str(tmp_path), "HERMES_HOME": str(tmp_path / "home")}
    if startup is not None:
        env["HERMES_YOLO_MODE"] = startup
    code = (
        "import json,os,sys; from tools import approval; from tools.code_execution_env import _scrub_child_env,_build_child_env; "
        "os.environ['HERMES_YOLO_MODE']='0' if approval._YOLO_MODE_FROZEN else '1'; "
        "a=_scrub_child_env(os.environ); b=_build_child_env(rpc_endpoint='socket',rpc_token='synthetic',tmpdir=os.environ['HOME'],child_python=sys.executable); "
        "print(json.dumps([a.get('HERMES_YOLO_MODE'),b.get('HERMES_YOLO_MODE')]))"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=30, check=True)
    marker = "1" if startup == "1" else None
    assert json.loads(result.stdout) == [marker, marker]


@pytest.mark.parametrize("mode", ["manual", "smart", "off"])
@pytest.mark.parametrize("surface", ["foreground", "background-pty", "nonterminal", "factory", "served"])
def test_global_launch_mode_all_bridges(homes, mode, surface):
    builders = {
        "foreground": lambda: local._make_run_env({}),
        "background-pty": lambda: local._sanitize_subprocess_env({}),
        "nonterminal": lambda: local.hermes_subprocess_env(base_env={}),
        "factory": lambda: local.build_subprocess_env({}),
        "served": lambda: local.served_profile_child_env(base={}, target_home=homes["manual"]),
    }
    with _scope(homes[mode]):
        assert approval.is_approval_bypass_active() == (mode == "off")
        assert builders[surface]().get("HERMES_YOLO_MODE") == ("1" if mode == "off" else None)
    assert not approval.is_session_yolo_enabled("retry1-parent")


def test_global_mode_concurrent_profiles_a_b_a_selected_profile_independent(homes):
    def launch(mode, session, target):
        token = set_current_session_key(session)
        try:
            with _scope(homes[mode]):
                env = local.hermes_subprocess_env(base_env={"PATH": os.defpath, "HOME": os.environ["HOME"]})
                env = local.served_profile_child_env(base=env, target_home=homes[target])
                assert env["HERMES_HOME"] == str(homes[target])
                # Startup inheritance must be independent even of an OFF target's own config.
                assert _probe(env)[:1] == [mode == "off"]
                assert approval.is_approval_bypass_active() == (mode == "off")
        finally:
            reset_current_session_key(token)
    rows = (("off", "retry1-parent", "manual"), ("manual", "retry1-sibling", "off"),
            ("off", "retry1-parent", "manual"))
    for row in rows:
        launch(*row)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(launch, *row) for row in rows]
        for future in futures:
            future.result(timeout=45)
    assert not approval.is_session_yolo_enabled("retry1-parent")
    assert not approval.is_session_yolo_enabled("retry1-sibling")


@pytest.mark.parametrize("mode", ["manual", "smart", "off"])
def test_global_mode_native_worker_switch_to_manual_and_cleanup(homes, mode):
    from tools.delegate_tool_child_run import _ChildRun
    seen = []
    class PassiveChild:
        session_id = "retry1-child"
        def run_conversation(self, **kwargs):
            with _scope(homes["manual"]):
                seen.append((get_current_session_key(), approval.is_approval_bypass_active(),
                             _scrub_child_env({}).get("HERMES_YOLO_MODE")))
            return {"completed": True, "final_response": "passive", "messages": [], "api_calls": 0}
        def get_activity_summary(self):
            return {"api_call_count": 0}
    with _scope(homes[mode]):
        run = _ChildRun(PassiveChild(), None, 0, "passive", None, None, child_task_id="retry1-task")
        _, failure, deferred = run.await_child()
        assert failure is None and deferred is False
        enabled = mode == "off"
        assert seen == [("retry1-child", enabled, "1" if enabled else None)]
        assert approval.is_approval_bypass_active() == enabled
    assert get_current_session_key() == "retry1-parent"
    assert not approval.is_session_yolo_enabled("retry1-child")
    assert not approval.is_session_yolo_enabled("retry1-parent")


@pytest.mark.parametrize("mode", ["manual", "smart", "off"])
def test_global_mode_clean_wrapper_passive_selected_manual_profile(homes, monkeypatch, mode):
    spec = importlib.util.spec_from_file_location("retry1_herm_prof", PLUGIN)
    assert spec is not None and spec.loader is not None
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    captured = []
    monkeypatch.setattr(plugin.os, "execv", lambda exe, argv: captured.append(argv))
    monkeypatch.setenv("HERMES_YOLO_MODE", "1")
    with _scope(homes[mode]):
        plugin._run(SimpleNamespace(profile="manual", args=["version"]))
    argv = captured[0]
    offset = argv.index("hermes")
    env = dict(item.split("=", 1) for item in argv[2:offset])
    assert env.get("HERMES_YOLO_MODE") == ("1" if mode == "off" else None)
    probe = "from tools import approval; print(int(approval._YOLO_MODE_FROZEN))"
    result = subprocess.run([*argv[:offset], sys.executable, "-c", probe], cwd=ROOT,
                            capture_output=True, text=True, timeout=30, check=True)
    assert result.stdout.strip() == str(int(mode == "off"))
    assert argv[offset:] == ["hermes", "-p", "manual", "version"]
    assert env["TMPDIR"] == "/var/tmp"


def test_mode_off_preserves_native_deny_and_hardline_floors(homes):
    with _scope(homes["off"]):
        assert approval.is_approval_bypass_active()
        for command in ("printf DENIED", "rm -rf /"):
            result = approval.check_all_command_guards(command, "local")
            assert result["approved"] is False


def test_ordinary_auto_approval_callback_does_not_grant_global_mode(homes):
    from tools import terminal_tool
    terminal_tool.set_approval_callback(lambda *args, **kwargs: True)
    try:
        with _scope(homes["manual"]):
            assert not approval.is_approval_bypass_active()
            assert "HERMES_YOLO_MODE" not in _scrub_child_env({"HERMES_YOLO_MODE": "1"})
            assert "HERMES_YOLO_MODE" not in local.build_subprocess_env({})
    finally:
        terminal_tool.set_approval_callback(None)
