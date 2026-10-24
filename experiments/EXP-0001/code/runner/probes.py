"""Probe sessions: one startup session per arm, fixture and seed, asked every probe at once.

The session runs in a fresh git sandbox that holds only the arm's CLAUDE.md, with no tools and no plugin. The
prompt lists the questions and nothing else: it names no item, section or category. Answers come back as JSON in
the reply text and are parsed here (the runner exposes no structured-output switch), the same way the judge's
replies are.
"""

from __future__ import annotations

import json
import random
import sys
import uuid
from pathlib import Path

import arms
import claude_run
import fixtures
import isolation
import judge
import seal

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
UNKNOWN = "UNKNOWN"
REVIEW_NAME = "isolation-review.json"


def probe_order(spec: dict, seed: int, rng_seed) -> list:
    """The fixture's probes in a shuffled order that depends on (rng_seed, fixture, seed) and not on the arm.

    Every arm of one fixture and seed sees the same order, so arms differ by their memory file alone.
    """
    probes = list(spec["probes"])
    random.Random(f"{rng_seed}:{spec['id']}:{seed}").shuffle(probes)
    return probes


def probe_prompt(spec: dict, seed: int, rng_seed) -> str:
    """The prompt text: question IDs and questions only, then the output schema."""
    questions = "\n".join(f"{p['id']}: {p['question']}" for p in probe_order(spec, seed, rng_seed))
    template = (PROMPTS / "probe-answer.md").read_text(encoding="utf-8")
    schema = (PROMPTS / "probe-answer.schema.json").read_text(encoding="utf-8")
    return judge.fill(template, questions=questions) + "\nSchema:\n" + schema


def parse_answers(text, expected_ids) -> tuple:
    """Return ({probe id: answer text}, problems). An id with no usable answer is absent from the dict.

    The reply may be fenced; anything that is not one JSON object matching the schema yields no answers. An id that
    appears more than once excludes every one of its answers (as the judge does for a duplicated label) and is
    reported as `duplicate:<id>`.
    """
    body = judge.extract_json_text(text)
    try:
        value = json.loads(body) if isinstance(body, str) else None
    except ValueError:
        return {}, ["output_not_json"]
    schema = json.loads((PROMPTS / "probe-answer.schema.json").read_text(encoding="utf-8"))
    problems = judge.validate(value, schema) if value is not None else ["output_missing"]
    if problems:
        return {}, problems
    expected = set(expected_ids)
    seen = {}
    for entry in value["answers"]:
        seen[entry["id"]] = seen.get(entry["id"], 0) + 1
    duplicated = {pid for pid in expected if seen.get(pid, 0) > 1}
    answers, extra = {}, 0
    for entry in value["answers"]:
        if entry["id"] in expected and entry["id"] not in duplicated and entry["answer"].strip():
            answers[entry["id"]] = entry["answer"].strip()
        elif entry["id"] not in duplicated:
            extra += 1
    out = [f"extra_or_duplicate_entries:{extra}"] if extra else []
    out += [f"duplicate:{pid}" for pid in sorted(duplicated)]
    out += [f"missing:{pid}" for pid in sorted(expected - set(answers) - duplicated)]
    return answers, out


input_tokens = arms.input_total


def run_probe_session(spec: dict, arm_text: str, cfg: dict, environ, runner, markers: list, seed: int, label: str) -> dict:
    """One probe session. Returns the result file's content plus "record" (the run record, for the raw log).

    ok is false when the run fails an isolation check; the caller re-runs the session once, then excludes the cell.
    A session that fails only outcome checks is ok but not re-run: its answers are left empty (the missing-data
    rule applies) and the failed checks are listed under "outcome_failures". An unparsable reply is still ok (the
    run was isolated) with answers {} and the problems listed.
    """
    sandbox = claude_run.make_sandbox("probe", environ)
    (sandbox / "CLAUDE.md").write_text(arm_text, encoding="utf-8")
    fixtures.git_commit_all(sandbox)
    prompt = probe_prompt(spec, seed, cfg.get("rng_seed", 0))
    record = runner(cfg, prompt, sandbox, label=label, phase="evaluation", kind="probe", probe=True,
                    session_id=str(uuid.uuid4()), environ=environ)
    proof = isolation.check(record, cfg, markers)
    record["proof"] = proof
    summary = arms.summary_of(record)
    if proof["isolation_failures"]:
        return dict(summary, ok=False, labels=list(proof["failures"]), answers={}, problems=[], record=record)
    if proof["outcome_failures"]:
        return dict(summary, ok=True, labels=[], answers={}, problems=["outcome:" + c for c in proof["outcome_failures"]],
                    outcome_failures=list(proof["outcome_failures"]), record=record)
    text = (isolation.result_event(record["events"]) or {}).get("result")
    answers, problems = parse_answers(text, [p["id"] for p in spec["probes"]])
    return dict(summary, ok=True, labels=[], answers=answers, problems=problems, record=record)


# --------------------------------------------------------------------------
# The batch: preserve runs and probe sessions for arms x fixtures x seeds
# --------------------------------------------------------------------------

class BatchStop(Exception):
    """The batch cannot go on; the message is a short label. Finished units stay on disk."""


class PinStop(BatchStop):
    """The agent harness version or the model changed: the experiment stops, it is not re-run (README P:120)."""


def select_fixtures(fixtures_root, wanted) -> list:
    """Fixture ids under fixtures_root: "all" (active fixtures only), or comma-separated full ids or unique id prefixes.

    A named fixture whose spec is excluded refuses; so does a spec that is not valid JSON.
    """
    root = Path(fixtures_root)
    available = sorted(p.parent.name for p in root.glob("*/spec.json"))

    def excluded(fid):
        try:
            return fixtures.is_excluded(fixtures.load_spec(root / fid / "spec.json"))
        except (OSError, ValueError):
            raise BatchStop(f"spec unreadable: {fid}")

    if not wanted or wanted == "all":
        return [f for f in available if not excluded(f)]
    chosen = []
    for part in [w.strip() for w in wanted.split(",") if w.strip()]:
        hits = [f for f in available if f == part or f.startswith(part + "-")]
        if len(hits) != 1:
            raise BatchStop(f"fixture selector matches {len(hits)}: {part}")
        if excluded(hits[0]):
            raise BatchStop(f"fixture excluded: {hits[0]}")
        chosen.append(hits[0])
    return chosen


PROOF_LISTS = ("isolation_reruns", "excluded_cells", "outcome_only")


def proof_counts(meta: dict) -> dict:
    """The counts the report shows, from the three lists in run.json: isolation re-runs, cells excluded after one,
    and runs that failed only outcome checks, by check."""
    by_check = {}
    for entry in meta.get("outcome_only", []):
        for check in entry.get("checks", []):
            by_check[check] = by_check.get(check, 0) + 1
    return {"isolation_reruns": len(meta.get("isolation_reruns", [])),
            "excluded_after_rerun": len(meta.get("excluded_cells", [])),
            "outcome_only_by_check": dict(sorted(by_check.items()))}


def write_run_json(out, cfg, split, fids, arm_names, seeds, date, judge_model=None, environ=None) -> dict:
    """Create <out>/run.json, or extend an existing one. A changed pin refuses (a batch never mixes versions).

    A new run mints its run id and creates its raw folder (an existing folder refuses); a resumed run keeps the id
    recorded in its own run.json, and so its raw folder, but only when that folder's owner file names this results
    folder: a copied results folder refuses (arms.RawError) and must be run into a new --out.
    """
    path = Path(out) / "run.json"
    meta = {"version": 1, "run_id": Path(out).name, "split": split, "date": date, "rng_seed": cfg.get("rng_seed", 0),
            "claude_code_version": cfg.get("claude_code_version"), "model": cfg.get("model"),
            "plugin_ref": cfg.get("plugin_ref"), "judge_model": (cfg.get("judge") or {}).get("model"),
            "fixtures": sorted(fids), "arms": list(arm_names), "seeds": int(seeds)}
    for key in PROOF_LISTS:
        meta[key] = []
    old = arms.read_json(path) if path.is_file() else None
    if old is not None:
        for key in ("rng_seed", "claude_code_version", "model", "plugin_ref", "split"):
            if old.get(key) != meta[key]:
                raise BatchStop(f"{key} changed since this run started")
        meta["fixtures"] = sorted(set(old["fixtures"]) | set(fids))
        meta["arms"] = list(old["arms"]) + [a for a in arm_names if a not in old["arms"]]
        meta["seeds"] = max(int(old["seeds"]), int(seeds))
        meta["date"], meta["run_id"] = old["date"], old["run_id"]
        for key in PROOF_LISTS:
            meta[key] = list(old.get(key, []))
    if old is not None and arms.RUN_ID_RE.match(str(old.get("raw_run_id"))):
        arms.check_raw_owner(old["raw_run_id"], out, environ)
        meta["raw_run_id"] = old["raw_run_id"]
    else:
        meta["raw_run_id"] = arms.new_run_id()
        arms.create_raw_folder(meta["raw_run_id"], environ, out)
    meta["proof_counts"] = proof_counts(meta)
    arms.write_json(path, meta)
    return meta


def log_proof(out, key: str, entry: dict) -> None:
    """Append entry to one of run.json's proof lists and refresh its counts."""
    path = Path(out) / "run.json"
    meta = arms.read_json(path)
    meta.setdefault(key, []).append(entry)
    meta["proof_counts"] = proof_counts(meta)
    arms.write_json(path, meta)


def log_denials(out, cell: dict, record: dict) -> None:
    """Append one entry per permission denial to <out>/isolation-review.json (cell and tool name; no tool input)."""
    path = Path(out) / REVIEW_NAME
    entries = arms.read_json(path) if path.is_file() else []
    result = isolation.result_event(record.get("events") or []) or {}
    for denial in result.get("permission_denials") or []:
        name = denial.get("tool_name") if isinstance(denial, dict) else None
        entries.append({"cell": dict(cell), "denial": name if isinstance(name, str) else "?"})
    arms.write_json(path, entries)


def run_cell(out, cell: dict, once, environ, before_retry=None) -> object:
    """Run one cell (a preserve run, or a probe session) under the proof rules of README "Exclusions".

    once() runs the unit and returns its result dict with "record". Every attempt's record is written to the raw
    folder first. Then, in this order:
      a version or model change stops the experiment (PinStop);
      an unfinished run that carries a rate-limit signal stops the batch as before (the pilot waits and resumes once);
      an isolation failure is re-run once, and a second one excludes the cell, which is listed and never run again;
      outcome checks alone never lead to a re-run: they are counted, and a denial goes to the isolation-review log.
    before_retry, when given, is called before each attempt after the first (the batch passes its budget check, so a
    re-run cannot start once the budget is used up; its BatchStop propagates).
    Returns the result, or None when the cell was excluded.
    """
    label = f"{cell['fixture']} seed {cell['seed']} {cell['arm']} {cell['kind']}"
    for attempt in (1, 2):
        if attempt > 1 and before_retry is not None:
            before_retry()
        res = once()
        record = res["record"]
        arms.write_raw(out, cell["kind"], cell["arm"], cell["fixture"], cell["seed"], record, environ)
        proof = record["proof"]
        pins = sorted(set(proof["failures"]) & isolation.PIN_CHANGE_CHECKS)
        if pins:
            raise PinStop(f"{label}: " + ",".join(pins))
        if proof["failures"] and set(proof["failures"]) <= judge.RATE_LIMIT_LABELS & isolation.OUTCOME_CHECKS \
                and judge.rate_limit_message(record) is not None:
            raise BatchStop(f"{label} failed: " + ",".join(proof["failures"]))
        if proof["isolation_failures"]:
            entry = dict(cell, checks=list(proof["isolation_failures"]))
            if attempt == 1:
                log_proof(out, "isolation_reruns", entry)
                continue
            log_proof(out, "excluded_cells", entry)
            return None
        if not res["ok"]:
            raise BatchStop(f"{label} failed: " + ",".join(res["labels"]))
        if proof["outcome_failures"]:
            log_proof(out, "outcome_only", dict(cell, checks=list(proof["outcome_failures"])))
            if "denials" in proof["outcome_failures"]:
                log_denials(out, {k: cell[k] for k in ("fixture", "seed", "arm")}, record)
        return res


def run_batch(fixtures_root, out, split, fids, arm_names, seeds, cfg, environ, runner=None, budget_usd=None,
              date="", arms_dir=None, stdout=None) -> int:
    """Run every missing unit; a unit whose result file exists is skipped, so a stopped batch resumes where it ended.

    Per fixture and seed: each preserve arm gets one preserve run (its CLAUDE.md saved), then every arm gets one
    probe session. A unit that fails the isolation proof stops the batch (exit 1); so does the budget: the
    pilot ledger may grow by at most budget_usd during this call.
    """
    stdout = stdout or sys.stdout
    runner = runner or claude_run.run_claude
    out = Path(out)
    kinds = {a: arms.arm_kind(a, arms_dir) for a in arm_names}
    specs = {}
    for fid in fids:
        spec = fixtures.load_spec(Path(fixtures_root) / fid / "spec.json")
        if fixtures.is_excluded(spec):
            raise BatchStop(f"fixture excluded: {fid}")
        if fixtures.validate_spec(spec, directory_name=fid):
            raise BatchStop(f"spec invalid: {fid}")
        if any(k == "preserve" for k in kinds.values()) and not (
                (Path(fixtures_root) / fid / "transcript.jsonl").is_file() and (Path(fixtures_root) / fid / "repo").is_dir()):
            raise BatchStop(f"fixture not generated: {fid}")
        specs[fid] = spec
    write_run_json(out, cfg, split, fids, arm_names, seeds, date, environ=environ)
    markers = isolation.host_markers(environ)
    plugins = {}
    start = claude_run.spend_total(environ, "evaluation")
    done = skipped = 0

    def check_budget():
        if budget_usd is not None and claude_run.spend_total(environ, "evaluation") - start >= budget_usd:
            raise BatchStop("budget reached")

    def excluded(kind, arm, fid, seed) -> bool:
        return any((e["kind"], e["arm"], e["fixture"], e["seed"]) == (kind, arm, fid, seed)
                   for e in arms.read_json(Path(out) / "run.json").get("excluded_cells", []))

    for fid in fids:
        spec, fdir = specs[fid], Path(fixtures_root) / fid
        for seed in range(1, int(seeds) + 1):
            texts = {}
            for arm in arm_names:
                text_path = arms.arm_text_path(out, arm, fid, seed)
                if kinds[arm] == "static":
                    texts[arm] = arms.static_text(arm, spec)
                    if not text_path.is_file():
                        claude_run.atomic_write(text_path, texts[arm])
                    continue
                if text_path.is_file() and arms.preserve_path(out, arm, fid, seed).is_file():
                    texts[arm] = text_path.read_text(encoding="utf-8")
                    skipped += 1
                    continue
                if excluded("preserve", arm, fid, seed):
                    skipped += 1
                    continue
                check_budget()
                plugin = arms.plugin_for(arm, cfg, environ, plugins, arms_dir)
                cell = {"kind": "preserve", "arm": arm, "fixture": fid, "seed": seed}
                res = run_cell(out, cell, lambda: arms.preserve_run(spec, fdir, arm, cfg, environ, runner, plugin, markers),
                               environ, check_budget)
                if res is None:
                    continue  # excluded: no CLAUDE.md for this arm, so no probe session either
                claude_run.atomic_write(text_path, res["text"])
                extra = {"outcome_failures": res["outcome_failures"]} if res["outcome_failures"] else {}
                arms.write_json(arms.preserve_path(out, arm, fid, seed),
                                dict(arms.summary_of(res["record"]), changed=res["changed"], **extra))
                texts[arm] = res["text"]
                done += 1
            for arm in arm_names:
                path = arms.probe_path(out, arm, fid, seed)
                if path.is_file():
                    skipped += 1
                    continue
                if arm not in texts or excluded("probe", arm, fid, seed):
                    skipped += 1
                    continue
                check_budget()
                cell = {"kind": "probe", "arm": arm, "fixture": fid, "seed": seed}
                res = run_cell(out, cell, lambda: run_probe_session(spec, texts[arm], cfg, environ, runner, markers,
                                                                    seed, f"probe-{arm}"), environ,
                               check_budget)
                if res is None:
                    continue
                res.pop("record")
                arms.write_json(path, res)
                done += 1
    cost = claude_run.spend_total(environ, "evaluation") - start
    print(f"run: {done} units run, {skipped} already present, pilot ledger +${cost:.2f}", file=stdout)
    return 0


def command_run(fixtures_root, out, split, wanted, arm_names, seeds, budget_usd, date, environ=None, stdout=None,
                runner=None, arms_dir=None, seal_dir=None, preseal=None, repo_root=None) -> int:
    """Live: needs CPR_EVAL_LIVE=1, a clean environment and a passing preflight. --out follows the CLI's rule.

    A baseline arm on held-out fixtures needs seal_dir (a seal whose manifest.txt is committed in repo_root) and
    preseal (the floor and oracle results the seal covers): the seal is verified before any launch, and the pre-seal
    labels and session files are carried into out read-only (P:276). Dev fixtures are unchanged.
    """
    stdout = stdout or sys.stdout
    refusal = claude_run.out_refusal(None if out is None else str(out))
    if refusal:
        print(f"run: REFUSED {refusal}", file=stdout)
        return 2
    try:
        chosen = select_fixtures(fixtures_root, wanted)
    except BatchStop:
        chosen = None  # the batch reports it
    if chosen is not None:
        code, text = seal.baseline_guard(split, chosen, arm_names, fixtures_root, seal_dir, preseal, repo_root)
        if code:
            print(f"run: {'REFUSED' if code == 2 else 'STOP'} {text}", file=stdout)
            return code
        carry = seal.needs_seal(split, chosen, arm_names)
    else:
        carry = False

    def work(cfg, env, o):
        try:
            fids = select_fixtures(fixtures_root, wanted)
            if not fids:
                raise BatchStop("no fixtures selected")
            if carry:
                seal.carry_preseal(seal_dir, preseal, out)
            return run_batch(fixtures_root, out, split, fids, arm_names, seeds, cfg, env, runner, budget_usd, date,
                             arms_dir, o)
        except (BatchStop, arms.ArmError, seal.SealStop) as exc:
            print(f"run: STOP {exc}", file=o)
            return 1
    return fixtures._guarded("run", work, environ, stdout)
