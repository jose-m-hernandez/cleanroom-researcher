"""REPORT.md from a results directory (run.json and scores.json only).

The output is a pure function of those two files: no clock, no environment, fixed float formatting, so
regenerating from the same directory gives the same bytes.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "runner"))
import arms  # noqa: E402
import claude_run  # noqa: E402
import stats  # noqa: E402

BOOT_B = 10000
CI_ALPHA = 0.05
PAIRED_METRICS = (("primary", "Primary"), ("recall", "Recall"), ("precision", "Precision"), ("placement", "Placement"))
AA_METRICS = (("primary", "Primary"), ("recall", "Recall"))


class ReportError(Exception):
    """The results directory cannot be reported; the message is a short label."""


def fmt(x, nd: int = 3) -> str:
    """Fixed float formatting; None is n/a and negative zero is zero."""
    if x is None:
        return "n/a"
    s = "%.*f" % (nd, x)
    return s[1:] if s.startswith("-") and float(s) == 0 else s


def cell(values, rng_seed, nd: int = 3) -> str:
    """mean [lo, hi] over fixtures, or n/a."""
    values = [v for v in values if v is not None]
    if not values:
        return "n/a"
    lo, hi = stats.bootstrap_ci(values, b=BOOT_B, alpha=CI_ALPHA, rng_seed=rng_seed)
    return "%s [%s, %s]" % (fmt(stats.mean(values), nd), fmt(lo, nd), fmt(hi, nd))


def esc(text) -> str:
    return str(text).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def table(header: list, rows: list) -> list:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(" --- " for _ in header) + "|"]
    lines += ["| " + " | ".join(esc(c) for c in row) + " |" for row in rows]
    return lines + [""]


BY_CONSTRUCTION = "by construction"


def by_construction(arm: str, metric: str) -> bool:
    """True for the rows the scorable filter fixes in advance: every floor row and the oracle's Primary."""
    return arm == "floor" or (arm == "oracle" and metric == "primary")


def metric_by_fixture(scores, arm, metric) -> dict:
    return {f: v[metric] for f, v in scores["per_fixture"][arm].items()}


def seed_lists(scores, arm, metric) -> dict:
    """fixture -> per-seed values in seed order."""
    out = {}
    for f, by_seed in scores["per_seed"][arm].items():
        out[f] = [by_seed[s][metric] for s in sorted(by_seed, key=int)]
    return out


def load(results_dir) -> tuple:
    d = Path(results_dir)
    try:
        return arms.read_json(d / "run.json"), arms.read_json(d / "scores.json")
    except (OSError, ValueError):
        raise ReportError("run.json or scores.json missing or unreadable")


def build_markdown(run: dict, scores: dict) -> str:
    seed = run["rng_seed"]
    names = scores["arms"]
    fids = scores["fixtures"]
    out = [f"# Evaluation report: {run['run_id']}", ""]
    out.append("Split %s, run date %s. %d fixtures, %d seeds, arms %s." % (
        run["split"], run["date"], len(fids), scores["seeds"], ", ".join(names)))
    out.append("Pins: Claude Code %s, model %s, plugin ref %s, judge %s." % (
        run["claude_code_version"], run["model"], str(run["plugin_ref"])[:12], scores.get("judge_model") or run["judge_model"]))
    out.append("Unit of analysis: the fixture (seed means). Intervals are %d%% percentile bootstrap intervals "
               "(%d resamples, seed %s)." % (round((1 - CI_ALPHA) * 100), BOOT_B, seed))
    out.append("")

    counts = run.get("proof_counts")
    if isinstance(counts, dict):
        by_check = counts.get("outcome_only_by_check") or {}
        out += ["## Per-run proof counts", "",
                "Isolation re-runs: %d. Cells excluded after a re-run: %d. Runs that failed only outcome checks: %d%s." % (
                    counts.get("isolation_reruns", 0), counts.get("excluded_after_rerun", 0), sum(by_check.values()),
                    " (" + ", ".join(f"{k}: {v}" for k, v in sorted(by_check.items())) + ")" if by_check else ""), ""]
        for entry in run.get("excluded_cells", []):
            out.append("- Excluded: %s, %s, seed %s, %s run; failed %s." % (
                entry.get("fixture"), entry.get("arm"), entry.get("seed"), entry.get("kind"), ", ".join(entry.get("checks", []))))
        if run.get("excluded_cells"):
            out.append("")

    s = scores["sanity"]
    out += ["## Judge sanity controls", "",
            "Oracle recall %s (minimum %s). Mismatch recall %s (maximum %s). %s." % (
                fmt(s["oracle_recall"]), fmt(s["oracle_recall_min"], 2), fmt(s["mismatch_recall"]),
                fmt(s["mismatch_recall_max"], 2), "PASS" if s["ok"] else "FAIL"), ""]

    rows = []
    for a in names:
        row = [a]
        for m in ("primary", "primary_all", "recall", "precision", "placement", "false_answer"):
            row.append(cell(metric_by_fixture(scores, a, m).values(), seed))
        row.append(cell(metric_by_fixture(scores, a, "d_lines").values(), seed, 1))
        row.append(cell(metric_by_fixture(scores, a, "d_tokens").values(), seed, 1))
        row.append(fmt(stats.within_fixture_sd(seed_lists(scores, a, "primary"))))
        rows.append(row)
    out += ["## Per-arm metrics", "", "Each cell is the mean over fixtures with its interval. Primary is probe accuracy on "
            "scorable probes; Primary (all) is accuracy on every non-abstention probe, that is, the primary recomputed "
            "with the dropped probes included (for the oracle arm it is the probe-validity diagnostic). False answers "
            "are abstention probes answered with something other than an abstention (UNKNOWN, or a reply the judge labelled "
            "abstained or correct). Delta lines is the change in `wc -l` "
            "against the floor; Delta tokens is the arm's probe-session input tokens (input, cache creation and cache "
            "read) minus the floor's for the same fixture and seed. The last column is the pooled within-fixture SD "
            "of the primary across seeds.", ""]
    out += table(["Arm", "Primary", "Primary (all)", "Recall", "Precision", "Placement", "False answers",
                  "Delta lines", "Delta tokens", "Within-fixture SD"], rows)
    none_scorable = [f for f in fids if scores["scorable_counts"][f] == 0]
    out.append("Fixtures with no scorable probe: %d%s." % (len(none_scorable), (" (" + ", ".join(none_scorable) + ")") if none_scorable else ""))
    out.append("")

    rows = [[f, str(scores["scorable_counts"][f]), str(len(scores["dropped"][f])),
             str(sum(1 for r in scores["dropped"][f].values() if "floor_correct" in r)),
             str(sum(1 for r in scores["dropped"][f].values() if "oracle_minority" in r)),
             str(len(scores.get("excluded", {}).get(f, [])))] for f in fids]
    out += ["## Scorable probes", "", "A probe is scorable when the floor answered it correctly in none of its seeds and "
            "the oracle answered it correctly in a majority of its seeds. A dropped probe can fail both tests, or be dropped because a floor or oracle label is missing. An excluded "
            "probe is marked in its spec as one the gold cannot score fairly; it is neither scorable nor dropped.", ""]
    out += table(["Fixture", "Scorable", "Dropped", "Floor answered correctly", "Oracle in minority", "Excluded"], rows)
    missing = sum(1 for d in scores["dropped"].values() for r in d.values() if "label_missing" in r)
    out.append("Dropped %d of %d non-abstention probes (floor answered correctly: %d; oracle in minority: %d%s); "
               "excluded by the spec and not counted: %d." % (
        scores["dropped_total"], scores["non_abstention_total"], scores["dropped_floor"], scores["dropped_oracle"],
        "; label missing: %d" % missing if missing else "",
        sum(len(v) for v in scores.get("excluded", {}).values())))
    out.append("")

    if "scorable_split" in scores:
        split = scores["scorable_split"].values()
        out += ["## Primary by whether the repo states the answer", "",
                "Probe sessions see only CLAUDE.md. Each probe is tagged by whether a file in the fixture's repo, other "
                "than CLAUDE.md, states its answer (code_answers in the spec). Each cell is the primary over that "
                "group's scorable probes, as a mean over fixtures with its interval; a fixture with no scorable probe "
                "in a group is left out of that column.", ""]
        out += table(["Arm", "Primary, repo states it", "Primary, session only"],
                     [[a, cell(metric_by_fixture(scores, a, "primary_code").values(), seed),
                       cell(metric_by_fixture(scores, a, "primary_nocode").values(), seed)] for a in names])
        out.append("Scorable probes: repo states it %d, session only %d. Fixtures with at least one: %d and %d." % (
            sum(c["code"] for c in split), sum(c["nocode"] for c in split),
            sum(1 for c in split if c["code"] > 0), sum(1 for c in split if c["nocode"] > 0)))
        out.append("")

    # Paired differences against the baseline.
    out += ["## Paired differences against the baseline", ""]
    if "baseline" not in names:
        out += ["No baseline arm in this run.", ""]
    else:
        pending, wilc = [], []
        for a in names:
            if a == "baseline":
                continue
            for m, label in PAIRED_METRICS:
                diffs = stats.paired_diffs(metric_by_fixture(scores, a, m), metric_by_fixture(scores, "baseline", m))
                w = stats.wilcoxon(diffs) if diffs else None
                pending.append((a, label, diffs, w, by_construction(a, m)))
                if w and not by_construction(a, m):
                    wilc.append(w["p"])
        adjusted = iter(stats.holm(wilc))
        rows = []
        for a, label, diffs, w, fixed in pending:
            if not diffs:
                rows.append([a, label, "0", "n/a", "n/a", "n/a", "n/a", "n/a"])
                continue
            lo, hi = stats.bootstrap_ci(diffs, b=BOOT_B, alpha=CI_ALPHA, rng_seed=seed)
            sd = stats.stdev(diffs) if len(diffs) > 1 else None
            p_cells = [BY_CONSTRUCTION, BY_CONSTRUCTION] if fixed else [fmt(w["p"], 4), fmt(next(adjusted), 4)]
            rows.append([a, label, str(len(diffs)), fmt(stats.mean(diffs)), "[%s, %s]" % (fmt(lo), fmt(hi))] + p_cells
                        + [fmt(sd)])
        out += ["Difference is the arm minus the baseline, per fixture. Wilcoxon is the two-sided signed-rank test "
                "(exact when n is at most 25 with no tied values); Holm adjusts across the rows that have a p-value. "
                "The floor rows and the oracle Primary row are true by construction (a probe is scorable only when the "
                "floor misses it and the oracle answers it), so they carry no test and are left out of the Holm family.", ""]
        out += table(["Arm", "Metric", "n", "Mean diff", "95% CI", "Wilcoxon p", "Holm p", "SD of diffs"], rows)

    # A/A split.
    rows = []
    for a in names:
        for m, label in AA_METRICS:
            diffs = list(stats.aa_split(seed_lists(scores, a, m)).values())
            if diffs:
                lo, hi = stats.bootstrap_ci(diffs, b=BOOT_B, alpha=CI_ALPHA, rng_seed=seed)
                rows.append([a, label, str(len(diffs)), fmt(stats.mean(diffs)), "[%s, %s]" % (fmt(lo), fmt(hi)),
                             fmt(stats.stdev(diffs) if len(diffs) > 1 else None)])
            else:
                rows.append([a, label, "0", "n/a", "n/a", "n/a"])
    out += ["## A/A split", "", "Per fixture, the mean of seeds 1 and 2 minus the mean of seeds 3 and 4 (seed 5 is left "
            "out). The arm is compared with itself, so this is the noise floor of the other tables.", ""]
    out += table(["Arm", "Metric", "n", "Mean diff", "95% CI", "SD"], rows)

    # Probe-context tokens.
    tokens = {a: stats.seed_mean(list(metric_by_fixture(scores, a, "d_tokens").values())) for a in names}
    base = tokens.get("baseline")
    rows = []
    for a in names:
        ratio = fmt(tokens[a] / base) if (base is not None and base > 0 and tokens[a] is not None) else "n/a"
        rows.append([a, fmt(tokens[a], 1), ratio])
    out += ["## Probe-context tokens", "", "The arm's mean Delta tokens (the share of the probe context its CLAUDE.md "
            "adds over the floor), and that as a ratio to the baseline's.", ""]
    out += table(["Arm", "Delta tokens", "Ratio to baseline"], rows)

    # Cost.
    rows, totals = [], {"n": 0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "wall_s": 0.0}
    for kind in ("preserve", "probe", "judge"):
        for a in sorted(scores["calls"].get(kind, {}), key=lambda x: (names.index(x) if x in names else len(names), x)):
            c = scores["calls"][kind][a]
            rows.append([kind, a, str(c["n"]), fmt(c["cost_usd"], 4), str(int(c["input_tokens"])),
                         str(int(c["output_tokens"])), fmt(c["wall_s"], 1)])
            for k in totals:
                totals[k] += c[k]
    rows.append(["total", "", str(totals["n"]), fmt(totals["cost_usd"], 4), str(int(totals["input_tokens"])),
                 str(int(totals["output_tokens"])), fmt(totals["wall_s"], 1)])
    out += ["## Cost", "", "Dollar figures are the API-price equivalent of each call's reported cost; under a "
            "subscription they are not billed. Input tokens count input, cache creation and cache read.", ""]
    out += table(["Call type", "Arm", "Calls", "$", "Input tokens", "Output tokens", "Wall-clock s"], rows)
    for a in sorted(scores["preserve"]):
        info = scores["preserve"][a]
        out.append("%s preserve runs that changed CLAUDE.md: %d of %d." % (a, info["changed"], info["runs"]))
    out.append("")

    inv = scores["invalid"]
    out += ["## Missing and invalid", "",
            "Probe answers missing or unparsable: %d (left out of accuracy and of the false-answer rate, not counted as incorrect)."
            % inv["probe_answers_missing"],
            "Judge labels invalid: %d. Judge calls this pass: %d; cache hits: %d." % (
                inv["judge_labels_invalid"], scores["judge"]["calls_made"], scores["judge"]["cached_hits"]), ""]

    out += ["## Failure gallery", "", "### Baseline probe failures (first failing seed per scorable probe)", ""]
    g = scores["gallery"]
    if g["probes"]:
        out += table(["Fixture", "Probe", "Seed", "Label", "Question", "Expected", "Answer"],
                     [[x["fixture"], x["probe"], str(x["seed"]), x["label"], x["question"], x["gold"], x["answer"]]
                      for x in g["probes"]])
    else:
        out += ["None.", ""]
    out += ["### Needed items the baseline never matched", ""]
    if g["missed_items"]:
        out += table(["Fixture", "Item", "Statement"], [[x["fixture"], x["item"], x["statement"]] for x in g["missed_items"]])
    else:
        out += ["None.", ""]
    return "\n".join(out).rstrip("\n") + "\n"


def write_report(results_dir, out_dir=None, stdout=None) -> int:
    """Write REPORT.md into out_dir (default: results_dir). Exit 0, or 1 with a label."""
    stdout = stdout or sys.stdout
    for refusal in (claude_run.from_refusal(None if results_dir is None else str(results_dir)),
                    claude_run.out_refusal(str(out_dir)) if out_dir else ""):
        if refusal:
            print(f"report: REFUSED {refusal}", file=stdout)
            return 2
    try:
        run, scores = load(results_dir)
        md = build_markdown(run, scores)
    except ReportError as exc:
        print(f"report: STOP {exc}", file=stdout)
        return 1
    except KeyError as exc:
        print(f"report: STOP results incomplete: missing {exc.args[0]}", file=stdout)
        return 1
    dest = Path(out_dir) if out_dir else Path(results_dir)
    claude_run.atomic_write(dest / "REPORT.md", md)
    print(f"report: wrote REPORT.md ({len(md.splitlines())} lines)", file=stdout)
    return 0
