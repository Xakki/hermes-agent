"""Ink's bare tool Python must activate dependencies before gateway imports."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import textwrap

import pytest


_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "ui-tui" / "scripts" / "gateway_bootstrap.py"


@pytest.mark.parametrize("flags, verdict", [
    ([], "discovery"),
    (["--start"], "ensure"),
    (["--recover"], "ensure"),
])
def test_bare_python_activates_before_gateway_imports(tmp_path, flags, verdict):
    # Use the real PM activation, selecting this runner's immutable dependency tree
    # through its sealed-payload seam. Never read or lease the operator's install.
    driver = tmp_path / "launch.py"
    driver.write_text(textwrap.dedent("""\
        import builtins
        import runpy
        import sys
        from pathlib import Path
        from types import SimpleNamespace
        import pm.environments as environments

        selected_environment = Path(sys.argv[2])
        environments.install_state_dir = lambda root: selected_environment / 'no-state'
        environments.payload_venv = lambda root: selected_environment
        environments.payload_command_dir = lambda root: None
        original_import = builtins.__import__

        def importing(name, *args, **kwargs):
            module = original_import(name, *args, **kwargs)
            if name == 'hermes_cli.gateway_runtime':
                runtime = sys.modules[name]
                discover = runtime.discover_gateway_endpoint
                def receipt(verdict, *a, **k):
                    discover(a[0], timeout=5)
                    return SimpleNamespace(
                        state='absent', endpoint=None, reason_code=verdict, detail=None)
                runtime.discover_gateway_endpoint = lambda *a, **k: receipt('discovery', *a, **k)
                runtime.ensure_gateway_runtime = lambda *a, **k: receipt('ensure', *a, **k)
            return module

        builtins.__import__ = importing
        script, environment = sys.argv[1:3]
        sys.argv = [script, *sys.argv[3:]]
        runpy.run_path(script, run_name='__main__')
    """), encoding="utf-8")
    environment = Path(sys.prefix)
    site_packages = sysconfig.get_path("purelib")
    env = dict(os.environ, PYTHONNOUSERSITE="1", HERMES_DISABLE_LAZY_INSTALLS="1")
    # -S and the base executable model the PM tool interpreter, not the test venv.
    # The explicit dependency-path control matches the independently proven remedy.
    outcomes = []
    for pythonpath in (os.pathsep.join([str(_ROOT), site_packages]), str(_ROOT)):
        env["PYTHONPATH"] = pythonpath
        result = subprocess.run(
            [sys._base_executable, "-S", str(driver), str(_SCRIPT), str(environment), *flags],
            cwd=_ROOT, env=env, text=True, capture_output=True, timeout=20, check=False,
        )
        outcomes.append(result)
    control, bare = outcomes
    expected = f"gateway absent: {verdict}"
    assert control.returncode == 1 and control.stderr.strip() == expected, control.stderr
    assert control.stdout == ""
    assert bare.returncode == 1 and bare.stderr.strip() == expected, bare.stderr
    assert bare.stdout == ""
