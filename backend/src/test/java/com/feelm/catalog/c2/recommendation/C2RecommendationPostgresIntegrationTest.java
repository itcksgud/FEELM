package com.feelm.catalog.c2.recommendation;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.feelm.catalog.c1.api.C1ApiDtos;
import com.feelm.catalog.c1.foundation.C1OutboxDispatcher;
import com.feelm.catalog.c1.service.C1Service;
import com.feelm.catalog.c2.input.PostgresActiveRatingInputProjection;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.List;
import java.util.TreeMap;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest
@ActiveProfiles("local")
@Testcontainers(disabledWithoutDocker = true)
class C2RecommendationPostgresIntegrationTest {
    private static final UUID OWNER = UUID.fromString("018f6826-4da1-7c38-a846-8f794cd8b0cf");
    private static final UUID READY = UUID.fromString("6b226903-0ca4-4f5a-9bf0-50d6cedd224c");
    private static final UUID NOT_UI_READY = UUID.fromString("97204ea5-e6e5-4417-a13f-bc8197660705");
    private static final UUID REQUEST = UUID.fromString("a892ba87-b17c-48f3-996f-f999e5d03872");
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final AtomicReference<JsonNode> CAPTURED = new AtomicReference<>();
    private static final Path STORE;
    private static final HttpServer SERVER;

    static {
        try {
            STORE = Files.createTempDirectory("c2-candidate-integration-");
            publishStore(STORE);
            SERVER = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            SERVER.createContext("/internal/v1/recommendations/rank", exchange -> {
                JsonNode request = JSON.readTree(exchange.getRequestBody());
                CAPTURED.set(request);
                byte[] response = responseFor(request).getBytes(StandardCharsets.UTF_8);
                exchange.getResponseHeaders().add("Content-Type", "application/json");
                exchange.sendResponseHeaders(200, response.length);
                exchange.getResponseBody().write(response);
                exchange.close();
            });
            SERVER.start();
        } catch (Exception exception) {
            throw new ExceptionInInitializerError(exception);
        }
    }

    @Container
    static final PostgreSQLContainer<?> POSTGRES = new PostgreSQLContainer<>("postgres:17-alpine")
            .withDatabaseName("feelm_c2_recommendation_test");

    @DynamicPropertySource
    static void configure(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", POSTGRES::getJdbcUrl);
        registry.add("spring.datasource.username", POSTGRES::getUsername);
        registry.add("spring.datasource.password", POSTGRES::getPassword);
        registry.add("catalog.c1.watch-intent-scheduler-delay-ms", () -> "3600000");
        registry.add("catalog.c2.recommender.candidate-store-path", STORE::toString);
        registry.add("catalog.c2.recommender.base-url", () -> "http://127.0.0.1:" + SERVER.getAddress().getPort());
        registry.add("catalog.c2.recommender.auth-mode", () -> "fake");
        registry.add("catalog.c2.recommender.service-token", () -> "test-c2-service-token");
        registry.add("catalog.c2.recommender.timeout-ms", () -> "1000");
        registry.add("catalog.c2.recommender.local-fake-enabled", () -> "true");
    }

    @Autowired C1Service c1Service;
    @Autowired C1OutboxDispatcher dispatcher;
    @Autowired PostgresActiveRatingInputProjection projection;
    @Autowired JdbcTemplate jdbc;
    @Autowired InternalRecommendationService service;

    @AfterAll
    static void stopServer() {
        SERVER.stop(0);
    }

    @Test
    void usesPostgresCatalogAndConsistentRatingSnapshotWithoutSendingActorIdentity() {
        c1Service.putRating(
                OWNER, READY, "c2-client-integration-update", new C1ApiDtos.PutRatingRequest(5, 2),
                "c2-client-integration-trace"
        );
        UUID event = jdbc.queryForObject("""
                SELECT event_id FROM domain_outbox
                 WHERE event_type = 'RATING_UPDATED' AND status = 'PENDING'
                 ORDER BY occurred_at DESC LIMIT 1
                """, UUID.class);
        assertThat(dispatcher.dispatchOne(event, projection).status()).isEqualTo("PROCESSED");
        String projectedVersion = projection.findProjected(OWNER).orElseThrow().inputVersion();

        RecommenderPort.Result result = service.rank(OWNER, REQUEST);

        assertThat(result.snapshot().path("inputVersion").asText()).isEqualTo(projectedVersion);
        assertThat(result.snapshot().path("candidateSetVersion").asText())
                .isEqualTo(CAPTURED.get().path("candidateSet").path("candidateSetVersion").asText());
        assertThat(CAPTURED.get().path("candidateSet").path("movieIds")).hasSize(1);
        assertThat(CAPTURED.get().path("candidateSet").path("movieIds").get(0).asText())
                .isEqualTo(READY.toString());
        assertThat(CAPTURED.get().toString())
                .doesNotContain(OWNER.toString(), "userId", "email", "behavior", "token", "path");
    }

    private static String responseFor(JsonNode request) throws java.io.IOException {
        ObjectNode root = JSON.createObjectNode();
        root.put("requestId", request.path("requestId").asText());
        root.put("outcome", "COMPLETE");
        ObjectNode snapshot = root.putObject("snapshot");
        snapshot.put("recommendationVersion", "recommendation-v1-integration");
        snapshot.put("artifactSetVersion", "artifact-set-v1");
        snapshot.put("compatibilityId", "fixture-family-v1");
        snapshot.put("policyVersion", "policy-v1");
        snapshot.put("rankingPolicy", "BAYESIAN_POPULARITY_ONLY");
        snapshot.put("rankingAlpha", 0.0);
        snapshot.put("mappingVersion", "mapping-v1");
        snapshot.put("catalogVersion", "catalog-fixture-20260829-01");
        snapshot.put("candidateSetVersion", request.path("candidateSet").path("candidateSetVersion").asText());
        snapshot.put("inputVersion", request.path("preferenceInput").path("inputVersion").asText());
        ObjectNode versions = snapshot.putObject("modelVersions");
        ObjectNode checksums = snapshot.putObject("payloadChecksums");
        for (String key : List.of("bias", "factors", "calibration", "mapping")) {
            versions.put(key, key + "-v1");
            checksums.put(key, key.equals("mapping") ? "b".repeat(64) : "a".repeat(64));
        }
        ObjectNode item = root.putArray("items").addObject();
        item.put("movieId", READY.toString());
        item.put("rank", 1);
        item.put("rankingSource", "BAYESIAN_POPULARITY");
        ObjectNode star = item.putObject("expectedStar");
        star.put("status", "NOT_COMPUTED");
        star.putNull("value");
        star.put("displayEligible", false);
        star.put("confidence", "NOT_EVALUATED");
        star.putNull("confidencePolicyVersion");
        ObjectNode reason = item.putArray("reasons").addObject();
        reason.put("code", "POPULARITY_BASELINE");
        reason.put("reasonVersion", "policy-v1");
        reason.putObject("evidence").put("kind", "RANKING_POLICY").put("policyVersion", "policy-v1");
        root.putArray("issues");
        return JSON.writeValueAsString(root);
    }

    private static void publishStore(Path root) throws Exception {
        Files.createDirectories(root.resolve("versions"));
        TreeMap<String, Object> payload = new TreeMap<>();
        payload.put("candidateSetVersion", "");
        payload.put("catalogVersion", "catalog-fixture-20260829-01");
        payload.put("compatibilityId", "fixture-family-v1");
        payload.put("mappingPayloadSha256", "b".repeat(64));
        payload.put("movieIds", List.of(READY.toString(), NOT_UI_READY.toString()).stream().sorted().toList());
        payload.put("producerPolicy", "GLOBAL_VERIFIED_CATALOG_V1");
        payload.put("schemaVersion", 1);
        String version = "sha256:" + sha((JSON.writeValueAsString(payload) + "\n").getBytes(StandardCharsets.UTF_8));
        payload.put("candidateSetVersion", version);
        byte[] bytes = (JSON.writeValueAsString(payload) + "\n").getBytes(StandardCharsets.UTF_8);
        String digest = version.substring(7);
        Files.write(root.resolve("versions").resolve(digest + ".json"), bytes);
        TreeMap<String, Object> pointer = new TreeMap<>();
        pointer.put("candidateSetVersion", version);
        pointer.put("payload", "versions/" + digest + ".json");
        pointer.put("payloadSha256", sha(bytes));
        pointer.put("quarantine", "quarantine/" + digest + ".json");
        pointer.put("schemaVersion", 1);
        Files.writeString(root.resolve("active.json"), JSON.writeValueAsString(pointer) + "\n");
    }

    private static String sha(byte[] bytes) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
    }
}
