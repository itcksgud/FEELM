"""Fail-closed verifier for FM-v3 lineage, attribution, and predictions."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from fm_v3_evaluate import calculator_bundle, paired_target_result
from fm_v2_train import file_pin, tree_pin
from fm_zero_n_prepare import KeyDigest, selection_digest_and_rows


ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str, checks: list[str]) -> None:
    if not condition:
        raise RuntimeError(message)
    checks.append(message)


def parquet_key_digest(path: Path, key_fields: tuple[str, ...]) -> tuple[int, dict]:
    digest = KeyDigest()
    rows = 0
    for batch in ds.dataset(str(path), format="parquet").to_batches(columns=list(key_fields), batch_size=100_000):
        values = batch.to_pydict()
        for index in range(batch.num_rows):
            digest.update(tuple(values[field][index] for field in key_fields))
        rows += batch.num_rows
    return rows, digest.document()


def scan_predictions(path: Path, expected_rows: int, expected_keys: dict,
                     low: float, high: float) -> dict:
    digest = KeyDigest()
    rows = supported = unsupported = clipped = 0
    raw_min, raw_max = float("inf"), float("-inf")
    columns = ["episode_id", "candidate_movie_id", "supported", "raw_prediction", "prediction"]
    for batch in ds.dataset(str(path), format="parquet").to_batches(columns=columns, batch_size=100_000):
        values = batch.to_pydict()
        for index in range(batch.num_rows):
            digest.update((values["episode_id"][index], values["candidate_movie_id"][index]))
            is_supported = bool(values["supported"][index])
            raw = values["raw_prediction"][index]
            prediction = values["prediction"][index]
            if is_supported:
                supported += 1
                if raw is None or prediction is None or not math.isfinite(float(raw)) or not math.isfinite(float(prediction)):
                    raise RuntimeError("supported prediction is null/NaN/Inf")
                if not low <= float(prediction) <= high:
                    raise RuntimeError("prediction is outside configured bounds")
                raw_min, raw_max = min(raw_min, float(raw)), max(raw_max, float(raw))
                clipped += int(float(raw) < low or float(raw) > high)
            else:
                unsupported += 1
                if raw is not None or prediction is not None:
                    raise RuntimeError("unsupported row has a score")
            rows += 1
    if rows != expected_rows or digest.document() != expected_keys:
        raise RuntimeError("prediction rows or row-key multiset differ from prepared input")
    return {"rows": rows, "supported": supported, "unsupported": unsupported,
            "raw_min": raw_min, "raw_max": raw_max, "clipped_rows": clipped}


def load_npz(path: Path) -> dict[str, np.ndarray]:
    with np.load(path) as values:
        return {name: values[name].copy() for name in values.files}


def verify(args: argparse.Namespace) -> dict:
    if args.output.exists():
        raise FileExistsError(f"output already exists: {args.output}")
    checks: list[str] = []
    artifacts: dict[str, dict] = {}
    config = json.loads(args.config.read_text(encoding="utf-8"))
    input_manifest_path = args.input_root / "manifest.json"
    input_manifest = json.loads(input_manifest_path.read_text(encoding="utf-8"))
    prepared_manifest_path = args.prepared_root / "manifest.json"
    prepared = json.loads(prepared_manifest_path.read_text(encoding="utf-8"))
    schema_path = args.prepared_root / "feature-schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    distribution = json.loads((args.prepared_root / "split-distribution-report.json").read_text(encoding="utf-8"))
    run_report_path = args.fits_root / "run-report.json"
    tuning_path = args.fits_root / "tuning-report.json"
    validation_path = args.fits_root / "validation-report.json"
    run_report = json.loads(run_report_path.read_text(encoding="utf-8"))
    tuning = json.loads(tuning_path.read_text(encoding="utf-8"))
    validation = json.loads(validation_path.read_text(encoding="utf-8"))

    require(input_manifest["status"] == "PASS" and input_manifest["roles"] == ["TRAIN", "VALIDATION"],
            "input contains only TRAIN and VALIDATION roles", checks)
    require(input_manifest["final_test_materialized"] is False and input_manifest["final_test_label_accessed"] is False,
            "FINAL_TEST is physically absent from model input", checks)
    require(input_manifest["eligible_census"]["roles"] == config["expected_input"]["eligible_census"] and
            input_manifest["eligible_census"]["train_population_saturated"] is True and
            input_manifest["users"]["TRAIN"] == config["expected_input"]["eligible_census"]["TRAIN"]["total"],
            "source census proves every eligible TRAIN user was selected", checks)
    require(input_manifest["source_manifest"] == config["expected_input"]["source_manifest"] and
            input_manifest["prior_validation_exclusion"] ==
            config["expected_input"]["prior_validation_exclusion"] and
            input_manifest["prior_validation_exclusion"]["new_validation_intersection_users"] == 0 and
            input_manifest["source_contract"] == config["expected_input"]["source_contract"] and
            input_manifest["role_rows"] == config["expected_input"]["role_rows"] and
            input_manifest["rejected_without_json_parse"] ==
            config["expected_input"]["rejected_without_json_parse"],
            "source split/cutoff/user/target/catalog/sampling/seal and rejection counters match config", checks)
    require(file_pin(input_manifest_path) == config["expected_input"]["isolated_manifest"],
            "config pins the exact isolated input manifest", checks)
    episode_count, episode_digest, _ = selection_digest_and_rows(args.input_root / "episodes.jsonl", False)
    candidate_count, candidate_digest, _ = selection_digest_and_rows(args.input_root / "candidates.jsonl", False)
    require({"rows": episode_count, "sha256": episode_digest} == config["expected_input"]["episodes"] == input_manifest["episodes"],
            "isolated episode digest matches config and manifest", checks)
    require({"rows": candidate_count, "sha256": candidate_digest} == config["expected_input"]["validation_candidates"] == input_manifest["validation_candidates"],
            "isolated validation-candidate digest matches config and manifest", checks)
    require(prepared["status"] == "PASS" and prepared["failed_rows"] == 0 and
            prepared["final_test"] == "NOT_PRESENT_IN_INPUT_OR_PREPARED_ARTIFACTS",
            "prepared manifest is sealed PASS", checks)
    require(prepared["container_mount_contract"] == {
                "network": "none", "workspace": "/workspace:ro", "isolated_input": "/input:ro",
                "movies_csv": "/catalog/movies.csv:ro", "output": "/output:rw",
                "output_mount_is_dedicated_empty_directory": True, "raw_ratings_visible": False,
                "source_input_with_final_test_visible": False, "output_parent_or_siblings_visible": False},
            "prepare mount set excludes raw ratings, source FINAL_TEST, and sibling artifacts", checks)
    require(prepared["input_manifest"] == file_pin(input_manifest_path),
            "prepared manifest pins isolated input", checks)
    for filename, expected in prepared["files"].items():
        actual = file_pin(args.prepared_root / filename)
        require(actual == expected, f"prepared hash matches: {filename}", checks)
        artifacts[f"prepared.{filename}"] = actual
    current_sources = {name: file_pin(ROOT / name) for name in prepared["source_bundle"]["files"]}
    source_digest = hashlib.sha256(json.dumps(current_sources, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    require(current_sources == prepared["source_bundle"]["files"] and
            source_digest == prepared["source_bundle"]["digest"],
            "prepared source bundle matches current source", checks)

    require(schema["vocabulary"]["fit_internal_splits"] == ["FIT"] and
            schema["scaler"]["fit_internal_splits"] == ["FIT"] and
            tuning["preprocessing_fit_policy"] == config["preprocessing_fit_policy"],
            "vocabulary and scaler were fitted only on internal FIT", checks)
    require(schema["candidate_movie_id_feature"] is False and
            not any(name.startswith("candidate.movie_id:") for name in schema["ordered_names"]),
            "candidate movie ID is absent", checks)
    require(schema["user_id_embedding"] is False and not any("user_id" in name for name in schema["ordered_names"]),
            "user ID is absent", checks)
    require(list(schema["profiles"]) == config["profiles"], "profile order matches preregistration", checks)

    prepared_files = {"train_targets": "train-targets.parquet", "validation_targets": "validation-targets.parquet",
                      "validation_candidates": "validation-candidates.parquet"}
    prepared_keys = {}
    for stem, filename in prepared_files.items():
        rows, keys = parquet_key_digest(args.prepared_root / filename, ("episode_id", "candidate_movie_id"))
        require(rows == int(prepared["rows"][stem]) and keys == prepared["row_key_multisets"][stem],
                f"prepared row count and key multiset match: {stem}", checks)
        prepared_keys[stem] = keys

    profile = schema["profiles"]["content_cross_factor_v3"]
    history_factor = set(profile["positive_factor_indices"] + profile["negative_factor_indices"])
    weight_by_target = defaultdict(float)
    split_by_target = defaultdict(set)
    n0_violations = 0
    fit_rows = holdout_rows = 0
    min_value, max_value = float("inf"), float("-inf")
    parquet = pq.ParquetFile(args.prepared_root / "train-targets.parquet")
    for batch in parquet.iter_batches(columns=["uid", "target_movie_id", "prediction_at", "n", "sample_weight",
                                                   "internal_split", "feature_indices", "feature_values"]):
        values = batch.to_pydict()
        for index in range(batch.num_rows):
            key = (int(values["uid"][index]), int(values["target_movie_id"][index]),
                   int(values["prediction_at"][index]))
            weight_by_target[key] += float(values["sample_weight"][index])
            split_by_target[key].add(values["internal_split"][index])
            fit_rows += int(values["internal_split"][index] == "FIT")
            holdout_rows += int(values["internal_split"][index] == "HOLDOUT")
            row_values = values["feature_values"][index]
            if row_values:
                min_value = min(min_value, min(row_values)); max_value = max(max_value, max(row_values))
            if int(values["n"][index]) == 0 and history_factor.intersection(values["feature_indices"][index]):
                n0_violations += 1
    require(all(abs(value - 1.0) <= 1e-12 for value in weight_by_target.values()),
            "every target has total weight one", checks)
    require(all(len(value) == 1 for value in split_by_target.values()),
            "all N variants remain in one internal split", checks)
    require(fit_rows == tuning["internal_fit_rows"] and holdout_rows == tuning["internal_holdout_rows"],
            "tuning row counts match prepared internal split", checks)
    require(0.0 <= min_value <= max_value <= 1.0, "all active feature values are in [0,1]", checks)
    require(n0_violations == 0 and distribution["n0_history_factor_activation_violations"] == 0,
            "N=0 factor contribution is structurally zero", checks)

    require(run_report["status"] == "PASS" and run_report["profiles"] == config["profiles"] and
            run_report["runtime_image_id"] == config["runtime"]["image_id"] and
            run_report["final_test_opened"] is False,
            "run report matches config, runtime, and sealed policy", checks)
    require(run_report["container_mount_contract"] == {
                "network": "none", "workspace": "/workspace:ro", "prepared": "/prepared:ro",
                "output": "/output:rw", "output_mount_is_dedicated_empty_directory": True,
                "output_parent_or_siblings_visible": False, "raw_ratings_visible": False,
                "source_input_with_final_test_visible": False},
            "model container mount set excludes raw/source/sibling FINAL_TEST artifacts", checks)
    require(run_report["tuning_report"] == file_pin(tuning_path), "run report pins tuning report", checks)
    require(tuning["additive_parameters_frozen_and_shared"] is True,
            "tuning records frozen shared additive parameters", checks)

    additive_model = load_npz(args.fits_root / "matched_additive_v3" / "model" / "model.npz")
    require(all(additive_model[name].size == 0 for name in (
                "candidate_indices", "positive_indices", "negative_indices",
                "candidate_factors", "positive_factors", "negative_factors")),
            "additive model has no factor indices or parameters", checks)
    expected_model_files = {f"seed-{seed}.npz" for seed in config["model_seeds"]}
    actual_model_files = {path.name for path in
                          (args.fits_root / "content_cross_factor_v3" / "model").glob("*.npz")}
    require(actual_model_files == expected_model_files, "factor model seed file set is exact", checks)
    for seed in config["model_seeds"]:
        factor_model = load_npz(args.fits_root / "content_cross_factor_v3" / "model" / f"seed-{seed}.npz")
        require(np.array_equal(factor_model["intercept"], additive_model["intercept"]) and
                np.array_equal(factor_model["linear"], additive_model["linear"]),
                f"seed {seed}: factor model shares exact frozen additive parameters", checks)
        require(np.array_equal(factor_model["candidate_indices"],
                               np.asarray(profile["candidate_factor_indices"], dtype=np.int32)) and
                np.array_equal(factor_model["positive_indices"],
                               np.asarray(profile["positive_factor_indices"], dtype=np.int32)) and
                np.array_equal(factor_model["negative_indices"],
                               np.asarray(profile["negative_factor_indices"], dtype=np.int32)),
                f"seed {seed}: serialized factor indices exactly match schema", checks)
        factor_size = int(tuning["selected_factor_parameters"]["factor_size"])
        require(factor_model["candidate_factors"].shape == (len(profile["candidate_factor_indices"]), factor_size) and
                factor_model["positive_factors"].shape == (len(profile["positive_factor_indices"]), factor_size) and
                factor_model["negative_factors"].shape == (len(profile["negative_factor_indices"]), factor_size),
                f"seed {seed}: serialized factor shapes match schema and selected factor size", checks)
        require(all(np.isfinite(value).all() for value in factor_model.values()),
                f"seed {seed}: all model parameters are finite", checks)

    low, high = map(float, config["prediction_bounds"])
    profiles = {}
    for name in config["profiles"]:
        root = args.fits_root / name
        metrics_path = root / "metrics.json"
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        require(metrics["status"] == "PASS" and metrics["profile"] == name and metrics["failed_rows"] == 0,
                f"{name}: metrics identity PASS", checks)
        require(metrics["prepared_manifest"] == file_pin(prepared_manifest_path) and
                metrics["feature_schema"] == file_pin(schema_path),
                f"{name}: prepared/schema pins match", checks)
        target_scan = scan_predictions(root / "validation-target-predictions.parquet",
                                       int(prepared["rows"]["validation_targets"]),
                                       prepared_keys["validation_targets"], low, high)
        candidate_scan = scan_predictions(root / "validation-candidate-predictions.parquet",
                                          int(prepared["rows"]["validation_candidates"]),
                                          prepared_keys["validation_candidates"], low, high)
        require(metrics["target_prediction_stats"]["rows"] == target_scan["rows"] and
                metrics["candidate_prediction_stats"]["rows"] == candidate_scan["rows"],
                f"{name}: prediction rows are complete", checks)
        report_artifacts = {"metrics": file_pin(metrics_path), "model": tree_pin(root / "model"),
                            "target_predictions": tree_pin(root / "validation-target-predictions.parquet"),
                            "candidate_predictions": tree_pin(root / "validation-candidate-predictions.parquet")}
        require(metrics["model_artifact"] == report_artifacts["model"] and
                metrics["target_predictions_artifact"] == report_artifacts["target_predictions"] and
                metrics["candidate_predictions_artifact"] == report_artifacts["candidate_predictions"],
                f"{name}: metrics internal artifact pins match actual trees", checks)
        require(validation["profiles"][name]["fit"] == metrics and
                validation["profiles"][name]["artifacts"] == report_artifacts,
                f"{name}: validation report pins exact artifacts", checks)
        profiles[name] = {"target": target_scan, "candidates": candidate_scan,
                          "weighted_training_mse": metrics["weighted_training_raw_mse"]}
        artifacts[f"{name}.metrics"] = file_pin(metrics_path)
        artifacts[f"{name}.model"] = tree_pin(root / "model")
        artifacts[f"{name}.target_predictions"] = tree_pin(root / "validation-target-predictions.parquet")
        artifacts[f"{name}.candidate_predictions"] = tree_pin(root / "validation-candidate-predictions.parquet")

    factor_metrics = json.loads((args.fits_root / "content_cross_factor_v3" / "metrics.json").read_text(encoding="utf-8"))
    require(factor_metrics["additive_parameters_frozen_and_shared"] is True,
            "factor metrics affirm exact attribution", checks)
    require(factor_metrics["model_seeds"] == config["model_seeds"],
            "factor metrics seed list exactly matches preregistration", checks)
    expected_seed_prediction_files = {f"seed-{seed}.parquet" for seed in config["model_seeds"]}
    actual_seed_prediction_files = {
        path.name for path in
        (args.fits_root / "content_cross_factor_v3" / "diagnostic-seed-target-predictions").iterdir()
        if path.is_dir()
    }
    require(actual_seed_prediction_files == expected_seed_prediction_files,
            "diagnostic seed prediction directory set is exact", checks)
    for seed in config["model_seeds"]:
        path = args.fits_root / "content_cross_factor_v3" / "diagnostic-seed-target-predictions" / f"seed-{seed}.parquet"
        scan_predictions(path, int(prepared["rows"]["validation_targets"]),
                         prepared_keys["validation_targets"], low, high)
    require(factor_metrics["seed_target_predictions_artifact"] ==
            tree_pin(args.fits_root / "content_cross_factor_v3" / "diagnostic-seed-target-predictions"),
            "per-seed target prediction bundle hash matches", checks)

    require(validation["status"] == "PASS" and validation["config"] == file_pin(args.config) and
            validation["calculator"] == calculator_bundle() and
            validation["selection_policy"] == config["validation_selection_policy"] and
            validation["primary_estimand"] == config["primary_estimand"] and
            validation["primary_decision_rule"] == config["primary_decision"] and
            validation["internal_tuning"] == tuning and
            validation["validation_was_not_used_for_model_selection"] is True and
            validation["final_test_opened"] is False,
            "validation is exact report-only output", checks)
    expected_primary = paired_target_result(
        args.fits_root / "matched_additive_v3" / "validation-target-predictions.parquet",
        args.fits_root / "content_cross_factor_v3" / "validation-target-predictions.parquet",
        int(config["model_seeds"][0]), int(config["bootstrap_replicates"]),
    )
    require(validation["primary_comparison"] == expected_primary,
            "primary paired comparison matches deterministic recomputation", checks)
    expected_seed_diagnostics = {}
    for seed in config["model_seeds"]:
        expected_seed_diagnostics[str(seed)] = paired_target_result(
            args.fits_root / "matched_additive_v3" / "validation-target-predictions.parquet",
            args.fits_root / "content_cross_factor_v3" / "diagnostic-seed-target-predictions" / f"seed-{seed}.parquet",
            int(seed), int(config["bootstrap_replicates"]),
        )
    require(validation["seed_diagnostics"] == expected_seed_diagnostics and
            set(validation["seed_diagnostics"]) == {str(seed) for seed in config["model_seeds"]},
            "every preregistered seed diagnostic matches deterministic recomputation", checks)
    expected_decision = ("SUPPORT_CONTENT_INTERACTION" if expected_primary["user_bootstrap_95_ci"][1] < 0.0
                         else "KEEP_MATCHED_ADDITIVE_BASELINE")
    require(validation["primary_decision"] == expected_decision,
            "primary decision follows preregistered CI rule", checks)

    artifacts.update({"input_manifest": file_pin(input_manifest_path),
                      "prepared_manifest": file_pin(prepared_manifest_path),
                      "run_report": file_pin(run_report_path), "tuning_report": file_pin(tuning_path),
                      "validation_report": file_pin(validation_path)})
    artifact_digest = hashlib.sha256(json.dumps(artifacts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    report = {
        "schema_version": 3,
        "status": "PASS",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "config": file_pin(args.config),
        "source_bundle_digest": source_digest,
        "runtime_image_id": config["runtime"]["image_id"],
        "checks": checks,
        "profiles": profiles,
        "primary_comparison": expected_primary,
        "primary_decision": expected_decision,
        "artifacts": artifacts,
        "artifact_manifest_digest": artifact_digest,
        "artifact_hashes_recomputed_after_run": True,
        "final_test_opened": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--fits-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    verify(parser.parse_args())


if __name__ == "__main__":
    main()
