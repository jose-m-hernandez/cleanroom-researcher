import ast
import os
import re
import tempfile
import unittest

from lib import paths

REAL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Written out on purpose: a new import needs a new line here, in review.
STDLIB_ALLOWED = frozenset(
    [
        "__future__",
        "argparse",
        "ast",
        "copy",
        "datetime",
        "difflib",
        "hashlib",
        "io",
        "json",
        "math",
        "os",
        "pathlib",
        "posixpath",
        "re",
        "shutil",
        "socket",
        "stat",
        "statistics",
        "subprocess",
        "sys",
        "tarfile",
        "tempfile",
        "typing",
        "unicodedata",
        "unittest",
    ]
)

# Allowed only under experiments/<name>/code/runner/: they vary run to run.
RUNNER_ONLY = frozenset(["random", "time", "uuid"])

BANNED_MODULES = frozenset(
    [
        "socket", "ssl", "http", "urllib", "ftplib", "smtplib", "poplib",
        "imaplib", "telnetlib", "xmlrpc", "webbrowser",
    ]
)
BANNED_STRINGS = frozenset(
    ["curl", "wget", "mail", "mailx", "sendmail", "ssh", "scp", "nc"]
)
# Modules whose mere import is a hit: they load code or reach outside Python.
CAPABILITY_MODULES = frozenset(["importlib", "builtins", "ctypes"])
# (name, attribute) pairs that are a hit wherever they appear.
BANNED_ATTRIBUTES = frozenset(
    [
        ("importlib", "import_module"),
        ("builtins", "__import__"),
        ("os", "system"),
        ("os", "popen"),
    ]
)

# Files exempt from the tripwire. Empty in this commit.
TRIPWIRE_EXEMPT = frozenset()

SCAN_DIRS = ("tools", "tests", "experiments")
TRIPWIRE_DIRS = ("tools", "experiments")


def allowed_for(rel):
    if paths.RUNNER_ROOT.match(rel) and rel.endswith(".py"):
        return STDLIB_ALLOWED | RUNNER_ONLY
    return STDLIB_ALLOWED


def python_files(top, root=REAL_ROOT):
    base = os.path.join(root, top)
    found = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for name in sorted(filenames):
            if name.endswith(".py"):
                full = os.path.join(dirpath, name)
                found.append(os.path.relpath(full, root).replace(os.sep, "/"))
    return found


def parse(rel, root=REAL_ROOT):
    with open(os.path.join(root, *rel.split("/")), encoding="utf-8") as handle:
        return ast.parse(handle.read(), filename=rel)


def imported_modules(tree):
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.append(node.module.split(".")[0])
    return names


def local_modules():
    tools = os.path.join(REAL_ROOT, "tools")
    names = {"tests"}
    for entry in os.listdir(tools):
        full = os.path.join(tools, entry)
        if entry.endswith(".py"):
            names.add(entry[:-3])
        elif os.path.isfile(os.path.join(full, "__init__.py")):
            names.add(entry)
    return names


def import_violations(root=REAL_ROOT):
    local = local_modules()
    found = []
    for top in SCAN_DIRS:
        if not os.path.isdir(os.path.join(root, top)):
            continue
        for rel in python_files(top, root):
            allowed = allowed_for(rel)
            for name in imported_modules(parse(rel, root)):
                if name not in allowed and name not in local:
                    found.append("%s imports %s" % (rel, name))
    return found


def tripwire_hits(tree):
    """Return a list of descriptions of every capability the tree reaches for.

    Known limit: a module name or command assembled from split strings at
    runtime is invisible to a structural check like this one. Caught: banned
    and capability modules, os.system and os.popen as attributes or as
    from-imports (including a star import from os), __import__ and banned
    command strings.
    """
    hits = []
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.append(node.module.split(".")[0])
            if node.module == "os":
                for alias in node.names:
                    if alias.name in ("system", "popen", "*"):
                        hits.append("from os import %s" % alias.name)
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if (node.value.id, node.attr) in BANNED_ATTRIBUTES:
                hits.append("attribute %s.%s" % (node.value.id, node.attr))
        elif isinstance(node, ast.Name) and node.id == "__import__":
            hits.append("name __import__")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in BANNED_STRINGS:
                hits.append("string %r" % node.value)
    for name in modules:
        if name in BANNED_MODULES:
            hits.append("banned module %s" % name)
        if name in CAPABILITY_MODULES:
            hits.append("capability module %s" % name)
    return hits


class StdlibOnlyTests(unittest.TestCase):
    def test_files_are_found(self):
        self.assertTrue(python_files("tools"))
        self.assertTrue(python_files("tests"))

    def test_imports_are_stdlib_or_local(self):
        self.assertEqual(import_violations(), [])

    def test_pytest_is_not_allowed(self):
        self.assertNotIn("pytest", STDLIB_ALLOWED)

    def test_the_allowlist_catches_a_stranger(self):
        tree = ast.parse("import requests\nfrom yaml import safe_load\n")
        strangers = [n for n in imported_modules(tree) if n not in STDLIB_ALLOWED]
        self.assertEqual(strangers, ["requests", "yaml"])


class RunnerRootTests(unittest.TestCase):
    def violations(self, path, text):
        with tempfile.TemporaryDirectory() as tmp:
            full = os.path.join(tmp, *path.split("/"))
            os.makedirs(os.path.dirname(full))
            with open(full, "w", encoding="utf-8") as handle:
                handle.write(text)
            return import_violations(tmp)

    def test_runner_only_modules_pass_under_the_runner_root(self):
        text = "import random\nimport time\nimport uuid\n"
        self.assertEqual(
            self.violations("experiments/EXP-0001/code/runner/r.py", text), []
        )

    def test_runner_only_modules_fail_elsewhere(self):
        text = "import random\nimport time\nimport uuid\n"
        for path in (
            "experiments/EXP-0001/code/analysis.py",
            "experiments/EXP-0001/code/runner.py",
            "experiments/a/b/code/runner/r.py",
            "tools/x.py",
        ):
            with self.subTest(path=path):
                self.assertEqual(len(self.violations(path, text)), 3)

    def test_new_allowlist_names_pass_in_tools(self):
        self.assertEqual(self.violations("tools/x.py", "import copy\nimport json\n"), [])

    def test_a_stranger_fails_under_the_runner_root(self):
        found = self.violations(
            "experiments/EXP-0001/code/runner/r.py", "import requests\n"
        )
        self.assertEqual(len(found), 1)

    def test_runner_root_is_anchored_at_the_repository_root(self):
        found = self.violations(
            "tools/experiments/EXP-0001/code/runner/r.py", "import random\n"
        )
        self.assertEqual(len(found), 1)

    def test_runner_root_needs_the_runner_directory(self):
        found = self.violations(
            "experiments/EXP-0001/code/runner_old/r.py", "import random\n"
        )
        self.assertEqual(len(found), 1)

    def test_all_eight_new_names_are_allowed(self):
        names = ["copy", "difflib", "hashlib", "json", "math", "pathlib", "statistics", "tarfile"]
        text = "".join("import %s\n" % n for n in names)
        self.assertEqual(self.violations("tools/x.py", text), [])
        for name in names:
            with self.subTest(name=name):
                self.assertIn(name, STDLIB_ALLOWED)

    def test_runner_only_is_disjoint_from_the_allowlist(self):
        self.assertTrue(RUNNER_ONLY.isdisjoint(STDLIB_ALLOWED))


class TripwireTests(unittest.TestCase):
    def test_no_network_or_mail_capability_in_tools(self):
        checked = 0
        for top in TRIPWIRE_DIRS:
            for rel in python_files(top):
                if rel in TRIPWIRE_EXEMPT:
                    continue
                checked += 1
                self.assertEqual(tripwire_hits(parse(rel)), [], rel)
        self.assertGreater(checked, 0)

    def test_the_exemption_list_is_empty(self):
        self.assertEqual(TRIPWIRE_EXEMPT, frozenset())

    def assertHits(self, snippets):
        for source in snippets:
            self.assertTrue(tripwire_hits(ast.parse(source)), source)

    def test_planted_banned_module(self):
        self.assertHits(["import socket\n", "from urllib import request\n"])

    def test_planted_banned_string(self):
        self.assertHits(["run('curl')\n"])

    def test_planted_importlib_from_import(self):
        self.assertHits(["from importlib import import_module\n"])

    def test_planted_importlib_use(self):
        self.assertHits(["import importlib\n", "importlib.import_module('x')\n"])

    def test_planted_builtins_and_dunder_import(self):
        self.assertHits(
            ["import builtins\n", "builtins.__import__('x')\n", "__import__('x')\n"]
        )

    def test_planted_ctypes(self):
        self.assertHits(["import ctypes\n", "from ctypes import CDLL\n"])

    def test_planted_os_system(self):
        self.assertHits(["import os\nos.system('x')\n"])

    def test_planted_os_popen(self):
        self.assertHits(["import os\nos.popen('x')\n"])

    def test_planted_from_os_import_system(self):
        self.assertHits(["from os import system\n"])

    def test_planted_from_os_import_popen(self):
        self.assertHits(["from os import popen\n"])

    def test_planted_from_os_import_star(self):
        self.assertHits(["from os import *\n"])

    def test_from_os_import_path_has_no_hit(self):
        self.assertEqual(tripwire_hits(ast.parse("from os import path\n")), [])

    def test_clean_snippet_has_no_hit(self):
        source = "import os\nimport re\nprint(os.path.join('a', 'b'), re.escape('c'))\n"
        self.assertEqual(tripwire_hits(ast.parse(source)), [])


if __name__ == "__main__":
    unittest.main()
