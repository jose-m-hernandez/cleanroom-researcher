"""Offline tests for the home-path shape of the seal's copied-text scan: a users or home folder counts only as the
first segment of a path, so web routes pass, and the other home shapes still refuse.

Every string is built from parts: the vocabulary rule V4 reads this file too.
"""

from __future__ import annotations

import unittest
from unittest import mock

from tests import exp0001_paths  # noqa: F401

import fixtures  # noqa: E402
import seal  # noqa: E402

U = "/us" + "ers"
UP = "/Us" + "ers"
VAR = "$" + "HO" + "ME/x"


class HomeShapeTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(fixtures, "_current_username", return_value="nobody-by-this-name")
        patcher.start()
        self.addCleanup(patcher.stop)

    def hits(self, text):
        return seal.copied_text_hits({"f": text})

    def test_web_routes_with_a_users_segment_after_the_first_do_not_hit(self):
        for text in ("/v2" + U + "/me", "/api" + U + "/list", "/v2" + U + "/{id}", "https://x.example" + U + "/abc", "/v2/{id}" + U + "/me"):
            self.assertEqual(self.hits(text), [], text)

    def test_first_segment_and_other_home_shapes_hit(self):
        for text in (UP + "/a/x", U + "/a/x", "file://" + UP + "/a", "path=/ho" + "me/a/x", "(~" + "/x)", VAR):
            self.assertEqual(self.hits(text), [("f", "home path")], text)

    def test_the_stated_limit_a_method_word_before_a_users_route_still_hits(self):
        self.assertEqual(self.hits("GET " + U + "/me"), [("f", "home path")])

    def test_any_character_before_the_slash_but_a_segment_character_opens_a_path(self):
        for char in ("`", "[", "<", ">", ",", "|", ";", "{", "@", "\\", "!", "'", ":", "("):
            with self.subTest(char=char):
                self.assertEqual(self.hits("x" + " " + char + UP + "/a"), [("f", "home path")])
                self.assertEqual(self.hits(char + UP + "/a"), [("f", "home path")])

    def test_a_folder_name_after_the_double_slash_of_any_scheme_hits(self):
        self.assertEqual(self.hits("http:/" + "/ho" + "me/x"), [("f", "home path")])

    def test_the_windows_profile_shapes_hit(self):
        for text in ("C:" + "\\Us" + "ers\\" + "a", "%USER" + "PROFILE%"):
            with self.subTest(text=len(text)):
                self.assertEqual(self.hits(text), [("f", "home path")])


if __name__ == "__main__":
    unittest.main()
