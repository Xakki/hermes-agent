"""Documentation in interpreter sources must not become executable shell edges."""

from pathlib import Path
import shlex

import pytest

from cron import lifecycle_guard as guard


def _source_graph(tmp_path):
    bin_dir = tmp_path / "bin"
    types_dir = tmp_path / "lib" / "types"
    bin_dir.mkdir()
    types_dir.mkdir(parents=True)
    entry = bin_dir / "lint"
    entry.write_text(
        '#!/usr/bin/env node\nconst pkg = require("../package.json");\n',
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        '{\n  "name": "fixture-linter",\n  "typesVersions": {\n'
        '    "*": {\n      "rules": [\n'
        '        "./lib/types/rules.d.ts"\n      ]\n    }\n  }\n}\n',
        encoding="utf-8",
    )
    declarations = "export interface Rules {\n" + "".join(
        f"/** See [rule {i}](https://docs.example.test/rules/rule-{i}). */\n"
        f'  "rule-{i}"?: boolean;\n'
        for i in range(80)
    ) + "}\n"
    (types_dir / "rules.d.ts").write_text(declarations, encoding="utf-8")
    return entry


def test_documentation_graph_does_not_exhaust_backend_reads(tmp_path, monkeypatch, caplog):
    entry = _source_graph(tmp_path)
    reads = []
    remote = []
    original = guard._read_referenced_script

    def read(path, **kwargs):
        text, unsafe = original(path, **kwargs)
        if text:
            reads.append(Path(path).resolve())
        return text, unsafe

    def backend(path):
        remote.append(path)

    monkeypatch.setattr(guard, "_read_referenced_script", read)
    verdict = guard.scan_gateway_lifecycle(
        str(entry), cwd=str(tmp_path), read_remote_script=backend,
    )
    assert set(reads) == {
        entry.resolve(), (tmp_path / "package.json").resolve(),
        (tmp_path / "lib/types/rules.d.ts").resolve(),
    }
    assert not any("walk failed" in record.message for record in caplog.records)
    assert verdict == (False, None)
    assert remote == []


@pytest.mark.parametrize("body", [
    '#!/usr/bin/env node\nrequire("child_process").execSync("bash helper");\n',
    '#!/usr/bin/env python3\nimport os\nos.system("bash helper")\n',
    '#!/usr/bin/env node\nrequire("child_process").execSync("./helper");\n',
    '#!/usr/bin/env python3\nimport os\nos.system("./helper")\n',
    'import os\nos.system("./helper")\n',
])
def test_interpreter_source_still_follows_real_shell_helpers(tmp_path, body, caplog):
    entry = tmp_path / "entry"
    entry.write_text(body, encoding="utf-8")
    (tmp_path / "helper").write_text("bash leaf\n", encoding="utf-8")
    (tmp_path / "leaf").write_text("hermes gateway restart\n", encoding="utf-8")
    assert guard.scan_gateway_lifecycle(str(entry), cwd=str(tmp_path)) == (True, None)
    assert not any("walk failed" in record.message for record in caplog.records)


def test_shell_helper_in_metadata_is_not_hidden_by_comment_syntax(tmp_path):
    entry = _source_graph(tmp_path)
    (tmp_path / "lib/types/rules.d.ts").write_text("/*\n./leaf\n*/\n", encoding="utf-8")
    (tmp_path / "lib/types/leaf").write_text("hermes gateway restart\n", encoding="utf-8")
    assert guard.scan_gateway_lifecycle(str(entry), cwd=str(tmp_path)) == (True, None)


@pytest.mark.parametrize("name", ["source.d.ts", "source.js", "source.json", "suffixless"])
def test_explicit_shell_overrides_source_suffix_and_shebang(tmp_path, name):
    entry = tmp_path / name
    entry.write_text("#!/usr/bin/env node\n/*\n./leaf\n*/\n", encoding="utf-8")
    (tmp_path / "leaf").write_text("hermes gateway stop\n", encoding="utf-8")
    assert guard.scan_gateway_lifecycle(f"bash {entry}", cwd=str(tmp_path)) == (True, None)


def test_source_visit_does_not_hide_later_shell_visit(tmp_path):
    entry = _source_graph(tmp_path)
    declarations = tmp_path / "lib/types/rules.d.ts"
    declarations.write_text("/* [rule](https://docs.example.test/rule) */\n", encoding="utf-8")
    remote = []

    def backend(path):
        remote.append(path)
        return "hermes gateway restart\n" if "https:" in path else None

    assert guard.scan_gateway_lifecycle(
        f"{entry} && bash {declarations}", cwd=str(tmp_path), read_remote_script=backend,
    ) == (True, None)
    assert len(remote) == 2
    assert remote[0] == "/*"
    assert "https:" in remote[1]


def test_backend_source_graph_has_no_phantom_reads(tmp_path, caplog):
    files = {
        str(tmp_path / "entry"): '#!/usr/bin/env node\nrequire("./metadata.json");\n',
        str(tmp_path / "metadata.json"): '{\n"types": [\n"./rules.d.ts"\n]\n}\n',
        str(tmp_path / "rules.d.ts"): "export interface Rules {}\n" + "".join(
            f"/** [rule](https://docs.example.test/rule-{i}) */\n" for i in range(80)
        ),
    }
    reads = []

    def backend(path):
        reads.append(path)
        return files.get(path)

    assert guard.scan_gateway_lifecycle(
        str(tmp_path / "entry"), cwd=str(tmp_path), read_remote_script=backend,
    ) == (False, None)
    assert reads == list(files)
    assert not any("walk failed" in record.message for record in caplog.records)


def test_broad_missing_shell_graph_keeps_default_cap_and_resets(tmp_path):
    command = " && ".join(f"bash {tmp_path}/missing-{i}" for i in range(70))
    for _ in range(2):
        reads = []

        def backend(path):
            reads.append(path)

        unsafe, refusal = guard.scan_gateway_lifecycle(command, read_remote_script=backend)
        assert unsafe is True
        assert refusal == "the scan budget was exhausted (remote reads at depth 0)"
        assert len(reads) == guard._MAX_LIFECYCLE_SCAN_REMOTE_READS == 64


@pytest.mark.parametrize("lifecycle", [False, True])
def test_nested_cycle_is_bounded_and_reachable_lifecycle_is_found(tmp_path, monkeypatch, lifecycle):
    (tmp_path / "mid").write_text("bash leaf\n", encoding="utf-8")
    (tmp_path / "leaf").write_text(
        "bash mid\n" + ("hermes gateway restart\n" if lifecycle else "echo safe\n"),
        encoding="utf-8",
    )
    reads = []
    original = guard._read_referenced_script

    def read(path, **kwargs):
        reads.append(Path(path).name)
        return original(path, **kwargs)

    monkeypatch.setattr(guard, "_read_referenced_script", read)
    assert guard.scan_gateway_lifecycle("bash mid", cwd=str(tmp_path)) == (lifecycle, None)
    assert reads == ["mid", "leaf"]


@pytest.mark.parametrize("command", ["hermes gateway restart", "https://docs.example.test/rule", "bash https://docs.example.test/rule"])
def test_lifecycle_and_uri_shell_commands_remain_data_only_blocked(tmp_path, command):
    assert guard.scan_gateway_lifecycle(
        command, cwd=str(tmp_path), read_remote_script=lambda path: "hermes gateway stop\n",
    ) == (True, None)


@pytest.mark.parametrize("remote", [False, True])
def test_interpreter_shell_helper_over_scan_cap_fails_closed(tmp_path, remote, caplog):
    entry = tmp_path / "entry"
    entry.write_text('#!/usr/bin/env node\nrequire("child_process").execSync("bash helper");\n', encoding="utf-8")
    oversized = "x" * (guard._MAX_REFERENCED_SCRIPT_BYTES + 1)
    if not remote:
        (tmp_path / "helper").write_text(oversized, encoding="utf-8")
    unsafe, refusal = guard.scan_gateway_lifecycle(
        str(entry), cwd=str(tmp_path), read_remote_script=(lambda path: oversized) if remote else None,
    )
    assert unsafe is True
    assert refusal is not None
    assert "cap" in refusal
    assert not any("walk failed" in record.message for record in caplog.records)


@pytest.mark.platforms("linux")
@pytest.mark.parametrize("body", [
    '#!/usr/bin/env node\nrequire("child_process").execSync("./mid");\n',
    '#!/usr/bin/env node\nrequire("child_process").exec("./mid");\n',
    '#!/usr/bin/env node\nconst launch = require("child_process").execSync;\nlaunch("./mid");\n',
    '#!/usr/bin/env python3\nimport os\nos.system("./mid")\n',
    None,
], ids=["node-sync", "node-async", "node-alias", "python", "direct-bash"])
@pytest.mark.parametrize("lifecycle", [True, False], ids=["lifecycle", "benign"])
def test_source_call_preserves_suffixless_shell_interpretation(tmp_path, monkeypatch, caplog, body, lifecycle):
    """F1: a real no-shebang executable uses //absolute/path as a shell operand, not a JS comment."""
    entry, mid, leaf = (tmp_path / name for name in ("entry", "mid", "leaf"))
    leaf.write_text("hermes gateway restart\n" if lifecycle else "echo safe\n", encoding="utf-8")
    double_slash = "/" + str(leaf)
    assert Path(double_slash).samefile(leaf)
    mid.write_text(f". {double_slash}\n", encoding="utf-8")
    mid.chmod(0o700)
    assert mid.stat().st_mode & 0o111
    assert mid.read_bytes() == f". {double_slash}\n".encode()
    command = f"bash {mid}"
    expected_reads = {mid.resolve(), leaf.resolve()}
    if body is not None:
        entry.write_text(body, encoding="utf-8")
        entry.chmod(0o700)
        assert entry.stat().st_mode & 0o111
        command = str(entry)
        expected_reads.add(entry.resolve())
    reads = []
    original = guard._read_referenced_script

    def read(path, **kwargs):
        text, unsafe = original(path, **kwargs)
        if text is not None:
            reads.append(Path(path).resolve())
        return text, unsafe

    monkeypatch.setattr(guard, "_read_referenced_script", read)
    result = guard.scan_gateway_lifecycle(command, cwd=str(tmp_path))
    assert not any("walk failed" in record.message for record in caplog.records)
    assert result == (lifecycle, None), reads
    assert set(reads) == expected_reads


@pytest.mark.platforms("linux")
def test_node_shebang_auto_visit_cannot_suppress_explicit_shell(tmp_path, monkeypatch, caplog):
    """A node comment is inert for node but a real absolute executable path for bash."""
    entry, leaf = tmp_path / "entry", tmp_path / "leaf"
    leaf.write_text("hermes gateway restart\n", encoding="utf-8")
    double_slash = "/" + str(leaf)
    assert Path(double_slash).samefile(leaf)
    entry.write_text(f"#!/usr/bin/env node\n{double_slash}\n", encoding="utf-8")
    entry.chmod(0o700)
    leaf.chmod(0o700)
    reads = []
    original = guard._read_referenced_script

    def read(path, **kwargs):
        text, unsafe = original(path, **kwargs)
        if text is not None:
            reads.append(Path(path).resolve())
        return text, unsafe

    monkeypatch.setattr(guard, "_read_referenced_script", read)
    assert guard.scan_gateway_lifecycle(str(entry), cwd=str(tmp_path)) == (False, None)
    assert reads == [entry.resolve()]
    reads.clear()
    assert guard.scan_gateway_lifecycle(f"bash {entry}", cwd=str(tmp_path)) == (True, None)
    assert reads == [entry.resolve(), leaf.resolve()]
    reads.clear()
    result = guard.scan_gateway_lifecycle(f"{entry} && bash {entry}", cwd=str(tmp_path))
    assert not any("walk failed" in record.message for record in caplog.records)
    assert result == (True, None), reads
    assert reads == [entry.resolve(), entry.resolve(), leaf.resolve()]


_BINDING_WRAPPERS = [
    '(function(require){require("./mid");})(cp.execSync);',
    '(function(launch){launch("./mid");})(cp.execSync);',
    '((require)=>require("./mid"))(cp.execSync);',
    '(function(require=cp.execSync){require("./mid");})();',
    '(function({require}){require("./mid");})({require:cp.execSync});',
    '(function(...args){const [require]=args;require("./mid");})(cp.execSync);',
    '(function(){let require=cp.execSync;require("./mid");})();',
    'require=cp.execSync;require("./mid");',
    'globalThis["require"]=cp.execSync;globalThis["require"]("./mid");',
    'const msg=`${((require)=>require("./mid"))(cp.execSync)}`;',
    'const pattern=/[r]equire/;(function(require){require("./mid");})(cp.execSync);',
    r'(function(requ\u0069re){requ\u0069re("./mid");})(cp.execSync);',
]


@pytest.mark.platforms("linux")
@pytest.mark.parametrize("body", _BINDING_WRAPPERS, ids=[
    "parameter-require", "parameter-launch", "arrow", "default", "destructuring", "rest", "declaration",
    "reassignment", "property-write", "template-binding", "unsupported-regexp", "escaped-identifier",
])
@pytest.mark.parametrize("lifecycle", [True, False], ids=["lifecycle", "benign"])
def test_local_bindings_cannot_certify_ambient_loader(tmp_path, monkeypatch, caplog, body, lifecycle):
    entry, mid, leaf = (tmp_path / p for p in ("entry", "mid", "leaf"))
    entry.write_text('#!/usr/bin/env node\nconst cp=require("child_process");\n' + body + "\n", encoding="utf-8")
    mid.write_text(f". /{leaf}\n", encoding="utf-8")
    leaf.write_text("hermes gateway restart\n" if lifecycle else "echo safe\n", encoding="utf-8")
    entry.chmod(0o700)
    mid.chmod(0o700)
    assert Path("/" + str(leaf)).samefile(leaf)
    assert mid.read_bytes() == f". /{leaf}\n".encode()
    assert entry.stat().st_mode & mid.stat().st_mode & 0o111
    reads = []
    original = guard._read_referenced_script

    def read(path, **kwargs):
        result = original(path, **kwargs)
        if result[0] is not None:
            reads.append(Path(path).resolve())
        return result

    monkeypatch.setattr(guard, "_read_referenced_script", read)
    result = guard.scan_gateway_lifecycle(str(entry), cwd=str(tmp_path))
    assert not any("walk failed" in record.message for record in caplog.records)
    assert result == (lifecycle, None), reads
    assert set(reads) == {entry.resolve(), mid.resolve(), leaf.resolve()}


@pytest.mark.parametrize("prefix", [
    'const note="function(require){}";\n// require = fake;\n',
    'const msg=`value ${1+2} ${(()=>{const note="require";return note;})()}`;\n',
    '',
], ids=["binding-text-is-data", "template-code-without-conflict", "ambient"])
def test_ambient_loader_certificate_keeps_complete_documentation_graph(tmp_path, monkeypatch, caplog, prefix):
    entry = _source_graph(tmp_path)
    entry.write_text('#!/usr/bin/env node\n' + prefix + 'const pkg=require("../package.json");\n', encoding="utf-8")
    local, remote = [], []
    original = guard._read_referenced_script

    def read(path, **kwargs):
        result = original(path, **kwargs)
        if result[0] is not None:
            local.append(Path(path).resolve())
        return result

    def backend(path):
        remote.append(path)

    monkeypatch.setattr(guard, "_read_referenced_script", read)
    assert guard.scan_gateway_lifecycle(str(entry), cwd=str(tmp_path), read_remote_script=backend) == (False, None)
    assert set(local) == {entry.resolve(), (tmp_path / "package.json").resolve(), (tmp_path / "lib/types/rules.d.ts").resolve()}
    assert remote == []
    assert not any("walk failed" in record.message for record in caplog.records)


@pytest.mark.platforms("linux")
def test_auto_execution_honors_child_node_shebang(tmp_path, monkeypatch, caplog):
    entry, child, leaf = (tmp_path / p for p in ("entry", "child", "leaf"))
    entry.write_text('#!/usr/bin/env node\nrequire("child_process").execSync("./child");\n', encoding="utf-8")
    child.write_text(f"#!/usr/bin/env node\n/{leaf}\n", encoding="utf-8")
    leaf.write_text("hermes gateway restart\n", encoding="utf-8")
    for p in (entry, child, leaf):
        p.chmod(0o700)
    assert Path("/" + str(leaf)).samefile(leaf)
    reads = []
    original = guard._read_referenced_script

    def read(path, **kwargs):
        result = original(path, **kwargs)
        if result[0] is not None:
            reads.append(Path(path).resolve())
        return result

    monkeypatch.setattr(guard, "_read_referenced_script", read)
    assert guard.scan_gateway_lifecycle(str(entry), cwd=str(tmp_path)) == (False, None)
    assert set(reads) == {entry.resolve(), child.resolve()}
    assert guard.scan_gateway_lifecycle(f"bash {child}", cwd=str(tmp_path)) == (True, None)
    assert leaf.resolve() in reads
    assert not any("walk failed" in record.message for record in caplog.records)


def test_inert_visit_cannot_consume_later_executed_obligation(tmp_path, caplog):
    big = tmp_path / "big"
    big.write_text("#" + "x" * guard._MAX_REFERENCED_SCRIPT_BYTES, encoding="utf-8")
    payload = f"cat <<'EOF'\nbash {big}\nEOF"
    assert guard.scan_gateway_lifecycle(f"bash -c {shlex.quote(payload)}") == (False, None)
    assert guard.scan_gateway_lifecycle(f"bash {big}")[0] is True
    unsafe, refusal = guard.scan_gateway_lifecycle(f"bash -c {shlex.quote(payload)} && bash {big}")
    assert unsafe is True
    assert refusal is not None and "cap" in refusal
    assert not any("walk failed" in record.message for record in caplog.records)


def test_forced_shell_visit_cannot_consume_auto_node_obligation(tmp_path, caplog):
    entry, helper = tmp_path / "entry", tmp_path / "helper"
    entry.write_text('#!/usr/bin/env node\nrequire("child_process").execSync("bash helper");\n', encoding="utf-8")
    helper.write_text("hermes gateway restart\n", encoding="utf-8")
    assert guard.scan_gateway_lifecycle(f"bash {entry}", cwd=str(tmp_path)) == (False, None)
    assert guard.scan_gateway_lifecycle(f"bash {entry} && {entry}", cwd=str(tmp_path)) == (True, None)
    assert not any("walk failed" in record.message for record in caplog.records)
