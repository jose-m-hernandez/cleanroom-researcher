"""The pure parts of `author --repair` of EXP-0001: the evidence selection (P:253), the prompt, the checks on the
repaired specs, the runner's own diff, the repair log and the new store. No model call and no ledger write here;
runner/author.py makes the one call and records it.

The runner selects the evidence and never the owner: a probe is selected when the floor answered it correctly in any
seed, or the oracle answered it correctly in fewer than a majority of its seeds (the negation of the filter in
score.scorable_probes). The blind agent sees the guide, the selected fixtures' spec.json and that evidence, nothing else.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import arms
import claude_run
import fixtures
import score
import seal

ARMS = ("floor", "oracle")
LOG_SUFFIX = ".repair-log.md"
SHORT = 300
ABSENT = object()

INSTRUCTION = (
    "Repair defects only. Below are the guide, the fixture specs to repair, and for each probe that needs repair the "
    "evidence that selected it: the question, the gold answer, and each seed's floor answer and oracle answer with "
    "the judge's label. Change a fixture only where its evidence shows a defect in the fixture, and leave every other "
    "field as it is. Return each repaired fixture as the header line heldout-NNN-name (the fixture id, unchanged) "
    "followed by one fenced block holding the whole spec.json. Return only fixtures listed below. Keep every probe id, "
    "and add or remove no probe.")


def _seed_keys(table) -> list:
    return sorted(table, key=int)


def _cells(scores: dict, specs: dict):
    """(fixture id, probe, floor labels, oracle labels) for each active non-abstention probe, in fixture then spec order.
    A label list holds one entry per seed on record; None is a missing label."""
    for fid in scores["fixtures"]:
        by_arm = {arm: scores["labels"].get(arm, {}).get(fid, {}) for arm in ARMS}
        for probe in fixtures.active_probes(specs[fid]):
            if probe["type"] == "abstention":
                continue
            yield (fid, probe, *[[by_arm[arm][s].get(probe["id"]) for s in _seed_keys(by_arm[arm])] for arm in ARMS])


def _complete(floor: list, oracle: list) -> bool:
    return bool(floor) and bool(oracle) and None not in floor and None not in oracle


def count_missing(scores: dict, specs: dict) -> int:
    """How many probes have a missing floor or oracle label (score drops them as label_missing; they are not selected)."""
    return sum(1 for _f, _p, floor, oracle in _cells(scores, specs) if not _complete(floor, oracle))


def select_evidence(results, scores: dict, specs: dict) -> list:
    """The evidence items: {"fixture", "probe", "question", "gold", "seeds": {"1": {floor, floor_label, oracle,
    oracle_label}}, "reasons"}, for each probe the floor got right in any seed or the oracle got right in fewer than
    a majority of its seeds. Answers come from the session files of the results directory."""
    sessions, items = {}, []
    for fid, probe, floor, oracle in _cells(scores, specs):
        if not _complete(floor, oracle):
            continue
        reasons = []
        if floor.count("correct") > 0:
            reasons.append("floor_correct")
        if not 2 * oracle.count("correct") > len(oracle):
            reasons.append("oracle_minority")
        if not reasons:
            continue
        for arm in ARMS:
            sessions.setdefault((arm, fid), score.load_probe_sessions(results, arm, fid))
        seeds = {}
        for s in sorted(set(scores["labels"]["floor"][fid]) | set(scores["labels"]["oracle"][fid]), key=int):
            cell = {}
            for arm in ARMS:
                answer = (sessions[(arm, fid)].get(int(s)) or {}).get("answers", {}).get(probe["id"])
                cell[arm] = answer
                cell[arm + "_label"] = scores["labels"][arm][fid].get(s, {}).get(probe["id"])
            seeds[s] = cell
        items.append({"fixture": fid, "probe": probe["id"], "question": probe["question"], "gold": probe["answer"],
                      "seeds": seeds, "reasons": reasons})
    return items


def store_has_link_or_git(root) -> bool:
    """True when the store holds a symlink or a .git entry anywhere (a fixture store holds neither)."""
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            if name == ".git" or os.path.islink(os.path.join(base, name)):
                return True
    return False


def inputs(results, fixtures_root, runs: list):
    """(label, ctx): the label of the first guard that fires ("" when none), then the context the call needs.
    runs is the rows of author-runs.json. The guards, in order: the folders exist; the results hold no arm other than
    floor and oracle; scores.json is there, readable, from a completed pass and a floor and oracle scoring; the store
    holds no symlink or git folder; the specs load and validate (selection needs them, so this comes before the next
    three); no selected fixture is a replacement; no repair call has been made; something is selected."""
    results, fixtures_root = Path(results), Path(fixtures_root)
    if not results.is_dir():
        return "--from does not exist", None
    if not fixtures_root.is_dir():
        return "--fixtures-root does not exist", None
    try:
        if seal.has_baseline(results):
            return "the results hold an arm other than floor and oracle", None
    except seal.SealStop as exc:
        return str(exc), None
    if not (results / "scores.json").exists():
        return "scores.json is missing", None
    try:
        scores = arms.read_json(results / "scores.json")
        ok = (isinstance(scores, dict) and isinstance(scores.get("fixtures"), list)
              and all(isinstance(f, str) for f in scores["fixtures"]) and isinstance(scores.get("labels"), dict)
              and isinstance(scores.get("arms"), list))
    except (OSError, ValueError):
        return "scores.json is unreadable", None
    if not ok:
        return "scores.json is malformed", None
    problem = score.pass_problem(results, False, scores)
    if problem:
        return problem, None
    if sorted(scores["arms"]) != sorted(ARMS):
        return "scores.json is not a floor and oracle scoring", None
    if store_has_link_or_git(fixtures_root):
        return "the fixture store holds a symlink or a git folder", None
    try:
        specs = score.load_specs(fixtures_root, scores["fixtures"])
        evidence = select_evidence(results, scores, specs)
    except (score.ScoreAbort, KeyError, AttributeError, TypeError, ValueError) as exc:
        return (str(exc) if isinstance(exc, score.ScoreAbort) else "scores.json is malformed"), None
    ids = sorted({i["fixture"] for i in evidence})
    made = {i for r in runs if r.get("replaces") for i in r.get("ids_written", [])}
    for fid in ids:
        if fid in made:
            return f"fixture {fid} is a replacement (P:262)", None
    if any(r.get("mode") == "repair" for r in runs):
        return "a repair call has already been made (one round, P:252)", None
    if not ids:
        return "no probe needs repair", None
    texts = {fid: (fixtures_root / fid / "spec.json").read_text(encoding="utf-8") for fid in ids}
    return "", {"evidence": evidence, "ids": ids, "old_specs": {fid: specs[fid] for fid in ids}, "spec_texts": texts,
                "missing": count_missing(scores, specs)}


def refusal(results, fixtures_root, runs: list) -> str:
    """The label of the first guard that fires, "" when the call may go on (see inputs)."""
    return inputs(results, fixtures_root, runs)[0]


def _ascii(text) -> str:
    return str(text).encode("ascii", "replace").decode("ascii")


def build_prompt(guide_text: str, spec_texts: dict, evidence: list) -> str:
    """The instruction line, the guide, each selected fixture's spec.json text, then the evidence as plain text.
    spec_texts maps a fixture id to its spec.json text as it is on disk."""
    parts = [INSTRUCTION, guide_text.rstrip("\n")]
    for fid in sorted(spec_texts):
        text = spec_texts[fid]
        parts.append(f"Fixture {fid}: spec.json\n{text}{'' if text.endswith(chr(10)) else chr(10)}End of {fid}")
    lines = ["Evidence"]
    for item in evidence:
        lines += ["", f"Fixture {item['fixture']}, probe {item['probe']}", f"Question: {item['question']}",
                  f"Gold: {item['gold']}", "Selected because: " + ", ".join(item["reasons"])]
        for seed, cell in item["seeds"].items():
            lines += [f"Seed {seed} floor ({cell['floor_label']}): {cell['floor']}",
                      f"Seed {seed} oracle ({cell['oracle_label']}): {cell['oracle']}"]
    parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _probe_ids(spec) -> list:
    probes = spec.get("probes") if isinstance(spec, dict) else None
    return sorted(p.get("id") if isinstance(p, dict) and isinstance(p.get("id"), str) else "" for p in
                  (probes if isinstance(probes, list) else []))


def check_repaired(old_specs: dict, new_specs: dict) -> list:
    """Labels, `<id>: <label>`, never spec text: an id that was not selected, a selected id with no block, a probe id
    set that differs (a probe added or removed), and each label fixtures.validate_spec returns."""
    found = [f"{i}: not_selected" for i in sorted(new_specs) if i not in old_specs]
    found += [f"{i}: missing" for i in sorted(old_specs) if i not in new_specs]
    for fid in sorted(set(old_specs) & set(new_specs)):
        new = new_specs[fid]
        if not isinstance(new, dict):
            found.append(f"{fid}: not_an_object")
            continue
        if _probe_ids(new) != _probe_ids(old_specs[fid]):
            found.append(f"{fid}: probe_set_changed")
        try:
            found += [f"{fid}: {label}" for label in fixtures.validate_spec(new, directory_name=fid)]
        except Exception:  # a spec the validator cannot read at all
            found.append(f"{fid}: validate_failed")
    return found


def _short(value) -> str:
    text = "<absent>" if value is ABSENT else json.dumps(value, sort_keys=True, ensure_ascii=True)
    return text if len(text) <= SHORT else text[:SHORT - 3] + "..."


def _digest(value) -> str:
    """sha256 of the full value as JSON (the text _short truncates), so a change past the cut still shows."""
    if value is ABSENT:
        return "absent"
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode("utf-8")).hexdigest()


def _change(fid, probe, key, old, new) -> list:
    return [f"{fid} / {probe} / {key}: changed", f"  old: {_short(old)} sha256={_digest(old)}",
            f"  new: {_short(new)} sha256={_digest(new)}"]


def diff_lines(old: dict, new: dict) -> list:
    """The runner's own diff, field by field: the top level key by key (probe `-`), then each probe, keyed by its id.
    Each change is a `fixture / probe / field: changed` line and two lines with the old and new value as JSON."""
    fid = new.get("id", old.get("id"))
    lines = []
    for key in sorted((set(old) | set(new)) - {"probes"}):
        if old.get(key, ABSENT) != new.get(key, ABSENT):
            lines += _change(fid, "-", key, old.get(key, ABSENT), new.get(key, ABSENT))
    olds = {p["id"]: p for p in old.get("probes", [])}
    news = {p["id"]: p for p in new.get("probes", [])}
    if list(olds) != list(news):
        lines += [f"{fid} / - / probes: order changed", f"  old: {json.dumps(list(olds))}",
                  f"  new: {json.dumps(list(news))}"]
    for pid in sorted(set(olds) | set(news)):
        a, b = olds.get(pid, {}), news.get(pid, {})
        for key in sorted(set(a) | set(b)):
            if a.get(key, ABSENT) != b.get(key, ABSENT):
                lines += _change(fid, pid, key, a.get(key, ABSENT), b.get(key, ABSENT))
    return lines


def changed_probe_count(lines: list) -> int:
    """How many probes the diff lines change (a top level change, probe `-`, is not a probe)."""
    return len({line.split(" / ")[1] for line in lines if line.endswith(": changed")} - {"-"})


def log_entries(old_specs: dict, new_specs: dict, evidence: list) -> list:
    return [{"fixture": fid, "diff": diff_lines(old_specs[fid], new_specs[fid]),
             "evidence": [i for i in evidence if i["fixture"] == fid]} for fid in sorted(old_specs)]


def render_log(entries: list) -> str:
    """Markdown, plain ASCII. Labels only for the evidence: no answer text, so no target-derived text is published."""
    out = ["# Repair log", "",
           "Written by the runner. Each section lists the fields the agent changed (the runner's own diff) and the "
           "labels of the floor and oracle answers that selected the probes."]
    for entry in entries:
        out += ["", f"## {entry['fixture']}", "", "Changes:", ""]
        out += ["  " + line for line in entry["diff"]] or ["  none"]
        out += ["", "Selected by the runner:", ""]
        for item in entry["evidence"]:
            out.append(f"- {item['probe']}: " + ", ".join(item["reasons"]))
            for seed, cell in item["seeds"].items():
                out.append(f"  - seed {seed}: floor={_ascii(cell['floor_label'])} oracle={_ascii(cell['oracle_label'])}")
    return "\n".join(out) + "\n"


def write_log(path, entries: list) -> None:
    claude_run.atomic_write(path, render_log(entries))


def copy_store(src, dst) -> None:
    """Every file under src, byte for byte, at the same relative path under dst (which exists)."""
    src, dst = Path(src), Path(dst)
    for base, _dirs, files in os.walk(src):
        folder = dst / Path(base).relative_to(src)
        folder.mkdir(parents=True, exist_ok=True)
        for name in files:
            shutil.copyfile(Path(base) / name, folder / name)
