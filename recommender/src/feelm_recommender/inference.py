from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np

from .mapping import MappingQuarantine
from .service import RecommendationCore


@dataclass(frozen=True, slots=True, order=True)
class InferenceQuarantine:
    scope: str
    reason: str
    service_movie_id: str


@dataclass(frozen=True, slots=True)
class RankedMovie:
    service_movie_id: str
    popularity_score: float
    rank: int


@dataclass(frozen=True, slots=True)
class StarEstimate:
    service_movie_id: str
    stars: float
    direct_fold_in: bool


@dataclass(frozen=True, slots=True)
class OfflineInferenceResult:
    mapping_version: str
    policy_version: str
    model_versions: tuple[str, ...]
    ranking_policy: str
    ranking_alpha: float
    star_candidate_enabled: bool
    ranked_movies: tuple[RankedMovie, ...]
    star_estimates: tuple[StarEstimate, ...]
    request_quarantine: tuple[InferenceQuarantine, ...]
    mapping_quarantine: tuple[MappingQuarantine, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "mapping_version": self.mapping_version,
            "policy_version": self.policy_version,
            "model_versions": list(self.model_versions),
            "ranking_policy": self.ranking_policy,
            "ranking_alpha": self.ranking_alpha,
            "star_candidate_enabled": self.star_candidate_enabled,
            "ranked_movies": [
                {
                    "service_movie_id": movie.service_movie_id,
                    "popularity_score": movie.popularity_score,
                    "rank": movie.rank,
                }
                for movie in self.ranked_movies
            ],
            "star_estimates": [
                {
                    "service_movie_id": estimate.service_movie_id,
                    "stars": estimate.stars,
                    "direct_fold_in": estimate.direct_fold_in,
                }
                for estimate in self.star_estimates
            ],
            "request_quarantine": [
                {
                    "scope": item.scope,
                    "reason": item.reason,
                    "service_movie_id": item.service_movie_id,
                }
                for item in self.request_quarantine
            ],
            "mapping_quarantine": [
                {
                    "reason": item.reason,
                    "movielens_item_id": item.movielens_item_id,
                    "service_movie_id": item.service_movie_id,
                }
                for item in self.mapping_quarantine
            ],
        }


class OfflineInferencePipeline:
    """Deterministic service-UUID boundary around the MovieLens-indexed core."""

    def __init__(self, core: RecommendationCore) -> None:
        self.core = core

    def run(
        self,
        *,
        candidate_movie_ids: Iterable[str],
        onboarding: Sequence[tuple[str, float]] = (),
        k: int = 0,
        enable_candidate_stars: bool = False,
    ) -> OfflineInferenceResult:
        candidates, candidate_quarantine = self._resolve_candidates(candidate_movie_ids)
        candidate_service_ids = [service_id for service_id, _ in candidates]
        candidate_item_ids = np.asarray([item_id for _, item_id in candidates], dtype=np.int64)
        ranking = self.core.rank(candidate_item_ids)
        by_item_id = {item_id: service_id for service_id, item_id in candidates}
        score_by_item_id = {
            int(item_id): float(score)
            for item_id, score in zip(ranking.item_ids, ranking.scores, strict=True)
        }
        # HTTP/service determinism is defined in the service UUID space. The core's
        # MovieLens-ID tie break cannot leak across that boundary.
        service_order = sorted(
            candidates,
            key=lambda row: (-score_by_item_id[row[1]], row[0]),
        )
        ranked_movies = tuple(
            RankedMovie(
                service_movie_id=service_id,
                popularity_score=score_by_item_id[item_id],
                rank=rank,
            )
            for rank, (service_id, item_id) in enumerate(service_order, start=1)
        )

        request_quarantine = list(candidate_quarantine)
        star_estimates: tuple[StarEstimate, ...] = ()
        if enable_candidate_stars:
            onboarding_rows, onboarding_quarantine = self._resolve_onboarding(onboarding)
            request_quarantine.extend(onboarding_quarantine)
            if len(onboarding) != k or len(onboarding_rows) != k:
                request_quarantine.append(
                    InferenceQuarantine(
                        "STAR_HEAD", "VALIDATED_K_INPUT_NOT_AVAILABLE", str(k)
                    )
                )
            elif candidates:
                estimate = self.core.estimate_stars(
                    target_item_ids=candidate_item_ids,
                    onboarding_item_ids=[item_id for _, item_id, _ in onboarding_rows],
                    onboarding_ratings=[rating for _, _, rating in onboarding_rows],
                    k=k,
                )
                star_estimates = tuple(
                    StarEstimate(service_id, float(stars), bool(direct))
                    for service_id, stars, direct in zip(
                        candidate_service_ids,
                        estimate.stars,
                        estimate.direct_fold_in,
                        strict=True,
                    )
                )

        metadata = (
            self.core.bias_metadata,
            self.core.factor_metadata,
            self.core.calibrator_metadata,
            self.core.mapping_metadata,
        )
        return OfflineInferenceResult(
            mapping_version=self.core.item_mapping.mapping_version,
            policy_version=self.core.policy.version,
            model_versions=tuple(item.model_version for item in metadata),
            ranking_policy=ranking.ranking_policy,
            ranking_alpha=ranking.fold_in_alpha,
            star_candidate_enabled=enable_candidate_stars,
            ranked_movies=ranked_movies,
            star_estimates=star_estimates,
            request_quarantine=tuple(sorted(set(request_quarantine))),
            mapping_quarantine=self.core.item_mapping.quarantined,
        )

    def _resolve_candidates(
        self, service_movie_ids: Iterable[str]
    ) -> tuple[list[tuple[str, int]], tuple[InferenceQuarantine, ...]]:
        resolved, missing = self.core.item_mapping.resolve_service_ids(service_movie_ids)
        quarantine = [
            InferenceQuarantine("CANDIDATE", item.reason, item.service_movie_id or "")
            for item in missing
        ]
        accepted: dict[str, int] = {}
        for service_id, item_id in resolved:
            if service_id in accepted:
                quarantine.append(
                    InferenceQuarantine("CANDIDATE", "DUPLICATE_SERVICE_ID", service_id)
                )
                continue
            if item_id >= len(self.core.bias_model.item_counts) or self.core.bias_model.item_counts[item_id] <= 0:
                quarantine.append(
                    InferenceQuarantine("CANDIDATE", "MODEL_ITEM_NOT_AVAILABLE", service_id)
                )
                continue
            accepted[service_id] = item_id
        return sorted(accepted.items()), tuple(sorted(set(quarantine)))

    def _resolve_onboarding(
        self, onboarding: Sequence[tuple[str, float]]
    ) -> tuple[list[tuple[str, int, float]], tuple[InferenceQuarantine, ...]]:
        resolved: list[tuple[str, int, float]] = []
        quarantine: list[InferenceQuarantine] = []
        seen: set[str] = set()
        for raw_service_id, rating in onboarding:
            rows, missing = self.core.item_mapping.resolve_service_ids([raw_service_id])
            if missing:
                quarantine.extend(
                    InferenceQuarantine("ONBOARDING", item.reason, item.service_movie_id or "")
                    for item in missing
                )
                continue
            service_id, item_id = rows[0]
            if service_id in seen:
                quarantine.append(
                    InferenceQuarantine("ONBOARDING", "DUPLICATE_SERVICE_ID", service_id)
                )
                continue
            seen.add(service_id)
            resolved.append((service_id, item_id, float(rating)))
        return resolved, tuple(sorted(set(quarantine)))
