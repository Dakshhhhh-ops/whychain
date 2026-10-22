"""Small, documented statistics used by the analysis engine."""
from __future__ import annotations

import math
from itertools import combinations
from typing import Callable

import numpy as np
import pandas as pd

Z95 = 1.959964


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """95% Wilson interval for a proportion k/n."""
    if n <= 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def diff_ci(k1: int, n1: int, k0: int, n0: int, z: float = Z95) -> tuple[float, float, float]:
    """Difference of two proportions (p1 - p0) with a 95% normal-approximation interval."""
    if n1 <= 0 or n0 <= 0:
        return (float("nan"),) * 3
    p1, p0 = k1 / n1, k0 / n0
    se = math.sqrt(p1 * (1 - p1) / n1 + p0 * (1 - p0) / n0)
    return (p1 - p0, p1 - p0 - z * se, p1 - p0 + z * se)


def quantile_map(x: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """Replace each value of x by the reference value at the same rank: x keeps its order, but takes the reference's
    distribution. Used for counterfactuals ("what if transit had been as in the baseline")."""
    x = np.asarray(x, dtype=float)
    ref = np.asarray(ref, dtype=float)
    ref = ref[~np.isnan(ref)]
    if len(x) == 0 or len(ref) == 0:
        return x
    ranks = (pd.Series(x).rank(method="average").to_numpy() - 0.5) / len(x)
    return np.quantile(ref, np.clip(ranks, 0, 1))


def shapley(factors: list[str], value: Callable[[frozenset], float]) -> dict[str, float]:
    """Exact Shapley values: each factor's average marginal effect over every order of adding factors.
    The values add up to value(all) - value(none), whatever the interactions between factors."""
    n = len(factors)
    cache: dict[frozenset, float] = {}

    def v(s: frozenset) -> float:
        if s not in cache:
            cache[s] = value(s)
        return cache[s]

    phi = {}
    for f in factors:
        others = [g for g in factors if g != f]
        total = 0.0
        for r in range(len(others) + 1):
            w = math.factorial(r) * math.factorial(n - r - 1) / math.factorial(n)
            for s in combinations(others, r):
                s = frozenset(s)
                total += w * (v(s | {f}) - v(s))
        phi[f] = total
    return phi


def shift_share(focus: pd.DataFrame, ref: pd.DataFrame, dim: str, metric: str = "late") -> pd.DataFrame:
    """Split the difference in a rate between two groups of orders into segments, each with a rate effect (the segment
    itself got worse) and a mix effect (more orders came from that segment). The parts add up exactly:
        p_focus - p_ref = sum over segments of (rate_effect + mix_effect)
    with symmetric weights: rate = (w1 + w0)/2 * (r1 - r0),  mix = (r1 + r0)/2 * (w1 - w0)."""
    g1 = focus.groupby(dim, dropna=False)[metric].agg(["sum", "count"])
    g0 = ref.groupby(dim, dropna=False)[metric].agg(["sum", "count"])
    t = g1.join(g0, how="outer", lsuffix="_1", rsuffix="_0").fillna(0)
    n1, n0 = t["count_1"].sum(), t["count_0"].sum()
    w1, w0 = t["count_1"] / max(n1, 1), t["count_0"] / max(n0, 1)
    r1 = (t["sum_1"] / t["count_1"]).where(t["count_1"] > 0)
    r0 = (t["sum_0"] / t["count_0"]).where(t["count_0"] > 0)
    r1, r0 = r1.fillna(r0).fillna(0), r0.fillna(r1).fillna(0)
    out = pd.DataFrame({
        "focus_orders": t["count_1"].astype(int), "ref_orders": t["count_0"].astype(int),
        "focus_rate": r1, "ref_rate": r0, "focus_weight": w1, "ref_weight": w0,
        "rate_effect": (w1 + w0) / 2 * (r1 - r0), "mix_effect": (r1 + r0) / 2 * (w1 - w0),
    })
    out["total"] = out["rate_effect"] + out["mix_effect"]
    return out
