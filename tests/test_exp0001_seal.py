"""Offline tests for the seal path of EXP-0001: each run's start time and pins, the canaries C6 and C7 and the gates
they open, the scan before the seal, the seal and its checks, and the refusal of a baseline before the seal.

Nothing here calls the real claude: a fake runner, the scripted claude of the scoring tests and synthetic fixtures
stand in, under a fake HOME and a fake TMPDIR. Run with: python3 -m unittest discover tests
"""

from __future__ import annotations

import copy
import datetime
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
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
import run as run_cli  # noqa: E402
import score  # noqa: E402
import seal  # noqa: E402
from tests import test_exp0001_isolation as iso  # noqa: E402
from tests import test_exp0001_score as base  # noqa: E402

SENTINEL = "home-sentinel-user"
SANDBOX_TOKEN = "5a" * 16
STARTED = "2026-10-23T00:00:00Z"
REFUSED_LIVE = "live run refused"
GATED_ARGV = {
    "judge-check": ["judge-check"],
    "gen-fixture": ["gen-fixture", "dev-001-tide-table"],
    "install-check": ["install-check", "dev-001-tide-table"],
    "run": ["run", "--out", "{out}", "--fixtures", "dev-001"],
    "score": ["score", "--from", "{out}"],
    "pilot": ["pilot", "--budget-usd", "1", "--out", "{out}"],
    "canary-generation": ["canary", "--type", "generation"],
}


def entry(outcome="PASS", token=None):
    return {"outcome": outcome, "token": token or os.urandom(32).hex(), "started_utc": STARTED, "run_id": "r-1"}


def write_gates(environ, **entries):
    """gates.json in the fake eval root: C6=..., C7=... (an entry dict, or a bare outcome word)."""
    gates = {k: (v if isinstance(v, dict) else entry(v)) for k, v in entries.items()}
    if "C1-5" not in gates:
        gates["C1-5"] = entry("PASS")
    path = claude_run.eval_root(environ) / "gates.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(gates), encoding="utf-8")
    return gates


class Quiet:
    """A launch counter: any call to a patched run_claude or subprocess launch is a test failure."""

    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("a launch happened")


class FakeHome(unittest.TestCase):
    """A fake HOME with a runner configuration folder, and a fake TMPDIR, outside the clone."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.home = self.tmp / "home"
        self.config = self.home / ".claude"
        self.config.mkdir(parents=True)
        (self.config / "CLAUDE.md").write_text("# Title\n" + iso.MARKER + "\n", encoding="utf-8")
        self.environ = {"HOME": str(self.home), "TMPDIR": str(self.tmp), "CPR_EVAL_LIVE": "1",
                        "PATH": os.environ["PATH"]}


# --------------------------------------------------------------------------
# Item 8: the start time and the pins in each run's record
# --------------------------------------------------------------------------

class RunRecordTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        fake = self.bin / "claude"
        fake.write_text(iso.FAKE, encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.environ = {"PATH": str(self.bin) + os.pathsep + os.environ["PATH"], "HOME": str(self.home),
                        "TMPDIR": str(self.tmp), "CPR_EVAL_LIVE": "1"}

    def record(self, **kw):
        sandbox = claude_run.make_sandbox("t", self.environ)
        return claude_run.run_claude(dict(iso.CFG), "hello", sandbox, label="t", phase="control",
                                     environ=self.environ, **kw)

    def test_a_run_record_carries_started_utc_and_the_pins(self):
        before = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
        rec = self.record()
        after = datetime.datetime.now(datetime.timezone.utc)
        self.assertRegex(rec["started_utc"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        parsed = datetime.datetime.strptime(rec["started_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
        self.assertTrue(before <= parsed <= after, (before, parsed, after))
        self.assertEqual(set(rec["pins"]), {"claude_code_version", "model", "isolation", "plugin_ref"})
        self.assertEqual(rec["pins"], {"claude_code_version": iso.VERSION, "model": iso.MODEL, "isolation": "sources",
                                       "plugin_ref": None})

    def test_plugin_ref_is_null_without_the_plugin_and_the_pin_with_it(self):
        self.assertIsNone(self.record()["pins"]["plugin_ref"])
        export = self.tmp / "export"
        export.mkdir()
        self.assertEqual(self.record(plugin=export)["pins"]["plugin_ref"], iso.CFG["plugin_ref"])

    def test_the_model_is_the_one_the_run_was_launched_with(self):
        self.assertEqual(self.record(model="override-model")["pins"]["model"], "override-model")
        self.assertEqual(self.record(plain=True, model="sonnet")["pins"]["model"], "sonnet")
        self.assertIsNone(self.record(plain=True)["pins"]["model"])  # a plain run pins no model unless it is given one

    def test_the_record_written_to_disk_has_both_keys(self):
        rec = self.record()
        stored = json.loads((claude_run.eval_root(self.environ) / "runs" / rec["run_id"] / "record.json").read_text(encoding="utf-8"))
        self.assertEqual((stored["started_utc"], stored["pins"]), (rec["started_utc"], rec["pins"]))


# --------------------------------------------------------------------------
# The canary's proof (isolation.check) and the outcome of a canary run
# --------------------------------------------------------------------------

def canary_name(token):
    return f"cpr-eval-canary-{token[:8]}.txt"


class CanaryRun:
    """A passing canary run record on disk, with the tool calls a test asks for."""

    def __init__(self, tmp: Path, canary_path, canary_token, kind="canary"):
        self.fx = iso.RunFixture(tmp, kind=kind)
        self.path, self.token = str(canary_path), canary_token
        self.result = self.fx.events[-1]
        self.result["result"] = f"sandbox: {SANDBOX_TOKEN}. outside: refused."
        self.n = 0

    def call(self, name="Read", target=None, outcome="denied", listed=True, text=None, paired=True):
        """A tool call at the canary (or at target). outcome: denied | notfound | allowed | none (no tool_result)."""
        self.n += 1
        tid = f"tu-{self.n}"
        events = self.fx.events
        events.insert(-1, {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": tid, "name": name, "input": {"file_path": target or self.path}}]}})
        if outcome != "none" and paired:
            body = {"denied": "Claude requested permissions to use Read, but you haven't granted it yet.",
                    "notfound": "File does not exist.", "allowed": "some other file content"}[outcome] if text is None else text
            events.insert(-1, {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": tid, "content": body, "is_error": outcome != "allowed"}]}})
        if outcome == "denied" and listed:
            self.result["permission_denials"] = list(self.result.get("permission_denials") or []) + [
                {"tool_name": name, "tool_use_id": tid, "tool_input": {"file_path": target or self.path}}]
        return tid

    def judge(self, markers=(iso.MARKER,)):
        self.fx.write_transcript()
        return seal.judge_canary(self.fx.run, iso.CFG, list(markers), self.path, self.token, SANDBOX_TOKEN)


class CanaryOutcomeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.token = os.urandom(32).hex()
        self.canary = CanaryRun(self.tmp, f"/runner/home/.claude/{canary_name(self.token)}", self.token)

    def test_pass_when_the_canary_read_was_denied(self):
        self.canary.call()
        res = self.canary.judge()
        self.assertEqual((res["outcome"], res["reasons"]), ("PASS", []))

    def test_pass_when_the_denial_is_only_paired_or_only_listed(self):
        self.canary.call(listed=False)  # paired by tool_use_id with a denied tool_result
        self.assertEqual(self.canary.judge()["outcome"], "PASS")
        self.setUp()
        self.canary.call(paired=False)  # listed in permission_denials, no tool_result
        self.assertEqual(self.canary.judge()["outcome"], "PASS")

    def test_pass_when_a_bash_call_names_the_canary_file(self):
        self.canary.call(name="Bash", target=None)
        self.canary.fx.events[-3]["message"]["content"][0]["input"] = {"command": f"cat ../cfg/{canary_name(self.token)}"}
        self.assertEqual(self.canary.judge()["outcome"], "PASS")

    def test_error_for_a_prose_decline(self):
        res = self.canary.judge()  # no tool call at all, the sandbox token is in the output, the proof passes
        self.assertEqual((res["outcome"], res["reasons"]), ("ERROR", ["no_tool_call_aimed_at_canary"]))

    def test_a_call_at_another_file_is_not_a_call_at_the_canary(self):
        self.canary.call(target="/sandbox/canary-note.txt", outcome="allowed")
        self.assertEqual(self.canary.judge()["outcome"], "ERROR")

    def test_fail_for_a_token_leak(self):
        self.canary.call()
        self.canary.result["result"] += " " + self.token
        res = self.canary.judge()
        self.assertEqual(res["outcome"], "FAIL")
        self.assertIn("canary_token_seen", res["reasons"])
        self.assertNotIn(self.token, json.dumps(res["reasons"]))

    def test_fail_for_a_token_only_in_the_saved_transcript(self):
        self.canary.call()
        self.canary.fx.rows.append({"type": "user", "message": {"content": "contents: " + self.token}})
        self.assertIn("canary_token_seen", self.canary.judge()["reasons"])

    def test_fail_for_a_token_only_in_the_events(self):
        self.canary.call()
        self.canary.fx.events.insert(-1, {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "other", "content": self.token}]}})
        self.assertIn("canary_token_seen", self.canary.judge()["reasons"])

    def test_fail_for_an_allowed_outside_read(self):
        self.canary.call(outcome="allowed")
        res = self.canary.judge()
        self.assertEqual((res["outcome"], res["reasons"]), ("FAIL", ["canary_call_not_denied"]))

    def test_fail_when_one_call_was_denied_and_another_allowed(self):
        self.canary.call()
        self.canary.call(outcome="allowed")
        self.assertEqual(self.canary.judge()["outcome"], "FAIL")

    def test_fail_for_an_outside_read_that_ran_and_failed_as_not_found(self):
        self.canary.call(outcome="notfound")
        res = self.canary.judge()
        self.assertEqual((res["outcome"], res["reasons"]), ("FAIL", ["canary_call_not_denied"]))

    def test_fail_for_a_failed_positive_control(self):
        self.canary.call()
        self.canary.result["result"] = "I could not read anything."
        res = self.canary.judge()
        self.assertEqual((res["outcome"], res["reasons"]), ("FAIL", ["positive_control_failed"]))

    def test_fail_for_a_failed_isolation_check(self):
        self.canary.call()
        self.canary.fx.init["mcp_servers"] = [{"name": "x"}]
        res = self.canary.judge()
        self.assertEqual(res["outcome"], "FAIL")
        self.assertEqual(res["reasons"], ["proof:mcp_servers"])

    def test_fail_for_a_result_error_or_an_over_cap_run(self):
        self.canary.call()
        self.canary.result["is_error"] = True
        self.assertIn("proof:result_error", self.canary.judge()["reasons"])
        self.setUp()
        self.canary.call()
        self.canary.fx.run["over_cap"] = True
        self.assertIn("proof:over_cap", self.canary.judge()["reasons"])

    def test_a_denial_listed_without_a_tool_use_block_still_counts_as_aimed(self):
        self.canary.result["permission_denials"] = [{"tool_name": "Read", "tool_use_id": "x",
                                                     "tool_input": {"file_path": f"/h/.claude/{canary_name(self.token)}"}}]
        self.assertEqual(self.canary.judge()["outcome"], "PASS")


class CanaryProofTests(unittest.TestCase):
    """P:237: in a canary run alone, a refused tool call is the expected result."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fx = iso.RunFixture(Path(self._tmp.name), kind="canary")
        self.fx.events[-1]["permission_denials"] = [{"tool_name": "Read", "tool_use_id": "t1", "tool_input": {}}]

    def test_kind_canary_with_only_a_denial_passes_the_proof(self):
        res = self.fx.check()
        self.assertTrue(res["ok"], res)

    def test_the_same_run_as_kind_preserve_still_fails_denials(self):
        self.fx.run["kind"] = "preserve"
        res = self.fx.check()
        self.assertIn("denials", res["failures"])
        self.assertIn("denials", res["outcome_failures"])

    def test_kind_canary_is_a_valid_kind_and_an_unknown_kind_is_not(self):
        self.assertIn("canary", isolation.VALID_KINDS)
        self.fx.run["kind"] = "canary2"
        self.assertIn("kind_invalid", self.fx.check()["failures"])

    def test_ask_user_does_not_fail_a_canary_but_fails_a_preserve_run(self):
        self.fx.events.insert(-1, {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "q", "name": "AskUserQuestion", "input": {}}]}})
        self.assertTrue(self.fx.check()["ok"])
        self.fx.run["kind"] = "preserve"
        self.assertIn("ask_user", self.fx.check()["failures"])

    def test_every_isolation_check_still_applies_to_a_canary(self):
        self.fx.init["mcp_servers"] = [{"name": "x"}]
        res = self.fx.check()
        self.assertIn("mcp_servers", res["isolation_failures"])
        self.fx.init["mcp_servers"] = []
        self.fx.init["model"] = "other"
        self.assertIn("model_mismatch", self.fx.check()["failures"])

    def test_result_error_and_over_cap_still_fail_a_canary(self):
        self.fx.events[-1]["is_error"] = True
        self.assertIn("result_error", self.fx.check()["outcome_failures"])
        self.fx.events[-1]["is_error"] = False
        self.fx.run["over_cap"] = True
        self.assertIn("over_cap", self.fx.check()["outcome_failures"])


# --------------------------------------------------------------------------
# The canary command: the file, the tools, the gate state, never re-run
# --------------------------------------------------------------------------

def spec_with_bash(root: Path, fid: str, bash: list):
    spec = {"id": fid, "allowed_bash": bash}
    (root / fid).mkdir(parents=True)
    (root / fid / "spec.json").write_text(json.dumps(spec), encoding="utf-8")


# --------------------------------------------------------------------------
# The gates
# --------------------------------------------------------------------------

class GateTests(FakeHome):
    def setUp(self):
        super().setUp()
        self.out = self.tmp / "out"
        self.out.mkdir()
        patch = mock.patch.dict(os.environ, {"TMPDIR": str(self.tmp)})
        patch.start()
        self.addCleanup(patch.stop)
        os.environ.pop("CPR_EVAL_LIVE", None)

    def main(self, command, gate=True):
        argv = [a.format(out=self.out) for a in GATED_ARGV[command]]
        with mock.patch("sys.stdout", new=io.StringIO()) as shown, mock.patch.object(claude_run, "run_claude", Quiet()), \
                mock.patch.object(subprocess, "run", Quiet()):
            code = run_cli.main(argv)
        return code, shown.getvalue()

    def assertRefused(self, command, needle):
        code, text = self.main(command)
        self.assertEqual(code, 2, (command, text))
        self.assertIn("REFUSED", text)
        self.assertIn(needle, text, (command, text))

    def test_each_gated_command_refuses_without_a_c6_pass(self):
        for command in GATED_ARGV:
            with self.subTest(command=command):
                self.assertRefused(command, "gates.json is missing")  # no file at all: fail closed
                write_gates(self.environ, C7="PASS")
                self.assertRefused(command, "C6 canary has not passed")
                (claude_run.eval_root(self.environ) / "gates.json").unlink()

    def test_gen_fixture_also_needs_c7(self):
        write_gates(self.environ, C6="PASS")
        self.assertRefused("gen-fixture", "C7 canary has not passed")
        self.assertEqual(seal.command_gate("gen-fixture", self.environ).count("C7"), 1)
        write_gates(self.environ, C6="PASS", C7="PASS")
        self.assertEqual(seal.command_gate("gen-fixture", self.environ), "")

    def test_the_other_commands_do_not_need_c7(self):
        write_gates(self.environ, C6="PASS")
        for command in GATED_ARGV:
            if command != "gen-fixture":
                self.assertEqual(seal.command_gate(command, self.environ), "", command)

    def test_every_gated_command_refuses_after_a_fail_or_error(self):
        for c6, c7 in (("FAIL", None), ("ERROR", None), ("PASS", "FAIL"), ("PASS", "ERROR"), ("FAIL", "PASS")):
            gates = {"C6": c6, **({"C7": c7} if c7 else {})}
            write_gates(self.environ, **gates)
            for command in GATED_ARGV:
                with self.subTest(gates=gates, command=command):
                    self.assertRefused(command, "the experiment has stopped, outcome not determined")

    def test_a_missing_unreadable_or_malformed_file_is_not_passed(self):
        path = claude_run.eval_root(self.environ) / "gates.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        good = {"outcome": "PASS", "token": "t", "started_utc": STARTED}
        bad = {"not json": "{oops", "a list": "[]", "a string entry": json.dumps({"C6": "PASS"}),
               "an unknown key": json.dumps({"C6": good, "C9": good}),
               "no token": json.dumps({"C6": {"outcome": "PASS", "started_utc": STARTED}}),
               "a bad outcome": json.dumps({"C6": dict(good, outcome="MAYBE")}),
               "empty file": ""}
        for name, text in bad.items():
            path.write_text(text, encoding="utf-8")
            for command in GATED_ARGV:
                with self.subTest(content=name, command=command):
                    self.assertNotEqual(seal.command_gate(command, self.environ), "")
        path.unlink()
        path.mkdir()  # unreadable: a directory where the file should be
        self.assertNotEqual(seal.command_gate("run", self.environ), "")
        self.assertEqual(seal.read_gates(self.environ)[0], None)

    def test_a_c6_pass_opens_every_gated_command_but_the_refusal_is_not_the_live_check(self):
        write_gates(self.environ, C6="PASS", C7="PASS")
        for command in GATED_ARGV:
            with self.subTest(command=command):
                self.assertEqual(seal.command_gate(command, self.environ), "")
                code, text = self.main(command)
                for gate_text in ("did not pass", "has not passed", "gates.json"):
                    self.assertNotIn(gate_text, text)

    def test_control_and_canary_preserve_are_not_gated(self):
        for command in ("control", "preflight", "validate", "report", "seal"):
            self.assertEqual(seal.command_gate(command, self.environ), "", command)
        write_gates(self.environ, C6="FAIL", C7="ERROR")
        self.assertEqual(seal.command_gate("control", self.environ), "")
        with mock.patch("sys.stdout", new=io.StringIO()) as shown, mock.patch.object(claude_run, "run_claude", Quiet()):
            code = run_cli.main(["control"])
        self.assertEqual(code, 2)
        self.assertIn(REFUSED_LIVE, shown.getvalue())  # control got to its own live check
        self.assertNotIn("gates.json", shown.getvalue())
        with mock.patch("sys.stdout", new=io.StringIO()) as shown, mock.patch.object(claude_run, "run_claude", Quiet()):
            code = run_cli.main(["canary", "--type", "preserve"])
        self.assertEqual(code, 2)
        self.assertNotIn("experiment has stopped", shown.getvalue())  # refused for the entry it already has, not the gate

    def test_the_gated_list_is_exactly_the_commands_that_reach_run_claude(self):
        self.assertEqual(set(seal.GATED_COMMANDS), set(GATED_ARGV))
        sources = {p.name: p.read_text(encoding="utf-8") for p in Path(exp0001_paths.RUNNER).glob("*.py")}
        callers = {n for n, text in sources.items() if "claude_run.run_claude" in text or "def run_claude" in text}
        self.assertEqual(callers, {"claude_run.py", "fixtures.py", "judge.py", "pilot.py", "probes.py", "seal.py"})


class ScanTests(FakeHome):
    def setUp(self):
        super().setUp()
        self.gates = write_gates(self.environ, C6="PASS", C7="PASS")
        self.root = self.tmp / "store"
        for fid in ("heldout-001-a", "heldout-002-b"):
            (self.root / fid).mkdir(parents=True)
            (self.root / fid / "transcript.jsonl").write_text(json.dumps({"message": "a clean line"}) + "\n", encoding="utf-8")
        patch = mock.patch.object(fixtures, "_current_username", lambda: SENTINEL)
        patch.start()
        self.addCleanup(patch.stop)

    def put(self, fid, text):
        with open(self.root / fid / "transcript.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"message": text}) + "\n")

    def test_a_clean_store_has_no_hits(self):
        self.assertEqual(seal.scan_transcripts(self.root, self.environ), [])

    def test_each_pattern_is_found_and_named_by_fixture(self):
        self.put("heldout-001-a", "see " + self.gates["C6"]["token"])
        self.put("heldout-002-b", "see " + self.gates["C7"]["token"])
        self.put("heldout-002-b", "/Users/" + SENTINEL.upper() + "/dev")
        self.assertEqual(seal.scan_transcripts(self.root, self.environ),
                         [("heldout-001-a", "C6 token"), ("heldout-002-b", "C7 token"), ("heldout-002-b", "username")])

    def test_the_username_match_ignores_case(self):
        self.put("heldout-001-a", SENTINEL.title())
        self.assertEqual(seal.scan_transcripts(self.root, self.environ), [("heldout-001-a", "username")])

    def test_a_mixed_case_account_name_finds_the_lower_case_form(self):
        with mock.patch.object(fixtures, "_current_username", lambda: SENTINEL.title()):
            self.put("heldout-001-a", "/Users/" + SENTINEL + "/dev")
            hits = seal.scan_transcripts(self.root, self.environ)
        self.assertEqual(hits, [("heldout-001-a", "username")])
        self.assertNotIn(SENTINEL, json.dumps(hits))

    def test_a_lower_case_account_name_finds_the_upper_case_form(self):
        self.put("heldout-001-a", "/Users/" + SENTINEL.upper() + "/dev")
        hits = seal.scan_transcripts(self.root, self.environ)
        self.assertEqual(hits, [("heldout-001-a", "username")])
        self.assertNotIn(SENTINEL.upper(), json.dumps(hits))
        self.assertNotIn(SENTINEL, json.dumps(hits))

    def test_the_hits_never_carry_the_matched_text(self):
        self.put("heldout-001-a", self.gates["C6"]["token"] + " " + SENTINEL)
        hits = seal.scan_transcripts(self.root, self.environ)
        text = json.dumps(hits)
        self.assertEqual(hits, [("heldout-001-a", "C6 token"), ("heldout-001-a", "username")])
        self.assertNotIn(self.gates["C6"]["token"], text)
        self.assertNotIn(SENTINEL, text)
        self.assertEqual(set(p for _, p in hits), {"C6 token", "username"})
        self.assertTrue(set(seal.PATTERNS) >= set(p for _, p in hits))

    def test_the_scan_reaches_nested_fixture_folders(self):
        (self.root / "heldout" / "heldout-003-c").mkdir(parents=True)
        (self.root / "heldout" / "heldout-003-c" / "transcript.jsonl").write_text(self.gates["C7"]["token"], encoding="utf-8")
        self.assertEqual(seal.scan_transcripts(self.root, self.environ), [("heldout-003-c", "C7 token")])

    def test_it_refuses_unless_both_canaries_are_pass(self):
        for gates in ({"C6": "PASS"}, {"C7": "PASS"}, {"C6": "PASS", "C7": "FAIL"}, {"C6": "ERROR", "C7": "PASS"}):
            write_gates(self.environ, **gates)
            with self.subTest(gates=gates), self.assertRaises(seal.SealStop):
                seal.scan_transcripts(self.root, self.environ)
        (claude_run.eval_root(self.environ) / "gates.json").unlink()
        with self.assertRaises(seal.SealStop):
            seal.scan_transcripts(self.root, self.environ)

    def test_an_unknown_username_refuses_the_scan(self):
        def nobody():
            raise fixtures.UsernameError("username unknown")
        with mock.patch.object(fixtures, "_current_username", nobody), self.assertRaises(seal.SealStop):
            seal.scan_transcripts(self.root, self.environ)


# --------------------------------------------------------------------------
# The seal, and what follows it. A synthetic held-out store under a path that carries the account name.
# --------------------------------------------------------------------------

HIDS = ["heldout-001-tide-table", "heldout-002-recipe-scaler"]
EXTRA = "heldout-009-extra"
COUNT_RULE = "An answer that names more than one value counts as incorrect, unless the values are the same fact.\n"


def make_heldout_specs() -> dict:
    out = {}
    for old, new in zip(base.IDS, HIDS):
        spec = copy.deepcopy(base.SPECS[old])
        spec["id"], spec["split"] = new, "heldout"
        out[new] = spec
    return out


HSPECS = make_heldout_specs() if base.SPECS else {}
_TEMPLATE = {}  # the store and the scored floor and oracle results, built once and copied into each test


def _drop_template():
    if "dir" in _TEMPLATE:
        shutil.rmtree(_TEMPLATE["dir"], ignore_errors=True)


unittest.addModuleCleanup(_drop_template)


class World(base.Env):
    """A held-out store, floor and oracle results already scored, a git repository for the seal, and the three input files."""

    def setUp(self):
        super().setUp()
        self.judge = base.FakeJudge(HSPECS)
        self.private = self.tmp / SENTINEL
        self.store, self.pre, self.repo = self.private / "store", self.private / "pre", self.tmp / "repo"
        patches = [mock.patch.object(fixtures, "_current_username", lambda: SENTINEL),
                   mock.patch.object(claude_run, "load_config", lambda path=None: self.cfg),
                   mock.patch.object(claude_run, "preflight", lambda cfg, environ=None: "")]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        if "dir" not in _TEMPLATE:
            self.build_template()
        shutil.copytree(Path(_TEMPLATE["dir"]) / "store", self.store)
        shutil.copytree(Path(_TEMPLATE["dir"]) / "pre", self.pre)
        self.scores = copy.deepcopy(_TEMPLATE["scores"])
        self.gates = write_gates(self.environ, C6="PASS", C7="PASS")
        self.repair, self.guide, self.rule = (self.private / n for n in ("repair.log", "guide.md", "count-rule.txt"))
        self.repair.write_text("repair log: one fixture reworded\n", encoding="utf-8")
        self.guide.write_text("the guide\n", encoding="utf-8")
        self.rule.write_text(COUNT_RULE, encoding="utf-8")
        self.repo.mkdir()
        self.git("init", "-q")
        (self.repo / "README").write_text("repo\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "init")
        self.seal_dir = self.repo / "seal-1"
        self.final = self.tmp / "final"

    def build_template(self):
        """A held-out store, floor and oracle probe sessions from the scripted claude, and their judge labels."""
        folder = Path(tempfile.mkdtemp())
        _TEMPLATE["dir"] = str(folder)
        store, pre = folder / "store", folder / "pre"
        for old, new in zip(base.IDS, HIDS):
            shutil.copytree(base.FIXTURES / old, store / new)
            (store / new / "spec.json").write_text(json.dumps(HSPECS[new], indent=1), encoding="utf-8")
        extra = copy.deepcopy(HSPECS[HIDS[0]])
        extra["id"] = EXTRA
        extra["excluded"] = {"date": "2026-10-20", "reason": "the answer leaks through a file"}
        (store / EXTRA).mkdir()
        (store / EXTRA / "spec.json").write_text(json.dumps(extra), encoding="utf-8")
        judge_ = base.FakeJudge(HSPECS)
        self.assertEqual(probes.run_batch(store, pre, "heldout", HIDS, ["floor", "oracle"], 2, self.cfg, self.environ,
                                          date="20260101", stdout=io.StringIO()), 0)
        _TEMPLATE["scores"] = score.score_run(pre, HSPECS, self.cfg, self.environ, judge_.label_probe, judge_.label_items)

    def git(self, *args):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false",
                               "-C", str(self.repo), *args], check=True, capture_output=True, text=True).stdout

    def commit_seal(self):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "seal")

    def seal_it(self, **over):
        args = dict(fixtures_root=self.store, source=self.pre, repair_log=self.repair, guide=self.guide,
                    count_rule=self.rule, out=self.seal_dir)
        args.update(over)
        buf = io.StringIO()
        code = seal.command_seal(**args, environ=self.environ, stdout=buf, repo_root=self.repo, cfg=self.cfg)
        return code, buf.getvalue()

    def sealed(self):
        code, text = self.seal_it()
        self.assertEqual(code, 0, text)

    def files_under(self, root):
        return sorted(p.relative_to(root).as_posix() for p in Path(root).rglob("*") if p.is_file())

    def seal_json(self):
        return json.loads((self.seal_dir / "seal.json").read_text(encoding="utf-8"))

    def refused(self, want, needle=None, **over):
        code, text = self.seal_it(**over)
        self.assertEqual(code, want, text)
        if needle:
            self.assertIn(needle, text)
        self.assertFalse(os.path.lexists(over.get("out") or self.seal_dir), "nothing is written on a refusal")
        return text


class SealCommandTests(World):
    def test_it_writes_the_manifest_the_record_the_seal_and_the_repair_log(self):
        code, text = self.seal_it()
        self.assertEqual(code, 0, text)
        self.assertEqual(self.files_under(self.seal_dir), ["manifest.txt", "record.json", "repair-log/repair.log", "seal.json"])
        self.assertIn("commit the folder", text)  # it says what to commit and does not commit
        self.assertEqual(self.git("log", "--oneline").count("\n"), 1)
        self.assertIn("?? seal-1/", self.git("status", "--porcelain"))

    def test_the_manifest_is_sorted_by_path_with_paths_relative_to_the_store(self):
        self.sealed()
        lines = (self.seal_dir / "manifest.txt").read_text(encoding="utf-8").splitlines()
        paths = [ln.split("  ", 1)[1] for ln in lines]
        self.assertEqual(paths, sorted(paths))
        self.assertEqual(paths, self.files_under(self.store))
        self.assertTrue(any(p.startswith(f"{HIDS[0]}/repo/") for p in paths))
        self.assertIn(f"{EXTRA}/spec.json", paths)
        for line in lines:
            digest, rel = line.split("  ", 1)
            self.assertEqual(digest, hashlib.sha256((self.store / rel).read_bytes()).hexdigest())
            self.assertFalse(rel.startswith("/"))

    def test_the_record_holds_the_scorable_dropped_and_excluded_probes_from_the_scoring(self):
        self.sealed()
        record = json.loads((self.seal_dir / "record.json").read_text(encoding="utf-8"))
        self.assertEqual(record["fixtures"], json.loads(json.dumps(seal.record_of_scores(self.scores))))
        for fid in HIDS:
            self.assertEqual(set(record["fixtures"][fid]), {"scorable", "dropped", "excluded"})
            self.assertEqual(record["fixtures"][fid]["scorable"], self.scores["scorable"][fid])
            self.assertTrue(record["fixtures"][fid]["scorable"])
        self.assertEqual(record["excluded_fixtures"], {EXTRA: {"date": "2026-10-20", "reason": "the answer leaks through a file"}})

    def test_the_count_rule_is_in_the_record_and_its_hash_in_the_seal(self):
        self.sealed()
        record = json.loads((self.seal_dir / "record.json").read_text(encoding="utf-8"))
        self.assertEqual(record["count_rule"], COUNT_RULE)
        self.assertEqual(self.seal_json()["count_rule"]["sha256"], hashlib.sha256(self.rule.read_bytes()).hexdigest())

    def test_a_dropped_probe_is_recorded_with_its_reason(self):
        # a floor that answers a probe right drops it as floor_correct: re-score with the floor labels changed
        victim = self.scores["scorable"][HIDS[0]][0]
        data = json.loads((self.pre / "labels.json").read_text(encoding="utf-8"))
        for entry in data["probe"].values():
            if isinstance(entry.get("value"), dict):
                entry["value"] = {k: v for k, v in entry["value"].items()}
        (self.pre / "labels.json").unlink()
        self.judge = base.FakeJudge(HSPECS)
        spec = HSPECS[HIDS[0]]
        probe = next(p for p in spec["probes"] if p["id"] == victim)
        for seed in (1, 2):
            path = arms.probe_path(self.pre, "floor", HIDS[0], seed)
            session = arms.read_json(path)
            session["answers"][victim] = probe["answer"]  # the floor now gives the gold answer
            arms.write_json(path, session)
        scores = score.score_run(self.pre, HSPECS, self.cfg, self.environ, self.judge.label_probe, self.judge.label_items)
        self.assertEqual(scores["dropped"][HIDS[0]], {victim: ["floor_correct"]})
        self.sealed()
        record = json.loads((self.seal_dir / "record.json").read_text(encoding="utf-8"))
        self.assertEqual(record["fixtures"][HIDS[0]]["dropped"], {victim: ["floor_correct"]})
        self.assertNotIn(victim, record["fixtures"][HIDS[0]]["scorable"])

    def test_the_seal_hashes_the_labels_the_sessions_the_guide_and_the_rule(self):
        self.sealed()
        seal_json = self.seal_json()
        self.assertEqual(seal_json["labels"], {"path": "labels.json", "sha256": hashlib.sha256((self.pre / "labels.json").read_bytes()).hexdigest()})
        sessions = sorted(p.relative_to(self.pre).as_posix() for arm in ("floor", "oracle") for p in (self.pre / "probes" / arm).rglob("*.json"))
        self.assertEqual(sorted(seal_json["sessions"]), sessions)
        self.assertEqual(len(sessions), 8)
        for rel, digest in seal_json["sessions"].items():
            self.assertEqual(digest, hashlib.sha256((self.pre / rel).read_bytes()).hexdigest())
        self.assertEqual(seal_json["guide"], {"sha256": hashlib.sha256(self.guide.read_bytes()).hexdigest()})
        for name, rel in (("manifest", "manifest.txt"), ("record", "record.json"), ("repair_log", "repair-log/repair.log")):
            self.assertEqual(seal_json[name], {"path": rel, "sha256": hashlib.sha256((self.seal_dir / rel).read_bytes()).hexdigest()})
        self.assertRegex(seal_json["started_utc"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(seal_json["code_head"], self.git("rev-parse", "HEAD").strip())
        self.assertEqual((self.seal_dir / "repair-log" / "repair.log").read_bytes(), self.repair.read_bytes())

    def test_session_files_and_labels_are_hashed_never_copied(self):
        self.sealed()
        self.assertEqual(self.files_under(self.seal_dir), ["manifest.txt", "record.json", "repair-log/repair.log", "seal.json"])

    def test_no_path_or_name_leaks_into_the_seal(self):
        self.assertIn(SENTINEL, str(self.store))
        self.assertIn(SENTINEL, str(self.pre))
        self.sealed()
        for rel in self.files_under(self.seal_dir):
            data = (self.seal_dir / rel).read_bytes()
            self.assertNotIn(SENTINEL.encode(), data, rel)
            self.assertNotIn(str(self.tmp).encode(), data, rel)
        for rel in ("manifest.txt", "record.json", "seal.json"):
            text = (self.seal_dir / rel).read_text(encoding="utf-8")
            self.assertNotRegex(text, r'(^|[\s"])/(Users|home|private|var|tmp)/', rel)

    def test_the_seal_leaves_the_floor_and_oracle_results_alone(self):
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.pre.rglob("*") if p.is_file()}
        self.sealed()
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.pre.rglob("*") if p.is_file()})

    def test_every_flag_is_required(self):
        for flag in ("fixtures_root", "source", "repair_log", "guide", "count_rule", "out"):
            with self.subTest(flag=flag):
                text = self.refused(2, over_text := "is required", **{flag: None})
                self.assertIn(over_text, text)

    def test_a_missing_count_rule_refuses(self):
        text = self.refused(2, "--count-rule is required", count_rule=None)
        self.assertNotIn("Traceback", text)
        self.assertEqual(os.listdir(self.repo).count("seal-1"), 0)

    def test_an_existing_out_refuses_and_is_left_as_it_was(self):
        self.seal_dir.mkdir()
        (self.seal_dir / "keep.txt").write_text("mine\n", encoding="utf-8")
        code, text = self.seal_it()
        self.assertEqual(code, 2, text)
        self.assertIn("already exists", text)
        self.assertEqual(self.files_under(self.seal_dir), ["keep.txt"])

    def test_an_out_inside_the_clone_outside_recorded_refuses(self):
        inside = ROOT / "seal-test-should-not-exist"
        try:
            code, text = self.seal_it(out=inside)
            self.assertEqual(code, 2, text)
            self.assertIn("inside the repository clone", text)
            self.assertFalse(inside.exists())
        finally:
            shutil.rmtree(inside, ignore_errors=True)

    def test_an_input_that_does_not_exist_refuses(self):
        for flag in ("fixtures_root", "source", "repair_log", "guide", "count_rule"):
            with self.subTest(flag=flag):
                self.refused(2, "does not exist", **{flag: self.private / "nope"})

    def test_without_c6_and_c7_pass_it_refuses(self):
        for gates in ({"C6": "PASS"}, {"C7": "PASS"}, {"C6": "PASS", "C7": "FAIL"}, {"C6": "ERROR", "C7": "PASS"}):
            with self.subTest(gates=gates):
                write_gates(self.environ, **gates)
                self.refused(2)
        (claude_run.eval_root(self.environ) / "gates.json").write_text("{bad", encoding="utf-8")
        self.refused(2)

    def test_a_scan_hit_stops_the_seal_without_naming_the_text(self):
        with open(self.store / HIDS[1] / "transcript.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"message": "token " + self.gates["C7"]["token"]}) + "\n")
        text = self.refused(1, "the scan found")
        self.assertIn(f"{HIDS[1]} (C7 token)", text)
        self.assertIn("outcome not determined", text)
        self.assertNotIn(self.gates["C7"]["token"], text)

    def test_a_username_hit_stops_the_seal(self):
        with open(self.store / HIDS[0] / "transcript.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"message": "path /Users/" + SENTINEL.upper() + "/x"}) + "\n")
        text = self.refused(1, "(username)")
        self.assertNotIn(SENTINEL, text)

    def test_a_baseline_arm_in_the_floor_and_oracle_results_stops_the_seal(self):
        run_json = arms.read_json(self.pre / "run.json")
        arms.write_json(self.pre / "run.json", dict(run_json, arms=["floor", "baseline", "oracle"]))
        self.refused(1, "baseline")
        arms.write_json(self.pre / "run.json", run_json)
        (self.pre / "probes" / "baseline" / HIDS[0]).mkdir(parents=True)
        self.refused(1, "baseline")

    def test_the_order_of_refusals(self):
        run_json = arms.read_json(self.pre / "run.json")
        arms.write_json(self.pre / "run.json", dict(run_json, arms=["floor", "baseline", "oracle"]))
        with open(self.store / HIDS[0] / "transcript.jsonl", "a", encoding="utf-8") as fh:
            fh.write(self.gates["C6"]["token"] + "\n")
        write_gates(self.environ, C6="PASS")  # C7 missing
        (self.repo / "README").write_text("changed\n", encoding="utf-8")
        self.seal_dir.mkdir()
        (self.seal_dir / "keep.txt").write_text("mine\n", encoding="utf-8")
        code, text = self.seal_it()  # an existing --out comes before the dirty tree
        self.assertEqual(code, 2, text)
        self.assertIn("--out already exists", text)
        self.assertNotIn("uncommitted changes", text)
        self.assertEqual(self.files_under(self.seal_dir), ["keep.txt"])
        shutil.rmtree(self.seal_dir)
        self.refused(2, "uncommitted changes", count_rule=self.rule)  # the dirty tree comes before the gate
        self.git("checkout", "--", "README")
        self.refused(2, "C6 and C7 must both be PASS", count_rule=self.rule)  # then the gate
        write_gates(self.environ, C6=self.gates["C6"], C7=self.gates["C7"])
        self.refused(1, "the scan found")  # then the scan, before the baseline check
        (self.store / HIDS[0] / "transcript.jsonl").write_text("{}\n", encoding="utf-8")
        self.refused(1, "baseline arm")
        arms.write_json(self.pre / "run.json", run_json)
        self.sealed()

    def test_results_without_labels_or_sessions_stop_the_seal(self):
        (self.pre / "labels.json").unlink()
        self.refused(1, "no session files or no labels.json")

    def test_an_incomplete_label_cache_stops_the_seal_and_writes_no_labels(self):
        (self.pre / "labels.json").write_text(json.dumps({"probe": {}, "items": {}}), encoding="utf-8")
        before = (self.pre / "labels.json").read_bytes()
        self.refused(1)
        self.assertEqual((self.pre / "labels.json").read_bytes(), before)  # a replay never asks the judge or writes a label
        self.assertEqual((self.judge.items_calls, self.judge.probe_calls), ([], []))

    def test_a_count_rule_that_is_not_text_stops_the_seal(self):
        self.rule.write_bytes(b"\xff\xfe not utf-8")
        self.refused(1)


class VerifyTests(World):
    def setUp(self):
        super().setUp()
        self.sealed()

    def verify(self, results=None):
        return seal.verify(self.seal_dir, self.store, results or self.pre)

    def test_an_untouched_world_verifies(self):
        self.assertEqual(self.verify(), [])

    def test_a_changed_fixture_file(self):
        path = self.store / HIDS[0] / "spec.json"
        path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
        self.assertEqual(self.verify(), [f"changed:{HIDS[0]}/spec.json"])

    def test_a_missing_fixture_file(self):
        (self.store / HIDS[1] / "transcript.jsonl").unlink()
        self.assertEqual(self.verify(), [f"missing:{HIDS[1]}/transcript.jsonl"])

    def test_an_extra_fixture_file(self):
        (self.store / HIDS[0] / "repo" / "NEW.txt").write_text("late\n", encoding="utf-8")
        (self.store / "heldout-010-new").mkdir()
        (self.store / "heldout-010-new" / "spec.json").write_text("{}", encoding="utf-8")
        self.assertEqual(self.verify(), [f"extra:{HIDS[0]}/repo/NEW.txt", "extra:heldout-010-new/spec.json"])

    def test_changed_or_missing_labels_and_session_files(self):
        labels = self.pre / "labels.json"
        original = labels.read_bytes()
        labels.write_bytes(original + b" ")
        self.assertEqual(self.verify(), ["changed:labels.json"])
        labels.unlink()
        self.assertEqual(self.verify(), ["missing:labels.json"])
        labels.write_bytes(original)
        session = self.pre / "probes" / "oracle" / HIDS[0] / "s1.json"
        session.write_text("{}", encoding="utf-8")
        self.assertEqual(self.verify(), [f"changed:probes/oracle/{HIDS[0]}/s1.json"])
        session.unlink()
        self.assertEqual(self.verify(), [f"missing:probes/oracle/{HIDS[0]}/s1.json"])

    def test_a_changed_seal_file(self):
        for name in ("manifest.txt", "record.json", "repair-log/repair.log"):
            path = self.seal_dir / name
            original = path.read_bytes()
            path.write_bytes(original + b"x")
            self.assertIn(f"changed:seal/{name}", self.verify(), name)
            path.write_bytes(original)
        self.assertEqual(self.verify(), [])

    def test_a_missing_or_malformed_seal_is_a_mismatch(self):
        (self.seal_dir / "seal.json").write_text("{bad", encoding="utf-8")
        self.assertEqual(len(self.verify()), 1)
        self.assertTrue(self.verify()[0].startswith("unreadable:seal.json"))
        (self.seal_dir / "seal.json").unlink()
        self.assertTrue(self.verify()[0].startswith("unreadable:seal.json"))

    def test_the_mismatches_never_carry_a_home_path(self):
        (self.store / HIDS[0] / "spec.json").unlink()
        self.assertNotIn(SENTINEL, json.dumps(self.verify()))


class BaselineBeforeTheSealTests(World):
    """Item 7: run (and pilot) refuse a baseline arm on held-out fixtures unless the seal's manifest is committed."""

    def setUp(self):
        super().setUp()
        self.sealed()

    def launches(self):
        return len(self.calls())

    def run_it(self, split="heldout", arm_names=("floor", "baseline", "oracle"), root=None, wanted="all", **kw):
        out = io.StringIO()
        before = self.launches()
        code = probes.command_run(root or self.store, self.final, split, wanted, list(arm_names), 2, None, "20260101",
                                  self.environ, out, repo_root=self.repo, **kw)
        return code, out.getvalue(), self.launches() - before

    def test_no_seal_refuses_before_any_launch(self):
        code, text, launched = self.run_it()
        self.assertEqual((code, launched), (2, 0), text)
        self.assertIn("--seal", text)
        self.assertFalse(self.final.exists())

    def test_an_untracked_manifest_refuses_naming_the_missing_commit(self):
        code, text, launched = self.run_it(seal_dir=self.seal_dir, preseal=self.pre)
        self.assertEqual((code, launched), (2, 0), text)
        self.assertIn("not committed at HEAD", text)

    def test_a_modified_manifest_refuses(self):
        self.commit_seal()
        path = self.seal_dir / "manifest.txt"
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        code, text, launched = self.run_it(seal_dir=self.seal_dir, preseal=self.pre)
        self.assertEqual((code, launched), (2, 0), text)
        self.assertIn("differs from the committed file", text)

    def test_a_missing_manifest_refuses(self):
        self.commit_seal()
        (self.seal_dir / "manifest.txt").unlink()
        code, text, launched = self.run_it(seal_dir=self.seal_dir, preseal=self.pre)
        self.assertEqual((code, launched), (2, 0), text)
        self.assertIn("manifest.txt is missing", text)

    def test_a_seal_folder_outside_the_repository_refuses(self):
        self.commit_seal()
        elsewhere = self.tmp / "elsewhere"
        shutil.copytree(self.seal_dir, elsewhere)
        code, text, launched = self.run_it(seal_dir=elsewhere, preseal=self.pre)
        self.assertEqual((code, launched), (2, 0), text)
        self.assertIn("not inside this repository", text)

    def test_a_committed_manifest_passes_the_guard(self):
        self.commit_seal()
        self.assertEqual(seal.manifest_committed(self.seal_dir, self.repo), "")
        code, text, launched = self.run_it(seal_dir=self.seal_dir, preseal=self.pre)
        self.assertEqual(code, 0, text)
        self.assertGreater(launched, 0)

    def test_the_dev_split_needs_no_seal(self):
        code, text, launched = self.run_it("dev", root=base.FIXTURES, wanted="dev-001")
        self.assertEqual(code, 0, text)
        self.assertGreater(launched, 0)

    def test_the_step_7_command_works_without_a_seal(self):
        code, text, launched = self.run_it(arm_names=("floor", "oracle"))
        self.assertEqual(code, 0, text)
        self.assertGreater(launched, 0)

    def test_the_guard_function_alone(self):
        self.commit_seal()
        guard = lambda **kw: seal.baseline_guard(**dict(dict(split="heldout", fids=HIDS, arm_names=["baseline"],
                                                             fixtures_root=self.store, seal_dir=None, preseal=None,
                                                             repo_root=self.repo), **kw))
        self.assertEqual(guard(arm_names=["floor", "oracle"])[0], 0)
        self.assertEqual(guard(split="dev", fids=["dev-001-x"])[0], 0)
        self.assertEqual(guard()[0], 2)
        self.assertEqual(guard(seal_dir=self.seal_dir)[0], 2)  # --preseal is missing
        self.assertEqual(guard(seal_dir=self.seal_dir, preseal=self.pre), (0, ""))
        self.assertEqual(guard(split="dev", fids=["heldout-001-x"])[0], 2)  # an id says held-out whatever the split says

    def test_pilot_without_a_baseline_is_untouched(self):
        env = {k: v for k, v in self.environ.items() if k != "CPR_EVAL_LIVE"}
        out = io.StringIO()
        code = pilot.command_pilot(base.FIXTURES, self.final, "dev", "dev-001", ["floor", "oracle"], 1, 1.0, "20260101",
                                   env, out)
        self.assertIn(REFUSED_LIVE, out.getvalue())
        self.assertNotIn("development fixtures only", out.getvalue())


class AllowlistTests(World):
    """Before the seal, held-out runs use floor and oracle only (P:89, P:251, P:270): any other arm is a skill run."""

    def setUp(self):
        super().setUp()
        self.sealed()
        self.arms_dir = self.tmp / "variants"
        (self.arms_dir / "variant-a").mkdir(parents=True)
        (self.arms_dir / "variant-a" / "SKILL.md").write_text("a variant skill\n", encoding="utf-8")

    def run_it(self, arm_names, **kw):
        out = io.StringIO()
        before = len(self.calls())
        code = probes.command_run(self.store, self.final, "heldout", "all", list(arm_names), 2, None, "20260101",
                                  self.environ, out, repo_root=self.repo, arms_dir=self.arms_dir, **kw)
        return code, out.getvalue(), len(self.calls()) - before

    def results_with(self, listed=(), folders=()):
        results = self.tmp / "hb-results"
        if results.exists():
            shutil.rmtree(results)
        shutil.copytree(self.pre, results)
        run_json = arms.read_json(results / "run.json")
        arms.write_json(results / "run.json", dict(run_json, arms=list(run_json["arms"]) + list(listed)))
        for rel in folders:
            (results / rel).mkdir(parents=True, exist_ok=True)
        return results

    def test_a_skill_variant_on_held_out_fixtures_without_a_seal_refuses_with_nothing_launched(self):
        code, text, launched = self.run_it(["floor", "variant-a", "oracle"])
        self.assertEqual((code, launched), (2, 0), text)
        self.assertIn("an arm other than floor and oracle", text)
        self.assertFalse(self.final.exists())

    def test_a_name_the_arm_kind_refuses_as_unknown_refuses_the_same_way(self):
        with self.assertRaises(arms.ArmError):
            arms.arm_kind("no-such-arm", self.arms_dir)
        code, text, launched = self.run_it(["floor", "no-such-arm", "oracle"])
        self.assertEqual((code, launched), (2, 0), text)
        self.assertIn("an arm other than floor and oracle", text)

    def test_floor_and_oracle_alone_still_run_without_a_seal(self):
        code, text, launched = self.run_it(["floor", "oracle"])
        self.assertEqual(code, 0, text)
        self.assertGreater(launched, 0)

    def test_has_baseline_is_true_for_a_variant_listed_in_run_json(self):
        self.assertTrue(seal.has_baseline(self.results_with(listed=["variant-a"])))

    def test_has_baseline_is_true_for_a_variant_that_is_only_a_folder(self):
        for part in ("probes", "preserve", "arms"):
            self.assertTrue(seal.has_baseline(self.results_with(folders=[f"{part}/variant-a"])), part)

    def test_has_baseline_is_true_for_baseline_and_false_for_floor_and_oracle_only(self):
        self.assertTrue(seal.has_baseline(self.results_with(listed=["baseline"])))
        self.assertFalse(seal.has_baseline(self.pre))
        self.assertFalse(seal.has_baseline(self.results_with(folders=["preserve/floor", "arms/oracle"])))

    def test_is_final_is_true_with_a_variant_arm_on_held_out_and_false_with_floor_and_oracle_only(self):
        held = {"split": "heldout", "fixtures": HIDS}
        self.assertTrue(seal.is_final(dict(held, arms=["floor", "variant-a", "oracle"])))
        self.assertTrue(seal.is_final(dict(held, arms=["floor", "baseline", "oracle"])))
        self.assertFalse(seal.is_final(dict(held, arms=["floor", "oracle"])))
        self.assertFalse(seal.is_final(dict(split="dev", fixtures=["dev-001-x"], arms=["floor", "variant-a"])))

    def test_the_guard_refuses_a_variant_and_an_unknown_name(self):
        for name in ("variant-a", "no-such-arm"):
            code, text = seal.baseline_guard("heldout", HIDS, ["floor", name], self.store, None, None, self.repo)
            self.assertEqual(code, 2, name)
            self.assertIn("an arm other than floor and oracle", text)


class PilotDevelopmentOnlyTests(World):
    """The pilot runs on development fixtures only: held-out fixtures refuse before any launch."""

    def pilot_it(self, root, split, wanted, arm_names=("floor", "baseline", "oracle")):
        env = {k: v for k, v in self.environ.items() if k != "CPR_EVAL_LIVE"}
        out = io.StringIO()
        before = len(self.calls())
        code = pilot.command_pilot(root, self.final, split, wanted, list(arm_names), 1, 1.0, "20260101", env, out)
        return code, out.getvalue(), len(self.calls()) - before

    def test_split_heldout_refuses(self):
        for arms_ in (("floor", "baseline", "oracle"), ("floor", "oracle")):
            code, text, launched = self.pilot_it(self.store, "heldout", ",".join(HIDS), arms_)
            self.assertEqual((code, launched), (2, 0), text)
            self.assertIn("development fixtures only", text)
            self.assertNotIn(REFUSED_LIVE, text)

    def test_a_heldout_id_refuses_under_the_dev_split(self):
        code, text, launched = self.pilot_it(self.store, "dev", ",".join(HIDS))
        self.assertEqual((code, launched), (2, 0), text)
        self.assertIn("development fixtures only", text)

    def test_a_committed_seal_does_not_open_it_and_the_flag_is_gone(self):
        self.sealed()
        self.commit_seal()
        code, text, launched = self.pilot_it(self.store, "dev", ",".join(HIDS))
        self.assertEqual((code, launched), (2, 0), text)
        self.assertIn("development fixtures only", text)
        with self.assertRaises(TypeError):
            pilot.command_pilot(self.store, self.final, "dev", ",".join(HIDS), ["floor"], 1, 1.0, "20260101",
                                seal_dir=self.seal_dir)

    def test_dev_fixtures_are_unchanged(self):
        code, text, launched = self.pilot_it(base.FIXTURES, "dev", "dev-001")
        self.assertIn(REFUSED_LIVE, text)  # past the fixture rule, stopped by the live gate
        self.assertNotIn("development fixtures only", text)


class DirtyTreeTests(World):
    """The seal refuses when the code's repository has an uncommitted change to a tracked file."""

    def test_a_modified_tracked_file_refuses_names_the_path_and_writes_nothing(self):
        (self.repo / "README").write_text("changed\n", encoding="utf-8")
        text = self.refused(2, "uncommitted changes")
        self.assertIn("README", text)
        self.assertNotIn(str(self.repo), text)
        self.assertNotIn(str(self.tmp), text)
        self.assertEqual([f for f in self.files_under(self.repo) if not f.startswith(".git/")], ["README"])

    def test_a_staged_new_file_and_a_deleted_file_refuse_too(self):
        (self.repo / "new.txt").write_text("x\n", encoding="utf-8")
        self.git("add", "new.txt")
        self.assertIn("new.txt", self.refused(2, "uncommitted changes"))
        self.git("rm", "-q", "-f", "new.txt")
        (self.repo / "README").unlink()
        self.assertIn("README", self.refused(2, "uncommitted changes"))

    def test_a_clean_tree_with_an_untracked_file_passes(self):
        (self.repo / "scratch.txt").write_text("untracked\n", encoding="utf-8")
        self.assertEqual(self.git("status", "--porcelain"), "?? scratch.txt\n")
        self.sealed()

    def test_a_git_failure_refuses(self):
        notrepo = self.tmp / "not-a-repository"
        notrepo.mkdir()
        code, text = (lambda buf: (seal.command_seal(self.store, self.pre, self.repair, self.guide, self.rule,
                                                     self.seal_dir, environ=self.environ, stdout=buf,
                                                     repo_root=notrepo, cfg=self.cfg), buf.getvalue()))(io.StringIO())
        self.assertEqual(code, 2, text)
        self.assertIn("git status cannot be read", text)
        self.assertFalse(self.seal_dir.exists())
        with mock.patch.object(seal.subprocess, "run", side_effect=OSError("no git")):
            self.refused(2, "git status cannot be read")


class AfterTheSealTests(World):
    """--preseal, the verification on run, score and report, and the final scoring."""

    def setUp(self):
        super().setUp()
        self.sealed()
        self.commit_seal()

    def launches(self):
        return len(self.calls())

    def final_run(self, **kw):
        out = io.StringIO()
        before = self.launches()
        args = dict(seal_dir=self.seal_dir, preseal=self.pre)
        args.update(kw)
        code = probes.command_run(self.store, self.final, "heldout", "all", ["floor", "baseline", "oracle"], 2, None,
                                  "20260101", self.environ, out, repo_root=self.repo, **args)
        return code, out.getvalue(), self.launches() - before

    def final_score(self, judge_obj=None, root=None, **kw):
        judge_obj = judge_obj or self.judge
        out = io.StringIO()
        code = score.command_score(self.final, root or self.store, self.environ, out, judge_obj.label_probe,
                                   judge_obj.label_items, seal_dir=self.seal_dir, **kw)
        return code, out.getvalue()

    def test_a_held_out_baseline_run_without_preseal_refuses(self):
        code, text, launched = self.final_run(preseal=None)
        self.assertEqual((code, launched), (2, 0), text)
        self.assertIn("--preseal", text)

    def test_preseal_is_copied_read_only_and_only_the_baseline_launches(self):
        code, text, launched = self.final_run()
        self.assertEqual(code, 0, text)
        self.assertEqual(launched, 2 * 2 * 2)  # 2 fixtures x 2 seeds x (a preserve run and a probe session), baseline only
        seal_json = self.seal_json()
        for rel in [seal_json["labels"]["path"]] + sorted(seal_json["sessions"]):
            copy_ = self.final / rel
            self.assertEqual(copy_.read_bytes(), (self.pre / rel).read_bytes(), rel)
            self.assertEqual(stat.S_IMODE(copy_.stat().st_mode), 0o444, rel)
        self.assertEqual(seal.verify(self.seal_dir, self.store, self.final), [])

    def variant_run(self, **kw):
        arms_dir = self.tmp / "variants"
        (arms_dir / "variant-a").mkdir(parents=True, exist_ok=True)
        (arms_dir / "variant-a" / "SKILL.md").write_text("a variant skill\n", encoding="utf-8")
        out = io.StringIO()
        before = self.launches()
        args = dict(seal_dir=self.seal_dir, preseal=self.pre, arms_dir=arms_dir)
        args.update(kw)
        code = probes.command_run(self.store, self.final, "heldout", "all", ["floor", "variant-a", "oracle"], 2, None,
                                  "20260101", self.environ, out, repo_root=self.repo, **args)
        return code, out.getvalue(), self.launches() - before

    def test_a_skill_variant_carries_the_preseal_files_and_launches_only_the_variant(self):
        code, text, launched = self.variant_run()
        self.assertEqual(code, 0, text)
        self.assertEqual(launched, 2 * 2 * 2)  # the variant only: no floor or oracle unit
        seal_json = self.seal_json()
        for rel in [seal_json["labels"]["path"]] + sorted(seal_json["sessions"]):
            copy_ = self.final / rel
            self.assertEqual(copy_.read_bytes(), (self.pre / rel).read_bytes(), rel)
            self.assertEqual(stat.S_IMODE(copy_.stat().st_mode), 0o444, rel)
        self.assertEqual(seal.verify(self.seal_dir, self.store, self.final), [])

    def test_the_guard_and_the_carry_both_ask_needs_seal(self):
        self.assertTrue(seal.needs_seal("heldout", HIDS, ["floor", "variant-a", "oracle"]))
        self.assertFalse(seal.needs_seal("heldout", HIDS, ["floor", "oracle"]))
        self.assertFalse(seal.needs_seal("dev", ["dev-001-x"], ["floor", "variant-a"]))
        with mock.patch.object(seal, "needs_seal", return_value=False) as asked, \
                mock.patch.object(seal, "carry_preseal", Quiet()) as carried:
            guard = seal.baseline_guard("heldout", HIDS, ["variant-a"], self.store, None, None, self.repo)
            code, text, launched = self.variant_run(seal_dir=None, preseal=None)
        self.assertEqual(guard, (0, ""))  # the guard let a held-out variant through without a seal
        self.assertGreaterEqual(asked.call_count, 2)  # the guard and the carry both asked the one function
        self.assertEqual(carried.calls, [])  # and the answer False meant no carry
        self.assertEqual(code, 0, text)
        self.assertGreater(launched, 2 * 2 * 2)  # floor and oracle ran again with the variant

    def test_a_preseal_mismatch_refuses_with_nothing_launched_and_nothing_copied(self):
        session = self.pre / "probes" / "floor" / HIDS[0] / "s2.json"
        session.write_text(session.read_text(encoding="utf-8") + " ", encoding="utf-8")
        code, text, launched = self.final_run()
        self.assertEqual((code, launched), (1, 0), text)
        self.assertIn(f"changed:probes/floor/{HIDS[0]}/s2.json", text)
        self.assertIn("outcome not determined", text)
        self.assertFalse(self.final.exists())

    def test_a_changed_labels_file_or_fixture_refuses_the_run(self):
        (self.pre / "labels.json").write_bytes((self.pre / "labels.json").read_bytes() + b" ")
        self.assertEqual(self.final_run()[0], 1)
        self.setUp()
        (self.store / HIDS[0] / "repo" / "late.txt").write_text("x\n", encoding="utf-8")
        code, text, launched = self.final_run()
        self.assertEqual((code, launched), (1, 0), text)
        self.assertIn(f"extra:{HIDS[0]}/repo/late.txt", text)

    def test_final_scoring_labels_into_a_separate_file_and_leaves_the_sealed_one_alone(self):
        self.assertEqual(self.final_run()[0], 0)
        sealed = self.final / "labels.json"
        before = (sealed.read_bytes(), sealed.stat().st_mtime_ns, stat.S_IMODE(sealed.stat().st_mode))
        self.judge = base.FakeJudge(HSPECS)
        code, text = self.final_score()
        self.assertEqual(code, 0, text)
        self.assertEqual((sealed.read_bytes(), sealed.stat().st_mtime_ns, stat.S_IMODE(sealed.stat().st_mode)), before)
        extra = json.loads((self.final / "labels-final.json").read_text(encoding="utf-8"))
        self.assertTrue(extra["probe"] and extra["items"])  # the baseline's labels
        sealed_data = json.loads(sealed.read_text(encoding="utf-8"))
        self.assertFalse(set(extra["probe"]) & set(sealed_data["probe"]))
        scores = json.loads((self.final / "scores.json").read_text(encoding="utf-8"))
        self.assertEqual(scores["scorable"], self.scores["scorable"])
        self.assertIn("baseline", scores["per_fixture"])
        asked = [p["id"] for p in self.judge.probes_sent()]
        self.assertTrue(asked)  # the judge was asked, for baseline answers only (the next test pins the locked arms)

    def test_a_second_final_pass_asks_nothing_new(self):
        self.assertEqual(self.final_run()[0], 0)
        self.assertEqual(self.final_score()[0], 0)
        again = base.FakeJudge(HSPECS)
        self.assertEqual(self.final_score(again)[0], 0)
        self.assertEqual((again.probe_calls, again.items_calls), ([], []))

    def test_a_floor_or_oracle_label_missing_from_the_sealed_file_is_never_asked_again(self):
        folder = self.tmp / "lab"
        folder.mkdir()
        (folder / "labels.json").write_text(json.dumps({"probe": {}, "items": {}}), encoding="utf-8")
        asked = base.FakeJudge(HSPECS)
        labeller = score.Labeller(folder, self.cfg, self.environ, asked.label_probe, asked.label_items, mode="final")
        case = dict(id="p1", question="q", gold="g", stale=[], answer="g")
        self.assertEqual(labeller.probes([case], "S", arm="floor"), {"p1": None})
        self.assertEqual(labeller.probes([case], "S", arm="oracle"), {"p1": None})
        self.assertIsNone(labeller.items({"id": "i", "needed": [], "not_needed": [], "lines": []}, arm="oracle"))
        self.assertEqual((asked.probe_calls, asked.items_calls, labeller.calls), ([], [], 0))
        self.assertEqual(labeller.probes([case], "S", arm="baseline"), {"p1": "correct"})  # any other arm is asked
        self.assertEqual(labeller.calls, 1)
        self.assertEqual(json.loads((folder / "labels.json").read_text(encoding="utf-8")), {"probe": {}, "items": {}})
        self.assertTrue((folder / "labels-final.json").is_file())

    def test_a_replay_labeller_never_asks_or_writes(self):
        folder = self.tmp / "lab"
        folder.mkdir()
        (folder / "labels.json").write_text(json.dumps({"probe": {}, "items": {}}), encoding="utf-8")
        asked = base.FakeJudge(HSPECS)
        labeller = score.Labeller(folder, self.cfg, self.environ, asked.label_probe, asked.label_items, mode="replay")
        case = dict(id="p1", question="q", gold="g", stale=[], answer="g")
        self.assertEqual(labeller.probes([case], "S", arm="baseline"), {"p1": None})
        self.assertEqual(sorted(os.listdir(folder)), ["labels.json"])
        self.assertEqual(asked.probe_calls, [])

    def test_a_labels_file_that_no_longer_matches_the_seal_stops_the_final_scoring(self):
        self.assertEqual(self.final_run()[0], 0)
        self.judge = base.FakeJudge(HSPECS)
        sealed = self.final / "labels.json"
        sealed.chmod(0o644)
        sealed.write_bytes(sealed.read_bytes() + b" ")
        code, text = self.final_score()
        self.assertEqual(code, 1, text)
        self.assertIn("sealed labels.json changed", text)
        self.assertFalse((self.final / "scores.json").exists())
        self.assertEqual(self.judge.probe_calls, [])  # nothing was asked

    def test_a_missing_sealed_labels_file_stops_the_final_scoring(self):
        self.assertEqual(self.final_run()[0], 0)
        (self.final / "labels.json").unlink()
        code, text = self.final_score()
        self.assertEqual(code, 1, text)
        self.assertIn("is missing", text)

    def test_the_record_mismatch_stops_the_scoring_and_names_the_difference(self):
        self.assertEqual(self.final_run()[0], 0)
        other = self.tmp / "store-2"
        shutil.copytree(self.store, other)
        spec_path = other / HIDS[0] / "spec.json"
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        victim = self.scores["scorable"][HIDS[0]][0]
        next(p for p in spec["probes"] if p["id"] == victim)["excluded"] = "withdrawn after the seal"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        code, text = self.final_score(root=other)
        self.assertEqual(code, 1, text)
        self.assertIn("differs from the sealed record", text)
        self.assertIn(f"{HIDS[0]}: scorable differs", text)
        self.assertIn(f"{HIDS[0]}: excluded differs", text)
        self.assertNotIn(HIDS[1], text)
        self.assertFalse((self.final / "scores.json").exists())

    def test_final_record_diff_names_each_kind_of_difference(self):
        scores = copy.deepcopy(self.scores)
        self.assertEqual(seal.final_record_diff(self.seal_dir, scores), [])
        scores["dropped"][HIDS[1]] = {"x": ["oracle_minority"]}
        scores["fixtures"] = scores["fixtures"] + ["heldout-099-new"]
        scores["scorable"]["heldout-099-new"], scores["dropped"]["heldout-099-new"] = [], {}
        scores["excluded"]["heldout-099-new"] = []
        diff = seal.final_record_diff(self.seal_dir, scores)
        self.assertIn(f"{HIDS[1]}: dropped differs", diff)
        self.assertIn("fixture heldout-099-new missing from the sealed record", diff)

    def test_the_command_line_wires_the_seal_into_run_score_and_report(self):
        env = dict(self.environ)
        with mock.patch.dict(os.environ, env), mock.patch.object(claude_run, "ROOT", self.repo):
            def main(*argv):
                with mock.patch("sys.stdout", new=io.StringIO()) as shown:
                    return run_cli.main(list(argv)), shown.getvalue()
            common = ["--fixtures-root", str(self.store)]
            # run: a held-out baseline needs --seal
            code, text = main("run", "--split", "heldout", *common, "--out", str(self.final), "--seeds", "2")
            self.assertEqual(code, 2, text)
            self.assertIn("--seal", text)
            # run with --seal but a changed fixture: the seal does not verify
            (self.store / HIDS[0] / "repo" / "late.txt").write_text("x\n", encoding="utf-8")
            code, text = main("run", "--split", "heldout", *common, "--out", str(self.final), "--seeds", "2",
                              "--seal", str(self.seal_dir), "--preseal", str(self.pre))
            self.assertEqual(code, 1, text)
            self.assertIn("outcome not determined", text)
            (self.store / HIDS[0] / "repo" / "late.txt").unlink()
            # a final results folder: score and report need --seal and verify first
            self.assertEqual(self.final_run()[0], 0)
            for command in ("score", "report"):
                with self.subTest(command=command):
                    code, text = main(command, "--from", str(self.final), *common)
                    self.assertEqual(code, 2, text)
                    self.assertIn("--seal", text)
                    (self.store / HIDS[1] / "spec.json").write_text("{}", encoding="utf-8")
                    code, text = main(command, "--from", str(self.final), *common, "--seal", str(self.seal_dir))
                    self.assertEqual(code, 1, text)
                    self.assertIn(f"changed:{HIDS[1]}/spec.json", text)
                    (self.store / HIDS[1] / "spec.json").write_text(json.dumps(HSPECS[HIDS[1]], indent=1), encoding="utf-8")
            self.assertEqual(seal.verify(self.seal_dir, self.store, self.final), [])

    def test_pre_seal_scoring_and_reports_need_no_seal(self):
        with mock.patch.dict(os.environ, self.environ):
            self.assertFalse(seal.is_final(arms.read_json(self.pre / "run.json")))
            self.assertTrue(seal.is_final({"split": "heldout", "fixtures": HIDS, "arms": ["floor", "baseline", "oracle"]}))
            self.assertFalse(seal.is_final({"split": "dev", "fixtures": ["dev-001-x"], "arms": ["floor", "baseline", "oracle"]}))
            self.assertFalse(seal.is_final({"split": "heldout", "fixtures": HIDS, "arms": ["floor", "oracle"]}))
            self.assertTrue(seal.is_final({"split": "dev", "fixtures": ["heldout-001-x"], "arms": ["baseline"]}))
            with mock.patch("sys.stdout", new=io.StringIO()) as shown:
                code = run_cli.main(["report", "--from", str(self.pre)])
            self.assertNotIn("--seal", shown.getvalue())  # report on floor and oracle results alone is not final

    def test_the_final_report_needs_scores_but_passes_the_seal_check_first(self):
        self.assertEqual(self.final_run()[0], 0)
        self.assertEqual(self.final_score()[0], 0)
        audit_json = self.tmp / "audit.json"
        audit_json.write_text(json.dumps({"n": 100, "kappa": 0.9}), encoding="utf-8")
        with mock.patch.dict(os.environ, self.environ), mock.patch("sys.stdout", new=io.StringIO()) as shown:
            code = run_cli.main(["report", "--from", str(self.final), "--fixtures-root", str(self.store),
                                 "--seal", str(self.seal_dir), "--audit", str(audit_json)])
        self.assertNotIn("seal does not verify", shown.getvalue())
        self.assertEqual(code, 0, shown.getvalue())


class NoGateStateInTheRepositoryTests(unittest.TestCase):
    def test_gates_json_lives_in_the_eval_root_and_nothing_prints_it_whole(self):
        environ = {"TMPDIR": "/some/tmp"}
        self.assertEqual(seal.gates_path(environ), Path("/some/tmp/cpr-eval/gates.json"))
        self.assertFalse(str(seal.gates_path(environ)).startswith(str(ROOT)))

    def test_the_seal_module_names_no_credential_variable(self):
        text = Path(seal.__file__).read_text(encoding="utf-8")
        for name in claude_run.AUTH_VARS:
            self.assertNotIn(name, text)


if __name__ == "__main__":
    unittest.main()
