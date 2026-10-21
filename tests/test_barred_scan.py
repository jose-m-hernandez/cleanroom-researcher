import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import barred_scan
from lib import repofiles

REAL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REAL_ROOT, "tools", "barred_scan.py")

# Every synthetic entry is assembled here from parts, so this file holds none
# of them as one literal and the self-scan of this repository stays clean.
CANARY = "barred-strings-" + "canary-" + "t3st0001"
TERM = "zq-" + "canary-7f3"
WORD = "qz" + "xw"
SPACED = "zq-" + "two words"
LIST = CANARY + "\n" + TERM + "\n" + WORD + "\tword\n"
CLEAN = ("Ann Example", "ann@example.invalid")
NOTE = "some note"


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


def commit(root, message="Work", files=None, env=None):
    """One commit as a clean identity; env entries apply to this call only."""
    for path, text in (files or {}).items():
        write(root, path, text)
    ident = {
        "GIT_AUTHOR_NAME": CLEAN[0],
        "GIT_AUTHOR_EMAIL": CLEAN[1],
        "GIT_COMMITTER_NAME": CLEAN[0],
        "GIT_COMMITTER_EMAIL": CLEAN[1],
    }
    ident.update(env or {})
    git(root, "add", "-A", env=ident)
    git(root, "commit", "-q", "--allow-empty", "-m", message, env=ident)


def make_repo(root, files=None, message="Start"):
    os.makedirs(root, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    commit(root, message, files or {"a.txt": "nothing here\n"})


def raw_commit(root, header_extra):
    """Write a commit object by hand and point the current branch at it."""
    tree = subprocess.run(
        ["git", "-C", root, "mktree"], input=b"", stdout=subprocess.PIPE, check=True
    ).stdout.decode("ascii").strip()
    who = b"Ann Example <ann@example.invalid> 1700000000 +0000"
    raw = b"tree " + tree.encode("ascii") + b"\nauthor " + who + b"\ncommitter " + who
    raw += b"\n" + header_extra + b"\n\nWork\n"
    cid = subprocess.run(
        ["git", "-C", root, "hash-object", "-t", "commit", "--literally", "-w", "--stdin"],
        input=raw, stdout=subprocess.PIPE, check=True,
    ).stdout.decode("ascii").strip()
    git(root, "update-ref", "HEAD", cid)
    return cid


def fullwidth(text):
    return "".join(chr(ord(c) + 0xFEE0) if "!" <= c <= "~" else c for c in text)


def list_terms(list_text):
    """What a run's output must never hold: each entry, raw and folded."""
    terms = set()
    for line in list_text.replace("\ufeff", "").split("\n"):
        entry = line.strip().split("\t")[0]
        if not entry or entry.startswith("#"):
            continue
        for form in (entry.lower(), barred_scan.fold(entry)):
            if form:
                terms.add(form)
    return terms


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "repo")
        self.summary = os.path.join(self.tmp.name, "summary.txt")
        with open(self.summary, "w", encoding="utf-8"):
            pass

    def assert_quiet(self, text, list_text):
        for line in text.split("\n"):
            if not line:
                continue
            self.assertTrue(line.startswith("barred: "), line)
            self.assertFalse(line.startswith("::"), line)
        shown = text.lower()
        for term in list_terms(list_text):
            self.assertNotIn(term, shown)
        self.assertEqual(os.path.getsize(self.summary), 0)

    def scan(self, list_text=LIST, via="path", root=None, env=None):
        """Run the script as the CI job does; return (exit code, stdout lines)."""
        child = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("BARRED_") and k != "GITHUB_STEP_SUMMARY"
        }
        child["GITHUB_STEP_SUMMARY"] = self.summary
        if via == "path":
            path = os.path.join(self.tmp.name, "list.txt")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(list_text)
            child["BARRED_STRINGS_PATH"] = path
        else:
            child["BARRED_STRINGS"] = list_text
        child.update(env or {})
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--root", root or self.root],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=child,
            timeout=120,
        )
        out = proc.stdout.decode("utf-8")
        err = proc.stderr.decode("utf-8")
        self.assert_quiet(out + err, list_text)
        return proc.returncode, [l for l in out.split("\n") if l]

    def scan_both(self, list_text=LIST):
        for via in ("path", "env"):
            with self.subTest(via=via):
                yield self.scan(list_text, via)

    def inprocess(self, list_text=LIST):
        """Run main() here; return (exit code, stdout lines)."""
        path = os.path.join(self.tmp.name, "list.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(list_text)
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"BARRED_STRINGS_PATH": path}):
            os.environ.pop("BARRED_STRINGS", None)
            with mock.patch.object(sys, "stdout", buf):
                code = barred_scan.main(["--root", self.root])
        text = buf.getvalue()
        self.assert_quiet(text, list_text)
        return code, [l for l in text.split("\n") if l]

    def content_run(self, text, list_text=LIST):
        """Scan a one-file repository holding text."""
        make_repo(self.root, {"a.txt": text})
        return self.scan(list_text)


class FormatTests(Base):
    def test_a_clean_tree_is_ok_by_both_routes(self):
        make_repo(self.root)
        for code, lines in self.scan_both():
            self.assertEqual(code, 0, lines)
            self.assertRegex(
                lines[-1],
                r"^barred: ok \(3 entries, \d+ blobs, 1 commits, canary matched\)$",
            )

    def test_an_environment_list_wins_over_a_file(self):
        make_repo(self.root)
        code, lines = self.scan(LIST, "env", env={"BARRED_STRINGS_PATH": os.devnull})
        self.assertEqual(code, 0, lines)

    def test_an_empty_environment_list_falls_back_to_the_file(self):
        make_repo(self.root)
        code, lines = self.scan(LIST, "path", env={"BARRED_STRINGS": ""})
        self.assertEqual(code, 0, lines)

    def test_no_list_is_dead(self):
        make_repo(self.root)
        for env in ({}, {"BARRED_STRINGS": ""}):
            with self.subTest(env=env):
                child = {
                    k: v for k, v in os.environ.items() if not k.startswith("BARRED_")
                }
                child.update(env)
                proc = subprocess.run(
                    [sys.executable, SCRIPT, "--root", self.root],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=child,
                    timeout=120,
                )
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(
                    proc.stdout.decode("utf-8").strip(),
                    "barred: DEAD no list (set BARRED_STRINGS or BARRED_STRINGS_PATH)",
                )

    def test_a_list_of_only_comments_is_no_list(self):
        make_repo(self.root)
        for code, lines in self.scan_both("# nothing\n\n   \n"):
            self.assertEqual(code, 2, lines)
            self.assertEqual(
                lines,
                ["barred: DEAD no list (set BARRED_STRINGS or BARRED_STRINGS_PATH)"],
            )

    def test_a_bom_before_the_canary_still_loads(self):
        make_repo(self.root)
        for code, lines in self.scan_both("\ufeff" + LIST):
            self.assertEqual(code, 0, lines)
            self.assertIn("3 entries", lines[-1])

    def test_comments_and_blank_lines_are_skipped(self):
        make_repo(self.root)
        text = "# a comment\n\n" + CANARY + "\n   \n  # indented\n" + TERM + "\n" + WORD + "\tword\n"
        for code, lines in self.scan_both(text):
            self.assertEqual(code, 0, lines)
            self.assertIn("3 entries", lines[-1])

    def test_numbering_counts_entries_not_lines(self):
        text = "# top\n\n" + CANARY + "\n\n# mid\n" + TERM + "\n"
        code, lines = self.content_run("x " + TERM + " y\n", text)
        self.assertEqual(code, 1, lines)
        self.assertIn("barred: a.txt:1: entry #2", lines)

    def test_a_note_after_a_tab_is_ignored(self):
        text = CANARY + "\t" + NOTE + "\n" + TERM + "\t" + NOTE + "\tword\n"
        code, lines = self.content_run("a" + TERM + "b\n", text)
        self.assertEqual(code, 1, lines)
        self.assertIn("barred: a.txt:1: entry #2", lines)

    def test_the_word_flag_in_any_case(self):
        text = CANARY + "\n" + WORD + "\tWoRd\n"
        code, lines = self.content_run("a" + WORD + "b\n", text)
        self.assertEqual(code, 0, lines)

    def test_an_entry_empty_after_fold_is_dead(self):
        make_repo(self.root)
        for blank in ("\u200b", "\u200d\u2060"):
            for code, lines in self.scan_both(CANARY + "\n" + blank + "\n" + TERM + "\n"):
                self.assertEqual(code, 2, lines)
                self.assertEqual(lines, ["barred: DEAD entry #2 is empty after fold"])

    def test_a_missing_canary_is_dead(self):
        make_repo(self.root)
        for code, lines in self.scan_both(TERM + "\n" + WORD + "\tword\n"):
            self.assertEqual(code, 2, lines)
            self.assertEqual(lines, ["barred: DEAD entry 1 is not a canary"])

    def test_a_canary_with_seven_suffix_characters_is_dead(self):
        make_repo(self.root)
        short = "barred-strings-" + "canary-" + "1234567"
        for code, lines in self.scan_both(short + "\n" + TERM + "\n"):
            self.assertEqual(code, 2, lines)
            self.assertEqual(lines, ["barred: DEAD entry 1 is not a canary"])

    def test_a_canary_with_eight_suffix_characters_loads(self):
        make_repo(self.root)
        eight = "barred-strings-" + "canary-" + "12345678"
        for code, lines in self.scan_both(eight + "\n" + TERM + "\n"):
            self.assertEqual(code, 0, lines)

    def test_a_canary_with_stray_characters_is_dead(self):
        make_repo(self.root)
        for bad in ("-x", "_x", " x"):
            text = "barred-strings-" + "canary-" + "t3st0001" + bad + "\n"
            with self.subTest(bad=bad):
                code, lines = self.scan(text)
                self.assertEqual(code, 2, lines)

    def test_a_canary_is_read_through_the_fold(self):
        make_repo(self.root)
        code, lines = self.scan(fullwidth(CANARY).upper() + "\n" + TERM + "\n")
        self.assertEqual(code, 0, lines)

    def test_a_patched_matcher_that_misses_the_canary_is_dead(self):
        make_repo(self.root)
        with mock.patch.object(barred_scan, "scan_text", lambda _text, _entries: []):
            code, lines = self.inprocess()
        self.assertEqual(code, 2)
        self.assertEqual(lines, ["barred: DEAD the canary did not match"])

    def test_a_list_that_is_not_utf8_is_dead(self):
        make_repo(self.root)
        path = os.path.join(self.tmp.name, "list.txt")
        with open(path, "wb") as handle:
            handle.write(CANARY.encode("ascii") + b"\n\xff\xfe\n")
        child = {k: v for k, v in os.environ.items() if not k.startswith("BARRED_")}
        child["BARRED_STRINGS_PATH"] = path
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--root", self.root],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=child,
            timeout=120,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(
            proc.stdout.decode("utf-8").strip(), "barred: DEAD the list is not valid UTF-8"
        )


class TrimTests(Base):
    def test_d1_a_space_before_the_tab_is_not_part_of_a_whole_word_entry(self):
        text = CANARY + "\n" + WORD + " \tword\n"
        code, lines = self.content_run("a_" + WORD + "_b\n", text)
        self.assertEqual(code, 1, lines)
        self.assertIn("barred: a.txt:1: entry #2", lines)

    def test_d2_spaces_before_the_tab_are_not_part_of_a_substring_entry(self):
        text = CANARY + "\n" + TERM + "  \t" + NOTE + "\n"
        code, lines = self.content_run("a" + TERM + ".\n", text)
        self.assertEqual(code, 1, lines)
        self.assertIn("barred: a.txt:1: entry #2", lines)


class MatchingTests(Base):
    def assert_hit(self, text, number=2, list_text=LIST, line=1):
        code, lines = self.content_run(text, list_text)
        self.assertEqual(code, 1, lines)
        self.assertIn("barred: a.txt:%d: entry #%d" % (line, number), lines)

    def assert_miss(self, text, list_text=LIST):
        code, lines = self.content_run(text, list_text)
        self.assertEqual(code, 0, lines)

    def test_substring_in_mixed_case(self):
        self.assert_hit("x " + "Zq-" + "CaNaRy-7F3" + " y\n")

    def test_substring_inside_a_longer_word(self):
        self.assert_hit("pre" + TERM + "post\n")

    def test_substring_in_nfkc_equivalent_full_width_characters(self):
        self.assert_hit(fullwidth(TERM) + "\n")

    def test_substring_with_a_zero_width_joiner_inside(self):
        self.assert_hit(TERM[:5] + "\u200d" + TERM[5:] + "\n")

    def test_substring_with_other_format_characters_inside(self):
        self.assert_hit(TERM[:3] + "\u200b\u2060\ufeff" + TERM[3:] + "\n")

    def test_substring_across_a_run_of_whitespace(self):
        text = CANARY + "\n" + SPACED + "\n"
        self.assert_hit("zq-two" + " \n\t  " + "words\n", 2, text)

    def test_a_hit_across_lines_is_reported_on_the_line_it_starts(self):
        text = CANARY + "\n" + SPACED + "\n"
        self.assert_hit("one\nzq-two\n\nwords\nlast\n", 2, text, line=2)

    def test_a_hit_on_a_later_line(self):
        self.assert_hit("one\ntwo\nthree " + TERM + "\n", line=3)

    def test_a_hit_after_a_run_of_format_characters_keeps_its_line(self):
        self.assert_hit("\u200b\u200b\n\u200d\nx\n" + TERM + "\n", line=4)

    def test_the_whole_word_entry_hits_alone(self):
        self.assert_hit(WORD + "\n", 3)

    def test_the_whole_word_entry_hits_in_other_case(self):
        self.assert_hit(WORD.upper() + "\n", 3)

    def test_the_whole_word_entry_hits_between_punctuation(self):
        self.assert_hit("(" + WORD + "), " + WORD + ".\n", 3)

    def test_the_whole_word_entry_hits_between_underscores(self):
        self.assert_hit("a_" + WORD + "_b\n", 3)

    def test_the_whole_word_entry_hits_after_a_missed_occurrence(self):
        self.assert_hit("a" + WORD + "b then " + WORD + "\n", 3)

    def test_the_whole_word_entry_does_not_hit_inside_a_word(self):
        self.assert_miss("a" + WORD + "b\n")

    def test_the_whole_word_entry_does_not_hit_before_a_digit(self):
        self.assert_miss(WORD + "2\n")

    def test_the_whole_word_entry_does_not_hit_after_a_digit(self):
        self.assert_miss("2" + WORD + "\n")

    def test_the_whole_word_entry_does_not_hit_before_a_letter(self):
        self.assert_miss(WORD + "é\n")

    def test_the_substring_twin_hits_inside_a_word(self):
        twin = CANARY + "\n" + WORD + "\n"
        self.assert_hit("a" + WORD + "b\n", 2, twin)

    def test_a_clean_text_is_a_miss(self):
        self.assert_miss("nothing to see\nzq canary 7f3\n")


class ScopeTests(Base):
    def assert_hit_line(self, lines, expected):
        self.assertIn(expected, lines)
        self.assertEqual(lines[-1], "barred: %d finding(s)" % (len(lines) - 1))

    def test_content(self):
        make_repo(self.root, {"c.txt": "ok\n" + TERM + "\n"})
        code, lines = self.scan()
        self.assertEqual(code, 1, lines)
        self.assert_hit_line(lines, "barred: c.txt:2: entry #2")

    def test_content_by_the_environment_route(self):
        make_repo(self.root, {"c.txt": TERM + "\n"})
        code, lines = self.scan(LIST, "env")
        self.assertEqual(code, 1, lines)
        self.assertIn("barred: c.txt:1: entry #2", lines)

    def test_an_uncommitted_file_counts(self):
        make_repo(self.root)
        write(self.root, "new.txt", TERM + "\n")
        code, lines = self.scan()
        self.assertEqual(code, 1, lines)
        self.assertIn("barred: new.txt:1: entry #2", lines)

    def test_an_edited_file_counts_against_its_committed_blob(self):
        make_repo(self.root, {"e.txt": "clean\n"})
        write(self.root, "e.txt", "clean\n" + TERM + "\n")
        code, lines = self.scan()
        self.assertEqual(code, 1, lines)
        self.assertIn("barred: e.txt:2: entry #2", lines)

    def test_path(self):
        make_repo(self.root, {TERM + ".txt": "clean\n"})
        code, lines = self.scan()
        self.assertEqual(code, 1, lines)
        self.assertIn("barred: [hit]: entry #2 (path)", lines)

    def test_a_path_in_a_folder(self):
        make_repo(self.root, {"docs/" + WORD + "/x.txt": "clean\n"})
        code, lines = self.scan()
        self.assertEqual(code, 1, lines)
        self.assertIn("barred: docs/[hit]/x.txt: entry #3 (path)", lines)

    def test_a_content_hit_in_a_hitting_path_does_not_print_the_path(self):
        make_repo(self.root, {TERM + ".txt": TERM + "\n"})
        code, lines = self.scan()
        self.assertEqual(code, 1, lines)
        self.assertIn("barred: [hit]:1: entry #2", lines)

    def test_a_file_added_then_deleted_in_a_later_commit(self):
        make_repo(self.root)
        commit(self.root, "Add", {"old.txt": TERM + "\n"})
        git(self.root, "rm", "-q", "old.txt")
        commit(self.root, "Remove")
        self.assertFalse(os.path.exists(os.path.join(self.root, "old.txt")))
        code, lines = self.scan()
        self.assertEqual(code, 1, lines)
        self.assertIn("barred: old.txt:1: entry #2", lines)

    def test_a_deleted_path_is_still_a_path_hit(self):
        make_repo(self.root)
        commit(self.root, "Add", {TERM + ".txt": "clean\n"})
        git(self.root, "rm", "-q", TERM + ".txt")
        commit(self.root, "Remove")
        code, lines = self.scan()
        self.assertEqual(code, 1, lines)
        self.assertIn("barred: [hit]: entry #2 (path)", lines)

    def test_a_blob_is_reported_at_the_path_of_its_first_commit(self):
        make_repo(self.root, {"first.txt": TERM + "\n"})
        git(self.root, "mv", "first.txt", "second.txt")
        commit(self.root, "Rename")
        code, lines = self.scan()
        self.assertEqual(code, 1, lines)
        self.assertIn("barred: first.txt:1: entry #2", lines)
        self.assertFalse([l for l in lines if "second.txt:" in l], lines)

    def test_a_blob_held_twice_is_scanned_once(self):
        make_repo(self.root, {"one.txt": TERM + "\n", "two.txt": TERM + "\n"})
        code, lines = self.scan()
        self.assertEqual(code, 1, lines)
        self.assertEqual(len([l for l in lines if ".txt:1: entry #2" in l]), 1, lines)

    def test_a_commit_message(self):
        make_repo(self.root)
        commit(self.root, "Fix " + TERM + " here")
        sha = git(self.root, "rev-parse", "--short", "HEAD")
        code, lines = self.scan()
        self.assertEqual(code, 1, lines)
        self.assertIn("barred: commit %s: entry #2 (message)" % sha, lines)

    def test_a_message_body_line(self):
        make_repo(self.root)
        commit(self.root, "Subject\n\nBody line\n" + TERM + " in the body")
        sha = git(self.root, "rev-parse", "--short", "HEAD")
        code, lines = self.scan()
        self.assertIn("barred: commit %s: entry #2 (message)" % sha, lines)

    def test_a_signed_off_by_trailer(self):
        make_repo(self.root)
        message = "Subject\n\nSigned-off-by: Zed <" + TERM + "@example.invalid>"
        commit(self.root, message)
        sha = git(self.root, "rev-parse", "--short", "HEAD")
        code, lines = self.scan()
        self.assertEqual(code, 1, lines)
        self.assertIn("barred: commit %s: entry #2 (message)" % sha, lines)

    def identity_run(self, env, field):
        make_repo(self.root)
        commit(self.root, "Work", env=env)
        sha = git(self.root, "rev-parse", "--short", "HEAD")
        code, lines = self.scan()
        self.assertEqual(code, 1, lines)
        self.assertIn("barred: commit %s: entry #2 (%s)" % (sha, field), lines)
        fields = [l for l in lines if l.startswith("barred: commit %s:" % sha)]
        self.assertEqual(len(fields), 1, lines)

    def test_author_name(self):
        self.identity_run({"GIT_AUTHOR_NAME": TERM}, "author")

    def test_author_email(self):
        self.identity_run({"GIT_AUTHOR_EMAIL": TERM + "@example.invalid"}, "author email")

    def test_committer_name(self):
        self.identity_run({"GIT_COMMITTER_NAME": TERM}, "committer")

    def test_committer_email(self):
        self.identity_run(
            {"GIT_COMMITTER_EMAIL": TERM + "@example.invalid"}, "committer email"
        )

    def test_the_branch_name(self):
        make_repo(self.root)
        git(self.root, "branch", "-m", TERM)
        code, lines = self.scan()
        self.assertEqual(code, 1, lines)
        self.assertEqual(lines, ["barred: branch: entry #2", "barred: 1 finding(s)"])

    def test_a_side_branch_commit_is_scanned(self):
        make_repo(self.root)
        git(self.root, "checkout", "-q", "-b", "side")
        commit(self.root, "Side " + TERM)
        git(self.root, "checkout", "-q", "main")
        commit(self.root, "Main work")
        git(self.root, "merge", "-q", "--no-ff", "-m", "Merge side", "side")
        code, lines = self.scan()
        self.assertEqual(code, 1, lines)
        self.assertTrue([l for l in lines if l.endswith("entry #2 (message)")], lines)

    def test_a_path_with_a_line_break_cannot_start_a_workflow_command(self):
        name = "x\n::notice::" + "y.txt"
        make_repo(self.root, {name: TERM + "\n"})
        code, lines = self.scan()
        self.assertEqual(code, 1, lines)
        self.assertIn("barred: x\\u000a::notice::y.txt:1: entry #2", lines)

    def test_a_non_ascii_path_is_printed_as_ascii(self):
        make_repo(self.root, {"café.txt": TERM + "\n"})
        code, lines = self.scan()
        self.assertIn("barred: caf\\u00e9.txt:1: entry #2", lines)


class DeadTests(Base):
    def test_a_detached_head_is_dead(self):
        make_repo(self.root)
        commit(self.root, "Second")
        git(self.root, "checkout", "-q", "--detach")
        code, lines = self.scan()
        self.assertEqual(code, 2, lines)
        self.assertEqual(lines, ["barred: DEAD detached HEAD (no branch name to scan)"])

    def test_a_shallow_clone_is_dead(self):
        source = os.path.join(self.tmp.name, "source")
        make_repo(source)
        commit(source, "Second", {"b.txt": "more\n"})
        commit(source, "Third")
        shallow = os.path.join(self.tmp.name, "shallow")
        url = "file" + "://" + source
        subprocess.run(
            ["git", "clone", "-q", "--depth", "1", url, shallow],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        code, lines = self.scan(root=shallow)
        self.assertEqual(code, 2, lines)
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith("barred: DEAD "), lines)
        self.assertIn("full history", lines[0])

    def test_a_directory_that_is_not_a_repository_is_dead(self):
        os.makedirs(self.root)
        code, lines = self.scan()
        self.assertEqual(code, 2, lines)
        self.assertTrue(lines[0].startswith("barred: DEAD "), lines)

    def test_a_crash_prints_the_exception_type_and_never_its_message(self):
        make_repo(self.root)

        def crash(_text, _entries):
            raise RuntimeError(TERM)

        with mock.patch.object(barred_scan, "scan_text", crash):
            code, lines = self.inprocess()
        self.assertEqual(code, 2)
        self.assertEqual(lines, ["barred: DEAD RuntimeError"])


class ErrorMessageTests(Base):
    def test_c1_an_error_message_that_hits_the_list_is_withheld(self):
        make_repo(self.root)
        raw_commit(self.root, b"encoding " + TERM.encode("ascii"))
        code, lines = self.scan()
        self.assertEqual(code, 2, lines)
        self.assertEqual(lines[-1], "barred: DEAD [message withheld]")

    def test_c2_control_an_error_message_that_misses_the_list_is_printed(self):
        make_repo(self.root)
        raw_commit(self.root, b"encoding ISO-8859-1")
        code, lines = self.scan()
        self.assertEqual(code, 2, lines)
        self.assertIn("declares encoding ISO-8859-1", lines[-1])

    def test_an_error_before_the_list_loaded_is_withheld(self):
        make_repo(self.root)
        boom = repofiles.RepoFilesError("anything")
        with mock.patch.object(barred_scan, "parse_list", side_effect=boom):
            code, lines = self.inprocess()
        self.assertEqual(code, 2)
        self.assertEqual(lines, ["barred: DEAD [message withheld]"])


def header_commit(root, extra_lines):
    """A commit on top of HEAD whose header carries extra_lines after the committer."""
    parent = git(root, "rev-parse", "HEAD")
    tree = git(root, "rev-parse", "HEAD^{tree}")
    who = "Ann Example <ann@example.invalid> 1700000000 +0000"
    lines = ["tree " + tree, "parent " + parent, "author " + who, "committer " + who]
    raw = "\n".join(lines + list(extra_lines)) + "\n\nWork\n"
    cid = subprocess.run(
        ["git", "-C", root, "hash-object", "-t", "commit", "--literally", "-w", "--stdin"],
        input=raw.encode("utf-8"), stdout=subprocess.PIPE, check=True,
    ).stdout.decode("ascii").strip()
    git(root, "update-ref", "HEAD", cid)
    return git(root, "rev-parse", "--short", cid)


def continued_commit(root, key):
    """A commit on top of HEAD with a continuation line under the given covered key."""
    parent = git(root, "rev-parse", "HEAD")
    tree = git(root, "rev-parse", "HEAD^{tree}")
    who = "Ann Example <ann@example.invalid> 1700000000 +0000"
    lines = ["tree " + tree, "parent " + parent, "author " + who, "committer " + who]
    at = [i for i, line in enumerate(lines) if line.startswith(key + " ")][0]
    lines.insert(at + 1, " x " + TERM + " y")
    raw = "\n".join(lines) + "\n\nWork\n"
    cid = subprocess.run(
        ["git", "-C", root, "hash-object", "-t", "commit", "--literally", "-w", "--stdin"],
        input=raw.encode("utf-8"), stdout=subprocess.PIPE, check=True,
    ).stdout.decode("ascii").strip()
    git(root, "update-ref", "HEAD", cid)
    return git(root, "rev-parse", "--short", cid)


def forged_commit(root, edit, parents=None):
    """A commit on top of HEAD (or on the given parents) whose four header lines
    are passed through edit(lines) first, so a test can bend any of them."""
    heads = parents or [git(root, "rev-parse", "HEAD")]
    tree = git(root, "rev-parse", "HEAD^{tree}")
    who = "Ann Example <ann@example.invalid> 1700000000 +0000"
    lines = ["tree " + tree] + ["parent " + p for p in heads]
    lines += ["author " + who, "committer " + who]
    raw = "\n".join(edit(lines)) + "\n\nWork\n"
    cid = subprocess.run(
        ["git", "-C", root, "hash-object", "-t", "commit", "--literally", "-w", "--stdin"],
        input=raw.encode("utf-8"), stdout=subprocess.PIPE, check=True,
    ).stdout.decode("ascii").strip()
    git(root, "update-ref", "HEAD", cid)
    return git(root, "rev-parse", "--short", cid)


def replace_line(prefix, new):
    """An edit for forged_commit: the first line starting with prefix becomes new."""
    def edit(lines):
        at = [i for i, line in enumerate(lines) if line.startswith(prefix)][0]
        return lines[:at] + [new] + lines[at + 1 :]
    return edit


class HeaderTests(Base):
    def test_f2_cont_author_a_continuation_under_author_is_found(self):
        make_repo(self.root)
        sha = continued_commit(self.root, "author")
        code, lines = self.scan()
        self.assertEqual(code, 1, lines)
        self.assertIn("barred: commit %s: entry #2 (header)" % sha, lines)

    def test_f2_cont_parent_a_continuation_under_parent_is_found(self):
        make_repo(self.root)
        sha = continued_commit(self.root, "parent")
        code, lines = self.scan()
        self.assertEqual(code, 1, lines)
        self.assertIn("barred: commit %s: entry #2 (header)" % sha, lines)

    def test_f2_mergetag_a_term_in_an_embedded_tag_is_found(self):
        make_repo(self.root)
        parent = git(self.root, "rev-parse", "HEAD")
        sha = header_commit(
            self.root,
            [
                "mergetag object " + parent,
                " type commit",
                " tag v1",
                " tagger " + TERM + " <t@example.invalid> 1700000000 +0000",
                " ",
                " a message",
            ],
        )
        for code, lines in self.scan_both():
            self.assertEqual(code, 1, lines)
            self.assertIn("barred: commit %s: entry #2 (header)" % sha, lines)

    def test_f2_extra_a_term_in_an_extra_header_is_found(self):
        make_repo(self.root)
        sha = header_commit(self.root, ["x-note a " + TERM + " b"])
        code, lines = self.scan()
        self.assertEqual(code, 1, lines)
        self.assertIn("barred: commit %s: entry #2 (header)" % sha, lines)

    def test_f2_clean_a_commit_with_only_the_four_keys_has_no_header_finding(self):
        # The one-commit clean tests above also show no (header) line; this one
        # adds a parent, so the parent key is covered too.
        make_repo(self.root)
        header_commit(self.root, [])
        code, lines = self.scan()
        self.assertEqual(code, 0, lines)
        self.assertFalse([l for l in lines if "(header)" in l], lines)

    def test_f2_a_clean_extra_header_is_not_a_finding(self):
        make_repo(self.root)
        header_commit(self.root, ["x-note nothing here"])
        code, lines = self.scan()
        self.assertEqual(code, 0, lines)

    def assert_header_found(self, edit):
        make_repo(self.root)
        sha = forged_commit(self.root, edit)
        code, lines = self.scan()
        self.assertEqual(code, 1, lines)
        self.assertIn("barred: commit %s: entry #2 (header)" % sha, lines)

    def test_a_tz_author_junk_after_the_zone_is_found(self):
        self.assert_header_found(
            replace_line("author ", "author a <a@x.invalid> 1700000000 +0000 " + TERM)
        )

    def test_a_tz_committer_junk_after_the_zone_is_found(self):
        self.assert_header_found(
            replace_line("committer ", "committer a <a@x.invalid> 1700000000 +0000 " + TERM)
        )

    def test_a_angle_a_second_angle_bracket_field_is_found(self):
        self.assert_header_found(
            replace_line("author ", "author a <e@x.invalid> <" + TERM + "> 1700000000 +0000")
        )

    def test_a_tree2_a_second_tree_line_is_found(self):
        def edit(lines):
            return lines[:1] + ["tree " + TERM] + lines[1:]

        self.assert_header_found(edit)

    def test_a_dup_author_the_first_of_two_author_lines_is_found(self):
        def edit(lines):
            at = [i for i, line in enumerate(lines) if line.startswith("author ")][0]
            first = "author " + TERM + " <e@x.invalid> 1700000000 +0000"
            second = "author Ann Example <ann@example.invalid> 1700000000 +0000"
            return lines[:at] + [first, second] + lines[at + 1 :]

        self.assert_header_found(edit)

    def test_a_merge_clean_a_well_formed_merge_has_no_header_finding(self):
        make_repo(self.root)
        git(self.root, "checkout", "-q", "-b", "side")
        commit(self.root, "Side", {"b.txt": "other\n"})
        side = git(self.root, "rev-parse", "HEAD")
        git(self.root, "checkout", "-q", "main")
        main = git(self.root, "rev-parse", "HEAD")
        forged_commit(self.root, lambda lines: lines, parents=[main, side])
        code, lines = self.scan()
        self.assertEqual(code, 0, lines)
        self.assertFalse([l for l in lines if "(header)" in l], lines)

    def test_f3_a_deleted_gitlink_path_is_still_a_path(self):
        make_repo(self.root)
        ident = {
            "GIT_AUTHOR_NAME": CLEAN[0],
            "GIT_AUTHOR_EMAIL": CLEAN[1],
            "GIT_COMMITTER_NAME": CLEAN[0],
            "GIT_COMMITTER_EMAIL": CLEAN[1],
        }
        link = TERM + "-mod"
        head = git(self.root, "rev-parse", "HEAD")
        git(self.root, "update-index", "--add", "--cacheinfo", "160000,%s,%s" % (head, link))
        git(self.root, "commit", "-q", "-m", "Add link", env=ident)
        git(self.root, "rm", "-q", "--cached", link)
        git(self.root, "commit", "-q", "-m", "Remove link", env=ident)
        code, lines = self.scan()
        self.assertEqual(code, 1, lines)
        self.assertTrue([l for l in lines if l.endswith("entry #2 (path)")], lines)


class MutationPinTests(Base):
    def test_b_a_deleted_file_in_a_nested_directory_is_found(self):
        make_repo(self.root, {"sub/deep/x.txt": TERM + "\n"})
        os.remove(os.path.join(self.root, "sub", "deep", "x.txt"))
        commit(self.root, "Remove it")
        code, lines = self.scan()
        self.assertEqual(code, 1, lines)
        self.assertIn("barred: sub/deep/x.txt:1: entry #2", lines)

    def test_c_raw_a_message_that_hits_as_written_is_withheld(self):
        bad = os.path.join(self.tmp.name, fullwidth(TERM))
        os.makedirs(bad)
        code, lines = self.scan(root=bad)
        self.assertEqual(code, 2, lines)
        self.assertEqual(lines, ["barred: DEAD [message withheld]"])

    def test_c_esc_a_message_that_hits_only_once_escaped_is_withheld(self):
        entry = "x" + "\\" + "u00e9y"
        list_text = CANARY + "\n" + entry + "\n"
        bad = os.path.join(self.tmp.name, "x" + "\u00e9" + "y")
        os.makedirs(bad)
        code, lines = self.scan(list_text, root=bad)
        self.assertEqual(code, 2, lines)
        self.assertEqual(lines, ["barred: DEAD [message withheld]"])

    def test_d_a_self_overlapping_whole_word_entry_restarts_one_character_on(self):
        list_text = CANARY + "\n" + "qz-" + "qz" + "\tword\n"
        make_repo(self.root, {"a.txt": "xqz-qz-qz\n"})
        code, lines = self.scan(list_text)
        self.assertEqual(code, 1, lines)
        self.assertIn("barred: a.txt:1: entry #2", lines)

    def test_e_two_entries_in_one_text_are_both_reported(self):
        list_text = CANARY + "\n" + TERM + "\n" + "zq-" + "second-9k" + "\n"
        make_repo(self.root, {"a.txt": TERM + " and " + "zq-" + "second-9k\n"})
        code, lines = self.scan(list_text)
        self.assertEqual(code, 1, lines)
        self.assertIn("barred: a.txt:1: entry #2", lines)
        self.assertIn("barred: a.txt:1: entry #3", lines)


class CanaryNumberTests(Base):
    def test_m_canary_the_canary_must_hit_as_entry_one_itself(self):
        make_repo(self.root)
        text = CANARY + "\n" + "canary-" + "t3st" + "\n"
        real = barred_scan.first_hit

        def skip_entry_one(folded, entry):
            return -1 if entry.number == 1 else real(folded, entry)

        with mock.patch.object(barred_scan, "first_hit", skip_entry_one):
            code, lines = self.inprocess(text)
        self.assertEqual(code, 2)
        self.assertEqual(lines, ["barred: DEAD the canary did not match"])


class PinTests(Base):
    def test_p_withheld_a_path_that_hits_only_as_a_whole_is_withheld(self):
        text = CANARY + "\n" + "qq-dir/" + "sec-ret9" + "\n"
        make_repo(self.root, {"qq-dir/" + "sec-ret9": "clean\n"})
        code, lines = self.scan(text)
        self.assertEqual(code, 1, lines)
        self.assertIn("barred: [path withheld]: entry #2 (path)", lines)

    def test_p_rawpath_a_path_that_hits_only_after_the_fold_is_hidden(self):
        make_repo(self.root, {fullwidth(TERM) + ".txt": "clean\n"})
        code, lines = self.scan()
        self.assertEqual(code, 1, lines)
        self.assertIn("barred: [hit]: entry #2 (path)", lines)

    def test_p_del_the_delete_character_is_escaped(self):
        make_repo(self.root, {"a" + "\x7f" + "b.txt": TERM + "\n"})
        code, lines = self.scan()
        self.assertEqual(code, 1, lines)
        self.assertIn("barred: a\\u007fb.txt:1: entry #2", lines)

    def test_p_symlink_an_uncommitted_link_target_is_scanned(self):
        make_repo(self.root)
        os.symlink(TERM, os.path.join(self.root, "lnk"))
        code, lines = self.scan()
        self.assertEqual(code, 1, lines)
        self.assertIn("barred: lnk:1: entry #2", lines)

    def test_p_bom_a_bom_then_a_comment_then_the_canary_loads(self):
        make_repo(self.root)
        text = "\ufeff" + "# a comment\n" + CANARY + "\n" + TERM + "\n"
        for code, lines in self.scan_both(text):
            self.assertEqual(code, 0, lines)
            self.assertIn("canary matched", lines[-1])

    def test_p_lo_a_letter_of_category_lo_is_a_word_character(self):
        text = CANARY + "\n" + WORD + "\tword\n"
        for n, body in enumerate((WORD + "\u4e2d\n", "\u4e2d" + WORD + "\n")):
            with self.subTest(body=n):
                root = os.path.join(self.tmp.name, "lo%d" % n)
                make_repo(root, {"a.txt": body})
                code, lines = self.scan(text, root=root)
                self.assertEqual(code, 0, lines)


class FoldTests(unittest.TestCase):
    def test_the_fold_order_and_steps(self):
        self.assertEqual(barred_scan.fold("ＡＢ"), "ab")
        self.assertEqual(barred_scan.fold("a\u200db"), "ab")
        self.assertEqual(barred_scan.fold("a \n\t b"), "a b")
        self.assertEqual(barred_scan.fold("  a"), " a")
        self.assertEqual(barred_scan.fold("Straße"), "strasse")

    def test_a_word_character_is_a_letter_or_a_decimal_digit(self):
        for char in ("a", "é", "7", "٣"):
            self.assertTrue(barred_scan.is_word_char(char), char)
        for char in ("_", "-", " ", "²", "́"):
            self.assertFalse(barred_scan.is_word_char(char), char)


class SelfScanTests(Base):
    def test_the_real_repo_passes_with_the_synthetic_list(self):
        root = REAL_ROOT
        if repofiles.current_branch(REAL_ROOT) is None:
            # A CI checkout of a pull request is detached; scan an attached clone.
            root = os.path.join(self.tmp.name, "clone")
            url = "file" + "://" + REAL_ROOT
            subprocess.run(
                ["git", "clone", "-q", url, root],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            git(root, "checkout", "-q", "-b", "selfscan")
        code, lines = self.scan(root=root)
        self.assertEqual(code, 0, lines)
        self.assertRegex(lines[-1], r"^barred: ok \(3 entries, \d+ blobs, \d+ commits, canary matched\)$")


if __name__ == "__main__":
    unittest.main()
