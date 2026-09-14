"""Shared paths and fail-closed helpers for the hybrid345 experiment.

This module deliberately has no dependency on ratings or evaluation labels.  The
document and encoder stages import it before those data are allowed to be read.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "recommendation" / "experiments" / "hybrid345"
OUT = ROOT / "outputs" / "recommendation-evidence" / "hybrid345"
RUNTIME = ROOT / "outputs" / "recommendation-evidence" / "hybrid345-runtime"
PRELABEL_REVIEW = DOC / "prelabel-code-review.json"
PRELABEL_FILES = (
    "scripts/hybrid345_common.py",
    "scripts/hybrid345_documents.py",
    "scripts/hybrid345_encode.py",
    "scripts/hybrid345_models.py",
    "scripts/hybrid345_score.py",
    "scripts/test_hybrid345_documents.py",
    "scripts/test_hybrid345_encode.py",
    "scripts/test_hybrid345_models.py",
    "scripts/test_hybrid345_score.py",
    "docs/recommendation/experiments/hybrid345/DESIGN.md",
    "docs/recommendation/experiments/hybrid345/config.json",
    "docs/recommendation/experiments/hybrid345/design-review.json",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, value: Any) -> None:
    """Atomically write stable, human-readable JSON on the same volume."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, destination)


def _sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk_size):
            digest.update(block)
    return digest.hexdigest()


def pin(file: str | Path) -> dict[str, Any]:
    path = Path(file)
    require(path.is_file(), f"missing file: {path}")
    return {"bytes": int(path.stat().st_size), "sha256": _sha256_file(path)}


def load_config() -> dict[str, Any]:
    config = read_json(DOC / "config.json")
    require(config.get("experiment") == "hybrid345", "hybrid345 config identity drift")
    require(config.get("claim_scope") == "DEVELOPMENT_ONLY", "hybrid345 claim scope drift")
    return config


def source_path(name: str) -> Path:
    config = load_config()
    require(name in config.get("sources", {}), f"unknown hybrid345 source: {name}")
    raw = Path(str(config["sources"][name]))
    resolved = (raw if raw.is_absolute() else ROOT / raw).resolve()
    require(resolved.is_relative_to(ROOT.resolve()), f"source escapes repository: {name}")
    require(resolved.exists(), f"missing hybrid345 source {name}: {resolved}")
    return resolved


def prelabel_fingerprint() -> dict[str, dict[str, Any]]:
    return {name: pin(ROOT / name) for name in PRELABEL_FILES}


def require_prelabel_review() -> dict[str, Any]:
    require(PRELABEL_REVIEW.is_file(), "independent pre-label code review is required")
    review = read_json(PRELABEL_REVIEW)
    require(review.get("status") == "PASS", "pre-label code review did not pass")
    require(review.get("fingerprint") == prelabel_fingerprint(),
            "pre-label implementation changed after independent review")
    return review
