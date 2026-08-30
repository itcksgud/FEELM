package com.feelm.catalog.c1;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.feelm.catalog.c1.api.C1ApiDtos;
import com.feelm.catalog.c1.service.C1Service;
import com.feelm.catalog.c2.input.ActiveRatingInputPort;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.annotation.Transactional;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("local")
@Testcontainers(disabledWithoutDocker = true)
@Transactional
class C1ExplicitGapPostgresAcceptanceTest {
    private static final String OWNER = "Bearer test-c1-owner-token";
    private static final String OTHER = "Bearer test-c1-other-token";
    private static final UUID OWNER_ID = UUID.fromString("018f6826-4da1-7c38-a846-8f794cd8b0cf");
    private static final UUID OTHER_ID = UUID.fromString("5f93a51d-a6f1-41dc-8d86-6b570d53bd82");
    private static final UUID ACTIVE_CATALOG = UUID.fromString("10000000-0000-0000-0000-000000000002");
    private static final UUID PROVIDER = UUID.fromString("d392a4d5-0428-4e06-aa41-aef899c06842");
    private static final UUID MOVIE_RATED = UUID.fromString("6b226903-0ca4-4f5a-9bf0-50d6cedd224c");
    private static final UUID MOVIE_EN = UUID.fromString("19406c31-213f-4fe1-93f6-109f8570ec20");
    private static final UUID MOVIE_NONE = UUID.fromString("e8f7cf02-9bc4-4ff7-87b7-12fb02dd2490");
    private static final UUID MOVIE_UNKNOWN = UUID.fromString("1958ba3a-3d8c-4a4f-8845-124c0b12373e");
    private static final UUID MOVIE_STALE = UUID.fromString("0437c1c0-06d5-4cdf-a7d1-5d5f1dc42e89");
    private static final UUID MOVIE_INSIDE = UUID.fromString("e67778c9-7b2e-42d4-9d3e-a3026b2efea3");
    private static final UUID MOVIE_PRESTIGE = UUID.fromString("cc3ddb45-0511-46ea-bf28-95b67c9fd20f");
    private static final UUID OFFER_NETFLIX = UUID.fromString("4c411f48-9990-4938-9f6c-cf17b42ce4cb");
    private static final UUID OFFER_WATCHA = UUID.fromString("780702d1-a92d-4f78-9d0c-f327748b6281");
    private static final UUID OFFER_STALE = UUID.fromString("afaa874e-20d0-42de-a143-f89ee8f706d5");
    private static final OffsetDateTime NOW = OffsetDateTime.parse("2026-08-29T12:00:00Z");

    @Container
    static final PostgreSQLContainer<?> POSTGRES = new PostgreSQLContainer<>("postgres:17-alpine")
            .withDatabaseName("feelm_c1_explicit_gap_test");

    @DynamicPropertySource
    static void configure(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", POSTGRES::getJdbcUrl);
        registry.add("spring.datasource.username", POSTGRES::getUsername);
        registry.add("spring.datasource.password", POSTGRES::getPassword);
        registry.add("catalog.c1.watch-intent-scheduler-delay-ms", () -> "3600000");
    }

    @Autowired MockMvc mvc;
    @Autowired ObjectMapper objectMapper;
    @Autowired JdbcTemplate jdbc;
    @Autowired C1Service service;
    @Autowired ActiveRatingInputPort ratingInputs;

    @Test
    void invalidMovieAndUnverifiedOfferShareTheSameNotFoundAndPersistNothing() throws Exception {
        int intentsBefore = count("SELECT count(*) FROM watch_intent WHERE user_id = ?", OTHER_ID);
        int eventsBefore = count("SELECT count(*) FROM user_behavior_event WHERE actor_user_id = ?", OTHER_ID);

        jdbc.update("""
                UPDATE movie_catalog_projection SET visibility_status = 'UI_INCOMPLETE'
                 WHERE catalog_version_id = ? AND movie_id = ?
                """, ACTIVE_CATALOG, MOVIE_RATED);
        String hidden = mvc.perform(post("/api/v1/watch-intents")
                        .header("Authorization", OTHER)
                        .header("Idempotency-Key", "gap-invalid-movie-001")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"movieId\":\"%s\",\"offerId\":\"%s\"}"
                                .formatted(MOVIE_RATED, OFFER_NETFLIX)))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("RESOURCE_NOT_FOUND"))
                .andReturn().getResponse().getContentAsString();

        jdbc.update("""
                UPDATE movie_availability_snapshot
                   SET fetched_at = '2026-08-20T12:00:00Z',
                       fresh_until = '2026-08-21T12:00:00Z',
                       serve_until = '2026-08-27T12:00:00Z'
                 WHERE id = (SELECT snapshot_id FROM movie_ott_offer WHERE id = ?)
                """, OFFER_STALE);
        String invalidOffer = mvc.perform(post("/api/v1/watch-intents")
                        .header("Authorization", OTHER)
                        .header("Idempotency-Key", "bbbbbbbbbbbbbbbb")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"movieId\":\"%s\",\"offerId\":\"%s\"}"
                                .formatted(MOVIE_STALE, OFFER_STALE)))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("RESOURCE_NOT_FOUND"))
                .andReturn().getResponse().getContentAsString();

        assertThat(objectMapper.readTree(hidden).path("message").asText())
                .isEqualTo(objectMapper.readTree(invalidOffer).path("message").asText());
        assertThat(count("SELECT count(*) FROM watch_intent WHERE user_id = ?", OTHER_ID)).isEqualTo(intentsBefore);
        assertThat(count("SELECT count(*) FROM user_behavior_event WHERE actor_user_id = ?", OTHER_ID)).isEqualTo(eventsBefore);
        assertThat(count("SELECT count(*) FROM idempotency_record WHERE actor_user_id = ?", OTHER_ID)).isZero();
    }

    @Test
    void expiredIntentNeverCreatesPreferenceOrRecommendationInput() {
        UUID actor = UUID.randomUUID();
        insertTerminalIntent(actor, MOVIE_INSIDE, "EXPIRED", null);

        assertThat(service.pending(actor, null, 20).items()).isEmpty();
        assertThat(service.popcornBucket(actor).totalCount()).isZero();
        assertThat(service.tasteProfile(actor).items()).isEmpty();
        assertThat(ratingInputs.findProjected(actor)).isEmpty();
        assertThat(count("SELECT count(*) FROM viewing_record WHERE user_id = ?", actor)).isZero();
        assertThat(count("SELECT count(*) FROM rating WHERE user_id = ?", actor)).isZero();
        assertThat(count("""
                SELECT count(*) FROM rating_taste_contribution c
                JOIN rating r ON r.id = c.rating_id WHERE r.user_id = ?
                """, actor)).isZero();
        assertThat(count("SELECT count(*) FROM flavor_aggregate WHERE user_id = ?", actor)).isZero();
        assertThat(count("SELECT count(*) FROM taste_aggregate WHERE user_id = ?", actor)).isZero();
    }

    @Test
    void onboardingPreferenceEventsStayOutsideRatingAggregates() {
        UUID actor = UUID.randomUUID();
        C1ApiDtos.PopcornBucket beforeBucket = service.popcornBucket(actor);
        C1ApiDtos.TasteProfile beforeTaste = service.tasteProfile(actor);

        insertOnboardingSignal(actor, MOVIE_RATED, "LIKE");
        insertOnboardingSignal(actor, MOVIE_EN, "DISLIKE");

        assertThat(service.popcornBucket(actor)).isEqualTo(beforeBucket);
        assertThat(service.tasteProfile(actor)).isEqualTo(beforeTaste);
        assertThat(ratingInputs.findProjected(actor)).isEmpty();
        assertThat(count("SELECT count(*) FROM rating WHERE user_id = ?", actor)).isZero();
        assertThat(count("SELECT count(*) FROM rating_taste_contribution c JOIN rating r ON r.id = c.rating_id WHERE r.user_id = ?", actor)).isZero();
        assertThat(count("SELECT count(*) FROM flavor_aggregate WHERE user_id = ?", actor)).isZero();
        assertThat(count("SELECT count(*) FROM taste_aggregate WHERE user_id = ?", actor)).isZero();
    }

    @Test
    void missingFlavorAssignmentRejectsRatingBeforeAnyPartialWrite() throws Exception {
        insertViewing(OTHER_ID, MOVIE_INSIDE);
        jdbc.update("DELETE FROM movie_flavor_assignment WHERE mapping_version = 'v1' AND movie_id = ?", MOVIE_INSIDE);

        mvc.perform(put("/api/v1/me/ratings/{movieId}", MOVIE_INSIDE)
                        .header("Authorization", OTHER)
                        .header("Idempotency-Key", "gap-no-flavor-0001")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"value\":4}"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("FLAVOR_ASSIGNMENT_REQUIRED"));

        assertThat(count("SELECT count(*) FROM rating WHERE user_id = ? AND movie_id = ?", OTHER_ID, MOVIE_INSIDE)).isZero();
        assertThat(count("SELECT count(*) FROM frame WHERE user_id = ? AND movie_id = ?", OTHER_ID, MOVIE_INSIDE)).isZero();
        assertThat(count("SELECT count(*) FROM popcorn WHERE user_id = ?", OTHER_ID)).isZero();
        assertThat(count("SELECT count(*) FROM flavor_aggregate WHERE user_id = ?", OTHER_ID)).isZero();
        assertThat(count("SELECT count(*) FROM taste_aggregate WHERE user_id = ?", OTHER_ID)).isZero();
        assertThat(count("SELECT count(*) FROM user_behavior_event WHERE actor_user_id = ?", OTHER_ID)).isZero();
        assertThat(count("SELECT count(*) FROM idempotency_record WHERE actor_user_id = ?", OTHER_ID)).isZero();
    }

    @Test
    void filmCursorTraversesTimestampAndMovieTieBreakWithoutDuplicates() {
        createRating(OTHER_ID, MOVIE_NONE, 3, "gap-film-none-001");
        createRating(OTHER_ID, MOVIE_INSIDE, 4, "gap-film-inside-1");
        createRating(OTHER_ID, MOVIE_PRESTIGE, 5, "gap-film-prestige");
        jdbc.update("UPDATE frame SET created_at = '2026-08-29T12:00:00Z' WHERE user_id = ? AND movie_id = ?", OTHER_ID, MOVIE_NONE);
        jdbc.update("UPDATE frame SET created_at = '2026-08-29T11:00:00Z' WHERE user_id = ? AND movie_id IN (?, ?)", OTHER_ID, MOVIE_INSIDE, MOVIE_PRESTIGE);

        List<UUID> traversed = new ArrayList<>();
        String cursor = null;
        for (int pageNumber = 0; pageNumber < 3; pageNumber++) {
            C1ApiDtos.FilmPage page = service.film(OTHER_ID, cursor, 1);
            assertThat(page.totalCount()).isEqualTo(3);
            assertThat(page.items()).hasSize(1);
            traversed.add(page.items().get(0).movie().movieId());
            assertThat(page.hasNext()).isEqualTo(pageNumber < 2);
            cursor = page.nextCursor();
        }

        C1ApiDtos.FilmPage replay = service.film(OTHER_ID, null, 1);
        assertThat(traversed).containsExactly(MOVIE_NONE, MOVIE_PRESTIGE, MOVIE_INSIDE);
        assertThat(new HashSet<>(traversed)).hasSize(3);
        assertThat(replay.items().get(0).movie().movieId()).isEqualTo(MOVIE_NONE);
        assertThat(replay.nextCursor()).isNotBlank();
    }

    @Test
    void catalogVersionSwapDoesNotRewriteExistingTasteContributionsOrAggregates() {
        C1ApiDtos.PopcornBucket beforeBucket = service.popcornBucket(OWNER_ID);
        C1ApiDtos.TasteProfile beforeTaste = service.tasteProfile(OWNER_ID);
        C1ApiDtos.FilmPage beforeFilm = service.film(OWNER_ID, null, 20);
        List<Map<String, Object>> beforeContributions = contributionSnapshot();
        List<Map<String, Object>> beforeAggregates = aggregateSnapshot();
        UUID nextVersion = UUID.randomUUID();
        UUID syncRun = UUID.randomUUID();

        jdbc.update("""
                INSERT INTO catalog_sync_run (id, job_type, status, started_at, finished_at, source_version, metrics)
                VALUES (?, 'TEST_VERSION_SWAP', 'SUCCEEDED', ?, ?, 'gap-catalog-v2', '{}'::jsonb)
                """, syncRun, NOW, NOW);
        jdbc.update("""
                INSERT INTO catalog_version (id, public_version, sync_run_id, status, published_at, source_hash)
                VALUES (?, 'catalog-gap-20260829-02', ?, 'STAGING', NULL, 'gap-catalog-v2-hash')
                """, nextVersion, syncRun);
        copyCatalogRelations(ACTIVE_CATALOG, nextVersion);
        jdbc.update("UPDATE catalog_version SET status = 'RETIRED' WHERE id = ?", ACTIVE_CATALOG);
        jdbc.update("UPDATE catalog_version SET status = 'ACTIVE', published_at = ? WHERE id = ?", NOW, nextVersion);

        assertThat(service.popcornBucket(OWNER_ID)).isEqualTo(beforeBucket);
        assertThat(service.tasteProfile(OWNER_ID)).isEqualTo(beforeTaste);
        assertThat(service.film(OWNER_ID, null, 20)).isEqualTo(beforeFilm);
        assertThat(contributionSnapshot()).isEqualTo(beforeContributions);
        assertThat(aggregateSnapshot()).isEqualTo(beforeAggregates);
        assertThat(jdbc.queryForObject("SELECT catalog_version_id FROM rating_taste_contribution LIMIT 1", UUID.class))
                .isEqualTo(ACTIVE_CATALOG);
    }

    @Test
    void behaviorEventsPersistOnlyThePerTypePayloadAllowlist() throws Exception {
        mvc.perform(post("/api/v1/watch-intents")
                        .header("Authorization", OTHER)
                        .header("Idempotency-Key", "gap-event-click-001")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"movieId\":\"%s\",\"offerId\":\"%s\"}".formatted(MOVIE_EN, OFFER_WATCHA)))
                .andExpect(status().isCreated());

        UUID confirmable = insertConfirmableIntent(OTHER_ID, MOVIE_NONE);
        mvc.perform(post("/api/v1/watch-intents/{watchIntentId}/confirmation", confirmable)
                        .header("Authorization", OTHER)
                        .header("Idempotency-Key", "gap-event-confirm1")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"watched\":false,\"expectedRevision\":1}"))
                .andExpect(status().isOk());

        insertViewing(OTHER_ID, MOVIE_INSIDE);
        mvc.perform(put("/api/v1/me/ratings/{movieId}", MOVIE_INSIDE)
                        .header("Authorization", OTHER).header("Idempotency-Key", "gap-event-rate-001")
                        .contentType(MediaType.APPLICATION_JSON).content("{\"value\":3}"))
                .andExpect(status().isOk());
        mvc.perform(put("/api/v1/me/ratings/{movieId}", MOVIE_INSIDE)
                        .header("Authorization", OTHER).header("Idempotency-Key", "gap-event-rate-002")
                        .contentType(MediaType.APPLICATION_JSON).content("{\"value\":4,\"expectedRevision\":1}"))
                .andExpect(status().isOk());
        mvc.perform(delete("/api/v1/me/ratings/{movieId}", MOVIE_INSIDE)
                        .header("Authorization", OTHER).header("Idempotency-Key", "gap-event-rate-003")
                        .header("X-Expected-Revision", "2"))
                .andExpect(status().isOk());

        List<EventRow> events = jdbc.query("""
                SELECT e.event_type, e.payload::text, o.payload::text AS outbox_payload
                  FROM user_behavior_event e JOIN domain_outbox o ON o.event_id = e.event_id
                 WHERE e.actor_user_id = ? ORDER BY e.event_type
                """, (rs, row) -> new EventRow(
                rs.getString("event_type"), rs.getString("payload"), rs.getString("outbox_payload")
        ), OTHER_ID);
        assertThat(events).hasSize(5);
        assertThat(events.stream().map(EventRow::eventType).collect(java.util.stream.Collectors.toSet()))
                .containsExactlyInAnyOrder(
                        "OTT_LINK_CLICKED", "WATCH_CONFIRMATION_RESPONDED",
                        "RATING_CREATED", "RATING_UPDATED", "RATING_DELETED"
                );
        Map<String, Set<String>> expectedFields = Map.of(
                "OTT_LINK_CLICKED", Set.of("movieId", "providerId", "linkType"),
                "WATCH_CONFIRMATION_RESPONDED", Set.of("movieId", "watched"),
                "RATING_CREATED", Set.of("movieId", "ratingRevision"),
                "RATING_UPDATED", Set.of("movieId", "ratingRevision"),
                "RATING_DELETED", Set.of("movieId", "ratingRevision")
        );
        for (EventRow event : events) {
            JsonNode payload = objectMapper.readTree(event.payload());
            Set<String> actual = new HashSet<>();
            payload.fieldNames().forEachRemaining(actual::add);
            assertThat(actual).isEqualTo(expectedFields.get(event.eventType()));
            assertThat(objectMapper.readTree(event.outboxPayload())).isEqualTo(payload);
            String serialized = event.payload().toLowerCase();
            assertThat(serialized).doesNotContain(
                    "bearer", "test-c1", "http://", "https://", "destination", "freetext", "\"value\""
            );
        }
    }

    @Test
    void newClickAfterTerminalNotWatchedOrExpiredCreatesFreshIntentWindows() throws Exception {
        List<TerminalCase> cases = List.of(
                new TerminalCase(UUID.randomUUID(), MOVIE_RATED, OFFER_NETFLIX, "CONFIRMED_NOT_WATCHED"),
                new TerminalCase(UUID.randomUUID(), MOVIE_EN, OFFER_WATCHA, "EXPIRED")
        );
        for (int index = 0; index < cases.size(); index++) {
            TerminalCase fixture = cases.get(index);
            UUID oldIntent = insertTerminalIntent(fixture.actor(), fixture.movie(), fixture.status(), fixture.offer());
            C1Service.HttpMutation mutation = service.createWatchIntent(
                    fixture.actor(), "gap-terminal-click-00" + index,
                    new C1ApiDtos.CreateWatchIntentRequest(fixture.movie(), fixture.offer()),
                    "gap-terminal-trace-" + index
            );
            C1ApiDtos.WatchIntentClickResult result = objectMapper.treeToValue(
                    mutation.body(), C1ApiDtos.WatchIntentClickResult.class
            );

            assertThat(mutation.status()).isEqualTo(201);
            assertThat(result.outcome()).isEqualTo("CREATED");
            assertThat(result.watchIntent()).isNotNull();
            assertThat(result.watchIntent().watchIntentId()).isNotEqualTo(oldIntent);
            assertThat(result.watchIntent().clickedAt()).isEqualTo(NOW);
            assertThat(result.watchIntent().confirmationDueAt()).isEqualTo(NOW.plusHours(48));
            assertThat(result.watchIntent().expiresAt()).isEqualTo(NOW.plusDays(7));
            assertThat(count("SELECT count(*) FROM watch_intent WHERE user_id = ? AND movie_id = ?", fixture.actor(), fixture.movie()))
                    .isEqualTo(2);
            assertThat(count("SELECT count(*) FROM user_behavior_event WHERE actor_user_id = ? AND event_type = 'OTT_LINK_CLICKED'", fixture.actor()))
                    .isEqualTo(1);
        }
    }

    private void createRating(UUID actor, UUID movie, int value, String key) {
        insertViewing(actor, movie);
        C1Service.HttpMutation result = service.putRating(
                actor, movie, key, new C1ApiDtos.PutRatingRequest(value, null), "gap-film-trace"
        );
        assertThat(result.status()).isEqualTo(200);
    }

    private UUID insertViewing(UUID actor, UUID movie) {
        UUID intent = insertTerminalIntent(actor, movie, "CONFIRMED_WATCHED", null);
        UUID viewing = UUID.randomUUID();
        jdbc.update("""
                INSERT INTO viewing_record (
                    id, user_id, movie_id, source_watch_intent_id, provider_id,
                    status, watched_confirmed_at, revision
                ) VALUES (?, ?, ?, ?, ?, 'WATCHED_CONFIRMED', ?, 1)
                """, viewing, actor, movie, intent, PROVIDER, NOW.minusDays(1));
        return viewing;
    }

    private UUID insertConfirmableIntent(UUID actor, UUID movie) {
        UUID id = UUID.randomUUID();
        OffsetDateTime clicked = NOW.minusDays(2).minusHours(1);
        jdbc.update("""
                INSERT INTO watch_intent (
                    id, user_id, movie_id, provider_id, status, clicked_at,
                    confirmation_due_at, expires_at, responded_at, revision
                ) VALUES (?, ?, ?, ?, 'CONFIRMATION_PENDING', ?, ?, ?, NULL, 1)
                """, id, actor, movie, PROVIDER, clicked, clicked.plusHours(48), clicked.plusDays(7));
        return id;
    }

    private UUID insertTerminalIntent(UUID actor, UUID movie, String status, UUID offer) {
        UUID id = UUID.randomUUID();
        OffsetDateTime clicked = NOW.minusDays(10);
        jdbc.update("""
                INSERT INTO watch_intent (
                    id, user_id, movie_id, provider_id, source_offer_id, status,
                    clicked_at, confirmation_due_at, expires_at, responded_at, revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 2)
                """, id, actor, movie, PROVIDER, offer, status, clicked,
                clicked.plusHours(48), clicked.plusDays(7), clicked.plusDays(3));
        return id;
    }

    private void insertOnboardingSignal(UUID actor, UUID movie, String sentiment) {
        jdbc.update("""
                INSERT INTO domain_outbox (
                    event_id, aggregate_type, aggregate_id, event_type, schema_version,
                    payload, occurred_at, status, attempt_count
                ) VALUES (?, 'ONBOARDING_PREFERENCE', ?, 'ONBOARDING_PREFERENCE_SET', 1,
                          jsonb_build_object('movieId', ?, 'sentiment', ?), ?, 'PENDING', 0)
                """, UUID.randomUUID(), movie, movie.toString(), sentiment, NOW);
    }

    private void copyCatalogRelations(UUID source, UUID target) {
        jdbc.update("""
                INSERT INTO movie_catalog_projection (
                    catalog_version_id, movie_id, media_type, identity_status, visibility_status,
                    original_title, original_language, release_date, runtime_minutes, poster_path,
                    backdrop_path, tmdb_vote_average, tmdb_vote_count, metadata_fetched_at, deleted
                ) SELECT ?, movie_id, media_type, identity_status, visibility_status,
                         original_title, original_language, release_date, runtime_minutes, poster_path,
                         backdrop_path, tmdb_vote_average, tmdb_vote_count, metadata_fetched_at, deleted
                    FROM movie_catalog_projection WHERE catalog_version_id = ?
                """, target, source);
        jdbc.update("""
                INSERT INTO movie_localization (catalog_version_id, movie_id, locale, title, overview, source, fetched_at)
                SELECT ?, movie_id, locale, title, overview, source, fetched_at
                  FROM movie_localization WHERE catalog_version_id = ?
                """, target, source);
        jdbc.update("""
                INSERT INTO movie_genre (catalog_version_id, movie_id, genre_id, display_order)
                SELECT ?, movie_id, genre_id, display_order FROM movie_genre WHERE catalog_version_id = ?
                """, target, source);
        jdbc.update("""
                INSERT INTO movie_country (catalog_version_id, movie_id, country_code, display_order)
                SELECT ?, movie_id, country_code, display_order FROM movie_country WHERE catalog_version_id = ?
                """, target, source);
        jdbc.update("""
                INSERT INTO movie_credit (
                    catalog_version_id, movie_id, person_id, credit_type, job, character_name, credit_order
                ) SELECT ?, movie_id, person_id, credit_type, job, character_name, credit_order
                    FROM movie_credit WHERE catalog_version_id = ?
                """, target, source);
    }

    private List<Map<String, Object>> contributionSnapshot() {
        return jdbc.queryForList("""
                SELECT rating_id, dimension_type, dimension_key, rating_value,
                       catalog_version_id, flavor_mapping_version, derivation_version
                  FROM rating_taste_contribution ORDER BY rating_id, dimension_type, dimension_key
                """);
    }

    private List<Map<String, Object>> aggregateSnapshot() {
        List<Map<String, Object>> rows = new ArrayList<>(jdbc.queryForList("""
                SELECT 'FLAVOR' AS kind, flavor_id::text AS dimension_key,
                       rating_count, rating_sum, revision
                  FROM flavor_aggregate WHERE user_id = ?
                """, OWNER_ID));
        rows.addAll(jdbc.queryForList("""
                SELECT 'TASTE:' || dimension_type AS kind, dimension_key,
                       rating_count, rating_sum, revision
                  FROM taste_aggregate WHERE user_id = ?
                """, OWNER_ID));
        rows.sort(java.util.Comparator.comparing(row -> row.get("kind") + ":" + row.get("dimension_key")));
        return rows;
    }

    private int count(String sql, Object... arguments) {
        Integer result = jdbc.queryForObject(sql, Integer.class, arguments);
        return result == null ? 0 : result;
    }

    private record EventRow(String eventType, String payload, String outboxPayload) {
    }

    private record TerminalCase(UUID actor, UUID movie, UUID offer, String status) {
    }
}
