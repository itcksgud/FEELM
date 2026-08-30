package com.feelm.catalog.c6.service;

import com.feelm.catalog.c2.input.ActiveRatingInputPort;
import com.feelm.catalog.c2.recommendation.CandidateSetPort;

import java.math.BigDecimal;
import java.util.List;
import java.util.UUID;

public interface C6RecommenderPort {
    Result interpret(Command command);

    record Command(
            UUID requestId,
            CandidateSetPort.Snapshot candidateSet,
            List<UUID> eligibleMovieIds,
            String inputVersion,
            List<ActiveRatingInputPort.RatingInput> ratings
    ) {
        public Command {
            eligibleMovieIds = List.copyOf(eligibleMovieIds);
            ratings = List.copyOf(ratings);
        }
    }

    record Result(
            String experimentVersion,
            Snapshot snapshot,
            RatingProfile ratingProfile,
            List<Item> items,
            List<String> limitations
    ) {
        public Result {
            items = List.copyOf(items);
            limitations = List.copyOf(limitations);
        }
    }

    record Snapshot(
            String artifactSetVersion,
            String policyVersion,
            String inputVersion,
            String kSelectionPolicyVersion,
            String utilityPolicyVersion,
            int availableRatingCount,
            int usedRatingCount
    ) {
    }

    record RatingProfile(
            int activeRatingCount,
            BigDecimal mean,
            BigDecimal median,
            String confidence
    ) {
    }

    record Item(
            UUID movieId,
            BigDecimal predictedRating,
            BigDecimal expectedRelativeUtility,
            boolean directFoldIn,
            String confidence,
            boolean displayEligible
    ) {
    }
}
