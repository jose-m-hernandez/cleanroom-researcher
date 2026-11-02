"""Regenerate experiments/EXP-0002/REPORT.md from the recorded run.

Reads recorded/run.json and recorded/scores.json and renders them with the unchanged report module.
The failure gallery is withheld because it quotes unpublished fixture text; a fixed sentence replaces it.
Usage: python3 make_report.py --seed <int> --out <dir>
"""

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HEADING = "## Failure gallery"
SENTENCE = "The failure gallery is withheld because it quotes the development fixtures' questions, answers and statements, which are not published."


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    run = json.loads((HERE / "recorded" / "run.json").read_text(encoding="utf-8"))
    scores = json.loads((HERE / "recorded" / "scores.json").read_text(encoding="utf-8"))
    if args.seed != run["rng_seed"]:
        print("make_report: --seed does not match the recorded run's rng_seed", file=sys.stderr)
        return 2

    sys.path.insert(0, str(HERE / "code"))
    import report

    scores["gallery"] = {"probes": [], "missed_items": []}
    text = report.build_markdown(run, scores)
    lines = text.split("\n")
    if lines.count(HEADING) != 1:
        print("make_report: the report must hold the gallery heading exactly once", file=sys.stderr)
        return 2
    head = "\n".join(lines[: lines.index(HEADING)]) + "\n"
    out = head + HEADING + "\n\n" + SENTENCE + "\n"

    dest = Path(args.out) / "experiments" / "EXP-0002" / "REPORT.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(out, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
