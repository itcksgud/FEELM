package com.feelm.catalog.c2.recommendation;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.feelm.catalog.c2.input.ActiveRatingInputPort;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;

import java.net.InetSocketAddress;
import java.net.http.HttpClient;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Consumer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class JdkRecommenderClientTest {
    private static final UUID REQUEST = UUID.fromString("a892ba87-b17c-48f3-996f-f999e5d03872");
    private static final UUID MOVIE = UUID.fromString("6b226903-0ca4-4f5a-9bf0-50d6cedd224c");
    private final ObjectMapper mapper = new ObjectMapper();
    private HttpServer server;

    @AfterEach
    void stop() {
        if (server != null) server.stop(0);
    }

    @Test
    void sendsOnlyApprovedFieldsAndPreservesSnapshotExactly() throws Exception {
        AtomicReference<JsonNode> captured = new AtomicReference<>();
        start(200, validResponse(null), exchange -> {
            try {
                assertThat(exchange.getRequestHeaders().getFirst("Authorization"))
                        .isEqualTo("Bearer test-c2-service-token");
                captured.set(mapper.readTree(exchange.getRequestBody()));
            } catch (Exception exception) {
                throw new RuntimeException(exception);
            }
        });
        RecommenderPort.Result result = client().rank(command());
        assertThat(result.snapshot().path("candidateSetVersion").asText()).isEqualTo("candidate-v1");
        assertThat(result.snapshot().path("inputVersion").asText()).isEqualTo("input-v1");
        String requestJson = captured.get().toString();
        assertThat(requestJson).doesNotContain("userId", "email", "behavior", "actor", "path", "token");
        assertThat(captured.get().path("preferenceInput").path("ratings").get(0).path("revision").asInt())
                .isEqualTo(2);
    }

    @Test
    void mapsAuthenticationAndAvailabilityStatusesToTypedFailures() throws Exception {
        for (int status : List.of(401, 403, 503)) {
            stop();
            start(status, "{}", ignored -> { });
            C2RecommendationFailure.Code expected = switch (status) {
                case 401 -> C2RecommendationFailure.Code.AUTH_REQUIRED;
                case 403 -> C2RecommendationFailure.Code.AUTH_FORBIDDEN;
                default -> C2RecommendationFailure.Code.SERVICE_UNAVAILABLE;
            };
            assertThatThrownBy(() -> client().rank(command()))
                    .isInstanceOfSatisfying(C2RecommendationFailure.class,
                            failure -> assertThat(failure.code()).isEqualTo(expected));
        }
    }

    @Test
    void mapsDeadlineAndConnectionFailuresWithoutReturningAStaleResult() throws Exception {
        start(200, validResponse(null), ignored -> {
            try {
                Thread.sleep(150);
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
            }
        });
        JdkRecommenderClient deadline = new JdkRecommenderClient(
                mapper, HttpClient.newHttpClient(),
                "http://127.0.0.1:" + server.getAddress().getPort(),
                "fake", "test-c2-service-token", 20, true
        );
        assertThatThrownBy(() -> deadline.rank(command()))
                .isInstanceOfSatisfying(C2RecommendationFailure.class,
                        failure -> assertThat(failure.code())
                                .isEqualTo(C2RecommendationFailure.Code.DEADLINE_EXCEEDED));

        JdkRecommenderClient connection = new JdkRecommenderClient(
                mapper, HttpClient.newHttpClient(), "http://127.0.0.1:1",
                "fake", "test-c2-service-token", 100, true
        );
        assertThatThrownBy(() -> connection.rank(command()))
                .isInstanceOfSatisfying(C2RecommendationFailure.class,
                        failure -> assertThat(failure.code())
                                .isEqualTo(C2RecommendationFailure.Code.CONNECTION_FAILURE));
    }

    @Test
    void malformed200IsInvalidResponseAndExtraSensitiveFieldsCannotCrossAdapter() throws Exception {
        List<Consumer<ObjectNode>> attacks = List.of(
                root -> root.put("userId", UUID.randomUUID().toString()),
                root -> ((ObjectNode) root.path("items").get(0)).put("path", "C:/secret"),
                root -> ((ObjectNode) root.path("items").get(0).path("expectedStar")).put("token", "secret"),
                root -> ((com.fasterxml.jackson.databind.node.ArrayNode) root.path("issues"))
                        .addObject().put("token", "secret"),
                root -> ((ObjectNode) root.path("snapshot").path("payloadChecksums")).put("user", "secret")
        );
        for (Consumer<ObjectNode> attack : attacks) {
            stop();
            start(200, validResponse(attack), ignored -> { });
            assertThatThrownBy(() -> client().rank(command()))
                    .isInstanceOfSatisfying(C2RecommendationFailure.class,
                            failure -> assertThat(failure.code())
                                    .isEqualTo(C2RecommendationFailure.Code.INVALID_RESPONSE));
        }
    }

    @Test
    void rejectsOutcomeIssueAndScopeCodeContradictions() throws Exception {
        List<Consumer<ObjectNode>> contradictions = List.of(
                root -> addIssue(root, "CANDIDATE", "SERVICE_ID_NOT_MAPPED", MOVIE),
                root -> root.put("outcome", "PARTIAL"),
                root -> {
                    root.put("outcome", "EMPTY");
                    root.withArray("items").removeAll();
                },
                root -> {
                    root.put("outcome", "PARTIAL");
                    root.withArray("items").removeAll();
                    addIssue(root, "CANDIDATE", "SERVICE_ID_NOT_MAPPED", MOVIE);
                },
                root -> {
                    root.put("outcome", "PARTIAL");
                    addIssue(root, "CANDIDATE", "STAR_HEAD_UNAVAILABLE", MOVIE);
                },
                root -> {
                    root.put("outcome", "PARTIAL");
                    addIssue(root, "STAR_HEAD", "SERVICE_ID_NOT_MAPPED", null);
                },
                root -> {
                    root.put("outcome", "PARTIAL");
                    addIssue(root, "STAR_HEAD", "STAR_HEAD_UNAVAILABLE", null);
                }
        );
        for (Consumer<ObjectNode> contradiction : contradictions) {
            stop();
            start(200, validResponse(contradiction), ignored -> { });
            assertThatThrownBy(() -> client().rank(command()))
                    .isInstanceOfSatisfying(C2RecommendationFailure.class,
                            failure -> assertThat(failure.code())
                                    .isEqualTo(C2RecommendationFailure.Code.INVALID_RESPONSE));
        }
    }

    @Test
    void configurationIsFailClosedUnlessLocalFakeModeIsExplicit() {
        JdkRecommenderClient disabled = new JdkRecommenderClient(
                mapper, HttpClient.newHttpClient(), "http://127.0.0.1:1", "", "", 200, false
        );
        assertThatThrownBy(() -> disabled.rank(command()))
                .isInstanceOfSatisfying(C2RecommendationFailure.class,
                        failure -> assertThat(failure.code())
                                .isEqualTo(C2RecommendationFailure.Code.CONFIGURATION_UNAVAILABLE));
    }

    private void start(int status, String response, Consumer<com.sun.net.httpserver.HttpExchange> inspect)
            throws Exception {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/internal/v1/recommendations/rank", exchange -> {
            inspect.accept(exchange);
            byte[] bytes = response.getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().add("Content-Type", "application/json");
            exchange.sendResponseHeaders(status, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        });
        server.start();
    }

    private JdkRecommenderClient client() {
        return new JdkRecommenderClient(
                mapper, HttpClient.newHttpClient(),
                "http://127.0.0.1:" + server.getAddress().getPort(),
                "fake", "test-c2-service-token", 1000, true
        );
    }

    private void addIssue(ObjectNode root, String scope, String code, UUID movieId) {
        ObjectNode issue = root.withArray("issues").addObject();
        issue.put("scope", scope);
        issue.put("code", code);
        if (movieId == null) issue.putNull("movieId");
        else issue.put("movieId", movieId.toString());
        issue.put("retriable", false);
    }

    private RecommenderPort.Command command() {
        CandidateSetPort.Snapshot candidates = new CandidateSetPort.Snapshot(
                "candidate-v1", "catalog-fixture-20260829-01", "b".repeat(64),
                "fixture-family-v1", List.of(MOVIE)
        );
        ActiveRatingInputPort.Snapshot ratings = new ActiveRatingInputPort.Snapshot(
                "input-v1", List.of(new ActiveRatingInputPort.RatingInput(MOVIE, 4, 2))
        );
        return new RecommenderPort.Command(REQUEST, candidates, List.of(MOVIE), ratings);
    }

    private String validResponse(Consumer<ObjectNode> mutate) throws Exception {
        ObjectNode root = mapper.createObjectNode();
        root.put("requestId", REQUEST.toString());
        root.put("outcome", "COMPLETE");
        ObjectNode snapshot = root.putObject("snapshot");
        snapshot.put("recommendationVersion", "recommendation-v1-fixture");
        snapshot.put("artifactSetVersion", "artifact-set-v1");
        snapshot.put("compatibilityId", "fixture-family-v1");
        snapshot.put("policyVersion", "policy-v1");
        snapshot.put("rankingPolicy", "BAYESIAN_POPULARITY_ONLY");
        snapshot.put("rankingAlpha", 0.0);
        snapshot.put("mappingVersion", "mapping-v1");
        snapshot.put("catalogVersion", "catalog-fixture-20260829-01");
        snapshot.put("candidateSetVersion", "candidate-v1");
        snapshot.put("inputVersion", "input-v1");
        ObjectNode versions = snapshot.putObject("modelVersions");
        ObjectNode checksums = snapshot.putObject("payloadChecksums");
        for (String key : List.of("bias", "factors", "calibration", "mapping")) {
            versions.put(key, key + "-v1");
            checksums.put(key, key.equals("mapping") ? "b".repeat(64) : "a".repeat(64));
        }
        ObjectNode item = root.putArray("items").addObject();
        item.put("movieId", MOVIE.toString());
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
        ObjectNode evidence = reason.putObject("evidence");
        evidence.put("kind", "RANKING_POLICY");
        evidence.put("policyVersion", "policy-v1");
        root.putArray("issues");
        if (mutate != null) mutate.accept(root);
        return mapper.writeValueAsString(root);
    }
}
