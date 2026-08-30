from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .artifact_set import LoadedArtifactSet, load_artifact_set
from .errors import ArtifactCompatibilityError, ArtifactValidationError
from .mapping import ItemIdMapping
from .metadata import ArtifactKind, ArtifactMetadata


CANDIDATE_SCHEMA_VERSION = 1
PRODUCER_POLICY = "GLOBAL_VERIFIED_CATALOG_V1"
COVERAGE_SCOPE = "INPUT_CATALOG_ONLY_NOT_PRODUCTION_COVERAGE"
QUARANTINE_REASONS = frozenset(
    {"NOT_UI_READY", "NOT_MAPPED", "MAPPING_CONFLICT", "MODEL_ITEM_MISSING", "DUPLICATE"}
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _atomic_write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ArtifactValidationError(f"cannot read {label}") from error
    if not isinstance(value, dict):
        raise ArtifactValidationError(f"{label} root must be an object")
    return value


def _service_uuid(value: object) -> str:
    if not isinstance(value, str):
        raise ArtifactValidationError("Catalog movieId must be a service UUID")
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError) as error:
        raise ArtifactValidationError("Catalog movieId must be a service UUID") from error


@dataclass(frozen=True, slots=True)
class CatalogProjection:
    service_movie_id: str
    eligible: bool
    duplicate: bool = False


@dataclass(frozen=True, slots=True)
class CandidateExportResult:
    catalog_version: str
    catalog_sha256: str
    candidate_set_version: str
    candidate_payload_sha256: str
    accepted_records: int
    quarantined_records: int
    reason_counts: dict[str, int]
    published: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "PASS",
            "catalog_version": self.catalog_version,
            "catalog_sha256": self.catalog_sha256,
            "candidate_set_version": self.candidate_set_version,
            "candidate_payload_sha256": self.candidate_payload_sha256,
            "accepted_records": self.accepted_records,
            "quarantined_records": self.quarantined_records,
            "reason_counts": self.reason_counts,
            "published": self.published,
            "producer_policy": PRODUCER_POLICY,
            "coverage_scope": COVERAGE_SCOPE,
        }


def _parse_catalog(catalog_bytes: bytes) -> tuple[str, str, list[CatalogProjection]]:
    lines = catalog_bytes.splitlines()
    if not lines:
        raise ArtifactValidationError("catalog artifact is empty")
    try:
        header = json.loads(lines[0].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ArtifactValidationError("catalog header is invalid") from error
    if (
        not isinstance(header, dict)
        or header.get("recordType") != "artifactHeader"
        or header.get("schemaVersion") != 1
    ):
        raise ArtifactValidationError("catalog first record must be artifactHeader schema v1")
    catalog_version = header.get("catalogVersion")
    if not isinstance(catalog_version, str) or not catalog_version.strip():
        raise ArtifactValidationError("catalogVersion must not be blank")

    identities: dict[str, list[bool]] = {}
    projections: list[CatalogProjection] = []
    for line in lines[1:]:
        if not line.strip():
            raise ArtifactValidationError("catalog contains a blank record")
        try:
            record = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ArtifactValidationError("catalog contains invalid JSON") from error
        if not isinstance(record, dict):
            raise ArtifactValidationError("catalog record envelope is invalid")
        record_type = record.get("recordType")
        if record_type not in {"movieIdentity", "movieProjection"}:
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict):
            raise ArtifactValidationError("catalog movie record payload is invalid")
        service_id = _service_uuid(payload.get("movieId"))
        if record_type == "movieIdentity":
            identities.setdefault(service_id, []).append(
                payload.get("identityStatus") == "IDENTITY_VERIFIED"
            )
            continue
        eligible = (
            payload.get("mediaType") == "MOVIE"
            and payload.get("identityStatus") == "IDENTITY_VERIFIED"
            and payload.get("visibilityStatus") == "UI_READY"
            and payload.get("deleted") is False
        )
        projections.append(CatalogProjection(service_id, eligible))

    if not projections:
        raise ArtifactValidationError("catalog contains no movieProjection records")
    projection_counts = Counter(item.service_movie_id for item in projections)
    result: list[CatalogProjection] = []
    for projection in projections:
        identity_rows = identities.get(projection.service_movie_id, [])
        identity_ok = len(identity_rows) == 1 and identity_rows[0]
        duplicate = projection_counts[projection.service_movie_id] > 1 or len(identity_rows) > 1
        result.append(CatalogProjection(projection.service_movie_id, projection.eligible and identity_ok and not duplicate, duplicate))
    return catalog_version, hashlib.sha256(catalog_bytes).hexdigest(), result


def _candidate_version_payload(
    *,
    catalog_version: str,
    mapping_payload_sha256: str,
    compatibility_id: str,
    movie_ids: list[str],
    candidate_set_version: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": CANDIDATE_SCHEMA_VERSION,
        "candidateSetVersion": candidate_set_version,
        "catalogVersion": catalog_version,
        "mappingPayloadSha256": mapping_payload_sha256,
        "compatibilityId": compatibility_id,
        "producerPolicy": PRODUCER_POLICY,
        "movieIds": movie_ids,
    }


def validate_candidate_payload(payload_bytes: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ArtifactValidationError("candidate artifact is not valid UTF-8 JSON") from error
    required = {
        "schemaVersion",
        "candidateSetVersion",
        "catalogVersion",
        "mappingPayloadSha256",
        "compatibilityId",
        "producerPolicy",
        "movieIds",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise ArtifactValidationError("candidate artifact fields differ from schema v1")
    if payload["schemaVersion"] != 1 or payload["producerPolicy"] != PRODUCER_POLICY:
        raise ArtifactCompatibilityError("candidate schema or producer policy is unsupported")
    for key in ("catalogVersion", "compatibilityId"):
        if not isinstance(payload[key], str) or not payload[key].strip():
            raise ArtifactValidationError(f"candidate {key} must not be blank")
    if not isinstance(payload["mappingPayloadSha256"], str) or not _SHA256.fullmatch(
        payload["mappingPayloadSha256"]
    ):
        raise ArtifactValidationError("candidate mappingPayloadSha256 is invalid")
    movie_ids = payload["movieIds"]
    if not isinstance(movie_ids, list):
        raise ArtifactValidationError("candidate movieIds must be an array")
    canonical_ids = sorted(_service_uuid(item) for item in movie_ids)
    if movie_ids != canonical_ids or len(movie_ids) != len(set(movie_ids)):
        raise ArtifactValidationError("candidate movieIds must be unique canonical UUIDs")
    version_seed = _candidate_version_payload(
        catalog_version=payload["catalogVersion"],
        mapping_payload_sha256=payload["mappingPayloadSha256"],
        compatibility_id=payload["compatibilityId"],
        movie_ids=movie_ids,
        candidate_set_version="",
    )
    expected_version = f"sha256:{hashlib.sha256(_canonical_json(version_seed)).hexdigest()}"
    if payload["candidateSetVersion"] != expected_version:
        raise ArtifactValidationError("candidateSetVersion does not match canonical payload")
    if payload_bytes != _canonical_json(payload):
        raise ArtifactValidationError("candidate artifact bytes are not canonical JSON")
    return payload


def build_candidate_artifacts(
    *,
    catalog_bytes: bytes,
    mapping: ItemIdMapping,
    mapping_metadata: ArtifactMetadata,
    active_serving_set: LoadedArtifactSet,
) -> tuple[bytes, bytes, CandidateExportResult]:
    mapping_metadata.require_kind(ArtifactKind.ITEM_ID_MAPPING)
    catalog_version, catalog_sha256, projections = _parse_catalog(catalog_bytes)
    compatibility = mapping_metadata.compatibility or {}
    if compatibility.get("catalog_version") != catalog_version:
        raise ArtifactCompatibilityError("Catalog version differs from mapping metadata")
    if compatibility.get("catalog_sha256") != catalog_sha256:
        raise ArtifactCompatibilityError("Catalog checksum differs from mapping metadata")
    active = active_serving_set.core
    if active.mapping_metadata.payload_sha256 != mapping_metadata.payload_sha256:
        raise ArtifactCompatibilityError("active serving mapping checksum differs")
    if active.mapping_metadata.compatibility_id != mapping_metadata.compatibility_id:
        raise ArtifactCompatibilityError("active serving mapping family differs")
    if active_serving_set.catalog_version != catalog_version:
        raise ArtifactCompatibilityError("active serving Catalog version differs")

    conflicting_service_ids = {
        item.service_movie_id
        for item in mapping.quarantined
        if item.service_movie_id is not None
    }
    reasons: Counter[str] = Counter()
    accepted: set[str] = set()
    for projection in projections:
        service_id = projection.service_movie_id
        if projection.duplicate:
            reasons["DUPLICATE"] += 1
            continue
        if not projection.eligible:
            reasons["NOT_UI_READY"] += 1
            continue
        item_id = mapping.by_service_id.get(service_id)
        if item_id is None:
            reasons[
                "MAPPING_CONFLICT" if service_id in conflicting_service_ids else "NOT_MAPPED"
            ] += 1
            continue
        if (
            item_id >= len(active.bias_model.item_counts)
            or active.bias_model.item_counts[item_id] <= 0
        ):
            reasons["MODEL_ITEM_MISSING"] += 1
            continue
        if service_id in accepted:
            reasons["DUPLICATE"] += 1
            continue
        accepted.add(service_id)

    if not accepted:
        raise ArtifactValidationError("candidate export has no accepted records")
    movie_ids = sorted(accepted)
    version_seed = _candidate_version_payload(
        catalog_version=catalog_version,
        mapping_payload_sha256=mapping_metadata.payload_sha256,
        compatibility_id=mapping_metadata.compatibility_id,
        movie_ids=movie_ids,
        candidate_set_version="",
    )
    candidate_set_version = f"sha256:{hashlib.sha256(_canonical_json(version_seed)).hexdigest()}"
    payload = _candidate_version_payload(
        catalog_version=catalog_version,
        mapping_payload_sha256=mapping_metadata.payload_sha256,
        compatibility_id=mapping_metadata.compatibility_id,
        movie_ids=movie_ids,
        candidate_set_version=candidate_set_version,
    )
    payload_bytes = _canonical_json(payload)
    payload_sha256 = hashlib.sha256(payload_bytes).hexdigest()
    reason_counts = {key: reasons.get(key, 0) for key in sorted(QUARANTINE_REASONS) if reasons.get(key, 0)}
    quarantined = sum(reason_counts.values())
    quarantine = {
        "schemaVersion": 1,
        "candidateSetVersion": candidate_set_version,
        "candidatePayloadSha256": payload_sha256,
        "catalogVersion": catalog_version,
        "producerPolicy": PRODUCER_POLICY,
        "sourceRecords": len(projections),
        "acceptedRecords": len(movie_ids),
        "quarantinedRecords": quarantined,
        "coverageScope": COVERAGE_SCOPE,
        "reasonCounts": reason_counts,
    }
    result = CandidateExportResult(
        catalog_version,
        catalog_sha256,
        candidate_set_version,
        payload_sha256,
        len(movie_ids),
        quarantined,
        reason_counts,
        False,
    )
    return payload_bytes, _canonical_json(quarantine), result


def export_candidate_artifacts(
    *,
    catalog_path: str | Path,
    mapping_payload_path: str | Path,
    mapping_metadata_path: str | Path,
    serving_manifest_path: str | Path,
    candidate_path: str | Path,
    quarantine_path: str | Path,
    store_dir: str | Path | None = None,
) -> CandidateExportResult:
    try:
        catalog_bytes = Path(catalog_path).read_bytes()
    except OSError as error:
        raise ArtifactValidationError("cannot read Catalog artifact") from error
    mapping_metadata = ArtifactMetadata.load(mapping_metadata_path)
    mapping = ItemIdMapping.load(mapping_payload_path, mapping_metadata)
    active = load_artifact_set(serving_manifest_path)
    payload_bytes, quarantine_bytes, result = build_candidate_artifacts(
        catalog_bytes=catalog_bytes,
        mapping=mapping,
        mapping_metadata=mapping_metadata,
        active_serving_set=active,
    )
    candidate_output = Path(candidate_path)
    quarantine_output = Path(quarantine_path)
    _atomic_write(candidate_output, payload_bytes)
    _atomic_write(quarantine_output, quarantine_bytes)
    published = False
    if store_dir is not None:
        LocalCandidateStore(store_dir).publish(
            candidate_output,
            quarantine_output,
            expected_payload_sha256=result.candidate_payload_sha256,
        )
        published = True
    return CandidateExportResult(
        result.catalog_version,
        result.catalog_sha256,
        result.candidate_set_version,
        result.candidate_payload_sha256,
        result.accepted_records,
        result.quarantined_records,
        result.reason_counts,
        published,
    )


class LocalCandidateStore:
    """Local immutable candidate versions with active and previous rollback pointers."""

    _lock = threading.Lock()

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.versions = self.root / "versions"
        self.quarantine = self.root / "quarantine"
        self.active_path = self.root / "active.json"
        self.rollback_path = self.root / "rollback.json"

    def _validate_quarantine(
        self, quarantine_bytes: bytes, candidate: dict[str, Any], payload_sha256: str
    ) -> dict[str, Any]:
        try:
            report = json.loads(quarantine_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ArtifactValidationError("candidate quarantine is invalid") from error
        required = {
            "schemaVersion", "candidateSetVersion", "candidatePayloadSha256",
            "catalogVersion", "producerPolicy", "sourceRecords", "acceptedRecords",
            "quarantinedRecords", "coverageScope", "reasonCounts",
        }
        if not isinstance(report, dict) or set(report) != required:
            raise ArtifactValidationError("candidate quarantine fields differ from schema v1")
        if report["schemaVersion"] != 1 or report["producerPolicy"] != PRODUCER_POLICY:
            raise ArtifactCompatibilityError("candidate quarantine policy is unsupported")
        if report["candidateSetVersion"] != candidate["candidateSetVersion"]:
            raise ArtifactValidationError("candidate quarantine version differs")
        if report["candidatePayloadSha256"] != payload_sha256:
            raise ArtifactValidationError("candidate quarantine payload checksum differs")
        if report["catalogVersion"] != candidate["catalogVersion"]:
            raise ArtifactValidationError("candidate quarantine Catalog version differs")
        counts = report["reasonCounts"]
        if not isinstance(counts, dict) or not set(counts) <= QUARANTINE_REASONS:
            raise ArtifactValidationError("candidate quarantine reason allowlist differs")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts.values()):
            raise ArtifactValidationError("candidate quarantine reason counts are invalid")
        if report["acceptedRecords"] != len(candidate["movieIds"]):
            raise ArtifactValidationError("candidate quarantine accepted count differs")
        if report["quarantinedRecords"] != sum(counts.values()):
            raise ArtifactValidationError("candidate quarantine count differs")
        if report["sourceRecords"] != report["acceptedRecords"] + report["quarantinedRecords"]:
            raise ArtifactValidationError("candidate quarantine source count differs")
        if quarantine_bytes != _canonical_json(report):
            raise ArtifactValidationError("candidate quarantine bytes are not canonical JSON")
        return report

    def _pointer(self, candidate: dict[str, Any], payload_sha256: str) -> dict[str, Any]:
        digest = candidate["candidateSetVersion"].removeprefix("sha256:")
        return {
            "schemaVersion": 1,
            "candidateSetVersion": candidate["candidateSetVersion"],
            "payloadSha256": payload_sha256,
            "payload": f"versions/{digest}.json",
            "quarantine": f"quarantine/{digest}.json",
        }

    def _read_pointer(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        pointer = _read_json_object(path, "candidate store pointer")
        if set(pointer) != {
            "schemaVersion", "candidateSetVersion", "payloadSha256", "payload", "quarantine"
        } or pointer.get("schemaVersion") != 1:
            raise ArtifactValidationError("candidate store pointer is invalid")
        return pointer

    def publish(
        self,
        candidate_path: str | Path,
        quarantine_path: str | Path,
        *,
        expected_payload_sha256: str,
    ) -> dict[str, Any]:
        try:
            payload_bytes = Path(candidate_path).read_bytes()
            quarantine_bytes = Path(quarantine_path).read_bytes()
        except OSError as error:
            raise ArtifactValidationError("cannot read candidate publish inputs") from error
        payload_sha256 = hashlib.sha256(payload_bytes).hexdigest()
        if not _SHA256.fullmatch(expected_payload_sha256) or payload_sha256 != expected_payload_sha256:
            raise ArtifactValidationError("candidate publish checksum differs")
        candidate = validate_candidate_payload(payload_bytes)
        if not candidate["movieIds"]:
            raise ArtifactValidationError("candidate publish requires at least one movie")
        self._validate_quarantine(quarantine_bytes, candidate, payload_sha256)
        pointer = self._pointer(candidate, payload_sha256)
        digest = candidate["candidateSetVersion"].removeprefix("sha256:")
        version_path = self.versions / f"{digest}.json"
        quarantine_store_path = self.quarantine / f"{digest}.json"

        with self._lock:
            old_active = self._read_pointer(self.active_path)
            if version_path.exists() and version_path.read_bytes() != payload_bytes:
                raise ArtifactValidationError("immutable candidate version differs")
            if quarantine_store_path.exists() and quarantine_store_path.read_bytes() != quarantine_bytes:
                raise ArtifactValidationError("immutable candidate quarantine differs")
            if not version_path.exists():
                _atomic_write(version_path, payload_bytes)
            if not quarantine_store_path.exists():
                _atomic_write(quarantine_store_path, quarantine_bytes)
            if old_active == pointer:
                return pointer
            if old_active is not None:
                _atomic_write(self.rollback_path, _canonical_json(old_active))
            _atomic_write(self.active_path, _canonical_json(pointer))
        return pointer

    def active(self) -> dict[str, Any] | None:
        return self._read_pointer(self.active_path)

    def rollback(self) -> dict[str, Any] | None:
        return self._read_pointer(self.rollback_path)

    def load_active(self) -> dict[str, Any]:
        pointer = self._read_pointer(self.active_path)
        if pointer is None:
            raise ArtifactValidationError("candidate store has no active pointer")
        relative = Path(pointer["payload"])
        if relative.is_absolute() or ".." in relative.parts or relative.parts[:1] != ("versions",):
            raise ArtifactValidationError("candidate store payload path is invalid")
        payload_path = (self.root / relative).resolve()
        if self.root.resolve() not in payload_path.parents:
            raise ArtifactValidationError("candidate store payload path escapes store")
        try:
            payload_bytes = payload_path.read_bytes()
        except OSError as error:
            raise ArtifactValidationError("candidate active payload is unavailable") from error
        if hashlib.sha256(payload_bytes).hexdigest() != pointer["payloadSha256"]:
            raise ArtifactValidationError("candidate active payload checksum differs")
        payload = validate_candidate_payload(payload_bytes)
        if payload["candidateSetVersion"] != pointer["candidateSetVersion"]:
            raise ArtifactValidationError("candidate active pointer version differs")
        return payload
