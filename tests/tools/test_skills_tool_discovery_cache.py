"""Regression tests for the find_all_skills discovery cache (#58985 salvage).

Covers the cache-signature fix layered on the cherry-picked contributor
commit: the original keyed the cache on the max mtime of only the TOP-LEVEL
scan dirs, so adding/removing a skill inside a category subdir (which bumps
the category dir's mtime, not the root's) served a stale list indefinitely.
The signature now covers roots + immediate children (mirroring
hermes_cli/profiles.py::_count_skills) plus the disabled-set, with a short
TTL bounding in-place SKILL.md edit staleness.
"""

import time

import pytest

import tools.skills_tool as st


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch, tmp_path):
    """Isolate every test: clear the module cache and point the scan at
    an empty external-dirs list + a tmp skills root."""
    st._SKILLS_CACHE.clear()
    monkeypatch.setattr(st, "_skills_dir", lambda: tmp_path / "skills")
    monkeypatch.setattr(
        "agent.skill_utils.get_external_skills_dirs", list
    )
    monkeypatch.setattr(st, "_get_disabled_skill_names", lambda: set())
    yield
    st._SKILLS_CACHE.clear()


def _write_skill(root, category, name, description="a skill"):
    d = root / "skills" / category / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n# {name}\n",
        encoding="utf-8",
    )
    return d


def test_cache_hit_serves_copies_not_cache_objects(tmp_path):
    """Callers mutate the returned dicts (web_server annotates
    s['enabled']/s['usage']) — the cache must hand out per-call copies."""
    _write_skill(tmp_path, "cat-a", "skill-one")
    first = st.find_all_skills()
    assert [s["name"] for s in first] == ["skill-one"]

    # Mutate what the first caller got; the next (cached) call must be clean.
    first[0]["enabled"] = False
    first.append({"name": "junk"})

    second = st.find_all_skills()
    assert [s["name"] for s in second] == ["skill-one"]
    assert "enabled" not in second[0], "cache poisoned by caller mutation"
    assert second is not first


def test_disabled_and_full_views_cached_separately(tmp_path, monkeypatch):
    _write_skill(tmp_path, "cat-a", "skill-one")
    _write_skill(tmp_path, "cat-a", "skill-two")
    monkeypatch.setattr(st, "_get_disabled_skill_names", lambda: {"skill-two"})

    filtered = sorted(s["name"] for s in st.find_all_skills())
    everything = sorted(s["name"] for s in st.find_all_skills(skip_disabled=True))
    assert filtered == ["skill-one"]
    assert everything == ["skill-one", "skill-two"]


@pytest.mark.parametrize("prefix", ["", "\ufeff"])
def test_command_scan_reuses_catalog_metadata(tmp_path, monkeypatch, prefix):
    """A short complete header has the same YAML view in both discovery consumers."""
    from pathlib import Path
    from unittest.mock import patch

    from agent import skill_commands as sc

    skill = _write_skill(tmp_path, "cat-a", "reuse", "Original description.") / "SKILL.md"
    skill.write_text(prefix + skill.read_text(encoding="utf-8"), encoding="utf-8")
    sc._skill_commands_by_key.clear()
    st.clear_skills_cache()
    original_read = Path.read_text
    reads = []

    def observe_read(path, *args, **kwargs):
        if path == skill:
            reads.append(path)
        return original_read(path, *args, **kwargs)

    with patch.object(Path, "read_text", observe_read), patch.object(
        st, "_parse_frontmatter", wraps=st._parse_frontmatter
    ) as parse:
        commands = sc.scan_skill_commands()
        assert commands["/reuse"]["description"] == "Original description."
        assert st.find_all_skills()[0]["description"] == "Original description."
        assert (parse.call_count, len(reads)) == (1, 1), "unchanged compatible skill parsed/read twice"


@pytest.mark.parametrize("full_first", [False, True])
@pytest.mark.parametrize("content", [
    "---\nname: sample\ndescription: A description.\n---\n# Heading\nBody.\n",
    "\ufeff---\nname: sample\ndescription: A BOM description.\n---\nBody.\n",
    "---\nname: sample\ndescription: [broken\n---\nFallback.\n",
    "---\nname: full-name\npadding: " + "x" * 4100 + "\ndescription: Full header.\n---\nBody.\n",
    "---\nname: sample\n---\n" + "# heading\n" * 500 + "Late fallback.\n",
    "---\nname: sample\n---\n" + "z" * 1200 + "\n",
    "# No header\nPlain fallback.\n",
    "---\nname: sample\ndescription: null\n---\n# No body\n",
], ids=["short", "bom", "malformed", "long-header", "late-body", "long-fallback", "no-header", "null"])
def test_metadata_views_preserve_bounded_and_full_parsing(tmp_path, content, full_first):
    from tools.skills_tool_metadata import read_skill_metadata

    skill = _write_skill(tmp_path, "cat", "sample") / "SKILL.md"
    skill.write_text(content, encoding="utf-8")
    st.clear_skills_cache()
    decoded = skill.read_text(encoding="utf-8-sig")
    for full in (full_first, not full_first):
        expected, body = st._parse_frontmatter(decoded if full else decoded[:4000])
        fallback = next((line.strip() for line in body.strip().split("\n")
                         if line.strip() and not line.strip().startswith("#")), "")
        actual, actual_fallback = read_skill_metadata(skill, full=full)
        assert actual == expected
        assert actual_fallback == fallback[:80 if full else 1025]
        # Nested YAML data is never shared with gate/caller mutations.
        actual["description"] = "poison"
        assert read_skill_metadata(skill, full=full)[0] == expected


def test_replacement_decode_never_supplies_a_strict_command(tmp_path):
    from agent import skill_commands as sc

    skill = _write_skill(tmp_path, "cat", "invalid-byte") / "SKILL.md"
    with skill.open("ab") as stream:
        stream.write(b"\nInvalid body: \xff\n")
    st.clear_skills_cache()
    assert st.find_all_skills()[0]["name"] == "invalid-byte"
    assert "/invalid-byte" not in sc.scan_skill_commands()


def test_raw_metadata_survives_scope_views_but_not_changes_or_reload(tmp_path, monkeypatch):
    from unittest.mock import patch

    from tools.skills_tool_metadata import read_skill_metadata

    skill = _write_skill(tmp_path, "cat", "shared") / "SKILL.md"
    st.clear_skills_cache()
    with patch.object(st, "_parse_frontmatter", wraps=st._parse_frontmatter) as parse:
        first = st._skill_catalog()
        assert first[0]["name"] == "shared"
        first[0]["description"] = "caller mutation"
        # A second visibility scope uses the same raw file but a different disabled set.
        monkeypatch.setattr(st, "_get_disabled_skill_names", lambda: {"shared"})
        assert st._skill_catalog() == []
        assert st._skill_catalog(skip_disabled=True)[0]["description"] == "a skill"
        assert parse.call_count == 1
        skill.write_text("---\nname: changed-name\n---\nNew fallback.\n", encoding="utf-8")
        assert read_skill_metadata(skill, full=True)[0]["name"] == "changed-name"
        assert parse.call_count == 2
        st.clear_skills_cache()
        assert read_skill_metadata(skill)[0]["name"] == "changed-name"
        assert parse.call_count == 3
        skill.unlink()
        with pytest.raises(FileNotFoundError):
            read_skill_metadata(skill)


def test_metadata_failure_recovers_and_retention_is_bounded(tmp_path, monkeypatch):
    from pathlib import Path
    from unittest.mock import patch

    from tools import skills_tool_metadata as metadata

    skills = [_write_skill(tmp_path, "cat", f"skill-{i}") / "SKILL.md" for i in range(3)]
    st.clear_skills_cache()
    monkeypatch.setattr(metadata, "_MAX_METADATA_FILES", 2)
    with patch.object(Path, "read_text", side_effect=OSError("fixture read failed")):
        with pytest.raises(OSError, match="fixture read failed"):
            metadata.read_skill_metadata(skills[0])
    with patch.object(st, "_parse_frontmatter", wraps=st._parse_frontmatter) as parse:
        for skill in skills:
            assert metadata.read_skill_metadata(skill)[0]["name"] == skill.parent.name
        assert len(metadata._metadata) == 2
        assert metadata.read_skill_metadata(skills[0])[0]["name"] == "skill-0"
        assert parse.call_count == 4
