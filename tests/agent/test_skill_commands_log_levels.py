"""Behavioral coverage for skill-command scan log levels in agent/skill_commands.py.

Contract: a skill whose slug collides with a core Hermes command logs the skip
notice at INFO (expected, routine); a duplicate skill slug ("git_helper" vs
"git-helper") keeps its WARNING (first-wins precedence decision worth surfacing).
"""

import logging

from agent.skill_commands import _scan_skill_md


def _write_skill_md(tmp_path, name):
    skill_dir = tmp_path / name
    skill_dir.mkdir(parents=True)
    content = (
        "---\n"
        f"name: {name}\n"
        f"description: Description for {name}.\n"
        "---\n"
        f"\n# {name}\n\nDo the thing.\n"
    )
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(content, encoding="utf-8")
    return skill_md


def test_core_command_collision_skip_logs_at_info(caplog, tmp_path):
    """A skill whose slug collides with a core command logs its skip at INFO."""
    skill_md = _write_skill_md(tmp_path, "git_helper")  # slugifies to a core command name

    with caplog.at_level(logging.INFO, logger="agent.skill_commands"):
        commands = {}
        _scan_skill_md(
            skill_md, disabled=set(), seen_names=set(), commands=commands,
            resolve_command=lambda slug: object(),  # any core command counts as a collision
        )

    assert commands == {}  # skip behavior unchanged
    records = [r for r in caplog.records if "collides with a core Hermes command" in r.getMessage()]
    assert len(records) == 1
    assert records[0].levelno == logging.INFO
    assert "git_helper" in records[0].getMessage()
    assert not any(r.levelno >= logging.WARNING for r in records)


def test_duplicate_skill_slug_stays_warning(caplog, tmp_path):
    """Two skills normalizing to the same slug keep the first-wins WARNING."""
    first = _write_skill_md(tmp_path, "git_helper")
    second = _write_skill_md(tmp_path, "git-helper")  # normalizes to the same slug

    with caplog.at_level(logging.INFO, logger="agent.skill_commands"):
        commands, seen = {}, set()
        _scan_skill_md(first, disabled=set(), seen_names=seen, commands=commands,
                       resolve_command=lambda slug: None)
        _scan_skill_md(second, disabled=set(), seen_names=seen, commands=commands,
                       resolve_command=lambda slug: None)

    assert commands["/git-helper"]["name"] == "git_helper"  # first wins, unchanged
    records = [r for r in caplog.records if "already claimed" in r.getMessage()]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
