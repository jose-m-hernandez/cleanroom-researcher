"""Offline tests for the clean-room lint of EXP-0001 (P:249): `lint.command_lint` over a fixture store.

Every test passes an explicit environ holding a synthetic barred list, built from parts so this file holds none of
the shapes it looks for. Nothing here reads the real list or calls claude.
"""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path

from tests import exp0001_paths  # noqa: F401

import lint  # noqa: E402

CANARY = "barred-strings-" + "canary-" + "t3st0001"
TERM = "zq-" + "canary-7f3"
LIST = CANARY + "\n" + TERM + "\n"
HOME_PATH = "/Us" + "ers/" + "alice/notes"
ID_SHAPE = "HA" + "R" + "-001"


class LintTests(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.tmp = Path(td.name).resolve()
        self.root = self.tmp / "store"
        for fid in ("dev-001", "dev-002"):
            (self.root / fid).mkdir(parents=True)
            (self.root / fid / "spec.json").write_text('{"id": "%s"}\n' % fid, encoding="utf-8")
        self.environ = {"BARRED_STRINGS": LIST}

    def run_lint(self, root=None, environ=None):
        out = io.StringIO()
        code = lint.command_lint(root or self.root, self.environ if environ is None else environ, out)
        return code, out.getvalue()

    def test_a_missing_list_is_dead_never_a_pass(self):
        code, text = self.run_lint(environ={})
        self.assertEqual(code, 2)
        self.assertTrue(text.startswith("lint: DEAD"), text)

    def test_a_first_entry_that_is_not_a_canary_is_dead(self):
        code, text = self.run_lint(environ={"BARRED_STRINGS": TERM + "\n"})
        self.assertEqual(code, 2)
        self.assertIn("lint: DEAD", text)

    def test_a_clean_store_passes_with_its_file_count(self):
        code, text = self.run_lint()
        self.assertEqual(code, 0, text)
        self.assertEqual(text, "lint: ok (2 files, 2 entries, canary matched)\n")

    def test_a_file_holding_a_term_fails_by_entry_number_and_never_prints_it(self):
        (self.root / "dev-001" / "notes.txt").write_text("a line\nhas " + TERM + " in it\n", encoding="utf-8")
        code, text = self.run_lint()
        self.assertEqual(code, 1)
        self.assertIn("entry #2", text)
        self.assertIn("notes.txt:2", text)
        self.assertNotIn(TERM, text)
        self.assertIn("lint: FAIL (1 findings)", text)

    def test_a_home_path_fails_rule_v4(self):
        (self.root / "dev-001" / "notes.txt").write_text("see " + HOME_PATH + "\n", encoding="utf-8")
        code, text = self.run_lint()
        self.assertEqual(code, 1)
        self.assertIn("notes.txt:1: V4", text)
        self.assertNotIn(HOME_PATH, text)

    def test_a_reserved_id_shape_fails_rule_v1(self):
        (self.root / "dev-002" / "notes.txt").write_text("ticket " + ID_SHAPE + "\n", encoding="utf-8")
        code, text = self.run_lint()
        self.assertEqual(code, 1)
        self.assertIn("notes.txt:1: V1", text)
        self.assertNotIn(ID_SHAPE, text)

    def test_a_git_folder_and_a_symlink_are_findings_and_not_followed(self):
        (self.root / "dev-001" / ".git").mkdir()
        (self.root / "dev-001" / ".git" / "config").write_text("see " + TERM + "\n", encoding="utf-8")
        os.symlink(self.root / "dev-002" / "spec.json", self.root / "dev-001" / "link.json")
        code, text = self.run_lint()
        self.assertEqual(code, 1)
        self.assertIn("lint: dev-001/.git: git folder (a fixture store holds none; not scanned)", text)
        self.assertIn("lint: dev-001/link.json: symlink (not followed)", text)
        self.assertNotIn("entry #", text)  # the git folder was not scanned
        self.assertIn("lint: FAIL (2 findings)", text)

    def test_a_file_name_holding_a_term_is_a_path_finding(self):
        (self.root / "dev-001" / (TERM + ".txt")).write_text("clean\n", encoding="utf-8")
        code, text = self.run_lint()
        self.assertEqual(code, 1)
        self.assertIn("path holds entry #2", text)
        self.assertNotIn(TERM, text)

    def test_a_missing_root_is_refused(self):
        code, text = self.run_lint(root=self.tmp / "nowhere")
        self.assertEqual(code, 2)
        self.assertIn("REFUSED", text)

    def test_a_symlinked_root_is_refused(self):
        os.symlink(self.root, self.tmp / "alias")
        code, text = self.run_lint(root=self.tmp / "alias")
        self.assertEqual(code, 2)
        self.assertIn("REFUSED", text)


if __name__ == "__main__":
    unittest.main()
