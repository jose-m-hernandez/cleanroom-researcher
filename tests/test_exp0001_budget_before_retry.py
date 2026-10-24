"""Offline test that the pilot budget is checked before an isolation re-run, not only before each cell.

The scripted claude of tests/test_exp0001_score.py stands in for the live service.
"""

from __future__ import annotations

import unittest
from unittest import mock

from tests import exp0001_paths  # noqa: F401

import claude_run  # noqa: E402
import probes  # noqa: E402
from tests import test_exp0001_plumbing as plumbing  # noqa: E402


class BudgetBeforeRetryTests(plumbing.Scripted):  # Scripted defines no tests
    def test_a_budget_used_up_by_attempt_one_stops_the_batch_before_the_re_run(self):
        self.script = {1: plumbing.mcp_loaded}  # the baseline preserve run (cost 0.2) fails the isolation proof
        with self.assertRaises(probes.BatchStop) as ctx:
            self.go(budget_usd=0.2)
        self.assertEqual(str(ctx.exception), "budget reached")
        self.assertEqual(len(self.calls()), 1)  # no second launch
        self.assertAlmostEqual(claude_run.spend_total(self.environ, "evaluation"), 0.2)

    def test_with_room_left_the_re_run_still_happens(self):
        self.script = {1: plumbing.mcp_loaded}
        self.assertEqual(self.go(budget_usd=5.0), 0)
        self.assertEqual(len(self.preserve_calls()), 2)

    def test_run_cell_calls_before_retry_only_before_the_second_attempt(self):
        seen = []

        def once():
            seen.append("once")
            return {"record": {"proof": {"failures": [], "isolation_failures": ["x"], "outcome_failures": []}}}
        cell = {"kind": "probe", "arm": "floor", "fixture": "f", "seed": 1}
        with mock.patch.object(probes.arms, "write_raw"), mock.patch.object(probes, "log_proof"):
            probes.run_cell(self.out, cell, once, self.environ, lambda: seen.append("check"))
        self.assertEqual(seen, ["once", "check", "once"])


if __name__ == "__main__":
    unittest.main()
