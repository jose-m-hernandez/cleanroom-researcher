# Contributing

You can help in three ways. Read the one that fits, then the checklist.

## Ways to contribute

- **Report a problem with an experiment.** Open an issue. Say which experiment, what you expected, and what you saw.
- **Propose a new experiment.** Open a pull request. Copy `docs/experiment-template.md`, start the records with `tools/new_target.py`, and follow `docs/adding-a-target.md`.
- **Turn notes into a synthesis.** Follow `docs/writing-a-synthesis.md`.

## Before you open a pull request

Run the whole gate, which is every check in `docs/checks.md` and the tests, in a clean environment, from the root of your clone:

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
- [ ] Recorded data is made for the experiment, or everyone in it agreed to its use. It is never a real session transcript.
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

## Approving an intentional use

The maintainers can approve one intentional use of a barred string, so the scan no longer reports it. An approval names one entry in one git object, and nothing else. Approvals are kept private with the list, never in this repository.

1. Run `python3 tools/barred_scan.py --ids` locally with the list set. A finding that can be approved ends with `[object <id>]`. A finding in a path or in the branch name cannot be approved: rename it.
2. Add one line to the private allow list: the object id, a TAB, the entry number, a TAB, and a reason in plain words that does not contain the barred string.
3. Run the scan again. It must pass. A line that no longer matches a finding fails the scan as stale: delete it.

An approval covers exact bytes. An edit to an approved file makes a new object, which needs its own line. A commit approval is lost if the commit is rewritten, for example by a rebase, an amend or a squash merge.

## Questions

Open an issue.
