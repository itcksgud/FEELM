package com.feelm.catalog.c2b.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.feelm.catalog.api.ApiException;
import com.feelm.catalog.c1.foundation.C1FoundationException;
import com.feelm.catalog.c1.foundation.C1IdempotencyService;
import com.feelm.catalog.c2.recommendation.C2RecommendationFailure;
import com.feelm.catalog.c2.recommendation.RecommenderPort;
import com.feelm.catalog.c2b.api.C2BApiDtos;
import org.springframework.context.annotation.Profile;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Clock;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;

import static com.feelm.catalog.c2b.api.C2BApiDtos.*;

@Service
@Profile({"postgres", "local"})
public final class PersonalDiscoveryService {
    private static final int PAGE_SIZE = 3;
    private static final int MAX_CANDIDATES = 500;
    private static final String GENRE_SEPARATOR = "\u001f";

    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;
    private final Clock clock;
    private final PersonalDiscoveryRankPort ranking;
    private final PersonalDiscoveryCursorCodec cursors;
    private final C1IdempotencyService idempotency;
    private final PlatformTransactionManager transactionManager;

    public PersonalDiscoveryService(
            JdbcTemplate jdbc,
            ObjectMapper objectMapper,
            Clock clock,
            PersonalDiscoveryRankPort ranking,
            PersonalDiscoveryCursorCodec cursors,
            C1IdempotencyService idempotency,
            PlatformTransactionManager transactionManager
    ) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
        this.clock = clock;
        this.ranking = ranking;
        this.cursors = cursors;
        this.idempotency = idempotency;
        this.transactionManager = transactionManager;
    }

    public RecommendationDelivery getOrCreate(UUID actor) {
        DeliveryRow existing = findActive(actor, false);
        if (existing != null) {
            return render(existing);
        }
        RecommenderPort.Result result = rank(actor);
        return transaction().execute(status -> {
            lockActor(actor);
            DeliveryRow concurrent = findActive(actor, true);
            if (concurrent != null) {
                return render(concurrent);
            }
            RankedSnapshot snapshot = validate(result);
            Selection selection = select(actor, result.items(), 0);
            UUID deliveryId = UUID.randomUUID();
            OffsetDateTime current = now();
            jdbc.update("""
                    INSERT INTO recommendation_delivery (
                        id, actor_user_id, status, revision, label, composition,
                        recommendation_version, policy_version, mapping_version, catalog_version,
                        candidate_set_version, input_version, candidate_count, scan_offset,
                        created_at, updated_at
                    ) VALUES (?, ?, 'ACTIVE', 1, 'POPULARITY_BASELINE', 'BASELINE_THREE',
                              ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    deliveryId, actor, snapshot.recommendationVersion(), snapshot.policyVersion(),
                    snapshot.mappingVersion(), snapshot.catalogVersion(), snapshot.candidateSetVersion(),
                    snapshot.inputVersion(), snapshot.candidateCount(), selection.scanOffset(), current, current);
            insertItems(actor, deliveryId, 0, null, selection.selected(), current);
            return render(findActive(actor, true));
        });
    }

    public HttpMutation append(
            UUID actor,
            UUID deliveryId,
            String key,
            AppendRecommendationsRequest request
    ) {
        ObjectNode canonical = objectMapper.createObjectNode()
                .put("appendEventId", request.appendEventId().toString())
                .put("deliveryId", deliveryId.toString())
                .put("expectedRevision", request.expectedRevision())
                .put("cursor", request.cursor());
        try {
            C1IdempotencyService.ExecutionResult execution = idempotency.execute(
                    actor,
                    "APPEND_PERSONAL_DISCOVERY",
                    key,
                    canonical,
                    () -> appendMutation(actor, deliveryId, request, canonical)
            );
            boolean replayed = execution.replayed() || execution.response().status() == 200;
            RecommendationAppend stored = objectMapper.convertValue(
                    execution.response().body(), RecommendationAppend.class
            );
            RecommendationAppend wire = stored.withReplayed(replayed);
            return new HttpMutation(replayed ? 200 : 201, objectMapper.valueToTree(wire));
        } catch (C1FoundationException exception) {
            throw translateIdempotency(exception);
        }
    }

    public HttpMutation dismiss(
            UUID actor,
            UUID deliveryItemId,
            String key,
            DismissRecommendationRequest request
    ) {
        ObjectNode canonical = objectMapper.createObjectNode()
                .put("dismissalEventId", request.dismissalEventId().toString())
                .put("deliveryItemId", deliveryItemId.toString())
                .put("expectedRevision", request.expectedRevision())
                .put("reason", request.reason());
        try {
            C1IdempotencyService.ExecutionResult execution = idempotency.execute(
                    actor,
                    "DISMISS_RECOMMENDATION",
                    key,
                    canonical,
                    () -> dismissMutation(actor, deliveryItemId, request, canonical)
            );
            boolean replayed = execution.replayed() || execution.response().status() == 200;
            RecommendationDismissal stored = objectMapper.convertValue(
                    execution.response().body(), RecommendationDismissal.class
            );
            RecommendationDismissal wire = stored.withReplayed(replayed);
            return new HttpMutation(replayed ? 200 : 201, objectMapper.valueToTree(wire));
        } catch (C1FoundationException exception) {
            throw translateIdempotency(exception);
        }
    }

    private C1IdempotencyService.MutationResponse appendMutation(
            UUID actor,
            UUID deliveryId,
            AppendRecommendationsRequest request,
            JsonNode canonical
    ) {
        lockActor(actor);
        lockEvent(actor, "APPEND", request.appendEventId());
        String requestHash = fingerprint(canonical);
        StoredEvent existing = findAppendEvent(request.appendEventId());
        if (existing != null) {
            requireOwnedEvent(existing, actor, deliveryId, requestHash);
            return new C1IdempotencyService.MutationResponse(200, existing.body(), request.appendEventId());
        }

        DeliveryRow delivery = findOwned(deliveryId, actor, true);
        if (delivery == null) {
            throw notFound();
        }
        PersonalDiscoveryCursorCodec.Decoded cursor = cursors.decode(
                request.cursor(), actor, deliveryId, clock.instant()
        );
        if (delivery.revision() != request.expectedRevision()
                || cursor.revision() != delivery.revision()
                || cursor.offset() != delivery.scanOffset()) {
            throw stale();
        }

        RecommenderPort.Result result = rank(actor);
        RankedSnapshot snapshot = validate(result);
        requireCompatible(delivery, snapshot);
        Selection selection = select(actor, result.items(), delivery.scanOffset());
        int nextPosition = jdbc.queryForObject("""
                SELECT coalesce(max(sequence_position), 0) + 1
                  FROM recommendation_delivery_item WHERE delivery_id = ?
                """, Integer.class, deliveryId);
        OffsetDateTime current = now();
        List<DeliveryItem> appended = insertItems(
                actor, deliveryId, nextPosition - 1, request.appendEventId(), selection.selected(), current
        );
        int nextRevision = delivery.revision() + 1;
        jdbc.update("""
                UPDATE recommendation_delivery
                   SET revision = ?, scan_offset = ?, candidate_count = ?, input_version = ?, updated_at = ?
                 WHERE id = ? AND actor_user_id = ? AND revision = ?
                """, nextRevision, selection.scanOffset(), snapshot.candidateCount(), snapshot.inputVersion(),
                current, deliveryId, actor, delivery.revision());

        DeliveryRow updated = findOwned(deliveryId, actor, true);
        RecommendationPageInfo pageInfo = pageInfo(updated, activeItemCount(deliveryId));
        List<SafeIssue> issues = issues(selection.issueCounts());
        String outcome = appended.size() == PAGE_SIZE ? "COMPLETE" : appended.isEmpty() ? "EMPTY" : "PARTIAL";
        RecommendationAppend response = new RecommendationAppend(
                request.appendEventId(), deliveryId, nextRevision, outcome,
                new SelectionSummary(selection.scannedCount(), appended.size(), selection.excludedCount()),
                appended, issues, pageInfo, false
        );
        JsonNode storedBody = withoutReplay(objectMapper.valueToTree(response));
        jdbc.update("""
                INSERT INTO recommendation_append_event (
                    append_event_id, actor_user_id, delivery_id, canonical_request_sha256,
                    result_revision, appended_item_count, response_body, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?::jsonb, ?)
                """, request.appendEventId(), actor, deliveryId, requestHash, nextRevision, appended.size(),
                storedBody.toString(), current);
        return new C1IdempotencyService.MutationResponse(201, storedBody, request.appendEventId());
    }

    private C1IdempotencyService.MutationResponse dismissMutation(
            UUID actor,
            UUID deliveryItemId,
            DismissRecommendationRequest request,
            JsonNode canonical
    ) {
        lockActor(actor);
        lockEvent(actor, "DISMISS", request.dismissalEventId());
        String requestHash = fingerprint(canonical);
        StoredDismissal existing = findDismissal(request.dismissalEventId());
        if (existing != null) {
            if (!existing.actor().equals(actor)) {
                throw notFound();
            }
            if (!existing.itemId().equals(deliveryItemId) || !existing.requestHash().equals(requestHash)) {
                throw conflict("IDEMPOTENCY_KEY_REUSED");
            }
            return new C1IdempotencyService.MutationResponse(200, existing.body(), request.dismissalEventId());
        }

        ItemOwner item = findItemOwner(deliveryItemId, actor);
        if (item == null) {
            throw notFound();
        }
        DeliveryRow delivery = findOwned(item.deliveryId(), actor, true);
        if (delivery == null) {
            throw notFound();
        }
        if (delivery.revision() != request.expectedRevision()) {
            throw stale();
        }
        if (!"ACTIVE".equals(item.status())) {
            throw conflict("RECOMMENDATION_ITEM_TERMINAL");
        }
        OffsetDateTime current = now();
        int nextRevision = delivery.revision() + 1;
        jdbc.update("""
                UPDATE recommendation_delivery_item
                   SET status = 'DISMISSED_NOT_INTERESTED', terminal_at = ?, terminal_event_id = ?
                 WHERE id = ? AND actor_user_id = ? AND status = 'ACTIVE'
                """, current, request.dismissalEventId(), deliveryItemId, actor);
        jdbc.update("""
                UPDATE recommendation_delivery SET revision = ?, updated_at = ?
                 WHERE id = ? AND actor_user_id = ? AND revision = ?
                """, nextRevision, current, delivery.id(), actor, delivery.revision());
        RecommendationDismissal response = new RecommendationDismissal(
                request.dismissalEventId(), deliveryItemId, nextRevision,
                "DISMISSED_NOT_INTERESTED", current, false
        );
        JsonNode storedBody = withoutReplay(objectMapper.valueToTree(response));
        jdbc.update("""
                INSERT INTO recommendation_dismissal_event (
                    dismissal_event_id, actor_user_id, delivery_item_id, canonical_request_sha256,
                    result_revision, occurred_at, response_body
                ) VALUES (?, ?, ?, ?, ?, ?, ?::jsonb)
                """, request.dismissalEventId(), actor, deliveryItemId, requestHash,
                nextRevision, current, storedBody.toString());
        return new C1IdempotencyService.MutationResponse(201, storedBody, request.dismissalEventId());
    }

    private Selection select(UUID actor, List<RecommenderPort.Item> ranked, int offset) {
        List<CandidateCard> selected = new ArrayList<>();
        Map<String, Integer> counts = new LinkedHashMap<>();
        int scanned = 0;
        int excluded = 0;
        int scanOffset = offset;
        for (RecommenderPort.Item item : ranked) {
            if (item.rank() <= offset) {
                continue;
            }
            if (item.rank() > MAX_CANDIDATES || selected.size() == PAGE_SIZE) {
                break;
            }
            scanned++;
            scanOffset = item.rank();
            CandidateCard candidate = candidate(actor, item.movieId(), item.rank());
            String issue = candidate == null ? "CANDIDATE_NOT_UI_READY"
                    : candidate.rated() ? "CANDIDATE_ALREADY_RATED"
                    : candidate.seen() ? "CANDIDATE_ALREADY_SEEN" : null;
            if (issue != null) {
                counts.merge(issue, 1, Integer::sum);
                excluded++;
                continue;
            }
            selected.add(candidate);
        }
        return new Selection(selected, counts, scanned, excluded, scanOffset);
    }

    private CandidateCard candidate(UUID actor, UUID movieId, int sourceRank) {
        List<CandidateCard> values = jdbc.query("""
                SELECT p.movie_id,
                       COALESCE((SELECT ml.title FROM movie_localization ml
                                  WHERE ml.catalog_version_id = p.catalog_version_id AND ml.movie_id = p.movie_id
                                  ORDER BY CASE ml.locale WHEN 'ko-KR' THEN 0 WHEN 'en-US' THEN 1 ELSE 2 END
                                  LIMIT 1), p.original_title) AS display_title,
                       p.poster_path,
                       extract(year from p.release_date)::int AS release_year,
                       COALESCE((SELECT string_agg(g.display_name_ko, ? ORDER BY mg.display_order)
                                   FROM movie_genre mg JOIN genre g ON g.id = mg.genre_id
                                  WHERE mg.catalog_version_id = p.catalog_version_id AND mg.movie_id = p.movie_id), '') AS genres,
                       EXISTS(SELECT 1 FROM rating r WHERE r.user_id = ? AND r.movie_id = p.movie_id
                              AND r.logical_status = 'ACTIVE') AS rated,
                       EXISTS(SELECT 1 FROM viewing_record v WHERE v.user_id = ? AND v.movie_id = p.movie_id) AS seen
                  FROM catalog_version cv
                  JOIN movie_catalog_projection p ON p.catalog_version_id = cv.id
                 WHERE cv.status = 'ACTIVE' AND p.movie_id = ?
                   AND p.visibility_status = 'UI_READY' AND p.identity_status = 'IDENTITY_VERIFIED'
                   AND p.deleted = false
                """, (rs, row) -> new CandidateCard(
                rs.getObject("movie_id", UUID.class), sourceRank, rs.getString("display_title"),
                posterUrl(rs.getString("poster_path")), (Integer) rs.getObject("release_year"),
                splitGenres(rs.getString("genres")), rs.getBoolean("rated"), rs.getBoolean("seen")
        ), GENRE_SEPARATOR, actor, actor, movieId);
        return values.isEmpty() ? null : values.get(0);
    }

    private List<DeliveryItem> insertItems(
            UUID actor,
            UUID deliveryId,
            int priorPosition,
            UUID appendEventId,
            List<CandidateCard> candidates,
            OffsetDateTime current
    ) {
        List<DeliveryItem> result = new ArrayList<>();
        int position = priorPosition;
        for (CandidateCard candidate : candidates) {
            position++;
            UUID itemId = UUID.randomUUID();
            jdbc.update("""
                    INSERT INTO recommendation_delivery_item (
                        id, delivery_id, actor_user_id, movie_id, sequence_position, source_rank,
                        recommendation_type, status, display_title, poster_url, release_year,
                        genre_labels, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'POPULARITY_BASELINE', 'ACTIVE', ?, ?, ?, ?, ?)
                    """, itemId, deliveryId, actor, candidate.movieId(), position, candidate.sourceRank(),
                    candidate.title(), candidate.posterUrl(), candidate.releaseYear(),
                    String.join(GENRE_SEPARATOR, candidate.genres()), current);
            result.add(toItem(itemId, position, candidate));
        }
        return result;
    }

    private RecommendationDelivery render(DeliveryRow delivery) {
        List<DeliveryItem> items = jdbc.query("""
                SELECT id, sequence_position, source_rank, movie_id, display_title, poster_url,
                       release_year, genre_labels
                  FROM recommendation_delivery_item
                 WHERE delivery_id = ? AND actor_user_id = ? AND status = 'ACTIVE'
                 ORDER BY sequence_position
                """, (rs, row) -> new DeliveryItem(
                rs.getObject("id", UUID.class), rs.getInt("sequence_position"),
                rs.getInt("source_rank"), "POPULARITY_BASELINE",
                new MovieCard(
                        rs.getObject("movie_id", UUID.class), rs.getString("display_title"),
                        rs.getString("poster_url"), (Integer) rs.getObject("release_year"),
                        splitGenres(rs.getString("genre_labels"))
                )
        ), delivery.id(), delivery.actor());
        return new RecommendationDelivery(
                delivery.id(), delivery.revision(), "POPULARITY_BASELINE",
                "BASELINE_THREE", items, pageInfo(delivery, items.size())
        );
    }

    private RecommendationPageInfo pageInfo(DeliveryRow delivery, int activeItems) {
        boolean hasMore = delivery.scanOffset() < delivery.candidateCount();
        if (!hasMore) {
            return new RecommendationPageInfo(activeItems, false, null, null);
        }
        Instant expires = clock.instant().plusSeconds(600);
        return new RecommendationPageInfo(
                activeItems, true,
                cursors.encode(delivery.actor(), delivery.id(), delivery.revision(), delivery.scanOffset(), expires),
                OffsetDateTime.ofInstant(expires, ZoneOffset.UTC)
        );
    }

    private RankedSnapshot validate(RecommenderPort.Result result) {
        if (result == null || result.snapshot() == null || result.items() == null || result.items().isEmpty()) {
            throw unavailable();
        }
        JsonNode snapshot = result.snapshot();
        if (!"BAYESIAN_POPULARITY_ONLY".equals(text(snapshot, "rankingPolicy"))
                || snapshot.path("rankingAlpha").decimalValue().signum() != 0) {
            throw unavailable();
        }
        Set<UUID> unique = new HashSet<>();
        int expectedRank = 1;
        for (RecommenderPort.Item item : result.items()) {
            if (item == null || item.movieId() == null || item.rank() != expectedRank
                    || item.rank() > MAX_CANDIDATES || !unique.add(item.movieId())) {
                throw unavailable();
            }
            JsonNode star = item.value() == null ? null : item.value().path("expectedStar");
            if (star == null || !"NOT_COMPUTED".equals(star.path("status").asText())
                    || !star.path("value").isNull() || star.path("displayEligible").asBoolean(true)) {
                throw unavailable();
            }
            expectedRank++;
        }
        return new RankedSnapshot(
                text(snapshot, "recommendationVersion"), text(snapshot, "policyVersion"),
                text(snapshot, "mappingVersion"), text(snapshot, "catalogVersion"),
                text(snapshot, "candidateSetVersion"), text(snapshot, "inputVersion"), result.items().size()
        );
    }

    private void requireCompatible(DeliveryRow delivery, RankedSnapshot current) {
        if (!delivery.recommendationVersion().equals(current.recommendationVersion())
                || !delivery.policyVersion().equals(current.policyVersion())
                || !delivery.mappingVersion().equals(current.mappingVersion())
                || !delivery.catalogVersion().equals(current.catalogVersion())
                || !delivery.candidateSetVersion().equals(current.candidateSetVersion())) {
            throw stale();
        }
    }

    private RecommenderPort.Result rank(UUID actor) {
        try {
            return ranking.rank(actor, UUID.randomUUID());
        } catch (C2RecommendationFailure | IllegalArgumentException failure) {
            throw unavailable();
        }
    }

    private DeliveryRow findActive(UUID actor, boolean lock) {
        List<DeliveryRow> values = jdbc.query("""
                SELECT * FROM recommendation_delivery
                 WHERE actor_user_id = ? AND status = 'ACTIVE'
                """ + (lock ? " FOR UPDATE" : ""), (rs, row) -> deliveryRow(rs), actor);
        return values.isEmpty() ? null : values.get(0);
    }

    private DeliveryRow findOwned(UUID deliveryId, UUID actor, boolean lock) {
        List<DeliveryRow> values = jdbc.query("""
                SELECT * FROM recommendation_delivery
                 WHERE id = ? AND actor_user_id = ? AND status = 'ACTIVE'
                """ + (lock ? " FOR UPDATE" : ""), (rs, row) -> deliveryRow(rs), deliveryId, actor);
        return values.isEmpty() ? null : values.get(0);
    }

    private DeliveryRow deliveryRow(java.sql.ResultSet rs) throws java.sql.SQLException {
        return new DeliveryRow(
                rs.getObject("id", UUID.class), rs.getObject("actor_user_id", UUID.class),
                rs.getInt("revision"), rs.getString("recommendation_version"), rs.getString("policy_version"),
                rs.getString("mapping_version"), rs.getString("catalog_version"),
                rs.getString("candidate_set_version"), rs.getString("input_version"),
                rs.getInt("candidate_count"), rs.getInt("scan_offset")
        );
    }

    private ItemOwner findItemOwner(UUID itemId, UUID actor) {
        List<ItemOwner> values = jdbc.query("""
                SELECT id, delivery_id, status FROM recommendation_delivery_item
                 WHERE id = ? AND actor_user_id = ? FOR UPDATE
                """, (rs, row) -> new ItemOwner(
                rs.getObject("id", UUID.class), rs.getObject("delivery_id", UUID.class), rs.getString("status")
        ), itemId, actor);
        return values.isEmpty() ? null : values.get(0);
    }

    private StoredEvent findAppendEvent(UUID eventId) {
        List<StoredEvent> values = jdbc.query("""
                SELECT actor_user_id, delivery_id, canonical_request_sha256, response_body
                  FROM recommendation_append_event WHERE append_event_id = ?
                """, (rs, row) -> new StoredEvent(
                rs.getObject("actor_user_id", UUID.class), rs.getObject("delivery_id", UUID.class),
                rs.getString("canonical_request_sha256"), readJson(rs.getString("response_body"))
        ), eventId);
        return values.isEmpty() ? null : values.get(0);
    }

    private StoredDismissal findDismissal(UUID eventId) {
        List<StoredDismissal> values = jdbc.query("""
                SELECT actor_user_id, delivery_item_id, canonical_request_sha256, response_body
                  FROM recommendation_dismissal_event WHERE dismissal_event_id = ?
                """, (rs, row) -> new StoredDismissal(
                rs.getObject("actor_user_id", UUID.class), rs.getObject("delivery_item_id", UUID.class),
                rs.getString("canonical_request_sha256"), readJson(rs.getString("response_body"))
        ), eventId);
        return values.isEmpty() ? null : values.get(0);
    }

    private void requireOwnedEvent(StoredEvent event, UUID actor, UUID delivery, String hash) {
        if (!event.actor().equals(actor)) {
            throw notFound();
        }
        if (!event.deliveryId().equals(delivery) || !event.requestHash().equals(hash)) {
            throw conflict("IDEMPOTENCY_KEY_REUSED");
        }
    }

    private int activeItemCount(UUID deliveryId) {
        return jdbc.queryForObject("""
                SELECT count(*) FROM recommendation_delivery_item
                 WHERE delivery_id = ? AND status = 'ACTIVE'
                """, Integer.class, deliveryId);
    }

    private List<SafeIssue> issues(Map<String, Integer> counts) {
        return List.of("CANDIDATE_NOT_UI_READY", "CANDIDATE_ALREADY_RATED", "CANDIDATE_ALREADY_SEEN")
                .stream().filter(counts::containsKey)
                .map(code -> new SafeIssue(code, counts.get(code), false)).toList();
    }

    private DeliveryItem toItem(UUID itemId, int position, CandidateCard candidate) {
        return new DeliveryItem(
                itemId, position, candidate.sourceRank(),
                "POPULARITY_BASELINE",
                new MovieCard(
                        candidate.movieId(), candidate.title(), candidate.posterUrl(),
                        candidate.releaseYear(), candidate.genres()
                )
        );
    }

    private JsonNode withoutReplay(JsonNode value) {
        JsonNode copy = value.deepCopy();
        if (copy instanceof ObjectNode object) {
            object.remove("replayed");
        }
        return copy;
    }

    private JsonNode readJson(String value) {
        try {
            return objectMapper.readTree(value);
        } catch (Exception exception) {
            throw unavailable();
        }
    }

    private String text(JsonNode object, String field) {
        JsonNode value = object.get(field);
        if (value == null || !value.isTextual() || value.textValue().isBlank()) {
            throw unavailable();
        }
        return value.textValue();
    }

    private String fingerprint(JsonNode value) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256")
                    .digest(objectMapper.writeValueAsBytes(value)));
        } catch (Exception exception) {
            throw unavailable();
        }
    }

    private void lockActor(UUID actor) {
        jdbc.query("SELECT pg_advisory_xact_lock(hashtextextended(?, 0))", (rs, row) -> 0,
                actor + ":PERSONAL_DISCOVERY");
    }

    private void lockEvent(UUID actor, String operation, UUID eventId) {
        jdbc.query("SELECT pg_advisory_xact_lock(hashtextextended(?, 0))", (rs, row) -> 0,
                actor + ":" + operation + ":" + eventId);
    }

    private TransactionTemplate transaction() {
        return new TransactionTemplate(transactionManager);
    }

    private OffsetDateTime now() {
        return OffsetDateTime.ofInstant(clock.instant(), ZoneOffset.UTC);
    }

    private static String posterUrl(String path) {
        return path == null ? null : "https://image.tmdb.org/t/p/w500" + path;
    }

    private static List<String> splitGenres(String value) {
        if (value == null || value.isEmpty()) {
            return List.of();
        }
        return List.of(value.split(GENRE_SEPARATOR, -1));
    }

    private static ApiException notFound() {
        return new ApiException(HttpStatus.NOT_FOUND, "RESOURCE_NOT_FOUND", "리소스를 찾을 수 없어요.");
    }

    private static ApiException stale() {
        return new ApiException(HttpStatus.CONFLICT, "RECOMMENDATION_DELIVERY_STALE", "추천 목록을 새로 확인해 주세요.");
    }

    private static ApiException conflict(String code) {
        return new ApiException(HttpStatus.CONFLICT, code, "추천 요청 상태가 충돌했어요.");
    }

    private static ApiException unavailable() {
        return new ApiException(
                HttpStatus.SERVICE_UNAVAILABLE, "RECOMMENDATION_UNAVAILABLE",
                "추천을 불러올 수 없어요. 잠시 후 다시 시도해 주세요."
        );
    }

    private static ApiException translateIdempotency(C1FoundationException exception) {
        if ("IDEMPOTENCY_KEY_REUSED".equals(exception.code())) {
            return conflict("IDEMPOTENCY_KEY_REUSED");
        }
        return new ApiException(HttpStatus.BAD_REQUEST, "VALIDATION_ERROR", "요청 값을 확인해 주세요.");
    }

    public record HttpMutation(int status, JsonNode body) {
    }

    private record RankedSnapshot(
            String recommendationVersion,
            String policyVersion,
            String mappingVersion,
            String catalogVersion,
            String candidateSetVersion,
            String inputVersion,
            int candidateCount
    ) {
    }

    private record DeliveryRow(
            UUID id,
            UUID actor,
            int revision,
            String recommendationVersion,
            String policyVersion,
            String mappingVersion,
            String catalogVersion,
            String candidateSetVersion,
            String inputVersion,
            int candidateCount,
            int scanOffset
    ) {
    }

    private record CandidateCard(
            UUID movieId,
            int sourceRank,
            String title,
            String posterUrl,
            Integer releaseYear,
            List<String> genres,
            boolean rated,
            boolean seen
    ) {
    }

    private record Selection(
            List<CandidateCard> selected,
            Map<String, Integer> issueCounts,
            int scannedCount,
            int excludedCount,
            int scanOffset
    ) {
    }

    private record ItemOwner(UUID itemId, UUID deliveryId, String status) {
    }

    private record StoredEvent(UUID actor, UUID deliveryId, String requestHash, JsonNode body) {
    }

    private record StoredDismissal(UUID actor, UUID itemId, String requestHash, JsonNode body) {
    }
}
