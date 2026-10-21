# Home-directory lookups in code are allowed only in runner code that builds an isolated environment

Date: 2026-10-21

> **The rule.** Check V5 (a home-directory lookup in code) fails everywhere except `experiments/*/harness/runner/`. There, code may read or set a home directory only to build an isolated environment for a run. The scope is a path rule in `tools/check_vocabulary.py`, never an entry in an exemption list.

## Why

A lookup of the user's home directory is how one machine's private state leaks into a result. Analysis code never needs one.

A runner that isolates a live run has to set a fresh home directory for the child process, and it has to check that the child cannot see the real one. That is the one legitimate use, and it lives in one kind of place. A path rule covers every future experiment without anyone adding a list entry, and an exemption list would only grow.
