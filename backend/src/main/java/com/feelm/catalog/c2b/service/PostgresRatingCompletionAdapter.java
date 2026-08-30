package com.feelm.catalog.c2b.service;

import com.feelm.catalog.c1.service.C1RatingCompletionPort;
import org.springframework.context.annotation.Profile;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;

@Component
@Profile({"postgres", "local"})
public final class PostgresRatingCompletionAdapter implements C1RatingCompletionPort {
    private final JdbcTemplate jdbc;

    public PostgresRatingCompletionAdapter(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    public void completeRatedRecommendationItems(
            UUID actorUserId,
            UUID movieId,
            UUID ratingId,
            int ratingRevision,
            OffsetDateTime occurredAt
    ) {
        jdbc.query(
                "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                (rs, row) -> 0,
                actorUserId + ":PERSONAL_DISCOVERY"
        );
        List<UUID> deliveries = jdbc.query("""
                SELECT delivery_id
                  FROM recommendation_delivery_item
                 WHERE actor_user_id = ? AND movie_id = ? AND status = 'ACTIVE'
                 ORDER BY delivery_id
                 FOR UPDATE
                """, (rs, row) -> rs.getObject("delivery_id", UUID.class), actorUserId, movieId);
        for (UUID deliveryId : deliveries) {
            jdbc.update("""
                    UPDATE recommendation_delivery_item
                       SET status = 'COMPLETED_RATED', terminal_at = ?,
                           completion_rating_id = ?, completion_rating_revision = ?
                     WHERE delivery_id = ? AND actor_user_id = ? AND movie_id = ? AND status = 'ACTIVE'
                    """, occurredAt, ratingId, ratingRevision, deliveryId, actorUserId, movieId);
            jdbc.update("""
                    UPDATE recommendation_delivery
                       SET revision = revision + 1, updated_at = ?
                     WHERE id = ? AND actor_user_id = ? AND status = 'ACTIVE'
                    """, occurredAt, deliveryId, actorUserId);
        }
    }
}
