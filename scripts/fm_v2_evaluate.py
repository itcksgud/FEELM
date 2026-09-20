"""Evaluate FM-v2 profiles on VALIDATION without selecting models on VALIDATION."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from fm_zero_n_evaluate import bootstrap_ci, evaluate_profile, file_pin, tree_pin


ROOT = Path(__file__).resolve().parents[1]
CALCULATOR_FILES = ("scripts/fm_v2_evaluate.py", "scripts/fm_zero_n_evaluate.py")


def calculator_bundle() -> dict:
    files = {name: file_pin(ROOT / name) for name in CALCULATOR_FILES}
    digest = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"algorithm": "ORDERED_FILE_PINS_V1", "files": files, "digest": digest}


def paired_vs_linear(linear_root: Path, factor_root: Path, seed: int, replicates: int) -> dict:
    columns = ["episode_id", "uid", "label", "prediction"]
    linear = pd.read_parquet(linear_root / "validation-target-predictions.parquet", columns=columns)
    factor = pd.read_parquet(factor_root / "validation-target-predictions.parquet", columns=columns)
    merged = factor.merge(linear, on=["episode_id", "uid", "label"], suffixes=("_factor", "_linear"),
                          validate="one_to_one")
    if len(merged) != len(linear):
        raise RuntimeError("paired validation rows differ")
    merged["mse_delta"] = np.square(merged.prediction_factor - merged.label) - np.square(merged.prediction_linear - merged.label)
    users = merged.groupby("uid", sort=True).mse_delta.mean()
    return {
        "rows": int(len(merged)), "users": int(merged.uid.nunique()),
        "mean_mse_delta": float(merged.mse_delta.mean()),
        "user_macro_mse_delta": float(users.mean()),
        "user_bootstrap_95_ci": bootstrap_ci(users.to_numpy(float), seed, replicates),
        "improved_user_fraction": float((users < 0).mean()),
    }


def evaluate(args: argparse.Namespace) -> dict:
    if args.output.exists():
        raise FileExistsError(f"output already exists: {args.output}")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    run_report = json.loads((args.fits_root / "run-report.json").read_text(encoding="utf-8"))
    tuning = json.loads((args.fits_root / "tuning-report.json").read_text(encoding="utf-8"))
    if run_report["status"] != "PASS" or run_report["final_test_opened"] is not False:
        raise RuntimeError("run report is not sealed PASS")
    profiles = {}
    seed = int(config["model_seed"])
    for name in config["profiles"]:
        root = args.fits_root / name
        metrics = json.loads((root / "metrics.json").read_text(encoding="utf-8"))
        if metrics["status"] != "PASS" or metrics["final_test_opened"] is not False:
            raise RuntimeError(f"invalid metrics: {name}")
        result = evaluate_profile(root, config, seed)
        result["fit"] = metrics
        result["artifacts"] = {
            "metrics": file_pin(root / "metrics.json"),
            "model": tree_pin(root / "model"),
            "target_predictions": tree_pin(root / "validation-target-predictions.parquet"),
            "candidate_predictions": tree_pin(root / "validation-candidate-predictions.parquet"),
        }
        if name != "sparse_linear_v2":
            result["paired_vs_sparse_linear_v2"] = paired_vs_linear(
                args.fits_root / "sparse_linear_v2", root, seed, int(config["bootstrap_replicates"])
            )
        profiles[name] = result
    report = {
        "schema_version": 2, "status": "PASS",
        "config": file_pin(args.config),
        "calculator": calculator_bundle(),
        "selection_policy": config["validation_selection_policy"],
        "internal_tuning": tuning,
        "profiles": profiles,
        "validation_was_not_used_for_model_selection": True,
        "final_test_opened": False,
        "movie_lens_evidence": "WEAK_OFFLINE_EVIDENCE_FOR_A_DIFFERENT_POPULATION",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fits-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    evaluate(parser.parse_args())


if __name__ == "__main__":
    main()
