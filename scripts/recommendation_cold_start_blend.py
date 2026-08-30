#!/usr/bin/env python3
"""REC-EV-003B: tune separate cold-start blends for stars and ranking."""

from __future__ import annotations

import argparse
import json
import math
import platform
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import sklearn

from recommendation_baseline_calibration import (
    apply_isotonic,
    artifact_record,
    choose_later_boundary,
    fit_isotonic,
    regression_metrics,
    sha256_file,
)
from recommendation_cold_start_curve import (
    bootstrap_mean_difference,
    markdown_table,
    metric_or_na,
    user_macro_mae,
)


K_VALUES = (1, 3, 5, 10, 20)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cold-start-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--alpha-step", type=float, default=0.1)
    parser.add_argument("--ranking-noninferiority", type=float, default=0.01)
    parser.add_argument("--star-min-relative-improvement", type=float, default=0.03)
    parser.add_argument("--bootstrap-repeats", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def alpha_grid(step: float) -> np.ndarray:
    if not 0 < step <= 1:
        raise ValueError("alpha step must be in (0, 1]")
    values = np.arange(0.0, 1.0 + step / 2, step)
    values[-1] = 1.0
    return np.round(values, 10)


def stable_user_selection(user_ids: np.ndarray, seed: int) -> np.ndarray:
    values = user_ids.astype(np.uint64)
    mixed = values * np.uint64(11400714819323198485) + np.uint64(seed)
    return (mixed & np.uint64(1)) == 0


def macro_mae(frame: pd.DataFrame, actual: np.ndarray, predicted: np.ndarray) -> float:
    working = pd.DataFrame(
        {
            "user_id": frame["user_id"].to_numpy(),
            "absolute_error": np.abs(predicted - actual),
        }
    )
    return float(working.groupby("user_id")["absolute_error"].mean().mean())


def ranking_per_user(frame: pd.DataFrame, score: np.ndarray) -> pd.DataFrame:
    ranked = frame[["user_id", "is_positive"]].copy()
    ranked["score"] = score
    ranked = ranked.sort_values(
        ["user_id", "score", "is_positive"],
        ascending=[True, False, False],
        kind="stable",
    )
    ranked["rank"] = ranked.groupby("user_id").cumcount() + 1
    positive = ranked.loc[ranked["is_positive"] == 1, ["user_id", "rank"]].copy()
    ranks = positive["rank"].to_numpy(dtype=np.float64)
    positive["ndcg_at_10"] = np.where(
        ranks <= 10, 1.0 / np.log2(ranks + 1), 0.0
    )
    positive["hit_at_10"] = (ranks <= 10).astype(np.float64)
    return positive.set_index("user_id")


def bootstrap_difference(
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
    samples = np.empty(repeats, dtype=np.float64)
    for index in range(repeats):
        positions = rng.integers(0, len(differences), size=len(differences))
        samples[index] = float(differences[positions].mean())
    return {
        "users": int(len(differences)),
        "mean_difference": round(float(differences.mean()), 6),
        "ci95_low": round(float(np.quantile(samples, 0.025)), 6),
        "ci95_high": round(float(np.quantile(samples, 0.975)), 6),
    }


def build_report(manifest: dict[str, Any]) -> str:
    curve = manifest["metrics"]["selected_curve"]
    table = markdown_table(
        [
            "K",
            "Star α",
            "Macro MAE",
            "Relative gain",
            "Δ MAE vs K0 (95% CI)",
            "Rank α",
            "Eval NDCG@10",
            "Δ NDCG vs Popularity (95% CI)",
        ],
        [
            (
                k,
                f"{values['star_alpha']:.1f}",
                f"{values['star_macro_mae']:.4f}",
                f"{values['star_relative_improvement']:.2%}",
                f"{values['star_vs_k0']['mean_difference']:+.4f} "
                f"[{values['star_vs_k0']['ci95_low']:+.4f}, {values['star_vs_k0']['ci95_high']:+.4f}]",
                f"{values['rank_alpha']:.1f}",
                f"{values['ranking_eval']['ndcg_at_10']:.4f}",
                f"{values['rank_vs_popularity']['mean_difference']:+.4f} "
                f"[{values['rank_vs_popularity']['ci95_low']:+.4f}, {values['rank_vs_popularity']['ci95_high']:+.4f}]",
            )
            for k, values in curve.items()
        ],
    )
    selection_rows: list[tuple[Any, ...]] = []
    for k, values in manifest["metrics"]["alpha_selection"].items():
        selection_rows.append(
            (
                k,
                f"{values['star_alpha']:.1f}",
                f"{values['star_selection_macro_mae']:.4f}",
                f"{values['rank_alpha']:.1f}",
                f"{values['rank_selection_ndcg_at_10']:.4f}",
            )
        )
    selection_table = markdown_table(
        ["K", "Selected star α", "Selection Macro MAE", "Selected rank α", "Selection NDCG@10"],
        selection_rows,
    )
    conclusion = manifest["conclusion"]
    runtime = manifest["runtime"]
    return f"""# REC-EV-003B — Cold-start dual-head blend

> 상태: `COMPLETED`  
> 생성 시각: {manifest['run_at_utc']}  
> Test 사용: `NO`

## 1. 결론

{conclusion['summary']}

- 데이터 관점 최소 K: `{conclusion['minimum_supported_k']}`
- 최초 통계적 개선 K: `{conclusion['first_statistically_supported_k']}`
- 예상 별점 Gate: `{conclusion['star_gate']}`
- 추천 순위 Gate: `{conclusion['ranking_gate']}`
- 제품 온보딩 결정: `{conclusion['product_gate']}`

## 2. 선택과 최종 평가를 분리한 방식

예상 별점과 추천 순위에 같은 α를 강제하지 않았다.

- 별점: `(1-α) × K별 Bias + α × Fold-in`
- 순위: `(1-α) × Popularity + α × Fold-in`
- α 후보: {manifest['protocol']['alpha_grid']}
- 별점 α 선택: Validation 앞 절반을 다시 시간 분할해 앞 구간에서 Isotonic 학습, 뒤 구간에서
  사용자 macro MAE 최소화
- 별점 최종 평가: 기존 Validation 뒤 절반
- 순위 α 선택: sampled-ranking 사용자의 deterministic 절반
- 순위 최종 평가: α 선택에 쓰지 않은 나머지 사용자

따라서 아래 최종 수치는 α를 고른 행·사용자와 분리돼 있다.

## 3. 선택된 α

{selection_table}

## 4. 보지 않은 평가 구간 결과

{table}

별점 차이는 `blend MAE - K0 Bias MAE`라서 음수가 개선이다. 순위 차이는
`blend NDCG - K0 Popularity NDCG`라서 음수가 악화다. 같은 사용자 단위 1,000회 bootstrap 95%
신뢰구간을 보고 Gate를 정했다. 통계적 개선만으로 입력 부담을 정당화하지 않으며, Test를 열기
전에 K0 대비 상대 MAE 개선 {manifest['protocol']['star_min_relative_improvement']:.0%} 이상을
실질적 품질 Gate로 잠갔다.

## 5. 해석

별점 head에서 K 입력이 유효하더라도 순위 head의 최적 α가 0이면, 입력한 평가를 추천 순위에
강제로 쓰지 않는다. 이 경우 Fold-in은 예상 별점·설명 신호로만 저장하고 추천 순위는 Popularity와
향후 콘텐츠 Hybrid가 담당한다. “개인화 데이터를 받았으니 반드시 순위를 바꿔야 한다”는 요구는
성능 근거가 아니다.

## 6. 한계

- ranking은 여전히 sampled 후보이며 full-catalog 결과가 아니다.
- α grid는 0.1 간격이고 ALS 하이퍼파라미터는 한 조합이다.
- 사용자 절반 분할은 시간 분할이 아니라 사용자 일반화 검사다.
- 최소 K는 데이터 품질 조건이며 실제 화면 이탈·입력 시간은 포함하지 않는다.

## 7. 재현

- 전체 실행: {runtime['total_seconds']:.2f}s
- Python `{runtime['python']}`, scikit-learn `{runtime['scikit_learn']}`

```powershell
py -3 scripts/recommendation_cold_start_blend.py `
  --cold-start-manifest docs\\recommendation\\evidence\\manifests\\rec-ev-003.json `
  --output-dir outputs\\recommendation-evidence\\rec-ev-003b `
  --manifest docs\\recommendation\\evidence\\manifests\\rec-ev-003b.json `
  --evidence docs\\recommendation\\evidence\\REC-EV-003B-cold-start-blend.md
```
"""


def main() -> int:
    args = parse_args()
    started = time.perf_counter()
    cold_manifest = json.loads(
        args.cold_start_manifest.read_text(encoding="utf-8")
    )
    if cold_manifest["source"]["test_used"] is not False:
        raise RuntimeError("REC-EV-003 unexpectedly used Test")
    artifact_records = cold_manifest["artifacts"]
    prediction_path = Path(artifact_records["validation_predictions"]["path"])
    candidate_path = Path(artifact_records["sampled_ranking"]["path"])
    cohort_path = Path(artifact_records["cohort"]["path"])
    for path in (prediction_path, candidate_path, cohort_path):
        if not path.exists():
            raise FileNotFoundError(path)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    predictions = pd.read_parquet(prediction_path)
    candidates = pd.read_parquet(candidate_path)
    cohort = pd.read_parquet(cohort_path)
    final_boundary = cold_manifest["protocol"]["calibration_boundary"]
    early_timestamps = predictions.loc[
        predictions["timestamp"] < final_boundary, "timestamp"
    ].to_numpy(dtype=np.int64)
    selection_boundary = choose_later_boundary(early_timestamps, 0.5)
    calibration_fit = predictions["timestamp"].to_numpy() < selection_boundary
    star_selection = (
        (predictions["timestamp"].to_numpy() >= selection_boundary)
        & (predictions["timestamp"].to_numpy() < final_boundary)
    )
    star_evaluation = predictions["timestamp"].to_numpy() >= final_boundary
    if predictions.loc[calibration_fit, "timestamp"].max() >= predictions.loc[star_selection, "timestamp"].min():
        raise RuntimeError("star calibration and selection overlap")
    if predictions.loc[star_selection, "timestamp"].max() >= predictions.loc[star_evaluation, "timestamp"].min():
        raise RuntimeError("star selection and evaluation overlap")

    candidate_user_ids = candidates["user_id"].to_numpy(dtype=np.int64)
    selection_users = stable_user_selection(candidate_user_ids, args.seed)
    unique_user_assignment = pd.Series(
        selection_users, index=candidate_user_ids
    ).groupby(level=0).nunique()
    if int(unique_user_assignment.max()) != 1:
        raise RuntimeError("ranking user split is not stable within user")
    rank_selection = selection_users
    rank_evaluation = ~selection_users
    alphas = alpha_grid(args.alpha_step)

    baseline_star_raw = predictions["prediction_k0_fallback_raw"].to_numpy(
        dtype=np.float64
    )
    baseline_star_calibrator = fit_isotonic(
        baseline_star_raw[predictions["timestamp"].to_numpy() < final_boundary],
        predictions.loc[
            predictions["timestamp"] < final_boundary, "rating"
        ].to_numpy(dtype=np.float64),
    )
    baseline_star = apply_isotonic(baseline_star_calibrator, baseline_star_raw)
    baseline_eval_frame = predictions.loc[star_evaluation]
    _, baseline_star_user_errors = user_macro_mae(
        baseline_eval_frame.assign(prediction=baseline_star[star_evaluation]),
        "prediction",
    )
    baseline_star_macro = float(baseline_star_user_errors.mean())

    baseline_rank_score = candidates["score_k0"].to_numpy(dtype=np.float64)
    baseline_rank_per_user = ranking_per_user(candidates, baseline_rank_score)
    selection_metrics: dict[str, Any] = {}
    selected_curve: dict[str, Any] = {}
    star_output = predictions[["row_id", "user_id", "movie_id", "rating", "timestamp"]].copy()
    rank_output = candidates[["candidate_id", "user_id", "movie_id", "is_positive"]].copy()

    for k in K_VALUES:
        bias_raw = predictions[f"prediction_k{k}_bias_raw"].to_numpy(dtype=np.float64)
        fold_raw = predictions[f"prediction_k{k}_fallback_raw"].to_numpy(dtype=np.float64)
        selection_actual = predictions.loc[star_selection, "rating"].to_numpy(
            dtype=np.float64
        )
        star_candidates: list[tuple[float, float]] = []
        for alpha in alphas:
            raw = (1.0 - alpha) * bias_raw + alpha * fold_raw
            calibrator = fit_isotonic(
                raw[calibration_fit],
                predictions.loc[calibration_fit, "rating"].to_numpy(dtype=np.float64),
            )
            calibrated = apply_isotonic(calibrator, raw)
            selection_mae = macro_mae(
                predictions.loc[star_selection],
                selection_actual,
                calibrated[star_selection],
            )
            star_candidates.append((float(alpha), selection_mae))
        star_alpha, star_selection_mae = min(
            star_candidates, key=lambda value: (value[1], value[0])
        )

        fold_rank = candidates[f"score_k{k}"].to_numpy(dtype=np.float64)
        rank_candidates: list[tuple[float, float]] = []
        for alpha in alphas:
            score = (1.0 - alpha) * baseline_rank_score + alpha * fold_rank
            per_user = ranking_per_user(candidates.loc[rank_selection], score[rank_selection])
            rank_candidates.append((float(alpha), float(per_user["ndcg_at_10"].mean())))
        rank_alpha, rank_selection_ndcg = max(
            rank_candidates, key=lambda value: (value[1], -value[0])
        )

        final_raw = (1.0 - star_alpha) * bias_raw + star_alpha * fold_raw
        final_calibrator = fit_isotonic(
            final_raw[predictions["timestamp"].to_numpy() < final_boundary],
            predictions.loc[
                predictions["timestamp"] < final_boundary, "rating"
            ].to_numpy(dtype=np.float64),
        )
        final_star = apply_isotonic(final_calibrator, final_raw)
        star_output[f"prediction_k{k}"] = final_star
        eval_frame = predictions.loc[star_evaluation].copy()
        eval_frame["prediction"] = final_star[star_evaluation]
        star_macro, star_user_errors = user_macro_mae(eval_frame, "prediction")
        star_micro = regression_metrics(
            eval_frame["rating"].to_numpy(dtype=np.float64),
            eval_frame["prediction"].to_numpy(dtype=np.float64),
        )
        star_ci = bootstrap_mean_difference(
            star_user_errors,
            baseline_star_user_errors,
            args.bootstrap_repeats,
            args.seed + k,
        )

        final_rank_score = (
            (1.0 - rank_alpha) * baseline_rank_score + rank_alpha * fold_rank
        )
        rank_output[f"score_k{k}"] = final_rank_score
        candidate_eval = candidates.loc[rank_evaluation]
        rank_eval_per_user = ranking_per_user(
            candidate_eval, final_rank_score[rank_evaluation]
        )
        baseline_eval_per_user = baseline_rank_per_user.reindex(
            rank_eval_per_user.index
        )
        rank_ci = bootstrap_difference(
            rank_eval_per_user["ndcg_at_10"],
            baseline_eval_per_user["ndcg_at_10"],
            args.bootstrap_repeats,
            args.seed + 100 + k,
        )
        ranking_eval = {
            "users": int(len(rank_eval_per_user)),
            "hit_rate_at_10": round(float(rank_eval_per_user["hit_at_10"].mean()), 6),
            "ndcg_at_10": round(float(rank_eval_per_user["ndcg_at_10"].mean()), 6),
        }
        selection_metrics[str(k)] = {
            "star_alpha": star_alpha,
            "star_selection_macro_mae": round(star_selection_mae, 6),
            "rank_alpha": rank_alpha,
            "rank_selection_ndcg_at_10": round(rank_selection_ndcg, 6),
            "star_grid": [
                {"alpha": alpha, "macro_mae": round(value, 6)}
                for alpha, value in star_candidates
            ],
            "rank_grid": [
                {"alpha": alpha, "ndcg_at_10": round(value, 6)}
                for alpha, value in rank_candidates
            ],
        }
        selected_curve[str(k)] = {
            "star_alpha": star_alpha,
            "star_macro_mae": round(star_macro, 6),
            "star_relative_improvement": round(
                (baseline_star_macro - star_macro) / baseline_star_macro, 6
            ),
            "star_micro": star_micro,
            "star_vs_k0": star_ci,
            "rank_alpha": rank_alpha,
            "ranking_eval": ranking_eval,
            "rank_vs_popularity": rank_ci,
        }

    statistically_supported = [
        k
        for k in K_VALUES
        if selected_curve[str(k)]["star_vs_k0"]["ci95_high"] < 0
        and selected_curve[str(k)]["rank_vs_popularity"]["ci95_low"]
        >= -args.ranking_noninferiority
    ]
    supported = [
        k
        for k in statistically_supported
        if selected_curve[str(k)]["star_relative_improvement"]
        >= args.star_min_relative_improvement
    ]
    minimum_k = min(supported) if supported else None
    first_statistical_k = (
        min(statistically_supported) if statistically_supported else None
    )
    if minimum_k is None:
        minimum_label = "NONE"
        star_gate = "NO_K_SIGNIFICANTLY_IMPROVES_STAR_WITH_RANK_NONINFERIORITY"
    else:
        minimum_label = f"K{minimum_k}_DATA_ONLY"
        star_gate = f"K{minimum_k}_FIRST_PRACTICALLY_SUPPORTED_FOR_STAR_HEAD"
    rank_alphas = {
        k: selected_curve[str(k)]["rank_alpha"] for k in K_VALUES
    }
    if all(alpha == 0.0 for alpha in rank_alphas.values()):
        rank_gate = "KEEP_POPULARITY_ALPHA_0_FOR_ALL_K"
    else:
        rank_gate = "BLEND_WEIGHTS_SELECTED_BY_HELD_OUT_USERS"
    summary = (
        f"선택·평가를 분리한 dual-head 실험에서 데이터 조건을 처음 통과한 지점은 "
        f"{minimum_label}였다. 별점 head는 K별 Bias와 Fold-in을 혼합했지만, 순위 head는 "
        f"{rank_gate}였다. 따라서 K 입력은 예상 별점 신뢰도를 높이는 용도로만 우선 사용하고, "
        "추천 순위 개인화 효과는 콘텐츠 Hybrid나 full-catalog 검증 전까지 주장하지 않는다."
    )

    star_output_path = args.output_dir / "selected_star_predictions.parquet"
    star_output.to_parquet(star_output_path, index=False, compression="zstd")
    rank_output_path = args.output_dir / "selected_ranking_scores.parquet"
    rank_output.to_parquet(rank_output_path, index=False, compression="zstd")
    manifest = {
        "schema_version": 1,
        "evidence_id": "REC-EV-003B",
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": {
            "cold_start_manifest": str(args.cold_start_manifest),
            "cold_start_manifest_sha256": sha256_file(args.cold_start_manifest),
            "test_used": False,
        },
        "protocol": {
            "version": "cold-start-dual-head-blend-v1",
            "alpha_grid": alphas.tolist(),
            "star_calibration_boundary": selection_boundary,
            "star_selection_boundary": final_boundary,
            "ranking_user_split": "multiplicative-hash-parity-v1",
            "ranking_selection_users": int(candidates.loc[rank_selection, "user_id"].nunique()),
            "ranking_evaluation_users": int(candidates.loc[rank_evaluation, "user_id"].nunique()),
            "ranking_noninferiority": args.ranking_noninferiority,
            "star_min_relative_improvement": args.star_min_relative_improvement,
            "bootstrap_repeats": args.bootstrap_repeats,
            "seed": args.seed,
        },
        "metrics": {
            "alpha_selection": selection_metrics,
            "selected_curve": selected_curve,
        },
        "artifacts": {
            "selected_star_predictions": artifact_record(
                star_output_path, len(star_output)
            ),
            "selected_ranking_scores": artifact_record(
                rank_output_path, len(rank_output)
            ),
        },
        "validation": {
            "status": "PASS",
            "calibration_precedes_star_selection": True,
            "star_selection_precedes_final_evaluation": True,
            "ranking_selection_and_evaluation_users_disjoint": bool(
                set(candidates.loc[rank_selection, "user_id"]).isdisjoint(
                    set(candidates.loc[rank_evaluation, "user_id"])
                )
            ),
            "all_selected_predictions_finite": bool(
                all(np.isfinite(star_output[f"prediction_k{k}"]).all() for k in K_VALUES)
            ),
        },
        "runtime": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scikit_learn": sklearn.__version__,
            "total_seconds": round(time.perf_counter() - started, 3),
        },
        "conclusion": {
            "summary": summary,
            "minimum_supported_k": minimum_label,
            "first_statistically_supported_k": (
                f"K{first_statistical_k}" if first_statistical_k is not None else "NONE"
            ),
            "star_gate": star_gate,
            "ranking_gate": rank_gate,
            "product_gate": "WAITING_FOR_REACT_INPUT_COST_AND_FULL_CATALOG",
        },
    }
    if not all(manifest["validation"].values()):
        raise RuntimeError(f"REC-EV-003B validation failed: {manifest['validation']}")
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(build_report(manifest), encoding="utf-8")
    print(f"Manifest written to {args.manifest}")
    print(f"Evidence written to {args.evidence}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
