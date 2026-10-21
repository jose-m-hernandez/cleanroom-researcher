"""The admission check: every file has exactly one row saying why it may be here.

Usage: python3 tools/check_admission.py [--root PATH]

Output: finding lines ``admission: <path>[:<line>]: <reason>``, then one summary
line. Exit 0 pass, 1 findings, 2 dead or usage error.
"""

from __future__ import annotations

import argparse
import datetime
import os
import posixpath
import re
import stat
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional, Set, Tuple

try:
    from lib import header, repofiles

    IMPORT_ERROR: Optional[BaseException] = None
except Exception as _exc:  # reported as DEAD by run(), not as a traceback
    header = repofiles = None  # type: ignore[assignment]
    IMPORT_ERROR = _exc

CHECK = "admission"
LEDGER = "docs/admissions.md"
VOCABULARY = "docs/vocabulary.md"
LEDGER_HEADER = "| path | class | basis | provenance |"
CLASSES = ("sourced", "fixture", "original")
MAX_BYTES = 100000
GENERATOR_TIMEOUT = 120
HTTPS = "https" + ":" + "//"
RESERVED_HOSTS = ("example.com", "example.net", "example.org")
RESERVED_SUFFIXES = (".example", ".test", ".invalid", ".localhost")

SRC_ID = re.compile(r"^SRC-[0-9]{4}$")
SRC_WORD = re.compile(r"\bSRC-([0-9]{4})\b")
URL = re.compile("https?" + ":" + r"//[^\s`'\"<>)\]}]*", re.IGNORECASE)
ISO_DATE = re.compile(r"(?<![0-9])[0-9]{4}-[0-9]{2}-[0-9]{2}(?![0-9])")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SEPARATOR = re.compile(r"^\|(\s*:?-+:?\s*\|)+$")
FIXTURE_BASIS = re.compile(r"^generator=(\S+) seed=(-?[0-9]+)$")
VOCAB_ROW = re.compile(r"^\| `([^`]+)` \| (.+) \|$")

Finding = Tuple[str, Optional[int], str]
Row = Dict[str, object]


def valid_date(text: str) -> bool:
    if not re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$", text):
        return False
    try:
        datetime.date.fromisoformat(text)
    except ValueError:
        return False
    return True


def has_valid_date(text: str) -> bool:
    return any(valid_date(m.group(0)) for m in ISO_DATE.finditer(text))


def is_normal_path(path: str) -> bool:
    if not path or path.startswith("/"):
        return False
    if any(seg in ("", ".", "..") for seg in path.split("/")):
        return False
    return posixpath.normpath(path) == path


def host_of(url: str) -> str:
    rest = url[url.find("://") + 3:]
    # A browser reads a backslash as a slash, so it ends the authority too.
    netloc = re.split(r"[/?#\\]", rest, maxsplit=1)[0]
    netloc = netloc.rsplit("@", 1)[-1]
    if not netloc.startswith("["):
        netloc = netloc.rsplit(":", 1)[0]
    return netloc.lower()


def is_reserved_host(host: str) -> bool:
    if host == "localhost":
        return True
    for name in RESERVED_HOSTS:
        if host == name or host.endswith("." + name):
            return True
    return host.endswith(RESERVED_SUFFIXES)


def parse_ledger(text: str) -> Tuple[List[Row], List[Tuple[int, str]]]:
    rows: List[Row] = []
    problems: List[Tuple[int, str]] = []
    blocks: List[List[Tuple[int, str]]] = []
    current: Optional[List[Tuple[int, str]]] = None
    for number, line in enumerate(text.split("\n"), 1):
        if line.startswith("|"):
            if current is None:
                current = []
                blocks.append(current)
            current.append((number, line))
        else:
            current = None
    if len(blocks) != 1:
        problems.append((1, "A1 the ledger must hold exactly one table"))
        if not blocks:
            return rows, problems
    block = blocks[0]
    if block[0][1].strip() != LEDGER_HEADER:
        problems.append((block[0][0], "A1 ledger table header is not exact"))
    if len(block) < 2 or not SEPARATOR.match(block[1][1].strip()):
        problems.append((block[0][0], "A1 ledger table has no separator row"))
        body = block[1:]
    else:
        body = block[2:]
    for number, line in body:
        stripped = line.strip()
        cells = [c.strip() for c in stripped.split("|")[1:-1]]
        if not stripped.endswith("|") or len(cells) != 4:
            problems.append((number, "A1 malformed ledger row"))
            continue
        rows.append(
            {
                "line": number,
                "path": cells[0],
                "class": cells[1],
                "basis": cells[2],
                "provenance": cells[3],
            }
        )
    return rows, problems


def vocabulary_values(text: str, section: str) -> Set[str]:
    values: Set[str] = set()
    inside = False
    for line in text.split("\n"):
        if line.startswith("## "):
            inside = line[3:].strip() == section
        elif inside:
            match = VOCAB_ROW.match(line.strip())
            if match:
                values.add(match.group(1))
    return values


def check_a1(files: List[str], rows: List[Row]) -> List[Finding]:
    found: List[Finding] = []
    for path in files:
        mine = [r for r in rows if r["path"] == path]
        if not mine:
            found.append((path, None, "A1 not admitted"))
        for extra in mine[1:]:
            found.append((LEDGER, int(extra["line"]), "A1 duplicate row: " + path))  # type: ignore[arg-type]
    return found


def check_a2(files: Set[str], rows: List[Row]) -> List[Finding]:
    found: List[Finding] = []
    for row in rows:
        path = str(row["path"])
        line = int(row["line"])  # type: ignore[arg-type]
        if not is_normal_path(path):
            found.append((LEDGER, line, "A2 escapes root: " + path))
        elif path not in files:
            found.append((LEDGER, line, "A2 listed but missing: " + path))
    return found


def check_a3(rows: List[Row]) -> List[Finding]:
    found: List[Finding] = []
    for row in rows:
        line = int(row["line"])  # type: ignore[arg-type]
        if row["class"] not in CLASSES:
            found.append((LEDGER, line, "A3 unknown class '%s'" % row["class"]))
        if not has_valid_date(str(row["provenance"])):
            found.append(
                (LEDGER, line, "A3 provenance needs an ISO date: " + str(row["path"]))
            )
    return found


def run_generator(
    root: str, generator: str, seed: str, relpath: str, committed: bytes
) -> Optional[str]:
    """Run a fixture generator; return a problem text, or None when it matches."""
    with tempfile.TemporaryDirectory() as out:
        cmd = [sys.executable, generator, "--seed", seed, "--out", out]
        try:
            proc = subprocess.run(
                cmd,
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=GENERATOR_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return "generator timed out after %d s" % GENERATOR_TIMEOUT
        except OSError as exc:
            return "generator could not start: %s" % exc
        if proc.returncode >= 2:
            tail = proc.stderr.decode("utf-8", "replace").strip().split("\n")[-1]
            return "generator exited %d: %s" % (proc.returncode, tail)
        produced = os.path.join(out, *relpath.split("/"))
        if not os.path.isfile(produced):
            return "generator did not produce " + relpath
        with open(produced, "rb") as handle:
            fresh = handle.read()
    if fresh != committed:
        return "committed file differs from the generator output"
    return None


def check_a4(root: str, rows: List[Row], files: Set[str]) -> List[Finding]:
    found: List[Finding] = []
    first: Dict[str, Row] = {}
    for row in rows:
        first.setdefault(str(row["path"]), row)
    for row in rows:
        line = int(row["line"])  # type: ignore[arg-type]
        path = str(row["path"])
        cls = row["class"]
        basis = str(row["basis"])
        if cls == "sourced":
            ids = [part.strip() for part in basis.split(",")]
            for sid in ids:
                if not SRC_ID.match(sid):
                    found.append((LEDGER, line, "A4 bad source id '%s' for %s" % (sid, path)))
                elif "sources/%s/source.md" % sid not in files:
                    found.append((LEDGER, line, "A4 no sources/%s/source.md for %s" % (sid, path)))
        elif cls == "fixture":
            match = FIXTURE_BASIS.match(basis)
            if not match:
                found.append((LEDGER, line, "A4 fixture basis must be generator=<path> seed=<integer>: " + path))
                continue
            generator, seed = match.group(1), match.group(2)
            if not is_normal_path(generator) or not is_normal_path(path):
                found.append((LEDGER, line, "A4 generator or fixture path escapes root: " + path))
                continue
            gen_row = first.get(generator)
            if gen_row is None or gen_row["class"] != "original":
                found.append((LEDGER, line, "A4 generator %s has no original row" % generator))
                continue
            if path not in files:
                continue
            with open(os.path.join(root, *path.split("/")), "rb") as handle:
                committed = handle.read()
            problem = run_generator(root, generator, seed, path, committed)
            if problem:
                found.append((LEDGER, line, "A4 %s: %s" % (problem, path)))
        elif cls == "original":
            if basis != "-":
                found.append((LEDGER, line, "A4 original basis must be '-': " + path))
    return found


def line_matches(text: str, pattern: "re.Pattern[str]"):
    for number, line in enumerate(text.split("\n"), 1):
        for match in pattern.finditer(line):
            yield number, match


def check_a5(texts: Dict[str, str], existing: Set[str]) -> List[Finding]:
    found: List[Finding] = []
    for path, text in texts.items():
        for number, match in line_matches(text, SRC_WORD):
            sid = "SRC-" + match.group(1)
            if sid not in existing:
                found.append((path, number, "A5 names missing source " + sid))
    return found


def check_a6(texts: Dict[str, str], registered: Set[str]) -> List[Finding]:
    found: List[Finding] = []
    for path, text in texts.items():
        for number, match in line_matches(text, URL):
            url = match.group(0).rstrip(".,;:")
            if url in registered or is_reserved_host(host_of(url)):
                continue
            found.append((path, number, "A6 unregistered URL " + url))
    return found


def check_a7(contents: Dict[str, bytes], classes: Dict[str, str]) -> List[Finding]:
    found: List[Finding] = []
    for path, data in contents.items():
        if classes.get(path) == "fixture":
            continue
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            found.append((path, None, "A7 not valid UTF-8"))
    return found


def check_a8(sources: Dict[str, str], types: Optional[Set[str]]) -> List[Finding]:
    found: List[Finding] = []
    for path, text in sources.items():
        folder = path.split("/")[1]
        _title, pairs, problems = header.parse(text)
        for number, reason in problems:
            found.append((path, number, "A8 header: " + reason))
        if problems:
            continue
        keys = tuple(k for k, _ in pairs)
        if keys != header.SOURCE_KEYS:
            found.append((path, None, "A8 keys must be exactly " + ", ".join(header.SOURCE_KEYS)))
            continue
        values = dict(pairs)
        if values["id"] != folder or not SRC_ID.match(values["id"]):
            found.append((path, None, "A8 id must equal the folder name and have the shape SRC- plus four digits"))
        url = values["url"]
        if not url.startswith(HTTPS) or is_reserved_host(host_of(url)):
            found.append((path, None, "A8 url must be https with a real host"))
        if types is None:
            found.append((path, None, "A8 cannot read the source types in " + VOCABULARY))
        elif values["source_type"] not in types:
            found.append((path, None, "A8 unknown source_type '%s'" % values["source_type"]))
        if not valid_date(values["fetched"]):
            found.append((path, None, "A8 fetched must be an ISO date"))
        if not SHA256.match(values["sha256"]):
            found.append((path, None, "A8 sha256 must be 64 lowercase hex characters"))
        if values["snapshot"] not in ("yes", "no"):
            found.append((path, None, "A8 snapshot must be yes or no"))
    return found


def check_a9(sizes: Dict[str, int], classes: Dict[str, str]) -> List[Finding]:
    found: List[Finding] = []
    for path, size in sizes.items():
        if size > MAX_BYTES and classes.get(path) != "fixture":
            found.append((path, None, "A9 %d bytes is over %d" % (size, MAX_BYTES)))
    return found


def raw_segment_paths(paths: List[str]) -> List[str]:
    """The paths in which some segment is named _raw, in any letter case."""
    return [p for p in paths if any(seg.lower() == "_raw" for seg in p.split("/"))]


def check_a10(root: str) -> List[Finding]:
    """No path segment named _raw in any commit or on disk, ignored paths included."""
    found: List[Finding] = []
    _blobs, historic = repofiles.historic_blobs(root)
    for path in raw_segment_paths(historic):
        found.append(
            (path, None, "A10 _raw path in history; remove it from history, raw output stays outside the repository")
        )
    def unreadable(err: OSError) -> None:
        where = os.path.relpath(str(err.filename), root).replace(os.sep, "/")
        found.append(
            (where, None, "A10 cannot read this directory, so a _raw path inside it could be missed; make it readable")
        )

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False, onerror=unreadable):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        prefix = "" if rel_dir == "." else rel_dir + "/"
        if prefix == "":
            dirnames[:] = [d for d in dirnames if d != ".git"]
        hit_dirs = set()
        for name in sorted(dirnames + filenames):
            rel = prefix + name
            if raw_segment_paths([rel]):
                found.append(
                    (rel, None, "A10 _raw path on disk; move raw output outside the repository")
                )
                if name in dirnames:
                    hit_dirs.add(name)
        dirnames[:] = [d for d in dirnames if d not in hit_dirs]
    return found


def control() -> bool:
    """A known-bad in-memory input must produce a finding from each rule used."""
    bad_id = "SRC-" + "9999"
    bad_url = HTTPS + "docs.python.org/3/"
    one = any(r.startswith("A1 not admitted") for _, _, r in check_a1(["control.txt"], []))
    five = bool(check_a5({"control.txt": bad_id + "\n"}, set()))
    six = bool(check_a6({"control.txt": bad_url + "\n"}, set()))
    ten = bool(raw_segment_paths(["x/_raw/y"]))
    return one and five and six and ten


def format_finding(finding: Finding) -> str:
    path, line, reason = finding
    where = path if line is None else "%s:%d" % (path, line)
    return "%s: %s: %s" % (CHECK, where, reason)


def run(root: str) -> Tuple[int, List[str]]:
    """Run the check on root. Return (exit code, output lines)."""
    if IMPORT_ERROR is not None:
        raise IMPORT_ERROR
    if not control():
        return 2, ["%s: DEAD the positive control produced no finding" % CHECK]
    try:
        files = repofiles.file_set(root)
    except repofiles.RepoFilesError as exc:
        return 2, ["%s: DEAD %s" % (CHECK, exc)]
    if not files:
        return 2, ["%s: DEAD the repository holds 0 files" % CHECK]

    findings: List[Finding] = []
    contents: Dict[str, bytes] = {}
    for path in files:
        full = os.path.join(root, *path.split("/"))
        try:
            mode = os.lstat(full).st_mode
        except OSError:
            mode = 0
        if not stat.S_ISREG(mode):
            findings.append((path, None, "A0 cannot classify"))
            continue
        with open(full, "rb") as handle:
            contents[path] = handle.read()

    rows: List[Row] = []
    if LEDGER in contents:
        rows, problems = parse_ledger(contents[LEDGER].decode("utf-8", "replace"))
        findings.extend((LEDGER, n, r) for n, r in problems)
    else:
        findings.append((LEDGER, None, "A1 the ledger is missing"))

    classes: Dict[str, str] = {}
    for row in rows:
        classes.setdefault(str(row["path"]), str(row["class"]))
    fileset = set(files)
    texts = {p: d.decode("utf-8", "replace") for p, d in contents.items()}
    source_paths = sorted(
        p for p in contents
        if p.count("/") == 2 and p.startswith("sources/") and p.endswith("/source.md")
    )
    existing = set(p.split("/")[1] for p in source_paths)
    registered: Set[str] = set()
    for path in source_paths:
        for key, value in header.parse(texts[path])[1]:
            if key == "url":
                registered.add(value)
    types = None
    if VOCABULARY in texts:
        types = vocabulary_values(texts[VOCABULARY], "Source types")

    findings.extend(check_a1(files, rows))
    findings.extend(check_a2(fileset, rows))
    findings.extend(check_a3(rows))
    findings.extend(check_a4(root, rows, fileset))
    findings.extend(check_a5(texts, existing))
    findings.extend(check_a6(texts, registered))
    findings.extend(check_a7(contents, classes))
    findings.extend(check_a8({p: texts[p] for p in source_paths}, types))
    findings.extend(check_a9({p: len(d) for p, d in contents.items()}, classes))
    findings.extend(check_a10(root))

    lines = [format_finding(f) for f in findings]
    if findings:
        lines.append("%s: %d finding(s)" % (CHECK, len(findings)))
        return 1, lines
    lines.append("%s: ok (%d files, 0 findings)" % (CHECK, len(files)))
    return 0, lines


def main(argv: Optional[List[str]] = None) -> int:
    default_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(description="Check that every file is admitted.")
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
