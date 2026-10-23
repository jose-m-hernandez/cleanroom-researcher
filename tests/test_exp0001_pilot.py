"""Tests for the pilot command's refusals and helpers, on a scripted claude and a fake judge.

Run with: python3 -m unittest discover tests
"""

from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests import exp0001_paths  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]

import claude_run  # noqa: E402
import pilot  # noqa: E402
from tests import test_exp0001_score as base  # noqa: E402


class PilotEnv(base.Env):
    def setUp(self):
        super().setUp()
        self.judge = base.FakeJudge(base.SPECS)
        self.script = {}
        self.launched = 0
        self.sleeps = []

    def runner(self, cfg, prompt, sandbox, **kw):
        rec = claude_run.run_claude(cfg, prompt, sandbox, **kw)
        self.launched += 1
        edit = self.script.get(self.launched)
        if edit:
            edit(rec)
        return rec


class CommandTests(PilotEnv):
    def setUp(self):
        super().setUp()
        for name, fake in (("preflight", lambda cfg, environ=None: ""), ("load_config", lambda path=None: self.cfg)):
            original = getattr(claude_run, name)
            setattr(claude_run, name, fake)
            self.addCleanup(setattr, claude_run, name, original)

    def command(self, budget, environ=None, **kw):
        out = io.StringIO()
        code = pilot.command_pilot(base.FIXTURES, self.out, "dev", "dev-001..dev-002", ["floor", "baseline", "oracle"], 1,
                                   budget, "20260101", environ or self.environ, out, runner=self.runner,
                                   label_probe=self.judge.label_probe, label_items=self.judge.label_items,
                                   sleep=self.sleeps.append, head=("0123abc", True), **kw)
        return code, out.getvalue()

    def test_a_budget_above_the_pilot_cap_refuses_before_any_call(self):
        code, text = self.command(30.01)
        self.assertEqual(code, 2)
        self.assertIn("REFUSED --budget-usd", text)
        for bad in (0, -1, float("nan")):
            self.assertEqual(self.command(bad)[0], 2)
        self.assertEqual((self.launched, self.calls()), (0, []))
        self.assertFalse(self.out.exists())


class HelperTests(unittest.TestCase):
    def test_p90_is_nearest_rank(self):
        self.assertIsNone(pilot.p90([]))
        self.assertEqual(pilot.p90([5]), 5)
        self.assertEqual(pilot.p90(list(range(1, 11))), 9)
        self.assertEqual(pilot.p90([3, 1, 2]), 3)

    def test_a_batch_failure_message_yields_its_labels(self):
        self.assertEqual(pilot.failed_unit_labels("dev-001 seed 1 baseline preserve failed: a,b"), ["a", "b"])
        self.assertEqual(pilot.failed_unit_labels("dev-001 seed 2 floor probe failed: result_error"), ["result_error"])
        self.assertIsNone(pilot.failed_unit_labels("budget reached"))

    def test_the_cli_refuses_a_budget_above_the_cap_and_a_missing_live_flag(self):
        env = {"PATH": "/usr/bin:/bin"}
        run = lambda *a: subprocess.run([sys.executable, exp0001_paths.RUN, "pilot", *a], capture_output=True,
                                        text=True, encoding="utf-8", env=env)
        with tempfile.TemporaryDirectory() as out:  # --out is required and must lie outside the clone
            proc = run("--budget-usd", "34.01", "--out", out)  # the evaluation cap is 34
            self.assertEqual(proc.returncode, 2)
            self.assertIn("REFUSED", proc.stdout)
            proc = run("--budget-usd", "34", "--out", out)
            self.assertEqual(proc.returncode, 2)
            self.assertIn("live run refused", proc.stdout)
            self.assertNotEqual(run("--out", out).returncode, 0)  # --budget-usd is required


if __name__ == "__main__":
    unittest.main()
