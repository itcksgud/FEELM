package com.feelm.catalog.c1.foundation;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.feelm.catalog.security.C1Ownership;
import com.feelm.catalog.security.CatalogUserContext;
import com.feelm.catalog.security.CatalogUserContextResolver;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.UUID;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("local")
@Testcontainers(disabledWithoutDocker = true)
class C1FoundationPostgresIntegrationTest {
    private static final UUID MOVIE = UUID.fromString("6b226903-0ca4-4f5a-9bf0-50d6cedd224c");
    private static final UUID PROVIDER = UUID.fromString("d392a4d5-0428-4e06-aa41-aef899c06842");
    private static final UUID SHADOW = UUID.fromString("18828763-1fd7-4ee4-a97f-1496db3c6490");
    private static final UUID ACTIVE_CATALOG = UUID.fromString("10000000-0000-0000-0000-000000000002");

    @Container
    static final PostgreSQLContainer<?> POSTGRES = new PostgreSQLContainer<>("postgres:17-alpine")
            .withDatabaseName("feelm_c1_foundation_test");

    @DynamicPropertySource
    static void configurePostgres(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", POSTGRES::getJdbcUrl);
        registry.add("spring.datasource.username", POSTGRES::getUsername);
        registry.add("spring.datasource.password", POSTGRES::getPassword);
    }

    @Autowired
    JdbcTemplate jdbc;

    @Autowired
    MockMvc mvc;

    @Autowired
    ObjectMapper objectMapper;

    @Autowired
    C1TimePolicy timePolicy;

    @Autowired
    C1IdempotencyService idempotency;

    @Autowired
    C1MutationJournal journal;

    @Autowired
    C1RatingEligibilityRepository eligibility;

    @Autowired
    C1InvariantRepository invariants;

    @Autowired
    CatalogUserContextResolver userContextResolver;

    @Autowired
    C1Ownership ownership;

    @Test
    void migrationsSeedApprovedFlavorReferencesAndAConsistentLocalFixture() {
        assertThat(jdbc.queryForObject("SELECT count(*) FROM popcorn_flavor", Long.class)).isEqualTo(8);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM flavor_genre_mapping WHERE mapping_version = 'v1'", Long.class))
                .isEqualTo(19);
        Map<Integer, String> actualMapping = jdbc.query(
                """
                SELECT gm.source_genre_id, f.flavor_code || ':' || f.display_name AS flavor
                  FROM flavor_genre_mapping gm
                  JOIN popcorn_flavor f ON f.id = gm.flavor_id
                 WHERE gm.mapping_version = 'v1'
                """,
                resultSet -> {
                    Map<Integer, String> values = new java.util.TreeMap<>();
                    while (resultSet.next()) {
                        values.put(resultSet.getInt("source_genre_id"), resultSet.getString("flavor"));
                    }
                    return values;
                }
        );
        assertThat(actualMapping).containsExactlyInAnyOrderEntriesOf(Map.ofEntries(
                Map.entry(28, "ADRENALINE:짜릿함"), Map.entry(12, "ADRENALINE:짜릿함"),
                Map.entry(16, "WONDER:상상"), Map.entry(14, "WONDER:상상"), Map.entry(878, "WONDER:상상"),
                Map.entry(35, "JOY:유쾌함"), Map.entry(10751, "JOY:유쾌함"),
                Map.entry(18, "HEART:여운"), Map.entry(10749, "HEART:여운"),
                Map.entry(80, "SHADOW:긴장"), Map.entry(27, "SHADOW:긴장"),
                Map.entry(9648, "SHADOW:긴장"), Map.entry(53, "SHADOW:긴장"),
                Map.entry(99, "REAL:현실"),
                Map.entry(36, "LEGACY:시대"), Map.entry(10752, "LEGACY:시대"), Map.entry(37, "LEGACY:시대"),
                Map.entry(10402, "RHYTHM:리듬"), Map.entry(10770, "RHYTHM:리듬")
        ));
        assertThat(jdbc.queryForObject("SELECT count(*) FROM movie_flavor_assignment WHERE mapping_version = 'v1'", Long.class))
                .isEqualTo(7);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM c1_rating_eligible_movie", Long.class)).isEqualTo(7);

        C1InvariantRepository.ProjectionInvariant invariant = invariants.currentProjectionInvariant();
        assertThat(invariant.valid()).isTrue();
        assertThat(invariant.activeRatingCount()).isEqualTo(1);

        jdbc.update("""
                INSERT INTO flavor_mapping_version (mapping_version, status, created_at)
                VALUES ('v2-test', 'STAGING', now())
                """);
        jdbc.update("""
                INSERT INTO flavor_genre_mapping (mapping_version, source_genre_id, flavor_id, source_display_order)
                VALUES ('v2-test', 80, ?, 0)
                """, SHADOW);
        assertThat(jdbc.queryForObject(
                "SELECT count(*) FROM flavor_genre_mapping WHERE mapping_version = 'v2-test' AND flavor_id = ?",
                Long.class,
                SHADOW
        )).isEqualTo(1);
    }

    @Test
    void watchIntentTimePolicyAndDatabaseConstraintsFixThe48HourAnd7DayBoundaries() {
        Instant clickedAt = Instant.parse("2026-08-29T12:00:00Z");
        C1TimePolicy.Window window = timePolicy.fromFirstActiveClick(clickedAt);
        assertThat(window.confirmationDueAt()).isEqualTo(Instant.parse("2026-08-31T12:00:00Z"));
        assertThat(window.expiresAt()).isEqualTo(Instant.parse("2026-09-05T12:00:00Z"));
        assertThat(timePolicy.isConfirmationDue(window, window.confirmationDueAt().minusMillis(1))).isFalse();
        assertThat(timePolicy.isConfirmationDue(window, window.confirmationDueAt())).isTrue();
        assertThat(timePolicy.isConfirmationDue(window, window.expiresAt().minusMillis(1))).isTrue();
        assertThat(timePolicy.isConfirmationDue(window, window.expiresAt())).isFalse();

        UUID actor = UUID.fromString("63ed7999-9147-4891-b25b-0efd976bcf18");
        insertActiveIntent(UUID.randomUUID(), actor, clickedAt, window.confirmationDueAt(), window.expiresAt());
        assertThatThrownBy(() -> insertActiveIntent(
                UUID.randomUUID(), actor, clickedAt, window.confirmationDueAt(), window.expiresAt()
        )).isInstanceOf(DataIntegrityViolationException.class);
        assertThatThrownBy(() -> insertActiveIntent(
                UUID.randomUUID(), UUID.randomUUID(), clickedAt, window.confirmationDueAt().plusSeconds(1), window.expiresAt()
        )).isInstanceOf(DataIntegrityViolationException.class);
    }

    @Test
    void ratingEligibilityRequiresUiReadyAndExactlyOneActiveAssignment() {
        assertThat(eligibility.isRatingEligible(MOVIE)).isTrue();

        UUID noAssignment = UUID.fromString("a92fe7ef-8ee7-4da7-a77a-ff34c206e1f1");
        jdbc.update("INSERT INTO movie_identity (id, created_at) VALUES (?, now())", noAssignment);
        jdbc.update("""
                INSERT INTO movie_catalog_projection (
                    catalog_version_id, movie_id, media_type, identity_status, visibility_status,
                    original_title, original_language, runtime_minutes, poster_path,
                    tmdb_vote_count, metadata_fetched_at, deleted
                ) VALUES (?, ?, 'MOVIE', 'IDENTITY_VERIFIED', 'UI_READY', 'No Assignment', 'en', 90,
                          '/no-assignment.jpg', 0, now(), false)
                """, ACTIVE_CATALOG, noAssignment);
        assertThat(eligibility.isRatingEligible(noAssignment)).isFalse();

        jdbc.update("""
                INSERT INTO movie_flavor_assignment (
                    mapping_version, movie_id, flavor_id, assignment_source,
                    source_genre_id, source_display_order, assigned_at
                ) VALUES ('v1', ?, ?, 'PRIMARY_TMDB_GENRE', 80, 0, now())
                """, noAssignment, SHADOW);
        assertThat(eligibility.isRatingEligible(noAssignment)).isTrue();
        assertThatThrownBy(() -> jdbc.update("""
                INSERT INTO movie_flavor_assignment (
                    mapping_version, movie_id, flavor_id, assignment_source,
                    source_genre_id, source_display_order, assigned_at
                ) VALUES ('v1', ?, ?, 'PRIMARY_TMDB_GENRE', 53, 0, now())
                """, noAssignment, SHADOW)).isInstanceOf(DataIntegrityViolationException.class);
    }

    @Test
    void idempotencyReplaysSameCanonicalBodyAndRejectsDifferentReuseInOneJournaledTransaction() {
        UUID actor = UUID.fromString("0dbd0415-ff40-4e41-9a28-29799655f21f");
        UUID intent = UUID.fromString("756f3fd1-d41d-4c89-9df7-0a1cd8127cc5");
        AtomicInteger mutations = new AtomicInteger();
        JsonNode request = objectMapper.createObjectNode().put("movieId", MOVIE.toString()).put("offerId", "offer-one");

        C1IdempotencyService.ExecutionResult first = idempotency.execute(
                actor,
                "CREATE_WATCH_INTENT",
                "c1-watch-create-0001",
                request,
                () -> {
                    mutations.incrementAndGet();
                    ObjectNode payload = objectMapper.createObjectNode()
                            .put("movieId", MOVIE.toString())
                            .put("providerId", PROVIDER.toString())
                            .put("linkType", "AGGREGATOR");
                    journal.append(actor, "OTT_LINK_CLICKED", "WATCH_INTENT", intent, "trace-c1-test", payload);
                    return new C1IdempotencyService.MutationResponse(
                            201,
                            objectMapper.createObjectNode().put("outcome", "CREATED"),
                            intent
                    );
                }
        );

        JsonNode sameCanonicalBody = objectMapper.createObjectNode().put("offerId", "offer-one").put("movieId", MOVIE.toString());
        C1IdempotencyService.ExecutionResult replay = idempotency.execute(
                actor,
                "CREATE_WATCH_INTENT",
                "c1-watch-create-0001",
                sameCanonicalBody,
                () -> {
                    mutations.incrementAndGet();
                    throw new AssertionError("replay must not execute the mutation");
                }
        );

        assertThat(first.replayed()).isFalse();
        assertThat(replay.replayed()).isTrue();
        assertThat(replay.response().body().path("outcome").asText()).isEqualTo("CREATED");
        assertThat(mutations).hasValue(1);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM user_behavior_event WHERE actor_user_id = ?", Long.class, actor))
                .isEqualTo(1);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM domain_outbox WHERE aggregate_id = ?", Long.class, intent))
                .isEqualTo(1);

        JsonNode different = objectMapper.createObjectNode().put("movieId", MOVIE.toString()).put("offerId", "offer-two");
        assertThatThrownBy(() -> idempotency.execute(
                actor,
                "CREATE_WATCH_INTENT",
                "c1-watch-create-0001",
                different,
                () -> new C1IdempotencyService.MutationResponse(201, objectMapper.createObjectNode(), intent)
        )).isInstanceOf(C1FoundationException.class)
                .extracting(exception -> ((C1FoundationException) exception).code())
                .isEqualTo("IDEMPOTENCY_KEY_REUSED");

        UUID rolledBackActor = UUID.fromString("8e6eb5b2-ecac-4f04-971f-6a44afcb20cf");
        UUID rolledBackIntent = UUID.fromString("fbf11214-7b9a-4d73-bfd6-d69ad6dcf681");
        assertThatThrownBy(() -> idempotency.execute(
                rolledBackActor,
                "CREATE_WATCH_INTENT",
                "c1-watch-rollback-0001",
                request,
                () -> {
                    ObjectNode payload = objectMapper.createObjectNode()
                            .put("movieId", MOVIE.toString())
                            .put("providerId", PROVIDER.toString())
                            .put("linkType", "AGGREGATOR");
                    journal.append(
                            rolledBackActor,
                            "OTT_LINK_CLICKED",
                            "WATCH_INTENT",
                            rolledBackIntent,
                            "trace-c1-rollback",
                            payload
                    );
                    throw new C1FoundationException("FAIL_AFTER_JOURNAL", "injected transaction failure");
                }
        )).isInstanceOf(C1FoundationException.class)
                .extracting(exception -> ((C1FoundationException) exception).code())
                .isEqualTo("FAIL_AFTER_JOURNAL");
        assertThat(jdbc.queryForObject(
                "SELECT count(*) FROM user_behavior_event WHERE actor_user_id = ?", Long.class, rolledBackActor
        )).isZero();
        assertThat(jdbc.queryForObject(
                "SELECT count(*) FROM domain_outbox WHERE aggregate_id = ?", Long.class, rolledBackIntent
        )).isZero();
        assertThat(jdbc.queryForObject(
                "SELECT count(*) FROM idempotency_record WHERE actor_user_id = ?", Long.class, rolledBackActor
        )).isZero();
    }

    @Test
    void requiredAuthAndOwnershipUseStableFakeActorsWithPostgresProfile() throws Exception {
        mvc.perform(get("/api/v1/me/ratings"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("UNAUTHORIZED"));
        mvc.perform(get("/api/v1/me/ratings").header("Authorization", "Bearer test-c1-invalid-token"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("UNAUTHORIZED"));

        CatalogUserContext owner = userContextResolver.resolveRequired("Bearer test-c1-owner-token");
        CatalogUserContext other = userContextResolver.resolveRequired("Bearer test-c1-other-token");
        ownership.requireOwner(owner.actorUserId(), owner.actorUserId());
        assertThatThrownBy(() -> ownership.requireOwner(owner.actorUserId(), other.actorUserId()))
                .hasMessageNotContaining(owner.actorUserId().toString())
                .hasMessageNotContaining(other.actorUserId().toString());
    }

    private void insertActiveIntent(
            UUID intentId,
            UUID actor,
            Instant clickedAt,
            Instant dueAt,
            Instant expiresAt
    ) {
        jdbc.update("""
                INSERT INTO watch_intent (
                    id, user_id, movie_id, provider_id, status,
                    clicked_at, confirmation_due_at, expires_at, revision
                ) VALUES (?, ?, ?, ?, 'LINK_CLICKED', ?, ?, ?, 1)
                """,
                intentId,
                actor,
                MOVIE,
                PROVIDER,
                OffsetDateTime.ofInstant(clickedAt, ZoneOffset.UTC),
                OffsetDateTime.ofInstant(dueAt, ZoneOffset.UTC),
                OffsetDateTime.ofInstant(expiresAt, ZoneOffset.UTC)
        );
    }
}
