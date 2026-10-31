"""The human audit of EXP-0001 (README P:293-306), offline: the frame of judged baseline answers, the balanced draw, the
blinded export with its key, Cohen's kappa against the owner's labels, and the judge-minus-person difference (P:159).

Nothing here makes a model call. The draw seed is `seal.AUDIT_SEED`, written into the seal before any draw; the
bootstrap seed is the run's `rng_seed` (P:471). Shares, cell allocations and rounding are done in integers.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import sys
from pathlib import Path

import arms
import claude_run
import fixtures
import score
import seal
import stats

LABELS = ("correct", "incorrect", "abstained")
TYPES = ("single-fact", "knowledge-update", "temporal")
SAMPLE_SIZE = 100
BASE_SHARES = {"correct": 33, "incorrect": 34, "abstained": 33}  # P:295-297: 34, 33, 33 with the extra on incorrect
MIN_PER_CELL = 5  # P:298
KAPPA_BAR = 0.6  # P:306
BOOTSTRAP_B = 10000  # P:471
ITEMS_NAME, OWNER_NAME, KEY_NAME, AUDIT_NAME = "items.json", "owner-labels.json", "key.json", "audit.json"


# --------------------------------------------------------------------------
# The frame, the shares and the draw
# --------------------------------------------------------------------------

def frame(results_dir, specs, scores, run) -> list:
    """The baseline answers the judge labelled in the final scoring (P:294), sorted by id.

    One item per answer: {"id", "fixture", "seed", "probe", "type", "label", "case", "scorable"}. Out of the frame:
    abstention probes, excluded probes, a cell listed in run["excluded_cells"] for the baseline arm, an answer that is
    missing or parses as UNKNOWN (it never reaches the judge), and an answer whose label is missing or not one of LABELS.
    `case` is what the judge saw: {"question", "gold", "stale", "answer"}.
    """
    out_cells = {(e.get("fixture"), e.get("seed")) for e in run.get("excluded_cells", []) if e.get("arm") == "baseline"}
    items = []
    for fid in run["fixtures"]:
        if fid not in specs:
            continue
        spec = specs[fid]
        by_seed = scores["labels"]["baseline"].get(fid, {})
        sessions = score.load_probe_sessions(results_dir, "baseline", fid)
        scorable = set(scores["scorable"].get(fid, []))
        for seed_text in by_seed:
            seed = int(seed_text)
            if (fid, seed) in out_cells or seed not in sessions:
                continue
            answers = sessions[seed].get("answers") or {}
            for probe in fixtures.active_probes(spec):
                if probe["type"] == "abstention":
                    continue
                answer = answers.get(probe["id"])
                if answer is None or score._is_unknown(answer):
                    continue
                label = by_seed[seed_text].get(probe["id"])
                if label not in LABELS:
                    continue
                if probe["type"] not in TYPES:
                    raise ValueError(f"probe {probe['id']} has a type outside {', '.join(TYPES)}: {probe['type']}")
                case = {k: v for k, v in score.probe_case(probe, answer).items() if k != "id"}
                items.append({"id": f"{fid}/{seed}/{probe['id']}", "fixture": fid, "seed": seed, "probe": probe["id"],
                              "type": probe["type"], "label": label, "case": case, "scorable": probe["id"] in scorable})
    return sorted(items, key=lambda item: item["id"])


def label_shares(sizes: dict) -> dict:
    """How many answers to draw from each judge label (P:295-297), given each label's frame count.

    Start from BASE_SHARES. A label with fewer answers than its share is fixed at its size; the shortfall of the labels
    fixed in a pass goes to the labels still open (all to one, or split equally between two, an odd answer to
    `incorrect` when it is open, else to `correct`). Repeat until no label is short. When all three are fixed,
    everything is taken (a frame under 100 is allowed; its n is reported).

    The final clause ("else to `correct`") is the XO's default, to be confirmed: it covers an `incorrect` that is itself
    short, which P:297 does not reach.
    """
    shares, fixed = dict(BASE_SHARES), set()
    while True:
        short = [label for label in LABELS if label not in fixed and sizes.get(label, 0) < shares[label]]
        if not short:
            return shares
        shortfall = 0
        for label in short:
            shortfall += shares[label] - sizes.get(label, 0)
            shares[label] = sizes.get(label, 0)
            fixed.add(label)
        open_labels = [label for label in LABELS if label not in fixed]
        if not open_labels:
            return shares
        if len(open_labels) == 1:
            shares[open_labels[0]] += shortfall
        else:
            half, odd = divmod(shortfall, 2)
            for label in open_labels:
                shares[label] += half
            shares["incorrect" if "incorrect" in open_labels else "correct"] += odd


def _spread(extra: int, open_types: list, sizes: dict) -> dict:
    """extra units over the open cells in proportion to cell size: floors, then the leftover to the largest remainders
    (ties in TYPES order). Integers only."""
    total = sum(sizes[t] for t in open_types)
    if total <= 0:
        raise ValueError("no open cell can take the remaining answers")
    added = {t: extra * sizes[t] // total for t in open_types}
    leftover = extra - sum(added.values())
    for t in sorted(open_types, key=lambda t: (-(extra * sizes[t] % total), TYPES.index(t)))[:leftover]:
        added[t] += 1
    return added


def cell_allocation(share: int, cell_sizes: dict) -> dict:
    """How many answers to draw from each probe-type cell of one label (P:298).

    Every cell first gets min(MIN_PER_CELL, size). The rest is spread over the open cells in proportion to cell size, by
    largest remainder; a cell at or above its size is capped and closed, and its excess is spread again over the cells
    still open, until none is left. A share at or above the label's count takes everything.
    """
    sizes = {t: cell_sizes.get(t, 0) for t in TYPES}
    if share >= sum(sizes.values()):
        return sizes
    alloc = {t: min(MIN_PER_CELL, sizes[t]) for t in TYPES}
    extra = share - sum(alloc.values())
    if extra < 0:
        raise ValueError("the share is below the minimum for the cells")
    open_types = list(TYPES)
    while extra > 0:
        added = _spread(extra, open_types, sizes)
        extra = 0
        for t in list(open_types):
            alloc[t] += added[t]
            if alloc[t] >= sizes[t]:
                extra += alloc[t] - sizes[t]
                alloc[t] = sizes[t]
                open_types.remove(t)
    return alloc


def _plan(items) -> tuple:
    """(frame_sizes, shares, allocation) of a frame: {label: {type: n}}, {label: n}, {label: {type: n}}."""
    frame_sizes = {label: {t: 0 for t in TYPES} for label in LABELS}
    for item in items:
        frame_sizes[item["label"]][item["type"]] += 1
    shares = label_shares({label: sum(frame_sizes[label].values()) for label in LABELS})
    allocation = {label: cell_allocation(shares[label], frame_sizes[label]) for label in LABELS}
    return frame_sizes, shares, allocation


def draw(items) -> list:
    """The ids drawn (P:299): per label in LABELS order, per type in TYPES order, `random.Random(SEED).sample` of the
    cell's sorted ids (a fresh generator for every cell), each cell's ids in sample's own order."""
    _, _, allocation = _plan(items)
    drawn = []
    for label in LABELS:
        for t in TYPES:
            ids = sorted(item["id"] for item in items if item["label"] == label and item["type"] == t)
            drawn += random.Random(seal.AUDIT_SEED).sample(ids, allocation[label][t])
    return drawn


def blind_order(drawn: list) -> list:
    """A copy of drawn shuffled by random.Random(AUDIT_SEED).shuffle (P:301)."""
    order = list(drawn)
    random.Random(seal.AUDIT_SEED).shuffle(order)
    return order


def names_more_than_one(answer, gold, stale):
    """An answer names more than one value when its probe has at least one stale answer and the answer contains, ignoring letter case, both the gold answer and at least one stale answer; probes without stale answers are not counted.

    Returns None when the probe has no non-empty stale answer (the rule does not apply: a stated limit), else a bool.
    No other normalisation: no whitespace or punctuation handling.
    """
    stale = [s for s in stale or [] if isinstance(s, str) and s]
    if not stale:
        return None
    text = answer.casefold()
    return gold.casefold() in text and any(s.casefold() in text for s in stale)


# --------------------------------------------------------------------------
# The export
# --------------------------------------------------------------------------

def _emit(stdout, text) -> None:
    print(text, file=stdout or sys.stdout)


def export(results_dir, seal_dir, fixtures_root, out, stdout=None) -> int:
    """Write the blinded sample, the owner's labels file and the key (P:299-304). Exit 2: --out refused or existing.
    Exit 1: the seal does not verify, the audit seed is wrong, the results are not the final scoring of the sealed set.
    Nothing is written on a refusal. The printed line holds no primary figure, kappa or label (P:305)."""
    reason = claude_run.out_refusal(str(out))
    if reason:
        _emit(stdout, f"audit-export: REFUSED {reason}")
        return 2
    out = Path(out)
    if os.path.lexists(out):
        _emit(stdout, "audit-export: REFUSED --out already exists")
        return 2
    found = seal.verify(seal_dir, fixtures_root, results_dir)
    if found:
        _emit(stdout, "audit-export: STOP the seal does not verify (outcome not determined): " + ", ".join(found))
        return 1
    try:
        sealed = json.loads((Path(seal_dir) / seal.SEAL_NAME).read_text(encoding="utf-8"))
        seed_ok = type(sealed.get("audit_seed")) is int and sealed["audit_seed"] == seal.AUDIT_SEED
    except (OSError, ValueError, AttributeError):
        seed_ok = False
    if not seed_ok:
        _emit(stdout, "audit-export: STOP the seal's audit_seed is not the audit seed (outcome not determined)")
        return 1
    try:
        run = arms.read_json(Path(results_dir) / "run.json")
        if not seal.is_final(run):
            _emit(stdout, "audit-export: STOP the results are not the final scoring (held-out results with a baseline arm)")
            return 1
        scores = arms.read_json(Path(results_dir) / "scores.json")
        if not isinstance(scores.get("labels"), dict):
            _emit(stdout, "audit-export: STOP scores.json has no labels block")
            return 1
        diff = seal.final_record_diff(seal_dir, scores)
        if diff:
            _emit(stdout, "audit-export: STOP the scores are not the final scoring of the sealed set: " + "; ".join(diff))
            return 1
        items = frame(results_dir, score.load_specs(fixtures_root, run["fixtures"]), scores, run)
        frame_sizes, shares, allocation = _plan(items)
        drawn = draw(items)
        order = blind_order(drawn)
        by_id = {item["id"]: item for item in items}
        bootstrap_seed = run["rng_seed"]
    except (OSError, ValueError, KeyError, TypeError, AttributeError, score.ScoreAbort) as exc:
        _emit(stdout, f"audit-export: STOP {type(exc).__name__}: {exc} (outcome not determined)")
        return 1
    shown = {"version": 1, "items": []}
    owner = {"version": 1, "labels": {}}
    key = {"version": 1, "audit_seed": seal.AUDIT_SEED, "bootstrap_seed": bootstrap_seed, "frame_sizes": frame_sizes,
           "shares": shares, "allocation": allocation, "items": {}}
    for k, item_id in enumerate(order, start=1):
        item = by_id[item_id]
        case = item["case"]
        shown["items"].append({"item": k, "question": case["question"], "gold": case["gold"], "stale": case["stale"],
                               "answer": case["answer"]})
        owner["labels"][str(k)] = {"label": None, "names_more_than_one_value": None}
        key["items"][str(k)] = {"id": item_id, "fixture": item["fixture"], "seed": item["seed"], "probe": item["probe"],
                                "type": item["type"], "judge_label": item["label"], "scorable": item["scorable"],
                                "mechanical_multi": names_more_than_one(case["answer"], case["gold"], case["stale"])}
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.mkdir()  # exclusive: an existing folder raises
    except FileExistsError:
        _emit(stdout, "audit-export: REFUSED --out already exists")
        return 2
    try:
        for name, data in ((ITEMS_NAME, shown), (OWNER_NAME, owner), (KEY_NAME, key)):
            claude_run.atomic_write(out / name, seal._dump(data))
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)
        raise
    counts = {label: sum(allocation[label].values()) for label in LABELS}
    _emit(stdout, f"audit-export: wrote {ITEMS_NAME}, {OWNER_NAME} and {KEY_NAME} (frame {len(items)}; sample "
                  f"{len(drawn)}: correct {counts['correct']}, incorrect {counts['incorrect']}, abstained {counts['abstained']})")
    return 0


# --------------------------------------------------------------------------
# The scoring of the owner's labels
# --------------------------------------------------------------------------

def _agreement(pairs) -> float:
    return stats._r(sum(1 for j, o in pairs if j == o) / len(pairs))


def summarise(key: dict, owner: dict) -> dict:
    """Kappa, agreement, kappa by type, the judge-minus-person difference and the multi-value check (P:306, P:159).

    Raises ValueError unless the owner's labels hold exactly the key's item numbers, each label is one of LABELS and each
    names_more_than_one_value is a JSON bool.
    """
    try:
        key_items, owner_labels = key["items"], owner["labels"]
        if set(key_items) != set(owner_labels):
            raise ValueError("the owner's labels and the key do not hold the same item numbers")
        numbers = sorted(key_items, key=int)
        rows = []
        for k in numbers:
            given = owner_labels[k]
            label, multi = given["label"], given["names_more_than_one_value"]
            if label not in LABELS:
                raise ValueError(f"item {k}: the label is not one of {', '.join(LABELS)}")
            if not isinstance(multi, bool):
                raise ValueError(f"item {k}: names_more_than_one_value is not true or false")
            rows.append((k, key_items[k], label, multi))
        frame_sizes = key["frame_sizes"]
        bootstrap_seed = key["bootstrap_seed"]
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"malformed key or owner labels ({type(exc).__name__}: {exc})")
    if not rows:
        raise ValueError("no items")
    judge = [info["judge_label"] for _, info, _, _ in rows]
    person = [label for _, _, label, _ in rows]
    by_type = {}
    for t in TYPES:
        pairs = [(info["judge_label"], label) for _, info, label, _ in rows if info["type"] == t]
        if pairs:
            by_type[t] = {"n": len(pairs), "agreement": _agreement(pairs),
                          "kappa": stats.cohen_kappa([j for j, _ in pairs], [o for _, o in pairs])}
    sampled = {label: judge.count(label) for label in LABELS}
    weight = {label: sum(frame_sizes[label].values()) / sampled[label] for label in LABELS if sampled[label]}
    per_fixture = {}
    for _, info, label, _ in rows:
        if info["scorable"]:
            per_fixture.setdefault(info["fixture"], []).append((weight[info["judge_label"]], info["judge_label"], label))
    diffs = []
    for fid in sorted(per_fixture):
        total = sum(w for w, _, _ in per_fixture[fid])
        j = sum(w for w, judged, _ in per_fixture[fid] if judged == "correct") / total
        p = sum(w for w, _, owned in per_fixture[fid] if owned == "correct") / total
        diffs.append(j - p)
    if diffs:
        lo, hi = stats.bootstrap_ci(diffs, b=BOOTSTRAP_B, alpha=0.05, rng_seed=bootstrap_seed)
        minus = {"value": stats._r(stats.mean(diffs)), "ci": [lo, hi], "fixtures": len(diffs), "b": BOOTSTRAP_B,
                 "seed": bootstrap_seed}
    else:
        minus = {"value": None, "ci": [None, None], "fixtures": 0, "b": BOOTSTRAP_B, "seed": bootstrap_seed}
    mech = [(info["mechanical_multi"], multi) for _, info, _, multi in rows if info["mechanical_multi"] is not None]
    multi_value = {"n": len(mech), "agreement": _agreement(mech) if mech else None,
                   "mechanical_true": sum(1 for m, _ in mech if m), "owner_true": sum(1 for _, o in mech if o)}
    return {"n": len(rows), "kappa": stats.cohen_kappa(judge, person), "agreement": _agreement(list(zip(judge, person))),
            "kappa_bar": KAPPA_BAR, "by_type": by_type, "judge_minus_person": minus, "multi_value": multi_value}


def score_audit(key_path, labels_path, out, stdout=None) -> int:
    """Read the key and the owner's labels, write audit.json (exit 0). Exit 2: --out refused or existing. Exit 1: an
    unreadable file or a labels file that does not match the key (the reason is printed)."""
    reason = claude_run.out_refusal(str(out))
    if reason:
        _emit(stdout, f"audit-score: REFUSED {reason}")
        return 2
    out = Path(out)
    if os.path.lexists(out):
        _emit(stdout, "audit-score: REFUSED --out already exists")
        return 2
    try:
        key = json.loads(Path(key_path).read_text(encoding="utf-8"))
        owner = json.loads(Path(labels_path).read_text(encoding="utf-8"))
        summary = summarise(key, owner)
    except (OSError, ValueError) as exc:
        _emit(stdout, f"audit-score: STOP {type(exc).__name__}: {exc}")
        return 1
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.mkdir()
    except FileExistsError:
        _emit(stdout, "audit-score: REFUSED --out already exists")
        return 2
    try:
        claude_run.atomic_write(out / AUDIT_NAME, seal._dump({"version": 1, **summary}))
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)
        raise
    _emit(stdout, f"audit-score: n {summary['n']}, kappa {summary['kappa']:.3f} (bar {KAPPA_BAR}); wrote {AUDIT_NAME}")
    return 0
