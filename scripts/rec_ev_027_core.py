"""Deterministic primitives for the REC-EV-027 strict item-cold experiment."""

from __future__ import annotations

import hashlib
import math
from typing import Iterable, Sequence

import numpy as np


RATING_VALUES = np.arange(0.5, 5.01, 0.5, dtype=np.float64)
MASK64 = (1 << 64) - 1


def rating_index(raw: bytes | str | float) -> int:
    value = float(raw)
    index = int(round(value * 2.0)) - 1
    if not 0 <= index < 10 or abs(value - float(RATING_VALUES[index])) > 1e-9:
        raise ValueError(f"rating outside pinned MovieLens bins: {value}")
    return index


def user_equal_prior(histograms: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    hist = np.asarray(histograms, dtype=np.float64)
    if hist.ndim != 2 or hist.shape[1] != 10:
        raise ValueError("users by ten-bin histograms required")
    totals = hist.sum(axis=1)
    valid = totals > 0
    if not bool(valid.any()):
        raise ValueError("at least one warm training user is required")
    pi0 = (hist[valid] / totals[valid, None]).mean(axis=0)
    pi0 /= pi0.sum()
    g0_mid = np.cumsum(pi0) - 0.5 * pi0
    return pi0, g0_mid


def smoothed_q(indices: Sequence[int], histogram: Sequence[int], g0_mid: Sequence[float]) -> np.ndarray:
    idx = np.asarray(indices, dtype=np.int8)
    hist = np.asarray(histogram, dtype=np.float64)
    prior = np.asarray(g0_mid, dtype=np.float64)
    if hist.shape != (10,) or prior.shape != (10,) or bool((idx < 0).any()) or bool((idx > 9).any()):
        raise ValueError("invalid percentile inputs")
    below = np.cumsum(hist) - hist
    return (below[idx] + 0.5 * hist[idx] + 5.0 * prior[idx]) / (hist.sum() + 5.0)


def profile_weights(indices: Sequence[int], g0_mid: Sequence[float], encoding: str) -> np.ndarray:
    idx = np.asarray(indices, dtype=np.int8)
    hist = np.bincount(idx, minlength=10)
    weights = 2.0 * smoothed_q(idx, hist, g0_mid) - 1.0
    if encoding == "PERCENTILE_MAGNITUDE":
        return weights
    if encoding == "BINARY_SIGN":
        return np.sign(weights)
    raise ValueError(f"unsupported encoding: {encoding}")


def full_history_q(indices: Sequence[int], histogram: Sequence[int]) -> np.ndarray:
    idx = np.asarray(indices, dtype=np.int8)
    hist = np.asarray(histogram, dtype=np.float64)
    if hist.shape != (10,) or hist.sum() <= 0:
        raise ValueError("nonempty ten-bin full history required")
    below = np.cumsum(hist) - hist
    return (below[idx] + 0.5 * hist[idx]) / hist.sum()


def splitmix_bpr_pairs(
    like_positions: Sequence[int],
    dislike_positions: Sequence[int],
    *,
    item_ids: np.ndarray,
    track: str,
    fold: str,
    seed: int,
    epoch: int,
    user_key: str,
    maximum_pairs: int = 16,
) -> list[tuple[int, int]]:
    likes = sorted(set(map(int, like_positions)), key=lambda pos: int(item_ids[pos]))
    dislikes = sorted(set(map(int, dislike_positions)), key=lambda pos: int(item_ids[pos]))
    if set(likes) & set(dislikes):
        raise ValueError("like and dislike positions overlap")
    total = len(likes) * len(dislikes)
    wanted = min(total, int(maximum_pairs))
    if wanted <= 0:
        return []
    payload = f"REC_EV_027_BPR_PAIR_V2|{track}|{fold}|{int(seed)}|{int(epoch)}|{user_key}".encode("utf-8")
    state = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big", signed=False)
    chosen: set[int] = set()
    while len(chosen) < wanted:
        state = (state + 0x9E3779B97F4A7C15) & MASK64
        mixed = state
        mixed = ((mixed ^ (mixed >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
        mixed = ((mixed ^ (mixed >> 27)) * 0x94D049BB133111EB) & MASK64
        mixed ^= mixed >> 31
        chosen.add(int(mixed % total))
    return [(likes[index // len(dislikes)], dislikes[index % len(dislikes)]) for index in sorted(chosen)]


def weighted_similarity_scores(similarity: np.ndarray, weights: Sequence[float]) -> tuple[np.ndarray, bool]:
    matrix = np.asarray(similarity, dtype=np.float64)
    vector = np.asarray(weights, dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[1] != len(vector):
        raise ValueError("target by profile similarity required")
    denominator = float(np.abs(vector).sum())
    if denominator <= 0 or not math.isfinite(denominator):
        return np.full(matrix.shape[0], np.nan), False
    scores = matrix @ vector / denominator
    active = bool(np.isfinite(scores).all() and np.unique(scores).size >= 2)
    return scores, active


def deterministic_order(
    movie_ids: Sequence[int],
    scores: Sequence[float],
    *,
    phase: str,
    track: str,
    fold: str,
    model: str,
    encoding: str,
    user_key: str,
) -> np.ndarray:
    movies = np.asarray(movie_ids, dtype=np.int64)
    values = np.asarray(scores, dtype=np.float64)
    if movies.shape != values.shape or not np.isfinite(values).all():
        raise ValueError("finite aligned movie scores required")
    return np.asarray(sorted(range(len(movies)), key=lambda index: (
        -float(values[index]),
        hashlib.sha256(
            f"REC_EV_027_SCORE_TIE_V1|{phase}|{track}|{fold}|{model}|{encoding}|SEED_AGGREGATED|{user_key}|{int(movies[index])}".encode("utf-8")
        ).digest(),
        int(movies[index]),
    )), dtype=np.int16)


def rrf_scores(component_orders: Iterable[np.ndarray], size: int, c: float = 10.0) -> np.ndarray:
    active = [np.asarray(order, dtype=np.int64) for order in component_orders if len(order)]
    if not active:
        return np.full(size, np.nan)
    if len(active) == 1:
        scores = np.empty(size, dtype=np.float64)
        scores[active[0]] = np.arange(size, 0, -1, dtype=np.float64)
        return scores
    scores = np.zeros(size, dtype=np.float64)
    for order in active:
        ranks = np.empty(size, dtype=np.int64)
        ranks[order] = np.arange(1, size + 1)
        scores += 1.0 / (float(c) + ranks)
    return scores


def analytic_random_top2(q_values: Sequence[float]) -> dict[str, float]:
    q = np.asarray(q_values, dtype=np.float64)
    n = len(q)
    if n < 2 or not np.isfinite(q).all():
        raise ValueError("at least two finite judged labels required")
    pair_count = n * (n - 1) / 2.0
    low = int(np.count_nonzero(q <= 0.20))
    high = int(np.count_nonzero(q >= 0.80))
    safe_pairs = (n - low) * (n - low - 1) / 2.0
    no_good_pairs = (n - high) * (n - high - 1) / 2.0
    minima = [min(float(q[i]), float(q[j])) for i in range(n) for j in range(i + 1, n)]
    return {
        "HARM20": 1.0 - safe_pairs / pair_count,
        "TOP2_MEAN_Q": float(q.mean()),
        "TOP2_MIN_Q": float(np.mean(minima)),
        "GOOD80": 1.0 - no_good_pairs / pair_count,
    }


def ranked_top2_metrics(order: Sequence[int], q_values: Sequence[float]) -> dict[str, float]:
    q = np.asarray(q_values, dtype=np.float64)[np.asarray(order, dtype=np.int64)[:2]]
    if q.shape != (2,) or not np.isfinite(q).all():
        raise ValueError("exactly two ranked labels required")
    return {
        "HARM20": float(bool((q <= 0.20).any())),
        "TOP2_MEAN_Q": float(q.mean()),
        "TOP2_MIN_Q": float(q.min()),
        "GOOD80": float(bool((q >= 0.80).any())),
    }


def simultaneous_max_t(values: np.ndarray, *, repeats: int, seed: int) -> dict[str, np.ndarray | float]:
    matrix = np.asarray(values, dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[0] < 2 or not np.isfinite(matrix).all():
        raise ValueError("finite users by contrasts matrix required")
    n = matrix.shape[0]
    point = matrix.mean(axis=0)
    se = matrix.std(axis=0, ddof=1) / math.sqrt(n)
    nonzero = se > 0
    maxima = np.zeros(int(repeats), dtype=np.float64)
    rng = np.random.Generator(np.random.PCG64(int(seed)))
    for repeat in range(int(repeats)):
        indices = rng.integers(0, n, size=n, endpoint=False, dtype=np.int64)
        if bool(nonzero.any()):
            t = (matrix[indices].mean(axis=0)[nonzero] - point[nonzero]) / se[nonzero]
            maxima[repeat] = float(np.max(np.abs(t)))
    critical = float(np.quantile(maxima, 0.95, method="higher"))
    half = critical * se
    return {"point": point, "se": se, "critical": critical, "low": point - half, "high": point + half}
