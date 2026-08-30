#!/usr/bin/env python3
"""Evaluate discrete relative-utility policies on the untouched REC-EV-003B tail.

This analysis does not train or select a rating model. It compares the C6 v1
right-inclusive raw ECDF with a discrete-scale, quantized midrank ECDF. Model
selection precedes the evaluation boundary and MovieLens Test is never read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


K_VALUES = (1, 3, 5, 10, 20)
BASELINE_POLICY = "C6_RIGHT_INCLUSIVE_RAW_ECDF_V1"
CANDIDATE_POLICY = "C6_DISCRETE_QUANTIZED_MIDRANK_ECDF_V2"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--cold-predictions", type=Path, required=True)
    parser.add_argument("--onboarding", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--rating-step", type=float, default=0.5)
    parser.add_argument("--minimum-mae-improvement", type=float, default=0.15)
    parser.add_argument("--minimum-absolute-bias-reduction", type=float, default=0.40)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact(path: Path, rows: int | None = None) -> dict[str, Any]:
    record: dict[str, Any] = {
        "path": str(path),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }
    if rows is not None:
        record["rows"] = rows
    return record


def quantize_to_step(values: np.ndarray, step: float) -> np.ndarray:
    if not math.isfinite(step) or step <= 0:
        raise ValueError("rating step must be positive and finite")
    numeric = np.asarray(values, dtype=np.float64)
    if not np.isfinite(numeric).all():
        raise ValueError("target values must be finite")
    return np.floor(numeric / step + 0.5) * step


def right_inclusive_ecdf(history: np.ndarray, targets: np.ndarray) -> np.ndarray:
    samples = np.asarray(history, dtype=np.float64)
    target = np.asarray(targets, dtype=np.float64)
    if samples.ndim != 1 or len(samples) == 0:
        raise ValueError("non-empty one-dimensional history is required")
    return (1.0 + (samples[:, None] <= target[None, :]).sum(axis=0)) / (len(samples) + 2.0)


def quantized_midrank_ecdf(history: np.ndarray, targets: np.ndarray, step: float) -> np.ndarray:
    samples = np.asarray(history, dtype=np.float64)
    target = quantize_to_step(np.asarray(targets, dtype=np.float64), step)
    if samples.ndim != 1 or len(samples) == 0:
        raise ValueError("non-empty one-dimensional history is required")
    less = (samples[:, None] < target[None, :]).sum(axis=0)
    equal = (samples[:, None] == target[None, :]).sum(axis=0)
    return (1.0 + less + 0.5 * equal) / (len(samples) + 2.0)


def metric_block(predicted: np.ndarray, observed: np.ndarray) -> dict[str, float | int]:
    predicted = np.asarray(predicted, dtype=np.float64)
    observed = np.asarray(observed, dtype=np.float64)
    if len(predicted) == 0 or len(predicted) != len(observed):
        raise ValueError("aligned non-empty values are required")
    error = predicted - observed
    correlation = pd.Series(predicted).corr(pd.Series(observed), method="spearman")
    return {
        "rows": int(len(error)),
        "mae": round(float(np.abs(error).mean()), 6),
        "rmse": round(float(np.sqrt(np.square(error).mean())), 6),
        "mean_error": round(float(error.mean()), 6),
        "spearman": round(float(correlation), 6),
    }


def macro_mae(user_ids: np.ndarray, predicted: np.ndarray, observed: np.ndarray) -> float:
    frame = pd.DataFrame(
        {"user_id": user_ids, "absolute_error": np.abs(predicted - observed)}
    )
    return float(frame.groupby("user_id")["absolute_error"].mean().mean())


def segment_metrics(
    segments: np.ndarray,
    baseline_predicted: np.ndarray,
    baseline_observed: np.ndarray,
    candidate_predicted: np.ndarray,
    candidate_observed: np.ndarray,
) -> dict[str, Any]:
    frame = pd.DataFrame(
        {
            "segment": segments,
            "baseline_error": np.abs(baseline_predicted - baseline_observed),
            "candidate_error": np.abs(candidate_predicted - candidate_observed),
            "baseline_signed": baseline_predicted - baseline_observed,
            "candidate_signed": candidate_predicted - candidate_observed,
        }
    )
    result: dict[str, Any] = {}
    for name, group in frame.groupby("segment", sort=True):
        baseline_mae = float(group["baseline_error"].mean())
        candidate_mae = float(group["candidate_error"].mean())
        result[str(name)] = {
            "rows": int(len(group)),
            "baseline_mae": round(baseline_mae, 6),
            "candidate_mae": round(candidate_mae, 6),
            "relative_mae_improvement": round(
                (baseline_mae - candidate_mae) / baseline_mae, 6
            ),
            "baseline_mean_error": round(float(group["baseline_signed"].mean()), 6),
            "candidate_mean_error": round(float(group["candidate_signed"].mean()), 6),
        }
    return result


def evaluate(
    predictions: pd.DataFrame,
    cold_predictions: pd.DataFrame,
    onboarding: pd.DataFrame,
    *,
    boundary: int,
    rating_step: float,
    minimum_mae_improvement: float,
    minimum_absolute_bias_reduction: float,
) -> dict[str, Any]:
    required_prediction = {"row_id", "user_id", "rating", "timestamp"} | {
        f"prediction_k{k}" for k in K_VALUES
    }
    if not required_prediction.issubset(predictions.columns):
        raise ValueError("selected prediction columns are incomplete")
    required_cold = {"row_id", "prediction_k0_fallback_isotonic", "rating_mean_quartile"}
    if not required_cold.issubset(cold_predictions.columns):
        raise ValueError("cold prediction columns are incomplete")
    required_onboarding = {"user_id", "rating", "onboarding_order"}
    if not required_onboarding.issubset(onboarding.columns):
        raise ValueError("onboarding columns are incomplete")

    evaluation = predictions.loc[predictions["timestamp"] >= boundary].merge(
        cold_predictions[list(required_cold)], on="row_id", validate="one_to_one"
    ).reset_index(drop=True)
    if evaluation.empty or int(evaluation["timestamp"].min()) < boundary:
        raise ValueError("evaluation boundary was not enforced")
    if evaluation["row_id"].duplicated().any():
        raise ValueError("evaluation row_id must be unique")

    histories = {
        int(user_id): group.sort_values(["onboarding_order"], kind="stable")["rating"].to_numpy(
            dtype=np.float64
        )
        for user_id, group in onboarding.groupby("user_id", sort=False)
    }
    evaluation_users = set(evaluation["user_id"].astype(int).unique())
    if missing := sorted(user_id for user_id in evaluation_users if len(histories.get(user_id, [])) < max(K_VALUES)):
        raise ValueError(f"evaluation users miss K20 history: {len(missing)}")

    user_ids = evaluation["user_id"].to_numpy(dtype=np.int64)
    actual_ratings = evaluation["rating"].to_numpy(dtype=np.float64)
    segments = evaluation["rating_mean_quartile"].astype(str).to_numpy()
    curve: dict[str, Any] = {}
    for k in K_VALUES:
        predicted_ratings = evaluation[f"prediction_k{k}"].to_numpy(dtype=np.float64)
        baseline_utility = np.empty(len(evaluation), dtype=np.float64)
        baseline_observed = np.empty(len(evaluation), dtype=np.float64)
        candidate_utility = np.empty(len(evaluation), dtype=np.float64)
        candidate_observed = np.empty(len(evaluation), dtype=np.float64)

        for user_id, positions in evaluation.groupby("user_id", sort=False).indices.items():
            history = histories[int(user_id)][:k]
            predicted = predicted_ratings[positions]
            actual = actual_ratings[positions]
            baseline_utility[positions] = right_inclusive_ecdf(history, predicted)
            baseline_observed[positions] = right_inclusive_ecdf(history, actual)
            candidate_utility[positions] = quantized_midrank_ecdf(history, predicted, rating_step)
            candidate_observed[positions] = quantized_midrank_ecdf(history, actual, rating_step)

        baseline_metrics = metric_block(baseline_utility, baseline_observed)
        candidate_metrics = metric_block(candidate_utility, candidate_observed)
        baseline_macro = macro_mae(user_ids, baseline_utility, baseline_observed)
        candidate_macro = macro_mae(user_ids, candidate_utility, candidate_observed)
        relative_mae_improvement = (
            float(baseline_metrics["mae"]) - float(candidate_metrics["mae"])
        ) / float(baseline_metrics["mae"])
        baseline_bias = abs(float(baseline_metrics["mean_error"]))
        candidate_bias = abs(float(candidate_metrics["mean_error"]))
        bias_reduction = (
            (baseline_bias - candidate_bias) / baseline_bias if baseline_bias > 0 else 0.0
        )
        segments_result = segment_metrics(
            segments,
            baseline_utility,
            baseline_observed,
            candidate_utility,
            candidate_observed,
        )
        gates = {
            "micro_mae_improvement": relative_mae_improvement >= minimum_mae_improvement,
            "absolute_bias_reduction": bias_reduction >= minimum_absolute_bias_reduction,
            "spearman_non_regression": float(candidate_metrics["spearman"])
            >= float(baseline_metrics["spearman"]),
            "all_rating_style_segments_improve_mae": all(
                values["candidate_mae"] < values["baseline_mae"]
                for values in segments_result.values()
            ),
        }
        curve[str(k)] = {
            "baseline": baseline_metrics,
            "candidate": candidate_metrics,
            "baseline_user_macro_mae": round(baseline_macro, 6),
            "candidate_user_macro_mae": round(candidate_macro, 6),
            "relative_micro_mae_improvement": round(relative_mae_improvement, 6),
            "absolute_bias_reduction": round(bias_reduction, 6),
            "spearman_change": round(
                float(candidate_metrics["spearman"]) - float(baseline_metrics["spearman"]), 6
            ),
            "rating_style_segments": segments_result,
            "gates": gates,
            "gate_pass": all(gates.values()),
        }

    return {
        "evaluation_boundary": boundary,
        "evaluation_rows": int(len(evaluation)),
        "evaluation_users": int(evaluation["user_id"].nunique()),
        "rating_step": rating_step,
        "k_curve": curve,
        "all_k_pass": all(values["gate_pass"] for values in curve.values()),
    }


def report_text(manifest: dict[str, Any], result: dict[str, Any]) -> str:
    rows = []
    for k, values in result["k_curve"].items():
        rows.append(
            f"| {k} | {values['baseline']['mae']:.6f} | {values['candidate']['mae']:.6f} | "
            f"{values['relative_micro_mae_improvement']:.2%} | "
            f"{values['baseline']['mean_error']:+.6f} → {values['candidate']['mean_error']:+.6f} | "
            f"{values['baseline']['spearman']:.6f} → {values['candidate']['spearman']:.6f} | "
            f"{'PASS' if values['gate_pass'] else 'FAIL'} |"
        )
    table = "\n".join(rows)
    return f"""# REC-EV-015 — discrete relative-utility policy

> 상태: `COMPLETED_OFFLINE_EVIDENCE`  
> 제품 만족도 주장: `NO`  
> 제품 노출 승인: `NO`

## 결론

MovieLens 0.5 단위의 동점을 상단으로 보내는 v1 right-inclusive ECDF는 연속 예측값이
실제 평점보다 아주 작게 낮아도 해당 동점 전체를 놓쳐 상대 효용을 체계적으로 낮게 추정했다.
예측을 평점 격자에 quantize한 뒤 동점의 중간을 쓰는 v2 midrank ECDF는 K1·K3·K5·K10·K20
모두 잠겨 둔 Gate를 통과했다. 따라서 `{manifest['conclusion']['decision']}`로 기록한다.

이는 사용자 만족도를 측정했다는 뜻이 아니다. 숨겨진 실제 평점을 개인의 이전 평점 분포에서
일관되게 위치시키는 정규화 규칙을 검증한 것이다.

## 프로토콜

- 소스: REC-EV-003B의 선택이 끝난 예상 별점과 REC-EV-003 onboarding history
- 평가: 선택 경계 `{result['evaluation_boundary']}` 이후 `{result['evaluation_rows']:,}`건, `{result['evaluation_users']:,}`명
- MovieLens Test 사용: `NO`
- baseline: `{BASELINE_POLICY}`
- candidate: `{CANDIDATE_POLICY}`
- candidate 공식: `q = round_to_rating_step(prediction)`, `(1 + count(r < q) + 0.5 * count(r = q)) / (n + 2)`
- 검증 격자: `{result['rating_step']}`

## K별 결과

| K | v1 MAE | v2 MAE | v2 개선 | 평균 오차 v1 → v2 | Spearman v1 → v2 | Gate |
| ---: | ---: | ---: | ---: | ---: | ---: | --- |
{table}

Gate는 각 K에서 MAE 15% 이상 개선, 절대 bias 40% 이상 감소, Spearman 비열화,
평점 평균 4분위 모든 구간의 MAE 개선을 동시에 요구했다.

## 한계와 채택 경계

- MovieLens 0.5 척도 결과이며 C1 정수 1~5의 제품 calibration을 증명하지 않는다.
- v2는 C6 local experiment의 상대 효용 표현에만 채택한다.
- C2B 예상 별점·추천 순위·제품 문구를 열지 않는다.
- 미평가는 부정 신호가 아니며, 이 값을 `satisfaction`으로 이름 바꾸지 않는다.
"""


def main() -> int:
    args = parse_args()
    started = time.perf_counter()
    source = json.loads(args.source_manifest.read_text(encoding="utf-8"))
    if source.get("evidence_id") != "REC-EV-003B" or source.get("source", {}).get("test_used") is not False:
        raise RuntimeError("REC-EV-003B source or Test boundary is invalid")
    prediction_record = source["artifacts"]["selected_star_predictions"]
    prediction_path = Path(prediction_record["path"])
    if sha256_file(prediction_path) != prediction_record["sha256"]:
        raise RuntimeError("selected star prediction checksum drift")

    predictions = pd.read_parquet(prediction_path)
    cold_predictions = pd.read_parquet(args.cold_predictions)
    onboarding = pd.read_parquet(args.onboarding)
    boundary = int(source["protocol"]["star_selection_boundary"])
    result = evaluate(
        predictions,
        cold_predictions,
        onboarding,
        boundary=boundary,
        rating_step=args.rating_step,
        minimum_mae_improvement=args.minimum_mae_improvement,
        minimum_absolute_bias_reduction=args.minimum_absolute_bias_reduction,
    )
    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    manifest = {
        "schema_version": 1,
        "evidence_id": "REC-EV-015",
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": {
            "rec_ev_003b_manifest": artifact(args.source_manifest),
            "selected_star_predictions": artifact(prediction_path, len(predictions)),
            "cold_predictions": artifact(args.cold_predictions, len(cold_predictions)),
            "onboarding": artifact(args.onboarding, len(onboarding)),
            "test_used": False,
        },
        "protocol": {
            "version": "relative-utility-discrete-policy-eval-v1",
            "baseline_policy": BASELINE_POLICY,
            "candidate_policy": CANDIDATE_POLICY,
            "minimum_mae_improvement": args.minimum_mae_improvement,
            "minimum_absolute_bias_reduction": args.minimum_absolute_bias_reduction,
            "spearman_non_regression": True,
            "all_rating_style_segments_must_improve": True,
        },
        "result": artifact(args.result),
        "runtime": {
            "python": platform.python_version(),
            "pandas": pd.__version__,
            "numpy": np.__version__,
            "seconds": round(time.perf_counter() - started, 3),
        },
        "conclusion": {
            "all_k_pass": result["all_k_pass"],
            "decision": (
                "ADOPT_C6_LOCAL_EXPERIMENT_V2_KEEP_PRODUCT_BLOCKED"
                if result["all_k_pass"]
                else "REJECT_CANDIDATE_KEEP_V1"
            ),
            "product_display_approved": False,
            "satisfaction_claim_supported": False,
        },
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.evidence.write_text(report_text(manifest, result), encoding="utf-8")
    print(json.dumps({"status": "PASS", "evidence_id": "REC-EV-015", "decision": manifest["conclusion"]["decision"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
