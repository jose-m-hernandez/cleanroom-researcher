"""Tests for `run --supersede <ids> --repair-log <file>` of EXP-0001: the floor and oracle re-check of repaired held-out
fixtures. The old sessions and arm texts are moved aside, never deleted; only the moved units run again; every other
unit stays; a refused call changes nothing.

A scripted `claude` stands in for the live service (tests/test_exp0001_score.py); nothing here calls the real CLI.
Run with: python3 -m unittest discover tests
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import unittest
from pathlib import Path
from unittest import mock

from tests import exp0001_paths  # noqa: F401

import arms  # noqa: E402
import claude_run  # noqa: E402
import probes  # noqa: E402
import run as run_cli  # noqa: E402
import score  # noqa: E402
from tests import test_exp0001_seal as sealt  # noqa: E402

HIDS = sealt.HIDS
STAMP = "20261119T101112Z"


def snap(root) -> dict:
    """{relative path: sha256} of every file under root."""
    root = Path(root)
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


class SupersedeBase(sealt.World):
    """A held-out store and a floor and oracle results folder (2 fixtures, 2 seeds) made by the batch itself."""

    def setUp(self):
        super().setUp()
        self.results = self.tmp / "results-s"
        buf = io.StringIO()
        code = probes.run_batch(self.store, self.results, "heldout", HIDS, ["floor", "oracle"], 2, self.cfg, self.environ,
                                date="20260101", stdout=buf)
        self.assertEqual(code, 0, buf.getvalue())
        self.log = self.tmp / "store.repair-log.md"
        self.log.write_text("# Repair log\n\n" + "".join(f"## {fid}\n\nChanges:\n\n  none\n\n" for fid in HIDS), encoding="utf-8")
        patch = mock.patch.object(probes, "utc_stamp", lambda: STAMP)
        patch.start()
        self.addCleanup(patch.stop)

    def rerun(self, supersede=None, repair_log="default", arm_names=("floor", "oracle"), wanted="all", split="heldout",
              store=None, environ=None, runner=None, out=None, **kw):
        text = io.StringIO()
        before = len(self.calls())
        code = probes.command_run(store or self.store, out or self.results, split, wanted, list(arm_names), 2, None,
                                  "20260101", self.environ if environ is None else environ, text, runner=runner,
                                  supersede=supersede, repair_log=self.log if repair_log == "default" else repair_log,
                                  **kw)
        return code, text.getvalue(), len(self.calls()) - before

    def run_json(self):
        return arms.read_json(self.results / "run.json")

    def moved_files(self, before, ids):
        """The relative paths in `before` that belong to the floor and oracle folders of ids."""
        return [rel for rel in before if any(rel.startswith(f"{kind}/{arm}/{fid}/") for kind in ("probes", "arms")
                                             for arm in ("floor", "oracle") for fid in ids)]


class MoveTests(SupersedeBase):
    def test_the_old_folders_move_byte_for_byte_and_everything_else_stays(self):
        before = snap(self.results)
        record = probes.supersede_files(self.results, [HIDS[0]], self.log, STAMP)
        after = snap(self.results)
        moved = self.moved_files(before, [HIDS[0]])
        self.assertEqual(len(moved), 8)  # 2 arms x 2 seeds, a session file and an arm text each
        for rel in moved:
            self.assertNotIn(rel, after)
            self.assertEqual(after[f"superseded/{STAMP}/{rel}"], before[rel], rel)
        for rel, digest in before.items():
            if rel not in moved:
                self.assertEqual(after[rel], digest, rel)  # run.json included: nothing was excluded
        self.assertEqual(sorted(set(after) - set(before)),
                         sorted([f"superseded/{STAMP}/{rel}" for rel in moved] + ["superseded.json"]))
        self.assertEqual(record, arms.read_json(self.results / "superseded.json"))

    def test_the_record_holds_the_exact_keys_the_hash_and_the_moved_list(self):
        probes.supersede_files(self.results, [HIDS[0]], self.log, STAMP)
        record = arms.read_json(self.results / "superseded.json")
        self.assertEqual(set(record), {"version", "stamp", "ids", "scoring_occasion", "repair_log", "moved", "excluded_cells"})
        self.assertEqual((record["version"], record["stamp"], record["ids"]), (1, STAMP, [HIDS[0]]))
        self.assertEqual(record["repair_log"], {"name": "store.repair-log.md",
                                                "sha256": hashlib.sha256(self.log.read_bytes()).hexdigest()})
        rels = [f"{kind}/{arm}/{HIDS[0]}" for kind in ("arms", "probes") for arm in ("floor", "oracle")]
        self.assertEqual(record["moved"], [{"from": r, "to": f"superseded/{STAMP}/{r}"} for r in rels])
        self.assertEqual(record["excluded_cells"], [])
        self.assertNotIn(str(self.tmp), json.dumps(record))  # no absolute path, no machine data

    def test_the_record_opens_the_second_scoring_occasion(self):
        probes.supersede_files(self.results, [HIDS[0]], self.log, STAMP)
        record = arms.read_json(self.results / "superseded.json")
        self.assertEqual(record["scoring_occasion"], 2)
        self.assertEqual(score.current_occasion(self.results), 2)

    def test_ids_are_recorded_sorted_and_both_fixtures_can_move(self):
        record = probes.supersede_files(self.results, [HIDS[1], HIDS[0]], self.log, STAMP)
        self.assertEqual(record["ids"], HIDS)
        self.assertEqual(len(record["moved"]), 8)
        self.assertEqual([m["from"] for m in record["moved"]], sorted(m["from"] for m in record["moved"]))

    def test_preserve_files_of_a_superseded_fixture_move_too(self):
        folder = self.results / "preserve" / "floor" / HIDS[0]
        folder.mkdir(parents=True)
        (folder / "s1.json").write_text("{}", encoding="utf-8")
        record = probes.supersede_files(self.results, [HIDS[0]], self.log, STAMP)
        self.assertFalse(folder.exists())
        self.assertTrue((self.results / "superseded" / STAMP / "preserve" / "floor" / HIDS[0] / "s1.json").is_file())
        self.assertIn(f"preserve/floor/{HIDS[0]}", [m["from"] for m in record["moved"]])

    def test_a_move_failure_after_the_first_folder_puts_everything_back(self):
        before = snap(self.results)
        real, seen = os.rename, []

        def flaky(src, dst):
            seen.append(src)
            if len(seen) == 2:
                raise OSError("disk trouble")
            return real(src, dst)
        with mock.patch.object(probes.os, "rename", flaky), self.assertRaises(probes.BatchStop):
            probes.supersede_files(self.results, [HIDS[0]], self.log, STAMP)
        self.assertGreaterEqual(len(seen), 3)  # the first move, the failing one, and at least one put back
        self.assertEqual(snap(self.results), before)
        self.assertFalse((self.results / "superseded").exists())

    def test_the_same_stamp_twice_refuses_without_moving(self):
        (self.results / "superseded" / STAMP).mkdir(parents=True)
        before = snap(self.results)
        with self.assertRaises(probes.BatchStop):
            probes.supersede_files(self.results, [HIDS[0]], self.log, STAMP)
        self.assertEqual(snap(self.results), before)


class ExcludedCellsTests(SupersedeBase):
    def setUp(self):
        super().setUp()
        meta = self.run_json()
        mine = {"kind": "probe", "arm": "floor", "fixture": HIDS[0], "seed": 1, "checks": ["markers_found"]}
        other = {"kind": "probe", "arm": "oracle", "fixture": HIDS[1], "seed": 2, "checks": ["markers_found"]}
        meta["excluded_cells"] = [mine, other]
        meta["isolation_reruns"] = [dict(mine), dict(other)]
        meta["outcome_only"] = [dict(mine, checks=["denials"])]
        meta["proof_counts"] = probes.proof_counts(meta)
        arms.write_json(self.results / "run.json", meta)
        # the excluded cell has no session file
        (self.results / "probes" / "floor" / HIDS[0] / "s1.json").unlink()
        (self.results / "probes" / "oracle" / HIDS[1] / "s2.json").unlink()
        self.mine, self.other = mine, other

    def test_the_entries_of_the_superseded_fixture_move_into_the_record_and_the_others_stay(self):
        record = probes.supersede_files(self.results, [HIDS[0]], self.log, STAMP)
        self.assertEqual(record["excluded_cells"], [self.mine])
        meta = self.run_json()
        self.assertEqual(meta["excluded_cells"], [self.other])
        self.assertEqual(meta["isolation_reruns"], [self.mine, self.other])  # events that happened: they stay
        self.assertEqual(meta["outcome_only"], [dict(self.mine, checks=["denials"])])

    def test_the_counts_and_the_isolation_file_follow(self):
        probes.supersede_files(self.results, [HIDS[0]], self.log, STAMP)
        meta = self.run_json()
        self.assertEqual(meta["proof_counts"], {"isolation_reruns": 2, "excluded_after_rerun": 1,
                                                "outcome_only_by_check": {"denials": 1}})
        shown = arms.read_json(probes.REPO_ROOT.joinpath(*claude_run.RECORDED_DIR, "runs", meta["run_id"], "isolation.json"))
        self.assertEqual(shown["excluded"]["count"], 1)
        self.assertEqual(shown["proof_counts"], meta["proof_counts"])

    def test_the_excluded_cell_no_longer_blocks_the_rerun(self):
        code, text, launched = self.rerun(supersede=[HIDS[0]])
        self.assertEqual(code, 0, text)
        self.assertTrue((self.results / "probes" / "floor" / HIDS[0] / "s1.json").is_file())
        self.assertEqual(launched, 4)
        # the other fixture's excluded cell is still excluded: its session file was not recreated
        self.assertFalse((self.results / "probes" / "oracle" / HIDS[1] / "s2.json").exists())
        self.assertEqual(self.run_json()["excluded_cells"], [self.other])


class RerunTests(SupersedeBase):
    def test_exactly_the_moved_units_run_again(self):
        before = snap(self.results)
        code, text, launched = self.rerun(supersede=[HIDS[0]])
        self.assertEqual(code, 0, text)
        self.assertIn(f"run: superseded 1 fixtures (4 folders moved) into superseded/{STAMP}/", text)
        self.assertRegex(text, r"run: 4 units run, 4 already present")
        self.assertEqual(launched, 4)
        after = snap(self.results)
        for rel in self.moved_files(before, [HIDS[1]]):
            self.assertEqual(after[rel], before[rel], rel)  # the other fixture's files are untouched
        for arm in ("floor", "oracle"):
            for seed in (1, 2):
                self.assertTrue(arms.probe_path(self.results, arm, HIDS[0], seed).is_file())

    def test_the_old_raw_records_are_not_overwritten(self):
        run_id = self.run_json()["raw_run_id"]
        folder = arms.raw_dir(run_id, self.environ)
        first = folder / f"probe-floor-{HIDS[0]}-s1.json"
        self.assertTrue(first.is_file())
        old = first.read_bytes()
        self.assertEqual(self.rerun(supersede=[HIDS[0]])[0], 0)
        self.assertEqual(first.read_bytes(), old)
        self.assertTrue((folder / f"probe-floor-{HIDS[0]}-s1-a2.json").is_file())
        self.assertFalse((folder / f"probe-floor-{HIDS[1]}-s1-a2.json").exists())

    def test_the_rerun_reads_the_arm_text_of_the_new_spec_and_not_the_old_one(self):
        old_text = arms.arm_text_path(self.results, "oracle", HIDS[0], 1).read_text(encoding="utf-8")
        new_store = self.tmp / "store-2"
        shutil.copytree(self.store, new_store)
        spec_path = new_store / HIDS[0] / "spec.json"
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        needed = [i for i in spec["items"] if i["needed"]][0]
        needed["oracle_line"] += " (as repaired)"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        new_text = arms.static_text("oracle", spec)
        self.assertNotEqual(new_text, old_text)
        code, text, _ = self.rerun(supersede=[HIDS[0]], store=new_store)
        self.assertEqual(code, 0, text)
        written = arms.arm_text_path(self.results, "oracle", HIDS[0], 1).read_text(encoding="utf-8")
        self.assertEqual(written, new_text)
        self.assertNotEqual(written, old_text)
        kept = self.results / "superseded" / STAMP / "arms" / "oracle" / HIDS[0] / "s1.md"
        self.assertEqual(kept.read_text(encoding="utf-8"), old_text)  # the old text is moved, not lost

    def test_a_plain_run_afterwards_has_nothing_to_do_and_a_second_supersede_refuses(self):
        self.assertEqual(self.rerun(supersede=[HIDS[0]])[0], 0)
        code, text, launched = self.rerun(repair_log=None)
        self.assertEqual((code, launched), (0, 0), text)
        self.assertIn("run: 0 units run, 8 already present", text)
        code, text, launched = self.rerun(supersede=[HIDS[0]])
        self.assertEqual((code, launched), (2, 0), text)
        self.assertEqual(text, "run: REFUSED this folder already holds a superseded record (one repair round, P:252)\n")

    def test_scores_labels_and_the_pass_counter_are_not_touched(self):
        judge = sealt.base.FakeJudge(sealt.HSPECS)
        scores = score.score_run(self.results, sealt.HSPECS, self.cfg, self.environ, judge.label_probe, judge.label_items)
        score.write_scores(self.results, dict(scores, **{"pass": {"id": 1, "mode": "normal"}}))
        score.pass_count(self.results, "normal", "started")
        names = ("score-passes.json", "scores.json", "labels.json")
        before = {n: (self.results / n).read_bytes() for n in names}
        self.assertEqual(self.rerun(supersede=[HIDS[0]])[0], 0)
        self.assertEqual({n: (self.results / n).read_bytes() for n in names}, before)
        self.assertFalse((self.results / "score-passes-occasion-2.json").exists())


class RefusalTests(SupersedeBase):
    def refuses(self, label, **kw):
        before, quiet = snap(self.results), sealt.Quiet()
        calls = len(self.calls())
        code, text, _ = self.rerun(runner=quiet, **kw)
        self.assertEqual((code, text), (2, f"run: REFUSED {label}\n"))
        self.assertEqual(quiet.calls, [])
        self.assertEqual(len(self.calls()), calls)
        self.assertEqual(snap(self.results), before)
        self.assertFalse((self.results / "superseded").exists())

    def test_1_only_one_of_the_two_flags(self):
        label = "--supersede and --repair-log go together"
        self.refuses(label, supersede=[HIDS[0]], repair_log=None)
        self.refuses(label, supersede=None, repair_log=self.log)

    def test_2_floor_and_oracle_on_held_out_only(self):
        label = "--supersede is for the floor and oracle re-check of held-out fixtures only"
        self.refuses(label, supersede=[HIDS[0]], seal_dir=self.seal_dir)
        self.refuses(label, supersede=[HIDS[0]], preseal=self.pre)
        self.refuses(label, supersede=[HIDS[0]], arm_names=("floor", "baseline"))
        self.refuses(label, supersede=[HIDS[0]], arm_names=("floor", "oracle", "variant-a"))
        self.refuses(label, supersede=[HIDS[0]], split="dev")

    def test_3_ids_of_the_held_out_form(self):
        label = "--supersede needs ids of the form heldout-NNN-name"
        for ids in (["dev-001-tide-table"], ["heldout-1-x"], ["heldout-001-Tide"], [HIDS[0], "nope"], []):
            self.refuses(label, supersede=ids)

    def test_4_the_fixture_is_in_the_results_folder(self):
        self.refuses("fixture heldout-005-nope is not in this results folder", supersede=["heldout-005-nope"])
        self.refuses(f"fixture {HIDS[0]} is not in this results folder", supersede=[HIDS[0]], out=self.tmp / "no-results")

    def test_5_the_floor_and_oracle_sessions_exist(self):
        shutil.rmtree(self.results / "probes" / "oracle" / HIDS[0])
        self.refuses(f"fixture {HIDS[0]} has no floor and oracle sessions to supersede", supersede=[HIDS[0]])
        shutil.rmtree(self.results / "probes" / "floor" / HIDS[1])
        self.refuses(f"fixture {HIDS[1]} has no floor and oracle sessions to supersede", supersede=[HIDS[1]])

    def test_6_the_repair_log_exists_and_names_every_id(self):
        self.refuses("the repair log is missing", supersede=[HIDS[0]], repair_log=self.tmp / "no-log.md")
        short = self.tmp / "short.md"
        short.write_text(f"# Repair log\n\n## {HIDS[1]}\n", encoding="utf-8")
        self.refuses(f"the repair log does not name fixture {HIDS[0]}", supersede=[HIDS[0]], repair_log=short)
        self.refuses(f"the repair log does not name fixture {HIDS[0]}", supersede=[HIDS[1], HIDS[0]], repair_log=short)
        loose = self.tmp / "loose.md"  # a section line is exactly "## <id>": a mention is not one
        loose.write_text(f"see {HIDS[0]}\n### {HIDS[0]}\n## {HIDS[0]} extra\n", encoding="utf-8")
        self.refuses(f"the repair log does not name fixture {HIDS[0]}", supersede=[HIDS[0]], repair_log=loose)

    def test_7_one_repair_round_per_folder(self):
        arms.write_json(self.results / "superseded.json", {"version": 1})
        self.refuses("this folder already holds a superseded record (one repair round, P:252)", supersede=[HIDS[0]])

    def test_8_the_selection_includes_every_superseded_id(self):
        self.refuses(f"fixture {HIDS[0]} is not selected by --fixtures", supersede=[HIDS[0]], wanted=HIDS[1])

    def test_the_first_refusal_that_fires_wins(self):
        self.refuses("--supersede needs ids of the form heldout-NNN-name", supersede=["nope"], repair_log=self.tmp / "none")
        self.refuses("--supersede is for the floor and oracle re-check of held-out fixtures only", supersede=["nope"],
                     split="dev")
        self.refuses("fixture heldout-005-nope is not in this results folder", supersede=["heldout-005-nope"],
                     repair_log=self.tmp / "none")
        self.refuses("the repair log is missing", supersede=[HIDS[0]], repair_log=self.tmp / "none", wanted=HIDS[1])


class GateTests(SupersedeBase):
    def test_a_refused_live_gate_moves_nothing(self):
        before = snap(self.results)
        environ = {k: v for k, v in self.environ.items() if k != "CPR_EVAL_LIVE"}
        code, text, launched = self.rerun(supersede=[HIDS[0]], environ=environ)
        self.assertEqual((code, launched), (2, 0), text)
        self.assertIn("live run refused", text)
        self.assertEqual(snap(self.results), before)

    def test_a_failed_preflight_moves_nothing(self):
        before = snap(self.results)
        with mock.patch.object(claude_run, "preflight", lambda cfg, environ=None: "claude is the wrong version"):
            code, text, launched = self.rerun(supersede=[HIDS[0]])
        self.assertEqual((code, launched), (1, 0), text)
        self.assertIn("preflight", text)
        self.assertEqual(snap(self.results), before)

    def test_a_changed_pin_or_an_invalid_new_spec_stops_before_anything_moves(self):
        before = snap(self.results)
        with mock.patch.object(claude_run, "load_config", lambda path=None: dict(self.cfg, model="another-model")):
            code, text, launched = self.rerun(supersede=[HIDS[0]])
        self.assertEqual((code, launched), (1, 0), text)
        self.assertIn("run: STOP model changed since this run started", text)
        self.assertEqual(snap(self.results), before)
        broken = self.tmp / "store-broken"
        shutil.copytree(self.store, broken)
        spec_path = broken / HIDS[0] / "spec.json"
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        spec["probes"] = []
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        code, text, launched = self.rerun(supersede=[HIDS[0]], store=broken)
        self.assertEqual((code, launched), (1, 0), text)
        self.assertIn(f"run: STOP spec invalid: {HIDS[0]}", text)
        self.assertEqual(snap(self.results), before)

    def test_a_move_failure_stops_the_command_with_the_folder_as_it_was(self):
        before = snap(self.results)
        real, seen = os.rename, []

        def flaky(src, dst):
            seen.append(src)
            if len(seen) == 2:
                raise OSError("disk trouble")
            return real(src, dst)
        with mock.patch.object(probes.os, "rename", flaky):
            code, text, launched = self.rerun(supersede=[HIDS[0]])
        self.assertEqual((code, launched), (1, 0), text)
        self.assertIn("run: STOP supersede failed, nothing moved", text)
        self.assertEqual(snap(self.results), before)


class SeedsTests(SupersedeBase):
    """`run --supersede` reruns exactly the seeds the folder recorded: --seeds must equal run.json's `seeds`."""

    def go(self, seeds, supersede=None, runner=None, **kw):
        text = io.StringIO()
        code = probes.command_run(self.store, self.results, "heldout", "all", ["floor", "oracle"], seeds, None, "20260101",
                                  self.environ, text, runner=runner, supersede=[HIDS[0]] if supersede is None else supersede,
                                  repair_log=self.log, **kw)
        return code, text.getvalue()

    def set_seeds(self, value, drop=False):
        meta = self.run_json()
        if drop:
            del meta["seeds"]
        else:
            meta["seeds"] = value
        arms.write_json(self.results / "run.json", meta)

    def refuses(self, label, seeds):
        before, quiet = snap(self.results), sealt.Quiet()
        calls = len(self.calls())
        code, text = self.go(seeds, runner=quiet)
        self.assertEqual((code, text), (2, f"run: REFUSED {label}\n"))
        self.assertEqual(quiet.calls, [])
        self.assertEqual(len(self.calls()), calls)
        self.assertEqual(snap(self.results), before)
        self.assertFalse((self.results / "superseded").exists())

    def test_more_seeds_than_recorded_refuses(self):
        self.refuses("--seeds 3 must equal the 2 seeds recorded in run.json", 3)

    def test_fewer_seeds_than_recorded_refuses(self):
        self.refuses("--seeds 1 must equal the 2 seeds recorded in run.json", 1)

    def test_the_same_seeds_run_as_before(self):
        code, text = self.go(2)
        self.assertEqual(code, 0, text)
        self.assertTrue((self.results / "superseded.json").is_file())

    def test_a_run_json_without_an_integer_seeds_refuses(self):
        label = "run.json does not record the seeds"
        self.set_seeds(None, drop=True)
        self.refuses(label, 2)
        self.set_seeds("2")
        self.refuses(label, 2)
        self.set_seeds(True)
        self.refuses(label, 1)

    def test_the_superseded_record_refusal_comes_first(self):
        arms.write_json(self.results / "superseded.json", {"version": 1})
        self.refuses("this folder already holds a superseded record (one repair round, P:252)", 3)

    def test_a_plain_run_with_other_seeds_is_unchanged(self):
        code, text = self.plain(3)
        self.assertEqual(code, 0, text)
        self.assertNotIn("must equal", text)
        self.assertEqual(self.run_json()["seeds"], 3)

    def plain(self, seeds):
        text = io.StringIO()
        code = probes.command_run(self.store, self.results, "heldout", "all", ["floor", "oracle"], seeds, None, "20260101",
                                  self.environ, text)
        return code, text.getvalue()


class PreflightTests(SupersedeBase):
    """Each refusal of supersede_preflight stops the command (exit 1) before anything moves."""

    def stops(self, label, cfg=None, store=None, line=None):
        before, quiet = snap(self.results), sealt.Quiet()
        calls = len(self.calls())
        if cfg is None:
            code, text, _ = self.rerun(supersede=[HIDS[0]], runner=quiet, store=store)
        else:
            with mock.patch.object(claude_run, "load_config", lambda path=None: cfg):
                code, text, _ = self.rerun(supersede=[HIDS[0]], runner=quiet, store=store)
        self.assertEqual((code, text.splitlines()[-1]), (1, line or f"run: STOP {label}"), text)
        self.assertEqual(quiet.calls, [])
        self.assertEqual(len(self.calls()), calls)
        self.assertEqual(snap(self.results), before)
        self.assertFalse((self.results / "superseded").exists())
        self.assertFalse((self.results / "superseded.json").exists())

    def test_g_the_rng_seed_changed(self):
        self.stops("rng_seed changed since this run started", dict(self.cfg, rng_seed=self.cfg.get("rng_seed", 0) + 1))

    def test_h_the_claude_code_version_changed(self):
        self.stops("claude_code_version changed since this run started", dict(self.cfg, claude_code_version="0.0.1-other"))

    def test_i_the_plugin_ref_changed(self):
        self.stops("plugin_ref changed since this run started", dict(self.cfg, plugin_ref="other-ref"))

    def test_j_the_split_changed(self):
        meta = self.run_json()
        meta["split"] = "dev"
        arms.write_json(self.results / "run.json", meta)
        self.stops("split changed since this run started")

    def test_k_the_raw_folder_belongs_to_another_results_folder(self):
        raw = arms.raw_dir(self.run_json()["raw_run_id"], self.environ)
        owner = arms.read_json(raw / arms.OWNER_FILE)
        owner["results_folder"] = str(self.tmp)
        arms.write_json(raw / arms.OWNER_FILE, owner)
        # arms.RawError is not an ArmError: the preflight turns it into a BatchStop that carries the real message (exit 1)
        self.stops("", line="run: STOP raw folder belongs to another results folder: a copied or moved results folder "
                            "must be run into a new --out")

    def test_l_a_missing_spec_of_a_superseded_id(self):
        # select_fixtures drops a fixture folder without a spec.json, so the id is refused (exit 2) before the preflight
        broken = self.tmp / "store-nospec"
        shutil.copytree(self.store, broken)
        (broken / HIDS[0] / "spec.json").unlink()
        quiet = sealt.Quiet()
        before = snap(self.results)
        code, text, _ = self.rerun(supersede=[HIDS[0]], runner=quiet, store=broken)
        self.assertEqual((code, text), (2, f"run: REFUSED fixture {HIDS[0]} is not selected by --fixtures\n"))
        self.assertEqual(quiet.calls, [])
        self.assertEqual(snap(self.results), before)

    def test_m_the_raw_folder_is_missing(self):
        shutil.rmtree(arms.raw_dir(self.run_json()["raw_run_id"], self.environ))
        self.stops("", line="run: STOP raw folder missing: TMPDIR changed or the folder was swept; resume with the "
                            "TMPDIR the run started with, or run into a new --out")


class PreflightSpecTests(SupersedeBase):
    """An unreadable spec is a BatchStop, not a traceback, whether the preflight is called directly or through run."""

    def preflight(self, store):
        probes.supersede_preflight(store, self.results, [HIDS[0]], self.cfg, "heldout", self.environ)

    def broken_store(self):
        broken = self.tmp / "store-broken"
        shutil.copytree(self.store, broken)
        return broken, broken / HIDS[0] / "spec.json"

    def test_n_direct_call_missing_spec(self):
        broken, spec = self.broken_store()
        spec.unlink()
        with self.assertRaisesRegex(probes.BatchStop, f"^spec unreadable: {HIDS[0]}$"):
            self.preflight(broken)

    def test_o_direct_call_invalid_json(self):
        broken, spec = self.broken_store()
        spec.write_text("{not json", encoding="utf-8")
        with self.assertRaisesRegex(probes.BatchStop, f"^spec unreadable: {HIDS[0]}$"):
            self.preflight(broken)

    def test_p_direct_call_spec_is_a_directory(self):
        broken, spec = self.broken_store()
        spec.unlink()
        spec.mkdir()
        with self.assertRaisesRegex(probes.BatchStop, f"^spec unreadable: {HIDS[0]}$"):
            self.preflight(broken)

    def test_q_through_run_invalid_json_stops_in_selection(self):
        broken, spec = self.broken_store()
        spec.write_text("{not json", encoding="utf-8")
        before, quiet = snap(self.results), sealt.Quiet()
        calls = len(self.calls())
        code, text, _ = self.rerun(supersede=[HIDS[0]], runner=quiet, store=broken)
        self.assertEqual((code, text.splitlines()[-1]), (1, f"run: STOP spec unreadable: {HIDS[0]}"), text)
        self.assertEqual(quiet.calls, [])
        self.assertEqual(len(self.calls()), calls)
        self.assertEqual(snap(self.results), before)
        self.assertFalse((self.results / "superseded").exists())
        self.assertFalse((self.results / "superseded.json").exists())


class SupersedeOrderTests(SupersedeBase):
    """Every read the record needs happens before the first move; the record is written right after the moves."""

    def stops(self, label):
        before = snap(self.results)
        listing = sorted(p.relative_to(self.results).as_posix() for p in self.results.rglob("*"))
        with self.assertRaises(probes.BatchStop) as caught:
            probes.supersede_files(self.results, [HIDS[0]], self.log, STAMP)
        self.assertEqual(str(caught.exception), f"supersede failed, nothing moved: {label}")
        self.assertEqual(snap(self.results), before)
        self.assertEqual(sorted(p.relative_to(self.results).as_posix() for p in self.results.rglob("*")), listing)
        self.assertFalse((self.results / "superseded").exists())
        self.assertFalse((self.results / "superseded.json").exists())

    def only_record_fails(self):
        real = arms.write_json

        def flaky(path, data):
            if Path(path) == self.results / "superseded.json":
                raise OSError("disk full")
            return real(path, data)
        return mock.patch.object(probes.arms, "write_json", flaky)

    def test_a_missing_repair_log_leaves_the_folder_untouched(self):
        self.log.unlink()
        self.stops("FileNotFoundError")

    def test_a_run_json_that_is_not_json_leaves_the_folder_untouched(self):
        (self.results / "run.json").write_text("{not json", encoding="utf-8")
        self.stops("JSONDecodeError")

    def test_a_malformed_entry_that_proof_counts_cannot_read_leaves_the_folder_untouched(self):
        meta = self.run_json()
        meta["outcome_only"] = [{"checks": 5}]  # proof_counts iterates the checks of every entry
        arms.write_json(self.results / "run.json", meta)
        self.stops("TypeError")

    def test_a_failed_record_write_puts_everything_back_and_leaves_no_record(self):
        before = snap(self.results)
        with self.only_record_fails(), self.assertRaises(probes.BatchStop) as caught:
            probes.supersede_files(self.results, [HIDS[0]], self.log, STAMP)
        self.assertEqual(str(caught.exception), "supersede failed, nothing moved: OSError")
        self.assertEqual(snap(self.results), before)
        self.assertFalse((self.results / "superseded").exists())
        self.assertFalse((self.results / "superseded.json").exists())

    def test_a_partial_record_is_removed_by_the_rollback(self):
        real = arms.write_json

        def torn(path, data):
            if Path(path) == self.results / "superseded.json":
                Path(path).write_text('{"version"', encoding="utf-8")
                raise OSError("disk full")
            return real(path, data)
        before = snap(self.results)
        with mock.patch.object(probes.arms, "write_json", torn), self.assertRaises(probes.BatchStop):
            probes.supersede_files(self.results, [HIDS[0]], self.log, STAMP)
        self.assertEqual(snap(self.results), before)
        self.assertFalse((self.results / "superseded.json").exists())

    def test_a_failed_isolation_write_comes_after_the_record_and_is_not_wrapped(self):
        before = snap(self.results)
        with mock.patch.object(probes, "write_isolation", side_effect=OSError("disk full")), self.assertRaises(OSError):
            probes.supersede_files(self.results, [HIDS[0]], self.log, STAMP)
        record = arms.read_json(self.results / "superseded.json")
        self.assertEqual(set(record), {"version", "stamp", "ids", "scoring_occasion", "repair_log", "moved", "excluded_cells"})
        self.assertEqual(record["stamp"], STAMP)
        for rel in self.moved_files(before, [HIDS[0]]):
            self.assertEqual(snap(self.results)[f"superseded/{STAMP}/{rel}"], before[rel], rel)

    def test_a_plain_run_resumes_after_a_failed_isolation_write(self):
        before = snap(self.results)
        with mock.patch.object(probes, "write_isolation", side_effect=OSError("disk full")):
            code, text, _ = self.rerun(supersede=[HIDS[0]])
        self.assertEqual(code, 1, text)
        self.assertEqual(text.splitlines()[-1], "run: ERROR OSError")
        code, text, launched = self.rerun(repair_log=None)
        self.assertEqual(code, 0, text)
        self.assertIn("run: 4 units run, 4 already present", text)
        self.assertEqual(launched, 4)
        after = snap(self.results)
        for rel in self.moved_files(before, [HIDS[0]]):
            self.assertIn(rel, after)
        for rel in self.moved_files(before, [HIDS[1]]):
            self.assertEqual(after[rel], before[rel], rel)


class CliTests(SupersedeBase):
    def main(self, *flags):
        argv = ["run", "--out", str(self.results), "--split", "heldout", "--fixtures-root", str(self.store), *flags]
        with mock.patch.dict(os.environ, self.environ), mock.patch.object(probes, "command_run", return_value=0) as ran, \
                mock.patch("sys.stdout", new=io.StringIO()):
            code = run_cli.main(argv)
        return code, ran

    def test_the_two_flags_reach_command_run(self):
        code, ran = self.main("--arms", "floor,oracle", "--supersede", f"{HIDS[0]}, {HIDS[1]}", "--repair-log", str(self.log))
        self.assertEqual(code, 0)
        kwargs = ran.call_args.kwargs
        self.assertEqual((kwargs["supersede"], kwargs["repair_log"]), (HIDS, str(self.log)))

    def test_without_them_command_run_gets_none(self):
        code, ran = self.main()
        self.assertEqual(code, 0)
        kwargs = ran.call_args.kwargs
        self.assertEqual((kwargs["supersede"], kwargs["repair_log"]), (None, None))

    def test_the_help_says_what_the_flags_do(self):
        help_text = io.StringIO()
        with mock.patch("sys.stdout", new=help_text), self.assertRaises(SystemExit):
            run_cli.main(["run", "--help"])
        flat = re.sub(r"\s+", " ", help_text.getvalue())
        self.assertIn("never deleted", flat)
        self.assertIn("superseded.json", flat)


if __name__ == "__main__":
    unittest.main()
