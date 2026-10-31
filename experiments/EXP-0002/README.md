# Does a one-line change to `/cpr:preserve` keep more of a session's facts?

id: EXP-0002
subject: SRC-0003
protocol: preregistered
read_log: reconstructed
spend_cap_usd: 45
launched_by: the repository owner

## Abstract

This is a planned study, and no results exist yet. It is exploratory, and it makes no confirmatory claim. Its method is the evaluation harness of EXP-0001, which was written after its authors had read the target, and its read log is reconstructed. Its fixtures are development fixtures that were used while that harness was built, so they are not blind.

It compares two versions of the `/cpr:preserve` skill of the CPR plugin on the same 19 fixtures × 3 repeats:
- **A** is the published skill at plugin commit `cfd1e77`.
- **B** (`whole-values`) is A with one sentence of its skill file extended. It keeps exact numbers as well as paths, flags and identifiers. It keeps every item when the session gave a list. When the session changed a value, it records only the latest one.

The primary measure is EXP-0001's probe accuracy on scorable probes. The outcome is decided by the 95% interval of the mean per-fixture difference, B minus A, against 0 and against δ = 0.05. The expected half-width of that interval is 0.026 to 0.041. Its width depends on how differently the fixtures respond to the two versions, which has not been measured.

Any finding holds only for these fixtures, those two models, Claude Code 2.1.302 and this one change.

## Question

On sessions where the published skill's failures are known, does version B keep more of the facts a later session needs than version A, fewer, or about as many?

## Hypotheses and planned analysis

This protocol is `preregistered`: it is committed here before the first recorded run, and only runs made after that commit count as data. It is exploratory all the same, because the method was written after its authors had read the target, and because B was chosen from these fixtures' own failures (Threats 1 and 2).

### Terms

The terms are EXP-0001's: fixture, probe, arm, cell, seed, judge and scorable probe (`experiments/EXP-0001/README.md`, Terms and Scorable probes). Two arms are under test:
- `baseline` (A): the `CLAUDE.md` after `/cpr:preserve quick` from the plugin at `cfd1e77e07d2b3698591688e7e151d83d3ef51c3`;
- `whole-values` (B): the same, with the skill file replaced by `target/whole-values/SKILL.md`. That file is A's skill file with line 36 changed, and nothing else differs.

`floor` and `oracle` run as in EXP-0001, and only to decide which probes are scorable.

### Primary measure

- Per arm, as in EXP-0001:
  - per cell, the scorable probes labelled `correct`, divided by the scorable probes that have a label;
  - per fixture, the mean over seeds.
- The scorable rule is unchanged: not an abstention probe; not flagged `excluded`; the floor correct in 0 of its 3 seeds; the oracle correct in at least 2 of its 3.
- **The difference,** per fixture: B's per-fixture value minus A's, over the fixtures where both have one.

### The decision rule

- **The interval:**
  - a 95% percentile bootstrap of the mean difference, resampling fixtures, b = 10000, seeded with 20261017;
  - it is the evaluation harness's paired interval, as printed in `REPORT.md`'s table "Paired differences against the baseline", row `whole-values`, metric Primary, column "95% CI".
- **δ = 0.05,** the smallest difference this experiment treats as mattering. In development run r1 it is about 7 of the 142 scorable probes, and about a third of that run's gap of 0.139 between the baseline and the oracle. The two failure classes B addresses hold 10 of r1's 21 baseline misses (Provenance). δ is fixed here, before any run.
- **Outcomes,** checked in this order:
  1. **B better:** the lower bound is above 0.
  2. **B worse:** the upper bound is below 0.
  3. **No difference as large as δ:** the whole interval lies strictly inside (−0.05, 0.05).
  4. **Inconclusive:** anything else.
- A bound exactly equal to 0, −0.05 or 0.05 at the printed precision is not beyond it.
- When B is better and the upper bound is also below 0.05, the report says so.
- An experiment that stops before the report is "not determined", by EXP-0001's stopping rule, applied as written.

### Number of runs, and why

- **The size:** 19 fixtures × 3 seeds × 4 arms (floor, oracle, A, B). It is fixed by the development fixtures that exist, not chosen for power.
- **The variance:** the variance of a per-fixture difference is 2·σ_w²/3 + σ_int².
  - σ_w = 0.070 is the seed-to-seed spread measured once, on 5 pilot fixtures, by earlier code (EXP-0001, Threat 8).
  - σ_int is the fixture × version interaction. It is not measured: no run has had two versions.
  - Pairing removes the spread between fixtures, which is most of EXP-0001's per-fixture variance.
- **The expected half-width** is 0.026 if σ_int = 0, 0.034 if σ_int = 0.05, and 0.041 if σ_int = 0.071 (as large as EXP-0001's spread between fixtures).
- **The smallest true difference detected with 80% power** is 0.037, 0.049 and 0.059 at those three values.
  - So δ = 0.05 is detectable only if σ_int is at most about 0.05.
  - A true difference of 0 lands in outcome 3 most of the time at the first two widths.
- The report gives the observed SD of the differences.

### Reported, not decisive

- The paired differences in recall, precision and placement.
- Each arm's false-answer rate on abstention probes.
- Each arm's probe-context tokens.

There is one primary question, so no correction for multiple comparisons applies.

## Materials

- **Target:** `/cpr:preserve quick`, plugin commit `cfd1e77e07d2b3698591688e7e151d83d3ef51c3`, loaded from a pinned export (SRC-0003).
- **Version B:** `target/whole-values/SKILL.md`, with the upstream MIT notice beside it in `target/whole-values/LICENSE`. It is the target's text, so it sits under `target/`, the one place target text may go.
  - Line 36 of A reads: "Keep exact values for paths, flags and identifiers."
  - In B it reads: "Keep exact values for paths, flags, identifiers and numbers, keep every item when the session gave a list, and when the session changed a value, record only the latest one."
  - The rest of that line, and every other line, is A's.
- **Models:**
  - `claude-sonnet-5-5` runs the preserve runs and the probe sessions;
  - `claude-opus-5-5` is the judge, which never learns the arm, as in EXP-0001.
  - No sampling setting is passed to either.
- **Tools.** Preserve runs and the isolation controls run with Claude Code's default tool set, in the permission mode `acceptEdits`, with the skill's own allowed tools and `--setting-sources project`, so user-level settings, their deny rules included, do not load for these runs. Probe sessions and judge calls run with no tools. Each run record states which, as `tool_set`: `default` or `none`. The default set is kept so that both arms match the development runs; it is not a minimal set.
- **Agent harness:** Claude Code 2.1.302, the version the fixtures were generated on (each fixture's `gen.json`).
- **Fixtures:**
  - the 19 run development fixtures of the CPR evaluation harness, `dev-001` to `dev-020` without the excluded `dev-014`, as they stand in its `evals/fixtures/dev/` at commit `f83fee132ee5700effa694ec6138c2672d99dd29`;
  - this includes the probes excluded in development run r1;
  - they are used as they are: no probe is repaired, excluded or reworded for this experiment.
  - that commit sits on a branch of the repository owner's fork of the plugin that has not been published, so it cannot be fetched. The fixtures are read from it at run time and are not copied into this repository. While that branch is unpublished, a reader can check the protocol, the method code and the variant here, but cannot re-run this experiment or read the development error analysis cited under Provenance.
- **Method code:** `code/`, a copy of `experiments/EXP-0001/code/` that differs in exactly five places:
  1. `config.json`: the ceiling and the phase caps;
  2. the folder skill variants are read from: `target/` of this experiment, in place of `code/arms/`;
  3. the eval-root folder name, `cpr-eval-exp0002`, so that its ledgers and gates never mix with EXP-0001's;
  4. the recorded-results path, `experiments/EXP-0002/recorded/`, and the messages that name it;
  5. each run record's `tool_set` field (Tools, below).

  A test checks that the copy differs in these five places only. `code/README.md` is EXP-0001's, unchanged, so its command paths name EXP-0001. For this experiment, read `experiments/EXP-0002/code/` in their place.
- **Spend plan** (API-price equivalent, from EXP-0001's recorded means):
  - controls: C1-5 at most $5.25, C6 at most $0.75, judge-check $0.08, which gives $6.08;
  - floor and oracle, 19 × 3, with their judge calls: $5.72;
  - A and B, 19 × 3 each, with their judge calls: $27.13;
  - the planned total is $38.93, under a ceiling of $45, a headroom of $6.07;
  - phase caps: control $7.00, evaluation $36.00, generation $1.00 (nothing is generated);
  - the ledger is this experiment's own, so it does not count against EXP-0001's ceiling;
  - a cap stop is "not determined".

## Methods

1. **Commit** this protocol, the method-code copy and the variant, before any live call.
2. **Preflight, controls and canary.** `preflight`, then `control` (C1-5) and `canary --type preserve` (C6), then `judge-check`, all in this experiment's eval root. A failure stops the experiment as not determined, and a failed control is never re-run. There is no C7, because nothing is generated.
3. **Run.** `run --split dev --fixtures all --arms floor,oracle,baseline,whole-values --seeds 3`. The order is the harness's: by fixture, then by seed, with A and B in turn inside each, so drift over time falls on both arms alike.
4. **Score,** twice, under EXP-0001's missing-data rule.
5. **Report.** `report`, from the recorded files, with no model call. The outcome follows from the rule above.

What is left out, and why:
- **No seal.** The fixtures are not new, so there is nothing to seal them against.
- **No repair round.** Repairing on this experiment's own floor and oracle runs would change the set mid-experiment.
- **No human audit.** The judge's leniency enters both arms alike, so it largely cancels in a difference (EXP-0001, Threat 2). No judge-minus-person figure is reported.

## Results

Not yet run.

## Analysis

Not yet run.

## Threats to validity

1. **Not blind.** The fixtures, their probes and their r1 exclusions were all shaped while the harness was built, and B was chosen by reading these fixtures' failures, so B is fitted to them. A "better" result says B fixes these sessions' failures; it does not say that B generalises. *Done:* this is disclosed in the Abstract and in the scope of any finding.
2. **One change, and its costs.**
   - B was chosen to help two known failure classes.
   - Fuller lists and exact numbers may give a probe session more to infer from on abstention probes. All three false answers in r1 were inferences from added lines.
   - Recording only the latest value drops the earlier one, which no development probe asks for, so this set cannot show that cost.
   - Files may grow. *Done:* the false-answer rate, precision and tokens are reported for both arms.
3. **σ_int is unknown,** so the interval may be as wide as 0.041. *Done:* the observed SD of the differences is reported.
4. **Same-family judge,** as in EXP-0001. Leniency largely cancels in a difference, but a judge that favours one arm's style would not cancel. *Done:* the judge never learns the arm. Nothing else.
5. **The pins,** as in EXP-0001's Threat 13. *Done:* the conclusion is stated for these pins only.

## Deviations from the preregistration

none

## How to reproduce

`python3 tools/reproduce.py EXP-0002` rebuilds the generated files from the recorded data, and says for each whether it is identical to the committed copy. Until the first recorded run it prints no generated files.

## How to replicate

Use the fixtures at the named commit, Claude Code 2.1.302, the two models and the plugin at the pinned commit. Run Methods steps 2 to 5 on your own account. A replication agrees when it reaches the same one of the four outcomes. This criterion is fixed here, before any result.

## Provenance

- **Who runs it and pays (`launched_by`):** the repository owner.
- **Read log:** `read-log.md`, kind `reconstructed`.
  - The method is EXP-0001's harness, built by people and agents who read the skill's text without keeping a log at the time.
  - The one read made for this experiment, the full skill text of A, written down to change its line 36, is logged there.
- **Where B came from:** the development error analysis, `evals/ERROR-ANALYSIS.md` of the CPR evaluation harness at `f83fee1`, development run r1. Its table "The 21 scorable misses" puts 7 misses in the class "Partial: a related fragment kept, the asked part lost" and 3 in "Wrong or stale statement kept". B targets those 10.
- **Licences:**
  - This write-up is CC-BY-4.0 and the method code is MIT, as for EXP-0001.
  - `target/whole-values/SKILL.md` is the target's MIT-licensed text with one line changed; its notice is in the `LICENSE` beside it.

## Changelog

- 2026-10-31: protocol committed (preregistration). `spend_cap_usd` set to 45 and `launched_by` set to the repository owner, both before any live call.
