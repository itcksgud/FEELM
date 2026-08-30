from __future__ import annotations

from dataclasses import dataclass, field

from .errors import ArtifactCompatibilityError


@dataclass(frozen=True, slots=True)
class DualHeadCandidatePolicy:
    """The selected REC-EV-003B candidate; this is intentionally not a champion."""

    version: str = "cold-start-dual-head-blend-v1"
    evidence_id: str = "REC-EV-003B"
    star_alpha_by_k: dict[int, float] = field(
        default_factory=lambda: {0: 0.0, 1: 0.1, 3: 0.1, 5: 0.1, 10: 0.3, 20: 0.4}
    )
    minimum_practical_star_k: int = 10
    ranking_policy: str = "BAYESIAN_POPULARITY_ONLY"
    ranking_alpha: float = 0.0
    status: str = "VALIDATED_CANDIDATE_NOT_CHAMPION"

    def __post_init__(self) -> None:
        if self.ranking_alpha != 0.0 or self.ranking_policy != "BAYESIAN_POPULARITY_ONLY":
            raise ArtifactCompatibilityError(
                "REC-EV-003B permits no Fold-in contribution to ranking"
            )
        if set(self.star_alpha_by_k) != {0, 1, 3, 5, 10, 20}:
            raise ArtifactCompatibilityError("candidate policy K values were changed")
        if any(not 0.0 <= alpha <= 1.0 for alpha in self.star_alpha_by_k.values()):
            raise ArtifactCompatibilityError("star alpha must be between zero and one")

    def star_alpha(self, k: int) -> float:
        try:
            return self.star_alpha_by_k[k]
        except KeyError as error:
            raise ValueError(
                f"K={k} was not validated; supported values are {sorted(self.star_alpha_by_k)}"
            ) from error


REC_EV_003B_POLICY = DualHeadCandidatePolicy()

