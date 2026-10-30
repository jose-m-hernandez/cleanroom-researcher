"""Offline tests for the human audit of EXP-0001 (README P:293-306, P:159): the frame, the balanced draw, the blinded
export, kappa and the judge-minus-person difference, and the audit seed in the seal.

Nothing here calls a model. The seal worlds of tests/test_exp0001_seal.py are built by calling their setUp on an
instance, never by subclassing. This file does not import `random`: the test vector holds its expected ids as literals.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests import exp0001_paths  # noqa: F401

import audit  # noqa: E402
import claude_run  # noqa: E402
import score  # noqa: E402
import seal  # noqa: E402
import stats  # noqa: E402
from tests import test_exp0001_score as base  # noqa: E402
from tests import test_exp0001_seal as st  # noqa: E402

VECTOR = Path(__file__).resolve().parent / "data" / "exp0001-audit-vector" / "vector.json"
SEED = 20261022
FRAME_COUNTS = {"correct": (40, 12, 3), "incorrect": (20, 20, 20), "abstained": (10, 4, 2)}


def sizes(c, i, a):
    return {"correct": c, "incorrect": i, "abstained": a}


def cells(sf, ku, t):
    return {"single-fact": sf, "knowledge-update": ku, "temporal": t}


def vector_frame() -> list:
    """The vector's frame by its formula: for each label, type and i in range(n)."""
    out = []
    for label, counts in FRAME_COUNTS.items():
        for kind, n in zip(audit.TYPES, counts):
            for i in range(n):
                out.append({"id": "heldout-%03d-vector/%d/%s%s%02d" % (i % 25 + 1, i % 3 + 1, label[0], kind[0], i),
                            "label": label, "type": kind})
    return out


class ShareTests(unittest.TestCase):
    """T1: label_shares (P:295-297)."""

    def test_the_label_shares(self):
        for given, want in [
            (sizes(100, 100, 100), sizes(33, 34, 33)),
            (sizes(55, 60, 16), sizes(41, 43, 16)),  # shortfall 17: 8 each, the odd one to incorrect
            (sizes(50, 21, 50), sizes(40, 21, 39)),  # incorrect short by 13: 6 each, the odd one to correct (default)
            (sizes(10, 20, 100), sizes(10, 20, 70)),  # two short
            (sizes(40, 100, 5), sizes(40, 55, 5)),  # a cascade: abstained, then correct
            (sizes(10, 10, 10), sizes(10, 10, 10)),  # a frame under 100
        ]:
            with self.subTest(given=given):
                self.assertEqual(audit.label_shares(given), want)

    def test_a_missing_label_counts_as_zero(self):
        self.assertEqual(audit.label_shares({"correct": 100, "incorrect": 100}), sizes(49, 51, 0))  # 33 over two: 16, odd to incorrect


class AllocationTests(unittest.TestCase):
    """T2: cell_allocation (P:298)."""

    def test_the_cell_allocations(self):
        for share, given, want in [
            (41, cells(40, 12, 3), cells(27, 11, 3)),  # temporal capped at 3, its excess re-spread
            (43, cells(20, 20, 20), cells(15, 14, 14)),  # equal remainders: the tie goes in TYPES order
            (16, cells(10, 4, 2), cells(10, 4, 2)),  # take all
            (15, cells(3, 2, 40), cells(3, 2, 10)),  # the two small cells are closed at their size
        ]:
            with self.subTest(share=share):
                got = audit.cell_allocation(share, given)
                self.assertEqual(got, want)
                self.assertEqual(sum(got.values()), min(share, sum(given.values())))

    def test_a_missing_type_counts_as_zero(self):
        self.assertEqual(audit.cell_allocation(14, {"single-fact": 10, "temporal": 10}), cells(7, 0, 7))


class VectorTests(unittest.TestCase):
    """T3: the test vector."""

    def setUp(self):
        self.vector = json.loads(VECTOR.read_text(encoding="utf-8"))

    def test_the_frame_is_the_formula(self):
        self.assertEqual(self.vector["seed"], SEED)
        self.assertEqual(self.vector["frame"], vector_frame())
        self.assertEqual(len(self.vector["frame"]), 131)

    def test_the_shares_and_the_allocation(self):
        self.assertEqual(self.vector["shares"], sizes(41, 43, 16))
        self.assertEqual(self.vector["allocation"], {"correct": cells(27, 11, 3), "incorrect": cells(15, 14, 14),
                                                      "abstained": cells(10, 4, 2)})

    def test_the_draw_and_the_blind_order(self):
        drawn = audit.draw(self.vector["frame"])
        self.assertEqual(drawn, self.vector["drawn"])
        self.assertEqual(audit.blind_order(drawn), self.vector["blind_order"])
        self.assertEqual(len(set(drawn)), 100)
        self.assertEqual(sorted(self.vector["blind_order"]), sorted(drawn))
        self.assertNotEqual(self.vector["blind_order"], drawn)

    def test_the_draw_is_in_the_frame_and_follows_the_shares(self):
        by_id = {item["id"]: item for item in self.vector["frame"]}
        drawn = self.vector["drawn"]
        self.assertTrue(set(drawn) <= set(by_id))
        for label, share in self.vector["shares"].items():
            self.assertEqual(sum(1 for i in drawn if by_id[i]["label"] == label), share)
        for label, per_type in self.vector["allocation"].items():
            for kind, n in per_type.items():
                self.assertEqual(sum(1 for i in drawn if by_id[i]["label"] == label and by_id[i]["type"] == kind), n)

    def test_the_draw_does_not_depend_on_the_frame_order(self):
        self.assertEqual(audit.draw(list(reversed(self.vector["frame"]))), self.vector["drawn"])


class FrameTests(unittest.TestCase):
    """T4: frame, on a small tree."""

    FID = "heldout-001-frame"

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.tmp = Path(td.name)

        def probe(pid, kind, **extra):
            return {"id": pid, "type": kind, "question": f"question {pid}", "answer": f"gold {pid}",
                    "reject": [f"stale {pid}"], **extra}
        self.spec = {"id": self.FID, "probes": [
            probe("p1", "single-fact"), probe("p2", "single-fact"), probe("p3", "knowledge-update"),
            probe("p4", "temporal"), probe("p5", "single-fact"), probe("p6", "abstention"),
            probe("p7", "single-fact", excluded={"date": "2026-10-20", "reason": "leaks"}), probe("p8", "temporal")]}
        answers = {"p1": "a1", "p2": "a2", "p3": "UNKNOWN", "p5": "a5", "p6": "a6", "p7": "a7", "p8": "a8"}  # p4 missing
        labels = {"p1": "correct", "p2": "incorrect", "p3": "abstained", "p4": None, "p5": None, "p6": "incorrect",
                  "p7": "correct", "p8": "abstained"}
        for arm in ("floor", "baseline", "oracle"):
            for seed in (1, 2, 3):
                path = self.tmp / "probes" / arm / self.FID / f"s{seed}.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"answers": answers}), encoding="utf-8")
        self.scores = {"scorable": {self.FID: ["p1", "p8"]},
                       "labels": {arm: {self.FID: {str(s): dict(labels) for s in (1, 2, 3)}}
                                  for arm in ("floor", "baseline", "oracle")}}
        self.run = {"fixtures": [self.FID, "heldout-002-absent"], "excluded_cells": [
            {"fixture": self.FID, "arm": "baseline", "seed": 2, "kind": "probe"},
            {"fixture": self.FID, "arm": "floor", "seed": 3, "kind": "probe"}]}

    def frame(self):
        return audit.frame(self.tmp, {self.FID: self.spec, "heldout-009-other": self.spec}, self.scores, self.run)

    def test_exactly_the_judged_baseline_answers_are_in(self):
        items = self.frame()
        want = [f"{self.FID}/{seed}/{pid}" for seed in (1, 3) for pid in ("p1", "p2", "p8")]
        self.assertEqual([i["id"] for i in items], want)
        self.assertEqual([i["id"] for i in items], sorted(i["id"] for i in items))

    def test_each_item_holds_its_facts(self):
        item = {i["id"]: i for i in self.frame()}[f"{self.FID}/3/p2"]
        self.assertEqual({k: item[k] for k in ("fixture", "seed", "probe", "type", "label", "scorable")},
                         {"fixture": self.FID, "seed": 3, "probe": "p2", "type": "single-fact", "label": "incorrect",
                          "scorable": False})
        self.assertEqual(item["case"], {"question": "question p2", "gold": "gold p2", "stale": ["stale p2"],
                                        "answer": "a2"})
        flags = {i["probe"]: i["scorable"] for i in self.frame() if i["id"].endswith("/1/" + i["probe"])}
        self.assertEqual(flags, {"p1": True, "p2": False, "p8": True})

    def test_the_floor_exclusion_does_not_remove_the_baseline_answer(self):
        self.assertIn(f"{self.FID}/3/p1", [i["id"] for i in self.frame()])
        self.run["excluded_cells"].append({"fixture": self.FID, "arm": "baseline", "seed": 3, "kind": "preserve"})
        self.assertEqual({i["seed"] for i in self.frame()}, {1})

    def test_a_label_outside_the_three_is_out(self):
        self.scores["labels"]["baseline"][self.FID]["1"]["p1"] = "maybe"
        self.assertNotIn(f"{self.FID}/1/p1", [i["id"] for i in self.frame()])

    def test_a_type_outside_the_three_raises(self):
        self.spec["probes"][0]["type"] = "other"
        with self.assertRaises(ValueError) as caught:
            self.frame()
        self.assertIn("p1", str(caught.exception))


class RuleTests(unittest.TestCase):
    """T5: names_more_than_one (ruling Q8)."""

    def test_the_rule(self):
        stale = ["oldest first"]
        self.assertIs(audit.names_more_than_one("Newest first, not oldest first", "newest first", stale), True)
        self.assertIs(audit.names_more_than_one("newest first", "newest first", stale), False)
        self.assertIs(audit.names_more_than_one("oldest first", "newest first", stale), False)
        self.assertIsNone(audit.names_more_than_one("anything", "x", []))
        self.assertIsNone(audit.names_more_than_one("anything", "x", [""]))

    def test_the_rule_in_words_is_the_docstring_sentence(self):
        sentence = ("An answer names more than one value when its probe has at least one stale answer and the answer "
                    "contains, ignoring letter case, both the gold answer and at least one stale answer; probes "
                    "without stale answers are not counted.")
        self.assertTrue(audit.names_more_than_one.__doc__.startswith(sentence))


def build(rows, frame_sizes, seed=20261017):
    """(key, owner) from rows (k, fixture, type, judge, owner label, scorable[, mechanical, owner bool])."""
    key = {"version": 1, "audit_seed": SEED, "bootstrap_seed": seed, "frame_sizes": frame_sizes, "items": {}}
    owner = {"version": 1, "labels": {}}
    for k, row in enumerate(rows, start=1):
        fid, kind, judged, owned, scorable = row[:5]
        mech, said = (row[5], row[6]) if len(row) > 5 else (None, False)
        key["items"][str(k)] = {"id": f"{fid}/1/p{k}", "fixture": fid, "seed": 1, "probe": f"p{k}", "type": kind,
                                "judge_label": judged, "scorable": scorable, "mechanical_multi": mech}
        owner["labels"][str(k)] = {"label": owned, "names_more_than_one_value": said}
    return key, owner


def kappa_case():
    """P:392-397: 33 correct, 34 incorrect, 33 abstained by the judge; the owner differs on 4 judge-correct items."""
    rows = []
    for n in range(100):
        judged = "correct" if n < 33 else "incorrect" if n < 67 else "abstained"
        owned = "incorrect" if n < 4 else judged
        kind = "temporal" if 90 <= n < 93 else "single-fact"
        rows.append(("heldout-001-a", kind, judged, owned, False))
    return build(rows, {"correct": cells(20, 10, 3), "incorrect": cells(20, 10, 4), "abstained": cells(20, 10, 3)})


class SummariseKappaTests(unittest.TestCase):
    """T6: kappa and agreement."""

    def test_the_worked_case(self):
        out = audit.summarise(*kappa_case())
        self.assertEqual(out["n"], 100)
        self.assertEqual(out["agreement"], 0.96)
        self.assertEqual(round(out["kappa"], 2), 0.94)
        self.assertEqual(out["kappa_bar"], 0.6)
        self.assertIsNone(out["judge_minus_person"]["value"])  # no scorable item

    def test_by_type(self):
        out = audit.summarise(*kappa_case())["by_type"]
        self.assertEqual(sorted(out), ["single-fact", "temporal"])  # a type with no item is left out
        self.assertEqual(out["single-fact"]["n"], 97)
        self.assertEqual(out["temporal"], {"n": 3, "agreement": 1.0, "kappa": 1.0})  # one label: kappa is 1.0 when equal
        self.assertEqual(out["single-fact"]["agreement"], stats._r(93 / 97))

    def test_one_label_on_each_side_but_not_equal_is_zero(self):
        key, owner = build([("heldout-001-a", "temporal", "correct", "correct", False),
                            ("heldout-001-a", "temporal", "correct", "incorrect", False)],
                           {"correct": cells(0, 0, 2), "incorrect": cells(0, 0, 0), "abstained": cells(0, 0, 0)})
        self.assertEqual(audit.summarise(key, owner)["kappa"], 0.0)

    def test_the_multi_value_check(self):
        rows = [("heldout-001-a", "single-fact", "correct", "correct", False, True, True),
                ("heldout-001-a", "single-fact", "correct", "correct", False, False, True),
                ("heldout-001-a", "single-fact", "correct", "correct", False, False, False),
                ("heldout-001-a", "single-fact", "correct", "correct", False, None, True)]
        key, owner = build(rows, {"correct": cells(4, 0, 0), "incorrect": cells(0, 0, 0), "abstained": cells(0, 0, 0)})
        self.assertEqual(audit.summarise(key, owner)["multi_value"],
                         {"n": 3, "agreement": stats._r(2 / 3), "mechanical_true": 1, "owner_true": 2})
        key["items"] = {k: dict(v, mechanical_multi=None) for k, v in key["items"].items()}
        self.assertEqual(audit.summarise(key, owner)["multi_value"],
                         {"n": 0, "agreement": None, "mechanical_true": 0, "owner_true": 0})


def minus_case(extra=False):
    """T7: frame totals 60/30/10, a sample of 30/20/10 (weights 2, 1.5, 1), two scorable fixtures and padding."""
    a, b, pad = "heldout-001-a", "heldout-002-b", "heldout-003-pad"
    k = "single-fact"
    rows = [(a, k, "correct", "correct", True), (a, k, "incorrect", "correct", True),
            (b, k, "correct", "incorrect", True), (b, k, "abstained", "abstained", True)]
    pads = {"correct": 28, "incorrect": 19, "abstained": 9}
    if extra:  # a non-scorable item in fixture A takes the place of one padding item, so the weights stay
        rows.append((a, k, "correct", "incorrect", False))
        pads["correct"] -= 1
    for label, n in pads.items():
        rows += [(pad, k, label, label, False)] * n
    return build(rows, {"correct": cells(60, 0, 0), "incorrect": cells(30, 0, 0), "abstained": cells(10, 0, 0)})


class SummariseMinusTests(unittest.TestCase):
    """T7: judge-minus-person."""

    def test_the_hand_worked_value(self):
        key, owner = minus_case()
        out = audit.summarise(key, owner)["judge_minus_person"]
        self.assertEqual(out["value"], stats._r((-3 / 7 + 2 / 3) / 2))
        self.assertEqual(out["value"], 0.119047619)
        self.assertEqual((out["fixtures"], out["b"], out["seed"]), (2, 10000, 20261017))

    def test_a_non_scorable_item_does_not_move_it(self):
        plain = audit.summarise(*minus_case())["judge_minus_person"]
        with_extra = audit.summarise(*minus_case(extra=True))["judge_minus_person"]
        self.assertEqual(with_extra, plain)

    def test_the_interval_is_the_bootstrap_of_the_fixture_values(self):
        key, owner = minus_case()
        out = audit.summarise(key, owner)["judge_minus_person"]
        want = stats.bootstrap_ci([4 / 7 - 1, 2 / 3 - 0], b=10000, alpha=0.05, rng_seed=key["bootstrap_seed"])
        self.assertEqual(out["ci"], list(want))

    def test_no_scorable_item_gives_none(self):
        out = audit.summarise(*kappa_case())["judge_minus_person"]
        self.assertEqual((out["value"], out["ci"], out["fixtures"]), (None, [None, None], 0))

    def test_weights_matter(self):
        key, owner = minus_case()
        key["frame_sizes"] = {"correct": cells(30, 0, 0), "incorrect": cells(20, 0, 0), "abstained": cells(10, 0, 0)}
        # weights 1: A is 1 of 2 judged correct against 2 of 2, B is 1 of 2 against 0 of 2
        self.assertEqual(audit.summarise(key, owner)["judge_minus_person"]["value"], 0.0)


class SummariseRefusalTests(unittest.TestCase):
    """T8: a labels file that does not match the key."""

    def setUp(self):
        self.key, self.owner = kappa_case()

    def test_a_missing_item(self):
        del self.owner["labels"]["7"]
        with self.assertRaises(ValueError):
            audit.summarise(self.key, self.owner)

    def test_an_extra_item(self):
        self.owner["labels"]["101"] = {"label": "correct", "names_more_than_one_value": False}
        with self.assertRaises(ValueError):
            audit.summarise(self.key, self.owner)

    def test_a_label_outside_the_three(self):
        for bad in ("maybe", None, "Correct"):
            self.owner["labels"]["3"]["label"] = bad
            with self.subTest(label=bad), self.assertRaises(ValueError):
                audit.summarise(self.key, self.owner)

    def test_a_value_that_is_not_a_bool(self):
        for bad in (None, "yes", 1, 0):
            self.owner["labels"]["3"]["names_more_than_one_value"] = bad
            with self.subTest(value=bad), self.assertRaises(ValueError):
                audit.summarise(self.key, self.owner)

    def test_a_malformed_file(self):
        with self.assertRaises(ValueError):
            audit.summarise({}, self.owner)
        with self.assertRaises(ValueError):
            audit.summarise(self.key, {"labels": []})


class SealSeedTests(unittest.TestCase):
    """T9: the audit seed in the seal."""

    def setUp(self):
        self.w = st.World("setUp")
        self.w.setUp()
        self.addCleanup(self.w.doCleanups)
        self.w.sealed()

    def verify(self):
        return seal.verify(self.w.seal_dir, self.w.store, self.w.pre)

    def rewrite(self, **change):
        data = self.w.seal_json()
        for key, value in change.items():
            if value is KeyError:
                del data[key]
            else:
                data[key] = value
        (self.w.seal_dir / "seal.json").write_text(json.dumps(data), encoding="utf-8")

    def test_a_fresh_seal_holds_the_seed_and_verifies(self):
        self.assertEqual(self.w.seal_json()["audit_seed"], SEED)
        self.assertIs(type(self.w.seal_json()["audit_seed"]), int)
        self.assertEqual(seal.AUDIT_SEED, SEED)
        self.assertEqual(self.verify(), [])

    def test_a_wrong_missing_or_mistyped_seed_is_a_mismatch(self):
        for value in (1, KeyError, "20261022", 20261022.0, True, None):
            self.setUp()
            self.rewrite(audit_seed=value)
            with self.subTest(value=repr(value)):
                self.assertEqual(self.verify(), ["changed:audit_seed"])


class ExportTests(unittest.TestCase):
    """T10 and T11: export end to end, and its refusals."""

    def setUp(self):
        self.a = st.AfterTheSealTests("setUp")
        self.a.setUp()
        self.addCleanup(self.a.doCleanups)
        self.out = self.a.tmp / "audit-out"

    def finish(self):
        self.assertEqual(self.a.final_run()[0], 0)
        self.a.judge = base.FakeJudge(st.HSPECS)
        self.assertEqual(self.a.final_score()[0], 0)

    def export(self, out=None, results=None):
        buf = io.StringIO()
        code = audit.export(results or self.a.final, self.a.seal_dir, self.a.store, out or self.out, stdout=buf)
        return code, buf.getvalue()

    def scores(self):
        return json.loads((self.a.final / "scores.json").read_text(encoding="utf-8"))

    def read(self, name):
        return json.loads((self.out / name).read_text(encoding="utf-8"))

    def test_end_to_end(self):
        self.finish()
        code, text = self.export()
        self.assertEqual(code, 0, text)
        run = json.loads((self.a.final / "run.json").read_text(encoding="utf-8"))
        items = audit.frame(self.a.final, score.load_specs(self.a.store, run["fixtures"]), self.scores(), run)
        self.assertTrue(items)
        self.assertLess(len(items), 100)  # a small frame: everything is taken
        self.assertRegex(text, r"^audit-export: wrote items\.json, owner-labels\.json and key\.json \(frame %d; sample %d: "
                               r"correct \d+, incorrect \d+, abstained \d+\)\n$" % (len(items), len(items)))
        self.assertEqual(sorted(os.listdir(self.out)), ["items.json", "key.json", "owner-labels.json"])
        shown, owner, key = self.read("items.json"), self.read("owner-labels.json"), self.read("key.json")
        self.assertEqual(shown["version"], 1)
        self.assertEqual(len(shown["items"]), len(items))
        for item in shown["items"]:
            self.assertEqual(sorted(item), ["answer", "gold", "item", "question", "stale"])
        self.assertEqual([i["item"] for i in shown["items"]], list(range(1, len(items) + 1)))
        self.assertEqual(sorted(owner["labels"], key=int), [str(k) for k in range(1, len(items) + 1)])
        self.assertTrue(all(v == {"label": None, "names_more_than_one_value": None} for v in owner["labels"].values()))
        ids = [v["id"] for v in key["items"].values()]
        self.assertEqual(sorted(ids), sorted(set(ids)))
        self.assertEqual(sorted(ids), [i["id"] for i in items])
        self.assertEqual((key["audit_seed"], key["bootstrap_seed"]), (SEED, run["rng_seed"]))
        self.assertEqual(sum(sum(c.values()) for c in key["allocation"].values()), len(items))
        for k, info in key["items"].items():
            self.assertEqual(shown["items"][int(k) - 1]["answer"], next(i for i in items if i["id"] == info["id"])["case"]["answer"])

    def test_the_line_holds_no_primary_figure(self):
        self.finish()
        code, text = self.export()
        self.assertEqual(code, 0, text)
        figures = {"%.3f" % cell["primary"] for by_fixture in self.scores()["per_fixture"].values()
                   for cell in by_fixture.values() if cell["primary"] is not None}
        self.assertTrue(figures)
        self.assertEqual([f for f in figures if f in text], [])
        self.assertNotIn("kappa", text)

    def test_the_same_inputs_give_the_same_bytes(self):
        self.finish()
        self.assertEqual(self.export()[0], 0)
        first = {n: (self.out / n).read_bytes() for n in os.listdir(self.out)}
        again = self.a.tmp / "audit-again"
        self.assertEqual(self.export(out=again)[0], 0)
        self.assertEqual({n: (again / n).read_bytes() for n in os.listdir(again)}, first)

    def refused(self, want, needle=None, **kw):
        code, text = self.export(**kw)
        self.assertEqual(code, want, text)
        if needle:
            self.assertIn(needle, text)
        return text

    def test_an_existing_out_is_refused(self):
        self.finish()
        self.out.mkdir()
        self.refused(2, "already exists")
        self.assertEqual(os.listdir(self.out), [])

    def test_an_out_inside_the_clone_is_refused(self):
        self.finish()
        inside = claude_run.ROOT / "audit-test-should-not-exist"
        self.refused(2, "inside the repository clone", out=inside)
        self.assertFalse(os.path.lexists(inside))

    def test_a_wrong_audit_seed_stops(self):
        self.finish()
        data = self.a.seal_json()
        data["audit_seed"] = 1
        (self.a.seal_dir / "seal.json").write_text(json.dumps(data), encoding="utf-8")
        self.refused(1, "changed:audit_seed")
        self.assertFalse(os.path.lexists(self.out))

    def test_scores_without_labels_stop(self):
        self.finish()
        data = self.scores()
        del data["labels"]
        (self.a.final / "scores.json").write_text(json.dumps(data), encoding="utf-8")
        self.refused(1, "no labels block")
        self.assertFalse(os.path.lexists(self.out))

    def test_scores_that_are_not_the_sealed_record_stop(self):
        self.finish()
        data = self.scores()
        data["scorable"][st.HIDS[0]] = data["scorable"][st.HIDS[0]][:-1]
        (self.a.final / "scores.json").write_text(json.dumps(data), encoding="utf-8")
        self.refused(1, "not the final scoring of the sealed set")
        self.assertFalse(os.path.lexists(self.out))

    def test_pre_seal_results_are_not_final(self):
        self.refused(1, "not the final scoring", results=self.a.pre)
        self.assertFalse(os.path.lexists(self.out))

    def test_results_that_changed_since_the_seal_stop(self):
        self.finish()
        labels = self.a.final / "labels.json"
        labels.chmod(0o644)
        labels.write_bytes(labels.read_bytes() + b" ")
        self.refused(1, "changed:labels.json")
        self.assertFalse(os.path.lexists(self.out))

    def test_a_failure_after_the_folder_is_made_removes_it(self):
        self.finish()
        from unittest import mock
        with mock.patch.object(claude_run, "atomic_write", side_effect=OSError("disk")), self.assertRaises(OSError):
            self.export()
        self.assertFalse(os.path.lexists(self.out))


class CliTests(unittest.TestCase):
    """T12: the two commands on the command line."""

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.tmp = Path(td.name).resolve()

    def run_cli(self, *args):
        env = dict(os.environ)
        env.pop("CPR_EVAL_LIVE", None)
        env["TMPDIR"] = str(self.tmp)
        return subprocess.run([sys.executable, exp0001_paths.RUN, *args], capture_output=True, text=True,
                              encoding="utf-8", env=env, stdin=subprocess.DEVNULL)

    def test_a_missing_flag_exits_2(self):
        for args in (["audit-export", "--seal", "s", "--fixtures-root", "f", "--out", "o"],
                     ["audit-export", "--from", "r", "--fixtures-root", "f", "--out", "o"],
                     ["audit-export", "--from", "r", "--seal", "s", "--out", "o"],
                     ["audit-export", "--from", "r", "--seal", "s", "--fixtures-root", "f"],
                     ["audit-score", "--labels", "l", "--out", "o"],
                     ["audit-score", "--key", "k", "--out", "o"],
                     ["audit-score", "--key", "k", "--labels", "l"]):
            with self.subTest(args=args):
                got = self.run_cli(*args)
                self.assertEqual(got.returncode, 2, got.stdout + got.stderr)
                self.assertIn("required", got.stderr)

    def test_audit_score_writes_audit_json(self):
        key, owner = kappa_case()
        (self.tmp / "key.json").write_text(json.dumps(key), encoding="utf-8")
        (self.tmp / "owner.json").write_text(json.dumps(owner), encoding="utf-8")
        got = self.run_cli("audit-score", "--key", str(self.tmp / "key.json"), "--labels", str(self.tmp / "owner.json"),
                           "--out", str(self.tmp / "audit"))
        self.assertEqual(got.returncode, 0, got.stdout + got.stderr)
        kappa = audit.summarise(key, owner)["kappa"]
        self.assertEqual(got.stdout, "audit-score: n 100, kappa %.3f (bar 0.6); wrote audit.json\n" % kappa)
        self.assertIn("kappa 0.940 ", got.stdout)
        written = json.loads((self.tmp / "audit" / "audit.json").read_text(encoding="utf-8"))
        self.assertEqual(written, dict(audit.summarise(key, owner), version=1))

    def test_audit_score_refusals(self):
        key, owner = kappa_case()
        (self.tmp / "key.json").write_text(json.dumps(key), encoding="utf-8")
        del owner["labels"]["5"]
        (self.tmp / "owner.json").write_text(json.dumps(owner), encoding="utf-8")
        got = self.run_cli("audit-score", "--key", str(self.tmp / "key.json"), "--labels", str(self.tmp / "owner.json"),
                           "--out", str(self.tmp / "audit"))
        self.assertEqual(got.returncode, 1, got.stdout + got.stderr)
        self.assertFalse((self.tmp / "audit").exists())
        got = self.run_cli("audit-score", "--key", str(self.tmp / "nope.json"), "--labels", str(self.tmp / "owner.json"),
                           "--out", str(self.tmp / "audit"))
        self.assertEqual(got.returncode, 1, got.stdout + got.stderr)
        (self.tmp / "audit").mkdir()
        got = self.run_cli("audit-score", "--key", str(self.tmp / "key.json"), "--labels", str(self.tmp / "owner.json"),
                           "--out", str(self.tmp / "audit"))
        self.assertEqual(got.returncode, 2, got.stdout + got.stderr)


if __name__ == "__main__":
    unittest.main()
