"""The set of files a repository holds, as git sees it."""

from __future__ import annotations

import os
import subprocess
from typing import Dict, List, Optional, Tuple


class RepoFilesError(Exception):
    """The root is not a usable git checkout. Callers report this as DEAD."""


_DROPPED_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")


def _env() -> Dict[str, str]:
    """A copy of the environment without the variables that redirect git."""
    return {k: v for k, v in os.environ.items() if k not in _DROPPED_ENV}


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
            raise RepoFilesError("commit %s could not be read" % cid[:7])
        size = int(fields[2])
        body = out[eol + 1 : eol + 1 + size]
        if len(body) != size:
            raise RepoFilesError("commit %s could not be read" % cid[:7])
        objects.append(body)
        pos = eol + 1 + size + 1
    return objects


def _message_of(cid: str, raw: bytes) -> str:
    short = cid[:7]
    if b"\0" in raw:
        raise RepoFilesError("commit %s holds a NUL byte" % short)
    split = raw.find(b"\n\n")
    header = raw if split < 0 else raw[:split]
    body = b"" if split < 0 else raw[split + 2 :]
    for line in header.split(b"\n"):
        if line.startswith(b"encoding "):
            value = line[len(b"encoding ") :].decode("utf-8", "replace").strip()
            if value.upper() != "UTF-8":
                raise RepoFilesError("commit %s declares encoding %s" % (short, value))
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        raise RepoFilesError("commit %s holds a body that is not valid UTF-8" % short)


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
    return [_message_of(cid, raw) for cid, raw in zip(ids, raws)]


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
