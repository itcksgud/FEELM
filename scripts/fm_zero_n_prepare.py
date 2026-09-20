"""Prepare TRAIN-fitted sparse FM rows while keeping FINAL_TEST sealed."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from fm_zero_n_features import (
    build_feature_map,
    fit_normalizer,
    fit_vocabulary,
    parse_movie,
    schema_document,
    vectorize,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = (
    "experiments/fm-zero-n/config.json",
    "performance/fm-zero-n.Dockerfile",
    "scripts/fm_zero_n_features.py",
    "scripts/fm_zero_n_prepare.py",
    "scripts/fm_zero_n_worker.py",
    "scripts/fm_zero_n_run.py",
    "scripts/fm_zero_n_evaluate.py",
    "scripts/fm_zero_n_verify.py",
)


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


def selection_digest_and_rows(path: Path, keep_rows: bool) -> tuple[int, str, list[dict]]:
    """Digest TRAIN/VALIDATION only; reject FINAL_TEST before JSON parsing."""
    digest = hashlib.sha256()
    count = 0
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            if serialized_role(line) == "FINAL_TEST":
                continue
            row = json.loads(line)
            if row["role"] not in {"TRAIN", "VALIDATION"}:
                raise RuntimeError("unexpected selection role")
            encoded = json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
            if count:
                digest.update(b"\n")
            digest.update(encoded)
            count += 1
            if keep_rows:
                rows.append(row)
    return count, digest.hexdigest(), rows


def verify_frozen_manifest(manifest: dict, frozen: dict, input_seed: int, distribution_path: Path) -> None:
    checks = (
        (manifest.get("schema_version"), frozen["schema_version"], "schema version"),
        (manifest.get("seed"), input_seed, "seed"),
        (manifest.get("input", {}).get("ratings_sha256"), frozen["ratings_sha256"], "ratings digest"),
        (manifest.get("input", {}).get("movies_sha256"), frozen["movies_sha256"], "movies digest"),
        (manifest.get("users"), frozen["users"], "role users"),
        (manifest.get("targets"), frozen["targets"], "role targets"),
        (manifest.get("episodes"), frozen["episodes"], "episode digest"),
        (manifest.get("candidates"), frozen["candidates"], "candidate digest"),
        (manifest.get("final_test_seal_id"), frozen["final_test_seal_id"], "final-test seal"),
        (manifest.get("user_partition", {}).get("role_user_digests"), frozen["role_user_digests"], "role digests"),
        (manifest.get("distribution_report", {}).get("sha256"), frozen["distribution_report_sha256"], "distribution digest claim"),
        (file_pin(distribution_path)["sha256"], frozen["distribution_report_sha256"], "distribution file digest"),
    )
    for actual, expected, label in checks:
        if actual != expected:
            raise RuntimeError(f"frozen input {label} mismatch")


def select_frozen_contract(config: dict, manifest: dict, distribution_path: Path) -> tuple[str, dict]:
    for name in ("frozen_input", "fixture_input"):
        frozen = config[name]
        if manifest.get("users") == frozen["users"]:
            verify_frozen_manifest(manifest, frozen, int(config["input_seed"]), distribution_path)
            return name, frozen
    raise RuntimeError("input user counts match neither the frozen 10% input nor deterministic fixture")


class KeyDigest:
    MASK = (1 << 256) - 1

    def __init__(self) -> None:
        self.count = 0
        self.xor = 0
        self.total = 0
        self.square_total = 0

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


def stable_rank(seed: int, *parts: object) -> int:
    return int.from_bytes(hashlib.sha256(":".join(map(str, (seed, *parts))).encode()).digest()[:8], "big")


def role_for_user(seed: int, uid: int) -> str:
    bucket = stable_rank(seed, "role", uid) % 100
    return "TRAIN" if bucket < 70 else "VALIDATION" if bucket < 85 else "FINAL_TEST"


def load_train_movie_support(ratings_path: Path, seed: int, train_end: int = 1577836799) -> Counter:
    support: Counter = Counter()
    with ratings_path.open("r", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            if int(row["timestamp"]) <= train_end and role_for_user(seed, int(row["userId"])) == "TRAIN":
                support[int(row["movieId"])] += 1
    return support


def choose_training_rows(rows: list[dict], seed: int) -> list[dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        if row["role"] == "TRAIN":
            groups[(int(row["uid"]), int(row["target_movie_id"]), int(row["prediction_at"]))].append(row)
    selected = []
    for key, variants in sorted(groups.items()):
        variants.sort(key=lambda row: (int(row["n"]), row["n_bucket"]))
        selected.append(variants[stable_rank(seed, "training-variant", *key) % len(variants)])
    return selected


class BatchWriter:
    def __init__(self, path: Path, key_fields: tuple[str, ...], batch_size: int = 20_000):
        self.path = path
        self.batch_size = batch_size
        self.rows: list[dict] = []
        self.writer: pq.ParquetWriter | None = None
        self.count = 0
        self.key_fields = key_fields
        self.key_digest = KeyDigest()

    def append(self, row: dict) -> None:
        self.key_digest.update(tuple(row[field] for field in self.key_fields))
        self.rows.append(row)
        if len(self.rows) >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        if not self.rows:
            return
        table = pa.Table.from_pylist(self.rows)
        if self.writer is None:
            self.writer = pq.ParquetWriter(self.path, table.schema, compression="zstd")
        self.writer.write_table(table)
        self.count += len(self.rows)
        self.rows.clear()

    def close(self) -> None:
        self.flush()
        if self.writer is None:
            raise RuntimeError(f"no rows written: {self.path}")
        self.writer.close()


def load_movies(path: Path) -> dict:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return {int(row["movieId"]): parse_movie(int(row["movieId"]), row["title"], row["genres"])
                for row in csv.DictReader(stream)}


def base_row(episode: dict, candidate_movie_id: int, candidate_rank: int, label_state: str,
             label: float | None, is_target: bool) -> dict:
    return {
        "episode_id": ":".join(map(str, (episode["role"], episode["uid"], episode["target_movie_id"], episode["prediction_at"], episode["n"]))),
        "role": episode["role"],
        "uid": int(episode["uid"]),
        "target_movie_id": int(episode["target_movie_id"]),
        "candidate_movie_id": int(candidate_movie_id),
        "prediction_at": int(episode["prediction_at"]),
        "n": int(episode["n"]),
        "n_bucket": episode["n_bucket"],
        "total_history_count": int(episode["total_history_count"]),
        "supported_history_count": int(episode["supported_history_count"]),
        "label": label,
        "candidate_rank": int(candidate_rank),
        "label_state": label_state,
        "is_target": bool(is_target),
        "supported": True,
        "unsupported_reason": None,
    }


def feature_row(base: dict, episode: dict, movie, movies: dict, schema: dict, config: dict,
                diagnostics: dict[str, Counter], cache: dict | None = None, cache_key: tuple | None = None) -> dict:
    diagnostics["rows"] += 1
    if movie is None:
        diagnostics["unsupported_rows"] += 1
        return {**base, "feature_indices": [], "feature_values": [], "feature_size": len(schema["ordered_names"]),
                "candidate_genres": "", "supported": False, "unsupported_reason": "CANDIDATE_NOT_IN_FROZEN_MOVIELENS_CATALOG"}
    cached = cache.get(cache_key) if cache is not None and cache_key is not None else None
    if cached is None:
        features, observed = build_feature_map(
            episode, movie, movies, schema["vocabulary"], schema["normalizer"], float(config["positive_threshold"])
        )
        indices, values = vectorize(features, schema["ordered_names"])
        cached = ({"feature_indices": indices, "feature_values": values,
                   "feature_size": len(schema["ordered_names"]), "candidate_genres": "|".join(movie.genres)}, observed)
        if cache is not None and cache_key is not None:
            cache[cache_key] = cached
    payload, observed = cached
    for kind in ("oov", "missing", "active"):
        diagnostics[kind].update(observed[kind])
    return {**base, **payload}


def distribution_report(selection: list[dict], training: list[dict], diagnostics: dict,
                        vocabulary: dict, writers: dict[str, BatchWriter], input_manifest: dict,
                        movies: dict, schema: dict, train_support: Counter) -> dict:
    diag_document = {}
    for role, role_diag in diagnostics.items():
        rows = int(role_diag["rows"])
        diag_document[role] = {
            "transformed_rows": rows,
            "unsupported_rows": int(role_diag["unsupported_rows"]),
            "active_feature_occurrences_by_namespace": dict(sorted(role_diag["active"].items())),
            "oov_occurrences_by_namespace": dict(sorted(role_diag["oov"].items())),
            "missing_occurrences_by_namespace": dict(sorted(role_diag["missing"].items())),
            "oov_occurrences_per_transformed_row": {name: value / rows for name, value in sorted(role_diag["oov"].items())} if rows else {},
            "missing_occurrences_per_transformed_row": {name: value / rows for name, value in sorted(role_diag["missing"].items())} if rows else {},
        }
    result = {
        "schema_version": 1,
        "status": "DISTRIBUTION_SHIFT",
        "final_test": "SEALED_LABEL_AXES; STRUCTURAL_COUNTS_FROM_FROZEN_MANIFEST_ONLY",
        "roles": {},
        "training_variant_policy": "ONE_DETERMINISTIC_N_VARIANT_PER_USER_TARGET_SHA256_V1",
        "training_n_rows": dict(sorted(Counter(row["n_bucket"] for row in training).items())),
        "training_user_contribution": {},
        "feature_diagnostics_by_role": diag_document,
        "namespace_schema_feature_counts": {name: len(indices) for name, indices in sorted(schema["namespaces"].items())},
        "vocabulary_sizes": {name: len(values) for name, values in vocabulary["namespaces"].items()},
        "prepared_rows": {name: writer.count for name, writer in writers.items()},
    }
    contribution = Counter(int(row["uid"]) for row in training)
    contribution_by_n = defaultdict(Counter)
    for row in training:
        contribution_by_n[row["n_bucket"]][int(row["uid"])] += 1
    values = sorted(contribution.values())
    result["training_user_contribution"] = {
        "users": len(values), "minimum_rows": min(values), "maximum_rows": max(values),
        "mean_rows": sum(values) / len(values), "total_rows": sum(values),
        "by_n": {bucket: {"rows": sum(counts.values()), "users": len(counts),
                            "max_rows_per_user": max(counts.values())}
                 for bucket, counts in sorted(contribution_by_n.items())},
    }
    for role in ("TRAIN", "VALIDATION"):
        rows = [row for row in selection if row["role"] == role]
        histories = [event for row in rows for event in row["history"]]
        target_movies = [movies[int(row["target_movie_id"])] for row in rows]
        target_years = [movie.year for movie in target_movies if movie.year is not None]
        genre_counts = Counter(genre for movie in target_movies for genre in movie.genres)
        result["roles"][role] = {
            "users": len({int(row["uid"]) for row in rows}),
            "episodes": len(rows),
            "targets": len({(int(row["uid"]), int(row["target_movie_id"]), int(row["prediction_at"])) for row in rows}),
            "unique_movies": len({int(row["target_movie_id"]) for row in rows} | {int(event["movie_id"]) for event in histories}),
            "n_rows": dict(sorted(Counter(row["n_bucket"] for row in rows).items())),
            "total_history_count": {"minimum": min(int(row["total_history_count"]) for row in rows), "maximum": max(int(row["total_history_count"]) for row in rows)},
            "supported_history_count": {"minimum": min(int(row["supported_history_count"]) for row in rows), "maximum": max(int(row["supported_history_count"]) for row in rows)},
            "positive_history_events": sum(float(event["rating"]) >= 4.0 for event in histories),
            "negative_history_events": sum(float(event["rating"]) < 4.0 for event in histories),
            "target_rating": dict(sorted(Counter(f"{float(row['target_rating']):.1f}" for row in rows).items())),
            "target_relevance": dict(sorted(Counter("POSITIVE" if float(row["target_rating"]) >= 4.0 else "NEGATIVE" for row in rows).items())),
            "target_full_train_role_support_bucket": dict(sorted(Counter(
                "0" if train_support[int(row["target_movie_id"])] == 0 else "1-9" if train_support[int(row["target_movie_id"])] < 10 else "10-49" if train_support[int(row["target_movie_id"])] < 50 else "50+"
                for row in rows).items())),
            "target_release_year": {"minimum": min(target_years), "maximum": max(target_years)} if target_years else "INSUFFICIENT_SAMPLE",
            "target_event_time": {"minimum": min(int(row["target_event_at"]) for row in rows), "maximum": max(int(row["target_event_at"]) for row in rows)},
            "target_genres": dict(sorted(genre_counts.items())),
        }
    result["roles"]["VALIDATION"]["candidate_rows"] = writers["validation_candidates"].count
    result["roles"]["VALIDATION"]["model_supported_candidate_rows"] = writers["validation_candidates"].count - int(diagnostics["VALIDATION"]["unsupported_rows"])
    result["roles"]["FINAL_TEST"] = {
        "users": int(input_manifest["users"]["FINAL_TEST"]),
        "targets": int(input_manifest["targets"]["FINAL_TEST"]),
        "labels": "SEALED",
    }
    return result


def prepare(args: argparse.Namespace) -> dict:
    if args.output_root.exists():
        raise FileExistsError(f"output already exists: {args.output_root}")
    config = json.loads((ROOT / "experiments/fm-zero-n/config.json").read_text(encoding="utf-8"))
    input_manifest = json.loads((args.input_root / "manifest.json").read_text(encoding="utf-8"))
    if input_manifest["status"] != "PASS":
        raise RuntimeError("frozen input manifest is not PASS")
    input_contract_id, frozen_contract = select_frozen_contract(
        config, input_manifest, args.input_root / "split-distribution-report.json"
    )
    for name, expected in (("ratings.csv", input_manifest["input"]["ratings_sha256"]),
                           ("movies.csv", input_manifest["input"]["movies_sha256"])):
        if file_pin(args.movielens_root / name)["sha256"] != expected:
            raise RuntimeError(f"{name} digest mismatch")
    episode_count, episode_digest, selection = selection_digest_and_rows(args.input_root / "episodes.jsonl", True)
    candidate_count, candidate_digest, _ = selection_digest_and_rows(args.input_root / "candidates.jsonl", False)
    actual_selection = {
        "selection_episodes": {"rows": episode_count, "sha256": episode_digest},
        "selection_candidates": {"rows": candidate_count, "sha256": candidate_digest},
    }
    for name, actual in actual_selection.items():
        if actual != frozen_contract[name]:
            raise RuntimeError(f"frozen input {name} content digest mismatch")
    training = choose_training_rows(selection, int(config["input_seed"]))
    movies = load_movies(args.movielens_root / "movies.csv")
    train_support = load_train_movie_support(args.movielens_root / "ratings.csv", int(config["input_seed"]))
    vocabulary = fit_vocabulary(training, movies, float(config["positive_threshold"]))
    normalizer = fit_normalizer(training, movies, float(config["positive_threshold"]))
    schema = schema_document(vocabulary, normalizer)

    args.output_root.mkdir(parents=True)
    writers = {
        "train_targets": BatchWriter(args.output_root / "train-targets.parquet", ("episode_id", "candidate_movie_id")),
        "validation_targets": BatchWriter(args.output_root / "validation-targets.parquet", ("episode_id", "candidate_movie_id")),
        "validation_candidates": BatchWriter(args.output_root / "validation-candidates.parquet", ("episode_id", "candidate_movie_id")),
    }
    diagnostics = {role: {"oov": Counter(), "missing": Counter(), "active": Counter(), "rows": 0, "unsupported_rows": 0}
                   for role in ("TRAIN", "VALIDATION")}
    for episode in training:
        movie = movies.get(int(episode["target_movie_id"]))
        if movie is None:
            raise RuntimeError("TRAIN target is unsupported; rows may not be silently dropped")
        state = "POSITIVE_OBSERVED" if float(episode["target_rating"]) >= float(config["positive_threshold"]) else "NEGATIVE_OBSERVED"
        base = base_row(episode, int(episode["target_movie_id"]), 0, state, float(episode["target_rating"]), True)
        writers["train_targets"].append(feature_row(base, episode, movie, movies, schema, config, diagnostics["TRAIN"]))

    validation_rows = [row for row in selection if row["role"] == "VALIDATION"]
    validation_groups: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for episode in validation_rows:
        movie = movies.get(int(episode["target_movie_id"]))
        if movie is None:
            raise RuntimeError("VALIDATION target is unsupported; rows may not be silently dropped")
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
        if current_key is None:
            return
        nonlocal cache_uid, feature_cache
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
                    base, episode, movies.get(movie_id), movies, schema, config, diagnostics["VALIDATION"], feature_cache, cache_key
                ))

    with (args.input_root / "candidates.jsonl").open("r", encoding="utf-8") as stream:
        for line in stream:
            role = serialized_role(line)
            if role == "FINAL_TEST" or role == "TRAIN":
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

    report = distribution_report(selection, training, diagnostics, vocabulary, writers, input_manifest, movies, schema, train_support)
    (args.output_root / "feature-schema.json").write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (args.output_root / "split-distribution-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    source_files = {name: file_pin(ROOT / name) for name in SOURCE_FILES if (ROOT / name).exists()}
    source_digest = hashlib.sha256(json.dumps(source_files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    files = {path.name: file_pin(path) for path in sorted(args.output_root.glob("*.parquet"))}
    files.update({name: file_pin(args.output_root / name) for name in ("feature-schema.json", "split-distribution-report.json")})
    manifest = {
        "schema_version": 1,
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_revision": args.source_revision,
        "input_contract_id": input_contract_id,
        "input_manifest": file_pin(args.input_root / "manifest.json"),
        "input_digests": {
            "frozen_global_episodes_claim": input_manifest["episodes"]["sha256"],
            "frozen_global_candidates_claim": input_manifest["candidates"]["sha256"],
            "selection_episodes": {"rows": episode_count, "sha256": episode_digest},
            "selection_candidates": {"rows": candidate_count, "sha256": candidate_digest},
            "split_distribution_report": input_manifest["distribution_report"]["sha256"]},
        "feature_schema": {"profile_digest": schema["profile_digest"],
                           "ordered_names_sha256": schema["ordered_names_sha256"],
                           "vocabulary_digest": vocabulary["digest"], "normalizer_digest": normalizer["digest"]},
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
