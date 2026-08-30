package com.feelm.catalog.importer;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.feelm.catalog.importer.CatalogArtifactV1Validator.ArtifactHeader;
import com.feelm.catalog.importer.CatalogArtifactV1Validator.ValidatedRecord;
import org.springframework.context.annotation.Profile;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.DigestInputStream;
import java.security.MessageDigest;
import java.time.Clock;
import java.time.Instant;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.HashMap;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

@Service
@Profile("postgres")
public class CatalogImportService {
    private static final int MAX_JSONL_LINE_CHARS = 4 * 1024 * 1024;

    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;
    private final CatalogArtifactV1Validator validator;
    private final Clock clock;
    private final PlatformTransactionManager transactionManager;

    public CatalogImportService(
            JdbcTemplate jdbc,
            ObjectMapper objectMapper,
            CatalogArtifactV1Validator validator,
            Clock clock,
            PlatformTransactionManager transactionManager
    ) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
        this.validator = validator;
        this.clock = clock;
        this.transactionManager = transactionManager;
    }

    public CatalogImportResult importArtifact(Path artifact) {
        validatePath(artifact);
        String sourceHash = sha256(artifact);
        ArtifactHeader header = readHeader(artifact);
        ExistingVersion existing = findVersion(header.catalogVersion());
        if (existing != null) {
            if (sourceHash.equals(existing.sourceHash())) {
                if (!Set.of("ACTIVE", "RETIRED").contains(existing.status())) {
                    throw new CatalogImportException(
                            "INVALID_EXISTING_VERSION_STATE",
                            "matching artifact exists in a non-published state"
                    );
                }
                return new CatalogImportResult(
                        CatalogImportResult.Status.ALREADY_IMPORTED,
                        header.catalogVersion(),
                        sourceHash,
                        Map.of()
                );
            }
            throw new CatalogImportException(
                    "CATALOG_VERSION_CONFLICT",
                    "catalogVersion already exists with a different source hash"
            );
        }

        UUID syncRunId = UUID.randomUUID();
        UUID catalogVersionId = UUID.randomUUID();
        Instant startedAt = clock.instant();
        TransactionTemplate transaction = new TransactionTemplate(transactionManager);
        try {
            CatalogImportResult result = transaction.execute(status -> importInTransaction(
                    artifact, header, sourceHash, syncRunId, catalogVersionId, startedAt
            ));
            if (result == null) {
                throw new CatalogImportException("IMPORT_TRANSACTION_FAILED", "catalog import transaction returned no result");
            }
            return result;
        } catch (RuntimeException exception) {
            String failureCode = failureCode(exception);
            recordFailedRun(header, sourceHash, startedAt, failureCode, exception);
            if (exception instanceof CatalogImportException importException) {
                throw importException;
            }
            throw new CatalogImportException(
                    failureCode,
                    "catalog import database operation failed",
                    0,
                    exception
            );
        }
    }

    private CatalogImportResult importInTransaction(
            Path artifact,
            ArtifactHeader header,
            String sourceHash,
            UUID syncRunId,
            UUID catalogVersionId,
            Instant startedAt
    ) {
        insertRun(syncRunId, header, startedAt);
        insertStagingVersion(catalogVersionId, syncRunId, header.catalogVersion(), sourceHash);

        ImportContext context = new ImportContext(catalogVersionId);
        streamRecords(artifact, (record, lineNumber) -> insertRecord(context, record));
        deriveFlavorAssignments(catalogVersionId);
        buildSearchDocuments(catalogVersionId);
        runQualityGates(catalogVersionId);
        publish(catalogVersionId, syncRunId, context, startedAt);

        return new CatalogImportResult(
                CatalogImportResult.Status.PUBLISHED,
                header.catalogVersion(),
                sourceHash,
                context.countsAsMap()
        );
    }

    private void recordFailedRun(
            ArtifactHeader header,
            String sourceHash,
            Instant startedAt,
            String failureCode,
            RuntimeException original
    ) {
        try {
            TransactionTemplate auditTransaction = new TransactionTemplate(transactionManager);
            auditTransaction.executeWithoutResult(status -> jdbc.update("""
                    INSERT INTO catalog_sync_run (
                        id, job_type, status, started_at, finished_at, source_version, metrics, failure_summary
                    ) VALUES (?, 'CATALOG_ARTIFACT_IMPORT', 'FAILED', ?, ?, ?, ?::jsonb, ?)
                    """,
                    UUID.randomUUID(),
                    timestamp(startedAt),
                    timestamp(clock.instant()),
                    header.catalogVersion(),
                    "{\"sourceHash\":\"" + sourceHash + "\"}",
                    failureCode
            ));
        } catch (RuntimeException auditFailure) {
            original.addSuppressed(auditFailure);
        }
    }

    private String failureCode(RuntimeException exception) {
        return exception instanceof CatalogImportException importException
                ? importException.code()
                : "DATABASE_IMPORT_FAILED";
    }

    private void validatePath(Path artifact) {
        if (artifact == null || !Files.isRegularFile(artifact) || !Files.isReadable(artifact)) {
            throw new CatalogImportException("ARTIFACT_NOT_READABLE", "artifact must be a readable regular file");
        }
    }

    private ArtifactHeader readHeader(Path artifact) {
        try (BufferedReader reader = utf8Reader(artifact)) {
            String line = reader.readLine();
            if (line == null || line.isBlank()) {
                throw new CatalogImportException("INVALID_HEADER", "artifact header is missing", 1);
            }
            return validator.validateHeader(parse(line, 1), 1);
        } catch (CatalogImportException exception) {
            throw exception;
        } catch (IOException exception) {
            throw new CatalogImportException("ARTIFACT_READ_FAILED", "artifact header could not be read", 1, exception);
        }
    }

    private void streamRecords(Path artifact, RecordConsumer consumer) {
        try (BufferedReader reader = utf8Reader(artifact)) {
            String line;
            long lineNumber = 0;
            while ((line = reader.readLine()) != null) {
                lineNumber++;
                if (lineNumber == 1) {
                    continue;
                }
                if (line.isBlank()) {
                    throw new CatalogImportException("INVALID_RECORD", "blank JSONL records are not allowed", lineNumber);
                }
                if (line.length() > MAX_JSONL_LINE_CHARS) {
                    throw new CatalogImportException("RECORD_TOO_LARGE", "JSONL record exceeds the size limit", lineNumber);
                }
                consumer.accept(validator.validateRecord(parse(line, lineNumber), lineNumber), lineNumber);
            }
            if (lineNumber == 1) {
                throw new CatalogImportException("EMPTY_ARTIFACT", "artifact has no data records", 1);
            }
        } catch (CatalogImportException exception) {
            throw exception;
        } catch (IOException exception) {
            throw new CatalogImportException("ARTIFACT_READ_FAILED", "artifact could not be streamed", 0, exception);
        }
    }

    private BufferedReader utf8Reader(Path artifact) throws IOException {
        return new BufferedReader(new InputStreamReader(
                Files.newInputStream(artifact),
                StandardCharsets.UTF_8.newDecoder()
                        .onMalformedInput(CodingErrorAction.REPORT)
                        .onUnmappableCharacter(CodingErrorAction.REPORT)
        ));
    }

    private JsonNode parse(String line, long lineNumber) {
        try {
            return objectMapper.readTree(line);
        } catch (JsonProcessingException exception) {
            throw new CatalogImportException("INVALID_JSON", "JSONL record is not valid JSON", lineNumber, exception);
        }
    }

    private String sha256(Path artifact) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            try (InputStream input = Files.newInputStream(artifact); DigestInputStream stream = new DigestInputStream(input, digest)) {
                stream.transferTo(java.io.OutputStream.nullOutputStream());
            }
            return HexFormat.of().formatHex(digest.digest());
        } catch (Exception exception) {
            throw new CatalogImportException("ARTIFACT_HASH_FAILED", "artifact SHA-256 could not be calculated", 0, exception);
        }
    }

    private ExistingVersion findVersion(String publicVersion) {
        List<ExistingVersion> rows = jdbc.query(
                "SELECT source_hash, status FROM catalog_version WHERE public_version = ?",
                (resultSet, rowNumber) -> new ExistingVersion(resultSet.getString("source_hash"), resultSet.getString("status")),
                publicVersion
        );
        return rows.isEmpty() ? null : rows.get(0);
    }

    private void insertRun(UUID runId, ArtifactHeader header, Instant startedAt) {
        String metrics;
        try {
            metrics = objectMapper.writeValueAsString(Map.of(
                    "schemaVersion", 1,
                    "movielensArchiveSha256", header.movielensArchiveSha256(),
                    "sources", header.sources()
            ));
        } catch (JsonProcessingException exception) {
            throw new CatalogImportException("INVALID_HEADER", "header provenance could not be normalized", 1, exception);
        }
        jdbc.update("""
                INSERT INTO catalog_sync_run (
                    id, job_type, status, started_at, source_version, metrics
                ) VALUES (?, 'CATALOG_ARTIFACT_IMPORT', 'RUNNING', ?, ?, ?::jsonb)
                """, runId, timestamp(startedAt), header.catalogVersion(), metrics);
    }

    private void insertStagingVersion(UUID versionId, UUID runId, String publicVersion, String sourceHash) {
        jdbc.update("""
                INSERT INTO catalog_version (id, public_version, sync_run_id, status, source_hash)
                VALUES (?, ?, ?, 'STAGING', ?)
                """, versionId, publicVersion, runId, sourceHash);
    }

    private void insertRecord(ImportContext context, ValidatedRecord record) {
        JsonNode payload = record.payload();
        switch (record.recordType()) {
            case "movieIdentity" -> insertMovieIdentity(payload);
            case "movieProjection" -> insertProjection(context.catalogVersionId, payload);
            case "localization" -> insertLocalization(context.catalogVersionId, payload);
            case "genre" -> insertGenre(context.catalogVersionId, payload);
            case "country" -> insertCountry(context.catalogVersionId, payload);
            case "credit" -> insertCredit(context.catalogVersionId, payload);
            case "provider" -> insertProvider(payload);
            case "availabilitySnapshot" -> insertSnapshot(context, payload);
            case "ottOffer" -> insertOffer(context, payload);
            default -> throw new CatalogImportException("UNKNOWN_RECORD_TYPE", "recordType has no importer");
        }
        context.increment(record.recordType());
    }

    private void insertMovieIdentity(JsonNode payload) {
        UUID movieId = uuid(payload, "movieId");
        jdbc.update("""
                INSERT INTO movie_identity (id, created_at) VALUES (?, ?)
                ON CONFLICT (id) DO NOTHING
                """, movieId, timestamp(instant(payload, "createdAt")));
        for (JsonNode external : payload.path("externalIds")) {
            String source = text(external, "source");
            String externalId = text(external, "externalId");
            List<UUID> mappedMovieIds = jdbc.query(
                    "SELECT movie_id FROM movie_external_id WHERE source = ? AND external_id = ?",
                    (resultSet, rowNumber) -> resultSet.getObject("movie_id", UUID.class),
                    source,
                    externalId
            );
            if (!mappedMovieIds.isEmpty() && !movieId.equals(mappedMovieIds.get(0))) {
                throw new CatalogImportException(
                        "IDENTITY_CONFLICT",
                        "an external identity is already mapped to a different public movieId"
                );
            }
            jdbc.update("""
                    INSERT INTO movie_external_id (
                        movie_id, source, external_id, verification_status, verified_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT (movie_id, source, external_id) DO UPDATE SET
                        verification_status = EXCLUDED.verification_status,
                        verified_at = EXCLUDED.verified_at
                    """,
                    movieId,
                    source,
                    externalId,
                    text(external, "verificationStatus"),
                    nullableTimestamp(external, "verifiedAt")
            );
        }
    }

    private void insertProjection(UUID versionId, JsonNode payload) {
        jdbc.update("""
                INSERT INTO movie_catalog_projection (
                    catalog_version_id, movie_id, media_type, identity_status, visibility_status,
                    original_title, original_language, release_date, runtime_minutes, poster_path, backdrop_path,
                    tmdb_vote_average, tmdb_vote_count, metadata_fetched_at, deleted
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                versionId,
                uuid(payload, "movieId"),
                text(payload, "mediaType"),
                text(payload, "identityStatus"),
                text(payload, "visibilityStatus"),
                text(payload, "originalTitle"),
                text(payload, "originalLanguage"),
                nullableDate(payload, "releaseDate"),
                nullableInteger(payload, "runtimeMinutes"),
                nullableText(payload, "posterPath"),
                nullableText(payload, "backdropPath"),
                nullableDecimal(payload, "tmdbVoteAverage"),
                payload.path("tmdbVoteCount").longValue(),
                timestamp(instant(payload, "metadataFetchedAt")),
                payload.path("deleted").booleanValue()
        );
    }

    private void insertLocalization(UUID versionId, JsonNode payload) {
        jdbc.update("""
                INSERT INTO movie_localization (
                    catalog_version_id, movie_id, locale, title, overview, source, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                versionId,
                uuid(payload, "movieId"),
                text(payload, "locale"),
                blankToNull(nullableText(payload, "title")),
                blankToNull(nullableText(payload, "overview")),
                text(payload, "source"),
                timestamp(instant(payload, "fetchedAt"))
        );
    }

    private void insertGenre(UUID versionId, JsonNode payload) {
        UUID genreId = upsertGenre(payload);
        jdbc.update("""
                INSERT INTO movie_genre (catalog_version_id, movie_id, genre_id, display_order)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (catalog_version_id, movie_id, genre_id) DO UPDATE SET
                    display_order = EXCLUDED.display_order
                """,
                versionId, uuid(payload, "movieId"), genreId, payload.path("displayOrder").intValue()
        );
    }

    private UUID upsertGenre(JsonNode payload) {
        return jdbc.queryForObject("""
                INSERT INTO genre (id, code, display_name_ko, display_order, active)
                VALUES (?, ?, ?, ?, true)
                ON CONFLICT (code) DO UPDATE SET
                    display_name_ko = EXCLUDED.display_name_ko,
                    display_order = LEAST(genre.display_order, EXCLUDED.display_order),
                    active = true
                RETURNING id
                """, UUID.class,
                UUID.randomUUID(),
                text(payload, "code"),
                text(payload, "displayName"),
                payload.path("displayOrder").intValue()
        );
    }

    private void insertCountry(UUID versionId, JsonNode payload) {
        String code = text(payload, "countryCode");
        String displayName = text(payload, "displayName");
        jdbc.update("""
                INSERT INTO country (code, display_name_ko, display_name_en)
                VALUES (?, ?, ?)
                ON CONFLICT (code) DO UPDATE SET
                    display_name_ko = EXCLUDED.display_name_ko,
                    display_name_en = EXCLUDED.display_name_en
                """, code, displayName, displayName);
        jdbc.update("""
                INSERT INTO movie_country (catalog_version_id, movie_id, country_code, display_order)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (catalog_version_id, movie_id, country_code) DO UPDATE SET
                    display_order = EXCLUDED.display_order
                """, versionId, uuid(payload, "movieId"), code, payload.path("displayOrder").intValue());
    }

    private void insertCredit(UUID versionId, JsonNode payload) {
        UUID personId = jdbc.queryForObject("""
                INSERT INTO person (id, tmdb_person_id, display_name, profile_path)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (tmdb_person_id) DO UPDATE SET
                    display_name = EXCLUDED.display_name,
                    profile_path = EXCLUDED.profile_path
                RETURNING id
                """, UUID.class,
                UUID.randomUUID(),
                payload.path("tmdbPersonId").longValue(),
                text(payload, "displayName"),
                nullableText(payload, "profilePath")
        );
        jdbc.update("""
                INSERT INTO movie_credit (
                    catalog_version_id, movie_id, person_id, credit_type, job, character_name, credit_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (catalog_version_id, movie_id, person_id, credit_type, job, character_name)
                DO UPDATE SET credit_order = EXCLUDED.credit_order
                """,
                versionId,
                uuid(payload, "movieId"),
                personId,
                text(payload, "creditType"),
                text(payload, "job"),
                text(payload, "characterName"),
                payload.path("creditOrder").intValue()
        );
    }

    private UUID insertProvider(JsonNode payload) {
        return jdbc.queryForObject("""
                INSERT INTO ott_provider (
                    id, tmdb_provider_id, provider_code, display_name, logo_path, display_priority, active
                ) VALUES (?, ?, ?, ?, ?, ?, true)
                ON CONFLICT (tmdb_provider_id) DO UPDATE SET
                    provider_code = EXCLUDED.provider_code,
                    display_name = EXCLUDED.display_name,
                    logo_path = EXCLUDED.logo_path,
                    display_priority = EXCLUDED.display_priority,
                    active = true
                RETURNING id
                """, UUID.class,
                UUID.randomUUID(),
                payload.path("tmdbProviderId").longValue(),
                text(payload, "providerCode"),
                text(payload, "displayName"),
                nullableText(payload, "logoPath"),
                payload.path("displayPriority").intValue()
        );
    }

    private void insertSnapshot(ImportContext context, JsonNode payload) {
        UUID snapshotId = uuid(payload, "snapshotId");
        UUID movieId = uuid(payload, "movieId");
        jdbc.update("""
                INSERT INTO movie_availability_snapshot (
                    id, catalog_version_id, movie_id, region, fetch_status, source, aggregator_url,
                    fetched_at, fresh_until, serve_until, failure_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                snapshotId,
                context.catalogVersionId,
                movieId,
                text(payload, "region"),
                text(payload, "fetchStatus"),
                text(payload, "source"),
                nullableText(payload, "aggregatorUrl"),
                timestamp(instant(payload, "fetchedAt")),
                timestamp(instant(payload, "freshUntil")),
                timestamp(instant(payload, "serveUntil")),
                nullableText(payload, "failureCode")
        );
        context.snapshotMovies.put(snapshotId, movieId);
    }

    private void insertOffer(ImportContext context, JsonNode payload) {
        UUID snapshotId = uuid(payload, "snapshotId");
        UUID movieId = uuid(payload, "movieId");
        UUID snapshotMovie = context.snapshotMovies.get(snapshotId);
        if (snapshotMovie == null || !snapshotMovie.equals(movieId)) {
            throw new CatalogImportException(
                    "ORPHAN_OFFER",
                    "ottOffer must reference a prior snapshot for the same movie"
            );
        }
        Long tmdbProviderId = payload.path("tmdbProviderId").longValue();
        List<UUID> providers = jdbc.query(
                "SELECT id FROM ott_provider WHERE tmdb_provider_id = ? AND active",
                (resultSet, rowNumber) -> resultSet.getObject("id", UUID.class),
                tmdbProviderId
        );
        if (providers.isEmpty()) {
            throw new CatalogImportException("ORPHAN_OFFER", "ottOffer references an unknown provider");
        }
        jdbc.update("""
                INSERT INTO movie_ott_offer (
                    id, snapshot_id, provider_id, monetization_type, link_type, landing_url, source_display_priority
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                UUID.randomUUID(),
                snapshotId,
                providers.get(0),
                text(payload, "monetizationType"),
                text(payload, "linkType"),
                nullableText(payload, "landingUrl"),
                payload.path("sourceDisplayPriority").intValue()
        );
    }

    private void buildSearchDocuments(UUID versionId) {
        jdbc.update("""
                INSERT INTO movie_search_document (
                    catalog_version_id, movie_id, normalized_title_terms, normalized_person_terms,
                    search_vector, popularity_score, built_at
                )
                SELECT p.catalog_version_id,
                       p.movie_id,
                       lower(concat_ws(' ', p.original_title,
                           COALESCE((SELECT string_agg(l.title, ' ') FROM movie_localization l
                                     WHERE l.catalog_version_id = p.catalog_version_id
                                       AND l.movie_id = p.movie_id AND l.title IS NOT NULL), ''))),
                       lower(COALESCE((SELECT string_agg(pe.display_name, ' ' ORDER BY c.credit_order)
                                      FROM movie_credit c JOIN person pe ON pe.id = c.person_id
                                     WHERE c.catalog_version_id = p.catalog_version_id
                                       AND c.movie_id = p.movie_id), '')),
                       to_tsvector('simple', lower(concat_ws(' ', p.original_title,
                           COALESCE((SELECT string_agg(l.title, ' ') FROM movie_localization l
                                     WHERE l.catalog_version_id = p.catalog_version_id
                                       AND l.movie_id = p.movie_id AND l.title IS NOT NULL), ''),
                           COALESCE((SELECT string_agg(pe.display_name, ' ' ORDER BY c.credit_order)
                                     FROM movie_credit c JOIN person pe ON pe.id = c.person_id
                                    WHERE c.catalog_version_id = p.catalog_version_id
                                      AND c.movie_id = p.movie_id), '')))),
                       p.tmdb_vote_count::numeric,
                       ?
                  FROM movie_catalog_projection p
                 WHERE p.catalog_version_id = ?
                """, timestamp(clock.instant()), versionId);
    }

    private void deriveFlavorAssignments(UUID versionId) {
        jdbc.update("""
                WITH primary_genre AS (
                    SELECT p.movie_id,
                           max(substring(g.code FROM 6)::integer) AS source_genre_id
                      FROM movie_catalog_projection p
                      JOIN movie_genre mg
                        ON mg.catalog_version_id = p.catalog_version_id
                       AND mg.movie_id = p.movie_id
                       AND mg.display_order = 0
                      JOIN genre g ON g.id = mg.genre_id AND g.code ~ '^TMDB_[0-9]+$'
                     WHERE p.catalog_version_id = ?
                       AND p.visibility_status = 'UI_READY'
                       AND p.identity_status = 'IDENTITY_VERIFIED'
                       AND p.deleted = false
                     GROUP BY p.movie_id
                    HAVING count(*) = 1
                )
                INSERT INTO movie_flavor_assignment (
                    mapping_version, movie_id, flavor_id, assignment_source,
                    source_genre_id, source_display_order, assigned_at
                )
                SELECT mv.mapping_version, pg.movie_id, gm.flavor_id, 'PRIMARY_TMDB_GENRE',
                       pg.source_genre_id, 0, ?
                  FROM primary_genre pg
                  JOIN flavor_mapping_version mv ON mv.status = 'ACTIVE'
                  JOIN flavor_genre_mapping gm
                    ON gm.mapping_version = mv.mapping_version
                   AND gm.source_genre_id = pg.source_genre_id
                ON CONFLICT (mapping_version, movie_id) DO UPDATE SET
                    flavor_id = EXCLUDED.flavor_id,
                    assignment_source = EXCLUDED.assignment_source,
                    source_genre_id = EXCLUDED.source_genre_id,
                    source_display_order = EXCLUDED.source_display_order,
                    assigned_at = EXCLUDED.assigned_at
                """, versionId, timestamp(clock.instant()));
    }

    private void runQualityGates(UUID versionId) {
        assertZero("TV_EXPOSURE", """
                SELECT count(*) FROM movie_catalog_projection
                 WHERE catalog_version_id = ? AND visibility_status IN ('CATALOG_VISIBLE', 'UI_READY')
                   AND (media_type <> 'MOVIE' OR identity_status <> 'IDENTITY_VERIFIED' OR deleted)
                """, versionId);
        assertZero("REQUIRED_PROJECTION", """
                SELECT count(*) FROM movie_catalog_projection p
                 WHERE p.catalog_version_id = ? AND p.visibility_status IN ('CATALOG_VISIBLE', 'UI_READY')
                   AND (
                       btrim(p.original_title) = ''
                       OR NOT EXISTS (
                           SELECT 1 FROM movie_localization l
                            WHERE l.catalog_version_id = p.catalog_version_id AND l.movie_id = p.movie_id
                              AND l.overview IS NOT NULL AND btrim(l.overview) <> ''
                       )
                       OR NOT EXISTS (
                           SELECT 1 FROM movie_genre g
                            WHERE g.catalog_version_id = p.catalog_version_id AND g.movie_id = p.movie_id
                       )
                   )
                """, versionId);
        assertZero("UI_READY_VALIDITY", """
                SELECT count(*) FROM movie_catalog_projection p
                 WHERE p.catalog_version_id = ? AND p.visibility_status = 'UI_READY'
                   AND (
                       p.poster_path IS NULL OR p.runtime_minutes IS NULL
                       OR NOT EXISTS (
                           SELECT 1 FROM movie_credit c
                            WHERE c.catalog_version_id = p.catalog_version_id AND c.movie_id = p.movie_id
                              AND c.credit_type = 'DIRECTOR'
                       )
                   )
                """, versionId);
        assertZero("FLAVOR_ASSIGNMENT", """
                SELECT count(*)
                  FROM movie_catalog_projection p
                 WHERE p.catalog_version_id = ?
                   AND p.visibility_status = 'UI_READY'
                   AND p.identity_status = 'IDENTITY_VERIFIED'
                   AND p.deleted = false
                   AND NOT EXISTS (
                       SELECT 1
                         FROM flavor_mapping_version mv
                         JOIN movie_flavor_assignment a
                           ON a.mapping_version = mv.mapping_version
                          AND a.movie_id = p.movie_id
                         JOIN flavor_genre_mapping gm
                           ON gm.mapping_version = a.mapping_version
                          AND gm.source_genre_id = a.source_genre_id
                          AND gm.flavor_id = a.flavor_id
                         JOIN movie_genre mg
                           ON mg.catalog_version_id = p.catalog_version_id
                          AND mg.movie_id = p.movie_id
                          AND mg.display_order = 0
                         JOIN genre g
                           ON g.id = mg.genre_id
                          AND g.code = 'TMDB_' || a.source_genre_id::text
                        WHERE mv.status = 'ACTIVE'
                        GROUP BY mv.mapping_version, a.movie_id
                       HAVING count(*) = 1
                   )
                """, versionId);
        assertZero("SNAPSHOT_CONSISTENCY", """
                SELECT count(*)
                  FROM (
                      SELECT s.id
                        FROM movie_availability_snapshot s
                        LEFT JOIN movie_ott_offer o ON o.snapshot_id = s.id
                       WHERE s.catalog_version_id = ?
                       GROUP BY s.id, s.fetch_status
                      HAVING (s.fetch_status = 'SUCCESS_EMPTY' AND count(o.id) <> 0)
                          OR (s.fetch_status = 'SUCCESS_LISTED' AND count(o.id) < 1)
                          OR (s.fetch_status = 'FAILED' AND count(o.id) <> 0)
                  ) violations
                """, versionId);
    }

    private void assertZero(String gate, String sql, UUID versionId) {
        List<Long> violations = jdbc.query(sql, (resultSet, rowNumber) -> resultSet.getLong(1), versionId);
        long count = violations.stream().mapToLong(Long::longValue).sum();
        if (count != 0) {
            throw new CatalogImportException("QUALITY_GATE_FAILED", gate + " quality gate failed");
        }
    }

    private void publish(UUID versionId, UUID runId, ImportContext context, Instant startedAt) {
        Instant finishedAt = clock.instant();
        jdbc.update("UPDATE catalog_version SET status = 'RETIRED' WHERE status = 'ACTIVE'");
        int published = jdbc.update("""
                UPDATE catalog_version
                   SET status = 'ACTIVE', published_at = ?
                 WHERE id = ? AND status = 'STAGING'
                """, timestamp(finishedAt), versionId);
        if (published != 1) {
            throw new CatalogImportException("ATOMIC_PUBLISH_FAILED", "staging catalog version could not be activated");
        }
        String metrics;
        try {
            metrics = objectMapper.writeValueAsString(Map.of(
                    "durationMillis", Math.max(0, finishedAt.toEpochMilli() - startedAt.toEpochMilli()),
                    "recordCounts", context.countsAsMap()
            ));
        } catch (JsonProcessingException exception) {
            throw new CatalogImportException("IMPORT_METRICS_FAILED", "import metrics could not be serialized", 0, exception);
        }
        jdbc.update("""
                UPDATE catalog_sync_run
                   SET status = 'SUCCEEDED', finished_at = ?, metrics = metrics || ?::jsonb
                 WHERE id = ? AND status = 'RUNNING'
                """, timestamp(finishedAt), metrics, runId);
    }

    private String text(JsonNode payload, String field) {
        return payload.path(field).textValue();
    }

    private String nullableText(JsonNode payload, String field) {
        JsonNode value = payload.get(field);
        return value == null || value.isNull() ? null : value.textValue();
    }

    private String blankToNull(String value) {
        return value == null || value.isBlank() ? null : value;
    }

    private UUID uuid(JsonNode payload, String field) {
        return UUID.fromString(text(payload, field));
    }

    private Instant instant(JsonNode payload, String field) {
        return Instant.parse(text(payload, field));
    }

    private OffsetDateTime timestamp(Instant instant) {
        return OffsetDateTime.ofInstant(instant, ZoneOffset.UTC);
    }

    private OffsetDateTime nullableTimestamp(JsonNode payload, String field) {
        JsonNode value = payload.get(field);
        return value == null || value.isNull() ? null : timestamp(Instant.parse(value.textValue()));
    }

    private LocalDate nullableDate(JsonNode payload, String field) {
        String value = nullableText(payload, field);
        return value == null ? null : LocalDate.parse(value);
    }

    private Integer nullableInteger(JsonNode payload, String field) {
        JsonNode value = payload.get(field);
        return value == null || value.isNull() ? null : value.intValue();
    }

    private java.math.BigDecimal nullableDecimal(JsonNode payload, String field) {
        JsonNode value = payload.get(field);
        return value == null || value.isNull() ? null : value.decimalValue();
    }

    private record ExistingVersion(String sourceHash, String status) {
    }

    private static final class ImportContext {
        private final UUID catalogVersionId;
        private final Map<String, Long> counts = new LinkedHashMap<>();
        private final Map<UUID, UUID> snapshotMovies = new HashMap<>();

        private ImportContext(UUID catalogVersionId) {
            this.catalogVersionId = catalogVersionId;
        }

        private void increment(String recordType) {
            counts.merge(recordType, 1L, Long::sum);
        }

        private Map<String, Long> countsAsMap() {
            return Map.copyOf(counts);
        }
    }

    @FunctionalInterface
    private interface RecordConsumer {
        void accept(ValidatedRecord record, long lineNumber);
    }
}
