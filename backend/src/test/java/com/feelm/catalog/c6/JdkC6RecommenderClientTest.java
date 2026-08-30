package com.feelm.catalog.c6.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.feelm.catalog.c2.input.ActiveRatingInputPort;
import com.feelm.catalog.c2.recommendation.CandidateSetPort;
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

class JdkC6RecommenderClientTest {
    private static final UUID REQUEST = UUID.fromString("13b59061-924d-4d8d-9807-587313a74e09");
    private static final UUID MOVIE = UUID.fromString("19406c31-213f-4fe1-93f6-109f8570ec20");
    private static final UUID RATED = UUID.fromString("6b226903-0ca4-4f5a-9bf0-50d6cedd224c");
    private final ObjectMapper mapper = new ObjectMapper();
    private HttpServer server;

    @AfterEach
    void stop() {
        if (server != null) server.stop(0);
    }

    @Test
    void sendsOnlyExactPrivateInputAndAcceptsValidatedExperiment() throws Exception {
        AtomicReference<JsonNode> request = new AtomicReference<>();
        start(validResponse(null, true), exchange -> {
            try {
                assertThat(exchange.getRequestHeaders().getFirst("Authorization"))
                        .isEqualTo("Bearer test-c2-service-token");
                request.set(mapper.readTree(exchange.getRequestBody()));
            } catch (Exception exception) {
                throw new RuntimeException(exception);
            }
        });

        C6RecommenderPort.Result result = client().interpret(command(true));
        assertThat(result.snapshot().usedRatingCount()).isEqualTo(1);
        assertThat(result.items()).singleElement().satisfies(item -> {
            assertThat(item.movieId()).isEqualTo(MOVIE);
            assertThat(item.displayEligible()).isFalse();
        });
        assertThat(request.get().fieldNames()).toIterable()
                .containsExactlyInAnyOrder("requestId", "candidateSet", "preferenceInput");
        assertThat(request.get().path("preferenceInput").path("ratings").get(0).path("movieId").asText())
                .isEqualTo(RATED.toString());
        assertThat(request.get().toString()).doesNotContain("userId", "email", "updatedAt", "token");
    }

    @Test
    void rejectsEveryStrictContractContradiction() throws Exception {
        List<Consumer<ObjectNode>> attacks = List.of(
                root -> root.put("userId", "leak"),
                root -> ((ObjectNode) root.path("snapshot")).put("usedRatingCount", 0),
                root -> ((ObjectNode) root.path("snapshot")).put("utilityPolicyVersion", "unknown-v3"),
                root -> ((ObjectNode) root.path("ratingProfile")).put("mean", 5),
                root -> ((ObjectNode) root.path("items").get(0)).put("displayEligible", true),
                root -> ((ObjectNode) root.path("items").get(0)).put("expectedRelativeUtility", 2),
                root -> ((ObjectNode) root.path("items").get(0)).put("predictedRating", 0.49),
                root -> ((ObjectNode) root.path("items").get(0)).put("confidence", "MEDIUM"),
                root -> root.withArray("limitations").remove(0)
        );
        for (Consumer<ObjectNode> attack : attacks) {
            stop();
            start(validResponse(attack, true), ignored -> { });
            assertThatThrownBy(() -> client().interpret(command(true)))
                    .isInstanceOfSatisfying(C6RecommenderFailure.class,
                            failure -> assertThat(failure.code())
                                    .isEqualTo(C6RecommenderFailure.Code.INVALID_RESPONSE));
        }
    }

    @Test
    void noRatingRequiresKZeroNoRelativeUtilityAndNoDirectFoldIn() throws Exception {
        start(validResponse(null, false), ignored -> { });
        C6RecommenderPort.Result result = client().interpret(command(false));
        assertThat(result.snapshot().availableRatingCount()).isZero();
        assertThat(result.snapshot().usedRatingCount()).isZero();
        assertThat(result.ratingProfile().mean()).isNull();
        assertThat(result.items()).singleElement().satisfies(item -> {
            assertThat(item.predictedRating()).isEqualByComparingTo("0.5");
            assertThat(item.expectedRelativeUtility()).isNull();
            assertThat(item.directFoldIn()).isFalse();
        });
    }

    @Test
    void configurationFailsClosed() {
        JdkC6RecommenderClient disabled = new JdkC6RecommenderClient(
                mapper, HttpClient.newHttpClient(), "http://127.0.0.1:1", "", "", 50, false
        );
        assertThatThrownBy(() -> disabled.interpret(command(false)))
                .isInstanceOfSatisfying(C6RecommenderFailure.class,
                        failure -> assertThat(failure.code())
                                .isEqualTo(C6RecommenderFailure.Code.CONFIGURATION_UNAVAILABLE));
    }

    private void start(String response, Consumer<com.sun.net.httpserver.HttpExchange> inspect) throws Exception {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/internal/v1/experiments/recommendation-interpretation", exchange -> {
            inspect.accept(exchange);
            byte[] bytes = response.getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().add("Content-Type", "application/json");
            exchange.sendResponseHeaders(200, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        });
        server.start();
    }

    private JdkC6RecommenderClient client() {
        return new JdkC6RecommenderClient(
                mapper, HttpClient.newHttpClient(), "http://127.0.0.1:" + server.getAddress().getPort(),
                "fake", "test-c2-service-token", 1000, true
        );
    }

    private C6RecommenderPort.Command command(boolean rated) {
        CandidateSetPort.Snapshot candidates = new CandidateSetPort.Snapshot(
                "candidate-v1", "catalog-fixture-20260829-01", "b".repeat(64),
                "fixture-family-v1", List.of(MOVIE)
        );
        List<ActiveRatingInputPort.RatingInput> ratings = rated
                ? List.of(new ActiveRatingInputPort.RatingInput(RATED, 4, 2)) : List.of();
        return new C6RecommenderPort.Command(REQUEST, candidates, List.of(MOVIE), "input-v1", ratings);
    }

    private String validResponse(Consumer<ObjectNode> mutate, boolean rated) throws Exception {
        ObjectNode root = mapper.createObjectNode();
        root.put("requestId", REQUEST.toString());
        root.put("experimentVersion", "c6-recommendation-interpretation-v2");
        ObjectNode snapshot = root.putObject("snapshot");
        snapshot.put("artifactSetVersion", "artifact-set-v1");
        snapshot.put("policyVersion", "policy-v1");
        snapshot.put("inputVersion", "input-v1");
        snapshot.put("kSelectionPolicyVersion", "C6_MOST_RECENT_VALIDATED_K_FLOOR_V1");
        snapshot.put("utilityPolicyVersion", "C6_DISCRETE_QUANTIZED_MIDRANK_ECDF_V2");
        snapshot.put("availableRatingCount", rated ? 1 : 0);
        snapshot.put("usedRatingCount", rated ? 1 : 0);
        ObjectNode profile = root.putObject("ratingProfile");
        profile.put("activeRatingCount", rated ? 1 : 0);
        if (rated) {
            profile.put("mean", 4);
            profile.put("median", 4);
        } else {
            profile.putNull("mean");
            profile.putNull("median");
        }
        profile.put("confidence", rated ? "LOW" : "INSUFFICIENT_DATA");
        ObjectNode item = root.putArray("items").addObject();
        item.put("movieId", MOVIE.toString());
        item.put("predictedRating", rated ? 4.2 : 0.5);
        if (rated) item.put("expectedRelativeUtility", 0.8);
        else item.putNull("expectedRelativeUtility");
        item.put("directFoldIn", rated);
        item.put("confidence", rated ? "LOW" : "INSUFFICIENT_DATA");
        item.put("displayEligible", false);
        var limitations = root.putArray("limitations");
        limitations.add("LOCAL_EXPERIMENT_ONLY");
        limitations.add("NOT_SELF_REPORTED_SATISFACTION");
        limitations.add("NOT_PRODUCT_DISPLAY_APPROVED");
        limitations.add("K_BUCKETED_MOST_RECENT");
        if (mutate != null) mutate.accept(root);
        return mapper.writeValueAsString(root);
    }
}
