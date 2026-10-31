"""The one wrapper around `claude -p` for this eval, plus the live control.

Every call builds its environment from an allowlist, exports the plugin from a
pinned git ref, records the stream and the persisted transcript location, and
leaves the proof to isolation.py.
"""

from __future__ import annotations

import datetime
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from pathlib import Path

import isolation

ROOT = Path(__file__).resolve().parents[4]  # the repository root (this file is experiments/<id>/code/runner/claude_run.py)
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.json"
PLUGIN_REPO_ENV = "CPR_EVAL_PLUGIN_REPO"
ENV_ALLOWLIST = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "LANG", "LC_ALL", "TERM")
# Credential variables a run's environment must never carry. Nothing forwards them (child_env builds from
# ENV_ALLOWLIST alone), and a mode that would need them is refused (TEMPCONFIG_REFUSAL).
AUTH_VARS = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")
TEMPCONFIG_REFUSAL = ("isolation mode tempconfig carries a credential into the run: it needs the repository owner's "
                      "ruling with a security review first")
PROMPT_OK = "Reply with the single word OK."
RUN_TIMEOUT = 900
MAX_OUTPUT_ENV = "CLAUDE_CODE_MAX_OUTPUT_TOKENS"
POLL_SECONDS = 20


# --------------------------------------------------------------------------
# Config, paths, spend
# --------------------------------------------------------------------------

def project_slug(path) -> str:
    """The folder name Claude Code derives from a project path: every character outside [A-Za-z0-9] becomes a hyphen."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def load_config(path=None) -> dict:
    return json.loads(Path(path or CONFIG_PATH).read_text(encoding="utf-8"))


def save_config(cfg: dict, path=None) -> None:
    atomic_write(Path(path or CONFIG_PATH), json.dumps(cfg, indent=2) + "\n")


def atomic_write(path: Path, text: str) -> None:
    """Write text to path through a temp file in the same directory, then os.replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class ConfigError(Exception):
    """A pinned config value is out of range: refuse to run."""


def validate_config(cfg: dict) -> None:
    if cfg.get("isolation") == "tempconfig":
        raise ConfigError(TEMPCONFIG_REFUSAL)
    if cfg.get("isolation") != "sources":
        raise ConfigError("isolation mode invalid")
    budget = cfg.get("max_budget_usd_per_run")
    if isinstance(budget, bool) or not isinstance(budget, (int, float)) or not math.isfinite(budget) \
            or not 0 < budget <= 5:
        raise ConfigError("max_budget_usd_per_run out of range")
    for key in ("probe_max_output_tokens", "probe_timeout_s"):
        val = cfg.get(key)
        if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
            raise ConfigError(f"{key} must be a positive integer")


def eval_root(environ=None) -> Path:
    environ = environ if environ is not None else os.environ
    return Path(environ.get("TMPDIR") or tempfile.gettempdir()) / "cpr-eval"


class SpendError(Exception):
    """spend.json exists but cannot be read or parsed: refuse to run."""


class SpendCapReached(Exception):
    """The cumulative spend is at or over the cap: refuse to launch."""


class LiveRefused(Exception):
    """CPR_EVAL_LIVE is not set to 1: refuse to launch."""


class SpawnError(OSError):
    """The claude process could not be started: no child ran."""


def _finite_nonneg(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def reported_cost(record):
    """The cost a record's run reported: cost_usd_reported, else (older records) cost_usd. None when not a finite number >= 0."""
    if not isinstance(record, dict):
        return None
    value = record["cost_usd_reported"] if "cost_usd_reported" in record else record.get("cost_usd")
    return float(value) if _finite_nonneg(value) else None


def invocation_cost(reported, prior=None, *, resumed=False, forked=False):
    """The cost of one invocation, from the total_cost_usd Claude Code reported for it.

    On a --resume turn the reported total is cumulative for the whole session, so the invocation's own cost is
    the reported value minus the prior invocation's reported value. Fail closed: with no prior, a prior that
    is not a finite number >= 0, or a negative difference, the full reported value is charged. A forked run
    starts a new session id, and the first fork turn is charged in full (the stored fork records report a
    first-turn-sized total, but no parent record exists to prove they restart from zero). A reported value that
    is not a finite number >= 0 is returned unchanged, for the caller to count as unknown.
    """
    if not _finite_nonneg(reported) or not resumed or forked:
        return reported
    base = reported_cost(prior)
    if base is None or reported < base:
        return reported
    return round(reported - base, 10)


PHASES = ("control", "judge", "generation", "evaluation")
SHARED_LEDGER = {"judge": "control"}  # the judge check spends from the control budget


def ledger_of(phase) -> str:
    """The ledger a phase spends from. An unknown phase refuses."""
    if phase not in PHASES:
        raise ConfigError("unknown phase")
    return SHARED_LEDGER.get(phase, phase)


def phase_cap(cfg: dict, phase) -> float:
    """The cap for a phase's ledger, from config phase_caps. A missing phase or cap refuses."""
    caps = cfg.get("phase_caps")
    cap = caps.get(ledger_of(phase)) if isinstance(caps, dict) else None
    if isinstance(cap, bool) or not isinstance(cap, (int, float)) or not math.isfinite(cap) or cap <= 0:
        raise ConfigError("phase cap missing")
    return float(cap)


def spend_ceiling(cfg: dict) -> float:
    """The ceiling on the sum of all ledgers, from config spend_ceiling_usd. A missing or bad value refuses."""
    value = cfg.get("spend_ceiling_usd")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ConfigError("spend ceiling missing")
    return float(value)


def _read_spend(environ) -> dict:
    path = eval_root(environ) / "spend.json"
    if not path.exists():
        return {"total": 0.0, "phases": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        total, phases = data["total"], data.get("phases", {})
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise SpendError("spend file unreadable")
    if not _finite_nonneg(total) or not isinstance(phases, dict) or not all(
            isinstance(k, str) and _finite_nonneg(v) for k, v in phases.items()):
        raise SpendError("spend file unreadable")
    return {"total": float(total), "phases": {k: float(v) for k, v in phases.items()}}


def spend_total(environ=None, phase: str = "control") -> float:
    """A missing file is zero. A file that exists but is unreadable or holds a bad number refuses.

    The control ledger is the file's original "total"; other phases live under "phases".
    """
    data = _read_spend(environ)
    ledger = ledger_of(phase)
    return data["total"] if ledger == "control" else data["phases"].get(ledger, 0.0)


def spend_sum(environ=None) -> float:
    """The sum of every ledger (the control total and each phase ledger)."""
    data = _read_spend(environ)
    return data["total"] + sum(data["phases"].values())


def add_spend(cost, environ=None, unknown_cost: float = 0.0, phase: str = "control") -> float:
    """Add a run's cost to a phase's ledger. A cost that is missing, negative or non-finite counts as unknown_cost."""
    data = _read_spend(environ)
    ledger = ledger_of(phase)
    amount = float(cost) if _finite_nonneg(cost) else float(unknown_cost)
    if ledger == "control":
        data["total"] = round(data["total"] + amount, 6)
        new = data["total"]
    else:
        new = data["phases"][ledger] = round(data["phases"].get(ledger, 0.0) + amount, 6)
    atomic_write(eval_root(environ) / "spend.json", json.dumps(data))
    return new


def spend_rebuild(environ=None, write=False, out=None, cfg=None) -> int:
    """Recompute spend.json from runs/*/record.json with the per-invocation rule; a dry run unless write.

    Records are grouped by session id and taken in file-modification order (a record carries no turn number).
    A record with no phase key predates phases and counts toward control (then the only ledger). A timed-out record, or one whose reported cost is unusable, counts as the per-run cap, as at launch.
    An unreadable or malformed record, or an unreadable current spend.json, refuses (exit 1) and writes nothing.
    A record with "forked" true (a fork of a resumed session) is charged its full reported value, as at launch.
    """
    out = out or sys.stdout
    try:
        cap = float((cfg or load_config())["max_budget_usd_per_run"])
        old = {"control": spend_total(environ, "control"), "generation": spend_total(environ, "generation"),
               "evaluation": spend_total(environ, "evaluation")}
        entries = []
        for path in sorted((eval_root(environ) / "runs").glob("*/record.json")):
            rec = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(rec, dict) or not isinstance(rec.get("session_id"), str) \
                    or not isinstance(rec.get("resumed"), bool):
                raise ValueError("malformed record")
            ledger_of(rec.get("phase", "control"))  # records from before phases existed spent from the one total
            entries.append((path.stat().st_mtime_ns, path.parent.name, rec))
    except (OSError, ValueError, KeyError, TypeError, ConfigError, SpendError) as exc:
        print(f"spend-rebuild: REFUSED {type(exc).__name__}: {exc}", file=out)
        return 1
    new = {"control": 0.0, "generation": 0.0, "evaluation": 0.0}
    last = {}
    for _, _, rec in sorted(entries, key=lambda e: e[:2]):
        sid = rec["session_id"]
        reported = reported_cost(rec)
        if rec.get("timed_out") or reported is None:
            amount = cap
        else:
            amount = invocation_cost(reported, last.get(sid), resumed=rec["resumed"],
                                     forked=rec.get("forked") is True)
        last[sid] = rec
        ledger = ledger_of(rec.get("phase", "control"))
        new[ledger] = round(new[ledger] + amount, 6)
    print(f"spend-rebuild: {len(entries)} records", file=out)
    for name in new:
        print(f"  {name:<9} old {old[name]:>10.6f}  new {new[name]:>10.6f}", file=out)
    if not write:
        print("spend-rebuild: dry run, nothing written (--write to apply)", file=out)
        return 0
    atomic_write(eval_root(environ) / "spend.json",
                 json.dumps({"total": new["control"], "phases": {"generation": new["generation"], "evaluation": new["evaluation"]}}))
    print("spend-rebuild: wrote spend.json", file=out)
    return 0


# --------------------------------------------------------------------------
# Plugin export
# --------------------------------------------------------------------------

class PluginRepoError(Exception):
    """CPR_EVAL_PLUGIN_REPO is unset, not an existing directory outside the clone, or does not hold the ref."""


def _stat_or_none(path):
    """os.stat of path, or None when it (or a parent) does not exist. Any other error propagates."""
    try:
        return os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None


def path_folders(path) -> list:
    """The folders from the resolved path up to the file-system root. A part that does not exist yet is skipped."""
    folder = os.path.realpath(str(path))
    found = []
    while True:
        found.append(folder)
        parent = os.path.dirname(folder)
        if parent == folder:
            return found
        folder = parent


def walk_finds(path, target_stat, on_error=True) -> bool:
    """Whether any folder from the resolved path up to the root is the folder target_stat describes (identity, not spelling).

    A case-insensitive file system names one folder many ways, and only the file system knows they are the same.
    A path that does not exist yet is judged by its nearest existing ancestors. A stat error other than "missing"
    returns on_error (True for the inside-the-clone question, so that an unreadable path is refused).
    """
    for folder in path_folders(path):
        try:
            seen = _stat_or_none(folder)
        except OSError:
            return on_error
        if seen is not None and os.path.samestat(target_stat, seen):
            return True
    return False


def inside_clone(path, root=None) -> bool:
    """Whether path lies inside the repository clone: a samestat walk from the resolved path up to the file-system root,
    against the stat of the clone's root (derived from this file's own location unless root is given)."""
    return walk_finds(path, os.stat(str(root if root is not None else ROOT)))


RECORDED_DIR = ("experiments", "EXP-0001", "recorded")


def path_refusal(value, flag, root=None) -> str:
    """"" when value is an acceptable folder to write into, else the refusal message naming flag.

    There is no default inside the repository, so a missing value refuses. A value inside the clone is accepted only
    when the same samestat walk finds experiments/EXP-0001/recorded/ among its folders.
    """
    if not value:
        return f"{flag} is required (there is no default inside the repository)"
    root = Path(ROOT if root is None else root)
    if not inside_clone(value, root):
        return ""
    recorded = _stat_or_none(root.joinpath(*RECORDED_DIR))
    if recorded is not None and walk_finds(value, recorded, on_error=False):
        return ""
    return f"{flag} is inside the repository clone and not under experiments/EXP-0001/recorded/"


def out_refusal(value, root=None) -> str:
    """"" when value is an acceptable --out, else the refusal message."""
    return path_refusal(value, "--out", root)


def from_refusal(value, root=None) -> str:
    """"" when value is an acceptable --from (score, report and pilot-md write into it), else the refusal message."""
    return path_refusal(value, "--from", root)


def plugin_repo(ref: str, environ=None, root=None) -> Path:
    """The git repository named by CPR_EVAL_PLUGIN_REPO. It must exist, lie outside the clone and hold ref."""
    environ = environ if environ is not None else os.environ
    value = environ.get(PLUGIN_REPO_ENV)
    if not value:
        raise PluginRepoError(f"{PLUGIN_REPO_ENV} is not set")
    path = Path(value)
    if not path.is_dir():
        raise PluginRepoError(f"{PLUGIN_REPO_ENV} is not an existing directory")
    if inside_clone(path, root):
        raise PluginRepoError(f"{PLUGIN_REPO_ENV} is inside the repository clone")
    found = subprocess.run(["git", "-C", str(path), "cat-file", "-e", f"{ref}^{{commit}}"], capture_output=True)
    if found.returncode != 0:
        raise PluginRepoError(f"{PLUGIN_REPO_ENV} does not hold the plugin ref")
    return path


def export_plugin(ref: str, environ=None, overlay_skill=None) -> Path:
    """git archive <ref> (from the repository named by CPR_EVAL_PLUGIN_REPO) into plugins/<ref>/; an optional SKILL.md overlay goes into a copy."""
    base = eval_root(environ) / "plugins"
    dest = base / ref
    if not (dest / "hooks" / "hooks.json").is_file():
        repo = plugin_repo(ref, environ)
        shutil.rmtree(dest, ignore_errors=True)
        data = subprocess.run(["git", "-C", str(repo), "archive", ref], check=True, capture_output=True).stdout
        dest.mkdir(parents=True)
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            for member in tar.getmembers():
                target = (dest / member.name).resolve()
                if member.issym() or member.islnk() or dest.resolve() not in (target, *target.parents):
                    continue
                tar.extract(member, dest)
    if overlay_skill is None:
        return dest
    copy = base / f"{ref}-overlay-{hashlib.sha256(Path(overlay_skill).read_bytes()).hexdigest()[:8]}"
    shutil.rmtree(copy, ignore_errors=True)
    shutil.copytree(dest, copy)
    shutil.copyfile(overlay_skill, copy / "skills" / "preserve" / "SKILL.md")
    return copy


# --------------------------------------------------------------------------
# Command and environment
# --------------------------------------------------------------------------

def child_env(cfg: dict, run_id: str, environ=None, probe: bool = False) -> dict:
    environ = environ if environ is not None else os.environ
    env = {k: environ[k] for k in ENV_ALLOWLIST if k in environ}
    env["CPR_CONFIG"] = str(eval_root(environ) / "sandboxes" / "cpr.json")
    if probe:
        # A probe answer is short: bound its output so a runaway cannot run long or large.
        env[MAX_OUTPUT_ENV] = str(cfg["probe_max_output_tokens"])
    if cfg.get("isolation") == "tempconfig":
        env["CLAUDE_CONFIG_DIR"] = str(eval_root(environ) / "config" / run_id)
    return env


def child_config_dir(env: dict) -> Path:
    return Path(env.get("CLAUDE_CONFIG_DIR") or (Path(env.get("HOME", str(Path.home()))) / ".claude"))


def bash_tools(patterns) -> list:
    """allowed_bash patterns as Bash(<pattern>) tool entries."""
    return [f"Bash({p})" for p in patterns]


def build_argv(cfg: dict, prompt: str, *, session_id=None, resume=None, plugin=None,
               plain=False, probe=False, model=None, allowed_tools=None, fork_session=False) -> list:
    argv = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--include-hook-events"]
    if plain:
        # Plain stays plain (no setting sources, no plugin); only the model is pinned explicitly.
        if model:
            argv += ["--model", model]
    else:
        # Account-level connectors load whatever the setting sources are; with no --mcp-config
        # this excludes all of them.
        argv += ["--strict-mcp-config", "--setting-sources", "project"]
        if plugin:
            argv += ["--plugin-dir", str(plugin)]
        model = model or cfg.get("model")
        if model:
            argv += ["--model", model]
    if allowed_tools:
        argv += ["--allowedTools", ",".join(allowed_tools)]
    argv += ["--permission-mode", "acceptEdits", "--permission-prompts", "none",
             "--max-budget-usd", str(cfg["max_budget_usd_per_run"])]
    argv += ["--resume", resume] if resume else ["--session-id", session_id or str(uuid.uuid4())]
    if fork_session and resume:
        argv += ["--fork-session"]
    if probe:
        argv += ["--tools", ""]
    return argv


class MemoryDirError(Exception):
    """The run's auto-memory dir already holds files at launch."""


def memory_state(config_dir, sandbox) -> str:
    mem = isolation.auto_memory_dir(config_dir, sandbox)
    if not mem.exists():
        return "absent"
    return "nonempty" if any(mem.iterdir()) else "empty"


def memory_listing(config_dir, sandbox) -> list:
    """Sorted [relative path, size] of every file in the run's auto-memory dir ([] when absent)."""
    mem = isolation.auto_memory_dir(config_dir, sandbox)
    if not mem.is_dir():
        return []
    return sorted([p.relative_to(mem).as_posix(), p.stat().st_size] for p in mem.rglob("*") if p.is_file())


def record_project_dir(config_dir, sandbox, environ=None) -> str:
    """Append this run's project dir (slug and path relative to the config dir) to the manifest. Never deletes."""
    slug = project_slug(Path(os.path.realpath(sandbox)))
    root = eval_root(environ)
    root.mkdir(parents=True, exist_ok=True)
    with open(root / "project-dirs.txt", "a", encoding="utf-8") as fh:
        fh.write(f"{slug}\tprojects/{slug}\n")
    return slug


def make_sandbox(label: str, environ=None) -> Path:
    path = eval_root(environ) / "sandboxes" / f"{label}-{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True)
    return path


def parse_stream(text: str) -> list:
    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


def run_claude(cfg: dict, prompt: str, sandbox: Path, *, label: str, phase: str, kind: str = "plain",
               session_id=None, resume=None, plugin=None, plain=False, probe=False,
               model=None, environ=None, prior=None, allowed_tools=None, fork_session=False,
               extra_session_ids=(), on_launch=None) -> dict:
    """One `claude -p` call. Returns the run record; proof is isolation.check().

    Every launch is gated here: CPR_EVAL_LIVE=1, a valid config, and the phase's cumulative spend under
    its cap (config phase_caps; an unknown phase or a missing cap refuses). A resumed run needs the
    previous record of the session as prior. A forked run records the new session id the stream reports.

    Cost: Claude Code's total_cost_usd is cumulative per session on a --resume turn. The raw value is kept as
    record["cost_usd_reported"]; record["cost_usd"] (and the spend charge) is this invocation's own cost:
    reported minus the prior record's reported value. A missing or bad prior, or a negative difference, charges
    the full reported value. A forked run (resume plus fork_session) is charged the full reported value. A
    timeout still charges the whole per-run cap.
    """
    live_env = environ if environ is not None else os.environ
    if live_env.get("CPR_EVAL_LIVE") != "1":
        raise LiveRefused("live run refused: set CPR_EVAL_LIVE=1")
    validate_config(cfg)
    if spend_total(environ, phase) >= phase_cap(cfg, phase):
        raise SpendCapReached("spend cap reached")
    if spend_sum(environ) >= spend_ceiling(cfg):
        raise SpendCapReached("spend ceiling reached")
    run_id = f"{label}-{uuid.uuid4().hex[:8]}"
    env = child_env(cfg, run_id, environ, probe=probe)
    timeout = cfg["probe_timeout_s"] if probe else RUN_TIMEOUT
    sid = resume or session_id or str(uuid.uuid4())
    argv = build_argv(cfg, prompt, session_id=None if resume else sid, resume=resume,
                      plugin=plugin, plain=plain, probe=probe, model=model,
                      allowed_tools=allowed_tools, fork_session=fork_session)
    config_dir = child_config_dir(env)
    mem_state = memory_state(config_dir, sandbox)
    if resume:
        if not prior or prior.get("session_id") != resume or prior.get("sandbox") != str(sandbox) \
                or prior.get("memory_dir_after") != memory_listing(config_dir, sandbox):
            raise MemoryDirError("auto-memory dir differs from turn 1 at resume")
    elif mem_state != "absent" and mem_state != "empty":
        raise MemoryDirError("auto-memory dir not empty at start")
    started = time.time()
    started_utc = datetime.datetime.fromtimestamp(started, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    launched_model = model if plain else (model or cfg.get("model"))
    timed_out = False
    # Known limit: in CPython's own Popen.__init__ (3.9 subprocess.py lines 1764-1777) the child exists before the
    # parent reads the exec-status pipe, so an exception in that window leaves a live child that is reported here
    # as SpawnError, "not launched". Shown only by fault injection; accepted.
    try:  # "launched" means Popen returned: a child process exists. Any failure inside this call is "not launched".
        child = subprocess.Popen(argv, cwd=str(sandbox), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    except Exception as exc:  # no child exists (a missing claude, a bad argument or environment): not a launch
        raise SpawnError("the claude command could not be started") from exc
    with child:  # closes the pipes and waits, as subprocess.run does
        try:
            if on_launch is not None:
                on_launch()  # the spawn point: a caller learns here that a child exists
            stdout, stderr = child.communicate(timeout=timeout)
            returncode, stderr_chars = child.returncode, len(stderr)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()  # as subprocess.run does: reading the pipes again would block on a grandchild holding them
            # True cost unknown: count the whole per-run cap as spent.
            timed_out, stdout, returncode, stderr_chars = True, "", None, 0
        except BaseException:  # after the launch: never a SpawnError
            child.kill()
            raise
    events = parse_stream(stdout)
    result = isolation.result_event(events) or {}
    if fork_session:
        reported = (isolation.init_event(events) or {}).get("session_id")
        if isinstance(reported, str) and reported:
            sid = reported
    reported_usd = result.get("total_cost_usd")
    transcript = config_dir / "projects" / project_slug(Path(os.path.realpath(sandbox))) / f"{sid}.jsonl"
    record = {
        "run_id": run_id, "label": label, "kind": kind, "isolation": cfg.get("isolation"),
        "session_id": sid, "session_ids": sorted({sid, *extra_session_ids}), "phase": phase,
        "resumed": bool(resume), "forked": bool(fork_session and resume), "plugin": str(plugin) if plugin else None,
        "sandbox": str(sandbox), "config_dir": str(config_dir), "memory_dir_at_start": mem_state, "transcript": str(transcript) if transcript.is_file() else None,
        "started_utc": started_utc,
        "pins": {"claude_code_version": cfg.get("claude_code_version"), "model": launched_model,
                 "isolation": cfg.get("isolation"), "plugin_ref": cfg.get("plugin_ref") if plugin else None},
        "env_names": sorted(env), "returncode": returncode, "stderr_chars": stderr_chars, "timed_out": timed_out,
        "events": events, "cost_usd_reported": reported_usd,
        "cost_usd": invocation_cost(reported_usd, prior, resumed=bool(resume), forked=bool(fork_session and resume)),
        "duration_ms": result.get("duration_ms"),
        "usage": result.get("usage"), "rate_limit_events": isolation.count_rate_limit_events(events),
        "over_cap": "budget" in str(result.get("subtype", "")),
        "wall_s": round(time.time() - started, 1),
        "memory_dir_after": memory_listing(config_dir, sandbox),
    }
    record_project_dir(config_dir, sandbox, environ)
    out = eval_root(environ) / "runs" / run_id
    out.mkdir(parents=True, exist_ok=True)
    atomic_write(out / "record.json", json.dumps(record))
    cap = float(cfg["max_budget_usd_per_run"])
    add_spend(cap if timed_out else record["cost_usd"], environ, unknown_cost=cap, phase=phase)
    return record


# --------------------------------------------------------------------------
# Preflight
# --------------------------------------------------------------------------

def preflight(cfg: dict, environ=None) -> str:
    """Return "" when every check passes, else the failing reason."""
    environ = environ if environ is not None else os.environ
    try:
        validate_config(cfg)
    except ConfigError as exc:
        return str(exc)
    tmp = environ.get("TMPDIR") or tempfile.gettempdir()
    root = eval_root(environ)
    root.mkdir(parents=True, exist_ok=True)
    real_root = Path(os.path.realpath(root))
    if Path(os.path.realpath(tmp)) not in real_root.parents:
        return "sandbox root is not under TMPDIR"
    for anc in real_root.parents:
        for name in ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", ".claude"):
            if (anc / name).exists():
                return f"ancestor {anc.name or '/'} holds {name}"
    try:
        version = subprocess.run(["claude", "--version"], capture_output=True, text=True, encoding="utf-8",
                                 env={k: environ[k] for k in ENV_ALLOWLIST if k in environ},
                                 stdin=subprocess.DEVNULL, timeout=60).stdout.split()
    except (OSError, subprocess.TimeoutExpired):
        return "claude --version failed"
    if not version or version[0] != cfg.get("claude_code_version"):
        return "claude version does not match the pin"
    return ""


# --------------------------------------------------------------------------
# Control
# --------------------------------------------------------------------------

def _wait_ledger(before, dirs, sid, want_min, seconds):
    deadline = time.time() + seconds
    while True:
        n = isolation.ledger_rows(before, dirs, sid)
        if n >= want_min or time.time() >= deadline:
            return n
        time.sleep(2)


def _init(rec):
    return isolation.init_event(rec["events"]) or {}


def _seed_c5(sandbox: Path) -> None:
    (sandbox / "CLAUDE.md").write_text("# Widget service\n\n## Status\n- Scaffolding only.\n", encoding="utf-8")
    git = ["git", "-c", "user.name=eval", "-c", "user.email=eval@example.com"]
    subprocess.run(["git", "init", "-q"], cwd=str(sandbox), check=True, capture_output=True)
    subprocess.run(git + ["add", "CLAUDE.md"], cwd=str(sandbox), check=True, capture_output=True)
    subprocess.run(git + ["commit", "-q", "-m", "seed"], cwd=str(sandbox), check=True, capture_output=True)


C5_TURN1 = ("Context for later: we decided the dev server runs on port 8081 because 8080 collides with the "
            "metrics sidecar. Also, the old /v1/upload endpoint is deprecated as of today; use /v2/upload. "
            "Just acknowledge in one line.")


def control(environ=None, out=None, runner=None, config_path=None) -> int:
    """Run the five controls. Any unexpected exception becomes a one-line label and exit 1.

    The result is recorded in gates.json as the C1-5 entry (P:239), through seal.record_control, which has no token:
    PASS when all five passed; FAIL when at least one child process was spawned (the runner's on_launch call, at the
    spawn point) and the command did not end in PASS (an exception after the first spawn included); nothing when it
    stopped before the first spawn (no live flag, no ledger, CLAUDE_CONFIG_DIR set, preflight FAIL, an unreadable spend
    file, a plugin-export error, a claude command that cannot be started: setup errors, not a failed control). A control
    is never re-run: with a C1-5 entry already in gates.json the command refuses (exit 2, no launch), after the
    live-flag check and the lock. The lock is control.lock in the eval root, created exclusively for the run and
    removed after the result is recorded (or after it is decided that there is nothing to record), on every path,
    an interrupt included; one that exists refuses (exit 2), whatever its content, and is never broken here. The one
    exception: when a child process was started and the result could not be recorded (the record raised, an interrupt
    included), the lock is kept, the STOP line says so, and the command exits 2 (an interrupt is re-raised instead).
    """
    import seal  # inside the function: seal imports claude_run

    out = out or sys.stdout
    environ = environ if environ is not None else os.environ
    runner = runner or run_claude
    launched = []  # one start time per launch; the first is the entry's started_utc

    def counted(*args, on_launch=None, **kwargs):
        def spawned():  # run_claude calls this at the spawn point: a child exists from here on
            if not launched:
                launched.append(seal.utc_now())
            if on_launch is not None:
                on_launch()
        return runner(*args, on_launch=spawned, **kwargs)

    counted.held = []  # _control puts the lock it created here; it is removed below, after the record
    code = 1
    keep = False  # True from the first record attempt until it returns normally: then the lock stays
    try:
        try:
            code = _control(environ, out, counted, config_path)
        except (SpendError, SpendCapReached, LiveRefused, ConfigError, MemoryDirError, PluginRepoError,
                isolation.LedgerError) as exc:
            print(f"control: STOP {exc}", file=out)
        except Exception as exc:  # label only: exception text can carry argv and paths
            print(f"control: ERROR {type(exc).__name__}", file=out)
        except BaseException:  # a Ctrl-C after a launch is a failed control too
            if launched:
                keep = True
                keep = not _record_control("FAIL", launched[0], environ, out)
            raise
        if launched:
            keep = True
            keep = not _record_control("PASS" if code == 0 else "FAIL", launched[0], environ, out)
            if keep:
                return 2  # the result was not written: a failed run, whatever the command's own code was
        return code
    finally:
        if not keep:
            for held in counted.held:
                held.unlink(missing_ok=True)  # only a lock this run created is in the list


def _record_control(outcome, started, environ, out) -> bool:
    """True when the C1-5 entry was written. Otherwise print the STOP line (the lock is kept) and return False; a
    BaseException from the record is re-raised after the line."""
    import seal  # inside the function: seal imports claude_run

    entry = {"outcome": outcome, "started_utc": started, "run_id": f"control-{uuid.uuid4().hex[:8]}"}
    try:
        seal.record_control(entry, environ)
        return True
    except BaseException as exc:  # label only: exception text can carry paths
        lock = eval_root(environ) / "control.lock"
        print(f"control: STOP could not record C1-5 in gates.json; the lock {lock} is kept: a child process was "
              "started and a control is never re-run (P:239), so control refuses until a person checks gates.json",
              file=out)
        if not isinstance(exc, Exception):
            raise
        return False


def _control(environ, out, runner, config_path) -> int:
    if environ.get("CPR_EVAL_LIVE") != "1":
        print("live run refused: set CPR_EVAL_LIVE=1", file=out)
        return 2
    import seal  # inside the function: seal imports claude_run

    lock = eval_root(environ) / "control.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        print(f"control: REFUSED {lock} exists: another control may be running; if none is, check gates.json for a "
              "C1-5 entry first, and a control that already launched is never re-run (P:239); what to record is the "
              "repository owner's call", file=out)
        return 2
    held = getattr(runner, "held", None)  # control's list: it removes the lock after recording C1-5
    if held is not None:
        held.append(lock)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"pid": os.getpid(), "started_utc": seal.utc_now()}) + "\n")
        return _control_locked(environ, out, runner, config_path)
    finally:
        if held is None:  # called without control: no one else removes it
            lock.unlink(missing_ok=True)  # only a lock this run created gets here


def _control_locked(environ, out, runner, config_path) -> int:
    import seal  # inside the function: seal imports claude_run

    gates, reason = seal.read_gates(environ, missing_ok=True)
    if gates is None or seal.CONTROL_KEY in gates:  # an unreadable gate file cannot be recorded into: fail closed
        print("control: REFUSED " + (reason + "; it is not overwritten" if gates is None else
                                     "C1-5 already has an entry; a control is never re-run"), file=out)
        return 2
    ledger_value = environ.get("CPR_EVAL_HOST_LEDGERS")
    if not ledger_value:
        print("ledger not configured", file=out)
        return 3
    dirs = isolation.ledger_dirs(ledger_value)
    cfg = load_config(config_path)
    if cfg.get("isolation") == "sources" and environ.get("CLAUDE_CONFIG_DIR"):
        print("unset CLAUDE_CONFIG_DIR", file=out)
        return 3
    reason = preflight(cfg, environ)
    if reason:
        print(f"preflight: FAIL {reason}", file=out)
        return 1
    cap = phase_cap(cfg, "control")
    ceiling = spend_ceiling(cfg)
    spend_total(environ)  # raises SpendError on an unreadable file, before any run
    markers = isolation.host_markers(environ)
    host_names = isolation.host_skill_agent_names(environ)
    export = export_plugin(cfg["plugin_ref"], environ)
    lines = []

    def stop(why: str, rec=None) -> int:
        for ln in lines:
            print(ln, file=out)
        print(f"control: STOP {why}", file=out)
        if rec is not None:
            print("hook event fields: " + json.dumps(isolation.hook_field_shapes(rec["events"])), file=out)
        return 1

    def guard():
        """None when the control cap and the whole-run ceiling both have room, else the reason to stop."""
        if spend_total(environ) >= cap:
            return "spend cap reached"
        if spend_sum(environ) >= ceiling:
            return "spend ceiling reached"
        return None

    # C1 plain: the instrument must fire.
    why = guard()
    if why:
        return stop(why)
    sid1 = str(uuid.uuid4())
    before = isolation.ledger_snapshot(dirs)
    c1 = runner(cfg, PROMPT_OK, make_sandbox("c1", environ), label="c1", phase="control", session_id=sid1, plain=True,
                model="sonnet", environ=environ)
    c1_rows = _wait_ledger(before, dirs, c1["session_id"], 1, POLL_SECONDS)
    m1 = isolation.measure(c1, {}, markers)
    model = _init(c1).get("model") or ""
    mcp = _init(c1).get("mcp_servers")
    mcp_n = len(mcp) if isinstance(mcp, list) else 0
    lines.append(f"C1 plain (instrument)      user_files={m1['user_files']} markers_found={m1['markers_found']} "
                 f"host_hooks={m1['host_hooks']} ledger_rows={c1_rows} mcp={mcp_n}")
    if not (m1["user_files"] >= 1 and m1["markers_found"] >= 1 and m1["host_hooks"] >= 1 and c1_rows >= 1):
        return stop("C1 did not fire", c1)
    if mcp_n < 1:
        return stop("mcp instrument cannot fire", c1)
    if "sonnet" not in model.lower():
        return stop("C1 model is not a Sonnet ID")
    lines[-1] += "  -> FIRES (expected)"
    cfg["model"] = model
    # C1 proves this CLI version writes the instructions attachment (it carried User files).
    cfg["instructions_attachment_proven"] = m1["instructions_attachments"] >= 1
    version = _init(c1).get("claude_code_version")
    if version != cfg["claude_code_version"]:
        return stop("claude code version differs from the pin")

    # C3 isolated, no plugin.
    why = guard()
    if why:
        return stop(why)
    c3 = runner(cfg, PROMPT_OK, make_sandbox("c3", environ), label="c3", phase="control", session_id=str(uuid.uuid4()), environ=environ)
    init3 = _init(c3)
    allow = {"skills": sorted(isolation.names_of(init3.get("skills"))),
             "agents": sorted(isolation.names_of(init3.get("agents")))}
    # The isolated, plugin-less baseline: C3's own hook count becomes the recorded fact.
    baseline = len(isolation.hook_events(c3["events"]))
    cfg3 = dict(cfg, init_allowlist=allow, isolated_baseline_hooks=baseline)
    chk3 = isolation.check(c3, cfg3, markers)
    shared = (isolation.shares_host_name(allow["skills"], host_names["skills"]) or
              isolation.shares_host_name(allow["agents"], host_names["agents"]))
    have = [bool(host_names["skills"]), bool(host_names["agents"])]
    overlap_state = "checked" if all(have) else ("not-applicable" if not any(have) else "partial")
    plugin_absent = all(p.get("name") != "cpr" for p in init3.get("plugins") or [] if isinstance(p, dict))
    c3_ok = chk3["ok"] and chk3["counts"]["hook_events"] == 0 and plugin_absent and not shared
    lines.append(f"C3 isolated, no plugin     plugin={'absent' if plugin_absent else 'PRESENT'} "
                 f"hooks={chk3['counts']['hook_events']} allowlist skills={len(allow['skills'])} agents={len(allow['agents'])} "
                 f"c3_overlap={overlap_state}"
                 + ("  -> PASS" if c3_ok else f"  -> FAIL {chk3['failures']} host_name_overlap={shared}"))
    if not c3_ok:
        return stop("C3 failed", c3)
    cfg["init_allowlist"] = allow
    cfg["isolated_baseline_hooks"] = baseline

    # C2 isolated with the plugin.
    why = guard()
    if why:
        return stop(why)
    before = isolation.ledger_snapshot(dirs)
    c2 = runner(cfg, PROMPT_OK, make_sandbox("c2", environ), label="c2", phase="control", session_id=str(uuid.uuid4()),
                plugin=export, environ=environ)
    c2_rows = _wait_ledger(before, dirs, c2["session_id"], 1, POLL_SECONDS)
    chk2 = isolation.check(c2, cfg, markers)
    c2_ok = chk2["ok"] and chk2["counts"]["sessionstart_started"] == 1 and chk2["counts"]["sessionstart_hooks"] == 1 and chk2["counts"]["host_hooks"] == 0 and c2_rows == 0
    lines.append(f"C2 isolated                checks={'PASS' if chk2['ok'] else 'FAIL ' + str(chk2['failures'])} "
                 f"sessionstart_hooks={chk2['counts']['sessionstart_hooks']} host_hooks={chk2['counts']['host_hooks']} "
                 f"ledger_rows={c2_rows}" + ("  -> PASS" if c2_ok else "  -> FAIL"))
    if not c2_ok:
        return stop("C2 failed (not switching modes)", c2)

    # C4 seeded log.
    why = guard()
    if why:
        return stop(why)
    before = isolation.ledger_snapshot(dirs)
    sb4 = make_sandbox("c4", environ)
    (sb4 / "CC-Session-Logs").mkdir()
    (sb4 / "CC-Session-Logs" / "2026-01-01-00_00-hook-canary.md").write_text("# Session Log\n", encoding="utf-8")
    c4 = runner(cfg, PROMPT_OK, sb4, label="c4", phase="control", session_id=str(uuid.uuid4()), plugin=export, environ=environ)
    c4_rows = _wait_ledger(before, dirs, c4["session_id"], 1, POLL_SECONDS)
    chk4 = isolation.check(c4, cfg, markers)
    canary = any(h.get("subtype") == "hook_response" and h.get("hook_event") == "SessionStart"
                 and 'topic "hook canary"' in str(h.get("stdout", "")) for h in isolation.hook_events(c4["events"]))
    c4_ok = chk4["ok"] and canary and c4_rows == 0
    lines.append(f"C4 isolated, seeded log    hook_stdout_canary={'yes' if canary else 'no'} ledger_rows={c4_rows}"
                 + ("  -> PASS" if c4_ok else f"  -> FAIL {chk4['failures']}"))
    if not c4_ok:
        return stop("C4 failed", c4)

    # C5 preserve quick, headless.
    why = guard()
    if why:
        return stop(why)
    before = isolation.ledger_snapshot(dirs)
    sb5 = make_sandbox("c5", environ)
    _seed_c5(sb5)
    original = (sb5 / "CLAUDE.md").read_text(encoding="utf-8")
    sid5 = str(uuid.uuid4())
    t1 = runner(cfg, C5_TURN1, sb5, label="c5a", phase="control", session_id=sid5, plugin=export, environ=environ)
    t2 = runner(cfg, "/cpr:preserve quick", sb5, label="c5b", phase="control", kind="preserve", resume=sid5, plugin=export,
                environ=environ, prior=t1)
    c5_rows = _wait_ledger(before, dirs, sid5, 1, POLL_SECONDS)
    chk1, chk5 = isolation.check(t1, cfg, markers), isolation.check(t2, cfg, markers)
    changed = (sb5 / "CLAUDE.md").read_text(encoding="utf-8") != original
    res2 = isolation.result_event(t2["events"]) or {}
    denials = len(res2.get("permission_denials") or [])
    c5_ok = chk1["ok"] and chk5["ok"] and changed and chk5["counts"]["ask_user"] == 0 and denials == 0 \
        and chk5["counts"]["sessionstart_hooks"] == 0 and c5_rows == 0
    lines.append(f"C5 preserve quick (resume) claude_md_changed={'yes' if changed else 'no'} "
                 f"ask_user={chk5['counts']['ask_user']} denials={denials} ledger_rows={c5_rows}"
                 + ("  -> PASS" if c5_ok else f"  -> FAIL turn1={chk1['failures']} turn2={chk5['failures']}"))
    if not c5_ok:
        return stop("C5 failed", t2)

    save_config(cfg, config_path)
    print(f"control  isolation={cfg['isolation']}  claude_code={cfg['claude_code_version']}  "
          f"model={cfg['model']}  hook_attribution=count", file=out)
    for ln in lines:
        print(ln, file=out)
    print("control: PASS (5/5)", file=out)
    return 0
