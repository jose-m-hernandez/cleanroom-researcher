"""Tests for fixture loading and validation, evidence, transcript normalization and the username scrub.

The fixtures these read are the small invented ones under tests/data/exp0001-fixtures/. Run with:
python3 -m unittest discover tests
"""

from __future__ import annotations

import copy
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from tests import exp0001_paths  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "tests" / "data" / "exp0001-fixtures"  # <split>/<id>/spec.json, as the loader expects

import claude_run  # noqa: E402
import fixtures  # noqa: E402
import isolation  # noqa: E402

PILOT = {
    "dev-001-tide-table": "readings-file",
    "dev-002-recipe-scaler": "scaler",
    "dev-003-greenhouse-log": "sensor-log",
    "dev-004-chess-clock": "game",
    "dev-005-bird-count": "tally",
}


def pilot(sid="dev-001-tide-table") -> dict:
    return fixtures.load_spec(DATA / "dev" / sid / "spec.json")


def labels(spec, directory=None) -> list:
    return fixtures.validate_spec(spec, directory_name=directory)


class PilotSetTests(unittest.TestCase):
    def test_the_five_pilot_specs_validate_and_cover_all_categories(self):
        problems, specs, _excluded = fixtures.validate_all(DATA)
        self.assertEqual(problems, [])
        self.assertTrue(set(PILOT) <= {s["id"] for s in specs})
        self.assertEqual(sum(1 for n in fixtures.category_counts(specs).values() if n >= 2), 6)

    def test_pilot_shapes_turns_and_tool_items(self):
        self.assertGreaterEqual(len(set(PILOT.values())), 3)
        specs = {sid: pilot(sid) for sid in PILOT}
        for sid, spec in specs.items():
            with self.subTest(sid=sid):
                self.assertTrue(any(i.get("source") == "tool" for i in spec["items"]))
                if not fixtures.is_long(spec):
                    self.assertTrue(6 <= len(spec["turns"]) <= 10, len(spec["turns"]))
        self.assertGreaterEqual(sum(fixtures.is_long(s) for s in specs.values()), 1)

    def test_pilot_content_is_generic(self):
        for sid in PILOT:
            text = (DATA / "dev" / sid / "spec.json").read_text(encoding="utf-8")
            with self.subTest(sid=sid):
                for address in re.findall(r"[\w.+-]+@[\w.-]+", text):
                    self.assertTrue(address.endswith(("@example.com", "@example.org", "@example.net")), address)
                self.assertNotRegex(text, "/" + "Users" + "/|/" + "home" + "/|" + "~" + "/\\.")  # built from parts: the vocabulary check flags a home path

    def test_no_pilot_turn_signposts_and_each_has_two_mid_turn_items(self):
        for sid in PILOT:
            spec = pilot(sid)
            with self.subTest(sid=sid):
                text = "\n".join(spec["turns"]).casefold()
                for phrase in fixtures.SIGNPOSTS:
                    self.assertNotIn(phrase, text)
                self.assertGreaterEqual(len(fixtures.mid_turn_items(spec)), 2)

    def test_labels_are_neutral(self):
        banned = {"correction", "lesson", "episodic", "kind", "candidate", "thought"}
        for sid in PILOT:
            spec = pilot(sid)
            keys = {k for i in spec["items"] for k in i} | {k for p in spec["probes"] for k in p}
            self.assertEqual(keys & banned, set(), sid)

    def test_cli_validate_prints_the_expected_line(self):
        # The command line takes no fixtures root, so the child process points the module's FIXTURES_DIR at the data.
        child = ("import sys; sys.path[:0] = [%r, %r]; import fixtures, run; from pathlib import Path; "
                 "fixtures.FIXTURES_DIR = Path(%r); sys.exit(run.main(['validate']))"
                 % (exp0001_paths.CODE, exp0001_paths.RUNNER, str(DATA)))
        proc = subprocess.run([sys.executable, "-c", child], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "validate: 5 fixtures OK (dev 5, heldout 0); 1 excluded: dev-006-tram-timetable; "
                         "categories covered 6/6")


class SpecRuleTests(unittest.TestCase):
    """Each rule has a failing input."""

    def setUp(self):
        self.spec = pilot()

    def assertLabel(self, label, spec=None, directory=None):
        found = labels(spec if spec is not None else self.spec, directory)
        self.assertIn(label, found)

    def test_base_spec_is_valid(self):
        self.assertEqual(labels(self.spec, "dev-001-tide-table"), [])

    def test_ids_split_and_directory(self):
        self.spec["id"] = "Bad ID"
        self.assertLabel("id_invalid")
        self.spec = pilot()
        self.spec["split"] = "heldout"
        self.assertLabel("split_mismatch")
        self.spec = pilot()
        self.assertLabel("id_directory_mismatch", directory="dev-009-other")

    def test_top_level_keys_are_closed_and_required(self):
        self.spec["extra"] = 1
        self.assertLabel("unknown_top_level_key")
        spec = pilot()
        del spec["turns"]
        self.assertLabel("missing_key:turns", spec)

    def test_the_excluded_marker_has_one_shape(self):
        self.spec["excluded"] = {"date": "2026-10-18", "reason": "Not run."}
        self.assertEqual(labels(self.spec, "dev-001-tide-table"), [])
        self.assertTrue(fixtures.is_excluded(self.spec))
        self.assertFalse(fixtures.is_excluded(pilot()))
        bad_marks = {
            "not_an_object": "Not run.",
            "missing_reason": {"date": "2026-10-18"},
            "extra_key": {"date": "2026-10-18", "reason": "Not run.", "by": "x"},
            "empty_reason": {"date": "2026-10-18", "reason": "   "},
            "bad_date": {"date": "18-10-2026", "reason": "Not run."},
            "date_not_string": {"date": 20261018, "reason": "Not run."},
        }
        for name, mark in bad_marks.items():
            with self.subTest(name):
                spec = pilot()
                spec["excluded"] = mark
                self.assertLabel("excluded_invalid", spec, "dev-001-tide-table")
                self.assertTrue(fixtures.is_excluded(spec))  # fail safe: still not run

    def test_a_probe_marker_is_a_non_empty_string_and_a_malformed_one_still_excludes(self):
        probe = self.spec["probes"][0]
        probe["excluded"] = "status and plan overlap"
        self.assertEqual(labels(self.spec, "dev-001-tide-table"), [])
        self.assertTrue(fixtures.probe_is_excluded(probe))
        self.assertEqual([p["id"] for p in fixtures.active_probes(self.spec)],
                         [p["id"] for p in self.spec["probes"][1:]])
        for name, mark in {"empty": "", "blank": "   ", "number": 1, "object": {"reason": "x"}, "null": None}.items():
            with self.subTest(name):
                spec = pilot()
                spec["probes"][0]["excluded"] = mark
                self.assertLabel("probes_excluded_invalid", spec, "dev-001-tide-table")
                self.assertTrue(fixtures.probe_is_excluded(spec["probes"][0]))  # fail safe

    def test_the_kind_minimums_count_active_probes_only(self):
        for probe in self.spec["probes"]:
            if probe["type"] == "temporal":
                probe["excluded"] = "overlap"
        self.assertLabel("probes_missing_temporal")

    def test_an_excluded_probe_still_covers_its_needed_item(self):
        probe = next(p for p in self.spec["probes"] if p["type"] == "single-fact")
        probe["excluded"] = "overlap"
        self.assertNotIn("probes_needed_item_without_probe", labels(self.spec, "dev-001-tide-table"))

    def test_an_excluded_spec_still_meets_every_other_rule(self):
        self.spec["excluded"] = {"date": "2026-10-18", "reason": "Not run."}
        self.spec["turns"] = []
        self.assertLabel("turns_missing")

    def test_repo_rules(self):
        for mutate, label in (
            (lambda s: s["repo"]["files"].pop("CLAUDE.md"), "repo_floor_missing"),
            (lambda s: s["repo"]["files"].update({"../x.py": "x"}), "repo_path_invalid"),
            (lambda s: s["repo"]["files"].update({"/abs.py": "x"}), "repo_path_invalid"),
            (lambda s: s["repo"]["files"].update({".git/config": "x"}), "repo_path_invalid"),
            (lambda s: s["repo"]["files"].update({"bad.py": "def ("}), "repo_python_does_not_parse"),
            (lambda s: s["repo"]["files"].update({"big.txt": "x" * 250_000}), "repo_too_large"),
            (lambda s: s["repo"].update({"files": {}}), "repo_files_missing"),
        ):
            with self.subTest(label=label):
                spec = pilot()
                mutate(spec)
                self.assertLabel(label, spec)

    def test_allowed_bash_rules(self):
        for bad in ([], ["python3 *; rm -rf /"], ["curl *"], [""], ["git status | cat"], ["python3 $(x)"]):
            with self.subTest(bad=bad):
                spec = pilot()
                spec["allowed_bash"] = bad
                self.assertTrue({"allowed_bash_missing", "allowed_bash_invalid"} & set(labels(spec)))

    def test_turn_rules(self):
        spec = pilot()
        spec["turns"] = []
        self.assertLabel("turns_missing", spec)
        spec = pilot()
        spec["turns"][1] = "/cpr:preserve quick"
        self.assertLabel("turns_slash_command", spec)
        spec = pilot()
        spec["turns"][1] = "x" * 2001
        self.assertLabel("turns_invalid", spec)

    def test_signpost_phrases_are_rejected_case_insensitively(self):
        self.assertEqual(len(fixtures.SIGNPOSTS), 11)
        for phrase in fixtures.SIGNPOSTS:
            with self.subTest(phrase=phrase):
                spec = pilot()
                spec["turns"][0] = "Please check the repo. " + phrase.upper() + " we use tabs."
                self.assertLabel("turns_signpost", spec)
        spec = pilot()
        spec["turns"][0] = "Don\u2019t forget the tests."
        self.assertLabel("turns_signpost", spec)

    def test_anchor_rules(self):
        spec = pilot()
        del spec["items"][0]["anchor"]
        self.assertLabel("items_anchor_missing", spec)
        spec = pilot()
        spec["items"][0]["anchor"] = "words that no turn contains"
        self.assertLabel("items_anchor_not_in_turns", spec)
        spec = pilot()
        tool = next(i for i in spec["items"] if i.get("source") == "tool")
        tool["anchor"] = spec["turns"][0][:10]
        self.assertLabel("items_tool_item_has_anchor", spec)
        spec = pilot()
        spec["items"][-1]["anchor"] = "x"
        self.assertLabel("items_not_needed_has_labels", spec)

    def test_mid_turn_detection(self):
        spec = {"turns": ["Do the thing. Because of reason X we skip Y.", "Because of reason X, do the thing."],
                "items": [{"id": "a", "needed": True, "anchor": "reason X we skip Y"},
                          {"id": "b", "needed": True, "anchor": "do the thing"},
                          {"id": "c", "needed": False, "anchor": "Do the thing"}]}
        self.assertEqual(fixtures.mid_turn_items(spec), ["a"])

    def test_evidence_rules(self):
        tool = lambda s: next(i for i in s["items"] if i.get("source") == "tool")  # noqa: E731
        cases = (
            (lambda s: tool(s).pop("evidence"), "items_evidence_missing"),
            (lambda s: tool(s).update(evidence=[]), "items_evidence_missing"),
            (lambda s: tool(s).update(evidence=[""]), "items_evidence_missing"),
            (lambda s: tool(s).update(evidence="ValueError"), "items_evidence_missing"),
            (lambda s: tool(s).update(evidence=["x" * 81]), "items_evidence_missing"),
            (lambda s: s["items"][0].update(evidence=["x"]), "items_evidence_on_non_tool_item"),
            (lambda s: s["items"][-1].update(evidence=["x"]), "items_evidence_on_non_tool_item"),
            (lambda s: tool(s).update(evidence=[s["turns"][0][:20]]), "items_evidence_in_turns"),
            (lambda s: tool(s).update(evidence=[s["turns"][0][:20].upper()]), "items_evidence_in_turns"),
        )
        for mutate, label in cases:
            with self.subTest(label=label):
                spec = pilot()
                mutate(spec)
                self.assertLabel(label, spec)

    def test_item_rules(self):
        cases = (
            (lambda s: s["items"][1].update(id="i1"), "items_ids_not_unique"),
            (lambda s: s["items"][0].update(kind="correction"), "items_malformed"),
            (lambda s: s["items"][0].update(suits=["Lessons"]), "items_suits_invalid"),
            (lambda s: s["items"][0].update(suits=[]), "items_suits_invalid"),
            (lambda s: s["items"][0].pop("oracle_line"), "items_oracle_missing"),
            (lambda s: s["items"][0].update(source="other"), "items_source_invalid"),
            (lambda s: s["items"][-1].update(suits=["Key Decisions"]), "items_not_needed_has_labels"),
            (lambda s: s["items"][-1].update(oracle_line="- x"), "items_not_needed_has_labels"),
            (lambda s: s["items"].__delitem__(slice(0, 3)), "items_needed_below_5"),
            (lambda s: s["items"].__delitem__(-1), "items_not_needed_below_2"),
        )
        for mutate, label in cases:
            with self.subTest(label=label):
                spec = pilot()
                mutate(spec)
                self.assertLabel(label, spec)

    def test_needed_item_statement_and_line_must_not_be_in_the_floor(self):
        spec = pilot()
        floor = spec["repo"]["files"]["CLAUDE.md"]
        spec["repo"]["files"]["CLAUDE.md"] = floor + spec["items"][0]["statement"] + "\n"
        self.assertLabel("items_statement_in_floor", spec)
        spec = pilot()
        spec["repo"]["files"]["CLAUDE.md"] = floor + spec["items"][0]["oracle_line"] + "\n"
        self.assertLabel("items_oracle_line_in_floor", spec)

    def test_tool_items_must_not_be_stated_in_turns(self):
        spec = pilot()
        tool = next(i for i in spec["items"] if i.get("source") == "tool")
        spec["turns"][0] = tool["statement"]
        self.assertLabel("items_tool_item_stated_in_turns", spec)
        spec = pilot()
        tool = next(i for i in spec["items"] if i.get("source") == "tool")
        probe = next(p for p in spec["probes"] if p.get("item") == tool["id"])
        spec["turns"][0] = "so " + probe["answer"] + " it is"
        self.assertLabel("probes_tool_item_answer_in_turns", spec)

    def test_probe_rules(self):
        cases = (
            (lambda s: s["probes"][1].update(id="p1"), "probes_ids_not_unique"),
            (lambda s: s["probes"][1].update(question=s["probes"][0]["question"]), "probes_questions_not_unique"),
            (lambda s: s["probes"][0].update(type="recall"), "probes_type_invalid"),
            (lambda s: s["probes"][0].update(item="n1"), "probes_item_not_a_needed_item"),
            (lambda s: s["probes"][0].update(item="zz"), "probes_item_not_a_needed_item"),
            (lambda s: s["probes"][3].update(reject=[]), "probes_knowledge_update_without_stale_answer"),
            (lambda s: s["probes"][0].update(near="i1"), "probes_near_only_on_abstention"),
            (lambda s: s["probes"][0].update(answer="UNKNOWN"), "probes_answer_unknown_on_non_abstention"),
            (lambda s: s["probes"][0].update(extra=1), "probes_malformed"),
            (lambda s: s["probes"].pop(2), "probes_needed_item_without_probe"),
            (lambda s: s["probes"][3].update(type="single-fact"), "probes_missing_knowledge_update"),
            (lambda s: s["probes"][6].update(type="single-fact"), "probes_missing_temporal"),
            (lambda s: [s["probes"].pop(-1), s["probes"].pop(-1)], "probes_missing_abstention"),
        )
        for mutate, label in cases:
            with self.subTest(label=label):
                spec = pilot()
                mutate(spec)
                self.assertLabel(label, spec)

    def test_abstention_probes_must_be_near_misses(self):
        index = next(i for i, p in enumerate(self.spec["probes"]) if p["type"] == "abstention")
        cases = (
            (lambda p: p.update(item="i1"), "probes_abstention_has_item"),
            (lambda p: p.update(near="zz"), "probes_abstention_near_missing"),
            (lambda p: p.update(near="n1"), "probes_abstention_near_missing"),
            (lambda p: p.pop("near"), "probes_abstention_near_missing"),
            (lambda p: p.update(answer="Maybe port 9000"), "probes_abstention_answer_not_unknown"),
            (lambda p: p.update(reject=["x"]), "probes_abstention_answer_not_unknown"),
            (lambda p: p.update(question="Banana smoothie recipe?"), "probes_abstention_not_a_near_miss"),
        )
        for mutate, label in cases:
            with self.subTest(label=label):
                spec = pilot()
                mutate(spec["probes"][index])
                self.assertLabel(label, spec)

    def test_abstention_question_must_be_something_never_said(self):
        probe = next(p for p in self.spec["probes"] if p["type"] == "abstention")
        self.spec["turns"][0] = "Hey, " + probe["question"]
        self.assertLabel("probes_abstention_question_in_turns")

    def test_code_answers_is_required_on_an_active_spec(self):
        del self.spec["probes"][2]["code_answers"]
        self.assertLabel("probes_code_answers_missing")
        self.assertNotIn("probes_code_answers_invalid", labels(self.spec))

    def test_code_answers_must_be_a_json_boolean(self):
        for bad in ("yes", 1, 0, None, "true", [True]):
            with self.subTest(value=bad):
                spec = pilot()
                spec["probes"][0]["code_answers"] = bad
                found = labels(spec)
                self.assertIn("probes_code_answers_invalid", found)
                self.assertNotIn("probes_code_answers_missing", found)

    def test_an_excluded_spec_may_omit_code_answers_but_not_misuse_it(self):
        self.spec["excluded"] = {"date": "2026-10-18", "reason": "Not run."}
        for probe in self.spec["probes"]:
            del probe["code_answers"]
        found = labels(self.spec, "dev-001-tide-table")
        self.assertNotIn("probes_code_answers_missing", found)
        self.assertNotIn("probes_code_answers_invalid", found)
        self.spec["probes"][0]["code_answers"] = "yes"
        self.assertLabel("probes_code_answers_invalid")

    def test_every_active_committed_probe_has_a_boolean_code_answers(self):
        seen = 0
        for path in sorted(DATA.glob("*/*/spec.json")):
            spec = fixtures.load_spec(path)
            if fixtures.is_excluded(spec):
                continue
            for probe in spec["probes"]:
                seen += 1
                with self.subTest(spec=path.parent.name, probe=probe["id"]):
                    self.assertIsInstance(probe.get("code_answers"), bool)
        self.assertGreater(seen, 0)

    def test_history_check_rules(self):
        token = self.spec["history_check"]["token"]
        spec = pilot()
        spec["history_check"]["token"] = "bluefinch"
        self.assertLabel("history_check_invalid", spec)
        spec = pilot()
        spec["turns"] = [t.replace(token, "SOMETHING") for t in spec["turns"]]
        self.assertLabel("history_token_not_in_turns", spec)
        spec = pilot()
        spec["repo"]["files"]["notes.txt"] = f"codename {token}"
        self.assertLabel("history_token_in_repo", spec)
        spec = pilot()
        spec["items"][-1]["statement"] += " " + token
        self.assertLabel("history_token_in_items_or_probes", spec)

    def test_floor_and_oracle_size_limits(self):
        spec = pilot()
        spec["repo"]["files"]["CLAUDE.md"] = "# t\n" * 5
        self.assertLabel("floor_length_out_of_range", spec)
        spec = pilot()
        spec["repo"]["files"]["CLAUDE.md"] = "# t\n" * 81
        self.assertLabel("floor_length_out_of_range", spec)
        spec = pilot()
        old = fixtures.ORACLE_MAX_LINES
        fixtures.ORACLE_MAX_LINES = 30
        self.addCleanup(setattr, fixtures, "ORACLE_MAX_LINES", old)
        self.assertLabel("oracle_too_long", spec)

    def test_oracle_rendering_inserts_into_existing_sections_or_appends(self):
        spec = pilot()
        spec["repo"]["files"]["CLAUDE.md"] = "# t\n\n## Decisions\n\n- old\n\n## Other\n\n- x\n"
        spec["items"] = [
            {"id": "a", "statement": "s", "needed": True, "suits": ["Key Decisions"], "oracle_line": "- new", "oracle_section": "Decisions"},
            {"id": "b", "statement": "s2", "needed": True, "suits": ["Next Steps"], "oracle_line": "- later", "oracle_section": "Next steps"},
            {"id": "c", "statement": "s3", "needed": False, "suits": []},
        ]
        out = fixtures.render_oracle(spec).split("\n")
        self.assertEqual(out[:6], ["# t", "", "## Decisions", "", "- old", "- new"])
        self.assertEqual(out[-3:], ["## Next steps", "- later", ""])
        self.assertNotIn("s3", "\n".join(out))


class EvidenceGateTests(unittest.TestCase):
    def rows(self, *results):
        out = []
        for n, content in enumerate(results):
            block = {"type": "tool_result", "tool_use_id": f"t{n}", "content": content}
            out.append({"type": "user", "message": {"content": [block]}})
        return out

    def spec(self):
        return {"items": [{"id": "a", "needed": True, "source": "tool", "evidence": ["alpha", "beta"]},
                          {"id": "b", "needed": True, "source": "user"}]}

    def test_every_string_must_appear_in_a_non_denied_result(self):
        spec = self.spec()
        self.assertEqual(fixtures.tool_evidence_missing(self.rows("alpha here", "and beta there"), spec), [])
        self.assertEqual(fixtures.tool_evidence_missing(self.rows("alpha only"), spec), ["a"])
        self.assertEqual(fixtures.tool_evidence_missing([], spec), ["a"])

    def test_denied_results_do_not_count(self):
        denied = "Permission for this tool use was denied. It requires approval. alpha beta"
        self.assertEqual(fixtures.tool_evidence_missing(self.rows(denied), self.spec()), ["a"])
        self.assertEqual(fixtures.tool_evidence_missing(self.rows(denied, "alpha beta"), self.spec()), [])
        self.assertEqual(fixtures.count_denials(self.rows(denied, "alpha beta", denied)), 2)

    def test_list_content_is_read(self):
        content = [{"type": "text", "text": "alpha"}, {"type": "text", "text": "beta"}]
        self.assertEqual(fixtures.tool_evidence_missing(self.rows(content), self.spec()), [])

    def test_a_command_failure_is_not_a_denial(self):
        rows = self.rows("Exit code 1\nTraceback ... alpha beta")
        self.assertEqual(fixtures.count_denials(rows), 0)
        self.assertEqual(fixtures.tool_evidence_missing(rows, self.spec()), [])

    def test_the_installed_pilot_fixture_meets_its_own_evidence(self):
        spec = pilot("dev-001-tide-table")
        path = DATA / "dev" / "dev-001-tide-table" / "transcript.jsonl"
        if not path.exists():
            self.skipTest("dev-001 is not installed in this checkout")
        self.assertEqual(fixtures.tool_evidence_missing(fixtures.read_jsonl(path), spec), [])


class SetRuleTests(unittest.TestCase):
    def test_duplicate_fixture_ids(self):
        a = pilot()
        self.assertIn("set: ids_not_unique", fixtures.validate_set([a, copy.deepcopy(a)]))

    def test_every_category_must_be_suited_by_two_items(self):
        a = pilot()
        for item in a["items"]:
            item["suits"] = [c for c in item["suits"] if c != "Next Steps"] if item["needed"] else []
        found = fixtures.validate_set([a])
        self.assertIn("set: category_suited_by_fewer_than_2_items:Next Steps", found)

    def test_a_complete_dev_split_needs_three_long_fixtures(self):
        base = pilot("dev-004-chess-clock")
        short = pilot()
        self.assertTrue(fixtures.is_long(base))
        specs = []
        for n in range(20):
            spec = copy.deepcopy(base if n < 2 else short)
            spec["id"] = f"dev-{n + 1:03d}-x"
            specs.append(spec)
        self.assertIn("set: dev_split_needs_3_long_fixtures", fixtures.validate_set(specs))
        specs[2] = dict(copy.deepcopy(base), id="dev-003-x")
        self.assertNotIn("set: dev_split_needs_3_long_fixtures", fixtures.validate_set(specs))
        self.assertEqual(sum(fixtures.is_long(s) for s in specs[:19]), 3)

    def test_an_incomplete_dev_split_is_not_held_to_the_long_count(self):
        self.assertNotIn("set: dev_split_needs_3_long_fixtures", fixtures.validate_set([pilot()]))

    def test_the_heldout_split_needs_five_long_fixtures_when_complete(self):
        specs = []
        for n in range(6):
            spec = copy.deepcopy(pilot("dev-004-chess-clock" if n < 4 else "dev-001-tide-table"))
            spec["id"] = f"heldout-{n + 1:03d}-x"
            spec["split"] = "heldout"
            specs.append(spec)
        self.assertIn("set: heldout_split_needs_5_long_fixtures", fixtures.validate_set(specs, heldout_complete=True))
        self.assertNotIn("set: heldout_split_needs_5_long_fixtures", fixtures.validate_set(specs, heldout_complete=False))
        specs[4] = dict(copy.deepcopy(pilot("dev-004-chess-clock")), id="heldout-005-x", split="heldout")
        self.assertNotIn("set: heldout_split_needs_5_long_fixtures", fixtures.validate_set(specs, heldout_complete=True))


class ExcludedSetTests(unittest.TestCase):
    MARK = {"date": "2026-10-18", "reason": "Not run."}

    def test_an_excluded_spec_does_not_count_toward_category_coverage(self):
        a = pilot()  # one item suits Next Steps in each pilot spec, so two specs make the minimum of 2
        b = pilot("dev-002-recipe-scaler")
        self.assertNotIn("set: category_suited_by_fewer_than_2_items:Next Steps", fixtures.validate_set([a, b]))
        b["excluded"] = self.MARK
        self.assertIn("set: category_suited_by_fewer_than_2_items:Next Steps", fixtures.validate_set([a, b]))

    def test_an_excluded_spec_does_not_count_toward_the_long_session_rule(self):
        specs = []
        for n in range(20):
            spec = copy.deepcopy(pilot("dev-001-tide-table"))
            spec["id"] = f"dev-{n + 1:03d}-x"
            spec["turns"] = ["t"] * 30 if n < 3 else ["t"] * 6
            specs.append(spec)
        self.assertNotIn("set: dev_split_needs_3_long_fixtures", fixtures.validate_set(specs))
        specs[0]["excluded"] = self.MARK
        self.assertIn("set: dev_split_needs_3_long_fixtures", fixtures.validate_set(specs))

    def test_ids_are_still_checked_across_excluded_specs(self):
        a, b = pilot(), pilot()
        b["excluded"] = self.MARK
        self.assertIn("set: ids_not_unique", fixtures.validate_set([a, b]))

    def test_run_validate_names_the_excluded_fixture_and_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for sid in ("dev-001-tide-table", "dev-002-recipe-scaler", "dev-003-greenhouse-log"):
                spec = pilot(sid)
                if sid == "dev-003-greenhouse-log":
                    spec["excluded"] = self.MARK
                target = root / "dev" / sid
                target.mkdir(parents=True)
                (target / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
            out = io.StringIO()
            code = fixtures.run_validate(root, out=out)
            self.assertEqual(code, 0, out.getvalue())
            self.assertRegex(out.getvalue(), r"^validate: 2 fixtures OK \(dev 2, heldout 0\); "
                                             r"1 excluded: dev-003-greenhouse-log; categories covered \d/6\n$")

    def test_a_malformed_marker_fails_validate(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = pilot("dev-001-tide-table")
            spec["excluded"] = {"date": "soon", "reason": "Not run."}
            target = Path(tmp) / "dev" / "dev-001-tide-table"
            target.mkdir(parents=True)
            (target / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
            out = io.StringIO()
            self.assertEqual(fixtures.run_validate(Path(tmp), out=out), 1)
            self.assertIn("dev-001-tide-table: excluded_invalid", out.getvalue())


class LoaderTests(unittest.TestCase):
    def test_validate_all_reports_labels_only_and_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad = pilot()
            bad["items"][0]["statement"] = "SECRET-STATEMENT-TEXT"
            bad["repo"]["files"]["CLAUDE.md"] += "SECRET-STATEMENT-TEXT\n"
            target = root / "dev" / "dev-001-tide-table"
            target.mkdir(parents=True)
            (target / "spec.json").write_text(json.dumps(bad), encoding="utf-8")
            broken = root / "dev" / "dev-002-broken"
            broken.mkdir()
            (broken / "spec.json").write_text("{not json", encoding="utf-8")
            out = io.StringIO()
            code = fixtures.run_validate(root, out=out)
            self.assertEqual(code, 1)
            self.assertIn("dev-001-tide-table: items_statement_in_floor", out.getvalue())
            self.assertIn("dev-002-broken: spec_not_json", out.getvalue())
            self.assertNotIn("SECRET-STATEMENT-TEXT", out.getvalue())
            self.assertIn("validate: FAIL 2 problems", out.getvalue())

    def test_an_empty_tree_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            self.assertEqual(fixtures.run_validate(Path(tmp), out=out), 1)
            self.assertIn("no_fixtures", out.getvalue())


# --------------------------------------------------------------------------
# Generation, normalization and install (offline; a scripted runner stands in for claude)
# --------------------------------------------------------------------------

LEAK_TYPES = "api-request-blob"
SNAPSHOT_TYPE = "prompt_snapshot"
HOST_MARKER = "a synthetic host instruction line for generation tests"


def shutil_rmtree(path):
    import shutil
    shutil.rmtree(path, ignore_errors=True)


class GenRunner:
    """Stands in for run_claude: writes a persisted transcript and returns a record."""

    def __init__(self, tmp: Path, cost=0.1, floor_edit_attempts=(), proof_leak_turn=None, leak_file_attempts=(),
                 r1_token=True, r0_leak=False, denials=(), evidence=("sensor-7 stuck at 99.9",),
                 evidence_attempts=(1, 2, 3), denied_only=False):
        self.tmp, self.cost = tmp, cost
        self.evidence, self.evidence_attempts, self.denied_only = list(evidence), set(evidence_attempts), denied_only
        self.config_dir = tmp / "home" / ".claude"
        self.floor_edit_attempts, self.proof_leak_turn = set(floor_edit_attempts), proof_leak_turn
        self.leak_file_attempts, self.r1_token, self.r0_leak = set(leak_file_attempts), r1_token, r0_leak
        self.denials = list(denials)
        self.calls = []
        self.attempt = 0
        self.last_uuid = {}
        self.made = 0

    def __call__(self, cfg, prompt, sandbox, *, label, phase, kind="plain", session_id=None, resume=None, plugin=None,
                 plain=False, probe=False, model=None, environ=None, prior=None, allowed_tools=None,
                 fork_session=False, extra_session_ids=()):
        self.calls.append({"label": label, "phase": phase, "kind": kind, "probe": probe, "resume": resume,
                           "fork": fork_session, "allowed": allowed_tools, "prior": prior, "session_id": session_id})
        if label == "gen-1":
            self.attempt += 1
        real = os.path.realpath(str(sandbox))
        slug = claude_run.project_slug(Path(real))
        sid = resume or session_id
        if fork_session:
            sid = self.new_id()
        pdir = self.config_dir / "projects" / slug
        pdir.mkdir(parents=True, exist_ok=True)
        path = pdir / f"{sid}.jsonl"
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
        if not rows:
            files = [{"path": str(sandbox / "CLAUDE.md"), "type": "Project", "content": "x"}]
            rows.append({"type": "attachment", "attachment": {"type": "instructions", "files": files}, "sessionId": sid})
            rows.append({"type": "queue-operation", "sessionId": sid})
        parent = rows[-1].get("uuid")
        answer = "ok"
        if kind == "probe" and resume:
            answer = "the codename is " + self.token if self.r1_token else "I do not know"
        elif kind == "probe":
            answer = "the codename is " + self.token if self.r0_leak else "I do not know"
        pieces = [
            ("user", [{"type": "text", "text": prompt}]),
            ("assistant", [{"type": "thinking", "thinking": "hmm", "signature": "SIG"}]),
            ("assistant", [{"type": "text", "text": f"working in {real} ({slug}) {answer}"}]),
        ]
        for role, content in pieces:
            row = {"type": role, "uuid": self.new_id(), "parentUuid": parent, "sessionId": sid, "cwd": real,
                   "timestamp": "2026-01-01T00:00:00Z", "version": "9", "gitBranch": "main", "isSidechain": False,
                   "userType": "external", "requestId": "req", "message": {"role": role, "content": content,
                                                                          "usage": {"input_tokens": 1}}}
            rows.append(row)
            parent = row["uuid"]
        if label == "gen-3" and self.evidence and self.attempt in self.evidence_attempts:
            use_id = "toolu_" + self.new_id()[-8:]
            text = "output: " + " | ".join(self.evidence)
            if self.denied_only:
                text = "Permission for this tool use was denied. It requires approval. " + text
            for role, content in (("assistant", [{"type": "tool_use", "id": use_id, "name": "Bash", "input": {"command": "x"}}]),
                                  ("user", [{"type": "tool_result", "tool_use_id": use_id, "content": text,
                                             "is_error": self.denied_only}])):
                row = {"type": role, "uuid": self.new_id(), "parentUuid": parent, "sessionId": sid, "cwd": real,
                       "message": {"role": role, "content": content}}
                rows.append(row)
                parent = row["uuid"]
        rows.append({"type": LEAK_TYPES, "blob": "x"})
        rows.append({"type": SNAPSHOT_TYPE, "blob": "x"})
        if self.proof_leak_turn and label == f"gen-{self.proof_leak_turn}":
            rows.append({"type": "user", "uuid": self.new_id(), "message": {"content": HOST_MARKER}, "sessionId": sid})
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        if label.startswith("gen-"):
            (sandbox / f"{label}.txt").write_text("work\n", encoding="utf-8")
            if label == "gen-2" and self.attempt in self.floor_edit_attempts:
                (sandbox / "CLAUDE.md").write_text("changed\n", encoding="utf-8")
            if label == "gen-3" and self.attempt in self.leak_file_attempts:
                (sandbox / "notes.txt").write_text("/" + "Users" + "/someone/work\n", encoding="utf-8")
        mem = isolation.auto_memory_dir(self.config_dir, sandbox)
        init = {"type": "system", "subtype": "init", "claude_code_version": cfg["claude_code_version"],
                "model": cfg["model"], "plugins": [], "mcp_servers": [], "skills": list(cfg["init_allowlist"]["skills"]),
                "agents": list(cfg["init_allowlist"]["agents"]), "tools": [] if probe else ["Read"],
                "memory_paths": {"auto": str(mem) + "/"}, "session_id": sid}
        result = {"type": "result", "subtype": "success", "is_error": False, "result": answer,
                  "permission_denials": [{"tool_name": t} for t in self.denials] if label == "gen-2" else [],
                  "total_cost_usd": self.cost, "duration_ms": 5, "usage": {"input_tokens": 3, "output_tokens": 4}}
        claude_run.add_spend(self.cost, environ, phase=phase)
        return {"events": [init, result], "session_id": sid, "session_ids": sorted({sid, *extra_session_ids}),
                "resumed": bool(resume), "kind": kind, "sandbox": str(sandbox), "plugin": None,
                "transcript": str(path), "config_dir": str(self.config_dir), "cost_usd": self.cost, "duration_ms": 5,
                "usage": result["usage"], "phase": phase}

    def new_id(self):
        """A fresh id in the uuid shape, counted rather than random so that a run is the same every time."""
        self.made += 1
        return f"00000000-0000-4000-8000-{self.made:012d}"

    @property
    def token(self):
        return self._token

    _token = "CUCUMBERDRIFT"


class GenTestBase(unittest.TestCase):
    FIXTURE = "dev-003-greenhouse-log"

    def setUp(self):
        patcher = mock.patch.object(fixtures, "_current_username", return_value="someuser")
        patcher.start()
        self.addCleanup(patcher.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        home = self.tmp / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "CLAUDE.md").write_text("# Title\n" + HOST_MARKER + "\n", encoding="utf-8")
        self.root = self.tmp / "fixtures"
        fdir = fixtures.fixture_dir(self.FIXTURE, self.root)
        fdir.mkdir(parents=True)
        (fdir / "spec.json").write_text(json.dumps(pilot(self.FIXTURE)), encoding="utf-8")
        self.fdir = fdir
        self.environ = {"PATH": os.environ["PATH"], "HOME": str(home), "TMPDIR": str(self.tmp), "CPR_EVAL_LIVE": "1"}
        self.cfg = claude_run.load_config()
        self.cfg["phase_caps"] = {"control": 3.0, "generation": 15.0, "evaluation": 30.0}  # not the committed values
        self.spec = pilot(self.FIXTURE)

    def generate(self, runner):
        out = io.StringIO()
        code = fixtures.generate_fixture(self.FIXTURE, self.cfg, self.environ, runner, self.root, out)
        return code, out.getvalue()


class NormalizeTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(fixtures, "_current_username", return_value="someuser")
        patcher.start()
        self.addCleanup(patcher.stop)

    def synthetic(self, sandbox: Path):
        real = os.path.realpath(str(sandbox))
        slug = claude_run.project_slug(Path(real))
        sid = "11111111-1111-4111-8111-111111111111"

        def row(kind, uuid_, parent, content, **extra):
            return dict({"type": kind, "uuid": uuid_, "parentUuid": parent, "sessionId": sid, "cwd": real,
                         "timestamp": "t", "version": "9", "gitBranch": "main", "isSidechain": False,
                         "userType": "external", "requestId": "r", "message": {"role": kind, "content": content,
                                                                                "usage": {"input_tokens": 1}}}, **extra)
        rows = [
            {"type": "queue-operation", "uuid": "q0", "sessionId": sid},
            {"type": "attachment", "uuid": "a0", "parentUuid": None, "attachment": {"type": "instructions"}},
            row("user", "u1", "a0", [{"type": "text", "text": f"look at {real}/app.py"}]),
            row("assistant", "a1", "u1", [{"type": "thinking", "thinking": "t", "signature": "S"}]),
            row("assistant", "a2", "a1", [{"type": "redacted_thinking", "data": "d"},
                                          {"type": "text", "text": f"slug {slug}", "signature": "S2"},
                                          {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": real + "/x"}}]),
            {"type": LEAK_TYPES, "uuid": "x1", "parentUuid": "a2"},
            {"type": SNAPSHOT_TYPE, "uuid": "x2", "parentUuid": "x1"},
            {"type": "cost-state", "uuid": "x3", "parentUuid": "x2"},
            {"type": "last-prompt", "uuid": "x4", "parentUuid": "x3"},
            row("user", "u2", "x4", [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]),
        ]
        return rows, real, slug

    def test_drops_rows_blocks_and_fields_relinks_parents_and_placeholders_the_sandbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows, real, slug = self.synthetic(Path(tmp))
            out = fixtures.normalize_transcript(rows, fixtures.sandbox_aliases(tmp), slug)
        self.assertEqual([r["uuid"] for r in out], ["u1", "a2", "u2"])
        self.assertEqual([r["parentUuid"] for r in out], [None, "u1", "a2"])
        text = json.dumps(out)
        for gone in ("thinking", "redacted_thinking", "signature", "requestId", "usage", "queue-operation", "attachment",
                     LEAK_TYPES, SNAPSHOT_TYPE, "cost-state", "last-prompt"):
            self.assertNotIn(gone, text)
        self.assertNotIn(real, text)
        self.assertNotIn(slug, text)
        self.assertIn("{{SANDBOX}}/app.py", text)
        self.assertIn("slug {{SANDBOX_SLUG}}", text)
        self.assertEqual(set(out[0]) - set(fixtures.FIELD_WHITELIST), set())

    VF = "/var/folders/ab/xyz/T"
    MON = "Oct"

    @staticmethod
    def home_of(name):
        return "/" + "Users" + "/" + name  # test fixture for the scrub: a home path built at runtime

    def norm_text(self, text, aliases=(), tmpdir=None, name="zed"):
        tmpdir = self.VF + "/" if tmpdir is None else tmpdir
        rows = [{"type": "user", "uuid": "u", "parentUuid": None,
                 "message": {"content": [{"type": "text", "text": text}]}}]
        with mock.patch.object(fixtures, "_current_username", return_value=name), \
                mock.patch.object(Path, "home", return_value=Path(self.home_of(name))):
            out = fixtures.normalize_transcript(rows, list(aliases), "", environ={"TMPDIR": tmpdir})
        return out[0]["message"]["content"][0]["text"]

    def test_traceback_temp_path_becomes_tmp_placeholder_keeping_the_random_parts(self):
        text = 'File "' + self.VF + '/tmpabc/saves/one.json", line 3'
        self.assertEqual(self.norm_text(text), 'File "{{TMP}}/tmpabc/saves/one.json", line 3')

    def test_every_temp_root_form_is_rewritten_including_json_escaped(self):
        for form in (self.VF, "/private" + self.VF, self.VF.replace("/", "\\/")):
            with self.subTest(form=form):
                got = self.norm_text("at " + form + "/q/r.txt", tmpdir="/nowhere/else/")
                self.assertEqual(got, "at {{TMP}}/q/r.txt")
        self.assertEqual(self.norm_text("at /scratch/t1//x/y", tmpdir="/scratch/t1/"), "at {{TMP}}/x/y")
        self.assertEqual(self.norm_text("at /scratch/t10/x", tmpdir="/scratch/t1/"), "at /scratch/t10/x")

    def test_sandbox_rewrite_runs_before_the_temp_rewrite(self):
        parent = self.VF + "/cpr-eval/gen/dev-099/attempt-1-abc123"
        text = "Permission to use Read denied: " + parent + "/other.txt; " + parent + "/repo/app.py"
        got = self.norm_text(text, aliases=[parent + "/repo"])
        self.assertEqual(got, "Permission to use Read denied: {{TMP}}/cpr-eval/gen/dev-099/attempt-1-abc123/other.txt; "
                              "{{SANDBOX}}/app.py")

    def test_username_is_replaced_as_a_whole_word_and_the_group_is_kept(self):
        row = "-rw-r--r--@ 1 {}  staff  1234 " + self.MON + " 16 10:02 f.txt"
        got = self.norm_text(row.format("zed") + "; zedge zed_x " + self.home_of("zed") + "/work")
        self.assertEqual(got, row.format("user") + "; zedge zed_x {{HOME}}/work")

    def test_a_name_after_any_line_break_is_replaced(self):
        for sep in ("\n", "\t", "\r\n"):
            with self.subTest(sep=repr(sep)):
                self.assertEqual(self.norm_text("log" + sep + "zed ran it"), "log" + sep + "user ran it")

    def test_a_name_after_a_json_escape_is_replaced_as_a_whole_word(self):
        for esc in ("\\n", "\\t", "\\r\\n"):  # the two-character JSON forms
            with self.subTest(esc=esc):
                self.assertEqual(self.norm_text("log" + esc + "zed ran it"), "log" + esc + "user ran it")

    def test_a_name_inside_a_longer_word_after_a_json_escape_is_kept(self):
        self.assertEqual(self.norm_text("log" + "\\n" + "zedge ran"), "log" + "\\n" + "zedge ran")

    def test_the_username_is_replaced_in_any_letter_case(self):
        self.assertEqual(self.norm_text("Hi Zed, ran it"), "Hi user, ran it")
        self.assertEqual(self.norm_text("ZED ran"), "user ran")
        self.assertEqual(self.norm_text("log" + "\\n" + "Zed ran"), "log" + "\\n" + "user ran")

    def test_a_name_glued_to_a_letter_digit_or_underscore_is_kept_in_any_case(self):
        text = "Zedge Zed_x ZEDx xZed"
        self.assertEqual(self.norm_text(text), text)

    def test_the_lower_case_name_is_still_replaced(self):
        self.assertEqual(self.norm_text("zed ran"), "user ran")

    def test_the_escape_set_stays_lower_case(self):
        for esc in ("\\N", "\\R", "\\T", "\\B", "\\F"):
            with self.subTest(esc=esc):
                text = "log" + esc + "zed ran"
                self.assertEqual(self.norm_text(text), text)

    def test_a_name_after_the_other_three_json_escapes_is_replaced(self):
        for esc in ("\\r", "\\b", "\\f"):
            with self.subTest(esc=esc):
                self.assertEqual(self.norm_text("log" + esc + "zed ran"), "log" + esc + "user ran")

    def test_a_letter_without_its_backslash_is_not_an_escape(self):
        for letter in "nrtbf":
            with self.subTest(letter=letter):
                text = "x" + letter + "zed ran"
                self.assertEqual(self.norm_text(text), text)

    def test_the_realpath_and_path_alias_pair_as_keys_is_an_error(self):
        row = {"type": "assistant", "uuid": "a3", "parentUuid": None,
               "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Read",
                                        "input": {"/private/opt/sb/x": 1, "/opt/sb/x": 2}}]}}
        with self.assertRaises(fixtures.GenError):
            fixtures.normalize_transcript([row], ["/private/opt/sb", "/opt/sb"], "")

    def test_a_home_path_after_a_newline_is_replaced(self):
        self.assertEqual(self.norm_text("log\n" + self.home_of("zed") + "/work"), "log\n{{HOME}}/work")

    def test_two_keys_equal_after_the_placeholders_are_an_error_that_names_no_key(self):
        row = {"type": "assistant", "uuid": "a2", "parentUuid": None,
               "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Read",
                                        "input": {"someuser": 1, "user": 2}}]}}
        with self.assertRaises(fixtures.GenError) as ctx:
            fixtures.normalize_transcript([row], [], "")
        self.assertNotIn("someuser", str(ctx.exception))

    def test_an_alias_holding_a_double_quote_is_replaced(self):
        self.assertEqual(self.norm_text('see /opt/sb"q/app.py', aliases=['/opt/sb"q']), "see {{SANDBOX}}/app.py")

    def test_a_row_without_escapes_normalizes_byte_identically_to_the_old_dump_path(self):
        with tempfile.TemporaryDirectory() as t:
            sandbox = Path(t) / "sb"
            sandbox.mkdir()
            aliases = fixtures.sandbox_aliases(sandbox)
            slug = claude_run.project_slug(Path(os.path.realpath(sandbox)))
            environ = {"TMPDIR": t}
            rows, _real, _slug = self.synthetic(sandbox)
            extra = {"type": "user", "uuid": "u3", "parentUuid": "u2", "message": {"role": "user", "content": [
                {"type": "text", "text": "someuser asked; then someuser said " + self.home_of("someuser") + "/w and "
                 + t + "/elsewhere/f.txt in " + slug},
                {"type": "tool_use", "id": "t9", "name": "Read",
                 "input": {"n": 3, "f": 1.5, "b": True, "z": None, "l": ["a", ["b", "c"]]}}]}}
            rows.append(extra)
            self.assertNotIn("\\", json.dumps(rows))  # no quote, backslash or control character in any string
            with mock.patch.object(Path, "home", return_value=Path(self.home_of("someuser"))):
                new = fixtures.normalize_transcript(rows, aliases, slug, environ=environ)
                with mock.patch.object(fixtures, "_placeholder_row",
                                       side_effect=lambda row, aliases, slug, environ=None: row):
                    kept = fixtures.normalize_transcript(rows, [], "", environ=environ)
                old = []
                for row in kept:
                    text = json.dumps(row, ensure_ascii=False)
                    for alias in sorted({a for a in aliases if a}, key=len, reverse=True):
                        text = text.replace(alias, fixtures.PLACEHOLDER_SANDBOX)
                    text = text.replace(slug, fixtures.PLACEHOLDER_SLUG)
                    text = fixtures._placeholder_identity(fixtures._placeholder_temp(text, environ))
                    old.append(json.loads(text))
        self.assertEqual(json.dumps(new, ensure_ascii=False), json.dumps(old, ensure_ascii=False))
        dumped = json.dumps(new, ensure_ascii=False)
        for marker in ("{{SANDBOX}}", "{{SANDBOX_SLUG}}", "{{TMP}}", "{{HOME}}"):
            self.assertIn(marker, dumped)
        self.assertNotIn("someuser", dumped)

    def test_a_row_that_held_only_thinking_is_dropped(self):
        rows = [{"type": "assistant", "uuid": "a", "parentUuid": None,
                 "message": {"content": [{"type": "thinking", "thinking": "t"}]}}]
        self.assertEqual(fixtures.normalize_transcript(rows, [], ""), [])

    def test_install_round_trips_the_placeholders_with_a_fresh_session_id_per_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp).resolve()
            gen_sandbox = tmp / "gen"
            gen_sandbox.mkdir()
            rows, real, slug = self.synthetic(gen_sandbox)
            fdir = tmp / "fx"
            (fdir / "repo").mkdir(parents=True)
            (fdir / "repo" / "app.py").write_text("print(1)\n", encoding="utf-8")
            fixtures.write_jsonl(fdir / "transcript.jsonl", fixtures.normalize_transcript(rows, fixtures.sandbox_aliases(gen_sandbox), slug))
            (tmp / "home" / ".claude").mkdir(parents=True)
            environ = {"PATH": os.environ["PATH"], "HOME": str(tmp / "home"), "TMPDIR": str(tmp)}
            cfg = {"isolation": "sources"}
            first = fixtures.install_fixture(fdir, cfg, environ)
            second = fixtures.install_fixture(fdir, cfg, environ)
            seen = set()
            for sandbox, sid, config_dir in (first, second):
                new_real = os.path.realpath(str(sandbox))
                new_slug = claude_run.project_slug(Path(new_real))
                path = config_dir / "projects" / new_slug / f"{sid}.jsonl"
                installed = fixtures.read_jsonl(path)
                text = json.dumps(installed)
                self.assertIn(new_real + "/app.py", text)
                self.assertNotIn("{{", text)
                self.assertEqual({r["sessionId"] for r in installed}, {sid})
                self.assertTrue((sandbox / "app.py").is_file())
                self.assertTrue((sandbox / ".git").is_dir())
                seen.add(sid)
                self.assertEqual(len(installed), 3)
            self.assertEqual(len(seen), 2)
            self.assertNotEqual(first[0], second[0])
            manifest = (claude_run.eval_root(environ) / "project-dirs.txt").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(manifest), 2)

    def test_snapshot_tree_skips_git_and_caches(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "src", Path(tmp) / "dst"
            (src / ".git").mkdir(parents=True)
            (src / ".git" / "HEAD").write_text("x", encoding="utf-8")
            (src / "__pycache__").mkdir()
            (src / "__pycache__" / "a.pyc").write_text("x", encoding="utf-8")
            (src / "pkg").mkdir()
            (src / "pkg" / "a.py").write_text("x", encoding="utf-8")
            self.assertEqual(fixtures.snapshot_tree(src, dst), ["pkg/a.py"])
            self.assertFalse((dst / ".git").exists())


class UsernameTests(unittest.TestCase):
    """The username for the scrub comes from USER, else LOGNAME, and fails closed (no pwd lookup)."""

    ROWS = [{"type": "user", "uuid": "u", "parentUuid": None,
             "message": {"content": [{"type": "text", "text": "alice ran it; bob watched; carol was absent"}]}}]

    def scrub(self):
        return fixtures.normalize_transcript(self.ROWS, [], "")[0]["message"]["content"][0]["text"]

    def test_the_scrub_uses_user(self):
        with mock.patch.dict(os.environ, {"USER": "alice", "LOGNAME": "bob"}, clear=True):
            self.assertEqual(fixtures._current_username(), "alice")
            self.assertEqual(self.scrub(), "user ran it; bob watched; carol was absent")

    def test_the_scrub_falls_back_to_logname(self):
        for env in ({"LOGNAME": "bob"}, {"USER": "", "LOGNAME": "bob"}):
            with self.subTest(env=env):
                with mock.patch.dict(os.environ, env, clear=True):
                    self.assertEqual(fixtures._current_username(), "bob")
                    self.assertEqual(self.scrub(), "alice ran it; user watched; carol was absent")

    def test_the_scrub_raises_when_both_are_unset_or_empty(self):
        for env in ({}, {"USER": "", "LOGNAME": ""}, {"USER": ""}):
            with self.subTest(env=env):
                with mock.patch.dict(os.environ, env, clear=True):
                    with self.assertRaises(fixtures.UsernameError):
                        fixtures._current_username()
                    with self.assertRaises(fixtures.UsernameError):
                        fixtures.normalize_transcript(self.ROWS, [], "")

    def test_the_home_folder_comes_from_path_home(self):
        home = "/" + "Users" + "/alice"  # test fixture for the scrub: a home path built at runtime
        rows = [{"type": "user", "uuid": "u", "parentUuid": None,
                 "message": {"content": [{"type": "text", "text": "saved to " + home + "/work"}]}}]
        with mock.patch.dict(os.environ, {"USER": "zed"}, clear=True), \
                mock.patch.object(Path, "home", return_value=Path(home)):
            out = fixtures.normalize_transcript(rows, [], "")
        self.assertEqual(out[0]["message"]["content"][0]["text"], "saved to {{HOME}}/work")


class GenCostTests(unittest.TestCase):
    def test_rederive_turns_cumulative_costs_into_per_invocation_costs(self):
        gen = {"model": "m", "total_cost_usd": 0.36, "turns": [
            {"turn": 1, "cost_usd": 0.03, "duration_ms": 1}, {"turn": 2, "cost_usd": 0.05, "duration_ms": 2},
            {"turn": 3, "cost_usd": 0.09, "duration_ms": 3}, {"turn": 4, "cost_usd": 0.08, "duration_ms": 4}]}
        new = fixtures.rederive_gen_costs(gen)
        self.assertEqual([t["cost_usd"] for t in new["turns"]], [0.03, 0.02, 0.04, 0.08])  # turn 4 fell: full value
        self.assertEqual(new["total_cost_usd"], 0.17)
        self.assertEqual([t["duration_ms"] for t in new["turns"]], [1, 2, 3, 4])
        self.assertEqual(new["model"], "m")
        self.assertEqual(gen["turns"][1]["cost_usd"], 0.05)  # the input is not modified

    def test_a_non_numeric_turn_cost_makes_the_next_turn_full(self):
        gen = {"turns": [{"turn": 1, "cost_usd": None}, {"turn": 2, "cost_usd": 0.05}]}
        new = fixtures.rederive_gen_costs(gen)
        self.assertEqual([t["cost_usd"] for t in new["turns"]], [None, 0.05])
        self.assertEqual(new["total_cost_usd"], 0.05)


if __name__ == "__main__":
    unittest.main()


class GenerateTests(GenTestBase):
    def test_success_installs_normalized_files_and_counts_spend_in_the_generate_phase(self):
        runner = GenRunner(self.tmp, denials=["Bash"])
        code, out = self.generate(runner)
        self.assertEqual(code, 0, out)
        lines = out.strip().splitlines()
        self.assertEqual(lines[0], f"{self.FIXTURE}: turns 7/7 ok, isolation PASS, claude_md unchanged, denials 1, cost $0.70")
        self.assertEqual(lines[1], f"install-check {self.FIXTURE}: R1 token found; R0 token absent; PASS")
        rows = fixtures.read_jsonl(self.fdir / "transcript.jsonl")
        self.assertTrue(rows)
        self.assertEqual({r["type"] for r in rows}, {"user", "assistant"})
        text = (self.fdir / "transcript.jsonl").read_text(encoding="utf-8")
        for gone in ("thinking", "signature", "requestId", "usage", "SIG", str(self.tmp)):
            self.assertNotIn(gone, text)
        self.assertIn("{{SANDBOX}}", text)
        self.assertTrue((self.fdir / "repo" / "CLAUDE.md").is_file())
        self.assertTrue((self.fdir / "repo" / "gen-7.txt").is_file())
        self.assertFalse((self.fdir / "repo" / ".git").exists())
        gen = json.loads((self.fdir / "gen.json").read_text(encoding="utf-8"))
        self.assertEqual(len(gen["turns"]), 7)
        self.assertEqual(gen["denials"], {"count": 1, "tools": {"Bash": 1}, "in_transcript": 0})
        self.assertEqual(gen["model"], self.cfg["model"])
        self.assertAlmostEqual(claude_run.spend_total(self.environ, "generation"), 0.9)  # 7 turns, R1 and R0
        self.assertEqual(claude_run.spend_total(self.environ, "control"), 0.0)
        self.assertEqual(fixtures.validate_spec(json.loads((self.fdir / "spec.json").read_text(encoding="utf-8")),
                                                self.FIXTURE), [])

    def test_missing_tool_evidence_fails_the_attempt_and_regenerates(self):
        runner = GenRunner(self.tmp, evidence_attempts=(2,))
        code, out = self.generate(runner)
        self.assertEqual(code, 0, out)
        self.assertEqual(runner.attempt, 2)
        self.assertEqual(len(runner.calls), 7 + 7 + 2)

    def test_evidence_that_only_appears_in_a_denied_result_does_not_count(self):
        runner = GenRunner(self.tmp, denied_only=True)
        code, out = self.generate(runner)
        self.assertEqual(code, 1)
        self.assertIn("tool_evidence_missing:k1", out)
        self.assertEqual(len(runner.calls), 21)  # three attempts, no install-check reached
        self.assertFalse((self.fdir / "transcript.jsonl").exists())

    def test_every_run_goes_through_the_runner_in_the_generate_phase(self):
        runner = GenRunner(self.tmp)
        self.generate(runner)
        gen_calls = runner.calls[:7]
        self.assertEqual({c["phase"] for c in runner.calls}, {"generation"})
        self.assertEqual([c["resume"] is None for c in gen_calls], [True] + [False] * 6)
        self.assertTrue(all(c["allowed"] == claude_run.bash_tools(self.spec["allowed_bash"]) for c in gen_calls))
        self.assertTrue(all(c["prior"] for c in gen_calls[1:]))
        r1, r0 = runner.calls[7], runner.calls[8]
        self.assertTrue(r1["fork"] and r1["probe"] and r1["resume"])
        self.assertTrue(r0["probe"] and r0["resume"] is None and not r0["fork"])

    def test_raw_transcripts_are_copied_never_moved(self):
        runner = GenRunner(self.tmp)
        self.generate(runner)
        raw = list((runner.config_dir / "projects").rglob("*.jsonl"))
        self.assertGreaterEqual(len(raw), 3)
        self.assertTrue(any("thinking" in p.read_text(encoding="utf-8") for p in raw))
        self.assertFalse(any(str(runner.config_dir) in str(p) for p in self.fdir.rglob("*")))

    def test_a_session_that_edits_the_floor_is_regenerated(self):
        runner = GenRunner(self.tmp, floor_edit_attempts=(1,))
        code, out = self.generate(runner)
        self.assertEqual(code, 0, out)
        self.assertEqual(runner.attempt, 2)
        self.assertEqual(len(runner.calls), 7 + 7 + 2)

    def test_floor_edits_on_two_attempts_then_the_third_installs(self):
        runner = GenRunner(self.tmp, floor_edit_attempts=(1, 2))
        code, out = self.generate(runner)
        self.assertEqual(code, 0, out)
        self.assertEqual(runner.attempt, 3)
        self.assertTrue((self.fdir / "transcript.jsonl").exists())

    def test_three_floor_edits_fail_and_nothing_is_installed(self):
        code, out = self.generate(GenRunner(self.tmp, floor_edit_attempts=(1, 2, 3)))
        self.assertEqual(code, 1)
        self.assertIn("claude_md_changed", out)
        self.assertFalse((self.fdir / "transcript.jsonl").exists())
        self.assertFalse((self.fdir / "repo").exists())

    def test_a_run_that_fails_the_proof_is_not_installed(self):
        runner = GenRunner(self.tmp, proof_leak_turn=3)
        code, out = self.generate(runner)
        self.assertEqual(code, 1)
        self.assertIn("turn 3 proof: markers_found", out)
        self.assertNotIn(HOST_MARKER, out)
        self.assertEqual(len(runner.calls), 9)  # three attempts, stopped at turn 3
        self.assertFalse((self.fdir / "transcript.jsonl").exists())

    def test_the_token_check_gates_the_install(self):
        code, out = self.generate(GenRunner(self.tmp, r1_token=False))
        self.assertEqual((code, "R1 token missing" in out), (1, True))
        self.assertFalse((self.fdir / "transcript.jsonl").exists())
        shutil_rmtree(self.tmp / "cpr-eval")
        code, out = self.generate(GenRunner(self.tmp, r0_leak=True))
        self.assertEqual((code, "R0 token present" in out), (1, True))
        self.assertFalse((self.fdir / "transcript.jsonl").exists())

    def test_the_generate_cap_stops_a_launch_and_prints_a_label(self):
        self.cfg["phase_caps"]["generation"] = 0.25
        runner = GenRunner(self.tmp)

        def capped(*args, **kwargs):
            if claude_run.spend_total(self.environ, "generation") >= claude_run.phase_cap(self.cfg, "generation"):
                raise claude_run.SpendCapReached("spend cap reached")
            return runner(*args, **kwargs)
        out = io.StringIO()
        original = claude_run.load_config
        claude_run.load_config = lambda path=None: self.cfg
        self.addCleanup(setattr, claude_run, "load_config", original)
        original_pre = claude_run.preflight
        claude_run.preflight = lambda cfg, environ=None: ""
        self.addCleanup(setattr, claude_run, "preflight", original_pre)
        code = fixtures.command_gen_fixture(self.FIXTURE, self.environ, out, capped, self.root)
        self.assertEqual(code, 1)
        self.assertIn(": STOP spend cap reached", out.getvalue())
        self.assertFalse((self.fdir / "transcript.jsonl").exists())

    def test_install_check_command_on_an_installed_fixture(self):
        self.generate(GenRunner(self.tmp))
        out = io.StringIO()
        original = claude_run.preflight
        claude_run.preflight = lambda cfg, environ=None: ""
        self.addCleanup(setattr, claude_run, "preflight", original)
        code = fixtures.command_install_check(self.FIXTURE, self.environ, out, GenRunner(self.tmp), self.root)
        self.assertEqual(code, 0, out.getvalue())
        self.assertEqual(out.getvalue().strip(), f"install-check {self.FIXTURE}: R1 token found; R0 token absent; PASS")
        (self.fdir / "transcript.jsonl").unlink()
        out = io.StringIO()
        self.assertEqual(fixtures.command_install_check(self.FIXTURE, self.environ, out, GenRunner(self.tmp), self.root), 1)
        self.assertIn("FAIL fixture not generated", out.getvalue())

    def test_gen_fixture_and_install_check_refuse_an_excluded_fixture_before_any_launch(self):
        spec = json.loads((self.fdir / "spec.json").read_text(encoding="utf-8"))
        spec["excluded"] = {"date": "2026-10-18", "reason": "Not run."}
        (self.fdir / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
        original = claude_run.preflight
        claude_run.preflight = lambda cfg, environ=None: self.fail("preflight reached")
        self.addCleanup(setattr, claude_run, "preflight", original)
        for command, label in ((fixtures.command_gen_fixture, self.FIXTURE),
                               (fixtures.command_install_check, f"install-check {self.FIXTURE}")):
            with self.subTest(label):
                out = io.StringIO()
                runner = GenRunner(self.tmp)
                self.assertEqual(command(self.FIXTURE, self.environ, out, runner, self.root), 1)
                self.assertEqual(out.getvalue().strip(), f"{label}: STOP fixture excluded")
                self.assertEqual(runner.calls, [])
        self.assertFalse((self.fdir / "transcript.jsonl").exists())

    def test_live_gate_and_config_dir_gate(self):
        env = dict(self.environ)
        env.pop("CPR_EVAL_LIVE")
        for command in (fixtures.command_gen_fixture, fixtures.command_install_check):
            out = io.StringIO()
            runner = GenRunner(self.tmp)
            self.assertEqual(command(self.FIXTURE, env, out, runner, self.root), 2)
            self.assertIn("live run refused: set CPR_EVAL_LIVE=1", out.getvalue())
            self.assertEqual(runner.calls, [])
        out = io.StringIO()
        self.assertEqual(fixtures.command_gen_fixture(self.FIXTURE, dict(self.environ, CLAUDE_CONFIG_DIR="x"), out,
                                                      GenRunner(self.tmp), self.root), 3)

    def test_cli_refuses_without_the_live_flag(self):
        env = dict(os.environ)
        env.pop("CPR_EVAL_LIVE", None)
        gated = Path(tempfile.mkdtemp())  # the C6 gate must be open for the command to reach the live check
        self.addCleanup(shutil.rmtree, gated, True)
        (gated / "cpr-eval").mkdir()
        (gated / "cpr-eval" / "gates.json").write_text(json.dumps(
            {**{k: {"outcome": "PASS", "token": "0" * 64, "started_utc": "2026-10-23T00:00:00Z"} for k in ("C6", "C7")},
             "C1-5": {"outcome": "PASS", "started_utc": "2026-10-23T00:00:00Z", "run_id": "r-1"}}),
            encoding="utf-8")
        env["TMPDIR"] = str(gated)
        for command in ("gen-fixture", "install-check"):
            proc = subprocess.run([sys.executable, exp0001_paths.RUN, command, "dev-001-tide-table"],
                                  capture_output=True, text=True, encoding="utf-8", env=env)
            self.assertEqual(proc.returncode, 2)
            self.assertIn("live run refused: set CPR_EVAL_LIVE=1", proc.stdout)

    def test_unknown_fixture_ids_fail_with_a_label(self):
        out = io.StringIO()
        code = fixtures.generate_fixture("dev-099-none", self.cfg, self.environ, GenRunner(self.tmp), self.root, out)
        self.assertEqual((code, out.getvalue().strip()), (1, "dev-099-none: FAIL spec not found"))
