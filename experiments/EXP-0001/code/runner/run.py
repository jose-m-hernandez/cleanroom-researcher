#!/usr/bin/env python3
"""Entry point for the evals harness: preflight, control, judge-check, validate, gen-fixture, install-check, install-verify, spend-rebuild, run, score, report, pilot, pilot-md."""

from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import claude_run
import fixtures
import judge
import pilot
import arms
import probes
import report  # noqa: E402
import score  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="run.py")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight", help="check the sandbox root, the claude version; no model call")
    sub.add_parser("control", help="live isolation control (five runs); needs CPR_EVAL_LIVE=1")
    sub.add_parser("judge-check", help="live judge controls and sanity cases; needs CPR_EVAL_LIVE=1")
    check = sub.add_parser("validate", help="validate every fixture spec under the fixtures folder; no model call")
    check.add_argument("--heldout-complete", action="store_true", help="also require the full held-out long-session count")
    gen = sub.add_parser("gen-fixture", help="generate, normalize, verify and install one fixture; needs CPR_EVAL_LIVE=1")
    gen.add_argument("fixture_id")
    inst = sub.add_parser("install-check", help="resume control for an installed fixture; needs CPR_EVAL_LIVE=1")
    inst.add_argument("fixture_id")
    verify = sub.add_parser("install-verify", help="re-check an installed fixture offline (evidence gate and lint)")
    verify.add_argument("fixture_id")
    rebuild = sub.add_parser("spend-rebuild", help="recompute spend.json from the stored run records (per-invocation costs); dry run unless --write")
    rebuild.add_argument("--write", action="store_true", help="write spend.json atomically")
    batch = sub.add_parser("run", help="preserve runs and probe sessions for arms x fixtures x seeds; needs CPR_EVAL_LIVE=1")
    batch.add_argument("--split", default="dev", choices=fixtures.SPLITS)
    batch.add_argument("--fixtures", default="all", help="'all', or comma-separated fixture ids or id prefixes")
    batch.add_argument("--fixtures-root", help="directory holding <id>/spec.json (default <code>/fixtures/<split>)")
    batch.add_argument("--out", help="results directory (required; outside the clone, or under experiments/EXP-0001/recorded/); raw logs go to $TMPDIR/cpr-eval/raw/<run id>/")
    batch.add_argument("--arms", default="floor,baseline,oracle")
    batch.add_argument("--seeds", type=int, default=5)
    batch.add_argument("--budget-usd", type=float, help="stop before a unit when the pilot ledger has grown by this much")
    batch.add_argument("--date", help="YYYYMMDD for run.json (default today)")
    scoring = sub.add_parser("score", help="judge labels and metrics for a results directory; needs CPR_EVAL_LIVE=1")
    scoring.add_argument("--from", dest="source", required=True, help="results directory written by run")
    scoring.add_argument("--fixtures-root", help="directory holding <id>/spec.json (default <code>/fixtures/<split of the run>)")
    scoring.add_argument("--model", help="judge model override (default: the judge pin)")
    rep = sub.add_parser("report", help="REPORT.md from a results directory; no model call")
    rep.add_argument("--from", dest="source", required=True, help="results directory holding run.json and scores.json")
    rep.add_argument("--out", help="directory to write into (default: the --from directory)")
    pil = sub.add_parser("pilot", help="the dev pilot: run, score, report and PILOT.md; needs CPR_EVAL_LIVE=1")
    pil.add_argument("--fixtures", default="dev-001..dev-005", help="a range 'dev-001..dev-005', or comma-separated ids or prefixes")
    pil.add_argument("--fixtures-root", help="directory holding <id>/spec.json (default <code>/fixtures/dev)")
    pil.add_argument("--seeds", type=int, default=3)
    pil.add_argument("--arms", default="floor,baseline,oracle")
    pil.add_argument("--budget-usd", type=float, required=True, help="most the pilot ledger may grow in this invocation; above phase_caps.evaluation refuses")
    pil.add_argument("--out", help="results directory (required; outside the clone, or under experiments/EXP-0001/recorded/)")
    pil.add_argument("--date", help="YYYYMMDD for run.json (default today)")
    pmd = sub.add_parser("pilot-md", help="regenerate PILOT.md from a pilot results directory; no model call")
    pmd.add_argument("--from", dest="source", required=True, help="results directory written by pilot")
    pmd.add_argument("--fixtures-root", help="directory holding <id>/gen.json (default <code>/fixtures/<split of the run>)")
    pmd.add_argument("--out", help="directory to write into (default: the --from directory)")
    args = parser.parse_args(argv)
    try:
        isolation_mode = claude_run.load_config().get("isolation")
    except (OSError, ValueError, AttributeError):
        isolation_mode = None  # an unreadable config is each command's own stop
    if isolation_mode == "tempconfig":
        print(f"{args.command}: REFUSED {claude_run.TEMPCONFIG_REFUSAL}")
        return 2
    if args.command in ("score", "report", "pilot-md"):
        refusal = claude_run.from_refusal(args.source)
        if refusal:
            print(f"{args.command}: REFUSED {refusal}")
            return 2
    if args.command in ("run", "pilot", "report", "pilot-md") and (args.out or args.command in ("run", "pilot")):
        refusal = claude_run.out_refusal(args.out)
        if refusal:
            print(f"{args.command}: REFUSED {refusal}")
            return 2
    if args.command == "preflight":
        reason = claude_run.preflight(claude_run.load_config())
        print("preflight: PASS" if not reason else f"preflight: FAIL {reason}")
        return 0 if not reason else 1
    if args.command == "control":
        return claude_run.control()
    if args.command == "judge-check":
        return judge.judge_check()
    if args.command == "validate":
        return fixtures.run_validate(heldout_complete=args.heldout_complete)
    if args.command == "gen-fixture":
        return fixtures.command_gen_fixture(args.fixture_id)
    if args.command == "install-check":
        return fixtures.command_install_check(args.fixture_id)
    if args.command == "install-verify":
        return fixtures.install_verify(args.fixture_id)
    if args.command == "spend-rebuild":
        return claude_run.spend_rebuild(write=args.write)
    if args.command == "run":
        date = args.date or datetime.date.today().strftime("%Y%m%d")
        root = Path(args.fixtures_root) if args.fixtures_root else fixtures.FIXTURES_DIR / args.split
        out = Path(args.out)
        return probes.command_run(root, out, args.split, args.fixtures, [a for a in args.arms.split(",") if a],
                                  args.seeds, args.budget_usd, date)
    if args.command == "score":
        out = Path(args.source)
        try:
            split = arms.read_json(out / "run.json")["split"]
        except (OSError, ValueError, KeyError):
            print("score: STOP run.json not found")
            return 1
        root = Path(args.fixtures_root) if args.fixtures_root else fixtures.FIXTURES_DIR / split
        return score.command_score(out, root, model=args.model)
    if args.command == "report":
        return report.write_report(args.source, args.out)
    if args.command == "pilot":
        date = args.date or datetime.date.today().strftime("%Y%m%d")
        root = Path(args.fixtures_root) if args.fixtures_root else fixtures.FIXTURES_DIR / "dev"
        out = Path(args.out)
        return pilot.command_pilot(root, out, "dev", args.fixtures, [a for a in args.arms.split(",") if a], args.seeds,
                                   args.budget_usd, date)
    if args.command == "pilot-md":
        return pilot.command_pilot_md(args.source, args.fixtures_root, args.out)
    return 2


if __name__ == "__main__":
    sys.exit(main())
