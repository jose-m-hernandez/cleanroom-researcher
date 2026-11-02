# A finished experiment reports observations, an outcome, conclusions and next experiments

Date: 2026-11-02

> **The rule.** When an experiment finishes, its Results section holds two subsections, `### Observations` and `### Outcome`, and its Analysis section holds two, `### Conclusions` and `### Next experiments`. Observations records what was measured, with no interpretation. Outcome applies the protocol's decision rule as committed and names the Outcome in the word that protocol defines. Conclusions says what the Outcome shows and what it does not. Next experiments lists questions the result suggests and commits to nothing. Preregistered text is never edited; a departure goes in `Deviations from the preregistration`.

## Why

A finished experiment should read as hypothesis, observations, results, conclusions and further experiments. The hypothesis is already the section `Hypotheses and planned analysis`, committed before the run. The other four needed names.

`Outcome` is the word for the decided answer, not `Results`. A subsection called Results under the section called Results repeats one word inside itself, and the repository already says Outcome: it is the column in the README table and the name of the vocabulary section that lists each experiment's outcomes.

The subsections are `###`, so no `##` heading changes. The list of `##` sections is pinned in the template, the new-experiment tool and its tests, and an existing experiment does not need new headings.

No check is added. A check that a finished experiment has the four subsections needs a machine-readable sign that it is finished, and no header key carries one.

The template carries one line under each subsection:

> ### Observations
> What was measured, with no interpretation: the number of fixtures, seeds and runs, each exclusion and re-run with its rule, and each generated table with the file it was copied from.
>
> ### Outcome
> The protocol's decision rule applied as committed: quote the one row it rests on, with its number and interval, and state the Outcome in the word that protocol defines (`docs/vocabulary.md`, Experiment outcomes).
>
> ### Conclusions
> What the Outcome shows and what it does not, for the target version, models and environment tested only, and what result would have changed it.
>
> ### Next experiments
> Each a question this result suggests, with one line on why; a proposal that commits to nothing, and an experiment only once it has its own preregistered folder.

The template's sentence under `## Analysis` changes with it. It said to "say whether the result supports it, counts against it, or is inconclusive", which are the first experiment's words. It now reads: "For each hypothesis, say which Outcome its protocol defines the result reaches, under the criterion committed above."
