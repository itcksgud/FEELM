package com.feelm.catalog.c2.input;

import com.feelm.catalog.c1.foundation.C1FoundationException;
import com.feelm.catalog.c1.foundation.C1OutboxDispatcher;
import org.springframework.context.annotation.Profile;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

import java.time.Clock;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;

@Component
@Profile({"postgres", "local"})
public class PostgresActiveRatingInputProjection implements ActiveRatingInputPort, C1OutboxDispatcher.Consumer {
    private static final Set<String> RATING_EVENTS = Set.of(
            "RATING_CREATED", "RATING_UPDATED", "RATING_DELETED"
    );

    private final JdbcTemplate jdbc;
    private final Clock clock;
    private final ActiveRatingInputVersioner versioner;

    public PostgresActiveRatingInputProjection(
            JdbcTemplate jdbc,
            Clock clock,
            ActiveRatingInputVersioner versioner
    ) {
        this.jdbc = jdbc;
        this.clock = clock;
        this.versioner = versioner;
    }

    @Override
    public void consume(C1OutboxDispatcher.Message message) {
        if (message == null || !RATING_EVENTS.contains(message.eventType())
                || !"RATING".equals(message.aggregateType())) {
            throw new C1FoundationException("C2_INPUT_EVENT_UNSUPPORTED", "outbox event is not a Rating input event");
        }
        if (alreadyApplied(message.eventId())) {
            return;
        }
        UUID actor = actorFor(message);
        lockActor(actor);
        if (alreadyApplied(message.eventId())) {
            return;
        }

        List<ActiveRatingInputPort.RatingInput> activeRatings = readActiveRatings(actor);
        ActiveRatingInputPort.Snapshot snapshot = versioner.canonicalSnapshot(activeRatings);
        OffsetDateTime rebuiltAt = OffsetDateTime.ofInstant(clock.instant(), ZoneOffset.UTC);
        jdbc.update("""
                INSERT INTO c2_rating_input_snapshot (
                    user_id, input_policy_version, input_version, rating_count,
                    source_event_id, projection_revision, rebuilt_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT (user_id) DO UPDATE
                   SET input_policy_version = EXCLUDED.input_policy_version,
                       input_version = EXCLUDED.input_version,
                       rating_count = EXCLUDED.rating_count,
                       source_event_id = EXCLUDED.source_event_id,
                       projection_revision = c2_rating_input_snapshot.projection_revision + 1,
                       rebuilt_at = EXCLUDED.rebuilt_at
                """,
                actor,
                ActiveRatingInputVersioner.POLICY_VERSION,
                snapshot.inputVersion(),
                snapshot.ratings().size(),
                message.eventId(),
                rebuiltAt
        );
        jdbc.update("DELETE FROM c2_rating_input_item WHERE user_id = ?", actor);
        for (int index = 0; index < snapshot.ratings().size(); index++) {
            ActiveRatingInputPort.RatingInput input = snapshot.ratings().get(index);
            jdbc.update("""
                    INSERT INTO c2_rating_input_item (
                        user_id, movie_id, rating_value, rating_revision, canonical_order
                    ) VALUES (?, ?, ?, ?, ?)
                    """, actor, input.movieId(), input.value(), input.revision(), index);
        }
        jdbc.update("""
                INSERT INTO c2_rating_input_event_application (event_id, user_id, input_version, applied_at)
                VALUES (?, ?, ?, ?)
                """, message.eventId(), actor, snapshot.inputVersion(), rebuiltAt);
    }

    @Override
    @Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ)
    public Optional<ActiveRatingInputPort.Snapshot> findProjected(UUID actorUserId) {
        if (actorUserId == null) {
            throw new IllegalArgumentException("actor is required");
        }
        List<String> versions = jdbc.query(
                "SELECT input_version FROM c2_rating_input_snapshot WHERE user_id = ?",
                (rs, row) -> rs.getString(1), actorUserId
        );
        if (versions.isEmpty()) {
            return Optional.empty();
        }
        List<ActiveRatingInputPort.RatingInput> ratings = jdbc.query("""
                SELECT movie_id, rating_value, rating_revision
                  FROM c2_rating_input_item
                 WHERE user_id = ?
                 ORDER BY canonical_order
                """, (rs, row) -> new ActiveRatingInputPort.RatingInput(
                rs.getObject("movie_id", UUID.class), rs.getInt("rating_value"), rs.getInt("rating_revision")
        ), actorUserId);
        ActiveRatingInputPort.Snapshot verified = versioner.canonicalSnapshot(ratings);
        if (!versions.get(0).equals(verified.inputVersion())) {
            throw new C1FoundationException("C2_INPUT_PROJECTION_CORRUPT", "Rating input projection is inconsistent");
        }
        return Optional.of(verified);
    }

    private UUID actorFor(C1OutboxDispatcher.Message message) {
        List<UUID> actors = jdbc.query("""
                SELECT actor_user_id
                  FROM user_behavior_event
                 WHERE event_id = ? AND event_type = ?
                   AND resource_type = 'RATING' AND resource_id = ?
                """, (rs, row) -> rs.getObject(1, UUID.class),
                message.eventId(), message.eventType(), message.aggregateId());
        if (actors.size() != 1) {
            throw new C1FoundationException("C2_INPUT_EVENT_INVALID", "Rating input event source is invalid");
        }
        return actors.get(0);
    }

    private List<ActiveRatingInputPort.RatingInput> readActiveRatings(UUID actor) {
        return jdbc.query("""
                SELECT movie_id, value, revision
                  FROM rating
                 WHERE user_id = ? AND logical_status = 'ACTIVE' AND value BETWEEN 1 AND 5
                """, (rs, row) -> new ActiveRatingInputPort.RatingInput(
                rs.getObject("movie_id", UUID.class), rs.getInt("value"), rs.getInt("revision")
        ), actor);
    }

    private boolean alreadyApplied(UUID eventId) {
        Boolean exists = jdbc.queryForObject(
                "SELECT EXISTS(SELECT 1 FROM c2_rating_input_event_application WHERE event_id = ?)",
                Boolean.class,
                eventId
        );
        return Boolean.TRUE.equals(exists);
    }

    private void lockActor(UUID actor) {
        jdbc.query(
                "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                (resultSet, rowNumber) -> 0,
                actor + ":C2_ACTIVE_RATING_INPUT"
        );
    }
}
