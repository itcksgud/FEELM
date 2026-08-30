package com.feelm.catalog.c2.recommendation;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

public interface RecommendationExposurePort {
    PersistResult persist(ExposureSnapshot snapshot);

    Optional<ExposureSnapshot> findOwned(UUID actorUserId, UUID exposureBatchId);

    record ModelArtifact(String version, String payloadSha256) {
    }

    record ExpectedStar(
            String status,
            BigDecimal value,
            boolean displayEligible,
            String confidence,
            String confidencePolicyVersion
    ) {
    }

    record ExposureItem(
            UUID recommendationItemId,
            UUID movieId,
            int position,
            int sourceRank,
            String recommendationType,
            ExpectedStar expectedStar
    ) {
    }

    record ExposureSnapshot(
            UUID exposureBatchId,
            UUID sourceRequestId,
            UUID actorUserId,
            String recommendationVersion,
            String artifactSetVersion,
            String compatibilityId,
            String policyVersion,
            String rankingPolicy,
            BigDecimal rankingAlpha,
            String mappingVersion,
            String catalogVersion,
            String candidateSetVersion,
            String inputVersion,
            ModelArtifact bias,
            ModelArtifact factors,
            ModelArtifact calibration,
            ModelArtifact mapping,
            String attributionPolicyVersion,
            Instant exposedAt,
            String canonicalPayloadSha256,
            List<ExposureItem> items
    ) {
        public ExposureSnapshot {
            items = List.copyOf(items);
        }
    }

    record PersistResult(ExposureSnapshot snapshot, boolean replayed) {
    }
}

