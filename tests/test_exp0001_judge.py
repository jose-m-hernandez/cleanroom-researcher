"""Offline tests for the Claude-backed judge. run_claude is replaced; nothing calls the real CLI.

Run with: python3 -m unittest discover tests
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path

from tests import exp0001_paths  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]

import claude_run  # noqa: E402
import judge  # noqa: E402

MARKER = "synthetic host instruction line used as a marker"


def batch_of(*cases, case_id="B1"):
    """A batch case from probe cases (the generic expect key rides along and is ignored)."""
    return {"id": case_id, "probes": list(cases)}


def probe_reply(expect, ids):
    """The batched reply a faithful judge would give for these probe ids."""
    return {"labels": [{"id": i, "label": expect[i], "quote": ""} for i in ids]}


def default_answers():
    answers = {}
    lines = [{"line": n, "kind": k, "item_id": i, "placed_ok": k == "needed", "quote": ""}
             for n, (k, i) in enumerate(judge.ITEMS_CASE["expect"], start=1)]
    answers["I1"] = {"lines": lines}
    return answers


JUDGE_MODEL = "claude-opus-5-5"
ONE = batch_of(judge.PROBE_CASES[0])  # one P1 probe in a batch
BATCH_ID = judge.PROBE_BATCH["id"]


class ClaudeJudgeTests(unittest.TestCase):
    """The claude backend, with run_claude replaced by a scripted runner."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        exe = bin_dir / "claude"
        exe.write_text("#!/bin/sh\necho '2.1.302 (Claude Code)'\n", encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
        home = self.tmp / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "CLAUDE.md").write_text("# Title\n" + MARKER + "\n", encoding="utf-8")
        self.environ = {"PATH": str(bin_dir) + os.pathsep + os.environ["PATH"], "HOME": str(home),
                        "TMPDIR": str(self.tmp), "CPR_EVAL_LIVE": "1"}
        self.config = self.tmp / "config.json"
        cfg = claude_run.load_config()
        cfg["judge"]["max_calls"] = 15  # tests never depend on the committed cap
        claude_run.save_config(cfg, self.config)
        self.answers = default_answers()  # reply per case id; a batch case id without one gets a faithful reply
        self.expect = {c["id"]: c["expect"] for c in judge.PROBE_CASES}
        self.prompts = []
        self.calls = []
        self.model = JUDGE_MODEL
        self.leak = False
        self.throttle = None  # CLI text of a throttled (limit) result, or None
        original = claude_run.run_claude
        claude_run.run_claude = self.fake_run
        self.addCleanup(setattr, claude_run, "run_claude", original)

    def fake_run(self, cfg, prompt, sandbox, *, label, kind="plain", probe=False, model=None, **kw):
        self.calls.append({"kind": kind, "probe": probe, "model": model, "plugin": kw.get("plugin"), "phase": kw.get("phase")})
        self.prompts.append(prompt)
        case_id = prompt.split("Case id: ")[1].split("\n")[0]
        asked = re.findall(r"^Answer id: (\S+)$", prompt, re.M)
        answer = self.answers[case_id] if case_id in self.answers else probe_reply(self.expect, asked)
        init = {"type": "system", "subtype": "init", "claude_code_version": cfg["claude_code_version"],
                "model": self.model, "plugins": [], "mcp_servers": [], "skills": list(cfg["init_allowlist"]["skills"]),
                "agents": list(cfg["init_allowlist"]["agents"]), "tools": [], "memory_paths": {}}
        rows = [{"type": "attachment", "attachment": {"type": "instructions", "files": []}}]
        if self.leak:
            rows.append({"type": "user", "message": {"content": MARKER}})
        tpath = self.tmp / f"t{len(self.calls)}.jsonl"
        tpath.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        text = json.dumps(answer)
        if "P2" in asked:
            text = "```json\n" + text + "\n```"
        events = [init, {"type": "result", "subtype": "success", "is_error": False, "permission_denials": [], "result": text}]
        if self.throttle:
            events = [init, {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}},
                      {"type": "result", "subtype": "error_during_execution", "is_error": True, "result": self.throttle}]
        return {"events": events, "sandbox": str(sandbox), "kind": kind, "resumed": False, "plugin": None,
                "transcript": str(tpath), "session_id": "s", "config_dir": str(self.tmp / "none")}

    def run_check(self):
        out = io.StringIO()
        code = judge.judge_check(self.environ, out, self.config)
        return code, out.getvalue()

    def test_pass_line_and_every_call_is_a_probe_run_on_the_judge_model(self):
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.assertEqual(out.strip(), f"judge-check: backend=claude model={JUDGE_MODEL} isolation=PASS calls 2 probe 7/7 items 5/5")
        self.assertEqual(len(self.calls), 2)
        for call in self.calls:
            self.assertEqual((call["kind"], call["probe"], call["model"], call["plugin"]), ("probe", True, JUDGE_MODEL, None))
        self.assertEqual(judge.load_stats(self.environ), {"count": 2, "successful": 2, "failed": 0})

    def test_judge_pin_is_separate_from_the_system_under_test_pin(self):
        cfg = claude_run.load_config(self.config)
        self.assertNotEqual(cfg["judge"]["model"], cfg["model"])
        self.assertEqual(cfg["judge"]["model"], JUDGE_MODEL)
        self.assertEqual(cfg["judge"]["cross_family_attempt"][:7], "failed:")
        # A run that reports the system-under-test model fails the judge's proof.
        self.model = cfg["model"]
        code, out = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("STOP probes isolation failed: model_mismatch", out)
        self.assertEqual(len(self.calls), 1)

    def test_failed_proof_excludes_the_label_and_stops(self):
        self.leak = True
        code, out = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("STOP probes isolation failed: markers_found", out)
        self.assertNotIn(MARKER, out)
        self.assertEqual(len(self.calls), 1)

    def test_failed_proofs_are_counted_as_failed(self):
        self.leak = True
        cfg = claude_run.load_config(self.config)
        judge.label_probe(cfg, ONE, self.environ)
        self.assertEqual(judge.load_stats(self.environ), {"count": 1, "successful": 0, "failed": 1})

    def test_throttled_calls_are_counted_apart_and_keep_the_cli_message(self):
        cfg = claude_run.load_config(self.config)
        labels, rec = judge.label_probe(cfg, ONE, self.environ, phase="evaluation", counter="score", max_calls=9)
        self.assertEqual(labels, {"P1": "correct"})
        self.throttle = "You've hit your limit. Resets at 3pm."
        labels, rec = judge.label_probe(cfg, ONE, self.environ, phase="evaluation", counter="score", max_calls=9)
        self.assertIsNone(labels)
        self.assertTrue(judge.is_throttled(rec))
        self.assertEqual(judge.load_stats(self.environ, "score"), {
            "count": 2, "successful": 1, "failed": 0, "throttled": 1, "throttle_messages": [self.throttle]})
        # the counters survive the next call's read and write
        self.throttle = None
        judge.label_probe(cfg, ONE, self.environ, phase="evaluation", counter="score", max_calls=9)
        stats = judge.load_stats(self.environ, "score")
        self.assertEqual((stats["count"], stats["successful"], stats["throttled"]), (3, 2, 1))
        self.assertEqual(stats["throttle_messages"], ["You've hit your limit. Resets at 3pm."])

    def test_a_throttle_with_a_real_proof_failure_is_a_failure_not_a_throttle(self):
        cfg = claude_run.load_config(self.config)
        self.throttle = "You've hit your limit."
        self.leak = True
        labels, rec = judge.label_probe(cfg, ONE, self.environ, phase="evaluation", counter="score", max_calls=9)
        self.assertIsNone(labels)
        self.assertFalse(judge.is_throttled(rec))
        self.assertEqual(judge.load_stats(self.environ, "score"), {"count": 1, "successful": 0, "failed": 1})

    def test_the_rate_limit_signal_ignores_the_informational_events_every_run_carries(self):
        info = {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed_warning"}}
        ok = {"type": "result", "subtype": "success", "is_error": False, "result": "the rate limit docs say 429"}
        self.assertIsNone(judge.rate_limit_message({"events": [info, ok]}))
        bad = {"type": "result", "subtype": "error_during_execution", "is_error": True}
        self.assertIsNone(judge.rate_limit_message({"events": [info, dict(bad, result="tool call could not be parsed")]}))
        self.assertEqual(judge.rate_limit_message({"events": [dict(bad, result="Usage limit reached  |  resets 5pm")]}),
                         "Usage limit reached | resets 5pm")
        self.assertEqual(judge.rate_limit_message({"events": [dict(bad, api_error_status=429, result="")]}), "HTTP 429")
        rejected = {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}}
        self.assertEqual(judge.rate_limit_message({"events": [rejected, bad]}), "rate_limit_event status=rejected")
        self.assertIsNone(judge.rate_limit_message({"events": []}))
        self.assertEqual(judge.rate_limit_message({"events": [rejected]}), "rate_limit_event status=rejected")

    def test_stats_and_spend_writes_are_atomic(self):
        seen = []
        original = os.replace
        os.replace = lambda src, dst: (seen.append((os.path.dirname(src) == os.path.dirname(dst), os.path.basename(dst))),
                                       original(src, dst))[1]
        self.addCleanup(setattr, os, "replace", original)
        judge.save_stats(self.environ, {"count": 1, "successful": 0, "failed": 0})
        claude_run.add_spend(0.25, self.environ)
        self.assertEqual(seen, [(True, "judge-calls.json"), (True, "spend.json")])
        self.assertEqual(sorted(p.name for p in claude_run.eval_root(self.environ).iterdir() if p.name.endswith(".json")),
                         ["judge-calls.json", "spend.json"])
        self.assertEqual([p for p in claude_run.eval_root(self.environ).iterdir() if "." in p.name and p.suffix not in (".json",)], [])

    def test_old_stats_file_with_extra_keys_still_loads_but_a_bad_count_refuses(self):
        claude_run.eval_root(self.environ).mkdir(parents=True)
        path = claude_run.eval_root(self.environ) / "judge-calls.json"
        path.write_text(json.dumps({"count": 2, "successful": 1, "legacy": 1}), encoding="utf-8")
        self.assertEqual(judge.load_stats(self.environ), {"count": 2, "successful": 1, "failed": 0})
        for bad in ('{"count": "x"}', '{"count": -1}', '[]', '{"successful": 1}'):
            path.write_text(bad, encoding="utf-8")
            with self.assertRaises(judge.JudgeBudgetError):
                judge.load_stats(self.environ)

    def test_label_with_claude_returns_none_when_the_proof_fails(self):
        self.leak = True
        cfg = claude_run.load_config(self.config)
        labels, rec = judge.label_probe(cfg, ONE, self.environ)
        self.assertIsNone(labels)
        self.assertFalse(rec["proof"]["ok"])

    def test_a_model_argument_overrides_the_pin(self):
        cfg = claude_run.load_config(self.config)
        self.model = "other-model-x"
        labels, rec = judge.label_probe(cfg, ONE, self.environ, model=self.model)
        self.assertEqual(labels, {"P1": "correct"})
        self.assertEqual(self.calls[0]["model"], "other-model-x")

    # -- one call per session ------------------------------------------------

    def label_session(self, **route):
        cfg = claude_run.load_config(self.config)
        return judge.label_probe(cfg, judge.PROBE_BATCH, self.environ, phase="evaluation", counter="score", max_calls=9, **route)

    def test_one_call_labels_a_whole_session_and_the_counter_counts_it_once(self):
        labels, rec = self.label_session()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(labels, {c["id"]: c["expect"] for c in judge.PROBE_CASES})
        self.assertEqual(rec["label_excluded"], [])
        self.assertEqual(rec["label_problems"], [])
        self.assertEqual(judge.load_stats(self.environ, "score"), {"count": 1, "successful": 1, "failed": 0})
        self.assertEqual((self.calls[0]["phase"], self.calls[0]["model"]), ("evaluation", JUDGE_MODEL))

    def test_labels_follow_the_ids_not_the_order_of_the_reply(self):
        reply = probe_reply(self.expect, [c["id"] for c in judge.PROBE_CASES])
        reply["labels"].reverse()
        self.answers[BATCH_ID] = reply
        labels, rec = self.label_session()
        self.assertEqual(labels, self.expect)

    def test_a_missing_label_excludes_only_that_probe(self):
        reply = probe_reply(self.expect, [c["id"] for c in judge.PROBE_CASES if c["id"] != "P3"])
        self.answers[BATCH_ID] = reply
        labels, rec = self.label_session()
        self.assertEqual(labels, {k: v for k, v in self.expect.items() if k != "P3"})
        self.assertEqual((rec["label_excluded"], rec["label_problems"]), (["P3"], ["P3:missing"]))
        self.assertEqual(judge.load_stats(self.environ, "score")["successful"], 1)

    def test_a_label_outside_the_enum_or_with_a_wrong_shape_excludes_only_that_probe(self):
        ids = [c["id"] for c in judge.PROBE_CASES]
        for bad in ({"id": "P4", "label": "Correct", "quote": ""},      # no case-folding
                    {"id": "P4", "label": "incorrect"},                   # a missing quote is not defaulted
                    {"id": "P4", "label": "incorrect", "quote": "", "why": "r"},  # no extra keys
                    {"id": "P4", "label": "incorrect", "quote": 3}):
            reply = probe_reply(self.expect, [i for i in ids if i != "P4"])
            reply["labels"].append(bad)
            self.answers[BATCH_ID] = reply
            labels, rec = self.label_session()
            self.assertEqual(labels, {k: v for k, v in self.expect.items() if k != "P4"}, bad)
            self.assertEqual((rec["label_excluded"], rec["label_problems"]), (["P4"], ["P4:invalid"]), bad)

    def test_a_duplicated_id_excludes_that_probe_even_when_both_labels_agree(self):
        reply = probe_reply(self.expect, [c["id"] for c in judge.PROBE_CASES])
        reply["labels"].append({"id": "P2", "label": self.expect["P2"], "quote": ""})
        self.answers[BATCH_ID] = reply
        labels, rec = self.label_session()
        self.assertNotIn("P2", labels)
        self.assertEqual(len(labels), len(judge.PROBE_CASES) - 1)
        self.assertEqual((rec["label_excluded"], rec["label_problems"]), (["P2"], ["P2:duplicate"]))

    def test_an_id_outside_the_batch_is_a_problem_and_excludes_nothing(self):
        reply = probe_reply(self.expect, [c["id"] for c in judge.PROBE_CASES])
        reply["labels"].append({"id": "ZZ", "label": "correct", "quote": ""})
        self.answers[BATCH_ID] = reply
        labels, rec = self.label_session()
        self.assertEqual(labels, self.expect)
        self.assertEqual((rec["label_excluded"], rec["label_problems"]), ([], ["unknown_id"]))

    def test_a_reply_that_is_not_the_envelope_excludes_every_probe(self):
        for reply, problem in (({"labels": "x"}, "envelope_invalid"), ({"labels": [], "extra": 1}, "envelope_invalid"),
                               ([], "envelope_invalid"), ({}, "envelope_invalid")):
            self.answers[BATCH_ID] = reply
            labels, rec = self.label_session()
            self.assertEqual(labels, {}, reply)
            self.assertEqual((rec["label_excluded"], rec["label_problems"]), (sorted(self.expect), [problem]), reply)
        self.answers[BATCH_ID] = {"labels": []}  # a valid envelope with nothing in it: each probe is missing
        labels, rec = self.label_session()
        self.assertEqual((labels, rec["label_problems"]), ({}, [f"{i}:missing" for i in sorted(self.expect)]))

    def test_a_reply_that_is_not_json_excludes_every_probe_and_the_problems_carry_no_text(self):
        original = self.fake_run

        def prose(cfg, prompt, sandbox, **kw):
            rec = original(cfg, prompt, sandbox, **kw)
            rec["events"][-1]["result"] = "It uses port 3000, I think."
            return rec
        claude_run.run_claude = prose
        labels, rec = self.label_session()
        self.assertEqual(labels, {})
        self.assertEqual(rec["label_problems"], ["output_not_json"])
        self.assertNotIn("3000", json.dumps([rec["label_excluded"], rec["label_problems"]]))

    def test_a_failed_proof_excludes_every_label_of_the_batched_call(self):
        self.leak = True
        labels, rec = self.label_session()
        self.assertIsNone(labels)
        self.assertFalse(rec["proof"]["ok"])
        self.assertIn("markers_found", rec["proof"]["failures"])
        self.assertEqual(judge.load_stats(self.environ, "score"), {"count": 1, "successful": 0, "failed": 1})

    def test_a_spent_budget_refuses_the_batched_call_before_it_launches(self):
        claude_run.eval_root(self.environ).mkdir(parents=True)
        judge.save_stats(self.environ, {"count": 9, "successful": 9, "failed": 0}, "score")
        path = claude_run.eval_root(self.environ) / "score-calls.json"
        before = path.read_text(encoding="utf-8")
        with self.assertRaises(judge.JudgeBudgetError):
            self.label_session()
        self.assertEqual(self.calls, [])
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_a_batch_with_repeated_or_no_probe_ids_is_refused_before_any_call_is_counted(self):
        cfg = claude_run.load_config(self.config)
        twice = batch_of(judge.PROBE_CASES[0], judge.PROBE_CASES[0])
        for case in (twice, batch_of()):
            with self.assertRaises(ValueError):
                judge.label_probe(cfg, case, self.environ, phase="evaluation", counter="score", max_calls=9)
        self.assertEqual(self.calls, [])
        self.assertEqual(judge.load_stats(self.environ, "score"), {"count": 0, "successful": 0, "failed": 0})

    def test_the_check_has_an_abstention_gold_case_and_accepts_abstained_or_correct_for_it(self):
        case = next(c for c in judge.PROBE_CASES if c["gold"] == judge.ABSTENTION_GOLD)
        self.assertEqual(len(judge.PROBE_CASES), 7)
        for label in ("abstained", "correct"):
            self.expect[case["id"]] = label
            code, out = self.run_check()
            self.assertEqual((code, out.strip()), (0, f"judge-check: backend=claude model={JUDGE_MODEL} isolation=PASS calls 2 probe 7/7 items 5/5"))
        self.expect[case["id"]] = "incorrect"  # a fabricated answer to an abstention probe is not accepted
        code, out = self.run_check()
        self.assertEqual((code, out.strip()), (1, f"judge-check: FAIL backend=claude model={JUDGE_MODEL} isolation=PASS calls 2 probe 6/7 items 5/5"))

    def test_no_internal_task_code_names_the_runner_or_the_check_batch(self):
        self.assertNotEqual(BATCH_ID, "C1")  # C1 is a control's name in claude_run
        source = Path(judge.__file__).read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"P\d-T\d", source))
        self.assertIn("claude_run.run_claude", judge.run_judge_call.__doc__)

    def test_prompt_hash_changes_with_the_prompt_or_the_schema_file(self):
        original = judge.PROMPTS
        self.addCleanup(setattr, judge, "PROMPTS", original)
        before = {k: judge.prompt_hash(k) for k in ("probe", "items")}
        self.assertEqual(len(set(before.values())), 2)
        for name, kind in (("judge-probe.md", "probe"), ("judge-probe.schema.json", "probe"), ("judge-items.md", "items")):
            copy_dir = self.tmp / ("p-" + name)
            shutil.copytree(original, copy_dir)
            (copy_dir / name).write_text((original / name).read_text(encoding="utf-8") + "\n ", encoding="utf-8")
            judge.PROMPTS = copy_dir
            after = {k: judge.prompt_hash(k) for k in ("probe", "items")}
            other = "items" if kind == "probe" else "probe"
            self.assertNotEqual(after[kind], before[kind], name)
            self.assertEqual(after[other], before[other], name)  # the other judge's files do not move its hash
        judge.PROMPTS = original

    def test_wrong_labels_report_a_failing_line(self):
        self.expect["P1"] = "incorrect"
        code, out = self.run_check()
        self.assertEqual(code, 1)
        self.assertEqual(out.strip(), f"judge-check: FAIL backend=claude model={JUDGE_MODEL} isolation=PASS calls 2 probe 6/7 items 5/5")

    def test_budget_is_shared_persisted_and_fails_closed(self):
        claude_run.eval_root(self.environ).mkdir(parents=True)
        judge.save_stats(self.environ, {"count": 14, "successful": 0, "throttled": 0})
        code, out = self.run_check()
        self.assertEqual(code, 1)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("judge call budget spent", out)
        (claude_run.eval_root(self.environ) / "judge-calls.json").write_text("junk", encoding="utf-8")
        self.calls.clear()
        code, out = self.run_check()
        self.assertEqual((code, self.calls), (1, []))
        self.assertIn("judge call file unreadable", out)

    def test_the_judge_check_spends_from_the_judge_phase_and_scoring_can_name_another(self):
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.assertEqual({c["phase"] for c in self.calls}, {"judge"})
        self.calls.clear()
        cfg = claude_run.load_config(self.config)
        for fn, case in ((judge.label_probe, ONE), (judge.label_items, judge.ITEMS_CASE)):
            fn(cfg, case, self.environ, phase="evaluation", counter="score", max_calls=10)
        self.assertEqual([c["phase"] for c in self.calls], ["evaluation", "evaluation"])
        self.assertEqual(claude_run.ledger_of("judge"), "control")  # the default still draws on the control budget
        self.assertEqual(claude_run.ledger_of("evaluation"), "evaluation")

    def test_scoring_has_its_own_call_counter_and_cap(self):
        cfg = claude_run.load_config(self.config)
        judge.save_stats(self.environ, {"count": 14, "successful": 0, "failed": 0})  # the judge check is nearly spent (cap 15)
        for _ in range(2):
            labels, rec = judge.label_probe(cfg, ONE, self.environ, phase="evaluation", counter="score", max_calls=2)
            self.assertEqual(labels, {"P1": "correct"})
        with self.assertRaises(judge.JudgeBudgetError):
            judge.label_probe(cfg, ONE, self.environ, phase="evaluation", counter="score", max_calls=2)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(judge.load_stats(self.environ, "score"), {"count": 2, "successful": 2, "failed": 0})
        self.assertEqual(judge.load_stats(self.environ), {"count": 14, "successful": 0, "failed": 0})  # untouched
        root = claude_run.eval_root(self.environ)
        self.assertTrue((root / "score-calls.json").is_file())
        # the other way round: a spent scoring counter does not stop the judge check
        judge.save_stats(self.environ, {"count": 0, "successful": 0, "failed": 0})
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.assertEqual(judge.load_stats(self.environ, "score")["count"], 2)

    def test_a_failed_proof_under_the_score_counter_is_counted_there(self):
        self.leak = True
        cfg = claude_run.load_config(self.config)
        labels, rec = judge.label_probe(cfg, ONE, self.environ, phase="evaluation", counter="score", max_calls=5)
        self.assertIsNone(labels)
        self.assertEqual(judge.load_stats(self.environ, "score"), {"count": 1, "successful": 0, "failed": 1})
        self.assertFalse((claude_run.eval_root(self.environ) / "judge-calls.json").exists())

    def test_claude_config_dir_is_refused(self):
        self.environ["CLAUDE_CONFIG_DIR"] = str(self.tmp / "cfg")
        code, out = self.run_check()
        self.assertEqual((code, self.calls), (3, []))
        self.assertIn("unset CLAUDE_CONFIG_DIR", out)

    def test_real_argv_for_a_judge_call_is_a_probe_with_the_judge_model(self):
        cfg = claude_run.load_config(self.config)
        argv = claude_run.build_argv(cfg, "p", session_id="x", probe=True, model=JUDGE_MODEL)
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertIn("--strict-mcp-config", argv)
        self.assertEqual(argv[argv.index("--setting-sources") + 1], "project")
        self.assertEqual(argv[argv.index("--model") + 1], JUDGE_MODEL)
        self.assertEqual(argv[argv.index("--max-budget-usd") + 1], "0.75")
        self.assertNotIn("--plugin-dir", argv)

    def test_fenced_json_is_unwrapped_but_other_text_is_rejected(self):
        self.assertEqual(judge.extract_json_text("```json\n{\"a\": 1}\n```"), "{\"a\": 1}")
        self.assertEqual(judge.extract_json_text("not fenced"), "not fenced")
        self.assertIsNone(judge.extract_json_text(None))


class UnitTests(unittest.TestCase):
    def test_validator_covers_the_keywords_we_use(self):
        envelope, element = judge.probe_schemas()
        good = {"id": "P1", "label": "correct", "quote": ""}
        self.assertEqual(judge.validate(good, element), [])
        self.assertEqual(judge.validate({"labels": [good]}, envelope), [])
        self.assertTrue(judge.validate(dict(good, label="x"), element))
        self.assertTrue(judge.validate({"id": "P1", "label": "correct"}, element))
        self.assertTrue(judge.validate(dict(good, why="r"), element))
        self.assertTrue(judge.validate(dict(good, quote=3), element))
        self.assertTrue(judge.validate(dict(good, id=1), element))
        self.assertTrue(judge.validate({"labels": {}}, envelope))
        self.assertTrue(judge.validate({"labels": [], "extra": 1}, envelope))
        items = json.loads((judge.PROMPTS / "judge-items.schema.json").read_text(encoding="utf-8"))
        good = {"lines": [{"line": 1, "kind": "needed", "item_id": "N1", "placed_ok": True, "quote": ""}]}
        self.assertEqual(judge.validate(good, items), [])
        good["lines"][0]["line"] = True
        self.assertTrue(judge.validate(good, items))

    def test_the_probe_schema_is_the_envelope_around_todays_label_and_quote_checks(self):
        envelope, element = judge.probe_schemas()
        self.assertEqual(element["properties"]["label"], {"type": "string", "enum": ["correct", "incorrect", "abstained"]})
        self.assertEqual(element["properties"]["quote"], {"type": "string"})
        self.assertEqual((element["required"], element["additionalProperties"]), (["id", "label", "quote"], False))
        self.assertEqual(envelope["properties"]["labels"], {"type": "array"})
        self.assertEqual((envelope["required"], envelope["additionalProperties"]), (["labels"], False))

    def test_the_batched_prompt_has_each_id_once_the_three_definitions_verbatim_and_no_question_list(self):
        prompt = judge.probe_prompt(judge.PROBE_BATCH)
        for case in judge.PROBE_CASES:
            self.assertEqual(prompt.count(f"Answer id: {case['id']}\n"), 1, case["id"])
            self.assertIn(case["answer"], prompt)
        self.assertEqual(prompt.count("Case id: " + BATCH_ID + "\n"), 1)
        for line in ("- correct: the answer states the gold answer (the wording may differ) and does not present a stale answer as current.",
                     "- incorrect: the answer states a different answer, or presents a stale answer as current.",
                     "- abstained: the answer says it does not know or cannot say, and gives no answer."):
            self.assertIn("\n" + line + "\n", prompt)
        self.assertNotIn("Questions:", prompt)
        self.assertIn("(none)", prompt)  # a probe with no stale answers
        self.assertIn("- 8080", prompt)

    def test_no_codex_code_is_left(self):
        for name in ("run_codex", "codex_markers", "find_rollout", "classify", "build_argv"):
            self.assertFalse(hasattr(judge, name), name)
        cfg = claude_run.load_config()
        self.assertEqual(sorted(cfg["judge"]), ["backend", "cross_family_attempt", "max_calls", "model"])
        self.assertNotIn("codex", judge.__doc__.split("A cross-family")[0].lower())


if __name__ == "__main__":
    unittest.main()
