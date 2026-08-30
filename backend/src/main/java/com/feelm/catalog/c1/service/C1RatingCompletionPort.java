package com.feelm.catalog.c1.service;

import java.time.OffsetDateTime;
import java.util.UUID;

public interface C1RatingCompletionPort {
    void completeRatedRecommendationItems(
            UUID actorUserId,
            UUID movieId,
            UUID ratingId,
            int ratingRevision,
            OffsetDateTime occurredAt
    );
}
