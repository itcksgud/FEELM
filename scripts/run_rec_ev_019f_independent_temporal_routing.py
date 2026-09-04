#!/usr/bin/env python3
"""Run the preregistered REC-EV-019F source-row/window temporal confirmation."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

try:
    from recommendation_binary_onboarding_preflight import (
        future_midrank_utilities,
        sequential_binary_labels,
        split_prefix,
        stable_user_bucket,
    )
except ModuleNotFoundError:  # package import used by unittest
    from scripts.recommendation_binary_onboarding_preflight import (
        future_midrank_utilities,
        sequential_binary_labels,
        split_prefix,
        stable_user_bucket,
    )


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "docs/recommendation/contracts/rec-ev-019f-independent-temporal-routing.json"
DEFAULT_PREREGISTRATION = ROOT / "docs/recommendation/evidence/REC-EV-019F-independent-temporal-routing-preregistration.md"
DEFAULT_MANIFEST = ROOT / "docs/recommendation/evidence/manifests/rec-ev-019f-validation.json"
RUNNER_SOURCE = Path(__file__).resolve()
VERIFIER_SOURCE = ROOT / "scripts/verify_rec_ev_019f_independent_temporal_routing.py"
VALIDATOR_SOURCE = ROOT / "scripts/validate_rec_ev_019f_contract.py"
HELPER_SOURCES = (
    ROOT / "scripts/recommendation_binary_onboarding_preflight.py",
    ROOT / "scripts/build_rec_ev_019a_cohorts.py",
)


class AuthorizationError(RuntimeError):
    """Raised before data access when the requested operation is not authorized."""


class InputFirewallError(RuntimeError):
    """Raised before a non-allowlisted path can be opened."""


class ResumeError(RuntimeError):
    """Raised when a lock or checkpoint cannot safely be reused."""


def user_key(user_id: int) -> str:
    return hashlib.sha256(f"feelm-ml32m-user-v1|{int(user_id)}".encode("utf-8")).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_bytes(path, canonical_json_bytes(value))


def repo_relative(path: Path, *, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise InputFirewallError("path is outside the repository") from error


class InputFirewall:
    def __init__(self, contract: Mapping[str, Any], *, root: Path = ROOT) -> None:
        self.root = root.resolve()
        self.entries = dict(contract["allowed_input_artifacts"])
        self.allowed = {str(entry["path"]) for entry in self.entries.values()}
        self.forbidden = set(map(str, contract["forbidden_input_artifacts"]))

    def validate(self, name: str) -> Path:
        if name not in self.entries:
            raise InputFirewallError("unknown input artifact key")
        path = (self.root / str(self.entries[name]["path"])).resolve()
        relative = repo_relative(path, root=self.root)
        if relative in self.forbidden:
            raise InputFirewallError("forbidden input artifact class")
        if relative not in self.allowed:
            raise InputFirewallError("unknown input artifact class")
        return path

    def validate_external(self, path: str | Path) -> Path:
        candidate = Path(path)
        absolute = candidate.resolve() if candidate.is_absolute() else (self.root / candidate).resolve()
        relative = repo_relative(absolute, root=self.root)
        if relative in self.forbidden:
            raise InputFirewallError("forbidden input artifact class")
        if relative not in self.allowed:
            raise InputFirewallError("unknown input artifact class")
        return absolute


def verify_sources(contract: Mapping[str, Any], firewall: InputFirewall) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for name in sorted(contract["allowed_input_artifacts"]):
        expected = contract["allowed_input_artifacts"][name]
        path = firewall.validate(name)
        if not path.is_file():
            raise FileNotFoundError(f"required allowlisted artifact is absent: {name}")
        if path.stat().st_size != int(expected["bytes"]):
            raise RuntimeError(f"source byte-size drift: {name}")
        digest = sha256_file(path)
        if digest != str(expected["sha256"]):
            raise RuntimeError(f"source SHA-256 drift: {name}")
        artifacts.append({
            "name": name,
            "path": repo_relative(path, root=firewall.root),
            "bytes": path.stat().st_size,
            "sha256": digest,
        })
    return artifacts


def source_code_attestation(*, root: Path) -> dict[str, dict[str, str]]:
    paths = {
        "runner": root / RUNNER_SOURCE.relative_to(ROOT),
        "verifier": root / VERIFIER_SOURCE.relative_to(ROOT),
        "contract_validator": root / VALIDATOR_SOURCE.relative_to(ROOT),
        "binary_label_helper": root / HELPER_SOURCES[0].relative_to(ROOT),
        "user_key_helper": root / HELPER_SOURCES[1].relative_to(ROOT),
    }
    for name, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"required source is absent before lock: {name}")
    return {
        name: {"path": repo_relative(path, root=root), "sha256": sha256_file(path)}
        for name, path in paths.items()
    }


def preregistered_spec(contract: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "evidence_classification": contract["evidence_classification"],
        "episode": contract["episode"],
        "cohort": contract["cohort"],
        "model": contract["model"],
        "frozen_routing_semantics": contract["frozen_routing_semantics"],
        "scoring": contract["scoring"],
        "metrics": contract["metrics"],
        "bootstrap": contract["bootstrap"],
        "decision_rule": contract["decision_rule"],
    }


def git_attestation(root: Path) -> dict[str, Any]:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.replace("\r\n", "\n")
    lines = [line for line in status.splitlines() if line]
    return {
        "revision": revision,
        "dirty": bool(lines),
        "status_porcelain": lines,
        "status_sha256": sha256_bytes(("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")),
    }


def create_or_verify_lock(
    contract: Mapping[str, Any],
    *,
    contract_path: Path,
    firewall: InputFirewall,
    resume: bool,
) -> dict[str, Any]:
    output_root = firewall.root / contract["output_root"]
    lock_path = output_root / contract["outputs"]["protocol_lock"]
    source_manifest_path = output_root / contract["outputs"]["source_manifest"]
    sources = verify_sources(contract, firewall)
    contract_sha = sha256_text(contract_path)
    prereg_path = firewall.root / DEFAULT_PREREGISTRATION.relative_to(ROOT)
    prereg_sha = sha256_text(prereg_path)
    code = source_code_attestation(root=firewall.root)
    source_digest = sha256_bytes(canonical_json_bytes(sources))
    spec_sha = sha256_bytes(canonical_json_bytes(preregistered_spec(contract)))
    source_manifest = {
        "schema_version": 1,
        "evidence_id": "REC-EV-019F",
        "classification": "PREREGISTERED_SOURCE_ROW_AND_TEMPORAL_WINDOW_CONFIRMATION",
        "independence_unit": "SOURCE_ROW_AND_TEMPORAL_WINDOW",
        "user_independent": False,
        "contract_path": repo_relative(contract_path, root=firewall.root),
        "contract_sha256": contract_sha,
        "preregistration_path": repo_relative(prereg_path, root=firewall.root),
        "preregistration_sha256": prereg_sha,
        "source_code": code,
        "artifacts": sources,
        "ranking_metrics_read": False,
        "eligibility_counts_observed": True,
        "observed_audit_expectations_are_not_blind": contract["evidence_classification"]["observed_audit_expectations_are_not_blind"],
        "rec_ev_019d_predictions_reused": False,
        "locked_test_used": False,
        "champion": None,
        "product_policy_updated": False,
    }
    expected_manifest_sha = sha256_bytes(canonical_json_bytes(source_manifest))
    if lock_path.is_file():
        if not resume:
            raise ResumeError("existing REC-EV-019F protocol lock requires --resume")
        lock = read_json(lock_path)
        checks = {
            "contract_sha256": contract_sha,
            "preregistration_sha256": prereg_sha,
            "source_artifacts_sha256": source_digest,
            "preregistered_spec_sha256": spec_sha,
            "source_code": code,
            "ranking_metrics_read": False,
            "eligibility_counts_observed": True,
        }
        for key, expected in checks.items():
            if lock.get(key) != expected:
                raise ResumeError(f"protocol lock mismatch: {key}")
        if not source_manifest_path.is_file() or sha256_file(source_manifest_path) != expected_manifest_sha:
            raise ResumeError("source manifest drift after protocol lock")
        if lock.get("git", {}).get("dirty") is not False:
            raise ResumeError("protocol lock was not created from a clean preregistration commit")
        return lock

    forbidden_existing = [
        output_root / contract["outputs"][name]
        for name in (
            "structural_cohort", "strict_cohort", "prefixes", "windows", "arm_definitions",
            "rankings", "user_arm_metrics", "paired_deltas", "strata", "result", "progress",
        )
    ]
    if any(path.exists() for path in forbidden_existing):
        raise RuntimeError("REC-EV-019F result artifact exists before protocol lock")
    git = git_attestation(firewall.root)
    if git["dirty"]:
        raise RuntimeError("REC-EV-019F lock requires a clean preregistration commit")
    output_root.mkdir(parents=True, exist_ok=True)
    atomic_write_json(source_manifest_path, source_manifest)
    lock = {
        "schema_version": 1,
        "evidence_id": "REC-EV-019F",
        "status": "PREREGISTERED_BEFORE_019F_RANKING_METRICS",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "created_at_epoch_ns": time.time_ns(),
        "contract_path": repo_relative(contract_path, root=firewall.root),
        "contract_sha256": contract_sha,
        "preregistration_path": repo_relative(prereg_path, root=firewall.root),
        "preregistration_sha256": prereg_sha,
        "source_manifest_path": repo_relative(source_manifest_path, root=firewall.root),
        "source_manifest_sha256": expected_manifest_sha,
        "source_artifacts_sha256": source_digest,
        "preregistered_spec_sha256": spec_sha,
        "source_code": code,
        "git": git,
        "ranking_metrics_read": False,
        "ranking_metrics_read_definition": "No REC-EV-019F comparator/candidate ranking metric, delta, bootstrap, or decision existed or was read before this lock; the structural/strict eligibility counts had already been audited and are disclosed.",
        "eligibility_counts_observed": True,
        "independence_unit": "SOURCE_ROW_AND_TEMPORAL_WINDOW",
        "user_independent": False,
        "locked_test_used": False,
        "champion": None,
        "product_policy_updated": False,
    }
    atomic_write_json(lock_path, lock)
    return lock


def base_train_midrank_from_manifest(manifest: Mapping[str, Any]) -> np.ndarray:
    counts = manifest["splits"]["train"]["rating_value_counts"]
    ordered = np.asarray(
        [int(counts[str(float(value))]) for value in np.arange(0.5, 5.01, 0.5)],
        dtype=np.float64,
    )
    cumulative_before = np.cumsum(ordered) - ordered
    return (cumulative_before + 0.5 * ordered) / ordered.sum()


def validation_bucket_frame(path: Path, *, protocol: Mapping[str, Any]) -> pd.DataFrame:
    prefix = split_prefix(dict(protocol))
    chunks: list[pd.DataFrame] = []
    parquet = pq.ParquetFile(path)
    for batch in parquet.iter_batches(columns=["user_id", "movie_id", "rating", "timestamp"], batch_size=262_144):
        frame = batch.to_pandas()
        buckets = frame["user_id"].map(lambda value: stable_user_bucket(int(value), split_prefix=prefix)).to_numpy(dtype=np.int16)
        mask = (buckets >= 50) & (buckets <= 59)
        if bool(mask.any()):
            chunks.append(frame.loc[mask].copy())
    if not chunks:
        raise RuntimeError("Validation bucket 50..59 is empty")
    return pd.concat(chunks, ignore_index=True).sort_values(
        ["user_id", "timestamp", "movie_id"], kind="stable", ignore_index=True,
    )


STRUCTURAL_SCHEMA = pa.schema([
    ("user_key", pa.string()),
    ("old_k10_tenth_source_position", pa.int32()),
    ("tail_start_source_position", pa.int32()),
    ("fresh_k10_tenth_source_position", pa.int32()),
    ("future_start_source_position", pa.int32()),
    ("future_end_source_position", pa.int32()),
    ("historical_019a_k10_user", pa.bool_()),
    ("historical_019a_any_user", pa.bool_()),
    ("independence_unit", pa.string()),
    ("user_independent", pa.bool_()),
])

STRICT_SCHEMA = pa.schema([
    ("user_key", pa.string()),
    ("future_positive_count", pa.int8()),
    ("candidate_positive_count", pa.int8()),
    ("historical_019a_k10_user", pa.bool_()),
    ("historical_019a_any_user", pa.bool_()),
    ("completely_new_to_019a_validation", pa.bool_()),
    ("historical_source_row_overlap", pa.int8()),
    ("independence_unit", pa.string()),
    ("user_independent", pa.bool_()),
])

PREFIX_SCHEMA = pa.schema([
    ("role", pa.string()),
    ("user_key", pa.string()),
    ("input_rank", pa.int8()),
    ("movie_id", pa.int32()),
    ("binary_label", pa.int8()),
    ("relative_utility", pa.float32()),
    ("source_position", pa.int32()),
    ("source_row_id", pa.string()),
    ("timestamp", pa.int64()),
])

WINDOW_SCHEMA = pa.schema([
    ("role", pa.string()),
    ("user_key", pa.string()),
    ("window_rank", pa.int8()),
    ("movie_id", pa.int32()),
    ("rating", pa.float32()),
    ("midrank_utility", pa.float32()),
    ("is_positive", pa.bool_()),
    ("is_negative", pa.bool_()),
    ("candidate_present", pa.bool_()),
    ("source_position", pa.int32()),
    ("source_row_id", pa.string()),
    ("timestamp", pa.int64()),
])

ARM_SCHEMA = pa.schema([
    ("user_key", pa.string()),
    ("profile", pa.string()),
    ("input_count", pa.int8()),
    ("candidate_valid_count", pa.int8()),
    ("candidate_positive_count", pa.int8()),
    ("candidate_negative_count", pa.int8()),
    ("applicable", pa.bool_()),
    ("fallback_user", pa.bool_()),
    ("profile_movie_ids", pa.list_(pa.int32())),
    ("profile_labels", pa.list_(pa.int8())),
    ("candidate_valid_movie_ids", pa.list_(pa.int32())),
    ("common_k10_seen_movie_ids", pa.list_(pa.int32())),
    ("full_catalog_rescored", pa.bool_()),
])

RANKING_SCHEMA = pa.schema([
    ("user_key", pa.string()),
    ("applicability_stratum", pa.string()),
    ("profile", pa.string()),
    ("model", pa.string()),
    ("rank", pa.int16()),
    ("movie_id", pa.int32()),
    ("effective_score", pa.float32()),
    ("fallback_used", pa.bool_()),
])

METRIC_SCHEMA = pa.schema([
    ("user_key", pa.string()),
    ("applicability_stratum", pa.string()),
    ("variant", pa.string()),
    ("source_profile", pa.string()),
    ("model", pa.string()),
    ("ndcg_at_10", pa.float64()),
    ("recall_at_10", pa.float64()),
    ("mrr_at_10", pa.float64()),
    ("candidate_recall_at_500", pa.float64()),
    ("positive_mean_rank_percentile", pa.float64()),
    ("harm_at_2", pa.bool_()),
    ("fallback_user", pa.bool_()),
    ("applicable_user", pa.bool_()),
])

PAIRED_SCHEMA = pa.schema([
    ("user_key", pa.string()),
    ("applicability_stratum", pa.string()),
    ("candidate_source_profile", pa.string()),
    ("delta_ndcg_at_10", pa.float64()),
    ("delta_recall_at_10", pa.float64()),
    ("delta_mrr_at_10", pa.float64()),
    ("delta_candidate_recall_at_500", pa.float64()),
    ("delta_positive_mean_rank_percentile", pa.float64()),
    ("delta_harm_at_2", pa.float64()),
    ("delta_fallback_user", pa.float64()),
    ("delta_applicable_user", pa.float64()),
])


def write_parquet_atomic(path: Path, rows: Sequence[Mapping[str, Any]], schema: pa.Schema) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    pq.write_table(pa.Table.from_pylist(list(rows), schema=schema), temporary, compression="zstd", use_dictionary=True)
    os.replace(temporary, path)


def combine_parquet_parts(paths: Sequence[Path], output: Path, schema: pa.Schema) -> None:
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    writer = pq.ParquetWriter(temporary, schema, compression="zstd", use_dictionary=True)
    try:
        for path in paths:
            writer.write_table(pq.read_table(path, schema=schema))
    finally:
        writer.close()
    os.replace(temporary, output)


def derive_episode(
    ratings: pd.DataFrame,
    *,
    global_midrank: np.ndarray,
    candidate_movie_ids: set[int],
    tuning_union: set[str],
    historical_k10_users: set[str],
    historical_any_users: set[str],
    shrinkage: float = 10.0,
    like_min: float = 0.15,
    dislike_max: float = -0.15,
    future_positive_min: float = 0.65,
    future_negative_max: float = 0.35,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    structural_rows: list[dict[str, Any]] = []
    strict_rows: list[dict[str, Any]] = []
    prefix_rows: list[dict[str, Any]] = []
    window_rows: list[dict[str, Any]] = []
    for raw_user_id, group in ratings.groupby("user_id", sort=True, observed=True):
        group = group.sort_values(["timestamp", "movie_id"], kind="stable", ignore_index=True)
        key = user_key(int(raw_user_id))
        values = group["rating"].to_numpy(dtype=np.float64, copy=False)
        movies = group["movie_id"].to_numpy(dtype=np.int64, copy=False)
        timestamps = group["timestamp"].to_numpy(dtype=np.int64, copy=False)
        historical_labels = sequential_binary_labels(
            values,
            global_midrank,
            shrinkage=shrinkage,
            like_min=like_min,
            dislike_max=dislike_max,
        )
        if len(historical_labels) < 10:
            continue
        old_tenth = int(historical_labels[9][0])
        tail_start = old_tenth + 11
        if key in tuning_union or tail_start >= len(values):
            continue
        tail_labels = sequential_binary_labels(
            values[tail_start:],
            global_midrank,
            shrinkage=shrinkage,
            like_min=like_min,
            dislike_max=dislike_max,
        )
        if len(tail_labels) < 10:
            continue
        selected = tail_labels[:10]
        fresh_tenth = tail_start + int(selected[-1][0])
        future_start = fresh_tenth + 1
        future_end = future_start + 9
        if future_end >= len(values):
            continue
        old_source_rows = {int(position) for position, _, _ in historical_labels[:10]}
        old_source_rows.update(range(old_tenth + 1, old_tenth + 11))
        fresh_prefix_positions = [tail_start + int(position) for position, _, _ in selected]
        fresh_window_positions = list(range(future_start, future_start + 10))
        overlap = len(old_source_rows.intersection(fresh_prefix_positions + fresh_window_positions))
        if overlap:
            raise RuntimeError("fresh episode overlaps historical prefix/evaluation source rows")
        structural_rows.append({
            "user_key": key,
            "old_k10_tenth_source_position": old_tenth,
            "tail_start_source_position": tail_start,
            "fresh_k10_tenth_source_position": fresh_tenth,
            "future_start_source_position": future_start,
            "future_end_source_position": future_end,
            "historical_019a_k10_user": key in historical_k10_users,
            "historical_019a_any_user": key in historical_any_users,
            "independence_unit": "SOURCE_ROW_AND_TEMPORAL_WINDOW",
            "user_independent": False,
        })
        for input_rank, (relative_position, label, utility) in enumerate(selected, start=1):
            position = tail_start + int(relative_position)
            prefix_rows.append({
                "role": "VALIDATION_019F_TEMPORAL",
                "user_key": key,
                "input_rank": input_rank,
                "movie_id": int(movies[position]),
                "binary_label": int(label),
                "relative_utility": float(utility),
                "source_position": position,
                "source_row_id": f"{key}:{position}",
                "timestamp": int(timestamps[position]),
            })
        future_ratings = values[future_start : future_start + 10]
        utilities = future_midrank_utilities(future_ratings)
        positives = utilities >= future_positive_min
        negatives = utilities <= future_negative_max
        candidate_positive_count = int(sum(
            bool(positive) and int(movie_id) in candidate_movie_ids
            for movie_id, positive in zip(movies[future_start : future_start + 10], positives, strict=True)
        ))
        for window_rank, offset in enumerate(range(10), start=1):
            position = future_start + offset
            window_rows.append({
                "role": "VALIDATION_019F_TEMPORAL",
                "user_key": key,
                "window_rank": window_rank,
                "movie_id": int(movies[position]),
                "rating": float(values[position]),
                # Metrics must consume the exact value persisted in the float32
                # window artifact so an independent artifact-only verifier is exact.
                "midrank_utility": float(np.float32(utilities[offset])),
                "is_positive": bool(positives[offset]),
                "is_negative": bool(negatives[offset]),
                "candidate_present": int(movies[position]) in candidate_movie_ids,
                "source_position": position,
                "source_row_id": f"{key}:{position}",
                "timestamp": int(timestamps[position]),
            })
        if int(positives.sum()) >= 3 and candidate_positive_count >= 1:
            strict_rows.append({
                "user_key": key,
                "future_positive_count": int(positives.sum()),
                "candidate_positive_count": candidate_positive_count,
                "historical_019a_k10_user": key in historical_k10_users,
                "historical_019a_any_user": key in historical_any_users,
                "completely_new_to_019a_validation": key not in historical_any_users,
                "historical_source_row_overlap": overlap,
                "independence_unit": "SOURCE_ROW_AND_TEMPORAL_WINDOW",
                "user_independent": False,
            })
    structural_rows.sort(key=lambda row: row["user_key"])
    strict_rows.sort(key=lambda row: row["user_key"])
    prefix_rows.sort(key=lambda row: (row["user_key"], row["input_rank"]))
    window_rows.sort(key=lambda row: (row["user_key"], row["window_rank"]))
    return structural_rows, strict_rows, prefix_rows, window_rows


def midrank_percentiles(values: np.ndarray) -> np.ndarray:
    scores = np.asarray(values, dtype=np.float64)
    if scores.ndim != 1 or not np.isfinite(scores).all():
        raise ValueError("scores must be finite and one-dimensional")
    order = np.argsort(scores, kind="mergesort")
    sorted_scores = scores[order]
    boundaries = np.r_[0, np.flatnonzero(sorted_scores[1:] != sorted_scores[:-1]) + 1, len(scores)]
    result = np.empty(len(scores), dtype=np.float32)
    for start, stop in zip(boundaries[:-1], boundaries[1:], strict=True):
        result[order[start:stop]] = np.float32((start + stop) / (2.0 * len(scores)))
    return result


def fold_in_profile(
    item_biases: np.ndarray,
    item_factors: np.ndarray,
    positions: Sequence[int],
    labels: Sequence[int],
    *,
    regularization: float,
    learning_rate: float,
    epochs: int,
) -> tuple[float, np.ndarray, bool]:
    observed = np.asarray(positions, dtype=np.int32)
    signs = np.asarray(labels, dtype=np.int8)
    if len(observed) != len(signs) or not len(observed) or not ({-1, 1} <= set(signs.tolist())):
        return 0.0, np.zeros(item_factors.shape[1], dtype=np.float32), True
    user_bias = 0.0
    user_vector = np.zeros(item_factors.shape[1], dtype=np.float64)
    frozen_biases = np.asarray(item_biases, dtype=np.float64)
    frozen_factors = np.asarray(item_factors, dtype=np.float64)
    for _ in range(int(epochs)):
        for position, label in zip(observed, signs, strict=True):
            item_vector = frozen_factors[int(position)]
            score = user_bias + frozen_biases[int(position)] + float(user_vector @ item_vector)
            signed_margin = float(label) * score
            factor = (
                -float(label) * math.exp(-signed_margin) / (1.0 + math.exp(-signed_margin))
                if signed_margin >= 0
                else -float(label) / (1.0 + math.exp(signed_margin))
            )
            user_vector -= learning_rate * (factor * item_vector + regularization * user_vector)
            user_bias -= learning_rate * factor
    return user_bias, user_vector.astype(np.float32), False


def deterministic_top_indices(candidate_ids: np.ndarray, scores: np.ndarray, *, top_n: int) -> np.ndarray:
    finite = np.flatnonzero(np.isfinite(scores))
    keep = min(int(top_n), len(finite))
    if keep < len(finite):
        finite = finite[np.argpartition(scores[finite], -keep)[-keep:]]
    order = np.lexsort((candidate_ids[finite], -scores[finite]))
    return finite[order[:keep]].astype(np.int64, copy=False)


def ranking_metrics(
    candidate_ids: np.ndarray,
    scores: np.ndarray,
    future_rows: Sequence[Mapping[str, Any]],
    *,
    fallback_user: bool,
) -> tuple[dict[str, Any], np.ndarray]:
    top_indices = deterministic_top_indices(candidate_ids, scores, top_n=500)
    ranked_ids = candidate_ids[top_indices].astype(np.int64)
    rank_by_movie = {int(movie_id): rank for rank, movie_id in enumerate(ranked_ids, start=1)}
    candidate_set = set(map(int, candidate_ids.tolist()))
    positives = [row for row in future_rows if bool(row["is_positive"]) and int(row["movie_id"]) in candidate_set]
    negatives = {int(row["movie_id"]) for row in future_rows if bool(row["is_negative"])}
    gains = {int(row["movie_id"]): float(row["midrank_utility"]) for row in positives}
    ideal = sorted(gains.values(), reverse=True)[:10]
    idcg = sum(gain / math.log2(rank + 1) for rank, gain in enumerate(ideal, start=1))
    dcg = sum(
        gains[movie_id] / math.log2(rank + 1)
        for movie_id, rank in rank_by_movie.items()
        if movie_id in gains and rank <= 10
    )
    first_positive = min((rank_by_movie[movie_id] for movie_id in gains if movie_id in rank_by_movie), default=None)
    movie_position = {int(movie_id): position for position, movie_id in enumerate(candidate_ids)}
    exact_ranks: list[int] = []
    for movie_id in gains:
        position = movie_position[movie_id]
        score = float(scores[position])
        if math.isfinite(score):
            exact_ranks.append(1 + int(np.count_nonzero(scores > score)) + int(np.count_nonzero((scores == score) & (candidate_ids < movie_id))))
    finite_count = max(1, int(np.count_nonzero(np.isfinite(scores))))
    return {
        "ndcg_at_10": float(dcg / idcg) if idcg else 0.0,
        "recall_at_10": float(sum(rank_by_movie.get(movie_id, 11) <= 10 for movie_id in gains) / len(gains)) if gains else 0.0,
        "mrr_at_10": float(1.0 / first_positive) if first_positive is not None and first_positive <= 10 else 0.0,
        "candidate_recall_at_500": float(any(movie_id in rank_by_movie for movie_id in gains)),
        "positive_mean_rank_percentile": float(np.mean([(rank - 1) / max(1, finite_count - 1) for rank in exact_ranks])) if exact_ranks else None,
        "harm_at_2": bool(set(map(int, ranked_ids[:2])).intersection(negatives)),
        "fallback_user": bool(fallback_user),
        "applicable_user": not bool(fallback_user),
    }, top_indices


def route_for_applicability(k5_applicable: bool, k10_applicable: bool) -> tuple[str, str, str]:
    if k5_applicable:
        return "BOTH_LIGHTFM", "K5", "K5_FOLD_IN"
    if k10_applicable:
        return "K10_NEWLY_APPLICABLE", "K10", "K10_FOLD_IN"
    return "BOTH_FALLBACK", "K5", "B0"


def bootstrap_paired(ndcg: np.ndarray, harm: np.ndarray, *, iterations: int, seed: int) -> dict[str, Any]:
    ndcg_values = np.asarray(ndcg, dtype=np.float64)
    harm_values = np.asarray(harm, dtype=np.float64)
    if len(ndcg_values) != len(harm_values) or not len(ndcg_values):
        raise ValueError("paired bootstrap needs equally sized nonempty arrays")
    rng = np.random.default_rng(int(seed))
    ndcg_means = np.empty(int(iterations), dtype=np.float64)
    harm_means = np.empty(int(iterations), dtype=np.float64)
    offset = 0
    while offset < int(iterations):
        stop = min(offset + 250, int(iterations))
        indices = rng.integers(0, len(ndcg_values), size=(stop - offset, len(ndcg_values)))
        ndcg_means[offset:stop] = ndcg_values[indices].mean(axis=1)
        harm_means[offset:stop] = harm_values[indices].mean(axis=1)
        offset = stop
    return {
        "iterations": int(iterations),
        "seed": int(seed),
        "ndcg_mean": float(ndcg_values.mean()),
        "ndcg_two_sided_95": [float(np.percentile(ndcg_means, 2.5)), float(np.percentile(ndcg_means, 97.5))],
        "harm_mean": float(harm_values.mean()),
        "harm_one_sided_95_upper": float(np.percentile(harm_means, 95.0)),
    }


def decide(bootstrap: Mapping[str, Any]) -> dict[str, str]:
    if float(bootstrap["harm_one_sided_95_upper"]) > 0.005:
        return {"status": "FAIL", "reason": "HARM_UPPER_EXCEEDS_0_005"}
    if float(bootstrap["ndcg_mean"]) >= 0.005 and float(bootstrap["ndcg_two_sided_95"][0]) > 0.0:
        return {
            "status": "PASS_INDEPENDENT_TEMPORAL_WINDOW_REQUIRES_TARGET_DOMAIN_CONFIRMATION",
            "reason": "TEMPORAL_WINDOW_EFFICACY_AND_SAFETY_THRESHOLDS_MET",
        }
    return {"status": "INCONCLUSIVE", "reason": "TEMPORAL_WINDOW_SUCCESS_NOT_ESTABLISHED"}


def delta_or_none(candidate: float | None, comparator: float | None) -> float | None:
    if candidate is None or comparator is None:
        return None
    return float(candidate - comparator)


def aggregate(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "users": int(frame["user_key"].nunique()),
        "ndcg_at_10": float(frame["ndcg_at_10"].mean()),
        "recall_at_10": float(frame["recall_at_10"].mean()),
        "mrr_at_10": float(frame["mrr_at_10"].mean()),
        "candidate_recall_at_500": float(frame["candidate_recall_at_500"].mean()),
        "positive_mean_rank_percentile": float(frame["positive_mean_rank_percentile"].mean()),
        "harm_at_2": float(frame["harm_at_2"].astype(float).mean()),
        "fallback_user_rate": float(frame["fallback_user"].astype(float).mean()),
        "applicability_rate": float(frame["applicable_user"].astype(float).mean()),
    }


def benefit_harm_counts(frame: pd.DataFrame) -> dict[str, int]:
    delta = frame["delta_ndcg_at_10"].to_numpy(dtype=np.float64)
    return {
        "benefit": int(np.count_nonzero(delta > 0.0)),
        "neutral": int(np.count_nonzero(delta == 0.0)),
        "harm": int(np.count_nonzero(delta < 0.0)),
    }


def artifact_entry(path: Path, *, root: Path) -> dict[str, Any]:
    return {"path": repo_relative(path, root=root), "bytes": path.stat().st_size, "sha256": sha256_file(path)}


def checkpoint_signature(lock: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes({
        "contract_sha256": lock["contract_sha256"],
        "preregistration_sha256": lock["preregistration_sha256"],
        "source_artifacts_sha256": lock["source_artifacts_sha256"],
        "source_code": lock["source_code"],
        "git_revision": lock["git"]["revision"],
    }))


def run_validation(
    contract: Mapping[str, Any],
    *,
    contract_path: Path,
    firewall: InputFirewall,
    lock: Mapping[str, Any],
    resume: bool,
) -> dict[str, Any]:
    if not contract["current_authorization"].get("validation_full_catalog_scoring", False):
        raise AuthorizationError("REC-EV-019F Validation scoring is not authorized")
    if not resume:
        raise ResumeError("REC-EV-019F real Validation requires --resume")
    run_started_ns = time.time_ns()
    if int(lock["created_at_epoch_ns"]) >= run_started_ns or lock["ranking_metrics_read"] is not False:
        raise RuntimeError("protocol lock did not precede REC-EV-019F ranking metrics")
    if lock["git"].get("dirty") is not False:
        raise RuntimeError("protocol lock was not created from a clean preregistration commit")
    prereg_path = firewall.root / DEFAULT_PREREGISTRATION.relative_to(ROOT)
    if sha256_text(contract_path) != lock["contract_sha256"] or sha256_text(prereg_path) != lock["preregistration_sha256"]:
        raise RuntimeError("contract or preregistration changed after protocol lock")
    if source_code_attestation(root=firewall.root) != lock["source_code"]:
        raise RuntimeError("runner/verifier/validator/helper source changed after protocol lock")
    verify_sources(contract, firewall)

    global_manifest = read_json(firewall.validate("global_time_manifest"))
    protocol = read_json(firewall.validate("evaluation_protocol"))
    global_midrank = base_train_midrank_from_manifest(global_manifest)
    candidate = pq.read_table(firewall.validate("candidate_core"), columns=["movie_id", "b0_score"]).to_pandas()
    if len(candidate) != 41625:
        raise RuntimeError("candidate count drift")
    candidate_ids = candidate["movie_id"].to_numpy(dtype=np.int64)
    if np.any(candidate_ids[1:] <= candidate_ids[:-1]):
        raise RuntimeError("candidate IDs are not unique ascending values")
    candidate_set = set(map(int, candidate_ids))
    candidate_position = {movie_id: position for position, movie_id in enumerate(candidate_ids)}
    historical_prefixes = pq.read_table(
        firewall.validate("historical_validation_prefixes"), columns=["user_key", "k"],
    ).to_pandas()
    historical_windows = pq.read_table(
        firewall.validate("historical_validation_windows"), columns=["user_key", "k"],
    ).to_pandas()
    historical_k10_users = set(historical_prefixes.loc[historical_prefixes["k"] == 10, "user_key"].astype(str))
    historical_any_users = set(historical_prefixes["user_key"].astype(str)) | set(historical_windows["user_key"].astype(str))
    selection = read_json(firewall.validate("validation_selection"))
    tuning_union = set(map(str, selection["tuning_panel"]["5"])) | set(map(str, selection["tuning_panel"]["10"]))
    if len(tuning_union) != int(contract["cohort"]["expected_tuning_union_users_observed"]):
        raise RuntimeError(f"tuning union drift: observed {len(tuning_union)}")
    ratings = validation_bucket_frame(firewall.validate("validation_ratings"), protocol=protocol)
    structural_rows, strict_rows, prefix_rows, window_rows = derive_episode(
        ratings,
        global_midrank=global_midrank,
        candidate_movie_ids=candidate_set,
        tuning_union=tuning_union,
        historical_k10_users=historical_k10_users,
        historical_any_users=historical_any_users,
        shrinkage=float(contract["episode"]["binary_shrinkage_lambda"]),
        like_min=float(contract["episode"]["binary_relative_like_min"]),
        dislike_max=float(contract["episode"]["binary_relative_dislike_max"]),
        future_positive_min=float(contract["episode"]["future_positive_midrank_min"]),
        future_negative_max=float(contract["episode"]["future_negative_midrank_max"]),
    )
    observed = {
        "structural_users": len(structural_rows),
        "strict_users": len(strict_rows),
        "existing_019a_k10_users": sum(bool(row["historical_019a_k10_user"]) for row in strict_rows),
        "outside_existing_019a_k10_users": sum(not bool(row["historical_019a_k10_user"]) for row in strict_rows),
        "completely_new_to_019a_validation_users": sum(bool(row["completely_new_to_019a_validation"]) for row in strict_rows),
    }
    expected = contract["evidence_classification"]["observed_audit_expectations_are_not_blind"]
    if observed != {key: int(expected[key]) for key in observed}:
        raise RuntimeError(f"pre-observed eligibility audit drift: {observed}")

    output_root = firewall.root / contract["output_root"]
    paths = {name: output_root / relative for name, relative in contract["outputs"].items() if name != "checkpoints"}
    write_parquet_atomic(paths["structural_cohort"], structural_rows, STRUCTURAL_SCHEMA)
    write_parquet_atomic(paths["strict_cohort"], strict_rows, STRICT_SCHEMA)
    write_parquet_atomic(paths["prefixes"], prefix_rows, PREFIX_SCHEMA)
    write_parquet_atomic(paths["windows"], window_rows, WINDOW_SCHEMA)

    strict_users = [row["user_key"] for row in strict_rows]
    strict_set = set(strict_users)
    prefix_groups = {
        str(key): group.sort_values("input_rank", kind="stable")
        for key, group in pd.DataFrame(prefix_rows).groupby("user_key", sort=True)
        if str(key) in strict_set
    }
    window_groups = {
        str(key): group.sort_values("window_rank", kind="stable")
        for key, group in pd.DataFrame(window_rows).groupby("user_key", sort=True)
        if str(key) in strict_set
    }
    contexts: dict[str, dict[str, Any]] = {}
    arm_rows: list[dict[str, Any]] = []
    for key in strict_users:
        prefix = prefix_groups[key]
        movies = prefix["movie_id"].to_numpy(dtype=np.int64)
        labels = prefix["binary_label"].to_numpy(dtype=np.int8)
        arm_data: dict[str, dict[str, Any]] = {}
        for profile, count in (("K5", 5), ("K10", 10)):
            profile_movies = list(map(int, movies[:count]))
            profile_labels = list(map(int, labels[:count]))
            valid = [(candidate_position[movie], movie, label) for movie, label in zip(profile_movies, profile_labels, strict=True) if movie in candidate_position]
            valid_labels = np.asarray([row[2] for row in valid], dtype=np.int8)
            applicable = {-1, 1} <= set(map(int, valid_labels))
            arm_data[profile] = {
                "positions": np.asarray([row[0] for row in valid], dtype=np.int32),
                "labels": valid_labels,
                "applicable": applicable,
                "valid_movie_ids": [row[1] for row in valid],
            }
        if arm_data["K5"]["applicable"] and not arm_data["K10"]["applicable"]:
            raise RuntimeError("K10 applicability cannot be lost after K5")
        stratum, candidate_profile, candidate_model = route_for_applicability(
            bool(arm_data["K5"]["applicable"]), bool(arm_data["K10"]["applicable"]),
        )
        common_seen = list(map(int, arm_data["K10"]["valid_movie_ids"]))
        for profile, count in (("K5", 5), ("K10", 10)):
            data = arm_data[profile]
            arm_rows.append({
                "user_key": key,
                "profile": profile,
                "input_count": count,
                "candidate_valid_count": len(data["valid_movie_ids"]),
                "candidate_positive_count": int(np.count_nonzero(data["labels"] == 1)),
                "candidate_negative_count": int(np.count_nonzero(data["labels"] == -1)),
                "applicable": bool(data["applicable"]),
                "fallback_user": not bool(data["applicable"]),
                "profile_movie_ids": list(map(int, movies[:count])),
                "profile_labels": list(map(int, labels[:count])),
                "candidate_valid_movie_ids": list(map(int, data["valid_movie_ids"])),
                "common_k10_seen_movie_ids": common_seen,
                "full_catalog_rescored": True,
            })
        contexts[key] = {
            "arms": arm_data,
            "stratum": stratum,
            "candidate_profile": candidate_profile,
            "candidate_model": candidate_model,
            "future": [
                {"movie_id": int(row.movie_id), "midrank_utility": float(row.midrank_utility), "is_positive": bool(row.is_positive), "is_negative": bool(row.is_negative)}
                for row in window_groups[key].itertuples(index=False)
            ],
        }
    write_parquet_atomic(paths["arm_definitions"], arm_rows, ARM_SCHEMA)

    config = read_json(firewall.validate("lightfm_config"))
    if config != contract["model"]["lightfm_config"]:
        raise RuntimeError("frozen LightFM config drift")
    with np.load(firewall.validate("lightfm_result"), allow_pickle=False) as fitted:
        item_biases = fitted["item_biases"].astype(np.float32)
        item_factors = fitted["item_factors"].astype(np.float32)
    if item_biases.shape != (41625,) or item_factors.shape != (41625, int(config["dimension"])):
        raise RuntimeError("frozen LightFM representation shape drift")
    if not np.isfinite(item_biases).all() or not np.isfinite(item_factors).all():
        raise RuntimeError("frozen LightFM representation contains non-finite values")
    b0_percentiles = midrank_percentiles(candidate["b0_score"].to_numpy(dtype=np.float64))

    checkpoints = output_root / contract["outputs"]["checkpoints"]
    checkpoints.mkdir(parents=True, exist_ok=True)
    signature = checkpoint_signature(lock)
    progress_path = paths["progress"]
    if progress_path.is_file():
        progress = read_json(progress_path)
        if progress.get("resume_signature") != signature:
            raise ResumeError("run progress belongs to another lock")
    else:
        progress = {
            "schema_version": 1,
            "evidence_id": "REC-EV-019F",
            "status": "RUNNING",
            "resume_signature": signature,
            "run_started_at_utc": datetime.now(timezone.utc).isoformat(),
            "run_started_epoch_ns": run_started_ns,
            "protocol_lock_created_at_epoch_ns": int(lock["created_at_epoch_ns"]),
            "completed_batches": [],
            "independence_unit": "SOURCE_ROW_AND_TEMPORAL_WINDOW",
            "user_independent": False,
            "locked_test_used": False,
            "champion": None,
            "product_policy_updated": False,
        }
        atomic_write_json(progress_path, progress)
    batch_size = min(32, int(contract["resource_bounds"]["user_batch_size_max"]))
    completed = set(map(int, progress.get("completed_batches", [])))
    ranking_parts: list[Path] = []
    metric_parts: list[Path] = []
    for batch_index, start in enumerate(range(0, len(strict_users), batch_size)):
        batch_users = strict_users[start : start + batch_size]
        ranking_part = checkpoints / f"batch-{batch_index:05d}-rankings.parquet"
        metric_part = checkpoints / f"batch-{batch_index:05d}-metrics.parquet"
        done_path = checkpoints / f"batch-{batch_index:05d}-done.json"
        ranking_parts.append(ranking_part)
        metric_parts.append(metric_part)
        if batch_index in completed:
            if not all(path.is_file() for path in (ranking_part, metric_part, done_path)):
                raise ResumeError("completed checkpoint is incomplete")
            done = read_json(done_path)
            if done.get("resume_signature") != signature:
                raise ResumeError("checkpoint signature mismatch")
            if done.get("ranking_sha256") != sha256_file(ranking_part) or done.get("metric_sha256") != sha256_file(metric_part):
                raise ResumeError("checkpoint artifact hash mismatch")
            continue
        rankings: list[dict[str, Any]] = []
        profile_values: dict[tuple[str, str], dict[str, Any]] = {}
        for profile in ("K5", "K10"):
            biases: list[float] = []
            vectors: list[np.ndarray] = []
            fallback_flags: list[bool] = []
            for key in batch_users:
                arm = contexts[key]["arms"][profile]
                bias, vector, fallback = fold_in_profile(
                    item_biases,
                    item_factors,
                    arm["positions"],
                    arm["labels"],
                    regularization=float(config["user_alpha"]),
                    learning_rate=float(config["learning_rate"]),
                    epochs=int(contract["model"]["target_fold_in"]["epochs"]),
                )
                if fallback == bool(arm["applicable"]):
                    raise RuntimeError("fold-in fallback/applicability mismatch")
                biases.append(float(bias))
                vectors.append(vector)
                fallback_flags.append(bool(fallback))
            score_matrix = np.vstack(vectors).astype(np.float32) @ item_factors.T
            score_matrix += item_biases[None, :]
            score_matrix += np.asarray(biases, dtype=np.float32)[:, None]
            for row_index, key in enumerate(batch_users):
                fallback = fallback_flags[row_index]
                effective = b0_percentiles.copy() if fallback else midrank_percentiles(score_matrix[row_index])
                effective[contexts[key]["arms"]["K10"]["positions"]] = -np.inf
                values, top_indices = ranking_metrics(candidate_ids, effective, contexts[key]["future"], fallback_user=fallback)
                profile_values[(key, profile)] = values
                model = "B0" if fallback else f"{profile}_FOLD_IN"
                rankings.extend({
                    "user_key": key,
                    "applicability_stratum": contexts[key]["stratum"],
                    "profile": profile,
                    "model": model,
                    "rank": rank,
                    "movie_id": int(candidate_ids[position]),
                    "effective_score": float(effective[position]),
                    "fallback_used": fallback,
                } for rank, position in enumerate(top_indices, start=1))
        metrics: list[dict[str, Any]] = []
        for key in batch_users:
            context = contexts[key]
            candidate_profile = str(context["candidate_profile"])
            for variant, profile, model in (
                ("COMPARATOR", "K5", "B0" if not context["arms"]["K5"]["applicable"] else "K5_FOLD_IN"),
                ("CANDIDATE", candidate_profile, context["candidate_model"]),
            ):
                metrics.append({
                    "user_key": key,
                    "applicability_stratum": context["stratum"],
                    "variant": variant,
                    "source_profile": profile,
                    "model": model,
                    **profile_values[(key, profile)],
                })
        rankings.sort(key=lambda row: (row["profile"], row["user_key"], row["rank"]))
        metrics.sort(key=lambda row: (row["variant"], row["user_key"]))
        write_parquet_atomic(ranking_part, rankings, RANKING_SCHEMA)
        write_parquet_atomic(metric_part, metrics, METRIC_SCHEMA)
        atomic_write_json(done_path, {
            "resume_signature": signature,
            "batch_index": batch_index,
            "users": batch_users,
            "profiles_full_catalog_rescored": ["K5", "K10"],
            "candidate_count_per_profile": 41625,
            "ranking_sha256": sha256_file(ranking_part),
            "metric_sha256": sha256_file(metric_part),
        })
        completed.add(batch_index)
        progress["completed_batches"] = sorted(completed)
        progress["last_completed_batch"] = batch_index
        atomic_write_json(progress_path, progress)

    combine_parquet_parts(ranking_parts, paths["rankings"], RANKING_SCHEMA)
    combine_parquet_parts(metric_parts, paths["user_arm_metrics"], METRIC_SCHEMA)
    metrics = pq.read_table(paths["user_arm_metrics"]).to_pandas()
    paired_rows: list[dict[str, Any]] = []
    for key, group in metrics.groupby("user_key", sort=True):
        by_variant = {str(row.variant): row for row in group.itertuples(index=False)}
        comparator, routed = by_variant["COMPARATOR"], by_variant["CANDIDATE"]
        paired_rows.append({
            "user_key": str(key),
            "applicability_stratum": str(routed.applicability_stratum),
            "candidate_source_profile": str(routed.source_profile),
            "delta_ndcg_at_10": float(routed.ndcg_at_10 - comparator.ndcg_at_10),
            "delta_recall_at_10": float(routed.recall_at_10 - comparator.recall_at_10),
            "delta_mrr_at_10": float(routed.mrr_at_10 - comparator.mrr_at_10),
            "delta_candidate_recall_at_500": float(routed.candidate_recall_at_500 - comparator.candidate_recall_at_500),
            "delta_positive_mean_rank_percentile": delta_or_none(routed.positive_mean_rank_percentile, comparator.positive_mean_rank_percentile),
            "delta_harm_at_2": float(routed.harm_at_2) - float(comparator.harm_at_2),
            "delta_fallback_user": float(routed.fallback_user) - float(comparator.fallback_user),
            "delta_applicable_user": float(routed.applicable_user) - float(comparator.applicable_user),
        })
    write_parquet_atomic(paths["paired_deltas"], paired_rows, PAIRED_SCHEMA)
    paired = pd.DataFrame(paired_rows)
    bootstrap = bootstrap_paired(
        paired["delta_ndcg_at_10"].to_numpy(dtype=np.float64),
        paired["delta_harm_at_2"].to_numpy(dtype=np.float64),
        iterations=int(contract["bootstrap"]["iterations"]),
        seed=int(contract["bootstrap"]["seed"]),
    )
    decision = decide(bootstrap)
    counts = {str(key): int(value) for key, value in paired["applicability_stratum"].value_counts().sort_index().items()}
    strata: dict[str, Any] = {
        "schema_version": 1,
        "strict_users": len(strict_users),
        "counts": counts,
        "benefit_harm_user_counts": benefit_harm_counts(paired),
        "by_stratum": {},
        "user_overlap": observed,
        "historical_source_row_overlap": 0,
        "independence_unit": "SOURCE_ROW_AND_TEMPORAL_WINDOW",
        "user_independent": False,
        "locked_test_used": False,
        "champion": None,
        "product_policy_updated": False,
    }
    for stratum, group in paired.groupby("applicability_stratum", sort=True):
        selected = metrics[metrics["applicability_stratum"] == stratum]
        strata["by_stratum"][str(stratum)] = {
            "users": int(len(group)),
            "benefit_harm_user_counts": benefit_harm_counts(group),
            "mean_deltas": {
                column: float(group[column].mean())
                for column in (
                    "delta_ndcg_at_10", "delta_recall_at_10", "delta_mrr_at_10",
                    "delta_candidate_recall_at_500", "delta_positive_mean_rank_percentile",
                    "delta_harm_at_2", "delta_fallback_user", "delta_applicable_user",
                )
            },
            "aggregate": {
                variant: aggregate(selected[selected["variant"] == variant])
                for variant in ("COMPARATOR", "CANDIDATE")
            },
        }
    atomic_write_json(paths["strata"], strata)
    aggregate_metrics = {
        variant: aggregate(metrics[metrics["variant"] == variant])
        for variant in ("COMPARATOR", "CANDIDATE")
    }
    non_gate_degradation = {
        "candidate_recall_at_500_degraded": float(paired["delta_candidate_recall_at_500"].mean()) < 0.0,
        "positive_mean_rank_percentile_degraded": float(paired["delta_positive_mean_rank_percentile"].mean()) > 0.0,
        "mean_delta_candidate_recall_at_500": float(paired["delta_candidate_recall_at_500"].mean()),
        "mean_delta_positive_mean_rank_percentile": float(paired["delta_positive_mean_rank_percentile"].mean()),
        "is_gate": False,
    }
    result = {
        "schema_version": 1,
        "evidence_id": "REC-EV-019F",
        "contract_id": contract["contract_id"],
        "status": decision["status"],
        "reason": decision["reason"],
        "evidence_classification": contract["evidence_classification"],
        "execution_role": "VALIDATION_019F_TEMPORAL",
        "contract_sha256": lock["contract_sha256"],
        "protocol_lock_sha256": sha256_file(paths["protocol_lock"]),
        "lock_preceded_019f_ranking_metrics": True,
        "ranking_metrics_read_at_lock": False,
        "eligibility_counts_observed_before_lock": True,
        "independence_unit": "SOURCE_ROW_AND_TEMPORAL_WINDOW",
        "user_independent": False,
        "rec_ev_019d_predictions_reused": False,
        "cohort": observed,
        "routing_counts": counts,
        "aggregate_metrics": aggregate_metrics,
        "paired_strict": {
            "users": len(paired),
            "bootstrap": bootstrap,
            "benefit_harm_user_counts": strata["benefit_harm_user_counts"],
        },
        "stratified_audit": strata["by_stratum"],
        "non_gate_degradation": non_gate_degradation,
        "decision_rule_priority": contract["decision_rule"]["priority"],
        "decision": decision,
        "maximum_success_status": "PASS_INDEPENDENT_TEMPORAL_WINDOW_REQUIRES_TARGET_DOMAIN_CONFIRMATION",
        "target_domain_confirmation_required": True,
        "interpretation_limits": [
            "Independence is source-row and temporal-window independence, not user independence.",
            "This does not establish Korean-user, Korean-movie, or recent-movie performance.",
            "No status from this experiment can select a champion or update product policy.",
        ],
        "locked_test_used": False,
        "champion": None,
        "product_policy_updated": False,
    }
    atomic_write_json(paths["result"], result)
    progress["status"] = "COMPLETE"
    progress["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    atomic_write_json(progress_path, progress)

    artifact_paths = [
        paths[name] for name in (
            "protocol_lock", "source_manifest", "structural_cohort", "strict_cohort", "prefixes", "windows",
            "arm_definitions", "rankings", "user_arm_metrics", "paired_deltas", "strata", "result", "progress",
        )
    ]
    manifest = {
        "schema_version": 1,
        "evidence_id": "REC-EV-019F",
        "status": decision["status"],
        "contract_path": repo_relative(contract_path, root=firewall.root),
        "contract_sha256": lock["contract_sha256"],
        "preregistration_path": repo_relative(prereg_path, root=firewall.root),
        "preregistration_sha256": lock["preregistration_sha256"],
        "source_checksums": {entry["name"]: entry["sha256"] for entry in verify_sources(contract, firewall)},
        "source_code": lock["source_code"],
        "execution": {
            "lock_command": "py -3 scripts/run_rec_ev_019f_independent_temporal_routing.py --phase lock --role validation-019f-temporal",
            "run_command": "py -3 scripts/run_rec_ev_019f_independent_temporal_routing.py --phase run --role validation-019f-temporal --resume",
            "verify_command": "py -3 scripts/verify_rec_ev_019f_independent_temporal_routing.py --manifest docs/recommendation/evidence/manifests/rec-ev-019f-validation.json --full-rescore-users all",
            "resume": True,
            "git_revision": lock["git"]["revision"],
            "git_dirty_at_lock": False,
            "ranking_metrics_read_at_lock": False,
            "eligibility_counts_observed_before_lock": True,
        },
        "artifacts": [artifact_entry(path, root=firewall.root) for path in artifact_paths],
        "result": result,
        "independence_unit": "SOURCE_ROW_AND_TEMPORAL_WINDOW",
        "user_independent": False,
        "locked_test_used": False,
        "champion": None,
        "product_policy_updated": False,
    }
    manifest_path = firewall.root / DEFAULT_MANIFEST.relative_to(ROOT)
    atomic_write_json(manifest_path, manifest)
    return result


def load_contract(path: Path, *, root: Path) -> dict[str, Any]:
    expected = (root / DEFAULT_CONTRACT.relative_to(ROOT)).resolve()
    if path.resolve() != expected:
        raise AuthorizationError("only the repository REC-EV-019F contract is accepted")
    contract = read_json(path)
    if contract.get("contract_id") != "REC-EV-019F-INDEPENDENT-TEMPORAL-ROUTING-V1":
        raise AuthorizationError("unexpected contract identity")
    if contract.get("invariants") != {
        "execution_role": "VALIDATION_019F_TEMPORAL",
        "independence_unit": "SOURCE_ROW_AND_TEMPORAL_WINDOW",
        "user_independent": False,
        "locked_test_used": False,
        "champion": None,
        "product_policy_updated": False,
    }:
        raise AuthorizationError("REC-EV-019F contract invariants are not fail-closed")
    return contract


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--phase", choices=("lock", "run"), required=True)
    parser.add_argument("--role", choices=("validation-019f-temporal",), required=True)
    parser.add_argument("--resume", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    contract_path = args.contract if args.contract.is_absolute() else ROOT / args.contract
    contract = load_contract(contract_path, root=ROOT)
    firewall = InputFirewall(contract, root=ROOT)
    lock = create_or_verify_lock(contract, contract_path=contract_path, firewall=firewall, resume=bool(args.resume))
    if args.phase == "lock":
        print(json.dumps({
            "status": lock["status"],
            "git": lock["git"],
            "ranking_metrics_read": lock["ranking_metrics_read"],
            "eligibility_counts_observed": lock["eligibility_counts_observed"],
        }, ensure_ascii=False, sort_keys=True))
        return 0
    result = run_validation(
        contract,
        contract_path=contract_path,
        firewall=firewall,
        lock=lock,
        resume=bool(args.resume),
    )
    print(json.dumps({
        "status": result["status"],
        "reason": result["reason"],
        "paired_strict": result["paired_strict"],
        "non_gate_degradation": result["non_gate_degradation"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
