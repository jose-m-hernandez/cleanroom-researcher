# Evaluation report: synthetic-demo

Split dev, run date 20260101. 6 fixtures, 5 seeds, arms floor, baseline, oracle.
Pins: Claude Code 0.0.0, model model-under-test, plugin ref 0123456789ab, judge judge-model.
Unit of analysis: the fixture (seed means). Intervals are 95% percentile bootstrap intervals (10000 resamples, seed 12345).

## Judge sanity controls

Oracle recall 0.972 (minimum 0.95). Mismatch recall 0.000 (maximum 0.05). PASS.

## Per-arm metrics

Each cell is the mean over fixtures with its interval. Primary is probe accuracy on scorable probes; Primary (all) is accuracy on every non-abstention probe, that is, the primary recomputed with the dropped probes included (for the oracle arm it is the probe-validity diagnostic). False answers are abstention probes answered with something other than an abstention (UNKNOWN, or a reply the judge labelled abstained or correct). Delta lines is the change in `wc -l` against the floor; Delta tokens is the arm's probe-session input tokens (input, cache creation and cache read) minus the floor's for the same fixture and seed. The last column is the pooled within-fixture SD of the primary across seeds.

| Arm | Primary | Primary (all) | Recall | Precision | Placement | False answers | Delta lines | Delta tokens | Within-fixture SD |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| floor | 0.000 [0.000, 0.000] | 0.200 [0.194, 0.206] | 0.000 [0.000, 0.000] | n/a | n/a | 0.507 [0.470, 0.533] | 0.0 [0.0, 0.0] | 0.0 [0.0, 0.0] | 0.000 |
| baseline | 0.447 [0.357, 0.549] | 0.673 [0.629, 0.724] | 0.507 [0.484, 0.527] | 0.546 [0.535, 0.557] | 0.719 [0.699, 0.739] | 0.291 [0.251, 0.338] | 11.8 [10.5, 12.8] | 237.6 [221.7, 252.4] | 0.060 |
| oracle | 0.954 [0.940, 0.967] | 0.964 [0.959, 0.969] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 0.213 [0.187, 0.235] | 9.0 [8.7, 9.2] | 192.7 [184.1, 201.4] | 0.027 |

Fixtures with no scorable probe: 0.

## Scorable probes

A probe is scorable when the floor answered it correctly in none of its seeds and the oracle answered it correctly in a majority of its seeds. A dropped probe can fail both tests, or be dropped because a floor or oracle label is missing. An excluded probe is marked in its spec as one the gold cannot score fairly; it is neither scorable nor dropped.

| Fixture | Scorable | Dropped | Floor answered correctly | Oracle in minority | Excluded |
| --- | --- | --- | --- | --- | --- |
| dev-901-alpha | 5 | 0 | 0 | 0 | 0 |
| dev-902-bravo | 4 | 1 | 1 | 0 | 0 |
| dev-903-charlie | 6 | 2 | 1 | 1 | 0 |
| dev-904-delta | 3 | 0 | 0 | 0 | 0 |
| dev-905-echo | 5 | 1 | 1 | 0 | 0 |
| dev-906-foxtrot | 4 | 2 | 1 | 1 | 0 |

Dropped 6 of 48 non-abstention probes (floor answered correctly: 4; oracle in minority: 2); excluded by the spec and not counted: 0.

## Paired differences against the baseline

Difference is the arm minus the baseline, per fixture. Wilcoxon is the two-sided signed-rank test (exact when n is at most 25 with no tied values); Holm adjusts across the rows that have a p-value. The floor rows and the oracle Primary row are true by construction (a probe is scorable only when the floor misses it and the oracle answers it), so they carry no test and are left out of the Holm family.

| Arm | Metric | n | Mean diff | 95% CI | Wilcoxon p | Holm p | SD of diffs |
| --- | --- | --- | --- | --- | --- | --- | --- |
| floor | Primary | 6 | -0.447 | [-0.549, -0.357] | by construction | by construction | 0.133 |
| floor | Recall | 6 | -0.507 | [-0.527, -0.484] | by construction | by construction | 0.030 |
| floor | Precision | 0 | n/a | n/a | n/a | n/a | n/a |
| floor | Placement | 0 | n/a | n/a | n/a | n/a | n/a |
| oracle | Primary | 6 | 0.507 | [0.412, 0.588] | by construction | by construction | 0.121 |
| oracle | Recall | 6 | 0.493 | [0.473, 0.516] | 0.0312 | 0.0938 | 0.030 |
| oracle | Precision | 6 | 0.454 | [0.443, 0.465] | 0.0312 | 0.0938 | 0.015 |
| oracle | Placement | 6 | 0.281 | [0.261, 0.301] | 0.0312 | 0.0938 | 0.028 |

## A/A split

Per fixture, the mean of seeds 1 and 2 minus the mean of seeds 3 and 4 (seed 5 is left out). The arm is compared with itself, so this is the noise floor of the other tables.

| Arm | Metric | n | Mean diff | 95% CI | SD |
| --- | --- | --- | --- | --- | --- |
| floor | Primary | 6 | 0.000 | [0.000, 0.000] | 0.000 |
| floor | Recall | 6 | 0.000 | [0.000, 0.000] | 0.000 |
| baseline | Primary | 6 | 0.001 | [-0.050, 0.058] | 0.073 |
| baseline | Recall | 6 | -0.105 | [-0.165, -0.034] | 0.091 |
| oracle | Primary | 6 | 0.005 | [-0.002, 0.014] | 0.011 |
| oracle | Recall | 6 | 0.000 | [0.000, 0.000] | 0.000 |

## Probe-context tokens

The arm's mean Delta tokens (the share of the probe context its CLAUDE.md adds over the floor), and that as a ratio to the baseline's.

| Arm | Delta tokens | Ratio to baseline |
| --- | --- | --- |
| floor | 0.0 | 0.000 |
| baseline | 237.6 | 1.000 |
| oracle | 192.7 | 0.811 |

## Cost

Dollar figures are the API-price equivalent of each call's reported cost; under a subscription they are not billed. Input tokens count input, cache creation and cache read.

| Call type | Arm | Calls | $ | Input tokens | Output tokens | Wall-clock s |
| --- | --- | --- | --- | --- | --- | --- |
| preserve | baseline | 30 | 2.4000 | 250000 | 18000 | 420.5 |
| probe | floor | 30 | 0.9000 | 400000 | 6000 | 150.2 |
| probe | baseline | 30 | 1.0000 | 410000 | 6200 | 160.8 |
| probe | oracle | 30 | 1.0000 | 412000 | 6100 | 158.4 |
| judge | judge | 210 | 6.3000 | 300000 | 21000 | 1500.0 |
| total |  | 330 | 11.6000 | 1772000 | 57300 | 2389.9 |

baseline preserve runs that changed CLAUDE.md: 29 of 30.

## Missing and invalid

Probe answers missing or unparsable: 2 (left out of accuracy and of the false-answer rate, not counted as incorrect).
Judge labels invalid: 1. Judge calls this pass: 210; cache hits: 95.

## Failure gallery

### Baseline probe failures (first failing seed per scorable probe)

| Fixture | Probe | Seed | Label | Question | Expected | Answer |
| --- | --- | --- | --- | --- | --- | --- |
| dev-901-alpha | p2 | 1 | incorrect | Which setting does the demo service read first? | The environment variable | The config file \| not the environment |
| dev-903-charlie | p4 | 2 | abstained | What is the retry limit now? | 5 | UNKNOWN |

### Needed items the baseline never matched

| Fixture | Item | Statement |
| --- | --- | --- |
| dev-902-bravo | i3 | The demo importer skips rows with no id. |
| dev-904-delta | i5 | Release notes are written after the tag. |
