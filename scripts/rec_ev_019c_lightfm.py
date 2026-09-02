"""Host-side serialization and pinned-container execution for B8."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np
from scipy import sparse


@dataclass(frozen=True)
class LightfmRepresentations:
    item_biases: np.ndarray
    item_factors: np.ndarray


def build_lightfm_item_features(structured: sparse.csr_matrix) -> sparse.csr_matrix:
    identity = sparse.identity(structured.shape[0], dtype=np.float32, format="csr")
    return sparse.hstack([identity, structured.astype(np.float32)], format="csr")


def fit_lightfm_in_container(
    contract: Mapping[str, Any],
    interactions: sparse.csr_matrix,
    item_features: sparse.csr_matrix,
    parameters: Mapping[str, Any],
    *,
    seed: int,
    job_directory: Path,
    root: Path,
) -> LightfmRepresentations:
    dependency = contract["models"]["B8_LIGHTFM"]["dependency"]
    fixed = contract["models"]["B8_LIGHTFM"]["fixed_parameters"]
    job_directory.mkdir(parents=True, exist_ok=True)
    config = {
        "dimension": int(parameters["dimension"]),
        "learning_schedule": str(parameters["learning_schedule"]),
        "learning_rate": float(fixed["learning_rate"]),
        "item_alpha": float(fixed["item_alpha"]),
        "user_alpha": float(fixed["user_alpha"]),
        "epochs": int(fixed["epochs"]),
        "seed": int(seed),
    }
    config_path = job_directory / "config.json"
    result_path = job_directory / "result.npz"
    if config_path.is_file() and result_path.is_file():
        existing = json.loads(config_path.read_text(encoding="utf-8"))
        if existing != config:
            raise RuntimeError("B8 cached fit configuration differs from the requested trial")
        result = np.load(result_path, allow_pickle=False)
        item_biases = result["item_biases"].astype(np.float32)
        item_factors = result["item_factors"].astype(np.float32)
        if item_biases.shape != (interactions.shape[1],) or item_factors.shape != (
            interactions.shape[1], int(parameters["dimension"])
        ):
            raise RuntimeError("B8 cached representation shape drift")
        if not np.isfinite(item_biases).all() or not np.isfinite(item_factors).all():
            raise RuntimeError("B8 cached representation contains non-finite values")
        return LightfmRepresentations(item_biases=item_biases, item_factors=item_factors)
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    sparse.save_npz(job_directory / "interactions.npz", interactions.astype(np.float32))
    sparse.save_npz(job_directory / "item-features.npz", item_features.astype(np.float32))
    relative_job = job_directory.resolve().relative_to(root.resolve()).as_posix()
    command = "\n".join([
        "python -m pip install --disable-pip-version-check --require-hashes -r requirements-rec-ev-019c.lock",
        f"python scripts/train_rec_ev_019c_lightfm.py --job /workspace/{relative_job}",
    ])
    completed = subprocess.run(
        [
            "docker", "run", "--rm", "--platform", "linux/amd64",
            "--mount", f"type=bind,source={root.resolve()},target=/workspace",
            "--mount", "type=volume,source=feelm-rec-ev-019c-pip,target=/root/.cache/pip",
            "--workdir", "/workspace", str(dependency["runtime_image"]), "sh", "-ec", command,
        ],
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"B8 pinned Linux container failed with exit code {completed.returncode}")
    result = np.load(result_path, allow_pickle=False)
    return LightfmRepresentations(
        item_biases=result["item_biases"].astype(np.float32),
        item_factors=result["item_factors"].astype(np.float32),
    )
