"""Fit FM-v5 profiles without mounting validation labels or FINAL_TEST."""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from fm_v2_train import (
    CrossFM, cgroup_peak, file_pin, fit_linear, frame_to_csr, initialize_fm,
    read_training, tree_pin, weighted_mse,
)


IDENTITY_COLUMNS = (
    "episode_id", "uid", "target_movie_id", "candidate_movie_id", "prediction_at", "n", "n_bucket",
    "total_history_count", "supported_history_count", "is_full_history", "candidate_rank", "is_target",
    "supported", "candidate_genres",
)
LINEAR_PROFILES = ("content_linear_v5", "aggregate_linear_v5", "sparse_linear_v5")
FACTOR_PROFILES = ("legacy_aggregate_fm_v5", "sparse_history_fm_v5", "sparse_history_content_fm_v5")


@dataclass
class LinearModel:
    intercept: float
    linear: np.ndarray

    def predict(self, matrix):
        return self.intercept + np.asarray(matrix @ self.linear).reshape(-1)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, intercept=np.asarray([self.intercept]), linear=self.linear)


def fit_factor_only(X_fit, y_fit, weight_fit, X_select, y_select, weight_select,
                    initial: CrossFM, learning_rate: float, reg: float, optimizer: dict,
                    max_epochs: int | None = None, patience: int | None = None) -> tuple[CrossFM, dict]:
    """Fit only interaction factors while keeping the matched linear model bit-identical."""
    model = initial.copy()
    frozen_intercept = float(model.intercept)
    frozen_linear = model.linear.copy()
    Xc, Xp, Xn = (X_fit[:, model.candidate_indices], X_fit[:, model.positive_indices],
                  X_fit[:, model.negative_indices])
    names = ("candidate_factors", "positive_factors", "negative_factors")
    first = {name: np.zeros_like(getattr(model, name)) for name in names}
    second = {name: np.zeros_like(getattr(model, name)) for name in names}
    beta1, beta2 = float(optimizer["beta1"]), float(optimizer["beta2"])
    epsilon = float(optimizer["epsilon"])
    clip_norm = float(optimizer["gradient_clip_norm"])
    epochs = int(max_epochs if max_epochs is not None else optimizer["max_epochs"])
    wait_limit = int(patience if patience is not None else optimizer["patience"])
    best_model = model.copy()
    best_value = weighted_mse(y_select, model.predict(X_select), weight_select)
    history = [{"epoch": 0, "fit_mse": weighted_mse(y_fit, model.predict(X_fit), weight_fit),
                "selection_mse": best_value, "gradient_norm": None}]
    best_epoch, wait = 0, 0
    for epoch in range(1, epochs + 1):
        candidate = np.asarray(Xc @ model.candidate_factors)
        positive = np.asarray(Xp @ model.positive_factors)
        negative = np.asarray(Xn @ model.negative_factors)
        prediction = frozen_intercept + np.asarray(X_fit @ frozen_linear).reshape(-1)
        prediction += np.sum(candidate * (positive + negative), axis=1)
        error = (2.0 * weight_fit / weight_fit.sum()) * (prediction - y_fit)
        gradients = {
            "candidate_factors": np.asarray(Xc.T @ (error[:, None] * (positive + negative))) +
                                 2.0 * reg * model.candidate_factors,
            "positive_factors": np.asarray(Xp.T @ (error[:, None] * candidate)) +
                                2.0 * reg * model.positive_factors,
            "negative_factors": np.asarray(Xn.T @ (error[:, None] * candidate)) +
                                2.0 * reg * model.negative_factors,
        }
        norm = math.sqrt(sum(float(np.square(value).sum()) for value in gradients.values()))
        scale = min(1.0, clip_norm / max(norm, 1e-12))
        for name in names:
            gradient = gradients[name] * scale
            first[name] = beta1 * first[name] + (1.0 - beta1) * gradient
            second[name] = beta2 * second[name] + (1.0 - beta2) * np.square(gradient)
            update = ((first[name] / (1.0 - beta1 ** epoch)) /
                      (np.sqrt(second[name] / (1.0 - beta2 ** epoch)) + epsilon))
            setattr(model, name, getattr(model, name) - learning_rate * update)
        if model.intercept != frozen_intercept or not np.array_equal(model.linear, frozen_linear):
            raise RuntimeError("factor-only optimizer changed frozen linear parameters")
        fit_value = weighted_mse(y_fit, model.predict(X_fit), weight_fit)
        selection_value = weighted_mse(y_select, model.predict(X_select), weight_select)
        history.append({"epoch": epoch, "fit_mse": fit_value, "selection_mse": selection_value,
                        "gradient_norm": norm, "gradient_clip_scale": scale})
        if selection_value < best_value - 1e-10:
            best_value, best_model, best_epoch, wait = selection_value, model.copy(), epoch, 0
        else:
            wait += 1
        if wait >= wait_limit:
            break
    return best_model, {"best_epoch": best_epoch, "best_selection_mse": best_value,
                        "epochs_executed": len(history) - 1, "history": history,
                        "linear_frozen": True, "intercept_frozen": True}


def _select_linear_reg(X, y, weight, split, indices: list[int], regs: list[float]) -> tuple[float, dict]:
    fit_mask, holdout_mask = split == "FIT", split == "HOLDOUT"
    trials = []
    selected_indices = np.asarray(indices, dtype=np.int32)
    for reg in regs:
        intercept, local, solver = fit_linear(X[fit_mask][:, selected_indices], y[fit_mask], weight[fit_mask], reg)
        prediction = intercept + np.asarray(X[holdout_mask][:, selected_indices] @ local).reshape(-1)
        trials.append({"reg": reg, "holdout_mse": weighted_mse(y[holdout_mask], prediction, weight[holdout_mask]),
                       "solver": solver})
    selected = min(trials, key=lambda item: (item["holdout_mse"], item["reg"]))
    return float(selected["reg"]), {"trials": trials, "selected_reg": float(selected["reg"]),
                                    "preprocessing_fit_scope": "FIT_ONLY"}


def _fit_linear_profile(X, y, weight, indices: list[int], reg: float) -> tuple[LinearModel, dict]:
    selected_indices = np.asarray(indices, dtype=np.int32)
    intercept, local, solver = fit_linear(X[:, selected_indices], y, weight, reg)
    full = np.zeros(X.shape[1], dtype=np.float64)
    full[selected_indices] = local
    return LinearModel(intercept, full), solver


def _factor_models(X_selection, y, weight, split, selection_schema: dict,
                   X_full, full_schema: dict, config: dict,
                   linear_models: dict[str, LinearModel], selected_regs: dict[str, float]) \
        -> tuple[dict[str, list[CrossFM]], dict]:
    fit_mask, holdout_mask = split == "FIT", split == "HOLDOUT"
    seeds = [int(value) for value in config["model"]["model_seeds"]]
    optimizer = config["model"]["optimizer"]
    result, report = {}, {}
    for profile in FACTOR_PROFILES:
        base_name = config["interaction_comparisons"][profile]
        base_indices = selection_schema["profiles"][base_name]["linear_indices"]
        selected = np.asarray(base_indices, dtype=np.int32)
        selection_intercept, local, selection_solver = fit_linear(
            X_selection[fit_mask][:, selected], y[fit_mask], weight[fit_mask], selected_regs[base_name]
        )
        selection_linear = np.zeros(X_selection.shape[1], dtype=np.float64)
        selection_linear[selected] = local
        selection_base = LinearModel(selection_intercept, selection_linear)
        trials = []
        profile_schema = selection_schema["profiles"][profile]
        for params in config["model"]["fm_grid"]:
            seed_trials = []
            for seed in seeds:
                initial = initialize_fm(selection_base.intercept, selection_base.linear, profile_schema,
                                        int(params["factor_size"]), seed)
                model, trace = fit_factor_only(
                    X_selection[fit_mask], y[fit_mask], weight[fit_mask],
                    X_selection[holdout_mask], y[holdout_mask], weight[holdout_mask], initial,
                    float(params["learning_rate"]), float(params["reg"]), optimizer,
                )
                seed_trials.append({"seed": seed, "model": model, "trace": trace})
            trials.append({"parameters": params, "mean_holdout_mse": float(np.mean([
                item["trace"]["best_selection_mse"] for item in seed_trials
            ])), "seed_trials": seed_trials})
        selected_trial = min(trials, key=lambda item: (item["mean_holdout_mse"], item["parameters"]["reg"]))
        members, refits = [], []
        for trial in selected_trial["seed_trials"]:
            epochs = int(trial["trace"]["best_epoch"])
            params = selected_trial["parameters"]
            base_model = linear_models[base_name]
            initial = initialize_fm(base_model.intercept, base_model.linear, full_schema["profiles"][profile],
                                    int(params["factor_size"]), int(trial["seed"]))
            model, trace = fit_factor_only(
                X_full, y, weight, X_full, y, weight, initial, float(params["learning_rate"]),
                float(params["reg"]), optimizer, max_epochs=epochs, patience=max(1, epochs + 1),
            )
            members.append(model)
            refits.append({"seed": trial["seed"], "selected_epoch": epochs, "trace": trace})
        result[profile] = members
        report[profile] = {
            "matched_linear_ablation": base_name,
            "linear_parameters_frozen": True,
            "trials": [{"parameters": item["parameters"], "mean_holdout_mse": item["mean_holdout_mse"],
                        "seed_trials": [{"seed": seed_item["seed"], "trace": seed_item["trace"]}
                                        for seed_item in item["seed_trials"]]}
                       for item in trials],
            "selected_parameters": selected_trial["parameters"],
            "selected_mean_holdout_mse": selected_trial["mean_holdout_mse"],
            "selection_linear_solver": selection_solver,
            "refits": refits,
        }
    return result, report


def _ensemble(members: list[CrossFM], matrix) -> np.ndarray:
    return np.mean(np.vstack([model.predict(matrix) for model in members]), axis=0)


def _write_predictions(input_path: Path, output_path: Path, feature_count: int,
                       linear_models: dict[str, LinearModel],
                       factors: dict[str, list[CrossFM]], config: dict) -> dict:
    parquet = pq.ParquetFile(input_path)
    if {"label", "observed_rating", "label_state"} & set(parquet.schema_arrow.names):
        raise RuntimeError("validation label firewall violation")
    columns = [name for name in IDENTITY_COLUMNS if name in parquet.schema_arrow.names]
    writer = None
    rows = 0
    bounds = [float(value) for value in config["prediction_bounds"]]
    seeds = [int(value) for value in config["model"]["model_seeds"]]
    stats = {name: {"raw_min": float("inf"), "raw_max": float("-inf"), "clipped": 0}
             for name in (*LINEAR_PROFILES, *FACTOR_PROFILES)}
    try:
        for batch in parquet.iter_batches(batch_size=100_000):
            table = pa.Table.from_batches([batch])
            matrix = frame_to_csr(table, feature_count)
            predictions = {name: model.predict(matrix) for name, model in linear_models.items()}
            seed_values = {}
            for profile, members in factors.items():
                values = [member.predict(matrix) for member in members]
                predictions[profile] = np.mean(np.vstack(values), axis=0)
                for seed, value in zip(seeds, values):
                    seed_values[f"{profile}__seed_{seed}"] = value
            output = table.select(columns)
            if "popularity_bayes_score" in table.column_names:
                output = output.append_column("popularity_bayes_v5", table["popularity_bayes_score"])
                output = output.append_column("popularity_count", table["popularity_count"])
            for name, raw in predictions.items():
                if not np.isfinite(raw).all():
                    raise RuntimeError(f"{name} produced NaN/Inf")
                clipped = np.clip(raw, bounds[0], bounds[1])
                stats[name]["raw_min"] = min(stats[name]["raw_min"], float(raw.min()))
                stats[name]["raw_max"] = max(stats[name]["raw_max"], float(raw.max()))
                stats[name]["clipped"] += int(np.sum(raw != clipped))
                output = output.append_column(name, pa.array(clipped, type=pa.float64()))
            for name, raw in seed_values.items():
                output = output.append_column(name, pa.array(np.clip(raw, bounds[0], bounds[1]), type=pa.float64()))
            if writer is None:
                writer = pq.ParquetWriter(output_path, output.schema, compression="zstd")
            writer.write_table(output)
            rows += len(output)
    finally:
        if writer is not None:
            writer.close()
    if writer is None:
        raise RuntimeError("prediction input is empty")
    return {"rows": rows, "profiles": {name: values | {"clipped_fraction": values["clipped"] / rows}
                                        for name, values in stats.items()}}


def run(args: argparse.Namespace) -> dict:
    started = time.monotonic()
    if not args.output_root.is_dir() or any(args.output_root.iterdir()):
        raise FileExistsError("fit output must be an existing empty isolated directory")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if config["status"] != "PREREGISTERED_REVIEWED":
        raise RuntimeError("FM-v5 requires independent preregistration review before fitting")
    if os.environ.get("FM_RUNTIME_IMAGE_ID") != config["runtime"]["image_id"]:
        raise RuntimeError("runtime image ID mismatch")
    if any(path.name.startswith("validation-") and "label" in path.name for path in args.prepared_root.iterdir()):
        raise RuntimeError("validation labels are visible to fitting")
    prepared = json.loads((args.prepared_root / "manifest.json").read_text(encoding="utf-8"))
    if (prepared.get("status") != "PASS" or prepared.get("schema_version") != 5 or
            prepared.get("final_test_opened") is not False or prepared.get("validation_labels_opened") is not False):
        raise RuntimeError("prepared manifest contract failed")
    for name, expected in prepared["files"].items():
        if name != "manifest.json" and file_pin(args.prepared_root / name) != expected:
            raise RuntimeError(f"prepared file hash mismatch: {name}")
    selection_schema = json.loads((args.prepared_root / "selection-feature-schema.json").read_text(encoding="utf-8"))
    schema = json.loads((args.prepared_root / "feature-schema.json").read_text(encoding="utf-8"))
    selection_feature_count = len(selection_schema["ordered_names"])
    feature_count = len(schema["ordered_names"])
    X_selection, y_selection, weight_selection, split_selection = read_training(
        args.prepared_root / "selection-train-targets.parquet", selection_feature_count
    )
    X, y, weight, split = read_training(args.prepared_root / "train-targets.parquet", feature_count)
    if (not np.array_equal(y_selection, y) or not np.array_equal(weight_selection, weight) or
            not np.array_equal(split_selection, split)):
        raise RuntimeError("selection/full TRAIN row alignment differs")
    regs = [float(value) for value in config["model"]["linear_reg_grid"]]
    linear_models: dict[str, LinearModel] = {}
    linear_reports, selected_regs = {}, {}
    for profile in LINEAR_PROFILES:
        selected_reg, selection_report = _select_linear_reg(
            X_selection, y, weight, split,
            selection_schema["profiles"][profile]["linear_indices"], regs,
        )
        model, solver = _fit_linear_profile(
            X, y, weight, schema["profiles"][profile]["linear_indices"], selected_reg
        )
        linear_models[profile] = model
        selected_regs[profile] = selected_reg
        linear_reports[profile] = selection_report | {"final_solver": solver}
    factors, factor_report = _factor_models(
        X_selection, y, weight, split, selection_schema, X, schema, config,
        linear_models, selected_regs,
    )
    tuning = {"linear_profiles": linear_reports,
              "factor_profiles": factor_report, "validation_labels_read": False,
              "final_test_opened": False,
              "selection_preprocessing_scope": "FIT_ONLY",
              "final_preprocessing_scope": "FULL_TRAIN_AFTER_SELECTION"}
    (args.output_root / "tuning-report.json").write_text(
        json.dumps(tuning, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    for profile, model in linear_models.items():
        model.save(args.output_root / profile / "model/model.npz")
    seeds = [int(value) for value in config["model"]["model_seeds"]]
    for profile, members in factors.items():
        for seed, member in zip(seeds, members):
            member.save(args.output_root / profile / "model" / f"seed-{seed}.npz")
    target_stats = _write_predictions(
        args.prepared_root / "validation-targets.parquet",
        args.output_root / "validation-target-predictions.parquet", feature_count,
        linear_models, factors, config,
    )
    candidate_stats = _write_predictions(
        args.prepared_root / "validation-candidates.parquet",
        args.output_root / "validation-candidate-predictions.parquet", feature_count,
        linear_models, factors, config,
    )
    train_prediction = {name: model.predict(X) for name, model in linear_models.items()}
    train_prediction.update({profile: _ensemble(members, X) for profile, members in factors.items()})
    metrics = {name: {"weighted_training_mse": weighted_mse(y, prediction, weight)}
               for name, prediction in train_prediction.items()}
    report = {
        "schema_version": 5, "status": "PASS", "profiles": config["model"]["profiles"],
        "training_rows": len(y), "feature_count": feature_count,
        "selection_feature_count": selection_feature_count, "training_metrics": metrics,
        "target_prediction_stats": target_stats, "candidate_prediction_stats": candidate_stats,
        "prepared_manifest": file_pin(args.prepared_root / "manifest.json"),
        "feature_schemas": {"selection": file_pin(args.prepared_root / "selection-feature-schema.json"),
                            "full": file_pin(args.prepared_root / "feature-schema.json")},
        "tuning_report": file_pin(args.output_root / "tuning-report.json"),
        "artifacts": {
            "models": {profile: tree_pin(args.output_root / profile / "model")
                       for profile in (*LINEAR_PROFILES, *FACTOR_PROFILES)},
            "target_predictions": file_pin(args.output_root / "validation-target-predictions.parquet"),
            "candidate_predictions": file_pin(args.output_root / "validation-candidate-predictions.parquet"),
        },
        "elapsed_seconds": time.monotonic() - started, "resource": cgroup_peak(),
        "runtime_image_id": config["runtime"]["image_id"], "validation_labels_read": False,
        "final_test_opened": False,
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
    run(parser.parse_args())


if __name__ == "__main__":
    main()
