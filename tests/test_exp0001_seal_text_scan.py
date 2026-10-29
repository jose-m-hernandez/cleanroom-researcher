"""Offline tests for the scan of the texts the seal copies unscanned: each spec's excluded block, the count rule and
the repair log. A hit refuses with exit 2, names the field and the kind, never the matched text, and writes nothing.

It also covers a normalized transcript reaching the seal.

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
WIN = "C:" + "\\Us" + "ers\\"  # a Windows profile prefix, from parts for the same reason


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

    ESCAPES = ("\\n", "\\t", "\\r\\n")  # the two-character JSON forms, never a real line break

    def test_a_path_after_a_pasted_json_escape_in_the_repair_log_refuses(self):
        for esc in self.ESCAPES:
            with self.subTest(esc=esc):
                self.setUp()  # a fresh world per case
                self.w.repair.write_text('repair log: evidence {"result": "done' + esc + HOME_PATH + '"}\n',
                                         encoding="utf-8")
                out = self.w.refused(2, "repair log")
                self.assertIn("home path", out)
                self.assertNotIn(HOME_PATH, out)

    def test_a_path_after_a_pasted_json_escape_in_the_count_rule_refuses(self):
        for esc in self.ESCAPES:
            with self.subTest(esc=esc):
                self.setUp()  # a fresh world per case
                self.w.rule.write_text(st.COUNT_RULE + "see" + esc + HOME_PATH + "\n", encoding="utf-8")
                out = self.w.refused(2, "count rule")
                self.assertIn("home path", out)
                self.assertNotIn(HOME_PATH, out)

    def test_a_path_after_a_json_escape_inside_a_walked_string_refuses(self):
        self.excluded_spec("pasted" + "\\n" + HOME_PATH)  # json.dumps writes it; the walk sees the two characters
        self.check_excluded_refusal()

    def test_the_helper_reports_each_kind_once_per_field_with_the_decoded_copy(self):
        got = seal.copied_text_hits({"a": "x" + "\\b" + HOME_PATH, "b": "plain " + HOME_PATH, "c": "clean"})
        self.assertEqual(got, [("a", "home path"), ("b", "home path")])

    def test_the_raw_copy_catches_a_profile_folder_that_starts_with_an_escape_letter(self):
        for folder in ("build", "files", "notes", "repo", "tools"):
            with self.subTest(folder=folder):
                self.assertEqual(seal.copied_text_hits({"a": "see " + WIN + folder}), [("a", "home path")])

    def test_a_profile_path_in_the_repair_log_refuses_end_to_end(self):
        self.w.repair.write_text("repair log: see " + WIN + "notes\n", encoding="utf-8")
        out = self.w.refused(2, "repair log")
        self.assertIn("home path", out)
        self.assertNotIn(WIN + "notes", out)

    def test_a_bare_backslash_r_or_f_before_a_path_still_hits(self):
        for esc in ("\\r", "\\f"):
            with self.subTest(esc=esc):
                self.assertEqual(seal.copied_text_hits({"a": "done" + esc + HOME_PATH}), [("a", "home path")])

    def test_the_account_name_is_reported_once_per_field(self):
        self.assertEqual(seal.copied_text_hits({"a": "x " + st.SENTINEL + " y"}), [("a", "account name")])

    def test_the_account_name_is_reported_before_the_home_path(self):
        self.assertEqual(seal.copied_text_hits({"a": HOME_PATH + " " + st.SENTINEL}),
                         [("a", "account name"), ("a", "home path")])

    def test_the_block_text_helper_walks_a_dict_inside_a_list(self):
        self.assertEqual(seal.excluded_block_text({"l": [{"k": "v"}, "w"]}), "l\nk\nv\nw")


class NormalizeThenSealTests(unittest.TestCase):
    def setUp(self):
        self.w = st.World("setUp")
        self.w.setUp()
        self.addCleanup(self.w.doCleanups)

    def normalized_row(self, form):
        row = {"type": "user", "uuid": "u", "parentUuid": None,
               "message": {"content": [{"type": "text", "text": "Hi " + form + ", done"}]}}
        return st.fixtures.normalize_transcript([row], [], "", environ=self.w.environ)

    def append_row(self, out):
        with open(self.w.store / st.HIDS[0] / "transcript.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(out[0]) + "\n")

    def test_a_capitalised_name_is_normalized_away_and_the_seal_passes(self):
        for form in (st.SENTINEL.title(), st.SENTINEL.upper()):
            with self.subTest(form=form):
                self.setUp()  # a fresh world per case
                out = self.normalized_row(form)
                self.assertNotIn(form.casefold(), json.dumps(out).casefold())
                self.append_row(out)
                self.w.sealed()

    def test_a_glued_name_survives_normalization_and_the_seal_refuses(self):
        form = st.SENTINEL.title() + "_x"
        out = self.normalized_row(form)
        self.assertIn(form, json.dumps(out))
        self.append_row(out)
        text = self.w.refused(1, "(username)")
        self.assertNotIn(st.SENTINEL.casefold(), text.casefold())


if __name__ == "__main__":
    unittest.main()
