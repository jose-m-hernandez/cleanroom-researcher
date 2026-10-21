"""The maintainers' barred-string scan.

Usage: python3 tools/barred_scan.py [--root PATH]

The list of barred strings is private. It arrives as the whole text in the
environment variable BARRED_STRINGS (the CI secret), or as a file path in
BARRED_STRINGS_PATH (a maintainer, locally). If both are set, BARRED_STRINGS
wins; an empty BARRED_STRINGS counts as not set.

The list, line by line, read as UTF-8 with a leading BOM stripped:

- each line is stripped; blank lines and lines starting with a hash are skipped;
- an entry is the text up to the line's first TAB, with whitespace stripped from
  both of its ends; if the next TAB-separated
  field is exactly ``word`` (any case) the entry matches whole words only,
  and any other text after the first TAB is a note and is ignored;
- entries are numbered from 1, in file order, after skipping.

Folding is applied to entries and to everything scanned, in this order: NFKC,
casefold, removal of every character of Unicode category Cf, and collapsing
each run of whitespace to one space. An entry that is empty after folding makes
the scan DEAD. A substring entry hits wherever its fold occurs in the fold of
the text. A whole-word entry hits only where the occurrence is bounded on each
side by the start or end of the text or by a character that is not a word
character; a word character is a letter (category L*) or a decimal digit (Nd),
and everything else, the underscore included, is a boundary.

Entry 1 is a mandatory canary. Every run, before scanning, the canary is
written alone on a line to a file in a fresh temporary directory and that file
is scanned with the same matcher; no hit is DEAD.

What is scanned: the content of every blob in the tree of every commit
reachable from HEAD (each distinct blob once, decoded as UTF-8 with
replacement) and of the current files (uncommitted ones count); every path
string that ever existed; every commit's full message, author and committer
name and email, and header lines (every header line except the first line of a
tree, parent, author or committer group that git's own parser fully covers: such
a line is skipped only when it fully matches its key's strict shape, an object
id for tree and parent, or a name, one angle-bracket email, a decimal time and a
signed four-digit zone for author and committer, and, for tree, author and
committer, that key appears exactly once in the header; a parent key may repeat.
Any other line is kept, key included, and so are all continuation lines: a
mergetag with its embedded tag, gpgsig, encoding, any extra header, junk after a
zone, a second email field, a repeated tree or author line); and the branch name. A detached HEAD and a shallow clone are
DEAD. For each entry, a file reports its first hit only.

Output is ordinals only: a term, its fold, or the matched text is never
printed. A path that itself hits an entry is shown with the hitting parts
removed, or withheld. Every line starts with ``barred: `` and holds no raw
control character. Nothing is written to any file the runner reads.
Exit 0 clean, 1 findings, 2 dead. Any exception prints its type name only.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
import unicodedata
from typing import Dict, List, Mapping, Optional, Tuple

try:
    from lib import repofiles

    IMPORT_ERROR: Optional[BaseException] = None
except Exception as _exc:  # reported as DEAD by main(), not as a traceback
    repofiles = None  # type: ignore[assignment]
    IMPORT_ERROR = _exc

CHECK = "barred"
ENV_LIST = "BARRED_STRINGS"
ENV_PATH = "BARRED_STRINGS_PATH"
NO_LIST = "no list (set %s or %s)" % (ENV_LIST, ENV_PATH)
CANARY_SHAPE = re.compile("barred-strings-" + "canary-" + "[a-z0-9]{8,}")
IDENTITY_FIELDS = ("author", "author email", "committer", "committer email")

Hit = Tuple[int, int]  # (entry number, offset in the folded text)


class Dead(Exception):
    """The scan cannot be trusted. The reason is written here, never from the list."""


class Entry:
    """One barred string: its number, its raw text, its fold, and its mode."""

    __slots__ = ("number", "raw", "folded", "word")

    def __init__(self, number: int, raw: str, word: bool) -> None:
        self.number = number
        self.raw = raw
        self.folded = fold(raw)
        self.word = word


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.casefold()
    text = "".join(c for c in text if unicodedata.category(c) != "Cf")
    return re.sub(r"\s+", " ", text)


def is_word_char(char: str) -> bool:
    category = unicodedata.category(char)
    return category.startswith("L") or category == "Nd"


def parse_list(text: str) -> List[Entry]:
    if text.startswith("\ufeff"):
        text = text[1:]
    entries: List[Entry] = []
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        entry = Entry(len(entries) + 1, fields[0].strip(), len(fields) > 1 and fields[1].lower() == "word")
        if not entry.folded:
            raise Dead("entry #%d is empty after fold" % entry.number)
        entries.append(entry)
    return entries


def load_list(environ: Mapping[str, str]) -> str:
    text = environ.get(ENV_LIST)
    if text:
        try:
            text.encode("utf-8")
        except UnicodeEncodeError:
            raise Dead("the list is not valid UTF-8")
        return text
    path = environ.get(ENV_PATH)
    if path:
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError:
            raise Dead("the list file could not be read")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            raise Dead("the list is not valid UTF-8")
    raise Dead(NO_LIST)


def first_hit(folded: str, entry: Entry) -> int:
    """Offset of the entry's first valid occurrence in folded text, or -1."""
    needle = entry.folded
    start = 0
    while True:
        index = folded.find(needle, start)
        if index < 0:
            return -1
        if not entry.word:
            return index
        end = index + len(needle)
        before_ok = index == 0 or not is_word_char(folded[index - 1])
        after_ok = end == len(folded) or not is_word_char(folded[end])
        if before_ok and after_ok:
            return index
        start = index + 1


def scan_text(text: str, entries: List[Entry]) -> List[Hit]:
    """The first hit of each entry in text, as (entry number, folded offset)."""
    folded = fold(text)
    hits: List[Hit] = []
    for entry in entries:
        index = first_hit(folded, entry)
        if index >= 0:
            hits.append((entry.number, index))
    return hits


def scan_file(path: str, entries: List[Entry]) -> List[Hit]:
    with open(path, "rb") as handle:
        data = handle.read()
    return scan_text(data.decode("utf-8", errors="replace"), entries)


def line_of(text: str, offset: int) -> int:
    """The line of text holding the folded offset: the first line whose fold,
    taken from the start of the text, reaches past it."""
    starts = [0] + [i + 1 for i, char in enumerate(text) if char == "\n"]
    low, high = 0, len(starts) - 1
    while low < high:
        mid = (low + high) // 2
        if len(fold(text[: starts[mid + 1]])) > offset:
            high = mid
        else:
            low = mid + 1
    return low + 1


def escape(text: str) -> str:
    """ASCII only, with every control and non-ASCII character written as an escape."""
    out = []
    for char in text:
        code = ord(char)
        if 32 <= code < 127:
            out.append(char)
        elif code < 0x10000:
            out.append("\\u%04x" % code)
        else:
            out.append("\\U%08x" % code)
    return "".join(out)


def display_path(path: str, entries: List[Entry]) -> str:
    """The path as it may be printed: never a hit, never a raw control character."""
    shown = escape(path)
    if not scan_text(shown, entries) and not scan_text(path, entries):
        return shown
    parts = ["[hit]" if scan_text(p, entries) else escape(p) for p in path.split("/")]
    shown = "/".join(parts)
    if scan_text(shown, entries):
        return "[path withheld]"
    return shown


def content_findings(path: str, data: bytes, entries: List[Entry]) -> List[str]:
    text = data.decode("utf-8", errors="replace")
    hits = scan_text(text, entries)
    if not hits:
        return []
    shown = display_path(path, entries)
    return [
        "%s: %s:%d: entry #%d" % (CHECK, shown, line_of(text, offset), number)
        for number, offset in hits
    ]


def check_canary(entries: List[Entry]) -> None:
    canary = entries[0]
    if not CANARY_SHAPE.fullmatch(canary.folded):
        raise Dead("entry 1 is not a canary")
    with tempfile.TemporaryDirectory() as tmp:
        probe = os.path.join(tmp, "canary.txt")
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write(canary.raw + "\n")
        hits = scan_file(probe, entries)
    if not any(number == 1 for number, _offset in hits):
        raise Dead("the canary did not match")


def current_files(root: str, paths: List[str]) -> List[Tuple[str, bytes]]:
    """(path, content) of each current file; a symlink holds its target text."""
    found = []
    for path in paths:
        full = os.path.join(root, *path.split("/"))
        if os.path.islink(full):
            found.append((path, os.fsencode(os.readlink(full))))
        elif os.path.isfile(full):
            with open(full, "rb") as handle:
                found.append((path, handle.read()))
    return found


def run(
    root: str, environ: Mapping[str, str], loaded: Optional[List[Entry]] = None
) -> Tuple[int, List[str]]:
    """Scan root. Return (exit code, output lines). Raises Dead when untrustworthy.

    When loaded is given, the parsed entries are appended to it as soon as they
    exist, so a caller handling an exception can match text against them."""
    if IMPORT_ERROR is not None:
        raise IMPORT_ERROR
    entries = parse_list(load_list(environ))
    if not entries:
        raise Dead(NO_LIST)
    if loaded is not None:
        loaded.extend(entries)
    check_canary(entries)

    branch = repofiles.current_branch(root)
    if branch is None:
        raise Dead("detached HEAD (no branch name to scan)")
    messages = repofiles.commit_messages(root)
    shas = repofiles.commit_short_ids(root)
    identities = repofiles.commit_identities(root)
    headers = repofiles.commit_headers(root)
    if (
        len(messages) != len(shas)
        or len(identities) != len(shas)
        or len(headers) != len(shas)
    ):
        raise Dead("the commit list changed while it was read")

    findings: List[str] = []
    blobs, historic_paths = repofiles.historic_blobs(root)
    seen = set(blob for blob, _path in blobs)
    contents = repofiles.blob_contents(root, [blob for blob, _path in blobs])
    for (_blob, path), data in zip(blobs, contents):
        findings.extend(content_findings(path, data, entries))

    files = repofiles.file_set(root)
    for path, data in current_files(root, files):
        blob = repofiles.blob_id_of(root, data)
        if blob in seen:
            continue
        seen.add(blob)
        findings.extend(content_findings(path, data, entries))

    all_paths: Dict[str, None] = {}
    for path in historic_paths + files:
        all_paths.setdefault(path, None)
    for path in all_paths:
        hits = scan_text(path, entries)
        if hits:
            shown = display_path(path, entries)
            for number, _offset in hits:
                findings.append("%s: %s: entry #%d (path)" % (CHECK, shown, number))

    for sha, message in zip(shas, messages):
        for number, _offset in scan_text(message, entries):
            findings.append("%s: commit %s: entry #%d (message)" % (CHECK, sha, number))
    for identity in identities:
        for field, value in zip(IDENTITY_FIELDS, identity[1:]):
            for number, _offset in scan_text(value, entries):
                findings.append(
                    "%s: commit %s: entry #%d (%s)" % (CHECK, identity[0], number, field)
                )

    for sha, header in zip(shas, headers):
        for number, _offset in scan_text(header, entries):
            findings.append("%s: commit %s: entry #%d (header)" % (CHECK, sha, number))

    for number, _offset in scan_text(branch, entries):
        findings.append("%s: branch: entry #%d" % (CHECK, number))

    if findings:
        findings.append("%s: %d finding(s)" % (CHECK, len(findings)))
        return 1, findings
    findings.append(
        "%s: ok (%d entries, %d blobs, %d commits, canary matched)"
        % (CHECK, len(entries), len(seen), len(shas))
    )
    return 0, findings


WITHHELD = "%s: DEAD [message withheld]" % CHECK


def safe_message(message: str, entries: List[Entry]) -> Optional[str]:
    """The message as it may be printed, or None when it or its escape hits."""
    shown = escape(message)
    if not entries or scan_text(message, entries) or scan_text(shown, entries):
        return None
    return shown


def main(argv: Optional[List[str]] = None) -> int:
    default_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(description="Scan the repository for barred strings.")
    parser.add_argument("--root", default=default_root, help="repository root")
    args = parser.parse_args(argv)
    loaded: List[Entry] = []
    try:
        code, lines = run(os.path.abspath(args.root), os.environ, loaded)
    except Dead as exc:
        print("%s: DEAD %s" % (CHECK, escape(str(exc))))
        return 2
    except Exception as exc:
        if repofiles is not None and isinstance(exc, repofiles.RepoFilesError):
            shown = safe_message(str(exc), loaded)
            print(WITHHELD if shown is None else "%s: DEAD %s" % (CHECK, shown))
        else:
            print("%s: DEAD %s" % (CHECK, type(exc).__name__))
        return 2
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
