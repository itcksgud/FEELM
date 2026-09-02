"""Context construction, scoring, metrics, and deterministic trial selection for 019C."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from rec_ev_019c_bounded_core import user_ranking_metrics
from rec_ev_019c_models import effective_with_b0_fallback


@dataclass(frozen=True)
class ValidationContext:
    user_key: str
    k: int
    anchor_positions: np.ndarray
    labels: np.ndarray
    evaluation_rows: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class ScoreResult:
    raw_scores: np.ndarray
    available: np.ndarray
    fallback_user: bool
    fallback_reason: str | None = None


def build_validation_contexts(
    prefixes: pd.DataFrame,
    windows: pd.DataFrame,
    movie_position: Mapping[int, int],
    *,
    selected_user_keys_by_k: Mapping[int, Iterable[str]] | None = None,
) -> dict[int, list[ValidationContext]]:
    """Build K0/K5/K10 contexts while retaining only final-core prefix positions."""
    selected = (
        {int(k): set(map(str, values)) for k, values in selected_user_keys_by_k.items()}
        if selected_user_keys_by_k is not None
        else None
    )
    prefix_groups = {
        (str(user_key), int(k)): group.sort_values("input_rank", kind="stable")
        for (user_key, k), group in prefixes.groupby(["user_key", "k"], sort=False, observed=True)
    }
    result: dict[int, list[ValidationContext]] = {0: [], 5: [], 10: []}
    for (user_key, k), group in windows.groupby(["user_key", "k"], sort=True, observed=True):
        key = str(user_key)
        k_value = int(k)
        if selected is not None and key not in selected.get(k_value, set()):
            continue
        prefix = prefix_groups.get((key, k_value))
        if k_value == 0:
            anchor_positions = np.empty(0, dtype=np.int32)
            labels = np.empty(0, dtype=np.int8)
        else:
            if prefix is None or len(prefix) != k_value:
                raise RuntimeError(f"invalid Validation prefix size for K{k_value}")
            kept = [
                (movie_position[int(row.movie_id)], int(row.binary_label))
                for row in prefix.itertuples(index=False)
                if int(row.movie_id) in movie_position
            ]
            anchor_positions = np.asarray([row[0] for row in kept], dtype=np.int32)
            labels = np.asarray([row[1] for row in kept], dtype=np.int8)
        evaluation_rows = tuple(
            {
                "movie_id": int(row.movie_id),
                "midrank_utility": float(row.midrank_utility),
                "is_positive": bool(row.is_positive),
                "is_negative": bool(row.is_negative),
            }
            for row in group.sort_values("window_rank", kind="stable").itertuples(index=False)
        )
        result.setdefault(k_value, []).append(
            ValidationContext(key, k_value, anchor_positions, labels, evaluation_rows)
        )
    for values in result.values():
        values.sort(key=lambda context: context.user_key)
    return result


def score_and_rank_context(
    context: ValidationContext,
    score_result: ScoreResult,
    *,
    candidate_ids: np.ndarray,
    b0_percentiles: np.ndarray,
    top_candidates: int,
    top_k: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    effective, fallback_mask = effective_with_b0_fallback(
        score_result.raw_scores,
        score_result.available,
        b0_percentiles,
    )
    seen = np.zeros(len(candidate_ids), dtype=bool)
    seen[context.anchor_positions] = True
    effective[seen] = -np.inf
    metrics, ranked_ids = user_ranking_metrics(
        candidate_ids,
        effective,
        context.evaluation_rows,
        top_candidates=top_candidates,
        top_k=top_k,
        fallback_user=score_result.fallback_user,
    )
    position = {int(movie_id): index for index, movie_id in enumerate(candidate_ids)}
    predictions = [
        {
            "rank": rank,
            "movie_id": int(movie_id),
            "effective_score": float(effective[position[int(movie_id)]]),
            "fallback_used": bool(fallback_mask[position[int(movie_id)]]),
            "fallback_reason": (
                score_result.fallback_reason if fallback_mask[position[int(movie_id)]] else None
            ),
        }
        for rank, movie_id in enumerate(ranked_ids, start=1)
    ]
    return metrics, predictions


def evaluate_contexts(
    contexts: Sequence[ValidationContext],
    score_provider: Callable[[ValidationContext], ScoreResult],
    *,
    candidate_ids: np.ndarray,
    b0_percentiles: np.ndarray,
    top_candidates: int = 500,
    top_k: int = 10,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    metric_rows: list[dict[str, Any]] = []
    predictions: dict[str, list[dict[str, Any]]] = {}
    for context in contexts:
        metrics, ranked = score_and_rank_context(
            context,
            score_provider(context),
            candidate_ids=candidate_ids,
            b0_percentiles=b0_percentiles,
            top_candidates=top_candidates,
            top_k=top_k,
        )
        metric_rows.append({"user_key": context.user_key, "k": context.k, **metrics})
        predictions[context.user_key] = ranked
    return metric_rows, predictions


def aggregate_user_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    if not rows:
        raise ValueError("cannot aggregate an empty user set")

    def mean(key: str) -> float:
        values = [float(row[key]) for row in rows if row.get(key) is not None]
        return float(np.mean(values)) if values else float("nan")

    return {
        "user_macro_ndcg_at_10": mean("ndcg_at_10"),
        "recall_at_10": mean("recall_at_10"),
        "mrr_at_10": mean("mrr_at_10"),
        "positive_mean_rank_percentile": mean("positive_mean_rank_percentile"),
        "candidate_recall_at_500": mean("candidate_recall_at_500"),
        "fallback_user_rate": mean("fallback_user"),
        "harm_at_2": mean("harm_at_2"),
        "miss_at_2": mean("miss_at_2"),
        "both_good_at_2": mean("both_good_at_2"),
        "safe_hit_at_2": mean("safe_hit_at_2"),
    }


def select_trial(aggregates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not aggregates:
        raise ValueError("no completed trial can be selected")
    ordered = sorted(
        aggregates,
        key=lambda row: (
            -float(row["user_macro_ndcg_at_10"]),
            -float(row["candidate_recall_at_500"]),
            float(row["fallback_user_rate"]),
            str(row["trial_id"]),
        ),
    )
    return dict(ordered[0])


def metrics_from_top_ranking(
    ranked_ids: Sequence[int],
    evaluation_rows: Sequence[Mapping[str, Any]],
    *,
    candidate_set: set[int],
    candidate_count_after_seen: int,
    exact_rank_provider: Callable[[int], int | None],
    top_k: int = 10,
    top_candidates: int = 500,
    fallback_user: bool = False,
) -> dict[str, Any]:
    """Evaluate a Top-N ranking without rescanning every catalog item."""
    ranked = list(map(int, ranked_ids[:top_candidates]))
    rank_by_movie = {movie_id: rank for rank, movie_id in enumerate(ranked, start=1)}
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
    first_positive = min((rank_by_movie[movie_id] for movie_id in gains if movie_id in rank_by_movie), default=None)
    exact_ranks = [rank for movie_id in gains if (rank := exact_rank_provider(movie_id)) is not None]
    top2_ids = set(ranked[:2])
    has_good = bool(top2_ids.intersection(gains))
    has_bad = bool(top2_ids.intersection(negatives))
    return {
        "ndcg_at_10": float(dcg / idcg) if idcg else 0.0,
        "recall_at_10": float(sum(rank_by_movie.get(movie_id, top_k + 1) <= top_k for movie_id in gains) / len(gains)) if gains else 0.0,
        "mrr_at_10": float(1.0 / first_positive) if first_positive is not None and first_positive <= top_k else 0.0,
        "positive_mean_rank_percentile": (
            float(np.mean([(rank - 1) / max(1, candidate_count_after_seen - 1) for rank in exact_ranks]))
            if exact_ranks else None
        ),
        "candidate_recall_at_500": float(any(movie_id in rank_by_movie for movie_id in gains)),
        "harm_at_2": has_bad,
        "miss_at_2": not has_good if gains else None,
        "both_good_at_2": len(top2_ids.intersection(gains)) == 2 if len(gains) >= 2 else None,
        "safe_hit_at_2": has_good and not has_bad if gains else None,
        "fallback_user": bool(fallback_user),
    }
