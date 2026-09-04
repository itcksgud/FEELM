#!/usr/bin/env python3
"""Analyze the bounded REC-EV-019C Validation run without opening Locked Test.

The runner writes large, machine-oriented Parquet artifacts.  This script turns
them into a small JSON evidence summary and report companion figures.  It never
selects a champion and it treats unobserved movies as UNKNOWN, not negative.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import zipfile
from pathlib import Path
from typing import Any, Iterable, Mapping

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pyarrow.dataset as ds
from matplotlib import font_manager
from matplotlib.ticker import PercentFormatter


ROOT = Path(__file__).resolve().parents[1]
RUN_ROOT = ROOT / "outputs/recommendation-evidence/rec-ev-019c"
SUMMARY_PATH = RUN_ROOT / "analysis-summary.json"
FIGURE_ROOT = ROOT / "docs/recommendation/figures"
MANIFEST_PATH = ROOT / "docs/recommendation/evidence/manifests/rec-ev-019c-analysis.json"

MODEL_LABELS = {
    "B0_MOVIELENS_BAYESIAN_RATING": "B0 인기도",
    "B2_ITEM_KNN": "B2 ItemKNN",
    "B4_BPR_MF": "B4 관측 BPR",
    "B6_TMDB_STRUCTURED_CONTENT": "B6 TMDB 구조",
    "B7_TMDB_TEXT_CONTENT": "B7 TMDB 텍스트",
    "B8_LIGHTFM": "B8 LightFM",
    "B9_RRF": "B9 RRF",
}
BASELINE = "B0_MOVIELENS_BAYESIAN_RATING"
LIGHTFM = "B8_LIGHTFM"
EPSILON = 1e-12
CONFIRMATORY_BOOTSTRAP_ITERATIONS = 2_000
CONFIRMATORY_BOOTSTRAP_SEED_BASE = 20260905
RELEASE_YEAR_GROUPS = (
    "RELEASE_BEFORE_2020",
    "RELEASE_2020_OR_LATER",
    "RELEASE_YEAR_UNKNOWN",
)
COLD_ITEM_GROUPS = ("BASE_TRAIN_ZERO", "BASE_TRAIN_OBSERVED")


def _native(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _native(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_native(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if pd.isna(value):
        return None
    return value


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(_native(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_entry(path: Path) -> dict[str, Any]:
    return {
        "path": path.resolve().relative_to(ROOT.resolve()).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def paired_bootstrap_ci(
    values: np.ndarray,
    *,
    seed: int = 20260904,
    iterations: int = 10_000,
) -> tuple[float, float]:
    clean = np.asarray(values, dtype=np.float64)
    clean = clean[np.isfinite(clean)]
    if len(clean) == 0:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    means = np.empty(iterations, dtype=np.float64)
    chunk = 500
    for start in range(0, iterations, chunk):
        stop = min(iterations, start + chunk)
        indices = rng.integers(0, len(clean), size=(stop - start, len(clean)))
        means[start:stop] = clean[indices].mean(axis=1)
    lower, upper = np.quantile(means, [0.025, 0.975])
    return float(lower), float(upper)


def mean_bool(series: pd.Series) -> float | None:
    valid = series.dropna()
    return float(valid.astype(float).mean()) if len(valid) else None


def aggregate_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "users": int(frame["user_key"].nunique()),
        "ndcg_at_10": float(frame["ndcg_at_10"].mean()),
        "recall_at_10": float(frame["recall_at_10"].mean()),
        "mrr_at_10": float(frame["mrr_at_10"].mean()),
        "positive_mean_rank_percentile": float(frame["positive_mean_rank_percentile"].mean()),
        "candidate_recall_at_500": float(frame["candidate_recall_at_500"].mean()),
        "harm_at_2": mean_bool(frame["harm_at_2"]),
        "miss_at_2": mean_bool(frame["miss_at_2"]),
        "both_good_at_2": mean_bool(frame["both_good_at_2"]),
        "safe_hit_at_2": mean_bool(frame["safe_hit_at_2"]),
        "fallback_user_rate": float(frame["fallback_user"].astype(float).mean()),
    }


def paired_summary(metrics: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for k in (5, 10):
        baseline = metrics.loc[
            (metrics["k"] == k) & (metrics["model_id"] == BASELINE)
        ].set_index("user_key")
        for model_id in MODEL_LABELS:
            if model_id == BASELINE:
                continue
            candidate = metrics.loc[
                (metrics["k"] == k) & (metrics["model_id"] == model_id)
            ].set_index("user_key")
            if candidate.empty:
                continue
            joined = candidate.join(baseline, lsuffix="_candidate", rsuffix="_baseline", how="inner")
            delta = (
                joined["ndcg_at_10_candidate"] - joined["ndcg_at_10_baseline"]
            ).to_numpy(dtype=np.float64)
            lower, upper = paired_bootstrap_ci(delta, seed=20260904 + k)
            harm_delta = (
                joined["harm_at_2_candidate"].astype(float)
                - joined["harm_at_2_baseline"].astype(float)
            ).to_numpy(dtype=np.float64)
            harm_lower, harm_upper = paired_bootstrap_ci(harm_delta, seed=20261004 + k)
            rows.append({
                "k": k,
                "model_id": model_id,
                "users": int(len(joined)),
                "delta_ndcg_mean": float(delta.mean()),
                "delta_ndcg_ci95": [lower, upper],
                "delta_ndcg_quantiles": {
                    label: float(value)
                    for label, value in zip(
                        ("p10", "p25", "p50", "p75", "p90"),
                        np.quantile(delta, [0.10, 0.25, 0.50, 0.75, 0.90]),
                    )
                },
                "benefit_rate": float(np.mean(delta > EPSILON)),
                "tie_rate": float(np.mean(np.abs(delta) <= EPSILON)),
                "harm_rate": float(np.mean(delta < -EPSILON)),
                "delta_harm_at_2": float(harm_delta.mean()),
                "delta_harm_at_2_ci95": [harm_lower, harm_upper],
                "delta_miss_at_2": float(
                    joined["miss_at_2_candidate"].astype(float).mean()
                    - joined["miss_at_2_baseline"].astype(float).mean()
                ),
                "delta_safe_hit_at_2": float(
                    joined["safe_hit_at_2_candidate"].astype(float).mean()
                    - joined["safe_hit_at_2_baseline"].astype(float).mean()
                ),
                "bootstrap": {
                    "unit": "user",
                    "method": "percentile",
                    "seed": 20260904 + k,
                    "iterations": 10_000,
                },
            })
    return rows


def confirmatory_paired_summary(
    metrics: pd.DataFrame,
    tuning_panel: Mapping[str, Iterable[str]],
) -> list[dict[str, Any]]:
    """Recompute LightFM T003 vs B0 after excluding the K-specific tuning panel."""
    rows: list[dict[str, Any]] = []
    for k in (5, 10):
        baseline = metrics.loc[
            (metrics["k"] == k) & (metrics["model_id"] == BASELINE)
        ].set_index("user_key")
        candidate = metrics.loc[
            (metrics["k"] == k) & (metrics["model_id"] == LIGHTFM)
        ].set_index("user_key")
        joined = candidate.join(baseline, lsuffix="_candidate", rsuffix="_baseline", how="inner")
        panel = set(map(str, tuning_panel[str(k)]))
        confirmatory = joined.loc[~joined.index.astype(str).isin(panel)]
        delta = (
            confirmatory["ndcg_at_10_candidate"]
            - confirmatory["ndcg_at_10_baseline"]
        ).to_numpy(dtype=np.float64)
        seed = CONFIRMATORY_BOOTSTRAP_SEED_BASE + k
        lower, upper = paired_bootstrap_ci(
            delta,
            seed=seed,
            iterations=CONFIRMATORY_BOOTSTRAP_ITERATIONS,
        )
        rows.append({
            "status": "CONFIRMATORY_AUXILIARY_TUNING_PANEL_EXCLUDED",
            "k": k,
            "model_id": LIGHTFM,
            "baseline_model_id": BASELINE,
            "users_before_exclusion": int(len(joined)),
            "tuning_panel_users_excluded": int(joined.index.astype(str).isin(panel).sum()),
            "users": int(len(confirmatory)),
            "delta_ndcg_mean": float(delta.mean()),
            "delta_ndcg_ci95": [lower, upper],
            "bootstrap": {
                "unit": "user",
                "method": "percentile",
                "seed": seed,
                "iterations": CONFIRMATORY_BOOTSTRAP_ITERATIONS,
            },
        })
    return rows


def common_user_k_diagnostic(metrics: pd.DataFrame) -> dict[str, Any]:
    """Compare absolute K5/K10 metrics on common users without claiming a K effect."""
    by_model_k = {
        (model_id, k): metrics.loc[
            (metrics["model_id"] == model_id) & (metrics["k"] == k)
        ].set_index("user_key")
        for model_id in (BASELINE, LIGHTFM)
        for k in (5, 10)
    }
    common = sorted(
        set(by_model_k[(LIGHTFM, 5)].index.astype(str))
        & set(by_model_k[(LIGHTFM, 10)].index.astype(str))
        & set(by_model_k[(BASELINE, 5)].index.astype(str))
        & set(by_model_k[(BASELINE, 10)].index.astype(str))
    )
    absolute = {
        model_id: {
            str(k): float(by_model_k[(model_id, k)].loc[common, "ndcg_at_10"].mean())
            for k in (5, 10)
        }
        for model_id in (BASELINE, LIGHTFM)
    }
    return {
        "status": "DIAGNOSTIC_COMMON_USERS_DIFFERENT_FUTURE_WINDOWS",
        "users": len(common),
        "same_users": True,
        "same_future_window": False,
        "absolute_ndcg_at_10": absolute,
        "delta_vs_b0": {
            str(k): float(
                (
                    by_model_k[(LIGHTFM, k)].loc[common, "ndcg_at_10"]
                    - by_model_k[(BASELINE, k)].loc[common, "ndcg_at_10"]
                ).mean()
            )
            for k in (5, 10)
        },
        "required_next_test": "SAME_USERS_SAME_FUTURE_WINDOW_PREFIX_ABLATION",
    }


def _quartile(series: pd.Series, prefix: str) -> pd.Series:
    ranked = series.rank(method="average", pct=True)
    bins = pd.cut(
        ranked,
        bins=[0.0, 0.25, 0.50, 0.75, 1.0],
        labels=[f"{prefix} Q1", f"{prefix} Q2", f"{prefix} Q3", f"{prefix} Q4"],
        include_lowest=True,
    )
    return bins.astype("string")


def source_history_depth(series: pd.Series) -> int:
    """Return the number of observed rating rows traversed through the last prefix signal."""
    return int(series.max()) + 1


def build_user_contexts(metrics: pd.DataFrame, candidate: pd.DataFrame) -> pd.DataFrame:
    prefixes = pd.read_parquet(
        ROOT / "outputs/recommendation-evidence/rec-ev-019a/validation-binary-prefixes.parquet"
    )
    windows = pd.read_parquet(
        ROOT / "outputs/recommendation-evidence/rec-ev-019a/validation-evaluation-windows.parquet"
    )
    structured = pd.read_parquet(
        ROOT / "outputs/recommendation-evidence/rec-ev-019b/structured-features.parquet",
        columns=["movie_id", "original_language", "feature_eligible"],
    )
    count_by_movie = candidate.set_index("movie_id")["b0_rating_count"]
    available_by_movie = candidate.set_index("movie_id")["structured_available"]

    prefixes["input_rating_count"] = prefixes["movie_id"].map(count_by_movie)
    prefixes["structured_available"] = prefixes["movie_id"].map(available_by_movie).eq(True)
    prefixes["valid_candidate_anchor"] = prefixes["input_rating_count"].notna()
    prefixes["valid_positive_anchor"] = (
        prefixes["valid_candidate_anchor"] & prefixes["binary_label"].eq(1)
    )
    prefixes["valid_negative_anchor"] = (
        prefixes["valid_candidate_anchor"] & prefixes["binary_label"].eq(-1)
    )
    prefix_context = prefixes.groupby(["user_key", "k"], observed=True).agg(
        prefix_positive=("binary_label", lambda value: int((value == 1).sum())),
        prefix_negative=("binary_label", lambda value: int((value == -1).sum())),
        valid_candidate_positive=("valid_positive_anchor", "sum"),
        valid_candidate_negative=("valid_negative_anchor", "sum"),
        prefix_utility_std=("relative_utility", "std"),
        input_popularity_median=("input_rating_count", "median"),
        input_structured_coverage=("structured_available", "mean"),
        observed_history_depth=("source_position", source_history_depth),
    ).reset_index()
    prefix_context["prefix_signal"] = np.where(
        (prefix_context["prefix_positive"] > 0) & (prefix_context["prefix_negative"] > 0),
        "원시 prefix 양쪽 신호",
        "원시 prefix 한쪽 신호",
    )
    prefix_context["raw_both_signals"] = (
        (prefix_context["prefix_positive"] > 0) & (prefix_context["prefix_negative"] > 0)
    )
    prefix_context["valid_candidate_both_signals"] = (
        (prefix_context["valid_candidate_positive"] > 0)
        & (prefix_context["valid_candidate_negative"] > 0)
    )
    prefix_context["candidate_anchor_loss_forces_fallback"] = (
        prefix_context["raw_both_signals"]
        & ~prefix_context["valid_candidate_both_signals"]
    )
    for k in (5, 10):
        mask = prefix_context["k"] == k
        prefix_context.loc[mask, "input_popularity_group"] = _quartile(
            prefix_context.loc[mask, "input_popularity_median"], "입력 인기도"
        ).values

    windows = windows.merge(
        structured[["movie_id", "original_language", "feature_eligible"]],
        on="movie_id",
        how="left",
    )
    windows["is_ko"] = windows["original_language"].eq("ko")
    window_context = windows.groupby(["user_key", "k"], observed=True).agg(
        evaluation_has_ko=("is_ko", "any"),
        evaluation_content_coverage=("feature_eligible", "mean"),
    ).reset_index()

    contexts = prefix_context.merge(window_context, on=["user_key", "k"], how="left")
    for k in (5, 10):
        mask = contexts["k"] == k
        contexts.loc[mask, "history_group"] = _quartile(
            contexts.loc[mask, "observed_history_depth"], "이력량"
        ).values
    contexts["ko_window_group"] = np.where(
        contexts["evaluation_has_ko"], "평가창에 한국어 원어 영화 있음", "없음"
    )
    return contexts


def fallback_anchor_diagnostics(
    metrics: pd.DataFrame,
    contexts: pd.DataFrame,
) -> list[dict[str, Any]]:
    lightfm = metrics.loc[
        metrics["model_id"] == LIGHTFM,
        ["user_key", "k", "fallback_user"],
    ].merge(
        contexts[
            [
                "user_key",
                "k",
                "raw_both_signals",
                "valid_candidate_both_signals",
                "candidate_anchor_loss_forces_fallback",
            ]
        ],
        on=["user_key", "k"],
        how="inner",
    )
    rows: list[dict[str, Any]] = []
    for k in (5, 10):
        group = lightfm.loc[lightfm["k"] == k]
        raw_both = group["raw_both_signals"].astype(bool)
        valid_both = group["valid_candidate_both_signals"].astype(bool)
        fallback = group["fallback_user"].astype(bool)
        anchor_loss = group["candidate_anchor_loss_forces_fallback"].astype(bool)
        rows.append({
            "k": k,
            "users": int(len(group)),
            "raw_both_signal_users": int(raw_both.sum()),
            "valid_candidate_both_anchor_users": int(valid_both.sum()),
            "raw_both_but_candidate_anchor_loss_users": int(anchor_loss.sum()),
            "raw_both_but_fallback_users": int((raw_both & fallback).sum()),
            "raw_one_sided_users": int((~raw_both).sum()),
            "raw_one_sided_fallback_users": int(((~raw_both) & fallback).sum()),
            "fallback_users": int(fallback.sum()),
            "fallback_is_design_precondition_not_signal_effect": True,
        })
    return rows


def cohort_summaries(
    metrics: pd.DataFrame,
    contexts: pd.DataFrame,
    best_by_k: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    enriched = metrics.merge(contexts, on=["user_key", "k"], how="left")
    rows: list[dict[str, Any]] = []
    dimensions = ["history_group", "input_popularity_group", "prefix_signal", "ko_window_group"]
    for k in (5, 10):
        best_model = str(best_by_k[str(k)]["model_id"])
        subset = enriched.loc[
            (enriched["k"] == k) & enriched["model_id"].isin([BASELINE, best_model])
        ]
        for dimension in dimensions:
            for cohort, group in subset.groupby(dimension, dropna=False, observed=True):
                for model_id, model_group in group.groupby("model_id", observed=True):
                    rows.append({
                        "k": k,
                        "best_model_id": best_model,
                        "dimension": dimension,
                        "cohort": str(cohort),
                        "model_id": str(model_id),
                        **aggregate_metrics(model_group),
                    })
    return rows


def item_slice_summary(prediction_path: Path, candidate: pd.DataFrame) -> list[dict[str, Any]]:
    windows = pd.read_parquet(
        ROOT / "outputs/recommendation-evidence/rec-ev-019a/validation-evaluation-windows.parquet"
    )
    structured = pd.read_parquet(
        ROOT / "outputs/recommendation-evidence/rec-ev-019b/structured-features.parquet",
        columns=["movie_id", "original_language", "release_year"],
    )
    item = candidate[["movie_id", "b0_rating_count"]].merge(structured, on="movie_id", how="left")
    item["popularity_group"] = _quartile(item["b0_rating_count"], "영화 인기도")
    item["language_group"] = np.where(item["original_language"].eq("ko"), "한국어 원어", "그 외")
    item["release_year_group"] = np.select(
        [item["release_year"].ge(2020), item["release_year"].notna()],
        ["RELEASE_2020_OR_LATER", "RELEASE_BEFORE_2020"],
        default="RELEASE_YEAR_UNKNOWN",
    )
    item["cold_item_group"] = np.where(
        item["b0_rating_count"].eq(0),
        "BASE_TRAIN_ZERO",
        "BASE_TRAIN_OBSERVED",
    )
    dimensions = (
        "popularity_group",
        "language_group",
        "release_year_group",
        "cold_item_group",
    )
    windows = windows.merge(item[["movie_id", *dimensions]], on="movie_id", how="left")
    windows = windows.loc[windows["movie_id"].isin(set(candidate["movie_id"].astype(int)))].copy()
    truth = {
        (str(row.user_key), int(row.k), int(row.movie_id)): {
            "is_positive": bool(row.is_positive),
            "is_negative": bool(row.is_negative),
            "popularity_group": row.popularity_group,
            "language_group": row.language_group,
            "release_year_group": row.release_year_group,
            "cold_item_group": row.cold_item_group,
        }
        for row in windows.itertuples(index=False)
    }
    matched: list[dict[str, Any]] = []
    seen_models: set[str] = set()
    seen_ks: set[int] = set()
    dataset = ds.dataset(prediction_path, format="parquet")
    for batch in dataset.to_batches(batch_size=131_072):
        frame = batch.to_pandas()
        seen_models.update(frame["model_id"].astype(str).unique())
        seen_ks.update(frame["k"].astype(int).unique())
        for row in frame.itertuples(index=False):
            key = (str(row.user_key), int(row.k), int(row.movie_id))
            target = truth.get(key)
            if target is not None:
                matched.append({
                    "model_id": str(row.model_id),
                    "k": int(row.k),
                    "rank": int(row.rank),
                    **target,
                })
    matched_frame = pd.DataFrame(matched)
    rows: list[dict[str, Any]] = []
    cohort_values = {
        "popularity_group": sorted(item["popularity_group"].dropna().astype(str).unique()),
        "language_group": ["한국어 원어", "그 외"],
        "release_year_group": list(RELEASE_YEAR_GROUPS),
        "cold_item_group": list(COLD_ITEM_GROUPS),
    }
    for dimension in dimensions:
        for k in sorted(seen_ks):
            cohorts = cohort_values[dimension]
            for model_id in sorted(seen_models):
                for cohort in cohorts:
                    group = matched_frame.loc[
                        (matched_frame["model_id"] == model_id)
                        & (matched_frame["k"] == k)
                        & (matched_frame[dimension] == cohort)
                    ]
                    positives = group.loc[group["is_positive"]]
                    negatives = group.loc[group["is_negative"]]
                    denominator = windows.loc[(windows["k"] == k) & (windows[dimension] == cohort)]
                    candidate_items = int((item[dimension].astype(str) == str(cohort)).sum())
                    total_positives = int(denominator["is_positive"].sum())
                    total_negatives = int(denominator["is_negative"].sum())
                    rows.append({
                        "model_id": model_id,
                        "k": k,
                        "dimension": dimension,
                        "cohort": str(cohort),
                        "candidate_item_total": candidate_items,
                        "observed_positive_total": total_positives,
                        "observed_positives_in_top500": int(len(positives)),
                        "positive_hit_at_500": float(len(positives) / total_positives) if total_positives else None,
                        "positive_hit_at_10": (
                            float((positives["rank"] <= 10).sum() / total_positives) if total_positives else None
                        ),
                        "observed_negative_total": total_negatives,
                        "observed_negatives_in_top500": int(len(negatives)),
                        "negative_top2_count": int((negatives["rank"] <= 2).sum()),
                        "negative_top2_rate_per_observed_negative": (
                            float((negatives["rank"] <= 2).sum() / total_negatives) if total_negatives else None
                        ),
                    })
    return rows


def core_item_slice_diagnostics(item_slices: list[dict[str, Any]]) -> dict[str, Any]:
    def row(k: int, dimension: str, cohort: str, model_id: str) -> dict[str, Any]:
        matches = [
            item
            for item in item_slices
            if int(item["k"]) == k
            and item["dimension"] == dimension
            and item["cohort"] == cohort
            and item["model_id"] == model_id
        ]
        if len(matches) != 1:
            raise RuntimeError(f"missing or duplicate item slice: K{k} {dimension} {cohort} {model_id}")
        return matches[0]

    positive_concentration: dict[str, Any] = {}
    korean_original_language: dict[str, Any] = {}
    release_year: dict[str, Any] = {}
    cold_item: dict[str, Any] = {}
    for k in (5, 10):
        popularity_rows = [
            item
            for item in item_slices
            if int(item["k"]) == k
            and item["dimension"] == "popularity_group"
            and item["model_id"] == BASELINE
        ]
        total_positive = sum(int(item["observed_positive_total"]) for item in popularity_rows)
        q4 = row(k, "popularity_group", "영화 인기도 Q4", BASELINE)
        q4_positive = int(q4["observed_positive_total"])
        positive_concentration[str(k)] = {
            "observed_positive_total": total_positive,
            "q4_observed_positive_total": q4_positive,
            "q4_share": float(q4_positive / total_positive) if total_positive else None,
        }

        ko_b0 = row(k, "language_group", "한국어 원어", BASELINE)
        ko_lightfm = row(k, "language_group", "한국어 원어", LIGHTFM)
        korean_original_language[str(k)] = {
            "observed_positive_total": int(ko_b0["observed_positive_total"]),
            "b0_positive_top500": int(ko_b0["observed_positives_in_top500"]),
            "lightfm_positive_top500": int(ko_lightfm["observed_positives_in_top500"]),
            "b0_positive_top10": int(
                round(float(ko_b0["positive_hit_at_10"] or 0.0) * int(ko_b0["observed_positive_total"]))
            ),
            "lightfm_positive_top10": int(
                round(float(ko_lightfm["positive_hit_at_10"] or 0.0) * int(ko_lightfm["observed_positive_total"]))
            ),
            "small_sample_no_inferiority_claim": True,
        }

        recent = row(k, "release_year_group", "RELEASE_2020_OR_LATER", BASELINE)
        release_year[str(k)] = {
            "release_year_gte_2020_candidate_items": int(recent["candidate_item_total"]),
            "observed_positive_total": int(recent["observed_positive_total"]),
            "quality_measured": int(recent["observed_positive_total"]) > 0,
        }

        zero = row(k, "cold_item_group", "BASE_TRAIN_ZERO", BASELINE)
        cold_item[str(k)] = {
            "base_train_zero_candidate_items": int(zero["candidate_item_total"]),
            "observed_positive_total": int(zero["observed_positive_total"]),
            "quality_measured": int(zero["observed_positive_total"]) > 0,
        }
    return {
        "positive_concentration": positive_concentration,
        "korean_original_language": korean_original_language,
        "release_year": release_year,
        "cold_item": cold_item,
    }


def load_titles() -> dict[int, str]:
    archive = Path(r"C:\higher\projects\MM\data\raw\ml-32m.zip")
    if not archive.is_file():
        return {}
    with zipfile.ZipFile(archive) as handle:
        with handle.open("ml-32m/movies.csv") as source:
            movies = pd.read_csv(source)
    return dict(zip(movies["movieId"].astype(int), movies["title"].astype(str)))


def example_users(
    metrics: pd.DataFrame,
    prediction_path: Path,
    best_by_k: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for k in (5, 10):
        best_model = str(best_by_k[str(k)]["model_id"])
        baseline = metrics.loc[
            (metrics["k"] == k) & (metrics["model_id"] == BASELINE)
        ].set_index("user_key")
        candidate = metrics.loc[
            (metrics["k"] == k) & (metrics["model_id"] == best_model)
        ].set_index("user_key")
        joined = candidate.join(baseline, lsuffix="_candidate", rsuffix="_baseline", how="inner")
        joined["delta"] = joined["ndcg_at_10_candidate"] - joined["ndcg_at_10_baseline"]
        for case, user_key in (
            ("가장 크게 개선", str(joined["delta"].idxmax())),
            ("가장 크게 악화", str(joined["delta"].idxmin())),
        ):
            row = joined.loc[user_key]
            selected.append({
                "k": k,
                "case": case,
                "user_key": user_key,
                "best_model_id": best_model,
                "baseline_ndcg_at_10": float(row["ndcg_at_10_baseline"]),
                "candidate_ndcg_at_10": float(row["ndcg_at_10_candidate"]),
                "delta_ndcg_at_10": float(row["delta"]),
                "baseline_harm_at_2": bool(row["harm_at_2_baseline"]),
                "candidate_harm_at_2": bool(row["harm_at_2_candidate"]),
            })

    keys = sorted({row["user_key"] for row in selected})
    models = sorted({BASELINE, *(row["best_model_id"] for row in selected)})
    dataset = ds.dataset(prediction_path, format="parquet")
    table = dataset.to_table(
        filter=(
            ds.field("user_key").isin(keys)
            & ds.field("model_id").isin(models)
            & (ds.field("rank") <= 2)
        )
    )
    predictions = table.to_pandas()
    windows = pd.read_parquet(
        ROOT / "outputs/recommendation-evidence/rec-ev-019a/validation-evaluation-windows.parquet",
        columns=["user_key", "k", "movie_id", "rating", "is_positive", "is_negative"],
    )
    truth = windows.set_index(["user_key", "k", "movie_id"]).to_dict("index")
    titles = load_titles()
    for case in selected:
        subset = predictions.loc[
            (predictions["user_key"] == case["user_key"])
            & (predictions["k"] == case["k"])
            & predictions["model_id"].isin([BASELINE, case["best_model_id"]])
        ].sort_values(["model_id", "rank"])
        top2: dict[str, list[dict[str, Any]]] = {}
        for model_id, group in subset.groupby("model_id", observed=True):
            top2[str(model_id)] = []
            for row in group.itertuples(index=False):
                observed = truth.get((str(row.user_key), int(row.k), int(row.movie_id)))
                top2[str(model_id)].append({
                    "rank": int(row.rank),
                    "movie_id": int(row.movie_id),
                    "title": titles.get(int(row.movie_id), f"MovieLens {int(row.movie_id)}"),
                    "offline_truth": (
                        "GOOD" if observed and observed["is_positive"]
                        else "BAD" if observed and observed["is_negative"]
                        else "OBSERVED_NEUTRAL" if observed
                        else "UNKNOWN"
                    ),
                    "rating": float(observed["rating"]) if observed else None,
                })
        case["top2"] = top2
        case["interpretation_boundary"] = "UNKNOWN은 싫어요가 아니라 이 오프라인 자료로 정답을 모른다는 뜻"
    return selected


def configure_charts() -> None:
    preferred = ["Malgun Gothic", "AppleGothic", "NanumGothic", "DejaVu Sans"]
    available = {font.name for font in font_manager.fontManager.ttflist}
    plt.rcParams["font.family"] = next(name for name in preferred if name in available)
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.facecolor"] = "white"
    plt.rcParams["axes.facecolor"] = "white"
    plt.rcParams["axes.edgecolor"] = "#B8C0CC"
    plt.rcParams["axes.titleweight"] = "bold"
    plt.rcParams["axes.titlesize"] = 14
    plt.rcParams["axes.labelsize"] = 10
    plt.rcParams["xtick.labelsize"] = 9
    plt.rcParams["ytick.labelsize"] = 9


def save_figure(fig: plt.Figure, name: str) -> None:
    FIGURE_ROOT.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURE_ROOT / name, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def chart_model_comparison(aggregate: list[dict[str, Any]]) -> None:
    frame = pd.DataFrame(aggregate)
    frame = frame.loc[frame["k"].isin([5, 10])]
    order = [model for model in MODEL_LABELS if model in set(frame["model_id"])]
    fig, axes = plt.subplots(2, 2, figsize=(13, 8.5), sharex=True)
    for column, k in enumerate((5, 10)):
        subset = frame.loc[frame["k"] == k].set_index("model_id").reindex(order)
        x = np.arange(len(subset))
        axes[0, column].bar(x, subset["ndcg_at_10"], color="#5B7FFF")
        axes[0, column].set_title(f"K={k} · 좋은 영화를 상단에 둔 정도")
        axes[0, column].set_ylabel("NDCG@10 · 높을수록 좋음")
        axes[0, column].grid(axis="y", alpha=0.18)
        axes[1, column].bar(x, subset["harm_at_2"], color="#E05263")
        axes[1, column].set_title(f"K={k} · 상위 2편 BAD 노출 위험")
        axes[1, column].set_ylabel("Harm@2 · 낮을수록 좋음")
        axes[1, column].yaxis.set_major_formatter(PercentFormatter(1.0))
        axes[1, column].grid(axis="y", alpha=0.18)
        axes[1, column].set_xticks(x, [MODEL_LABELS[item] for item in subset.index], rotation=30, ha="right")
    fig.suptitle("같은 모델도 순위 품질과 상위 2편 위험을 따로 봐야 한다", fontsize=16, fontweight="bold")
    fig.text(0.5, 0.01, "Validation만 사용 · 미평가 영화는 UNKNOWN · 제품 champion과 Locked Test는 열지 않음", ha="center", fontsize=9, color="#5E6673")
    fig.tight_layout(rect=(0, 0.035, 1, 0.96))
    save_figure(fig, "rec-ev-019c-model-comparison.png")


def chart_user_deltas(metrics: pd.DataFrame) -> None:
    fig, axes = plt.subplots(2, 1, figsize=(12.5, 8.2), sharex=True)
    for axis, k in zip(axes, (5, 10)):
        baseline = metrics.loc[
            (metrics["k"] == k) & (metrics["model_id"] == BASELINE), ["user_key", "ndcg_at_10"]
        ].set_index("user_key")["ndcg_at_10"]
        values: list[np.ndarray] = []
        labels: list[str] = []
        for model_id in MODEL_LABELS:
            if model_id == BASELINE:
                continue
            candidate = metrics.loc[
                (metrics["k"] == k) & (metrics["model_id"] == model_id), ["user_key", "ndcg_at_10"]
            ].set_index("user_key")["ndcg_at_10"]
            if candidate.empty:
                continue
            joined = pd.concat([candidate.rename("candidate"), baseline.rename("baseline")], axis=1).dropna()
            values.append((joined["candidate"] - joined["baseline"]).to_numpy())
            labels.append(MODEL_LABELS[model_id])
        axis.boxplot(values, tick_labels=labels, showfliers=False, whis=(10, 90), patch_artist=True,
                     boxprops={"facecolor": "#A5B7FF", "edgecolor": "#5B7FFF"},
                     medianprops={"color": "#E05263", "linewidth": 1.8})
        axis.axhline(0.0, color="#606975", linewidth=1.1)
        axis.set_title(f"K={k} · 같은 사용자에서 인기도 대비 NDCG 변화")
        axis.set_ylabel("Δ NDCG@10")
        axis.grid(axis="y", alpha=0.18)
    axes[-1].tick_params(axis="x", rotation=25)
    fig.suptitle("평균 하나보다 ‘누가 좋아지고 누가 나빠졌는지’가 중요하다", fontsize=16, fontweight="bold")
    fig.text(0.5, 0.01, "상자는 25~75%, 수염은 10~90% · 극단값은 사례표에서 별도 확인", ha="center", fontsize=9, color="#5E6673")
    fig.tight_layout(rect=(0, 0.035, 1, 0.96))
    save_figure(fig, "rec-ev-019c-user-delta-distribution.png")


def chart_benefit_harm(paired: list[dict[str, Any]]) -> None:
    frame = pd.DataFrame(paired)
    fig, axes = plt.subplots(2, 1, figsize=(11.5, 7.8), sharex=True)
    for axis, k in zip(axes, (5, 10)):
        subset = frame.loc[frame["k"] == k].copy()
        y = np.arange(len(subset))
        left = np.zeros(len(subset))
        for column, color, label in (
            ("benefit_rate", "#5B7FFF", "개선"),
            ("tie_rate", "#C7CDD6", "동률"),
            ("harm_rate", "#E05263", "악화"),
        ):
            values = subset[column].to_numpy()
            axis.barh(y, values, left=left, color=color, label=label)
            left += values
        axis.set_yticks(y, [MODEL_LABELS[item] for item in subset["model_id"]])
        axis.set_title(f"K={k} · 사용자별 인기도 대비 변화 비율")
        axis.xaxis.set_major_formatter(PercentFormatter(1.0))
        axis.set_xlim(0, 1)
        axis.grid(axis="x", alpha=0.18)
    axes[0].legend(frameon=False, ncol=3, loc="lower right")
    fig.suptitle("한 모델을 모든 사용자에게 쓰기 전에 개선·동률·악화 비율을 본다", fontsize=16, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    save_figure(fig, "rec-ev-019c-benefit-harm-rates.png")


def chart_stability(selection: Mapping[str, Any]) -> None:
    rows: list[dict[str, Any]] = []
    for model_id, by_k in selection["stability_panel"].items():
        for k, values in by_k.items():
            rows.append({"model_id": model_id, "k": int(k), **values})
    frame = pd.DataFrame(rows)
    fig, axis = plt.subplots(figsize=(8.8, 4.8))
    x = np.arange(len(frame))
    axis.errorbar(x, frame["ndcg_mean"], yerr=frame["ndcg_std"], fmt="o", capsize=5,
                  color="#5B7FFF", ecolor="#E05263", linewidth=2)
    labels = [f"{MODEL_LABELS[row.model_id]}\nK={row.k}" for row in frame.itertuples(index=False)]
    axis.set_xticks(x, labels)
    axis.set_ylabel("256명/K tuning panel · 5-seed NDCG@10 평균 ± 표준편차")
    axis.set_title("안정성은 전체 Validation이 아니라 tuning panel에서만 측정했다")
    axis.grid(axis="y", alpha=0.18)
    fig.tight_layout()
    save_figure(fig, "rec-ev-019c-stability.png")


def chart_item_slices(
    item_slices: list[dict[str, Any]],
    best_by_k: Mapping[str, Mapping[str, Any]],
) -> None:
    frame = pd.DataFrame(item_slices)
    fig, axes = plt.subplots(2, 2, figsize=(13, 8.3))
    for row_index, k in enumerate((5, 10)):
        best_model = str(best_by_k[str(k)]["model_id"])
        for column_index, dimension in enumerate(("popularity_group", "language_group")):
            axis = axes[row_index, column_index]
            subset = frame.loc[
                (frame["k"] == k)
                & (frame["dimension"] == dimension)
                & frame["model_id"].isin([BASELINE, best_model])
            ].copy()
            cohorts = sorted(subset["cohort"].unique())
            x = np.arange(len(cohorts))
            width = 0.36
            for offset, (model_id, color) in enumerate(((BASELINE, "#AAB2BE"), (best_model, "#5B7FFF"))):
                values = (
                    subset.loc[subset["model_id"] == model_id]
                    .set_index("cohort")["positive_hit_at_10"]
                    .reindex(cohorts)
                )
                axis.bar(
                    x + (offset - 0.5) * width,
                    values,
                    width=width,
                    color=color,
                    label=MODEL_LABELS[model_id],
                )
            axis.set_xticks(x, cohorts, rotation=20, ha="right")
            axis.yaxis.set_major_formatter(PercentFormatter(1.0))
            axis.set_ylabel("관측 GOOD의 Top-10 적중률")
            axis.set_title(f"K={k} · {'영화 인기도' if dimension == 'popularity_group' else '원어'} 구간")
            axis.grid(axis="y", alpha=0.18)
            axis.legend(frameon=False, fontsize=8)
    fig.suptitle("관측 positive 자체가 Q4에 약 96% 집중돼 저인기 구간은 검정력이 부족하다", fontsize=16, fontweight="bold")
    fig.text(0.5, 0.01, "한국어 원어는 TMDB original_language=ko proxy · 작은 표본의 Top-10 0을 열등 확정으로 해석하지 않음", ha="center", fontsize=9, color="#5E6673")
    fig.tight_layout(rect=(0, 0.035, 1, 0.96))
    save_figure(fig, "rec-ev-019c-item-slices.png")


def run_analysis() -> dict[str, Any]:
    required = [
        RUN_ROOT / "validation-user-metrics.parquet",
        RUN_ROOT / "trial-user-metrics.parquet",
        RUN_ROOT / "validation-predictions.parquet",
        RUN_ROOT / "validation-selection.json",
        RUN_ROOT / "candidate-core-final.parquet",
        RUN_ROOT / "resource-summary.json",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"019C Validation artifacts are incomplete: {missing}")

    metrics = pd.read_parquet(RUN_ROOT / "validation-user-metrics.parquet")
    candidate = pd.read_parquet(RUN_ROOT / "candidate-core-final.parquet")
    selection = json.loads((RUN_ROOT / "validation-selection.json").read_text(encoding="utf-8"))
    resource = json.loads((RUN_ROOT / "resource-summary.json").read_text(encoding="utf-8"))
    aggregate = [
        {"model_id": str(model_id), "k": int(k), **aggregate_metrics(group)}
        for (model_id, k), group in metrics.groupby(["model_id", "k"], observed=True)
    ]
    paired = paired_summary(metrics)
    contexts = build_user_contexts(metrics, candidate)
    confirmatory_paired = confirmatory_paired_summary(metrics, selection["tuning_panel"])
    common_k = common_user_k_diagnostic(metrics)
    fallback_anchors = fallback_anchor_diagnostics(metrics, contexts)
    cohorts = cohort_summaries(metrics, contexts, selection["single_best_per_k"])
    item_slices = item_slice_summary(RUN_ROOT / "validation-predictions.parquet", candidate)
    item_slice_diagnostics = core_item_slice_diagnostics(item_slices)
    cases = example_users(metrics, RUN_ROOT / "validation-predictions.parquet", selection["single_best_per_k"])

    summary = {
        "schema_version": 1,
        "evidence_id": "REC-EV-019C-ANALYSIS",
        "status": "VALIDATION_ANALYZED_LOCKED_TEST_UNOPENED",
        "interpretation": {
            "movie_lens_users_are_feelm_users": False,
            "unobserved_means_dislike": False,
            "locked_test_opened": False,
            "locked_test_used": False,
            "champion": None,
            "champion_selected": False,
            "product_policy_changed": False,
            "product_policy_updated": False,
            "post_hoc_results_are_confirmatory": False,
            "tuning_panel_excluded_paired_is_confirmatory_auxiliary": True,
        },
        "aggregate": aggregate,
        "paired_vs_b0": paired,
        "confirmatory_tuning_panel_excluded_vs_b0": confirmatory_paired,
        "common_user_k_diagnostic": common_k,
        "fallback_anchor_diagnostics": fallback_anchors,
        "cohorts_for_validation_best_vs_b0": cohorts,
        "item_slices": item_slices,
        "core_item_slice_diagnostics": item_slice_diagnostics,
        "example_users": cases,
        "selected_trials": selection["per_model_per_k"],
        "validation_best_per_k": selection["single_best_per_k"],
        "stability": selection["stability_panel"],
        "resource": {
            "wall_clock_seconds": resource["wall_clock_seconds"],
            "peak_rss_bytes": resource["peak_rss_bytes"],
            "artifact_bytes": resource["artifact_bytes"],
            "budget_counters": resource.get("budget_counters", {}),
        },
        "reproducibility": {
            "raw_artifacts_git_tracked": False,
            "raw_artifact_root": "outputs/recommendation-evidence/rec-ev-019c",
            "external_artifact_uri": None,
            "commit_only_third_party_reproduction": False,
            "limitation": (
                "Raw Parquet artifacts are ignored under outputs and no external artifact URI is recorded; "
                "the commit alone cannot reproduce the analysis on a third-party checkout."
            ),
        },
    }
    atomic_write_json(SUMMARY_PATH, summary)
    configure_charts()
    chart_model_comparison(aggregate)
    chart_user_deltas(metrics)
    chart_benefit_harm(paired)
    chart_stability(selection)
    chart_item_slices(item_slices, selection["single_best_per_k"])
    validation_manifest = ROOT / "docs/recommendation/evidence/manifests/rec-ev-019c-validation.json"
    figure_paths = [
        FIGURE_ROOT / "rec-ev-019c-model-comparison.png",
        FIGURE_ROOT / "rec-ev-019c-user-delta-distribution.png",
        FIGURE_ROOT / "rec-ev-019c-benefit-harm-rates.png",
        FIGURE_ROOT / "rec-ev-019c-stability.png",
        FIGURE_ROOT / "rec-ev-019c-item-slices.png",
    ]
    atomic_write_json(MANIFEST_PATH, {
        "schema_version": 1,
        "evidence_id": "REC-EV-019C-ANALYSIS",
        "status": "PASS_VALIDATION_ANALYSIS_ONLY",
        "source_validation_manifest": {
            "path": validation_manifest.resolve().relative_to(ROOT.resolve()).as_posix(),
            "sha256": sha256_file(validation_manifest),
        },
        "artifacts": [artifact_entry(SUMMARY_PATH), *[artifact_entry(path) for path in figure_paths]],
        "validation": {
            "locked_test_opened": False,
            "locked_test_used": False,
            "champion": None,
            "champion_selected": False,
            "product_policy_changed": False,
            "product_policy_updated": False,
            "post_hoc_results_are_confirmatory": False,
            "tuning_panel_excluded_paired_is_confirmatory_auxiliary": True,
        },
        "reproducibility": summary["reproducibility"],
    })
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, default=RUN_ROOT)
    args = parser.parse_args()
    if args.run_root.resolve() != RUN_ROOT.resolve():
        raise ValueError("REC-EV-019C analysis accepts only the contracted Validation output root")
    summary = run_analysis()
    print(json.dumps({
        "status": summary["status"],
        "summary": SUMMARY_PATH.resolve().relative_to(ROOT.resolve()).as_posix(),
        "manifest": MANIFEST_PATH.resolve().relative_to(ROOT.resolve()).as_posix(),
        "locked_test_opened": False,
        "locked_test_used": False,
        "champion": None,
        "champion_selected": False,
        "product_policy_updated": False,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
