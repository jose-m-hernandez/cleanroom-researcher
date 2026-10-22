"""The item header grammar.

An item file starts with a title line, a blank line, then a block of
``key: value`` lines that ends at the first blank line or the end of the file.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

SOURCE_KEYS = (
    "id",
    "url",
    "publisher",
    "source_type",
    "fetched",
    "sha256",
    "snapshot",
    "licence",
    "licence_basis",
    "notes",
)

CODE_REPO_KEYS = (
    "id",
    "url",
    "publisher",
    "source_type",
    "fetched",
    "sha256",
    "commit",
    "snapshot",
    "licence",
    "licence_basis",
    "notes",
)

EXPERIMENT_KEYS = ("id", "subject", "protocol", "read_log", "spend_cap_usd", "launched_by")

_KEY_LINE = re.compile(r"^([a-z][a-z0-9_]*): (.+)$")


def parse(
    text: str,
) -> Tuple[Optional[str], List[Tuple[str, str]], List[Tuple[int, str]]]:
    """Return (title, [(key, value), ...], [(line, reason), ...])."""
    lines = text.split("\n")
    problems: List[Tuple[int, str]] = []
    pairs: List[Tuple[str, str]] = []
    title: Optional[str] = None

    first = lines[0]
    if first.startswith("# ") and first[2:].strip():
        title = first[2:].strip()
    else:
        problems.append((1, "line 1 must be '# <title>' with a non-empty title"))

    if len(lines) < 2 or lines[1].strip():
        problems.append((2, "line 2 must be blank"))

    index = 2
    seen = set()
    start = index
    while index < len(lines) and lines[index].strip():
        number = index + 1
        match = _KEY_LINE.match(lines[index])
        if not match:
            problems.append((number, "not a 'key: value' line"))
        else:
            key = match.group(1)
            value = match.group(2).strip()
            if not value:
                problems.append((number, "empty value for '%s'" % key))
            elif "|" in value:
                problems.append((number, "value of '%s' contains '|'" % key))
            elif key in seen:
                problems.append((number, "duplicate key '%s'" % key))
            else:
                pairs.append((key, value))
            seen.add(key)
        index += 1
    if index == start:
        problems.append((start + 1, "header block is empty"))
    return title, pairs, problems
