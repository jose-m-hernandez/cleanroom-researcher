# Contributing

You can help in three ways. Each one is small. Read the one that fits, then the checklist.

## Ways to contribute

- **Report a problem with an experiment.** Open an issue. Say which experiment, what you expected, and what you saw.
- **Propose a new experiment.** Open a pull request. Copy `docs/experiment-template.md`, start the records with `tools/new_target.py`, and follow `docs/adding-a-target.md`.
- **Turn notes into a synthesis.** Follow `docs/writing-a-synthesis.md`.

## Before you open a pull request

Run the whole gate in a clean environment, from the root of your clone:

```
env -i PATH=/usr/bin:/bin HOME="$(mktemp -d)" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=user.useConfigOnly GIT_CONFIG_VALUE_0=true python3 tools/gate.py
```

The command clears the environment so the gate sees nothing from your home directory or git settings. If `python3` or `git` is not in `/usr/bin` or `/bin`, add its directory to `PATH` in the command.

Then rebuild every experiment's generated files:

```
python3 tools/reproduce.py --all
```

## Sign-off

Sign off every commit with `git commit -s`. The sign-off certifies the Developer Certificate of Origin 1.1: that you wrote the change, or have the right to submit it under this repository's licences.

## Pull-request checklist

- [ ] The protocol was committed before the first recorded run, or the experiment is labelled exploratory and its abstract says so.
- [ ] The read log is kept.
- [ ] Every new file has a row in `docs/admissions.md`.
- [ ] Target text appears only where its licence allows (`docs/decisions/2026-10-21-the-clean-room-line.md`).
- [ ] A live run declares its spend cap and isolation proof (`docs/decisions/2026-10-21-live-runs-stay-inside-boundaries.md`).
- [ ] A generator does not need the network.
- [ ] The gate passes.

## How review works

A maintainer reads your pull request for these six things.

- **Method:** the steps can be repeated by someone else, and the results follow from them.
- **Preregistration:** the protocol was committed before the first recorded run, or the experiment says plainly that it was not.
- **Licences:** each file has the right licence, and target text sits only where that licence allows.
- **Isolation proof:** a live run shows it could not see your home directory, credentials or other repositories.
- **Spend:** the cap was declared before the first call, and the runner stopped at it.
- **Readability:** a stranger can follow the write-up, and its words are the ones in `docs/vocabulary.md`.

## After merge

The gate runs on every pull request. A further scan that only maintainers can run checks every commit after merge; if it fails, a maintainer will contact you.

## Questions

Open an issue.
