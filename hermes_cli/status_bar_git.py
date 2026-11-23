"""Current git branch for the CLI status bar.

Reads ``.git/HEAD`` directly (no subprocess) so status-bar repaints stay cheap, with a
short per-directory TTL cache. Worktrees and submodules (``.git`` as a ``gitdir:``
pointer file) resolve to their private git dir, whose HEAD is per-worktree.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Optional

from prompt_toolkit.utils import get_cwidth

_TTL_SECONDS = 5.0
_cache: dict = {}
_CONTROLS = re.compile(r"[\x00-\x1f\x7f-\x9f\u061c\u200e\u200f\u2028-\u202e\u2066-\u2069]")


def _resolve_git_dir(start: Path) -> Optional[Path]:
    """Nearest enclosing git dir for ``start``, following worktree pointer files."""
    for parent in (start, *start.parents):
        dotgit = parent / ".git"
        if dotgit.is_dir():
            return dotgit
        if dotgit.is_file():
            try:
                line = dotgit.read_text(encoding="utf-8-sig", errors="replace").strip()
            except OSError:
                return None
            if line.startswith("gitdir:"):
                target = (parent / line.split(":", 1)[1].strip()).resolve()
                return target if target.is_dir() else None
            return None
    return None


def current_git_branch(cwd: Optional[str] = None) -> str:
    """Branch name for ``cwd`` (defaults to the process cwd); ``""`` outside a repo.

    Detached HEAD has no branch. The directory walk is cached for ~5s.
    """
    try:
        base = Path(cwd or os.getcwd())
    except OSError:
        return ""
    if not base.is_absolute():
        base = base.absolute()
    key = str(base)
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < _TTL_SECONDS:
        return hit[1]
    label = ""
    try:
        resolved = base.resolve(strict=True)
        git_dir = _resolve_git_dir(resolved) if resolved.is_dir() else None
        if git_dir is not None:
            with (git_dir / "HEAD").open(encoding="utf-8-sig", errors="replace") as stream:
                head = stream.read(4096).rstrip("\n")
            prefix = "ref: refs/heads/"
            if len(head) < 4096 and head.startswith(prefix) and not _CONTROLS.search(head):
                label = head[len(prefix):]
    except (OSError, RuntimeError, ValueError):  # missing cwd, malformed pointer or symlink loop
        pass
    _cache[key] = (now, label)
    return label


def _clip_start(text: str, width: int) -> str:
    if get_cwidth(text) <= width:
        return text
    tail = []
    remaining = width - 1
    for char in reversed(text):
        remaining -= get_cwidth(char)
        if remaining < 0:
            break
        tail.append(char)
    return "…" + "".join(reversed(tail))


def format_workspace_label(cwd: str, branch: str, width: int) -> str:
    """One optional status segment. Keep the directory suffix before dropping the branch."""
    if width < 6:
        return ""
    home = os.path.expanduser("~")
    if cwd == home or cwd.startswith(home + os.sep):
        cwd = "~" + cwd[len(home):]
    cwd = _CONTROLS.sub("�", cwd)
    if not cwd:
        return f"⎇ {_clip_start(branch, min(16, width - 2))}" if branch else ""
    tag = f" ({_clip_start(branch, min(16, width - 9))})" if branch and width >= 14 else ""
    return _clip_start(cwd, min(40, width - get_cwidth(tag))) + tag
