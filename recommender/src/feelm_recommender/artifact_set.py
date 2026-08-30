from __future__ import annotations

import hashlib
import io
import json
import shutil
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from .errors import ArtifactValidationError
from .metadata import ArtifactKind, ArtifactMetadata, ModelStatus, sha256_file
from .policy import REC_EV_003B_POLICY
from .service import RecommendationCore


ARTIFACT_SET_SCHEMA_VERSION = 1
ARTIFACT_KEYS = ("bias", "factors", "calibration", "mapping")
ARTIFACT_KINDS = {
    "bias": ArtifactKind.BIAS,
    "factors": ArtifactKind.ALS_ITEM_FACTORS,
    "calibration": ArtifactKind.HEAD_CALIBRATION_BUNDLE,
    "mapping": ArtifactKind.ITEM_ID_MAPPING,
}


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _write_json(path: Path, value: object) -> None:
    path.write_bytes(_json_bytes(value))


def _npy_bytes(value: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    np.lib.format.write_array(buffer, np.asarray(value), allow_pickle=False)
    return buffer.getvalue()


def _write_deterministic_npz(path: Path, arrays: Mapping[str, np.ndarray]) -> None:
    """Write an NPZ without timestamps or platform-specific ZIP metadata."""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name in sorted(arrays):
            info = zipfile.ZipInfo(f"{name}.npy", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o600 << 16
            archive.writestr(info, _npy_bytes(np.asarray(arrays[name])))


def _safe_relative_path(root: Path, raw: str) -> Path:
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts:
        raise ArtifactValidationError("artifact set paths must stay below the manifest directory")
    resolved = (root / relative).resolve()
    if resolved != root.resolve() and root.resolve() not in resolved.parents:
        raise ArtifactValidationError("artifact set path escapes the manifest directory")
    return resolved


@dataclass(frozen=True, slots=True)
class ArtifactFiles:
    payload: Path
    metadata: Path


@dataclass(frozen=True, slots=True)
class LoadedArtifactSet:
    artifact_set_version: str
    set_kind: str
    coverage: str
    catalog_version: str
    manifest_path: Path
    artifacts: dict[str, ArtifactFiles]
    core: RecommendationCore

    @property
    def metadata(self) -> dict[str, ArtifactMetadata]:
        return {
            key: ArtifactMetadata.load(files.metadata)
            for key, files in self.artifacts.items()
        }


def _manifest_content(
    *, set_kind: str, coverage: str, catalog_version: str, artifacts: dict[str, dict[str, str]]
) -> dict[str, Any]:
    return {
        "schema_version": ARTIFACT_SET_SCHEMA_VERSION,
        "set_kind": set_kind,
        "coverage": coverage,
        "catalog_version": catalog_version,
        "artifacts": artifacts,
    }


def _artifact_set_version(content: dict[str, Any]) -> str:
    return f"c2-serving-set-v1-{hashlib.sha256(_json_bytes(content)).hexdigest()[:24]}"


def load_artifact_set(
    manifest_path: str | Path, *, enable_candidate: bool = False
) -> LoadedArtifactSet:
    path = Path(manifest_path).resolve()
    try:
        root = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ArtifactValidationError("cannot read artifact set manifest") from error
    if not isinstance(root, dict) or root.get("schema_version") != 1:
        raise ArtifactValidationError("artifact set manifest schema_version must be 1")
    raw_artifacts = root.get("artifacts")
    if not isinstance(raw_artifacts, dict) or set(raw_artifacts) != set(ARTIFACT_KEYS):
        raise ArtifactValidationError("artifact set must contain exactly four artifact entries")
    content = _manifest_content(
        set_kind=str(root.get("set_kind", "")),
        coverage=str(root.get("coverage", "")),
        catalog_version=str(root.get("catalog_version", "")),
        artifacts=raw_artifacts,
    )
    for key in ("set_kind", "coverage", "catalog_version"):
        if not str(content[key]).strip():
            raise ArtifactValidationError(f"artifact set {key} must not be blank")
    expected_version = _artifact_set_version(content)
    if root.get("artifact_set_version") != expected_version:
        raise ArtifactValidationError("artifact_set_version does not match canonical manifest")
    files: dict[str, ArtifactFiles] = {}
    for key in ARTIFACT_KEYS:
        entry = raw_artifacts[key]
        if not isinstance(entry, dict) or set(entry) != {
            "payload", "metadata", "payload_sha256", "metadata_sha256"
        }:
            raise ArtifactValidationError(f"artifact set {key} entry is invalid")
        files[key] = ArtifactFiles(
            _safe_relative_path(path.parent, str(entry["payload"])),
            _safe_relative_path(path.parent, str(entry["metadata"])),
        )
        if sha256_file(files[key].payload) != entry["payload_sha256"]:
            raise ArtifactValidationError(f"artifact set {key} payload checksum differs")
        if sha256_file(files[key].metadata) != entry["metadata_sha256"]:
            raise ArtifactValidationError(f"artifact set {key} metadata checksum differs")
        metadata = ArtifactMetadata.load(files[key].metadata)
        metadata.require_kind(ARTIFACT_KINDS[key])

    core = RecommendationCore.from_artifacts(
        bias_payload=files["bias"].payload,
        bias_metadata_path=files["bias"].metadata,
        factor_payload=files["factors"].payload,
        factor_metadata_path=files["factors"].metadata,
        calibrator_payload=files["calibration"].payload,
        calibrator_metadata_path=files["calibration"].metadata,
        mapping_payload=files["mapping"].payload,
        mapping_metadata_path=files["mapping"].metadata,
        enable_candidate=enable_candidate,
    )
    mapping_compatibility = core.mapping_metadata.compatibility or {}
    if mapping_compatibility.get("catalog_version") != content["catalog_version"]:
        raise ArtifactValidationError("artifact set catalog_version differs from mapping metadata")
    # The readiness dry-run proves at least one mapped item is present in the
    # Popularity model, not merely that the numeric array accepts an index.
    available_items = [
        item_id
        for item_id in sorted(core.item_mapping.by_movielens_id)
        if item_id < len(core.bias_model.item_counts) and core.bias_model.item_counts[item_id] > 0
    ]
    if not available_items:
        raise ArtifactValidationError("serving dry-run has no mapped Popularity model item")
    first_item = available_items[0]
    core.rank(np.asarray([first_item], dtype=np.int64))
    return LoadedArtifactSet(
        expected_version,
        content["set_kind"],
        content["coverage"],
        content["catalog_version"],
        path,
        files,
        core,
    )


def _metadata_dict(
    *,
    kind: ArtifactKind,
    model_version: str,
    evidence_id: str,
    compatibility_id: str,
    checksum: str,
    parameters: dict[str, Any],
    compatibility: dict[str, Any] | None = None,
    factor_rank: int | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "artifact_kind": kind.value,
        "model_version": model_version,
        "model_status": ModelStatus.VALIDATED_CANDIDATE_NOT_CHAMPION.value,
        "evidence_id": evidence_id,
        "run_id": model_version,
        "compatibility_id": compatibility_id,
        "id_space": "movielens-int-v1",
        "payload_sha256": checksum,
        "parameters": parameters,
        "compatibility": compatibility,
        "rating_min": 0.5,
        "rating_max": 5.0,
        "factor_rank": factor_rank,
    }


def export_fixture_artifact_set(
    output_dir: str | Path,
    *,
    mapping_payload: str | Path | None = None,
    mapping_metadata_path: str | Path | None = None,
    fixture_item_count: int = 3,
) -> Path:
    """Export a complete deterministic non-production set for C2A contract tests.

    Supplying a Catalog mapping exercises real mapping coverage, but the generated model
    artifacts remain fixtures and must never be described as production coverage.
    """
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    if fixture_item_count < 3 or fixture_item_count > 10_000:
        raise ValueError("fixture item count must be between 3 and 10000")
    if (mapping_payload is None) != (mapping_metadata_path is None):
        raise ValueError("mapping payload and metadata must be supplied together")
    if mapping_payload is not None and fixture_item_count != 3:
        raise ValueError("fixture item count cannot replace a supplied mapping")

    if mapping_payload is None:
        mapping = {
            "schema_version": 1,
            "mapping_version": "c2-fixture-mapping-v1",
            "source_id_space": "movielens-int-v1",
            "target_id_space": "feelm-movie-uuid-v1",
            "records": [
                {"movielens_item_id": item_id, "service_movie_id": str(uuid.UUID(int=item_id))}
                for item_id in range(1, fixture_item_count + 1)
            ],
        }
        mapping_out = target / "mapping.json"
        _write_json(mapping_out, mapping)
        compatibility_id = "c2-fixture-serving-family-v1"
        catalog_version = "c2-fixture-catalog-v1"
        mapping_version = mapping["mapping_version"]
        mapping_checksum = sha256_file(mapping_out)
        mapping_metadata = _metadata_dict(
            kind=ArtifactKind.ITEM_ID_MAPPING,
            model_version=mapping_version,
            evidence_id="C2-CONTRACT-FIXTURE",
            compatibility_id=compatibility_id,
            checksum=mapping_checksum,
            parameters={"mapping_format": "json-v1", "coverage_scope": "FIXTURE_ONLY"},
            compatibility={
                "mapping_version": mapping_version,
                "source_id_space": "movielens-int-v1",
                "target_id_space": "feelm-movie-uuid-v1",
                "catalog_version": catalog_version,
                "catalog_sha256": "0" * 64,
            },
        )
        mapping_meta_out = target / "mapping.metadata.json"
        _write_json(mapping_meta_out, mapping_metadata)
        set_kind = "FIXTURE"
        coverage = "CONTRACT_FIXTURE_ONLY_NOT_PRODUCTION_COVERAGE"
    else:
        source_mapping = Path(mapping_payload)
        source_metadata = Path(mapping_metadata_path)
        source = ArtifactMetadata.load(source_metadata)
        source.require_kind(ArtifactKind.ITEM_ID_MAPPING)
        source.verify_payload(source_mapping)
        mapping_compatibility = source.compatibility or {}
        catalog_version = str(mapping_compatibility.get("catalog_version", ""))
        if not catalog_version:
            raise ArtifactValidationError("mapping metadata must bind catalog_version")
        mapping_out = target / "mapping.json"
        mapping_meta_out = target / "mapping.metadata.json"
        shutil.copyfile(source_mapping, mapping_out)
        shutil.copyfile(source_metadata, mapping_meta_out)
        compatibility_id = source.compatibility_id
        mapping_checksum = source.payload_sha256
        set_kind = "CATALOG_MAPPING_FIXTURE"
        coverage = "INPUT_CATALOG_MAPPING_ONLY_WITH_FIXTURE_MODEL_NOT_PRODUCTION_COVERAGE"

    mapping_root = json.loads(mapping_out.read_text(encoding="utf-8"))
    item_ids = sorted(int(row["movielens_item_id"]) for row in mapping_root["records"])
    max_item = max(item_ids)
    item_counts = np.zeros(max_item + 1, dtype=np.int64)
    item_sums = np.zeros(max_item + 1, dtype=np.float64)
    # Stable, distinct fixture scores. They carry no product or model claim.
    for position, item_id in enumerate(item_ids):
        count = 100 - min(position, 90)
        item_counts[item_id] = count
        item_sums[item_id] = count * (4.0 - min(position, 30) * 0.01)
    bias_out = target / "bias.npz"
    _write_deterministic_npz(
        bias_out,
        {
            "global_mean": np.asarray(3.5, dtype=np.float64),
            "movie_bias": np.zeros(max_item + 1, dtype=np.float64),
            "movie_counts": item_counts,
            "movie_sums": item_sums,
            "user_bias": np.zeros(1, dtype=np.float64),
            "user_counts": np.zeros(1, dtype=np.int64),
            "user_sums": np.zeros(1, dtype=np.float64),
        },
    )
    bias_checksum = sha256_file(bias_out)
    bias_version = f"c2-fixture-bias-{bias_checksum[:16]}"
    _write_json(
        target / "bias.metadata.json",
        _metadata_dict(
            kind=ArtifactKind.BIAS,
            model_version=bias_version,
            evidence_id="REC-EV-003B",
            compatibility_id=compatibility_id,
            checksum=bias_checksum,
            parameters={
                "reg_user": 10.0,
                "reg_item": 25.0,
                "iterations": 10,
                "popularity_prior_count": 50.0,
                "fixture_only": True,
            },
        ),
    )

    factors_out = target / "factors.npz"
    _write_deterministic_npz(
        factors_out,
        {
            "movie_ids": np.asarray(item_ids, dtype=np.int64),
            "movie_factors": np.zeros((len(item_ids), 2), dtype=np.float64),
        },
    )
    factor_checksum = sha256_file(factors_out)
    factor_version = f"c2-fixture-factors-{factor_checksum[:16]}"
    _write_json(
        target / "factors.metadata.json",
        _metadata_dict(
            kind=ArtifactKind.ALS_ITEM_FACTORS,
            model_version=factor_version,
            evidence_id="REC-EV-003B",
            compatibility_id=compatibility_id,
            checksum=factor_checksum,
            parameters={"reg_param": 0.1, "max_iter": 10, "seed": 42, "fixture_only": True},
            factor_rank=2,
        ),
    )

    calibrators = {
        str(k): {"x_thresholds": [0.5, 5.0], "y_thresholds": [0.5, 5.0]}
        for k in sorted(REC_EV_003B_POLICY.star_alpha_by_k)
    }
    calibration = {
        "schema_version": 2,
        "policy_version": REC_EV_003B_POLICY.version,
        "heads": {
            "ranking": {"mode": "NONE_POPULARITY_RAW", "alpha": 0.0},
            "star_blend": {"mode": "ISOTONIC_BY_K", "calibrators": calibrators},
        },
    }
    calibration_out = target / "calibration.json"
    _write_json(calibration_out, calibration)
    calibration_checksum = sha256_file(calibration_out)
    calibration_version = f"c2-fixture-calibration-{calibration_checksum[:16]}"
    _write_json(
        target / "calibration.metadata.json",
        _metadata_dict(
            kind=ArtifactKind.HEAD_CALIBRATION_BUNDLE,
            model_version=calibration_version,
            evidence_id="REC-EV-003B",
            compatibility_id=compatibility_id,
            checksum=calibration_checksum,
            parameters={"calibration": "contract-fixture", "fixture_only": True},
            compatibility={
                "policy_version": REC_EV_003B_POLICY.version,
                "star_head": "ISOTONIC_BY_K",
                "ranking_head": "NONE_POPULARITY_RAW",
                "ranking_alpha": 0.0,
                "bias_payload_sha256": bias_checksum,
                "factor_payload_sha256": factor_checksum,
                "mapping_payload_sha256": mapping_checksum,
            },
        ),
    )

    artifacts = {}
    for key, payload_name, metadata_name in (
        ("bias", "bias.npz", "bias.metadata.json"),
        ("factors", "factors.npz", "factors.metadata.json"),
        ("calibration", "calibration.json", "calibration.metadata.json"),
        ("mapping", "mapping.json", "mapping.metadata.json"),
    ):
        artifacts[key] = {
            "payload": payload_name,
            "metadata": metadata_name,
            "payload_sha256": sha256_file(target / payload_name),
            "metadata_sha256": sha256_file(target / metadata_name),
        }
    content = _manifest_content(
        set_kind=set_kind,
        coverage=coverage,
        catalog_version=catalog_version,
        artifacts=artifacts,
    )
    manifest = {**content, "artifact_set_version": _artifact_set_version(content)}
    manifest_path = target / "artifact-set.json"
    _write_json(manifest_path, manifest)
    load_artifact_set(manifest_path)
    return manifest_path


def assemble_artifact_set(
    output_dir: str | Path,
    *,
    artifacts: Mapping[str, tuple[str | Path, str | Path]],
    catalog_version: str,
    coverage: str = "EVIDENCE_BOUNDED_ARTIFACT_SET_NOT_PRODUCTION_APPROVAL",
) -> Path:
    """Copy and validate an already-produced evidence-bounded four-artifact set."""
    if set(artifacts) != set(ARTIFACT_KEYS):
        raise ValueError("exactly bias, factors, calibration and mapping are required")
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    entries: dict[str, dict[str, str]] = {}
    extensions = {"bias": ".npz", "factors": ".npz", "calibration": ".json", "mapping": ".json"}
    for key in ARTIFACT_KEYS:
        payload, metadata = map(Path, artifacts[key])
        payload_name = f"{key}{extensions[key]}"
        metadata_name = f"{key}.metadata.json"
        shutil.copyfile(payload, target / payload_name)
        shutil.copyfile(metadata, target / metadata_name)
        entries[key] = {
            "payload": payload_name,
            "metadata": metadata_name,
            "payload_sha256": sha256_file(target / payload_name),
            "metadata_sha256": sha256_file(target / metadata_name),
        }
    content = _manifest_content(
        set_kind="EVIDENCE_BOUNDED",
        coverage=coverage,
        catalog_version=catalog_version,
        artifacts=entries,
    )
    manifest_path = target / "artifact-set.json"
    _write_json(manifest_path, {**content, "artifact_set_version": _artifact_set_version(content)})
    load_artifact_set(manifest_path)
    return manifest_path
