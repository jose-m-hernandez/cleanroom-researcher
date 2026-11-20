"""Offline tests for three audit rows of EXP-0001: the judge-check gate (row 10), the scoring pass counter (row 31)
and the per-run isolation.json (row 51).

Nothing here calls the real claude: the judge's calls, the preflight and the runner are replaced by fakes.
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

import check_experiments  # noqa: E402
from lib import paths  # noqa: E402
import claude_run  # noqa: E402
import isolation  # noqa: E402
import judge  # noqa: E402
import pilot  # noqa: E402
import probes  # noqa: E402
import report  # noqa: E402
import score  # noqa: E402
import seal  # noqa: E402
from tests import test_exp0001_audit as audit_tests  # noqa: E402
from tests import test_exp0001_plumbing as plumbing  # noqa: E402
from tests import test_exp0001_seal as seal_tests  # noqa: E402

STARTED = "2026-11-18T00:00:00Z"
JC_REFUSAL = "the judge-check has not passed: run `judge-check` first"


def tokened(outcome="PASS"):
    return {"outcome": outcome, "token": os.urandom(16).hex(), "started_utc": STARTED, "run_id": "r-1"}


def tokenless(outcome="PASS"):
    return {"outcome": outcome, "started_utc": STARTED, "run_id": "r-1"}


class TestRow10(unittest.TestCase):
    """Row 10: a judge-check miss ends the experiment before any held-out run."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        (self.tmp / "home").mkdir()
        self.environ = {"HOME": str(self.tmp / "home"), "TMPDIR": str(self.tmp), "CPR_EVAL_LIVE": "1"}
        self.gates = claude_run.eval_root(self.environ) / "gates.json"
        self.gates.parent.mkdir(parents=True)
        self.calls = []
        self.proof_ok = True
        self.wrong = False
        self.raise_in_probe = None
        self.preflight = ""
        for target, fake in (("preflight", lambda cfg, environ: self.preflight),):
            patch = mock.patch.object(claude_run, target, fake)
            patch.start()
            self.addCleanup(patch.stop)
        for target, fake in (("label_probe", self.fake_probe), ("label_items", self.fake_items)):
            patch = mock.patch.object(judge, target, fake)
            patch.start()
            self.addCleanup(patch.stop)

    def fake_probe(self, cfg, case, environ, **kw):
        self.calls.append("probe")
        if self.raise_in_probe:
            raise self.raise_in_probe
        labels = {c["id"]: ("wrong-label" if self.wrong else c["expect"]) for c in judge.PROBE_CASES}
        return labels, {"proof": {"ok": self.proof_ok, "failures": [] if self.proof_ok else ["x"]}}

    def fake_items(self, cfg, case, environ, **kw):
        self.calls.append("items")
        lines = [{"line": n, "kind": k, "item_id": i, "placed_ok": k == "needed", "quote": ""}
                 for n, (k, i) in enumerate(judge.ITEMS_CASE["expect"], start=1)]
        return lines, {"proof": {"ok": True, "failures": []}}

    def check(self):
        out = io.StringIO()
        code = judge.judge_check(self.environ, out)
        return code, out.getvalue()

    def jc(self):
        if not self.gates.exists():
            return None
        return json.loads(self.gates.read_text(encoding="utf-8")).get("JC")

    def plant(self, **entries):
        self.gates.write_text(json.dumps(entries), encoding="utf-8")

    def test_a_pass_records_jc_pass_without_a_token(self):
        code, out = self.check()
        self.assertEqual(code, 0, out)
        entry = self.jc()
        self.assertEqual(entry["outcome"], "PASS")
        self.assertNotIn("token", entry)
        self.assertIsInstance(entry["started_utc"], str)
        self.assertIsInstance(entry["run_id"], str)
        self.assertEqual(seal.read_gates(self.environ)[1], "")

    def test_a_miss_records_jc_fail(self):
        self.wrong = True
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("judge-check: FAIL", out)
        self.assertEqual(self.jc()["outcome"], "FAIL")

    def test_a_stop_or_an_error_records_nothing(self):
        cases = {
            "isolation": lambda: setattr(self, "proof_ok", False),
            "preflight": lambda: setattr(self, "preflight", "no binary"),
            "budget": lambda: setattr(self, "raise_in_probe", judge.JudgeBudgetError("spent")),
            "error": lambda: setattr(self, "raise_in_probe", RuntimeError("boom")),
        }
        for name, arrange in cases.items():
            with self.subTest(case=name):
                self.proof_ok, self.preflight, self.raise_in_probe = True, "", None
                arrange()
                code, out = self.check()
                self.assertEqual(code, 1, out)
                self.assertIsNone(self.jc())

    def test_a_second_judge_check_is_refused_before_any_call(self):
        for outcome in ("PASS", "FAIL"):
            with self.subTest(outcome=outcome):
                self.plant(JC=tokenless(outcome))
                before = self.gates.read_bytes()
                self.calls.clear()
                code, out = self.check()
                self.assertEqual(code, 2, out)
                self.assertIn("judge-check: REFUSED", out)
                self.assertIn("a judge-check is never re-run", out)
                self.assertEqual(self.calls, [])
                self.assertEqual(self.gates.read_bytes(), before)

    def test_score_run_and_gen_fixture_need_a_jc_pass_after_c6(self):
        self.plant(**{"C1-5": tokenless(), "C6": tokened(), "C7": tokened()})
        for command in ("gen-fixture", "run", "score"):
            with self.subTest(command=command):
                self.assertEqual(seal.command_gate(command, self.environ), JC_REFUSAL)
        self.plant(**{"C1-5": tokenless(), "C6": tokened(), "C7": tokened(), "JC": tokenless()})
        for command in ("gen-fixture", "run", "score"):
            with self.subTest(command=command, jc="PASS"):
                self.assertEqual(seal.command_gate(command, self.environ), "")

    def test_the_other_commands_do_not_need_jc(self):
        self.plant(**{"C1-5": tokenless(), "C6": tokened()})
        for command in ("install-check", "pilot", "author", "canary-generation", "judge-check"):
            with self.subTest(command=command):
                self.assertEqual(seal.command_gate(command, self.environ), "")

    def test_the_order_is_c15_then_c6_then_jc_then_c7(self):
        self.plant(**{"C1-5": tokenless()})
        self.assertIn("C6 canary has not passed", seal.command_gate("run", self.environ))
        self.plant(**{"C1-5": tokenless(), "C6": tokened()})
        self.assertEqual(seal.command_gate("gen-fixture", self.environ), JC_REFUSAL)  # before the C7 text
        self.plant(**{"C1-5": tokenless(), "C6": tokened(), "JC": tokenless()})
        self.assertIn("C7 canary has not passed", seal.command_gate("gen-fixture", self.environ))
        self.assertEqual(seal.command_gate("run", self.environ), "")

    def test_a_jc_fail_stops_every_gated_command(self):
        self.plant(**{"C1-5": tokenless(), "C6": tokened(), "C7": tokened(), "JC": tokenless("FAIL")})
        for command in seal.GATED_COMMANDS:
            with self.subTest(command=command):
                self.assertIn("JC did not pass", seal.command_gate(command, self.environ))

    def test_a_jc_entry_that_carries_a_bad_outcome_is_malformed(self):
        self.plant(**{"C1-5": tokenless(), "C6": tokened(), "JC": tokenless("ERROR")})
        self.assertEqual(seal.read_gates(self.environ), (None, "gates.json is malformed"))


TWICE = "score: REFUSED the scoring command has run twice; there is no third pass"
BOTH_ABORTED = "score: STOP both scoring passes aborted (outcome not determined)"


class TestRow31(unittest.TestCase):
    """Row 31: scoring runs exactly twice per mode, never a third pass; both aborted is not determined."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        (self.tmp / "home").mkdir()
        self.environ = {"HOME": str(self.tmp / "home"), "TMPDIR": str(self.tmp), "CPR_EVAL_LIVE": "1"}
        self.out = self.tmp / "results"
        self.out.mkdir()
        (self.out / "run.json").write_text(json.dumps({"split": "dev", "fixtures": [], "arms": []}), encoding="utf-8")
        self.root = self.tmp / "fixtures"
        self.root.mkdir()
        self.scored = []  # the modes of the passes that reached the judge
        self.script = []  # per pass: None (success) or an exception to raise
        self.preflight = ""
        for target, fake in ((claude_run, ("preflight", lambda cfg, environ: self.preflight)),
                             (score, ("score_run", self.fake_score_run)),
                             (score, ("summary_line", lambda scores: "score: ok")),
                             (seal, ("verify_labels", lambda seal_dir, out: "")),
                             (seal, ("final_record_diff", lambda seal_dir, scores: []))):
            patch = mock.patch.object(target, fake[0], fake[1])
            patch.start()
            self.addCleanup(patch.stop)

    def fake_score_run(self, out, specs, cfg, environ, label_probe=None, label_items=None, model=None, max_calls=None,
                       mode="normal"):
        self.scored.append(mode)
        step = self.script.pop(0) if self.script else None
        if step is not None:
            raise step
        return {"fixtures": []}

    def score(self, final=False, environ=None):
        out = io.StringIO()
        code = score.command_score(self.out, self.root, environ or self.environ, out,
                                   seal_dir=(self.tmp / "seal") if final else None)
        return code, out.getvalue().strip()

    def passes(self):
        return json.loads((self.out / "score-passes.json").read_text(encoding="utf-8"))

    def test_a_third_start_in_one_mode_is_refused_before_any_call(self):
        self.assertEqual(self.score()[0], 0)
        self.assertEqual(self.score()[0], 0)
        code, text = self.score()
        self.assertEqual((code, text), (2, TWICE))
        self.assertEqual(self.scored, ["normal", "normal"])
        self.assertEqual(self.passes()["normal"], {"started": 2, "aborted": 0})

    def test_the_two_modes_count_apart(self):
        for final in (False, True, False, True):
            self.assertEqual(self.score(final=final)[0], 0)
        self.assertEqual(self.score(final=True), (2, TWICE))
        self.assertEqual(self.score(final=False), (2, TWICE))
        self.assertEqual(self.scored, ["normal", "final", "normal", "final"])
        self.assertEqual(self.passes(), {"final": {"started": 2, "aborted": 0}, "normal": {"started": 2, "aborted": 0}})

    def test_every_abort_kind_counts_and_two_aborts_end_the_scoring(self):
        kinds = (score.ScoreAbort("judge isolation failed: x"), judge.JudgeBudgetError("budget"))
        self.script = list(kinds)
        for _ in kinds:
            self.assertEqual(self.score()[0], 1)
        self.assertEqual(self.passes()["normal"], {"started": 2, "aborted": 2})
        self.assertEqual(self.score(), (2, BOTH_ABORTED))
        self.assertEqual(len(self.scored), 2)  # the third call reached no judge

    def test_one_abort_then_one_success_is_a_finished_scoring(self):
        self.script = [score.ScoreAbort("labels file unreadable")]
        self.assertEqual(self.score()[0], 1)
        self.assertEqual(self.score()[0], 0)
        self.assertEqual(self.passes()["normal"], {"started": 2, "aborted": 1})
        self.assertEqual(self.score(), (2, TWICE))

    def test_a_refused_call_is_not_a_pass(self):
        live_off = dict(self.environ)
        del live_off["CPR_EVAL_LIVE"]
        for _ in range(3):
            self.assertEqual(self.score(environ=live_off)[0], 2)
        self.preflight = "no binary"
        self.assertEqual(self.score()[0], 1)
        (self.out / "run.json").unlink()
        self.preflight = ""
        self.assertEqual(self.score(), (1, "score: STOP run.json not found"))
        self.assertFalse((self.out / "score-passes.json").exists())
        self.assertEqual(self.scored, [])

    def test_an_unreadable_counter_stops_before_any_call(self):
        (self.out / "score-passes.json").write_text("{oops", encoding="utf-8")
        code, text = self.score()
        self.assertEqual(code, 1)
        self.assertIn("score-passes.json", text)
        self.assertEqual(self.scored, [])

    def test_report_after_two_aborted_passes_is_not_a_report(self):
        """Evidence that report needs no change: score writes scores.json on success only, and report refuses without it."""
        self.script = [score.ScoreAbort("a"), score.ScoreAbort("b")]
        self.score()
        self.score()
        self.assertEqual(self.score(), (2, BOTH_ABORTED))
        self.assertFalse((self.out / "scores.json").exists())
        shown = io.StringIO()
        code = report.write_report(str(self.out), stdout=shown)
        self.assertEqual(code, 1)
        self.assertIn("report: STOP run.json or scores.json missing or unreadable", shown.getvalue())
        self.assertFalse((self.out / "REPORT.md").exists())


SANITY = "score: STOP the judge sanity controls failed (outcome not determined)"


class TestRow28(unittest.TestCase):
    """Row 28 (P:110 item 4, P:139): a failed judge sanity control is terminal: not determined, no further pass."""

    setUp = TestRow31.setUp
    fake_score_run = TestRow31.fake_score_run
    score = TestRow31.score
    passes = TestRow31.passes

    def failed_control(self):
        return score.SanityAbort("judge sanity controls failed: oracle recall 0.500 (min 0.95), mismatch recall 0.000 "
                                 "(max 0.05)")

    def test_a_failed_control_ends_the_scoring_and_refuses_a_second_pass(self):
        self.script = [self.failed_control()]
        code, text = self.score()
        self.assertEqual(code, 1)
        self.assertTrue(text.startswith("score: ABORT judge sanity controls failed: oracle recall 0.500"), text)
        self.assertTrue(text.endswith("(outcome not determined)"), text)
        self.assertEqual(self.scored, ["normal"])
        code, text = self.score()
        self.assertEqual((code, text), (2, SANITY))
        self.assertEqual(self.scored, ["normal"])  # the refused call reached no judge
        self.assertEqual(self.passes()["normal"], {"started": 1, "aborted": 1, "terminal": True})

    def test_report_after_a_failed_control_writes_nothing(self):
        self.script = [self.failed_control()]
        self.score()
        self.assertFalse((self.out / "scores.json").exists())
        shown = io.StringIO()
        self.assertEqual(report.write_report(str(self.out), stdout=shown), 1)
        self.assertIn("report: STOP", shown.getvalue())
        self.assertFalse((self.out / "REPORT.md").exists())

    def test_report_refuses_the_first_passes_file_when_the_second_pass_fails_a_control(self):
        self.assertEqual(self.score()[0], 0)
        self.script = [self.failed_control()]
        self.assertEqual(self.score()[0], 1)
        self.assertTrue((self.out / "scores.json").is_file())  # pass 1 left it; the experiment is still not determined
        shown = io.StringIO()
        self.assertEqual(report.write_report(str(self.out), stdout=shown), 1)
        self.assertIn("the judge sanity controls failed (outcome not determined)", shown.getvalue())
        self.assertFalse((self.out / "REPORT.md").exists())

    def test_positive_control_passing_controls_allow_a_second_pass(self):
        self.assertEqual(self.score()[0], 0)
        self.assertEqual(self.score()[0], 0)
        self.assertEqual(self.scored, ["normal", "normal"])
        self.assertEqual(self.passes()["normal"], {"started": 2, "aborted": 0})
        self.assertEqual(self.score(), (2, TWICE))  # only the third is refused, and for the other reason

    def test_a_failed_control_in_the_final_mode_does_not_end_the_normal_mode(self):
        """The pass counter counts the modes apart (P:130); P:110 does not tie them, so the counter stays per mode."""
        self.script = [self.failed_control()]
        self.assertEqual(self.score(final=True)[0], 1)
        self.assertEqual(self.score(final=True), (2, SANITY))
        self.assertEqual(self.score(final=False)[0], 0)
        self.assertEqual(self.scored, ["final", "normal"])

    def test_another_abort_is_not_terminal(self):
        self.script = [score.ScoreAbort("judge isolation failed: x")]
        self.assertEqual(self.score()[0], 1)
        self.assertEqual(self.score()[0], 0)
        self.assertNotIn("terminal", self.passes()["normal"])

    def test_a_counter_with_a_bad_terminal_value_is_unreadable(self):
        (self.out / "score-passes.json").write_text(json.dumps(
            {"normal": {"started": 1, "aborted": 1, "terminal": "yes"}, "final": {"started": 0, "aborted": 0}}),
            encoding="utf-8")
        code, text = self.score()
        self.assertEqual(code, 1)
        self.assertIn("score-passes.json", text)
        self.assertEqual(self.scored, [])


STALE = "report: STOP scores.json is not from the last completed final scoring pass; run score again"


class TestStaleScores(unittest.TestCase):
    """A scores.json left by an earlier pass is never read as the result of a later one (final report)."""

    score = TestRow31.score
    passes = TestRow31.passes

    def fake_score_run(self, out, specs, cfg, environ, label_probe=None, label_items=None, model=None, max_calls=None,
                       mode="normal"):
        self.scored.append(mode)
        step = self.script.pop(0) if self.script else None
        if step is not None:
            raise step
        return {"fixtures": [], "labels": {}, "multi_value": {}, "mode_scored": mode, "arms": [], "marker": len(self.scored)}

    def setUp(self):
        TestRow31.setUp(self)
        (self.out / "run.json").write_text(json.dumps({"split": "heldout", "fixtures": ["heldout-001-x"],
                                                       "arms": ["baseline"]}), encoding="utf-8")
        self.assertTrue(seal.is_final(json.loads((self.out / "run.json").read_text(encoding="utf-8"))))
        for target, fake in ((score, ("load_specs", lambda fixtures_root, ids: {})),
                             (report, ("build_markdown", lambda run, scores, final=None: "# report\n")),
                             (report, ("load_final", lambda seal_dir, audit_path: {}))):
            patch = mock.patch.object(target, fake[0], fake[1])
            patch.start()
            self.addCleanup(patch.stop)

    def run_report(self):
        shown = io.StringIO()
        code = report.write_report(str(self.out), stdout=shown, seal_dir=str(self.tmp / "seal"),
                                   audit_path=str(self.tmp / "audit.json"))
        return code, shown.getvalue().strip()

    def test_a_normal_scores_file_is_not_the_result_after_two_aborted_final_passes(self):
        self.assertEqual(self.score(final=False)[0], 0)
        self.script = [score.ScoreAbort("a"), score.ScoreAbort("b")]
        self.assertEqual(self.score(final=True)[0], 1)
        self.assertEqual(self.score(final=True)[0], 1)
        self.assertEqual(self.score(final=True), (2, BOTH_ABORTED))
        self.assertTrue((self.out / "scores.json").is_file())  # the normal pass's file is still there
        code, text = self.run_report()
        self.assertEqual((code, text), (1, STALE))
        self.assertFalse((self.out / "REPORT.md").exists())
        self.assertNotIn("marker", text)

    def test_the_first_final_pass_stands_when_only_the_second_pass_aborts(self):
        self.assertEqual(self.score(final=True)[0], 0)
        self.script = [score.ScoreAbort("a")]
        self.assertEqual(self.score(final=True)[0], 1)
        self.assertEqual(self.run_report(), (0, "report: wrote REPORT.md (1 lines)"))
        self.assertTrue((self.out / "REPORT.md").is_file())

    def test_a_stamp_whose_id_exceeds_the_passes_started_is_refused(self):
        self.assertEqual(self.score(final=True)[0], 0)
        self.assertEqual(self.score(final=True)[0], 0)
        path = self.out / "scores.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["pass"]["id"] = 3
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(self.run_report(), (1, STALE))

    def test_a_good_final_pass_is_accepted_by_the_report(self):
        self.assertEqual(self.score(final=False)[0], 0)
        self.assertEqual(self.score(final=True)[0], 0)
        self.assertEqual(self.run_report(), (0, "report: wrote REPORT.md (1 lines)"))
        self.assertTrue((self.out / "REPORT.md").is_file())
        stamp = json.loads((self.out / "scores.json").read_text(encoding="utf-8"))["pass"]
        self.assertEqual(stamp, {"id": 1, "mode": "final"})

    def test_an_aborted_pass_followed_by_a_good_one_is_accepted(self):
        self.script = [score.ScoreAbort("a")]
        self.assertEqual(self.score(final=True)[0], 1)
        self.assertEqual(self.score(final=True)[0], 0)
        self.assertEqual(self.run_report()[0], 0)

    def test_a_scores_file_without_a_stamp_or_with_the_other_mode_is_refused(self):
        self.assertEqual(self.score(final=True)[0], 0)
        path = self.out / "scores.json"
        good = json.loads(path.read_text(encoding="utf-8"))
        for name, stamp in (("none", None), ("mode", {"id": 1, "mode": "normal"}), ("id", {"id": 2, "mode": "final"}),
                            ("shape", "final-1")):
            with self.subTest(case=name):
                data = dict(good)
                data.pop("pass")
                if stamp is not None:
                    data["pass"] = stamp
                path.write_text(json.dumps(data), encoding="utf-8")
                self.assertEqual(self.run_report(), (1, STALE))
        path.write_text(json.dumps(good), encoding="utf-8")
        self.assertEqual(self.run_report()[0], 0)


class TestR3AuditExportStale(unittest.TestCase):
    """R3: audit-export applies the stale-stamp check of the report (score.pass_problem) to scores.json."""

    def setUp(self):
        self.e = audit_tests.ExportTests("setUp")  # built by calling setUp on an instance, as test_exp0001_audit does
        self.e.setUp()
        self.addCleanup(self.e.doCleanups)
        self.e.finish()
        self.final = self.e.a.final
        self.scores_path = self.final / "scores.json"
        self.good = json.loads(self.scores_path.read_text(encoding="utf-8"))

    def put_scores(self, data):
        self.scores_path.write_text(json.dumps(data), encoding="utf-8")

    def refused(self):
        code, text = self.e.export()
        self.assertEqual(code, 1, text)
        self.assertEqual(text, "audit-export: STOP scores.json is not from the last completed final scoring pass; "
                               "run score again\n")
        self.assertFalse(os.path.lexists(self.e.out))
        self.assertNotIn("primary", text)

    def test_a_normal_mode_scores_file_after_two_aborted_final_passes_is_refused(self):
        self.assertEqual(self.good["pass"], {"id": 1, "mode": "final"})
        (self.final / "score-passes.json").write_text(json.dumps(
            {"normal": {"started": 1, "aborted": 0}, "final": {"started": 2, "aborted": 2}}), encoding="utf-8")
        self.put_scores(dict(self.good, **{"pass": {"id": 1, "mode": "normal"}}))
        self.refused()

    def test_a_scores_file_without_a_stamp_is_refused(self):
        self.assertTrue((self.final / "score-passes.json").is_file())
        data = dict(self.good)
        del data["pass"]
        self.put_scores(data)
        self.refused()

    def test_positive_control_a_good_final_stamp_is_exported(self):
        code, text = self.e.export()
        self.assertEqual(code, 0, text)
        self.assertEqual(sorted(os.listdir(self.e.out)), ["items.json", "key.json", "owner-labels.json"])

    def test_a_folder_that_is_not_final_is_not_refused_by_this_check(self):
        """Mirrors the report: a non-final folder with no score-passes.json passes the check (the pilot writes scores.json
        without the counting command); audit-export still refuses it, for the other reason."""
        self.assertEqual(score.pass_problem(self.e.a.pre, False, {}), "")
        code, text = self.e.export(results=self.e.a.pre)
        self.assertEqual(code, 1, text)
        self.assertIn("not the final scoring", text)
        self.assertNotIn("scoring pass", text)


EARLIER = "scores.json is from an earlier scoring occasion; run score again"


def open_occasion(out, **record):
    """What `run --supersede` leaves behind: superseded.json, whose scoring_occasion names the occasion (P:458)."""
    data = {"version": 1, "ids": ["heldout-001-x"], "excluded_cells": [], "scoring_occasion": 2}
    data.update(record)
    Path(out, "superseded.json").write_text(json.dumps(data), encoding="utf-8")


class TestScoringOccasion(unittest.TestCase):
    """A repair re-check is its own scoring occasion with its own two passes, counted in its own file (P:458)."""

    setUp = TestRow31.setUp
    fake_score_run = TestRow31.fake_score_run
    score = TestRow31.score
    passes = TestRow31.passes

    def passes_2(self):
        return json.loads((self.out / "score-passes-occasion-2.json").read_text(encoding="utf-8"))

    def scores(self):
        return json.loads((self.out / "scores.json").read_text(encoding="utf-8"))

    def test_a_third_start_in_occasion_2_is_refused_and_two_are_allowed(self):
        open_occasion(self.out)
        self.assertEqual(self.score()[0], 0)
        self.assertEqual(self.score()[0], 0)
        self.assertEqual(self.score(), (2, TWICE))
        self.assertEqual(self.scored, ["normal", "normal"])
        self.assertEqual(self.passes_2()["normal"], {"started": 2, "aborted": 0})
        self.assertFalse((self.out / "score-passes.json").exists())

    def test_the_first_score_after_a_supersede_is_pass_1_of_occasion_2(self):
        self.assertEqual(self.score()[0], 0)
        self.assertEqual(self.score()[0], 0)  # occasion 1 used both of its passes
        before = (self.out / "score-passes.json").read_bytes()
        self.assertEqual(self.scores()["pass"], {"id": 2, "mode": "normal"})  # occasion 1 keeps its stamp as it was
        open_occasion(self.out)
        self.assertEqual(self.score()[0], 0)
        self.assertEqual(self.scores()["pass"], {"id": 1, "mode": "normal", "occasion": 2})
        self.assertEqual((self.out / "score-passes.json").read_bytes(), before)
        self.assertEqual(self.passes_2()["normal"], {"started": 1, "aborted": 0})
        self.assertEqual(self.passes_2()["final"], {"started": 0, "aborted": 0})

    def test_the_final_mode_counts_in_occasion_2_too(self):
        open_occasion(self.out)
        for _ in range(2):
            self.assertEqual(self.score(final=True)[0], 0)
        self.assertEqual(self.score(final=True), (2, TWICE))
        self.assertEqual(self.passes_2()["final"], {"started": 2, "aborted": 0})
        self.assertEqual(self.scores()["pass"], {"id": 2, "mode": "final", "occasion": 2})

    def test_two_aborted_passes_in_occasion_2_end_that_occasion_only(self):
        self.assertEqual(self.score()[0], 0)
        before = (self.out / "score-passes.json").read_bytes()
        open_occasion(self.out)
        self.script = [score.ScoreAbort("a"), score.ScoreAbort("b")]
        self.assertEqual(self.score()[0], 1)
        self.assertEqual(self.score()[0], 1)
        self.assertEqual(self.score(), (2, BOTH_ABORTED))
        self.assertEqual(self.passes_2()["normal"], {"started": 2, "aborted": 2})
        self.assertEqual((self.out / "score-passes.json").read_bytes(), before)  # occasion 1's counters did not move
        self.assertEqual(self.scored, ["normal", "normal", "normal"])

    def test_a_failed_sanity_control_in_occasion_2_is_terminal_for_occasion_2_only(self):
        self.assertEqual(self.score()[0], 0)
        before = (self.out / "score-passes.json").read_bytes()
        open_occasion(self.out)
        self.script = [score.SanityAbort("judge sanity controls failed: x")]
        self.assertEqual(self.score()[0], 1)
        self.assertEqual(self.score(), (2, SANITY))
        self.assertEqual(self.passes_2()["normal"], {"started": 1, "aborted": 1, "terminal": True})
        self.assertEqual((self.out / "score-passes.json").read_bytes(), before)
        self.assertEqual(self.passes_2()["final"], {"started": 0, "aborted": 0})

    def test_occasion_2_starts_from_nothing_whatever_occasion_1_used(self):
        self.script = [score.SanityAbort("judge sanity controls failed: x")]
        self.assertEqual(self.score()[0], 1)  # occasion 1 is terminal
        self.assertEqual(self.score(), (2, SANITY))
        open_occasion(self.out)
        self.assertEqual(self.score()[0], 0)
        self.assertEqual(self.passes_2()["normal"], {"started": 1, "aborted": 0})

    def test_a_record_with_no_occasion_key_is_occasion_1(self):
        Path(self.out, "superseded.json").write_text(json.dumps({"version": 1, "ids": []}), encoding="utf-8")
        self.assertEqual(self.score()[0], 0)
        self.assertEqual(self.scores()["pass"], {"id": 1, "mode": "normal"})
        self.assertTrue((self.out / "score-passes.json").is_file())

    def test_a_malformed_record_stops_scoring_before_any_judge_call(self):
        for text in ("{oops", "[]", json.dumps({"scoring_occasion": 1}), json.dumps({"scoring_occasion": "2"}),
                     json.dumps({"scoring_occasion": True}), json.dumps({"scoring_occasion": 2.0}), "null"):
            with self.subTest(text=text):
                (self.out / "superseded.json").write_text(text, encoding="utf-8")
                self.assertEqual(self.score(), (1, "score: ABORT superseded.json unreadable"))
                self.assertEqual(self.scored, [])
                self.assertEqual(score.pass_problem(self.out, False, {}), "superseded.json unreadable")
                self.assertEqual(score.pass_problem(self.out, True, {}), "superseded.json unreadable")
        self.assertFalse((self.out / "score-passes.json").exists())
        self.assertFalse((self.out / "score-passes-occasion-2.json").exists())

    def test_the_counter_of_occasion_2_has_the_same_shape_rules(self):
        open_occasion(self.out)
        (self.out / "score-passes-occasion-2.json").write_text("{oops", encoding="utf-8")
        code, text = self.score()
        self.assertEqual(code, 1)
        self.assertIn("score-passes-occasion-2.json", text)
        self.assertEqual(self.scored, [])

    def test_pass_problem_refuses_a_stale_scores_file_even_without_a_counter(self):
        self.assertEqual(self.score()[0], 0)
        stale = self.scores()
        open_occasion(self.out)
        for final in (False, True):
            self.assertEqual(score.pass_problem(self.out, final, stale), EARLIER)
        self.assertEqual(self.score()[0], 0)  # a pass in occasion 2 writes its stamp and its counter
        fresh = self.scores()
        self.assertEqual(score.pass_problem(self.out, False, fresh), "")
        self.assertEqual(score.pass_problem(self.out, False, stale), EARLIER)
        self.assertEqual(score.pass_problem(self.out, False, dict(fresh, **{"pass": {"id": 1, "mode": "normal"}})), EARLIER)
        self.assertEqual(score.pass_problem(self.out, False, dict(fresh, **{"pass": {"id": 1, "mode": "normal", "occasion": 3}})), EARLIER)

    def test_a_non_final_report_refuses_an_occasion_1_scores_file(self):
        self.assertEqual(self.score()[0], 0)
        open_occasion(self.out)
        self.assertFalse((self.out / "score-passes-occasion-2.json").exists())
        shown = io.StringIO()
        self.assertEqual(report.write_report(str(self.out), stdout=shown), 1)
        self.assertEqual(shown.getvalue().strip(), f"report: STOP {EARLIER}")
        self.assertFalse((self.out / "REPORT.md").exists())

    def test_a_folder_without_the_record_is_as_it_was(self):
        self.assertEqual(score.current_occasion(self.out), 1)
        self.assertEqual(score.passes_path(self.out), self.out / "score-passes.json")
        self.assertEqual(score.pass_problem(self.out, False, {}), "")  # the pilot rule: no counter, no check


class TestOccasionFinalReport(unittest.TestCase):
    """A final report accepts the last completed pass of the last occasion only."""

    fake_score_run = TestStaleScores.fake_score_run
    score = TestRow31.score
    passes = TestRow31.passes
    run_report = TestStaleScores.run_report

    def setUp(self):
        TestStaleScores.setUp(self)
        patch = mock.patch.object(report, "build_markdown",
                                  lambda run, scores, final=None, superseded=None: "# report\n")  # takes the record too
        patch.start()
        self.addCleanup(patch.stop)

    def test_an_occasion_1_final_scores_file_is_refused_once_the_record_says_occasion_2(self):
        self.assertEqual(self.score(final=True)[0], 0)
        self.assertEqual(self.run_report()[0], 0)
        open_occasion(self.out)
        self.assertEqual(self.run_report(), (1, f"report: STOP {EARLIER}"))
        self.assertEqual(self.score(final=True)[0], 0)
        self.assertEqual(self.run_report()[0], 0)
        stamp = json.loads((self.out / "scores.json").read_text(encoding="utf-8"))["pass"]
        self.assertEqual(stamp, {"id": 1, "mode": "final", "occasion": 2})

    def test_the_second_pass_aborting_leaves_the_first_pass_of_the_occasion_standing(self):
        open_occasion(self.out)
        self.assertEqual(self.score(final=True)[0], 0)
        self.script = [score.ScoreAbort("a")]
        self.assertEqual(self.score(final=True)[0], 1)
        self.assertEqual(self.run_report()[0], 0)


class TestOccasionAuditExport(unittest.TestCase):
    """audit-export applies the same check, so an occasion-1 scores.json is refused there too."""

    setUp = TestR3AuditExportStale.setUp
    put_scores = TestR3AuditExportStale.put_scores

    def test_an_occasion_1_scores_file_is_refused_once_the_record_says_occasion_2(self):
        open_occasion(self.final)
        code, text = self.e.export()
        self.assertEqual(code, 1, text)
        self.assertEqual(text, f"audit-export: STOP {EARLIER}\n")
        self.assertFalse(os.path.lexists(self.e.out))

    def test_a_scores_file_stamped_for_occasion_2_is_exported(self):
        open_occasion(self.final)
        (self.final / "score-passes-occasion-2.json").write_text(json.dumps(
            {"normal": {"started": 0, "aborted": 0}, "final": {"started": 1, "aborted": 0}}), encoding="utf-8")
        self.put_scores(dict(self.good, **{"pass": {"id": 1, "mode": "final", "occasion": 2}}))
        code, text = self.e.export()
        self.assertEqual(code, 0, text)


class TestRow51(plumbing.Scripted, unittest.TestCase):  # Scripted defines no tests
    """Row 51: each run has recorded/runs/<run>/isolation.json in the repository, counts and check labels only (E7)."""

    def proof_path(self, out=None):
        """The repository path (under the injected root) of the run's proof; <run> is the name of --out."""
        out = Path(out or self.out)
        return (self.repo_root.joinpath(*claude_run.RECORDED_DIR) / "runs" / self.run_json(out)["run_id"]
                / "isolation.json")

    def test_the_proof_is_at_the_repository_path_the_check_reads_and_not_under_out(self):
        self.assertEqual(self.go(), 0)
        self.assertEqual(self.run_json()["run_id"], self.out.name)
        path = self.repo_root / "experiments" / "EXP-0001" / "recorded" / "runs" / self.out.name / "isolation.json"
        self.assertEqual(self.proof_path(), path)
        self.assertTrue(path.is_file())
        relative = path.relative_to(self.repo_root).as_posix()
        match = paths.RUN_FILE.match(relative)
        self.assertEqual(match.groups(), ("EXP-0001", self.out.name))
        self.assertIsNone(check_experiments.isolation_problem(path.read_bytes()))
        self.assertEqual(sorted(p.name for p in self.out.rglob("isolation.json")), [])
        self.assertFalse((self.out / "runs").exists())

    def test_a_later_proof_replaces_the_file_at_the_repository_path(self):
        self.assertEqual(self.go(), 0)
        stale = self.proof_path()
        stale.write_text("{}\n", encoding="utf-8")
        self.assertEqual(self.go(), 0)
        self.assertIs(self.proof()["isolated"], True)
        self.assertEqual(sorted(p.name for p in stale.parent.iterdir()), ["isolation.json"])  # no temp file left

    def proof(self, out=None):
        return json.loads(self.proof_path(out).read_bytes().decode("utf-8"))

    def test_a_clean_batch_writes_an_isolated_proof_that_the_repository_check_accepts(self):
        self.assertEqual(self.go(), 0)
        path = self.proof_path()
        self.assertTrue(path.is_file())
        self.assertIsNone(check_experiments.isolation_problem(path.read_bytes()))
        proof = self.proof()
        meta = self.run_json()
        self.assertEqual(sorted(proof), ["checks", "excluded", "isolated", "proof_counts", "run_id"])
        self.assertEqual(proof["excluded"], {"count": 0, "checks": []})
        self.assertIs(proof["isolated"], True)
        self.assertEqual(proof["run_id"], meta["run_id"])
        self.assertEqual(proof["proof_counts"], meta["proof_counts"])
        self.assertEqual(proof["checks"], sorted(isolation.ISOLATION_CHECKS | isolation.OUTCOME_CHECKS))
        self.assertTrue(proof["checks"] and all(isinstance(c, str) and c for c in proof["checks"]))
        self.assertEqual(path.read_text(encoding="utf-8"), json.dumps(proof, indent=1, sort_keys=True) + "\n")

    def test_a_resolved_rerun_is_still_isolated_and_is_counted(self):
        self.script = {1: plumbing.mcp_loaded}  # the first preserve run fails the proof; its re-run passes
        self.assertEqual(self.go(), 0)
        proof = self.proof()
        self.assertIs(proof["isolated"], True)
        self.assertEqual(proof["proof_counts"]["isolation_reruns"], 1)
        self.assertEqual(proof["proof_counts"]["excluded_after_rerun"], 0)

    def test_an_excluded_cell_does_not_make_the_proof_not_isolated(self):
        """P:119-126 allow excluded cells: isolated is judged over the included cells; the file lists the excluded ones."""
        self.script = {1: plumbing.mcp_loaded, 2: plumbing.mcp_loaded}  # both attempts fail: the cell is excluded
        self.assertEqual(self.go(), 0)
        self.assertEqual(self.run_json()["proof_counts"]["excluded_after_rerun"], 1)
        proof = self.proof()
        self.assertIs(proof["isolated"], True)
        self.assertEqual(proof["excluded"]["count"], 1)
        self.assertTrue(proof["excluded"]["checks"])
        self.assertEqual(proof["excluded"]["checks"], sorted(proof["excluded"]["checks"]))
        self.assertTrue(set(proof["excluded"]["checks"]) <= isolation.ISOLATION_CHECKS)
        self.assertIsNone(check_experiments.isolation_problem(self.proof_path().read_bytes()))  # E7 accepts it

    def test_a_proof_logged_later_refreshes_the_file(self):
        self.assertEqual(self.go(), 0)
        self.assertIs(self.proof()["isolated"], True)
        probes.log_proof(self.out, "excluded_cells", {"kind": "probe", "arm": "floor", "fixture": "f", "seed": 1,
                                                       "checks": ["mcp_servers"]})
        self.assertIs(self.proof()["isolated"], True)  # an excluded cell is not an included failure
        self.assertEqual(self.proof()["proof_counts"]["excluded_after_rerun"], 1)
        self.assertEqual(self.proof()["excluded"], {"count": 1, "checks": ["mcp_servers"]})

    def test_a_real_isolation_failure_in_an_included_cell_is_not_isolated(self):
        """A cell that failed the isolation check, is not excluded and has no result yet (its re-run is pending) is an
        unresolved failure in an included cell."""
        self.assertEqual(self.go(), 0)
        meta = self.run_json()
        self.assertIs(self.proof()["isolated"], True)
        cell = {"kind": "probe", "arm": meta["arms"][0], "fixture": meta["fixtures"][0], "seed": 1, "checks": ["mcp_servers"]}
        pending = dict(cell, fixture="heldout-999-pending")  # a cell with no result file
        probes.log_proof(self.out, "isolation_reruns", pending)
        proof = self.proof()
        self.assertIs(proof["isolated"], False)
        self.assertEqual(proof["excluded"], {"count": 0, "checks": []})
        self.assertIsInstance(check_experiments.isolation_problem(self.proof_path().read_bytes()), str)

    def test_a_rerun_that_produced_its_result_is_resolved(self):
        """Positive control for the test above: the same entry for a cell whose result file exists is not a failure."""
        self.assertEqual(self.go(), 0)
        meta = self.run_json()
        cell = {"kind": "probe", "arm": meta["arms"][0], "fixture": meta["fixtures"][0], "seed": 1, "checks": ["mcp_servers"]}
        self.assertTrue(plumbing.arms.probe_path(self.out, cell["arm"], cell["fixture"], 1).is_file())
        probes.log_proof(self.out, "isolation_reruns", cell)
        self.assertIs(self.proof()["isolated"], True)

    def test_an_excluded_cell_and_an_included_failure_together_are_not_isolated(self):
        self.script = {1: plumbing.mcp_loaded, 2: plumbing.mcp_loaded}
        self.assertEqual(self.go(), 0)
        meta = self.run_json()
        probes.log_proof(self.out, "isolation_reruns", {"kind": "probe", "arm": meta["arms"][0],
                                                         "fixture": "heldout-999-pending", "seed": 1, "checks": ["mcp_servers"]})
        proof = self.proof()
        self.assertIs(proof["isolated"], False)
        self.assertEqual(proof["excluded"]["count"], 1)

    def test_the_excluded_summary_names_no_fixture_cell_or_path(self):
        self.script = {1: plumbing.mcp_loaded, 2: plumbing.mcp_loaded}
        self.assertEqual(self.go(), 0)
        meta = self.run_json()
        text = self.proof_path().read_text(encoding="utf-8")
        names = list(meta["fixtures"]) + [e["fixture"] for e in meta["excluded_cells"]] + [str(self.tmp), str(self.out)]
        for name in names:
            self.assertNotIn(name, text)
        self.assertEqual(sorted(self.proof()["excluded"]), ["checks", "count"])

    def test_the_file_holds_counts_and_labels_only(self):
        self.script = {1: plumbing.mcp_loaded, 2: plumbing.mcp_loaded}
        self.assertEqual(self.go(), 0)
        text = self.proof_path().read_text(encoding="utf-8")
        meta = self.run_json()
        for private in (meta["raw_run_id"], str(self.tmp), str(self.home), plumbing.base.HOST_MARKER):
            self.assertNotIn(private, text)
        proof = json.loads(text)

        def leaves(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    yield key
                    yield from leaves(value)
            elif isinstance(node, list):
                for value in node:
                    yield from leaves(value)
            else:
                yield node

        for leaf in leaves(proof):
            self.assertTrue(isinstance(leaf, (bool, int)) or (isinstance(leaf, str) and "/" not in leaf and len(leaf) < 60), leaf)

    def test_the_check_labels_are_every_label_the_isolation_check_can_give(self):
        """A drift guard: the constant names every label that isolation._check appends or returns."""
        source = (Path(isolation.__file__)).read_text(encoding="utf-8")
        body = source[source.index("def _check("):source.index("# Ledgers")]
        literals = set(re.findall(r'fails\.append\("([a-z_]+)"\)', body)) | set(re.findall(r'"failures": \["([a-z_]+)"\]', body))
        templates = set(re.findall(r'fails\.append\(f"([^"]+)"\)', body))
        self.assertEqual(templates, {"init_{key}_missing", "{label}_unpinned", "{label}_mismatch", "{key}_entry_invalid",
                                     "{key}_outside_allowlist"})
        expanded = {"init_%s_missing" % k for k in ("plugins", "skills", "agents", "mcp_servers")}
        expanded |= {"%s_%s" % (label, tail) for label in ("version", "model") for tail in ("unpinned", "mismatch")}
        expanded |= {"%s_%s" % (k, tail) for k in ("skills", "agents") for tail in ("entry_invalid", "outside_allowlist")}
        self.assertEqual(literals | expanded, isolation.ISOLATION_CHECKS | isolation.OUTCOME_CHECKS)
        self.assertFalse(isolation.ISOLATION_CHECKS & isolation.OUTCOME_CHECKS)
        self.assertLessEqual(isolation.PIN_CHANGE_CHECKS, isolation.ISOLATION_CHECKS)


# --------------------------------------------------------------------------
# Row 24 (P:277): the manifest, the record and the repair log are committed together
# --------------------------------------------------------------------------

class TestRow24(seal_tests.World, unittest.TestCase):  # World defines no tests
    """The commit that holds manifest.txt also holds record.json and the repair log, with the bytes on disk."""

    def setUp(self):
        super().setUp()
        self.assertEqual(self.seal_it()[0], 0)
        self.log_rel = "seal-1/repair-log/" + self.repair.name

    def commit(self, *paths, message="seal"):
        self.git("add", *paths)
        self.git("commit", "-q", "-m", message)

    def guard(self):
        return seal.baseline_guard("heldout", seal_tests.HIDS, ["baseline"], self.store, self.seal_dir, self.pre, self.repo)

    def assertRefusal(self, label, phrase):
        code, text = self.guard()
        self.assertEqual(code, 2, text)
        self.assertIn(label, text)
        self.assertIn(phrase, text)
        return text

    def test_all_three_committed_together_pass(self):
        """Positive control: the same guard that refuses below passes here."""
        self.commit("seal-1")
        self.assertEqual(self.guard(), (0, ""))

    def test_a_record_that_is_not_tracked_refuses_naming_it(self):
        self.commit("seal-1/manifest.txt", "seal-1/repair-log")
        self.assertRefusal("record.json", "not committed at HEAD")

    def test_a_repair_log_that_is_not_tracked_refuses_naming_it(self):
        self.commit("seal-1/manifest.txt", "seal-1/record.json")
        self.assertRefusal("repair log", "not committed at HEAD")

    def test_a_modified_record_refuses(self):
        self.commit("seal-1")
        path = self.seal_dir / "record.json"
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.assertRefusal("record.json", "differs from the committed file")

    def test_a_modified_repair_log_refuses(self):
        self.commit("seal-1")
        path = self.repair_log_path()
        path.write_text(path.read_text(encoding="utf-8") + "more\n", encoding="utf-8")
        self.assertRefusal("repair log", "differs from the committed file")

    def test_a_record_added_in_a_later_commit_refuses(self):
        self.commit("seal-1/manifest.txt", "seal-1/repair-log")
        self.commit("seal-1/record.json", message="later")
        self.assertRefusal("record.json", "not committed in the same commit as manifest.txt")

    def test_a_repair_log_added_in_a_later_commit_refuses(self):
        self.commit("seal-1/manifest.txt", "seal-1/record.json")
        self.commit("seal-1/repair-log", message="later")
        self.assertRefusal("repair log", "not committed in the same commit as manifest.txt")

    def test_a_missing_record_refuses(self):
        self.commit("seal-1")
        (self.seal_dir / "record.json").unlink()
        self.assertRefusal("record.json", "is missing")

    def test_a_missing_repair_log_refuses(self):
        self.commit("seal-1")
        self.repair_log_path().unlink()
        self.assertRefusal("repair log", "is missing")

    def test_the_refusal_carries_no_file_content(self):
        self.commit("seal-1/manifest.txt", "seal-1/repair-log")
        text = self.assertRefusal("record.json", "not committed at HEAD")
        self.assertNotIn("heldout-001", text)
        self.assertNotIn("one fixture reworded", text)

    def repair_log_path(self):
        return self.repo / self.log_rel


# --------------------------------------------------------------------------
# Row 21c (P:133, P:258-265): a held-out fixture with fewer than 3 scorable probes is dropped, not sealed
# --------------------------------------------------------------------------

def record_with(counts: dict) -> dict:
    """A record.json of fixtures with the given numbers of scorable probes and no excluded_fixtures."""
    return {"fixtures": {f: {"scorable": ["p%d" % n for n in range(c)], "dropped": {}, "excluded": []}
                         for f, c in counts.items()}, "excluded_fixtures": {}}


class TestRow21c(seal_tests.World, unittest.TestCase):  # World defines no tests
    REASON = "fewer than 3 scorable probes"

    def test_one_definition_of_the_minimum(self):
        self.assertEqual((score.MIN_SCORABLE_PROBES, pilot.DEFAULT_MIN_SCORABLE), (3, 3))

    def test_the_boundary_two_is_dropped_and_three_is_kept(self):
        found = score.too_few_scorable({"a": {"scorable": ["1", "2"]}, "b": {"scorable": ["1", "2", "3"]},
                                        "c": {"scorable": []}, "d": {}})
        self.assertEqual(sorted(found), ["a", "c", "d"])
        self.assertEqual(found["a"], {"reason": self.REASON, "scorable": 2})

    def seal_with_counts(self, first, second):
        fake = copy.deepcopy(self.scores)
        fake["scorable"][seal_tests.HIDS[0]] = ["p%d" % n for n in range(first)]
        fake["scorable"][seal_tests.HIDS[1]] = ["p%d" % n for n in range(second)]
        with mock.patch.object(score, "score_run", return_value=fake):
            self.assertEqual(self.seal_it()[0], 0)
        return json.loads((self.seal_dir / "record.json").read_text(encoding="utf-8"))

    def test_the_seal_lists_a_fixture_with_two_scorable_probes_as_dropped(self):
        record = self.seal_with_counts(2, 3)
        self.assertEqual(sorted(record["excluded_fixtures"]), sorted([seal_tests.EXTRA, seal_tests.HIDS[0]]))
        self.assertEqual(record["excluded_fixtures"][seal_tests.HIDS[0]], {"reason": self.REASON, "scorable": 2})
        self.assertEqual(sorted(record["fixtures"]), sorted(seal_tests.HIDS))  # the record of scores still has both

    def test_the_seal_keeps_a_fixture_with_exactly_three(self):
        """Boundary positive control."""
        record = self.seal_with_counts(3, 3)
        self.assertEqual(sorted(record["excluded_fixtures"]), [seal_tests.EXTRA])  # the spec's own excluded block only

    def test_report_counts_the_sealed_fixtures_without_the_dropped_one(self):
        counts = {"f%02d" % n: 3 for n in range(22)}
        counts["f22"] = 2
        record = record_with(counts)
        who = report.sealed_and_left(record, {"per_fixture": {"baseline": {f: {"primary": 1.0} for f in counts}}})
        self.assertEqual(who["sealed"], 22)
        self.assertEqual(who["excluded"], {"f22": self.REASON})

    def test_two_scorable_probes_take_the_sealed_count_under_twenty_and_report_says_not_determined(self):
        for good in (19, 20):
            with self.subTest(good=good):
                counts = {"f%02d" % n: 3 for n in range(good)}
                counts["bad"] = 2
                who = report.sealed_and_left(record_with(counts),
                                             {"per_fixture": {"baseline": {f: {"primary": 1.0} for f in counts}}})
                self.assertEqual(who["sealed"], good)
                # 20 sealed leave 5 of 25, which is not more than 5, so the interval (it holds 0.761) decides
                got = report.decide(0.7, 0.8, who["sealed"], who["left"], 0.9)[0]
                self.assertEqual(got, "not determined" if good < 20 else "inconclusive")


# --------------------------------------------------------------------------
# P:319: run and pilot keep raw records out of the repository, so their --out is outside every clone
# --------------------------------------------------------------------------

class TestRunOutOutsideClone(plumbing.iso.CloneFolders, unittest.TestCase):
    """A temporary folder plays the clone (claude_run.ROOT is patched), so nothing is written into the real tree."""

    def setUp(self):
        super().setUp()
        patch = mock.patch.object(claude_run, "ROOT", self.clone)
        patch.start()
        self.addCleanup(patch.stop)

    def call(self, command, out):
        shown = io.StringIO()
        environ = {"CPR_EVAL_LIVE": "1", "TMPDIR": str(self.tmp)}
        if command == "run":
            code = probes.command_run(plumbing.base.FIXTURES, out, "dev", "dev-001", ["floor"], 1, None, "20260101",
                                      environ, shown)
        else:
            code = pilot.command_pilot(plumbing.base.FIXTURES, out, "dev", "dev-001", ["floor"], 1, 1.0, "20260101",
                                       environ, shown)
        return code, shown.getvalue()

    def assert_refused(self, command, out):
        code, text = self.call(command, out)
        self.assertEqual(code, 2, (command, text))
        self.assertTrue(text.startswith(f"{command}: REFUSED --out is inside the repository clone"), text)
        self.assertFalse(Path(out).exists(), out)

    def test_an_out_under_recorded_is_refused_by_run_and_pilot(self):
        for command in ("run", "pilot"):
            with self.subTest(command=command):
                self.assert_refused(command, self.recorded / "x")

    def test_an_out_elsewhere_in_the_clone_is_still_refused(self):
        for command in ("run", "pilot"):
            with self.subTest(command=command):
                self.assert_refused(command, self.clone / "other" / "x")

    def test_an_out_in_a_temp_dir_outside_the_clone_is_not_refused(self):
        """Positive control: the same call with an outside --out gets past this check (to the live gate's own stop)."""
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outside, True)
        for command in ("run", "pilot"):
            with self.subTest(command=command):
                code, text = self.call(command, outside / "x")
                self.assertNotIn("REFUSED", text)
                self.assertNotEqual(code, 2, text)

    def test_the_command_line_refuses_an_out_under_recorded_for_run_and_pilot(self):
        import run
        for argv in (["run", "--out"], ["pilot", "--budget-usd", "1", "--out"]):
            with self.subTest(command=argv[0]):
                with mock.patch("sys.stdout", new=io.StringIO()) as shown:
                    code = run.main(argv + [str(self.recorded / "x")])
                self.assertEqual(code, 2, shown.getvalue())
                self.assertTrue(shown.getvalue().startswith(f"{argv[0]}: REFUSED --out is inside the repository clone"),
                                shown.getvalue())
                self.assertFalse((self.recorded / "x").exists())

    # Regression guards: the commands the ruling does not name keep accepting an --out under recorded/.

    def test_seal_still_accepts_an_out_under_recorded(self):
        shown = io.StringIO()
        seal.command_seal("fixtures", "pre", "repair", "guide", "rule", str(self.recorded / "seal-1"),
                          environ={}, stdout=shown, repo_root=self.clone)
        self.assertNotIn("inside the repository clone", shown.getvalue())
        self.assertFalse((self.recorded / "seal-1").exists())

    def test_report_still_accepts_an_out_under_recorded(self):
        shown = io.StringIO()
        report.write_report(self.outside / "results", str(self.recorded / "x"), shown)
        self.assertNotIn("REFUSED", shown.getvalue())
        self.assertFalse((self.recorded / "x").exists())

    def test_pilot_md_still_accepts_an_out_under_recorded(self):
        shown = io.StringIO()
        pilot.command_pilot_md(self.outside / "results", None, self.recorded / "x", shown)
        self.assertNotIn("REFUSED", shown.getvalue())
        self.assertFalse((self.recorded / "x").exists())


if __name__ == "__main__":
    unittest.main()
