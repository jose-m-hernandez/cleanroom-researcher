"""Test support: a labels.json in the legacy key format (model-less, no prompt hash) read as the current keys.

The committed results of dev-baseline-1seed and pilot-20261017 keep the legacy format: they were labelled before the
keys carried the judge and a prompt hash, and nothing re-scores them. Tests that replay those labels convert a copy in
a temporary directory with `convert`; the committed files are never rewritten. A stored key that matches no case of the
given specs is dropped from the copy (a label for a question that was later reworded).
"""

from __future__ import annotations

from pathlib import Path

from tests import exp0001_paths  # noqa: F401
import arms  # noqa: E402
import fixtures  # noqa: E402
import score  # noqa: E402

LEGACY_MODEL = ""


def _old_key(kind, case) -> str:
    return arms.content_key(kind, {k: v for k, v in case.items() if k != "id"}, LEGACY_MODEL)


def key_maps(sources: list, specs_list: list, judge_model: str) -> tuple:
    """({old probe key: new}, {old items key: new}) for every case the given results directories can ask about.

    sources are the results directories whose answers and arm texts were labelled into this labels.json (the
    directory itself, and one it was copied from). specs_list holds one {fixture id: spec} per spec version the
    labels may have been made against (the current specs, and an earlier commit's for labels made before a fixture
    repair). A pair that does not match what was labelled only adds keys nothing stores."""
    probe_map, items_map = {}, {}
    for source in sources:
        for specs in specs_list:
            probes_, items_ = _key_maps(source, specs, judge_model)
            probe_map.update(probes_)
            items_map.update(items_)
    return probe_map, items_map


def _key_maps(out, specs: dict, judge_model: str) -> tuple:
    out = Path(out)
    run = arms.read_json(out / "run.json")
    fids = [f for f in run["fixtures"] if f in specs]
    probe_map, items_map = {}, {}
    for a in run["arms"]:
        for f in fids:
            for session in score.load_probe_sessions(out, a, f).values():
                for p in specs[f]["probes"]:
                    answer = (session.get("answers") or {}).get(p["id"])
                    if answer is None or score._is_unknown(answer):
                        continue
                    case = score.probe_case(p, answer)
                    probe_map[_old_key("probe", case)] = score.answer_key(case, judge_model)
    ids = sorted(fids)

    def add_items(case):
        items_map[_old_key("items", case)] = score.items_key(case, judge_model)
    for k, fid in enumerate(ids):  # the sanity controls: the oracle diff against the fixture's own items and the next one's
        added = arms.added_lines(arms.static_text("floor", specs[fid]), arms.static_text("oracle", specs[fid]))
        add_items(score.items_case(fid, specs[fid], added))
        add_items(score.items_case(fid, specs[ids[(k + 1) % len(ids)]], added))
    for a in run["arms"]:
        if a == "floor":
            continue
        for f in fids:
            floor_text = arms.static_text("floor", specs[f])
            for seed in range(1, int(run["seeds"]) + 1):
                text = score.load_arm_text(out, a, f, seed)
                added = arms.added_lines(floor_text, text) if text is not None else []
                if added:
                    add_items(score.items_case(f, specs[f], added))
    return probe_map, items_map


def convert(labels_path, sources: list, specs_list: list, judge_model: str) -> dict:
    """The labels.json content at labels_path with every legacy key replaced by its current key."""
    data = arms.read_json(labels_path)
    probe_map, items_map = key_maps(sources, specs_list, judge_model)
    current_probe, current_items = set(probe_map.values()), set(items_map.values())

    def move(key, mapping, current):
        return mapping.get(key, key if key in current else None)
    new = {"probe": {}, "items": {}}
    for entry_key, entry in data["probe"].items():
        value = entry.get("value")
        if isinstance(value, str):  # the one-call-per-answer form: the entry key is the answer key
            key = move(entry_key, probe_map, current_probe)
            if key is not None:
                new["probe"][key] = entry
            continue
        moved = {move(k, probe_map, current_probe): v for k, v in value.items()}
        moved.pop(None, None)
        if not moved:
            continue
        base = arms.content_key("probe-batch", sorted(moved), judge_model)
        key, n = base, 2
        while key in new["probe"]:
            key, n = f"{base}-{n}", n + 1
        new["probe"][key] = dict(entry, value=moved)
    for key, entry in data["items"].items():
        moved_key = move(key, items_map, current_items)
        if moved_key is not None:
            new["items"][moved_key] = entry
    return new


def load_specs(root) -> dict:
    """{fixture id: spec} for every spec under root, unvalidated: only the keys of the cases are needed."""
    return {p.parent.name: fixtures.load_spec(p) for p in sorted(Path(root).glob("*/spec.json"))}
