"""Fixture specs: load, validate, generate, normalize and install.

A fixture is evals/fixtures/<split>/<id>/spec.json. Validation checks every rule in
FIXTURES.md and returns "<id>: <label>" problems, never the spec text. The
generation half (second half of this file) scripts the spec's turns through
claude_run.run_claude, proves each run, normalizes the persisted transcript and
installs it into a fresh sandbox. Raw transcripts stay under the eval temp root;
the committed fixture directory only receives normalized files.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import claude_run
import isolation

ROOT = Path(__file__).resolve().parents[1]  # experiments/<id>/code/
FIXTURES_DIR = ROOT / "fixtures"
SPLITS = ("dev", "heldout")
CATEGORIES = ("Phase/Status Changes", "Key Decisions", "New Files/Structure", "Patterns/Insights",
              "Blockers/Warnings", "Next Steps")
PROBE_TYPES = ("single-fact", "knowledge-update", "temporal", "abstention")
SOURCES = ("user", "tool", "both")
TOP_KEYS = {"id", "split", "summary", "repo", "allowed_bash", "turns", "history_check", "items", "probes"}
OPTIONAL_TOP_KEYS = {"excluded"}
EXCLUDED_KEYS = {"date", "reason"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ITEM_KEYS = {"id", "statement", "needed", "suits", "oracle_line", "oracle_section", "source", "anchor", "evidence"}
PROBE_KEYS = {"id", "code_answers", "item", "near", "type", "question", "answer", "reject", "excluded"}
ID_RE = re.compile(r"^(dev|heldout)-(\d{3})-[a-z0-9]+(?:-[a-z0-9]+)*$")
TOKEN_RE = re.compile(r"^[A-Z]{6,16}$")
BASH_COMMANDS = ("python3", "git", "ls", "cat", "wc", "head", "tail", "grep", "mkdir", "touch", "echo")
BASH_FORBIDDEN = set(";&|<>`$\n")
STOPWORDS = frozenset("""about after again also been before between both could does each from have into just more most
only other over same should some such than that their them then there these they this those through under until very
what when where which while will with would your""".split())
SIGNPOSTS = ("for the record", "note that", "remember that", "please remember", "important:", "decision:",
             "keep in mind", "don't forget", "make a note", "worth noting", "fyi")
FLOOR_LINES = (20, 80)
ORACLE_MAX_LINES = 200
LONG_TURNS = 30
DEV_COMPLETE = 19
DEV_LONG_MIN = 3
HELDOUT_LONG_MIN = 5
MAX_REPO_BYTES = 200_000
MAX_TURN_CHARS = 2000


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def spec_paths(root=None) -> list:
    root = Path(root) if root else FIXTURES_DIR
    return sorted(p for split in SPLITS for p in (root / split).glob("*/spec.json"))


def load_spec(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_all(root=None) -> list:
    """Return [(path, spec or None)]; None means the file is not valid JSON."""
    out = []
    for path in spec_paths(root):
        try:
            out.append((path, load_spec(path)))
        except (OSError, ValueError):
            out.append((path, None))
    return out


# --------------------------------------------------------------------------
# Oracle rendering
# --------------------------------------------------------------------------

def render_oracle(spec: dict) -> str:
    """Floor plus each needed item's oracle_line under its oracle_section (a missing section goes at the end)."""
    lines = spec["repo"]["files"]["CLAUDE.md"].split("\n")
    by_section: dict = {}
    for item in spec.get("items", []):
        if item.get("needed") is True:
            by_section.setdefault(item["oracle_section"], []).append(item["oracle_line"])
    for section, new_lines in by_section.items():
        heading = f"## {section}"
        if heading in lines:
            at = lines.index(heading) + 1
            while at < len(lines) and not lines[at].startswith("## "):
                at += 1
            while at > 0 and lines[at - 1].strip() == "":
                at -= 1
            lines[at:at] = new_lines
        else:
            while lines and lines[-1] == "":
                lines.pop()
            lines += ["", heading] + new_lines
    return "\n".join(lines).rstrip("\n") + "\n"


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------

def _words(text: str) -> set:
    return {w for w in re.split(r"[^a-z0-9]+", text.casefold()) if len(w) >= 4 and w not in STOPWORDS}


def _is_str(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _all_repo_text(spec: dict) -> str:
    files = spec["repo"]["files"]
    return "\n".join(list(files) + list(files.values()))


def is_excluded(spec) -> bool:
    """True when the spec carries an `excluded` marker at all; a malformed marker still counts (fail safe)."""
    return isinstance(spec, dict) and "excluded" in spec


def probe_is_excluded(probe) -> bool:
    """True when the probe carries an `excluded` key at all; a malformed marker still counts (fail safe)."""
    return isinstance(probe, dict) and "excluded" in probe


def active_probes(spec) -> list:
    """The spec's probes that are not excluded."""
    return [p for p in spec["probes"] if not probe_is_excluded(p)]


def _excluded_problems(spec) -> list:
    if "excluded" not in spec:
        return []
    mark = spec["excluded"]
    if (isinstance(mark, dict) and set(mark) == EXCLUDED_KEYS and isinstance(mark["date"], str)
            and DATE_RE.match(mark["date"]) and _is_str(mark["reason"])):
        return []
    return ["excluded_invalid"]


def validate_spec(spec, directory_name=None) -> list:
    """Return problem labels for one spec (empty means valid)."""
    if not isinstance(spec, dict):
        return ["spec_not_object"]
    sid = spec.get("id")
    bad = []
    unknown = set(spec) - TOP_KEYS - OPTIONAL_TOP_KEYS
    missing = TOP_KEYS - set(spec)
    if unknown:
        bad.append("unknown_top_level_key")
    if missing:
        return bad + [f"missing_key:{k}" for k in sorted(missing)]
    bad += _excluded_problems(spec)
    if not (isinstance(sid, str) and ID_RE.match(sid)):
        bad.append("id_invalid")
    elif spec["split"] != sid.split("-")[0]:
        bad.append("split_mismatch")
    if spec["split"] not in SPLITS:
        bad.append("split_invalid")
    if directory_name is not None and directory_name != sid:
        bad.append("id_directory_mismatch")
    if not _is_str(spec["summary"]):
        bad.append("summary_missing")
    bad += _repo_problems(spec)
    bad += _bash_problems(spec)
    bad += _turn_problems(spec)
    if bad and any(b.startswith(("repo_", "turns_")) for b in bad):
        return bad  # later rules need a sound repo and turns
    bad += _item_problems(spec)
    if any(b.startswith("items_") for b in bad):
        return bad
    bad += _probe_problems(spec)
    bad += _history_problems(spec)
    bad += _size_problems(spec)
    return bad


def _repo_problems(spec) -> list:
    repo = spec["repo"]
    if not (isinstance(repo, dict) and isinstance(repo.get("files"), dict) and repo["files"]):
        return ["repo_files_missing"]
    out = []
    files = repo["files"]
    if "CLAUDE.md" not in files:
        out.append("repo_floor_missing")
    total = 0
    for name, text in files.items():
        parts = Path(name).parts
        if not isinstance(text, str) or not name or name.startswith("/") or ".." in parts or ".git" in parts:
            out.append("repo_path_invalid")
            continue
        total += len(text.encode("utf-8"))
        if name.endswith(".py"):
            try:
                ast.parse(text)
            except SyntaxError:
                out.append("repo_python_does_not_parse")
    if total > MAX_REPO_BYTES:
        out.append("repo_too_large")
    return sorted(set(out))


def _bash_problems(spec) -> list:
    allowed = spec["allowed_bash"]
    if not (isinstance(allowed, list) and allowed):
        return ["allowed_bash_missing"]
    out = []
    for pattern in allowed:
        if not (_is_str(pattern) and len(pattern) <= 80) or BASH_FORBIDDEN & set(pattern) \
                or pattern.split()[0] not in BASH_COMMANDS:
            out.append("allowed_bash_invalid")
    return sorted(set(out))


def _turn_problems(spec) -> list:
    turns = spec["turns"]
    if not (isinstance(turns, list) and turns):
        return ["turns_missing"]
    out = []
    for turn in turns:
        if not _is_str(turn) or len(turn) > MAX_TURN_CHARS:
            out.append("turns_invalid")
        elif turn.lstrip().startswith("/"):
            out.append("turns_slash_command")
        elif any(phrase in turn.casefold().replace("\u2019", "'") for phrase in SIGNPOSTS):
            out.append("turns_signpost")
    return sorted(set(out))


def _item_problems(spec) -> list:
    items = spec["items"]
    if not (isinstance(items, list) and items):
        return ["items_missing"]
    out = []
    ids = [i.get("id") if isinstance(i, dict) else None for i in items]
    if len(set(ids)) != len(ids) or not all(isinstance(i, str) and i for i in ids):
        out.append("items_ids_not_unique")
    floor = spec["repo"]["files"]["CLAUDE.md"]
    turns = [t.casefold() for t in spec["turns"]]
    needed = rejected = 0
    for item in items:
        if not isinstance(item, dict) or set(item) - ITEM_KEYS or not isinstance(item.get("needed"), bool):
            return out + ["items_malformed"]
        if not _is_str(item.get("statement")):
            out.append("items_statement_missing")
            continue
        if item["needed"]:
            needed += 1
            suits = item.get("suits")
            if not (isinstance(suits, list) and suits and all(s in CATEGORIES for s in suits)):
                out.append("items_suits_invalid")
            if not (_is_str(item.get("oracle_line")) and _is_str(item.get("oracle_section"))):
                out.append("items_oracle_missing")
            else:
                if item["oracle_line"] in floor:
                    out.append("items_oracle_line_in_floor")
            if item["statement"] in floor:
                out.append("items_statement_in_floor")
        else:
            rejected += 1
            if item.get("suits") not in ([], None) or "oracle_line" in item or "oracle_section" in item:
                out.append("items_not_needed_has_labels")
        if item.get("source", "user") not in SOURCES:
            out.append("items_source_invalid")
        if item.get("source") == "tool" and any(item["statement"].casefold() in t for t in turns):
            out.append("items_tool_item_stated_in_turns")
        out += _evidence_problems(item, turns)
        if item["needed"]:
            anchor = item.get("anchor")
            if item.get("source") == "tool":
                if anchor is not None:
                    out.append("items_tool_item_has_anchor")
            elif not _is_str(anchor):
                out.append("items_anchor_missing")
            elif not any(anchor in t for t in spec["turns"]):
                out.append("items_anchor_not_in_turns")
        elif "anchor" in item:
            out.append("items_not_needed_has_labels")
    if needed < 5:
        out.append("items_needed_below_5")
    if rejected < 2:
        out.append("items_not_needed_below_2")
    return sorted(set(out))


def _evidence_problems(item, turns) -> list:
    """Tool items carry literal evidence strings that only their tool output produces; nothing else does."""
    evidence = item.get("evidence")
    if item.get("source") != "tool":
        return ["items_evidence_on_non_tool_item"] if "evidence" in item else []
    if not (isinstance(evidence, list) and evidence and all(_is_str(e) and len(e) <= 80 for e in evidence)):
        return ["items_evidence_missing"]
    if any(e.casefold() in t for e in evidence for t in turns):
        return ["items_evidence_in_turns"]
    return []


def _probe_problems(spec) -> list:
    excluded = is_excluded(spec)
    probes = spec["probes"]
    if not (isinstance(probes, list) and probes):
        return ["probes_missing"]
    items = {i["id"]: i for i in spec["items"]}
    turns_text = "\n".join(spec["turns"]).casefold()
    out = []
    ids = [p.get("id") if isinstance(p, dict) else None for p in probes]
    if len(set(ids)) != len(ids) or not all(isinstance(i, str) and i for i in ids):
        out.append("probes_ids_not_unique")
    questions = [p.get("question") for p in probes if isinstance(p, dict)]
    if len({q.casefold() for q in questions if isinstance(q, str)}) != len(questions):
        out.append("probes_questions_not_unique")
    kinds = set()
    covered = set()
    for probe in probes:
        if not isinstance(probe, dict) or set(probe) - PROBE_KEYS:
            return out + ["probes_malformed"]
        if probe_is_excluded(probe):
            if not _is_str(probe["excluded"]):
                out.append("probes_excluded_invalid")
            # The type and content rules below still run; the set rules (kinds, coverage) count active probes only.
        if "code_answers" not in probe:
            if not excluded:
                out.append("probes_code_answers_missing")
        elif not isinstance(probe["code_answers"], bool):
            out.append("probes_code_answers_invalid")
        kind = probe.get("type")
        if kind not in PROBE_TYPES:
            out.append("probes_type_invalid")
            continue
        if not probe_is_excluded(probe):
            kinds.add(kind)
        if not (_is_str(probe.get("question")) and _is_str(probe.get("answer"))
                and isinstance(probe.get("reject"), list) and all(_is_str(r) for r in probe["reject"])):
            out.append("probes_fields_invalid")
            continue
        if kind == "abstention":
            out += _abstention_problems(probe, items, turns_text)
            continue
        item = items.get(probe.get("item"))
        if item is None or not item["needed"]:
            out.append("probes_item_not_a_needed_item")
            continue
        if probe.get("near") is not None:
            out.append("probes_near_only_on_abstention")
        covered.add(item["id"])  # coverage counts excluded probes: the item keeps its probe as evidence
        if kind == "knowledge-update" and not probe["reject"]:
            out.append("probes_knowledge_update_without_stale_answer")
        if probe["answer"].strip().upper() == "UNKNOWN":
            out.append("probes_answer_unknown_on_non_abstention")
        if item.get("source") == "tool" and len(probe["answer"]) >= 5 and probe["answer"].casefold() in turns_text:
            out.append("probes_tool_item_answer_in_turns")
    for item in items.values():
        if item["needed"] and item["id"] not in covered:
            out.append("probes_needed_item_without_probe")
    for kind in ("knowledge-update", "temporal", "abstention"):
        if kind not in kinds:
            out.append(f"probes_missing_{kind.replace('-', '_')}")
    return sorted(set(out))


def _abstention_problems(probe, items, turns_text) -> list:
    out = []
    near = items.get(probe.get("near"))
    if probe.get("item") is not None:
        out.append("probes_abstention_has_item")
    if near is None or not near["needed"]:
        return out + ["probes_abstention_near_missing"]
    if probe["answer"].strip() != "UNKNOWN" or probe["reject"]:
        out.append("probes_abstention_answer_not_unknown")
    question = probe["question"]
    if question.casefold().rstrip("?") in turns_text:
        out.append("probes_abstention_question_in_turns")
    if not _words(question) & _words(near["statement"]):
        out.append("probes_abstention_not_a_near_miss")
    return out


def _history_problems(spec) -> list:
    check = spec["history_check"]
    if not (isinstance(check, dict) and set(check) == {"question", "token"} and _is_str(check["question"])
            and isinstance(check["token"], str) and TOKEN_RE.match(check["token"])):
        return ["history_check_invalid"]
    token = check["token"]
    out = []
    if not any(token in t for t in spec["turns"]):
        out.append("history_token_not_in_turns")
    if token in _all_repo_text(spec):
        out.append("history_token_in_repo")
    leaks = [i for i in spec["items"] if token in json.dumps(i)] + [p for p in spec["probes"] if token in json.dumps(p)]
    if leaks:
        out.append("history_token_in_items_or_probes")
    return out


def _size_problems(spec) -> list:
    out = []
    floor = spec["repo"]["files"]["CLAUDE.md"]
    n = len(floor.rstrip("\n").split("\n"))
    if not FLOOR_LINES[0] <= n <= FLOOR_LINES[1]:
        out.append("floor_length_out_of_range")
    if len(render_oracle(spec).rstrip("\n").split("\n")) >= ORACLE_MAX_LINES:
        out.append("oracle_too_long")
    return out


def _first_sentence_end(turn: str) -> int:
    m = re.search(r"[.?!](\s|$)", turn)
    return m.end() if m else len(turn)


def mid_turn_items(spec: dict) -> list:
    """IDs of needed items whose anchor starts after the first sentence of its turn."""
    out = []
    for item in spec["items"]:
        anchor = item.get("anchor")
        if item.get("needed") is True and anchor:
            for turn in spec["turns"]:
                at = turn.find(anchor)
                if at >= 0:
                    if at >= _first_sentence_end(turn):
                        out.append(item["id"])
                    break
    return out


def is_long(spec: dict) -> bool:
    return len(spec.get("turns", [])) >= LONG_TURNS


# --------------------------------------------------------------------------
# Set-level rules
# --------------------------------------------------------------------------

def category_counts(specs: list) -> dict:
    counts = {c: 0 for c in CATEGORIES}
    for spec in specs:
        for item in spec["items"]:
            if item.get("needed") is True:
                for cat in item["suits"]:
                    counts[cat] += 1
    return counts


def validate_set(specs: list, heldout_complete: bool = False) -> list:
    """Problems that only show across fixtures: id uniqueness, category coverage, long sessions.

    Ids are checked over every spec; the other rules look at the active (not excluded) specs only.
    """
    out = []
    ids = [s.get("id") for s in specs]
    if len(set(ids)) != len(ids):
        out.append("set: ids_not_unique")
    specs = [s for s in specs if not is_excluded(s)]
    if not specs:
        return out + ["set: no_fixtures"]
    counts = category_counts(specs)
    for cat, n in counts.items():
        if n < 2:
            out.append(f"set: category_suited_by_fewer_than_2_items:{cat}")
    dev = [s for s in specs if s["split"] == "dev"]
    heldout = [s for s in specs if s["split"] == "heldout"]
    if len(dev) >= DEV_COMPLETE and sum(is_long(s) for s in dev) < DEV_LONG_MIN:
        out.append("set: dev_split_needs_3_long_fixtures")
    if heldout_complete and sum(is_long(s) for s in heldout) < HELDOUT_LONG_MIN:
        out.append("set: heldout_split_needs_5_long_fixtures")
    return out


def validate_all(root=None, heldout_complete: bool = False) -> tuple:
    """Return (problems, active specs, excluded ids). Problems are '<id>: <label>' lines."""
    problems, specs, valid = [], [], []
    for path, spec in load_all(root):
        name = path.parent.name
        if spec is None:
            problems.append(f"{name}: spec_not_json")
            continue
        found = validate_spec(spec, directory_name=name)
        problems += [f"{name}: {label}" for label in found]
        if not found:
            valid.append(spec)
    specs = [s for s in valid if not is_excluded(s)]
    excluded = sorted(s["id"] for s in valid if is_excluded(s))
    if not problems:
        problems += validate_set(valid, heldout_complete)
    return problems, specs, excluded


def run_validate(root=None, heldout_complete: bool = False, out=None) -> int:
    out = out or sys.stdout
    problems, specs, excluded = validate_all(root, heldout_complete)
    if problems:
        for line in problems:
            print(line, file=out)
        print(f"validate: FAIL {len(problems)} problems", file=out)
        return 1
    counts = category_counts(specs)
    covered = sum(1 for n in counts.values() if n >= 2)
    dev = sum(1 for s in specs if s["split"] == "dev")
    held = len(specs) - dev
    note = f"; {len(excluded)} excluded: {', '.join(excluded)}" if excluded else ""
    print(f"validate: {len(specs)} fixtures OK (dev {dev}, heldout {held}){note}; "
          f"categories covered {covered}/{len(CATEGORIES)}", file=out)
    return 0


# --------------------------------------------------------------------------
# Normalize
# --------------------------------------------------------------------------

KEEP_TYPES = ("user", "assistant")
FIELD_WHITELIST = ("type", "uuid", "parentUuid", "isSidechain", "sessionId", "cwd", "timestamp", "message",
                   "version", "gitBranch", "userType")
DROP_BLOCKS = ("thinking", "redacted_thinking")
DROP_KEYS = ("signature", "requestId", "usage")
PLACEHOLDER_SANDBOX = "{{SANDBOX}}"
PLACEHOLDER_SLUG = "{{SANDBOX_SLUG}}"
PLACEHOLDER_TMP = "{{TMP}}"
PLACEHOLDER_HOME = "{{HOME}}"
PLACEHOLDER_USER = "user"
GIT_ENV = {"GIT_AUTHOR_NAME": "eval", "GIT_AUTHOR_EMAIL": "eval@example.com",
           "GIT_COMMITTER_NAME": "eval", "GIT_COMMITTER_EMAIL": "eval@example.com",
           "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z"}
MAX_ATTEMPTS = 3


class GenError(Exception):
    """A generation step failed. The message is a path-free label."""


def _scrub(node):
    """Drop thinking blocks, signatures, request ids and usage anywhere inside a message."""
    if isinstance(node, dict):
        return {k: _scrub(v) for k, v in node.items() if k not in DROP_KEYS}
    if isinstance(node, list):
        return [_scrub(v) for v in node if not (isinstance(v, dict) and v.get("type") in DROP_BLOCKS)]
    return node


def _empty_message(row: dict) -> bool:
    content = (row.get("message") or {}).get("content")
    return isinstance(content, list) and not content


class UsernameError(Exception):
    """The account name cannot be read, so the transcript cannot be scrubbed. The message is a path-free label."""


def _current_username() -> str:
    """The account running the harness, read at call time: USER, else LOGNAME. Tests monkeypatch this.

    Fails closed: with neither set (or both empty) it raises, and the transcript is not normalized.
    """
    name = os.environ.get("USER") or os.environ.get("LOGNAME")
    if not name:
        raise UsernameError("username unknown: set USER or LOGNAME")
    return name


def _slash_pattern(path: str) -> str:
    """Regex for path as it can appear in dumped JSON text: plain, or with each slash escaped (\\/ or \\\\/)."""
    return r"(?:/|\\{1,2}/)".join(re.escape(part) for part in path.split("/"))


_SL = r"(?:/|\\{1,2}/)"
_SEG = r"[^/\\\s\"]+"
_VAR_FOLDERS_ROOT = (_SL + "(?:private" + _SL + ")?var" + _SL + "folders" + _SL + _SEG + _SL + _SEG + _SL + "T")


def _temp_root_patterns(environ=None) -> list:
    """Regexes for the temp root: $TMPDIR, its realpath, and the macOS var/folders form."""
    environ = environ if environ is not None else os.environ
    patterns = []
    raw = environ.get("TMPDIR") or ""
    for root in {raw, os.path.realpath(raw) if raw else ""}:
        root = root.rstrip("/")
        if len(root) > 1:
            patterns.append(_slash_pattern(root))
    patterns.append(_VAR_FOLDERS_ROOT)
    return patterns


def _placeholder_temp(text: str, environ=None) -> str:
    """Rewrite any path under the temp root to {{TMP}}/..., keeping the rest of the path as it is."""
    for pattern in _temp_root_patterns(environ):
        text = re.sub(pattern + r"(?![A-Za-z0-9_.-])(" + _SL + "*)",
                      lambda m: PLACEHOLDER_TMP + ("/" if m.group(1) else ""), text)
    return text


def _placeholder_identity(text: str) -> str:
    """Home path to {{HOME}}, then the whole-word username to 'user'. The group name is not touched.

    It expects one string, not a JSON dump: a dump hides a name at the start of a line behind backslash-n.
    A name right after one of the five one-letter JSON escapes (a backslash followed by n, r, t, b or f) counts as a
    whole word, because a string can itself show escaped text.
    The stated limit: other encodings of the name are not decoded: unicode escapes, double-escaped text,
    percent-encoding, base64, HTML entities and any other encoding. The backstop is the seal's pre-seal username
    scan, which refuses.
    A known over-replacement: in a Windows-style path, a folder name made of the letter n, r, t, b or f followed by
    the account name (after a backslash) also has the name part replaced. It replaces more, never less, and only in
    normalized transcripts.
    """
    name = _current_username()
    if not name:
        return text
    homes = {"/Users/" + name}
    try:
        homes.add(str(Path.home()).rstrip("/"))
    except (KeyError, RuntimeError):
        pass
    for home in sorted((h for h in homes if len(h) > 1), key=len, reverse=True):
        text = re.sub(_slash_pattern(home) + r"(?![A-Za-z0-9_.-])", PLACEHOLDER_HOME, text)
    return re.sub(r"(?:(?<![A-Za-z0-9_])|(?<=\\[nrtbf]))" + re.escape(name) + r"(?![A-Za-z0-9_])", PLACEHOLDER_USER,
                  text)


def _map_strings(node, fn):
    """A copy of node with fn applied to every string key and string value; other scalars are returned unchanged."""
    if isinstance(node, dict):
        new = {}
        for key, value in node.items():
            new_key = fn(key) if isinstance(key, str) else key
            if new_key in new:
                raise GenError("normalize: two keys are equal after the placeholders")
            new[new_key] = _map_strings(value, fn)
        return new
    if isinstance(node, list):
        return [_map_strings(element, fn) for element in node]
    if isinstance(node, str):
        return fn(node)
    return node


def _placeholder_row(row, aliases, slug, environ=None):
    """Apply the sandbox, slug, temp-root, home and username placeholders to each string of one row."""
    def one(text):
        for alias in aliases:
            text = text.replace(alias, PLACEHOLDER_SANDBOX)
        if slug:
            text = text.replace(slug, PLACEHOLDER_SLUG)
        return _placeholder_identity(_placeholder_temp(text, environ))
    return _map_strings(row, one)


def normalize_transcript(rows: list, sandbox_aliases: list, slug: str, environ=None) -> list:
    """Keep user and assistant rows, strip identifying fields, relink parents, placeholder the sandbox.

    Order matters: sandbox paths first, then the rest of the temp root, then the home path, then the username. The
    steps run on each key and string value, never on a JSON dump (a dump hides a name at the start of a line behind
    backslash-n).
    """
    parent_of = {r.get("uuid"): r.get("parentUuid") for r in rows if isinstance(r, dict) and r.get("uuid")}
    kept = []
    for row in rows:
        if not isinstance(row, dict) or row.get("type") not in KEEP_TYPES:
            continue
        slim = {k: row[k] for k in FIELD_WHITELIST if k in row}
        if "message" in slim:
            slim["message"] = _scrub(slim["message"])
        if _empty_message(slim):
            continue
        kept.append(slim)
    kept_ids = {r.get("uuid") for r in kept}
    for row in kept:
        parent = row.get("parentUuid")
        while parent is not None and parent not in kept_ids:
            parent = parent_of.get(parent)
        row["parentUuid"] = parent
    aliases = sorted({a for a in sandbox_aliases if a}, key=len, reverse=True)
    out = []
    for row in kept:
        out.append(_placeholder_row(row, aliases, slug, environ))
    return out


def sandbox_aliases(sandbox) -> list:
    return [os.path.realpath(str(sandbox)), str(sandbox)]


def write_jsonl(path: Path, rows: list) -> None:
    claude_run.atomic_write(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def read_jsonl(path: Path) -> list:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


# --------------------------------------------------------------------------
# Sandboxes and install
# --------------------------------------------------------------------------

def git_commit_all(path: Path) -> None:
    env = dict(os.environ, **GIT_ENV)
    for args in (["init", "-q"], ["add", "-A"], ["-c", "commit.gpgsign=false", "commit", "-q", "-m", "fixture"]):
        subprocess.run(["git", *args], cwd=str(path), env=env, check=True, capture_output=True)


def snapshot_tree(source: Path, dest: Path) -> list:
    """Copy source to dest without .git or caches; return the relative files."""
    shutil.rmtree(dest, ignore_errors=True)
    files = []
    for dirpath, dirnames, filenames in os.walk(source):
        dirnames[:] = sorted(d for d in dirnames if d not in (".git", "__pycache__"))
        for name in sorted(filenames):
            src = Path(dirpath) / name
            rel = src.relative_to(source)
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest / rel)
            files.append(rel.as_posix())
    return files


def fixture_dir(fixture_id: str, root=None) -> Path:
    return Path(root or FIXTURES_DIR) / fixture_id.split("-")[0] / fixture_id


def install_fixture(fdir: Path, cfg: dict, environ, label: str = "inst", with_transcript: bool = True) -> tuple:
    """Copy fdir/repo into a fresh git sandbox; optionally write the transcript under a new session id.

    Returns (sandbox, session_id or None, config_dir). Every call gets its own sandbox and transcript.
    """
    sandbox = claude_run.make_sandbox(label, environ)
    snapshot_tree(Path(fdir) / "repo", sandbox)
    git_commit_all(sandbox)
    env = claude_run.child_env(cfg, f"install-{uuid.uuid4().hex[:6]}", environ)
    config_dir = claude_run.child_config_dir(env)
    if not with_transcript:
        return sandbox, None, config_dir
    real = os.path.realpath(str(sandbox))
    slug = claude_run.project_slug(Path(real))
    sid = str(uuid.uuid4())
    rows = []
    for row in read_jsonl(Path(fdir) / "transcript.jsonl"):
        text = json.dumps(row, ensure_ascii=False).replace(PLACEHOLDER_SLUG, slug).replace(PLACEHOLDER_SANDBOX, real)
        row = json.loads(text)
        row["sessionId"] = sid
        rows.append(row)
    write_jsonl(config_dir / "projects" / slug / f"{sid}.jsonl", rows)
    claude_run.record_project_dir(config_dir, sandbox, environ)
    return sandbox, sid, config_dir


def answer_text(record: dict) -> str:
    text = (isolation.result_event(record["events"]) or {}).get("result")
    return text if isinstance(text, str) else ""


def install_check(fdir: Path, spec: dict, cfg: dict, environ, runner=None) -> tuple:
    """R1: resume an installed copy (forked, no tools) and ask for the token. R0: the same question cold.

    Returns (ok, labels). Both runs must pass the per-run proof.
    """
    runner = runner or claude_run.run_claude
    markers = isolation.host_markers(environ)
    check = spec["history_check"]
    token, question = check["token"], check["question"]
    labels = []
    sandbox, sid, config_dir = install_fixture(fdir, cfg, environ, "inst")
    prior = {"session_id": sid, "sandbox": str(sandbox), "memory_dir_after": []}
    r1 = runner(cfg, question, sandbox, label="r1", phase="generation", kind="probe", probe=True, resume=sid,
                fork_session=True, prior=prior, extra_session_ids=(sid,), environ=environ)
    p1 = isolation.check(r1, cfg, markers)
    if not p1["ok"]:
        labels += ["R1 proof: " + ",".join(p1["failures"])]
    elif token not in answer_text(r1):
        labels.append("R1 token missing")
    cold, _, _ = install_fixture(fdir, cfg, environ, "cold", with_transcript=False)
    r0 = runner(cfg, question, cold, label="r0", phase="generation", kind="probe", probe=True,
                session_id=str(uuid.uuid4()), environ=environ)
    p0 = isolation.check(r0, cfg, markers)
    if not p0["ok"]:
        labels += ["R0 proof: " + ",".join(p0["failures"])]
    elif token in answer_text(r0):
        labels.append("R0 token present")
    return not labels, labels


# --------------------------------------------------------------------------
# Generate
# --------------------------------------------------------------------------

def _usage_numbers(usage) -> dict:
    keys = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    usage = usage if isinstance(usage, dict) else {}
    return {k: usage[k] for k in keys if isinstance(usage.get(k), (int, float)) and not isinstance(usage.get(k), bool)}


DENIAL_MARKERS = ("permission for this tool use was denied", "requested permissions to use",
                  "was denied automatically")


def _result_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and isinstance(c.get("text"), str))
    return ""


def tool_results(rows: list) -> list:
    """[(text, denied)] for every tool_result block in user rows."""
    out = []
    for row in rows:
        content = (row.get("message") or {}).get("content") if isinstance(row, dict) else None
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                text = _result_text(block)
                out.append((text, any(m in text.casefold() for m in DENIAL_MARKERS)))
    return out


def count_denials(rows: list) -> int:
    return sum(1 for _, denied in tool_results(rows) if denied)


def tool_evidence_missing(rows: list, spec: dict) -> list:
    """IDs of tool items whose evidence strings do not all appear in a tool_result that was not a denial."""
    texts = [t for t, denied in tool_results(rows) if not denied]
    missing = []
    for item in spec["items"]:
        if item.get("needed") is True and item.get("source") == "tool":
            if not all(any(e in t for t in texts) for e in item.get("evidence") or [None]):
                missing.append(item["id"])
    return missing


def rederive_gen_costs(gen: dict) -> dict:
    """A copy of an old gen.json whose per-turn cost_usd held the session-cumulative report, with per-invocation costs.

    Turn order, same rule as claude_run.invocation_cost: turn 1 in full; later turns minus the turn before,
    or in full when that turn is not a number or the difference is negative. total_cost_usd is recomputed.
    Not idempotent: apply it once, to a gen.json written before the per-invocation fix.
    """
    turns, prev = [], None
    for t in gen["turns"]:
        reported = t.get("cost_usd")
        prior = {"cost_usd_reported": prev} if prev is not None else None
        turns.append(dict(t, cost_usd=claude_run.invocation_cost(reported, prior, resumed=t.get("turn", 1) != 1)))
        prev = reported
    return dict(gen, turns=turns, total_cost_usd=round(sum(t["cost_usd"] or 0 for t in turns), 6))


def _attempt(spec: dict, cfg: dict, environ, runner, workdir: Path, markers: list) -> dict:
    """One generation attempt. Returns {"ok", "labels", ...}; nothing is written outside workdir."""
    sandbox = workdir / "repo"
    for name, text in spec["repo"]["files"].items():
        path = sandbox / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    git_commit_all(sandbox)
    sid = str(uuid.uuid4())
    allowed = claude_run.bash_tools(spec["allowed_bash"])
    turns, denials, prior, last = [], [], None, None
    for n, prompt in enumerate(spec["turns"], start=1):
        kwargs = dict(label=f"gen-{n}", phase="generation", allowed_tools=allowed, environ=environ)
        if n == 1:
            rec = runner(cfg, prompt, sandbox, session_id=sid, **kwargs)
        else:
            rec = runner(cfg, prompt, sandbox, resume=sid, prior=prior, **kwargs)
        proof = isolation.check(rec, cfg, markers)
        if not proof["ok"]:
            return {"ok": False, "labels": [f"turn {n} proof: " + ",".join(proof["failures"])]}
        result = isolation.result_event(rec["events"]) or {}
        # rec["cost_usd"] is this invocation's own cost (claude_run.invocation_cost), not the session-cumulative report.
        turns.append({"turn": n, "cost_usd": rec.get("cost_usd"), "duration_ms": rec.get("duration_ms"),
                      **_usage_numbers(rec.get("usage"))})
        denials += [str(d.get("tool_name", "?")) for d in (result.get("permission_denials") or [])
                    if isinstance(d, dict)]
        prior, last = rec, rec
    if (sandbox / "CLAUDE.md").read_bytes() != spec["repo"]["files"]["CLAUDE.md"].encode("utf-8"):
        return {"ok": False, "labels": ["claude_md_changed"]}
    transcript = Path(last["transcript"]) if last.get("transcript") else None
    if not transcript or not transcript.is_file():
        return {"ok": False, "labels": ["transcript_missing"]}
    rows = normalize_transcript(read_jsonl(transcript), sandbox_aliases(sandbox),
                                claude_run.project_slug(Path(os.path.realpath(str(sandbox)))))
    out = workdir / "out"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir()
    write_jsonl(out / "transcript.jsonl", rows)
    snapshot_tree(sandbox, out / "repo")
    by_tool: dict = {}
    for name in denials:
        by_tool[name] = by_tool.get(name, 0) + 1
    gen = {"claude_code_version": cfg.get("claude_code_version"), "model": cfg.get("model"), "turns": turns,
           "denials": {"count": len(denials), "tools": by_tool, "in_transcript": count_denials(rows)},
           "total_cost_usd": round(sum(t["cost_usd"] or 0 for t in turns), 6)}
    claude_run.atomic_write(out / "gen.json", json.dumps(gen, indent=2) + "\n")
    missing = tool_evidence_missing(rows, spec)
    if missing:
        return {"ok": False, "labels": [f"tool_evidence_missing:{i}" for i in missing]}
    return {"ok": True, "labels": [], "out": out, "gen": gen}


def generate_fixture(fixture_id: str, cfg: dict, environ, runner=None, root=None, out=None) -> int:
    """Generate, normalize, verify and install one fixture. Exit 0 only when it is installed."""
    out = out or sys.stdout
    runner = runner or claude_run.run_claude
    fdir = fixture_dir(fixture_id, root)
    try:
        spec = load_spec(fdir / "spec.json")
    except (OSError, ValueError):
        print(f"{fixture_id}: FAIL spec not found", file=out)
        return 1
    if validate_spec(spec, directory_name=fdir.name):
        print(f"{fixture_id}: FAIL spec invalid", file=out)
        return 1
    markers = isolation.host_markers(environ)
    base = claude_run.eval_root(environ) / "gen" / fixture_id
    last_labels = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        workdir = base / f"attempt-{attempt}-{uuid.uuid4().hex[:6]}"
        workdir.mkdir(parents=True)
        result = _attempt(spec, cfg, environ, runner, workdir, markers)
        last_labels = result["labels"]
        if not result["ok"]:
            continue
        staged = result["out"]
        ok, labels = install_check(staged, spec, cfg, environ, runner)
        if not ok:
            last_labels = labels
            continue
        for name in ("transcript.jsonl", "gen.json"):
            shutil.copyfile(staged / name, fdir / name)
        snapshot_tree(staged / "repo", fdir / "repo")
        gen = result["gen"]
        print(f"{fixture_id}: turns {len(gen['turns'])}/{len(spec['turns'])} ok, isolation PASS, claude_md unchanged, "
              f"denials {gen['denials']['count']}, cost ${gen['total_cost_usd']:.2f}", file=out)
        print(f"install-check {fixture_id}: R1 token found; R0 token absent; PASS", file=out)
        return 0
    print(f"{fixture_id}: FAIL after {MAX_ATTEMPTS} attempts: {'; '.join(last_labels)}", file=out)
    return 1


def run_install_check(fixture_id: str, cfg: dict, environ, runner=None, root=None, out=None) -> int:
    out = out or sys.stdout
    fdir = fixture_dir(fixture_id, root)
    try:
        spec = load_spec(fdir / "spec.json")
    except (OSError, ValueError):
        print(f"install-check {fixture_id}: FAIL spec not found", file=out)
        return 1
    if not ((fdir / "transcript.jsonl").is_file() and (fdir / "repo").is_dir()):
        print(f"install-check {fixture_id}: FAIL fixture not generated", file=out)
        return 1
    ok, labels = install_check(fdir, spec, cfg, environ, runner)
    if ok:
        print(f"install-check {fixture_id}: R1 token found; R0 token absent; PASS", file=out)
        return 0
    print(f"install-check {fixture_id}: FAIL {'; '.join(labels)}", file=out)
    return 1


# --------------------------------------------------------------------------
# Offline re-check of an installed fixture
# --------------------------------------------------------------------------

def install_verify(fixture_id: str, environ=None, root=None, out=None) -> int:
    """Evidence gate over an installed fixture. No model call."""
    environ = environ if environ is not None else os.environ
    out = out or sys.stdout
    fdir = fixture_dir(fixture_id, root)
    try:
        spec = load_spec(fdir / "spec.json")
        rows = read_jsonl(fdir / "transcript.jsonl")
    except (OSError, ValueError):
        print(f"install-verify {fixture_id}: FAIL fixture not generated", file=out)
        return 1
    if not ((fdir / "repo").is_dir() and (fdir / "gen.json").is_file()):
        print(f"install-verify {fixture_id}: FAIL fixture not generated", file=out)
        return 1
    if validate_spec(spec, directory_name=fdir.name):
        print(f"install-verify {fixture_id}: FAIL spec invalid", file=out)
        return 1
    labels = [f"tool_evidence_missing:{i}" for i in tool_evidence_missing(rows, spec)]
    if labels:
        print(f"install-verify {fixture_id}: FAIL {'; '.join(labels)}", file=out)
        return 1
    strings = sum(len(i.get("evidence", [])) for i in spec["items"])
    print(f"install-verify {fixture_id}: evidence {strings}/{strings} found, PASS", file=out)
    return 0


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def _guarded(label: str, work, environ, out) -> int:
    """Shared live gate for gen-fixture and install-check; known refusals print a path-free label."""
    environ = environ if environ is not None else os.environ
    out = out or sys.stdout
    if environ.get("CPR_EVAL_LIVE") != "1":
        print("live run refused: set CPR_EVAL_LIVE=1", file=out)
        return 2
    if environ.get("CLAUDE_CONFIG_DIR"):
        print("unset CLAUDE_CONFIG_DIR", file=out)
        return 3
    try:
        cfg = claude_run.load_config()
        reason = claude_run.preflight(cfg, environ)
        if reason:
            print(f"{label}: STOP preflight: {reason}", file=out)
            return 1
        return work(cfg, environ, out)
    except (claude_run.SpendError, claude_run.SpendCapReached, claude_run.LiveRefused, claude_run.ConfigError,
            claude_run.MemoryDirError, claude_run.PluginRepoError, GenError) as exc:
        print(f"{label}: STOP {exc}", file=out)
    except Exception as exc:  # label only: exception text can carry paths
        print(f"{label}: ERROR {type(exc).__name__}", file=out)
    return 1


def _excluded_stop(label: str, fixture_id: str, out, root) -> bool:
    """Print the STOP line and return True when the fixture's spec is excluded. Reads the spec only."""
    try:
        spec = load_spec(fixture_dir(fixture_id, root) / "spec.json")
    except (OSError, ValueError):
        return False
    if not is_excluded(spec):
        return False
    print(f"{label}: STOP fixture excluded", file=out or sys.stdout)
    return True


def command_gen_fixture(fixture_id: str, environ=None, out=None, runner=None, root=None) -> int:
    if _excluded_stop(fixture_id, fixture_id, out, root):
        return 1
    return _guarded(fixture_id, lambda cfg, env, o: generate_fixture(fixture_id, cfg, env, runner, root, o), environ, out)


def command_install_check(fixture_id: str, environ=None, out=None, runner=None, root=None) -> int:
    if _excluded_stop(f"install-check {fixture_id}", fixture_id, out, root):
        return 1
    return _guarded(f"install-check {fixture_id}",
                    lambda cfg, env, o: run_install_check(fixture_id, cfg, env, runner, root, o), environ, out)
