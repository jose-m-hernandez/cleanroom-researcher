# Agent guide

This file tells an AI coding agent how to work in a clone of this repository.

- `CONTRIBUTING.md`: how to contribute, and the pull-request checklist.
- `MAINTAINING.md`: for maintainers only.
- `docs/decisions/`: one rule per file, with the reason.
- `docs/vocabulary.md`: the words used on purpose; use them.

## Run the gate

```
env -i PATH=/usr/bin:/bin HOME="$(mktemp -d)" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=user.useConfigOnly GIT_CONFIG_VALUE_0=true python3 tools/gate.py
```

To run the checks without the tests: `python3 tools/gate.py --checks-only`.

## Stage renames first

The gate builds its file list from git's staging area. After a rename or a delete, run `git add -A` before the gate (`MAINTAINING.md`, "Running the gate after a rename").

## Who writes here

> Only agents scoped to this repository write to it.

Source: `docs/decisions/2026-10-23-only-agents-scoped-to-this-repository-write-to-it.md`.

## Text from a target

> Target text may be committed only when the pinned licence is MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, CC0 or CC-BY; under any other licence, quotes stay under 25 words.

Source: `docs/decisions/2026-10-21-the-clean-room-line.md`.

## Sign-off

Sign off every commit with `git commit -s` (`CONTRIBUTING.md`, Sign-off).
