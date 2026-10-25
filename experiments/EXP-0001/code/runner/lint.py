"""The clean-room lint of EXP-0001 (README P:249): the repository's vocabulary rules V1 to V4 and its barred-string
scan, run over a fixture store before the seal.

The barred list is read exactly as the repository's own scan reads it, from the BARRED_STRINGS or BARRED_STRINGS_PATH
environment variable. With no list, or a list whose first entry is not a working canary, the lint is DEAD (exit 2),
never a pass. It prints entry numbers and paths only, never a matched text. A fixture store holds no git folder and no
symlink, so both are findings, and neither is followed. Nothing here makes a model call.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "tools"))
import barred_scan  # noqa: E402  (the repository's barred-string scan)
import check_vocabulary  # noqa: E402  (the repository's vocabulary rules)

RULES = ("V1", "V2", "V3", "V4")


def command_lint(fixtures_root, environ=None, out=None) -> int:
    environ = os.environ if environ is None else environ
    out = out or sys.stdout
    root = str(fixtures_root)
    try:
        refused = os.path.islink(root) or not os.path.isdir(root)
    except OSError:
        refused = True
    if refused:
        print("lint: REFUSED fixtures root is not a folder", file=out)
        return 2
    try:
        entries = barred_scan.parse_list(barred_scan.load_list(str(ROOT), environ))
        if not entries:
            raise barred_scan.Dead(barred_scan.NO_LIST)
        barred_scan.check_canary(entries)
    except barred_scan.Dead as exc:
        print("lint: DEAD " + barred_scan.escape(str(exc)), file=out)
        return 2
    findings = []
    files = 0
    for here, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        filenames.sort()
        rel_here = os.path.relpath(here, root)
        rel_here = "" if rel_here == "." else Path(rel_here).as_posix()
        names = [(n, True) for n in dirnames] + [(n, False) for n in filenames]
        keep = []
        for name, is_dir in sorted(names):
            rel = f"{rel_here}/{name}" if rel_here else name
            full = os.path.join(here, name)
            shown = barred_scan.display_path(rel, entries)
            for number, _offset in barred_scan.scan_text(rel, entries):
                findings.append(f"lint: {shown}: path holds entry #{number}")
            if os.path.islink(full):
                findings.append(f"lint: {shown}: symlink (not followed)")
                continue
            if is_dir:
                if name == ".git":
                    findings.append(f"lint: {shown}: git folder (a fixture store holds none; not scanned)")
                else:
                    keep.append(name)
                continue
            try:
                with open(full, "rb") as fh:
                    data = fh.read()
            except OSError:
                findings.append(f"lint: {shown}: unreadable")
                continue
            files += 1
            text = data.decode("utf-8", errors="replace")
            for rule, line, reason in check_vocabulary.scan_text(text, False):
                if rule in RULES:
                    findings.append(f"lint: {shown}:{line}: {rule} {reason}")
            findings.extend(barred_scan.content_findings(shown, data, entries))
        dirnames[:] = keep
    if not findings:
        print(f"lint: ok ({files} files, {len(entries)} entries, canary matched)", file=out)
        return 0
    for line in findings:
        print(line, file=out)
    print(f"lint: FAIL ({len(findings)} findings)", file=out)
    return 1
