package com.feelm.catalog.c2.recommendation;

import org.springframework.context.annotation.Profile;
import org.springframework.dao.DataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Clock;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

@Component
@Profile({"postgres", "local"})
public class PostgresRecommendationExposureAdapter implements RecommendationExposurePort {
    private final JdbcTemplate jdbc;
    private final Clock clock;

    public PostgresRecommendationExposureAdapter(JdbcTemplate jdbc, Clock clock) {
        this.jdbc = jdbc;
        this.clock = clock;
    }

    @Override
    @Transactional(isolation = Isolation.READ_COMMITTED, propagation = Propagation.REQUIRES_NEW)
    public PersistResult persist(ExposureSnapshot snapshot) {
        try {
            lockBatch(snapshot.exposureBatchId());
            List<ExistingBatch> existing = jdbc.query("""
                    SELECT actor_user_id, canonical_payload_sha256
                      FROM recommendation_exposure_batch
                     WHERE exposure_batch_id = ?
                    """, (rs, row) -> new ExistingBatch(
                    rs.getObject("actor_user_id", UUID.class),
                    rs.getString("canonical_payload_sha256")
            ), snapshot.exposureBatchId());
            if (!existing.isEmpty()) {
                ExistingBatch prior = existing.get(0);
                if (!prior.actorUserId().equals(snapshot.actorUserId())
                        || !prior.canonicalPayloadSha256().equals(snapshot.canonicalPayloadSha256())) {
                    throw new RecommendationExposureException(
                            RecommendationExposureException.Code.EXPOSURE_BATCH_REUSED
                    );
                }
                return new PersistResult(
                        findOwned(snapshot.actorUserId(), snapshot.exposureBatchId()).orElseThrow(), true
                );
            }

            OffsetDateTime createdAt = OffsetDateTime.ofInstant(clock.instant(), ZoneOffset.UTC);
            jdbc.update("""
                    INSERT INTO recommendation_exposure_batch (
                        exposure_batch_id, source_request_id, actor_user_id,
                        recommendation_version, artifact_set_version, compatibility_id,
                        policy_version, ranking_policy, ranking_alpha, mapping_version,
                        catalog_version, candidate_set_version, input_version,
                        bias_model_version, bias_payload_sha256,
                        factors_model_version, factors_payload_sha256,
                        calibration_model_version, calibration_payload_sha256,
                        mapping_model_version, mapping_payload_sha256,
                        attribution_policy_version, exposed_at, item_count,
                        canonical_payload_sha256, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    snapshot.exposureBatchId(), snapshot.sourceRequestId(), snapshot.actorUserId(),
                    snapshot.recommendationVersion(), snapshot.artifactSetVersion(), snapshot.compatibilityId(),
                    snapshot.policyVersion(), snapshot.rankingPolicy(), snapshot.rankingAlpha(),
                    snapshot.mappingVersion(), snapshot.catalogVersion(), snapshot.candidateSetVersion(),
                    snapshot.inputVersion(), snapshot.bias().version(), snapshot.bias().payloadSha256(),
                    snapshot.factors().version(), snapshot.factors().payloadSha256(),
                    snapshot.calibration().version(), snapshot.calibration().payloadSha256(),
                    snapshot.mapping().version(), snapshot.mapping().payloadSha256(),
                    snapshot.attributionPolicyVersion(),
                    OffsetDateTime.ofInstant(snapshot.exposedAt(), ZoneOffset.UTC), snapshot.items().size(),
                    snapshot.canonicalPayloadSha256(), createdAt
            );

            List<ExposureItem> persistedItems = new ArrayList<>();
            for (ExposureItem item : snapshot.items()) {
                UUID recommendationItemId = UUID.randomUUID();
                jdbc.update("""
                        INSERT INTO recommendation_exposure_item (
                            recommendation_item_id, exposure_batch_id, actor_user_id, movie_id,
                            position, source_rank, recommendation_type,
                            expected_star_status, expected_star_value,
                            expected_star_display_eligible, expected_star_confidence,
                            expected_star_confidence_policy_version
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        recommendationItemId, snapshot.exposureBatchId(), snapshot.actorUserId(), item.movieId(),
                        item.position(), item.sourceRank(), item.recommendationType(),
                        item.expectedStar().status(), item.expectedStar().value(),
                        item.expectedStar().displayEligible(), item.expectedStar().confidence(),
                        item.expectedStar().confidencePolicyVersion()
                );
                persistedItems.add(new ExposureItem(
                        recommendationItemId, item.movieId(), item.position(), item.sourceRank(),
                        item.recommendationType(), item.expectedStar()
                ));
            }
            return new PersistResult(copyWithItems(snapshot, persistedItems), false);
        } catch (RecommendationExposureException safe) {
            throw safe;
        } catch (DataAccessException rejected) {
            throw new RecommendationExposureException(
                    RecommendationExposureException.Code.EXPOSURE_PERSISTENCE_REJECTED
            );
        }
    }

    @Override
    @Transactional(readOnly = true, isolation = Isolation.READ_COMMITTED)
    public Optional<ExposureSnapshot> findOwned(UUID actorUserId, UUID exposureBatchId) {
        List<ExposureSnapshot> batches = jdbc.query("""
                SELECT * FROM recommendation_exposure_batch
                 WHERE exposure_batch_id = ? AND actor_user_id = ?
                """, (rs, row) -> mapBatch(rs, List.of()),
                exposureBatchId, actorUserId);
        if (batches.isEmpty()) {
            return Optional.empty();
        }
        return Optional.of(copyWithItems(
                batches.get(0), loadItems(actorUserId, exposureBatchId)
        ));
    }

    private List<ExposureItem> loadItems(UUID actorUserId, UUID exposureBatchId) {
        return jdbc.query("""
                SELECT recommendation_item_id, movie_id, position, source_rank, recommendation_type,
                       expected_star_status, expected_star_value,
                       expected_star_display_eligible, expected_star_confidence,
                       expected_star_confidence_policy_version
                  FROM recommendation_exposure_item
                 WHERE exposure_batch_id = ? AND actor_user_id = ?
                 ORDER BY position
                """, (rs, row) -> new ExposureItem(
                rs.getObject("recommendation_item_id", UUID.class),
                rs.getObject("movie_id", UUID.class),
                rs.getInt("position"),
                rs.getInt("source_rank"),
                rs.getString("recommendation_type"),
                new ExpectedStar(
                        rs.getString("expected_star_status"),
                        rs.getBigDecimal("expected_star_value"),
                        rs.getBoolean("expected_star_display_eligible"),
                        rs.getString("expected_star_confidence"),
                        rs.getString("expected_star_confidence_policy_version")
                )
        ), exposureBatchId, actorUserId);
    }

    private ExposureSnapshot mapBatch(ResultSet rs, List<ExposureItem> items) throws SQLException {
        return new ExposureSnapshot(
                rs.getObject("exposure_batch_id", UUID.class),
                rs.getObject("source_request_id", UUID.class),
                rs.getObject("actor_user_id", UUID.class),
                rs.getString("recommendation_version"),
                rs.getString("artifact_set_version"),
                rs.getString("compatibility_id"),
                rs.getString("policy_version"),
                rs.getString("ranking_policy"),
                rs.getBigDecimal("ranking_alpha"),
                rs.getString("mapping_version"),
                rs.getString("catalog_version"),
                rs.getString("candidate_set_version"),
                rs.getString("input_version"),
                new ModelArtifact(rs.getString("bias_model_version"), rs.getString("bias_payload_sha256")),
                new ModelArtifact(rs.getString("factors_model_version"), rs.getString("factors_payload_sha256")),
                new ModelArtifact(rs.getString("calibration_model_version"), rs.getString("calibration_payload_sha256")),
                new ModelArtifact(rs.getString("mapping_model_version"), rs.getString("mapping_payload_sha256")),
                rs.getString("attribution_policy_version"),
                rs.getObject("exposed_at", OffsetDateTime.class).toInstant(),
                rs.getString("canonical_payload_sha256"),
                items
        );
    }

    private ExposureSnapshot copyWithItems(ExposureSnapshot value, List<ExposureItem> items) {
        return new ExposureSnapshot(
                value.exposureBatchId(), value.sourceRequestId(), value.actorUserId(),
                value.recommendationVersion(), value.artifactSetVersion(), value.compatibilityId(),
                value.policyVersion(), value.rankingPolicy(), value.rankingAlpha(), value.mappingVersion(),
                value.catalogVersion(), value.candidateSetVersion(), value.inputVersion(), value.bias(),
                value.factors(), value.calibration(), value.mapping(), value.attributionPolicyVersion(),
                value.exposedAt(), value.canonicalPayloadSha256(), items
        );
    }

    private void lockBatch(UUID batchId) {
        jdbc.query(
                "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                (rs, row) -> 0,
                batchId + ":C2_RECOMMENDATION_EXPOSURE"
        );
    }

    private record ExistingBatch(UUID actorUserId, String canonicalPayloadSha256) {
    }
}
