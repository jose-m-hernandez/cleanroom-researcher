"""Statistics for the eval. Standard library only; the unit of analysis is the fixture.

Every function is deterministic: the bootstrap takes an explicit seed and uses
its own random.Random, so the same inputs give the same interval.
"""

from __future__ import annotations

import math
import random
import statistics

ROUND = 10  # results are rounded so float noise never reaches a report


def _r(x: float) -> float:
    return round(x + 0.0, ROUND)  # + 0.0 turns -0.0 into 0.0


def mean(values) -> float:
    """Mean by fsum (exactly rounded), so ten copies of 0.2 average to 0.2."""
    values = list(values)
    return math.fsum(values) / len(values)


def seed_mean(values):
    """Mean of the seeds that have a value (None entries are skipped); None when no seed has one."""
    present = [v for v in values if v is not None]
    return _r(mean(present)) if present else None


def stdev(values) -> float:
    """Sample standard deviation (n - 1); needs at least two values."""
    return _r(statistics.stdev(list(values)))


def paired_diffs(arm_a: dict, arm_b: dict) -> list:
    """arm_a - arm_b for each fixture both arms have a value for, in fixture-id order."""
    return [_r(arm_a[k] - arm_b[k]) for k in sorted(set(arm_a) & set(arm_b))
            if arm_a[k] is not None and arm_b[k] is not None]


def bootstrap_ci(diffs, b: int = 10000, alpha: float = 0.05, rng_seed: int = 0) -> tuple:
    """Percentile bootstrap interval of the mean. Returns (lo, hi); (None, None) for no data."""
    diffs = list(diffs)
    if not diffs:
        return (None, None)
    rng = random.Random(rng_seed)
    n = len(diffs)
    means = sorted(math.fsum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(b))
    lo = means[int(math.floor(alpha / 2 * b))]
    hi = means[min(b - 1, int(math.ceil((1 - alpha / 2) * b)) - 1)]
    return (_r(lo), _r(hi))


def _average_ranks(values: list) -> tuple:
    """Ranks of values (ties share the mean rank) and the tie sizes."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    ties = []
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        ties.append(j - i + 1)
        i = j + 1
    return ranks, ties


def wilcoxon(diffs) -> dict:
    """Wilcoxon signed-rank test, two-sided. Zero differences are dropped first.

    Returns {"n", "w_plus", "w_minus", "p", "method"}. With n <= 25 and no tied absolute values the p-value is
    exact, by enumerating all 2**n sign patterns (twice the one-sided tail of the smaller rank sum, capped at 1).
    Otherwise it is the normal approximation with the tie correction to the variance and a continuity correction of
    one half. With no non-zero difference p is 1.
    """
    nz = [d for d in diffs if d != 0]
    n = len(nz)
    if n == 0:
        return {"n": 0, "w_plus": 0.0, "w_minus": 0.0, "p": 1.0, "method": "none"}
    ranks, ties = _average_ranks([abs(d) for d in nz])
    w_plus = math.fsum(r for r, d in zip(ranks, nz) if d > 0)
    w_minus = math.fsum(r for r, d in zip(ranks, nz) if d < 0)
    w = min(w_plus, w_minus)
    if n <= 25 and max(ties) == 1:
        # distribution of the positive rank sum over integer ranks 1..n: counts[s] subsets sum to s
        total = n * (n + 1) // 2
        counts = [0] * (total + 1)
        counts[0] = 1
        for rank in range(1, n + 1):
            for s in range(total, rank - 1, -1):
                counts[s] += counts[s - rank]
        tail = sum(counts[: int(w) + 1]) / 2 ** n
        p, method = min(1.0, 2 * tail), "exact"
    else:
        mu = n * (n + 1) / 4
        var = n * (n + 1) * (2 * n + 1) / 24 - sum(t ** 3 - t for t in ties) / 48
        if var <= 0:
            p = 1.0
        else:
            z = (abs(w - mu) - 0.5) / math.sqrt(var)
            p = min(1.0, 2 * (1 - statistics.NormalDist().cdf(max(z, 0.0))))
        method = "normal"
    return {"n": n, "w_plus": _r(w_plus), "w_minus": _r(w_minus), "p": _r(p), "method": method}


def within_fixture_sd(scores: dict):
    """Pooled within-fixture SD: the root mean square of each fixture's sample SD across its seeds.

    scores maps fixture -> list of per-seed values (None entries skipped). Fixtures with fewer than two seeds
    are left out. None when no fixture has two.
    """
    variances = []
    for values in scores.values():
        present = [v for v in values if v is not None]
        if len(present) >= 2:
            variances.append(statistics.variance(present))
    return _r(math.sqrt(mean(variances))) if variances else None


def cohen_kappa(a, b) -> float:
    """Cohen's kappa for two raters over the same items. Perfect agreement is 1.0 even when chance agreement is 1."""
    a, b = list(a), list(b)
    if len(a) != len(b) or not a:
        raise ValueError("kappa needs two equal, non-empty label lists")
    n = len(a)
    observed = sum(1 for x, y in zip(a, b) if x == y) / n
    expected = math.fsum((a.count(label) / n) * (b.count(label) / n) for label in set(a) | set(b))
    if expected == 1.0:
        return 1.0 if observed == 1.0 else 0.0
    return _r((observed - expected) / (1 - expected))


def holm(pvals) -> list:
    """Holm-Bonferroni adjusted p-values, in input order."""
    pvals = list(pvals)
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * pvals[i]))
        adjusted[i] = _r(running)
    return adjusted


def equivalent(diffs, delta: float, alpha: float = 0.10, rng_seed: int = 0) -> bool:
    """True when the upper bound of the (1 - alpha) bootstrap interval of the mean difference is below delta."""
    lo, hi = bootstrap_ci(diffs, alpha=alpha, rng_seed=rng_seed)
    return hi is not None and hi < delta


def aa_split(scores: dict) -> dict:
    """Per fixture: mean of seeds 1-2 minus mean of seeds 3-4 (seed 5 is left out so the halves match).

    scores maps fixture -> per-seed values in seed order. A fixture without four non-None seeds is left out.
    """
    out = {}
    for key in sorted(scores):
        values = list(scores[key])[:4]
        if len(values) == 4 and all(v is not None for v in values):
            out[key] = _r(mean(values[:2]) - mean(values[2:]))
    return out


def n_required(sd_d: float, delta: float, alpha: float = 0.05, power: float = 0.80) -> int:
    """Fixtures needed to detect a mean paired difference of delta: ceil(((z_{1-alpha/2} + z_power) * sd_d / delta)^2)."""
    z = statistics.NormalDist().inv_cdf
    return int(math.ceil(round(((z(1 - alpha / 2) + z(power)) * sd_d / delta) ** 2, 9)))

