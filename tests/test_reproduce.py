import io
import os
import sys
import tempfile
import unittest
from unittest import mock

import check_admission
import reproduce

FIRST = "EXP-" + "0001"
SECOND = "EXP-" + "0002"
MISSING = "EXP-" + "0009"
PROV = "Claude Code (test), 2026-10-22"
LEDGER = "docs/admissions.md"
GOOD = b"seed=7\n"


def generator_text(content_expr, relpath):
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


def ledger_text(rows):
    lines = [
        "# Admissions",
        "",
        "| path | class | basis | provenance |",
        "| --- | --- | --- | --- |",
    ]
    for path, cls, basis in rows:
        lines.append("| %s | %s | %s | %s |" % (path, cls, basis, PROV))
    return "\n".join(lines) + "\n"


def fixture_path(experiment):
    return "experiments/%s/data/f.txt" % experiment


def fixture_row(experiment, basis="generator=gen.py seed=7"):
    return (fixture_path(experiment), "fixture", basis)


def snapshot(root):
    found = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames:
            found[os.path.relpath(os.path.join(dirpath, name), root)] = None
        for name in filenames:
            full = os.path.join(dirpath, name)
            with open(full, "rb") as handle:
                found[os.path.relpath(full, root)] = handle.read()
    return found


class ReproduceCase(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = holder.name

    def write(self, path, data):
        full = os.path.join(self.root, *path.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        mode = "wb" if isinstance(data, bytes) else "w"
        with open(full, mode) as handle:
            handle.write(data)

    def build(self, experiments=(FIRST,), committed=None, rows=None, generator=None):
        """One generator for every experiment's fixture; committed maps id to bytes."""
        committed = committed or {}
        for name in experiments:
            self.write(fixture_path(name), committed.get(name, GOOD))
        if generator is None:
            generator = generator_text("b'seed=7\\n'", fixture_path(FIRST))
        self.write("gen.py", generator)
        if rows is None:
            rows = [("gen.py", "original", "-")] + [fixture_row(name) for name in experiments]
        self.write(LEDGER, ledger_text(rows))

    def run_main(self, *args):
        out = io.StringIO()
        with mock.patch.object(sys, "stdout", out):
            code = reproduce.main(list(args) + ["--root", self.root])
        return code, out.getvalue()


class SingleExperimentTests(ReproduceCase):
    def test_identical(self):
        self.build()
        code, out = self.run_main(FIRST)
        self.assertEqual(code, 0)
        self.assertEqual(out.count("rebuilt, identical"), 1)
        self.assertIn("reproduce: %s: 1 rebuilt, 1 identical, 0 differ" % FIRST, out)

    def test_flipped_byte_differs(self):
        self.build(committed={FIRST: b"seed=8\n"})
        code, out = self.run_main(FIRST)
        self.assertEqual(code, 1)
        self.assertIn(
            "differs (committed file differs from the generator output)", out
        )
        self.assertIn("1 rebuilt, 0 identical, 1 differ", out)

    def test_generator_failure_differs(self):
        self.build(generator="import sys\nsys.exit(3)\n")
        code, out = self.run_main(FIRST)
        self.assertEqual(code, 1)
        self.assertIn("differs (generator exited 3", out)

    def test_unparsable_basis_differs_not_dead(self):
        self.build(rows=[fixture_row(FIRST, basis="gen.py")])
        code, out = self.run_main(FIRST)
        self.assertEqual(code, 1)
        self.assertIn("%s: differs (" % fixture_path(FIRST), out)
        self.assertNotIn("DEAD", out)

    def test_missing_committed_file_differs_not_dead(self):
        self.build()
        os.remove(os.path.join(self.root, *fixture_path(FIRST).split("/")))
        code, out = self.run_main(FIRST)
        self.assertEqual(code, 1)
        self.assertIn("differs (the committed file is missing", out)
        self.assertNotIn("DEAD", out)

    def test_no_fixture_rows(self):
        self.build(rows=[])
        code, out = self.run_main(FIRST)
        self.assertEqual(code, 0)
        self.assertIn("reproduce: %s has no generated files" % FIRST, out)

    def test_unknown_experiment(self):
        self.build()
        code, out = self.run_main(MISSING)
        self.assertEqual(code, 1)
        self.assertIn(
            "reproduce: no experiment %s; the experiments are the folders under experiments/"
            % MISSING,
            out,
        )

    def test_malformed_id(self):
        self.build()
        code, out = self.run_main("exp-1")
        self.assertEqual(code, 1)
        self.assertIn("reproduce: no experiment exp-1;", out)

    def test_malformed_ledger(self):
        self.build()
        self.write(LEDGER, "no table here\n")
        code, out = self.run_main(FIRST)
        self.assertEqual(code, 1)
        self.assertIn("reproduce: docs/admissions.md cannot be read: ", out)
        self.assertIn("run tools/gate.py to see every finding", out)

    def test_other_experiments_rows_are_not_rebuilt(self):
        self.build(
            experiments=(FIRST, SECOND), committed={SECOND: b"wrong\n"}
        )
        code, out = self.run_main(FIRST)
        self.assertEqual(code, 0)
        self.assertNotIn(SECOND, out)


class AllTests(ReproduceCase):
    def test_no_experiments_folder(self):
        self.write(LEDGER, ledger_text([]))
        code, out = self.run_main("--all")
        self.assertEqual(code, 0)
        self.assertIn("reproduce: no experiments", out)

    def test_empty_experiments_folder(self):
        self.write(LEDGER, ledger_text([]))
        os.makedirs(os.path.join(self.root, "experiments"))
        code, out = self.run_main("--all")
        self.assertEqual(code, 0)
        self.assertIn("reproduce: no experiments", out)

    def test_second_differs_both_processed(self):
        generator = (
            generator_text("b'seed=7\\n'", fixture_path(FIRST))
            + "target = os.path.join(args.out, %r)\n" % fixture_path(SECOND)
            + "os.makedirs(os.path.dirname(target), exist_ok=True)\n"
            + "with open(target, 'wb') as handle:\n"
            + "    handle.write(b'seed=7\\n')\n"
        )
        self.build(
            experiments=(FIRST, SECOND),
            committed={SECOND: b"wrong\n"},
            generator=generator,
        )
        code, out = self.run_main("--all")
        self.assertEqual(code, 1)
        self.assertIn("reproduce: %s: 1 rebuilt, 1 identical, 0 differ" % FIRST, out)
        self.assertIn("reproduce: %s: 1 rebuilt, 0 identical, 1 differ" % SECOND, out)

    def test_malformed_ledger_reported_once(self):
        self.build(experiments=(FIRST, SECOND))
        self.write(LEDGER, "no table here\n")
        code, out = self.run_main("--all")
        self.assertEqual(code, 1)
        self.assertEqual(out.count("cannot be read"), 1)


class DeadAndUsageTests(ReproduceCase):
    def test_dead(self):
        self.build()
        with mock.patch.object(
            check_admission, "run_generator", side_effect=RuntimeError("boom")
        ):
            code, out = self.run_main(FIRST)
        self.assertEqual(code, 2)
        self.assertIn("reproduce: DEAD RuntimeError: boom", out)

    def test_import_failure_is_dead(self):
        with mock.patch.object(reproduce, "IMPORT_ERROR", ImportError("gone")):
            code, out = self.run_main("--all")
        self.assertEqual(code, 2)
        self.assertIn("reproduce: DEAD ImportError: gone", out)

    def usage(self, *args):
        with mock.patch.object(sys, "stderr", io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                reproduce.main(list(args))
        self.assertEqual(caught.exception.code, 2)

    def test_no_argument(self):
        self.usage()

    def test_id_and_all(self):
        self.usage(FIRST, "--all")


class NoWriteTests(ReproduceCase):
    def test_identical_run_writes_nothing(self):
        self.build()
        before = snapshot(self.root)
        self.assertEqual(self.run_main(FIRST)[0], 0)
        self.assertEqual(snapshot(self.root), before)

    def test_differing_run_writes_nothing(self):
        self.build(committed={FIRST: b"seed=8\n"})
        before = snapshot(self.root)
        self.assertEqual(self.run_main(FIRST)[0], 1)
        self.assertEqual(snapshot(self.root), before)


class RefusalCase(unittest.TestCase):
    """A throwaway repository one level below a parent that holds the marker."""

    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.parent = holder.name
        self.root = os.path.join(self.parent, "repo")
        os.makedirs(self.root)
        self.marker = os.path.join(self.parent, "ran.marker")

    def write(self, path, data, base=None):
        full = os.path.join(base or self.root, *path.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb" if isinstance(data, bytes) else "w") as handle:
            handle.write(data)
        return full

    def generator(self, relpath=None):
        """A generator that writes the right bytes and also a marker when it runs."""
        body = generator_text("b'seed=7\\n'", relpath or fixture_path(FIRST))
        return "open(%r, 'w').close()\n" % self.marker + body

    def ledger(self, rows):
        self.write(LEDGER, ledger_text(rows))

    def run_main(self, *args):
        out = io.StringIO()
        with mock.patch.object(sys, "stdout", out):
            code = reproduce.main(list(args) + ["--root", self.root])
        return code, out.getvalue()

    def assertRefused(self, args, reason, path=None):
        code, out = self.run_main(*args)
        self.assertEqual(code, 1, out)
        self.assertIn("%s: differs (%s)" % (path or fixture_path(FIRST), reason), out)
        self.assertFalse(os.path.exists(self.marker), "the generator ran")
        return out

    def plain(self):
        """The good layout: gen.py with an original row, one fixture."""
        self.write("gen.py", self.generator())
        self.write(fixture_path(FIRST), GOOD)
        self.ledger([("gen.py", "original", "-"), fixture_row(FIRST)])


NOT_NORMAL = "generator or fixture path is not a normal path inside the repository"


class RefusedRowTests(RefusalCase):
    def test_the_good_layout_runs_the_generator(self):
        self.plain()
        code, out = self.run_main(FIRST)
        self.assertEqual(code, 0, out)
        self.assertTrue(os.path.exists(self.marker))

    def test_dot_slash_generator(self):
        self.write("gen.py", self.generator())
        self.write(fixture_path(FIRST), GOOD)
        self.ledger([("gen.py", "original", "-"), fixture_row(FIRST, "generator=./gen.py seed=7")])
        self.assertRefused([FIRST], NOT_NORMAL)

    def test_parent_directory_generator(self):
        self.write("outside.py", self.generator(), base=self.parent)
        self.write(fixture_path(FIRST), GOOD)
        self.ledger([fixture_row(FIRST, "generator=../outside.py seed=7")])
        self.assertRefused([FIRST], NOT_NORMAL)

    def test_absolute_generator(self):
        full = self.write("outside.py", self.generator(), base=self.parent)
        self.write(fixture_path(FIRST), GOOD)
        self.ledger([fixture_row(FIRST, "generator=%s seed=7" % full)])
        self.assertRefused([FIRST], NOT_NORMAL)

    def test_symlinked_generator(self):
        self.write("real.py", self.generator())
        os.symlink("real.py", os.path.join(self.root, "gen.py"))
        self.write(fixture_path(FIRST), GOOD)
        self.ledger(
            [("real.py", "original", "-"), ("gen.py", "original", "-"), fixture_row(FIRST)]
        )
        self.assertRefused([FIRST], "gen.py is a symlink or not a regular file")

    def test_generator_that_is_not_a_regular_file(self):
        os.makedirs(os.path.join(self.root, "gen.py"))
        self.write(fixture_path(FIRST), GOOD)
        self.ledger([("gen.py", "original", "-"), fixture_row(FIRST)])
        self.assertRefused([FIRST], "gen.py is a symlink or not a regular file")

    def test_symlinked_fixture(self):
        self.write("gen.py", self.generator())
        self.write("elsewhere.txt", GOOD)
        os.makedirs(os.path.join(self.root, "experiments", FIRST, "data"))
        os.symlink(
            os.path.join(self.root, "elsewhere.txt"),
            os.path.join(self.root, *fixture_path(FIRST).split("/")),
        )
        self.ledger([("gen.py", "original", "-"), fixture_row(FIRST)])
        self.assertRefused([FIRST], "%s is a symlink or not a regular file" % fixture_path(FIRST))

    def test_fixture_under_a_symlinked_folder(self):
        self.write("gen.py", self.generator())
        self.write("realdata/f.txt", GOOD)
        os.makedirs(os.path.join(self.root, "experiments", FIRST))
        os.symlink(
            os.path.join(self.root, "realdata"),
            os.path.join(self.root, "experiments", FIRST, "data"),
        )
        self.ledger([("gen.py", "original", "-"), fixture_row(FIRST)])
        self.assertRefused([FIRST], "%s is a symlink or not a regular file" % fixture_path(FIRST))

    def test_generator_under_a_symlinked_folder(self):
        self.write("realtools/gen.py", self.generator())
        os.symlink(os.path.join(self.root, "realtools"), os.path.join(self.root, "tools"))
        self.write(fixture_path(FIRST), GOOD)
        self.ledger(
            [("tools/gen.py", "original", "-"), fixture_row(FIRST, "generator=tools/gen.py seed=7")]
        )
        self.assertRefused([FIRST], "tools/gen.py is a symlink or not a regular file")

    def test_fixture_path_that_leaves_its_experiment(self):
        escaping = "experiments/%s/../../escape.txt" % FIRST
        self.write("gen.py", self.generator())
        self.write("escape.txt", GOOD)
        os.makedirs(os.path.join(self.root, "experiments", FIRST))
        self.ledger([("gen.py", "original", "-"), (escaping, "fixture", "generator=gen.py seed=7")])
        self.assertRefused([FIRST], NOT_NORMAL, path=escaping)

    def test_fixture_outside_its_experiment_by_name(self):
        self.plain()
        row = {"path": fixture_path(SECOND), "class": "fixture", "basis": "generator=gen.py seed=7"}
        rows = [{"path": "gen.py", "class": "original"}, row]
        reason = reproduce.rebuild_row(self.root, row, FIRST, rows)
        self.assertEqual(reason, "fixture path is outside experiments/%s/" % FIRST)
        self.assertFalse(os.path.exists(self.marker))

    def test_generator_with_no_original_row(self):
        self.write("gen.py", self.generator())
        self.write(fixture_path(FIRST), GOOD)
        self.ledger([fixture_row(FIRST)])
        self.assertRefused([FIRST], "generator gen.py has no original row")

    def test_generator_whose_first_row_is_not_original(self):
        self.write("gen.py", self.generator())
        self.write(fixture_path(FIRST), GOOD)
        self.ledger([("gen.py", "sourced", "SRC-0001"), ("gen.py", "original", "-"), fixture_row(FIRST)])
        self.assertRefused([FIRST], "generator gen.py has no original row")

    def test_symlinked_experiment_folder_single(self):
        self.write("realexp/data/f.txt", GOOD)
        os.makedirs(os.path.join(self.root, "experiments"))
        os.symlink(
            os.path.join(self.root, "realexp"), os.path.join(self.root, "experiments", FIRST)
        )
        self.write("gen.py", self.generator())
        self.ledger([("gen.py", "original", "-"), fixture_row(FIRST)])
        code, out = self.run_main(FIRST)
        self.assertEqual(code, 1)
        self.assertIn(
            "reproduce: experiments/%s is a symlink; the experiments are real folders under experiments/"
            % FIRST,
            out,
        )
        self.assertFalse(os.path.exists(self.marker))

    def test_symlinked_experiment_folder_all(self):
        self.write("realexp/data/f.txt", GOOD)
        os.makedirs(os.path.join(self.root, "experiments"))
        os.symlink(
            os.path.join(self.root, "realexp"), os.path.join(self.root, "experiments", FIRST)
        )
        self.write("gen.py", self.generator())
        self.ledger([("gen.py", "original", "-"), fixture_row(FIRST)])
        code, out = self.run_main("--all")
        self.assertEqual(code, 1)
        self.assertIn(
            "reproduce: experiments/%s is a symlink; the experiments are real folders under experiments/"
            % FIRST,
            out,
        )
        self.assertFalse(os.path.exists(self.marker))


class RootAndSkippedRowTests(RefusalCase):
    def test_missing_root_with_all(self):
        out = io.StringIO()
        missing = os.path.join(self.parent, "nowhere")
        with mock.patch.object(sys, "stdout", out):
            code = reproduce.main(["--all", "--root", missing])
        self.assertEqual(code, 1)
        self.assertIn(
            "reproduce: %s has no docs/admissions.md; point --root at the repository root" % missing,
            out.getvalue(),
        )

    def test_root_without_a_ledger_single(self):
        os.makedirs(os.path.join(self.root, "experiments", FIRST))
        code, out = self.run_main(FIRST)
        self.assertEqual(code, 1)
        self.assertIn("has no docs/admissions.md; point --root at the repository root", out)

    def test_root_without_a_ledger_all(self):
        os.makedirs(os.path.join(self.root, "experiments", FIRST))
        code, out = self.run_main("--all")
        self.assertEqual(code, 1)
        self.assertIn("has no docs/admissions.md; point --root at the repository root", out)

    def test_all_reports_an_orphan_fixture_row(self):
        self.plain()
        orphan = fixture_path(SECOND)
        self.ledger([("gen.py", "original", "-"), fixture_row(FIRST), (orphan, "fixture", "generator=gen.py seed=7")])
        code, out = self.run_main("--all")
        self.assertEqual(code, 1)
        self.assertIn(
            "reproduce: %s: differs (its experiment folder experiments/%s/ is missing or not an experiment)"
            % (orphan, SECOND),
            out,
        )
        self.assertIn("reproduce: %s: 1 rebuilt, 1 identical, 0 differ" % FIRST, out)

    def test_all_reports_an_orphan_row_when_there_is_no_experiments_folder(self):
        self.write("gen.py", self.generator())
        self.ledger([("gen.py", "original", "-"), fixture_row(FIRST)])
        code, out = self.run_main("--all")
        self.assertEqual(code, 1)
        self.assertIn("is missing or not an experiment)", out)
        self.assertNotIn("no experiments", out)

    def test_all_reports_an_off_pattern_folder(self):
        self.plain()
        os.makedirs(os.path.join(self.root, "experiments", "scratch"))
        code, out = self.run_main("--all")
        self.assertEqual(code, 1)
        self.assertIn("reproduce: experiments/scratch is not an experiment folder (EXP-NNNN)", out)
        self.assertIn("reproduce: %s: 1 rebuilt, 1 identical, 0 differ" % FIRST, out)

    def test_all_reports_a_row_under_an_off_pattern_folder(self):
        self.write("gen.py", self.generator())
        self.write("experiments/scratch/data/f.txt", GOOD)
        self.ledger([("gen.py", "original", "-"), ("experiments/scratch/data/f.txt", "fixture", "generator=gen.py seed=7")])
        code, out = self.run_main("--all")
        self.assertEqual(code, 1)
        self.assertIn("experiments/scratch/ is missing or not an experiment)", out)
        self.assertFalse(os.path.exists(self.marker))


if __name__ == "__main__":
    unittest.main()
