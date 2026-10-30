"""Offline tests for the --from and --out rules inside the library entry points (score, report, pilot-md): a results
folder inside the clone is refused there too, not only by the command line.

Nothing here calls the real claude; every refusal comes before any read or write.
"""

from __future__ import annotations

import io
import unittest
from pathlib import Path
from unittest import mock

from tests import exp0001_paths  # noqa: F401

import claude_run  # noqa: E402
import pilot  # noqa: E402
import report  # noqa: E402
import score  # noqa: E402
from tests import test_exp0001_isolation as iso  # noqa: E402
from tests import test_exp0001_score as base  # noqa: E402


class FromInTheLibraryTests(iso.CloneFolders):
    def setUp(self):
        super().setUp()
        patch = mock.patch.object(claude_run, "ROOT", self.clone)
        patch.start()
        self.addCleanup(patch.stop)

    def score(self, source):
        shown = io.StringIO()
        code = score.command_score(source, base.FIXTURES, {"TMPDIR": str(self.tmp)}, shown)
        return code, shown.getvalue()

    def report(self, source, out_dir=None):
        shown = io.StringIO()
        return report.write_report(source, out_dir, shown), shown.getvalue()

    def pilot_md(self, source, out_dir=None):
        shown = io.StringIO()
        return pilot.command_pilot_md(source, None, out_dir, shown), shown.getvalue()

    def test_score_refuses_a_from_inside_the_clone_before_any_read(self):
        inside = self.clone / "other" / "run-1"
        code, text = self.score(inside)
        self.assertEqual(code, 2, text)
        self.assertTrue(text.startswith("score: REFUSED --from is inside the repository clone"), text)
        self.assertFalse(inside.exists())

    def test_score_refuses_a_missing_from(self):
        code, text = self.score(None)
        self.assertEqual((code, text.startswith("score: REFUSED --from is required")), (2, True), text)

    def test_report_refuses_a_from_inside_the_clone_and_writes_nothing(self):
        code, text = self.report(self.clone / "other")
        self.assertEqual(code, 2, text)
        self.assertTrue(text.startswith("report: REFUSED --from is inside the repository clone"), text)
        self.assertEqual(list((self.clone / "other").iterdir()), [])

    def test_report_refuses_an_out_dir_inside_the_clone(self):
        code, text = self.report(self.outside, self.clone / "other" / "r")
        self.assertEqual(code, 2, text)
        self.assertTrue(text.startswith("report: REFUSED --out is inside the repository clone"), text)
        self.assertFalse((self.clone / "other" / "r").exists())

    def test_pilot_md_refuses_a_from_inside_the_clone(self):
        code, text = self.pilot_md(self.clone / "other")
        self.assertEqual(code, 2, text)
        self.assertTrue(text.startswith("pilot-md: REFUSED --from is inside the repository clone"), text)

    def test_pilot_md_refuses_an_out_dir_inside_the_clone(self):
        code, text = self.pilot_md(self.outside, self.clone / "other" / "p")
        self.assertEqual(code, 2, text)
        self.assertTrue(text.startswith("pilot-md: REFUSED --out is inside the repository clone"), text)
        self.assertFalse((self.clone / "other" / "p").exists())

    def test_a_folder_outside_the_clone_and_one_under_recorded_pass_the_rule(self):
        for source in (self.outside, self.recorded):
            for code, text in (self.report(source), self.pilot_md(source)):
                self.assertNotEqual(code, 2, text)  # they go on to read, and stop on the missing run.json
                self.assertNotIn("REFUSED", text)


if __name__ == "__main__":
    unittest.main()
