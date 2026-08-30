package com.feelm.catalog.c2.input;

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

import java.util.List;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest
@ActiveProfiles("local")
@Testcontainers(disabledWithoutDocker = true)
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
class ActiveRatingInputOutboxWorkerPostgresIntegrationTest {
    private static final Set<String> RATING_EVENTS = Set.of(
            "RATING_CREATED", "RATING_UPDATED", "RATING_DELETED"
    );
    private static final UUID OWNER = UUID.fromString("018f6826-4da1-7c38-a846-8f794cd8b0cf");
    private static final UUID MOVIE = UUID.fromString("6b226903-0ca4-4f5a-9bf0-50d6cedd224c");
    private static final UUID RATING = UUID.fromString("0527c943-fb46-4aa5-aea2-130bdc752e75");

    @Container
    static final PostgreSQLContainer<?> POSTGRES = new PostgreSQLContainer<>("postgres:17-alpine")
            .withDatabaseName("feelm_c1_outbox_worker_test");

    @DynamicPropertySource
    static void configure(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", POSTGRES::getJdbcUrl);
        registry.add("spring.datasource.username", POSTGRES::getUsername);
        registry.add("spring.datasource.password", POSTGRES::getPassword);
        registry.add("catalog.c1.watch-intent-scheduler-delay-ms", () -> "3600000");
        registry.add("catalog.c1.outbox-worker.enabled", () -> "true");
        registry.add("catalog.c1.outbox-worker.batch-size", () -> "1");
        registry.add("catalog.c1.outbox-worker.max-attempts", () -> "3");
        registry.add("catalog.c1.outbox-worker.initial-delay-ms", () -> "3600000");
    }

    @Autowired
    JdbcTemplate jdbc;

    @Autowired
    C1Service c1Service;

    @Autowired
    C1OutboxDispatcher dispatcher;

    @Autowired
    PostgresActiveRatingInputProjection projection;

    @Autowired
    ActiveRatingInputOutboxWorker worker;

    @Test
    @Order(1)
    void runtimeWorkerUsesTheActualProjectionAndHonorsItsBatchBound() {
        // BH-C1-004 / AC-C1-049
        updateRating(5, 2, "worker-batch-a-0001");
        updateRating(4, 3, "worker-batch-b-0001");

        assertThat(worker.pollOnce()).isEqualTo(1);
        assertThat(count("SELECT count(*) FROM domain_outbox WHERE event_type = 'RATING_UPDATED' AND status = 'PROCESSED'"))
                .isEqualTo(1);
        assertThat(count("SELECT count(*) FROM domain_outbox WHERE event_type = 'RATING_UPDATED' AND status = 'PENDING'"))
                .isEqualTo(1);
        assertThat(count("SELECT count(*) FROM c2_rating_input_event_application")).isEqualTo(1);

        assertThat(worker.pollOnce()).isEqualTo(1);
        assertThat(count("SELECT count(*) FROM c2_rating_input_event_application")).isEqualTo(2);
    }

    @Test
    @Order(2)
    void aSingleWorkerInstanceRejectsOverlappingPollsWithoutDuplicateApplication() throws Exception {
        // BH-C1-004 single-instance re-entry safety
        updateRating(3, 4, "worker-reentry-0001");
        ExecutorService pool = Executors.newFixedThreadPool(2);
        CountDownLatch start = new CountDownLatch(1);
        try {
            Future<Integer> first = pool.submit(() -> {
                start.await();
                return worker.pollOnce();
            });
            Future<Integer> second = pool.submit(() -> {
                start.await();
                return worker.pollOnce();
            });
            start.countDown();
            assertThat(first.get() + second.get()).isEqualTo(1);
        } finally {
            pool.shutdownNow();
        }

        UUID eventId = eventForRevision(5);
        assertThat(count("SELECT count(*) FROM c2_rating_input_event_application WHERE event_id = ?", eventId))
                .isEqualTo(1);
        assertThat(jdbc.queryForObject("SELECT attempt_count FROM domain_outbox WHERE event_id = ?", Integer.class, eventId))
                .isEqualTo(1);
    }

    @Test
    @Order(3)
    void competingDatabaseClaimSkipsTheLockedEvent() throws Exception {
        // BH-C1-004 row-claim competition safety
        updateRating(2, 5, "worker-claim-0001");
        UUID eventId = eventForRevision(6);
        CountDownLatch consumerEntered = new CountDownLatch(1);
        CountDownLatch releaseConsumer = new CountDownLatch(1);
        ExecutorService pool = Executors.newFixedThreadPool(2);
        try {
            Future<Optional<C1OutboxDispatcher.DispatchResult>> claimed = pool.submit(() ->
                    dispatcher.dispatchNext(RATING_EVENTS, message -> {
                        projection.consume(message);
                        consumerEntered.countDown();
                        releaseConsumer.await();
                    }));
            consumerEntered.await();
            Future<Optional<C1OutboxDispatcher.DispatchResult>> competitor = pool.submit(() ->
                    dispatcher.dispatchNext(RATING_EVENTS, projection));

            assertThat(competitor.get()).isEmpty();
            releaseConsumer.countDown();
            assertThat(claimed.get()).hasValueSatisfying(result -> {
                assertThat(result.eventId()).isEqualTo(eventId);
                assertThat(result.status()).isEqualTo("PROCESSED");
            });
        } finally {
            releaseConsumer.countDown();
            pool.shutdownNow();
        }
        assertThat(count("SELECT count(*) FROM c2_rating_input_event_application WHERE event_id = ?", eventId))
                .isEqualTo(1);
    }

    @Test
    @Order(4)
    void retriesStopAtTheConfiguredLimitAndNeverRollBackTheC1Commit() {
        // BH-C1-004 / AC-C1-049 bounded retry and C1 isolation
        updateRating(1, 6, "worker-dead-letter-0001");
        UUID eventId = eventForRevision(7);

        for (int attempt = 1; attempt <= 3; attempt++) {
            C1OutboxDispatcher.DispatchResult result = dispatcher.dispatchNext(
                    RATING_EVENTS,
                    message -> { throw new IllegalStateException("simulated downstream outage"); }
            ).orElseThrow();
            assertThat(result.eventId()).isEqualTo(eventId);
            assertThat(result.attemptCount()).isEqualTo(attempt);
            assertThat(result.status()).isEqualTo(attempt == 3 ? "DEAD_LETTER" : "FAILED");
            if (attempt < 3) {
                jdbc.update("UPDATE domain_outbox SET next_attempt_at = NULL WHERE event_id = ?", eventId);
            }
        }

        assertThat(dispatcher.dispatchNext(RATING_EVENTS, projection)).isEmpty();
        assertThat(jdbc.queryForObject("SELECT status FROM domain_outbox WHERE event_id = ?", String.class, eventId))
                .isEqualTo("DEAD_LETTER");
        assertThat(count("SELECT count(*) FROM c2_rating_input_event_application WHERE event_id = ?", eventId))
                .isZero();
        assertThat(jdbc.queryForObject(
                "SELECT value FROM rating WHERE id = ? AND logical_status = 'ACTIVE'", Integer.class, RATING
        )).isEqualTo(1);
        assertThat(count("SELECT count(*) FROM frame WHERE rating_id = ?", RATING)).isEqualTo(1);
        assertThat(count("SELECT count(*) FROM popcorn WHERE rating_id = ?", RATING)).isEqualTo(1);
    }

    private void updateRating(int value, int expectedRevision, String idempotencyKey) {
        c1Service.putRating(
                OWNER,
                MOVIE,
                idempotencyKey,
                new C1ApiDtos.PutRatingRequest(value, expectedRevision),
                "trace-" + idempotencyKey
        );
    }

    private UUID eventForRevision(int revision) {
        List<UUID> events = jdbc.query("""
                SELECT event_id FROM domain_outbox
                 WHERE aggregate_id = ? AND event_type = 'RATING_UPDATED'
                   AND payload ->> 'ratingRevision' = ?
                """, (rs, row) -> rs.getObject(1, UUID.class), RATING, Integer.toString(revision));
        assertThat(events).hasSize(1);
        return events.get(0);
    }

    private int count(String sql, Object... args) {
        Integer value = jdbc.queryForObject(sql, Integer.class, args);
        return value == null ? 0 : value;
    }
}
