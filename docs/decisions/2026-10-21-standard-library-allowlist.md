# Code uses the Python standard library only, from a named allowlist

Date: 2026-10-21

> **The rule.** Every Python file outside an experiment's `recorded/` directory imports only modules on the allowlist in `tests/test_stdlib_only.py`. Repository-wide additions: `copy`, `difflib`, `hashlib`, `json`, `math`, `pathlib`, `statistics`, `tarfile`. Under `experiments/*/code/runner/` only: `random`, `time`, `uuid`. Anything else needs a new decision.

## Why

Anyone repeating an experiment should need Python and nothing else, so there are no installs and no version drift. A named allowlist makes each new dependency a visible decision instead of an accident.

`random`, `time` and `uuid` make output vary from run to run. Keeping them in runner code, which talks to live systems, keeps them out of the analysis code. The analysis must give the same bytes from the same recorded data every time.

Files in `recorded/` are data under study, such as the source of a small invented project a target was asked to work on. Their imports describe the subject, not our dependencies, so the allowlist does not apply to them.

Amended 2026-10-21: the experiment folder `harness/` is now `code/` (see `docs/vocabulary.md`).
