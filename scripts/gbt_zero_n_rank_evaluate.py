"""Compare pairwise ranking and regression ordering on identical validation candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from gbt_zero_n_evaluate import bootstrap_mean_ci, candidate_metrics


def pin(path: Path) -> dict[str, int | str]:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def tree_pin(path: Path) -> dict:
    files = {str(item.relative_to(path)).replace("\\", "/"): pin(item)
             for item in sorted(path.rglob("*")) if item.is_file() and not item.name.startswith(".")}
    encoded = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    return {"algorithm": "SORTED_RELATIVE_FILE_PINS_V1", "sha256": hashlib.sha256(encoded).hexdigest(),
            "files": files}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--regression-root", type=Path, required=True)
    parser.add_argument("--rank-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=622)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"output already exists: {args.output}")
    regression_path = args.regression_root / "validation-candidate-predictions.parquet"
    rank_path = args.rank_root / "validation-candidate-predictions.parquet"
    regression = pd.read_parquet(regression_path)
    ranking = pd.read_parquet(rank_path)
    identity = ["episode_id", "candidate_movie_id"]
    if set(map(tuple, regression[identity].to_numpy())) != set(map(tuple, ranking[identity].to_numpy())):
        raise RuntimeError("regression and rank objectives did not score identical candidates")
    regression_summary, regression_episode = candidate_metrics(regression)
    rank_summary, rank_episode = candidate_metrics(ranking)
    joined = rank_episode[["episode_id", "uid", "observed_ndcg_at_10"]].merge(
        regression_episode[["episode_id", "observed_ndcg_at_10"]],
        on="episode_id", suffixes=("_rank", "_regression"), validate="one_to_one",
    )
    judged = joined.dropna(subset=["observed_ndcg_at_10_rank", "observed_ndcg_at_10_regression"]).copy()
    judged["delta"] = judged.observed_ndcg_at_10_rank - judged.observed_ndcg_at_10_regression
    user_delta = judged.groupby("uid", sort=True).delta.mean()
    report = {
        "status": "PASS",
        "comparison": "PAIRWISE_RANK_GBT_VS_RESPONSE_RELATION_REGRESSION_GBT",
        "same_candidate_identity": True,
        "regression": regression_summary,
        "pairwise_rank": rank_summary,
        "paired_user_macro_ndcg_at_10_delta": float(user_delta.mean()),
        "paired_user_macro_ndcg_at_10_delta_bootstrap_95_ci": bootstrap_mean_ci(
            user_delta.to_numpy(dtype=float), args.seed
        ),
        "paired_users": int(len(user_delta)),
        "pairwise_rank_rating_mse": None,
        "pairwise_rank_rating_mse_reason": "PAIRWISE_OBJECTIVE_DOES_NOT_PRODUCE_A_RATING_SCALE_PREDICTION",
        "regression_predictions": tree_pin(regression_path) if regression_path.is_dir() else pin(regression_path),
        "rank_predictions": tree_pin(rank_path) if rank_path.is_dir() else pin(rank_path),
        "rank_metrics": pin(args.rank_root / "metrics.json"),
        "calculator": pin(Path(__file__)),
        "final_test_opened": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
