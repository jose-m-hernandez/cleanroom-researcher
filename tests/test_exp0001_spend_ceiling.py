"""Offline tests for the spend ceiling and the fork charge: the rebuild charges a fork in full, and the control and
the batch stop at the whole-run ceiling even when no launch would.

The helpers of tests/test_exp0001_isolation.py are used by calling setUp on instances of its test classes, never by
subclassing them or importing the class names (either would run their tests twice).
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from tests import exp0001_paths  # noqa: F401

import claude_run  # noqa: E402
import probes  # noqa: E402
from tests import test_exp0001_isolation as iso  # noqa: E402
from tests import test_exp0001_plumbing as plumbing  # noqa: E402


class ForkChargeTests(unittest.TestCase):
    def test_the_rebuild_charges_a_fork_that_reported_no_new_id_in_full(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        tmp = Path(td.name).resolve()
        environ = {"TMPDIR": str(tmp)}
        root = claude_run.eval_root(environ)
        root.mkdir(parents=True)
        (root / "spend.json").write_text(json.dumps({"total": 0.0, "phases": {}}), encoding="utf-8")
        iso.write_record(root, "p1", "s1", "generation", False, 1.0, 1)
        iso.write_record(root, "p2", "s1", "generation", True, 1.5, 2, forked=True)
        code = claude_run.spend_rebuild(environ, write=True, out=io.StringIO(), cfg=dict(iso.CFG))
        self.assertEqual(code, 0)
        data = json.loads((root / "spend.json").read_text(encoding="utf-8"))
        self.assertAlmostEqual(data["phases"]["generation"], 2.5)  # a plain resume would count 1.5

    def test_run_claude_marks_a_fork_and_not_a_plain_resume(self):
        h = iso.FakeClaudeTests("test_allowed_tools_and_fork_session_reach_the_command")
        h.setUp()
        self.addCleanup(h.doCleanups)
        sandbox = claude_run.make_sandbox("t", h.environ)
        sid = "00000000-0000-4000-8000-000000000001"
        prior = {"session_id": sid, "sandbox": str(sandbox), "memory_dir_after": []}
        forked = claude_run.run_claude(dict(iso.CFG), "q", sandbox, label="t", phase="generation", probe=True,
                                       resume=sid, fork_session=True, prior=prior, extra_session_ids=(sid,),
                                       environ=h.environ)
        plain = claude_run.run_claude(dict(iso.CFG), "q", sandbox, label="t", phase="generation", probe=True,
                                      resume=sid, prior=prior, extra_session_ids=(sid,), environ=h.environ)
        self.assertIs(forked["forked"], True)
        self.assertIs(plain["forked"], False)


class CeilingBeforeLaunchTests(unittest.TestCase):
    def write_ledgers(self, environ, control, generation, evaluation):
        root = claude_run.eval_root(environ)
        root.mkdir(parents=True, exist_ok=True)
        (root / "spend.json").write_text(
            json.dumps({"total": control, "phases": {"generation": generation, "evaluation": evaluation}}),
            encoding="utf-8")

    def test_the_control_stops_at_the_ceiling_while_its_own_cap_has_room(self):
        h = iso.ControlTests("test_spend_cap_stops_the_control")
        h.setUp()
        self.addCleanup(h.doCleanups)
        h.write_config(spend_ceiling_usd=10)
        self.write_ledgers(h.environ, 1.0, 4.0, 5.0)  # control 1.0 of its 3.0 cap, the sum exactly 10
        runner = iso.ScriptedRunner(h.tmp, h.ledger)
        code, out = h.run_control(runner)
        self.assertEqual(code, 1, out)
        self.assertEqual(runner.calls, [])
        self.assertIn("control: STOP spend ceiling reached", out)

    def test_the_control_cap_is_still_reported_first(self):
        h = iso.ControlTests("test_spend_cap_stops_the_control")
        h.setUp()
        self.addCleanup(h.doCleanups)
        h.write_config(spend_ceiling_usd=10)
        self.write_ledgers(h.environ, 3.0, 4.0, 5.0)
        runner = iso.ScriptedRunner(h.tmp, h.ledger)
        code, out = h.run_control(runner)
        self.assertEqual((code, runner.calls), (1, []))
        self.assertIn("control: STOP spend cap reached", out)

    def test_the_batch_stops_at_the_ceiling_with_no_budget_and_launches_nothing(self):
        h = plumbing.Scripted("runner")
        h.setUp()
        self.addCleanup(h.doCleanups)
        h.cfg["spend_ceiling_usd"] = 10
        self.write_ledgers(h.environ, 1.0, 4.0, 5.0)  # evaluation 5.0 of its 30.0 cap
        launched = []

        def runner(*args, **kw):
            launched.append(kw.get("label"))
            raise AssertionError("launched")
        with self.assertRaises(probes.BatchStop) as ctx:
            probes.run_batch(plumbing.base.FIXTURES, h.out, "dev", list(plumbing.IDS[:1]), ["floor", "baseline"], 1,
                             h.cfg, h.environ, runner=runner, date="20260101", stdout=io.StringIO())
        self.assertEqual(str(ctx.exception), "spend ceiling reached")
        self.assertEqual(launched, [])


if __name__ == "__main__":
    unittest.main()
