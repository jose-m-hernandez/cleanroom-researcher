import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import check_experiments
from lib import header, paths
from tests import test_check_admission as tca

REAL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MISSING_SOURCE = "SRC-" + "0009"
SECOND_SOURCE = "SRC-" + "0002"
GOOD_COMMIT = "a" * 40
VOCABULARY_PATH = "docs/vocabulary.md"
README = "experiments/EXP-0001/README.md"
TARGET = "experiments/EXP-0001/target/x.py"
SOURCE = "sources/SRC-0001/source.md"
READ_LOG = "experiments/EXP-0001/read-log.md"
READ_LOG_EMPTY = "| when | path | sha256 | reason |\n| --- | --- | --- | --- |\n"


def table(title, values):
    lines = ["## " + title, "", "| Value | Meaning |", "| --- | --- |"]
    lines.extend("| `%s` | Something. |" % v for v in values)
    return "\n".join(lines) + "\n\n"


VOCAB = (
    "# Vocabulary\n\n"
    + table("Source types", ["standard-spec", "code-repo", "release-artifact"])
    + table("Experiment protocols", ["preregistered", "exploratory"])
    + table("Read-log kinds", ["contemporaneous", "reconstructed"])
    + table("Redistributable licences", ["MIT", "Apache-2.0", "CC-BY-4.0"])
)


def readme_md(key_order=None, **over):
    values = {
        "id": "EXP-0001",
        "subject": "method",
        "protocol": "preregistered",
        "read_log": "contemporaneous",
        "spend_cap_usd": "5",
        "launched_by": "someone",
    }
    values.update(over)
    lines = ["# An experiment", ""]
    for key in (header.EXPERIMENT_KEYS if key_order is None else key_order):
        if values.get(key) is not None:
            lines.append("%s: %s" % (key, values[key]))
    return "\n".join(lines) + "\n"


def code_repo_md(**over):
    values = {"source_type": "code-repo", "commit": GOOD_COMMIT, "licence": "MIT"}
    values.update(over)
    return tca.source_md(key_order=header.CODE_REPO_KEYS, **values)


class ExperimentCase(tca.RepoCase):
    def make(self, extra=None, vocab=VOCAB, source=None, read_log=True):
        files = {"notes.txt": "hello\n", SOURCE: source or tca.source_md()}
        if vocab is not None:
            files[VOCABULARY_PATH] = vocab
        files.update(extra or {})
        if read_log and README in files:
            files.setdefault(READ_LOG, READ_LOG_EMPTY)
        self.build(files)

    def result(self):
        code, lines = check_experiments.run(self.root)
        return code, lines

    def finding_lines(self):
        code, lines = self.result()
        return code, [ln for ln in lines if ": E" in ln]

    def assertFinding(self, ident, path):
        code, found = self.finding_lines()
        self.assertEqual(code, 1, found)
        hits = [ln for ln in found if ident in ln and (path + ": ") in ln]
        self.assertEqual(len(hits), 1, found)

    def assertClean(self):
        code, lines = self.result()
        self.assertEqual(code, 0, lines)
        self.assertTrue(lines[-1].startswith("experiments: ok ("), lines)


class E1Tests(ExperimentCase):
    def test_a_well_formed_experiment_is_clean(self):
        self.make({README: readme_md()})
        self.assertClean()

    def test_summary_counts_experiments_and_sources(self):
        self.make({README: readme_md()})
        code, lines = self.result()
        self.assertEqual(lines[-1], "experiments: ok (1 experiments, 1 sources, 0 syntheses)")

    def test_a_repository_without_experiments_is_clean(self):
        self.make()
        code, lines = self.result()
        self.assertEqual(code, 0, lines)
        self.assertEqual(lines[-1], "experiments: ok (0 experiments, 1 sources, 0 syntheses)")

    def test_file_directly_under_experiments_is_a_finding(self):
        self.make({"experiments/notes.md": "x\n"})
        self.assertFinding("E1 files under experiments/ must sit inside an experiment folder", "experiments/notes.md")

    def test_folder_name_must_have_the_id_shape(self):
        self.make({"experiments/exp-1/README.md": readme_md(id="exp-1")})
        self.assertFinding("E1 experiment folder 'exp-1'", "experiments/exp-1")

    def test_missing_readme_is_a_finding(self):
        self.make({"experiments/EXP-0001/notes.md": "x\n"})
        self.assertFinding("E1 the experiment has no README.md", "experiments/EXP-0001")

    def test_header_problem_carries_its_line_number(self):
        self.make({README: "# T\nid: EXP-0001\n"})
        code, found = self.finding_lines()
        self.assertEqual(code, 1, found)
        self.assertTrue(any(ln.startswith("experiments: %s:2: E1 " % README) for ln in found), found)

    def test_missing_key_is_a_finding(self):
        self.make({README: readme_md(launched_by=None)})
        self.assertFinding("E1 the README header keys must be exactly", README)

    def test_keys_in_the_wrong_order_are_a_finding(self):
        order = ("id", "subject", "read_log", "protocol", "spend_cap_usd", "launched_by")
        self.make({README: readme_md(key_order=order)})
        self.assertFinding("E1 the README header keys must be exactly", README)

    def test_id_must_equal_the_folder(self):
        self.make({README: readme_md(id="EXP-0002")})
        self.assertFinding("E1 id is 'EXP-0002' but the folder is 'EXP-0001'", README)

    def test_subject_naming_a_missing_source_is_a_finding(self):
        self.make({README: readme_md(subject=MISSING_SOURCE)})
        self.assertFinding(
            "E1 subject names %s, which has no sources/%s/source.md" % (MISSING_SOURCE, MISSING_SOURCE),
            README,
        )

    def test_subject_naming_a_listed_source_is_clean(self):
        self.make({README: readme_md(subject="SRC-0001")})
        self.assertClean()

    def test_subject_that_is_neither_method_nor_an_id_is_a_finding(self):
        self.make({README: readme_md(subject="whatever")})
        self.assertFinding("E1 subject 'whatever' is neither method nor a source id", README)

    def test_unknown_protocol_is_a_finding(self):
        self.make({README: readme_md(protocol="confirmatory")})
        self.assertFinding("E1 protocol 'confirmatory'", README)

    def test_unknown_read_log_kind_is_a_finding(self):
        self.make({README: readme_md(read_log="live")})
        self.assertFinding("E1 read_log 'live'", README)

    def test_missing_vocabulary_is_one_finding(self):
        self.make({README: readme_md()}, vocab=None)
        self.assertFinding("E1 cannot read the Experiment protocols and Read-log kinds", VOCABULARY_PATH)

    def test_spend_cap_and_launched_by_values_are_not_checked_here(self):
        self.make({README: readme_md(spend_cap_usd="lots", launched_by="anyone at all")})
        code, found = self.finding_lines()
        self.assertEqual([ln for ln in found if " E1 " in ln], [], found)
        # E7 now checks spend_cap_usd, so 'lots' is an E7 finding (see E7Tests too).
        self.assertFinding("E7", README)


class E2Tests(ExperimentCase):
    def with_source(self, text):
        self.make({"sources/%s/source.md" % SECOND_SOURCE: text.replace("SRC-0001", SECOND_SOURCE)})

    def test_pinned_code_repo_is_clean(self):
        self.with_source(code_repo_md(id="SRC-0001"))
        self.assertClean()

    def test_short_commit_is_a_finding(self):
        self.with_source(code_repo_md(id="SRC-0001", commit="a" * 7))
        self.assertFinding("E2 commit must be 40 lowercase hex", "sources/%s/source.md" % SECOND_SOURCE)

    def test_upper_case_commit_is_a_finding(self):
        self.with_source(code_repo_md(id="SRC-0001", commit="A" * 40))
        self.assertFinding("E2 commit must be 40 lowercase hex", "sources/%s/source.md" % SECOND_SOURCE)

    def test_code_repo_without_a_licence_is_a_finding(self):
        self.with_source(code_repo_md(id="SRC-0001", licence="-"))
        self.assertFinding("E2 a code-repo source needs a licence", "sources/%s/source.md" % SECOND_SOURCE)

    def test_release_artifact_without_a_licence_is_a_finding(self):
        self.with_source(tca.source_md(id="SRC-0001", source_type="release-artifact", licence="-"))
        self.assertFinding("E2 a release-artifact source needs a licence", "sources/%s/source.md" % SECOND_SOURCE)

    def test_release_artifact_with_a_licence_is_clean(self):
        self.with_source(tca.source_md(id="SRC-0001", source_type="release-artifact", licence="MIT"))
        self.assertClean()

    def test_standard_spec_without_a_licence_is_clean(self):
        self.with_source(tca.source_md(id="SRC-0001", source_type="standard-spec", licence="-"))
        self.assertClean()

    def test_a_record_with_a_broken_header_is_skipped(self):
        self.with_source("not a header\n")
        self.assertClean()


class E3Tests(ExperimentCase):
    def with_subject(self, subject, licence="MIT", extra=None):
        files = {README: readme_md(subject=subject), TARGET: "x = 1\n"}
        files[SOURCE] = tca.source_md(licence=licence)
        files.update(extra or {})
        self.make(files, source=files.pop(SOURCE))

    def test_target_under_a_method_experiment_is_a_finding(self):
        self.with_subject("method")
        self.assertFinding("E3 experiment EXP-0001 has subject method", TARGET)

    def test_target_under_a_redistributable_source_is_clean(self):
        self.with_subject("SRC-0001", licence="MIT")
        self.assertClean()

    def test_target_under_a_copyleft_source_is_a_finding_that_names_it(self):
        self.with_subject("SRC-0001", licence="GPL-3.0")
        self.assertFinding("E3 licence 'GPL-3.0' of SRC-0001", TARGET)

    def test_target_under_a_non_commercial_source_is_a_finding(self):
        self.with_subject("SRC-0001", licence="CC-BY-NC-4.0")
        self.assertFinding("E3 licence 'CC-BY-NC-4.0' of SRC-0001", TARGET)

    def test_nested_target_folder_is_not_target_class(self):
        self.with_subject("method", extra={"experiments/EXP-0001/code/target/x.py": "x = 1\n"})
        code, found = self.finding_lines()
        self.assertEqual([ln for ln in found if "code/target" in ln], [])
        self.assertEqual(len([ln for ln in found if " E3 " in ln]), 1, found)

    def test_targets_folder_is_not_target_class(self):
        files = {README: readme_md(), "experiments/EXP-0001/targets/x.py": "x = 1\n"}
        self.make(files)
        self.assertClean()

    def test_nested_target_folder_alone_is_clean(self):
        files = {README: readme_md(), "experiments/EXP-0001/code/target/x.py": "x = 1\n"}
        self.make(files)
        self.assertClean()

    def test_target_under_a_broken_readme_is_one_e3_finding_plus_the_e1_findings(self):
        self.make({README: readme_md(launched_by=None), TARGET: "x = 1\n"})
        code, found = self.finding_lines()
        self.assertEqual(code, 1, found)
        e3 = [ln for ln in found if " E3 " in ln]
        self.assertEqual(len(e3), 1, found)
        self.assertIn("E3 cannot check the licence because the experiment record is invalid", e3[0])
        self.assertIn(TARGET, e3[0])
        self.assertTrue(any(" E1 " in ln for ln in found), found)

    def test_target_under_a_missing_source_is_a_finding(self):
        self.make({README: readme_md(subject=MISSING_SOURCE), TARGET: "x = 1\n"})
        self.assertFinding("E3 cannot check the licence because source %s is missing" % MISSING_SOURCE, TARGET)

    def test_target_under_a_broken_source_record_is_a_finding(self):
        self.make({README: readme_md(subject="SRC-0001"), TARGET: "x = 1\n"}, source="not a header\n")
        self.assertFinding("E3 cannot check the licence because source SRC-0001 is missing or its record is broken", TARGET)

    def test_missing_vocabulary_is_one_finding(self):
        files = {README: readme_md(subject="SRC-0001"), TARGET: "x = 1\n"}
        self.make(files, vocab=None)
        self.assertFinding("E3 cannot read the Redistributable licences table", VOCABULARY_PATH)

    def test_licence_list_is_not_the_source_type_list(self):
        vocab = VOCAB.replace("| `MIT` |", "| `Zlib` |", 1)
        self.make({README: readme_md(subject="SRC-0001"), TARGET: "x = 1\n"}, vocab=vocab)
        self.assertFinding("E3 licence '-' of SRC-0001", TARGET)


VOCAB_ALL = (
    VOCAB
    + table("Read-log reasons", ["interface", "method", "verification"])
    + table("Evidence grades", ["verified", "reported", "inferred", "speculative", "unknown"])
)
IDENTITY = ["-c", "user.name=t", "-c", "user.email=t@example.com"]
RECORDED = "experiments/EXP-0001/recorded/data.txt"
METHOD_FILE = "experiments/EXP-0001/%s/x.py" % paths.METHOD_DIR
RUN_OUT = "experiments/EXP-0001/recorded/runs/r1/out.txt"
PROOF = "experiments/EXP-0001/recorded/runs/r1/isolation.json"
PROOF_2 = "experiments/EXP-0001/recorded/runs/r2/isolation.json"
RUN_OUT_2 = "experiments/EXP-0001/recorded/runs/r2/out.txt"
GOOD_PROOF = json.dumps({"isolated": True, "checks": ["no network"]})
SYN = "syntheses/SYN-0001.md"
FINDING = "### A finding\nsources: SRC-0001\ngrade: verified\nfalsifier: A run that shows otherwise.\n"
GOOD_SYN = "# Synthesis\n\n" + FINDING + "\n### Another\nsources: SRC-0001, SRC-0001\ngrade: inferred\nfalsifier: Something.\n"


def log_md(*rows):
    return READ_LOG_EMPTY + "".join(rows)


def log_row(when="2026-01-02T00:00Z", path="a.txt", sha="a" * 64, reason="method"):
    return "| %s | %s | %s | %s |\n" % (when, path, sha, reason)


def with_abstract(text, abstract):
    return text + "\n" + abstract + "\n"


class Case(ExperimentCase):
    """ExperimentCase with the full vocabulary, git helpers and line-aware assertions."""

    def make(self, extra=None, vocab=VOCAB_ALL, source=None, read_log=True):
        super().make(extra, vocab, source, read_log)

    def git(self, *args, date=None):
        env = dict(os.environ)
        if date is not None:
            env["GIT_COMMITTER_DATE"] = date
        proc = subprocess.run(
            ["git", "-C", self.root] + list(args),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, check=True,
        )
        return proc.stdout.decode().strip()

    def commit(self, message="m", date=None):
        self.git("add", "-A")
        self.git(*(IDENTITY + ["commit", "-q", "-m", message]), date=date)

    def put(self, path, data):
        tca.write(self.root, path, data)

    def rule_lines(self, rule):
        code, lines = self.result()
        return code, [ln for ln in lines if re.search(r": %s " % rule, ln)]

    def assertHit(self, rule, path, count=1):
        code, hits = self.rule_lines(rule)
        mine = [ln for ln in hits if ln.startswith("experiments: " + path + ":")]
        self.assertEqual(code, 1, hits)
        self.assertEqual(len(mine), count, hits)

    def assertNone(self, rule):
        code, hits = self.rule_lines(rule)
        self.assertEqual(hits, [])


class PathsTests(unittest.TestCase):
    def test_runner_root_is_unchanged(self):
        self.assertEqual(paths.RUNNER_ROOT.pattern, "^experiments/[^/]+/code/runner/")

    def test_method_folder_paths(self):
        self.assertTrue(paths.METHOD_FILE.match("experiments/EXP-0001/%s/a/b.py" % paths.METHOD_DIR))
        self.assertIsNone(paths.METHOD_FILE.match("experiments/EXP-0001/other/b.py"))

    def test_a_file_nested_inside_a_run_folder_belongs_to_that_run(self):
        deep = "experiments/EXP-0001/recorded/runs/r1/sub/deeper/out.txt"
        self.assertEqual(paths.RUN_FILE.match(deep).groups(), ("EXP-0001", "r1"))

    def test_an_experiment_id_has_exactly_four_digits(self):
        self.assertTrue(paths.EXPERIMENT_ID.match("EXP-0001"))
        self.assertIsNone(paths.EXPERIMENT_ID.match("EXP-001"))
        self.assertIsNone(paths.EXPERIMENT_ID.match("EXP-00001"))

    def test_a_synthesis_name_ends_at_md(self):
        self.assertIsNone(paths.SYNTHESIS_FILE.match("syntheses/SYN-0001.md.bak"))
        self.assertIsNone(paths.SYNTHESIS_FILE.match("syntheses/SYN-0001.mdx"))

    def test_recorded_run_and_synthesis_paths(self):
        self.assertEqual(paths.RUN_FILE.match(RUN_OUT).groups(), ("EXP-0001", "r1"))
        self.assertIsNone(paths.RUN_FILE.match(RECORDED))
        self.assertTrue(paths.RECORDED_FILE.match(RECORDED))
        self.assertTrue(paths.SYNTHESIS_FILE.match(SYN))
        self.assertIsNone(paths.SYNTHESIS_FILE.match("syntheses/notes.md"))


class ReadLogTests(Case):
    def test_a_missing_read_log_is_a_finding(self):
        self.make({README: readme_md()}, read_log=False)
        self.assertHit("E6", READ_LOG)

    def test_a_bad_header_row_is_a_finding(self):
        self.make({README: readme_md(), READ_LOG: log_md().replace("sha256", "digest")})
        self.assertHit("E6", READ_LOG)

    def test_each_bad_cell_is_a_finding(self):
        # The reason is interface, so E6b cannot add a finding at the same file:
        # each hit below is the cell check alone.
        cases = {
            "when without Z": (log_row(when="2026-01-02T00:00", reason="interface"), "when '2026-01-02T00:00' is not a real UTC time"),
            "impossible date": (log_row(when="2026-02-30T00:00Z", reason="interface"), "when '2026-02-30T00:00Z' is not a real UTC time"),
            "wrong year only": (log_row(when="2026-13-01T00:00Z", reason="interface"), "is not a real UTC time"),
            "empty path": (log_row(path="", reason="interface"), "path is empty"),
            "short sha256": (log_row(sha="a" * 63, reason="interface"), "sha256 must be 64 lowercase hex"),
            "sha256 too long": (log_row(sha="a" * 65, reason="interface"), "sha256 must be 64 lowercase hex"),
            "reason skim": (log_row(reason="skim"), "reason 'skim' is not in the Read-log reasons table"),
        }
        for name, (row, text) in cases.items():
            with self.subTest(name):
                self.make({README: readme_md(), READ_LOG: log_md(row)})
                code, hits = self.rule_lines("E6")
                mine = [ln for ln in hits if ln.startswith("experiments: " + READ_LOG + ":")]
                self.assertEqual(code, 1, hits)
                self.assertEqual(len(mine), 1, hits)
                self.assertIn(text, mine[0])

    def test_a_short_sha256_is_not_accepted_as_a_prefix_of_a_digest(self):
        for length in (6, 7, 40, 63):
            with self.subTest(length):
                self.make({README: readme_md(), READ_LOG: log_md(log_row(sha="a" * length, reason="interface"))})
                code, hits = self.rule_lines("E6")
                self.assertEqual(code, 1, hits)

    def test_two_tables_are_a_finding(self):
        log = log_md(log_row(reason="interface")) + "\nSome words.\n\n" + READ_LOG_EMPTY
        self.make({README: readme_md(), READ_LOG: log})
        code, hits = self.rule_lines("E6")
        self.assertEqual(code, 1, hits)
        self.assertIn("exactly one table", hits[0])

    def test_a_table_with_no_separator_row_is_a_finding(self):
        log = "| when | path | sha256 | reason |\n" + log_row(reason="interface")
        self.make({README: readme_md(), READ_LOG: log})
        code, hits = self.rule_lines("E6")
        self.assertEqual(code, 1, hits)
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("no separator row", hits[0])

    def test_a_row_with_five_cells_is_a_finding(self):
        row = "| 2026-01-02T00:00Z | a.txt | " + "a" * 64 + " | interface | extra |\n"
        self.make({README: readme_md(), READ_LOG: log_md(row)})
        code, hits = self.rule_lines("E6")
        self.assertEqual(code, 1, hits)
        self.assertIn("malformed read-log row", hits[0])

    def test_a_row_with_three_cells_is_a_finding(self):
        row = "| 2026-01-02T00:00Z | a.txt | interface |\n"
        self.make({README: readme_md(), READ_LOG: log_md(row)})
        code, hits = self.rule_lines("E6")
        self.assertEqual(code, 1, hits)
        self.assertIn("malformed read-log row", hits[0])

    def test_a_real_time_is_read_as_that_time(self):
        self.assertEqual(check_experiments.when_seconds("2026-03-04T05:06Z"), 1772600760)
        self.assertEqual(check_experiments.when_seconds("2026-12-31T23:59Z") - check_experiments.when_seconds("2026-01-01T00:00Z"), 364 * 86400 + 23 * 3600 + 59 * 60)
        self.assertIsNone(check_experiments.when_seconds("2026-02-30T10:00Z"))
        self.assertIsNone(check_experiments.when_seconds("2026-04-31T10:00Z"))
        self.assertIsNone(check_experiments.when_seconds("2026-01-01T24:00Z"))

    def test_a_good_row_is_clean(self):
        self.make({README: readme_md(), READ_LOG: log_md(log_row(reason="interface"))})
        self.assertClean()


class E6aTests(Case):
    def test_readme_committed_before_recorded_data_is_clean(self):
        self.make({README: readme_md()})
        self.commit("readme")
        self.put(RECORDED, "data\n")
        self.commit("data")
        self.assertClean()

    def test_readme_and_recorded_data_in_one_commit_is_a_finding(self):
        self.make({README: readme_md(), RECORDED: "data\n"})
        self.commit("both")
        self.assertHit("E6", README)

    def test_readme_and_recorded_data_together_in_a_later_commit_is_a_finding(self):
        self.make()
        self.commit("base")
        self.put(README, readme_md())
        self.put(READ_LOG, READ_LOG_EMPTY)
        self.put(RECORDED, "data\n")
        self.commit("both, not the first commit")
        self.assertHit("E6", README)

    def test_recorded_data_on_a_side_branch_cut_before_the_readme_is_a_finding(self):
        self.make()
        self.commit("base")
        base = self.git("rev-parse", "HEAD")
        self.put(README, readme_md())
        self.put(READ_LOG, READ_LOG_EMPTY)
        self.commit("readme")
        self.git("checkout", "-q", "-b", "side", base)
        self.put(RECORDED, "data\n")
        self.commit("data on the side")
        self.git("checkout", "-q", "-")
        self.git(*(IDENTITY + ["merge", "-q", "--no-ff", "side", "-m", "m"]))
        self.assertHit("E6", README)

    def test_a_side_commit_that_simplification_would_drop_is_still_found(self):
        self.make()
        self.commit("base")
        base = self.git("rev-parse", "HEAD")
        self.put(README, readme_md())
        self.put(READ_LOG, READ_LOG_EMPTY)
        self.commit("readme")
        self.put(RECORDED, "data\n")
        self.commit("data on the main branch")
        self.git("checkout", "-q", "-b", "side", base)
        self.put(RECORDED, "data\n")
        self.commit("the same data on the side, before the readme")
        self.git("checkout", "-q", "-")
        self.git(*(IDENTITY + ["merge", "-q", "--no-ff", "side", "-m", "m"]))
        self.assertHit("E6", README)

    def test_exploratory_with_readme_and_data_in_one_commit_is_clean(self):
        readme = with_abstract(readme_md(protocol="exploratory"), "This run is exploratory.")
        self.make({README: readme, RECORDED: "data\n"})
        self.commit("both")
        self.assertClean()


class E6bTests(Case):
    def build_and_commit(self, date, log_rows, **over):
        readme = readme_md(**over)
        if over.get("read_log") == "reconstructed":
            readme = with_abstract(readme, "The read log was reconstructed.")
        self.make({README: readme, READ_LOG: log_md(*log_rows), METHOD_FILE: "x = 1\n"})
        self.commit("method", date=date)

    def test_method_committed_before_the_entry_is_clean(self):
        self.build_and_commit("2026-01-01T00:00:00Z", [log_row(when="2026-01-02T00:00Z")])
        self.assertClean()

    def test_method_committed_after_the_entry_is_a_finding(self):
        self.build_and_commit("2026-01-03T00:00:00Z", [log_row(when="2026-01-02T00:00Z")])
        self.assertHit("E6", READ_LOG)

    def test_a_method_entry_with_no_method_commit_is_a_finding(self):
        self.make({README: readme_md(), READ_LOG: log_md(log_row())})
        self.commit("no method")
        self.assertHit("E6", READ_LOG)

    def test_reconstructed_with_a_late_method_folder_is_clean(self):
        self.build_and_commit(
            "2026-01-03T00:00:00Z", [log_row(when="2026-01-02T00:00Z")], read_log="reconstructed"
        )
        self.assertClean()

    def test_a_commit_at_the_same_time_as_the_entry_is_clean(self):
        self.build_and_commit("2026-01-02T00:00:00Z", [log_row(when="2026-01-02T00:00Z")])
        self.assertClean()

    def test_a_commit_seconds_after_the_entry_is_a_finding(self):
        self.build_and_commit("2026-01-02T00:00:30Z", [log_row(when="2026-01-02T00:00Z")])
        self.assertHit("E6", READ_LOG)

    def test_the_earliest_of_two_method_entries_is_the_one_compared(self):
        rows = [log_row(when="2026-01-05T00:00Z"), log_row(when="2026-01-02T00:00Z")]
        self.build_and_commit("2026-01-03T00:00:00Z", rows)
        code, hits = self.rule_lines("E6")
        self.assertEqual(code, 1, hits)
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("later than the earliest method entry at 2026-01-02T00:00Z", hits[0])

    def test_the_first_of_two_method_commits_is_the_one_compared(self):
        self.build_and_commit("2026-01-01T00:00:00Z", [log_row(when="2026-01-02T00:00Z")])
        self.put(METHOD_FILE, "x = 2\n")
        self.commit("method again", date="2026-01-04T00:00:00Z")
        self.assertClean()

    def test_an_interface_entry_before_the_method_commit_is_clean(self):
        self.build_and_commit(
            "2026-01-03T00:00:00Z", [log_row(when="2026-01-02T00:00Z", reason="interface")]
        )
        self.assertClean()


class E6cTests(Case):
    def test_exploratory_without_an_abstract_is_a_finding(self):
        self.make({README: readme_md(protocol="exploratory")})
        self.assertHit("E6", README)

    def test_the_word_in_a_later_paragraph_is_a_finding(self):
        text = with_abstract(readme_md(protocol="exploratory"), "A first paragraph.\n\nThis is exploratory.")
        self.make({README: text})
        self.assertHit("E6", README)

    def test_the_word_opening_the_abstract_is_clean(self):
        text = with_abstract(readme_md(protocol="exploratory"), "Exploratory work on a thing.")
        self.make({README: text})
        self.assertClean()

    def test_reconstructed_without_the_word_is_a_finding(self):
        text = with_abstract(readme_md(read_log="reconstructed"), "Plain words only.")
        self.make({README: text})
        self.assertHit("E6", README)

    def test_preregistered_and_contemporaneous_need_no_abstract(self):
        self.make({README: readme_md()})
        self.assertClean()

    def test_the_word_inside_another_word_is_a_finding(self):
        text = with_abstract(readme_md(protocol="exploratory"), "A nonexploratory piece.")
        self.make({README: text})
        self.assertHit("E6", README)


class E7Tests(Case):
    def live(self, proof=GOOD_PROOF, extra=None, **over):
        files = {README: readme_md(**over), RUN_OUT: "out\n"}
        if proof is not None:
            files[PROOF] = proof
        files.update(extra or {})
        self.make(files)

    def test_no_runs_and_a_dash_cap_is_clean(self):
        self.make({README: readme_md(spend_cap_usd="-")})
        self.assertClean()

    def test_no_runs_and_a_zero_cap_is_a_finding(self):
        self.make({README: readme_md(spend_cap_usd="0")})
        self.assertHit("E7", README)

    def test_a_cap_with_three_decimals_is_a_finding(self):
        self.make({README: readme_md(spend_cap_usd="5.123")})
        self.assertHit("E7", README)

    def test_a_run_with_a_good_proof_is_clean(self):
        self.live()
        self.assertClean()

    def test_a_run_with_no_launcher_is_a_finding(self):
        self.live(launched_by="-")
        self.assertHit("E7", README)

    def test_a_run_with_a_dash_cap_is_a_finding(self):
        self.live(spend_cap_usd="-")
        self.assertHit("E7", README)

    def test_a_run_folder_without_isolation_json_is_a_finding(self):
        self.live(proof=None)
        self.assertHit("E7", PROOF)

    def test_isolated_must_be_the_boolean_true(self):
        self.live(proof=json.dumps({"isolated": 1, "checks": ["x"]}))
        self.assertHit("E7", PROOF)

    def test_checks_must_be_a_list_not_a_string(self):
        self.live(proof=json.dumps({"isolated": True, "checks": "abc"}))
        self.assertHit("E7", PROOF)

    def test_checks_must_not_be_empty(self):
        self.live(proof=json.dumps({"isolated": True, "checks": []}))
        self.assertHit("E7", PROOF)

    def test_checks_must_hold_non_empty_strings(self):
        self.live(proof=json.dumps({"isolated": True, "checks": [""]}))
        self.assertHit("E7", PROOF)

    def test_invalid_json_is_a_finding(self):
        self.live(proof="{not json")
        self.assertHit("E7", PROOF)

    def test_a_json_list_is_a_finding(self):
        self.live(proof="[1, 2]")
        self.assertHit("E7", PROOF)

    def test_two_runs_with_one_proof_is_one_finding(self):
        self.live(extra={RUN_OUT_2: "out\n"})
        code, hits = self.rule_lines("E7")
        self.assertEqual(code, 1, hits)
        self.assertEqual(len(hits), 1, hits)
        self.assertIn(PROOF_2, hits[0])


class SynthesisTests(Case):
    def syn(self, text, path=SYN):
        self.make({path: text})

    def test_a_good_synthesis_is_clean(self):
        self.syn(GOOD_SYN)
        code, lines = self.result()
        self.assertEqual(code, 0, lines)
        self.assertEqual(lines[-1], "experiments: ok (0 experiments, 1 sources, 1 syntheses)")

    def test_a_file_that_is_not_a_synthesis_name_is_a_finding(self):
        self.syn(GOOD_SYN, path="syntheses/notes.md")
        self.assertHit("S1", "syntheses/notes.md")
        code, hits = self.rule_lines("S1")
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("must be syntheses/SYN-NNNN.md", hits[0])

    def test_a_synthesis_with_no_finding_is_a_finding(self):
        self.syn("# Synthesis\n\nJust prose.\n")
        self.assertHit("S1", SYN)

    def test_a_missing_sources_line_is_a_finding(self):
        self.syn("# S\n\n### F\ngrade: verified\nfalsifier: x\n")
        self.assertHit("S1", SYN)

    def test_sources_that_are_not_ids_are_a_finding(self):
        self.syn(FINDING.replace("sources: SRC-0001", "sources: none"))
        self.assertHit("S1", SYN)

    def test_a_trailing_comma_in_sources_is_a_finding(self):
        self.syn(FINDING.replace("sources: SRC-0001", "sources: SRC-0001,"))
        self.assertHit("S1", SYN)

    def test_a_missing_grade_is_a_finding(self):
        self.syn(FINDING.replace("grade: verified\n", ""))
        self.assertHit("S2", SYN)

    def test_an_unknown_grade_is_a_finding(self):
        self.syn(FINDING.replace("grade: verified", "grade: likely"))
        self.assertHit("S2", SYN)

    def test_a_missing_falsifier_is_a_finding(self):
        self.syn(FINDING.replace("falsifier: A run that shows otherwise.\n", ""))
        self.assertHit("S3", SYN)

    def test_a_blank_falsifier_is_a_finding(self):
        self.syn(FINDING.replace("falsifier: A run that shows otherwise.", "falsifier:   "))
        self.assertHit("S3", SYN)

    def test_a_field_given_twice_is_a_finding(self):
        self.syn(FINDING + "grade: reported\n")
        self.assertHit("S2", SYN)

    def test_a_missing_grades_table_is_one_finding(self):
        self.make({SYN: GOOD_SYN}, vocab=None)
        code, hits = self.rule_lines("S2")
        self.assertEqual(code, 1, hits)
        self.assertEqual(len(hits), 1, hits)


class ShallowCloneTests(Case):
    def clone(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        target = os.path.join(holder.name, "clone")
        subprocess.run(
            ["git", "clone", "-q", "--depth", "1", "file://" + self.root, target],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        )
        return target

    def test_a_shallow_clone_with_a_preregistered_experiment_is_dead(self):
        self.make({README: readme_md()})
        self.commit("one")
        self.put("a.txt", "a\n")
        self.commit("two")
        self.put(RECORDED, "data\n")
        self.commit("three")
        code, lines = check_experiments.run(self.clone())
        self.assertEqual(code, 2, lines)
        self.assertTrue(lines[0].startswith("experiments: DEAD "), lines)

    def test_a_shallow_clone_without_experiments_is_clean(self):
        self.make()
        self.commit("one")
        self.put("a.txt", "a\n")
        self.commit("two")
        self.put("b.txt", "b\n")
        self.commit("three")
        code, lines = check_experiments.run(self.clone())
        self.assertEqual(code, 0, lines)


class ControlTests(ExperimentCase):
    def test_the_control_finds_every_rule(self):
        self.assertEqual(check_experiments.control(), [])

    def test_a_rule_that_finds_nothing_makes_the_check_dead(self):
        self.make({README: readme_md()})
        for rule in ("E1", "E2", "E3", "E6", "E7", "S1", "S2", "S3"):
            with self.subTest(rule=rule):
                with mock.patch.object(check_experiments, "check_" + rule.lower(), return_value=[]):
                    code, lines = check_experiments.run(self.root)
                self.assertEqual(code, 2, lines)
                self.assertEqual(
                    lines, ["experiments: DEAD the positive control produced no finding for " + rule]
                )

    def test_zero_files_is_dead(self):
        code, lines = self.result()
        self.assertEqual(code, 2)
        self.assertIn("experiments: DEAD", lines[0])


class CliTests(unittest.TestCase):
    def call_main(self, *args):
        out = io.StringIO()
        with mock.patch.object(sys, "stdout", out):
            code = check_experiments.main(list(args))
        return code, out.getvalue()

    def test_main_on_a_non_repository_is_dead(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self.call_main("--root", tmp)
        self.assertEqual(code, 2, out)
        self.assertIn("experiments: DEAD", out)

    def test_main_turns_an_exception_into_dead(self):
        with mock.patch.object(check_experiments, "run", side_effect=RuntimeError("bang")):
            code, out = self.call_main("--root", REAL_ROOT)
        self.assertEqual(code, 2)
        self.assertEqual(out.strip(), "experiments: DEAD RuntimeError: bang")

    def test_main_on_the_real_repository_passes(self):
        code, out = self.call_main("--root", REAL_ROOT)
        self.assertEqual(code, 0, out)
        self.assertIn("experiments: ok (", out)


if __name__ == "__main__":
    unittest.main()
