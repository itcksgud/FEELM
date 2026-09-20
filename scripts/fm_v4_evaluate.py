"""Evaluate frozen FM-v4 predictions after opening the separately mounted labels.

This command cannot fit or select a model.  It hashes and validates both prediction
artifacts before reading ``validation-labels.jsonl``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from fm_v2_train import file_pin, tree_pin
from fm_v4_train import PROFILE_ADDITIVE, PROFILE_FACTOR


def calculator_bundle() -> dict:
    root = Path(__file__).resolve().parents[1]
    files = {
        "scripts/fm_v4_evaluate.py": file_pin(root / "scripts/fm_v4_evaluate.py"),
        "scripts/fm_v4_train.py": file_pin(root / "scripts/fm_v4_train.py"),
    }
    return {
        "algorithm": "ORDERED_FILE_PINS_V1", "files": files,
        "sha256": hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
    }


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _prediction_root(fits_root: Path, profile: str) -> Path:
    return fits_root / profile / "validation-target-predictions.parquet"


def _pin_frozen_predictions(fits_root: Path) -> dict[str, dict]:
    pins = {}
    for profile in (PROFILE_ADDITIVE, PROFILE_FACTOR):
        root = fits_root / profile
        metrics_path = root / "metrics.json"
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        if (metrics.get("status") != "PASS" or metrics.get("profile") != profile or
                metrics.get("validation_labels_read") is not False or metrics.get("final_test_opened") is not False):
            raise RuntimeError(f"unsealed fit metrics: {profile}")
        actual = tree_pin(_prediction_root(fits_root, profile))
        if actual != metrics.get("target_predictions_artifact"):
            raise RuntimeError(f"frozen prediction artifact mismatch: {profile}")
        pins[profile] = {"metrics": file_pin(metrics_path), "target_predictions": actual}
    return pins


def _read_prediction(path: Path, suffix: str) -> pd.DataFrame:
    frame = pd.read_parquet(path)
    if {"label", "target_rating"} & set(frame.columns):
        raise RuntimeError("label firewall violation: frozen predictions contain a label")
    required = {"target_key", "uid", "n", "raw_prediction", "prediction"}
    if not required.issubset(frame.columns):
        raise RuntimeError(f"prediction artifact lacks {sorted(required - set(frame.columns))}")
    identity = ["target_key", "uid", "n"]
    if "episode_id" in frame.columns:
        identity.append("episode_id")
    if frame.duplicated(identity).any():
        raise RuntimeError("prediction row identity is not unique")
    if frame[["raw_prediction", "prediction"]].isna().any().any():
        raise RuntimeError("prediction artifact contains null")
    if not np.isfinite(frame[["raw_prediction", "prediction"]].to_numpy(float)).all():
        raise RuntimeError("prediction artifact contains NaN/Inf")
    return frame.rename(columns={
        "raw_prediction": f"raw_prediction_{suffix}", "prediction": f"prediction_{suffix}",
    })


def _read_labels(path: Path) -> pd.DataFrame:
    labels = pd.read_json(path, lines=True)
    required = {"target_key", "target_rating"}
    if not required.issubset(labels.columns):
        raise RuntimeError("validation label artifact requires exactly target_key and target_rating")
    if set(labels.columns) != required:
        raise RuntimeError("validation label artifact must contain only target_key and target_rating")
    labels = labels[["target_key", "target_rating"]].rename(columns={"target_rating": "label"})
    if labels.target_key.duplicated().any():
        raise RuntimeError("validation labels are not unique by target_key")
    if labels.label.isna().any() or not np.isfinite(labels.label.to_numpy(float)).all():
        raise RuntimeError("validation labels contain null/NaN/Inf")
    return labels


def join_frozen_predictions_and_labels(fits_root: Path, labels_path: Path) -> tuple[pd.DataFrame, dict]:
    if _within(labels_path, fits_root):
        raise RuntimeError("label firewall violation: labels must be mounted outside the fits root")
    pins = _pin_frozen_predictions(fits_root)  # This must complete before labels are opened.
    additive = _read_prediction(_prediction_root(fits_root, PROFILE_ADDITIVE), "additive")
    factor = _read_prediction(_prediction_root(fits_root, PROFILE_FACTOR), "factor")
    identity = ["target_key", "uid", "n"]
    if "episode_id" in additive.columns and "episode_id" in factor.columns:
        identity.append("episode_id")
    passthrough = [name for name in ("n_bucket", "prediction_at", "target_movie_id")
                   if name in additive.columns and name in factor.columns]
    merged = factor[identity + passthrough + ["raw_prediction_factor", "prediction_factor"]].merge(
        additive[identity + ["raw_prediction_additive", "prediction_additive"]],
        on=identity, how="inner", validate="one_to_one",
    )
    if len(merged) != len(additive) or len(merged) != len(factor):
        raise RuntimeError("additive and challenger prediction row sets differ")
    label_pin = file_pin(labels_path)
    labels = _read_labels(labels_path)
    if set(labels.target_key) != set(merged.target_key):
        missing = len(set(merged.target_key) - set(labels.target_key))
        extra = len(set(labels.target_key) - set(merged.target_key))
        raise RuntimeError(f"label target-key set differs: missing={missing}, extra={extra}")
    merged = merged.merge(labels, on="target_key", validate="many_to_one")
    if len(merged) != len(additive):
        raise RuntimeError("label join lost prediction rows")
    return merged, {"predictions": pins, "validation_labels": label_pin}


def _target_values(frame: pd.DataFrame, value_column: str) -> pd.DataFrame:
    return frame.groupby(["uid", "target_key"], sort=True, as_index=False)[value_column].mean()


def hierarchical_point(frame: pd.DataFrame, value_column: str) -> float:
    targets = _target_values(frame, value_column)
    return float(targets.groupby("uid", sort=True)[value_column].mean().mean())


def hierarchical_bootstrap(
    frame: pd.DataFrame, value_column: str, seed: int, replicates: int,
    quantiles: tuple[float, ...] = (0.025, 0.975),
) -> list[float] | str:
    """Bootstrap users, then targets within each sampled user, retaining row-variant means."""
    targets = _target_values(frame, value_column)
    grouped = [group[value_column].to_numpy(float) for _, group in targets.groupby("uid", sort=True)]
    if len(grouped) < 2:
        return "INSUFFICIENT_SAMPLE"
    width = max(len(values) for values in grouped)
    values = np.zeros((len(grouped), width), dtype=np.float64)
    counts = np.asarray([len(item) for item in grouped], dtype=np.int64)
    for index, item in enumerate(grouped):
        values[index, :len(item)] = item
    rng = np.random.default_rng(seed)
    output = np.empty(replicates, dtype=np.float64)
    chunk_size = 32
    draw_axis = np.arange(width)[None, None, :]
    for start in range(0, replicates, chunk_size):
        size = min(chunk_size, replicates - start)
        sampled_users = rng.integers(0, len(grouped), size=(size, len(grouped)))
        sampled_counts = counts[sampled_users]
        positions = (rng.random((size, len(grouped), width)) * sampled_counts[:, :, None]).astype(np.int64)
        sampled_values = values[sampled_users[:, :, None], positions]
        target_mask = draw_axis < sampled_counts[:, :, None]
        user_means = (sampled_values * target_mask).sum(axis=2) / sampled_counts
        output[start:start + size] = user_means.mean(axis=1)
    return [float(value) for value in np.quantile(output, quantiles)]


def _comparison_columns(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["squared_error_additive"] = np.square(result.prediction_additive - result.label)
    result["squared_error_factor"] = np.square(result.prediction_factor - result.label)
    result["mse_delta"] = result.squared_error_factor - result.squared_error_additive
    result["absolute_error_additive"] = np.abs(result.prediction_additive - result.label)
    result["absolute_error_factor"] = np.abs(result.prediction_factor - result.label)
    result["mae_delta"] = result.absolute_error_factor - result.absolute_error_additive
    return result


def evaluate_frame(frame: pd.DataFrame, config: dict) -> dict:
    primary_config = config["primary"]
    seed = int(primary_config["bootstrap_seed"])
    replicates = int(primary_config["bootstrap_replicates"])
    tolerance = float(primary_config["tie_tolerance"])
    bounds = [float(value) for value in config.get("prediction_bounds", [0.5, 5.0])]
    frame = _comparison_columns(frame)
    positive = frame[frame.n > 0].copy()
    if positive.empty:
        raise RuntimeError("primary validation cohort has no positive-N rows")
    additive_mse = hierarchical_point(positive, "squared_error_additive")
    factor_mse = hierarchical_point(positive, "squared_error_factor")
    delta = hierarchical_point(positive, "mse_delta")
    ci = hierarchical_bootstrap(positive, "mse_delta", seed, replicates)
    if isinstance(ci, str):
        raise RuntimeError("primary validation cohort is too small for bootstrap")
    relative_improvement = -delta / additive_mse
    statistically_positive = ci[1] < -tolerance
    materially_positive = relative_improvement + tolerance >= float(
        primary_config["minimum_relative_mse_improvement"]
    )
    if not statistically_positive:
        primary_outcome = "INCONCLUSIVE"
    elif not materially_positive:
        primary_outcome = "STATISTICALLY_POSITIVE_BUT_IMMATERIAL"
    else:
        primary_outcome = "SUPPORT_SIGNED_CONTENT_INTERACTION"

    n0 = frame[frame.n == 0]
    n0_raw_difference = float(np.abs(n0.raw_prediction_factor - n0.raw_prediction_additive).max()) if len(n0) else 0.0
    n0_difference = float(np.abs(n0.prediction_factor - n0.prediction_additive).max()) if len(n0) else 0.0
    n0_pass = max(n0_raw_difference, n0_difference) <= tolerance
    n_safety = []
    n_cells_pass = True
    for index, (n_value, cell) in enumerate(positive.groupby("n", sort=True)):
        users = int(cell.uid.nunique())
        item = {"n": int(n_value), "rows": len(cell), "users": users,
                "user_macro_mse_delta": hierarchical_point(cell, "mse_delta")}
        if users >= int(primary_config["n_cell_minimum_users"]):
            upper = hierarchical_bootstrap(cell, "mse_delta", seed + 100 + index, replicates, (0.95,))
            if isinstance(upper, str):
                raise RuntimeError("eligible N safety cell is too small for bootstrap")
            item["one_sided_95_upper"] = upper[0]
            item["limit"] = float(primary_config["n_cell_mse_delta_upper_limit"])
            item["pass"] = upper[0] < item["limit"] + tolerance
            n_cells_pass = n_cells_pass and item["pass"]
        else:
            item["one_sided_95_upper"] = "NOT_EVALUATED_BELOW_MINIMUM_USERS"
            item["pass"] = True
        n_safety.append(item)
    mae_delta = hierarchical_point(positive, "mae_delta")
    mae_upper_result = hierarchical_bootstrap(positive, "mae_delta", seed + 200, replicates, (0.95,))
    if isinstance(mae_upper_result, str):
        raise RuntimeError("MAE safety cohort is too small for bootstrap")
    mae_upper = mae_upper_result[0]
    mae_pass = mae_upper < float(primary_config["mae_delta_upper_limit"]) + tolerance
    additive_support = float(np.isfinite(frame.prediction_additive).mean())
    factor_support = float(np.isfinite(frame.prediction_factor).mean())
    support_pass = factor_support + tolerance >= additive_support
    additive_clipped = float(np.mean((frame.raw_prediction_additive < bounds[0]) |
                                     (frame.raw_prediction_additive > bounds[1])))
    factor_clipped = float(np.mean((frame.raw_prediction_factor < bounds[0]) |
                                   (frame.raw_prediction_factor > bounds[1])))
    clipping_increase = factor_clipped - additive_clipped
    clipping_pass = clipping_increase <= float(primary_config["maximum_clipping_fraction_increase"]) + tolerance
    safety_pass = n0_pass and n_cells_pass and mae_pass and support_pass and clipping_pass
    retention = ("RETAIN_SIGNED_CONTENT_INTERACTION" if
                 primary_outcome == "SUPPORT_SIGNED_CONTENT_INTERACTION" and safety_pass else
                 "KEEP_MATCHED_ADDITIVE_BASELINE")
    return {
        "primary": {
            "cohort": "ALL_FRESH_VALIDATION_USERS_WITH_AT_LEAST_ONE_N_POSITIVE_TARGET",
            "rows": len(positive), "users": int(positive.uid.nunique()),
            "targets": int(positive.target_key.nunique()),
            "additive_user_macro_mse": additive_mse,
            "factor_user_macro_mse": factor_mse,
            "user_macro_mse_delta": delta,
            "relative_mse_improvement": relative_improvement,
            "hierarchical_bootstrap_95_ci": ci,
            "bootstrap_replicates": replicates, "bootstrap_seed": seed,
            "minimum_relative_mse_improvement": float(primary_config["minimum_relative_mse_improvement"]),
            "outcome": primary_outcome,
        },
        "safety": {
            "pass": safety_pass,
            "n0": {"rows": len(n0), "raw_maximum_absolute_difference": n0_raw_difference,
                   "clipped_maximum_absolute_difference": n0_difference,
                   "tolerance": tolerance, "pass": n0_pass},
            "n_cells": n_safety,
            "overall_positive_n_mae": {"user_macro_delta": mae_delta, "one_sided_95_upper": mae_upper,
                                       "limit": float(primary_config["mae_delta_upper_limit"]), "pass": mae_pass},
            "support": {"additive": additive_support, "factor": factor_support, "pass": support_pass},
            "clipping": {"additive_fraction": additive_clipped, "factor_fraction": factor_clipped,
                         "increase": clipping_increase,
                         "maximum_increase": float(primary_config["maximum_clipping_fraction_increase"]),
                         "pass": clipping_pass},
        },
        "retention_decision": retention,
    }


def evaluate(args: argparse.Namespace) -> dict:
    if args.output.exists():
        raise FileExistsError(f"output already exists: {args.output}")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if config.get("status") != "PREREGISTERED_REVIEWED":
        raise RuntimeError("FM-v4 evaluation requires a preregistered reviewed config")
    if os.environ.get("FM_RUNTIME_IMAGE_ID") != config["runtime"]["image_id"]:
        raise RuntimeError("evaluation runtime image ID mismatch")
    run_report = json.loads((args.fits_root / "run-report.json").read_text(encoding="utf-8"))
    if (run_report.get("status") != "PASS" or
            run_report.get("profiles") != config["model"]["profiles"] or
            run_report.get("config") != file_pin(args.config) or
            run_report.get("runtime_image_id") != config["runtime"]["image_id"] or
            run_report.get("validation_labels_read") is not False or
            run_report.get("final_test_opened") is not False):
        raise RuntimeError("fit run report does not match the frozen evaluation contract")
    frame, artifacts = join_frozen_predictions_and_labels(args.fits_root, args.validation_labels)
    results = evaluate_frame(frame, config)
    report = {
        "schema_version": 4, "status": "PASS", "config": file_pin(args.config),
        "calculator": calculator_bundle(), "artifacts": artifacts, **results,
        "validation_was_not_used_for_fitting_or_model_selection": True,
        "labels_opened_only_after_prediction_hash_verification": True,
        "final_test_opened": False,
        "movie_lens_evidence": "WEAK_OFFLINE_EVIDENCE_FOR_A_DIFFERENT_POPULATION",
        "user_evaluation_authorized": False,
        "train_only_mechanism_diagnostics": {
            "status": "EXCLUDED_FROM_EXECUTABLE_PROTOCOL", "profiles": [],
            "cannot_override_primary": True,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fits-root", type=Path, required=True)
    parser.add_argument("--validation-labels", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    evaluate(parser.parse_args())


if __name__ == "__main__":
    main()
