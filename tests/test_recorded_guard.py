import os
import subprocess
import sys
import tempfile
import unittest

from lib import repofiles

REAL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REAL_ROOT, "tools", "recorded_guard.py")

CLEAN = ("Ann Example", "ann@example.invalid")
CLEAN_IDENT = {
    "GIT_AUTHOR_NAME": CLEAN[0],
    "GIT_AUTHOR_EMAIL": CLEAN[1],
    "GIT_COMMITTER_NAME": CLEAN[0],
    "GIT_COMMITTER_EMAIL": CLEAN[1],
}
PREFIX = "recorded-guard: "
TAIL = "a maintainer must re-admit this change (MAINTAINING.md)"
REC = "experiments/EXP-0001/recorded/out.txt"
LEDGER = "docs/admissions.md"
HEADER = "| path | class | basis | provenance |\n| --- | --- | --- | --- |\n"


def git(root, *args, **kwargs):
    env = dict(os.environ)
    env.update(kwargs.get("env") or {})
    cmd = ["git", "-c", "commit.gpgsign=false", "-C", root] + list(args)
    proc = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, env=env
    )
    return proc.stdout.decode("utf-8").strip()


def write(root, path, text):
    full = os.path.join(root, *path.split("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as handle:
        handle.write(text)


def commit(root, message="Work", files=None):
    for path, text in (files or {}).items():
        write(root, path, text)
    git(root, "add", "-A", env=CLEAN_IDENT)
    git(root, "commit", "-q", "--allow-empty", "-m", message, env=CLEAN_IDENT)


def row(path, cls, basis="-"):
    return "| %s | %s | %s | Claude Code (model), 2026-10-22 |\n" % (path, cls, basis)


def ledger(*rows):
    return HEADER + "".join(rows)


def line(path):
    return "%s%s: %s" % (PREFIX, path, TAIL)


def rows_line(k):
    return "%s%s: recorded rows changed: %d; %s" % (PREFIX, LEDGER, k, TAIL)


def clean_line(n):
    return "%sno recorded files or rows changed (%d files in this change)" % (PREFIX, n)


class GuardCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="recorded-guard-test-")
        self.addCleanup(self._tmp.cleanup)
        self.root = os.path.join(self._tmp.name, "repo")
        os.makedirs(self.root)
        git(self.root, "init", "-q", "-b", "main")

    def start(self, files=None):
        """The base commit; its id is kept."""
        commit(self.root, "Base", files or {"a.txt": "one\n"})
        self.base = git(self.root, "rev-parse", "HEAD")

    def guard(self, base=None):
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--base", self.base if base is None else base,
             "--root", self.root],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        out = proc.stdout.decode("utf-8")
        err = proc.stderr.decode("utf-8")
        self.assertNotIn("Traceback", out + err)
        for ln in out.splitlines():
            self.assertTrue(ln.startswith(PREFIX), ln)
        return proc.returncode, out.splitlines()


class ChangedPathTests(GuardCase):
    def test_g1_an_added_recorded_file(self):
        self.start()
        commit(self.root, "Add", {REC: "data\n"})
        self.assertEqual(self.guard(), (1, [line(REC)]))

    def test_g2_a_deleted_recorded_file(self):
        self.start({"a.txt": "one\n", REC: "data\n"})
        git(self.root, "rm", "-q", REC)
        commit(self.root, "Delete")
        self.assertEqual(self.guard(), (1, [line(REC)]))

    def test_g3_a_moved_recorded_file_shows_its_old_path(self):
        self.start({"a.txt": "one\n", REC: "data\n"})
        os.makedirs(os.path.join(self.root, "experiments", "EXP-0001", "code"))
        git(self.root, "mv", REC, "experiments/EXP-0001/code/out.txt")
        commit(self.root, "Move")
        self.assertEqual(self.guard(), (1, [line(REC)]))

    def test_g1_paths_are_listed_in_sorted_order(self):
        self.start()
        b = "experiments/EXP-0001/recorded/b.txt"
        a = "experiments/EXP-0001/recorded/a.txt"
        commit(self.root, "Add", {b: "b\n", a: "a\n"})
        self.assertEqual(self.guard(), (1, [line(a), line(b)]))


class LedgerRowTests(GuardCase):
    def base_ledger(self):
        return ledger(row("a.txt", "original"), row(REC, "recorded", "sha256=" + "0" * 64))

    def test_g4_an_added_recorded_row(self):
        self.start({"a.txt": "one\n", LEDGER: self.base_ledger()})
        extra = row("experiments/EXP-0001/recorded/z.txt", "recorded", "sha256=" + "1" * 64)
        commit(self.root, "Row", {LEDGER: self.base_ledger() + extra})
        self.assertEqual(self.guard(), (1, [rows_line(1)]))

    def test_g4_a_removed_recorded_row(self):
        self.start({"a.txt": "one\n", LEDGER: self.base_ledger()})
        commit(self.root, "Row", {LEDGER: ledger(row("a.txt", "original"))})
        self.assertEqual(self.guard(), (1, [rows_line(1)]))

    def test_g4_one_row_of_each_counts_two(self):
        self.start({"a.txt": "one\n", LEDGER: self.base_ledger()})
        extra = row("experiments/EXP-0001/recorded/z.txt", "recorded", "sha256=" + "1" * 64)
        commit(self.root, "Row", {LEDGER: ledger(row("a.txt", "original"), extra)})
        self.assertEqual(self.guard(), (1, [rows_line(2)]))

    def test_g4_a_class_cell_padded_with_a_tab_is_counted(self):
        self.start({"a.txt": "one\n", LEDGER: self.base_ledger()})
        padded = "| experiments/EXP-0001/recorded/z.txt |\trecorded\t| sha256=" + "1" * 64 + " | x |\n"
        commit(self.root, "Row", {LEDGER: self.base_ledger() + padded})
        self.assertEqual(self.guard(), (1, [rows_line(1)]))

    def test_g4_the_diff_header_lines_are_never_counted(self):
        # The "---" and "+++" header lines of the diff come before the first hunk.
        self.start({"a.txt": "one\n", LEDGER: self.base_ledger()})
        commit(self.root, "Row", {LEDGER: self.base_ledger() + row("b.txt", "original")})
        self.assertEqual(self.guard(), (0, [clean_line(1)]))
        self.assertEqual(
            repofiles.changed_lines(self.root, self.base, LEDGER),
            ["+" + row("b.txt", "original").rstrip("\n")],
        )

    def test_g4_an_added_original_row_gives_nothing(self):
        self.start({"a.txt": "one\n", LEDGER: self.base_ledger()})
        commit(self.root, "Row", {LEDGER: self.base_ledger() + row("b.txt", "original")})
        self.assertEqual(self.guard(), (0, [clean_line(1)]))

    def test_g4_a_recorded_file_and_a_recorded_row_give_two_lines(self):
        self.start({"a.txt": "one\n", LEDGER: self.base_ledger()})
        extra = row("experiments/EXP-0001/recorded/z.txt", "recorded", "sha256=" + "1" * 64)
        commit(self.root, "Both", {
            LEDGER: self.base_ledger() + extra,
            "experiments/EXP-0001/recorded/z.txt": "z\n",
        })
        self.assertEqual(
            self.guard(),
            (1, [line("experiments/EXP-0001/recorded/z.txt"), rows_line(1)]),
        )


class NearMissTests(GuardCase):
    def near(self, path):
        self.start()
        commit(self.root, "Near", {path: "x\n"})
        self.assertEqual(self.guard(), (0, [clean_line(1)]))

    def test_g5_a_recorded_folder_outside_an_experiment(self):
        self.near("tools/recorded/x.py")

    def test_g5_a_recorded_folder_deeper_in_an_experiment(self):
        self.near("experiments/EXP-0001/code/recorded/x.txt")

    def test_g5_a_recorded_folder_without_an_experiment_id(self):
        self.near("experiments/recorded/x.txt")

    def test_g5_an_original_row_whose_path_contains_recorded(self):
        self.start({"a.txt": "one\n", LEDGER: ledger(row("a.txt", "original"))})
        added = row("experiments/EXP-0001/code/recorded/x.txt", "original")
        commit(self.root, "Row", {LEDGER: ledger(row("a.txt", "original")) + added})
        self.assertEqual(self.guard(), (0, [clean_line(1)]))


class OrdinaryChangeTests(GuardCase):
    def test_g6_only_an_ordinary_file_changed(self):
        self.start()
        commit(self.root, "Edit", {"a.txt": "two\n", "b.txt": "new\n"})
        self.assertEqual(self.guard(), (0, [clean_line(2)]))


class BaseTests(GuardCase):
    def test_g7_a_base_that_is_not_a_full_id(self):
        self.start()
        reason = PREFIX + "DEAD the base is not a full commit id"
        for bad in (self.base[:12], self.base.upper(), "main", "HEAD", ""):
            with self.subTest(base=bad):
                self.assertEqual(self.guard(base=bad), (2, [reason]))

    def test_g8_a_well_formed_id_that_is_not_in_the_repository(self):
        self.start()
        reason = PREFIX + "DEAD the base commit is not in this clone's history"
        for missing in ("1" * 40, "1" * 64):
            with self.subTest(base=missing):
                self.assertEqual(self.guard(base=missing), (2, [reason]))

    def test_g8_a_tree_id_is_not_a_commit(self):
        self.start()
        tree = git(self.root, "rev-parse", "HEAD^{tree}")
        self.assertEqual(
            self.guard(base=tree),
            (2, [PREFIX + "DEAD the base commit is not in this clone's history"]),
        )


class MissingBaseTests(GuardCase):
    def test_g7_no_base_at_all_is_dead_without_usage_text(self):
        self.start()
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--root", self.root],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(
            proc.stdout.decode("utf-8"), PREFIX + "DEAD the base is not a full commit id\n"
        )
        self.assertEqual(proc.stderr.decode("utf-8"), "")


class OutputTests(GuardCase):
    def test_g9_a_root_that_is_not_a_repository_is_dead_without_a_traceback(self):
        self.start()
        empty = os.path.join(self._tmp.name, "empty")
        os.makedirs(empty)
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--base", self.base, "--root", empty],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        out = proc.stdout.decode("utf-8")
        self.assertEqual(proc.returncode, 2, out)
        self.assertNotIn("Traceback", out + proc.stderr.decode("utf-8"))
        for ln in out.splitlines():
            self.assertTrue(ln.startswith(PREFIX), ln)


if __name__ == "__main__":
    unittest.main()
