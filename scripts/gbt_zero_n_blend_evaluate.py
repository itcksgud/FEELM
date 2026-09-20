"""Select and confirm popularity/GBT rank blends independently at each exact K."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from gbt_zero_n_policy_evaluate import evaluate_scores, paired_comparison


DEFAULT_ALPHAS = (0.0, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0)
QUALITY_METRICS = ("observed_positive_recall_at_10", "observed_ndcg_at_10")


def rank_normalize(frame: pd.DataFrame, column: str) -> np.ndarray:
    return frame.groupby("episode_id", sort=False)[column].rank(method="average", pct=True).to_numpy(dtype=float)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evaluation-split", choices=["SELECTION", "CONFIRM"], required=True)
    parser.add_argument("--selection-report", type=Path)
    parser.add_argument("--noninferiority-margin", type=float, default=0.02)
    parser.add_argument("--alphas", type=float, nargs="*", default=list(DEFAULT_ALPHAS))
    parser.add_argument("--seed", type=int, default=622)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"output already exists: {args.output}")
    columns = [
        "episode_id", "uid", "evaluation_split", "candidate_movie_id", "n", "n_bucket",
        "total_history_count", "provided_history_count", "supported_history_count",
        "is_full_history", "is_controlled_prefix", "label", "label_state", "is_target", "prediction",
        "policy.popular_count_score",
    ]
    frame = pd.read_parquet(args.predictions, columns=columns)
    frame = frame[frame.evaluation_split == args.evaluation_split].copy()
    if frame.empty:
        raise RuntimeError(f"no rows for {args.evaluation_split}")
    if not (frame.provided_history_count == frame.n).all():
        raise RuntimeError("providedHistoryCount differs from exact K")
    if not (frame.supported_history_count == frame.n).all():
        raise RuntimeError("supportedHistoryCount differs from exact K")
    alphas = sorted(set(float(value) for value in args.alphas))
    if not alphas or alphas[0] != 0.0 or any(value < 0 or value > 1 for value in alphas):
        raise ValueError("alphas must be within [0,1] and include 0")

    frame["popular_rank"] = rank_normalize(frame, "policy.popular_count_score")
    frame["gbt_rank"] = rank_normalize(frame, "prediction")
    results: dict[str, dict] = {}
    selected: dict[str, float] = {}
    selection_values: dict[str, float] = {}
    if args.selection_report:
        prior = json.loads(args.selection_report.read_text(encoding="utf-8"))
        selection_values = {
            str(key): float(value) for key, value in prior["selected_alpha_by_k"].items()
        }

    for index, k in enumerate(sorted(int(value) for value in frame.n.unique())):
        cell = frame[frame.n == k].copy()
        cell_results: dict[str, dict] = {}
        episode_rows: dict[float, pd.DataFrame] = {}
        summaries: dict[float, dict] = {}
        for alpha in alphas:
            score = (1.0 - alpha) * cell.popular_rank.to_numpy(dtype=float) + alpha * cell.gbt_rank.to_numpy(dtype=float)
            summary, episodes = evaluate_scores(cell, score)
            summaries[alpha] = summary
            episode_rows[alpha] = episodes
        baseline = episode_rows[0.0]
        for alpha in alphas:
            comparisons = {}
            for offset, metric in enumerate((*QUALITY_METRICS, "observed_low_rating_exposure_at_10")):
                item, _ = paired_comparison(
                    episode_rows[alpha], baseline, metric, args.seed + 100 * index + 10 * offset + int(alpha * 100)
                )
                comparisons[metric] = item
            quality_pass = all(
                comparisons[metric]["bootstrap_95_ci"] is not None
                and comparisons[metric]["bootstrap_95_ci"][0] >= -args.noninferiority_margin
                for metric in QUALITY_METRICS
            )
            exposure_delta = comparisons["observed_low_rating_exposure_at_10"]["delta"]
            cell_results[f"{alpha:.2f}"] = {
                "alpha": alpha,
                "metrics": summaries[alpha],
                "paired_vs_pure_popularity": comparisons,
                "quality_noninferiority_pass": quality_pass,
                "low_rating_exposure_point_improved": exposure_delta is not None and exposure_delta < 0,
            }

        if args.evaluation_split == "SELECTION":
            eligible = [
                alpha for alpha in alphas if alpha > 0
                and cell_results[f"{alpha:.2f}"]["quality_noninferiority_pass"]
                and cell_results[f"{alpha:.2f}"]["low_rating_exposure_point_improved"]
            ]
            selected[str(k)] = max(eligible, default=0.0)
        else:
            if str(k) not in selection_values:
                raise RuntimeError(f"selection report does not contain K={k}")
            alpha = selection_values[str(k)]
            if alpha not in alphas:
                raise RuntimeError(f"selected alpha {alpha} is absent from confirmation grid")
            selected[str(k)] = alpha
        results[str(k)] = cell_results

    confirmation = {}
    if args.evaluation_split == "CONFIRM":
        for k, alpha in selected.items():
            item = results[k][f"{alpha:.2f}"]
            confirmation[k] = {
                "alpha": alpha,
                "status": (
                    "VALIDATED" if alpha > 0 and item["quality_noninferiority_pass"]
                    and item["low_rating_exposure_point_improved"]
                    else "PURE_POPULARITY" if alpha == 0
                    else "REJECTED_ON_CONFIRM"
                ),
            }
    positive_alphas = [value for value in selected.values() if value > 0]
    report = {
        "schema_version": 1,
        "status": "PASS",
        "evaluation_split": args.evaluation_split,
        "scope": "SAME_POOL_RERANK_CONTROLLED_PREFIX",
        "actual_low_history_status": "NOT_EVALUATED_CONTROLLED_PREFIX",
        "score": "WITHIN_EPISODE_PERCENTILE_BLEND=(1-alpha)*POPULAR_COUNT+alpha*GBT",
        "noninferiority_margin": args.noninferiority_margin,
        "quality_metrics": list(QUALITY_METRICS),
        "secondary_requirement": "LOW_RATING_EXPOSURE_POINT_DELTA_BELOW_ZERO",
        "alphas": alphas,
        "selected_alpha_by_k": selected,
        "k_based_blend_candidate": bool(positive_alphas),
        "confirmation": confirmation,
        "by_k": results,
        "limitations": [
            "alpha is selected on simulated MovieLens prefixes and is not a production serving weight",
            "candidate targets are forced into the sampled rerank pool",
            "UNKNOWN_SAMPLED candidates remain unjudged",
        ],
        "final_test_opened": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "evaluation_split": args.evaluation_split,
        "selected_alpha_by_k": selected,
        "output": str(args.output),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
