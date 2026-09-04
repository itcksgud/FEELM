#!/usr/bin/env python3
"""Run the preregistered REC-EV-019E post-hoc Validation routing mitigation."""

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


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "docs/recommendation/contracts/rec-ev-019e-no-retune-incremental-applicability-gate.json"
DEFAULT_PREREGISTRATION = ROOT / "docs/recommendation/evidence/REC-EV-019E-no-retune-incremental-applicability-preregistration.md"
DEFAULT_MANIFEST = ROOT / "docs/recommendation/evidence/manifests/rec-ev-019e-validation.json"
RUNNER_SOURCE = Path(__file__).resolve()
VERIFIER_SOURCE = ROOT / "scripts/verify_rec_ev_019e_no_retune_incremental_applicability.py"
VALIDATOR_SOURCE = ROOT / "scripts/validate_rec_ev_019e_contract.py"


class AuthorizationError(RuntimeError):
    """Raised before data access when the requested operation is not authorized."""


class InputFirewallError(RuntimeError):
    """Raised before an unknown or forbidden path can be opened."""


class ResumeError(RuntimeError):
    """Raised when a lock or checkpoint cannot safely be reused."""


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


def sha256_text_contract(path: Path) -> str:
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


def git_attestation(root: Path) -> dict[str, Any]:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True,
    ).stdout.strip()
    status_text = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.replace("\r\n", "\n")
    status_lines = [line for line in status_text.splitlines() if line]
    return {
        "revision": revision,
        "dirty": bool(status_lines),
        "status_porcelain": status_lines,
        "status_sha256": sha256_bytes(("\n".join(status_lines) + ("\n" if status_lines else "")).encode("utf-8")),
    }


def preregistered_spec(contract: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "evidence_classification": contract["evidence_classification"],
        "population": contract["population"],
        "comparator": contract["comparator"],
        "candidate": contract["candidate"],
        "source_ranking_reuse": contract["source_ranking_reuse"],
        "metrics": contract["metrics"],
        "bootstrap": contract["bootstrap"],
        "decision_rule": contract["decision_rule"],
    }


def source_code_attestation(*, root: Path) -> dict[str, dict[str, str]]:
    paths = {
        "runner": root / RUNNER_SOURCE.relative_to(ROOT),
        "verifier": root / VERIFIER_SOURCE.relative_to(ROOT),
        "contract_validator": root / VALIDATOR_SOURCE.relative_to(ROOT),
    }
    for name, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"required source is absent before lock: {name}")
    return {
        name: {"path": repo_relative(path, root=root), "sha256": sha256_file(path)}
        for name, path in paths.items()
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
    result_names = ("cohort", "routing", "user_arm_metrics", "paired_deltas", "strata", "result", "progress")
    sources = verify_sources(contract, firewall)
    contract_sha = sha256_text_contract(contract_path)
    prereg_path = firewall.root / DEFAULT_PREREGISTRATION.relative_to(ROOT)
    prereg_sha = sha256_text_contract(prereg_path)
    code = source_code_attestation(root=firewall.root)
    source_digest = sha256_bytes(canonical_json_bytes(sources))
    spec_sha = sha256_bytes(canonical_json_bytes(preregistered_spec(contract)))
    source_manifest = {
        "schema_version": 1,
        "evidence_id": "REC-EV-019E",
        "classification": "POST_HOC_REUSES_REC_EV_019D_CONFIRMATORY_USERS",
        "created_before_019e_hybrid_metrics": True,
        "rec_ev_019d_result_and_harm_decomposition_already_observed": True,
        "contract_path": repo_relative(contract_path, root=firewall.root),
        "contract_sha256": contract_sha,
        "preregistration_path": repo_relative(prereg_path, root=firewall.root),
        "preregistration_sha256": prereg_sha,
        "source_code": code,
        "artifacts": sources,
        "source_ranking_reuse": contract["source_ranking_reuse"],
        "locked_test_used": False,
        "champion": None,
        "product_policy_updated": False,
    }
    expected_manifest_sha = sha256_bytes(canonical_json_bytes(source_manifest))
    if lock_path.is_file():
        if not resume:
            raise ResumeError("existing protocol lock requires --resume")
        lock = read_json(lock_path)
        checks = {
            "contract_sha256": contract_sha,
            "preregistration_sha256": prereg_sha,
            "source_artifacts_sha256": source_digest,
            "preregistered_spec_sha256": spec_sha,
            "source_code": code,
            "future_metrics_read": False,
        }
        for key, expected in checks.items():
            if lock.get(key) != expected:
                raise ResumeError(f"protocol lock mismatch: {key}")
        if not source_manifest_path.is_file() or sha256_file(source_manifest_path) != expected_manifest_sha:
            raise ResumeError("source manifest drift after protocol lock")
        return lock
    existing = [output_root / contract["outputs"][name] for name in result_names]
    if any(path.exists() for path in existing):
        raise RuntimeError("019E metric/routing output exists before protocol lock")
    output_root.mkdir(parents=True, exist_ok=True)
    git = git_attestation(firewall.root)
    atomic_write_json(source_manifest_path, source_manifest)
    lock = {
        "schema_version": 1,
        "evidence_id": "REC-EV-019E",
        "status": "PREREGISTERED_POST_HOC_BEFORE_019E_HYBRID_RESULT",
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
        "future_metrics_read": False,
        "future_metrics_read_definition": contract["leakage_lock"]["future_metrics_read_definition"],
        "rec_ev_019d_result_and_harm_decomposition_already_observed": True,
        "locked_test_used": False,
        "champion": None,
        "product_policy_updated": False,
    }
    atomic_write_json(lock_path, lock)
    return lock


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
    ndcg_lower = float(bootstrap["ndcg_two_sided_95"][0])
    if float(bootstrap["harm_one_sided_95_upper"]) > 0.005:
        return {"status": "FAIL_SAFETY_MARGIN_EXCEEDED", "reason": "HARM_UPPER_EXCEEDS_0_005"}
    if float(bootstrap["ndcg_mean"]) >= 0.005 and ndcg_lower > 0.0:
        return {
            "status": "PASS_POST_HOC_VALIDATION_REQUIRES_FRESH_CONFIRMATION",
            "reason": "POST_HOC_EFFICACY_AND_SAFETY_THRESHOLDS_MET",
        }
    return {
        "status": "INCONCLUSIVE_POST_HOC_VALIDATION",
        "reason": "POST_HOC_SUCCESS_NOT_ESTABLISHED",
    }


def route_for_stratum(stratum: str) -> tuple[str, str]:
    routes = {
        "BOTH_LIGHTFM": ("K5", "K5_FOLD_IN"),
        "K10_NEWLY_APPLICABLE": ("K10", "K10_FOLD_IN"),
        "BOTH_FALLBACK": ("K5", "B0"),
    }
    if stratum not in routes:
        raise RuntimeError(f"unknown applicability stratum: {stratum}")
    return routes[stratum]


COHORT_SCHEMA = pa.schema([
    ("user_key", pa.string()),
    ("confirmatory", pa.bool_()),
    ("tuning_panel_excluded", pa.bool_()),
    ("applicability_stratum", pa.string()),
    ("candidate_source_arm", pa.string()),
    ("candidate_model", pa.string()),
])

ROUTING_SCHEMA = pa.schema([
    ("user_key", pa.string()),
    ("confirmatory", pa.bool_()),
    ("applicability_stratum", pa.string()),
    ("comparator_source_arm", pa.string()),
    ("candidate_source_arm", pa.string()),
    ("candidate_model", pa.string()),
    ("seen_mask", pa.string()),
    ("parameter_count", pa.int8()),
])

METRIC_SCHEMA = pa.schema([
    ("user_key", pa.string()),
    ("confirmatory", pa.bool_()),
    ("applicability_stratum", pa.string()),
    ("variant", pa.string()),
    ("source_arm", pa.string()),
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
    ("confirmatory", pa.bool_()),
    ("applicability_stratum", pa.string()),
    ("candidate_source_arm", pa.string()),
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


def checkpoint_signature(lock: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes({
        "contract_sha256": lock["contract_sha256"],
        "preregistration_sha256": lock["preregistration_sha256"],
        "source_artifacts_sha256": lock["source_artifacts_sha256"],
        "source_code": lock["source_code"],
    }))


def metric_payload(row: Any) -> dict[str, Any]:
    return {
        "ndcg_at_10": float(row.ndcg_at_10),
        "recall_at_10": float(row.recall_at_10),
        "mrr_at_10": float(row.mrr_at_10),
        "candidate_recall_at_500": float(row.candidate_recall_at_500),
        "positive_mean_rank_percentile": None if pd.isna(row.positive_mean_rank_percentile) else float(row.positive_mean_rank_percentile),
        "harm_at_2": bool(row.harm_at_2),
        "fallback_user": bool(row.fallback_user),
        "applicable_user": not bool(row.fallback_user),
    }


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


def run_validation(
    contract: Mapping[str, Any],
    *,
    contract_path: Path,
    firewall: InputFirewall,
    lock: Mapping[str, Any],
    resume: bool,
) -> dict[str, Any]:
    if not contract["current_authorization"].get("validation_routing_and_metric_recomputation", False):
        raise AuthorizationError("019E Validation routing is not authorized")
    if not resume:
        raise ResumeError("REC-EV-019E real Validation requires --resume")
    run_started_ns = time.time_ns()
    if int(lock["created_at_epoch_ns"]) >= run_started_ns or bool(lock["future_metrics_read"]):
        raise RuntimeError("protocol lock did not precede 019E hybrid result")
    if sha256_text_contract(contract_path) != lock["contract_sha256"]:
        raise RuntimeError("contract changed after protocol lock")
    prereg_path = firewall.root / DEFAULT_PREREGISTRATION.relative_to(ROOT)
    if sha256_text_contract(prereg_path) != lock["preregistration_sha256"]:
        raise RuntimeError("preregistration changed after protocol lock")
    if source_code_attestation(root=firewall.root) != lock["source_code"]:
        raise RuntimeError("runner/verifier/validator source changed after protocol lock")
    verify_sources(contract, firewall)

    cohort_source = pq.read_table(firewall.validate("rec_ev_019d_cohort")).to_pandas()
    source_metrics = pq.read_table(firewall.validate("rec_ev_019d_user_arm_metrics")).to_pandas()
    source_metrics = source_metrics[source_metrics["estimand"] == "COMMON_K10_SEEN_MASK"].copy()
    if len(cohort_source) != 1479 or int(cohort_source["confirmatory"].sum()) != 1053:
        raise RuntimeError("019D cohort drift")
    if len(source_metrics) != 1479 * 2:
        raise RuntimeError("019D common-mask metric count drift")
    metric_lookup = {
        (str(row.user_key), str(row.arm)): row for row in source_metrics.itertuples(index=False)
    }
    if len(metric_lookup) != 1479 * 2:
        raise RuntimeError("019D metric key duplication")

    output_root = firewall.root / contract["output_root"]
    checkpoints = output_root / contract["outputs"]["checkpoints"]
    checkpoints.mkdir(parents=True, exist_ok=True)
    signature = checkpoint_signature(lock)
    progress_path = output_root / contract["outputs"]["progress"]
    if progress_path.is_file():
        progress = read_json(progress_path)
        if progress.get("resume_signature") != signature:
            raise ResumeError("run progress belongs to another lock")
    else:
        progress = {
            "schema_version": 1,
            "evidence_id": "REC-EV-019E",
            "status": "RUNNING",
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

    cohort_rows: list[dict[str, Any]] = []
    for row in cohort_source.sort_values("user_key", kind="stable").itertuples(index=False):
        source_arm, model = route_for_stratum(str(row.applicability_stratum))
        cohort_rows.append({
            "user_key": str(row.user_key),
            "confirmatory": bool(row.confirmatory),
            "tuning_panel_excluded": bool(row.tuning_panel_excluded),
            "applicability_stratum": str(row.applicability_stratum),
            "candidate_source_arm": source_arm,
            "candidate_model": model,
        })
    cohort_path = output_root / contract["outputs"]["cohort"]
    write_parquet_atomic(cohort_path, cohort_rows, COHORT_SCHEMA)

    users = [row["user_key"] for row in cohort_rows]
    cohort_lookup = {row["user_key"]: row for row in cohort_rows}
    batch_size = min(64, int(contract["resource_bounds"]["user_batch_size_max"]))
    completed = set(map(int, progress.get("completed_batches", [])))
    routing_parts: list[Path] = []
    metric_parts: list[Path] = []
    paired_parts: list[Path] = []
    for batch_index, start in enumerate(range(0, len(users), batch_size)):
        batch_users = users[start : start + batch_size]
        routing_part = checkpoints / f"batch-{batch_index:05d}-routing.parquet"
        metric_part = checkpoints / f"batch-{batch_index:05d}-metrics.parquet"
        paired_part = checkpoints / f"batch-{batch_index:05d}-paired.parquet"
        done_path = checkpoints / f"batch-{batch_index:05d}-done.json"
        routing_parts.append(routing_part)
        metric_parts.append(metric_part)
        paired_parts.append(paired_part)
        if batch_index in completed:
            if not all(path.is_file() for path in (routing_part, metric_part, paired_part, done_path)):
                raise ResumeError("completed checkpoint is incomplete")
            done = read_json(done_path)
            if done.get("resume_signature") != signature:
                raise ResumeError("checkpoint signature mismatch")
            for key, path in (("routing_sha256", routing_part), ("metric_sha256", metric_part), ("paired_sha256", paired_part)):
                if done.get(key) != sha256_file(path):
                    raise ResumeError("checkpoint artifact hash mismatch")
            continue
        routing_rows: list[dict[str, Any]] = []
        metric_rows: list[dict[str, Any]] = []
        paired_rows: list[dict[str, Any]] = []
        for user_key in batch_users:
            cohort_row = cohort_lookup[user_key]
            candidate_arm = str(cohort_row["candidate_source_arm"])
            comparator_source = metric_lookup[(user_key, "K5")]
            candidate_source = metric_lookup[(user_key, candidate_arm)]
            if cohort_row["applicability_stratum"] == "BOTH_FALLBACK":
                other = metric_lookup[(user_key, "K10")]
                for name in ("ndcg_at_10", "recall_at_10", "mrr_at_10", "candidate_recall_at_500", "positive_mean_rank_percentile"):
                    if not math.isclose(float(getattr(comparator_source, name)), float(getattr(other, name)), rel_tol=0.0, abs_tol=0.0):
                        raise RuntimeError("B0 common-mask rankings differ across source arms")
                if bool(comparator_source.harm_at_2) != bool(other.harm_at_2):
                    raise RuntimeError("B0 common-mask Harm@2 differs across source arms")
            routing_rows.append({
                "user_key": user_key,
                "confirmatory": bool(cohort_row["confirmatory"]),
                "applicability_stratum": str(cohort_row["applicability_stratum"]),
                "comparator_source_arm": "K5",
                "candidate_source_arm": candidate_arm,
                "candidate_model": str(cohort_row["candidate_model"]),
                "seen_mask": "COMMON_K10_CANDIDATE_VALID_SEEN_MASK",
                "parameter_count": 0,
            })
            comparator = metric_payload(comparator_source)
            candidate = metric_payload(candidate_source)
            for variant, source_arm, values in (
                ("COMPARATOR", "K5", comparator),
                ("CANDIDATE", candidate_arm, candidate),
            ):
                metric_rows.append({
                    "user_key": user_key,
                    "confirmatory": bool(cohort_row["confirmatory"]),
                    "applicability_stratum": str(cohort_row["applicability_stratum"]),
                    "variant": variant,
                    "source_arm": source_arm,
                    **values,
                })
            paired_rows.append({
                "user_key": user_key,
                "confirmatory": bool(cohort_row["confirmatory"]),
                "applicability_stratum": str(cohort_row["applicability_stratum"]),
                "candidate_source_arm": candidate_arm,
                "delta_ndcg_at_10": candidate["ndcg_at_10"] - comparator["ndcg_at_10"],
                "delta_recall_at_10": candidate["recall_at_10"] - comparator["recall_at_10"],
                "delta_mrr_at_10": candidate["mrr_at_10"] - comparator["mrr_at_10"],
                "delta_candidate_recall_at_500": candidate["candidate_recall_at_500"] - comparator["candidate_recall_at_500"],
                "delta_positive_mean_rank_percentile": delta_or_none(candidate["positive_mean_rank_percentile"], comparator["positive_mean_rank_percentile"]),
                "delta_harm_at_2": float(candidate["harm_at_2"]) - float(comparator["harm_at_2"]),
                "delta_fallback_user": float(candidate["fallback_user"]) - float(comparator["fallback_user"]),
                "delta_applicable_user": float(candidate["applicable_user"]) - float(comparator["applicable_user"]),
            })
        write_parquet_atomic(routing_part, routing_rows, ROUTING_SCHEMA)
        write_parquet_atomic(metric_part, metric_rows, METRIC_SCHEMA)
        write_parquet_atomic(paired_part, paired_rows, PAIRED_SCHEMA)
        atomic_write_json(done_path, {
            "resume_signature": signature,
            "batch_index": batch_index,
            "users": batch_users,
            "routing_sha256": sha256_file(routing_part),
            "metric_sha256": sha256_file(metric_part),
            "paired_sha256": sha256_file(paired_part),
        })
        completed.add(batch_index)
        progress["completed_batches"] = sorted(completed)
        progress["last_completed_batch"] = batch_index
        atomic_write_json(progress_path, progress)

    routing_path = output_root / contract["outputs"]["routing"]
    metrics_path = output_root / contract["outputs"]["user_arm_metrics"]
    paired_path = output_root / contract["outputs"]["paired_deltas"]
    combine_parquet_parts(routing_parts, routing_path, ROUTING_SCHEMA)
    combine_parquet_parts(metric_parts, metrics_path, METRIC_SCHEMA)
    combine_parquet_parts(paired_parts, paired_path, PAIRED_SCHEMA)
    metrics = pq.read_table(metrics_path).to_pandas()
    paired = pq.read_table(paired_path).to_pandas()
    confirmatory_metrics = metrics[metrics["confirmatory"]]
    confirmatory_paired = paired[paired["confirmatory"]]
    if len(confirmatory_paired) != 1053:
        raise RuntimeError("confirmatory paired cohort drift")

    bootstrap = bootstrap_paired(
        confirmatory_paired["delta_ndcg_at_10"].to_numpy(dtype=np.float64),
        confirmatory_paired["delta_harm_at_2"].to_numpy(dtype=np.float64),
        iterations=int(contract["bootstrap"]["iterations"]),
        seed=int(contract["bootstrap"]["seed"]),
    )
    decision = decide(bootstrap)
    strata: dict[str, Any] = {
        "schema_version": 1,
        "confirmatory_users": 1053,
        "counts": {},
        "benefit_harm_user_counts": benefit_harm_counts(confirmatory_paired),
        "by_stratum": {},
        "locked_test_used": False,
        "champion": None,
        "product_policy_updated": False,
    }
    for stratum, group in confirmatory_paired.groupby("applicability_stratum", sort=True):
        selected_metrics = confirmatory_metrics[confirmatory_metrics["applicability_stratum"] == stratum]
        strata["counts"][str(stratum)] = int(len(group))
        strata["by_stratum"][str(stratum)] = {
            "users": int(len(group)),
            "benefit_harm_user_counts": benefit_harm_counts(group),
            "mean_deltas": {
                column: float(group[column].mean())
                for column in (
                    "delta_ndcg_at_10", "delta_recall_at_10", "delta_mrr_at_10",
                    "delta_candidate_recall_at_500", "delta_harm_at_2",
                    "delta_fallback_user", "delta_applicable_user",
                )
            },
            "aggregate": {
                variant: aggregate(selected_metrics[selected_metrics["variant"] == variant])
                for variant in ("COMPARATOR", "CANDIDATE")
            },
        }
    expected_counts = {"BOTH_FALLBACK": 115, "BOTH_LIGHTFM": 661, "K10_NEWLY_APPLICABLE": 277}
    if strata["counts"] != expected_counts:
        raise RuntimeError("019E strata count drift")
    strata_path = output_root / contract["outputs"]["strata"]
    atomic_write_json(strata_path, strata)

    aggregate_metrics: dict[str, Any] = {}
    for population, selected in (
        ("CONFIRMATORY", metrics[metrics["confirmatory"]]),
        ("ALL_K10_COHORT_DIAGNOSTIC", metrics),
    ):
        aggregate_metrics[population] = {
            variant: aggregate(selected[selected["variant"] == variant])
            for variant in ("COMPARATOR", "CANDIDATE")
        }
    result = {
        "schema_version": 1,
        "evidence_id": "REC-EV-019E",
        "contract_id": contract["contract_id"],
        "status": decision["status"],
        "reason": decision["reason"],
        "evidence_classification": contract["evidence_classification"],
        "execution_role": "VALIDATION_019E_POST_HOC",
        "contract_sha256": lock["contract_sha256"],
        "protocol_lock_sha256": sha256_file(output_root / contract["outputs"]["protocol_lock"]),
        "lock_preceded_019e_hybrid_result": True,
        "rec_ev_019d_result_and_harm_decomposition_already_observed": True,
        "source_ranking_reuse": contract["source_ranking_reuse"],
        "cohort": {"k10_users": 1479, "tuning_panel_excluded": 426, "confirmatory_users": 1053},
        "routing_counts_confirmatory": strata["counts"],
        "aggregate_metrics": aggregate_metrics,
        "paired_confirmatory": {
            "users": 1053,
            "bootstrap": bootstrap,
            "benefit_harm_user_counts": strata["benefit_harm_user_counts"],
        },
        "stratified_audit": strata["by_stratum"],
        "decision_rule_priority": contract["decision_rule"]["priority"],
        "decision": decision,
        "fresh_target_independent_validation_required": True,
        "interpretation_limits": [
            "The mitigation was selected after REC-EV-019D and reuses the same 1053 confirmatory users.",
            "A post-hoc pass is not new confirmatory evidence and cannot select a champion or update product policy.",
            "Fresh target-independent preregistered Validation is required before any product claim.",
        ],
        "locked_test_used": False,
        "champion": None,
        "product_policy_updated": False,
    }
    result_path = output_root / contract["outputs"]["result"]
    atomic_write_json(result_path, result)
    progress["status"] = "COMPLETE"
    progress["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    atomic_write_json(progress_path, progress)

    artifact_paths = [
        output_root / contract["outputs"]["protocol_lock"],
        output_root / contract["outputs"]["source_manifest"],
        cohort_path,
        routing_path,
        metrics_path,
        paired_path,
        strata_path,
        result_path,
        progress_path,
    ]
    manifest = {
        "schema_version": 1,
        "evidence_id": "REC-EV-019E",
        "status": decision["status"],
        "contract_path": repo_relative(contract_path, root=firewall.root),
        "contract_sha256": lock["contract_sha256"],
        "preregistration_path": repo_relative(prereg_path, root=firewall.root),
        "preregistration_sha256": lock["preregistration_sha256"],
        "source_checksums": {entry["name"]: entry["sha256"] for entry in verify_sources(contract, firewall)},
        "execution": {
            "lock_command": "py -3 scripts/run_rec_ev_019e_no_retune_incremental_applicability.py --phase lock --role validation-019e-post-hoc",
            "run_command": "py -3 scripts/run_rec_ev_019e_no_retune_incremental_applicability.py --phase run --role validation-019e-post-hoc --resume",
            "verify_command": "py -3 scripts/verify_rec_ev_019e_no_retune_incremental_applicability.py --manifest docs/recommendation/evidence/manifests/rec-ev-019e-validation.json",
            "required_019d_full_rescore_command": "py -3 scripts/verify_rec_ev_019d_prefix_ablation.py --manifest docs/recommendation/evidence/manifests/rec-ev-019d-validation.json --full-rescore-users all",
            "resume": True,
        },
        "artifacts": [artifact_entry(path, root=firewall.root) for path in artifact_paths],
        "result": result,
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
        raise AuthorizationError("only the repository REC-EV-019E contract is accepted")
    contract = read_json(path)
    if contract.get("contract_id") != "REC-EV-019E-NO-RETUNE-INCREMENTAL-APPLICABILITY-GATE":
        raise AuthorizationError("unexpected contract identity")
    if contract.get("invariants") != {
        "execution_role": "VALIDATION_019E_POST_HOC",
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
    parser.add_argument("--role", choices=("validation-019e-post-hoc",), required=True)
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
            "future_metrics_read": lock["future_metrics_read"],
            "git": lock["git"],
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
        "paired_confirmatory": result["paired_confirmatory"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
