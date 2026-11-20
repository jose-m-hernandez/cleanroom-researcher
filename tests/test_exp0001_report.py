"""Tests for evals/report.py. The report is regenerated from a small committed synthetic results tree and compared
byte for byte. Run with: python3 -m unittest discover tests
"""

from __future__ import annotations

import copy
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests import exp0001_paths  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]

import report  # noqa: E402

TREE = ROOT / "tests" / "data" / "exp0001-synthetic"


def read(path) -> str:
    return Path(path).read_text(encoding="utf-8")


class ReportBytesTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_regenerating_from_the_synthetic_tree_reproduces_the_committed_bytes(self):
        shutil.copytree(TREE, self.tmp / "src")  # a results tree inside the clone is refused, so read a copy
        self.assertEqual(report.write_report(self.tmp / "src", self.tmp / "r1", io.StringIO()), 0)
        for name in ("REPORT.md",):
            self.assertEqual((self.tmp / "r1" / name).read_bytes(), (TREE / name).read_bytes(), name)

    def test_two_runs_are_byte_identical(self):
        shutil.copytree(TREE, self.tmp / "src")  # a results tree inside the clone is refused, so read a copy
        report.write_report(self.tmp / "src", self.tmp / "a", io.StringIO())
        report.write_report(self.tmp / "src", self.tmp / "b", io.StringIO())
        for name in ("REPORT.md",):
            self.assertEqual((self.tmp / "a" / name).read_bytes(), (self.tmp / "b" / name).read_bytes())

    def test_only_run_json_and_scores_json_are_read(self):
        only = self.tmp / "only"
        only.mkdir()
        for name in ("run.json", "scores.json"):
            shutil.copyfile(TREE / name, only / name)
        report.write_report(only, self.tmp / "out", io.StringIO())
        self.assertEqual((self.tmp / "out" / "REPORT.md").read_bytes(), (TREE / "REPORT.md").read_bytes())
        report.write_report(only, None, io.StringIO())  # default destination is the results directory itself
        self.assertTrue((only / "REPORT.md").is_file())

    def test_cli_twice_and_cmp(self):
        shutil.copytree(TREE, self.tmp / "from")  # a --from inside the clone is refused, so the tree is read from a copy
        for name in ("r1", "r2"):
            proc = subprocess.run([sys.executable, exp0001_paths.RUN, "report", "--from", str(self.tmp / "from"),
                                   "--out", str(self.tmp / name)], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertRegex(proc.stdout.strip(), r"^report: wrote REPORT.md \(\d+ lines\)$")
        cmp = subprocess.run(["cmp", str(self.tmp / "r1" / "REPORT.md"), str(self.tmp / "r2" / "REPORT.md")])
        self.assertEqual(cmp.returncode, 0)

    def test_missing_or_incomplete_inputs_stop_with_a_label(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        self.assertEqual(report.write_report(empty, self.tmp / "o", io.StringIO()), 1)
        broken = self.tmp / "broken"
        broken.mkdir()
        shutil.copyfile(TREE / "run.json", broken / "run.json")
        (broken / "scores.json").write_text(json.dumps({"arms": ["floor"]}), encoding="utf-8")
        self.assertEqual(report.write_report(broken, self.tmp / "o", io.StringIO()), 1)
        self.assertFalse((self.tmp / "o").exists())


class ReportContentTests(unittest.TestCase):
    def setUp(self):
        self.run = json.loads(read(TREE / "run.json"))
        self.scores = json.loads(read(TREE / "scores.json"))
        self.md = report.build_markdown(self.run, self.scores)

    def test_every_required_table_is_present(self):
        for heading in ("## Judge sanity controls", "## Per-arm metrics", "## Scorable probes",
                        "## Paired differences against the baseline", "## A/A split", "## Probe-context tokens", "## Cost",
                        "## Failure gallery"):
            self.assertIn(heading, self.md)
        for column in ("Wilcoxon p", "Holm p", "Within-fixture SD", "Ratio to baseline", "Wall-clock s", "95% CI"):
            self.assertIn(column, self.md)
        self.assertIn("API-price equivalent", self.md)
        self.assertIn("Dropped 6 of 48 non-abstention probes", self.md)

    def test_numbers_have_fixed_formatting_and_no_float_noise(self):
        self.assertNotRegex(self.md, r"-0\.0+\b")
        self.assertNotRegex(self.md, r"\bnan\b|\binf\b|None")
        self.assertEqual(report.fmt(None), "n/a")
        self.assertEqual(report.fmt(-0.0001), "0.000")
        self.assertEqual(report.fmt(0.5), "0.500")
        self.assertEqual(report.fmt(12.345, 1), "12.3")
        for number in re.findall(r"\b\d+\.\d+\b", self.md):
            self.assertLessEqual(len(number.split(".")[1]), 4, number)

    def test_the_wilcoxon_and_holm_columns_match_the_statistics_module(self):
        import stats
        diffs = stats.paired_diffs({f: v["primary"] for f, v in self.scores["per_fixture"]["oracle"].items()},
                                   {f: v["primary"] for f, v in self.scores["per_fixture"]["baseline"].items()})
        p = stats.wilcoxon(diffs)["p"]
        self.assertEqual(len(diffs), 6)
        self.assertEqual(p, 0.03125)  # printed to four places: 0.0312
        self.assertRegex(self.md, r"\| oracle \| Primary \| 6 \| 0\.507 \| \[[-0-9., ]+\] \| by construction \| by construction \| ")

    def test_floor_rows_and_the_oracle_primary_are_by_construction_and_outside_the_holm_family(self):
        import stats
        rows = {tuple(c.strip() for c in ln.strip().strip("|").split("|")[:2]): [c.strip() for c in ln.strip().strip("|").split("|")]
                for ln in self.md.split("## Paired differences")[1].split("## A/A split")[0].splitlines()
                if ln.startswith("| ") and "---" not in ln and not ln.startswith("| Arm")}
        for key, cells in rows.items():
            fixed = key[0] == "floor" or key == ("oracle", "Primary")
            if cells[2] != "0":  # a row with no paired difference has no p-value cells at all
                self.assertEqual(cells[5:7] == ["by construction", "by construction"], fixed, key)
        # the family is the other rows: Holm over exactly their Wilcoxon p-values
        family = [(k, c) for k, c in rows.items() if c[5] != "by construction" and c[5] != "n/a"]
        self.assertGreaterEqual(len(family), 2)
        pvalues = []
        for (arm, label), _ in family:
            metric = label.lower()
            diffs = stats.paired_diffs({f: v[metric] for f, v in self.scores["per_fixture"][arm].items()},
                                       {f: v[metric] for f, v in self.scores["per_fixture"]["baseline"].items()})
            pvalues.append(stats.wilcoxon(diffs)["p"])
        for (key, cells), holm in zip(family, stats.holm(pvalues)):
            self.assertEqual(cells[6], report.fmt(holm, 4), key)

    def test_the_judge_comes_from_scores_json_with_run_json_as_the_fallback(self):
        self.assertIn("judge judge-model.", self.md)
        scores = copy.deepcopy(self.scores)
        scores["judge_model"] = "claude-from-scores"
        md = report.build_markdown(self.run, scores)
        self.assertIn("judge claude-from-scores.", md)
        self.assertNotIn("judge-model", md)

    def test_the_scorable_table_has_an_excluded_column(self):
        self.assertIn("| Fixture | Scorable | Dropped | Floor answered correctly | Oracle in minority | Excluded |", self.md)
        self.assertIn("excluded by the spec and not counted: 0.", self.md)  # a scores file without the key reads as none
        scores = copy.deepcopy(self.scores)
        f = scores["fixtures"][0]
        scores["excluded"] = {g: ([] if g != f else ["p6"]) for g in scores["fixtures"]}
        md = report.build_markdown(self.run, scores)
        row = next(l for l in md.splitlines() if l.startswith(f"| {f} |") and l.count("|") == 7)
        self.assertTrue(row.rstrip().endswith("| 1 |"), row)
        self.assertIn("excluded by the spec and not counted: 1.", md)

    def test_the_table_pipe_in_an_answer_is_escaped(self):
        self.assertIn("The config file \\| not the environment", self.md)

    def split_scores(self):
        """The synthetic scores plus the split: 3 fixtures with scorable code-tagged probes, 5 with untagged ones."""
        scores = copy.deepcopy(self.scores)
        fids = scores["fixtures"]
        code = [1, 0, 2, 0, 3, 0]
        nocode = [2, 2, 2, 2, 2, 0]
        scores["scorable_split"] = {f: {"code": c, "nocode": n} for f, c, n in zip(fids, code, nocode)}
        values = {"floor": (0.0, 0.0), "baseline": (0.5, 0.25), "oracle": (1.0, 1.0)}
        for arm, (with_code, without) in values.items():
            for f, c, n in zip(fids, code, nocode):
                scores["per_fixture"][arm][f]["primary_code"] = with_code if c else None
                scores["per_fixture"][arm][f]["primary_nocode"] = without if n else None
        return scores

    def test_a_run_without_the_split_has_no_split_section(self):
        self.assertNotIn("## Primary by whether the repo states the answer", self.md)
        self.assertNotIn("code_answers", self.md)

    def test_scores_with_the_split_render_the_section_between_scorable_and_paired(self):
        md = report.build_markdown(self.run, self.split_scores())
        heading = "## Primary by whether the repo states the answer"
        self.assertEqual(md.count(heading), 1)
        self.assertLess(md.index("## Scorable probes"), md.index(heading))
        self.assertLess(md.index(heading), md.index("## Paired differences against the baseline"))
        section = md[md.index(heading):md.index("## Paired differences against the baseline")]
        self.assertIn("Probe sessions see only CLAUDE.md. Each probe is tagged by whether a file in the fixture's repo, "
                      "other than CLAUDE.md, states its answer (code_answers in the spec).", section)
        self.assertIn("| Arm | Primary, repo states it | Primary, session only |", section)
        self.assertRegex(section, r"\| floor \| 0\.000 \[[0-9., ]+\] \| 0\.000 \[[0-9., ]+\] \|")
        self.assertRegex(section, r"\| baseline \| 0\.500 \[[0-9., ]+\] \| 0\.250 \[[0-9., ]+\] \|")
        self.assertRegex(section, r"\| oracle \| 1\.000 \[[0-9., ]+\] \| 1\.000 \[[0-9., ]+\] \|")
        # 1 + 2 + 3 = 6 code probes in 3 fixtures; 5 * 2 = 10 other probes in 5 fixtures
        self.assertTrue(section.endswith("Scorable probes: repo states it 6, session only 10. Fixtures with at least one: 3 and 5.\n\n"))

    def test_the_split_section_leaves_a_group_with_no_fixture_as_n_a(self):
        scores = self.split_scores()
        for f in scores["fixtures"]:
            scores["scorable_split"][f]["code"] = 0
            for arm in scores["per_fixture"].values():
                arm[f]["primary_code"] = None
        md = report.build_markdown(self.run, scores)
        self.assertIn("| baseline | n/a | 0.250 [", md)
        self.assertIn("Scorable probes: repo states it 0, session only 10. Fixtures with at least one: 0 and 5.", md)

    def test_a_run_without_a_baseline_still_reports(self):
        scores = copy.deepcopy(self.scores)
        scores["arms"] = ["floor", "oracle"]
        del scores["per_fixture"]["baseline"]
        del scores["per_seed"]["baseline"]
        md = report.build_markdown(self.run, scores)
        self.assertIn("No baseline arm in this run.", md)
        self.assertIn("| floor | 0.0 | n/a |", md)

    def test_undefined_metrics_print_as_n_a(self):
        scores = copy.deepcopy(self.scores)
        for arm in scores["per_fixture"].values():
            for f in arm.values():
                f["primary"] = None
        for arm in scores["per_seed"].values():
            for f in arm.values():
                for cell in f.values():
                    cell["primary"] = None
        scores["scorable_counts"] = {f: 0 for f in scores["fixtures"]}
        md = report.build_markdown(self.run, scores)
        self.assertIn("Fixtures with no scorable probe: 6", md)
        self.assertIn("| baseline | Primary | 0 | n/a |", md)

    def test_the_synthetic_tree_is_generic(self):
        text = "\n".join(read(p) for p in TREE.iterdir())
        for bad in ("/Users/", "/home/", "/private/", "@", "Documents"):
            self.assertNotIn(bad, text)


LINE = ("Superseded after the repair re-check: %d excluded cells of %d fixtures are not counted above; "
        "they are listed in superseded.json.")


def record(cells=0, ids=1) -> dict:
    return {"version": 1, "stamp": "20261119T101112Z", "scoring_occasion": 2,
            "ids": [f"heldout-{n:03d}-x" for n in range(1, ids + 1)],
            "excluded_cells": [{"kind": "probe", "arm": "floor", "fixture": "heldout-001-x", "seed": n, "checks": ["c"]}
                               for n in range(1, cells + 1)]}


class SupersededLineTests(unittest.TestCase):
    """One line says how many superseded excluded cells the report no longer counts."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.run = json.loads(read(TREE / "run.json"))
        self.scores = json.loads(read(TREE / "scores.json"))
        self.counted = dict(self.run, proof_counts={"isolation_reruns": 2, "excluded_after_rerun": 1,
                                                    "outcome_only_by_check": {}},
                            excluded_cells=[{"kind": "probe", "arm": "oracle", "fixture": "dev-001-x", "seed": 2,
                                             "checks": ["markers_found"]}])

    def results(self, run, superseded=None) -> Path:
        folder = self.tmp / "src"
        shutil.copytree(TREE, folder)
        (folder / "run.json").write_text(json.dumps(run), encoding="utf-8")
        if superseded is not None:
            (folder / "superseded.json").write_text(superseded if isinstance(superseded, str) else json.dumps(superseded),
                                                    encoding="utf-8")
        return folder

    def test_without_the_record_the_report_is_byte_for_byte_what_it_was(self):
        old = report.build_markdown(self.run, self.scores)
        self.assertEqual(report.build_markdown(self.run, self.scores, None, None), old)
        self.assertEqual(report.build_markdown(self.counted, self.scores), report.build_markdown(self.counted, self.scores, None))
        folder = self.results(self.run)
        self.assertEqual(report.write_report(folder, self.tmp / "o1", io.StringIO()), 0)
        self.assertEqual((self.tmp / "o1" / "REPORT.md").read_bytes(), (TREE / "REPORT.md").read_bytes())
        self.assertNotIn("Superseded", old)

    def test_the_line_has_the_exact_text_and_numbers(self):
        for cells, ids in ((3, 2), (1, 1), (0, 1)):  # printed even when the first number is 0
            with self.subTest(cells=cells, ids=ids):
                md = report.build_markdown(self.counted, self.scores, None, record(cells, ids))
                self.assertEqual(md.count("Superseded after the repair re-check"), 1)
                self.assertIn("\n" + LINE % (cells, ids) + "\n", md)

    def test_the_line_sits_after_the_isolation_paragraph_and_before_the_excluded_bullets(self):
        md = report.build_markdown(self.counted, self.scores, None, record(3, 2))
        at = lambda text: md.index(text)  # noqa: E731
        self.assertLess(at("Isolation re-runs: 2."), at("Superseded after"))
        self.assertLess(at("Superseded after"), at("- Excluded: dev-001-x"))
        self.assertLess(at("- Excluded: dev-001-x"), at("## Judge sanity controls"))
        plain = report.build_markdown(self.counted, self.scores)
        self.assertEqual(md.replace(LINE % (3, 2) + "\n\n", "", 1), plain)  # nothing else changed

    def test_without_proof_counts_the_line_is_its_own_paragraph_after_the_header_block(self):
        md = report.build_markdown(self.run, self.scores, None, record(2, 1))
        self.assertNotIn("## Per-run proof counts", md)
        self.assertLess(md.index("Unit of analysis:"), md.index("Superseded after"))
        self.assertLess(md.index("Superseded after"), md.index("## Judge sanity controls"))
        self.assertIn("\n\n" + LINE % (2, 1) + "\n\n## Judge sanity controls", md)
        self.assertEqual(md.replace(LINE % (2, 1) + "\n\n", "", 1), report.build_markdown(self.run, self.scores))

    def test_write_report_reads_the_record_from_the_results_folder(self):
        folder = self.results(self.counted, record(3, 2))
        # the record opens scoring occasion 2: the report accepts only a scores.json stamped for it
        (folder / "score-passes-occasion-2.json").write_text(json.dumps(
            {"normal": {"started": 1, "aborted": 0}, "final": {"started": 0, "aborted": 0}}), encoding="utf-8")
        (folder / "scores.json").write_text(json.dumps(dict(self.scores, **{"pass": {"id": 1, "mode": "normal", "occasion": 2}})),
                                            encoding="utf-8")
        self.assertEqual(report.write_report(folder, self.tmp / "o2", io.StringIO()), 0)
        self.assertIn(LINE % (3, 2), read(self.tmp / "o2" / "REPORT.md"))
        again = self.tmp / "o3"
        self.assertEqual(report.write_report(folder, again, io.StringIO()), 0)
        self.assertEqual((again / "REPORT.md").read_bytes(), (self.tmp / "o2" / "REPORT.md").read_bytes())  # deterministic

    def test_an_unreadable_or_malformed_record_is_a_report_error(self):
        for text in ("{oops", "[]", json.dumps({"ids": []}), json.dumps({"ids": {}, "excluded_cells": []}),
                     json.dumps({"ids": [], "excluded_cells": 3})):
            with self.subTest(text=text):
                shutil.rmtree(self.tmp / "src", ignore_errors=True)
                folder = self.results(self.run, text)
                shown = io.StringIO()
                self.assertEqual(report.write_report(folder, self.tmp / "o4", shown), 1)
                self.assertRegex(shown.getvalue(), r"^report: STOP superseded\.json (unreadable|malformed)\n$")
                self.assertFalse((self.tmp / "o4" / "REPORT.md").exists())


if __name__ == "__main__":
    unittest.main()
