#!/usr/bin/env python3
"""Build deterministic, user-disjoint binary-onboarding cohort artifacts.

REC-EV-019A materializes data and labels only. It never trains, scores, selects,
or promotes a recommender model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from recommendation_binary_onboarding_preflight import (
    future_midrank_utilities,
    global_midrank_ecdf,
    sequential_binary_labels,
    split_prefix,
    stable_user_bucket,
)
from recommendation_protocol_v4 import sha256_file


REPO_ROOT = Path(__file__).resolve().parents[1]
IDENTITY_ALLOWLIST = {"ML_TMDB_VERIFIED", "RECOVERED_BY_IMDB"}
ROLE_RANGES = {
    "ROUTER_TRAIN": (40, 49),
    "VALIDATION": (50, 59),
    "LOCKED_TEST": (60, 99),
}
HEX_64 = re.compile(r"^[0-9a-f]{64}$")


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value))


def user_key(user_id: int) -> str:
    return hashlib.sha256(
        f"feelm-ml32m-user-v1|{int(user_id)}".encode("utf-8")
    ).hexdigest()


def _zip_member(source: zipfile.ZipFile, suffix: str) -> str:
    matches = [name for name in source.namelist() if name.endswith(suffix)]
    if len(matches) != 1:
        raise RuntimeError(f"expected one archive member ending in {suffix}: {matches}")
    return matches[0]


def _load_contract(path: Path) -> dict[str, Any]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    if contract.get("status") != "APPROVED":
        raise RuntimeError("REC-EV-019A contract is not APPROVED")
    if contract.get("task_id") != "TASK-REC-EV-019A":
        raise RuntimeError("unexpected REC-EV-019A task id")
    return contract


def _load_protocol(path: Path, contract: dict[str, Any]) -> dict[str, Any]:
    protocol = json.loads(path.read_text(encoding="utf-8"))
    if protocol.get("protocol_version") != contract["protocol_version"]:
        raise RuntimeError("protocol and artifact contract versions differ")
    if protocol["candidate"].get("positive_injection") is not False:
        raise RuntimeError("positive injection must remain disabled")
    if protocol["inputs"].get("unrated_as_negative_forbidden") is not True:
        raise RuntimeError("unrated-as-negative prohibition is missing")
    return protocol


def _resolve_paths(contract: dict[str, Any]) -> dict[str, Path]:
    manifest_path = REPO_ROOT / contract["inputs"]["global_time_manifest"]
    source_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    paths = {
        "global_manifest": manifest_path,
        "train": REPO_ROOT / contract["inputs"]["train_parquet"],
        "validation": REPO_ROOT / contract["inputs"]["validation_parquet"],
        "test": REPO_ROOT / contract["inputs"]["test_parquet"],
        "archive": Path(source_manifest["source"]["archive"]),
    }
    for name, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"required {name} input is missing: {path}")
    expected = {
        "train": source_manifest["artifacts"]["train"]["sha256"],
        "validation": source_manifest["artifacts"]["validation"]["sha256"],
        "test": source_manifest["artifacts"]["test"]["sha256"],
        "archive": source_manifest["source"]["archive_sha256"],
    }
    for name, expected_sha in expected.items():
        if sha256_file(paths[name]) != expected_sha:
            raise RuntimeError(f"source checksum mismatch: {name}")
    return paths


def _bucket_lookup(prefix: str, maximum_user_id: int = 300_000) -> np.ndarray:
    return np.fromiter(
        (
            stable_user_bucket(user_id, split_prefix=prefix)
            for user_id in range(maximum_user_id + 1)
        ),
        dtype=np.uint8,
        count=maximum_user_id + 1,
    )


def _read_filtered_parquet(
    path: Path,
    bucket_lookup: np.ndarray,
    minimum_bucket: int,
    maximum_bucket: int,
    *,
    batch_size: int,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    parquet = pq.ParquetFile(path)
    for batch in parquet.iter_batches(batch_size=batch_size):
        frame = batch.to_pandas()
        user_ids = frame["user_id"].to_numpy(dtype=np.int64, copy=False)
        if user_ids.max(initial=0) >= len(bucket_lookup):
            raise RuntimeError("MovieLens user id exceeds the locked bucket lookup")
        buckets = bucket_lookup[user_ids]
        mask = (buckets >= minimum_bucket) & (buckets <= maximum_bucket)
        if bool(mask.any()):
            selected = frame.loc[mask].copy()
            selected["user_bucket"] = buckets[mask]
            frames.append(selected)
    if not frames:
        return pd.DataFrame(
            columns=["user_id", "movie_id", "rating", "timestamp", "user_bucket"]
        )
    result = pd.concat(frames, ignore_index=True)
    return result.sort_values(
        ["user_id", "timestamp", "movie_id"], kind="stable", ignore_index=True
    )


def _load_links(archive_path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(archive_path) as source:
        links = pd.read_csv(
            source.open(_zip_member(source, "links.csv")),
            usecols=["movieId", "tmdbId"],
            dtype={"movieId": "int32", "tmdbId": "string"},
        )
    links = links.rename(columns={"movieId": "movie_id", "tmdbId": "tmdb_id"})
    links["tmdb_id"] = pd.to_numeric(links["tmdb_id"], errors="coerce").astype("Int64")
    return links.loc[links["tmdb_id"].notna() & (links["tmdb_id"] > 0)].copy()


def build_candidate_core(base_train: pd.DataFrame, links: pd.DataFrame) -> pd.DataFrame:
    grouped = (
        base_train.groupby("movie_id", sort=True, observed=True)
        .agg(
            base_train_interaction_count=("movie_id", "size"),
            first_base_train_timestamp=("timestamp", "min"),
        )
        .reset_index()
    )
    result = grouped.merge(links, on="movie_id", how="inner", validate="one_to_one")
    result["identity_status"] = "LINK_PRESENT"
    return result[
        [
            "movie_id",
            "tmdb_id",
            "base_train_interaction_count",
            "first_base_train_timestamp",
            "identity_status",
        ]
    ].sort_values("movie_id", kind="stable", ignore_index=True)


def _empty_stage(total_users: int) -> dict[str, int]:
    return {
        "total_role_users": total_users,
        "input_and_future_users": 0,
        "minimum_positive_users": 0,
        "candidate_positive_users": 0,
        "strict_eligible_users": 0,
        "eligible_with_both_binary_classes": 0,
        "not_enough_binary_labels_or_future": 0,
        "not_enough_future_positives": 0,
        "no_positive_in_provisional_candidate": 0,
    }


def materialize_role_cohorts(
    ratings_frame: pd.DataFrame,
    *,
    role: str,
    global_midrank: np.ndarray,
    candidate_movie_ids: set[int],
    protocol: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Create strict-eligible prefix/window rows for one user role."""

    if role not in ROLE_RANGES:
        raise ValueError(f"unknown role: {role}")
    grouped = list(ratings_frame.groupby("user_id", sort=True, observed=True))
    k_values = [int(value) for value in protocol["inputs"]["binary_k_primary"]]
    future_count = int(protocol["relevance"]["future_window_ratings"])
    minimum_positives = int(protocol["relevance"]["minimum_future_positives"])
    positive_min = float(protocol["relevance"]["positive_midrank_utility_min"])
    negative_max = float(protocol["relevance"]["negative_midrank_utility_max"])
    stages = {str(k): _empty_stage(len(grouped)) for k in k_values}
    prefix_rows: list[dict[str, Any]] = []
    window_rows: list[dict[str, Any]] = []
    class_balance: dict[str, Counter[str]] = {
        str(k): Counter() for k in k_values if k > 0
    }

    for raw_user_id, group in grouped:
        group = group.sort_values(["timestamp", "movie_id"], kind="stable")
        ratings = group["rating"].to_numpy(dtype=np.float64, copy=False)
        movie_ids = group["movie_id"].to_numpy(dtype=np.int64, copy=False)
        timestamps = group["timestamp"].to_numpy(dtype=np.int64, copy=False)
        labels = sequential_binary_labels(
            ratings,
            global_midrank,
            shrinkage=float(protocol["inputs"]["binary_shrinkage_lambda"]),
            like_min=float(protocol["inputs"]["binary_relative_like_min"]),
            dislike_max=float(protocol["inputs"]["binary_relative_dislike_max"]),
        )
        key = user_key(int(raw_user_id))
        if not HEX_64.fullmatch(key):
            raise RuntimeError("invalid pseudonymous user key")

        for k in k_values:
            stage = stages[str(k)]
            selected_labels: list[tuple[int, int, float]] = []
            if k == 0:
                start = 0
            elif len(labels) < k:
                stage["not_enough_binary_labels_or_future"] += 1
                continue
            else:
                selected_labels = labels[:k]
                start = int(selected_labels[-1][0]) + 1
            stop = start + future_count
            if stop > len(ratings):
                stage["not_enough_binary_labels_or_future"] += 1
                continue
            stage["input_and_future_users"] += 1
            held_ratings = ratings[start:stop]
            held_movies = movie_ids[start:stop]
            held_timestamps = timestamps[start:stop]
            utilities = future_midrank_utilities(held_ratings)
            positives = utilities >= positive_min
            negatives = utilities <= negative_max
            if int(positives.sum()) < minimum_positives:
                stage["not_enough_future_positives"] += 1
                continue
            stage["minimum_positive_users"] += 1
            candidate_positive = any(
                int(movie_id) in candidate_movie_ids
                for movie_id in held_movies[positives]
            )
            if not candidate_positive:
                stage["no_positive_in_provisional_candidate"] += 1
                continue
            stage["candidate_positive_users"] += 1
            stage["strict_eligible_users"] += 1
            if k > 0:
                both_classes = len({label for _, label, _ in selected_labels}) == 2
                stage["eligible_with_both_binary_classes"] += int(both_classes)
                for input_rank, (position, binary_label, relative_utility) in enumerate(
                    selected_labels, start=1
                ):
                    prefix_rows.append(
                        {
                            "role": role,
                            "user_key": key,
                            "k": k,
                            "input_rank": input_rank,
                            "movie_id": int(movie_ids[position]),
                            "binary_label": int(binary_label),
                            "relative_utility": float(relative_utility),
                            "source_position": int(position),
                            "timestamp": int(timestamps[position]),
                        }
                    )
                    class_balance[str(k)][
                        "like_rows" if binary_label == 1 else "dislike_rows"
                    ] += 1
            for window_rank, (
                movie_id,
                rating,
                utility,
                positive,
                negative,
                timestamp,
            ) in enumerate(
                zip(
                    held_movies,
                    held_ratings,
                    utilities,
                    positives,
                    negatives,
                    held_timestamps,
                ),
                start=1,
            ):
                window_rows.append(
                    {
                        "role": role,
                        "user_key": key,
                        "k": k,
                        "window_rank": window_rank,
                        "movie_id": int(movie_id),
                        "rating": float(rating),
                        "midrank_utility": float(utility),
                        "is_positive": bool(positive),
                        "is_negative": bool(negative),
                        "provisional_candidate_present": int(movie_id)
                        in candidate_movie_ids,
                        "timestamp": int(timestamp),
                    }
                )

    summary = {
        "role_users": len(grouped),
        "eligibility": stages,
        "class_balance": {
            k: {name: int(value) for name, value in counts.items()}
            for k, counts in class_balance.items()
        },
    }
    return prefix_rows, window_rows, summary


def _write_base_train(path: Path, frame: pd.DataFrame) -> None:
    users = sorted(
        ((int(raw_id), user_key(int(raw_id))) for raw_id in frame["user_id"].unique()),
        key=lambda item: item[1],
    )
    user_to_rank = {raw_id: rank for rank, (raw_id, _) in enumerate(users)}
    ranks = frame["user_id"].map(user_to_rank).to_numpy(dtype=np.int32)
    order = np.lexsort(
        (
            frame["movie_id"].to_numpy(dtype=np.int64),
            frame["timestamp"].to_numpy(dtype=np.int64),
            ranks,
        )
    )
    sorted_ranks = ranks[order]
    keys = pa.array([key for _, key in users], type=pa.string())
    key_array = pa.DictionaryArray.from_arrays(pa.array(sorted_ranks), keys)
    table = pa.Table.from_arrays(
        [
            key_array,
            pa.array(frame["movie_id"].to_numpy(dtype=np.int32)[order]),
            pa.array(frame["rating"].to_numpy(dtype=np.float32)[order]),
            pa.array(frame["timestamp"].to_numpy(dtype=np.int64)[order]),
            pa.array(frame["user_bucket"].to_numpy(dtype=np.uint8)[order]),
        ],
        names=["user_key", "movie_id", "rating", "timestamp", "user_bucket"],
    )
    pq.write_table(
        table,
        path,
        compression="zstd",
        use_dictionary=True,
        store_schema=False,
    )


def _write_candidate_core(path: Path, frame: pd.DataFrame) -> None:
    schema = pa.schema(
        [
            ("movie_id", pa.int32()),
            ("tmdb_id", pa.int32()),
            ("base_train_interaction_count", pa.int64()),
            ("first_base_train_timestamp", pa.int64()),
            ("identity_status", pa.string()),
        ]
    )
    pq.write_table(pa.Table.from_pandas(frame, schema=schema, preserve_index=False), path, compression="zstd")


def _write_prefixes(path: Path, rows: list[dict[str, Any]]) -> None:
    schema = pa.schema(
        [
            ("role", pa.string()),
            ("user_key", pa.string()),
            ("k", pa.int8()),
            ("input_rank", pa.int8()),
            ("movie_id", pa.int32()),
            ("binary_label", pa.int8()),
            ("relative_utility", pa.float32()),
            ("source_position", pa.int32()),
            ("timestamp", pa.int64()),
        ]
    )
    table = pa.Table.from_pylist(rows, schema=schema).sort_by(
        [("role", "ascending"), ("user_key", "ascending"), ("k", "ascending"), ("input_rank", "ascending")]
    )
    pq.write_table(table, path, compression="zstd")


def _write_windows(path: Path, rows: list[dict[str, Any]]) -> None:
    schema = pa.schema(
        [
            ("role", pa.string()),
            ("user_key", pa.string()),
            ("k", pa.int8()),
            ("window_rank", pa.int8()),
            ("movie_id", pa.int32()),
            ("rating", pa.float32()),
            ("midrank_utility", pa.float32()),
            ("is_positive", pa.bool_()),
            ("is_negative", pa.bool_()),
            ("provisional_candidate_present", pa.bool_()),
            ("timestamp", pa.int64()),
        ]
    )
    table = pa.Table.from_pylist(rows, schema=schema).sort_by(
        [("role", "ascending"), ("user_key", "ascending"), ("k", "ascending"), ("window_rank", "ascending")]
    )
    pq.write_table(table, path, compression="zstd")


def _final_identity_gate(
    windows: pd.DataFrame,
    candidate_movie_ids: set[int],
    identity_path: Path,
    minimum_users: int,
) -> dict[str, Any]:
    if not identity_path.is_file():
        return {
            "status": "PENDING_REC_EV_019B_ARTIFACT",
            "pass": None,
            "locked_test_model_predictions_opened": False,
        }
    identity = pd.read_parquet(identity_path, columns=["movie_id", "identity_status"])
    allowed = set(
        identity.loc[
            identity["identity_status"].isin(IDENTITY_ALLOWLIST), "movie_id"
        ].astype(int)
    )
    candidate_identity = identity.loc[identity["movie_id"].isin(candidate_movie_ids)].copy()
    candidate_status_counts = {
        str(name): int(count)
        for name, count in candidate_identity["identity_status"].value_counts().sort_index().items()
    }
    final_candidate_movies = len(candidate_movie_ids.intersection(allowed))
    k10 = windows.loc[
        (windows["role"] == "LOCKED_TEST") & (windows["k"] == 10)
    ].copy()
    provisional_users = int(k10["user_key"].nunique())
    k10["final_candidate_positive"] = k10["is_positive"] & k10["movie_id"].isin(allowed)
    final_users = int(
        k10.groupby("user_key", observed=True)["final_candidate_positive"].any().sum()
    )
    return {
        "status": "PASS" if final_users >= minimum_users else "FAIL_INSUFFICIENT_USERS",
        "identity_artifact": identity_path.relative_to(REPO_ROOT).as_posix(),
        "identity_artifact_sha256": sha256_file(identity_path),
        "identity_status_allowlist": sorted(IDENTITY_ALLOWLIST),
        "provisional_candidate_movies": len(candidate_movie_ids),
        "candidate_identity_rows_found": int(len(candidate_identity)),
        "candidate_identity_status_counts": candidate_status_counts,
        "final_identity_candidate_movies": final_candidate_movies,
        "provisional_k10_strict_eligible_users": provisional_users,
        "final_identity_k10_strict_eligible_users": final_users,
        "users_removed_by_final_identity": provisional_users - final_users,
        "minimum_users": minimum_users,
        "pass": final_users >= minimum_users,
        "locked_test_model_predictions_opened": False,
    }


def build(args: argparse.Namespace) -> dict[str, Any]:
    contract = _load_contract(args.contract)
    protocol_path = REPO_ROOT / contract["inputs"]["protocol"]
    protocol = _load_protocol(protocol_path, contract)
    paths = _resolve_paths(contract)
    prefix = split_prefix(protocol)
    lookup = _bucket_lookup(prefix)
    output_root = REPO_ROOT / contract["output_root"]
    output_root.mkdir(parents=True, exist_ok=True)

    source_checksums = {
        "global_time_manifest_sha256": sha256_file(paths["global_manifest"]),
        "movielens_archive_sha256": sha256_file(paths["archive"]),
        "train_parquet_sha256": sha256_file(paths["train"]),
        "validation_parquet_sha256": sha256_file(paths["validation"]),
        "test_parquet_sha256": sha256_file(paths["test"]),
        "protocol_sha256": sha256_file(protocol_path),
        "contract_sha256": sha256_file(args.contract),
    }
    protocol_lock = {
        "schema_version": 2,
        "evidence_id": "REC-EV-019A",
        "protocol_sha256": source_checksums["protocol_sha256"],
        "contract_sha256": source_checksums["contract_sha256"],
        "source_checksums": source_checksums,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_before_model_test": True,
        "model_predictions_created": False,
        "product_policy_changed": False,
    }
    write_json(output_root / "protocol-lock.json", protocol_lock)

    print("loading cutoff-safe Base Train rows", flush=True)
    base_train = _read_filtered_parquet(
        paths["train"], lookup, 0, 39, batch_size=args.batch_size
    )
    global_midrank = global_midrank_ecdf(base_train["rating"].to_numpy(dtype=np.float64))
    links = _load_links(paths["archive"])
    candidate_core = build_candidate_core(base_train, links)
    candidate_ids = set(candidate_core["movie_id"].astype(int))

    _write_base_train(output_root / "base-train-ratings.parquet", base_train)
    _write_candidate_core(output_root / "candidate-core-provisional.parquet", candidate_core)
    print(
        f"Base Train: {len(base_train):,} rows, {base_train['user_id'].nunique():,} users, {len(candidate_core):,} linked candidates",
        flush=True,
    )

    role_frames = {
        "ROUTER_TRAIN": _read_filtered_parquet(
            paths["validation"], lookup, 40, 49, batch_size=args.batch_size
        ),
        "VALIDATION": _read_filtered_parquet(
            paths["validation"], lookup, 50, 59, batch_size=args.batch_size
        ),
        "LOCKED_TEST": _read_filtered_parquet(
            paths["test"], lookup, 60, 99, batch_size=args.batch_size
        ),
    }
    all_prefixes: list[dict[str, Any]] = []
    all_windows: list[dict[str, Any]] = []
    role_summaries: dict[str, Any] = {}
    for role, frame in role_frames.items():
        prefixes, windows, summary = materialize_role_cohorts(
            frame,
            role=role,
            global_midrank=global_midrank,
            candidate_movie_ids=candidate_ids,
            protocol=protocol,
        )
        all_prefixes.extend(prefixes)
        all_windows.extend(windows)
        role_summaries[role] = summary
        print(
            f"{role}: {summary['role_users']:,} users, K10 strict {summary['eligibility']['10']['strict_eligible_users']:,}",
            flush=True,
        )

    _write_prefixes(output_root / "binary-prefixes.parquet", all_prefixes)
    _write_windows(output_root / "evaluation-windows.parquet", all_windows)
    windows_frame = pd.DataFrame(all_windows)
    minimum_users = int(contract["gates"]["locked_test_k10_strict_eligible_min"])
    provisional_test_k10 = int(
        role_summaries["LOCKED_TEST"]["eligibility"]["10"]["strict_eligible_users"]
    )
    final_identity_path = REPO_ROOT / contract["inputs"]["final_identity_parquet_optional_postcheck"]
    final_gate = _final_identity_gate(
        windows_frame, candidate_ids, final_identity_path, minimum_users
    )

    drop_reasons = {
        role: {
            k: {
                name: int(values[name])
                for name in (
                    "not_enough_binary_labels_or_future",
                    "not_enough_future_positives",
                    "no_positive_in_provisional_candidate",
                )
            }
            for k, values in summary["eligibility"].items()
        }
        for role, summary in role_summaries.items()
    }
    class_balance = {
        role: summary["class_balance"] for role, summary in role_summaries.items()
    }
    candidate_positive_rate = {
        role: {
            k: (
                values["candidate_positive_users"] / values["minimum_positive_users"]
                if values["minimum_positive_users"]
                else None
            )
            for k, values in summary["eligibility"].items()
        }
        for role, summary in role_summaries.items()
    }
    provisional_pass = provisional_test_k10 >= minimum_users
    summary = {
        "schema_version": 2,
        "evidence_id": "REC-EV-019A",
        "protocol_version": protocol["protocol_version"],
        "source_checksums": source_checksums,
        "role_counts": {
            "BASE_TRAIN": {
                "users": int(base_train["user_id"].nunique()),
                "ratings": int(len(base_train)),
            },
            **{
                role: {
                    "users": int(data["user_id"].nunique()),
                    "ratings": int(len(data)),
                }
                for role, data in role_frames.items()
            },
        },
        "candidate_core": {
            "cutoff_safe_distinct_movies": int(base_train["movie_id"].nunique()),
            "provisional_linked_movies": int(len(candidate_core)),
            "future_catalog_feature_superset_is_candidate_core": False,
        },
        "eligibility_by_role_and_k": {
            role: data["eligibility"] for role, data in role_summaries.items()
        },
        "drop_reasons": drop_reasons,
        "class_balance": class_balance,
        "candidate_positive_rate": candidate_positive_rate,
        "provisional_locked_test_k10_gate": {
            "eligible_users": provisional_test_k10,
            "minimum_users": minimum_users,
            "pass": provisional_pass,
        },
        "final_identity_locked_test_k10_gate": final_gate,
        "raw_user_ids_stored": False,
        "unrated_as_dislike": False,
        "neutral_as_dislike": False,
        "locked_test_model_predictions_opened": False,
        "product_policy_changed": False,
    }
    write_json(output_root / "cohort-summary.json", summary)

    artifacts: list[dict[str, Any]] = []
    for name in (
        "base-train-ratings.parquet",
        "candidate-core-provisional.parquet",
        "binary-prefixes.parquet",
        "evaluation-windows.parquet",
        "cohort-summary.json",
        "protocol-lock.json",
    ):
        path = output_root / name
        artifacts.append(
            {
                "path": path.relative_to(REPO_ROOT).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    full_pass = provisional_pass and final_gate.get("pass") is not False
    manifest = {
        "schema_version": 2,
        "evidence_id": "REC-EV-019A",
        "status": "PASS_COHORT_GATES" if full_pass else "BLOCKED_COHORT_GATE_FAILURE",
        "contract": args.contract.relative_to(REPO_ROOT).as_posix(),
        "contract_sha256": source_checksums["contract_sha256"],
        "protocol_lock": "outputs/recommendation-evidence/rec-ev-019a/protocol-lock.json",
        "source_checksums": source_checksums,
        "artifacts": artifacts,
        "validation": {
            "locked_test_k10_provisional_eligible": provisional_test_k10,
            "locked_test_k10_final_identity_eligible": final_gate.get(
                "final_identity_k10_strict_eligible_users"
            ),
            "final_identity_candidate_movies": final_gate.get(
                "final_identity_candidate_movies"
            ),
            "minimum_users": minimum_users,
            "prefix_nested_k5_in_k10": True,
            "raw_user_ids_stored": False,
            "locked_test_model_predictions_opened": False,
            "product_policy_changed": False,
        },
    }
    manifest_path = REPO_ROOT / "docs/recommendation/evidence/manifests/rec-ev-019a.json"
    write_json(manifest_path, manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build REC-EV-019A cohort artifacts")
    parser.add_argument(
        "--contract",
        type=Path,
        default=REPO_ROOT
        / "docs/recommendation/contracts/rec-ev-019a-artifacts.json",
    )
    parser.add_argument("--batch-size", type=int, default=1_000_000)
    args = parser.parse_args()
    args.contract = args.contract.resolve()
    return args


if __name__ == "__main__":
    try:
        result = build(parse_args())
        print(json.dumps({"status": result["status"]}, ensure_ascii=False))
    except Exception as error:
        print(f"REC-EV-019A build failed: {error}")
        raise SystemExit(1)
