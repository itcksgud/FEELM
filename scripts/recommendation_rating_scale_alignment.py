#!/usr/bin/env python3
"""REC-EV-003C: compare rating-scale alignment options without inventing C1 labels."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from recommendation_baseline_calibration import regression_metrics, sha256_file


K_VALUES = (1, 3, 5, 10, 20)
SOURCE_MIN = 0.5
SOURCE_MAX = 5.0
PRODUCT_MIN = 1.0
PRODUCT_MAX = 5.0
PROTOCOL_VERSION = "rating-scale-alignment-v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    return parser.parse_args()


def identity(values: np.ndarray) -> np.ndarray:
    return np.asarray(values, dtype=np.float64).copy()


def clamp(values: np.ndarray) -> np.ndarray:
    return np.clip(np.asarray(values, dtype=np.float64), PRODUCT_MIN, PRODUCT_MAX)


def round_half_up(values: np.ndarray) -> np.ndarray:
    rounded = np.floor(np.asarray(values, dtype=np.float64) + 0.5)
    return np.clip(rounded, PRODUCT_MIN, PRODUCT_MAX)


def affine(values: np.ndarray) -> np.ndarray:
    source = np.asarray(values, dtype=np.float64)
    return PRODUCT_MIN + (source - SOURCE_MIN) * (
        (PRODUCT_MAX - PRODUCT_MIN) / (SOURCE_MAX - SOURCE_MIN)
    )


def macro_mae(user_ids: np.ndarray, actual: np.ndarray, predicted: np.ndarray) -> float:
    frame = pd.DataFrame(
        {"user_id": user_ids, "absolute_error": np.abs(predicted - actual)}
    )
    return float(frame.groupby("user_id", sort=False)["absolute_error"].mean().mean())


def option_metrics(
    *,
    user_ids: np.ndarray,
    actual: np.ndarray,
    predicted: np.ndarray,
    transform: Callable[[np.ndarray], np.ndarray],
) -> dict[str, Any]:
    transformed_prediction = transform(predicted)
    transformed_actual = transform(actual)
    native = regression_metrics(actual, transformed_prediction)
    paired = regression_metrics(transformed_actual, transformed_prediction)
    native["macro_mae"] = round(
        macro_mae(user_ids, actual, transformed_prediction), 6
    )
    paired["macro_mae"] = round(
        macro_mae(user_ids, transformed_actual, transformed_prediction), 6
    )
    return {
        "prediction_min": round(float(transformed_prediction.min()), 6),
        "prediction_max": round(float(transformed_prediction.max()), 6),
        "prediction_outside_product_scale_fraction": round(
            float(
                np.mean(
                    (transformed_prediction < PRODUCT_MIN)
                    | (transformed_prediction > PRODUCT_MAX)
                )
            ),
            6,
        ),
        "against_original_movielens_labels": native,
        "diagnostic_with_labels_transformed_the_same_way": paired,
    }


def analyze(source_manifest: dict[str, Any], predictions: pd.DataFrame) -> dict[str, Any]:
    boundary = int(source_manifest["protocol"]["star_selection_boundary"])
    evaluation = predictions.loc[predictions["timestamp"] >= boundary].copy()
    if len(evaluation) == 0:
        raise ValueError("held-out evaluation rows are empty")
    required = {"user_id", "rating", "timestamp"} | {
        f"prediction_k{k}" for k in K_VALUES
    }
    missing = sorted(required - set(evaluation.columns))
    if missing:
        raise ValueError(f"prediction artifact is missing columns: {missing}")
    if not np.isfinite(
        evaluation[["rating", *[f"prediction_k{k}" for k in K_VALUES]]].to_numpy()
    ).all():
        raise ValueError("held-out ratings and predictions must be finite")

    actual = evaluation["rating"].to_numpy(dtype=np.float64)
    users = evaluation["user_id"].to_numpy(dtype=np.int64)
    transforms: dict[str, Callable[[np.ndarray], np.ndarray]] = {
        "AS_IS_0_5_TO_5": identity,
        "CLAMP_1_TO_5": clamp,
        "ROUND_HALF_UP_TO_INTEGER_1_TO_5": round_half_up,
        "AFFINE_0_5_TO_5_INTO_1_TO_5": affine,
    }
    options: dict[str, Any] = {
        name: {
            "technical_properties": properties,
            "by_k": {},
        }
        for name, properties in {
            "AS_IS_0_5_TO_5": {
                "range_preserving_for_product": False,
                "monotonic": True,
                "strictly_monotonic": True,
                "invertible": True,
                "product_semantics": "REJECT_SOURCE_SCALE_CAN_OUTPUT_BELOW_C1_MINIMUM",
                "decision": "REJECT_FOR_PRODUCT_STAR_OUTPUT",
            },
            "CLAMP_1_TO_5": {
                "range_preserving_for_product": True,
                "monotonic": True,
                "strictly_monotonic": False,
                "invertible": False,
                "product_semantics": "REJECT_COLLAPSES_SOURCE_VALUES_AND_ALTERS_0_5_LABEL_MEANING",
                "decision": "REJECT",
            },
            "ROUND_HALF_UP_TO_INTEGER_1_TO_5": {
                "range_preserving_for_product": True,
                "monotonic": True,
                "strictly_monotonic": False,
                "invertible": False,
                "product_semantics": "REJECT_TURNS_A_CALIBRATED_EXPECTATION_INTO_A_DISCRETE_ENTERED_RATING",
                "decision": "REJECT",
            },
            "AFFINE_0_5_TO_5_INTO_1_TO_5": {
                "range_preserving_for_product": True,
                "monotonic": True,
                "strictly_monotonic": True,
                "invertible": True,
                "product_semantics": "UNPROVEN_SHIFTS_INTERIOR_STAR_ANCHORS_WITHOUT_C1_LABELS",
                "decision": "DO_NOT_ADOPT_WITHOUT_C1_PAIRED_VALIDATION",
            },
        }.items()
    }
    for name, transform in transforms.items():
        for k in K_VALUES:
            predicted = evaluation[f"prediction_k{k}"].to_numpy(dtype=np.float64)
            options[name]["by_k"][str(k)] = option_metrics(
                user_ids=users,
                actual=actual,
                predicted=predicted,
                transform=transform,
            )

    actual_counts = evaluation["rating"].value_counts().sort_index()
    return {
        "schema_version": 1,
        "evidence_id": "REC-EV-003C",
        "protocol": {
            "version": PROTOCOL_VERSION,
            "source_prediction_scale": {"minimum": SOURCE_MIN, "maximum": SOURCE_MAX},
            "product_rating_scale": {
                "minimum": PRODUCT_MIN,
                "maximum": PRODUCT_MAX,
                "allowed_values": [1, 2, 3, 4, 5],
            },
            "same_held_out_rows_for_all_options": True,
            "held_out_timestamp_boundary_inclusive": boundary,
            "selection_criteria_locked_before_comparison": [
                "OUTPUT_RANGE_WITHIN_PRODUCT_SCALE",
                "MONOTONIC",
                "NO_UNVERSIONED_LOSSY_CLAMP_OR_ROUND",
                "HELD_OUT_C1_INTEGER_LABELS_REQUIRED_FOR_MAE_AND_CALIBRATION",
                "RAW_MODEL_SCALE_VALUE_RETAINED_FOR_AUDIT",
                "PRODUCT_STAR_SEMANTICS_MUST_NOT_CHANGE_BY_NUMERIC_RESCALING_ALONE",
            ],
        },
        "source_data": {
            "evaluation_rows": int(len(evaluation)),
            "evaluation_users": int(evaluation["user_id"].nunique()),
            "rating_counts": {
                str(float(value)): int(count) for value, count in actual_counts.items()
            },
            "non_integer_actual_fraction": round(
                float(np.mean(actual != np.floor(actual))), 6
            ),
            "actual_below_product_minimum_fraction": round(
                float(np.mean(actual < PRODUCT_MIN)), 6
            ),
            "has_paired_c1_integer_labels": False,
        },
        "options": options,
        "recalibration": {
            "decision": "NOT_EVALUABLE",
            "reason": "NO_HELD_OUT_C1_INTEGER_RATING_PAIRED_WITH_PRE_RATING_PREDICTION",
            "required_artifact_schema": "c1-product-star-alignment-pairs-v1",
            "required_exporter": {
                "cli": "feelm-recommender export-product-scale-validation",
                "allowlisted_input_fields": [
                    "prediction_id",
                    "predicted_at",
                    "rated_at",
                    "model_scale_prediction",
                    "actual_c1_rating",
                    "k",
                    "model_version",
                    "artifact_set_version",
                    "policy_version",
                    "split",
                ],
                "forbidden_fields": [
                    "user_id",
                    "movie_id",
                    "movielens_user_id",
                    "movielens_item_id",
                    "token",
                ],
                "split_policy": "CALIBRATION_RATINGS_PRECEDE_VALIDATION_PREDICTIONS_V1",
                "actual_rating_contract": "C1_INTEGER_1_TO_5",
                "prediction_contract": "MODEL_SCALE_0_5_TO_5_BEFORE_PRODUCT_ADAPTER",
            },
        },
        "decision": {
            "selected": "STAR_DISABLED_FAIL_CLOSED",
            "dn_c2_008_status": "BLOCKED_PENDING_C1_PAIRED_VALIDATION",
            "ranking_impact": "NONE_POPULARITY_REMAINS_AVAILABLE",
            "expected_star_champion": False,
            "expected_star_ui_approved": False,
            "reason": (
                "MovieLens held-out labels use 0.5 increments and cannot establish the "
                "semantics or calibration of C1 integer 1..5 outcomes. Clamp and round are "
                "lossy; affine is technically invertible but only rescales units."
            ),
        },
    }


def markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    header = "| " + " | ".join(headers) + " |"
    divider = "| " + " | ".join("---" for _ in headers) + " |"
    body = ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join([header, divider, *body])


def build_report(manifest: dict[str, Any]) -> str:
    options = manifest["options"]
    rows: list[list[str]] = []
    for name, option in options.items():
        k10 = option["by_k"]["10"]
        native = k10["against_original_movielens_labels"]
        diagnostic = k10["diagnostic_with_labels_transformed_the_same_way"]
        properties = option["technical_properties"]
        rows.append(
            [
                name,
                f"{k10['prediction_min']:.3f}..{k10['prediction_max']:.3f}",
                f"{native['macro_mae']:.4f}",
                f"{native['ece_decile']:.4f}",
                f"{diagnostic['macro_mae']:.4f}",
                "YES" if properties["invertible"] else "NO",
                properties["decision"],
            ]
        )
    table = markdown_table(
        [
            "Option",
            "K10 output range",
            "Macro MAE vs ML labels",
            "ECE vs ML labels",
            "Diagnostic macro MAE after same label transform",
            "Invertible",
            "Decision",
        ],
        rows,
    )
    source = manifest["source_data"]
    source_sha = manifest["source"]["predictions_sha256"]
    return f"""# REC-EV-003C — MovieLens→C1 rating scale alignment

> 상태: `COMPLETED_FAIL_CLOSED`  
> Test 사용: `NO`  
> DN-C2-008: `BLOCKED_PENDING_C1_PAIRED_VALIDATION`

## 1. 결론

현재 근거로 product-scale adapter를 채택하지 않는다. expected-star는 disabled를 유지하고
Popularity ranking은 그대로 제공한다. REC-EV-003B candidate는 champion이 아니며 숫자 UI도 승인되지 않았다.

MovieLens held-out 실제값은 0.5 간격 `0.5..5.0`이고 C1 입력·결과 계약은 integer `1..5`다.
동일한 held-out {source['evaluation_rows']:,}행/{source['evaluation_users']:,}명에서 네 옵션을 비교했지만,
C1 actual Rating과 prediction-before-rating이 paired된 행은 없다. 따라서 MovieLens 단위 변환만으로
C1 예상 별점의 MAE·calibration 또는 제품 의미를 검증할 수 없다.

## 2. 사전 선택 기준

1. product output이 `1..5` 범위를 보존한다.
2. 변환은 단조이며, 손실 변환이면 raw model-scale 값을 별도 snapshot한다.
3. clamp/round 같은 unversioned lossy 변환은 채택하지 않는다.
4. held-out C1 integer Rating에서 MAE와 calibration을 같은 row로 평가한다.
5. 숫자 rescale만으로 사용자가 입력한 1~5 Rating 의미가 같다고 주장하지 않는다.
6. adapter version과 source/target scale, fit/eval split, checksum을 artifact로 고정한다.

## 3. 동일 validation 행 비교

{table}

- 표는 REC-EV-003B의 첫 실질 후보인 K10을 요약한다. machine manifest는 K1/K3/K5/K10/K20을
  모두 포함하며, as-is K1과 K3의 held-out prediction minimum은 실제로 0.5였다.
- `vs ML labels`는 prediction만 바꿔 원래 MovieLens label과 비교한 진단이다.
- `same label transform`은 prediction과 label에 같은 변환을 적용한 수학적 진단일 뿐 C1 검증이 아니다.
- affine의 낮아진 오차는 단위 폭이 `4.5→4.0`으로 줄어든 결과이며 모델 개선이 아니다.
- clamp는 `[0.5,1.0]`을 한 값으로 합치고 round는 calibration resolution을 없애므로 금지 후보다.
- affine은 range·단조성·invertibility를 만족하지만 내부 별점 anchor를 이동시키며 C1 label 근거가 없다.

## 4. 데이터 격차

- 평가 행의 non-integer MovieLens actual 비율: {source['non_integer_actual_fraction']:.2%}
- product minimum 1 미만 actual 비율: {source['actual_below_product_minimum_fraction']:.2%}
- C1 paired integer labels: `NO`
- source prediction SHA-256: `{source_sha}`

필요 artifact는 `c1-product-star-alignment-pairs-v1`이다. 실제 C1에서 rating 전에 저장된 model-scale
prediction과 이후 integer Rating을 leakage 없이 연결하되 userId/movieId/token을 export하지 않는다.
`CALIBRATION`과 이후 시간의 `VALIDATION` split을 모두 포함하고 adapter fit에는 CALIBRATION만 사용한다.

Exporter 입력은 정확히 `prediction_id`, `predicted_at`, `rated_at`, `model_scale_prediction`,
`actual_c1_rating`, `k`, `model_version`, `artifact_set_version`, `policy_version`, `split`만 허용한다.
`user_id`, `movie_id`, MovieLens ID, token은 거부한다. Rating은 prediction 뒤에 발생해야 하고 모든
CALIBRATION Rating 시각은 첫 VALIDATION prediction보다 앞서야 한다. 출력 payload와 sidecar는
canonical JSON과 SHA-256을 사용하며 생성시각을 포함하지 않는다.

```powershell
py -3 -m feelm_recommender export-product-scale-validation `
  --source outputs\\c2\\joined-product-scale-source.json `
  --payload outputs\\c2\\c1-product-scale-pairs.json `
  --metadata outputs\\c2\\c1-product-scale-pairs.metadata.json `
  --dataset-version c1-product-scale-v1
```

## 5. 옵션 판정

| Option | 판정 | 이유 |
| --- | --- | --- |
| 그대로 사용 | Reject for product output | 1 미만 값을 만들 수 있고 C1 척도와 다름 |
| clamp | Reject | 비가역·비엄격 단조, 낮은 값 의미 병합 |
| round | Reject | 비가역·이산화, 예상값과 사용자가 입력한 Rating을 혼동 |
| versioned affine | Hold | invertible하지만 C1 calibration·anchor 의미 근거 없음 |
| C1-label recalibration | Not evaluable | paired held-out C1 label artifact 없음 |
| fail closed | Selected | 근거 없는 UI 숫자와 의미 변환을 만들지 않음 |

## 6. 재현

```powershell
py -3 scripts/recommendation_rating_scale_alignment.py `
  --source-manifest docs/recommendation/evidence/manifests/rec-ev-003b.json `
  --predictions outputs/recommendation-evidence/rec-ev-003b/selected_star_predictions.parquet `
  --manifest docs/recommendation/evidence/manifests/rec-ev-003c.json `
  --evidence docs/recommendation/evidence/REC-EV-003C-rating-scale-alignment.md
```
"""


def main() -> int:
    args = parse_args()
    source_manifest = json.loads(args.source_manifest.read_text(encoding="utf-8"))
    expected = source_manifest["artifacts"]["selected_star_predictions"]["sha256"]
    actual_sha = sha256_file(args.predictions)
    if actual_sha != expected:
        raise RuntimeError(
            f"prediction checksum mismatch: expected {expected}, got {actual_sha}"
        )
    predictions = pd.read_parquet(args.predictions)
    manifest = analyze(source_manifest, predictions)
    manifest["source"] = {
        "evidence_id": source_manifest["evidence_id"],
        "manifest_path": args.source_manifest.as_posix(),
        "manifest_sha256": sha256_file(args.source_manifest),
        "predictions_path": args.predictions.as_posix(),
        "predictions_sha256": actual_sha,
        "test_used": False,
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(build_report(manifest), encoding="utf-8")
    print(json.dumps({"status": "PASS", "decision": manifest["decision"]["selected"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
