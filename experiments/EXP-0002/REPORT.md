# Evaluation report: exp0002-results

Split dev, run date 20261102. 19 fixtures, 3 seeds, arms floor, oracle, baseline, whole-values.
Pins: Claude Code 2.1.302, model claude-sonnet-5-5, plugin ref cfd1e77e07d2, judge claude-opus-5-5.
Unit of analysis: the fixture (seed means). Intervals are 95% percentile bootstrap intervals (10000 resamples, seed 20261017).

## Per-run proof counts

Isolation re-runs: 0. Cells excluded after a re-run: 0. Runs that failed only outcome checks: 0.

## Judge sanity controls

Oracle recall 1.000 (minimum 0.95). Mismatch recall 0.000 (maximum 0.05). PASS.

## Per-arm metrics

Each cell is the mean over fixtures with its interval. Primary is probe accuracy on scorable probes; Primary (all) is accuracy on every non-abstention probe, that is, the primary recomputed with the dropped probes included (for the oracle arm it is the probe-validity diagnostic). False answers are abstention probes answered with something other than an abstention (UNKNOWN, or a reply the judge labelled abstained or correct). Delta lines is the change in `wc -l` against the floor; Delta tokens is the arm's probe-session input tokens (input, cache creation and cache read) minus the floor's for the same fixture and seed. The last column is the pooled within-fixture SD of the primary across seeds.

| Arm | Primary | Primary (all) | Recall | Precision | Placement | False answers | Delta lines | Delta tokens | Within-fixture SD |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| floor | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | n/a | n/a | 0.000 [0.000, 0.000] | 0.0 [0.0, 0.0] | 0.0 [0.0, 0.0] | 0.000 |
| oracle | 0.998 [0.995, 1.000] | 0.998 [0.995, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.000] | 19.8 [19.1, 20.7] | 275.9 [254.5, 300.2] | 0.013 |
| baseline | 0.842 [0.792, 0.890] | 0.842 [0.792, 0.890] | 0.892 [0.854, 0.929] | 0.583 [0.542, 0.621] | 0.759 [0.698, 0.816] | 0.088 [0.026, 0.158] | 26.0 [24.0, 28.1] | 592.1 [521.4, 677.2] | 0.081 |
| whole-values | 0.842 [0.792, 0.890] | 0.842 [0.792, 0.890] | 0.900 [0.869, 0.932] | 0.559 [0.525, 0.592] | 0.781 [0.733, 0.826] | 0.070 [0.018, 0.132] | 26.5 [24.6, 28.5] | 620.0 [551.0, 705.8] | 0.075 |

Fixtures with no scorable probe: 0.

## Scorable probes

A probe is scorable when the floor answered it correctly in none of its seeds and the oracle answered it correctly in a majority of its seeds. A dropped probe can fail both tests, or be dropped because a floor or oracle label is missing. An excluded probe is marked in its spec as one the gold cannot score fairly; it is neither scorable nor dropped.

| Fixture | Scorable | Dropped | Floor answered correctly | Oracle in minority | Excluded |
| --- | --- | --- | --- | --- | --- |
| dev-001-csv-tally | 7 | 0 | 0 | 0 | 0 |
| dev-002-notes-api | 6 | 0 | 0 | 0 | 1 |
| dev-003-sales-etl | 7 | 0 | 0 | 0 | 0 |
| dev-004-md-linter | 10 | 0 | 0 | 0 | 0 |
| dev-005-flag-service | 6 | 0 | 0 | 0 | 1 |
| dev-006-ledger-cli | 12 | 0 | 0 | 0 | 0 |
| dev-007-link-shortener | 7 | 0 | 0 | 0 | 0 |
| dev-008-photo-renamer | 7 | 0 | 0 | 0 | 0 |
| dev-009-inventory-diff | 7 | 0 | 0 | 0 | 0 |
| dev-010-cron-expr | 6 | 0 | 0 | 0 | 1 |
| dev-011-webhook-router | 10 | 0 | 0 | 0 | 1 |
| dev-012-backup-snapshots | 8 | 0 | 0 | 0 | 0 |
| dev-013-unit-convert | 6 | 0 | 0 | 0 | 1 |
| dev-015-adventure-engine | 10 | 0 | 0 | 0 | 1 |
| dev-016-mail-templates | 6 | 0 | 0 | 0 | 1 |
| dev-017-metrics-exporter | 8 | 0 | 0 | 0 | 0 |
| dev-018-shift-planner | 7 | 0 | 0 | 0 | 0 |
| dev-019-plugin-registry | 6 | 0 | 0 | 0 | 1 |
| dev-020-ci-summary | 7 | 0 | 0 | 0 | 0 |

Dropped 0 of 143 non-abstention probes (floor answered correctly: 0; oracle in minority: 0); excluded by the spec and not counted: 8.

## Primary by whether the repo states the answer

Probe sessions see only CLAUDE.md. Each probe is tagged by whether a file in the fixture's repo, other than CLAUDE.md, states its answer (code_answers in the spec). Each cell is the primary over that group's scorable probes, as a mean over fixtures with its interval; a fixture with no scorable probe in a group is left out of that column.

| Arm | Primary, repo states it | Primary, session only |
| --- | --- | --- |
| floor | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| oracle | 1.000 [1.000, 1.000] | 0.996 [0.989, 1.000] |
| baseline | 0.867 [0.810, 0.922] | 0.794 [0.709, 0.874] |
| whole-values | 0.887 [0.836, 0.934] | 0.759 [0.647, 0.861] |

Scorable probes: repo states it 83, session only 60. Fixtures with at least one: 19 and 19.

## Paired differences against the baseline

Difference is the arm minus the baseline, per fixture. Wilcoxon is the two-sided signed-rank test (exact when n is at most 25 with no tied values); Holm adjusts across the rows that have a p-value. The floor rows and the oracle Primary row are true by construction (a probe is scorable only when the floor misses it and the oracle answers it), so they carry no test and are left out of the Holm family.

| Arm | Metric | n | Mean diff | 95% CI | Wilcoxon p | Holm p | SD of diffs |
| --- | --- | --- | --- | --- | --- | --- | --- |
| floor | Primary | 19 | -0.842 | [-0.890, -0.792] | by construction | by construction | 0.113 |
| floor | Recall | 19 | -0.892 | [-0.929, -0.854] | by construction | by construction | 0.086 |
| floor | Precision | 0 | n/a | n/a | n/a | n/a | n/a |
| floor | Placement | 0 | n/a | n/a | n/a | n/a | n/a |
| oracle | Primary | 19 | 0.157 | [0.109, 0.204] | by construction | by construction | 0.109 |
| oracle | Recall | 19 | 0.108 | [0.071, 0.146] | 0.0009 | 0.0046 | 0.086 |
| oracle | Precision | 19 | 0.417 | [0.379, 0.458] | 0.0000 | 0.0000 | 0.089 |
| oracle | Placement | 19 | 0.241 | [0.184, 0.302] | 0.0002 | 0.0013 | 0.136 |
| whole-values | Primary | 19 | 0.000 | [-0.023, 0.024] | 0.8884 | 0.8884 | 0.053 |
| whole-values | Recall | 19 | 0.008 | [-0.006, 0.026] | 0.3096 | 0.6193 | 0.036 |
| whole-values | Precision | 19 | -0.024 | [-0.045, -0.003] | 0.0602 | 0.2408 | 0.050 |
| whole-values | Placement | 19 | 0.022 | [-0.032, 0.071] | 0.1638 | 0.4915 | 0.117 |

## A/A split

Per fixture, the mean of seeds 1 and 2 minus the mean of seeds 3 and 4 (seed 5 is left out). The arm is compared with itself, so this is the noise floor of the other tables.

| Arm | Metric | n | Mean diff | 95% CI | SD |
| --- | --- | --- | --- | --- | --- |
| floor | Primary | 0 | n/a | n/a | n/a |
| floor | Recall | 0 | n/a | n/a | n/a |
| oracle | Primary | 0 | n/a | n/a | n/a |
| oracle | Recall | 0 | n/a | n/a | n/a |
| baseline | Primary | 0 | n/a | n/a | n/a |
| baseline | Recall | 0 | n/a | n/a | n/a |
| whole-values | Primary | 0 | n/a | n/a | n/a |
| whole-values | Recall | 0 | n/a | n/a | n/a |

## Probe-context tokens

The arm's mean Delta tokens (the share of the probe context its CLAUDE.md adds over the floor), and that as a ratio to the baseline's.

| Arm | Delta tokens | Ratio to baseline |
| --- | --- | --- |
| floor | 0.0 | 0.000 |
| oracle | 275.9 | 0.466 |
| baseline | 592.1 | 1.000 |
| whole-values | 620.0 | 1.047 |

## Cost

Dollar figures are the API-price equivalent of each call's reported cost; under a subscription they are not billed. Input tokens count input, cache creation and cache read.

| Call type | Arm | Calls | $ | Input tokens | Output tokens | Wall-clock s |
| --- | --- | --- | --- | --- | --- | --- |
| preserve | baseline | 57 | 8.3378 | 7038240 | 108749 | 1208.3 |
| preserve | whole-values | 57 | 8.4654 | 7045730 | 116996 | 1272.2 |
| probe | floor | 57 | 0.8561 | 218547 | 12641 | 257.5 |
| probe | oracle | 57 | 0.9655 | 234276 | 17294 | 273.9 |
| probe | baseline | 57 | 1.0610 | 252298 | 19633 | 299.7 |
| probe | whole-values | 57 | 1.0658 | 253885 | 19479 | 286.1 |
| judge | judge | 329 | 13.0440 | 1350034 | 195591 | 2219.8 |
| total |  | 671 | 33.7957 | 16393010 | 490383 | 5817.5 |

baseline preserve runs that changed CLAUDE.md: 57 of 57.
whole-values preserve runs that changed CLAUDE.md: 57 of 57.

## Missing and invalid

Probe answers missing or unparsable: 0 (left out of accuracy and of the false-answer rate, not counted as incorrect).
Judge labels invalid: 2. Judge calls this pass: 3; cache hits: 1484.

## Failure gallery

The failure gallery is withheld because it quotes the development fixtures' questions, answers and statements, which are not published.
