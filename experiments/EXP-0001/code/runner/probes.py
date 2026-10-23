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

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
UNKNOWN = "UNKNOWN"


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

    ok is false when the run fails the isolation proof; the caller stops the batch. An unparsable reply is
    still ok (the run was isolated) with answers {} and the problems listed.
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
    if not proof["ok"]:
        return dict(summary, ok=False, labels=list(proof["failures"]), answers={}, problems=[], record=record)
    text = (isolation.result_event(record["events"]) or {}).get("result")
    answers, problems = parse_answers(text, [p["id"] for p in spec["probes"]])
    return dict(summary, ok=True, labels=[], answers=answers, problems=problems, record=record)


# --------------------------------------------------------------------------
# The batch: preserve runs and probe sessions for arms x fixtures x seeds
# --------------------------------------------------------------------------

class BatchStop(Exception):
    """The batch cannot go on; the message is a short label. Finished units stay on disk."""


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


def write_run_json(out, cfg, split, fids, arm_names, seeds, date, judge_model=None) -> dict:
    """Create <out>/run.json, or extend an existing one. A changed pin refuses (a batch never mixes versions)."""
    path = Path(out) / "run.json"
    meta = {"version": 1, "run_id": Path(out).name, "split": split, "date": date, "rng_seed": cfg.get("rng_seed", 0),
            "claude_code_version": cfg.get("claude_code_version"), "model": cfg.get("model"),
            "plugin_ref": cfg.get("plugin_ref"), "judge_model": (cfg.get("judge") or {}).get("model"),
            "fixtures": sorted(fids), "arms": list(arm_names), "seeds": int(seeds)}
    if path.is_file():
        old = arms.read_json(path)
        for key in ("rng_seed", "claude_code_version", "model", "plugin_ref", "split"):
            if old.get(key) != meta[key]:
                raise BatchStop(f"{key} changed since this run started")
        meta["fixtures"] = sorted(set(old["fixtures"]) | set(fids))
        meta["arms"] = list(old["arms"]) + [a for a in arm_names if a not in old["arms"]]
        meta["seeds"] = max(int(old["seeds"]), int(seeds))
        meta["date"], meta["run_id"] = old["date"], old["run_id"]
    arms.write_json(path, meta)
    return meta


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
    write_run_json(out, cfg, split, fids, arm_names, seeds, date)
    markers = isolation.host_markers(environ)
    plugins = {}
    start = claude_run.spend_total(environ, "evaluation")
    done = skipped = 0

    def check_budget():
        if budget_usd is not None and claude_run.spend_total(environ, "evaluation") - start >= budget_usd:
            raise BatchStop("budget reached")

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
                check_budget()
                plugin = arms.plugin_for(arm, cfg, environ, plugins, arms_dir)
                res = arms.preserve_run(spec, fdir, arm, cfg, environ, runner, plugin, markers)
                arms.write_raw(out, "preserve", arm, fid, seed, res["record"], environ)
                if not res["ok"]:
                    raise BatchStop(f"{fid} seed {seed} {arm} preserve failed: " + ",".join(res["labels"]))
                claude_run.atomic_write(text_path, res["text"])
                arms.write_json(arms.preserve_path(out, arm, fid, seed),
                                dict(arms.summary_of(res["record"]), changed=res["changed"]))
                texts[arm] = res["text"]
                done += 1
            for arm in arm_names:
                path = arms.probe_path(out, arm, fid, seed)
                if path.is_file():
                    skipped += 1
                    continue
                check_budget()
                res = run_probe_session(spec, texts[arm], cfg, environ, runner, markers, seed, f"probe-{arm}")
                arms.write_raw(out, "probe", arm, fid, seed, res.pop("record"), environ)
                if not res["ok"]:
                    raise BatchStop(f"{fid} seed {seed} {arm} probe failed: " + ",".join(res["labels"]))
                arms.write_json(path, res)
                done += 1
    cost = claude_run.spend_total(environ, "evaluation") - start
    print(f"run: {done} units run, {skipped} already present, pilot ledger +${cost:.2f}", file=stdout)
    return 0


def command_run(fixtures_root, out, split, wanted, arm_names, seeds, budget_usd, date, environ=None, stdout=None,
                runner=None, arms_dir=None) -> int:
    """Live: needs CPR_EVAL_LIVE=1, a clean environment and a passing preflight."""
    stdout = stdout or sys.stdout

    def work(cfg, env, o):
        try:
            fids = select_fixtures(fixtures_root, wanted)
            if not fids:
                raise BatchStop("no fixtures selected")
            return run_batch(fixtures_root, out, split, fids, arm_names, seeds, cfg, env, runner, budget_usd, date,
                             arms_dir, o)
        except (BatchStop, arms.ArmError) as exc:
            print(f"run: STOP {exc}", file=o)
            return 1
    return fixtures._guarded("run", work, environ, stdout)
