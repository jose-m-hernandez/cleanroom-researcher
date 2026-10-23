"""Tests for arms, probe sessions, the batch and scoring. A scripted `claude` executable and fake judges stand in
for the live services; nothing here calls the real CLI.

Run with: python3 -m unittest discover tests
"""

from __future__ import annotations

import copy
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests import exp0001_paths  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]

import arms  # noqa: E402
import claude_run  # noqa: E402
import fixtures  # noqa: E402
import judge  # noqa: E402
import probes  # noqa: E402
import score  # noqa: E402
from tests import exp0001_legacy_labels as legacy_labels  # noqa: E402

# the invented fixtures and results under tests/data
FIXTURES = ROOT / "tests" / "data" / "exp0001-fixtures" / "dev"
IDS = ["dev-001-tide-table", "dev-002-recipe-scaler"]
HOST_MARKER = "a synthetic host instruction line used as a marker"
JUNK = "- The maintainer drinks herbal tea."


def spec_of(fid) -> dict:
    return fixtures.load_spec(FIXTURES / fid / "spec.json")


try:
    SPECS = {fid: spec_of(fid) for fid in IDS}
except FileNotFoundError:  # without the data under tests/data the module still imports, and each test that reads it fails
    SPECS = {}


# --------------------------------------------------------------------------
# A scripted claude: reads pins and a plan from files next to it
# --------------------------------------------------------------------------

FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, os, re, sys
here = os.path.dirname(os.path.abspath(__file__))
a = sys.argv[1:]
if a and a[0] == "--version":
    print("2.1.302 (Claude Code)")
    sys.exit(0)
pins = json.load(open(os.path.join(here, "pins.json")))
plan = json.load(open(os.path.join(here, "plan.json")))
prompt = a[1]


def opt(name):
    return a[a.index(name) + 1] if name in a else None


open(os.path.join(here, "calls.jsonl"), "a").write(json.dumps({"argv": a, "cwd": os.getcwd()}) + "\n")
resume = opt("--resume")
sid = resume or opt("--session-id")
cwd = os.path.realpath(os.getcwd())
slug = re.sub(r"[^A-Za-z0-9]", "-", cwd)
pdir = os.path.join(os.environ["HOME"], ".claude", "projects", slug)
os.makedirs(pdir, exist_ok=True)
text = open("CLAUDE.md").read()
fx = plan["fixtures"].get(text.split("\n")[0], {})
reply = "ok"
cost = plan["cost"]
if prompt.startswith("/cpr:preserve"):
    cost = plan["preserve_cost"]
    if fx.get("preserve_append") and not plan.get("preserve_noop"):
        open("CLAUDE.md", "a").write("\n" + "\n".join(fx["preserve_append"]) + "\n")
elif "Questions:" in prompt:
    block = prompt.split("Questions:")[1].split("Reply with only")[0]
    answers = []
    for pid in re.findall(r"^(\S+): ", block, re.M):
        spec = fx.get("probes", {}).get(pid, {})
        if pid in fx.get("always_correct", []):
            ans = spec["answer"]
        elif pid in fx.get("never_correct", []):
            ans = "It is something else entirely."
        elif spec.get("abstention"):
            ans = "Probably 9999." if fx.get("false_if") and fx["false_if"] in text else "UNKNOWN"
        elif spec.get("needle") and spec["needle"] in text:
            ans = spec["answer"]
        else:
            ans = "UNKNOWN"
        answers.append({"id": pid, "answer": ans})
    reply = json.dumps({"answers": answers})
    if plan.get("garbage_reply"):
        reply = "I cannot comply."
text_now = open("CLAUDE.md").read()
rows = [{"type": "attachment", "sessionId": sid, "attachment": {"type": "instructions", "files": [
    {"path": os.path.join(cwd, "CLAUDE.md"), "type": "Project", "content": "x"}]}}]
if plan.get("leak"):
    rows.append({"type": "user", "sessionId": sid, "message": {"content": plan["leak"]}})
with open(os.path.join(pdir, sid + ".jsonl"), "a") as fh:
    for r in rows:
        fh.write(json.dumps(r) + "\n")
plugins = [{"name": "cc-plugin-a", "path": "builtin", "source": "x"}]
skills = list(pins["init_allowlist"]["skills"])
if "--plugin-dir" in a:
    plugins.append({"name": "cpr", "path": opt("--plugin-dir"), "source": "cpr@inline"})
    skills.append("cpr:preserve")
init = {"type": "system", "subtype": "init", "claude_code_version": pins["claude_code_version"], "model": pins["model"],
        "plugins": plugins, "mcp_servers": [], "skills": skills, "agents": list(pins["init_allowlist"]["agents"]),
        "tools": [] if "--tools" in a else ["Read"], "memory_paths": {"auto": os.path.join(pdir, "memory") + "/"},
        "session_id": sid}
usage = {"input_tokens": 3, "cache_creation_input_tokens": 100, "cache_read_input_tokens": len(text_now), "output_tokens": 7}
result = {"type": "result", "subtype": "success", "is_error": False, "result": reply, "total_cost_usd": cost,
          "duration_ms": 5, "usage": usage, "permission_denials": []}
print(json.dumps(init))
print(json.dumps(result))
'''


def build_plan(specs: dict) -> dict:
    """Per fixture (keyed by the floor's first line): what the scripted claude appends and how it answers.

    Baseline appends the oracle lines of the first three needed items plus one junk line. A probe is answered
    correctly when its item's oracle line is in CLAUDE.md. The junk line makes abstention probes get a false answer.
    """
    plan = {"cost": 0.01, "preserve_cost": 0.2, "fixtures": {}}
    for spec in specs.values():
        items = {i["id"]: i for i in spec["items"]}
        needed = [i for i in spec["items"] if i["needed"]]
        probes_ = {}
        for p in spec["probes"]:
            if p["type"] == "abstention":
                probes_[p["id"]] = {"abstention": True, "answer": "UNKNOWN"}
            else:
                probes_[p["id"]] = {"needle": items[p["item"]]["oracle_line"], "answer": p["answer"]}
        first = spec["repo"]["files"]["CLAUDE.md"].split("\n")[0]
        plan["fixtures"][first] = {
            "preserve_append": ["## Notes"] + [i["oracle_line"] for i in needed[:3]] + [JUNK],
            "probes": probes_, "false_if": JUNK, "always_correct": [], "never_correct": []}
    return plan


class Env(unittest.TestCase):
    """A sandboxed HOME and TMPDIR, a scripted claude on PATH, and a stub plugin export."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        exe = self.bin / "claude"
        exe.write_text(FAKE_CLAUDE, encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
        self.home = self.tmp / "home"
        (self.home / ".claude").mkdir(parents=True)
        (self.home / ".claude" / "CLAUDE.md").write_text("# Title\n" + HOST_MARKER + "\n", encoding="utf-8")
        self.environ = {"PATH": str(self.bin) + os.pathsep + os.environ["PATH"], "HOME": str(self.home),
                        "TMPDIR": str(self.tmp), "CPR_EVAL_LIVE": "1"}
        self.cfg = claude_run.load_config()
        self.cfg["phase_caps"] = {"control": 3.0, "generation": 15.0, "evaluation": 30.0}
        self.cfg["score"] = {"max_judge_calls": 500}
        (self.bin / "pins.json").write_text(json.dumps(self.cfg), encoding="utf-8")
        self.plan = build_plan(SPECS)
        self.write_plan()
        self.export = self.tmp / "export"
        (self.export / "hooks").mkdir(parents=True)
        (self.export / "hooks" / "hooks.json").write_text(json.dumps({"hooks": {"SessionStart": [
            {"matcher": "startup", "hooks": [{"type": "command", "command": "x"}]}]}}), encoding="utf-8")
        original = claude_run.export_plugin
        claude_run.export_plugin = lambda ref, environ=None, overlay_skill=None: self.export
        self.addCleanup(setattr, claude_run, "export_plugin", original)
        self.out = self.tmp / "results"

    def write_plan(self):
        (self.bin / "plan.json").write_text(json.dumps(self.plan), encoding="utf-8")

    def calls(self):
        path = self.bin / "calls.jsonl"
        return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def batch(self, ids=IDS, arm_names=("floor", "baseline", "oracle"), seeds=2, **kw):
        out = io.StringIO()
        code = None
        try:
            code = probes.run_batch(FIXTURES, self.out, "dev", list(ids), list(arm_names), seeds, self.cfg, self.environ,
                                    date="20260101", stdout=out, **kw)
        finally:
            self.last_output = out.getvalue()
        return code


class FakeJudge:
    """Labels like a careful judge, from the specs. Modes: ok, blind (never matches), sloppy (matches anything).

    label_probe takes a batch case (one probe session) and answers every probe in one call. drop names probe ids
    left out of the reply, each as (batch case id, probe id); probe_proof_ok False fails the proof of probe calls only (items calls still pass).
    """

    def __init__(self, specs, mode="ok", proof_ok=True, probe_proof_ok=True, drop=()):
        self.mode, self.proof_ok, self.probe_proof_ok, self.drop = mode, proof_ok, probe_proof_ok, set(drop)
        self.by_line = {}
        for spec in specs.values():
            for item in spec["items"]:
                if item["needed"]:
                    self.by_line[item["oracle_line"].strip()] = item["id"]
        self.probe_calls, self.items_calls = [], []

    def rec(self, value, proof_ok=None):
        ok = self.proof_ok if proof_ok is None else proof_ok
        proof = {"ok": ok, "failures": [] if ok else ["markers_found"]}
        return value, {"proof": proof, "cost_usd": 0.01, "usage": {"input_tokens": 10, "output_tokens": 2}, "wall_s": 1.0}

    def label_probe(self, cfg, case, environ, model=None, **route):
        self.probe_calls.append((case, route))
        if not self.probe_proof_ok:
            return self.rec(None, proof_ok=False)
        labels = {}
        for p in case["probes"]:
            if (case["id"], p["id"]) in self.drop:
                continue
            if p["gold"] == score.ABSTENTION_GOLD:
                labels[p["id"]] = "incorrect"
            else:
                labels[p["id"]] = "correct" if p["gold"].strip().casefold() in p["answer"].casefold() else "incorrect"
        return self.rec(labels, proof_ok=self.proof_ok)

    def probes_sent(self):
        """Every probe case sent in a probe call, in call order."""
        return [p for case, _ in self.probe_calls for p in case["probes"]]

    def label_items(self, cfg, case, environ, model=None, **route):
        self.items_calls.append((case, route))
        needed_ids = [i["id"] for i in case["needed"]]
        not_needed = {i["id"] for i in case["not_needed"]}
        out = []
        for n, (heading, text) in enumerate(case["lines"], start=1):
            item = self.by_line.get(text.strip())
            if self.mode == "sloppy":
                out.append({"line": n, "kind": "needed", "item_id": needed_ids[(n - 1) % len(needed_ids)], "placed_ok": True, "quote": ""})
            elif self.mode == "ok" and item in needed_ids:
                out.append({"line": n, "kind": "needed", "item_id": item, "placed_ok": True, "quote": ""})
            elif self.mode == "ok" and item in not_needed:
                out.append({"line": n, "kind": "not_needed", "item_id": item, "placed_ok": False, "quote": ""})
            else:
                out.append({"line": n, "kind": "unplanted", "item_id": "", "placed_ok": False, "quote": ""})
        return self.rec(out)


# --------------------------------------------------------------------------
# Arms
# --------------------------------------------------------------------------

class ArmsTests(unittest.TestCase):
    def test_floor_and_oracle_text_and_kinds(self):
        spec = SPECS[IDS[0]]
        self.assertEqual(arms.static_text("floor", spec), spec["repo"]["files"]["CLAUDE.md"])
        self.assertEqual(arms.static_text("oracle", spec), fixtures.render_oracle(spec))
        needed = [i for i in spec["items"] if i["needed"]]
        for item in needed:
            self.assertIn(item["oracle_line"], arms.static_text("oracle", spec))
            self.assertNotIn(item["oracle_line"], arms.static_text("floor", spec))
        self.assertEqual([arms.arm_kind(a) for a in ("floor", "oracle", "baseline")], ["static", "static", "preserve"])
        with self.assertRaises(arms.ArmError):
            arms.arm_kind("no-such-arm")

    def test_a_skill_variant_is_a_preserve_arm_only_when_its_skill_file_exists(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "lean").mkdir()
            (Path(d) / "lean" / "SKILL.md").write_text("x", encoding="utf-8")
            self.assertEqual(arms.arm_kind("lean", d), "preserve")
            self.assertIsNone(arms.variant_skill("other", d))
            self.assertIsNone(arms.variant_skill("../lean", d))

    def test_the_oracle_added_lines_are_the_oracle_lines(self):
        spec = SPECS[IDS[0]]
        added = arms.added_lines(arms.static_text("floor", spec), arms.static_text("oracle", spec))
        needed = [i["oracle_line"] for i in spec["items"] if i["needed"]]
        self.assertEqual(sorted(t for _, t in added), sorted(needed))

    def test_added_lines_name_their_heading_and_skip_blanks_and_headings(self):
        floor = "# T\n\n## A\n- one\n\n## B\n- two\n"
        new = "# T\n\n## A\n- one\n- added under A\n\n## B\n- two\n\n## New\n- fresh\n\n"
        self.assertEqual(arms.added_lines(floor, new), [("A", "- added under A"), ("New", "- fresh")])

    def test_a_rewritten_line_counts_as_added_and_a_deleted_line_does_not(self):
        self.assertEqual(arms.added_lines("# T\n- old\n- keep\n", "# T\n- new\n- keep\n"), [("T", "- new")])
        self.assertEqual(arms.added_lines("# T\n- old\n- keep\n", "# T\n- keep\n"), [])

    def test_line_count_is_wc_l(self):
        self.assertEqual((arms.line_count("a\nb\n"), arms.line_count("a\nb"), arms.line_count("")), (2, 1, 0))

    def test_raw_records_go_under_the_eval_root_never_the_results_directory(self):
        with tempfile.TemporaryDirectory() as d:
            environ = {"TMPDIR": d}
            out = Path(d) / "results" / "run-1"
            run_id = arms.new_run_id()  # raw folders are keyed by the run id recorded in run.json, not by the folder's name
            arms.write_raw(out, "probe", "floor", "fx-1", 2, {"events": [1, 2]}, environ, run_id=run_id)
            expected = claude_run.eval_root(environ) / "raw" / run_id / "probe-floor-fx-1-s2.json"
            self.assertEqual(arms.raw_path(out, "probe", "floor", "fx-1", 2, environ, run_id=run_id), expected)
            self.assertEqual(json.loads(expected.read_text(encoding="utf-8")), {"events": [1, 2]})
            self.assertFalse(out.exists())
            self.assertNotIn("_raw", expected.parts)


# --------------------------------------------------------------------------
# Probe prompt and answers
# --------------------------------------------------------------------------

class ProbePromptTests(unittest.TestCase):
    def test_the_prompt_lists_every_probe_and_hints_at_nothing_else(self):
        self.assertEqual(sorted(SPECS), sorted(IDS))  # a loop over no specs would prove nothing
        for fid, spec in SPECS.items():
            prompt = probes.probe_prompt(spec, 1, 99)
            for p in spec["probes"]:
                self.assertIn(f"{p['id']}: {p['question']}", prompt)
            lowered = prompt.casefold()
            for item in spec["items"]:
                self.assertNotIn(item["statement"].casefold(), lowered)
                self.assertNotIn(item.get("oracle_line", "\0").casefold(), lowered)
                self.assertNotIn(item["id"] + ":", prompt)
            for p in spec["probes"]:
                for text in [p["answer"]] + p["reject"]:
                    if len(text) >= 5 and text != "UNKNOWN":
                        self.assertNotIn(text.casefold(), lowered, (fid, p["id"]))
            for name in fixtures.CATEGORIES + ("needed", "oracle", "decision", "important"):
                self.assertNotIn(name.casefold(), lowered, name)
            self.assertIn("UNKNOWN", prompt)  # the instruction to abstain is the one allowed sentence

    def test_order_is_shared_by_arms_deterministic_and_varies_by_seed(self):
        spec = SPECS[IDS[0]]
        a = [p["id"] for p in probes.probe_order(spec, 1, 5)]
        self.assertEqual(a, [p["id"] for p in probes.probe_order(spec, 1, 5)])
        self.assertEqual(sorted(a), sorted(p["id"] for p in spec["probes"]))
        self.assertTrue(any([p["id"] for p in probes.probe_order(spec, s, 5)] != a for s in range(2, 8)))

    def test_parse_answers_plain_fenced_and_broken(self):
        good = json.dumps({"answers": [{"id": "p1", "answer": " 8082 "}, {"id": "p2", "answer": "UNKNOWN"}]})
        self.assertEqual(probes.parse_answers(good, ["p1", "p2"]), ({"p1": "8082", "p2": "UNKNOWN"}, []))
        self.assertEqual(probes.parse_answers("```json\n" + good + "\n```", ["p1", "p2"])[0]["p1"], "8082")
        for bad in ("I cannot comply.", None, "[]", '{"answers": "x"}', '{"answers": [{"id": "p1"}]}',
                    '{"answers": [], "extra": 1}'):
            answers, problems = probes.parse_answers(bad, ["p1"])
            self.assertEqual(answers, {}, bad)
            self.assertTrue(problems, bad)

    def test_unknown_and_empty_entries_are_reported_and_ids_can_be_missing(self):
        text = json.dumps({"answers": [{"id": "p1", "answer": "a"}, {"id": "zz", "answer": "c"}, {"id": "p2", "answer": "  "}]})
        answers, problems = probes.parse_answers(text, ["p1", "p2", "p3"])
        self.assertEqual(answers, {"p1": "a"})
        self.assertEqual(problems, ["extra_or_duplicate_entries:2", "missing:p2", "missing:p3"])

    def test_a_duplicated_probe_id_excludes_both_answers_even_when_they_agree(self):
        text = json.dumps({"answers": [{"id": "p1", "answer": "a"}, {"id": "p1", "answer": "a"}, {"id": "p2", "answer": "b"},
                                       {"id": "p3", "answer": "c"}, {"id": "p3", "answer": "  "}]})
        answers, problems = probes.parse_answers(text, ["p1", "p2", "p3"])
        self.assertEqual(answers, {"p2": "b"})
        self.assertEqual(problems, ["duplicate:p1", "duplicate:p3"])

    def test_input_tokens_sum_the_three_input_kinds(self):
        self.assertEqual(probes.input_tokens({"input_tokens": 3, "cache_creation_input_tokens": 10, "cache_read_input_tokens": 100,
                                              "output_tokens": 50}), 113)
        self.assertEqual(probes.input_tokens({"input_tokens": 3}), 3)
        self.assertIsNone(probes.input_tokens({"output_tokens": 3}))
        self.assertIsNone(probes.input_tokens(None))


# --------------------------------------------------------------------------
# Metrics without a batch
# --------------------------------------------------------------------------

class MetricTests(unittest.TestCase):
    def test_scorable_needs_floor_zero_and_oracle_majority(self):
        spec = {"probes": [{"id": "a", "type": "single-fact"}, {"id": "b", "type": "single-fact"},
                           {"id": "c", "type": "single-fact"}, {"id": "d", "type": "single-fact"},
                           {"id": "z", "type": "abstention"}]}
        seeds = [1, 2, 3, 4, 5]
        labels = {}
        for s in seeds:
            labels[("floor", s, "a")] = "incorrect"                       # floor never right: scorable
            labels[("oracle", s, "a")] = "correct" if s <= 3 else "incorrect"  # oracle right in 3 of 5: kept
            labels[("floor", s, "b")] = "correct" if s == 5 else "incorrect"   # floor right once: dropped
            labels[("oracle", s, "b")] = "correct"
            labels[("floor", s, "c")] = "abstained"
            labels[("oracle", s, "c")] = "correct" if s <= 2 else "incorrect"  # oracle fails in 3: dropped
            labels[("floor", s, "d")] = "incorrect"
            labels[("oracle", s, "d")] = None                                  # unlabelled: dropped as label_missing
            labels[("floor", s, "z")] = "abstained"
            labels[("oracle", s, "z")] = "abstained"
        got = score.scorable_probes(spec, labels, seeds, seeds)
        self.assertEqual(got["scorable"], ["a"])
        self.assertEqual(got["dropped"], {"b": ["floor_correct"], "c": ["oracle_minority"], "d": ["label_missing"]})
        self.assertEqual(got["non_abstention"], ["a", "b", "c", "d"])  # abstention probes are never in the primary

    def test_a_missing_floor_label_drops_the_probe_as_label_missing_even_when_the_oracle_is_fine(self):
        spec = {"probes": [{"id": "a", "type": "single-fact"}, {"id": "b", "type": "single-fact"}]}
        labels = {("floor", 1, "a"): None, ("oracle", 1, "a"): "correct", ("floor", 2, "a"): "incorrect",
                  ("oracle", 2, "a"): "correct", ("floor", 1, "b"): "incorrect", ("oracle", 1, "b"): "correct",
                  ("floor", 2, "b"): "incorrect", ("oracle", 2, "b"): "correct"}
        got = score.scorable_probes(spec, labels, [1, 2], [1, 2])
        self.assertEqual((got["scorable"], got["dropped"]), (["b"], {"a": ["label_missing"]}))

    def test_an_excluded_abstention_probe_is_listed_as_excluded_from_the_one_exclusion_rule(self):
        spec = {"probes": [{"id": "a", "type": "single-fact"}, {"id": "z", "type": "abstention", "excluded": "x"},
                           {"id": "y", "type": "abstention"}, {"id": "m", "type": "single-fact", "excluded": ""}]}
        got = score.scorable_probes(spec, {("floor", 1, "a"): "incorrect", ("oracle", 1, "a"): "correct"}, [1], [1])
        self.assertEqual((got["scorable"], got["non_abstention"], got["excluded"]), (["a"], ["a"], ["z", "m"]))

    def test_an_excluded_probe_is_never_scorable_dropped_or_in_the_primary(self):
        spec = {"probes": [{"id": "a", "type": "single-fact"}, {"id": "b", "type": "single-fact", "excluded": "overlap"},
                           {"id": "c", "type": "single-fact", "excluded": ""}]}
        labels = {}
        for pid in "abc":  # all three would otherwise qualify; b and c would also be dropped without the marker
            labels[("floor", 1, pid)] = "incorrect"
            labels[("oracle", 1, pid)] = "correct"
        labels[("floor", 1, "c")] = "correct"
        got = score.scorable_probes(spec, labels, [1], [1])
        self.assertEqual(got["scorable"], ["a"])
        self.assertEqual(got["non_abstention"], ["a"])
        self.assertEqual(got["dropped"], {})
        self.assertEqual(got["excluded"], ["b", "c"])

    def test_a_probe_failing_both_tests_lists_both_reasons(self):
        spec = {"probes": [{"id": "a", "type": "temporal"}]}
        labels = {("floor", 1, "a"): "correct", ("oracle", 1, "a"): "incorrect"}
        self.assertEqual(score.scorable_probes(spec, labels, [1], [1])["dropped"], {"a": ["floor_correct", "oracle_minority"]})

    def test_majority_is_more_than_half_of_the_seeds_present(self):
        spec = {"probes": [{"id": "a", "type": "single-fact"}]}
        for n, correct, kept in ((3, 2, True), (3, 1, False), (4, 2, False), (4, 3, True), (1, 1, True)):
            labels = {("oracle", s, "a"): "correct" if s <= correct else "incorrect" for s in range(1, n + 1)}
            labels.update({("floor", s, "a"): "incorrect" for s in range(1, n + 1)})
            got = score.scorable_probes(spec, labels, list(range(1, n + 1)), list(range(1, n + 1)))
            self.assertEqual(bool(got["scorable"]), kept, (n, correct))

    def test_item_cell_recall_precision_and_placement(self):
        spec = {"items": [{"id": "i1", "needed": True}, {"id": "i2", "needed": True}, {"id": "i3", "needed": True},
                          {"id": "i4", "needed": True}, {"id": "n1", "needed": False}]}
        added = [("A", "l1"), ("A", "l2"), ("B", "l3"), ("B", "l4"), ("B", "l5")]
        lines = [{"line": 1, "kind": "needed", "item_id": "i1", "placed_ok": True, "quote": ""},
                 {"line": 2, "kind": "needed", "item_id": "i1", "placed_ok": False, "quote": ""},   # same item twice: one match
                 {"line": 3, "kind": "needed", "item_id": "i2", "placed_ok": False, "quote": ""},
                 {"line": 4, "kind": "not_needed", "item_id": "n1", "placed_ok": False, "quote": ""},
                 {"line": 5, "kind": "needed", "item_id": "nope", "placed_ok": True, "quote": ""}]  # unknown id: unplanted
        got = score.item_cell(spec, added, lines)
        self.assertEqual((got["recall"], got["precision"], got["placement"]), (0.5, 0.6, 0.5))
        self.assertEqual(got["matched"], ["i1", "i2"])
        self.assertEqual(score.item_cell(spec, [], []), {"recall": 0.0, "precision": None, "placement": None, "matched": []})
        self.assertEqual(score.item_cell(spec, added, None)["recall"], None)

    def test_item_cell_is_none_unless_the_line_rows_are_exactly_1_to_n_each_once(self):
        spec = {"items": [{"id": "i1", "needed": True}, {"id": "i2", "needed": True}]}
        added = [("A", "l1"), ("A", "l2"), ("B", "l3")]

        def row(n, item="i1"):
            return {"line": n, "kind": "needed", "item_id": item, "placed_ok": True, "quote": ""}
        good = [row(1), row(2, "i2"), row(3)]
        self.assertEqual(score.item_cell(spec, added, good)["recall"], 1.0)
        self.assertEqual(score.item_cell(spec, added, list(reversed(good)))["recall"], 1.0)  # order does not matter
        for name, rows in (("a line the judge did not label", good[:2]), ("a repeated line", good + [row(2, "i2")]),
                           ("a line past the end", good + [row(4)]), ("a skipped number", [row(1), row(3), row(4)]),
                           ("line numbers from 0", [row(0), row(1), row(2)]), ("a row that is not an object", good[:2] + ["x"]),
                           ("a line that is not an integer", good[:2] + [row("3")])):
            cell = score.item_cell(spec, added, rows)
            self.assertEqual((cell["recall"], cell["precision"], cell["placement"], cell["matched"]), (None, None, None, []), name)
        self.assertEqual(score.item_cell(spec, [], [])["recall"], 0.0)  # no added line, no row
        self.assertIsNone(score.item_cell(spec, [], [row(1)])["recall"])

    def test_only_the_exact_word_unknown_skips_the_judge(self):
        self.assertTrue(all(score._is_unknown(x) for x in ("UNKNOWN", " unknown. ", "Unknown")))
        self.assertFalse(any(score._is_unknown(x) for x in ("I don't know", "unknown port", "", None)))

    def test_abstention_cases_use_a_neutral_gold(self):
        spec = SPECS[IDS[0]]
        ab = [p for p in spec["probes"] if p["type"] == "abstention"][0]
        self.assertEqual(score.probe_case(ab, "x")["gold"], score.ABSTENTION_GOLD)
        other = [p for p in spec["probes"] if p["type"] != "abstention"][0]
        self.assertEqual(score.probe_case(other, "x")["gold"], other["answer"])


# --------------------------------------------------------------------------
# The batch, through the real run_claude and the scripted claude
# --------------------------------------------------------------------------

class BatchTests(Env):
    def test_units_files_and_the_pilot_ledger(self):
        self.assertEqual(self.batch(), 0, self.last_output)
        run = arms.read_json(self.out / "run.json")
        self.assertEqual((run["fixtures"], run["arms"], run["seeds"], run["split"]), (sorted(IDS), ["floor", "baseline", "oracle"], 2, "dev"))
        self.assertEqual(run["rng_seed"], self.cfg["rng_seed"])
        self.assertNotIn(str(self.tmp), (self.out / "run.json").read_text(encoding="utf-8"))
        for fid in IDS:
            for seed in (1, 2):
                for arm in ("floor", "baseline", "oracle"):
                    self.assertTrue(arms.arm_text_path(self.out, arm, fid, seed).is_file())
                    self.assertTrue(arms.probe_path(self.out, arm, fid, seed).is_file())
                self.assertTrue(arms.preserve_path(self.out, "baseline", fid, seed).is_file())
                self.assertTrue(arms.raw_path(self.out, "probe", "floor", fid, seed, self.environ).is_file())
        # 4 preserve runs at 0.2 charged in full (no earlier record exists for an installed session) + 12 probe sessions at 0.01
        self.assertAlmostEqual(claude_run.spend_total(self.environ, "evaluation"), 4 * 0.2 + 12 * 0.01)
        self.assertEqual(claude_run.spend_total(self.environ, "generation"), 0.0)
        self.assertEqual(claude_run.spend_total(self.environ, "control"), 0.0)
        pres = arms.read_json(arms.preserve_path(self.out, "baseline", IDS[0], 1))
        self.assertEqual((pres["cost_usd"], pres["changed"]), (0.2, True))

    def test_the_command_lines_keep_the_isolation_flags_and_the_resume_shape(self):
        self.assertEqual(self.batch(ids=IDS[:1], seeds=1), 0)
        calls = self.calls()
        preserve = [c for c in calls if c["argv"][1] == "/cpr:preserve quick"]
        probe = [c for c in calls if "--tools" in c["argv"]]
        self.assertEqual((len(preserve), len(probe), len(calls)), (1, 3, 4))
        argv = preserve[0]["argv"]
        self.assertIn("--resume", argv)
        self.assertNotIn("--session-id", argv)
        self.assertNotIn("--fork-session", argv)
        self.assertEqual(argv[argv.index("--plugin-dir") + 1], str(self.export))
        self.assertNotIn("--tools", argv)
        for call in calls:
            self.assertIn("--strict-mcp-config", call["argv"])
            self.assertEqual(call["argv"][call["argv"].index("--setting-sources") + 1], "project")
            self.assertEqual(call["argv"][call["argv"].index("--model") + 1], self.cfg["model"])
        for call in probe:
            self.assertEqual(call["argv"][call["argv"].index("--tools") + 1], "")
            self.assertNotIn("--plugin-dir", call["argv"])
            self.assertNotIn("--resume", call["argv"])
            self.assertIn("--session-id", call["argv"])
            self.assertEqual(sorted(os.listdir(call["cwd"])), [".git", "CLAUDE.md"])

    def test_preserve_runs_pass_the_install_style_prior_so_the_run_is_charged_in_full(self):
        runner_calls = []
        real = claude_run.run_claude

        def runner(*args, **kwargs):
            runner_calls.append(kwargs)
            return real(*args, **kwargs)
        self.batch(ids=IDS[:1], arm_names=("baseline",), seeds=1, runner=runner)
        preserve = [k for k in runner_calls if k["kind"] == "preserve"][0]
        self.assertEqual(preserve["phase"], "evaluation")
        self.assertEqual(sorted(preserve["prior"]), ["memory_dir_after", "sandbox", "session_id"])
        self.assertEqual(preserve["prior"]["session_id"], preserve["resume"])
        self.assertNotIn("cost_usd_reported", preserve["prior"])
        self.assertNotIn("cost_usd", preserve["prior"])
        probe = [k for k in runner_calls if k["kind"] == "probe"][0]
        self.assertEqual((probe["phase"], probe["probe"], probe.get("resume"), probe.get("plugin")), ("evaluation", True, None, None))

    def test_baseline_adds_lines_and_floor_and_oracle_files_match_the_spec(self):
        self.batch(ids=IDS[:1], seeds=1)
        spec = SPECS[IDS[0]]
        read = lambda arm: arms.arm_text_path(self.out, arm, IDS[0], 1).read_text(encoding="utf-8")  # noqa: E731
        self.assertEqual(read("floor"), spec["repo"]["files"]["CLAUDE.md"])
        self.assertEqual(read("oracle"), fixtures.render_oracle(spec))
        self.assertIn(JUNK, read("baseline"))
        self.assertTrue(read("baseline").startswith(read("floor")))

    def test_a_second_run_does_nothing_and_a_wider_one_adds_only_the_new_units(self):
        self.batch(ids=IDS[:1], seeds=1)
        n = len(self.calls())
        self.assertEqual(self.batch(ids=IDS[:1], seeds=1), 0)
        self.assertEqual(len(self.calls()), n)
        self.assertIn("0 units run", self.last_output)
        self.batch(ids=IDS[:1], seeds=2)
        self.assertEqual(len(self.calls()), n + 4)
        self.assertEqual(arms.read_json(self.out / "run.json")["seeds"], 2)

    def test_an_isolation_failure_is_re_run_once_then_the_cell_is_excluded_with_its_labels(self):
        self.plan["leak"] = HOST_MARKER
        self.write_plan()
        # README "Exclusions": an isolation failure is re-run once, and a second one excludes the cell. It no longer
        # stops the batch.
        self.assertEqual(self.batch(ids=IDS[:1], seeds=1), 0)
        meta = arms.read_json(self.out / "run.json")
        self.assertEqual(len(meta["excluded_cells"]), 3)  # the baseline preserve, the floor probe and the oracle probe
        self.assertTrue(all("markers_found" in e["checks"] for e in meta["excluded_cells"]))
        self.assertNotIn(HOST_MARKER, (self.out / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(len(self.calls()), 6)  # each of those twice; the baseline has no CLAUDE.md, so no probe session
        self.assertFalse(arms.preserve_path(self.out, "baseline", IDS[0], 1).exists())

    def test_the_budget_stops_before_the_next_unit(self):
        with self.assertRaises(probes.BatchStop) as ctx:
            self.batch(budget_usd=0.2)
        self.assertEqual(str(ctx.exception), "budget reached")
        self.assertEqual(len(self.calls()), 1)  # the preserve run took the ledger to the cap; no further unit starts
        self.assertAlmostEqual(claude_run.spend_total(self.environ, "evaluation"), 0.2)

    def test_the_pilot_cap_refuses_a_launch(self):
        self.cfg["phase_caps"]["evaluation"] = 0.015
        with self.assertRaises(claude_run.SpendCapReached):
            self.batch()

    def test_a_changed_pin_refuses_to_extend_a_run(self):
        self.batch(ids=IDS[:1], seeds=1)
        self.cfg["model"] = "another-model"
        with self.assertRaises(probes.BatchStop) as ctx:
            self.batch(ids=IDS[:1], seeds=1)
        self.assertIn("model changed", str(ctx.exception))

    def test_a_missing_generated_fixture_or_unknown_arm_is_refused_before_any_call(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / IDS[0]).mkdir()
            (Path(d) / IDS[0] / "spec.json").write_text(json.dumps(SPECS[IDS[0]]), encoding="utf-8")
            with self.assertRaises(probes.BatchStop):
                probes.run_batch(Path(d), self.out, "dev", IDS[:1], ["baseline"], 1, self.cfg, self.environ)
        with self.assertRaises(arms.ArmError):
            self.batch(arm_names=("floor", "mystery"))
        self.assertEqual(self.calls(), [])

    def test_an_unparsable_reply_is_recorded_not_fatal(self):
        self.plan["garbage_reply"] = True
        self.write_plan()
        self.assertEqual(self.batch(ids=IDS[:1], arm_names=("floor",), seeds=1), 0)
        data = arms.read_json(arms.probe_path(self.out, "floor", IDS[0], 1))
        self.assertEqual((data["ok"], data["answers"]), (True, {}))
        self.assertTrue(data["problems"])

    def test_selectors(self):
        self.assertEqual(probes.select_fixtures(FIXTURES, "dev-001,dev-002-recipe-scaler"), IDS)
        everything = probes.select_fixtures(FIXTURES, "all")
        self.assertEqual(len(everything), 5)
        self.assertNotIn("dev-006-tram-timetable", everything)
        with self.assertRaises(probes.BatchStop):
            probes.select_fixtures(FIXTURES, "dev-00")
        with self.assertRaises(probes.BatchStop):
            probes.select_fixtures(FIXTURES, "dev-099")

    def excluded_root(self):
        """A fixtures root holding copies of dev-001 and dev-002, with dev-002 marked excluded."""
        root = self.tmp / "excl-fixtures"
        for fid in IDS:
            shutil.copytree(FIXTURES / fid, root / fid)
        path = root / IDS[1] / "spec.json"
        spec = fixtures.load_spec(path)
        spec["excluded"] = {"date": "2026-10-18", "reason": "Not run."}
        path.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return root

    def stub_live(self):
        original = claude_run.preflight
        claude_run.preflight = lambda cfg, environ=None: ""
        self.addCleanup(setattr, claude_run, "preflight", original)
        loader = claude_run.load_config
        claude_run.load_config = lambda path=None: self.cfg
        self.addCleanup(setattr, claude_run, "load_config", loader)

    def run_command(self, root, wanted, results):
        out = io.StringIO()
        code = probes.command_run(root, self.tmp / results, "dev", wanted, ["floor", "baseline"], 1, None, "20260101",
                                  self.environ, out)
        return code, out.getvalue()

    def test_an_excluded_fixture_is_never_selected_or_run(self):
        self.stub_live()
        root = self.excluded_root()
        self.assertEqual(probes.select_fixtures(root, "all"), IDS[:1])
        code, text = self.run_command(root, "dev-001", "only")
        self.assertEqual(code, 0, text)
        only = self.calls()
        self.assertTrue(only)
        (self.bin / "calls.jsonl").unlink()
        code, text = self.run_command(root, "all", "everything")
        self.assertEqual(code, 0, text)
        every = self.calls()
        self.assertEqual(len(every), len(only))
        second_floor = spec_of(IDS[1])["repo"]["files"]["CLAUDE.md"].split("\n")[0]
        for call in every:
            self.assertNotIn(second_floor, json.dumps(call))
            self.assertNotIn(IDS[1], json.dumps(call))
        self.assertEqual(arms.read_json(self.tmp / "everything" / "run.json")["fixtures"], IDS[:1])

    def test_naming_an_excluded_fixture_refuses_with_zero_calls(self):
        self.stub_live()
        root = self.excluded_root()
        for wanted in ("dev-002", "dev-002-recipe-scaler", "dev-001,dev-002"):
            with self.subTest(wanted):
                code, text = self.run_command(root, wanted, "named")
                self.assertEqual(code, 1)
                self.assertIn("run: STOP fixture excluded: dev-002-recipe-scaler", text)
        self.assertEqual(self.calls(), [])
        self.assertFalse((self.tmp / "named").exists())

    def test_run_batch_refuses_an_excluded_fixture_before_writing_or_launching(self):
        root = self.excluded_root()
        with self.assertRaises(probes.BatchStop) as caught:
            probes.run_batch(root, self.out, "dev", list(IDS), ["floor"], 1, self.cfg, self.environ,
                             date="20260101", stdout=io.StringIO())
        self.assertEqual(str(caught.exception), f"fixture excluded: {IDS[1]}")
        self.assertEqual(self.calls(), [])
        self.assertFalse((self.out / "run.json").exists())

    def test_an_unreadable_spec_refuses_selection(self):
        root = self.excluded_root()
        (root / IDS[0] / "spec.json").write_text("{not json", encoding="utf-8")
        for wanted in ("all", "dev-001"):
            with self.assertRaises(probes.BatchStop) as caught:
                probes.select_fixtures(root, wanted)
            self.assertEqual(str(caught.exception), f"spec unreadable: {IDS[0]}")

    def test_the_command_gates_live_use_and_prints_labels(self):
        original = claude_run.preflight
        claude_run.preflight = lambda cfg, environ=None: ""
        self.addCleanup(setattr, claude_run, "preflight", original)
        loader = claude_run.load_config
        claude_run.load_config = lambda path=None: self.cfg
        self.addCleanup(setattr, claude_run, "load_config", loader)
        out = io.StringIO()
        env = dict(self.environ)
        env.pop("CPR_EVAL_LIVE")
        self.assertEqual(probes.command_run(FIXTURES, self.out, "dev", "dev-001", ["floor"], 1, None, "20260101", env, out), 2)
        self.assertEqual(self.calls(), [])
        out = io.StringIO()
        self.assertEqual(probes.command_run(FIXTURES, self.out, "dev", "dev-001", ["floor", "nope"], 1, None, "20260101",
                                            self.environ, out), 1)
        self.assertIn("run: STOP", out.getvalue())
        out = io.StringIO()
        self.assertEqual(probes.command_run(FIXTURES, self.out, "dev", "dev-001", ["floor"], 1, None, "20260101",
                                            self.environ, out), 0)
        self.assertRegex(out.getvalue(), r"^run: 1 units run, 0 already present, pilot ledger \+\$0\.01")


# --------------------------------------------------------------------------
# Scoring a batch
# --------------------------------------------------------------------------

class ScoreTests(Env):
    def setUp(self):
        super().setUp()
        self.judge = FakeJudge(SPECS)

    def run_batch_and_score(self, seeds=2, mode="ok", **kw):
        self.assertEqual(self.batch(seeds=seeds), 0)
        self.judge.mode = mode
        return score.score_run(self.out, SPECS, self.cfg, self.environ, self.judge.label_probe, self.judge.label_items, **kw)

    def expected(self, fid):
        spec = SPECS[fid]
        non_abs = [p for p in spec["probes"] if p["type"] != "abstention" and "excluded" not in p]
        needed = [i["id"] for i in spec["items"] if i["needed"]]
        first3 = set(needed[:3])
        hit = [p for p in non_abs if p["item"] in first3]
        return spec, non_abs, needed, hit

    def judged(self, fid):
        """Every non-abstention probe, excluded ones included: the judge sees them all."""
        return [p for p in SPECS[fid]["probes"] if p["type"] != "abstention"]

    def test_metrics_for_a_full_batch(self):
        scores = self.run_batch_and_score()
        self.assertEqual(scores["fixtures"], sorted(IDS))
        self.assertTrue(scores["sanity"]["ok"])
        self.assertEqual((scores["sanity"]["oracle_recall"], scores["sanity"]["mismatch_recall"]), (1.0, 0.0))
        for fid in IDS:
            spec, non_abs, needed, hit = self.expected(fid)
            self.assertEqual(scores["scorable"][fid], [p["id"] for p in non_abs])  # floor never right, oracle always
            self.assertEqual(scores["dropped"][fid], {})
            pf = scores["per_fixture"]
            self.assertEqual(pf["floor"][fid]["primary"], 0.0)
            self.assertEqual(pf["oracle"][fid]["primary"], 1.0)
            self.assertEqual(pf["baseline"][fid]["primary"], round(len(hit) / len(non_abs), 10))
            self.assertEqual(pf["baseline"][fid]["primary_all"], pf["baseline"][fid]["primary"])
            # recall: 3 of the needed items; precision: 3 of 4 added lines; placement: judged ok
            self.assertEqual(pf["baseline"][fid]["recall"], round(3 / len(needed), 10))
            self.assertEqual(pf["baseline"][fid]["precision"], 0.75)
            self.assertEqual(pf["baseline"][fid]["placement"], 1.0)
            self.assertEqual((pf["oracle"][fid]["recall"], pf["oracle"][fid]["precision"]), (1.0, 1.0))
            self.assertEqual((pf["floor"][fid]["recall"], pf["floor"][fid]["precision"]), (0.0, None))
            # abstention: the junk line makes the baseline answer a question the session never settled
            self.assertEqual(pf["floor"][fid]["false_answer"], 0.0)
            self.assertEqual(pf["oracle"][fid]["false_answer"], 0.0)
            self.assertEqual(pf["baseline"][fid]["false_answer"], 1.0)
            # growth: lines by wc -l logic, tokens as the probe-session input difference
            floor_text = arms.static_text("floor", spec)
            for arm in ("baseline", "oracle"):
                text = arms.arm_text_path(self.out, arm, fid, 1).read_text(encoding="utf-8")
                self.assertEqual(pf[arm][fid]["d_lines"], text.count("\n") - floor_text.count("\n"))
                self.assertEqual(pf[arm][fid]["d_tokens"], len(text) - len(floor_text))
            self.assertEqual((pf["floor"][fid]["d_lines"], pf["floor"][fid]["d_tokens"]), (0.0, 0.0))

    def test_floor_fail_filter_drops_a_probe_the_floor_can_answer(self):
        fid = IDS[0]
        _, non_abs, _, _ = self.expected(fid)
        first = list(self.plan["fixtures"])[0]
        victim = non_abs[0]["id"]
        self.plan["fixtures"][first]["always_correct"] = [victim]  # every arm, floor included, answers it
        self.write_plan()
        scores = self.run_batch_and_score()
        self.assertEqual(scores["dropped"][fid], {victim: ["floor_correct"]})
        self.assertNotIn(victim, scores["scorable"][fid])
        self.assertEqual(scores["dropped_floor"], 1)
        # the primary leaves it out; the all-probes figure keeps it, so the floor is not 0 there
        self.assertEqual(scores["per_fixture"]["floor"][fid]["primary"], 0.0)
        self.assertGreater(scores["per_fixture"]["floor"][fid]["primary_all"], 0.0)
        self.assertEqual(scores["scorable_counts"][fid], len(non_abs) - 1)

    def test_oracle_majority_drops_a_probe_the_oracle_cannot_answer(self):
        fid = IDS[1]
        _, non_abs, _, _ = self.expected(fid)
        first = list(self.plan["fixtures"])[1]
        victim = non_abs[-1]["id"]
        self.plan["fixtures"][first]["never_correct"] = [victim]
        self.write_plan()
        scores = self.run_batch_and_score()
        self.assertEqual(scores["dropped"][fid], {victim: ["oracle_minority"]})
        self.assertEqual(scores["dropped_oracle"], 1)
        self.assertEqual(scores["per_fixture"]["oracle"][fid]["primary"], 1.0)
        self.assertLess(scores["per_fixture"]["oracle"][fid]["primary_all"], 1.0)  # the diagnostic shows the dropped probe

    def test_sanity_aborts_when_the_judge_cannot_find_oracle_lines(self):
        self.assertEqual(self.batch(), 0)
        self.judge.mode = "blind"
        with self.assertRaises(score.ScoreAbort) as ctx:
            score.score_run(self.out, SPECS, self.cfg, self.environ, self.judge.label_probe, self.judge.label_items)
        self.assertIn("oracle recall 0.000", str(ctx.exception))
        self.assertFalse((self.out / "scores.json").exists())

    def test_sanity_aborts_when_the_judge_matches_lines_to_the_wrong_fixtures_items(self):
        self.assertEqual(self.batch(), 0)
        self.judge.mode = "sloppy"
        with self.assertRaises(score.ScoreAbort) as ctx:
            score.score_run(self.out, SPECS, self.cfg, self.environ, self.judge.label_probe, self.judge.label_items)
        self.assertIn("mismatch recall", str(ctx.exception))
        self.assertNotIn("oracle recall 0", str(ctx.exception))
        self.assertEqual(self.judge.probe_calls, [])  # the controls run before any probe is labelled

    def test_the_mismatch_control_judges_each_oracle_diff_against_the_next_fixtures_items_cyclically(self):
        self.assertEqual(self.batch(), 0)
        score.score_run(self.out, SPECS, self.cfg, self.environ, self.judge.label_probe, self.judge.label_items)
        a, b = SPECS[IDS[0]], SPECS[IDS[1]]

        def lines_of(spec):
            return sorted(i["oracle_line"] for i in spec["items"] if i["needed"])

        def facts_of(spec):
            return [i["statement"] for i in spec["items"] if i["needed"]]
        cases = [c for c, _ in self.judge.items_calls[:4]]
        # oracle recall: own items; mismatch: the next fixture's items (the last wraps to the first)
        for n, (lines_from, items_from) in enumerate([(a, a), (a, b), (b, b), (b, a)]):
            self.assertEqual(sorted(t for _, t in cases[n]["lines"]), lines_of(lines_from), n)
            self.assertEqual([i["fact"] for i in cases[n]["needed"]], facts_of(items_from), n)

    def test_sanity_needs_two_fixtures(self):
        self.assertEqual(self.batch(ids=IDS[:1]), 0)
        with self.assertRaises(score.ScoreAbort) as ctx:
            score.score_run(self.out, {IDS[0]: SPECS[IDS[0]]}, self.cfg, self.environ, self.judge.label_probe, self.judge.label_items)
        self.assertIn("at least 2 fixtures", str(ctx.exception))

    def test_thresholds_are_the_decided_ones(self):
        self.assertEqual((score.ORACLE_RECALL_MIN, score.MISMATCH_RECALL_MAX), (0.95, 0.05))

    def test_every_judge_call_spends_from_the_pilot_ledger_with_the_score_counter(self):
        self.run_batch_and_score()
        routes = [r for _, r in self.judge.probe_calls + self.judge.items_calls]
        self.assertTrue(routes)
        for route in routes:
            self.assertEqual((route["phase"], route["counter"], route["max_calls"]), ("evaluation", "score", 500))

    def test_labels_are_cached_so_a_second_pass_makes_no_judge_call(self):
        first = self.run_batch_and_score()
        made = len(self.judge.probe_calls) + len(self.judge.items_calls)
        self.assertGreater(made, 0)
        self.assertEqual(first["judge"]["calls_made"], made)
        second = score.score_run(self.out, SPECS, self.cfg, self.environ, self.judge.label_probe, self.judge.label_items)
        self.assertEqual(len(self.judge.probe_calls) + len(self.judge.items_calls), made)
        self.assertEqual(second["judge"]["calls_made"], 0)
        self.assertEqual(second["per_fixture"], first["per_fixture"])

    def test_the_judge_prompt_never_names_the_arm(self):
        self.run_batch_and_score()
        run = json.loads((self.out / "run.json").read_text(encoding="utf-8"))
        arm_names = sorted(set(run["arms"]) | {"floor", "baseline", "oracle"})
        self.assertTrue(self.judge.probe_calls)
        for case, _ in self.judge.probe_calls:
            prompt = judge.probe_prompt(case).lower()
            for arm in arm_names:
                self.assertNotIn(arm.lower(), prompt, f"arm {arm!r} leaks into the judge prompt for case {case['id']!r}")
            self.assertRegex(case["id"], r"^[0-9a-f]{10}$")

    def test_identical_answers_share_one_label_and_unknown_never_reaches_the_judge(self):
        self.run_batch_and_score(seeds=2)
        cases = self.judge.probes_sent()
        keys = [json.dumps({k: v for k, v in c.items() if k != "id"}, sort_keys=True) for c in cases]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue(all(c["answer"].strip().upper() != "UNKNOWN" for c in cases))

    # -- one judge call per probe session --------------------------------------

    def oracle_calls(self, seeds=2):
        """Score floor and oracle sessions only: the floor answers UNKNOWN throughout, the oracle answers every
        non-abstention probe and says UNKNOWN to the abstention ones."""
        self.assertEqual(self.batch(arm_names=("floor", "oracle"), seeds=seeds), 0)
        return score.score_run(self.out, SPECS, self.cfg, self.environ, self.judge.label_probe, self.judge.label_items)

    def test_a_session_gets_exactly_one_probe_call_holding_exactly_its_judge_needing_answers(self):
        scores = self.oracle_calls(seeds=2)
        calls = [c for c, _ in self.judge.probe_calls]
        # floor: all UNKNOWN; seed 2: all hits. The batch id is opaque: fixture, seed and probe ids, never the arm.
        # An excluded probe is still asked and judged (its label is evidence); it only leaves the scoring.
        want = {}
        for fid in IDS:
            want[score.blind_case_id(fid, 1, self.judged(fid))] = fid
        self.assertEqual(sorted(c["id"] for c in calls), sorted(want))
        for case in calls:
            fid = want[case["id"]]
            self.assertGreaterEqual(len(case["probes"]), 2)
            self.assertEqual([p["id"] for p in case["probes"]], [p["id"] for p in self.judged(fid)])
            self.assertTrue(all(p["answer"] != "UNKNOWN" for p in case["probes"]))
        self.assertTrue(all(len(c["probes"]) > 0 for c in calls))
        self.assertEqual(scores["judge"]["calls_made"], len(calls) + len(self.judge.items_calls))
        self.assertEqual(scores["invalid"], {"probe_answers_missing": 0, "judge_labels_invalid": 0})

    def test_a_second_pass_over_the_same_results_makes_no_probe_call(self):
        self.oracle_calls(seeds=1)
        made = len(self.judge.probe_calls)
        second = score.score_run(self.out, SPECS, self.cfg, self.environ, self.judge.label_probe, self.judge.label_items)
        self.assertEqual((len(self.judge.probe_calls), second["judge"]["calls_made"]), (made, 0))

    def test_one_dropped_label_excludes_only_that_probe_is_counted_and_is_the_only_one_asked_again(self):
        self.assertEqual(self.batch(arm_names=("floor", "oracle"), seeds=1), 0)
        victim = [p["id"] for p in SPECS[IDS[0]]["probes"] if p["type"] != "abstention"][0]
        sid = score.blind_case_id(IDS[0], 1, [p for p in SPECS[IDS[0]]["probes"] if p["type"] != "abstention"])
        self.judge.drop = {(sid, victim)}
        first = score.score_run(self.out, SPECS, self.cfg, self.environ, self.judge.label_probe, self.judge.label_items)
        self.assertEqual(first["invalid"], {"probe_answers_missing": 0, "judge_labels_invalid": 1})
        _, non_abs, _, _ = self.expected(IDS[0])
        self.assertEqual(first["dropped"][IDS[0]], {victim: ["label_missing"]})  # the oracle label is missing
        self.assertEqual(first["per_fixture"]["oracle"][IDS[0]]["primary_all"], 1.0)  # left out, not scored as incorrect
        self.assertEqual(first["per_fixture"]["oracle"][IDS[1]]["primary_all"], 1.0)
        data = arms.read_json(self.out / "labels.json")
        self.assertEqual(sorted(e["excluded"] for e in data["probe"].values()), [0, 1])
        self.assertEqual(len(data["probe"]), 2)
        kept = [e for e in data["probe"].values() if e["excluded"] == 1][0]
        self.assertEqual(len(kept["value"]), len(non_abs) - 1)  # the other labels of that call are cached
        # the next pass asks again for the dropped probe alone
        again = FakeJudge(SPECS)
        second = score.score_run(self.out, SPECS, self.cfg, self.environ, again.label_probe, again.label_items)
        self.assertEqual([[p["id"] for p in c["probes"]] for c, _ in again.probe_calls], [[victim]])
        self.assertEqual(second["invalid"]["judge_labels_invalid"], 0)
        self.assertEqual(second["per_fixture"]["oracle"][IDS[0]]["primary_all"], 1.0)

    def test_a_failed_proof_on_a_probe_batch_aborts_and_keeps_the_items_labels_but_none_of_its_own(self):
        self.assertEqual(self.batch(), 0)
        self.judge.probe_proof_ok = False
        with self.assertRaises(score.ScoreAbort) as ctx:
            score.score_run(self.out, SPECS, self.cfg, self.environ, self.judge.label_probe, self.judge.label_items)
        self.assertTrue(str(ctx.exception).startswith("judge isolation failed"), str(ctx.exception))
        self.assertEqual(len(self.judge.probe_calls), 1)
        data = arms.read_json(self.out / "labels.json")
        self.assertEqual(data["probe"], {})
        self.assertGreater(len(data["items"]), 0)

    def test_a_batched_call_stores_its_cost_once_and_the_cost_table_counts_calls_not_labels(self):
        scores = self.run_batch_and_score()
        data = arms.read_json(self.out / "labels.json")
        self.assertTrue(any(e["probes"] > 1 for e in data["probe"].values()))
        for entry in data["probe"].values():
            self.assertEqual((entry["cost_usd"], entry["wall_s"], entry["output_tokens"]), (0.01, 1.0, 2))
            self.assertNotIn("cost_usd", json.dumps(entry["value"]))
        row = scores["calls"]["judge"]["judge"]
        self.assertEqual(row["n"], scores["judge"]["calls_made"])
        self.assertAlmostEqual(row["cost_usd"], 0.01 * scores["judge"]["calls_made"])
        self.assertLess(row["n"], sum(len(c["probes"]) for c, _ in self.judge.probe_calls) + len(self.judge.items_calls))

    def test_legacy_per_answer_entries_still_load_and_any_other_shape_is_unreadable(self):
        self.assertEqual(self.batch(arm_names=("floor", "oracle"), seeds=1), 0)
        first = self.oracle_calls(seeds=1)
        data = arms.read_json(self.out / "labels.json")
        legacy = {"probe": {}, "items": data["items"]}
        for entry in data["probe"].values():
            for key, label in entry["value"].items():
                legacy["probe"][key] = {"value": label, "cost_usd": 0.01, "wall_s": 1.0, "input_tokens_total": None, "output_tokens": 2}
        arms.write_json(self.out / "labels.json", legacy)
        again = FakeJudge(SPECS)
        second = score.score_run(self.out, SPECS, self.cfg, self.environ, again.label_probe, again.label_items)
        self.assertEqual((again.probe_calls, second["judge"]["calls_made"]), ([], 0))
        self.assertEqual(second["per_fixture"], first["per_fixture"])
        for bad in ({"value": 3}, {"value": {"k": 3}}, {"value": None}, "x"):
            arms.write_json(self.out / "labels.json", {"probe": {"k": bad}, "items": {}})
            with self.assertRaises(score.ScoreAbort) as ctx:
                score.Labeller(self.out, self.cfg, self.environ, again.label_probe, again.label_items)
            self.assertEqual(str(ctx.exception), "labels file unreadable")

    def test_two_probes_with_one_answer_in_a_session_are_sent_once_and_both_get_the_label(self):
        self.out.mkdir(parents=True)
        labeller = score.Labeller(self.out, self.cfg, self.environ, self.judge.label_probe, self.judge.label_items)
        base = {"question": "q?", "gold": "g", "stale": [], "answer": "it is g"}
        got = labeller.probes([dict(base, id="a"), dict(base, id="b"), dict(base, id="c", answer="no idea")], "S1")
        self.assertEqual(got, {"a": "correct", "b": "correct", "c": "incorrect"})
        self.assertEqual([[p["id"] for p in c["probes"]] for c, _ in self.judge.probe_calls], [["a", "c"]])
        self.assertEqual((labeller.calls, labeller.cached, labeller.invalid), (1, 1, 0))
        # an entry is never overwritten: the same keys sent again land under a suffixed key
        labeller.known.clear()
        labeller.probes([dict(base, id="a")], "S2")
        labeller.known.clear()
        labeller.probes([dict(base, id="a")], "S3")
        keys = sorted(labeller.data["probe"])
        self.assertEqual(len(keys), 3)
        self.assertEqual([k for k in keys if k.endswith("-2")], [k for k in keys if "-" in k])  # exactly the repeat is suffixed
        self.assertEqual(len([k for k in keys if "-" in k]), 1)

    def test_the_judge_call_budget_is_checked_by_the_real_counter(self):
        self.assertEqual(self.batch(), 0)
        cfg = copy.deepcopy(self.cfg)
        cfg["judge"]["max_calls"] = 1000  # the judge check's cap is not what limits scoring
        calls = []
        real = claude_run.run_claude

        def fake_run(cfg_, prompt, sandbox, *, label, phase, **kw):
            calls.append(phase)
            raise AssertionError("not reached when the budget is spent")
        claude_run.run_claude = fake_run
        self.addCleanup(setattr, claude_run, "run_claude", real)
        claude_run.eval_root(self.environ).mkdir(parents=True, exist_ok=True)
        judge.save_stats(self.environ, {"count": 3, "successful": 3, "failed": 0}, "score")
        with self.assertRaises(judge.JudgeBudgetError):
            score.score_run(self.out, SPECS, cfg, self.environ, max_calls=3)
        self.assertEqual(calls, [])
        self.assertEqual(judge.load_stats(self.environ), {"count": 0, "successful": 0, "failed": 0})  # judge check untouched

    def test_a_judge_isolation_failure_aborts_and_keeps_nothing_it_could_not_trust(self):
        self.assertEqual(self.batch(), 0)
        self.judge.proof_ok = False
        with self.assertRaises(score.ScoreAbort) as ctx:
            score.score_run(self.out, SPECS, self.cfg, self.environ, self.judge.label_probe, self.judge.label_items)
        self.assertIn("judge isolation failed: markers_found", str(ctx.exception))
        self.assertFalse((self.out / "labels.json").exists())

    def test_missing_answers_count_as_incorrect_and_are_reported(self):
        self.plan["garbage_reply"] = True
        self.write_plan()
        self.assertEqual(self.batch(ids=IDS, arm_names=("floor", "oracle"), seeds=1), 0)
        scores = score.score_run(self.out, SPECS, self.cfg, self.environ, self.judge.label_probe, self.judge.label_items)
        self.assertGreater(scores["invalid"]["probe_answers_missing"], 0)
        self.assertEqual(scores["scorable_counts"], {f: 0 for f in IDS})  # the oracle answered nothing: no probe is scorable
        self.assertIsNone(scores["per_fixture"]["oracle"][IDS[0]]["primary"])

    def drop_baseline_answer(self, fid, pid):
        path = arms.probe_path(self.out, "baseline", fid, 1)
        data = arms.read_json(path)
        del data["answers"][pid]
        arms.write_json(path, data)

    def test_a_probe_without_a_label_is_left_out_of_numerator_and_denominator_not_scored_incorrect(self):
        fid = IDS[0]
        _, non_abs, _, _ = self.expected(fid)
        self.assertEqual(self.batch(seeds=1), 0)
        n = len(non_abs)
        base = score.score_run(self.out, SPECS, self.cfg, self.environ, self.judge.label_probe, self.judge.label_items)
        self.assertEqual(base["per_fixture"]["baseline"][fid]["primary"], round(3 / n, 10))
        self.drop_baseline_answer(fid, non_abs[-1]["id"])  # a probe the baseline missed, now unlabelled
        got = score.score_run(self.out, SPECS, self.cfg, self.environ, self.judge.label_probe, self.judge.label_items)
        self.assertEqual(got["invalid"]["probe_answers_missing"], 1)
        cell = got["per_fixture"]["baseline"][fid]
        self.assertEqual((cell["primary"], cell["primary_all"]), (round(3 / (n - 1), 10), round(3 / (n - 1), 10)))
        self.assertEqual(got["scorable"][fid], base["scorable"][fid])  # the baseline's label never decides scorability
        self.drop_baseline_answer(fid, non_abs[0]["id"])  # and a probe it got right
        cell = score.score_run(self.out, SPECS, self.cfg, self.environ, self.judge.label_probe,
                               self.judge.label_items)["per_fixture"]["baseline"][fid]
        self.assertEqual(cell["primary"], round(2 / (n - 2), 10))

    def test_an_abstention_probe_labelled_correct_is_not_a_false_answer_but_incorrect_is(self):
        fid = IDS[0]
        self.assertEqual(self.batch(seeds=1), 0)

        def labeller(verdicts):
            def label_probe(cfg, case, environ, model=None, **route):
                labels, rec = self.judge.label_probe(cfg, case, environ, model, **route)
                for p in case["probes"]:
                    if p["gold"] == score.ABSTENTION_GOLD:
                        labels[p["id"]] = verdicts[p["id"]]
                return labels, rec
            return label_probe
        abstentions = [p["id"] for p in SPECS[fid]["probes"] if p["type"] == "abstention"]
        self.assertEqual(len(abstentions), 2)
        # the baseline's junk line makes it answer both ("Probably 9999.", not the word UNKNOWN), so the judge sees both
        mixed = {abstentions[0]: "correct", abstentions[1]: "incorrect"}
        got = score.score_run(self.out, SPECS, self.cfg, self.environ, labeller(mixed), self.judge.label_items)
        self.assertEqual(got["per_fixture"]["baseline"][fid]["false_answer"], 0.5)
        for verdicts, expect in (({k: "correct" for k in abstentions}, 0.0), ({k: "abstained" for k in abstentions}, 0.0),
                                 ({k: "incorrect" for k in abstentions}, 1.0)):
            self.tearDownLabels()
            got = score.score_run(self.out, SPECS, self.cfg, self.environ, labeller(verdicts), self.judge.label_items)
            self.assertEqual(got["per_fixture"]["baseline"][fid]["false_answer"], expect, verdicts)

    def tearDownLabels(self):
        (self.out / "labels.json").unlink()

    def test_an_excluded_abstention_probe_is_out_of_the_false_answer_rate_and_listed_as_excluded(self):
        fid = IDS[0]
        specs = copy.deepcopy(SPECS)
        abstentions = [p for p in specs[fid]["probes"] if p["type"] == "abstention"]
        abstentions[0]["excluded"] = "the gold cannot score it"
        self.assertEqual(self.batch(seeds=1), 0)

        def label_probe(cfg, case, environ, model=None, **route):
            labels, rec = self.judge.label_probe(cfg, case, environ, model, **route)
            for p in case["probes"]:
                if p["id"] == abstentions[0]["id"]:
                    labels[p["id"]] = "incorrect"  # would count as false if it were still counted
                elif p["gold"] == score.ABSTENTION_GOLD:
                    labels[p["id"]] = "abstained"
            return labels, rec
        got = score.score_run(self.out, specs, self.cfg, self.environ, label_probe, self.judge.label_items)
        self.assertEqual(got["excluded"][fid], [abstentions[0]["id"]])
        self.assertEqual(got["per_fixture"]["baseline"][fid]["false_answer"], 0.0)

    def test_floor_and_oracle_must_hold_exactly_seeds_1_to_n(self):
        self.assertEqual(self.batch(arm_names=("floor", "baseline", "oracle"), seeds=2), 0)
        floor_s1 = arms.probe_path(self.out, "floor", IDS[0], 1)
        saved = floor_s1.read_bytes()
        floor_s1.unlink()  # seed 2 only
        with self.assertRaises(score.ScoreAbort) as ctx:
            score.score_run(self.out, SPECS, self.cfg, self.environ, self.judge.label_probe, self.judge.label_items)
        self.assertIn("exactly seeds 1..2", str(ctx.exception))
        floor_s1.write_bytes(saved)
        extra = arms.probe_path(self.out, "oracle", IDS[1], 3)  # a seed beyond run.json's 2
        extra.write_bytes(arms.probe_path(self.out, "oracle", IDS[1], 2).read_bytes())
        with self.assertRaises(score.ScoreAbort) as ctx:
            score.score_run(self.out, SPECS, self.cfg, self.environ, self.judge.label_probe, self.judge.label_items)
        self.assertIn("exactly seeds 1..2", str(ctx.exception))
        extra.unlink()
        self.assertTrue(score.score_run(self.out, SPECS, self.cfg, self.environ, self.judge.label_probe,
                                        self.judge.label_items)["sanity"]["ok"])

    def test_cache_keys_carry_the_resolved_judge_and_the_prompt_hash(self):
        self.assertEqual(self.batch(seeds=1), 0)
        pin = self.cfg["judge"]["model"]
        first = score.score_run(self.out, SPECS, self.cfg, self.environ, self.judge.label_probe, self.judge.label_items)
        self.assertEqual(first["judge_model"], pin)
        keys = {k for e in arms.read_json(self.out / "labels.json")["probe"].values() for k in e["value"]}
        probe_case = next(c for c, _ in self.judge.probe_calls)["probes"][0]
        self.assertIn(score.answer_key(probe_case, pin), keys)
        self.assertNotIn(score.answer_key(probe_case, ""), keys)
        calls = first["judge"]["calls_made"]
        self.assertGreater(calls, 0)
        # naming the pinned model is the same judge as naming none; another judge is a miss and labels again
        same = score.score_run(self.out, SPECS, self.cfg, self.environ, self.judge.label_probe, self.judge.label_items, model=pin)
        self.assertEqual((same["judge"]["calls_made"], same["judge_model"]), (0, pin))
        other = score.score_run(self.out, SPECS, self.cfg, self.environ, self.judge.label_probe, self.judge.label_items, model="claude-other-1")
        self.assertEqual((other["judge"]["calls_made"], other["judge_model"]), (calls, "claude-other-1"))

    def test_a_changed_judge_prompt_is_a_cache_miss(self):
        self.assertEqual(self.batch(seeds=1), 0)
        first = score.score_run(self.out, SPECS, self.cfg, self.environ, self.judge.label_probe, self.judge.label_items)
        original = judge.PROMPTS
        self.addCleanup(setattr, judge, "PROMPTS", original)
        edited = self.tmp / "prompts"
        shutil.copytree(original, edited)
        (edited / "judge-probe.md").write_text((original / "judge-probe.md").read_text(encoding="utf-8") + "\nAlso: be kind.\n",
                                               encoding="utf-8")
        judge.PROMPTS = edited
        again = score.score_run(self.out, SPECS, self.cfg, self.environ, self.judge.label_probe, self.judge.label_items)
        items_calls = len(self.judge.items_calls)
        self.assertGreater(again["judge"]["calls_made"], 0)  # the probe labels are asked again
        self.assertLess(again["judge"]["calls_made"], first["judge"]["calls_made"])  # the items labels stay cached
        self.assertEqual(len(self.judge.items_calls), items_calls)

    def test_floor_and_oracle_sessions_are_required(self):
        self.assertEqual(self.batch(arm_names=("floor", "baseline", "oracle")), 0)
        for path in (self.out / "probes" / "oracle").rglob("*.json"):
            path.unlink()
        with self.assertRaises(score.ScoreAbort) as ctx:
            score.score_run(self.out, SPECS, self.cfg, self.environ, self.judge.label_probe, self.judge.label_items)
        self.assertIn("floor and oracle probe sessions are required", str(ctx.exception))

    def test_gallery_and_cost_table(self):
        scores = self.run_batch_and_score()
        missed = {(m["fixture"], m["item"]) for m in scores["gallery"]["missed_items"]}
        for fid in IDS:
            _, _, needed, _ = self.expected(fid)
            for item in needed[3:]:
                self.assertIn((fid, item), missed)
            for item in needed[:3]:
                self.assertNotIn((fid, item), missed)
        self.assertTrue(all(g["label"] != "correct" for g in scores["gallery"]["probes"]))
        calls = scores["calls"]
        self.assertEqual(calls["preserve"]["baseline"]["n"], 4)
        self.assertAlmostEqual(calls["preserve"]["baseline"]["cost_usd"], 0.8)
        self.assertEqual(calls["probe"]["floor"]["n"], 4)
        self.assertEqual(calls["judge"]["judge"]["n"], scores["judge"]["calls_made"])
        self.assertEqual(scores["preserve"], {"baseline": {"runs": 4, "changed": 4}})

    def test_a_preserve_run_that_changes_nothing_scores_zero_recall_not_an_error(self):
        self.plan["preserve_noop"] = True
        self.write_plan()
        scores = self.run_batch_and_score()
        self.assertEqual(scores["preserve"], {"baseline": {"runs": 4, "changed": 0}})
        self.assertEqual(scores["per_fixture"]["baseline"][IDS[0]]["recall"], 0.0)
        self.assertIsNone(scores["per_fixture"]["baseline"][IDS[0]]["precision"])
        self.assertEqual(scores["per_fixture"]["baseline"][IDS[0]]["primary"], 0.0)

    def test_scores_json_round_trips_and_carries_no_path(self):
        scores = self.run_batch_and_score()
        score.write_scores(self.out, scores)
        text = (self.out / "scores.json").read_text(encoding="utf-8")
        self.assertEqual(json.loads(text)["per_fixture"], scores["per_fixture"])
        self.assertNotIn(str(self.tmp), text)
        self.assertNotIn("_matched", text)

    def test_a_scored_batch_goes_through_the_report(self):
        import report
        scores = self.run_batch_and_score()
        score.write_scores(self.out, scores)
        dest = self.tmp / "report"
        self.assertEqual(report.write_report(self.out, dest, io.StringIO()), 0)
        md = (dest / "REPORT.md").read_text(encoding="utf-8")
        self.assertIn("## Per-arm metrics", md)
        self.assertIn("| baseline |", md)
        self.assertIn("API-price equivalent", md)
        self.assertNotIn(str(self.tmp), md)
        self.assertFalse((dest / "pareto.svg").exists())  # the chart is gone; the report writes REPORT.md only

    def test_command_score_prints_one_summary_line(self):
        self.assertEqual(self.batch(), 0)
        original = claude_run.preflight
        claude_run.preflight = lambda cfg, environ=None: ""
        self.addCleanup(setattr, claude_run, "preflight", original)
        loader = claude_run.load_config
        claude_run.load_config = lambda path=None: self.cfg
        self.addCleanup(setattr, claude_run, "load_config", loader)
        out = io.StringIO()
        code = score.command_score(self.out, FIXTURES, self.environ, out, self.judge.label_probe, self.judge.label_items)
        self.assertEqual(code, 0, out.getvalue())
        self.assertRegex(out.getvalue(), r"^score: 2 fixtures, arms floor,baseline,oracle, judge calls \d+ \(cache hits \d+\), "
                                         r"sanity oracle_recall 1\.000 mismatch_recall 0\.000 PASS, scorable ")
        self.assertTrue((self.out / "scores.json").is_file())
        out = io.StringIO()
        self.judge.mode = "blind"
        (self.out / "labels.json").unlink()
        self.assertEqual(score.command_score(self.out, FIXTURES, self.environ, out, self.judge.label_probe, self.judge.label_items), 1)
        self.assertIn("score: ABORT judge sanity controls failed", out.getvalue())
        out = io.StringIO()
        env = dict(self.environ)
        env.pop("CPR_EVAL_LIVE")
        self.assertEqual(score.command_score(self.out, FIXTURES, env, out), 2)


class ScoreSplitTests(Env):
    """The primary split by each probe's code_answers tag, on tags set by hand in a copy of the specs."""

    def setUp(self):
        super().setUp()
        self.judge = FakeJudge(SPECS)
        self.specs = copy.deepcopy(SPECS)
        # Scorable probes are p1..p8 in dev-001 and p1..p5, p7 and p8 in dev-002, whose p6 the spec excludes (the floor is never
        # right, the oracle always). The baseline answers a probe correctly when its item is one of the first three
        # needed items: p1, p2, p3 in both fixtures.
        # dev-001: true on p1 and p4, false on the other six scorable probes.
        for probe in self.specs[IDS[0]]["probes"]:
            probe["code_answers"] = probe["id"] in ("p1", "p4")
        # dev-002: false on every probe except p4, which carries no tag at all.
        for probe in self.specs[IDS[1]]["probes"]:
            probe["code_answers"] = False
        del next(p for p in self.specs[IDS[1]]["probes"] if p["id"] == "p4")["code_answers"]

    def score(self):
        self.assertEqual(self.batch(), 0)
        return score.score_run(self.out, self.specs, self.cfg, self.environ, self.judge.label_probe, self.judge.label_items)

    def test_the_primary_is_split_by_the_tag(self):
        scores = self.score()
        pf = scores["per_fixture"]
        # dev-001 baseline: 8 scorable probes, correct on p1, p2, p3 -> primary 3/8.
        #   tagged true {p1, p4}: p1 correct, p4 not -> 1/2.  tagged false {p2, p3, p5, p6, p7, p8}: p2, p3 correct -> 2/6.
        base = pf["baseline"][IDS[0]]
        self.assertEqual(base["primary"], round(3 / 8, 10))
        self.assertEqual(base["primary_code"], 0.5)
        self.assertEqual(base["primary_nocode"], round(2 / 6, 10))
        # floor is never right and the oracle always is, whatever the tag.
        self.assertEqual((pf["floor"][IDS[0]]["primary_code"], pf["floor"][IDS[0]]["primary_nocode"]), (0.0, 0.0))
        self.assertEqual((pf["oracle"][IDS[0]]["primary_code"], pf["oracle"][IDS[0]]["primary_nocode"]), (1.0, 1.0))
        # the per-seed cells carry the same numbers
        for cell in scores["per_seed"]["baseline"][IDS[0]].values():
            self.assertEqual((cell["primary_code"], cell["primary_nocode"]), (0.5, round(2 / 6, 10)))

    def test_an_empty_group_gives_none_and_an_untagged_probe_counts_in_neither(self):
        scores = self.score()
        # dev-002: no probe is tagged true, so the true group is empty in every arm.
        for arm in ("floor", "baseline", "oracle"):
            self.assertIsNone(scores["per_fixture"][arm][IDS[1]]["primary_code"], arm)
            for cell in scores["per_seed"][arm][IDS[1]].values():
                self.assertIsNone(cell["primary_code"], arm)
        # p4 has no tag: the false group is p1, p2, p3, p5, p7, p8 (6 probes; p6 is excluded by the spec), baseline
        # correct on p1, p2, p3 -> 3/6. The primary covers the 7 scorable probes -> 3/7.
        base = scores["per_fixture"]["baseline"][IDS[1]]
        self.assertEqual(base["primary_nocode"], 0.5)
        self.assertEqual(base["primary"], round(3 / 7, 10))

    def test_scorable_split_counts_and_the_summary_line_clause(self):
        scores = self.score()
        self.assertEqual(scores["scorable_split"], {IDS[0]: {"code": 2, "nocode": 6}, IDS[1]: {"code": 0, "nocode": 6}})
        self.assertTrue(score.summary_line(scores).endswith(", scorable by code_answers: true 2, false 12"))

    def test_a_spec_without_the_tag_leaves_both_groups_empty(self):
        for probe in self.specs[IDS[0]]["probes"]:
            probe.pop("code_answers")
        scores = self.score()
        self.assertEqual(scores["scorable_split"][IDS[0]], {"code": 0, "nocode": 0})
        base = scores["per_fixture"]["baseline"][IDS[0]]
        self.assertEqual((base["primary_code"], base["primary_nocode"]), (None, None))
        self.assertEqual(base["primary"], round(3 / 8, 10))


# --------------------------------------------------------------------------
# Replaying the stored pilot through the batched judge
# --------------------------------------------------------------------------

PILOT = ROOT / "tests" / "data" / "exp0001-results" / "pilot-syn"

# The questions the stored pilot asked, before the fixtures were repaired and reworded them: (fixture id, probe id) -> question.
PILOT_TIME_QUESTIONS = {
    ("dev-002-recipe-scaler", "p6"): "Which parts of the plan have nobody started on?",
    ("dev-001-tide-table", "p7"): "What follows the station test among the open jobs?",
}


class PilotReplayTests(unittest.TestCase):
    """Re-score the committed pilot's probe sessions through the real batched judge path.

    The scripted runner answers each batch with the label the pilot stored for that answer, in reversed order. This
    proves the batching plumbing (case building, id mapping, caching, counting) reproduces the pilot's scores from
    the same labels. It does not prove that a live model labels a batch as well as it labels one answer; the live
    judge-check and the second-labeller agreement (kappa) cover that.

    The stored pilot predates a repair of two probes, so the replay restores the two questions it asked.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        home = self.tmp / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "CLAUDE.md").write_text("# Title\n" + HOST_MARKER + "\n", encoding="utf-8")
        self.environ = {"PATH": os.environ["PATH"], "HOME": str(home), "TMPDIR": str(self.tmp), "CPR_EVAL_LIVE": "1"}
        self.cfg = claude_run.load_config()
        self.cfg["score"] = {"max_judge_calls": 500}
        self.out = self.tmp / "replay"
        self.out.mkdir()
        shutil.copy(PILOT / "run.json", self.out / "run.json")
        for name in ("arms", "preserve", "probes"):  # never _raw/, never a write into the committed directory
            shutil.copytree(PILOT / name, self.out / name)
        # The committed labels keep the legacy key format; a converted copy is what the replay reads.
        self.stored = legacy_labels.convert(PILOT / "labels.json", [self.out], [self.pilot_specs()], self.cfg["judge"]["model"])
        arms.write_json(self.out / "labels.json", {"probe": {}, "items": self.stored["items"]})
        self.current, self.batches, self.returned, self.runs = None, [], [], 0
        original = claude_run.run_claude
        claude_run.run_claude = self.fake_run
        self.addCleanup(setattr, claude_run, "run_claude", original)

    def pilot_specs(self) -> dict:
        """The current specs with the two questions the pilot asked put back (and their exclusion lifted)."""
        specs = score.load_specs(FIXTURES, arms.read_json(self.out / "run.json")["fixtures"])
        for (fid, pid), asked in PILOT_TIME_QUESTIONS.items():
            probe = next(p for p in specs[fid]["probes"] if p["id"] == pid)
            self.assertNotEqual(probe["question"], asked)  # the spec was repaired; a revert makes this overlay stale
            probe["question"] = asked
            probe.pop("excluded", None)  # the pilot predates the probe exclusion, so both probes were scored
        return specs

    def stored_label(self, probe):
        return self.stored["probe"][score.answer_key(probe, self.cfg["judge"]["model"])]["value"]

    def fake_run(self, cfg, prompt, sandbox, *, label, kind="plain", probe=False, model=None, **kw):
        if self.current is None:
            raise AssertionError("the replay asked the runner for an items case; every items label is cached")
        self.runs += 1
        self.assertIn("Case id: " + self.current["id"] + "\n", prompt)
        reply = {"labels": [{"id": p["id"], "label": self.stored_label(p), "quote": ""} for p in reversed(self.current["probes"])]}
        init = {"type": "system", "subtype": "init", "claude_code_version": cfg["claude_code_version"], "model": model,
                "plugins": [], "mcp_servers": [], "skills": list(cfg["init_allowlist"]["skills"]),
                "agents": list(cfg["init_allowlist"]["agents"]), "tools": [], "memory_paths": {}}
        tpath = self.tmp / f"t{self.runs}.jsonl"
        tpath.write_text(json.dumps({"type": "attachment", "attachment": {"type": "instructions", "files": []}}) + "\n",
                         encoding="utf-8")
        events = [init, {"type": "result", "subtype": "success", "is_error": False, "permission_denials": [],
                         "result": json.dumps(reply)}]
        return {"events": events, "sandbox": str(sandbox), "kind": kind, "resumed": False, "plugin": None,
                "transcript": str(tpath), "session_id": "s", "config_dir": str(self.tmp / "none")}

    def label_probe(self, cfg, case, environ, model=None, **route):
        self.batches.append(case)
        self.current = case
        try:
            labels, rec = judge.label_probe(cfg, case, environ, model, **route)
        finally:
            self.current = None
        self.returned.append(labels)
        return labels, rec

    def test_the_stored_pilot_rescored_in_session_batches_reproduces_its_scores(self):
        committed = arms.read_json(PILOT / "scores.json")
        specs = self.pilot_specs()
        scores = score.score_run(self.out, specs, self.cfg, self.environ, label_probe=self.label_probe,
                                 label_items=judge.label_items)
        scores = json.loads(json.dumps(scores))
        self.assertEqual(len(self.batches), 4)
        self.assertEqual(sum(len(labels) for labels in self.returned), 20)
        self.assertEqual(scores["invalid"], {"probe_answers_missing": 0, "judge_labels_invalid": 0})
        self.assertEqual(scores["judge"], {"calls_made": 4, "cached_hits": 44})  # repeated answers and items lookups
        self.assertEqual(scores["calls"]["judge"]["judge"]["n"], 10)  # 6 stored items entries + 4 batch entries
        # the label and quote checks are today's, and every returned label passes them
        _, element = judge.probe_schemas()
        self.assertEqual(element["properties"]["label"], {"type": "string", "enum": ["correct", "incorrect", "abstained"]})
        self.assertEqual(element["properties"]["quote"], {"type": "string"})
        for labels in self.returned:
            for pid, label in labels.items():
                self.assertIn(label, ("correct", "incorrect", "abstained"))
                self.assertEqual(judge.validate({"id": pid, "label": label, "quote": ""}, element), [])
        # The committed pilot predates the code_answers split: compare everything it has, and check the additions apart.
        split = scores.pop("scorable_split")
        self.assertEqual({f: c["code"] + c["nocode"] for f, c in split.items()}, scores["scorable_counts"])  # every probe is tagged
        for by_fixture in scores["per_fixture"].values():
            for cell in by_fixture.values():
                del cell["primary_code"], cell["primary_nocode"]
        for by_fixture in scores["per_seed"].values():
            for by_seed in by_fixture.values():
                for cell in by_seed.values():
                    del cell["primary_code"], cell["primary_nocode"]
        self.assertEqual(scores.pop("excluded"), {f: [] for f in scores["fixtures"]})  # the pilot predates the key
        self.assertEqual(scores.pop("judge_model"), self.cfg["judge"]["model"])  # nor did it record the judge
        self.assertEqual(sorted(scores), sorted(committed))
        for key in committed:
            if key not in ("judge", "calls"):
                self.assertEqual(scores[key], committed[key], key)


# --------------------------------------------------------------------------
# The command line
# --------------------------------------------------------------------------

class CliTests(unittest.TestCase):
    def run_cli(self, *args, env=None):
        e = dict(os.environ)
        e.pop("CPR_EVAL_LIVE", None)
        e.update(env or {})
        return subprocess.run([sys.executable, exp0001_paths.RUN, *args], capture_output=True, text=True,
                              encoding="utf-8", env=e)

    def test_run_and_score_refuse_without_the_live_flag(self):
        with tempfile.TemporaryDirectory() as d:
            proc = self.run_cli("run", "--out", d, "--fixtures", "dev-001")
            self.assertEqual(proc.returncode, 2)
            self.assertIn("live run refused: set CPR_EVAL_LIVE=1", proc.stdout)
            (Path(d) / "run.json").write_text(json.dumps({"split": "dev", "fixtures": [], "arms": []}), encoding="utf-8")
            proc = self.run_cli("score", "--from", d)
            self.assertEqual(proc.returncode, 2)

    def test_score_without_a_run_file_and_report_without_scores_stop_with_a_label(self):
        with tempfile.TemporaryDirectory() as d:
            proc = self.run_cli("score", "--from", d)
            self.assertEqual((proc.returncode, proc.stdout.strip()), (1, "score: STOP run.json not found"))
            proc = self.run_cli("report", "--from", d)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("report: STOP", proc.stdout)

    def test_every_subcommand_is_listed(self):
        proc = self.run_cli("--help")
        for name in ("run", "score", "report"):
            self.assertIn(name, proc.stdout)


if __name__ == "__main__":
    unittest.main()
