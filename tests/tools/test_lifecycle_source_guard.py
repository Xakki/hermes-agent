"""Exercise source discovery through the supervised terminal guard seam, without execution."""

import json
from types import SimpleNamespace

import pytest

from tools.terminal_tool_guards import gateway_lifecycle_block


@pytest.mark.parametrize("lifecycle", [False, True])
def test_source_documentation_and_helper_through_terminal_guard(tmp_path, monkeypatch, lifecycle):
    from tools import process_registry

    monkeypatch.setattr(process_registry, "_is_supervised_gateway_process", lambda: True)
    entry = tmp_path / "lint"
    entry.write_text(
        "#!/usr/bin/env node\n" + "".join(
            f"/** See [rule](https://docs.example.test/rule-{i}) */\n" for i in range(80)
        ) + 'require("child_process").execSync("bash helper");\n',
        encoding="utf-8",
    )
    (tmp_path / "helper").write_text(
        "hermes gateway restart\n" if lifecycle else "echo safe\n", encoding="utf-8",
    )

    def forbidden(*args, **kwargs):
        raise BaseException("a local guard scan must not execute backend commands")

    result = gateway_lifecycle_block(
        command=str(entry), env=SimpleNamespace(cwd=str(tmp_path), execute=forbidden),
        env_type="local", cwd=str(tmp_path), workdir=None, session_key="source-fixture",
    )
    if lifecycle:
        assert result is not None
        blocked = json.loads(result)
        assert blocked["exit_code"] == 1
        assert "cannot restart, stop" in blocked["error"]
        assert "scan budget" not in blocked["error"]
    else:
        assert result is None
