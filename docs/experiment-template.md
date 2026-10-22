# <Title of the experiment>

id: EXP-NNNN
subject: SRC-NNNN, the source record of the target; or method, when the experiment tests this repository's own method
protocol: preregistered or exploratory
read_log: contemporaneous or reconstructed
spend_cap_usd: - when there is no live run; otherwise a positive amount, such as 5 or 2.50
launched_by: - when there is no live run; otherwise who runs the experiment and pays for it

## Abstract

Write one paragraph that says what was asked, what was done and what was found. If the protocol is exploratory, the paragraph must say so. If the read log is reconstructed, it must say that too.

**Under test:** <the target, its pinned version, and a link to its source record, such as [SRC-NNNN](../../sources/SRC-NNNN/source.md); or "this repository's own method; no outside tool">

## Question

State the one question this experiment answers, in a sentence a stranger can follow.

## Hypotheses and planned analysis

List each hypothesis and say how the results will be analysed. Commit this section before the first recorded run. `preregistered` means the protocol was committed here before the first recorded run, and not entered in an outside registry. `exploratory` means the protocol was not committed before the first recorded run. The `Experiment protocols` table in `docs/vocabulary.md` is the definition.

## Materials

List what the experiment uses: the target, pinned by its source record, and the models, their versions and the environment. The method code goes under `experiments/EXP-NNNN/code/`.

## Methods

Describe the steps of the method, in the order they run.

## Results

Report the results as tables generated from the recorded data. Include each table verbatim, and mark it as generated.

## Analysis

Analyse the results as planned, and say what they show.

## Threats to validity

List what could make the results wrong.

## Deviations from the preregistration

List each departure from the protocol as committed, with its reason, or write none.

## How to reproduce

Give the command that rebuilds the generated files from the recorded data: `python3 tools/reproduce.py EXP-NNNN`.

## How to replicate

Say what to run on your own account, and what counts as agreement with the recorded results: the interval, the unit and how to compare. Give the expected cost and time.

## Provenance

Say who ran the experiment and paid for it (`launched_by`), point to the read log (`read-log.md`), and name the licences that apply.

## Changelog

List each change to this record, with its date.
