#!/usr/bin/env python3
"""Entry point for the evals harness: preflight, control, canary, judge-check, validate, gen-fixture, install-check, install-verify, spend-rebuild, lint, author, seal, audit-export, audit-score, run, score, report, pilot, pilot-md."""

from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import claude_run
import fixtures
import judge
import lint
import pilot
import arms
import audit
import author
import probes
import repair
import seal
import report  # noqa: E402
import score  # noqa: E402


def seal_stop(command, final, seal_dir, fixtures_root, results):
    """None when the command may go on; else its exit code (2 without --seal, 1 when the seal does not verify)."""
    if not final:
        return None
    if not seal_dir:
        print(f"{command}: REFUSED held-out results with baseline cells need --seal (P:276-282)")
        return 2
    found = seal.verify(seal_dir, fixtures_root, results)
    if found:
        print(f"{command}: STOP the seal does not verify (outcome not determined): " + ", ".join(found))
        return 1
    return None


def author_flag_refusal(args) -> str:
    """"" when the flags of `author` fit together, else the refusal label (before any gate, any read, any launch)."""
    if not args.repair:
        if args.source or args.fixtures_root:
            return "--from and --fixtures-root belong to --repair"
        return "" if args.prompt else "--prompt is required"
    if not (args.source and args.fixtures_root and args.guide and args.out):
        return "--repair needs --from, --fixtures-root, --guide and --out"
    for flag, value in (("--prompt", args.prompt), ("--extra", args.extra), ("--replaces", args.replaces),
                        ("--only-ids", args.only_ids)):
        if value is not None:
            return f"--repair cannot be used with {flag}"
    return ""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="run.py")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight", help="check the sandbox root, the claude version; no model call")
    sub.add_parser("control", help="live isolation control (five runs); needs CPR_EVAL_LIVE=1")
    can = sub.add_parser("canary", help="live canary run: C6 (preserve) or C7 (generation); needs CPR_EVAL_LIVE=1; never re-run")
    can.add_argument("--type", required=True, choices=sorted(seal.CANARY_KEYS), help="preserve (C6) or generation (C7)")
    can.add_argument("--fixtures-root", help="directory holding the fixture specs (default <code>/fixtures); C7 takes the union of their allowed_bash")
    sub.add_parser("judge-check", help="live judge controls and sanity cases; needs CPR_EVAL_LIVE=1")
    check = sub.add_parser("validate", help="validate every fixture spec under the fixtures folder; no model call")
    check.add_argument("--heldout-complete", action="store_true", help="also require the full held-out long-session count")
    check.add_argument("--fixtures-root", help="a fixture store (<id>/spec.json) instead of the in-repo fixtures folder; the set rules run on it, the lint does not")
    gen = sub.add_parser("gen-fixture", help="generate, normalize, verify and install one fixture; needs CPR_EVAL_LIVE=1")
    gen.add_argument("fixture_id")
    inst = sub.add_parser("install-check", help="resume control for an installed fixture; needs CPR_EVAL_LIVE=1")
    inst.add_argument("fixture_id")
    verify = sub.add_parser("install-verify", help="re-check an installed fixture offline (evidence gate)")
    verify.add_argument("fixture_id")
    for store_command in (gen, inst, verify):
        store_command.add_argument("--fixtures-root", help="a fixture store (<id>/spec.json) instead of the in-repo fixtures folder")
    rebuild = sub.add_parser("spend-rebuild", help="recompute spend.json from the stored run records (per-invocation costs); dry run unless --write")
    rebuild.add_argument("--write", action="store_true", help="write spend.json atomically")
    lnt = sub.add_parser("lint", help="clean-room lint of a fixture store (P:249): vocabulary rules V1-V4 and the barred-string scan; needs BARRED_STRINGS or BARRED_STRINGS_PATH")
    lnt.add_argument("fixtures_root")
    aut = sub.add_parser("author", help="one tool-less call that writes held-out specs, or with --repair repairs them; at most 4 calls, repair included, and 3 replacements; needs CPR_EVAL_LIVE=1")
    aut.add_argument("--prompt", help="the authoring prompt file (required without --repair)")
    aut.add_argument("--repair", action="store_true", help="one repair call over the floor and oracle results of --from: --out is a new full store, the repair log is written beside it as <out>.repair-log.md")
    aut.add_argument("--from", dest="source", help="with --repair: the floor and oracle results (run.json, scores.json, probes/); read only")
    aut.add_argument("--fixtures-root", help="with --repair: the fixture store to repair (copied byte for byte into --out)")
    aut.add_argument("--guide", required=True, help="the scrubbed guide file")
    aut.add_argument("--out", required=True, help="the spec store to write <id>/spec.json into (outside every clone)")
    aut.add_argument("--extra", help="a file whose text is appended to the prompt; only its sha256 is recorded")
    aut.add_argument("--only-ids", help="comma-separated ids; other blocks in the reply are not written")
    aut.add_argument("--replaces", help="comma-separated ids this call replaces; at most 3 replacements in all")
    batch = sub.add_parser("run", help="preserve runs and probe sessions for arms x fixtures x seeds; needs CPR_EVAL_LIVE=1")
    batch.add_argument("--split", default="dev", choices=fixtures.SPLITS)
    batch.add_argument("--fixtures", default="all", help="'all', or comma-separated fixture ids or id prefixes")
    batch.add_argument("--fixtures-root", help="directory holding <id>/spec.json (default <code>/fixtures/<split>)")
    batch.add_argument("--out", help="results directory (required; outside every clone); raw logs go to $TMPDIR/cpr-eval/raw/<run id>/")
    batch.add_argument("--arms", default="floor,baseline,oracle")
    batch.add_argument("--seal", help="seal folder; a baseline arm on held-out fixtures needs one whose manifest.txt is committed")
    batch.add_argument("--preseal", help="floor and oracle results the seal covers; carried into --out (held-out baseline runs)")
    batch.add_argument("--supersede", help="comma-separated held-out fixture ids: move their floor and oracle sessions and arm texts aside (never deleted) into <out>/superseded/<stamp>/, write superseded.json and rerun only them; needs --repair-log")
    batch.add_argument("--repair-log", help="the repair log (<store>.repair-log.md); every --supersede id must have its '## <id>' section in it")
    batch.add_argument("--seeds", type=int, default=5)
    batch.add_argument("--budget-usd", type=float, help="stop before a unit when the pilot ledger has grown by this much")
    batch.add_argument("--date", help="YYYYMMDD for run.json (default today)")
    scoring = sub.add_parser("score", help="judge labels and metrics for a results directory; needs CPR_EVAL_LIVE=1")
    scoring.add_argument("--from", dest="source", required=True, help="results directory written by run")
    scoring.add_argument("--fixtures-root", help="directory holding <id>/spec.json (default <code>/fixtures/<split of the run>)")
    scoring.add_argument("--model", help="judge model override (default: the judge pin)")
    scoring.add_argument("--seal", help="seal folder; required to score held-out results that hold baseline cells (final scoring)")
    rep = sub.add_parser("report", help="REPORT.md from a results directory; no model call")
    rep.add_argument("--from", dest="source", required=True, help="results directory holding run.json and scores.json")
    rep.add_argument("--out", help="directory to write into (default: the --from directory)")
    rep.add_argument("--seal", help="seal folder; required for held-out results that hold baseline cells")
    rep.add_argument("--fixtures-root", help="directory holding <id>/spec.json, for verifying the seal (default <code>/fixtures/<split of the run>)")
    rep.add_argument("--audit", help="the audit's audit.json; required for a held-out final report (P:105, P:146)")
    sl = sub.add_parser("seal", help="seal the held-out fixtures and the floor and oracle results; no model call")
    sl.add_argument("--fixtures-root", help="the held-out fixtures store (<id>/spec.json)")
    sl.add_argument("--from", dest="source", help="floor and oracle results (run.json, labels.json, probes/)")
    sl.add_argument("--repair-log", help="the repair log, copied into the seal")
    sl.add_argument("--guide", help="the guide, hashed into the seal")
    sl.add_argument("--count-rule", help="the rule for counting answers that name more than one value, recorded in the seal")
    sl.add_argument("--out", help="seal folder to create (must not exist; outside the clone, or under experiments/EXP-0001/recorded/)")
    aex = sub.add_parser("audit-export", help="the human audit's blinded sample, owner labels file and key (P:293-306); no model call")
    aex.add_argument("--from", dest="source", required=True, help="the final results directory (run.json, scores.json, probes/)")
    aex.add_argument("--seal", required=True, help="the seal folder")
    aex.add_argument("--fixtures-root", required=True, help="the held-out fixtures store (<id>/spec.json)")
    aex.add_argument("--out", required=True, help="folder to create (must not exist; outside the clone, or under experiments/EXP-0001/recorded/)")
    asc = sub.add_parser("audit-score", help="kappa, agreement by type and the judge-minus-person difference from the owner's labels (P:306, P:159); no model call")
    asc.add_argument("--key", required=True, help="key.json from audit-export")
    asc.add_argument("--labels", required=True, help="the owner's filled-in owner-labels.json")
    asc.add_argument("--out", required=True, help="folder to create for audit.json (must not exist)")
    pil = sub.add_parser("pilot", help="the dev pilot: run, score, report and PILOT.md; needs CPR_EVAL_LIVE=1")
    pil.add_argument("--fixtures", default="dev-001..dev-005", help="a range 'dev-001..dev-005', or comma-separated ids or prefixes")
    pil.add_argument("--fixtures-root", help="directory holding <id>/spec.json (default <code>/fixtures/dev)")
    pil.add_argument("--seeds", type=int, default=3)
    pil.add_argument("--arms", default="floor,baseline,oracle")
    pil.add_argument("--budget-usd", type=float, required=True, help="most the pilot ledger may grow in this invocation; above phase_caps.evaluation refuses")
    pil.add_argument("--out", help="results directory (required; outside every clone)")
    pil.add_argument("--date", help="YYYYMMDD for run.json (default today)")
    pmd = sub.add_parser("pilot-md", help="regenerate PILOT.md from a pilot results directory; no model call")
    pmd.add_argument("--from", dest="source", required=True, help="results directory written by pilot")
    pmd.add_argument("--fixtures-root", help="directory holding <id>/gen.json (default <code>/fixtures/<split of the run>)")
    pmd.add_argument("--out", help="directory to write into (default: the --from directory)")
    args = parser.parse_args(argv)
    if args.command == "author":
        refusal = author_flag_refusal(args)
        if refusal:
            print(f"author: REFUSED {refusal}")
            return 2
    store = None
    if args.command in ("gen-fixture", "install-check", "install-verify", "validate") and args.fixtures_root is not None:
        store = Path(args.fixtures_root)
        if not store.is_dir():
            print(f"{args.command}: REFUSED --fixtures-root does not exist")
            return 2
        if repair.store_has_link_or_git(store):
            print(f"{args.command}: REFUSED the fixture store holds a symlink or a git folder")
            return 2
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
    if args.command in ("run", "pilot", "report", "pilot-md", "seal", "author") and (args.out or args.command in ("run", "pilot", "seal", "author")):
        refusal = (claude_run.strict_out_refusal if args.command in ("run", "pilot") else claude_run.out_refusal)(args.out)
        if refusal:
            print(f"{args.command}: REFUSED {refusal}")
            return 2
    gated = "canary-generation" if args.command == "canary" and args.type == "generation" else args.command
    if args.command != "canary" and args.command != "seal":  # canary checks its own gate and never-re-run rule
        refusal = seal.command_gate(gated)
        if refusal:
            print(f"{args.command}: REFUSED {refusal}")
            return 2
    if args.command == "preflight":
        reason = claude_run.preflight(claude_run.load_config())
        print("preflight: PASS" if not reason else f"preflight: FAIL {reason}")
        return 0 if not reason else 1
    if args.command == "control":
        return claude_run.control()
    if args.command == "canary":
        return seal.command_canary(args.type, args.fixtures_root)
    if args.command == "seal":
        return seal.command_seal(args.fixtures_root, args.source, args.repair_log, args.guide, args.count_rule, args.out)
    if args.command == "audit-export":
        return audit.export(args.source, args.seal, args.fixtures_root, args.out)
    if args.command == "audit-score":
        return audit.score_audit(args.key, args.labels, args.out)
    if args.command == "judge-check":
        return judge.judge_check()
    if args.command == "validate":
        if store:
            return fixtures.run_validate(store, heldout_complete=args.heldout_complete, flat=True)
        return fixtures.run_validate(heldout_complete=args.heldout_complete)
    if args.command == "gen-fixture":
        if store:
            return fixtures.command_gen_fixture(args.fixture_id, root=store, flat=True)
        return fixtures.command_gen_fixture(args.fixture_id)
    if args.command == "install-check":
        if store:
            return fixtures.command_install_check(args.fixture_id, root=store, flat=True)
        return fixtures.command_install_check(args.fixture_id)
    if args.command == "install-verify":
        if store:
            return fixtures.install_verify(args.fixture_id, root=store, flat=True)
        return fixtures.install_verify(args.fixture_id)
    if args.command == "spend-rebuild":
        return claude_run.spend_rebuild(write=args.write)
    if args.command == "lint":
        return lint.command_lint(args.fixtures_root)
    if args.command == "author" and args.repair:
        return author.command_repair(args.source, args.fixtures_root, args.guide, args.out)
    if args.command == "author":
        split = lambda value: [i.strip() for i in value.split(",")] if value is not None else None  # noqa: E731
        return author.command_author(args.prompt, args.guide, args.out, args.extra, split(args.only_ids),
                                     split(args.replaces))
    if args.command == "run":
        date = args.date or datetime.date.today().strftime("%Y%m%d")
        root = Path(args.fixtures_root) if args.fixtures_root else fixtures.FIXTURES_DIR / args.split
        out = Path(args.out)
        supersede = None if args.supersede is None else [i.strip() for i in args.supersede.split(",") if i.strip()]
        return probes.command_run(root, out, args.split, args.fixtures, [a for a in args.arms.split(",") if a],
                                  args.seeds, args.budget_usd, date, seal_dir=args.seal, preseal=args.preseal,
                                  supersede=supersede, repair_log=args.repair_log)
    if args.command == "score":
        out = Path(args.source)
        try:
            split = arms.read_json(out / "run.json")["split"]
        except (OSError, ValueError, KeyError):
            print("score: STOP run.json not found")
            return 1
        root = Path(args.fixtures_root) if args.fixtures_root else fixtures.FIXTURES_DIR / split
        final = seal.is_final(arms.read_json(out / "run.json"))
        stop = seal_stop("score", final, args.seal, root, out)
        if stop is not None:
            return stop
        return score.command_score(out, root, model=args.model, seal_dir=args.seal if final else None)
    if args.command == "report":
        try:
            run = arms.read_json(Path(args.source) / "run.json")
        except (OSError, ValueError):
            run = {}
        final = seal.is_final(run)
        if final:
            root = Path(args.fixtures_root) if args.fixtures_root else fixtures.FIXTURES_DIR / run.get("split", "heldout")
            stop = seal_stop("report", final, args.seal, root, Path(args.source))
            if stop is not None:
                return stop
        if final:
            return report.write_report(args.source, args.out, seal_dir=args.seal, audit_path=args.audit)
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
