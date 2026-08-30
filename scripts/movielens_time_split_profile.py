#!/usr/bin/env python3
"""Build a leakage-safe global time split and MovieLens user rating-style profile."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import sys
import zipfile
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow


RATING_VALUES = tuple(value / 2 for value in range(1, 11))
RATING_COLUMNS = [f"rating_{value:.1f}_count" for value in RATING_VALUES]
OUTPUT_COLUMNS = ["user_id", "movie_id", "rating", "timestamp"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--protocol-version", default="global-time-v1")
    parser.add_argument("--train-fraction", type=float, default=0.80)
    parser.add_argument("--validation-fraction", type=float, default=0.10)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def zip_member(archive: zipfile.ZipFile, basename: str) -> str:
    matches = [name for name in archive.namelist() if name.endswith("/" + basename)]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one {basename}; found {matches}")
    return matches[0]


def read_ratings(archive_path: Path) -> pd.DataFrame:
    print("Reading MovieLens ratings.csv...", flush=True)
    with zipfile.ZipFile(archive_path) as archive:
        with archive.open(zip_member(archive, "ratings.csv")) as handle:
            ratings = pd.read_csv(
                handle,
                dtype={
                    "userId": "int32",
                    "movieId": "int32",
                    "rating": "float32",
                    "timestamp": "int64",
                },
            )
    ratings = ratings.rename(
        columns={"userId": "user_id", "movieId": "movie_id"}
    )
    if list(ratings.columns) != OUTPUT_COLUMNS:
        raise RuntimeError(f"Unexpected ratings columns: {list(ratings.columns)}")
    if ratings.isna().any().any():
        raise RuntimeError("ratings.csv contains null values")
    invalid = ~ratings["rating"].isin(RATING_VALUES)
    if bool(invalid.any()):
        raise RuntimeError(
            f"Unexpected rating values: {ratings.loc[invalid, 'rating'].unique().tolist()}"
        )
    return ratings


def choose_boundaries(
    timestamps: np.ndarray,
    train_fraction: float,
    validation_fraction: float,
) -> tuple[int, int]:
    if not 0 < train_fraction < 1:
        raise ValueError("train_fraction must be between 0 and 1")
    if not 0 < validation_fraction < 1:
        raise ValueError("validation_fraction must be between 0 and 1")
    if train_fraction + validation_fraction >= 1:
        raise ValueError("train + validation fractions must be less than 1")
    if len(timestamps) < 3:
        raise ValueError("at least three ratings are required")

    train_boundary = int(
        np.quantile(timestamps, train_fraction, method="higher")
    )
    validation_boundary = int(
        np.quantile(
            timestamps,
            train_fraction + validation_fraction,
            method="higher",
        )
    )
    if train_boundary >= validation_boundary:
        raise ValueError("time boundaries collapsed; timestamps lack enough variation")
    return train_boundary, validation_boundary


def split_masks(
    timestamps: pd.Series, train_boundary: int, validation_boundary: int
) -> dict[str, pd.Series]:
    return {
        "train": timestamps < train_boundary,
        "validation": (timestamps >= train_boundary)
        & (timestamps < validation_boundary),
        "test": timestamps >= validation_boundary,
    }


def utc_timestamp(timestamp: int | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def numeric_summary(values: pd.Series) -> dict[str, float | int | None]:
    clean = values.dropna()
    if clean.empty:
        return {
            "min": None,
            "p10": None,
            "p25": None,
            "median": None,
            "p75": None,
            "p90": None,
            "max": None,
        }
    quantiles = clean.quantile([0.10, 0.25, 0.50, 0.75, 0.90])
    return {
        "min": round(float(clean.min()), 6),
        "p10": round(float(quantiles.loc[0.10]), 6),
        "p25": round(float(quantiles.loc[0.25]), 6),
        "median": round(float(quantiles.loc[0.50]), 6),
        "p75": round(float(quantiles.loc[0.75]), 6),
        "p90": round(float(quantiles.loc[0.90]), 6),
        "max": round(float(clean.max()), 6),
    }


def count_bucket(value: int) -> str:
    if value < 3:
        return "K1-2"
    if value < 5:
        return "K3-4"
    if value < 10:
        return "K5-9"
    if value < 20:
        return "K10-19"
    if value < 50:
        return "K20-49"
    if value < 100:
        return "K50-99"
    return "K100+"


def build_user_profiles(train: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    grouped = train.groupby("user_id", sort=True)["rating"]
    profiles = grouped.agg(
        rating_count="count",
        rating_mean="mean",
        rating_std="std",
        rating_min="min",
        rating_max="max",
        distinct_rating_values="nunique",
    )
    profiles["rating_std"] = profiles["rating_std"].fillna(0.0)

    counts = (
        train.groupby(["user_id", "rating"], sort=True)
        .size()
        .unstack(fill_value=0)
        .reindex(columns=RATING_VALUES, fill_value=0)
    )
    counts.columns = RATING_COLUMNS
    profiles = profiles.join(counts)
    profiles["rating_span"] = profiles["rating_max"] - profiles["rating_min"]
    profiles["raw_4plus_rate"] = (
        profiles[[f"rating_{value:.1f}_count" for value in RATING_VALUES if value >= 4.0]]
        .sum(axis=1)
        .div(profiles["rating_count"])
    )

    mean_quartiles = profiles["rating_mean"].quantile([0.25, 0.50, 0.75])
    q25, q50, q75 = (float(mean_quartiles.loc[value]) for value in (0.25, 0.50, 0.75))
    profiles["rating_mean_quartile"] = np.select(
        [
            profiles["rating_mean"] <= q25,
            profiles["rating_mean"] <= q50,
            profiles["rating_mean"] <= q75,
        ],
        ["Q1_LOWER_MEAN", "Q2", "Q3"],
        default="Q4_HIGHER_MEAN",
    )
    profiles["train_history_bucket"] = profiles["rating_count"].map(count_bucket)
    profiles = profiles.reset_index()

    quartile_rows: list[dict[str, Any]] = []
    for name, group in profiles.groupby("rating_mean_quartile", sort=True):
        quartile_rows.append(
            {
                "quartile": str(name),
                "users": int(len(group)),
                "mean_rating": round(float(group["rating_mean"].mean()), 4),
                "median_raw_4plus_rate": round(
                    float(group["raw_4plus_rate"].median()), 4
                ),
                "p10_raw_4plus_rate": round(
                    float(group["raw_4plus_rate"].quantile(0.10)), 4
                ),
                "p90_raw_4plus_rate": round(
                    float(group["raw_4plus_rate"].quantile(0.90)), 4
                ),
            }
        )

    history_order = ["K1-2", "K3-4", "K5-9", "K10-19", "K20-49", "K50-99", "K100+"]
    history_counts = profiles["train_history_bucket"].value_counts()
    summary = {
        "train_users": int(len(profiles)),
        "rating_count_per_user": numeric_summary(profiles["rating_count"]),
        "rating_mean_per_user": numeric_summary(profiles["rating_mean"]),
        "rating_std_per_user": numeric_summary(profiles["rating_std"]),
        "rating_span_per_user": numeric_summary(profiles["rating_span"]),
        "raw_4plus_rate_per_user": numeric_summary(profiles["raw_4plus_rate"]),
        "mean_quartile_boundaries": {
            "q25": round(q25, 6),
            "q50": round(q50, 6),
            "q75": round(q75, 6),
        },
        "by_rating_mean_quartile": quartile_rows,
        "by_train_history_bucket": {
            bucket: int(history_counts.get(bucket, 0)) for bucket in history_order
        },
        "users_with_no_raw_4plus": int((profiles["raw_4plus_rate"] == 0).sum()),
        "users_with_only_raw_4plus": int((profiles["raw_4plus_rate"] == 1).sum()),
    }
    return profiles, summary


def split_summary(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "rows": 0,
            "users": 0,
            "movies": 0,
            "earliest_utc": None,
            "latest_utc": None,
            "rating_value_counts": {},
        }
    return {
        "rows": int(len(frame)),
        "users": int(frame["user_id"].nunique()),
        "movies": int(frame["movie_id"].nunique()),
        "earliest_utc": utc_timestamp(int(frame["timestamp"].min())),
        "latest_utc": utc_timestamp(int(frame["timestamp"].max())),
        "rating_value_counts": {
            f"{float(key):.1f}": int(value)
            for key, value in frame["rating"].value_counts().sort_index().items()
        },
    }


def cold_identity_summary(
    frame: pd.DataFrame, train_users: set[int], train_movies: set[int]
) -> dict[str, int | float]:
    known_user = frame["user_id"].isin(train_users)
    known_movie = frame["movie_id"].isin(train_movies)
    counts = {
        "known_user_known_movie": int((known_user & known_movie).sum()),
        "new_user_known_movie": int((~known_user & known_movie).sum()),
        "known_user_new_movie": int((known_user & ~known_movie).sum()),
        "new_user_new_movie": int((~known_user & ~known_movie).sum()),
    }
    total = len(frame)
    return {
        **counts,
        "known_users": int(frame.loc[known_user, "user_id"].nunique()),
        "new_users": int(frame.loc[~known_user, "user_id"].nunique()),
        "known_movies": int(frame.loc[known_movie, "movie_id"].nunique()),
        "new_movies": int(frame.loc[~known_movie, "movie_id"].nunique()),
        "row_coverage_known_user_known_movie": round(
            counts["known_user_known_movie"] / total, 6
        )
        if total
        else 0.0,
    }


def validate_splits(splits: dict[str, pd.DataFrame], source_rows: int) -> dict[str, Any]:
    errors: list[str] = []
    row_sum = sum(len(frame) for frame in splits.values())
    if row_sum != source_rows:
        errors.append(f"row count mismatch: {row_sum} != {source_rows}")

    train = splits["train"]
    validation = splits["validation"]
    test = splits["test"]
    if train.empty or validation.empty or test.empty:
        errors.append("one or more splits are empty")
    else:
        if int(train["timestamp"].max()) >= int(validation["timestamp"].min()):
            errors.append("train timestamp overlaps validation")
        if int(validation["timestamp"].max()) >= int(test["timestamp"].min()):
            errors.append("validation timestamp overlaps test")

    if errors:
        raise RuntimeError("; ".join(errors))
    return {
        "status": "PASS",
        "row_count_preserved": True,
        "strict_time_order": True,
        "same_timestamp_not_split_across_boundaries": True,
    }


def markdown_table(headers: list[str], rows: Iterable[Iterable[Any]]) -> str:
    result = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    result.extend("| " + " | ".join(str(value) for value in row) + " |" for row in rows)
    return "\n".join(result)


def format_number(value: int | float) -> str:
    if isinstance(value, int):
        return f"{value:,}"
    return f"{value:,.4f}"


def build_evidence_markdown(manifest: dict[str, Any]) -> str:
    splits = manifest["splits"]
    profile = manifest["rating_style"]
    quartiles = profile["by_rating_mean_quartile"]
    history = profile["by_train_history_bucket"]
    source_rows = manifest["source"]["rating_rows"]

    split_table = markdown_table(
        ["Split", "Rows", "Rate", "Users", "Movies", "Time range (UTC)"],
        [
            (
                name,
                f"{item['rows']:,}",
                f"{item['rows'] / source_rows:.2%}",
                f"{item['users']:,}",
                f"{item['movies']:,}",
                f"{item['earliest_utc']} → {item['latest_utc']}",
            )
            for name, item in splits.items()
        ],
    )
    quartile_table = markdown_table(
        ["Train mean quartile", "Users", "Mean rating", "Median raw 4+ rate", "P10~P90"],
        [
            (
                row["quartile"],
                f"{row['users']:,}",
                f"{row['mean_rating']:.3f}",
                f"{row['median_raw_4plus_rate']:.1%}",
                f"{row['p10_raw_4plus_rate']:.1%}~{row['p90_raw_4plus_rate']:.1%}",
            )
            for row in quartiles
        ],
    )
    history_table = markdown_table(
        ["Train history", "Users"],
        [(bucket, f"{count:,}") for bucket, count in history.items()],
    )
    identity_table = markdown_table(
        [
            "Split",
            "Warm rows",
            "Warm coverage",
            "New-user rows / users",
            "New-item rows / movies",
            "Both-new rows",
        ],
        [
            (
                name,
                f"{item['identity_vs_train']['known_user_known_movie']:,}",
                f"{item['identity_vs_train']['row_coverage_known_user_known_movie']:.2%}",
                f"{item['identity_vs_train']['new_user_known_movie']:,} / {item['identity_vs_train']['new_users']:,}",
                f"{item['identity_vs_train']['known_user_new_movie']:,} / {item['identity_vs_train']['new_movies']:,}",
                f"{item['identity_vs_train']['new_user_new_movie']:,}",
            )
            for name, item in splits.items()
            if name != "train"
        ],
    )

    return f"""# REC-EV-001 — MovieLens 시간 분할·사용자 rating-style

> 상태: `COMPLETED`  
> 생성 시각: {manifest['run_at_utc']}  
> Protocol: `{manifest['protocol']['version']}`  
> Source SHA-256: `{manifest['source']['archive_sha256']}`

## 1. 결론

MovieLens 전체 {source_rows:,}개 평점을 전역 timestamp 기준으로 분할했고, 경계 timestamp를 통째로
뒤 split에 배치해 Train→Validation→Test 사이의 시간 중첩을 제거했다. 검증 결과는
`{manifest['validation']['status']}`다.

사용자별 Train 평균과 raw 4점 이상 비율은 넓게 다르다. 따라서 모든 사용자에게 `rating >= 4`를
동일한 추천 성공 기준으로 쓰지 않는다. 이 결과는 사용자 개인 척도로 정규화하는
`user-ecdf-shrunk-v1`의 필요성을 뒷받침하지만, 어떤 추천 모델이 우수한지는 아직 판단하지 않는다.

또한 기존 사용자·기존 영화로만 평가할 수 있는 warm row coverage는 Validation
`{splits['validation']['identity_vs_train']['row_coverage_known_user_known_movie']:.2%}`, Test
`{splits['test']['identity_vs_train']['row_coverage_known_user_known_movie']:.2%}`다. 전역 시간 분할은
실제 미래의 신규 사용자·영화 fallback을 평가하는 대표 프로토콜로 유지하되, 개인화 모델 자체는
같은 사용자의 과거가 있는 warm-user diagnostic으로 별도 평가해야 한다.

## 2. 고정 전역 시간 분할

{split_table}

경계:

- Train: timestamp `< {manifest['protocol']['train_boundary']}`
- Validation: `{manifest['protocol']['train_boundary']} <= timestamp < {manifest['protocol']['validation_boundary']}`
- Test: timestamp `>= {manifest['protocol']['validation_boundary']}`

{identity_table}

신규 사용자·신규 영화 행을 제거하지 않았다. 후속 모델은 warm coverage와 fallback 포함 coverage를
분리해 보고해야 한다.

## 3. 사용자별 rating-style 차이

Train 사용자 평균 분포:

- P10 `{profile['rating_mean_per_user']['p10']:.3f}`
- P25 `{profile['rating_mean_per_user']['p25']:.3f}`
- Median `{profile['rating_mean_per_user']['median']:.3f}`
- P75 `{profile['rating_mean_per_user']['p75']:.3f}`
- P90 `{profile['rating_mean_per_user']['p90']:.3f}`

{quartile_table}

`raw 4+ rate`는 사용자가 Train에서 준 평점 중 4점 이상 비율이다. 이것은 사용자의 영화 선택과
점수 사용 습관이 함께 섞인 관측치이며 성격상의 엄격함을 뜻하지 않는다. 그럼에도 quartile별
차이가 크면 공통 4점 threshold가 사용자마다 다른 양성 비율을 만든다는 사실은 변하지 않는다.

- raw 4+가 한 번도 없는 Train 사용자: {profile['users_with_no_raw_4plus']:,}명
- Train 평점이 모두 raw 4+인 사용자: {profile['users_with_only_raw_4plus']:,}명

## 4. Train 이력량

{history_table}

MovieLens 전체 사용자는 최소 20편을 평가했지만 전역 시간 분할의 Train 시점에는 이력이 더 적은
사용자가 존재한다. 이 분포는 K0/K3/K5/K10 cold-start 실험의 실제 대상 크기를 정하는 근거다.
다만 자연적으로 Train 이력이 20편 미만인 사용자는
{sum(history[bucket] for bucket in ('K1-2', 'K3-4', 'K5-9', 'K10-19')):,}명뿐이므로, 안정적인
cold-start 비교는 충분한 이력이 있는 사용자의 최초 K개만 의도적으로 남기는 시뮬레이션으로 만든다.

## 5. 검증과 재현

- Source row 보존: `{manifest['validation']['row_count_preserved']}`
- Strict time order: `{manifest['validation']['strict_time_order']}`
- 경계 timestamp 분리 금지: `{manifest['validation']['same_timestamp_not_split_across_boundaries']}`
- 사용자 profile 입력: Train only
- 분할 artifact와 profile checksum: manifest에 기록

재현 명령:

```powershell
py -3 scripts/movielens_time_split_profile.py `
  --archive C:\\higher\\projects\\MM\\data\\raw\\ml-32m.zip `
  --output-dir outputs\\recommendation-evidence\\global-time-v1 `
  --manifest docs\\recommendation\\evidence\\manifests\\global-time-v1.json `
  --evidence docs\\recommendation\\evidence\\REC-EV-001-rating-style.md
```

## 6. 판단 가능한 것과 불가능한 것

판단 가능:

- 공통 raw `4점 이상`을 사용자 공통 만족 기준으로 사용하면 사용자별 양성 비율이 달라진다.
- 후속 추천 평가는 개인 Train 분포와 이력량을 반영해야 한다.
- Validation/Test에는 Train에 없던 사용자·영화 fallback 평가가 필요하다.

아직 판단 불가:

- 예상 별점을 화면에 표시할지
- K5와 K10 중 어느 onboarding 부담이 적절한지
- ALS·Hybrid 중 어느 모델이 더 나은지
- confidence 경계와 탐험 손실 허용치

다음 evidence는 `REC-EV-002` Bias·Popularity·ALS 기준선과 예상 별점 calibration이다.
"""


def main() -> int:
    args = parse_args()
    if not args.archive.exists():
        print(f"Archive not found: {args.archive}", file=sys.stderr)
        return 2

    archive_hash = sha256_file(args.archive)
    ratings = read_ratings(args.archive)
    timestamps = ratings["timestamp"].to_numpy(copy=False)
    train_boundary, validation_boundary = choose_boundaries(
        timestamps, args.train_fraction, args.validation_fraction
    )
    masks = split_masks(ratings["timestamp"], train_boundary, validation_boundary)
    splits = {name: ratings.loc[mask, OUTPUT_COLUMNS] for name, mask in masks.items()}
    validation = validate_splits(splits, len(ratings))

    print(
        "Boundaries: "
        f"train<{utc_timestamp(train_boundary)}, "
        f"validation<{utc_timestamp(validation_boundary)}",
        flush=True,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    artifact_paths: dict[str, Path] = {}
    artifact_hashes: dict[str, str] = {}
    for name, frame in splits.items():
        path = args.output_dir / f"{name}.parquet"
        print(f"Writing {name}: {len(frame):,} rows -> {path}", flush=True)
        frame.to_parquet(path, index=False, compression="zstd")
        artifact_paths[name] = path
        artifact_hashes[name] = sha256_file(path)

    profiles, rating_style = build_user_profiles(splits["train"])
    profile_path = args.output_dir / "user_rating_profiles.parquet"
    profiles.to_parquet(profile_path, index=False, compression="zstd")
    artifact_paths["user_rating_profiles"] = profile_path
    artifact_hashes["user_rating_profiles"] = sha256_file(profile_path)

    train_users = set(int(value) for value in splits["train"]["user_id"].unique())
    train_movies = set(int(value) for value in splits["train"]["movie_id"].unique())
    split_stats: dict[str, Any] = {}
    for name, frame in splits.items():
        item = split_summary(frame)
        if name != "train":
            item["identity_vs_train"] = cold_identity_summary(
                frame, train_users, train_movies
            )
        split_stats[name] = item

    manifest = {
        "schema_version": 1,
        "evidence_id": "REC-EV-001",
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": {
            "archive": str(args.archive),
            "archive_sha256": archive_hash,
            "rating_rows": int(len(ratings)),
            "users": int(ratings["user_id"].nunique()),
            "movies": int(ratings["movie_id"].nunique()),
        },
        "protocol": {
            "version": args.protocol_version,
            "train_fraction_target": args.train_fraction,
            "validation_fraction_target": args.validation_fraction,
            "test_fraction_target": round(
                1 - args.train_fraction - args.validation_fraction, 10
            ),
            "boundary_quantile_method": "higher",
            "same_timestamp_policy": "entire boundary timestamp goes to later split",
            "train_boundary": train_boundary,
            "train_boundary_utc": utc_timestamp(train_boundary),
            "validation_boundary": validation_boundary,
            "validation_boundary_utc": utc_timestamp(validation_boundary),
            "user_profile_source": "train only",
        },
        "splits": split_stats,
        "rating_style": rating_style,
        "artifacts": {
            name: {
                "path": str(path),
                "sha256": artifact_hashes[name],
                "bytes": path.stat().st_size,
            }
            for name, path in artifact_paths.items()
        },
        "validation": validation,
        "runtime": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "pyarrow": pyarrow.__version__,
        },
    }

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
