"""Flag a fork pull request that changes recorded data, for a maintainer to re-admit.

Usage: python3 tools/recorded_guard.py --base <commit> [--root PATH]

A pull request from a fork runs CI without this repository's secrets, so the
barred-string scan cannot run on it, and recorded data needs a maintainer's
hand re-admission. This tool lists what in the change a maintainer must look at.
It is a signal, not a control: a pull request can change the workflow that
runs it. The controls are the steps in MAINTAINING.md.

The change is the difference between --base and HEAD, which in CI is the pull
request's merge commit. The rules, in this order:

1. --base must be a full commit id, 40 or 64 lowercase hex characters;
   anything else is DEAD.
2. The base commit must be in this clone's history; if not, DEAD.
3. Every changed path under experiments/EXP-NNNN/recorded/ (the one test is
   paths.is_recorded_path) gets one line, in sorted order. Renames are not
   followed, so a file moved out of recorded/ shows under its old path, and a
   deleted file shows too.
4. The added and removed lines of docs/admissions.md are counted when their
   class cell, the second cell of the line after its first character, is
   exactly ``recorded``. A count above 0 gets one line.
5. Any line from rules 3 and 4 exits 1. Otherwise one line says how many files
   changed, and the exit is 0.
6. A git failure in rule 3 or 4 is DEAD. Any other exception prints its type
   name only, never a traceback.

Every output line starts with ``recorded-guard: ``. Exit 0 nothing to
re-admit, 1 findings, 2 dead.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import List, Optional

try:
    from lib import paths, repofiles

    IMPORT_ERROR: Optional[BaseException] = None
except Exception as _exc:  # reported as DEAD by main(), not as a traceback
    paths = None  # type: ignore[assignment]
    repofiles = None  # type: ignore[assignment]
    IMPORT_ERROR = _exc

CHECK = "recorded-guard"
FULL_ID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
LEDGER = "docs/admissions.md"
TAIL = "a maintainer must re-admit this change (MAINTAINING.md)"


def is_recorded_row(line: str) -> bool:
    # After the + or -, the line is "| path | class | ..."; cell 0 is empty.
    cells = line[1:].split("|")
    return len(cells) > 2 and cells[2].strip(" ") == "recorded"


def run(root: str, base: str):
    if not FULL_ID.match(base):
        return 2, ["%s: DEAD the base is not a full commit id" % CHECK]
    try:
        present = repofiles.has_commit(root, base)
    except repofiles.RepoFilesError:
        present = False  # a 64-digit id the helper does not accept is not in history
    if not present:
        return 2, ["%s: DEAD the base commit is not in this clone's history" % CHECK]
    try:
        changed = repofiles.changed_paths(root, base)
        found = [
            "%s: %s: %s" % (CHECK, p, TAIL) for p in changed if paths.is_recorded_path(p)
        ]
        k = sum(1 for ln in repofiles.changed_lines(root, base, LEDGER) if is_recorded_row(ln))
    except repofiles.RepoFilesError:
        return 2, ["%s: DEAD git diff failed" % CHECK]
    if k > 0:
        found.append("%s: %s: recorded rows changed: %d; %s" % (CHECK, LEDGER, k, TAIL))
    if found:
        return 1, found
    return 0, [
        "%s: no recorded files or rows changed (%d files in this change)" % (CHECK, len(changed))
    ]


def main(argv: Optional[List[str]] = None) -> int:
    default_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(
        description="Flag a fork pull request that changes recorded data."
    )
    parser.add_argument("--base", required=True, help="the pull request's base commit")
    parser.add_argument("--root", default=default_root, help="repository root")
    args = parser.parse_args(argv)
    try:
        if IMPORT_ERROR is not None:
            raise IMPORT_ERROR
        code, lines = run(os.path.abspath(args.root), args.base)
    except Exception as exc:
        print("%s: DEAD %s" % (CHECK, type(exc).__name__))
        return 2
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
