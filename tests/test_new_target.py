import hashlib
import io
import os
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


if __name__ == "__main__":
    unittest.main()
