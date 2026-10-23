"""Tests for fixture evidence, transcript normalization and the username scrub. Run with: python3 -m unittest discover tests"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from tests import exp0001_paths  # noqa: F401

import claude_run  # noqa: E402
import fixtures  # noqa: E402


class EvidenceGateTests(unittest.TestCase):
    def rows(self, *results):
        out = []
        for n, content in enumerate(results):
            block = {"type": "tool_result", "tool_use_id": f"t{n}", "content": content}
            out.append({"type": "user", "message": {"content": [block]}})
        return out

    def spec(self):
        return {"items": [{"id": "a", "needed": True, "source": "tool", "evidence": ["alpha", "beta"]},
                          {"id": "b", "needed": True, "source": "user"}]}

    def test_every_string_must_appear_in_a_non_denied_result(self):
        spec = self.spec()
        self.assertEqual(fixtures.tool_evidence_missing(self.rows("alpha here", "and beta there"), spec), [])
        self.assertEqual(fixtures.tool_evidence_missing(self.rows("alpha only"), spec), ["a"])
        self.assertEqual(fixtures.tool_evidence_missing([], spec), ["a"])

    def test_denied_results_do_not_count(self):
        denied = "Permission for this tool use was denied. It requires approval. alpha beta"
        self.assertEqual(fixtures.tool_evidence_missing(self.rows(denied), self.spec()), ["a"])
        self.assertEqual(fixtures.tool_evidence_missing(self.rows(denied, "alpha beta"), self.spec()), [])
        self.assertEqual(fixtures.count_denials(self.rows(denied, "alpha beta", denied)), 2)

    def test_list_content_is_read(self):
        content = [{"type": "text", "text": "alpha"}, {"type": "text", "text": "beta"}]
        self.assertEqual(fixtures.tool_evidence_missing(self.rows(content), self.spec()), [])

    def test_a_command_failure_is_not_a_denial(self):
        rows = self.rows("Exit code 1\nTraceback ... alpha beta")
        self.assertEqual(fixtures.count_denials(rows), 0)
        self.assertEqual(fixtures.tool_evidence_missing(rows, self.spec()), [])


class LoaderTests(unittest.TestCase):

    def test_an_empty_tree_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            self.assertEqual(fixtures.run_validate(Path(tmp), out=out), 1)
            self.assertIn("no_fixtures", out.getvalue())


# --------------------------------------------------------------------------
# Normalization and install (offline)
# --------------------------------------------------------------------------

LEAK_TYPES = "api-request-blob"
SNAPSHOT_TYPE = "prompt_snapshot"


class NormalizeTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(fixtures, "_current_username", return_value="someuser")
        patcher.start()
        self.addCleanup(patcher.stop)

    def synthetic(self, sandbox: Path):
        real = os.path.realpath(str(sandbox))
        slug = claude_run.project_slug(Path(real))
        sid = "11111111-1111-4111-8111-111111111111"

        def row(kind, uuid_, parent, content, **extra):
            return dict({"type": kind, "uuid": uuid_, "parentUuid": parent, "sessionId": sid, "cwd": real,
                         "timestamp": "t", "version": "9", "gitBranch": "main", "isSidechain": False,
                         "userType": "external", "requestId": "r", "message": {"role": kind, "content": content,
                                                                                "usage": {"input_tokens": 1}}}, **extra)
        rows = [
            {"type": "queue-operation", "uuid": "q0", "sessionId": sid},
            {"type": "attachment", "uuid": "a0", "parentUuid": None, "attachment": {"type": "instructions"}},
            row("user", "u1", "a0", [{"type": "text", "text": f"look at {real}/app.py"}]),
            row("assistant", "a1", "u1", [{"type": "thinking", "thinking": "t", "signature": "S"}]),
            row("assistant", "a2", "a1", [{"type": "redacted_thinking", "data": "d"},
                                          {"type": "text", "text": f"slug {slug}", "signature": "S2"},
                                          {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": real + "/x"}}]),
            {"type": LEAK_TYPES, "uuid": "x1", "parentUuid": "a2"},
            {"type": SNAPSHOT_TYPE, "uuid": "x2", "parentUuid": "x1"},
            {"type": "cost-state", "uuid": "x3", "parentUuid": "x2"},
            {"type": "last-prompt", "uuid": "x4", "parentUuid": "x3"},
            row("user", "u2", "x4", [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]),
        ]
        return rows, real, slug

    def test_drops_rows_blocks_and_fields_relinks_parents_and_placeholders_the_sandbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows, real, slug = self.synthetic(Path(tmp))
            out = fixtures.normalize_transcript(rows, fixtures.sandbox_aliases(tmp), slug)
        self.assertEqual([r["uuid"] for r in out], ["u1", "a2", "u2"])
        self.assertEqual([r["parentUuid"] for r in out], [None, "u1", "a2"])
        text = json.dumps(out)
        for gone in ("thinking", "redacted_thinking", "signature", "requestId", "usage", "queue-operation", "attachment",
                     LEAK_TYPES, SNAPSHOT_TYPE, "cost-state", "last-prompt"):
            self.assertNotIn(gone, text)
        self.assertNotIn(real, text)
        self.assertNotIn(slug, text)
        self.assertIn("{{SANDBOX}}/app.py", text)
        self.assertIn("slug {{SANDBOX_SLUG}}", text)
        self.assertEqual(set(out[0]) - set(fixtures.FIELD_WHITELIST), set())

    VF = "/var/folders/ab/xyz/T"
    MON = "Oct"

    @staticmethod
    def home_of(name):
        return "/" + "Users" + "/" + name  # test fixture for the scrub: a home path built at runtime

    def norm_text(self, text, aliases=(), tmpdir=None, name="zed"):
        tmpdir = self.VF + "/" if tmpdir is None else tmpdir
        rows = [{"type": "user", "uuid": "u", "parentUuid": None,
                 "message": {"content": [{"type": "text", "text": text}]}}]
        with mock.patch.object(fixtures, "_current_username", return_value=name), \
                mock.patch.object(Path, "home", return_value=Path(self.home_of(name))):
            out = fixtures.normalize_transcript(rows, list(aliases), "", environ={"TMPDIR": tmpdir})
        return out[0]["message"]["content"][0]["text"]

    def test_traceback_temp_path_becomes_tmp_placeholder_keeping_the_random_parts(self):
        text = 'File "' + self.VF + '/tmpabc/saves/one.json", line 3'
        self.assertEqual(self.norm_text(text), 'File "{{TMP}}/tmpabc/saves/one.json", line 3')

    def test_every_temp_root_form_is_rewritten_including_json_escaped(self):
        for form in (self.VF, "/private" + self.VF, self.VF.replace("/", "\\/")):
            with self.subTest(form=form):
                got = self.norm_text("at " + form + "/q/r.txt", tmpdir="/nowhere/else/")
                self.assertEqual(got, "at {{TMP}}/q/r.txt")
        self.assertEqual(self.norm_text("at /scratch/t1//x/y", tmpdir="/scratch/t1/"), "at {{TMP}}/x/y")
        self.assertEqual(self.norm_text("at /scratch/t10/x", tmpdir="/scratch/t1/"), "at /scratch/t10/x")

    def test_sandbox_rewrite_runs_before_the_temp_rewrite(self):
        parent = self.VF + "/cpr-eval/gen/dev-099/attempt-1-abc123"
        text = "Permission to use Read denied: " + parent + "/other.txt; " + parent + "/repo/app.py"
        got = self.norm_text(text, aliases=[parent + "/repo"])
        self.assertEqual(got, "Permission to use Read denied: {{TMP}}/cpr-eval/gen/dev-099/attempt-1-abc123/other.txt; "
                              "{{SANDBOX}}/app.py")

    def test_username_is_replaced_as_a_whole_word_and_the_group_is_kept(self):
        row = "-rw-r--r--@ 1 {}  staff  1234 " + self.MON + " 16 10:02 f.txt"
        got = self.norm_text(row.format("zed") + "; zedge zed_x " + self.home_of("zed") + "/work")
        self.assertEqual(got, row.format("user") + "; zedge zed_x {{HOME}}/work")

    def test_a_row_that_held_only_thinking_is_dropped(self):
        rows = [{"type": "assistant", "uuid": "a", "parentUuid": None,
                 "message": {"content": [{"type": "thinking", "thinking": "t"}]}}]
        self.assertEqual(fixtures.normalize_transcript(rows, [], ""), [])

    def test_install_round_trips_the_placeholders_with_a_fresh_session_id_per_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp).resolve()
            gen_sandbox = tmp / "gen"
            gen_sandbox.mkdir()
            rows, real, slug = self.synthetic(gen_sandbox)
            fdir = tmp / "fx"
            (fdir / "repo").mkdir(parents=True)
            (fdir / "repo" / "app.py").write_text("print(1)\n", encoding="utf-8")
            fixtures.write_jsonl(fdir / "transcript.jsonl", fixtures.normalize_transcript(rows, fixtures.sandbox_aliases(gen_sandbox), slug))
            (tmp / "home" / ".claude").mkdir(parents=True)
            environ = {"PATH": os.environ["PATH"], "HOME": str(tmp / "home"), "TMPDIR": str(tmp)}
            cfg = {"isolation": "sources"}
            first = fixtures.install_fixture(fdir, cfg, environ)
            second = fixtures.install_fixture(fdir, cfg, environ)
            seen = set()
            for sandbox, sid, config_dir in (first, second):
                new_real = os.path.realpath(str(sandbox))
                new_slug = claude_run.project_slug(Path(new_real))
                path = config_dir / "projects" / new_slug / f"{sid}.jsonl"
                installed = fixtures.read_jsonl(path)
                text = json.dumps(installed)
                self.assertIn(new_real + "/app.py", text)
                self.assertNotIn("{{", text)
                self.assertEqual({r["sessionId"] for r in installed}, {sid})
                self.assertTrue((sandbox / "app.py").is_file())
                self.assertTrue((sandbox / ".git").is_dir())
                seen.add(sid)
                self.assertEqual(len(installed), 3)
            self.assertEqual(len(seen), 2)
            self.assertNotEqual(first[0], second[0])
            manifest = (claude_run.eval_root(environ) / "project-dirs.txt").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(manifest), 2)

    def test_snapshot_tree_skips_git_and_caches(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "src", Path(tmp) / "dst"
            (src / ".git").mkdir(parents=True)
            (src / ".git" / "HEAD").write_text("x", encoding="utf-8")
            (src / "__pycache__").mkdir()
            (src / "__pycache__" / "a.pyc").write_text("x", encoding="utf-8")
            (src / "pkg").mkdir()
            (src / "pkg" / "a.py").write_text("x", encoding="utf-8")
            self.assertEqual(fixtures.snapshot_tree(src, dst), ["pkg/a.py"])
            self.assertFalse((dst / ".git").exists())


class UsernameTests(unittest.TestCase):
    """The username for the scrub comes from USER, else LOGNAME, and fails closed (no pwd lookup)."""

    ROWS = [{"type": "user", "uuid": "u", "parentUuid": None,
             "message": {"content": [{"type": "text", "text": "alice ran it; bob watched; carol was absent"}]}}]

    def scrub(self):
        return fixtures.normalize_transcript(self.ROWS, [], "")[0]["message"]["content"][0]["text"]

    def test_the_scrub_uses_user(self):
        with mock.patch.dict(os.environ, {"USER": "alice", "LOGNAME": "bob"}, clear=True):
            self.assertEqual(fixtures._current_username(), "alice")
            self.assertEqual(self.scrub(), "user ran it; bob watched; carol was absent")

    def test_the_scrub_falls_back_to_logname(self):
        for env in ({"LOGNAME": "bob"}, {"USER": "", "LOGNAME": "bob"}):
            with self.subTest(env=env):
                with mock.patch.dict(os.environ, env, clear=True):
                    self.assertEqual(fixtures._current_username(), "bob")
                    self.assertEqual(self.scrub(), "alice ran it; user watched; carol was absent")

    def test_the_scrub_raises_when_both_are_unset_or_empty(self):
        for env in ({}, {"USER": "", "LOGNAME": ""}, {"USER": ""}):
            with self.subTest(env=env):
                with mock.patch.dict(os.environ, env, clear=True):
                    with self.assertRaises(fixtures.UsernameError):
                        fixtures._current_username()
                    with self.assertRaises(fixtures.UsernameError):
                        fixtures.normalize_transcript(self.ROWS, [], "")

    def test_the_home_folder_comes_from_path_home(self):
        home = "/" + "Users" + "/alice"  # test fixture for the scrub: a home path built at runtime
        rows = [{"type": "user", "uuid": "u", "parentUuid": None,
                 "message": {"content": [{"type": "text", "text": "saved to " + home + "/work"}]}}]
        with mock.patch.dict(os.environ, {"USER": "zed"}, clear=True), \
                mock.patch.object(Path, "home", return_value=Path(home)):
            out = fixtures.normalize_transcript(rows, [], "")
        self.assertEqual(out[0]["message"]["content"][0]["text"], "saved to {{HOME}}/work")


class GenCostTests(unittest.TestCase):
    def test_rederive_turns_cumulative_costs_into_per_invocation_costs(self):
        gen = {"model": "m", "total_cost_usd": 0.36, "turns": [
            {"turn": 1, "cost_usd": 0.03, "duration_ms": 1}, {"turn": 2, "cost_usd": 0.05, "duration_ms": 2},
            {"turn": 3, "cost_usd": 0.09, "duration_ms": 3}, {"turn": 4, "cost_usd": 0.08, "duration_ms": 4}]}
        new = fixtures.rederive_gen_costs(gen)
        self.assertEqual([t["cost_usd"] for t in new["turns"]], [0.03, 0.02, 0.04, 0.08])  # turn 4 fell: full value
        self.assertEqual(new["total_cost_usd"], 0.17)
        self.assertEqual([t["duration_ms"] for t in new["turns"]], [1, 2, 3, 4])
        self.assertEqual(new["model"], "m")
        self.assertEqual(gen["turns"][1]["cost_usd"], 0.05)  # the input is not modified

    def test_a_non_numeric_turn_cost_makes_the_next_turn_full(self):
        gen = {"turns": [{"turn": 1, "cost_usd": None}, {"turn": 2, "cost_usd": 0.05}]}
        new = fixtures.rederive_gen_costs(gen)
        self.assertEqual([t["cost_usd"] for t in new["turns"]], [None, 0.05])
        self.assertEqual(new["total_cost_usd"], 0.05)


if __name__ == "__main__":
    unittest.main()
