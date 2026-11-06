# Preregistered sections take labelled addenda, never edits

Date: 2026-11-05

> **The rule.** Preregistered text is never edited, moved or deleted. Text may be added inside a preregistered section only as a dated addendum, set off and labelled ("Added <YYYY-MM-DD>, after the recorded run:"), and listed in the experiment's Changelog. The check: the number of lines `git blame` attributes to the preregistration commit must not drop.

## Why

`2026-11-02-a-finished-experiment-reports-observations-an-outcome-conclusions-and-next-experiments.md` says preregistered text is never edited, and says nothing about adding to it. Sometimes a preregistered section goes stale after the run, for example a line that describes what the tool prints before the first recorded run.

A labelled addendum lets a reader tell what was fixed before the run from what came after: the label carries the date and says it follows the recorded run, and the Changelog lists it. An edit would erase that line between before and after, and the preregistration exists to keep it. Moving or deleting text does the same, so they are barred too.

Commit 5af9057 is the precedent. It added the Under test line and the Results subsections around preregistered text in `experiments/EXP-0002/README.md` and logged them in the Changelog. It is consistent with this rule.

The check works because an addendum only adds lines, while an edit, move or deletion takes lines away from the preregistration commit's blame. A count that drops means preregistered text was changed.
