package com.feelm.catalog.c2.recommendation;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.system.CapturedOutput;
import org.springframework.boot.test.system.OutputCaptureExtension;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

@SpringBootTest
@ActiveProfiles("local")
@Testcontainers(disabledWithoutDocker = true)
@ExtendWith(OutputCaptureExtension.class)
class RecommendationExposurePostgresIntegrationTest {
    private static final UUID OWNER = UUID.fromString("018f6826-4da1-7c38-a846-8f794cd8b0cf");
    private static final UUID OTHER = UUID.fromString("5f93a51d-a6f1-41dc-8d86-6b570d53bd82");
    private static final UUID REQUEST = UUID.fromString("ee9d3340-b38c-4317-a7c5-874aaed79576");
    private static final UUID BATCH = UUID.fromString("0111b31e-6a7f-4cc8-8a54-e38e70e40072");
    private static final Instant EXPOSED_AT = Instant.parse("2026-08-29T11:59:30Z");
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final List<UUID> MOVIES = java.util.stream.IntStream.rangeClosed(1, 20)
            .mapToObj(index -> UUID.nameUUIDFromBytes(
                    ("c2-exposure-fixture-" + index).getBytes(StandardCharsets.UTF_8)
            )).toList();

    @Container
    static final PostgreSQLContainer<?> POSTGRES = new PostgreSQLContainer<>("postgres:17-alpine")
            .withDatabaseName("feelm_c2_exposure_test");

    @DynamicPropertySource
    static void configure(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", POSTGRES::getJdbcUrl);
        registry.add("spring.datasource.username", POSTGRES::getUsername);
        registry.add("spring.datasource.password", POSTGRES::getPassword);
        registry.add("catalog.c1.watch-intent-scheduler-delay-ms", () -> "3600000");
        registry.add("catalog.c1.outbox-worker.enabled", () -> "false");
    }

    @Autowired RecommendationExposureService exposures;
    @Autowired JdbcTemplate jdbc;

    @BeforeEach
    void reset() {
        jdbc.update("DELETE FROM recommendation_exposure_batch");
        OffsetDateTime created = OffsetDateTime.ofInstant(
                Instant.parse("2026-08-29T00:00:00Z"), ZoneOffset.UTC
        );
        for (UUID movie : MOVIES) {
            jdbc.update("""
                    INSERT INTO movie_identity(id, created_at) VALUES (?, ?)
                    ON CONFLICT (id) DO NOTHING
                    """, movie, created);
        }
    }

    @Test
    void storesOnlyThreeActuallyExposedItemsFromTwentyWithCompleteTypedVersions() {
        RecommenderPort.Result result = result(MOVIES);
        int behaviorBefore = jdbc.queryForObject("SELECT count(*) FROM user_behavior_event", Integer.class);
        int outboxBefore = jdbc.queryForObject("SELECT count(*) FROM domain_outbox", Integer.class);
        List<RecommendationExposureService.SelectedItem> selected = List.of(
                new RecommendationExposureService.SelectedItem(MOVIES.get(14), 3),
                new RecommendationExposureService.SelectedItem(MOVIES.get(1), 1),
                new RecommendationExposureService.SelectedItem(MOVIES.get(6), 2)
        );

        RecommendationExposurePort.PersistResult stored = exposures.persistActualExposure(
                OWNER, BATCH, EXPOSED_AT, result, selected
        );

        assertThat(stored.replayed()).isFalse();
        assertThat(stored.snapshot().items())
                .extracting(RecommendationExposurePort.ExposureItem::movieId)
                .containsExactly(MOVIES.get(1), MOVIES.get(6), MOVIES.get(14));
        assertThat(stored.snapshot().items())
                .extracting(RecommendationExposurePort.ExposureItem::sourceRank)
                .containsExactly(2, 7, 15);
        assertThat(stored.snapshot().items())
                .allSatisfy(item -> {
                    assertThat(item.recommendationItemId()).isNotNull();
                    assertThat(item.recommendationType()).isEqualTo("POPULARITY_BASELINE");
                    assertThat(item.expectedStar().status()).isEqualTo("NOT_COMPUTED");
                    assertThat(item.expectedStar().value()).isNull();
                    assertThat(item.expectedStar().displayEligible()).isFalse();
                    assertThat(item.expectedStar().confidence()).isEqualTo("NOT_EVALUATED");
                    assertThat(item.expectedStar().confidencePolicyVersion()).isNull();
                });
        assertThat(stored.snapshot().recommendationVersion()).isEqualTo("recommendation-v1-exposure");
        assertThat(stored.snapshot().artifactSetVersion()).isEqualTo("artifact-set-v1");
        assertThat(stored.snapshot().compatibilityId()).isEqualTo("fixture-family-v1");
        assertThat(List.of(
                stored.snapshot().bias().version(), stored.snapshot().factors().version(),
                stored.snapshot().calibration().version(), stored.snapshot().mapping().version()
        )).containsExactly("bias-v1", "factors-v1", "calibration-v1", "mapping-v1");
        assertThat(List.of(
                stored.snapshot().bias().payloadSha256(), stored.snapshot().factors().payloadSha256(),
                stored.snapshot().calibration().payloadSha256()
        )).allMatch("a".repeat(64)::equals);
        assertThat(stored.snapshot().mapping().payloadSha256()).isEqualTo("b".repeat(64));
        assertThat(stored.snapshot().attributionPolicyVersion())
                .isEqualTo(RecommendationExposureService.ATTRIBUTION_POLICY_VERSION);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM recommendation_exposure_batch", Integer.class))
                .isEqualTo(1);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM recommendation_exposure_item", Integer.class))
                .isEqualTo(3);
        assertThat(jdbc.queryForObject("""
                SELECT count(*) FROM information_schema.columns
                 WHERE table_name IN ('recommendation_exposure_batch', 'recommendation_exposure_item')
                   AND data_type IN ('json', 'jsonb')
                """, Integer.class)).isZero();
        assertThat(jdbc.queryForObject("""
                SELECT count(*) FROM recommendation_exposure_item
                 WHERE expected_star_value IS NOT NULL OR expected_star_display_eligible
                    OR expected_star_confidence_policy_version IS NOT NULL
                """, Integer.class)).isZero();
        assertThat(jdbc.queryForObject("SELECT count(*) FROM user_behavior_event", Integer.class))
                .isEqualTo(behaviorBefore);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM domain_outbox", Integer.class))
                .isEqualTo(outboxBefore);
        assertThat(jdbc.queryForObject("""
                SELECT count(*) FROM rating
                 WHERE user_id = ? AND movie_id IN (?, ?, ?)
                """, Integer.class, OWNER, MOVIES.get(1), MOVIES.get(6), MOVIES.get(14))).isZero();
    }

    @Test
    void exactBatchRetryIsIdempotentButAnewBatchPreservesRepeatedMovieExposure() {
        RecommenderPort.Result result = result(MOVIES);
        List<RecommendationExposureService.SelectedItem> selected = selections(MOVIES.get(0), MOVIES.get(1));
        RecommendationExposurePort.PersistResult first = exposures.persistActualExposure(
                OWNER, BATCH, EXPOSED_AT, result, selected
        );
        RecommendationExposurePort.PersistResult replay = exposures.persistActualExposure(
                OWNER, BATCH, EXPOSED_AT, result, selected
        );
        assertThat(replay.replayed()).isTrue();
        assertThat(replay.snapshot().items())
                .extracting(RecommendationExposurePort.ExposureItem::recommendationItemId)
                .containsExactlyElementsOf(first.snapshot().items().stream()
                        .map(RecommendationExposurePort.ExposureItem::recommendationItemId).toList());
        assertThat(jdbc.queryForObject("SELECT count(*) FROM recommendation_exposure_item", Integer.class))
                .isEqualTo(2);

        assertThatThrownBy(() -> exposures.persistActualExposure(
                OWNER, BATCH, EXPOSED_AT, result, selections(MOVIES.get(0), MOVIES.get(2))
        )).isInstanceOfSatisfying(RecommendationExposureException.class,
                failure -> assertThat(failure.code())
                        .isEqualTo(RecommendationExposureException.Code.EXPOSURE_BATCH_REUSED));

        UUID secondBatch = UUID.fromString("09b8184b-3acc-45ad-9236-b5563dbaac98");
        RecommendationExposurePort.PersistResult repeated = exposures.persistActualExposure(
                OWNER, secondBatch, EXPOSED_AT.plusSeconds(20), result, selected
        );
        assertThat(repeated.replayed()).isFalse();
        assertThat(repeated.snapshot().items().get(0).recommendationItemId())
                .isNotEqualTo(first.snapshot().items().get(0).recommendationItemId());
        assertThat(jdbc.queryForObject("SELECT count(*) FROM recommendation_exposure_item", Integer.class))
                .isEqualTo(4);
        assertThat(exposures.findOwned(OTHER, BATCH)).isEmpty();
        assertThat(exposures.findOwned(OWNER, BATCH)).isPresent();
    }

    @Test
    void concurrentExactBatchRetryCreatesOneBatchAndOneItemSet() throws Exception {
        RecommenderPort.Result result = result(List.of(MOVIES.get(0), MOVIES.get(1)));
        List<RecommendationExposureService.SelectedItem> selected = selections(MOVIES.get(0), MOVIES.get(1));
        CountDownLatch start = new CountDownLatch(1);
        var pool = Executors.newFixedThreadPool(2);
        try {
            var first = pool.submit(() -> {
                start.await();
                return exposures.persistActualExposure(OWNER, BATCH, EXPOSED_AT, result, selected);
            });
            var second = pool.submit(() -> {
                start.await();
                return exposures.persistActualExposure(OWNER, BATCH, EXPOSED_AT, result, selected);
            });
            start.countDown();
            assertThat(List.of(first.get().replayed(), second.get().replayed()))
                    .containsExactlyInAnyOrder(false, true);
        } finally {
            pool.shutdownNow();
        }
        assertThat(jdbc.queryForObject("SELECT count(*) FROM recommendation_exposure_batch", Integer.class))
                .isEqualTo(1);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM recommendation_exposure_item", Integer.class))
                .isEqualTo(2);
    }

    @Test
    void foreignKeyFailureRollsBackTheWholeBatchAndStarConstraintFailsClosed() {
        UUID missingMovie = UUID.fromString("ffffffff-ffff-4fff-8fff-ffffffffffff");
        RecommenderPort.Result result = result(List.of(MOVIES.get(0), missingMovie));
        assertThatThrownBy(() -> exposures.persistActualExposure(
                OWNER, BATCH, EXPOSED_AT, result, selections(MOVIES.get(0), missingMovie)
        )).isInstanceOfSatisfying(RecommendationExposureException.class,
                failure -> assertThat(failure.code())
                        .isEqualTo(RecommendationExposureException.Code.EXPOSURE_PERSISTENCE_REJECTED));
        assertThat(jdbc.queryForObject("SELECT count(*) FROM recommendation_exposure_batch", Integer.class))
                .isZero();
        assertThat(jdbc.queryForObject("SELECT count(*) FROM recommendation_exposure_item", Integer.class))
                .isZero();

        RecommendationExposurePort.PersistResult valid = exposures.persistActualExposure(
                OWNER, BATCH, EXPOSED_AT, result(List.of(MOVIES.get(0))),
                selections(MOVIES.get(0))
        );
        UUID itemId = valid.snapshot().items().get(0).recommendationItemId();
        assertThatThrownBy(() -> jdbc.update("""
                UPDATE recommendation_exposure_item
                   SET expected_star_display_eligible = true
                 WHERE recommendation_item_id = ?
                """, itemId)).isInstanceOf(DataIntegrityViolationException.class);
        assertThat(jdbc.queryForObject("""
                SELECT expected_star_display_eligible FROM recommendation_exposure_item
                 WHERE recommendation_item_id = ?
                """, Boolean.class, itemId)).isFalse();
    }

    @Test
    void failuresAndLogsExposeOnlySafeCodes(CapturedOutput output) {
        RecommenderPort.Result result = result(List.of(MOVIES.get(0)));
        exposures.persistActualExposure(
                OWNER, BATCH, EXPOSED_AT, result, selections(MOVIES.get(0))
        );
        assertThatThrownBy(() -> exposures.persistActualExposure(
                OTHER, BATCH, EXPOSED_AT, result, selections(MOVIES.get(0))
        )).isInstanceOfSatisfying(RecommendationExposureException.class, failure -> {
            assertThat(failure.getMessage()).isEqualTo("EXPOSURE_BATCH_REUSED");
            assertThat(failure.getMessage()).doesNotContain(
                    OWNER.toString(), OTHER.toString(), MOVIES.get(0).toString()
            );
        });
        assertThat(output.getAll()).doesNotContain(
                OWNER.toString(), OTHER.toString(), MOVIES.get(0).toString(),
                "recommendation-v1-exposure", "fixture-family-v1"
        );
    }

    private List<RecommendationExposureService.SelectedItem> selections(UUID... movies) {
        List<RecommendationExposureService.SelectedItem> selected = new ArrayList<>();
        for (int index = 0; index < movies.length; index++) {
            selected.add(new RecommendationExposureService.SelectedItem(movies[index], index + 1));
        }
        return selected;
    }

    private RecommenderPort.Result result(List<UUID> movies) {
        ObjectNode snapshot = JSON.createObjectNode();
        snapshot.put("recommendationVersion", "recommendation-v1-exposure");
        snapshot.put("artifactSetVersion", "artifact-set-v1");
        snapshot.put("compatibilityId", "fixture-family-v1");
        snapshot.put("policyVersion", "policy-v1");
        snapshot.put("rankingPolicy", "BAYESIAN_POPULARITY_ONLY");
        snapshot.put("rankingAlpha", 0.0);
        snapshot.put("mappingVersion", "mapping-v1");
        snapshot.put("catalogVersion", "catalog-fixture-20260829-01");
        snapshot.put("candidateSetVersion", "candidate-v1");
        snapshot.put("inputVersion", "c2-active-rating-input-v1:sha256:" + "c".repeat(64));
        ObjectNode versions = snapshot.putObject("modelVersions");
        ObjectNode checksums = snapshot.putObject("payloadChecksums");
        for (String key : List.of("bias", "factors", "calibration", "mapping")) {
            versions.put(key, key + "-v1");
            checksums.put(key, key.equals("mapping") ? "b".repeat(64) : "a".repeat(64));
        }
        List<RecommenderPort.Item> items = new ArrayList<>();
        for (int index = 0; index < movies.size(); index++) {
            ObjectNode item = JSON.createObjectNode();
            item.put("movieId", movies.get(index).toString());
            item.put("rank", index + 1);
            item.put("rankingSource", "BAYESIAN_POPULARITY");
            ObjectNode star = item.putObject("expectedStar");
            star.put("status", "NOT_COMPUTED");
            star.putNull("value");
            star.put("displayEligible", false);
            star.put("confidence", "NOT_EVALUATED");
            star.putNull("confidencePolicyVersion");
            item.putArray("reasons");
            items.add(new RecommenderPort.Item(movies.get(index), index + 1, item));
        }
        return new RecommenderPort.Result(REQUEST, "COMPLETE", snapshot, items, List.of());
    }
}
