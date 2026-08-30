package com.feelm.catalog.c5.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.feelm.catalog.api.ApiException;
import com.feelm.catalog.api.CatalogApiDtos;
import com.feelm.catalog.c5.api.C5ApiDtos.*;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Profile;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.annotation.Isolation;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.sql.Date;
import java.sql.Timestamp;
import java.time.Clock;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneId;
import java.time.ZonedDateTime;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.Base64;
import java.util.Comparator;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.UUID;

@Service
@Profile("local")
@ConditionalOnProperty(name = "c5.local.enabled", havingValue = "true")
public class C5LocalService {
    private static final ZoneId KST = ZoneId.of("Asia/Seoul");
    private static final Set<String> PRIVACY_RESOURCES = Set.of("PROFILE", "FILM", "POPCORN");
    private static final Set<String> VISIBILITIES = Set.of("PRIVATE", "PUBLIC");
    private static final String TITLE_SQL = """
            COALESCE((SELECT ml.title
                        FROM catalog_version cv
                        JOIN movie_catalog_projection mcp ON mcp.catalog_version_id=cv.id
                        JOIN movie_localization ml ON ml.catalog_version_id=mcp.catalog_version_id AND ml.movie_id=mcp.movie_id
                       WHERE cv.status='ACTIVE' AND mcp.movie_id=v.movie_id AND mcp.deleted=false
                       ORDER BY CASE ml.locale WHEN 'ko-KR' THEN 0 WHEN 'en-US' THEN 1 ELSE 2 END, ml.locale
                       LIMIT 1), '영화')
            """;

    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;
    private final Clock clock;
    private final C5CursorCodec cursors;
    private final C5LocalPdfStore pdfStore;
    private final SecureRandom secureRandom = new SecureRandom();

    public C5LocalService(
            JdbcTemplate jdbc,
            ObjectMapper objectMapper,
            Clock clock,
            C5CursorCodec cursors,
            C5LocalPdfStore pdfStore
    ) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
        this.clock = clock;
        this.cursors = cursors;
        this.pdfStore = pdfStore;
    }

    public UUID requireActiveActor(UUID actor) {
        Integer active = jdbc.query("SELECT 1 FROM c4_user_account WHERE user_id=? AND membership_status='ACTIVE'",
                rs -> rs.next() ? 1 : null, actor);
        if (active == null) throw ApiException.unauthorized();
        return actor;
    }

    @Transactional
    public TasteReportSummaryPage listReports(UUID actor, String cursor, int limit) {
        requireActiveActor(actor);
        C5CursorCodec.Decoded decoded = cursors.decode(cursor, "report-list", actor.toString(), 3);
        List<ReportRow> rows;
        if (decoded == null) {
            rows = jdbc.query("""
                    SELECT report_id,period_start,period_end,revision,status,source_watermark,
                           viewing_count,rated_count,rating_sum,created_at
                      FROM c5_taste_report_revision
                     WHERE owner_user_id=?
                     ORDER BY period_start DESC,revision DESC,report_id DESC LIMIT ?
                    """, this::reportRow, actor, limit + 1);
        } else {
            rows = jdbc.query("""
                    SELECT report_id,period_start,period_end,revision,status,source_watermark,
                           viewing_count,rated_count,rating_sum,created_at
                      FROM c5_taste_report_revision
                     WHERE owner_user_id=? AND (period_start,revision,report_id) < (?,?,?)
                     ORDER BY period_start DESC,revision DESC,report_id DESC LIMIT ?
                    """, this::reportRow, actor, Date.valueOf(decoded.lastKey().get(0)),
                    Integer.parseInt(decoded.lastKey().get(1)), UUID.fromString(decoded.lastKey().get(2)), limit + 1);
        }
        int total = jdbc.queryForObject("SELECT count(*) FROM c5_taste_report_revision WHERE owner_user_id=?",
                Integer.class, actor);
        boolean more = rows.size() > limit;
        List<ReportRow> page = rows.subList(0, Math.min(limit, rows.size()));
        String next = more ? cursors.encode("report-list", actor.toString(), List.of(
                page.get(page.size() - 1).periodStart().toString(),
                Integer.toString(page.get(page.size() - 1).revision()),
                page.get(page.size() - 1).reportId().toString())) : null;
        return new TasteReportSummaryPage(total, more, next, page.stream().map(this::summary).toList());
    }

    @Transactional(isolation = Isolation.REPEATABLE_READ)
    public TasteReport createReport(UUID actor, String idempotencyKey, CreateTasteReportRequest request) {
        requireActiveActor(actor);
        Period period = period(request.periodStart());
        String canonical = period.start().toString();
        TasteReport replay = replay(actor, "createMyTasteReportRevision", idempotencyKey, canonical, TasteReport.class);
        if (replay != null) return replay;
        advisoryLock("c5-report:" + actor + ":" + period.start());

        Instant now = Instant.now(clock);
        Instant eligibleAt = period.end().plusDays(1).atStartOfDay(KST).toInstant().plus(72, ChronoUnit.HOURS);
        if (now.isBefore(eligibleAt)) {
            throw validation("periodStart", "period_not_complete_plus_72h");
        }
        // PostgreSQL owns the snapshot watermark. transaction_timestamp() is fixed at this
        // REPEATABLE READ transaction boundary, so rows committed after the snapshot cannot be
        // implied to be part of the report even when the local fixture Clock differs from DB time.
        Instant sourceCutoff = sourceTransactionWatermark();
        List<SourceItem> source = sourceItems(actor, period, sourceCutoff);
        int revision = jdbc.queryForObject("""
                SELECT coalesce(max(revision),0)+1 FROM c5_taste_report_revision
                 WHERE owner_user_id=? AND period_start=?
                """, Integer.class, actor, Date.valueOf(period.start()));
        jdbc.update("""
                UPDATE c5_taste_report_revision SET status='SUPERSEDED',superseded_at=?
                 WHERE owner_user_id=? AND period_start=? AND status IN ('READY','EMPTY_NO_ACTIVITY')
                """, ts(now), actor, Date.valueOf(period.start()));
        UUID reportId = UUID.randomUUID();
        int ratedCount = (int) source.stream().filter(item -> item.ratingValue() != null).count();
        int ratingSum = source.stream().map(SourceItem::ratingValue).filter(value -> value != null)
                .mapToInt(Integer::intValue).sum();
        String status = source.isEmpty() ? "EMPTY_NO_ACTIVITY" : "READY";
        jdbc.update("""
                INSERT INTO c5_taste_report_revision(
                  report_id,owner_user_id,period_start,period_end,revision,status,source_watermark,
                  viewing_count,rated_count,rating_sum,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """, reportId, actor, Date.valueOf(period.start()), Date.valueOf(period.end()), revision,
                status, ts(sourceCutoff), source.size(), ratedCount, ratingSum, ts(now));
        int position = 1;
        for (SourceItem item : source) {
            jdbc.update("""
                    INSERT INTO c5_taste_report_period_item(
                      report_id,position,movie_id,viewing_record_id,viewing_revision,
                      rating_id,rating_revision,rating_value,display_title,poster_url,watched_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """, reportId, position++, item.movieId(), item.viewingId(), item.viewingRevision(),
                    item.ratingId(), item.ratingRevision(), item.ratingValue(), item.displayTitle(), item.posterUrl(),
                    ts(item.watchedAt()));
        }
        TasteReport result = report(actor, reportId, null, 100);
        store(actor, "createMyTasteReportRevision", idempotencyKey, canonical, 201, result);
        return result;
    }

    @Transactional(readOnly = true)
    public TasteReport report(UUID actor, UUID reportId, String cursor, int limit) {
        requireActiveActor(actor);
        ReportRow row = ownedReport(actor, reportId);
        return report(row, actor + ":" + reportId, cursor, limit);
    }

    @Transactional
    public ReportExport createExport(UUID actor, UUID reportId, String idempotencyKey) {
        requireActiveActor(actor);
        ReportRow row = ownedReport(actor, reportId);
        String canonical = reportId.toString();
        ReportExport replay = replay(actor, "createMyTasteReportExport", idempotencyKey, canonical, ReportExport.class);
        if (replay != null) return replay;
        UUID exportId = UUID.randomUUID();
        Instant now = securityNow();
        Instant expires = now.plus(24, ChronoUnit.HOURS);
        jdbc.update("""
                INSERT INTO c5_report_export_job(export_id,owner_user_id,report_id,status,attempts,created_at,updated_at,expires_at)
                VALUES (?,?,?,'PENDING',0,?,?,?)
                """, exportId, actor, reportId, ts(now), ts(now), ts(expires));
        C5LocalPdfStore.StoredArtifact artifact = null;
        try {
            TasteReport report = report(row, "pdf:" + reportId, null, 100);
            artifact = pdfStore.render(report, allReportItems(reportId));
            jdbc.update("""
                    INSERT INTO c5_report_export_artifact(
                      export_id,opaque_path,content_sha256,content_size,media_type,created_at,expires_at)
                    VALUES (?,?,?,?,'application/pdf',?,?)
                    """, exportId, artifact.opaquePath(), artifact.sha256(), artifact.size(), ts(now), ts(expires));
            jdbc.update("UPDATE c5_report_export_job SET status='READY',attempts=1,updated_at=? WHERE export_id=?",
                    ts(now), exportId);
        } catch (RuntimeException exception) {
            if (artifact != null) {
                try {
                    pdfStore.delete(artifact.opaquePath());
                } catch (RuntimeException ignored) {
                    // No path or secret is logged. The bounded cleanup job retries database-known artifacts only.
                }
            }
            throw exception;
        }
        ReportExport result = export(actor, exportId);
        store(actor, "createMyTasteReportExport", idempotencyKey, canonical, 202, result);
        return result;
    }

    @Transactional
    public ReportExport export(UUID actor, UUID exportId) {
        requireActiveActor(actor);
        expireExport(exportId, actor, securityNow());
        ExportRow row = ownedExport(actor, exportId);
        return export(row);
    }

    @Transactional
    public PdfContent download(UUID actor, UUID exportId) {
        requireActiveActor(actor);
        Instant now = securityNow();
        expireExport(exportId, actor, now);
        ExportRow job = ownedExport(actor, exportId);
        if (!"READY".equals(job.status()) || !job.expiresAt().isAfter(now)) {
            throw conflict("EXPORT_EXPIRED", "내보내기 파일이 만료되었어요.");
        }
        ArtifactRow artifact = jdbc.query("""
                SELECT opaque_path,content_sha256,expires_at FROM c5_report_export_artifact WHERE export_id=?
                """, rs -> rs.next() ? new ArtifactRow(rs.getString(1), rs.getString(2), instant(rs, 3)) : null,
                exportId);
        if (artifact == null) throw conflict("REPORT_NOT_READY", "내보내기 파일을 준비하지 못했어요.");
        if (!artifact.expiresAt().isAfter(now)) {
            throw conflict("EXPORT_EXPIRED", "내보내기 파일이 만료되었어요.");
        }
        byte[] bytes = pdfStore.read(artifact.path());
        String actual = sha256(bytes);
        if (!MessageDigest.isEqual(actual.getBytes(StandardCharsets.US_ASCII),
                artifact.sha256().getBytes(StandardCharsets.US_ASCII))) {
            throw conflict("REPORT_NOT_READY", "내보내기 파일을 검증하지 못했어요.");
        }
        return new PdfContent(bytes, actual);
    }

    @Scheduled(fixedDelayString = "${c5.artifact-cleanup-delay-ms:60000}")
    @Transactional
    public void cleanupExpiredArtifacts() {
        Instant now = securityNow();
        List<ArtifactPath> expired = jdbc.query("""
                SELECT a.export_id,a.opaque_path FROM c5_report_export_artifact a
                JOIN c5_report_export_job j ON j.export_id=a.export_id
                WHERE a.expires_at<=? OR j.expires_at<=?
                ORDER BY a.export_id LIMIT 100
                """, (rs, rowNum) -> new ArtifactPath(rs.getObject(1, UUID.class), rs.getString(2)), ts(now), ts(now));
        for (ArtifactPath artifact : expired) {
            pdfStore.delete(artifact.path());
            jdbc.update("DELETE FROM c5_report_export_artifact WHERE export_id=?", artifact.exportId());
            jdbc.update("UPDATE c5_report_export_job SET status='EXPIRED',updated_at=? WHERE export_id=?",
                    ts(now), artifact.exportId());
        }
        jdbc.update("""
                UPDATE c5_report_export_job SET status='EXPIRED',updated_at=?
                 WHERE expires_at<=? AND status IN ('PENDING','READY','FAILED')
                """, ts(now), ts(now));
    }

    @Transactional
    public PrivacySettings privacy(UUID actor) {
        requireActiveActor(actor);
        ensureCapability(actor);
        return privacySettings(actor);
    }

    @Transactional
    public PrivacySettings replacePrivacy(
            UUID actor,
            String idempotencyKey,
            ReplacePrivacySettingsRequest request
    ) {
        requireActiveActor(actor);
        Map<String, String> resources = validatePrivacy(request.resources());
        String canonical = request.expectedRevision() + "\n" + resources;
        PrivacySettings replay = replay(actor, "replaceMyPrivacySettings", idempotencyKey, canonical,
                PrivacySettings.class);
        if (replay != null) return replay;
        ensureCapability(actor);
        Capability capability = jdbc.queryForObject("""
                SELECT public_profile_id,revision FROM c5_public_profile_capability
                 WHERE owner_user_id=? FOR UPDATE
                """, (rs, rowNum) -> new Capability(rs.getObject(1, UUID.class), rs.getLong(2)), actor);
        if (capability.revision() != request.expectedRevision()) {
            throw conflict("REVISION_CONFLICT", "설정이 이미 변경되었어요.");
        }
        Instant now = securityNow();
        for (Map.Entry<String, String> entry : resources.entrySet()) {
            jdbc.update("""
                    INSERT INTO c5_user_privacy_setting(owner_user_id,resource,visibility,updated_at)
                    VALUES (?,?,?,?)
                    ON CONFLICT(owner_user_id,resource) DO UPDATE SET visibility=excluded.visibility,updated_at=excluded.updated_at
                    """, actor, entry.getKey(), entry.getValue(), ts(now));
        }
        jdbc.update("UPDATE c5_public_profile_capability SET revision=revision+1,updated_at=? WHERE owner_user_id=?",
                ts(now), actor);
        PrivacySettings result = privacySettings(actor);
        store(actor, "replaceMyPrivacySettings", idempotencyKey, canonical, 200, result);
        return result;
    }

    @Transactional(readOnly = true)
    public PublicProfile publicProfile(UUID publicProfileId) {
        UUID owner = publicOwner(publicProfileId, "PROFILE");
        String nickname = jdbc.query("SELECT nickname FROM c4_user_profile WHERE user_id=?",
                rs -> rs.next() ? rs.getString(1) : null, owner);
        if (nickname == null) throw notFound();
        return new PublicProfile(publicProfileId, nickname);
    }

    @Transactional(readOnly = true)
    public PublicFilmPage publicFilm(UUID publicProfileId, String cursor, int limit) {
        UUID owner = publicOwner(publicProfileId, "FILM");
        String scope = publicProfileId + ":FILM";
        C5CursorCodec.Decoded decoded = cursors.decode(cursor, "public-film", scope, 2);
        List<PublicFilmRow> rows;
        String select = """
                SELECT f.id,f.movie_id,%s AS title,v.watched_confirmed_at
                  FROM frame f JOIN rating r ON r.id=f.rating_id AND r.logical_status='ACTIVE'
                  JOIN viewing_record v ON v.id=f.viewing_record_id
                 WHERE f.user_id=?
                """.formatted(TITLE_SQL);
        if (decoded == null) {
            rows = jdbc.query(select + " ORDER BY v.watched_confirmed_at DESC,f.id DESC LIMIT ?",
                    this::publicFilmRow, owner, limit + 1);
        } else {
            rows = jdbc.query(select + " AND (v.watched_confirmed_at,f.id)<(?,?)"
                            + " ORDER BY v.watched_confirmed_at DESC,f.id DESC LIMIT ?",
                    this::publicFilmRow, owner, ts(Instant.parse(decoded.lastKey().get(0))),
                    UUID.fromString(decoded.lastKey().get(1)), limit + 1);
        }
        int total = jdbc.queryForObject("""
                SELECT count(*) FROM frame f JOIN rating r ON r.id=f.rating_id AND r.logical_status='ACTIVE'
                 WHERE f.user_id=?
                """, Integer.class, owner);
        boolean more = rows.size() > limit;
        List<PublicFilmRow> page = rows.subList(0, Math.min(limit, rows.size()));
        String next = more ? cursors.encode("public-film", scope, List.of(
                page.get(page.size() - 1).watchedAt().toString(), page.get(page.size() - 1).frameId().toString())) : null;
        return new PublicFilmPage(total, more, next, page.stream()
                .map(row -> new PublicFilmItem(row.frameId(), row.movieId(), row.title(), row.watchedAt())).toList());
    }

    @Transactional(readOnly = true)
    public PublicPopcornPage publicPopcorn(UUID publicProfileId, String cursor, int limit) {
        UUID owner = publicOwner(publicProfileId, "POPCORN");
        String scope = publicProfileId + ":POPCORN";
        C5CursorCodec.Decoded decoded = cursors.decode(cursor, "public-popcorn", scope, 2);
        String select = """
                SELECT p.id,p.frame_id,f.movie_id,%s AS title,p.created_at
                  FROM popcorn p JOIN frame f ON f.id=p.frame_id
                  JOIN rating r ON r.id=p.rating_id AND r.logical_status='ACTIVE'
                  JOIN viewing_record v ON v.id=f.viewing_record_id
                 WHERE p.user_id=?
                """.formatted(TITLE_SQL);
        List<PublicPopcornRow> rows;
        if (decoded == null) {
            rows = jdbc.query(select + " ORDER BY p.created_at DESC,p.id DESC LIMIT ?",
                    this::publicPopcornRow, owner, limit + 1);
        } else {
            rows = jdbc.query(select + " AND (p.created_at,p.id)<(?,?) ORDER BY p.created_at DESC,p.id DESC LIMIT ?",
                    this::publicPopcornRow, owner, ts(Instant.parse(decoded.lastKey().get(0))),
                    UUID.fromString(decoded.lastKey().get(1)), limit + 1);
        }
        int total = jdbc.queryForObject("""
                SELECT count(*) FROM popcorn p JOIN rating r ON r.id=p.rating_id AND r.logical_status='ACTIVE'
                 WHERE p.user_id=?
                """, Integer.class, owner);
        boolean more = rows.size() > limit;
        List<PublicPopcornRow> page = rows.subList(0, Math.min(limit, rows.size()));
        String next = more ? cursors.encode("public-popcorn", scope, List.of(
                page.get(page.size() - 1).createdAt().toString(), page.get(page.size() - 1).popcornId().toString())) : null;
        return new PublicPopcornPage(total, more, next, page.stream().map(row ->
                new PublicPopcornItem(row.popcornId(), row.frameId(), row.movieId(), row.title())).toList());
    }

    @Transactional
    public CreatedReportShare createShare(UUID actor, UUID reportId, String idempotencyKey) {
        requireActiveActor(actor);
        ownedReport(actor, reportId);
        String canonical = reportId.toString();
        ExistingIdempotency existing = idempotency(actor, "createMyTasteReportShare", idempotencyKey, canonical);
        if (existing != null) {
            throw conflict("SHARE_UNAVAILABLE", "공유 보안 문자열은 최초 응답에서만 확인할 수 있어요.");
        }
        advisoryLock("c5-share:" + actor);
        Instant now = securityNow();
        expireShares(now);
        int active = jdbc.queryForObject("""
                SELECT count(*) FROM c5_report_share_grant WHERE owner_user_id=? AND status='ACTIVE'
                """, Integer.class, actor);
        if (active >= 3) throw conflict("INVALID_STATE_TRANSITION", "활성 공유는 최대 3개까지 만들 수 있어요.");
        String raw = randomToken();
        UUID shareId = UUID.randomUUID();
        Instant expires = ZonedDateTime.ofInstant(now, KST).plusMonths(1).toInstant();
        jdbc.update("""
                INSERT INTO c5_report_share_grant(
                  share_id,owner_user_id,report_id,token_sha256,status,created_at,expires_at)
                VALUES (?,?,?,?, 'ACTIVE',?,?)
                """, shareId, actor, reportId, sha256(raw), ts(now), ts(expires));
        CreatedReportShare result = new CreatedReportShare(shareId, reportId, raw,
                "/shared-report#token=" + raw, expires);
        ObjectNode safeReceipt = objectMapper.createObjectNode();
        safeReceipt.put("shareId", shareId.toString());
        safeReceipt.put("reportId", reportId.toString());
        safeReceipt.put("expiresAt", expires.toString());
        safeReceipt.put("rawSecretReplayable", false);
        storeNode(actor, "createMyTasteReportShare", idempotencyKey, canonical, 201, safeReceipt);
        return result;
    }

    @Transactional
    public void revokeShare(UUID actor, UUID shareId, String idempotencyKey) {
        requireActiveActor(actor);
        String canonical = shareId.toString();
        ExistingIdempotency replay = idempotency(actor, "revokeMyTasteReportShare", idempotencyKey, canonical);
        if (replay != null) return;
        ShareRow share = jdbc.query("""
                SELECT share_id,status,expires_at FROM c5_report_share_grant
                 WHERE share_id=? AND owner_user_id=? FOR UPDATE
                """, rs -> rs.next() ? new ShareRow(rs.getObject(1, UUID.class), rs.getString(2), instant(rs, 3)) : null,
                shareId, actor);
        if (share == null) throw notFound();
        Instant now = securityNow();
        if ("ACTIVE".equals(share.status())) {
            jdbc.update("UPDATE c5_report_share_grant SET status='REVOKED',terminal_at=? WHERE share_id=?",
                    ts(now), shareId);
        }
        jdbc.update("""
                UPDATE c5_report_share_viewer_session SET status='REVOKED',terminal_at=?
                 WHERE share_id=? AND status='ACTIVE'
                """, ts(now), shareId);
        storeNode(actor, "revokeMyTasteReportShare", idempotencyKey, canonical, 204,
                objectMapper.createObjectNode());
    }

    @Transactional
    public ReportViewerSession exchange(String rawToken) {
        if (rawToken == null || rawToken.length() < 43 || rawToken.length() > 256) throw notFound();
        Instant now = securityNow();
        expireShares(now);
        ShareGrant grant = jdbc.query("""
                SELECT share_id,report_id FROM c5_report_share_grant
                 WHERE token_sha256=? AND status='ACTIVE' AND expires_at>?
                """, rs -> rs.next() ? new ShareGrant(rs.getObject(1, UUID.class), rs.getObject(2, UUID.class)) : null,
                sha256(rawToken), ts(now));
        if (grant == null) throw notFound();
        String rawSession = randomToken();
        UUID sessionId = UUID.randomUUID();
        Instant expires = now.plus(15, ChronoUnit.MINUTES);
        jdbc.update("""
                INSERT INTO c5_report_share_viewer_session(
                  session_id,share_id,session_sha256,status,created_at,expires_at)
                VALUES (?,?,?,'ACTIVE',?,?)
                """, sessionId, grant.shareId(), sha256(rawSession), ts(now), ts(expires));
        return new ReportViewerSession(rawSession, expires);
    }

    @Transactional
    public SharedTasteReport sharedReport(String rawSession, String cursor, int limit) {
        if (rawSession == null || rawSession.length() < 43 || rawSession.length() > 256) throw notFound();
        Instant now = securityNow();
        expireShares(now);
        ViewerAccess access = jdbc.query("""
                SELECT s.session_id,g.report_id,g.owner_user_id,p.nickname
                  FROM c5_report_share_viewer_session s
                  JOIN c5_report_share_grant g ON g.share_id=s.share_id
                  JOIN c4_user_account a ON a.user_id=g.owner_user_id AND a.membership_status='ACTIVE'
                  JOIN c4_user_profile p ON p.user_id=g.owner_user_id
                 WHERE s.session_sha256=? AND s.status='ACTIVE' AND s.expires_at>?
                   AND g.status='ACTIVE' AND g.expires_at>?
                """, rs -> rs.next() ? new ViewerAccess(rs.getObject(1, UUID.class), rs.getObject(2, UUID.class),
                        rs.getObject(3, UUID.class), rs.getString(4)) : null,
                sha256(rawSession), ts(now), ts(now));
        if (access == null) throw notFound();
        ReportRow row = ownedReport(access.ownerId(), access.reportId());
        TasteReport report = report(row, "viewer:" + access.sessionId() + ":" + access.reportId(), cursor, limit);
        return new SharedTasteReport(report, access.nickname());
    }

    @Transactional
    public NotificationSettings notificationSettings(UUID actor) {
        requireActiveActor(actor);
        Setting setting = currentSetting(actor);
        if (!setting.enabled()) jdbc.update("DELETE FROM c5_in_app_notification WHERE owner_user_id=?", actor);
        return new NotificationSettings(setting.enabled(), setting.revision());
    }

    @Transactional
    public NotificationSettings replaceNotificationSettings(
            UUID actor,
            String idempotencyKey,
            ReplaceNotificationSettingsRequest request
    ) {
        requireActiveActor(actor);
        String canonical = request.expectedRevision() + "\n" + request.watchConfirmationDueEnabled();
        NotificationSettings replay = replay(actor, "replaceMyNotificationSettings", idempotencyKey, canonical,
                NotificationSettings.class);
        if (replay != null) return replay;
        advisoryLock("c5-notification-setting:" + actor);
        Setting setting = currentSetting(actor);
        if (setting.revision() != request.expectedRevision()) {
            throw conflict("REVISION_CONFLICT", "설정이 이미 변경되었어요.");
        }
        Instant now = securityNow();
        long next = setting.revision() + 1;
        jdbc.update("""
                INSERT INTO c5_user_notification_setting(
                  owner_user_id,watch_confirmation_due_enabled,revision,updated_at)
                VALUES (?,?,?,?)
                ON CONFLICT(owner_user_id) DO UPDATE SET
                  watch_confirmation_due_enabled=excluded.watch_confirmation_due_enabled,
                  revision=excluded.revision,updated_at=excluded.updated_at
                """, actor, request.watchConfirmationDueEnabled(), next, ts(now));
        if (request.watchConfirmationDueEnabled()) syncNotifications(actor, now);
        else jdbc.update("DELETE FROM c5_in_app_notification WHERE owner_user_id=?", actor);
        NotificationSettings result = new NotificationSettings(request.watchConfirmationDueEnabled(), next);
        store(actor, "replaceMyNotificationSettings", idempotencyKey, canonical, 200, result);
        return result;
    }

    @Transactional
    public NotificationPage notifications(UUID actor, String cursor, int limit) {
        requireActiveActor(actor);
        Setting setting = currentSetting(actor);
        if (!setting.enabled()) {
            jdbc.update("DELETE FROM c5_in_app_notification WHERE owner_user_id=?", actor);
            return new NotificationPage(0, false, null, List.of());
        }
        Instant now = securityNow();
        syncNotifications(actor, now);
        C5CursorCodec.Decoded decoded = cursors.decode(cursor, "notification-list", actor.toString(), 2);
        List<NotificationRow> rows;
        if (decoded == null) {
            rows = jdbc.query("""
                    SELECT notification_id,state,message,created_at FROM c5_in_app_notification
                     WHERE owner_user_id=? ORDER BY created_at DESC,notification_id DESC LIMIT ?
                    """, this::notificationRow, actor, limit + 1);
        } else {
            rows = jdbc.query("""
                    SELECT notification_id,state,message,created_at FROM c5_in_app_notification
                     WHERE owner_user_id=? AND (created_at,notification_id)<(?,?)
                     ORDER BY created_at DESC,notification_id DESC LIMIT ?
                    """, this::notificationRow, actor, ts(Instant.parse(decoded.lastKey().get(0))),
                    UUID.fromString(decoded.lastKey().get(1)), limit + 1);
        }
        int total = jdbc.queryForObject("SELECT count(*) FROM c5_in_app_notification WHERE owner_user_id=?",
                Integer.class, actor);
        boolean more = rows.size() > limit;
        List<NotificationRow> page = rows.subList(0, Math.min(limit, rows.size()));
        String next = more ? cursors.encode("notification-list", actor.toString(), List.of(
                page.get(page.size() - 1).createdAt().toString(), page.get(page.size() - 1).id().toString())) : null;
        return new NotificationPage(total, more, next, page.stream().map(this::notification).toList());
    }

    @Transactional
    public InAppNotification updateNotification(
            UUID actor,
            UUID notificationId,
            String idempotencyKey,
            UpdateNotificationStateRequest request
    ) {
        requireActiveActor(actor);
        String target = request.state() == null ? "" : request.state().toUpperCase(Locale.ROOT);
        if (!Set.of("READ", "DISMISSED").contains(target)) throw validation("state", "unsupported_state");
        String canonical = notificationId + "\n" + target;
        InAppNotification replay = replay(actor, "updateMyNotificationState", idempotencyKey, canonical,
                InAppNotification.class);
        if (replay != null) return replay;
        NotificationRow current = jdbc.query("""
                SELECT notification_id,state,message,created_at FROM c5_in_app_notification
                 WHERE notification_id=? AND owner_user_id=? FOR UPDATE
                """, rs -> rs.next() ? notificationRow(rs, 0) : null, notificationId, actor);
        if (current == null) throw notFound();
        boolean allowed = ("UNREAD".equals(current.state()) && Set.of("READ", "DISMISSED").contains(target))
                || ("READ".equals(current.state()) && "DISMISSED".equals(target));
        if (!allowed) throw conflict("INVALID_STATE_TRANSITION", "알림 상태를 변경할 수 없어요.");
        Instant now = securityNow();
        if ("DISMISSED".equals(target)) {
            jdbc.update("""
                    UPDATE c5_in_app_notification SET state='DISMISSED',terminal_at=?,
                      expires_at=LEAST(expires_at,?) WHERE notification_id=?
                    """, ts(now), ts(now.plus(7, ChronoUnit.DAYS)), notificationId);
        } else {
            jdbc.update("UPDATE c5_in_app_notification SET state='READ' WHERE notification_id=?", notificationId);
        }
        NotificationRow updated = jdbc.queryForObject("""
                SELECT notification_id,state,message,created_at FROM c5_in_app_notification WHERE notification_id=?
                """, this::notificationRow, notificationId);
        InAppNotification result = notification(updated);
        store(actor, "updateMyNotificationState", idempotencyKey, canonical, 200, result);
        return result;
    }

    private Period period(LocalDate start) {
        if (start == null || !(start.getMonthValue() == 1 && start.getDayOfMonth() == 1)
                && !(start.getMonthValue() == 7 && start.getDayOfMonth() == 1)) {
            throw validation("periodStart", "must_be_calendar_half_start");
        }
        return new Period(start, start.getMonthValue() == 1
                ? LocalDate.of(start.getYear(), 6, 30) : LocalDate.of(start.getYear(), 12, 31));
    }

    private List<SourceItem> sourceItems(UUID actor, Period period, Instant sourceCutoff) {
        Instant from = period.start().atStartOfDay(KST).toInstant();
        Instant until = period.end().plusDays(1).atStartOfDay(KST).toInstant();
        String sql = """
                SELECT v.id,v.revision,v.movie_id,v.watched_confirmed_at,
                       r.id,r.revision,r.value,%s AS title,
                       NULL::text AS poster_url
                  FROM viewing_record v
                  LEFT JOIN rating r ON r.viewing_record_id=v.id AND r.logical_status='ACTIVE'
                 WHERE v.user_id=? AND v.watched_confirmed_at>=? AND v.watched_confirmed_at<?
                   AND v.watched_confirmed_at<=?
                 ORDER BY v.watched_confirmed_at ASC,v.movie_id ASC
                """.formatted(TITLE_SQL);
        return jdbc.query(sql, (rs, rowNum) -> new SourceItem(
                rs.getObject(1, UUID.class), rs.getInt(2), rs.getObject(3, UUID.class), instant(rs, 4),
                rs.getObject(5, UUID.class), nullableInt(rs, 6), nullableInt(rs, 7), rs.getString(8),
                rs.getString(9)), actor, ts(from), ts(until), ts(sourceCutoff));
    }

    private TasteReport report(ReportRow row, String scope, String cursor, int limit) {
        ReportMoviePage page = reportItems(row.reportId(), scope, cursor, limit);
        BigDecimal average = row.ratedCount() == 0 ? null : BigDecimal.valueOf(row.ratingSum())
                .divide(BigDecimal.valueOf(row.ratedCount()), 2, RoundingMode.HALF_UP).stripTrailingZeros();
        return new TasteReport(row.reportId(), row.periodStart(), row.periodEnd(), row.revision(), row.status(),
                row.createdAt(), new FactualReportMetrics(row.viewingCount(), row.ratedCount(), average), page);
    }

    private ReportMoviePage reportItems(UUID reportId, String scope, String cursor, int limit) {
        C5CursorCodec.Decoded decoded = cursors.decode(cursor, "report-items", scope, 1);
        int after = decoded == null ? 0 : Integer.parseInt(decoded.lastKey().get(0));
        List<PositionedReportItem> rows = jdbc.query("""
                SELECT position,movie_id,display_title,poster_url,watched_at,rating_value
                  FROM c5_taste_report_period_item WHERE report_id=? AND position>?
                 ORDER BY position LIMIT ?
                """, (rs, rowNum) -> new PositionedReportItem(rs.getInt(1), reportMovieItem(rs, 2)),
                reportId, after, limit + 1);
        int total = jdbc.queryForObject("SELECT count(*) FROM c5_taste_report_period_item WHERE report_id=?",
                Integer.class, reportId);
        boolean more = rows.size() > limit;
        List<PositionedReportItem> page = rows.subList(0, Math.min(limit, rows.size()));
        String next = more ? cursors.encode("report-items", scope,
                List.of(Integer.toString(page.get(page.size() - 1).position()))) : null;
        return new ReportMoviePage(total, more, next, page.stream().map(PositionedReportItem::item).toList());
    }

    private List<ReportMovieItem> allReportItems(UUID reportId) {
        return jdbc.query("""
                SELECT movie_id,display_title,poster_url,watched_at,rating_value
                  FROM c5_taste_report_period_item WHERE report_id=? ORDER BY position
                """, (rs, rowNum) -> reportMovieItem(rs, 1), reportId);
    }

    private ReportMovieItem reportMovieItem(java.sql.ResultSet rs, int start) throws java.sql.SQLException {
        String poster = rs.getString(start + 2);
        return new ReportMovieItem(rs.getObject(start, UUID.class), rs.getString(start + 1),
                poster == null ? null : URI.create(poster), instant(rs, start + 3), nullableInt(rs, start + 4));
    }

    private ReportRow ownedReport(UUID actor, UUID reportId) {
        ReportRow row = jdbc.query("""
                SELECT report_id,period_start,period_end,revision,status,source_watermark,
                       viewing_count,rated_count,rating_sum,created_at
                  FROM c5_taste_report_revision WHERE report_id=? AND owner_user_id=?
                """, rs -> rs.next() ? reportRow(rs, 0) : null, reportId, actor);
        if (row == null) throw notFound();
        return row;
    }

    private ReportRow reportRow(java.sql.ResultSet rs, int ignored) throws java.sql.SQLException {
        return new ReportRow(rs.getObject(1, UUID.class), rs.getDate(2).toLocalDate(),
                rs.getDate(3).toLocalDate(), rs.getInt(4), rs.getString(5), instant(rs, 6),
                rs.getInt(7), rs.getInt(8), rs.getInt(9), instant(rs, 10));
    }

    private TasteReportSummary summary(ReportRow row) {
        return new TasteReportSummary(row.reportId(), row.periodStart(), row.periodEnd(), row.revision(),
                row.status(), row.createdAt());
    }

    private ExportRow ownedExport(UUID actor, UUID exportId) {
        ExportRow row = jdbc.query("""
                SELECT export_id,report_id,status,created_at,expires_at FROM c5_report_export_job
                 WHERE export_id=? AND owner_user_id=?
                """, rs -> rs.next() ? new ExportRow(rs.getObject(1, UUID.class), rs.getObject(2, UUID.class),
                        rs.getString(3), instant(rs, 4), instant(rs, 5)) : null, exportId, actor);
        if (row == null) throw notFound();
        return row;
    }

    private ReportExport export(ExportRow row) {
        String href = "READY".equals(row.status()) ? "/api/v1/me/report-exports/" + row.exportId() + "/content" : null;
        return new ReportExport(row.exportId(), row.reportId(), row.status(), row.createdAt(), row.expiresAt(), href);
    }

    private void expireExport(UUID exportId, UUID actor, Instant now) {
        jdbc.update("""
                UPDATE c5_report_export_job SET status='EXPIRED',updated_at=?
                 WHERE export_id=? AND owner_user_id=? AND expires_at<=? AND status<>'EXPIRED'
                """, ts(now), exportId, actor, ts(now));
    }

    private void ensureCapability(UUID actor) {
        Instant now = securityNow();
        int inserted = jdbc.update("""
                INSERT INTO c5_public_profile_capability(
                  owner_user_id,public_profile_id,revision,created_at,updated_at)
                VALUES (?,?,1,?,?) ON CONFLICT(owner_user_id) DO NOTHING
                """, actor, UUID.randomUUID(), ts(now), ts(now));
        if (inserted == 1) {
            for (String resource : List.of("PROFILE", "FILM", "POPCORN")) {
                jdbc.update("""
                        INSERT INTO c5_user_privacy_setting(owner_user_id,resource,visibility,updated_at)
                        VALUES (?,?,'PRIVATE',?)
                        """, actor, resource, ts(now));
            }
        }
    }

    private PrivacySettings privacySettings(UUID actor) {
        Capability capability = jdbc.queryForObject("""
                SELECT public_profile_id,revision FROM c5_public_profile_capability WHERE owner_user_id=?
                """, (rs, rowNum) -> new Capability(rs.getObject(1, UUID.class), rs.getLong(2)), actor);
        Map<String, String> values = new TreeMap<>();
        PRIVACY_RESOURCES.forEach(resource -> values.put(resource, "PRIVATE"));
        jdbc.query("SELECT resource,visibility FROM c5_user_privacy_setting WHERE owner_user_id=?",
                rs -> {
                    while (rs.next()) values.put(rs.getString(1), rs.getString(2));
                    return null;
                }, actor);
        List<ResourcePrivacy> resources = List.of("PROFILE", "FILM", "POPCORN").stream()
                .map(resource -> new ResourcePrivacy(resource, values.get(resource))).toList();
        return new PrivacySettings(capability.publicProfileId(), capability.revision(), resources);
    }

    private Map<String, String> validatePrivacy(List<ResourcePrivacy> request) {
        if (request == null || request.size() != 3) throw validation("resources", "exactly_three_required");
        Map<String, String> values = new TreeMap<>();
        for (ResourcePrivacy item : request) {
            String resource = item.resource() == null ? "" : item.resource().toUpperCase(Locale.ROOT);
            String visibility = item.visibility() == null ? "" : item.visibility().toUpperCase(Locale.ROOT);
            if (!PRIVACY_RESOURCES.contains(resource) || !VISIBILITIES.contains(visibility)
                    || values.put(resource, visibility) != null) {
                throw validation("resources", "exact_unique_resource_set_required");
            }
        }
        if (!values.keySet().equals(PRIVACY_RESOURCES)) throw validation("resources", "exact_resource_set_required");
        return Map.copyOf(values);
    }

    private UUID publicOwner(UUID publicProfileId, String resource) {
        UUID owner = jdbc.query("""
                SELECT c.owner_user_id FROM c5_public_profile_capability c
                JOIN c5_user_privacy_setting s ON s.owner_user_id=c.owner_user_id
                JOIN c4_user_account a ON a.user_id=c.owner_user_id AND a.membership_status='ACTIVE'
                WHERE c.public_profile_id=? AND s.resource=? AND s.visibility='PUBLIC'
                """, rs -> rs.next() ? rs.getObject(1, UUID.class) : null, publicProfileId, resource);
        if (owner == null) throw notFound();
        return owner;
    }

    private PublicFilmRow publicFilmRow(java.sql.ResultSet rs, int ignored) throws java.sql.SQLException {
        return new PublicFilmRow(rs.getObject(1, UUID.class), rs.getObject(2, UUID.class),
                rs.getString(3), instant(rs, 4));
    }

    private PublicPopcornRow publicPopcornRow(java.sql.ResultSet rs, int ignored) throws java.sql.SQLException {
        return new PublicPopcornRow(rs.getObject(1, UUID.class), rs.getObject(2, UUID.class),
                rs.getObject(3, UUID.class), rs.getString(4), instant(rs, 5));
    }

    private void expireShares(Instant now) {
        jdbc.update("""
                UPDATE c5_report_share_grant SET status='EXPIRED',terminal_at=?
                 WHERE status='ACTIVE' AND expires_at<=?
                """, ts(now), ts(now));
        jdbc.update("""
                UPDATE c5_report_share_viewer_session s SET status='EXPIRED',terminal_at=?
                 WHERE s.status='ACTIVE' AND (s.expires_at<=? OR EXISTS (
                   SELECT 1 FROM c5_report_share_grant g WHERE g.share_id=s.share_id AND g.status<>'ACTIVE'))
                """, ts(now), ts(now));
    }

    private Setting currentSetting(UUID actor) {
        return jdbc.query("""
                SELECT watch_confirmation_due_enabled,revision FROM c5_user_notification_setting
                 WHERE owner_user_id=?
                """, rs -> rs.next() ? new Setting(rs.getBoolean(1), rs.getLong(2)) : new Setting(false, 1), actor);
    }

    private void syncNotifications(UUID actor, Instant now) {
        jdbc.update("""
                DELETE FROM c5_in_app_notification
                 WHERE owner_user_id=? AND expires_at<=?
                """, actor, ts(now));
        jdbc.update("""
                UPDATE c5_in_app_notification n SET state='DISMISSED',terminal_at=?,expires_at=LEAST(n.expires_at,?)
                 WHERE n.owner_user_id=? AND n.state<>'DISMISSED' AND NOT EXISTS (
                   SELECT 1 FROM watch_intent w WHERE w.id=n.source_id AND w.user_id=n.owner_user_id
                     AND w.revision=n.source_revision AND w.status='CONFIRMATION_PENDING'
                     AND w.confirmation_due_at<=? AND w.expires_at>?)
                """, ts(now), ts(now.plus(7, ChronoUnit.DAYS)), actor, ts(now), ts(now));
        jdbc.update("""
                INSERT INTO c5_in_app_notification(
                  notification_id,owner_user_id,category,source_type,source_id,source_revision,
                  state,message,created_at,expires_at)
                SELECT gen_random_uuid(),w.user_id,'WATCH_CONFIRMATION_DUE','WATCH_INTENT',w.id,w.revision,
                       'UNREAD','감상 여부를 확인해 주세요.',?,LEAST(w.expires_at,?::timestamptz)
                  FROM watch_intent w
                 WHERE w.user_id=? AND w.status='CONFIRMATION_PENDING'
                   AND w.confirmation_due_at<=? AND w.expires_at>?
                ON CONFLICT(owner_user_id,source_type,source_id,source_revision) DO NOTHING
                """, ts(now), ts(now.plus(30, ChronoUnit.DAYS)), actor, ts(now), ts(now));
    }

    private NotificationRow notificationRow(java.sql.ResultSet rs, int ignored) throws java.sql.SQLException {
        return new NotificationRow(rs.getObject(1, UUID.class), rs.getString(2), rs.getString(3), instant(rs, 4));
    }

    private InAppNotification notification(NotificationRow row) {
        return new InAppNotification(row.id(), "WATCH_CONFIRMATION_DUE", row.state(), row.message(), row.createdAt());
    }

    private <T> T replay(UUID actor, String operation, String key, String canonical, Class<T> type) {
        ExistingIdempotency existing = idempotency(actor, operation, key, canonical);
        if (existing == null) return null;
        try {
            return objectMapper.treeToValue(existing.body(), type);
        } catch (Exception exception) {
            throw new IllegalStateException("Stored C5 idempotency response is invalid", exception);
        }
    }

    private ExistingIdempotency idempotency(UUID actor, String operation, String key, String canonical) {
        validateKey(key);
        advisoryLock("c5-idempotency:" + actor + ":" + operation + ":" + key);
        String hash = sha256(canonical);
        ExistingIdempotency existing = jdbc.query("""
                SELECT request_sha256,response_status,response_body FROM c5_idempotency_result
                 WHERE actor_user_id=? AND operation=? AND idempotency_key=?
                """, rs -> rs.next() ? new ExistingIdempotency(rs.getString(1), rs.getInt(2),
                        readTree(rs.getString(3))) : null, actor, operation, key);
        if (existing != null && !MessageDigest.isEqual(hash.getBytes(StandardCharsets.US_ASCII),
                existing.requestHash().getBytes(StandardCharsets.US_ASCII))) {
            throw conflict("IDEMPOTENCY_KEY_REUSED", "같은 키를 다른 요청에 사용할 수 없어요.");
        }
        return existing;
    }

    private void store(UUID actor, String operation, String key, String canonical, int status, Object body) {
        storeNode(actor, operation, key, canonical, status, objectMapper.valueToTree(body));
    }

    private void storeNode(UUID actor, String operation, String key, String canonical, int status, JsonNode body) {
        jdbc.update("""
                INSERT INTO c5_idempotency_result(
                  actor_user_id,operation,idempotency_key,request_sha256,response_status,response_body,created_at)
                VALUES (?,?,?,?,?,CAST(? AS jsonb),?)
                """, actor, operation, key, sha256(canonical), status, body.toString(), ts(securityNow()));
    }

    private void validateKey(String key) {
        if (key == null || key.length() < 8 || key.length() > 128 || !key.matches("^[!-~]+$")) {
            throw validation("Idempotency-Key", "invalid_format");
        }
    }

    private void advisoryLock(String value) {
        jdbc.query("SELECT pg_advisory_xact_lock(hashtextextended(?,0))", (rs, rowNum) -> 0, value);
    }

    private Instant securityNow() {
        return jdbc.queryForObject("SELECT clock_timestamp()", (rs, rowNum) -> rs.getTimestamp(1).toInstant());
    }

    private Instant sourceTransactionWatermark() {
        return jdbc.queryForObject("SELECT transaction_timestamp()", (rs, rowNum) -> rs.getTimestamp(1).toInstant());
    }

    private JsonNode readTree(String value) {
        try {
            return objectMapper.readTree(value);
        } catch (Exception exception) {
            throw new IllegalStateException("Invalid stored JSON", exception);
        }
    }

    private String randomToken() {
        byte[] value = new byte[32];
        secureRandom.nextBytes(value);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(value);
    }

    private static String sha256(String value) {
        return sha256(value.getBytes(StandardCharsets.UTF_8));
    }

    private static String sha256(byte[] value) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value));
        } catch (Exception exception) {
            throw new IllegalStateException(exception);
        }
    }

    private static Instant instant(java.sql.ResultSet rs, int column) throws java.sql.SQLException {
        Timestamp timestamp = rs.getTimestamp(column);
        return timestamp == null ? null : timestamp.toInstant();
    }

    private static Integer nullableInt(java.sql.ResultSet rs, int column) throws java.sql.SQLException {
        int value = rs.getInt(column);
        return rs.wasNull() ? null : value;
    }

    private static Timestamp ts(Instant value) {
        return Timestamp.from(value);
    }

    private static ApiException validation(String field, String reason) {
        return new ApiException(HttpStatus.BAD_REQUEST, "VALIDATION_ERROR", "요청 값을 확인해 주세요.",
                List.of(new CatalogApiDtos.FieldError(field, reason)));
    }

    private static ApiException notFound() {
        return new ApiException(HttpStatus.NOT_FOUND, "RESOURCE_NOT_FOUND", "요청한 정보를 찾을 수 없어요.");
    }

    private static ApiException conflict(String code, String message) {
        return new ApiException(HttpStatus.CONFLICT, code, message);
    }

    private record Period(LocalDate start, LocalDate end) {
    }

    private record SourceItem(
            UUID viewingId,
            int viewingRevision,
            UUID movieId,
            Instant watchedAt,
            UUID ratingId,
            Integer ratingRevision,
            Integer ratingValue,
            String displayTitle,
            String posterUrl
    ) {
    }

    private record ReportRow(
            UUID reportId,
            LocalDate periodStart,
            LocalDate periodEnd,
            int revision,
            String status,
            Instant sourceWatermark,
            int viewingCount,
            int ratedCount,
            int ratingSum,
            Instant createdAt
    ) {
    }

    private record PositionedReportItem(int position, ReportMovieItem item) {
    }

    private record ExportRow(UUID exportId, UUID reportId, String status, Instant createdAt, Instant expiresAt) {
    }

    private record ArtifactRow(String path, String sha256, Instant expiresAt) {
    }

    private record ArtifactPath(UUID exportId, String path) {
    }

    private record Capability(UUID publicProfileId, long revision) {
    }

    private record PublicFilmRow(UUID frameId, UUID movieId, String title, Instant watchedAt) {
    }

    private record PublicPopcornRow(UUID popcornId, UUID frameId, UUID movieId, String title, Instant createdAt) {
    }

    private record ShareRow(UUID shareId, String status, Instant expiresAt) {
    }

    private record ShareGrant(UUID shareId, UUID reportId) {
    }

    private record ViewerAccess(UUID sessionId, UUID reportId, UUID ownerId, String nickname) {
    }

    private record Setting(boolean enabled, long revision) {
    }

    private record NotificationRow(UUID id, String state, String message, Instant createdAt) {
    }

    private record ExistingIdempotency(String requestHash, int status, JsonNode body) {
    }
}
