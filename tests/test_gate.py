import io
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import gate
from lib import repofiles

REAL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(REAL_ROOT, "tools", "gate.py")
WORKFLOW = os.path.join(REAL_ROOT, ".github", "workflows", "gate.yml")


def run_gate(*args):
    proc = subprocess.run(
        [sys.executable, GATE] + list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return proc.returncode, proc.stdout.decode("utf-8")


def git_init(path):
    subprocess.run(
        ["git", "init", "-q", path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


VOCAB_TEXT = (
    "# Vocabulary\n\n## Terms\n\n| Value | Meaning |\n| --- | --- |\n"
    + "| `" + "harn" + "ess` | The runtime. |\n"
)


def make_repo(tmp, files):
    """A temporary git repository whose every file is admitted, so the admission
    stage is clean and the exit code reflects the tests stage alone."""
    files = dict(files)
    files.setdefault("docs/vocabulary.md", VOCAB_TEXT)
    files["docs/admissions.md"] = None
    rows = ["| path | class | basis | provenance |", "| --- | --- | --- | --- |"]
    for path in sorted(files):
        rows.append("| %s | original | - | Claude Code (test), 2026-10-20 |" % path)
    files["docs/admissions.md"] = "# Admissions\n\n" + "\n".join(rows) + "\n"
    for path, text in files.items():
        full = os.path.join(tmp, *path.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as handle:
            handle.write(text)
    git_init(tmp)


def case_module(body):
    return "import unittest\n\n" + body


def read_workflow():
    with open(WORKFLOW, encoding="utf-8") as handle:
        return handle.read()


class GateRunTests(unittest.TestCase):
    def test_checks_only_on_the_real_repo(self):
        code, out = run_gate("--checks-only")
        self.assertEqual(code, 0, out)
        self.assertIn("tests: not run (--checks-only)", out)
        self.assertEqual(out.strip().split("\n")[-1], "gate: PASS (checks only)")

    def test_empty_tests_directory_is_dead(self):
        with tempfile.TemporaryDirectory() as tmp:
            for path in repofiles.file_set(REAL_ROOT):
                parts = path.split("/")
                if parts[0] == "tests" and parts[-1] != "__init__.py":
                    continue
                target = os.path.join(tmp, *parts)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copyfile(os.path.join(REAL_ROOT, *parts), target)
            git_init(tmp)
            code, out = run_gate("--root", tmp)
        self.assertEqual(code, 2, out)
        self.assertIn("tests: DEAD 0 tests", out)
        self.assertEqual(out.strip().split("\n")[-1], "gate: DEAD")

    def test_module_level_test_function_is_a_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "tests"))
            with open(os.path.join(tmp, "tests", "test_x.py"), "w", encoding="utf-8") as handle:
                handle.write("def test_x():\n    pass\n")
            git_init(tmp)
            code, out = run_gate("--root", tmp)
        self.assertEqual(code, 1, out)
        self.assertIn("tests: tests/test_x.py:1: module-level test function", out)
        self.assertEqual(out.strip().split("\n")[-1], "gate: FAIL")

    def test_failing_test_is_reported_by_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "tests"))
            with open(os.path.join(tmp, "tests", "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write("")
            body = (
                "import unittest\n\n"
                "class Boom(unittest.TestCase):\n"
                "    def test_fail(self):\n"
                "        self.assertEqual(1, 2)\n"
            )
            with open(os.path.join(tmp, "tests", "test_boom.py"), "w", encoding="utf-8") as handle:
                handle.write(body)
            git_init(tmp)
            code, out = run_gate("--root", tmp)
        self.assertEqual(code, 1, out)
        self.assertIn("tests: tests.test_boom.Boom.test_fail: FAIL", out)
        self.assertIn("tests: 1 finding(s)", out)


def last_line(out):
    return out.strip().split("\n")[-1]


class TestsStageTests(unittest.TestCase):
    def gate_on(self, modules):
        files = {"tests/__init__.py": ""}
        for name, text in modules.items():
            files["tests/" + name] = text
        with tempfile.TemporaryDirectory() as tmp:
            make_repo(tmp, files)
            return run_gate("--root", tmp)

    def test_clean_run_passes(self):
        body = case_module("class T(unittest.TestCase):\n    def test_a(self):\n        pass\n")
        code, out = self.gate_on({"test_x.py": body})
        self.assertEqual(code, 0, out)
        self.assertIn("tests: ok (1 tests)", out)
        self.assertEqual(last_line(out), "gate: PASS")

    def test_process_exit_zero_is_dead(self):
        body = case_module(
            "import os\n\nclass T(unittest.TestCase):\n"
            "    def test_a(self):\n        os._exit(0)\n"
        )
        code, out = self.gate_on({"test_x.py": body})
        self.assertEqual(code, 2, out)
        self.assertIn("tests: DEAD the test run ended without a result (exit 0)", out)
        self.assertEqual(last_line(out), "gate: DEAD")

    def test_process_exit_one_is_dead(self):
        body = case_module(
            "import os\n\nclass T(unittest.TestCase):\n"
            "    def test_a(self):\n        os._exit(1)\n"
        )
        code, out = self.gate_on({"test_x.py": body})
        self.assertEqual(code, 2, out)
        self.assertIn("tests: DEAD the test run ended without a result (exit 1)", out)
        self.assertEqual(last_line(out), "gate: DEAD")

    def test_one_skip_among_passes_is_dead(self):
        body = case_module(
            "class T(unittest.TestCase):\n"
            "    def test_a(self):\n        pass\n\n"
            "    @unittest.skip('later')\n"
            "    def test_b(self):\n        pass\n"
        )
        code, out = self.gate_on({"test_x.py": body})
        self.assertEqual(code, 2, out)
        self.assertIn("tests: DEAD (1 skipped: tests.test_x.T.test_b)", out)
        self.assertNotIn("tests: ok", out)
        self.assertEqual(last_line(out), "gate: DEAD")

    def test_all_skipped_is_dead(self):
        body = case_module(
            "@unittest.skip('later')\nclass T(unittest.TestCase):\n"
            "    def test_a(self):\n        pass\n\n"
            "    def test_b(self):\n        pass\n"
        )
        code, out = self.gate_on({"test_x.py": body})
        self.assertEqual(code, 2, out)
        self.assertIn("tests: DEAD (2 skipped: ", out)
        self.assertEqual(last_line(out), "gate: DEAD")

    def test_skip_at_import_is_dead(self):
        body = case_module("raise unittest.SkipTest('not here')\n")
        code, out = self.gate_on({"test_x.py": body})
        self.assertEqual(code, 2, out)
        self.assertIn("tests: DEAD (1 skipped: ", out)
        self.assertIn("test_x", out)
        self.assertEqual(last_line(out), "gate: DEAD")

    def test_expected_failure_is_dead(self):
        body = case_module(
            "class T(unittest.TestCase):\n"
            "    def test_a(self):\n        pass\n\n"
            "    @unittest.expectedFailure\n"
            "    def test_b(self):\n        self.fail('known')\n"
        )
        code, out = self.gate_on({"test_x.py": body})
        self.assertEqual(code, 2, out)
        self.assertIn("tests: DEAD (1 expected failures: tests.test_x.T.test_b)", out)
        self.assertEqual(last_line(out), "gate: DEAD")

    def test_unexpected_success_is_a_finding(self):
        body = case_module(
            "class T(unittest.TestCase):\n"
            "    @unittest.expectedFailure\n"
            "    def test_b(self):\n        pass\n"
        )
        code, out = self.gate_on({"test_x.py": body})
        self.assertEqual(code, 1, out)
        self.assertIn("tests: tests.test_x.T.test_b: UNEXPECTED SUCCESS", out)
        self.assertEqual(last_line(out), "gate: FAIL")

    def test_raising_test_is_an_error_finding(self):
        body = case_module(
            "class T(unittest.TestCase):\n"
            "    def test_e(self):\n        raise ValueError('bad')\n"
        )
        code, out = self.gate_on({"test_x.py": body})
        self.assertEqual(code, 1, out)
        self.assertIn("tests: tests.test_x.T.test_e: ERROR", out)
        self.assertIn("ValueError: bad", out)
        self.assertEqual(last_line(out), "gate: FAIL")

    def test_module_level_test_in_a_subfolder_is_a_finding(self):
        files = {
            "tests/__init__.py": "",
            "tests/sub/__init__.py": "",
            "tests/sub/test_z.py": "def test_z():\n    pass\n",
        }
        with tempfile.TemporaryDirectory() as tmp:
            make_repo(tmp, files)
            code, out = run_gate("--root", tmp)
        self.assertEqual(code, 1, out)
        self.assertIn("tests: tests/sub/test_z.py:1: module-level test function", out)

    def test_file_without_the_test_underscore_is_not_collected(self):
        good = case_module("class T(unittest.TestCase):\n    def test_a(self):\n        pass\n")
        bad = case_module("class U(unittest.TestCase):\n    def test_b(self):\n        self.fail('x')\n")
        code, out = self.gate_on({"test_ok.py": good, "testfoo.py": bad})
        self.assertEqual(code, 0, out)
        self.assertIn("tests: ok (1 tests)", out)


class TestsStageInProcessTests(unittest.TestCase):
    def test_dead_control_gives_exit_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(gate, "t1_findings", return_value=[]):
                code, lines = gate.run_tests(tmp)
        self.assertEqual(code, 2)
        self.assertTrue(lines[0].startswith("tests: DEAD "), lines)
        self.assertIn("positive control", lines[0])

    def test_missing_tests_directory_is_dead(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, lines = gate.run_tests(tmp)
        self.assertEqual(code, 2)
        self.assertEqual(lines, ["tests: DEAD 0 tests"])


class CrashGuardTests(unittest.TestCase):
    def run_main(self, admission, tests):
        out = io.StringIO()
        stub = lambda _root: (0, ["vocabulary: stub"])
        with mock.patch.object(gate, "run_admission", admission):
            with mock.patch.object(gate, "run_vocabulary", stub):
                with mock.patch.object(gate, "run_tests", tests):
                    with mock.patch.object(sys, "stdout", out):
                        code = gate.main(["--root", REAL_ROOT])
        return code, out.getvalue().strip().split("\n")

    def test_crashed_admission_is_dead_and_tests_still_run(self):
        def crash(_root):
            raise RuntimeError("boom")

        code, lines = self.run_main(crash, lambda _root: (0, ["tests: stub"]))
        self.assertEqual(code, 2)
        self.assertEqual(lines[0], "admission: DEAD RuntimeError: boom")
        self.assertIn("tests: stub", lines)
        self.assertEqual(lines[-1], "gate: DEAD")

    def test_crashed_tests_stage_is_dead(self):
        def crash(_root):
            raise RuntimeError("bang")

        code, lines = self.run_main(lambda _root: (0, ["admission: stub"]), crash)
        self.assertEqual(code, 2)
        self.assertEqual(lines[0], "admission: stub")
        self.assertEqual(lines[1], "vocabulary: stub")
        self.assertEqual(lines[2], "tests: DEAD RuntimeError: bang")
        self.assertEqual(lines[-1], "gate: DEAD")

    def test_broken_import_is_dead_not_a_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = os.path.join(tmp, "tools")
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
            git_init(tmp)
            proc = subprocess.run(
                [sys.executable, os.path.join(copy, "gate.py"), "--checks-only", "--root", tmp],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        out = proc.stdout.decode("utf-8")
        self.assertEqual(proc.returncode, 2, out)
        self.assertIn("admission: DEAD ModuleNotFoundError", out)
        self.assertEqual(out.strip().split("\n")[-1], "gate: DEAD")
        self.assertNotIn("Traceback", proc.stderr.decode("utf-8"))


class WorkflowTests(unittest.TestCase):
    def gate_job_lines(self):
        lines = read_workflow().split("\n")
        start = lines.index("  gate:")
        end = lines.index("  dev-tools:")
        return lines[start:end]

    def test_gate_job_runs_exactly_the_gate(self):
        runs = [l.strip() for l in self.gate_job_lines() if re.match(r"^\s*(- )?run:", l)]
        self.assertEqual(runs, ["- run: python3 tools/gate.py"])

    def test_every_action_is_pinned_to_a_commit(self):
        uses = [l for l in read_workflow().split("\n") if re.match(r"^\s*(- )?uses:", l)]
        self.assertTrue(uses)
        for line in uses:
            self.assertRegex(line, r"@[0-9a-f]{40}(\s|$)")

    def test_no_pull_request_target(self):
        self.assertNotIn("pull_request_target", read_workflow())

    def test_every_job_declares_a_ten_minute_timeout(self):
        lines = read_workflow().split("\n")
        for job in ("gate", "dev-tools"):
            start = lines.index("  %s:" % job)
            block = []
            for line in lines[start + 1:]:
                if re.match(r"^  \S", line):
                    break
                block.append(line)
            self.assertIn("    timeout-minutes: 10", block, job)


class StageOrderTests(unittest.TestCase):
    def run_main(self, *args):
        order = []

        def stage(name):
            def run(_root):
                order.append(name)
                return 0, ["%s: stub" % name]
            return run

        out = io.StringIO()
        with mock.patch.object(gate, "run_admission", stage("admission")):
            with mock.patch.object(gate, "run_vocabulary", stage("vocabulary")):
                with mock.patch.object(gate, "run_tests", stage("tests")):
                    with mock.patch.object(sys, "stdout", out):
                        code = gate.main(["--root", REAL_ROOT] + list(args))
        return code, order, out.getvalue().strip().split("\n")

    def test_stages_run_admission_vocabulary_tests(self):
        code, order, lines = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(order, ["admission", "vocabulary", "tests"])
        self.assertEqual(lines[-1], "gate: PASS")

    def test_checks_only_runs_admission_and_vocabulary(self):
        code, order, lines = self.run_main("--checks-only")
        self.assertEqual(code, 0)
        self.assertEqual(order, ["admission", "vocabulary"])
        self.assertEqual(lines[-1], "gate: PASS (checks only)")

    def test_a_crashed_vocabulary_stage_is_dead_and_tests_still_run(self):
        def crash(_root):
            raise RuntimeError("boom")

        order = []
        out = io.StringIO()
        with mock.patch.object(gate, "run_admission", lambda _r: (0, ["admission: stub"])):
            with mock.patch.object(gate, "run_vocabulary", crash):
                with mock.patch.object(gate, "run_tests", lambda _r: (order.append("t") or 0, ["tests: stub"])):
                    with mock.patch.object(sys, "stdout", out):
                        code = gate.main(["--root", REAL_ROOT])
        lines = out.getvalue().strip().split("\n")
        self.assertEqual(code, 2)
        self.assertEqual(lines[1], "vocabulary: DEAD RuntimeError: boom")
        self.assertEqual(order, ["t"])
        self.assertEqual(lines[-1], "gate: DEAD")

    def test_checks_only_on_the_real_repo_prints_both_checks(self):
        code, out = run_gate("--checks-only")
        self.assertEqual(code, 0, out)
        lines = out.strip().split("\n")
        self.assertTrue(lines[0].startswith("admission: ok ("), lines)
        self.assertTrue(lines[1].startswith("vocabulary: ok ("), lines)


class T1WidenedTests(unittest.TestCase):
    def test_class_not_a_testcase_is_a_hit(self):
        source = "class TestThing:\n    def test_a(self):\n        pass\n"
        self.assertEqual(
            gate.t1_findings(source, "tests/test_x.py"),
            ["tests: tests/test_x.py:1: test class is not a unittest.TestCase"],
        )

    def test_nested_class_is_a_hit(self):
        source = "import unittest\n\nclass Outer(unittest.TestCase):\n    class TestInner:\n        pass\n"
        found = gate.t1_findings(source, "tests/test_x.py")
        self.assertEqual(len(found), 1, found)
        self.assertIn(":4: test class is not a unittest.TestCase", found[0])

    def test_unittest_testcase_subclass_passes(self):
        source = "import unittest\n\nclass TestThing(unittest.TestCase):\n    pass\n"
        self.assertEqual(gate.t1_findings(source, "tests/test_x.py"), [])

    def test_import_of_a_unittest_submodule_binds_unittest(self):
        source = (
            "import unittest.mock\n\n"
            "class TestThing(unittest.TestCase):\n    pass\n"
        )
        self.assertEqual(gate.t1_findings(source, "tests/test_x.py"), [])

    def test_a_submodule_alias_is_not_the_unittest_module(self):
        source = (
            "import unittest.mock as m\n\n"
            "class TestThing(m.TestCase):\n    pass\n"
        )
        self.assertEqual(len(gate.t1_findings(source, "tests/test_x.py")), 1)

    def test_import_unittest_as_alias_still_passes(self):
        source = "import unittest as u\n\nclass TestThing(u.TestCase):\n    pass\n"
        self.assertEqual(gate.t1_findings(source, "tests/test_x.py"), [])

    def test_imported_testcase_subclass_passes(self):
        source = "from unittest import TestCase\n\nclass TestThing(TestCase):\n    pass\n"
        self.assertEqual(gate.t1_findings(source, "tests/test_x.py"), [])

    def test_a_bare_testcase_name_without_the_import_is_a_hit(self):
        source = "class TestThing(TestCase):\n    pass\n"
        self.assertEqual(len(gate.t1_findings(source, "tests/test_x.py")), 1)

    def test_class_not_a_testcase_is_found_in_a_subfolder(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "tests", "sub"))
            with open(os.path.join(tmp, "tests", "sub", "test_q.py"), "w", encoding="utf-8") as handle:
                handle.write("class TestQ:\n    pass\n")
            found = gate.t1_scan(os.path.join(tmp, "tests"))
        self.assertEqual(
            found, ["tests: tests/sub/test_q.py:1: test class is not a unittest.TestCase"]
        )

    def test_suffix_named_test_file_is_a_hit(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "tests", "sub"))
            with open(os.path.join(tmp, "tests", "sub", "thing_test.py"), "w", encoding="utf-8") as handle:
                handle.write("X = 1\n")
            found = gate.t1_scan(os.path.join(tmp, "tests"))
        self.assertEqual(found, ["tests: tests/sub/thing_test.py: test file pattern is test_*.py"])

    def test_prefix_named_test_file_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "tests"))
            with open(os.path.join(tmp, "tests", "test_thing.py"), "w", encoding="utf-8") as handle:
                handle.write("X = 1\n")
            self.assertEqual(gate.t1_scan(os.path.join(tmp, "tests")), [])


GOOD_TEST = case_module("class T(unittest.TestCase):\n    def test_a(self):\n        pass\n")


class ChildModeTests(unittest.TestCase):
    def test_child_mode_ends_with_a_child_line_and_never_a_gate_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_repo(tmp, {"tests/__init__.py": "", "tests/test_a.py": GOOD_TEST})
            result = os.path.join(tmp, "result.out")
            proc = subprocess.run(
                [sys.executable, GATE, "--root", tmp, "--tests-result", result],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=60,
            )
            self.assertTrue(os.path.exists(result))
        lines = proc.stdout.decode("utf-8").strip().split("\n")
        self.assertEqual(proc.returncode, 0, lines)
        self.assertTrue(lines[-1].startswith("tests-child:"), lines)
        self.assertFalse([l for l in lines if l.startswith("gate:")], lines)

    def test_the_parent_does_not_echo_the_child_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_repo(tmp, {"tests/__init__.py": "", "tests/test_a.py": GOOD_TEST})
            code, out = run_gate("--root", tmp)
        self.assertEqual(code, 0, out)
        self.assertNotIn("tests-child:", out)


class ChildStdinTests(unittest.TestCase):
    def test_a_test_reading_stdin_does_not_block(self):
        body = case_module(
            "import sys\n\n"
            "class T(unittest.TestCase):\n"
            "    def test_a(self):\n"
            "        self.assertEqual(sys.stdin.read(), '')\n"
        )
        read_end, write_end = os.pipe()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                make_repo(tmp, {"tests/__init__.py": "", "tests/test_a.py": body})
                # The write end stays open, so an inherited stdin would block.
                proc = subprocess.run(
                    [sys.executable, GATE, "--root", tmp],
                    stdin=read_end,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=60,
                )
        finally:
            os.close(read_end)
            os.close(write_end)
        out = proc.stdout.decode("utf-8")
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("tests: ok (1 tests)", out)


class NetworkBlockTests(unittest.TestCase):
    def test_create_connection_raises(self):
        with self.assertRaises(OSError) as ctx:
            socket.create_connection(("127.0.0.1", 9))
        self.assertIn("network disabled in tests", str(ctx.exception))

    def test_connect_raises(self):
        sock = socket.socket()
        self.addCleanup(sock.close)
        with self.assertRaises(OSError) as ctx:
            sock.connect(("127.0.0.1", 9))
        self.assertIn("network disabled in tests", str(ctx.exception))
        with self.assertRaises(OSError) as ctx:
            sock.connect_ex(("127.0.0.1", 9))
        self.assertIn("network disabled in tests", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
