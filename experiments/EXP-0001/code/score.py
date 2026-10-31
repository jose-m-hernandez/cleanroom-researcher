"""Scoring: judge labels in, per fixture x arm x seed metrics out.

Reads a results directory written by `run` (run.json, arms/, probes/) plus the fixture specs, labels every
probe answer and every added memory line through the judge, and writes scores.json. Probe answers are labelled
one judge call per probe session (all of the session's answers that still need a label), matched back by probe id.
Labels are cached in labels.json by input content, the judge model and a hash of the judge prompt and schema files,
so a second pass makes no repeated call and a different judge never reuses a label. A probe whose label is missing or
malformed in a reply is excluded and counted, is left out of its cell's numerator and denominator (never scored as
incorrect), and is the only one asked again on the next pass; a failed isolation proof aborts the pass and keeps none
of that call's labels.

Primary metric: probe accuracy on the fixture's scorable probes. A probe is scorable when the floor answered it
correctly in none of its seeds and the oracle answered it correctly in a majority of its seeds; abstention probes
are never in the primary. A probe with a missing floor or oracle label is dropped as `label_missing`.
Floor and oracle must hold exactly seeds 1..N, and every list comes from `fixtures.active_probes`.
The judge sanity controls run first on every pass and abort it on failure.
The primary is also split by each probe's `code_answers` tag (whether a file in the repo other than CLAUDE.md states
the answer): primary_code and primary_nocode are accuracy over the scorable probes tagged true and false.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "runner"))
import arms  # noqa: E402
import claude_run  # noqa: E402
import fixtures  # noqa: E402
import judge  # noqa: E402
import stats  # noqa: E402

ORACLE_RECALL_MIN = 0.95
MISMATCH_RECALL_MAX = 0.05
ABSTENTION_GOLD = judge.ABSTENTION_GOLD
METRICS = ("primary", "primary_code", "primary_nocode", "primary_all", "recall", "precision", "placement", "false_answer", "d_lines", "d_tokens")
JUDGE_LABELS = ("correct", "incorrect", "abstained")
LOCKED_ARMS = ("floor", "oracle")  # in a final pass these arms' labels come only from the sealed labels file
FINAL_LABELS_NAME = "labels-final.json"
GALLERY_MAX = 10
ANSWER_CHARS = 160
SEED_RE = re.compile(r"^s(\d+)\.json$")


class ScoreAbort(Exception):
    """Scoring cannot continue; the message is a short label. Labels already paid for stay cached."""


def _r(x):
    return None if x is None else stats._r(x)


def _is_unknown(answer) -> bool:
    return isinstance(answer, str) and re.sub(r"[^a-z]", "", answer.casefold()) == "unknown"


# --------------------------------------------------------------------------
# Label cache and judge calls
# --------------------------------------------------------------------------

def answer_key(case, judge_model) -> str:
    """The cache key of one probe answer: its content, the judge that labels it and the judge prompt files."""
    return arms.content_key("probe", {k: v for k, v in case.items() if k != "id"}, judge_model, judge.prompt_hash("probe"))


def items_key(case, judge_model) -> str:
    """The cache key of one items case, bound the same way."""
    return arms.content_key("items", {k: v for k, v in case.items() if k != "id"}, judge_model, judge.prompt_hash("items"))


class Labeller:
    """Judge calls with a content-keyed cache in <out>/labels.json, each persisted as it returns.

    Items labels are cached one per call. Probe labels are cached per answer key, but stored one entry per
    batched call under "probe": {"value": {answer key: label}, "probes", "excluded", cost and tokens once}.
    Entries from the one-call-per-answer form (value is a label string, key is the answer key) still load.

    Three modes. "normal" is the pass described above. "final" is a scoring pass after the seal (P:276-282): the
    sealed labels.json is read and never opened for writing, new labels go to labels-final.json, and a floor or
    oracle label missing from the sealed file is never asked of the judge again (it comes back None). "replay" reads
    labels.json, makes no judge call and writes nothing: the seal uses it to rebuild the scorable record.
    """

    def __init__(self, out, cfg, environ, label_probe, label_items, model=None, max_calls=None, mode="normal"):
        if mode not in ("normal", "final", "replay"):
            raise ScoreAbort("labeller mode invalid")
        self.mode = mode
        self.path = Path(out) / ("labels.json" if mode == "normal" else FINAL_LABELS_NAME)
        self.cfg, self.environ, self.model = cfg, environ, model
        self._probe, self._items = label_probe, label_items
        self.max_calls = int(cfg.get("score", {}).get("max_judge_calls", 0) if max_calls is None else max_calls)
        self.sealed = {"probe": {}, "items": {}}
        if mode != "normal":
            self.sealed = self._load(Path(out) / "labels.json", required=True)
        self.data = {"probe": {}, "items": {}}
        if mode != "replay" and self.path.is_file():
            self.data = self._load(self.path)
        self.known = self._index_probe_labels(self.sealed["probe"])
        self.known.update(self._index_probe_labels(self.data["probe"]))
        self.calls = 0
        self.cached = 0
        self.invalid = 0

    @staticmethod
    def _load(path, required=False) -> dict:
        if not path.is_file():
            if required:
                raise ScoreAbort("labels file unreadable")
            return {"probe": {}, "items": {}}
        data = arms.read_json(path)
        if not (isinstance(data, dict) and isinstance(data.get("probe"), dict) and isinstance(data.get("items"), dict)):
            raise ScoreAbort("labels file unreadable")
        return data

    def _no_call(self, arm) -> bool:
        """Whether a label missing from the cache must not be asked of the judge (replay, or a locked arm after the seal)."""
        return self.mode == "replay" or (self.mode == "final" and arm in LOCKED_ARMS)

    def _index_probe_labels(self, entries) -> dict:
        """{answer key: label} from both entry shapes; anything else is unreadable."""
        known = {}
        for key, entry in entries.items():
            value = entry.get("value") if isinstance(entry, dict) else None
            if isinstance(value, str):
                known[key] = value
            elif isinstance(value, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
                known.update(value)
            else:
                raise ScoreAbort("labels file unreadable")
        return known

    def _entry(self, rec, **fields) -> dict:
        usage = rec.get("usage")
        return dict(fields, cost_usd=rec.get("cost_usd"), wall_s=rec.get("wall_s"), input_tokens_total=arms.input_total(usage),
                    output_tokens=usage.get("output_tokens") if isinstance(usage, dict) else None)

    def _take(self, kind, key, case, fn, arm=None):
        entry = self.data[kind].get(key) or self.sealed[kind].get(key)
        if entry is not None:
            self.cached += 1
            return entry["value"]
        if self._no_call(arm):
            return None
        value, rec = fn(self.cfg, case, self.environ, self.model, phase="evaluation", counter="score", max_calls=self.max_calls)
        self.calls += 1
        proof = rec.get("proof") or {}
        if not proof.get("ok"):
            raise ScoreAbort("judge isolation failed: " + ",".join(proof.get("failures") or ["no_proof"]))
        if value is None:
            self.invalid += 1
            return None
        self.data[kind][key] = self._entry(rec, value=value)
        arms.write_json(self.path, self.data)
        return value

    @property
    def judge_model(self) -> str:
        """The judge that labels: the override, else the config pin. Every cache key carries it."""
        return self.model or self.cfg["judge"]["model"]

    def answer_key(self, case) -> str:
        return answer_key(case, self.judge_model)

    def probes(self, cases, session_id="batch", arm=None) -> dict:
        """Labels for one probe session: {probe id: label or None}. cases are probe cases with unique ids.

        Answers whose key is already labelled are cache hits. The rest go to the judge in one call (each distinct
        answer once; a repeat inside the session shares its label). A failed proof aborts; a probe left without a
        label is counted as invalid, is not cached, and comes back None.
        """
        keys = {c["id"]: self.answer_key(c) for c in cases}
        result, sent, first, repeats = {}, [], set(), []
        for c in cases:
            key = keys[c["id"]]
            if key in self.known:
                self.cached += 1
                result[c["id"]] = self.known[key]
            elif key in first:
                repeats.append(c)
            else:
                first.add(key)
                sent.append(c)
        if not sent:
            return result
        if self._no_call(arm):
            for c in sent + repeats:
                result[c["id"]] = None
            return result
        labels, rec = self._probe(self.cfg, {"id": session_id, "probes": sent}, self.environ, self.model,
                                  phase="evaluation", counter="score", max_calls=self.max_calls)
        self.calls += 1
        proof = rec.get("proof") or {}
        if not proof.get("ok"):
            raise ScoreAbort("judge isolation failed: " + ",".join(proof.get("failures") or ["no_proof"]))
        labels = labels or {}
        survived = {keys[c["id"]]: labels[c["id"]] for c in sent if labels.get(c["id"]) in JUDGE_LABELS}
        excluded = len(sent) - len(survived)
        self.invalid += excluded
        base = arms.content_key("probe-batch", sorted(keys[c["id"]] for c in sent), self.judge_model)
        entry_key, n = base, 2
        while entry_key in self.data["probe"]:  # never overwrite an entry
            entry_key, n = f"{base}-{n}", n + 1
        self.data["probe"][entry_key] = self._entry(rec, value=survived, probes=len(sent), excluded=excluded)
        self.known.update(survived)
        arms.write_json(self.path, self.data)
        for c in sent:
            result[c["id"]] = survived.get(keys[c["id"]])
        for c in repeats:
            result[c["id"]] = survived.get(keys[c["id"]])
            self.cached += 1 if result[c["id"]] is not None else 0
        return result

    def items(self, case, arm=None):
        return self._take("items", items_key(case, self.judge_model), case, self._items, arm)

    def call_rows(self) -> dict:
        """Cost and tokens of every cached label, for the cost table."""
        rows = [r for d in (self.sealed, self.data) for kind in ("probe", "items") for r in d[kind].values()]

        def total(key):
            return _r(sum(r[key] for r in rows if isinstance(r.get(key), (int, float))))
        return {"n": len(rows), "cost_usd": total("cost_usd"), "input_tokens": total("input_tokens_total"),
                "output_tokens": total("output_tokens"), "wall_s": total("wall_s")}


# --------------------------------------------------------------------------
# Cases
# --------------------------------------------------------------------------

def items_case(case_id, spec: dict, added: list) -> dict:
    """The judge's items case: spec's needed and not-needed items against the added lines."""
    needed = [{"id": i["id"], "fact": i["statement"], "heading": " / ".join([i["oracle_section"]] + list(i["suits"]))}
              for i in spec["items"] if i["needed"]]
    not_needed = [{"id": i["id"], "fact": i["statement"]} for i in spec["items"] if not i["needed"]]
    return {"id": case_id, "needed": needed, "not_needed": not_needed,
            "lines": [(heading or "(none)", text) for heading, text in added]}


def probe_case(spec_probe: dict, answer: str) -> dict:
    abstain = spec_probe["type"] == "abstention"
    return {"id": spec_probe["id"], "question": spec_probe["question"],
            "gold": ABSTENTION_GOLD if abstain else spec_probe["answer"], "stale": list(spec_probe["reject"]),
            "answer": answer}


def item_cell(spec: dict, added: list, lines) -> dict:
    """Recall, precision and placement of one arm text, from the judge's line labels.

    The cell is None unless the label rows number the added lines exactly 1..len(added), each once: a missing label
    list, a skipped line, a repeated line or a line past the end gives no cell, never a partial one.
    """
    numbers = [row.get("line") for row in lines or [] if isinstance(row, dict)]
    rows_ok = (lines is not None and len(numbers) == len(lines)
               and all(isinstance(n, int) and not isinstance(n, bool) for n in numbers)
               and sorted(numbers) == list(range(1, len(added) + 1)))
    if not rows_ok:
        return {"recall": None, "precision": None, "placement": None, "matched": []}
    needed = {i["id"] for i in spec["items"] if i["needed"]}
    by_line = {row["line"]: row for row in lines}
    matched, needed_lines = {}, 0
    for n in range(1, len(added) + 1):
        row = by_line.get(n)
        if row and row["kind"] == "needed" and row["item_id"] in needed:
            needed_lines += 1
            matched[row["item_id"]] = matched.get(row["item_id"], False) or bool(row["placed_ok"])
    return {"recall": _r(len(matched) / len(needed)),
            "precision": _r(needed_lines / len(added)) if added else None,
            "placement": _r(sum(matched.values()) / len(matched)) if matched else None,
            "matched": sorted(matched)}


# --------------------------------------------------------------------------
# Loading a results directory
# --------------------------------------------------------------------------

def load_specs(fixtures_root, ids) -> dict:
    """{fixture id: spec} from <fixtures_root>/<id>/spec.json."""
    specs = {}
    for fid in ids:
        try:
            spec = fixtures.load_spec(Path(fixtures_root) / fid / "spec.json")
        except (OSError, ValueError):
            raise ScoreAbort(f"spec not found: {fid}")
        if fixtures.validate_spec(spec, directory_name=fid):
            raise ScoreAbort(f"spec invalid: {fid}")
        specs[fid] = spec
    return specs


def load_probe_sessions(out, arm, fid) -> dict:
    """{seed: probes result} for the sessions present on disk."""
    folder = Path(out) / "probes" / arm / fid
    found = {}
    for path in sorted(folder.glob("s*.json")) if folder.is_dir() else []:
        m = SEED_RE.match(path.name)
        if m:
            found[int(m.group(1))] = arms.read_json(path)
    return found


def load_arm_text(out, arm, fid, seed):
    path = arms.arm_text_path(out, arm, fid, seed)
    return path.read_text(encoding="utf-8") if path.is_file() else None


# --------------------------------------------------------------------------
# Sanity controls
# --------------------------------------------------------------------------

def sanity_controls(specs: dict, labeller: Labeller) -> dict:
    """Oracle recall (>= 0.95) and the cyclic mismatch control (<= 0.05), each a mean over fixtures. Raises on failure."""
    ids = sorted(specs)
    if len(ids) < 2:
        raise ScoreAbort("sanity controls need at least 2 fixtures")
    oracle_recalls, mismatch_recalls = [], []
    for k, fid in enumerate(ids):
        spec, nxt = specs[fid], specs[ids[(k + 1) % len(ids)]]
        added = arms.added_lines(arms.static_text("floor", spec), arms.static_text("oracle", spec))
        own = item_cell(spec, added, labeller.items(items_case(fid, spec, added), arm="oracle"))
        other = item_cell(nxt, added, labeller.items(items_case(fid, nxt, added), arm="oracle"))
        if own["recall"] is None or other["recall"] is None:
            raise ScoreAbort("sanity control labels invalid")
        oracle_recalls.append(own["recall"])
        mismatch_recalls.append(other["recall"])
    result = {"oracle_recall": _r(stats.mean(oracle_recalls)), "mismatch_recall": _r(stats.mean(mismatch_recalls)),
              "oracle_recall_min": ORACLE_RECALL_MIN, "mismatch_recall_max": MISMATCH_RECALL_MAX}
    result["ok"] = result["oracle_recall"] >= ORACLE_RECALL_MIN and result["mismatch_recall"] <= MISMATCH_RECALL_MAX
    if not result["ok"]:
        raise ScoreAbort("judge sanity controls failed: oracle recall %.3f (min %.2f), mismatch recall %.3f (max %.2f)"
                         % (result["oracle_recall"], ORACLE_RECALL_MIN, result["mismatch_recall"], MISMATCH_RECALL_MAX))
    return result


# --------------------------------------------------------------------------
# Probe filter
# --------------------------------------------------------------------------

def scorable_probes(spec: dict, labels: dict, floor_seeds: list, oracle_seeds: list) -> dict:
    """The floor-fail filter and the oracle-majority filter for one fixture.

    labels[(arm, seed, probe id)] is a judge label or None. A probe is scorable when the floor answered it
    correctly in 0 of its seeds and the oracle answered it correctly in a majority of its seeds (more than
    half). A probe with a missing (None) floor or oracle label is dropped as `label_missing` and no other reason is
    tested. Abstention probes never qualify, and neither does a probe the spec marks `excluded` (abstention or not): it
    is left out of every list but "excluded". The lists come from fixtures.active_probes, the one exclusion rule.
    Returns {"scorable", "non_abstention", "dropped": {pid: [reasons]}, "excluded": [pid]}.
    """
    active = fixtures.active_probes(spec)
    kept = {p["id"] for p in active}
    excluded = [p["id"] for p in spec["probes"] if p["id"] not in kept]
    non_abs = [p["id"] for p in active if p["type"] != "abstention"]
    scorable, dropped = [], {}
    for pid in non_abs:
        if any(labels.get((arm, s, pid)) is None for arm, seeds in (("floor", floor_seeds), ("oracle", oracle_seeds)) for s in seeds):
            dropped[pid] = ["label_missing"]
            continue
        floor_correct = sum(1 for s in floor_seeds if labels.get(("floor", s, pid)) == "correct")
        oracle_correct = sum(1 for s in oracle_seeds if labels.get(("oracle", s, pid)) == "correct")
        reasons = []
        if floor_correct > 0:
            reasons.append("floor_correct")
        if not 2 * oracle_correct > len(oracle_seeds):
            reasons.append("oracle_minority")
        if reasons:
            dropped[pid] = reasons
        else:
            scorable.append(pid)
    return {"scorable": scorable, "non_abstention": non_abs, "dropped": dropped, "excluded": excluded}


# --------------------------------------------------------------------------
# The scoring pass
# --------------------------------------------------------------------------

def blind_case_id(fid: str, seed, cases) -> str:
    """Opaque id for a judged batch: 10 hex of sha256 over fixture, seed and the sorted probe ids. Never the arm."""
    text = "\n".join([str(fid), str(seed)] + sorted(c["id"] for c in cases))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]


def score_run(out, specs: dict, cfg: dict, environ, label_probe=None, label_items=None, model=None, max_calls=None,
              mode="normal") -> dict:
    """Score a results directory. Returns the scores dict (also written to <out>/scores.json by write_scores).

    mode is the Labeller's ("normal", "final" after the seal, "replay" for the seal's own record).

    The dict carries a "labels" block, {arm: {fixture: {seed: {probe id: label}}}}: every probe of every session on disk,
    in spec order, with the label the judge gave it (None for a missing answer or label). The human audit draws from it.

    The dict also carries "multi_value", {"applicable": a, "count": k} (README P:160), over the baseline arm only: every
    seed with a session, every active probe whose type is not abstention. An answer is applicable when it is present,
    does not parse as UNKNOWN and audit.names_more_than_one is not None for it (its probe has a stale answer); count is
    how many applicable answers name more than one value. Both are 0 without a baseline arm.
    """
    import audit  # inside the function: audit imports score
    out = Path(out)
    run = arms.read_json(out / "run.json")
    fids = [f for f in run["fixtures"] if f in specs]
    arm_names = run["arms"]
    if "floor" not in arm_names or "oracle" not in arm_names:
        raise ScoreAbort("floor and oracle arms are required")
    labeller = Labeller(out, cfg, environ, label_probe or judge.label_probe, label_items or judge.label_items,
                        model, max_calls, mode)
    sanity = sanity_controls({f: specs[f] for f in fids}, labeller)

    # Probe labels for every session on disk.
    sessions = {(a, f): load_probe_sessions(out, a, f) for a in arm_names for f in fids}
    for f in fids:
        if not sessions[("floor", f)] or not sessions[("oracle", f)]:
            raise ScoreAbort(f"floor and oracle probe sessions are required: {f}")
    seeds_n = int(run["seeds"])
    for f in fids:
        for a in ("floor", "oracle"):
            if sorted(sessions[(a, f)]) != list(range(1, seeds_n + 1)):
                raise ScoreAbort(f"floor and oracle must hold exactly seeds 1..{seeds_n}: {f}")
    labels = {f: {} for f in fids}
    invalid_answers = 0
    for f in fids:
        for a in arm_names:
            for seed, session in sessions[(a, f)].items():
                judged = []  # the session's answers that need the judge, in probe order
                for p in specs[f]["probes"]:
                    answer = (session.get("answers") or {}).get(p["id"])
                    if answer is None:
                        label = None
                        invalid_answers += 1
                    elif _is_unknown(answer):
                        label = "abstained"
                    else:
                        judged.append(probe_case(p, answer))
                        continue
                    labels[f][(a, seed, p["id"])] = label
                for pid, label in labeller.probes(judged, blind_case_id(f, seed, judged), arm=a).items():
                    labels[f][(a, seed, pid)] = label

    per_seed = {a: {f: {} for f in fids} for a in arm_names}
    filters, probe_totals, preserve_info, scorable_split = {}, {}, {}, {}
    for f in fids:
        spec = specs[f]
        floor_seeds = sorted(sessions[("floor", f)])
        oracle_seeds = sorted(sessions[("oracle", f)])
        flt = scorable_probes(spec, labels[f], floor_seeds, oracle_seeds)
        filters[f] = flt
        tags = {p["id"]: p.get("code_answers") for p in spec["probes"]}
        split = {"code": [pid for pid in flt["scorable"] if tags.get(pid) is True],
                 "nocode": [pid for pid in flt["scorable"] if tags.get(pid) is False]}
        scorable_split[f] = {"code": len(split["code"]), "nocode": len(split["nocode"])}
        abstentions = [p["id"] for p in fixtures.active_probes(spec) if p["type"] == "abstention"]
        floor_text = arms.static_text("floor", spec)
        for a in arm_names:
            for seed in range(1, seeds_n + 1):
                session = sessions[(a, f)].get(seed)
                text = load_arm_text(out, a, f, seed)
                cell = {m: None for m in METRICS}
                if session is not None:
                    def lab(pid, a=a, seed=seed, f=f):
                        return labels[f].get((a, seed, pid))
                    # A probe without a label is left out of numerator and denominator, never counted as incorrect.
                    for metric, group in (("primary_all", flt["non_abstention"]), ("primary", flt["scorable"]),
                                          ("primary_code", split["code"]), ("primary_nocode", split["nocode"])):
                        labelled = [pid for pid in group if lab(pid) is not None]
                        if labelled:
                            cell[metric] = _r(sum(1 for pid in labelled if lab(pid) == "correct") / len(labelled))
                    answered = [pid for pid in abstentions if lab(pid) is not None]
                    if answered:  # against the neutral gold the judge may call an abstaining reply "correct": not false
                        cell["false_answer"] = _r(sum(1 for pid in answered if lab(pid) not in ("abstained", "correct")) / len(answered))
                    floor_session = sessions[("floor", f)].get(seed)
                    mine, base = session.get("input_tokens_total"), (floor_session or {}).get("input_tokens_total")
                    if mine is not None and base is not None:
                        cell["d_tokens"] = mine - base
                if text is not None:
                    cell["d_lines"] = arms.line_count(text) - arms.line_count(floor_text)
                    if a == "floor":
                        added = []
                        item = item_cell(spec, added, [])
                    else:
                        added = arms.added_lines(floor_text, text)
                        item = item_cell(spec, added, labeller.items(items_case(f, spec, added), arm=a) if added else [])
                    cell.update(recall=item["recall"], precision=item["precision"], placement=item["placement"])
                    cell["_matched"] = item["matched"]
                per_seed[a][f][str(seed)] = cell
    per_fixture = {a: {f: {m: stats.seed_mean([c[m] for c in per_seed[a][f].values()]) for m in METRICS} for f in fids}
                   for a in arm_names}

    # Preserve runs, calls and the cost table.
    calls = {"preserve": {}, "probe": {}}
    for a in arm_names:
        for f in fids:
            for seed, session in sessions[(a, f)].items():
                _add_call(calls["probe"], a, session)
            folder = out / "preserve" / a / f
            for path in sorted(folder.glob("s*.json")) if folder.is_dir() else []:
                data = arms.read_json(path)
                _add_call(calls["preserve"], a, data)
                info = preserve_info.setdefault(a, {"runs": 0, "changed": 0})
                info["runs"] += 1
                info["changed"] += 1 if data.get("changed") else 0
    calls["judge"] = {"judge": labeller.call_rows()}

    scorable_counts = {f: len(filters[f]["scorable"]) for f in fids}
    dropped = {f: filters[f]["dropped"] for f in fids}
    dropped_total = sum(len(d) for d in dropped.values())
    applicable = count = 0
    for f in fids:
        for session in sessions.get(("baseline", f), {}).values():
            for p in fixtures.active_probes(specs[f]):
                answer = (session.get("answers") or {}).get(p["id"])
                if p["type"] == "abstention" or not isinstance(answer, str) or _is_unknown(answer):
                    continue
                case = probe_case(p, answer)
                multi = audit.names_more_than_one(answer, case["gold"], case["stale"])
                if multi is not None:
                    applicable += 1
                    count += 1 if multi else 0
    scores = {
        "version": 1, "fixtures": fids, "arms": arm_names, "seeds": seeds_n, "judge_model": labeller.judge_model,
        "sanity": sanity, "scorable": {f: filters[f]["scorable"] for f in fids}, "scorable_counts": scorable_counts,
        "scorable_split": scorable_split, "excluded": {f: filters[f]["excluded"] for f in fids}, "dropped": dropped, "dropped_total": dropped_total,
        "dropped_floor": sum(1 for d in dropped.values() for r in d.values() if "floor_correct" in r),
        "dropped_oracle": sum(1 for d in dropped.values() for r in d.values() if "oracle_minority" in r),
        "non_abstention_total": sum(len(filters[f]["non_abstention"]) for f in fids),
        "per_seed": per_seed, "per_fixture": per_fixture, "preserve": preserve_info, "calls": calls,
        "invalid": {"probe_answers_missing": invalid_answers, "judge_labels_invalid": labeller.invalid},
        "judge": {"calls_made": labeller.calls, "cached_hits": labeller.cached},
        "gallery": _gallery(specs, fids, labels, sessions, per_seed, filters),
        "labels": {a: {f: {str(seed): {p["id"]: labels[f].get((a, seed, p["id"])) for p in specs[f]["probes"]}
                           for seed in sorted(sessions[(a, f)])} for f in fids} for a in arm_names},
        "multi_value": {"applicable": applicable, "count": count},
    }
    for by_fixture in per_seed.values():
        for by_seed in by_fixture.values():
            for cell in by_seed.values():
                cell.pop("_matched", None)
    return scores


def _add_call(table: dict, arm: str, data: dict) -> None:
    row = table.setdefault(arm, {"n": 0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "wall_s": 0.0})
    row["n"] += 1
    for key, src in (("cost_usd", "cost_usd"), ("input_tokens", "input_tokens_total"), ("output_tokens", "output_tokens"),
                     ("wall_s", "wall_s")):
        v = data.get(src)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            row[key] = _r(row[key] + v)


def _gallery(specs, fids, labels, sessions, per_seed, filters) -> dict:
    """Baseline probe failures on scorable probes and needed items the baseline never matched; capped, deterministic."""
    probes, missed = [], []
    if "baseline" not in {a for a, _ in sessions}:
        return {"probes": probes, "missed_items": missed}
    for f in fids:
        by_id = {p["id"]: p for p in specs[f]["probes"]}
        for pid in filters[f]["scorable"]:
            for seed in sorted(sessions[("baseline", f)]):
                label = labels[f].get(("baseline", seed, pid))
                if label != "correct":
                    answer = ((sessions[("baseline", f)][seed].get("answers") or {}).get(pid) or "")
                    probes.append({"fixture": f, "seed": seed, "probe": pid, "question": by_id[pid]["question"],
                                   "gold": by_id[pid]["answer"], "answer": answer[:ANSWER_CHARS],
                                   "label": label or "missing"})
                    break
        matched = set()
        for cell in per_seed["baseline"][f].values():
            matched.update(cell.get("_matched") or [])
        ran = any(c.get("recall") is not None for c in per_seed["baseline"][f].values())
        for item in specs[f]["items"]:
            if ran and item["needed"] and item["id"] not in matched:
                missed.append({"fixture": f, "item": item["id"], "statement": item["statement"]})
    return {"probes": probes[:GALLERY_MAX], "missed_items": missed[:GALLERY_MAX]}


def write_scores(out, scores: dict) -> None:
    arms.write_json(Path(out) / "scores.json", scores)


def summary_line(scores: dict) -> str:
    s = scores["sanity"]
    counts = ",".join(f"{f}={n}" for f, n in sorted(scores["scorable_counts"].items()))
    split = scores["scorable_split"].values()
    return ("score: %d fixtures, arms %s, judge calls %d (cache hits %d), sanity oracle_recall %.3f mismatch_recall %.3f PASS, "
            "scorable %s, dropped %d/%d, scorable by code_answers: true %d, false %d"
            % (len(scores["fixtures"]), ",".join(scores["arms"]), scores["judge"]["calls_made"],
               scores["judge"]["cached_hits"], s["oracle_recall"], s["mismatch_recall"], counts,
               scores["dropped_total"], scores["non_abstention_total"],
               sum(c["code"] for c in split), sum(c["nocode"] for c in split)))


def command_score(out, fixtures_root, environ=None, stdout=None, label_probe=None, label_items=None, model=None,
                  max_calls=None, seal_dir=None) -> int:
    """Live: the judge calls need CPR_EVAL_LIVE=1, a clean environment and a passing preflight.

    With seal_dir this is the final scoring (P:276-282): <out>/labels.json is re-hashed against seal.json before it is
    read and is never opened for writing; floor and oracle labels come from it alone; every new label goes to
    labels-final.json; and the final scorable record must equal the sealed record.json, or scoring stops.
    """
    stdout = stdout or sys.stdout
    refusal = claude_run.from_refusal(None if out is None else str(out))
    if refusal:
        print(f"score: REFUSED {refusal}", file=stdout)
        return 2

    def work(config, env, o):
        try:
            run = arms.read_json(Path(out) / "run.json")
        except (OSError, ValueError):
            print("score: STOP run.json not found", file=o)
            return 1
        try:
            specs = load_specs(fixtures_root, run["fixtures"])
            if seal_dir:
                import seal as seal_mod  # here, not at the top: seal imports this module
                mismatch = seal_mod.verify_labels(seal_dir, out)
                if mismatch:
                    print(f"score: STOP the sealed labels.json {mismatch} (outcome not determined)", file=o)
                    return 1
            scores = score_run(out, specs, config, env, label_probe, label_items, model, max_calls,
                               "final" if seal_dir else "normal")
            if seal_dir:
                diff = seal_mod.final_record_diff(seal_dir, scores)
                if diff:
                    print("score: STOP the final scorable record differs from the sealed record (outcome not determined): "
                          + "; ".join(diff), file=o)
                    return 1
        except ScoreAbort as exc:
            print(f"score: ABORT {exc}", file=o)
            return 1
        except judge.JudgeBudgetError as exc:
            print(f"score: STOP {exc} (labels so far are saved; raise score.max_judge_calls and rerun)", file=o)
            return 1
        write_scores(out, scores)
        print(summary_line(scores), file=o)
        return 0
    return fixtures._guarded("score", work, environ, stdout)

