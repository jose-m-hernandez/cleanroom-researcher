"""Tests for the runner plumbing of EXP-0001: raw records keyed by a run id, the proof split and what the callers do
with it, the loaded check on every run type, the credential stop, the `--out` and `--from` rules, and the README.

A scripted `claude` stands in for the live service (tests/test_exp0001_score.py); nothing here calls the real CLI,
sets CPR_EVAL_LIVE for a real run, or carries a credential.

Run with: python3 -m unittest discover tests
"""

from __future__ import annotations

import copy
import io
import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import exp0001_paths  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]

import arms  # noqa: E402
import claude_run  # noqa: E402
import fixtures  # noqa: E402
import isolation  # noqa: E402
import judge  # noqa: E402
import pilot  # noqa: E402
import probes  # noqa: E402
import report  # noqa: E402
from tests import test_exp0001_isolation as iso  # noqa: E402
from tests import test_exp0001_pilot as pil  # noqa: E402
from tests import test_exp0001_score as base  # noqa: E402

IDS = base.IDS
README = ROOT / "experiments" / "EXP-0001" / "code" / "README.md"
CONFIG = ROOT / "experiments" / "EXP-0001" / "code" / "config.json"
LIMIT_TEXT = pil.LIMIT_TEXT


def result_of(rec):
    return isolation.result_event(rec["events"])


def init_of(rec):
    return isolation.init_event(rec["events"])


def plain_failure(rec):
    result_of(rec).update(is_error=True, subtype="error_during_execution", result="the model call failed")


def mcp_loaded(rec):
    init_of(rec)["mcp_servers"] = [{"name": "connector"}]


def host_hook(rec):
    rec["events"].insert(1, {"type": "system", "subtype": "hook_started", "hook_event": "Stop", "hook_name": "host",
                             "hook_id": "host-1"})


def asked_the_user(rec):
    rec["events"].insert(1, {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "AskUserQuestion"}]}})


def denied(rec):
    result_of(rec)["permission_denials"] = [{"tool_name": "Bash", "tool_input": {"command": "SECRET-COMMAND"}}]


def throttled_without_init(rec):
    """A throttled run (pil.rate_limited) whose init event is also missing: an isolation failure under a throttle."""
    pil.rate_limited(rec)
    rec["events"][:] = [e for e in rec["events"] if e.get("subtype") != "init"]


def over_cap(rec):
    rec["over_cap"] = True


def new_version(rec):
    init_of(rec)["claude_code_version"] = "9.9.9"


def new_model(rec):
    init_of(rec)["model"] = "another-model"


def stray_plugin(rec):
    init_of(rec)["plugins"].append({"name": "cpr", "path": "/elsewhere", "source": "cpr@inline"})


class Scripted(base.Env):
    """The scripted claude plus a runner that edits the Nth finished record, as the pilot tests do."""

    def setUp(self):
        super().setUp()
        self.script = {}
        self.launched = 0

    def runner(self, cfg, prompt, sandbox, **kw):
        rec = claude_run.run_claude(cfg, prompt, sandbox, **kw)
        self.launched += 1
        edit = self.script.get(self.launched)
        if edit:
            edit(rec)
        return rec

    def run_json(self, out=None):
        return arms.read_json(Path(out or self.out) / "run.json")

    def go(self, arm_names=("floor", "baseline"), ids=IDS[:1], seeds=1, out=None, **kw):
        stdout = io.StringIO()
        try:
            return probes.run_batch(base.FIXTURES, out or self.out, "dev", list(ids), list(arm_names), seeds, self.cfg,
                                    self.environ, runner=self.runner, date="20260101", stdout=stdout, **kw)
        finally:
            self.last_output = stdout.getvalue()

    def preserve_calls(self):
        return [c for c in self.calls() if c["argv"][1] == "/cpr:preserve quick"]

    def raw_files(self, out=None):
        raw = arms.raw_dir(self.run_json(out)["raw_run_id"], self.environ)
        return sorted(p for p in raw.iterdir() if p.name != arms.OWNER_FILE)


# --------------------------------------------------------------------------
# Item 1: raw records keyed by a unique run id
# --------------------------------------------------------------------------

class RawKeyTests(Scripted):
    def test_the_run_id_has_the_utc_stamp_and_eight_hex_characters(self):
        for _ in range(3):
            self.assertRegex(arms.new_run_id(), r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
        self.assertNotEqual(arms.new_run_id(), arms.new_run_id())

    def test_two_out_folders_with_the_same_basename_get_two_raw_folders_and_neither_writes_into_the_other(self):
        a, b = self.tmp / "one" / "results", self.tmp / "two" / "results"
        self.assertEqual(a.name, b.name)
        self.go(arm_names=("floor",), out=a)
        in_a = self.raw_files(a)
        self.assertTrue(in_a)
        self.go(arm_names=("floor",), out=b)
        id_a, id_b = self.run_json(a)["raw_run_id"], self.run_json(b)["raw_run_id"]
        self.assertNotEqual(id_a, id_b)
        folder_a, folder_b = arms.raw_dir(id_a, self.environ), arms.raw_dir(id_b, self.environ)
        self.assertNotEqual(folder_a, folder_b)
        self.assertEqual(self.raw_files(a), in_a)  # the second run left the first run's folder as it was
        self.assertEqual(len(self.raw_files(b)), len(in_a))
        args = ("probe", "floor", IDS[0], 1, self.environ)
        self.assertEqual(arms.raw_path(a, *args).parent, folder_a)
        self.assertEqual(arms.raw_path(b, *args).parent, folder_b)
        self.assertNotIn(a.name, [p.name for p in (folder_a, folder_b)])

    def test_a_raw_folder_that_already_exists_refuses_and_nothing_is_launched(self):
        taken = arms.new_run_id()
        arms.create_raw_folder(taken, self.environ)
        with self.assertRaises(arms.RawError):
            arms.create_raw_folder(taken, self.environ)
        with mock.patch.object(arms, "new_run_id", return_value=taken):
            with self.assertRaises(arms.RawError):
                self.go(arm_names=("floor",))
        self.assertEqual(self.calls(), [])
        self.assertFalse((self.out / "run.json").exists())

    def test_a_raw_record_is_never_overwritten(self):
        self.go(arm_names=("floor",))
        first = self.raw_files()[0]
        before = first.read_text(encoding="utf-8")
        args = (self.out, "probe", "floor", IDS[0], 1, {"events": ["new"]}, self.environ)
        with self.assertRaises(arms.RawError):
            arms.write_raw(*args, attempt=1)
        self.assertEqual(first.read_text(encoding="utf-8"), before)
        again = arms.write_raw(*args)  # no attempt named: the first free one
        self.assertEqual(again.name, first.name[:-len(".json")] + "-a2.json")
        self.assertEqual(first.read_text(encoding="utf-8"), before)

    def test_a_resume_reuses_the_run_id_recorded_in_its_own_run_json(self):
        self.go(arm_names=("floor",), seeds=1)
        first = self.run_json()["raw_run_id"]
        folders = sorted(p.name for p in (claude_run.eval_root(self.environ) / "raw").iterdir())
        self.go(arm_names=("floor",), seeds=2)
        self.assertEqual(self.run_json()["raw_run_id"], first)
        self.assertEqual(sorted(p.name for p in (claude_run.eval_root(self.environ) / "raw").iterdir()), folders)
        self.assertEqual(len(self.raw_files()), 2)  # the seed-2 session was added to the same folder

    def test_raw_records_never_sit_in_the_results_directory_or_under_a_path_part_named_raw_with_an_underscore(self):
        self.go(arm_names=("floor",))
        for path in self.raw_files():
            self.assertNotIn("_raw", path.parts)
            self.assertFalse(str(path).startswith(str(self.out)))
        self.assertNotIn(str(self.tmp), (self.out / "run.json").read_text(encoding="utf-8"))

    def test_a_missing_or_malformed_run_id_refuses(self):
        self.out.mkdir()
        (self.out / "run.json").write_text(json.dumps({"raw_run_id": "results"}), encoding="utf-8")
        with self.assertRaises(arms.RawError):
            arms.raw_path(self.out, "probe", "floor", "f", 1, self.environ)
        with self.assertRaises(arms.RawError):
            arms.raw_path(self.tmp / "nowhere", "probe", "floor", "f", 1, self.environ)

    def test_raw_dir_refuses_a_missing_or_malformed_run_id_when_it_is_passed_directly(self):
        for bad in ("", None, 5, "results", "a/b", "../x", "..", "20260101T000000Z-abcd1234/../x", "/abs/20260101T000000Z-abcd1234"):
            with self.subTest(run_id=bad):
                with self.assertRaises(arms.RawError):
                    arms.raw_dir(bad, self.environ)
        good = arms.new_run_id()
        self.assertEqual(arms.raw_dir(good, self.environ).name, good)


# --------------------------------------------------------------------------
# Item 2: the proof split
# --------------------------------------------------------------------------

class SplitTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fx = iso.RunFixture(Path(self._tmp.name), kind="preserve")

    def groups(self):
        res = self.fx.check()
        self.assertEqual(sorted(res["isolation_failures"] + res["outcome_failures"]), res["failures"])
        self.assertFalse(set(res["isolation_failures"]) & set(res["outcome_failures"]))
        return res["isolation_failures"], res["outcome_failures"]

    def test_a_good_run_has_two_empty_groups(self):
        self.assertEqual(self.groups(), ([], []))

    def test_each_outcome_check_lands_in_the_outcome_group_alone(self):
        cases = (("result_error", lambda: result_of(self.fx.run).update(is_error=True, subtype="error_during_execution")),
                 ("ask_user", lambda: self.fx.events.insert(1, {"type": "assistant", "message": {"content": [
                     {"type": "tool_use", "name": "AskUserQuestion"}]}})),
                 ("denials", lambda: result_of(self.fx.run).update(permission_denials=[{"tool_name": "Bash"}])),
                 ("over_cap", lambda: self.fx.run.update(over_cap=True)))
        for label, plant in cases:
            with self.subTest(label):
                fx = iso.RunFixture(Path(tempfile.mkdtemp(dir=self._tmp.name)), kind="preserve")
                self.fx = fx
                plant()
                self.assertEqual(self.groups(), ([], [label]))

    def test_over_cap_is_read_from_the_record_and_is_not_an_isolation_failure(self):
        self.fx.run["over_cap"] = False
        self.assertEqual(self.groups(), ([], []))
        self.fx.run["over_cap"] = True
        self.assertEqual(self.groups(), ([], ["over_cap"]))

    def test_load_checks_land_in_the_isolation_group_alone(self):
        init = self.fx.init
        init["mcp_servers"] = [{"name": "x"}]
        self.assertEqual(self.groups(), (["mcp_servers"], []))
        init["mcp_servers"] = []
        self.fx.events.insert(0, {"type": "system", "subtype": "hook_started", "hook_event": "Stop", "hook_id": "z"})
        self.assertIn("host_hook", self.groups()[0])
        self.assertEqual(self.groups()[1], [])
        self.fx.events.pop(0)
        self.fx.rows.append({"type": "user", "message": {"content": iso.MARKER}})
        self.assertEqual(self.groups(), (["markers_found"], []))

    def test_version_and_model_changes_are_isolation_checks_that_the_callers_treat_as_a_stop(self):
        self.fx.init["claude_code_version"] = "0.0.1"
        self.assertEqual(self.groups(), (["version_mismatch"], []))
        self.assertEqual(isolation.PIN_CHANGE_CHECKS, frozenset({"version_mismatch", "model_mismatch"}))
        self.fx.init["claude_code_version"] = iso.VERSION
        self.fx.init["model"] = "other"
        self.assertEqual(self.groups(), (["model_mismatch"], []))

    def test_a_missing_init_event_is_an_isolation_failure(self):
        self.fx.events[:] = [e for e in self.fx.events if e.get("subtype") != "init"]
        self.assertEqual(self.groups(), (["init_missing"], []))

    def test_a_run_that_fails_both_kinds_reports_each_in_its_group(self):
        self.fx.init["mcp_servers"] = [{"name": "x"}]
        result_of(self.fx.run).update(permission_denials=[{"tool_name": "Bash"}])
        self.assertEqual(self.groups(), (["mcp_servers"], ["denials"]))


class CallerTests(Scripted):
    """What the batch does with each kind of failure (README "Exclusions"; Changelog clarification 4)."""

    def cell_counts(self):
        return self.run_json()["proof_counts"]

    def test_a_preserve_run_that_fails_only_outcome_checks_is_scored_and_never_re_run(self):
        for n, (label, edit) in enumerate((("result_error", plain_failure), ("ask_user", asked_the_user),
                                          ("over_cap", over_cap), ("denials", denied))):
            with self.subTest(label):
                out = self.tmp / f"outcome-{n}"
                self.script, self.launched = {1: edit}, 0
                (self.bin / "calls.jsonl").unlink(missing_ok=True)
                self.assertEqual(self.go(out=out), 0)
                self.assertEqual(len(self.preserve_calls()), 1)  # one preserve run: no re-run
                self.assertEqual(len(self.calls()), 3)  # that run and the two probe sessions
                text = arms.arm_text_path(out, "baseline", IDS[0], 1).read_text(encoding="utf-8")
                self.assertIn(base.JUNK, text)  # scored on the CLAUDE.md the run left behind
                self.assertEqual(arms.read_json(arms.preserve_path(out, "baseline", IDS[0], 1))["outcome_failures"], [label])
                meta = self.run_json(out)
                self.assertEqual(meta["proof_counts"], {"isolation_reruns": 0, "excluded_after_rerun": 0,
                                                        "outcome_only_by_check": {label: 1}})
                self.assertTrue(arms.probe_path(out, "baseline", IDS[0], 1).is_file())

    def test_a_denial_is_logged_for_review_with_the_cell_and_the_tool_and_nothing_else_is(self):
        self.script = {1: denied}
        self.go()
        log = arms.read_json(self.out / "isolation-review.json")
        self.assertEqual(log, [{"cell": {"fixture": IDS[0], "seed": 1, "arm": "baseline"}, "denial": "Bash"}])
        self.assertNotIn("SECRET-COMMAND", (self.out / "isolation-review.json").read_text(encoding="utf-8"))
        out = self.tmp / "other"
        self.script, self.launched = {1: plain_failure}, 0
        self.go(out=out)
        self.assertFalse((out / "isolation-review.json").exists())

    def test_an_isolation_failure_is_re_run_once_and_a_pass_keeps_the_cell(self):
        self.script = {1: mcp_loaded}
        self.assertEqual(self.go(), 0)
        self.assertEqual(len(self.preserve_calls()), 2)
        meta = self.run_json()
        self.assertEqual(meta["isolation_reruns"], [{"kind": "preserve", "arm": "baseline", "fixture": IDS[0], "seed": 1,
                                                     "checks": ["mcp_servers"]}])
        self.assertEqual((meta["excluded_cells"], meta["outcome_only"]), ([], []))
        self.assertEqual(self.cell_counts(), {"isolation_reruns": 1, "excluded_after_rerun": 0, "outcome_only_by_check": {}})
        self.assertTrue(arms.preserve_path(self.out, "baseline", IDS[0], 1).is_file())
        names = [p.name for p in self.raw_files()]
        self.assertIn(f"preserve-baseline-{IDS[0]}-s1.json", names)
        self.assertIn(f"preserve-baseline-{IDS[0]}-s1-a2.json", names)  # both attempts are kept

    def test_a_second_isolation_failure_excludes_the_cell_which_is_listed_and_never_run_again(self):
        self.script = {1: mcp_loaded, 2: host_hook}
        self.assertEqual(self.go(), 0)
        self.assertEqual(len(self.preserve_calls()), 2)
        meta = self.run_json()
        self.assertEqual(len(meta["isolation_reruns"]), 1)
        self.assertEqual([{k: v for k, v in e.items() if k != "checks"} for e in meta["excluded_cells"]],
                         [{"kind": "preserve", "arm": "baseline", "fixture": IDS[0], "seed": 1}])
        self.assertIn("host_hook", meta["excluded_cells"][0]["checks"])  # the second attempt's failure, not the first's
        self.assertEqual(self.cell_counts(), {"isolation_reruns": 1, "excluded_after_rerun": 1, "outcome_only_by_check": {}})
        self.assertFalse(arms.preserve_path(self.out, "baseline", IDS[0], 1).exists())
        self.assertFalse(arms.probe_path(self.out, "baseline", IDS[0], 1).exists())  # no CLAUDE.md, so no session
        self.assertTrue(arms.probe_path(self.out, "floor", IDS[0], 1).is_file())  # the other arm goes on
        n = len(self.calls())
        self.assertEqual(self.go(), 0)  # a resume does not run the excluded cell again
        self.assertEqual(len(self.calls()), n)
        self.assertEqual(self.run_json()["excluded_cells"], meta["excluded_cells"])

    def test_a_resume_does_not_run_a_probe_cell_that_was_excluded(self):
        self.script = {2: mcp_loaded, 3: mcp_loaded}  # the floor's probe session fails isolation twice
        self.assertEqual(self.go(), 0)
        meta = self.run_json()
        self.assertEqual([(e["kind"], e["arm"]) for e in meta["excluded_cells"]], [("probe", "floor")])
        floor_calls = lambda: [c for c in self.calls() if "floor" in json.dumps(c)]
        n, n_floor = len(self.calls()), len(floor_calls())
        self.assertEqual(self.go(), 0)
        self.assertEqual((len(self.calls()), len(floor_calls())), (n, n_floor))  # the excluded cell is not launched again
        self.assertEqual(self.run_json()["excluded_cells"], meta["excluded_cells"])
        self.assertFalse(arms.probe_path(self.out, "floor", IDS[0], 1).exists())

    def test_a_throttled_run_that_also_fails_an_isolation_check_is_re_run_not_a_batch_stop(self):
        # README "Exclusions": init_missing is an isolation check, so a throttle does not turn it into a batch stop.
        self.script = {1: throttled_without_init}
        self.assertEqual(self.go(), 0, self.last_output)
        self.assertEqual(len(self.preserve_calls()), 2)  # one re-run
        meta = self.run_json()
        self.assertEqual([(e["kind"], e["arm"], e["checks"]) for e in meta["isolation_reruns"]],
                         [("preserve", "baseline", ["init_missing"])])
        self.assertEqual(meta["excluded_cells"], [])
        self.assertTrue(arms.preserve_path(self.out, "baseline", IDS[0], 1).is_file())

    def test_a_second_throttled_run_that_fails_isolation_excludes_the_cell(self):
        self.script = {1: throttled_without_init, 2: throttled_without_init}
        self.assertEqual(self.go(), 0, self.last_output)
        self.assertEqual(len(self.preserve_calls()), 2)
        meta = self.run_json()
        self.assertEqual(len(meta["isolation_reruns"]), 1)
        self.assertEqual([(e["kind"], e["arm"], e["checks"]) for e in meta["excluded_cells"]],
                         [("preserve", "baseline", ["init_missing"])])
        self.assertFalse(arms.preserve_path(self.out, "baseline", IDS[0], 1).exists())

    def test_a_probe_session_that_fails_isolation_re_runs_only_that_session(self):
        self.script = {2: mcp_loaded}  # call 1 is the preserve run; call 2 is the floor's probe session
        self.assertEqual(self.go(), 0)
        self.assertEqual(len(self.preserve_calls()), 1)
        self.assertEqual(len(self.calls()), 4)
        kept = arms.preserve_path(self.out, "baseline", IDS[0], 1).read_bytes()
        meta = self.run_json()
        self.assertEqual([(e["kind"], e["arm"]) for e in meta["isolation_reruns"]], [("probe", "floor")])
        self.assertTrue(arms.probe_path(self.out, "floor", IDS[0], 1).is_file())
        self.assertEqual(arms.preserve_path(self.out, "baseline", IDS[0], 1).read_bytes(), kept)

    def test_a_probe_session_that_fails_isolation_twice_is_excluded_and_the_preserve_output_stays(self):
        self.script = {2: mcp_loaded, 3: mcp_loaded}
        self.assertEqual(self.go(), 0)
        meta = self.run_json()
        self.assertEqual([(e["kind"], e["arm"]) for e in meta["excluded_cells"]], [("probe", "floor")])
        self.assertFalse(arms.probe_path(self.out, "floor", IDS[0], 1).exists())
        self.assertTrue(arms.preserve_path(self.out, "baseline", IDS[0], 1).is_file())
        self.assertEqual(len(self.preserve_calls()), 1)

    def test_a_probe_session_that_fails_only_outcome_checks_is_not_re_run_and_its_answers_are_missing(self):
        self.script = {2: plain_failure}
        self.assertEqual(self.go(), 0)
        self.assertEqual(len(self.calls()), 3)
        data = arms.read_json(arms.probe_path(self.out, "floor", IDS[0], 1))
        self.assertEqual((data["ok"], data["answers"], data["outcome_failures"]), (True, {}, ["result_error"]))
        self.assertEqual(self.cell_counts()["outcome_only_by_check"], {"result_error": 1})

    def test_a_version_or_model_change_stops_the_experiment_with_no_re_run(self):
        for edit in (new_version, new_model):
            with self.subTest(edit.__name__):
                out = self.tmp / edit.__name__
                self.script, self.launched = {1: edit}, 0
                (self.bin / "calls.jsonl").unlink(missing_ok=True)
                with self.assertRaises(probes.PinStop):
                    self.go(out=out)
                self.assertEqual(len(self.calls()), 1)
                self.assertEqual(self.run_json(out)["isolation_reruns"], [])
                self.assertFalse(arms.preserve_path(out, "baseline", IDS[0], 1).exists())
        self.assertTrue(issubclass(probes.PinStop, probes.BatchStop))

    def test_a_throttled_unit_still_stops_the_batch_for_the_pilots_one_wait(self):
        self.script = {1: pil.rate_limited}
        with self.assertRaises(probes.BatchStop) as caught:
            self.go()
        self.assertEqual(pilot.failed_unit_labels(str(caught.exception)), ["result_error"])
        meta = self.run_json()
        self.assertEqual((meta["isolation_reruns"], meta["outcome_only"]), ([], []))

    def test_a_resume_after_a_stop_keeps_the_counts_and_adds_to_them(self):
        self.script = {1: mcp_loaded}
        self.go(ids=IDS[:1], seeds=1)
        self.script, self.launched = {1: plain_failure}, 0
        (self.bin / "calls.jsonl").unlink(missing_ok=True)
        self.go(ids=IDS[:1], seeds=2)
        self.assertEqual(self.cell_counts(), {"isolation_reruns": 1, "excluded_after_rerun": 0,
                                              "outcome_only_by_check": {"result_error": 1}})

    def test_the_report_shows_the_counts_by_check(self):
        tree = ROOT / "tests" / "data" / "exp0001-synthetic"
        run, scores = arms.read_json(tree / "run.json"), arms.read_json(tree / "scores.json")
        self.assertNotIn("Per-run proof counts", report.build_markdown(run, scores))
        run = dict(run, proof_counts={"isolation_reruns": 2, "excluded_after_rerun": 1,
                                      "outcome_only_by_check": {"denials": 1, "result_error": 2}},
                   excluded_cells=[{"kind": "preserve", "arm": "baseline", "fixture": "fx-1", "seed": 2,
                                    "checks": ["host_hook"]}])
        text = report.build_markdown(run, scores)
        self.assertIn("Isolation re-runs: 2. Cells excluded after a re-run: 1. Runs that failed only outcome checks: 3 "
                      "(denials: 1, result_error: 2).", text)
        self.assertIn("- Excluded: fx-1, baseline, seed 2, preserve run; failed host_hook.", text)


# --------------------------------------------------------------------------
# Item 3: the per-run proof on every run type
# --------------------------------------------------------------------------

class EveryRunTypeTests(Scripted):
    """Generation turns, preserve runs, probe sessions and judge calls each run the proof and each fail on a planted
    load violation. A run that loads no plugin shows that no plugin loaded."""

    def setUp(self):
        super().setUp()
        self.markers = isolation.host_markers(self.environ)
        self.spec = base.SPECS[IDS[0]]
        self.runner = lambda cfg, prompt, sandbox, **kw: claude_run.run_claude(cfg, prompt, sandbox, **kw)
        self.cfg["judge"] = dict(self.cfg["judge"], model=self.cfg["model"])  # the scripted claude reports one model
        real = claude_run.run_claude

        def with_claude_md(cfg, prompt, sandbox, **kw):
            # every run type reaches the scripted claude through here, the judge's included
            if not (Path(sandbox) / "CLAUDE.md").exists():
                (Path(sandbox) / "CLAUDE.md").write_text("# Judge\n", encoding="utf-8")
            rec = real(cfg, prompt, sandbox, **kw)
            self.launched += 1
            if self.script.get(self.launched):
                self.script[self.launched](rec)
            return rec
        patch = mock.patch.object(claude_run, "run_claude", with_claude_md)
        patch.start()
        self.addCleanup(patch.stop)

    def preserve(self):
        res = arms.preserve_run(self.spec, base.FIXTURES / IDS[0], "baseline", self.cfg, self.environ, self.runner,
                                self.export, self.markers)
        return res["record"]["proof"]["failures"]

    def probe(self):
        res = probes.run_probe_session(self.spec, "# Floor\n", self.cfg, self.environ, self.runner, self.markers, 1, "probe-floor")
        return res["record"]["proof"]["failures"]

    def generation(self):
        workdir = self.tmp / f"gen-{self.launched}-{len(list(self.tmp.iterdir()))}"
        workdir.mkdir()
        res = fixtures._attempt(self.spec, self.cfg, self.environ, self.runner, workdir, self.markers)
        labels = [x for x in res["labels"] if x.startswith("turn 1 proof: ")]
        return labels[0][len("turn 1 proof: "):].split(",") if labels else []

    def judge_call(self):
        rec = judge.run_judge_call(self.cfg, "Judge this.", "judge-probe.schema.json", self.environ, phase="evaluation",
                                   counter="score", max_calls=50)
        return rec["proof"]["failures"]

    def runs(self):
        return (("generation", self.generation), ("preserve", self.preserve), ("probe", self.probe), ("judge", self.judge_call))

    def plant(self, edit):
        self.launched = 0
        self.script = {1: edit}

    def test_a_clean_run_of_each_type_passes_the_proof(self):
        for name, run in self.runs():
            with self.subTest(name):
                self.plant(lambda rec: None)
                self.assertEqual(run(), [])

    def test_a_marker_in_the_transcript_fails_each_run_type(self):
        for name, run in self.runs():
            with self.subTest(name):
                self.plan["leak"] = base.HOST_MARKER
                self.write_plan()
                self.plant(lambda rec: None)
                self.assertIn("markers_found", run())
        self.plan.pop("leak")
        self.write_plan()

    def test_a_host_hook_fails_each_run_type(self):
        for name, run in self.runs():
            with self.subTest(name):
                self.plant(host_hook)
                self.assertIn("host_hook", run())

    def test_an_mcp_server_fails_each_run_type(self):
        for name, run in self.runs():
            with self.subTest(name):
                self.plant(mcp_loaded)
                self.assertIn("mcp_servers", run())

    def test_a_plugin_in_a_run_that_loads_none_fails_and_a_run_without_one_shows_none(self):
        for name, run in self.runs():
            if name == "preserve":
                continue
            with self.subTest(name):
                self.plant(stray_plugin)
                self.assertIn("plugin_unexpected", run())
                self.plant(lambda rec: self.assertEqual(
                    [p["name"] for p in init_of(rec)["plugins"] if p["name"] == "cpr"], []))
                self.assertEqual(run(), [])

    def test_a_preserve_run_that_loads_the_plugin_must_load_it_from_the_export(self):
        def lost(rec):
            init_of(rec)["plugins"] = [p for p in init_of(rec)["plugins"] if p["name"] != "cpr"]
        self.plant(lost)
        self.assertIn("plugin_cpr_count", self.preserve())
        self.plant(lambda rec: init_of(rec)["plugins"].append({"name": "cpr", "path": "/elsewhere"}))
        self.assertIn("plugin_cpr_count", self.preserve())


# --------------------------------------------------------------------------
# Item 13: the credential stop
# --------------------------------------------------------------------------

class CredentialStopTests(Scripted):
    REFUSAL = "needs the repository owner's ruling with a security review first"

    def tempconfig_cfg(self):
        return dict(self.cfg, isolation="tempconfig")

    def test_the_child_environment_never_holds_a_credential_whatever_the_parent_has(self):
        parent = dict(self.environ, CLAUDE_CODE_OAUTH_TOKEN="parent-token-value", ANTHROPIC_API_KEY="parent-key-value")
        for cfg in (self.cfg, self.tempconfig_cfg()):
            with self.subTest(cfg["isolation"]):
                env = claude_run.child_env(cfg, "r1", parent)
                for name in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"):
                    self.assertNotIn(name, env)
                self.assertNotIn("parent-token-value", json.dumps(env))
                self.assertNotIn("parent-key-value", json.dumps(env))
                self.assertFalse(set(claude_run.AUTH_VARS) & set(env))

    def test_a_real_launch_records_an_environment_without_the_variables(self):
        parent = dict(self.environ, CLAUDE_CODE_OAUTH_TOKEN="parent-token-value", ANTHROPIC_API_KEY="parent-key-value")
        sandbox = claude_run.make_sandbox("probe", parent)
        (sandbox / "CLAUDE.md").write_text("# T\n", encoding="utf-8")
        rec = claude_run.run_claude(self.cfg, "hello", sandbox, label="x", phase="evaluation", kind="probe", probe=True,
                                    environ=parent)
        self.assertFalse(set(claude_run.AUTH_VARS) & set(rec["env_names"]))

    def test_tempconfig_is_refused_by_the_library(self):
        with self.assertRaises(claude_run.ConfigError) as caught:
            claude_run.validate_config(self.tempconfig_cfg())
        self.assertIn(self.REFUSAL, str(caught.exception))
        self.assertIn(self.REFUSAL, claude_run.preflight(self.tempconfig_cfg(), self.environ))
        sandbox = claude_run.make_sandbox("probe", self.environ)
        with self.assertRaises(claude_run.ConfigError):
            claude_run.run_claude(self.tempconfig_cfg(), "hello", sandbox, label="x", phase="evaluation", environ=self.environ)
        self.assertEqual(self.calls(), [])

    def test_every_command_refuses_a_tempconfig_config(self):
        import run
        a_dir = str(self.tmp / "somewhere")
        argvs = (["preflight"], ["control"], ["judge-check"], ["validate"], ["gen-fixture", "dev-001"],
                 ["install-check", "dev-001"], ["install-verify", "dev-001"], ["spend-rebuild"],
                 ["run", "--out", a_dir], ["score", "--from", a_dir], ["report", "--from", a_dir],
                 ["pilot", "--budget-usd", "1", "--out", a_dir], ["pilot-md", "--from", a_dir])
        with mock.patch.object(claude_run, "load_config", return_value=self.tempconfig_cfg()):
            for argv in argvs:
                with self.subTest(argv=argv):
                    with mock.patch("sys.stdout", new=io.StringIO()) as shown:
                        self.assertEqual(run.main(argv), 2)
                    self.assertTrue(shown.getvalue().startswith(f"{argv[0]}: REFUSED "), shown.getvalue())
                    self.assertIn(self.REFUSAL, shown.getvalue())
        self.assertEqual(self.calls(), [])
        self.assertFalse(Path(a_dir).exists())

    def test_the_shipped_config_names_the_sources_mode_and_the_pinned_plugin(self):
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(cfg["isolation"], "sources")
        self.assertEqual(cfg["plugin_ref"], "cfd1e77e07d2b3698591688e7e151d83d3ef51c3")


# --------------------------------------------------------------------------
# Items 14 and 15: --out in the library entry points, --from writes
# --------------------------------------------------------------------------

class OutInTheLibraryTests(iso.CloneFolders):
    def setUp(self):
        super().setUp()
        patch = mock.patch.object(claude_run, "ROOT", self.clone)
        patch.start()
        self.addCleanup(patch.stop)

    def call(self, command, out):
        shown = io.StringIO()
        if command == "run":
            code = probes.command_run(base.FIXTURES, out, "dev", "dev-001", ["floor"], 1, None, "20260101",
                                      {"CPR_EVAL_LIVE": "1", "TMPDIR": str(self.tmp)}, shown)
        else:
            code = pilot.command_pilot(base.FIXTURES, out, "dev", "dev-001", ["floor"], 1, 1.0, "20260101",
                                       {"CPR_EVAL_LIVE": "1", "TMPDIR": str(self.tmp)}, shown)
        return code, shown.getvalue()

    def refuses(self, path):
        for command in ("run", "pilot"):
            code, text = self.call(command, path)
            self.assertEqual(code, 2, (command, text))
            self.assertTrue(text.startswith(f"{command}: REFUSED --out is inside the repository clone"), text)

    def test_a_missing_out_refuses(self):
        for command in ("run", "pilot"):
            code, text = self.call(command, None)
            self.assertEqual((code, text.startswith(f"{command}: REFUSED --out is required")), (2, True), text)

    def test_an_out_inside_the_clone_outside_recorded_refuses(self):
        self.refuses(self.clone / "other" / "run-1")

    def test_a_case_variant_refuses(self):
        variant = self.form_case_variant(self.clone / "other")
        if self.case_sensitive(self.clone / "other"):
            # Case-sensitive file system: the variant is a missing place outside the clone,
            # so it gets the verdict of a plainly outside, missing control path.
            control = self.outside_control()
            self.assertFalse(variant.exists())
            for command in ("run", "pilot"):
                code, text = self.call(command, variant)
                control_code, control_text = self.call(command, control)
                self.assertEqual((code, "REFUSED" in text), (control_code, "REFUSED" in control_text))
                self.assertNotEqual(control_code, 2, control_text)
                self.assertNotIn("REFUSED", control_text)
            return
        self.refuses(variant)

    def test_a_symlinked_ancestor_refuses(self):
        self.refuses(self.form_symlinked_ancestor(self.clone / "other"))

    def test_a_dot_dot_path_refuses(self):
        self.refuses(self.form_dot_dot(self.clone / "other"))

    def test_a_value_that_is_itself_a_symlink_refuses(self):
        self.refuses(self.form_value_is_a_symlink(self.clone / "other"))

    def test_an_out_outside_the_clone_passes_the_rule(self):
        for command in ("run", "pilot"):
            code, text = self.call(command, self.outside / "run-1")
            self.assertNotIn("REFUSED", text)
            self.assertNotEqual(code, 2, text)  # it goes on to the live gate, which stops it without a launch
            self.assertIn("pilot" if command == "pilot" else "run", text)


class FromInTheCommandLineTests(iso.CloneFolders):
    def setUp(self):
        super().setUp()
        patch = mock.patch.object(claude_run, "ROOT", self.clone)
        patch.start()
        self.addCleanup(patch.stop)

    def main(self, command, value):
        import run
        with mock.patch("sys.stdout", new=io.StringIO()) as shown:
            code = run.main([command, "--from", str(value)])
        return code, shown.getvalue()

    def refuses(self, value):
        for command in ("score", "report", "pilot-md"):
            with self.subTest(command=command):
                code, text = self.main(command, value)
                self.assertEqual(code, 2, text)
                self.assertTrue(text.startswith(f"{command}: REFUSED --from is inside the repository clone"), text)

    def test_a_from_inside_the_clone_outside_recorded_refuses(self):
        self.refuses(self.clone / "other" / "run-1")
        self.refuses(self.clone)

    def test_a_case_variant_refuses(self):
        variant = self.form_case_variant(self.clone / "other")
        if self.case_sensitive(self.clone / "other"):
            control = self.outside_control()
            self.assertFalse(variant.exists())
            for command in ("score", "report", "pilot-md"):
                with self.subTest(command=command):
                    code, text = self.main(command, variant)
                    control_code, control_text = self.main(command, control)
                    self.assertEqual((code, "REFUSED" in text), (control_code, "REFUSED" in control_text))
                    self.assertNotIn("is inside the repository clone", control_text)
                    self.assertFalse(control_text.startswith(f"{command}: REFUSED --from is inside"), control_text)
            return
        self.refuses(variant)

    def test_a_symlinked_ancestor_refuses(self):
        self.refuses(self.form_symlinked_ancestor(self.clone / "other"))

    def test_a_dot_dot_path_refuses(self):
        self.refuses(self.form_dot_dot(self.clone / "other"))

    def test_a_value_that_is_itself_a_symlink_refuses(self):
        self.refuses(self.form_value_is_a_symlink(self.clone / "other"))

    def test_a_from_under_recorded_or_outside_the_clone_passes_the_rule(self):
        gated = Path(tempfile.mkdtemp())  # score needs the C6 gate open to get past the gate to its own stop
        self.addCleanup(shutil.rmtree, gated, True)
        (gated / "cpr-eval").mkdir()
        (gated / "cpr-eval" / "gates.json").write_text(
            json.dumps({"C6": {"outcome": "PASS", "token": "0" * 64, "started_utc": "2026-10-23T00:00:00Z"}}),
            encoding="utf-8")
        patch = mock.patch.dict(os.environ, {"TMPDIR": str(gated)})
        patch.start()
        self.addCleanup(patch.stop)
        for value in (self.recorded / "run-1", self.outside / "run-1"):
            for command in ("score", "report", "pilot-md"):
                with self.subTest(command=command, value=value.parent.name):
                    code, text = self.main(command, value)
                    self.assertNotIn("REFUSED", text)
                    self.assertEqual(code, 1, text)  # the folder holds no results: an ordinary stop, nothing written
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_the_clone_stat_walk_is_the_one_out_uses(self):
        self.assertIn("--from is inside the repository clone", claude_run.from_refusal(self.clone / "other"))
        self.assertEqual(claude_run.from_refusal(self.recorded / "x"), "")


# --------------------------------------------------------------------------
# Items 16 to 18: README, the pilot's stops
# --------------------------------------------------------------------------

class TextAndStopTests(unittest.TestCase):
    def test_the_readme_holds_no_committed_results_text_and_no_named_result_sets(self):
        text = README.read_text(encoding="utf-8")
        for needle in ("Committed results", "dev-baseline-1seed", "pilot-20261017", "committed under"):
            self.assertNotIn(needle, text)
        self.assertNotRegex(text, r"results (?:are|were) committed")

    def test_the_readmes_pilot_example_uses_the_evaluation_cap(self):
        text = README.read_text(encoding="utf-8")
        cap = json.loads(CONFIG.read_text(encoding="utf-8"))["phase_caps"]["evaluation"]
        self.assertEqual(cap, 34.0)
        self.assertIn("--budget-usd 34 ", text)
        self.assertNotIn("--budget-usd 30 ", text)

    def test_the_plugin_repo_error_is_a_pilot_stop(self):
        self.assertIn(claude_run.PluginRepoError, pilot.STOPS)

    def test_the_synthetic_fixtures_path_in_the_score_test_exists(self):
        self.assertTrue((base.FIXTURES / IDS[0] / "spec.json").is_file())
        self.assertEqual(base.FIXTURES, ROOT / "tests" / "data" / "exp0001-fixtures" / "dev")


class PluginRepoStopTests(pil.PilotEnv):
    def test_a_plugin_repo_error_mid_pilot_is_an_incomplete_stop_not_a_crash(self):
        def refuse(arm, cfg, environ, cache, arms_dir=None):
            raise claude_run.PluginRepoError("CPR_EVAL_PLUGIN_REPO is not set")
        with mock.patch.object(arms, "plugin_for", refuse):
            self.assertEqual(self.pilot(wanted=IDS[0], seeds=1), 1, self.last_output)
        self.assertEqual(self.state()["status"], "INCOMPLETE")
        self.assertEqual(self.launched, 0)


class ThrottleWithIsolationFailureTests(pil.PilotEnv):
    def test_a_throttled_run_that_fails_init_missing_is_not_waited_for_by_the_pilot(self):
        self.script = {1: throttled_without_init, 2: throttled_without_init}
        self.pilot()
        self.assertEqual(self.sleeps, [])  # no wait: the throttle exception does not cover an isolation failure
        self.assertEqual(self.state()["throttles"], [])
        excluded = arms.read_json(self.out / "run.json")["excluded_cells"]
        self.assertEqual([e["checks"] for e in excluded], [["init_missing"]])
        # 16 launches in a clean pilot; the excluded preserve cell ran twice (+1) and its probe session never ran (-1)
        self.assertEqual(self.launched, 16)
        self.assertIn("init_missing", self.pilot_md())


if __name__ == "__main__":
    unittest.main()
