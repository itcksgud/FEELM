"""Prepare full-eligible FM-v3 data with preprocessing fitted on internal FIT only."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq

from fm_v2_prepare import annotate_training_rows, target_key
from fm_v3_features import build_feature_map, fit_scaler, fit_vocabulary, parse_movie, schema_document, vectorize
from fm_zero_n_prepare import BatchWriter, base_row, file_pin, selection_digest_and_rows


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "experiments/fm-v3/config.json"
SOURCE_FILES = (
    "experiments/fm-v3/config.json",
    "experiments/fm-v3/DESIGN.md",
    "performance/fm-zero-n.Dockerfile",
    "scripts/gbt_zero_n_build_input.py",
    "scripts/fm_v3_build_input.py",
    "scripts/fm_v2_features.py",
    "scripts/fm_v2_prepare.py",
    "scripts/fm_v2_train.py",
    "scripts/fm_v3_isolate_input.py",
    "scripts/fm_v3_features.py",
    "scripts/fm_v3_prepare.py",
    "scripts/fm_v3_train.py",
    "scripts/fm_v3_run.py",
    "scripts/fm_v3_evaluate.py",
    "scripts/fm_v3_verify.py",
    "scripts/test_fm_v3_features.py",
    "scripts/test_fm_v3_build_input.py",
    "scripts/test_fm_v3_train.py",
    "scripts/fm_zero_n_prepare.py",
    "scripts/fm_zero_n_evaluate.py",
)


def load_movies(path: Path) -> dict:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return {int(row["movieId"]): parse_movie(int(row["movieId"]), row["title"], row["genres"])
                for row in csv.DictReader(stream)}


def feature_row(base: dict, episode: dict, movie, movies: dict, schema: dict, config: dict,
                diagnostics: dict, cache: dict | None = None, cache_key: tuple | None = None) -> dict:
    diagnostics["rows"] += 1
    if movie is None:
        diagnostics["unsupported_rows"] += 1
        return {**base, "feature_indices": [], "feature_values": [],
                "feature_size": len(schema["ordered_names"]), "candidate_genres": "", "supported": False,
                "unsupported_reason": "CANDIDATE_NOT_IN_FROZEN_MOVIELENS_CATALOG"}
    cached = cache.get(cache_key) if cache is not None and cache_key is not None else None
    if cached is None:
        features, observed = build_feature_map(
            episode, movie, movies, schema["vocabulary"], schema["scaler"],
            float(config["positive_threshold"]),
        )
        indices, values = vectorize(features, schema["ordered_names"])
        if values and (min(values) < 0.0 or max(values) > 1.0):
            raise RuntimeError("prepared feature value outside [0,1]")
        cached = ({"feature_indices": indices, "feature_values": values,
                   "feature_size": len(schema["ordered_names"]),
                   "candidate_genres": "|".join(movie.genres)}, observed)
        if cache is not None and cache_key is not None:
            cache[cache_key] = cached
    payload, observed = cached
    for kind in ("oov", "missing", "active", "clipped"):
        diagnostics[kind].update(observed[kind])
    return {**base, **payload}


def verify_isolated_input(config: dict, args: argparse.Namespace) -> tuple[dict, list[dict], dict]:
    manifest_path = args.input_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = config["expected_input"]
    checks = (
        (manifest.get("status"), "PASS", "status"),
        (manifest.get("contract"), config["input_contract"], "contract"),
        (manifest.get("roles"), expected["isolated_roles"], "roles"),
        (manifest.get("users"), {"TRAIN": expected["source_users"]["TRAIN"],
                                 "VALIDATION": expected["source_users"]["VALIDATION"]}, "users"),
        (manifest.get("eligible_census", {}).get("roles"), expected["eligible_census"], "eligible census"),
        (manifest.get("eligible_census", {}).get("train_population_saturated"), True, "TRAIN census saturation"),
        (manifest.get("source_manifest"), expected["source_manifest"], "source manifest pin"),
        (manifest.get("prior_validation_exclusion"), expected["prior_validation_exclusion"],
         "prior validation exclusion"),
        (manifest.get("source_contract"), expected["source_contract"], "source contract"),
        (manifest.get("role_rows"), expected["role_rows"], "role rows"),
        (manifest.get("rejected_without_json_parse"), expected["rejected_without_json_parse"],
         "pre-parse rejection counters"),
        (manifest.get("final_test_materialized"), False, "FINAL_TEST materialization"),
        (manifest.get("final_test_label_accessed"), False, "FINAL_TEST label access"),
        (file_pin(manifest_path), expected["isolated_manifest"], "manifest pin"),
    )
    for actual, wanted, label in checks:
        if actual != wanted:
            raise RuntimeError(f"isolated input {label} mismatch: {actual!r} != {wanted!r}")
    episode_count, episode_digest, selection = selection_digest_and_rows(args.input_root / "episodes.jsonl", True)
    candidate_count, candidate_digest, _ = selection_digest_and_rows(args.input_root / "candidates.jsonl", False)
    episode_pin = {"rows": episode_count, "sha256": episode_digest}
    candidate_pin = {"rows": candidate_count, "sha256": candidate_digest}
    if episode_pin != expected["episodes"] or episode_pin != manifest["episodes"]:
        raise RuntimeError("isolated episode digest mismatch")
    if candidate_pin != expected["validation_candidates"] or candidate_pin != manifest["validation_candidates"]:
        raise RuntimeError("isolated validation-candidate digest mismatch")
    if any(row["role"] not in {"TRAIN", "VALIDATION"} for row in selection):
        raise RuntimeError("isolated episodes contain a forbidden role")
    return manifest, selection, {"episodes": episode_pin, "validation_candidates": candidate_pin}


def prepare(args: argparse.Namespace) -> dict:
    if os.environ.get("FM_PREP_OUTPUT_MOUNT_ISOLATED") != "true":
        raise RuntimeError("FM-v3 prepare requires an isolated output mount")
    if not args.output_root.is_dir() or any(args.output_root.iterdir()):
        raise FileExistsError(f"prepare output must be an existing empty isolated mount: {args.output_root}")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    input_manifest, selection, input_digests = verify_isolated_input(config, args)
    movies_pin = file_pin(args.movies_csv)
    if movies_pin["sha256"] != input_manifest["source_snapshot"]["movies_sha256"]:
        raise RuntimeError("movies.csv digest mismatch")

    training = annotate_training_rows(selection)
    fit_rows = [row for row in training if row["internal_split"] == "FIT"]
    holdout_rows = [row for row in training if row["internal_split"] == "HOLDOUT"]
    if not fit_rows or not holdout_rows:
        raise RuntimeError("internal FIT/HOLDOUT split is empty")
    movies = load_movies(args.movies_csv)
    vocabulary = fit_vocabulary(fit_rows, movies, float(config["positive_threshold"]))
    scaler = fit_scaler(fit_rows, movies, float(config["positive_threshold"]))
    schema = schema_document(vocabulary, scaler)

    writers = {
        "train_targets": BatchWriter(args.output_root / "train-targets.parquet", ("episode_id", "candidate_movie_id")),
        "validation_targets": BatchWriter(args.output_root / "validation-targets.parquet", ("episode_id", "candidate_movie_id")),
        "validation_candidates": BatchWriter(args.output_root / "validation-candidates.parquet", ("episode_id", "candidate_movie_id")),
    }
    diagnostics = {role: {kind: Counter() for kind in ("oov", "missing", "active", "clipped")} |
                   {"rows": 0, "unsupported_rows": 0} for role in ("FIT", "HOLDOUT", "VALIDATION")}

    for episode in training:
        movie = movies.get(int(episode["target_movie_id"]))
        if movie is None:
            raise RuntimeError("TRAIN target is unsupported")
        state = ("POSITIVE_OBSERVED" if float(episode["target_rating"]) >= float(config["positive_threshold"])
                 else "NEGATIVE_OBSERVED")
        base = base_row(episode, int(episode["target_movie_id"]), 0, state,
                        float(episode["target_rating"]), True)
        base.update(sample_weight=float(episode["sample_weight"]), internal_split=episode["internal_split"])
        writers["train_targets"].append(feature_row(
            base, episode, movie, movies, schema, config, diagnostics[episode["internal_split"]]
        ))

    validation_rows = [row for row in selection if row["role"] == "VALIDATION"]
    validation_groups: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for episode in validation_rows:
        movie = movies.get(int(episode["target_movie_id"]))
        if movie is None:
            raise RuntimeError("VALIDATION target is unsupported")
        state = ("POSITIVE_OBSERVED" if float(episode["target_rating"]) >= float(config["positive_threshold"])
                 else "NEGATIVE_OBSERVED")
        base = base_row(episode, int(episode["target_movie_id"]), 0, state,
                        float(episode["target_rating"]), True)
        writers["validation_targets"].append(feature_row(
            base, episode, movie, movies, schema, config, diagnostics["VALIDATION"]
        ))
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
        expected_digest = hashlib.sha256(
            ",".join(str(item["candidate_movie_id"]) for item in pool).encode()
        ).hexdigest()
        if len({int(item["candidate_movie_id"]) for item in pool}) != len(pool):
            raise RuntimeError(f"duplicate candidate ID: {current_key}")
        processed_groups.add(current_key)
        for episode in episodes:
            if episode["candidate_digest"] != expected_digest:
                raise RuntimeError(f"candidate digest mismatch: {current_key}")
            for item in pool:
                movie_id = int(item["candidate_movie_id"])
                base = base_row(episode, movie_id, int(item["candidate_rank"]), item["label_state"],
                                None if item["observed_rating"] is None else float(item["observed_rating"]),
                                movie_id == int(episode["target_movie_id"]))
                cache_key = (int(episode["prediction_at"]), int(episode["n"]), movie_id)
                writers["validation_candidates"].append(feature_row(
                    base, episode, movies.get(movie_id), movies, schema, config,
                    diagnostics["VALIDATION"], feature_cache, cache_key,
                ))

    with (args.input_root / "candidates.jsonl").open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            item = json.loads(line)
            if item["role"] != "VALIDATION":
                raise RuntimeError("physically isolated candidates contain a non-VALIDATION row")
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

    factor_profile = schema["profiles"]["content_cross_factor_v3"]
    history_factor = set(factor_profile["positive_factor_indices"] + factor_profile["negative_factor_indices"])
    n0_violations = 0
    for batch in pq.ParquetFile(args.output_root / "train-targets.parquet").iter_batches(
        columns=["n", "feature_indices"]
    ):
        for n, indices in zip(batch.column(0).to_pylist(), batch.column(1).to_pylist()):
            if int(n) == 0 and history_factor.intersection(indices):
                n0_violations += 1
    if n0_violations:
        raise RuntimeError("N=0 rows activate history factor fields")

    report = {
        "schema_version": 3,
        "status": "PASS",
        "training_rows": len(training),
        "training_targets": len(group_weights),
        "preprocessing_fit_rows": len(fit_rows),
        "preprocessing_excluded_holdout_rows": len(holdout_rows),
        "preprocessing_fit_policy": config["preprocessing_fit_policy"],
        "internal_split_rows": dict(sorted(Counter(row["internal_split"] for row in training).items())),
        "internal_split_targets": dict(sorted(Counter(next(iter(value)) for value in split_by_target.values()).items())),
        "target_weight_sum_min": min(group_weights.values()),
        "target_weight_sum_max": max(group_weights.values()),
        "n0_history_factor_activation_violations": n0_violations,
        "candidate_movie_id_feature": False,
        "feature_value_range": [0.0, 1.0],
        "raw_ratings_mounted_or_read": False,
        "diagnostics": {role: {kind: (dict(sorted(value.items())) if isinstance(value, Counter) else value)
                               for kind, value in values.items()}
                        for role, values in diagnostics.items()},
        "prepared_rows": {name: writer.count for name, writer in writers.items()},
        "final_test": "NOT_PRESENT_IN_PHYSICALLY_ISOLATED_INPUT",
    }
    (args.output_root / "feature-schema.json").write_text(
        json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (args.output_root / "split-distribution-report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    missing_sources = [name for name in SOURCE_FILES if not (ROOT / name).is_file()]
    if missing_sources:
        raise RuntimeError(f"declared source files are missing: {missing_sources}")
    source_files = {name: file_pin(ROOT / name) for name in SOURCE_FILES}
    source_digest = hashlib.sha256(json.dumps(source_files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    files = {path.name: file_pin(path) for path in sorted(args.output_root.glob("*.parquet"))}
    files.update({name: file_pin(args.output_root / name)
                  for name in ("feature-schema.json", "split-distribution-report.json")})
    manifest = {
        "schema_version": 3,
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_revision": args.source_revision,
        "input_contract": config["input_contract"],
        "input_manifest": file_pin(args.input_root / "manifest.json"),
        "input_digests": input_digests,
        "feature_schema": {"profile_digest": schema["profile_digest"],
                           "ordered_names_sha256": schema["ordered_names_sha256"],
                           "vocabulary_digest": vocabulary["digest"], "scaler_digest": scaler["digest"]},
        "rows": {name: writer.count for name, writer in writers.items()},
        "row_key_multisets": {name: writer.key_digest.document() for name, writer in writers.items()},
        "source_bundle": {"algorithm": "ORDERED_FILE_PINS_V1", "digest": source_digest, "files": source_files},
        "files": files,
        "failed_rows": 0,
        "container_mount_contract": {
            "network": "none",
            "workspace": "/workspace:ro",
            "isolated_input": "/input:ro",
            "movies_csv": "/catalog/movies.csv:ro",
            "output": "/output:rw",
            "output_mount_is_dedicated_empty_directory": True,
            "raw_ratings_visible": False,
            "source_input_with_final_test_visible": False,
            "output_parent_or_siblings_visible": False
        },
        "final_test": "NOT_PRESENT_IN_INPUT_OR_PREPARED_ARTIFACTS",
        "final_test_label_accessed_for_model_or_metrics": False,
    }
    (args.output_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--movies-csv", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    prepare(parser.parse_args())


if __name__ == "__main__":
    main()
