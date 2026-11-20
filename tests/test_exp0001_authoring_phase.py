"""Offline tests for the authoring agent's own spend bound in EXP-0001.

The `author` command charges a ledger phase of its own, `authoring`, capped at $3.00 (4 calls at $0.75) by a
constant in the code (config.json's phase_caps stay as pinned), counted under the total ceiling and not under the
generation cap. Nothing here calls the real claude: a fake process object stands in for the launch.
Run with: python3 -m unittest tests.test_exp0001_authoring_phase
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import exp0001_paths  # noqa: E402

import claude_run  # noqa: E402
from tests import test_exp0001_author as at  # noqa: E402
from tests import test_exp0001_isolation as iso  # noqa: E402


class TestAuthoringPhase(at.AuthorCase, unittest.TestCase):
    """The author command's run is charged to `authoring`; the cap, the ceiling and the rebuild know the phase."""

    def cfg(self, **extra):
        return dict(iso.CFG, model="m-1", **extra)

    def write_spend(self, control=0.0, **phases):
        root = claude_run.eval_root(self.environ)
        root.mkdir(parents=True, exist_ok=True)
        (root / "spend.json").write_text(json.dumps({"total": control, "phases": phases}), encoding="utf-8")

    def launch(self, phase, cfg=None):
        at.FakeChild.launches = []
        sandbox = claude_run.make_sandbox("t", self.environ)
        with mock.patch.object(claude_run.subprocess, "Popen", at.FakeChild):
            return claude_run.run_claude(cfg or self.cfg(), "PROMPT", sandbox, label="t", phase=phase,
                                         kind="probe", notools=True, environ=self.environ)

    def test_i_an_author_run_charges_authoring_by_its_cost_and_generation_by_zero(self):
        at.FakeChild.launches = []
        with mock.patch.object(claude_run.subprocess, "Popen", at.FakeChild):
            self.author(runner=None)
        self.assertEqual(len(at.FakeChild.launches), 1)
        authoring = claude_run.spend_total(self.environ, "authoring")
        self.assertGreater(authoring, 0)
        self.assertEqual(claude_run.spend_total(self.environ, "generation"), 0.0)
        phases = json.loads((claude_run.eval_root(self.environ) / "spend.json").read_text(encoding="utf-8"))["phases"]
        self.assertEqual(phases, {"authoring": authoring})

    def test_ii_generation_is_unaffected_and_the_total_counts_authoring(self):
        self.write_spend(authoring=3.0, generation=14.0)
        self.assertEqual(claude_run.spend_total(self.environ, "generation"), 14.0)
        rec = self.launch("generation")  # positive control: authoring at its bound leaves generation its own cap
        self.assertEqual(rec["phase"], "generation")
        self.assertEqual(len(at.FakeChild.launches), 1)
        self.assertEqual(claude_run.spend_total(self.environ, "authoring"), 3.0)
        self.assertEqual(claude_run.spend_sum(self.environ), 3.0 + claude_run.spend_total(self.environ, "generation"))
        self.write_spend(control=2.0, authoring=3.0, generation=10.0, evaluation=30.0)
        self.assertEqual(claude_run.spend_sum(self.environ), 45.0)
        with self.assertRaises(claude_run.SpendCapReached) as caught:  # 45 reaches the ceiling only with authoring
            self.launch("generation", self.cfg(spend_ceiling_usd=45))
        self.assertIn("spend ceiling reached", str(caught.exception))
        self.assertEqual(at.FakeChild.launches, [])
        self.write_spend(control=2.0, generation=10.0, evaluation=30.0)  # the control: 42 of 45 launches
        self.launch("generation", self.cfg(spend_ceiling_usd=45))
        self.assertEqual(len(at.FakeChild.launches), 1)

    def test_iii_authoring_is_refused_at_its_bound_by_the_shared_launch_check(self):
        self.assertEqual(claude_run.phase_cap(self.cfg(), "authoring"), 3.0)
        self.write_spend(authoring=3.0)
        with self.assertRaises(claude_run.SpendCapReached) as caught:
            self.launch("authoring")
        self.assertEqual(str(caught.exception), "spend cap reached")
        self.assertEqual(at.FakeChild.launches, [])
        self.write_spend(authoring=2.25)
        self.launch("authoring")
        self.assertEqual(len(at.FakeChild.launches), 1)
        self.assertEqual(claude_run.spend_total(self.environ, "authoring"), 2.25 + 0.75)

    def test_iv_the_four_call_counter_times_the_per_run_budget_is_the_bound(self):
        self.assertEqual(at.author.MAX_RUNS * float(iso.CFG["max_budget_usd_per_run"]),
                         claude_run.phase_cap(self.cfg(), "authoring"))
        self.proof_ok = False
        for _ in range(4):
            self.assertEqual(self.author()[0], 1)
        code, text = self.author()
        self.assertEqual(code, 1)
        self.assertIn("author: STOP the author run budget is spent (4 runs)", text)
        self.assertEqual(len(self.calls), 4)
        self.assertEqual({kw["phase"] for _, kw in self.calls}, {"authoring"})

    def test_v_the_rebuild_restores_an_authoring_total(self):
        root = claude_run.eval_root(self.environ)
        root.mkdir(parents=True, exist_ok=True)
        iso.write_record(root, "a1", "s1", "authoring", False, 0.5, 1)
        iso.write_record(root, "a2", "s2", "authoring", False, 0.25, 2)
        iso.write_record(root, "g1", "s3", "generation", False, 1.0, 3)
        self.write_spend(control=0.0, authoring=9.0)
        out = io.StringIO()
        self.assertEqual(claude_run.spend_rebuild(self.environ, write=True, out=out, cfg=dict(iso.CFG)), 0)
        data = json.loads((root / "spend.json").read_text(encoding="utf-8"))
        self.assertAlmostEqual(data["phases"]["authoring"], 0.75)
        self.assertAlmostEqual(data["phases"]["generation"], 1.0)
        self.assertIn("authoring", out.getvalue())

    def test_the_committed_config_keeps_its_three_phase_caps_and_no_authoring_key(self):
        cfg = json.loads((Path(exp0001_paths.CODE) / "config.json").read_text(encoding="utf-8"))  # load_config is patched here
        self.assertEqual(cfg["phase_caps"], {"control": 8.5, "generation": 19.5, "evaluation": 34.0})
        self.assertEqual(claude_run.phase_cap(cfg, "authoring"), 3.0)


class TestAuthoringSpendTotals(unittest.TestCase):
    def test_authoring_is_its_own_ledger_not_control_s_or_generation_s(self):
        self.assertIn("authoring", claude_run.PHASES)
        self.assertEqual(claude_run.ledger_of("authoring"), "authoring")
        with tempfile.TemporaryDirectory() as t:
            environ = {"TMPDIR": str(Path(t).resolve())}
            claude_run.eval_root(environ).mkdir(parents=True)
            claude_run.add_spend(0.5, environ, phase="authoring")
            self.assertEqual(claude_run.spend_total(environ, "authoring"), 0.5)
            self.assertEqual(claude_run.spend_total(environ, "control"), 0.0)
            self.assertEqual(claude_run.spend_total(environ, "generation"), 0.0)
            self.assertEqual(claude_run.spend_sum(environ), 0.5)
