"""Fit FM-v6 prefix profiles without mounting VALIDATION labels or FINAL_TEST."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import numpy as np

from fm_v2_train import cgroup_peak, file_pin, read_training, tree_pin, weighted_mse
from fm_v5_train import (
    FACTOR_PROFILES, LINEAR_PROFILES, _ensemble, _factor_models, _fit_linear_profile,
    _select_linear_reg, _write_predictions,
)


def run(args: argparse.Namespace) -> dict:
    started = time.monotonic()
    if not args.output_root.is_dir() or any(args.output_root.iterdir()):
        raise FileExistsError("fit output must be an existing empty directory")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if os.environ.get("FM_RUNTIME_IMAGE_ID") != config["runtime"]["image_id"]:
        raise RuntimeError("runtime image ID mismatch")
    prepared_path = args.prepared_root / "manifest.json"
    prepared = json.loads(prepared_path.read_text(encoding="utf-8"))
    if (prepared.get("schema_version") != 6 or prepared.get("status") != "PASS" or
            prepared.get("validation_labels") != "NOT_MOUNTED" or
            prepared.get("validation_labels_opened") is not False or
            prepared.get("final_test_opened") is not False):
        raise RuntimeError("prepared prefix contract failed")
    if args.preflight:
        expected = config["resource_preflight"]
        if (prepared["subset"]["modulus"] != expected["user_modulus"] or
                prepared["subset"]["bucket"] != expected["user_bucket"] or prepared["subset"]["is_full"]):
            raise RuntimeError("preflight subset differs from frozen resource gate")
    elif config["status"] != "PREREGISTERED_REVIEWED" or not prepared["subset"]["is_full"]:
        raise RuntimeError("full fit requires reviewed config and full prepared population")
    elif prepared.get("promotion", {}).get("receipt") != config["resource_preflight"].get("receipt"):
        raise RuntimeError("full fit prepared artifact lacks the frozen promotion receipt")
    for name, expected in prepared["files"].items():
        if file_pin(args.prepared_root / name) != expected:
            raise RuntimeError(f"prepared file pin mismatch: {name}")
    selection_schema = json.loads((args.prepared_root / "selection-feature-schema.json").read_text(encoding="utf-8"))
    full_schema = json.loads((args.prepared_root / "feature-schema.json").read_text(encoding="utf-8"))
    X_selection, y_selection, w_selection, split_selection = read_training(
        args.prepared_root / "selection-train-targets.parquet", len(selection_schema["ordered_names"])
    )
    X, y, weight, split = read_training(
        args.prepared_root / "train-targets.parquet", len(full_schema["ordered_names"])
    )
    if (not np.array_equal(y_selection, y) or not np.array_equal(w_selection, weight) or
            not np.array_equal(split_selection, split)):
        raise RuntimeError("selection/full TRAIN matrices have different row targets or splits")
    regs = [float(value) for value in config["model"]["linear_reg_grid"]]
    models, selected_regs, linear_reports = {}, {}, {}
    for profile in LINEAR_PROFILES:
        reg, selection_report = _select_linear_reg(
            X_selection, y, weight, split,
            selection_schema["profiles"][profile]["linear_indices"], regs,
        )
        model, solver = _fit_linear_profile(
            X, y, weight, full_schema["profiles"][profile]["linear_indices"], reg,
        )
        models[profile], selected_regs[profile] = model, reg
        linear_reports[profile] = selection_report | {"final_solver": solver}
    factors, factor_reports = _factor_models(
        X_selection, y, weight, split, selection_schema, X, full_schema, config, models, selected_regs,
    )
    tuning = {"linear_profiles": linear_reports, "factor_profiles": factor_reports,
              "selection_preprocessing_scope": "FIT_ONLY",
              "final_preprocessing_scope": "ALL_TRAIN_AFTER_SELECTION",
              "linear_parameters_frozen_in_fm": True,
              "validation_labels_read": False, "final_test_opened": False}
    tuning_path = args.output_root / "tuning-report.json"
    tuning_path.write_text(json.dumps(tuning, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for profile, model in models.items():
        model.save(args.output_root / profile / "model/model.npz")
    seeds = [int(value) for value in config["model"]["model_seeds"]]
    for profile, members in factors.items():
        for seed, member in zip(seeds, members):
            member.save(args.output_root / profile / "model" / f"seed-{seed}.npz")
    feature_count = len(full_schema["ordered_names"])
    target_stats = _write_predictions(
        args.prepared_root / "validation-targets.parquet",
        args.output_root / "validation-target-predictions.parquet", feature_count, models, factors, config,
    )
    candidate_stats = _write_predictions(
        args.prepared_root / "validation-candidates.parquet",
        args.output_root / "validation-candidate-predictions.parquet", feature_count, models, factors, config,
    )
    predictions = {name: model.predict(X) for name, model in models.items()}
    predictions.update({name: _ensemble(members, X) for name, members in factors.items()})
    metrics = {name: {"weighted_training_mse": weighted_mse(y, values, weight)}
               for name, values in predictions.items()}
    artifacts = {
        "models": {profile: tree_pin(args.output_root / profile / "model")
                   for profile in (*LINEAR_PROFILES, *FACTOR_PROFILES)},
        "target_predictions": file_pin(args.output_root / "validation-target-predictions.parquet"),
        "candidate_predictions": file_pin(args.output_root / "validation-candidate-predictions.parquet"),
    }
    report = {
        "schema_version": 6, "status": "PASS", "run_kind": "RESOURCE_PREFLIGHT" if args.preflight else "FULL",
        "profiles": config["model"]["profiles"], "model_seeds": seeds,
        "training_rows": len(y), "feature_count": feature_count,
        "selection_feature_count": len(selection_schema["ordered_names"]), "training_metrics": metrics,
        "target_prediction_stats": target_stats, "candidate_prediction_stats": candidate_stats,
        "prepared_manifest": file_pin(prepared_path), "config": file_pin(args.config),
        "feature_schemas": {"selection": file_pin(args.prepared_root / "selection-feature-schema.json"),
                            "full": file_pin(args.prepared_root / "feature-schema.json")},
        "tuning_report": file_pin(tuning_path), "artifacts": artifacts,
        "elapsed_seconds": time.monotonic() - started, "resource": cgroup_peak(),
        "runtime_image_id": config["runtime"]["image_id"], "gpu_training": False,
        "validation_labels_read": False, "final_test_opened": False,
    }
    (args.output_root / "run-report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--preflight", action="store_true")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
