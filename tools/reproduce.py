"""The reproduce command: rebuild an experiment's generated files and compare.

Usage: python3 tools/reproduce.py EXP-NNNN [--root PATH]
       python3 tools/reproduce.py --all [--root PATH]

Give exactly one of an experiment id or --all. For each fixture row in
docs/admissions.md whose path is under experiments/<id>/, the generator named
in the row's basis is run in a temporary folder and its output is compared with
the committed bytes. Nothing is written to the repository. A row is refused,
without running its generator, when the ledger's own rules refuse it: a path
that is not normal, a fixture outside its experiment, a generator with no
original row, or a generator or fixture that is a symlink, sits under a
symlink, or is not a regular file.

Output: one line per generated file, ``reproduce: <path>: rebuilt, identical``
or ``reproduce: <path>: differs (<reason>)``, then one summary line per
experiment, ``reproduce: <id>: <n> rebuilt, <i> identical, <d> differ``. An
experiment with no fixture rows prints ``reproduce: <id> has no generated
files``. An unknown experiment prints ``reproduce: no experiment <id>; the
experiments are the folders under experiments/``. An unreadable ledger prints
``reproduce: docs/admissions.md cannot be read: <problem>; run tools/gate.py to
see every finding``. --all with no experiments prints ``reproduce: no
experiments``. A root with no docs/admissions.md prints ``reproduce: <root> has
no docs/admissions.md; point --root at the repository root``. A symlinked
experiment folder, an off-pattern folder under --all, and a fixture row whose
experiment folder is missing are each reported, and the run exits 1.

Exit 0 every file is identical, or there was nothing to rebuild. Exit 1 a file
differs, the experiment is unknown, or the ledger is missing or cannot be read. Exit 2 usage
error or dead: an unexpected error prints ``reproduce: DEAD <Name>: <message>``.
"""

from __future__ import annotations

import argparse
import os
import stat
import sys
from typing import List, Optional

try:
    import check_admission
    from lib import paths

    IMPORT_ERROR: Optional[BaseException] = None
except Exception as _exc:  # reported as DEAD by main(), not as a traceback
    check_admission = paths = None  # type: ignore[assignment]
    IMPORT_ERROR = _exc

CHECK = "reproduce"


def say(lines: List[str], text: str) -> None:
    lines.append("%s: %s" % (CHECK, text))


def read_ledger(root: str):
    """Return (rows, first problem text); the problem is None when it reads."""
    path = os.path.join(root, *check_admission.LEDGER.split("/"))
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        return [], "the file cannot be opened (%s)" % type(exc).__name__
    rows, problems = check_admission.parse_ledger(text)
    if problems:
        return rows, problems[0][1]
    return rows, None


def unsafe_file(root: str, relpath: str) -> bool:
    """True when relpath, or a folder on the way to it, is a symlink, or it is not a regular file.

    A path that does not exist yet is not unsafe here; the caller reports it as missing.
    """
    current = root
    parts = relpath.split("/")
    for index, part in enumerate(parts):
        current = os.path.join(current, part)
        try:
            mode = os.lstat(current).st_mode
        except OSError:
            return False
        if stat.S_ISLNK(mode):
            return True
        if index == len(parts) - 1 and not stat.S_ISREG(mode):
            return True
    return False


def refuse_row(root: str, experiment: str, row, generator: str, rows) -> Optional[str]:
    """Return the reason the ledger's own rules refuse this fixture row, else None."""
    relpath = str(row["path"])
    if not check_admission.is_normal_path(generator) or not check_admission.is_normal_path(
        relpath
    ):
        return "generator or fixture path is not a normal path inside the repository"
    if not relpath.startswith("experiments/%s/" % experiment):
        return "fixture path is outside experiments/%s/" % experiment
    first = None
    for other in rows:
        if str(other["path"]) == generator:
            first = other
            break
    if first is None or first["class"] != "original":
        return "generator %s has no original row" % generator
    for checked in (generator, relpath):
        if unsafe_file(root, checked):
            return "%s is a symlink or not a regular file" % checked
    return None


def rebuild_row(root: str, row, experiment: str, rows) -> Optional[str]:
    """Return None when the row's file is rebuilt identical, else the reason."""
    relpath = str(row["path"])
    match = check_admission.FIXTURE_BASIS.match(str(row["basis"]))
    if not match:
        return "the basis must be generator=<path> seed=<int>"
    refused = refuse_row(root, experiment, row, match.group(1), rows)
    if refused is not None:
        return refused
    full = os.path.join(root, *relpath.split("/"))
    try:
        with open(full, "rb") as handle:
            committed = handle.read()
    except OSError:
        return "the committed file is missing or cannot be read"
    return check_admission.run_generator(
        root, match.group(1), match.group(2), relpath, committed
    )


def reproduce_one(root: str, experiment: str, rows, lines: List[str]) -> int:
    prefix = "experiments/%s/" % experiment
    mine = [
        r for r in rows if r["class"] == "fixture" and str(r["path"]).startswith(prefix)
    ]
    if not mine:
        say(lines, "%s has no generated files" % experiment)
        return 0
    identical = 0
    differ = 0
    for row in mine:
        problem = rebuild_row(root, row, experiment, rows)
        if problem is None:
            identical += 1
            say(lines, "%s: rebuilt, identical" % row["path"])
        else:
            differ += 1
            say(lines, "%s: differs (%s)" % (row["path"], problem))
    say(
        lines,
        "%s: %d rebuilt, %d identical, %d differ" % (experiment, len(mine), identical, differ),
    )
    return 1 if differ else 0


def symlink_line(name: str) -> str:
    return (
        "experiments/%s is a symlink; the experiments are real folders under experiments/"
        % name
    )


def survey(root: str, lines: List[str]):
    """Return (experiment folders to process, 1 if a folder was reported else 0)."""
    base = os.path.join(root, "experiments")
    if not os.path.isdir(base):
        return [], 0
    names: List[str] = []
    code = 0
    for name in sorted(os.listdir(base)):
        full = os.path.join(base, name)
        if not os.path.isdir(full):
            continue
        if not paths.EXPERIMENT_ID.match(name):
            say(lines, "experiments/%s is not an experiment folder (EXP-NNNN)" % name)
            code = 1
        elif os.path.islink(full):
            say(lines, symlink_line(name))
            code = 1
        else:
            names.append(name)
    return names, code


def orphan_rows(rows, names: List[str], lines: List[str]) -> int:
    code = 0
    for row in rows:
        if row["class"] != "fixture":
            continue
        parts = str(row["path"]).split("/")
        if len(parts) >= 3 and parts[0] == "experiments" and parts[1] not in names:
            say(
                lines,
                "%s: differs (its experiment folder experiments/%s/ is missing or not an experiment)"
                % (row["path"], parts[1]),
            )
            code = 1
    return code


def run(root: str, experiment: Optional[str], everything: bool):
    lines: List[str] = []
    ledger = os.path.join(root, *check_admission.LEDGER.split("/"))
    if not os.path.exists(ledger):
        say(
            lines,
            "%s has no %s; point --root at the repository root" % (root, check_admission.LEDGER),
        )
        return 1, lines
    code = 0
    if everything:
        names, code = survey(root, lines)
    else:
        names = [str(experiment)]
        folder = os.path.join(root, "experiments", names[0])
        if paths.EXPERIMENT_ID.match(names[0]) and os.path.islink(folder):
            say(lines, symlink_line(names[0]))
            return 1, lines
        if not paths.EXPERIMENT_ID.match(names[0]) or not os.path.isdir(folder):
            say(
                lines,
                "no experiment %s; the experiments are the folders under experiments/"
                % names[0],
            )
            return 1, lines
    rows, problem = read_ledger(root)
    if problem is not None:
        say(
            lines,
            "%s cannot be read: %s; run tools/gate.py to see every finding"
            % (check_admission.LEDGER, problem),
        )
        return 1, lines
    if everything:
        if orphan_rows(rows, names, lines):
            code = 1
        if not names and not lines:
            say(lines, "no experiments")
            return 0, lines
    for name in names:
        if reproduce_one(root, name, rows, lines):
            code = 1
    return code, lines


def main(argv: Optional[List[str]] = None) -> int:
    default_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(
        description="Rebuild an experiment's generated files and compare them with the committed bytes."
    )
    parser.add_argument("experiment", nargs="?", help="an experiment id, EXP-NNNN")
    parser.add_argument("--all", action="store_true", help="every experiment")
    parser.add_argument("--root", default=default_root, help="repository root")
    args = parser.parse_args(argv)
    if bool(args.experiment) == bool(args.all):
        parser.error("give exactly one of an experiment id or --all")
    try:
        if IMPORT_ERROR is not None:
            raise IMPORT_ERROR
        code, lines = run(os.path.abspath(args.root), args.experiment, args.all)
    except Exception as exc:
        print("%s: DEAD %s: %s" % (CHECK, type(exc).__name__, exc))
        return 2
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
