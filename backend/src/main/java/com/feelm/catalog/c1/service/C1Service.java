package com.feelm.catalog.c1.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.feelm.catalog.api.ApiException;
import com.feelm.catalog.c1.api.C1ApiDtos;
import com.feelm.catalog.c1.api.C1CursorCodec;
import com.feelm.catalog.c1.foundation.C1FoundationException;
import com.feelm.catalog.c1.foundation.C1IdempotencyService;
import com.feelm.catalog.c1.foundation.C1MutationJournal;
import com.feelm.catalog.c1.foundation.C1TimePolicy;
import org.springframework.context.annotation.Profile;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Clock;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static com.feelm.catalog.c1.api.C1ApiDtos.*;

@Service
@Profile({"postgres", "local"})
public class C1Service {
    private static final String DERIVATION_VERSION = "c1-v1";
    private static final String RECOMMENDATION_QUEUED = "QUEUED";

    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;
    private final Clock clock;
    private final C1TimePolicy timePolicy;
    private final C1IdempotencyService idempotency;
    private final C1MutationJournal journal;
    private final C1CursorCodec cursors;
    private final C1MutationFaultInjector faults;
    private final C1RatingCompletionPort ratingCompletion;

    public C1Service(
            JdbcTemplate jdbc,
            ObjectMapper objectMapper,
            Clock clock,
            C1TimePolicy timePolicy,
            C1IdempotencyService idempotency,
            C1MutationJournal journal,
            C1CursorCodec cursors,
            C1MutationFaultInjector faults,
            C1RatingCompletionPort ratingCompletion
    ) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
        this.clock = clock;
        this.timePolicy = timePolicy;
        this.idempotency = idempotency;
        this.journal = journal;
        this.cursors = cursors;
        this.faults = faults;
        this.ratingCompletion = ratingCompletion;
    }

    public HttpMutation createWatchIntent(
            UUID actor,
            String key,
            CreateWatchIntentRequest request,
            String traceId
    ) {
        ObjectNode canonical = objectMapper.createObjectNode()
                .put("movieId", request.movieId().toString())
                .put("offerId", request.offerId().toString());
        return execute(actor, "CREATE_WATCH_INTENT", key, canonical, () -> {
            lockDomain(actor, "WATCH_INTENT", request.movieId());
            expireActiveIntent(actor, request.movieId());
            Offer offer = findCurrentOffer(request.movieId(), request.offerId());
            if (offer == null) {
                throw notFound();
            }
            ExistingViewing viewed = findViewing(actor, request.movieId());
            ExistingIntent active = findActiveIntentForUpdate(actor, request.movieId());
            String outcome;
            ExistingIntent returnedIntent = null;
            UUID eventResource;
            int status;
            if (viewed != null) {
                outcome = "ALREADY_WATCHED";
                eventResource = viewed.sourceWatchIntentId();
                status = 200;
            } else if (active != null) {
                outcome = "ACTIVE_REUSED";
                returnedIntent = active;
                eventResource = active.id();
                status = 200;
            } else {
                UUID intentId = UUID.randomUUID();
                C1TimePolicy.Window window = timePolicy.fromFirstActiveClick(clock.instant());
                jdbc.update("""
                        INSERT INTO watch_intent (
                            id, user_id, movie_id, provider_id, source_offer_id, status,
                            clicked_at, confirmation_due_at, expires_at, responded_at, revision
                        ) VALUES (?, ?, ?, ?, ?, 'LINK_CLICKED', ?, ?, ?, NULL, 1)
                        """,
                        intentId, actor, request.movieId(), offer.providerId(), offer.offerId(),
                        utc(window.clickedAt()), utc(window.confirmationDueAt()), utc(window.expiresAt()));
                returnedIntent = new ExistingIntent(
                        intentId, "LINK_CLICKED", utc(window.clickedAt()), utc(window.confirmationDueAt()),
                        utc(window.expiresAt()), 1
                );
                eventResource = intentId;
                outcome = "CREATED";
                status = 201;
            }
            ObjectNode payload = objectMapper.createObjectNode()
                    .put("movieId", request.movieId().toString())
                    .put("providerId", offer.providerId().toString())
                    .put("linkType", offer.linkType());
            journal.append(actor, "OTT_LINK_CLICKED", "WATCH_INTENT", eventResource, traceId, payload);
            WatchIntentSnapshot snapshot = returnedIntent == null ? null : new WatchIntentSnapshot(
                    returnedIntent.id(), returnedIntent.status(), returnedIntent.clickedAt(),
                    returnedIntent.dueAt(), returnedIntent.expiresAt(), returnedIntent.revision());
            WatchIntentClickResult body = new WatchIntentClickResult(
                    outcome,
                    request.movieId(),
                    offer.providerId(),
                    snapshot,
                    new ExternalDestination(offer.linkType(), offer.destination(), true)
            );
            return mutation(status, body, eventResource);
        });
    }

    public CursorPage<PendingWatchConfirmation> pending(UUID actor, String cursor, int limit) {
        OffsetDateTime now = now();
        int total = count("""
                SELECT count(*) FROM watch_intent
                 WHERE user_id = ?
                   AND status IN ('LINK_CLICKED', 'CONFIRMATION_PENDING')
                   AND confirmation_due_at <= ? AND expires_at > ?
                """, actor, now, now);
        String revision = scalarString("""
                SELECT count(*) || ':' || coalesce(sum(revision), 0)
                  FROM watch_intent
                 WHERE user_id = ? AND status IN ('LINK_CLICKED', 'CONFIRMATION_PENDING')
                """, actor);
        String scope = scope("pending", actor);
        int offset = cursors.decode(cursor, scope, revision);
        List<PendingWatchConfirmation> items = jdbc.query("""
                SELECT w.id, w.clicked_at, w.confirmation_due_at, w.expires_at, w.revision,
                       w.movie_id, p.original_title, p.poster_path, extract(year from p.release_date)::int AS release_year,
                       COALESCE((SELECT l.title FROM movie_localization l
                                  WHERE l.catalog_version_id = p.catalog_version_id AND l.movie_id = p.movie_id
                                  ORDER BY CASE l.locale WHEN 'ko-KR' THEN 0 WHEN 'en-US' THEN 1 ELSE 2 END LIMIT 1),
                                p.original_title) AS display_title,
                       pr.id AS provider_id, pr.display_name AS provider_name
                  FROM watch_intent w
                  JOIN catalog_version cv ON cv.status = 'ACTIVE'
                  JOIN movie_catalog_projection p ON p.catalog_version_id = cv.id AND p.movie_id = w.movie_id
                  JOIN ott_provider pr ON pr.id = w.provider_id
                 WHERE w.user_id = ?
                   AND w.status IN ('LINK_CLICKED', 'CONFIRMATION_PENDING')
                   AND w.confirmation_due_at <= ? AND w.expires_at > ?
                 ORDER BY w.confirmation_due_at DESC, w.movie_id ASC
                 OFFSET ? LIMIT ?
                """, (rs, row) -> new PendingWatchConfirmation(
                rs.getObject("id", UUID.class),
                movie(rs),
                new ProviderSummary(rs.getObject("provider_id", UUID.class), rs.getString("provider_name")),
                rs.getObject("clicked_at", OffsetDateTime.class),
                rs.getObject("confirmation_due_at", OffsetDateTime.class),
                rs.getObject("expires_at", OffsetDateTime.class),
                rs.getInt("revision")
        ), actor, now, now, offset, limit + 1);
        return page(items, total, offset, limit, scope, revision);
    }

    public HttpMutation confirm(
            UUID actor,
            UUID intentId,
            String key,
            ConfirmWatchIntentRequest request,
            String traceId
    ) {
        ObjectNode canonical = objectMapper.createObjectNode()
                .put("watchIntentId", intentId.toString())
                .put("watched", request.watched())
                .put("expectedRevision", request.expectedRevision());
        return execute(actor, "CONFIRM_WATCH_INTENT", key, canonical, () -> {
            ConfirmationIntent intent = findConfirmationIntentForUpdate(actor, intentId);
            if (intent == null) {
                throw notFound();
            }
            Instant current = clock.instant();
            boolean due = !current.isBefore(intent.dueAt().toInstant()) && current.isBefore(intent.expiresAt().toInstant());
            if (!List.of("LINK_CLICKED", "CONFIRMATION_PENDING").contains(intent.status()) || !due) {
                throw conflict("WATCH_INTENT_NOT_CONFIRMABLE", "감상 확인 가능한 상태가 아니에요.");
            }
            if (intent.revision() != request.expectedRevision()) {
                throw conflict("REVISION_CONFLICT", "최신 상태를 다시 확인해 주세요.");
            }
            String newStatus = request.watched() ? "CONFIRMED_WATCHED" : "CONFIRMED_NOT_WATCHED";
            OffsetDateTime respondedAt = now();
            int newRevision = intent.revision() + 1;
            jdbc.update("""
                    UPDATE watch_intent SET status = ?, responded_at = ?, revision = ?
                     WHERE id = ? AND user_id = ? AND revision = ?
                    """, newStatus, respondedAt, newRevision, intentId, actor, intent.revision());
            faults.checkpoint(C1MutationFaultInjector.Checkpoint.AFTER_CONFIRMATION_STATUS_UPDATED);

            ViewingRecordSummary viewing = null;
            if (request.watched()) {
                ExistingViewing existing = findViewing(actor, intent.movieId());
                UUID viewingId;
                int viewingRevision;
                OffsetDateTime watchedAt;
                if (existing == null) {
                    viewingId = UUID.randomUUID();
                    viewingRevision = 1;
                    watchedAt = respondedAt;
                    jdbc.update("""
                            INSERT INTO viewing_record (
                                id, user_id, movie_id, source_watch_intent_id, provider_id,
                                status, watched_confirmed_at, revision
                            ) VALUES (?, ?, ?, ?, ?, 'WATCHED_CONFIRMED', ?, 1)
                            """, viewingId, actor, intent.movieId(), intentId, intent.providerId(), watchedAt);
                } else {
                    viewingId = existing.id();
                    viewingRevision = existing.revision();
                    watchedAt = existing.watchedAt();
                }
                viewing = new ViewingRecordSummary(
                        viewingId, intent.movieId(), "WATCHED_CONFIRMED", watchedAt,
                        provider(intent.providerId()), viewingRevision
                );
            }
            ObjectNode payload = objectMapper.createObjectNode()
                    .put("movieId", intent.movieId().toString())
                    .put("watched", request.watched());
            journal.append(actor, "WATCH_CONFIRMATION_RESPONDED", "WATCH_INTENT", intentId, traceId, payload);
            WatchConfirmationResult body = new WatchConfirmationResult(
                    intentId, newStatus, respondedAt, newRevision, viewing
            );
            return mutation(200, body, intentId);
        });
    }

    public CursorPage<UnratedViewingRecord> unrated(UUID actor, String cursor, int limit) {
        int total = count("SELECT count(*) FROM viewing_record WHERE user_id = ? AND status = 'WATCHED_CONFIRMED'", actor);
        String revision = scalarString("""
                SELECT count(*) || ':' || coalesce(sum(revision), 0)
                  FROM viewing_record WHERE user_id = ? AND status = 'WATCHED_CONFIRMED'
                """, actor);
        String scope = scope("unrated", actor);
        int offset = cursors.decode(cursor, scope, revision);
        List<UnratedViewingRecord> items = jdbc.query("""
                SELECT v.id, v.watched_confirmed_at, v.revision,
                       v.movie_id, p.original_title, p.poster_path, extract(year from p.release_date)::int AS release_year,
                       COALESCE((SELECT l.title FROM movie_localization l
                                  WHERE l.catalog_version_id = p.catalog_version_id AND l.movie_id = p.movie_id
                                  ORDER BY CASE l.locale WHEN 'ko-KR' THEN 0 WHEN 'en-US' THEN 1 ELSE 2 END LIMIT 1),
                                p.original_title) AS display_title,
                       pr.id AS provider_id, pr.display_name AS provider_name
                  FROM viewing_record v
                  JOIN catalog_version cv ON cv.status = 'ACTIVE'
                  JOIN movie_catalog_projection p ON p.catalog_version_id = cv.id AND p.movie_id = v.movie_id
                  JOIN ott_provider pr ON pr.id = v.provider_id
                 WHERE v.user_id = ? AND v.status = 'WATCHED_CONFIRMED'
                 ORDER BY v.watched_confirmed_at DESC, v.movie_id ASC
                 OFFSET ? LIMIT ?
                """, (rs, row) -> new UnratedViewingRecord(
                rs.getObject("id", UUID.class), movie(rs),
                rs.getObject("watched_confirmed_at", OffsetDateTime.class),
                new ProviderSummary(rs.getObject("provider_id", UUID.class), rs.getString("provider_name")),
                rs.getInt("revision")
        ), actor, offset, limit + 1);
        return page(items, total, offset, limit, scope, revision);
    }

    public CursorPage<RatingItem> ratings(UUID actor, String cursor, int limit) {
        int total = count("SELECT count(*) FROM rating WHERE user_id = ? AND logical_status = 'ACTIVE'", actor);
        String revision = scalarString("""
                SELECT count(*) || ':' || coalesce(sum(revision), 0)
                  FROM rating WHERE user_id = ? AND logical_status = 'ACTIVE'
                """, actor);
        String scope = scope("ratings", actor);
        int offset = cursors.decode(cursor, scope, revision);
        List<RatingItem> items = jdbc.query("""
                SELECT r.id AS rating_id, r.movie_id, r.value, r.revision, r.created_at, r.updated_at,
                       f.id AS frame_id, v.watched_confirmed_at,
                       p.original_title, p.poster_path, extract(year from p.release_date)::int AS release_year,
                       COALESCE((SELECT l.title FROM movie_localization l
                                  WHERE l.catalog_version_id = p.catalog_version_id AND l.movie_id = p.movie_id
                                  ORDER BY CASE l.locale WHEN 'ko-KR' THEN 0 WHEN 'en-US' THEN 1 ELSE 2 END LIMIT 1),
                                p.original_title) AS display_title
                  FROM rating r
                  JOIN frame f ON f.rating_id = r.id
                  JOIN viewing_record v ON v.id = r.viewing_record_id
                  JOIN catalog_version cv ON cv.status = 'ACTIVE'
                  JOIN movie_catalog_projection p ON p.catalog_version_id = cv.id AND p.movie_id = r.movie_id
                 WHERE r.user_id = ? AND r.logical_status = 'ACTIVE'
                 ORDER BY r.updated_at DESC, r.movie_id ASC
                 OFFSET ? LIMIT ?
                """, (rs, row) -> new RatingItem(
                rating(rs), movie(rs), rs.getObject("watched_confirmed_at", OffsetDateTime.class),
                rs.getObject("frame_id", UUID.class)
        ), actor, offset, limit + 1);
        return page(items, total, offset, limit, scope, revision);
    }

    public HttpMutation putRating(
            UUID actor,
            UUID movieId,
            String key,
            PutRatingRequest request,
            String traceId
    ) {
        ObjectNode canonical = objectMapper.createObjectNode()
                .put("movieId", movieId.toString())
                .put("value", request.value());
        if (request.expectedRevision() == null) {
            canonical.putNull("expectedRevision");
        } else {
            canonical.put("expectedRevision", request.expectedRevision());
        }
        return execute(actor, "PUT_RATING", key, canonical, () -> mutateRating(actor, movieId, request, traceId));
    }

    public HttpMutation deleteRating(
            UUID actor,
            UUID movieId,
            String key,
            int expectedRevision,
            String traceId
    ) {
        ObjectNode canonical = objectMapper.createObjectNode()
                .put("movieId", movieId.toString())
                .put("expectedRevision", expectedRevision);
        return execute(actor, "DELETE_RATING", key, canonical,
                () -> removeRating(actor, movieId, expectedRevision, traceId));
    }

    public FilmPage film(UUID actor, String cursor, int limit) {
        int total = count("SELECT count(*) FROM frame WHERE user_id = ?", actor);
        int filmRevision = jdbc.queryForObject("""
                SELECT coalesce(sum(r.revision), 0)::int
                  FROM frame f JOIN rating r ON r.id = f.rating_id
                 WHERE f.user_id = ? AND r.logical_status = 'ACTIVE'
                """, Integer.class, actor);
        String revision = total + ":" + filmRevision;
        String scope = scope("film", actor);
        int offset = cursors.decode(cursor, scope, revision);
        List<FrameSummary> items = jdbc.query("""
                SELECT f.id AS frame_id, f.created_at, r.value AS my_rating, v.watched_confirmed_at,
                       f.movie_id, p.original_title, p.poster_path, extract(year from p.release_date)::int AS release_year,
                       COALESCE((SELECT l.title FROM movie_localization l
                                  WHERE l.catalog_version_id = p.catalog_version_id AND l.movie_id = p.movie_id
                                  ORDER BY CASE l.locale WHEN 'ko-KR' THEN 0 WHEN 'en-US' THEN 1 ELSE 2 END LIMIT 1),
                                p.original_title) AS display_title
                  FROM frame f
                  JOIN rating r ON r.id = f.rating_id AND r.logical_status = 'ACTIVE'
                  JOIN viewing_record v ON v.id = f.viewing_record_id
                  JOIN catalog_version cv ON cv.status = 'ACTIVE'
                  JOIN movie_catalog_projection p ON p.catalog_version_id = cv.id AND p.movie_id = f.movie_id
                 WHERE f.user_id = ?
                 ORDER BY f.created_at DESC, f.movie_id ASC
                 OFFSET ? LIMIT ?
                """, (rs, row) -> new FrameSummary(
                rs.getObject("frame_id", UUID.class), movie(rs), rs.getInt("my_rating"),
                rs.getObject("watched_confirmed_at", OffsetDateTime.class),
                rs.getObject("created_at", OffsetDateTime.class)
        ), actor, offset, limit + 1);
        boolean hasNext = items.size() > limit;
        List<FrameSummary> visible = hasNext ? items.subList(0, limit) : items;
        return new FilmPage(total, hasNext, hasNext ? cursors.encode(scope, revision, offset + limit) : null,
                filmRevision, List.copyOf(visible));
    }

    public FrameDetail frame(UUID actor, UUID frameId) {
        List<FrameDetail> values = jdbc.query("""
                SELECT f.id AS frame_id, f.created_at AS frame_created_at, f.derivation_version,
                       r.id AS rating_id, r.movie_id, r.value, r.revision, r.created_at, r.updated_at,
                       v.watched_confirmed_at,
                       p.original_title, p.poster_path, extract(year from p.release_date)::int AS release_year,
                       COALESCE((SELECT l.title FROM movie_localization l
                                  WHERE l.catalog_version_id = p.catalog_version_id AND l.movie_id = p.movie_id
                                  ORDER BY CASE l.locale WHEN 'ko-KR' THEN 0 WHEN 'en-US' THEN 1 ELSE 2 END LIMIT 1),
                                p.original_title) AS display_title,
                       pr.id AS provider_id, pr.display_name AS provider_name
                  FROM frame f
                  JOIN rating r ON r.id = f.rating_id AND r.logical_status = 'ACTIVE'
                  JOIN viewing_record v ON v.id = f.viewing_record_id
                  JOIN ott_provider pr ON pr.id = v.provider_id
                  JOIN catalog_version cv ON cv.status = 'ACTIVE'
                  JOIN movie_catalog_projection p ON p.catalog_version_id = cv.id AND p.movie_id = f.movie_id
                 WHERE f.id = ? AND f.user_id = ?
                """, (rs, row) -> new FrameDetail(
                rs.getObject("frame_id", UUID.class), movie(rs), rating(rs),
                rs.getObject("watched_confirmed_at", OffsetDateTime.class),
                new ProviderSummary(rs.getObject("provider_id", UUID.class), rs.getString("provider_name")),
                rs.getObject("frame_created_at", OffsetDateTime.class), rs.getString("derivation_version")
        ), frameId, actor);
        if (values.isEmpty()) {
            throw notFound();
        }
        return values.get(0);
    }

    public PopcornBucket popcornBucket(UUID actor) {
        String mappingVersion = scalarString("SELECT mapping_version FROM flavor_mapping_version WHERE status = 'ACTIVE'", new Object[]{});
        List<FlavorAggregate> flavors = jdbc.query("""
                SELECT f.id, f.flavor_code, f.display_name, f.color_token,
                       coalesce(a.popcorn_count, 0) AS popcorn_count,
                       coalesce(a.rating_count, 0) AS rating_count,
                       a.rating_sum
                  FROM popcorn_flavor f
                  LEFT JOIN flavor_aggregate a ON a.flavor_id = f.id AND a.user_id = ?
                 WHERE f.active = true
                 ORDER BY CASE f.flavor_code
                    WHEN 'ADRENALINE' THEN 1 WHEN 'WONDER' THEN 2 WHEN 'JOY' THEN 3 WHEN 'HEART' THEN 4
                    WHEN 'SHADOW' THEN 5 WHEN 'REAL' THEN 6 WHEN 'LEGACY' THEN 7 ELSE 8 END
                """, (rs, row) -> {
            int ratingCount = rs.getInt("rating_count");
            BigDecimal average = ratingCount == 0 ? null
                    : BigDecimal.valueOf(rs.getInt("rating_sum"))
                    .divide(BigDecimal.valueOf(ratingCount), 2, RoundingMode.HALF_UP).stripTrailingZeros();
            return new FlavorAggregate(
                    rs.getObject("id", UUID.class), rs.getString("flavor_code"), rs.getString("display_name"),
                    rs.getString("color_token"), rs.getInt("popcorn_count"), ratingCount, average
            );
        }, actor);
        int total = flavors.stream().mapToInt(FlavorAggregate::count).sum();
        long revision = maxAggregateRevision(actor);
        return new PopcornBucket(total, mappingVersion, revision, flavors);
    }

    public TasteProfile tasteProfile(UUID actor) {
        List<TasteAggregate> items = jdbc.query("""
                SELECT dimension_type, dimension_key, rating_count, rating_sum
                  FROM taste_aggregate
                 WHERE user_id = ? AND rating_count > 0
                 ORDER BY dimension_type, dimension_key
                """, (rs, row) -> {
            String type = rs.getString("dimension_type");
            String storedKey = rs.getString("dimension_key");
            DimensionDisplay display = dimensionDisplay(type, storedKey);
            int count = rs.getInt("rating_count");
            BigDecimal average = BigDecimal.valueOf(rs.getInt("rating_sum"))
                    .divide(BigDecimal.valueOf(count), 2, RoundingMode.HALF_UP).stripTrailingZeros();
            return new TasteAggregate(type, display.publicKey(), display.displayName(), count, average);
        }, actor);
        return new TasteProfile(DERIVATION_VERSION, maxAggregateRevision(actor), items);
    }

    @Transactional
    public int advanceWatchIntents() {
        OffsetDateTime current = now();
        int expired = jdbc.update("""
                UPDATE watch_intent
                   SET status = 'EXPIRED', responded_at = ?, revision = revision + 1
                 WHERE status IN ('LINK_CLICKED', 'CONFIRMATION_PENDING') AND expires_at <= ?
                """, current, current);
        int due = jdbc.update("""
                UPDATE watch_intent
                   SET status = 'CONFIRMATION_PENDING', revision = revision + 1
                 WHERE status = 'LINK_CLICKED' AND confirmation_due_at <= ? AND expires_at > ?
                """, current, current);
        return expired + due;
    }

    private C1IdempotencyService.MutationResponse mutateRating(
            UUID actor,
            UUID movieId,
            PutRatingRequest request,
            String traceId
    ) {
        lockDomain(actor, "RATING", movieId);
        ViewingForRating viewing = findViewingForRating(actor, movieId);
        if (viewing == null) {
            throw conflict("WATCH_CONFIRMATION_REQUIRED", "감상 확인이 먼저 필요해요.");
        }
        Eligibility eligibility = findEligibility(movieId);
        if (eligibility == null) {
            throw conflict("FLAVOR_ASSIGNMENT_REQUIRED", "현재 이 영화는 평가할 수 없어요.");
        }
        RatingRow active = findActiveRatingForUpdate(actor, movieId);
        RatingRow deleted = active == null ? findLatestDeletedRatingForUpdate(actor, movieId) : null;
        OffsetDateTime current = now();
        String mutationType;
        String eventType;
        UUID ratingId;
        int revision;
        OffsetDateTime createdAt;
        UUID frameId;
        UUID popcornId;

        if (active == null) {
            if (request.expectedRevision() != null) {
                throw conflict("REVISION_CONFLICT", "최신 상태를 다시 확인해 주세요.");
            }
            mutationType = "CREATED";
            eventType = "RATING_CREATED";
            if (deleted == null) {
                ratingId = UUID.randomUUID();
                revision = 1;
                createdAt = current;
                jdbc.update("""
                        INSERT INTO rating (
                            id, user_id, movie_id, viewing_record_id, value, logical_status, revision,
                            created_at, updated_at, deleted_at, deletion_trace_id
                        ) VALUES (?, ?, ?, ?, ?, 'ACTIVE', 1, ?, ?, NULL, NULL)
                        """, ratingId, actor, movieId, viewing.id(), request.value(), current, current);
            } else {
                ratingId = deleted.id();
                revision = deleted.revision() + 1;
                createdAt = deleted.createdAt();
                jdbc.update("""
                        UPDATE rating SET viewing_record_id = ?, value = ?, logical_status = 'ACTIVE', revision = ?,
                                          updated_at = ?, deleted_at = NULL, deletion_trace_id = NULL
                         WHERE id = ? AND user_id = ? AND logical_status = 'DELETED'
                        """, viewing.id(), request.value(), revision, current, ratingId, actor);
            }
            faults.checkpoint(C1MutationFaultInjector.Checkpoint.AFTER_RATING_WRITTEN);
            jdbc.update("UPDATE viewing_record SET status = 'RATED_COMPLETED', revision = revision + 1 WHERE id = ?", viewing.id());
            frameId = UUID.randomUUID();
            popcornId = UUID.randomUUID();
            jdbc.update("""
                    INSERT INTO frame (
                        id, user_id, movie_id, viewing_record_id, rating_id, derivation_version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, frameId, actor, movieId, viewing.id(), ratingId, DERIVATION_VERSION, current, current);
            jdbc.update("""
                    INSERT INTO popcorn (
                        id, user_id, frame_id, rating_id, flavor_id, flavor_mapping_version, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, popcornId, actor, frameId, ratingId, eligibility.flavorId(), eligibility.mappingVersion(), current);
            faults.checkpoint(C1MutationFaultInjector.Checkpoint.AFTER_POPCORN_WRITTEN);
            applyFlavorDelta(actor, eligibility.flavorId(), 1, request.value(), current);
        } else {
            if (request.expectedRevision() == null || active.revision() != request.expectedRevision()) {
                throw conflict("REVISION_CONFLICT", "최신 상태를 다시 확인해 주세요.");
            }
            mutationType = "UPDATED";
            eventType = "RATING_UPDATED";
            ratingId = active.id();
            revision = active.revision() + 1;
            createdAt = active.createdAt();
            Projection projection = projectionForUpdate(ratingId);
            if (projection == null) {
                throw new IllegalStateException("active rating projection is missing");
            }
            frameId = projection.frameId();
            popcornId = projection.popcornId();
            applyFlavorDelta(actor, projection.flavorId(), -1, -active.value(), current);
            removeTasteContribution(actor, ratingId, current);
            jdbc.update("""
                    UPDATE rating SET value = ?, revision = ?, updated_at = ?
                     WHERE id = ? AND user_id = ? AND logical_status = 'ACTIVE'
                    """, request.value(), revision, current, ratingId, actor);
            faults.checkpoint(C1MutationFaultInjector.Checkpoint.AFTER_RATING_WRITTEN);
            jdbc.update("UPDATE frame SET updated_at = ? WHERE id = ?", current, frameId);
            jdbc.update("""
                    UPDATE popcorn SET flavor_id = ?, flavor_mapping_version = ? WHERE id = ?
                    """, eligibility.flavorId(), eligibility.mappingVersion(), popcornId);
            faults.checkpoint(C1MutationFaultInjector.Checkpoint.AFTER_POPCORN_WRITTEN);
            applyFlavorDelta(actor, eligibility.flavorId(), 1, request.value(), current);
        }
        addTasteContribution(actor, ratingId, movieId, request.value(), eligibility, current);
        ObjectNode payload = objectMapper.createObjectNode()
                .put("movieId", movieId.toString())
                .put("ratingRevision", revision);
        journal.append(actor, eventType, "RATING", ratingId, traceId, payload);
        ratingCompletion.completeRatedRecommendationItems(
                actor, movieId, ratingId, revision, current
        );
        Rating responseRating = new Rating(ratingId, movieId, request.value(), revision, createdAt, current);
        DerivedState derived = new DerivedState(
                "RATED_COMPLETED", frameId, popcornId, filmCount(actor),
                Math.max(1, maxAggregateRevision(actor)), RECOMMENDATION_QUEUED
        );
        return mutation(200, new RatingMutationResult(mutationType, responseRating, derived), ratingId);
    }

    private C1IdempotencyService.MutationResponse removeRating(
            UUID actor,
            UUID movieId,
            int expectedRevision,
            String traceId
    ) {
        lockDomain(actor, "RATING", movieId);
        RatingRow rating = findActiveRatingForUpdate(actor, movieId);
        if (rating == null) {
            throw notFound();
        }
        if (rating.revision() != expectedRevision) {
            throw conflict("REVISION_CONFLICT", "최신 상태를 다시 확인해 주세요.");
        }
        Projection projection = projectionForUpdate(rating.id());
        if (projection == null) {
            throw new IllegalStateException("active rating projection is missing");
        }
        OffsetDateTime current = now();
        applyFlavorDelta(actor, projection.flavorId(), -1, -rating.value(), current);
        removeTasteContribution(actor, rating.id(), current);
        faults.checkpoint(C1MutationFaultInjector.Checkpoint.AFTER_DELETE_AGGREGATES_REVERSED);
        jdbc.update("DELETE FROM popcorn WHERE id = ?", projection.popcornId());
        jdbc.update("DELETE FROM frame WHERE id = ?", projection.frameId());
        int revision = rating.revision() + 1;
        jdbc.update("""
                UPDATE rating SET logical_status = 'DELETED', revision = ?, updated_at = ?, deleted_at = ?, deletion_trace_id = ?
                 WHERE id = ? AND user_id = ? AND logical_status = 'ACTIVE'
                """, revision, current, current, traceId, rating.id(), actor);
        jdbc.update("""
                UPDATE viewing_record SET status = 'WATCHED_CONFIRMED', revision = revision + 1
                 WHERE id = ? AND user_id = ?
                """, rating.viewingId(), actor);
        ObjectNode payload = objectMapper.createObjectNode()
                .put("movieId", movieId.toString())
                .put("ratingRevision", revision);
        journal.append(actor, "RATING_DELETED", "RATING", rating.id(), traceId, payload);
        RatingDeletionResult body = new RatingDeletionResult(
                movieId, true, "WATCHED_CONFIRMED", false, false, filmCount(actor),
                Math.max(1, maxAggregateRevision(actor)), RECOMMENDATION_QUEUED
        );
        return mutation(200, body, rating.id());
    }

    private void addTasteContribution(
            UUID actor,
            UUID ratingId,
            UUID movieId,
            int value,
            Eligibility eligibility,
            OffsetDateTime current
    ) {
        List<Dimension> dimensions = new ArrayList<>();
        dimensions.addAll(jdbc.query("""
                SELECT 'GENRE' AS dimension_type, g.id::text AS dimension_key
                  FROM movie_genre mg JOIN genre g ON g.id = mg.genre_id
                 WHERE mg.catalog_version_id = ? AND mg.movie_id = ? AND mg.display_order = 0
                """, (rs, row) -> new Dimension(rs.getString(1), rs.getString(2)),
                eligibility.catalogVersionId(), movieId));
        dimensions.addAll(jdbc.query("""
                SELECT 'COUNTRY' AS dimension_type, country_code::text AS dimension_key
                  FROM movie_country WHERE catalog_version_id = ? AND movie_id = ?
                """, (rs, row) -> new Dimension(rs.getString(1), rs.getString(2)),
                eligibility.catalogVersionId(), movieId));
        dimensions.addAll(jdbc.query("""
                SELECT 'DIRECTOR' AS dimension_type, person_id::text AS dimension_key
                  FROM movie_credit
                 WHERE catalog_version_id = ? AND movie_id = ? AND credit_type = 'DIRECTOR'
                """, (rs, row) -> new Dimension(rs.getString(1), rs.getString(2)),
                eligibility.catalogVersionId(), movieId));
        for (Dimension dimension : dimensions) {
            jdbc.update("""
                    INSERT INTO rating_taste_contribution (
                        rating_id, dimension_type, dimension_key, rating_value,
                        catalog_version_id, flavor_mapping_version, derivation_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, ratingId, dimension.type(), dimension.key(), value,
                    eligibility.catalogVersionId(), eligibility.mappingVersion(), DERIVATION_VERSION);
            jdbc.update("""
                    INSERT INTO taste_aggregate (
                        user_id, dimension_type, dimension_key, rating_count, rating_sum, revision, updated_at
                    ) VALUES (?, ?, ?, 1, ?, 1, ?)
                    ON CONFLICT (user_id, dimension_type, dimension_key) DO UPDATE
                       SET rating_count = taste_aggregate.rating_count + 1,
                           rating_sum = taste_aggregate.rating_sum + EXCLUDED.rating_sum,
                           revision = taste_aggregate.revision + 1,
                           updated_at = EXCLUDED.updated_at
                    """, actor, dimension.type(), dimension.key(), value, current);
        }
    }

    private void removeTasteContribution(UUID actor, UUID ratingId, OffsetDateTime current) {
        List<TasteContribution> contributions = jdbc.query("""
                SELECT dimension_type, dimension_key, rating_value
                  FROM rating_taste_contribution WHERE rating_id = ? FOR UPDATE
                """, (rs, row) -> new TasteContribution(rs.getString(1), rs.getString(2), rs.getInt(3)), ratingId);
        for (TasteContribution contribution : contributions) {
            jdbc.update("""
                    UPDATE taste_aggregate
                       SET rating_count = rating_count - 1,
                           rating_sum = rating_sum - ?,
                           revision = revision + 1,
                           updated_at = ?
                     WHERE user_id = ? AND dimension_type = ? AND dimension_key = ?
                    """, contribution.value(), current, actor, contribution.type(), contribution.key());
        }
        jdbc.update("DELETE FROM rating_taste_contribution WHERE rating_id = ?", ratingId);
    }

    private void applyFlavorDelta(UUID actor, UUID flavorId, int countDelta, int sumDelta, OffsetDateTime current) {
        if (countDelta > 0) {
            jdbc.update("""
                    INSERT INTO flavor_aggregate (
                        user_id, flavor_id, popcorn_count, rating_count, rating_sum, revision, updated_at
                    ) VALUES (?, ?, 1, 1, ?, 1, ?)
                    ON CONFLICT (user_id, flavor_id) DO UPDATE
                       SET popcorn_count = flavor_aggregate.popcorn_count + 1,
                           rating_count = flavor_aggregate.rating_count + 1,
                           rating_sum = flavor_aggregate.rating_sum + EXCLUDED.rating_sum,
                           revision = flavor_aggregate.revision + 1,
                           updated_at = EXCLUDED.updated_at
                    """, actor, flavorId, sumDelta, current);
        } else {
            int changed = jdbc.update("""
                    UPDATE flavor_aggregate
                       SET popcorn_count = popcorn_count - 1,
                           rating_count = rating_count - 1,
                           rating_sum = rating_sum + ?,
                           revision = revision + 1,
                           updated_at = ?
                     WHERE user_id = ? AND flavor_id = ? AND popcorn_count > 0
                    """, sumDelta, current, actor, flavorId);
            if (changed != 1) {
                throw new IllegalStateException("flavor aggregate delta invariant failed");
            }
        }
    }

    private Offer findCurrentOffer(UUID movieId, UUID offerId) {
        List<Offer> values = jdbc.query("""
                SELECT o.id, o.provider_id, o.link_type,
                       coalesce(o.landing_url, s.aggregator_url) AS destination
                  FROM movie_ott_offer o
                  JOIN movie_availability_snapshot s ON s.id = o.snapshot_id
                  JOIN catalog_version cv ON cv.id = s.catalog_version_id AND cv.status = 'ACTIVE'
                  JOIN movie_catalog_projection p ON p.catalog_version_id = cv.id AND p.movie_id = s.movie_id
                 WHERE o.id = ? AND s.movie_id = ?
                   AND p.identity_status = 'IDENTITY_VERIFIED'
                   AND p.visibility_status IN ('UI_READY', 'CATALOG_VISIBLE') AND p.deleted = false
                   AND s.region = 'KR' AND s.fetch_status = 'SUCCESS_LISTED' AND s.serve_until > ?
                   AND coalesce(o.landing_url, s.aggregator_url) IS NOT NULL
                   AND s.id = (SELECT s2.id FROM movie_availability_snapshot s2
                                WHERE s2.catalog_version_id = cv.id AND s2.movie_id = s.movie_id
                                  AND s2.region = 'KR' AND s2.fetch_status IN ('SUCCESS_LISTED', 'SUCCESS_EMPTY')
                                ORDER BY s2.fetched_at DESC, s2.id LIMIT 1)
                """, (rs, row) -> new Offer(
                rs.getObject("id", UUID.class), rs.getObject("provider_id", UUID.class),
                rs.getString("link_type"), rs.getString("destination")
        ), offerId, movieId, now());
        return values.isEmpty() ? null : values.get(0);
    }

    private ExistingIntent findActiveIntentForUpdate(UUID actor, UUID movieId) {
        List<ExistingIntent> values = jdbc.query("""
                SELECT id, status, clicked_at, confirmation_due_at, expires_at, revision
                  FROM watch_intent
                 WHERE user_id = ? AND movie_id = ? AND status IN ('LINK_CLICKED', 'CONFIRMATION_PENDING')
                 FOR UPDATE
                """, (rs, row) -> new ExistingIntent(
                rs.getObject("id", UUID.class), rs.getString("status"),
                rs.getObject("clicked_at", OffsetDateTime.class),
                rs.getObject("confirmation_due_at", OffsetDateTime.class),
                rs.getObject("expires_at", OffsetDateTime.class), rs.getInt("revision")
        ), actor, movieId);
        return values.isEmpty() ? null : values.get(0);
    }

    private ConfirmationIntent findConfirmationIntentForUpdate(UUID actor, UUID id) {
        List<ConfirmationIntent> values = jdbc.query("""
                SELECT id, movie_id, provider_id, status, confirmation_due_at, expires_at, revision
                  FROM watch_intent WHERE id = ? AND user_id = ? FOR UPDATE
                """, (rs, row) -> new ConfirmationIntent(
                rs.getObject("id", UUID.class), rs.getObject("movie_id", UUID.class),
                rs.getObject("provider_id", UUID.class), rs.getString("status"),
                rs.getObject("confirmation_due_at", OffsetDateTime.class),
                rs.getObject("expires_at", OffsetDateTime.class), rs.getInt("revision")
        ), id, actor);
        return values.isEmpty() ? null : values.get(0);
    }

    private ExistingViewing findViewing(UUID actor, UUID movieId) {
        List<ExistingViewing> values = jdbc.query("""
                SELECT id, source_watch_intent_id, watched_confirmed_at, revision
                  FROM viewing_record WHERE user_id = ? AND movie_id = ?
                """, (rs, row) -> new ExistingViewing(
                rs.getObject("id", UUID.class), rs.getObject("source_watch_intent_id", UUID.class),
                rs.getObject("watched_confirmed_at", OffsetDateTime.class), rs.getInt("revision")
        ), actor, movieId);
        return values.isEmpty() ? null : values.get(0);
    }

    private ViewingForRating findViewingForRating(UUID actor, UUID movieId) {
        List<ViewingForRating> values = jdbc.query("""
                SELECT id, provider_id FROM viewing_record WHERE user_id = ? AND movie_id = ? FOR UPDATE
                """, (rs, row) -> new ViewingForRating(
                rs.getObject("id", UUID.class), rs.getObject("provider_id", UUID.class)
        ), actor, movieId);
        return values.isEmpty() ? null : values.get(0);
    }

    private Eligibility findEligibility(UUID movieId) {
        List<Eligibility> values = jdbc.query("""
                SELECT movie_id, catalog_version_id, mapping_version, flavor_id
                  FROM c1_rating_eligible_movie WHERE movie_id = ?
                """, (rs, row) -> new Eligibility(
                rs.getObject("catalog_version_id", UUID.class), rs.getString("mapping_version"),
                rs.getObject("flavor_id", UUID.class)
        ), movieId);
        return values.isEmpty() ? null : values.get(0);
    }

    private RatingRow findActiveRatingForUpdate(UUID actor, UUID movieId) {
        return findRating(actor, movieId, "ACTIVE");
    }

    private RatingRow findLatestDeletedRatingForUpdate(UUID actor, UUID movieId) {
        return findRating(actor, movieId, "DELETED");
    }

    private RatingRow findRating(UUID actor, UUID movieId, String status) {
        List<RatingRow> values = jdbc.query("""
                SELECT id, viewing_record_id, value, revision, created_at, updated_at
                  FROM rating
                 WHERE user_id = ? AND movie_id = ? AND logical_status = ?
                 ORDER BY updated_at DESC LIMIT 1 FOR UPDATE
                """, (rs, row) -> new RatingRow(
                rs.getObject("id", UUID.class), rs.getObject("viewing_record_id", UUID.class),
                rs.getInt("value"), rs.getInt("revision"),
                rs.getObject("created_at", OffsetDateTime.class), rs.getObject("updated_at", OffsetDateTime.class)
        ), actor, movieId, status);
        return values.isEmpty() ? null : values.get(0);
    }

    private Projection projectionForUpdate(UUID ratingId) {
        List<Projection> values = jdbc.query("""
                SELECT f.id AS frame_id, p.id AS popcorn_id, p.flavor_id
                  FROM frame f JOIN popcorn p ON p.frame_id = f.id
                 WHERE f.rating_id = ? FOR UPDATE OF f, p
                """, (rs, row) -> new Projection(
                rs.getObject("frame_id", UUID.class), rs.getObject("popcorn_id", UUID.class),
                rs.getObject("flavor_id", UUID.class)
        ), ratingId);
        return values.isEmpty() ? null : values.get(0);
    }

    private ProviderSummary provider(UUID providerId) {
        return jdbc.queryForObject("SELECT id, display_name FROM ott_provider WHERE id = ?",
                (rs, row) -> new ProviderSummary(rs.getObject("id", UUID.class), rs.getString("display_name")),
                providerId);
    }

    private DimensionDisplay dimensionDisplay(String type, String key) {
        if ("COUNTRY".equals(type)) {
            List<DimensionDisplay> values = jdbc.query(
                    "SELECT code::text, display_name_ko FROM country WHERE code = ?",
                    (rs, row) -> new DimensionDisplay(rs.getString(1), rs.getString(2)), key);
            return values.isEmpty() ? new DimensionDisplay(key, key) : values.get(0);
        }
        if ("DIRECTOR".equals(type)) {
            List<DimensionDisplay> values = jdbc.query(
                    "SELECT id::text, display_name FROM person WHERE id::text = ?",
                    (rs, row) -> new DimensionDisplay(rs.getString(1), rs.getString(2)), key);
            return values.isEmpty() ? new DimensionDisplay(key, "감독") : values.get(0);
        }
        List<DimensionDisplay> byId = jdbc.query(
                "SELECT id::text, display_name_ko FROM genre WHERE id::text = ?",
                (rs, row) -> new DimensionDisplay(rs.getString(1), rs.getString(2)), key);
        if (!byId.isEmpty()) {
            return byId.get(0);
        }
        return new DimensionDisplay(
                UUID.nameUUIDFromBytes(("genre:" + key).getBytes(StandardCharsets.UTF_8)).toString(),
                "장르"
        );
    }

    private int filmCount(UUID actor) {
        return count("SELECT count(*) FROM frame WHERE user_id = ?", actor);
    }

    private void lockDomain(UUID actor, String resourceType, UUID resourceId) {
        jdbc.query(
                "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                (resultSet, rowNumber) -> 0,
                actor + ":" + resourceType + ":" + resourceId
        );
    }

    private void expireActiveIntent(UUID actor, UUID movieId) {
        OffsetDateTime current = now();
        jdbc.update("""
                UPDATE watch_intent
                   SET status = 'EXPIRED', responded_at = ?, revision = revision + 1
                 WHERE user_id = ? AND movie_id = ?
                   AND status IN ('LINK_CLICKED', 'CONFIRMATION_PENDING') AND expires_at <= ?
                """, current, actor, movieId, current);
    }

    private long maxAggregateRevision(UUID actor) {
        Long result = jdbc.queryForObject("""
                SELECT greatest(
                    coalesce((SELECT max(revision) FROM flavor_aggregate WHERE user_id = ?), 0),
                    coalesce((SELECT max(revision) FROM taste_aggregate WHERE user_id = ?), 0)
                )
                """, Long.class, actor, actor);
        return result == null ? 0 : result;
    }

    private <T> CursorPage<T> page(
            List<T> queried,
            int total,
            int offset,
            int limit,
            String scope,
            String revision
    ) {
        boolean hasNext = queried.size() > limit;
        List<T> items = hasNext ? queried.subList(0, limit) : queried;
        return new CursorPage<>(total, hasNext, hasNext ? cursors.encode(scope, revision, offset + limit) : null,
                List.copyOf(items));
    }

    private HttpMutation execute(
            UUID actor,
            String operation,
            String key,
            JsonNode canonical,
            java.util.function.Supplier<C1IdempotencyService.MutationResponse> mutation
    ) {
        try {
            C1IdempotencyService.ExecutionResult result = idempotency.execute(actor, operation, key, canonical, mutation);
            return new HttpMutation(result.response().status(), result.response().body());
        } catch (C1FoundationException exception) {
            if ("IDEMPOTENCY_KEY_REUSED".equals(exception.code())) {
                throw conflict("IDEMPOTENCY_KEY_REUSED", "Idempotency-Key를 다른 요청에 재사용할 수 없어요.");
            }
            if (exception.code().startsWith("INVALID_IDEMPOTENCY")) {
                throw validation("Idempotency-Key", "invalid_format");
            }
            throw exception;
        }
    }

    private C1IdempotencyService.MutationResponse mutation(int status, Object body, UUID resourceId) {
        return new C1IdempotencyService.MutationResponse(status, objectMapper.valueToTree(body), resourceId);
    }

    private int count(String sql, Object... args) {
        Integer result = jdbc.queryForObject(sql, Integer.class, args);
        return result == null ? 0 : result;
    }

    private String scalarString(String sql, Object... args) {
        String value = jdbc.queryForObject(sql, String.class, args);
        return value == null ? "0" : value;
    }

    private String scope(String operation, UUID actor) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest((operation + ":" + actor).getBytes(StandardCharsets.UTF_8));
            return operation + ":" + HexFormat.of().formatHex(digest, 0, 12);
        } catch (Exception exception) {
            throw new IllegalStateException("cursor scope failed", exception);
        }
    }

    private OffsetDateTime now() {
        return utc(clock.instant());
    }

    private static OffsetDateTime utc(Instant instant) {
        return OffsetDateTime.ofInstant(instant, ZoneOffset.UTC);
    }

    private static MovieSummary movie(java.sql.ResultSet rs) throws java.sql.SQLException {
        String posterPath = rs.getString("poster_path");
        Integer releaseYear = (Integer) rs.getObject("release_year");
        return new MovieSummary(
                rs.getObject("movie_id", UUID.class), rs.getString("display_title"),
                posterPath == null ? null : "https://image.tmdb.org/t/p/w500" + posterPath,
                releaseYear
        );
    }

    private static Rating rating(java.sql.ResultSet rs) throws java.sql.SQLException {
        return new Rating(
                rs.getObject("rating_id", UUID.class), rs.getObject("movie_id", UUID.class),
                rs.getInt("value"), rs.getInt("revision"),
                rs.getObject("created_at", OffsetDateTime.class), rs.getObject("updated_at", OffsetDateTime.class)
        );
    }

    private static ApiException notFound() {
        return new ApiException(HttpStatus.NOT_FOUND, "RESOURCE_NOT_FOUND", "리소스를 찾을 수 없어요.");
    }

    private static ApiException conflict(String code, String message) {
        return new ApiException(HttpStatus.CONFLICT, code, message);
    }

    private static ApiException validation(String field, String reason) {
        return new ApiException(
                HttpStatus.BAD_REQUEST, "VALIDATION_ERROR", "요청 값을 확인해 주세요.",
                List.of(new com.feelm.catalog.api.CatalogApiDtos.FieldError(field, reason))
        );
    }

    public record HttpMutation(int status, JsonNode body) {
    }

    private record Offer(UUID offerId, UUID providerId, String linkType, String destination) {
    }

    private record ExistingIntent(
            UUID id, String status, OffsetDateTime clickedAt, OffsetDateTime dueAt, OffsetDateTime expiresAt, int revision
    ) {
    }

    private record ConfirmationIntent(
            UUID id, UUID movieId, UUID providerId, String status, OffsetDateTime dueAt, OffsetDateTime expiresAt,
            int revision
    ) {
    }

    private record ExistingViewing(
            UUID id, UUID sourceWatchIntentId, OffsetDateTime watchedAt, int revision
    ) {
    }

    private record ViewingForRating(UUID id, UUID providerId) {
    }

    private record Eligibility(UUID catalogVersionId, String mappingVersion, UUID flavorId) {
    }

    private record RatingRow(
            UUID id, UUID viewingId, int value, int revision, OffsetDateTime createdAt, OffsetDateTime updatedAt
    ) {
    }

    private record Projection(UUID frameId, UUID popcornId, UUID flavorId) {
    }

    private record Dimension(String type, String key) {
    }

    private record TasteContribution(String type, String key, int value) {
    }

    private record DimensionDisplay(String publicKey, String displayName) {
    }
}
