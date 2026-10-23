"""Judge calls through the isolated Claude runner.

Every call is a probe-mode run (no tools, no plugin, setting sources limited to
the project) on the judge's own model pin. It counts against a persisted call
budget before it launches and must pass isolation.check, or its labels are
excluded. A cross-family (Codex) judge was attempted and could not exclude user
instructions, so it was removed. Output carries counts and labels only.

Probe answers are labelled in batches: one call per probe session, one label
object per answer, matched by the answer's id and never by position. A failed
proof excludes every label of the call. A missing, duplicated or malformed label
excludes that probe only; nothing is coerced or guessed.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import claude_run
import isolation

HERE = Path(__file__).resolve().parents[1]  # experiments/<id>/code/
PROMPTS = HERE / "prompts"
ABSTENTION_GOLD = "Nothing in the memory says; the only right reply is that it is unknown."


class JudgeBudgetError(Exception):
    """The persisted call budget is spent or its file is unreadable."""


class JudgeStop(Exception):
    """A stop condition; the message is a short label."""


# --------------------------------------------------------------------------
# Persisted call budget and counters
# --------------------------------------------------------------------------

def _stats_path(environ, counter: str = "judge") -> Path:
    """The call counter's file. "judge" (judge-check) keeps judge-calls.json; any other counter has its own file."""
    return claude_run.eval_root(environ) / ("judge-calls.json" if counter == "judge" else f"{counter}-calls.json")


def load_stats(environ, counter: str = "judge") -> dict:
    """A missing file is zero calls. A file that exists but is unreadable refuses (fail closed)."""
    path = _stats_path(environ, counter)
    if not path.exists():
        return {"count": 0, "successful": 0, "failed": 0}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        stats = {k: int(data.get(k, 0)) for k in ("successful", "failed")}
        stats["count"] = int(data["count"])
        messages = None
        if "throttled" in data:  # present only once a call has been throttled
            stats["throttled"] = int(data["throttled"])
            messages = data.get("throttle_messages", [])
            if not isinstance(messages, list) or not all(isinstance(m, str) for m in messages):
                raise ValueError("throttle messages")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise JudgeBudgetError("judge call file unreadable")
    if min(stats.values()) < 0:
        raise JudgeBudgetError("judge call file unreadable")
    if messages is not None:
        stats["throttle_messages"] = messages
    return stats


def save_stats(environ, stats: dict, counter: str = "judge") -> None:
    claude_run.atomic_write(_stats_path(environ, counter), json.dumps(stats))


def take_call(environ, max_calls: int, counter: str = "judge") -> None:
    stats = load_stats(environ, counter)
    if stats["count"] >= max_calls:
        raise JudgeBudgetError("judge call budget spent")
    stats["count"] += 1
    save_stats(environ, stats, counter)


def bump(environ, key: str, counter: str = "judge") -> None:
    stats = load_stats(environ, counter)
    stats[key] += 1
    save_stats(environ, stats, counter)


def record_throttle(environ, message: str, counter: str = "judge") -> None:
    """Count a throttled call apart from successful and failed ones, and keep the CLI's message text."""
    stats = load_stats(environ, counter)
    stats["throttled"] = stats.get("throttled", 0) + 1
    stats["throttle_messages"] = list(stats.get("throttle_messages", [])) + [message]
    save_stats(environ, stats, counter)


# --------------------------------------------------------------------------
# Rate-limit signal
# --------------------------------------------------------------------------
# Every run's stream carries rate_limit_event rows whose info.status is "allowed" or "allowed_warning" (they report
# plan usage), so their count says nothing about a limit. A run was throttled when it did not succeed AND the result
# says so (HTTP 429 in api_error_status, or limit/quota wording in the CLI's result text) or a rate_limit_event
# carries a status other than the two allowed ones. No limited run is stored yet; this is the inferred shape.

RATE_LIMIT_OK = ("allowed", "allowed_warning")
RATE_LIMIT_TEXT = re.compile(r"usage limit|hit your limit|limit reached|rate.?limit|too many requests|quota|\b429\b", re.I)
# Labels a throttled run fails the proof with because it never finished; any other label is a real proof failure.
RATE_LIMIT_LABELS = frozenset({"result_error", "denials", "init_missing"})
MESSAGE_CHARS = 300


def rate_limit_message(rec: dict):
    """The throttle message of a run record, or None when the run was not throttled.

    Only the CLI's own limit or quota text is kept (the result text when it reads like one, else a status label);
    never model or transcript content.
    """
    events = rec.get("events") or []
    result = isolation.result_event(events)
    if isinstance(result, dict) and result.get("is_error") is False and result.get("subtype") == "success":
        return None
    result = result or {}
    text = result.get("result") if isinstance(result.get("result"), str) else ""
    limited_text = bool(RATE_LIMIT_TEXT.search(text))
    http_429 = result.get("api_error_status") == 429
    statuses = set()
    for event in events:
        if isinstance(event, dict) and event.get("type") == "rate_limit_event":
            info = event.get("rate_limit_info")
            status = info.get("status") if isinstance(info, dict) else None
            if isinstance(status, str) and status not in RATE_LIMIT_OK:
                statuses.add(status[:40])
    if limited_text:
        return " ".join(text.split())[:MESSAGE_CHARS]
    if http_429:
        return "HTTP 429"
    if statuses:
        return "rate_limit_event status=" + ",".join(sorted(statuses))
    return None


def is_throttled(rec: dict) -> bool:
    """A record whose proof failed only in the ways an unfinished run does, and that carries a rate-limit signal."""
    proof = rec.get("proof") or {}
    return (not proof.get("ok") and set(proof.get("failures") or ["?"]) <= RATE_LIMIT_LABELS
            and rate_limit_message(rec) is not None)


# --------------------------------------------------------------------------
# Schema validation (small, stdlib)
# --------------------------------------------------------------------------

def validate(value, schema, path="$") -> list:
    """Return a list of problem labels (empty means valid). Handles the keywords our schemas use."""
    problems = []
    kind = schema.get("type")
    checks = {"object": dict, "array": list, "string": str, "boolean": bool}
    if kind == "integer":
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif kind in checks:
        ok = isinstance(value, checks[kind])
    else:
        ok = True
    if not ok:
        return [f"{path}:type"]
    if "enum" in schema and value not in schema["enum"]:
        problems.append(f"{path}:enum")
    if kind == "object":
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                problems.append(f"{path}.{key}:missing")
        if schema.get("additionalProperties") is False:
            problems += [f"{path}.{k}:extra" for k in value if k not in props]
        for key, sub in props.items():
            if key in value:
                problems += validate(value[key], sub, f"{path}.{key}")
    if kind == "array" and "items" in schema:
        for i, item in enumerate(value):
            problems += validate(item, schema["items"], f"{path}[{i}]")
    return problems


def load_schema(name: str) -> Path:
    return PROMPTS / name


PROMPT_FILES = {"probe": ("judge-probe.md", "judge-probe.schema.json"), "items": ("judge-items.md", "judge-items.schema.json")}


def prompt_hash(kind: str) -> str:
    """A short hash of the judge prompt and schema files for "probe" or "items"; a label cache key carries it, so a
    changed prompt or schema never reuses an old label."""
    digest = hashlib.sha256()
    for name in PROMPT_FILES[kind]:
        digest.update(name.encode("utf-8") + b"\0" + (PROMPTS / name).read_bytes() + b"\0")
    return digest.hexdigest()[:16]


# --------------------------------------------------------------------------
# Labelling
# --------------------------------------------------------------------------

def fill(template: str, **values) -> str:
    for key, val in values.items():
        template = template.replace("{" + key + "}", str(val))
    return template


PROBE_BLOCK = """Answer id: {id}

Question:
{question}

Gold answer:
{gold}

Stale answers (once true, now wrong):
{stale}

The model's answer:
{answer}"""


def probe_prompt(case: dict) -> str:
    """The prompt for a batch case {"id", "probes": [probe case, ...]}: one block per answer."""
    blocks = []
    for p in case["probes"]:
        stale = "\n".join(f"- {s}" for s in p["stale"]) or "(none)"
        blocks.append(PROBE_BLOCK.format(id=p["id"], question=p["question"], gold=p["gold"], stale=stale, answer=p["answer"]))
    return fill((PROMPTS / "judge-probe.md").read_text(encoding="utf-8"), case_id=case["id"], probes="\n\n---\n\n".join(blocks))


def items_prompt(case: dict) -> str:
    needed = "\n".join(f"{i['id']}: {i['fact']} (heading: {i['heading']})" for i in case["needed"])
    not_needed = "\n".join(f"{i['id']}: {i['fact']}" for i in case["not_needed"])
    lines = "\n".join(f"{n}. [{h}] {t}" for n, (h, t) in enumerate(case["lines"], start=1))
    return fill((PROMPTS / "judge-items.md").read_text(encoding="utf-8"), case_id=case["id"],
                needed=needed, not_needed=not_needed, lines=lines)


def parse_output(call: dict, schema_name: str):
    """Return (value, problems). Invalid or missing output is a problem, never a guess."""
    if call.get("output") is None:
        return None, ["output_missing"]
    try:
        value = json.loads(call["output"])
    except ValueError:
        return None, ["output_not_json"]
    schema = json.loads(load_schema(schema_name).read_text(encoding="utf-8"))
    return value, validate(value, schema)


def extract_json_text(text):
    """The reply may arrive in a code fence; strip it. Anything else is left for the parser to reject."""
    if not isinstance(text, str):
        return None
    m = re.fullmatch(r"\s*```(?:json)?\s*(.*?)\s*```\s*", text, re.S)
    return m.group(1) if m else text


def run_judge_call(cfg, prompt, schema_name, environ, model=None, *, phase="judge", counter="judge", max_calls=None):
    """One judge (or labeller) call through `claude_run.run_claude` in probe mode. Returns the run record, with rec["proof"].

    The run must pass isolation.check against the judge's own model pin (not the
    system-under-test pin); rec["proof"] holds the failure labels. Counts against the persisted budget before it
    launches; the proof outcome is counted as successful, failed or (a rate-limit signal on an unfinished run)
    throttled, and a throttle keeps the CLI's message text.

    phase picks the spend ledger (the default, "judge", spends from the control budget; scoring passes "evaluation").
    counter picks the persisted call counter and max_calls its cap (default: the judge block's max_calls). The
    judge check uses the defaults; scoring uses its own counter so it never draws on the judge check's budget.
    """
    jcfg = cfg["judge"]
    pin = model or jcfg["model"]
    take_call(environ, int(jcfg["max_calls"] if max_calls is None else max_calls), counter)
    schema_text = load_schema(schema_name).read_text(encoding="utf-8")
    sandbox = claude_run.make_sandbox("judge", environ)
    rec = claude_run.run_claude(cfg, prompt + "\nSchema:\n" + schema_text + "\nReply with only the JSON object.",
                                sandbox, label="judge", phase=phase, kind="probe", probe=True, model=pin, environ=environ)
    proof = isolation.check(rec, dict(cfg, model=pin), isolation.host_markers(environ))
    rec["proof"] = proof
    if proof["ok"]:
        bump(environ, "successful", counter)
    elif is_throttled(rec):
        record_throttle(environ, rate_limit_message(rec), counter)
    else:
        bump(environ, "failed", counter)
    return rec


def reply_text(rec):
    """The reply text of a run record, unfenced; None when the run has no result text."""
    return extract_json_text((isolation.result_event(rec["events"]) or {}).get("result"))


def label_with_claude(cfg, prompt, schema_name, key, environ, model=None, **route):
    """run_judge_call, then the whole reply checked against the schema. Returns (value[key] or None, rec).

    None means the proof failed or the output was invalid. route is run_judge_call's phase, counter and max_calls.
    """
    rec = run_judge_call(cfg, prompt, schema_name, environ, model, **route)
    if not rec["proof"]["ok"]:
        return None, rec
    value, problems = parse_output({"output": reply_text(rec)}, schema_name)
    return (value[key] if value is not None and not problems else None), rec


def label_items(cfg, case: dict, environ, model=None, **route):
    return label_with_claude(cfg, items_prompt(case), "judge-items.schema.json", "lines", environ, model, **route)


def probe_schemas():
    """(envelope, element) from the one probe schema file: the envelope is the schema without its element schema."""
    schema = json.loads(load_schema("judge-probe.schema.json").read_text(encoding="utf-8"))
    element = schema["properties"]["labels"]["items"]
    envelope = copy.deepcopy(schema)
    del envelope["properties"]["labels"]["items"]
    return envelope, element


def parse_probe_labels(text, ids):
    """Return ({probe id: label}, sorted excluded ids, sorted problem labels) for a batched reply.

    A reply that is missing, not JSON or has an invalid envelope excludes every probe. Otherwise each element is
    checked on its own: an id that appears twice, an invalid element, or no element at all excludes that probe
    only. An id outside the batch is a problem and excludes nothing. Nothing is coerced; problems are labels and
    ids, never answer or quote text.
    """
    envelope, element = probe_schemas()
    wanted = set(ids)
    if text is None:
        return {}, sorted(wanted), ["output_missing"]
    try:
        value = json.loads(text)
    except ValueError:
        return {}, sorted(wanted), ["output_not_json"]
    if validate(value, envelope):
        return {}, sorted(wanted), ["envelope_invalid"]
    appearances = {}
    for el in value["labels"]:
        if isinstance(el, dict) and isinstance(el.get("id"), str):
            appearances[el["id"]] = appearances.get(el["id"], 0) + 1
    labels, excluded, problems = {}, set(), set()
    for el in value["labels"]:
        bad = validate(el, element)
        eid = el.get("id") if isinstance(el, dict) else None
        if not isinstance(eid, str):
            problems.add("element_invalid")
        elif eid not in wanted:
            problems.add("unknown_id")
        elif appearances[eid] > 1:
            excluded.add(eid)
            problems.add(f"{eid}:duplicate")
        elif bad:
            excluded.add(eid)
            problems.add(f"{eid}:invalid")
        else:
            labels[eid] = el["label"]
    for pid in sorted(wanted - set(labels) - excluded):
        excluded.add(pid)
        problems.add(f"{pid}:missing")
    return labels, sorted(excluded), sorted(problems)


def label_probe(cfg, case: dict, environ, model=None, **route):
    """Label a batch case {"id", "probes": [...]} in one call. Return ({probe id: label}, record).

    None instead of the dict means the proof failed: every label of the call is excluded and rec["proof"] holds
    the failure labels. Otherwise the dict holds the probes whose label survived the schema check; rec["label_excluded"]
    lists the rest and rec["label_problems"] says why. Probe ids must be unique in the batch (ValueError, before
    any call is counted). route is run_judge_call's phase, counter and max_calls.
    """
    ids = [p["id"] for p in case["probes"]]
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("probe batch needs unique, non-empty probe ids")
    rec = run_judge_call(cfg, probe_prompt(case), "judge-probe.schema.json", environ, model, **route)
    if not rec["proof"]["ok"]:
        return None, rec
    labels, excluded, problems = parse_probe_labels(reply_text(rec), ids)
    rec["label_excluded"], rec["label_problems"] = excluded, problems
    return labels, rec


# --------------------------------------------------------------------------
# Synthetic cases (invented, generic)
# --------------------------------------------------------------------------

PROBE_CASES = [
    {"id": "P1", "question": "Which port does the dev server use?", "gold": "8081", "stale": ["8080"],
     "answer": "It runs on port 8081.", "expect": "correct"},
    {"id": "P2", "question": "Which upload endpoint should clients call?", "gold": "/v2/upload",
     "stale": ["/v1/upload"], "answer": "Use /v2/upload; v1 is deprecated.", "expect": "correct"},
    {"id": "P3", "question": "Which port does the dev server use?", "gold": "8081", "stale": ["8080"],
     "answer": "It uses port 3000.", "expect": "incorrect"},
    {"id": "P4", "question": "Which upload endpoint should clients call?", "gold": "/v2/upload",
     "stale": ["/v1/upload"], "answer": "Clients should call /v1/upload.", "expect": "incorrect"},
    {"id": "P5", "question": "Which database holds sessions?", "gold": "Postgres", "stale": [],
     "answer": "I do not have that information.", "expect": "abstained"},
    {"id": "P6", "question": "Who owns the release process?", "gold": "the platform team", "stale": [],
     "answer": "That is not stated in the context I have, so I cannot say.", "expect": "abstained"},
    # An abstention probe carries the neutral gold below. The judge may call an abstaining reply "abstained" or
    # "correct"; scoring counts both as not false, so the check accepts both.
    {"id": "P7", "question": "Which region hosts the staging database?", "gold": ABSTENTION_GOLD, "stale": [],
     "answer": "Nothing in the context says which region that is.", "expect": "abstained",
     "accept": ("abstained", "correct")},
]

PROBE_BATCH = {"id": "check-probes", "probes": PROBE_CASES}

ITEMS_CASE = {
    "id": "I1",
    "needed": [
        {"id": "N1", "fact": "The dev server runs on port 8081 because 8080 collides with the metrics sidecar", "heading": "Commands"},
        {"id": "N2", "fact": "The /v1/upload endpoint is deprecated; use /v2/upload", "heading": "Decisions"},
        {"id": "N3", "fact": "Run the tests with make check", "heading": "Commands"},
    ],
    "not_needed": [{"id": "E1", "fact": "A flaky test timed out in the cache layer on Tuesday"}],
    "lines": [
        ("Commands", "- Dev server runs on port 8081 (8080 collides with the metrics sidecar)."),
        ("Decisions", "- /v1/upload is deprecated; use /v2/upload."),
        ("Commands", "- Run the tests with make check."),
        ("Status", "- Tuesday's flaky test failed with a timeout in the cache layer."),
        ("Notes", "- The maintainer prefers tabs."),
    ],
    "expect": [("needed", "N1"), ("needed", "N2"), ("needed", "N3"), ("not_needed", "E1"), ("unplanted", "")],
}


def score_items(lines, expect) -> int:
    """Lines matching kind and item ID (and placed_ok for needed lines), by line number."""
    got = {l["line"]: l for l in lines if isinstance(l, dict)}
    ok = 0
    for n, (kind, item) in enumerate(expect, start=1):
        row = got.get(n)
        if row and row["kind"] == kind and row["item_id"] == item and (row["placed_ok"] if kind == "needed" else True):
            ok += 1
    return ok


# --------------------------------------------------------------------------
# judge-check
# --------------------------------------------------------------------------

def judge_check(environ=None, out=None, config_path=None) -> int:
    out = out or sys.stdout
    environ = environ if environ is not None else os.environ
    try:
        return _judge_check(environ, out, config_path)
    except JudgeStop as stop:
        print(f"judge-check: STOP {stop}", file=out)
    except (JudgeBudgetError, claude_run.SpendError) as exc:
        print(f"judge-check: STOP {exc}", file=out)
    except Exception as exc:  # label only: exception text can carry paths
        print(f"judge-check: ERROR {type(exc).__name__}", file=out)
    return 1


def _judge_check(environ, out, config_path) -> int:
    if environ.get("CPR_EVAL_LIVE") != "1":
        print("live run refused: set CPR_EVAL_LIVE=1", file=out)
        return 2
    cfg = claude_run.load_config(config_path)
    jcfg = cfg["judge"]
    if jcfg.get("backend") != "claude":
        print("judge-check: STOP unsupported judge backend", file=out)
        return 1
    if environ.get("CLAUDE_CONFIG_DIR"):
        print("unset CLAUDE_CONFIG_DIR", file=out)
        return 3
    reason = claude_run.preflight(cfg, environ)
    if reason:
        print(f"judge-check: STOP preflight: {reason}", file=out)
        return 1
    load_stats(environ)  # fail closed on an unreadable call file before any call
    model = jcfg["model"]
    # Two calls, each through the same proof: the probe cases as one batch, then the items case.
    labels, rec = label_probe(cfg, PROBE_BATCH, environ)
    if not rec["proof"]["ok"]:
        raise JudgeStop("probes isolation failed: " + ",".join(rec["proof"]["failures"]))
    probe_ok = sum(1 for case in PROBE_CASES  # an excluded probe is wrong
                   if labels.get(case["id"]) in case.get("accept", (case["expect"],)))
    lines, rec = label_items(cfg, ITEMS_CASE, environ)
    if not rec["proof"]["ok"]:
        raise JudgeStop("items isolation failed: " + ",".join(rec["proof"]["failures"]))
    items_ok = score_items(lines, ITEMS_CASE["expect"]) if lines else 0
    summary = f"backend=claude model={model} isolation=PASS calls 2 probe {probe_ok}/{len(PROBE_CASES)} items {items_ok}/5"
    if probe_ok != len(PROBE_CASES) or items_ok != 5:
        print(f"judge-check: FAIL {summary}", file=out)
        return 1
    print(f"judge-check: {summary}", file=out)
    return 0
