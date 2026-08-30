class RecommenderError(Exception):
    """Base error for deterministic recommender-core failures."""


class ArtifactValidationError(RecommenderError):
    """An artifact is malformed or its checksum is invalid."""


class ArtifactCompatibilityError(RecommenderError):
    """Artifacts cannot safely be used together."""


class CandidateNotEnabledError(RecommenderError):
    """A validated candidate was used without an explicit product feature gate."""

