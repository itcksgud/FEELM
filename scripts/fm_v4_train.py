"""Fit the preregistered FM-v4 additive and signed-content interaction models.

VALIDATION labels are deliberately not an input to this command.  The only labels read here
are the TRAIN labels embedded in ``train-targets.parquet``.
"""

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
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from scipy import sparse

from fm_v2_train import cgroup_peak, file_pin, fit_linear, frame_to_csr, tree_pin, weighted_mse
from fm_v4_prepare import SOURCE_FILES as PREPARE_SOURCE_FILES


PROFILE_ADDITIVE = "matched_additive_v4"
PROFILE_FACTOR = "signed_genre_tag_fm_v4"
FIELDS = ("genre", "tag")
PREDICTION_COLUMNS = (
    "target_key", "episode_id", "uid", "target_movie_id", "prediction_at", "n", "n_bucket"
)
EXPECTED_PREPARED_FILES = {
    "train-targets.parquet", "validation-targets.parquet", "feature-schema.json",
    "split-distribution-report.json",
}
EXPECTED_SOURCE_BUNDLE_FILES = set(PREPARE_SOURCE_FILES)
EXPECTED_PREPARE_MOUNT_CONTRACT = {
    "network": "none", "workspace": "/workspace:ro", "isolated_input": "/input:ro",
    "movies_csv": "/catalog/movies.csv:ro", "tags_csv": "/catalog/tags.csv:ro",
    "output": "/output:rw", "raw_ratings_visible": False,
    "source_input_with_final_test_visible": False, "validation_labels_visible": False,
    "output_parent_or_siblings_visible": False,
}
FIT_MOUNT_CONTRACT = {
    "network": "none", "workspace": "/workspace:ro", "prepared": "/prepared:ro",
    "output": "/output:rw", "output_mount_is_dedicated_empty_directory": True,
    "output_parent_or_siblings_visible": False, "raw_ratings_visible": False,
    "source_input_visible": False, "validation_labels_visible": False,
    "final_test_visible": False,
}


def _optimizer(config: dict) -> dict:
    model = config.get("model", {})
    supplied = model.get("optimizer", config.get("optimizer", {}))
    return {
        "learning_rate": float(supplied.get("learning_rate", model.get("factor_learning_rate", 0.01))),
        "max_epochs": int(supplied.get("max_epochs", 100)),
        "patience": int(supplied.get("patience", 12)),
        "beta1": float(supplied.get("beta1", 0.9)),
        "beta2": float(supplied.get("beta2", 0.999)),
        "epsilon": float(supplied.get("epsilon", 1e-8)),
        "gradient_clip_norm": float(supplied.get("gradient_clip_norm", 10.0)),
    }


def _ridge_grid(config: dict) -> list[float]:
    model = config.get("model", {})
    values = model.get("ridge_reg_grid", config.get("ridge_reg_grid", [1e-4, 1e-3]))
    values = [float(value) for value in values]
    if values != [1e-4, 1e-3]:
        raise RuntimeError(f"FM-v4 ridge grid is fixed at [0.0001, 0.001], got {values}")
    return values


def _interaction_grid(config: dict) -> list[float]:
    model = config.get("model", {})
    values = model.get("factor_reg_grid", config.get("interaction_reg_grid", [1e-3, 1e-2]))
    values = [float(value) for value in values]
    if values != [1e-3, 1e-2]:
        raise RuntimeError(f"FM-v4 interaction grid is fixed at [0.001, 0.01], got {values}")
    return values


def _model_seeds(config: dict) -> list[int]:
    values = [int(value) for value in config.get("model", {}).get(
        "model_seeds", config.get("model_seeds", [623, 1623, 2623])
    )]
    if values != [623, 1623, 2623]:
        raise RuntimeError(f"FM-v4 seeds are fixed, got {values}")
    return values


def _factor_rank(config: dict) -> int:
    value = int(config.get("model", {}).get("factor_rank", config.get("factor_rank", 4)))
    if value != 4:
        raise RuntimeError(f"FM-v4 factor rank is fixed at 4, got {value}")
    return value


def _ordered_file_digest(files: dict) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _schema_digest(document: dict) -> str:
    payload = dict(document)
    claimed = payload.pop("profile_digest", None)
    actual = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if claimed != actual:
        raise RuntimeError("feature schema profile digest mismatch")
    return actual


def validate_prepared_contract(prepared_root: Path, config: dict) -> tuple[dict, dict, dict]:
    """Fail closed on the exact prepared manifest and every artifact it delegates."""
    manifest_path = prepared_root / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError("prepared manifest is required")
    prepared_pin = file_pin(manifest_path)
    configured_pin = config.get("pins", {}).get("prepared_manifest")
    if configured_pin is not None and prepared_pin != configured_pin:
        raise RuntimeError("prepared manifest differs from frozen config pin")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("schema_version") != 4 or manifest.get("status") != "PASS" or
            manifest.get("failed_rows") != 0 or manifest.get("final_test_opened") is not False or
            manifest.get("validation_labels_opened") is not False or
            manifest.get("final_test") != "NOT_PRESENT_IN_INPUT_OR_PREPARED_ARTIFACTS" or
            manifest.get("validation_labels") != "NOT_PRESENT_IN_INPUT_OR_PREPARED_ARTIFACTS"):
        raise RuntimeError("prepared manifest is not a sealed schema-v4 PASS")
    isolated_pin = config.get("pins", {}).get("isolated_manifest")
    if not isolated_pin or manifest.get("input_manifest") != isolated_pin:
        raise RuntimeError("prepared input manifest does not match the frozen isolated manifest")
    input_files = manifest.get("input_files")
    if (not isinstance(input_files, dict) or
            set(input_files) != {"train-episodes.jsonl", "validation-episodes.jsonl"} or
            any(set(value) != {"bytes", "sha256"} for value in input_files.values())):
        raise RuntimeError("prepared isolated-input file pins are incomplete")
    if manifest.get("catalog_files") != {
        "movies.csv": config["source"]["movies"], "tags.csv": config["source"]["tags"]
    }:
        raise RuntimeError("prepared catalog pins differ from frozen config")
    if manifest.get("container_mount_contract") != EXPECTED_PREPARE_MOUNT_CONTRACT:
        raise RuntimeError("prepared mount-isolation contract mismatch")
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != EXPECTED_PREPARED_FILES:
        raise RuntimeError("prepared manifest file inventory is not exact")
    for filename, expected in files.items():
        if file_pin(prepared_root / filename) != expected:
            raise RuntimeError(f"prepared artifact hash mismatch: {filename}")
    source_bundle = manifest.get("source_bundle", {})
    source_files = source_bundle.get("files")
    if (source_bundle.get("algorithm") != "ORDERED_FILE_PINS_V1" or not isinstance(source_files, dict) or
            set(source_files) != EXPECTED_SOURCE_BUNDLE_FILES):
        raise RuntimeError("prepared source bundle contract is absent")
    root = Path(__file__).resolve().parents[1]
    current_sources = {name: file_pin(root / name) for name in source_files}
    if current_sources != source_files or _ordered_file_digest(current_sources) != source_bundle.get("digest"):
        raise RuntimeError("prepared source bundle differs from the executing source tree")
    rows = manifest.get("rows", {})
    expected_rows = {
        "train_targets": pq.ParquetFile(prepared_root / "train-targets.parquet").metadata.num_rows,
        "validation_targets": pq.ParquetFile(prepared_root / "validation-targets.parquet").metadata.num_rows,
    }
    if rows != expected_rows:
        raise RuntimeError("prepared row counts differ from Parquet metadata")
    schema_path = prepared_root / "feature-schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    _schema_digest(schema)
    schema_claim = manifest.get("feature_schema", {})
    exact_schema_claim = {
        "profile_digest": schema["profile_digest"],
        "ordered_names_sha256": schema["ordered_names_sha256"],
        "genre_vocabulary_digest": schema["genre_vocabulary"]["digest"],
        "tag_vocabulary_digest": schema["tag_vocabulary"]["digest"],
        "scaler_digest": schema["scaler"]["digest"],
        "global_rating_mean_digest": schema["global_rating_mean"]["digest"],
    }
    if schema_claim != exact_schema_claim:
        raise RuntimeError("prepared manifest feature-schema claims differ from the schema")
    return manifest, schema, prepared_pin


def _schema_field(schema: dict, field: str) -> dict:
    try:
        item = schema["interaction_fields"][field]
        result = {
            "candidate_indices": np.asarray(item["candidate_indices"], dtype=np.int32),
            "q_indices": np.asarray(item["q_indices"], dtype=np.int32),
            "gate_index": int(item["gate_index"]),
        }
    except (KeyError, TypeError) as exc:
        raise RuntimeError(f"feature schema lacks the FM-v4 {field} interaction layout") from exc
    if len(result["candidate_indices"]) != len(result["q_indices"]):
        raise RuntimeError(f"{field} candidate/Q dimensions differ")
    return result


@dataclass
class AdditiveModel:
    intercept: float
    linear: np.ndarray

    def predict(self, X: sparse.csr_matrix) -> np.ndarray:
        return self.intercept + np.asarray(X @ self.linear).reshape(-1)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, intercept=np.asarray([self.intercept]), linear=self.linear)


@dataclass
class FieldParameters:
    candidate_indices: np.ndarray
    q_indices: np.ndarray
    gate_index: int
    diagonal: np.ndarray
    left: np.ndarray
    right: np.ndarray

    def copy(self) -> "FieldParameters":
        return FieldParameters(
            self.candidate_indices.copy(), self.q_indices.copy(), self.gate_index,
            self.diagonal.copy(), self.left.copy(), self.right.copy(),
        )


@dataclass
class SignedContentModel:
    intercept: float
    linear: np.ndarray
    fields: dict[str, FieldParameters]

    def copy(self) -> "SignedContentModel":
        return SignedContentModel(
            self.intercept, self.linear.copy(), {name: field.copy() for name, field in self.fields.items()}
        )

    def predict(self, X: sparse.csr_matrix) -> np.ndarray:
        prediction = self.intercept + np.asarray(X @ self.linear).reshape(-1)
        for field in self.fields.values():
            prediction += field_contribution(X, field)
        return prediction

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        values: dict[str, np.ndarray] = {
            "intercept": np.asarray([self.intercept]), "linear": self.linear,
        }
        for name, field in self.fields.items():
            values.update({
                f"{name}_candidate_indices": field.candidate_indices,
                f"{name}_q_indices": field.q_indices,
                f"{name}_gate_index": np.asarray([field.gate_index], dtype=np.int32),
                f"{name}_diagonal": field.diagonal,
                f"{name}_left": field.left,
                f"{name}_right": field.right,
            })
        np.savez_compressed(path, **values)


def initialize_interaction(additive: AdditiveModel, schema: dict, seed: int, rank: int = 4) -> SignedContentModel:
    if rank != 4:
        raise RuntimeError("FM-v4 interaction rank is fixed at 4")
    rng = np.random.default_rng(seed)
    fields = {}
    for name in FIELDS:
        layout = _schema_field(schema, name)
        width = len(layout["candidate_indices"])
        # A random left side and zero right side break symmetry while keeping epoch-0 residual exactly zero.
        fields[name] = FieldParameters(
            layout["candidate_indices"], layout["q_indices"], layout["gate_index"],
            np.zeros(width, dtype=np.float64),
            rng.normal(0.0, 0.01, size=(width, rank)),
            np.zeros((width, rank), dtype=np.float64),
        )
    return SignedContentModel(additive.intercept, additive.linear.copy(), fields)


def _field_inputs(X: sparse.csr_matrix, field: FieldParameters) -> tuple[sparse.csr_matrix, sparse.csr_matrix, np.ndarray]:
    candidate = X[:, field.candidate_indices].tocsr()
    q = X[:, field.q_indices].tocsr()
    gate = np.asarray(X[:, field.gate_index].toarray()).reshape(-1)
    return candidate, q, gate


def _field_contribution_from_inputs(candidate: sparse.csr_matrix, q: sparse.csr_matrix,
                                    gate: np.ndarray, field: FieldParameters) -> np.ndarray:
    same = candidate.multiply(q)
    candidate_left = np.asarray(candidate @ field.left)
    q_right = np.asarray(q @ field.right)
    # Remove i==j from the low-rank product exactly; the learned diagonal is the only diagonal path.
    latent_diagonal = np.asarray(same @ np.sum(field.left * field.right, axis=1)).reshape(-1)
    diagonal = np.asarray(same @ field.diagonal).reshape(-1)
    return gate * (diagonal + np.sum(candidate_left * q_right, axis=1) - latent_diagonal)


def field_contribution(X: sparse.csr_matrix, field: FieldParameters) -> np.ndarray:
    return _field_contribution_from_inputs(*_field_inputs(X, field), field)


def interaction_objective_and_gradients(
    model: SignedContentModel, X: sparse.csr_matrix, y: np.ndarray, weight: np.ndarray, reg: float,
) -> tuple[float, dict[str, dict[str, np.ndarray]]]:
    if len(y) == 0 or weight.sum() <= 0:
        raise ValueError("interaction objective requires positive-weight rows")
    base = model.intercept + np.asarray(X @ model.linear).reshape(-1)
    inputs = {name: _field_inputs(X, field) for name, field in model.fields.items()}
    prediction = base.copy()
    for name, field in model.fields.items():
        prediction += _field_contribution_from_inputs(*inputs[name], field)
    error_gradient = (2.0 * weight / weight.sum()) * (prediction - y)
    gradients: dict[str, dict[str, np.ndarray]] = {}
    penalty = 0.0
    for name, field in model.fields.items():
        candidate, q, gate = inputs[name]
        scaled_error = error_gradient * gate
        overlap = np.asarray(candidate.multiply(q).T @ scaled_error).reshape(-1)
        candidate_left = np.asarray(candidate @ field.left)
        q_right = np.asarray(q @ field.right)
        grad_left = np.asarray(candidate.multiply(scaled_error[:, None]).T @ q_right)
        grad_left -= overlap[:, None] * field.right
        grad_right = np.asarray(q.T @ (scaled_error[:, None] * candidate_left))
        grad_right -= overlap[:, None] * field.left
        gradients[name] = {
            "diagonal": overlap + 2.0 * reg * field.diagonal,
            "left": grad_left + 2.0 * reg * field.left,
            "right": grad_right + 2.0 * reg * field.right,
        }
        penalty += float(np.square(field.diagonal).sum() + np.square(field.left).sum() +
                         np.square(field.right).sum())
    return weighted_mse(y, prediction, weight) + reg * penalty, gradients


def positive_n_user_macro_mse(metadata: pd.DataFrame, actual: np.ndarray, prediction: np.ndarray) -> float:
    if len(metadata) != len(actual) or len(actual) != len(prediction):
        raise ValueError("metadata and prediction lengths differ")
    frame = metadata[["uid", "target_key", "n"]].copy()
    frame["squared_error"] = np.square(prediction - actual)
    frame = frame[frame.n > 0]
    if frame.empty:
        raise ValueError("selection cohort has no N>0 rows")
    targets = frame.groupby(["uid", "target_key"], sort=True).squared_error.mean()
    return float(targets.groupby(level="uid", sort=True).mean().mean())


def fit_interaction_only(
    X_fit: sparse.csr_matrix, y_fit: np.ndarray, weight_fit: np.ndarray,
    X_select: sparse.csr_matrix, y_select: np.ndarray, select_metadata: pd.DataFrame,
    initial: SignedContentModel, reg: float, optimizer: dict,
    max_epochs: int | None = None, restore_best: bool = True,
) -> tuple[SignedContentModel, dict]:
    model = initial.copy()
    frozen_intercept, frozen_linear = model.intercept, model.linear.copy()
    names = tuple((field, parameter) for field in FIELDS for parameter in ("diagonal", "left", "right"))
    first = {(field, parameter): np.zeros_like(getattr(model.fields[field], parameter))
             for field, parameter in names}
    second = {key: np.zeros_like(value) for key, value in first.items()}
    beta1, beta2 = optimizer["beta1"], optimizer["beta2"]
    epsilon, learning_rate = optimizer["epsilon"], optimizer["learning_rate"]
    epochs = int(max_epochs if max_epochs is not None else optimizer["max_epochs"])
    best = model.copy()
    best_value = positive_n_user_macro_mse(select_metadata, y_select, model.predict(X_select))
    best_epoch = 0
    wait = 0
    history = [{"epoch": 0, "fit_weighted_mse": weighted_mse(y_fit, model.predict(X_fit), weight_fit),
                "selection_positive_n_user_macro_mse": best_value}]
    for epoch in range(1, epochs + 1):
        _, gradients = interaction_objective_and_gradients(model, X_fit, y_fit, weight_fit, reg)
        norm = math.sqrt(sum(float(np.square(gradients[field][parameter]).sum())
                             for field, parameter in names))
        scale = min(1.0, optimizer["gradient_clip_norm"] / max(norm, 1e-15))
        for field_name, parameter in names:
            key = (field_name, parameter)
            gradient = gradients[field_name][parameter] * scale
            first[key] = beta1 * first[key] + (1.0 - beta1) * gradient
            second[key] = beta2 * second[key] + (1.0 - beta2) * np.square(gradient)
            update = ((first[key] / (1.0 - beta1 ** epoch)) /
                      (np.sqrt(second[key] / (1.0 - beta2 ** epoch)) + epsilon))
            setattr(model.fields[field_name], parameter,
                    getattr(model.fields[field_name], parameter) - learning_rate * update)
        if model.intercept != frozen_intercept or not np.array_equal(model.linear, frozen_linear):
            raise RuntimeError("interaction optimizer mutated frozen additive parameters")
        selection = positive_n_user_macro_mse(select_metadata, y_select, model.predict(X_select))
        history.append({
            "epoch": epoch,
            "fit_weighted_mse": weighted_mse(y_fit, model.predict(X_fit), weight_fit),
            "selection_positive_n_user_macro_mse": selection,
            "gradient_norm": norm,
            "gradient_clip_scale": scale,
        })
        if selection < best_value - 1e-12:
            best_value, best, best_epoch, wait = selection, model.copy(), epoch, 0
        else:
            wait += 1
        if restore_best and wait >= optimizer["patience"]:
            break
    selected = best if restore_best else model
    return selected, {
        "best_epoch": best_epoch,
        "best_selection_positive_n_user_macro_mse": best_value,
        "epochs_executed": len(history) - 1,
        "returned_epoch": best_epoch if restore_best else len(history) - 1,
        "history": history,
    }


def _read_training(path: Path, feature_count: int) -> tuple[sparse.csr_matrix, np.ndarray, np.ndarray,
                                                                  np.ndarray, np.ndarray, pd.DataFrame]:
    columns = ["target_key", "uid", "n", "internal_split", "label", "weight_additive",
               "weight_interaction", "feature_indices", "feature_values", "feature_size"]
    table = pq.read_table(path, columns=columns)
    sizes = np.asarray(table["feature_size"].to_pylist(), dtype=np.int64)
    if len(sizes) == 0 or not np.all(sizes == feature_count):
        raise RuntimeError("row feature_size does not match feature schema")
    matrix = frame_to_csr(table, feature_count)
    if not np.isfinite(matrix.data).all():
        raise RuntimeError("training features contain NaN/Inf")
    labels = table["label"].to_numpy(zero_copy_only=False).astype(np.float64)
    additive_weight = table["weight_additive"].to_numpy(zero_copy_only=False).astype(np.float64)
    interaction_weight = table["weight_interaction"].to_numpy(zero_copy_only=False).astype(np.float64)
    split = np.asarray(table["internal_split"].to_pylist())
    metadata = table.select(["uid", "target_key", "n"]).to_pandas()
    if not np.isfinite(labels).all() or np.any(additive_weight <= 0) or np.any(interaction_weight < 0):
        raise RuntimeError("invalid label or training weight")
    if set(np.unique(split)) != {"FIT", "HOLDOUT"}:
        raise RuntimeError("inner split must contain FIT and HOLDOUT")
    if np.any((metadata.n.to_numpy() == 0) & (interaction_weight != 0)):
        raise RuntimeError("N=0 rows must have zero interaction weight")
    if np.any((metadata.n.to_numpy() > 0) & (interaction_weight <= 0)):
        raise RuntimeError("positive-N rows must have positive interaction weight")
    return matrix, labels, additive_weight, interaction_weight, split, metadata


def tune_and_refit(X: sparse.csr_matrix, y: np.ndarray, additive_weight: np.ndarray,
                   interaction_weight: np.ndarray, split: np.ndarray, metadata: pd.DataFrame,
                   schema: dict, config: dict) -> tuple[AdditiveModel, list[SignedContentModel], dict]:
    fit_mask, holdout_mask = split == "FIT", split == "HOLDOUT"
    positive = metadata.n.to_numpy() > 0
    if not np.any(fit_mask & positive) or not np.any(holdout_mask & positive):
        raise RuntimeError("both inner partitions require positive-N rows")
    ridge_trials = []
    for reg in _ridge_grid(config):
        intercept, linear, solver = fit_linear(X[fit_mask], y[fit_mask], additive_weight[fit_mask], reg)
        prediction = intercept + np.asarray(X[holdout_mask] @ linear).reshape(-1)
        score = positive_n_user_macro_mse(metadata.loc[holdout_mask].reset_index(drop=True),
                                          y[holdout_mask], prediction)
        ridge_trials.append({"reg": reg, "holdout_positive_n_user_macro_mse": score,
                             "solver": solver, "intercept": intercept, "linear": linear})
    selected_ridge = min(ridge_trials, key=lambda item: (item["holdout_positive_n_user_macro_mse"], item["reg"]))
    initial_additive = AdditiveModel(selected_ridge["intercept"], selected_ridge["linear"])
    optimizer = _optimizer(config)
    seeds = _model_seeds(config)
    factor_trials = []
    fit_positive = fit_mask & positive
    holdout_positive = holdout_mask & positive
    holdout_metadata = metadata.loc[holdout_positive].reset_index(drop=True)
    for reg in _interaction_grid(config):
        seed_trials = []
        for seed in seeds:
            initial = initialize_interaction(initial_additive, schema, seed, rank=_factor_rank(config))
            model, trace = fit_interaction_only(
                X[fit_positive], y[fit_positive], interaction_weight[fit_positive],
                X[holdout_positive], y[holdout_positive], holdout_metadata,
                initial, reg, optimizer,
            )
            seed_trials.append({"seed": seed, "trace": trace, "model": model})
        factor_trials.append({
            "reg": reg,
            "mean_holdout_positive_n_user_macro_mse": float(np.mean([
                item["trace"]["best_selection_positive_n_user_macro_mse"] for item in seed_trials
            ])),
            "seed_trials": seed_trials,
        })
    selected_factor = min(factor_trials, key=lambda item: (item["mean_holdout_positive_n_user_macro_mse"], item["reg"]))

    final_intercept, final_linear, final_solver = fit_linear(
        X, y, additive_weight, float(selected_ridge["reg"])
    )
    final_additive = AdditiveModel(final_intercept, final_linear)
    final_members = []
    refits = []
    for item in selected_factor["seed_trials"]:
        seed, epochs = int(item["seed"]), int(item["trace"]["best_epoch"])
        initial = initialize_interaction(final_additive, schema, seed, rank=_factor_rank(config))
        model, trace = fit_interaction_only(
            X[positive], y[positive], interaction_weight[positive],
            X[positive], y[positive], metadata.loc[positive].reset_index(drop=True),
            initial, float(selected_factor["reg"]), optimizer,
            max_epochs=epochs, restore_best=False,
        )
        if model.intercept != final_additive.intercept or not np.array_equal(model.linear, final_additive.linear):
            raise RuntimeError("final interaction member does not bitwise-share additive parameters")
        final_members.append(model)
        refits.append({"seed": seed, "selected_epoch": epochs, "trace": trace})
    tuning = {
        "model_contract": config["model"],
        "selection_metric": "TRAIN_INNER_HOLDOUT_POSITIVE_N_HIERARCHICAL_USER_MACRO_MSE",
        "internal_fit_rows": int(fit_mask.sum()),
        "internal_holdout_rows": int(holdout_mask.sum()),
        "ridge_trials": [{key: value for key, value in item.items() if key not in {"intercept", "linear"}}
                         for item in ridge_trials],
        "selected_ridge_reg": selected_ridge["reg"],
        "interaction_trials": [{
            "reg": item["reg"],
            "mean_holdout_positive_n_user_macro_mse": item["mean_holdout_positive_n_user_macro_mse"],
            "seed_trials": [{"seed": seed_item["seed"], "trace": seed_item["trace"]}
                            for seed_item in item["seed_trials"]],
        } for item in factor_trials],
        "selected_interaction_reg": selected_factor["reg"],
        "selected_interaction_mean_holdout_positive_n_user_macro_mse":
            selected_factor["mean_holdout_positive_n_user_macro_mse"],
        "selected_epochs_by_seed": {str(item["seed"]): int(item["trace"]["best_epoch"])
                                    for item in selected_factor["seed_trials"]},
        "final_ridge_solver": final_solver,
        "final_interaction_refits": refits,
        "rank": 4,
        "additive_parameters_frozen_and_shared_bitwise": True,
        "validation_labels_read": False,
        "train_only_mechanism_diagnostics": {
            "status": "EXCLUDED_FROM_EXECUTABLE_PROTOCOL",
            "profiles": [],
            "reason": "only the frozen primary comparison is executable; diagnostics cannot override it",
        },
    }
    return final_additive, final_members, tuning


def _ensemble_predict(members: list[SignedContentModel], X: sparse.csr_matrix) -> np.ndarray:
    return np.mean(np.vstack([member.predict(X) for member in members]), axis=0)


def write_predictions(input_path: Path, output_path: Path, predictor, feature_count: int,
                      bounds: list[float], batch_size: int = 100_000) -> dict:
    parquet = pq.ParquetFile(input_path)
    exposed_labels = {"label", "target_rating"} & set(parquet.schema_arrow.names)
    if exposed_labels:
        raise RuntimeError(f"label firewall violation: validation feature input contains {sorted(exposed_labels)}")
    columns = [name for name in PREDICTION_COLUMNS if name in parquet.schema_arrow.names]
    if "target_key" not in columns or "uid" not in columns or "n" not in columns:
        raise RuntimeError("validation feature input lacks prediction identity columns")
    writer = None
    rows = clipped = 0
    raw_min, raw_max = float("inf"), float("-inf")
    try:
        for batch in parquet.iter_batches(batch_size=batch_size):
            table = pa.Table.from_batches([batch])
            sizes = np.asarray(table["feature_size"].to_pylist(), dtype=np.int64)
            if not np.all(sizes == feature_count):
                raise RuntimeError("validation row feature_size does not match schema")
            X = frame_to_csr(table, feature_count)
            raw = np.asarray(predictor(X), dtype=np.float64)
            if not np.isfinite(raw).all():
                raise RuntimeError("model produced NaN/Inf")
            prediction = np.clip(raw, float(bounds[0]), float(bounds[1]))
            clipped += int(np.sum(prediction != raw))
            raw_min, raw_max = min(raw_min, float(raw.min())), max(raw_max, float(raw.max()))
            output = table.select(columns)
            output = output.append_column("raw_prediction", pa.array(raw, type=pa.float64()))
            output = output.append_column("prediction", pa.array(prediction, type=pa.float64()))
            if writer is None:
                output_path.mkdir(parents=True, exist_ok=False)
                writer = pq.ParquetWriter(output_path / "part-00000.parquet", output.schema, compression="zstd")
            writer.write_table(output)
            rows += len(output)
    finally:
        if writer is not None:
            writer.close()
    if writer is None:
        raise RuntimeError("validation feature input is empty")
    return {"rows": rows, "raw_min": raw_min, "raw_max": raw_max,
            "clipped_rows": clipped, "clipped_fraction": clipped / rows}


def run(args: argparse.Namespace) -> dict:
    started = time.monotonic()
    if os.environ.get("FM_OUTPUT_MOUNT_ISOLATED") != "true":
        raise RuntimeError("FM-v4 requires an isolated output mount")
    if args.prepared_root.as_posix() != "/prepared" or args.output_root.as_posix() != "/output":
        raise RuntimeError("FM-v4 fitting requires the audited /prepared:ro and /output:rw mount paths")
    if not args.output_root.is_dir() or any(args.output_root.iterdir()):
        raise FileExistsError(f"output must be an existing empty isolated mount: {args.output_root}")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if config.get("status") != "PREREGISTERED_REVIEWED":
        raise RuntimeError("FM-v4 config must be preregistered and independently reviewed before fitting")
    if config.get("model", {}).get("profiles") != [PROFILE_ADDITIVE, PROFILE_FACTOR]:
        raise RuntimeError("FM-v4 primary profile order/configuration mismatch")
    if (args.prepared_root / "validation-labels.jsonl").exists():
        raise RuntimeError("label firewall violation: validation labels are visible to fitting")
    manifest, schema, prepared_pin = validate_prepared_contract(args.prepared_root, config)
    manifest_path = args.prepared_root / "manifest.json"
    schema_path = args.prepared_root / "feature-schema.json"
    feature_count = len(schema["ordered_names"])
    linear_indices = schema.get("profiles", {}).get(PROFILE_ADDITIVE, {}).get("linear_indices")
    if linear_indices is not None and list(map(int, linear_indices)) != list(range(feature_count)):
        raise RuntimeError("matched additive schema must cover every ordered feature exactly once")
    runtime_id = config.get("runtime", {}).get("image_id")
    if runtime_id and os.environ.get("FM_RUNTIME_IMAGE_ID") != runtime_id:
        raise RuntimeError("runtime image ID mismatch")
    X, y, additive_weight, interaction_weight, split, metadata = _read_training(
        args.prepared_root / "train-targets.parquet", feature_count
    )
    additive, members, tuning = tune_and_refit(
        X, y, additive_weight, interaction_weight, split, metadata, schema, config
    )
    tuning_path = args.output_root / "tuning-report.json"
    tuning_path.write_text(json.dumps(tuning, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tuning_pin = file_pin(tuning_path)
    additive_root = args.output_root / PROFILE_ADDITIVE
    factor_root = args.output_root / PROFILE_FACTOR
    additive.save(additive_root / "model" / "model.npz")
    for seed, member in zip(_model_seeds(config), members):
        member.save(factor_root / "model" / f"seed-{seed}.npz")
    bounds = [float(value) for value in config.get("prediction_bounds", [0.5, 5.0])]
    validation_path = args.prepared_root / "validation-targets.parquet"
    additive_stats = write_predictions(validation_path, additive_root / "validation-target-predictions.parquet",
                                       additive.predict, feature_count, bounds)
    factor_stats = write_predictions(validation_path, factor_root / "validation-target-predictions.parquet",
                                     lambda values: _ensemble_predict(members, values), feature_count, bounds)
    seed_stats = {}
    for seed, member in zip(_model_seeds(config), members):
        seed_stats[str(seed)] = write_predictions(
            validation_path, factor_root / "diagnostic-seed-target-predictions" / f"seed-{seed}.parquet",
            member.predict, feature_count, bounds,
        )
    additive_train = additive.predict(X)
    factor_train = _ensemble_predict(members, X)
    contribution = factor_train - additive_train
    common = {
        "schema_version": 4, "status": "PASS", "runtime_image_id": runtime_id,
        "feature_count": feature_count, "training_rows": len(y),
        "config": file_pin(args.config), "profiles": [PROFILE_ADDITIVE, PROFILE_FACTOR],
        "prepared_manifest": prepared_pin, "prepared_input_manifest": manifest["input_manifest"],
        "prepared_source_bundle_digest": manifest["source_bundle"]["digest"],
        "feature_schema": file_pin(schema_path), "tuning_report": tuning_pin,
        "container_mount_contract": FIT_MOUNT_CONTRACT,
        "final_test_opened": False,
        "validation_labels_read": False,
    }
    additive_metrics = common | {
        "profile": PROFILE_ADDITIVE, "estimator_kind": "WEIGHTED_RIDGE",
        "interaction_enabled": False, "selected_ridge_reg": tuning["selected_ridge_reg"],
        "weighted_training_mse": weighted_mse(y, additive_train, additive_weight),
        "target_prediction_stats": additive_stats,
        "model_artifact": tree_pin(additive_root / "model"),
        "target_predictions_artifact": tree_pin(additive_root / "validation-target-predictions.parquet"),
    }
    factor_metrics = common | {
        "profile": PROFILE_FACTOR,
        "estimator_kind": "FROZEN_ADDITIVE_DIAGONAL_ASYMMETRIC_RANK4_GENRE_TAG_SEED_MEAN",
        "interaction_enabled": True, "rank": 4, "model_seeds": _model_seeds(config),
        "selected_interaction_reg": tuning["selected_interaction_reg"],
        "selected_epochs_by_seed": tuning["selected_epochs_by_seed"],
        "additive_parameters_frozen_and_shared_bitwise": True,
        "weighted_positive_n_training_mse": weighted_mse(
            y[metadata.n.to_numpy() > 0], factor_train[metadata.n.to_numpy() > 0],
            interaction_weight[metadata.n.to_numpy() > 0],
        ),
        "factor_contribution": {
            "mean": float(contribution.mean()), "mean_absolute": float(np.abs(contribution).mean()),
            "maximum_absolute": float(np.abs(contribution).max()),
            "n0_maximum_absolute": float(np.abs(contribution[metadata.n.to_numpy() == 0]).max(initial=0.0)),
        },
        "target_prediction_stats": factor_stats, "seed_target_prediction_stats": seed_stats,
        "model_artifact": tree_pin(factor_root / "model"),
        "target_predictions_artifact": tree_pin(factor_root / "validation-target-predictions.parquet"),
        "seed_target_predictions_artifact": tree_pin(factor_root / "diagnostic-seed-target-predictions"),
    }
    (additive_root / "metrics.json").write_text(
        json.dumps(additive_metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (factor_root / "metrics.json").write_text(
        json.dumps(factor_metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    report = {
        "schema_version": 4, "status": "PASS", "profiles": [PROFILE_ADDITIVE, PROFILE_FACTOR],
        "elapsed_seconds": time.monotonic() - started, "resource": cgroup_peak(),
        "runtime_image_id": runtime_id, "config": file_pin(args.config),
        "prepared_manifest": prepared_pin, "prepared_input_manifest": manifest["input_manifest"],
        "prepared_source_bundle_digest": manifest["source_bundle"]["digest"],
        "feature_schema": file_pin(schema_path), "tuning_report": tuning_pin,
        "container_mount_contract": FIT_MOUNT_CONTRACT,
        "validation_labels_read": False, "final_test_opened": False,
        "train_only_mechanism_diagnostics": tuning["train_only_mechanism_diagnostics"],
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
