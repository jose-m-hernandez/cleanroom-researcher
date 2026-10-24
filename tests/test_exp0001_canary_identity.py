"""Offline tests for how the seal step removes its canary file: only when the file is still the one it made.

Nothing here calls the real claude: each test makes its own folder and files.
"""

from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

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


    def test_a_different_file_holding_the_exact_token_is_left_alone(self):
        token = os.urandom(32).hex()
        path, ident = seal.create_canary_file(self.config, token)
        stand_in = self.tmp / "stand-in.txt"
        stand_in.write_text(token + "\n", encoding="utf-8")  # same bytes, new inode
        os.replace(stand_in, path)
        self.assertNotEqual(os.lstat(path).st_ino, ident[0])
        self.assertFalse(seal.remove_canary_file(path, ident))
        self.assertEqual(path.read_text(encoding="utf-8"), token + "\n")

    def test_a_symlink_whose_own_identity_is_given_is_neither_followed_nor_removed(self):
        token = os.urandom(32).hex()
        path, _ = seal.create_canary_file(self.config, token)
        target = self.tmp / "symlink-target.txt"
        target.write_text(token + "\n", encoding="utf-8")
        path.unlink()
        os.symlink(target, path)
        st = os.lstat(path)  # the link's own inode and device, with the right token
        self.assertFalse(seal.remove_canary_file(path, (st.st_ino, st.st_dev, token)))
        self.assertTrue(os.path.islink(path))
        self.assertEqual(target.read_text(encoding="utf-8"), token + "\n")

    def test_a_fifo_at_the_canary_path_does_not_block_the_open(self):
        token = os.urandom(32).hex()
        path, _ = seal.create_canary_file(self.config, token)
        path.unlink()
        os.mkfifo(path)
        st = os.lstat(path)
        ident = (st.st_ino, st.st_dev, token)
        real = os.lstat
        regular = os.stat_result((0o100600,) + tuple(st)[1:])  # a FIFO that passes the identity and regular-file checks

        def lstat(p, *a, **k):
            return regular if str(p) == str(path) else real(p, *a, **k)
        result = []
        with mock.patch.object(seal.os, "lstat", lstat):
            thread = threading.Thread(target=lambda: result.append(seal.remove_canary_file(path, ident)), daemon=True)
            thread.start()
            thread.join(5)
            alive = thread.is_alive()
            if alive:  # release a blocked open so the thread ends
                fd = os.open(str(path), os.O_RDWR)
                os.close(fd)
                thread.join(5)
        self.assertFalse(alive, "the open blocked on a FIFO")
        self.assertEqual(result, [False])
        self.assertTrue(os.path.lexists(path))


if __name__ == "__main__":
    unittest.main()
