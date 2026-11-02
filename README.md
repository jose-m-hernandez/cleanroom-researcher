# Repeatable experiments on AI tools

Does a tool that saves a session's notes keep the facts a later session needs? Does a one-line change to its instructions help? This repository asks questions like these about AI tools and answers them by experiment. Each experiment commits its question, its method, and the result that would count against its prediction before the first run. It then records what happened and says how to check it. Once an experiment has run, one offline command rebuilds its generated files from the recorded data and says whether each matches the committed copy, and you can replicate the experiment on your own account.

If you think an experiment is wrong, or want to add one, start with `CONTRIBUTING.md`.

Designed and run by José Miguel Hernandez, who directed Claude Code to write its code and documents (see docs/admissions.md).

## Experiments

| ID | Question | Protocol | Claim | Status | Outcome |
| --- | --- | --- | --- | --- | --- |
| [EXP-0001](experiments/EXP-0001/README.md) | Does the development accuracy of `/cpr:preserve` hold on fresh, blind fixtures? | preregistered | exploratory | not run | - |
| [EXP-0002](experiments/EXP-0002/README.md) | Does a one-line change to `/cpr:preserve` keep more of a session's facts? | preregistered | exploratory | not run | - |

Both are exploratory: their method was written after its authors read the tool, so neither can confirm anything. Words are in `docs/vocabulary.md`: `not run` means the protocol is committed and no live call has been made; `running` means the first live call has been made; `finished` means scoring is complete or the experiment has ended, and the Outcome is one of the outcomes that experiment's own protocol defines, listed by experiment under Experiment outcomes in `docs/vocabulary.md`. `-` means none yet.

## How an experiment is written

Each experiment is a folder under `experiments/` whose `README.md` follows `docs/experiment-template.md`. Directly after its abstract, the line **Under test** names the tool being tested, or says the experiment tests this repository's own method. Its header says whether its protocol was preregistered (committed in this repository before the first recorded run; not entered in an outside registry) or exploratory (not committed before the first recorded run).

## Rebuild an experiment

`python3 tools/reproduce.py EXP-NNNN` rebuilds the generated files of that experiment on your machine, offline, and says for each whether it is identical to the committed copy. `python3 tools/reproduce.py --all` does every experiment.

Until an experiment's first recorded run, `python3 tools/reproduce.py EXP-NNNN` (and `--all`) prints `reproduce: EXP-NNNN has no generated files` and exits 0. That is the expected result, not a failure.

## Replicate an experiment

Each experiment's `README.md` has a How to replicate section: what to run on your own account, what counts as agreement with the recorded results, and the expected cost and time.

EXP-0002 cannot be replicated while its fixtures are unpublished (`experiments/EXP-0002/README.md`, Materials).

## Find your way

**Read an experiment.** Start from its row above. Read its title and **Question**; then **Threats to validity**, for what could make it wrong and what was done about it.

**Check or repeat one.** Its **How to reproduce** and **How to replicate** sections.

**Look up a word.** [docs/vocabulary.md](docs/vocabulary.md): the words this repository uses on purpose, and the allowed values for grades, source types, protocols, claims and statuses. [docs/checks.md](docs/checks.md): what each check code (A4, V6, E6 and so on) means.

**Add to it.**
- [CONTRIBUTING.md](CONTRIBUTING.md): how to propose an experiment, and what review looks for.
- [docs/adding-a-target.md](docs/adding-a-target.md): pin a tool and start an experiment.
- [docs/writing-a-synthesis.md](docs/writing-a-synthesis.md): turn your own research notes into a checked synthesis. None exists yet.

**See why the rules are what they are.** [docs/decisions/](docs/decisions/): one rule per file, with the reason.

## Licences

Code is under the MIT licence (`LICENSE`). Write-ups, syntheses, protocols and recorded data are under CC-BY-4.0 (`LICENSE-CC-BY-4.0`). Text copied from a target keeps its authors' licence; this repository does not relicense it.

Maintainer: José Miguel Hernandez (@jose-m-hernandez) · https://jose.io
