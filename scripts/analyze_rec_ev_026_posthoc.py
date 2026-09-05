#!/usr/bin/env python3
"""Read-only post-hoc diagnostics for the sealed REC-EV-026 result.

This module must never be used as a confirmatory result.  It summarizes the
already exposed labels, estimates the sample size implied by the locked
precision gate, and describes user/movie slices without changing the sealed
REC-EV-026 directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SEALED = ROOT / "outputs/recommendation-evidence/rec-ev-026"
DEFAULT_OUTPUT = ROOT / "outputs/recommendation-evidence/rec-ev-026-posthoc"
EXECUTION_CONTRACT = ROOT / "docs/recommendation/contracts/rec-ev-026-content-cf-alignment-execution.json"
DESIGN_CONTRACT = ROOT / "docs/recommendation/contracts/rec-ev-026-content-cf-alignment-design.json"
TARGET_MARGIN = 0.02
INCREMENTAL_UTILITY_MARGIN = 0.005
INCREMENTAL_SAFETY_MARGIN = 0.01
PRECISION_LIMIT = 0.05


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def atomic_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8")
    temporary.replace(path)


def equal_count_quartile(frame: pd.DataFrame, value: str) -> pd.Series:
    ordered = frame.sort_values(["evidence_id", value, "user_key022"], kind="stable").copy()
    ordered["_ordinal"] = ordered.groupby("evidence_id").cumcount()
    ordered["_size"] = ordered.groupby("evidence_id")["user_key022"].transform("size")
    ordered["_quartile"] = np.minimum((4 * ordered["_ordinal"] // ordered["_size"]) + 1, 4)
    return ordered.set_index(["evidence_id", "user_key022"])["_quartile"].astype(int)


def profile_segments(profile: pd.DataFrame) -> pd.DataFrame:
    users = (
        profile.groupby(["evidence_id", "user_key022"], as_index=False)
        .agg(
            profile_items=("movie_id", "size"),
            profile_rating_mean=("rating", "mean"),
            profile_rating_std=("rating", "std"),
            profile_rating_min=("rating", "min"),
            profile_rating_max=("rating", "max"),
        )
        .sort_values(["evidence_id", "user_key022"], kind="stable")
        .reset_index(drop=True)
    )
    if not bool((users["profile_items"] == 14).all()):
        raise RuntimeError("profile cardinality drift")
    quartiles = equal_count_quartile(users, "profile_rating_std")
    users["dispersion_quartile"] = [
        f"Q{int(quartiles.loc[(row.evidence_id, row.user_key022)])}"
        for row in users.itertuples(index=False)
    ]
    return users


def user_slice_summary(metrics: pd.DataFrame, users: pd.DataFrame) -> pd.DataFrame:
    target = metrics.loc[(metrics["active"]) & (metrics["domain"] == "TARGET")].copy()
    per_user = (
        target.groupby(
            ["evidence_id", "user_key022", "head", "encoding", "k"],
            as_index=False,
        )[["utility_improvement", "safety_improvement"]]
        .mean()
        .merge(
            users[["evidence_id", "user_key022", "dispersion_quartile"]],
            on=["evidence_id", "user_key022"],
            how="left",
            validate="many_to_one",
        )
    )
    per_user["positive_utility"] = per_user["utility_improvement"] > 0
    per_user["positive_safety"] = per_user["safety_improvement"] > 0
    return (
        per_user.groupby(
            ["evidence_id", "head", "encoding", "k", "dispersion_quartile"],
            as_index=False,
        )
        .agg(
            users=("user_key022", "nunique"),
            mean_utility_improvement=("utility_improvement", "mean"),
            mean_safety_improvement=("safety_improvement", "mean"),
            positive_utility_share=("positive_utility", "mean"),
            positive_safety_share=("positive_safety", "mean"),
        )
        .sort_values(
            ["evidence_id", "head", "encoding", "k", "dispersion_quartile"],
            kind="stable",
        )
        .reset_index(drop=True)
    )


def model_summary(metrics: pd.DataFrame) -> pd.DataFrame:
    target = metrics.loc[(metrics["active"]) & (metrics["domain"] == "TARGET")]
    per_user = (
        target.groupby(
            ["evidence_id", "user_key022", "head", "encoding", "k"], as_index=False
        )[["utility_improvement", "safety_improvement"]]
        .mean()
    )
    per_user["positive_utility"] = per_user["utility_improvement"] > 0
    per_user["positive_safety"] = per_user["safety_improvement"] > 0
    return (
        per_user.groupby(["evidence_id", "head", "encoding", "k"], as_index=False)
        .agg(
            users=("user_key022", "nunique"),
            mean_utility_improvement=("utility_improvement", "mean"),
            mean_safety_improvement=("safety_improvement", "mean"),
            positive_utility_share=("positive_utility", "mean"),
            positive_safety_share=("positive_safety", "mean"),
        )
        .sort_values(["evidence_id", "head", "encoding", "k"], kind="stable")
        .reset_index(drop=True)
    )


def load_years() -> pd.DataFrame:
    execution = read_json(EXECUTION_CONTRACT)
    structured_spec = execution["allowed_input_artifacts"]["structured_features"]
    structured_path = Path(structured_spec["path"])
    if not structured_path.is_absolute():
        structured_path = ROOT / structured_path
    if sha256_file(structured_path) != structured_spec["sha256"]:
        raise RuntimeError("structured feature hash drift")
    years = pd.read_parquet(structured_path, columns=["movie_id", "release_year"])
    if years["movie_id"].duplicated().any():
        raise RuntimeError("duplicate structured movie_id")
    return years


def release_bucket(evidence_id: str, year: float) -> str:
    if not math.isfinite(float(year)):
        return "UNKNOWN"
    value = int(year)
    if evidence_id == "REC-EV-026B" and 2020 <= value <= 2023:
        return str(value)
    if value < 2000:
        return "PRE_2000"
    if value < 2010:
        return "2000_2009"
    if value < 2020:
        return "2010_2019"
    if value <= 2023:
        return "2020_2023"
    return "OTHER"


def movie_slice_summary(ranks: pd.DataFrame, labels: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    target = ranks.loc[(ranks["active"]) & (ranks["domain"] == "TARGET")].copy()
    target = target.merge(load_years(), on="movie_id", how="left", validate="many_to_one")
    target["release_bucket"] = [
        release_bucket(evidence_id, year)
        for evidence_id, year in zip(target["evidence_id"], target["release_year"])
    ]
    target["top2"] = target["rank"] <= 2
    q = labels.loc[labels["role"] == "TARGET", ["evidence_id", "user_key022", "movie_id", "q_eval"]]
    if q.duplicated(["evidence_id", "user_key022", "movie_id"]).any():
        raise RuntimeError("target evaluation label identity drift")
    target = target.merge(
        q,
        on=["evidence_id", "user_key022", "movie_id"],
        how="left",
        validate="many_to_one",
    )
    if target["q_eval"].isna().any():
        raise RuntimeError("missing target q_eval")
    target["selected_q"] = target["q_eval"].where(target["top2"])
    slices = (
        target.groupby(
            ["evidence_id", "head", "encoding", "k", "release_bucket"],
            as_index=False,
        )
        .agg(
            candidate_rows=("movie_id", "size"),
            unique_movies=("movie_id", "nunique"),
            top2_rows=("top2", "sum"),
            top2_rate=("top2", "mean"),
            selected_q_mean=("selected_q", "mean"),
        )
        .sort_values(
            ["evidence_id", "head", "encoding", "k", "release_bucket"],
            kind="stable",
        )
        .reset_index(drop=True)
    )
    selected = target.loc[target["top2"]]
    movie_counts = (
        selected.groupby(["evidence_id", "head", "encoding", "k", "movie_id"], as_index=False)
        .size()
        .rename(columns={"size": "selections"})
    )
    concentration_rows: list[dict[str, Any]] = []
    for key, group in movie_counts.groupby(["evidence_id", "head", "encoding", "k"], sort=True):
        counts = group["selections"].to_numpy(dtype=float)
        total = float(counts.sum())
        shares = counts / total
        concentration_rows.append(
            {
                "evidence_id": key[0],
                "head": key[1],
                "encoding": key[2],
                "k": int(key[3]),
                "top2_rows": int(total),
                "unique_selected_movies": int(len(counts)),
                "top10_movie_share": float(np.sort(counts)[-10:].sum() / total),
                "selection_hhi": float(np.square(shares).sum()),
            }
        )
    concentration = pd.DataFrame(concentration_rows).sort_values(
        ["evidence_id", "head", "encoding", "k"], kind="stable", ignore_index=True
    )
    return slices, concentration


def precision_diagnostics(result: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    intervals = pd.DataFrame(result["simultaneous_intervals"])
    intervals["current_users"] = intervals["experiment"].map(result["users"]).astype(int)
    intervals["approx_users_for_half_width_lt_0_05"] = np.ceil(
        intervals["current_users"] * np.square(intervals["half_width"] / PRECISION_LIMIT)
    ).astype(int)
    imprecise = intervals.loc[intervals["half_width"] >= PRECISION_LIMIT].copy()
    e5bpr_target = intervals.loc[
        (
            (intervals["head"] == "E5_TO_BPR")
            & (intervals["kind"] == "ABSOLUTE")
            & (intervals["class"] == "TARGET_IMPROVEMENT")
        )
        | ((intervals["kind"] == "INCREMENTAL") & (intervals["domain"] == "TARGET"))
    ].copy()

    def margin(row: pd.Series) -> float:
        if row["kind"] == "ABSOLUTE":
            return TARGET_MARGIN
        if str(row["endpoint"]).startswith("UTILITY_"):
            return INCREMENTAL_UTILITY_MARGIN
        return INCREMENTAL_SAFETY_MARGIN

    e5bpr_target["locked_margin"] = e5bpr_target.apply(margin, axis=1)
    e5bpr_target["point_estimate_meets_margin"] = e5bpr_target["mean"] >= e5bpr_target["locked_margin"]
    feasibility = []
    for (experiment, kind), group in e5bpr_target.groupby(["experiment", "kind"], sort=True):
        feasibility.append(
            {
                "experiment": experiment,
                "kind": kind,
                "contrasts": int(len(group)),
                "point_estimates_below_locked_margin": int((~group["point_estimate_meets_margin"]).sum()),
                "all_point_estimates_meet_locked_margin": bool(group["point_estimate_meets_margin"].all()),
            }
        )
    summary: dict[str, Any] = {
        "simultaneous_contrasts": int(len(intervals)),
        "imprecise_contrasts_half_width_gte_0_05": int(len(imprecise)),
        "imprecise_by_experiment": {
            str(key): int(value)
            for key, value in imprecise.groupby("experiment").size().to_dict().items()
        },
        "approx_required_users_for_imprecise_contrasts": {
            "minimum": int(imprecise["approx_users_for_half_width_lt_0_05"].min()),
            "median": int(math.ceil(float(imprecise["approx_users_for_half_width_lt_0_05"].median()))),
            "maximum": int(imprecise["approx_users_for_half_width_lt_0_05"].max()),
            "method": "N_CURRENT_TIMES_SQUARED_HALF_WIDTH_RATIO; PLANNING_ONLY; CRITICAL_VALUE_AND_VARIANCE_HELD_FIXED",
        },
        "e5_to_bpr_point_estimate_margin_check": feasibility,
    }
    columns = [
        "index", "experiment", "kind", "class", "baseline", "domain", "head", "encoding",
        "k", "endpoint", "mean", "low", "high", "half_width", "se", "current_users",
        "approx_users_for_half_width_lt_0_05",
    ]
    return imprecise[columns].sort_values("index", kind="stable").reset_index(drop=True), summary


def validate_sealed_result(result: dict[str, Any], sealed_inventory: dict[str, str]) -> None:
    required = {
        "content-cf-alignment-result.json",
        "content-cf-alignment-result.integrity.json",
        "panel-metrics.parquet",
        "score-rank.parquet",
        "cache/evaluation-labels.parquet",
        "cache/profile-ratings.parquet",
    }
    if not required.issubset(sealed_inventory):
        raise RuntimeError("sealed result inventory incomplete")
    if result["status"] != "INCONCLUSIVE_PRECISION_OR_NONESTIMABLE":
        raise RuntimeError("unexpected sealed result status")
    if result["locked_test_opened"] or result["final_reserve_opened"]:
        raise RuntimeError("forbidden reserve-open flag")
    if result["product_policy_updated"] or result["champion"] is not None:
        raise RuntimeError("product decision leakage")
    if result["timestamp_bytes_parsed"] != 0:
        raise RuntimeError("timestamp access drift")
    if len(result["simultaneous_intervals"]) != 312:
        raise RuntimeError("contrast family drift")


def run(output_root: Path) -> dict[str, Any]:
    before = inventory(SEALED)
    result = read_json(SEALED / "content-cf-alignment-result.json")
    validate_sealed_result(result, before)
    profile = pd.read_parquet(SEALED / "cache/profile-ratings.parquet")
    labels = pd.read_parquet(SEALED / "cache/evaluation-labels.parquet")
    ranks = pd.read_parquet(SEALED / "score-rank.parquet")
    metrics = pd.read_parquet(SEALED / "panel-metrics.parquet")

    users = profile_segments(profile)
    user_slices = user_slice_summary(metrics, users)
    models = model_summary(metrics)
    movie_slices, concentration = movie_slice_summary(ranks, labels)
    imprecise, precision = precision_diagnostics(result)

    after = inventory(SEALED)
    if before != after:
        raise RuntimeError("sealed REC-EV-026 artifacts changed during post-hoc analysis")

    atomic_csv(output_root / "profile-user-segments.csv", users)
    atomic_csv(output_root / "user-slice-summary.csv", user_slices)
    atomic_csv(output_root / "target-model-summary.csv", models)
    atomic_csv(output_root / "movie-release-slice-summary.csv", movie_slices)
    atomic_csv(output_root / "movie-selection-concentration.csv", concentration)
    atomic_csv(output_root / "imprecise-contrasts.csv", imprecise)
    summary = {
        "schema_version": 1,
        "evidence_id": "REC-EV-026-POSTHOC",
        "status": "POSTHOC_DESCRIPTIVE_ONLY",
        "source_result_status": result["status"],
        "claim_boundary": [
            "DO_NOT_USE_FOR_CONFIRMATORY_PASS",
            "DO_NOT_SELECT_K_ENCODING_OR_MODEL_CHAMPION_FROM_THESE_LABELS",
            "MOVIELENS_USERS_ARE_NOT_KOREAN_USERS",
            "NO_PRODUCTION_POLICY_UPDATE",
        ],
        "sealed_source": {
            "path": SEALED.relative_to(ROOT).as_posix(),
            "file_count": len(before),
            "inventory_sha256": hashlib.sha256(
                json.dumps(before, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "unchanged_after_analysis": True,
        },
        "users": result["users"],
        "precision": precision,
        "tables": {
            "profile_user_segments": "profile-user-segments.csv",
            "user_slice_summary": "user-slice-summary.csv",
            "target_model_summary": "target-model-summary.csv",
            "movie_release_slice_summary": "movie-release-slice-summary.csv",
            "movie_selection_concentration": "movie-selection-concentration.csv",
            "imprecise_contrasts": "imprecise-contrasts.csv",
        },
    }
    atomic_json(output_root / "posthoc-analysis.json", summary)
    if before != inventory(SEALED):
        raise RuntimeError("sealed REC-EV-026 artifacts changed after post-hoc output write")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    summary = run(args.output_root.resolve())
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
