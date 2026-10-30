# Writing a synthesis from your own research notes

A synthesis is a set of findings, each with a grade, a falsifier and the pinned sources that support it. This runbook turns your own research notes into one that this repository can check. You need a clone and Python 3.9 or later.

## 1. The aim

Your notes are not copied into this repository. They are rebuilt here as a synthesis, `syntheses/SYN-NNNN.md`, from pinned sources. Only a finding that a pinned source supports comes across. The repository has no `syntheses/` folder until its first synthesis, so create the folder along with the file.

## 2. Make the sources

Every source that a finding you keep relies on becomes a source record, `sources/SRC-NNNN/source.md`, one for each pinned version. The same repository at three commits is three records.

1. For each source, follow `docs/adding-a-target.md` to fetch and pin it outside the clone. Make the record with `tools/new_target.py`, or by hand in the same format (copy `sources/SRC-0001/source.md` and its header keys). Each new file needs a row in `docs/admissions.md`.
2. If a source has no recorded hash, fetch it again and hash it now. Do not copy a hash from your notes.
3. A pointer to private or machine-local state cannot become a source: a path on someone's computer, a private chat, a session log. A finding that rests only on such a pointer is cut (step 6).

## 3. Write the synthesis

Create `syntheses/SYN-NNNN.md`, with the number one more than the highest existing synthesis, or `0001` for the first. The format is:

- a title line, `# <title>`;
- one `### ` heading for each finding;
- under each finding, three lines:
  - `grade:` a value of the `Evidence grades` table in `docs/vocabulary.md`;
  - `falsifier:` what would show the finding wrong;
  - `sources:` the source ids, separated by commas.

A short example:

```
# What the example licence says

### The licence text is the standard MIT text

grade: verified
falsifier: A byte comparison of the pinned file with the published licence text that finds a difference.
sources: SRC-0001
```

The finding and the source number in this example are made up; yours come from your own records.

Give each of the three lines once per finding. Add a row for the synthesis file to `docs/admissions.md`. `docs/admissions.md` lists every file this repository accepts, with why it may be here and where it came from. The gate fails on a file that is not listed.

## 4. What stays out

Leave these in your notes:

- provenance headers;
- the names of the tools and sessions that produced the notes;
- branch names;
- run history;
- any wording from the original that a source does not support.

## 5. Quotes

Quotes follow the licence rule in `docs/decisions/2026-10-21-the-clean-room-line.md`. If the source's licence is on the `Redistributable licences` list in `docs/vocabulary.md`, you may quote it. If it is not, a quote stays under 25 words.

## 6. A finding that fails the checks is cut

When a finding cannot pass the checks (it has no source, its source cannot be pinned, or you cannot write a falsifier for it), cut it. Never reword it until it passes.

## 7. Run the gate

From the root of the clone:

```
env -i PATH=/usr/bin:/bin HOME="$(mktemp -d)" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=user.useConfigOnly GIT_CONFIG_VALUE_0=true python3 tools/gate.py
```

The command clears the environment so the gate sees nothing from your home directory or git settings. If `python3` or `git` is not in `/usr/bin` or `/bin`, add its directory to `PATH` in the command.

The last line must be `gate: PASS`. A finding line names the file and says how to fix it. If a check fails and the fix is not obvious from its message, stop and ask by opening an issue on this repository (see `CONTRIBUTING.md`).
