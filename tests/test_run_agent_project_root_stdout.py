"""Regression tests for one-shot stdout purity during session setup."""

import os
import subprocess
import sys
from pathlib import Path


def test_project_root_resolution_keeps_one_shot_stdout_available(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(repo_root), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import run_agent; "
            f"run_agent._launch_project_root_for_session('oneshot', {str(tmp_path)!r}); "
            "print('stdout-marker')",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "stdout-marker\n"
