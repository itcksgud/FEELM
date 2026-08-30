package com.feelm.catalog.c2.recommendation;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import java.io.IOException;
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
public final class JdkRecommenderClient implements RecommenderPort {
    static final String LOCAL_OWNER_TOKEN = "test-c2-service-token";
    private static final Set<String> ROOT_FIELDS = Set.of("requestId", "outcome", "snapshot", "items", "issues");
    private static final Set<String> SNAPSHOT_FIELDS = Set.of(
            "recommendationVersion", "artifactSetVersion", "compatibilityId", "policyVersion",
            "rankingPolicy", "rankingAlpha", "mappingVersion", "catalogVersion",
            "candidateSetVersion", "inputVersion", "modelVersions", "payloadChecksums"
    );
    private static final Set<String> VERSION_MAP_FIELDS = Set.of("bias", "factors", "calibration", "mapping");
    private static final Set<String> ITEM_FIELDS = Set.of("movieId", "rank", "rankingSource", "expectedStar", "reasons");
    private static final Set<String> STAR_FIELDS = Set.of("status", "value", "displayEligible", "confidence", "confidencePolicyVersion");
    private static final Set<String> REASON_FIELDS = Set.of("code", "reasonVersion", "evidence");
    private static final Set<String> EVIDENCE_FIELDS = Set.of("kind", "policyVersion");
    private static final Set<String> ISSUE_FIELDS = Set.of("scope", "code", "movieId", "retriable");
    private static final Set<String> CANDIDATE_ISSUE_CODES = Set.of(
            "INVALID_SERVICE_ID", "SERVICE_ID_NOT_MAPPED", "DUPLICATE_SERVICE_ID",
            "MODEL_ITEM_NOT_AVAILABLE"
    );
    private static final Set<String> STAR_HEAD_ISSUE_CODES = Set.of(
            "VALIDATED_K_INPUT_NOT_AVAILABLE", "STAR_HEAD_UNAVAILABLE", "STAR_SCALE_INCOMPATIBLE"
    );

    private final ObjectMapper mapper;
    private final HttpClient client;
    private final String baseUrl;
    private final String authMode;
    private final String serviceToken;
    private final long timeoutMs;
    private final boolean localFakeEnabled;

    @Autowired
    public JdkRecommenderClient(
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

    JdkRecommenderClient(
            ObjectMapper mapper, HttpClient client, String baseUrl, String authMode,
            String serviceToken, long timeoutMs, boolean localFakeEnabled
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
    public Result rank(Command command) {
        requireConfiguration();
        try {
            byte[] body = mapper.writeValueAsBytes(requestBody(command));
            URI endpoint = URI.create(baseUrl).resolve("/internal/v1/recommendations/rank");
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
                case 401 -> throw failure(C2RecommendationFailure.Code.AUTH_REQUIRED, false);
                case 403 -> throw failure(C2RecommendationFailure.Code.AUTH_FORBIDDEN, false);
                case 503 -> throw failure(C2RecommendationFailure.Code.SERVICE_UNAVAILABLE, true);
                default -> throw failure(C2RecommendationFailure.Code.UPSTREAM_REJECTED, false);
            };
        } catch (C2RecommendationFailure failure) {
            throw failure;
        } catch (HttpTimeoutException timeout) {
            throw failure(C2RecommendationFailure.Code.DEADLINE_EXCEEDED, true);
        } catch (ConnectException connection) {
            throw failure(C2RecommendationFailure.Code.CONNECTION_FAILURE, true);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            throw failure(C2RecommendationFailure.Code.CONNECTION_FAILURE, true);
        } catch (IOException | IllegalArgumentException invalid) {
            throw failure(C2RecommendationFailure.Code.CONNECTION_FAILURE, true);
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
        preference.put("inputVersion", command.preferenceInput().inputVersion());
        ArrayNode ratings = preference.putArray("ratings");
        command.preferenceInput().ratings().forEach(input -> {
            ObjectNode rating = ratings.addObject();
            rating.put("movieId", input.movieId().toString());
            rating.put("value", input.value());
            rating.put("revision", input.revision());
        });
        root.put("starPolicy", "DISABLED");
        return root;
    }

    private Result parseResponse(byte[] bytes, Command command) {
        try {
            JsonNode root = mapper.readTree(bytes);
            exactFields(root, ROOT_FIELDS);
            UUID requestId = UUID.fromString(text(root, "requestId"));
            if (!requestId.equals(command.requestId())) throw invalidResponse();
            String outcome = text(root, "outcome");
            if (!Set.of("COMPLETE", "PARTIAL", "EMPTY").contains(outcome)) throw invalidResponse();
            JsonNode snapshot = root.get("snapshot");
            exactFields(snapshot, SNAPSHOT_FIELDS);
            if (!"BAYESIAN_POPULARITY_ONLY".equals(text(snapshot, "rankingPolicy"))
                    || !snapshot.path("rankingAlpha").isNumber()
                    || snapshot.path("rankingAlpha").decimalValue().signum() != 0
                    || !command.candidateSet().catalogVersion().equals(text(snapshot, "catalogVersion"))
                    || !command.candidateSet().candidateSetVersion().equals(text(snapshot, "candidateSetVersion"))
                    || !command.preferenceInput().inputVersion().equals(text(snapshot, "inputVersion"))
                    || !command.candidateSet().compatibilityId().equals(text(snapshot, "compatibilityId"))) {
                throw invalidResponse();
            }
            JsonNode checksums = snapshot.path("payloadChecksums");
            JsonNode versions = snapshot.path("modelVersions");
            exactFields(versions, VERSION_MAP_FIELDS);
            exactFields(checksums, VERSION_MAP_FIELDS);
            for (String key : VERSION_MAP_FIELDS) {
                text(versions, key);
                if (!text(checksums, key).matches("[0-9a-f]{64}")) throw invalidResponse();
            }
            if (!command.candidateSet().mappingPayloadSha256().equals(text(checksums, "mapping"))) {
                throw invalidResponse();
            }
            JsonNode itemNodes = root.get("items");
            JsonNode issueNodes = root.get("issues");
            if (!itemNodes.isArray() || !issueNodes.isArray()) throw invalidResponse();
            Set<UUID> candidates = new HashSet<>(command.eligibleMovieIds());
            Set<UUID> returned = new HashSet<>();
            List<Item> items = new ArrayList<>();
            int expectedRank = 1;
            for (JsonNode item : itemNodes) {
                exactFields(item, ITEM_FIELDS);
                UUID movieId = UUID.fromString(text(item, "movieId"));
                int rank = item.path("rank").asInt(-1);
                JsonNode star = item.path("expectedStar");
                exactFields(star, STAR_FIELDS);
                if (!candidates.contains(movieId) || !returned.add(movieId) || rank != expectedRank++
                        || !"BAYESIAN_POPULARITY".equals(text(item, "rankingSource"))
                        || !"NOT_COMPUTED".equals(text(star, "status"))
                        || !star.path("value").isNull()
                        || !star.path("displayEligible").isBoolean()
                        || star.path("displayEligible").booleanValue()
                        || !"NOT_EVALUATED".equals(text(star, "confidence"))
                        || !star.path("confidencePolicyVersion").isNull()) {
                    throw invalidResponse();
                }
                JsonNode reasons = item.path("reasons");
                if (!reasons.isArray()) throw invalidResponse();
                for (JsonNode reason : reasons) {
                    exactFields(reason, REASON_FIELDS);
                    JsonNode evidence = reason.path("evidence");
                    exactFields(evidence, EVIDENCE_FIELDS);
                    if (!"POPULARITY_BASELINE".equals(text(reason, "code"))
                            || text(reason, "reasonVersion").isBlank()
                            || !"RANKING_POLICY".equals(text(evidence, "kind"))
                            || text(evidence, "policyVersion").isBlank()) {
                        throw invalidResponse();
                    }
                }
                items.add(new Item(movieId, rank, item.deepCopy()));
            }
            List<JsonNode> issues = new ArrayList<>();
            Set<String> issueKeys = new HashSet<>();
            for (JsonNode issue : issueNodes) {
                exactFields(issue, ISSUE_FIELDS);
                String scope = text(issue, "scope");
                String code = text(issue, "code");
                JsonNode issueMovieId = issue.path("movieId");
                if (!(issueMovieId.isNull() || issueMovieId.isTextual())
                        || !issue.path("retriable").isBoolean()) {
                    throw invalidResponse();
                }
                if ("CANDIDATE".equals(scope)) {
                    if (!CANDIDATE_ISSUE_CODES.contains(code) || !issueMovieId.isTextual()) {
                        throw invalidResponse();
                    }
                } else if ("STAR_HEAD".equals(scope)) {
                    if (!STAR_HEAD_ISSUE_CODES.contains(code) || !issueMovieId.isNull()) {
                        throw invalidResponse();
                    }
                    // This adapter always requests starPolicy=DISABLED. A STAR_HEAD issue therefore
                    // contradicts the exact request sent by Spring and must fail closed.
                    throw invalidResponse();
                } else {
                    throw invalidResponse();
                }
                if (issueMovieId.isTextual()) {
                    UUID issueId = UUID.fromString(issueMovieId.textValue());
                    if (!candidates.contains(issueId)) throw invalidResponse();
                }
                String issueKey = scope + ':' + code + ':' + issueMovieId.asText("null");
                if (!issueKeys.add(issueKey)) throw invalidResponse();
                issues.add(issue.deepCopy());
            }
            switch (outcome) {
                case "COMPLETE" -> {
                    if (items.isEmpty() || !issues.isEmpty() || !returned.equals(candidates)) {
                        throw invalidResponse();
                    }
                }
                case "PARTIAL" -> {
                    if (items.isEmpty() || issues.isEmpty()) throw invalidResponse();
                }
                case "EMPTY" -> {
                    if (!items.isEmpty() || issues.isEmpty()) throw invalidResponse();
                }
                default -> throw invalidResponse();
            }
            return new Result(requestId, outcome, snapshot.deepCopy(), items, issues);
        } catch (C2RecommendationFailure failure) {
            throw failure;
        } catch (Exception ignored) {
            throw invalidResponse();
        }
    }

    private void requireConfiguration() {
        if (!localFakeEnabled || !"fake".equals(authMode) || !LOCAL_OWNER_TOKEN.equals(serviceToken)
                || baseUrl == null || baseUrl.isBlank() || timeoutMs <= 0) {
            throw failure(C2RecommendationFailure.Code.CONFIGURATION_UNAVAILABLE, false);
        }
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

    private C2RecommendationFailure invalidResponse() {
        return failure(C2RecommendationFailure.Code.INVALID_RESPONSE, false);
    }

    private C2RecommendationFailure failure(C2RecommendationFailure.Code code, boolean retryable) {
        return new C2RecommendationFailure(code, retryable);
    }
}
