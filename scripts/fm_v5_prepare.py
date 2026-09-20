"""Prepare byte-identical GBT-v7 rows for FM-v5 with sealed validation labels."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from fm_v5_features import build_feature_map, fit_scaler, fit_vocabulary, parse_movie, schema_document, vectorize
from fm_zero_n_prepare import BatchWriter, file_pin


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "experiments/fm-v5/config.json"
SOURCE_FILES = (
    "experiments/fm-v5/config.json", "experiments/fm-v5/DECISION-CRITERIA.md",
    "performance/fm-zero-n.Dockerfile", "scripts/fm_v2_features.py", "scripts/fm_v2_train.py",
    "scripts/fm_zero_n_prepare.py", "scripts/fm_v5_build_popularity.py",
    "scripts/fm_v5_features.py", "scripts/fm_v5_prepare.py", "scripts/fm_v5_train.py",
    "scripts/fm_v5_evaluate.py", "scripts/fm_v5_verify.py", "scripts/fm_v5_run.py",
    "scripts/test_fm_v5_features.py", "scripts/test_fm_v5_train.py", "scripts/test_fm_v5_evaluate.py",
)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _target_key(row: dict) -> tuple[int, int, int]:
    return int(row["uid"]), int(row["target_movie_id"]), int(row["prediction_at"])


def _episode_id(row: dict) -> str:
    return ":".join(map(str, (row["role"], row["uid"], row["target_movie_id"], row["prediction_at"], row["n"])))


def _inner_split(uid: int, config: dict) -> str:
    spec = config["internal_split"]
    bucket = int.from_bytes(hashlib.sha256(f"{spec['salt']}:{uid}".encode()).digest(), "big") % 5
    return "HOLDOUT" if bucket == int(spec["holdout_bucket"]) else "FIT"


def _load_catalog(path: Path) -> dict:
    movies = {}
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            movies[int(row["movie_id"])] = parse_movie(int(row["movie_id"]), row["title"], row["genres"])
    return movies


def _load_and_verify_input(input_root: Path, config: dict) -> tuple[list[dict], dict, dict, dict]:
    execution = config["execution_input"]
    handoff = execution["handoff"]
    paths = {
        "episodes": input_root / "input/episodes.train-validation.jsonl",
        "candidates": input_root / "input/candidates.train-validation.jsonl",
        "manifest": input_root / "input/manifest.train-validation.json",
        "distribution": input_root / "input/split-distribution-report.train-validation.json",
        "lineage": input_root / "metadata/handoff-lineage.json",
        "seal": input_root / "metadata/FINAL_TEST-SEAL.json",
    }
    expected_files = {
        "episodes": handoff["train_validation_episode_file_sha256"],
        "candidates": handoff["train_validation_candidate_file_sha256"],
    }
    for name, expected in expected_files.items():
        if file_pin(paths[name])["sha256"] != expected:
            raise RuntimeError(f"v7 handoff {name} file hash mismatch")
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    lineage = json.loads(paths["lineage"].read_text(encoding="utf-8"))
    distribution = json.loads(paths["distribution"].read_text(encoding="utf-8"))
    seal = json.loads(paths["seal"].read_text(encoding="utf-8"))
    checks = (
        (manifest["episodes"]["sha256"], execution["source_episode_sha256"], "episode source digest"),
        (manifest["candidates"]["sha256"], execution["source_candidate_sha256"], "candidate source digest"),
        (manifest["distribution_report"]["sha256"], execution["source_distribution_report_sha256"],
         "distribution source digest"),
        (lineage["episodes"]["export"]["logical_sha256"],
         handoff["train_validation_episode_logical_sha256"], "episode export logical digest"),
        (lineage["candidates"]["export"]["logical_sha256"],
         handoff["train_validation_candidate_logical_sha256"], "candidate export logical digest"),
        (seal["seal_id"], handoff["final_test_seal_id"], "FINAL_TEST seal"),
        (distribution["status"], "DISTRIBUTION_SHIFT", "distribution status"),
    )
    for actual, expected, label in checks:
        if actual != expected:
            raise RuntimeError(f"v7 handoff {label} mismatch")
    episodes = []
    counts = Counter()
    with paths["episodes"].open("r", encoding="utf-8") as stream:
        for line in stream:
            if "FINAL_TEST" in line:
                raise RuntimeError("FINAL_TEST token visible in episode export")
            row = json.loads(line)
            if row["role"] not in {"TRAIN", "VALIDATION"}:
                raise RuntimeError("unexpected episode role")
            if int(row["n"]) != len(row["history"]):
                raise RuntimeError("episode N/history length mismatch")
            if any(int(item["event_at"]) >= int(row["prediction_at"]) for item in row["history"]):
                raise RuntimeError("history is not strictly pre-prediction")
            episodes.append(row)
            counts[row["role"]] += 1
    if counts != Counter(lineage["episodes"]["export"]["roles"]):
        raise RuntimeError("episode role counts differ from handoff lineage")
    return episodes, manifest, distribution, lineage


def _annotate_training(episodes: list[dict], config: dict) -> list[dict]:
    groups: dict[tuple[int, int, int], list[dict]] = defaultdict(list)
    for row in episodes:
        if row["role"] == "TRAIN":
            groups[_target_key(row)].append(row)
    user_targets = Counter(key[0] for key in groups)
    annotated = []
    for key in sorted(groups):
        variants = sorted(groups[key], key=lambda item: (int(item["n"]), str(item["n_bucket"])))
        if len({int(item["n"]) for item in variants}) != len(variants):
            raise RuntimeError(f"duplicate N variant: {key}")
        label = {float(item["target_rating"]) for item in variants}
        if len(label) != 1:
            raise RuntimeError(f"target variants disagree on label: {key}")
        weight = 1.0 / (user_targets[key[0]] * len(variants))
        for row in variants:
            annotated.append({**row, "sample_weight": weight,
                              "internal_split": _inner_split(key[0], config)})
    if set(row["internal_split"] for row in annotated) != {"FIT", "HOLDOUT"}:
        raise RuntimeError("TRAIN inner split lacks FIT or HOLDOUT")
    return annotated


def _popularity(root: Path, config: dict) -> tuple[dict[int, tuple[int, float]], dict]:
    manifest_path = root / "manifest.json"
    values_path = root / "popularity.train-only.jsonl"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = config["popularity_artifact"]
    checks = (
        (manifest.get("status"), "PASS", "status"),
        (manifest.get("artifact_kind"), "TRAIN_ONLY_POPULARITY_V1", "kind"),
        (manifest.get("source_input_id"), config["execution_input"]["run_id"], "source input"),
        (manifest.get("ordered_train_user_digest"), expected["ordered_train_user_digest"], "TRAIN users"),
        (manifest.get("source_ratings", {}).get("sha256"), expected["source_ratings_sha256"], "ratings source"),
        (file_pin(values_path), expected["artifact"], "artifact pin"),
        (file_pin(manifest_path), expected["manifest"], "manifest pin"),
        (manifest.get("contains_user_ids"), False, "user-id absence"),
        (manifest.get("contains_event_rows"), False, "event-row absence"),
        (manifest.get("final_test_opened"), False, "FINAL_TEST seal"),
    )
    for actual, wanted, label in checks:
        if actual != wanted:
            raise RuntimeError(f"TRAIN-only popularity {label} mismatch")
    values: dict[int, tuple[int, float]] = {}
    with values_path.open("r", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            movie_id = int(row["movie_id"])
            if movie_id in values:
                raise RuntimeError("duplicate movie in popularity artifact")
            values[movie_id] = int(row["rating_count"]), float(row["bayes_score"])
    if len(values) != int(manifest["movies"]):
        raise RuntimeError("popularity artifact movie count mismatch")
    return values, manifest


def _feature_payload(episode: dict, candidate_id: int, movies: dict, schema: dict, config: dict,
                     diagnostics: dict) -> dict:
    movie = movies.get(candidate_id)
    diagnostics["rows"] += 1
    if movie is None:
        diagnostics["unsupported_rows"] += 1
        return {"feature_indices": [], "feature_values": [], "feature_size": len(schema["ordered_names"]),
                "supported": False, "candidate_genres": "", "unsupported_reason": "CATALOG_MISSING"}
    features, observed = build_feature_map(
        episode, movie, movies, schema["vocabulary"], schema["scaler"], float(config["positive_threshold"])
    )
    indices, values = vectorize(features, schema["ordered_names"])
    for name in ("oov", "missing", "active", "clipped"):
        diagnostics[name].update(observed[name])
    return {"feature_indices": indices, "feature_values": values, "feature_size": len(schema["ordered_names"]),
            "supported": True, "candidate_genres": "|".join(movie.genres), "unsupported_reason": None}


def _identity(episode: dict, candidate_id: int, candidate_rank: int) -> dict:
    return {
        "episode_id": _episode_id(episode), "role": episode["role"], "uid": int(episode["uid"]),
        "target_movie_id": int(episode["target_movie_id"]), "candidate_movie_id": int(candidate_id),
        "prediction_at": int(episode["prediction_at"]), "n": int(episode["n"]),
        "n_bucket": str(episode["n_bucket"]), "total_history_count": int(episode["total_history_count"]),
        "supported_history_count": int(episode["supported_history_count"]),
        "is_full_history": bool(episode["is_full_history"]), "candidate_rank": int(candidate_rank),
        "is_target": int(candidate_id) == int(episode["target_movie_id"]),
    }


def prepare(args: argparse.Namespace) -> dict:
    if not args.output_root.is_dir() or any(args.output_root.iterdir()):
        raise FileExistsError("prepared output must be an existing empty isolated directory")
    if not args.labels_root.is_dir() or any(args.labels_root.iterdir()):
        raise FileExistsError("label output must be an existing empty isolated directory")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    episodes, input_manifest, distribution, lineage = _load_and_verify_input(args.input_root, config)
    catalog_manifest = json.loads((args.input_root / "catalog/catalog-snapshots.json").read_text(encoding="utf-8"))
    train_catalog = args.input_root / "catalog/TRAIN.catalog.jsonl"
    validation_catalog = args.input_root / "catalog/VALIDATION.catalog.jsonl"
    if file_pin(train_catalog)["sha256"] != catalog_manifest["roles"]["TRAIN"]["content_sha256"]:
        raise RuntimeError("TRAIN catalog hash mismatch")
    if file_pin(validation_catalog)["sha256"] != catalog_manifest["roles"]["VALIDATION"]["content_sha256"]:
        raise RuntimeError("VALIDATION catalog hash mismatch")
    train_movies, validation_movies = _load_catalog(train_catalog), _load_catalog(validation_catalog)
    training = _annotate_training(episodes, config)
    fit_training = [row for row in training if row["internal_split"] == "FIT"]
    selection_vocabulary = fit_vocabulary(fit_training, train_movies, float(config["positive_threshold"]))
    selection_scaler = fit_scaler(fit_training, train_movies, float(config["positive_threshold"]))
    selection_schema = schema_document(selection_vocabulary, selection_scaler)
    full_vocabulary = fit_vocabulary(training, train_movies, float(config["positive_threshold"]))
    full_scaler = fit_scaler(training, train_movies, float(config["positive_threshold"]))
    full_schema = schema_document(full_vocabulary, full_scaler)
    popularity, popularity_meta = _popularity(args.popularity_root, config)

    writers = {
        "selection_train_targets": BatchWriter(args.output_root / "selection-train-targets.parquet",
                                                ("episode_id", "candidate_movie_id")),
        "train_targets": BatchWriter(args.output_root / "train-targets.parquet", ("episode_id", "candidate_movie_id")),
        "validation_targets": BatchWriter(args.output_root / "validation-targets.parquet", ("episode_id", "candidate_movie_id")),
        "validation_candidates": BatchWriter(args.output_root / "validation-candidates.parquet", ("episode_id", "candidate_movie_id")),
        "target_labels": BatchWriter(args.labels_root / "validation-target-labels.parquet", ("episode_id", "candidate_movie_id")),
        "candidate_labels": BatchWriter(args.labels_root / "validation-candidate-labels.parquet", ("episode_id", "candidate_movie_id")),
    }
    diagnostics = {role: {name: Counter() for name in ("oov", "missing", "active", "clipped")} |
                   {"rows": 0, "unsupported_rows": 0} for role in ("TRAIN", "VALIDATION")}

    for episode in training:
        identity = _identity(episode, int(episode["target_movie_id"]), 0)
        common = {"label": float(episode["target_rating"]),
                  "sample_weight": float(episode["sample_weight"]),
                  "internal_split": episode["internal_split"]}
        selection_row = identity | _feature_payload(
            episode, int(episode["target_movie_id"]), train_movies, selection_schema,
            config, diagnostics["TRAIN"]
        ) | common
        full_row = identity | _feature_payload(
            episode, int(episode["target_movie_id"]), train_movies, full_schema,
            config, diagnostics["TRAIN"]
        ) | common
        writers["selection_train_targets"].append(selection_row)
        writers["train_targets"].append(full_row)

    validation_groups: dict[tuple[int, int, int], list[dict]] = defaultdict(list)
    for episode in episodes:
        if episode["role"] != "VALIDATION":
            continue
        validation_groups[_target_key(episode)].append(episode)
        identity = _identity(episode, int(episode["target_movie_id"]), 0)
        target_count, target_bayes = popularity.get(
            int(episode["target_movie_id"]), (0, popularity_meta["global_mean"])
        )
        writers["validation_targets"].append(
            identity | {"popularity_count": int(target_count),
                        "popularity_bayes_score": float(target_bayes)} |
            _feature_payload(episode, int(episode["target_movie_id"]), validation_movies,
                             full_schema, config, diagnostics["VALIDATION"])
        )
        writers["target_labels"].append(identity | {"observed_rating": float(episode["target_rating"]),
                                                     "label_state": ("POSITIVE_OBSERVED" if
                                                                      float(episode["target_rating"]) >=
                                                                      float(config["positive_threshold"])
                                                                      else "NEGATIVE_OBSERVED")})

    current_key = None
    pool: list[dict] = []
    processed: set[tuple[int, int, int]] = set()

    def flush_pool() -> None:
        if current_key is None:
            return
        variants = validation_groups.get(current_key)
        if not variants:
            raise RuntimeError(f"candidate group lacks validation episodes: {current_key}")
        ranks = [int(item["candidate_rank"]) for item in pool]
        if ranks != list(range(len(pool))):
            raise RuntimeError(f"candidate ranks are not contiguous: {current_key}")
        digest = hashlib.sha256(",".join(str(item["candidate_movie_id"]) for item in pool).encode()).hexdigest()
        for episode in variants:
            if episode["candidate_digest"] != digest:
                raise RuntimeError(f"candidate digest mismatch: {current_key}")
            for item in pool:
                candidate_id = int(item["candidate_movie_id"])
                identity = _identity(episode, candidate_id, int(item["candidate_rank"]))
                count, bayes = popularity.get(candidate_id, (0, popularity_meta["global_mean"]))
                writers["validation_candidates"].append(
                    identity | {"popularity_count": int(count), "popularity_bayes_score": float(bayes)} |
                    _feature_payload(episode, candidate_id, validation_movies, full_schema, config,
                                     diagnostics["VALIDATION"])
                )
                observed = item.get("observed_rating")
                writers["candidate_labels"].append(identity | {
                    "label_state": item["label_state"],
                    "observed_rating": float(observed) if observed is not None else float("nan"),
                    "sampling_probability": float(item.get("sampling_probability", 1.0)),
                    "importance_weight": float(item.get("importance_weight", 1.0)),
                })
        processed.add(current_key)

    candidate_path = args.input_root / "input/candidates.train-validation.jsonl"
    with candidate_path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if "FINAL_TEST" in line:
                raise RuntimeError("FINAL_TEST token visible in candidate export")
            if '"role": "VALIDATION"' not in line:
                continue
            item = json.loads(line)
            key = int(item["uid"]), int(item["target_movie_id"]), int(item["prediction_at"])
            if key != current_key:
                flush_pool()
                current_key, pool = key, []
            pool.append(item)
    flush_pool()
    if processed != set(validation_groups):
        raise RuntimeError("validation candidate and episode key sets differ")
    for writer in writers.values():
        writer.close()

    (args.output_root / "selection-feature-schema.json").write_text(
        json.dumps(selection_schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (args.output_root / "feature-schema.json").write_text(
        json.dumps(full_schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    report = {
        "schema_version": 5, "status": "PASS", "distribution_status": distribution["status"],
        "popularity": popularity_meta,
        "diagnostics": {role: {name: dict(sorted(value.items())) if isinstance(value, Counter) else value
                                for name, value in role_values.items()}
                        for role, role_values in diagnostics.items()},
        "preprocessing_contract": {
            "selection_fit_rows": len(fit_training),
            "selection_fit_split": "FIT_ONLY",
            "selection_schema_digest": selection_schema["profile_digest"],
            "final_refit_rows": len(training),
            "final_schema_digest": full_schema["profile_digest"],
        },
        "unavailable_content_namespaces": full_schema["unavailable_namespaces"],
        "final_test": "SEALED_NOT_TRANSFERRED", "final_test_opened": False,
        "validation_labels_opened_for_fit": False,
    }
    (args.output_root / "split-distribution-report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    source_files = {name: file_pin(ROOT / name) for name in SOURCE_FILES}
    source_digest = _digest(source_files)
    prepared_files = {path.name: file_pin(path) for path in sorted(args.output_root.glob("*")) if path.is_file()}
    label_files = {path.name: file_pin(path) for path in sorted(args.labels_root.glob("*")) if path.is_file()}
    manifest = {
        "schema_version": 5, "status": "PASS", "created_at": datetime.now(timezone.utc).isoformat(),
        "source_revision": args.source_revision,
        "input_root_id": config["execution_input"]["run_id"],
        "input_export_files": {"episodes": file_pin(args.input_root / "input/episodes.train-validation.jsonl"),
                               "candidates": file_pin(candidate_path)},
        "input_source_digests": {"episodes": config["execution_input"]["source_episode_sha256"],
                                 "candidates": config["execution_input"]["source_candidate_sha256"],
                                 "distribution": config["execution_input"]["source_distribution_report_sha256"]},
        "feature_schemas": {
            "selection_fit_only": {"profile_digest": selection_schema["profile_digest"],
                                    "ordered_names_sha256": selection_schema["ordered_names_sha256"],
                                    "vocabulary_digest": selection_vocabulary["digest"],
                                    "scaler_digest": selection_scaler["digest"]},
            "final_full_train": {"profile_digest": full_schema["profile_digest"],
                                 "ordered_names_sha256": full_schema["ordered_names_sha256"],
                                 "vocabulary_digest": full_vocabulary["digest"],
                                 "scaler_digest": full_scaler["digest"]},
        },
        "popularity_artifact": {"manifest": file_pin(args.popularity_root / "manifest.json"),
                                "artifact": file_pin(args.popularity_root / "popularity.train-only.jsonl")},
        "rows": {name: writer.count for name, writer in writers.items()},
        "row_key_multisets": {name: writer.key_digest.document() for name, writer in writers.items()},
        "source_bundle": {"algorithm": "ORDERED_FILE_PINS_V1", "digest": source_digest,
                          "files": source_files},
        "files": prepared_files, "sealed_label_files": label_files,
        "failed_rows": 0, "validation_labels": "SEPARATE_UNMOUNTED_ARTIFACT",
        "validation_labels_opened": False, "final_test": "SEALED_NOT_TRANSFERRED",
        "final_test_opened": False,
    }
    manifest_path = args.output_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    label_manifest = {
        "schema_version": 1, "status": "SEALED_VALIDATION_LABELS", "source_prepared_manifest": file_pin(manifest_path),
        "files": label_files, "row_key_multisets": {name: writers[name].key_digest.document()
                                                     for name in ("target_labels", "candidate_labels")},
        "final_test": "NOT_PRESENT", "final_test_opened": False,
    }
    (args.labels_root / "manifest.json").write_text(
        json.dumps(label_manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--popularity-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--labels-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--source-revision", required=True)
    prepare(parser.parse_args())


if __name__ == "__main__":
    main()
