package com.feelm.catalog.c2.recommendation;

import com.fasterxml.jackson.databind.JsonNode;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HexFormat;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;

@Service
@Profile({"postgres", "local"})
public final class RecommendationExposureService {
    public static final String ATTRIBUTION_POLICY_VERSION = "c2-direct-item-attribution-v1";
    public static final String RECOMMENDATION_TYPE = "POPULARITY_BASELINE";
    private static final List<String> MODEL_KINDS = List.of("bias", "factors", "calibration", "mapping");
    private static final String RANKING_POLICY = "BAYESIAN_POPULARITY_ONLY";

    private final RecommendationExposurePort exposurePort;

    public RecommendationExposureService(RecommendationExposurePort exposurePort) {
        this.exposurePort = exposurePort;
    }

    public RecommendationExposurePort.PersistResult persistActualExposure(
            UUID actorUserId,
            UUID exposureBatchId,
            Instant exposedAt,
            RecommenderPort.Result recommendation,
            List<SelectedItem> selectedItems
    ) {
        require(actorUserId != null && exposureBatchId != null && exposedAt != null
                && recommendation != null && selectedItems != null && !selectedItems.isEmpty(),
                RecommendationExposureException.Code.INVALID_EXPOSURE_REQUEST);
        require(Set.of("COMPLETE", "PARTIAL").contains(recommendation.outcome()),
                RecommendationExposureException.Code.INVALID_EXPOSURE_REQUEST);
        require(recommendation.requestId() != null,
                RecommendationExposureException.Code.INVALID_SERVING_SNAPSHOT);

        Map<UUID, RecommenderPort.Item> rankedItems = new HashMap<>();
        for (RecommenderPort.Item item : recommendation.items()) {
            require(item != null && item.movieId() != null && item.rank() >= 1
                            && rankedItems.put(item.movieId(), item) == null,
                    RecommendationExposureException.Code.INVALID_SERVING_SNAPSHOT);
        }

        List<SelectedItem> ordered = selectedItems.stream()
                .sorted(Comparator.comparingInt(SelectedItem::position))
                .toList();
        Set<UUID> selectedMovies = new HashSet<>();
        List<RecommendationExposurePort.ExposureItem> exposureItems = new ArrayList<>();
        for (int index = 0; index < ordered.size(); index++) {
            SelectedItem selected = ordered.get(index);
            require(selected != null && selected.movieId() != null && selected.position() == index + 1
                            && selectedMovies.add(selected.movieId()),
                    RecommendationExposureException.Code.INVALID_EXPOSURE_REQUEST);
            RecommenderPort.Item ranked = rankedItems.get(selected.movieId());
            require(ranked != null, RecommendationExposureException.Code.INVALID_EXPOSURE_REQUEST);
            exposureItems.add(toExposureItem(selected, ranked));
        }

        JsonNode snapshot = recommendation.snapshot();
        require(snapshot != null && snapshot.isObject(),
                RecommendationExposureException.Code.INVALID_SERVING_SNAPSHOT);
        Map<String, RecommendationExposurePort.ModelArtifact> models = modelArtifacts(snapshot);
        String recommendationVersion = text(snapshot, "recommendationVersion");
        String artifactSetVersion = text(snapshot, "artifactSetVersion");
        String compatibilityId = text(snapshot, "compatibilityId");
        String policyVersion = text(snapshot, "policyVersion");
        String rankingPolicy = text(snapshot, "rankingPolicy");
        BigDecimal rankingAlpha = decimal(snapshot, "rankingAlpha");
        require(RANKING_POLICY.equals(rankingPolicy) && rankingAlpha.signum() == 0,
                RecommendationExposureException.Code.INVALID_SERVING_SNAPSHOT);

        RecommendationExposurePort.ExposureSnapshot typed = new RecommendationExposurePort.ExposureSnapshot(
                exposureBatchId,
                recommendation.requestId(),
                actorUserId,
                recommendationVersion,
                artifactSetVersion,
                compatibilityId,
                policyVersion,
                rankingPolicy,
                rankingAlpha,
                text(snapshot, "mappingVersion"),
                text(snapshot, "catalogVersion"),
                text(snapshot, "candidateSetVersion"),
                text(snapshot, "inputVersion"),
                models.get("bias"),
                models.get("factors"),
                models.get("calibration"),
                models.get("mapping"),
                ATTRIBUTION_POLICY_VERSION,
                exposedAt,
                "",
                exposureItems
        );
        String fingerprint = fingerprint(typed);
        return exposurePort.persist(copyWithFingerprint(typed, fingerprint));
    }

    public Optional<RecommendationExposurePort.ExposureSnapshot> findOwned(
            UUID actorUserId, UUID exposureBatchId
    ) {
        if (actorUserId == null || exposureBatchId == null) {
            return Optional.empty();
        }
        return exposurePort.findOwned(actorUserId, exposureBatchId);
    }

    private RecommendationExposurePort.ExposureItem toExposureItem(
            SelectedItem selected, RecommenderPort.Item ranked
    ) {
        JsonNode item = ranked.value();
        require(item != null && item.isObject()
                        && ranked.movieId().toString().equals(text(item, "movieId"))
                        && item.path("rank").isInt()
                        && item.path("rank").intValue() == ranked.rank()
                        && "BAYESIAN_POPULARITY".equals(text(item, "rankingSource")),
                RecommendationExposureException.Code.INVALID_SERVING_SNAPSHOT);
        JsonNode star = item.path("expectedStar");
        require(star.isObject()
                        && "NOT_COMPUTED".equals(text(star, "status"))
                        && star.path("value").isNull()
                        && star.path("displayEligible").isBoolean()
                        && !star.path("displayEligible").booleanValue()
                        && "NOT_EVALUATED".equals(text(star, "confidence"))
                        && star.path("confidencePolicyVersion").isNull(),
                RecommendationExposureException.Code.INVALID_SERVING_SNAPSHOT);
        return new RecommendationExposurePort.ExposureItem(
                null,
                selected.movieId(),
                selected.position(),
                ranked.rank(),
                RECOMMENDATION_TYPE,
                new RecommendationExposurePort.ExpectedStar(
                        "NOT_COMPUTED", null, false, "NOT_EVALUATED", null
                )
        );
    }

    private Map<String, RecommendationExposurePort.ModelArtifact> modelArtifacts(JsonNode snapshot) {
        JsonNode versions = snapshot.path("modelVersions");
        JsonNode checksums = snapshot.path("payloadChecksums");
        require(versions.isObject() && checksums.isObject(),
                RecommendationExposureException.Code.INVALID_SERVING_SNAPSHOT);
        Map<String, RecommendationExposurePort.ModelArtifact> result = new HashMap<>();
        for (String kind : MODEL_KINDS) {
            String checksum = text(checksums, kind);
            require(checksum.matches("[a-f0-9]{64}"),
                    RecommendationExposureException.Code.INVALID_SERVING_SNAPSHOT);
            result.put(kind, new RecommendationExposurePort.ModelArtifact(
                    text(versions, kind), checksum
            ));
        }
        return result;
    }

    private RecommendationExposurePort.ExposureSnapshot copyWithFingerprint(
            RecommendationExposurePort.ExposureSnapshot value, String fingerprint
    ) {
        return new RecommendationExposurePort.ExposureSnapshot(
                value.exposureBatchId(), value.sourceRequestId(), value.actorUserId(),
                value.recommendationVersion(), value.artifactSetVersion(), value.compatibilityId(),
                value.policyVersion(), value.rankingPolicy(), value.rankingAlpha(), value.mappingVersion(),
                value.catalogVersion(), value.candidateSetVersion(), value.inputVersion(), value.bias(),
                value.factors(), value.calibration(), value.mapping(), value.attributionPolicyVersion(),
                value.exposedAt(), fingerprint, value.items()
        );
    }

    private String fingerprint(RecommendationExposurePort.ExposureSnapshot value) {
        StringBuilder canonical = new StringBuilder();
        add(canonical, value.exposureBatchId());
        add(canonical, value.sourceRequestId());
        add(canonical, value.actorUserId());
        add(canonical, value.recommendationVersion());
        add(canonical, value.artifactSetVersion());
        add(canonical, value.compatibilityId());
        add(canonical, value.policyVersion());
        add(canonical, value.rankingPolicy());
        add(canonical, value.rankingAlpha().toPlainString());
        add(canonical, value.mappingVersion());
        add(canonical, value.catalogVersion());
        add(canonical, value.candidateSetVersion());
        add(canonical, value.inputVersion());
        for (RecommendationExposurePort.ModelArtifact model :
                List.of(value.bias(), value.factors(), value.calibration(), value.mapping())) {
            add(canonical, model.version());
            add(canonical, model.payloadSha256());
        }
        add(canonical, value.attributionPolicyVersion());
        add(canonical, value.exposedAt().toString());
        for (RecommendationExposurePort.ExposureItem item : value.items()) {
            add(canonical, item.movieId());
            add(canonical, item.position());
            add(canonical, item.sourceRank());
            add(canonical, item.recommendationType());
            add(canonical, item.expectedStar().status());
            add(canonical, item.expectedStar().displayEligible());
            add(canonical, item.expectedStar().confidence());
        }
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256")
                    .digest(canonical.toString().getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException("SHA-256 unavailable", impossible);
        }
    }

    private void add(StringBuilder target, Object value) {
        String normalized = String.valueOf(value);
        target.append(normalized.length()).append(':').append(normalized).append('|');
    }

    private String text(JsonNode node, String field) {
        JsonNode value = node.get(field);
        require(value != null && value.isTextual() && !value.textValue().isBlank(),
                RecommendationExposureException.Code.INVALID_SERVING_SNAPSHOT);
        return value.textValue();
    }

    private BigDecimal decimal(JsonNode node, String field) {
        JsonNode value = node.get(field);
        require(value != null && value.isNumber(),
                RecommendationExposureException.Code.INVALID_SERVING_SNAPSHOT);
        return value.decimalValue();
    }

    private void require(boolean condition, RecommendationExposureException.Code code) {
        if (!condition) {
            throw new RecommendationExposureException(code);
        }
    }

    public record SelectedItem(UUID movieId, int position) {
    }
}
