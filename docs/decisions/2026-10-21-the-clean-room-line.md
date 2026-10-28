# The clean-room line: what may influence a method, what may be redistributed, what may be run

Date: 2026-10-21

> **The rule.** A method is written before its author reads the target for method work, and the protocol is preregistered: committed before the first recorded run. An experiment whose protocol was not committed before its first recorded run is labelled exploratory, and its abstract says so. Every experiment keeps a read log, and each entry's reason is one of `interface`, `method` or `verification`. Target text may be committed only when the pinned licence is MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, CC0 or CC-BY; under any other licence, quotes stay under 25 words. A target may be run, but only in isolation, and never by the gate.

## Why

The repository studies other people's tools without letting their work shape ours unseen. Three things can leak, and each gets its own limit:

- **Influence.** If a method is written after reading how the target works, the evaluation measures the target against itself. Preregistration is the standard scientific guard against that. The read log records what was opened and why. It is an honesty record, not proof: it cannot show that nothing was read without being logged. Reading for the interface (a README, an install step) is allowed and logged.
- **Redistribution.** Committing someone's text is a licence question. The list names licences that allow redistribution without conditions on use; non-commercial and no-derivatives variants are excluded. CC-BY-SA is off the list: its share-alike terms may not fit this repository's licences, so until a legal review rules, its text may only be quoted, under 25 words.
- **Execution.** Running a target can reach the network, the file system and an account. Isolation keeps the runner's own machine and data out of the run. The gate only checks files, so it never runs a target.

An experiment whose protocol was not committed before its first run is labelled exploratory, and its abstract says so.
