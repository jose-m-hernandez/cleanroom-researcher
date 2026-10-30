"""Offline tests for the canary command and its real-runner path (C6 and C7): the file, the tools, the gate state,
never re-run. Moved from test_exp0001_seal.py for that file's size; the tests are unchanged apart from the C1-5 entry.
"""

from __future__ import annotations

import copy
import datetime
import io
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

from tests import exp0001_paths  # noqa: F401

import claude_run  # noqa: E402
import isolation  # noqa: E402
import seal  # noqa: E402
from tests import test_exp0001_isolation as iso  # noqa: E402
from tests.test_exp0001_seal import (  # noqa: E402  (none of these defines a test_ method)
    CanaryRun, FakeHome, Quiet, REFUSED_LIVE, canary_name, entry, spec_with_bash, write_gates)


class CanaryCommandTests(FakeHome):
    """command_canary with a fake runner standing in for run_claude."""

    def setUp(self):
        super().setUp()
        (self.tmp / "fx").mkdir()
        self.fx = iso.RunFixture(self.tmp / "fx", kind="canary")
        self.events0, self.rows0 = copy.deepcopy(self.fx.events), copy.deepcopy(self.fx.rows)
        patches = [mock.patch.object(claude_run, "load_config", lambda path=None: dict(iso.CFG)),
                   mock.patch.object(claude_run, "preflight", lambda cfg, environ=None: ""),
                   mock.patch.object(claude_run, "export_plugin", lambda ref, environ=None, overlay_skill=None: self.fx.export)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.seen = []
        self.token_in_file = None
        self.c15 = write_gates(self.environ)  # the C1-5 PASS entry: preserve needs it

    def runner(self, behaviour="deny", sandbox_ok=True, replace_file=False):
        """A runner that finds the canary file in the fake config folder and plays one of the canary outcomes."""
        def run(cfg, prompt, sandbox, **kw):
            files = sorted(self.config.glob("cpr-eval-canary-*.txt"))
            self.assertEqual(len(files), 1, "the canary file is in place during the run")
            path = files[0]
            self.token_in_file = path.read_text(encoding="utf-8").strip()
            self.seen.append(dict(kw, prompt=prompt, sandbox=sandbox, canary_path=path))
            fx = self.fx
            fx.events[:] = copy.deepcopy(self.events0)  # every run starts from a clean record
            fx.rows[:] = copy.deepcopy(self.rows0)
            fx.init = next(e for e in fx.events if e.get("subtype") == "init")
            fx.sandbox = Path(sandbox)
            fx.init["memory_paths"] = {"auto": str(Path(sandbox) / "mem")}
            fx.rows[0]["attachment"]["files"][0]["path"] = str(Path(sandbox) / "CLAUDE.md")
            fx.run.update(sandbox=str(sandbox), kind=kw["kind"], started_utc="2026-10-23T01:02:03Z", run_id="canary-test01",
                          plugin=str(kw["plugin"]))
            cr = CanaryRun.__new__(CanaryRun)
            cr.fx, cr.path, cr.token, cr.result, cr.n = fx, str(path), self.token_in_file, fx.events[-1], 0
            cr.result["result"] = (f"sandbox: {(Path(sandbox) / seal.SANDBOX_NOTE).read_text(encoding='utf-8').strip()}"
                                   if sandbox_ok else "sandbox: unreadable")
            if behaviour == "deny":
                cr.call()
            elif behaviour == "leak":
                cr.call()
                cr.result["result"] += " " + self.token_in_file
            elif behaviour == "allowed":
                cr.call(outcome="allowed")
            elif behaviour == "raise":
                kw["on_launch"]()  # ruling 2.1: a crash after the launch has fired the spawn-point callback
                raise RuntimeError("the runner broke")
            elif behaviour == "spend":
                raise claude_run.SpendCapReached("spend cap reached")
            if replace_file:
                path.unlink()
                path.write_text("someone else's file\n", encoding="utf-8")
            fx.write_transcript()
            return dict(fx.run)
        return run

    def canary(self, canary_type="preserve", root=None, **kw):
        out = io.StringIO()
        code = seal.command_canary(canary_type, root, self.environ, out, self.runner(**kw))
        return code, out.getvalue()

    def gates(self):
        return json.loads((claude_run.eval_root(self.environ) / "gates.json").read_text(encoding="utf-8"))

    def test_a_pass_is_recorded_in_gates_json_and_the_file_is_removed(self):
        code, text = self.canary()
        self.assertEqual(code, 0, text)
        gates = self.gates()
        self.assertEqual(set(gates), {"C1-5", "C6"})
        self.assertEqual((gates["C6"]["outcome"], gates["C6"]["run_id"], gates["C6"]["started_utc"]),
                         ("PASS", "canary-test01", "2026-10-23T01:02:03Z"))
        self.assertEqual(gates["C6"]["token"], self.token_in_file)  # the scan needs the token the file held
        self.assertEqual(list(self.config.glob("cpr-eval-canary-*")), [])
        self.assertEqual(len(self.token_in_file), 64)  # 256 bits, hex
        self.assertRegex(self.token_in_file, r"^[0-9a-f]{64}$")

    def test_the_command_prints_the_outcome_and_the_run_id_only(self):
        code, text = self.canary()
        self.assertIn("PASS", text)
        self.assertIn("canary-test01", text)
        self.assertNotIn(self.token_in_file, text)
        self.assertNotIn(str(self.home), text)

    def test_the_canary_file_lies_in_the_runner_config_folder_outside_the_sandbox(self):
        self.canary()
        call = self.seen[0]
        self.assertEqual(call["canary_path"].parent, self.config)
        self.assertEqual(call["canary_path"].name, canary_name(self.token_in_file))
        self.assertFalse(isolation._inside(call["canary_path"], call["sandbox"]))
        self.assertIn(str(call["canary_path"]), call["prompt"])
        self.assertTrue((Path(call["sandbox"]) / seal.SANDBOX_NOTE).is_file())  # the positive control is inside
        self.assertNotIn(self.token_in_file, call["prompt"])

    def test_the_run_has_its_own_kind_and_the_tools_of_a_preserve_run(self):
        self.canary()
        call = self.seen[0]
        self.assertEqual((call["kind"], call["phase"], call["label"]), ("canary", "control", "canary-preserve"))
        self.assertIsNone(call["allowed_tools"])  # a preserve run passes no Bash tools
        self.assertIsNotNone(call["plugin"])
        self.assertNotIn("probe", call)

    def test_c7_gets_the_union_of_the_specs_allowed_bash(self):
        write_gates(self.environ, C6="PASS")
        root = self.tmp / "specs"
        spec_with_bash(root, "dev-001-a", ["git status", "ls *"])
        spec_with_bash(root, "dev-002-b", ["wc -l *", "ls *", "cat *"])
        code, text = self.canary("generation", root)
        self.assertEqual(code, 0, text)
        self.assertEqual(self.seen[0]["allowed_tools"], ["Bash(cat *)", "Bash(git status)", "Bash(ls *)", "Bash(wc -l *)"])
        self.assertIn("dev-001-a, dev-002-b", text)  # the report names the specs that contributed
        self.assertEqual(self.seen[0]["kind"], "canary")
        self.assertEqual(set(self.gates()), {"C1-5", "C6", "C7"})

    def test_union_bash_is_sorted_deduplicated_and_names_its_specs(self):
        root = self.tmp / "specs"
        spec_with_bash(root / "dev", "dev-001-a", ["git status", "ls *"])
        spec_with_bash(root / "heldout", "heldout-001-b", ["wc -l *", "ls *"])
        self.assertEqual(seal.union_bash(root), (["git status", "ls *", "wc -l *"], ["dev-001-a", "heldout-001-b"]))
        with self.assertRaises(seal.SealStop):
            seal.union_bash(self.tmp / "empty")

    def test_c7_with_no_allowed_bash_anywhere_stops_before_the_file_is_made(self):
        write_gates(self.environ, C6="PASS")
        code, text = self.canary("generation", self.tmp / "none")
        self.assertEqual(code, 1)
        self.assertEqual((self.seen, list(self.config.glob("cpr-eval-canary-*"))), ([], []))
        self.assertEqual(set(self.gates()), {"C1-5", "C6"})

    def test_fail_outcomes_are_recorded_and_exit_1(self):
        for behaviour in ("leak", "allowed"):
            with self.subTest(behaviour=behaviour):
                write_gates(self.environ)  # back to the C1-5 entry alone (was: delete gates.json)
                code, text = self.canary(behaviour=behaviour)
                self.assertEqual(code, 1)
                self.assertEqual(self.gates()["C6"]["outcome"], "FAIL")
                self.assertNotIn(self.token_in_file, text)

    def test_a_failed_positive_control_is_a_fail(self):
        code, _ = self.canary(sandbox_ok=False)
        self.assertEqual((code, self.gates()["C6"]["outcome"]), (1, "FAIL"))

    def test_a_prose_decline_is_recorded_as_error(self):
        code, text = self.canary(behaviour="none")
        self.assertEqual((code, self.gates()["C6"]["outcome"]), (1, "ERROR"))
        self.assertIn("no_tool_call_aimed_at_canary", text)

    def test_a_canary_is_never_re_run(self):
        for outcome in ("PASS", "FAIL", "ERROR"):
            with self.subTest(outcome=outcome):
                write_gates(self.environ, C6=outcome)
                self.seen.clear()
                before = (claude_run.eval_root(self.environ) / "gates.json").read_bytes()
                code, text = self.canary()
                self.assertEqual(code, 2, text)
                self.assertIn("never re-run", text)
                self.assertEqual(self.seen, [])  # nothing launched
                self.assertEqual((claude_run.eval_root(self.environ) / "gates.json").read_bytes(), before)
        write_gates(self.environ, C6="PASS", C7="FAIL")
        code, text = self.canary("generation", self.tmp)
        self.assertEqual(code, 2, text)
        self.assertEqual(self.seen, [])

    def test_c7_needs_c6_to_have_passed(self):
        for gates in ({}, {"C6": "FAIL"}, {"C6": "ERROR"}):
            with self.subTest(gates=gates):
                write_gates(self.environ, **gates)
                code, text = self.canary("generation", self.tmp)
                self.assertEqual(code, 2, text)
                self.assertEqual(self.seen, [])

    def test_a_malformed_gates_file_refuses_and_is_not_overwritten(self):
        path = claude_run.eval_root(self.environ) / "gates.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        code, text = self.canary()
        self.assertEqual(code, 2, text)
        self.assertEqual(path.read_text(encoding="utf-8"), "{not json")

    def test_a_runner_that_breaks_after_the_file_exists_records_error_and_removes_the_file(self):
        code, text = self.canary(behaviour="raise")
        self.assertEqual(code, 1)
        self.assertIn("ERROR RuntimeError", text)
        self.assertEqual(self.gates()["C6"]["outcome"], "ERROR")
        self.assertEqual(list(self.config.glob("cpr-eval-canary-*")), [])

    def test_a_refusal_before_the_launch_records_nothing_and_the_canary_can_still_run(self):
        code, text = self.canary(behaviour="spend")
        self.assertEqual(code, 1)
        self.assertEqual(self.gates(), self.c15)  # no C6 entry; C1-5 unchanged
        self.assertEqual(list(self.config.glob("cpr-eval-canary-*")), [])
        code, _ = self.canary()
        self.assertEqual(code, 0)

    def test_the_canary_file_is_left_alone_when_another_file_replaced_it(self):
        code, _ = self.canary(replace_file=True)
        self.assertEqual(code, 0)
        left = list(self.config.glob("cpr-eval-canary-*"))
        self.assertEqual(len(left), 1)
        self.assertEqual(left[0].read_text(encoding="utf-8"), "someone else's file\n")

    def test_create_and_remove_work_on_the_same_inode_only(self):
        token = os.urandom(32).hex()
        path, ident = seal.create_canary_file(self.config, token)
        self.assertEqual(path.name, canary_name(token))
        self.assertEqual(path.read_text(encoding="utf-8").strip(), token)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        with self.assertRaises(FileExistsError):  # created exclusively, never overwritten
            seal.create_canary_file(self.config, token)
        self.assertEqual(path.read_text(encoding="utf-8").strip(), token)
        self.assertTrue(seal.remove_canary_file(path, ident))
        self.assertFalse(path.exists())
        self.assertFalse(seal.remove_canary_file(path, ident))  # already gone
        path2, ident2 = seal.create_canary_file(self.config, token)
        path2.unlink()
        path2.write_text("other\n", encoding="utf-8")
        self.assertFalse(seal.remove_canary_file(path2, ident2))
        self.assertTrue(path2.exists())

    def test_runner_config_dir_is_the_folder_the_host_checks_read(self):
        self.assertEqual(seal.runner_config_dir(self.environ), self.config)
        self.assertEqual(isolation.host_memory_files(self.environ)[0].parent, seal.runner_config_dir(self.environ))
        other = self.tmp / "other-config"
        other.mkdir()
        (other / "CLAUDE.md").write_text("x\n", encoding="utf-8")
        env = dict(self.environ, CLAUDE_CONFIG_DIR=str(other))
        self.assertEqual(seal.runner_config_dir(env), other)
        self.assertEqual(isolation.host_memory_files(env)[0].parent, other)

    def test_a_config_folder_inside_the_sandbox_stops_the_canary(self):
        with mock.patch.object(claude_run, "make_sandbox", lambda label, environ=None: self.home):  # holds .claude
            code, text = self.canary()
        self.assertEqual(code, 1)
        self.assertIn("inside the sandbox", text)
        self.assertEqual((self.seen, list(self.config.glob("cpr-eval-canary-*"))), ([], []))

    def test_a_missing_config_folder_stops_the_canary(self):
        shutil.rmtree(self.config)
        code, text = self.canary()
        self.assertEqual(code, 1)
        self.assertEqual(self.seen, [])

    def test_the_canary_is_gated_like_every_launch(self):
        env = {k: v for k, v in self.environ.items() if k != "CPR_EVAL_LIVE"}
        out = io.StringIO()
        code = seal.command_canary("preserve", None, env, out, self.runner())
        self.assertEqual(code, 2)
        self.assertIn(REFUSED_LIVE, out.getvalue())
        self.assertEqual(self.seen, [])

    def test_the_canary_goes_through_run_claude_by_default(self):
        class LaunchedThenQuiet(Quiet):
            def __call__(self, *args, **kwargs):
                kwargs["on_launch"]()  # ruling 2.1: the stand-in raises after the launch, so it fires the callback first
                return super().__call__(*args, **kwargs)
        gate = LaunchedThenQuiet()
        with mock.patch.object(claude_run, "run_claude", gate):
            out = io.StringIO()
            code = seal.command_canary("preserve", None, self.environ, out, None)
        self.assertEqual(len(gate.calls), 1)  # the default runner is claude_run.run_claude
        self.assertEqual(gate.calls[0][1]["kind"], "canary")
        self.assertEqual(self.gates()["C6"]["outcome"], "ERROR")  # the stand-in raised after the file existed
        self.assertEqual(code, 1)


class CanaryRealRunnerTests(FakeHome):
    """Ruling 2.1: _canary_run with the real claude_run.run_claude. ERROR is recorded only for an exception raised
    after the launch; a refusal before the spawn leaves no entry and the canary can still run."""

    def setUp(self):
        super().setUp()
        self.cfg = dict(iso.CFG)
        self.export = self.tmp / "export"
        self.export.mkdir()
        patch = mock.patch.object(claude_run, "export_plugin", lambda ref, environ=None, overlay_skill=None: self.export)
        patch.start()
        self.addCleanup(patch.stop)
        self.order = []
        self.c15 = write_gates(self.environ)  # the C1-5 PASS entry: preserve needs it

    def gates_path(self):
        return claude_run.eval_root(self.environ) / "gates.json"

    def left(self):
        return list(self.config.glob("cpr-eval-canary-*"))

    def drive(self, spawn, cfg=None, environ=None):
        """_canary_run with the real run_claude; claude_run.subprocess.Popen is replaced by spawn only for that call
        (the sandbox's git setup runs before it, through the same module)."""
        def runner(*args, on_launch=None, **kwargs):
            def launched():
                self.order.append("on_launch")
                on_launch()
            with mock.patch.object(claude_run.subprocess, "Popen", spawn):
                return claude_run.run_claude(*args, on_launch=launched, **kwargs)
        return seal._canary_run("preserve", self.tmp, cfg or self.cfg, environ or self.environ, runner, io.StringIO())

    def refused_before_the_spawn(self, exc_class, **kw):
        spawn = Quiet()
        with self.assertRaises(exc_class):
            self.drive(spawn, **kw)
        self.assertEqual(spawn.calls, [])  # never launched
        self.assertEqual(self.order, [])  # and the spawn-point callback never ran
        self.assertEqual(json.loads(self.gates_path().read_text(encoding="utf-8")), self.c15)  # no C6 entry: the canary is not spent
        self.assertEqual(self.left(), [])  # the canary file is gone
        self.assertEqual(seal.canary_refusal("preserve", self.environ), "")

    def write_spend(self, text):
        path = claude_run.eval_root(self.environ) / "spend.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_live_refused_before_the_spawn_records_nothing(self):
        env = {k: v for k, v in self.environ.items() if k != "CPR_EVAL_LIVE"}
        self.refused_before_the_spawn(claude_run.LiveRefused, environ=env)

    def test_config_error_before_the_spawn_records_nothing(self):
        for label, cfg in (("tempconfig", dict(self.cfg, isolation="tempconfig")),
                           ("no phase cap", {k: v for k, v in self.cfg.items() if k != "phase_caps"}),
                           ("no spend ceiling", {k: v for k, v in self.cfg.items() if k != "spend_ceiling_usd"})):
            with self.subTest(label):
                self.refused_before_the_spawn(claude_run.ConfigError, cfg=cfg)

    def test_spend_error_before_the_spawn_records_nothing(self):
        self.write_spend("{not json")
        self.refused_before_the_spawn(claude_run.SpendError)

    def test_spend_cap_reached_before_the_spawn_records_nothing(self):
        for label, text in (("phase cap", json.dumps({"total": 3.0, "phases": {}})),
                            ("ceiling", json.dumps({"total": 0, "phases": {"generation": 60.0}}))):
            with self.subTest(label):
                self.write_spend(text)
                self.refused_before_the_spawn(claude_run.SpendCapReached)

    def test_memory_dir_error_before_the_spawn_records_nothing(self):
        make = claude_run.make_sandbox

        def sandbox_with_memory(label, environ=None):
            sandbox = make(label, environ)
            mem = isolation.auto_memory_dir(self.config, sandbox)
            mem.mkdir(parents=True)
            (mem / "old.md").write_text("left over\n", encoding="utf-8")
            return sandbox
        with mock.patch.object(claude_run, "make_sandbox", sandbox_with_memory):
            self.refused_before_the_spawn(claude_run.MemoryDirError)

    def test_a_spawn_that_fails_is_a_spawn_error_and_the_canary_is_not_spent(self):
        def no_claude(*args, **kwargs):
            self.order.append("subprocess.Popen")
            raise FileNotFoundError("claude")
        with self.assertRaises(claude_run.SpawnError) as caught:
            self.drive(no_claude)
        self.assertIsInstance(caught.exception, OSError)
        self.assertIsInstance(caught.exception.__cause__, FileNotFoundError)
        self.assertEqual(self.order, ["subprocess.Popen"])  # no child, so the callback never ran
        self.assertEqual(json.loads(self.gates_path().read_text(encoding="utf-8")), self.c15)  # no C6 entry
        self.assertEqual(self.left(), [])
        self.assertEqual(seal.canary_refusal("preserve", self.environ), "")

    def test_a_crash_after_the_launch_records_error(self):
        kills = []

        class Child:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def communicate(self, timeout=None):
                raise RuntimeError("the child broke")

            def kill(self):
                kills.append(1)

        def spawn(*args, **kwargs):
            self.order.append("subprocess.Popen")
            return Child()
        with self.assertRaises(RuntimeError):
            self.drive(spawn)
        self.assertEqual(self.order, ["subprocess.Popen", "on_launch"])  # the callback ran only after the child existed
        self.assertEqual(kills, [1])
        self.assertEqual(json.loads(self.gates_path().read_text(encoding="utf-8"))["C6"]["outcome"], "ERROR")
        self.assertEqual(self.left(), [])

    def test_an_oserror_after_the_child_started_is_error_not_a_spawn_error(self):
        kills = []

        class Child:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def communicate(self, timeout=None):
                raise OSError(5, "EIO")

            def kill(self):
                kills.append(1)

        def spawn(*args, **kwargs):
            return Child()
        with self.assertRaises(OSError) as caught:
            self.drive(spawn)
        self.assertNotIsInstance(caught.exception, claude_run.SpawnError)  # the raw OSError, not "not launched"
        self.assertEqual(caught.exception.errno, 5)
        self.assertEqual(json.loads(self.gates_path().read_text(encoding="utf-8"))["C6"]["outcome"], "ERROR")
        self.assertEqual(kills, [1])
        self.assertEqual(self.order, ["on_launch"])
        self.assertEqual(self.left(), [])

    def test_a_non_oserror_before_any_child_is_not_a_launch(self):
        def bad_env(*args, **kwargs):
            raise ValueError("embedded null byte")
        with self.assertRaises(claude_run.SpawnError) as caught:
            self.drive(bad_env)
        self.assertIsInstance(caught.exception.__cause__, ValueError)
        self.assertEqual(json.loads(self.gates_path().read_text(encoding="utf-8")), self.c15)  # no C6 entry: not spent
        self.assertEqual(self.order, [])  # on_launch never ran
        self.assertEqual(self.left(), [])
        self.assertEqual(seal.canary_refusal("preserve", self.environ), "")

    def test_the_real_popen_with_a_stub_claude_calls_on_launch_once(self):
        stub = self.tmp / "stub"
        stub.mkdir()
        claude = stub / "claude"
        claude.write_text('#!/bin/sh\necho \'{"type": "result", "subtype": "success", "total_cost_usd": 0.01}\'\n',
                          encoding="utf-8")
        claude.chmod(claude.stat().st_mode | stat.S_IEXEC)
        launched = []
        env = dict(self.environ, PATH=str(stub) + os.pathsep + self.environ["PATH"])
        sandbox = claude_run.make_sandbox("t", env)
        rec = claude_run.run_claude(iso.CFG, "hello", sandbox, label="t", phase="control", environ=env,
                                    on_launch=lambda: launched.append(1))
        self.assertEqual(launched, [1])
        self.assertEqual(rec["returncode"], 0)
        self.assertFalse(rec["timed_out"])
        self.assertEqual(rec["events"][0]["type"], "result")

    def stub_on_path(self, body):
        stub = Path(tempfile.mkdtemp(dir=self.tmp))
        claude = stub / "claude"
        claude.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        claude.chmod(claude.stat().st_mode | stat.S_IEXEC)
        return dict(self.environ, PATH=str(stub) + os.pathsep + self.environ["PATH"])

    def spied_run(self, env, **kwargs):
        """run_claude with the real Popen, keeping every instance it returns."""
        real, kept = subprocess.Popen, []

        def spy(*args, **kw):
            child = real(*args, **kw)
            kept.append(child)
            return child
        sandbox = claude_run.make_sandbox("t", env)  # the sandbox's git setup runs before the spy
        with mock.patch.object(claude_run.subprocess, "Popen", spy):
            rec = claude_run.run_claude(dict(iso.CFG, probe_timeout_s=1), "hello", sandbox, label="t",
                                        phase="control", environ=env, **kwargs)
        self.assertEqual(len(kept), 1)
        return rec, kept[0]

    def test_a_timeout_returns_in_time_even_when_a_grandchild_holds_the_pipes(self):
        pidfile = self.tmp / "grandchild.pid"
        env = self.stub_on_path(f'sleep 30 &\necho $! > "{pidfile}"\nexec sleep 30\n')

        def reap():
            if pidfile.is_file() and pidfile.read_text(encoding="utf-8").strip():
                try:
                    os.kill(int(pidfile.read_text(encoding="utf-8").strip()), 9)  # SIGKILL: the signal module is not on this suite's import list
                except ProcessLookupError:
                    pass
        self.addCleanup(reap)
        t0 = datetime.datetime.now()
        rec, child = self.spied_run(env, probe=True)
        elapsed = (datetime.datetime.now() - t0).total_seconds()
        self.assertTrue(rec["timed_out"])
        self.assertLess(elapsed, 1 + 4)  # not held for the grandchild's 30 seconds
        self.assertIsNone(rec["returncode"])
        self.assertEqual(rec["stderr_chars"], 0)
        self.assertEqual(rec["events"], [])

    def test_pipes_are_closed_and_the_child_is_reaped_after_a_normal_run(self):
        env = self.stub_on_path('echo \'{"type": "result", "subtype": "success"}\'\n')
        rec, child = self.spied_run(env)
        self.assertFalse(rec["timed_out"])
        self.assertIsNotNone(child.returncode)
        self.assertTrue(child.stdout.closed and child.stderr.closed)

    def test_pipes_are_closed_and_the_child_is_reaped_after_a_timeout(self):
        env = self.stub_on_path("exec sleep 30\n")
        rec, child = self.spied_run(env, probe=True)
        self.assertTrue(rec["timed_out"])
        self.assertIsNotNone(child.returncode)
        self.assertTrue(child.stdout.closed and child.stderr.closed)

    def test_a_ctrl_c_after_the_launch_records_error(self):
        class Child:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def communicate(self, timeout=None):
                raise KeyboardInterrupt

            def kill(self):
                pass

        def spawn(*args, **kwargs):
            return Child()
        with self.assertRaises(KeyboardInterrupt):
            self.drive(spawn)
        self.assertEqual(self.order, ["on_launch"])
        self.assertEqual(json.loads(self.gates_path().read_text(encoding="utf-8"))["C6"]["outcome"], "ERROR")
        self.assertEqual(self.left(), [])

    def test_a_ctrl_c_before_any_child_records_nothing(self):
        def interrupted(*args, **kwargs):
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.drive(interrupted)
        self.assertEqual(self.order, [])  # on_launch never ran
        self.assertEqual(json.loads(self.gates_path().read_text(encoding="utf-8")), self.c15)  # no C6 entry
        self.assertEqual(self.left(), [])
