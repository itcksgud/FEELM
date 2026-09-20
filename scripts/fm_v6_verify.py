"""Fail-closed verifier for FM-v6 prefix artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from collections import Counter, defaultdict
from itertools import zip_longest
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from fm_v2_train import file_pin, tree_pin
from fm_v5_train import FACTOR_PROFILES, LINEAR_PROFILES
from fm_zero_n_prepare import KeyDigest


ROOT = Path(__file__).resolve().parents[1]


def require(value: bool, message: str, checks: list[str]) -> None:
    if not value:
        raise RuntimeError(message)
    checks.append(message)


def parquet_key_digest(path: Path) -> dict:
    result = KeyDigest()
    for batch in pq.ParquetFile(path).iter_batches(columns=["episode_id", "candidate_movie_id"]):
        for episode_id, movie_id in zip(batch.column(0).to_pylist(), batch.column(1).to_pylist()):
            result.update((episode_id, movie_id))
    return result.document()


def verify_input(root: Path, labels: Path, config: dict, checks: list[str]) -> None:
    manifest_path = root / "manifest.json"
    require(file_pin(manifest_path) == config["isolated_input"]["manifest"],
            "isolated feature manifest matches frozen config", checks)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest.get("status") == "PASS" and
            manifest.get("validation_labels") == "PHYSICALLY_SEPARATE_UNMOUNTED_ROOT" and
            manifest.get("final_test_opened") is False, "isolated input firewalls pass", checks)
    source = manifest["source_input"]
    require(source.get("schema_version") == 6 and source.get("status") == "PASS" and
            source["population"]["eligible_users"] == config["input"]["eligible_users"] and
            source["population"]["users"] == config["input"]["role_users"] and
            source["episodes"]["sha256"] == config["input"]["episodes_sha256"] and
            source["candidates"]["sha256"] == config["input"]["candidates_sha256"] and
            source["final_test_seal"]["seal_id"] == config["input"]["final_test_seal_id"],
            "embedded source contract matches frozen config", checks)
    for relative, expected in manifest["files"].items():
        require(file_pin(root / relative) == expected, f"isolated file pin matches: {relative}", checks)
    label_manifest = json.loads((labels / "manifest.json").read_text(encoding="utf-8"))
    require(label_manifest.get("status") == "SEALED_VALIDATION_LABELS" and
            label_manifest.get("source_feature_manifest") == file_pin(manifest_path) and
            label_manifest.get("final_test") == "NOT_PRESENT", "label root links to isolated input", checks)
    require(file_pin(labels / "manifest.json") == config["isolated_input"]["validation_labels"]["manifest"],
            "validation label manifest matches frozen config", checks)
    for name, expected in label_manifest["files"].items():
        require(file_pin(labels / name) == expected, f"label file pin matches: {name}", checks)
    require(label_manifest["files"]["validation-target-labels.jsonl"] ==
            config["isolated_input"]["validation_labels"]["target"] and
            label_manifest["files"]["validation-candidate-labels-base.jsonl"] ==
            config["isolated_input"]["validation_labels"]["candidate_base"],
            "validation label file pins match frozen config", checks)
    for relative in ("input/episodes.features.jsonl", "input/candidates.features.jsonl"):
        with (root / relative).open("rb") as stream:
            require(not any(token in block for block in iter(lambda: stream.read(8 * 1024 * 1024), b"")
                            for token in (b"FINAL_TEST", b"observed_rating", b"label_state")),
                    f"{relative} contains no FINAL_TEST or validation-label tokens", checks)
    expected_k = set(map(int, config["input"]["k_values"]))
    groups: dict[tuple[str, int, int, int], set[int]] = defaultdict(set)
    validation_episode_ids: set[str] = set()
    role_users: dict[str, list[int]] = defaultdict(list)
    seen_users: dict[str, set[int]] = defaultdict(set)
    candidate_digest_by_validation_group: dict[tuple[int, int, int], str] = {}
    episode_rows = 0
    with (root / "input/episodes.features.jsonl").open("r", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            role, uid, n = str(row["role"]), int(row["uid"]), int(row["n"])
            if role not in {"TRAIN", "VALIDATION"}:
                raise RuntimeError("unexpected episode role")
            if len(row["history"]) != n or not all(
                    int(event["sequence_position"]) < int(row["prediction_sequence_position"])
                    for event in row["history"]):
                raise RuntimeError("episode prefix is not exact and future-free")
            if role == "VALIDATION" and "target_rating" in row:
                raise RuntimeError("validation target label leaked into feature input")
            if role == "TRAIN" and "target_rating" not in row:
                raise RuntimeError("TRAIN label absent from feature input")
            key = (role, uid, int(row["target_movie_id"]), int(row["prediction_at"]))
            groups[key].add(n)
            if uid not in seen_users[role]:
                seen_users[role].add(uid); role_users[role].append(uid)
            if role == "VALIDATION":
                eid = ":".join(map(str, (role, uid, row["target_movie_id"], row["prediction_at"], n)))
                validation_episode_ids.add(eid)
                candidate_digest_by_validation_group[(uid, int(row["target_movie_id"]),
                                                       int(row["prediction_at"]))] = row["candidate_digest"]
            episode_rows += 1
    require(all(values == expected_k for values in groups.values()),
            "every exported user-target has the complete exact-K matrix", checks)
    checks.append("every episode prefix is exact and future-free")
    require(episode_rows == manifest["rows"]["episodes"], "episode structure row count matches", checks)
    for role in ("TRAIN", "VALIDATION"):
        actual = hashlib.sha256(",".join(map(str, role_users[role])).encode()).hexdigest()
        require(actual == source["population"]["role_user_digests"][role],
                f"ordered {role} user digest recomputes", checks)
    candidate_groups: dict[tuple[int, int, int], list[int]] = defaultdict(list)
    feature_candidate_path = root / "input/candidates.features.jsonl"
    label_candidate_path = labels / "validation-candidate-labels-base.jsonl"
    judgment_counts: dict[tuple[int, int, int], Counter[str]] = defaultdict(Counter)
    candidate_rows = 0
    with feature_candidate_path.open("r", encoding="utf-8") as features, \
            label_candidate_path.open("r", encoding="utf-8") as label_stream:
        for feature_line, label_line in zip_longest(features, label_stream):
            if feature_line is None or label_line is None:
                raise RuntimeError("candidate feature/label base row counts differ")
            feature, label = json.loads(feature_line), json.loads(label_line)
            key_fields = ("uid", "target_movie_id", "prediction_at", "candidate_movie_id", "candidate_rank")
            if any(feature[name] != label[name] for name in key_fields):
                raise RuntimeError("candidate feature/label base row order differs")
            key = (int(feature["uid"]), int(feature["target_movie_id"]), int(feature["prediction_at"]))
            candidate_groups[key].append(int(feature["candidate_movie_id"]))
            judgment_counts[key][str(label["label_state"])] += 1
            candidate_rows += 1
    require(set(candidate_groups) == set(candidate_digest_by_validation_group),
            "candidate and validation-episode group sets match", checks)
    for key, movie_ids in candidate_groups.items():
        ranks_expected = int(config["input"]["candidate_count"])
        if len(movie_ids) != ranks_expected:
            raise RuntimeError(f"candidate count mismatch for {key}")
        actual_digest = hashlib.sha256(",".join(map(str, movie_ids)).encode()).hexdigest()
        if actual_digest != candidate_digest_by_validation_group[key]:
            raise RuntimeError(f"candidate digest differs across K for {key}")
        counts = judgment_counts[key]
        if counts["POSITIVE_OBSERVED"] + counts["NEGATIVE_OBSERVED"] != config["input"]["judgment_count"]:
            raise RuntimeError(f"observed judgment count mismatch for {key}")
        if counts["UNKNOWN_SAMPLED"] != ranks_expected - config["input"]["judgment_count"]:
            raise RuntimeError(f"UNKNOWN count mismatch for {key}")
    require(candidate_rows == manifest["rows"]["candidates"], "candidate structure row count matches", checks)
    target_label_ids = set()
    with (labels / "validation-target-labels.jsonl").open("r", encoding="utf-8") as stream:
        for line in stream:
            target_label_ids.add(str(json.loads(line)["episode_id"]))
    require(target_label_ids == validation_episode_ids, "validation target label episode set matches", checks)


def verify_prepared(root: Path, config: dict, checks: list[str]) -> dict:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    require(manifest.get("schema_version") == 6 and manifest.get("status") == "PASS" and
            manifest.get("failed_rows") == 0, "prepared manifest is schema-v6 PASS", checks)
    require(manifest.get("validation_labels") == "NOT_MOUNTED" and
            manifest.get("validation_labels_opened") is False and
            manifest.get("final_test_opened") is False, "prepared firewalls pass", checks)
    require(manifest.get("isolated_input_manifest") == config["isolated_input"]["manifest"],
            "prepared manifest pins exact isolated input", checks)
    for name, expected in manifest["files"].items():
        require(file_pin(root / name) == expected, f"prepared file pin matches: {name}", checks)
    mapping = {
        "selection_train_targets": "selection-train-targets.parquet",
        "train_targets": "train-targets.parquet",
        "validation_targets": "validation-targets.parquet",
        "validation_candidates": "validation-candidates.parquet",
    }
    for key, filename in mapping.items():
        require(parquet_key_digest(root / filename) == manifest["row_key_multisets"][key],
                f"recomputed row-key multiset matches: {key}", checks)
    require(manifest["row_key_multisets"]["selection_train_targets"] ==
            manifest["row_key_multisets"]["train_targets"], "selection/full TRAIN keys match", checks)
    for filename in ("validation-targets.parquet", "validation-candidates.parquet"):
        columns = set(pq.ParquetFile(root / filename).schema_arrow.names)
        require(not ({"label", "observed_rating", "label_state"} & columns),
                f"{filename} exposes no labels", checks)
    schema = json.loads((root / "feature-schema.json").read_text(encoding="utf-8"))
    history_factor_indices = set()
    for profile in FACTOR_PROFILES:
        history_factor_indices.update(schema["profiles"][profile]["positive_factor_indices"])
        history_factor_indices.update(schema["profiles"][profile]["negative_factor_indices"])
    violations = 0
    for batch in pq.ParquetFile(root / "train-targets.parquet").iter_batches(columns=["n", "feature_indices"]):
        violations += sum(int(n) == 0 and bool(history_factor_indices.intersection(indices))
                          for n, indices in zip(batch.column(0).to_pylist(), batch.column(1).to_pylist()))
    require(violations == 0, "K=0 activates no history factor value", checks)
    source_files = manifest["source_bundle"]["files"]
    require({name: file_pin(ROOT / name) for name in source_files} == source_files,
            "prepared source bundle matches workspace", checks)
    return manifest


def verify_fit(root: Path, prepared_root: Path, config: dict, config_path: Path,
               checks: list[str]) -> dict:
    report = json.loads((root / "run-report.json").read_text(encoding="utf-8"))
    require(report.get("schema_version") == 6 and report.get("status") == "PASS",
            "fit report is schema-v6 PASS", checks)
    require(report.get("validation_labels_read") is False and report.get("final_test_opened") is False,
            "fit opened neither labels nor FINAL_TEST", checks)
    require(report.get("profiles") == config["model"]["profiles"] and
            report.get("model_seeds") == config["model"]["model_seeds"],
            "fit profile and seed matrix matches config", checks)
    require(report.get("runtime_image_id") == config["runtime"]["image_id"] and
            report.get("config") == file_pin(config_path), "fit runtime/config pins match", checks)
    require(report.get("prepared_manifest") == file_pin(prepared_root / "manifest.json"),
            "fit pins prepared manifest", checks)
    require(report.get("tuning_report") == file_pin(root / "tuning-report.json"),
            "fit tuning report pin matches", checks)
    require(report.get("feature_schemas", {}).get("selection") ==
            file_pin(prepared_root / "selection-feature-schema.json") and
            report.get("feature_schemas", {}).get("full") ==
            file_pin(prepared_root / "feature-schema.json"),
            "fit selection/full feature schema pins match", checks)
    expected_model_keys = set(LINEAR_PROFILES) | set(FACTOR_PROFILES)
    require(set(report["artifacts"]["models"]) == expected_model_keys,
            "model artifact key set exactly matches configured trainable profiles", checks)
    for profile, expected in report["artifacts"]["models"].items():
        require(tree_pin(root / profile / "model") == expected, f"model tree matches: {profile}", checks)
    for key, filename in (("target_predictions", "validation-target-predictions.parquet"),
                          ("candidate_predictions", "validation-candidate-predictions.parquet")):
        require(file_pin(root / filename) == report["artifacts"][key], f"prediction pin matches: {key}", checks)
    for profile, matched in config["interaction_comparisons"].items():
        linear = np.load(root / matched / "model/model.npz")
        for seed in config["model"]["model_seeds"]:
            fm = np.load(root / profile / "model" / f"seed-{seed}.npz")
            require(np.array_equal(fm["intercept"], linear["intercept"]) and
                    np.array_equal(fm["linear"], linear["linear"]),
                    f"{profile} seed {seed} preserves matched linear parameters", checks)
    prepared = json.loads((prepared_root / "manifest.json").read_text(encoding="utf-8"))
    candidate = pq.ParquetFile(root / "validation-candidate-predictions.parquet")
    require(candidate.metadata.num_rows == report["candidate_prediction_stats"]["rows"],
            "candidate prediction row count matches report", checks)
    expected_columns = {f"{profile}__seed_{seed}" for profile in FACTOR_PROFILES
                        for seed in config["model"]["model_seeds"]}
    expected_base_columns = {
        "episode_id", "uid", "target_movie_id", "candidate_movie_id", "prediction_at", "n", "n_bucket",
        "total_history_count", "supported_history_count", "is_full_history", "candidate_rank", "is_target",
        "supported", "candidate_genres", "popularity_count",
    }
    expected_prediction_columns = expected_base_columns | set(config["model"]["profiles"]) | expected_columns
    for filename, key in (("validation-target-predictions.parquet", "validation_targets"),
                          ("validation-candidate-predictions.parquet", "validation_candidates")):
        prediction_path = root / filename
        parquet = pq.ParquetFile(prediction_path)
        require(set(parquet.schema_arrow.names) == expected_prediction_columns and
                len(parquet.schema_arrow.names) == len(expected_prediction_columns),
                f"{filename} has the exact allowed base/profile/seed schema", checks)
        require(parquet.metadata.num_rows == prepared["rows"][key] and
                parquet_key_digest(prediction_path) == prepared["row_key_multisets"][key],
                f"{filename} exactly covers prepared {key} row keys", checks)
    return report


def verify_evaluation(path: Path, root: Path, prepared_root: Path, fits_root: Path,
                      labels_root: Path, config_path: Path, checks: list[str]) -> None:
    report = json.loads(path.read_text(encoding="utf-8"))
    require(report.get("schema_version") == 6 and report.get("status") == "PASS",
            "evaluation is schema-v6 PASS", checks)
    require(report.get("claim_ceiling") == "INPUT_COUNT_POLICY_CANDIDATE" and
            report.get("K_POLICY_STATUS") != "VALIDATED" and
            report.get("TRANSITION_THRESHOLD_STATUS") != "VALIDATED",
            "evaluation enforces candidate-only claim ceiling", checks)
    require(report.get("validation_labels_opened") is True and report.get("final_test_opened") is False,
            "evaluation opens validation only", checks)
    require(report["artifacts"].get("config") == file_pin(config_path) and
            report["artifacts"].get("evaluator") == file_pin(ROOT / "scripts/fm_v6_evaluate.py"),
            "evaluation pins config and metric engine", checks)
    expected_artifacts = {
        "fit_report": file_pin(fits_root / "run-report.json"),
        "prepared_manifest": file_pin(prepared_root / "manifest.json"),
        "config": file_pin(config_path),
        "evaluator": file_pin(ROOT / "scripts/fm_v6_evaluate.py"),
        "labels_manifest": file_pin(labels_root / "manifest.json"),
        "candidate_predictions": file_pin(fits_root / "validation-candidate-predictions.parquet"),
        "candidate_labels": file_pin(labels_root / "validation-candidate-labels-base.jsonl"),
        "target_predictions": file_pin(fits_root / "validation-target-predictions.parquet"),
        "target_labels": file_pin(labels_root / "validation-target-labels.jsonl"),
        "selected_contributions": file_pin(root / "selected-policy-contributions.parquet"),
    }
    require(report.get("artifacts") == expected_artifacts,
            "every evaluation artifact link matches its actual file", checks)
    artifact = report["artifacts"]["selected_contributions"]
    require(file_pin(root / "selected-policy-contributions.parquet") == artifact,
            "selected contribution artifact pin matches", checks)
    table = pq.read_table(root / "selected-policy-contributions.parquet",
                          columns=["uid", "n", "ndcg_at_2"])
    frame = table.to_pandas()
    values = frame.groupby("uid").ndcg_at_2.mean()
    require(len(values) > 0 and np.isfinite(values.to_numpy()).all(),
            "selected NDCG contributions recompute to finite user values", checks)
    # Re-run the frozen metric engine from immutable predictions plus sealed labels.
    # Comparing decision-bearing sections makes metric, bootstrap, gate, and policy
    # selection tampering fail closed rather than merely checking a receipt.
    from fm_v6_evaluate import evaluate
    with tempfile.TemporaryDirectory() as directory:
        audit_root = Path(directory)
        audit_path = audit_root / "validation-report.json"
        recomputed = evaluate(argparse.Namespace(
            prepared_root=prepared_root, fits_root=fits_root, labels_root=labels_root,
            output_root=audit_root, output=audit_path, config=config_path,
        ))
    decision_keys = ("summaries", "exact_k_evidence", "thresholds", "policy_evidence",
                     "interaction_evidence", "target_error_metrics_secondary", "selected_policy",
                     "K_POLICY_STATUS", "TRANSITION_THRESHOLD_STATUS", "FM_INTERACTION_STATUS")
    require(all(report.get(key) == recomputed.get(key) for key in decision_keys),
            "evaluation metrics, confidence intervals, gates, and selection recompute exactly", checks)


def main() -> None:
    global args
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--labels-root", type=Path, required=True)
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--fits-root", type=Path)
    parser.add_argument("--evaluation-root", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    checks: list[str] = []
    verify_input(args.input_root, args.labels_root, config, checks)
    verify_prepared(args.prepared_root, config, checks)
    if args.fits_root:
        verify_fit(args.fits_root, args.prepared_root, config, args.config, checks)
    if args.evaluation_root:
        if not args.fits_root:
            raise RuntimeError("evaluation verification requires fit root")
        verify_evaluation(args.evaluation_root / "validation-report.json", args.evaluation_root,
                          args.prepared_root, args.fits_root, args.labels_root, args.config, checks)
    receipt = {"schema_version": 6, "status": "PASS", "check_count": len(checks), "checks": checks,
               "final_test_opened": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
