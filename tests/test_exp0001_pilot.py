"""Tests for the pilot: the whole run driven through a scripted claude and a fake judge, then PILOT.md.

Faults are injected by a runner that calls the real run_claude (against the scripted claude) and then edits the
returned record, so the isolation proof, the ledger and the files all behave as in a live run.

Run with: python3 -m unittest discover tests
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests import exp0001_paths  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]

import arms  # noqa: E402
import claude_run  # noqa: E402
import isolation  # noqa: E402
import judge  # noqa: E402
import pilot  # noqa: E402
import probes  # noqa: E402
from tests import test_exp0001_score as base  # noqa: E402

LIMIT_TEXT = "You've hit your limit. Resets at 3pm."
ALL = ",".join(base.IDS)


def result_of(rec):
    return isolation.result_event(rec["events"])


def rate_limited(rec):
    """Turn a finished record into what a throttled run looks like."""
    rec["events"].insert(1, {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}})
    result_of(rec).update(is_error=True, subtype="error_during_execution", result=LIMIT_TEXT, api_error_status=429)


def plain_failure(rec):
    result_of(rec).update(is_error=True, subtype="error_during_execution", result="the model call failed")


def new_version(rec):
    isolation.init_event(rec["events"])["claude_code_version"] = "9.9.9"


def with_plan_events(rec):
    window = {"five_hour": {"utilization": 0.25}, "seven_day": {"utilization": 0.4}}
    rec["events"].insert(1, {"type": "rate_limit_event", "rate_limit_info": {
        "status": "allowed_warning", "unifiedWindows": window}})


class PilotEnv(base.Env):
    def setUp(self):
        super().setUp()
        self.judge = base.FakeJudge(base.SPECS)
        self.script = {}
        self.launched = 0
        self.sleeps = []

    def runner(self, cfg, prompt, sandbox, **kw):
        rec = claude_run.run_claude(cfg, prompt, sandbox, **kw)
        self.launched += 1
        edit = self.script.get(self.launched)
        if edit:
            edit(rec)
        return rec

    def pilot(self, wanted=ALL, seeds=2, budget=30.0, **kw):
        out = io.StringIO()
        args = dict(runner=self.runner, label_probe=self.judge.label_probe, label_items=self.judge.label_items,
                    sleep=self.sleeps.append, head=("0123abc", False), stdout=out)
        args.update(kw)
        try:
            return pilot.run_pilot(base.FIXTURES, self.out, "dev", wanted, ["floor", "baseline", "oracle"], seeds, budget,
                                   "20260101", self.cfg, self.environ, **args)
        finally:
            self.last_output = out.getvalue()

    def state(self):
        return arms.read_json(self.out / pilot.STATE_NAME)

    def pilot_md(self):
        return (self.out / "PILOT.md").read_text(encoding="utf-8")


class FullRunTests(PilotEnv):
    def test_a_full_fake_pilot_writes_the_report_and_a_complete_pilot_md(self):
        self.assertEqual(self.pilot(), 0, self.last_output)
        self.assertTrue((self.out / "REPORT.md").is_file())
        self.assertTrue((self.out / "scores.json").is_file())
        text = self.pilot_md()
        self.assertTrue(text.rstrip().endswith("\nCOMPLETE"), text[-300:])
        self.assertIn("(COMPLETE)", text.splitlines()[0])
        self.assertEqual(self.sleeps, [])
        self.assertEqual(self.launched, 16)  # 4 preserve runs + 12 probe sessions
        for needle in ("API-price equivalent, subscription", "Pins", "Claude Code 2.1.302", "claude-sonnet-5-5",
                       "HEAD 0123abc, working tree clean", "Judge sanity controls: oracle recall", "PASS",
                       "Not reported by Claude Code", "Isolation FAIL count: 0"):
            self.assertIn(needle, text)
        for fid in base.IDS:
            self.assertRegex(text, r"\| %s \| \d+ \| \d+ \| ok \|" % fid)
        self.assertNotIn(str(self.tmp), text)
        self.assertNotIn(str(self.tmp), (self.out / pilot.STATE_NAME).read_text(encoding="utf-8"))

    def test_the_per_call_type_table_and_the_gate_table_are_filled_from_measured_means(self):
        self.assertEqual(self.pilot(), 0, self.last_output)
        text = self.pilot_md()
        lines = {ln.split("|")[1].strip(): [c.strip() for c in ln.strip().strip("|").split("|")]
                 for ln in text.splitlines() if ln.startswith("| ") and "---" not in ln}
        pres, prob, jud = lines["preserve"], lines["probe"], lines["judge"]
        self.assertEqual((pres[1], pres[2], pres[3]), ("4", "0.2000", "0.2000"))
        self.assertEqual((prob[1], prob[2]), ("12", "0.0100"))
        self.assertEqual(jud[2], "0.0100")
        self.assertEqual(lines["fixture-gen turn"][1], str(sum(len(json.loads(
            (base.FIXTURES / fid / "gen.json").read_text(encoding="utf-8"))["turns"]) for fid in base.IDS)))
        gate = text.split("## Budget projection")[1].split("## Completion")[0]
        self.assertNotIn("n/a", gate)
        # dev run: 100 preserve at 0.2 + 300 probe at 0.01 + 400 judge calls at 0.01
        self.assertIn("100 x preserve 0.2000 + 300 x probe 0.0100 + 400 x judge 0.0100 = $27.00", gate)
        rows = [ln for ln in gate.splitlines() if ln.startswith("| ") and "---" not in ln]
        self.assertEqual(len(rows) - 1, 6)  # six blocks under the header
        self.assertIn("| Dev run,", gate)
        self.assertIn("Fixture gen", gate)

    def test_plan_usage_reported_by_the_stream_is_shown_not_inferred(self):
        self.script = {1: with_plan_events, 2: with_plan_events}
        self.assertEqual(self.pilot(), 0, self.last_output)
        text = self.pilot_md()
        self.assertNotIn("Not reported by Claude Code", text)
        self.assertRegex(text, r"\| five_hour \| 2 \| 0\.250 \| 0\.250 \| 0\.250 \|")
        self.assertIn("allowed_warning 2", text)
        self.assertEqual(self.state()["plan_usage"]["events"], 2)

    def test_pilot_md_regenerates_byte_for_byte_and_through_the_cli(self):
        self.assertEqual(self.pilot(), 0, self.last_output)
        first = (self.out / "PILOT.md").read_bytes()
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(pilot.command_pilot_md(self.out, base.FIXTURES, d, io.StringIO()), 0)
            self.assertEqual((Path(d) / "PILOT.md").read_bytes(), first)
            proc = subprocess.run([sys.executable, exp0001_paths.RUN, "pilot-md", "--from", str(self.out),
                                   "--fixtures-root", str(base.FIXTURES), "--out", d], capture_output=True, text=True,
                                  encoding="utf-8")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual((Path(d) / "PILOT.md").read_bytes(), first)
        self.assertEqual(pilot.command_pilot_md(self.tmp / "nowhere", None, None, io.StringIO()), 1)

    def test_a_range_expands_against_the_fixtures_that_exist_and_an_unknown_id_refuses(self):
        self.assertEqual(pilot.expand_fixtures(base.FIXTURES, "dev-001..dev-005"), [
            "dev-001-tide-table", "dev-002-recipe-scaler", "dev-003-greenhouse-log", "dev-004-chess-clock", "dev-005-bird-count"])
        self.assertEqual(pilot.expand_fixtures(base.FIXTURES, "dev-002..dev-003,dev-001"), [
            "dev-002-recipe-scaler", "dev-003-greenhouse-log", "dev-001-tide-table"])
        for bad in ("dev-001..dev-099", "dev-099", "dev-003..dev-001", ""):
            with self.assertRaises(probes.BatchStop):
                pilot.expand_fixtures(base.FIXTURES, bad or ",")

    def test_a_range_skips_an_excluded_fixture_and_one_ending_on_it_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for fid in ("dev-001-tide-table", "dev-002-recipe-scaler", "dev-003-greenhouse-log"):
                spec = json.loads((base.FIXTURES / fid / "spec.json").read_text(encoding="utf-8"))
                if fid == "dev-002-recipe-scaler":
                    spec["excluded"] = {"date": "2026-10-18", "reason": "Not run."}
                (root / fid).mkdir()
                (root / fid / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
            wanted = ["dev-001-tide-table", "dev-003-greenhouse-log"]
            self.assertEqual(pilot.expand_fixtures(root, "dev-001..dev-003"), wanted)
            self.assertEqual(pilot.expand_fixtures(root, "all"), wanted)
            for bad in ("dev-001..dev-002", "dev-002..dev-003", "dev-002"):
                with self.assertRaises(probes.BatchStop) as caught:
                    pilot.expand_fixtures(root, bad)
                self.assertEqual(str(caught.exception), "fixture excluded: dev-002-recipe-scaler")


class StopTests(PilotEnv):
    def test_the_cost_cap_mid_batch_is_incomplete_and_a_rerun_resumes_without_relaunching(self):
        self.assertEqual(self.pilot(budget=0.5), 1, self.last_output)
        self.assertIn("cost cap", self.pilot_md())
        self.assertIn("INCOMPLETE: cost cap", self.pilot_md())
        self.assertFalse((self.out / "scores.json").exists())
        self.assertFalse(self.state()["scored"])
        first = self.launched
        self.assertGreater(first, 0)
        self.assertLess(first, 16)
        self.assertEqual(self.pilot(budget=30.0), 0, self.last_output)
        self.assertEqual(self.launched, 16)  # every unit exactly once across both invocations
        self.assertTrue(self.pilot_md().rstrip().endswith("\nCOMPLETE"))

    def test_the_phase_cap_is_a_cost_cap_stop_too(self):
        self.cfg["phase_caps"]["evaluation"] = 0.3
        self.assertEqual(self.pilot(), 1, self.last_output)
        self.assertIn("phase cap", self.pilot_md())

    def test_one_rate_limit_then_success_waits_retries_once_and_completes(self):
        self.script = {1: rate_limited}
        self.assertEqual(self.pilot(), 0, self.last_output)
        self.assertEqual(self.sleeps, [60])
        self.assertEqual(self.launched, 17)  # the throttled launch ran again
        self.assertEqual(self.state()["throttles"], [{"where": "batch", "message": LIMIT_TEXT}])
        text = self.pilot_md()
        self.assertIn("Throttles in the preserve and probe batch: 1.", text)
        self.assertIn("- batch: " + LIMIT_TEXT, text)
        self.assertTrue(text.rstrip().endswith("\nCOMPLETE"))

    def test_two_rate_limits_stop_the_batch(self):
        self.script = {1: rate_limited, 2: rate_limited}
        self.assertEqual(self.pilot(), 1, self.last_output)
        self.assertEqual(self.sleeps, [60])
        self.assertEqual(self.launched, 2)
        self.assertIn("rate limit persisted after one wait and retry (S4)", self.pilot_md())
        self.assertEqual(self.state()["isolation_failures"], 0)

    def test_a_run_that_fails_only_an_outcome_check_does_not_stop_the_pilot_and_is_not_run_again(self):
        # README "Exclusions": the port separates outcome checks from isolation checks; the harness at 08ef45f stopped here.
        self.script = {1: plain_failure}
        self.assertEqual(self.pilot(), 0, self.last_output)
        self.assertEqual((self.sleeps, self.launched), ([], 16))
        self.assertEqual(self.state()["isolation_failures"], 0)
        self.assertEqual(arms.read_json(self.out / "run.json")["proof_counts"]["outcome_only_by_check"], {"result_error": 1})
        self.assertIn("Isolation FAIL count: 0", self.pilot_md())

    def test_a_throttle_that_also_fails_the_isolation_proof_is_not_waited_for(self):
        # An isolation failure is re-run once at once (README "Exclusions"), then the cell is excluded; no 60 s wait.
        self.plan["leak"] = base.HOST_MARKER
        self.write_plan()
        self.script = {1: rate_limited}
        self.assertEqual(self.pilot(), 1, self.last_output)
        self.assertEqual(self.sleeps, [])
        # 24 launches: the plan leaks the host marker into every run, so the 4 preserve cells fail isolation and are
        # excluded, which skips their 4 probe sessions; the other 8 probe cells fail too: 12 cells x 2 launches (the
        # run and its one re-run) = 24. Nothing is launched after an exclusion.
        self.assertEqual(self.launched, 24)
        excluded = arms.read_json(self.out / "run.json")["excluded_cells"]
        self.assertEqual(len(excluded), 12)
        self.assertTrue(all("markers_found" in e["checks"] for e in excluded))
        self.assertIn("markers_found", self.pilot_md())
        self.assertNotIn(base.HOST_MARKER, self.pilot_md())

    def test_a_version_change_mid_batch_stops(self):
        self.script = {3: new_version}
        self.assertEqual(self.pilot(), 1, self.last_output)
        self.assertEqual(self.launched, 3)
        text = self.pilot_md()
        self.assertIn("claude_code_version or model changed mid-batch (S10)", text)
        self.assertNotIn("isolation FAIL", text)

    def test_a_missing_probe_answer_marks_the_run_incomplete_with_the_reason(self):
        def drop_last_answer(rec):
            result = result_of(rec)
            reply = json.loads(result["result"])
            reply["answers"].pop()
            result["result"] = json.dumps(reply)
        self.script = {3: drop_last_answer}  # the baseline's probe session on the first fixture, seed 1
        self.assertEqual(self.pilot(), 1, self.last_output)
        self.assertEqual(self.state()["status"], "INCOMPLETE")
        self.assertTrue(self.state()["scored"])  # the run was scored and reported; the gate then refuses COMPLETE
        self.assertEqual(arms.read_json(self.out / "scores.json")["invalid"]["probe_answers_missing"], 1)
        text = self.pilot_md()
        self.assertIn("invalid results: 0 judge labels invalid, 1 probe answers missing", text)
        self.assertTrue(text.rstrip().splitlines()[-1].startswith("INCOMPLETE: invalid results"))

    def test_an_invalid_judge_label_marks_the_run_incomplete_with_the_reason(self):
        victim = [p["id"] for p in base.SPECS[base.IDS[0]]["probes"] if p["type"] != "abstention"][-1]
        asked = []

        def label_probe(cfg, case, environ, model=None, **route):
            labels, rec = self.judge.label_probe(cfg, case, environ, model, **route)
            if not asked and victim in labels:  # the judge leaves one probe out of one reply
                asked.append(labels.pop(victim))
            return labels, rec
        self.assertEqual(self.pilot(label_probe=label_probe), 1, self.last_output)
        self.assertTrue(asked)
        self.assertIn("invalid results: 1 judge labels invalid, 0 probe answers missing", self.pilot_md())

    def test_fewer_than_three_scorable_probes_marks_the_run_incomplete_and_names_the_fixture(self):
        spec = base.SPECS[base.IDS[1]]
        first_line = spec["repo"]["files"]["CLAUDE.md"].split("\n")[0]
        probe_ids = [p["id"] for p in spec["probes"] if p["type"] != "abstention"]
        self.plan["fixtures"][first_line]["never_correct"] = probe_ids[2:]  # two probes left that anything can answer
        self.write_plan()
        self.assertEqual(self.pilot(), 1, self.last_output)
        self.assertEqual(self.state()["reasons"], ["fewer than 3 scorable probes on %s (2)" % base.IDS[1]])
        text = self.pilot_md()
        self.assertIn("INCOMPLETE: fewer than 3 scorable probes on " + base.IDS[1], text)
        self.assertRegex(text, r"\| %s \| 2 \| \d+ \| BELOW MINIMUM \|" % base.IDS[1])
        self.assertTrue((self.out / "REPORT.md").is_file())

    def test_failed_judge_sanity_controls_are_reported_and_stop(self):
        self.judge.mode = "blind"
        self.assertEqual(self.pilot(), 1, self.last_output)
        text = self.pilot_md()
        self.assertIn("judge sanity controls failed (S11)", text)
        self.assertIn("Judge sanity controls: not run", text)
        self.assertFalse((self.out / "scores.json").exists())

    def test_a_throttled_judge_call_stops_scoring_and_the_counters_keep_it_apart(self):
        def throttled_probe(cfg, case, environ, model=None, **route):
            self.judge.probe_calls.append((case, route))
            judge.take_call(environ, 99, "score")
            rec = {"events": [{"type": "result", "subtype": "error_during_execution", "is_error": True, "result": LIMIT_TEXT}],
                   "proof": {"ok": False, "failures": ["result_error"]}}
            judge.record_throttle(environ, judge.rate_limit_message(rec), "score")
            return None, rec
        self.assertEqual(self.pilot(label_probe=throttled_probe), 1, self.last_output)
        state = self.state()
        self.assertEqual(state["judge_calls"]["throttled"], 1)
        self.assertEqual(state["judge_throttle_messages"], [LIMIT_TEXT])
        text = self.pilot_md()
        self.assertIn("Judge calls: 1 made, 0 successful, 0 failed, 1 throttled.", text)
        self.assertIn("- throttled: " + LIMIT_TEXT, text)
        self.assertIn("judge call throttled (S4)", text)

    def test_the_budget_also_gates_judge_calls(self):
        # The batch takes 0.92 of the ledger; a 0.92 allowance leaves nothing for the judge.
        self.assertEqual(self.pilot(budget=0.92), 1, self.last_output)
        self.assertEqual(len(self.judge.probe_calls) + len(self.judge.items_calls), 0)
        self.assertIn("cost cap", self.pilot_md())


class CommandTests(PilotEnv):
    def setUp(self):
        super().setUp()
        for name, fake in (("preflight", lambda cfg, environ=None: ""), ("load_config", lambda path=None: self.cfg)):
            original = getattr(claude_run, name)
            setattr(claude_run, name, fake)
            self.addCleanup(setattr, claude_run, name, original)

    def command(self, budget, environ=None, **kw):
        out = io.StringIO()
        code = pilot.command_pilot(base.FIXTURES, self.out, "dev", "dev-001..dev-002", ["floor", "baseline", "oracle"], 1,
                                   budget, "20260101", environ or self.environ, out, runner=self.runner,
                                   label_probe=self.judge.label_probe, label_items=self.judge.label_items,
                                   sleep=self.sleeps.append, head=("0123abc", True), **kw)
        return code, out.getvalue()

    def test_a_budget_above_the_pilot_cap_refuses_before_any_call(self):
        code, text = self.command(30.01)
        self.assertEqual(code, 2)
        self.assertIn("REFUSED --budget-usd", text)
        for bad in (0, -1, float("nan")):
            self.assertEqual(self.command(bad)[0], 2)
        self.assertEqual((self.launched, self.calls()), (0, []))
        self.assertFalse(self.out.exists())

    def test_the_live_gate_applies_and_a_budget_at_the_cap_runs(self):
        env = dict(self.environ)
        env.pop("CPR_EVAL_LIVE")
        code, text = self.command(30.0, env)
        self.assertEqual((code, self.launched), (2, 0))
        self.assertIn("live run refused", text)
        code, text = self.command(30.0)
        self.assertEqual(code, 0, text)
        self.assertIn("pilot: COMPLETE", text)
        self.assertIn("working tree dirty", self.pilot_md())

    def test_an_unknown_fixture_in_the_range_refuses(self):
        self.assertTrue((base.FIXTURES / base.IDS[0] / "spec.json").is_file())  # the refusal is for the unknown id, not an empty folder
        out = io.StringIO()
        code = pilot.command_pilot(base.FIXTURES, self.out, "dev", "dev-001..dev-099", ["floor"], 1, 30.0, "20260101",
                                   self.environ, out, runner=self.runner)
        self.assertEqual(code, 1)
        self.assertIn("pilot: STOP fixture selector matches 0", out.getvalue())
        self.assertEqual(self.launched, 0)


class HelperTests(unittest.TestCase):
    def test_p90_is_nearest_rank(self):
        self.assertIsNone(pilot.p90([]))
        self.assertEqual(pilot.p90([5]), 5)
        self.assertEqual(pilot.p90(list(range(1, 11))), 9)
        self.assertEqual(pilot.p90([3, 1, 2]), 3)

    def test_a_batch_failure_message_yields_its_labels(self):
        self.assertEqual(pilot.failed_unit_labels("dev-001 seed 1 baseline preserve failed: a,b"), ["a", "b"])
        self.assertEqual(pilot.failed_unit_labels("dev-001 seed 2 floor probe failed: result_error"), ["result_error"])
        self.assertIsNone(pilot.failed_unit_labels("budget reached"))

    def test_the_cli_refuses_a_budget_above_the_cap_and_a_missing_live_flag(self):
        env = {"PATH": "/usr/bin:/bin"}
        run = lambda *a: subprocess.run([sys.executable, exp0001_paths.RUN, "pilot", *a], capture_output=True,
                                        text=True, encoding="utf-8", env=env)
        with tempfile.TemporaryDirectory() as out:  # --out is required and must lie outside the clone
            proc = run("--budget-usd", "34.01", "--out", out)  # the evaluation cap is 34
            self.assertEqual(proc.returncode, 2)
            self.assertIn("REFUSED", proc.stdout)
            proc = run("--budget-usd", "34", "--out", out)
            self.assertEqual(proc.returncode, 2)
            self.assertIn("live run refused", proc.stdout)
            self.assertNotEqual(run("--out", out).returncode, 0)  # --budget-usd is required


if __name__ == "__main__":
    unittest.main()
