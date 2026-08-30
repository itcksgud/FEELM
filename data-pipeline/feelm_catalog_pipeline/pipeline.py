from __future__ import annotations

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .artifact import ArtifactWriter, QualityCollector, make_header, write_quality_report
from .identity import IdentityEntry, IdentityMap
from .movielens import MovieLensMovie, archive_sha256, read_movielens_archive
from .normalization import NormalizedMovie, normalize_movie
from .tmdb import TmdbGateway


@dataclass(frozen=True)
class PipelineConfig:
    archive: Path
    output: Path
    quality_report: Path
    catalog_version: str
    identity_map_input: Path | None = None
    identity_map_output: Path | None = None
    previous_quality_report: Path | None = None
    generated_at: datetime | None = None
    workers: int = 4
    limit: int = 0
    title_similarity_threshold: float = 0.65


@dataclass(frozen=True)
class PipelineResult:
    artifact_path: Path
    quality_report_path: Path
    identity_map_path: Path
    input_count: int
    artifact_sha256: str
    all_publish_gates_passed: bool


class CatalogPipeline:
    def __init__(
        self,
        gateway: TmdbGateway,
        *,
        movie_uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
        snapshot_uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    ) -> None:
        self._gateway = gateway
        self._movie_uuid_factory = movie_uuid_factory
        self._snapshot_uuid_factory = snapshot_uuid_factory

    def run(self, config: PipelineConfig) -> PipelineResult:
        if config.workers < 1:
            raise ValueError("workers must be at least one")
        if not 0 <= config.title_similarity_threshold <= 1:
            raise ValueError("title similarity threshold must be between zero and one")
        now = config.generated_at or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("generated_at must be timezone-aware")
        movies = read_movielens_archive(config.archive)
        if config.limit > 0:
            movies = movies[: config.limit]
        checksum = archive_sha256(config.archive)
        identity_map = IdentityMap.load(config.identity_map_input)
        identities = [
            identity_map.resolve(movie, now, self._movie_uuid_factory) for movie in movies
        ]
        identity_output = config.identity_map_output or config.output.with_suffix(".identity-map.json")
        header = make_header(
            catalog_version=config.catalog_version,
            generated_at=now,
            movielens_checksum=checksum,
            archive_name=config.archive.name,
        )
        writer = ArtifactWriter(config.output, header)
        quality = QualityCollector()
        seen_providers: set[int] = set()

        def process(pair: tuple[MovieLensMovie, IdentityEntry]) -> NormalizedMovie:
            movie, identity = pair
            return normalize_movie(
                movie,
                identity,
                self._gateway,
                now,
                title_threshold=config.title_similarity_threshold,
                snapshot_uuid_factory=self._snapshot_uuid_factory,
            )

        try:
            with ThreadPoolExecutor(max_workers=config.workers) as executor:
                for outcome in executor.map(process, zip(movies, identities)):
                    if outcome.resolved_tmdb_id is not None:
                        identity_map.attach(outcome.identity, "TMDB", outcome.resolved_tmdb_id)
                    quality.observe_outcome(
                        outcome.identity_status,
                        outcome.visibility_status,
                        outcome.safe_errors,
                        outcome.recovered,
                    )
                    for item in outcome.records:
                        if item["recordType"] == "provider":
                            provider_id = int(item["payload"]["tmdbProviderId"])
                            if provider_id in seen_providers:
                                continue
                            seen_providers.add(provider_id)
                        writer.write(item)
                        quality.observe_record(item)
            artifact_sha = writer.finish()
        except BaseException:
            writer.abort()
            raise

        previous_report = None
        if config.previous_quality_report and config.previous_quality_report.exists():
            previous_report = json.loads(config.previous_quality_report.read_text(encoding="utf-8"))
        report = quality.build_report(
            catalog_version=config.catalog_version,
            generated_at=now,
            movielens_checksum=checksum,
            artifact_sha256=artifact_sha,
            input_count=len(movies),
            previous_report=previous_report,
        )
        write_quality_report(config.quality_report, report)
        identity_map.write(identity_output, now)
        return PipelineResult(
            artifact_path=config.output,
            quality_report_path=config.quality_report,
            identity_map_path=identity_output,
            input_count=len(movies),
            artifact_sha256=artifact_sha,
            all_publish_gates_passed=bool(report["allPublishGatesPassed"]),
        )
