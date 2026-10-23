import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import check_vocabulary
from lib import repofiles

REAL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REAL_ROOT, "tools", "check_vocabulary.py")

# Every example is assembled here, so this file passes the rules it tests.
W = "harn" + "ess"
ROW = "| `" + W + "` | The runtime. |"
VOCAB = "\n".join(
    ["# Vocabulary", "", "## Terms", "", "| Value | Meaning |", "| --- | --- |", ROW, ""]
)
RESERVED_ID = "HA" + "R" + "-001"
TILDE = "~" + "/"
USERS = "/Us" + "ers/"
HOMEDIR = "/ho" + "me/"
ENV_HOME = "$" + "HO" + "ME"
BRACED = "${" + "HOME}"
WIN_ENV = "%USER" + "PROFILE%"
WIN_PATH = "C:" + "\\Us" + "ers\\"
PATH_HOME = "Path" + ".home("
EXPAND = "expand" + "user("
HOME_KEY = '"' + "HO" + 'ME"'
HOME_KEY_SINGLE = "'" + "HO" + "ME'"
IDENT = "-c", "user.name=test", "-c", "user.email=test@example.invalid"


def git(root, *args):
    cmd = ["git"] + list(IDENT) + ["-C", root] + list(args)
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)


def write(root, path, text):
    full = os.path.join(root, *path.split("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as handle:
        handle.write(text)


def make_repo(root, files, vocab=True, messages=()):
    """A git repository holding files, with one commit per message."""
    files = dict(files)
    if vocab:
        files.setdefault("docs/vocabulary.md", VOCAB)
    for path, text in files.items():
        write(root, path, text)
    subprocess.run(
        ["git", "init", "-q", root],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    for message in messages:
        git(root, "add", "-A")
        git(root, "commit", "-q", "--allow-empty", "-m", message)


def commit_as(root, message, name, email, committer=None, committer_email=None):
    """One commit with the given identities; the committer env is for this call only."""
    env = dict(os.environ)
    if committer is not None:
        env["GIT_COMMITTER_NAME"] = committer
    if committer_email is not None:
        env["GIT_COMMITTER_EMAIL"] = committer_email
    cmd = ["git", "-c", "user.name=" + name, "-c", "user.email=" + email, "-C", root]
    cmd += ["commit", "-q", "--allow-empty", "-m", message]
    subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, env=env
    )


def out(root, *args):
    cmd = ["git"] + list(IDENT) + ["-C", root] + list(args)
    return subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True
    ).stdout.decode("utf-8").strip()


def raw_commit(root, header_extra, body):
    """Write a commit object by hand (bytes body) and point the current branch at it."""
    tree = subprocess.run(
        ["git", "-C", root, "mktree"], input=b"", stdout=subprocess.PIPE, check=True
    ).stdout.decode("ascii").strip()
    who = b"T <t@example.invalid> 1700000000 +0000"
    raw = b"tree " + tree.encode("ascii") + b"\nauthor " + who + b"\ncommitter " + who + b"\n"
    raw += header_extra + b"\n" + body
    proc = subprocess.run(
        ["git", "-C", root, "hash-object", "-t", "commit", "-w", "--stdin", "--literally"],
        input=raw, stdout=subprocess.PIPE, check=True,
    )
    cid = proc.stdout.decode("ascii").strip()
    out(root, "update-ref", "HEAD", cid)
    return cid


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "repo")
        os.makedirs(self.root)

    def check(self, files, **kwargs):
        make_repo(self.root, files, **kwargs)
        return check_vocabulary.run(self.root)

    def assertHit(self, result, rule, where=None):
        code, lines = result
        self.assertEqual(code, 1, lines)
        hits = [l for l in lines if (": %s " % rule) in l]
        self.assertTrue(hits, lines)
        if where is not None:
            self.assertTrue(any(where in l for l in hits), lines)

    def assertClean(self, result):
        code, lines = result
        self.assertEqual(code, 0, lines)
        self.assertRegex(lines[-1], r"^vocabulary: ok \(\d+ files, \d+ commit messages\)$")


class V1Tests(Base):
    def test_reserved_id_is_a_hit(self):
        self.assertHit(self.check({"a.txt": "see " + RESERVED_ID + " here\n"}), "V1", "a.txt:1:")

    def test_other_prefixes_are_hits(self):
        text = "MO" + "D-12 and RI" + "N-7\n"
        code, lines = self.check({"a.txt": text})
        self.assertEqual(code, 1)
        self.assertEqual(len([l for l in lines if ": V1 " in l]), 1, lines)

    def test_each_prefix_hits_on_its_own_line(self):
        # Passes today by design: a pin.
        for text in (RESERVED_ID, "MO" + "D-12", "RI" + "N-7"):
            with self.subTest(text=text):
                with tempfile.TemporaryDirectory() as tmp:
                    make_repo(tmp, {"a.txt": "x\n" + text + "\n"})
                    self.assertHit(check_vocabulary.run(tmp), "V1", "a.txt:2:")

    def test_lowercase_is_clean(self):
        self.assertClean(self.check({"a.txt": "har" + "-001\n"}))

    def test_not_a_whole_word_is_clean(self):
        self.assertClean(self.check({"a.txt": "CHAR" + "-001 and HA" + "R-0x1\n"}))


class V2Tests(Base):
    def test_compound_before_is_a_hit(self):
        self.assertHit(self.check({"a.txt": "foo" + "-" + W + "\n"}), "V2")

    def test_compound_after_is_a_hit(self):
        self.assertHit(self.check({"a.txt": W + "_" + "bar\n"}), "V2")

    def test_digit_join_is_a_hit(self):
        self.assertHit(self.check({"a.txt": "x1_" + W + "\n"}), "V2")

    def test_upper_case_is_a_hit(self):
        # Passes today by design: a pin.
        self.assertHit(self.check({"a.txt": "FOO" + "-" + W.upper() + "\n"}), "V2", "a.txt:1:")

    def test_bare_word_is_clean(self):
        self.assertClean(self.check({"a.txt": "the agent " + W + " runs\n"}))

    def test_longer_word_is_clean(self):
        self.assertClean(self.check({"a.txt": W + "ing\n"}))

    def test_word_as_a_heading_outside_the_readme_is_clean(self):
        self.assertClean(self.check({"notes.md": "# " + W.capitalize() + "\n\nText.\n"}))


class V3Tests(Base):
    def test_word_then_lane_is_a_hit(self):
        self.assertHit(self.check({"a.txt": "the " + W + " " + "la" + "ne\n"}), "V3")

    def test_across_a_line_break_is_a_hit(self):
        self.assertHit(self.check({"a.txt": W + "\n" + "LA" + "NE\n"}), "V3", "a.txt:1:")

    def test_no_space_before_the_lane_word_is_clean(self):
        # Passes today by design: a pin.
        self.assertClean(self.check({"a.txt": W + "la" + "ne\n"}))

    def test_separated_is_clean(self):
        self.assertClean(self.check({"a.txt": W + ", then the " + "la" + "ne\n"}))


class V4Tests(Base):
    def test_every_path_shape_is_a_hit(self):
        shapes = [
            TILDE + "x",
            USERS + "a",
            HOMEDIR + "a",
            ENV_HOME,
            BRACED,
            WIN_ENV,
            WIN_PATH + "x",
        ]
        for shape in shapes:
            with self.subTest(shape=shape):
                with tempfile.TemporaryDirectory() as tmp:
                    make_repo(tmp, {"a.txt": "see " + shape + "\n"})
                    self.assertHit(check_vocabulary.run(tmp), "V4", "a.txt:1:")

    def test_upper_case_is_a_hit(self):
        # Passes today by design: a pin.
        for shape in (USERS.upper() + "A", HOMEDIR.upper() + "A"):
            with self.subTest(shape=shape):
                with tempfile.TemporaryDirectory() as tmp:
                    make_repo(tmp, {"a.txt": "see " + shape + "\n"})
                    self.assertHit(check_vocabulary.run(tmp), "V4", "a.txt:1:")

    def test_tilde_without_a_slash_is_clean(self):
        self.assertClean(self.check({"a.txt": "about ~5 items, ~ and ~x\n"}))

    def test_users_without_a_letter_is_clean(self):
        self.assertClean(self.check({"a.txt": "the " + USERS[:-1] + " dir, " + USERS + "\n"}))

    def test_home_dir_without_a_letter_is_clean(self):
        self.assertClean(self.check({"a.txt": "at " + HOMEDIR + "\n"}))


class V5Tests(Base):
    def test_every_python_shape_is_a_hit(self):
        for shape in (PATH_HOME, EXPAND, HOME_KEY, HOME_KEY_SINGLE):
            with self.subTest(shape=shape):
                with tempfile.TemporaryDirectory() as tmp:
                    make_repo(tmp, {"a.py": "x = " + shape + "\n"})
                    self.assertHit(check_vocabulary.run(tmp), "V5", "a.py:1:")

    def test_upper_case_is_a_hit(self):
        # Passes today by design: a pin.
        for shape in (PATH_HOME.upper(), EXPAND.upper()):
            with self.subTest(shape=shape):
                with tempfile.TemporaryDirectory() as tmp:
                    make_repo(tmp, {"a.py": "x = " + shape + "\n"})
                    self.assertHit(check_vocabulary.run(tmp), "V5", "a.py:1:")

    def test_r5_runner_root_is_anchored_at_the_repository_root(self):
        path = "tools/experiments/EXP-0001/code/runner/r.py"
        self.assertHit(self.check({path: "x = " + EXPAND + ")\n"}), "V5", path + ":1:")

    def test_ascii_limit_is_stated_for_v5_and_v7(self):
        flat = " ".join(check_vocabulary.__doc__.split())
        sentence = "Rules are ASCII-defined; Unicode lookalikes are not matched."
        self.assertEqual(flat.count(sentence), 2)

    def test_prose_in_markdown_is_clean(self):
        self.assertClean(self.check({"a.md": "Set HO" + "ME to a folder. " + PATH_HOME + "\n"}))

    def test_unquoted_name_in_python_is_clean(self):
        self.assertClean(self.check({"a.py": "HO" + "MEWORK = 1\nprint(HO" + "ME_COUNT)\n"}))

    def test_limit_is_stated_verbatim(self):
        sentence = (
            "V5 matches names as written: an aliased expanduser or Path.home, "
            "and a HOME key built at runtime, are not tracked."
        )
        flat = " ".join(check_vocabulary.__doc__.split())
        self.assertIn(sentence, flat)

    def test_s1_a_quoted_home_key_outside_tests_is_a_hit(self):
        # Passes today by design: a pin.
        self.assertHit(self.check({"tools/x.py": "e = {" + HOME_KEY + ": 1}\n"}), "V5", "tools/x.py:1:")

    def test_s2_a_quoted_home_key_in_tests_is_clean(self):
        self.assertClean(self.check({"tests/x.py": "e = {" + HOME_KEY + ": 1}\n"}))

    def test_s3_an_expanduser_call_in_tests_is_a_hit(self):
        # Passes today by design: a pin.
        self.assertHit(self.check({"tests/x.py": "p = " + EXPAND + ")\n"}), "V5", "tests/x.py:1:")

    def test_s4_a_path_home_call_in_tests_is_a_hit(self):
        # Passes today by design: a pin.
        self.assertHit(self.check({"tests/x.py": "p = " + PATH_HOME + ")\n"}), "V5", "tests/x.py:1:")

    def test_s5_tests_elsewhere_in_the_path_is_not_scoped(self):
        # Passes today by design: a pin.
        for path in ("tools/tests_helper.py", "docs/tests/x.py", "tests_x/y.py"):
            with self.subTest(path=path):
                with tempfile.TemporaryDirectory() as tmp:
                    make_repo(tmp, {path: "e = {" + HOME_KEY + ": 1}\n"})
                    self.assertHit(check_vocabulary.run(tmp), "V5", path + ":1:")

    def test_r1_runner_root_skips_v5(self):
        path = "experiments/EXP-0001/code/runner/r.py"
        for text in (
            "p = " + EXPAND + ")\n",
            "p = " + PATH_HOME + ")\n",
            "e = {" + HOME_KEY + ": 1}\n",
        ):
            with self.subTest(text=text):
                with tempfile.TemporaryDirectory() as tmp:
                    make_repo(tmp, {path: text})
                    self.assertClean(check_vocabulary.run(tmp))

    def test_r2_outside_the_runner_root_v5_hits(self):
        for path in (
            "experiments/EXP-0001/code/x.py",
            "experiments/EXP-0001/code/runner.py",
            "tools/x.py",
        ):
            for text in (
                "p = " + EXPAND + ")\n",
                "p = " + PATH_HOME + ")\n",
                "e = {" + HOME_KEY + ": 1}\n",
            ):
                with self.subTest(path=path, text=text):
                    with tempfile.TemporaryDirectory() as tmp:
                        make_repo(tmp, {path: text})
                        self.assertHit(check_vocabulary.run(tmp), "V5", path + ":1:")

    def test_r3_runner_root_still_gets_v4(self):
        path = "experiments/EXP-0001/code/runner/r.py"
        self.assertHit(self.check({path: "p = '" + TILDE + "x'\n"}), "V4", path + ":1:")

    def test_r4_near_miss_runner_paths_still_hit(self):
        for path in (
            "experiments/a/b/code/runner/r.py",
            "experiments/EXP-0001/runner/r.py",
            "experiments/EXP-0001/other/runner/r.py",
            "experiments/EXP-0001/code/x/runner/r.py",
        ):
            with self.subTest(path=path):
                with tempfile.TemporaryDirectory() as tmp:
                    make_repo(tmp, {path: "p = " + EXPAND + ")\n"})
                    self.assertHit(check_vocabulary.run(tmp), "V5", path + ":1:")


# V8 examples: every token-shaped string is assembled here from parts.
EXP = "EXP-" + "0001"
TOKENS = {
    "gh": ("gh" + "p" + "_") + "A" * 36,
    "gh-u": ("gh" + "u" + "_") + "b1" * 20,
    "fine-grained": ("github" + "_pat_") + "A_" * 11,
    "aws": ("AK" + "IA") + "Q" * 16,
    "sk": ("sk" + "-") + "a-" * 10,
    "slack": ("xo" + "xb" + "-") + "1" * 12,
    "google": ("AI" + "za") + "Z" * 35,
    "key": "-----" + "BEGIN " + "RSA " + "PRIVATE KEY" + "-----",
    "key-bare": "-----" + "BEGIN " + "PRIVATE KEY" + "-----",
}
NEAR_MISSES = {
    "sk": "ta" + "sk" + "-" + "abcdefghijklmnopqrstuvwxy",
    "aws": ("AK" + "IA") + "Q" * 15,
    "gh": ("gh" + "p" + "_") + "A" * 35,
    "slack": ("xo" + "xq" + "-") + "1" * 12,
    "google": ("AI" + "za") + "Z" * 34,
    "key": "-----" + "BEGIN " + "PUBLIC KEY" + "-----",
    "fine-grained": ("github" + "_pat_") + "A" * 21,
}


class V8Tests(Base):
    def test_every_pattern_has_a_positive(self):
        for name, token in TOKENS.items():
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    make_repo(tmp, {"a.txt": "value " + token + " end\n"})
                    result = check_vocabulary.run(tmp)
                    self.assertHit(result, "V8", "a.txt:1: V8 token-shaped string")
                    self.assertNotIn(token, "\n".join(result[1]))

    def test_every_pattern_has_a_near_miss(self):
        for name, text in NEAR_MISSES.items():
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    make_repo(tmp, {"a.txt": "value " + text + " end\n"})
                    self.assertClean(check_vocabulary.run(tmp))

    def test_sk_lookbehind_blocks_a_longer_word(self):
        for before in ("a", "9", "_", "-"):
            with self.subTest(before=before):
                with tempfile.TemporaryDirectory() as tmp:
                    make_repo(tmp, {"a.txt": before + "sk" + "-" + "a" * 25 + "\n"})
                    self.assertClean(check_vocabulary.run(tmp))

    def test_aws_shape_must_end_the_word(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_repo(tmp, {"a.txt": ("AK" + "IA") + "Q" * 16 + "R\n"})
            self.assertClean(check_vocabulary.run(tmp))

    def test_the_rule_is_case_sensitive(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_repo(tmp, {"a.txt": ("GH" + "P" + "_") + "A" * 36 + "\n"})
            self.assertClean(check_vocabulary.run(tmp))

    def test_a_recorded_file_holding_a_token_shape_is_a_hit(self):
        path = "experiments/%s/recorded/out.txt" % EXP
        with tempfile.TemporaryDirectory() as tmp:
            make_repo(tmp, {path: "k " + TOKENS["gh"] + "\n"})
            result = check_vocabulary.run(tmp)
            self.assertHit(result, "V8", path + ":1:")
            self.assertNotIn(TOKENS["gh"], "\n".join(result[1]))

    def test_a_commit_message_holding_a_token_shape_is_a_hit(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_repo(tmp, {"a.txt": "x\n"}, messages=["Add " + TOKENS["aws"]])
            code, lines = check_vocabulary.run(tmp)
            self.assertEqual(code, 1, lines)
            self.assertRegex(lines[0], r"^vocabulary: commit [0-9a-f]+: V8 token-shaped string$")
            self.assertNotIn(TOKENS["aws"], "\n".join(lines))

    def test_an_identity_holding_a_token_shape_is_a_hit(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_repo(tmp, {"a.txt": "x\n"}, messages=["First"])
            write(tmp, "b.txt", "y\n")
            git(tmp, "add", "-A")
            commit_as(tmp, "Second", TOKENS["sk"], "t@example.invalid")
            code, lines = check_vocabulary.run(tmp)
            self.assertEqual(code, 1, lines)
            self.assertTrue(any(": V8 author token-shaped string" in l for l in lines), lines)
            self.assertNotIn(TOKENS["sk"], "\n".join(lines))

    def test_the_v8_control_is_required(self):
        make_repo(self.root, {"a.txt": "x\n"})
        controls = dict(check_vocabulary.CONTROLS)
        controls["V8"] = "nothing to see"
        with mock.patch.object(check_vocabulary, "CONTROLS", controls):
            code, lines = check_vocabulary.run(self.root)
        self.assertEqual(code, 2)
        self.assertIn("V8", lines[0])

    def test_v8_is_a_commit_rule(self):
        self.assertIn("V8", check_vocabulary.COMMIT_RULES)


class RecordedV5Tests(Base):
    CALL = "x = " + PATH_HOME + ")\n"

    def test_a_recorded_python_file_is_not_checked_for_v5(self):
        self.assertClean(self.check({"experiments/%s/recorded/a.py" % EXP: self.CALL}))

    def test_the_same_file_under_code_fails(self):
        path = "experiments/%s/code/a.py" % EXP
        self.assertHit(self.check({path: self.CALL}), "V5", path + ":1:")

    def test_a_recorded_folder_outside_an_experiment_is_checked(self):
        for path in (
            "tools/experiments/%s/recorded/a.py" % EXP,
            "recorded/a.py",
            "experiments/%s/recorded_x/a.py" % EXP,
            "experiments/%s/code/recorded/a.py" % EXP,
        ):
            with self.subTest(path=path):
                with tempfile.TemporaryDirectory() as tmp:
                    make_repo(tmp, {path: self.CALL})
                    self.assertHit(check_vocabulary.run(tmp), "V5", path + ":1:")

    def test_other_rules_still_apply_to_a_recorded_file(self):
        path = "experiments/%s/recorded/a.py" % EXP
        self.assertHit(self.check({path: "x = '" + TILDE + "y'\n"}), "V4", path + ":1:")

    def test_scan_text_takes_the_recorded_flag(self):
        self.assertEqual(check_vocabulary.scan_text(self.CALL, True, in_recorded=True), [])
        self.assertTrue(check_vocabulary.scan_text(self.CALL, True))


class V6Tests(Base):
    def vocab_with(self, rows):
        lines = ["# Vocabulary", "", "## Terms", "", "| Value | Meaning |", "| --- | --- |"]
        lines.extend(rows)
        lines.append("")
        return "\n".join(lines)

    def test_one_row_is_clean(self):
        self.assertClean(self.check({}))

    def test_second_row_in_another_file_is_a_hit(self):
        self.assertHit(self.check({"notes.md": "intro\n" + ROW + "\n"}), "V6", "notes.md:2:")

    def test_row_shape_with_odd_spacing_is_a_hit(self):
        line = "|" + "`" + W + "`" + "   |" + " other |"
        self.assertHit(self.check({"notes.md": line + "\n"}), "V6", "notes.md:1:")

    def test_zero_rows_is_a_hit(self):
        result = self.check({"docs/vocabulary.md": self.vocab_with([])})
        self.assertHit(result, "V6", "docs/vocabulary.md")

    def test_two_rows_is_a_hit(self):
        result = self.check({"docs/vocabulary.md": self.vocab_with([ROW, ROW])})
        self.assertHit(result, "V6", "docs/vocabulary.md")

    def test_a_row_outside_the_terms_section_does_not_count(self):
        text = self.vocab_with([]) + "\n## Other\n\n" + ROW + "\n"
        self.assertHit(self.check({"docs/vocabulary.md": text}), "V6", "docs/vocabulary.md")

    def test_missing_vocabulary_file_is_a_hit(self):
        self.assertHit(self.check({"a.txt": "x\n"}, vocab=False), "V6", "docs/vocabulary.md")

    def test_readme_first_paragraph_naming_the_word_is_a_hit(self):
        text = "# Title\n\nA tool for the " + W + " world.\n\nMore.\n"
        self.assertHit(self.check({"README.md": text}), "V6", "README.md:3:")

    def test_readme_heading_naming_the_word_is_a_hit(self):
        text = "# The " + W.upper() + "\n\nA tool.\n"
        self.assertHit(self.check({"README.md": text}), "V6", "README.md:1:")

    def test_readme_naming_it_further_down_is_clean(self):
        text = "# Title\n\nA tool.\nTwo lines.\n\nLater, the " + W + " appears.\n"
        self.assertClean(self.check({"README.md": text}))

    def test_readme_without_a_heading_is_clean(self):
        self.assertClean(self.check({"README.md": "Plain text.\n"}))


class V7Tests(Base):
    def test_commit_message_with_a_compound_is_a_hit(self):
        result = self.check({"a.txt": "x\n"}, messages=["Add " + "foo" + "-" + W + " support"])
        code, lines = result
        self.assertEqual(code, 1, lines)
        self.assertRegex(lines[0], r"^vocabulary: commit [0-9a-f]+: V2 ")
        self.assertEqual(lines[-1], "vocabulary: 1 finding(s)")

    def test_commit_message_with_each_early_rule_is_a_hit(self):
        for message, rule in [
            ("Add " + RESERVED_ID, "V1"),
            ("Add the " + W + " " + "la" + "ne", "V3"),
            ("Add " + TILDE + "x", "V4"),
        ]:
            with self.subTest(rule=rule):
                with tempfile.TemporaryDirectory() as tmp:
                    make_repo(tmp, {"a.txt": "x\n"}, messages=[message])
                    self.assertHit(check_vocabulary.run(tmp), rule, "commit ")

    def test_python_only_rule_does_not_apply_to_messages(self):
        result = self.check({"a.txt": "x\n"}, messages=["Use " + PATH_HOME + ")"])
        self.assertClean(result)

    def test_clean_message_passes_and_is_counted(self):
        code, lines = self.check({"a.txt": "x\n"}, messages=["First", "Second"])
        self.assertEqual(code, 0, lines)
        self.assertEqual(lines[-1], "vocabulary: ok (2 files, 2 commit messages)")

    def test_body_of_a_message_is_scanned(self):
        result = self.check({"a.txt": "x\n"}, messages=["Subject\n\nBody mentions " + RESERVED_ID])
        self.assertHit(result, "V1", "commit ")

    def test_no_commits_reports_zero_messages(self):
        code, lines = self.check({"a.txt": "x\n"})
        self.assertEqual(code, 0, lines)
        self.assertEqual(lines[-1], "vocabulary: ok (2 files, 0 commit messages)")

    def test_shallow_clone_is_dead(self):
        source = os.path.join(self.tmp.name, "source")
        os.makedirs(source)
        make_repo(source, {"a.txt": "x\n"}, messages=["One"])
        write(source, "b.txt", "y\n")
        git(source, "add", "-A")
        git(source, "commit", "-q", "-m", "Two")
        clone = os.path.join(self.tmp.name, "clone")
        subprocess.run(
            ["git", "clone", "-q", "--depth", "1", "file://" + source, clone],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        self.assertTrue(repofiles.is_shallow(clone))
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--root", clone],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        out = proc.stdout.decode("utf-8")
        self.assertEqual(proc.returncode, 2, out)
        self.assertTrue(out.startswith("vocabulary: DEAD "), out)
        self.assertIn("git fetch --unshallow", out)


class IdentityTests(Base):
    def repo(self):
        make_repo(self.root, {"a.txt": "x\n"})
        git(self.root, "add", "-A")

    def test_author_name_with_a_compound_is_an_author_hit(self):
        self.repo()
        commit_as(self.root, "One", "foo" + "-" + W, "x@example.invalid")
        code, lines = check_vocabulary.run(self.root)
        self.assertEqual(code, 1, lines)
        self.assertRegex(lines[0], r"^vocabulary: commit [0-9a-f]+: V2 author reserved word")

    def test_author_email_with_a_home_path_is_an_author_email_hit(self):
        self.repo()
        commit_as(self.root, "One", "Test", TILDE + "x@example.invalid")
        code, lines = check_vocabulary.run(self.root)
        self.assertEqual(code, 1, lines)
        self.assertRegex(lines[0], r"^vocabulary: commit [0-9a-f]+: V4 author email home")

    def test_committer_name_with_a_reserved_id_is_a_committer_hit(self):
        self.repo()
        commit_as(self.root, "One", "Test", "x@example.invalid", committer=RESERVED_ID)
        code, lines = check_vocabulary.run(self.root)
        self.assertEqual(code, 1, lines)
        self.assertRegex(lines[0], r"^vocabulary: commit [0-9a-f]+: V1 committer reserved id")

    def test_committer_email_with_a_home_path_is_a_committer_email_hit(self):
        self.repo()
        commit_as(self.root, "One", "Test", "x@example.invalid", committer_email=TILDE + "y")
        code, lines = check_vocabulary.run(self.root)
        self.assertEqual(code, 1, lines)
        self.assertRegex(lines[0], r"^vocabulary: commit [0-9a-f]+: V4 committer email home")

    def test_trailer_with_a_compound_is_a_hit(self):
        self.repo()
        message = "Subject\n\nBody.\n\nSigned-off-by: foo" + "-" + W + " <x@example.invalid>"
        commit_as(self.root, message, "Test", "x@example.invalid")
        self.assertHit(check_vocabulary.run(self.root), "V2", "commit ")

    def test_clean_non_ascii_identity_passes(self):
        self.repo()
        commit_as(self.root, "One", "Jos\u00e9 Example", "x@example.invalid")
        self.assertClean(check_vocabulary.run(self.root))

    def test_identity_reader_lists_fields_oldest_first(self):
        self.repo()
        commit_as(self.root, "One", "A", "a@example.invalid", "C", "c@example.invalid")
        commit_as(self.root, "Two", "B", "b@example.invalid", "D", "d@example.invalid")
        rows = repofiles.commit_identities(self.root)
        self.assertEqual([r[1:] for r in rows], [
            ("A", "a@example.invalid", "C", "c@example.invalid"),
            ("B", "b@example.invalid", "D", "d@example.invalid"),
        ])
        self.assertEqual([r[0] for r in rows], repofiles.commit_short_ids(self.root))

    def test_identity_reader_no_commits_is_empty(self):
        make_repo(self.root, {"a.txt": "x\n"})
        self.assertEqual(repofiles.commit_identities(self.root), [])

    def test_identity_reader_not_a_repository_raises(self):
        with self.assertRaises(repofiles.RepoFilesError):
            repofiles.commit_identities(self.tmp.name)

    def test_identity_reader_shallow_clone_raises(self):
        source = os.path.join(self.tmp.name, "source")
        os.makedirs(source)
        make_repo(source, {"a.txt": "x\n"}, messages=["One"])
        git(source, "commit", "-q", "--allow-empty", "-m", "Two")
        clone = os.path.join(self.tmp.name, "clone")
        subprocess.run(
            ["git", "clone", "-q", "--depth", "1", "file://" + source, clone],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        with self.assertRaises(repofiles.RepoFilesError):
            repofiles.commit_identities(clone)

    def test_identity_reader_git_failure_with_a_head_raises(self):
        make_repo(self.root, {"a.txt": "x\n"}, messages=["First"])
        failed = subprocess.CompletedProcess([], 128, b"", b"fatal: broken")
        real = subprocess.run

        def fake(cmd, *args, **kwargs):
            if "log" in cmd:
                return failed
            return real(cmd, *args, **kwargs)

        with mock.patch.object(repofiles.subprocess, "run", fake):
            with self.assertRaises(repofiles.RepoFilesError):
                repofiles.commit_identities(self.root)


class CommitMessageReaderTests(Base):
    def test_messages_are_whole_and_oldest_first(self):
        make_repo(self.root, {"a.txt": "x\n"}, messages=["First\n\nBody one.", "Second"])
        self.assertEqual(
            repofiles.commit_messages(self.root), ["First\n\nBody one.\n", "Second\n"]
        )

    def test_short_ids_match_the_messages(self):
        make_repo(self.root, {"a.txt": "x\n"}, messages=["First", "Second"])
        self.assertEqual(
            len(repofiles.commit_short_ids(self.root)),
            len(repofiles.commit_messages(self.root)),
        )

    def test_no_commits_is_an_empty_list(self):
        make_repo(self.root, {"a.txt": "x\n"})
        self.assertEqual(repofiles.commit_messages(self.root), [])

    def test_git_failure_with_a_head_raises(self):
        make_repo(self.root, {"a.txt": "x\n"}, messages=["First"])
        failed = subprocess.CompletedProcess([], 128, b"", b"fatal: broken")
        real = subprocess.run

        def fake(cmd, *args, **kwargs):
            if "rev-list" in cmd:
                return failed
            return real(cmd, *args, **kwargs)

        with mock.patch.object(repofiles.subprocess, "run", fake):
            with self.assertRaises(repofiles.RepoFilesError):
                repofiles.commit_messages(self.root)

    def test_not_a_repository_raises(self):
        with self.assertRaises(repofiles.RepoFilesError):
            repofiles.commit_messages(self.tmp.name)


class ControlAndCrashTests(Base):
    def test_each_rule_has_a_control_that_hits(self):
        self.assertIsNone(check_vocabulary.control())

    def test_a_dead_control_gives_exit_two(self):
        make_repo(self.root, {"a.txt": "x\n"})
        for rule in ("V1", "V2", "V3", "V4", "V5"):
            with self.subTest(rule=rule):
                controls = dict(check_vocabulary.CONTROLS)
                controls[rule] = "nothing to see"
                with mock.patch.object(check_vocabulary, "CONTROLS", controls):
                    code, lines = check_vocabulary.run(self.root)
                self.assertEqual(code, 2)
                self.assertTrue(lines[0].startswith("vocabulary: DEAD "), lines)
                self.assertIn(rule, lines[0])

    def test_zero_files_is_dead(self):
        subprocess.run(["git", "init", "-q", self.root], check=True)
        code, lines = check_vocabulary.run(self.root)
        self.assertEqual(code, 2)
        self.assertEqual(lines, ["vocabulary: DEAD the repository holds 0 files"])

    def test_not_a_repository_is_dead(self):
        code, lines = check_vocabulary.run(self.tmp.name)
        self.assertEqual(code, 2)
        self.assertTrue(lines[0].startswith("vocabulary: DEAD "), lines)

    def test_main_turns_a_crash_into_dead(self):
        def boom(_root):
            raise RuntimeError("boom")

        out = io.StringIO()
        with mock.patch.object(check_vocabulary, "run", boom):
            with mock.patch.object(sys, "stdout", out):
                code = check_vocabulary.main(["--root", self.root])
        self.assertEqual(code, 2)
        self.assertEqual(out.getvalue().strip(), "vocabulary: DEAD RuntimeError: boom")

    def test_broken_import_is_dead_not_a_traceback(self):
        copy = os.path.join(self.tmp.name, "tools")
        shutil.copytree(
            os.path.join(REAL_ROOT, "tools"), copy,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        path = os.path.join(copy, "lib", "repofiles.py")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        text = text.replace(
            "import subprocess\n", "import subprocess\nimport no_such_module_" + "zz\n", 1
        )
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        proc = subprocess.run(
            [sys.executable, os.path.join(copy, "check_vocabulary.py"), "--root", self.root],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        out = proc.stdout.decode("utf-8")
        self.assertEqual(proc.returncode, 2, out)
        self.assertTrue(out.startswith("vocabulary: DEAD ModuleNotFoundError"), out)
        self.assertNotIn("Traceback", proc.stderr.decode("utf-8"))

    def test_non_utf8_bytes_are_replaced_not_fatal(self):
        make_repo(self.root, {})
        with open(os.path.join(self.root, "raw.bin"), "wb") as handle:
            handle.write(b"\xff\xfe plain\n")
        code, lines = check_vocabulary.run(self.root)
        self.assertEqual(code, 0, lines)


class RawCommitTests(Base):
    def test_a_nul_byte_in_a_commit_is_dead(self):
        make_repo(self.root, {"a.txt": "x\n"})
        body = b"fine\0more " + ("foo" + "-" + W).encode("ascii") + b"\n"
        cid = raw_commit(self.root, b"", body)
        code, lines = check_vocabulary.run(self.root)
        self.assertEqual(code, 2, lines)
        self.assertEqual(lines, ["vocabulary: DEAD commit %s holds a NUL byte" % cid[:7]])

    def test_the_nul_byte_reason_names_the_commit_by_its_percent_h(self):
        make_repo(self.root, {"a.txt": "x\n"})
        git(self.root, "config", "core.abbrev", "12")
        cid = raw_commit(self.root, b"", b"fine\0more\n")
        short = out(self.root, "log", "-1", "--format=%h")
        self.assertEqual(len(short), 12)
        code, lines = check_vocabulary.run(self.root)
        self.assertEqual(code, 2, lines)
        self.assertEqual(lines, ["vocabulary: DEAD commit %s holds a NUL byte" % short])
        self.assertTrue(cid.startswith(short))

    def test_a_declared_non_utf8_encoding_is_dead(self):
        make_repo(self.root, {"a.txt": "x\n"})
        cid = raw_commit(self.root, b"encoding ISO-8859-1\n", b"caf\xe9\n")
        code, lines = check_vocabulary.run(self.root)
        self.assertEqual(code, 2, lines)
        self.assertEqual(
            lines, ["vocabulary: DEAD commit %s declares encoding ISO-8859-1" % cid[:7]]
        )

    def test_a_declared_utf8_encoding_in_any_case_is_read(self):
        make_repo(self.root, {"a.txt": "x\n"})
        raw_commit(self.root, b"encoding utf-8\n", "caf\u00e9\n".encode("utf-8"))
        self.assertClean(check_vocabulary.run(self.root))

    def test_a_body_that_is_not_valid_utf8_is_dead(self):
        make_repo(self.root, {"a.txt": "x\n"})
        cid = raw_commit(self.root, b"", b"caf\xe9\n")
        code, lines = check_vocabulary.run(self.root)
        self.assertEqual(code, 2, lines)
        self.assertTrue(lines[0].startswith("vocabulary: DEAD commit %s " % cid[:7]), lines)

    def test_the_message_is_what_follows_the_header(self):
        make_repo(self.root, {"a.txt": "x\n"})
        raw_commit(self.root, b"", b"Subject\n\nBody.\n")
        self.assertEqual(repofiles.commit_messages(self.root), ["Subject\n\nBody.\n"])


class HistoryShapeTests(Base):
    def test_a_merged_in_side_commit_is_scanned(self):
        # Passes today by design: it pins that history is not first-parent only.
        make_repo(self.root, {"a.txt": "x\n"}, messages=["Base"])
        git(self.root, "checkout", "-q", "-b", "side")
        git(self.root, "commit", "-q", "--allow-empty", "-m", "Add foo" + "-" + W)
        side = out(self.root, "rev-parse", "--short", "HEAD")
        git(self.root, "checkout", "-q", "-")
        git(self.root, "commit", "-q", "--allow-empty", "-m", "Main work")
        git(self.root, "merge", "-q", "--no-ff", "-m", "Merge side", "side")
        code, lines = check_vocabulary.run(self.root)
        self.assertEqual(code, 1, lines)
        self.assertTrue(
            any(l.startswith("vocabulary: commit %s: V2 " % side) for l in lines), lines
        )


class GitEnvironmentTests(Base):
    def test_a_git_dir_in_the_environment_does_not_redirect_the_scan(self):
        make_repo(self.root, {"a.txt": "x\n"}, messages=["Add foo" + "-" + W])
        other = os.path.join(self.tmp.name, "other")
        os.makedirs(other)
        make_repo(other, {"b.txt": "y\n"}, messages=["Clean"])
        with mock.patch.dict(os.environ, {"GIT_DIR": os.path.join(other, ".git")}):
            code, lines = check_vocabulary.run(self.root)
        self.assertEqual(code, 1, lines)
        self.assertTrue(any(": V2 " in l and "commit " in l for l in lines), lines)

    def test_every_git_call_drops_the_redirecting_variables(self):
        make_repo(self.root, {"a.txt": "x\n"}, messages=["One"])
        seen = []
        real = subprocess.run

        def spy(cmd, *args, **kwargs):
            seen.append((cmd, kwargs.get("env")))
            return real(cmd, *args, **kwargs)

        planted = {"GIT_DIR": "/nowhere", "GIT_WORK_TREE": "/nowhere", "GIT_INDEX_FILE": "/nowhere"}
        with mock.patch.dict(os.environ, planted):
            with mock.patch.object(repofiles.subprocess, "run", spy):
                repofiles.file_set(self.root)
                repofiles.is_shallow(self.root)
                repofiles.commit_messages(self.root)
                repofiles.commit_short_ids(self.root)
                repofiles.commit_identities(self.root)
        self.assertTrue(seen)
        for cmd, env in seen:
            with self.subTest(cmd=cmd):
                self.assertEqual(cmd[:2], ["git", "--no-replace-objects"])
                self.assertIsNotNone(env)
                for key in planted:
                    self.assertNotIn(key, env)

    def test_a_replace_entry_does_not_hide_a_commit(self):
        make_repo(self.root, {"a.txt": "x\n"}, messages=["Clean one", "Clean two"])
        clean = out(self.root, "rev-parse", "HEAD")
        git(self.root, "commit", "-q", "--allow-empty", "-m", "Add foo" + "-" + W)
        bad = out(self.root, "rev-parse", "HEAD")
        git(self.root, "replace", bad, clean)
        code, lines = check_vocabulary.run(self.root)
        self.assertEqual(code, 1, lines)
        self.assertTrue(
            any(l.startswith("vocabulary: commit %s: V2 " % bad[:7]) for l in lines), lines
        )

    def graft_repo(self):
        """Three commits; returns the newest commit's full id."""
        make_repo(self.root, {"a.txt": "x\n"}, messages=["One", "Two", "Three"])
        return out(self.root, "rev-parse", "HEAD")

    def assert_graft_is_live_but_harmless(self, newest):
        # Control: plain git, with the same environment, sees only one commit.
        self.assertEqual(out(self.root, "rev-list", "HEAD").split(), [newest])
        self.assertEqual(len(repofiles.commit_short_ids(self.root)), 3)
        self.assertEqual(len(repofiles.commit_messages(self.root)), 3)

    def test_f1_env_a_graft_file_named_in_the_environment_does_not_hide_history(self):
        newest = self.graft_repo()
        graft = os.path.join(self.tmp.name, "grafts")
        with open(graft, "w", encoding="utf-8") as handle:
            handle.write(newest + "\n")
        with mock.patch.dict(os.environ, {"GIT_GRAFT_FILE": graft}):
            self.assert_graft_is_live_but_harmless(newest)

    def test_f1_file_the_repositorys_own_graft_file_does_not_hide_history(self):
        newest = self.graft_repo()
        graft = os.path.join(self.root, ".git", "info", "grafts")
        os.makedirs(os.path.dirname(graft), exist_ok=True)
        with open(graft, "w", encoding="utf-8") as handle:
            handle.write(newest + "\n")
        self.assert_graft_is_live_but_harmless(newest)


class GitConfigRedirectTests(Base):
    """Config variables that set core.excludesFile must not hide a file."""

    def planted(self):
        make_repo(self.root, {"a.txt": "x\n"}, messages=["One"])
        write(self.root, "hidden.txt", "y\n")
        ignore = os.path.join(self.tmp.name, "ignore")
        with open(ignore, "w", encoding="utf-8") as handle:
            handle.write("hidden.txt\n")
        return ignore

    def assertStillListed(self, extra):
        # Control: git itself, given these variables, does hide the file.
        # The control is isolated from the user's own git config, so it
        # measures only the variable under test.
        env = {
            k: v
            for k, v in os.environ.items()
            if k
            not in (
                "GIT_CONFIG_GLOBAL",
                "GIT_CONFIG_NOSYSTEM",
                "GIT_CONFIG_PARAMETERS",
                "GIT_CONFIG_COUNT",
            )
            and not k.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))
        }
        env["HOME"] = tempfile.mkdtemp(dir=self.tmp.name)
        env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(dir=self.tmp.name)
        env.update(extra)
        raw = subprocess.run(
            ["git", "-C", self.root, "ls-files", "--others", "--exclude-standard"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            env=env,
        ).stdout
        self.assertNotIn(b"hidden.txt", raw)
        with mock.patch.dict(os.environ, extra):
            self.assertIn("hidden.txt", repofiles.file_set(self.root))

    def test_a_global_config_file_does_not_hide_a_file(self):
        ignore = self.planted()
        config = os.path.join(self.tmp.name, "gitconfig")
        with open(config, "w", encoding="utf-8") as handle:
            handle.write("[core]\n\texcludesFile = %s\n" % ignore)
        self.assertStillListed({"GIT_CONFIG_GLOBAL": config})

    def test_a_system_config_file_does_not_hide_a_file(self):
        ignore = self.planted()
        config = os.path.join(self.tmp.name, "gitconfig")
        with open(config, "w", encoding="utf-8") as handle:
            handle.write("[core]\n\texcludesFile = %s\n" % ignore)
        self.assertStillListed({"GIT_CONFIG_SYSTEM": config})

    def test_config_parameters_do_not_hide_a_file(self):
        ignore = self.planted()
        self.assertStillListed(
            {"GIT_CONFIG_PARAMETERS": "'core.excludesfile'='%s'" % ignore}
        )

    def test_a_config_count_with_key_and_value_does_not_hide_a_file(self):
        ignore = self.planted()
        self.assertStillListed(
            {
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "core.excludesFile",
                "GIT_CONFIG_VALUE_0": ignore,
            }
        )

    def test_every_numbered_key_and_value_is_dropped(self):
        planted = {
            "GIT_CONFIG_COUNT": "20",
            "GIT_CONFIG_KEY_0": "a.b",
            "GIT_CONFIG_VALUE_0": "c",
            "GIT_CONFIG_KEY_19": "a.b",
            "GIT_CONFIG_VALUE_19": "c",
            "GIT_CONFIG_KEYS": "kept",
        }
        with mock.patch.dict(os.environ, planted):
            env = repofiles._env()
        self.assertEqual(
            {k for k in env if k.startswith("GIT_CONFIG")},
            {"GIT_CONFIG_GLOBAL", "GIT_CONFIG_KEYS", "GIT_CONFIG_NOSYSTEM"},
        )


class AmbientGitConfigTests(Base):
    """The user's own git config and default ignore file must not hide a file."""

    AMBIENT = ("XDG_CONFIG_HOME", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
               "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT")

    def planted(self):
        make_repo(self.root, {"a.txt": "x\n"}, messages=["One"])
        write(self.root, "planted.txt", "y\n")

    def ignore_file(self, *parts):
        path = os.path.join(self.tmp.name, *parts)
        write(self.tmp.name, "/".join(parts), "planted.txt\n")
        return path

    def assertRouteLiveThenListed(self, env):
        """Control: plain git, run with env, hides the file; file_set does not."""
        with mock.patch.dict(os.environ, env):
            for key in self.AMBIENT:
                if key not in env:
                    os.environ.pop(key, None)
            raw = subprocess.run(
                ["git", "-C", self.root, "ls-files", "--others", "--exclude-standard"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                env=dict(os.environ),
            ).stdout
            self.assertNotIn(b"planted.txt", raw)
            self.assertIn("planted.txt", repofiles.file_set(self.root))

    def test_a1_a_global_gitconfig_in_home_does_not_hide_a_file(self):
        self.planted()
        home = os.path.join(self.tmp.name, "profile")
        os.makedirs(home)
        ignore = self.ignore_file("ignore-a1")
        write(home, ".gitconfig", "[core]\n\texcludesFile = %s\n" % ignore)
        self.assertRouteLiveThenListed({"HOME": home})

    def test_a2_the_default_ignore_file_under_home_does_not_hide_a_file(self):
        self.planted()
        home = os.path.join(self.tmp.name, "profile")
        write(home, ".config/git/ignore", "planted.txt\n")
        self.assertRouteLiveThenListed({"HOME": home})

    def test_a3_the_default_ignore_file_under_xdg_does_not_hide_a_file(self):
        self.planted()
        xdg = os.path.join(self.tmp.name, "xdg")
        write(xdg, "git/ignore", "planted.txt\n")
        home = os.path.join(self.tmp.name, "profile")
        os.makedirs(home)
        self.assertRouteLiveThenListed({"HOME": home, "XDG_CONFIG_HOME": xdg})

    def test_a4_the_environment_pins_config_and_points_xdg_at_an_empty_directory(self):
        planted = {
            "GIT_CONFIG_GLOBAL": "/nowhere",
            "XDG_CONFIG_HOME": "/nowhere",
            "GIT_CONFIG_NOSYSTEM": "0",
        }
        with mock.patch.dict(os.environ, planted):
            env = repofiles._env()
        self.assertEqual(env["GIT_CONFIG_GLOBAL"], os.devnull)
        self.assertEqual(env["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertTrue(os.path.isdir(env["XDG_CONFIG_HOME"]))
        self.assertEqual(os.listdir(env["XDG_CONFIG_HOME"]), [])
        self.assertNotEqual(env["XDG_CONFIG_HOME"], "/nowhere")


class LengthGuardTests(Base):
    def test_more_messages_than_short_ids_is_dead(self):
        # Passes today by design: a pin.
        make_repo(self.root, {"a.txt": "x\n"}, messages=["One"])
        with mock.patch.object(repofiles, "commit_messages", lambda _r: ["a", "b"]):
            code, lines = check_vocabulary.run(self.root)
        self.assertEqual(code, 2)
        self.assertEqual(lines, ["vocabulary: DEAD the commit list changed while it was read"])

    def test_fewer_identities_than_short_ids_is_dead(self):
        # Passes today by design: a pin.
        make_repo(self.root, {"a.txt": "x\n"}, messages=["One"])
        with mock.patch.object(repofiles, "commit_identities", lambda _r: []):
            code, lines = check_vocabulary.run(self.root)
        self.assertEqual(code, 2)
        self.assertEqual(lines, ["vocabulary: DEAD the commit list changed while it was read"])

    def test_an_identity_record_of_the_wrong_size_is_dead(self):
        # Passes today by design: a pin.
        make_repo(self.root, {"a.txt": "x\n"}, messages=["One"])
        junk = b"a\0b\0c\0d\0e\0f\0"
        with mock.patch.object(repofiles, "_history", lambda _r, _f: junk):
            with self.assertRaises(repofiles.RepoFilesError):
                repofiles.commit_identities(self.root)
            code, lines = check_vocabulary.run(self.root)
        self.assertEqual(code, 2)
        self.assertEqual(
            lines, ["vocabulary: DEAD git log returned a malformed identity list"]
        )


class RealRepoTests(unittest.TestCase):
    def test_the_real_repo_passes(self):
        code, lines = check_vocabulary.run(REAL_ROOT)
        self.assertEqual(code, 0, lines)
        self.assertRegex(lines[-1], r"^vocabulary: ok \(\d+ files, \d+ commit messages\)$")

    def test_the_real_repo_exits_zero_from_the_command_line(self):
        proc = subprocess.run(
            [sys.executable, SCRIPT],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout)


if __name__ == "__main__":
    unittest.main()
