"""Offline tests for `author --repair` of EXP-0001: the runner selects the evidence from the floor and oracle scores,
one tool-less call lets the blind agent repair the selected fixtures, and the runner writes the new store and the log.

Nothing here calls the real claude: the fake runner of tests/test_exp0001_author.py stands in for the launch.
Run with: python3 -m unittest tests.test_exp0001_repair
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import exp0001_paths  # noqa: F401

import claude_run  # noqa: E402
import fixtures  # noqa: E402
import judge  # noqa: E402
import run as run_cli  # noqa: E402
import seal  # noqa: E402
from tests import test_exp0001_author as at  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEV_SPEC = ROOT / "tests" / "data" / "exp0001-fixtures" / "dev" / "dev-001-tide-table" / "spec.json"
TIDE, FERRY = "heldout-001-tide-table", "heldout-002-ferry-log"
PIDS = [f"p{n}" for n in range(1, 11)]
SEEDS = ("1", "2", "3")
CLEAN_SENTINEL = "CLEAN-SPEC-SENTINEL"
CLEAN_SPEC_TEXT = CLEAN_SENTINEL  # the clean fixture's summary: it must never reach the agent
NEW_QUESTION = "Which time standard do the printed harbour times follow?"


def make_spec(fid, summary, excluded=()):
    """A valid held-out-shaped spec, taken from the development fixture the repository already tests with."""
    spec = json.loads(DEV_SPEC.read_text(encoding="utf-8"))
    spec["id"], spec["split"], spec["summary"] = fid, "heldout", summary
    for probe in spec["probes"]:
        if probe["id"] in excluded:
            probe["excluded"] = "not a fair question"
    return spec


def spec_text(spec):
    return json.dumps(spec, indent=1) + "\n"


def answer_of(arm, fid, pid, seed):
    return f"{arm}-answer-{fid}-{pid}-s{seed}"


def labels_of(fid):
    """{arm: {seed: {probe id: label}}}. The default is the scorable shape: the floor wrong, the oracle right."""
    table = {arm: {s: {pid: ("incorrect" if arm == "floor" else "correct") for pid in PIDS} for s in SEEDS}
             for arm in ("floor", "oracle")}
    if fid == TIDE:
        table["floor"]["2"]["p1"] = "correct"  # the floor right in one seed: selected
        for s in ("2", "3"):
            table["oracle"][s]["p2"] = "incorrect"  # the oracle right in 1 of 3: selected
        table["oracle"]["3"]["p3"] = "incorrect"  # the oracle right in 2 of 3, the floor in none: not selected
        table["floor"]["3"]["p5"] = None  # a missing label: not selected, counted
        table["floor"]["1"]["p6"] = "correct"  # p6 is excluded in the spec: not selected
        table["floor"]["1"]["p9"] = "correct"  # an abstention probe: not selected
    return table


def build_world(tmp):
    """(results dir, fixture store) under tmp: two fixtures, TIDE with selected probes and FERRY clean."""
    results, store = tmp / "results", tmp / "srcstore"
    scores = {"version": 1, "fixtures": [TIDE, FERRY], "arms": ["floor", "oracle"], "seeds": 3,
              "pass": {"mode": "normal", "id": 1}, "labels": {"floor": {}, "oracle": {}}}
    for fid, summary, excluded in ((TIDE, "A harbour tide table.", ("p6",)), (FERRY, CLEAN_SPEC_TEXT, ())):
        folder = store / fid
        (folder / "repo").mkdir(parents=True)
        (folder / "spec.json").write_text(spec_text(make_spec(fid, summary, excluded)), encoding="utf-8")
        (folder / "transcript.jsonl").write_bytes(b'{"k": 1}\n{"k": 2}\n')
        (folder / "repo" / "notes.txt").write_bytes(b"line one\r\nline two\n\x00\xff")
        table = labels_of(fid)
        for arm in ("floor", "oracle"):
            scores["labels"][arm][fid] = table[arm]
            (results / "probes" / arm / fid).mkdir(parents=True)
            for s in SEEDS:
                answers = {pid: answer_of(arm, fid, pid, s) for pid in PIDS}
                (results / "probes" / arm / fid / f"s{s}.json").write_text(
                    json.dumps({"answers": answers, "ok": True}), encoding="utf-8")
    (results / "run.json").write_text(json.dumps({"arms": ["floor", "oracle"], "fixtures": [TIDE, FERRY], "seeds": 3,
                                                  "split": "heldout"}), encoding="utf-8")
    (results / "scores.json").write_text(json.dumps(scores), encoding="utf-8")
    return results, store


def repaired_spec():
    spec = make_spec(TIDE, "A harbour tide table.", ("p6",))
    spec["probes"][0]["question"] = NEW_QUESTION
    return spec


def tree(root):
    """{relative path: bytes} for every file under root."""
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(Path(root).rglob("*")) if p.is_file()}


class RepairCase(at.AuthorCase):
    """The author tests' world (fake HOME, runner and config) plus a results directory and a fixture store."""

    def setUp(self):
        import repair
        self.repair_mod = repair
        super().setUp()
        self.results, self.root = build_world(self.tmp)
        self.new = self.tmp / "newstore"
        self.reply = block_of(repaired_spec())

    def repair(self, **kw):
        args = dict(source=self.results, fixtures_root=self.root, guide_path=self.tmp / "g.md", out_path=self.new,
                    environ=self.environ, runner=self.runner)
        args.update(kw)
        stream = io.StringIO()
        code = at.author.command_repair(out=stream, **args)
        return code, stream.getvalue()

    def scores(self):
        return json.loads((self.results / "scores.json").read_text(encoding="utf-8"))

    def write_scores(self, scores):
        (self.results / "scores.json").write_text(json.dumps(scores), encoding="utf-8")

    def counter(self):
        return judge.load_stats(self.environ, "author")["count"]

    def ledger(self, rows):
        path = claude_run.eval_root(self.environ) / "author-runs.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows), encoding="utf-8")

    def parent_leftovers(self):
        return [p.name for p in self.tmp.iterdir() if p.name.startswith(".repair-")]

    def assertRefused(self, label, **kw):
        before = self.counter()
        code, text = self.repair(**kw)
        self.assertEqual(code, 2, text)
        self.assertIn(f"author: REFUSED {label}", text)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.counter(), before)
        return text

    def specs(self):
        return fixtures_specs(self.root)


def block_of(spec):
    return at.block(spec["id"], spec_text(spec))


def fixtures_specs(root):
    return {fid: fixtures.load_spec(Path(root) / fid / "spec.json") for fid in (TIDE, FERRY)}


class TestSelector(RepairCase, unittest.TestCase):
    def select(self):
        return self.repair_mod.select_evidence(self.results, self.scores(), self.specs())

    def keyed(self):
        return {(i["fixture"], i["probe"]): i for i in self.select()}

    def test_the_floor_right_in_any_seed_selects_the_probe(self):
        item = self.keyed()[(TIDE, "p1")]
        self.assertEqual(item["reasons"], ["floor_correct"])

    def test_the_oracle_right_in_one_of_three_selects_the_probe(self):
        item = self.keyed()[(TIDE, "p2")]
        self.assertEqual(item["reasons"], ["oracle_minority"])

    def test_the_oracle_right_in_two_of_three_and_the_floor_in_none_does_not(self):
        self.assertNotIn((TIDE, "p3"), self.keyed())

    def test_an_abstention_probe_and_an_excluded_probe_are_not_selected(self):
        keys = self.keyed()
        self.assertNotIn((TIDE, "p9"), keys)
        self.assertNotIn((TIDE, "p6"), keys)

    def test_the_oracle_right_in_exactly_half_of_its_seeds_selects_the_probe(self):
        scores = self.scores()
        for arm in ("floor", "oracle"):
            scores["labels"][arm][TIDE]["4"] = {pid: dict(scores["labels"][arm][TIDE]["1"])[pid] for pid in PIDS}
        for s, label in (("1", "correct"), ("2", "correct"), ("3", "incorrect"), ("4", "incorrect")):
            scores["labels"]["oracle"][TIDE][s]["p3"] = label  # 2 of 4: not a majority, so score would drop it
        items = {(i["fixture"], i["probe"]): i for i in
                 self.repair_mod.select_evidence(self.results, scores, self.specs())}
        self.assertEqual(items[(TIDE, "p3")]["reasons"], ["oracle_minority"])
        self.assertEqual(items[(TIDE, "p3")]["seeds"]["4"]["floor"], None)  # no session file for that seed

    def test_a_missing_label_is_not_selected_and_is_counted(self):
        self.assertNotIn((TIDE, "p5"), self.keyed())
        self.assertEqual(self.repair_mod.count_missing(self.scores(), self.specs()), 1)

    def test_exactly_the_probes_that_score_would_drop_for_these_reasons_are_selected(self):
        self.assertEqual(sorted(self.keyed()), [(TIDE, "p1"), (TIDE, "p2")])

    def test_an_item_holds_the_question_the_gold_and_each_seeds_answers_and_labels(self):
        item = self.keyed()[(TIDE, "p1")]
        probe = self.specs()[TIDE]["probes"][0]
        self.assertEqual((item["question"], item["gold"]), (probe["question"], probe["answer"]))
        self.assertEqual(sorted(item["seeds"]), ["1", "2", "3"])
        self.assertEqual(item["seeds"]["2"], {"floor": answer_of("floor", TIDE, "p1", "2"), "floor_label": "correct",
                                              "oracle": answer_of("oracle", TIDE, "p1", "2"),
                                              "oracle_label": "correct"})


class TestEvidenceInThePrompt(RepairCase, unittest.TestCase):
    def prompt(self):
        code, text = self.repair()
        self.assertEqual(code, 0, text)
        ((prompt, _kw),) = self.calls
        return prompt

    def test_the_prompt_holds_the_selected_spec_bytes_the_question_the_gold_and_every_seeds_answers_and_labels(self):
        prompt = self.prompt()
        self.assertIn((self.root / TIDE / "spec.json").read_text(encoding="utf-8"), prompt)
        probe = self.specs()[TIDE]["probes"][0]
        self.assertIn(probe["question"], prompt)
        self.assertIn(probe["answer"], prompt)
        for pid in ("p1", "p2"):
            for s in SEEDS:
                self.assertIn(answer_of("floor", TIDE, pid, s), prompt)
                self.assertIn(answer_of("oracle", TIDE, pid, s), prompt)
        self.assertIn("correct", prompt)
        self.assertIn("incorrect", prompt)
        self.assertIn("GUIDE TEXT", prompt)
        self.assertIn("heldout-NNN-name", prompt)

    def test_the_prompt_holds_no_clean_fixture_no_unselected_answer_no_baseline_and_no_path(self):
        prompt = self.prompt()
        self.assertNotIn(CLEAN_SENTINEL, prompt)
        self.assertNotIn(FERRY, prompt)
        self.assertNotIn(answer_of("floor", TIDE, "p4", "1"), prompt)
        self.assertNotIn(answer_of("oracle", TIDE, "p3", "1"), prompt)
        self.assertNotIn("baseline", prompt.casefold())
        self.assertNotIn("skill", prompt.casefold())
        self.assertNotIn(str(self.tmp), prompt)
        self.assertNotIn(str(self.root), prompt)

    def test_the_prompt_is_built_the_same_way_twice(self):
        evidence = self.repair_mod.select_evidence(self.results, self.scores(), self.specs())
        texts = {TIDE: (self.root / TIDE / "spec.json").read_text(encoding="utf-8")}
        first = self.repair_mod.build_prompt("GUIDE TEXT", texts, evidence)
        self.assertEqual(first, self.repair_mod.build_prompt("GUIDE TEXT", texts, evidence))


class TestGuards(RepairCase, unittest.TestCase):
    def test_a_baseline_folder_in_the_results_is_refused(self):
        (self.results / "probes" / "baseline" / TIDE).mkdir(parents=True)
        self.assertRefused("the results hold an arm other than floor and oracle")

    def test_a_baseline_arm_in_run_json_is_refused(self):
        (self.results / "run.json").write_text(json.dumps({"arms": ["floor", "baseline", "oracle"]}), encoding="utf-8")
        self.assertRefused("the results hold an arm other than floor and oracle")

    def test_an_unreadable_run_json_is_refused(self):
        (self.results / "run.json").unlink()
        self.assertRefused("run.json is missing or unreadable")

    def test_a_missing_scores_json_is_refused(self):
        (self.results / "scores.json").unlink()
        self.assertRefused("scores.json is missing")

    def test_a_scores_json_from_the_wrong_mode_is_refused(self):
        (self.results / "score-passes.json").write_text(json.dumps(
            {"normal": {"started": 1, "aborted": 0}, "final": {"started": 1, "aborted": 0}}), encoding="utf-8")
        scores = self.scores()
        scores["pass"] = {"mode": "final", "id": 1}
        self.write_scores(scores)
        self.assertRefused("scores.json is not from the last completed normal scoring pass; run score again")

    def test_a_scores_json_that_scored_another_arm_is_refused(self):
        scores = self.scores()
        scores["arms"] = ["floor", "baseline", "oracle"]
        self.write_scores(scores)
        self.assertRefused("scores.json is not a floor and oracle scoring")

    def test_a_selected_fixture_that_is_a_replacement_is_refused(self):
        self.ledger([{"run": 1, "replaces": ["heldout-009-old"], "ids_written": [TIDE]}])
        self.assertRefused(f"fixture {TIDE} is a replacement (P:262)")

    def test_a_second_repair_call_is_refused_whatever_the_first_one_wrote(self):
        self.ledger([{"run": 1, "mode": "repair", "replaces": [], "ids_written": []}])
        self.assertRefused("a repair call has already been made (one round, P:252)")

    def test_nothing_selected_is_refused(self):
        scores = self.scores()
        scores["labels"]["floor"][TIDE]["2"]["p1"] = "incorrect"
        for s in ("2", "3"):
            scores["labels"]["oracle"][TIDE][s]["p2"] = "correct"
        self.write_scores(scores)
        self.assertRefused("no probe needs repair")

    def test_an_out_that_exists_is_refused_and_left_alone(self):
        self.new.mkdir()
        (self.new / "keep.txt").write_text("keep", encoding="utf-8")
        self.assertRefused("--out exists")
        self.assertEqual(tree(self.new), {"keep.txt": b"keep"})

    def test_an_out_that_is_a_dangling_symlink_is_refused(self):
        os.symlink(self.tmp / "nowhere", self.new)
        self.assertRefused("--out exists")

    def test_a_repair_log_path_that_exists_is_refused(self):
        Path(str(self.new) + ".repair-log.md").write_text("old", encoding="utf-8")
        self.assertRefused("the repair log path exists")
        self.assertFalse(self.new.exists())

    def test_an_out_inside_the_clone_is_refused(self):
        out = ROOT / "scratch-repair-out"
        before = self.counter()
        code, text = self.repair(out_path=out)
        self.assertEqual(code, 2, text)
        self.assertIn("inside the repository clone", text)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.counter(), before)
        self.assertFalse(out.exists())

    def test_a_selected_spec_that_does_not_validate_is_refused(self):
        (self.root / TIDE / "spec.json").write_text("{}", encoding="utf-8")
        self.assertRefused(f"spec invalid: {TIDE}")

    def test_a_store_that_holds_a_symlink_or_a_git_folder_is_refused(self):
        link = self.root / FERRY / "link"
        os.symlink(self.tmp / "g.md", link)
        self.assertRefused("the fixture store holds a symlink or a git folder")
        link.unlink()
        (self.root / FERRY / "repo" / ".git").mkdir()
        self.assertRefused("the fixture store holds a symlink or a git folder")

    def test_a_missing_source_or_store_is_refused(self):
        self.assertRefused("--from does not exist", source=self.tmp / "nope")
        self.assertRefused("--fixtures-root does not exist", fixtures_root=self.tmp / "nope")

    def test_a_refusal_writes_nothing_beside_out(self):
        self.new.mkdir()
        before = sorted(p.name for p in self.tmp.iterdir())
        self.assertRefused("--out exists")
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), before)

    def test_a_missing_live_switch_is_refused_before_any_call(self):
        del self.environ["CPR_EVAL_LIVE"]
        code, text = self.repair()
        self.assertEqual(code, 2)
        self.assertIn(at.LIVE_REFUSED, text)
        self.assertEqual(self.calls, [])


class TestSuccess(RepairCase, unittest.TestCase):
    def test_one_call_with_the_authoring_keywords_and_the_full_store_in_out(self):
        before_store = tree(self.root)
        code, text = self.repair()
        self.assertEqual(code, 0, text)
        self.assertIn("author: repaired 1 fixtures, 1 probes", text)
        ((_prompt, kw),) = self.calls
        self.assertEqual((kw["phase"], kw["kind"], kw["label"]), ("authoring", "probe", "author"))
        self.assertIs(kw["notools"], True)
        self.assertEqual(set(kw), {"label", "phase", "kind", "notools", "environ"})
        self.assertEqual(tree(self.root), before_store)  # the source store is never touched
        new = tree(self.new)
        self.assertEqual(sorted(new), sorted(before_store))
        for path, data in before_store.items():
            if path != f"{TIDE}/spec.json":
                self.assertEqual(new[path], data, path)
        body = spec_text(repaired_spec()).encode("utf-8")
        self.assertEqual(new[f"{TIDE}/spec.json"], body)
        self.assertNotEqual(new[f"{TIDE}/spec.json"], before_store[f"{TIDE}/spec.json"])
        self.assertIn(str(self.new), text)

    def test_the_log_is_written_beside_out_and_never_inside_it(self):
        code, text = self.repair()
        log = Path(str(self.new) + ".repair-log.md")
        self.assertTrue(log.is_file())
        self.assertIn(str(log), text)
        self.assertFalse([p for p in self.new.rglob("*") if "repair-log" in p.name])
        self.assertEqual(self.parent_leftovers(), [])

    def test_the_ledger_row_counter_and_spend(self):
        self.repair()
        (row,) = self.eval_file("author-runs.json")
        self.assertEqual((row["mode"], row["run"], row["isolation"], row["replaces"]), ("repair", 1, "PASS", []))
        self.assertEqual(row["repair_ids"], [TIDE])
        self.assertEqual(row["ids_written"], [TIDE])
        self.assertEqual(self.counter(), 1)
        self.assertIsNone(self.eval_file("author-replacements.json"))

    def test_the_call_is_charged_to_the_authoring_phase(self):
        with mock.patch.object(claude_run.subprocess, "Popen", at.FakeChild):
            at.FakeChild.launches = []
            code, text = self.repair(runner=None)  # the empty stream holds no reply: the call was still made
        self.assertEqual(code, 1)
        self.assertEqual(len(at.FakeChild.launches), 1)
        self.assertGreater(claude_run.spend_total(self.environ, "authoring"), 0)
        self.assertEqual(claude_run.spend_total(self.environ, "generation"), 0.0)

    def test_stdout_holds_no_spec_text_and_no_answer(self):
        code, text = self.repair()
        self.assertNotIn(NEW_QUESTION, text)
        self.assertNotIn(answer_of("floor", TIDE, "p1", "2"), text)

    def test_a_missing_label_is_reported_as_a_count(self):
        code, text = self.repair()
        self.assertIn("author: 1 probes with a missing label were not selected", text)


class TestRefusalsAfterTheCall(RepairCase, unittest.TestCase):
    def assertWrittenNothing(self, code, text, label=None):
        self.assertEqual(code, 1, text)
        if label:
            self.assertIn(label, text)
        self.assertFalse(self.new.exists())
        self.assertFalse(Path(str(self.new) + ".repair-log.md").exists())
        self.assertEqual(self.parent_leftovers(), [])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.counter(), 1)
        (row,) = self.eval_file("author-runs.json")
        self.assertEqual((row["mode"], row["ids_written"]), ("repair", []))
        self.assertNotIn(SENTINEL_TEXT, text)

    def test_a_probe_added(self):
        spec = repaired_spec()
        extra = copy.deepcopy(spec["probes"][0])
        extra["id"], extra["question"] = "p11", "Is this an extra question about the harbour times?"
        spec["probes"].append(extra)
        self.assertEqual(fixtures.validate_spec(spec, directory_name=TIDE), [])  # a real, valid added probe
        self.reply = block_of(spec)
        code, text = self.repair()
        self.assertWrittenNothing(code, text, f"{TIDE}: probe_set_changed")

    def test_a_probe_removed(self):
        spec = repaired_spec()
        del spec["probes"][-1]
        self.reply = block_of(spec)
        code, text = self.repair()
        self.assertWrittenNothing(code, text, f"{TIDE}: probe_set_changed")

    def test_a_different_fixture_id(self):
        self.reply = block_of(make_spec(FERRY, CLEAN_SPEC_TEXT))
        code, text = self.repair()
        self.assertWrittenNothing(code, text, f"{FERRY}: not_selected")
        self.assertIn(f"{TIDE}: missing", text)

    def test_a_missing_selected_fixture(self):
        self.reply = "I found nothing to change.\n"
        code, text = self.repair()
        self.assertWrittenNothing(code, text, f"{TIDE}: missing")

    def test_an_invalid_spec(self):
        spec = repaired_spec()
        del spec["summary"]
        self.reply = block_of(spec)
        code, text = self.repair()
        self.assertWrittenNothing(code, text, f"{TIDE}: missing_key:")

    def test_a_block_that_is_not_json_and_a_bad_header(self):
        self.reply = at.block(TIDE, "{oops secret-body\n") + at.block("not-an-id")
        code, text = self.repair()
        self.assertWrittenNothing(code, text, f"{TIDE}: json_invalid")
        self.assertIn("block-2: header_invalid", text)
        self.assertNotIn("secret-body", text)

    def test_a_second_block_for_the_same_fixture(self):
        self.reply = block_of(repaired_spec()) + block_of(repaired_spec())
        code, text = self.repair()
        self.assertWrittenNothing(code, text, f"{TIDE}: duplicate")

    def test_a_failed_isolation_proof_writes_nothing_and_uses_the_round(self):
        self.proof_ok = False
        code, text = self.repair()
        self.assertWrittenNothing(code, text, "isolation proof failed")
        self.proof_ok = True
        self.calls.clear()
        self.assertRefused("a repair call has already been made (one round, P:252)")

    def test_an_invalid_reply_uses_the_round(self):
        self.reply = "nothing\n"
        code, text = self.repair()
        self.assertEqual(code, 1)
        self.reply = block_of(repaired_spec())
        self.calls.clear()
        self.assertRefused("a repair call has already been made (one round, P:252)")

    def test_a_failure_while_building_the_store_leaves_nothing_behind(self):
        with mock.patch.object(self.repair_mod, "write_log", side_effect=OSError("disk")):
            code, text = self.repair()
        self.assertEqual(code, 1, text)
        self.assertFalse(self.new.exists())
        self.assertFalse(Path(str(self.new) + ".repair-log.md").exists())
        self.assertEqual(self.parent_leftovers(), [])
        (row,) = self.eval_file("author-runs.json")
        self.assertEqual(row["mode"], "repair")

    def test_a_failure_after_the_copy_leaves_nothing_behind(self):
        real = os.rename
        calls = []

        def failing(src, dst):
            calls.append(dst)
            raise OSError("rename")

        with mock.patch.object(at.author.os, "rename", failing):
            code, text = self.repair()
        self.assertEqual(code, 1, text)
        self.assertTrue(calls)
        self.assertFalse(self.new.exists())
        self.assertEqual(self.parent_leftovers(), [])
        self.assertTrue(real)


class TestLog(RepairCase, unittest.TestCase):
    def log_text(self, environ=None, out=None):
        code, text = self.repair(**({"environ": environ, "out_path": out} if environ else {}))
        self.assertEqual(code, 0, text)
        path = Path(str(out or self.new) + ".repair-log.md")
        return path.read_text(encoding="utf-8"), path.read_bytes()

    def test_the_log_names_the_fixture_the_probe_the_changed_field_and_the_selecting_labels(self):
        text, _ = self.log_text()
        self.assertIn(TIDE, text)
        self.assertIn(f"{TIDE} / p1 / question: changed", text)
        self.assertIn("p1: floor_correct", text)
        self.assertIn("p2: oracle_minority", text)
        self.assertIn("seed 2: floor=correct oracle=correct", text)
        self.assertIn("seed 3: floor=incorrect oracle=incorrect", text)
        self.assertNotIn(FERRY, text)

    def test_the_log_holds_no_answer_text_and_is_plain_ascii(self):
        text, raw = self.log_text()
        for arm in ("floor", "oracle"):
            for s in SEEDS:
                self.assertNotIn(answer_of(arm, TIDE, "p1", s), text)
        self.assertNotIn("answer-", text)
        raw.decode("ascii")
        self.assertNotIn("—", text)

    def test_the_log_passes_the_seals_copied_text_scan(self):
        text, _ = self.log_text()
        with mock.patch.object(fixtures, "_current_username", lambda: "home-sentinel-user"):
            self.assertEqual(seal.copied_text_hits({"repair log": text}), [])

    def test_a_second_build_is_byte_identical(self):
        _, first = self.log_text()
        second_tmp = self.tmp / "second"
        second_tmp.mkdir()
        environ = dict(self.environ, TMPDIR=str(second_tmp))
        _, second = self.log_text(environ=environ, out=self.tmp / "newstore2")
        self.assertEqual(first, second)

    def test_diff_lines_are_one_line_per_changed_field_in_a_fixed_order(self):
        old, new = make_spec(TIDE, "A harbour tide table."), make_spec(TIDE, "A harbour tide table.")
        new["summary"] = "Changed summary."
        new["probes"][1]["answer"] = "x" * 400
        new["probes"][0]["question"] = NEW_QUESTION
        lines = self.repair_mod.diff_lines(old, new)
        heads = [line for line in lines if line.endswith(": changed")]
        self.assertEqual(heads, [f"{TIDE} / - / summary: changed", f"{TIDE} / p1 / question: changed",
                                 f"{TIDE} / p2 / answer: changed"])
        self.assertEqual(lines, self.repair_mod.diff_lines(old, new))
        self.assertTrue(all(len(line) < 400 for line in lines))
        self.assertEqual(self.repair_mod.diff_lines(old, old), [])

    def test_check_repaired_names_each_defect_without_spec_text(self):
        old = {TIDE: make_spec(TIDE, "A harbour tide table.")}
        good = {TIDE: repaired_spec()}
        self.assertEqual(self.repair_mod.check_repaired(old, good), [])
        self.assertEqual(self.repair_mod.check_repaired(old, {}), [f"{TIDE}: missing"])
        other = {TIDE: good[TIDE], FERRY: make_spec(FERRY, "x")}
        self.assertEqual(self.repair_mod.check_repaired(old, other), [f"{FERRY}: not_selected"])


class TestModeInTheLedger(RepairCase, unittest.TestCase):
    def test_a_row_with_no_mode_is_read_as_author_and_does_not_close_the_repair_round(self):
        self.ledger([{"run": 1, "replaces": [], "ids_written": [TIDE]}])
        self.assertEqual(at.author.row_mode({"run": 1}), "author")
        self.assertEqual(at.author.row_mode({"run": 1, "mode": "repair"}), "repair")
        code, text = self.repair()
        self.assertEqual(code, 0, text)

    def test_a_row_with_no_mode_and_replaces_is_still_a_replacement_for_the_guard(self):
        self.ledger([{"run": 1, "replaces": ["heldout-009-old"], "ids_written": [TIDE]}])
        self.assertRefused(f"fixture {TIDE} is a replacement (P:262)")

    def test_authoring_calls_write_the_mode(self):
        code, text = self.author()
        self.assertEqual(code, 0, text)
        self.assertEqual(self.eval_file("author-runs.json")[0]["mode"], "author")
        self.reply = at.reply("heldout-004-new-one")
        self.author(replaces=["heldout-001-tide-table"], out_path=self.tmp / "store2")
        self.assertEqual(self.eval_file("author-runs.json")[1]["mode"], "replace")

    def test_a_repair_counts_toward_the_four_call_counter(self):
        self.proof_ok = False
        self.assertEqual(self.repair()[0], 1)
        self.assertEqual(self.counter(), 1)
        for _ in range(3):
            self.assertEqual(self.author()[0], 1)
        code, text = self.author()
        self.assertIn("author: STOP the author run budget is spent (4 runs)", text)


class TestCli(RepairCase, unittest.TestCase):
    def main(self, *argv):
        with mock.patch.dict(os.environ, {"TMPDIR": str(self.tmp)}):
            with mock.patch("sys.stdout", new=io.StringIO()) as shown:
                code = run_cli.main(["author", *argv])
        return code, shown.getvalue()

    def repair_args(self):
        return ["--repair", "--from", str(self.results), "--fixtures-root", str(self.root),
                "--guide", str(self.tmp / "g.md"), "--out", str(self.new)]

    def test_author_without_repair_and_without_prompt_is_refused(self):
        code, text = self.main("--guide", str(self.tmp / "g.md"), "--out", str(self.new))
        self.assertEqual(code, 2)
        self.assertIn("author: REFUSED --prompt is required", text)

    def test_repair_with_each_flag_it_does_not_take_is_refused(self):
        for flag, value in (("--prompt", str(self.tmp / "p.md")), ("--extra", str(self.tmp / "x.md")),
                            ("--replaces", "heldout-001-a"), ("--only-ids", "heldout-001-a")):
            with self.subTest(flag=flag):
                code, text = self.main(*self.repair_args(), flag, value)
                self.assertEqual(code, 2, text)
                self.assertIn(f"author: REFUSED --repair cannot be used with {flag}", text)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.new.exists())

    def test_repair_without_from_or_without_fixtures_root_is_refused(self):
        args = self.repair_args()
        for flag in ("--from", "--fixtures-root"):
            with self.subTest(flag=flag):
                at_ = args.index(flag)
                code, text = self.main(*(args[:at_] + args[at_ + 2:]))
                self.assertEqual(code, 2, text)
                self.assertIn("author: REFUSED --repair needs --from, --fixtures-root, --guide and --out", text)

    def test_from_without_repair_is_refused(self):
        code, text = self.main("--prompt", str(self.tmp / "p.md"), "--guide", str(self.tmp / "g.md"),
                               "--out", str(self.new), "--from", str(self.results))
        self.assertEqual(code, 2, text)
        self.assertIn("author: REFUSED --from and --fixtures-root belong to --repair", text)

    def test_repair_through_the_cli_reaches_the_gate_like_author_does(self):
        code, text = self.main(*self.repair_args())
        self.assertEqual(code, 2)
        self.assertIn("REFUSED", text)
        self.assertFalse(self.new.exists())


class TestTheRoundHoldsWhenTheCallRaises(RepairCase, unittest.TestCase):
    """The call is counted before the model runs; a raise after the count still uses the one repair round."""

    def raising_runner(self, cfg, prompt, sandbox, **kw):
        self.calls.append((prompt, kw))
        raise RuntimeError("boom")

    def rows(self):
        return self.eval_file("author-runs.json")

    def assertUnknownRow(self):
        (row,) = self.rows()
        self.assertEqual((row["mode"], row["run"], row["isolation"], row["replaces"]), ("repair", 1, "UNKNOWN", []))
        self.assertEqual(row["ids_written"], [])
        self.assertEqual(row["repair_ids"], [TIDE])
        self.assertEqual(row["guide_sha256"], hashlib.sha256(at.GUIDE.encode("utf-8")).hexdigest())
        self.assertEqual(len(row["prompt_sha256"]), 64)
        for key in ("started_utc", "claude_code_version", "model", "extra_sha256", "cost_usd"):
            self.assertIsNone(row[key], key)
        self.assertEqual(self.counter(), 1)

    def assertNothingPublished(self):
        self.assertFalse(self.new.exists())
        self.assertFalse(Path(str(self.new) + ".repair-log.md").exists())
        self.assertEqual(self.parent_leftovers(), [])

    def assertSecondCallRefused(self):
        self.calls.clear()
        self.assertRefused("a repair call has already been made (one round, P:252)")
        self.assertEqual(self.counter(), 1)

    def test_a_runner_that_raises_leaves_an_unknown_row_and_the_round_is_used(self):
        code, text = self.repair(runner=self.raising_runner)
        self.assertEqual(code, 1, text)
        self.assertIn("author: ERROR RuntimeError", text)
        self.assertEqual(len(self.calls), 1)
        self.assertNothingPublished()
        self.assertUnknownRow()
        self.assertSecondCallRefused()
        self.assertEqual(len(self.rows()), 1)

    def test_an_isolation_check_that_raises_leaves_an_unknown_row_and_the_round_is_used(self):
        with mock.patch.object(at.author.isolation, "check", side_effect=RuntimeError("boom")):
            code, text = self.repair()
        self.assertEqual(code, 1, text)
        self.assertIn("author: ERROR RuntimeError", text)
        self.assertEqual(len(self.calls), 1)
        self.assertNothingPublished()
        self.assertUnknownRow()
        self.assertSecondCallRefused()

    def test_a_spent_budget_writes_no_row_because_no_call_was_counted(self):
        for _ in range(4):
            judge.take_call(self.environ, 4, "author")
        code, text = self.repair(runner=self.raising_runner)
        self.assertEqual(code, 1, text)
        self.assertIn("author: STOP the author run budget is spent", text)
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.rows())
        self.assertEqual(self.counter(), 4)

    def test_a_spent_budget_adds_no_row_to_a_ledger_that_exists(self):
        self.ledger([{"run": 1, "mode": "author", "replaces": [], "ids_written": []}])
        for _ in range(4):
            judge.take_call(self.environ, 4, "author")
        code, text = self.repair(runner=self.raising_runner)
        self.assertEqual(code, 1, text)
        self.assertEqual(len(self.rows()), 1)

    def test_each_existing_path_writes_exactly_one_row(self):
        self.assertEqual(self.repair()[0], 0)
        self.assertEqual(len(self.rows()), 1)
        for n, (setup, expect) in enumerate((("proof", "isolation proof failed"), ("no text", "holds no text"),
                                             ("refused", "the reply is refused")), 2):
            with self.subTest(path=setup):
                self.ledger([])  # a clean ledger: each path is a first and only repair call
                self.proof_ok = setup != "proof"
                self.reply = {"proof": self.reply, "no text": None, "refused": "nothing\n"}[setup]
                code, text = self.repair(out_path=self.tmp / f"store-{n}")
                self.assertEqual(code, 1, text)
                self.assertIn(expect, text)
                self.assertEqual(len(self.rows()), 1)
                self.assertEqual(self.rows()[0]["mode"], "repair")
                self.proof_ok, self.reply = True, block_of(repaired_spec())

    def test_a_publish_that_raises_still_writes_one_row(self):
        with mock.patch.object(self.repair_mod, "write_log", side_effect=OSError("disk")):
            code, text = self.repair()
        self.assertEqual(code, 1, text)
        (row,) = self.rows()
        self.assertEqual((row["mode"], row["isolation"]), ("repair", "PASS"))


class TestOutParent(RepairCase, unittest.TestCase):
    def test_an_out_under_a_missing_parent_is_refused_before_any_call_and_creates_nothing(self):
        before = sorted(p.name for p in self.tmp.iterdir())
        text = self.assertRefused("--out needs an existing parent folder", out_path=self.tmp / "gone" / "deeper" / "s")
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), before)
        self.assertNotIn("gone", text)
        self.assertIsNone(self.eval_file("author-runs.json"))

    def test_an_out_whose_parent_is_a_file_is_refused(self):
        (self.tmp / "afile").write_text("x", encoding="utf-8")
        self.assertRefused("--out needs an existing parent folder", out_path=self.tmp / "afile" / "s")

    def test_the_missing_parent_refusal_comes_after_the_clone_and_exists_refusals(self):
        out = ROOT / "scratch-gone" / "s"
        code, text = self.repair(out_path=out)
        self.assertIn("inside the repository clone", text)
        self.assertFalse(out.parent.exists())

    def test_an_out_under_an_existing_parent_succeeds_and_publish_makes_no_parent(self):
        parent = self.tmp / "made"
        parent.mkdir()
        code, text = self.repair(out_path=parent / "s")
        self.assertEqual(code, 0, text)
        self.assertTrue((parent / "s").is_dir())
        parent_after = sorted(p.name for p in parent.iterdir())
        self.assertEqual(parent_after, ["s", "s.repair-log.md"])

    def test_publish_does_not_create_a_missing_parent(self):
        with self.assertRaises(OSError):
            at.author._publish(self.root, self.tmp / "nope" / "s", self.tmp / "nope" / "s.repair-log.md", {}, [])
        self.assertFalse((self.tmp / "nope").exists())


SENTINEL_TEXT = at.SENTINEL


if __name__ == "__main__":
    unittest.main()


class TestLogCompleteness(RepairCase, unittest.TestCase):
    """The log shows a probe reorder, and a change past character 300 still shows in a digest."""

    def log_for(self, spec):
        self.reply = block_of(spec)
        code, text = self.repair()
        self.assertEqual(code, 0, text)
        return Path(str(self.new) + ".repair-log.md").read_text(encoding="utf-8")

    def reordered(self):
        spec = make_spec(TIDE, "A harbour tide table.", ("p6",))
        spec["probes"] = list(reversed(spec["probes"]))
        return spec

    def test_a_reorder_only_reply_is_logged_as_order_changed_with_both_id_lists(self):
        text = self.log_for(self.reordered())
        old_ids, new_ids = json.dumps(PIDS), json.dumps(list(reversed(PIDS)))
        self.assertIn(f"{TIDE} / - / probes: order changed", text)
        self.assertIn(f"  old: {old_ids}", text)
        self.assertIn(f"  new: {new_ids}", text)
        tide_section = text.split(f"## {TIDE}")[1].split("Selected by the runner:")[0]
        self.assertNotIn("none", tide_section)

    def test_a_reorder_is_logged_even_when_fields_also_changed(self):
        spec = self.reordered()
        spec["summary"] = "Changed summary."
        lines = self.repair_mod.diff_lines(make_spec(TIDE, "A harbour tide table.", ("p6",)), spec)
        self.assertIn(f"{TIDE} / - / probes: order changed", lines)
        self.assertIn(f"{TIDE} / - / summary: changed", lines)

    def test_the_order_line_is_not_logged_when_the_order_is_unchanged(self):
        old = make_spec(TIDE, "A harbour tide table.")
        new = make_spec(TIDE, "A harbour tide table.")
        new["probes"][0]["question"] = NEW_QUESTION
        self.assertFalse([l for l in self.repair_mod.diff_lines(old, new) if "order changed" in l])

    def test_a_reorder_only_diff_changes_no_probe(self):
        old = make_spec(TIDE, "A harbour tide table.", ("p6",))
        lines = self.repair_mod.diff_lines(old, self.reordered())
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0], f"{TIDE} / - / probes: order changed")
        self.assertEqual(self.repair_mod.changed_probe_count(lines), 0)

    def test_two_values_that_differ_only_after_character_300_get_different_digests_in_the_log(self):
        first = make_spec(TIDE, "A harbour tide table.", ("p6",))
        first["probes"][1]["answer"] = "a" * 350 + "X" + "a" * 49
        second = copy.deepcopy(first)
        second["probes"][1]["answer"] = "a" * 350 + "Y" + "a" * 49
        old = make_spec(TIDE, "A harbour tide table.", ("p6",))
        old_hash = hashlib.sha256(json.dumps(old["probes"][1]["answer"], sort_keys=True,
                                             ensure_ascii=True).encode("utf-8")).hexdigest()
        digests = []
        for spec in (first, second):
            lines = self.repair_mod.diff_lines(old, spec)
            new_lines = [l for l in lines if l.startswith("  new: ")]
            self.assertEqual(len(new_lines), 1)
            digests.append(new_lines[0].rsplit("sha256=", 1)[1])
            self.assertTrue([l for l in lines if l.startswith("  old: ")][0].endswith("sha256=" + old_hash))
        self.assertNotEqual(digests[0], digests[1])
        self.assertEqual(digests[0], hashlib.sha256(json.dumps(first["probes"][1]["answer"], sort_keys=True,
                                                               ensure_ascii=True).encode("utf-8")).hexdigest())
        text = self.log_for(first)
        self.assertIn("sha256=" + digests[0], text)

    def test_an_absent_old_value_prints_sha256_absent(self):
        old = make_spec(TIDE, "A harbour tide table.")
        new = make_spec(TIDE, "A harbour tide table.")
        new["extra_field"] = "x"
        lines = self.repair_mod.diff_lines(old, new)
        self.assertIn("  old: <absent> sha256=absent", lines)
        self.assertTrue([l for l in lines if l.startswith("  new: ")][0].startswith('  new: "x" sha256='))


class TestFixturesRootOnTheCli(unittest.TestCase):
    """gen-fixture, install-check and install-verify with --fixtures-root: refusals before any other work."""

    COMMANDS = (("gen-fixture", "command_gen_fixture"), ("install-check", "command_install_check"),
                ("install-verify", "install_verify"))
    ID = "heldout-001-tide-table"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.store = self.tmp / "store"
        (self.store / self.ID).mkdir(parents=True)

    def main(self, *argv):
        with mock.patch("sys.stdout", new=io.StringIO()) as shown:
            code = run_cli.main(list(argv))
        return code, shown.getvalue()

    def test_a_missing_store_is_refused_for_each_command(self):
        for command, name in self.COMMANDS:
            with self.subTest(command):
                with mock.patch.object(fixtures, name, side_effect=AssertionError("reached")) as reached, \
                        mock.patch.object(claude_run, "load_config", side_effect=AssertionError("config")):
                    code, text = self.main(command, self.ID, "--fixtures-root", str(self.tmp / "nope"))
                self.assertEqual(code, 2, text)
                self.assertIn(f"{command}: REFUSED --fixtures-root does not exist", text)
                reached.assert_not_called()

    def test_a_store_with_a_symlink_or_a_git_folder_is_refused_for_each_command(self):
        (self.store / self.ID / "link").symlink_to(self.tmp)
        for command, name in self.COMMANDS:
            with self.subTest(command + " symlink"):
                with mock.patch.object(fixtures, name, side_effect=AssertionError("reached")) as reached, \
                        mock.patch.object(claude_run, "load_config", side_effect=AssertionError("config")):
                    code, text = self.main(command, self.ID, "--fixtures-root", str(self.store))
                self.assertEqual(code, 2, text)
                self.assertIn(f"{command}: REFUSED the fixture store holds a symlink or a git folder", text)
                reached.assert_not_called()
        (self.store / self.ID / "link").unlink()
        (self.store / ".git").mkdir()
        for command, name in self.COMMANDS:
            with self.subTest(command + " git"):
                with mock.patch.object(fixtures, name, side_effect=AssertionError("reached")) as reached, \
                        mock.patch.object(claude_run, "load_config", side_effect=AssertionError("config")):
                    code, text = self.main(command, self.ID, "--fixtures-root", str(self.store))
                self.assertEqual(code, 2, text)
                self.assertIn(f"{command}: REFUSED the fixture store holds a symlink or a git folder", text)
                reached.assert_not_called()

    def test_without_the_flag_each_command_is_called_as_before(self):
        for command, name in self.COMMANDS:
            with self.subTest(command):
                with mock.patch.object(fixtures, name, return_value=0) as called, \
                        mock.patch.object(seal, "command_gate", return_value=""):
                    code, _ = self.main(command, self.ID)
                self.assertEqual(code, 0)
                called.assert_called_once_with(self.ID)

    def test_with_the_flag_each_command_gets_the_store_and_flat(self):
        for command, name in self.COMMANDS:
            with self.subTest(command):
                with mock.patch.object(fixtures, name, return_value=0) as called, \
                        mock.patch.object(seal, "command_gate", return_value=""):
                    code, _ = self.main(command, self.ID, "--fixtures-root", str(self.store))
                self.assertEqual(code, 0)
                called.assert_called_once_with(self.ID, root=self.store, flat=True)
