# Checks

This page says what each check code means. The words this repository uses on purpose, and the values each field may take, are in `docs/vocabulary.md`.

| Code | What it checks | Where |
| --- | --- | --- |
| A0 | Every file the repository lists is a regular file. A link, a socket or anything else that cannot be read as a file is refused. | `tools/check_admission.py` |
| A1 | Every file has exactly one row in `docs/admissions.md`. A file with no row, or with two, fails, and so does a ledger that is missing or is not one well-formed table. | `tools/check_admission.py` |
| A2 | Every path in the ledger is a normal path inside the repository, and the file it names exists. | `tools/check_admission.py` |
| A3 | Every row has one of the four known classes (`original`, `sourced`, `fixture`, `recorded`), and its provenance holds an ISO date. | `tools/check_admission.py` |
| A4 | A row's basis fits its class. A `sourced` row names source records that exist. A `fixture` row names a generator that has an `original` row, and running the generator with the seed gives the committed bytes. An `original` row has the basis `-`. A `recorded` row names a file under an experiment's `recorded/` folder, and its basis is `sha256=` followed by the 64-character lowercase hash of the file's bytes. Every file under a `recorded/` folder has a `recorded` row. | `tools/check_admission.py` |
| A5 | A source id written anywhere in the repository has a source record. | `tools/check_admission.py` |
| A6 | A web address written anywhere, except in a recorded file, is either the address of a source record or a reserved example host. | `tools/check_admission.py` |
| A7 | Every file is valid UTF-8, except a fixture or a recorded file. | `tools/check_admission.py` |
| A8 | Every source record has a clean header: the exact keys for its type, an id equal to its folder, an `https` address with a real host, a known source type, an ISO fetch date, a 64-character lowercase hash, and a snapshot of `yes` or `no`. | `tools/check_admission.py` |
| A9 | No file is over 100000 bytes, except a fixture or a recorded file. | `tools/check_admission.py` |
| A10 | No path anywhere has a part named `_raw`, in any commit or on disk, even one that git ignores. | `tools/check_admission.py` |
| V1 | No file or commit message holds an id shaped like the ones the three reserved prefixes make. | `tools/check_vocabulary.py` |
| V2 | The reserved word is not joined to a letter or a digit by a hyphen or an underscore. | `tools/check_vocabulary.py` |
| V3 | The reserved word is not followed by the word for a lane. | `tools/check_vocabulary.py` |
| V4 | No home path, machine path or home-directory variable appears. | `tools/check_vocabulary.py` |
| V5 | A Python file does not look up the home directory. Test files may set a temporary one for a child process, and live-run runner code and recorded files are not checked. | `tools/check_vocabulary.py` |
| V6 | `docs/vocabulary.md` has exactly one row for the reserved word, no other file has a line shaped like that row, and the README's first heading and first paragraph do not use the word. | `tools/check_vocabulary.py` |
| V7 | Every commit message passes V1 to V4 and V8, and so do the author and committer names and addresses. | `tools/check_vocabulary.py` |
| V8 | No file or commit message holds a string shaped like an access token or a private key: the common GitHub, AWS, Slack and Google token and key shapes, keys that start `sk-`, and private-key headers. Recorded files are checked too. | `tools/check_vocabulary.py` |
| E1 | An experiment is a folder `experiments/EXP-NNNN/` with a README. Its header has the exact keys, an id equal to the folder, a subject that is `method` or an existing source, a known protocol and a known read-log kind. | `tools/check_experiments.py` |
| E2 | A pinned source has what its type needs: a code repository has a full 40-character commit and a licence, and a release file has a licence. | `tools/check_experiments.py` |
| E3 | A file under an experiment's `target/` folder has a subject that is a source whose licence is on the redistributable list. | `tools/check_experiments.py` |
| E4 | A recorded file never changes: every commit that holds it, and the working tree, hold the same bytes. To correct recorded data, add a new file and say why in the experiment's changelog. | `tools/check_experiments.py` |
| E6 | Order and disclosure, in three parts. E6a: recorded data in a preregistered experiment comes after a commit of its README. E6b: with a contemporaneous read log, the method code is committed no later than the first read-log entry for method. E6c: an exploratory experiment says "exploratory" in its abstract, and one with a reconstructed read log says "reconstructed". E6b compares times that a person wrote down and that can be set by hand, so it is a consistency check on the read log, not proof of order. | `tools/check_experiments.py` |
| E7 | An experiment with recorded runs declares a positive spend cap and who launched it, and each run folder holds an `isolation.json` proof. | `tools/check_experiments.py` |
| S1 | A synthesis is `syntheses/SYN-NNNN.md`, holds at least one finding, and each finding names its sources as a comma-separated list of source ids. | `tools/check_experiments.py` |
| S2 | Each finding has a grade from the `Evidence grades` table. | `tools/check_experiments.py` |
| S3 | Each finding has a falsifier that is not empty. | `tools/check_experiments.py` |

The reserved word is the one in the first row of the Terms table in `docs/vocabulary.md`, which says it is not used alone. V2 and V3 refuse the shapes that join it to another word. This page does not write those shapes out, because it is checked too and would fail its own check.

E5 is the `_raw` rule, which A10 implements.
