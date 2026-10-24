# Only agents scoped to this repository write to it

Date: 2026-10-23

> **The rule.** Only agents scoped to this repository write to it. That covers changes to its files, its commits and their messages, its pull-request text, and pushes to its branches. Agents scoped elsewhere may review the repository and propose changes, but they never write to it. Rewriting the history of the default branch stays with the repository owner.

## Why

A commit message and pull-request text written from outside the repository carried text that the repository's barred-string scan refuses. The scan was right to refuse it, and the fix cost a rewrite of history.

A squash merge copies the pull request's description into the commit message. Text added to a pull request therefore becomes repository history, so the description is a write to the repository like any other.

Keeping every write inside agents scoped to this repository keeps one set of rules, this repository's, over all of its text. Maintainers can then trust that the scan and the vocabulary checks have seen what lands.

Outside agents lose nothing they need: a review or a proposed change still reaches the repository, through an agent scoped to it.
