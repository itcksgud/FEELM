#!/usr/bin/env python3
"""Linux dependency smoke test for REC-EV-019C B8 LightFM.

This is a tiny synthetic fit only. It proves the pinned binary imports and that
the signed-logistic/frozen-item fold-in design is executable; it never opens a
MovieLens, TMDB, Validation, or Locked Test artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import sys
from pathlib import Path
from typing import Any

import numpy as np
import scipy.sparse as sp
from lightfm import LightFM


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs/recommendation/contracts/rec-ev-019c-validation-artifacts.json"


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: Any) -> bytes:
    payload = canonical_json_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    return payload


def frozen_logistic_fold_in(
    item_biases: np.ndarray,
    item_embeddings: np.ndarray,
    observed_item_ids: np.ndarray,
    labels: np.ndarray,
    *,
    epochs: int = 80,
    learning_rate: float = 0.05,
    regularization: float = 0.001,
) -> tuple[float, np.ndarray]:
    user_bias = 0.0
    user_vector = np.zeros(item_embeddings.shape[1], dtype=np.float32)
    for _ in range(epochs):
        for item_id, label in zip(observed_item_ids, labels, strict=True):
            item_vector = item_embeddings[int(item_id)]
            score = user_bias + float(item_biases[int(item_id)]) + float(user_vector @ item_vector)
            signed_margin = float(label) * score
            if signed_margin >= 0:
                factor = -float(label) * math.exp(-signed_margin) / (1.0 + math.exp(-signed_margin))
            else:
                factor = -float(label) / (1.0 + math.exp(signed_margin))
            user_vector -= learning_rate * (factor * item_vector + regularization * user_vector)
            user_bias -= learning_rate * factor
    return user_bias, user_vector


def run_smoke(contract: dict[str, Any]) -> dict[str, Any]:
    dependency = contract["models"]["B8_LIGHTFM"]["dependency"]
    installed_version = importlib.metadata.version(dependency["distribution"])
    machine = platform.machine().lower()

    rows = np.array([0, 0, 1, 1, 2, 2], dtype=np.int32)
    columns = np.array([0, 1, 1, 2, 3, 4], dtype=np.int32)
    signed_labels = np.array([1, -1, 1, -1, 1, -1], dtype=np.float32)
    interactions = sp.coo_matrix((signed_labels, (rows, columns)), shape=(3, 5), dtype=np.float32)
    confidence = sp.coo_matrix(
        (np.ones_like(signed_labels), (rows, columns)),
        shape=interactions.shape,
        dtype=np.float32,
    )
    metadata = sp.csr_matrix(
        np.array(
            [
                [1.0, 0.0, 0.0],
                [1.0, 1.0, 0.0],
                [0.0, 1.0, 0.0],
                [0.0, 0.0, 1.0],
                [1.0, 0.0, 1.0],
            ],
            dtype=np.float32,
        )
    )
    item_features = sp.hstack([sp.identity(5, dtype=np.float32, format="csr"), metadata], format="csr")
    model = LightFM(
        no_components=4,
        loss="logistic",
        learning_schedule="adagrad",
        learning_rate=0.05,
        item_alpha=0.000001,
        user_alpha=0.000001,
        random_state=17,
    )
    model.fit(
        interactions,
        item_features=item_features,
        sample_weight=confidence,
        epochs=4,
        num_threads=1,
        verbose=False,
    )
    base_predictions = model.predict(
        np.zeros(5, dtype=np.int32),
        np.arange(5, dtype=np.int32),
        item_features=item_features,
        num_threads=1,
    )
    item_biases, item_embeddings = model.get_item_representations(item_features)
    item_hash_before = hashlib.sha256(item_biases.tobytes() + item_embeddings.tobytes()).hexdigest()
    target_bias, target_vector = frozen_logistic_fold_in(
        item_biases,
        item_embeddings,
        np.array([0, 1], dtype=np.int32),
        np.array([1, -1], dtype=np.float32),
    )
    target_predictions = target_bias + item_biases + item_embeddings @ target_vector
    item_hash_after = hashlib.sha256(item_biases.tobytes() + item_embeddings.tobytes()).hexdigest()

    checks = {
        "python_3_12": sys.version_info[:2] == (3, 12),
        "linux_x86_64": sys.platform.startswith("linux") and machine in {"x86_64", "amd64"},
        "distribution_version_exact": installed_version == dependency["version"],
        "signed_logistic_fit": set(interactions.data.tolist()) == {-1.0, 1.0} and model.loss == "logistic",
        "predictions_finite": bool(np.isfinite(base_predictions).all() and np.isfinite(target_predictions).all()),
        "item_features_used": item_features.shape == (5, 8) and item_embeddings.shape == (5, 4),
        "item_parameters_frozen_during_target_fold_in": item_hash_before == item_hash_after,
        "unrated_negative_sampling_absent": interactions.nnz == 6 and confidence.nnz == 6,
        "pairwise_losses_not_executed": model.loss not in {"bpr", "warp", "warp-kos"},
    }
    required = contract["dependency_smoke_artifacts"]["required_checks"]
    if set(checks) != set(required):
        raise RuntimeError("dependency smoke check inventory differs from contract")
    if not all(checks.values()):
        raise RuntimeError(f"dependency smoke failed: {sorted(key for key, value in checks.items() if not value)}")

    return {
        "schema_version": 1,
        "evidence_id": "REC-EV-019C-LIGHTFM-LINUX-SMOKE",
        "status": "PASS_DEPENDENCY_SMOKE",
        "contract_sha256": sha256_file(CONTRACT_PATH),
        "runtime_lock_sha256": sha256_file(ROOT / dependency["hash_lock"]),
        "runtime_image": dependency["runtime_image"],
        "python": platform.python_version(),
        "platform": platform.platform(),
        "distribution": dependency["distribution"],
        "distribution_version": installed_version,
        "import_package": dependency["import_package"],
        "loss": model.loss,
        "signed_interaction_values": sorted(set(interactions.data.tolist())),
        "checks": checks,
        "base_prediction_checksum": hashlib.sha256(base_predictions.astype(np.float32).tobytes()).hexdigest(),
        "target_prediction_checksum": hashlib.sha256(target_predictions.astype(np.float32).tobytes()).hexdigest(),
        "real_validation_executed": False,
        "locked_test_opened": False,
        "product_policy_changed": False,
        "product_champion": None,
        "next_gate": "RESOURCE_DRY_RUN_AND_CONTRACT_AMENDMENT_REVIEW",
    }


def write_evidence(contract: dict[str, Any], result: dict[str, Any]) -> tuple[Path, Path]:
    paths = contract["dependency_smoke_artifacts"]
    result_path = ROOT / paths["result"]
    manifest_path = ROOT / paths["manifest"]
    result_bytes = atomic_json(result_path, result)
    manifest = {
        "schema_version": 1,
        "evidence_id": result["evidence_id"],
        "status": result["status"],
        "contract_sha256": result["contract_sha256"],
        "runtime_lock_sha256": result["runtime_lock_sha256"],
        "artifacts": [
            {
                "path": paths["result"],
                "bytes": len(result_bytes),
                "sha256": hashlib.sha256(result_bytes).hexdigest(),
            }
        ],
        "validation": {
            "required_checks": list(paths["required_checks"]),
            "all_required_checks_pass": True,
            "real_validation_executed": False,
            "locked_test_opened": False,
        },
        "adoption": {
            "champion": None,
            "product_policy_changed": False,
            "real_validation_authorized": False,
        },
    }
    atomic_json(manifest_path, manifest)
    return result_path, manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test REC-EV-019C LightFM dependency")
    parser.add_argument("--contract", type=Path, default=CONTRACT_PATH)
    args = parser.parse_args()
    try:
        contract = json.loads(args.contract.read_text(encoding="utf-8"))
        result = run_smoke(contract)
        result_path, manifest_path = write_evidence(contract, result)
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "distribution": f"{result['distribution']}=={result['distribution_version']}",
                    "result": result_path.relative_to(ROOT).as_posix(),
                    "manifest": manifest_path.relative_to(ROOT).as_posix(),
                    "real_validation_executed": False,
                    "locked_test_opened": False,
                    "next_gate": result["next_gate"],
                },
                sort_keys=True,
            )
        )
        return 0
    except Exception as error:
        print(f"REC-EV-019C LightFM dependency smoke failed: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
