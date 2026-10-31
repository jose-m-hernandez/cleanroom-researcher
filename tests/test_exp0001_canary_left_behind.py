"""Offline tests for the report of a changed canary file: the canary command prints a line and records
canary_left_behind in the gate entry; the outcome itself does not change.

The fake runner of tests/test_exp0001_seal.py is used by calling setUp on an instance of its test class, never by
subclassing it or importing the class name (either would run its tests twice).
"""

from __future__ import annotations

import unittest

from tests import exp0001_paths  # noqa: F401

from tests import test_exp0001_seal_canary as st  # noqa: E402


class CanaryLeftBehindTests(unittest.TestCase):
    def setUp(self):
        self.h = st.CanaryCommandTests("gates")
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)

    def test_a_changed_canary_is_reported_with_its_path_and_recorded(self):
        code, text = self.h.canary(replace_file=True)
        path = self.h.seen[0]["canary_path"]
        self.assertTrue(path.exists())  # the replacement was not removed
        self.assertIn(f"canary left behind (changed): remove it by hand: {path}\n", text)
        self.assertIs(self.h.gates()["C6"]["canary_left_behind"], True)
        self.assertEqual((code, self.h.gates()["C6"]["outcome"]), (0, "PASS"))  # the outcome is unchanged

    def test_a_normal_run_prints_no_such_line_and_records_false(self):
        code, text = self.h.canary()
        self.assertEqual(code, 0, text)
        self.assertNotIn("left behind", text)
        self.assertIs(self.h.gates()["C6"]["canary_left_behind"], False)

    def test_a_failing_run_with_a_changed_canary_records_true_on_the_fail_entry(self):
        code, text = self.h.canary(behaviour="allowed", replace_file=True)
        self.assertEqual(code, 1, text)
        self.assertIn("canary left behind (changed): remove it by hand: ", text)
        self.assertEqual(self.h.gates()["C6"]["outcome"], "FAIL")
        self.assertIs(self.h.gates()["C6"]["canary_left_behind"], True)

    def test_an_error_entry_gets_no_new_key_and_an_unchanged_canary_no_line(self):
        code, text = self.h.canary(behaviour="raise")
        self.assertNotEqual(code, 0, text)
        self.assertNotIn("left behind", text)
        self.assertEqual(self.h.gates()["C6"]["outcome"], "ERROR")
        self.assertNotIn("canary_left_behind", self.h.gates()["C6"])

if __name__ == "__main__":
    unittest.main()
