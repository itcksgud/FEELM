package com.feelm.catalog.c1;

import com.feelm.catalog.c1.api.C1ApiDtos;
import com.feelm.catalog.c1.service.C1MutationFaultInjector;
import com.feelm.catalog.c1.service.C1Service;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.context.annotation.Primary;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

@SpringBootTest
@ActiveProfiles("local")
@Testcontainers(disabledWithoutDocker = true)
@Import(C1MutationRollbackPostgresIntegrationTest.FaultTestConfiguration.class)
class C1MutationRollbackPostgresIntegrationTest {
    private static final UUID OWNER = UUID.fromString("018f6826-4da1-7c38-a846-8f794cd8b0cf");
    private static final UUID PENDING_INTENT = UUID.fromString("8b7f4a21-4bc4-4c5e-93cb-4e348abcae02");
    private static final UUID UNRATED_MOVIE = UUID.fromString("19406c31-213f-4fe1-93f6-109f8570ec20");
    private static final UUID ACTIVE_MOVIE = UUID.fromString("6b226903-0ca4-4f5a-9bf0-50d6cedd224c");

    @Container
    static final PostgreSQLContainer<?> POSTGRES = new PostgreSQLContainer<>("postgres:17-alpine")
            .withDatabaseName("feelm_c1_mutation_rollback_test");

    @DynamicPropertySource
    static void configure(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", POSTGRES::getJdbcUrl);
        registry.add("spring.datasource.username", POSTGRES::getUsername);
        registry.add("spring.datasource.password", POSTGRES::getPassword);
        registry.add("catalog.c1.watch-intent-scheduler-delay-ms", () -> "3600000");
        registry.add("catalog.c1.outbox-worker.enabled", () -> "false");
    }

    @Autowired
    JdbcTemplate jdbc;

    @Autowired
    C1Service service;

    @Autowired
    ControllableFaultInjector faults;

    @AfterEach
    void clearFault() {
        faults.clear();
    }

    @Test
    void confirmationFailureAfterIntentUpdateRollsBackTheWholeAggregate() {
        // AC-C1-018
        assertRollback(
                C1MutationFaultInjector.Checkpoint.AFTER_CONFIRMATION_STATUS_UPDATED,
                () -> service.confirm(
                        OWNER, PENDING_INTENT, "rollback-confirm-0001",
                        new C1ApiDtos.ConfirmWatchIntentRequest(true, 1), "trace-rollback-confirm"
                )
        );
    }

    @Test
    void failAfterRatingRowRollsBackRatingAndEveryDerivedState() {
        // AC-C1-028 / FAIL-AFTER-RATING
        assertRollback(
                C1MutationFaultInjector.Checkpoint.AFTER_RATING_WRITTEN,
                () -> service.putRating(
                        OWNER, UNRATED_MOVIE, "rollback-rating-0001",
                        new C1ApiDtos.PutRatingRequest(5, null), "trace-rollback-rating"
                )
        );
    }

    @Test
    void failAfterPopcornRollsBackRatingFramePopcornAndAggregates() {
        // AC-C1-028 / FAIL-AFTER-POPCORN
        assertRollback(
                C1MutationFaultInjector.Checkpoint.AFTER_POPCORN_WRITTEN,
                () -> service.putRating(
                        OWNER, UNRATED_MOVIE, "rollback-popcorn-0001",
                        new C1ApiDtos.PutRatingRequest(5, null), "trace-rollback-popcorn"
                )
        );
    }

    @Test
    void deleteFailureAfterAggregateInverseRestoresTheActiveAggregateExactly() {
        // AC-C1-028 / delete aggregate inverse failure
        assertRollback(
                C1MutationFaultInjector.Checkpoint.AFTER_DELETE_AGGREGATES_REVERSED,
                () -> service.deleteRating(
                        OWNER, ACTIVE_MOVIE, "rollback-delete-0001", 2, "trace-rollback-delete"
                )
        );
    }

    private void assertRollback(C1MutationFaultInjector.Checkpoint checkpoint, Runnable mutation) {
        StateFingerprint before = fingerprint();
        faults.failAt(checkpoint);
        assertThatThrownBy(mutation::run)
                .isInstanceOf(InjectedMutationFailure.class)
                .hasMessageContaining(checkpoint.name());
        StateFingerprint after = fingerprint();

        assertThat(after.counts()).as("row counts after rollback").isEqualTo(before.counts());
        assertThat(after.sha256()).as("canonical aggregate hash after rollback").isEqualTo(before.sha256());
    }

    private StateFingerprint fingerprint() {
        List<TableQuery> queries = List.of(
                table("watch_intent", """
                        SELECT to_jsonb(t)::text FROM (
                          SELECT id, movie_id, provider_id, source_offer_id, status, clicked_at,
                                 confirmation_due_at, expires_at, responded_at, revision
                            FROM watch_intent WHERE user_id = ? ORDER BY id
                        ) t ORDER BY t.id
                        """),
                table("viewing_record", """
                        SELECT to_jsonb(t)::text FROM (
                          SELECT id, movie_id, source_watch_intent_id, provider_id, status,
                                 watched_confirmed_at, revision
                            FROM viewing_record WHERE user_id = ? ORDER BY id
                        ) t ORDER BY t.id
                        """),
                table("rating", """
                        SELECT to_jsonb(t)::text FROM (
                          SELECT id, movie_id, viewing_record_id, value, logical_status, revision,
                                 created_at, updated_at, deleted_at, deletion_trace_id
                            FROM rating WHERE user_id = ? ORDER BY id
                        ) t ORDER BY t.id
                        """),
                table("frame", """
                        SELECT to_jsonb(t)::text FROM (
                          SELECT id, movie_id, viewing_record_id, rating_id, derivation_version, created_at, updated_at
                            FROM frame WHERE user_id = ? ORDER BY id
                        ) t ORDER BY t.id
                        """),
                table("popcorn", """
                        SELECT to_jsonb(t)::text FROM (
                          SELECT id, frame_id, rating_id, flavor_id, flavor_mapping_version, created_at
                            FROM popcorn WHERE user_id = ? ORDER BY id
                        ) t ORDER BY t.id
                        """),
                table("flavor_aggregate", """
                        SELECT to_jsonb(t)::text FROM (
                          SELECT flavor_id, popcorn_count, rating_count, rating_sum, revision, updated_at
                            FROM flavor_aggregate WHERE user_id = ? ORDER BY flavor_id
                        ) t ORDER BY t.flavor_id
                        """),
                table("rating_taste_contribution", """
                        SELECT to_jsonb(t)::text FROM (
                          SELECT c.rating_id, c.dimension_type, c.dimension_key, c.rating_value,
                                 c.catalog_version_id, c.flavor_mapping_version, c.derivation_version
                            FROM rating_taste_contribution c JOIN rating r ON r.id = c.rating_id
                           WHERE r.user_id = ? ORDER BY c.rating_id, c.dimension_type, c.dimension_key
                        ) t ORDER BY t.rating_id, t.dimension_type, t.dimension_key
                        """),
                table("taste_aggregate", """
                        SELECT to_jsonb(t)::text FROM (
                          SELECT dimension_type, dimension_key, rating_count, rating_sum, revision, updated_at
                            FROM taste_aggregate WHERE user_id = ? ORDER BY dimension_type, dimension_key
                        ) t ORDER BY t.dimension_type, t.dimension_key
                        """),
                table("behavior", """
                        SELECT to_jsonb(t)::text FROM (
                          SELECT event_id, event_type, resource_type, resource_id, occurred_at,
                                 trace_id, schema_version, payload
                            FROM user_behavior_event WHERE actor_user_id = ? ORDER BY event_id
                        ) t ORDER BY t.event_id
                        """),
                table("outbox", """
                        SELECT to_jsonb(t)::text FROM (
                          SELECT o.event_id, o.aggregate_type, o.aggregate_id, o.event_type, o.schema_version,
                                 o.payload, o.occurred_at, o.status, o.attempt_count, o.next_attempt_at, o.processed_at
                            FROM domain_outbox o JOIN user_behavior_event b ON b.event_id = o.event_id
                           WHERE b.actor_user_id = ? ORDER BY o.event_id
                        ) t ORDER BY t.event_id
                        """),
                table("idempotency", """
                        SELECT to_jsonb(t)::text FROM (
                          SELECT operation_code, idempotency_key, request_hash, response_status,
                                 response_body, resource_id, created_at, expires_at
                            FROM idempotency_record WHERE actor_user_id = ? ORDER BY operation_code, idempotency_key
                        ) t ORDER BY t.operation_code, t.idempotency_key
                        """)
        );
        Map<String, Integer> counts = new LinkedHashMap<>();
        List<String> canonical = new ArrayList<>();
        for (TableQuery query : queries) {
            List<String> rows = jdbc.queryForList(query.sql(), String.class, OWNER);
            counts.put(query.name(), rows.size());
            canonical.add(query.name());
            canonical.addAll(rows);
        }
        try {
            String hash = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256")
                    .digest(String.join("\n", canonical).getBytes(StandardCharsets.UTF_8)));
            return new StateFingerprint(Map.copyOf(counts), hash);
        } catch (Exception exception) {
            throw new IllegalStateException("rollback fingerprint failed", exception);
        }
    }

    private static TableQuery table(String name, String sql) {
        return new TableQuery(name, sql);
    }

    @TestConfiguration
    static class FaultTestConfiguration {
        @Bean
        @Primary
        ControllableFaultInjector controllableFaultInjector() {
            return new ControllableFaultInjector();
        }
    }

    static final class ControllableFaultInjector implements C1MutationFaultInjector {
        private final AtomicReference<Checkpoint> armed = new AtomicReference<>();

        void failAt(Checkpoint checkpoint) {
            armed.set(checkpoint);
        }

        void clear() {
            armed.set(null);
        }

        @Override
        public void checkpoint(Checkpoint checkpoint) {
            if (armed.compareAndSet(checkpoint, null)) {
                throw new InjectedMutationFailure(checkpoint.name());
            }
        }
    }

    static final class InjectedMutationFailure extends RuntimeException {
        InjectedMutationFailure(String checkpoint) {
            super("injected C1 mutation failure at " + checkpoint);
        }
    }

    private record TableQuery(String name, String sql) {
    }

    private record StateFingerprint(Map<String, Integer> counts, String sha256) {
    }
}
