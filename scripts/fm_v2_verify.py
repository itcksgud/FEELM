"""Fail-closed verifier for FM-v2 inputs, promotion, gates, and artifact lineage."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pyarrow.dataset as ds
import pyarrow.parquet as pq

from fm_v2_evaluate import calculator_bundle
from fm_v2_synthetic import calculate as calculate_synthetic, calculator_bundle as synthetic_calculator_bundle
from fm_v2_train import file_pin, tree_pin
from fm_zero_n_prepare import KeyDigest, select_frozen_contract, selection_digest_and_rows


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


def verify(args: argparse.Namespace) -> dict:
    if args.output.exists():
        raise FileExistsError(f"output already exists: {args.output}")
    checks: list[str] = []
    artifacts: dict[str, dict] = {}
    config = json.loads(args.config.read_text(encoding="utf-8"))
    input_manifest_path = args.input_root / "manifest.json"
    prepared_manifest_path = args.prepared_root / "manifest.json"
    input_manifest = json.loads(input_manifest_path.read_text(encoding="utf-8"))
    prepared = json.loads(prepared_manifest_path.read_text(encoding="utf-8"))
    schema = json.loads((args.prepared_root / "feature-schema.json").read_text(encoding="utf-8"))
    distribution = json.loads((args.prepared_root / "split-distribution-report.json").read_text(encoding="utf-8"))
    run_report_path = args.fits_root / "run-report.json"
    tuning_path = args.fits_root / "tuning-report.json"
    validation_path = args.fits_root / "validation-report.json"
    run_report = json.loads(run_report_path.read_text(encoding="utf-8"))
    tuning = json.loads(tuning_path.read_text(encoding="utf-8"))
    synthetic = json.loads(args.synthetic_report.read_text(encoding="utf-8"))
    validation = json.loads(validation_path.read_text(encoding="utf-8"))

    input_contract, frozen = select_frozen_contract(config, input_manifest, args.input_root / "split-distribution-report.json")
    require(input_contract == prepared["input_contract_id"], "prepared input contract matches frozen input", checks)
    require(prepared["input_manifest"] == file_pin(input_manifest_path), "prepared input-manifest pin matches actual input", checks)
    episode_count, episode_digest, _ = selection_digest_and_rows(args.input_root / "episodes.jsonl", False)
    candidate_count, candidate_digest, _ = selection_digest_and_rows(args.input_root / "candidates.jsonl", False)
    require({"rows": episode_count, "sha256": episode_digest} == frozen["selection_episodes"] == prepared["input_digests"]["selection_episodes"],
            "actual TRAIN/VALIDATION episode digest matches frozen and prepared claims", checks)
    require({"rows": candidate_count, "sha256": candidate_digest} == frozen["selection_candidates"] == prepared["input_digests"]["selection_candidates"],
            "actual TRAIN/VALIDATION candidate digest matches frozen and prepared claims", checks)
    require(prepared["status"] == "PASS" and prepared["failed_rows"] == 0, "prepared manifest is PASS", checks)
    require(prepared["final_test"] == "REJECTED_BEFORE_JSON_PARSE_AND_NOT_WRITTEN" and
            prepared["final_test_label_accessed_for_model_or_metrics"] is False,
            "FINAL_TEST was rejected before JSON parsing", checks)
    for filename, expected in prepared["files"].items():
        actual = file_pin(args.prepared_root / filename)
        require(actual == expected, f"prepared hash matches: {filename}", checks)
        artifacts[f"prepared.{filename}"] = actual
    current_sources = {name: file_pin(ROOT / name) for name in prepared["source_bundle"]["files"]}
    source_digest = hashlib.sha256(json.dumps(current_sources, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    require(current_sources == prepared["source_bundle"]["files"] and source_digest == prepared["source_bundle"]["digest"],
            "prepared source bundle matches current code", checks)
    require(schema["value_range"] == [float(config["fixture_gate"]["feature_values_min"]),
                                      float(config["fixture_gate"]["feature_values_max"])] and
            schema["scaler"]["fit_roles"] == ["TRAIN"] and schema["vocabulary"]["fit_roles"] == ["TRAIN"],
            "feature schema is TRAIN-fitted and uses configured bounds", checks)
    require(schema["user_id_embedding"] is False and not any("user_id" in name for name in schema["ordered_names"]),
            "user ID is absent", checks)
    require(set(schema["profiles"]) == set(config["profiles"]), "profile set is unique and preregistered", checks)
    require(schema["ordered_names_sha256"] == hashlib.sha256("\n".join(schema["ordered_names"]).encode()).hexdigest(),
            "ordered feature-name hash matches", checks)

    prepared_keys = {}
    prepared_files = {"train_targets": "train-targets.parquet", "validation_targets": "validation-targets.parquet",
                      "validation_candidates": "validation-candidates.parquet"}
    for stem, filename in prepared_files.items():
        rows, keys = parquet_key_digest(args.prepared_root / filename, ("episode_id", "candidate_movie_id"))
        require(rows == int(prepared["rows"][stem]) and keys == prepared["row_key_multisets"][stem],
                f"prepared row count and key multiset match: {stem}", checks)
        prepared_keys[stem] = keys

    hybrid = schema["profiles"]["cross_hybrid_fm_v2"]
    history_factor = set(hybrid["positive_factor_indices"] + hybrid["negative_factor_indices"])
    weight_by_target = defaultdict(float)
    split_by_target = defaultdict(set)
    n0_violations = 0
    min_value, max_value = float("inf"), float("-inf")
    train_rows = 0
    parquet = pq.ParquetFile(args.prepared_root / "train-targets.parquet")
    for batch in parquet.iter_batches(columns=["uid", "target_movie_id", "prediction_at", "n", "sample_weight",
                                                   "internal_split", "feature_indices", "feature_values"]):
        values = batch.to_pydict()
        for index in range(batch.num_rows):
            key = (int(values["uid"][index]), int(values["target_movie_id"][index]), int(values["prediction_at"][index]))
            weight_by_target[key] += float(values["sample_weight"][index])
            split_by_target[key].add(values["internal_split"][index])
            row_values = values["feature_values"][index]
            if row_values:
                min_value = min(min_value, min(row_values)); max_value = max(max_value, max(row_values))
            if int(values["n"][index]) == 0 and history_factor.intersection(values["feature_indices"][index]):
                n0_violations += 1
            train_rows += 1
    require(train_rows == int(prepared["rows"]["train_targets"]), "all prepared training rows scanned", checks)
    require(all(abs(value - 1.0) <= 1e-12 for value in weight_by_target.values()),
            "every target has total training weight one", checks)
    require(all(len(value) == 1 for value in split_by_target.values()), "all N variants stay in one internal split", checks)
    require(min_value >= float(config["fixture_gate"]["feature_values_min"]) and
            max_value <= float(config["fixture_gate"]["feature_values_max"]),
            "all active feature values satisfy configured bounds", checks)
    require(n0_violations == 0 and distribution["n0_history_factor_activation_violations"] == 0,
            "N=0 factor contribution is structurally zero", checks)
    expected_synthetic = calculate_synthetic(args.config)
    require(synthetic == expected_synthetic, "synthetic report matches deterministic recomputation", checks)
    require(synthetic["calculator"] == synthetic_calculator_bundle() and synthetic["config"] == file_pin(args.config) and
            abs(float(synthetic["relative_mse"]) -
                float(synthetic["fm_holdout_mse"]) / float(synthetic["linear_holdout_mse"])) <= 1e-15,
            "synthetic calculator/config pins and ratio are exact", checks)
    if config["fixture_gate"]["synthetic_cross_recovery_required"]:
        require(synthetic["status"] == "PASS" and
                synthetic["fm_holdout_mse"] / synthetic["linear_holdout_mse"] <=
                float(config["fixture_gate"]["synthetic_relative_mse_max"]),
                "synthetic cross effect satisfies configured recovery threshold", checks)

    require(run_report["status"] == "PASS" and run_report["final_test_opened"] is False and
            run_report["runtime_image_id"] == config["runtime"]["image_id"],
            "run used pinned runtime with FINAL_TEST sealed", checks)
    require(run_report["profiles"] == config["profiles"], "run profile order matches preregistered config", checks)
    require(run_report["tuning_report"] == file_pin(tuning_path), "run report pins the actual tuning report", checks)
    if input_contract == "frozen_input":
        require(args.fixture_verification is not None and args.fixture_verification.is_file(),
                "10% verification received a fixture promotion report", checks)
        fixture = json.loads(args.fixture_verification.read_text(encoding="utf-8"))
        require(run_report["fixture_promotion"] is not None and
                run_report["fixture_promotion"]["report"] == file_pin(args.fixture_verification),
                "10% run pins the supplied fixture promotion report", checks)
        require(fixture["status"] == "PASS" and fixture["input_contract_id"] == "fixture_input" and
                fixture["config"] == file_pin(args.config) and fixture["source_bundle_digest"] == source_digest and
                fixture["runtime_image_id"] == config["runtime"]["image_id"] and fixture["final_test_opened"] is False,
                "fixture promotion report matches config/source/runtime and sealed fixture contract", checks)
    else:
        require(run_report["fixture_promotion"] is None, "fixture run has no recursive promotion dependency", checks)
    require(validation["status"] == "PASS" and validation["validation_was_not_used_for_model_selection"] is True and
            validation["final_test_opened"] is False, "validation is report-only and FINAL_TEST remains sealed", checks)
    require(validation["config"] == file_pin(args.config), "validation report pins the exact config", checks)
    require(validation["calculator"] == calculator_bundle(), "validation calculator and dependency bundle match", checks)
    require(validation["selection_policy"] == config["validation_selection_policy"] and
            validation["internal_tuning"] == tuning,
            "validation selection policy and embedded tuning match config and tuning report", checks)
    require(set(validation["profiles"]) == set(config["profiles"]), "validation includes every preregistered profile", checks)

    profiles = {}
    linear_train = float(tuning["final_linear_weighted_training_mse"])
    low, high = map(float, config["prediction_bounds"])
    for name in config["profiles"]:
        root = args.fits_root / name
        metrics_path = root / "metrics.json"
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        require(metrics["status"] == "PASS" and metrics["profile"] == name and metrics["failed_rows"] == 0 and
                int(metrics["seed"]) == int(config["model_seed"]), f"{name}: metrics identity and seed PASS", checks)
        require(metrics["feature_count"] == len(schema["ordered_names"]) and
                metrics["interaction_enabled"] is (name != "sparse_linear_v2"),
                f"{name}: feature count and interaction identity match", checks)
        require(metrics["prepared_manifest"] == file_pin(prepared_manifest_path) and
                metrics["feature_schema"] == file_pin(args.prepared_root / "feature-schema.json"),
                f"{name}: exact prepared/schema pins match", checks)
        require(metrics["final_test_opened"] is False and metrics["runtime_image_id"] == config["runtime"]["image_id"],
                f"{name}: pinned runtime and FINAL_TEST sealed", checks)
        model_pin = tree_pin(root / "model")
        target_pin = tree_pin(root / "validation-target-predictions.parquet")
        candidate_pin = tree_pin(root / "validation-candidate-predictions.parquet")
        require(model_pin == metrics["model_artifact"], f"{name}: model hash matches", checks)
        require(target_pin == metrics["target_predictions_artifact"], f"{name}: target prediction hash matches", checks)
        require(candidate_pin == metrics["candidate_predictions_artifact"], f"{name}: candidate prediction hash matches", checks)
        target_scan = scan_predictions(root / "validation-target-predictions.parquet",
                                       int(prepared["rows"]["validation_targets"]),
                                       prepared_keys["validation_targets"], low, high)
        candidate_scan = scan_predictions(root / "validation-candidate-predictions.parquet",
                                          int(prepared["rows"]["validation_candidates"]),
                                          prepared_keys["validation_candidates"], low, high)
        require(int(metrics["target_prediction_stats"]["rows"]) == target_scan["rows"] and
                int(metrics["candidate_prediction_stats"]["rows"]) == candidate_scan["rows"],
                f"{name}: no prediction rows were dropped or replaced", checks)
        report_artifacts = {"metrics": file_pin(metrics_path), "model": model_pin,
                            "target_predictions": target_pin, "candidate_predictions": candidate_pin}
        require(validation["profiles"][name]["fit"] == metrics and
                validation["profiles"][name]["artifacts"] == report_artifacts,
                f"{name}: validation report links exact metrics/model/predictions", checks)
        expected_tuning = ({"selected_reg": tuning["selected_linear_reg"]} if name == "sparse_linear_v2"
                           else tuning["factor_profiles"][name])
        require(metrics["tuning"] == expected_tuning, f"{name}: metrics tuning matches actual tuning report", checks)
        if name != "sparse_linear_v2":
            factor = tuning["factor_profiles"][name]
            if config["fixture_gate"]["weighted_training_mse_not_worse_than_linear"]:
                require(float(metrics["weighted_training_raw_mse"]) <= linear_train + 1e-12,
                        f"{name}: weighted training MSE is not worse than linear", checks)
            require(float(factor["relative_holdout_mse"]) <=
                    float(config["fixture_gate"]["internal_holdout_relative_mse_max"]) + 1e-12,
                    f"{name}: internal holdout satisfies configured relative MSE gate", checks)
        profiles[name] = {"weighted_training_mse": metrics["weighted_training_raw_mse"],
                          "target": target_scan, "candidates": candidate_scan}
        artifacts[f"{name}.metrics"] = file_pin(metrics_path)
        artifacts[f"{name}.model"] = model_pin
        artifacts[f"{name}.target_predictions"] = target_pin
        artifacts[f"{name}.candidate_predictions"] = candidate_pin

    artifacts.update({"input_manifest": file_pin(input_manifest_path),
                      "prepared_manifest": file_pin(prepared_manifest_path),
                      "run_report": file_pin(run_report_path), "tuning_report": file_pin(tuning_path),
                      "validation_report": file_pin(validation_path), "synthetic_report": file_pin(args.synthetic_report)})
    artifact_digest = hashlib.sha256(json.dumps(artifacts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    report = {
        "schema_version": 2, "status": "PASS", "verified_at": datetime.now(timezone.utc).isoformat(),
        "input_contract_id": input_contract, "config": file_pin(args.config),
        "source_bundle_digest": source_digest, "runtime_image_id": config["runtime"]["image_id"],
        "checks": checks, "profiles": profiles, "synthetic": synthetic,
        "artifacts": artifacts, "artifact_manifest_digest": artifact_digest,
        "artifact_hashes_recomputed_after_run": True, "final_test_opened": False,
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
    parser.add_argument("--synthetic-report", type=Path, required=True)
    parser.add_argument("--fixture-verification", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    verify(parser.parse_args())


if __name__ == "__main__":
    main()
