package com.feelm.catalog.c1;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.feelm.catalog.c1.api.C1ApiDtos;
import com.feelm.catalog.c1.service.C1Service;
import com.feelm.catalog.c1.foundation.C1OutboxDispatcher;
import org.junit.jupiter.api.MethodOrderer;
import org.junit.jupiter.api.Order;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestMethodOrder;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.util.UUID;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;

import static org.assertj.core.api.Assertions.assertThat;
import static org.hamcrest.Matchers.hasSize;
import static org.hamcrest.Matchers.nullValue;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("local")
@Testcontainers(disabledWithoutDocker = true)
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
class C1VerticalPostgresAcceptanceTest {
    private static final String OWNER = "Bearer test-c1-owner-token";
    private static final String OTHER = "Bearer test-c1-other-token";
    private static final UUID OWNER_ID = UUID.fromString("018f6826-4da1-7c38-a846-8f794cd8b0cf");
    private static final UUID RATED_MOVIE = UUID.fromString("6b226903-0ca4-4f5a-9bf0-50d6cedd224c");
    private static final UUID TO_CONFIRM_MOVIE = UUID.fromString("e8f7cf02-9bc4-4ff7-87b7-12fb02dd2490");
    private static final UUID STALE_MOVIE = UUID.fromString("0437c1c0-06d5-4cdf-a7d1-5d5f1dc42e89");
    private static final UUID STALE_OFFER = UUID.fromString("afaa874e-20d0-42de-a143-f89ee8f706d5");
    private static final UUID PENDING_INTENT = UUID.fromString("2dfa8b82-9f40-452d-a63f-18347483f7b7");
    private static final UUID OTHER_INTENT = UUID.fromString("aef9c2be-1e46-4778-8c6c-9873989fd672");
    private static final UUID NOT_WATCHED_INTENT = UUID.fromString("8b7f4a21-4bc4-4c5e-93cb-4e348abcae02");
    private static final UUID FRAME = UUID.fromString("2b480314-590c-4d9a-b5df-1ef745c15e76");
    private static final UUID CONCURRENT_MOVIE = UUID.fromString("e67778c9-7b2e-42d4-9d3e-a3026b2efea3");

    @Container
    static final PostgreSQLContainer<?> POSTGRES = new PostgreSQLContainer<>("postgres:17-alpine")
            .withDatabaseName("feelm_c1_vertical_test");

    @DynamicPropertySource
    static void configure(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", POSTGRES::getJdbcUrl);
        registry.add("spring.datasource.username", POSTGRES::getUsername);
        registry.add("spring.datasource.password", POSTGRES::getPassword);
        registry.add("catalog.c1.watch-intent-scheduler-delay-ms", () -> "3600000");
    }

    @Autowired
    MockMvc mvc;

    @Autowired
    ObjectMapper objectMapper;

    @Autowired
    JdbcTemplate jdbc;

    @Autowired
    C1Service c1Service;

    @Autowired
    C1OutboxDispatcher outboxDispatcher;

    @Test
    @Order(1)
    void approvedFixtureReadsExposeOnlyOwnerProjectionAndStableEmptySemantics() throws Exception {
        // AC-C1-007, 032, 034, 035, 037, 038, 039, 044, 050
        String pending = mvc.perform(get("/api/v1/me/watch-intents/pending-confirmation")
                        .header("Authorization", OWNER).param("limit", "1"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.totalCount").value(2))
                .andExpect(jsonPath("$.items", hasSize(1)))
                .andExpect(jsonPath("$.hasNext").value(true))
                .andReturn().getResponse().getContentAsString();
        String cursor = objectMapper.readTree(pending).path("nextCursor").asText();
        mvc.perform(get("/api/v1/me/ratings").header("Authorization", OWNER).param("cursor", cursor))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("INVALID_CURSOR"));

        mvc.perform(get("/api/v1/me/film").header("Authorization", OWNER))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.totalCount").value(1))
                .andExpect(jsonPath("$.items[0].frameId").value(FRAME.toString()))
                .andExpect(jsonPath("$.userId").doesNotExist());
        mvc.perform(get("/api/v1/me/film/frames/{id}", FRAME).header("Authorization", OWNER))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.rating.value").value(4))
                .andExpect(jsonPath("$.provider.name").value("Netflix"))
                .andExpect(jsonPath("$.movie.tmdbId").doesNotExist());
        mvc.perform(get("/api/v1/me/film/frames/{id}", FRAME).header("Authorization", OTHER))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("RESOURCE_NOT_FOUND"));
        mvc.perform(get("/api/v1/me/popcorn-bucket").header("Authorization", OWNER))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.totalCount").value(1))
                .andExpect(jsonPath("$.flavors[3].code").value("HEART"))
                .andExpect(jsonPath("$.flavors[3].averageRating").value(nullValue()))
                .andExpect(jsonPath("$.flavors[4].code").value("SHADOW"))
                .andExpect(jsonPath("$.flavors[4].averageRating").value(4.0));
        mvc.perform(get("/api/v1/me/taste-profile").header("Authorization", OWNER))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items", hasSize(3)))
                .andExpect(jsonPath("$.items[2].dimensionKey")
                        .value("2d07d5d3-486f-4638-9d58-49331e798c76"));
    }

    @Test
    @Order(2)
    void ottClickCreatesReplaysAndReusesWithoutCreatingViewingOrRating() throws Exception {
        // AC-C1-001, 002, 003, 004, 006
        String body = """
                {"movieId":"0437c1c0-06d5-4cdf-a7d1-5d5f1dc42e89",
                 "offerId":"afaa874e-20d0-42de-a143-f89ee8f706d5"}
                """;
        String first = mvc.perform(post("/api/v1/watch-intents")
                        .header("Authorization", OWNER)
                        .header("Idempotency-Key", "watch-create-0001")
                        .contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.outcome").value("CREATED"))
                .andExpect(jsonPath("$.watchIntent.confirmationDueAt").value("2026-08-31T12:00:00Z"))
                .andExpect(jsonPath("$.watchIntent.expiresAt").value("2026-09-05T12:00:00Z"))
                .andReturn().getResponse().getContentAsString();
        UUID intent = UUID.fromString(objectMapper.readTree(first).path("watchIntent").path("watchIntentId").asText());

        mvc.perform(post("/api/v1/watch-intents")
                        .header("Authorization", OWNER)
                        .header("Idempotency-Key", "watch-create-0001")
                        .contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.watchIntent.watchIntentId").value(intent.toString()));
        assertThat(count("SELECT count(*) FROM user_behavior_event WHERE resource_id = ?", intent)).isEqualTo(1);

        mvc.perform(post("/api/v1/watch-intents")
                        .header("Authorization", OWNER)
                        .header("Idempotency-Key", "watch-create-0001")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body.replace(STALE_MOVIE.toString(), RATED_MOVIE.toString())))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("IDEMPOTENCY_KEY_REUSED"));

        mvc.perform(post("/api/v1/watch-intents")
                        .header("Authorization", OWNER)
                        .header("Idempotency-Key", "watch-create-0002")
                        .contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.outcome").value("ACTIVE_REUSED"))
                .andExpect(jsonPath("$.watchIntent.watchIntentId").value(intent.toString()));
        assertThat(count("SELECT count(*) FROM watch_intent WHERE user_id = ? AND movie_id = ?", OWNER_ID, STALE_MOVIE))
                .isEqualTo(1);
        assertThat(count("SELECT count(*) FROM viewing_record WHERE user_id = ? AND movie_id = ?", OWNER_ID, STALE_MOVIE))
                .isZero();
        assertThat(count("SELECT count(*) FROM user_behavior_event WHERE resource_id = ?", intent)).isEqualTo(2);

        int watchedIntentCount = count(
                "SELECT count(*) FROM watch_intent WHERE user_id = ? AND movie_id = ?", OWNER_ID, RATED_MOVIE);
        int watchedClickEvents = count("""
                SELECT count(*) FROM user_behavior_event
                 WHERE actor_user_id = ? AND event_type = 'OTT_LINK_CLICKED' AND payload->>'movieId' = ?
                """, OWNER_ID, RATED_MOVIE.toString());
        String alreadyWatchedBody = """
                {"movieId":"6b226903-0ca4-4f5a-9bf0-50d6cedd224c",
                 "offerId":"4c411f48-9990-4938-9f6c-cf17b42ce4cb"}
                """;
        mvc.perform(post("/api/v1/watch-intents")
                        .header("Authorization", OWNER).header("Idempotency-Key", "watch-already-0001")
                        .contentType(MediaType.APPLICATION_JSON).content(alreadyWatchedBody))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.outcome").value("ALREADY_WATCHED"))
                .andExpect(jsonPath("$.watchIntent").value(nullValue()));
        assertThat(count("SELECT count(*) FROM watch_intent WHERE user_id = ? AND movie_id = ?", OWNER_ID, RATED_MOVIE))
                .isEqualTo(watchedIntentCount);
        assertThat(count("""
                SELECT count(*) FROM user_behavior_event
                 WHERE actor_user_id = ? AND event_type = 'OTT_LINK_CLICKED' AND payload->>'movieId' = ?
                """, OWNER_ID, RATED_MOVIE.toString())).isEqualTo(watchedClickEvents + 1);
        mvc.perform(post("/api/v1/watch-intents")
                        .header("Authorization", OWNER).header("Idempotency-Key", "watch-already-0001")
                        .contentType(MediaType.APPLICATION_JSON).content(alreadyWatchedBody))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.outcome").value("ALREADY_WATCHED"));
        assertThat(count("""
                SELECT count(*) FROM user_behavior_event
                 WHERE actor_user_id = ? AND event_type = 'OTT_LINK_CLICKED' AND payload->>'movieId' = ?
                """, OWNER_ID, RATED_MOVIE.toString())).isEqualTo(watchedClickEvents + 1);
    }

    @Test
    @Order(3)
    void dueConfirmationAndRatingLifecycleAreAtomicAndIdempotent() throws Exception {
        // AC-C1-011, 012, 013, 016, 017, 019, 020, 022, 023, 025, 026, 027, 041
        mvc.perform(post("/api/v1/watch-intents/{id}/confirmation", OTHER_INTENT)
                        .header("Authorization", OWNER)
                        .header("Idempotency-Key", "confirm-other-0001")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"watched\":true,\"expectedRevision\":2}"))
                .andExpect(status().isNotFound());

        UUID earlyIntent = jdbc.queryForObject("""
                SELECT id FROM watch_intent WHERE user_id = ? AND movie_id = ? AND status = 'LINK_CLICKED'
                """, UUID.class, OWNER_ID, STALE_MOVIE);
        mvc.perform(post("/api/v1/watch-intents/{id}/confirmation", earlyIntent)
                        .header("Authorization", OWNER).header("Idempotency-Key", "confirm-early-0001")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"watched\":true,\"expectedRevision\":1}"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("WATCH_INTENT_NOT_CONFIRMABLE"));
        assertThat(jdbc.queryForObject("SELECT status FROM watch_intent WHERE id = ?", String.class, earlyIntent))
                .isEqualTo("LINK_CLICKED");
        assertThat(count("SELECT count(*) FROM user_behavior_event WHERE resource_id = ? AND event_type = 'WATCH_CONFIRMATION_RESPONDED'", earlyIntent))
                .isZero();

        UUID expiredIntent = UUID.randomUUID();
        jdbc.update("""
                INSERT INTO watch_intent (
                    id, user_id, movie_id, provider_id, status, clicked_at,
                    confirmation_due_at, expires_at, responded_at, revision
                ) VALUES (?, ?, 'cc3ddb45-0511-46ea-bf28-95b67c9fd20f',
                          'd392a4d5-0428-4e06-aa41-aef899c06842', 'EXPIRED',
                          '2026-08-20T12:00:00Z', '2026-08-22T12:00:00Z', '2026-08-27T12:00:00Z',
                          '2026-08-27T12:00:00Z', 2)
                """, expiredIntent, OWNER_ID);
        mvc.perform(post("/api/v1/watch-intents/{id}/confirmation", expiredIntent)
                        .header("Authorization", OWNER).header("Idempotency-Key", "confirm-expired-001")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"watched\":true,\"expectedRevision\":2}"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("WATCH_INTENT_NOT_CONFIRMABLE"));
        assertThat(jdbc.queryForObject("SELECT revision FROM watch_intent WHERE id = ?", Integer.class, expiredIntent))
                .isEqualTo(2);

        mvc.perform(post("/api/v1/watch-intents/{id}/confirmation", NOT_WATCHED_INTENT)
                        .header("Authorization", OWNER).header("Idempotency-Key", "confirm-notwatched-1")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"watched\":false,\"expectedRevision\":1}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("CONFIRMED_NOT_WATCHED"))
                .andExpect(jsonPath("$.viewingRecord").value(nullValue()));
        assertThat(count("""
                SELECT count(*) FROM viewing_record
                 WHERE user_id = ? AND movie_id = '1958ba3a-3d8c-4a4f-8845-124c0b12373e'
                """, OWNER_ID)).isZero();
        assertThat(count("""
                SELECT count(*) FROM rating_taste_contribution c
                  JOIN rating r ON r.id = c.rating_id
                 WHERE r.user_id = ? AND r.movie_id = '1958ba3a-3d8c-4a4f-8845-124c0b12373e'
                """, OWNER_ID)).isZero();
        mvc.perform(put("/api/v1/me/ratings/1958ba3a-3d8c-4a4f-8845-124c0b12373e")
                        .header("Authorization", OWNER).header("Idempotency-Key", "rating-needs-watch1")
                        .contentType(MediaType.APPLICATION_JSON).content("{\"value\":4}"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("WATCH_CONFIRMATION_REQUIRED"));
        assertThat(count("""
                SELECT count(*) FROM rating
                 WHERE user_id = ? AND movie_id = '1958ba3a-3d8c-4a4f-8845-124c0b12373e'
                """, OWNER_ID)).isZero();

        String confirmationBody = "{\"watched\":true,\"expectedRevision\":1}";
        String first = mvc.perform(post("/api/v1/watch-intents/{id}/confirmation", PENDING_INTENT)
                        .header("Authorization", OWNER)
                        .header("Idempotency-Key", "confirm-watch-0001")
                        .contentType(MediaType.APPLICATION_JSON).content(confirmationBody))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("CONFIRMED_WATCHED"))
                .andExpect(jsonPath("$.viewingRecord.movieId").value(TO_CONFIRM_MOVIE.toString()))
                .andReturn().getResponse().getContentAsString();
        UUID viewing = UUID.fromString(objectMapper.readTree(first).path("viewingRecord").path("viewingRecordId").asText());
        mvc.perform(post("/api/v1/watch-intents/{id}/confirmation", PENDING_INTENT)
                        .header("Authorization", OWNER)
                        .header("Idempotency-Key", "confirm-watch-0001")
                        .contentType(MediaType.APPLICATION_JSON).content(confirmationBody))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.viewingRecord.viewingRecordId").value(viewing.toString()));
        assertThat(count("SELECT count(*) FROM viewing_record WHERE id = ?", viewing)).isEqualTo(1);

        mvc.perform(get("/api/v1/me/viewing-records/unrated").header("Authorization", OWNER))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items[?(@.movie.movieId == '%s')]", TO_CONFIRM_MOVIE).isNotEmpty());

        mvc.perform(put("/api/v1/me/ratings/{movieId}", TO_CONFIRM_MOVIE)
                        .header("Authorization", OWNER).header("Idempotency-Key", "rating-invalid-001")
                        .contentType(MediaType.APPLICATION_JSON).content("{\"value\":3.5}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_ERROR"));

        String created = mvc.perform(put("/api/v1/me/ratings/{movieId}", TO_CONFIRM_MOVIE)
                        .header("Authorization", OWNER).header("Idempotency-Key", "rating-create-001")
                        .contentType(MediaType.APPLICATION_JSON).content("{\"value\":4}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.mutation").value("CREATED"))
                .andExpect(jsonPath("$.rating.revision").value(1))
                .andExpect(jsonPath("$.derivedState.recommendationRefresh").value("QUEUED"))
                .andReturn().getResponse().getContentAsString();
        JsonNode createdJson = objectMapper.readTree(created);
        UUID rating = UUID.fromString(createdJson.path("rating").path("ratingId").asText());
        UUID frame = UUID.fromString(createdJson.path("derivedState").path("frameId").asText());
        UUID popcorn = UUID.fromString(createdJson.path("derivedState").path("popcornId").asText());
        assertThat(flavorAggregate("HEART", "rating_count")).isEqualTo(1);
        assertThat(flavorAggregate("HEART", "rating_sum")).isEqualTo(4);

        mvc.perform(put("/api/v1/me/ratings/{movieId}", TO_CONFIRM_MOVIE)
                        .header("Authorization", OWNER).header("Idempotency-Key", "rating-create-001")
                        .contentType(MediaType.APPLICATION_JSON).content("{\"value\":4}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.rating.ratingId").value(rating.toString()));
        assertThat(count("SELECT count(*) FROM frame WHERE rating_id = ?", rating)).isEqualTo(1);
        assertThat(count("SELECT count(*) FROM popcorn WHERE rating_id = ?", rating)).isEqualTo(1);

        int ratingEventsBeforeStale = count("SELECT count(*) FROM user_behavior_event WHERE resource_id = ?", rating);
        mvc.perform(put("/api/v1/me/ratings/{movieId}", TO_CONFIRM_MOVIE)
                        .header("Authorization", OWNER).header("Idempotency-Key", "rating-stale-0001")
                        .contentType(MediaType.APPLICATION_JSON).content("{\"value\":5,\"expectedRevision\":99}"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("REVISION_CONFLICT"));
        assertThat(jdbc.queryForObject("SELECT value FROM rating WHERE id = ?", Integer.class, rating)).isEqualTo(4);
        assertThat(count("SELECT count(*) FROM user_behavior_event WHERE resource_id = ?", rating))
                .isEqualTo(ratingEventsBeforeStale);

        mvc.perform(put("/api/v1/me/ratings/{movieId}", TO_CONFIRM_MOVIE)
                        .header("Authorization", OWNER).header("Idempotency-Key", "rating-update-001")
                        .contentType(MediaType.APPLICATION_JSON).content("{\"value\":5,\"expectedRevision\":1}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.mutation").value("UPDATED"))
                .andExpect(jsonPath("$.rating.revision").value(2))
                .andExpect(jsonPath("$.derivedState.frameId").value(frame.toString()))
                .andExpect(jsonPath("$.derivedState.popcornId").value(popcorn.toString()));
        assertThat(flavorAggregate("HEART", "rating_count")).isEqualTo(1);
        assertThat(flavorAggregate("HEART", "rating_sum")).isEqualTo(5);

        mvc.perform(delete("/api/v1/me/ratings/{movieId}", TO_CONFIRM_MOVIE)
                        .header("Authorization", OWNER)
                        .header("Idempotency-Key", "rating-delete-001")
                        .header("X-Expected-Revision", "2"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.ratingRemoved").value(true))
                .andExpect(jsonPath("$.viewingStatus").value("WATCHED_CONFIRMED"));
        mvc.perform(delete("/api/v1/me/ratings/{movieId}", TO_CONFIRM_MOVIE)
                        .header("Authorization", OWNER)
                        .header("Idempotency-Key", "rating-delete-001")
                        .header("X-Expected-Revision", "2"))
                .andExpect(status().isOk());

        assertThat(jdbc.queryForObject("SELECT logical_status FROM rating WHERE id = ?", String.class, rating))
                .isEqualTo("DELETED");
        assertThat(count("SELECT count(*) FROM frame WHERE id = ?", frame)).isZero();
        assertThat(count("SELECT count(*) FROM popcorn WHERE id = ?", popcorn)).isZero();
        assertThat(count("SELECT count(*) FROM rating_taste_contribution WHERE rating_id = ?", rating)).isZero();
        assertThat(jdbc.queryForObject("SELECT status FROM viewing_record WHERE id = ?", String.class, viewing))
                .isEqualTo("WATCHED_CONFIRMED");
        assertThat(flavorAggregate("HEART", "rating_count")).isZero();
        assertThat(flavorAggregate("HEART", "rating_sum")).isZero();
        assertThat(jdbc.queryForObject("SELECT orphan_frame_count + orphan_popcorn_count FROM c1_projection_invariant", Long.class))
                .isZero();
        assertThat(count("""
                SELECT count(*) FROM user_behavior_event
                 WHERE jsonb_exists(payload, 'value')
                    OR jsonb_exists(payload, 'destination')
                    OR jsonb_exists(payload, 'url')
                """)).isZero();
        assertThat(count("SELECT count(*) FROM user_behavior_event"))
                .isEqualTo(count("SELECT count(*) FROM domain_outbox"));

        mvc.perform(put("/api/v1/me/ratings/{movieId}", TO_CONFIRM_MOVIE)
                        .header("Authorization", OWNER).header("Idempotency-Key", "rating-reactivate1")
                        .contentType(MediaType.APPLICATION_JSON).content("{\"value\":3}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.mutation").value("CREATED"))
                .andExpect(jsonPath("$.rating.ratingId").value(rating.toString()))
                .andExpect(jsonPath("$.rating.revision").value(4));
        assertThat(jdbc.queryForObject("SELECT logical_status FROM rating WHERE id = ?", String.class, rating))
                .isEqualTo("ACTIVE");
        assertThat(count("SELECT count(*) FROM frame WHERE rating_id = ?", rating)).isEqualTo(1);
        assertThat(count("SELECT count(*) FROM popcorn WHERE rating_id = ?", rating)).isEqualTo(1);
        assertThat(flavorAggregate("HEART", "rating_count")).isEqualTo(1);
        assertThat(flavorAggregate("HEART", "rating_sum")).isEqualTo(3);
    }

    @Test
    @Order(4)
    void concurrentDifferentKeysSerializeOnTheUserMovieDomainAndEachActualClickAddsOneEvent() throws Exception {
        // AC-C1-006 plus the partial-unique race behind BR-C1-012/023.
        UUID snapshot = UUID.randomUUID();
        UUID offer = UUID.randomUUID();
        jdbc.update("""
                INSERT INTO movie_availability_snapshot (
                    id, catalog_version_id, movie_id, region, fetch_status, source, aggregator_url,
                    fetched_at, fresh_until, serve_until
                ) VALUES (?, '10000000-0000-0000-0000-000000000002', ?, 'KR', 'SUCCESS_LISTED',
                          'C1_TEST', 'https://example.test/watch',
                          '2026-08-29T12:00:00Z', '2026-08-30T12:00:00Z', '2026-09-05T12:00:00Z')
                """, snapshot, CONCURRENT_MOVIE);
        jdbc.update("""
                INSERT INTO movie_ott_offer (
                    id, snapshot_id, provider_id, monetization_type, link_type, landing_url, source_display_priority
                ) VALUES (?, ?, 'd392a4d5-0428-4e06-aa41-aef899c06842', 'FLATRATE', 'DIRECT',
                          'https://example.test/watch', 1)
                """, offer, snapshot);

        CountDownLatch start = new CountDownLatch(1);
        var executor = Executors.newFixedThreadPool(2);
        try {
            Future<C1Service.HttpMutation> first = executor.submit(() -> {
                start.await();
                return c1Service.createWatchIntent(
                        OWNER_ID, "watch-race-key-0001",
                        new C1ApiDtos.CreateWatchIntentRequest(CONCURRENT_MOVIE, offer), "race-trace-one"
                );
            });
            Future<C1Service.HttpMutation> second = executor.submit(() -> {
                start.await();
                return c1Service.createWatchIntent(
                        OWNER_ID, "watch-race-key-0002",
                        new C1ApiDtos.CreateWatchIntentRequest(CONCURRENT_MOVIE, offer), "race-trace-two"
                );
            });
            start.countDown();
            assertThat(List.of(first.get().status(), second.get().status())).containsExactlyInAnyOrder(200, 201);
        } finally {
            executor.shutdownNow();
        }
        assertThat(count("SELECT count(*) FROM watch_intent WHERE user_id = ? AND movie_id = ?", OWNER_ID, CONCURRENT_MOVIE))
                .isEqualTo(1);
        assertThat(count("""
                SELECT count(*) FROM user_behavior_event
                 WHERE actor_user_id = ? AND event_type = 'OTT_LINK_CLICKED'
                   AND payload->>'movieId' = ?
                """, OWNER_ID, CONCURRENT_MOVIE.toString())).isEqualTo(2);
    }

    @Test
    @Order(5)
    void schedulerUsesInclusiveDueAndExclusiveExpiryWithoutInventingUserEvents() {
        // AC-C1-010 and BR-C1-050: system time transitions are not user behavior events.
        UUID dueIntent = UUID.randomUUID();
        UUID expiredIntent = UUID.randomUUID();
        UUID dueActor = UUID.randomUUID();
        UUID expiredActor = UUID.randomUUID();
        jdbc.update("""
                INSERT INTO watch_intent (
                    id, user_id, movie_id, provider_id, status, clicked_at,
                    confirmation_due_at, expires_at, revision
                ) VALUES (?, ?, ?, 'd392a4d5-0428-4e06-aa41-aef899c06842', 'LINK_CLICKED',
                          '2026-08-27T12:00:00Z', '2026-08-29T12:00:00Z', '2026-09-03T12:00:00Z', 1)
                """, dueIntent, dueActor, RATED_MOVIE);
        jdbc.update("""
                INSERT INTO watch_intent (
                    id, user_id, movie_id, provider_id, status, clicked_at,
                    confirmation_due_at, expires_at, revision
                ) VALUES (?, ?, ?, 'd392a4d5-0428-4e06-aa41-aef899c06842', 'LINK_CLICKED',
                          '2026-08-22T12:00:00Z', '2026-08-24T12:00:00Z', '2026-08-29T12:00:00Z', 1)
                """, expiredIntent, expiredActor, RATED_MOVIE);

        c1Service.advanceWatchIntents();
        assertThat(jdbc.queryForObject("SELECT status FROM watch_intent WHERE id = ?", String.class, dueIntent))
                .isEqualTo("CONFIRMATION_PENDING");
        assertThat(jdbc.queryForObject("SELECT revision FROM watch_intent WHERE id = ?", Integer.class, dueIntent))
                .isEqualTo(2);
        assertThat(jdbc.queryForObject("SELECT status FROM watch_intent WHERE id = ?", String.class, expiredIntent))
                .isEqualTo("EXPIRED");
        assertThat(jdbc.queryForObject("SELECT responded_at IS NOT NULL FROM watch_intent WHERE id = ?", Boolean.class, expiredIntent))
                .isTrue();
        assertThat(count("SELECT count(*) FROM user_behavior_event WHERE resource_id IN (?, ?)", dueIntent, expiredIntent))
                .isZero();
    }

    @Test
    @Order(6)
    void outboxRetriesAreIsolatedAndTheConsumerDeduplicatesByEventId() {
        // AC-C1-049: three downstream failures do not alter committed C1 state; recovery applies once.
        UUID eventId = jdbc.queryForObject("""
                SELECT event_id FROM domain_outbox
                 WHERE event_type = 'RATING_CREATED' AND status = 'PENDING'
                 ORDER BY occurred_at, event_id LIMIT 1
                """, UUID.class);
        int ratingsBefore = count("SELECT count(*) FROM rating WHERE user_id = ? AND logical_status = 'ACTIVE'", OWNER_ID);
        AtomicInteger calls = new AtomicInteger();
        Set<UUID> applied = ConcurrentHashMap.newKeySet();
        C1OutboxDispatcher.Consumer flaky = message -> {
            if (calls.incrementAndGet() <= 3) {
                throw new IllegalStateException("fixture consumer unavailable");
            }
            applied.add(message.eventId());
        };

        for (int attempt = 1; attempt <= 3; attempt++) {
            C1OutboxDispatcher.DispatchResult result = outboxDispatcher.dispatchOne(eventId, flaky);
            assertThat(result.status()).isEqualTo("FAILED");
            assertThat(result.attemptCount()).isEqualTo(attempt);
            assertThat(count("SELECT count(*) FROM rating WHERE user_id = ? AND logical_status = 'ACTIVE'", OWNER_ID))
                    .isEqualTo(ratingsBefore);
            jdbc.update("UPDATE domain_outbox SET next_attempt_at = NULL WHERE event_id = ?", eventId);
        }
        C1OutboxDispatcher.DispatchResult recovered = outboxDispatcher.dispatchOne(eventId, flaky);
        assertThat(recovered.status()).isEqualTo("PROCESSED");
        assertThat(recovered.attemptCount()).isEqualTo(4);
        assertThat(applied).containsExactly(eventId);
        assertThat(jdbc.queryForObject("SELECT status FROM domain_outbox WHERE event_id = ?", String.class, eventId))
                .isEqualTo("PROCESSED");
        assertThat(outboxDispatcher.dispatchOne(eventId, flaky).status()).isEqualTo("NOT_READY");
        assertThat(calls.get()).isEqualTo(4);
        assertThat(count("SELECT count(*) FROM rating WHERE user_id = ? AND logical_status = 'ACTIVE'", OWNER_ID))
                .isEqualTo(ratingsBefore);
    }

    private int count(String sql, Object... args) {
        Integer value = jdbc.queryForObject(sql, Integer.class, args);
        return value == null ? 0 : value;
    }

    private int flavorAggregate(String flavorCode, String column) {
        if (!("rating_count".equals(column) || "rating_sum".equals(column))) {
            throw new IllegalArgumentException("unsupported aggregate column");
        }
        Integer value = jdbc.queryForObject("""
                SELECT a.%s
                  FROM flavor_aggregate a JOIN popcorn_flavor f ON f.id = a.flavor_id
                 WHERE a.user_id = ? AND f.flavor_code = ?
                """.formatted(column), Integer.class, OWNER_ID, flavorCode);
        return value == null ? 0 : value;
    }
}
