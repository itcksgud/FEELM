from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


ALLOWED_RECORD_TYPES = {
    "movieIdentity",
    "movieProjection",
    "localization",
    "genre",
    "country",
    "credit",
    "provider",
    "availabilitySnapshot",
    "ottOffer",
}


def make_header(
    *, catalog_version: str, generated_at: datetime, movielens_checksum: str, archive_name: str
) -> dict[str, Any]:
    return {
        "recordType": "artifactHeader",
        "schemaVersion": 1,
        "catalogVersion": catalog_version,
        "generatedAt": generated_at.isoformat(),
        "sourceChecksums": {"movielensArchiveSha256": movielens_checksum},
        "sources": [
            {
                "name": "MOVIELENS_32M",
                "archiveName": archive_name,
                "sha256": movielens_checksum,
            },
            {"name": "TMDB_API", "apiVersion": "3", "region": "KR"},
        ],
    }


def validate_record(item: dict[str, Any]) -> None:
    record_type = item.get("recordType")
    if record_type not in ALLOWED_RECORD_TYPES:
        raise ValueError(f"unknown artifact recordType: {record_type}")
    if not isinstance(item.get("payload"), dict):
        raise ValueError(f"{record_type} payload must be an object")


class ArtifactWriter:
    def __init__(self, output: Path, header: dict[str, Any]) -> None:
        self.output = output
        self._temporary = output.with_suffix(output.suffix + ".tmp")
        output.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self._temporary.open("w", encoding="utf-8", newline="\n")
        self._stream.write(json.dumps(header, ensure_ascii=False, separators=(",", ":")) + "\n")

    def write(self, item: dict[str, Any]) -> None:
        validate_record(item)
        self._stream.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")

    def finish(self) -> str:
        self._stream.flush()
        self._stream.close()
        self._temporary.replace(self.output)
        digest = hashlib.sha256()
        with self.output.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def abort(self) -> None:
        if not self._stream.closed:
            self._stream.close()
        if self._temporary.exists():
            self._temporary.unlink()


class QualityCollector:
    def __init__(self) -> None:
        self.record_counts: Counter[str] = Counter()
        self.identity_statuses: Counter[str] = Counter()
        self.visibility_statuses: Counter[str] = Counter()
        self.safe_errors: Counter[str] = Counter()
        self.recovered = 0
        self._external_owners: dict[tuple[str, str], str] = {}
        self._external_conflicts: list[str] = []
        self._identities: set[str] = set()
        self._projections: dict[str, dict[str, Any]] = {}
        self._localizations: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._genres: Counter[str] = Counter()
        self._directors: Counter[str] = Counter()
        self._providers: set[int] = set()
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._offers: Counter[str] = Counter()
        self._offer_provider_refs: list[tuple[str, int]] = []

    def observe_outcome(
        self, identity_status: str, visibility_status: str | None, errors: tuple[str, ...], recovered: bool
    ) -> None:
        self.identity_statuses[identity_status] += 1
        if visibility_status:
            self.visibility_statuses[visibility_status] += 1
        self.safe_errors.update(errors)
        self.recovered += int(recovered)

    def observe_record(self, item: dict[str, Any]) -> None:
        record_type = str(item["recordType"])
        payload = item["payload"]
        self.record_counts[record_type] += 1
        movie_id = str(payload.get("movieId", ""))
        if record_type == "movieIdentity":
            self._identities.add(movie_id)
            for external in payload.get("externalIds") or []:
                key = (str(external["source"]), str(external["externalId"]))
                owner = self._external_owners.get(key)
                if owner and owner != movie_id:
                    self._external_conflicts.append(f"{key[0]}:{key[1]}")
                self._external_owners[key] = movie_id
        elif record_type == "movieProjection":
            self._projections[movie_id] = payload
        elif record_type == "localization":
            self._localizations[movie_id].append(payload)
        elif record_type == "genre":
            self._genres[movie_id] += 1
        elif record_type == "credit" and payload.get("creditType") == "DIRECTOR":
            self._directors[movie_id] += 1
        elif record_type == "provider":
            self._providers.add(int(payload["tmdbProviderId"]))
        elif record_type == "availabilitySnapshot":
            self._snapshots[str(payload["snapshotId"])] = payload
        elif record_type == "ottOffer":
            snapshot_id = str(payload["snapshotId"])
            self._offers[snapshot_id] += 1
            self._offer_provider_refs.append((snapshot_id, int(payload["tmdbProviderId"])))

    def build_report(
        self,
        *,
        catalog_version: str,
        generated_at: datetime,
        movielens_checksum: str,
        artifact_sha256: str,
        input_count: int,
        previous_report: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        missing_required: list[str] = []
        invalid_ui_ready: list[str] = []
        tv_exposure: list[str] = []
        orphan_relations: list[str] = []
        for movie_id, projection in self._projections.items():
            if projection.get("mediaType") != "MOVIE":
                tv_exposure.append(movie_id)
            localizations = self._localizations.get(movie_id, [])
            has_title = any(item.get("title") for item in localizations) or projection.get("originalTitle")
            has_overview = any(item.get("overview") for item in localizations)
            visible = projection.get("visibilityStatus") in {"CATALOG_VISIBLE", "UI_READY"}
            if visible and (not has_title or not has_overview or self._genres[movie_id] < 1):
                missing_required.append(movie_id)
            if projection.get("visibilityStatus") == "UI_READY" and (
                not projection.get("posterPath")
                or not projection.get("runtimeMinutes")
                or self._directors[movie_id] < 1
            ):
                invalid_ui_ready.append(movie_id)
        relation_movie_ids = set(self._localizations) | set(self._genres) | set(self._directors)
        orphan_relations.extend(sorted(relation_movie_ids - set(self._projections)))
        snapshot_violations: list[str] = []
        for snapshot_id, snapshot in self._snapshots.items():
            offer_count = self._offers[snapshot_id]
            status = snapshot.get("fetchStatus")
            if (status == "SUCCESS_EMPTY" and offer_count != 0) or (
                status == "SUCCESS_LISTED" and offer_count < 1
            ) or (status == "FAILED" and offer_count != 0):
                snapshot_violations.append(snapshot_id)
        for snapshot_id, provider_id in self._offer_provider_refs:
            if snapshot_id not in self._snapshots or provider_id not in self._providers:
                orphan_relations.append(f"offer:{snapshot_id}:{provider_id}")
        gates = {
            "externalIdUniqueness": {
                "passed": not self._external_conflicts,
                "violationCount": len(self._external_conflicts),
                "examples": self._external_conflicts[:10],
            },
            "tvExposure": {
                "passed": not tv_exposure,
                "violationCount": len(tv_exposure),
                "examples": tv_exposure[:10],
            },
            "requiredProjection": {
                "passed": not missing_required,
                "violationCount": len(missing_required),
                "examples": missing_required[:10],
            },
            "uiReadyValidity": {
                "passed": not invalid_ui_ready,
                "violationCount": len(invalid_ui_ready),
                "examples": invalid_ui_ready[:10],
            },
            "relationIntegrity": {
                "passed": not orphan_relations,
                "violationCount": len(orphan_relations),
                "examples": orphan_relations[:10],
            },
            "snapshotConsistency": {
                "passed": not snapshot_violations,
                "violationCount": len(snapshot_violations),
                "examples": snapshot_violations[:10],
            },
        }
        current_summary = {
            "inputMovieCount": input_count,
            "identityStatuses": dict(sorted(self.identity_statuses.items())),
            "visibilityStatuses": dict(sorted(self.visibility_statuses.items())),
            "recordCounts": dict(sorted(self.record_counts.items())),
            "recoveredIdentityCount": self.recovered,
            "safeErrorCounts": dict(sorted(self.safe_errors.items())),
        }
        previous_summary = (previous_report or {}).get("summary") or {}
        regression = {
            key: {
                "previous": previous_summary.get("visibilityStatuses", {}).get(key, 0),
                "current": self.visibility_statuses.get(key, 0),
                "delta": self.visibility_statuses.get(key, 0)
                - int(previous_summary.get("visibilityStatuses", {}).get(key, 0)),
            }
            for key in ("CATALOG_VISIBLE", "UI_READY", "UI_INCOMPLETE")
        }
        return {
            "schemaVersion": 1,
            "catalogVersion": catalog_version,
            "generatedAt": generated_at.isoformat(),
            "sourceChecksums": {"movielensArchiveSha256": movielens_checksum},
            "artifactSha256": artifact_sha256,
            "summary": current_summary,
            "gates": gates,
            "allPublishGatesPassed": all(item["passed"] for item in gates.values()),
            "regression": regression,
        }


def write_quality_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)

