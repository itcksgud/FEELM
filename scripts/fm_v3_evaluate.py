"""Report-only validation evaluation for the preregistered FM-v3 contrast."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from fm_zero_n_evaluate import bootstrap_ci, evaluate_profile, file_pin, tree_pin


ROOT = Path(__file__).resolve().parents[1]
CALCULATOR_FILES = ("scripts/fm_v3_evaluate.py", "scripts/fm_zero_n_evaluate.py")


def calculator_bundle() -> dict:
    files = {name: file_pin(ROOT / name) for name in CALCULATOR_FILES}
    digest = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"algorithm": "ORDERED_FILE_PINS_V1", "files": files, "digest": digest}


def paired_target_result(additive_path: Path, factor_path: Path, seed: int, replicates: int) -> dict:
    columns = ["episode_id", "uid", "label", "prediction"]
    additive = pd.read_parquet(additive_path, columns=columns)
    factor = pd.read_parquet(factor_path, columns=columns)
    merged = factor.merge(additive, on=["episode_id", "uid", "label"], suffixes=("_factor", "_additive"),
                          validate="one_to_one")
    if len(merged) != len(additive):
        raise RuntimeError("paired validation target rows differ")
    merged["mse_factor"] = np.square(merged.prediction_factor - merged.label)
    merged["mse_additive"] = np.square(merged.prediction_additive - merged.label)
    merged["mse_delta"] = merged.mse_factor - merged.mse_additive
    users = merged.groupby("uid", sort=True).agg(
        factor_mse=("mse_factor", "mean"), additive_mse=("mse_additive", "mean"), delta=("mse_delta", "mean")
    )
    return {
        "rows": int(len(merged)),
        "users": int(len(users)),
        "factor_user_macro_mse": float(users.factor_mse.mean()),
        "additive_user_macro_mse": float(users.additive_mse.mean()),
        "user_macro_mse_delta": float(users.delta.mean()),
        "row_micro_mse_delta": float(merged.mse_delta.mean()),
        "user_bootstrap_95_ci": bootstrap_ci(users.delta.to_numpy(float), seed, replicates),
        "improved_user_fraction": float((users.delta < 0).mean()),
    }


def evaluate(args: argparse.Namespace) -> dict:
    if args.output.exists():
        raise FileExistsError(f"output already exists: {args.output}")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    run_report = json.loads((args.fits_root / "run-report.json").read_text(encoding="utf-8"))
    tuning = json.loads((args.fits_root / "tuning-report.json").read_text(encoding="utf-8"))
    if run_report["status"] != "PASS" or run_report["final_test_opened"] is not False:
        raise RuntimeError("run report is not a sealed PASS")
    profiles = {}
    bootstrap_seed = int(config["model_seeds"][0])
    for name in config["profiles"]:
        root = args.fits_root / name
        metrics = json.loads((root / "metrics.json").read_text(encoding="utf-8"))
        if metrics["status"] != "PASS" or metrics["final_test_opened"] is not False:
            raise RuntimeError(f"invalid metrics: {name}")
        result = evaluate_profile(root, config, bootstrap_seed)
        result["fit"] = metrics
        result["artifacts"] = {
            "metrics": file_pin(root / "metrics.json"),
            "model": tree_pin(root / "model"),
            "target_predictions": tree_pin(root / "validation-target-predictions.parquet"),
            "candidate_predictions": tree_pin(root / "validation-candidate-predictions.parquet"),
        }
        profiles[name] = result

    additive_targets = args.fits_root / "matched_additive_v3" / "validation-target-predictions.parquet"
    factor_root = args.fits_root / "content_cross_factor_v3"
    primary = paired_target_result(
        additive_targets,
        factor_root / "validation-target-predictions.parquet",
        bootstrap_seed,
        int(config["bootstrap_replicates"]),
    )
    seed_diagnostics = {}
    for model_seed in config["model_seeds"]:
        seed_diagnostics[str(model_seed)] = paired_target_result(
            additive_targets,
            factor_root / "diagnostic-seed-target-predictions" / f"seed-{model_seed}.parquet",
            int(model_seed),
            int(config["bootstrap_replicates"]),
        )
    ci_upper = float(primary["user_bootstrap_95_ci"][1])
    decision = ("SUPPORT_CONTENT_INTERACTION" if ci_upper < 0.0
                else "KEEP_MATCHED_ADDITIVE_BASELINE")
    report = {
        "schema_version": 3,
        "status": "PASS",
        "config": file_pin(args.config),
        "calculator": calculator_bundle(),
        "selection_policy": config["validation_selection_policy"],
        "primary_estimand": config["primary_estimand"],
        "primary_comparison": primary,
        "primary_decision_rule": config["primary_decision"],
        "primary_decision": decision,
        "seed_diagnostics": seed_diagnostics,
        "internal_tuning": tuning,
        "profiles": profiles,
        "validation_was_not_used_for_model_selection": True,
        "final_test_opened": False,
        "movie_lens_evidence": "WEAK_OFFLINE_EVIDENCE_FOR_A_DIFFERENT_POPULATION",
        "ranking_metrics_are_diagnostic_only": True,
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
