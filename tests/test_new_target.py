import hashlib
import io
import os
import re
import shutil
import tempfile
import unittest
from unittest import mock

import check_admission
import check_experiments
import check_vocabulary
import new_target
from lib import header, paths
from tests import test_check_admission as tca

REAL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTTPS = "https" + "://"
REAL_URL = HTTPS + "docs.python.org/3/"
PROV = "Claude Code (test), 2026-10-21"
GOOD_COMMIT = "c" * 40
GOOD_SHA = "d" * 64
SEEDED_SOURCE = "SRC-0001"
THIRD_SOURCE = "SRC-" + "0003"
SECOND_SOURCE = "SRC-" + "0002"
FOURTH_SOURCE = "SRC-" + "0004"
SECOND_EXPERIMENT = "EXP-0002"


def real_text(relpath):
    with open(os.path.join(REAL_ROOT, *relpath.split("/")), encoding="utf-8") as handle:
        return handle.read()


def argv(root, **over):
    values = {
        "title": "An outside tool",
        "type": "code-repo",
        "url": REAL_URL,
        "publisher": "A publisher",
        "fetched": "2026-10-21",
        "sha256": GOOD_SHA,
        "commit": GOOD_COMMIT,
        "licence": "MIT",
        "licence-basis": "the LICENSE file at the pinned commit",
        "provenance": PROV,
    }
    values.update(over)
    out = ["--root", root]
    for key, value in values.items():
        if value is not None:
            out.extend(["--" + key, value])
    return out


def tree_hashes(root):
    found = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            full = os.path.join(dirpath, name)
            with open(full, "rb") as handle:
                found[os.path.relpath(full, root)] = hashlib.sha256(handle.read()).hexdigest()
        for name in dirnames:
            found[os.path.relpath(os.path.join(dirpath, name), root) + "/"] = "dir"
    return found


class NewTargetCase(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = holder.name
        tca.git_init(self.root)
        files = {
            "docs/vocabulary.md": real_text("docs/vocabulary.md"),
            "sources/SRC-0001/source.md": real_text("sources/SRC-0001/source.md"),
        }
        rows = tca.auto_rows(files)
        files[tca.LEDGER] = tca.ledger_text(rows)
        for path, data in files.items():
            tca.write(self.root, path, data)

    def run_tool(self, **over):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
            code = new_target.main(argv(self.root, **over))
        return code, out.getvalue(), err.getvalue()

    def read(self, relpath):
        with open(os.path.join(self.root, *relpath.split("/")), encoding="utf-8") as handle:
            return handle.read()

    def assertChecksClean(self):
        for check in (check_admission, check_vocabulary, check_experiments):
            code, lines = check.run(self.root)
            self.assertEqual(code, 0, lines)


class WriteTests(NewTargetCase):
    def test_code_repo_target(self):
        code, out, err = self.run_tool()
        self.assertEqual((code, err), (0, ""))
        self.assertChecksClean()
        self.assertEqual(
            out.split("\n")[:-1],
            [
                "new_target: wrote sources/%s/source.md" % SECOND_SOURCE,
                "new_target: wrote experiments/EXP-0001/README.md",
                "new_target: wrote experiments/EXP-0001/read-log.md",
                "new_target: wrote docs/admissions.md",
            ],
        )
        source = self.read("sources/%s/source.md" % SECOND_SOURCE)
        lines = source.split("\n")
        self.assertEqual(lines[:2], ["# An outside tool", ""])
        self.assertEqual([ln.split(": ")[0] for ln in lines[2:-1]], list(header.CODE_REPO_KEYS))
        self.assertIn("id: " + SECOND_SOURCE, lines)
        self.assertIn("snapshot: no", lines)
        self.assertIn("commit: " + GOOD_COMMIT, lines)
        self.assertTrue(source.endswith("notes: -\n"))
        readme = self.read("experiments/EXP-0001/README.md")
        lines = readme.split("\n")
        self.assertEqual(lines[:2], ["# An outside tool", ""])
        self.assertEqual(
            lines[2:8],
            [
                "id: EXP-0001",
                "subject: " + SECOND_SOURCE,
                "protocol: preregistered",
                "read_log: contemporaneous",
                "spend_cap_usd: -",
                "launched_by: -",
            ],
        )
        headings = [ln for ln in lines if ln.startswith("## ")]
        self.assertEqual(
            headings,
            ["## " + t for t in (
                "Abstract", "Question", "Hypotheses and planned analysis", "Materials",
                "Methods", "Results", "Analysis", "Threats to validity",
                "Deviations from the preregistration", "How to reproduce",
                "How to replicate", "Provenance", "Changelog",
            )],
        )
        for index, line in enumerate(lines):
            if line.startswith("## "):
                self.assertTrue(lines[index + 2].startswith("TODO:"), line)
        self.assertIn(paths.METHOD_DIR + "/", readme)
        self.assertEqual(
            self.read("experiments/EXP-0001/read-log.md"),
            "# Read log\n\n| when | path | sha256 | reason |\n| --- | --- | --- | --- |\n",
        )
        ledger = self.read(tca.LEDGER).split("\n")
        self.assertEqual(
            ledger[-4:],
            [
                "| sources/%s/source.md | original | - | %s |" % (SECOND_SOURCE, PROV),
                "| experiments/EXP-0001/README.md | original | - | %s |" % PROV,
                "| experiments/EXP-0001/read-log.md | original | - | %s |" % PROV,
                "",
            ],
        )

    def test_web_page_target(self):
        code, out, err = self.run_tool(type="web-page", commit=None, licence="-")
        self.assertEqual((code, err), (0, ""))
        self.assertChecksClean()
        lines = self.read("sources/%s/source.md" % SECOND_SOURCE).split("\n")
        self.assertEqual([ln.split(": ")[0] for ln in lines[2:-1]], list(header.SOURCE_KEYS))

    def test_exploratory_protocol(self):
        code, out, err = self.run_tool(protocol="exploratory")
        self.assertEqual((code, err), (0, ""))
        self.assertChecksClean()
        readme = self.read("experiments/EXP-0001/README.md")
        self.assertIn("protocol: exploratory", readme)
        self.assertIn("exploratory", check_experiments.abstract_of(readme))

    def test_numbering_skips_gaps(self):
        tca.write(self.root, "sources/%s/source.md" % THIRD_SOURCE, "x\n")
        code, out, err = self.run_tool(type="web-page", commit=None)
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(os.path.isfile(os.path.join(self.root, "sources", FOURTH_SOURCE, "source.md")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "sources", SECOND_SOURCE)))

    def test_second_run_allocates_the_next_numbers(self):
        self.assertEqual(self.run_tool()[0], 0)
        code, out, err = self.run_tool(url=HTTPS + "pypi.org/", title="A second tool")
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(os.path.isfile(os.path.join(self.root, "sources", THIRD_SOURCE, "source.md")))
        readme = self.read("experiments/%s/README.md" % SECOND_EXPERIMENT)
        self.assertIn("id: " + SECOND_EXPERIMENT, readme)
        self.assertIn("subject: " + THIRD_SOURCE, readme)
        self.assertChecksClean()


class RefusalTests(NewTargetCase):
    def test_each_refusal_exits_1_and_writes_nothing(self):
        cases = {
            "commit missing for code-repo": {"commit": None},
            "commit given for web-page": {"type": "web-page"},
            "short sha256": {"sha256": "d" * 63},
            "http url": {"url": "http" + "://docs.python.org/3/"},
            "reserved url": {"url": HTTPS + "example.com/x"},
            "unknown type": {"type": "gadget"},
            "impossible date": {"fetched": "2026-02-30"},
            "dash licence for code-repo": {"licence": "-"},
            "pipe in title": {"title": "A | B"},
            "provenance without a date": {"provenance": "Claude Code (test)"},
            "unknown protocol": {"protocol": "hopeful"},
            "short commit": {"commit": "c" * 39},
        }
        for name, over in cases.items():
            with self.subTest(name):
                before = tree_hashes(self.root)
                code, out, err = self.run_tool(**over)
                self.assertEqual(code, 1, (out, err))
                self.assertEqual(out, "")
                self.assertTrue(err.startswith("new_target: "), err)
                self.assertEqual(tree_hashes(self.root), before)

    def test_a_missing_argument_is_a_refusal(self):
        before = tree_hashes(self.root)
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            code = new_target.main(["--root", self.root, "--title", "x"])
        self.assertEqual(code, 1)
        self.assertEqual(tree_hashes(self.root), before)

    def test_no_overwrite(self):
        known = b"known bytes\n"
        tca.write(self.root, "experiments/EXP-0001/README.md", known)
        before = tree_hashes(self.root)
        # The folder makes the next number EXP-0002, so force the clash on purpose.
        with mock.patch.object(new_target, "next_number", return_value=1):
            code, out, err = self.run_tool(type="web-page", commit=None)
        self.assertEqual(code, 1, err)
        with open(os.path.join(self.root, "experiments", "EXP-0001", "README.md"), "rb") as handle:
            self.assertEqual(handle.read(), known)
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, "sources"))), [SEEDED_SOURCE])
        self.assertEqual(tree_hashes(self.root), before)


class ValueAndTypeTests(NewTargetCase):
    def assertRefused(self, text, **over):
        before = tree_hashes(self.root)
        code, out, err = self.run_tool(**over)
        self.assertEqual((code, out), (1, ""), err)
        self.assertIn(text, err)
        self.assertEqual(tree_hashes(self.root), before)

    def test_unknown_type_alone_is_refused(self):
        # No --commit, so the commit rule cannot be what refuses it.
        self.assertRefused("is not a Source types value", type="gadget", commit=None)

    def test_a_line_feed_is_refused(self):
        self.assertRefused("pipe or a line break", title="A\nB")

    def test_a_carriage_return_is_refused(self):
        self.assertRefused("pipe or a line break", title="A\rB")

    def test_an_empty_value_is_refused(self):
        self.assertRefused("--title is empty", title="")

    def test_a_padded_value_is_refused(self):
        self.assertRefused("leading or trailing spaces", title=" A ")

    def test_a_release_artifact_needs_a_licence(self):
        self.assertRefused("must name the licence", type="release-artifact", commit=None, licence="-")

    def test_a_file_named_like_an_id_does_not_count(self):
        tca.write(self.root, "sources/" + "SRC-" + "0009", "a regular file\n")
        code, out, err = self.run_tool(type="web-page", commit=None)
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(os.path.isfile(os.path.join(self.root, "sources", SECOND_SOURCE, "source.md")))


class LedgerShapeTests(NewTargetCase):
    def refuse_ledger(self, tail):
        text = self.read(tca.LEDGER) + tail
        tca.write(self.root, tca.LEDGER, text)
        before = tree_hashes(self.root)
        code, out, err = self.run_tool()
        self.assertEqual((code, out), (1, ""), err)
        self.assertIn("does not end with its one table", err)
        self.assertEqual(tree_hashes(self.root), before)

    def test_a_malformed_last_row_is_refused(self):
        self.refuse_ledger("| only | three | cells |\n")

    def test_prose_after_the_table_is_refused(self):
        self.refuse_ledger("\nThe table above is not the end of this file.\n")


class ParentFolderTests(NewTargetCase):
    def assertRefusedUnchanged(self, name, why):
        before = tree_hashes(self.root)
        code, out, err = self.run_tool()
        self.assertEqual((code, out), (1, ""), err)
        self.assertIn(name, err)
        self.assertIn(why, err)
        self.assertEqual(tree_hashes(self.root), before)
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, "sources"))), [SEEDED_SOURCE])

    def test_a_file_where_the_experiment_folder_must_go(self):
        tca.write(self.root, "experiments/EXP-0001", "a regular file\n")
        # The file is not a folder, so the tool picks EXP-0001 and must refuse.
        self.assertRefusedUnchanged("experiments/EXP-0001", "is a file where a folder must be")

    def test_experiments_itself_a_file(self):
        tca.write(self.root, "experiments", "a regular file\n")
        self.assertRefusedUnchanged("experiments", "is a file where a folder must be")

    def test_sources_a_symlink_to_outside_the_root(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        shutil.rmtree(os.path.join(self.root, "sources"))
        try:
            os.symlink(outside.name, os.path.join(self.root, "sources"))
        except (OSError, NotImplementedError, AttributeError) as exc:
            self.skipTest("this platform cannot make symlinks: %s" % exc)
        before = tree_hashes(self.root)
        code, out, err = self.run_tool()
        self.assertEqual((code, out), (1, ""), err)
        self.assertIn("sources is a symlink", err)
        self.assertEqual(tree_hashes(self.root), before)
        self.assertEqual(os.listdir(outside.name), [])

    def test_experiments_a_symlink_to_a_folder_inside_the_root(self):
        os.makedirs(os.path.join(self.root, "elsewhere"))
        try:
            os.symlink(os.path.join(self.root, "elsewhere"), os.path.join(self.root, "experiments"))
        except (OSError, NotImplementedError, AttributeError) as exc:
            self.skipTest("this platform cannot make symlinks: %s" % exc)
        before = tree_hashes(self.root)
        code, out, err = self.run_tool()
        self.assertEqual((code, out), (1, ""), err)
        self.assertIn("experiments is a symlink", err)
        self.assertEqual(tree_hashes(self.root), before)
        self.assertEqual(os.listdir(os.path.join(self.root, "elsewhere")), [])

    def _ledger_attempt(self, outside_names):
        before_outside = {}
        for n in outside_names:
            with open(os.path.join(self.outside.name, n), "rb") as handle:
                before_outside[n] = handle.read()
        before_root = tree_hashes(self.root)
        code, out, err = self.run_tool()
        self.assertEqual((code, out), (1, ""), err)
        for name, data in before_outside.items():
            with open(os.path.join(self.outside.name, name), "rb") as handle:
                self.assertEqual(handle.read(), data)
        self.assertEqual(sorted(os.listdir(self.outside.name)), sorted(outside_names))
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, "sources"))), [SEEDED_SOURCE])
        self.assertFalse(os.path.exists(os.path.join(self.root, "experiments", "EXP-0001")))
        self.assertEqual(tree_hashes(self.root), before_root)
        return err

    def test_docs_a_symlink_to_a_folder_outside_the_root(self):
        self.outside = tempfile.TemporaryDirectory()
        self.addCleanup(self.outside.cleanup)
        for name in ("vocabulary.md", "admissions.md"):
            shutil.copy(os.path.join(self.root, "docs", name), os.path.join(self.outside.name, name))
        shutil.rmtree(os.path.join(self.root, "docs"))
        try:
            os.symlink(self.outside.name, os.path.join(self.root, "docs"))
        except (OSError, NotImplementedError, AttributeError) as exc:
            self.skipTest("this platform cannot make symlinks: %s" % exc)
        err = self._ledger_attempt(["vocabulary.md", "admissions.md"])
        self.assertIn("docs is a symlink", err)

    def test_the_ledger_itself_a_symlink_to_a_file_outside_the_root(self):
        self.outside = tempfile.TemporaryDirectory()
        self.addCleanup(self.outside.cleanup)
        ledger = os.path.join(self.root, "docs", "admissions.md")
        shutil.copy(ledger, os.path.join(self.outside.name, "admissions.md"))
        os.unlink(ledger)
        try:
            os.symlink(os.path.join(self.outside.name, "admissions.md"), ledger)
        except (OSError, NotImplementedError, AttributeError) as exc:
            self.skipTest("this platform cannot make symlinks: %s" % exc)
        err = self._ledger_attempt(["admissions.md"])
        self.assertIn("docs/admissions.md is a symlink", err)
        self.assertTrue(os.path.islink(ledger))

    def test_a_parent_that_resolves_outside_the_root_is_refused(self):
        with self.assertRaises(new_target.Refusal) as caught:
            new_target.check_parent(self.root, "../outside/file.md")
        self.assertIn("outside --root", str(caught.exception))
        new_target.check_parent(self.root, "sources/%s/source.md" % SECOND_SOURCE)


class DeadTests(NewTargetCase):
    def test_an_unexpected_exception_is_dead(self):
        before = tree_hashes(self.root)
        with mock.patch.object(new_target, "next_number", side_effect=RuntimeError("boom")):
            code, out, err = self.run_tool()
        self.assertEqual(code, 2)
        self.assertIn("new_target: DEAD RuntimeError: boom", out + err)
        self.assertEqual(tree_hashes(self.root), before)


UNDER_TEST_PREFIX = "**Under test:** "


def under_test_for(title, pin, source_id):
    return "%s%s, %s ([%s](../../sources/%s/source.md))." % (UNDER_TEST_PREFIX, title, pin, source_id, source_id)


def section_titles(text):
    return [ln[3:] for ln in text.split("\n") if ln.startswith("## ")]


def template_problems(text):
    """What differs between a template text and the tool's layout; empty when they agree."""
    problems = []
    if section_titles(text) != [title for title, _placeholder in new_target.SECTIONS]:
        problems.append("the section headings differ from new_target.SECTIONS")
    _title, pairs, header_problems = header.parse(text)
    if header_problems or tuple(key for key, _value in pairs) != header.EXPERIMENT_KEYS:
        problems.append("the header keys differ from header.EXPERIMENT_KEYS")
    lines = text.split("\n")
    if "## Abstract" in lines and "## Question" in lines:
        start, end = lines.index("## Abstract"), lines.index("## Question")
        between = lines[start + 1:end]
        guidance = [ln for ln in between if ln.strip()]
        ok = (
            len(guidance) == 2
            and not guidance[0].startswith(UNDER_TEST_PREFIX)
            and guidance[1].startswith(UNDER_TEST_PREFIX)
            and between.index(guidance[1]) > between.index(guidance[0]) + 1
            and between[between.index(guidance[1]) - 1] == ""
        )
        if not ok:
            problems.append("the Under test line is not its own paragraph between the abstract and Question")
    else:
        problems.append("the template has no Abstract or Question heading")
    return problems


class UnderTestLineTests(NewTargetCase):
    def written(self, **over):
        code, _out, err = self.run_tool(**over)
        self.assertEqual((code, err), (0, ""))
        return self.read("experiments/EXP-0001/README.md")

    def assertLineInPlace(self, readme, expected):
        lines = readme.split("\n")
        self.assertEqual(lines.count(expected), 1)
        at = lines.index(expected)
        self.assertGreater(at, lines.index("## Abstract"))
        self.assertLess(at, lines.index("## Question"))
        self.assertEqual(lines[at - 1], "")
        self.assertTrue(lines[at - 2].startswith("TODO:"))
        self.assertEqual(lines[at + 1], "")
        self.assertEqual(lines[at + 2], "## Question")
        self.assertEqual(check_experiments.abstract_of(readme), lines[at - 2])

    def test_code_repo_line(self):
        readme = self.written()
        expected = under_test_for("An outside tool", "pinned at commit " + GOOD_COMMIT, SECOND_SOURCE)
        self.assertLineInPlace(readme, expected)
        link = "experiments/EXP-0001/../../sources/%s/source.md" % SECOND_SOURCE
        self.assertTrue(os.path.isfile(os.path.join(self.root, *link.split("/"))))

    def test_other_source_line(self):
        readme = self.written(type="web-page", commit=None, licence="-")
        expected = under_test_for("An outside tool", "pinned by SHA-256 " + GOOD_SHA, SECOND_SOURCE)
        self.assertLineInPlace(readme, expected)
        self.assertNotIn("pinned at commit", readme)
        link = "experiments/EXP-0001/../../sources/%s/source.md" % SECOND_SOURCE
        self.assertTrue(os.path.isfile(os.path.join(self.root, *link.split("/"))))

    def test_the_line_with_its_link_dropped_is_not_the_line(self):
        readme = self.written()
        self.assertIn("](../../sources/%s/source.md))." % SECOND_SOURCE, readme)

    def test_the_blank_line_decides_what_the_abstract_is(self):
        readme = self.written()
        lines = readme.split("\n")
        at = next(i for i, ln in enumerate(lines) if ln.startswith(UNDER_TEST_PREFIX))
        placeholder, line = lines[at - 2], lines[at]
        self.assertEqual(check_experiments.abstract_of(readme), placeholder)
        merged = "\n".join(lines[:at - 1] + lines[at:])
        self.assertEqual(check_experiments.abstract_of(merged), placeholder + "\n" + line)

    def test_exploratory_still_passes_and_the_check_reads_the_real_abstract(self):
        readme = self.written(protocol="exploratory", title="An exploratory outside tool")
        self.assertIn(UNDER_TEST_PREFIX, readme)
        self.assertIn("exploratory", check_experiments.abstract_of(readme))
        self.assertChecksClean()
        # The line says the word, the abstract does not: E6c must still fail.
        path = os.path.join(self.root, "experiments", "EXP-0001", "README.md")
        plain = readme.replace(new_target.EXPLORATORY_ABSTRACT, "TODO: write one paragraph.")
        self.assertNotEqual(plain, readme)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(plain)
        self.assertNotIn("exploratory", check_experiments.abstract_of(plain))
        code, lines = check_experiments.run(self.root)
        self.assertEqual(code, 1, lines)
        self.assertTrue(any("E6" in ln and "exploratory" in ln for ln in lines), lines)


class TemplateDriftTests(unittest.TestCase):
    def setUp(self):
        self.text = real_text("docs/experiment-template.md")

    def test_the_template_matches_the_tool(self):
        self.assertEqual(template_problems(self.text), [])

    def test_the_headings_equal_the_sections(self):
        self.assertEqual(section_titles(self.text), [title for title, _p in new_target.SECTIONS])

    def test_the_header_keys_equal_the_experiment_keys(self):
        title, pairs, problems = header.parse(self.text)
        self.assertEqual(problems, [])
        self.assertEqual(tuple(key for key, _v in pairs), header.EXPERIMENT_KEYS)

    def test_the_abstract_is_the_guidance_not_the_under_test_line(self):
        abstract = check_experiments.abstract_of(self.text)
        self.assertTrue(abstract)
        self.assertNotIn(UNDER_TEST_PREFIX, abstract)

    def test_swapping_two_sections_is_caught(self):
        lines = self.text.split("\n")
        one, two = lines.index("## Methods"), lines.index("## Results")
        lines[one], lines[two] = lines[two], lines[one]
        self.assertTrue(template_problems("\n".join(lines)))

    def test_dropping_a_header_key_is_caught(self):
        text = self.text.replace("read_log: ", "read_log_x: ", 1)
        self.assertNotEqual(text, self.text)
        self.assertTrue(template_problems(text))
        lines = [ln for ln in self.text.split("\n") if not ln.startswith("launched_by: ")]
        self.assertTrue(template_problems("\n".join(lines)))

    def test_moving_the_under_test_line_is_caught(self):
        lines = self.text.split("\n")
        at = next(i for i, ln in enumerate(lines) if ln.startswith(UNDER_TEST_PREFIX))
        line = lines.pop(at)
        lines.insert(lines.index("## Abstract"), line)
        self.assertTrue(template_problems("\n".join(lines)))
        merged = self.text.replace("\n\n" + UNDER_TEST_PREFIX, "\n" + UNDER_TEST_PREFIX)
        self.assertNotEqual(merged, self.text)
        self.assertTrue(template_problems(merged))

    def test_the_under_test_line_is_exactly_this_with_its_link(self):
        expected = (
            "**Under test:** <the target, its pinned version, and a link to its source record, "
            "such as [SRC-NNNN](../../sources/SRC-NNNN/source.md); "
            "or \"this repository's own method; no outside tool\">"
        )
        self.assertEqual(self.text.split("\n").count(expected), 1)

    def test_the_template_names_the_reproduce_command(self):
        self.assertIn("`python3 tools/reproduce.py EXP-NNNN`", self.text)

    def test_the_protocol_definitions_equal_the_vocabulary(self):
        vocabulary = real_text("docs/vocabulary.md").split("\n")
        start = vocabulary.index("## Experiment protocols")
        meanings = {}
        for line in vocabulary[start + 1:]:
            if line.startswith("## "):
                break
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) == 2 and cells[0].startswith("`"):
                meanings[cells[0].strip("`")] = cells[1]
        self.assertEqual(set(meanings), {"preregistered", "exploratory"})
        in_template = dict(re.findall(r"`(\w+)` means ([^.]*)\.", self.text))
        for value, meaning in meanings.items():
            first, *rest = meaning.rstrip(".").split(". ")
            body = first[0].lower() + first[1:]
            for sentence in rest:
                body += ", and " + sentence.replace("It was ", "", 1)
            self.assertEqual(in_template.get(value), body, value)

    def test_the_template_names_the_method_folder(self):
        self.assertIn("`experiments/EXP-NNNN/%s/`" % paths.METHOD_DIR, self.text)


if __name__ == "__main__":
    unittest.main()
