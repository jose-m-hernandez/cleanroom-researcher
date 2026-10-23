"""The maintainers' barred-string scan.

Usage: python3 tools/barred_scan.py [--root PATH] [--ids]

The list of barred strings is private. It arrives as the whole text in the
environment variable BARRED_STRINGS (the CI secret), or as a file path in
BARRED_STRINGS_PATH (a maintainer, locally). If both are set, BARRED_STRINGS
wins; an empty BARRED_STRINGS counts as not set. A list read from
BARRED_STRINGS_PATH gets the allow file's guards, in this order, and each is
DEAD: the path is missing, a symlink or not a regular file; the file has more
than one link; the path resolves inside the repository (a gitignored list there
would not be scanned); the file cannot be read. When BARRED_STRINGS is used the
path is not examined.

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

The allow list is private, like the list, and is how a maintainer approves one
exact use: one entry, in one git object. It arrives as the whole text in the
environment variable BARRED_ALLOW (the CI secret), or as a file path in
BARRED_ALLOW_PATH (a maintainer, locally). Both unset or empty means no allow
lines, and the scan behaves as if this feature did not exist. In CI, meaning the
environment variable GITHUB_ACTIONS equals ``true``, only BARRED_ALLOW is
accepted. These are DEAD: both variables set; BARRED_ALLOW_PATH in CI; a path
that is missing, a symlink or not a regular file; a file with more than one
link (a hard link, which can give one file a second name outside the
repository); a path that resolves inside the repository; a file that cannot be
read.

The allow list is ASCII only; any other byte is DEAD. It is read after the
canary check and before any scanning, split on newlines, and each line, in
order, is:

- DEAD if it holds a carriage return;
- skipped if it is blank after stripping, or its first character is a hash;
- otherwise exactly three TAB-separated fields, or DEAD: an object id of 40
  lowercase hex characters, an entry number (a decimal from 2 up, no leading
  zero, never 1, the canary), and a reason of printable ASCII with no space at
  either end; the reason is scanned against the list and a hit is DEAD, and a
  line that repeats an earlier (object id, entry number) pair is DEAD.

A line skips a content finding when its object id is the blob's id and its
entry number is the finding's entry; a blob is one object however many paths
hold it. A line skips a commit finding (message, author or committer name or
email, header) when its object id is the full commit id and its entry number
is the finding's entry. A path finding and a branch finding are never skipped.
Each skipped finding counts as allowed. When the allowed count is above 0 the
summary lines add it: ``A allowed``. A line that skipped nothing in this run is
itself a finding, ``allow line N: stale``, which counts in the total. A DEAD
text, like every output line, never holds a reason, a list text or an object id
read from the allow list.

The option ``--ids`` appends `` [object <id>]`` to each content finding (the
full blob id) and each commit finding (the full commit id): the lines an allow
line could skip. Path, branch, stale and summary lines get nothing. It is DEAD
in CI. Without it every output line is as it was before the allow list.
"""

from __future__ import annotations

import argparse
import os
import re
import stat
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
ENV_ALLOW = "BARRED_ALLOW"
ENV_ALLOW_PATH = "BARRED_ALLOW_PATH"
ALLOW_ID = re.compile("[0-9a-f]{40}")
ALLOW_ENTRY = re.compile("[1-9][0-9]*")
ALLOW_REASON = re.compile("[\\x21-\\x7e]([\\x20-\\x7e]*[\\x21-\\x7e])?")
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


def inside_repository(root: str, path: str) -> bool:
    """Whether path lies under root. Raises OSError when a stat fails.

    Identity, not spelling: a case-insensitive file system names one folder
    many ways, and only the file system knows they are the same folder."""
    root_stat = os.stat(root)
    folder = os.path.dirname(os.path.realpath(path))
    while True:
        if os.path.samestat(root_stat, os.stat(folder)):
            return True
        parent = os.path.dirname(folder)
        if parent == folder:
            return False
        folder = parent


def load_list(root: str, environ: Mapping[str, str]) -> str:
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
            info = os.lstat(path)
        except OSError:
            raise Dead("the list file is missing, a symlink or not a regular file")
        if not stat.S_ISREG(info.st_mode):
            raise Dead("the list file is missing, a symlink or not a regular file")
        if info.st_nlink > 1:
            raise Dead(
                "the list file is a hard link (it has more than one name); "
                "use a file with a single name"
            )
        try:
            inside = inside_repository(root, path)
        except OSError:
            raise Dead("the list file could not be read")
        if inside:
            raise Dead("the list file must live outside the repository")
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


def in_ci(environ: Mapping[str, str]) -> bool:
    return environ.get("GITHUB_ACTIONS") == "true"


def load_allow(root: str, environ: Mapping[str, str]) -> bytes:
    """The raw allow list, or b"" when neither variable is set. Raises Dead."""
    text = environ.get(ENV_ALLOW)
    path = environ.get(ENV_ALLOW_PATH)
    if text and path:
        raise Dead("set %s or %s, not both" % (ENV_ALLOW, ENV_ALLOW_PATH))
    if path and in_ci(environ):
        raise Dead("%s is not accepted in CI" % ENV_ALLOW_PATH)
    if text:
        try:
            return text.encode("utf-8")
        except UnicodeEncodeError:
            raise Dead("the allow list is not ASCII")
    if not path:
        return b""
    try:
        info = os.lstat(path)
    except OSError:
        raise Dead("the allow file is missing, a symlink or not a regular file")
    if not stat.S_ISREG(info.st_mode):
        raise Dead("the allow file is missing, a symlink or not a regular file")
    if info.st_nlink > 1:
        raise Dead(
            "the allow file is a hard link (it has more than one name); "
            "use a file with a single name"
        )
    try:
        inside = inside_repository(root, path)
    except OSError:
        raise Dead("the allow file could not be read")
    if inside:
        raise Dead("the allow file must live outside the repository")
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        raise Dead("the allow file could not be read")


def parse_allow(data: bytes, entries: List["Entry"]) -> List[Tuple[int, str, int]]:
    """The allow lines as (line number, object id, entry number). Raises Dead.

    No text of a line is ever put in a Dead message."""
    if any(byte > 0x7F for byte in data):
        raise Dead("the allow list is not ASCII")
    lines: List[Tuple[int, str, int]] = []
    seen: Dict[Tuple[str, int], int] = {}
    for number, line in enumerate(data.decode("ascii").split("\n"), 1):
        if "\r" in line:
            raise Dead("allow line %d: carriage return" % number)
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 3:
            raise Dead("allow line %d: not three TAB-separated fields" % number)
        oid, entry, reason = fields
        if not ALLOW_ID.fullmatch(oid):
            raise Dead("allow line %d: object id is not 40 lowercase hex characters" % number)
        if not ALLOW_ENTRY.fullmatch(entry) or entry == "1":
            raise Dead("allow line %d: entry number is not a decimal from 2 up" % number)
        if not ALLOW_REASON.fullmatch(reason):
            raise Dead("allow line %d: reason is empty, padded or not printable" % number)
        if scan_text(reason, entries):
            raise Dead("allow line %d: reason contains a barred string" % number)
        pair = (oid, int(entry))
        if pair in seen:
            raise Dead("allow line %d: repeats line %d" % (number, seen[pair]))
        seen[pair] = number
        lines.append((number, oid, int(entry)))
    return lines


class Allow:
    """The parsed allow lines, and which of them skipped a finding."""

    def __init__(self, lines: List[Tuple[int, str, int]]) -> None:
        self.lines = lines
        self.by_pair = {(oid, entry): number for number, oid, entry in lines}
        self.used: set = set()
        self.count = 0

    def skips(self, oid: Optional[str], entry: int) -> bool:
        number = self.by_pair.get((oid, entry))
        if number is None:
            return False
        self.used.add(number)
        self.count += 1
        return True

    def stale(self) -> List[int]:
        return [number for number, _oid, _entry in self.lines if number not in self.used]


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


def content_findings(
    path: str,
    data: bytes,
    entries: List[Entry],
    blob: Optional[str] = None,
    allow: Optional[Allow] = None,
    ids: bool = False,
) -> List[str]:
    text = data.decode("utf-8", errors="replace")
    hits = scan_text(text, entries)
    if allow is not None:
        hits = [(number, offset) for number, offset in hits if not allow.skips(blob, number)]
    if not hits:
        return []
    shown = display_path(path, entries)
    suffix = " [object %s]" % blob if ids and blob else ""
    return [
        "%s: %s:%d: entry #%d%s" % (CHECK, shown, line_of(text, offset), number, suffix)
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
    root: str,
    environ: Mapping[str, str],
    loaded: Optional[List[Entry]] = None,
    ids: bool = False,
) -> Tuple[int, List[str]]:
    """Scan root. Return (exit code, output lines). Raises Dead when untrustworthy.

    When loaded is given, the parsed entries are appended to it as soon as they
    exist, so a caller handling an exception can match text against them."""
    if IMPORT_ERROR is not None:
        raise IMPORT_ERROR
    if ids and in_ci(environ):
        raise Dead("--ids is not accepted in CI")
    entries = parse_list(load_list(root, environ))
    if not entries:
        raise Dead(NO_LIST)
    if loaded is not None:
        loaded.extend(entries)
    check_canary(entries)
    allow = Allow(parse_allow(load_allow(root, environ), entries))

    branch = repofiles.current_branch(root)
    if branch is None:
        raise Dead("detached HEAD (no branch name to scan)")
    messages = repofiles.commit_messages(root)
    shas = repofiles.commit_short_ids(root)
    full_ids = repofiles.commit_ids(root)
    identities = repofiles.commit_identities(root)
    headers = repofiles.commit_headers(root)
    if (
        len(messages) != len(shas)
        or len(full_ids) != len(shas)
        or len(identities) != len(shas)
        or len(headers) != len(shas)
    ):
        raise Dead("the commit list changed while it was read")

    findings: List[str] = []
    blobs, historic_paths = repofiles.historic_blobs(root)
    seen = set(blob for blob, _path in blobs)
    contents = repofiles.blob_contents(root, [blob for blob, _path in blobs])
    for (blob, path), data in zip(blobs, contents):
        findings.extend(content_findings(path, data, entries, blob, allow, ids))

    files = repofiles.file_set(root)
    for path, data in current_files(root, files):
        blob = repofiles.blob_id_of(root, data)
        if blob in seen:
            continue
        seen.add(blob)
        findings.extend(content_findings(path, data, entries, blob, allow, ids))

    all_paths: Dict[str, None] = {}
    for path in historic_paths + files:
        all_paths.setdefault(path, None)
    for path in all_paths:
        hits = scan_text(path, entries)
        if hits:
            shown = display_path(path, entries)
            for number, _offset in hits:
                findings.append("%s: %s: entry #%d (path)" % (CHECK, shown, number))

    def commit_finding(cid: str, sha: str, number: int, what: str) -> None:
        if allow.skips(cid, number):
            return
        suffix = " [object %s]" % cid if ids else ""
        findings.append("%s: commit %s: entry #%d (%s)%s" % (CHECK, sha, number, what, suffix))

    for cid, sha, message in zip(full_ids, shas, messages):
        for number, _offset in scan_text(message, entries):
            commit_finding(cid, sha, number, "message")
    for cid, identity in zip(full_ids, identities):
        for field, value in zip(IDENTITY_FIELDS, identity[1:]):
            for number, _offset in scan_text(value, entries):
                commit_finding(cid, identity[0], number, field)

    for cid, sha, header in zip(full_ids, shas, headers):
        for number, _offset in scan_text(header, entries):
            commit_finding(cid, sha, number, "header")

    for number, _offset in scan_text(branch, entries):
        findings.append("%s: branch: entry #%d" % (CHECK, number))

    for number in allow.stale():
        findings.append("%s: allow line %d: stale" % (CHECK, number))

    allowed = ", %d allowed" % allow.count if allow.count else ""
    if findings:
        findings.append("%s: %d finding(s)%s" % (CHECK, len(findings), allowed))
        return 1, findings
    findings.append(
        "%s: ok (%d entries, %d blobs, %d commits%s, canary matched)"
        % (CHECK, len(entries), len(seen), len(shas), allowed)
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
    parser.add_argument("--ids", action="store_true", help="show the object id of each approvable finding")
    args = parser.parse_args(argv)
    loaded: List[Entry] = []
    try:
        code, lines = run(os.path.abspath(args.root), os.environ, loaded, args.ids)
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
