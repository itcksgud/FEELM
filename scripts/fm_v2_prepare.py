"""Prepare balanced all-N FM-v2 rows while rejecting FINAL_TEST before JSON parsing."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from fm_v2_features import build_feature_map, fit_scaler, fit_vocabulary, parse_movie, schema_document, vectorize
from fm_zero_n_prepare import (
    BatchWriter,
    base_row,
    file_pin,
    load_train_movie_support,
    select_frozen_contract,
    selection_digest_and_rows,
    serialized_role,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "experiments/fm-v2/config.json"
SOURCE_FILES = (
    "experiments/fm-v2/config.json",
    "experiments/fm-v2/DESIGN.md",
    "performance/fm-zero-n.Dockerfile",
    "scripts/fm_v2_features.py",
    "scripts/fm_v2_prepare.py",
    "scripts/fm_v2_train.py",
    "scripts/fm_v2_run.py",
    "scripts/fm_v2_evaluate.py",
    "scripts/fm_v2_verify.py",
    "scripts/fm_v2_synthetic.py",
    "scripts/fm_zero_n_prepare.py",
    "scripts/fm_zero_n_evaluate.py",
)


def load_movies(path: Path) -> dict:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return {int(row["movieId"]): parse_movie(int(row["movieId"]), row["title"], row["genres"])
                for row in csv.DictReader(stream)}


def target_key(row: dict) -> tuple[int, int, int]:
    return int(row["uid"]), int(row["target_movie_id"]), int(row["prediction_at"])


def annotate_training_rows(rows: list[dict]) -> list[dict]:
    """Keep all variants, weight every target to one, and hold out each user's latest target."""
    groups: dict[tuple[int, int, int], list[dict]] = defaultdict(list)
    for row in rows:
        if row["role"] == "TRAIN":
            groups[target_key(row)].append(row)
    latest_by_user: dict[int, tuple[int, int, int]] = {}
    for key, variants in groups.items():
        representative = max(variants, key=lambda row: (int(row["target_event_at"]), int(row["target_movie_id"])))
        rank = (int(representative["target_event_at"]), int(representative["prediction_at"]), int(representative["target_movie_id"]))
        current = latest_by_user.get(key[0])
        if current is None:
            latest_by_user[key[0]] = key
        else:
            old = max(groups[current], key=lambda row: (int(row["target_event_at"]), int(row["target_movie_id"])))
            old_rank = (int(old["target_event_at"]), int(old["prediction_at"]), int(old["target_movie_id"]))
            if rank > old_rank:
                latest_by_user[key[0]] = key
    annotated = []
    for key in sorted(groups):
        variants = sorted(groups[key], key=lambda row: (int(row["n"]), row["n_bucket"]))
        weight = 1.0 / len(variants)
        split = "HOLDOUT" if latest_by_user[key[0]] == key else "FIT"
        for row in variants:
            annotated.append({**row, "sample_weight": weight, "internal_split": split})
    return annotated


def feature_row(base: dict, episode: dict, movie, movies: dict, schema: dict, config: dict,
                diagnostics: dict, cache: dict | None = None, cache_key: tuple | None = None) -> dict:
    diagnostics["rows"] += 1
    if movie is None:
        diagnostics["unsupported_rows"] += 1
        return {**base, "feature_indices": [], "feature_values": [], "feature_size": len(schema["ordered_names"]),
                "candidate_genres": "", "supported": False,
                "unsupported_reason": "CANDIDATE_NOT_IN_FROZEN_MOVIELENS_CATALOG"}
    cached = cache.get(cache_key) if cache is not None and cache_key is not None else None
    if cached is None:
        features, observed = build_feature_map(
            episode, movie, movies, schema["vocabulary"], schema["scaler"], float(config["positive_threshold"])
        )
        indices, values = vectorize(features, schema["ordered_names"])
        if values and (min(values) < 0.0 or max(values) > 1.0):
            raise RuntimeError("prepared feature value outside [0,1]")
        cached = ({"feature_indices": indices, "feature_values": values,
                   "feature_size": len(schema["ordered_names"]), "candidate_genres": "|".join(movie.genres)}, observed)
        if cache is not None and cache_key is not None:
            cache[cache_key] = cached
    payload, observed = cached
    for kind in ("oov", "missing", "active", "clipped"):
        diagnostics[kind].update(observed[kind])
    return {**base, **payload}


def prepare(args: argparse.Namespace) -> dict:
    if args.output_root.exists():
        raise FileExistsError(f"output already exists: {args.output_root}")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    input_manifest = json.loads((args.input_root / "manifest.json").read_text(encoding="utf-8"))
    if input_manifest["status"] != "PASS":
        raise RuntimeError("frozen input manifest is not PASS")
    input_contract_id, frozen = select_frozen_contract(
        config, input_manifest, args.input_root / "split-distribution-report.json"
    )
    for name, expected in (("ratings.csv", input_manifest["input"]["ratings_sha256"]),
                           ("movies.csv", input_manifest["input"]["movies_sha256"])):
        if file_pin(args.movielens_root / name)["sha256"] != expected:
            raise RuntimeError(f"{name} digest mismatch")
    episode_count, episode_digest, selection = selection_digest_and_rows(args.input_root / "episodes.jsonl", True)
    candidate_count, candidate_digest, _ = selection_digest_and_rows(args.input_root / "candidates.jsonl", False)
    if {"rows": episode_count, "sha256": episode_digest} != frozen["selection_episodes"]:
        raise RuntimeError("selection episode digest mismatch")
    if {"rows": candidate_count, "sha256": candidate_digest} != frozen["selection_candidates"]:
        raise RuntimeError("selection candidate digest mismatch")

    training = annotate_training_rows(selection)
    movies = load_movies(args.movielens_root / "movies.csv")
    train_support = load_train_movie_support(args.movielens_root / "ratings.csv", int(config["input_seed"]))
    vocabulary = fit_vocabulary(training, movies, float(config["positive_threshold"]))
    scaler = fit_scaler(training, movies, float(config["positive_threshold"]))
    schema = schema_document(vocabulary, scaler)

    args.output_root.mkdir(parents=True)
    writers = {
        "train_targets": BatchWriter(args.output_root / "train-targets.parquet", ("episode_id", "candidate_movie_id")),
        "validation_targets": BatchWriter(args.output_root / "validation-targets.parquet", ("episode_id", "candidate_movie_id")),
        "validation_candidates": BatchWriter(args.output_root / "validation-candidates.parquet", ("episode_id", "candidate_movie_id")),
    }
    diagnostics = {role: {kind: Counter() for kind in ("oov", "missing", "active", "clipped")} |
                   {"rows": 0, "unsupported_rows": 0} for role in ("TRAIN", "VALIDATION")}

    for episode in training:
        movie = movies.get(int(episode["target_movie_id"]))
        if movie is None:
            raise RuntimeError("TRAIN target is unsupported")
        state = "POSITIVE_OBSERVED" if float(episode["target_rating"]) >= float(config["positive_threshold"]) else "NEGATIVE_OBSERVED"
        base = base_row(episode, int(episode["target_movie_id"]), 0, state, float(episode["target_rating"]), True)
        base.update(sample_weight=float(episode["sample_weight"]), internal_split=episode["internal_split"])
        writers["train_targets"].append(feature_row(base, episode, movie, movies, schema, config, diagnostics["TRAIN"]))

    validation_rows = [row for row in selection if row["role"] == "VALIDATION"]
    validation_groups: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for episode in validation_rows:
        movie = movies.get(int(episode["target_movie_id"]))
        if movie is None:
            raise RuntimeError("VALIDATION target is unsupported")
        state = "POSITIVE_OBSERVED" if float(episode["target_rating"]) >= float(config["positive_threshold"]) else "NEGATIVE_OBSERVED"
        base = base_row(episode, int(episode["target_movie_id"]), 0, state, float(episode["target_rating"]), True)
        writers["validation_targets"].append(feature_row(base, episode, movie, movies, schema, config, diagnostics["VALIDATION"]))
        validation_groups[(int(episode["uid"]), int(episode["target_movie_id"]))].append(episode)

    current_key = None
    pool: list[dict] = []
    cache_uid: int | None = None
    feature_cache: dict = {}
    processed_groups: set[tuple[int, int]] = set()

    def flush_pool() -> None:
        nonlocal cache_uid, feature_cache
        if current_key is None:
            return
        episodes = validation_groups.get(current_key)
        if not episodes:
            raise RuntimeError(f"candidate group has no validation episode: {current_key}")
        if cache_uid != current_key[0]:
            cache_uid, feature_cache = current_key[0], {}
        expected = hashlib.sha256(",".join(str(item["candidate_movie_id"]) for item in pool).encode()).hexdigest()
        if len({int(item["candidate_movie_id"]) for item in pool}) != len(pool):
            raise RuntimeError(f"duplicate candidate ID: {current_key}")
        processed_groups.add(current_key)
        for episode in episodes:
            if episode["candidate_digest"] != expected:
                raise RuntimeError(f"candidate digest mismatch: {current_key}")
            for item in pool:
                movie_id = int(item["candidate_movie_id"])
                base = base_row(episode, movie_id, int(item["candidate_rank"]), item["label_state"],
                                None if item["observed_rating"] is None else float(item["observed_rating"]),
                                movie_id == int(episode["target_movie_id"]))
                cache_key = (int(episode["prediction_at"]), int(episode["n"]), movie_id)
                writers["validation_candidates"].append(feature_row(
                    base, episode, movies.get(movie_id), movies, schema, config,
                    diagnostics["VALIDATION"], feature_cache, cache_key
                ))

    with (args.input_root / "candidates.jsonl").open("r", encoding="utf-8") as stream:
        for line in stream:
            role = serialized_role(line)
            if role in {"FINAL_TEST", "TRAIN"}:
                continue
            item = json.loads(line)
            key = (int(item["uid"]), int(item["target_movie_id"]))
            if key != current_key:
                flush_pool()
                current_key, pool = key, []
            pool.append(item)
    flush_pool()
    if processed_groups != set(validation_groups):
        raise RuntimeError("validation episode/candidate group sets differ")
    for writer in writers.values():
        writer.close()

    group_weights = defaultdict(float)
    split_by_target = defaultdict(set)
    for row in training:
        group_weights[target_key(row)] += float(row["sample_weight"])
        split_by_target[target_key(row)].add(row["internal_split"])
    if any(abs(value - 1.0) > 1e-12 for value in group_weights.values()):
        raise RuntimeError("target sample weights do not sum to one")
    if any(len(value) != 1 for value in split_by_target.values()):
        raise RuntimeError("target variants cross internal splits")
    n0_factor_violations = 0
    factor_indices = set(schema["profiles"]["cross_hybrid_fm_v2"]["positive_factor_indices"] +
                         schema["profiles"]["cross_hybrid_fm_v2"]["negative_factor_indices"])
    import pyarrow.parquet as pq
    for batch in pq.ParquetFile(args.output_root / "train-targets.parquet").iter_batches(columns=["n", "feature_indices"]):
        for n, indices in zip(batch.column(0).to_pylist(), batch.column(1).to_pylist()):
            if int(n) == 0 and factor_indices.intersection(indices):
                n0_factor_violations += 1
    if n0_factor_violations:
        raise RuntimeError("N=0 rows activate history factor fields")

    report = {
        "schema_version": 2,
        "status": "PASS",
        "training_variant_policy": config["training_variant_policy"],
        "internal_holdout_policy": config["internal_holdout_policy"],
        "training_rows": len(training),
        "training_targets": len(group_weights),
        "training_n_rows": dict(sorted(Counter(row["n_bucket"] for row in training).items())),
        "internal_split_rows": dict(sorted(Counter(row["internal_split"] for row in training).items())),
        "internal_split_targets": dict(sorted(Counter(next(iter(value)) for value in split_by_target.values()).items())),
        "target_weight_sum_min": min(group_weights.values()),
        "target_weight_sum_max": max(group_weights.values()),
        "n0_history_factor_activation_violations": n0_factor_violations,
        "feature_value_range": [0.0, 1.0],
        "train_movie_support_movies": len(train_support),
        "diagnostics": {
            role: {kind: (dict(sorted(value.items())) if isinstance(value, Counter) else value)
                   for kind, value in values.items()}
            for role, values in diagnostics.items()
        },
        "prepared_rows": {name: writer.count for name, writer in writers.items()},
        "final_test": "SEALED_LABEL_AXES; REJECTED_BEFORE_JSON_PARSE",
    }
    (args.output_root / "feature-schema.json").write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (args.output_root / "split-distribution-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    missing_sources = [name for name in SOURCE_FILES if not (ROOT / name).is_file()]
    if missing_sources:
        raise RuntimeError(f"declared source files are missing: {missing_sources}")
    source_files = {name: file_pin(ROOT / name) for name in SOURCE_FILES}
    source_digest = hashlib.sha256(json.dumps(source_files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    files = {path.name: file_pin(path) for path in sorted(args.output_root.glob("*.parquet"))}
    files.update({name: file_pin(args.output_root / name) for name in ("feature-schema.json", "split-distribution-report.json")})
    manifest = {
        "schema_version": 2,
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_revision": args.source_revision,
        "input_contract_id": input_contract_id,
        "input_manifest": file_pin(args.input_root / "manifest.json"),
        "input_digests": {
            "selection_episodes": {"rows": episode_count, "sha256": episode_digest},
            "selection_candidates": {"rows": candidate_count, "sha256": candidate_digest},
        },
        "feature_schema": {"profile_digest": schema["profile_digest"],
                           "ordered_names_sha256": schema["ordered_names_sha256"],
                           "vocabulary_digest": vocabulary["digest"], "scaler_digest": scaler["digest"]},
        "rows": {name: writer.count for name, writer in writers.items()},
        "row_key_multisets": {name: writer.key_digest.document() for name, writer in writers.items()},
        "source_bundle": {"algorithm": "ORDERED_FILE_PINS_V1", "digest": source_digest, "files": source_files},
        "files": files,
        "failed_rows": 0,
        "final_test": "REJECTED_BEFORE_JSON_PARSE_AND_NOT_WRITTEN",
        "final_test_label_accessed_for_model_or_metrics": False,
    }
    (args.output_root / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--movielens-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    prepare(parser.parse_args())


if __name__ == "__main__":
    main()
