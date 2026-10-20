"""The set of files a repository holds, as git sees it."""

from __future__ import annotations

import subprocess
from typing import List


class RepoFilesError(Exception):
    """The root is not a usable git checkout. Callers report this as DEAD."""


def _git(root: str, *args: str) -> bytes:
    cmd = ["git", "-C", root] + list(args)
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
        )
    except OSError as exc:
        raise RepoFilesError("git is not available: %s" % exc)
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
