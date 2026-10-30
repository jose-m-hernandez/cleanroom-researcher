# Fixture authoring guide

A fixture is one scripted working session on a small invented software project, plus the facts a later session would need from it and the questions that test whether those facts survived. `evals/fixtures/<split>/<id>/spec.json` is the whole definition; `python3 evals/run.py validate` checks every rule below and prints problems as `<id>: <label>` without echoing spec text.

## Shape of a spec

```json
{
  "id": "dev-001-csv-tally",
  "split": "dev",
  "summary": "one neutral line",
  "repo": {"files": {"CLAUDE.md": "...floor, 20 to 80 lines...", "app/main.py": "..."}},
  "allowed_bash": ["python3 *", "git *", "ls *"],
  "turns": ["scripted user message 1", "..."],
  "history_check": {"question": "What codename did I give the release branch?", "token": "BLUEFINCH"},
  "items": [{"id": "i1", "statement": "...", "needed": true, "suits": ["Key Decisions"],
             "oracle_line": "- ...", "oracle_section": "Decisions", "source": "user",
             "anchor": "exact words from the turn that state it"}],
  "probes": [{"id": "p1", "code_answers": false, "item": "i1", "type": "knowledge-update", "question": "...", "answer": "...", "reject": ["..."]},
             {"id": "p9", "code_answers": false, "item": null, "near": "i1", "type": "abstention", "question": "...", "answer": "UNKNOWN", "reject": []}]
}
```

Only these keys are allowed at each level; anything else (for example an extra label on an item) fails validation. Labels are neutral: an item is `needed` (a future session needs it) or not, and `suits` names which of the baseline's six categories the item would naturally go under: Phase/Status Changes, Key Decisions, New Files/Structure, Patterns/Insights, Blockers/Warnings, Next Steps. `suits` describes those categories and nothing else; not-needed items carry `suits: []`. `source` is optional (`user`, `tool` or `both`) and marks where the session first shows the fact. A needed item that is not `source: tool` carries an `anchor`: the exact words in a turn that state it (the validator checks they occur, and the tests count how many sit after the first sentence of their turn). Tool-sourced items carry no anchor.

## Rules the validator enforces

- IDs: `<split>-<nnn>-<slug>`, equal to the directory name; item and probe IDs unique inside a fixture; fixture IDs unique in the set.
- Probe `type` is one of `single-fact`, `knowledge-update`, `temporal`, `abstention`. Every needed item has at least one probe. Each fixture has at least 5 needed items, 2 not-needed items, and one knowledge-update, one temporal and one abstention probe. A knowledge-update probe lists the stale answer in `reject`.
- Every abstention probe is a near-miss: `item` is null, `near` names a needed item, `answer` is `UNKNOWN`, `reject` is empty, the question shares a content word with that item's statement, and the question is about something the session never said (it does not appear in the turns).
- Every probe of an active spec carries `code_answers`, true or false. An excluded spec may omit it.
- `suits` values come only from the six categories. Across the set every category is suited by at least 2 items.
- The floor `CLAUDE.md` has 20 to 80 lines; the rendered oracle (floor plus each needed item's `oracle_line` under its `oracle_section`, created at the end if absent) stays under 200 lines. No needed item's `statement` or `oracle_line` appears in the floor as an exact substring.
- `history_check.token` is 6 to 16 capital letters, appears in `turns`, and appears nowhere in `repo`, items or probes.
- Long sessions: a long fixture has 30 or more turns. A complete dev split (19 run fixtures) needs at least 3 long fixtures; the held-out split needs at least 5 (`validate --heldout-complete`).
- `allowed_bash` entries start with one of a short list of commands and contain no shell operators. Turns never start with `/` (a slash would invoke a command instead of talking). Python files in `repo` must parse.
- Turns carry no signposting: the validator rejects the phrases "for the record", "note that", "remember that", "please remember", "important:", "decision:", "keep in mind", "don't forget", "make a note", "worth noting" and "fyi" (case-insensitive). At least 2 needed items per pilot fixture sit mid-turn.
- An item with `source: tool` must not be stated in any turn, and its probe answers must not appear in the turns.
- A `source: tool` item carries `evidence`: a list of short literal strings that only its tool output produces (for example an exception message). Evidence is required on tool items, forbidden elsewhere, at most 80 characters per string, and must not appear in any turn. Generation checks that every evidence string appears in a tool result that was not a permission denial; otherwise the attempt fails with `tool_evidence_missing:<item id>` and is regenerated. `python3 evals/run.py install-verify <id>` re-runs that gate on an installed fixture without any model call; the clean-room lint is the separate `lint` command (P:249).
- Keep the commands a turn asks for simple and listed in `allowed_bash`: a compound command (`cmd; echo $?`) is refused by the allowlist unless every part is listed, so a turn that asks for an exit code needs `echo *` allowed.

## Excluding a fixture

A spec that cannot be generated stays in the tree with a top-level `excluded` object, `{"date": "YYYY-MM-DD", "reason": "..."}`, so the reason is kept. The validator still checks the rest of the spec, leaves it out of the set rules and the counts, and names it on its own (`1 excluded: <id>`). `run`, `pilot`, `gen-fixture` and `install-check` never launch it: `all` and ranges skip it, and naming it refuses.

### Excluding a probe

A single probe that cannot be scored fairly carries an optional `"excluded": "<reason>"`, a non-empty string. Example: a "not started" probe whose gold names one item while the fixture's remaining-work plan leaves others equally unstarted. The probe stays in the spec as evidence and is still asked and judged, but `scorable_probes` never counts it, so it appears in neither the primary nor the dropped counts, and REPORT.md's scorable-probes table shows it in an "Excluded" column. The validator rejects an empty or non-string value (`probes_excluded_invalid`); a malformed marker still counts as excluded. The set rules (a knowledge-update, a temporal and an abstention probe per fixture) count active probes only; the rule that every needed item has a probe counts excluded probes too, since the item keeps its probe as evidence.

## Writing the repo

Keep the project small enough to read in a minute: 5 to 12 files, standard library only, a passing test suite at the start (run it before you ship the spec). The floor `CLAUDE.md` is what a competent maintainer would already have: overview, layout, commands, conventions. It must not contain any fact the session will teach; that is what the items are for.

## Planting an item

Every needed item must be something the session visibly produces, in one of two ways.

1. **In user speech.** Put it in a turn as a natural instruction or decision: "Add the /v2/users endpoint; the old routes stay frozen, so every new endpoint goes under /v2/." Say it once, plainly, the way a person does, and make the scripted turn also ask for real work so the assistant has something to do. Knowledge updates are a plain change of mind in a later turn ("Change of plan: make the limit 88 instead of 100"). Temporal items are an explicit order ("Remaining work, in this order: A, then B, then C").
2. **In tool output.** Plant a constraint in the repo that only shows when something runs, then script a turn that makes the assistant run it and report ("Try defining a flag called New_Feature and tell me exactly what it says"). The turn must not state the fact. Mark the item `source: tool`. Each pilot fixture has at least one. Run the command yourself against the starting repo and confirm the output shows the fact.

Aim for the item to be one that the assistant would plausibly be asked about later, not trivia: a decision with its reason, a constraint, a rename, a convention, a status, an ordered plan.

### No signposting

Real people rarely announce that something is worth keeping; they state it in passing while asking for work. A signposted turn also tells every arm what to save, which inflates recall for all of them and shrinks the difference the evaluation exists to measure. Plant facts as a reason given while asking, a correction, or a plain statement of intent inside a request, and keep the ask first or last so the fact is not the turn's lead. Before: "Decision for the record: we will not auto-detect delimiters, because sniffing misreads quoted fields. Add a --delimiter option." After: "Add a --delimiter option (default comma) and thread it through read_rows. The user passes it themselves, no sniffing, since that misreads quoted fields."

## Writing distractors

Not-needed items are things a future session does not need: an approach that was tried and reverted, a flaky CI run, schedule news, small talk. Say them in a turn marked "no action needed" so the assistant is not asked to do anything, and keep them recognisably different from the needed items (a distractor that also states a decision is a needed item). Write them in the same plain voice as the rest of the turns; the point is that the answer to "should this be recorded" is no.

## Probes

Write one or two probes per needed item, phrased as a fresh question with a short expected answer. For a stale-answer probe list the old value in `reject`. Add at least one abstention probe per fixture that asks about something adjacent to a real item that the session never settled; the correct answer is that it is not known.

`code_answers` records whether the toy repo's code answers the probe. Probe sessions see only `CLAUDE.md`, and a preserve skill may point to code instead of copying it, so reports split the primary metric by this tag. Set it to true only when a file in `repo/` other than `CLAUDE.md` holds a line a reader would quote as the answer; anything that needs inference stays false. On an abstention probe, true means the code answers a question the session never settled, which is worth a second look at the probe.

## Content rules

Everything is invented and generic: neutral project names, `@example.com` addresses if any, no real people, no paths from any real machine, and nothing that reads like the tooling of any particular workstation. The release-branch codename in `history_check` is an arbitrary token used to test that a resumed session carries the conversation.

`dev-014-log-digest` is kept as a spec with an `excluded` block but was not run: its generation failed six times (compound shell commands outside the allowlist, then the resume control), so it has no installed transcript. It counts toward none of the results, which cover the other 19 dev fixtures.
