"""The experiments check: every experiment folder is a well-formed record.

Usage: python3 tools/check_experiments.py [--root PATH]

Output: finding lines ``experiments: <path>[:<line>]: E<n> <what is wrong>; <how
to fix it>``, then one summary line. Exit 0 pass, 1 findings, 2 dead or usage
error.

Rules:

- E1 experiment records: files under experiments/ sit inside an experiment
  folder named EXP- plus four digits, each with a README whose header has the
  exact keys, an id equal to the folder, a subject (method, or a listed
  source), a known protocol and a known read-log kind.
- E2 pinned sources: a code-repo source has a 40-character lowercase commit and
  a licence; a release-artifact source has a licence.
- E3 the target text class: a file in an experiment's target/ folder needs a
  subject that is a source whose licence is on the redistributable list.
- E6 order and disclosure. E6a: in a preregistered experiment, every commit that adds or changes a file under recorded/ has an earlier commit, among its ancestors, that touched the experiment's README. E6b: in an experiment whose read log is contemporaneous, the first commit under the method folder is no later than the earliest read-log entry whose reason is method. E6c: an exploratory experiment's abstract says "exploratory", and an experiment with a reconstructed read log says "reconstructed" there. E6a uses git ancestry. E6b compares the committer time with times a person wrote down, and both can be set by hand: it is a consistency check on the read log, an honesty record, and not proof of order.
- E7 live runs: an experiment with any recorded/runs/<run>/ file declares a positive spend_cap_usd and a launched_by, and each run folder holds an isolation.json proof.
- S1 a synthesis file is syntheses/SYN-NNNN.md, holds at least one finding (a "### " heading), and each finding names its sources.
- S2 each finding has a grade from the Evidence grades table.
- S3 each finding has a falsifier.

That every cited source exists is A5's job, and reserved id shapes are V1's.

Rule numbers follow the repository's check list: E4 is reserved for the
recorded-file hash check, and E5 is the _raw rule, implemented as A10 in
check_admission.py.

A repository with no experiments/ folder is clean. A positive control runs
before any scan: each of E1 to E3, E6, E7 and S1 to S3 must find a planted
problem, or the check is DEAD. Each rule is a function of parsed data, so the
control needs no disk; the history rules take history facts that run()
gathers. A shallow clone is DEAD when an experiment needs history for E6a or
E6b.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
from typing import Dict, List, Optional, Set, Tuple

try:
    import check_admission
    from lib import header, paths, repofiles

    IMPORT_ERROR: Optional[BaseException] = None
except Exception as _exc:  # reported as DEAD by run(), not as a traceback
    check_admission = header = paths = repofiles = None  # type: ignore[assignment]
    IMPORT_ERROR = _exc

CHECK = "experiments"
VOCABULARY = "docs/vocabulary.md"
EXPERIMENTS_DIR = "experiments/"
SOURCE_FILE = re.compile(r"^sources/(SRC-[0-9]{4})/source\.md$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")

Finding = Tuple[str, Optional[int], str]
Record = Dict[str, str]


def read_record(text: str, keys: Tuple[str, ...]) -> Optional[Record]:
    """The header values when the header is clean and has exactly these keys."""
    _title, pairs, problems = header.parse(text)
    if problems or tuple(k for k, _ in pairs) != keys:
        return None
    return dict(pairs)


def readme_path(folder: str) -> str:
    return "%s%s/README.md" % (EXPERIMENTS_DIR, folder)


def experiment_folders(files: List[str]) -> List[str]:
    found: Set[str] = set()
    for path in files:
        match = paths.EXPERIMENT_FILE.match(path)
        if match:
            found.add(match.group(1))
    return sorted(found)


def source_ids(files: List[str]) -> Set[str]:
    return set(m.group(1) for m in (SOURCE_FILE.match(p) for p in files) if m)


def check_e1(
    files: List[str],
    readmes: Dict[str, str],
    protocols: Optional[Set[str]],
    kinds: Optional[Set[str]],
) -> List[Finding]:
    """E1 over the listed paths and the text of each listed README, by folder."""
    found: List[Finding] = []
    listed = set(files)
    for path in files:
        if path.startswith(EXPERIMENTS_DIR) and path.count("/") == 1:
            found.append(
                (
                    path,
                    None,
                    "E1 files under experiments/ must sit inside an experiment folder; "
                    "move it into experiments/EXP-NNNN/",
                )
            )
    folders = experiment_folders(files)
    for folder in folders:
        if not paths.EXPERIMENT_ID.match(folder):
            found.append(
                (
                    EXPERIMENTS_DIR + folder,
                    None,
                    "E1 experiment folder '%s' is not named EXP- plus four digits; "
                    "rename it to experiments/EXP-NNNN" % folder,
                )
            )
        readme = readme_path(folder)
        if readme not in listed or folder not in readmes:
            found.append(
                (
                    EXPERIMENTS_DIR + folder,
                    None,
                    "E1 the experiment has no README.md; add %s with the experiment header" % readme,
                )
            )
            continue
        _title, pairs, problems = header.parse(readmes[folder])
        for number, reason in problems:
            found.append(
                (
                    readme,
                    number,
                    "E1 the README header is not valid (%s); write a '# title' line, "
                    "a blank line, then one 'key: value' line for each field" % reason,
                )
            )
        if problems:
            continue
        if tuple(k for k, _ in pairs) != header.EXPERIMENT_KEYS:
            found.append(
                (
                    readme,
                    None,
                    "E1 the README header keys must be exactly %s, in that order; "
                    "add, remove or reorder the keys" % ", ".join(header.EXPERIMENT_KEYS),
                )
            )
            continue
        values = dict(pairs)
        if values["id"] != folder:
            found.append(
                (
                    readme,
                    None,
                    "E1 id is '%s' but the folder is '%s'; make the id equal the folder name"
                    % (values["id"], folder),
                )
            )
        subject = values["subject"]
        if subject != "method":
            if not check_admission.SRC_ID.match(subject):
                found.append(
                    (
                        readme,
                        None,
                        "E1 subject '%s' is neither method nor a source id; "
                        "write method, or the id of a source record" % subject,
                    )
                )
            elif "sources/%s/source.md" % subject not in listed:
                found.append(
                    (
                        readme,
                        None,
                        "E1 subject names %s, which has no sources/%s/source.md; "
                        "add that source record or correct the id" % (subject, subject),
                    )
                )
        for key, allowed, table in (
            ("protocol", protocols, "Experiment protocols"),
            ("read_log", kinds, "Read-log kinds"),
        ):
            if allowed is not None and values[key] not in allowed:
                found.append(
                    (
                        readme,
                        None,
                        "E1 %s '%s' is not in the %s table of %s; use one of: %s"
                        % (key, values[key], table, VOCABULARY, ", ".join(sorted(allowed))),
                    )
                )
    if folders and (protocols is None or kinds is None):
        found.append(
            (
                VOCABULARY,
                None,
                "E1 cannot read the Experiment protocols and Read-log kinds tables in %s; "
                "restore that file" % VOCABULARY,
            )
        )
    return found


def experiment_subjects(files: List[str], readmes: Dict[str, str]) -> Dict[str, Optional[str]]:
    """Each experiment folder's subject, or None when its README header is unusable."""
    subjects: Dict[str, Optional[str]] = {}
    listed = set(files)
    for folder in experiment_folders(files):
        record = None
        if readme_path(folder) in listed and folder in readmes:
            record = read_record(readmes[folder], header.EXPERIMENT_KEYS)
        subjects[folder] = None if record is None else record["subject"]
    return subjects


def check_e2(records: Dict[str, Optional[Record]]) -> List[Finding]:
    """E2 over each source record's values; None marks a header A8 reports."""
    found: List[Finding] = []
    for path in sorted(records):
        values = records[path]
        if values is None:
            continue
        kind = values.get("source_type")
        if kind == "code-repo":
            if not COMMIT.match(values.get("commit", "")):
                found.append(
                    (
                        path,
                        None,
                        "E2 commit must be 40 lowercase hex characters, the full commit id; "
                        "write the full id of the commit that was read",
                    )
                )
        if kind in ("code-repo", "release-artifact"):
            if values.get("licence") == "-":
                found.append(
                    (
                        path,
                        None,
                        "E2 a %s source needs a licence, and this one has '-'; "
                        "record the licence it was published under" % kind,
                    )
                )
    return found


def check_e3(
    files: List[str],
    subjects: Dict[str, Optional[str]],
    records: Dict[str, Optional[Record]],
    licences: Optional[Set[str]],
) -> List[Finding]:
    """E3 over the listed target files, the experiment subjects and the source records."""
    found: List[Finding] = []
    needs_list = False
    for path in sorted(files):
        match = paths.TARGET_FILE.match(path)
        if not match:
            continue
        experiment = match.group(1)
        subject = subjects.get(experiment)
        if subject is None:
            found.append(
                (
                    path,
                    None,
                    "E3 cannot check the licence because the experiment record is invalid; "
                    "fix the E1 findings first",
                )
            )
        elif subject == "method":
            found.append(
                (
                    path,
                    None,
                    "E3 experiment %s has subject method, so there is no target to take text from; "
                    "remove the file or give the experiment a source subject" % experiment,
                )
            )
        else:
            record = records.get("sources/%s/source.md" % subject)
            if record is None:
                found.append(
                    (
                        path,
                        None,
                        "E3 cannot check the licence because source %s is missing or its record is broken; "
                        "fix that source record first" % subject,
                    )
                )
            elif licences is None:
                needs_list = True
            else:
                licence = record.get("licence", "")
                if licence not in licences:
                    found.append(
                        (
                            path,
                            None,
                            "E3 licence '%s' of %s is not in the Redistributable licences table of %s, "
                            "so text from it may not sit in this file; remove the file or use a source "
                            "with a listed licence" % (licence, subject, VOCABULARY),
                        )
                    )
    if needs_list:
        found.append(
            (
                VOCABULARY,
                None,
                "E3 cannot read the Redistributable licences table in %s; restore that file" % VOCABULARY,
            )
        )
    return found


READ_LOG_HEADER = "| when | path | sha256 | reason |"
READ_LOG_EMPTY = READ_LOG_HEADER + "\n| --- | --- | --- | --- |\n"
WHEN_SHAPE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}Z$")
WHEN_FORMAT = "%Y-%m-%dT%H:%MZ"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SPEND_CAP = re.compile(r"^[0-9]+(\.[0-9]{1,2})?$")
SPEND_ZERO = re.compile(r"^0+(\.0+)?$")
FINDING_FIELD = re.compile(r"^(grade|falsifier|sources): (.*)$")
SOURCE_LIST_SEPARATOR = ","

ReadLogRow = Dict[str, object]


def read_log_path(folder: str) -> str:
    return "%s%s/read-log.md" % (EXPERIMENTS_DIR, folder)


def experiment_records(files: List[str], readmes: Dict[str, str]) -> Dict[str, Optional[Record]]:
    """Each experiment folder's header values, or None when the README header is unusable."""
    records: Dict[str, Optional[Record]] = {}
    listed = set(files)
    for folder in experiment_folders(files):
        record = None
        if readme_path(folder) in listed and folder in readmes:
            record = read_record(readmes[folder], header.EXPERIMENT_KEYS)
        records[folder] = record
    return records


def when_seconds(value: str) -> Optional[int]:
    """Unix seconds for a read-log time, or None when it is not a real UTC time."""
    if not WHEN_SHAPE.match(value):
        return None
    try:
        moment = datetime.datetime.strptime(value, WHEN_FORMAT)
    except ValueError:
        return None
    return int(moment.replace(tzinfo=datetime.timezone.utc).timestamp())


def iso_minutes(seconds: int) -> str:
    moment = datetime.datetime.fromtimestamp(seconds, tz=datetime.timezone.utc)
    return moment.strftime(WHEN_FORMAT)


def parse_read_log(
    text: str, reasons: Optional[Set[str]]
) -> Tuple[List[ReadLogRow], List[Tuple[int, str]]]:
    """The valid rows of a read log, and a (line, finding text) pair for each problem."""
    rows: List[ReadLogRow] = []
    problems: List[Tuple[int, str]] = []
    blocks: List[List[Tuple[int, str]]] = []
    current: Optional[List[Tuple[int, str]]] = None
    for number, line in enumerate(text.split("\n"), 1):
        if line.startswith("|"):
            if current is None:
                current = []
                blocks.append(current)
            current.append((number, line))
        else:
            current = None
    if len(blocks) != 1:
        problems.append(
            (1, "E6 the read log must hold exactly one table; keep one table with the header %s" % READ_LOG_HEADER)
        )
        if not blocks:
            return rows, problems
    block = blocks[0]
    if block[0][1].strip() != READ_LOG_HEADER:
        problems.append((block[0][0], "E6 the read-log table header is not exact; write %s" % READ_LOG_HEADER))
    if len(block) < 2 or not check_admission.SEPARATOR.match(block[1][1].strip()):
        problems.append((block[0][0], "E6 the read-log table has no separator row; add | --- | --- | --- | --- | under the header"))
        body = block[1:]
    else:
        body = block[2:]
    for number, line in body:
        stripped = line.strip()
        cells = [c.strip() for c in stripped.split("|")[1:-1]]
        if not stripped.endswith("|") or len(cells) != 4:
            problems.append((number, "E6 malformed read-log row; write four cells: when, path, sha256, reason"))
            continue
        when, path, sha, reason = cells
        seconds = when_seconds(when)
        bad = False
        if seconds is None:
            bad = True
            problems.append(
                (number, "E6 when '%s' is not a real UTC time like 2026-01-02T03:04Z; rewrite that cell" % when)
            )
        if not path:
            bad = True
            problems.append((number, "E6 path is empty; write the path of the file that was read"))
        if not SHA256.match(sha):
            bad = True
            problems.append((number, "E6 sha256 must be 64 lowercase hex characters; write the digest of the file that was read"))
        if reasons is not None and reason not in reasons:
            bad = True
            problems.append(
                (
                    number,
                    "E6 reason '%s' is not in the Read-log reasons table of %s; use one of: %s"
                    % (reason, VOCABULARY, ", ".join(sorted(reasons))),
                )
            )
        if not bad and seconds is not None:
            rows.append({"line": number, "seconds": seconds, "reason": reason})
    return rows, problems


def method_rows(rows: List[ReadLogRow]) -> List[ReadLogRow]:
    return [r for r in rows if r["reason"] == "method"]


def abstract_of(text: str) -> str:
    """The first paragraph after the header block, or an empty string."""
    lines = text.split("\n")
    index = 2
    while index < len(lines) and lines[index].strip():
        index += 1
    while index < len(lines) and (not lines[index].strip() or lines[index].startswith("#")):
        index += 1
    taken: List[str] = []
    while index < len(lines) and lines[index].strip() and not lines[index].startswith("#"):
        taken.append(lines[index])
        index += 1
    return "\n".join(taken)


def has_word(text: str, word: str) -> bool:
    return re.search(r"\b" + re.escape(word) + r"\b", text, re.IGNORECASE) is not None


def check_e6(
    files: List[str],
    readmes: Dict[str, str],
    logs: Dict[str, str],
    reasons: Optional[Set[str]],
    ancestry: Dict[str, List[Tuple[str, bool]]],
    first_method_time: Dict[str, Optional[int]],
) -> List[Finding]:
    """E6 over the READMEs, the read logs and the history facts, each by folder.

    ancestry holds, per experiment, a (short id, touched_before) pair for each
    commit under recorded/. first_method_time holds the earliest committer time
    of a commit under the method folder, or None when there is none."""
    found: List[Finding] = []
    listed = set(files)
    records = experiment_records(files, readmes)
    for folder in sorted(records):
        record = records[folder]
        if record is None:
            continue
        readme = readme_path(folder)
        log_path = read_log_path(folder)
        rows: List[ReadLogRow] = []
        if log_path not in listed or folder not in logs:
            found.append(
                (
                    log_path,
                    None,
                    "E6 the experiment has no read-log.md; add one with the table header %s" % READ_LOG_HEADER,
                )
            )
        else:
            rows, problems = parse_read_log(logs[folder], reasons)
            for number, reason in problems:
                found.append((log_path, number, reason))
        if record["protocol"] == "preregistered":
            for short, before in ancestry.get(folder, []):
                if not before:
                    found.append(
                        (
                            readme,
                            None,
                            "E6 recorded data in commit %s has no earlier commit of the README, so the "
                            "protocol was not preregistered; set protocol: exploratory, or commit the "
                            "protocol before recorded data" % short,
                        )
                    )
        methods = method_rows(rows)
        if record["read_log"] == "contemporaneous" and methods:
            earliest = min(methods, key=lambda r: int(r["seconds"]))  # type: ignore[call-overload]
            earliest_time = int(earliest["seconds"])  # type: ignore[call-overload]
            first = first_method_time.get(folder)
            if first is None:
                found.append(
                    (
                        log_path,
                        int(earliest["line"]),  # type: ignore[call-overload]
                        "E6 the read log has a method entry but no commit holds the method folder; "
                        "commit the method before reading the target for method work",
                    )
                )
            elif first > earliest_time:
                found.append(
                    (
                        log_path,
                        int(earliest["line"]),  # type: ignore[call-overload]
                        "E6 the first commit under the method folder is at %s, later than the earliest "
                        "method entry at %s; commit the method first, or set read_log: reconstructed"
                        % (iso_minutes(first), iso_minutes(earliest_time)),
                    )
                )
        abstract = abstract_of(readmes.get(folder, ""))
        for applies, word in (
            (record["protocol"] == "exploratory", "exploratory"),
            (record["read_log"] == "reconstructed", "reconstructed"),
        ):
            if applies and not has_word(abstract, word):
                found.append(
                    (
                        readme,
                        None,
                        "E6 the abstract (the first paragraph after the header) must say '%s'; "
                        "add that word to it" % word,
                    )
                )
    if reasons is None and any(r is not None for r in records.values()):
        found.append(
            (
                VOCABULARY,
                None,
                "E6 cannot read the Read-log reasons table in %s; restore that file" % VOCABULARY,
            )
        )
    return found


def check_e7(
    files: List[str], readmes: Dict[str, str], proofs: Dict[str, bytes]
) -> List[Finding]:
    """E7 over the listed paths, the READMEs and the bytes of each listed isolation.json."""
    found: List[Finding] = []
    listed = set(files)
    records = experiment_records(files, readmes)
    for folder in sorted(records):
        record = records[folder]
        if record is None:
            continue
        readme = readme_path(folder)
        cap = record["spend_cap_usd"]
        cap_ok = cap == "-" or (SPEND_CAP.match(cap) is not None and SPEND_ZERO.match(cap) is None)
        if not cap_ok:
            found.append(
                (
                    readme,
                    None,
                    "E7 spend_cap_usd '%s' must be - or a positive amount like 5 or 2.50; "
                    "write a valid cap" % cap,
                )
            )
        runs: Set[str] = set()
        for path in files:
            match = paths.RUN_FILE.match(path)
            if match and match.group(1) == folder:
                runs.add(match.group(2))
        if not runs:
            continue
        if cap == "-":
            found.append(
                (
                    readme,
                    None,
                    "E7 the experiment has recorded runs but spend_cap_usd is -; "
                    "write the positive cap the runs were held to",
                )
            )
        if record["launched_by"] == "-":
            found.append(
                (
                    readme,
                    None,
                    "E7 the experiment has recorded runs but launched_by is -; "
                    "write who launched them",
                )
            )
        for run in sorted(runs):
            proof = "%s%s/recorded/runs/%s/isolation.json" % (EXPERIMENTS_DIR, folder, run)
            if proof not in listed or proof not in proofs:
                found.append(
                    (
                        proof,
                        None,
                        "E7 run '%s' has no isolation.json; add the proof that the run was isolated" % run,
                    )
                )
                continue
            problem = isolation_problem(proofs[proof])
            if problem:
                found.append(
                    (proof, None, "E7 isolation.json %s; fix the file so it records the isolation checks" % problem)
                )
    return found


def isolation_problem(data: bytes) -> Optional[str]:
    """What is wrong with an isolation.json, or None when it is acceptable."""
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return "is not UTF-8 JSON"
    if not isinstance(parsed, dict):
        return "is not a JSON object"
    if parsed.get("isolated") is not True:
        return "does not have \"isolated\": true"
    checks = parsed.get("checks")
    if not isinstance(checks, list) or not checks or not all(isinstance(c, str) and c for c in checks):
        return "needs \"checks\" to be a non-empty list of non-empty strings"
    return None


Block = Dict[str, object]


def synthesis_blocks(text: str) -> List[Block]:
    """The finding blocks of a synthesis: line, field values, and fields given twice."""
    lines = text.split("\n")
    blocks: List[Block] = []
    index = 0
    while index < len(lines):
        if lines[index].startswith("### "):
            block: Block = {"line": index + 1, "fields": {}, "twice": set()}
            index += 1
            while index < len(lines) and not lines[index].startswith("#"):
                match = FINDING_FIELD.match(lines[index])
                if match:
                    fields = block["fields"]
                    if match.group(1) in fields:  # type: ignore[operator]
                        block["twice"].add(match.group(1))  # type: ignore[attr-defined]
                    else:
                        fields[match.group(1)] = match.group(2)  # type: ignore[index]
                index += 1
            blocks.append(block)
        else:
            index += 1
    return blocks


def twice_finding(path: str, block: Block, field: str, rule: str) -> Finding:
    return (
        path,
        int(block["line"]),  # type: ignore[call-overload]
        "%s the finding gives '%s:' twice; keep one line" % (rule, field),
    )


def check_s1(files: List[str], texts: Dict[str, str]) -> List[Finding]:
    """S1 over the listed syntheses paths and the text of each synthesis file."""
    found: List[Finding] = []
    for path in sorted(files):
        if not path.startswith("syntheses/"):
            continue
        if not paths.SYNTHESIS_FILE.match(path):
            found.append(
                (
                    path,
                    None,
                    "S1 a synthesis file must be syntheses/SYN-NNNN.md; rename or move this file",
                )
            )
            continue
        blocks = synthesis_blocks(texts.get(path, ""))
        if not blocks:
            found.append(
                (
                    path,
                    None,
                    "S1 the synthesis holds no finding; add a '### ' heading for each finding",
                )
            )
        for block in blocks:
            fields = block["fields"]
            line = int(block["line"])  # type: ignore[call-overload]
            if "sources" in block["twice"]:  # type: ignore[operator]
                found.append(twice_finding(path, block, "sources", "S1"))
            if "sources" not in fields:  # type: ignore[operator]
                found.append((path, line, "S1 the finding has no 'sources:' line; name its sources"))
                continue
            value = str(fields["sources"]).strip()  # type: ignore[index]
            parts = value.split(SOURCE_LIST_SEPARATOR)
            ids = [parts[0]] + [p.lstrip(" ") for p in parts[1:]]
            if not all(check_admission.SRC_ID.match(i) for i in ids):
                found.append(
                    (
                        path,
                        line,
                        "S1 sources '%s' must be a comma-separated list of source ids; "
                        "write ids like the source folder names" % value,
                    )
                )
    return found


def check_s2(files: List[str], texts: Dict[str, str], grades: Optional[Set[str]]) -> List[Finding]:
    """S2 over the listed syntheses and their text; grades is the Evidence grades table."""
    found: List[Finding] = []
    needs_list = False
    for path in sorted(files):
        if not paths.SYNTHESIS_FILE.match(path):
            continue
        for block in synthesis_blocks(texts.get(path, "")):
            fields = block["fields"]
            line = int(block["line"])  # type: ignore[call-overload]
            if "grade" in block["twice"]:  # type: ignore[operator]
                found.append(twice_finding(path, block, "grade", "S2"))
            if "grade" not in fields:  # type: ignore[operator]
                found.append((path, line, "S2 the finding has no 'grade:' line; give it a grade"))
            elif grades is None:
                needs_list = True
            else:
                value = str(fields["grade"]).strip()  # type: ignore[index]
                if value not in grades:
                    found.append(
                        (
                            path,
                            line,
                            "S2 grade '%s' is not in the Evidence grades table of %s; use one of: %s"
                            % (value, VOCABULARY, ", ".join(sorted(grades))),
                        )
                    )
    if needs_list:
        found.append(
            (VOCABULARY, None, "S2 cannot read the Evidence grades table in %s; restore that file" % VOCABULARY)
        )
    return found


def check_s3(files: List[str], texts: Dict[str, str]) -> List[Finding]:
    """S3 over the listed syntheses and their text."""
    found: List[Finding] = []
    for path in sorted(files):
        if not paths.SYNTHESIS_FILE.match(path):
            continue
        for block in synthesis_blocks(texts.get(path, "")):
            fields = block["fields"]
            line = int(block["line"])  # type: ignore[call-overload]
            if "falsifier" in block["twice"]:  # type: ignore[operator]
                found.append(twice_finding(path, block, "falsifier", "S3"))
            if "falsifier" not in fields:  # type: ignore[operator]
                found.append((path, line, "S3 the finding has no 'falsifier:' line; say what would show it wrong"))
            elif not str(fields["falsifier"]).strip():  # type: ignore[index]
                found.append((path, line, "S3 the falsifier is empty; say what would show the finding wrong"))
    return found


def plant_readme(protocol: str, kind: str, cap: str = "5", abstract: str = "") -> str:
    values = {
        "id": "EXP-0001",
        "subject": "method",
        "protocol": protocol,
        "read_log": kind,
        "spend_cap_usd": cap,
        "launched_by": "someone",
    }
    lines = ["# T", ""] + ["%s: %s" % (k, values[k]) for k in header.EXPERIMENT_KEYS]
    if abstract:
        lines.extend(["", abstract])
    return "\n".join(lines) + "\n"


def control() -> List[str]:
    """Return the rules whose planted bad input produced no finding."""
    target = "experiments/EXP-0001/target/x.txt"
    e1 = check_e1(["experiments/notes.md"], {}, set(), set())
    e2 = check_e2({"sources/s/source.md": {"source_type": "code-repo", "commit": "abc", "licence": "-"}})
    e3 = check_e3([target], {"EXP-0001": "method"}, {}, set())

    readme = "experiments/EXP-0001/README.md"
    log = "experiments/EXP-0001/read-log.md"
    files = [readme, log]
    method_row = "| 2026-01-02T00:00Z | a | %s | method |\n" % ("a" * 64)
    prereg = {"EXP-0001": plant_readme("preregistered", "contemporaneous")}
    exploratory = {"EXP-0001": plant_readme("exploratory", "contemporaneous")}
    reasons = {"method"}
    e6 = (
        check_e6(files, prereg, {"EXP-0001": READ_LOG_EMPTY}, reasons, {"EXP-0001": [("abc1234", False)]}, {})
        and check_e6(files, prereg, {"EXP-0001": READ_LOG_EMPTY + method_row}, reasons, {}, {})
        and check_e6(files, exploratory, {"EXP-0001": READ_LOG_EMPTY}, reasons, {}, {})
    )
    e7 = check_e7(files, {"EXP-0001": plant_readme("preregistered", "contemporaneous", cap="0")}, {})

    synthesis = "syntheses/SYN-0001.md"
    text = {synthesis: "# S\n\n### f\nsources: none\ngrade: x\nfalsifier:   \n"}
    s1 = check_s1([synthesis], text)
    s2 = check_s2([synthesis], text, set())
    s3 = check_s3([synthesis], text)
    return [
        rule
        for rule, hits in (
            ("E1", e1), ("E2", e2), ("E3", e3), ("E6", e6), ("E7", e7),
            ("S1", s1), ("S2", s2), ("S3", s3),
        )
        if not hits
    ]


def format_finding(finding: Finding) -> str:
    path, line, reason = finding
    where = path if line is None else "%s:%d" % (path, line)
    return "%s: %s: %s" % (CHECK, where, reason)


def read_text(root: str, path: str) -> str:
    with open(os.path.join(root, *path.split("/")), "rb") as handle:
        return handle.read().decode("utf-8", "replace")


def read_bytes(root: str, path: str) -> bytes:
    with open(os.path.join(root, *path.split("/")), "rb") as handle:
        return handle.read()


def history_facts(
    root: str,
    files: List[str],
    readmes: Dict[str, str],
    logs: Dict[str, str],
    reasons: Optional[Set[str]],
) -> Tuple[Dict[str, List[Tuple[str, bool]]], Dict[str, Optional[int]]]:
    """The facts E6a and E6b need, gathered only for the experiments that need them."""
    ancestry: Dict[str, List[Tuple[str, bool]]] = {}
    first_method_time: Dict[str, Optional[int]] = {}
    records = experiment_records(files, readmes)
    for folder in sorted(records):
        record = records[folder]
        if record is None:
            continue
        if record["protocol"] == "preregistered":
            readme = readme_path(folder)
            commits = repofiles.commits_touching(root, "%s%s/recorded" % (EXPERIMENTS_DIR, folder))
            ancestry[folder] = [(c[:7], repofiles.touched_before(root, c, readme)) for c in commits]
        if record["read_log"] == "contemporaneous" and folder in logs:
            rows, _problems = parse_read_log(logs[folder], reasons)
            if method_rows(rows):
                commits = repofiles.commits_touching(
                    root, "%s%s/%s" % (EXPERIMENTS_DIR, folder, paths.METHOD_DIR)
                )
                times = [repofiles.commit_time(root, c) for c in commits]
                first_method_time[folder] = min(times) if times else None
    return ancestry, first_method_time


def run(root: str) -> Tuple[int, List[str]]:
    """Run the check on root. Return (exit code, output lines)."""
    if IMPORT_ERROR is not None:
        raise IMPORT_ERROR
    silent = control()
    if silent:
        return 2, [
            "%s: DEAD the positive control produced no finding for %s" % (CHECK, ", ".join(silent))
        ]
    try:
        files = repofiles.file_set(root)
    except repofiles.RepoFilesError as exc:
        return 2, ["%s: DEAD %s" % (CHECK, exc)]
    if not files:
        return 2, ["%s: DEAD the repository holds 0 files" % CHECK]

    listed = set(files)
    protocols = kinds = licences = reasons = grades = None
    if VOCABULARY in listed:
        text = read_text(root, VOCABULARY)
        protocols = check_admission.vocabulary_values(text, "Experiment protocols")
        kinds = check_admission.vocabulary_values(text, "Read-log kinds")
        licences = check_admission.vocabulary_values(text, "Redistributable licences")
        reasons = check_admission.vocabulary_values(text, "Read-log reasons")
        grades = check_admission.vocabulary_values(text, "Evidence grades")

    folders = experiment_folders(files)
    readmes = {f: read_text(root, readme_path(f)) for f in folders if readme_path(f) in listed}
    source_paths = sorted(p for p in files if SOURCE_FILE.match(p))
    records: Dict[str, Optional[Record]] = {}
    for path in source_paths:
        text = read_text(root, path)
        _title, pairs, problems = header.parse(text)
        records[path] = None if problems else dict(pairs)
    subjects = experiment_subjects(files, readmes)
    logs = {f: read_text(root, read_log_path(f)) for f in folders if read_log_path(f) in listed}
    proofs = {
        p: read_bytes(root, p) for p in files if paths.RUN_FILE.match(p) and p.endswith("/isolation.json")
    }
    synthesis_files = [p for p in files if p.startswith("syntheses/")]
    texts = {p: read_text(root, p) for p in synthesis_files if paths.SYNTHESIS_FILE.match(p)}
    try:
        ancestry, first_method_time = history_facts(root, files, readmes, logs, reasons)
    except repofiles.RepoFilesError as exc:
        return 2, ["%s: DEAD %s" % (CHECK, exc)]

    findings: List[Finding] = []
    findings.extend(check_e1(files, readmes, protocols, kinds))
    findings.extend(check_e2(records))
    findings.extend(check_e3(files, subjects, records, licences))
    findings.extend(check_e6(files, readmes, logs, reasons, ancestry, first_method_time))
    findings.extend(check_e7(files, readmes, proofs))
    findings.extend(check_s1(files, texts))
    findings.extend(check_s2(files, texts, grades))
    findings.extend(check_s3(files, texts))

    lines = [format_finding(f) for f in findings]
    if findings:
        lines.append("%s: %d finding(s)" % (CHECK, len(findings)))
        return 1, lines
    lines.append(
        "%s: ok (%d experiments, %d sources, %d syntheses)"
        % (CHECK, len(folders), len(source_paths), len(texts))
    )
    return 0, lines


def main(argv: Optional[List[str]] = None) -> int:
    default_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(description="Check that every experiment is a well-formed record.")
    parser.add_argument("--root", default=default_root, help="repository root")
    args = parser.parse_args(argv)
    try:
        code, lines = run(os.path.abspath(args.root))
    except Exception as exc:
        print("%s: DEAD %s: %s" % (CHECK, type(exc).__name__, exc))
        return 2
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
