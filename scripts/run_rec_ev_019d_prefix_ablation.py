#!/usr/bin/env python3
"""Run the preregistered REC-EV-019D Validation-only prefix ablation."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "docs/recommendation/contracts/rec-ev-019d-prefix-ablation-artifacts.json"
DEFAULT_MANIFEST = ROOT / "docs/recommendation/evidence/manifests/rec-ev-019d-validation.json"


class AuthorizationError(RuntimeError):
    """Raised before data access when the requested operation is not authorized."""


class InputFirewallError(RuntimeError):
    """Raised before a disallowed path can be opened."""


class ResumeError(RuntimeError):
    """Raised when a checkpoint cannot safely be reused."""


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


def sha256_contract(path: Path) -> str:
    """Hash the JSON contract with newline normalization for cross-platform checkout parity."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


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


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def repo_relative(path: Path, *, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise InputFirewallError("path is outside the repository") from error


class InputFirewall:
    def __init__(self, contract: Mapping[str, Any], *, root: Path = ROOT) -> None:
        self.root = root.resolve()
        self.entries = dict(contract["allowed_input_artifacts"])
        self.allowed = {str(value["path"]) for value in self.entries.values()}
        self.forbidden = set(map(str, contract["forbidden_input_artifacts"]))

    def validate(self, name: str) -> Path:
        if name not in self.entries:
            raise InputFirewallError("unknown input artifact key")
        relative = str(self.entries[name]["path"])
        absolute = (self.root / relative).resolve()
        normalized = repo_relative(absolute, root=self.root)
        if normalized in self.forbidden:
            raise InputFirewallError("forbidden input artifact class")
        if normalized not in self.allowed:
            raise InputFirewallError("unknown input artifact class")
        return absolute

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
        actual_bytes = path.stat().st_size
        if actual_bytes != int(expected["bytes"]):
            raise RuntimeError(f"source byte-size drift: {name}")
        actual_sha256 = sha256_file(path)
        if actual_sha256 != str(expected["sha256"]):
            raise RuntimeError(f"source SHA-256 drift: {name}")
        artifacts.append({
            "name": name,
            "path": repo_relative(path, root=firewall.root),
            "bytes": actual_bytes,
            "sha256": actual_sha256,
        })
    return artifacts


def preregistered_spec(contract: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "cohort": contract["cohort"],
        "confirmatory_set": contract["confirmatory_set"],
        "model": contract["model"],
        "arms": contract["arms"],
        "estimands": contract["estimands"],
        "metrics": contract["metrics"],
        "bootstrap": contract["bootstrap"],
        "decision_rule": contract["decision_rule"],
    }


def create_or_verify_lock(
    contract: Mapping[str, Any],
    *,
    contract_path: Path,
    firewall: InputFirewall,
    resume: bool,
) -> dict[str, Any]:
    output_root = firewall.root / contract["output_root"]
    lock_path = firewall.root / contract["leakage_lock"]["path"]
    source_manifest_path = output_root / contract["outputs"]["source_manifest"]
    contract_sha256 = sha256_contract(contract_path)
    sources = verify_sources(contract, firewall)
    config_path = firewall.validate("lightfm_config")
    actual_config = read_json(config_path)
    if actual_config != contract["model"]["lightfm_config"]:
        raise RuntimeError("LightFM config differs from the preregistered config")
    source_manifest = {
        "schema_version": 1,
        "evidence_id": "REC-EV-019D",
        "created_before_future_label_join": True,
        "contract_path": repo_relative(contract_path, root=firewall.root),
        "contract_sha256": contract_sha256,
        "artifacts": sources,
        "base_representation": {
            "trial_id": contract["model"]["trial_id"],
            "seed": contract["model"]["seed"],
            "config": actual_config,
            "shared_by_both_arms": True,
            "cache_reused": True,
            "resume_required": True,
            "external_artifact_uri": None,
            "commit_only_third_party_reproducible": False,
            "limitation": contract["model"]["base_representation"]["third_party_reproducibility_limit"],
        },
        "locked_test_used": False,
        "champion": None,
        "product_policy_updated": False,
    }
    expected_source_sha = sha256_bytes(canonical_json_bytes(source_manifest))
    spec_sha = sha256_bytes(canonical_json_bytes(preregistered_spec(contract)))
    source_digest = sha256_bytes(canonical_json_bytes(sources))
    if lock_path.is_file():
        if not resume:
            raise ResumeError("existing protocol lock requires --resume")
        lock = read_json(lock_path)
        checks = {
            "contract_sha256": contract_sha256,
            "source_artifacts_sha256": source_digest,
            "preregistered_spec_sha256": spec_sha,
            "future_labels_joined_at_lock": False,
        }
        for key, value in checks.items():
            if lock.get(key) != value:
                raise ResumeError(f"protocol lock mismatch: {key}")
        if not source_manifest_path.is_file() or sha256_file(source_manifest_path) != expected_source_sha:
            raise ResumeError("source manifest drift after protocol lock")
        return lock
    if resume:
        output_root.mkdir(parents=True, exist_ok=True)
    atomic_write_json(source_manifest_path, source_manifest)
    lock = {
        "schema_version": 1,
        "evidence_id": "REC-EV-019D",
        "status": "PREREGISTERED_BEFORE_FUTURE_LABEL_JOIN",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "created_at_epoch_ns": time.time_ns(),
        "contract_path": repo_relative(contract_path, root=firewall.root),
        "contract_sha256": contract_sha256,
        "source_manifest_path": repo_relative(source_manifest_path, root=firewall.root),
        "source_manifest_sha256": expected_source_sha,
        "source_artifacts_sha256": source_digest,
        "preregistered_spec_sha256": spec_sha,
        "locked_fields": contract["leakage_lock"]["locked_fields"],
        "future_labels_joined_at_lock": False,
        "locked_test_used": False,
        "champion": None,
        "product_policy_updated": False,
    }
    atomic_write_json(lock_path, lock)
    return lock


def midrank_percentiles(values: np.ndarray) -> np.ndarray:
    scores = np.asarray(values, dtype=np.float64)
    if scores.ndim != 1 or not np.isfinite(scores).all():
        raise ValueError("scores must be a finite one-dimensional array")
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
    if len(observed) != len(signs) or not len(observed) or not set(signs.tolist()) <= {-1, 1}:
        return 0.0, np.zeros(item_factors.shape[1], dtype=np.float32), True
    if not ({-1, 1} <= set(signs.tolist())):
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
            if signed_margin >= 0:
                factor = -float(label) * math.exp(-signed_margin) / (1.0 + math.exp(-signed_margin))
            else:
                factor = -float(label) / (1.0 + math.exp(signed_margin))
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
    top_candidates: int,
    top_k: int,
    fallback_user: bool,
) -> tuple[dict[str, Any], np.ndarray]:
    top_indices = deterministic_top_indices(candidate_ids, scores, top_n=top_candidates)
    ranked_ids = candidate_ids[top_indices].astype(np.int64)
    rank_by_movie = {int(movie_id): rank for rank, movie_id in enumerate(ranked_ids, start=1)}
    candidate_set = set(map(int, candidate_ids.tolist()))
    positives = [row for row in future_rows if bool(row["is_positive"]) and int(row["movie_id"]) in candidate_set]
    negatives = {int(row["movie_id"]) for row in future_rows if bool(row["is_negative"])}
    gains = {int(row["movie_id"]): float(row["midrank_utility"]) for row in positives}
    ideal = sorted(gains.values(), reverse=True)[:top_k]
    idcg = sum(gain / math.log2(rank + 1) for rank, gain in enumerate(ideal, start=1))
    dcg = sum(
        gains[movie_id] / math.log2(rank + 1)
        for movie_id, rank in rank_by_movie.items()
        if movie_id in gains and rank <= top_k
    )
    first_positive = min((rank_by_movie[movie_id] for movie_id in gains if movie_id in rank_by_movie), default=None)
    exact_ranks: list[int] = []
    movie_position = {int(movie_id): position for position, movie_id in enumerate(candidate_ids)}
    for movie_id in gains:
        position = movie_position[movie_id]
        score = float(scores[position])
        if not math.isfinite(score):
            continue
        exact_ranks.append(
            1
            + int(np.count_nonzero(scores > score))
            + int(np.count_nonzero((scores == score) & (candidate_ids < movie_id)))
        )
    finite_count = max(1, int(np.count_nonzero(np.isfinite(scores))))
    top2 = set(map(int, ranked_ids[:2]))
    return {
        "ndcg_at_10": float(dcg / idcg) if idcg else 0.0,
        "recall_at_10": float(sum(rank_by_movie.get(movie_id, top_k + 1) <= top_k for movie_id in gains) / len(gains)) if gains else 0.0,
        "mrr_at_10": float(1.0 / first_positive) if first_positive is not None and first_positive <= top_k else 0.0,
        "candidate_recall_at_500": float(any(movie_id in rank_by_movie for movie_id in gains)),
        "positive_mean_rank_percentile": float(np.mean([(rank - 1) / max(1, finite_count - 1) for rank in exact_ranks])) if exact_ranks else None,
        "harm_at_2": bool(top2.intersection(negatives)),
        "fallback_user": bool(fallback_user),
    }, top_indices


def bootstrap_paired(
    ndcg_delta: np.ndarray,
    harm_delta: np.ndarray,
    *,
    iterations: int,
    seed: int,
) -> dict[str, Any]:
    ndcg = np.asarray(ndcg_delta, dtype=np.float64)
    harm = np.asarray(harm_delta, dtype=np.float64)
    if len(ndcg) != len(harm) or not len(ndcg):
        raise ValueError("paired bootstrap needs equally sized nonempty arrays")
    rng = np.random.default_rng(int(seed))
    ndcg_means = np.empty(int(iterations), dtype=np.float64)
    harm_means = np.empty(int(iterations), dtype=np.float64)
    offset = 0
    while offset < int(iterations):
        stop = min(offset + 250, int(iterations))
        indices = rng.integers(0, len(ndcg), size=(stop - offset, len(ndcg)))
        ndcg_means[offset:stop] = ndcg[indices].mean(axis=1)
        harm_means[offset:stop] = harm[indices].mean(axis=1)
        offset = stop
    return {
        "iterations": int(iterations),
        "seed": int(seed),
        "ndcg_mean": float(ndcg.mean()),
        "ndcg_two_sided_95": [float(np.percentile(ndcg_means, 2.5)), float(np.percentile(ndcg_means, 97.5))],
        "harm_mean": float(harm.mean()),
        "harm_one_sided_95_upper": float(np.percentile(harm_means, 95.0)),
    }


def decide(bootstrap: Mapping[str, Any]) -> dict[str, str]:
    ndcg_mean = float(bootstrap["ndcg_mean"])
    ndcg_lower, ndcg_upper = map(float, bootstrap["ndcg_two_sided_95"])
    harm_upper = float(bootstrap["harm_one_sided_95_upper"])
    if harm_upper > 0.005:
        return {"status": "FAIL", "reason": "SAFETY_MARGIN_EXCEEDED"}
    if ndcg_upper < 0.0:
        return {"status": "FAIL", "reason": "EFFICACY_INTERVAL_ENTIRELY_NEGATIVE"}
    if ndcg_mean >= 0.005 and ndcg_lower > 0.0 and harm_upper <= 0.005:
        return {"status": "PASS", "reason": "ALL_PREDECLARED_EFFICACY_AND_SAFETY_CRITERIA_MET"}
    return {
        "status": "INCONCLUSIVE",
        "reason": "PREDECLARED_SUCCESS_NOT_ESTABLISHED_WITHOUT_A_DECLARED_FAIL_CONDITION",
    }


COHORT_SCHEMA = pa.schema([
    ("user_key", pa.string()),
    ("confirmatory", pa.bool_()),
    ("tuning_panel_excluded", pa.bool_()),
    ("applicability_stratum", pa.string()),
    ("raw_both_k5", pa.bool_()),
    ("raw_both_k10", pa.bool_()),
    ("candidate_applicable_k5", pa.bool_()),
    ("candidate_applicable_k10", pa.bool_()),
    ("anchor_loss_k5", pa.bool_()),
    ("anchor_loss_k10", pa.bool_()),
])

ARM_SCHEMA = pa.schema([
    ("user_key", pa.string()),
    ("arm", pa.string()),
    ("input_count", pa.int8()),
    ("raw_positive_count", pa.int8()),
    ("raw_negative_count", pa.int8()),
    ("candidate_valid_count", pa.int8()),
    ("candidate_positive_count", pa.int8()),
    ("candidate_negative_count", pa.int8()),
    ("fallback_user", pa.bool_()),
    ("profile_movie_ids", pa.list_(pa.int32())),
    ("profile_labels", pa.list_(pa.int8())),
    ("candidate_valid_movie_ids", pa.list_(pa.int32())),
])

PREDICTION_SCHEMA = pa.schema([
    ("user_key", pa.string()),
    ("confirmatory", pa.bool_()),
    ("estimand", pa.string()),
    ("arm", pa.string()),
    ("rank", pa.int16()),
    ("movie_id", pa.int32()),
    ("effective_score", pa.float32()),
    ("fallback_used", pa.bool_()),
    ("fallback_reason", pa.string()),
])

METRIC_SCHEMA = pa.schema([
    ("user_key", pa.string()),
    ("confirmatory", pa.bool_()),
    ("applicability_stratum", pa.string()),
    ("estimand", pa.string()),
    ("arm", pa.string()),
    ("ndcg_at_10", pa.float64()),
    ("recall_at_10", pa.float64()),
    ("mrr_at_10", pa.float64()),
    ("candidate_recall_at_500", pa.float64()),
    ("positive_mean_rank_percentile", pa.float64()),
    ("harm_at_2", pa.bool_()),
    ("fallback_user", pa.bool_()),
])

PAIRED_SCHEMA = pa.schema([
    ("user_key", pa.string()),
    ("confirmatory", pa.bool_()),
    ("applicability_stratum", pa.string()),
    ("estimand", pa.string()),
    ("delta_ndcg_at_10", pa.float64()),
    ("delta_recall_at_10", pa.float64()),
    ("delta_mrr_at_10", pa.float64()),
    ("delta_candidate_recall_at_500", pa.float64()),
    ("delta_harm_at_2", pa.float64()),
    ("fallback_transition", pa.string()),
])


def write_parquet_atomic(path: Path, rows: Sequence[Mapping[str, Any]], schema: pa.Schema) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    table = pa.Table.from_pylist(list(rows), schema=schema)
    pq.write_table(table, temporary, compression="zstd", use_dictionary=True)
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


def prepare_contexts(
    contract: Mapping[str, Any],
    firewall: InputFirewall,
) -> tuple[
    np.ndarray,
    np.ndarray,
    np.ndarray,
    dict[str, dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    np.ndarray,
]:
    candidate_path = firewall.validate("candidate_core")
    candidate = pq.read_table(candidate_path, columns=["movie_id", "b0_score"]).to_pandas()
    if len(candidate) != int(contract["model"]["candidate_count"]):
        raise RuntimeError("candidate count drift")
    candidate_ids = candidate["movie_id"].to_numpy(dtype=np.int64)
    if len(set(map(int, candidate_ids))) != len(candidate_ids) or np.any(candidate_ids[1:] <= candidate_ids[:-1]):
        raise RuntimeError("candidate IDs are not unique ascending values")
    b0_percentiles = midrank_percentiles(candidate["b0_score"].to_numpy(dtype=np.float64))
    movie_position = {int(movie_id): position for position, movie_id in enumerate(candidate_ids)}

    prefix_path = firewall.validate("validation_prefixes")
    prefixes = pq.read_table(prefix_path, columns=[
        "role", "user_key", "k", "input_rank", "movie_id", "binary_label", "source_position", "timestamp",
    ]).to_pandas()
    windows_path = firewall.validate("validation_windows")
    windows = pq.read_table(windows_path, columns=[
        "role", "user_key", "k", "window_rank", "movie_id", "midrank_utility", "is_positive", "is_negative",
    ]).to_pandas()
    if set(prefixes["role"].astype(str)) != {"VALIDATION"} or set(windows["role"].astype(str)) != {"VALIDATION"}:
        raise RuntimeError("Validation allowlist contains another role")
    prefixes = prefixes[prefixes["k"] == 10].copy()
    windows = windows[windows["k"] == 10].copy()
    prefix_users = set(prefixes["user_key"].astype(str))
    window_users = set(windows["user_key"].astype(str))
    expected_users = int(contract["cohort"]["expected_users"])
    if prefix_users != window_users or len(prefix_users) != expected_users:
        raise RuntimeError("K10 cohort drift")

    selection_path = firewall.validate("validation_selection")
    selection = read_json(selection_path)
    tuning_union = set(map(str, selection["tuning_panel"]["5"])) | set(map(str, selection["tuning_panel"]["10"]))
    excluded = prefix_users.intersection(tuning_union)
    if len(excluded) != int(contract["confirmatory_set"]["expected_excluded_users"]):
        raise RuntimeError("confirmatory tuning-panel exclusion drift")

    prefix_groups = {str(key): group.sort_values("input_rank", kind="stable") for key, group in prefixes.groupby("user_key", sort=True)}
    window_groups = {str(key): group.sort_values("window_rank", kind="stable") for key, group in windows.groupby("user_key", sort=True)}
    contexts: dict[str, dict[str, Any]] = {}
    cohort_rows: list[dict[str, Any]] = []
    arm_rows: list[dict[str, Any]] = []
    for user_key in sorted(prefix_users):
        prefix = prefix_groups[user_key]
        future = window_groups[user_key]
        if prefix["input_rank"].astype(int).tolist() != list(range(1, 11)):
            raise RuntimeError(f"input rank drift for {user_key}")
        source_positions = prefix["source_position"].to_numpy(dtype=np.int64)
        timestamps = prefix["timestamp"].to_numpy(dtype=np.int64)
        if np.any(source_positions[1:] <= source_positions[:-1]):
            raise RuntimeError(f"source order drift for {user_key}")
        if np.any(timestamps[1:] < timestamps[:-1]):
            raise RuntimeError(f"timestamp order drift for {user_key}")
        if future["window_rank"].astype(int).tolist() != list(range(1, 11)):
            raise RuntimeError(f"future window rank drift for {user_key}")
        movie_ids = prefix["movie_id"].to_numpy(dtype=np.int64)
        labels = prefix["binary_label"].to_numpy(dtype=np.int8)
        if not set(map(int, labels)) <= {-1, 1}:
            raise RuntimeError("binary label domain drift")
        arm_data: dict[str, dict[str, Any]] = {}
        for arm, count in (("K5", 5), ("K10", 10)):
            arm_movies = movie_ids[:count]
            arm_labels = labels[:count]
            kept = [(movie_position[int(movie_id)], int(movie_id), int(label)) for movie_id, label in zip(arm_movies, arm_labels, strict=True) if int(movie_id) in movie_position]
            positions = np.asarray([row[0] for row in kept], dtype=np.int32)
            valid_movies = [row[1] for row in kept]
            valid_labels = np.asarray([row[2] for row in kept], dtype=np.int8)
            raw_both = {-1, 1} <= set(map(int, arm_labels))
            applicable = {-1, 1} <= set(map(int, valid_labels))
            arm_data[arm] = {
                "positions": positions,
                "labels": valid_labels,
                "movie_ids": list(map(int, arm_movies)),
                "raw_labels": list(map(int, arm_labels)),
                "valid_movie_ids": valid_movies,
                "raw_both": raw_both,
                "applicable": applicable,
                "anchor_loss": bool(raw_both and not applicable),
            }
            arm_rows.append({
                "user_key": user_key,
                "arm": arm,
                "input_count": count,
                "raw_positive_count": int(np.count_nonzero(arm_labels == 1)),
                "raw_negative_count": int(np.count_nonzero(arm_labels == -1)),
                "candidate_valid_count": len(kept),
                "candidate_positive_count": int(np.count_nonzero(valid_labels == 1)),
                "candidate_negative_count": int(np.count_nonzero(valid_labels == -1)),
                "fallback_user": not applicable,
                "profile_movie_ids": list(map(int, arm_movies)),
                "profile_labels": list(map(int, arm_labels)),
                "candidate_valid_movie_ids": valid_movies,
            })
        if arm_data["K5"]["movie_ids"] != arm_data["K10"]["movie_ids"][:5] or arm_data["K5"]["raw_labels"] != arm_data["K10"]["raw_labels"][:5]:
            raise RuntimeError("first5 is not an exact first10 prefix")
        applicable5 = bool(arm_data["K5"]["applicable"])
        applicable10 = bool(arm_data["K10"]["applicable"])
        if applicable5 and not applicable10:
            raise RuntimeError("K10 applicability cannot be lost after an applicable K5 prefix")
        stratum = "BOTH_LIGHTFM" if applicable5 else ("K10_NEWLY_APPLICABLE" if applicable10 else "BOTH_FALLBACK")
        confirmatory = user_key not in excluded
        cohort_rows.append({
            "user_key": user_key,
            "confirmatory": confirmatory,
            "tuning_panel_excluded": not confirmatory,
            "applicability_stratum": stratum,
            "raw_both_k5": bool(arm_data["K5"]["raw_both"]),
            "raw_both_k10": bool(arm_data["K10"]["raw_both"]),
            "candidate_applicable_k5": applicable5,
            "candidate_applicable_k10": applicable10,
            "anchor_loss_k5": bool(arm_data["K5"]["anchor_loss"]),
            "anchor_loss_k10": bool(arm_data["K10"]["anchor_loss"]),
        })
        contexts[user_key] = {
            "confirmatory": confirmatory,
            "stratum": stratum,
            "arms": arm_data,
            "future": [
                {
                    "movie_id": int(row.movie_id),
                    "midrank_utility": float(row.midrank_utility),
                    "is_positive": bool(row.is_positive),
                    "is_negative": bool(row.is_negative),
                }
                for row in future.itertuples(index=False)
            ],
        }

    config_path = firewall.validate("lightfm_config")
    if read_json(config_path) != contract["model"]["lightfm_config"]:
        raise RuntimeError("LightFM config drift after lock")
    result_path = firewall.validate("lightfm_result")
    with np.load(result_path, allow_pickle=False) as fitted:
        item_biases = fitted["item_biases"].astype(np.float32)
        item_factors = fitted["item_factors"].astype(np.float32)
    dimension = int(contract["model"]["lightfm_config"]["dimension"])
    if item_biases.shape != (len(candidate_ids),) or item_factors.shape != (len(candidate_ids), dimension):
        raise RuntimeError("LightFM item representation shape drift")
    if not np.isfinite(item_biases).all() or not np.isfinite(item_factors).all():
        raise RuntimeError("LightFM item representation contains non-finite values")
    return candidate_ids, b0_percentiles, item_biases, contexts, cohort_rows, arm_rows, item_factors


def checkpoint_signature(contract_sha256: str, lock: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes({
        "contract_sha256": contract_sha256,
        "source_artifacts_sha256": lock["source_artifacts_sha256"],
        "preregistered_spec_sha256": lock["preregistered_spec_sha256"],
    }))


def artifact_entry(path: Path, *, root: Path) -> dict[str, Any]:
    return {
        "path": repo_relative(path, root=root),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def aggregate_metrics(frame: pd.DataFrame) -> dict[str, float]:
    return {
        "users": int(frame["user_key"].nunique()),
        "ndcg_at_10": float(frame["ndcg_at_10"].mean()),
        "recall_at_10": float(frame["recall_at_10"].mean()),
        "mrr_at_10": float(frame["mrr_at_10"].mean()),
        "candidate_recall_at_500": float(frame["candidate_recall_at_500"].mean()),
        "harm_at_2": float(frame["harm_at_2"].astype(float).mean()),
        "fallback_user_rate": float(frame["fallback_user"].astype(float).mean()),
    }


def run_validation(
    contract: Mapping[str, Any],
    *,
    contract_path: Path,
    firewall: InputFirewall,
    lock: Mapping[str, Any],
    resume: bool,
) -> dict[str, Any]:
    if not contract["current_authorization"].get("real_validation_fit_or_score", False):
        raise AuthorizationError("real Validation is not authorized")
    if not resume:
        raise ResumeError("REC-EV-019D real Validation requires --resume")
    run_started_ns = time.time_ns()
    if int(lock["created_at_epoch_ns"]) >= run_started_ns or bool(lock["future_labels_joined_at_lock"]):
        raise RuntimeError("protocol lock did not precede future-label access")
    contract_sha256 = sha256_contract(contract_path)
    if contract_sha256 != lock["contract_sha256"]:
        raise RuntimeError("contract changed after protocol lock")
    verify_sources(contract, firewall)

    output_root = firewall.root / contract["output_root"]
    checkpoints = output_root / contract["outputs"]["checkpoints"]
    checkpoints.mkdir(parents=True, exist_ok=True)
    signature = checkpoint_signature(contract_sha256, lock)
    progress_path = output_root / contract["outputs"]["progress"]
    if progress_path.is_file():
        progress = read_json(progress_path)
        if progress.get("resume_signature") != signature:
            raise ResumeError("run progress belongs to another contract or source set")
    else:
        progress = {
            "schema_version": 1,
            "resume_signature": signature,
            "run_started_at_utc": datetime.now(timezone.utc).isoformat(),
            "run_started_epoch_ns": run_started_ns,
            "protocol_lock_created_at_epoch_ns": int(lock["created_at_epoch_ns"]),
            "completed_batches": [],
            "locked_test_used": False,
            "champion": None,
            "product_policy_updated": False,
        }
        atomic_write_json(progress_path, progress)

    candidate_ids, b0_percentiles, item_biases, contexts, cohort_rows, arm_rows, item_factors = prepare_contexts(contract, firewall)
    cohort_path = output_root / contract["outputs"]["cohort"]
    arms_path = output_root / contract["outputs"]["arm_definitions"]
    write_parquet_atomic(cohort_path, cohort_rows, COHORT_SCHEMA)
    write_parquet_atomic(arms_path, arm_rows, ARM_SCHEMA)

    users = sorted(contexts)
    batch_size = min(32, int(contract["resource_bounds"]["user_batch_size_max"]))
    prediction_parts: list[Path] = []
    metric_parts: list[Path] = []
    completed = set(map(int, progress.get("completed_batches", [])))
    fixed = contract["model"]["lightfm_config"]
    top_candidates = int(contract["metrics"]["top_candidates"])
    top_k = int(contract["metrics"]["top_k"])
    for batch_index, start in enumerate(range(0, len(users), batch_size)):
        batch_users = users[start : start + batch_size]
        prediction_part = checkpoints / f"batch-{batch_index:05d}-predictions.parquet"
        metric_part = checkpoints / f"batch-{batch_index:05d}-metrics.parquet"
        done_path = checkpoints / f"batch-{batch_index:05d}-done.json"
        prediction_parts.append(prediction_part)
        metric_parts.append(metric_part)
        if batch_index in completed:
            if not prediction_part.is_file() or not metric_part.is_file() or not done_path.is_file():
                raise ResumeError("completed checkpoint is incomplete")
            done = read_json(done_path)
            if done.get("resume_signature") != signature:
                raise ResumeError("checkpoint signature mismatch")
            if sha256_file(prediction_part) != done["prediction_sha256"] or sha256_file(metric_part) != done["metric_sha256"]:
                raise ResumeError("checkpoint artifact hash mismatch")
            continue

        prediction_rows: list[dict[str, Any]] = []
        metric_rows: list[dict[str, Any]] = []
        for arm in ("K5", "K10"):
            biases: list[float] = []
            vectors: list[np.ndarray] = []
            fallback_flags: list[bool] = []
            for user_key in batch_users:
                profile = contexts[user_key]["arms"][arm]
                bias, vector, fallback = fold_in_profile(
                    item_biases,
                    item_factors,
                    profile["positions"],
                    profile["labels"],
                    regularization=float(fixed["user_alpha"]),
                    learning_rate=float(fixed["learning_rate"]),
                    epochs=int(contract["model"]["target_fold_in"]["epochs"]),
                )
                biases.append(float(bias))
                vectors.append(vector)
                fallback_flags.append(bool(fallback))
            score_matrix = np.vstack(vectors).astype(np.float32) @ item_factors.T
            score_matrix += item_biases[None, :]
            score_matrix += np.asarray(biases, dtype=np.float32)[:, None]
            for row_index, user_key in enumerate(batch_users):
                context = contexts[user_key]
                fallback = fallback_flags[row_index]
                effective = b0_percentiles.copy() if fallback else midrank_percentiles(score_matrix[row_index])
                for estimand in ("COMMON_K10_SEEN_MASK", "ARM_SPECIFIC_SEEN_MASK"):
                    seen_positions = (
                        context["arms"]["K10"]["positions"]
                        if estimand == "COMMON_K10_SEEN_MASK"
                        else context["arms"][arm]["positions"]
                    )
                    masked = effective.copy()
                    masked[seen_positions] = -np.inf
                    metrics, top_indices = ranking_metrics(
                        candidate_ids,
                        masked,
                        context["future"],
                        top_candidates=top_candidates,
                        top_k=top_k,
                        fallback_user=fallback,
                    )
                    metric_rows.append({
                        "user_key": user_key,
                        "confirmatory": bool(context["confirmatory"]),
                        "applicability_stratum": context["stratum"],
                        "estimand": estimand,
                        "arm": arm,
                        **metrics,
                    })
                    reason = "PREFIX_LACKS_EITHER_CANDIDATE_VALID_BINARY_CLASS" if fallback else None
                    prediction_rows.extend({
                        "user_key": user_key,
                        "confirmatory": bool(context["confirmatory"]),
                        "estimand": estimand,
                        "arm": arm,
                        "rank": rank,
                        "movie_id": int(candidate_ids[position]),
                        "effective_score": float(masked[position]),
                        "fallback_used": fallback,
                        "fallback_reason": reason,
                    } for rank, position in enumerate(top_indices, start=1))
        prediction_rows.sort(key=lambda row: (row["estimand"], row["arm"], row["user_key"], row["rank"]))
        metric_rows.sort(key=lambda row: (row["estimand"], row["arm"], row["user_key"]))
        write_parquet_atomic(prediction_part, prediction_rows, PREDICTION_SCHEMA)
        write_parquet_atomic(metric_part, metric_rows, METRIC_SCHEMA)
        atomic_write_json(done_path, {
            "resume_signature": signature,
            "batch_index": batch_index,
            "users": batch_users,
            "prediction_sha256": sha256_file(prediction_part),
            "metric_sha256": sha256_file(metric_part),
        })
        completed.add(batch_index)
        progress["completed_batches"] = sorted(completed)
        progress["last_completed_batch"] = batch_index
        atomic_write_json(progress_path, progress)

    predictions_path = output_root / contract["outputs"]["predictions"]
    metrics_path = output_root / contract["outputs"]["user_arm_metrics"]
    combine_parquet_parts(prediction_parts, predictions_path, PREDICTION_SCHEMA)
    combine_parquet_parts(metric_parts, metrics_path, METRIC_SCHEMA)
    metrics = pq.read_table(metrics_path).to_pandas()

    paired_rows: list[dict[str, Any]] = []
    for (user_key, estimand), group in metrics.groupby(["user_key", "estimand"], sort=True):
        by_arm = {str(row.arm): row for row in group.itertuples(index=False)}
        if set(by_arm) != {"K5", "K10"}:
            raise RuntimeError("paired metric arm drift")
        k5, k10 = by_arm["K5"], by_arm["K10"]
        transition = (
            "BOTH_LIGHTFM" if not k5.fallback_user and not k10.fallback_user
            else "K10_NEWLY_APPLICABLE" if k5.fallback_user and not k10.fallback_user
            else "BOTH_FALLBACK" if k5.fallback_user and k10.fallback_user
            else "INVALID_K10_FALLBACK_TRANSITION"
        )
        if transition == "INVALID_K10_FALLBACK_TRANSITION":
            raise RuntimeError(transition)
        paired_rows.append({
            "user_key": str(user_key),
            "confirmatory": bool(k5.confirmatory),
            "applicability_stratum": str(k5.applicability_stratum),
            "estimand": str(estimand),
            "delta_ndcg_at_10": float(k10.ndcg_at_10 - k5.ndcg_at_10),
            "delta_recall_at_10": float(k10.recall_at_10 - k5.recall_at_10),
            "delta_mrr_at_10": float(k10.mrr_at_10 - k5.mrr_at_10),
            "delta_candidate_recall_at_500": float(k10.candidate_recall_at_500 - k5.candidate_recall_at_500),
            "delta_harm_at_2": float(k10.harm_at_2) - float(k5.harm_at_2),
            "fallback_transition": transition,
        })
    paired_path = output_root / contract["outputs"]["paired_deltas"]
    write_parquet_atomic(paired_path, paired_rows, PAIRED_SCHEMA)
    paired = pd.DataFrame(paired_rows)
    cohort = pd.DataFrame(cohort_rows)
    confirmatory = cohort[cohort["confirmatory"]]
    strata_counts = {str(key): int(value) for key, value in confirmatory["applicability_stratum"].value_counts().sort_index().items()}
    strata = {
        "schema_version": 1,
        "confirmatory_users": int(len(confirmatory)),
        "mutually_exclusive_counts": strata_counts,
        "raw_both_but_candidate_anchor_loss": {
            "K5": int(confirmatory["anchor_loss_k5"].sum()),
            "K10": int(confirmatory["anchor_loss_k10"].sum()),
        },
        "expected_for_drift_detection_only": contract["applicability_strata"]["expected_confirmatory_counts_for_drift_detection_only"],
    }
    expected = contract["applicability_strata"]["expected_confirmatory_counts_for_drift_detection_only"]
    strata["matches_expected"] = bool(
        strata_counts == {key: int(expected[key]) for key in ("BOTH_FALLBACK", "BOTH_LIGHTFM", "K10_NEWLY_APPLICABLE")}
        and strata["raw_both_but_candidate_anchor_loss"]["K5"] == int(expected["RAW_BOTH_BUT_CANDIDATE_ANCHOR_LOSS_K5"])
        and strata["raw_both_but_candidate_anchor_loss"]["K10"] == int(expected["RAW_BOTH_BUT_CANDIDATE_ANCHOR_LOSS_K10"])
    )
    strata_path = output_root / contract["outputs"]["strata"]
    atomic_write_json(strata_path, strata)

    primary_paired = paired[(paired["confirmatory"]) & (paired["estimand"] == "COMMON_K10_SEEN_MASK")]
    bootstrap = bootstrap_paired(
        primary_paired["delta_ndcg_at_10"].to_numpy(dtype=np.float64),
        primary_paired["delta_harm_at_2"].to_numpy(dtype=np.float64),
        iterations=int(contract["bootstrap"]["iterations"]),
        seed=int(contract["bootstrap"]["seed"]),
    )
    decision = decide(bootstrap)
    aggregate: dict[str, Any] = {}
    for estimand in ("COMMON_K10_SEEN_MASK", "ARM_SPECIFIC_SEEN_MASK"):
        aggregate[estimand] = {}
        for population, selected in (
            ("CONFIRMATORY", metrics[(metrics["confirmatory"]) & (metrics["estimand"] == estimand)]),
            ("ALL_K10_COHORT", metrics[metrics["estimand"] == estimand]),
        ):
            aggregate[estimand][population] = {
                arm: aggregate_metrics(selected[selected["arm"] == arm]) for arm in ("K5", "K10")
            }
    result = {
        "schema_version": 1,
        "evidence_id": "REC-EV-019D",
        "status": decision["status"],
        "reason": decision["reason"],
        "execution_role": "VALIDATION_019D",
        "contract_sha256": contract_sha256,
        "protocol_lock_sha256": sha256_file(firewall.root / contract["leakage_lock"]["path"]),
        "lock_preceded_future_label_join": True,
        "cohort": {
            "k10_users": len(cohort_rows),
            "tuning_panel_excluded": int((~cohort["confirmatory"]).sum()),
            "confirmatory_users": int(cohort["confirmatory"].sum()),
        },
        "strata": strata,
        "primary_estimand": {
            "id": "COMMON_K10_SEEN_MASK_PROFILE_ABLATION",
            "population": "CONFIRMATORY",
            "paired_users": len(primary_paired),
            "bootstrap": bootstrap,
            "decision": decision,
        },
        "aggregate_metrics": aggregate,
        "secondary_estimand_is_decision_gate": False,
        "base_cache": {
            "reused": True,
            "shared_by_both_arms": True,
            "path": contract["allowed_input_artifacts"]["lightfm_result"]["path"],
            "sha256": contract["allowed_input_artifacts"]["lightfm_result"]["sha256"],
            "commit_only_third_party_reproducible": False,
        },
        "interpretation_limits": [
            "Primary is a controlled profile-information ablation that uses K10 information in both seen masks.",
            "Secondary changes both profile information and the seen set and is diagnostic only.",
            "This does not establish Korean-user, Korean-movie, recent-movie, or product-policy performance.",
        ],
        "locked_test_used": False,
        "champion": None,
        "product_policy_updated": False,
    }
    result_path = output_root / contract["outputs"]["result"]
    atomic_write_json(result_path, result)
    progress["status"] = "COMPLETE"
    progress["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    progress["locked_test_used"] = False
    progress["champion"] = None
    progress["product_policy_updated"] = False
    atomic_write_json(progress_path, progress)

    if sha256_contract(contract_path) != contract_sha256:
        raise RuntimeError("contract changed during execution")
    artifact_paths = [
        firewall.root / contract["leakage_lock"]["path"],
        output_root / contract["outputs"]["source_manifest"],
        cohort_path,
        arms_path,
        predictions_path,
        metrics_path,
        paired_path,
        strata_path,
        result_path,
        progress_path,
    ]
    manifest = {
        "schema_version": 1,
        "evidence_id": "REC-EV-019D",
        "status": decision["status"],
        "contract_path": repo_relative(contract_path, root=firewall.root),
        "contract_sha256": contract_sha256,
        "source_checksums": {entry["name"]: entry["sha256"] for entry in verify_sources(contract, firewall)},
        "execution": {
            "lock_command": "py -3 scripts/run_rec_ev_019d_prefix_ablation.py --phase lock --role validation-019d",
            "run_command": "py -3 scripts/run_rec_ev_019d_prefix_ablation.py --phase run --role validation-019d --resume",
            "verify_command": "py -3 scripts/verify_rec_ev_019d_prefix_ablation.py --manifest docs/recommendation/evidence/manifests/rec-ev-019d-validation.json",
            "resume": True,
            "lightfm_config": contract["model"]["lightfm_config"],
        },
        "artifacts": [artifact_entry(path, root=firewall.root) for path in artifact_paths],
        "result": result,
        "locked_test_used": False,
        "champion": None,
        "product_policy_updated": False,
    }
    atomic_write_json(DEFAULT_MANIFEST if firewall.root == ROOT else firewall.root / DEFAULT_MANIFEST.relative_to(ROOT), manifest)
    return result


def load_contract(path: Path, *, root: Path) -> dict[str, Any]:
    expected = (root / DEFAULT_CONTRACT.relative_to(ROOT)).resolve()
    if path.resolve() != expected:
        raise AuthorizationError("only the repository REC-EV-019D contract is accepted")
    contract = read_json(path)
    if contract.get("contract_id") != "rec-ev-019d-prefix-ablation-artifacts-v1":
        raise AuthorizationError("unexpected contract identity")
    invariants = contract.get("invariants", {})
    if invariants != {
        "execution_role": "VALIDATION_019D",
        "locked_test_used": False,
        "champion": None,
        "product_policy_updated": False,
    }:
        raise AuthorizationError("contract invariants are not fail-closed")
    return contract


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--phase", choices=("lock", "run"), required=True)
    parser.add_argument("--role", choices=("validation-019d",), required=True)
    parser.add_argument("--resume", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    contract_path = args.contract if args.contract.is_absolute() else ROOT / args.contract
    contract = load_contract(contract_path, root=ROOT)
    firewall = InputFirewall(contract, root=ROOT)
    lock = create_or_verify_lock(
        contract,
        contract_path=contract_path,
        firewall=firewall,
        resume=bool(args.resume),
    )
    if args.phase == "lock":
        print(json.dumps({
            "status": lock["status"],
            "contract_sha256": lock["contract_sha256"],
            "created_at_utc": lock["created_at_utc"],
            "future_labels_joined_at_lock": lock["future_labels_joined_at_lock"],
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
        "primary": result["primary_estimand"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
