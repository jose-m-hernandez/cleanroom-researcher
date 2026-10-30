"""Offline tests for what the canary command says when the canary file is gone, replaced by a dangling symlink, or
cannot be removed: no false "left behind" line for a missing file, one for a dangling link, and a line that says why.

The fake runner of tests/test_exp0001_seal.py is used by calling setUp on an instance of its test class, never by
subclassing it or importing the class name (either would run its tests twice).
"""

from __future__ import annotations

import io
import os
import unittest

from tests import exp0001_paths  # noqa: F401

import seal  # noqa: E402
from tests import test_exp0001_seal as st  # noqa: E402


class CanaryGoneTests(unittest.TestCase):
    def setUp(self):
        self.h = st.CanaryCommandTests("gates")
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)

    def canary_after(self, act):
        """Run the preserve canary; after the inner run returns, act(canary path) changes what is at the path."""
        inner = self.h.runner()

        def wrapped(*args, **kw):
            rec = inner(*args, **kw)
            act(self.h.seen[-1]["canary_path"])
            return rec
        out = io.StringIO()
        code = seal.command_canary("preserve", None, self.h.environ, out, wrapped)
        return code, out.getvalue()

    def test_a_canary_that_is_already_gone_is_not_reported_as_left_behind(self):
        code, text = self.canary_after(lambda path: path.unlink())
        self.assertEqual(code, 0, text)
        self.assertNotIn("left behind", text)
        self.assertIs(self.h.gates()["C6"]["canary_left_behind"], False)

    def test_a_dangling_symlink_in_its_place_is_reported(self):
        def act(path):
            path.unlink()
            os.symlink(str(path.parent / "no-such-target"), str(path))
        code, text = self.canary_after(act)
        self.assertEqual(code, 0, text)
        self.assertIn("canary left behind", text)
        self.assertIs(self.h.gates()["C6"]["canary_left_behind"], True)

    @unittest.skipIf(os.geteuid() == 0, "root ignores folder permissions")
    def test_a_canary_that_could_not_be_removed_says_so_and_not_changed(self):
        def act(path):
            self.addCleanup(os.chmod, str(path.parent), 0o700)  # registered first: runs even if an assertion fails
            os.chmod(str(path.parent), 0o500)
        code, text = self.canary_after(act)
        self.assertEqual(code, 0, text)
        self.assertIn("canary left behind (could not remove): remove it by hand: ", text)
        self.assertNotIn("(changed)", text)
        self.assertIs(self.h.gates()["C6"]["canary_left_behind"], True)


if __name__ == "__main__":
    unittest.main()
