"""The `author` command of EXP-0001: one tool-less model call that writes held-out fixture specs.

The prompt sent is the prompt file's text, a blank line, the guide's text and, when given, a blank line and an extra
file's text (each part with its trailing newlines stripped). Nothing else. The reply is split mechanically: a header
line holding a held-out id, then a fenced block; the text between the fences is checked with json.loads (accept or
reject only) and written byte for byte as <out>/<id>/spec.json. Authoring does no reformat; a repair call is made only with `--repair`
(command_repair: one call, see repair.py). The command prints counts and labels, never spec text. It makes at most 4
calls in all, repair included (a persisted counter), and at most 3 replacements.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

import claude_run
import fixtures
import isolation
import judge
import repair

MAX_RUNS = 4
MAX_REPLACEMENTS = 3
ID_LINE = re.compile(r"^heldout-\d{3}-[a-z0-9-]+$")
BLOCK = re.compile(r"^(?P<head>[^\n]*)\n```[^\n]*\n(?P<body>.*?)^```[ \t]*$", re.M | re.S)
RUNS_NAME, REPLACEMENTS_NAME = "author-runs.json", "author-replacements.json"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_list(path):
    """(rows, "") for a ledger that is a missing file (empty) or a JSON list of objects; else (None, label)."""
    path = Path(path)
    if not path.exists():
        return [], ""
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, f"{path.name} is unreadable"
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        return None, f"{path.name} is malformed"
    return rows, ""


def append_row(path, row: dict) -> None:
    rows, reason = read_list(path)
    if rows is None:
        raise ValueError(reason)
    claude_run.atomic_write(path, json.dumps(rows + [row], indent=1) + "\n")


def row_mode(row: dict) -> str:
    """The mode of an author-runs.json row; a row without one (an older ledger) is read as `author`."""
    return row.get("mode") or "author"


def replacement_refusal(replaces, environ) -> str:
    """"" when this call may name these ids as replaced, else the refusal label (before any launch)."""
    if replaces is None:
        return ""
    if not replaces or not all(ID_LINE.match(i) for i in replaces):
        return "--replaces needs ids of the form heldout-NNN-name"
    root = claude_run.eval_root(environ)
    done, reason = read_list(root / REPLACEMENTS_NAME)
    runs, why = read_list(root / RUNS_NAME)
    if done is None or runs is None:
        return reason or why
    if len(done) + len(replaces) > MAX_REPLACEMENTS:
        return f"at most {MAX_REPLACEMENTS} replacements (a replacement made for a generation failure counts)"
    made = {i for r in runs if r.get("replaces") for i in r.get("ids_written", [])}
    if any(i in made for i in replaces):
        return "a replacement is not itself replaced"
    return ""


def split_reply(text: str) -> list:
    """[(header, body)] for each header line plus fenced block, in order. The body is the text between the fences."""
    return [(m.group("head").strip(), m.group("body")) for m in BLOCK.finditer(text)]


def _write_blocks(blocks, out_dir: Path, only_ids, limit, out) -> tuple:
    """Write the accepted blocks; return (ids written, count of blocks refused). validate_spec labels are printed
    as `<id>: <label>` and do not change the count."""
    written, labels, extra = [], 0, 0
    for number, (head, body) in enumerate(blocks, 1):
        name = head if ID_LINE.match(head) else f"block-{number}"
        if limit is not None and len(written) >= limit:
            extra += 1
            continue
        if not ID_LINE.match(head):
            print(f"{name}: header_invalid", file=out)
            labels += 1
            continue
        if only_ids is not None and head not in only_ids:
            continue
        try:
            json.loads(body)
        except ValueError:
            print(f"{name}: json_invalid", file=out)
            labels += 1
            continue
        target = out_dir / head
        if target.exists():
            print(f"{name}: exists", file=out)
            labels += 1
            continue
        claude_run.atomic_write(target / "spec.json", body)
        written.append(head)
        for label in fixtures.validate_spec(json.loads(body), directory_name=head):
            print(f"{head}: {label}", file=out)  # a finding for the person; the spec stays as written
    if extra:
        print(f"author: {extra} extra blocks not written", file=out)
    return written, labels


def command_author(prompt_path, guide_path, out_path, extra_path=None, only_ids=None, replaces=None,
                   environ=None, out=None, runner=None) -> int:
    environ = environ if environ is not None else os.environ
    out = out or sys.stdout
    if claude_run.inside_clone(out_path):
        print("author: REFUSED --out is inside the repository clone", file=out)
        return 2
    if environ.get("CPR_EVAL_LIVE") != "1":
        print("live run refused: set CPR_EVAL_LIVE=1", file=out)
        return 2
    if environ.get("CLAUDE_CONFIG_DIR"):
        print("unset CLAUDE_CONFIG_DIR", file=out)
        return 3
    refusal = replacement_refusal(replaces, environ)
    if refusal:
        print(f"author: REFUSED {refusal}", file=out)
        return 2
    return _with_stops(lambda cfg: _run(cfg, prompt_path, guide_path, Path(out_path), extra_path, only_ids, replaces,
                                        environ, out, runner), environ, out)


def _with_stops(work, environ, out) -> int:
    """Load the config, run the preflight, then work(cfg); the stops every call shares print a label and exit 1."""
    try:
        cfg = claude_run.load_config()
        reason = claude_run.preflight(cfg, environ)
        if reason:
            print(f"author: STOP preflight: {reason}", file=out)
            return 1
        return work(cfg)
    except judge.JudgeBudgetError as exc:
        if "spent" in str(exc):
            print(f"author: STOP the author run budget is spent ({MAX_RUNS} runs)", file=out)
        else:
            print("author: STOP the author call counter is unreadable", file=out)
    except (claude_run.SpendError, claude_run.SpendCapReached, claude_run.LiveRefused, claude_run.ConfigError,
            claude_run.MemoryDirError) as exc:
        print(f"author: STOP {exc}", file=out)
    except Exception as exc:  # label only: exception text can carry paths
        print(f"author: ERROR {type(exc).__name__}", file=out)
    return 1


def _read_inputs(prompt_path, guide_path, extra_path):
    """(parts, hashes) with each file read as bytes; an unreadable file raises OSError or ValueError."""
    parts, hashes = [], {}
    for key, path in (("prompt", prompt_path), ("guide", guide_path), ("extra", extra_path)):
        if path is None:
            hashes[key] = None
            continue
        raw = Path(path).read_bytes()
        hashes[key] = _sha(raw)
        parts.append(raw.decode("utf-8").rstrip("\n"))
    return parts, hashes


def _launch(cfg, prompt, environ, runner, on_counted=None):
    """Count the call, make it and prove isolation: (call number, run record, proof). Authoring and repair share it,
    so a repair call carries the same keyword set, and so the same argv and env, as an authoring call. on_counted
    (repair only) is told the call number as soon as the call is counted, so a raise later still knows it."""
    judge.take_call(environ, MAX_RUNS, "author")  # the fifth call is refused here, before any launch
    number = judge.load_stats(environ, "author")["count"]
    if on_counted:
        on_counted(number)
    sandbox = claude_run.make_sandbox("author", environ)
    runner = runner or claude_run.run_claude
    rec = runner(cfg, prompt, sandbox, label="author", phase="authoring", kind="probe", notools=True,
                 environ=environ)
    return number, rec, isolation.check(rec, cfg, isolation.host_markers(environ))


def _row(number, rec, proof, mode, guide_sha, prompt_sha, extra_sha, replaces) -> dict:
    return {"run": number, "mode": mode, "started_utc": rec.get("started_utc"),
            "claude_code_version": (rec.get("pins") or {}).get("claude_code_version"),
            "model": (rec.get("pins") or {}).get("model"), "isolation": "PASS" if proof["ok"] else "FAIL",
            "guide_sha256": guide_sha, "prompt_sha256": prompt_sha, "extra_sha256": extra_sha,
            "replaces": replaces, "ids_written": [], "cost_usd": rec.get("cost_usd")}


def _run(cfg, prompt_path, guide_path, out_dir, extra_path, only_ids, replaces, environ, out, runner) -> int:
    try:
        parts, hashes = _read_inputs(prompt_path, guide_path, extra_path)
    except (OSError, ValueError):
        print("author: STOP an input file is unreadable", file=out)
        return 1
    prompt = "\n\n".join(parts)
    number, rec, proof = _launch(cfg, prompt, environ, runner)
    row = _row(number, rec, proof, "replace" if replaces else "author", hashes["guide"], hashes["prompt"],
               hashes["extra"], list(replaces or []))
    root = claude_run.eval_root(environ)
    try:
        if not proof["ok"]:
            judge.bump(environ, "failed", "author")
            print(f"author: STOP isolation proof failed: {', '.join(proof['failures'])}", file=out)
            return 1
        judge.bump(environ, "successful", "author")
        for i in replaces or []:
            append_row(root / REPLACEMENTS_NAME, {"replaces": i, "run": number})
        text = (isolation.result_event(rec["events"]) or {}).get("result")
        if not isinstance(text, str):
            print("author: STOP the reply holds no text", file=out)
            return 1
        written, labels = _write_blocks(split_reply(text), out_dir,
                                        set(only_ids) if only_ids else None,
                                        len(replaces) if replaces else None, out)
        row["ids_written"] = written
        print(f"author: wrote {len(written)} specs", file=out)
        return 0 if written and not labels else 1
    finally:
        append_row(root / RUNS_NAME, row)


def command_repair(source, fixtures_root, guide_path, out_path, environ=None, out=None, runner=None) -> int:
    """One call that lets the blind agent repair the probes the floor and oracle results point at (P:252-256).
    --out is a new store (every file of --fixtures-root, the repaired specs over the selected ones) and the repair log is
    written beside it as <out>.repair-log.md; both appear only after full success. Every guard refuses (exit 2) before a
    call is counted; a call that fails the proof or returns an invalid reply uses the one repair round and writes nothing."""
    environ = environ if environ is not None else os.environ
    out = out or sys.stdout
    out_dir = Path(out_path)
    log_path = Path(str(out_dir) + repair.LOG_SUFFIX)
    if claude_run.inside_clone(out_path):
        print("author: REFUSED --out is inside the repository clone", file=out)
        return 2
    if os.path.lexists(out_dir) or os.path.lexists(log_path):
        print("author: REFUSED " + ("--out exists" if os.path.lexists(out_dir) else "the repair log path exists"),
              file=out)
        return 2
    if not out_dir.parent.is_dir():
        print("author: REFUSED --out needs an existing parent folder", file=out)
        return 2
    if environ.get("CPR_EVAL_LIVE") != "1":
        print("live run refused: set CPR_EVAL_LIVE=1", file=out)
        return 2
    if environ.get("CLAUDE_CONFIG_DIR"):
        print("unset CLAUDE_CONFIG_DIR", file=out)
        return 3
    runs, why = read_list(claude_run.eval_root(environ) / RUNS_NAME)
    label, ctx = (why, None) if runs is None else repair.inputs(source, fixtures_root, runs)
    if label:
        print(f"author: REFUSED {label}", file=out)
        return 2
    if ctx["missing"]:
        print(f"author: {ctx['missing']} probes with a missing label were not selected", file=out)
    return _with_stops(lambda cfg: _run_repair(cfg, ctx, fixtures_root, guide_path, out_dir, log_path, environ, out,
                                               runner), environ, out)


def _parse_repair(blocks) -> tuple:
    """({id: (spec, body)}, labels) for the reply's blocks; a bad header, bad JSON or a repeated id is a label."""
    found, labels = {}, []
    for number, (head, body) in enumerate(blocks, 1):
        if not ID_LINE.match(head):
            labels.append(f"block-{number}: header_invalid")
            continue
        try:
            spec = json.loads(body)
        except ValueError:
            labels.append(f"{head}: json_invalid")
            continue
        if head in found:
            labels.append(f"{head}: duplicate")
            continue
        found[head] = (spec, body)
    return found, labels


def _publish(fixtures_root, out_dir, log_path, bodies, entries) -> None:
    """Build the store in a temporary sibling folder and the log in a temporary sibling file, then rename both into
    place. Nothing is left behind on any failure."""
    tmp = Path(tempfile.mkdtemp(dir=str(out_dir.parent), prefix=".repair-"))
    tmp_log = tmp.with_name(tmp.name + ".log")
    placed = False
    try:
        repair.copy_store(fixtures_root, tmp)
        for fid, body in bodies.items():
            (tmp / fid / "spec.json").write_bytes(body.encode("utf-8"))
        repair.write_log(tmp_log, entries)
        os.rename(tmp, out_dir)
        placed = True
        os.rename(tmp_log, log_path)
        placed = False
    finally:
        if placed:
            shutil.rmtree(out_dir, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)
        if tmp_log.exists():
            tmp_log.unlink()


def _run_repair(cfg, ctx, fixtures_root, guide_path, out_dir, log_path, environ, out, runner) -> int:
    try:
        raw = Path(guide_path).read_bytes()
        guide = raw.decode("utf-8")
    except (OSError, ValueError):
        print("author: STOP an input file is unreadable", file=out)
        return 1
    prompt = repair.build_prompt(guide, ctx["spec_texts"], ctx["evidence"])
    root = claude_run.eval_root(environ)
    counted = []
    try:
        number, rec, proof = _launch(cfg, prompt, environ, runner, counted.append)
    except BaseException:
        if counted:  # the call was counted but did not finish: it still uses the one repair round
            unknown = {"run": counted[0], "mode": "repair", "started_utc": None, "claude_code_version": None,
                       "model": None, "isolation": "UNKNOWN", "guide_sha256": _sha(raw),
                       "prompt_sha256": _sha(prompt.encode("utf-8")), "extra_sha256": None, "replaces": [],
                       "ids_written": [], "cost_usd": None, "repair_ids": list(ctx["ids"])}
            append_row(root / RUNS_NAME, unknown)
        raise
    row = _row(number, rec, proof, "repair", _sha(raw), _sha(prompt.encode("utf-8")), None, [])
    row["repair_ids"] = list(ctx["ids"])
    try:
        if not proof["ok"]:
            judge.bump(environ, "failed", "author")
            print(f"author: STOP isolation proof failed: {', '.join(proof['failures'])}", file=out)
            return 1
        judge.bump(environ, "successful", "author")
        text = (isolation.result_event(rec["events"]) or {}).get("result")
        if not isinstance(text, str):
            print("author: STOP the reply holds no text", file=out)
            return 1
        found, labels = _parse_repair(split_reply(text))
        labels += repair.check_repaired(ctx["old_specs"], {fid: spec for fid, (spec, _b) in found.items()})
        if labels:
            for label in labels:
                print(label, file=out)
            print("author: STOP the reply is refused; nothing was written (the repair round is used)", file=out)
            return 1
        entries = repair.log_entries(ctx["old_specs"], {fid: spec for fid, (spec, _b) in found.items()},
                                     ctx["evidence"])
        _publish(fixtures_root, out_dir, log_path, {fid: body for fid, (_s, body) in found.items()}, entries)
        row["ids_written"] = sorted(found)
        print(f"author: repaired {len(found)} fixtures, "
              f"{sum(repair.changed_probe_count(e['diff']) for e in entries)} probes", file=out)
        print(f"author: store {out_dir}", file=out)
        print(f"author: repair log {log_path}", file=out)
        return 0
    finally:
        append_row(root / RUNS_NAME, row)
