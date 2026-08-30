package com.feelm.catalog.c2.input;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.feelm.catalog.c1.api.C1ApiDtos;
import com.feelm.catalog.c1.foundation.C1OutboxDispatcher;
import com.feelm.catalog.c1.service.C1Service;
import org.junit.jupiter.api.MethodOrderer;
import org.junit.jupiter.api.Order;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestMethodOrder;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicInteger;

import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest
@ActiveProfiles("local")
@Testcontainers(disabledWithoutDocker = true)
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
class C2ActiveRatingInputPostgresIntegrationTest {
    private static final UUID OWNER = UUID.fromString("018f6826-4da1-7c38-a846-8f794cd8b0cf");
    private static final UUID ACTIVE_MOVIE = UUID.fromString("6b226903-0ca4-4f5a-9bf0-50d6cedd224c");
    private static final UUID UNRATED_MOVIE = UUID.fromString("19406c31-213f-4fe1-93f6-109f8570ec20");
    private static final UUID ACTIVE_RATING = UUID.fromString("0527c943-fb46-4aa5-aea2-130bdc752e75");
    private static final UUID UNRATED_VIEWING = UUID.fromString("531a4e1d-2da8-48f1-a702-79fd875793d3");

    @Container
    static final PostgreSQLContainer<?> POSTGRES = new PostgreSQLContainer<>("postgres:17-alpine")
            .withDatabaseName("feelm_c2_rating_input_test");

    @DynamicPropertySource
    static void configure(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", POSTGRES::getJdbcUrl);
        registry.add("spring.datasource.username", POSTGRES::getUsername);
        registry.add("spring.datasource.password", POSTGRES::getPassword);
        registry.add("catalog.c1.watch-intent-scheduler-delay-ms", () -> "3600000");
    }

    @Autowired
    JdbcTemplate jdbc;

    @Autowired
    ObjectMapper objectMapper;

    @Autowired
    C1Service c1Service;

    @Autowired
    C1OutboxDispatcher dispatcher;

    @Autowired
    PostgresActiveRatingInputProjection projection;

    @Autowired
    ActiveRatingInputVersioner versioner;

    @Test
    @Order(1)
    void canonicalVersionIsOrderIndependentAndContainsNoActorOrClockMaterial() {
        // AC-C2-024
        UUID low = UUID.fromString("19406c31-213f-4fe1-93f6-109f8570ec20");
        UUID high = UUID.fromString("e8f7cf02-9bc4-4ff7-87b7-12fb02dd2490");
        ActiveRatingInputPort.RatingInput first = new ActiveRatingInputPort.RatingInput(high, 5, 3);
        ActiveRatingInputPort.RatingInput second = new ActiveRatingInputPort.RatingInput(low, 2, 7);

        ActiveRatingInputPort.Snapshot forward = versioner.canonicalSnapshot(List.of(first, second));
        ActiveRatingInputPort.Snapshot reversed = versioner.canonicalSnapshot(List.of(second, first));

        assertThat(forward).isEqualTo(reversed);
        assertThat(forward.ratings()).extracting(ActiveRatingInputPort.RatingInput::movieId)
                .containsExactly(low, high);
        assertThat(forward.inputVersion())
                .matches("^c2-active-rating-input-v1:sha256:[a-f0-9]{64}$")
                .doesNotContain(OWNER.toString())
                .doesNotContain("2026-08-29");
    }

    @Test
    @Order(2)
    void outboxProjectionContainsOnlyActiveIntegerRatingsAndDeduplicatesEventId() {
        // AC-C2-023, AC-C2-025
        UUID deletedRating = UUID.randomUUID();
        jdbc.update("""
                INSERT INTO rating (
                    id, user_id, movie_id, viewing_record_id, value, logical_status, revision,
                    created_at, updated_at, deleted_at, deletion_trace_id
                ) VALUES (?, ?, ?, ?, 2, 'DELETED', 4,
                          '2026-08-25T00:00:00Z', '2026-08-26T00:00:00Z',
                          '2026-08-26T00:00:00Z', 'fixture-deleted')
                """, deletedRating, OWNER, UNRATED_MOVIE, UNRATED_VIEWING);

        c1Service.putRating(
                OWNER,
                ACTIVE_MOVIE,
                "c2-input-update-0001",
                new C1ApiDtos.PutRatingRequest(5, 2),
                "c2-input-trace-0001"
        );
        UUID eventId = pendingEvent("RATING_UPDATED");
        assertThat(dispatcher.dispatchOne(eventId, projection).status()).isEqualTo("PROCESSED");

        ActiveRatingInputPort.Snapshot snapshot = projection.findProjected(OWNER).orElseThrow();
        assertThat(snapshot.ratings()).containsExactly(
                new ActiveRatingInputPort.RatingInput(ACTIVE_MOVIE, 5, 3)
        );
        assertThat(snapshot.ratings()).noneMatch(input -> input.movieId().equals(UNRATED_MOVIE));
        assertThat(count("SELECT count(*) FROM c2_rating_input_item WHERE user_id = ?", OWNER)).isEqualTo(1);
        assertThat(count("SELECT count(*) FROM c2_rating_input_event_application WHERE event_id = ?", eventId))
                .isEqualTo(1);
        long projectionRevision = jdbc.queryForObject(
                "SELECT projection_revision FROM c2_rating_input_snapshot WHERE user_id = ?", Long.class, OWNER);

        projection.consume(message(eventId));
        assertThat(jdbc.queryForObject(
                "SELECT projection_revision FROM c2_rating_input_snapshot WHERE user_id = ?", Long.class, OWNER))
                .isEqualTo(projectionRevision);
        assertThat(jdbc.queryForObject("SELECT value FROM rating WHERE id = ?", Integer.class, ACTIVE_RATING))
                .isEqualTo(5);
    }

    @Test
    @Order(3)
    void consumerFailureRollsBackProjectionAndRetriesWithoutRollingBackC1() {
        // AC-C2-022, AC-C2-025
        String previousVersion = projection.findProjected(OWNER).orElseThrow().inputVersion();
        c1Service.putRating(
                OWNER,
                ACTIVE_MOVIE,
                "c2-input-update-0002",
                new C1ApiDtos.PutRatingRequest(4, 3),
                "c2-input-trace-0002"
        );
        UUID eventId = pendingEvent("RATING_UPDATED");
        AtomicInteger attempts = new AtomicInteger();
        C1OutboxDispatcher.Consumer unavailableAfterProjection = message -> {
            projection.consume(message);
            attempts.incrementAndGet();
            throw new IllegalStateException("fixture recommender unavailable");
        };

        for (int expectedAttempt = 1; expectedAttempt <= 2; expectedAttempt++) {
            C1OutboxDispatcher.DispatchResult failed = dispatcher.dispatchOne(eventId, unavailableAfterProjection);
            assertThat(failed.status()).isEqualTo("FAILED");
            assertThat(failed.attemptCount()).isEqualTo(expectedAttempt);
            assertThat(projection.findProjected(OWNER).orElseThrow().inputVersion()).isEqualTo(previousVersion);
            assertThat(count("SELECT count(*) FROM c2_rating_input_event_application WHERE event_id = ?", eventId))
                    .isZero();
            assertThat(jdbc.queryForObject("SELECT value FROM rating WHERE id = ?", Integer.class, ACTIVE_RATING))
                    .isEqualTo(4);
            jdbc.update("UPDATE domain_outbox SET next_attempt_at = NULL WHERE event_id = ?", eventId);
        }

        C1OutboxDispatcher.DispatchResult recovered = dispatcher.dispatchOne(eventId, projection);
        assertThat(recovered.status()).isEqualTo("PROCESSED");
        assertThat(recovered.attemptCount()).isEqualTo(3);
        ActiveRatingInputPort.Snapshot refreshed = projection.findProjected(OWNER).orElseThrow();
        assertThat(refreshed.inputVersion()).isNotEqualTo(previousVersion);
        assertThat(refreshed.ratings()).containsExactly(
                new ActiveRatingInputPort.RatingInput(ACTIVE_MOVIE, 4, 4)
        );
        assertThat(attempts.get()).isEqualTo(2);
    }

    @Test
    @Order(4)
    void ratingDeleteProducesANewEmptyInputVersion() {
        // AC-C2-026
        ActiveRatingInputPort.Snapshot before = projection.findProjected(OWNER).orElseThrow();
        c1Service.deleteRating(
                OWNER,
                ACTIVE_MOVIE,
                "c2-input-delete-0001",
                4,
                "c2-input-trace-0003"
        );
        UUID eventId = pendingEvent("RATING_DELETED");
        assertThat(dispatcher.dispatchOne(eventId, projection).status()).isEqualTo("PROCESSED");

        ActiveRatingInputPort.Snapshot after = projection.findProjected(OWNER).orElseThrow();
        assertThat(after.ratings()).isEmpty();
        assertThat(after.inputVersion()).isNotEqualTo(before.inputVersion());
        assertThat(after.inputVersion()).isEqualTo(versioner.canonicalSnapshot(List.of()).inputVersion());
        assertThat(count("SELECT count(*) FROM c2_rating_input_item WHERE user_id = ?", OWNER)).isZero();
        assertThat(jdbc.queryForObject("SELECT logical_status FROM rating WHERE id = ?", String.class, ACTIVE_RATING))
                .isEqualTo("DELETED");
    }

    private UUID pendingEvent(String eventType) {
        return jdbc.queryForObject("""
                SELECT event_id FROM domain_outbox
                 WHERE event_type = ? AND status = 'PENDING'
                   AND event_id NOT IN (SELECT event_id FROM c2_rating_input_event_application)
                 ORDER BY occurred_at, event_id LIMIT 1
                """, UUID.class, eventType);
    }

    private C1OutboxDispatcher.Message message(UUID eventId) {
        return jdbc.queryForObject("""
                SELECT event_id, aggregate_type, aggregate_id, event_type, schema_version, payload, occurred_at, attempt_count
                  FROM domain_outbox WHERE event_id = ?
                """, (rs, row) -> {
            JsonNode payload;
            try {
                payload = objectMapper.readTree(rs.getString("payload"));
            } catch (Exception exception) {
                throw new IllegalStateException("Outbox test fixture payload is not valid JSON", exception);
            }
            return new C1OutboxDispatcher.Message(
                    rs.getObject("event_id", UUID.class), rs.getString("aggregate_type"),
                    rs.getObject("aggregate_id", UUID.class), rs.getString("event_type"),
                    rs.getInt("schema_version"), payload,
                    rs.getObject("occurred_at", OffsetDateTime.class), rs.getInt("attempt_count")
            );
        }, eventId);
    }

    private int count(String sql, Object... args) {
        Integer value = jdbc.queryForObject(sql, Integer.class, args);
        return value == null ? 0 : value;
    }
}
