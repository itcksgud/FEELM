from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import threading
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from .artifact_set import LoadedArtifactSet, load_artifact_set
from .errors import (
    ArtifactCompatibilityError,
    ArtifactValidationError,
    CandidateNotEnabledError,
)
from .inference import OfflineInferencePipeline
from .interpretation import (
    EXPERIMENT_VERSION,
    K_SELECTION_POLICY_VERSION,
    UTILITY_POLICY_VERSION,
    LIMITATIONS,
    InterpretationInputError,
    InterpretationRating,
    interpret_recommendations,
)
from .metadata import ArtifactKind


LOCAL_FAKE_SERVICE_TOKEN = "test-c2-service-token"
LOCAL_FAKE_FORBIDDEN_TOKEN = "test-c2-forbidden-token"
ZERO_REQUEST_ID = "00000000-0000-0000-0000-000000000000"
SAFE_LOAD_FAILURE = "ARTIFACT_COMPATIBILITY_FAILURE"
logger = logging.getLogger("feelm_recommender.serving")


def _safe_error(request_id: str, code: str, message: str, retriable: bool, status: int):
    return JSONResponse(
        status_code=status,
        content={
            "requestId": request_id,
            "code": code,
            "message": message,
            "retriable": retriable,
        },
    )


def _safe_request_id(raw: str | None) -> str:
    try:
        return str(uuid.UUID(str(raw)))
    except (ValueError, AttributeError):
        return ZERO_REQUEST_ID


class RatingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    movieId: uuid.UUID
    value: StrictInt = Field(ge=1, le=5)
    revision: StrictInt = Field(ge=1)


class PreferenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inputVersion: str = Field(min_length=1, max_length=128)
    ratings: list[RatingInput]

    @field_validator("ratings")
    @classmethod
    def unique_ratings(cls, values: list[RatingInput]) -> list[RatingInput]:
        movie_ids = [item.movieId for item in values]
        if len(movie_ids) != len(set(movie_ids)):
            raise ValueError("ratings must contain at most one active row per movie")
        return values


class CandidateSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidateSetVersion: str = Field(min_length=1, max_length=128)
    movieIds: list[uuid.UUID] = Field(min_length=1)

    @field_validator("movieIds")
    @classmethod
    def unique_movies(cls, values: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(values) != len(set(values)):
            raise ValueError("candidate movie IDs must be unique")
        return values


class RecommendationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestId: uuid.UUID
    candidateSet: CandidateSet
    preferenceInput: PreferenceInput
    starPolicy: Literal["DISABLED", "REC_EV_003B_CANDIDATE"]


class RecommendationInterpretationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestId: uuid.UUID
    candidateSet: CandidateSet
    preferenceInput: PreferenceInput


@dataclass(frozen=True, slots=True)
class RegistrySnapshot:
    artifact_set: LoadedArtifactSet | None
    failure_code: str | None


class ArtifactRegistry:
    """Validate outside the lock, then atomically swap one complete artifact set."""

    def __init__(self, *, enable_candidate: bool = False) -> None:
        self._lock = threading.Lock()
        self._active: LoadedArtifactSet | None = None
        self._failure_code: str | None = None
        self._enable_candidate = enable_candidate

    def reload(self, manifest_path: str | Path) -> bool:
        try:
            candidate = load_artifact_set(
                manifest_path, enable_candidate=self._enable_candidate
            )
        except (ArtifactCompatibilityError, ArtifactValidationError, OSError, ValueError):
            with self._lock:
                self._failure_code = SAFE_LOAD_FAILURE
            logger.warning("artifact_reload status=FAIL reason_code=%s", SAFE_LOAD_FAILURE)
            return False
        with self._lock:
            self._active = candidate
            self._failure_code = None
        logger.info(
            "artifact_reload status=PASS artifact_set_version=%s",
            candidate.artifact_set_version,
        )
        return True

    def snapshot(self) -> RegistrySnapshot:
        with self._lock:
            return RegistrySnapshot(self._active, self._failure_code)


bearer = HTTPBearer(auto_error=False)


def require_local_fake_auth(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> None:
    if request.app.state.auth_mode != "fake":
        raise LocalAuthError
    if (
        credentials is not None
        and credentials.scheme.lower() == "bearer"
        and secrets.compare_digest(credentials.credentials, LOCAL_FAKE_FORBIDDEN_TOKEN)
    ):
        raise LocalForbiddenError
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not secrets.compare_digest(credentials.credentials, LOCAL_FAKE_SERVICE_TOKEN)
    ):
        raise LocalAuthError


class LocalAuthError(Exception):
    pass


class LocalForbiddenError(Exception):
    pass


def _canonical_recommendation_version(
    payload: RecommendationRequest, artifact_set_version: str
) -> str:
    value = {
        "artifactSetVersion": artifact_set_version,
        "candidateSet": {
            "candidateSetVersion": payload.candidateSet.candidateSetVersion,
            "movieIds": sorted(str(item) for item in payload.candidateSet.movieIds),
        },
        "preferenceInput": {
            "inputVersion": payload.preferenceInput.inputVersion,
            "ratings": sorted(
                (
                    {"movieId": str(item.movieId), "revision": item.revision, "value": item.value}
                    for item in payload.preferenceInput.ratings
                ),
                key=lambda item: (item["movieId"], item["revision"], item["value"]),
            ),
        },
        "starPolicy": payload.starPolicy,
    }
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"recommendation-v1-{hashlib.sha256(encoded).hexdigest()}"


def _expected_star() -> dict[str, object]:
    return {
        "status": "NOT_COMPUTED",
        "value": None,
        "displayEligible": False,
        "confidence": "NOT_EVALUATED",
        "confidencePolicyVersion": None,
    }


def _snapshot(artifact_set: LoadedArtifactSet, payload: RecommendationRequest) -> dict[str, Any]:
    core = artifact_set.core
    metadata = {
        "bias": core.bias_metadata,
        "factors": core.factor_metadata,
        "calibration": core.calibrator_metadata,
        "mapping": core.mapping_metadata,
    }
    return {
        "recommendationVersion": _canonical_recommendation_version(
            payload, artifact_set.artifact_set_version
        ),
        "artifactSetVersion": artifact_set.artifact_set_version,
        "compatibilityId": core.bias_metadata.compatibility_id,
        "policyVersion": core.policy.version,
        "rankingPolicy": core.policy.ranking_policy,
        "rankingAlpha": core.policy.ranking_alpha,
        "mappingVersion": core.item_mapping.mapping_version,
        "catalogVersion": artifact_set.catalog_version,
        "candidateSetVersion": payload.candidateSet.candidateSetVersion,
        "inputVersion": payload.preferenceInput.inputVersion,
        "modelVersions": {key: value.model_version for key, value in metadata.items()},
        "payloadChecksums": {key: value.payload_sha256 for key, value in metadata.items()},
    }


def create_app(
    *,
    artifact_manifest: str | Path | None = None,
    registry: ArtifactRegistry | None = None,
    auth_mode: str | None = None,
    c6_local_experiment_enabled: bool | None = None,
) -> FastAPI:
    experiment_enabled = (
        c6_local_experiment_enabled
        if c6_local_experiment_enabled is not None
        else os.getenv("C6_LOCAL_EXPERIMENT_ENABLED", "").strip().lower() == "true"
    )
    artifact_registry = registry or ArtifactRegistry(enable_candidate=experiment_enabled)
    configured_manifest = artifact_manifest or os.getenv("C2_ARTIFACT_SET_MANIFEST")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if configured_manifest:
            artifact_registry.reload(configured_manifest)
        yield

    app = FastAPI(
        title="FEELM C2A Internal Recommendation Serving",
        version="0.1.0-c2a",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.artifact_registry = artifact_registry
    app.state.auth_mode = auth_mode if auth_mode is not None else os.getenv("C2_AUTH_MODE", "")

    @app.exception_handler(LocalAuthError)
    async def auth_error(request: Request, _: LocalAuthError):
        return _safe_error(
            _safe_request_id(request.headers.get("X-Request-Id")),
            "INTERNAL_AUTH_REQUIRED",
            "Internal service authentication is required.",
            False,
            401,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, _: RequestValidationError):
        return _safe_error(
            _safe_request_id(request.headers.get("X-Request-Id")),
            "INVALID_RECOMMENDATION_REQUEST",
            "Recommendation request validation failed.",
            False,
            422,
        )

    @app.exception_handler(LocalForbiddenError)
    async def forbidden_error(request: Request, _: LocalForbiddenError):
        return _safe_error(
            _safe_request_id(request.headers.get("X-Request-Id")),
            "INTERNAL_AUTH_FORBIDDEN",
            "Internal service principal is not authorized.",
            False,
            403,
        )

    @app.get("/internal/health/live", dependencies=[Depends(require_local_fake_auth)])
    async def liveness():
        return {"status": "LIVE"}

    @app.get("/internal/health/ready", dependencies=[Depends(require_local_fake_auth)])
    async def readiness():
        current = artifact_registry.snapshot()
        if current.artifact_set is None:
            checks = [
                {"artifactKind": kind.value, "status": "FAIL", "reasonCode": current.failure_code or "ARTIFACT_SET_NOT_LOADED"}
                for kind in (
                    ArtifactKind.BIAS,
                    ArtifactKind.ALS_ITEM_FACTORS,
                    ArtifactKind.HEAD_CALIBRATION_BUNDLE,
                    ArtifactKind.ITEM_ID_MAPPING,
                )
            ]
            checks.append({"artifactKind": "serving-dry-run", "status": "FAIL", "reasonCode": current.failure_code or "ARTIFACT_SET_NOT_LOADED"})
            return JSONResponse(
                status_code=503,
                content={"status": "NOT_READY", "artifactSetVersion": None, "checks": checks},
            )
        active = current.artifact_set
        checks = [
            {
                "artifactKind": active.core.bias_metadata.artifact_kind.value,
                "status": "PASS",
                "reasonCode": None,
            },
            {
                "artifactKind": active.core.factor_metadata.artifact_kind.value,
                "status": "PASS",
                "reasonCode": None,
            },
            {
                "artifactKind": active.core.calibrator_metadata.artifact_kind.value,
                "status": "PASS",
                "reasonCode": None,
            },
            {
                "artifactKind": active.core.mapping_metadata.artifact_kind.value,
                "status": "PASS",
                "reasonCode": None,
            },
            {"artifactKind": "serving-dry-run", "status": "PASS", "reasonCode": None},
        ]
        return {"status": "READY", "artifactSetVersion": active.artifact_set_version, "checks": checks}

    @app.post("/internal/v1/recommendations/rank", dependencies=[Depends(require_local_fake_auth)])
    async def rank(
        payload: RecommendationRequest,
        x_request_id: Annotated[str, Header(alias="X-Request-Id")],
        traceparent: Annotated[str | None, Header()] = None,
    ):
        header_id = _safe_request_id(x_request_id)
        if traceparent is not None and re.fullmatch(
            r"[0-9a-f]{2}-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}", traceparent
        ) is None:
            return _safe_error(
                header_id,
                "INVALID_RECOMMENDATION_REQUEST",
                "Recommendation request validation failed.",
                False,
                422,
            )
        if header_id != str(payload.requestId):
            return _safe_error(
                header_id,
                "REQUEST_ID_MISMATCH",
                "Request correlation identifiers do not match.",
                False,
                422,
            )
        current = artifact_registry.snapshot()
        if current.artifact_set is None:
            code = current.failure_code or "ARTIFACT_SET_NOT_READY"
            return _safe_error(
                header_id,
                code,
                "No compatible recommendation artifact set is ready.",
                True,
                503,
            )
        active = current.artifact_set
        result = OfflineInferencePipeline(active.core).run(
            candidate_movie_ids=[str(item) for item in payload.candidateSet.movieIds]
        )
        items = [
            {
                "movieId": item.service_movie_id,
                "rank": item.rank,
                "rankingSource": "BAYESIAN_POPULARITY",
                "expectedStar": _expected_star(),
                "reasons": [],
            }
            for item in result.ranked_movies
        ]
        issues = [
            {
                "scope": "CANDIDATE",
                "code": item.reason,
                "movieId": item.service_movie_id or None,
                "retriable": False,
            }
            for item in result.request_quarantine
        ]
        if payload.starPolicy == "REC_EV_003B_CANDIDATE":
            issues.append(
                {
                    "scope": "STAR_HEAD",
                    "code": "STAR_SCALE_INCOMPATIBLE",
                    "movieId": None,
                    "retriable": False,
                }
            )
        issues.sort(key=lambda item: (item["scope"], item["code"], item["movieId"] or ""))
        outcome = "EMPTY" if not items else ("PARTIAL" if issues else "COMPLETE")
        return {
            "requestId": str(payload.requestId),
            "outcome": outcome,
            "snapshot": _snapshot(active, payload),
            "items": items,
            "issues": issues,
        }

    if experiment_enabled:

        @app.post(
            "/internal/v1/experiments/recommendation-interpretation",
            dependencies=[Depends(require_local_fake_auth)],
        )
        async def recommendation_interpretation(
            payload: RecommendationInterpretationRequest,
            x_request_id: Annotated[str, Header(alias="X-Request-Id")],
        ):
            header_id = _safe_request_id(x_request_id)
            if header_id != str(payload.requestId):
                return _safe_error(
                    header_id,
                    "REQUEST_ID_MISMATCH",
                    "Request correlation identifiers do not match.",
                    False,
                    422,
                )
            current = artifact_registry.snapshot()
            if current.artifact_set is None:
                code = current.failure_code or "ARTIFACT_SET_NOT_READY"
                return _safe_error(
                    header_id,
                    code,
                    "No compatible recommendation artifact set is ready.",
                    True,
                    503,
                )
            active = current.artifact_set
            try:
                result = interpret_recommendations(
                    active.core,
                    candidate_movie_ids=[
                        str(item) for item in payload.candidateSet.movieIds
                    ],
                    ratings_most_recent_first=[
                        InterpretationRating(str(item.movieId), float(item.value))
                        for item in payload.preferenceInput.ratings
                    ],
                )
            except InterpretationInputError:
                return _safe_error(
                    header_id,
                    "INVALID_RECOMMENDATION_REQUEST",
                    "Recommendation request validation failed.",
                    False,
                    422,
                )
            except CandidateNotEnabledError:
                return _safe_error(
                    header_id,
                    "EXPERIMENT_ARTIFACT_NOT_READY",
                    "The local experiment artifact is not ready.",
                    True,
                    503,
                )
            profile = result.rating_profile
            return {
                "requestId": str(payload.requestId),
                "experimentVersion": EXPERIMENT_VERSION,
                "snapshot": {
                    "artifactSetVersion": active.artifact_set_version,
                    "policyVersion": active.core.policy.version,
                    "inputVersion": payload.preferenceInput.inputVersion,
                    "kSelectionPolicyVersion": K_SELECTION_POLICY_VERSION,
                    "utilityPolicyVersion": UTILITY_POLICY_VERSION,
                    "availableRatingCount": profile.active_rating_count,
                    "usedRatingCount": result.used_rating_count,
                },
                "ratingProfile": {
                    "activeRatingCount": profile.active_rating_count,
                    "mean": profile.mean,
                    "median": profile.median,
                    "confidence": profile.confidence,
                },
                "items": [
                    {
                        "movieId": item.movie_id,
                        "predictedRating": item.predicted_rating,
                        "expectedRelativeUtility": item.expected_relative_utility,
                        "directFoldIn": item.direct_fold_in,
                        "confidence": item.confidence,
                        "displayEligible": False,
                    }
                    for item in result.items
                ],
                "limitations": list(LIMITATIONS),
            }

    return app


app = create_app()
