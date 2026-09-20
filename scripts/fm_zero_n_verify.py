"""Fail-closed verifier for frozen input, sparse schema, predictions, and artifact pins."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pyarrow.dataset as ds


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "experiments/fm-zero-n/config.json"


class KeyDigest:
    MASK = (1 << 256) - 1

    def __init__(self) -> None:
        self.count = self.xor = self.total = self.square_total = 0

    def update(self, values: tuple) -> None:
        value = int.from_bytes(hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).digest(), "big")
        self.count += 1
        self.xor ^= value
        self.total = (self.total + value) & self.MASK
        self.square_total = (self.square_total + value * value) & self.MASK

    def document(self) -> dict:
        return {"algorithm": "SHA256_KEY_MULTISET_XOR_SUM_SUMSQ_V1", "count": self.count,
                "xor": f"{self.xor:064x}", "sum_mod_2_256": f"{self.total:064x}",
                "sum_squares_mod_2_256": f"{self.square_total:064x}"}


def n_bucket(value: int) -> str:
    for upper, label in ((0, "0"), (1, "1"), (2, "2"), (4, "3-4"), (9, "5-9"),
                         (19, "10-19"), (29, "20-29"), (49, "30-49")):
        if value <= upper:
            return label
    return "50+"


def file_pin(path: Path) -> dict[str, int | str]:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def serialized_role(line: str) -> str:
    match = re.search(r'"role"\s*:\s*"(TRAIN|VALIDATION|FINAL_TEST)"', line)
    if not match:
        raise RuntimeError("serialized row has no recognized role")
    return match.group(1)


def tree_pin(path: Path) -> dict:
    files = {str(item.relative_to(path)).replace("\\", "/"): file_pin(item) for item in sorted(path.rglob("*")) if item.is_file()}
    digest = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"bytes": sum(int(value["bytes"]) for value in files.values()), "sha256": digest, "files": len(files)}


def require(condition: bool, message: str, checks: list[str]) -> None:
    if not condition:
        raise RuntimeError(message)
    checks.append(message)


def canonical_schema_digest(schema: dict) -> str:
    payload = {key: value for key, value in schema.items() if key != "profile_digest"}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def verify_input(input_root: Path, manifest: dict, prepared: dict, frozen: dict) -> dict:
    roles = {"TRAIN", "VALIDATION"}
    users: dict[int, str] = {}
    role_users = defaultdict(set)
    pools = {}
    rows = 0
    arbitrary_n = False
    current_key = None
    histories = []
    ns = []
    episode_digest = hashlib.sha256()

    def finish_history() -> None:
        if not histories:
            return
        ordered = sorted(histories, key=len)
        if len(ordered[0]) != 0:
            raise RuntimeError("nested history group has no N=0 row")
        for left, right in zip(ordered, ordered[1:]):
            if left != right[:len(left)]:
                raise RuntimeError(f"history prefix violation: {current_key}")
        if len(ns) != len(set(ns)):
            raise RuntimeError(f"duplicate N inside nested group: {current_key}")

    with (input_root / "episodes.jsonl").open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            if serialized_role(line) == "FINAL_TEST":
                continue
            row = json.loads(line)
            encoded = json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
            if rows:
                episode_digest.update(b"\n")
            episode_digest.update(encoded)
            rows += 1
            role = row["role"]
            if role not in roles:
                raise RuntimeError("unexpected user role")
            uid = int(row["uid"])
            if uid in users and users[uid] != role:
                raise RuntimeError("user role overlap")
            users[uid] = role
            role_users[role].add(uid)
            window = manifest["cutoffs"][role]
            if int(row["prediction_at"]) != int(window["feature_cutoff_inclusive"]) + 1:
                raise RuntimeError("global time window or predictionAt mismatch")
            if not (int(window["target_start_exclusive"]) < int(row["target_event_at"]) <= int(window["target_end_inclusive"])):
                raise RuntimeError("target event is outside the role window")
            history = row["history"]
            if int(row["supported_history_count"]) != len(history) or int(row["total_history_count"]) < len(history):
                raise RuntimeError("totalHistoryCount < supportedHistoryCount or count mismatch")
            if int(row["n"]) != len(history) or row["n_bucket"] != n_bucket(int(row["n"])):
                raise RuntimeError("N/history length/bucket mismatch")
            if any(int(event["movie_id"]) == int(row["target_movie_id"]) for event in history):
                raise RuntimeError("target appears in history")
            event_order = [(int(event["event_at"]), str(event["event_id"])) for event in history]
            if event_order != sorted(event_order) or any(value[0] >= int(row["prediction_at"]) for value in event_order):
                raise RuntimeError("history order or future-history leak")
            arbitrary_n |= int(row["n"]) not in {0, 10}
            key = (role, uid, int(row["target_movie_id"]), int(row["prediction_at"]))
            if key != current_key:
                finish_history()
                current_key, histories, ns = key, [], []
            histories.append(tuple(str(event["event_id"]) for event in history))
            ns.append(int(row["n"]))
            pools[(role, uid, int(row["target_movie_id"]))] = row["candidate_digest"]
    finish_history()
    expected_episodes = frozen["selection_episodes"]
    if prepared["input_digests"]["selection_episodes"] != expected_episodes:
        raise RuntimeError("prepared selection episode pin differs from independent config pin")
    if rows != int(expected_episodes["rows"]) or episode_digest.hexdigest() != expected_episodes["sha256"] or not arbitrary_n:
        raise RuntimeError("episode row count mismatch or arbitrary N was rejected")
    for role in roles:
        digest = hashlib.sha256(",".join(map(str, sorted(role_users[role]))).encode()).hexdigest()
        if digest != manifest["user_partition"]["role_user_digests"][role]:
            raise RuntimeError(f"role digest mismatch: {role}")

    candidate_rows = 0
    current = None
    ids = []
    rank = 0
    seen = set()

    def finish_candidates() -> None:
        if current is None:
            return
        digest = hashlib.sha256(",".join(map(str, ids)).encode()).hexdigest()
        if pools.get(current) != digest or current[2] not in ids or len(ids) != len(set(ids)):
            raise RuntimeError(f"candidate digest or target inclusion mismatch: {current}")
        seen.add(current)

    candidate_digest = hashlib.sha256()
    with (input_root / "candidates.jsonl").open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            if serialized_role(line) == "FINAL_TEST":
                continue
            row = json.loads(line)
            encoded = json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
            if candidate_rows:
                candidate_digest.update(b"\n")
            candidate_digest.update(encoded)
            candidate_rows += 1
            key = (row["role"], int(row["uid"]), int(row["target_movie_id"]))
            if key != current:
                finish_candidates()
                current, ids, rank = key, [], 0
            if int(row["candidate_rank"]) != rank:
                raise RuntimeError("candidate ranks are not contiguous")
            rank += 1
            ids.append(int(row["candidate_movie_id"]))
            if row["label_state"] == "UNKNOWN_SAMPLED" and row["observed_rating"] is not None:
                raise RuntimeError("UNKNOWN_SAMPLED was converted to an observed negative")
    finish_candidates()
    expected_candidates = frozen["selection_candidates"]
    if prepared["input_digests"]["selection_candidates"] != expected_candidates:
        raise RuntimeError("prepared selection candidate pin differs from independent config pin")
    if candidate_rows != int(expected_candidates["rows"]) or candidate_digest.hexdigest() != expected_candidates["sha256"] or seen != set(pools):
        raise RuntimeError("candidate rows/groups mismatch")
    return {"episode_rows": rows, "candidate_rows": candidate_rows,
            "role_users": {**{role: len(role_users[role]) for role in sorted(roles)},
                           "FINAL_TEST": int(manifest["users"]["FINAL_TEST"])},
            "final_test_handling": "REJECTED_BY_ROLE_SUBSTRING_BEFORE_JSON_PARSE"}


def scan_predictions(path: Path, expected_rows: int, low: float, high: float, expected_keys: dict) -> dict:
    dataset = ds.dataset(path, format="parquet")
    rows = supported = unsupported = 0
    keys = KeyDigest()
    for batch in dataset.to_batches(columns=["episode_id", "candidate_movie_id", "role", "supported", "prediction", "raw_prediction"]):
        values = batch.to_pydict()
        for episode_id, candidate_id, role, is_supported, prediction, raw in zip(
                values["episode_id"], values["candidate_movie_id"], values["role"], values["supported"], values["prediction"], values["raw_prediction"]):
            rows += 1
            keys.update((episode_id, candidate_id))
            if role != "VALIDATION":
                raise RuntimeError("prediction artifact contains a non-VALIDATION row")
            if is_supported:
                supported += 1
                if prediction is None or raw is None or not math.isfinite(float(prediction)) or not math.isfinite(float(raw)):
                    raise RuntimeError("supported prediction contains null/NaN/Inf")
                if not (low <= float(prediction) <= high):
                    raise RuntimeError("final prediction outside [0.5, 5]")
            else:
                unsupported += 1
                if prediction is not None:
                    raise RuntimeError("unsupported prediction was silently replaced by a score")
    if rows != expected_rows:
        raise RuntimeError("failed rows were silently removed")
    if keys.document() != expected_keys:
        raise RuntimeError("prediction row-key multiset differs from prepared input")
    return {"rows": rows, "supported": supported, "unsupported": unsupported}


def verify(args: argparse.Namespace) -> dict:
    if args.output.exists():
        raise FileExistsError(f"output already exists: {args.output}")
    checks: list[str] = []
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    input_manifest = json.loads((args.input_root / "manifest.json").read_text(encoding="utf-8"))
    prepared = json.loads((args.prepared_root / "manifest.json").read_text(encoding="utf-8"))
    schema = json.loads((args.prepared_root / "feature-schema.json").read_text(encoding="utf-8"))
    validation = json.loads((args.fits_root / "validation-report.json").read_text(encoding="utf-8"))
    require(prepared.get("input_contract_id") in {"frozen_input", "fixture_input"}, "prepared input contract ID is recognized", checks)
    frozen = config[prepared["input_contract_id"]]
    require(input_manifest["status"] == "PASS", "input manifest PASS", checks)
    require(input_manifest["schema_version"] == frozen["schema_version"] and input_manifest["seed"] == config["input_seed"], "frozen input schema and seed match", checks)
    require(input_manifest["input"] == {"ratings_sha256": frozen["ratings_sha256"], "movies_sha256": frozen["movies_sha256"]}, "frozen raw MovieLens hashes match", checks)
    require(input_manifest["users"] == frozen["users"] and input_manifest["targets"] == frozen["targets"], "frozen role user/target counts match", checks)
    require(input_manifest["episodes"] == frozen["episodes"] and input_manifest["candidates"] == frozen["candidates"], "frozen global episode/candidate claims match", checks)
    require(input_manifest["final_test_seal_id"] == frozen["final_test_seal_id"] and input_manifest["user_partition"]["role_user_digests"] == frozen["role_user_digests"], "frozen role digests and FINAL_TEST seal match", checks)
    require(file_pin(args.input_root / "split-distribution-report.json")["sha256"] == frozen["distribution_report_sha256"], "frozen split distribution artifact hash matches without parsing it", checks)
    require(prepared["status"] == "PASS" and prepared["failed_rows"] == 0, "prepared manifest PASS with zero failed rows", checks)
    require(prepared["input_manifest"]["sha256"] == file_pin(args.input_root / "manifest.json")["sha256"], "prepared input manifest pin matches", checks)
    require(prepared["input_digests"]["frozen_global_episodes_claim"] == frozen["episodes"]["sha256"] and prepared["input_digests"]["frozen_global_candidates_claim"] == frozen["candidates"]["sha256"], "frozen global digest claims are pinned", checks)
    require(prepared["final_test"] == "REJECTED_BEFORE_JSON_PARSE_AND_NOT_WRITTEN" and prepared["final_test_label_accessed_for_model_or_metrics"] is False, "FINAL_TEST is rejected before JSON parsing and not written", checks)
    for filename, expected in prepared["files"].items():
        require(file_pin(args.prepared_root / filename) == expected, f"prepared artifact hash matches: {filename}", checks)
    current_source_files = {name: file_pin(ROOT / name) for name in prepared["source_bundle"]["files"]}
    current_source_digest = hashlib.sha256(json.dumps(current_source_files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    require(current_source_files == prepared["source_bundle"]["files"] and current_source_digest == prepared["source_bundle"]["digest"], "prepared source bundle including Dockerfile matches current files", checks)
    require(schema["vocabulary"]["fit_roles"] == ["TRAIN"] and schema["normalizer"]["fit_roles"] == ["TRAIN"], "vocabulary and normalizer fit TRAIN only", checks)
    require(schema["ordered_names_sha256"] == hashlib.sha256("\n".join(schema["ordered_names"]).encode()).hexdigest(), "ordered-name hash matches", checks)
    require(schema["profile_digest"] == canonical_schema_digest(schema), "feature schema/profile digest matches", checks)
    require(len(schema["oov_indices"]) == len(set(schema["oov_indices"].values())), "namespace OOV indexes do not collide", checks)
    require(schema["profiles"]["sparse_linear_only"]["indices"] == schema["profiles"]["sparse_history_content_fm"]["indices"], "linear-only uses the same sparse vector as full FM", checks)
    require(schema["user_id_embedding"] is False and not any("user_id" in name for name in schema["ordered_names"]), "user ID embedding is absent", checks)
    require(all(value["status"] == "UNAVAILABLE" for value in schema["unavailable_blocks"].values()), "director/actor/keyword blocks are explicitly UNAVAILABLE", checks)
    require(set(validation["profiles"]) == set(config["profiles"]) and validation["final_test_opened"] is False, "all preregistered profiles evaluated with FINAL_TEST sealed", checks)
    expected_calculator = {"path": "scripts/fm_zero_n_evaluate.py",
                           "sha256": file_pin(ROOT / "scripts/fm_zero_n_evaluate.py")["sha256"]}
    require(validation.get("calculator") == expected_calculator, "validation report calculator is pinned to the current evaluator", checks)
    semantic = verify_input(args.input_root, input_manifest, prepared, frozen)
    checks.append("role overlap, time windows, history prefixes, arbitrary N, and candidate digests passed")

    low, high = map(float, config["prediction_bounds"])
    profiles = {}
    artifacts = {
        "prepared_manifest": file_pin(args.prepared_root / "manifest.json"),
        "feature_schema": file_pin(args.prepared_root / "feature-schema.json"),
        "distribution_report": file_pin(args.prepared_root / "split-distribution-report.json"),
        "validation_report": file_pin(args.fits_root / "validation-report.json"),
    }
    primary_seed = int(config["model_seeds"][0])
    runtime = json.loads((args.fits_root / f"runtime-seed-{primary_seed}.json").read_text(encoding="utf-8"))
    require(runtime["docker_image_id"] == config["runtime"]["image_id"] and runtime["spark_version"] == config["runtime"]["spark_version"] and
            int(runtime["seed"]) == primary_seed and set(runtime["profiles"]) == set(config["profiles"]),
            "primary runtime image, Spark version, seed, and profile set are pinned", checks)
    artifacts["runtime"] = file_pin(args.fits_root / f"runtime-seed-{primary_seed}.json")
    for profile in config["profiles"]:
        root = args.fits_root / f"{profile}-seed-{primary_seed}"
        metrics = json.loads((root / "metrics.json").read_text(encoding="utf-8"))
        require(metrics["status"] == "PASS" and metrics["failed_rows"] == 0 and metrics["profile"] == profile and int(metrics["seed"]) == primary_seed,
                f"{profile}: fit identity PASS with zero failed rows", checks)
        require(metrics["prepared_manifest"]["sha256"] == file_pin(args.prepared_root / "manifest.json")["sha256"], f"{profile}: exact prepared input digest", checks)
        require(metrics["feature_schema"]["sha256"] == file_pin(args.prepared_root / "feature-schema.json")["sha256"], f"{profile}: exact feature schema digest", checks)
        require(metrics["final_test_opened"] is False and metrics["gpu_training"] is False, f"{profile}: FINAL_TEST sealed and CPU Spark run", checks)
        require(metrics["runtime_image_id"] == config["runtime"]["image_id"], f"{profile}: pinned runtime image ID", checks)
        require(metrics["interaction_enabled"] is (profile != "sparse_linear_only"), f"{profile}: interaction/linear identity is truthful", checks)
        require(tree_pin(root / "model")["sha256"] == metrics["model_artifact_sha256"], f"{profile}: model artifact digest matches", checks)
        target_tree = tree_pin(root / "validation-target-predictions.parquet")
        candidate_tree = tree_pin(root / "validation-candidate-predictions.parquet")
        require(target_tree["sha256"] == metrics["target_predictions_artifact"], f"{profile}: target prediction artifact hash matches metrics manifest", checks)
        require(candidate_tree["sha256"] == metrics["candidate_predictions_artifact"], f"{profile}: candidate prediction artifact hash matches metrics manifest", checks)
        target = scan_predictions(root / "validation-target-predictions.parquet", int(prepared["rows"]["validation_targets"]), low, high,
                                  prepared["row_key_multisets"]["validation_targets"])
        candidates = scan_predictions(root / "validation-candidate-predictions.parquet", int(prepared["rows"]["validation_candidates"]), low, high,
                                      prepared["row_key_multisets"]["validation_candidates"])
        report_artifacts = {"metrics": file_pin(root / "metrics.json"), "target_predictions": target_tree,
                            "candidate_predictions": candidate_tree}
        require(validation["profiles"][profile]["fit"] == metrics,
                f"{profile}: validation report fit is linked to actual metrics", checks)
        require(validation["profiles"][profile]["artifacts"] == report_artifacts,
                f"{profile}: validation report is linked to actual prediction hashes", checks)
        profiles[profile] = {"target": target, "candidates": candidates, "fit_seconds": metrics["fit_seconds"],
                             "peak_ram_bytes": metrics["resource"]["peak_ram_bytes"]}
        artifacts[f"{profile}.metrics"] = file_pin(root / "metrics.json")
        artifacts[f"{profile}.model"] = tree_pin(root / "model")
        artifacts[f"{profile}.target_predictions"] = target_tree
        artifacts[f"{profile}.candidate_predictions"] = candidate_tree

    factor_profiles = [profile for profile in config["profiles"] if profile != "sparse_linear_only"]
    selected = min(factor_profiles, key=lambda profile: validation["profiles"][profile]["validation_user_macro_mse"])
    require(validation["selected_exploratory_factor_profile"] == selected,
            "selected factor profile is the minimum reported validation user-macro MSE", checks)
    sensitivity = validation.get("seed_sensitivity", [])
    sensitivity_by_seed = {int(item["seed"]): item for item in sensitivity}
    configured_seeds = [int(value) for value in config["model_seeds"]]
    require(len(sensitivity) == len(configured_seeds) and set(sensitivity_by_seed) == set(configured_seeds),
            "seed sensitivity contains every configured seed exactly once", checks)
    for seed in configured_seeds:
        seed_runtime_path = args.fits_root / f"runtime-seed-{seed}.json"
        seed_runtime = json.loads(seed_runtime_path.read_text(encoding="utf-8"))
        expected_runtime_profiles = set(config["profiles"]) if seed == primary_seed else {selected}
        require(seed_runtime["docker_image_id"] == config["runtime"]["image_id"] and
                seed_runtime["spark_version"] == config["runtime"]["spark_version"] and
                int(seed_runtime["seed"]) == seed and set(seed_runtime["profiles"]) == expected_runtime_profiles,
                f"seed {seed}: runtime and executed profile set are pinned", checks)
        artifacts[f"runtime.seed-{seed}"] = file_pin(seed_runtime_path)
        seed_root = args.fits_root / f"{selected}-seed-{seed}"
        seed_metrics = json.loads((seed_root / "metrics.json").read_text(encoding="utf-8"))
        require(seed_metrics["status"] == "PASS" and seed_metrics["profile"] == selected and
                int(seed_metrics["seed"]) == seed and seed_metrics["failed_rows"] == 0,
                f"seed {seed}: selected-profile fit identity PASS", checks)
        require(seed_metrics["prepared_manifest"]["sha256"] == file_pin(args.prepared_root / "manifest.json")["sha256"] and
                seed_metrics["feature_schema"]["sha256"] == file_pin(args.prepared_root / "feature-schema.json")["sha256"],
                f"seed {seed}: exact prepared/schema digests", checks)
        require(seed_metrics["runtime_image_id"] == config["runtime"]["image_id"] and
                seed_metrics["final_test_opened"] is False and seed_metrics["gpu_training"] is False,
                f"seed {seed}: pinned runtime, FINAL_TEST sealed, CPU Spark run", checks)
        require(tree_pin(seed_root / "model")["sha256"] == seed_metrics["model_artifact_sha256"],
                f"seed {seed}: model artifact digest matches", checks)
        seed_target_tree = tree_pin(seed_root / "validation-target-predictions.parquet")
        seed_candidate_tree = tree_pin(seed_root / "validation-candidate-predictions.parquet")
        require(seed_target_tree["sha256"] == seed_metrics["target_predictions_artifact"] and
                seed_candidate_tree["sha256"] == seed_metrics["candidate_predictions_artifact"],
                f"seed {seed}: prediction artifact digests match", checks)
        if seed != primary_seed:
            scan_predictions(seed_root / "validation-target-predictions.parquet", int(prepared["rows"]["validation_targets"]), low, high,
                             prepared["row_key_multisets"]["validation_targets"])
            scan_predictions(seed_root / "validation-candidate-predictions.parquet", int(prepared["rows"]["validation_candidates"]), low, high,
                             prepared["row_key_multisets"]["validation_candidates"])
        seed_report_artifacts = {"metrics": file_pin(seed_root / "metrics.json"),
                                 "target_predictions": seed_target_tree,
                                 "candidate_predictions": seed_candidate_tree}
        require(sensitivity_by_seed[seed]["fit"] == seed_metrics and
                sensitivity_by_seed[seed]["artifacts"] == seed_report_artifacts,
                f"seed {seed}: sensitivity report is linked to actual metrics and predictions", checks)
        artifacts[f"{selected}.seed-{seed}.metrics"] = file_pin(seed_root / "metrics.json")
        artifacts[f"{selected}.seed-{seed}.target_predictions"] = seed_target_tree
        artifacts[f"{selected}.seed-{seed}.candidate_predictions"] = seed_candidate_tree

    failures = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((args.fits_root / "logs").glob("*.failure.json"))] if (args.fits_root / "logs").exists() else []
    artifact_digest = hashlib.sha256(json.dumps(artifacts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    report = {
        "schema_version": 1, "status": "PASS", "verified_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks, "input_semantics": semantic, "profiles": profiles,
        "selected_exploratory_factor_profile": selected,
        "seed_sensitivity": sensitivity, "failure_records": failures,
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
    parser.add_argument("--output", type=Path, required=True)
    verify(parser.parse_args())


if __name__ == "__main__":
    main()
