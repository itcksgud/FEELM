from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .errors import ArtifactCompatibilityError, ArtifactValidationError
from .metadata import ArtifactKind, ArtifactMetadata


@dataclass(frozen=True, slots=True, order=True)
class MappingQuarantine:
    reason: str
    movielens_item_id: int | None
    service_movie_id: str | None


@dataclass(frozen=True, slots=True)
class ItemIdMapping:
    mapping_version: str
    source_id_space: str
    target_id_space: str
    by_service_id: dict[str, int]
    by_movielens_id: dict[int, str]
    quarantined: tuple[MappingQuarantine, ...]

    def __post_init__(self) -> None:
        if not self.mapping_version.strip():
            raise ArtifactValidationError("mapping_version must not be blank")
        if not self.source_id_space.strip() or not self.target_id_space.strip():
            raise ArtifactValidationError("mapping ID spaces must not be blank")
        if not self.by_service_id or not self.by_movielens_id:
            raise ArtifactValidationError("item mapping must contain accepted records")
        if any(source <= 0 for source in self.by_movielens_id):
            raise ArtifactValidationError("MovieLens item IDs must be positive")
        expected_inverse = {
            source: service for service, source in self.by_service_id.items()
        }
        if expected_inverse != self.by_movielens_id:
            raise ArtifactValidationError("item mapping indexes are not exact inverses")

    @classmethod
    def load(
        cls, payload_path: str | Path, metadata: ArtifactMetadata
    ) -> "ItemIdMapping":
        metadata.require_kind(ArtifactKind.ITEM_ID_MAPPING)
        metadata.verify_payload(payload_path)
        try:
            root = json.loads(Path(payload_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ArtifactValidationError(f"cannot load item mapping payload: {error}") from error
        if not isinstance(root, dict) or root.get("schema_version") != 1:
            raise ArtifactCompatibilityError("item mapping payload schema_version must be 1")
        compatibility = metadata.compatibility or {}
        header = {
            "mapping_version": root.get("mapping_version"),
            "source_id_space": root.get("source_id_space"),
            "target_id_space": root.get("target_id_space"),
        }
        for key, value in header.items():
            if not isinstance(value, str) or not value.strip():
                raise ArtifactValidationError(f"item mapping {key} must not be blank")
            if compatibility.get(key) != value:
                raise ArtifactCompatibilityError(
                    f"item mapping metadata {key} does not match payload"
                )
        records = root.get("records")
        if not isinstance(records, list) or not records:
            raise ArtifactValidationError("item mapping records must be a non-empty array")
        parsed = [cls._parse_record(record, index + 1) for index, record in enumerate(records)]
        source_targets: dict[int, set[str]] = {}
        target_sources: dict[str, set[int]] = {}
        for source, target in parsed:
            source_targets.setdefault(source, set()).add(target)
            target_sources.setdefault(target, set()).add(source)

        conflicting_sources = {
            source for source, targets in source_targets.items() if len(targets) > 1
        }
        conflicting_targets = {
            target for target, sources in target_sources.items() if len(sources) > 1
        }
        quarantined: set[MappingQuarantine] = set()
        accepted: set[tuple[int, str]] = set()
        for source, target in parsed:
            if source in conflicting_sources:
                quarantined.add(MappingQuarantine("SOURCE_ID_CONFLICT", source, target))
            elif target in conflicting_targets:
                quarantined.add(MappingQuarantine("SERVICE_ID_CONFLICT", source, target))
            else:
                accepted.add((source, target))
        if not accepted:
            raise ArtifactValidationError("item mapping has no non-conflicting records")
        return cls(
            mapping_version=header["mapping_version"],
            source_id_space=header["source_id_space"],
            target_id_space=header["target_id_space"],
            by_service_id={target: source for source, target in sorted(accepted)},
            by_movielens_id={source: target for source, target in sorted(accepted)},
            quarantined=tuple(sorted(quarantined)),
        )

    @staticmethod
    def _parse_record(value: Any, line_number: int) -> tuple[int, str]:
        if not isinstance(value, dict) or set(value) != {
            "movielens_item_id", "service_movie_id"
        }:
            raise ArtifactValidationError(
                f"item mapping record {line_number} must contain only movielens_item_id and service_movie_id"
            )
        source = value["movielens_item_id"]
        if isinstance(source, bool) or not isinstance(source, int) or source <= 0:
            raise ArtifactValidationError(
                f"item mapping record {line_number} has an invalid MovieLens item ID"
            )
        try:
            target = str(uuid.UUID(str(value["service_movie_id"])))
        except (ValueError, AttributeError) as error:
            raise ArtifactValidationError(
                f"item mapping record {line_number} has an invalid service UUID"
            ) from error
        return source, target

    def resolve_service_ids(
        self, service_movie_ids: Iterable[str]
    ) -> tuple[list[tuple[str, int]], tuple[MappingQuarantine, ...]]:
        resolved: list[tuple[str, int]] = []
        missing: set[MappingQuarantine] = set()
        for raw in service_movie_ids:
            try:
                service_id = str(uuid.UUID(str(raw)))
            except (ValueError, AttributeError):
                missing.add(MappingQuarantine("INVALID_SERVICE_ID", None, str(raw)))
                continue
            source = self.by_service_id.get(service_id)
            if source is None:
                missing.add(MappingQuarantine("SERVICE_ID_NOT_MAPPED", None, service_id))
            else:
                resolved.append((service_id, source))
        return resolved, tuple(sorted(missing))
