#!/usr/bin/env python3
"""Run REC-EV-002 Bias, Popularity, ALS, and star-calibration evidence.

Only the fixed Train and Validation artifacts from REC-EV-001 are read. Test is
intentionally not accepted as a command-line input so it cannot influence model
selection or calibration.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import platform
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow
import pyspark
import sklearn
from sklearn.isotonic import IsotonicRegression


RATING_MIN = 0.5
RATING_MAX = 5.0
RATING_VALUES = np.arange(RATING_MIN, RATING_MAX + 0.5, 0.5)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split-dir", type=Path, required=True)
    parser.add_argument("--split-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--rank", type=int, default=32)
    parser.add_argument("--reg-param", type=float, default=0.1)
    parser.add_argument("--max-iter", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--bias-reg-user", type=float, default=10.0)
    parser.add_argument("--bias-reg-item", type=float, default=25.0)
    parser.add_argument("--bias-iterations", type=int, default=10)
    parser.add_argument("--popularity-prior", type=float, default=50.0)
    parser.add_argument("--calibration-fraction", type=float, default=0.5)
    parser.add_argument("--ranking-negatives", type=int, default=99)
    parser.add_argument("--spark-master", default="local[4]")
    parser.add_argument("--spark-driver-memory", default="8g")
    parser.add_argument("--spark-shuffle-partitions", type=int, default=32)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_directory(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        digest.update(item.relative_to(path).as_posix().encode("utf-8"))
        with item.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def artifact_record(path: Path, rows: int | None = None) -> dict[str, Any]:
    if path.is_dir():
        size = sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
        digest = sha256_directory(path)
    else:
        size = path.stat().st_size
        digest = sha256_file(path)
    result: dict[str, Any] = {
        "path": str(path),
        "sha256": digest,
        "bytes": size,
    }
    if rows is not None:
        result["rows"] = rows
    return result


def choose_later_boundary(timestamps: np.ndarray, fraction: float) -> int:
    if not 0 < fraction < 1:
        raise ValueError("fraction must be between 0 and 1")
    if timestamps.size < 2:
        raise ValueError("at least two timestamps are required")
    boundary = int(np.quantile(timestamps, fraction, method="higher"))
    if not bool((timestamps < boundary).any()) or not bool((timestamps >= boundary).any()):
        raise ValueError("calibration boundary collapsed")
    return boundary


def dense_sufficient_statistics(
    ids: np.ndarray, ratings: np.ndarray, length: int
) -> tuple[np.ndarray, np.ndarray]:
    counts = np.bincount(ids, minlength=length).astype(np.int64, copy=False)
    sums = np.bincount(ids, weights=ratings, minlength=length)
    return counts, sums


def fit_regularized_bias(
    user_ids: np.ndarray,
    movie_ids: np.ndarray,
    ratings: np.ndarray,
    *,
    user_size: int,
    movie_size: int,
    reg_user: float,
    reg_item: float,
    iterations: int,
) -> dict[str, np.ndarray | float]:
    if not (len(user_ids) == len(movie_ids) == len(ratings)) or len(ratings) == 0:
        raise ValueError("non-empty aligned arrays are required")
    if reg_user < 0 or reg_item < 0 or iterations < 1:
        raise ValueError("regularization must be non-negative and iterations positive")

    global_mean = float(np.mean(ratings, dtype=np.float64))
    user_counts, user_sums = dense_sufficient_statistics(user_ids, ratings, user_size)
    movie_counts, movie_sums = dense_sufficient_statistics(movie_ids, ratings, movie_size)
    user_bias = np.zeros(user_size, dtype=np.float64)
    movie_bias = np.zeros(movie_size, dtype=np.float64)

    for _ in range(iterations):
        item_residual_sums = np.bincount(
            movie_ids,
            weights=ratings - global_mean - user_bias[user_ids],
            minlength=movie_size,
        )
        movie_bias = np.divide(
            item_residual_sums,
            movie_counts + reg_item,
            out=np.zeros(movie_size, dtype=np.float64),
            where=(movie_counts + reg_item) > 0,
        )
        user_residual_sums = np.bincount(
            user_ids,
            weights=ratings - global_mean - movie_bias[movie_ids],
            minlength=user_size,
        )
        user_bias = np.divide(
            user_residual_sums,
            user_counts + reg_user,
            out=np.zeros(user_size, dtype=np.float64),
            where=(user_counts + reg_user) > 0,
        )

    return {
        "global_mean": global_mean,
        "user_counts": user_counts,
        "user_sums": user_sums,
        "movie_counts": movie_counts,
        "movie_sums": movie_sums,
        "user_bias": user_bias,
        "movie_bias": movie_bias,
    }


def safe_take(values: np.ndarray, ids: np.ndarray, default: float = 0.0) -> np.ndarray:
    result = np.full(ids.shape, default, dtype=np.float64)
    valid = (ids >= 0) & (ids < len(values))
    result[valid] = values[ids[valid]]
    return result


def score_factor_pairs(
    user_ids: np.ndarray,
    movie_ids: np.ndarray,
    user_factor_ids: np.ndarray,
    user_factor_values: np.ndarray,
    movie_factor_ids: np.ndarray,
    movie_factor_values: np.ndarray,
) -> np.ndarray:
    """Score pairs with factors exported from Spark without Hadoop file writes."""
    user_size = int(user_factor_ids.max()) + 1
    movie_size = int(movie_factor_ids.max()) + 1
    rank = user_factor_values.shape[1]
    if movie_factor_values.shape[1] != rank:
        raise ValueError("user and movie factor ranks differ")
    dense_users = np.full((user_size, rank), np.nan, dtype=np.float32)
    dense_movies = np.full((movie_size, rank), np.nan, dtype=np.float32)
    dense_users[user_factor_ids] = user_factor_values
    dense_movies[movie_factor_ids] = movie_factor_values
    result = np.full(user_ids.shape, np.nan, dtype=np.float64)
    valid = (
        (user_ids >= 0)
        & (user_ids < user_size)
        & (movie_ids >= 0)
        & (movie_ids < movie_size)
    )
    if bool(valid.any()):
        left = dense_users[user_ids[valid]]
        right = dense_movies[movie_ids[valid]]
        finite = np.isfinite(left).all(axis=1) & np.isfinite(right).all(axis=1)
        valid_positions = np.flatnonzero(valid)
        result[valid_positions[finite]] = np.einsum(
            "ij,ij->i", left[finite], right[finite], optimize=True
        )
    return result


def predict_bias(
    user_ids: np.ndarray,
    movie_ids: np.ndarray,
    global_mean: float,
    user_bias: np.ndarray,
    movie_bias: np.ndarray,
) -> np.ndarray:
    return np.clip(
        global_mean
        + safe_take(user_bias, user_ids)
        + safe_take(movie_bias, movie_ids),
        RATING_MIN,
        RATING_MAX,
    )


def predict_popularity(
    movie_ids: np.ndarray,
    global_mean: float,
    movie_counts: np.ndarray,
    movie_sums: np.ndarray,
    prior: float,
) -> np.ndarray:
    counts = safe_take(movie_counts, movie_ids)
    sums = safe_take(movie_sums, movie_ids)
    return np.clip(
        np.divide(
            sums + prior * global_mean,
            counts + prior,
            out=np.full(movie_ids.shape, global_mean, dtype=np.float64),
            where=(counts + prior) > 0,
        ),
        RATING_MIN,
        RATING_MAX,
    )


def identity_state(
    user_ids: np.ndarray,
    movie_ids: np.ndarray,
    user_counts: np.ndarray,
    movie_counts: np.ndarray,
) -> np.ndarray:
    known_user = safe_take(user_counts, user_ids) > 0
    known_movie = safe_take(movie_counts, movie_ids) > 0
    return np.select(
        [
            known_user & known_movie,
            ~known_user & known_movie,
            known_user & ~known_movie,
        ],
        ["KNOWN_USER_KNOWN_ITEM", "NEW_USER_KNOWN_ITEM", "KNOWN_USER_NEW_ITEM"],
        default="NEW_USER_NEW_ITEM",
    )


def calibration_error(
    actual: np.ndarray, predicted: np.ndarray, bins: int = 10
) -> float:
    if len(actual) == 0:
        return math.nan
    order = np.argsort(predicted, kind="stable")
    bucket_indices = np.array_split(order, min(bins, len(order)))
    weighted = 0.0
    for indices in bucket_indices:
        weighted += len(indices) * abs(
            float(np.mean(predicted[indices])) - float(np.mean(actual[indices]))
        )
    return weighted / len(actual)


def regression_metrics(
    actual: np.ndarray, predicted: np.ndarray, expected_rows: int | None = None
) -> dict[str, int | float]:
    actual = np.asarray(actual, dtype=np.float64)
    predicted = np.asarray(predicted, dtype=np.float64)
    finite = np.isfinite(actual) & np.isfinite(predicted)
    a = actual[finite]
    p = predicted[finite]
    denominator = expected_rows if expected_rows is not None else len(actual)
    if len(a) == 0:
        return {
            "rows": 0,
            "coverage": 0.0,
            "mae": math.nan,
            "rmse": math.nan,
            "mean_error": math.nan,
            "ece_decile": math.nan,
            "within_0_5": math.nan,
            "within_1_0": math.nan,
        }
    error = p - a
    absolute = np.abs(error)
    return {
        "rows": int(len(a)),
        "coverage": round(len(a) / denominator, 6) if denominator else 0.0,
        "mae": round(float(np.mean(absolute)), 6),
        "rmse": round(float(np.sqrt(np.mean(np.square(error)))), 6),
        "mean_error": round(float(np.mean(error)), 6),
        "ece_decile": round(calibration_error(a, p), 6),
        "within_0_5": round(float(np.mean(absolute <= 0.5)), 6),
        "within_1_0": round(float(np.mean(absolute <= 1.0)), 6),
    }


def fit_isotonic(raw: np.ndarray, actual: np.ndarray) -> IsotonicRegression:
    finite = np.isfinite(raw) & np.isfinite(actual)
    if int(finite.sum()) < 2:
        raise ValueError("at least two finite calibration rows are required")
    return IsotonicRegression(
        y_min=RATING_MIN,
        y_max=RATING_MAX,
        increasing=True,
        out_of_bounds="clip",
    ).fit(raw[finite], actual[finite])


def apply_isotonic(model: IsotonicRegression, raw: np.ndarray) -> np.ndarray:
    result = np.full(raw.shape, np.nan, dtype=np.float64)
    finite = np.isfinite(raw)
    result[finite] = model.predict(raw[finite])
    return np.clip(result, RATING_MIN, RATING_MAX)


def rating_midrank_ecdf(
    ratings: np.ndarray,
    user_ids: np.ndarray,
    profile_counts: np.ndarray,
    user_totals: np.ndarray,
    global_counts: np.ndarray,
    shrinkage: float = 20.0,
) -> np.ndarray:
    rating_indices = np.rint((ratings - RATING_MIN) * 2).astype(np.int64)
    rating_indices = np.clip(rating_indices, 0, len(RATING_VALUES) - 1)
    user_rows = profile_counts[user_ids]
    lower_user = np.take_along_axis(
        np.cumsum(user_rows, axis=1) - user_rows,
        rating_indices[:, None],
        axis=1,
    ).ravel()
    equal_user = np.take_along_axis(
        user_rows, rating_indices[:, None], axis=1
    ).ravel()
    global_lower_all = np.cumsum(global_counts) - global_counts
    global_total = float(global_counts.sum())
    global_ecdf = (
        global_lower_all[rating_indices] + 0.5 * global_counts[rating_indices]
    ) / global_total
    totals = user_totals[user_ids].astype(np.float64)
    user_ecdf = np.divide(
        lower_user + 0.5 * equal_user,
        totals,
        out=global_ecdf.copy(),
        where=totals > 0,
    )
    weight = totals / (totals + shrinkage)
    return weight * user_ecdf + (1.0 - weight) * global_ecdf


def build_profile_count_matrix(
    user_ids: np.ndarray, ratings: np.ndarray, user_size: int
) -> tuple[np.ndarray, np.ndarray]:
    matrix = np.zeros((user_size, len(RATING_VALUES)), dtype=np.int32)
    rating_indices = np.rint((ratings - RATING_MIN) * 2).astype(np.int64)
    np.add.at(matrix, (user_ids, rating_indices), 1)
    return matrix, matrix.sum(axis=1, dtype=np.int64)


def sample_ranking_candidates(
    train_user_ids: np.ndarray,
    train_movie_ids: np.ndarray,
    validation: pd.DataFrame,
    validation_eval_mask: np.ndarray,
    user_counts: np.ndarray,
    movie_counts: np.ndarray,
    relative_utility: np.ndarray,
    *,
    negatives: int,
    seed: int,
) -> pd.DataFrame:
    warm = (
        validation_eval_mask
        & (safe_take(user_counts, validation["user_id"].to_numpy()) > 0)
        & (safe_take(movie_counts, validation["movie_id"].to_numpy()) > 0)
        & (relative_utility >= 0.7)
    )
    positives = validation.loc[warm, ["user_id", "movie_id", "timestamp"]].copy()
    positives = positives.sort_values(["user_id", "timestamp", "movie_id"])
    positives = positives.groupby("user_id", sort=True, as_index=False).tail(1)
    if positives.empty:
        raise RuntimeError("no warm relative-utility positives for ranking diagnostic")

    positive_users = positives["user_id"].to_numpy(dtype=np.int64)
    relevant_train = np.isin(train_user_ids, positive_users)
    seen_frame = pd.DataFrame(
        {
            "user_id": train_user_ids[relevant_train],
            "movie_id": train_movie_ids[relevant_train],
        }
    )
    seen_by_user = {
        int(user): set(group["movie_id"].astype(int).tolist())
        for user, group in seen_frame.groupby("user_id", sort=False)
    }
    universe = np.flatnonzero(movie_counts > 0).astype(np.int64)
    rows: list[tuple[int, int, int]] = []
    for record in positives.itertuples(index=False):
        user_id = int(record.user_id)
        positive_movie = int(record.movie_id)
        forbidden = seen_by_user.get(user_id, set()) | {positive_movie}
        rng = np.random.default_rng(seed + user_id)
        chosen: set[int] = set()
        while len(chosen) < negatives:
            draw_size = max((negatives - len(chosen)) * 3, 128)
            for candidate in rng.choice(universe, size=draw_size, replace=True):
                movie_id = int(candidate)
                if movie_id not in forbidden:
                    chosen.add(movie_id)
                    if len(chosen) == negatives:
                        break
        rows.append((user_id, positive_movie, 1))
        rows.extend((user_id, movie_id, 0) for movie_id in sorted(chosen))
    result = pd.DataFrame(rows, columns=["user_id", "movie_id", "is_positive"])
    result["candidate_id"] = np.arange(len(result), dtype=np.int64)
    return result[["candidate_id", "user_id", "movie_id", "is_positive"]]


def sampled_ranking_metrics(candidates: pd.DataFrame, score_col: str) -> dict[str, Any]:
    required = {"user_id", "is_positive", score_col}
    if not required.issubset(candidates.columns):
        raise ValueError(f"missing columns: {sorted(required - set(candidates.columns))}")
    finite = np.isfinite(candidates[score_col].to_numpy(dtype=np.float64))
    scored = candidates.loc[finite, ["user_id", "is_positive", score_col]].copy()
    scored = scored.sort_values(
        ["user_id", score_col, "is_positive"],
        ascending=[True, False, False],
        kind="stable",
    )
    scored["rank"] = scored.groupby("user_id").cumcount() + 1
    positives = scored.loc[scored["is_positive"] == 1, ["user_id", "rank"]]
    expected_users = int(candidates["user_id"].nunique())
    users = int(len(positives))
    ranks = positives["rank"].to_numpy(dtype=np.float64)
    return {
        "protocol": "SAMPLED_1_POSITIVE_PLUS_NEGATIVES",
        "users": users,
        "user_coverage": round(users / expected_users, 6),
        "hit_rate_at_10": round(float(np.mean(ranks <= 10)), 6),
        "ndcg_at_10": round(
            float(np.mean(np.where(ranks <= 10, 1.0 / np.log2(ranks + 1), 0.0))),
            6,
        ),
        "mrr": round(float(np.mean(1.0 / ranks)), 6),
        "median_rank": round(float(np.median(ranks)), 3),
    }


def markdown_table(headers: list[str], rows: Iterable[Iterable[Any]]) -> str:
    result = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    result.extend("| " + " | ".join(str(value) for value in row) + " |" for row in rows)
    return "\n".join(result)


def metric_table(metrics: dict[str, dict[str, Any]], names: list[str]) -> str:
    return markdown_table(
        ["Model", "Rows", "Coverage", "MAE", "RMSE", "ECE", "Within ±0.5"],
        [
            (
                name,
                f"{metrics[name]['rows']:,}",
                f"{metrics[name]['coverage']:.2%}",
                f"{metrics[name]['mae']:.4f}",
                f"{metrics[name]['rmse']:.4f}",
                f"{metrics[name]['ece_decile']:.4f}",
                f"{metrics[name]['within_0_5']:.2%}",
            )
            for name in names
        ],
    )


def build_evidence_markdown(manifest: dict[str, Any]) -> str:
    metrics = manifest["metrics"]["validation_eval"]
    regression_names = [
        "global_mean",
        "popularity",
        "bias_raw",
        "bias_isotonic",
        "als_warm_raw",
        "als_warm_isotonic",
        "als_bias_fallback_raw",
        "als_bias_fallback_isotonic",
    ]
    regression_table = metric_table(metrics, regression_names)
    ranking = manifest["metrics"]["sampled_ranking"]
    ranking_table = markdown_table(
        ["Model", "Users", "Coverage", "HR@10", "NDCG@10", "MRR", "Median rank"],
        [
            (
                name,
                f"{values['users']:,}",
                f"{values['user_coverage']:.2%}",
                f"{values['hit_rate_at_10']:.4f}",
                f"{values['ndcg_at_10']:.4f}",
                f"{values['mrr']:.4f}",
                f"{values['median_rank']:.1f}",
            )
            for name, values in ranking.items()
        ],
    )
    state_rows = []
    for state, values in manifest["metrics"]["by_identity_state"].items():
        state_rows.append(
            (
                state,
                f"{values['rows']:,}",
                f"{values['bias_isotonic']['mae']:.4f}",
                f"{values['als_bias_fallback_isotonic']['mae']:.4f}",
                f"{values['als_direct_coverage']:.2%}",
            )
        )
    state_table = markdown_table(
        ["Identity state", "Rows", "Bias MAE", "ALS+fallback MAE", "ALS direct coverage"],
        state_rows,
    )
    segment_rows = []
    for segment, values in manifest["metrics"]["by_rating_mean_quartile"].items():
        segment_rows.append(
            (
                segment,
                f"{values['rows']:,}",
                f"{values['bias_isotonic']['mae']:.4f}",
                f"{values['als_warm_isotonic']['mae']:.4f}",
                f"{values['als_warm_isotonic']['ece_decile']:.4f}",
            )
        )
    segment_table = markdown_table(
        ["Train mean quartile", "Rows", "Bias MAE", "ALS warm MAE", "ALS warm ECE"],
        segment_rows,
    )
    als = manifest["model"]["als"]
    runtime = manifest["runtime"]
    conclusion = manifest["conclusion"]
    return f"""# REC-EV-002 — Bias·Popularity·ALS와 예상 별점 calibration

> 상태: `COMPLETED`  
> 생성 시각: {manifest['run_at_utc']}  
> Split: `{manifest['protocol']['split_version']}`  
> Validation 내부 protocol: `{manifest['protocol']['calibration_version']}`  
> Test 사용: `NO`

## 1. 결론

{conclusion['summary']}

- 예상 별점 숫자 UI 결정: `{conclusion['predicted_star_ui_gate']}`
- confidence 경계 결정: `{conclusion['confidence_gate']}`
- 다음 실험: `{conclusion['next_experiment']}`

## 2. 누수 방지와 비교 조건

- 모델 학습: REC-EV-001 Train만 사용
- Isotonic 보정 학습: Validation 앞 시간 구간 `< {manifest['protocol']['calibration_boundary']}`
- 아래 회귀 평가: Validation 뒤 시간 구간 `>= {manifest['protocol']['calibration_boundary']}`
- 경계 timestamp는 통째로 뒤 구간에 배치
- Test artifact는 CLI 입력도, 실행 입력도 아님
- ALS: rank `{als['rank']}`, regParam `{als['reg_param']}`, maxIter `{als['max_iter']}`, seed `{als['seed']}`
- ALS가 직접 예측하지 못한 신규 사용자·영화는 Bias fallback으로 별도 표시

## 3. 예상 별점 회귀·보정 결과

{regression_table}

`als_warm_*`는 Train에 사용자와 영화가 모두 존재해 ALS가 직접 예측한 행만 평가한다.
`als_bias_fallback_*`는 신규 상태까지 포함해 100% 응답하는 서비스 형태다. 서로 다른 coverage의
MAE만 보고 모델 우열을 단정하지 않는다.

## 4. 신규 사용자·영화 상태별 결과

{state_table}

전역 시간 분할에서는 신규 사용자가 다수이므로, ALS 자체 성능과 fallback 품질을 반드시 나눠
본다. 전체 평균 하나만 보고 ALS의 품질이라고 표현하면 안 된다.

## 5. 사용자 rating-style 구간

{segment_table}

공통 raw 4점 threshold 대신 Train 평균 구간별 오차와 calibration을 확인했다. 구간별 오차가
다르면 예상 별점 confidence를 동일하게 노출할 근거가 없다.

## 6. 동일 후보 순위 진단

> 주의: 다음 표는 `{manifest['protocol']['ranking_candidate_policy']}`이며 최종 채택용
> `FULL_CATALOG` 지표가 아니다. 모델 탈락과 파이프라인 검증에만 쓴다.

{ranking_table}

각 사용자에서 개인 Train ECDF로 계산한 relative utility 0.7 이상인 최신 Validation 평가 1개와,
Train에 존재하지만 그 사용자가 평가하지 않은 영화 {manifest['protocol']['ranking_negatives']}개를
같은 seed로 비교했다. 미평가 영화를 실제 싫어요라고 주장하지 않는다.

## 7. 비용과 재현

- Bias 학습: {runtime['bias_train_seconds']:.2f}s
- ALS 학습: {runtime['als_train_seconds']:.2f}s
- 전체 실행: {runtime['total_seconds']:.2f}s
- Spark master: `{runtime['spark_master']}`
- Python `{runtime['python']}`, PySpark `{runtime['pyspark']}`, scikit-learn `{runtime['scikit_learn']}`

```powershell
py -3 scripts/recommendation_baseline_calibration.py `
  --split-dir outputs\\recommendation-evidence\\global-time-v1 `
  --split-manifest docs\\recommendation\\evidence\\manifests\\global-time-v1.json `
  --output-dir outputs\\recommendation-evidence\\rec-ev-002 `
  --manifest docs\\recommendation\\evidence\\manifests\\rec-ev-002.json `
  --evidence docs\\recommendation\\evidence\\REC-EV-002-prediction-calibration.md

py -3 scripts/verify_recommendation_baseline.py `
  --manifest docs\\recommendation\\evidence\\manifests\\rec-ev-002.json
```

## 8. 한계

- 하이퍼파라미터 grid search와 3개 seed 비교 전의 첫 기준선이다.
- sampled ranking은 full-catalog 순위를 대체하지 않는다.
- MovieLens의 미래 신규 사용자는 기존 사용자의 cold-start 시뮬레이션과 성격이 다르다.
- 오프라인 예상 별점 오차는 실제 서비스에서 숫자 표현을 이해하는지 증명하지 않는다.
- Test는 최종 후보와 기준을 잠근 뒤에만 한 번 사용한다.
"""


def main() -> int:
    args = parse_args()
    started = time.perf_counter()
    train_path = args.split_dir / "train.parquet"
    validation_path = args.split_dir / "validation.parquet"
    profile_path = args.split_dir / "user_rating_profiles.parquet"
    for required in (train_path, validation_path, profile_path, args.split_manifest):
        if not required.exists():
            raise FileNotFoundError(required)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    split_manifest_hash = sha256_file(args.split_manifest)
    split_manifest = json.loads(args.split_manifest.read_text(encoding="utf-8"))
    if split_manifest["protocol"]["version"] != "global-time-v1":
        raise RuntimeError("REC-EV-002 requires global-time-v1")

    print("Reading fixed Train for Bias/Popularity...", flush=True)
    train = pd.read_parquet(train_path, columns=["user_id", "movie_id", "rating"])
    train_user_ids = train["user_id"].to_numpy(dtype=np.int64, copy=False)
    train_movie_ids = train["movie_id"].to_numpy(dtype=np.int64, copy=False)
    train_ratings = train["rating"].to_numpy(dtype=np.float64, copy=False)
    user_size = int(max(train_user_ids.max(), split_manifest["source"]["users"])) + 1
    movie_size = int(max(train_movie_ids.max(), split_manifest["source"]["movies"])) + 1

    bias_started = time.perf_counter()
    bias = fit_regularized_bias(
        train_user_ids,
        train_movie_ids,
        train_ratings,
        user_size=user_size,
        movie_size=movie_size,
        reg_user=args.bias_reg_user,
        reg_item=args.bias_reg_item,
        iterations=args.bias_iterations,
    )
    bias_seconds = time.perf_counter() - bias_started
    bias_path = args.output_dir / "bias_parameters.npz"
    np.savez_compressed(bias_path, **bias)

    print("Reading Validation and building leakage-safe calibration halves...", flush=True)
    validation = pd.read_parquet(validation_path).reset_index(drop=True)
    validation["row_id"] = np.arange(len(validation), dtype=np.int64)
    calibration_boundary = choose_later_boundary(
        validation["timestamp"].to_numpy(dtype=np.int64), args.calibration_fraction
    )
    calibration_fit_mask = validation["timestamp"].to_numpy() < calibration_boundary
    validation_eval_mask = ~calibration_fit_mask

    user_counts = bias["user_counts"]
    movie_counts = bias["movie_counts"]
    user_sums = bias["user_sums"]
    movie_sums = bias["movie_sums"]
    user_bias = bias["user_bias"]
    movie_bias = bias["movie_bias"]
    global_mean = float(bias["global_mean"])
    val_users = validation["user_id"].to_numpy(dtype=np.int64)
    val_movies = validation["movie_id"].to_numpy(dtype=np.int64)
    actual = validation["rating"].to_numpy(dtype=np.float64)
    validation["identity_state"] = identity_state(
        val_users, val_movies, user_counts, movie_counts
    )
    validation["prediction_global"] = global_mean
    validation["prediction_popularity"] = predict_popularity(
        val_movies,
        global_mean,
        movie_counts,
        movie_sums,
        args.popularity_prior,
    )
    validation["prediction_bias_raw"] = predict_bias(
        val_users, val_movies, global_mean, user_bias, movie_bias
    )

    profiles = pd.read_parquet(profile_path)
    quartile_map = profiles.set_index("user_id")["rating_mean_quartile"]
    validation["rating_mean_quartile"] = (
        validation["user_id"].map(quartile_map).fillna("NEW_USER")
    )
    profile_count_matrix, profile_totals = build_profile_count_matrix(
        train_user_ids, train_ratings, user_size
    )
    global_rating_counts = profile_count_matrix.sum(axis=0, dtype=np.int64)
    relative_utility = np.full(len(validation), np.nan, dtype=np.float64)
    known_user_rows = safe_take(user_counts, val_users) > 0
    relative_utility[known_user_rows] = rating_midrank_ecdf(
        actual[known_user_rows],
        val_users[known_user_rows],
        profile_count_matrix,
        profile_totals,
        global_rating_counts,
    )
    validation["relative_utility"] = relative_utility

    candidates = sample_ranking_candidates(
        train_user_ids,
        train_movie_ids,
        validation,
        validation_eval_mask,
        user_counts,
        movie_counts,
        relative_utility,
        negatives=args.ranking_negatives,
        seed=args.seed,
    )
    candidate_users = candidates["user_id"].to_numpy(dtype=np.int64)
    candidate_movies = candidates["movie_id"].to_numpy(dtype=np.int64)
    candidates["score_popularity"] = predict_popularity(
        candidate_movies,
        global_mean,
        movie_counts,
        movie_sums,
        args.popularity_prior,
    )
    candidates["score_bias"] = predict_bias(
        candidate_users, candidate_movies, global_mean, user_bias, movie_bias
    )
    candidate_path = args.output_dir / "sampled_ranking_candidates.parquet"
    candidates.to_parquet(candidate_path, index=False, compression="zstd")

    train_rows = len(train_user_ids)
    # Explicit names keep the large 25.6M-row numpy views out of the Spark phase.
    del train, profiles, profile_count_matrix, train_user_ids, train_movie_ids, train_ratings
    gc.collect()

    print(
        f"Training Spark ALS on {split_manifest['splits']['train']['rows']:,} rows...",
        flush=True,
    )
    from pyspark.ml.recommendation import ALS
    from pyspark.sql import SparkSession

    spark = (
        SparkSession.builder.master(args.spark_master)
        .appName("feelm-rec-ev-002")
        .config("spark.driver.memory", args.spark_driver_memory)
        .config("spark.sql.shuffle.partitions", str(args.spark_shuffle_partitions))
        .config("spark.sql.execution.arrow.pyspark.enabled", "true")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("WARN")
    als_train_started = time.perf_counter()
    spark_train = spark.read.parquet(str(train_path.resolve())).select(
        "user_id", "movie_id", "rating"
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
    als_model = estimator.fit(spark_train)
    als_seconds = time.perf_counter() - als_train_started
    # Spark/Hadoop local writes require winutils.exe on Windows. Export the learned
    # factors through the driver and use the exact ALS dot product locally instead.
    user_factor_frame = als_model.userFactors.toPandas().sort_values("id")
    movie_factor_frame = als_model.itemFactors.toPandas().sort_values("id")
    spark.stop()

    user_factor_ids = user_factor_frame["id"].to_numpy(dtype=np.int64)
    movie_factor_ids = movie_factor_frame["id"].to_numpy(dtype=np.int64)
    user_factor_values = np.asarray(
        user_factor_frame["features"].tolist(), dtype=np.float32
    )
    movie_factor_values = np.asarray(
        movie_factor_frame["features"].tolist(), dtype=np.float32
    )
    model_path = args.output_dir / "als_factors.npz"
    np.savez_compressed(
        model_path,
        user_ids=user_factor_ids,
        user_factors=user_factor_values,
        movie_ids=movie_factor_ids,
        movie_factors=movie_factor_values,
    )
    validation["prediction_als_raw"] = score_factor_pairs(
        val_users,
        val_movies,
        user_factor_ids,
        user_factor_values,
        movie_factor_ids,
        movie_factor_values,
    )
    candidates["score_als"] = score_factor_pairs(
        candidate_users,
        candidate_movies,
        user_factor_ids,
        user_factor_values,
        movie_factor_ids,
        movie_factor_values,
    )

    validation["prediction_als_bias_fallback_raw"] = validation[
        "prediction_als_raw"
    ].where(
        np.isfinite(validation["prediction_als_raw"]),
        validation["prediction_bias_raw"],
    )

    calibrators: dict[str, IsotonicRegression] = {}
    for raw_name, calibrated_name in (
        ("prediction_bias_raw", "prediction_bias_isotonic"),
        ("prediction_als_raw", "prediction_als_isotonic"),
        (
            "prediction_als_bias_fallback_raw",
            "prediction_als_bias_fallback_isotonic",
        ),
    ):
        raw = validation[raw_name].to_numpy(dtype=np.float64)
        model = fit_isotonic(raw[calibration_fit_mask], actual[calibration_fit_mask])
        calibrators[raw_name] = model
        validation[calibrated_name] = apply_isotonic(model, raw)

    prediction_path = args.output_dir / "validation_predictions.parquet"
    validation.to_parquet(prediction_path, index=False, compression="zstd")
    scored_candidate_path = args.output_dir / "sampled_ranking_scored.parquet"
    candidates.to_parquet(scored_candidate_path, index=False, compression="zstd")

    eval_frame = validation.loc[validation_eval_mask]
    eval_actual = eval_frame["rating"].to_numpy(dtype=np.float64)
    expected_eval = len(eval_frame)
    metric_columns = {
        "global_mean": "prediction_global",
        "popularity": "prediction_popularity",
        "bias_raw": "prediction_bias_raw",
        "bias_isotonic": "prediction_bias_isotonic",
        "als_warm_raw": "prediction_als_raw",
        "als_warm_isotonic": "prediction_als_isotonic",
        "als_bias_fallback_raw": "prediction_als_bias_fallback_raw",
        "als_bias_fallback_isotonic": "prediction_als_bias_fallback_isotonic",
    }
    overall_metrics = {
        name: regression_metrics(
            eval_actual,
            eval_frame[column].to_numpy(dtype=np.float64),
            expected_rows=expected_eval,
        )
        for name, column in metric_columns.items()
    }

    by_identity: dict[str, Any] = {}
    for state, frame in eval_frame.groupby("identity_state", sort=True):
        state_actual = frame["rating"].to_numpy(dtype=np.float64)
        by_identity[str(state)] = {
            "rows": int(len(frame)),
            "bias_isotonic": regression_metrics(
                state_actual,
                frame["prediction_bias_isotonic"].to_numpy(dtype=np.float64),
            ),
            "als_bias_fallback_isotonic": regression_metrics(
                state_actual,
                frame["prediction_als_bias_fallback_isotonic"].to_numpy(dtype=np.float64),
            ),
            "als_direct_coverage": round(
                float(np.isfinite(frame["prediction_als_raw"]).mean()), 6
            ),
        }

    warm_eval = eval_frame.loc[
        eval_frame["identity_state"] == "KNOWN_USER_KNOWN_ITEM"
    ]
    by_quartile: dict[str, Any] = {}
    for quartile, frame in warm_eval.groupby("rating_mean_quartile", sort=True):
        segment_actual = frame["rating"].to_numpy(dtype=np.float64)
        by_quartile[str(quartile)] = {
            "rows": int(len(frame)),
            "bias_isotonic": regression_metrics(
                segment_actual,
                frame["prediction_bias_isotonic"].to_numpy(dtype=np.float64),
            ),
            "als_warm_isotonic": regression_metrics(
                segment_actual,
                frame["prediction_als_isotonic"].to_numpy(dtype=np.float64),
            ),
        }

    ranking_metrics = {
        "popularity": sampled_ranking_metrics(candidates, "score_popularity"),
        "bias": sampled_ranking_metrics(candidates, "score_bias"),
        "als": sampled_ranking_metrics(candidates, "score_als"),
    }

    als_warm = overall_metrics["als_warm_isotonic"]
    bias_all = overall_metrics["bias_isotonic"]
    als_fallback = overall_metrics["als_bias_fallback_isotonic"]
    direct_coverage = overall_metrics["als_warm_raw"]["coverage"]
    summary = (
        f"Validation 뒤 구간에서 ALS 직접 예측 coverage는 {direct_coverage:.2%}다. "
        f"Warm 행의 보정 ALS MAE는 {als_warm['mae']:.4f}, 전체 행에서 Bias fallback을 "
        f"합친 보정 MAE는 {als_fallback['mae']:.4f}, 보정 Bias MAE는 "
        f"{bias_all['mae']:.4f}였다. 따라서 ALS warm 성능을 전체 사용자 성능으로 확대 "
        "해석할 수 없고, 예상 별점 숫자 노출은 cold-start·구간별 confidence 실험 뒤에 결정한다."
    )

    calibrator_record = {
        name: {
            "x_thresholds": model.X_thresholds_.tolist(),
            "y_thresholds": model.y_thresholds_.tolist(),
        }
        for name, model in calibrators.items()
    }
    calibrator_path = args.output_dir / "isotonic_calibrators.json"
    calibrator_path.write_text(
        json.dumps(calibrator_record, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    manifest = {
        "schema_version": 1,
        "evidence_id": "REC-EV-002",
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": {
            "split_manifest": str(args.split_manifest),
            "split_manifest_sha256": split_manifest_hash,
            "archive_sha256": split_manifest["source"]["archive_sha256"],
            "train_rows": int(train_rows),
            "validation_rows": int(len(validation)),
            "test_used": False,
        },
        "protocol": {
            "split_version": split_manifest["protocol"]["version"],
            "calibration_version": "validation-forward-half-v1",
            "calibration_boundary": calibration_boundary,
            "calibration_fit_rows": int(calibration_fit_mask.sum()),
            "validation_eval_rows": int(validation_eval_mask.sum()),
            "same_timestamp_policy": "entire boundary timestamp goes to later half",
            "calibration_model": "isotonic-regression-clipped-0.5-5.0",
            "ranking_candidate_policy": "SAMPLED_1_POSITIVE_PLUS_99_NEGATIVES",
            "ranking_negatives": args.ranking_negatives,
            "ranking_positive": "latest warm Validation-eval item with user-ecdf-shrunk-v1 >= 0.7",
            "relative_utility_shrinkage": 20.0,
            "seed": args.seed,
        },
        "model": {
            "bias": {
                "iterations": args.bias_iterations,
                "reg_user": args.bias_reg_user,
                "reg_item": args.bias_reg_item,
            },
            "popularity": {"bayesian_prior_count": args.popularity_prior},
            "als": {
                "rank": args.rank,
                "reg_param": args.reg_param,
                "max_iter": args.max_iter,
                "seed": args.seed,
                "implicit_prefs": False,
                "nonnegative": False,
            },
        },
        "metrics": {
            "validation_eval": overall_metrics,
            "by_identity_state": by_identity,
            "by_rating_mean_quartile": by_quartile,
            "sampled_ranking": ranking_metrics,
        },
        "artifacts": {
            "bias_parameters": artifact_record(bias_path),
            "validation_predictions": artifact_record(prediction_path, len(validation)),
            "sampled_ranking_candidates": artifact_record(
                scored_candidate_path, len(candidates)
            ),
            "isotonic_calibrators": artifact_record(calibrator_path),
            "als_model": artifact_record(model_path),
        },
        "validation": {
            "status": "PASS",
            "train_precedes_calibration": bool(
                split_manifest["splits"]["train"]["latest_utc"]
                < split_manifest["splits"]["validation"]["earliest_utc"]
            ),
            "calibration_precedes_eval": bool(
                validation.loc[calibration_fit_mask, "timestamp"].max()
                < validation.loc[validation_eval_mask, "timestamp"].min()
            ),
            "all_validation_rows_scored_by_bias_fallback": bool(
                np.isfinite(validation["prediction_als_bias_fallback_isotonic"]).all()
            ),
            "als_direct_rows_match_warm_state": bool(
                np.array_equal(
                    np.isfinite(validation["prediction_als_raw"].to_numpy()),
                    validation["identity_state"].to_numpy()
                    == "KNOWN_USER_KNOWN_ITEM",
                )
            ),
        },
        "runtime": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "pyarrow": pyarrow.__version__,
            "pyspark": pyspark.__version__,
            "scikit_learn": sklearn.__version__,
            "java_home_required": "Java 17+",
            "spark_master": args.spark_master,
            "spark_driver_memory": args.spark_driver_memory,
            "bias_train_seconds": round(bias_seconds, 3),
            "als_train_seconds": round(als_seconds, 3),
            "total_seconds": round(time.perf_counter() - started, 3),
        },
        "conclusion": {
            "summary": summary,
            "predicted_star_ui_gate": "WAITING_FOR_REC-EV-003_AND_UI_COMPARISON",
            "confidence_gate": "WAITING_FOR_COLD_START_AND_ERROR_SEGMENTS",
            "next_experiment": "REC-EV-003 K0~K20 cold-start simulation",
        },
    }

    if not all(manifest["validation"].values()):
        manifest["validation"]["status"] = "FAIL"
        raise RuntimeError(f"evidence validation failed: {manifest['validation']}")
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(build_evidence_markdown(manifest), encoding="utf-8")
    print(f"Manifest written to {args.manifest}", flush=True)
    print(f"Evidence written to {args.evidence}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
