# Does the development accuracy of `/cpr:preserve` hold on fresh, blind fixtures?

id: EXP-0001
subject: SRC-0003
protocol: preregistered
read_log: reconstructed
spend_cap_usd: 55
launched_by: the repository owner

## Abstract

This is a planned study, and no results exist yet. It is exploratory, and it makes no confirmatory claim: its method was written after its authors had read the target, and the clean-room line (`docs/decisions/2026-10-21-the-clean-room-line.md`) requires the opposite order, so this protocol takes it to rule out a confirmatory claim. Its protocol is still committed before any run, which is what the header's `preregistered` records. It asks how well the `/cpr:preserve` skill of the CPR plugin, pinned at commit `cfd1e77`, keeps the facts a later session needs. The measurement uses 25 new synthetic working sessions ("fixtures"), written by a separate agent that never sees this protocol's prediction or any earlier result, and whose copy of the authoring guide has its development findings removed. Their questions are checked against two reference notes files, one empty of the session's facts and one complete, and the fixtures are then sealed, together with the list of questions that count, by a list of file hashes before the skill runs on any of them. For each fixture, in each of 3 independent repeats, the skill runs in its `quick` mode on `claude-sonnet-5-5` and updates the project's notes file, `CLAUDE.md`. A fresh `claude-sonnet-5-5` session then answers questions about the working session from that file alone, and `claude-opus-5-5` labels each answer against a written gold answer. The primary exploratory question predicts that the share of answerable questions answered correctly is no more than 0.10 below the development figure of 0.861: the lower bound of its 95% confidence interval will be above 0.761. The result supports the prediction if that lower bound is above 0.761, counts against it if the upper bound is below 0.761, and is otherwise inconclusive; an experiment that ends before its scoring is complete has the outcome not determined. The expected half-width of the interval is 0.032 to 0.037, so a true level near 0.786 would most likely come out inconclusive, with a probability of 0.67 to 0.74. Any finding will be stated relative to that threshold of 0.761, which comes from a single seed of development run r1, whose probes were partly excluded after their answers were read. It will hold only for the skill at that commit, those two models, version 2.1.302 of the Claude Code agent harness, and at most 25 fixtures (no fewer than 20) × 3 repeats, and only for fixtures whose needed facts were written to suit the skill's own six note categories. The read log is reconstructed: the evaluation harness was built by people and agents who had read the skill's text, and no read log was kept while they did.

**Under test:** the `/cpr:preserve` skill of the CPR plugin, at plugin commit `cfd1e77e07d2b3698591688e7e151d83d3ef51c3`; source record [SRC-0003](../../sources/SRC-0003/source.md).

## Question

What level of probe accuracy does the published `/cpr:preserve` skill reach on fresh, blind, held-out fixtures?

Put plainly: after the skill saves a working session into a project's `CLAUDE.md`, a fresh session tries to answer questions about that working session from the file alone. The questions that count are the ones a complete notes file lets a fresh session answer and the starting notes file does not. What share of those does it answer correctly, on sessions that nobody used while building or tuning this evaluation? The answer is exploratory, not confirmatory: the method was written after its authors had read the skill, and the fixtures were written toward the skill's own six note categories (Abstract; Threat 5).

## Hypotheses and planned analysis

This protocol is `preregistered`. The protocol was committed here before the first recorded run. It was not entered in an outside registry. (The other value, `exploratory`, means: The protocol was not committed before the first recorded run. That does not describe this protocol.) Only runs made after this protocol is committed count as data for this experiment. Its claim is exploratory in a second sense, all the same: it is not a confirmatory claim, because the method was written after its authors had read the target (the primary exploratory question, below; Threat 5).

### Terms

- **Fixture:** one scripted working session on a small invented software project. It holds a starting repository with its starting `CLAUDE.md`, the user's turns, the facts a later session would need ("needed items"), some facts it would not need, and the probe questions.
- **Probe:** a question about the session, with a short gold answer. Its type is one of four:
  - `single-fact`;
  - `knowledge-update`: a value that changed during the session, with the old value listed as a stale answer;
  - `temporal`: an order of work;
  - `abstention`: a near-miss question whose correct answer is that the session never said.
- **Arm:** what the probe session reads.
  - `floor` is the fixture's starting `CLAUDE.md`, unchanged.
  - `baseline` is the `CLAUDE.md` after `/cpr:preserve quick` has run on the session. It is the only arm under test.
  - `oracle` is the starting file plus one hand-written line for each needed item, under its intended heading.
  - `floor` and `oracle` exist only to decide which probes are scorable, and to run the judge's sanity controls.
- **Cell:** one fixture × one arm × one seed.
- **Seed:** an independent repeat of a cell. Seeds are independent repeats, not sampling control (fixing the settings that govern a model's random choices, so that a repeat gives the same output). No temperature, random seed or other sampling setting is passed to any model, and the seed number only labels the repeat. The floor and oracle files are the same in every seed; only their probe sessions repeat.
- **Judge:** `claude-opus-5-5`. It labels each probe answer `correct`, `incorrect` or `abstained`, given the question, the gold answer and the stale answers.

### Scorable probes

A probe is scorable when all four of these hold:
- it is not an abstention probe;
- its fixture spec does not flag it `excluded`;
- the floor answered it correctly in 0 of its 3 seeds;
- the oracle answered it correctly in at least 2 of its 3 seeds.

A probe that the floor or the oracle has no label for, in any seed, is dropped. The evaluation harness's behaviour at 4 or more seeds does not apply (the harness asks the oracle to be correct in more than half of its seeds, which at the 3 seeds used here is the 2 stated above). Abstention probes are never in the primary measure.

The rule is fixed by this protocol. The scorable set it yields is sealed together with the fixtures, before any baseline run (Methods, steps 7 and 8). The sealed list is the authority: the final scoring uses it, and a set re-derived in the final scoring that differs from it is a mismatch, which ends the experiment (Stopping rule). No probe is excluded, repaired or reworded after the seal.

### Primary measure

Probe accuracy on scorable probes, a share from 0 to 1, built in three steps:
1. **Per cell:** the scorable probes labelled `correct`, divided by the scorable probes that have a label.
2. **Per fixture:** the mean of its baseline cells' values, over the seeds that have one.
3. **For the experiment:** the mean of the per-fixture values, over the fixtures in the primary.

### The primary exploratory question (exploratory under the clean-room line)

- **Why exploratory.** The method was written after its authors had read the target, and the clean-room line (`docs/decisions/2026-10-21-the-clean-room-line.md`) requires the opposite order, which this protocol takes to rule out a confirmatory claim. So this question makes no confirmatory claim. Its analysis plan, its 95% interval and δ below are kept as stated all the same.
- **Threshold.** T = 0.761. It is the development primary, 0.861, minus δ = 0.10, where δ is the smallest difference this experiment treats as mattering. The 0.861 comes from a single seed of development run r1, the result set `dev-baseline-1seed-r1` (see Provenance). It is a design input, not data for this experiment.
- **Prediction.** The lower bound of the 95% confidence interval of the baseline's primary measure is above 0.761. If it is, the result supports the prediction.
- **What counts against it.** An upper bound below 0.761 counts against the prediction. That would mean the development figure overstates the held-out level by more than δ. It would not mean that the skill keeps nothing.
- **Otherwise** the outcome is inconclusive. A bound exactly equal to 0.761 is neither above nor below it, so it is inconclusive too.
- **Not determined.** An experiment that ends before its scoring is complete has the outcome not determined (Stopping rule).
- The two decisive outcomes cannot both hold, so the order in which they are checked does not matter.
- Every outcome is stated relative to this T, not as an absolute standard for the skill.
- **The likely outcome near T.** The expected half-width of the interval (the distance from its centre to either end) is 0.032 to 0.037, from two estimates of the baseline's spread, each divided by the square root of 25 fixtures and multiplied by 1.96:
  - 0.032 treats development run r1's one-seed spread of 0.100 as the spread between fixtures plus the pilot's seed-to-seed spread of 0.070, and averages the seed part over 3 seeds;
  - 0.037 uses the pilot's 3-seed spread of 0.095 directly.

  So if the true level is near 0.786, the pilot's 3-seed level, the most likely outcome is inconclusive: at those two half-widths, by a normal approximation with the fixture as the unit, its probability is 0.67 and 0.74.
- There is no second question, and no comparison of the skill with a plain prompt or with another model.

### The test that decides the primary question

A 95% percentile bootstrap confidence interval decides the primary question.
- A **confidence interval** is the range of values the data leave plausible for the true mean.
- The **bootstrap** builds it by resampling: draw n per-fixture values, with replacement, from the n observed ones, where n is the number of fixtures in the primary (at most 25); take their mean; repeat b = 10000 times; keep the middle 95% of those means, from the 2.5th to the 97.5th percentile.
- The unit is the fixture, so the 3 seeds of one fixture count once.
- The random number generator is seeded with 20261017, so the same data always give the same interval.

### Number of runs, and why

At most 25 fixtures × 3 seeds × 3 arms; at least 20 fixtures are sealed (Methods, step 7).
- **25 fixtures.** The sizing rule is the larger of 25 and the number of fixtures needed to detect a difference of δ (here, a held-out level δ away from the development figure; the rule's formula sizes a test of a mean difference against δ, and is used here only for sizing, because the primary question tests a level against T) with 80% power at a 5% false-alarm rate (the chance of reporting a difference when there is none). Power is the chance of detecting a true difference of that size. The standard deviation is the typical distance of values from their mean; that of the baseline's score across fixtures measured 0.095 to 0.100 in the pilot and development runs. The 0.095 (the pilot) and the 0.097 (the first development run) come from earlier code than commit `08ef45f` and from earlier probe wording; the 0.100 comes from development run r1, scored by the code at `08ef45f` on the first development run's `CLAUDE.md` files. At that spread the number needed is 8, so 25 is used.
- **3 seeds.** Nearly all of the interval's width comes from differences between fixtures, not between repeats. On the first estimate above, the expected half-width is about 0.032 at 3 seeds and 0.031 at 5. This estimate rests on a single measurement of seed-to-seed variation, on 5 fixtures in the pilot, scored by earlier code with earlier probe wording, so it is indicative only.

### Stopping rule

- There is one held-out run.
- A cell is re-run only when one of its runs failed an isolation check, and at most once (Exclusions). Each re-run is listed, with its cell and the check that failed. A run that fails only outcome checks is never re-run. The floor and oracle sessions that run again on a repaired fixture before the seal are part of the pre-seal check, not re-runs (Methods, step 7).
- An interrupted run, such as one stopped by a crash or by a rate limit that outlasts the one wait and retry, may be resumed under the same pins. Units that already have a recorded result are not run again.
- The run stops, and is not resumed, when any of these happens:
  - the agent harness version or a model identifier changes after the first recorded run (a version found to differ before it is handled in Methods, step 4);
  - a judge sanity control fails;
  - an isolation control or a canary fails (Methods, steps 4 and 6); a failed control is never re-run;
  - spend reaches the declared ceiling, `spend_cap_usd`, or a phase cap of the evaluation harness.

**How the experiment can end.** Each way it can end has exactly one outcome.
- **The scoring completes** (Methods, step 12). The outcome is the one the primary question's rule gives, after the missing-data rule and the audit are applied: supports, counts against or inconclusive.
- **Every other end** has the outcome **not determined**: neither supporting nor counting against the prediction, reported as such, and not re-run under this protocol. These ends are:
  1. a stop at the ceiling or at a phase cap;
  2. a change of agent harness version or model identifier after the first recorded run;
  3. fewer than 20 fixtures sealed (Methods, step 7);
  4. a failed judge sanity control, on any scoring pass;
  5. a judge call that fails an isolation check in both scoring passes, so that neither pass completes;
  6. a mismatch against the sealed set: a fixture file against the manifest, the scorable set re-derived in the final scoring against the sealed record, or a sealed labels or session file against its hash (Methods, steps 8 and 10);
  7. an isolation or canary abort: a failed isolation control or canary, C1 to C7, which is never re-run; or the scan before the seal finds a canary token or the runner's username in a transcript;
  8. a failed judge-check (Checks on the judge);
  9. any other stop before the report of Methods, step 12.

### Exclusions

- **Two kinds of check.** The per-run proof (Isolation) has isolation checks, on what a run loaded, and outcome checks, on how it ended: an error, a question to the user, a denied permission, or the per-run spend cap of $0.75 being reached (`over_cap`).
- **A failed isolation check.** The cell is kept out of scoring and re-run once, except a version or model change, which stops the experiment (stopping rule). If the re-run also fails an isolation check, the cell is excluded and listed, with its reason, and it is not re-run again.
- **Outcome checks only.**
  - A cell whose preserve run fails only outcome checks is scored on the `CLAUDE.md` that run left behind, and it is never re-run. A denied permission is also logged for isolation review by the repository owner.
  - A probe session that fails only outcome checks is not re-run; its missing answers fall under the missing-data rule.
  - A judge call that fails only outcome checks leaves its labels missing, and the second scoring pass asks for them again.
  - The evaluation harness at `08ef45f` fails a run on both kinds of check alike, and stops a batch on a failed preserve run; the port separates the two kinds.
- **Counts.** The report gives the number of isolation re-runs, the number of cells excluded after one, and the number of runs that failed only outcome checks, by check.
- A probe answer missing from a session, or a judge label that is missing or malformed, is left out of that cell's numerator and denominator. It is not scored as incorrect.
- No probe may be flagged `excluded` after the seal.
- **Missing data:**
  - The scoring command runs exactly twice: the second time immediately after the first, whatever the first showed. The second pass asks the judge again only for the labels the first left missing. There is no third pass.
  - A judge call that fails an isolation check aborts the whole pass it is in, and none of that call's labels is kept; labels from earlier calls stay cached. The second pass then asks again for every label still missing. If the second pass is aborted too, the experiment ends with the outcome not determined.
  - A floor or oracle label still missing after the second pass drops that probe as `label_missing`. This happens before the seal, so the drop is part of the sealed record.
  - A fixture that still has fewer than 3 scorable probes after the repair round (3 is the evaluation harness's own minimum) is dropped before the seal, and replaced while fewer than 3 replacements have been made, unless the authoring agent's 4 runs are used up, in which case it is dropped without one (Methods, step 7). It is named, with its reason, in the sealed record.
  - A fixture none of whose baseline seeds yields a value leaves the primary, and is listed with its reason.
  - If more than 5 of the 25 fixtures (20%) leave the primary, counting fixtures dropped before the seal and not replaced and fixtures with no baseline value, the outcome of the primary question is inconclusive. If fewer than 20 fixtures are sealed, the outcome is not determined instead.

### Checks on the judge

- **Judge sanity controls.** They run first on every scoring pass, and scoring aborts if either fails; the experiment then ends with the outcome not determined. They check the line-matching judge, not the labels on probe answers.
  - Oracle recall must be at least 0.95. Recall here is the share of a fixture's needed items that the judge finds among the oracle file's added lines, averaged over fixtures.
  - The cyclic mismatch control must give a recall of at most 0.05. It judges fixture i's oracle lines against fixture i+1's items, which should match almost nothing.
- **Judge-check.** A live run of 7 synthetic probe cases in one batch, one of them an abstention probe, and one synthetic items case of 5 lines. It runs before any held-out run. Every case must pass; a miss ends the experiment before any held-out run, with the outcome not determined.
- **Human audit.** The repository owner labels a blind sample of 100 judged baseline answers on non-abstention probes, balanced across the judge's labels.
  - Agreement is Cohen's kappa, which is agreement corrected for chance: 1 is perfect agreement, and 0 is what chance alone would give.
  - At kappa ≥ 0.6 the judge's labels stand.
  - Below 0.6, the outcome of the primary question is reported as inconclusive, and the answers are not re-scored.
  - Kappa ≥ 0.6 does not bound a judge bias as large as δ: in the worked case of Threat 12, a judge that raises the level by 0.10 still reaches a kappa of 0.94. So the judge-minus-person difference is reported beside it (Reported, not decisive).
  - How the sample is drawn and blinded is set out in Methods, step 11.

### Reported, not decisive

None of these changes the outcome of the primary question. Each that is a mean over fixtures is reported with its 95% bootstrap interval.
- **Guardrail: probe-context tokens.** The baseline probe session's input tokens minus the floor probe session's, for the same fixture and seed. It is the share of the probe session's context that the skill's file takes up.
- **Guardrail: the false-answer rate on abstention probes.** The share of labelled abstention probes whose answer is labelled neither `abstained` nor `correct`. Against the neutral gold answer of an abstention probe, a reply that abstains may be labelled `correct`.
- The number of dropped probes, with reasons, and the primary measure over all non-abstention probes with the dropped ones included.
- The pre-seal repair log: each repair, with the floor and oracle evidence that caused it, and each fixture dropped or replaced.
- The counts of isolation re-runs, of cells excluded after one, and of runs that failed only outcome checks (Exclusions).
- The held-out primary recomputed with the filter that produced T: floor and oracle seed 1 only, so a probe counts when the floor's seed-1 answer is not correct and the oracle's is.
- **The judge-minus-person difference.** On the audit sample, the baseline primary from the judge's labels minus the same from the repository owner's labels, with each label's answers weighted by the inverse of that label's sampling fraction (the share of that label's judged answers that the sample drew), so that the sample stands for the whole frame. Its interval resamples fixtures: a cluster bootstrap, which resamples whole fixtures, with all their sampled answers, rather than single answers, because answers within a fixture are correlated.
- **Answers naming more than one value.** The count of baseline answers that name more than one candidate value. An answer that names the gold answer and another value can be labelled `correct`; the judge prompt is not changed. They are counted mechanically, by a rule fixed before the seal, and the repository owner checks that rule on the audit sample. No new judge call is made.
- The primary split by each probe's `code_answers` tag, which says whether a file in the fixture's repository, other than `CLAUDE.md`, states the answer.
- Recall, precision (the share of the file's added lines that record a needed item) and placement (the share of recorded needed items with a line under a heading that fits) of needed items in the baseline file, from the line-matching judge.
- The pooled within-fixture standard deviation of the baseline primary across seeds: the spread of each fixture's seed values around that fixture's own mean, combined over fixtures as the root of the mean variance.
- Every other table in the evaluation harness's report, such as paired differences between arms (each fixture's value in one arm minus its value in another) or the Wilcoxon signed-rank test (a test of whether such paired differences centre on zero, which uses their ranks rather than their sizes), is not part of this analysis. If discussed, it is labelled exploratory, in the template's sense of not planned in this protocol.

### Number of comparisons

There is one primary question, so no correction for multiple comparisons applies.

## Materials

- **Target.** The `/cpr:preserve` skill of the CPR plugin, source record SRC-0003, at plugin commit `cfd1e77e07d2b3698591688e7e151d83d3ef51c3`.
  - Each run loads the plugin from an export of that commit, never from a working copy.
  - The skill is invoked as `/cpr:preserve quick`. The `quick` argument skips the skill's question to the user, so the skill can run with no person present.
- **Models.** Each model is recorded by the identifier the provider returns in each run's start event. A run whose identifier differs from the pin fails its isolation proof.
  - `claude-sonnet-5-5` runs the preserve runs, the probe sessions, and the scripted turns that generate the fixtures.
  - `claude-opus-5-5` is the judge. It labels all of one probe session's answers in a single call (batched). In a second kind of call, it matches the lines a file added to the fixture's items.
  - No Fable model is used, in any arm or in any pilot.
- **Sampling settings.** None are set: no temperature, random seed or effort option is passed to any model, so each runs at the agent harness's defaults. Probe sessions have no tools and are capped at 2048 output tokens and 180 seconds. Every run is capped at $0.75 of API-price-equivalent spend.
- **Agent harness.** Claude Code, version 2.1.302. T was derived on this version. If the installed version differs before the first recorded run, the new version is pinned and recorded in a Changelog entry before that run (Methods, step 4). A change after the first recorded run ends the experiment with the outcome not determined.
- **Evaluation harness.** The method code under `experiments/EXP-0001/code/`: a port of the CPR evaluation harness at commit `08ef45f9d10ad679af78ed008fe7d05f8c60c900`. It is Python 3, standard library only. The operating system and Python versions are recorded at the first live run. The code that exports, draws and scores the human audit's sample is part of the method code committed at step 2, with a test vector (Methods, step 11).
- **Prompts,** under `code/`:
  - The probe prompt asks for one short sentence per question, using only the project instructions loaded in the session. When they do not say, the answer must be exactly `UNKNOWN`. Answers come back as JSON that matches a schema.
  - The probe judge prompt gives, for each answer, the question, the gold answer, the stale answers and the model's answer, and it defines the three labels. Its output is bound to a schema and carries no rationale.
  - The items judge prompt numbers each added line, with the heading it was added under. For each line, it asks which item the line records (a needed item, a not-needed item, or neither) and whether the heading fits.
- **Task set.** 25 held-out fixtures, not yet written; at most 3 of them may be replaced before the seal (Methods, steps 6 and 7).
  - 20 are short sessions and 5 are long sessions of 30 or more turns.
  - They follow the rules of the fixture authoring guide in the method code. The authoring agent reads a copy of that guide with the passages that report development findings removed; the copy's sha256 is recorded in the seal. Each fixture has at least 5 needed items and 2 not-needed items, and at least one knowledge-update, one temporal and one abstention probe.
  - Their ids take the form `heldout-NNN-<slug>`.
- **Dates of the first and last recorded run.** None yet.
- **Spend plan.** All amounts are API-price equivalents; under a subscription they are not billed.
  - **The ceiling.** `spend_cap_usd` is 55, set at the protocol commit, before any live call. It replaces the earlier ceiling.
  - **The rule for the ceiling.** The planned path at recorded mean costs fits under $55. A bounded worst case may stop, and a stop is "not determined".
  - **The planned path** is the run with its one repair round and no replacements. Priced from the mean costs of earlier recorded runs, it comes to $53.30, which leaves $1.70 under $55:
    - evaluation: floor and oracle, 25 fixtures × 3 seeds, one pass, with their judge calls, $7.53, plus the baseline, 25 fixtures × 3 seeds, with their judge calls, $17.85, gives $25.38; with one floor-and-oracle repair round, $7.53, it is $32.91;
    - generation, accepted attempts only: $9.06;
    - control: the isolation controls, $5.25, plus the canary positive controls, $3.00, plus the live judge-check, $0.08, gives $8.33;
    - the authoring agent: at most 4 runs at $0.75 each, $3.00;
    - total: $32.91 + $9.06 + $8.33 + $3.00 = $53.30.
  - **Contingencies,** which are not part of the planned path:
    - **Replacements.** At recorded means, 3 replacements add $0.72 (all short) to $2.54 (all long) in generation, and $0.90 for their floor and oracle sessions. The total then becomes $54.92 to $56.74. This case may stop at a cap, and the stop is "not determined".
    - **Generation retries.** Like replacements, they are a contingency, bounded by the generation cap of $19.50.
  - **What counts against it.** Every call type counts against the $55: the controls, the canaries and their positive controls, the authoring agent, generation, floor and oracle, the repair round, the baseline and the judge.
  - **Call types with no recorded price** are bounded by the per-run cap of $0.75 a run. The isolation controls make at most 7 runs, so at most $5.25. A repaired fixture's re-check is at most 6 probe sessions and their judge calls, each capped. The authoring agent makes at most 4 runs, so at most $3.00. Generation retries are bounded by the generation cap. The judge calls a second scoring pass repeats are bounded by the per-run cap, and together by the evaluation cap. The repository owner's labelling time is not priced; it is not API spend.
  - **The ledger and its phase caps.** The experiment uses a fresh spend ledger, in its own temporary folder, so no earlier spend counts against it. Its phase caps are:
    - generation, which holds every generation turn, including retries and the replacements' generation: $19.50;
    - control, which holds the controls, the canaries and their positive controls, and the live judge-check: $8.50;
    - evaluation, which holds floor, oracle, the repair round, the replacements' floor and oracle sessions, baseline and scoring: $34.00.

    The live judge-check spends from the control ledger, and the replacements' floor and oracle sessions from the evaluation ledger, because that is where the evaluation harness charges them. The phase caps nest under $55. With the authoring agent's bound they sum to $65.00 ($19.50 + $34.00 + $8.50 + $3.00), so the $55 ceiling binds first: it binds their sum, whichever cap is reached first.
  - **Headroom,** at the planned figures above:
    - under the generation cap: $19.50 − $9.06 = $10.44, for generation retries and replacements;
    - under the control cap: $8.50 − $8.33 = $0.17;
    - under the evaluation cap: $34.00 − $32.91 = $1.09; at recorded means, the replacements' floor and oracle sessions, $0.90, fit inside it;
    - under $55: $55 − $53.30 = $1.70.
  - **Why the path is bounded this way.** There is at most one repair round, at most 3 replacements, no repair round for a replacement, and no replacement for a dropped replacement (Methods, step 7).
  - **A cap stop.** Any cap stop ends the experiment with the outcome not determined (Stopping rule). The decision goes back to the repository owner, and the ceiling is not raised again without the owner's word.

## Methods

The steps run in this order. A step does not start until the one before it has finished and passed. A replacement fixture (steps 6 and 7) is authored, generated and checked on floor and oracle before the seal of step 8, like the fixtures it replaces.

1. **Isolation review.** Before any live run, the port reads the run wrapper's isolation proof. Here "see" means "can read through a tool call". If runs can see the agent harness's real configuration folder, the port isolates them from it, for example by giving each run its own configuration folder. If the only remedy would put a credential in the run's environment, the repository owner rules on it, with a security review, before any live run, and nothing runs until then. A code change this needs is made before step 2. If it is made after step 2, it is recorded as a deviation made before any data was seen.
2. **Commit.** This protocol and the method code are committed, including the audit's export, draw and kappa code with its test vector. Nothing run before this commit counts as data.
3. **Declare.** `spend_cap_usd` (55) and `launched_by` (the repository owner) are set at the protocol commit, before any live call, and the Changelog records it. The fresh spend ledger is created in its own temporary folder, with the phase caps under Materials.
4. **Preflight and isolation controls.** A preflight, with no model call, checks the sandbox root, the installed agent harness version and the configuration. If the installed version is not 2.1.302, the new version is pinned, and a Changelog entry records it before the first recorded run; Threat 13 names the version T was derived on. Then six live controls run:
   - C1, a plain run with no isolation, must see the runner's own instruction files and markers, which proves the checks can fire;
   - C2, an isolated run with the plugin, must pass every check of the isolation proof;
   - C3, an isolated run without the plugin, must show the plugin absent and no hook events;
   - C4, an isolated run over a planted session log, must show the plugin's hook output;
   - C5, `/cpr:preserve quick` run headless on a resumed session, must be able to change `CLAUDE.md`;
   - C6, the preserve canary. The runner first places a file holding a random token outside the sandbox, in the runner's own configuration folder. An isolated run, with the same tools and permissions as a preserve run, is asked to read a file inside its sandbox and report its contents, and to read the canary file and report its contents. C6 passes only if all of these hold:
     - the read inside the sandbox succeeds (the positive control);
     - the transcript holds a tool call aimed at the canary path, and that call was denied;
     - the token appears nowhere in the run's output or saved transcript.

     A run in which the model declines in prose, with no such tool call, is a canary **ERROR**, not a pass, and the gate stays closed. In this run alone, a refused tool call is the expected result.

   **Gate:** no recorded baseline run happens until C6 passes, and nothing live after this step runs until all six pass. A failed isolation control, including a canary ERROR, stops the experiment with the outcome not determined. It is never re-run: a failed isolation control is not retried around.
5. **Judge-check.** It runs live, before any held-out run, as set out under Checks on the judge.
6. **Author and generate the fixtures.**
   - A fresh agent writes the 25 fixture specs. It reads only the fixture specification, a copy of the fixture authoring guide with the passages that report development findings removed, and the generation specification. It never sees this protocol's prediction or threshold, nor any development result or error analysis. The agent harness and model of the authoring agent, and the sha256 of its copy of the guide, are recorded with the seal. Its runs count against `spend_cap_usd` (Materials, spend plan).
   - **C7, the generation canary.** Before any generation turn, C6's test runs again with the widest `allowed_bash` among the 25 specs in place of a preserve run's tools, with the same positive control and the same pass rule. No generation turn runs until C7 passes. If C7 fails, the experiment stops with the outcome not determined, and C7 is not re-run. The canary is run once per run type, not per spec: a replacement spec is covered by the per-run proof of what each run loaded, and no extra canary runs for it.
   - Each spec's scripted turns are run through the isolated runner on `claude-sonnet-5-5`. Fixture generation is a live run, so it happens only after step 2.
   - A session is accepted only if every turn passes its isolation proof and the session left `CLAUDE.md` unchanged.
   - Its transcript is then normalized: only user and assistant rows are kept, reasoning blocks and account data are dropped, and sandbox paths are replaced by placeholders.
   - A resume control asks a resumed session for a token that only the conversation holds, and a fresh session for the same token. The first must answer it and the second must not.
   - Each spec gets at most 3 generation attempts. A spec that is not accepted after 3 is kept, with an `excluded` block that gives the date and reason, and, while fewer than 3 replacements have been made, the authoring agent writes a replacement, which gets its own 3 attempts. Such a replacement counts toward the limit of 3 replacements in step 7, and it gets no repair round in step 7. All of this happens before the seal and before any baseline run.
   - All 25 fixtures must pass the validator, including its rule of at least 5 long fixtures, and the clean-room lint.
7. **Floor and oracle, check and repair.**
   - One probe session runs per floor and oracle cell, 3 seeds each, with those two arms only: `python3 code/run.py run --split heldout --arms floor,oracle --seeds 3`. The port refuses a `baseline` arm on held-out fixtures until the seal commit of step 8 exists. The cells are scored in the two passes of the missing-data rule, and the scorable rule is applied.
   - **One repair round, on floor and oracle evidence only.**
     - There is at most one repair round. The blind authoring agent of step 6 makes every repair, from floor and oracle evidence only, and it repairs fixture defects only.
     - A probe the oracle cannot answer (correct in fewer than 2 of its 3 seeds), or one the floor answers correctly in any seed, goes to the repair round.
     - Every repair is logged: the fixture, the probe, what changed, and the floor and oracle evidence that caused it.
     - After the round, the floor and oracle sessions of every repaired fixture run again, all 3 seeds, on its repaired files. These sessions are part of this check, not re-runs under the stopping rule, because the files they read have changed. Each one is logged, and its spend counts against this experiment's own ledger and its declared ceiling.
   - **After the round.**
     - A probe that still fails is dropped: it is flagged `excluded`, with its reason, so it stays in its spec as evidence, and it is never edited again.
     - If dropping a probe leaves a fixture without a knowledge-update, a temporal or an abstention probe, each of which every fixture must have (Materials, task set), the whole fixture is dropped.
     - A fixture that still has fewer than 3 scorable probes is dropped too. A dropped fixture is replaced while fewer than 3 replacements have been made.
     - There are at most 3 replacements. Generation failures in step 6 and fixtures dropped here count toward the same 3; a replacement made for a generation failure counts toward them and gets no repair round.
     - A replacement is authored, generated and checked like the others, but it gets no repair round: if it fails, it is dropped. A dropped replacement is not itself replaced.
     - When the authoring agent's 4 runs are used up, no further authoring is done: a fixture that needs a replacement no one has authored is dropped without one, and fewer than 20 sealed fixtures is still 'not determined'.
     - A replacement's generation spends from the generation ledger, and its floor and oracle sessions from the evaluation ledger (Materials, spend plan).
     - If fewer than 20 fixtures are sealed, the experiment ends with the outcome not determined.
   - **Why repairs here do not tune the result.** Floor and oracle are validity checks, not the arm under test:
     - the floor shows that a question cannot be answered without the session;
     - the oracle shows that it can be answered from a complete notes file.

     Neither involves the skill, no baseline run exists yet, and the agent that repairs has seen no development result. So a repair cannot move the probe set toward what the skill writes, or away from it.
   - The last floor and oracle sessions of each fixture are the experiment's floor and oracle. They are not run again after the seal.
8. **Seal.**
   - Before sealing, every normalized transcript is scanned for the canary tokens of C6 and C7 and for the runner's username. A hit ends the experiment with the outcome not determined.
   - The sha256 hash of every fixture file is written, sorted, to a manifest.
   - Beside it, a record lists, for each fixture, the scorable probe ids, the dropped probe ids with their reasons, and the excluded probe ids; and it lists every fixture dropped or replaced, with its reason.
   - The seal also holds the sha256 of the pre-seal labels file, of every floor and oracle session file, and of the authoring agent's copy of the guide. The labels file and the session files are carried into the final results folder.
   - The manifest, the record and the repair log are committed together, in one commit, before any baseline run. Nothing from a baseline run is seen before the seal.
   - The seal commit is pushed to the public remote before step 9, and the push is recorded.
   - The fixture files stay in a private store outside this repository until the results are published. They are published with the results, so that anyone can check them against the manifest and replicate the run.
   - Every later step first checks the files against the manifest and the sealed hashes, and a mismatch ends the experiment with the outcome not determined.
   - The sealed scorable record is the authority. The final scoring must reproduce it exactly; any difference stops scoring and ends the experiment with the outcome not determined.
   - No probe is excluded, repaired or reworded after the seal.
9. **Baseline.** For each fixture and seed:
   - a fresh copy of the fixture's repository and transcript is installed in a new sandbox;
   - the session is resumed, `/cpr:preserve quick` runs, and the resulting `CLAUDE.md` is saved;
   - a probe session then starts in a fresh sandbox that holds only that file, with no tools and no plugin, and is asked all of the fixture's probes at once.
10. **Score.**
    - The judge sanity controls run first.
    - Then the judge labels every probe answer, one call per probe session. A batch's id is built from the fixture, the seed and the probe ids, never the arm, so the judge cannot tell which arm it is grading.
    - An answer of `UNKNOWN` is parsed as `abstained` and never sent to the judge.
    - Labels are cached by the answer's content, the judge model and the judge prompt, so the same answer always carries the same label. The final scoring reuses the sealed pre-seal labels for the floor and oracle answers. Final-scoring labels are written to a separate file, and the sealed pre-seal labels file is never written after the seal.
    - Scoring runs in two passes, under the missing-data rule. A judge call that fails an isolation check aborts its pass (Exclusions).
11. **Human audit.**
    - **Frame:** every baseline answer on a non-abstention probe that the judge labelled in the final scoring, over all seeds. Answers parsed as `UNKNOWN` never reach the judge, so they are not in the frame.
    - **Balance across labels:** the sample holds an equal number of answers from each judge label (`correct`, `incorrect`, `abstained`). Balancing makes the three labels equally common in the sample, which removes the effect of the label base rate on kappa: one label being far more common than the others no longer pulls kappa down.
      - 100 is not a multiple of 3, so the shares are 34, 33 and 33, with the extra answer on `incorrect`.
      - If a label holds fewer judged answers than its share, all of them are taken. The shortfall is split equally between the other two labels, and an odd answer goes to `incorrect`.
    - **Within each label:** the sample is stratified by probe type (`single-fact`, `knowledge-update`, `temporal`). Each label × type cell gets at least 5 answers, or all of them if it holds fewer. The rest of the label's share is spread in proportion to cell size, rounding by largest remainder (each cell first gets the whole-number part of its share, and the answers left over go, one each, to the cells with the largest fractional parts).
    - **Draw:** the stable id of an answer is `fixture_id/seed/probe_id`, and the list of answers is sorted by it. The draw is `random.Random(SEED).sample` over that sorted list, taken within each label × type cell. SEED is 20261022, a different seed from the bootstrap's, so the two random processes do not share one, and it is written into the sealed manifest before the draw. The code is committed at step 2, with a test vector: a fixed input and the sample it must draw.
    - **Blinding:**
      - The repository owner sees, for each answer, only what the judge saw: the question, the gold answer, the stale answers and the answer, in shuffled order.
      - The arm, fixture, seed and the judge's label are hidden.
      - A separate key file maps each item to its source and judge label. It is not opened until labelling is finished.
      - The owner applies the judge's three label definitions.
    - **Timing:** the sample is exported and labelled after scoring, and before the report is generated or any figure of the primary measure is seen.
    - **Agreement:** Cohen's kappa over all 100 decides, against the 0.6 bar. Raw agreement and kappa are also reported for each probe type. The judge-minus-person difference (Reported, not decisive) comes from the same sample.
12. **Report and decide.** The report is generated from the recorded run and score files, with no model call. The outcome of the primary question then follows from the rule above, after the missing-data rule and the audit have been applied, and is stated relative to T = 0.761. Any end before this step has the outcome not determined (Stopping rule).

### What one run is

One run is one headless invocation of the agent harness: a fixture-generation turn, a preserve run, a probe session, a judge call, a control or a canary. Each has its own folder, `recorded/runs/<run>/`.

### Order

Runs are not randomised. They run sequentially, in the evaluation harness's fixed order: by fixture, then by seed. Floor and oracle run in step 7, before the seal. In step 9, for each fixture and seed, the preserve run comes first and its probe session second.

### What each run records

Each run records its UTC start time, its cost, its token counts (input, cache creation, cache read and output), its duration, its rate-limit events, the agent harness version and model identifier it reported, and its isolation proof as counts only. Raw transcripts are never recorded, because they hold account and machine data. The full run records that the evaluation harness writes are kept outside this repository, in a folder not named `_raw`, because the repository's gate refuses any path part with that name. The recorded results hold derived files only: the resulting `CLAUDE.md`, the answers, the labels and the run records.

### Isolation

- Before any live run, the port reads the run wrapper's isolation proof. If runs can see (can read through a tool call) the agent harness's real configuration folder, they are isolated from it, for example by a per-run configuration folder (Methods, step 1). A remedy that would put a credential in the run's environment waits for the repository owner's ruling, with a security review, and nothing runs until then.
- Each live run is headless: the prompt is passed on the command line and standard input is closed, so no person can answer it.
- Each live run is passed `--setting-sources project` and `--strict-mcp-config`, and it asserts that no MCP server is loaded. An MCP (Model Context Protocol) server is a connector that gives the model extra tools or data.
- Its environment is built from a short allowlist of variables.
- Its sandbox is a new temporary directory, outside any project, with no instruction file in any parent directory.
- **The per-run isolation proof is boundary 2's per-run proof.** It runs on every run type: generation, preserve, probe and judge. It reads the run's event stream and its saved transcript, and it has two kinds of check.
  - **Isolation checks**, on what the run loaded:
    - the agent harness version and the model equal the pins;
    - a run that loads the plugin loaded it from the export, and every other run shows that no plugin loaded;
    - no MCP server is loaded;
    - skills and agents come only from the recorded allowlist and the plugin;
    - every memory file and instruction file lies inside the sandbox;
    - every hook event belongs to the plugin;
    - text markers taken from the runner's own user instruction files are absent from the transcript (they are counted, never stored);
    - a probe session had no tools.
  - **Outcome checks**, on how the run ended:
    - the run did not end in an error;
    - a preserve run asked the user nothing;
    - a preserve run had no permission denied;
    - the run did not reach the per-run spend cap.

  A failed isolation check leads to at most one re-run of the cell; a failed outcome check never leads to a re-run (Exclusions).
- **Boundary 2.** The live-runs decision asks that each run prove it could not see the runner's home directory, credentials, other repositories, session logs or connected services. The per-run proof shows what a run loaded; a canary shows what a run could read. It was ruled before this protocol was committed that the per-run proof on every run type, together with a canary for each run type that has tools, meets boundary 2. The run types with tools are preserve runs (canary C6) and generation turns (canary C7); probe sessions and judge calls have none.
- **The canaries.** Each canary has a positive control in the same run: a read inside the sandbox that must succeed, beside the read outside it that must fail. The outside read passes only if the transcript holds a tool call aimed at the canary path and that call was denied. A run in which the model declines in prose is a canary ERROR, and the gate stays closed. No recorded baseline run happens until C6 passes, and no generation turn until C7 passes. A failed canary stops the experiment with the outcome not determined, and it is never re-run (Methods, steps 4 and 6).
- Before the seal, every normalized transcript is scanned for the canary tokens and the runner's username (Methods, step 8).
- Judge calls pass the same proof. A judge call that fails an isolation check aborts the whole scoring pass, and none of that call's labels is kept (Exclusions).
- Each run records its proof as `recorded/runs/<run>/isolation.json`.
- Live runs are launched by the repository owner, never by an agent.
- Running this experiment contacts no one.

## Results

Not yet run.

## Analysis

Not yet run.

## Threats to validity

1. **Whether the measure captures the question.** Probe accuracy is a stand-in for whether a later session can do its work. Probe sessions see only `CLAUDE.md`, so a skill that points to the code instead of copying a fact from it loses the probes that the code would answer. *Done:* the primary is reported split by the `code_answers` tag. Nothing else is done.
2. **Judge leniency enters the level directly.** In a comparison between two arms, a judge that is too lenient raises both arms and the bias cancels; in a single level like this one, nothing cancels it. The judge and the model under test come from the same model family. A judge from another model family was not achieved: one was attempted, and its command-line tool could not be kept from loading the runner's user instructions. *Done:* the human audit is the only check of the labels on probe answers against a human reading, and its kappa bar does not bound a bias as large as δ (Threat 12). Besides it, the judge never learns which arm it is grading, `UNKNOWN` answers are parsed rather than judged, and the judge-check runs live before any held-out run. The sanity controls check only the line matcher.
3. **What the judge sees.** The judge is given the gold answer and the stale answers, so its task is matching, not judging truth, and its labels carry no rationale. Batching might let one answer in a call influence the label of another; this is speculation, not a measurement. An answer that names two candidate values, one of them the gold answer, can be labelled `correct`, and the audit applies the same definitions, so it cannot see this either; the labels do not measure how often it happens. *Done:* the count of baseline answers that name more than one candidate value is reported, counted mechanically by a rule fixed before the seal, which the repository owner checks on the audit sample. The judge prompt is not changed, and no new judge call is made.
4. **Fixture difficulty sets the level.** The measured level depends on how hard the authoring agent made the fixtures. That agent is from the same model family as the model under test and the judge. The threshold assumes that the held-out author follows the same authoring guide as the development author did. *Done:* the author is a fresh agent that never sees the prediction or the development results, and it reads a copy of the authoring guide with the passages that report development findings removed, whose sha256 is sealed. The set is sealed before the arm under test runs. Nothing measures difficulty itself.
5. **The method was built after reading the target, so this experiment is exploratory.** The evaluation harness was built by people and agents who had read the skill's text, and no read log was kept at the time, so the read log is reconstructed. The clean-room line (`docs/decisions/2026-10-21-the-clean-room-line.md`) requires a method to be written before its author reads the target, which this protocol takes to rule out a confirmatory claim from a method written after, so this experiment makes none. In one known place the measure follows the target: each needed item is labelled with which of the skill's own six note categories it suits, and the authoring rules require every category to be suited by at least 2 items across the set. So the fixtures are written toward what the skill is built to record, which can raise its level. The primary measure does not use the categories; the placement figure, which is reported but not decisive, does. *Done:* the experiment is labelled exploratory here, in the Abstract and in the heading of the primary question; the reading is disclosed in the read log and in the scope of the conclusion (Abstract; How to replicate). The protocol is still committed before any run. Nothing else.
6. **The threshold carries development choices with it.** T comes from a single seed of development run r1, on fixtures seen while the evaluation harness was built, after 8 of their probes had been excluded once their answers were read. T also treats 0.861 as exact, although that figure has its own sampling error. Two differences in how probes are filtered also separate T from the held-out measure:
   - **The held-out filter is stricter.** For T, a probe counted when the floor missed it in its one seed and the oracle answered it in its one. Here the floor must miss it in all 3 seeds and the oracle answer it in at least 2. A probe the floor sometimes guesses is probably easier for the baseline too, so the stricter filter likely removes easy probes that T kept, which lowers the held-out level by an amount nobody has measured.
   - **T leaves out probes that the held-out check cannot remove.** The 8 probes excluded in r1 were all baseline misses, and excluding them moved the development primary from 0.813 to 0.861. The pre-seal check cannot remove probes of that kind, because the oracle answers them, so any the held-out author writes count against the prediction.

   So a result that counts against the prediction could reflect these differences, or error in the development figure, as well as a real drop; and a result that supports it says only that the held-out level clears 0.761. *Done:* the held-out set is new, and its exclusions are sealed before any baseline run. The held-out primary recomputed with T's filter is reported (Reported, not decisive). Every conclusion is stated relative to this T. Nothing is done about the error in 0.861.
7. **The sessions are synthetic.**
   - The sessions are scripted turns in toy repositories.
   - The preserve run resumes a history that the model did not write in that process, and normalization drops the agent harness's own attachments.
   - Every run is a first session for the plugin: no earlier plugin logs exist, and the plugin's start hook does not fire on a resumed session, so the plugin's memory brief never reaches the preserve run.
   - Notes files stay under 200 lines, so the skill's archive path is untested.

   *Done:* nothing. The scope of any finding is limited to such sessions.
8. **Repeats are not controlled.** No sampling setting is fixed. Seed-to-seed variation has been measured once, on 5 fixtures in the pilot, scored by earlier code with earlier probe wording, at a standard deviation of 0.070. If it is much larger on this set, the interval is wider than planned. With 3 seeds, the evaluation harness's split-half noise estimate (for each fixture, the mean of seeds 1 and 2 minus the mean of seeds 3 and 4) cannot be computed, because it needs 4. *Done:* the pooled within-fixture standard deviation is reported.
9. **Order, time and caching.** Runs are not randomised, so anything that drifts over the hours of a run lines up with the run order. Floor and oracle also run earlier than the baseline, so the two are hours apart. A change behind an unchanged model identifier, or a change in service load, would not be seen. The provider's prompt caching changes cost and token accounting; whether it changes answers is not known. *Done:* every run checks the agent harness version and the model identifier against the pins, and a change after the first recorded run ends the experiment. The token guardrail counts cached input tokens too. Nothing is done about changes that the identifiers do not show.
10. **What the isolation proof shows.** The per-run proof is a record of what a run loaded, and of whether anything from the runner's instruction files reached the transcript. On its own it does not show that a run could not read other files on the runner's machine. Preserve runs and generation turns can use the tools they are allowed; probe sessions and judge calls have none. *Done:*
    - before any live run, the port reads the run wrapper's proof and, if runs can read the real configuration folder through a tool call, isolates them from it;
    - the per-run proof runs on every run type, and it was ruled before this protocol was committed that this proof, with a canary for each run type that has tools, meets boundary 2;
    - canary C6 tests a preserve run's tools and canary C7 the widest generation tools among the 25 specs; each passes only on a denied tool call aimed at the canary path, beside a read inside the sandbox that succeeds, so a model that only declines in prose cannot pass it;
    - every normalized transcript is scanned for the canary tokens and the runner's username before the seal;
    - C1 proves the other checks can fire;
    - recorded results hold derived files only, never raw transcripts.

    Each canary tests one file in one place. It does not prove that no other path is readable.
11. **Repairs before the seal.** Probes are repaired, or dropped, on floor and oracle evidence before the seal, in at most one round. That cannot tune the result toward the skill: floor and oracle are validity checks that do not involve the skill, no baseline run exists yet, and every repair is made by the blind authoring agent, which has seen no development result. Repairs do shape the set, though. They remove questions that a complete notes file could not answer, which the scorable rule would drop anyway, and a probe that still fails after the round is dropped, not edited again. *Done:* every repair, drop and replacement is logged and published, and nothing is repaired or excluded after the seal.
12. **The human audit.** There is one labeller, who knows the design, and 100 items invite fatigue. Because the sample is balanced across the judge's labels, kappa on it describes agreement as if the three labels were equally common, not at the rates the run produced. A label with few judged answers may not fill its share; `abstained` is the likely one, because answers of exactly `UNKNOWN` never reach the judge. A kappa of at least 0.6 does not bound a judge bias of size δ. A worked case:
    - Take a true baseline level of 0.75, and a judge that labels 40% of truly incorrect baseline answers `correct`. The judge's level is then 0.75 + 0.25 × 0.40 = 0.85, which raises the level by 0.10, that is, by δ.
    - Of the answers the judge labels `correct`, 0.10 / 0.85, about 12%, are truly incorrect: about 4 of the 33 sampled. Every other answer is taken to agree.
    - The judge's label counts in the sample are 33, 34 and 33 (`correct`, `incorrect`, `abstained`); the person's are 29, 38 and 33.
    - Observed agreement is 0.96 (96 of 100). Chance agreement, the sum over labels of the product of the two raters' shares, is 0.33 × 0.29 + 0.34 × 0.38 + 0.33 × 0.33 = 0.334.
    - Kappa is (0.96 − 0.334) / (1 − 0.334) = 0.94, well above the bar of 0.6.

    *Done:* the frame is the baseline's judged answers on non-abstention probes, the answers the primary depends on. The labeller is blind to arm, fixture and the judge's label. Raw agreement is reported beside kappa for each probe type, and the judge-minus-person difference in the primary is reported.
13. **Beyond the pins.** One model writes and reads, one model judges, and one agent harness version and one plugin commit are used. T was derived on Claude Code 2.1.302; if the agent harness is re-pinned before the first recorded run, T carries that change of version, untested. Nothing here speaks to other models, other versions, real working sessions, or fixtures that were not written around the skill's own note categories. *Done:* the conclusion is stated only for these pins and these fixtures, and only relative to T = 0.761.
14. **A spend stop.** The ceiling is $55 at API-price equivalent, and every call type counts against it. The planned path at recorded mean costs fits under $55. A bounded worst case may stop, and a stop is "not determined". The planned path, with its one repair round and no replacements, comes to $53.30 at recorded means, $1.70 under $55. Replacements and generation retries are contingencies outside it: 3 replacements at recorded means bring the total to $54.92 to $56.74, so that case may stop at a cap. The fresh ledger's evaluation phase cap is $34.00, $1.09 above the planned $32.91 (its basis of $25.38 plus one repair round of $7.53). The generation cap stays $19.50. The control phase cap, which holds the controls, the canaries and their positive controls, and the live judge-check, is $8.50, $0.17 above the planned $8.33. The authoring agent is bounded at 4 runs, $3.00. The phase caps nest under $55, and the $55 ceiling binds their sum, whichever cap comes first. Any cap stop ends the experiment with the outcome not determined, the decision goes back to the repository owner, and the ceiling is not raised again without the owner's word. *Done:* the plan is priced in advance from recorded means, call types with no recorded price are bounded by the per-run cap, the headroom under each cap is stated (Materials, spend plan), and a fresh spend ledger is used.
15. **The number of runs and of comparisons.** There are at most 25 fixtures × 3 seeds, and one primary question, so no correction for multiple comparisons applies. The figures reported but not decisive are not tested.

## Deviations from the preregistration

none

## How to reproduce

`python3 tools/reproduce.py EXP-0001` rebuilds the generated files from the recorded data and says, for each, whether it is identical to the committed copy. It does not check the copies of tables in this README, and it does not run the target or any model again; that is replication. The evaluation harness's report is built from the recorded run files and score files alone, with no model call, and the same inputs give the same bytes.

## How to replicate

**What to run.** On your own account, with Claude Code 2.1.302, `claude-sonnet-5-5`, `claude-opus-5-5`, and the plugin at the pinned commit:
- Check the published held-out fixtures against the manifest.
- Run Methods steps 1, 4 and 5: the isolation review, the isolation controls (including the canary control C6), and the judge-check. The controls must pass on your machine.
- Run the floor and oracle sessions of step 7 on the published fixtures, with no repairs: the fixtures are used exactly as published. Apply the scorable rule to your own floor and oracle results. The sealed scorable set decides your outcome. Your own scorable set is reported beside it, as a separate replication.
- Then run steps 9 to 12: the baseline, 3 seeds each; scoring; your own blind audit of 100 judged answers; and the report.
- Do not generate fixtures.

**Expected cost and time.** About $25.38 at API-price equivalent for floor and oracle ($7.53) and the baseline ($17.85), plus $0.08 for the judge-check. The isolation controls have no recorded price; each run of them is bounded by the per-run cap of $0.75. It takes about 1.1 hours of sequential call time, plus the time to label 100 answers.

**What counts as agreement.**
- **Rule.** A replication agrees when its own outcome is the same as the recorded one: supports the prediction, counts against it, or inconclusive. Its outcome is computed by the same rule as the primary question: the same T of 0.761, the same bootstrap with the fixture as the unit, the same sealed scorable set, the same missing-data rule, and the same kappa bar.
- **Reuse.** The primary question keeps its analysis plan, its 95% interval and δ = 0.10 as stated, so that a later blind replication can reuse them.
- **Unit and comparison.** The unit is the per-fixture baseline primary, and the two runs are compared through their outcomes.
- **How close.** They must be close enough to reach the same side of T.
- **Against an inconclusive record.** Agreement with an inconclusive record is uninformative: an imprecise replication lands on inconclusive too, so it agrees by default.
- **Against a record that is not determined.** There is no recorded outcome to agree with.
- **Your own audit.** Report your kappa beside your outcome. Below 0.6 your outcome is inconclusive by the same rule, so a disagreement may then come from your labelling rather than from the target.
- **Why this one.** It tests the claim this experiment actually makes, a decision against T. An imprecise replication lands on inconclusive, so it disagrees with a record that supports or counts against the prediction, instead of passing by default, as an overlap of intervals would let it.

This criterion was fixed in this protocol, before any result was seen.

**Scope of what agreement shows.** Agreement shows only that the outcome relative to T = 0.761 repeats, for the same pins and on the same fixtures. T comes from a single seed of development run r1, and those fixtures were written to suit the skill's own six note categories. Agreement says nothing beyond that.

**If a pin is no longer available.** A substitute model or agent harness version changes what is measured. A run with a substitute is a new experiment, not a replication, and it cannot agree or disagree with this one under the criterion above. Report it as a new experiment, and name the substitute.

## Provenance

- **Who runs it and pays (`launched_by`).** The repository owner. The header's `launched_by` and `spend_cap_usd` are set at the protocol commit, before the first live call.
- **Read log.** It is in `read-log.md`, and its kind is `reconstructed`. The evaluation harness was built in October 2026 by people and agents who read the skill's text, and no read log was kept at the time. Its entries can only be written afterwards, from memory and history, which is what `reconstructed` means.
- **The pilot that informed the design.** Three development result sets informed this design. They are not data for this experiment:
  - `pilot-20261017` and `dev-baseline-1seed` were scored by code earlier than commit `08ef45f`, and their probe questions were reworded afterwards;
  - `dev-baseline-1seed-r1` was re-scored with the code at `08ef45f`, and its baseline `CLAUDE.md` files are those of `dev-baseline-1seed`. The threshold T is derived from its primary measure.
- **How this protocol was drafted.** An agent (Claude Code, `claude-opus-5-5`) drafted it from written design rulings and revised it after two reviews. It called no model, and it opened no file of the target.
- **Licences.** This write-up and the recorded data are licensed CC-BY-4.0 (`LICENSE-CC-BY-4.0`), and the method code under `code/` is licensed MIT (`LICENSE`), as `docs/decisions/2026-10-21-write-ups-and-data-are-cc-by-4.md` sets out. The target is licensed MIT, as recorded in SRC-0003.

## Changelog

- 2026-10-22: protocol committed (preregistration).
- 2026-10-22: `spend_cap_usd` set to 55 and `launched_by` set to the repository owner, both before any live call.
- 2026-10-22: the read log is reconstructed from commit history. Its times are upper bounds taken from that history, not the times of the reads, and it lists what the history shows was read, not a guarantee that nothing else was read.
- 2026-10-23: clarifications made before any data. None changes the analysis plan; each states how an existing rule is read, as ruled before the first live call.
  - Final scoring and missing labels (P:130, P:132, P:291): the final scoring only reuses the sealed floor and oracle labels, and never asks the judge again for a floor or oracle label that went missing before the seal.
  - "Runs exactly twice" (P:130) applies to each scoring occasion: each pre-seal round, and the final scoring.
  - "First recorded run" (P:99 and elsewhere) means the first live call of any kind, which is the first isolation control of Methods step 4. A change of agent harness version or model identifier after that call stops the experiment with the outcome "not determined". A version found to differ at the preflight, before any live call, is handled as Methods step 4 says.
  - A probe-session isolation failure (P:120): the re-run repeats only that probe session, and the cell's preserve output stays as it was.
  - The authoring agent's spend (P:198, P:205 to P:208) is charged to the generation ledger, under the generation cap of $19.50. The planned path is unchanged.
  - A lost spend ledger (P:205, P:225): if the temporary spend ledger is lost during the experiment, it is rebuilt from the per-run records with the evaluation harness's own ledger-rebuild command before any run resumes.
  - A failed resume control (P:247) falls under the gate rule of P:221 and the catch-all end of P:115.
  - "The one wait and retry" (P:97) is one wait of 60 seconds (the evaluation harness's `rate_limit_wait_s`), then one retry.
  - The audit frame (P:294, P:399) is baseline answers on non-abstention probes, including probes that turn out not to be scorable; Threat 12's phrase "the answers the primary depends on" is a looser description of the same frame.
  - Two labels short (P:297, P:299): what happens when two judge labels fall short of their share is fixed by the audit code and its test vector, committed at Methods step 2, before any data.
  - The $3.00 for canary positive controls (P:197, P:204, P:346): each positive control runs inside its canary run, so this amount is a conservative allowance on top of the bound for the 7 control runs, not a separate set of runs.
  - Threat 5's list of where the label appears (P:367): the Question section (P:20) also states that the claim is exploratory.
  - The plugin commit (P:12): the Abstract gives the short id `cfd1e77`; the full id, `cfd1e77e07d2b3698591688e7e151d83d3ef51c3`, is at P:14 and P:172.
  - The sum of the phase caps (P:210): the $65.00 sum counts the authoring agent's bound of $3.00 as if it were a cap.
  - The plugin check (P:331) applies to the `cpr` plugin, the only plugin any run loads.
  - The rule for counting answers that name more than one value (P:160) is fixed at the seal, after this protocol's commit, and is recorded in the sealed record.
  - The cluster bootstrap for the judge-minus-person difference (P:159) uses b = 10000 resamples and the seed 20261017, the same as the primary interval (P:85).
- 2026-10-23: the method code under `code/` lands in stages: first a port of the evaluation harness to this repository's rules, then the runner features and the analysis this protocol requires. Methods step 2 is complete only when a later Changelog entry says the method code is complete. No live call has been made and no run is recorded.
- 2026-10-23: a clarification made before any data. In C7 (P:243), "the widest `allowed_bash` among the 25 specs" means the union of every spec's `allowed_bash` entries, which is at least as wide as any one spec's list.
- 2026-10-25: a clarification made before any data. The clean-room lint (P:249) is the evaluation harness's `lint` command: it runs this repository's vocabulary rules V1 to V4 and its barred-string scan over the fixture store, prints entry numbers only, and fails closed when no list is set.
