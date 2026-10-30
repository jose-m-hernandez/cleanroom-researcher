"""Offline tests for the C1-5 entry of gates.json (README P:239): every gated command and `canary --type preserve` need it
PASS, `read_gates` takes it without a token, `control` records it (PASS, FAIL, or nothing for a setup stop) and never
re-runs, and the pre-seal scan still reads the C6 and C7 tokens only.

Nothing here calls the real claude. The control tests use the scripted runner of tests/test_exp0001_isolation.py and the
scan world of tests/test_exp0001_seal.py by calling setUp on an instance, never by subclassing them or importing a class
that defines tests (either would run its tests twice).
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import exp0001_paths  # noqa: F401

import claude_run  # noqa: E402
import run as run_cli  # noqa: E402
import seal  # noqa: E402
from tests import test_exp0001_isolation as iso  # noqa: E402
from tests import test_exp0001_seal as st  # noqa: E402

KEY = "C1-5"
NOT_PASSED = "the C1-C5 controls have not passed: run `control` first"
STOPPED = "the experiment has stopped, outcome not determined"


def control_entry(outcome="PASS"):
    return {"outcome": outcome, "started_utc": st.STARTED, "run_id": "control-0a1b2c3d"}


def put(environ, **entries):
    """gates.json exactly as given (st.write_gates adds a C1-5 PASS entry; these tests need to leave it out)."""
    path = claude_run.eval_root(environ) / "gates.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries), encoding="utf-8")


class GateEnv(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.environ = {"TMPDIR": str(self.tmp), "HOME": str(self.tmp / "home")}


class CommandGateTests(GateEnv):
    """G1: the gated commands and C1-5."""

    def test_every_gated_command_refuses_without_a_c1_5_entry(self):
        put(self.environ, C6=st.entry("PASS"), C7=st.entry("PASS"))
        for command in seal.GATED_COMMANDS:
            with self.subTest(command=command):
                self.assertEqual(seal.command_gate(command, self.environ), NOT_PASSED)

    def test_c1_5_is_checked_before_c6(self):
        put(self.environ)
        self.assertEqual(seal.command_gate("run", self.environ), NOT_PASSED)  # not the "C6 canary has not passed" text

    def test_every_gated_command_refuses_after_a_c1_5_fail(self):
        put(self.environ, **{KEY: control_entry("FAIL")}, C6=st.entry("PASS"), C7=st.entry("PASS"))
        for command in seal.GATED_COMMANDS:
            with self.subTest(command=command):
                text = seal.command_gate(command, self.environ)
                self.assertIn(STOPPED, text)
                self.assertIn(KEY, text)

    def test_with_all_three_passing_each_command_opens(self):
        put(self.environ, **{KEY: control_entry()}, C6=st.entry("PASS"), C7=st.entry("PASS"))
        for command in seal.GATED_COMMANDS:
            with self.subTest(command=command):
                self.assertEqual(seal.command_gate(command, self.environ), "")

    def test_the_cli_refuses_with_the_new_message_before_any_launch(self):
        put(self.environ, C6=st.entry("PASS"))
        with mock.patch.dict(os.environ, {"TMPDIR": str(self.tmp)}), mock.patch("sys.stdout", new=io.StringIO()) as shown, \
                mock.patch.object(claude_run, "run_claude", st.Quiet()):
            os.environ.pop("CPR_EVAL_LIVE", None)
            code = run_cli.main(["judge-check"])
        self.assertEqual(code, 2, shown.getvalue())
        self.assertIn("REFUSED", shown.getvalue())
        self.assertIn(NOT_PASSED, shown.getvalue())

    def test_control_is_not_gated(self):
        put(self.environ, **{KEY: control_entry("FAIL")})
        self.assertEqual(seal.command_gate("control", self.environ), "")


class CanaryPreserveGateTests(GateEnv):
    """G2: canary --type preserve needs C1-5 PASS; generation keeps its C6 gate."""

    def test_preserve_refuses_without_a_c1_5_pass(self):
        put(self.environ)
        self.assertEqual(seal.canary_refusal("preserve", self.environ), NOT_PASSED)
        self.assertEqual(seal.canary_refusal("preserve", {"TMPDIR": str(self.tmp / "empty")}), NOT_PASSED)  # no file at all

    def test_preserve_refuses_after_a_c1_5_fail(self):
        put(self.environ, **{KEY: control_entry("FAIL")})
        text = seal.canary_refusal("preserve", self.environ)
        self.assertIn(STOPPED, text)

    def test_preserve_opens_with_a_c1_5_pass(self):
        put(self.environ, **{KEY: control_entry()})
        self.assertEqual(seal.canary_refusal("preserve", self.environ), "")

    def test_a_preserve_canary_is_still_never_re_run(self):
        put(self.environ, **{KEY: control_entry()}, C6=st.entry("FAIL"))
        self.assertIn("never re-run", seal.canary_refusal("preserve", self.environ))

    def test_generation_still_needs_c6(self):
        put(self.environ, **{KEY: control_entry()})
        self.assertIn("C6", seal.canary_refusal("generation", self.environ))
        put(self.environ, **{KEY: control_entry()}, C6=st.entry("PASS"))
        self.assertEqual(seal.canary_refusal("generation", self.environ), "")


class ReadGatesTests(GateEnv):
    """G3: the C1-5 entry has no token."""

    def test_an_entry_without_a_token_is_accepted(self):
        put(self.environ, **{KEY: control_entry()})
        gates, reason = seal.read_gates(self.environ)
        self.assertEqual((gates, reason), ({KEY: control_entry()}, ""))
        put(self.environ, **{KEY: control_entry("FAIL")})
        self.assertEqual(seal.read_gates(self.environ)[0], {KEY: control_entry("FAIL")})

    def test_an_outcome_outside_pass_and_fail_is_malformed(self):
        for outcome in ("ERROR", "MAYBE", "pass", None):
            put(self.environ, **{KEY: dict(control_entry(), outcome=outcome)})
            with self.subTest(outcome=outcome):
                self.assertEqual(seal.read_gates(self.environ), (None, "gates.json is malformed"))

    def test_a_canary_entry_without_a_token_is_still_malformed(self):
        for key in ("C6", "C7"):
            put(self.environ, **{key: {"outcome": "PASS", "started_utc": st.STARTED, "run_id": "r-1"}})
            with self.subTest(key=key):
                self.assertEqual(seal.read_gates(self.environ), (None, "gates.json is malformed"))

    def test_an_unknown_key_is_still_malformed(self):
        put(self.environ, **{"C2": control_entry()})
        self.assertEqual(seal.read_gates(self.environ), (None, "gates.json is malformed"))


class ControlRecordTests(unittest.TestCase):
    """G4: `control` records C1-5, on the scripted runner of the isolation tests."""

    def setUp(self):
        self.h = iso.ControlTests("run_control")
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.environ = self.h.environ
        self.path = claude_run.eval_root(self.environ) / "gates.json"

    def gates(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def runner(self, **kw):
        return iso.ScriptedRunner(self.h.tmp, self.h.ledger, **kw)

    def test_five_passes_record_a_pass_with_no_token(self):
        runner = self.runner()
        code, out = self.h.run_control(runner)
        self.assertEqual(code, 0, out)
        gates = self.gates()
        self.assertEqual(set(gates), {KEY})
        entry = gates[KEY]
        self.assertEqual(sorted(entry), ["outcome", "run_id", "started_utc"])
        self.assertEqual(entry["outcome"], "PASS")
        self.assertRegex(entry["run_id"], r"^control-[0-9a-f]{8}$")
        self.assertRegex(entry["started_utc"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(seal.read_gates(self.environ)[0], gates)

    def test_a_control_that_fails_after_a_launch_records_a_fail(self):
        runner = self.runner(c1_fires=False)
        code, out = self.h.run_control(runner)
        self.assertEqual((code, runner.calls), (1, ["c1"]), out)
        self.assertEqual(self.gates()[KEY]["outcome"], "FAIL")
        self.assertEqual(set(self.gates()), {KEY})

    def test_an_exception_from_the_second_launch_records_a_fail(self):
        inner = self.runner()

        def second_raises(*args, **kwargs):
            if inner.calls:
                raise RuntimeError("boom")
            return inner(*args, **kwargs)
        code, out = self.h.run_control(second_raises)
        self.assertEqual(code, 1)
        self.assertEqual(out.strip(), "control: ERROR RuntimeError")
        self.assertEqual(inner.calls, ["c1"])
        self.assertEqual(self.gates()[KEY]["outcome"], "FAIL")

    def test_an_exception_from_the_first_launch_is_a_fail_too(self):
        def boom(*args, **kwargs):
            kwargs["on_launch"]()  # the child was spawned, then the run raised
            raise RuntimeError("boom")
        code, _ = self.h.run_control(boom)
        self.assertEqual(code, 1)
        self.assertEqual(self.gates()[KEY]["outcome"], "FAIL")  # the spawn happened

    def test_a_setup_stop_before_the_first_launch_records_nothing(self):
        cases = {
            "no live flag": (dict(self.environ, CPR_EVAL_LIVE="0"), None, 2, "live run refused"),
            "no ledger": ({k: v for k, v in self.environ.items() if k != "CPR_EVAL_HOST_LEDGERS"}, None, 3,
                          "ledger not configured"),
            "config dir set": (dict(self.environ, CLAUDE_CONFIG_DIR=str(self.h.tmp / "cfg")), None, 3,
                               "unset CLAUDE_CONFIG_DIR"),
            "preflight FAIL": (self.environ, lambda cfg, environ=None: "claude is missing", 1, "preflight: FAIL"),
        }
        for name, (environ, preflight, want_code, want_text) in cases.items():
            runner = self.runner()
            with self.subTest(name=name):
                patch = mock.patch.object(claude_run, "preflight", preflight) if preflight else mock.MagicMock()
                with patch:
                    code, out = self.h.run_control(runner, environ)
                self.assertEqual((code, runner.calls), (want_code, []), out)
                self.assertIn(want_text, out)
                self.assertFalse(self.path.exists())

    def test_an_unreadable_spend_file_records_nothing(self):
        root = claude_run.eval_root(self.environ)
        root.mkdir(parents=True, exist_ok=True)
        (root / "spend.json").write_text("garbage", encoding="utf-8")
        runner = self.runner()
        code, out = self.h.run_control(runner)
        self.assertEqual((code, runner.calls), (1, []), out)
        self.assertFalse(self.path.exists())

    def test_a_control_is_never_re_run(self):
        for outcome in ("PASS", "FAIL"):
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({KEY: control_entry(outcome)}), encoding="utf-8")
            before = self.path.read_bytes()
            runner = self.runner()
            with self.subTest(outcome=outcome):
                code, out = self.h.run_control(runner)
                self.assertEqual((code, runner.calls), (2, []), out)
                self.assertIn("control: REFUSED C1-5 already has an entry; a control is never re-run", out)
                self.assertEqual(self.path.read_bytes(), before)

    def test_the_live_check_comes_before_the_never_re_run_check(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({KEY: control_entry()}), encoding="utf-8")
        code, out = self.h.run_control(self.runner(), {k: v for k, v in self.environ.items() if k != "CPR_EVAL_LIVE"})
        self.assertEqual(code, 2)
        self.assertIn("live run refused", out)
        self.assertNotIn("never re-run", out)

    def test_an_unreadable_or_malformed_gates_file_refuses_and_is_not_overwritten(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for name, text in {"not json": "{not json", "unknown key": json.dumps({"C9": control_entry()}),
                           "a list": "[]"}.items():
            self.path.write_text(text, encoding="utf-8")
            runner = self.runner()
            with self.subTest(name=name):
                code, out = self.h.run_control(runner)
                self.assertEqual((code, runner.calls), (2, []), out)
                self.assertIn("control: REFUSED", out)
                self.assertEqual(self.path.read_text(encoding="utf-8"), text)

    def test_a_c6_entry_already_there_is_kept_beside_the_new_one(self):
        put(self.environ, C6=st.entry("PASS"))
        code, out = self.h.run_control(self.runner())
        self.assertEqual(code, 0, out)
        self.assertEqual(set(self.gates()), {KEY, "C6"})
        self.assertEqual(self.gates()["C6"]["outcome"], "PASS")


class ControlSpawnAndLockTests(unittest.TestCase):
    """G6 to G12: a launch counts at the spawn point, and `control` holds an exclusive lock in the eval root."""

    def setUp(self):
        self.h = iso.ControlTests("run_control")
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.environ = self.h.environ
        self.path = claude_run.eval_root(self.environ) / "gates.json"
        self.lock = claude_run.eval_root(self.environ) / "control.lock"

    def gates(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def runner(self, **kw):
        return iso.ScriptedRunner(self.h.tmp, self.h.ledger, **kw)

    def real_runner(self, spawn):
        """The real run_claude; claude_run.subprocess.Popen is replaced by spawn only for the call (the sandbox's git
        setup runs before the runner is called, through the same module)."""
        def runner(*args, **kwargs):
            with mock.patch.object(claude_run.subprocess, "Popen", spawn):
                return claude_run.run_claude(*args, **kwargs)
        return runner

    def test_a_missing_claude_command_writes_nothing_and_the_experiment_stays_runnable(self):
        # G6
        spawn = mock.Mock(side_effect=FileNotFoundError("no such command"))
        code, out = self.h.run_control(self.real_runner(spawn))
        self.assertNotEqual(code, 0, out)
        self.assertEqual(spawn.call_count, 1)  # the spawn was tried, and failed before any child existed
        self.assertTrue(not self.path.exists() or KEY not in self.gates(), out)
        self.assertFalse(self.lock.exists())
        runner = self.runner()
        code, out = self.h.run_control(runner)
        self.assertEqual(code, 0, out)  # not refused by never-re-run
        self.assertEqual(self.gates()[KEY]["outcome"], "PASS")

    def test_a_spawn_error_before_on_launch_records_nothing_and_after_it_records_a_fail(self):
        # G7
        def before(*args, **kwargs):
            raise claude_run.SpawnError("claude could not be started")
        code, out = self.h.run_control(before)
        self.assertEqual(code, 1)
        self.assertEqual(out.strip(), "control: ERROR SpawnError")
        self.assertFalse(self.path.exists())

        def after(*args, **kwargs):
            kwargs["on_launch"]()
            raise claude_run.SpawnError("the child died")
        code, out = self.h.run_control(after)
        self.assertEqual(code, 1)
        self.assertEqual(self.gates()[KEY]["outcome"], "FAIL")

    def test_a_caller_on_launch_is_called_too(self):
        seen = []

        def fake(*args, on_launch=None, **kwargs):
            on_launch()
            raise RuntimeError("stop")
        wrapped = {}
        original = claude_run._control

        def capture(environ, out, runner, config_path):
            wrapped["runner"] = runner
            return original(environ, out, runner, config_path)
        with mock.patch.object(claude_run, "_control", capture):
            self.h.run_control(fake)
        with self.assertRaises(RuntimeError):
            wrapped["runner"](on_launch=lambda: seen.append("caller"))
        self.assertEqual(seen, ["caller"])

    def test_an_existing_lock_refuses_with_exit_2_and_changes_nothing(self):
        # G8
        for with_gates in (False, True):
            self.lock.parent.mkdir(parents=True, exist_ok=True)
            self.lock.write_bytes(b'{"pid": 4242, "started_utc": "2026-10-30T10:00:00Z"}\n')
            if with_gates:
                put(self.environ, C6=st.entry("PASS"))
            gates_before = self.path.read_bytes() if self.path.exists() else None
            lock_before = self.lock.read_bytes()
            runner = self.runner()
            with self.subTest(with_gates=with_gates):
                code, out = self.h.run_control(runner)
                self.assertEqual((code, runner.calls), (2, []), out)
                self.assertEqual(out.strip(), "control: REFUSED %s exists: another control may be running; if none is, "
                                 "delete the file and run control again" % self.lock)
                self.assertIn(str(self.lock), out)
                self.assertEqual(self.lock.read_bytes(), lock_before)
                self.assertEqual(self.path.read_bytes() if self.path.exists() else None, gates_before)
            self.lock.unlink()

    def test_a_stale_lock_refuses_too_and_is_not_broken(self):
        # G9
        for text in ('{"pid": 0, "started_utc": "2020-01-01T00:00:00Z"}\n', "not even json\n", ""):
            self.lock.parent.mkdir(parents=True, exist_ok=True)
            self.lock.write_text(text, encoding="utf-8")
            runner = self.runner()
            with self.subTest(text=text):
                code, out = self.h.run_control(runner)
                self.assertEqual((code, runner.calls), (2, []), out)
                self.assertIn("control: REFUSED", out)
                self.assertEqual(self.lock.read_text(encoding="utf-8"), text)
                self.assertFalse(self.path.exists())
            self.lock.unlink()

    def test_the_lock_exists_during_the_run_and_is_gone_after_every_kind_of_end(self):
        # G10
        during = []

        def watched(inner):
            def run(*args, **kwargs):
                during.append(self.lock.exists())
                return inner(*args, **kwargs)
            return run

        def raises(exc):
            def run(*args, **kwargs):
                during.append(self.lock.exists())
                kwargs["on_launch"]()
                raise exc
            return run
        cases = {"pass": watched(self.runner()), "fail": watched(self.runner(c1_fires=False)),
                 "exception after a spawn": raises(RuntimeError("boom")), "interrupt after a spawn": raises(KeyboardInterrupt())}
        for name, runner in cases.items():
            self.path.unlink(missing_ok=True)  # a control is never re-run
            del during[:]
            with self.subTest(name=name):
                if name == "interrupt after a spawn":
                    with self.assertRaises(KeyboardInterrupt):
                        self.h.run_control(runner)
                else:
                    self.h.run_control(runner)
                self.assertTrue(during and all(during), during)
                self.assertFalse(self.lock.exists())

    def test_the_lock_holds_this_pid_and_a_start_time(self):
        seen = []

        def look(*args, **kwargs):
            seen.append(json.loads(self.lock.read_text(encoding="utf-8")))
            raise RuntimeError("stop")
        self.h.run_control(look)
        self.assertEqual(sorted(seen[0]), ["pid", "started_utc"])
        self.assertEqual(seen[0]["pid"], os.getpid())
        self.assertRegex(seen[0]["started_utc"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_the_live_check_comes_before_the_lock(self):
        # G11
        self.lock.parent.mkdir(parents=True, exist_ok=True)
        self.lock.write_bytes(b"held\n")
        runner = self.runner()
        code, out = self.h.run_control(runner, {k: v for k, v in self.environ.items() if k != "CPR_EVAL_LIVE"})
        self.assertEqual((code, runner.calls), (2, []), out)
        self.assertIn("live run refused", out)
        self.assertNotIn("REFUSED", out)
        self.assertEqual(self.lock.read_bytes(), b"held\n")

    def test_an_interrupt_after_a_spawn_records_a_fail_re_raises_and_leaves_no_lock(self):
        # G12
        def interrupted(*args, **kwargs):
            kwargs["on_launch"]()
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.h.run_control(interrupted)
        self.assertEqual(self.gates()[KEY]["outcome"], "FAIL")
        self.assertFalse(self.lock.exists())


class ControlLockHeldUntilRecordedTests(unittest.TestCase):
    """L1, L2: the lock is held until the C1-5 entry is written, and removed after it."""

    def setUp(self):
        self.h = iso.ControlTests("run_control")
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.environ = self.h.environ
        self.path = claude_run.eval_root(self.environ) / "gates.json"
        self.lock = claude_run.eval_root(self.environ) / "control.lock"

    def gates(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def runner(self, **kw):
        return iso.ScriptedRunner(self.h.tmp, self.h.ledger, **kw)

    def spy(self, during, hook=None):
        real = seal.record_control

        def record(entry, environ=None):
            during.append(self.lock.exists())
            if hook is not None:
                hook()
            return real(entry, environ)
        return mock.patch.object(seal, "record_control", record)

    def test_the_lock_exists_while_c1_5_is_written_and_is_gone_after(self):
        # L1
        def interrupted(*args, **kwargs):
            kwargs["on_launch"]()
            raise KeyboardInterrupt()
        cases = {"pass": (self.runner(), "PASS"), "fail": (self.runner(c1_fires=False), "FAIL"),
                 "interrupt": (interrupted, "FAIL")}
        for name, (runner, outcome) in cases.items():
            self.path.unlink(missing_ok=True)  # a control is never re-run
            during = []
            with self.subTest(name=name), self.spy(during):
                if name == "interrupt":
                    with self.assertRaises(KeyboardInterrupt):
                        self.h.run_control(runner)
                else:
                    self.h.run_control(runner)
                self.assertEqual(during, [True])
                self.assertEqual(self.gates()[KEY]["outcome"], outcome)
                self.assertFalse(self.lock.exists())

    def test_a_second_control_started_during_the_record_is_refused(self):
        # L2
        second = {}

        def overlap():
            runner = self.runner()
            out = io.StringIO()
            second["code"] = claude_run.control(self.environ, out, runner, self.h.config)
            second["out"], second["calls"] = out.getvalue(), runner.calls
        during = []
        with self.spy(during, overlap):
            code, out = self.h.run_control(self.runner())
        self.assertEqual(code, 0, out)
        self.assertEqual((second["code"], second["calls"]), (2, []), second)
        self.assertEqual(second["out"].strip(), "control: REFUSED %s exists: another control may be running; if none "
                         "is, delete the file and run control again" % self.lock)
        self.assertEqual(set(self.gates()), {KEY})
        self.assertEqual(self.gates()[KEY]["outcome"], "PASS")
        self.assertFalse(self.lock.exists())


class ScanTokenTests(unittest.TestCase):
    """G5: the pre-seal scan reads the C6 and C7 tokens only."""

    def setUp(self):
        self.h = st.ScanTests("test_a_clean_store_has_no_hits")
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        # the real C1-5 entry has no token (the helper's has one): rewrite the file with the C6 and C7 entries kept
        put(self.h.environ, **{KEY: control_entry(), "C6": self.h.gates["C6"], "C7": self.h.gates["C7"]})

    def test_a_c1_5_entry_without_a_token_and_a_clean_store_passes(self):
        gates = seal.read_gates(self.h.environ)[0]
        self.assertEqual(set(gates), {KEY, "C6", "C7"})
        self.assertNotIn("token", gates[KEY])
        self.assertEqual(seal.scan_transcripts(self.h.root, self.h.environ), [])

    def test_the_c1_5_run_id_is_not_a_needle_and_the_canary_tokens_still_are(self):
        self.h.put("heldout-001-a", "run " + control_entry()["run_id"] + " started " + control_entry()["started_utc"])
        self.assertEqual(seal.scan_transcripts(self.h.root, self.h.environ), [])
        self.h.put("heldout-002-b", "see " + self.h.gates["C7"]["token"])
        self.assertEqual(seal.scan_transcripts(self.h.root, self.h.environ), [("heldout-002-b", "C7 token")])

    def test_a_token_on_the_c1_5_entry_is_not_read(self):
        stray = "cd" * 32
        put(self.h.environ, **{KEY: dict(control_entry(), token=stray), "C6": self.h.gates["C6"], "C7": self.h.gates["C7"]})
        self.h.put("heldout-001-a", "see " + stray)
        self.assertEqual(seal.scan_transcripts(self.h.root, self.h.environ), [])


if __name__ == "__main__":
    unittest.main()
