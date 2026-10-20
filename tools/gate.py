"""The one gate command.

Usage: python3 tools/gate.py [--root PATH] [--checks-only]

Runs every stage (admission, vocabulary, tests), whatever an earlier one
reported. The last line is exactly
``gate: PASS``, ``gate: FAIL`` or ``gate: DEAD`` (``gate: PASS (checks only)``
with --checks-only). Exit 0 pass, 1 findings, 2 dead or usage error.
"""

from __future__ import annotations

import argparse
import ast
import io
import os
import subprocess
import sys
import tempfile
import unittest
from typing import Callable, List, Optional, Tuple

TEST_PATTERN = "test_*.py"
CHILD_PREFIX = "tests-child:"


def _unittest_names(tree: ast.AST) -> Tuple[set, set]:
    """Local names bound to the unittest module, and to unittest.TestCase."""
    modules = set()
    classes = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "unittest":
                    # "import unittest.mock" binds unittest; "import unittest.mock as m" binds m.
                    if alias.asname:
                        if alias.name == "unittest":
                            modules.add(alias.asname)
                    else:
                        modules.add("unittest")
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == "unittest":
            for alias in node.names:
                if alias.name == "TestCase":
                    classes.add(alias.asname or "TestCase")
    return modules, classes


def _is_testcase_base(base: ast.expr, modules: set, classes: set) -> bool:
    if isinstance(base, ast.Name):
        return base.id in classes
    if isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name):
        return base.attr == "TestCase" and base.value.id in modules
    return False


def t1_findings(source: str, label: str) -> List[str]:
    """Rule T1: a test file defines no module-level test function, and every
    class named Test* subclasses unittest.TestCase directly."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    found = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            found.append("tests: %s:%d: module-level test function" % (label, node.lineno))
    modules, classes = _unittest_names(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            if not any(_is_testcase_base(b, modules, classes) for b in node.bases):
                found.append(
                    "tests: %s:%d: test class is not a unittest.TestCase" % (label, node.lineno)
                )
    return found


def run_admission(root: str) -> Tuple[int, List[str]]:
    # Imported here so that a broken import is reported by the stage guard.
    import check_admission

    return check_admission.run(root)


def run_vocabulary(root: str) -> Tuple[int, List[str]]:
    # Imported here so that a broken import is reported by the stage guard.
    import check_vocabulary

    return check_vocabulary.run(root)


def guarded(
    name: str, stage: Callable[[str], Tuple[int, List[str]]], root: str
) -> Tuple[int, List[str]]:
    """Run one stage; an exception becomes a DEAD result, never a traceback."""
    try:
        return stage(root)
    except Exception as exc:
        return 2, ["%s: DEAD %s: %s" % (name, type(exc).__name__, exc)]


def t1_scan(tests_dir: str) -> List[str]:
    """Rule T1 over every .py file under tests/, at any depth."""
    found: List[str] = []
    for dirpath, dirnames, filenames in os.walk(tests_dir):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            full = os.path.join(dirpath, name)
            label = os.path.relpath(full, os.path.dirname(tests_dir)).replace(os.sep, "/")
            if name.endswith("_test.py"):
                found.append("tests: %s: test file pattern is test_*.py" % label)
            with open(full, encoding="utf-8") as handle:
                found.extend(t1_findings(handle.read(), label))
    return found


def child_run(root: str, result_path: str) -> int:
    """Run the suite in this process and leave a result file for the parent.

    The file is written under a temporary name and moved into place, so a
    reader sees either nothing or the whole record.
    """
    tests_dir = os.path.join(root, "tests")
    suite = unittest.TestLoader().discover(
        tests_dir, pattern=TEST_PATTERN, top_level_dir=root
    )
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=0).run(suite)
    record = {
        "complete": True,
        "run": result.testsRun,
        "failures": [[t.id(), trace] for t, trace in result.failures],
        "errors": [[t.id(), trace] for t, trace in result.errors],
        "skipped": [t.id() for t, _reason in result.skipped],
        "expected_failures": [t.id() for t, _trace in result.expectedFailures],
        "unexpected_successes": [t.id() for t in result.unexpectedSuccesses],
    }
    part = result_path + ".part"
    with open(part, "w", encoding="utf-8") as handle:
        handle.write(repr(record))
    os.replace(part, result_path)
    return 0


def _is_pairs(value: object) -> bool:
    return isinstance(value, list) and all(
        isinstance(p, list) and len(p) == 2 and all(isinstance(x, str) for x in p)
        for p in value
    )


def _is_strings(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(x, str) for x in value)


def read_result(path: str) -> Optional[dict]:
    """The child's record, or None when it is missing, partial or malformed."""
    try:
        with open(path, encoding="utf-8") as handle:
            record = ast.literal_eval(handle.read())
    except (OSError, ValueError, SyntaxError, MemoryError, RecursionError):
        return None
    if not isinstance(record, dict) or record.get("complete") is not True:
        return None
    run = record.get("run")
    if not isinstance(run, int) or isinstance(run, bool):
        return None
    for key in ("failures", "errors"):
        if not _is_pairs(record.get(key)):
            return None
    for key in ("skipped", "expected_failures", "unexpected_successes"):
        if not _is_strings(record.get(key)):
            return None
    return record


def _output_lines(proc: "subprocess.CompletedProcess[bytes]") -> List[str]:
    lines: List[str] = []
    for blob in (proc.stdout, proc.stderr):
        text = blob.decode("utf-8", "replace").rstrip()
        if text:
            lines.extend(l for l in text.split("\n") if not l.startswith(CHILD_PREFIX))
    return lines


def run_tests(root: str) -> Tuple[int, List[str]]:
    control = t1_findings("def test_control():\n    pass\n", "control.py")
    if not control:
        return 2, ["tests: DEAD the T1 positive control produced no finding"]

    tests_dir = os.path.join(root, "tests")
    t1: List[str] = []
    if os.path.isdir(tests_dir):
        t1 = t1_scan(tests_dir)
    if t1:
        return 1, t1 + ["tests: %d finding(s)" % len(t1)]

    if not os.path.isdir(tests_dir):
        return 2, ["tests: DEAD 0 tests"]

    with tempfile.TemporaryDirectory() as scratch:
        result_path = os.path.join(scratch, "result.txt")
        cmd = [
            sys.executable, os.path.abspath(__file__),
            "--root", root, "--tests-result", result_path,
        ]
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        record = read_result(result_path)
    echoed = _output_lines(proc)
    if record is None:
        return 2, [
            "tests: DEAD the test run ended without a result (exit %d)" % proc.returncode
        ] + echoed
    if record["run"] == 0:
        return 2, ["tests: DEAD 0 tests"] + echoed

    dead: List[str] = []
    if record["skipped"]:
        dead.append(
            "tests: DEAD (%d skipped: %s)"
            % (len(record["skipped"]), ", ".join(record["skipped"]))
        )
    if record["expected_failures"]:
        dead.append(
            "tests: DEAD (%d expected failures: %s)"
            % (len(record["expected_failures"]), ", ".join(record["expected_failures"]))
        )
    lines: List[str] = []
    tracebacks: List[str] = []
    for kind, items in (("FAIL", record["failures"]), ("ERROR", record["errors"])):
        for test_id, trace in items:
            lines.append("tests: %s: %s" % (test_id, kind))
            tracebacks.append(trace.rstrip())
    for test_id in record["unexpected_successes"]:
        lines.append("tests: %s: UNEXPECTED SUCCESS" % test_id)
    count = len(lines)
    summary = ["tests: %d finding(s)" % count] if count else []
    if dead:
        return 2, dead + lines + tracebacks + summary + echoed
    if count:
        return 1, lines + tracebacks + summary + echoed
    return 0, ["tests: ok (%d tests)" % record["run"]] + echoed


def main(argv: Optional[List[str]] = None) -> int:
    default_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(description="Run the gate.")
    parser.add_argument("--root", default=default_root, help="repository root")
    parser.add_argument(
        "--checks-only", action="store_true", help="skip the tests stage"
    )
    parser.add_argument(
        "--tests-result",
        metavar="FILE",
        help="internal: run the test suite alone and write its record to FILE",
    )
    args = parser.parse_args(argv)
    root = os.path.abspath(args.root)
    if args.tests_result:
        code = child_run(root, os.path.abspath(args.tests_result))
        print(CHILD_PREFIX + " result written")
        sys.stdout.flush()
        return code

    codes = []
    code, lines = guarded("admission", run_admission, root)
    codes.append(code)
    for line in lines:
        print(line)
    code, lines = guarded("vocabulary", run_vocabulary, root)
    codes.append(code)
    for line in lines:
        print(line)
    if args.checks_only:
        print("tests: not run (--checks-only)")
    else:
        code, lines = guarded("tests", run_tests, root)
        codes.append(code)
        for line in lines:
            print(line)

    worst = max(codes)
    word = {0: "PASS", 1: "FAIL"}.get(worst, "DEAD")
    if worst == 0 and args.checks_only:
        word = "PASS (checks only)"
    print("gate: " + word)
    sys.stdout.flush()
    return worst


if __name__ == "__main__":
    sys.exit(main())
