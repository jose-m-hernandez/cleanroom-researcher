# Repeatable experiments on AI tools

This repository is a collection of experiments on AI tools. Each experiment asks one question about a tool, states its protocol, records what happened, and tells you how to check it. You can rebuild every table and figure in an experiment from the recorded data with one command, with no account and no cost. You can also replicate an experiment on your own account and compare your result with ours; each write-up says what counts as agreement. Write-ups follow the layout of a short paper. If you think an experiment is wrong, or you want to add one, start with `CONTRIBUTING.md`.

## How an experiment is written

Each experiment is a folder under `experiments/` whose `README.md` follows `docs/experiment-template.md`. Directly after its abstract, the line **Under test** names the tool being tested, or says the experiment tests this repository's own method. Its header says whether its protocol was preregistered (committed in this repository before the first recorded run; not entered in an outside registry) or exploratory (not committed before the first recorded run).

## Rebuild an experiment

`python3 tools/reproduce.py EXP-NNNN` rebuilds the generated files of that experiment on your machine, offline, and says for each whether it is identical to the committed copy. `python3 tools/reproduce.py --all` does every experiment.

EXP-0001, the only experiment so far, is preregistered and has not been run yet. Until its first recorded run, `python3 tools/reproduce.py EXP-0001` (and `--all`) prints `reproduce: EXP-0001 has no generated files` and exits 0. That is the expected result, not a failure.

## Replicate an experiment

Each experiment's `README.md` has a How to replicate section: what to run on your own account, what counts as agreement with the recorded results, and the expected cost and time.

## Find your way

- [CONTRIBUTING.md](CONTRIBUTING.md): how to propose an experiment, and what review looks for.
- [docs/adding-a-target.md](docs/adding-a-target.md): pin a tool and start an experiment.
- [docs/writing-a-synthesis.md](docs/writing-a-synthesis.md): turn your own research notes into a checked synthesis.
- [docs/vocabulary.md](docs/vocabulary.md): the words this repository uses on purpose, and the allowed values for grades, source types and protocols.
- [docs/checks.md](docs/checks.md): what each check code (A4, V6, E6 and so on) means.
- [docs/decisions/](docs/decisions/): the rules, one per file, with the reason for each.

## Licences

Code is under the MIT licence (`LICENSE`). Write-ups, syntheses, protocols and recorded data are under CC-BY-4.0 (`LICENSE-CC-BY-4.0`). Text copied from a target keeps its authors' licence; this repository does not relicense it.
