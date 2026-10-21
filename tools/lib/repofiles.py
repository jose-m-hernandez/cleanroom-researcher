"""The set of files a repository holds, as git sees it."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from typing import Dict, List, Optional, Tuple


class RepoFilesError(Exception):
    """The root is not a usable git checkout. Callers report this as DEAD."""


_DROPPED_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    # Any of these can set core.excludesFile and hide an untracked file from
    # the file set.
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
)
_DROPPED_ENV_PATTERN = re.compile(r"^GIT_CONFIG_(KEY|VALUE)_[0-9]+$")


_EMPTY_CONFIG_HOME: Optional[tempfile.TemporaryDirectory] = None


def _empty_config_home() -> str:
    """An empty directory, made on first use and removed when the process exits.

    Pointing XDG_CONFIG_HOME at it stops git reading the user's default ignore
    file, which GIT_CONFIG_GLOBAL does not stop."""
    global _EMPTY_CONFIG_HOME
    if _EMPTY_CONFIG_HOME is None:
        # The object removes the directory when it is collected or the process exits.
        _EMPTY_CONFIG_HOME = tempfile.TemporaryDirectory(prefix="repofiles-")
    return _EMPTY_CONFIG_HOME.name


def _env() -> Dict[str, str]:
    """A copy of the environment for git: the variables that redirect it are
    dropped, and no user or system config or ignore file is read."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in _DROPPED_ENV and not _DROPPED_ENV_PATTERN.match(k)
    }
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    # A graft file can cut a commit's parents and hide history; git reads this
    # one instead of the repository's own, and --no-replace-objects does not
    # turn grafts off.
    env["GIT_GRAFT_FILE"] = os.devnull
    env["XDG_CONFIG_HOME"] = _empty_config_home()
    return env


def _run(root: str, args: List[str], data: Optional[bytes] = None):
    """Run git in root with replace refs ignored and the environment cleaned."""
    cmd = ["git", "--no-replace-objects", "-C", root] + list(args)
    try:
        return subprocess.run(
            cmd,
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            env=_env(),
        )
    except OSError as exc:
        raise RepoFilesError("git is not available: %s" % exc)


def _git(root: str, *args: str) -> bytes:
    proc = _run(root, list(args))
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip().split("\n")[0]
        raise RepoFilesError("not a git checkout: %s (%s)" % (root, detail))
    return proc.stdout


def file_set(root: str) -> List[str]:
    """Repo-relative POSIX paths: tracked files plus untracked, unignored ones."""
    out = _git(
        root, "ls-files", "-z", "--cached", "--others", "--exclude-standard"
    )
    try:
        text = out.decode("utf-8")
    except UnicodeDecodeError:
        raise RepoFilesError("git listed a path that is not valid UTF-8")
    return sorted(set(p for p in text.split("\0") if p))


def is_shallow(root: str) -> bool:
    out = _git(root, "rev-parse", "--is-shallow-repository")
    return out.decode("utf-8").strip() == "true"


def _has_commits(root: str) -> bool:
    return _run(root, ["rev-parse", "-q", "--verify", "HEAD"]).returncode == 0


def _history(root: str, fmt: str) -> bytes:
    proc = _run(root, ["log", "--reverse", "--format=" + fmt, "HEAD"])
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip().split("\n")[0]
        raise RepoFilesError("git log failed in %s (%s)" % (root, detail))
    return proc.stdout


def _require_full_history(root: str) -> None:
    if is_shallow(root):
        raise RepoFilesError(
            "history checks need full history: run git fetch --unshallow"
        )


def _commit_ids(root: str) -> List[str]:
    proc = _run(root, ["rev-list", "--reverse", "HEAD"])
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip().split("\n")[0]
        raise RepoFilesError("git rev-list failed in %s (%s)" % (root, detail))
    return [i for i in proc.stdout.decode("ascii", "replace").split("\n") if i]


def _short(root: str, cid: str) -> str:
    """The abbreviation git itself uses for cid, the same as %h in a log."""
    proc = _run(root, ["rev-parse", "--short", cid])
    text = proc.stdout.decode("ascii", "replace").strip()
    if proc.returncode != 0 or not text:
        return cid[:7]
    return text


def _raw_objects(root: str, ids: List[str]) -> List[bytes]:
    """The raw bytes of each commit, read in one git process."""
    data = ("\n".join(ids) + "\n").encode("ascii")
    proc = _run(root, ["cat-file", "--batch"], data)
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip().split("\n")[0]
        raise RepoFilesError("git cat-file failed in %s (%s)" % (root, detail))
    out = proc.stdout
    pos = 0
    objects = []
    for cid in ids:
        eol = out.find(b"\n", pos)
        fields = out[pos:eol].split(b" ") if eol >= 0 else []
        if len(fields) != 3 or fields[1] != b"commit" or not fields[2].isdigit():
            raise RepoFilesError("commit %s could not be read" % _short(root, cid))
        size = int(fields[2])
        body = out[eol + 1 : eol + 1 + size]
        if len(body) != size:
            raise RepoFilesError("commit %s could not be read" % _short(root, cid))
        objects.append(body)
        pos = eol + 1 + size + 1
    return objects


def _split_commit(root: str, cid: str, raw: bytes) -> Tuple[bytes, bytes]:
    """(header bytes, body bytes) of a raw commit, after the NUL and encoding checks."""
    if b"\0" in raw:
        raise RepoFilesError("commit %s holds a NUL byte" % _short(root, cid))
    split = raw.find(b"\n\n")
    header = raw if split < 0 else raw[:split]
    body = b"" if split < 0 else raw[split + 2 :]
    for line in header.split(b"\n"):
        if line.startswith(b"encoding "):
            value = line[len(b"encoding ") :].decode("utf-8", "replace").strip()
            if value.upper() != "UTF-8":
                raise RepoFilesError(
                    "commit %s declares encoding %s" % (_short(root, cid), value)
                )
    return header, body


def _message_of(root: str, cid: str, raw: bytes) -> str:
    _header, body = _split_commit(root, cid, raw)
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        raise RepoFilesError(
            "commit %s holds a body that is not valid UTF-8" % _short(root, cid)
        )


_COVERED_HEADERS = ("tree", "parent", "author", "committer")
_ONCE_ONLY_HEADERS = ("tree", "author", "committer")
_HEX_ID = "[0-9a-f]{40}(?:[0-9a-f]{24})?"
_IDENTITY = "[^<>\\n]*<[^<>\\n]*> [0-9]+ [+-][0-9]{4}"
_STRICT_SHAPE = {
    "tree": re.compile("tree " + _HEX_ID),
    "parent": re.compile("parent " + _HEX_ID),
    "author": re.compile("author " + _IDENTITY),
    "committer": re.compile("committer " + _IDENTITY),
}


def _header_of(root: str, cid: str, raw: bytes) -> str:
    """The header text the scan reads, each continuation line joined to its header
    line by a newline.

    The first line of a group whose key is tree, parent, author or committer is
    dropped only when git's own parser fully covers it: the line must match that
    key's strict shape (a tree or parent line is an object id; an author or
    committer line is a name, one angle-bracket email, a decimal time and a
    signed four-digit zone), and a tree, author or committer key must appear
    exactly once in the header (a parent key may repeat, as merges have several).
    Any other first line of such a group is kept, key included, so junk after the
    zone, a second email field, a second tree line or a first of two author lines
    is scanned. Continuation lines of every group are kept, and every group with
    another key is kept whole."""
    header, _body = _split_commit(root, cid, raw)
    try:
        text = header.decode("utf-8")
    except UnicodeDecodeError:
        raise RepoFilesError(
            "commit %s holds a header that is not valid UTF-8" % _short(root, cid)
        )
    groups: List[List[str]] = []
    for line in text.split("\n"):
        if line.startswith(" ") and groups:
            groups[-1].append(line[1:])
        else:
            groups.append([line])
    counts: Dict[str, int] = {}
    for group in groups:
        key = group[0].split(" ", 1)[0]
        counts[key] = counts.get(key, 0) + 1
    kept = []
    for group in groups:
        key = group[0].split(" ", 1)[0]
        if key in _COVERED_HEADERS:
            well_formed = _STRICT_SHAPE[key].fullmatch(group[0]) is not None
            once = key not in _ONCE_ONLY_HEADERS or counts[key] == 1
            if well_formed and once:
                group = group[1:]
        if group:
            kept.append("\n".join(group))
    return "\n".join(kept)


def commit_messages(root: str) -> List[str]:
    """The full message of every commit reachable from HEAD, oldest first,
    read from each commit's raw object."""
    _require_full_history(root)
    if not _has_commits(root):
        return []
    ids = _commit_ids(root)
    if not ids:
        return []
    raws = _raw_objects(root, ids)
    return [_message_of(root, cid, raw) for cid, raw in zip(ids, raws)]


def commit_headers(root: str) -> List[str]:
    """Per commit reachable from HEAD, oldest first: the header lines that git's
    own parser does not fully cover (mergetag, gpgsig, encoding, any extra header,
    continuation lines, and any tree, parent, author or committer line that is
    malformed or repeated), keys included, joined with newlines. Empty when there
    are none."""
    _require_full_history(root)
    if not _has_commits(root):
        return []
    ids = _commit_ids(root)
    if not ids:
        return []
    raws = _raw_objects(root, ids)
    return [_header_of(root, cid, raw) for cid, raw in zip(ids, raws)]


def commit_short_ids(root: str) -> List[str]:
    """Abbreviated ids of the same commits, in the same order."""
    _require_full_history(root)
    if not _has_commits(root):
        return []
    text = _history(root, "%h").decode("utf-8", "replace")
    return [p for p in text.split("\n") if p]


def commit_identities(root: str) -> List[Tuple[str, str, str, str, str]]:
    """Per commit reachable from HEAD, oldest first: short id, author name,
    author email, committer name, committer email."""
    _require_full_history(root)
    if not _has_commits(root):
        return []
    text = _history(root, "%h%x00%an%x00%ae%x00%cn%x00%ce%x00").decode("utf-8", "replace")
    parts = text.split("\0")
    if parts and not parts[-1].strip():
        parts.pop()
    if len(parts) % 5 != 0:
        raise RepoFilesError("git log returned a malformed identity list")
    rows = []
    for i in range(0, len(parts), 5):
        # The log's line terminator follows each record, so later ones start with it.
        rows.append((parts[i].lstrip("\n"),) + tuple(parts[i + 1 : i + 5]))
    return rows


def current_branch(root: str) -> Optional[str]:
    """The short name of the checked-out branch, or None when HEAD is detached."""
    proc = _run(root, ["symbolic-ref", "-q", "--short", "HEAD"])
    if proc.returncode == 1:
        return None
    if proc.returncode != 0:
        raise RepoFilesError("git symbolic-ref failed in %s" % root)
    return proc.stdout.decode("utf-8", "replace").rstrip("\n")


def historic_blobs(root: str) -> Tuple[List[Tuple[str, str]], List[str]]:
    """Every blob in the tree of any commit reachable from HEAD, and every path
    that ever held one.

    Returns (blobs, paths). blobs holds one (blob id, path) pair per distinct
    blob, in commit order, with the path it had at the first commit that holds
    it. paths holds each distinct path once, in first-seen order.
    """
    _require_full_history(root)
    if not _has_commits(root):
        return [], []
    first_path: Dict[str, str] = {}
    paths: Dict[str, None] = {}
    for cid in _commit_ids(root):
        proc = _run(root, ["ls-tree", "-r", "-z", "--full-tree", cid])
        if proc.returncode != 0:
            raise RepoFilesError("git ls-tree failed in %s" % root)
        for entry in proc.stdout.split(b"\0"):
            if not entry:
                continue
            meta, _tab, raw_path = entry.partition(b"\t")
            fields = meta.split(b" ")
            if len(fields) != 3:
                raise RepoFilesError("git ls-tree returned a malformed entry")
            path = raw_path.decode("utf-8", "replace")
            paths.setdefault(path, None)
            if fields[1] != b"blob":
                continue  # a submodule link holds no content here
            blob = fields[2].decode("ascii", "replace")
            first_path.setdefault(blob, path)
    return list(first_path.items()), list(paths)


def blob_contents(root: str, ids: List[str], chunk: int = 64):
    """Yield the raw bytes of each blob in ids, in order."""
    for start in range(0, len(ids), chunk):
        part = ids[start : start + chunk]
        data = ("\n".join(part) + "\n").encode("ascii")
        proc = _run(root, ["cat-file", "--batch"], data)
        if proc.returncode != 0:
            raise RepoFilesError("git cat-file failed in %s" % root)
        out = proc.stdout
        pos = 0
        for _blob in part:
            eol = out.find(b"\n", pos)
            fields = out[pos:eol].split(b" ") if eol >= 0 else []
            if len(fields) != 3 or fields[1] != b"blob" or not fields[2].isdigit():
                raise RepoFilesError("a blob could not be read")
            size = int(fields[2])
            body = out[eol + 1 : eol + 1 + size]
            if len(body) != size:
                raise RepoFilesError("a blob could not be read")
            yield body
            pos = eol + 1 + size + 1


def blob_id_of(root: str, data: bytes) -> str:
    """The blob id git would give data, without writing anything."""
    proc = _run(root, ["hash-object", "--stdin"], data)
    if proc.returncode != 0:
        raise RepoFilesError("git hash-object failed in %s" % root)
    return proc.stdout.decode("ascii", "replace").strip()
