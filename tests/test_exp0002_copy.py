"""Offline checks that EXP-0002's copy of the method code differs from EXP-0001's in the five named places only.

Run with: python3 -m unittest discover tests

Every check that executes the copy's code runs in a child process with the copy's folders alone on the path. The
modules are never imported here: EXP-0001's tests import modules of the same bare names, and Python would hand
this file the cached EXP-0001 modules, so a check run in this process would prove nothing about the copy.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "experiments" / "EXP-0001" / "code"
NEW = ROOT / "experiments" / "EXP-0002" / "code"
TARGET = ROOT / "experiments" / "EXP-0002" / "target" / "whole-values"

A_LINE_36 = 'Leave out implementation detail, full file contents, timestamps, and anything the code or git history already records. Point to files instead of copying them. Keep exact values for paths, flags and identifiers.'
B_LINE_36 = 'Leave out implementation detail, full file contents, timestamps, and anything the code or git history already records. Point to files instead of copying them. Keep exact values for paths, flags, identifiers and numbers, keep every item when the session gave a list, and when the session changed a value, record only the latest one.'
A_SKILL_SHA256 = "d0c1cd8c5dfe939a8e2c61ec8eb2e04212e0388bafe872432343b03f0c38a9c6"
UPSTREAM_LICENSE_SHA256 = "f01636164a0e17b1557652587a0f179ac1d7fbf837fdbe0021c252af6e94507b"
PROVENANCE = ("SKILL.md in this folder is skills/preserve/SKILL.md of the CPR plugin at commit "
              "cfd1e77e07d2b3698591688e7e151d83d3ef51c3, with line 36 changed for EXP-0002.")

# Per file: exact (old, new, count) substitutions that turn the EXP-0001 text into the EXP-0002 text.
SUBSTITUTIONS = {
    "runner/arms.py": [
        ("(a SKILL.md under arms/<name>/)", "(a SKILL.md under the experiment's target/<name>/)", 1),
        ('ARMS_DIR = HERE / "arms"', 'ARMS_DIR = HERE.parent / "target"', 1),
        ("$TMPDIR/cpr-eval/raw/", "$TMPDIR/cpr-eval-exp0002/raw/", 2),
    ],
    "runner/claude_run.py": [
        ('/ "cpr-eval"\n', '/ "cpr-eval-exp0002"\n', 1),
        ('RECORDED_DIR = ("experiments", "EXP-0001", "recorded")', 'RECORDED_DIR = ("experiments", "EXP-0002", "recorded")', 1),
        ("finds experiments/EXP-0001/recorded/ among", "finds experiments/EXP-0002/recorded/ among", 1),
        ("and not under experiments/EXP-0001/recorded/", "and not under experiments/EXP-0002/recorded/", 1),
        ('"plugin_ref": cfg.get("plugin_ref") if plugin else None},\n',
         '"plugin_ref": cfg.get("plugin_ref") if plugin else None},\n'
         '        "tool_set": "none" if probe else "default",\n', 1),
    ],
    "runner/run.py": [
        ("experiments/EXP-0001/recorded/", "experiments/EXP-0002/recorded/", 4),
        ("$TMPDIR/cpr-eval/raw/", "$TMPDIR/cpr-eval-exp0002/raw/", 1),
    ],
}

# EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it. EXP-0002/code stays byte-frozen, so the change to
# EXP-0001's code is named here and nowhere else: this file is added to EXP-0001 and absent from EXP-0002 ...
EXP0001_ONLY_ADDITIONS = {
    "runner/author.py",  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
    "runner/repair.py",  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
}

# ... and these exact (post-change text, pre-change text, count) substitutions, applied to EXP-0001's text before
# SUBSTITUTIONS, turn it back into the text EXP-0002 copied. Counts are asserted on EXP-0001's text.
AUTHOR_LINE_README = (
    '''15. `CPR_EVAL_LIVE=1 python3 experiments/EXP-0001/code/runner/run.py author --prompt <file> --guide <file> --out <store> [--extra <file>] [--only-ids <id>,<id>] [--replaces <id>,<id>]` makes one call with no tools (`notools`: `--tools ""`, no output cap, the run timeout), charged to its own `authoring` bound of $3.00 (4 x $0.75), counted under the $55 ceiling and not under the generation cap, and needs the C1-5 and C6 gates (not C7). The prompt sent is the prompt file, a blank line, the guide and, with `--extra`, a blank line and that file; the sha256 of each input is recorded, not the extra text. The reply is split mechanically: a header line holding a held-out id, then a fenced block; a block that parses as JSON is written byte for byte as `<store>/<id>/spec.json` (an existing id folder is never overwritten); authoring repairs and reformats nothing (a repair call is made only with `--repair`, step 16). `--out` must be outside every clone, including under `experiments/EXP-0001/recorded/`. The command prints counts and labels (`<id>: <label>` from the spec validator), never spec text. At most 4 calls are made in all, the repair call of step 16 included (`author-calls.json`; a refused fifth call and a failed isolation proof both leave the count as it is), and a call that fails the isolation proof writes no spec (exit 1). `--replaces` names the ids a call replaces: at most 3 replacements in all (`author-replacements.json`, entries added once the proof passes), a replacement is never itself replaced, and a call writes at most as many specs as ids it names. Each call that reached a launch adds a row to `author-runs.json` (run number, mode, start time, version, model, proof outcome, input hashes, ids written, cost); the mode is `author`, `replace` or `repair`, and a row with no mode is read as `author`. The files are under `$TMPDIR/cpr-eval/`.
'''
)
REPAIR_LINE_README = (
    '''16. `CPR_EVAL_LIVE=1 python3 experiments/EXP-0001/code/runner/run.py author --repair --from <floor-and-oracle results> --fixtures-root <store> --guide <file> --out <new store>` makes the one repair call (P:252-256) through the same launch as step 15: the same pins, isolation proof and `notools`. `--repair` takes no `--prompt`, `--extra`, `--replaces` or `--only-ids` (each refuses, exit 2): the runner builds the prompt itself. The runner selects the evidence (P:253), never the owner: from `<results>/scores.json` it takes each active non-abstention probe whose floor and oracle labels are all present and that the floor answered correctly in any seed or the oracle answered correctly in fewer than a majority of its seeds (the negation of the filter in `score`), and a probe with a missing label is left out and counted. The blind agent (P:252) receives the guide, the `spec.json` of each fixture that holds a selected probe and, for each selected probe, its question, its gold answer and each seed's floor answer and oracle answer with their labels; nothing else. The runner checks the reply: only selected fixture ids, each selected fixture present once, the same probe ids (no probe added or removed) and a spec the validator accepts. Only a reply that passes writes anything: `--out` (which must not exist and must lie outside every clone) is a new full store holding every file under `--fixtures-root` byte for byte, with the agent's `spec.json` written over each selected fixture's, built in a temporary folder beside `--out` and renamed into place; the repair log (the runner's own diff, field by field, and the labels that selected each probe, with no answer text; each change line carries the sha256 of the full old and new value, and a reorder of the probes is logged as `order changed`) is written beside it as `<out>.repair-log.md`, never inside the store. A reply that fails the check writes nothing and prints labels only (exit 1). One repair call is allowed: it counts toward the 4-call counter and the `authoring` bound of step 15, and a failed isolation proof or a refused reply uses it. Every refusal before the call (exit 2, no call counted, nothing written) names its label: `--from` or `--fixtures-root` missing, a baseline arm in the results, `scores.json` missing or not from a completed floor and oracle scoring pass, a symlink or a git folder in the store, a selected fixture that is a replacement, a repair call already made, or no probe selected. `--from` and `--fixtures-root` are only read. The owner runs `gen-fixture <id> --fixtures-root <new store>` on each repaired id, then `lint <new store>`, then `run --split heldout --arms floor,oracle --fixtures <repaired ids> --seeds 3 --fixtures-root <new store> --out <the original folder> --supersede <repaired ids> --repair-log <new store>.repair-log.md` (P:256; the old sessions are moved to `superseded/<stamp>/`, not deleted, step 6), and passes the repair log to `seal --repair-log`.
'''
)
AUTHOR_PARSER_RUN = (
    '''    aut = sub.add_parser("author", help="one tool-less call that writes held-out specs, or with --repair repairs them; at most 4 calls, repair included, and 3 replacements; needs CPR_EVAL_LIVE=1")
    aut.add_argument("--prompt", help="the authoring prompt file (required without --repair)")
    aut.add_argument("--repair", action="store_true", help="one repair call over the floor and oracle results of --from: --out is a new full store, the repair log is written beside it as <out>.repair-log.md")
    aut.add_argument("--from", dest="source", help="with --repair: the floor and oracle results (run.json, scores.json, probes/); read only")
    aut.add_argument("--fixtures-root", help="with --repair: the fixture store to repair (copied byte for byte into --out)")
    aut.add_argument("--guide", required=True, help="the scrubbed guide file")
    aut.add_argument("--out", required=True, help="the spec store to write <id>/spec.json into (outside every clone)")
    aut.add_argument("--extra", help="a file whose text is appended to the prompt; only its sha256 is recorded")
    aut.add_argument("--only-ids", help="comma-separated ids; other blocks in the reply are not written")
    aut.add_argument("--replaces", help="comma-separated ids this call replaces; at most 3 replacements in all")
'''
)
AUTHOR_DISPATCH_RUN = (
    '''    if args.command == "author" and args.repair:
        return author.command_repair(args.source, args.fixtures_root, args.guide, args.out)
    if args.command == "author":
        split = lambda value: [i.strip() for i in value.split(",")] if value is not None else None  # noqa: E731
        return author.command_author(args.prompt, args.guide, args.out, args.extra, split(args.only_ids),
                                     split(args.replaces))
'''
)
AUTHOR_FLAG_REFUSAL_RUN = (
    '''def author_flag_refusal(args) -> str:
    """"" when the flags of `author` fit together, else the refusal label (before any gate, any read, any launch)."""
    if not args.repair:
        if args.source or args.fixtures_root:
            return "--from and --fixtures-root belong to --repair"
        return "" if args.prompt else "--prompt is required"
    if not (args.source and args.fixtures_root and args.guide and args.out):
        return "--repair needs --from, --fixtures-root, --guide and --out"
    for flag, value in (("--prompt", args.prompt), ("--extra", args.extra), ("--replaces", args.replaces),
                        ("--only-ids", args.only_ids)):
        if value is not None:
            return f"--repair cannot be used with {flag}"
    return ""


'''
)
AUTHOR_FLAG_GUARD_RUN = (
    '''    if args.command == "author":
        refusal = author_flag_refusal(args)
        if refusal:
            print(f"author: REFUSED {refusal}")
            return 2
'''
)
RECORD_JUDGE_CHECK_SEAL = (
    '''def record_judge_check(entry: dict, environ=None) -> None:
    """Add the JC entry of `judge-check`. An existing entry is never replaced: a judge-check is not re-run."""
    gates, reason = read_gates(environ, missing_ok=True)
    if gates is None:
        raise SealStop(reason)
    if JUDGE_KEY in gates:
        raise SealStop(f"{JUDGE_KEY} already has an entry")
    gates[JUDGE_KEY] = entry
    claude_run.atomic_write(gates_path(environ), _dump(gates))


'''
)
ISOLATION_CHECKS_BLOCK = (
    '''# Every isolation check label `check` can give (the pin-change labels included); a run's isolation.json names them with
# OUTCOME_CHECKS. A test reads `_check` and fails when this set and the source disagree.
ISOLATION_CHECKS = frozenset({
    "init_missing", "init_plugins_missing", "init_skills_missing", "init_agents_missing", "init_mcp_servers_missing",
    "isolation_invalid", "memory_paths_invalid", "version_unpinned", "version_mismatch", "model_unpinned",
    "model_mismatch", "no_markers", "kind_invalid", "sandbox_missing", "plugin_entry_invalid", "plugin_cpr_count",
    "plugin_path", "plugin_source", "plugin_unexpected", "plugin_not_builtin", "mcp_servers", "allowlist_unset",
    "skills_entry_invalid", "agents_entry_invalid", "skills_outside_allowlist", "agents_outside_allowlist",
    "skill_cpr_preserve_missing", "memory_path_outside", "memory_dir_symlink", "host_hook", "baseline_unproven",
    "hook_unpaired", "sessionstart_count", "transcript_missing", "instructions_attachment_missing",
    "transcript_corrupt", "transcript_session_mismatch", "instructions_not_project", "user_or_local_files",
    "instructions_outside_sandbox", "instructions_mismatch", "markers_found", "probe_tools"})
'''
)
PROBES_LOG_PROOF_NEW = (
    '''REPO_ROOT = claude_run.ROOT  # the folder that holds experiments/, from this code's own location, never from --out


def write_isolation(out) -> None:
    """Write <repository>/<RECORDED_DIR>/runs/<run id>/isolation.json from <out>/run.json: counts and check labels only.

    The run id is the name of --out. The raw results stay outside the repository (README P:319); only this file is
    written into it, at the path the preregistration names (P:311, P:349). "isolated" is judged over the included cells
    (P:119-126 allow excluded ones): it is false only while a failed-isolation cell is neither excluded nor finished.
    "excluded" counts the excluded cells and lists the isolation check labels that excluded them, nothing else.
    "checks" names every check the per-run proof applies (isolation and outcome). The repository's E7 check reads this
    file for an experiment's recorded runs.
    """
    meta = arms.read_json(Path(out) / "run.json")
    run_id = meta["run_id"]
    if run_id in ("", ".", ".."):
        raise BatchStop("run id is not a folder name")
    labels = isolation.ISOLATION_CHECKS | isolation.OUTCOME_CHECKS
    excluded = meta.get("excluded_cells", [])
    cell_key = lambda e: (e["kind"], e["arm"], e["fixture"], e["seed"])
    pending = [e for e in meta.get("isolation_reruns", []) if cell_key(e) not in {cell_key(x) for x in excluded}
               and not (arms.preserve_path if e["kind"] == "preserve" else arms.probe_path)(
                   out, e["arm"], e["fixture"], e["seed"]).is_file()]
    proof = {"checks": sorted(labels), "excluded": {"count": len(excluded), "checks": sorted(
                 {c for e in excluded for c in e.get("checks", []) if c in labels})},
             "isolated": not pending,
             "proof_counts": meta["proof_counts"], "run_id": run_id}
    arms.write_json(Path(REPO_ROOT).joinpath(*claude_run.RECORDED_DIR, "runs", run_id, "isolation.json"), proof)


def log_proof(out, key: str, entry: dict) -> None:
    """Append entry to one of run.json's proof lists and refresh its counts and the run's isolation.json."""
    path = Path(out) / "run.json"
    meta = arms.read_json(path)
    meta.setdefault(key, []).append(entry)
    meta["proof_counts"] = proof_counts(meta)
    arms.write_json(path, meta)
    write_isolation(out)
'''
)
PROBES_LOG_PROOF_OLD = (
    '''def log_proof(out, key: str, entry: dict) -> None:
    """Append entry to one of run.json's proof lists and refresh its counts."""
    path = Path(out) / "run.json"
    meta = arms.read_json(path)
    meta.setdefault(key, []).append(entry)
    meta["proof_counts"] = proof_counts(meta)
    arms.write_json(path, meta)
'''
)
SCORE_PASSES_HELPERS = (
    '''PASSES_NAME = "score-passes.json"
MAX_PASSES = 2  # the scoring command runs exactly twice per mode; there is no third pass (P:130-131)
PASS_MODES = ("normal", "final")
SUPERSEDED_NAME = "superseded.json"
EARLIER_OCCASION = "scores.json is from an earlier scoring occasion; run score again"


def current_occasion(out) -> int:
    """The scoring occasion of a results folder: 1, or the integer scoring_occasion (>= 2) that superseded.json holds.

    A repair re-check (`run --supersede`) opens a new occasion, and "runs exactly twice" (P:130, clarified at P:458)
    applies to each occasion. A superseded.json that is not JSON, or whose scoring_occasion is not an integer >= 2,
    stops scoring (ScoreAbort).
    """
    path = Path(out) / SUPERSEDED_NAME
    if not path.exists():
        return 1
    try:
        data = arms.read_json(path)
    except (OSError, ValueError):
        raise ScoreAbort(f"{SUPERSEDED_NAME} unreadable")
    if not isinstance(data, dict):
        raise ScoreAbort(f"{SUPERSEDED_NAME} unreadable")
    if "scoring_occasion" not in data:
        return 1
    occasion = data["scoring_occasion"]
    if type(occasion) is not int or occasion < 2:
        raise ScoreAbort(f"{SUPERSEDED_NAME} unreadable")
    return occasion


def passes_path(out) -> Path:
    """The pass counter of the folder's current occasion: score-passes.json for occasion 1, else
    score-passes-occasion-<n>.json (same schema)."""
    occasion = current_occasion(out)
    return Path(out) / (PASSES_NAME if occasion == 1 else f"score-passes-occasion-{occasion}.json")


def _passes_read(out) -> dict:
    """{mode: {"started": n, "aborted": n}} from the current occasion's pass counter (score-passes.json), plus
    "terminal": true in a mode whose pass failed a judge sanity control; no file is no pass yet. Anything else that
    is not that shape stops scoring before any call (ScoreAbort)."""
    path = passes_path(out)
    fresh = {m: {"started": 0, "aborted": 0} for m in PASS_MODES}
    if not path.exists():
        return fresh
    try:
        data = arms.read_json(path)
    except (OSError, ValueError):
        raise ScoreAbort(f"{path.name} unreadable")
    if not (isinstance(data, dict) and set(data) == set(PASS_MODES) and all(
            isinstance(v, dict) and set(v) in ({"started", "aborted"}, {"started", "aborted", "terminal"})
            and all(type(v[k]) is int and v[k] >= 0 for k in ("started", "aborted")) and v["aborted"] <= v["started"]
            and v.get("terminal", True) is True
            for v in data.values())):
        raise ScoreAbort(f"{path.name} unreadable")
    return data


def pass_refusal(out, mode: str) -> str:
    """"" when a pass in this mode may start, else the refusal line (exit 2). Two aborted passes end the scoring
    with the outcome not determined; two started passes are all there are."""
    counts = _passes_read(out)[mode]
    if counts.get("terminal"):
        return "score: STOP the judge sanity controls failed (outcome not determined)"
    if counts["aborted"] >= MAX_PASSES:
        return "score: STOP both scoring passes aborted (outcome not determined)"
    if counts["started"] >= MAX_PASSES:
        return "score: REFUSED the scoring command has run twice; there is no third pass"
    return ""


def pass_count(out, mode: str, key: str, terminal: bool = False) -> int:
    """Add one to <out>/score-passes.json under mode and key ("started" or "aborted"); atomic. terminal marks the mode
    as ended by a failed judge sanity control in the same write. Returns the new count."""
    data = _passes_read(out)
    data[mode][key] += 1
    if terminal:
        data[mode]["terminal"] = True
    arms.write_json(passes_path(out), data)
    return data[mode][key]


def pass_problem(out, final: bool, scores: dict) -> str:
    """"" when scores.json is the result of the last completed pass of the mode being reported, else the refusal label.
    command_score stamps scores["pass"] = {"mode", "id"} (id = the pass number) when a pass succeeds; a pass that aborts
    writes nothing, so the first pass's file stands when only the second aborts (id 1 of 2 started). A file from the other
    mode, or with an id beyond score-passes.json's starts, is refused.
    A final report always needs the stamp; another report needs it only where the command counted passes (the pilot
    writes scores.json without the command). The label names no score.
    """
    mode = "final" if final else "normal"
    try:
        occasion = current_occasion(out)
    except ScoreAbort as exc:
        return str(exc)
    if occasion >= 2 and not passes_path(out).exists():
        return EARLIER_OCCASION  # whatever the pilot rule says, a stale occasion-1 scores.json is refused
    if not final and not passes_path(out).exists():
        return ""
    try:
        counts = _passes_read(out)[mode]
    except ScoreAbort:
        return f"{passes_path(out).name} unreadable"
    if counts.get("terminal"):
        return "the judge sanity controls failed (outcome not determined)"
    started = counts["started"]
    stamp = scores.get("pass") if isinstance(scores, dict) else None
    if occasion >= 2 and not (isinstance(stamp, dict) and stamp.get("occasion") == occasion):
        return EARLIER_OCCASION
    if (isinstance(stamp, dict) and stamp.get("mode") == mode and type(stamp.get("id")) is int
            and 1 <= stamp["id"] <= started):
        return ""
    return f"scores.json is not from the last completed {mode} scoring pass; run score again"


'''
)
SCORE_PASS_GUARD = (
    '''            mode = "final" if seal_dir else "normal"
            occasion = current_occasion(out)
            refusal = pass_refusal(out, mode)
            if refusal:  # a refused call is not a pass; nothing has been asked of the judge
                print(refusal, file=o)
                return 2
            pass_id = pass_count(out, mode, "started")  # a pass starts here, before any judge call
            try:
                scores = score_run(out, specs, config, env, label_probe, label_items, model, max_calls, mode)
            except (ScoreAbort, judge.JudgeBudgetError) as exc:
                pass_count(out, mode, "aborted", terminal=isinstance(exc, SanityAbort))
                raise
'''
)
SCORE_SANITY_ABORT_CLASS = (
    '''

class SanityAbort(ScoreAbort):
    """A judge sanity control failed: the experiment ends with the outcome not determined (P:110 item 4, P:139)."""
'''
)
SCORE_SANITY_ABORT_EXCEPT = (
    '''        except SanityAbort as exc:  # terminal: the counter refuses every later pass in this mode
            print(f"score: ABORT {exc} (outcome not determined)", file=o)
            return 1
'''
)
SCORE_TOO_FEW_SCORABLE = (
    '''

MIN_SCORABLE_PROBES = 3  # P:133: a fixture with fewer scorable probes is dropped (the evaluation harness's own minimum)


def too_few_scorable(fixtures: dict) -> dict:
    """{fixture id: {"reason", "scorable": n}} for each fixture of a sealed record's `fixtures` block with fewer than
    MIN_SCORABLE_PROBES scorable probes (P:133); a fixture with no scorable list counts 0. Counts and a fixed reason only."""
    return {f: {"reason": "fewer than %d scorable probes" % MIN_SCORABLE_PROBES, "scorable": n}
            for f, rec in fixtures.items()
            for n in [len(rec.get("scorable") or []) if isinstance(rec, dict) else 0] if n < MIN_SCORABLE_PROBES}
'''
)
SEAL_FILES_TOGETHER = (
    '''

def seal_files_committed_together(seal_dir, repo_root=None) -> str:
    """"" when record.json and the repair log are tracked at HEAD with the bytes on disk and were added by the same
    commit as manifest.txt (P:277), else the message naming the file by label. Call after manifest_committed."""
    root = Path(repo_root or claude_run.ROOT)
    seal_dir = Path(seal_dir)

    def rel_of(path):
        return os.path.relpath(os.path.realpath(path), os.path.realpath(root)).replace(os.sep, "/")

    def git(*args):
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=30)

    logs = sorted(p for p in (seal_dir / REPAIR_DIR).glob("*") if p.is_file()) if (seal_dir / REPAIR_DIR).is_dir() else []
    files = [(RECORD_NAME, seal_dir / RECORD_NAME)] + ([("the repair log", logs[0])] if logs else [("the repair log", None)])
    try:
        added = git("log", "--diff-filter=A", "--format=%H", "-1", "HEAD", "--", rel_of(seal_dir / MANIFEST_NAME)).stdout
        for label, path in files:
            if path is None or not path.is_file():
                return f"{label} is missing: write the seal and commit it with {MANIFEST_NAME}"
            rel = rel_of(path)
            blob = git("cat-file", "blob", f"HEAD:{rel}")
            if blob.returncode != 0:
                return f"{label} is not committed at HEAD: commit it with {MANIFEST_NAME}"
            if blob.stdout != path.read_bytes():
                return f"{label} differs from the committed file: commit it with {MANIFEST_NAME}"
            if git("log", "--diff-filter=A", "--format=%H", "-1", "HEAD", "--", rel).stdout != added:
                return f"{label} was not committed in the same commit as {MANIFEST_NAME}: commit them together"
    except (OSError, subprocess.SubprocessError):
        return f"git cannot read this repository: {RECORD_NAME} and the repair log are not known to be committed"
    return ""
'''
)
PROBES_SUPERSEDE_BLOCK = (
    '''# --------------------------------------------------------------------------
# Supersede: the floor and oracle re-check of repaired held-out fixtures (README step 6)
# --------------------------------------------------------------------------

SUPERSEDED_NAME = "superseded.json"
SUPERSEDE_ARMS = ("floor", "oracle")
SUPERSEDE_FOLDERS = ("probes", "arms", "preserve")
SUPERSEDE_ID = re.compile(r"^heldout-\\d{3}-[a-z0-9-]+$")  # author.ID_LINE's pattern
SCORING_OCCASION = 2  # a re-check is its own scoring occasion with its own two passes (P:458 clarifies P:130)


def utc_stamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def supersede_refusal(out, ids, repair_log, split, arm_names, seal_dir, preseal, seeds=None) -> str:
    """"" or the first refusal label for the supersede flags, checked in the order the README lists (exit 2)."""
    if (ids is None) != (repair_log is None):
        return "--supersede and --repair-log go together"
    if ids is None:
        return ""
    if seal_dir or preseal or split != "heldout" or not arm_names or any(a not in SUPERSEDE_ARMS for a in arm_names):
        return "--supersede is for the floor and oracle re-check of held-out fixtures only"
    if not ids or not all(SUPERSEDE_ID.match(i) for i in ids):
        return "--supersede needs ids of the form heldout-NNN-name"
    out = Path(out)
    try:
        known = arms.read_json(out / "run.json")["fixtures"]
    except (OSError, ValueError, KeyError, TypeError):
        known = []
    for fid in ids:
        if fid not in known:
            return f"fixture {fid} is not in this results folder"
    for fid in ids:
        if not all((out / "probes" / arm / fid).is_dir() for arm in SUPERSEDE_ARMS):
            return f"fixture {fid} has no floor and oracle sessions to supersede"
    try:
        lines = Path(repair_log).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return "the repair log is missing"
    for fid in ids:
        if f"## {fid}" not in lines:
            return f"the repair log does not name fixture {fid}"
    if (out / SUPERSEDED_NAME).exists():
        return "this folder already holds a superseded record (one repair round, P:252)"
    if seeds is not None:  # the rerun covers exactly the seeds the folder recorded
        try:
            recorded = arms.read_json(out / "run.json")["seeds"]
        except (OSError, ValueError, KeyError, TypeError):
            recorded = None
        if type(recorded) is not int:
            return "run.json does not record the seeds"
        if int(seeds) != recorded:
            return f"--seeds {int(seeds)} must equal the {recorded} seeds recorded in run.json"
    return ""


def supersede_preflight(fixtures_root, out, ids, cfg, split, environ) -> None:
    """Raise BatchStop for what run_batch would refuse after the files had moved: an invalid new spec, a changed pin."""
    for fid in ids:
        try:
            spec = fixtures.load_spec(Path(fixtures_root) / fid / "spec.json")
        except (OSError, ValueError):
            raise BatchStop(f"spec unreadable: {fid}")
        if fixtures.validate_spec(spec, directory_name=fid):
            raise BatchStop(f"spec invalid: {fid}")
    old = arms.read_json(Path(out) / "run.json")
    for key, want in (("rng_seed", cfg.get("rng_seed", 0)), ("claude_code_version", cfg.get("claude_code_version")),
                      ("model", cfg.get("model")), ("plugin_ref", cfg.get("plugin_ref")), ("split", split)):
        if old.get(key) != want:
            raise BatchStop(f"{key} changed since this run started")
    if arms.RUN_ID_RE.match(str(old.get("raw_run_id"))):
        try:
            arms.check_raw_owner(old["raw_run_id"], out, environ)
        except arms.RawError as exc:
            raise BatchStop(str(exc))


def supersede_files(out, ids, repair_log, stamp=None) -> dict:
    """Move the floor and oracle sessions, arm texts and preserve files of ids to <out>/superseded/<stamp>/ and write
    <out>/superseded.json. Nothing is deleted; the folders keep their relative paths. All folders move or none: a
    failed move puts back what moved and raises. excluded_cells entries of these fixtures move into the record (they
    would block the rerun); isolation_reruns and outcome_only entries stay (events that happened)."""
    out, ids = Path(out), sorted(ids)
    stamp = stamp or utc_stamp()
    target = out / "superseded" / stamp
    if target.exists():
        raise BatchStop(f"superseded/{stamp} already exists")
    plan = [Path(kind) / arm / fid for fid in ids for arm in SUPERSEDE_ARMS for kind in SUPERSEDE_FOLDERS
            if (out / kind / arm / fid).is_dir()]
    try:  # every read the record needs, before the first move: a failure here leaves the folders as they were
        meta = arms.read_json(out / "run.json")
        moved_cells = [e for e in meta.get("excluded_cells", []) if e.get("fixture") in ids]
        log_sha = hashlib.sha256(Path(repair_log).read_bytes()).hexdigest()
        new_meta = dict(meta, excluded_cells=[e for e in meta.get("excluded_cells", []) if e not in moved_cells])
        new_meta["proof_counts"] = proof_counts(new_meta)
        record = {"version": 1, "stamp": stamp, "ids": ids, "scoring_occasion": SCORING_OCCASION,
                  "repair_log": {"name": Path(repair_log).name, "sha256": log_sha},
                  "moved": [{"from": r.as_posix(), "to": (Path("superseded") / stamp / r).as_posix()}
                            for r in sorted(plan, key=lambda p: p.as_posix())],
                  "excluded_cells": moved_cells}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise BatchStop(f"supersede failed, nothing moved: {type(exc).__name__}")
    done = []
    try:
        for rel in plan:
            (target / rel).parent.mkdir(parents=True, exist_ok=True)
            os.rename(out / rel, target / rel)
            done.append(rel)
        arms.write_json(out / SUPERSEDED_NAME, record)  # all or nothing until the record exists
    except OSError as exc:
        for rel in reversed(done):
            os.rename(target / rel, out / rel)
        shutil.rmtree(target, ignore_errors=True)
        (out / SUPERSEDED_NAME).unlink(missing_ok=True)
        try:
            (out / "superseded").rmdir()  # only when empty
        except OSError:
            pass
        raise BatchStop(f"supersede failed, nothing moved: {type(exc).__name__}")
    arms.write_json(out / "run.json", new_meta)
    write_isolation(out)
    return record


'''
)
JUDGE_CHECK_GUARD = (
    '''    import seal  # inside the function: seal imports score, which imports this module

    gates, reason = seal.read_gates(environ, missing_ok=True)
    if gates is None or seal.JUDGE_KEY in gates:  # an unreadable gate file cannot be recorded into: fail closed
        print("judge-check: REFUSED " + (reason + "; it is not overwritten" if gates is None else
                                         "JC already has an entry; a judge-check is never re-run"), file=out)
        return 2
    started = seal.utc_now()
'''
)
EXP0001_ONLY_SUBSTITUTIONS = {
    "README.md": [
        # the repair re-check: run --supersede, validate --fixtures-root, seal copies superseded.json, one scoring occasion
        # per re-check, one report line; EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        (" `validate --fixtures-root <store>` runs the set rules (unique ids, category coverage, and with `--heldout-complete` at least 5 long fixtures) on the store and refuses a store that does not exist or holds a symlink or a git folder; it does not run the lint.",
         "", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        (" [--supersede <ids> --repair-log <file>]", "", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        (''' `--supersede <ids> --repair-log <file>` (comma-separated held-out fixture ids, and the repair log of step 16) is the floor and oracle re-check of repaired fixtures: it refuses (exit 2, before any launch and before anything moves) unless both flags are given, `--split` is `heldout`, `--arms` holds only `floor` and `oracle`, neither `--seal` nor `--preseal` is given, every id has the form `heldout-NNN-name`, is listed in `<out>/run.json`, has floor and oracle sessions there and is selected by `--fixtures`, the repair log has a `## <id>` line for it, and `<out>/superseded.json` does not exist yet (the flag runs once per folder), and `--seeds` equals the seed count recorded in `<out>/run.json`. After the live gate and the preflight it moves, and never deletes, each id's floor and oracle `probes/`, `arms/` and `preserve/` folders to `<out>/superseded/<UTC stamp>/` under the same relative paths (all or none), moves that id's `excluded_cells` entries out of `run.json` into the record, writes `<out>/superseded.json` (`ids`, the repair log's name and sha256, the `moved` paths, those `excluded_cells`, the `stamp` and `scoring_occasion` 2, with no absolute path) and runs only the units that are now missing, so every other unit is left alone and the folder holds every fixture that `seal --from` replays; a stopped batch is resumed by a plain `run`. It does not touch `score-passes.json`, `scores.json` or `labels.json` (step 7 says how the new record is read).''',
         "", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        (''' A results folder that holds `superseded.json` (step 6) is in scoring occasion `scoring_occasion` (2): `score` counts that occasion's passes in `<out>/score-passes-occasion-2.json`, with the rules above (two starts and a third refused, two aborts not determined, a failed judge sanity control final for that occasion and mode), and leaves `score-passes.json` as it was; the stamp in `scores.json` then carries `occasion`, and `report`, `audit-export` and `author --repair` accept only the last completed pass of the last occasion (a `scores.json` stamped for an earlier occasion is refused, a report that is not final included). A `superseded.json` that is not JSON, or whose `scoring_occasion` is not an integer of at least 2, stops `score` before any judge call. The rule is P:458: "Runs exactly twice" (P:130) applies to each scoring occasion: each pre-seal round, and the final scoring.''',
         "", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        (" When the results folder holds `superseded.json`, the report prints one line saying how many superseded excluded cells it no longer counts, and pointing at that file (in the per-run proof counts, or after the header when `run.json` has none); a `superseded.json` that is unreadable or malformed stops the report.",
         "", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        (" When `--from` holds a `superseded.json` (step 6), `seal` copies it into the seal folder and records its sha256 in `seal.json` under `superseded`, and `verify` re-hashes that copy.",
         "", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("(required: a folder outside every clone, `experiments/EXP-0001/recorded/` included, for `run` and `pilot` alike; the other commands that take `--out` still accept a folder under `experiments/EXP-0001/recorded/`)",
         "(required: a folder outside the clone, or one under `experiments/EXP-0001/recorded/`)", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("Ten of the 19 commands", "Ten of the 18 commands", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("The other 9 (", "The other 8 (", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("`score`, `pilot` and `author`) launch", "`score` and `pilot`) launch", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("`pilot`, `author` and `canary --type generation`", "`pilot` and `canary --type generation`", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        (AUTHOR_LINE_README, "", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        (REPAIR_LINE_README, "", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        (" It records the result in `gates.json` as the entry `JC` (PASS when every case passes, FAIL on a miss, nothing when it stops or errors before the result), and it refuses to run again (exit 2, no launch) once an entry exists, so a judge-check is never re-run. `gen-fixture`, `run` and `score` refuse until `JC` is PASS (step 11).\n",
         "\n", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        (" Each batch also writes `experiments/EXP-0001/recorded/runs/<run id>/isolation.json` into the repository, where `<run id>` is the name of the `--out` folder (not the raw run id below): it holds the labels of the checks the per-run proof applies, whether any included cell has an unresolved isolation failure (`isolated`; an excluded cell does not make it false), the number of excluded cells and the isolation check labels that excluded them (`excluded`), the proof counts and that run id, and nothing from a raw record (`pilot` writes it the same way).",
         "", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        (" The command counts its passes per mode (`normal`, and `final` when `--seal` is given) in `<out>/score-passes.json`: a pass counts as started before its first judge call and as aborted when it stops without a result (a scoring abort or the judge call cap); a third start in one mode is refused (exit 2), and two aborted passes in a mode stop it with the outcome not determined (exit 2). A pass that succeeds stamps its mode and number into `scores.json`, and `report` refuses a `scores.json` whose stamp is missing, from the other mode, or numbered beyond the passes started; a final report always needs the stamp, and another report needs it only when `score-passes.json` exists (the pilot writes `scores.json` without the command). A failed judge sanity control ends the scoring with the outcome not determined: the mode is marked `terminal` in `score-passes.json`, every later pass in it is refused (exit 2), and `report` refuses a `scores.json` of that mode.",
         "", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("; either failing aborts the pass and is terminal, below)", "; either failing aborts the pass)", 1),  # row 28; same marker
        (" The same guard also needs `record.json` and the repair log committed in the same commit as `manifest.txt`, with the bytes on disk.",
         "", 1),  # row 24; same marker
        (" A fixture with fewer than 3 scorable probes (the minimum the pilot also uses) is listed in `excluded_fixtures` with that reason and its count, and `report` does not count it as sealed.",
         "", 1),  # row 21c; same marker
        ("`gen-fixture`, `run` and `score` also need `JC` PASS (step 4), and `gen-fixture` needs C7 PASS as well; a FAIL or ERROR entry for either canary, or a FAIL for `JC`, refuses them all;",
         "`gen-fixture` also needs C7 PASS; a FAIL or ERROR entry for either canary refuses them all;", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        (" `audit-export` refuses a `scores.json` that fails the same pass-stamp check as `report`.",
         "", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("(`spend.json`, one total per phase; it also holds an `authoring` total, which is not a `phase_caps` key: its $3.00 bound is a constant in the code, held by the 4-call counter, which counts the repair call, at $0.75 a call, and it counts in the $55 ceiling)",
         "(`spend.json`, one total per phase)", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("gen-fixture <id> [--fixtures-root <store>]` scripts", "gen-fixture <id>` scripts", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        (" With `--fixtures-root` these commands read and write `<store>/<id>/` directly (the layout `author` writes) and refuse, before any other work, a store that does not exist or holds a symlink or a git folder; without it they use the in-repo `fixtures/<split>/<id>` folder. `install-verify` takes the same flag.",
         "", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
    ],
    "runner/claude_run.py": [
        (",\n               notools=False) -> list:", ") -> list:", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("if probe or notools:\n        # notools: no tools, but no output cap and the full run timeout (the author command's long reply).\n",
         "if probe:\n", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        (", notools=False) -> dict:", ") -> dict:", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        (", notools=notools)", ")", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ('PHASES = ("control", "judge", "generation", "evaluation", "authoring")\n',
         'PHASES = ("control", "judge", "generation", "evaluation")\n', 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("AUTHORING_CAP_USD = 3.00  # the authoring agent's own bound (4 calls x $0.75); a code constant, not a config key\n",
         "", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ('    """The cap for a phase\'s ledger, from config phase_caps. A missing phase or cap refuses.\n\n'
         '    The authoring ledger\'s cap is the constant AUTHORING_CAP_USD, not a config value.\n    """\n'
         '    if ledger_of(phase) == "authoring":\n        return AUTHORING_CAP_USD\n',
         '    """The cap for a phase\'s ledger, from config phase_caps. A missing phase or cap refuses."""\n', 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        (', "authoring": spend_total(environ, "authoring")}\n', "}\n", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ('new = {"control": 0.0, "generation": 0.0, "evaluation": 0.0, "authoring": 0.0}\n',
         'new = {"control": 0.0, "generation": 0.0, "evaluation": 0.0}\n', 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ('"evaluation": new["evaluation"],\n                                                        "authoring": new["authoring"]}}))\n',
         '"evaluation": new["evaluation"]}}))\n', 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("def path_refusal(value, flag, root=None, allow_recorded=True) -> str:",
         "def path_refusal(value, flag, root=None) -> str:", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("    if not allow_recorded:  # run and pilot: P:319 keeps raw records out of the repository, so no folder in any clone\n"
         '        return f"{flag} is inside the repository clone"\n', "", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ('def strict_out_refusal(value, root=None) -> str:\n'
         '    """"" when value is an acceptable --out for run and pilot (outside every clone, recorded/ included), else the refusal."""\n'
         '    return path_refusal(value, "--out", root, allow_recorded=False)\n\n\n',
         "", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
    ],
    "runner/run.py": [
        # the repair re-check: run --supersede --repair-log, validate --fixtures-root; EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ('    check.add_argument("--fixtures-root", help="a fixture store (<id>/spec.json) instead of the in-repo fixtures folder; the set rules run on it, the lint does not")\n',
         "", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ('    batch.add_argument("--supersede", help="comma-separated held-out fixture ids: move their floor and oracle sessions and arm texts aside (never deleted) into <out>/superseded/<stamp>/, write superseded.json and rerun only them; needs --repair-log")\n'
         '    batch.add_argument("--repair-log", help="the repair log (<store>.repair-log.md); every --supersede id must have its \'## <id>\' section in it")\n',
         "", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("        if store:\n            return fixtures.run_validate(store, heldout_complete=args.heldout_complete, flat=True)\n",
         "", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ('        supersede = None if args.supersede is None else [i.strip() for i in args.supersede.split(",") if i.strip()]\n', "", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("                                  args.seeds, args.budget_usd, date, seal_dir=args.seal, preseal=args.preseal,\n"
         "                                  supersede=supersede, repair_log=args.repair_log)\n",
         "                                  args.seeds, args.budget_usd, date, seal_dir=args.seal, preseal=args.preseal)\n", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("lint, author, seal,", "lint, seal,", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("import audit\nimport author\n", "import audit\n", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        (AUTHOR_PARSER_RUN, "", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ('"seal", "author") and (', '"seal") and (', 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ('"seal", "author")):', '"seal")):', 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        (AUTHOR_DISPATCH_RUN, "", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        (AUTHOR_FLAG_REFUSAL_RUN, "", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        (AUTHOR_FLAG_GUARD_RUN, "", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("(claude_run.strict_out_refusal if args.command in (\"run\", \"pilot\") else claude_run.out_refusal)(args.out)",
         "claude_run.out_refusal(args.out)", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ('"results directory (required; outside every clone); raw',
         '"results directory (required; outside the clone, or under experiments/EXP-0001/recorded/); raw', 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ('pil.add_argument("--out", help="results directory (required; outside every clone)")',
         'pil.add_argument("--out", help="results directory (required; outside the clone, or under experiments/EXP-0001/recorded/)")', 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("import probes\nimport repair\nimport seal\n", "import probes\nimport seal\n", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ('    for store_command in (gen, inst, verify):\n'
         '        store_command.add_argument("--fixtures-root", help="a fixture store (<id>/spec.json) instead of the in-repo fixtures folder")\n',
         "", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ('    store = None\n'
         '    if args.command in ("gen-fixture", "install-check", "install-verify", "validate") and args.fixtures_root is not None:\n'
         '        store = Path(args.fixtures_root)\n'
         '        if not store.is_dir():\n'
         '            print(f"{args.command}: REFUSED --fixtures-root does not exist")\n'
         '            return 2\n'
         '        if repair.store_has_link_or_git(store):\n'
         '            print(f"{args.command}: REFUSED the fixture store holds a symlink or a git folder")\n'
         '            return 2\n',
         "", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("        if store:\n            return fixtures.command_gen_fixture(args.fixture_id, root=store, flat=True)\n", "", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("        if store:\n            return fixtures.command_install_check(args.fixture_id, root=store, flat=True)\n", "", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("        if store:\n            return fixtures.install_verify(args.fixture_id, root=store, flat=True)\n", "", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
    ],
    "runner/fixtures.py": [
        # validate reads a flat store; EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("def spec_paths(root=None, flat: bool = False) -> list:\n"
         "    if flat:  # a store: <root>/<id>/spec.json, the split is the spec's own field\n"
         '        return sorted(Path(root).glob("*/spec.json"))\n'
         "    root = Path(root) if root else FIXTURES_DIR\n",
         "def spec_paths(root=None) -> list:\n    root = Path(root) if root else FIXTURES_DIR\n", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("def load_all(root=None, flat: bool = False) -> list:", "def load_all(root=None) -> list:", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("    for path in spec_paths(root, flat):\n", "    for path in spec_paths(root):\n", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("def validate_all(root=None, heldout_complete: bool = False, flat: bool = False) -> tuple:",
         "def validate_all(root=None, heldout_complete: bool = False) -> tuple:", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("    for path, spec in load_all(root, flat):\n", "    for path, spec in load_all(root):\n", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("def run_validate(root=None, heldout_complete: bool = False, out=None, flat: bool = False) -> int:",
         "def run_validate(root=None, heldout_complete: bool = False, out=None) -> int:", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("    problems, specs, excluded = validate_all(root, heldout_complete, flat)\n",
         "    problems, specs, excluded = validate_all(root, heldout_complete)\n", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ('def fixture_dir(fixture_id: str, root=None, flat: bool = False) -> Path:\n'
         '    """<root>/<split>/<id> (the in-repo layout); with flat, <root>/<id> (a store, the layout author writes)."""\n'
         '    if flat:\n        return Path(root) / fixture_id\n',
         "def fixture_dir(fixture_id: str, root=None) -> Path:\n", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("root=None, out=None, flat: bool = False) -> int:\n    \"\"\"Generate, normalize", "root=None, out=None) -> int:\n    \"\"\"Generate, normalize", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("    runner = runner or claude_run.run_claude\n    fdir = fixture_dir(fixture_id, root, flat)\n",
         "    runner = runner or claude_run.run_claude\n    fdir = fixture_dir(fixture_id, root)\n", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("root=None, out=None, flat: bool = False) -> int:\n    out = out or sys.stdout\n    fdir = fixture_dir(fixture_id, root, flat)\n",
         "root=None, out=None) -> int:\n    out = out or sys.stdout\n    fdir = fixture_dir(fixture_id, root)\n", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("def install_verify(fixture_id: str, environ=None, root=None, out=None, flat: bool = False) -> int:",
         "def install_verify(fixture_id: str, environ=None, root=None, out=None) -> int:", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("    out = out or sys.stdout\n    fdir = fixture_dir(fixture_id, root, flat)\n    try:\n        spec = load_spec(fdir / \"spec.json\")\n        rows",
         "    out = out or sys.stdout\n    fdir = fixture_dir(fixture_id, root)\n    try:\n        spec = load_spec(fdir / \"spec.json\")\n        rows", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("def _excluded_stop(label: str, fixture_id: str, out, root, flat: bool = False) -> bool:",
         "def _excluded_stop(label: str, fixture_id: str, out, root) -> bool:", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ('load_spec(fixture_dir(fixture_id, root, flat) / "spec.json")', 'load_spec(fixture_dir(fixture_id, root) / "spec.json")', 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("def command_gen_fixture(fixture_id: str, environ=None, out=None, runner=None, root=None, flat: bool = False) -> int:\n"
         "    if _excluded_stop(fixture_id, fixture_id, out, root, flat):\n"
         "        return 1\n"
         "    return _guarded(fixture_id, lambda cfg, env, o: generate_fixture(fixture_id, cfg, env, runner, root, o, flat),\n"
         "                    environ, out)\n",
         "def command_gen_fixture(fixture_id: str, environ=None, out=None, runner=None, root=None) -> int:\n"
         "    if _excluded_stop(fixture_id, fixture_id, out, root):\n"
         "        return 1\n"
         "    return _guarded(fixture_id, lambda cfg, env, o: generate_fixture(fixture_id, cfg, env, runner, root, o), environ, out)\n", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("def command_install_check(fixture_id: str, environ=None, out=None, runner=None, root=None, flat: bool = False) -> int:\n"
         "    if _excluded_stop(f\"install-check {fixture_id}\", fixture_id, out, root, flat):\n"
         "        return 1\n"
         "    return _guarded(f\"install-check {fixture_id}\",\n"
         "                    lambda cfg, env, o: run_install_check(fixture_id, cfg, env, runner, root, o, flat), environ, out)",
         "def command_install_check(fixture_id: str, environ=None, out=None, runner=None, root=None) -> int:\n"
         "    if _excluded_stop(f\"install-check {fixture_id}\", fixture_id, out, root):\n"
         "        return 1\n"
         "    return _guarded(f\"install-check {fixture_id}\",\n"
         "                    lambda cfg, env, o: run_install_check(fixture_id, cfg, env, runner, root, o), environ, out)", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
    ],
    "runner/audit.py": [
        ("        stale = score.pass_problem(results_dir, True, scores)  # the report's check: a stale scores.json is refused\n"
         "        if stale:\n"
         '            _emit(stdout, f"audit-export: STOP {stale}")\n'
         "            return 1\n", "", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
    ],
    "runner/pilot.py": [
        ("    refusal = claude_run.strict_out_refusal(None if out is None else str(out))\n",
         "    refusal = claude_run.out_refusal(None if out is None else str(out))\n", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        # row 21c: one definition of the minimum, in score.py; EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("DEFAULT_MIN_SCORABLE = score.MIN_SCORABLE_PROBES  # one definition, in score.py (P:133)\n",
         "DEFAULT_MIN_SCORABLE = 3\n", 1),
    ],
    "runner/seal.py": [
        # the seal carries the record of a repair re-check; EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ('SUPERSEDED_NAME = "superseded.json"\n', "", 1),
        ("        # EXP-0001 unblock: the record of a repair re-check travels with the seal\n"
         "        if (Path(source) / SUPERSEDED_NAME).is_file():\n"
         "            shutil.copyfile(Path(source) / SUPERSEDED_NAME, out / SUPERSEDED_NAME)\n"
         '            seal["superseded"] = _ref(out / SUPERSEDED_NAME, SUPERSEDED_NAME)\n',
         "", 1),
        ('    ref = seal.get("superseded")\n'
         "    if ref is not None:\n"
         "        try:\n"
         '            _check_file(seal_dir / ref["path"], ref["sha256"], f"seal/{ref[\'path\']}", found)\n'
         "        except (KeyError, TypeError):\n"
         '            return ["unreadable:seal.json"]\n',
         "", 1),
        ('"canary-generation",\n                  "author")', '"canary-generation")', 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        # row 10: the judge-check gate (the JC key of gates.json); EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ('JUDGE_KEY = "JC"  # the judge-check, recorded as one entry with no token; a PASS opens gen-fixture, run and score\n'
         'JUDGE_GATED = ("gen-fixture", "run", "score")\n', "", 1),
        ('    """A canary entry carries a token; the C1-5 control entry and the JC judge-check entry carry none and their outcome\n'
         '    is PASS or FAIL only."""\n',
         '    """A canary entry carries a token; the C1-5 control entry carries none and its outcome is PASS or FAIL only."""\n', 1),
        ("    if key in (CONTROL_KEY, JUDGE_KEY):\n", "    if key == CONTROL_KEY:\n", 1),
        ("    The keys are C6, C7, C1-5 (CONTROL_KEY) and JC (JUDGE_KEY): the first two carry a token, the others carry none.\n",
         "    The keys are C6, C7 and C1-5 (CONTROL_KEY): the first two carry a token, C1-5 carries none.\n", 1),
        ("{CONTROL_KEY, JUDGE_KEY}\n", "{CONTROL_KEY}\n", 1),
        (RECORD_JUDGE_CHECK_SEAL, "", 1),
        ("    GATED_COMMANDS need C1-5 PASS (checked first: `control` passed all five isolation controls) and then C6 PASS;\n"
         "    gen-fixture, run and score need a JC PASS (the judge-check) after that, and gen-fixture needs C7 PASS as well,\n"
         "    checked last. A FAIL or ERROR entry for C1-5, JC or either canary stops them all. `control`\n",
         "    GATED_COMMANDS need C1-5 PASS (checked first: `control` passed all five isolation controls) and then C6 PASS, and\n"
         "    gen-fixture needs C7 PASS as well. A FAIL or ERROR entry for C1-5 or either canary stops them all. `control`\n", 1),
        ('    if command in JUDGE_GATED and JUDGE_KEY not in gates:\n'
         '        return "the judge-check has not passed: run `judge-check` first"\n', "", 1),
        # row 24: record.json and the repair log are committed with the manifest; EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        (SEAL_FILES_TOGETHER, "", 1),
        # row 21c: a fixture with fewer than 3 scorable probes is listed as excluded; same marker
        ('        sealed_scores = record_of_scores(scores)\n'
         '        record = {"version": 1, "fixtures": sealed_scores,  # a fixture with fewer than 3 scorable probes is dropped (P:133)\n'
         '                  "excluded_fixtures": dict(sorted({**score.too_few_scorable(sealed_scores), **excluded_fixtures(fixtures_root)}.items())),\n'
         '                  "count_rule": count_text}\n',
         '        record = {"version": 1, "fixtures": record_of_scores(scores), "excluded_fixtures": excluded_fixtures(fixtures_root),\n'
         '                  "count_rule": count_text}\n', 1),
        ("    reason = manifest_committed(seal_dir, repo_root) or seal_files_committed_together(seal_dir, repo_root)\n",
         "    reason = manifest_committed(seal_dir, repo_root)\n", 1),
    ],
    "runner/isolation.py": [
        # row 51: the label list behind each run's isolation.json; EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        (ISOLATION_CHECKS_BLOCK, "", 1),
    ],
    "runner/probes.py": [
        # run --supersede: the floor and oracle re-check of repaired fixtures; EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("import datetime\nimport hashlib\nimport json\nimport os\nimport random\nimport re\nimport shutil\nimport sys\nimport uuid\n",
         "import json\nimport random\nimport sys\nimport uuid\n", 1),
        (PROBES_SUPERSEDE_BLOCK, "", 1),
        ("preseal=None, repo_root=None, supersede=None,\n                repair_log=None) -> int:",
         "preseal=None, repo_root=None) -> int:", 1),
        ("    labels and session files are carried into out read-only (P:276). Dev fixtures are unchanged.\n\n"
         "    supersede (a list of ids) with repair_log (the CLI's --supersede and --repair-log) moves those held-out fixtures'\n"
         "    floor and oracle sessions aside after the live gate and reruns only them (see supersede_files); its refusals exit 2\n"
         "    before any move.\n",
         "    labels and session files are carried into out read-only (P:276). Dev fixtures are unchanged.\n", 1),
        ("    refusal = supersede_refusal(out, supersede, repair_log, split, arm_names, seal_dir, preseal, seeds)\n"
         "    try:\n"
         "        chosen = select_fixtures(fixtures_root, wanted)\n"
         "    except BatchStop:\n"
         "        chosen = None  # the batch reports it\n"
         "    if not refusal and supersede is not None and chosen is not None:\n"
         '        refusal = next((f"fixture {i} is not selected by --fixtures" for i in supersede if i not in chosen), "")\n'
         "    if refusal:\n"
         '        print(f"run: REFUSED {refusal}", file=stdout)\n'
         "        return 2\n"
         "    if chosen is not None:\n",
         "    try:\n"
         "        chosen = select_fixtures(fixtures_root, wanted)\n"
         "    except BatchStop:\n"
         "        chosen = None  # the batch reports it\n"
         "    if chosen is not None:\n", 1),
        ("            if supersede is not None:  # after the live gate and the preflight: a refused gate moves nothing\n"
         "                supersede_preflight(fixtures_root, out, supersede, cfg, split, env)\n"
         "                record = supersede_files(out, supersede, repair_log)\n"
         '                print(f"run: superseded {len(record[\'ids\'])} fixtures ({len(record[\'moved\'])} folders moved) into "\n'
         '                      f"superseded/{record[\'stamp\']}/", file=o)\n',
         "", 1),
        # row 51: each run's isolation.json; EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        (PROBES_LOG_PROOF_NEW, PROBES_LOG_PROOF_OLD, 1),
        ("    write_isolation(out)  # the batch ended without a stop: its proof counts are final\n", "", 1),
        ("    refusal = claude_run.strict_out_refusal(None if out is None else str(out))\n",
         "    refusal = claude_run.out_refusal(None if out is None else str(out))\n", 1),  # EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
    ],
    "score.py": [
        # row 31: the scoring command runs twice per mode; EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        (SCORE_PASSES_HELPERS, "", 1),
        (SCORE_PASS_GUARD,
         '            scores = score_run(out, specs, config, env, label_probe, label_items, model, max_calls,\n'
         '                               "final" if seal_dir else "normal")\n', 1),
        # a stale scores.json is never the result; EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ('        scores["pass"] = {"id": pass_id, "mode": mode}  # report refuses a scores.json whose stamp is not the last start\n'
         '        if occasion >= 2:\n'
         '            scores["pass"]["occasion"] = occasion\n',
         "", 1),
        # row 21c: the one definition of the minimum; EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        (SCORE_TOO_FEW_SCORABLE, "", 1),
        # row 28: a failed judge sanity control is terminal; EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        (SCORE_SANITY_ABORT_CLASS, "", 1),
        ('raise SanityAbort("judge sanity controls failed: oracle', 'raise ScoreAbort("judge sanity controls failed: oracle', 1),
        (SCORE_SANITY_ABORT_EXCEPT, "", 1),
    ],
    "report.py": [
        # one line for the superseded excluded cells; EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("The output is a pure function of run.json and scores.json (and superseded.json, when the folder holds one) and, for a held-out final report, also the seal's",
         "The output is a pure function of run.json and scores.json and, for a held-out final report, also the seal's", 1),
        ('''def load_superseded(results_dir):
    """The record <results folder>/superseded.json when it exists, else None; an unreadable or malformed one is a
    ReportError."""
    path = Path(results_dir) / "superseded.json"
    if not path.exists():
        return None
    try:
        record = arms.read_json(path)
    except (OSError, ValueError):
        raise ReportError("superseded.json unreadable")
    if not (isinstance(record, dict) and isinstance(record.get("ids"), list)
            and isinstance(record.get("excluded_cells"), list)):
        raise ReportError("superseded.json malformed")
    return record


def superseded_line(superseded: dict) -> str:
    return ("Superseded after the repair re-check: %d excluded cells of %d fixtures are not counted above; "
            "they are listed in superseded.json." % (len(superseded["excluded_cells"]), len(superseded["ids"])))


def build_markdown(run: dict, scores: dict, final=None, superseded=None) -> str:
    """The report text. final is None for every report but a held-out final one, which passes
    {"record", "repair_log_text", "audit"}; with None the report is exactly the one without the outcome sections.
    superseded is None unless the results folder holds superseded.json; then one line says how many excluded cells
    the report no longer counts."""
''',
         '''def build_markdown(run: dict, scores: dict, final=None) -> str:
    """The report text. final is None for every report but a held-out final one, which passes
    {"record", "repair_log_text", "audit"}; with None the report is exactly the one without the outcome sections."""
''', 1),
        ('        if superseded is not None:\n            out += [superseded_line(superseded), ""]\n', "", 1),
        ('    elif superseded is not None:\n        out += [superseded_line(superseded), ""]\n', "", 1),
        ("        superseded = load_superseded(results_dir)\n"
         "        md = build_markdown(run, scores, final) if superseded is None else build_markdown(run, scores, final, superseded)\n",
         "        md = build_markdown(run, scores, final)\n", 1),
        # a stale scores.json is never the result; EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("    import score  # inside the function, as seal is: the pass stamp check lives with the pass counter\n", "", 1),
        ("        stale = score.pass_problem(results_dir, seal.is_final(run), scores)\n"
         "        if stale:\n"
         "            raise ReportError(stale)\n", "", 1),
        # row 21c: a fixture with fewer than 3 scorable probes is dropped; EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        (" A fixture with fewer than\n    3 scorable probes is dropped too (P:133), whether or not the seal listed it.", "", 1),
        ("    import score  # inside the function, as in write_report: the one definition of the minimum lives with the scoring\n", "", 1),
        ('    excluded = {**score.too_few_scorable(record["fixtures"]), **record["excluded_fixtures"]}\n',
         '    excluded = record["excluded_fixtures"]\n', 1),
    ],
    "runner/judge.py": [
        # row 10: judge-check records JC and is not re-run; EXP-0001 unblock, post-EXP-0002; EXP-0002 ran without it
        ("import sys\nimport uuid\nfrom pathlib", "import sys\nfrom pathlib", 1),
        (JUDGE_CHECK_GUARD, "", 1),
        ("    missed = probe_ok != len(PROBE_CASES) or items_ok != 5\n"
         '    entry = {"outcome": "FAIL" if missed else "PASS", "started_utc": started, '
         '"run_id": f"judge-check-{uuid.uuid4().hex[:8]}"}\n'
         "    seal.record_judge_check(entry, environ)  # a stop or an error above recorded nothing\n"
         "    if missed:\n",
         "    if probe_ok != len(PROBE_CASES) or items_ok != 5:\n", 1),
    ],
}
CONFIG_CHANGES = {"spend_ceiling_usd": 45, "phase_caps": {"control": 7.0, "generation": 1.0, "evaluation": 36.0}}


def code_files(folder: Path) -> set:
    return {str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file() and "__pycache__" not in p.parts}


def child(script: str, *args: str) -> str:
    """Run script in an isolated child process and return its stdout."""
    proc = subprocess.run([sys.executable, "-I", "-c", script, *args], capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise AssertionError("child failed (%d): %s" % (proc.returncode, proc.stderr))
    return proc.stdout


PATHS = '''
import sys
code = sys.argv[1]
sys.path[:0] = [code, code + "/runner"]
'''


class CopyDiffersInFivePlacesOnly(unittest.TestCase):
    """The copy's file set and text equal EXP-0001's, apart from the pinned substitutions."""

    def test_same_file_set(self):
        self.assertEqual(code_files(OLD) - EXP0001_ONLY_ADDITIONS, code_files(NEW))
        self.assertLessEqual(EXP0001_ONLY_ADDITIONS, code_files(OLD))
        self.assertFalse(EXP0001_ONLY_ADDITIONS & code_files(NEW))

    def test_text_differs_by_the_pinned_substitutions_only(self):
        for rel in sorted(code_files(OLD) - {"config.json"} - EXP0001_ONLY_ADDITIONS):
            with self.subTest(file=rel):
                old = (OLD / rel).read_text(encoding="utf-8")
                expect = old
                for was, now, count in EXP0001_ONLY_SUBSTITUTIONS.get(rel, []):
                    self.assertEqual(old.count(was), count, "%s: %r occurs %d times in EXP-0001" % (rel, was, old.count(was)))
                    expect = expect.replace(was, now)
                old = expect
                for was, now, count in SUBSTITUTIONS.get(rel, []):
                    self.assertEqual(old.count(was), count, "%s: %r occurs %d times in EXP-0001" % (rel, was, old.count(was)))
                    expect = expect.replace(was, now)
                self.assertEqual((NEW / rel).read_text(encoding="utf-8"), expect)

    def test_every_pinned_file_exists(self):
        self.assertTrue(set(SUBSTITUTIONS) <= code_files(OLD))

    def test_config_differs_in_the_four_values_only(self):
        old = json.loads((OLD / "config.json").read_text(encoding="utf-8"))
        new = json.loads((NEW / "config.json").read_text(encoding="utf-8"))
        expect = dict(old, spend_ceiling_usd=45, phase_caps={"control": 7.0, "generation": 1.0, "evaluation": 36.0})
        self.assertEqual(new, expect)
        self.assertNotEqual(old["spend_ceiling_usd"], new["spend_ceiling_usd"])
        self.assertEqual(set(old["phase_caps"]), set(new["phase_caps"]))


class EvalRootsDiffer(unittest.TestCase):
    """The two copies keep their spend ledger and raw logs under different folders of the same TMPDIR."""

    def test_roots_differ_under_one_tmpdir(self):
        script = PATHS + 'import claude_run\nprint(claude_run.eval_root({"TMPDIR": sys.argv[2]}))\n'
        with tempfile.TemporaryDirectory() as t:
            old = child(script, str(OLD), t).strip()
            new = child(script, str(NEW), t).strip()
        self.assertNotEqual(old, new)
        self.assertTrue(old.endswith("cpr-eval"), old)
        self.assertTrue(new.endswith("cpr-eval-exp0002"), new)


FAKE = r'''#!/usr/bin/env python3
import json, os, re, sys
here = os.path.dirname(os.path.abspath(__file__))
open(os.path.join(here, "calls.jsonl"), "a").write(json.dumps({"argv": sys.argv[1:]}) + "\n")
a = sys.argv
sid = a[a.index("--session-id") + 1] if "--session-id" in a else a[a.index("--resume") + 1]
slug = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(os.getcwd()))
d = os.path.join(os.environ["HOME"], ".claude", "projects", slug)
os.makedirs(d, exist_ok=True)
open(os.path.join(d, sid + ".jsonl"), "w").write(json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n")
print(json.dumps({"type": "system", "subtype": "init", "claude_code_version": "9.9.9", "model": "claude-test-model", "session_id": sid}))
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.01, "duration_ms": 5, "usage": {}, "permission_denials": []}))
'''

RUN_BOTH = PATHS + '''
import json, os
from pathlib import Path
import claude_run
tmp, bindir = Path(sys.argv[2]), Path(sys.argv[3])
CFG = {
    "claude_code_version": "9.9.9", "model": "claude-test-model", "isolated_baseline_hooks": 0, "isolation": "sources",
    "max_budget_usd_per_run": 0.75, "probe_max_output_tokens": 2048, "probe_timeout_s": 180,
    "phase_caps": {"control": 3.0, "generation": 15.0, "evaluation": 30.0}, "spend_ceiling_usd": 55,
    "plugin_ref": "cfd1e77e07d2b3698591688e7e151d83d3ef51c3", "instructions_attachment_proven": True,
    "init_allowlist": {"skills": ["update-config"], "agents": ["general-purpose"]},
}
environ = {"PATH": str(bindir) + os.pathsep + os.environ["PATH"], "HOME": str(tmp / "home"), "TMPDIR": str(tmp), "CPR_EVAL_LIVE": "1"}
sandbox = claude_run.make_sandbox("t", environ)
export = tmp / "export"
export.mkdir()
first = claude_run.run_claude(dict(CFG), "hello", sandbox, label="t", phase="control", plugin=export, environ=environ)
prior = {"session_id": "abc", "sandbox": str(sandbox), "memory_dir_after": []}
second = claude_run.run_claude(dict(CFG), "p", sandbox, label="t", phase="control", probe=True, resume="abc", environ=environ, prior=prior)
print(json.dumps({"tool_sets": [first.get("tool_set", "MISSING"), second.get("tool_set", "MISSING")]}))
'''


class RunRecordsCarryToolSet(unittest.TestCase):
    """Each run record says which tool set the run had, and the tool flags themselves are unchanged."""

    def test_records_and_argv(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t).resolve()
            bindir = tmp / "bin"
            bindir.mkdir()
            (tmp / "home").mkdir()
            fake = bindir / "claude"
            fake.write_text(FAKE, encoding="utf-8")
            fake.chmod(0o755)
            out = child(RUN_BOTH, str(NEW), str(tmp), str(bindir))
            calls = [json.loads(ln)["argv"] for ln in (bindir / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(json.loads(out.strip().splitlines()[-1])["tool_sets"], ["default", "none"])
        self.assertEqual(len(calls), 2)
        self.assertNotIn("--tools", calls[0])
        self.assertIn("--tools", calls[1])
        self.assertEqual(calls[1][calls[1].index("--tools") + 1], "")


class VariantIsOneLine(unittest.TestCase):
    """The variant's skill file is the published file with line 36 replaced."""

    def lines(self):
        return (TARGET / "SKILL.md").read_text(encoding="utf-8").split("\n")

    def test_length_and_line_36(self):
        lines = self.lines()
        self.assertEqual(len(lines), 63)  # 62 lines and the final newline
        self.assertEqual(lines[-1], "")
        self.assertEqual(lines[35], B_LINE_36)
        self.assertNotEqual(B_LINE_36, A_LINE_36)

    def test_restoring_line_36_gives_the_published_file(self):
        lines = self.lines()
        lines[35] = A_LINE_36
        self.assertEqual(hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest(), A_SKILL_SHA256)


class LicenceNotice(unittest.TestCase):
    """The variant folder carries the MIT notice and says which file it covers."""

    def test_notice(self):
        text = (TARGET / "LICENSE").read_text(encoding="utf-8")
        lines = text.rstrip("\n").split("\n")
        self.assertTrue(text.startswith("MIT License"))
        self.assertTrue(any(ln.startswith("Copyright (c)") for ln in lines))
        self.assertEqual(lines[-1], PROVENANCE)

    def test_first_21_lines_are_the_upstream_notice(self):
        """The first 21 lines are the upstream MIT text, byte for byte."""
        text = (TARGET / "LICENSE").read_text(encoding="utf-8")
        head = "".join(text.splitlines(keepends=True)[:21])
        self.assertEqual(hashlib.sha256(head.encode("utf-8")).hexdigest(), UPSTREAM_LICENSE_SHA256)


class TargetSkillFilesCarryALicence(unittest.TestCase):
    """Every target skill file in the repository has the MIT notice beside it."""

    def test_every_target_skill_has_a_licence(self):
        """An empty glob fails (positive control); each SKILL.md has a LICENSE file."""
        skills = sorted((ROOT / "experiments").glob("*/target/*/SKILL.md"))
        self.assertTrue(skills, "no target skill files found")
        for skill in skills:
            self.assertTrue((skill.parent / "LICENSE").is_file(), f"no LICENSE beside {skill}")


class VariantsAreFoundUnderTarget(unittest.TestCase):
    """The copy reads skill variants from the experiment's target/ folder."""

    def test_variant_skill_path(self):
        script = PATHS + 'import arms\np = arms.variant_skill("whole-values")\nprint(p)\n'
        got = Path(child(script, str(NEW)).strip()).resolve()
        self.assertEqual(got, (TARGET / "SKILL.md").resolve())
        self.assertEqual(got.parts[-4:], ("EXP-0002", "target", "whole-values", "SKILL.md"))


if __name__ == "__main__":
    unittest.main()
