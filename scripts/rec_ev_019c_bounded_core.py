"""Deterministic, bounded primitives for the REC-EV-019C Validation runner."""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

import numpy as np


class BudgetExceeded(RuntimeError):
    """Raised before a contracted compute or wall-clock budget is crossed."""


def stable_digest(*parts: object) -> bytes:
    payload = "|".join(str(part) for part in parts).encode("utf-8")
    return hashlib.sha256(payload).digest()


def select_tuning_panel(
    user_keys_by_k: Mapping[int, Iterable[str]],
    users_per_k: Mapping[int, int],
) -> dict[int, list[str]]:
    """Choose a model-independent panel from pseudonymous user keys."""
    selected: dict[int, list[str]] = {}
    for k, requested in sorted(users_per_k.items()):
        keys = sorted(set(user_keys_by_k[int(k)]))
        if len(keys) < int(requested):
            raise ValueError(f"not enough Validation users for K{k} tuning panel")
        ranked = sorted(
            keys,
            key=lambda key: (stable_digest("REC_EV_019C_TUNING_PANEL_V1", key, int(k)), key),
        )
        selected[int(k)] = ranked[: int(requested)]
    return selected


def sample_observed_pairs(
    like_movie_ids: Sequence[int],
    dislike_movie_ids: Sequence[int],
    *,
    user_key: str,
    model_seed: int,
    epoch: int,
    maximum_pairs: int = 16,
) -> list[tuple[int, int]]:
    """Sample observed LIKE>DISLIKE pairs without materializing their Cartesian product."""
    likes = sorted(set(map(int, like_movie_ids)))
    dislikes = sorted(set(map(int, dislike_movie_ids)))
    if maximum_pairs <= 0:
        raise ValueError("maximum_pairs must be positive")
    if set(likes).intersection(dislikes):
        raise ValueError("an observed item cannot be both LIKE and DISLIKE")
    total = len(likes) * len(dislikes)
    wanted = min(total, maximum_pairs)
    if not wanted:
        return []
    chosen: set[int] = set()
    mask = (1 << 64) - 1
    state = int.from_bytes(
        stable_digest("REC_EV_019C_B4_PAIR_V3", model_seed, epoch, user_key)[:8], "big"
    )
    while len(chosen) < wanted:
        state = (state + 0x9E3779B97F4A7C15) & mask
        mixed = state
        mixed = ((mixed ^ (mixed >> 30)) * 0xBF58476D1CE4E5B9) & mask
        mixed = ((mixed ^ (mixed >> 27)) * 0x94D049BB133111EB) & mask
        mixed ^= mixed >> 31
        chosen.add(mixed % total)
    return [(likes[index // len(dislikes)], dislikes[index % len(dislikes)]) for index in sorted(chosen)]


@dataclass
class BudgetLedger:
    limits: Mapping[str, int]
    started_at: float = field(default_factory=time.monotonic)
    counters: dict[str, int] = field(default_factory=dict)

    def charge(self, name: str, amount: int) -> int:
        if amount < 0:
            raise ValueError("budget charge cannot be negative")
        limit_key = {
            "full_catalog_user_item_scores": "maximum_full_catalog_user_item_scores",
            "b8_base_updates": "maximum_b8_base_updates",
            "b4_pair_updates": "maximum_b4_pair_updates",
            "rrf_rank_contributions": "maximum_rrf_rank_contributions",
        }.get(name)
        if limit_key is None:
            raise KeyError(f"unknown budget counter: {name}")
        next_value = self.counters.get(name, 0) + int(amount)
        if next_value > int(self.limits[limit_key]):
            raise BudgetExceeded(f"{name} would exceed {limit_key}")
        self.counters[name] = next_value
        return next_value

    def check_wall_clock(self, *, now: float | None = None) -> str:
        elapsed = (time.monotonic() if now is None else float(now)) - self.started_at
        if elapsed >= int(self.limits["wall_clock_hard_limit_seconds"]):
            raise BudgetExceeded("wall clock hard limit reached; checkpoint before stopping")
        if elapsed >= int(self.limits["wall_clock_soft_limit_seconds"]):
            return "SOFT_LIMIT"
        return "WITHIN_LIMIT"


def deterministic_top_indices(
    candidate_ids: np.ndarray,
    scores: np.ndarray,
    *,
    top_n: int,
) -> np.ndarray:
    if candidate_ids.ndim != 1 or scores.ndim != 1 or len(candidate_ids) != len(scores):
        raise ValueError("candidate IDs and scores must be aligned one-dimensional arrays")
    if top_n <= 0:
        raise ValueError("top_n must be positive")
    finite = np.flatnonzero(np.isfinite(scores))
    if not len(finite):
        return np.empty(0, dtype=np.int64)
    keep = min(int(top_n), len(finite))
    if keep < len(finite):
        finite = finite[np.argpartition(scores[finite], -keep)[-keep:]]
    order = np.lexsort((candidate_ids[finite], -scores[finite]))
    return finite[order[:keep]].astype(np.int64, copy=False)


def exact_rank(
    candidate_ids: np.ndarray,
    scores: np.ndarray,
    movie_id: int,
) -> int | None:
    positions = np.flatnonzero(candidate_ids == int(movie_id))
    if len(positions) != 1:
        return None
    position = int(positions[0])
    score = float(scores[position])
    if not math.isfinite(score):
        return None
    return 1 + int(np.count_nonzero(scores > score)) + int(
        np.count_nonzero((scores == score) & (candidate_ids < int(movie_id)))
    )


def user_ranking_metrics(
    candidate_ids: np.ndarray,
    scores: np.ndarray,
    evaluation_rows: Sequence[Mapping[str, Any]],
    *,
    top_candidates: int = 500,
    top_k: int = 10,
    top2: int = 2,
    fallback_user: bool = False,
) -> tuple[dict[str, Any], list[int]]:
    """Score only observed future labels; every other recommendation stays UNKNOWN."""
    top_indices = deterministic_top_indices(candidate_ids, scores, top_n=top_candidates)
    ranked_ids = candidate_ids[top_indices].astype(np.int64).tolist()
    rank_by_movie = {int(movie_id): rank for rank, movie_id in enumerate(ranked_ids, start=1)}
    candidate_set = set(map(int, candidate_ids.tolist()))
    positives = [row for row in evaluation_rows if bool(row["is_positive"]) and int(row["movie_id"]) in candidate_set]
    negatives = {int(row["movie_id"]) for row in evaluation_rows if bool(row["is_negative"])}
    gains = {int(row["movie_id"]): float(row["midrank_utility"]) for row in positives}
    ideal = sorted(gains.values(), reverse=True)[:top_k]
    idcg = sum(gain / math.log2(rank + 1) for rank, gain in enumerate(ideal, start=1))
    dcg = sum(
        gains[movie_id] / math.log2(rank + 1)
        for movie_id, rank in rank_by_movie.items()
        if movie_id in gains and rank <= top_k
    )
    positive_count = len(gains)
    positive_top10 = sum(1 for movie_id in gains if rank_by_movie.get(movie_id, top_k + 1) <= top_k)
    first_positive = min((rank_by_movie[movie_id] for movie_id in gains if movie_id in rank_by_movie), default=None)
    exact_positive_ranks = [
        rank for movie_id in gains if (rank := exact_rank(candidate_ids, scores, movie_id)) is not None
    ]
    denominator = max(1, int(np.count_nonzero(np.isfinite(scores))))
    top2_ids = set(ranked_ids[:top2])
    has_good = bool(top2_ids.intersection(gains))
    has_bad = bool(top2_ids.intersection(negatives))
    return {
        "ndcg_at_10": float(dcg / idcg) if idcg else 0.0,
        "recall_at_10": float(positive_top10 / positive_count) if positive_count else 0.0,
        "mrr_at_10": float(1.0 / first_positive) if first_positive is not None and first_positive <= top_k else 0.0,
        "positive_mean_rank_percentile": (
            float(np.mean([(rank - 1) / max(1, denominator - 1) for rank in exact_positive_ranks]))
            if exact_positive_ranks
            else None
        ),
        "candidate_recall_at_500": float(any(movie_id in rank_by_movie for movie_id in gains)),
        "harm_at_2": has_bad,
        "miss_at_2": not has_good if positive_count else None,
        "both_good_at_2": len(top2_ids.intersection(gains)) == 2 if positive_count >= 2 else None,
        "safe_hit_at_2": has_good and not has_bad if positive_count else None,
        "fallback_user": bool(fallback_user),
    }, ranked_ids
