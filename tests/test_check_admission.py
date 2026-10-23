import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import check_admission
from lib import header, paths, repofiles

REAL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REAL_ROOT, "tools", "check_admission.py")
HTTPS = "https" + "://"
MISSING_ID = "SRC-" + "0002"
PROV = "Claude Code (test), 2026-10-20"
LEDGER = "docs/admissions.md"

VOCAB = (
    "# Vocabulary\n\n## Source types\n\n| Value | Meaning |\n| --- | --- |\n"
    "| `standard-spec` | A standard. |\n"
)
VOCAB_CODE_REPO = VOCAB + "| `code-repo` | A repository. |\n"


def source_md(key_order=None, **over):
    values = {
        "commit": "b" * 40,
        "id": "SRC-0001",
        "url": HTTPS + "docs.python.org/3/",
        "publisher": "Python",
        "source_type": "standard-spec",
        "fetched": "2026-10-20",
        "sha256": "a" * 64,
        "snapshot": "no",
        "licence": "-",
        "licence_basis": "-",
        "notes": "none",
    }
    values.update(over)
    lines = ["# A source", ""]
    for key in (header.SOURCE_KEYS if key_order is None else key_order):
        if key in values and values[key] is not None:
            lines.append("%s: %s" % (key, values[key]))
    return "\n".join(lines) + "\n"


def generator_md(relpath, content_expr):
    return (
        "import argparse\nimport os\n\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--seed', type=int, required=True)\n"
        "parser.add_argument('--out', required=True)\n"
        "args = parser.parse_args()\n"
        "target = os.path.join(args.out, %r)\n"
        "os.makedirs(os.path.dirname(target), exist_ok=True)\n"
        "with open(target, 'wb') as handle:\n"
        "    handle.write(%s)\n" % (relpath, content_expr)
    )


def break_tools_copy(tmp):
    """Copy tools/ into tmp with a module-level import of a module that is absent."""
    copy = os.path.join(tmp, "tools")
    shutil.copytree(
        os.path.join(REAL_ROOT, "tools"), copy, ignore=shutil.ignore_patterns("__pycache__")
    )
    path = os.path.join(copy, "lib", "repofiles.py")
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    text = text.replace("import subprocess\n", "import subprocess\nimport no_such_module_" + "zz\n", 1)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return copy


def base_files():
    return {
        "docs/vocabulary.md": VOCAB,
        "sources/SRC-0001/source.md": source_md(),
        "notes.txt": "hello\n",
    }


def ledger_text(rows):
    lines = [
        "# Admissions",
        "",
        "Every file has one row.",
        "",
        "| path | class | basis | provenance |",
        "| --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"


def auto_rows(files, overrides=None):
    overrides = overrides or {}
    rows = []
    for path in sorted(set(files) | {LEDGER}):
        cls, basis = overrides.get(path, ("original", "-"))
        rows.append((path, cls, basis, PROV))
    return rows


def git_init(path):
    subprocess.run(
        ["git", "init", "-q", path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def write(root, path, data):
    full = os.path.join(root, *path.split("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    mode = "wb" if isinstance(data, bytes) else "w"
    kwargs = {} if isinstance(data, bytes) else {"encoding": "utf-8"}
    with open(full, mode, **kwargs) as handle:
        handle.write(data)


class RepoCase(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = holder.name
        git_init(self.root)

    def build(self, files=None, rows=None, overrides=None, extra_rows=()):
        files = dict(base_files() if files is None else files)
        if rows is None:
            rows = auto_rows(files, overrides)
        rows = list(rows) + list(extra_rows)
        files[LEDGER] = ledger_text(rows)
        for path, data in files.items():
            write(self.root, path, data)

    def result(self):
        code, lines = check_admission.run(self.root)
        return code, "\n".join(lines)

    def assertFinding(self, ident, text=""):
        code, out = self.result()
        self.assertEqual(code, 1, out)
        self.assertIn(ident, out)
        self.assertIn(text, out)

    def assertClean(self):
        code, out = self.result()
        self.assertEqual(code, 0, out)
        self.assertIn("admission: ok (", out)


class A0Tests(RepoCase):
    def test_regular_files_pass(self):
        self.build()
        self.assertClean()

    def test_symlink_cannot_be_classified(self):
        self.build()
        os.symlink("notes.txt", os.path.join(self.root, "link.txt"))
        self.assertFinding("A0 cannot classify", "link.txt")

    def test_zero_files_is_dead(self):
        code, out = self.result()
        self.assertEqual(code, 2)
        self.assertIn("admission: DEAD", out)


class A1Tests(RepoCase):
    def test_file_without_a_row_is_not_admitted(self):
        self.build()
        write(self.root, "stray.txt", "x\n")
        self.assertFinding("A1 not admitted", "stray.txt")

    def test_second_row_is_a_duplicate(self):
        self.build(extra_rows=[("notes.txt", "original", "-", PROV)])
        self.assertFinding("A1 duplicate row", "notes.txt")

    def test_one_row_each_passes(self):
        self.build()
        self.assertClean()

    def edit_ledger(self, edit):
        self.build()
        full = os.path.join(self.root, "docs", "admissions.md")
        with open(full, encoding="utf-8") as handle:
            text = handle.read()
        write(self.root, LEDGER, edit(text))

    def test_inexact_header_is_a_finding(self):
        self.edit_ledger(lambda t: t.replace("| path | class |", "| path | kind |", 1))
        self.assertFinding("A1 ledger table header is not exact")

    def test_two_tables_are_a_finding(self):
        self.edit_ledger(lambda t: t + "\nmore\n\n| a | b |\n| --- | --- |\n")
        self.assertFinding("exactly one table")

    def test_three_cell_row_is_malformed(self):
        self.edit_ledger(lambda t: t + "| odd.txt | original | - |\n")
        self.assertFinding("malformed ledger row")


class FileSetTests(unittest.TestCase):
    def test_tracked_kept_and_ignored_left_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            git_init(tmp)
            write(tmp, "kept.txt", "k\n")
            write(tmp, ".gitignore", "junk/\n")
            write(tmp, "junk/x.txt", "j\n")
            subprocess.run(
                ["git", "-C", tmp, "update-index", "--add", "kept.txt"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            self.assertEqual(repofiles.file_set(tmp), [".gitignore", "kept.txt"])


class A2Tests(RepoCase):
    def test_row_for_a_missing_file(self):
        self.build(extra_rows=[("ghost.txt", "original", "-", PROV)])
        self.assertFinding("A2 listed but missing", "ghost.txt")

    def escapes(self, bad):
        self.build(extra_rows=[(bad, "original", "-", PROV)])
        self.assertFinding("A2 escapes root", bad)

    def test_parent_segment_escapes_root(self):
        self.escapes("../outside.txt")

    def test_absolute_path_escapes_root(self):
        self.escapes("/abs.txt")

    def test_dot_segment_escapes_root(self):
        self.escapes("a/./b.txt")

    def test_empty_segment_escapes_root(self):
        self.escapes("a//b.txt")

    def test_exact_paths_pass(self):
        self.build()
        self.assertClean()


class A3Tests(RepoCase):
    def test_unknown_class(self):
        rows = [r if r[0] != "notes.txt" else ("notes.txt", "mystery", "-", PROV) for r in auto_rows(base_files())]
        self.build(rows=rows)
        self.assertFinding("A3 unknown class")

    def test_provenance_must_not_be_empty(self):
        rows = [r if r[0] != "notes.txt" else ("notes.txt", "original", "-", "") for r in auto_rows(base_files())]
        self.build(rows=rows)
        self.assertFinding("A3 provenance needs an ISO date")

    def test_provenance_needs_a_valid_date(self):
        rows = [r if r[0] != "notes.txt" else ("notes.txt", "original", "-", "Tool, 2026-13-45") for r in auto_rows(base_files())]
        self.build(rows=rows)
        self.assertFinding("A3 provenance needs an ISO date")

    def test_five_digit_year_is_not_a_date(self):
        rows = [r if r[0] != "notes.txt" else ("notes.txt", "original", "-", "Tool, 1" + "2026-10-20") for r in auto_rows(base_files())]
        self.build(rows=rows)
        self.assertFinding("A3 provenance needs an ISO date")

    def test_valid_class_and_date_pass(self):
        self.build()
        self.assertClean()


class A4Tests(RepoCase):
    def fixture_files(self, content):
        files = base_files()
        files["gen.py"] = generator_md("data/f.txt", "b'seed=7\\n'")
        files["data/f.txt"] = content
        return files

    def fixture_overrides(self):
        return {"data/f.txt": ("fixture", "generator=gen.py seed=7")}

    def test_sourced_with_a_real_source_passes(self):
        files = base_files()
        self.build(files, overrides={"notes.txt": ("sourced", "SRC-0001")})
        self.assertClean()

    def test_sourced_without_a_source_file_fails(self):
        self.build(overrides={"notes.txt": ("sourced", MISSING_ID)})
        self.assertFinding("A4 no sources/" + MISSING_ID + "/source.md")

    def test_fixture_identical_passes(self):
        self.build(self.fixture_files("seed=7\n"), overrides=self.fixture_overrides())
        self.assertClean()

    def test_fixture_drifted_fails(self):
        self.build(self.fixture_files("seed=8\n"), overrides=self.fixture_overrides())
        self.assertFinding("A4 committed file differs")

    def test_fixture_generator_must_be_original(self):
        overrides = self.fixture_overrides()
        overrides["gen.py"] = ("sourced", "SRC-0001")
        self.build(self.fixture_files("seed=7\n"), overrides=overrides)
        self.assertFinding("A4 generator gen.py has no original row")

    def test_fixture_generator_failure_fails(self):
        files = self.fixture_files("seed=7\n")
        files["gen.py"] = "import sys\nsys.exit(3)\n"
        self.build(files, overrides=self.fixture_overrides())
        self.assertFinding("A4 generator exited 3")

    def test_fixture_generator_exiting_two_fails(self):
        files = self.fixture_files("seed=7\n")
        files["gen.py"] = "import sys\nsys.exit(2)\n"
        self.build(files, overrides=self.fixture_overrides())
        self.assertFinding("A4 generator exited 2")

    def test_fixture_generator_timeout_fails(self):
        files = self.fixture_files("seed=7\n")
        files["gen.py"] = "import time\ntime.sleep(30)\n"
        self.build(files, overrides=self.fixture_overrides())
        with mock.patch.object(check_admission, "GENERATOR_TIMEOUT", 1):
            self.assertFinding("A4 generator timed out after 1 s")

    def test_fixture_basis_shape(self):
        overrides = {"data/f.txt": ("fixture", "gen.py")}
        self.build(self.fixture_files("seed=7\n"), overrides=overrides)
        self.assertFinding("A4 fixture basis must be")

    def test_original_with_a_basis_fails(self):
        self.build(overrides={"notes.txt": ("original", "SRC-0001")})
        self.assertFinding("A4 original basis must be '-'")

    def test_original_passes(self):
        self.build()
        self.assertClean()


def exp_id():
    return "EXP-" + "0001"


def rec_path(name="x.txt"):
    return "experiments/%s/recorded/%s" % (exp_id(), name)


def code_path(name="x.txt"):
    return "experiments/%s/code/%s" % (exp_id(), name)


def sha_basis(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return "sha256=" + hashlib.sha256(data).hexdigest()


class RecordedPathTests(unittest.TestCase):
    def test_is_recorded_path_agrees_with_is_normal_path(self):
        cases = [
            "../outside.txt",
            "/abs.txt",
            "a/./b.txt",
            "a//b.txt",
            "a/b.txt",
            "",
            "a/",
            rec_path(),
            "/" + rec_path(),
            rec_path("a/b/c.txt"),
            rec_path("../code/x.txt"),
            rec_path("./x.txt"),
            "experiments/%s/recorded//x.txt" % exp_id(),
            "experiments/%s/./recorded/x.txt" % exp_id(),
            "experiments/%s/recorded/x.txt\n" % exp_id(),
            "experiments/%s/recorded/" % exp_id(),
            "experiments/%s/recorded" % exp_id(),
            code_path(),
            "experiments/EXP-12/recorded/x.txt",
            "tools/experiments/%s/recorded/x.txt" % exp_id(),
        ]
        for path in cases:
            with self.subTest(path=path):
                expected = check_admission.is_normal_path(path) and (
                    paths.RECORDED_FILE.fullmatch(path) is not None
                )
                self.assertEqual(paths.is_recorded_path(path), expected)

    def test_is_recorded_path_true_and_false_cases(self):
        self.assertTrue(paths.is_recorded_path(rec_path()))
        self.assertTrue(paths.is_recorded_path(rec_path("runs/r1/out.json")))
        for path in (rec_path("../code/x.txt"), "/" + rec_path(), code_path(), "experiments/x/recorded/y"):
            with self.subTest(path=path):
                self.assertFalse(paths.is_recorded_path(path))

    def test_is_recorded_path_refuses_a_trailing_newline(self):
        self.assertFalse(paths.is_recorded_path(rec_path() + "\n"))


class RecordedClassTests(RepoCase):
    def files_with(self, data="result 1\n", name="x.txt"):
        files = base_files()
        files[rec_path(name)] = data
        return files

    def recorded(self, data="result 1\n", name="x.txt", basis=None):
        files = self.files_with(data, name)
        override = {rec_path(name): ("recorded", sha_basis(data) if basis is None else basis)}
        return files, override

    def test_a_valid_recorded_file_passes(self):
        files, over = self.recorded()
        self.build(files, overrides=over)
        self.assertClean()

    def test_a_flipped_byte_fails_the_hash(self):
        files, over = self.recorded()
        files[rec_path()] = "result 2\n"
        self.build(files, overrides=over)
        self.assertFinding("A4 recorded file does not match its sha256 basis: " + rec_path())

    def test_uppercase_hex_is_refused(self):
        files, over = self.recorded()
        upper = "sha256=" + hashlib.sha256(b"result 1\n").hexdigest().upper()
        over[rec_path()] = ("recorded", upper)
        self.build(files, overrides=over)
        self.assertFinding(
            "A4 recorded basis must be sha256= and 64 lowercase hex characters: " + rec_path()
        )

    def test_a_basis_without_the_prefix_is_refused(self):
        files, over = self.recorded()
        over[rec_path()] = ("recorded", hashlib.sha256(b"result 1\n").hexdigest())
        self.build(files, overrides=over)
        self.assertFinding(
            "A4 recorded basis must be sha256= and 64 lowercase hex characters: " + rec_path()
        )

    def test_a_recorded_row_outside_recorded_is_refused(self):
        files = base_files()
        files[code_path()] = "x\n"
        self.build(files, overrides={code_path(): ("recorded", sha_basis("x\n"))})
        self.assertFinding(
            "A4 a recorded row must sit under experiments/EXP-NNNN/recorded/: " + code_path()
        )

    def test_an_original_row_for_a_recorded_file_is_refused(self):
        files = self.files_with()
        self.build(files)
        self.assertFinding("A4 a file under recorded/ must have a recorded row: " + rec_path())

    def test_a_recorded_file_with_no_row_is_refused(self):
        files = self.files_with()
        rows = [r for r in auto_rows(files) if r[0] != rec_path()]
        self.build(files, rows=rows)
        code, out = self.result()
        self.assertEqual(code, 1, out)
        self.assertIn("A1 not admitted", out)
        self.assertIn("A4 a file under recorded/ must have a recorded row: " + rec_path(), out)

    def test_a_dotdot_path_in_a_recorded_row_gets_no_exemption(self):
        files = base_files()
        files[code_path()] = "x\n"
        sneaky = rec_path("../code/x.txt")
        rows = auto_rows(files) + [(sneaky, "recorded", sha_basis(files[code_path()]), PROV)]
        self.build(files, rows=rows)
        code, out = self.result()
        self.assertEqual(code, 1, out)
        self.assertIn("A2 escapes root: " + sneaky, out)
        self.assertIn("A4 a recorded row must sit under experiments/EXP-NNNN/recorded/: " + sneaky, out)

    def test_a_dotdot_row_cannot_exempt_the_real_file(self):
        files = base_files()
        bad = "see " + HTTPS + "unlisted.invalidhost.net/x\n"
        files[code_path()] = bad
        sneaky = rec_path("../code/x.txt")
        rows = auto_rows(files)
        rows.append((sneaky, "recorded", sha_basis(bad), PROV))
        self.build(files, rows=rows)
        self.assertFinding("A6 unregistered URL")

    def test_a_symlinked_recorded_folder_gives_no_exemption(self):
        files = base_files()
        self.build(files)
        outside = os.path.join(self.root, "elsewhere")
        write(self.root, "elsewhere/x.txt", "see " + HTTPS + "unlisted.invalidhost.net/x\n")
        link_parent = os.path.join(self.root, "experiments", exp_id())
        os.makedirs(link_parent)
        os.symlink(outside, os.path.join(link_parent, "recorded"))
        write(self.root, LEDGER, ledger_text(
            auto_rows(set(files) | {"elsewhere/x.txt", "experiments/%s/recorded" % exp_id()})
            + [(rec_path(), "recorded", sha_basis("see " + HTTPS + "unlisted.invalidhost.net/x\n"), PROV)]
        ))
        code, out = self.result()
        self.assertEqual(code, 1, out)
        self.assertIn("A0 cannot classify", out)
        self.assertIn("experiments/%s/recorded: A0" % exp_id(), out)
        self.assertIn("A2 listed but missing: " + rec_path(), out)
        self.assertIn("elsewhere/x.txt:1: A6 unregistered URL", out)

    URL_TEXT = "see " + HTTPS + "unlisted.invalidhost.net/x\n"

    def exemption(self, data, finding):
        """The same data passes as a recorded file, fails as an original file and
        as a recorded row outside recorded/."""
        files, over = self.recorded(data)
        self.build(files, overrides=over)
        self.assertClean()

        # The first case's recorded file stays on disk unless it is removed, and
        # its own finding would satisfy a loose check on the output.
        shutil.rmtree(os.path.join(self.root, "experiments"))

        files = base_files()
        files[code_path()] = data
        self.build(files)
        self.assertFindingAt(finding, code_path())

        files = base_files()
        files[code_path()] = data
        self.build(files, overrides={code_path(): ("recorded", sha_basis(data))})
        self.assertFindingAt(finding, code_path())

    def assertFindingAt(self, ident, path):
        """A finding line that starts at path and holds ident."""
        code, out = self.result()
        self.assertEqual(code, 1, out)
        prefix = "%s: %s" % (check_admission.CHECK, path)
        self.assertTrue(
            any(ln.startswith(prefix) and ident in ln for ln in out.splitlines()), out
        )

    def test_a6_exemption_pair(self):
        self.exemption(self.URL_TEXT, "A6 unregistered URL")

    def test_a7_exemption_pair(self):
        self.exemption(b"\xff\xfe\x00", "A7 not valid UTF-8")

    def test_a9_exemption_pair(self):
        self.exemption("x" * 100001, "A9")

    def test_a_generator_that_is_a_recorded_file_has_no_original_row(self):
        files = self.files_with("print(1)\n", "gen.py")
        files["data/f.txt"] = "seed=7\n"
        overrides = {
            rec_path("gen.py"): ("recorded", sha_basis("print(1)\n")),
            "data/f.txt": ("fixture", "generator=%s seed=7" % rec_path("gen.py")),
        }
        self.build(files, overrides=overrides)
        self.assertFinding("A4 generator %s has no original row" % rec_path("gen.py"))

    def test_recorded_is_a_known_class(self):
        self.assertIn("recorded", check_admission.CLASSES)


class A5Tests(RepoCase):
    def test_missing_source_id_fails(self):
        files = base_files()
        files["notes.txt"] = "see " + "SRC-" + "9999" + "\n"
        self.build(files)
        self.assertFinding("A5 names missing source " + "SRC-" + "9999")

    def test_five_digit_id_is_not_read_as_a_four_digit_one(self):
        # No source folder exists here, so a misread SRC-0001 would be missing.
        self.build({"notes.txt": "see " + "SRC-" + "00012" + "\n"})
        self.assertClean()

    def test_existing_source_id_passes(self):
        files = base_files()
        files["notes.txt"] = "see " + "SRC-" + "0001" + "\n"
        self.build(files)
        self.assertClean()


class A6Tests(RepoCase):
    def test_unregistered_url_fails(self):
        files = base_files()
        files["notes.txt"] = "see " + HTTPS + "docs.python.org/other\n"
        self.build(files)
        self.assertFinding("A6 unregistered URL")

    def test_upper_case_scheme_fails(self):
        files = base_files()
        files["notes.txt"] = "see " + "HTTPS" + "://" + "other.python.org/x\n"
        self.build(files)
        self.assertFinding("A6 unregistered URL")

    def test_mixed_case_scheme_fails(self):
        files = base_files()
        files["notes.txt"] = "see " + "Https" + "://" + "other.python.org/x\n"
        self.build(files)
        self.assertFinding("A6 unregistered URL")

    def test_registered_url_passes_even_with_trailing_punctuation(self):
        files = base_files()
        files["notes.txt"] = "see (" + HTTPS + "docs.python.org/3/).\nand " + HTTPS + "docs.python.org/3/,\n"
        self.build(files)
        self.assertClean()

    def test_reserved_hosts_pass(self):
        files = base_files()
        hosts = ["example.com", "a.example.org", "x.example", "y.test", "z.invalid", "h.localhost", "localhost:8080"]
        files["notes.txt"] = "\n".join(HTTPS + h + "/p" for h in hosts) + "\n"
        self.build(files)
        self.assertClean()

    def test_backslash_ends_the_host(self):
        files = base_files()
        files["notes.txt"] = HTTPS + "other.python.org" + "\\" + "@example.com/x\n"
        self.build(files)
        self.assertFinding("A6 unregistered URL")

    def test_userinfo_does_not_hide_the_host(self):
        files = base_files()
        files["notes.txt"] = HTTPS + "example.com@" + "other.python.org/x\n"
        self.build(files)
        self.assertFinding("A6 unregistered URL")

    def test_lookalike_host_fails(self):
        files = base_files()
        files["notes.txt"] = HTTPS + "notexample.com/p\n"
        self.build(files)
        self.assertFinding("A6 unregistered URL")


class A7Tests(RepoCase):
    def test_invalid_utf8_fails(self):
        files = base_files()
        files["blob.bin"] = b"\xff\xfe\x00"
        self.build(files)
        self.assertFinding("A7 not valid UTF-8", "blob.bin")

    def test_fixture_is_exempt(self):
        files = base_files()
        files["gen.py"] = generator_md("blob.bin", "b'\\xff\\xfe\\x00'")
        files["blob.bin"] = b"\xff\xfe\x00"
        self.build(files, overrides={"blob.bin": ("fixture", "generator=gen.py seed=1")})
        self.assertClean()


class A8Tests(RepoCase):
    def build_source(self, **over):
        files = base_files()
        files["sources/SRC-0001/source.md"] = source_md(**over)
        self.build(files)

    def test_valid_source_passes(self):
        self.build_source()
        self.assertClean()

    def test_missing_key_fails(self):
        self.build_source(notes=None)
        self.assertFinding("A8 keys must be exactly")

    def test_id_must_match_folder(self):
        self.build_source(id=MISSING_ID)
        self.assertFinding("A8 id must equal the folder name")

    def test_url_must_be_https(self):
        self.build_source(url="http" + "://docs.python.org/3/")
        self.assertFinding("A8 url must be https")

    def test_url_host_must_not_be_reserved(self):
        self.build_source(url=HTTPS + "example.com/x")
        self.assertFinding("A8 url must be https")

    def test_source_type_must_be_in_the_vocabulary(self):
        self.build_source(source_type="rumour")
        self.assertFinding("A8 unknown source_type")

    def test_source_type_listed_in_another_section_is_unknown(self):
        files = base_files()
        files["docs/vocabulary.md"] = VOCAB + "\n## Other\n\n| Value | Meaning |\n| --- | --- |\n| `rumour` | Not a type. |\n"
        files["sources/SRC-0001/source.md"] = source_md(source_type="rumour")
        self.build(files)
        self.assertFinding("A8 unknown source_type")

    def test_fetched_must_be_a_date(self):
        self.build_source(fetched="2026-02-30")
        self.assertFinding("A8 fetched must be an ISO date")

    def test_sha256_shape(self):
        self.build_source(sha256="A" * 64)
        self.assertFinding("A8 sha256")

    def test_snapshot_values(self):
        self.build_source(snapshot="maybe")
        self.assertFinding("A8 snapshot must be yes or no")

    def build_code_repo(self, key_order, **over):
        files = base_files()
        files["docs/vocabulary.md"] = VOCAB_CODE_REPO
        files["sources/SRC-0001/source.md"] = source_md(key_order=key_order, **over)
        self.build(files)

    def test_code_repo_record_with_commit_passes(self):
        self.build_code_repo(header.CODE_REPO_KEYS, source_type="code-repo")
        self.assertClean()

    def test_code_repo_record_without_commit_fails(self):
        self.build_code_repo(header.SOURCE_KEYS, source_type="code-repo")
        self.assertFinding("A8 keys must be exactly", "commit")

    def test_other_record_with_commit_fails(self):
        self.build_code_repo(header.CODE_REPO_KEYS, source_type="standard-spec")
        self.assertFinding("A8 keys must be exactly")


class A9Tests(RepoCase):
    def test_big_file_fails(self):
        files = base_files()
        files["big.txt"] = "x" * 100001
        self.build(files)
        self.assertFinding("A9", "big.txt")

    def test_file_at_the_limit_passes(self):
        files = base_files()
        files["big.txt"] = "x" * 100000
        self.build(files)
        self.assertClean()

    def test_fixture_is_exempt(self):
        files = base_files()
        files["gen.py"] = generator_md("big.txt", "b'x' * 100001")
        files["big.txt"] = "x" * 100001
        self.build(files, overrides={"big.txt": ("fixture", "generator=gen.py seed=1")})
        self.assertClean()


class A10Tests(RepoCase):
    def commit(self, message):
        subprocess.run(
            ["git", "-C", self.root, "add", "-A"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        )
        subprocess.run(
            ["git", "-C", self.root, "-c", "user.name=t", "-c", "user.email=t@example.com",
             "commit", "-q", "-m", message],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        )

    def test_ignored_raw_directory_on_disk_is_found(self):
        files = base_files()
        files[".gitignore"] = "_raw/\n"
        self.build(files)
        write(self.root, "_raw/x", "raw\n")
        self.assertFinding("A10 _raw path on disk", "_raw")

    def test_raw_path_in_history_is_found(self):
        files = base_files()
        files["_raw/x"] = "raw\n"
        self.build(files)
        self.commit("add")
        subprocess.run(
            ["git", "-C", self.root, "rm", "-q", "-r", "-f", "_raw"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        )
        self.build(base_files())
        self.commit("remove")
        self.assertFinding("A10 _raw path in history", "_raw/x")

    def test_upper_case_raw_segment_on_disk_is_found(self):
        files = base_files()
        files["a/_RAW/x"] = "raw\n"
        self.build(files)
        self.assertFinding("A10 _raw path on disk", "a/_RAW")

    def test_one_raw_directory_gives_one_disk_finding(self):
        files = base_files()
        files["_raw/x"] = "raw\n"
        files["_raw/y"] = "raw\n"
        self.build(files)
        code, out = self.result()
        self.assertEqual(out.count("A10 _raw path on disk"), 1, out)

    def test_near_misses_are_clean(self):
        files = base_files()
        files["raw/x"] = "a\n"
        files["_rawx/y"] = "a\n"
        files["x_raw/y"] = "a\n"
        self.build(files)
        self.assertClean()

    def test_raw_under_a_dot_directory_is_found(self):
        # One repo, two ignored _raw directories; each case needs its own line.
        files = base_files()
        files[".gitignore"] = ".cache/\na/.hidden/\n"
        self.build(files)
        write(self.root, ".cache/_raw/x", "raw\n")
        write(self.root, "a/.hidden/_raw/x", "raw\n")
        code, out = self.result()
        self.assertEqual(code, 1, out)
        lines = [ln for ln in out.splitlines() if "A10 _raw path on disk" in ln]
        for raw_dir in (".cache/_raw", "a/.hidden/_raw"):
            with self.subTest(raw_dir=raw_dir):
                self.assertEqual(len([ln for ln in lines if raw_dir in ln]), 1, out)

    def test_unreadable_directory_is_a_finding(self):
        # Root can read a chmod 000 directory, so the failure is injected.
        files = base_files()
        files[".gitignore"] = "u/\n"
        self.build(files)
        os.makedirs(os.path.join(self.root, "u"))
        real_walk = os.walk
        target = os.path.join(self.root, "u")

        def walk(top, *args, **kwargs):
            onerror = kwargs.get("onerror")
            self.assertIsNotNone(onerror)
            onerror(OSError(13, "Permission denied", target))
            return real_walk(top, *args, **kwargs)

        with mock.patch.object(check_admission.os, "walk", side_effect=walk):
            code, out = self.result()
        self.assertEqual(code, 1, out)
        self.assertIn(
            "admission: u: A10 cannot read this directory, so a _raw path inside it could be missed; make it readable",
            out.splitlines(),
        )

    def test_unreadable_directory_with_a_bytes_filename_is_reported_by_its_path(self):
        files = base_files()
        files[".gitignore"] = "u/\n"
        self.build(files)
        os.makedirs(os.path.join(self.root, "u"))
        real_walk = os.walk
        target = os.fsencode(os.path.join(self.root, "u"))

        def walk(top, *args, **kwargs):
            onerror = kwargs.get("onerror")
            self.assertIsNotNone(onerror)
            onerror(OSError(13, "Permission denied", target))
            return real_walk(top, *args, **kwargs)

        with mock.patch.object(check_admission.os, "walk", side_effect=walk):
            code, out = self.result()
        self.assertEqual(code, 1, out)
        self.assertIn(
            "admission: u: A10 cannot read this directory, so a _raw path inside it could be missed; make it readable",
            out.splitlines(),
        )

    def test_unreadable_directory_without_a_filename_is_reported_as_dot(self):
        files = base_files()
        self.build(files)
        real_walk = os.walk

        def walk(top, *args, **kwargs):
            onerror = kwargs.get("onerror")
            self.assertIsNotNone(onerror)
            onerror(OSError(13, "Permission denied"))
            return real_walk(top, *args, **kwargs)

        with mock.patch.object(check_admission.os, "walk", side_effect=walk):
            code, out = self.result()
        self.assertEqual(code, 1, out)
        self.assertIn(
            "admission: .: A10 cannot read this directory, so a _raw path inside it could be missed; make it readable",
            out.splitlines(),
        )

    def test_file_named_raw_is_found(self):
        files = base_files()
        files[".gitignore"] = "_raw\n"
        self.build(files)
        write(self.root, "a/_raw", "raw\n")
        self.assertFinding("A10 _raw path on disk", "a/_raw")

    def test_raw_inside_a_nested_git_directory_is_found(self):
        files = base_files()
        files[".gitignore"] = "n/\n"
        self.build(files)
        write(self.root, "n/.git/_raw/x", "raw\n")
        self.assertFinding("A10 _raw path on disk", "n/.git/_raw")

    def test_raw_segment_paths_helper(self):
        paths = ["x/_raw/y", "x/_Raw", "raw/y", "x_raw"]
        self.assertEqual(check_admission.raw_segment_paths(paths), paths[:2])


class ControlAndCliTests(RepoCase):
    def test_dead_control_gives_exit_two(self):
        self.build()
        with mock.patch.object(check_admission, "check_a1", return_value=[]):
            code, lines = check_admission.run(self.root)
        self.assertEqual(code, 2)
        self.assertIn("admission: DEAD", lines[0])

    def test_control_needs_the_raw_rule(self):
        self.build()
        with mock.patch.object(check_admission, "raw_segment_paths", return_value=[]):
            code, lines = check_admission.run(self.root)
        self.assertEqual(code, 2)
        self.assertIn("DEAD", "\n".join(lines))

    def test_non_checkout_gives_exit_two(self):
        with tempfile.TemporaryDirectory() as plain:
            proc = subprocess.run(
                [sys.executable, SCRIPT, "--root", plain],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn(b"admission: DEAD", proc.stdout)

    def test_exception_in_run_is_dead(self):
        with mock.patch.object(check_admission, "run", side_effect=RuntimeError("boom")):
            with mock.patch("builtins.print") as printed:
                code = check_admission.main(["--root", self.root])
        self.assertEqual(code, 2)
        printed.assert_called_with("admission: DEAD RuntimeError: boom")

    def test_broken_import_is_dead_when_run_directly(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = break_tools_copy(tmp)
            git_init(tmp)
            proc = subprocess.run(
                [sys.executable, os.path.join(copy, "check_admission.py"), "--root", tmp],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        out = proc.stdout.decode("utf-8")
        self.assertEqual(proc.returncode, 2, out)
        self.assertTrue(out.startswith("admission: DEAD ModuleNotFoundError"), out)
        self.assertNotIn("Traceback", proc.stderr.decode("utf-8"))

    def test_real_repo_passes(self):
        proc = subprocess.run(
            [sys.executable, SCRIPT],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        out = proc.stdout.decode("utf-8")
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("admission: ok (", out)

    def test_finding_line_shape(self):
        self.build()
        write(self.root, "stray.txt", "x\n")
        _code, out = self.result()
        self.assertIn("admission: stray.txt: A1 not admitted", out)
        self.assertIn("admission: 1 finding(s)", out)


if __name__ == "__main__":
    unittest.main()
