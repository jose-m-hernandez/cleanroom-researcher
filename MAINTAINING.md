# Maintaining

This page is for the maintainers. Contributors need only `CONTRIBUTING.md`.

## Before merging a pull request from a fork

A pull request from a fork runs CI without this repository's secrets, so CI cannot run the barred-string scan on it. The steps below are the control, and nothing in CI replaces them.

1. Fetch the pull request's head: `git fetch origin pull/<number>/head:pr-<number>`.
2. Check out `pr-<number>`, set `BARRED_STRINGS_PATH` to the private list file, and run `python3 tools/barred_scan.py`. It scans every commit on the branch, not only the current files.
3. Merge only if the scan passes. If it fails, ask the contributor to change the file it names. Never quote the list in a comment, issue or commit message.

## Re-admitting recorded data and workflow changes

Do not merge the fork's own pull request if it changes a file under `experiments/EXP-NNNN/recorded/`, a `recorded` row in `docs/admissions.md`, or anything under `.github/`. Instead:

1. Check each recorded file against its row: `shasum -a 256 <file>` must print the hash in that row's `basis` column.
2. Check two things: the data was made for the experiment, or everyone in it agreed to its use; and it is not a real session transcript.
3. For a change under `.github/`, read the whole diff yourself: workflow files run with this repository's secrets.
4. Push `pr-<number>` to a branch in this repository (`git push origin pr-<number>`) and open a pull request from that branch, so that CI runs with the secrets. Merge it, then close the fork's pull request with a comment linking the new one.

## The recorded-guard job

The `recorded-guard` job in `.github/workflows/gate.yml` runs on pull requests from forks. It fails when a pull request changes a recorded file or a `recorded` row, and its message says that a maintainer must re-admit the change. It does not flag changes under `.github/`; step 3 above covers those.

The job is a signal, not a control: a pull request can change the workflow file and so remove the job. The controls are the scan and the re-admission steps above. Code-owner review of the paths in `.github/CODEOWNERS` adds a second review only while the default branch requires it; that is a repository setting, so confirm it is on before relying on it.

## After merge

The `barred` job runs with the secrets on every push to this repository. If it fails after a merge, open the file it names and fix it in a new pull request.

## Approving an intentional use

The maintainers can approve one intentional use of a barred string, so the scan no longer reports it. An approval names one entry in one git object, and nothing else. Approvals are kept private with the list, never in this repository.

1. Run `python3 tools/barred_scan.py --ids` locally with the list set. A finding that can be approved ends with `[object <id>]`. A finding in a path or in the branch name cannot be approved: rename it.
2. Add one line to the private allow list: the object id, a TAB, the entry number, a TAB, and a reason in plain words that does not contain the barred string.
3. Run the scan again. It must pass. A line that no longer matches a finding fails the scan as stale: delete it.

An approval covers exact bytes. An edit to an approved file makes a new object, which needs its own line. A commit approval is lost if the commit is rewritten, for example by a rebase, an amend or a squash merge.

