"""Tests for arms, probe sessions, the batch and scoring. A scripted `claude` executable and fake judges stand in
for the live services; nothing here calls the real CLI.

Run with: python3 -m unittest discover tests
"""

from __future__ import annotations

import io
import json
import os
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
import probes  # noqa: E402
import score  # noqa: E402

# the dev fixtures are not part of this experiment: tests that read them are not ported
FIXTURES = ROOT / "evals" / "fixtures" / "dev"
IDS = ["dev-001-csv-tally", "dev-002-notes-api"]
HOST_MARKER = "a synthetic host instruction line used as a marker"
JUNK = "- The maintainer likes tabs."


def spec_of(fid) -> dict:
    return fixtures.load_spec(FIXTURES / fid / "spec.json")


try:
    SPECS = {fid: spec_of(fid) for fid in IDS}
except FileNotFoundError:  # the dev fixtures are not part of this experiment
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

    def test_a_skill_variant_is_a_preserve_arm_only_when_its_skill_file_exists(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "lean").mkdir()
            (Path(d) / "lean" / "SKILL.md").write_text("x", encoding="utf-8")
            self.assertEqual(arms.arm_kind("lean", d), "preserve")
            self.assertIsNone(arms.variant_skill("other", d))
            self.assertIsNone(arms.variant_skill("../lean", d))


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
            arms.write_raw(out, "probe", "floor", "fx-1", 2, {"events": [1, 2]}, environ)
            expected = claude_run.eval_root(environ) / "raw" / "run-1" / "probe-floor-fx-1-s2.json"
            self.assertEqual(arms.raw_path(out, "probe", "floor", "fx-1", 2, environ), expected)
            self.assertEqual(json.loads(expected.read_text(encoding="utf-8")), {"events": [1, 2]})
            self.assertFalse(out.exists())
            self.assertNotIn("_raw", expected.parts)


# --------------------------------------------------------------------------
# Probe prompt and answers
# --------------------------------------------------------------------------

class ProbePromptTests(unittest.TestCase):


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


# --------------------------------------------------------------------------
# The batch, through the real run_claude and the scripted claude
# --------------------------------------------------------------------------


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


    def test_thresholds_are_the_decided_ones(self):
        self.assertEqual((score.ORACLE_RECALL_MIN, score.MISMATCH_RECALL_MAX), (0.95, 0.05))


    # -- one judge call per probe session --------------------------------------

    def oracle_calls(self, seeds=2):
        """Score floor and oracle sessions only: the floor answers UNKNOWN throughout, the oracle answers every
        non-abstention probe and says UNKNOWN to the abstention ones."""
        self.assertEqual(self.batch(arm_names=("floor", "oracle"), seeds=seeds), 0)
        return score.score_run(self.out, SPECS, self.cfg, self.environ, self.judge.label_probe, self.judge.label_items)


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


    def drop_baseline_answer(self, fid, pid):
        path = arms.probe_path(self.out, "baseline", fid, 1)
        data = arms.read_json(path)
        del data["answers"][pid]
        arms.write_json(path, data)


    def tearDownLabels(self):
        (self.out / "labels.json").unlink()


# --------------------------------------------------------------------------
# Replaying the stored pilot through the batched judge
# --------------------------------------------------------------------------

PILOT = ROOT / "evals" / "results" / "pilot-20261017"

# The questions the pilot asked, before the 2026-10-18 fixture repair reworded them: (fixture id, probe id) -> question.
PILOT_TIME_QUESTIONS = {
    ("dev-002-notes-api", "p6"): "Which features have not been started?",
    ("dev-005-flag-service", "p6"): "Which feature has not been started yet?",
}


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
