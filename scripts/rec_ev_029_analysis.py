"""Pure metric and winner-selection helpers for REC-EV-029B."""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np

from rec_ev_027_core import analytic_random_top2


USER_METRICS = ("HARM20", "TOP2_MIN_Q", "TOP2_MEAN_Q")
ITEM_METRICS = ("ITEM_UTILITY", "ITEM_LOW")


def benefit(model: np.ndarray | float, comparator: np.ndarray | float, metric: str):
    left = np.asarray(model, dtype=np.float64)
    right = np.asarray(comparator, dtype=np.float64)
    result = right - left if metric in {"HARM20", "ITEM_LOW"} else left - right
    return float(result) if result.ndim == 0 else result


def user_metric_arrays(
    q_values: np.ndarray, top2: np.ndarray, active: np.ndarray
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    q = np.asarray(q_values, dtype=np.float64)
    choices = np.asarray(top2, dtype=np.int64)
    enabled = np.asarray(active, dtype=bool)
    if q.ndim != 2 or choices.shape != (q.shape[0], 2) or enabled.shape != (q.shape[0],):
        raise ValueError("users-by-target q, users-by-two Top2, and aligned activity required")
    random_rows = [analytic_random_top2(row) for row in q]
    random = {
        metric: np.asarray([row[metric] for row in random_rows], dtype=np.float64)
        for metric in USER_METRICS
    }
    model = {metric: values.copy() for metric, values in random.items()}
    if bool(enabled.any()):
        rows = np.flatnonzero(enabled)
        selected = q[rows[:, None], choices[rows]]
        model["HARM20"][rows] = (selected <= 0.20).any(axis=1).astype(np.float64)
        model["TOP2_MIN_Q"][rows] = selected.min(axis=1)
        model["TOP2_MEAN_Q"][rows] = selected.mean(axis=1)
    return model, random


def item_contribution_arrays(
    q_values: np.ndarray, top2: np.ndarray, active: np.ndarray
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    q = np.asarray(q_values, dtype=np.float64)
    choices = np.asarray(top2, dtype=np.int64)
    enabled = np.asarray(active, dtype=bool)
    if q.ndim != 2 or choices.shape != (q.shape[0], 2) or enabled.shape != (q.shape[0],):
        raise ValueError("users-by-target q, users-by-two Top2, and aligned activity required")
    random = {"ITEM_UTILITY": q.copy(), "ITEM_LOW": (q <= 0.20).astype(np.float64)}
    model = {metric: values.copy() for metric, values in random.items()}
    scale = q.shape[1] / 2.0
    for row in np.flatnonzero(enabled):
        model["ITEM_UTILITY"][row] = 0.0
        model["ITEM_LOW"][row] = 0.0
        selected = choices[row]
        model["ITEM_UTILITY"][row, selected] = scale * q[row, selected]
        model["ITEM_LOW"][row, selected] = scale * (q[row, selected] <= 0.20)
    return model, random


def equal_movie_mean(movie_ids: np.ndarray, values: np.ndarray) -> float:
    movies = np.asarray(movie_ids, dtype=np.int64).ravel()
    scores = np.asarray(values, dtype=np.float64).ravel()
    if movies.shape != scores.shape or not np.isfinite(scores).all():
        raise ValueError("finite aligned occurrence values required")
    _, inverse = np.unique(movies, return_inverse=True)
    totals = np.bincount(inverse, weights=scores)
    counts = np.bincount(inverse)
    return float(np.mean(totals / counts))


def candidate_is_eligible(summary: Mapping[str, Any]) -> bool:
    user = summary["pooled_user_benefits"]
    item = summary["pooled_item_benefits"]
    return bool(
        float(summary["minimum_fold_own_active_rate"]) >= 0.95
        and float(summary["minimum_fold_shuffle_active_rate"]) >= 0.95
        and float(user["OWN_VS_RANDOM"]["HARM20"]) >= 0.0
        and float(user["OWN_VS_RANDOM"]["TOP2_MIN_Q"]) > 0.0
        and float(user["OWN_VS_RANDOM"]["TOP2_MEAN_Q"]) > 0.0
        and float(user["OWN_VS_SHUFFLE"]["HARM20"]) >= 0.0
        and float(user["OWN_VS_SHUFFLE"]["TOP2_MIN_Q"]) > 0.0
        and float(user["OWN_VS_SHUFFLE"]["TOP2_MEAN_Q"]) > 0.0
        and float(item["OWN_VS_RANDOM"]["ITEM_UTILITY"]) > 0.0
        and float(item["OWN_VS_RANDOM"]["ITEM_LOW"]) >= 0.0
        and float(item["OWN_VS_SHUFFLE"]["ITEM_UTILITY"]) > 0.0
        and float(item["OWN_VS_SHUFFLE"]["ITEM_LOW"]) >= 0.0
        and int(summary["fold_direction_pass_count"]) >= 3
    )


def winner_key(summary: Mapping[str, Any]) -> tuple[float, float, float, float, float, int, int, int]:
    family_order = {"STRUCTURED_DIRECT": 0, "E5_DIRECT": 1, "STRUCTURED_E5_RRF": 2}
    encoding_order = {"PERCENTILE_MAGNITUDE": 0, "BINARY_SIGN": 1}
    return (
        -float(summary["minimum_fold_harm_benefit_own_vs_random"]),
        -float(summary["minimum_fold_harm_benefit_own_vs_shuffle"]),
        -float(summary["pooled_user_benefits"]["OWN_VS_RANDOM"]["HARM20"]),
        -float(summary["minimum_fold_min_q_benefit_own_vs_random"]),
        -float(summary["pooled_user_metrics"]["OWN"]["TOP2_MEAN_Q"]),
        int(summary["k"]),
        family_order[str(summary["family"])],
        encoding_order[str(summary["encoding"])],
    )


def select_winner(summaries: list[dict[str, Any]]) -> dict[str, Any] | None:
    eligible = [summary for summary in summaries if candidate_is_eligible(summary)]
    return min(eligible, key=winner_key) if eligible else None
