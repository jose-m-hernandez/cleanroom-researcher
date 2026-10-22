"""The new-target tool: the skeleton of a new target, in one command.

Usage: python3 tools/new_target.py --title TEXT --type TYPE --url URL
    --publisher TEXT --fetched DATE --sha256 HEX [--commit HEX]
    --licence TEXT --licence-basis TEXT [--notes TEXT] [--protocol VALUE]
    --provenance TEXT [--root PATH]

What it writes, with <S> the next source id and <E> the next experiment id:

- sources/<S>/source.md, the source record of the pinned target;
- experiments/<E>/README.md, the experiment record, with TODO placeholders;
- experiments/<E>/read-log.md, an empty read log;
- three rows in docs/admissions.md, one for each file above.

The next number is one more than the highest folder of that kind on disk, so
a gap is never refilled and an untracked folder still counts.

What it refuses, with exit 1, before it writes anything: an argument that is
missing, empty, or holds a pipe or a line break; a type, protocol or licence
that the vocabulary does not allow; a URL that is not https with a real host;
a date that is not an ISO date; a hash of the wrong shape; --commit for any
type but code-repo (and a missing --commit for code-repo); and any path it
would write that already exists, or whose parent folder is a file, a symlink
or outside --root. It never overwrites or deletes a file. The
ledger only gains rows.

Output: one line ``new_target: wrote <path>`` per path. Exit 0 success, 1
refusal (``new_target: <what is wrong>; <how to fix it>`` on stderr), 2 dead
(``new_target: DEAD <type>: <message>``).

It never fetches anything: fetch and pin the target first by following docs/adding-a-target.md.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from typing import Dict, List, Optional, Tuple

try:
    import check_admission
    import check_experiments
    from lib import header, paths

    IMPORT_ERROR: Optional[BaseException] = None
except Exception as _exc:  # reported as DEAD by main(), not as a traceback
    check_admission = check_experiments = header = paths = None  # type: ignore[assignment]
    IMPORT_ERROR = _exc

TOOL = "new_target"
VOCABULARY = "docs/vocabulary.md"
LEDGER = "docs/admissions.md"
SOURCES_DIR = "sources"
EXPERIMENTS_DIR = "experiments"
DEFAULT_PROTOCOL = "preregistered"
READ_LOG_KIND = "contemporaneous"
CODE_REPO = "code-repo"
LICENCE_REQUIRED = ("code-repo", "release-artifact")
SOURCE_FOLDER = re.compile(r"^SRC-([0-9]{4})$")
EXPERIMENT_FOLDER = re.compile(r"^EXP-([0-9]{4})$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")

SECTIONS = (
    ("Abstract", "TODO: write one paragraph that says what was asked, what was done and what was found."),
    ("Question", "TODO: state the question this experiment answers."),
    ("Hypotheses and planned analysis", "TODO: list each hypothesis and say how the results will be analysed."),
    ("Materials", "TODO: list what the experiment uses; the method and its code go under %s."),
    ("Methods", "TODO: describe the steps of the method."),
    ("Results", "TODO: report the results, with a pointer to the recorded data."),
    ("Analysis", "TODO: analyse the results as planned and say what they show."),
    ("Threats to validity", "TODO: list what could make the results wrong."),
    ("Deviations from the preregistration", "TODO: list each departure from the protocol as committed, or write none."),
    ("How to reproduce", "TODO: give the exact commands that repeat this experiment from this repository."),
    ("How to replicate", "TODO: say how someone could test the same question with new materials."),
    ("Provenance", "TODO: say who ran the experiment, when, and with what."),
    ("Changelog", "TODO: list each change to this record, with its date."),
)
EXPLORATORY_ABSTRACT = (
    "TODO: write one paragraph that says what was asked, what was done and what was found; "
    "say that the protocol is exploratory, not committed before the first recorded run."
)


class Refusal(Exception):
    """An invalid input, reported as ``what is wrong; how to fix it``."""


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> "None":  # type: ignore[override]
        raise Refusal("%s; run with --help to see every argument" % message)


def build_parser(default_root: str) -> argparse.ArgumentParser:
    parser = Parser(description="Create the skeleton for a new target.")
    parser.add_argument("--root", default=default_root, help="repository root")
    parser.add_argument("--title", required=True, help="title of both records")
    parser.add_argument("--type", required=True, dest="source_type", help="a Source types value")
    parser.add_argument("--url", required=True, help="https URL with a real host")
    parser.add_argument("--publisher", required=True)
    parser.add_argument("--fetched", required=True, help="ISO date of the fetch")
    parser.add_argument("--sha256", required=True, help="64 lowercase hex characters")
    parser.add_argument("--commit", default=None, help="40 lowercase hex; code-repo only")
    parser.add_argument("--licence", required=True)
    parser.add_argument("--licence-basis", required=True, dest="licence_basis")
    parser.add_argument("--notes", default="-")
    parser.add_argument("--protocol", default=DEFAULT_PROTOCOL, help="an Experiment protocols value")
    parser.add_argument("--provenance", required=True, help="the ledger provenance cell; needs an ISO date")
    return parser


def read_text(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def check_value(name: str, value: str) -> None:
    if not value.strip():
        raise Refusal("--%s is empty; give it a value" % name)
    if value != value.strip():
        raise Refusal("--%s has leading or trailing spaces; remove them" % name)
    if "|" in value or "\n" in value or "\r" in value:
        raise Refusal("--%s contains a pipe or a line break; remove it" % name)


def validate(args: argparse.Namespace, types: set, protocols: set) -> None:
    for name in ("title", "source_type", "url", "publisher", "fetched", "sha256",
                 "licence", "licence_basis", "notes", "protocol", "provenance"):
        check_value(name.replace("_", "-"), getattr(args, name))
    if args.commit is not None:
        check_value("commit", args.commit)
    if args.source_type not in types:
        raise Refusal(
            "--type '%s' is not a Source types value; use one of: %s"
            % (args.source_type, ", ".join(sorted(types)))
        )
    if args.protocol not in protocols:
        raise Refusal(
            "--protocol '%s' is not an Experiment protocols value; use one of: %s"
            % (args.protocol, ", ".join(sorted(protocols)))
        )
    url = args.url
    if (not url.startswith(check_admission.HTTPS) or re.search(r"\s", url)
            or not check_admission.host_of(url)
            or check_admission.is_reserved_host(check_admission.host_of(url))):
        raise Refusal("--url must be https with a real host, not a reserved one; give the address of the target itself")
    if not check_admission.valid_date(args.fetched):
        raise Refusal("--fetched '%s' is not an ISO date like 2026-01-31; write the day the target was fetched" % args.fetched)
    if not check_admission.SHA256.match(args.sha256):
        raise Refusal("--sha256 must be 64 lowercase hex characters; compute it with shasum -a 256")
    if args.source_type == CODE_REPO:
        if args.commit is None:
            raise Refusal("--commit is required for a code-repo; pass the 40-character commit that git rev-parse HEAD prints")
        if not COMMIT.match(args.commit):
            raise Refusal("--commit must be 40 lowercase hex characters; pass the full commit id")
    elif args.commit is not None:
        raise Refusal("--commit is only for a code-repo; remove it")
    if args.licence == "-" and args.source_type in LICENCE_REQUIRED:
        raise Refusal("--licence must name the licence for a %s, not '-'; read the licence file" % args.source_type)
    if not check_admission.has_valid_date(args.provenance):
        raise Refusal("--provenance needs an ISO date like 2026-01-31; add the date")


def next_number(folder: str, pattern: "re.Pattern[str]") -> int:
    """One more than the highest numbered folder on disk, or 1."""
    highest = 0
    if os.path.isdir(folder):
        for name in os.listdir(folder):
            match = pattern.match(name)
            if match and os.path.isdir(os.path.join(folder, name)):
                highest = max(highest, int(match.group(1)))
    return highest + 1


def header_block(keys: Tuple[str, ...], values: Dict[str, str]) -> List[str]:
    return ["%s: %s" % (key, values[key]) for key in keys]


def source_text(args: argparse.Namespace, source_id: str) -> str:
    values = {
        "id": source_id,
        "url": args.url,
        "publisher": args.publisher,
        "source_type": args.source_type,
        "fetched": args.fetched,
        "sha256": args.sha256,
        "snapshot": "no",
        "licence": args.licence,
        "licence_basis": args.licence_basis,
        "notes": args.notes,
    }
    keys = header.SOURCE_KEYS
    if args.source_type == CODE_REPO:
        values["commit"] = args.commit
        keys = header.CODE_REPO_KEYS
    lines = ["# " + args.title, ""] + header_block(keys, values)
    return "\n".join(lines) + "\n"


def readme_text(args: argparse.Namespace, source_id: str, experiment_id: str) -> str:
    values = {
        "id": experiment_id,
        "subject": source_id,
        "protocol": args.protocol,
        "read_log": READ_LOG_KIND,
        "spend_cap_usd": "-",
        "launched_by": "-",
    }
    lines = ["# " + args.title, ""] + header_block(header.EXPERIMENT_KEYS, values)
    for title, placeholder in SECTIONS:
        if title == "Abstract" and args.protocol == "exploratory":
            placeholder = EXPLORATORY_ABSTRACT
        elif title == "Materials":
            placeholder = placeholder % (paths.METHOD_DIR + "/")
        lines.extend(["", "## " + title, "", placeholder])
    return "\n".join(lines) + "\n"


def read_log_text() -> str:
    return "# Read log\n\n" + check_experiments.READ_LOG_EMPTY


def ledger_with_rows(text: str, rows: List[str]) -> str:
    """The ledger text with rows inserted right after the table's last row."""
    _parsed, problems = check_admission.parse_ledger(text)
    body = text.rstrip("\n")
    if problems or not body.split("\n")[-1].startswith("|"):
        raise Refusal("%s does not end with its one table; repair the ledger first" % LEDGER)
    return body + "\n" + "\n".join(rows) + "\n" + text[len(body):].lstrip("\n")


def replace_ledger(path: str, text: str) -> None:
    folder = os.path.dirname(path)
    mode = os.stat(path).st_mode & 0o777
    handle, temporary = tempfile.mkstemp(dir=folder, prefix=".ledger-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(text)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def check_parent(root: str, relpath: str) -> None:
    """Refuse unless every existing folder on the way to relpath is a real folder
    and the parent, resolved, is inside root. Nothing is created here."""
    real_root = os.path.realpath(root)
    current = root
    for part in relpath.split("/")[:-1]:
        current = os.path.join(current, part)
        shown = os.path.relpath(current, root).replace(os.sep, "/")
        if os.path.islink(current):
            raise Refusal("%s is a symlink; replace it with a real folder inside --root" % shown)
        if os.path.lexists(current) and not os.path.isdir(current):
            raise Refusal("%s is a file where a folder must be; move it aside" % shown)
    resolved = os.path.realpath(os.path.dirname(os.path.join(root, *relpath.split("/"))))
    if resolved != real_root and not resolved.startswith(real_root + os.sep):
        raise Refusal("%s would be written outside --root; use a real folder inside --root" % relpath)


def create(path: str, text: str) -> None:
    with open(path, "x", encoding="utf-8") as handle:
        handle.write(text)


def write_target(args: argparse.Namespace) -> List[str]:
    root = os.path.abspath(args.root)
    vocabulary = os.path.join(root, *VOCABULARY.split("/"))
    ledger = os.path.join(root, *LEDGER.split("/"))
    if not os.path.isfile(vocabulary) or not os.path.isfile(ledger):
        raise Refusal("%s or %s is missing under --root; point --root at the repository root" % (VOCABULARY, LEDGER))
    vocab_text = read_text(vocabulary)
    types = check_admission.vocabulary_values(vocab_text, "Source types")
    protocols = check_admission.vocabulary_values(vocab_text, "Experiment protocols")
    if not types or not protocols:
        raise Refusal("%s has no Source types or Experiment protocols table; restore that file" % VOCABULARY)
    validate(args, types, protocols)

    source_id = "SRC-%04d" % next_number(os.path.join(root, SOURCES_DIR), SOURCE_FOLDER)
    experiment_id = "EXP-%04d" % next_number(os.path.join(root, EXPERIMENTS_DIR), EXPERIMENT_FOLDER)
    planned = [
        ("%s/%s/source.md" % (SOURCES_DIR, source_id), source_text(args, source_id)),
        ("%s/%s/README.md" % (EXPERIMENTS_DIR, experiment_id), readme_text(args, source_id, experiment_id)),
        ("%s/%s/read-log.md" % (EXPERIMENTS_DIR, experiment_id), read_log_text()),
    ]
    check_parent(root, LEDGER)
    if os.path.islink(ledger):
        raise Refusal("%s is a symlink; replace it with the real file inside --root" % LEDGER)
    for relpath, _text in planned:
        check_parent(root, relpath)
        if os.path.lexists(os.path.join(root, *relpath.split("/"))):
            raise Refusal("%s already exists; move it aside, because this tool never overwrites" % relpath)
    rows = ["| %s | original | - | %s |" % (relpath, args.provenance) for relpath, _t in planned]
    new_ledger = ledger_with_rows(read_text(ledger), rows)

    written: List[str] = []
    for relpath, text in planned:
        full = os.path.join(root, *relpath.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        create(full, text)
        written.append(relpath)
    replace_ledger(ledger, new_ledger)
    written.append(LEDGER)
    return written


def main(argv: Optional[List[str]] = None) -> int:
    default_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        if IMPORT_ERROR is not None:
            raise IMPORT_ERROR
        args = build_parser(default_root).parse_args(argv)
        written = write_target(args)
    except Refusal as exc:
        print("%s: %s" % (TOOL, exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print("%s: DEAD %s: %s" % (TOOL, type(exc).__name__, exc), file=sys.stderr)
        return 2
    for relpath in written:
        print("%s: wrote %s" % (TOOL, relpath))
    return 0


if __name__ == "__main__":
    sys.exit(main())
