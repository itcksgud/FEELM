from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ArtifactValidationError


SOURCE_ID_SPACE = "movielens-int-v1"
TARGET_ID_SPACE = "feelm-movie-uuid-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_POSITIVE_DECIMAL = re.compile(r"^[1-9][0-9]*$")


@dataclass(frozen=True, slots=True, order=True)
class CatalogMappingQuarantine:
    line_number: int
    reason: str
    movielens_external_id: str | None = None
    service_movie_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "line_number": self.line_number,
            "reason": self.reason,
        }
        if self.movielens_external_id is not None:
            result["movielens_external_id"] = self.movielens_external_id
        if self.service_movie_id is not None:
            result["service_movie_id"] = self.service_movie_id
        return result


@dataclass(frozen=True, slots=True)
class CatalogMappingExportResult:
    catalog_version: str
    catalog_sha256: str
    mapping_version: str
    mapping_sha256: str
    accepted_records: int
    quarantined_records: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "PASS",
            "catalog_version": self.catalog_version,
            "catalog_sha256": self.catalog_sha256,
            "mapping_version": self.mapping_version,
            "mapping_sha256": self.mapping_sha256,
            "accepted_records": self.accepted_records,
            "quarantined_records": self.quarantined_records,
            "coverage_scope": "INPUT_CATALOG_ONLY_NOT_PRODUCTION_COVERAGE",
        }


@dataclass(frozen=True, slots=True, order=True)
class _Candidate:
    movielens_item_id: int
    service_movie_id: str
    line_number: int


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(value)
    temporary.replace(path)


def _read_header(line: bytes) -> tuple[str, str]:
    try:
        header = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ArtifactValidationError(f"catalog header is not valid UTF-8 JSON: {error}") from error
    if not isinstance(header, dict) or header.get("recordType") != "artifactHeader":
        raise ArtifactValidationError("catalog first record must be artifactHeader")
    if header.get("schemaVersion") != 1:
        raise ArtifactValidationError("catalog artifact schemaVersion must be 1")
    catalog_version = header.get("catalogVersion")
    if not isinstance(catalog_version, str) or not catalog_version.strip():
        raise ArtifactValidationError("catalogVersion must be a nonblank string")
    checksums = header.get("sourceChecksums")
    archive_sha256 = (
        checksums.get("movielensArchiveSha256") if isinstance(checksums, dict) else None
    )
    if not isinstance(archive_sha256, str) or not _SHA256.fullmatch(archive_sha256):
        raise ArtifactValidationError(
            "sourceChecksums.movielensArchiveSha256 must be lowercase SHA-256"
        )
    return catalog_version, archive_sha256


def _service_uuid(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError):
        return None


def _extract_candidates(
    catalog_bytes: bytes,
) -> tuple[str, str, list[_Candidate], list[CatalogMappingQuarantine]]:
    lines = catalog_bytes.splitlines()
    if not lines:
        raise ArtifactValidationError("catalog artifact is empty")
    catalog_version, archive_sha256 = _read_header(lines[0])
    candidates: list[_Candidate] = []
    quarantine: list[CatalogMappingQuarantine] = []
    for line_number, line in enumerate(lines[1:], start=2):
        if not line.strip():
            quarantine.append(CatalogMappingQuarantine(line_number, "INVALID_JSON"))
            continue
        try:
            record = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            quarantine.append(CatalogMappingQuarantine(line_number, "INVALID_JSON"))
            continue
        if not isinstance(record, dict):
            quarantine.append(
                CatalogMappingQuarantine(line_number, "INVALID_RECORD_ENVELOPE")
            )
            continue
        if record.get("recordType") != "movieIdentity":
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict):
            quarantine.append(
                CatalogMappingQuarantine(line_number, "INVALID_RECORD_ENVELOPE")
            )
            continue
        service_id = _service_uuid(payload.get("movieId"))
        if service_id is None:
            quarantine.append(
                CatalogMappingQuarantine(line_number, "INVALID_SERVICE_MOVIE_ID")
            )
            continue
        if payload.get("identityStatus") != "IDENTITY_VERIFIED":
            quarantine.append(
                CatalogMappingQuarantine(
                    line_number, "IDENTITY_NOT_VERIFIED", service_movie_id=service_id
                )
            )
            continue
        external_ids = payload.get("externalIds")
        if not isinstance(external_ids, list):
            quarantine.append(
                CatalogMappingQuarantine(
                    line_number, "INVALID_EXTERNAL_IDS", service_movie_id=service_id
                )
            )
            continue
        movielens_ids = [
            external
            for external in external_ids
            if isinstance(external, dict) and external.get("source") == "MOVIELENS"
        ]
        if not movielens_ids:
            quarantine.append(
                CatalogMappingQuarantine(
                    line_number, "MOVIELENS_EXTERNAL_ID_MISSING", service_movie_id=service_id
                )
            )
            continue
        for external in movielens_ids:
            external_id = external.get("externalId")
            if external.get("verificationStatus") not in {"VERIFIED", "RECOVERED"}:
                quarantine.append(
                    CatalogMappingQuarantine(
                        line_number,
                        "MOVIELENS_EXTERNAL_ID_UNVERIFIED",
                        service_movie_id=service_id,
                    )
                )
                continue
            if not isinstance(external_id, str) or not _POSITIVE_DECIMAL.fullmatch(
                external_id
            ):
                quarantine.append(
                    CatalogMappingQuarantine(
                        line_number,
                        "INVALID_MOVIELENS_EXTERNAL_ID",
                        service_movie_id=service_id,
                    )
                )
                continue
            candidates.append(_Candidate(int(external_id), service_id, line_number))
    return catalog_version, archive_sha256, candidates, quarantine


def _resolve_candidates(
    candidates: list[_Candidate], quarantine: list[CatalogMappingQuarantine]
) -> tuple[list[tuple[int, str]], list[CatalogMappingQuarantine]]:
    first_by_pair: dict[tuple[int, str], _Candidate] = {}
    for candidate in sorted(candidates, key=lambda item: item.line_number):
        pair = (candidate.movielens_item_id, candidate.service_movie_id)
        if pair in first_by_pair:
            quarantine.append(
                CatalogMappingQuarantine(
                    candidate.line_number,
                    "DUPLICATE_MAPPING",
                    str(candidate.movielens_item_id),
                    candidate.service_movie_id,
                )
            )
        else:
            first_by_pair[pair] = candidate

    source_targets: dict[int, set[str]] = {}
    target_sources: dict[str, set[int]] = {}
    for source, target in first_by_pair:
        source_targets.setdefault(source, set()).add(target)
        target_sources.setdefault(target, set()).add(source)
    conflicting_sources = {
        source for source, targets in source_targets.items() if len(targets) > 1
    }
    conflicting_targets = {
        target for target, sources in target_sources.items() if len(sources) > 1
    }

    accepted: list[tuple[int, str]] = []
    for pair, candidate in sorted(first_by_pair.items()):
        source, target = pair
        if source in conflicting_sources:
            quarantine.append(
                CatalogMappingQuarantine(
                    candidate.line_number,
                    "SOURCE_ID_CONFLICT",
                    str(source),
                    target,
                )
            )
        elif target in conflicting_targets:
            quarantine.append(
                CatalogMappingQuarantine(
                    candidate.line_number,
                    "SERVICE_ID_CONFLICT",
                    str(source),
                    target,
                )
            )
        else:
            accepted.append(pair)
    return accepted, sorted(quarantine)


def export_catalog_mapping(
    *,
    catalog_path: str | Path,
    mapping_path: str | Path,
    metadata_path: str | Path,
    quarantine_path: str | Path,
    compatibility_id: str,
) -> CatalogMappingExportResult:
    if not compatibility_id.strip():
        raise ArtifactValidationError("compatibility_id must not be blank")
    try:
        catalog_bytes = Path(catalog_path).read_bytes()
    except OSError as error:
        raise ArtifactValidationError(f"cannot read catalog artifact: {error}") from error
    catalog_sha256 = hashlib.sha256(catalog_bytes).hexdigest()
    catalog_version, archive_sha256, candidates, quarantine = _extract_candidates(
        catalog_bytes
    )
    accepted, quarantine = _resolve_candidates(candidates, quarantine)
    if not accepted:
        raise ArtifactValidationError("catalog has no safe MovieLens-to-service mappings")

    version_seed = f"{catalog_version}\n{catalog_sha256}".encode("utf-8")
    mapping_version = f"catalog-mapping-v1-{hashlib.sha256(version_seed).hexdigest()[:24]}"
    mapping = {
        "schema_version": 1,
        "mapping_version": mapping_version,
        "source_id_space": SOURCE_ID_SPACE,
        "target_id_space": TARGET_ID_SPACE,
        "records": [
            {"movielens_item_id": source, "service_movie_id": target}
            for source, target in accepted
        ],
    }
    mapping_bytes = _canonical_json(mapping)
    mapping_sha256 = hashlib.sha256(mapping_bytes).hexdigest()
    reason_counts = Counter(item.reason for item in quarantine)
    quarantine_report = {
        "schema_version": 1,
        "catalog_version": catalog_version,
        "catalog_sha256": catalog_sha256,
        "mapping_version": mapping_version,
        "accepted_records": len(accepted),
        "quarantined_records": len(quarantine),
        "coverage_scope": "INPUT_CATALOG_ONLY_NOT_PRODUCTION_COVERAGE",
        "reason_counts": dict(sorted(reason_counts.items())),
        "records": [item.to_dict() for item in quarantine],
    }
    metadata = {
        "schema_version": 1,
        "artifact_kind": "movielens-service-item-mapping-v1",
        "model_version": mapping_version,
        "model_status": "VALIDATED_CANDIDATE_NOT_CHAMPION",
        "evidence_id": f"catalog-artifact:{catalog_version}",
        "run_id": mapping_version,
        "compatibility_id": compatibility_id,
        "id_space": SOURCE_ID_SPACE,
        "payload_sha256": mapping_sha256,
        "parameters": {
            "accepted_records": len(accepted),
            "catalog_sha256": catalog_sha256,
            "catalog_version": catalog_version,
            "exporter": "catalog-jsonl-v1-to-mapping-v1",
            "mapping_format": "json-v1",
            "quarantined_records": len(quarantine),
        },
        "compatibility": {
            "catalog_sha256": catalog_sha256,
            "catalog_version": catalog_version,
            "mapping_version": mapping_version,
            "movielens_archive_sha256": archive_sha256,
            "source_id_space": SOURCE_ID_SPACE,
            "target_id_space": TARGET_ID_SPACE,
        },
        "rating_min": 0.5,
        "rating_max": 5.0,
        "factor_rank": None,
    }

    _write_bytes(Path(mapping_path), mapping_bytes)
    _write_bytes(Path(metadata_path), _canonical_json(metadata))
    _write_bytes(Path(quarantine_path), _canonical_json(quarantine_report))
    return CatalogMappingExportResult(
        catalog_version=catalog_version,
        catalog_sha256=catalog_sha256,
        mapping_version=mapping_version,
        mapping_sha256=mapping_sha256,
        accepted_records=len(accepted),
        quarantined_records=len(quarantine),
    )
