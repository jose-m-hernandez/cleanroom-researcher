"""Offline tests for the `author` command of EXP-0001 and for the `notools` keyword it needs.

Nothing here calls the real claude: a fake runner stands in for the launch, and the probe golden uses a fake
process object. Run with: python3 -m unittest tests.test_exp0001_author
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import exp0001_paths  # noqa: F401

import author  # noqa: E402
import claude_run  # noqa: E402
import run as run_cli  # noqa: E402
import seal  # noqa: E402
from tests import test_exp0001_isolation as iso  # noqa: E402
from tests import test_exp0001_seal as seal_tests  # noqa: E402

GOLDEN_PROBE_ARGV = [
    "claude", "-p", "PROMPT", "--output-format", "stream-json", "--verbose", "--include-hook-events",
    "--strict-mcp-config", "--setting-sources", "project", "--model", "m-1", "--permission-mode", "acceptEdits",
    "--permission-prompts", "none", "--max-budget-usd", "0.75", "--session-id", "sid-1", "--tools", "",
]
GOLDEN_PROBE_ENV = {
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "2048", "CPR_CONFIG": "/fixed/tmp/cpr-eval/sandboxes/cpr.json",
    "HOME": "/fixed/home", "PATH": "/usr/bin", "TMPDIR": "/fixed/tmp",
}
FIXED_ENVIRON = {"TMPDIR": "/fixed/tmp", "HOME": "/fixed/home", "PATH": "/usr/bin"}


class FakeChild:
    """Stands in for the Popen object: records how it was started and returns an empty stream."""

    launches = []

    def __init__(self, argv, **kwargs):
        self.argv = argv
        self.kwargs = kwargs
        self.timeout = None
        self.returncode = 0
        FakeChild.launches.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def communicate(self, timeout=None):
        self.timeout = timeout
        return "", ""


class TestProbeGolden(unittest.TestCase):
    """The probe argv, env and timeout, captured at base, must not change when `notools` is added."""

    def cfg(self):
        return dict(iso.CFG, model="m-1")

    def test_build_argv_probe_is_byte_identical(self):
        argv = claude_run.build_argv(self.cfg(), "PROMPT", session_id="sid-1", probe=True)
        self.assertEqual(argv, GOLDEN_PROBE_ARGV)

    def test_child_env_probe_is_byte_identical(self):
        env = claude_run.child_env(self.cfg(), "run-1", FIXED_ENVIRON, probe=True)
        self.assertEqual(env, GOLDEN_PROBE_ENV)

    def test_run_claude_probe_launches_with_the_golden_argv_env_and_timeout(self):
        FakeChild.launches = []
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t).resolve()
            environ = {"TMPDIR": str(tmp), "HOME": str(tmp / "home"), "PATH": "/usr/bin", "CPR_EVAL_LIVE": "1"}
            sandbox = claude_run.make_sandbox("g", environ)
            with mock.patch.object(claude_run.subprocess, "Popen", FakeChild):
                claude_run.run_claude(self.cfg(), "PROMPT", sandbox, label="g", phase="control", kind="probe",
                                      probe=True, session_id="sid-1", environ=environ)
            (child,) = FakeChild.launches
            self.assertEqual(child.argv, GOLDEN_PROBE_ARGV)
            self.assertEqual(child.timeout, 180)
            expected = dict(GOLDEN_PROBE_ENV, HOME=str(tmp / "home"), TMPDIR=str(tmp),
                            CPR_CONFIG=str(tmp / "cpr-eval" / "sandboxes" / "cpr.json"))
            self.assertEqual(child.kwargs["env"], expected)


class TestNotools(unittest.TestCase):
    """`notools=True` adds `--tools ""` and nothing else: no output cap, and the full run timeout."""

    def cfg(self):
        return dict(iso.CFG, model="m-1")

    def test_build_argv_notools_equals_the_probe_argv(self):
        argv = claude_run.build_argv(self.cfg(), "PROMPT", session_id="sid-1", notools=True)
        self.assertEqual(argv, GOLDEN_PROBE_ARGV)

    def test_build_argv_without_notools_has_no_tools_flag(self):
        self.assertNotIn("--tools", claude_run.build_argv(self.cfg(), "PROMPT", session_id="sid-1"))

    def test_run_claude_notools_has_no_output_cap_and_the_run_timeout(self):
        FakeChild.launches = []
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t).resolve()
            environ = {"TMPDIR": str(tmp), "HOME": str(tmp / "home"), "PATH": "/usr/bin", "CPR_EVAL_LIVE": "1"}
            sandbox = claude_run.make_sandbox("g", environ)
            with mock.patch.object(claude_run.subprocess, "Popen", FakeChild):
                rec = claude_run.run_claude(self.cfg(), "PROMPT", sandbox, label="g", phase="generation",
                                            kind="probe", notools=True, session_id="sid-1", environ=environ)
            (child,) = FakeChild.launches
        self.assertEqual(child.argv, GOLDEN_PROBE_ARGV)
        self.assertNotIn(claude_run.MAX_OUTPUT_ENV, child.kwargs["env"])
        self.assertEqual(child.timeout, claude_run.RUN_TIMEOUT)
        self.assertEqual(rec["phase"], "generation")


ROOT = Path(__file__).resolve().parents[1]
PROMPT = "AUTHOR PROMPT TEXT\n\n\n"
GUIDE = "GUIDE TEXT\n"
EXTRA = "EXTRA EVIDENCE TEXT\n"
SENTINEL = "spec-sentinel-qzx"
LIVE_REFUSED = "live run refused"


def block(spec_id, body=None):
    """A header line and a fenced block, as the command expects the reply to carry them."""
    body = body if body is not None else json.dumps({"id": spec_id, "summary": SENTINEL}, indent=1) + "\n"
    return f"{spec_id}\n```json\n{body}```\n"


def reply(*ids):
    return "Here are the specs.\n\n" + "\n".join(block(i) for i in ids)


class AuthorCase(unittest.TestCase):
    """A fake HOME and TMPDIR outside the clone, a fake runner, a patched config and preflight."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        home = self.tmp / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "CLAUDE.md").write_text("# Title\n" + iso.MARKER + "\n", encoding="utf-8")
        self.environ = {"HOME": str(home), "TMPDIR": str(self.tmp), "CPR_EVAL_LIVE": "1", "PATH": "/usr/bin"}
        (self.tmp / "p.md").write_text(PROMPT, encoding="utf-8")
        (self.tmp / "g.md").write_text(GUIDE, encoding="utf-8")
        (self.tmp / "x.md").write_text(EXTRA, encoding="utf-8")
        self.store = self.tmp / "store"
        self.calls = []
        self.reply = reply("heldout-001-tide-table", "heldout-002-ferry-log", "heldout-003-kiln-notes")
        self.proof_ok = True
        for target, value in ((claude_run, "load_config"), (claude_run, "preflight")):
            patch = mock.patch.object(target, value, (lambda path=None: dict(iso.CFG)) if value == "load_config"
                                      else (lambda cfg, environ=None: ""))
            patch.start()
            self.addCleanup(patch.stop)

    def runner(self, cfg, prompt, sandbox, **kw):
        self.calls.append((prompt, kw))
        folder = self.tmp / f"fx{len(self.calls)}"
        folder.mkdir()
        fx = iso.RunFixture(folder, kind="probe", with_plugin=False)
        fx.events[-1]["result"] = self.reply
        if not self.proof_ok:
            fx.init["model"] = "other"
        fx.write_transcript()
        fx.run.update({"started_utc": "2026-11-18T00:00:00Z", "cost_usd": 0.5, "phase": kw["phase"],
                       "pins": {"claude_code_version": iso.VERSION, "model": iso.MODEL}})
        return fx.run

    def author(self, **kw):
        args = dict(prompt_path=self.tmp / "p.md", guide_path=self.tmp / "g.md", out_path=self.store,
                    environ=self.environ, runner=self.runner)
        args.update(kw)
        stream = io.StringIO()
        code = author.command_author(out=stream, **args)
        return code, stream.getvalue()

    def eval_file(self, name):
        path = claude_run.eval_root(self.environ) / name
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


class TestAuthorGate(AuthorCase, unittest.TestCase):
    def test_a_refused_without_cpr_eval_live(self):
        del self.environ["CPR_EVAL_LIVE"]
        code, text = self.author()
        self.assertEqual(code, 2)
        self.assertIn(LIVE_REFUSED, text)
        self.assertEqual(self.calls, [])

    def test_b_refused_without_gates_and_without_c6_allowed_without_c7(self):
        write = seal_tests.write_gates
        with mock.patch.dict(os.environ, {"TMPDIR": str(self.tmp)}):
            os.environ.pop("CPR_EVAL_LIVE", None)
            self.assertIn("gates.json is missing", seal.command_gate("author", self.environ))
            gates = claude_run.eval_root(self.environ) / "gates.json"
            write(self.environ)  # C1-5 only
            self.assertIn("C6 canary has not passed", seal.command_gate("author", self.environ))
            gates.unlink()
            write(self.environ, C6="PASS", JC=seal_tests.jc())  # no C7; author needs no JC either way
            self.assertEqual(seal.command_gate("author", self.environ), "")
            self.assertIn("C7 canary has not passed", seal.command_gate("gen-fixture", self.environ))
            gates.unlink()
            write(self.environ, C6="FAIL")
            self.assertIn("did not pass", seal.command_gate("author", self.environ))

    def test_the_gate_stops_the_command_before_the_runner_is_reached(self):
        with mock.patch.dict(os.environ, {"TMPDIR": str(self.tmp)}):
            argv = ["author", "--prompt", str(self.tmp / "p.md"), "--guide", str(self.tmp / "g.md"),
                    "--out", str(self.store)]
            with mock.patch("sys.stdout", new=io.StringIO()) as shown:
                code = run_cli.main(argv)
        self.assertEqual(code, 2)
        self.assertIn("REFUSED", shown.getvalue())
        self.assertEqual(self.calls, [])

    def test_c_the_fifth_call_is_refused_and_the_counter_survives_a_failed_proof(self):
        self.proof_ok = False
        for n in range(4):
            code, text = self.author()
            self.assertEqual(code, 1, text)
            self.assertIn("isolation proof failed", text)
        code, text = self.author()
        self.assertEqual(code, 1)
        self.assertIn("author: STOP the author run budget is spent (4 runs)", text)
        self.assertEqual(len(self.calls), 4)
        stats = self.eval_file("author-calls.json")
        self.assertEqual((stats["count"], stats["failed"], stats["successful"]), (4, 4, 0))

    def test_d_out_inside_the_clone_is_refused_including_under_recorded(self):
        for inside in (ROOT / "scratch-author-out", ROOT / "experiments" / "EXP-0001" / "recorded" / "author-x"):
            code, text = self.author(out_path=inside)
            self.assertEqual(code, 2, text)
            self.assertIn("inside the repository clone", text)
        self.assertEqual(self.calls, [])
        self.assertFalse((ROOT / "scratch-author-out").exists())

    def test_the_cli_refuses_an_out_without_a_value_for_the_flag_and_names_only_a_label(self):
        with mock.patch("sys.stdout", new=io.StringIO()) as shown, mock.patch("sys.stderr", new=io.StringIO()):
            with self.assertRaises(SystemExit):
                run_cli.main(["author", "--prompt", "p", "--guide", "g"])
        self.assertEqual(shown.getvalue(), "")


class TestAuthorWrites(AuthorCase, unittest.TestCase):
    def test_e_three_blocks_write_three_specs_whose_bytes_equal_the_blocks(self):
        code, text = self.author()
        self.assertEqual(code, 0, text)
        self.assertIn("author: wrote 3 specs", text)
        for spec_id in ("heldout-001-tide-table", "heldout-002-ferry-log", "heldout-003-kiln-notes"):
            want = json.dumps({"id": spec_id, "summary": SENTINEL}, indent=1) + "\n"
            self.assertEqual((self.store / spec_id / "spec.json").read_bytes(), want.encode("utf-8"))
            self.assertEqual([p.name for p in (self.store / spec_id).iterdir()], ["spec.json"])
        self.assertEqual(sorted(p.name for p in self.store.iterdir()),
                         ["heldout-001-tide-table", "heldout-002-ferry-log", "heldout-003-kiln-notes"])

    def test_f_a_bad_header_or_a_non_json_block_writes_nothing_and_names_a_label(self):
        self.reply = (block("not-an-id") + block("heldout-001-tide-table", "{oops secret-body\n")
                      + block("heldout-002-ferry-log"))
        code, text = self.author()
        self.assertEqual(code, 1)
        self.assertIn("block-1: header_invalid", text)
        self.assertIn("heldout-001-tide-table: json_invalid", text)
        self.assertEqual([p.name for p in self.store.iterdir()], ["heldout-002-ferry-log"])
        self.assertNotIn("secret-body", text)

    def test_an_existing_id_directory_is_never_overwritten(self):
        (self.store / "heldout-001-tide-table").mkdir(parents=True)
        code, text = self.author()
        self.assertEqual(code, 1)
        self.assertIn("heldout-001-tide-table: exists", text)
        self.assertEqual(list((self.store / "heldout-001-tide-table").iterdir()), [])
        self.assertEqual(sorted(p.name for p in self.store.iterdir()),
                         ["heldout-001-tide-table", "heldout-002-ferry-log", "heldout-003-kiln-notes"])

    def test_g_stdout_holds_no_spec_text(self):
        code, text = self.author()
        self.assertNotIn(SENTINEL, text)
        self.assertNotIn("Here are the specs", text)

    def test_validate_labels_are_printed_as_id_and_label_only(self):
        code, text = self.author()
        self.assertIn("heldout-001-tide-table: missing_key:", text)
        self.assertNotIn(SENTINEL, text)

    def test_h_a_failed_isolation_proof_writes_no_spec(self):
        self.proof_ok = False
        code, text = self.author()
        self.assertEqual(code, 1)
        self.assertIn("author: STOP isolation proof failed: model_mismatch", text)
        self.assertFalse(self.store.exists())
        self.assertEqual(self.eval_file("author-runs.json")[0]["isolation"], "FAIL")

    def test_only_ids_writes_only_the_named_blocks(self):
        code, text = self.author(only_ids=["heldout-002-ferry-log"])
        self.assertEqual([p.name for p in self.store.iterdir()], ["heldout-002-ferry-log"])

    def test_i_the_runner_gets_notools_the_authoring_phase_and_only_the_prompt_and_guide(self):
        self.author()
        ((prompt, kw),) = self.calls
        self.assertIs(kw["notools"], True)
        self.assertEqual(kw["phase"], "authoring")
        self.assertEqual(kw["kind"], "probe")
        self.assertEqual(prompt, "AUTHOR PROMPT TEXT\n\nGUIDE TEXT")
        self.assertNotIn("dev-0", prompt)
        self.assertNotIn(SENTINEL, prompt)

    def test_extra_text_is_appended_and_only_its_sha256_is_recorded(self):
        self.author(extra_path=self.tmp / "x.md")
        ((prompt, kw),) = self.calls
        self.assertEqual(prompt, "AUTHOR PROMPT TEXT\n\nGUIDE TEXT\n\nEXTRA EVIDENCE TEXT")
        (row,) = self.eval_file("author-runs.json")
        self.assertEqual(row["extra_sha256"], hashlib.sha256(EXTRA.encode("utf-8")).hexdigest())
        self.assertEqual(row["prompt_sha256"], hashlib.sha256(PROMPT.encode("utf-8")).hexdigest())
        self.assertEqual(row["guide_sha256"], hashlib.sha256(GUIDE.encode("utf-8")).hexdigest())
        self.assertNotIn("EXTRA EVIDENCE", json.dumps(row))
        self.assertEqual((row["run"], row["isolation"], row["model"], row["cost_usd"]), (1, "PASS", iso.MODEL, 0.5))
        self.assertEqual(row["ids_written"], ["heldout-001-tide-table", "heldout-002-ferry-log", "heldout-003-kiln-notes"])

    def test_an_unreadable_input_does_not_use_a_run(self):
        code, text = self.author(guide_path=self.tmp / "missing.md")
        self.assertEqual(code, 1)
        self.assertIn("an input file is unreadable", text)
        self.assertIsNone(self.eval_file("author-calls.json"))

    def test_the_isolation_proof_accepts_a_probe_kind_run_with_no_tools(self):
        (self.tmp / "own").mkdir()
        fx = iso.RunFixture(self.tmp / "own", kind="probe", with_plugin=False)
        self.assertTrue(fx.check()["ok"], fx.check())


class TestAuthorReplacements(AuthorCase, unittest.TestCase):
    def test_a_call_without_replaces_is_not_counted_as_a_replacement(self):
        self.author()
        self.assertIsNone(self.eval_file("author-replacements.json"))
        self.assertEqual(self.eval_file("author-runs.json")[0]["replaces"], [])

    def test_a_replaces_call_records_one_entry_per_id_after_the_proof_passes(self):
        self.reply = reply("heldout-004-new-one", "heldout-005-new-two")
        self.author(replaces=["heldout-001-tide-table", "heldout-002-ferry-log"])
        self.assertEqual(self.eval_file("author-replacements.json"),
                         [{"replaces": "heldout-001-tide-table", "run": 1},
                          {"replaces": "heldout-002-ferry-log", "run": 1}])
        self.assertEqual(self.eval_file("author-runs.json")[0]["replaces"],
                         ["heldout-001-tide-table", "heldout-002-ferry-log"])

    def test_a_failed_proof_records_no_replacement(self):
        self.proof_ok = False
        self.author(replaces=["heldout-001-tide-table"])
        self.assertIsNone(self.eval_file("author-replacements.json"))

    def test_more_than_three_replacements_are_refused_before_a_run_is_used(self):
        ledger = claude_run.eval_root(self.environ) / "author-replacements.json"
        ledger.parent.mkdir(parents=True)
        ledger.write_text(json.dumps([{"replaces": f"heldout-00{n}-a", "run": 1} for n in (1, 2, 3)]), encoding="utf-8")
        code, text = self.author(replaces=["heldout-009-z"])
        self.assertEqual(code, 2)
        self.assertIn("author: REFUSED at most 3 replacements (a replacement made for a generation failure counts)", text)
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.eval_file("author-calls.json"))

    def test_a_replacement_is_not_itself_replaced(self):
        self.reply = reply("heldout-004-new-one")
        code, text = self.author(replaces=["heldout-001-tide-table"])
        self.assertEqual(code, 0, text)
        code, text = self.author(replaces=["heldout-004-new-one"])
        self.assertEqual(code, 2)
        self.assertIn("author: REFUSED a replacement is not itself replaced", text)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.eval_file("author-calls.json")["count"], 1)

    def test_an_id_that_was_not_a_replacement_may_be_replaced(self):
        code, text = self.author()  # ids 001-003 written by a call without --replaces
        self.reply = reply("heldout-004-new-one")
        code, text = self.author(replaces=["heldout-001-tide-table"], out_path=self.tmp / "store2")
        self.assertEqual(code, 0, text)

    def test_extra_blocks_beyond_the_ids_named_are_counted_and_not_written(self):
        code, text = self.author(replaces=["heldout-009-z"])
        self.assertIn("author: 2 extra blocks not written", text)
        self.assertEqual([p.name for p in self.store.iterdir()], ["heldout-001-tide-table"])

    def test_replaces_ids_must_have_the_held_out_shape(self):
        for bad in (["dev-001-a"], ["heldout-1-a"], ["heldout-001-A"], []):
            code, text = self.author(replaces=bad)
            self.assertEqual(code, 2, bad)
            self.assertIn("author: REFUSED --replaces needs ids of the form heldout-NNN-name", text)
        self.assertEqual(self.calls, [])

    def test_refusals_print_labels_and_never_the_id_text(self):
        code, text = self.author(replaces=["heldout-001-CAPS-secret"])
        self.assertNotIn("secret", text)


if __name__ == "__main__":
    unittest.main()
