from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

from .bias import BiasModel, _integer_ids, _ratings
from .calibration import HeadCalibrationBundle, load_head_calibration_bundle
from .errors import ArtifactCompatibilityError, CandidateNotEnabledError
from .factors import ItemFactorModel
from .metadata import ArtifactKind, ArtifactMetadata, require_same_family
from .mapping import ItemIdMapping
from .policy import DualHeadCandidatePolicy, REC_EV_003B_POLICY


@dataclass(frozen=True, slots=True)
class StarEstimateResult:
    item_ids: npt.NDArray[np.int64]
    stars: npt.NDArray[np.float64]
    direct_fold_in: npt.NDArray[np.bool_]
    k: int
    provided_ratings: int
    known_factor_ratings: int
    star_alpha: float
    calibrated: bool
    policy_version: str
    model_versions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RankingResult:
    item_ids: npt.NDArray[np.int64]
    scores: npt.NDArray[np.float64]
    ranked_item_ids: npt.NDArray[np.int64]
    ranking_policy: str
    fold_in_alpha: float
    model_version: str


class RecommendationCore:
    """Framework-neutral serving boundary for the validated cold-start candidate."""

    def __init__(
        self,
        *,
        bias_model: BiasModel,
        item_factors: ItemFactorModel,
        calibration_bundle: HeadCalibrationBundle,
        item_mapping: ItemIdMapping,
        bias_metadata: ArtifactMetadata,
        factor_metadata: ArtifactMetadata,
        calibrator_metadata: ArtifactMetadata,
        mapping_metadata: ArtifactMetadata,
        policy: DualHeadCandidatePolicy = REC_EV_003B_POLICY,
        enable_candidate: bool = False,
        onboarding_bias_regularization: float = 10.0,
        popularity_prior_count: float = 50.0,
    ) -> None:
        bias_metadata.require_kind(ArtifactKind.BIAS)
        factor_metadata.require_kind(ArtifactKind.ALS_ITEM_FACTORS)
        calibrator_metadata.require_kind(ArtifactKind.HEAD_CALIBRATION_BUNDLE)
        mapping_metadata.require_kind(ArtifactKind.ITEM_ID_MAPPING)
        require_same_family(
            bias_metadata, factor_metadata, calibrator_metadata, mapping_metadata
        )
        if calibrator_metadata.evidence_id != policy.evidence_id:
            raise ArtifactCompatibilityError(
                "calibrator bundle must come from the selected dual-head evidence"
            )
        if factor_metadata.factor_rank != item_factors.rank:
            raise ArtifactCompatibilityError("loaded factor rank differs from metadata")
        if (bias_metadata.rating_min, bias_metadata.rating_max) != (
            bias_model.rating_min,
            bias_model.rating_max,
        ):
            raise ArtifactCompatibilityError("loaded Bias rating scale differs from metadata")
        if float(factor_metadata.parameters["reg_param"]) != item_factors.reg_param:
            raise ArtifactCompatibilityError("loaded factor regularization differs from metadata")
        if calibration_bundle.policy_version != policy.version:
            raise ArtifactCompatibilityError(
                "calibration policy version differs from the selected candidate"
            )
        if set(calibration_bundle.star_blend) != set(policy.star_alpha_by_k):
            raise ArtifactCompatibilityError(
                "blend-specific calibrators are required for every validated K"
            )
        if (
            policy.ranking_policy != "BAYESIAN_POPULARITY_ONLY"
            or calibration_bundle.ranking_mode != "NONE_POPULARITY_RAW"
            or calibration_bundle.ranking_alpha != policy.ranking_alpha
        ):
            raise ArtifactCompatibilityError("ranking calibration violates the alpha-0 policy")
        for calibrator in calibration_bundle.star_blend.values():
            if (calibrator.rating_min, calibrator.rating_max) != (
                bias_model.rating_min,
                bias_model.rating_max,
            ):
                raise ArtifactCompatibilityError("calibrator and Bias rating scales differ")
        compatibility = calibrator_metadata.compatibility or {}
        checksum_bindings = {
            "bias_payload_sha256": bias_metadata.payload_sha256,
            "factor_payload_sha256": factor_metadata.payload_sha256,
            "mapping_payload_sha256": mapping_metadata.payload_sha256,
        }
        for key, expected in checksum_bindings.items():
            if compatibility.get(key) != expected:
                raise ArtifactCompatibilityError(
                    f"calibration compatibility binding {key} does not match"
                )
        mapping_compatibility = mapping_metadata.compatibility or {}
        if item_mapping.mapping_version != mapping_compatibility.get("mapping_version"):
            raise ArtifactCompatibilityError("loaded item mapping version differs from metadata")
        if item_mapping.source_id_space != bias_metadata.id_space:
            raise ArtifactCompatibilityError("item mapping source ID space differs from models")
        if onboarding_bias_regularization < 0 or popularity_prior_count < 0:
            raise ValueError("serving regularization and popularity prior must be non-negative")
        expected_reg_user = float(bias_metadata.parameters["reg_user"])
        if onboarding_bias_regularization != expected_reg_user:
            raise ArtifactCompatibilityError(
                "onboarding Bias regularization must match the validated artifact"
            )
        if popularity_prior_count != float(
            bias_metadata.parameters["popularity_prior_count"]
        ):
            raise ArtifactCompatibilityError(
                "Popularity prior must match the validated artifact"
            )
        self.bias_model = bias_model
        self.item_factors = item_factors
        self.calibration_bundle = calibration_bundle
        self.item_mapping = item_mapping
        self.bias_metadata = bias_metadata
        self.factor_metadata = factor_metadata
        self.calibrator_metadata = calibrator_metadata
        self.mapping_metadata = mapping_metadata
        self.policy = policy
        self.onboarding_bias_regularization = onboarding_bias_regularization
        self.popularity_prior_count = popularity_prior_count
        self.enable_candidate = enable_candidate

    @classmethod
    def from_artifacts(
        cls,
        *,
        bias_payload: str | Path,
        bias_metadata_path: str | Path,
        factor_payload: str | Path,
        factor_metadata_path: str | Path,
        calibrator_payload: str | Path,
        calibrator_metadata_path: str | Path,
        mapping_payload: str | Path,
        mapping_metadata_path: str | Path,
        enable_candidate: bool = False,
    ) -> "RecommendationCore":
        bias_metadata = ArtifactMetadata.load(bias_metadata_path)
        factor_metadata = ArtifactMetadata.load(factor_metadata_path)
        calibrator_metadata = ArtifactMetadata.load(calibrator_metadata_path)
        mapping_metadata = ArtifactMetadata.load(mapping_metadata_path)
        bias = BiasModel.load_npz(bias_payload, bias_metadata)
        factors = ItemFactorModel.load_npz(factor_payload, factor_metadata)
        calibration_bundle = load_head_calibration_bundle(
            calibrator_payload, calibrator_metadata
        )
        item_mapping = ItemIdMapping.load(mapping_payload, mapping_metadata)
        return cls(
            bias_model=bias,
            item_factors=factors,
            calibration_bundle=calibration_bundle,
            item_mapping=item_mapping,
            bias_metadata=bias_metadata,
            factor_metadata=factor_metadata,
            calibrator_metadata=calibrator_metadata,
            mapping_metadata=mapping_metadata,
            enable_candidate=enable_candidate,
            onboarding_bias_regularization=float(bias_metadata.parameters["reg_user"]),
        )

    def estimate_stars(
        self,
        *,
        target_item_ids: npt.ArrayLike,
        onboarding_item_ids: npt.ArrayLike,
        onboarding_ratings: npt.ArrayLike,
        k: int,
    ) -> StarEstimateResult:
        if not self.enable_candidate:
            raise CandidateNotEnabledError(
                "REC-EV-003B star candidate is not a champion; explicit opt-in is required"
            )
        targets = _integer_ids(target_item_ids, "target_item_ids")
        onboarding_items = _integer_ids(onboarding_item_ids, "onboarding_item_ids")
        ratings = _ratings(onboarding_ratings)
        alpha = self.policy.star_alpha(k)
        if len(onboarding_items) != len(ratings) or len(ratings) != k:
            raise ValueError(f"K={k} requires exactly {k} aligned onboarding ratings")
        if bool((ratings < self.bias_model.rating_min).any()) or bool(
            (ratings > self.bias_model.rating_max).any()
        ):
            raise ValueError("onboarding ratings are outside the configured scale")
        if k == 0:
            bias_raw = self.bias_model.predict(
                np.full(len(targets), len(self.bias_model.user_bias), dtype=np.int64), targets
            )
            blended = bias_raw
            direct = np.zeros(len(targets), dtype=bool)
            known_factor_ratings = 0
        else:
            bias_raw = self.bias_model.predict_for_onboarding_user(
                targets,
                onboarding_items,
                ratings,
                reg_user=self.onboarding_bias_regularization,
            )
            fold_in = self.item_factors.fold_in(onboarding_items, ratings)
            direct = np.zeros(len(targets), dtype=bool)
            fold_raw = np.full(len(targets), np.nan, dtype=np.float64)
            if fold_in.factor is not None:
                fold_raw, direct = self.item_factors.score(fold_in.factor, targets)
            blended = bias_raw.copy()
            blended[direct] = (1.0 - alpha) * bias_raw[direct] + alpha * fold_raw[direct]
            known_factor_ratings = fold_in.factor_count
        stars = self.calibration_bundle.star_blend[k].apply(blended)
        return StarEstimateResult(
            item_ids=targets,
            stars=stars,
            direct_fold_in=direct,
            k=k,
            provided_ratings=len(ratings),
            known_factor_ratings=known_factor_ratings,
            star_alpha=alpha,
            calibrated=True,
            policy_version=self.policy.version,
            model_versions=(
                self.bias_metadata.model_version,
                self.factor_metadata.model_version,
                self.calibrator_metadata.model_version,
            ),
        )

    def rank(self, candidate_item_ids: npt.ArrayLike) -> RankingResult:
        items = _integer_ids(candidate_item_ids, "candidate_item_ids")
        scores = self.bias_model.popularity(items, prior_count=self.popularity_prior_count)
        order = np.lexsort((items, -scores))
        return RankingResult(
            item_ids=items,
            scores=scores,
            ranked_item_ids=items[order],
            ranking_policy=self.policy.ranking_policy,
            fold_in_alpha=self.policy.ranking_alpha,
            model_version=self.bias_metadata.model_version,
        )
