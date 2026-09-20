"""Prepare FM-v4 sparse target rows without exposing validation labels or raw ratings."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from fm_v4_features import (
    ExternalTagStore,
    build_feature_map,
    fit_genre_vocabulary,
    fit_global_rating_mean,
    fit_scaler,
    parse_movie,
    schema_document,
    vectorize,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "experiments/fm-v4/config.json"
SOURCE_FILES = (
    "experiments/fm-v4/config.json",
    "experiments/fm-v4/DESIGN.md",
    "performance/fm-zero-n.Dockerfile",
    "scripts/gbt_zero_n_build_input.py",
    "scripts/fm_v2_train.py",
    "scripts/fm_v4_build_input.py",
    "scripts/fm_v4_isolate_input.py",
    "scripts/fm_v4_features.py",
    "scripts/fm_v4_prepare.py",
    "scripts/fm_v4_train.py",
    "scripts/fm_v4_run.py",
    "scripts/fm_v4_evaluate.py",
    "scripts/fm_v4_verify.py",
    "scripts/fm_zero_n_prepare.py",
    "scripts/test_fm_v4_build_input.py",
    "scripts/test_fm_v4_features.py",
    "scripts/test_fm_v4_train.py",
    "scripts/test_fm_v4_verify.py",
)


def file_pin(path: Path) -> dict[str, int | str]:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def _canonical_target_key(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _inner_split(uid: int, split_config: dict) -> str:
    token = f"{split_config['inner_user_salt']}:{uid}".encode()
    bucket = int.from_bytes(hashlib.sha256(token).digest(), "big") % int(split_config["inner_user_holdout_modulus"])
    return "HOLDOUT" if bucket == int(split_config["inner_user_holdout_bucket"]) else "FIT"


def annotate_training_rows(rows: list[dict], split_config: dict) -> list[dict]:
    """Assign a user-disjoint split and equal user/target/variant weights."""
    targets: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for row in rows:
        if row["role"] != "TRAIN":
            raise RuntimeError("training annotator received a non-TRAIN row")
        targets[(int(row["uid"]), _canonical_target_key(row["target_key"]))].append(row)
    by_user: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for key, variants in targets.items():
        by_user[key[0]].append(key)
        labels = {float(row["target_rating"]) for row in variants}
        if len(labels) != 1:
            raise RuntimeError(f"target variants disagree on label: {key}")
        n_values = [int(row["n"]) for row in variants]
        if len(n_values) != len(set(n_values)):
            raise RuntimeError(f"duplicate N variant: {key}")

    active_targets_by_user = {
        uid: [key for key in keys if any(int(row["n"]) > 0 for row in targets[key])]
        for uid, keys in by_user.items()
    }
    annotated: list[dict] = []
    for uid in sorted(by_user):
        user_targets = by_user[uid]
        active_targets = active_targets_by_user[uid]
        for key in sorted(user_targets):
            variants = sorted(targets[key], key=lambda row: (int(row["n"]), str(row["n_bucket"])))
            positive_count = sum(int(row["n"]) > 0 for row in variants)
            additive_weight = 1.0 / (len(user_targets) * len(variants))
            for row in variants:
                interaction_weight = (1.0 / (len(active_targets) * positive_count)
                                      if int(row["n"]) > 0 and positive_count and active_targets else 0.0)
                annotated.append({
                    **row,
                    "target_key": key[1],
                    "internal_split": _inner_split(uid, split_config),
                    "weight_additive": additive_weight,
                    "weight_interaction": interaction_weight,
                })
    return annotated


def _validate_episode(row: dict, role: str) -> dict:
    required = {"role", "target_key", "uid", "target_movie_id", "target_event_at", "prediction_at",
                "n", "n_bucket", "total_history_count", "history"}
    if role == "TRAIN":
        required.add("target_rating")
    missing = required - set(row)
    if missing:
        raise RuntimeError(f"{role} episode missing fields: {sorted(missing)}")
    if row["role"] != role:
        raise RuntimeError(f"{role} file contains {row['role']} row")
    if role == "VALIDATION" and ({"target_rating", "label"} & set(row)):
        raise RuntimeError("validation feature episode exposes a label")
    if int(row["n"]) != len(row["history"]):
        raise RuntimeError("episode N differs from serialized recent history length")
    keys = [(int(event["event_at"]), int(event["movie_id"])) for event in row["history"]]
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        raise RuntimeError("history is not strict chronological unique-event order")
    target_event_key = int(row["target_event_at"]), int(row["target_movie_id"])
    if any(key >= target_event_key for key in keys):
        raise RuntimeError("history contains target/future event")
    if int(row["prediction_at"]) != int(row["target_event_at"]):
        raise RuntimeError("prediction_at must equal target_event_at")
    normalized = dict(row)
    normalized["target_key"] = _canonical_target_key(row["target_key"])
    return normalized


def _load_episodes(path: Path, role: str) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                rows.append(_validate_episode(json.loads(line), role))
    if not rows:
        raise RuntimeError(f"{role} episode file is empty")
    return rows


def _expected_file_pin(manifest: dict, filename: str, role: str) -> dict:
    if isinstance(manifest.get("files"), dict) and filename in manifest["files"]:
        return manifest["files"][filename]
    key = filename.removesuffix(".jsonl").replace("-", "_")
    if isinstance(manifest.get(key), dict):
        return manifest[key]
    if isinstance(manifest.get("episodes"), dict) and isinstance(manifest["episodes"].get(role), dict):
        return manifest["episodes"][role]
    raise RuntimeError(f"isolated manifest does not pin {filename}")


def verify_isolated_input(input_root: Path, config: dict) -> tuple[dict, dict]:
    manifest_path = input_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "PASS":
        raise RuntimeError("isolated input manifest is not PASS")
    if manifest.get("final_test_materialized") not in (None, False):
        raise RuntimeError("isolated input claims FINAL_TEST materialization")
    pins = {}
    for filename, role in (("train-episodes.jsonl", "TRAIN"),
                           ("validation-episodes.jsonl", "VALIDATION")):
        path = input_root / filename
        actual = file_pin(path)
        expected = _expected_file_pin(manifest, filename, role)
        if actual != {"bytes": int(expected["bytes"]), "sha256": expected["sha256"]}:
            raise RuntimeError(f"isolated {filename} pin mismatch")
        pins[filename] = actual
    configured_pin = config.get("pins", {}).get("isolated_manifest")
    actual_manifest_pin = file_pin(manifest_path)
    if configured_pin is not None and actual_manifest_pin != configured_pin:
        raise RuntimeError("isolated manifest differs from frozen config pin")
    return manifest, {"manifest.json": actual_manifest_pin, **pins}


def load_movies(path: Path) -> dict:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return {int(row["movieId"]): parse_movie(int(row["movieId"]), row["title"], row["genres"])
                for row in csv.DictReader(stream)}


def load_tag_store(path: Path, excluded_uids: set[int]) -> ExternalTagStore:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return ExternalTagStore(csv.DictReader(stream), excluded_uids)


def _base_row(episode: dict, training: bool) -> dict:
    target_key = _canonical_target_key(episode["target_key"])
    row = {
        "episode_id": f"{target_key}:{int(episode['n'])}",
        "target_key": target_key,
        "role": episode["role"],
        "uid": int(episode["uid"]),
        "target_movie_id": int(episode["target_movie_id"]),
        "prediction_at": int(episode["prediction_at"]),
        "n": int(episode["n"]),
        "n_bucket": str(episode["n_bucket"]),
        "total_history_count": int(episode["total_history_count"]),
        "supported": True,
        "unsupported_reason": None,
    }
    if training:
        row.update({
            "label": float(episode["target_rating"]),
            "internal_split": episode["internal_split"],
            "weight_additive": float(episode["weight_additive"]),
            "weight_interaction": float(episode["weight_interaction"]),
        })
    return row


def _feature_row(episode: dict, training: bool, movies: dict, tag_store: ExternalTagStore,
                 schema: dict, feature_config: dict, diagnostics: dict) -> dict:
    movie = movies.get(int(episode["target_movie_id"]))
    if movie is None:
        raise RuntimeError("target movie is absent from the frozen catalog")
    features, observed = build_feature_map(
        episode, movie, movies, schema["genre_vocabulary"], tag_store, schema["tag_vocabulary"],
        float(schema["global_rating_mean"]["value"]), schema["scaler"], feature_config,
    )
    indices, values = vectorize(features, schema["ordered_names"])
    if values and (min(values) < -1.0 or max(values) > 1.0):
        raise RuntimeError("prepared feature outside [-1,1]")
    diagnostics["rows"] += 1
    for kind in ("active", "missing", "oov", "clipped"):
        diagnostics[kind].update(observed[kind])
    return {
        **_base_row(episode, training),
        "feature_indices": indices,
        "feature_values": values,
        "feature_size": len(schema["ordered_names"]),
        "candidate_genres": "|".join(movie.genres),
    }


def _assert_weights(training: list[dict]) -> dict:
    additive = defaultdict(float)
    interaction = defaultdict(float)
    target_additive = defaultdict(float)
    target_interaction = defaultdict(float)
    for row in training:
        uid = int(row["uid"])
        key = uid, row["target_key"]
        additive[uid] += float(row["weight_additive"])
        interaction[uid] += float(row["weight_interaction"])
        target_additive[key] += float(row["weight_additive"])
        target_interaction[key] += float(row["weight_interaction"])
        if int(row["n"]) == 0 and float(row["weight_interaction"]) != 0.0:
            raise RuntimeError("N=0 received interaction weight")
    if any(abs(value - 1.0) > 1e-12 for value in additive.values()):
        raise RuntimeError("additive user weights do not sum to one")
    active_users = {int(row["uid"]) for row in training if int(row["n"]) > 0}
    if any(abs(interaction[uid] - 1.0) > 1e-12 for uid in active_users):
        raise RuntimeError("interaction active-user weights do not sum to one")
    return {
        "additive_user_weight_sum_min": min(additive.values()),
        "additive_user_weight_sum_max": max(additive.values()),
        "interaction_active_user_weight_sum_min": min(interaction[uid] for uid in active_users),
        "interaction_active_user_weight_sum_max": max(interaction[uid] for uid in active_users),
        "target_additive_weight_sum_min": min(target_additive.values()),
        "target_additive_weight_sum_max": max(target_additive.values()),
        "target_interaction_weight_sum_min": min(target_interaction.values()),
        "target_interaction_weight_sum_max": max(target_interaction.values()),
    }


def prepare(args: argparse.Namespace) -> dict:
    import pyarrow.parquet as pq
    from fm_zero_n_prepare import BatchWriter

    if os.environ.get("FM_V4_PREP_OUTPUT_MOUNT_ISOLATED") != "true":
        raise RuntimeError("FM-v4 prepare requires an isolated output mount")
    if not args.output_root.is_dir() or any(args.output_root.iterdir()):
        raise FileExistsError("prepare output must be an existing empty dedicated directory")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    input_manifest, input_pins = verify_isolated_input(args.input_root, config)
    for label, path in (("movies", args.movies_path), ("tags", args.tags_path)):
        if file_pin(path) != config["source"][label]:
            raise RuntimeError(f"{label}.csv differs from config source pin")

    train_rows = _load_episodes(args.input_root / "train-episodes.jsonl", "TRAIN")
    validation_rows = _load_episodes(args.input_root / "validation-episodes.jsonl", "VALIDATION")
    training = annotate_training_rows(train_rows, config["split"])
    weight_report = _assert_weights(training)
    fit_rows = [row for row in training if row["internal_split"] == "FIT"]
    holdout_rows = [row for row in training if row["internal_split"] == "HOLDOUT"]
    if not fit_rows or not holdout_rows:
        raise RuntimeError("user-disjoint internal FIT/HOLDOUT split is empty")

    selected_uids = {int(row["uid"]) for row in train_rows + validation_rows}
    movies = load_movies(args.movies_path)
    tag_store = load_tag_store(args.tags_path, selected_uids)
    feature_config = config["feature"]
    tag_vocabulary = tag_store.vocabulary(
        int(feature_config["tag_vocabulary_timestamp_exclusive"]),
        int(feature_config["tag_min_movie_document_frequency"]),
        int(feature_config["tag_vocabulary_size"]),
    )
    if len(tag_vocabulary["values"]) != int(feature_config["tag_vocabulary_size"]):
        raise RuntimeError("external tag corpus did not produce the fixed vocabulary size")
    genre_vocabulary = fit_genre_vocabulary(fit_rows, movies)
    global_mean = fit_global_rating_mean(fit_rows)
    scaler = fit_scaler(fit_rows, movies, genre_vocabulary, tag_store, tag_vocabulary,
                        float(global_mean["value"]), feature_config)
    schema = schema_document(genre_vocabulary, tag_vocabulary, scaler, global_mean,
                             feature_config, config["model"])

    writers = {
        "train_targets": BatchWriter(args.output_root / "train-targets.parquet", ("episode_id",)),
        "validation_targets": BatchWriter(args.output_root / "validation-targets.parquet", ("episode_id",)),
    }
    diagnostics = {role: {kind: Counter() for kind in ("active", "missing", "oov", "clipped")} |
                   {"rows": 0} for role in ("FIT", "HOLDOUT", "VALIDATION")}
    for row in training:
        writers["train_targets"].append(_feature_row(
            row, True, movies, tag_store, schema, feature_config, diagnostics[row["internal_split"]]
        ))
    for row in validation_rows:
        writers["validation_targets"].append(_feature_row(
            row, False, movies, tag_store, schema, feature_config, diagnostics["VALIDATION"]
        ))
    for writer in writers.values():
        writer.close()

    validation_schema = pq.ParquetFile(args.output_root / "validation-targets.parquet").schema_arrow.names
    if "label" in validation_schema or "target_rating" in validation_schema:
        raise RuntimeError("validation prepared rows expose labels")
    interaction_indices = set()
    gate_indices = set()
    for field in schema["interaction_fields"].values():
        interaction_indices.update(field["q_indices"])
        gate_indices.add(field["gate_index"])
    n0_violations = 0
    for batch in pq.ParquetFile(args.output_root / "train-targets.parquet").iter_batches(
        columns=["n", "weight_interaction", "feature_indices"]
    ):
        for n, weight, indices in zip(*(column.to_pylist() for column in batch.columns)):
            if int(n) == 0 and (float(weight) != 0.0 or interaction_indices.intersection(indices)
                                or gate_indices.intersection(indices)):
                n0_violations += 1
    if n0_violations:
        raise RuntimeError("N=0 activates interaction inputs/gates")

    expected = config["expected_census"]
    train_target_variants: dict[tuple[int, str], list[int]] = defaultdict(list)
    validation_target_variants: dict[tuple[int, str], list[int]] = defaultdict(list)
    for row in training:
        train_target_variants[(int(row["uid"]), row["target_key"])].append(int(row["n"]))
    for row in validation_rows:
        validation_target_variants[(int(row["uid"]), row["target_key"])].append(int(row["n"]))
    observed = {
        "TRAIN": {"active_users": len({uid for (uid, key), values in train_target_variants.items()
                                        if any(n > 0 for n in values)}),
                  "active_targets": sum(any(n > 0 for n in values)
                                        for values in train_target_variants.values()),
                  "n0_targets": sum(not any(n > 0 for n in values)
                                    for values in train_target_variants.values())},
        "VALIDATION": {"active_users": len({uid for (uid, key), values in validation_target_variants.items()
                                             if any(n > 0 for n in values)}),
                       "active_targets": sum(any(n > 0 for n in values)
                                             for values in validation_target_variants.values()),
                       "n0_targets": sum(not any(n > 0 for n in values)
                                         for values in validation_target_variants.values())},
    }
    for role in ("TRAIN", "VALIDATION"):
        for name, value in observed[role].items():
            if value != int(expected[role][name]):
                raise RuntimeError(f"{role} {name} census mismatch: {value} != {expected[role][name]}")

    report = {
        "schema_version": 4,
        "status": "PASS",
        "internal_split": "USER_DISJOINT_SHA256_MOD5",
        "internal_split_rows": dict(sorted(Counter(row["internal_split"] for row in training).items())),
        "internal_split_users": {split: len({int(row["uid"]) for row in training
                                             if row["internal_split"] == split})
                                 for split in ("FIT", "HOLDOUT")},
        "census": observed,
        "weights": weight_report,
        "preprocessing": {"genre_vocabulary": "TRAIN_INNER_FIT_ONLY",
                          "global_rating_mean": "TRAIN_INNER_FIT_ONLY",
                          "numeric_scaler": "TRAIN_INNER_FIT_ONLY",
                          "tag_vocabulary": "EXTERNAL_USERS_PRE_2017_ONLY"},
        "external_tags": {"raw_rows": tag_store.raw_rows, "normalized_rows": tag_store.normalized_rows,
                          "deduplicated_rows": tag_store.deduplicated_rows,
                          "excluded_selected_contributors": len(selected_uids)},
        "n0_interaction_activation_violations": n0_violations,
        "validation_label_columns": [],
        "candidate_movie_id_feature": False,
        "user_id_feature": False,
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
        "schema_version": 4,
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_revision": args.source_revision,
        "input_manifest": input_pins["manifest.json"],
        "input_files": {name: pin for name, pin in input_pins.items() if name != "manifest.json"},
        "catalog_files": {"movies.csv": file_pin(args.movies_path), "tags.csv": file_pin(args.tags_path)},
        "feature_schema": {"profile_digest": schema["profile_digest"],
                           "ordered_names_sha256": schema["ordered_names_sha256"],
                           "genre_vocabulary_digest": genre_vocabulary["digest"],
                           "tag_vocabulary_digest": tag_vocabulary["digest"],
                           "scaler_digest": scaler["digest"],
                           "global_rating_mean_digest": global_mean["digest"]},
        "rows": {name: writer.count for name, writer in writers.items()},
        "row_key_multisets": {name: writer.key_digest.document() for name, writer in writers.items()},
        "source_bundle": {"algorithm": "ORDERED_FILE_PINS_V1", "digest": source_digest,
                          "files": source_files},
        "files": files,
        "failed_rows": 0,
        "container_mount_contract": {
            "network": "none", "workspace": "/workspace:ro", "isolated_input": "/input:ro",
            "movies_csv": "/catalog/movies.csv:ro", "tags_csv": "/catalog/tags.csv:ro",
            "output": "/output:rw", "raw_ratings_visible": False,
            "source_input_with_final_test_visible": False, "validation_labels_visible": False,
            "output_parent_or_siblings_visible": False,
        },
        "final_test": "NOT_PRESENT_IN_INPUT_OR_PREPARED_ARTIFACTS",
        "final_test_opened": False,
        "validation_labels_opened": False,
        "validation_labels": "NOT_PRESENT_IN_INPUT_OR_PREPARED_ARTIFACTS",
    }
    (args.output_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--movies-path", type=Path, required=True)
    parser.add_argument("--tags-path", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--source-revision", required=True)
    prepare(parser.parse_args())


if __name__ == "__main__":
    main()
