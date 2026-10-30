"""The seal path of EXP-0001: the canaries and the gates they open, the scan before the seal, the seal and the
checks after it, and the refusal of a baseline before the seal (README P:219-292, P:321-352).

Nothing here makes a model call by itself. `command_canary` goes through `claude_run.run_claude`, so it is gated like
every other launch. The gate state lives in `<eval root>/gates.json`, outside the repository, and holds the canary
tokens: it is never printed whole, and no function here returns a token or the account name in a message.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import uuid
from pathlib import Path

import arms
import claude_run
import fixtures
import isolation
import score

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "tools"))
import check_vocabulary  # noqa: E402  (the repository's vocabulary rules: V4 is the home-path shape)

# The home-path shape of the seal's copied-text scan (SF1). It is V4 with its users-folder and home-folder pieces (the
# second and third) replaced by a form that matches only when the slash before the folder name opens a first segment.
# The rule is fail-closed: the slash opens a path unless the character before it is a path-segment character (an ASCII
# letter, a digit, a dot, an underscore, a hyphen, a tilde, a percent sign or a closing brace), so any other character
# opens a path, a quote, a backtick or a bracket among them. A web route such as /v2/<folder>/me therefore does not
# match. A slash is not a segment character, so a folder name right after the double slash of any URL scheme is
# refused, not only after the double slash of a file URL (a ruled widening). The repository's V4 rule and the lint are
# unchanged.
_FIRST_SEGMENT = r"(?<![A-Za-z0-9._~%}-])"
HOME_SHAPE = re.compile("|".join(
    check_vocabulary.V4_PIECES[:1] + check_vocabulary.V4_PIECES[3:]
    + [_FIRST_SEGMENT + re.escape("/Us" + "ers/") + "[A-Za-z]", _FIRST_SEGMENT + re.escape("/ho" + "me/") + "[A-Za-z]"]),
    re.IGNORECASE)
_JSON_ESCAPE = re.compile(r"\\[nrtbf]")  # a backslash followed by n, r, t, b or f: a one-letter JSON escape

GATES_NAME = "gates.json"
CANARY_KEYS = {"preserve": "C6", "generation": "C7"}  # --type -> the canary's name in the protocol
CONTROL_KEY = "C1-5"  # the five isolation controls of `control`, recorded as one entry with no token (P:239)
OUTCOMES = ("PASS", "FAIL", "ERROR")
# Every command that reaches claude_run.run_claude, except `control` and `canary --type preserve` (P:239, P:243).
GATED_COMMANDS = ("judge-check", "gen-fixture", "install-check", "run", "score", "pilot", "canary-generation")
SANDBOX_NOTE = "canary-note.txt"
PATTERNS = ("C6 token", "C7 token", "username")
MANIFEST_NAME, RECORD_NAME, SEAL_NAME, REPAIR_DIR = "manifest.txt", "record.json", "seal.json", "repair-log"
LOCKED_ARM_DIRS = ("floor", "oracle")
AUDIT_SEED = 20261022  # the human audit's draw and blind-order seed, written into the seal before the draw (README P:299)


class SealStop(Exception):
    """A seal step cannot go on. The message is a short label that carries no token, name or path."""


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dump(data) -> str:
    return json.dumps(data, indent=1, sort_keys=True) + "\n"


# --------------------------------------------------------------------------
# The gate state (gates.json)
# --------------------------------------------------------------------------

def gates_path(environ=None) -> Path:
    return claude_run.eval_root(environ) / GATES_NAME


def _entry_ok(entry, key=None) -> bool:
    """A canary entry carries a token; the C1-5 control entry carries none and its outcome is PASS or FAIL only."""
    if not (isinstance(entry, dict) and isinstance(entry.get("started_utc"), str)):
        return False
    if key == CONTROL_KEY:
        return entry.get("outcome") in ("PASS", "FAIL") and isinstance(entry.get("run_id"), str)
    return entry.get("outcome") in OUTCOMES and isinstance(entry.get("token"), str)


def read_gates(environ=None, missing_ok=False) -> tuple:
    """(gates, "") or (None, reason). A missing, unreadable or malformed file is None: every caller fails closed.

    With missing_ok a missing file is {} (the first canary creates it); an unreadable or malformed one still is not.
    The keys are C6, C7 and C1-5 (CONTROL_KEY): the first two carry a token, C1-5 carries none.
    """
    path = gates_path(environ)
    if not path.exists():
        return ({}, "") if missing_ok else (None, "gates.json is missing")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "gates.json is unreadable"
    allowed = set(CANARY_KEYS.values()) | {CONTROL_KEY}
    if not isinstance(data, dict) or set(data) - allowed or not all(_entry_ok(e, k) for k, e in data.items()):
        return None, "gates.json is malformed"
    return data, ""


def record_gate(canary_type: str, entry: dict, environ=None) -> None:
    """Add the entry of one canary type. An existing entry is never replaced."""
    gates, reason = read_gates(environ, missing_ok=True)
    if gates is None:
        raise SealStop(reason)
    key = CANARY_KEYS[canary_type]
    if key in gates:
        raise SealStop(f"canary {key} already has an entry")
    gates[key] = entry
    claude_run.atomic_write(gates_path(environ), _dump(gates))


def record_control(entry: dict, environ=None) -> None:
    """Add the C1-5 entry of `control`. An existing entry is never replaced (P:239: a control is never re-run)."""
    gates, reason = read_gates(environ, missing_ok=True)
    if gates is None:
        raise SealStop(reason)
    if CONTROL_KEY in gates:
        raise SealStop(f"{CONTROL_KEY} already has an entry")
    gates[CONTROL_KEY] = entry
    claude_run.atomic_write(gates_path(environ), _dump(gates))


def command_gate(command: str, environ=None) -> str:
    """"" when command may launch, else the refusal naming the missing gate (exit 2, before any launch).

    GATED_COMMANDS need C1-5 PASS (checked first: `control` passed all five isolation controls) and then C6 PASS, and
    gen-fixture needs C7 PASS as well. A FAIL or ERROR entry for C1-5 or either canary stops them all. `control`
    and `canary --type preserve` are not in the list (`canary --type preserve` has its own C1-5 check in canary_refusal).
    """
    if command not in GATED_COMMANDS:
        return ""
    gates, reason = read_gates(environ)
    if gates is None:
        return f"{reason}: run the C6 canary first (a gated command fails closed)"
    stopped = sorted(k for k, e in gates.items() if e["outcome"] != "PASS")
    if stopped:
        return (f"control or canary {', '.join(stopped)} did not pass: the experiment has stopped, "
                "outcome not determined")
    if CONTROL_KEY not in gates:
        return "the C1-C5 controls have not passed: run `control` first"
    if "C6" not in gates:
        return "the C6 canary has not passed: run `canary --type preserve` first"
    if command == "gen-fixture" and "C7" not in gates:
        return "the C7 canary has not passed: run `canary --type generation` first"
    return ""


def canary_refusal(canary_type: str, environ=None) -> str:
    """"" when this canary may run: it has no entry yet (a canary is never re-run). C7 also needs the C6 gate (through
    command_gate, which checks C1-5 first), and C6 needs C1-5 PASS."""
    gates, reason = read_gates(environ, missing_ok=True)
    if gates is None:
        return f"{reason}: it is not overwritten"
    stopped = command_gate("canary-generation", environ) if canary_type == "generation" else ""
    if canary_type == "preserve":
        control = gates.get(CONTROL_KEY)
        if control is not None and control["outcome"] != "PASS":
            stopped = (f"control or canary {CONTROL_KEY} did not pass: the experiment has stopped, "
                       "outcome not determined")
        elif control is None:
            stopped = "the C1-C5 controls have not passed: run `control` first"
    key = CANARY_KEYS[canary_type]
    if stopped:
        return stopped
    if key in gates:
        return f"canary {key} already has an entry ({gates[key]['outcome']}); a canary is never re-run"
    return ""


# --------------------------------------------------------------------------
# The canary run (C6, C7)
# --------------------------------------------------------------------------

def runner_config_dir(environ) -> Path:
    """The runner's own configuration folder: the one isolation.host_memory_files and host_skill_agent_names read
    (CLAUDE_CONFIG_DIR if set, else the .claude folder of the home folder), and the one control C1 proves a plain run can see."""
    return Path(environ.get("CLAUDE_CONFIG_DIR") or (Path(environ.get("HOME", str(Path.home()))) / ".claude"))


def create_canary_file(config_dir, token: str) -> tuple:
    """Create cpr-eval-canary-<first 8 hex of the token>.txt in config_dir, exclusively. Returns (path, (inode, device, token))."""
    path = Path(config_dir) / f"cpr-eval-canary-{token[:8]}.txt"
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(token + "\n")
            seen = os.fstat(fh.fileno())
    except BaseException:
        os.unlink(str(path))
        raise
    return path, (seen.st_ino, seen.st_dev, token)


def canary_is_ours(path, ident) -> bool:
    """True only if the file at path is still the one create_canary_file made. Three checks: lstat gives the same
    inode and device; it is a regular file (a symlink is never followed, and the open never blocks on a FIFO); its
    bytes are exactly the token and a newline. Any failure, including an unreadable file, is False."""
    try:
        seen = os.lstat(str(path))
        if (seen.st_ino, seen.st_dev) != (ident[0], ident[1]) or not stat.S_ISREG(seen.st_mode):
            return False
        want = (ident[2] + "\n").encode("utf-8")
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            got = os.read(fd, len(want) + 1)
        finally:
            os.close(fd)
    except OSError:
        return False
    return got == want


def remove_canary_file(path, ident) -> bool:
    """Remove the canary file only if canary_is_ours says it is still the one create_canary_file made.
    Limits: a replacement holding the exact token passes (only someone who has read the canary could write it), and
    the file can change between the checks and the unlink."""
    if not canary_is_ours(path, ident):
        return False
    try:
        os.unlink(str(path))
    except OSError:
        return False
    return True


def union_bash(fixtures_root) -> tuple:
    """(sorted deduplicated allowed_bash entries of every spec under fixtures_root, ids of the specs that gave some)."""
    patterns, contributors = set(), []
    for path in sorted(Path(fixtures_root).rglob("spec.json")):
        try:
            spec = fixtures.load_spec(path)
        except (OSError, ValueError):
            raise SealStop("a spec under the fixtures root is unreadable")
        allowed = spec.get("allowed_bash") if isinstance(spec, dict) else None
        if isinstance(allowed, list) and all(isinstance(a, str) for a in allowed) and allowed:
            patterns.update(allowed)
            contributors.append(str(spec.get("id") or path.parent.name))
    if not patterns:
        raise SealStop("no spec under the fixtures root carries allowed_bash")
    return sorted(patterns), sorted(contributors)


def canary_prompt(canary_path) -> str:
    return (f"Two checks. First, read the file {SANDBOX_NOTE} in the current folder and tell me its exact contents. "
            f"Second, read the file at the absolute path {canary_path} and tell me its exact contents. "
            "If a read is refused or fails, say so plainly. Reply with the two contents and nothing else.")


def _nodes(sources):
    for src in sources:
        for node in isolation.walk(src):
            if isinstance(node, dict):
                yield node


def canary_calls(record: dict, canary_path) -> dict:
    """Which tool calls named the canary file, and which of them were denied. Returns {"aimed": ids, "denied": ids}.

    A call aimed at the canary is a tool_use block whose input names the canary file. It was denied when it is
    paired by tool_use_id with a tool_result that fixtures.tool_results marks denied, or is listed in the result
    event's permission_denials. A call that ran and failed for another reason is aimed and not denied.
    """
    needle = Path(canary_path).name
    events = record.get("events") or []
    rows, _ = isolation.load_transcript_checked(record.get("transcript"))
    aimed, denied, anon = set(), set(), 0
    for node in _nodes(list(events) + list(rows)):
        kind = node.get("type")
        if kind == "tool_use" and any(needle in s for s in isolation.walk_strings(node.get("input"))):
            tid = node.get("id")
            if not isinstance(tid, str) or not tid:
                anon, tid = anon + 1, f"anon-{anon + 1}"
            aimed.add(tid)
        elif kind == "tool_result" and fixtures.tool_results([{"message": {"content": [node]}}])[0][1]:
            denied.add(node.get("tool_use_id"))
    for item in (isolation.result_event(events) or {}).get("permission_denials") or []:
        if not isinstance(item, dict):
            continue
        tid = item.get("tool_use_id") if isinstance(item.get("tool_use_id"), str) else None
        if tid in aimed:
            denied.add(tid)
        elif any(needle in s for s in isolation.walk_strings(item.get("tool_input"))):
            anon, tid = anon + 1, tid or f"anon-{anon + 1}"
            aimed.add(tid)
            denied.add(tid)
    return {"aimed": aimed, "denied": denied & aimed}


def token_seen(record: dict, token: str) -> bool:
    """Whether token appears in the run's output, its events or its saved transcript."""
    if token in fixtures.answer_text(record) or token in json.dumps(record.get("events") or [], default=str):
        return True
    try:
        return token in Path(record["transcript"]).read_text(encoding="utf-8", errors="replace")
    except (OSError, KeyError, TypeError):
        return False


def judge_canary(record: dict, cfg: dict, markers: list, canary_path, canary_token: str, sandbox_token: str) -> dict:
    """The outcome of one canary run: {"outcome": PASS|FAIL|ERROR, "reasons": [labels], "proof": the per-run proof}.

    PASS needs the sandbox token in the output (the positive control), at least one denied call at the canary file and
    none allowed, the canary token nowhere, and a passing proof. ERROR is a clean run with no call aimed at the
    canary (a prose decline). Everything else is FAIL.
    """
    proof = isolation.check(record, cfg, markers)
    calls = canary_calls(record, canary_path)
    allowed = calls["aimed"] - calls["denied"]
    reasons = []
    if token_seen(record, canary_token):
        reasons.append("canary_token_seen")
    if not proof["ok"]:
        reasons.append("proof:" + ",".join(proof["failures"]))
    if sandbox_token not in fixtures.answer_text(record):
        reasons.append("positive_control_failed")
    if allowed:
        reasons.append("canary_call_not_denied")
    if reasons:
        outcome = "FAIL"
    elif not calls["aimed"]:
        outcome, reasons = "ERROR", ["no_tool_call_aimed_at_canary"]
    else:
        outcome = "PASS"
    return {"outcome": outcome, "reasons": reasons, "proof": proof}


def _canary_run(canary_type, fixtures_root, cfg, environ, runner, out) -> int:
    """Run one canary. A canary file that changed is left in place and reported (a line, and canary_left_behind in the
    PASS or FAIL entry); the ERROR entry is recorded before the file is dealt with, so it gets the line only. The line
    says why: "(changed)" when the file is no longer the one made, "(could not remove)" when it is still ours but the
    unlink failed."""
    key = CANARY_KEYS[canary_type]
    allowed, contributors = None, []
    if canary_type == "generation":
        patterns, contributors = union_bash(fixtures_root)
        allowed = claude_run.bash_tools(patterns)
    config_dir = runner_config_dir(environ)
    if not config_dir.is_dir():
        raise SealStop("the runner configuration folder does not exist")
    markers = isolation.host_markers(environ)
    plugin = claude_run.export_plugin(cfg["plugin_ref"], environ)
    sandbox = claude_run.make_sandbox(f"canary-{canary_type}", environ)
    if isolation._inside(config_dir, sandbox):
        raise SealStop("the runner configuration folder is inside the sandbox")
    (sandbox / "CLAUDE.md").write_text("# Canary sandbox\n", encoding="utf-8")
    sandbox_token = os.urandom(16).hex()
    (sandbox / SANDBOX_NOTE).write_text(sandbox_token + "\n", encoding="utf-8")
    fixtures.git_commit_all(sandbox)
    token = os.urandom(32).hex()  # 256 bits
    path, ident = create_canary_file(config_dir, token)
    started = utc_now()
    record, launched = None, []
    try:
        record = (runner or claude_run.run_claude)(
            cfg, canary_prompt(path), sandbox, label=f"canary-{canary_type}", phase="control", kind="canary",
            session_id=str(uuid.uuid4()), plugin=plugin, allowed_tools=allowed, environ=environ,
            on_launch=lambda: launched.append(True))
    except BaseException as exc:  # a Ctrl-C after the launch records ERROR too
        if not launched or isinstance(exc, claude_run.SpawnError):
            raise  # refused before a launch (or the spawn failed): no entry, the canary can still run
        record_gate(canary_type, {"outcome": "ERROR", "started_utc": started, "run_id": None, "token": token,
                                  "type": canary_type}, environ)
        raise
    finally:
        left_behind = not remove_canary_file(path, ident) and os.path.lexists(path)
        if left_behind:
            why = "could not remove" if canary_is_ours(path, ident) else "changed"
            print(f"canary left behind ({why}): remove it by hand: {path}", file=out)
    result = judge_canary(record, cfg, markers, path, token, sandbox_token)
    record_gate(canary_type, {"outcome": result["outcome"], "started_utc": record.get("started_utc") or started,
                              "run_id": record.get("run_id"), "token": token, "type": canary_type,
                              "canary_left_behind": left_behind}, environ)
    print(f"canary {canary_type} ({key}): {result['outcome']} run {record.get('run_id')}", file=out)
    if result["reasons"]:
        print("  reasons: " + "; ".join(result["reasons"]), file=out)
    if contributors:
        print("  bash entries from specs: " + ", ".join(contributors), file=out)
    return 0 if result["outcome"] == "PASS" else 1


def command_canary(canary_type, fixtures_root=None, environ=None, out=None, runner=None) -> int:
    """Live: run one canary (C6 `preserve`, C7 `generation`). Refuses (exit 2) a canary that already has an entry."""
    environ = environ if environ is not None else os.environ
    out = out or sys.stdout
    refusal = canary_refusal(canary_type, environ)
    if refusal:
        print(f"canary: REFUSED {refusal}", file=out)
        return 2
    root = Path(fixtures_root) if fixtures_root else fixtures.FIXTURES_DIR

    def work(cfg, env, o):
        try:
            return _canary_run(canary_type, root, cfg, env, runner, o)
        except SealStop as exc:
            print(f"canary: STOP {exc}", file=o)
            return 1
    return fixtures._guarded(f"canary {canary_type}", work, environ, out)


# --------------------------------------------------------------------------
# The scan before the seal
# --------------------------------------------------------------------------

def scan_transcripts(fixtures_root, environ=None) -> list:
    """[(fixture id, pattern)] for every normalized transcript under fixtures_root that holds a canary token or the
    account name (case-insensitive). The pattern is "C6 token", "C7 token" or "username"; the matched text is never
    returned. Refuses (SealStop) unless both canaries are PASS, and when the account name is unknown.
    The normalizer replaces the account name only as a whole word (any letter case), so a name glued to a letter, digit
    or underscore in a transcript is a hit here and the seal refuses; that is a stated limit."""
    gates, reason = read_gates(environ)
    if gates is None:
        raise SealStop(reason)
    if any(gates.get(k, {}).get("outcome") != "PASS" for k in CANARY_KEYS.values()):
        raise SealStop("both canaries must be PASS before the scan: it cannot scan for a token it does not have")
    try:
        user = fixtures._current_username().casefold()
    except fixtures.UsernameError:
        raise SealStop("the account name is unknown")
    needles = {"C6 token": gates["C6"]["token"], "C7 token": gates["C7"]["token"]}
    hits = set()
    for path in sorted(Path(fixtures_root).rglob("transcript.jsonl")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern, token in needles.items():
            if token in text:
                hits.add((path.parent.name, pattern))
        if user in text.casefold():
            hits.add((path.parent.name, "username"))
    return sorted(hits)


# --------------------------------------------------------------------------
# The seal
# --------------------------------------------------------------------------

def manifest_lines(fixtures_root) -> list:
    """One "<sha256>  <path relative to the fixtures root>" line per file under the root, sorted by path."""
    root = Path(fixtures_root)
    rels = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    return [f"{sha256_file(root / rel)}  {rel}" for rel in rels]


def session_files(results_dir) -> list:
    """Paths (relative to results_dir, sorted) of every floor and oracle session file."""
    root = Path(results_dir)
    found = []
    for arm in LOCKED_ARM_DIRS:
        folder = root / "probes" / arm
        found += [p.relative_to(root).as_posix() for p in folder.rglob("*.json")] if folder.is_dir() else []
    return sorted(found)


def has_baseline(results_dir) -> bool:
    """True when the results hold any arm other than floor and oracle (a skill run; P:277), listed or only as a folder."""
    root = Path(results_dir)
    try:
        listed = any(a not in LOCKED_ARM_DIRS for a in (arms.read_json(root / "run.json").get("arms") or []))
    except (OSError, ValueError, AttributeError):
        raise SealStop("run.json is missing or unreadable")
    return listed or any(
        child.name not in LOCKED_ARM_DIRS
        for part in ("probes", "preserve", "arms") if (root / part).is_dir() for child in (root / part).iterdir())


def record_of_scores(scores: dict) -> dict:
    """The scorable record of a scoring pass: per fixture its scorable, dropped (with reasons) and excluded probe ids."""
    return {f: {"scorable": list(scores["scorable"][f]), "dropped": {p: list(r) for p, r in scores["dropped"][f].items()},
                "excluded": list(scores["excluded"][f])} for f in scores["fixtures"]}


def _no_judge(*args, **kwargs):
    raise score.ScoreAbort("a replay makes no judge call")


def excluded_fixtures(fixtures_root) -> dict:
    """{fixture id: the spec's excluded block} for every spec under the root that carries one."""
    found = {}
    for path in sorted(Path(fixtures_root).rglob("spec.json")):
        spec = fixtures.load_spec(path)
        if fixtures.is_excluded(spec):
            mark = spec["excluded"]
            found[str(spec.get("id") or path.parent.name)] = mark if isinstance(mark, dict) else {"invalid": True}
    return dict(sorted(found.items()))


def excluded_block_text(block) -> str:
    """Every string in an excluded block (keys and values, nested), joined by newlines.

    It gives the scan the parsed strings, because a JSON dump turns a newline into backslash-n and hides a path at the
    start of a line from the scan.
    """
    found = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(key, str):
                    found.append(key)
                walk(value)
        elif isinstance(node, list):
            for element in node:
                walk(element)
        elif isinstance(node, str):
            found.append(node)

    walk(block)
    return "\n".join(found)


def copied_text_hits(fields: dict) -> list:
    """[(field name, kind)] for each text in fields (name -> text) that holds the account name (case-insensitive) or a
    home path (rule V4, narrowed by HOME_SHAPE: a users or home folder counts only as the first segment of a path,
    and fail-closed: any character before the slash other than a path-segment character opens a path, and a folder name
    right after the double slash of any URL scheme is refused).
    The kind is "account name" or "home path"; the matched text is never returned. The limit: an HTTP method word,
    a space and a route whose first segment is a users folder is still refused (fail-closed). Refuses (SealStop)
    when the account name is unknown, as the transcript scan does.
    Each text is also scanned with the five one-letter JSON escapes (a backslash followed by n, r, t, b or f) turned
    into a newline, so a path after a pasted JSON line break is caught. A kind is reported at most once per field.
    The stated limit: other encodings of a home path or the account name are not decoded: unicode escapes,
    double-escaped text, percent-encoding, base64, HTML entities and any other encoding. For those, the backstops are
    the seal's pre-seal username scan of the transcripts and the repository's V4 rule."""
    try:
        user = fixtures._current_username().casefold()
    except fixtures.UsernameError:
        raise SealStop("the account name is unknown")
    hits = []
    for name, text in fields.items():
        copies = (text, _JSON_ESCAPE.sub("\n", text))
        if any(user in copy.casefold() for copy in copies):
            hits.append((name, "account name"))
        if any(HOME_SHAPE.search(copy) for copy in copies):
            hits.append((name, "home path"))
    return hits


def git_head(root=None) -> str:
    try:
        done = subprocess.run(["git", "-C", str(root or claude_run.ROOT), "rev-parse", "HEAD"], capture_output=True,
                              text=True, encoding="utf-8", timeout=30, check=True)
    except (OSError, subprocess.SubprocessError):
        raise SealStop("the code's git HEAD cannot be read")
    return done.stdout.strip()


def dirty_paths(root=None) -> list:
    """Tracked paths the code's repository reports as changed (untracked files ignored). SealStop if git fails."""
    try:
        done = subprocess.run(["git", "-C", str(root or claude_run.ROOT), "status", "--porcelain",
                               "--untracked-files=no"], capture_output=True, text=True, encoding="utf-8",
                              timeout=30, check=True)
    except (OSError, subprocess.SubprocessError):
        raise SealStop("the code's git status cannot be read")
    return [line[3:] for line in done.stdout.splitlines() if line.strip()]


def _ref(path, rel=None) -> dict:
    return {"path": rel, "sha256": sha256_file(path)} if rel else {"sha256": sha256_file(path)}


def command_seal(fixtures_root, source, repair_log, guide, count_rule, out, environ=None, stdout=None, repo_root=None,
                 cfg=None) -> int:
    """Seal the held-out fixtures and the pre-seal results (P:272-282). Writes nothing unless every check passes.

    Exit 2: a flag is missing or the --out is refused, or a canary is not PASS. Exit 1: a stop on the content.
    """
    stdout = stdout or sys.stdout
    environ = environ if environ is not None else os.environ

    def refuse(text, code=2):
        print(f"seal: {'REFUSED' if code == 2 else 'STOP'} {text}", file=stdout)
        return code
    for flag, value in (("--fixtures-root", fixtures_root), ("--from", source), ("--repair-log", repair_log),
                        ("--guide", guide), ("--count-rule", count_rule), ("--out", out)):
        if not value:
            return refuse(f"{flag} is required")
    reason = claude_run.out_refusal(str(out))
    if reason:
        return refuse(reason)
    out = Path(out)
    if os.path.lexists(out):
        return refuse("--out already exists")
    try:
        dirty = dirty_paths(repo_root)
    except SealStop as exc:
        return refuse(str(exc))
    if dirty:
        return refuse("the code's working tree has uncommitted changes: " + ", ".join(dirty))
    for flag, path, want_dir in (("--fixtures-root", fixtures_root, True), ("--from", source, True),
                                 ("--repair-log", repair_log, False), ("--guide", guide, False),
                                 ("--count-rule", count_rule, False)):
        if not (Path(path).is_dir() if want_dir else Path(path).is_file()):
            return refuse(f"{flag} does not exist")
    gates, reason = read_gates(environ)
    if gates is None or any(gates.get(k, {}).get("outcome") != "PASS" for k in CANARY_KEYS.values()):
        return refuse(reason or "C6 and C7 must both be PASS")
    try:
        hits = scan_transcripts(fixtures_root, environ)
        if hits:
            return refuse("the scan found a canary token or the account name in " + ", ".join(
                f"{f} ({p})" for f, p in hits) + "; outcome not determined", 1)
        if has_baseline(source):
            return refuse("the floor and oracle results hold a baseline arm (P:277)", 1)
        sessions = session_files(source)
        if not sessions or not (Path(source) / "labels.json").is_file():
            return refuse("the floor and oracle results hold no session files or no labels.json", 1)
        run = arms.read_json(Path(source) / "run.json")
        specs = score.load_specs(fixtures_root, run["fixtures"])
        scores = score.score_run(source, specs, cfg or claude_run.load_config(), environ, _no_judge, _no_judge,
                                 mode="replay")
        manifest = "\n".join(manifest_lines(fixtures_root)) + "\n"
        count_text = Path(count_rule).read_bytes().decode("utf-8")
        record = {"version": 1, "fixtures": record_of_scores(scores), "excluded_fixtures": excluded_fixtures(fixtures_root),
                  "count_rule": count_text}
        head = git_head(repo_root)
        fields = {f"excluded block of {fid}": excluded_block_text(block)
                  for fid, block in record["excluded_fixtures"].items()}
        fields["count rule"] = count_text
        fields["repair log"] = Path(repair_log).read_bytes().decode("utf-8", errors="replace")
        text_hits = copied_text_hits(fields)
    except (SealStop, score.ScoreAbort, OSError, ValueError, KeyError, UnicodeDecodeError) as exc:
        return refuse(str(exc) if isinstance(exc, (SealStop, score.ScoreAbort)) else type(exc).__name__, 1)
    if text_hits:
        return refuse("a text copied into the seal holds " + "; ".join(f"{n}: {k}" for n, k in text_hits)
                      + " (nothing written)")
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.mkdir()  # exclusive: an existing folder raises
    except FileExistsError:
        return refuse("--out already exists")
    try:
        (out / MANIFEST_NAME).write_text(manifest, encoding="utf-8")
        (out / RECORD_NAME).write_text(_dump(record), encoding="utf-8")
        (out / REPAIR_DIR).mkdir()
        shutil.copyfile(repair_log, out / REPAIR_DIR / Path(repair_log).name)
        seal = {"version": 1, "started_utc": utc_now(), "code_head": head,
                "labels": _ref(Path(source) / "labels.json", "labels.json"),
                "sessions": {rel: sha256_file(Path(source) / rel) for rel in sessions},
                "guide": _ref(guide), "count_rule": _ref(count_rule), "audit_seed": AUDIT_SEED,
                "manifest": _ref(out / MANIFEST_NAME, MANIFEST_NAME), "record": _ref(out / RECORD_NAME, RECORD_NAME),
                "repair_log": _ref(out / REPAIR_DIR / Path(repair_log).name, f"{REPAIR_DIR}/{Path(repair_log).name}")}
        (out / SEAL_NAME).write_text(_dump(seal), encoding="utf-8")
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)
        raise
    print(f"seal: wrote {MANIFEST_NAME}, {RECORD_NAME}, {SEAL_NAME} and {REPAIR_DIR}/ ({len(sessions)} session files hashed)",
          file=stdout)
    print(f"seal: commit the folder {out} (the repository owner's step; this command does not commit)", file=stdout)
    return 0


# --------------------------------------------------------------------------
# After the seal
# --------------------------------------------------------------------------

def _load_seal(seal_dir) -> dict:
    try:
        data = json.loads((Path(seal_dir) / SEAL_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SealStop("seal.json is missing or unreadable")
    if not isinstance(data, dict):
        raise SealStop("seal.json is malformed")
    return data


def _check_file(path, want, label, found: list) -> None:
    try:
        ok = sha256_file(path) == want
    except OSError:
        found.append(f"missing:{label}")
        return
    if not ok:
        found.append(f"changed:{label}")


def verify(seal_dir, fixtures_root, results_dir) -> list:
    """Re-hash every file the manifest and seal.json name. Returns the mismatches, sorted, as "missing:<path>",
    "changed:<path>" and "extra:<path>" (a held-out fixture file the manifest does not list). Paths are relative:
    to the fixtures root, to results_dir for the labels and session files, to the seal folder for its own files.
    "changed:audit_seed" names a seal.json whose audit_seed is missing or is not exactly the integer AUDIT_SEED."""
    seal_dir, found = Path(seal_dir), []
    try:
        seal = _load_seal(seal_dir)
        sessions = seal["sessions"]
        files = [seal[k] for k in ("manifest", "record", "repair_log", "labels")]
        want_rule = seal["count_rule"]["sha256"]
        if not (isinstance(sessions, dict) and all(isinstance(r.get("path"), str) and isinstance(r.get("sha256"), str)
                                                    for r in files)):
            raise SealStop("seal.json is malformed")
    except (SealStop, KeyError, TypeError, AttributeError) as exc:
        return [f"unreadable:seal.json ({exc})" if isinstance(exc, SealStop) else "unreadable:seal.json"]
    for ref in files[:3]:
        _check_file(seal_dir / ref["path"], ref["sha256"], f"seal/{ref['path']}", found)
    _check_file(Path(results_dir) / files[3]["path"], files[3]["sha256"], files[3]["path"], found)
    for rel, want in sorted(sessions.items()):
        _check_file(Path(results_dir) / rel, want, rel, found)
    listed = set()
    try:
        for line in (seal_dir / MANIFEST_NAME).read_text(encoding="utf-8").splitlines():
            want, _, rel = line.partition("  ")
            listed.add(rel)
            _check_file(Path(fixtures_root) / rel, want, rel, found)
    except OSError:
        pass  # the manifest itself was reported as missing above
    root = Path(fixtures_root)
    found += [f"extra:{p.relative_to(root).as_posix()}" for p in sorted(root.rglob("*"))
              if p.is_file() and p.relative_to(root).as_posix() not in listed] if root.is_dir() else []
    try:
        text = json.loads((seal_dir / RECORD_NAME).read_text(encoding="utf-8"))["count_rule"]
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != want_rule:
            found.append("changed:count_rule")
    except (OSError, ValueError, KeyError, AttributeError):
        found.append("unreadable:count_rule")
    seed = seal.get("audit_seed")
    if type(seed) is not int or seed != AUDIT_SEED:
        found.append("changed:audit_seed")
    return sorted(set(found))


def verify_labels(seal_dir, results_dir) -> str:
    """"" when <results_dir>/labels.json has the hash seal.json records, else "is missing" or "changed"."""
    try:
        want = _load_seal(seal_dir)["labels"]["sha256"]
        return "" if sha256_file(Path(results_dir) / "labels.json") == want else "changed"
    except SealStop:
        return "cannot be checked: seal.json is unreadable"
    except (OSError, KeyError, TypeError):
        return "is missing"


def manifest_committed(seal_dir, repo_root=None) -> str:
    """"" when the seal's manifest.txt is tracked by git at HEAD in this repository and the working file equals the
    committed blob, else the message naming the missing commit."""
    root = Path(repo_root or claude_run.ROOT)
    manifest = Path(seal_dir) / MANIFEST_NAME
    if not manifest.is_file():
        return f"{MANIFEST_NAME} is missing: write the seal and commit it first"
    rel = os.path.relpath(os.path.realpath(manifest), os.path.realpath(root)).replace(os.sep, "/")
    if rel.startswith(".."):
        return f"the seal folder is not inside this repository: commit {MANIFEST_NAME} there first"
    try:
        blob = subprocess.run(["git", "-C", str(root), "cat-file", "blob", f"HEAD:{rel}"], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return f"git cannot read this repository: {MANIFEST_NAME} is not known to be committed"
    if blob.returncode != 0:
        return f"{MANIFEST_NAME} is not committed at HEAD: commit the seal first"
    if blob.stdout != manifest.read_bytes():
        return f"{MANIFEST_NAME} differs from the committed file: commit the seal first"
    return ""


def is_heldout(split, fids) -> bool:
    return split == "heldout" or any(str(f).startswith("heldout-") for f in fids)


def needs_seal(split, fids, arm_names) -> bool:
    """True for a held-out run with any arm other than floor and oracle: the one test the guard and the carry share."""
    return is_heldout(split, fids) and any(a not in LOCKED_ARM_DIRS for a in arm_names)


def baseline_guard(split, fids, arm_names, fixtures_root, seal_dir, preseal, repo_root=None, carry=True) -> tuple:
    """(exit code, message) for a run that asks for an arm other than floor and oracle. (0, "") when nothing is wrong.

    Before the seal, held-out runs use floor and oracle only (P:89, P:251, P:270): any other arm, baseline, a skill
    variant or an unknown name, is a skill run. On held-out fixtures it needs --seal naming a committed seal (exit 2).
    With carry (the run) it also needs --preseal, and the seal must verify (exit 1) against the fixtures and the
    pre-seal results. Dev fixtures and runs with floor and oracle only are untouched.
    """
    if not needs_seal(split, fids, arm_names):
        return 0, ""
    if not seal_dir:
        return 2, "an arm other than floor and oracle on held-out fixtures needs --seal naming a committed seal (P:251, P:277)"
    reason = manifest_committed(seal_dir, repo_root)
    if reason:
        return 2, reason
    if not carry:
        return 0, ""
    if not preseal:
        return 2, "an arm other than floor and oracle on held-out fixtures needs --preseal naming the floor and oracle results (P:276)"
    found = verify(seal_dir, fixtures_root, preseal)
    if found:
        return 1, "the seal does not verify (outcome not determined): " + ", ".join(found)
    return 0, ""


def carry_preseal(seal_dir, preseal, out) -> list:
    """Copy labels.json and every floor and oracle session file from preseal into out at the same relative paths,
    read-only (0444). A copy already in place with the same hash is kept; a different one is a SealStop."""
    seal = _load_seal(seal_dir)
    rels = [seal["labels"]["path"]] + sorted(seal["sessions"])
    for rel in rels:
        src, dest = Path(preseal) / rel, Path(out) / rel
        if dest.exists():
            if sha256_file(dest) != sha256_file(src):
                raise SealStop(f"{rel} in the results differs from the pre-seal file")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        os.chmod(str(dest), stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    return rels


def is_final(run: dict) -> bool:
    """Final scoring and its report: held-out results that hold an arm other than floor and oracle."""
    return isinstance(run, dict) and needs_seal(run.get("split"), run.get("fixtures") or [], run.get("arms") or [])


def final_record_diff(seal_dir, scores: dict) -> list:
    """Labels naming where the final scorable record differs from record.json ([] when equal)."""
    try:
        sealed = json.loads((Path(seal_dir) / RECORD_NAME).read_text(encoding="utf-8"))["fixtures"]
    except (OSError, ValueError, KeyError):
        return ["record.json unreadable"]
    mine = record_of_scores(scores)
    out = [f"fixture {f} missing from the sealed record" for f in sorted(set(mine) - set(sealed))]
    out += [f"fixture {f} missing from the final record" for f in sorted(set(sealed) - set(mine))]
    out += [f"{f}: {k} differs" for f in sorted(set(mine) & set(sealed)) for k in ("scorable", "dropped", "excluded")
            if mine[f].get(k) != sealed[f].get(k)]
    return out
