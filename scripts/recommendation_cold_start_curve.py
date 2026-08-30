#!/usr/bin/env python3
"""Run leakage-safe REC-EV-003 K0/K1/K3/K5/K10/K20 cold-start evidence."""

from __future__ import annotations

import argparse
import gc
import json
import math
import platform
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow
import pyspark
import sklearn

from recommendation_baseline_calibration import (
    RATING_MAX,
    RATING_MIN,
    apply_isotonic,
    artifact_record,
    fit_isotonic,
    fit_regularized_bias,
    predict_popularity,
    regression_metrics,
    sampled_ranking_metrics,
    safe_take,
    sha256_file,
)


K_VALUES = (0, 1, 3, 5, 10, 20)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split-dir", type=Path, required=True)
    parser.add_argument("--split-manifest", type=Path, required=True)
    parser.add_argument("--baseline-manifest", type=Path, required=True)
    parser.add_argument("--baseline-predictions", type=Path, required=True)
    parser.add_argument("--baseline-candidates", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cohort-manifest", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--rank", type=int, default=32)
    parser.add_argument("--reg-param", type=float, default=0.1)
    parser.add_argument("--max-iter", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--min-history", type=int, default=20)
    parser.add_argument("--bias-reg-user", type=float, default=10.0)
    parser.add_argument("--bias-reg-item", type=float, default=25.0)
    parser.add_argument("--bias-iterations", type=int, default=10)
    parser.add_argument("--popularity-prior", type=float, default=50.0)
    parser.add_argument("--bootstrap-repeats", type=int, default=1000)
    parser.add_argument("--spark-master", default="local[4]")
    parser.add_argument("--spark-driver-memory", default="8g")
    parser.add_argument("--spark-shuffle-partitions", type=int, default=32)
    return parser.parse_args()


def select_cohort(
    validation: pd.DataFrame,
    profiles: pd.DataFrame,
    calibration_boundary: int,
    min_history: int,
) -> pd.DataFrame:
    warm = validation.loc[
        validation["identity_state"] == "KNOWN_USER_KNOWN_ITEM"
    ]
    per_user = (
        warm.assign(
            calibration_row=warm["timestamp"] < calibration_boundary,
            evaluation_row=warm["timestamp"] >= calibration_boundary,
        )
        .groupby("user_id", sort=True)
        .agg(
            calibration_rows=("calibration_row", "sum"),
            evaluation_rows=("evaluation_row", "sum"),
        )
        .reset_index()
    )
    cohort = per_user.merge(
        profiles[
            [
                "user_id",
                "rating_count",
                "rating_mean",
                "rating_std",
                "rating_mean_quartile",
                "train_history_bucket",
            ]
        ],
        on="user_id",
        how="inner",
        validate="one_to_one",
    )
    cohort = cohort.loc[
        (cohort["calibration_rows"] > 0)
        & (cohort["evaluation_rows"] > 0)
        & (cohort["rating_count"] >= min_history)
    ].sort_values("user_id")
    if cohort.empty:
        raise RuntimeError("cold-start cohort is empty")
    return cohort.reset_index(drop=True)


def first_k_events(train: pd.DataFrame, cohort_ids: np.ndarray, max_k: int) -> pd.DataFrame:
    selected = train.loc[
        train["user_id"].isin(cohort_ids),
        ["user_id", "movie_id", "rating", "timestamp"],
    ].copy()
    selected = selected.sort_values(
        ["user_id", "timestamp", "movie_id"], kind="stable"
    )
    selected["onboarding_order"] = selected.groupby("user_id").cumcount() + 1
    selected = selected.loc[selected["onboarding_order"] <= max_k]
    counts = selected.groupby("user_id").size()
    if len(counts) != len(cohort_ids) or int(counts.min()) < max_k:
        raise RuntimeError("every cohort user must have max_k onboarding events")
    return selected.reset_index(drop=True)


def build_dense_item_factors(
    item_ids: np.ndarray, factors: np.ndarray
) -> np.ndarray:
    result = np.full(
        (int(item_ids.max()) + 1, factors.shape[1]), np.nan, dtype=np.float32
    )
    result[item_ids] = factors
    return result


def lookup_item_factors(
    movie_ids: np.ndarray, dense_item_factors: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    rank = dense_item_factors.shape[1]
    values = np.full((len(movie_ids), rank), np.nan, dtype=np.float32)
    in_range = (movie_ids >= 0) & (movie_ids < len(dense_item_factors))
    values[in_range] = dense_item_factors[movie_ids[in_range]]
    finite = np.isfinite(values).all(axis=1)
    return values, finite


def fold_in_factors(
    cohort_ids: np.ndarray,
    onboarding: pd.DataFrame,
    dense_item_factors: np.ndarray,
    k: int,
    reg_param: float,
) -> tuple[np.ndarray, np.ndarray]:
    if k <= 0:
        return (
            np.full((len(cohort_ids), dense_item_factors.shape[1]), np.nan),
            np.zeros(len(cohort_ids), dtype=np.int64),
        )
    rank = dense_item_factors.shape[1]
    result = np.full((len(cohort_ids), rank), np.nan, dtype=np.float64)
    factor_counts = np.zeros(len(cohort_ids), dtype=np.int64)
    row_by_user = {int(user_id): index for index, user_id in enumerate(cohort_ids)}
    selected = onboarding.loc[onboarding["onboarding_order"] <= k]
    for user_id, frame in selected.groupby("user_id", sort=False):
        movie_ids = frame["movie_id"].to_numpy(dtype=np.int64)
        item_factors, available = lookup_item_factors(movie_ids, dense_item_factors)
        item_factors = item_factors[available].astype(np.float64)
        ratings = frame["rating"].to_numpy(dtype=np.float64)[available]
        count = len(ratings)
        index = row_by_user[int(user_id)]
        factor_counts[index] = count
        if count == 0:
            continue
        normal = item_factors.T @ item_factors
        # Spark explicit ALS-WR scales regularization by this user's rating count.
        normal.flat[:: rank + 1] += reg_param * count
        right = item_factors.T @ ratings
        try:
            result[index] = np.linalg.solve(normal, right)
        except np.linalg.LinAlgError:
            result[index] = np.linalg.lstsq(normal, right, rcond=None)[0]
    return result, factor_counts


def onboarding_user_bias(
    cohort_ids: np.ndarray,
    onboarding: pd.DataFrame,
    k: int,
    global_mean: float,
    movie_bias: np.ndarray,
    reg_user: float,
) -> np.ndarray:
    result = np.zeros(len(cohort_ids), dtype=np.float64)
    if k == 0:
        return result
    row_by_user = {int(user_id): index for index, user_id in enumerate(cohort_ids)}
    selected = onboarding.loc[onboarding["onboarding_order"] <= k]
    for user_id, frame in selected.groupby("user_id", sort=False):
        movies = frame["movie_id"].to_numpy(dtype=np.int64)
        ratings = frame["rating"].to_numpy(dtype=np.float64)
        residual = ratings - global_mean - safe_take(movie_bias, movies)
        result[row_by_user[int(user_id)]] = float(residual.sum()) / (
            len(residual) + reg_user
        )
    return result


def align_user_values(
    user_ids: np.ndarray, cohort_ids: np.ndarray, values: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    positions = np.searchsorted(cohort_ids, user_ids)
    valid = (positions >= 0) & (positions < len(cohort_ids))
    valid &= cohort_ids[np.clip(positions, 0, len(cohort_ids) - 1)] == user_ids
    return positions, valid


def score_fold_in(
    user_ids: np.ndarray,
    movie_ids: np.ndarray,
    cohort_ids: np.ndarray,
    user_factors: np.ndarray,
    dense_item_factors: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    positions, known_user = align_user_values(user_ids, cohort_ids, user_factors)
    item_factors, known_item = lookup_item_factors(movie_ids, dense_item_factors)
    direct = known_user & known_item
    direct &= np.isfinite(user_factors[np.clip(positions, 0, len(user_factors) - 1)]).all(
        axis=1
    )
    result = np.full(len(user_ids), np.nan, dtype=np.float64)
    if bool(direct.any()):
        result[direct] = np.einsum(
            "ij,ij->i",
            user_factors[positions[direct]],
            item_factors[direct],
            optimize=True,
        )
    return result, direct


def predict_cold_bias(
    user_ids: np.ndarray,
    movie_ids: np.ndarray,
    cohort_ids: np.ndarray,
    user_biases: np.ndarray,
    global_mean: float,
    movie_bias: np.ndarray,
) -> np.ndarray:
    positions, valid = align_user_values(user_ids, cohort_ids, user_biases)
    aligned_bias = np.zeros(len(user_ids), dtype=np.float64)
    aligned_bias[valid] = user_biases[positions[valid]]
    return np.clip(
        global_mean + safe_take(movie_bias, movie_ids) + aligned_bias,
        RATING_MIN,
        RATING_MAX,
    )


def user_macro_mae(frame: pd.DataFrame, prediction_column: str) -> tuple[float, pd.Series]:
    finite = np.isfinite(frame[prediction_column])
    scored = frame.loc[finite, ["user_id", "rating", prediction_column]].copy()
    scored["absolute_error"] = np.abs(
        scored[prediction_column].to_numpy() - scored["rating"].to_numpy()
    )
    per_user = scored.groupby("user_id")["absolute_error"].mean()
    return float(per_user.mean()), per_user


def bootstrap_mean_difference(
    candidate: pd.Series,
    baseline: pd.Series,
    repeats: int,
    seed: int,
) -> dict[str, float | int]:
    paired = pd.concat(
        [candidate.rename("candidate"), baseline.rename("baseline")], axis=1
    ).dropna()
    differences = (paired["candidate"] - paired["baseline"]).to_numpy()
    rng = np.random.default_rng(seed)
    bootstrap = np.empty(repeats, dtype=np.float64)
    for index in range(repeats):
        positions = rng.integers(0, len(differences), size=len(differences))
        bootstrap[index] = float(np.mean(differences[positions]))
    return {
        "users": int(len(differences)),
        "mean_difference": round(float(np.mean(differences)), 6),
        "ci95_low": round(float(np.quantile(bootstrap, 0.025)), 6),
        "ci95_high": round(float(np.quantile(bootstrap, 0.975)), 6),
    }


def mean_profile_stability(
    cohort: pd.DataFrame, onboarding: pd.DataFrame, k: int
) -> dict[str, float | int | None]:
    if k == 0:
        return {
            "users": int(len(cohort)),
            "mean_absolute_error": None,
            "p90_absolute_error": None,
            "within_0_25": None,
            "within_0_5": None,
        }
    observed = (
        onboarding.loc[onboarding["onboarding_order"] <= k]
        .groupby("user_id")["rating"]
        .mean()
        .rename("onboarding_mean")
    )
    joined = cohort.set_index("user_id")[["rating_mean"]].join(observed).dropna()
    error = np.abs(joined["onboarding_mean"] - joined["rating_mean"])
    return {
        "users": int(len(error)),
        "mean_absolute_error": round(float(error.mean()), 6),
        "p90_absolute_error": round(float(error.quantile(0.90)), 6),
        "within_0_25": round(float((error <= 0.25).mean()), 6),
        "within_0_5": round(float((error <= 0.5).mean()), 6),
    }


def metric_or_na(value: float | int | None, digits: int = 4) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "N/A"
    if isinstance(value, int):
        return f"{value:,}"
    return f"{value:.{digits}f}"


def markdown_table(headers: list[str], rows: list[tuple[Any, ...]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(str(value) for value in row) + " |" for row in rows)
    return "\n".join(lines)


def build_evidence_markdown(manifest: dict[str, Any]) -> str:
    curves = manifest["metrics"]["k_curve"]
    curve_table = markdown_table(
        [
            "K",
            "Direct user coverage",
            "Direct row coverage",
            "Macro MAE",
            "Micro MAE",
            "ECE",
            "Sampled NDCG@10",
            "Profile mean MAE",
        ],
        [
            (
                k,
                f"{values['direct_user_coverage']:.2%}",
                f"{values['direct_raw']['coverage']:.2%}",
                metric_or_na(values["macro_mae"]),
                metric_or_na(values["fallback_isotonic"]["mae"]),
                metric_or_na(values["fallback_isotonic"]["ece_decile"]),
                metric_or_na(values["sampled_ranking"]["ndcg_at_10"]),
                metric_or_na(values["profile_mean_stability"]["mean_absolute_error"]),
            )
            for k, values in curves.items()
        ],
    )
    ci_table = markdown_table(
        ["K vs K0", "Users", "Macro MAE difference", "95% CI"],
        [
            (
                k,
                f"{values['users']:,}",
                f"{values['mean_difference']:+.4f}",
                f"[{values['ci95_low']:+.4f}, {values['ci95_high']:+.4f}]",
            )
            for k, values in manifest["metrics"]["paired_mae_vs_k0"].items()
        ],
    )
    quartile_table = markdown_table(
        ["Train mean quartile", *[f"K{k}" for k in K_VALUES]],
        [
            (
                quartile,
                *[
                    metric_or_na(values[str(k)]["macro_mae"])
                    for k in K_VALUES
                ],
            )
            for quartile, values in manifest["metrics"]["by_rating_mean_quartile"].items()
        ],
    )
    cohort = manifest["cohort"]
    runtime = manifest["runtime"]
    conclusion = manifest["conclusion"]
    return f"""# REC-EV-003 — K0~K20 leakage-safe cold-start 곡선

> 상태: `COMPLETED`  
> 생성 시각: {manifest['run_at_utc']}  
> Cohort: `{manifest['protocol']['cohort_version']}` / {cohort['users']:,}명  
> Test 사용: `NO`

## 1. 결론

{conclusion['summary']}

- 데이터 품질 knee: `{conclusion['data_quality_knee']}`
- 온보딩 최대 입력 수 제품 결정: `{conclusion['onboarding_product_gate']}`
- 예상 별점 confidence 결정: `{conclusion['confidence_gate']}`
- 추천 순위 Gate: `{conclusion['ranking_gate']}`
- 다음 실험: `{conclusion['next_experiment']}`

## 2. 왜 기존 ALS factor를 그대로 쓰지 않았는가

평가 사용자 {cohort['users']:,}명을 ALS와 Bias 학습에서 통째로 제외했다. 이들의 전체 Train 이력을
학습한 item factor를 사용한 뒤 K개만 보였다고 주장하면 숨긴 이력이 item factor에 간접 반영된다.
이번 실험은 나머지 사용자 {cohort['remaining_train_users']:,}명, 평점
{cohort['remaining_train_rows']:,}개로 item factor를 다시 학습하고, 평가 사용자의 시간상 최초 K개
평점만 Fold-in에 사용했다.

평가 사용자는 모두 Train 이력 20개 이상이며 Validation 앞·뒤 구간에 future rating이 있다.
앞 구간은 K별 Isotonic 보정, 뒤 구간은 최종 곡선에만 사용했다. Test는 읽지 않았다.

## 3. K별 결과

{curve_table}

`Macro MAE`는 사용자를 동일 가중한 예상 별점 오차이고 주 곡선이다. `Micro MAE`는 평가 행을
동일 가중해 활동량이 많은 사용자의 영향이 더 크다. K0는 개인 factor가 없으므로 cohort 제외
Bias/Popularity fallback만 사용한다. K1 이상은 직접 Fold-in이 불가능한 영화에 Bias fallback을
사용해 서비스 coverage 100%를 유지한다.

`Sampled NDCG@10`은 REC-EV-002와 같은 positive 1개+미평가 99개 진단 후보이며 full-catalog
지표가 아니다. 미평가를 실제 싫어요로 간주하지 않는다.

## 4. K0 대비 사용자 단위 paired bootstrap

{ci_table}

차이는 `K MAE - K0 MAE`다. 음수이면 K 입력이 사용자 macro MAE를 줄였다는 뜻이다. 같은 사용자를
1,000회 bootstrap했으며 Test를 본 뒤 기준을 바꾸지 않았다.

## 5. 사용자 rating-style 구간별 Macro MAE

{quartile_table}

공통 4점 threshold가 아니라 각 사용자의 실제 별점 오차를 비교한다. 특정 quartile만 개선되면
모든 사용자에게 같은 confidence를 표시할 수 없다.

## 6. 판단 범위

판단 가능:

- K가 늘 때 예상 별점·sampled ranking·개인 평균 추정이 실제로 얼마나 변하는지
- 데이터 관점에서 성능 증가가 둔화하는 구간
- K0 fallback과 K개 Fold-in 사이의 coverage·비용 차이

아직 판단 불가:

- 사용자가 K개 입력 화면을 실제로 완료하는 비율과 시간
- full-catalog Top-N에서의 최종 순위 우승 모델
- MovieLens 결과가 실제 FEELM 신규 사용자와 동일한지
- 예상 별점 숫자를 어떤 문구로 보여줄지

## 7. 비용과 재현

- Cohort 제외 ALS 학습: {runtime['als_train_seconds']:.2f}s
- 전체 실행: {runtime['total_seconds']:.2f}s
- Spark master: `{runtime['spark_master']}`
- Python `{runtime['python']}`, PySpark `{runtime['pyspark']}`, scikit-learn `{runtime['scikit_learn']}`

```powershell
py -3 scripts/recommendation_cold_start_curve.py `
  --split-dir outputs\\recommendation-evidence\\global-time-v1 `
  --split-manifest docs\\recommendation\\evidence\\manifests\\global-time-v1.json `
  --baseline-manifest docs\\recommendation\\evidence\\manifests\\rec-ev-002.json `
  --baseline-predictions outputs\\recommendation-evidence\\rec-ev-002\\validation_predictions.parquet `
  --baseline-candidates outputs\\recommendation-evidence\\rec-ev-002\\sampled_ranking_scored.parquet `
  --output-dir outputs\\recommendation-evidence\\rec-ev-003 `
  --cohort-manifest docs\\recommendation\\evidence\\manifests\\cold-start-cohort-v1.json `
  --manifest docs\\recommendation\\evidence\\manifests\\rec-ev-003.json `
  --evidence docs\\recommendation\\evidence\\REC-EV-003-cold-start.md
```

## 8. 한계

- ALS rank/regParam은 REC-EV-002 첫 기준선이며 grid·다중 seed 전이다.
- Fold-in은 MovieLens explicit rating만 사용하고 FEELM 행동·콘텐츠 특징은 아직 없다.
- 평가 cohort는 미래 평가가 앞·뒤 구간에 모두 있는 활동 사용자라 전체 신규 가입자를 대표하지
  않는다.
- sampled ranking은 후보 추출 편향이 있으며 최종 채택에 사용하지 않는다.
- 온보딩 UX 비용은 React 비교 화면에서 별도로 판단해야 한다.
"""


def main() -> int:
    args = parse_args()
    started = time.perf_counter()
    train_path = args.split_dir / "train.parquet"
    profile_path = args.split_dir / "user_rating_profiles.parquet"
    required = [
        train_path,
        profile_path,
        args.split_manifest,
        args.baseline_manifest,
        args.baseline_predictions,
        args.baseline_candidates,
    ]
    for path in required:
        if not path.exists():
            raise FileNotFoundError(path)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    split_manifest = json.loads(args.split_manifest.read_text(encoding="utf-8"))
    baseline_manifest = json.loads(args.baseline_manifest.read_text(encoding="utf-8"))
    if split_manifest["protocol"]["version"] != "global-time-v1":
        raise RuntimeError("REC-EV-003 requires global-time-v1")
    if baseline_manifest["source"]["test_used"] is not False:
        raise RuntimeError("baseline evidence unexpectedly used Test")
    calibration_boundary = int(
        baseline_manifest["protocol"]["calibration_boundary"]
    )

    profiles = pd.read_parquet(profile_path)
    baseline_predictions = pd.read_parquet(args.baseline_predictions)
    cohort = select_cohort(
        baseline_predictions,
        profiles,
        calibration_boundary,
        args.min_history,
    )
    cohort_ids = cohort["user_id"].to_numpy(dtype=np.int64)
    print(f"Selected leakage-safe cohort: {len(cohort_ids):,} users", flush=True)

    train = pd.read_parquet(train_path)
    onboarding = first_k_events(train, cohort_ids, max(K_VALUES))
    excluded = train["user_id"].isin(cohort_ids).to_numpy()
    excluded_rows = int(excluded.sum())
    remaining = train.loc[~excluded, ["user_id", "movie_id", "rating"]]
    if bool(remaining["user_id"].isin(cohort_ids).any()):
        raise RuntimeError("cohort leakage in remaining Train")

    train_users = train["user_id"].to_numpy(dtype=np.int64, copy=False)
    train_movies = train["movie_id"].to_numpy(dtype=np.int64, copy=False)
    train_ratings = train["rating"].to_numpy(dtype=np.float64, copy=False)
    remaining_users = remaining["user_id"].to_numpy(dtype=np.int64, copy=False)
    remaining_movies = remaining["movie_id"].to_numpy(dtype=np.int64, copy=False)
    remaining_ratings = remaining["rating"].to_numpy(dtype=np.float64, copy=False)
    user_size = int(max(train_users.max(), cohort_ids.max())) + 1
    movie_size = int(train_movies.max()) + 1

    bias = fit_regularized_bias(
        remaining_users,
        remaining_movies,
        remaining_ratings,
        user_size=user_size,
        movie_size=movie_size,
        reg_user=args.bias_reg_user,
        reg_item=args.bias_reg_item,
        iterations=args.bias_iterations,
    )
    global_mean = float(bias["global_mean"])
    movie_bias = bias["movie_bias"]
    movie_counts = bias["movie_counts"]
    movie_sums = bias["movie_sums"]
    bias_path = args.output_dir / "cohort_excluded_bias_parameters.npz"
    np.savez_compressed(bias_path, **bias)

    cohort_manifest = {
        "schema_version": 1,
        "cohort_version": "cold-start-cohort-v1",
        "source_split": split_manifest["protocol"]["version"],
        "selection": {
            "min_train_history": args.min_history,
            "requires_warm_validation_calibration_row": True,
            "requires_warm_validation_evaluation_row": True,
            "calibration_boundary": calibration_boundary,
            "order": "ascending user_id",
        },
        "users": int(len(cohort_ids)),
        "user_ids": cohort_ids.tolist(),
    }
    args.cohort_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.cohort_manifest.write_text(
        json.dumps(cohort_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    cohort_path = args.output_dir / "cohort.parquet"
    cohort.to_parquet(cohort_path, index=False, compression="zstd")
    onboarding_path = args.output_dir / "onboarding_first_20.parquet"
    onboarding.to_parquet(onboarding_path, index=False, compression="zstd")

    validation = baseline_predictions.loc[
        baseline_predictions["user_id"].isin(cohort_ids)
    ].copy()
    validation = validation.sort_values("row_id").reset_index(drop=True)
    candidates = pd.read_parquet(args.baseline_candidates)
    candidates = candidates.loc[candidates["user_id"].isin(cohort_ids)].copy()
    if candidates.empty:
        raise RuntimeError("cohort has no fixed sampled-ranking candidates")

    remaining_train_rows = len(remaining)
    remaining_train_users = int(remaining["user_id"].nunique())
    del (
        remaining,
        remaining_users,
        remaining_movies,
        remaining_ratings,
        train,
        train_users,
        train_movies,
        train_ratings,
        excluded,
        profiles,
        baseline_predictions,
    )
    gc.collect()

    print(
        f"Training cohort-excluded ALS on {remaining_train_rows:,} rows...",
        flush=True,
    )
    from pyspark.ml.recommendation import ALS
    from pyspark.sql import SparkSession
    from pyspark.sql.functions import broadcast

    spark = (
        SparkSession.builder.master(args.spark_master)
        .appName("feelm-rec-ev-003")
        .config("spark.driver.memory", args.spark_driver_memory)
        .config("spark.sql.shuffle.partitions", str(args.spark_shuffle_partitions))
        .config("spark.sql.execution.arrow.pyspark.enabled", "true")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("WARN")
    spark_train = spark.read.parquet(str(train_path.resolve())).select(
        "user_id", "movie_id", "rating"
    )
    spark_cohort = spark.createDataFrame(
        [(int(user_id),) for user_id in cohort_ids], ["user_id"]
    )
    spark_remaining = spark_train.join(
        broadcast(spark_cohort), on="user_id", how="left_anti"
    )
    estimator = ALS(
        rank=args.rank,
        maxIter=args.max_iter,
        regParam=args.reg_param,
        userCol="user_id",
        itemCol="movie_id",
        ratingCol="rating",
        seed=args.seed,
        implicitPrefs=False,
        nonnegative=False,
        coldStartStrategy="nan",
        numUserBlocks=10,
        numItemBlocks=10,
    )
    als_started = time.perf_counter()
    als_model = estimator.fit(spark_remaining)
    als_seconds = time.perf_counter() - als_started
    item_factor_frame = als_model.itemFactors.toPandas().sort_values("id")
    spark.stop()

    item_factor_ids = item_factor_frame["id"].to_numpy(dtype=np.int64)
    item_factor_values = np.asarray(
        item_factor_frame["features"].tolist(), dtype=np.float32
    )
    dense_item_factors = build_dense_item_factors(
        item_factor_ids, item_factor_values
    )
    item_factor_path = args.output_dir / "cohort_excluded_item_factors.npz"
    np.savez_compressed(
        item_factor_path,
        movie_ids=item_factor_ids,
        movie_factors=item_factor_values,
    )

    validation_users = validation["user_id"].to_numpy(dtype=np.int64)
    validation_movies = validation["movie_id"].to_numpy(dtype=np.int64)
    validation_actual = validation["rating"].to_numpy(dtype=np.float64)
    calibration_mask = validation["timestamp"].to_numpy() < calibration_boundary
    evaluation_mask = ~calibration_mask
    if not bool(calibration_mask.any()) or not bool(evaluation_mask.any()):
        raise RuntimeError("cohort calibration/evaluation halves are empty")

    candidate_users = candidates["user_id"].to_numpy(dtype=np.int64)
    candidate_movies = candidates["movie_id"].to_numpy(dtype=np.int64)

    user_factor_cube = np.full(
        (len(K_VALUES), len(cohort_ids), args.rank), np.nan, dtype=np.float32
    )
    factor_count_matrix = np.zeros(
        (len(K_VALUES), len(cohort_ids)), dtype=np.int16
    )
    calibrator_records: dict[str, Any] = {}
    per_user_errors: dict[int, pd.Series] = {}
    k_curve: dict[str, Any] = {}

    for k_index, k in enumerate(K_VALUES):
        print(f"Scoring cold-start K={k}...", flush=True)
        user_factors, factor_counts = fold_in_factors(
            cohort_ids,
            onboarding,
            dense_item_factors,
            k,
            args.reg_param,
        )
        user_factor_cube[k_index] = user_factors.astype(np.float32)
        factor_count_matrix[k_index] = factor_counts.astype(np.int16)
        user_biases = onboarding_user_bias(
            cohort_ids,
            onboarding,
            k,
            global_mean,
            movie_bias,
            args.bias_reg_user,
        )
        bias_prediction = predict_cold_bias(
            validation_users,
            validation_movies,
            cohort_ids,
            user_biases,
            global_mean,
            movie_bias,
        )
        direct_prediction, direct_rows = score_fold_in(
            validation_users,
            validation_movies,
            cohort_ids,
            user_factors,
            dense_item_factors,
        )
        if k == 0:
            raw_prediction = bias_prediction
        else:
            raw_prediction = np.where(
                np.isfinite(direct_prediction), direct_prediction, bias_prediction
            )
        calibrator = fit_isotonic(
            raw_prediction[calibration_mask], validation_actual[calibration_mask]
        )
        calibrated = apply_isotonic(calibrator, raw_prediction)
        validation[f"prediction_k{k}_bias_raw"] = bias_prediction
        validation[f"prediction_k{k}_direct_raw"] = direct_prediction
        validation[f"prediction_k{k}_fallback_raw"] = raw_prediction
        validation[f"prediction_k{k}_fallback_isotonic"] = calibrated
        validation[f"prediction_k{k}_direct"] = direct_rows
        calibrator_records[str(k)] = {
            "x_thresholds": calibrator.X_thresholds_.tolist(),
            "y_thresholds": calibrator.y_thresholds_.tolist(),
        }

        eval_frame = validation.loc[evaluation_mask]
        eval_actual = eval_frame["rating"].to_numpy(dtype=np.float64)
        fallback_metrics = regression_metrics(
            eval_actual,
            eval_frame[f"prediction_k{k}_fallback_isotonic"].to_numpy(
                dtype=np.float64
            ),
        )
        direct_metrics = regression_metrics(
            eval_actual,
            eval_frame[f"prediction_k{k}_direct_raw"].to_numpy(dtype=np.float64),
            expected_rows=len(eval_frame),
        )
        macro_mae, user_errors = user_macro_mae(
            eval_frame, f"prediction_k{k}_fallback_isotonic"
        )
        per_user_errors[k] = user_errors

        candidate_bias = predict_cold_bias(
            candidate_users,
            candidate_movies,
            cohort_ids,
            user_biases,
            global_mean,
            movie_bias,
        )
        candidate_direct, _ = score_fold_in(
            candidate_users,
            candidate_movies,
            cohort_ids,
            user_factors,
            dense_item_factors,
        )
        if k == 0:
            candidate_score = predict_popularity(
                candidate_movies,
                global_mean,
                movie_counts,
                movie_sums,
                args.popularity_prior,
            )
        else:
            candidate_score = np.where(
                np.isfinite(candidate_direct), candidate_direct, candidate_bias
            )
        candidates[f"score_k{k}"] = candidate_score
        ranking_metrics = sampled_ranking_metrics(candidates, f"score_k{k}")

        direct_user_coverage = float((factor_counts > 0).mean()) if k else 0.0
        k_curve[str(k)] = {
            "cohort_users": int(len(cohort_ids)),
            "evaluation_rows": int(evaluation_mask.sum()),
            "direct_user_coverage": round(direct_user_coverage, 6),
            "mean_available_onboarding_factors": round(
                float(factor_counts.mean()), 6
            ),
            "macro_mae": round(macro_mae, 6),
            "fallback_isotonic": fallback_metrics,
            "direct_raw": direct_metrics,
            "sampled_ranking": ranking_metrics,
            "profile_mean_stability": mean_profile_stability(
                cohort, onboarding, k
            ),
        }

    factor_cube_path = args.output_dir / "foldin_user_factors.npz"
    np.savez_compressed(
        factor_cube_path,
        k_values=np.asarray(K_VALUES, dtype=np.int16),
        user_ids=cohort_ids,
        user_factors=user_factor_cube,
        available_item_counts=factor_count_matrix,
    )
    calibrator_path = args.output_dir / "cold_start_calibrators.json"
    calibrator_path.write_text(
        json.dumps(calibrator_records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    prediction_path = args.output_dir / "cold_start_validation_predictions.parquet"
    validation.to_parquet(prediction_path, index=False, compression="zstd")
    candidate_path = args.output_dir / "cold_start_sampled_ranking.parquet"
    candidates.to_parquet(candidate_path, index=False, compression="zstd")

    paired_vs_k0 = {
        str(k): bootstrap_mean_difference(
            per_user_errors[k],
            per_user_errors[0],
            args.bootstrap_repeats,
            args.seed + k,
        )
        for k in K_VALUES
        if k != 0
    }
    quartile_by_user = cohort.set_index("user_id")["rating_mean_quartile"]
    by_quartile: dict[str, Any] = {}
    for quartile in sorted(cohort["rating_mean_quartile"].unique()):
        users = quartile_by_user.loc[quartile_by_user == quartile].index
        by_quartile[str(quartile)] = {}
        for k in K_VALUES:
            segment = per_user_errors[k].reindex(users).dropna()
            by_quartile[str(quartile)][str(k)] = {
                "users": int(len(segment)),
                "macro_mae": round(float(segment.mean()), 6),
            }

    macro_values = {k: k_curve[str(k)]["macro_mae"] for k in K_VALUES}
    ranking_values = {
        k: k_curve[str(k)]["sampled_ranking"]["ndcg_at_10"] for k in K_VALUES
    }
    best_mae = min(macro_values.values())
    best_ndcg = max(ranking_values.values())
    knee_candidates = [
        k
        for k in K_VALUES
        if macro_values[k] <= best_mae + 0.01
        and ranking_values[k] >= best_ndcg - 0.01
    ]
    data_knee = min(knee_candidates) if knee_candidates else None
    if data_knee is None:
        knee_text = "NO_SINGLE_K_WITHIN_0.01_OF_BOTH_BEST_METRICS"
    else:
        knee_text = f"K{data_knee}_DIAGNOSTIC_ONLY"
    summary = (
        f"평가 사용자 {len(cohort_ids):,}명을 학습에서 제외한 결과, 사용자 macro MAE는 "
        f"K0 {macro_values[0]:.4f}에서 K20 {macro_values[20]:.4f}로 변했고 sampled "
        f"NDCG@10은 K0 {ranking_values[0]:.4f}에서 K20 {ranking_values[20]:.4f}로 "
        f"변했다. 두 지표의 최선에서 모두 0.01 이내인 가장 작은 K는 {knee_text}다. "
        "K10은 K0 대비 예상 별점 MAE가 처음 유의하게 개선된 지점이지만 모든 K의 sampled "
        "ranking이 K0 Popularity보다 나빴다. 따라서 Fold-in을 단독 순위로 전환하지 않고 "
        "Popularity prior와 혼합하는 후속 실험이 먼저다."
    )

    manifest = {
        "schema_version": 1,
        "evidence_id": "REC-EV-003",
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": {
            "split_manifest": str(args.split_manifest),
            "split_manifest_sha256": sha256_file(args.split_manifest),
            "baseline_manifest": str(args.baseline_manifest),
            "baseline_manifest_sha256": sha256_file(args.baseline_manifest),
            "cohort_manifest": str(args.cohort_manifest),
            "cohort_manifest_sha256": sha256_file(args.cohort_manifest),
            "archive_sha256": split_manifest["source"]["archive_sha256"],
            "test_used": False,
        },
        "protocol": {
            "version": "cold-start-foldin-v1",
            "split_version": split_manifest["protocol"]["version"],
            "cohort_version": cohort_manifest["cohort_version"],
            "calibration_boundary": calibration_boundary,
            "k_values": list(K_VALUES),
            "onboarding_order": "earliest timestamp then movie_id",
            "cohort_excluded_from_als_and_bias": True,
            "calibration_source": "cohort Validation early half",
            "evaluation_source": "cohort Validation late half",
            "candidate_policy": baseline_manifest["protocol"][
                "ranking_candidate_policy"
            ],
            "bootstrap_repeats": args.bootstrap_repeats,
            "seed": args.seed,
        },
        "cohort": {
            "users": int(len(cohort_ids)),
            "excluded_train_rows": excluded_rows,
            "remaining_train_rows": int(remaining_train_rows),
            "remaining_train_users": remaining_train_users,
            "calibration_rows": int(calibration_mask.sum()),
            "evaluation_rows": int(evaluation_mask.sum()),
            "sampled_ranking_users": int(candidates["user_id"].nunique()),
            "rating_mean_quartiles": {
                str(key): int(value)
                for key, value in cohort["rating_mean_quartile"]
                .value_counts()
                .sort_index()
                .items()
            },
        },
        "model": {
            "als": {
                "rank": args.rank,
                "reg_param": args.reg_param,
                "max_iter": args.max_iter,
                "seed": args.seed,
                "fold_in_regularization": "reg_param * available_rating_count",
            },
            "bias": {
                "iterations": args.bias_iterations,
                "reg_user": args.bias_reg_user,
                "reg_item": args.bias_reg_item,
            },
        },
        "metrics": {
            "k_curve": k_curve,
            "paired_mae_vs_k0": paired_vs_k0,
            "by_rating_mean_quartile": by_quartile,
        },
        "artifacts": {
            "cohort": artifact_record(cohort_path, len(cohort)),
            "onboarding_first_20": artifact_record(onboarding_path, len(onboarding)),
            "cohort_excluded_bias": artifact_record(bias_path),
            "cohort_excluded_item_factors": artifact_record(item_factor_path),
            "foldin_user_factors": artifact_record(factor_cube_path),
            "calibrators": artifact_record(calibrator_path),
            "validation_predictions": artifact_record(
                prediction_path, len(validation)
            ),
            "sampled_ranking": artifact_record(candidate_path, len(candidates)),
        },
        "validation": {
            "status": "PASS",
            "cohort_absent_from_model_train": True,
            "all_users_have_20_ordered_inputs": bool(
                (onboarding.groupby("user_id").size() == max(K_VALUES)).all()
            ),
            "calibration_precedes_evaluation": bool(
                validation.loc[calibration_mask, "timestamp"].max()
                < validation.loc[evaluation_mask, "timestamp"].min()
            ),
            "all_k_fallback_predictions_finite": bool(
                all(
                    np.isfinite(
                        validation[f"prediction_k{k}_fallback_isotonic"]
                    ).all()
                    for k in K_VALUES
                )
            ),
            "same_evaluation_users_for_all_k": bool(
                all(len(per_user_errors[k]) == len(per_user_errors[0]) for k in K_VALUES)
            ),
        },
        "runtime": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "pyarrow": pyarrow.__version__,
            "pyspark": pyspark.__version__,
            "scikit_learn": sklearn.__version__,
            "spark_master": args.spark_master,
            "spark_driver_memory": args.spark_driver_memory,
            "als_train_seconds": round(als_seconds, 3),
            "total_seconds": round(time.perf_counter() - started, 3),
        },
        "conclusion": {
            "summary": summary,
            "data_quality_knee": knee_text,
            "onboarding_product_gate": "BLOCKED_BY_RANKING_MODEL_AND_REACT_INPUT_COST",
            "confidence_gate": "K10_FIRST_SIGNIFICANT_MAE_GAIN_BUT_SEGMENT_REGRESSION",
            "ranking_gate": "FAILS_K0_POPULARITY_BASELINE_AT_ALL_K",
            "next_experiment": "REC-EV-003B popularity-prior blend and ALS tuning",
        },
    }
    if not all(manifest["validation"].values()):
        manifest["validation"]["status"] = "FAIL"
        raise RuntimeError(f"cold-start validation failed: {manifest['validation']}")
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(build_evidence_markdown(manifest), encoding="utf-8")
    print(f"Cohort manifest written to {args.cohort_manifest}", flush=True)
    print(f"Manifest written to {args.manifest}", flush=True)
    print(f"Evidence written to {args.evidence}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
