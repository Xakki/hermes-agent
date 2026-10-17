"""Keep disposable build products out of Git without hiding source files."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = (
    "hermes_cli/.web_dist-build-fixture/public/favicon.ico",
    "hermes_cli/.web_dist-previous-fixture/product/index.html",
    "apps/desktop/.dist-build-fixture/index.js",
    "apps/desktop/.dist-previous-fixture/product/index.js",
    "ui-tui/.dist-build-fixture/entry.js",
    "ui-tui/.dist-previous-fixture/product/entry.js",
    "ui-tui/.dist-fixture/entry.js",
    "apps/desktop/core.19",
    "core.1234",
    "apps/desktop/crash.dmp",
    "report-goal-interaction-autonomy.md",
    "src/editor.tmp",
    "src/editor.swp",
    "src/editor.swo",
    "src/editor.py~",
)
SOURCES = (
    "apps/desktop/src/core/index.ts",
    "apps/desktop/electron/get-windows.d.ts",
    "apps/desktop/src/plugins/hello-runtime/plugin.runtime.js",
    "hermes_cli/source_build.py",
    "ui-tui/src/entry.tsx",
    "tests/fixtures/source.json",
    "tests/fixtures/core.1.ts",
    "report-format.md",
    "uv.lock",
    "package-lock.json",
    ".env.example",
)


@pytest.fixture
def repo(tmp_path):
    directory = tmp_path / "checkout"
    directory.mkdir()
    subprocess.run(["git", "init", "-q", str(directory)], check=True, capture_output=True)
    shutil.copyfile(ROOT / ".gitignore", directory / ".gitignore")
    for name in (*ARTIFACTS, *SOURCES):
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
    return directory


def ignored(repo, names):
    result = subprocess.run(
        ["git", "-C", str(repo), "check-ignore", "--no-index", "-z", "--stdin"],
        input=b"\0".join(name.encode() for name in names) + b"\0",
        capture_output=True,
        check=False,
    )
    assert result.returncode in (0, 1)
    return {name.decode() for name in result.stdout.split(b"\0") if name}


def test_build_runtime_and_temporary_files_are_ignored(repo):
    assert ignored(repo, ARTIFACTS) == set(ARTIFACTS)


def test_source_fixtures_and_lockfiles_remain_visible(repo):
    assert ignored(repo, SOURCES) == set()
