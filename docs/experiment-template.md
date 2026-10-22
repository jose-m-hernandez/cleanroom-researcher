# <Title of the experiment>

id: EXP-NNNN
subject: SRC-NNNN (the target's source record), or the word method when the experiment tests this repository's own method
protocol: preregistered or exploratory (defined under Hypotheses and planned analysis)
read_log: contemporaneous or reconstructed
spend_cap_usd: write - when there is no live run; otherwise a positive amount, such as 5 or 2.50
launched_by: write - when there is no live run; otherwise who runs the experiment and pays for it

## Abstract

Write one paragraph that says what was asked, what was done and what was found. If the protocol is exploratory, the paragraph must say so. If the read log is reconstructed, it must say that too. State the finding with its scope: the target at its pinned version, the models, and the number of recorded runs. Claim nothing here that the Analysis section does not support.

**Under test:** <the target, its pinned version, and a link to its source record, such as [SRC-NNNN](../../sources/SRC-NNNN/source.md); or "this repository's own method; no outside tool">

## Question

State the one question this experiment answers, in a sentence a stranger can follow.

## Hypotheses and planned analysis

For each hypothesis, give the prediction as an observable result: the measure, its unit, and the direction or threshold. Then give the result that would count against it. Name the primary measure, the number of runs and why that number, the stopping rule, which runs will be excluded and on what rule, and the test or comparison that decides each hypothesis. Commit this section before the first recorded run. If the protocol is exploratory, open this section with: "Exploratory: nothing here was committed before the first recorded run. Written on <date>, after <n> runs." Any hypothesis below is one to test in a later experiment, not one this experiment tested. `preregistered` means the protocol was committed here before the first recorded run, and not entered in an outside registry. `exploratory` means the protocol was not committed before the first recorded run. The `Experiment protocols` table in `docs/vocabulary.md` is the definition.

## Materials

List what the experiment uses: the target, pinned by its source record; each model, by the exact identifier the provider returned, with its sampling settings (temperature, maximum tokens, seed if any); the agent harness or evaluation harness and its version; the operating system and runtime versions; the task set or prompts; and the dates of the first and last recorded run. Put the method code under `experiments/EXP-NNNN/code/`.

## Methods

Describe the steps of the method, in the order they run. Say what one run is, how runs were ordered or randomised, what each run records, and, for a live run, how it was isolated (the `isolation.json` proof each run folder holds).

## Results

Report the results as tables generated from the recorded data. Include each table verbatim, and under each table name the generated file it was copied from. Report every recorded run, including failed and aborted ones, and list each excluded run with the rule that excluded it. Give the number of runs behind each figure and its spread or interval.

## Analysis

Analyse the results as planned. For each hypothesis, say whether the result supports it, counts against it, or is inconclusive under the criterion committed above. Label any analysis that was not planned as exploratory. State the conclusion only for the target version, models and environment tested, and say what result would have changed it.

## Threats to validity

List what could make the results wrong, and for each say what was done about it, or that nothing was. Cover at least: whether the measure captures the question; confounds such as model changes between runs, caching, network, or the author having read the target's method before designing the measure (see the read log); whether the result holds beyond the pinned version, models and environment; and the number of runs and the number of comparisons made.

## Deviations from the preregistration

List each departure from the protocol as committed, with its reason, and say whether it was made before or after any recorded data was seen; or write `none`. In an exploratory experiment no protocol was committed first: write `not applicable; exploratory`.

## How to reproduce

Give the command that rebuilds the generated files and says for each whether it is identical to the committed copy: `python3 tools/reproduce.py EXP-NNNN`. This rebuilds and compares the generated files only. It does not check the copies in this README, and it does not run the target again; that is replication.

## How to replicate

Say what to run on your own account, and what counts as agreement with the recorded results: the unit measured, how the two are compared, and how close they must be. Give the expected cost and time. Say whether this agreement criterion was fixed before the results were seen. If a pinned model or target version is no longer available, say what a replicator should substitute and how that weakens a comparison.

## Provenance

Say who ran the experiment and paid for it (`launched_by`), point to the read log (`read-log.md`), and name the licences that apply.

## Changelog

List each change to this record, with its date.
