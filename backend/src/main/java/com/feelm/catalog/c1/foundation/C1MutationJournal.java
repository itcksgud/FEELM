package com.feelm.catalog.c1.foundation;

import com.fasterxml.jackson.databind.JsonNode;
import org.springframework.context.annotation.Profile;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

import java.time.Clock;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

@Component
@Profile({"postgres", "local"})
public final class C1MutationJournal {
    private static final Map<String, Set<String>> PAYLOAD_FIELDS = Map.of(
            "OTT_LINK_CLICKED", Set.of("movieId", "providerId", "linkType"),
            "WATCH_CONFIRMATION_RESPONDED", Set.of("movieId", "watched"),
            "RATING_CREATED", Set.of("movieId", "ratingRevision"),
            "RATING_UPDATED", Set.of("movieId", "ratingRevision"),
            "RATING_DELETED", Set.of("movieId", "ratingRevision")
    );

    private final JdbcTemplate jdbc;
    private final Clock clock;

    public C1MutationJournal(JdbcTemplate jdbc, Clock clock) {
        this.jdbc = jdbc;
        this.clock = clock;
    }

    public UUID append(
            UUID actorUserId,
            String eventType,
            String resourceType,
            UUID resourceId,
            String traceId,
            JsonNode payload
    ) {
        validate(actorUserId, eventType, resourceType, resourceId, traceId, payload);
        UUID eventId = UUID.randomUUID();
        OffsetDateTime occurredAt = OffsetDateTime.ofInstant(clock.instant(), ZoneOffset.UTC);
        jdbc.update("""
                INSERT INTO user_behavior_event (
                    event_id, actor_user_id, event_type, resource_type, resource_id,
                    occurred_at, trace_id, schema_version, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?::jsonb)
                """, eventId, actorUserId, eventType, resourceType, resourceId, occurredAt, traceId, payload.toString());
        jdbc.update("""
                INSERT INTO domain_outbox (
                    event_id, aggregate_type, aggregate_id, event_type, schema_version,
                    payload, occurred_at, status, attempt_count
                ) VALUES (?, ?, ?, ?, 1, ?::jsonb, ?, 'PENDING', 0)
                """, eventId, resourceType, resourceId, eventType, payload.toString(), occurredAt);
        return eventId;
    }

    private void validate(
            UUID actorUserId,
            String eventType,
            String resourceType,
            UUID resourceId,
            String traceId,
            JsonNode payload
    ) {
        Set<String> allowed = PAYLOAD_FIELDS.get(eventType);
        if (actorUserId == null || resourceId == null || traceId == null || traceId.isBlank()
                || allowed == null || payload == null || !payload.isObject()) {
            throw new C1FoundationException("INVALID_BEHAVIOR_EVENT", "behavior event is invalid");
        }
        if (!("WATCH_INTENT".equals(resourceType) || "RATING".equals(resourceType))) {
            throw new C1FoundationException("INVALID_BEHAVIOR_EVENT", "behavior resource type is invalid");
        }
        Set<String> actual = payload.properties().stream().map(Map.Entry::getKey).collect(java.util.stream.Collectors.toSet());
        if (!actual.equals(allowed)) {
            throw new C1FoundationException("INVALID_BEHAVIOR_PAYLOAD", "behavior payload is not allowlisted");
        }
    }
}
