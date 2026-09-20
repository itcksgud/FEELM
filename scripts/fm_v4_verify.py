"""Independently verify the complete FM-v4 source-to-decision artifact lineage."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from scipy import sparse

from fm_v2_train import file_pin, frame_to_csr, tree_pin
from fm_v4_build_input import user_digest
from fm_v4_evaluate import calculator_bundle, evaluate_frame, join_frozen_predictions_and_labels
from fm_v4_train import (EXPECTED_PREPARED_FILES, EXPECTED_PREPARE_MOUNT_CONTRACT,
                         EXPECTED_SOURCE_BUNDLE_FILES, FIT_MOUNT_CONTRACT, FIELDS,
                         PROFILE_ADDITIVE, PROFILE_FACTOR)

SOURCE_FILES = {"manifest.json", "train-episodes.jsonl", "validation-episodes.jsonl",
                "validation-labels.jsonl"}
ISOLATED_FILES = {"manifest.json", "train-episodes.jsonl", "validation-episodes.jsonl"}


def require(condition: bool, message: str, checks: list[str]) -> None:
    if not condition:
        raise RuntimeError(message)
    checks.append(message)


def normalized_pin(value: dict) -> dict:
    return {"bytes": int(value["bytes"]), "sha256": str(value["sha256"])}


def ordered_digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class KeyDigest:
    MASK = (1 << 256) - 1

    def __init__(self) -> None:
        self.count = self.xor = self.total = self.square_total = 0

    def update(self, values: tuple) -> None:
        number = int.from_bytes(hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).digest(), "big")
        self.count += 1
        self.xor ^= number
        self.total = (self.total + number) & self.MASK
        self.square_total = (self.square_total + number * number) & self.MASK

    def document(self) -> dict:
        return {"algorithm": "SHA256_KEY_MULTISET_XOR_SUM_SUMSQ_V1", "count": self.count,
                "xor": f"{self.xor:064x}", "sum_mod_2_256": f"{self.total:064x}",
                "sum_squares_mod_2_256": f"{self.square_total:064x}"}


def recent_n_values(history_length: int) -> list[int]:
    cap = min(history_length, 50)
    return sorted({0, cap, *(value for value in (1, 2, 4, 7, 15, 25, 40, 50) if value <= cap)})


def scan_episode_file(path: Path, role: str) -> dict:
    """Hash and validate an episode file in one streaming pass."""
    digest, key_digest = hashlib.sha256(), KeyDigest()
    byte_count = rows = active_targets = n0_targets = 0
    users, targets, episodes, active_users = set(), set(), set(), set()
    current_key, current_uid, current_total = None, -1, -1
    current_ns: list[int] = []
    previous_history: list[str] = []

    def finish_target() -> None:
        nonlocal active_targets, n0_targets
        if current_key is None:
            return
        if current_ns != recent_n_values(current_total):
            raise RuntimeError(f"{role} target has wrong recent-N variants: {current_key}")
        if any(value > 0 for value in current_ns):
            active_targets += 1
            active_users.add(current_uid)
        else:
            n0_targets += 1

    with path.open("rb") as stream:
        for raw in stream:
            digest.update(raw)
            byte_count += len(raw)
            if not raw.strip():
                continue
            row = json.loads(raw)
            rows += 1
            required = {"uid", "target_key", "episode_id", "target_movie_id", "target_event_at",
                        "prediction_at", "n", "n_bucket", "total_history_count", "history"}
            if row.get("role") != role or not required <= row.keys():
                raise RuntimeError(f"{path.name} role/field contract mismatch")
            if (role == "TRAIN") != ("target_rating" in row):
                raise RuntimeError(f"{path.name} target-rating boundary violation")
            uid, n = int(row["uid"]), int(row["n"])
            target_key = str(row["target_key"])
            expected_key = f"{role}:{uid}:{int(row['target_event_at'])}:{int(row['target_movie_id'])}"
            if (target_key != expected_key or row["episode_id"] != f"{target_key}:{n}" or
                    int(row["prediction_at"]) != int(row["target_event_at"])):
                raise RuntimeError(f"{path.name} target/episode identity mismatch")
            history = row["history"]
            if (len(history) != n or int(row.get("supported_history_count", -1)) != n or n > 50 or
                    int(row.get("history_cap", -1)) != 50 or str(row["n_bucket"]) != str(n)):
                raise RuntimeError(f"{path.name} recent-N size contract mismatch")
            history_keys = [(int(item["event_at"]), int(item["movie_id"])) for item in history]
            if history_keys != sorted(history_keys) or any(
                    key >= (int(row["target_event_at"]), int(row["target_movie_id"])) for key in history_keys):
                raise RuntimeError(f"{path.name} history is non-temporal/non-chronological")
            event_ids = [str(item["event_id"]) for item in history]
            if target_key != current_key:
                finish_target()
                if target_key in targets:
                    raise RuntimeError(f"{path.name} target variants are non-contiguous")
                targets.add(target_key)
                current_key, current_uid = target_key, uid
                current_total, current_ns, previous_history = int(row["total_history_count"]), [], []
            if int(row["total_history_count"]) != current_total or uid != current_uid:
                raise RuntimeError(f"{path.name} target variants disagree")
            if current_ns and n <= current_ns[-1]:
                raise RuntimeError(f"{path.name} N variants are not increasing")
            if previous_history and previous_history != event_ids[-len(previous_history):]:
                raise RuntimeError(f"{path.name} histories are not nested recent suffixes")
            if n == 0 and event_ids:
                raise RuntimeError(f"{path.name} N=0 history is non-empty")
            previous_history, current_ns = event_ids, [*current_ns, n]
            users.add(uid)
            if row["episode_id"] in episodes:
                raise RuntimeError(f"{path.name} duplicate episode_id")
            episodes.add(row["episode_id"])
            key_digest.update((row["episode_id"],))
    finish_target()
    return {"file": {"rows": rows, "bytes": byte_count, "sha256": digest.hexdigest()},
            "users": users, "targets": targets, "key_multiset": key_digest.document(),
            "active_users": active_users, "active_targets": active_targets, "n0_targets": n0_targets}


def scan_labels(path: Path) -> dict:
    digest, labels = hashlib.sha256(), {}
    byte_count = rows = 0
    with path.open("rb") as stream:
        for raw in stream:
            digest.update(raw)
            byte_count += len(raw)
            if not raw.strip():
                continue
            row = json.loads(raw)
            if set(row) != {"target_key", "target_rating"}:
                raise RuntimeError("sealed labels have unexpected fields")
            key, value = str(row["target_key"]), float(row["target_rating"])
            if key in labels or not math.isfinite(value):
                raise RuntimeError("sealed labels are duplicate/non-finite")
            labels[key], rows = value, rows + 1
    return {"file": {"rows": rows, "bytes": byte_count, "sha256": digest.hexdigest()}, "labels": labels}


def parquet_key_multiset(path: Path) -> dict:
    digest = KeyDigest()
    for batch in pq.ParquetFile(path).iter_batches(columns=["episode_id"]):
        for value in batch.column(0).to_pylist():
            digest.update((value,))
    return digest.document()


def payload_digest(value: dict, digest_key: str = "digest") -> str:
    payload = {key: item for key, item in value.items() if key != digest_key}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def verify_source_and_isolation(args, config: dict, checks: list[str]) -> tuple[dict, dict, dict]:
    source_path, isolated_path = args.source_root / "manifest.json", args.isolated_root / "manifest.json"
    require({item.name for item in args.source_root.iterdir()} == SOURCE_FILES, "source inventory is exact", checks)
    require({item.name for item in args.isolated_root.iterdir()} == ISOLATED_FILES, "isolated inventory is exact", checks)
    require(file_pin(source_path) == normalized_pin(config["pins"]["source_manifest"]),
            "source manifest matches config pin", checks)
    require(file_pin(isolated_path) == normalized_pin(config["pins"]["isolated_manifest"]),
            "isolated manifest matches config pin", checks)
    source, isolated = (json.loads(path.read_text(encoding="utf-8")) for path in (source_path, isolated_path))
    require(source.get("status") == isolated.get("status") == "PASS", "source/isolation statuses are PASS", checks)
    require(source.get("contract") == "FM_V4_LABEL_SEPARATED_ROLLING_RECENT_N_SOURCE_V1" and
            isolated.get("contract") == "FM_V4_PHYSICALLY_ISOLATED_LABEL_BLIND_TRAIN_VALIDATION_V1",
            "source/isolation contracts are exact", checks)
    require(int(source["seed"]) == int(isolated["seed"]) == int(config["split"]["seed"]),
            "source/isolation seeds equal config", checks)
    raw_pins = {name: normalized_pin(config["source"][name]) for name in ("ratings", "movies", "tags")}
    require(source["input"] == isolated["source_input"] == raw_pins,
            "raw pins link config/source/isolation", checks)
    require(source["config_contract"]["expected_census"] == config["expected_census"] and
            source["cutoffs"] == config["split"]["windows"], "source census/windows equal config", checks)
    require(source["target_policy"] ==
            "UP_TO_4_LABEL_BLIND_SHA256_SELECTED_TARGETS_PER_USER_USING_TIMESTAMP_AND_MOVIE_ID" and
            source["prediction_at_policy"] == "ACTUAL_TARGET_EVENT_TIMESTAMP" and
            source["history_policy"] == {
                "eligibility": "EVENT_(TIMESTAMP,MOVIE_ID)_STRICTLY_LESS_THAN_TARGET_KEY",
                "selection": "MOST_RECENT_N_THEN_RESTORED_TO_CHRONOLOGICAL_ORDER",
                "always_include_zero_history_variant": True,
                "base_n_values": config["split"]["n_values"][1:],
                "include_capped_full_history_variant": True,
                "capped_full_history_maximum": config["split"]["maximum_history"],
                "variant_set_formula":
                    "{0} UNION {K IN BASE_N_VALUES WHERE K <= MIN(H,50)} UNION {MIN(H,50)}",
            }, "source target/time/recent-N policies equal the frozen design", checks)

    train = scan_episode_file(args.source_root / "train-episodes.jsonl", "TRAIN")
    validation = scan_episode_file(args.source_root / "validation-episodes.jsonl", "VALIDATION")
    labels = scan_labels(args.source_root / "validation-labels.jsonl")
    require(train["file"] == source["files"]["train_episodes"], "source TRAIN hash/count recomputes", checks)
    require(validation["file"] == source["files"]["validation_episodes"],
            "source VALIDATION hash/count recomputes", checks)
    require(labels["file"] == source["files"]["validation_labels"] and
            set(labels["labels"]) == validation["targets"], "sealed labels hash and target set recompute", checks)
    boundary = source["label_boundary"]
    require(boundary == {"training_target_rating_in_episode": True,
                         "validation_target_rating_in_episode": False,
                         "validation_label_fields": ["target_key", "target_rating"],
                         "validation_label_rows": len(labels["labels"]), "final_test_materialized": False,
                         "final_test_labels_materialized": False}, "source label boundary is exact", checks)
    require(source.get("candidate_ranking_files_materialized") is False,
            "source has no ranking candidates", checks)
    for role, scan in (("TRAIN", train), ("VALIDATION", validation)):
        expected = config["expected_census"][role]
        require(len(scan["users"]) == source["users"][role] == expected["eligible_users"] and
                len(scan["targets"]) == source["targets"][role] and
                scan["file"]["rows"] == source["episodes"][role], f"source {role} counts recompute", checks)
        require(len(scan["active_users"]) == expected["active_users"] and
                scan["active_targets"] == expected["active_targets"] and scan["n0_targets"] == expected["n0_targets"],
                f"source {role} active/N0 census recomputes", checks)
        require(user_digest(scan["users"]) == source["user_partition"]["role_user_digests"][role],
                f"source {role} user digest recomputes", checks)
    census = source["eligible_census"]
    require(census["full_train_population_saturated"] is True and
            census["full_fresh_validation_population_saturated"] is True and
            census["final_test_mode"] == "CENSUS_ONLY_NO_EPISODES_NO_LABELS" and
            census["raw_eligible_users"]["FINAL_TEST"] == config["expected_census"]["FINAL_TEST"]["eligible_users"],
            "full census and unopened FINAL_TEST contract match config", checks)
    prior, expected_prior = source["prior_validation_exclusion"], config["source"]["prior_validation"]
    require(prior["sources"]["622"]["users"] == expected_prior["seed_622"]["users"] and
            prior["sources"]["622"]["user_digest"] == expected_prior["seed_622"]["user_digest"] and
            prior["sources"]["3623"]["users"] == expected_prior["seed_3623"]["users"] and
            prior["sources"]["3623"]["user_digest"] == expected_prior["seed_3623"]["user_digest"] and
            prior["source_intersection_users"] == expected_prior["expected_intersection_users"] and
            prior["union_users"] == expected_prior["expected_union_users"] and
            prior["new_validation_intersection_users"] == 0 and
            prior["new_validation_user_digest"] == user_digest(validation["users"]),
            "prior-validation exclusion is exact", checks)

    isolated_train = scan_episode_file(args.isolated_root / "train-episodes.jsonl", "TRAIN")
    isolated_validation = scan_episode_file(args.isolated_root / "validation-episodes.jsonl", "VALIDATION")
    require(isolated["source_manifest"] == file_pin(source_path), "isolation links exact source manifest", checks)
    require(isolated["source_episode_files"] == {"train_episodes": source["files"]["train_episodes"],
                                                  "validation_episodes": source["files"]["validation_episodes"]},
            "isolation links exact source episodes", checks)
    require(isolated["sealed_validation_labels"] == source["files"]["validation_labels"] and
            isolated["prior_validation_exclusion"] == prior and isolated["eligible_census"] == census,
            "isolation preserves sealed-label/census/prior-validation lineage", checks)
    require(isolated["roles"] == ["TRAIN", "VALIDATION"] and
            isolated["users"] == {"TRAIN": len(isolated_train["users"]),
                                  "VALIDATION": len(isolated_validation["users"])} and
            isolated["targets"] == {"TRAIN": len(isolated_train["targets"]),
                                    "VALIDATION": len(isolated_validation["targets"])} and
            isolated["episodes"] == {"TRAIN": isolated_train["file"]["rows"],
                                     "VALIDATION": isolated_validation["file"]["rows"]},
            "isolated roles/users/targets/episodes independently recompute", checks)
    require(isolated["target_policy"] == source["target_policy"] and
            isolated["prediction_at_policy"] == source["prediction_at_policy"] and
            isolated["history_policy"] == source["history_policy"] and
            isolated["candidate_ranking_files_materialized"] is False,
            "isolation preserves target/time/history and no-ranking contracts", checks)
    require(isolated["validation_labels_opened"] is False and isolated["validation_labels_materialized"] is False and
            isolated["final_test_opened"] is False and isolated["final_test_materialized"] is False,
            "isolation opened/materialized neither labels nor FINAL_TEST", checks)
    for role, original, copied, filename in (("TRAIN", train, isolated_train, "train-episodes.jsonl"),
                                               ("VALIDATION", validation, isolated_validation,
                                                "validation-episodes.jsonl")):
        require(copied["file"] == isolated["files"][filename] == original["file"],
                f"isolated {role} exact bytes equal source", checks)
        require(copied["key_multiset"] == original["key_multiset"],
                f"isolated {role} key multiset equals source", checks)
    return source, isolated, {"TRAIN": isolated_train, "VALIDATION": isolated_validation, "labels": labels}


def verify_weights_and_split(train: pd.DataFrame, config: dict, checks: list[str]) -> dict:
    require(bool((train.groupby("uid").internal_split.nunique() == 1).all()),
            "inner split is user-disjoint", checks)
    split = config["split"]
    expected_split = train.uid.map(lambda uid: "HOLDOUT" if int.from_bytes(hashlib.sha256(
        f"{split['inner_user_salt']}:{int(uid)}".encode()).digest(), "big") %
        int(split["inner_user_holdout_modulus"]) == int(split["inner_user_holdout_bucket"]) else "FIT")
    require(bool((expected_split == train.internal_split).all()), "inner split recomputes from config", checks)
    group = train.groupby(["uid", "target_key"], sort=False)
    variants = group.n.transform("size").to_numpy(float)
    user_targets = train.groupby("uid", sort=False).target_key.transform("nunique").to_numpy(float)
    require(bool(np.allclose(train.weight_additive, 1.0 / (variants * user_targets), rtol=0, atol=1e-15)),
            "additive weights recompute by user/target/variant", checks)
    positive_count = group.n.transform(lambda values: int((values > 0).sum())).to_numpy(float)
    target_positive = train.groupby(["uid", "target_key"], sort=False).n.agg(
        lambda values: bool((values > 0).any())
    )
    active_target_counts = target_positive[target_positive].groupby(level="uid").size()
    active_targets = train.uid.map(active_target_counts).to_numpy(float)
    expected_interaction = np.zeros(len(train), dtype=float)
    positive_rows = train.n.to_numpy() > 0
    expected_interaction[positive_rows] = 1.0 / (
        active_targets[positive_rows] * positive_count[positive_rows]
    )
    require(bool(np.allclose(train.weight_interaction, expected_interaction, rtol=0, atol=1e-15)),
            "interaction weights recompute and exclude N=0", checks)
    require(bool(np.allclose(train.groupby("uid").weight_additive.sum(), 1.0, rtol=0, atol=1e-12)) and
            bool(np.allclose(train[train.n > 0].groupby("uid").weight_interaction.sum(), 1.0,
                             rtol=0, atol=1e-12)), "per-user training weights sum to one", checks)
    return {"FIT": {"rows": int((train.internal_split == "FIT").sum()),
                    "users": int(train.loc[train.internal_split == "FIT", "uid"].nunique())},
            "HOLDOUT": {"rows": int((train.internal_split == "HOLDOUT").sum()),
                        "users": int(train.loc[train.internal_split == "HOLDOUT", "uid"].nunique())}}


def verify_prepared(args, config: dict, isolated: dict, scans: dict, checks: list[str]):
    manifest_path = args.prepared_root / "manifest.json"
    prepared = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(prepared.get("schema_version") == 4 and prepared.get("status") == "PASS" and
            prepared.get("failed_rows") == 0, "prepared manifest is zero-failure schema-v4 PASS", checks)
    configured = config.get("pins", {}).get("prepared_manifest")
    require(configured is None or file_pin(manifest_path) == normalized_pin(configured),
            "prepared manifest matches config pin when configured", checks)
    require(prepared["input_manifest"] == file_pin(args.isolated_root / "manifest.json") ==
            normalized_pin(config["pins"]["isolated_manifest"]), "prepared links exact isolated manifest", checks)
    require(prepared["input_files"] == {name: normalized_pin(value) for name, value in isolated["files"].items()},
            "prepared input files equal isolated artifacts", checks)
    require(prepared["catalog_files"] == {"movies.csv": normalized_pin(config["source"]["movies"]),
                                           "tags.csv": normalized_pin(config["source"]["tags"])},
            "prepared catalog pins equal config", checks)
    require(prepared["container_mount_contract"] == EXPECTED_PREPARE_MOUNT_CONTRACT,
            "prepare mount contract is exact", checks)
    require(prepared["final_test_opened"] is False and prepared["validation_labels_opened"] is False and
            prepared["final_test"] == "NOT_PRESENT_IN_INPUT_OR_PREPARED_ARTIFACTS" and
            prepared["validation_labels"] == "NOT_PRESENT_IN_INPUT_OR_PREPARED_ARTIFACTS",
            "prepare opened neither labels nor FINAL_TEST", checks)
    require(set(prepared["files"]) == EXPECTED_PREPARED_FILES and
            {item.name for item in args.prepared_root.iterdir()} == EXPECTED_PREPARED_FILES | {"manifest.json"},
            "prepared file inventories are exact", checks)
    for name, expected in prepared["files"].items():
        require(file_pin(args.prepared_root / name) == normalized_pin(expected), f"prepared {name} hash exact", checks)
    source_files = prepared["source_bundle"]["files"]
    require(set(source_files) == EXPECTED_SOURCE_BUNDLE_FILES,
            "prepared source bundle inventory is exact", checks)
    actual_sources = {name: file_pin(args.repo_root / name) for name in source_files}
    require(actual_sources == source_files and prepared["source_bundle"]["algorithm"] == "ORDERED_FILE_PINS_V1" and
            prepared["source_bundle"]["digest"] == ordered_digest(actual_sources),
            "prepared source bundle equals executing tree", checks)

    train_path, validation_path = (args.prepared_root / name for name in
                                   ("train-targets.parquet", "validation-targets.parquet"))
    rows = {"train_targets": pq.ParquetFile(train_path).metadata.num_rows,
            "validation_targets": pq.ParquetFile(validation_path).metadata.num_rows}
    multisets = {"train_targets": parquet_key_multiset(train_path),
                 "validation_targets": parquet_key_multiset(validation_path)}
    require(prepared["rows"] == rows and prepared["row_key_multisets"] == multisets,
            "prepared row counts/key multisets recompute", checks)
    require(multisets["train_targets"] == scans["TRAIN"]["key_multiset"] and
            multisets["validation_targets"] == scans["VALIDATION"]["key_multiset"],
            "prepared row keys equal isolated episode keys", checks)
    train_columns = ["episode_id", "target_key", "uid", "n", "label", "internal_split",
                     "weight_additive", "weight_interaction", "feature_indices", "feature_values", "feature_size"]
    validation_columns = ["episode_id", "target_key", "uid", "n", "feature_indices", "feature_values", "feature_size"]
    train_table, validation_table = pq.read_table(train_path, columns=train_columns), pq.read_table(
        validation_path, columns=validation_columns)
    train = train_table.select(train_columns[:8]).to_pandas()
    validation = validation_table.select(validation_columns[:4]).to_pandas()
    require(not ({"label", "target_rating"} & set(pq.ParquetFile(validation_path).schema_arrow.names)),
            "prepared validation exposes no labels", checks)
    split_counts = verify_weights_and_split(train, config, checks)

    schema = json.loads((args.prepared_root / "feature-schema.json").read_text(encoding="utf-8"))
    require(schema["profile_digest"] == payload_digest(schema, "profile_digest"),
            "feature-schema profile digest recomputes", checks)
    for name in ("genre_vocabulary", "tag_vocabulary", "scaler", "global_rating_mean"):
        require(schema[name]["digest"] == payload_digest(schema[name]), f"schema {name} digest recomputes", checks)
    require(schema["ordered_names_sha256"] == hashlib.sha256("\n".join(schema["ordered_names"]).encode()).hexdigest(),
            "ordered feature-name digest recomputes", checks)
    feature_count = len(schema["ordered_names"])
    require(feature_count == len(set(schema["ordered_names"])) and
            not any("user_id" in name or "movie_id" in name for name in schema["ordered_names"]) and
            schema["user_id_embedding"] is False and schema["candidate_movie_id_feature"] is False,
            "schema excludes identity features", checks)
    require(schema["profiles"] == {PROFILE_ADDITIVE: {"linear_indices": list(range(feature_count)),
                                                       "interaction_fields": []},
                                   PROFILE_FACTOR: {"linear_indices": list(range(feature_count)),
                                                    "interaction_fields": ["genre", "tag"]}},
            "schema primary profile/order contract exact", checks)
    require(set(schema["interaction_fields"]) == set(FIELDS) and schema["factor_rank"] == 4,
            "schema has exact rank-4 genre/tag fields", checks)
    require(prepared["feature_schema"] == {
        "profile_digest": schema["profile_digest"], "ordered_names_sha256": schema["ordered_names_sha256"],
        "genre_vocabulary_digest": schema["genre_vocabulary"]["digest"],
        "tag_vocabulary_digest": schema["tag_vocabulary"]["digest"],
        "scaler_digest": schema["scaler"]["digest"],
        "global_rating_mean_digest": schema["global_rating_mean"]["digest"]},
        "prepared schema claims equal actual schema", checks)
    fit_rows = split_counts["FIT"]["rows"]
    require(all(schema[name]["fit_roles"] == ["TRAIN"] and
                schema[name]["fit_internal_splits"] == ["FIT"] and schema[name]["fit_rows"] == fit_rows
                for name in ("genre_vocabulary", "scaler", "global_rating_mean")) and
            schema["tag_vocabulary"]["fit_corpus"] == "EXTERNAL_USERS_ONLY",
            "preprocessing records FIT/external-only fitting", checks)
    fit = train.internal_split == "FIT"
    mean = float(np.dot(train.loc[fit, "weight_additive"], train.loc[fit, "label"]) /
                 train.loc[fit, "weight_additive"].sum())
    require(abs(mean - schema["global_rating_mean"]["value"]) <= 1e-15,
            "FIT-only global mean independently recomputes", checks)
    report = json.loads((args.prepared_root / "split-distribution-report.json").read_text(encoding="utf-8"))
    require(report["status"] == "PASS" and report["internal_split"] == "USER_DISJOINT_SHA256_MOD5" and
            report["preprocessing"] == {"genre_vocabulary": "TRAIN_INNER_FIT_ONLY",
                                         "global_rating_mean": "TRAIN_INNER_FIT_ONLY",
                                         "numeric_scaler": "TRAIN_INNER_FIT_ONLY",
                                         "tag_vocabulary": "EXTERNAL_USERS_PRE_2017_ONLY"},
            "split report fixes FIT-only preprocessing", checks)
    require(report["internal_split_rows"] == {name: values["rows"] for name, values in split_counts.items()} and
            report["internal_split_users"] == {name: values["users"] for name, values in split_counts.items()},
            "split report counts recompute", checks)
    expected_census = {role: {name: config["expected_census"][role][name]
                              for name in ("active_users", "active_targets", "n0_targets")}
                       for role in ("TRAIN", "VALIDATION")}
    require(report["census"] == expected_census, "prepared census equals config", checks)
    q_indices = {int(index) for field in schema["interaction_fields"].values() for index in field["q_indices"]}
    gates = {int(field["gate_index"]) for field in schema["interaction_fields"].values()}
    violations = 0
    for table, training in ((train_table, True), (validation_table, False)):
        for row_index, (n, size, indices, values) in enumerate(zip(
                table["n"].to_pylist(), table["feature_size"].to_pylist(),
                table["feature_indices"].to_pylist(), table["feature_values"].to_pylist())):
            if (size != feature_count or len(indices) != len(values) or indices != sorted(set(indices)) or
                    any(index < 0 or index >= feature_count for index in indices) or
                    any(not math.isfinite(value) or abs(value) > 1.0 for value in values)):
                raise RuntimeError("malformed prepared sparse row")
            if n == 0 and ((q_indices | gates).intersection(indices) or
                           (training and train.iloc[row_index].weight_interaction != 0.0)):
                violations += 1
    require(violations == report["n0_interaction_activation_violations"] == 0,
            "N=0 Q/gates/interaction weights are zero", checks)
    return prepared, schema, train, validation


def finite_document(value) -> bool:
    if isinstance(value, dict):
        return all(finite_document(item) for item in value.values())
    if isinstance(value, list):
        return all(finite_document(item) for item in value)
    return not isinstance(value, float) or math.isfinite(value)


def verify_trace(trace: dict, key: str, max_epochs: int) -> None:
    history = trace["history"]
    if ([item["epoch"] for item in history] != list(range(len(history))) or
            trace["epochs_executed"] != len(history) - 1 or len(history) - 1 > max_epochs):
        raise RuntimeError("invalid tuning trace epoch lineage")
    best = history[0]
    for item in history[1:]:
        if item[key] < best[key] - 1e-12:
            best = item
    if trace["best_epoch"] != best["epoch"] or not math.isclose(
            trace[f"best_{key}"], best[key], rel_tol=0, abs_tol=1e-15):
        raise RuntimeError("tuning best epoch/value does not recompute")


def verify_tuning(tuning: dict, config: dict, train: pd.DataFrame, checks: list[str]) -> None:
    require(finite_document(tuning), "tuning numerics are finite", checks)
    require(tuning["selection_metric"] == "TRAIN_INNER_HOLDOUT_POSITIVE_N_HIERARCHICAL_USER_MACRO_MSE" and
            tuning["validation_labels_read"] is False and tuning["rank"] == 4 and
            tuning["additive_parameters_frozen_and_shared_bitwise"] is True,
            "tuning selection/frozen-additive contract exact", checks)
    require(tuning["model_contract"] == config["model"],
            "tuning records the exact frozen model/optimizer contract", checks)
    require(tuning["internal_fit_rows"] == int((train.internal_split == "FIT").sum()) and
            tuning["internal_holdout_rows"] == int((train.internal_split == "HOLDOUT").sum()),
            "tuning row counts equal prepared split", checks)
    ridge = tuning["ridge_trials"]
    require([item["reg"] for item in ridge] == config["model"]["ridge_reg_grid"],
            "ridge grid/order equals config", checks)
    best_ridge = min(ridge, key=lambda item: (item["holdout_positive_n_user_macro_mse"], item["reg"]))
    require(tuning["selected_ridge_reg"] == best_ridge["reg"], "ridge selection recomputes", checks)
    trials, seeds = tuning["interaction_trials"], config["model"]["model_seeds"]
    require([item["reg"] for item in trials] == config["model"]["factor_reg_grid"],
            "interaction grid/order equals config", checks)
    for trial in trials:
        require([item["seed"] for item in trial["seed_trials"]] == seeds,
                f"reg {trial['reg']} seed set/order exact", checks)
        for item in trial["seed_trials"]:
            verify_trace(item["trace"], "selection_positive_n_user_macro_mse",
                         config["model"]["optimizer"]["max_epochs"])
        mean = float(np.mean([item["trace"]["best_selection_positive_n_user_macro_mse"]
                              for item in trial["seed_trials"]]))
        require(math.isclose(mean, trial["mean_holdout_positive_n_user_macro_mse"], rel_tol=0, abs_tol=1e-15),
                f"reg {trial['reg']} seed-mean MSE recomputes", checks)
    selected = min(trials, key=lambda item: (item["mean_holdout_positive_n_user_macro_mse"], item["reg"]))
    require(tuning["selected_interaction_reg"] == selected["reg"] and
            tuning["selected_interaction_mean_holdout_positive_n_user_macro_mse"] ==
            selected["mean_holdout_positive_n_user_macro_mse"], "interaction selection recomputes", checks)
    epochs = {str(item["seed"]): item["trace"]["best_epoch"] for item in selected["seed_trials"]}
    require(tuning["selected_epochs_by_seed"] == epochs, "selected epochs link chosen traces", checks)
    require([item["seed"] for item in tuning["final_interaction_refits"]] == seeds,
            "final refit seeds equal config", checks)
    for item in tuning["final_interaction_refits"]:
        epoch = epochs[str(item["seed"])]
        require(item["selected_epoch"] == item["trace"]["epochs_executed"] ==
                item["trace"]["returned_epoch"] == epoch, f"seed {item['seed']} refit epoch exact", checks)
        require([entry["epoch"] for entry in item["trace"]["history"]] == list(range(epoch + 1)),
                f"seed {item['seed']} refit trace epochs are contiguous", checks)
    require(tuning["train_only_mechanism_diagnostics"] == {
        "status": "EXCLUDED_FROM_EXECUTABLE_PROTOCOL", "profiles": [],
        "reason": "only the frozen primary comparison is executable; diagnostics cannot override it"},
        "diagnostics excluded from executable protocol", checks)


def load_prediction(path: Path) -> pd.DataFrame:
    frame = pd.read_parquet(path).sort_values("episode_id").reset_index(drop=True)
    if frame.episode_id.duplicated().any() or not np.isfinite(frame[["raw_prediction", "prediction"]]).all().all():
        raise RuntimeError("duplicate/non-finite prediction")
    return frame


def verify_prediction_stats(frame: pd.DataFrame, claimed: dict, bounds: list[float]) -> bool:
    raw = frame.raw_prediction.to_numpy(float)
    clipped = int(np.sum((raw < bounds[0]) | (raw > bounds[1])))
    return (claimed["rows"] == len(frame) and claimed["clipped_rows"] == clipped and
            math.isclose(claimed["clipped_fraction"], clipped / len(frame), rel_tol=0, abs_tol=1e-15) and
            claimed["raw_min"] == float(raw.min()) and claimed["raw_max"] == float(raw.max()))


def predict_factor(X: sparse.csr_matrix, member, schema: dict) -> np.ndarray:
    prediction = member["intercept"][0] + np.asarray(X @ member["linear"]).reshape(-1)
    for name in FIELDS:
        candidate, q = X[:, member[f"{name}_candidate_indices"]], X[:, member[f"{name}_q_indices"]]
        gate = np.asarray(X[:, int(member[f"{name}_gate_index"][0])].toarray()).reshape(-1)
        same, left, right = candidate.multiply(q), member[f"{name}_left"], member[f"{name}_right"]
        diagonal = np.asarray(same @ member[f"{name}_diagonal"]).reshape(-1)
        off = np.sum(np.asarray(candidate @ left) * np.asarray(q @ right), axis=1)
        off -= np.asarray(same @ np.sum(left * right, axis=1)).reshape(-1)
        prediction += gate * (diagonal + off)
    return prediction


def verify_fits(args, config: dict, prepared: dict, schema: dict, train: pd.DataFrame,
                validation: pd.DataFrame, checks: list[str]) -> dict:
    require({item.name for item in args.fits_root.iterdir()} == {
        "run-report.json", "tuning-report.json", PROFILE_ADDITIVE, PROFILE_FACTOR},
        "fit inventory contains only primary protocol", checks)
    run_path, tuning_path = args.fits_root / "run-report.json", args.fits_root / "tuning-report.json"
    run, tuning = (json.loads(path.read_text(encoding="utf-8")) for path in (run_path, tuning_path))
    config_pin, prepared_pin = file_pin(args.config), file_pin(args.prepared_root / "manifest.json")
    schema_pin, tuning_pin = file_pin(args.prepared_root / "feature-schema.json"), file_pin(tuning_path)
    require(run["status"] == "PASS" and run["schema_version"] == 4 and
            run["profiles"] == config["model"]["profiles"] == [PROFILE_ADDITIVE, PROFILE_FACTOR],
            "fit status/profile order exact", checks)
    require(run["runtime_image_id"] == config["runtime"]["image_id"] and run["config"] == config_pin and
            run["prepared_manifest"] == prepared_pin and run["prepared_input_manifest"] == prepared["input_manifest"] and
            run["prepared_source_bundle_digest"] == prepared["source_bundle"]["digest"] and
            run["feature_schema"] == schema_pin and run["tuning_report"] == tuning_pin,
            "run pins config/prepared/schema/runtime/tuning", checks)
    require(run["container_mount_contract"] == FIT_MOUNT_CONTRACT and run["validation_labels_read"] is False and
            run["final_test_opened"] is False, "fit mount/label/FINAL_TEST contract exact", checks)
    verify_tuning(tuning, config, train, checks)
    require(run["train_only_mechanism_diagnostics"] == tuning["train_only_mechanism_diagnostics"],
            "run records diagnostic exclusion", checks)
    metrics = {}
    for profile in (PROFILE_ADDITIVE, PROFILE_FACTOR):
        root = args.fits_root / profile
        expected_items = {"metrics.json", "model", "validation-target-predictions.parquet"}
        if profile == PROFILE_FACTOR:
            expected_items.add("diagnostic-seed-target-predictions")
        require({item.name for item in root.iterdir()} == expected_items,
                f"{profile} artifact inventory is exact", checks)
        require({item.name for item in (root / "validation-target-predictions.parquet").iterdir()} ==
                {"part-00000.parquet"}, f"{profile} ensemble prediction part inventory is exact", checks)
        document = json.loads((root / "metrics.json").read_text(encoding="utf-8"))
        require(document["status"] == "PASS" and document["schema_version"] == 4 and
                document["profile"] == profile and document["profiles"] == [PROFILE_ADDITIVE, PROFILE_FACTOR],
                f"{profile} status/profile/order exact", checks)
        require(document["runtime_image_id"] == config["runtime"]["image_id"] and document["config"] == config_pin and
                document["prepared_manifest"] == prepared_pin and
                document["prepared_input_manifest"] == prepared["input_manifest"] and
                document["prepared_source_bundle_digest"] == prepared["source_bundle"]["digest"] and
                document["feature_schema"] == schema_pin and document["tuning_report"] == tuning_pin and
                document["container_mount_contract"] == FIT_MOUNT_CONTRACT,
                f"{profile} metrics pin full lineage", checks)
        require(document["feature_count"] == len(schema["ordered_names"]) and
                document["training_rows"] == len(train) and document["validation_labels_read"] is False and
                document["final_test_opened"] is False, f"{profile} dimensions/seals exact", checks)
        require(tree_pin(root / "model") == document["model_artifact"] and
                tree_pin(root / "validation-target-predictions.parquet") == document["target_predictions_artifact"],
                f"{profile} model/prediction pins recompute", checks)
        metrics[profile] = document
    feature_count, rank = len(schema["ordered_names"]), config["model"]["factor_rank"]
    with np.load(args.fits_root / PROFILE_ADDITIVE / "model" / "model.npz") as additive:
        require(set(additive.files) == {"intercept", "linear"} and additive["intercept"].shape == (1,) and
                additive["linear"].shape == (feature_count,) and all(np.isfinite(additive[name]).all()
                                                                     for name in additive.files),
                "additive NPZ keys/shapes/finiteness exact", checks)
        intercept, linear = additive["intercept"].copy(), additive["linear"].copy()
    seeds, model_root = config["model"]["model_seeds"], args.fits_root / PROFILE_FACTOR / "model"
    require({item.name for item in (args.fits_root / PROFILE_ADDITIVE / "model").iterdir()} == {"model.npz"},
            "additive model inventory is exact", checks)
    require({item.name for item in model_root.iterdir()} == {f"seed-{seed}.npz" for seed in seeds},
            "factor model seed inventory exact", checks)
    keys = {"intercept", "linear"}
    for name in FIELDS:
        keys |= {f"{name}_{item}" for item in ("candidate_indices", "q_indices", "gate_index",
                                                "diagonal", "left", "right")}
    members = {}
    for seed in seeds:
        member = np.load(model_root / f"seed-{seed}.npz")
        require(set(member.files) == keys and np.array_equal(member["intercept"], intercept) and
                np.array_equal(member["linear"], linear), f"seed {seed} keys/frozen additive exact", checks)
        for name in FIELDS:
            layout, width = schema["interaction_fields"][name], len(schema["interaction_fields"][name]["candidate_indices"])
            require(np.array_equal(member[f"{name}_candidate_indices"], layout["candidate_indices"]) and
                    np.array_equal(member[f"{name}_q_indices"], layout["q_indices"]) and
                    int(member[f"{name}_gate_index"][0]) == layout["gate_index"] and
                    member[f"{name}_diagonal"].shape == (width,) and
                    member[f"{name}_left"].shape == member[f"{name}_right"].shape == (width, rank) and
                    all(np.isfinite(member[f"{name}_{item}"]).all() for item in ("diagonal", "left", "right")),
                    f"seed {seed} {name} indices/rank/finiteness exact", checks)
        members[seed] = member
    require(metrics[PROFILE_FACTOR]["model_seeds"] == seeds and metrics[PROFILE_FACTOR]["rank"] == rank and
            metrics[PROFILE_FACTOR]["selected_epochs_by_seed"] == tuning["selected_epochs_by_seed"] and
            metrics[PROFILE_FACTOR]["selected_interaction_reg"] == tuning["selected_interaction_reg"] and
            metrics[PROFILE_ADDITIVE]["selected_ridge_reg"] == tuning["selected_ridge_reg"],
            "metrics link selected tuning values", checks)
    table = pq.read_table(args.prepared_root / "validation-targets.parquet")
    X = frame_to_csr(table, feature_count)
    identity = table.select(["episode_id", "target_key", "uid", "n"]).to_pandas().sort_values(
        "episode_id").reset_index(drop=True)
    additive_frame = load_prediction(args.fits_root / PROFILE_ADDITIVE / "validation-target-predictions.parquet")
    factor_frame = load_prediction(args.fits_root / PROFILE_FACTOR / "validation-target-predictions.parquet")
    for name, frame in ((PROFILE_ADDITIVE, additive_frame), (PROFILE_FACTOR, factor_frame)):
        require(frame[["episode_id", "target_key", "uid", "n"]].equals(identity),
                f"{name} row identities equal prepared", checks)
        require(verify_prediction_stats(frame, metrics[name]["target_prediction_stats"],
                                        config["prediction_bounds"]),
                f"{name} prediction statistics recompute", checks)
    additive_raw = intercept[0] + np.asarray(X @ linear).reshape(-1)
    additive_order = pd.read_parquet(args.fits_root / PROFILE_ADDITIVE / "validation-target-predictions.parquet")
    require(np.allclose(additive_order.raw_prediction, additive_raw, rtol=0, atol=1e-12) and
            np.array_equal(additive_order.prediction, np.clip(additive_raw, *config["prediction_bounds"])),
            "additive predictions recompute from NPZ/features", checks)
    seed_root = args.fits_root / PROFILE_FACTOR / "diagnostic-seed-target-predictions"
    require({item.name for item in seed_root.iterdir()} == {f"seed-{seed}.parquet" for seed in seeds} and
            tree_pin(seed_root) == metrics[PROFILE_FACTOR]["seed_target_predictions_artifact"],
            "per-seed prediction inventory/tree exact", checks)
    seed_raw = []
    for seed in seeds:
        require({item.name for item in (seed_root / f"seed-{seed}.parquet").iterdir()} == {"part-00000.parquet"},
                f"seed {seed} prediction part inventory is exact", checks)
        frame = pd.read_parquet(seed_root / f"seed-{seed}.parquet")
        require(frame[["episode_id", "target_key", "uid", "n"]].sort_values("episode_id").reset_index(drop=True).equals(identity),
                f"seed {seed} row identities equal prepared", checks)
        raw = predict_factor(X, members[seed], schema)
        require(np.allclose(frame.raw_prediction, raw, rtol=0, atol=1e-12) and
                np.array_equal(frame.prediction, np.clip(raw, *config["prediction_bounds"])),
                f"seed {seed} predictions recompute", checks)
        stats = metrics[PROFILE_FACTOR]["seed_target_prediction_stats"][str(seed)]
        clipped = int(np.sum((raw < config["prediction_bounds"][0]) | (raw > config["prediction_bounds"][1])))
        require(verify_prediction_stats(frame, stats, config["prediction_bounds"]) and
                stats["clipped_rows"] == clipped,
                f"seed {seed} prediction stats recompute", checks)
        seed_raw.append(raw)
    ensemble = np.mean(np.vstack(seed_raw), axis=0)
    factor_order = pd.read_parquet(args.fits_root / PROFILE_FACTOR / "validation-target-predictions.parquet")
    require(np.allclose(factor_order.raw_prediction, ensemble, rtol=0, atol=1e-12) and
            np.array_equal(factor_order.prediction, np.clip(ensemble, *config["prediction_bounds"])),
            "fixed mean ensemble recomputes", checks)
    n0 = table["n"].to_numpy(zero_copy_only=False) == 0
    require(np.max(np.abs(ensemble[n0] - additive_raw[n0]), initial=0.0) <= config["primary"]["tie_tolerance"],
            "actual N=0 predictions tie", checks)
    for member in members.values():
        member.close()
    return {"run": run, "tuning": tuning, "metrics": metrics}


def verify_evaluation(args, config: dict, source: dict, checks: list[str]) -> dict:
    report = json.loads(args.validation_report.read_text(encoding="utf-8"))
    require(report["schema_version"] == 4 and report["status"] == "PASS" and report["final_test_opened"] is False,
            "validation report is sealed schema-v4 PASS", checks)
    require(report["config"] == file_pin(args.config) and report["calculator"] == calculator_bundle(),
            "validation config/calculator pins recompute", checks)
    labels = args.source_root / "validation-labels.jsonl"
    require(file_pin(labels) == normalized_pin(source["files"]["validation_labels"]),
            "evaluation labels equal source seal", checks)
    frame, artifacts = join_frozen_predictions_and_labels(args.fits_root, labels)
    recomputed = evaluate_frame(frame, config)
    require(report["artifacts"] == artifacts, "evaluation artifact pins recompute", checks)
    require(report["primary"] == recomputed["primary"] and report["safety"] == recomputed["safety"] and
            report["retention_decision"] == recomputed["retention_decision"],
            "primary/safety/decision recompute", checks)
    require(report["validation_was_not_used_for_fitting_or_model_selection"] is True and
            report["labels_opened_only_after_prediction_hash_verification"] is True,
            "evaluation label opening is post-prediction", checks)
    require(report["train_only_mechanism_diagnostics"] == {
        "status": "EXCLUDED_FROM_EXECUTABLE_PROTOCOL", "profiles": [], "cannot_override_primary": True},
        "evaluation excludes diagnostic profiles", checks)
    return recomputed


def verify(args: argparse.Namespace) -> dict:
    if args.output.exists():
        raise FileExistsError(f"verification output already exists: {args.output}")
    checks: list[str] = []
    config = json.loads(args.config.read_text(encoding="utf-8"))
    require(config.get("status") == "PREREGISTERED_REVIEWED", "config is preregistered/reviewed", checks)
    require(config["model"]["profiles"] == [PROFILE_ADDITIVE, PROFILE_FACTOR] and
            config["model"]["diagnostic_profiles"] == [],
            "config contains only ordered primary executable profiles", checks)
    source, isolated, scans = verify_source_and_isolation(args, config, checks)
    prepared, schema, train, validation = verify_prepared(args, config, isolated, scans, checks)
    verify_fits(args, config, prepared, schema, train, validation, checks)
    recomputed = verify_evaluation(args, config, source, checks)
    inventory = {"config": file_pin(args.config), "source": tree_pin(args.source_root),
                 "isolated": tree_pin(args.isolated_root), "prepared": tree_pin(args.prepared_root),
                 "fits": tree_pin(args.fits_root), "validation_report": file_pin(args.validation_report)}
    output = {"schema_version": 4, "status": "PASS", "checks": checks, "check_count": len(checks),
              "source_bundle_digest": prepared["source_bundle"]["digest"],
              "artifact_inventory": inventory, "artifact_manifest_digest": ordered_digest(inventory),
              "primary": recomputed["primary"], "safety": recomputed["safety"],
              "retention_decision": recomputed["retention_decision"],
              "train_only_mechanism_diagnostics": {
                  "status": "EXCLUDED_FROM_EXECUTABLE_PROTOCOL", "profiles": [],
                  "cannot_override_primary": True},
              "validation_labels_opened_only_for_post_prediction_evaluation_and_verification": True,
              "final_test_opened": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--isolated-root", type=Path, required=True)
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--fits-root", type=Path, required=True)
    parser.add_argument("--validation-report", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    verify(parser.parse_args())


if __name__ == "__main__":
    main()
