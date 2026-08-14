"""Project-scoped Claude deny snapshots stored outside project worktrees."""

from __future__ import annotations

from pathlib import Path


def _project_candidates(cwd: Path) -> list[Path]:
    """Return cwd -> Git root, or cwd alone when no Git boundary exists."""
    cwd = cwd.resolve()
    git_root = next(
        (candidate for candidate in (cwd, *cwd.parents) if (candidate / ".git").exists()),
        None,
    )
    candidates = [cwd]
    if git_root is not None and git_root != cwd:
        for parent in cwd.parents:
            candidates.append(parent)
            if parent == git_root:
                break
    return candidates


def load_claude_project_permissions(cwd: Path | None = None) -> tuple[set[str], set[str]]:
    """Return project deny patterns from the user-owned Hermes snapshot.

    Runtime never reads ``.claude/settings*.json`` from a worktree. An explicit
    import stores deny-only lists under canonical absolute project paths in the
    Hermes config. Cwd-to-Git-root lookup preserves project/session isolation;
    without a Git boundary only the exact cwd may match.
    """
    try:
        from hermes_cli.config import load_config_readonly

        config = load_config_readonly()
        permissions_cfg = config.get("permissions") or {}
        if not isinstance(permissions_cfg, dict) or permissions_cfg.get(
            "claude_project_snapshot"
        ) is not True:
            return set(), set()
        snapshots = permissions_cfg.get("claude_project_denies") or {}
        if not isinstance(snapshots, dict):
            return set(), set()
        if cwd is None:
            from agent.runtime_cwd import resolve_agent_cwd

            cwd = resolve_agent_cwd()
    except Exception:
        return set(), set()

    denied: set[str] = set()
    for project_dir in _project_candidates(Path(cwd)):
        key = str(project_dir.resolve())
        patterns = snapshots.get(key)
        if not isinstance(patterns, list):
            continue
        denied.update(
            pattern.strip()
            for pattern in patterns
            if isinstance(pattern, str) and pattern.strip()
        )
    return set(), denied
