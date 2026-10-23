"""Per-run isolation proof for eval runs of `claude -p`.

Pure functions over a run record (stream events plus the persisted transcript).
A run record is a dict with at least: events (list), transcript (path or None),
sandbox (path), session_id, resumed (bool), kind ("plain", "preserve", "probe", "canary"),
plugin (export dir or None). Marker text is held in memory only; results carry
counts and labels, never text.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import claude_run
from normalize import normalize

VALID_KINDS = ("plain", "preserve", "probe", "canary")
ISOLATION_MODES = ("sources", "tempconfig")
# The per-run proof has two kinds of check (README "Exclusions"). Outcome checks are about how a run ended; every
# other failure label is an isolation check, about what the run loaded or saw.
OUTCOME_CHECKS = frozenset({"result_error", "ask_user", "denials", "over_cap"})
# A change of the agent harness version or the model stops the experiment instead of leading to a re-run (README P:120).
PIN_CHANGE_CHECKS = frozenset({"version_mismatch", "model_mismatch"})
MARKER_MIN_CHARS = 25
MARKERS_PER_FILE = 3


# --------------------------------------------------------------------------
# Stream helpers
# --------------------------------------------------------------------------

def init_event(events: list):
    for ev in events:
        if isinstance(ev, dict) and ev.get("type") == "system" and ev.get("subtype") == "init":
            return ev
    return None


def result_event(events: list):
    found = None
    for ev in events:
        if isinstance(ev, dict) and ev.get("type") == "result":
            found = ev
    return found


def hook_events(events: list) -> list:
    """Every system event whose subtype starts with hook_ (started, response, progress, ...)."""
    return [e for e in events if isinstance(e, dict) and e.get("type") == "system"
            and isinstance(e.get("subtype"), str) and e["subtype"].startswith("hook_")]


def walk(node):
    """Yield every dict and list nested in node, node included."""
    stack = [node]
    while stack:
        cur = stack.pop()
        yield cur
        if isinstance(cur, dict):
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)


def walk_strings(node):
    for cur in walk(node):
        if isinstance(cur, dict):
            for val in cur.values():
                if isinstance(val, str):
                    yield val
        elif isinstance(cur, list):
            for val in cur:
                if isinstance(val, str):
                    yield val


def names_of(items) -> list:
    """Names from a list of strings. Non-string entries are not names (the proof fails on them)."""
    return [i for i in items or [] if isinstance(i, str)]


def count_tool_use(events: list, name: str) -> int:
    n = 0
    for ev in events:
        for node in walk(ev):
            if isinstance(node, dict) and node.get("type") == "tool_use" and node.get("name") == name:
                n += 1
    return n


def count_rate_limit_events(events: list) -> int:
    return sum(1 for e in events if isinstance(e, dict) and e.get("type") == "rate_limit_event")


# --------------------------------------------------------------------------
# Hook attribution by count
# --------------------------------------------------------------------------
# A hook event carries no field naming its script, so a plugin run is judged by
# counts: every hook event must be one hooks.json declares, in no greater number
# than it declares commands for the run's session source.

def _matcher_fits(matcher, event: str, resumed: bool) -> bool:
    if not matcher or matcher == "*":
        return True
    if event == "SessionStart":
        source = "resume" if resumed else "startup"
        return source in matcher.split("|")
    return True  # other events: count every group (an upper bound)


def declared_counts(export_dir, resumed: bool = False) -> dict:
    """Event -> number of hook commands that may fire, from hooks.json (events -> matcher groups -> hooks)."""
    if not export_dir:
        return {}
    try:
        data = json.loads((Path(export_dir) / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    events = data.get("hooks") if isinstance(data, dict) else None
    for event, groups in (events.items() if isinstance(events, dict) else []):
        n = 0
        for group in groups if isinstance(groups, list) else []:
            if isinstance(group, dict) and _matcher_fits(group.get("matcher"), event, resumed):
                n += sum(1 for h in group.get("hooks", []) if isinstance(h, dict) and h.get("command"))
        out[event] = n
    return out


def hook_tally(events: list, export_dir, resumed: bool) -> dict:
    """Counts of hook events against what hooks.json declares. Counts only."""
    allowed = declared_counts(export_dir, resumed)
    declared = set(declared_counts(export_dir, False)) | set(declared_counts(export_dir, True))
    started, responses, others = {}, {}, {}
    ids_started, ids_response = [], []
    for ev in hook_events(events):
        name = ev.get("hook_event")
        if ev.get("subtype") == "hook_started":
            started[name] = started.get(name, 0) + 1
            ids_started.append(ev.get("hook_id"))
        elif ev.get("subtype") == "hook_response":
            responses[name] = responses.get(name, 0) + 1
            ids_response.append(ev.get("hook_id"))
        else:  # hook_progress and any other hook_ subtype: the event must be declared; no count bound
            others[name] = others.get(name, 0) + 1
    undeclared = sum(n for d in (started, responses, others) for name, n in d.items() if name not in declared)
    excess = sum(max(0, n - allowed.get(name, 0)) for d in (started, responses) for name, n in d.items() if name in declared)
    unpaired = 0 if (sorted(map(str, ids_started)) == sorted(map(str, ids_response)) and None not in ids_started
                     and "" not in ids_started) else 1
    return {"started": started, "responses": responses, "undeclared": undeclared, "excess": excess,
            "unpaired": unpaired, "host_hooks": undeclared + excess + unpaired}


def hook_field_shapes(events: list) -> dict:
    """Event type -> sorted field names. Names only, safe to print."""
    shapes = {}
    for ev in hook_events(events):
        shapes.setdefault(ev.get("subtype"), set()).update(ev.keys())
    return {k: sorted(v) for k, v in shapes.items()}


# --------------------------------------------------------------------------
# Host markers (memory only)
# --------------------------------------------------------------------------

def _imports_of(path: Path, seen: set, out: list) -> None:
    real = path.resolve()
    if real in seen or not path.is_file():
        return
    seen.add(real)
    out.append(path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for line in text.splitlines():
        if line.startswith("@") and len(line) > 1:
            target = line[1:].split()[0]
            _imports_of((path.parent / os.path.expanduser(target)), seen, out)


def host_memory_files(environ) -> list:
    base = environ.get("CLAUDE_CONFIG_DIR") or str(Path(environ.get("HOME", str(Path.home()))) / ".claude")
    files: list = []
    _imports_of(Path(base) / "CLAUDE.md", set(), files)
    return files


def markers_from_files(files: list) -> list:
    """The 3 longest distinct non-blank lines of at least 25 characters from each file."""
    markers = []
    for path in files:
        try:
            lines = [ln.strip() for ln in path.read_text(encoding="utf-8", errors="replace").splitlines()]
        except OSError:
            continue
        distinct = sorted({ln for ln in lines if len(ln) >= MARKER_MIN_CHARS}, key=lambda s: (-len(s), s))
        markers.extend(distinct[:MARKERS_PER_FILE])
    return markers


def host_markers(environ) -> list:
    return markers_from_files(host_memory_files(environ))


def _strings_and_keys(node):
    for cur in walk(node):
        if isinstance(cur, dict):
            for key, val in cur.items():
                if isinstance(key, str):
                    yield key
                if isinstance(val, str):
                    yield val
        elif isinstance(cur, list):
            for val in cur:
                if isinstance(val, str):
                    yield val


def count_markers_found(rows: list, markers: list) -> int:
    """How many markers occur in any decoded JSON string or key of rows, after normalization."""
    wanted = {normalize(m): m for m in markers}
    remaining = set(wanted)
    for row in rows:
        for text in _strings_and_keys(row):
            text = normalize(text)
            for marker in list(remaining):
                if marker in text:
                    remaining.discard(marker)
        if not remaining:
            break
    return len(wanted) - len(remaining)


# --------------------------------------------------------------------------
# Transcript
# --------------------------------------------------------------------------

def load_transcript_checked(path) -> tuple:
    """Return (rows, corrupt line count). A missing or unreadable file gives ([], 0)."""
    rows, corrupt = [], 0
    if not path:
        return rows, corrupt
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    corrupt += 1
    except OSError:
        return [], 0
    return rows, corrupt


def load_transcript(path) -> list:
    return load_transcript_checked(path)[0]


def session_ids_in(rows: list) -> set:
    """Top-level session ids carried by transcript rows."""
    return {r[k] for r in rows if isinstance(r, dict) for k in ("sessionId", "session_id") if k in r}


def user_local_files(rows: list) -> int:
    """Files of type User or Local in any attachment that carries a files list."""
    n = 0
    for row in rows:
        for node in walk(row):
            if isinstance(node, dict) and isinstance(node.get("files"), list):
                n += sum(1 for f in node["files"] if isinstance(f, dict) and f.get("type") in ("User", "Local"))
    return n


def instruction_files(rows: list) -> list:
    files = []
    for row in rows:
        for node in walk(row):
            if isinstance(node, dict) and node.get("type") == "instructions" and isinstance(node.get("files"), list):
                files.extend(f for f in node["files"] if isinstance(f, dict))
    return files


def _inside(path, root) -> bool:
    try:
        Path(os.path.realpath(os.path.expanduser(str(path)))).relative_to(os.path.realpath(str(root)))
        return True
    except ValueError:
        return False


def has_instructions_attachment(rows: list) -> bool:
    return any(isinstance(n, dict) and n.get("type") == "instructions" and isinstance(n.get("files"), list)
               for row in rows for n in walk(row))


def auto_memory_dir(config_dir, sandbox) -> Path:
    """The per-project auto-memory dir Claude Code derives from the sandbox path."""
    return Path(config_dir) / "projects" / claude_run.project_slug(Path(os.path.realpath(str(sandbox)))) / "memory"


def memory_path_ok(path, sandbox, config_dir) -> bool:
    """Inside the sandbox, or exactly this run's auto-memory dir (a trailing slash is fine)."""
    if _inside(path, sandbox):
        return True
    if not config_dir:
        return False
    real = os.path.realpath(os.path.expanduser(str(path)))
    return real == os.path.realpath(str(auto_memory_dir(config_dir, sandbox)))


def auto_memory_symlinked(config_dir, sandbox) -> bool:
    mem = auto_memory_dir(config_dir, sandbox)
    return mem.is_symlink() or mem.parent.is_symlink()


def sandbox_has_claude_md(sandbox) -> bool:
    if not sandbox:
        return False
    base = Path(sandbox)
    return (base / "CLAUDE.md").is_file() or (base / ".claude" / "CLAUDE.md").is_file()


def memory_path_values(init: dict):
    """All memory_paths values, or None when the shape is not a flat dict or list of strings."""
    node = init.get("memory_paths")
    values = list(node.values()) if isinstance(node, dict) else list(node) if isinstance(node, list) else None
    return values


def memory_paths_valid(values) -> bool:
    return values is not None and all(isinstance(v, str) and v.startswith("/") for v in values)


# --------------------------------------------------------------------------
# Measure and check
# --------------------------------------------------------------------------

def measure(run: dict, cfg: dict, markers: list) -> dict:
    """Counts only. Safe to print."""
    events = run.get("events") or []
    init = init_event(events) or {}
    hooks = hook_events(events)
    tally = hook_tally(events, run.get("plugin"), bool(run.get("resumed")))
    rows, corrupt = load_transcript_checked(run.get("transcript"))
    files = instruction_files(rows)
    sids = session_ids_in(rows)
    allowed_sids = set(run.get("session_ids") or []) | {run.get("session_id")}
    sandbox = run.get("sandbox")
    ss = [h for h in hooks if h.get("subtype") == "hook_response" and h.get("hook_event") == "SessionStart"]
    return {
        "hook_events": len(hooks),
        "host_hooks": tally["host_hooks"] if run.get("plugin") else len(hooks),
        "hook_unpaired": tally["unpaired"],
        "sessionstart_started": tally["started"].get("SessionStart", 0),
        "sessionstart_hooks": len(ss),
        "sessionstart_ok": sum(1 for h in ss if h.get("exit_code") == 0),
        "transcript_rows": len(rows),
        "transcript_corrupt": corrupt,
        "transcript_session_mismatch": sum(1 for sid in sids if sid not in allowed_sids),
        "user_local_files": user_local_files(rows),
        "instruction_files": len(files),
        "instructions_attachments": 1 if has_instructions_attachment(rows) else 0,
        "user_files": sum(1 for f in files if f.get("type") == "User"),
        "non_project_files": sum(1 for f in files if f.get("type") != "Project"),
        "files_outside_sandbox": sum(1 for f in files if not (sandbox and _inside(f.get("path", ""), sandbox))),
        "markers_checked": len(set(markers)),
        "markers_found": count_markers_found(rows, markers),
        "ask_user": count_tool_use(events, "AskUserQuestion"),
        "rate_limit_events": count_rate_limit_events(events),
    }


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def split_failures(failures) -> tuple:
    """(isolation labels, outcome labels), each sorted, from a list of failure labels."""
    labels = sorted(set(failures))
    return [x for x in labels if x not in OUTCOME_CHECKS], [x for x in labels if x in OUTCOME_CHECKS]


def check(run: dict, cfg: dict, markers: list) -> dict:
    """Apply the per-run proof. Returns {"ok", "failures", "isolation_failures", "outcome_failures", "counts"}.

    Labels only. Every check fails closed. "failures" is the union of the two named groups: isolation checks (what
    the run loaded or saw) and outcome checks (result_error, ask_user, denials, over_cap).
    """
    return _grouped(_check(run, cfg, markers))


def _grouped(res: dict) -> dict:
    iso, outcome = split_failures(res["failures"])
    return dict(res, isolation_failures=iso, outcome_failures=outcome)


def _check(run: dict, cfg: dict, markers: list) -> dict:
    fails = []
    events = run.get("events") or []
    init = init_event(events)
    result = result_event(events)
    counts = measure(run, cfg, markers)
    if init is None:
        return {"ok": False, "failures": ["init_missing"], "counts": counts}
    sandbox = run.get("sandbox")
    if cfg.get("isolation") not in ISOLATION_MODES:
        fails.append("isolation_invalid")
    for key in ("plugins", "skills", "agents", "mcp_servers"):
        if not isinstance(init.get(key), list):
            fails.append(f"init_{key}_missing")
    values = memory_path_values(init)
    if not memory_paths_valid(values):
        fails.append("memory_paths_invalid")
    for init_key, label in (("claude_code_version", "version"), ("model", "model")):
        pin, seen = cfg.get(init_key), init.get(init_key)
        if not _text(pin) or not _text(seen):
            fails.append(f"{label}_unpinned")
        elif pin != seen:
            fails.append(f"{label}_mismatch")
    if counts["markers_checked"] == 0:
        fails.append("no_markers")
    if run.get("kind") not in VALID_KINDS:
        fails.append("kind_invalid")
    if not _text(sandbox):
        fails.append("sandbox_missing")

    # Plugins and MCP servers.
    plugins = init.get("plugins") if isinstance(init.get("plugins"), list) else []
    if any(not (isinstance(p, dict) and _text(p.get("name"))) for p in plugins):
        fails.append("plugin_entry_invalid")
    export = run.get("plugin")
    cpr_entries = [p for p in plugins if isinstance(p, dict) and p.get("name") == "cpr"]
    others = [p for p in plugins if isinstance(p, dict) and p.get("name") != "cpr"]
    if export:
        if len(cpr_entries) != 1:
            fails.append("plugin_cpr_count")
        else:
            entry = cpr_entries[0]
            if entry.get("path"):
                if os.path.realpath(entry["path"]) != os.path.realpath(str(export)):
                    fails.append("plugin_path")
            elif entry.get("source") != "cpr@inline":
                fails.append("plugin_source")
    elif cpr_entries:
        fails.append("plugin_unexpected")
    if any(p.get("path") != "builtin" for p in others):
        fails.append("plugin_not_builtin")
    if init.get("mcp_servers"):
        fails.append("mcp_servers")

    # Skills and agents: strings only; cpr: names only with a plugin export.
    allow = cfg.get("init_allowlist")
    if not isinstance(allow, dict):
        fails.append("allowlist_unset")
    for key in ("skills", "agents"):
        entries = init.get(key) if isinstance(init.get(key), list) else []
        if any(not isinstance(n, str) for n in entries):
            fails.append(f"{key}_entry_invalid")
        if isinstance(allow, dict):
            allowed = set(allow.get(key, []))
            extra = [n for n in entries if isinstance(n, str) and n not in allowed
                     and not (export and n.startswith("cpr:"))]
            if extra:
                fails.append(f"{key}_outside_allowlist")
    if export and "cpr:preserve" not in names_of(init.get("skills")):
        fails.append("skill_cpr_preserve_missing")
    if memory_paths_valid(values) and _text(sandbox):
        if any(not memory_path_ok(p, sandbox, run.get("config_dir")) for p in values):
            fails.append("memory_path_outside")
        if run.get("config_dir") and auto_memory_symlinked(run["config_dir"], sandbox):
            fails.append("memory_dir_symlink")

    # Hooks.
    if counts["host_hooks"]:
        fails.append("host_hook")
    baseline = cfg.get("isolated_baseline_hooks")
    if type(baseline) is not int or baseline != 0:
        fails.append("baseline_unproven")
    want = 0 if run.get("resumed") else 1
    if export:
        if counts["hook_unpaired"]:
            fails.append("hook_unpaired")
        # The counting instrument must be alive: a startup run shows CPR's SessionStart pair.
        if not (counts["sessionstart_started"] == counts["sessionstart_hooks"] == counts["sessionstart_ok"] == want):
            fails.append("sessionstart_count")
    elif counts["hook_events"]:
        fails.append("host_hook")

    # Transcript and instructions.
    if not counts["transcript_rows"]:
        fails.append("transcript_missing")
    elif not counts["instructions_attachments"]:
        # Required when the sandbox holds a CLAUDE.md. Otherwise its absence means user_files=0,
        # but only once C1 has proven this CLI version writes the attachment.
        if sandbox_has_claude_md(sandbox) or cfg.get("instructions_attachment_proven") is not True:
            fails.append("instructions_attachment_missing")
    if counts["transcript_corrupt"]:
        fails.append("transcript_corrupt")
    if counts["transcript_session_mismatch"]:
        fails.append("transcript_session_mismatch")
    if counts["non_project_files"]:
        fails.append("instructions_not_project")
    if counts["user_local_files"]:
        fails.append("user_or_local_files")
    if counts["files_outside_sandbox"]:
        fails.append("instructions_outside_sandbox")
    if sandbox_has_claude_md(sandbox):
        base = Path(sandbox)
        expected = {os.path.realpath(str(p)) for p in (base / "CLAUDE.md", base / ".claude" / "CLAUDE.md") if p.is_file()}
        listed = [os.path.realpath(str(f.get("path", "")))
                  for f in instruction_files(load_transcript(run.get("transcript")))]
        if set(listed) != expected or len(listed) != len(set(listed)):
            fails.append("instructions_mismatch")
    if counts["markers_found"]:
        fails.append("markers_found")

    # Result: present, not an error, subtype success. Kind decides the rest.
    if result is None or result.get("is_error") is not False or result.get("subtype") != "success":
        fails.append("result_error")
    # over_cap is set by the runner when the per-run budget stopped the run; it is an outcome, not an isolation failure.
    if run.get("over_cap") is True:
        fails.append("over_cap")
    kind = run.get("kind")
    # Kind "canary" (P:237) takes every check above, result_error and over_cap included, but not ask_user or
    # denials: in a canary run a refused tool call is the expected result.
    if kind == "preserve":
        if counts["ask_user"]:
            fails.append("ask_user")
        if result is not None and result.get("permission_denials") != []:
            fails.append("denials")
    if kind == "probe" and init.get("tools") != []:
        fails.append("probe_tools")
    return {"ok": not fails, "failures": sorted(set(fails)), "counts": counts}


# --------------------------------------------------------------------------
# Ledgers
# --------------------------------------------------------------------------

class LedgerError(Exception):
    """A ledger could not be read or changed shape. The message is the label."""


def ledger_dirs(value: str) -> list:
    return [Path(p) for p in value.split(os.pathsep) if p.strip()]


def _ledger_files(d) -> list:
    """Every regular file directly in a ledger dir (any name; gzip is out of scope)."""
    d = Path(d)
    try:
        entries = sorted(d.iterdir())
    except OSError:
        raise LedgerError("ledger_unreadable")
    return [e for e in entries if e.is_file()]


def _count_lines(f) -> int:
    try:
        with open(f, "rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        raise LedgerError("ledger_unreadable")


def ledger_snapshot(dirs: list) -> dict:
    snap = {}
    for d in dirs:
        for f in _ledger_files(d):
            snap[str(f)] = _count_lines(f)
    return snap


def ledger_rows(before: dict, dirs: list, session_id: str) -> int:
    """New ledger lines (past each file's earlier line count) that carry the session id.

    A ledger that was replaced, vanished or shrank since the snapshot raises ledger_rotated.
    """
    needle = session_id.encode("utf-8")
    n = 0
    seen = set()
    for d in dirs:
        for f in _ledger_files(d):
            seen.add(str(f))
            skip = before.get(str(f), 0)
            try:
                with open(f, "rb") as fh:
                    lines = fh.readlines()
            except OSError:
                raise LedgerError("ledger_unreadable")
            if len(lines) < skip:
                raise LedgerError("ledger_rotated")
            n += sum(1 for line in lines[skip:] if needle in line)
    if set(before) - seen:
        raise LedgerError("ledger_rotated")
    return n


# --------------------------------------------------------------------------
# Host skills and agents (names, memory only)
# --------------------------------------------------------------------------

def host_skill_agent_names(environ) -> dict:
    base = Path(environ.get("CLAUDE_CONFIG_DIR") or (Path(environ.get("HOME", str(Path.home()))) / ".claude"))
    out = {}
    for key in ("skills", "agents"):
        names = set()
        d = base / key
        if d.is_dir():
            for child in d.iterdir():
                names.add(child.stem if child.is_file() else child.name)
        out[key] = names
    return out


def shares_host_name(init_names: list, host_names: set) -> bool:
    return any(n in host_names or n.split(":")[-1] in host_names for n in init_names)
