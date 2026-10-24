"""Offline tests for how the seal step removes its canary file: only when the file is still the one it made.

Nothing here calls the real claude: each test makes its own folder and files.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from tests import exp0001_paths  # noqa: F401

import seal  # noqa: E402


class CanaryFileIdentityTests(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.tmp = Path(td.name).resolve()
        self.config = self.tmp / "config"
        self.config.mkdir()

    def test_a_replacement_that_reuses_the_inode_is_left_alone(self):
        token = os.urandom(32).hex()
        path, _ = seal.create_canary_file(self.config, token)
        path.unlink()
        path.write_text("other\n", encoding="utf-8")
        st = os.lstat(path)
        ident = (st.st_ino, st.st_dev, token)  # what ext4 inode reuse would look like
        self.assertFalse(seal.remove_canary_file(path, ident))
        self.assertTrue(path.exists())
        self.assertEqual(path.read_text(encoding="utf-8"), "other\n")

    def test_a_symlink_at_the_canary_path_is_neither_followed_nor_removed(self):
        token = os.urandom(32).hex()
        path, _ = seal.create_canary_file(self.config, token)
        target = self.tmp / "symlink-target.txt"
        target.write_text(token + "\n", encoding="utf-8")
        path.unlink()
        os.symlink(target, path)
        st = os.stat(target)
        ident = (st.st_ino, st.st_dev, token)  # a check that followed the link would pass everything
        self.assertFalse(seal.remove_canary_file(path, ident))
        self.assertTrue(os.path.islink(path))
        self.assertTrue(target.exists())
        self.assertEqual(target.read_text(encoding="utf-8"), token + "\n")


if __name__ == "__main__":
    unittest.main()
