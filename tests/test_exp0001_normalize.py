"""Tests for the text normalization of the EXP-0001 runner. Run with: python3 -m unittest discover tests"""

from __future__ import annotations

import unittest

from tests import exp0001_paths  # noqa: F401

from normalize import normalize


class NormalizeTests(unittest.TestCase):
    def test_zero_width_characters_and_nul_are_dropped(self):
        self.assertEqual(normalize("zq​canary⁠" + "7731"), "zqcanary7731")
        self.assertEqual(normalize("zq\x00canary7731"), "zqcanary7731")
        self.assertEqual(normalize("a‌‍﻿b"), "ab")

    def test_fullwidth_letters_fold_to_ascii(self):
        word = "zqcanary7731"
        fullwidth = "".join(chr(ord(c) + 0xFEE0) for c in word)
        self.assertNotEqual(fullwidth, word)
        self.assertEqual(normalize(fullwidth), word)

    def test_a_non_breaking_space_becomes_a_plain_space(self):
        self.assertEqual(normalize("a b"), "a b")
        self.assertEqual(normalize("/dir "), "/dir ")

    def test_plain_text_is_unchanged(self):
        text = "A plain line, with café and 'quotes' and digits 123.\n"
        self.assertEqual(normalize(text), text)


if __name__ == "__main__":
    unittest.main()
