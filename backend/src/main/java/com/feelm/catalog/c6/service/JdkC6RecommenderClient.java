package com.feelm.catalog.c6.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.feelm.catalog.c2.input.ActiveRatingInputPort;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.net.ConnectException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.net.http.HttpTimeoutException;
import java.time.Duration;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.UUID;

@Component
@Profile("local")
@ConditionalOnProperty(name = "catalog.c6.local.enabled", havingValue = "true")
public final class JdkC6RecommenderClient implements C6RecommenderPort {
    static final String EXPERIMENT_VERSION = "c6-recommendation-interpretation-v2";
    static final String K_POLICY_VERSION = "C6_MOST_RECENT_VALIDATED_K_FLOOR_V1";
    static final String UTILITY_POLICY_VERSION = "C6_DISCRETE_QUANTIZED_MIDRANK_ECDF_V2";
    static final String LOCAL_OWNER_TOKEN = "test-c2-service-token";
    private static final Set<String> ROOT_FIELDS = Set.of(
            "requestId", "experimentVersion", "snapshot", "ratingProfile", "items", "limitations"
    );
    private static final Set<String> SNAPSHOT_FIELDS = Set.of(
            "artifactSetVersion", "policyVersion", "inputVersion", "kSelectionPolicyVersion", "utilityPolicyVersion",
            "availableRatingCount", "usedRatingCount"
    );
    private static final Set<String> PROFILE_FIELDS = Set.of(
            "activeRatingCount", "mean", "median", "confidence"
    );
    private static final Set<String> ITEM_FIELDS = Set.of(
            "movieId", "predictedRating", "expectedRelativeUtility", "directFoldIn",
            "confidence", "displayEligible"
    );
    private static final Set<String> REQUIRED_LIMITATIONS = Set.of(
            "LOCAL_EXPERIMENT_ONLY", "NOT_SELF_REPORTED_SATISFACTION",
            "NOT_PRODUCT_DISPLAY_APPROVED", "K_BUCKETED_MOST_RECENT"
    );
    private static final Set<String> CONFIDENCE_VALUES = Set.of(
            "INSUFFICIENT_DATA", "LOW", "MEDIUM", "HIGH"
    );

    private final ObjectMapper mapper;
    private final HttpClient client;
    private final String baseUrl;
    private final String authMode;
    private final String serviceToken;
    private final long timeoutMs;
    private final boolean localFakeEnabled;

    @Autowired
    public JdkC6RecommenderClient(
            ObjectMapper mapper,
            @Value("${catalog.c2.recommender.base-url:}") String baseUrl,
            @Value("${catalog.c2.recommender.auth-mode:}") String authMode,
            @Value("${catalog.c2.recommender.service-token:}") String serviceToken,
            @Value("${catalog.c2.recommender.timeout-ms:0}") long timeoutMs,
            @Value("${catalog.c2.recommender.local-fake-enabled:false}") boolean localFakeEnabled
    ) {
        this(mapper, HttpClient.newBuilder().followRedirects(HttpClient.Redirect.NEVER).build(),
                baseUrl, authMode, serviceToken, timeoutMs, localFakeEnabled);
    }

    JdkC6RecommenderClient(
            ObjectMapper mapper,
            HttpClient client,
            String baseUrl,
            String authMode,
            String serviceToken,
            long timeoutMs,
            boolean localFakeEnabled
    ) {
        this.mapper = mapper;
        this.client = client;
        this.baseUrl = baseUrl;
        this.authMode = authMode;
        this.serviceToken = serviceToken;
        this.timeoutMs = timeoutMs;
        this.localFakeEnabled = localFakeEnabled;
    }

    @Override
    public Result interpret(Command command) {
        requireConfiguration();
        validateCommand(command);
        try {
            URI endpoint = URI.create(baseUrl).resolve("/internal/v1/experiments/recommendation-interpretation");
            byte[] body = mapper.writeValueAsBytes(requestBody(command));
            HttpRequest request = HttpRequest.newBuilder(endpoint)
                    .timeout(Duration.ofMillis(timeoutMs))
                    .header("Content-Type", "application/json")
                    .header("Accept", "application/json")
                    .header("Authorization", "Bearer " + serviceToken)
                    .header("X-Request-Id", command.requestId().toString())
                    .POST(HttpRequest.BodyPublishers.ofByteArray(body))
                    .build();
            HttpResponse<byte[]> response = client.send(request, HttpResponse.BodyHandlers.ofByteArray());
            return switch (response.statusCode()) {
                case 200 -> parseResponse(response.body(), command);
                case 401 -> throw failure(C6RecommenderFailure.Code.AUTH_REQUIRED, false);
                case 403 -> throw failure(C6RecommenderFailure.Code.AUTH_FORBIDDEN, false);
                case 503 -> throw failure(C6RecommenderFailure.Code.SERVICE_UNAVAILABLE, true);
                default -> throw failure(C6RecommenderFailure.Code.UPSTREAM_REJECTED, false);
            };
        } catch (C6RecommenderFailure failure) {
            throw failure;
        } catch (HttpTimeoutException timeout) {
            throw failure(C6RecommenderFailure.Code.DEADLINE_EXCEEDED, true);
        } catch (ConnectException connection) {
            throw failure(C6RecommenderFailure.Code.CONNECTION_FAILURE, true);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            throw failure(C6RecommenderFailure.Code.CONNECTION_FAILURE, true);
        } catch (IOException | IllegalArgumentException invalid) {
            throw failure(C6RecommenderFailure.Code.CONNECTION_FAILURE, true);
        }
    }

    private ObjectNode requestBody(Command command) {
        ObjectNode root = mapper.createObjectNode();
        root.put("requestId", command.requestId().toString());
        ObjectNode candidates = root.putObject("candidateSet");
        candidates.put("candidateSetVersion", command.candidateSet().candidateSetVersion());
        ArrayNode ids = candidates.putArray("movieIds");
        command.eligibleMovieIds().forEach(id -> ids.add(id.toString()));
        ObjectNode preference = root.putObject("preferenceInput");
        preference.put("inputVersion", command.inputVersion());
        ArrayNode ratings = preference.putArray("ratings");
        command.ratings().forEach(input -> {
            ObjectNode rating = ratings.addObject();
            rating.put("movieId", input.movieId().toString());
            rating.put("value", input.value());
            rating.put("revision", input.revision());
        });
        return root;
    }

    private Result parseResponse(byte[] bytes, Command command) {
        try {
            JsonNode root = mapper.readTree(bytes);
            exactFields(root, ROOT_FIELDS);
            if (!command.requestId().equals(UUID.fromString(text(root, "requestId")))
                    || !EXPERIMENT_VERSION.equals(text(root, "experimentVersion"))) {
                throw invalidResponse();
            }

            JsonNode snapshotNode = root.path("snapshot");
            exactFields(snapshotNode, SNAPSHOT_FIELDS);
            int available = exactNonNegativeInt(snapshotNode, "availableRatingCount");
            int used = exactNonNegativeInt(snapshotNode, "usedRatingCount");
            if (!command.inputVersion().equals(text(snapshotNode, "inputVersion"))
                    || !K_POLICY_VERSION.equals(text(snapshotNode, "kSelectionPolicyVersion"))
                    || !UTILITY_POLICY_VERSION.equals(text(snapshotNode, "utilityPolicyVersion"))
                    || available != command.ratings().size()
                    || used != selectedK(available)
                    || text(snapshotNode, "artifactSetVersion").length() > 256
                    || text(snapshotNode, "policyVersion").length() > 256) {
                throw invalidResponse();
            }
            Snapshot snapshot = new Snapshot(
                    text(snapshotNode, "artifactSetVersion"), text(snapshotNode, "policyVersion"),
                    command.inputVersion(), K_POLICY_VERSION, UTILITY_POLICY_VERSION, available, used
            );

            JsonNode profileNode = root.path("ratingProfile");
            exactFields(profileNode, PROFILE_FIELDS);
            int activeCount = exactNonNegativeInt(profileNode, "activeRatingCount");
            BigDecimal mean = nullableDecimal(profileNode, "mean", BigDecimal.ONE, BigDecimal.valueOf(5));
            BigDecimal median = nullableDecimal(profileNode, "median", BigDecimal.ONE, BigDecimal.valueOf(5));
            String confidence = confidence(profileNode, "confidence");
            if (activeCount != available || !same(mean, expectedMean(command.ratings()))
                    || !same(median, expectedMedian(command.ratings()))
                    || !confidence.equals(confidenceForK(used))) {
                throw invalidResponse();
            }
            RatingProfile profile = new RatingProfile(activeCount, mean, median, confidence);

            JsonNode itemsNode = root.path("items");
            if (!itemsNode.isArray()) throw invalidResponse();
            Set<UUID> eligible = new HashSet<>(command.eligibleMovieIds());
            Set<UUID> returned = new HashSet<>();
            List<Item> items = new ArrayList<>();
            for (JsonNode itemNode : itemsNode) {
                exactFields(itemNode, ITEM_FIELDS);
                UUID movieId = UUID.fromString(text(itemNode, "movieId"));
                BigDecimal predicted = requiredDecimal(
                        itemNode, "predictedRating", BigDecimal.valueOf(0.5), BigDecimal.valueOf(5)
                );
                BigDecimal utility = nullableDecimal(itemNode, "expectedRelativeUtility", BigDecimal.ZERO, BigDecimal.ONE);
                if (!eligible.contains(movieId) || !returned.add(movieId)
                        || !itemNode.path("directFoldIn").isBoolean()
                        || !itemNode.path("displayEligible").isBoolean()
                        || itemNode.path("displayEligible").booleanValue()) {
                    throw invalidResponse();
                }
                boolean direct = itemNode.path("directFoldIn").booleanValue();
                if (used == 0 && (direct || utility != null)) throw invalidResponse();
                String itemConfidence = confidence(itemNode, "confidence");
                if (!itemConfidence.equals(confidence)) throw invalidResponse();
                items.add(new Item(movieId, predicted, utility, direct, itemConfidence, false));
            }
            if (!returned.equals(eligible)) throw invalidResponse();

            JsonNode limitationsNode = root.path("limitations");
            if (!limitationsNode.isArray()) throw invalidResponse();
            List<String> limitations = new ArrayList<>();
            limitationsNode.forEach(node -> {
                if (!node.isTextual()) throw invalidResponse();
                limitations.add(node.textValue());
            });
            if (limitations.size() != REQUIRED_LIMITATIONS.size()
                    || !Set.copyOf(limitations).equals(REQUIRED_LIMITATIONS)) {
                throw invalidResponse();
            }
            return new Result(EXPERIMENT_VERSION, snapshot, profile, items, limitations);
        } catch (C6RecommenderFailure failure) {
            throw failure;
        } catch (Exception invalid) {
            throw invalidResponse();
        }
    }

    private void validateCommand(Command command) {
        if (command == null || command.requestId() == null || command.candidateSet() == null
                || command.inputVersion() == null || command.inputVersion().isBlank()) {
            throw failure(C6RecommenderFailure.Code.CONFIGURATION_UNAVAILABLE, false);
        }
        if (command.eligibleMovieIds().stream().anyMatch(java.util.Objects::isNull)
                || command.eligibleMovieIds().stream().distinct().count() != command.eligibleMovieIds().size()) {
            throw failure(C6RecommenderFailure.Code.CONFIGURATION_UNAVAILABLE, false);
        }
        Set<UUID> ratingIds = new HashSet<>();
        for (ActiveRatingInputPort.RatingInput rating : command.ratings()) {
            if (rating == null || rating.movieId() == null || rating.value() < 1 || rating.value() > 5
                    || rating.revision() < 1 || !ratingIds.add(rating.movieId())) {
                throw failure(C6RecommenderFailure.Code.CONFIGURATION_UNAVAILABLE, false);
            }
        }
    }

    private void requireConfiguration() {
        if (!localFakeEnabled || !"fake".equals(authMode) || !LOCAL_OWNER_TOKEN.equals(serviceToken)
                || baseUrl == null || baseUrl.isBlank() || timeoutMs <= 0) {
            throw failure(C6RecommenderFailure.Code.CONFIGURATION_UNAVAILABLE, false);
        }
    }

    private static int selectedK(int available) {
        int selected = 0;
        for (int value : List.of(0, 1, 3, 5, 10, 20)) {
            if (value <= available) selected = value;
        }
        return selected;
    }

    private static BigDecimal expectedMean(List<ActiveRatingInputPort.RatingInput> ratings) {
        if (ratings.isEmpty()) return null;
        double sum = ratings.stream().mapToInt(ActiveRatingInputPort.RatingInput::value).sum();
        return BigDecimal.valueOf(sum / ratings.size()).stripTrailingZeros();
    }

    private static BigDecimal expectedMedian(List<ActiveRatingInputPort.RatingInput> ratings) {
        if (ratings.isEmpty()) return null;
        List<Integer> values = ratings.stream().map(ActiveRatingInputPort.RatingInput::value).sorted().toList();
        int middle = values.size() / 2;
        if (values.size() % 2 == 1) return BigDecimal.valueOf(values.get(middle));
        return BigDecimal.valueOf(values.get(middle - 1) + values.get(middle))
                .divide(BigDecimal.valueOf(2), 1, RoundingMode.UNNECESSARY).stripTrailingZeros();
    }

    static String confidenceForK(int k) {
        return switch (k) {
            case 0 -> "INSUFFICIENT_DATA";
            case 1, 3, 5 -> "LOW";
            case 10 -> "MEDIUM";
            case 20 -> "HIGH";
            default -> throw new IllegalArgumentException("unvalidated C6 K");
        };
    }

    private String confidence(JsonNode node, String field) {
        String value = text(node, field);
        if (!CONFIDENCE_VALUES.contains(value)) throw invalidResponse();
        return value;
    }

    private BigDecimal requiredDecimal(JsonNode node, String field, BigDecimal min, BigDecimal max) {
        BigDecimal value = nullableDecimal(node, field, min, max);
        if (value == null) throw invalidResponse();
        return value;
    }

    private BigDecimal nullableDecimal(JsonNode node, String field, BigDecimal min, BigDecimal max) {
        JsonNode value = node.get(field);
        if (value == null) throw invalidResponse();
        if (value.isNull()) return null;
        if (!value.isNumber()) throw invalidResponse();
        BigDecimal decimal = value.decimalValue();
        if (decimal.compareTo(min) < 0 || decimal.compareTo(max) > 0) throw invalidResponse();
        return decimal.stripTrailingZeros();
    }

    private int exactNonNegativeInt(JsonNode node, String field) {
        JsonNode value = node.get(field);
        if (value == null || !value.isIntegralNumber() || !value.canConvertToInt() || value.intValue() < 0) {
            throw invalidResponse();
        }
        return value.intValue();
    }

    private String text(JsonNode node, String field) {
        JsonNode value = node.get(field);
        if (value == null || !value.isTextual() || value.textValue().isBlank()) throw invalidResponse();
        return value.textValue();
    }

    private void exactFields(JsonNode node, Set<String> expected) {
        if (node == null || !node.isObject()) throw invalidResponse();
        Set<String> actual = new HashSet<>();
        node.fieldNames().forEachRemaining(actual::add);
        if (!actual.equals(expected)) throw invalidResponse();
    }

    private static boolean same(BigDecimal left, BigDecimal right) {
        return left == null ? right == null : right != null && left.compareTo(right) == 0;
    }

    private C6RecommenderFailure invalidResponse() {
        return failure(C6RecommenderFailure.Code.INVALID_RESPONSE, false);
    }

    private C6RecommenderFailure failure(C6RecommenderFailure.Code code, boolean retryable) {
        return new C6RecommenderFailure(code, retryable);
    }
}
