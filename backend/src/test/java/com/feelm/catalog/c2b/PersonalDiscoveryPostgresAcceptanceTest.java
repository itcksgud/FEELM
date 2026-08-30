package com.feelm.catalog.c2b;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.feelm.catalog.c2.recommendation.RecommenderPort;
import com.feelm.catalog.c2b.service.PersonalDiscoveryRankPort;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.context.annotation.Primary;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("local")
@Testcontainers(disabledWithoutDocker = true)
@Import(PersonalDiscoveryPostgresAcceptanceTest.RankingConfiguration.class)
class PersonalDiscoveryPostgresAcceptanceTest {
    private static final UUID OWNER = UUID.fromString("5f93a51d-a6f1-41dc-8d86-6b570d53bd82");
    private static final String OWNER_TOKEN = "Bearer test-c1-other-token";
    private static final String OTHER_TOKEN = "Bearer test-c1-owner-token";
    private static final UUID PROVIDER = UUID.fromString("d392a4d5-0428-4e06-aa41-aef899c06842");
    private static final UUID M1 = UUID.fromString("6b226903-0ca4-4f5a-9bf0-50d6cedd224c");
    private static final UUID M2 = UUID.fromString("19406c31-213f-4fe1-93f6-109f8570ec20");
    private static final List<UUID> MOVIES = List.of(
            M1,
            M2,
            UUID.fromString("e8f7cf02-9bc4-4ff7-87b7-12fb02dd2490"),
            UUID.fromString("1958ba3a-3d8c-4a4f-8845-124c0b12373e"),
            UUID.fromString("0437c1c0-06d5-4cdf-a7d1-5d5f1dc42e89"),
            UUID.fromString("e67778c9-7b2e-42d4-9d3e-a3026b2efea3"),
            UUID.fromString("cc3ddb45-0511-46ea-bf28-95b67c9fd20f")
    );
    private static final UUID WATCH_M1 = UUID.fromString("d4219fb5-a672-4efb-ae4f-5b09a7b3d101");
    private static final UUID VIEW_M1 = UUID.fromString("d4219fb5-a672-4efb-ae4f-5b09a7b3d102");
    private static final UUID WATCH_M2 = UUID.fromString("d4219fb5-a672-4efb-ae4f-5b09a7b3d201");
    private static final UUID VIEW_M2 = UUID.fromString("d4219fb5-a672-4efb-ae4f-5b09a7b3d202");

    @Container
    static final PostgreSQLContainer<?> POSTGRES = new PostgreSQLContainer<>("postgres:17-alpine")
            .withDatabaseName("feelm_c2b_acceptance_test");

    @DynamicPropertySource
    static void configure(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", POSTGRES::getJdbcUrl);
        registry.add("spring.datasource.username", POSTGRES::getUsername);
        registry.add("spring.datasource.password", POSTGRES::getPassword);
        registry.add("catalog.c1.watch-intent-scheduler-delay-ms", () -> "3600000");
        registry.add("catalog.c1.outbox-worker.enabled", () -> "false");
    }

    @Autowired MockMvc mvc;
    @Autowired ObjectMapper json;
    @Autowired JdbcTemplate jdbc;
    @Autowired StubRanking ranking;

    @BeforeEach
    void reset() {
        dropFaultTrigger();
        jdbc.update("DELETE FROM recommendation_dismissal_event");
        jdbc.update("DELETE FROM recommendation_append_event");
        jdbc.update("DELETE FROM recommendation_delivery");
        jdbc.update("DELETE FROM idempotency_record WHERE actor_user_id = ?", OWNER);
        jdbc.update("DELETE FROM domain_outbox WHERE event_id IN (SELECT event_id FROM user_behavior_event WHERE actor_user_id = ?)", OWNER);
        jdbc.update("DELETE FROM user_behavior_event WHERE actor_user_id = ?", OWNER);
        jdbc.update("DELETE FROM rating_taste_contribution WHERE rating_id IN (SELECT id FROM rating WHERE user_id = ?)", OWNER);
        jdbc.update("DELETE FROM popcorn WHERE user_id = ?", OWNER);
        jdbc.update("DELETE FROM frame WHERE user_id = ?", OWNER);
        jdbc.update("DELETE FROM rating WHERE user_id = ?", OWNER);
        jdbc.update("DELETE FROM flavor_aggregate WHERE user_id = ?", OWNER);
        jdbc.update("DELETE FROM taste_aggregate WHERE user_id = ?", OWNER);
        jdbc.update("DELETE FROM viewing_record WHERE id IN (?, ?)", VIEW_M1, VIEW_M2);
        jdbc.update("DELETE FROM watch_intent WHERE id IN (?, ?)", WATCH_M1, WATCH_M2);
        ranking.movies(MOVIES);
    }

    @AfterEach
    void cleanFaultTrigger() {
        dropFaultTrigger();
    }

    @Test
    void requiresBearerForGetAppendAndDismiss() throws Exception {
        mvc.perform(get("/api/v1/me/recommendations/personal-discovery"))
                .andExpect(status().isUnauthorized());
        mvc.perform(post("/api/v1/me/recommendation-deliveries/{id}/append", UUID.randomUUID())
                        .header("Idempotency-Key", "append-auth-required")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"appendEventId":"82b9ba99-e23f-4cf3-bc72-ef0d3bf2b61a","expectedRevision":1,"cursor":"invalid-but-filtered"}
                                """))
                .andExpect(status().isUnauthorized());
        mvc.perform(post("/api/v1/me/recommendation-delivery-items/{id}/dismissals", UUID.randomUUID())
                        .header("Idempotency-Key", "dismiss-auth-required")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"dismissalEventId":"42b9ba99-e23f-4cf3-bc72-ef0d3bf2b61a","expectedRevision":1,"reason":"NOT_INTERESTED"}
                                """))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void createsThreeOnceAndAppendsAtMostThreeWithoutReplacingExistingItems() throws Exception {
        JsonNode initial = getCollection();
        assertThat(initial.path("items")).hasSize(3);
        assertThat(initial.path("deliveryRevision").asInt()).isEqualTo(1);
        assertThat(initial.path("label").asText()).isEqualTo("POPULARITY_BASELINE");
        assertThat(initial.path("composition").asText()).isEqualTo("BASELINE_THREE");
        assertThat(initial.has("personalization")).isFalse();
        JsonNode firstWireItem = initial.path("items").get(0);
        assertThat(firstWireItem.has("displayStatus")).isFalse();
        assertThat(firstWireItem.has("exposureStatus")).isFalse();
        assertThat(firstWireItem.has("recommendationItemId")).isFalse();
        assertThat(firstWireItem.has("expectedStar")).isFalse();
        assertThat(firstWireItem.has("reasons")).isFalse();
        List<String> originalIds = ids(initial.path("items"), "deliveryItemId");

        JsonNode reentry = getCollection();
        assertThat(ids(reentry.path("items"), "deliveryItemId")).containsExactlyElementsOf(originalIds);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM recommendation_delivery_item", Integer.class)).isEqualTo(3);

        UUID eventId = UUID.fromString("a7b6ed9c-5b48-461c-92a2-0bb75be8d601");
        String body = appendBody(eventId, initial);
        JsonNode appended = json.readTree(mvc.perform(post(
                                "/api/v1/me/recommendation-deliveries/{deliveryId}/append",
                                UUID.fromString(initial.path("deliveryId").asText()))
                        .header("Authorization", OWNER_TOKEN)
                        .header("Idempotency-Key", "cccccccccccccccc")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isCreated())
                .andExpect(header().string("Cache-Control", "no-store, private"))
                .andExpect(jsonPath("$.replayed").value(false))
                .andReturn().getResponse().getContentAsString());

        assertThat(appended.path("appendedItems")).hasSize(3);
        assertThat(appended.path("selectionSummary").path("selectedCount").asInt()).isEqualTo(3);
        assertThat(appended.path("outcome").asText()).isEqualTo("COMPLETE");
        assertThat(ids(appended.path("appendedItems"), "deliveryItemId"))
                .doesNotContainAnyElementsOf(originalIds);

        JsonNode cumulative = getCollection();
        assertThat(cumulative.path("items")).hasSize(6);
        assertThat(ids(cumulative.path("items"), "deliveryItemId").subList(0, 3))
                .containsExactlyElementsOf(originalIds);
        assertThat(positions(cumulative.path("items"))).containsExactly(1, 2, 3, 4, 5, 6);

        JsonNode replay = json.readTree(mvc.perform(post(
                                "/api/v1/me/recommendation-deliveries/{deliveryId}/append",
                                UUID.fromString(initial.path("deliveryId").asText()))
                        .header("Authorization", OWNER_TOKEN)
                        .header("Idempotency-Key", "cccccccccccccccc")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.replayed").value(true))
                .andReturn().getResponse().getContentAsString());
        assertThat(ids(replay.path("appendedItems"), "deliveryItemId"))
                .containsExactlyElementsOf(ids(appended.path("appendedItems"), "deliveryItemId"));
        assertThat(jdbc.queryForObject("SELECT count(*) FROM recommendation_delivery_item", Integer.class)).isEqualTo(6);

        mvc.perform(post("/api/v1/me/recommendation-deliveries/{deliveryId}/append",
                        UUID.fromString(initial.path("deliveryId").asText()))
                        .header("Authorization", OTHER_TOKEN)
                        .header("Idempotency-Key", "append-cross-owner-1")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isNotFound());
    }

    @Test
    void explicitNotInterestedRemovesOnlyThatCardAndIsIdempotent() throws Exception {
        JsonNode initial = getCollection();
        UUID itemId = UUID.fromString(initial.path("items").get(1).path("deliveryItemId").asText());
        UUID dismissal = UUID.fromString("4f19f9bb-dad1-42fc-99b0-ae7d0213c5de");
        String body = """
                {"dismissalEventId":"%s","expectedRevision":1,"reason":"NOT_INTERESTED"}
                """.formatted(dismissal);

        JsonNode response = json.readTree(mvc.perform(post(
                                "/api/v1/me/recommendation-delivery-items/{deliveryItemId}/dismissals", itemId)
                        .header("Authorization", OWNER_TOKEN)
                        .header("Idempotency-Key", "dismiss-not-interested-001")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.status").value("DISMISSED_NOT_INTERESTED"))
                .andExpect(jsonPath("$.replayed").value(false))
                .andReturn().getResponse().getContentAsString());
        assertThat(response.path("deliveryRevision").asInt()).isEqualTo(2);
        JsonNode remaining = getCollection();
        assertThat(remaining.path("items")).hasSize(2);
        assertThat(ids(remaining.path("items"), "deliveryItemId")).doesNotContain(itemId.toString());
        assertThat(positions(remaining.path("items"))).containsExactly(1, 3);

        mvc.perform(post("/api/v1/me/recommendation-delivery-items/{deliveryItemId}/dismissals", itemId)
                        .header("Authorization", OWNER_TOKEN)
                        .header("Idempotency-Key", "dismiss-not-interested-001")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.replayed").value(true));
        assertThat(jdbc.queryForObject("SELECT count(*) FROM recommendation_dismissal_event", Integer.class))
                .isEqualTo(1);

        mvc.perform(post("/api/v1/me/recommendation-delivery-items/{deliveryItemId}/dismissals",
                        UUID.fromString(initial.path("items").get(0).path("deliveryItemId").asText()))
                        .header("Authorization", OWNER_TOKEN)
                        .header("Idempotency-Key", "dismiss-invalid-reason")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"dismissalEventId":"f1bad451-a182-4642-9683-29e16be68223","expectedRevision":2,"reason":"LOW_RATING"}
                                """))
                .andExpect(status().isBadRequest());
        assertThat(jdbc.queryForObject("SELECT count(*) FROM recommendation_dismissal_event", Integer.class))
                .isEqualTo(1);
    }

    @Test
    void viewingOnlyKeepsCardButRatingCompletesItInTheC1TransactionAndDeleteDoesNotRestore() throws Exception {
        JsonNode initial = getCollection();
        UUID firstItem = UUID.fromString(initial.path("items").get(0).path("deliveryItemId").asText());
        UUID secondItem = UUID.fromString(initial.path("items").get(1).path("deliveryItemId").asText());
        addViewing(WATCH_M2, VIEW_M2, M2);
        assertThat(ids(getCollection().path("items"), "deliveryItemId")).contains(secondItem.toString());

        addViewing(WATCH_M1, VIEW_M1, M1);
        mvc.perform(put("/api/v1/me/ratings/{movieId}", M1)
                        .header("Authorization", OWNER_TOKEN)
                        .header("Idempotency-Key", "rating-completes-recommendation")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"value\":1,\"expectedRevision\":null}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.rating.value").value(1));

        JsonNode afterRating = getCollection();
        assertThat(ids(afterRating.path("items"), "deliveryItemId"))
                .doesNotContain(firstItem.toString())
                .contains(secondItem.toString());
        assertThat(jdbc.queryForObject("""
                SELECT status FROM recommendation_delivery_item WHERE id = ?
                """, String.class, firstItem)).isEqualTo("COMPLETED_RATED");
        assertThat(jdbc.queryForObject("""
                SELECT completion_rating_revision FROM recommendation_delivery_item WHERE id = ?
                """, Integer.class, firstItem)).isEqualTo(1);
        int revisionAfterCreate = jdbc.queryForObject("""
                SELECT revision FROM recommendation_delivery WHERE actor_user_id = ?
                """, Integer.class, OWNER);

        mvc.perform(put("/api/v1/me/ratings/{movieId}", M1)
                        .header("Authorization", OWNER_TOKEN)
                        .header("Idempotency-Key", "rating-update-does-not-recomplete")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"value\":5,\"expectedRevision\":1}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.rating.value").value(5));
        assertThat(jdbc.queryForObject("""
                SELECT revision FROM recommendation_delivery WHERE actor_user_id = ?
                """, Integer.class, OWNER)).isEqualTo(revisionAfterCreate);

        mvc.perform(delete("/api/v1/me/ratings/{movieId}", M1)
                        .header("Authorization", OWNER_TOKEN)
                        .header("Idempotency-Key", "rating-delete-no-card-restore")
                        .header("X-Expected-Revision", "2"))
                .andExpect(status().isOk());
        assertThat(ids(getCollection().path("items"), "deliveryItemId"))
                .doesNotContain(firstItem.toString());
    }

    @Test
    void ratingRollsBackWhenTheSameTransactionCannotCompleteRecommendationItem() throws Exception {
        JsonNode initial = getCollection();
        UUID firstItem = UUID.fromString(initial.path("items").get(0).path("deliveryItemId").asText());
        addViewing(WATCH_M1, VIEW_M1, M1);
        jdbc.execute("""
                CREATE OR REPLACE FUNCTION test_fail_c2b_completion() RETURNS trigger AS $$
                BEGIN
                    IF NEW.status = 'COMPLETED_RATED' THEN
                        RAISE EXCEPTION 'injected c2b completion failure';
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql
                """);
        jdbc.execute("""
                CREATE TRIGGER test_fail_c2b_completion_trigger
                BEFORE UPDATE ON recommendation_delivery_item
                FOR EACH ROW EXECUTE FUNCTION test_fail_c2b_completion()
                """);

        mvc.perform(put("/api/v1/me/ratings/{movieId}", M1)
                        .header("Authorization", OWNER_TOKEN)
                        .header("Idempotency-Key", "rating-c2b-atomic-rollback")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"value\":4,\"expectedRevision\":null}"))
                .andExpect(status().isServiceUnavailable());

        assertThat(jdbc.queryForObject("""
                SELECT count(*) FROM rating WHERE user_id = ? AND movie_id = ?
                """, Integer.class, OWNER, M1)).isZero();
        assertThat(jdbc.queryForObject("""
                SELECT status FROM recommendation_delivery_item WHERE id = ?
                """, String.class, firstItem)).isEqualTo("ACTIVE");
        assertThat(jdbc.queryForObject("""
                SELECT count(*) FROM idempotency_record
                 WHERE actor_user_id = ? AND operation_code = 'PUT_RATING'
                   AND idempotency_key = 'rating-c2b-atomic-rollback'
                """, Integer.class, OWNER)).isZero();
    }

    @Test
    void returnsNormalEmptyCollectionWhenEveryRankedCandidateIsAlreadySeen() throws Exception {
        ranking.movies(List.of(M1));
        addViewing(WATCH_M1, VIEW_M1, M1);

        JsonNode empty = getCollection();
        assertThat(empty.path("items")).isEmpty();
        assertThat(empty.path("pageInfo").path("activeItemCount").asInt()).isZero();
        assertThat(empty.path("pageInfo").path("hasMore").asBoolean()).isFalse();
        assertThat(jdbc.queryForObject("SELECT count(*) FROM recommendation_delivery", Integer.class)).isEqualTo(1);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM recommendation_delivery_item", Integer.class)).isZero();
    }

    private JsonNode getCollection() throws Exception {
        String body = mvc.perform(get("/api/v1/me/recommendations/personal-discovery")
                        .header("Authorization", OWNER_TOKEN))
                .andExpect(status().isOk())
                .andExpect(header().string("Cache-Control", "no-store, private"))
                .andReturn().getResponse().getContentAsString();
        return json.readTree(body);
    }

    private String appendBody(UUID eventId, JsonNode delivery) throws Exception {
        ObjectNode body = json.createObjectNode();
        body.put("appendEventId", eventId.toString());
        body.put("expectedRevision", delivery.path("deliveryRevision").asInt());
        body.put("cursor", delivery.path("pageInfo").path("nextCursor").asText());
        return json.writeValueAsString(body);
    }

    private void addViewing(UUID watchId, UUID viewingId, UUID movieId) {
        OffsetDateTime clicked = OffsetDateTime.parse("2026-08-20T01:00:00Z");
        jdbc.update("""
                INSERT INTO watch_intent (
                    id, user_id, movie_id, provider_id, source_offer_id, status,
                    clicked_at, confirmation_due_at, expires_at, responded_at, revision
                ) VALUES (?, ?, ?, ?, NULL, 'CONFIRMED_WATCHED', ?, ?, ?, ?, 2)
                """, watchId, OWNER, movieId, PROVIDER, clicked, clicked.plusHours(48),
                clicked.plusDays(7), clicked.plusDays(3));
        jdbc.update("""
                INSERT INTO viewing_record (
                    id, user_id, movie_id, source_watch_intent_id, provider_id,
                    status, watched_confirmed_at, revision
                ) VALUES (?, ?, ?, ?, ?, 'WATCHED_CONFIRMED', ?, 1)
                """, viewingId, OWNER, movieId, watchId, PROVIDER, clicked.plusDays(3));
    }

    private void dropFaultTrigger() {
        jdbc.execute("DROP TRIGGER IF EXISTS test_fail_c2b_completion_trigger ON recommendation_delivery_item");
        jdbc.execute("DROP FUNCTION IF EXISTS test_fail_c2b_completion()");
    }

    private List<String> ids(JsonNode array, String field) {
        List<String> result = new ArrayList<>();
        array.forEach(item -> result.add(item.path(field).asText()));
        return result;
    }

    private List<Integer> positions(JsonNode array) {
        List<Integer> result = new ArrayList<>();
        array.forEach(item -> result.add(item.path("position").asInt()));
        return result;
    }

    @TestConfiguration
    static class RankingConfiguration {
        @Bean
        @Primary
        StubRanking personalDiscoveryTestRanking() {
            return new StubRanking();
        }
    }

    static final class StubRanking implements PersonalDiscoveryRankPort {
        private volatile List<UUID> movies = MOVIES;

        void movies(List<UUID> value) {
            movies = List.copyOf(value);
        }

        @Override
        public RecommenderPort.Result rank(UUID actorUserId, UUID requestId) {
            ObjectMapper mapper = new ObjectMapper();
            ObjectNode snapshot = mapper.createObjectNode();
            snapshot.put("recommendationVersion", "c2b-local-baseline-v1");
            snapshot.put("policyVersion", "popularity-baseline-v1");
            snapshot.put("rankingPolicy", "BAYESIAN_POPULARITY_ONLY");
            snapshot.put("rankingAlpha", 0);
            snapshot.put("mappingVersion", "mapping-v1");
            snapshot.put("catalogVersion", "catalog-fixture-20260829-01");
            snapshot.put("candidateSetVersion", "candidate-set-c2b-local-v1");
            snapshot.put("inputVersion", "c2-active-rating-input-v1:sha256:" + "a".repeat(64));
            List<RecommenderPort.Item> items = new ArrayList<>();
            for (int index = 0; index < movies.size(); index++) {
                ObjectNode value = mapper.createObjectNode();
                ObjectNode star = value.putObject("expectedStar");
                star.put("status", "NOT_COMPUTED");
                star.putNull("value");
                star.put("displayEligible", false);
                star.put("confidence", "NOT_EVALUATED");
                star.putNull("confidencePolicyVersion");
                value.putArray("reasons");
                items.add(new RecommenderPort.Item(movies.get(index), index + 1, value));
            }
            return new RecommenderPort.Result(requestId, "COMPLETE", snapshot, items, List.of());
        }
    }
}
