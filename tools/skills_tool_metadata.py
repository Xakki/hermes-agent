"""Raw discovery metadata shared by the bounded catalog and strict command scanner.

Visibility/precedence stays in the catalog; only file-derived data lives here.
Full skill bodies are discarded after extracting the description fallback.
"""

import re
import threading
import time
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path

from utils import file_signature

_MAX_METADATA_FILES = 2048
_METADATA_TTL_SECONDS = 30.0
_metadata: OrderedDict = OrderedDict()
_metadata_lock = threading.Lock()
_generation = 0


def clear_skill_metadata_cache() -> None:
    """Explicit reloads also invalidate in-progress metadata publications."""
    global _generation
    with _metadata_lock:
        _generation += 1
        _metadata.clear()


def _fallback(body: str, limit: int) -> str:
    return next((line.strip()[:limit] for line in body.split("\n")
                 if line.strip() and not line.strip().startswith("#")), "")


def _read_views(path: Path, full: bool) -> dict:
    from tools import skills_tool as st

    content = path.read_text(encoding="utf-8-sig") if full else st._read_skill_text(path)
    prefix = content[:4000]
    parsed, body = st._parse_frontmatter(content if full else prefix)
    views = {full: (parsed, _fallback(body, 80 if full else 1025))}
    # Replacement decoding is not strict decoding, even if the bad byte is in the body.
    # Conservatively re-read literal replacement characters too. Long/unclosed headers
    # must keep their distinct bounded/full parse rather than borrowing the catalog view.
    normalized = prefix.removeprefix("\ufeff")
    end_match = re.search(r"\n---\s*\n", normalized[3:]) if normalized.startswith("---") else None
    complete = not normalized.startswith("---") or end_match is not None
    if complete and "\ufffd" not in content:
        if full:
            # Parse results agree; only the fallback's body window/length differs.
            bounded_body = normalized[end_match.end() + 3:] if end_match else normalized
            views[False] = (parsed, _fallback(bounded_body, 1025))
        else:
            offset = len(normalized) - len(body)
            views[True] = (parsed, _fallback(content.removeprefix("\ufeff")[offset:], 80))
    return views


def read_skill_metadata(path: Path, *, full: bool = False) -> tuple[dict, str]:
    """Copied frontmatter and fallback for one view, fingerprinted independently of scope.

    Use the existing catalog TTL and file-signature convention; do not pin failures or
    data read across a write/reload. Locks protect publication, never I/O or YAML parsing.
    """
    signature = file_signature(path.stat())
    now = time.monotonic()
    with _metadata_lock:
        generation = _generation
        cached = _metadata.get(path)
        if cached is not None and cached[0] == signature and now - cached[1] < _METADATA_TTL_SECONDS:
            if full in cached[2]:
                _metadata.move_to_end(path)
                return deepcopy(cached[2][full])
        else:
            cached = None
    views = _read_views(path, full)
    if file_signature(path.stat()) == signature:
        with _metadata_lock:
            if generation == _generation:
                if cached is not None:
                    views = {**cached[2], **views}
                _metadata[path] = (signature, now, views)
                _metadata.move_to_end(path)
                while len(_metadata) > _MAX_METADATA_FILES:
                    _metadata.popitem(last=False)
    return deepcopy(views[full])
