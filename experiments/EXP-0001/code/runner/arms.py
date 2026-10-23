"""Arms: the three memory files a probe session can start from, and the results layout.

floor     the fixture's CLAUDE.md, unchanged.
baseline  the plugin's `/cpr:preserve quick` run on --resume against an installed copy of the fixture's
          session; the resulting CLAUDE.md.
oracle    the floor plus each needed item's oracle_line under its oracle_section.

Floor and oracle are seed-invariant files. A skill variant (a SKILL.md under arms/<name>/) is a further
preserve arm and runs the same `quick` argument, so no arm is asked a question.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from pathlib import Path

import claude_run
import fixtures
import isolation

HERE = Path(__file__).resolve().parents[1]  # experiments/<id>/code/
ARMS_DIR = HERE / "arms"
BUILTIN_ARMS = ("floor", "baseline", "oracle")
PRESERVE_PROMPT = "/cpr:preserve quick"
HEADING_RE = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")


class ArmError(Exception):
    """An arm name that is not a built-in and has no skill variant."""


def variant_skill(name: str, arms_dir=None):
    """The SKILL.md of a skill-variant arm, or None."""
    path = Path(arms_dir or ARMS_DIR) / name / "SKILL.md"
    return path if NAME_RE.match(name) and path.is_file() else None


def arm_kind(name: str, arms_dir=None) -> str:
    """"static" (floor, oracle) or "preserve" (baseline and any skill variant). An unknown name refuses."""
    if name in ("floor", "oracle"):
        return "static"
    if name == "baseline" or variant_skill(name, arms_dir):
        return "preserve"
    raise ArmError("unknown arm")


def static_text(arm: str, spec: dict) -> str:
    floor = spec["repo"]["files"]["CLAUDE.md"]
    return floor if arm == "floor" else fixtures.render_oracle(spec)


def line_count(text: str) -> int:
    """`wc -l` logic: the number of newline characters."""
    return text.count("\n")


def added_lines(floor: str, arm_text: str) -> list:
    """[(heading, line)] for each line the arm added or rewrote relative to the floor.

    Blank lines and heading lines are not counted as added lines; a heading names the section its lines sit
    under (the nearest heading above, "" before the first one).
    """
    old, new = floor.split("\n"), arm_text.split("\n")
    heading_of, current = [], ""
    for line in new:
        m = HEADING_RE.match(line)
        if m:
            current = m.group(1)
        heading_of.append(current)
    out = []
    for tag, _, _, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag in ("insert", "replace"):
            for j in range(j1, j2):
                text = new[j].rstrip()
                if text.strip() and not HEADING_RE.match(text):
                    out.append((heading_of[j], text))
    return out


# --------------------------------------------------------------------------
# Results layout (one directory per run; every file derived, none carries a path from the machine)
# --------------------------------------------------------------------------

def arm_text_path(out, arm, fixture_id, seed) -> Path:
    return Path(out) / "arms" / arm / fixture_id / f"s{seed}.md"


def preserve_path(out, arm, fixture_id, seed) -> Path:
    return Path(out) / "preserve" / arm / fixture_id / f"s{seed}.json"


def probe_path(out, arm, fixture_id, seed) -> Path:
    return Path(out) / "probes" / arm / fixture_id / f"s{seed}.json"


def write_json(path, data) -> None:
    claude_run.atomic_write(Path(path), json.dumps(data, indent=1, sort_keys=True) + "\n")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def raw_path(out, kind, arm, fixture_id, seed, environ=None) -> Path:
    """Under $TMPDIR/cpr-eval/raw/<run>/, never inside the results directory. <run> is the results directory's name."""
    return claude_run.eval_root(environ) / "raw" / Path(out).name / f"{kind}-{arm}-{fixture_id}-s{seed}.json"


def write_raw(out, kind, arm, fixture_id, seed, record, environ=None) -> None:
    """The full run record (events included), under $TMPDIR/cpr-eval/raw/<run>/. Never read by score or report."""
    claude_run.atomic_write(raw_path(out, kind, arm, fixture_id, seed, environ), json.dumps(record, default=str))


def input_total(usage):
    """input + cache_creation + cache_read tokens of a result's usage; None when input_tokens is not a number."""
    if not isinstance(usage, dict):
        return None

    def num(key):
        v = usage.get(key)
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    base = num("input_tokens")
    if base is None:
        return None
    return base + (num("cache_creation_input_tokens") or 0) + (num("cache_read_input_tokens") or 0)


def summary_of(record: dict) -> dict:
    """The fields of a run record worth keeping in a result file: cost, tokens, time. No events, no paths."""
    usage = record.get("usage") if isinstance(record.get("usage"), dict) else {}
    return {"cost_usd": record.get("cost_usd"), "duration_ms": record.get("duration_ms"), "wall_s": record.get("wall_s"),
            "rate_limit_events": record.get("rate_limit_events"), "input_tokens_total": input_total(usage),
            "output_tokens": usage.get("output_tokens") if isinstance(usage.get("output_tokens"), (int, float)) else None}


# --------------------------------------------------------------------------
# The preserve run
# --------------------------------------------------------------------------

def preserve_run(spec: dict, fdir, arm: str, cfg: dict, environ, runner, plugin, markers: list) -> dict:
    """Install the fixture, run `/cpr:preserve quick` on --resume, and read back CLAUDE.md.

    Returns {"ok", "labels", "text", "changed", "record"}. ok is false when the run fails the isolation proof or
    CLAUDE.md is gone; the caller stops the batch. The prior record is the kind install_check builds (the
    installed session has no earlier record in this process), so the run is charged its full reported cost.
    """
    sandbox, sid, _ = fixtures.install_fixture(Path(fdir), cfg, environ, f"pres-{arm}")
    prior = {"session_id": sid, "sandbox": str(sandbox), "memory_dir_after": []}
    record = runner(cfg, PRESERVE_PROMPT, sandbox, label=f"preserve-{arm}", phase="evaluation", kind="preserve",
                    resume=sid, plugin=plugin, prior=prior, environ=environ)
    proof = isolation.check(record, cfg, markers)
    record["proof"] = proof
    if not proof["ok"]:
        return {"ok": False, "labels": list(proof["failures"]), "text": None, "changed": False, "record": record}
    path = Path(sandbox) / "CLAUDE.md"
    if not path.is_file():
        return {"ok": False, "labels": ["claude_md_missing"], "text": None, "changed": False, "record": record}
    text = path.read_text(encoding="utf-8")
    return {"ok": True, "labels": [], "text": text, "changed": text != spec["repo"]["files"]["CLAUDE.md"],
            "record": record}


def plugin_for(arm: str, cfg: dict, environ, cache: dict, arms_dir=None):
    """The plugin export for a preserve arm (the pinned ref, with the variant's SKILL.md overlaid), once per arm."""
    if arm not in cache:
        overlay = variant_skill(arm, arms_dir)
        cache[arm] = claude_run.export_plugin(cfg["plugin_ref"], environ, overlay_skill=overlay)
    return cache[arm]


def content_key(*parts) -> str:
    """A short stable key for a labelling input."""
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]

