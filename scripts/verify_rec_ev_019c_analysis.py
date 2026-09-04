#!/usr/bin/env python3
"""Independent integrity and boundary checks for REC-EV-019C analysis."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

from analyze_rec_ev_019c_validation import (
    BASELINE,
    CONFIRMATORY_BOOTSTRAP_ITERATIONS,
    CONFIRMATORY_BOOTSTRAP_SEED_BASE,
    LIGHTFM,
    ROOT,
    sha256_file,
)
from verify_rec_ev_019c_validation import verify_manifest as verify_validation_manifest


DEFAULT_MANIFEST = ROOT / "docs/recommendation/evidence/manifests/rec-ev-019c-analysis.json"


def _resolve_repo_path(value: str, *, root: Path) -> Path:
    path = (root / value).resolve()
    path.relative_to(root.resolve())
    return path


def _close(actual: float, expected: float, *, tolerance: float = 1e-10) -> bool:
    return bool(np.isclose(actual, expected, rtol=0.0, atol=tolerance, equal_nan=True))


def _paired_bootstrap_ci(values: np.ndarray, *, seed: int, iterations: int) -> tuple[float, float]:
    clean = np.asarray(values, dtype=np.float64)
    clean = clean[np.isfinite(clean)]
    rng = np.random.default_rng(seed)
    means = np.empty(iterations, dtype=np.float64)
    chunk = 500
    for start in range(0, iterations, chunk):
        stop = min(iterations, start + chunk)
        indices = rng.integers(0, len(clean), size=(stop - start, len(clean)))
        means[start:stop] = clean[indices].mean(axis=1)
    lower, upper = np.quantile(means, [0.025, 0.975])
    return float(lower), float(upper)


def _quartile(series: pd.Series) -> pd.Series:
    ranked = series.rank(method="average", pct=True)
    return pd.cut(
        ranked,
        bins=[0.0, 0.25, 0.50, 0.75, 1.0],
        labels=["영화 인기도 Q1", "영화 인기도 Q2", "영화 인기도 Q3", "영화 인기도 Q4"],
        include_lowest=True,
    ).astype("string")


def _find_summary_row(
    rows: list[dict[str, Any]],
    *,
    k: int,
    dimension: str,
    cohort: str,
    model_id: str,
) -> dict[str, Any]:
    matches = [
        row
        for row in rows
        if int(row["k"]) == k
        and row["dimension"] == dimension
        and row["cohort"] == cohort
        and row["model_id"] == model_id
    ]
    if len(matches) != 1:
        raise RuntimeError(f"missing or duplicate core slice: K{k} {dimension} {cohort} {model_id}")
    return matches[0]


def _recompute_korean_prediction_hits(
    prediction_path: Path,
    truth: set[tuple[str, int, int]],
) -> dict[tuple[str, int], dict[str, int]]:
    result = {
        (model_id, k): {"top500": 0, "top10": 0}
        for model_id in (BASELINE, LIGHTFM)
        for k in (5, 10)
    }
    dataset = ds.dataset(prediction_path, format="parquet")
    scanner = dataset.scanner(
        columns=["user_key", "k", "model_id", "rank", "movie_id"],
        filter=ds.field("model_id").isin([BASELINE, LIGHTFM]),
        batch_size=131_072,
    )
    for batch in scanner.to_batches():
        frame = batch.to_pandas()
        for row in frame.itertuples(index=False):
            key = (str(row.user_key), int(row.k), int(row.movie_id))
            bucket_key = (str(row.model_id), int(row.k))
            if bucket_key not in result or key not in truth:
                continue
            bucket = result[bucket_key]
            bucket["top500"] += 1
            bucket["top10"] += int(row.rank) <= 10
    return result


def verify_analysis_manifest(path: Path, *, root: Path = ROOT) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("status") != "PASS_VALIDATION_ANALYSIS_ONLY":
        raise RuntimeError("analysis manifest status is not PASS_VALIDATION_ANALYSIS_ONLY")
    boundary = manifest.get("validation", {})
    if boundary != {
        "champion": None,
        "champion_selected": False,
        "locked_test_opened": False,
        "locked_test_used": False,
        "post_hoc_results_are_confirmatory": False,
        "product_policy_changed": False,
        "product_policy_updated": False,
        "tuning_panel_excluded_paired_is_confirmatory_auxiliary": True,
    }:
        raise RuntimeError("analysis boundary changed or is incomplete")

    validation_ref = manifest["source_validation_manifest"]
    validation_path = _resolve_repo_path(validation_ref["path"], root=root)
    if sha256_file(validation_path) != validation_ref["sha256"]:
        raise RuntimeError("source Validation manifest checksum differs")
    verify_validation_manifest(validation_path, root=root)

    artifacts: dict[str, Path] = {}
    for row in manifest["artifacts"]:
        artifact = _resolve_repo_path(row["path"], root=root)
        if not artifact.is_file():
            raise RuntimeError(f"analysis artifact is missing: {row['path']}")
        if artifact.stat().st_size != int(row["bytes"]):
            raise RuntimeError(f"analysis artifact size differs: {row['path']}")
        if sha256_file(artifact) != row["sha256"]:
            raise RuntimeError(f"analysis artifact checksum differs: {row['path']}")
        artifacts[row["path"]] = artifact

    summary_path = next(path for name, path in artifacts.items() if name.endswith("analysis-summary.json"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("status") != "VALIDATION_ANALYZED_LOCKED_TEST_UNOPENED":
        raise RuntimeError("analysis summary status differs")
    interpretation = summary["interpretation"]
    required_false = (
        "movie_lens_users_are_feelm_users", "unobserved_means_dislike", "locked_test_opened",
        "locked_test_used", "champion_selected", "product_policy_changed", "product_policy_updated",
        "post_hoc_results_are_confirmatory",
    )
    if any(interpretation.get(key) is not False for key in required_false):
        raise RuntimeError("analysis interpretation boundary is not fail-closed")
    if interpretation.get("champion") is not None:
        raise RuntimeError("analysis selected a champion")
    if interpretation.get("tuning_panel_excluded_paired_is_confirmatory_auxiliary") is not True:
        raise RuntimeError("confirmatory auxiliary scope is not explicit")

    reproducibility = summary.get("reproducibility", {})
    if reproducibility.get("raw_artifacts_git_tracked") is not False:
        raise RuntimeError("raw output tracking boundary changed")
    if reproducibility.get("external_artifact_uri") is not None:
        raise RuntimeError("unexpected external artifact URI")
    if reproducibility.get("commit_only_third_party_reproduction") is not False:
        raise RuntimeError("commit-only reproduction was overstated")
    if manifest.get("reproducibility") != reproducibility:
        raise RuntimeError("manifest and summary reproducibility limits differ")

    metrics = pd.read_parquet(
        root / "outputs/recommendation-evidence/rec-ev-019c/validation-user-metrics.parquet"
    )
    expected_groups = int(metrics.groupby(["model_id", "k"], observed=True).ngroups)
    if len(summary["aggregate"]) != expected_groups:
        raise RuntimeError("aggregate row count differs from Validation metrics")
    for row in summary["aggregate"]:
        group = metrics.loc[
            (metrics["model_id"] == row["model_id"]) & (metrics["k"] == int(row["k"]))
        ]
        if int(row["users"]) != int(group["user_key"].nunique()):
            raise RuntimeError("aggregate user count differs")
        if not _close(float(row["ndcg_at_10"]), float(group["ndcg_at_10"].mean())):
            raise RuntimeError("aggregate NDCG differs")
        if not _close(float(row["harm_at_2"]), float(group["harm_at_2"].astype(float).mean())):
            raise RuntimeError("aggregate Harm@2 differs")

    paired = summary["paired_vs_b0"]
    models_by_k = {
        int(k): set(metrics.loc[metrics["k"] == int(k), "model_id"])
        for k in (5, 10)
    }
    expected_paired = sum(len(models - {BASELINE}) for models in models_by_k.values())
    if len(paired) != expected_paired:
        raise RuntimeError("paired comparison count differs")
    for row in paired:
        total = float(row["benefit_rate"]) + float(row["tie_rate"]) + float(row["harm_rate"])
        if not _close(total, 1.0):
            raise RuntimeError("benefit/tie/harm rates do not sum to one")
        lower, upper = map(float, row["delta_ndcg_ci95"])
        if lower > upper:
            raise RuntimeError("paired NDCG confidence interval is reversed")

    selection = json.loads(
        (root / "outputs/recommendation-evidence/rec-ev-019c/validation-selection.json").read_text(
            encoding="utf-8"
        )
    )
    confirmatory_rows = summary.get("confirmatory_tuning_panel_excluded_vs_b0", [])
    if len(confirmatory_rows) != 2:
        raise RuntimeError("confirmatory tuning-panel-excluded paired rows differ")
    for k in (5, 10):
        panel = set(map(str, selection["tuning_panel"][str(k)]))
        if len(panel) != 256:
            raise RuntimeError(f"tuning panel size differs for K={k}")
        baseline = metrics.loc[
            (metrics["k"] == k) & (metrics["model_id"] == BASELINE)
        ].set_index("user_key")
        lightfm = metrics.loc[
            (metrics["k"] == k) & (metrics["model_id"] == LIGHTFM)
        ].set_index("user_key")
        joined = lightfm.join(baseline, lsuffix="_candidate", rsuffix="_baseline", how="inner")
        confirmatory = joined.loc[~joined.index.astype(str).isin(panel)]
        delta = (
            confirmatory["ndcg_at_10_candidate"] - confirmatory["ndcg_at_10_baseline"]
        ).to_numpy(dtype=np.float64)
        seed = CONFIRMATORY_BOOTSTRAP_SEED_BASE + k
        lower, upper = _paired_bootstrap_ci(
            delta,
            seed=seed,
            iterations=CONFIRMATORY_BOOTSTRAP_ITERATIONS,
        )
        row = next(item for item in confirmatory_rows if int(item["k"]) == k)
        if row.get("status") != "CONFIRMATORY_AUXILIARY_TUNING_PANEL_EXCLUDED":
            raise RuntimeError(f"confirmatory status differs for K={k}")
        if int(row["users"]) != len(confirmatory):
            raise RuntimeError(f"confirmatory user count differs for K={k}")
        if int(row["tuning_panel_users_excluded"]) != 256:
            raise RuntimeError(f"confirmatory exclusion count differs for K={k}")
        if not _close(float(row["delta_ndcg_mean"]), float(delta.mean())):
            raise RuntimeError(f"confirmatory mean differs for K={k}")
        if not _close(float(row["delta_ndcg_ci95"][0]), lower):
            raise RuntimeError(f"confirmatory lower CI differs for K={k}")
        if not _close(float(row["delta_ndcg_ci95"][1]), upper):
            raise RuntimeError(f"confirmatory upper CI differs for K={k}")
        if row.get("bootstrap") != {
            "iterations": CONFIRMATORY_BOOTSTRAP_ITERATIONS,
            "method": "percentile",
            "seed": seed,
            "unit": "user",
        }:
            raise RuntimeError(f"confirmatory bootstrap contract differs for K={k}")

    known_confirmatory = {
        5: (1358, 0.03331, 0.02582, 0.04114),
        10: (1223, 0.04532, 0.03681, 0.05462),
    }
    for row in confirmatory_rows:
        expected = known_confirmatory[int(row["k"])]
        rounded = (
            int(row["users"]),
            round(float(row["delta_ndcg_mean"]), 5),
            round(float(row["delta_ndcg_ci95"][0]), 5),
            round(float(row["delta_ndcg_ci95"][1]), 5),
        )
        if rounded != expected:
            raise RuntimeError(f"confirmatory audit value differs for K={row['k']}: {rounded}")

    common = sorted(
        set(metrics.loc[(metrics["model_id"] == LIGHTFM) & (metrics["k"] == 5), "user_key"])
        & set(metrics.loc[(metrics["model_id"] == LIGHTFM) & (metrics["k"] == 10), "user_key"])
    )
    common_summary = summary.get("common_user_k_diagnostic", {})
    if len(common) != 1253 or int(common_summary.get("users", -1)) != len(common):
        raise RuntimeError("common K5/K10 user count differs")
    if common_summary.get("same_future_window") is not False:
        raise RuntimeError("K5/K10 future windows were incorrectly treated as identical")
    for model_id in (BASELINE, LIGHTFM):
        for k in (5, 10):
            group = metrics.loc[
                (metrics["model_id"] == model_id) & (metrics["k"] == k)
            ].set_index("user_key")
            expected = float(group.loc[common, "ndcg_at_10"].mean())
            actual = float(common_summary["absolute_ndcg_at_10"][model_id][str(k)])
            if not _close(actual, expected):
                raise RuntimeError(f"common-user absolute NDCG differs for {model_id} K={k}")
    lightfm_absolute = common_summary["absolute_ndcg_at_10"][LIGHTFM]
    if round(float(lightfm_absolute["5"]), 6) != 0.075359:
        raise RuntimeError("common-user LightFM K5 NDCG audit value differs")
    if round(float(lightfm_absolute["10"]), 6) != 0.075348:
        raise RuntimeError("common-user LightFM K10 NDCG audit value differs")

    prefixes = pd.read_parquet(
        root / "outputs/recommendation-evidence/rec-ev-019a/validation-binary-prefixes.parquet"
    )
    candidate = pd.read_parquet(
        root / "outputs/recommendation-evidence/rec-ev-019c/candidate-core-final.parquet"
    )
    candidate_ids = set(candidate["movie_id"].astype(int))
    fallback_rows = summary.get("fallback_anchor_diagnostics", [])
    for k, known_loss in ((5, 97), (10, 46)):
        group = prefixes.loc[prefixes["k"] == k].copy()
        raw = group.groupby("user_key", observed=True)["binary_label"].agg(
            lambda values: set(map(int, values))
        )
        valid = group.loc[group["movie_id"].isin(candidate_ids)].groupby(
            "user_key", observed=True
        )["binary_label"].agg(lambda values: set(map(int, values)))
        lightfm = metrics.loc[
            (metrics["model_id"] == LIGHTFM) & (metrics["k"] == k),
            ["user_key", "fallback_user"],
        ].set_index("user_key")
        users = set(lightfm.index.astype(str))
        raw_both = {user for user in users if {-1, 1} <= raw.get(user, set())}
        valid_both = {user for user in users if {-1, 1} <= valid.get(user, set())}
        fallback = set(lightfm.index[lightfm["fallback_user"].astype(bool)].astype(str))
        anchor_loss = raw_both - valid_both
        if anchor_loss != raw_both & fallback or len(anchor_loss) != known_loss:
            raise RuntimeError(f"candidate-anchor fallback audit differs for K={k}")
        row = next(item for item in fallback_rows if int(item["k"]) == k)
        if int(row["raw_both_but_candidate_anchor_loss_users"]) != known_loss:
            raise RuntimeError(f"reported candidate-anchor fallback differs for K={k}")
        if row.get("fallback_is_design_precondition_not_signal_effect") is not True:
            raise RuntimeError(f"fallback interpretation boundary differs for K={k}")

    history_rows = [row for row in summary["cohorts_for_validation_best_vs_b0"] if row["dimension"] == "history_group"]
    for k in (5, 10):
        cohorts = {row["cohort"] for row in history_rows if int(row["k"]) == k}
        if len(cohorts) < 2:
            raise RuntimeError(f"history cohorts collapsed for K={k}")

    item_slices = summary["item_slices"]
    slice_keys = [
        (row["model_id"], int(row["k"]), row["dimension"], row["cohort"])
        for row in item_slices
    ]
    if len(slice_keys) != len(set(slice_keys)):
        raise RuntimeError("item slice keys are duplicated")
    for k in (5, 10):
        for dimension in (
            "popularity_group",
            "language_group",
            "release_year_group",
            "cold_item_group",
        ):
            by_model = {
                model_id: {
                    row["cohort"]
                    for row in item_slices
                    if int(row["k"]) == k and row["dimension"] == dimension and row["model_id"] == model_id
                }
                for model_id in models_by_k[k]
            }
            expected = set().union(*by_model.values())
            if any(cohorts != expected for cohorts in by_model.values()):
                raise RuntimeError(f"item slice cohort coverage differs across models for K={k} {dimension}")

    structured = pd.read_parquet(
        root / "outputs/recommendation-evidence/rec-ev-019b/structured-features.parquet",
        columns=["movie_id", "original_language", "release_year"],
    )
    candidate_context = candidate.merge(structured, on="movie_id", how="left")
    candidate_context["popularity_group"] = _quartile(candidate_context["b0_rating_count"])
    windows = pd.read_parquet(
        root / "outputs/recommendation-evidence/rec-ev-019a/validation-evaluation-windows.parquet"
    )
    windows = windows.loc[windows["movie_id"].isin(candidate_ids)].merge(
        candidate_context[
            ["movie_id", "popularity_group", "original_language", "release_year", "b0_rating_count"]
        ],
        on="movie_id",
        how="left",
    )
    core = summary.get("core_item_slice_diagnostics", {})
    for k, known_q4, known_total in ((5, 6086, 6345), (10, 5729, 5943)):
        positive = windows.loc[(windows["k"] == k) & windows["is_positive"]]
        q4 = int((positive["popularity_group"] == "영화 인기도 Q4").sum())
        if (q4, len(positive)) != (known_q4, known_total):
            raise RuntimeError(f"Q4 positive concentration audit differs for K={k}")
        recorded = core["positive_concentration"][str(k)]
        if int(recorded["q4_observed_positive_total"]) != q4:
            raise RuntimeError(f"reported Q4 positive count differs for K={k}")
        if int(recorded["observed_positive_total"]) != len(positive):
            raise RuntimeError(f"reported positive denominator differs for K={k}")

    korean_truth = {
        (str(row.user_key), int(row.k), int(row.movie_id))
        for row in windows.loc[
            windows["is_positive"] & windows["original_language"].eq("ko")
        ].itertuples(index=False)
    }
    korean_counts = {
        k: sum(1 for _, row_k, _ in korean_truth if row_k == k)
        for k in (5, 10)
    }
    if korean_counts != {5: 21, 10: 23}:
        raise RuntimeError("Korean original-language positive counts differ")
    korean_hits = _recompute_korean_prediction_hits(
        root / "outputs/recommendation-evidence/rec-ev-019c/validation-predictions.parquet",
        korean_truth,
    )
    expected_hits = {
        (BASELINE, 5): {"top500": 10, "top10": 0},
        (LIGHTFM, 5): {"top500": 6, "top10": 0},
        (BASELINE, 10): {"top500": 10, "top10": 0},
        (LIGHTFM, 10): {"top500": 6, "top10": 0},
    }
    if korean_hits != expected_hits:
        raise RuntimeError(f"Korean original-language prediction hits differ: {korean_hits}")
    for k in (5, 10):
        recorded = core["korean_original_language"][str(k)]
        if int(recorded["observed_positive_total"]) != korean_counts[k]:
            raise RuntimeError(f"reported Korean positive count differs for K={k}")
        if int(recorded["b0_positive_top500"]) != expected_hits[(BASELINE, k)]["top500"]:
            raise RuntimeError(f"reported Korean B0 Top500 differs for K={k}")
        if int(recorded["lightfm_positive_top500"]) != expected_hits[(LIGHTFM, k)]["top500"]:
            raise RuntimeError(f"reported Korean LightFM Top500 differs for K={k}")
        if recorded.get("small_sample_no_inferiority_claim") is not True:
            raise RuntimeError(f"small-sample interpretation boundary differs for K={k}")

    recent_candidate_count = int(candidate_context["release_year"].ge(2020).sum())
    if recent_candidate_count != 9:
        raise RuntimeError("release_year>=2020 candidate count differs")
    cold_candidate_count = int(candidate_context["b0_rating_count"].eq(0).sum())
    if cold_candidate_count != 0:
        raise RuntimeError("base-train-zero candidate count differs")
    for k in (5, 10):
        recent_positive = int(
            windows.loc[
                (windows["k"] == k) & windows["is_positive"] & windows["release_year"].ge(2020)
            ].shape[0]
        )
        cold_positive = int(
            windows.loc[
                (windows["k"] == k) & windows["is_positive"] & windows["b0_rating_count"].eq(0)
            ].shape[0]
        )
        if recent_positive != 0 or cold_positive != 0:
            raise RuntimeError(f"recent/cold zero-positive audit differs for K={k}")
        if core["release_year"][str(k)] != {
            "quality_measured": False,
            "observed_positive_total": 0,
            "release_year_gte_2020_candidate_items": 9,
        }:
            raise RuntimeError(f"reported release-year diagnostic differs for K={k}")
        if core["cold_item"][str(k)] != {
            "base_train_zero_candidate_items": 0,
            "observed_positive_total": 0,
            "quality_measured": False,
        }:
            raise RuntimeError(f"reported cold-item diagnostic differs for K={k}")

    for k in (5, 10):
        recent_row = _find_summary_row(
            item_slices,
            k=k,
            dimension="release_year_group",
            cohort="RELEASE_2020_OR_LATER",
            model_id=BASELINE,
        )
        zero_row = _find_summary_row(
            item_slices,
            k=k,
            dimension="cold_item_group",
            cohort="BASE_TRAIN_ZERO",
            model_id=BASELINE,
        )
        if int(recent_row["candidate_item_total"]) != 9 or int(recent_row["observed_positive_total"]) != 0:
            raise RuntimeError(f"release-year zero slice omitted or changed for K={k}")
        if int(zero_row["candidate_item_total"]) != 0 or int(zero_row["observed_positive_total"]) != 0:
            raise RuntimeError(f"cold-item zero slice omitted or changed for K={k}")

    return {
        "status": "PASS_REC_EV_019C_ANALYSIS_VERIFICATION",
        "aggregate_groups": expected_groups,
        "paired_comparisons": expected_paired,
        "locked_test_opened": False,
        "locked_test_used": False,
        "champion": None,
        "champion_selected": False,
        "product_policy_changed": False,
        "product_policy_updated": False,
        "confirmatory_users": {"5": 1358, "10": 1223},
        "candidate_anchor_loss_fallback_users": {"5": 97, "10": 46},
        "release_year_gte_2020_candidates": 9,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    result = verify_analysis_manifest(args.manifest.resolve(), root=ROOT)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
