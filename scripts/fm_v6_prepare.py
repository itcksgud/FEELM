"""Prepare label-isolated full-prefix FM-v6 rows with FIT-only selection preprocessing."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from fm_v5_features import build_feature_map, fit_scaler, fit_vocabulary, parse_movie, schema_document, vectorize
from fm_zero_n_prepare import BatchWriter, file_pin
from fm_v2_train import cgroup_peak


ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = (
    "experiments/fm-v6/config.json", "experiments/fm-v6/DESIGN.md",
    "experiments/fm-v6/jira-design-correction.json",
    "experiments/fm-v6/RESOURCE-PREFLIGHT.json",
    "performance/fm-zero-n.Dockerfile", "scripts/fm_v2_features.py", "scripts/fm_v2_train.py",
    "scripts/fm_zero_n_prepare.py", "scripts/fm_v5_features.py", "scripts/fm_v5_train.py",
    "scripts/fm_v6_build_prefix_input.py", "scripts/fm_v6_build_popularity.py",
    "scripts/fm_v6_isolate_input.py", "scripts/fm_v6_prepare.py", "scripts/fm_v6_train.py",
    "scripts/fm_v6_evaluate.py", "scripts/fm_v6_verify.py", "scripts/fm_v6_run.py",
    "scripts/test_fm_v5_features.py", "scripts/test_fm_v5_train.py",
    "scripts/test_fm_v6_evaluate.py", "scripts/test_fm_v6_prefix_input.py",
)


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def design_digest(config: dict) -> str:
    normalized = json.loads(json.dumps(config))
    normalized.pop("status", None)
    gate = normalized.get("resource_preflight", {})
    for key in ("status", "receipt", "results"):
        gate.pop(key, None)
    return digest(normalized)


def verify_promotion(config: dict) -> dict:
    expected = config["resource_preflight"]
    receipt_path = ROOT / "experiments/fm-v6/RESOURCE-PREFLIGHT.json"
    if expected.get("status") != "PASS" or file_pin(receipt_path) != expected.get("receipt"):
        raise RuntimeError("full preparation lacks an exact PASS resource-preflight receipt")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if (receipt.get("status") != "PASS" or receipt.get("design_digest") != design_digest(config) or
            receipt.get("required_stages") != ["PREPARE", "FIT", "EVALUATE", "VERIFY"]):
        raise RuntimeError("resource-preflight receipt does not promote this frozen design")
    return {"receipt": file_pin(receipt_path), "design_digest": receipt["design_digest"]}


def episode_id(row: dict) -> str:
    return ":".join(map(str, (row["role"], row["uid"], row["target_movie_id"], row["prediction_at"], row["n"])))


def target_key(row: dict) -> tuple[int, int, int]:
    return int(row["uid"]), int(row["target_movie_id"]), int(row["prediction_at"])


def selected_user(uid: int, modulus: int, bucket: int) -> bool:
    return int.from_bytes(hashlib.sha256(f"fmv6-preflight:{uid}".encode()).digest(), "big") % modulus == bucket


def inner_split(uid: int, config: dict) -> str:
    spec = config["internal_split"]
    value = int.from_bytes(hashlib.sha256(f"{spec['salt']}:{uid}".encode()).digest(), "big") % 5
    return "HOLDOUT" if value == int(spec["holdout_bucket"]) else "FIT"


def load_catalog(path: Path) -> dict:
    movies = {}
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            movies[int(row["movie_id"])] = parse_movie(int(row["movie_id"]), row["title"], row["genres"])
    return movies


def verify_input(root: Path, config: dict) -> dict:
    manifest_path = root / "manifest.json"
    if file_pin(manifest_path) != config["isolated_input"]["manifest"]:
        raise RuntimeError("isolated input manifest pin mismatch")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("schema_version") != 6 or manifest.get("status") != "PASS" or
            manifest.get("validation_labels") != "PHYSICALLY_SEPARATE_UNMOUNTED_ROOT" or
            manifest.get("final_test_opened") is not False):
        raise RuntimeError("isolated input contract failed")
    for relative, expected in manifest["files"].items():
        if file_pin(root / relative) != expected:
            raise RuntimeError(f"isolated input file pin mismatch: {relative}")
    return manifest


def load_episodes(root: Path, config: dict, modulus: int, bucket: int) -> list[dict]:
    result = []
    counts: Counter[tuple[str, int]] = Counter()
    with (root / "input/episodes.features.jsonl").open("r", encoding="utf-8") as stream:
        for line in stream:
            if "FINAL_TEST" in line:
                raise RuntimeError("FINAL_TEST token visible in isolated episode input")
            row = json.loads(line)
            if not selected_user(int(row["uid"]), modulus, bucket):
                continue
            if int(row["n"]) != len(row["history"]):
                raise RuntimeError("prefix K/history length mismatch")
            if any(int(event["sequence_position"]) >= int(row["prediction_sequence_position"])
                   for event in row["history"]):
                raise RuntimeError("future event visible in prefix history")
            if row["role"] == "VALIDATION" and "target_rating" in row:
                raise RuntimeError("validation target label visible to prepare")
            if row["role"] == "TRAIN" and "target_rating" not in row:
                raise RuntimeError("TRAIN target label missing")
            result.append(row)
            counts[(row["role"], int(row["n"]))] += 1
    expected_k = set(map(int, config["input"]["k_values"]))
    for role in ("TRAIN", "VALIDATION"):
        if {k for (cell_role, k), value in counts.items() if cell_role == role and value} != expected_k:
            raise RuntimeError(f"selected {role} subset lacks a required prefix K")
    return result


def annotate_training(episodes: list[dict], config: dict) -> list[dict]:
    groups: dict[tuple[int, int, int], list[dict]] = defaultdict(list)
    for row in episodes:
        if row["role"] == "TRAIN":
            groups[target_key(row)].append(row)
    rows = []
    for key in sorted(groups):
        variants = sorted(groups[key], key=lambda row: int(row["n"]))
        if [int(row["n"]) for row in variants] != list(map(int, config["input"]["k_values"])):
            raise RuntimeError(f"TRAIN target lacks exact prefix matrix: {key}")
        for row in variants:
            rows.append(row | {"sample_weight": 1.0 / len(variants),
                               "internal_split": inner_split(int(row["uid"]), config)})
    if {row["internal_split"] for row in rows} != {"FIT", "HOLDOUT"}:
        raise RuntimeError("selected TRAIN subset lacks FIT or HOLDOUT")
    return rows


def load_popularity(root: Path, config: dict) -> tuple[dict[int, tuple[int, float]], dict]:
    manifest_path, artifact = root / "manifest.json", root / "popularity.train-only.jsonl"
    if file_pin(manifest_path) != config["popularity"]["manifest"]:
        raise RuntimeError("popularity manifest pin mismatch")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("artifact_kind") != "PREFIX_TRAIN_TARGET_POPULARITY_V2" or
            manifest.get("ordered_train_user_digest") != config["popularity"]["train_user_digest"] or
            manifest.get("source_input_manifest") != config["input"]["manifest"] or
            manifest.get("training_contract") !=
            "ONE_POSITION_20_TARGET_PER_TRAIN_USER_MATCHED_TO_LEARNED_MODEL_LABELS" or
            file_pin(artifact) != config["popularity"]["artifact"] or
            manifest.get("final_test_opened") is not False):
        raise RuntimeError("TRAIN-only popularity contract failed")
    values = {}
    with artifact.open("r", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            values[int(row["movie_id"])] = int(row["rating_count"]), float(row["bayes_score"])
    return values, manifest


def feature_payload(episode: dict, candidate_id: int, movies: dict, schema: dict, config: dict,
                    diagnostic: dict) -> dict:
    diagnostic["rows"] += 1
    movie = movies.get(candidate_id)
    if movie is None:
        diagnostic["unsupported_rows"] += 1
        return {"feature_indices": [], "feature_values": [], "feature_size": len(schema["ordered_names"]),
                "supported": False, "candidate_genres": "", "unsupported_reason": "CATALOG_MISSING"}
    features, observed = build_feature_map(episode, movie, movies, schema["vocabulary"], schema["scaler"],
                                           float(config["positive_threshold"]))
    indices, values = vectorize(features, schema["ordered_names"])
    for name in ("oov", "missing", "active", "clipped"):
        diagnostic[name].update(observed[name])
    return {"feature_indices": indices, "feature_values": values, "feature_size": len(schema["ordered_names"]),
            "supported": True, "candidate_genres": "|".join(movie.genres), "unsupported_reason": None}


def identity(episode: dict, candidate_id: int, rank: int) -> dict:
    return {"episode_id": episode_id(episode), "role": episode["role"], "uid": int(episode["uid"]),
            "target_movie_id": int(episode["target_movie_id"]), "candidate_movie_id": int(candidate_id),
            "prediction_at": int(episode["prediction_at"]), "n": int(episode["n"]),
            "n_bucket": str(episode["n_bucket"]), "total_history_count": int(episode["total_history_count"]),
            "supported_history_count": int(episode["supported_history_count"]),
            "is_full_history": bool(episode["is_full_history"]), "candidate_rank": int(rank),
            "is_target": int(candidate_id) == int(episode["target_movie_id"])}


def prepare(args: argparse.Namespace) -> dict:
    started = time.monotonic()
    if not args.output_root.is_dir() or any(args.output_root.iterdir()):
        raise FileExistsError("prepared output must be an existing empty directory")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    promotion = None
    if args.user_modulus == 1:
        if config["status"] != "PREREGISTERED_REVIEWED":
            raise RuntimeError("full preparation requires independent preregistration review")
        promotion = verify_promotion(config)
    isolated = verify_input(args.input_root, config)
    episodes = load_episodes(args.input_root, config, args.user_modulus, args.user_bucket)
    train_catalog_path = args.input_root / "catalog/TRAIN.catalog.jsonl"
    validation_catalog_path = args.input_root / "catalog/VALIDATION.catalog.jsonl"
    catalog_manifest = json.loads((args.input_root / "catalog/catalog-snapshots.json").read_text(encoding="utf-8"))
    if (file_pin(train_catalog_path) != catalog_manifest["roles"]["TRAIN"]["content"] or
            file_pin(validation_catalog_path) != catalog_manifest["roles"]["VALIDATION"]["content"]):
        raise RuntimeError("catalog pin mismatch")
    train_movies, validation_movies = load_catalog(train_catalog_path), load_catalog(validation_catalog_path)
    training = annotate_training(episodes, config)
    fit_rows = [row for row in training if row["internal_split"] == "FIT"]
    selection_vocabulary = fit_vocabulary(fit_rows, train_movies, float(config["positive_threshold"]))
    selection_scaler = fit_scaler(fit_rows, train_movies, float(config["positive_threshold"]))
    selection_schema = schema_document(selection_vocabulary, selection_scaler, schema_version=6,
                                       feature_profile_version="fm-v6-prefix-sparse-categorical-v1")
    full_vocabulary = fit_vocabulary(training, train_movies, float(config["positive_threshold"]))
    full_scaler = fit_scaler(training, train_movies, float(config["positive_threshold"]))
    full_schema = schema_document(full_vocabulary, full_scaler, schema_version=6,
                                  feature_profile_version="fm-v6-prefix-sparse-categorical-v1")
    popularity, popularity_meta = load_popularity(args.popularity_root, config)
    writers = {
        "selection_train_targets": BatchWriter(args.output_root / "selection-train-targets.parquet",
                                                ("episode_id", "candidate_movie_id")),
        "train_targets": BatchWriter(args.output_root / "train-targets.parquet", ("episode_id", "candidate_movie_id")),
        "validation_targets": BatchWriter(args.output_root / "validation-targets.parquet", ("episode_id", "candidate_movie_id")),
        "validation_candidates": BatchWriter(args.output_root / "validation-candidates.parquet",
                                              ("episode_id", "candidate_movie_id")),
    }
    diagnostics = {role: {name: Counter() for name in ("oov", "missing", "active", "clipped")} |
                   {"rows": 0, "unsupported_rows": 0} for role in ("TRAIN_SELECTION", "TRAIN_FULL", "VALIDATION")}
    for row in training:
        movie_id = int(row["target_movie_id"])
        common = identity(row, movie_id, 0) | {"label": float(row["target_rating"]),
                                               "sample_weight": float(row["sample_weight"]),
                                               "internal_split": row["internal_split"]}
        writers["selection_train_targets"].append(common | feature_payload(
            row, movie_id, train_movies, selection_schema, config, diagnostics["TRAIN_SELECTION"]
        ))
        writers["train_targets"].append(common | feature_payload(
            row, movie_id, train_movies, full_schema, config, diagnostics["TRAIN_FULL"]
        ))
    validation_groups: dict[tuple[int, int, int], list[dict]] = defaultdict(list)
    for row in episodes:
        if row["role"] != "VALIDATION":
            continue
        validation_groups[target_key(row)].append(row)
        movie_id = int(row["target_movie_id"])
        count, score = popularity.get(movie_id, (0, float(popularity_meta["global_mean"])))
        writers["validation_targets"].append(identity(row, movie_id, 0) |
                                              {"popularity_count": count, "popularity_bayes_score": score} |
                                              feature_payload(row, movie_id, validation_movies, full_schema,
                                                              config, diagnostics["VALIDATION"]))
    current_key = None
    pool: list[dict] = []
    processed = set()

    def flush() -> None:
        if current_key is None:
            return
        variants = sorted(validation_groups.get(current_key, []), key=lambda row: int(row["n"]))
        if [int(row["n"]) for row in variants] != list(map(int, config["input"]["k_values"])):
            raise RuntimeError(f"VALIDATION target lacks exact prefix matrix: {current_key}")
        ranks = [int(item["candidate_rank"]) for item in pool]
        if ranks != list(range(int(config["input"]["candidate_count"]))):
            raise RuntimeError(f"candidate ranks differ from contract: {current_key}")
        pool_digest = hashlib.sha256(",".join(str(item["candidate_movie_id"]) for item in pool).encode()).hexdigest()
        for row in variants:
            if row["candidate_digest"] != pool_digest:
                raise RuntimeError(f"candidate pool differs across prefix K: {current_key}")
            for item in pool:
                movie_id = int(item["candidate_movie_id"])
                count, score = popularity.get(movie_id, (0, float(popularity_meta["global_mean"])))
                writers["validation_candidates"].append(identity(row, movie_id, int(item["candidate_rank"])) |
                                                         {"popularity_count": count,
                                                          "popularity_bayes_score": score} |
                                                         feature_payload(row, movie_id, validation_movies,
                                                                         full_schema, config,
                                                                         diagnostics["VALIDATION"]))
        processed.add(current_key)

    with (args.input_root / "input/candidates.features.jsonl").open("r", encoding="utf-8") as stream:
        for line in stream:
            if "FINAL_TEST" in line:
                raise RuntimeError("FINAL_TEST token visible in candidate features")
            item = json.loads(line)
            if not selected_user(int(item["uid"]), args.user_modulus, args.user_bucket):
                continue
            key = target_key(item)
            if key != current_key:
                flush()
                current_key, pool = key, []
            pool.append(item)
    flush()
    if processed != set(validation_groups):
        raise RuntimeError("validation episode/candidate group sets differ")
    for writer in writers.values():
        writer.close()
    (args.output_root / "selection-feature-schema.json").write_text(
        json.dumps(selection_schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (args.output_root / "feature-schema.json").write_text(
        json.dumps(full_schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    report = {
        "schema_version": 6, "status": "PASS", "subset": {"modulus": args.user_modulus,
                                                              "bucket": args.user_bucket},
        "diagnostics": {role: {name: dict(sorted(value.items())) if isinstance(value, Counter) else value
                                for name, value in values.items()} for role, values in diagnostics.items()},
        "elapsed_seconds": time.monotonic() - started, "resource": cgroup_peak(),
        "validation_labels_visible": False, "final_test_opened": False,
    }
    (args.output_root / "prepare-report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    source_files = {name: file_pin(ROOT / name) for name in SOURCE_FILES}
    prepared_files = {path.name: file_pin(path) for path in sorted(args.output_root.glob("*")) if path.is_file()}
    manifest = {
        "schema_version": 6, "status": "PASS", "created_at": datetime.now(timezone.utc).isoformat(),
        "source_revision": args.source_revision, "config": file_pin(args.config),
        "promotion": promotion,
        "isolated_input_manifest": file_pin(args.input_root / "manifest.json"),
        "subset": {"algorithm": "SHA256_UID_MOD_V1", "modulus": args.user_modulus,
                   "bucket": args.user_bucket, "is_full": args.user_modulus == 1},
        "feature_schemas": {"selection": {"profile_digest": selection_schema["profile_digest"],
                                             "fit_scope": "INTERNAL_FIT_ONLY"},
                            "full": {"profile_digest": full_schema["profile_digest"],
                                     "fit_scope": "ALL_SELECTED_TRAIN_AFTER_TUNING"}},
        "rows": {name: writer.count for name, writer in writers.items()},
        "row_key_multisets": {name: writer.key_digest.document() for name, writer in writers.items()},
        "source_bundle": {"algorithm": "ORDERED_FILE_PINS_V1", "files": source_files,
                          "digest": digest(source_files)},
        "files": prepared_files, "failed_rows": 0,
        "validation_labels": "NOT_MOUNTED", "validation_labels_opened": False,
        "final_test": "SEALED_NOT_EXPORTED", "final_test_opened": False,
    }
    (args.output_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--popularity-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--user-modulus", type=int, default=1)
    parser.add_argument("--user-bucket", type=int, default=0)
    args = parser.parse_args()
    if args.user_modulus <= 0 or not 0 <= args.user_bucket < args.user_modulus:
        raise ValueError("invalid user subset")
    prepare(args)


if __name__ == "__main__":
    main()
