"""Offline tests for the scan of the texts the seal copies unscanned: each spec's excluded block, the count rule and
the repair log. A hit refuses with exit 2, names the field and the kind, never the matched text, and writes nothing.

The held-out world of tests/test_exp0001_seal.py is built by calling its setUp on an instance, never by subclassing.
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

from tests import exp0001_paths  # noqa: F401

import seal  # noqa: E402
from tests import test_exp0001_seal as st  # noqa: E402

HOME_PATH = "/Us" + "ers/" + "alice/notes"  # built from parts: the vocabulary rule V4 reads this file too


class SealTextScanTests(unittest.TestCase):
    def setUp(self):
        self.w = st.World("setUp")
        self.w.setUp()
        self.addCleanup(self.w.doCleanups)

    def excluded_spec(self, reason):
        path = self.w.store / st.EXTRA / "spec.json"
        spec = json.loads(path.read_text(encoding="utf-8"))
        spec["excluded"]["reason"] = reason
        path.write_text(json.dumps(spec), encoding="utf-8")

    def excluded_extra(self, key, value):
        path = self.w.store / st.EXTRA / "spec.json"
        spec = json.loads(path.read_text(encoding="utf-8"))
        spec["excluded"][key] = value
        path.write_text(json.dumps(spec), encoding="utf-8")

    def plant(self, field, kind):
        text = st.SENTINEL.upper() if kind == "account name" else HOME_PATH
        if field == "count rule":
            self.w.rule.write_text(st.COUNT_RULE + f"see {text}\n", encoding="utf-8")
        elif field == "repair log":
            self.w.repair.write_text(f"repair log: see {text}\n", encoding="utf-8")
        else:
            self.excluded_spec(f"leaks through {text}")
        return text

    def test_each_field_and_kind_refuses_names_the_field_and_writes_nothing(self):
        for field in (f"excluded block of {st.EXTRA}", "count rule", "repair log"):
            for kind in ("account name", "home path"):
                with self.subTest(field=field, kind=kind):
                    self.setUp()  # a fresh world per case
                    text = self.plant(field, kind)
                    out = self.w.refused(2, field)
                    self.assertIn(kind, out)
                    self.assertNotIn(text, out)
                    self.assertNotIn(text.casefold(), out.casefold())

    def check_excluded_refusal(self):
        out = self.w.refused(2, f"excluded block of {st.EXTRA}")
        self.assertIn("home path", out)
        self.assertNotIn(HOME_PATH, out)

    def test_a_path_after_any_line_break_in_a_reason_still_refuses(self):
        for sep in ("\n", "\t", "\r\n", "\x0b"):
            with self.subTest(sep=repr(sep)):
                self.setUp()  # a fresh world per case
                self.excluded_spec("pasted output" + sep + HOME_PATH)
                self.check_excluded_refusal()

    def test_a_path_in_a_nested_list_refuses(self):
        self.excluded_extra("log", ["ok", ["line one\n" + HOME_PATH]])
        self.check_excluded_refusal()

    def test_a_path_in_a_key_refuses(self):
        self.excluded_extra("\n" + HOME_PATH, "x")
        self.check_excluded_refusal()

    def test_the_block_text_helper_walks_keys_and_strings_in_order(self):
        self.assertEqual(seal.excluded_block_text({"a": 1, "b": [None, "x", True], "k": {"y": "z"}}), "a\nb\nx\nk\ny\nz")

    def test_a_clean_seal_still_passes(self):
        self.w.sealed()
        self.assertEqual(self.w.files_under(self.w.seal_dir),
                         ["manifest.txt", "record.json", "repair-log/repair.log", "seal.json"])

    def test_the_helper_names_fields_not_text_and_refuses_an_unknown_account(self):
        self.assertEqual(seal.copied_text_hits({"a": "clean", "b": HOME_PATH}), [("b", "home path")])
        with mock.patch.object(seal.fixtures, "_current_username", side_effect=seal.fixtures.UsernameError("x")):
            with self.assertRaises(seal.SealStop):
                seal.copied_text_hits({"a": "clean"})


if __name__ == "__main__":
    unittest.main()
