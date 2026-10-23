"""The pilot: the dev fixtures through every arm, scored and reported, with the stop conditions wired in, then PILOT.md.

Sequence: probes.run_batch (preserve runs and probe sessions; resumable) -> score.score_run -> report.write_report
-> PILOT.md. Every stop writes what exists and marks the run INCOMPLETE with its reason; running the command again
resumes, because finished units and cached judge labels are skipped.

A throttled unit is told from any other proof failure by judge.rate_limit_message (see there for the signal): one
throttle waits and resumes the batch once, a second stops it. Any other proof failure stops at once.

PILOT.md is a pure function of the results directory (run.json, pilot-state.json, scores.json, the result files and
labels.json) plus the fixtures' gen.json files, so `pilot-md --from` rebuilds it byte for byte.
"""

from __future__ import annotations

import io
import json
import math
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import arms
import claude_run
import fixtures
import isolation
import judge
import probes
import report  # noqa: E402
import score  # noqa: E402

STATE_NAME = "pilot-state.json"
PILOT_MD = "PILOT.md"
PIN_KEYS = ("claude_code_version", "model")
DEFAULT_WAIT_S = 60
DEFAULT_MIN_SCORABLE = 3
FAILED_UNIT_RE = re.compile(r" (?:preserve|probe) failed: (.*)$")


class PinChanged(probes.PinStop):
    """A record's Claude Code version or model differs from the pin."""


class RateLimited(Exception):
    """The batch was throttled again after its one retry."""


class CostCap(Exception):
    """The --budget-usd allowance is used up (checked before each judge call)."""


class JudgeThrottled(Exception):
    """A judge call was throttled; the message is the CLI's own text."""


# --------------------------------------------------------------------------
# Fixture selection
# --------------------------------------------------------------------------

def expand_fixtures(fixtures_root, wanted) -> list:
    """Fixture directory names for "dev-001..dev-005", ids, prefixes or "all", comma-separated.

    A range runs from the first id to the last among the fixtures that exist, in sorted order; an id that matches
    no fixture, or more than one, refuses (probes.BatchStop).
    """
    root = Path(fixtures_root)
    available = sorted(p.parent.name for p in root.glob("*/spec.json"))
    chosen = []
    for part in [w.strip() for w in str(wanted or "all").split(",") if w.strip()]:
        if ".." in part:
            low, high = part.split("..", 1)
            first, last = probes.select_fixtures(root, low)[0], probes.select_fixtures(root, high)[0]
            i, j = available.index(first), available.index(last)
            if i > j:
                raise probes.BatchStop(f"fixture range runs backwards: {part}")
            active = set(probes.select_fixtures(root, "all"))
            hits = [h for h in available[i:j + 1] if h in active]
        else:
            hits = probes.select_fixtures(root, part)
        chosen += [h for h in hits if h not in chosen]
    if not chosen:
        raise probes.BatchStop("no fixtures selected")
    return chosen


# --------------------------------------------------------------------------
# Plan-usage observations (what the stream's rate_limit_event rows report)
# --------------------------------------------------------------------------

class PlanUsage:
    """Statuses and per-window utilization, as Claude Code reports them. Nothing here is inferred."""

    def __init__(self, data=None):
        data = data if isinstance(data, dict) else {}
        self.events = int(data.get("events", 0))
        self.statuses = dict(data.get("statuses", {}))
        self.windows = {k: dict(v) for k, v in data.get("windows", {}).items()}

    def observe(self, events) -> int:
        """Fold in one record's rate_limit_event rows; returns how many there were."""
        n = 0
        for event in events or []:
            if not (isinstance(event, dict) and event.get("type") == "rate_limit_event"):
                continue
            n += 1
            info = event.get("rate_limit_info") if isinstance(event.get("rate_limit_info"), dict) else {}
            status = info.get("status")
            if isinstance(status, str):
                self.statuses[status[:40]] = self.statuses.get(status[:40], 0) + 1
            windows = info.get("unifiedWindows") if isinstance(info.get("unifiedWindows"), dict) else {}
            for name in sorted(windows):
                util = windows[name].get("utilization") if isinstance(windows[name], dict) else None
                if isinstance(util, (int, float)) and not isinstance(util, bool) and math.isfinite(util):
                    w = self.windows.setdefault(str(name)[:40], {"n": 0})
                    w["first"] = w.get("first", util)
                    w["last"] = util
                    w["max"] = max(w.get("max", util), util)
                    w["n"] += 1
        self.events += n
        return n

    def to_dict(self) -> dict:
        return {"events": self.events, "statuses": self.statuses, "windows": self.windows}


# --------------------------------------------------------------------------
# The runner wrapper
# --------------------------------------------------------------------------

class Tracker:
    """Wraps the batch runner: pin check, plan-usage observation, and the last call's rate-limit message."""

    def __init__(self, base, plan_usage: PlanUsage):
        self.base = base
        self.plan = plan_usage
        self.last_limit = None
        self.calls = 0
        self.rate_limit_events = 0

    def runner(self, cfg, prompt, sandbox, **kwargs):
        self.last_limit = None
        record = self.base(cfg, prompt, sandbox, **kwargs)
        self.calls += 1
        self.rate_limit_events += self.plan.observe(record.get("events"))
        init = isolation.init_event(record.get("events") or [])
        if init is not None:
            for key in PIN_KEYS:
                if init.get(key) != cfg.get(key):
                    raise PinChanged(f"{key} changed mid-batch")
        self.last_limit = judge.rate_limit_message(record)
        return record


def failed_unit_labels(message: str):
    """The proof labels in a run_batch failure message, or None when the message is not a failed unit."""
    m = FAILED_UNIT_RE.search(message)
    return [x for x in m.group(1).split(",") if x] if m else None


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------

def git_head(root=None) -> tuple:
    """(HEAD sha, working tree dirty) of the repo, or ("unknown", False)."""
    root = str(root or claude_run.ROOT)
    try:
        sha = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True, text=True,
                             encoding="utf-8", timeout=30, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "-C", root, "status", "--porcelain"], capture_output=True, text=True,
                                    encoding="utf-8", timeout=30, check=True).stdout.strip())
        return sha, dirty
    except (OSError, subprocess.SubprocessError):
        return "unknown", False


def new_state() -> dict:
    return {"version": 1, "status": "INCOMPLETE", "reasons": [], "scored": False, "isolation_failures": 0,
            "head_sha": "unknown", "tree_dirty": False, "budget_usd": None, "cap_usd": None, "spent_usd": 0.0,
            "throttles": [], "plan_usage": PlanUsage().to_dict(), "judge_rate_limit_events": 0,
            "judge_calls": {"count": 0, "successful": 0, "failed": 0, "throttled": 0}, "judge_throttle_messages": []}


def load_state(out) -> dict:
    path = Path(out) / STATE_NAME
    state = new_state()
    if path.is_file():
        state.update(arms.read_json(path))
    return state


def save_state(out, state: dict) -> None:
    arms.write_json(Path(out) / STATE_NAME, state)


# --------------------------------------------------------------------------
# The pilot
# --------------------------------------------------------------------------

def describe(exc) -> tuple:
    """(reason text, isolation failure count) for an exception that stops the pilot."""
    text = str(exc)
    if isinstance(exc, probes.PinStop):
        return f"claude_code_version or model changed mid-batch (S10): {text}", 0
    if isinstance(exc, probes.BatchStop):
        if text == "budget reached":
            return "cost cap: the pilot budget was reached", 0
        labels = failed_unit_labels(text)
        if labels is not None:
            return f"isolation FAIL (S1): {text}", 1
        return f"batch stopped: {text}", 0
    if isinstance(exc, RateLimited):
        return f"rate limit persisted after one wait and retry (S4): {text}", 0
    if isinstance(exc, claude_run.SpendCapReached):
        return "cost cap: the pilot phase cap was reached", 0
    if isinstance(exc, CostCap):
        return "cost cap: the pilot budget was reached", 0
    if isinstance(exc, JudgeThrottled):
        return f"judge call throttled (S4): {text}", 0
    if isinstance(exc, arms.ArmError):
        return f"batch stopped: {text}", 0
    if isinstance(exc, judge.JudgeBudgetError):
        return f"judge call budget stopped scoring: {text}", 0
    if isinstance(exc, score.ScoreAbort):
        if text.startswith("judge sanity controls failed"):
            return f"judge sanity controls failed (S11): {text}", 0
        if text.startswith("judge isolation failed"):
            return f"isolation FAIL (S1) on a judge call: {text}", 1
        return f"scoring stopped: {text}", 0
    return f"error: {type(exc).__name__}", 0


STOPS = (probes.BatchStop, RateLimited, claude_run.SpendCapReached, CostCap, JudgeThrottled, judge.JudgeBudgetError,
         score.ScoreAbort, arms.ArmError, claude_run.PluginRepoError)


def run_pilot(fixtures_root, out, split, wanted, arm_names, seeds, budget_usd, date, cfg, environ, runner=None,
              label_probe=None, label_items=None, sleep=None, stdout=None, head=None, arms_dir=None) -> int:
    """Run (or resume) the pilot. Returns 0 when COMPLETE, 1 when INCOMPLETE. PILOT.md is written either way."""
    stdout = stdout or sys.stdout
    sleep = sleep or time.sleep
    out = Path(out)
    fids = expand_fixtures(fixtures_root, wanted)
    settings = cfg.get("pilot") or {}
    wait_s = settings.get("rate_limit_wait_s", DEFAULT_WAIT_S)
    minimum = int(settings.get("min_scorable_probes", DEFAULT_MIN_SCORABLE))
    state = load_state(out)
    sha, dirty = head if head else git_head()
    state.update(status="INCOMPLETE", reasons=[], scored=False, head_sha=sha, tree_dirty=dirty, min_scorable=minimum,
                 budget_usd=float(budget_usd), cap_usd=claude_run.phase_cap(cfg, "evaluation"))
    out.mkdir(parents=True, exist_ok=True)
    save_state(out, state)
    start = claude_run.spend_total(environ, "evaluation")
    judge_before = judge.load_stats(environ, "score")
    plan = PlanUsage(state["plan_usage"])
    tracker = Tracker(runner or claude_run.run_claude, plan)
    reason = None

    def remaining() -> float:
        return budget_usd - (claude_run.spend_total(environ, "evaluation") - start)

    def batch() -> None:
        for attempt in (1, 2):
            try:
                probes.run_batch(fixtures_root, out, split, fids, arm_names, seeds, cfg, environ, tracker.runner,
                                 remaining(), date, arms_dir, stdout)
                return
            except probes.PinStop:
                raise
            except probes.BatchStop as exc:
                labels = failed_unit_labels(str(exc))
                if labels is None or not tracker.last_limit or not set(labels) <= judge.RATE_LIMIT_LABELS & isolation.OUTCOME_CHECKS:
                    raise
                state["throttles"].append({"where": "batch", "message": tracker.last_limit})
                save_state(out, state)
                if attempt == 2:
                    raise RateLimited(tracker.last_limit)
                print(f"pilot: rate limited, waiting {wait_s} s and resuming once", file=stdout)
                sleep(wait_s)

    def guarded(fn):
        def call(cfg_, case, environ_, model=None, **route):
            if remaining() <= 0:
                raise CostCap("budget")
            value, rec = fn(cfg_, case, environ_, model, **route)
            state["judge_rate_limit_events"] += plan.observe(rec.get("events"))
            if judge.is_throttled(rec):
                raise JudgeThrottled(judge.rate_limit_message(rec))
            return value, rec
        return call

    try:
        try:
            batch()
            run = arms.read_json(out / "run.json")
            specs = score.load_specs(fixtures_root, run["fixtures"])
            scores = score.score_run(out, specs, cfg, environ, guarded(label_probe or judge.label_probe),
                                     guarded(label_items or judge.label_items))
            score.write_scores(out, scores)
            state["scored"] = True
            if report.write_report(out, None, io.StringIO()) != 0:
                raise score.ScoreAbort("the report could not be written")
            bad = scores["invalid"]
            if bad["judge_labels_invalid"] > 0 or bad["probe_answers_missing"] > 0:
                state["reasons"].append("invalid results: %d judge labels invalid, %d probe answers missing" % (
                    bad["judge_labels_invalid"], bad["probe_answers_missing"]))
            low = sorted(f for f, n in scores["scorable_counts"].items() if n < minimum)
            if low:
                state["reasons"].append("fewer than %d scorable probes on %s" % (
                    minimum, ", ".join(f"{f} ({scores['scorable_counts'][f]})" for f in low)))
        except STOPS as exc:
            text, isolation_fails = describe(exc)
            state["reasons"].append(text)
            state["isolation_failures"] += isolation_fails
        except Exception as exc:
            state["reasons"].append(describe(exc)[0])
            raise
    finally:
        state["plan_usage"] = plan.to_dict()
        _fold_judge_counter(state, judge_before, environ)
        state["spent_usd"] = round(state["spent_usd"] + claude_run.spend_total(environ, "evaluation") - start, 6)
        state["status"] = "COMPLETE" if not state["reasons"] else "INCOMPLETE"
        save_state(out, state)
        try:
            text = write_pilot_md(out, fixtures_root)
            print(f"pilot: {state['status']}" + ("" if not state["reasons"] else ": " + "; ".join(state["reasons"])),
                  file=stdout)
            print(f"pilot: wrote PILOT.md ({len(text.splitlines())} lines)", file=stdout)
        except (OSError, ValueError, KeyError):
            print("pilot: PILOT.md could not be written", file=stdout)
    return 0 if state["status"] == "COMPLETE" else 1


def _fold_judge_counter(state: dict, before: dict, environ) -> None:
    """Add this invocation's change in the scoring call counter to the state."""
    try:
        after = judge.load_stats(environ, "score")
    except judge.JudgeBudgetError:
        return
    calls = state["judge_calls"]
    for key in ("count", "successful", "failed", "throttled"):
        calls[key] = calls.get(key, 0) + after.get(key, 0) - before.get(key, 0)
    state["judge_throttle_messages"] += after.get("throttle_messages", [])[len(before.get("throttle_messages", [])):]


def command_pilot(fixtures_root, out, split, wanted, arm_names, seeds, budget_usd, date, environ=None, stdout=None,
                  runner=None, label_probe=None, label_items=None, sleep=None, head=None, arms_dir=None) -> int:
    """Live: needs CPR_EVAL_LIVE=1, a clean environment and a passing preflight. --budget-usd above the cap refuses,
    and so does an --out the CLI would refuse."""
    stdout = stdout or sys.stdout
    refusal = claude_run.out_refusal(None if out is None else str(out))
    if refusal:
        print(f"pilot: REFUSED {refusal}", file=stdout)
        return 2
    try:
        cap = claude_run.phase_cap(claude_run.load_config(), "evaluation")
    except (OSError, ValueError, claude_run.ConfigError):
        print("pilot: STOP config unreadable or the pilot cap is missing", file=stdout)
        return 1
    if not (isinstance(budget_usd, (int, float)) and math.isfinite(budget_usd) and 0 < budget_usd <= cap):
        print(f"pilot: REFUSED --budget-usd must be above 0 and at most phase_caps.evaluation ({cap:.2f})", file=stdout)
        return 2

    def work(cfg, env, o):
        try:
            return run_pilot(fixtures_root, out, split, wanted, arm_names, seeds, float(budget_usd), date, cfg, env,
                             runner, label_probe, label_items, sleep, o, head, arms_dir)
        except STOPS as exc:
            print(f"pilot: STOP {exc}", file=o)
            return 1
    return fixtures._guarded("pilot", work, environ, stdout)


# --------------------------------------------------------------------------
# PILOT.md
# --------------------------------------------------------------------------

def p90(values):
    """Nearest-rank 90th percentile; None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.9 * len(ordered)) - 1)]


def _mean(values):
    return sum(values) / len(values) if values else None


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _call(cost, inp, outp, wall, rl) -> dict:
    return {"cost": cost if _num(cost) else None, "inp": inp if _num(inp) else None,
            "out": outp if _num(outp) else None, "wall": wall if _num(wall) else None,
            "rl": rl if _num(rl) else None}


def collect_calls(out, fids, fixtures_root) -> dict:
    """Per-call numbers by call type, from the committed gen.json files and the stored result files."""
    out = Path(out)
    calls = {"gen": [], "preserve": [], "probe": [], "judge": []}
    for fid in fids:
        path = Path(fixtures_root) / fid / "gen.json"
        if path.is_file():
            for turn in arms.read_json(path).get("turns", []):
                inp = sum(turn.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens",
                                                     "cache_read_input_tokens")) if _num(turn.get("input_tokens")) else None
                wall = turn["duration_ms"] / 1000 if _num(turn.get("duration_ms")) else None
                calls["gen"].append(_call(turn.get("cost_usd"), inp, turn.get("output_tokens"), wall, None))
    for kind, folder in (("preserve", "preserve"), ("probe", "probes")):
        for path in sorted((out / folder).glob("*/*/s*.json")):
            d = arms.read_json(path)
            calls[kind].append(_call(d.get("cost_usd"), d.get("input_tokens_total"), d.get("output_tokens"),
                                     d.get("wall_s"), d.get("rate_limit_events")))
    labels = out / "labels.json"
    if labels.is_file():
        data = arms.read_json(labels)
        for kind in ("probe", "items"):
            for key in sorted(data.get(kind, {})):
                d = data[kind][key]
                calls["judge"].append(_call(d.get("cost_usd"), d.get("input_tokens_total"), d.get("output_tokens"),
                                            d.get("wall_s"), None))
    return calls


def _vals(rows, key):
    return [r[key] for r in rows if r[key] is not None]


def _mean_of(calls, kind, key):
    return _mean(_vals(calls[kind], key))


# The rows of the budget projection table: (block, preserve and probe calls, judge calls as text, {type: count}, judge calls).
# The judge-calls column counts the judge's calls; the judge runs on Claude (config judge backend), so they are priced at
# the measured judge mean. A count of None is a slot: the formula is shown and the count is left to the reader.
BUDGET_ROWS = (
    ("Fixture gen, 15 more dev + 25 held-out (about 8 turns each; 3 dev and 5 held-out of about 30)",
     "about 500", "0", {"gen": 500}, 0),
    ("Error-analysis baseline, 20 dev x 1 seed", "20 preserve + 20 probe", "about 40",
     {"preserve": 20, "probe": 20}, 40),
    ("Dev run, 20 x 5 seeds x {floor, baseline, oracle}", "100 preserve + 300 probe", "about 400",
     {"preserve": 100, "probe": 300}, 400),
    ("Held-out check, 25 x 5 x {floor, oracle}, plus repairs", "about 250 probe", "about 250",
     {"probe": 250}, 250),
    ("Dev iteration, up to 3 rounds x 3 ablations x 20 x 5", "up to 900 preserve + 900 probe", "up to 1,800",
     {"preserve": 900, "probe": 900}, 1800),
    ("Held-out run, 25 x 5 x 6 arms (4 run preserve)", "500 preserve + 750 probe", "about 1,250",
     {"preserve": 500, "probe": 750}, 1250),
)
TYPE_LABEL = {"gen": "gen turn", "preserve": "preserve", "probe": "probe", "judge": "judge"}


def _projection(counts: dict, calls: dict) -> tuple:
    """(formula + dollars cell, wall-clock cell) for one row. None counts are slots; a missing mean is n/a."""
    parts, dollars, seconds, known = [], 0.0, 0.0, True
    for kind in ("gen", "preserve", "probe", "judge"):
        if kind not in counts:
            continue
        n = counts[kind]
        cost, wall = _mean_of(calls, kind, "cost"), _mean_of(calls, kind, "wall")
        shown = "<slot>" if n is None else f"{n:,}"
        parts.append(f"{shown} x {TYPE_LABEL[kind]} {report.fmt(cost, 4)}")
        if n is None or cost is None or wall is None:
            known = False
        else:
            dollars += n * cost
            seconds += n * wall
    formula = " + ".join(parts)
    if not known:
        return f"{formula} = n/a", "n/a"
    return f"{formula} = ${dollars:,.2f}", f"{seconds / 3600:,.2f} h"


def _stat_row(name, rows) -> list:
    n = len(rows)
    out = [name, str(n)]
    for key, nd in (("cost", 4), ("inp", 0), ("out", 0), ("wall", 1)):
        vals = _vals(rows, key)
        out += [report.fmt(_mean(vals), nd), report.fmt(p90(vals), nd)]
    rl = _vals(rows, "rl")
    out.append(str(int(sum(rl))) if rl else "n/a")
    return out


def collect_arm_calls(out, arm_names) -> dict:
    """{arm: {"preserve": [calls], "probe": [calls]}} from the stored result files."""
    out = Path(out)
    return {arm: {kind: [_call_of(p) for p in sorted((out / folder / arm).glob("*/s*.json"))]
                  for kind, folder in (("preserve", "preserve"), ("probe", "probes"))} for arm in arm_names}


def build_pilot_md(run: dict, state: dict, scores, calls: dict, arm_calls: dict) -> str:
    plan = PlanUsage(state.get("plan_usage"))
    status = state.get("status", "INCOMPLETE")
    reasons = state.get("reasons", [])
    out = [f"# Pilot report: {run['run_id']} ({status})", ""]
    out.append("Split %s, run date %s. %d fixtures, %d seeds, arms %s." % (
        run["split"], run["date"], len(run["fixtures"]), run["seeds"], ", ".join(run["arms"])))
    out.append("Fixtures: %s." % ", ".join(run["fixtures"]))
    out.append("")

    out += ["## Pins", "",
            "- Claude Code %s" % run["claude_code_version"],
            "- Model %s" % run["model"],
            "- Plugin ref %s" % run["plugin_ref"],
            "- Judge model %s" % run["judge_model"],
            "- HEAD %s, working tree %s" % (state.get("head_sha", "unknown"), "dirty" if state.get("tree_dirty") else "clean"),
            ""]

    out += ["## Per call type", "",
            "Cost is `total_cost_usd` per call: the API-price equivalent, not a charge, under a subscription. Input "
            "tokens are input + cache creation + cache read; output tokens are shown separately. Wall-clock is seconds "
            "per call (for a fixture-gen turn it is the CLI's `duration_ms`, which is all gen.json keeps). Each cell "
            "pair is mean, then p90 (nearest rank). Rate-limit events are the informational `rate_limit_event` rows in "
            "each stream (status allowed or allowed_warning), not throttles.", ""]
    header = ["Call type", "n", "$ mean (API-price equivalent, subscription)", "$ p90 (API-price equivalent, subscription)",
              "input tokens mean", "input tokens p90", "output tokens mean", "output tokens p90",
              "wall-clock s mean", "wall-clock s p90", "rate-limit events"]
    rows = [_stat_row("fixture-gen turn", calls["gen"]), _stat_row("preserve", calls["preserve"]),
            _stat_row("probe", calls["probe"]), _stat_row("judge", calls["judge"])]
    judge_rl = state.get("judge_rate_limit_events")
    rows[3][-1] = str(int(judge_rl)) if _num(judge_rl) else "n/a"
    out += report.table(header, rows)

    jc = state.get("judge_calls", {})
    out += ["## Rate limits and judge throttles", ""]
    out.append("Throttles in the preserve and probe batch: %d." % len(state.get("throttles", [])))
    for t in state.get("throttles", []):
        out.append("- %s: %s" % (t.get("where", "batch"), t.get("message", "")))
    out.append("Judge calls: %d made, %d successful, %d failed, %d throttled." % (
        jc.get("count", 0), jc.get("successful", 0), jc.get("failed", 0), jc.get("throttled", 0)))
    for message in state.get("judge_throttle_messages", []):
        out.append("- throttled: %s" % message)
    out.append("")

    out += ["## Subscription usage", ""]
    if plan.windows:
        out.append("Reported by Claude Code in its rate-limit events (%d events; statuses %s). Utilization is shown as "
                   "reported, in order of the calls." % (
                       plan.events, ", ".join(f"{k} {plan.statuses[k]}" for k in sorted(plan.statuses)) or "none"))
        out.append("")
        out += report.table(["Window", "observations", "first", "last", "max"],
                            [[name, str(w["n"]), report.fmt(w["first"], 3), report.fmt(w["last"], 3), report.fmt(w["max"], 3)]
                             for name, w in sorted(plan.windows.items())])
    else:
        out.append("Not reported by Claude Code: no record carries usage-against-plan data.")
        out.append("")

    out += ["## Per arm", ""]
    if scores is not None:
        s = scores["sanity"]
        out.append("Judge sanity controls: oracle recall %s (minimum %s), mismatch recall %s (maximum %s): %s." % (
            report.fmt(s["oracle_recall"]), report.fmt(s["oracle_recall_min"], 2), report.fmt(s["mismatch_recall"]),
            report.fmt(s["mismatch_recall_max"], 2), "PASS" if s["ok"] else "FAIL"))
    else:
        out.append("Judge sanity controls: not run (scoring did not complete).")
    out.append("")
    arm_rows = []
    for arm in run["arms"]:
        pres, prob = arm_calls[arm]["preserve"], arm_calls[arm]["probe"]
        arm_rows.append([arm, str(len(pres)), report.fmt(_mean(_vals(pres, "cost")), 4), str(len(prob)),
                         report.fmt(_mean(_vals(prob, "cost")), 4)])
    out += report.table(["Arm", "preserve runs", "preserve $ mean", "probe sessions", "probe $ mean"], arm_rows)
    minimum = int(state.get("min_scorable", DEFAULT_MIN_SCORABLE))
    if scores is not None:
        out.append("Scorable probes per fixture (the floor-fail and oracle-majority filters; the same for every arm). "
                   "A fixture needs at least %d." % minimum)
        out.append("")
        out += report.table(["Fixture", "scorable probes", "non-abstention probes", "check"],
                            [[f, str(scores["scorable_counts"][f]), str(len(scores["scorable"][f]) + len(scores["dropped"][f])),
                              "ok" if scores["scorable_counts"][f] >= minimum else "BELOW MINIMUM"]
                             for f in scores["fixtures"]])
    else:
        out.append("Scorable probes per fixture: not computed (scoring did not complete).")
        out.append("")

    out += ["## Budget projection", "",
            "Each cell multiplies a planned call count by the mean measured above (gen turn, preserve, probe, "
            "judge), and wall-clock assumes the calls run one after another. The Judge calls column counts judge calls; "
            "the judge runs on Claude, so they are priced at the measured judge mean. Costs are API-price equivalents under "
            "a subscription. Held-out n is planned at 25 here and set later.", ""]
    table_rows = []
    for block, claude_calls, judge_call_text, counts, judge_calls in BUDGET_ROWS:
        priced = dict(counts)
        if judge_calls:
            priced["judge"] = judge_calls
        dollars, wall = _projection(priced, calls)
        table_rows.append([block, claude_calls, judge_call_text, dollars, wall])
    out += report.table(["Block", "Claude calls", "Judge calls", "$ (measured mean x count)", "Wall-clock"], table_rows)

    out += ["## Completion", "",
            "Pilot ledger growth in this results directory: $%s (budget per invocation $%s, phase cap $%s). "
            "Isolation FAIL count: %d." % (report.fmt(state.get("spent_usd"), 2), report.fmt(state.get("budget_usd"), 2),
                                           report.fmt(state.get("cap_usd"), 2), state.get("isolation_failures", 0)),
            ""]
    # README "Exclusions": an excluded cell is listed, with its reason (check names only, never marker text).
    for entry in run.get("excluded_cells", []):
        out.append("- Excluded: %s, %s, seed %s, %s run; failed %s." % (
            entry.get("fixture"), entry.get("arm"), entry.get("seed"), entry.get("kind"), ", ".join(entry.get("checks", []))))
    if run.get("excluded_cells"):
        out.append("")
    out.append("COMPLETE" if status == "COMPLETE" else "INCOMPLETE: " + "; ".join(reasons or ["no reason recorded"]))
    return "\n".join(out) + "\n"


def _call_of(path) -> dict:
    d = arms.read_json(path)
    return _call(d.get("cost_usd"), d.get("input_tokens_total"), d.get("output_tokens"), d.get("wall_s"),
                 d.get("rate_limit_events"))


def render_pilot_md(out, fixtures_root=None) -> str:
    """PILOT.md text for a results directory. Raises OSError/ValueError/KeyError when run.json or the state is missing."""
    out = Path(out)
    run = arms.read_json(out / "run.json")
    state = arms.read_json(out / STATE_NAME)
    root = Path(fixtures_root) if fixtures_root else fixtures.FIXTURES_DIR / run["split"]
    scores = arms.read_json(out / "scores.json") if state.get("scored") else None
    calls = collect_calls(out, run["fixtures"], root)
    return build_pilot_md(run, state, scores, calls, collect_arm_calls(out, run["arms"]))


def write_pilot_md(out, fixtures_root=None, dest=None) -> str:
    text = render_pilot_md(out, fixtures_root)
    claude_run.atomic_write(Path(dest or out) / PILOT_MD, text)
    return text


def command_pilot_md(source, fixtures_root=None, out_dir=None, stdout=None) -> int:
    """Regenerate PILOT.md offline from a results directory; no model call."""
    stdout = stdout or sys.stdout
    try:
        text = write_pilot_md(source, fixtures_root, out_dir)
    except (OSError, ValueError, KeyError):
        print("pilot-md: STOP run.json or pilot-state.json missing or unreadable", file=stdout)
        return 1
    print(f"pilot-md: wrote PILOT.md ({len(text.splitlines())} lines)", file=stdout)
    return 0
