# Adding a target

This runbook takes you from nothing to a new target with a source record, an experiment record and a passing gate. You need a clone of this repository and Python 3.9 or later. Everything here uses the Python standard library and `git`.

## 1. What a target is

A target is the outside tool under test; the `target` row of `docs/vocabulary.md` is its definition. Every pinned thing in this repository is a source. A target is a source that is the tool under test. Each target gets two records: a source record in `sources/SRC-NNNN/source.md`, which pins exactly which version of the target you studied, and an experiment record in `experiments/EXP-NNNN/`, which holds the question, the method and the results.

## 2. Check the licence first

Read the target's licence before you copy any of its text anywhere. The rule is in `docs/decisions/2026-10-21-the-clean-room-line.md`, and the list of licences that allow redistribution is the `Redistributable licences` table in `docs/vocabulary.md`.

- If the licence is on that list, target text may be committed, but only under `experiments/EXP-NNNN/target/`.
- If it is not on that list, no target text goes in `target/`, and any quote stays under 25 words.

If you cannot tell which licence applies, stop and ask by opening an issue on this repository (step 11) (see `CONTRIBUTING.md`).

## 3. Fetch and pin the target outside the clone

Pinning means writing down exactly which bytes you studied, so that someone else can fetch the same ones. Do this in a scratch directory outside the clone, so that nothing from the target lands in the repository by accident:

```
scratch="$(mktemp -d)"
cd "$scratch"
```

**For a code repository.**

1. Clone it into the scratch directory: `git clone <the repository address> repo`.
2. Check out the exact commit you will study: `git -C repo checkout <commit>`.
3. Run `git -C repo rev-parse HEAD`. The 40-character id it prints must equal the commit you meant to pin. Use that full id as `--commit`.
4. Hash the licence file: `shasum -a 256 repo/<the licence file>`. The 64-character result is `--sha256`. For a code repository, the hash covers the licence file, because the commit already pins the rest.

**For a release file** (a downloaded archive or binary).

1. Download it into the scratch directory with your usual tool.
2. Hash the bytes you downloaded: `shasum -a 256 <the file>`. The result is `--sha256`.

**Hosted services** have no bytes to pin. They are not covered by this runbook yet. Do not add one with a made-up hash; ask by opening an issue on this repository (see `CONTRIBUTING.md`).

For a target, the source type is normally `code-repo` or `release-artifact`. The other types in the `Source types` table are for sources a synthesis cites.

## 4. Run `tools/new_target.py`

From the root of the clone:

```
python3 tools/new_target.py \
  --title "Example tool" \
  --type code-repo \
  --url https://example.com/example/tool \
  --publisher "Example publisher" \
  --fetched 2026-01-31 \
  --sha256 0000000000000000000000000000000000000000000000000000000000000000 \
  --commit 1111111111111111111111111111111111111111 \
  --licence MIT \
  --licence-basis "the LICENSE file at the pinned commit" \
  --provenance "Your name or tool, 2026-01-31"
```

The values above are dummies. Use your own, and use the real address of the target: the tool refuses `example.com` and the other reserved hosts, and it refuses any address that is not `https`. For a source that is not a code repository, such as a release file, set `--type` to its type (`release-artifact` for a release file) and leave out `--commit`: the tool refuses `--commit` for any type but `code-repo`. The optional arguments are `--notes` (default `-`), `--protocol` (default `preregistered`) and `--root`.

The tool writes a source record, an experiment record with `TODO:` placeholders for each section, an empty read log, and three rows in `docs/admissions.md`. `docs/admissions.md` lists every file this repository accepts, with why it may be here and where it came from. The gate fails on a file that is not listed. It prints one `new_target: wrote <path>` line for each path. It validates everything before it writes, it never overwrites an existing file, and it never fetches anything. If it refuses, it says what is wrong and how to fix it, and it has changed nothing.

Replace every `TODO:` line in the experiment record as you go. Read the new source record and check that each value is the one you pinned.

## 5. Write the protocol before you read the target for method work

Fill in the `Question`, `Hypotheses and planned analysis` and `Methods` sections before you read the target for how it does the thing you are measuring. Reading its README to learn how to install or call it is fine, and is logged (step 6).

Then commit the experiment record before the first recorded run. The `protocol` line of the record says which of two cases applies; the definitions are in the `Experiment protocols` table of `docs/vocabulary.md`:

- `preregistered`: the protocol was committed here before the first recorded run. It was not entered in an outside registry.
- `exploratory`: the protocol was not committed before the first recorded run. An exploratory experiment says so in its abstract: the abstract must contain the word `exploratory`. The abstract is the first paragraph under `## Abstract`.

If you are already past the point of committing first, change `protocol` to `exploratory` and say so in the abstract. Do not backdate anything.

Commit the method code under `experiments/EXP-NNNN/code/` before you first read the target for method work. When the read log is `contemporaneous`, the check compares the commit time of the first commit that touches that folder with the time of the earliest read-log entry whose reason is `method`, and the commit must not be later. Both times can be set by hand, so this is a consistency check on the read log, not proof (step 6).

## 6. Keep the read log as you go

`experiments/EXP-NNNN/read-log.md` records each target file you open. Add a row when you open the file, not afterwards. The columns are:

| column | what to write |
| --- | --- |
| `when` | The UTC time, like `2026-01-31T14:05Z`. |
| `path` | The path of the file you read. |
| `sha256` | The hash of that file, 64 lowercase hex characters. |
| `reason` | One of the three reasons below. |

The reasons are the `Read-log reasons` table of `docs/vocabulary.md`: `interface` (to learn how to install, run or call the target), `method` (for how the target does the thing being measured) and `verification` (to check a result against the target's own behaviour or text).

The header of the experiment record has a `read_log` line with two values, defined in the `Read-log kinds` table of `docs/vocabulary.md`. Keep `contemporaneous`, the value `tools/new_target.py` writes, when each row was written as you opened the file. Change it to `reconstructed` only if the log was written afterwards. A reconstructed log must say `reconstructed` in the abstract. Do not backdate entries to make a log look contemporaneous.

The read log is an honesty record, not proof. It cannot show that nothing was read without being logged. Its value is that you wrote down what you did.

## 7. Where things go

- Method code goes under `experiments/EXP-NNNN/code/`, and code that builds and runs a live run goes under `experiments/EXP-NNNN/code/runner/`.
- Results go under `experiments/EXP-NNNN/recorded/`.
- Raw output stays outside the clone, always. The gate fails on any path with a `_raw` part, even one that git ignores, so do not create one inside the clone.
- Target text goes only under `experiments/EXP-NNNN/target/`, and only when step 2 allows it.
- Every new file needs a row in `docs/admissions.md`. The new-target tool adds rows for the three files it writes; you add the rest.

## 8. Live runs

A live run calls the target for real, on your own account. `docs/decisions/2026-10-21-live-runs-stay-inside-boundaries.md` is the rule. Its six boundaries:

1. The target's terms: the run is allowed by the licence or the terms of use, and nobody signs up for an account or a paid plan or works around a rate limit without the project owner's approval.
2. Isolation: each run proves it could not see your home directory, credentials, other repositories, session logs or connected services.
3. Spend: a cap is declared before the first call, every call type is priced in advance, and the runner stops at the cap.
4. Attribution: the record says who ran the experiment and paid for it.
5. No outreach: running an experiment never opens an issue or contacts the target's authors.
6. Order: the protocol is committed before the run, and the gate never makes a live call.

Before the first call, set `spend_cap_usd` (a positive amount, such as `5` or `2.50`) and `launched_by` (who runs and pays) in the experiment's header. For each run, add `experiments/EXP-NNNN/recorded/runs/<run>/isolation.json` with `"isolated": true` and a non-empty `"checks"` list of strings that name what was checked.

## 9. Run the gate

From the root of the clone, run the whole gate in a clean environment:

```
env -i PATH=/usr/bin:/bin HOME="$(mktemp -d)" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=user.useConfigOnly GIT_CONFIG_VALUE_0=true python3 tools/gate.py
```

The command clears the environment so the gate sees nothing from your home directory or git settings. If `python3` or `git` is not in `/usr/bin` or `/bin`, add its directory to `PATH` in the command.

A pass ends with these lines, with your own counts:

```
admission: ok (... files, 0 findings)
vocabulary: ok (... files, ... commit messages)
experiments: ok (... experiments, ... sources, 0 syntheses)
tests: ok (... tests)
gate: PASS
```

Any other last line (`gate: FAIL` or `gate: DEAD`) means a stage found a problem. Each finding names the file and says how to fix it.

## 10. New vocabulary is data

A new row in `docs/vocabulary.md`, such as a new source type, is data the checks read, not a new rule: add the row and the checks accept the value.

## 11. Stop and ask when

Ask by opening an issue on this repository (see `CONTRIBUTING.md`). Stop and ask when:

- the licence is unclear;
- a run would need a new account or new terms;
- a recorded byte would have to change to pass a check;
- a check fails and the fix is not obvious from its message.
