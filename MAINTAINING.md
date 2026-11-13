# Maintaining

This page is for the maintainers. Contributors need only `CONTRIBUTING.md`.

## Before merging a pull request from a fork

A pull request from a fork runs CI without this repository's secrets, so CI cannot run the barred-string scan on it, and the `barred` job fails on purpose: a fork's pull request is never merged as it stands. The steps below are the control, and nothing in CI replaces them.

1. Fetch the pull request's head: `git fetch origin pull/<number>/head:pr-<number>`.
2. Check out `pr-<number>`, set `BARRED_STRINGS_PATH` to the private list file, and run `python3 tools/barred_scan.py`. It scans every commit on the branch, not only the current files.
3. If the scan passes, push `pr-<number>` to a branch in this repository and open a pull request from that branch (as in step 4 of the section "Re-admitting recorded data and workflow changes" below), so that CI runs the scan with the secrets; merge that pull request, and close the fork's with a comment linking it. If the scan fails, ask the contributor to change the file it names. Never quote the list in a comment, issue or commit message.

## Re-admitting recorded data and workflow changes

Do not merge the fork's own pull request if it changes a file under `experiments/EXP-NNNN/recorded/`, a `recorded` row in `docs/admissions.md`, or anything under `.github/`. Instead:

1. Check each recorded file against its row: `shasum -a 256 <file>` must print the hash in that row's `basis` column.
2. Check two things: the data was made for the experiment, or everyone in it agreed to its use; and it is not a real session transcript.
3. For a change under `.github/`, read the whole diff yourself: workflow files run with this repository's secrets.
4. Push `pr-<number>` to a branch in this repository (`git push origin pr-<number>`) and open a pull request from that branch, so that CI runs with the secrets. Merge it, then close the fork's pull request with a comment linking the new one.

## The recorded-guard job

The `recorded-guard` job in `.github/workflows/gate.yml` runs on pull requests from forks. It fails when a pull request changes a recorded file or a `recorded` row, and its message says that a maintainer must re-admit the change. It does not flag changes under `.github/`; step 3 of the section "Re-admitting recorded data and workflow changes" covers those.

The job is a signal, not a control: a pull request can change the workflow file and so remove the job. The controls are the scan and the re-admission steps above. Code-owner review of the paths in `.github/CODEOWNERS` adds a second review only while the default branch requires it; that is a repository setting, so confirm it is on before relying on it.

## After merge

The `barred` job runs with the secrets on every push to a branch in this repository, and the scan reads all the history it can reach: every blob of every commit, every path that ever existed, every commit message and identity, and the branch name. A finding that has reached the default branch therefore stays in history. A later pull request that edits or renames the file does not clear it, and the job keeps failing on every branch that contains that commit.

If the use is intentional, approve it (the section "Approving an intentional use"; a finding in a path or a branch name cannot be approved).

If it is not, removing it means rewriting the default branch's history, and that stays with the repository owner (the decision of 2026-10-23 in `docs/decisions/`). Never quote the string in a comment, issue or commit message.

A rewrite gives new ids to the rewritten commit and every commit after it. An approval that names one of them is lost, and a `prereg.txt` that names one no longer matches history, so E8 fails until the sidecar names the new ids.

## Approving an intentional use

The maintainers can approve one intentional use of a barred string, so the scan no longer reports it. An approval names one entry in one git object, and nothing else. Approvals are kept private with the list, never in this repository.

1. Run `python3 tools/barred_scan.py --ids` locally with the list set. A finding that can be approved ends with `[object <id>]`. A finding in a path or in the branch name cannot be approved: rename it.
2. Add one line to the private allow list: the object id, a TAB, the entry number, a TAB, and a reason in plain words that does not contain the barred string.
3. Run the scan again. It must pass. A line that no longer matches a finding fails the scan as stale: delete it.

An approval covers exact bytes. An edit to an approved file makes a new object, which needs its own line. A commit approval is lost if the commit is rewritten, for example by a rebase, an amend or a squash merge.

## Changing an experiment after it is published

Preregistered text is never edited, moved or deleted, and that includes a typo fix or a reflowed paragraph. E8 counts the README lines that `git blame` attributes to the commits in `prereg.txt`, and each of these lowers the count.

To correct or update preregistered text, add a dated addendum, set off and labelled "Added <YYYY-MM-DD>, after the recorded run:", and list it in the experiment's Changelog (`docs/decisions/2026-11-05-preregistered-sections-take-labelled-addenda-never-edits.md`).

E8 has two messages. "More than the recorded" means the sidecar is wrong: set `readme_lines` to the count at the preregistration. "Fewer than the recorded" has two causes:

- Preregistered text was edited, moved or deleted: restore it and add an addendum instead.
- A commit id in `prereg.txt` is no longer in history, so git blame can attribute no lines to it. This follows a history rewrite, or a squash merge of a branch whose sidecar named its own commits, which the merge replaced. Restoring text does not help: the sidecar must name the new ids, as in "After merge".

To tell them apart, run `git merge-base --is-ancestor <id> HEAD` for each id in the sidecar. It prints nothing and exits 0 when the commit is in HEAD's history, and it fails when the id is not. If every id passes, the cause is the first.

When a pull request is squash-merged, the commits it contained are replaced by one new commit. A pull request that adds or changes a `prereg.txt` must therefore name commit ids that will exist on the default branch after the merge.

A recorded file never changes (E4). To correct one, add a new file with its own `recorded` row in `docs/admissions.md` (A4), and say why in the Changelog.

A README that takes only addenda only grows, and no file other than a generated or recorded one may be over 100000 bytes (A9). Check the README's size before you write a long addendum.

## Everything CI runs, and how to run it before you push

| Job | What it runs | Python | Local command |
| --- | --- | --- | --- |
| `gate-tests` (and the summary job `gate`) | the whole gate, `python3 tools/gate.py` | 3.9 and 3.13 | the clean-environment command in `CONTRIBUTING.md` |
| `dev-tools` | the test suite under pytest | 3.13 | `python3 -m pytest -q -p no:cacheprovider tests`, after installing `requirements-dev.txt` (`CONTRIBUTING.md`, "Before you open a pull request") |
| `barred` | the scan, with the secrets | 3.13 | `python3 tools/barred_scan.py`, with `BARRED_STRINGS_PATH` set to the private list |
| `reproduce` | every experiment's generated files | 3.9 and 3.13 | `python3 tools/reproduce.py --all` |
| `recorded-guard` | the guard, on fork pull requests only | 3.13 | `python3 tools/recorded_guard.py --base <full commit id>`, giving the tip of the pull request's base branch, which is what CI passes |

`--checks-only` is not the gate. It skips the tests stage, one of four, and the tests read the text of some documents and of `.github/workflows/gate.yml`, so a change to words alone can fail there. Four things make a local run differ from CI:

- As root, some tests skip, and the gate counts a skip as DEAD.
- In a shallow clone E8 fails and the barred scan is DEAD. CI fetches the full history.
- On a detached HEAD the barred scan is DEAD. Run it on a named branch: the branch name is scanned too.
- CI runs Python 3.9 and 3.13, so a pass on one does not cover the other.

## Running the gate after a rename

The gate builds its file list from git's staging area, plus any untracked files that are not ignored. A file you renamed or deleted but have not staged still appears under its old name, so the gate fails on a file that is no longer there. Stage the change first with `git add -A`, then run the gate with the clean-environment command in `CONTRIBUTING.md`. If you would rather not stage in your working copy, run the gate in a fresh clone where the change is staged.

## Reproduce CI's case-sensitive run on macOS

CI runs on Ubuntu, whose filesystem is case-sensitive, and the gate counts a skipped test as DEAD. A Mac's default volume ignores case, so some cases fail only on CI. To run the gate on a case-sensitive volume, with no sudo, work in an empty folder outside your clone and keep your clone's path in `$CLONE`:

```
hdiutil create -size 1g -fs 'Case-sensitive APFS' -type SPARSE -volname CRTEST crtest
mkdir crmnt && hdiutil attach crtest.sparseimage -nobrowse -mountpoint "$PWD/crmnt"
mkdir crmnt/tmp && git clone "$CLONE" crmnt/repo && cd crmnt/repo
```

Run the command from `CONTRIBUTING.md` here, with `TMPDIR="$PWD/../tmp"` added after `PATH=/usr/bin:/bin`, so the tests' scratch files land on the volume too (`env -i` would otherwise drop it). When done, `cd` out of the volume, run `hdiutil detach crmnt`, and delete `crtest.sparseimage`.
