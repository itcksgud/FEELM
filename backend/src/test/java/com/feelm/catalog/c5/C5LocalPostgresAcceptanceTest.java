package com.feelm.catalog.c5;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.feelm.catalog.c5.api.C5Controller;
import com.feelm.catalog.c5.api.C5ApiDtos.CreateTasteReportRequest;
import com.feelm.catalog.c5.service.C5LocalService;
import com.feelm.catalog.c5.service.C5LoopbackGuard;
import com.feelm.catalog.security.C4AccessTokenVerifier;
import com.feelm.catalog.security.CatalogUserContext;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.http.HttpHeaders;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.servlet.mvc.method.annotation.RequestMappingHandlerMapping;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Timestamp;
import java.time.Instant;
import java.time.ZoneId;
import java.util.HexFormat;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest(properties = {
        "spring.flyway.locations=classpath:db/migration,classpath:db/local",
        "spring.flyway.out-of-order=true",
        "catalog.fixed-clock=2026-08-29T12:00:00Z",
        "catalog.auth-mode=fake",
        "catalog.c4.enabled=false",
        "catalog.c3.enabled=false",
        "catalog.c2b.enabled=false",
        "c5.local.enabled=true",
        "server.address=127.0.0.1",
        "c5.artifact-cleanup-delay-ms=3600000"
})
@AutoConfigureMockMvc
@ActiveProfiles("local")
@Testcontainers(disabledWithoutDocker = true)
class C5LocalPostgresAcceptanceTest {
    private static final UUID OWNER = UUID.fromString("018f6826-4da1-7c38-a846-8f794cd8b0cf");
    private static final UUID OTHER = UUID.fromString("5f93a51d-a6f1-41dc-8d86-6b570d53bd82");
    private static final UUID MOVIE = UUID.fromString("e67778c9-7b2e-42d4-9d3e-a3026b2efea3");
    private static final UUID MOVIE_TWO = UUID.fromString("97204ea5-e6e5-4417-a13f-bc8197660705");
    private static final UUID MOVIE_THREE = UUID.fromString("0437c1c0-06d5-4cdf-a7d1-5d5f1dc42e89");
    private static final UUID PROVIDER = UUID.fromString("d392a4d5-0428-4e06-aa41-aef899c06842");
    private static final UUID WATCH = UUID.fromString("4eb2e4b8-882b-48d9-9870-f76b8c1e9001");
    private static final UUID VIEWING = UUID.fromString("4eb2e4b8-882b-48d9-9870-f76b8c1e9002");
    private static final UUID RATING = UUID.fromString("4eb2e4b8-882b-48d9-9870-f76b8c1e9003");
    private static final UUID FRAME = UUID.fromString("4eb2e4b8-882b-48d9-9870-f76b8c1e9004");
    private static final UUID POPCORN = UUID.fromString("4eb2e4b8-882b-48d9-9870-f76b8c1e9005");
    private static final UUID WATCH_TWO = UUID.fromString("4eb2e4b8-882b-48d9-9870-f76b8c1e9011");
    private static final UUID VIEWING_TWO = UUID.fromString("4eb2e4b8-882b-48d9-9870-f76b8c1e9012");
    private static final UUID RATING_TWO = UUID.fromString("4eb2e4b8-882b-48d9-9870-f76b8c1e9013");
    private static final UUID WATCH_THREE = UUID.fromString("4eb2e4b8-882b-48d9-9870-f76b8c1e9021");
    private static final UUID VIEWING_THREE = UUID.fromString("4eb2e4b8-882b-48d9-9870-f76b8c1e9022");
    private static final UUID RATING_THREE = UUID.fromString("4eb2e4b8-882b-48d9-9870-f76b8c1e9023");
    private static final String OWNER_AUTH = "Bearer c5-dynamic-owner-access";
    private static final String OTHER_AUTH = "Bearer c5-dynamic-other-access";

    @TempDir
    static Path artifactDirectory;

    @Container
    static final PostgreSQLContainer<?> POSTGRES = new PostgreSQLContainer<>("postgres:17.6-alpine")
            .withDatabaseName("feelm")
            .withUsername("feelm")
            .withPassword("feelm_local");

    @DynamicPropertySource
    static void properties(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", POSTGRES::getJdbcUrl);
        registry.add("spring.datasource.username", POSTGRES::getUsername);
        registry.add("spring.datasource.password", POSTGRES::getPassword);
        registry.add("c5.artifact-directory", () -> artifactDirectory.toString());
    }

    @Autowired MockMvc mvc;
    @Autowired ObjectMapper objectMapper;
    @Autowired JdbcTemplate jdbc;
    @Autowired C5LocalService service;
    @Autowired @Qualifier("requestMappingHandlerMapping") RequestMappingHandlerMapping mappings;
    @Autowired PlatformTransactionManager transactionManager;

    @BeforeEach
    void reset() {
        jdbc.execute("""
                TRUNCATE TABLE
                  c5_report_share_viewer_session,c5_report_share_grant,
                  c5_report_export_artifact,c5_report_export_job,
                  c5_taste_report_period_item,c5_taste_report_revision,
                  c5_user_privacy_setting,c5_public_profile_capability,
                  c5_in_app_notification,c5_user_notification_setting,c5_idempotency_result
                """);
        jdbc.update("DELETE FROM popcorn WHERE id=?", POPCORN);
        jdbc.update("DELETE FROM frame WHERE id=?", FRAME);
        jdbc.update("DELETE FROM rating WHERE id IN (?,?,?)", RATING, RATING_TWO, RATING_THREE);
        jdbc.update("DELETE FROM viewing_record WHERE id IN (?,?,?)", VIEWING, VIEWING_TWO, VIEWING_THREE);
        jdbc.update("DELETE FROM watch_intent WHERE id IN (?,?,?)", WATCH, WATCH_TWO, WATCH_THREE);

        upsertActor(OWNER, "c5_owner");
        upsertActor(OTHER, "c5_other");
        jdbc.update("""
                INSERT INTO watch_intent(
                  id,user_id,movie_id,provider_id,status,clicked_at,confirmation_due_at,expires_at,responded_at,revision)
                VALUES (?,?,?,?,'CONFIRMED_WATCHED',?,?,?,?,2)
                """, WATCH, OWNER, MOVIE, PROVIDER, ts("2026-06-20T10:00:00Z"),
                ts("2026-06-22T10:00:00Z"), ts("2026-06-27T10:00:00Z"), ts("2026-06-23T10:00:00Z"));
        jdbc.update("""
                INSERT INTO viewing_record(
                  id,user_id,movie_id,source_watch_intent_id,provider_id,status,watched_confirmed_at,revision)
                VALUES (?,?,?,?,?,'RATED_COMPLETED',?,2)
                """, VIEWING, OWNER, MOVIE, WATCH, PROVIDER, ts("2026-06-23T10:00:00Z"));
        jdbc.update("""
                INSERT INTO rating(
                  id,user_id,movie_id,viewing_record_id,value,logical_status,revision,created_at,updated_at)
                VALUES (?,?,?,?,4,'ACTIVE',1,?,?)
                """, RATING, OWNER, MOVIE, VIEWING, ts("2026-06-23T10:05:00Z"), ts("2026-06-23T10:05:00Z"));
        jdbc.update("""
                INSERT INTO frame(id,user_id,movie_id,viewing_record_id,rating_id,derivation_version,created_at,updated_at)
                VALUES (?,?,?,?,?,'c5-test-v1',?,?)
                """, FRAME, OWNER, MOVIE, VIEWING, RATING, ts("2026-06-23T10:05:00Z"), ts("2026-06-23T10:05:00Z"));
        jdbc.update("""
                INSERT INTO popcorn(id,user_id,frame_id,rating_id,flavor_id,flavor_mapping_version,created_at)
                VALUES (?,?,?,?,?,'v1',?)
                """, POPCORN, OWNER, FRAME, RATING,
                UUID.fromString("18828763-1fd7-4ee4-a97f-1496db3c6490"), ts("2026-06-23T10:05:00Z"));
        insertReportOnlyRating(WATCH_TWO, VIEWING_TWO, RATING_TWO, MOVIE_TWO, 3,
                "2026-06-23T10:30:00Z");
        insertReportOnlyRating(WATCH_THREE, VIEWING_THREE, RATING_THREE, MOVIE_THREE, 3,
                "2026-06-23T11:00:00Z");
        jdbc.update("""
                UPDATE watch_intent SET status='CONFIRMATION_PENDING',responded_at=NULL,revision=1
                 WHERE id IN ('2dfa8b82-9f40-452d-a63f-18347483f7b7','8b7f4a21-4bc4-4c5e-93cb-4e348abcae02')
                """);
    }

    @Test
    void mapsExactlyNineteenLocalOperationsAndFailsClosedOffLoopback() throws Exception {
        long count = mappings.getHandlerMethods().values().stream()
                .filter(method -> method.getBeanType().equals(C5Controller.class))
                .count();
        assertThat(count).isEqualTo(19);
        mvc.perform(get("/api/v1/me/taste-reports").header(HttpHeaders.AUTHORIZATION, OWNER_AUTH))
                .andExpect(status().isOk());
        mvc.perform(owner(get("/api/v1/me/taste-reports").header("Origin", "http://127.0.0.1:5173")))
                .andExpect(status().isOk());
        mvc.perform(owner(get("/api/v1/me/taste-reports").header("Origin", "https://public.example")))
                .andExpect(status().isNotFound()).andExpect(jsonPath("$.code").value("RESOURCE_NOT_FOUND"))
                .andExpect(header().string(HttpHeaders.CACHE_CONTROL, "no-store, private"))
                .andExpect(header().string("Referrer-Policy", "no-referrer"));
        mvc.perform(get("/api/v1/me/taste-reports"))
                .andExpect(status().isUnauthorized()).andExpect(jsonPath("$.code").value("UNAUTHORIZED"))
                .andExpect(header().string(HttpHeaders.CACHE_CONTROL, "no-store, private"))
                .andExpect(header().string("Referrer-Policy", "no-referrer"));
        mvc.perform(get("/api/v1/me/taste-reports").header(HttpHeaders.AUTHORIZATION, OWNER_AUTH)
                        .with(request -> { request.setRemoteAddr("203.0.113.7"); return request; }))
                .andExpect(status().isNotFound()).andExpect(jsonPath("$.code").value("RESOURCE_NOT_FOUND"));
        C5LoopbackGuard unsafe = new C5LoopbackGuard("0.0.0.0");
        assertThatThrownBy(unsafe::validateBind).isInstanceOf(IllegalStateException.class);
    }

    @Test
    void reportIsKstHalfYearImmutableRevisionedOwnerScopedAndCursorBound() throws Exception {
        JsonNode first = createReport("c5-report-create-0001");
        assertThat(first.path("status").asText()).isEqualTo("READY");
        assertThat(first.path("metrics").path("viewingCount").asInt()).isEqualTo(3);
        assertThat(first.path("metrics").path("ratedCount").asInt()).isEqualTo(3);
        assertThat(first.path("metrics").path("averageRating").decimalValue()).isEqualByComparingTo("3.33");
        assertForbidden(first.toString());
        UUID firstId = UUID.fromString(first.path("reportId").asText());
        Transactional boundary = C5LocalService.class.getMethod(
                "createReport", UUID.class, String.class, CreateTasteReportRequest.class)
                .getAnnotation(Transactional.class);
        assertThat(boundary).isNotNull();
        assertThat(boundary.isolation()).isEqualTo(Isolation.REPEATABLE_READ);
        Instant sourceWatermark = jdbc.queryForObject(
                "SELECT source_watermark FROM c5_taste_report_revision WHERE report_id=?",
                (rs, rowNum) -> rs.getTimestamp(1).toInstant(), firstId);
        Instant latestIncluded = jdbc.queryForObject(
                "SELECT max(watched_at) FROM c5_taste_report_period_item WHERE report_id=?",
                (rs, rowNum) -> rs.getTimestamp(1).toInstant(), firstId);
        assertThat(latestIncluded).isBeforeOrEqualTo(sourceWatermark);

        String replay = mvc.perform(owner(post("/api/v1/me/taste-reports"))
                        .header("Idempotency-Key", "c5-report-create-0001")
                        .contentType("application/json").content("{\"periodStart\":\"2026-01-01\"}"))
                .andExpect(status().isCreated()).andReturn().getResponse().getContentAsString();
        assertThat(objectMapper.readTree(replay)).isEqualTo(first);
        mvc.perform(owner(post("/api/v1/me/taste-reports"))
                        .header("Idempotency-Key", "c5-report-create-0001")
                        .contentType("application/json").content("{\"periodStart\":\"2025-07-01\"}"))
                .andExpect(status().isConflict()).andExpect(jsonPath("$.code").value("IDEMPOTENCY_KEY_REUSED"));

        jdbc.update("UPDATE rating SET value=5,revision=revision+1,updated_at=? WHERE id=?", ts("2026-08-29T11:00:00Z"), RATING);
        JsonNode second = createReport("c5-report-create-0002");
        UUID secondId = UUID.fromString(second.path("reportId").asText());
        assertThat(second.path("revision").asInt()).isEqualTo(2);
        assertThat(second.path("periodItems").path("items").get(0).path("rating").asInt()).isEqualTo(5);
        mvc.perform(owner(get("/api/v1/me/taste-reports/{id}", firstId)))
                .andExpect(status().isOk()).andExpect(jsonPath("$.status").value("SUPERSEDED"))
                .andExpect(jsonPath("$.periodItems.items[0].rating").value(4));
        mvc.perform(other(get("/api/v1/me/taste-reports/{id}", secondId)))
                .andExpect(status().isNotFound()).andExpect(jsonPath("$.code").value("RESOURCE_NOT_FOUND"));

        JsonNode page = read(owner(get("/api/v1/me/taste-reports").param("limit", "1")), 200);
        assertThat(page.path("totalCount").asInt()).isEqualTo(2);
        String cursor = page.path("nextCursor").asText();
        read(owner(get("/api/v1/me/taste-reports").param("limit", "1").param("cursor", cursor)), 200);
        mvc.perform(owner(get("/api/v1/me/taste-reports").param("cursor", cursor + "x")))
                .andExpect(status().isBadRequest()).andExpect(jsonPath("$.code").value("INVALID_CURSOR"));
        mvc.perform(owner(post("/api/v1/me/taste-reports"))
                        .header("Idempotency-Key", "c5-report-future-0001")
                        .contentType("application/json").content("{\"periodStart\":\"2026-07-01\"}"))
                .andExpect(status().isBadRequest()).andExpect(jsonPath("$.code").value("VALIDATION_ERROR"));
        assertThatThrownBy(() -> jdbc.update("UPDATE c5_taste_report_revision SET rating_sum=1 WHERE report_id=?", firstId))
                .isInstanceOf(RuntimeException.class);
        assertThatThrownBy(() -> jdbc.update(
                "DELETE FROM c5_taste_report_period_item WHERE report_id=? AND position=1", firstId))
                .isInstanceOf(RuntimeException.class);
        assertThatThrownBy(() -> jdbc.update("DELETE FROM c5_taste_report_revision WHERE report_id=?", firstId))
                .isInstanceOf(RuntimeException.class);
    }

    @Test
    void pdfIsDeterministicTextLayerOwnerOnlyHashCheckedAndCleanedAfterTwentyFourHours() throws Exception {
        UUID reportId = UUID.fromString(createReport("c5-pdf-report-0001").path("reportId").asText());
        JsonNode export = read(owner(post("/api/v1/me/taste-reports/{id}/exports", reportId)
                .header("Idempotency-Key", "c5-export-create-001")), 202);
        UUID exportId = UUID.fromString(export.path("exportId").asText());
        assertThat(export.path("status").asText()).isEqualTo("READY");
        mvc.perform(other(get("/api/v1/me/report-exports/{id}", exportId)))
                .andExpect(status().isNotFound());
        byte[] pdf = mvc.perform(owner(get("/api/v1/me/report-exports/{id}/content", exportId)))
                .andExpect(status().isOk()).andExpect(content().contentType("application/pdf"))
                .andExpect(header().string(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename=feelm-report.pdf"))
                .andReturn().getResponse().getContentAsByteArray();
        assertThat(new String(pdf, 0, 8, StandardCharsets.US_ASCII)).startsWith("%PDF-1.7");
        assertThat(new String(pdf, StandardCharsets.US_ASCII)).contains("FEELM Factual Half-Year Report");
        String koreanActualText = HexFormat.of().withUpperCase()
                .formatHex("인사이드 맨".getBytes(StandardCharsets.UTF_16BE));
        assertThat(new String(pdf, StandardCharsets.US_ASCII)).contains(koreanActualText);
        String path = jdbc.queryForObject("SELECT opaque_path FROM c5_report_export_artifact WHERE export_id=?", String.class, exportId);
        String storedHash = jdbc.queryForObject("SELECT content_sha256 FROM c5_report_export_artifact WHERE export_id=?", String.class, exportId);
        assertThat(path).doesNotContain(OWNER.toString()).doesNotContain("example");
        assertThat(storedHash).hasSize(64);
        assertThat(Files.exists(Path.of(path))).isTrue();
        assertThat(jdbc.queryForObject("""
                SELECT (a.expires_at=j.expires_at) FROM c5_report_export_artifact a
                JOIN c5_report_export_job j ON j.export_id=a.export_id WHERE a.export_id=?
                """, Boolean.class, exportId)).isTrue();
        assertThatThrownBy(() -> jdbc.update("""
                UPDATE c5_report_export_artifact SET expires_at=expires_at-interval '1 hour' WHERE export_id=?
                """, exportId)).isInstanceOf(RuntimeException.class);

        Timestamp expiredAt = jdbc.queryForObject("SELECT clock_timestamp()-interval '1 second'", Timestamp.class);
        jdbc.update("""
                WITH changed_job AS (
                  UPDATE c5_report_export_job SET expires_at=? WHERE export_id=? RETURNING export_id
                )
                UPDATE c5_report_export_artifact a SET expires_at=?
                  FROM changed_job j WHERE a.export_id=j.export_id
                """, expiredAt, exportId, expiredAt);
        service.cleanupExpiredArtifacts();
        assertThat(Files.exists(Path.of(path))).isFalse();
        mvc.perform(owner(get("/api/v1/me/report-exports/{id}", exportId)))
                .andExpect(status().isOk()).andExpect(jsonPath("$.status").value("EXPIRED"));
        mvc.perform(owner(get("/api/v1/me/report-exports/{id}/content", exportId)))
                .andExpect(status().isConflict()).andExpect(jsonPath("$.code").value("EXPORT_EXPIRED"));
    }

    @Test
    void databaseRejectsInvalidRatingAggregatesAndCrossOwnerExportOrShare() throws Exception {
        UUID reportId = UUID.fromString(createReport("c5-db-invariant-report").path("reportId").asText());
        assertThatThrownBy(() -> jdbc.update("""
                INSERT INTO c5_taste_report_revision(
                  report_id,owner_user_id,period_start,period_end,revision,status,source_watermark,
                  viewing_count,rated_count,rating_sum,created_at)
                VALUES (?,?,DATE '2025-01-01',DATE '2025-06-30',99,'READY',clock_timestamp(),1,2,2,clock_timestamp())
                """, UUID.randomUUID(), OWNER)).isInstanceOf(RuntimeException.class);
        assertThatThrownBy(() -> jdbc.update("""
                INSERT INTO c5_taste_report_revision(
                  report_id,owner_user_id,period_start,period_end,revision,status,source_watermark,
                  viewing_count,rated_count,rating_sum,created_at)
                VALUES (?,?,DATE '2025-01-01',DATE '2025-06-30',100,'READY',clock_timestamp(),1,1,0,clock_timestamp())
                """, UUID.randomUUID(), OWNER)).isInstanceOf(RuntimeException.class);

        assertThatThrownBy(() -> jdbc.update("""
                INSERT INTO c5_report_export_job(
                  export_id,owner_user_id,report_id,status,attempts,created_at,updated_at,expires_at)
                VALUES (?,?,?,'PENDING',0,clock_timestamp(),clock_timestamp(),clock_timestamp()+interval '1 hour')
                """, UUID.randomUUID(), OTHER, reportId)).isInstanceOf(RuntimeException.class);
        assertThatThrownBy(() -> jdbc.update("""
                INSERT INTO c5_report_share_grant(
                  share_id,owner_user_id,report_id,token_sha256,status,created_at,expires_at)
                VALUES (?,?,?,?,'ACTIVE',clock_timestamp(),clock_timestamp()+interval '1 day')
                """, UUID.randomUUID(), OTHER, reportId, "a".repeat(64))).isInstanceOf(RuntimeException.class);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM c5_report_export_job", Integer.class)).isZero();
        assertThat(jdbc.queryForObject("SELECT count(*) FROM c5_report_share_grant", Integer.class)).isZero();
    }

    @Test
    void deferredSnapshotAggregateConstraintRejectsMissingExtraMismatchedAndGappedItemsAtCommit() {
        TransactionTemplate transaction = new TransactionTemplate(transactionManager);

        UUID missing = UUID.randomUUID();
        assertThatThrownBy(() -> transaction.executeWithoutResult(status ->
                insertSnapshotSummary(missing, 1, 0, 0, "READY")))
                .isInstanceOf(RuntimeException.class)
                .hasStackTraceContaining("C5 report snapshot aggregate mismatch");

        UUID extra = UUID.randomUUID();
        assertThatThrownBy(() -> transaction.executeWithoutResult(status -> {
            insertSnapshotSummary(extra, 1, 1, 4, "READY");
            insertSnapshotItem(extra, 1, MOVIE, UUID.randomUUID(), 4);
            insertSnapshotItem(extra, 2, MOVIE_TWO, UUID.randomUUID(), null);
        })).isInstanceOf(RuntimeException.class)
                .hasStackTraceContaining("C5 report snapshot aggregate mismatch");

        UUID sumMismatch = UUID.randomUUID();
        assertThatThrownBy(() -> transaction.executeWithoutResult(status -> {
            insertSnapshotSummary(sumMismatch, 2, 2, 10, "READY");
            insertSnapshotItem(sumMismatch, 1, MOVIE, UUID.randomUUID(), 4);
            insertSnapshotItem(sumMismatch, 2, MOVIE_TWO, UUID.randomUUID(), 3);
        })).isInstanceOf(RuntimeException.class)
                .hasStackTraceContaining("C5 report snapshot aggregate mismatch");

        UUID gap = UUID.randomUUID();
        assertThatThrownBy(() -> transaction.executeWithoutResult(status -> {
            insertSnapshotSummary(gap, 2, 2, 7, "READY");
            insertSnapshotItem(gap, 1, MOVIE, UUID.randomUUID(), 4);
            insertSnapshotItem(gap, 3, MOVIE_TWO, UUID.randomUUID(), 3);
        })).isInstanceOf(RuntimeException.class)
                .hasStackTraceContaining("C5 report snapshot aggregate mismatch");

        assertThat(jdbc.queryForObject("""
                SELECT count(*) FROM c5_taste_report_revision WHERE report_id IN (?,?,?,?)
                """, Integer.class, missing, extra, sumMismatch, gap)).isZero();
    }

    @Test
    void privateDefaultsAndExactCapabilitiesExposeEveryActiveFilmAndPopcornWithScopedCursors() throws Exception {
        JsonNode defaults = read(owner(get("/api/v1/me/privacy-settings")), 200);
        assertThat(defaults.path("revision").asLong()).isEqualTo(1);
        assertThat(defaults.path("resources").size()).isEqualTo(3);
        assertThat(defaults.path("resources").findValuesAsText("visibility")).containsOnly("PRIVATE");
        UUID publicId = UUID.fromString(defaults.path("publicProfileId").asText());
        mvc.perform(get("/api/v1/public/profiles/{id}", publicId)).andExpect(status().isNotFound());

        String allPublic = """
                {"expectedRevision":1,"resources":[
                  {"resource":"PROFILE","visibility":"PUBLIC"},
                  {"resource":"FILM","visibility":"PUBLIC"},
                  {"resource":"POPCORN","visibility":"PUBLIC"}]}
                """;
        JsonNode replaced = read(owner(put("/api/v1/me/privacy-settings")
                .header("Idempotency-Key", "gggggggggggggggg")
                .contentType("application/json").content(allPublic)), 200);
        assertThat(replaced.path("revision").asLong()).isEqualTo(2);
        mvc.perform(get("/api/v1/public/profiles/{id}", publicId))
                .andExpect(status().isOk()).andExpect(jsonPath("$.nickname").value("c5_owner"));

        JsonNode film1 = read(get("/api/v1/public/profiles/{id}/film", publicId).param("limit", "1"), 200);
        assertThat(film1.path("totalCount").asInt()).isEqualTo(2);
        String filmCursor = film1.path("nextCursor").asText();
        JsonNode film2 = read(get("/api/v1/public/profiles/{id}/film", publicId)
                .param("limit", "1").param("cursor", filmCursor), 200);
        assertThat(film1.path("items").get(0).path("frameId").asText())
                .isNotEqualTo(film2.path("items").get(0).path("frameId").asText());
        JsonNode popcorn = read(get("/api/v1/public/profiles/{id}/popcorns", publicId).param("limit", "1"), 200);
        assertThat(popcorn.path("totalCount").asInt()).isEqualTo(2);
        mvc.perform(get("/api/v1/public/profiles/{id}/popcorns", publicId).param("cursor", filmCursor))
                .andExpect(status().isBadRequest()).andExpect(jsonPath("$.code").value("INVALID_CURSOR"));

        String profileOnly = """
                {"expectedRevision":2,"resources":[
                  {"resource":"PROFILE","visibility":"PUBLIC"},
                  {"resource":"FILM","visibility":"PRIVATE"},
                  {"resource":"POPCORN","visibility":"PRIVATE"}]}
                """;
        read(owner(put("/api/v1/me/privacy-settings").header("Idempotency-Key", "hhhhhhhhhhhhhhhh")
                .contentType("application/json").content(profileOnly)), 200);
        mvc.perform(get("/api/v1/public/profiles/{id}/film", publicId))
                .andExpect(status().isNotFound()).andExpect(jsonPath("$.code").value("RESOURCE_NOT_FOUND"));
        mvc.perform(owner(put("/api/v1/me/privacy-settings").header("Idempotency-Key", "iiiiiiiiiiiiiiii")
                        .contentType("application/json").content(allPublic)))
                .andExpect(status().isConflict()).andExpect(jsonPath("$.code").value("REVISION_CONFLICT"));
        assertForbidden(film1.toString() + popcorn);
    }

    @Test
    void shareStoresOnlyHashesUsesReportOnlyViewerAndRevokeImmediatelyFailsClosed() throws Exception {
        UUID reportId = UUID.fromString(createReport("c5-share-report-001").path("reportId").asText());
        JsonNode share = read(owner(post("/api/v1/me/taste-reports/{id}/shares", reportId)
                .header("Idempotency-Key", "c5-share-create-0001")), 201);
        UUID shareId = UUID.fromString(share.path("shareId").asText());
        String raw = share.path("rawToken").asText();
        assertThat(raw).hasSize(43);
        assertThat(share.path("shareHref").asText()).isEqualTo("/shared-report#token=" + raw);
        Instant grantCreated = jdbc.queryForObject("SELECT created_at FROM c5_report_share_grant WHERE share_id=?",
                (rs, rowNum) -> rs.getTimestamp(1).toInstant(), shareId);
        Instant grantExpires = Instant.parse(share.path("expiresAt").asText());
        assertThat(grantExpires).isEqualTo(grantCreated.atZone(ZoneId.of("Asia/Seoul")).plusMonths(1).toInstant());
        String stored = jdbc.queryForObject("SELECT token_sha256 FROM c5_report_share_grant WHERE share_id=?", String.class, shareId);
        assertThat(stored).hasSize(64).isNotEqualTo(raw);
        assertThat(jdbc.queryForObject("SELECT response_body::text FROM c5_idempotency_result WHERE operation='createMyTasteReportShare'", String.class))
                .doesNotContain(raw);
        mvc.perform(owner(post("/api/v1/me/taste-reports/{id}/shares", reportId)
                        .header("Idempotency-Key", "c5-share-create-0001")))
                .andExpect(status().isConflict()).andExpect(jsonPath("$.code").value("SHARE_UNAVAILABLE"));

        JsonNode viewer = read(post("/api/v1/public/report-shares/exchange")
                .contentType("application/json").content("{\"rawToken\":\"" + raw + "\"}"), 200);
        String session = viewer.path("viewerSessionToken").asText();
        assertThat(session).hasSize(43);
        assertThat(jdbc.queryForObject("SELECT session_sha256 FROM c5_report_share_viewer_session", String.class))
                .isNotEqualTo(session);
        Long viewerTtl = jdbc.queryForObject("""
                SELECT extract(epoch FROM (expires_at-created_at))::bigint FROM c5_report_share_viewer_session
                """, Long.class);
        assertThat(viewerTtl).isEqualTo(900L);
        JsonNode shared = objectMapper.readTree(mvc.perform(get("/api/v1/public/shared-report")
                        .header("X-Report-Viewer-Session", session))
                .andExpect(status().isOk()).andExpect(header().string("Cache-Control",
                        org.hamcrest.Matchers.containsString("no-store")))
                .andExpect(header().string("Referrer-Policy", "no-referrer"))
                .andReturn().getResponse().getContentAsString());
        assertThat(shared.path("report").path("reportId").asText()).isEqualTo(reportId.toString());
        assertThat(shared.path("ownerNickname").asText()).isEqualTo("c5_owner");
        assertForbidden(shared.toString());

        mvc.perform(other(post("/api/v1/me/report-shares/{id}/revoke", shareId)
                        .header("Idempotency-Key", "c5-share-revoke-other")))
                .andExpect(status().isNotFound());
        mvc.perform(owner(post("/api/v1/me/report-shares/{id}/revoke", shareId)
                        .header("Idempotency-Key", "c5-share-revoke-0001")))
                .andExpect(status().isNoContent());
        mvc.perform(get("/api/v1/public/shared-report").header("X-Report-Viewer-Session", session))
                .andExpect(status().isNotFound());
        mvc.perform(post("/api/v1/public/report-shares/exchange")
                        .contentType("application/json").content("{\"rawToken\":\"" + raw + "\"}"))
                .andExpect(status().isNotFound());
    }

    @Test
    void providerlessNotificationIsOffByDefaultDedupesDueSourcesAndSupportsReadDismiss() throws Exception {
        mvc.perform(owner(get("/api/v1/me/notification-settings")))
                .andExpect(status().isOk()).andExpect(jsonPath("$.watchConfirmationDueEnabled").value(false))
                .andExpect(jsonPath("$.revision").value(1));
        mvc.perform(owner(get("/api/v1/me/notifications")))
                .andExpect(status().isOk()).andExpect(jsonPath("$.totalCount").value(0));
        assertThat(jdbc.queryForObject("SELECT count(*) FROM c5_in_app_notification", Integer.class)).isZero();

        String enable = "{\"watchConfirmationDueEnabled\":true,\"expectedRevision\":1}";
        read(owner(put("/api/v1/me/notification-settings")
                .header("Idempotency-Key", "c5-notify-setting-001")
                .contentType("application/json").content(enable)), 200);
        JsonNode page = read(owner(get("/api/v1/me/notifications").param("limit", "1")), 200);
        assertThat(page.path("totalCount").asInt()).isEqualTo(2);
        UUID notificationId = UUID.fromString(page.path("items").get(0).path("notificationId").asText());
        String cursor = page.path("nextCursor").asText();
        read(owner(get("/api/v1/me/notifications").param("limit", "1").param("cursor", cursor)), 200);
        mvc.perform(other(put("/api/v1/me/notifications/{id}/state", notificationId)
                        .header("Idempotency-Key", "jjjjjjjjjjjjjjjj")
                        .contentType("application/json").content("{\"state\":\"READ\"}")))
                .andExpect(status().isNotFound());
        mvc.perform(owner(put("/api/v1/me/notifications/{id}/state", notificationId)
                        .header("Idempotency-Key", "c5-notify-read-0001")
                        .contentType("application/json").content("{\"state\":\"READ\"}")))
                .andExpect(status().isOk()).andExpect(jsonPath("$.state").value("READ"));
        mvc.perform(owner(put("/api/v1/me/notifications/{id}/state", notificationId)
                        .header("Idempotency-Key", "kkkkkkkkkkkkkkkk")
                        .contentType("application/json").content("{\"state\":\"DISMISSED\"}")))
                .andExpect(status().isOk()).andExpect(jsonPath("$.state").value("DISMISSED"));

        mvc.perform(owner(put("/api/v1/me/notification-settings")
                        .header("Idempotency-Key", "c5-notify-setting-002")
                        .contentType("application/json")
                        .content("{\"watchConfirmationDueEnabled\":false,\"expectedRevision\":2}")))
                .andExpect(status().isOk()).andExpect(jsonPath("$.revision").value(3));
        assertThat(jdbc.queryForObject("SELECT count(*) FROM c5_in_app_notification", Integer.class)).isZero();
        mvc.perform(owner(put("/api/v1/me/notification-settings")
                        .header("Idempotency-Key", "c5-notify-setting-002")
                        .contentType("application/json").content(enable)))
                .andExpect(status().isConflict()).andExpect(jsonPath("$.code").value("IDEMPOTENCY_KEY_REUSED"));
    }

    private JsonNode createReport(String idempotencyKey) throws Exception {
        return read(owner(post("/api/v1/me/taste-reports"))
                .header("Idempotency-Key", idempotencyKey)
                .contentType("application/json").content("{\"periodStart\":\"2026-01-01\"}"), 201);
    }

    private JsonNode read(MockHttpServletRequestBuilder request, int expectedStatus) throws Exception {
        return objectMapper.readTree(mvc.perform(request).andExpect(status().is(expectedStatus))
                .andReturn().getResponse().getContentAsString());
    }

    private static MockHttpServletRequestBuilder owner(MockHttpServletRequestBuilder request) {
        return request.header(HttpHeaders.AUTHORIZATION, OWNER_AUTH).with(value -> {
            value.setRemoteAddr("127.0.0.1");
            return value;
        });
    }

    private static MockHttpServletRequestBuilder other(MockHttpServletRequestBuilder request) {
        return request.header(HttpHeaders.AUTHORIZATION, OTHER_AUTH).with(value -> {
            value.setRemoteAddr("127.0.0.1");
            return value;
        });
    }

    private void insertReportOnlyRating(
            UUID watchId,
            UUID viewingId,
            UUID ratingId,
            UUID movieId,
            int ratingValue,
            String watchedAt
    ) {
        Instant watched = Instant.parse(watchedAt);
        jdbc.update("""
                INSERT INTO watch_intent(
                  id,user_id,movie_id,provider_id,status,clicked_at,confirmation_due_at,expires_at,responded_at,revision)
                VALUES (?,?,?,?,'CONFIRMED_WATCHED',?,?,?,?,2)
                """, watchId, OWNER, movieId, PROVIDER, Timestamp.from(watched.minus(3, java.time.temporal.ChronoUnit.DAYS)),
                Timestamp.from(watched.minus(1, java.time.temporal.ChronoUnit.DAYS)),
                Timestamp.from(watched.plus(4, java.time.temporal.ChronoUnit.DAYS)), Timestamp.from(watched));
        jdbc.update("""
                INSERT INTO viewing_record(
                  id,user_id,movie_id,source_watch_intent_id,provider_id,status,watched_confirmed_at,revision)
                VALUES (?,?,?,?,?,'RATED_COMPLETED',?,1)
                """, viewingId, OWNER, movieId, watchId, PROVIDER, Timestamp.from(watched));
        jdbc.update("""
                INSERT INTO rating(
                  id,user_id,movie_id,viewing_record_id,value,logical_status,revision,created_at,updated_at)
                VALUES (?,?,?,?,?,'ACTIVE',1,?,?)
                """, ratingId, OWNER, movieId, viewingId, ratingValue,
                Timestamp.from(watched.plusSeconds(60)), Timestamp.from(watched.plusSeconds(60)));
    }

    private void insertSnapshotSummary(
            UUID reportId,
            int viewingCount,
            int ratedCount,
            int ratingSum,
            String status
    ) {
        jdbc.update("""
                INSERT INTO c5_taste_report_revision(
                  report_id,owner_user_id,period_start,period_end,revision,status,source_watermark,
                  viewing_count,rated_count,rating_sum,created_at)
                VALUES (?,?,DATE '2024-01-01',DATE '2024-06-30',1,?,clock_timestamp(),?,?,?,clock_timestamp())
                """, reportId, OWNER, status, viewingCount, ratedCount, ratingSum);
    }

    private void insertSnapshotItem(
            UUID reportId,
            int position,
            UUID movieId,
            UUID viewingId,
            Integer ratingValue
    ) {
        UUID ratingId = ratingValue == null ? null : UUID.randomUUID();
        jdbc.update("""
                INSERT INTO c5_taste_report_period_item(
                  report_id,position,movie_id,viewing_record_id,viewing_revision,
                  rating_id,rating_revision,rating_value,display_title,watched_at)
                VALUES (?,?,?,?,1,?,?,?,'snapshot invariant fixture',TIMESTAMPTZ '2024-06-01T00:00:00Z')
                """, reportId, position, movieId, viewingId, ratingId,
                ratingValue == null ? null : 1, ratingValue);
    }

    private void upsertActor(UUID id, String nickname) {
        jdbc.update("""
                INSERT INTO c4_user_account(user_id,membership_status,created_at,activated_at,pending_purge_at)
                VALUES (?,'ACTIVE',?,?,?) ON CONFLICT(user_id) DO UPDATE SET membership_status='ACTIVE',activated_at=excluded.activated_at
                """, id, ts("2026-01-01T00:00:00Z"), ts("2026-01-01T00:00:00Z"), ts("2030-01-01T00:00:00Z"));
        jdbc.update("""
                INSERT INTO c4_user_profile(user_id,nickname,nickname_normalized,normalization_version,revision,nickname_changed_at)
                VALUES (?,?,?,'c5-test-v1',1,?) ON CONFLICT(user_id) DO UPDATE SET nickname=excluded.nickname,nickname_normalized=excluded.nickname_normalized
                """, id, nickname, nickname, ts("2026-01-01T00:00:00Z"));
    }

    private static Timestamp ts(String value) {
        return Timestamp.from(Instant.parse(value));
    }

    private static void assertForbidden(String value) {
        assertThat(value.toLowerCase())
                .doesNotContain("expectedstar", "satisfaction", "tastediagnosis", "tastecomparison", "fairness");
    }

    @TestConfiguration
    static class DynamicAccessTokenConfiguration {
        @Bean
        C4AccessTokenVerifier c5TestVerifier() {
            return token -> {
                if ("c5-dynamic-owner-access".equals(token)) {
                    return Optional.of(new CatalogUserContext(true, OWNER, Set.of()));
                }
                if ("c5-dynamic-other-access".equals(token)) {
                    return Optional.of(new CatalogUserContext(true, OTHER, Set.of()));
                }
                return Optional.empty();
            };
        }
    }
}
