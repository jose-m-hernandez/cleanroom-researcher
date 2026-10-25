"""Offline tests for the raw folder's owner file: a resume reuses the raw folder of its own results folder only.

A copied results folder carries the original's run id; it must be refused, not allowed to write into the original's
raw folder. The scripted claude of tests/test_exp0001_score.py stands in for the live service.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from tests import exp0001_paths  # noqa: F401

import arms  # noqa: E402
from tests import test_exp0001_plumbing as plumbing  # noqa: E402


class ResumeOwnerTests(plumbing.Scripted):  # Scripted defines no tests; it supplies go() and run_json()
    def raw_names(self):
        return sorted(p.name for p in arms.raw_dir(self.run_json()["raw_run_id"], self.environ).iterdir())

    def test_a_new_raw_folder_records_the_resolved_results_folder(self):
        self.go(arm_names=("floor",), seeds=1)
        owner = arms.raw_dir(self.run_json()["raw_run_id"], self.environ) / arms.OWNER_FILE
        self.assertEqual(json.loads(owner.read_text(encoding="utf-8")), {"results_folder": str(Path(self.out).resolve())})

    def test_a_copied_results_folder_is_refused_and_writes_nothing_into_the_original_raw_folder(self):
        self.go(arm_names=("floor",), seeds=1)
        before = self.raw_names()
        copy = self.tmp / "copied-results"
        shutil.copytree(self.out, copy)
        launched = self.launched
        with self.assertRaises(arms.RawError) as ctx:
            self.go(arm_names=("floor",), seeds=2, out=copy)
        self.assertIn("belongs to another results folder", str(ctx.exception))
        self.assertIn("new --out", str(ctx.exception))
        self.assertEqual(self.raw_names(), before)
        self.assertEqual(self.launched, launched)  # nothing was launched

    def test_the_original_resumed_still_reuses_its_run_id(self):
        self.go(arm_names=("floor",), seeds=1)
        first = self.run_json()["raw_run_id"]
        self.assertEqual(self.go(arm_names=("floor",), seeds=2), 0)
        self.assertEqual(self.run_json()["raw_run_id"], first)
        self.assertEqual(len([n for n in self.raw_names() if n != arms.OWNER_FILE]), 2)

    def test_a_missing_owner_file_refuses(self):
        self.go(arm_names=("floor",), seeds=1)
        (arms.raw_dir(self.run_json()["raw_run_id"], self.environ) / arms.OWNER_FILE).unlink()
        with self.assertRaises(arms.RawError) as ctx:
            self.go(arm_names=("floor",), seeds=2)
        self.assertIn("belongs to another results folder", str(ctx.exception))

    def test_a_missing_raw_folder_refuses(self):
        self.go(arm_names=("floor",), seeds=1)
        shutil.rmtree(arms.raw_dir(self.run_json()["raw_run_id"], self.environ))
        with self.assertRaises(arms.RawError) as ctx:
            self.go(arm_names=("floor",), seeds=2)
        self.assertIn("raw folder missing", str(ctx.exception))
        self.assertIn("TMPDIR", str(ctx.exception))

    def test_another_spelling_of_the_same_results_folder_resumes_and_reuses_its_run_id(self):
        self.go(arm_names=("floor",), seeds=1)
        first = self.run_json()["raw_run_id"]
        link = self.tmp / "results-link"
        link.symlink_to(self.out, target_is_directory=True)
        self.assertEqual(self.go(arm_names=("floor",), seeds=2, out=link), 0)
        self.assertEqual(self.run_json()["raw_run_id"], first)
