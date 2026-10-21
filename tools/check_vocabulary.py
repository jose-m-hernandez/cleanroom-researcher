"""The vocabulary check: reserved words and shapes stay out of every file.

Usage: python3 tools/check_vocabulary.py [--root PATH]

Output: finding lines ``vocabulary: <path>[:<line>]: V<n> <reason>`` (a commit
message hit reads ``vocabulary: commit <short sha>: V<n> <reason>``), then one
summary line. Exit 0 pass, 1 findings, 2 dead or usage error.

Rules, case-insensitive unless stated:

- V1 reserved id shapes (three fixed prefixes, a hyphen, digits), as a whole
  word. Case-sensitive.
- V2 the word named in docs/vocabulary.md joined to a letter or digit by a
  hyphen or an underscore, on either side.
- V3 that word, whitespace, then the word for a lane.
- V4 home and machine paths: a tilde-slash anywhere, a users or home directory
  followed by a letter, and the usual home-directory variables.
- V5 in .py files only: a home-directory lookup call anywhere, or a quoted home
  key outside tests/ (a test may set a temporary HOME for a child process).
  Python files under experiments/<name>/harness/runner/ are not checked for V5:
  runner code builds the isolated environment for a live run.
  V5 matches names as written: an aliased expanduser or Path.home, and a HOME
  key built at runtime, are not tracked.
  Rules are ASCII-defined; Unicode lookalikes are not matched.
- V6 docs/vocabulary.md has exactly one row for that word under its Terms
  heading, no other file has a line shaped like that row, and the README's
  first heading and first paragraph do not use the word.
- V7 every commit message passes V1 to V4, trailers included, and so do the
  author name, author email, committer name and committer email of every
  commit (reported as ``vocabulary: commit <short sha>: V<n> <field> <reason>``).
  Identity fields are checked for shapes only; names are not this check's job.
  Rules are ASCII-defined; Unicode lookalikes are not matched.

Every pattern piece and every positive example is assembled at runtime, so this
file passes its own rules. A positive control runs before any scan: each of V1
to V5 must hit a synthetic string, or the check is DEAD.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

try:
    from lib import repofiles

    IMPORT_ERROR: Optional[BaseException] = None
except Exception as _exc:  # reported as DEAD by run(), not as a traceback
    repofiles = None  # type: ignore[assignment]
    IMPORT_ERROR = _exc

CHECK = "vocabulary"
VOCABULARY = "docs/vocabulary.md"
README = "README.md"

WORD = "harn" + "ess"
LANE = "la" + "ne"
JOIN = "[-_]"
ALNUM = "[A-Za-z0-9]"

V1_PATTERN = r"\b(?:" + "|".join(["HA" + "R", "MO" + "D", "RI" + "N"]) + r")-[0-9]+\b"
V2_PATTERN = ALNUM + JOIN + WORD + "|" + WORD + JOIN + ALNUM
V3_PATTERN = WORD + r"\s+" + LANE
V4_PIECES = [
    re.escape("~" + "/"),
    re.escape("/Us" + "ers/") + "[A-Za-z]",
    re.escape("/ho" + "me/") + "[A-Za-z]",
    re.escape("$" + "HO" + "ME"),
    re.escape("${" + "HOME}"),
    re.escape("%USER" + "PROFILE%"),
    re.escape("C:" + "\\Us" + "ers\\"),
]
V5_CALL_PIECES = [
    re.escape("Path" + ".home("),
    re.escape("expand" + "user("),
]
V5_KEY_PIECE = r"([\"'])" + "HO" + r"ME\1"
TESTS_DIR = "tests"
RUNNER_ROOT = re.compile(r"^experiments/[^/]+/harness/runner/")

# (rule, compiled pattern, reason). V1 is case-sensitive; the rest are not.
TEXT_RULES = [
    ("V1", re.compile(V1_PATTERN), "reserved id shape"),
    ("V2", re.compile(V2_PATTERN, re.IGNORECASE), "reserved word in a compound"),
    ("V3", re.compile(V3_PATTERN, re.IGNORECASE), "reserved word followed by lane"),
    ("V4", re.compile("|".join(V4_PIECES), re.IGNORECASE), "home or machine path"),
]
# Both V5 entries report as V5 with the same reason; the key entry is skipped
# for files under tests/.
V5_CALLS = ("V5", re.compile("|".join(V5_CALL_PIECES), re.IGNORECASE), "home lookup in code")
V5_KEY = ("V5", re.compile(V5_KEY_PIECE, re.IGNORECASE), "home lookup in code")
COMMIT_RULES = ("V1", "V2", "V3", "V4")
IDENTITY_FIELDS = ("author", "author email", "committer", "committer email")

TERMS_ROW = re.compile(r"^\|\s*`" + WORD + r"`\s*\|")
BARE_WORD = re.compile(WORD, re.IGNORECASE)

# One synthetic string per rule, each of which must hit.
CONTROLS: Dict[str, str] = {
    "V1": "HA" + "R" + "-001",
    "V2": "foo" + "-" + WORD,
    "V3": WORD + " " + LANE,
    "V4": "~" + "/" + "x",
    "V5": "Path" + ".home(",
}

Finding = Tuple[str, Optional[int], str]


def scan_text(
    text: str, is_py: bool, in_tests: bool = False, in_runner: bool = False
) -> List[Tuple[str, int, str]]:
    """Hits of V1 to V5 in text as (rule, line, reason), one per rule and line.

    in_tests drops the quoted-key piece of V5; the call pieces still apply.
    in_runner drops both V5 entries; V1 to V4 still apply.
    """
    rules = list(TEXT_RULES)
    if is_py and not in_runner:
        rules.append(V5_CALLS)
        if not in_tests:
            rules.append(V5_KEY)
    seen = set()
    found: List[Tuple[str, int, str]] = []
    for rule, pattern, reason in rules:
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            if (rule, line) in seen:
                continue
            seen.add((rule, line))
            found.append((rule, line, reason))
    found.sort(key=lambda hit: (hit[1], hit[0]))
    return found


def control() -> Optional[str]:
    """None when every rule hits its synthetic string, else the rules that did not."""
    missed = []
    for rule in ("V1", "V2", "V3", "V4", "V5"):
        sample = CONTROLS.get(rule, "")
        if not any(r == rule for r, _l, _why in scan_text(sample, True)):
            missed.append(rule)
    if missed:
        return ", ".join(missed)
    return None


def terms_rows(text: str) -> int:
    """How many rows of the word sit under the Terms heading."""
    inside = False
    count = 0
    for line in text.split("\n"):
        if line.startswith("#"):
            inside = line.strip() == "## Terms"
            continue
        if inside and TERMS_ROW.match(line):
            count += 1
    return count


def check_v6(texts: Dict[str, str]) -> List[Finding]:
    found: List[Finding] = []
    if VOCABULARY not in texts:
        found.append((VOCABULARY, None, "V6 the vocabulary file is missing"))
    else:
        rows = terms_rows(texts[VOCABULARY])
        if rows != 1:
            found.append(
                (VOCABULARY, None, "V6 the Terms table has %d rows for the word, not 1" % rows)
            )
    for path in sorted(texts):
        if path == VOCABULARY:
            continue
        for number, line in enumerate(texts[path].split("\n"), 1):
            if TERMS_ROW.match(line):
                found.append((path, number, "V6 a second row defining the term"))
    if README in texts:
        lines = texts[README].split("\n")
        start = next((i for i, l in enumerate(lines) if l.startswith("# ")), None)
        if start is not None:
            if BARE_WORD.search(lines[start]):
                found.append((README, start + 1, "V6 the term is in the first heading"))
            i = start + 1
            while i < len(lines) and not lines[i].strip():
                i += 1
            while i < len(lines) and lines[i].strip():
                if BARE_WORD.search(lines[i]):
                    found.append((README, i + 1, "V6 the term is in the first paragraph"))
                i += 1
    return found


def format_finding(finding: Finding) -> str:
    path, line, reason = finding
    where = path if line is None else "%s:%d" % (path, line)
    return "%s: %s: %s" % (CHECK, where, reason)


def run(root: str) -> Tuple[int, List[str]]:
    """Run the check on root. Return (exit code, output lines)."""
    if IMPORT_ERROR is not None:
        raise IMPORT_ERROR
    missed = control()
    if missed is not None:
        return 2, ["%s: DEAD the positive control found no hit for %s" % (CHECK, missed)]
    try:
        files = repofiles.file_set(root)
    except repofiles.RepoFilesError as exc:
        return 2, ["%s: DEAD %s" % (CHECK, exc)]
    if not files:
        return 2, ["%s: DEAD the repository holds 0 files" % CHECK]

    findings: List[Finding] = []
    texts: Dict[str, str] = {}
    for path in files:
        full = os.path.join(root, *path.split("/"))
        with open(full, encoding="utf-8", errors="replace") as handle:
            texts[path] = handle.read()
        in_tests = path.split("/")[0] == TESTS_DIR and "/" in path
        in_runner = RUNNER_ROOT.match(path) is not None
        for rule, line, reason in scan_text(
            texts[path], path.endswith(".py"), in_tests, in_runner
        ):
            findings.append((path, line, "%s %s" % (rule, reason)))
    findings.extend(check_v6(texts))

    try:
        messages = repofiles.commit_messages(root)
        shas = repofiles.commit_short_ids(root)
        identities = repofiles.commit_identities(root)
    except repofiles.RepoFilesError as exc:
        return 2, ["%s: DEAD %s" % (CHECK, exc)]
    if len(messages) != len(shas) or len(identities) != len(shas):
        return 2, ["%s: DEAD the commit list changed while it was read" % CHECK]
    commit_lines = []
    for sha, message in zip(shas, messages):
        for rule, _line, reason in scan_text(message, False):
            if rule in COMMIT_RULES:
                commit_lines.append("%s: commit %s: %s %s" % (CHECK, sha, rule, reason))
    for identity in identities:
        sha = identity[0]
        for field, value in zip(IDENTITY_FIELDS, identity[1:]):
            for rule, _line, reason in scan_text(value, False):
                if rule in COMMIT_RULES:
                    commit_lines.append(
                        "%s: commit %s: %s %s %s" % (CHECK, sha, rule, field, reason)
                    )

    lines = [format_finding(f) for f in findings] + commit_lines
    total = len(lines)
    if total:
        lines.append("%s: %d finding(s)" % (CHECK, total))
        return 1, lines
    lines.append("%s: ok (%d files, %d commit messages)" % (CHECK, len(files), len(messages)))
    return 0, lines


def main(argv: Optional[List[str]] = None) -> int:
    default_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(description="Check the repository's vocabulary.")
    parser.add_argument("--root", default=default_root, help="repository root")
    args = parser.parse_args(argv)
    try:
        code, lines = run(os.path.abspath(args.root))
    except Exception as exc:
        print("%s: DEAD %s: %s" % (CHECK, type(exc).__name__, exc))
        return 2
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
