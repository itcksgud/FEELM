"""Tune and fit weighted cross-field FM models without using VALIDATION for selection."""

from __future__ import annotations

import argparse
import hashlib
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
from scipy.sparse.linalg import lsqr


OUTPUT_COLUMNS = [
    "episode_id", "role", "uid", "target_movie_id", "candidate_movie_id", "prediction_at",
    "n", "n_bucket", "total_history_count", "supported_history_count", "label", "candidate_rank",
    "label_state", "is_target", "candidate_genres", "supported", "unsupported_reason",
]


def file_pin(path: Path) -> dict[str, int | str]:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def tree_pin(path: Path) -> dict:
    files = {str(item.relative_to(path)).replace("\\", "/"): file_pin(item)
             for item in sorted(path.rglob("*")) if item.is_file()}
    return {
        "bytes": sum(int(value["bytes"]) for value in files.values()),
        "files": len(files),
        "sha256": hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
    }


def cgroup_peak() -> dict:
    for path in (Path("/sys/fs/cgroup/memory.peak"), Path("/sys/fs/cgroup/memory/memory.max_usage_in_bytes")):
        if path.exists():
            return {"peak_ram_bytes": int(path.read_text().strip()), "peak_ram_source": str(path)}
    return {"peak_ram_bytes": "NOT_EXPOSED", "peak_ram_source": "NOT_EXPOSED"}


def lists_to_csr(indices: list, values: list, rows: int, columns: int) -> sparse.csr_matrix:
    lengths = np.fromiter((len(item) for item in indices), dtype=np.int64, count=rows)
    indptr = np.empty(rows + 1, dtype=np.int64)
    indptr[0] = 0
    np.cumsum(lengths, out=indptr[1:])
    nnz = int(indptr[-1])
    if nnz:
        flat_indices = np.fromiter((value for item in indices for value in item), dtype=np.int32, count=nnz)
        flat_values = np.fromiter((value for item in values for value in item), dtype=np.float64, count=nnz)
    else:
        flat_indices = np.empty(0, dtype=np.int32)
        flat_values = np.empty(0, dtype=np.float64)
    return sparse.csr_matrix((flat_values, flat_indices, indptr), shape=(rows, columns))


def frame_to_csr(table: pa.Table, columns: int) -> sparse.csr_matrix:
    return lists_to_csr(table["feature_indices"].to_pylist(), table["feature_values"].to_pylist(), len(table), columns)


def read_training(path: Path, columns: int) -> tuple[sparse.csr_matrix, np.ndarray, np.ndarray, np.ndarray]:
    table = pq.read_table(path, columns=["feature_indices", "feature_values", "label", "sample_weight", "internal_split"])
    matrix = frame_to_csr(table, columns)
    labels = table["label"].to_numpy(zero_copy_only=False).astype(np.float64)
    weights = table["sample_weight"].to_numpy(zero_copy_only=False).astype(np.float64)
    split = np.asarray(table["internal_split"].to_pylist())
    return matrix, labels, weights, split


def weighted_mse(actual: np.ndarray, prediction: np.ndarray, weight: np.ndarray) -> float:
    return float(np.dot(weight, np.square(prediction - actual)) / weight.sum())


def fit_linear(X: sparse.csr_matrix, y: np.ndarray, weight: np.ndarray, reg: float) -> tuple[float, np.ndarray, dict]:
    design = sparse.hstack((sparse.csr_matrix(np.ones((X.shape[0], 1))), X), format="csr")
    root_weight = np.sqrt(weight)
    weighted_design = design.multiply(root_weight[:, None])
    weighted_y = y * root_weight
    result = lsqr(weighted_design, weighted_y, damp=math.sqrt(reg * weight.sum()),
                  atol=1e-7, btol=1e-7, iter_lim=300, show=False)
    coefficients = result[0]
    return float(coefficients[0]), coefficients[1:].astype(np.float64), {
        "iterations": int(result[2]), "stop_code": int(result[1]), "residual_norm": float(result[3]),
        "condition_estimate": float(result[6]),
    }


@dataclass
class CrossFM:
    intercept: float
    linear: np.ndarray
    candidate_indices: np.ndarray
    positive_indices: np.ndarray
    negative_indices: np.ndarray
    candidate_factors: np.ndarray
    positive_factors: np.ndarray
    negative_factors: np.ndarray

    def predict(self, X: sparse.csr_matrix) -> np.ndarray:
        prediction = self.intercept + np.asarray(X @ self.linear).reshape(-1)
        if self.candidate_factors.size:
            candidate = np.asarray(X[:, self.candidate_indices] @ self.candidate_factors)
            positive = np.asarray(X[:, self.positive_indices] @ self.positive_factors)
            negative = np.asarray(X[:, self.negative_indices] @ self.negative_factors)
            prediction = prediction + np.sum(candidate * (positive + negative), axis=1)
        return prediction

    def copy(self) -> "CrossFM":
        return CrossFM(self.intercept, self.linear.copy(), self.candidate_indices.copy(),
                       self.positive_indices.copy(), self.negative_indices.copy(),
                       self.candidate_factors.copy(), self.positive_factors.copy(), self.negative_factors.copy())

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, intercept=np.asarray([self.intercept]), linear=self.linear,
                            candidate_indices=self.candidate_indices, positive_indices=self.positive_indices,
                            negative_indices=self.negative_indices, candidate_factors=self.candidate_factors,
                            positive_factors=self.positive_factors, negative_factors=self.negative_factors)


def initialize_fm(intercept: float, linear: np.ndarray, profile: dict, factor_size: int, seed: int) -> CrossFM:
    rng = np.random.default_rng(seed)
    candidate_indices = np.asarray(profile["candidate_factor_indices"], dtype=np.int32)
    positive_indices = np.asarray(profile["positive_factor_indices"], dtype=np.int32)
    negative_indices = np.asarray(profile["negative_factor_indices"], dtype=np.int32)
    candidate = rng.normal(0.0, 0.01, size=(len(candidate_indices), factor_size))
    # Exact linear predictions at epoch 0 while retaining a symmetry-breaking candidate side.
    positive = np.zeros((len(positive_indices), factor_size), dtype=np.float64)
    negative = np.zeros((len(negative_indices), factor_size), dtype=np.float64)
    return CrossFM(intercept, linear.copy(), candidate_indices, positive_indices, negative_indices,
                   candidate, positive, negative)


def fit_fm(X_fit: sparse.csr_matrix, y_fit: np.ndarray, weight_fit: np.ndarray,
           X_select: sparse.csr_matrix, y_select: np.ndarray, weight_select: np.ndarray,
           initial: CrossFM, learning_rate: float, reg: float, optimizer: dict,
           max_epochs: int | None = None, patience: int | None = None) -> tuple[CrossFM, dict]:
    model = initial.copy()
    Xc = X_fit[:, model.candidate_indices]
    Xp = X_fit[:, model.positive_indices]
    Xn = X_fit[:, model.negative_indices]
    parameter_names = ("linear", "candidate_factors", "positive_factors", "negative_factors")
    first = {name: np.zeros_like(getattr(model, name)) for name in parameter_names}
    second = {name: np.zeros_like(getattr(model, name)) for name in parameter_names}
    first_b = second_b = 0.0
    beta1, beta2 = float(optimizer["beta1"]), float(optimizer["beta2"])
    epsilon = float(optimizer["epsilon"])
    clip_norm = float(optimizer["gradient_clip_norm"])
    epochs = int(max_epochs if max_epochs is not None else optimizer["max_epochs"])
    wait_limit = int(patience if patience is not None else optimizer["patience"])
    best_model = model.copy()
    best_value = weighted_mse(y_select, model.predict(X_select), weight_select)
    history = [{"epoch": 0, "fit_mse": weighted_mse(y_fit, model.predict(X_fit), weight_fit),
                "selection_mse": best_value, "gradient_norm": None}]
    best_epoch = 0
    wait = 0
    for epoch in range(1, epochs + 1):
        candidate = np.asarray(Xc @ model.candidate_factors)
        positive = np.asarray(Xp @ model.positive_factors)
        negative = np.asarray(Xn @ model.negative_factors)
        prediction = model.intercept + np.asarray(X_fit @ model.linear).reshape(-1) + np.sum(candidate * (positive + negative), axis=1)
        error = (2.0 * weight_fit / weight_fit.sum()) * (prediction - y_fit)
        gradients = {
            "linear": np.asarray(X_fit.T @ error).reshape(-1) + 2.0 * reg * model.linear,
            "candidate_factors": np.asarray(Xc.T @ (error[:, None] * (positive + negative))) + 2.0 * reg * model.candidate_factors,
            "positive_factors": np.asarray(Xp.T @ (error[:, None] * candidate)) + 2.0 * reg * model.positive_factors,
            "negative_factors": np.asarray(Xn.T @ (error[:, None] * candidate)) + 2.0 * reg * model.negative_factors,
        }
        gradient_b = float(error.sum())
        norm = math.sqrt(gradient_b * gradient_b + sum(float(np.square(value).sum()) for value in gradients.values()))
        scale = min(1.0, clip_norm / max(norm, 1e-12))
        gradient_b *= scale
        for name in gradients:
            gradients[name] *= scale
        first_b = beta1 * first_b + (1.0 - beta1) * gradient_b
        second_b = beta2 * second_b + (1.0 - beta2) * gradient_b * gradient_b
        model.intercept -= learning_rate * (first_b / (1.0 - beta1 ** epoch)) / (math.sqrt(second_b / (1.0 - beta2 ** epoch)) + epsilon)
        for name in parameter_names:
            first[name] = beta1 * first[name] + (1.0 - beta1) * gradients[name]
            second[name] = beta2 * second[name] + (1.0 - beta2) * np.square(gradients[name])
            update = (first[name] / (1.0 - beta1 ** epoch)) / (np.sqrt(second[name] / (1.0 - beta2 ** epoch)) + epsilon)
            setattr(model, name, getattr(model, name) - learning_rate * update)
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
                        "epochs_executed": len(history) - 1, "history": history}


def tune_models(X: sparse.csr_matrix, y: np.ndarray, weight: np.ndarray, split: np.ndarray,
                schema: dict, config: dict) -> tuple[dict[str, CrossFM], dict]:
    fit_mask, holdout_mask = split == "FIT", split == "HOLDOUT"
    X_fit, y_fit, w_fit = X[fit_mask], y[fit_mask], weight[fit_mask]
    X_holdout, y_holdout, w_holdout = X[holdout_mask], y[holdout_mask], weight[holdout_mask]
    linear_trials = []
    for reg in config["linear_reg_grid"]:
        intercept, linear, solver = fit_linear(X_fit, y_fit, w_fit, float(reg))
        fit_value = weighted_mse(y_fit, intercept + X_fit @ linear, w_fit)
        holdout_value = weighted_mse(y_holdout, intercept + X_holdout @ linear, w_holdout)
        linear_trials.append({"reg": float(reg), "fit_mse": fit_value, "holdout_mse": holdout_value,
                              "solver": solver, "intercept": intercept, "linear": linear})
    selected_linear = min(linear_trials, key=lambda item: item["holdout_mse"])
    public_linear_trials = [{key: value for key, value in item.items() if key not in {"intercept", "linear"}}
                            for item in linear_trials]
    tuning = {"linear_trials": public_linear_trials,
              "selected_linear_reg": selected_linear["reg"],
              "internal_fit_rows": int(fit_mask.sum()), "internal_holdout_rows": int(holdout_mask.sum()),
              "internal_fit_weight": float(w_fit.sum()), "internal_holdout_weight": float(w_holdout.sum()),
              "factor_profiles": {}}
    selected = {}
    for profile_name in ("cross_content_fm_v2", "cross_hybrid_fm_v2"):
        trials = []
        for params in config["fm_grid"]:
            initial = initialize_fm(selected_linear["intercept"], selected_linear["linear"],
                                    schema["profiles"][profile_name], int(params["factor_size"]),
                                    int(config["model_seed"]))
            model, trace = fit_fm(X_fit, y_fit, w_fit, X_holdout, y_holdout, w_holdout, initial,
                                  float(params["learning_rate"]), float(params["reg"]), config["optimizer"])
            trials.append({"parameters": params, "initialization_seed": int(config["model_seed"]),
                           "trace": trace, "model": model})
        best = min(trials, key=lambda item: item["trace"]["best_selection_mse"])
        tuning["factor_profiles"][profile_name] = {
            "trials": [{"parameters": item["parameters"], "initialization_seed": item["initialization_seed"],
                        "trace": item["trace"]} for item in trials],
            "selected_parameters": best["parameters"], "selected_epoch": best["trace"]["best_epoch"],
            "selected_initialization_seed": best["initialization_seed"],
            "linear_holdout_mse": selected_linear["holdout_mse"],
            "relative_holdout_mse": best["trace"]["best_selection_mse"] / selected_linear["holdout_mse"],
        }

    all_intercept, all_linear, all_solver = fit_linear(X, y, weight, float(selected_linear["reg"]))
    linear_model = CrossFM(all_intercept, all_linear, np.empty(0, dtype=np.int32), np.empty(0, dtype=np.int32),
                           np.empty(0, dtype=np.int32), np.empty((0, 0)), np.empty((0, 0)), np.empty((0, 0)))
    selected["sparse_linear_v2"] = linear_model
    tuning["final_linear_solver"] = all_solver
    tuning["final_linear_weighted_training_mse"] = weighted_mse(y, linear_model.predict(X), weight)
    for profile_name in ("cross_content_fm_v2", "cross_hybrid_fm_v2"):
        chosen = tuning["factor_profiles"][profile_name]
        params = chosen["selected_parameters"]
        initial = initialize_fm(all_intercept, all_linear, schema["profiles"][profile_name],
                                int(params["factor_size"]), int(chosen["selected_initialization_seed"]))
        epoch_count = int(chosen["selected_epoch"])
        model, trace = fit_fm(X, y, weight, X, y, weight, initial, float(params["learning_rate"]),
                              float(params["reg"]), config["optimizer"], max_epochs=epoch_count,
                              patience=max(1, epoch_count + 1))
        selected[profile_name] = model
        chosen["final_refit_trace"] = trace
        chosen["final_weighted_training_mse"] = weighted_mse(y, model.predict(X), weight)
        chosen["relative_training_mse"] = chosen["final_weighted_training_mse"] / tuning["final_linear_weighted_training_mse"]
    return selected, tuning


def write_predictions(input_path: Path, output_roots: dict[str, Path], models: dict[str, CrossFM],
                      feature_count: int, bounds: list[float], batch_size: int = 100_000) -> dict[str, dict]:
    writers: dict[str, pq.ParquetWriter] = {}
    counts = {name: 0 for name in models}
    raw_min = {name: float("inf") for name in models}
    raw_max = {name: float("-inf") for name in models}
    clipped = {name: 0 for name in models}
    try:
        parquet = pq.ParquetFile(input_path)
        for batch in parquet.iter_batches(batch_size=batch_size):
            table = pa.Table.from_batches([batch])
            X = frame_to_csr(table, feature_count)
            supported = np.asarray(table["supported"].to_pylist(), dtype=bool)
            base = table.select(OUTPUT_COLUMNS)
            for name, model in models.items():
                raw = model.predict(X)
                if not np.isfinite(raw[supported]).all():
                    raise RuntimeError(f"{name} produced NaN/Inf")
                prediction = np.clip(raw, float(bounds[0]), float(bounds[1]))
                raw = np.where(supported, raw, np.nan)
                prediction = np.where(supported, prediction, np.nan)
                raw_min[name] = min(raw_min[name], float(np.nanmin(raw)))
                raw_max[name] = max(raw_max[name], float(np.nanmax(raw)))
                clipped[name] += int(np.sum(supported & ((raw < float(bounds[0])) | (raw > float(bounds[1])))))
                output = base.append_column("raw_prediction", pa.array(raw, type=pa.float64(), from_pandas=True))
                output = output.append_column("prediction", pa.array(prediction, type=pa.float64(), from_pandas=True))
                if name not in writers:
                    output_roots[name].mkdir(parents=True, exist_ok=False)
                    writers[name] = pq.ParquetWriter(output_roots[name] / "part-00000.parquet", output.schema, compression="zstd")
                writers[name].write_table(output)
                counts[name] += len(output)
    finally:
        for writer in writers.values():
            writer.close()
    return {name: {"rows": counts[name], "raw_min": raw_min[name], "raw_max": raw_max[name],
                   "clipped_rows": clipped[name], "clipped_fraction": clipped[name] / max(1, counts[name])}
            for name in models}


def run(args: argparse.Namespace) -> dict:
    started = time.monotonic()
    if args.output_root.exists():
        raise FileExistsError(f"output already exists: {args.output_root}")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    prepared = json.loads((args.prepared_root / "manifest.json").read_text(encoding="utf-8"))
    schema = json.loads((args.prepared_root / "feature-schema.json").read_text(encoding="utf-8"))
    if prepared["status"] != "PASS" or prepared["final_test"] != "REJECTED_BEFORE_JSON_PARSE_AND_NOT_WRITTEN":
        raise RuntimeError("prepared contract failed")
    if os.environ.get("FM_RUNTIME_IMAGE_ID") != config["runtime"]["image_id"]:
        raise RuntimeError("runtime image ID mismatch")
    promotion = None
    if prepared["input_contract_id"] == "frozen_input":
        if args.promotion_report is None or not args.promotion_report.is_file():
            raise RuntimeError("10% frozen input requires a fixture promotion report")
        promotion_report = json.loads(args.promotion_report.read_text(encoding="utf-8"))
        expected_config_pin = file_pin(args.config)
        checks = (
            (promotion_report.get("status"), "PASS", "fixture status"),
            (promotion_report.get("input_contract_id"), "fixture_input", "fixture input contract"),
            (promotion_report.get("config"), expected_config_pin, "fixture config pin"),
            (promotion_report.get("source_bundle_digest"), prepared["source_bundle"]["digest"], "fixture source bundle"),
            (promotion_report.get("runtime_image_id"), config["runtime"]["image_id"], "fixture runtime"),
            (promotion_report.get("final_test_opened"), False, "fixture FINAL_TEST seal"),
        )
        for actual, expected, label in checks:
            if actual != expected:
                raise RuntimeError(f"promotion {label} mismatch")
        promotion = {"report": file_pin(args.promotion_report), "verified_fields": [label for _, _, label in checks]}
    for filename, expected in prepared["files"].items():
        if file_pin(args.prepared_root / filename) != expected:
            raise RuntimeError(f"prepared artifact hash mismatch: {filename}")
    args.output_root.mkdir(parents=True)
    feature_count = len(schema["ordered_names"])
    X, y, weight, split = read_training(args.prepared_root / "train-targets.parquet", feature_count)
    models, tuning = tune_models(X, y, weight, split, schema, config)
    tuning_path = args.output_root / "tuning-report.json"
    tuning_path.write_text(json.dumps(tuning, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for name, model in models.items():
        model.save(args.output_root / name / "model" / "model.npz")

    target_roots = {name: args.output_root / name / "validation-target-predictions.parquet" for name in models}
    candidate_roots = {name: args.output_root / name / "validation-candidate-predictions.parquet" for name in models}
    target_stats = write_predictions(args.prepared_root / "validation-targets.parquet", target_roots, models,
                                     feature_count, config["prediction_bounds"])
    candidate_stats = write_predictions(args.prepared_root / "validation-candidates.parquet", candidate_roots, models,
                                        feature_count, config["prediction_bounds"])
    metrics = {}
    for name, model in models.items():
        root = args.output_root / name
        train_prediction = model.predict(X)
        clipped_train = np.clip(train_prediction, *map(float, config["prediction_bounds"]))
        profile_tuning = (tuning["factor_profiles"].get(name, {}) if name != "sparse_linear_v2" else
                          {"selected_reg": tuning["selected_linear_reg"]})
        document = {
            "schema_version": 2, "status": "PASS", "profile": name,
            "estimator_kind": "WEIGHTED_RIDGE" if name == "sparse_linear_v2" else "WEIGHTED_CROSS_FIELD_FM",
            "interaction_enabled": name != "sparse_linear_v2", "seed": int(config["model_seed"]),
            "runtime_image_id": config["runtime"]["image_id"], "feature_count": feature_count,
            "training_rows": int(X.shape[0]), "training_target_weight": float(weight.sum()),
            "weighted_training_raw_mse": weighted_mse(y, train_prediction, weight),
            "weighted_training_clipped_mse": weighted_mse(y, clipped_train, weight),
            "tuning": profile_tuning, "target_prediction_stats": target_stats[name],
            "candidate_prediction_stats": candidate_stats[name],
            "model_artifact": tree_pin(root / "model"),
            "target_predictions_artifact": tree_pin(root / "validation-target-predictions.parquet"),
            "candidate_predictions_artifact": tree_pin(root / "validation-candidate-predictions.parquet"),
            "prepared_manifest": file_pin(args.prepared_root / "manifest.json"),
            "feature_schema": file_pin(args.prepared_root / "feature-schema.json"),
            "failed_rows": 0, "final_test_opened": False, "gpu_training": False,
        }
        (root / "metrics.json").write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        metrics[name] = document
    run_report = {
        "schema_version": 2, "status": "PASS", "profiles": list(models),
        "elapsed_seconds": time.monotonic() - started, "resource": cgroup_peak(),
        "runtime_image_id": config["runtime"]["image_id"], "tuning_report": file_pin(tuning_path),
        "fixture_promotion": promotion, "final_test_opened": False,
    }
    (args.output_root / "run-report.json").write_text(json.dumps(run_report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(run_report, indent=2))
    return run_report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--promotion-report", type=Path)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
