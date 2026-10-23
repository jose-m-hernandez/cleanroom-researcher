# An experiment's claim is confirmatory only when its protocol was committed first and its method was written clean-room

Date: 2026-10-22

> **The rule.** An experiment's claim is `confirmatory` only when its protocol was committed before the first recorded run and its method was written before its author read the target for method work. Otherwise its claim is `exploratory`, and its abstract says so. The header's `protocol` value records only the order of commit and run; the `Experiment claims` table in `docs/vocabulary.md` defines both claim values.

## Why

The word `exploratory` had come to carry two senses. One is the protocol's order, defined in the `Experiment protocols` table. The other is whether the claim may be confirmatory, which the clean-room line (`docs/decisions/2026-10-21-the-clean-room-line.md`) settles by requiring the method to be written before its author reads the target.

A protocol can be committed before its first run and still have a method written after reading the target, so the order alone does not settle the claim. The first experiment, EXP-0001, is that case.

The new table names the claim separately, with `exploratory` covering both causes, so one word has one meaning at the level of the claim. No header key is added, and no existing check changes.

The template's sentence about the abstract now refers to the claim, which covers an exploratory protocol as before.
