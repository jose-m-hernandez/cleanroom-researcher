"""Offline tests for the outcome a held-out final report of EXP-0001 states (README P:66-70, P:105-115, P:134-135,
P:146, P:156-164): the decision rule, who left the primary, the T-filter figure, the multi-value count, the new sections
end to end, and the printed bounds being the decided bounds.

The seal and scoring worlds of tests/test_exp0001_seal.py and tests/test_exp0001_score.py are used by calling their setUp
on an instance, never by subclassing them or importing a class that defines tests. This file does not import `random`.
Dev and synthetic reports stay byte-identical: tests/test_exp0001_report.py pins that against the committed REPORT.md
(test_regenerating_from_the_synthetic_tree_reproduces_the_committed_bytes).
"""

from __future__ import annotations

import io
import json
import re
import unittest
from pathlib import Path
from unittest import mock

from tests import exp0001_paths  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]

import audit  # noqa: E402
import report  # noqa: E402
import score  # noqa: E402
from tests import test_exp0001_score as base  # noqa: E402
from tests import test_exp0001_seal as st  # noqa: E402

TREE = ROOT / "tests" / "data" / "exp0001-synthetic"
NEW_HEADINGS = ["## The primary question", "## Reported, not decisive (additions)", "## Pre-seal repair log"]
EXPLORATORY_HEADING = "## Exploratory (not planned in the protocol): paired differences against the baseline"
REASON_SEALED = "fewer than 20 fixtures sealed (P:109)"
REASON_LEFT = "more than 5 of the 25 fixtures left the primary (P:135)"
REASON_KAPPA = "the audit's kappa is below 0.6 (P:146)"
REASON_INTERVAL = "the interval includes 0.761 (P:68)"


class DecideTests(unittest.TestCase):
    """D1: decide(lo, hi, sealed, left, kappa), by table."""

    def test_the_table(self):
        for sealed, left, kappa, lo, hi, outcome, reasons in [
            (19, 0, 0.9, 0.80, 0.90, "not determined", [REASON_SEALED]),
            (25, 6, 0.9, 0.80, 0.90, "inconclusive", [REASON_LEFT]),
            (25, 5, 0.59, 0.80, 0.90, "inconclusive", [REASON_KAPPA]),
            (25, 5, 0.6, 0.762, 0.90, "supports the prediction", []),
            (25, 0, 0.9, 0.761, 0.90, "inconclusive", [REASON_INTERVAL]),  # equal is not above
            (25, 0, 0.9, 0.60, 0.760, "counts against the prediction", []),
            (25, 0, 0.9, 0.60, 0.761, "inconclusive", [REASON_INTERVAL]),  # equal is not below
            (20, 5, 0.6, 0.762, 0.9, "supports the prediction", []),  # both edges allowed
        ]:
            with self.subTest(sealed=sealed, left=left, kappa=kappa, lo=lo, hi=hi):
                self.assertEqual(report.decide(lo, hi, sealed, left, kappa), (outcome, reasons))

    def test_the_precedence_is_sealed_then_left_then_kappa(self):
        self.assertEqual(report.decide(0.9, 0.95, 19, 9, 0.1), ("not determined", [REASON_SEALED]))
        self.assertEqual(report.decide(0.9, 0.95, 25, 9, 0.1), ("inconclusive", [REASON_LEFT]))
        self.assertEqual(report.decide(0.9, 0.95, 25, 0, 0.1), ("inconclusive", [REASON_KAPPA]))

    def test_no_interval_is_inconclusive(self):
        self.assertEqual(report.decide(None, None, 25, 0, 0.9), ("inconclusive", [REASON_INTERVAL]))

    def test_the_constants(self):
        self.assertEqual((report.THRESHOLD, report.PLANNED_FIXTURES, report.MAX_LEFT, report.MIN_SEALED),
                         (0.761, 25, 5, 20))


class LeftTests(unittest.TestCase):
    """D2: who left the primary."""

    def test_sealed_and_left(self):
        ids = ["heldout-%03d-x" % n for n in range(1, 23)]
        record = {"fixtures": {f: {} for f in ids},
                  "excluded_fixtures": {ids[-1]: {"reason": "the answer leaks", "date": "2026-10-20"}}}
        baseline = {f: {"primary": 0.5} for f in ids}
        baseline[ids[3]] = {"primary": None}
        baseline[ids[7]] = {"primary": None}
        out = report.sealed_and_left(record, {"per_fixture": {"baseline": baseline}})
        self.assertEqual(out["sealed"], 21)
        self.assertEqual(out["no_value"], [ids[3], ids[7]])
        self.assertEqual(out["left"], 4 + 2)  # 25 less 21 sealed, plus the two sealed fixtures with no baseline value
        self.assertEqual(out["excluded"], {ids[-1]: "the answer leaks"})

    def test_more_sealed_than_planned_never_goes_below_zero(self):
        ids = ["f%02d" % n for n in range(27)]
        out = report.sealed_and_left({"fixtures": {f: {} for f in ids}, "excluded_fixtures": {}},
                                     {"per_fixture": {"baseline": {f: {"primary": 1.0} for f in ids}}})
        self.assertEqual((out["sealed"], out["left"]), (27, 0))


def t_scores() -> dict:
    """D3: two fixtures, baseline seeds 1 to 3. For fA the probes are: p1 and p2 scorable, p3 and p5 dropped, p4 in
    neither. p1 passes the filter (floor seed 1 wrong, oracle seed 1 right); p2 is out (floor right); p3 passes; p5 is
    out (no oracle seed-1 label); p4 is out (not scorable or dropped). fB's one probe is out (floor right)."""
    def labels(**by_probe):
        return {p: v for p, v in by_probe.items()}
    floor_a = labels(p1="incorrect", p2="correct", p3="incorrect", p4="incorrect", p5="incorrect")
    oracle_a = labels(p1="correct", p2="correct", p3="correct", p4="correct")  # p5 has no oracle label
    return {
        "fixtures": ["fA", "fB"],
        "scorable": {"fA": ["p1", "p2"], "fB": ["p1"]},
        "dropped": {"fA": {"p3": ["oracle_minority"], "p5": ["label_missing"]}, "fB": {}},
        "labels": {
            "floor": {"fA": {"1": floor_a, "2": labels(p1="correct")}, "fB": {"1": labels(p1="correct")}},
            "oracle": {"fA": {"1": oracle_a, "2": labels(p1="incorrect")}, "fB": {"1": labels(p1="correct")}},
            "baseline": {
                "fA": {"1": labels(p1="correct", p2="correct", p3="incorrect", p4="correct", p5="correct"),
                       "2": labels(p1="correct", p2="incorrect", p3="correct", p4="incorrect", p5="incorrect"),
                       "3": labels(p1="incorrect", p2="correct", p3=None, p4="correct", p5="correct")},
                "fB": {"1": labels(p1="correct"), "2": labels(p1="correct"), "3": labels(p1="correct")}},
        },
    }


class TFilterTests(unittest.TestCase):
    """D3: the T-filter primary (P:158; ruling Q9)."""

    def test_the_cells_are_the_hand_values(self):
        got = report.t_filter_cells(t_scores())
        # fA keeps p1 and p3. Seed 1: p1 right, p3 wrong = 1/2. Seed 2: both right = 1. Seed 3: p1 wrong, p3 unlabelled = 0.
        self.assertEqual(got["fA"], 0.5)
        self.assertIsNone(got["fB"])  # no probe passes the filter

    def test_a_missing_oracle_seed_1_label_leaves_the_probe_out(self):
        scores = t_scores()
        scores["labels"]["oracle"]["fA"]["1"]["p3"] = None
        self.assertEqual(report.t_filter_cells(scores)["fA"], report.stats._r(2 / 3))  # p1 alone: 1, 1, 0
        scores["labels"]["oracle"]["fA"]["1"].pop("p3")  # absent, as well as None
        self.assertEqual(report.t_filter_cells(scores)["fA"], report.stats._r(2 / 3))

    def test_a_missing_floor_seed_1_label_leaves_the_probe_out(self):
        scores = t_scores()
        scores["labels"]["floor"]["fA"]["1"]["p1"] = None
        scores["labels"]["floor"]["fA"]["1"].pop("p3")
        self.assertIsNone(report.t_filter_cells(scores)["fA"])

    def test_a_floor_label_of_abstained_counts_as_not_correct(self):
        # K5: the rule is "present and not correct", not "incorrect": p3 (floor abstained, oracle correct) is kept
        scores = t_scores()
        scores["labels"]["floor"]["fA"]["1"]["p3"] = "abstained"
        self.assertEqual(report.t_filter_cells(scores)["fA"], 0.5)  # p1 and p3 as before; without p3 it would be 2/3

    def test_only_seed_1_of_the_floor_and_the_oracle_counts(self):
        scores = t_scores()
        scores["labels"]["floor"]["fA"]["2"] = {p: "incorrect" for p in ("p1", "p2", "p3")}  # seed 2 is not read
        scores["labels"]["oracle"]["fA"]["2"] = {p: "correct" for p in ("p1", "p2", "p3")}
        self.assertEqual(report.t_filter_cells(scores)["fA"], 0.5)


class MultiValueTests(unittest.TestCase):
    """D4: score_run's multi_value, on the scoring world of tests/test_exp0001_score.py (its Env, set up on an instance)."""

    def test_the_count_over_baseline_answers_with_stale_answers(self):
        env = base.Env("calls")
        env.setUp()
        self.addCleanup(env.doCleanups)
        self.assertEqual(env.batch(), 0, env.last_output)
        fid_a, fid_b = base.IDS  # dev-001 p4: gold "newest first", stale "oldest first"; dev-002 p4: gold "six", stale "four"

        def put(arm, fid, seed, **answers):
            path = env.out / "probes" / arm / fid / f"s{seed}.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["answers"].update(answers)
            path.write_text(json.dumps(data), encoding="utf-8")
        put("baseline", fid_a, 1, p4="Newest first, not oldest first", p1="harbour local time")
        put("baseline", fid_a, 2, p4="newest first")
        put("baseline", fid_b, 1, p4="UNKNOWN")
        put("baseline", fid_b, 2, p4="six")
        put("floor", fid_a, 1, p4="newest first and oldest first")  # a floor answer: never counted
        judge = base.FakeJudge(base.SPECS)
        scores = score.score_run(env.out, base.SPECS, env.cfg, env.environ, judge.label_probe, judge.label_items)
        # applicable: dev-001 p4 in seeds 1 and 2, dev-002 p4 in seed 2 (an UNKNOWN, and the probes with no stale answer, are out)
        self.assertEqual(scores["multi_value"], {"applicable": 3, "count": 1})

    def test_no_baseline_arm_gives_zero_and_zero(self):
        env = base.Env("calls")
        env.setUp()
        self.addCleanup(env.doCleanups)
        self.assertEqual(env.batch(arm_names=("floor", "oracle")), 0, env.last_output)
        judge = base.FakeJudge(base.SPECS)
        scores = score.score_run(env.out, base.SPECS, env.cfg, env.environ, judge.label_probe, judge.label_items)
        self.assertEqual(scores["multi_value"], {"applicable": 0, "count": 0})


def final_input(n_audit=100, kappa=0.9, count_rule="the count rule\nsecond line", repair="repair log line\nline two"):
    """A hand `final` dict for build_markdown, over the synthetic tree's six fixtures (25 sealed, none left)."""
    ids = json.loads((TREE / "scores.json").read_text(encoding="utf-8"))["fixtures"]
    record = {"fixtures": {f: {} for f in ids + ["heldout-%03d-pad" % n for n in range(19)]}, "excluded_fixtures": {},
              "count_rule": count_rule}
    return {"record": record, "repair_log_text": repair, "audit": {"n": n_audit, "kappa": kappa}}


def final_world():
    run = json.loads((TREE / "run.json").read_text(encoding="utf-8"))
    scores = json.loads((TREE / "scores.json").read_text(encoding="utf-8"))
    scores["multi_value"] = {"applicable": 7, "count": 2}
    scores["labels"] = {"floor": {}, "oracle": {}, "baseline": {}}
    return run, scores


class PrintedBoundsTests(unittest.TestCase):
    """D7: the bounds printed in the primary question are the bounds decide received."""

    def build(self, lo, hi):
        run, scores = final_world()
        seen = []
        real = report.decide

        def spy(*args):
            seen.append(args)
            return real(*args)
        who = {"sealed": 25, "left": 0, "no_value": [], "excluded": {}}  # the other sections are not under test here
        with mock.patch.object(report.stats, "bootstrap_ci", lambda values, **kw: (lo, hi)), \
                mock.patch.object(report, "sealed_and_left", lambda record, scores: who), \
                mock.patch.object(report, "decide", spy):
            md = report.build_markdown(run, scores, final_input())
        return md, seen

    def printed(self, md):
        m = re.search(r"lower (\S+), upper (\S+)\.", md)
        self.assertIsNotNone(m, md)
        return m.group(1), m.group(2)

    def test_the_printed_bounds_are_the_decided_bounds(self):
        for lo, hi in [(0.7614, 0.90123456), (0.76049, 0.9), (0.7609999, 0.8765432101)]:
            with self.subTest(lo=lo, hi=hi):
                md, seen = self.build(lo, hi)
                self.assertEqual(len(seen), 1)
                printed = self.printed(md)
                self.assertEqual((float(printed[0]), float(printed[1])), (seen[0][0], seen[0][1]))
                self.assertEqual((float(printed[0]), float(printed[1])), (lo, hi))
                self.assertNotEqual(printed[0], "%.3f" % lo)  # more digits than the 3-decimal table format

    def test_a_lower_bound_of_0_7614_prints_as_0_7614_beside_the_outcome_that_used_it(self):
        md, seen = self.build(0.7614, 0.9)
        self.assertIn("lower 0.7614, upper 0.9.", md)
        self.assertIn("Outcome: supports the prediction.", md)


class PrintedKappaTests(unittest.TestCase):
    """K1: the kappa printed in the primary question is the kappa decide received, not a rounding of it."""

    def build(self, kappa):
        run, scores = final_world()
        seen = []
        real = report.decide

        def spy(*args):
            seen.append(args)
            return real(*args)
        who = {"sealed": 25, "left": 0, "no_value": [], "excluded": {}}  # the other sections are not under test here
        with mock.patch.object(report.stats, "bootstrap_ci", lambda values, **kw: (0.8, 0.9)), \
                mock.patch.object(report, "sealed_and_left", lambda record, scores: who), \
                mock.patch.object(report, "decide", spy):
            md = report.build_markdown(run, scores, final_input(kappa=kappa))
        return md, seen

    def test_a_kappa_of_0_5996_is_printed_as_0_5996_beside_inconclusive(self):
        md, seen = self.build(0.5996)
        line = [ln for ln in md.splitlines() if ln.startswith("The audit's kappa is")]
        self.assertEqual(len(line), 1, md)
        m = re.search(r"kappa is (\S+) against the bar 0\.6", line[0])
        self.assertIsNotNone(m, line[0])
        self.assertEqual(float(m.group(1)), seen[0][4])
        self.assertEqual(float(m.group(1)), 0.5996)
        self.assertNotIn("0.600", line[0])
        self.assertIn("Outcome: inconclusive.", md)
        self.assertIn("- " + REASON_KAPPA, md)


class NonFiniteDecideTests(unittest.TestCase):
    """K2: decide is the second line of defence against a non-finite figure."""

    def test_a_nan_kappa_and_an_infinite_bound_raise(self):
        with self.assertRaises(ValueError):
            report.decide(0.8, 0.9, 25, 0, float("nan"))
        for lo, hi in ((float("inf"), 0.9), (0.8, float("-inf")), (float("nan"), 0.9)):
            with self.subTest(lo=lo, hi=hi), self.assertRaises(ValueError):
                report.decide(lo, hi, 25, 0, 0.9)
        self.assertEqual(report.decide(None, None, 25, 0, 0.9), ("inconclusive", [REASON_INTERVAL]))  # None stays allowed


class ExcludedFixtureTextTests(unittest.TestCase):
    """K4: an excluded fixture's reason is free text; it prints as an indented block and cannot make a heading."""

    def test_a_reason_with_a_heading_stays_in_the_block(self):
        run, scores = final_world()
        final = final_input()
        final["record"]["excluded_fixtures"] = {"heldout-901-x": {"reason": "bad\n\n## Outcome: supports the prediction."}}
        lines = report.build_markdown(run, scores, final).splitlines()
        self.assertFalse([ln for ln in lines if ln.startswith("## Outcome")])
        at = lines.index("Excluded fixtures:")
        self.assertEqual(lines[at + 1:at + 5], ["", "    heldout-901-x: bad", "    ", "    ## Outcome: supports the prediction."])
        self.assertEqual(len(re.findall(r"^Outcome: ", "\n".join(lines), re.M)), 1)

    def test_no_excluded_fixture_reads_none(self):
        run, scores = final_world()
        md = report.build_markdown(run, scores, final_input())
        self.assertIn("\nExcluded fixtures: none.\n", md)

    def test_each_primary_question_statement_is_its_own_paragraph(self):
        run, scores = final_world()
        lines = report.build_markdown(run, scores, final_input()).splitlines()
        start = lines.index("## The primary question")
        end = next(i for i, ln in enumerate(lines) if i > start and ln.startswith("Excluded fixtures"))
        section = lines[start:end]
        self.assertGreater(len([ln for ln in section if ln]), 7, section)
        for a, b in zip(section, section[1:]):
            self.assertFalse(a and b, "two statements share a paragraph: %r / %r" % (a, b))


class FinalReportTests(unittest.TestCase):
    """D5: the held-out final report end to end, on the seal world of tests/test_exp0001_seal.py."""

    def setUp(self):
        self.a = st.AfterTheSealTests("launches")
        self.a.setUp()
        self.addCleanup(self.a.doCleanups)
        self.assertEqual(self.a.final_run()[0], 0)
        self.assertEqual(self.a.final_score()[0], 0)
        self.audit_json = self.a.tmp / "audit.json"
        self.audit_json.write_text(json.dumps({"n": 12, "kappa": 0.9}), encoding="utf-8")

    def write(self, out_name, **kw):
        out = io.StringIO()
        code = report.write_report(self.a.final, self.a.tmp / out_name, out, **kw)
        return code, out.getvalue()

    def test_without_the_audit_or_the_seal_it_refuses_and_writes_nothing(self):
        for kw in ({"seal_dir": self.a.seal_dir}, {"audit_path": self.audit_json}, {}):
            with self.subTest(kw=sorted(kw)):
                code, text = self.write("refused", **kw)
                self.assertEqual(code, 2, text)
                self.assertIn("report: REFUSED a held-out final report needs --seal and --audit", text)
                self.assertFalse((self.a.tmp / "refused").exists())

    def test_a_non_finite_kappa_or_a_bad_n_is_exit_1_with_a_label(self):
        # K2: NaN and Infinity are written as JSON text, which Python's json reads
        kappa_label = "audit.json kappa is not a finite number"
        n_label = "audit.json n is not an integer of at least 1"
        cases = [('{"n": 12, "kappa": NaN}', kappa_label), ('{"n": 12, "kappa": Infinity}', kappa_label),
                 ('{"n": 12, "kappa": true}', "audit.json missing or unreadable"),
                 ('{"n": 0, "kappa": 0.9}', n_label), ('{"n": -1, "kappa": 0.9}', n_label),
                 ('{"n": 1.5, "kappa": 0.9}', n_label), ('{"n": true, "kappa": 0.9}', "audit.json missing or unreadable")]
        for text, label in cases:
            self.audit_json.write_text(text, encoding="utf-8")
            with self.subTest(text=text):
                with self.assertRaises(report.ReportError) as caught:
                    report.load_final(self.a.seal_dir, self.audit_json)
                self.assertEqual(str(caught.exception), label)
                code, shown = self.write("nonfinite", seal_dir=self.a.seal_dir, audit_path=self.audit_json)
                self.assertEqual(code, 1, shown)
                self.assertIn("report: STOP " + label, shown)
                self.assertFalse((self.a.tmp / "nonfinite").exists())

    def test_a_scores_file_without_labels_or_multi_value_is_exit_1_and_writes_nothing(self):
        # K3
        path = self.a.final / "scores.json"
        original = json.loads(path.read_text(encoding="utf-8"))
        for key in ("labels", "multi_value"):
            self.assertIn(key, original)
            path.write_text(json.dumps({k: v for k, v in original.items() if k != key}), encoding="utf-8")
            with self.subTest(key=key):
                code, shown = self.write("noscores", seal_dir=self.a.seal_dir, audit_path=self.audit_json)
                self.assertEqual(code, 1, shown)
                self.assertIn("report: STOP scores.json has no %s: re-run score" % key, shown)
                self.assertFalse((self.a.tmp / "noscores").exists())

    def test_a_repair_log_path_outside_the_seal_folder_is_exit_1_with_a_label(self):
        # K6
        seal_json = Path(self.a.seal_dir) / "seal.json"
        data = json.loads(seal_json.read_text(encoding="utf-8"))
        data["repair_log"]["path"] = "../outside.txt"
        seal_json.write_text(json.dumps(data), encoding="utf-8")
        (Path(self.a.seal_dir).parent / "outside.txt").write_text("a real file, but outside\n", encoding="utf-8")
        code, shown = self.write("outside", seal_dir=self.a.seal_dir, audit_path=self.audit_json)
        self.assertEqual(code, 1, shown)
        self.assertIn("report: STOP repair log path is outside the seal folder", shown)
        self.assertFalse((self.a.tmp / "outside").exists())

    def test_an_unreadable_audit_is_exit_1_with_a_label(self):
        code, text = self.write("bad", seal_dir=self.a.seal_dir, audit_path=self.a.tmp / "no-such-audit.json")
        self.assertEqual(code, 1, text)
        self.assertIn("audit.json missing or unreadable", text)
        self.assertFalse((self.a.tmp / "bad").exists())

    def test_the_report_holds_the_new_sections_in_order(self):
        code, text = self.write("one", seal_dir=self.a.seal_dir, audit_path=self.audit_json)
        self.assertEqual(code, 0, text)
        md = (self.a.tmp / "one" / "REPORT.md").read_text(encoding="utf-8")
        positions = [md.index(h) for h in ["## Judge sanity controls"] + NEW_HEADINGS]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(len(re.findall(r"^Outcome: ", md, re.M)), 1)
        self.assertIn("Outcome: not determined.", md)  # two fixtures are sealed in this world
        self.assertIn("- " + REASON_SEALED, md)
        self.assertIn("audit n < 100: 12 answers", md)
        self.assertIn("    repair log: one fixture reworded", md)
        for line in st.COUNT_RULE.splitlines():
            self.assertIn("    " + line if line else "", md)
        self.assertNotIn("```", md.split(NEW_HEADINGS[2])[1].split("\n## ")[0])  # an indented block, never a fenced one
        self.assertIn(EXPLORATORY_HEADING, md)
        self.assertNotIn("## Paired differences against the baseline", md)
        self.assertIn("(P:164)", md)

    def test_the_caveat_goes_away_at_100_answers(self):
        self.audit_json.write_text(json.dumps({"n": 100, "kappa": 0.9}), encoding="utf-8")
        self.write("hundred", seal_dir=self.a.seal_dir, audit_path=self.audit_json)
        md = (self.a.tmp / "hundred" / "REPORT.md").read_text(encoding="utf-8")
        self.assertNotIn("audit n < 100", md)

    def test_the_same_inputs_give_the_same_bytes_twice(self):
        for name in ("a", "b"):
            self.assertEqual(self.write(name, seal_dir=self.a.seal_dir, audit_path=self.audit_json)[0], 0)
        self.assertEqual((self.a.tmp / "a" / "REPORT.md").read_bytes(), (self.a.tmp / "b" / "REPORT.md").read_bytes())

    def test_the_cli_passes_audit_through(self):
        out = io.StringIO()
        with mock.patch.dict("os.environ", self.a.environ), mock.patch("sys.stdout", new=out):
            code = st.run_cli.main(["report", "--from", str(self.a.final), "--fixtures-root", str(self.a.store),
                                    "--seal", str(self.a.seal_dir), "--audit", str(self.audit_json),
                                    "--out", str(self.a.tmp / "cli")])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("Outcome: ", (self.a.tmp / "cli" / "REPORT.md").read_text(encoding="utf-8"))


class DevReportTests(unittest.TestCase):
    """D6 and the dev rule: with final=None the report is today's, and the new sections and labels are absent."""

    def test_without_final_nothing_new_appears(self):
        run = json.loads((TREE / "run.json").read_text(encoding="utf-8"))
        scores = json.loads((TREE / "scores.json").read_text(encoding="utf-8"))
        md = report.build_markdown(run, scores)
        self.assertEqual(md.encode("utf-8"), (TREE / "REPORT.md").read_bytes())  # also pinned in test_exp0001_report.py
        for heading in NEW_HEADINGS + [EXPLORATORY_HEADING]:
            self.assertNotIn(heading, md)
        self.assertIn("## Paired differences against the baseline", md)
        self.assertIn("## A/A split", md)

    def test_a_final_report_drops_an_empty_aa_table_and_a_dev_report_keeps_it(self):
        run, scores = final_world()
        self.assertIn("## A/A split", report.build_markdown(run, scores, final_input()))  # rows with data are kept
        for by_fixture in scores["per_seed"].values():
            for fid, by_seed in by_fixture.items():
                by_fixture[fid] = {s: v for s, v in by_seed.items() if s in ("1", "2")}  # too few seeds: every row has n 0
        self.assertNotIn("## A/A split", report.build_markdown(run, scores, final_input()))
        self.assertIn("## A/A split", report.build_markdown(run, scores))


if __name__ == "__main__":
    unittest.main()
