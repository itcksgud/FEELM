package com.feelm.catalog.c6.api;

import java.math.BigDecimal;
import java.util.List;
import java.util.UUID;

public final class C6ApiDtos {
    private C6ApiDtos() {
    }

    public record RecommendationInterpretationExperiment(
            String experimentVersion,
            String inputVersion,
            ModelContext modelContext,
            RatingProfile ratingProfile,
            List<Prediction> predictions,
            List<TasteEvidence> tasteEvidence,
            List<String> limitations
    ) {
        public RecommendationInterpretationExperiment {
            predictions = List.copyOf(predictions);
            tasteEvidence = List.copyOf(tasteEvidence);
            limitations = List.copyOf(limitations);
        }
    }

    public record ModelContext(
            String artifactSetVersion,
            String policyVersion,
            String kSelectionPolicyVersion,
            String utilityPolicyVersion,
            int availableRatingCount,
            int usedRatingCount
    ) {
    }

    public record RatingProfile(
            int activeRatingCount,
            BigDecimal mean,
            BigDecimal median,
            String confidence
    ) {
    }

    public record Prediction(
            MovieSummary movie,
            BigDecimal predictedRating,
            BigDecimal expectedRelativeUtility,
            boolean directFoldIn,
            String confidence,
            boolean displayEligible
    ) {
    }

    public record MovieSummary(
            UUID movieId,
            String title,
            String posterUrl,
            Integer releaseYear,
            List<String> genres
    ) {
        public MovieSummary {
            genres = List.copyOf(genres);
        }
    }

    public record TasteEvidence(
            String dimensionType,
            String dimensionKey,
            String displayName,
            int ratingCount,
            BigDecimal averageRating,
            BigDecimal liftFromUserMean,
            String confidence
    ) {
    }
}
