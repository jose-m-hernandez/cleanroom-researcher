"""Offline checks that EXP-0002's copy of the method code differs from EXP-0001's in the five named places only.

Run with: python3 -m unittest discover tests

Every check that executes the copy's code runs in a child process with the copy's folders alone on the path. The
modules are never imported here: EXP-0001's tests import modules of the same bare names, and Python would hand
this file the cached EXP-0001 modules, so a check run in this process would prove nothing about the copy.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "experiments" / "EXP-0001" / "code"
NEW = ROOT / "experiments" / "EXP-0002" / "code"
TARGET = ROOT / "experiments" / "EXP-0002" / "target" / "whole-values"

A_LINE_36 = 'Leave out implementation detail, full file contents, timestamps, and anything the code or git history already records. Point to files instead of copying them. Keep exact values for paths, flags and identifiers.'
B_LINE_36 = 'Leave out implementation detail, full file contents, timestamps, and anything the code or git history already records. Point to files instead of copying them. Keep exact values for paths, flags, identifiers and numbers, keep every item when the session gave a list, and when the session changed a value, record only the latest one.'
A_SKILL_SHA256 = "d0c1cd8c5dfe939a8e2c61ec8eb2e04212e0388bafe872432343b03f0c38a9c6"
PROVENANCE = ("SKILL.md in this folder is skills/preserve/SKILL.md of the CPR plugin at commit "
              "cfd1e77e07d2b3698591688e7e151d83d3ef51c3, with line 36 changed for EXP-0002.")

# Per file: exact (old, new, count) substitutions that turn the EXP-0001 text into the EXP-0002 text.
SUBSTITUTIONS = {
    "runner/arms.py": [
        ("(a SKILL.md under arms/<name>/)", "(a SKILL.md under the experiment's target/<name>/)", 1),
        ('ARMS_DIR = HERE / "arms"', 'ARMS_DIR = HERE.parent / "target"', 1),
        ("$TMPDIR/cpr-eval/raw/", "$TMPDIR/cpr-eval-exp0002/raw/", 2),
    ],
    "runner/claude_run.py": [
        ('/ "cpr-eval"\n', '/ "cpr-eval-exp0002"\n', 1),
        ('RECORDED_DIR = ("experiments", "EXP-0001", "recorded")', 'RECORDED_DIR = ("experiments", "EXP-0002", "recorded")', 1),
        ("finds experiments/EXP-0001/recorded/ among", "finds experiments/EXP-0002/recorded/ among", 1),
        ("and not under experiments/EXP-0001/recorded/", "and not under experiments/EXP-0002/recorded/", 1),
        ('"plugin_ref": cfg.get("plugin_ref") if plugin else None},\n',
         '"plugin_ref": cfg.get("plugin_ref") if plugin else None},\n'
         '        "tool_set": "none" if probe else "default",\n', 1),
    ],
    "runner/run.py": [
        ("experiments/EXP-0001/recorded/", "experiments/EXP-0002/recorded/", 4),
        ("$TMPDIR/cpr-eval/raw/", "$TMPDIR/cpr-eval-exp0002/raw/", 1),
    ],
}
CONFIG_CHANGES = {"spend_ceiling_usd": 45, "phase_caps": {"control": 7.0, "generation": 1.0, "evaluation": 36.0}}


def code_files(folder: Path) -> set:
    return {str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file() and "__pycache__" not in p.parts}


def child(script: str, *args: str) -> str:
    """Run script in an isolated child process and return its stdout."""
    proc = subprocess.run([sys.executable, "-I", "-c", script, *args], capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise AssertionError("child failed (%d): %s" % (proc.returncode, proc.stderr))
    return proc.stdout


PATHS = '''
import sys
code = sys.argv[1]
sys.path[:0] = [code, code + "/runner"]
'''


class CopyDiffersInFivePlacesOnly(unittest.TestCase):
    """The copy's file set and text equal EXP-0001's, apart from the pinned substitutions."""

    def test_same_file_set(self):
        self.assertEqual(code_files(OLD), code_files(NEW))

    def test_text_differs_by_the_pinned_substitutions_only(self):
        for rel in sorted(code_files(OLD) - {"config.json"}):
            with self.subTest(file=rel):
                old = (OLD / rel).read_text(encoding="utf-8")
                expect = old
                for was, now, count in SUBSTITUTIONS.get(rel, []):
                    self.assertEqual(old.count(was), count, "%s: %r occurs %d times in EXP-0001" % (rel, was, old.count(was)))
                    expect = expect.replace(was, now)
                self.assertEqual((NEW / rel).read_text(encoding="utf-8"), expect)

    def test_every_pinned_file_exists(self):
        self.assertTrue(set(SUBSTITUTIONS) <= code_files(OLD))

    def test_config_differs_in_the_four_values_only(self):
        old = json.loads((OLD / "config.json").read_text(encoding="utf-8"))
        new = json.loads((NEW / "config.json").read_text(encoding="utf-8"))
        expect = dict(old, spend_ceiling_usd=45, phase_caps={"control": 7.0, "generation": 1.0, "evaluation": 36.0})
        self.assertEqual(new, expect)
        self.assertNotEqual(old["spend_ceiling_usd"], new["spend_ceiling_usd"])
        self.assertEqual(set(old["phase_caps"]), set(new["phase_caps"]))


class EvalRootsDiffer(unittest.TestCase):
    """The two copies keep their spend ledger and raw logs under different folders of the same TMPDIR."""

    def test_roots_differ_under_one_tmpdir(self):
        script = PATHS + 'import claude_run\nprint(claude_run.eval_root({"TMPDIR": sys.argv[2]}))\n'
        with tempfile.TemporaryDirectory() as t:
            old = child(script, str(OLD), t).strip()
            new = child(script, str(NEW), t).strip()
        self.assertNotEqual(old, new)
        self.assertTrue(old.endswith("cpr-eval"), old)
        self.assertTrue(new.endswith("cpr-eval-exp0002"), new)


FAKE = r'''#!/usr/bin/env python3
import json, os, re, sys
here = os.path.dirname(os.path.abspath(__file__))
open(os.path.join(here, "calls.jsonl"), "a").write(json.dumps({"argv": sys.argv[1:]}) + "\n")
a = sys.argv
sid = a[a.index("--session-id") + 1] if "--session-id" in a else a[a.index("--resume") + 1]
slug = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(os.getcwd()))
d = os.path.join(os.environ["HOME"], ".claude", "projects", slug)
os.makedirs(d, exist_ok=True)
open(os.path.join(d, sid + ".jsonl"), "w").write(json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n")
print(json.dumps({"type": "system", "subtype": "init", "claude_code_version": "9.9.9", "model": "claude-test-model", "session_id": sid}))
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.01, "duration_ms": 5, "usage": {}, "permission_denials": []}))
'''

RUN_BOTH = PATHS + '''
import json, os
from pathlib import Path
import claude_run
tmp, bindir = Path(sys.argv[2]), Path(sys.argv[3])
CFG = {
    "claude_code_version": "9.9.9", "model": "claude-test-model", "isolated_baseline_hooks": 0, "isolation": "sources",
    "max_budget_usd_per_run": 0.75, "probe_max_output_tokens": 2048, "probe_timeout_s": 180,
    "phase_caps": {"control": 3.0, "generation": 15.0, "evaluation": 30.0}, "spend_ceiling_usd": 55,
    "plugin_ref": "cfd1e77e07d2b3698591688e7e151d83d3ef51c3", "instructions_attachment_proven": True,
    "init_allowlist": {"skills": ["update-config"], "agents": ["general-purpose"]},
}
environ = {"PATH": str(bindir) + os.pathsep + os.environ["PATH"], "HOME": str(tmp / "home"), "TMPDIR": str(tmp), "CPR_EVAL_LIVE": "1"}
sandbox = claude_run.make_sandbox("t", environ)
export = tmp / "export"
export.mkdir()
first = claude_run.run_claude(dict(CFG), "hello", sandbox, label="t", phase="control", plugin=export, environ=environ)
prior = {"session_id": "abc", "sandbox": str(sandbox), "memory_dir_after": []}
second = claude_run.run_claude(dict(CFG), "p", sandbox, label="t", phase="control", probe=True, resume="abc", environ=environ, prior=prior)
print(json.dumps({"tool_sets": [first.get("tool_set", "MISSING"), second.get("tool_set", "MISSING")]}))
'''


class RunRecordsCarryToolSet(unittest.TestCase):
    """Each run record says which tool set the run had, and the tool flags themselves are unchanged."""

    def test_records_and_argv(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t).resolve()
            bindir = tmp / "bin"
            bindir.mkdir()
            (tmp / "home").mkdir()
            fake = bindir / "claude"
            fake.write_text(FAKE, encoding="utf-8")
            fake.chmod(0o755)
            out = child(RUN_BOTH, str(NEW), str(tmp), str(bindir))
            calls = [json.loads(ln)["argv"] for ln in (bindir / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(json.loads(out.strip().splitlines()[-1])["tool_sets"], ["default", "none"])
        self.assertEqual(len(calls), 2)
        self.assertNotIn("--tools", calls[0])
        self.assertIn("--tools", calls[1])
        self.assertEqual(calls[1][calls[1].index("--tools") + 1], "")


class VariantIsOneLine(unittest.TestCase):
    """The variant's skill file is the published file with line 36 replaced."""

    def lines(self):
        return (TARGET / "SKILL.md").read_text(encoding="utf-8").split("\n")

    def test_length_and_line_36(self):
        lines = self.lines()
        self.assertEqual(len(lines), 63)  # 62 lines and the final newline
        self.assertEqual(lines[-1], "")
        self.assertEqual(lines[35], B_LINE_36)
        self.assertNotEqual(B_LINE_36, A_LINE_36)

    def test_restoring_line_36_gives_the_published_file(self):
        lines = self.lines()
        lines[35] = A_LINE_36
        self.assertEqual(hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest(), A_SKILL_SHA256)


class LicenceNotice(unittest.TestCase):
    """The variant folder carries the MIT notice and says which file it covers."""

    def test_notice(self):
        text = (TARGET / "LICENSE").read_text(encoding="utf-8")
        lines = text.rstrip("\n").split("\n")
        self.assertTrue(text.startswith("MIT License"))
        self.assertTrue(any(ln.startswith("Copyright (c)") for ln in lines))
        self.assertEqual(lines[-1], PROVENANCE)


class VariantsAreFoundUnderTarget(unittest.TestCase):
    """The copy reads skill variants from the experiment's target/ folder."""

    def test_variant_skill_path(self):
        script = PATHS + 'import arms\np = arms.variant_skill("whole-values")\nprint(p)\n'
        got = Path(child(script, str(NEW)).strip()).resolve()
        self.assertEqual(got, (TARGET / "SKILL.md").resolve())
        self.assertEqual(got.parts[-4:], ("EXP-0002", "target", "whole-values", "SKILL.md"))


if __name__ == "__main__":
    unittest.main()
