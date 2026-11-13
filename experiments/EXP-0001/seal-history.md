# EXP-0001: the first seal, set aside and made again

> Added 2026-11-12, after the recorded run: a first seal was made, set aside before it was committed, and is made again. This note says when the first seal was made and from which commit, why it was set aside, what was written again and from what, and gives the sha256 of each of its files.
>
> The first seal was made at 2026-11-13 03:10:56 UTC (2026-11-12 19:10:56 US Pacific time) from commit `66d14fa403a360e81c6eb70277fd7272c7ad57a2`, by the agent that ran the offline steps for the repository owner. It wrote four files: `manifest.txt`, `record.json`, `seal.json` and the repair log.
>
> The first seal was set aside before any seal commit and before any baseline run. Its files could not be committed as written: in four lines of the repair log, a field label printed by the procedure that writes that log joined this repository's reserved word (`docs/vocabulary.md`) to another word with an underscore, which this repository's vocabulary check refuses. That procedure was written for this run and is kept outside this repository. The label names the Claude Code version recorded for each authoring run; that value, 2.1.302, is unchanged. The agent stopped, as the procedure written for this run says to when the check refuses a seal file, and did not edit the seal's files. Only that label was then changed in the procedure, which now prints it as `claude_code_version`, and the repository owner installed the changed procedure.
>
> The agent then wrote the repair log again with the changed procedure, from the same four authoring runs. No recorded value changed: in the repair log, only that label changed. The seal is made again from a commit that holds this note, and that seal records the commit it was made from.
>
> The first seal's files are kept, unchanged, by the repository owner outside this repository. Their sha256 hashes are:
>
> - `manifest.txt`: `14cc23c6c82d620dfcce4e2881aeef03e900b1fc852e049bd5d23e5e828fdce5`
> - `record.json`: `f760ef72423b9a6c0afd43791f18f5f56f850956cc9b12a25b02c30d81fd27d9`
> - `seal.json`: `29f43e31cef790007485fd500f4f4edb56002a000806bf2b7fe4a1f97a82b751`
> - the repair log, `repair-log.md`: `1c114dfa1efa38f84562f9a6c0e888d5944d9c1025840bb2c8af330a59305421`
