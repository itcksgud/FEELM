"""Train an exactly matched additive baseline and frozen-additive content FM ensemble."""

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
from scipy import sparse

from fm_v2_train import (
    OUTPUT_COLUMNS,
    CrossFM,
    cgroup_peak,
    file_pin,
    fit_linear,
    read_training,
    tree_pin,
    weighted_mse,
)


@dataclass
class EnsembleFM:
    members: list[CrossFM]

    def predict(self, X: sparse.csr_matrix) -> np.ndarray:
        return np.mean(np.vstack([member.predict(X) for member in self.members]), axis=0)

    def save(self, root: Path, seeds: list[int]) -> None:
        for seed, member in zip(seeds, self.members):
            member.save(root / f"seed-{seed}.npz")


def empty_additive(intercept: float, linear: np.ndarray) -> CrossFM:
    indices = np.empty(0, dtype=np.int32)
    factors = np.empty((0, 0), dtype=np.float64)
    return CrossFM(intercept, linear.copy(), indices, indices.copy(), indices.copy(),
                   factors, factors.copy(), factors.copy())


def _adam_scalar(value: float, gradient: float, first: float, second: float, epoch: int,
                 learning_rate: float, beta1: float, beta2: float, epsilon: float) -> tuple[float, float, float]:
    first = beta1 * first + (1.0 - beta1) * gradient
    second = beta2 * second + (1.0 - beta2) * gradient * gradient
    update = (first / (1.0 - beta1 ** epoch)) / (math.sqrt(second / (1.0 - beta2 ** epoch)) + epsilon)
    return value - learning_rate * update, first, second


def fit_additive(X_fit: sparse.csr_matrix, y_fit: np.ndarray, weight_fit: np.ndarray,
                 X_select: sparse.csr_matrix, y_select: np.ndarray, weight_select: np.ndarray,
                 initial: CrossFM, learning_rate: float, reg: float, optimizer: dict,
                 max_epochs: int | None = None, patience: int | None = None,
                 restore_best: bool = True) -> tuple[CrossFM, dict]:
    model = initial.copy()
    first = np.zeros_like(model.linear)
    second = np.zeros_like(model.linear)
    first_b = second_b = 0.0
    beta1, beta2 = float(optimizer["beta1"]), float(optimizer["beta2"])
    epsilon = float(optimizer["epsilon"])
    clip_norm = float(optimizer["gradient_clip_norm"])
    epochs = int(max_epochs if max_epochs is not None else optimizer["max_epochs"])
    wait_limit = int(patience if patience is not None else optimizer["patience"])
    best_model = model.copy()
    best_value = weighted_mse(y_select, model.predict(X_select), weight_select)
    best_epoch = 0
    history = [{"epoch": 0, "fit_mse": weighted_mse(y_fit, model.predict(X_fit), weight_fit),
                "selection_mse": best_value, "gradient_norm": None, "gradient_clip_scale": 1.0}]
    wait = 0
    for epoch in range(1, epochs + 1):
        prediction = model.intercept + np.asarray(X_fit @ model.linear).reshape(-1)
        error = (2.0 * weight_fit / weight_fit.sum()) * (prediction - y_fit)
        gradient_b = float(error.sum())
        gradient_w = np.asarray(X_fit.T @ error).reshape(-1) + 2.0 * reg * model.linear
        norm = math.sqrt(gradient_b * gradient_b + float(np.square(gradient_w).sum()))
        scale = min(1.0, clip_norm / max(norm, 1e-12))
        gradient_b *= scale
        gradient_w *= scale
        model.intercept, first_b, second_b = _adam_scalar(
            model.intercept, gradient_b, first_b, second_b, epoch,
            learning_rate, beta1, beta2, epsilon,
        )
        first = beta1 * first + (1.0 - beta1) * gradient_w
        second = beta2 * second + (1.0 - beta2) * np.square(gradient_w)
        model.linear -= learning_rate * ((first / (1.0 - beta1 ** epoch)) /
                                         (np.sqrt(second / (1.0 - beta2 ** epoch)) + epsilon))
        fit_value = weighted_mse(y_fit, model.predict(X_fit), weight_fit)
        selection_value = weighted_mse(y_select, model.predict(X_select), weight_select)
        history.append({"epoch": epoch, "fit_mse": fit_value, "selection_mse": selection_value,
                        "gradient_norm": norm, "gradient_clip_scale": scale})
        if selection_value < best_value - 1e-10:
            best_value, best_model, best_epoch, wait = selection_value, model.copy(), epoch, 0
        else:
            wait += 1
        if restore_best and wait >= wait_limit:
            break
    selected_model = best_model if restore_best else model
    return selected_model, {
        "best_epoch": best_epoch,
        "best_selection_mse": best_value,
        "epochs_executed": len(history) - 1,
        "returned_epoch": best_epoch if restore_best else len(history) - 1,
        "history": history,
    }


def initialize_factor_model(additive: CrossFM, profile: dict, factor_size: int, seed: int) -> CrossFM:
    rng = np.random.default_rng(seed)
    candidate_indices = np.asarray(profile["candidate_factor_indices"], dtype=np.int32)
    positive_indices = np.asarray(profile["positive_factor_indices"], dtype=np.int32)
    negative_indices = np.asarray(profile["negative_factor_indices"], dtype=np.int32)
    candidate = rng.normal(0.0, 0.01, size=(len(candidate_indices), factor_size))
    positive = np.zeros((len(positive_indices), factor_size), dtype=np.float64)
    negative = np.zeros((len(negative_indices), factor_size), dtype=np.float64)
    return CrossFM(additive.intercept, additive.linear.copy(), candidate_indices, positive_indices, negative_indices,
                   candidate, positive, negative)


def factor_objective_and_gradients(
    model: CrossFM, X: sparse.csr_matrix, y: np.ndarray, weight: np.ndarray, reg: float,
    Xc: sparse.csr_matrix | None = None, Xp: sparse.csr_matrix | None = None,
    Xn: sparse.csr_matrix | None = None,
) -> tuple[float, dict[str, np.ndarray]]:
    """Return weighted-MSE-plus-L2 and exact factor gradients; additive terms stay frozen."""
    Xc = X[:, model.candidate_indices] if Xc is None else Xc
    Xp = X[:, model.positive_indices] if Xp is None else Xp
    Xn = X[:, model.negative_indices] if Xn is None else Xn
    candidate = np.asarray(Xc @ model.candidate_factors)
    positive = np.asarray(Xp @ model.positive_factors)
    negative = np.asarray(Xn @ model.negative_factors)
    prediction = model.intercept + np.asarray(X @ model.linear).reshape(-1)
    prediction += np.sum(candidate * (positive + negative), axis=1)
    error = (2.0 * weight / weight.sum()) * (prediction - y)
    gradients = {
        "candidate_factors": np.asarray(Xc.T @ (error[:, None] * (positive + negative))) +
                             2.0 * reg * model.candidate_factors,
        "positive_factors": np.asarray(Xp.T @ (error[:, None] * candidate)) +
                            2.0 * reg * model.positive_factors,
        "negative_factors": np.asarray(Xn.T @ (error[:, None] * candidate)) +
                            2.0 * reg * model.negative_factors,
    }
    penalty = sum(float(np.square(getattr(model, name)).sum())
                  for name in ("candidate_factors", "positive_factors", "negative_factors"))
    objective = weighted_mse(y, prediction, weight) + reg * penalty
    return objective, gradients


def fit_factor_only(X_fit: sparse.csr_matrix, y_fit: np.ndarray, weight_fit: np.ndarray,
                    X_select: sparse.csr_matrix, y_select: np.ndarray, weight_select: np.ndarray,
                    initial: CrossFM, learning_rate: float, reg: float, optimizer: dict,
                    max_epochs: int | None = None, patience: int | None = None,
                    restore_best: bool = True) -> tuple[CrossFM, dict]:
    model = initial.copy()
    frozen_intercept = float(model.intercept)
    frozen_linear = model.linear.copy()
    Xc = X_fit[:, model.candidate_indices]
    Xp = X_fit[:, model.positive_indices]
    Xn = X_fit[:, model.negative_indices]
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
    best_epoch = 0
    history = [{"epoch": 0, "fit_mse": weighted_mse(y_fit, model.predict(X_fit), weight_fit),
                "selection_mse": best_value, "gradient_norm": None, "gradient_clip_scale": 1.0}]
    wait = 0
    for epoch in range(1, epochs + 1):
        _, gradients = factor_objective_and_gradients(
            model, X_fit, y_fit, weight_fit, reg, Xc=Xc, Xp=Xp, Xn=Xn
        )
        norm = math.sqrt(sum(float(np.square(value).sum()) for value in gradients.values()))
        scale = min(1.0, clip_norm / max(norm, 1e-12))
        for name in names:
            gradients[name] *= scale
            first[name] = beta1 * first[name] + (1.0 - beta1) * gradients[name]
            second[name] = beta2 * second[name] + (1.0 - beta2) * np.square(gradients[name])
            update = ((first[name] / (1.0 - beta1 ** epoch)) /
                      (np.sqrt(second[name] / (1.0 - beta2 ** epoch)) + epsilon))
            setattr(model, name, getattr(model, name) - learning_rate * update)
        if model.intercept != frozen_intercept or not np.array_equal(model.linear, frozen_linear):
            raise RuntimeError("factor-only optimizer mutated frozen additive parameters")
        fit_value = weighted_mse(y_fit, model.predict(X_fit), weight_fit)
        selection_value = weighted_mse(y_select, model.predict(X_select), weight_select)
        history.append({"epoch": epoch, "fit_mse": fit_value, "selection_mse": selection_value,
                        "gradient_norm": norm, "gradient_clip_scale": scale})
        if selection_value < best_value - 1e-10:
            best_value, best_model, best_epoch, wait = selection_value, model.copy(), epoch, 0
        else:
            wait += 1
        if restore_best and wait >= wait_limit:
            break
    selected_model = best_model if restore_best else model
    return selected_model, {
        "best_epoch": best_epoch,
        "best_selection_mse": best_value,
        "epochs_executed": len(history) - 1,
        "returned_epoch": best_epoch if restore_best else len(history) - 1,
        "history": history,
    }


def tune_and_refit(X: sparse.csr_matrix, y: np.ndarray, weight: np.ndarray, split: np.ndarray,
                   schema: dict, config: dict) -> tuple[CrossFM, EnsembleFM, list[CrossFM], dict]:
    fit_mask, holdout_mask = split == "FIT", split == "HOLDOUT"
    X_fit, y_fit, w_fit = X[fit_mask], y[fit_mask], weight[fit_mask]
    X_holdout, y_holdout, w_holdout = X[holdout_mask], y[holdout_mask], weight[holdout_mask]
    ridge_trials = []
    for reg in config["ridge_initialization_reg_grid"]:
        intercept, linear, solver = fit_linear(X_fit, y_fit, w_fit, float(reg))
        holdout_mse = weighted_mse(y_holdout, intercept + X_holdout @ linear, w_holdout)
        ridge_trials.append({"reg": float(reg), "holdout_mse": holdout_mse, "solver": solver,
                             "intercept": intercept, "linear": linear})
    selected_ridge = min(ridge_trials, key=lambda item: item["holdout_mse"])
    additive_trials = []
    for params in config["additive_grid"]:
        initial = empty_additive(selected_ridge["intercept"], selected_ridge["linear"])
        model, trace = fit_additive(
            X_fit, y_fit, w_fit, X_holdout, y_holdout, w_holdout, initial,
            float(params["learning_rate"]), float(params["reg"]), config["optimizer"],
        )
        additive_trials.append({"parameters": params, "trace": trace, "model": model})
    selected_additive = min(additive_trials, key=lambda item: item["trace"]["best_selection_mse"])

    profile = schema["profiles"]["content_cross_factor_v3"]
    factor_trials = []
    for params in config["factor_grid"]:
        seed_trials = []
        for seed in config["model_seeds"]:
            initial = initialize_factor_model(
                selected_additive["model"], profile, int(params["factor_size"]), int(seed)
            )
            model, trace = fit_factor_only(
                X_fit, y_fit, w_fit, X_holdout, y_holdout, w_holdout, initial,
                float(params["learning_rate"]), float(params["reg"]), config["optimizer"],
            )
            seed_trials.append({"seed": int(seed), "trace": trace, "model": model})
        factor_trials.append({
            "parameters": params,
            "mean_best_holdout_mse": float(np.mean(
                [trial["trace"]["best_selection_mse"] for trial in seed_trials]
            )),
            "seed_trials": seed_trials,
        })
    selected_factor = min(factor_trials, key=lambda item: item["mean_best_holdout_mse"])

    all_intercept, all_linear, all_solver = fit_linear(
        X, y, weight, float(selected_ridge["reg"])
    )
    additive_initial = empty_additive(all_intercept, all_linear)
    additive_epochs = int(selected_additive["trace"]["best_epoch"])
    final_additive, additive_refit = fit_additive(
        X, y, weight, X, y, weight, additive_initial,
        float(selected_additive["parameters"]["learning_rate"]),
        float(selected_additive["parameters"]["reg"]), config["optimizer"],
        max_epochs=additive_epochs, patience=max(1, additive_epochs + 1), restore_best=False,
    )
    final_members = []
    factor_refits = []
    for selected_seed in selected_factor["seed_trials"]:
        seed = int(selected_seed["seed"])
        epochs = int(selected_seed["trace"]["best_epoch"])
        initial = initialize_factor_model(
            final_additive, profile, int(selected_factor["parameters"]["factor_size"]), seed
        )
        member, trace = fit_factor_only(
            X, y, weight, X, y, weight, initial,
            float(selected_factor["parameters"]["learning_rate"]),
            float(selected_factor["parameters"]["reg"]), config["optimizer"],
            max_epochs=epochs, patience=max(1, epochs + 1), restore_best=False,
        )
        if member.intercept != final_additive.intercept or not np.array_equal(member.linear, final_additive.linear):
            raise RuntimeError("final factor member does not share the exact additive parameters")
        final_members.append(member)
        factor_refits.append({"seed": seed, "selected_epoch": epochs, "trace": trace})

    tuning = {
        "preprocessing_fit_policy": config["preprocessing_fit_policy"],
        "internal_fit_rows": int(fit_mask.sum()),
        "internal_holdout_rows": int(holdout_mask.sum()),
        "internal_fit_weight": float(w_fit.sum()),
        "internal_holdout_weight": float(w_holdout.sum()),
        "ridge_trials": [{key: value for key, value in item.items() if key not in {"intercept", "linear"}}
                         for item in ridge_trials],
        "selected_ridge_reg": selected_ridge["reg"],
        "additive_trials": [{"parameters": item["parameters"], "trace": item["trace"]}
                            for item in additive_trials],
        "selected_additive_parameters": selected_additive["parameters"],
        "selected_additive_epoch": additive_epochs,
        "factor_trials": [
            {"parameters": item["parameters"], "mean_best_holdout_mse": item["mean_best_holdout_mse"],
             "seed_trials": [{"seed": trial["seed"], "trace": trial["trace"]}
                             for trial in item["seed_trials"]]}
            for item in factor_trials
        ],
        "selected_factor_parameters": selected_factor["parameters"],
        "selected_factor_mean_holdout_mse": selected_factor["mean_best_holdout_mse"],
        "selected_factor_epochs_by_seed": {str(item["seed"]): int(item["trace"]["best_epoch"])
                                           for item in selected_factor["seed_trials"]},
        "additive_holdout_mse": selected_additive["trace"]["best_selection_mse"],
        "factor_relative_holdout_mse": (selected_factor["mean_best_holdout_mse"] /
                                        selected_additive["trace"]["best_selection_mse"]),
        "final_ridge_solver": all_solver,
        "final_additive_refit": additive_refit,
        "final_factor_refits": factor_refits,
        "additive_parameters_frozen_and_shared": True,
    }
    return final_additive, EnsembleFM(final_members), final_members, tuning


def write_predictions(input_path: Path, output_path: Path, model, feature_count: int,
                      bounds: list[float], batch_size: int = 100_000) -> dict:
    writer: pq.ParquetWriter | None = None
    rows = clipped = 0
    raw_min, raw_max = float("inf"), float("-inf")
    try:
        parquet = pq.ParquetFile(input_path)
        for batch in parquet.iter_batches(batch_size=batch_size):
            table = pa.Table.from_batches([batch])
            from fm_v2_train import frame_to_csr
            X = frame_to_csr(table, feature_count)
            supported = np.asarray(table["supported"].to_pylist(), dtype=bool)
            raw = model.predict(X)
            if not np.isfinite(raw[supported]).all():
                raise RuntimeError("model produced NaN/Inf")
            prediction = np.clip(raw, float(bounds[0]), float(bounds[1]))
            clipped += int(np.sum(supported & ((raw < float(bounds[0])) | (raw > float(bounds[1])))))
            if supported.any():
                raw_min = min(raw_min, float(raw[supported].min()))
                raw_max = max(raw_max, float(raw[supported].max()))
            raw = np.where(supported, raw, np.nan)
            prediction = np.where(supported, prediction, np.nan)
            output = table.select(OUTPUT_COLUMNS)
            output = output.append_column("raw_prediction", pa.array(raw, type=pa.float64(), from_pandas=True))
            output = output.append_column("prediction", pa.array(prediction, type=pa.float64(), from_pandas=True))
            if writer is None:
                output_path.mkdir(parents=True, exist_ok=False)
                writer = pq.ParquetWriter(output_path / "part-00000.parquet", output.schema, compression="zstd")
            writer.write_table(output)
            rows += len(output)
    finally:
        if writer is not None:
            writer.close()
    if writer is None:
        raise RuntimeError("prediction input had no rows")
    return {"rows": rows, "raw_min": raw_min, "raw_max": raw_max,
            "clipped_rows": clipped, "clipped_fraction": clipped / max(1, rows)}


def contribution_stats(additive: CrossFM, factor, X: sparse.csr_matrix) -> dict:
    values = factor.predict(X) - additive.predict(X)
    absolute = np.abs(values)
    return {"rows": int(len(values)), "mean": float(values.mean()), "mean_absolute": float(absolute.mean()),
            "p95_absolute": float(np.quantile(absolute, 0.95)), "maximum_absolute": float(absolute.max()),
            "nonzero_fraction": float(np.mean(absolute > 1e-15))}


def run(args: argparse.Namespace) -> dict:
    started = time.monotonic()
    if os.environ.get("FM_OUTPUT_MOUNT_ISOLATED") != "true":
        raise RuntimeError("FM-v3 requires the runner's isolated output mount contract")
    if not args.output_root.is_dir() or any(args.output_root.iterdir()):
        raise FileExistsError(f"output must be an existing empty isolated mount: {args.output_root}")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    prepared_path = args.prepared_root / "manifest.json"
    prepared = json.loads(prepared_path.read_text(encoding="utf-8"))
    schema_path = args.prepared_root / "feature-schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    if prepared["status"] != "PASS" or prepared["final_test"] != "NOT_PRESENT_IN_INPUT_OR_PREPARED_ARTIFACTS":
        raise RuntimeError("prepared contract failed")
    if os.environ.get("FM_RUNTIME_IMAGE_ID") != config["runtime"]["image_id"]:
        raise RuntimeError("runtime image ID mismatch")
    for filename, expected in prepared["files"].items():
        if file_pin(args.prepared_root / filename) != expected:
            raise RuntimeError(f"prepared artifact hash mismatch: {filename}")

    feature_count = len(schema["ordered_names"])
    X, y, weight, split = read_training(args.prepared_root / "train-targets.parquet", feature_count)
    additive, ensemble, seed_models, tuning = tune_and_refit(X, y, weight, split, schema, config)
    tuning_path = args.output_root / "tuning-report.json"
    tuning_path.write_text(json.dumps(tuning, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    additive_root = args.output_root / "matched_additive_v3"
    factor_root = args.output_root / "content_cross_factor_v3"
    additive.save(additive_root / "model" / "model.npz")
    ensemble.save(factor_root / "model", [int(seed) for seed in config["model_seeds"]])
    models = {"matched_additive_v3": additive, "content_cross_factor_v3": ensemble}
    prediction_stats = {}
    for name, model in models.items():
        root = args.output_root / name
        target_stats = write_predictions(
            args.prepared_root / "validation-targets.parquet", root / "validation-target-predictions.parquet",
            model, feature_count, config["prediction_bounds"],
        )
        candidate_stats = write_predictions(
            args.prepared_root / "validation-candidates.parquet", root / "validation-candidate-predictions.parquet",
            model, feature_count, config["prediction_bounds"],
        )
        prediction_stats[name] = {"target": target_stats, "candidate": candidate_stats}

    seed_prediction_stats = {}
    for seed, model in zip(config["model_seeds"], seed_models):
        path = factor_root / "diagnostic-seed-target-predictions" / f"seed-{seed}.parquet"
        seed_prediction_stats[str(seed)] = write_predictions(
            args.prepared_root / "validation-targets.parquet", path, model,
            feature_count, config["prediction_bounds"],
        )

    train_additive = additive.predict(X)
    train_factor = ensemble.predict(X)
    contribution = contribution_stats(additive, ensemble, X)
    common = {
        "schema_version": 3,
        "status": "PASS",
        "runtime_image_id": config["runtime"]["image_id"],
        "feature_count": feature_count,
        "training_rows": int(X.shape[0]),
        "training_target_weight": float(weight.sum()),
        "prepared_manifest": file_pin(prepared_path),
        "feature_schema": file_pin(schema_path),
        "failed_rows": 0,
        "final_test_opened": False,
    }
    additive_metrics = common | {
        "profile": "matched_additive_v3",
        "estimator_kind": "RIDGE_INITIALIZED_FULL_BATCH_ADAM_ADDITIVE",
        "interaction_enabled": False,
        "effective_interaction_enabled": False,
        "weighted_training_raw_mse": weighted_mse(y, train_additive, weight),
        "weighted_training_clipped_mse": weighted_mse(y, np.clip(train_additive, *config["prediction_bounds"]), weight),
        "tuning": {"selected_ridge_reg": tuning["selected_ridge_reg"],
                   "selected_additive_parameters": tuning["selected_additive_parameters"],
                   "selected_additive_epoch": tuning["selected_additive_epoch"]},
        "target_prediction_stats": prediction_stats["matched_additive_v3"]["target"],
        "candidate_prediction_stats": prediction_stats["matched_additive_v3"]["candidate"],
        "model_artifact": tree_pin(additive_root / "model"),
        "target_predictions_artifact": tree_pin(additive_root / "validation-target-predictions.parquet"),
        "candidate_predictions_artifact": tree_pin(additive_root / "validation-candidate-predictions.parquet"),
    }
    effective = contribution["maximum_absolute"] > 1e-15
    factor_metrics = common | {
        "profile": "content_cross_factor_v3",
        "estimator_kind": "FROZEN_ADDITIVE_CONTENT_CROSS_FM_SEED_MEAN",
        "interaction_enabled": True,
        "effective_interaction_enabled": effective,
        "model_seeds": config["model_seeds"],
        "weighted_training_raw_mse": weighted_mse(y, train_factor, weight),
        "weighted_training_clipped_mse": weighted_mse(y, np.clip(train_factor, *config["prediction_bounds"]), weight),
        "tuning": {"selected_factor_parameters": tuning["selected_factor_parameters"],
                   "selected_factor_epochs_by_seed": tuning["selected_factor_epochs_by_seed"]},
        "factor_contribution": contribution,
        "additive_parameters_frozen_and_shared": True,
        "target_prediction_stats": prediction_stats["content_cross_factor_v3"]["target"],
        "candidate_prediction_stats": prediction_stats["content_cross_factor_v3"]["candidate"],
        "seed_target_prediction_stats": seed_prediction_stats,
        "model_artifact": tree_pin(factor_root / "model"),
        "target_predictions_artifact": tree_pin(factor_root / "validation-target-predictions.parquet"),
        "candidate_predictions_artifact": tree_pin(factor_root / "validation-candidate-predictions.parquet"),
        "seed_target_predictions_artifact": tree_pin(factor_root / "diagnostic-seed-target-predictions"),
    }
    (additive_root / "metrics.json").write_text(
        json.dumps(additive_metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (factor_root / "metrics.json").write_text(
        json.dumps(factor_metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    run_report = {
        "schema_version": 3,
        "status": "PASS",
        "profiles": config["profiles"],
        "elapsed_seconds": time.monotonic() - started,
        "resource": cgroup_peak(),
        "runtime_image_id": config["runtime"]["image_id"],
        "tuning_report": file_pin(tuning_path),
        "full_eligible_train_users": int(config["expected_input"]["source_users"]["TRAIN"]),
        "container_mount_contract": {
            "network": "none",
            "workspace": "/workspace:ro",
            "prepared": "/prepared:ro",
            "output": "/output:rw",
            "output_mount_is_dedicated_empty_directory": True,
            "output_parent_or_siblings_visible": False,
            "raw_ratings_visible": False,
            "source_input_with_final_test_visible": False
        },
        "final_test_opened": False,
    }
    (args.output_root / "run-report.json").write_text(
        json.dumps(run_report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(run_report, indent=2, ensure_ascii=False))
    return run_report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
