package com.feelm.catalog.importer;

import com.fasterxml.jackson.databind.JsonNode;
import org.springframework.stereotype.Component;

import java.time.Instant;
import java.time.LocalDate;
import java.util.HashSet;
import java.util.Iterator;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.regex.Pattern;

@Component
final class CatalogArtifactV1Validator {
    static final Set<String> RECORD_TYPES = Set.of(
            "movieIdentity", "movieProjection", "localization", "genre", "country", "credit",
            "provider", "availabilitySnapshot", "ottOffer"
    );
    private static final Pattern SHA256 = Pattern.compile("^[a-f0-9]{64}$");
    private static final Pattern COUNTRY = Pattern.compile("^[A-Z]{2}$");

    ArtifactHeader validateHeader(JsonNode node, long line) {
        objectWithExactFields(node, line, "header", Set.of(
                "recordType", "schemaVersion", "catalogVersion", "generatedAt", "sourceChecksums", "sources"
        ));
        requireText(node, "recordType", line, 1, 64);
        if (!"artifactHeader".equals(node.path("recordType").asText())) {
            fail("INVALID_HEADER", "the first recordType must be artifactHeader", line);
        }
        requireInteger(node, "schemaVersion", line, 1, Integer.MAX_VALUE);
        if (node.path("schemaVersion").asInt() != 1) {
            fail("UNSUPPORTED_SCHEMA_VERSION", "only artifact schemaVersion 1 is supported", line);
        }
        String catalogVersion = requireText(node, "catalogVersion", line, 1, 128);
        Instant generatedAt = requireInstant(node, "generatedAt", line);
        JsonNode checksums = node.path("sourceChecksums");
        objectWithExactFields(checksums, line, "sourceChecksums", Set.of("movielensArchiveSha256"));
        String checksum = requireText(checksums, "movielensArchiveSha256", line, 64, 64);
        if (!SHA256.matcher(checksum).matches()) {
            fail("INVALID_HEADER", "movielensArchiveSha256 must be lowercase SHA-256", line);
        }
        JsonNode sources = node.path("sources");
        if (!sources.isArray() || sources.size() < 2) {
            fail("INVALID_HEADER", "sources must contain at least two provenance objects", line);
        }
        for (JsonNode source : sources) {
            if (!source.isObject()) {
                fail("INVALID_HEADER", "each sources item must be an object", line);
            }
        }
        return new ArtifactHeader(catalogVersion, generatedAt, checksum, sources.deepCopy());
    }

    ValidatedRecord validateRecord(JsonNode node, long line) {
        objectWithExactFields(node, line, "record", Set.of("recordType", "payload"));
        String type = requireText(node, "recordType", line, 1, 64);
        if (!RECORD_TYPES.contains(type)) {
            fail("UNKNOWN_RECORD_TYPE", "unknown recordType", line);
        }
        JsonNode payload = node.path("payload");
        if (!payload.isObject()) {
            fail("INVALID_RECORD", "payload must be an object", line);
        }
        switch (type) {
            case "movieIdentity" -> movieIdentity(payload, line);
            case "movieProjection" -> movieProjection(payload, line);
            case "localization" -> localization(payload, line);
            case "genre" -> genre(payload, line);
            case "country" -> country(payload, line);
            case "credit" -> credit(payload, line);
            case "provider" -> provider(payload, line);
            case "availabilitySnapshot" -> availability(payload, line);
            case "ottOffer" -> offer(payload, line);
            default -> throw new IllegalStateException("validated recordType has no validator");
        }
        return new ValidatedRecord(type, payload);
    }

    private void movieIdentity(JsonNode payload, long line) {
        exact(payload, line, Set.of("movieId", "createdAt", "identityStatus", "externalIds", "provenance"));
        requireUuid(payload, "movieId", line);
        requireInstant(payload, "createdAt", line);
        requireEnum(payload, "identityStatus", line, Set.of(
                "IDENTITY_VERIFIED", "TYPE_MISMATCH_TV", "TMDB_NOT_FOUND", "IDENTITY_REVIEW_REQUIRED", "SOURCE_REMOVED"
        ));
        JsonNode externalIds = payload.path("externalIds");
        if (!externalIds.isArray() || externalIds.size() == 0) {
            fail("INVALID_RECORD", "externalIds must be a non-empty array", line);
        }
        for (JsonNode external : externalIds) {
            exact(external, line, Set.of("source", "externalId", "verificationStatus", "verifiedAt"));
            requireEnum(external, "source", line, Set.of("MOVIELENS", "TMDB", "IMDB", "WIKIDATA"));
            requireText(external, "externalId", line, 1, 64);
            requireEnum(external, "verificationStatus", line, Set.of("VERIFIED", "RECOVERED", "UNVERIFIED"));
            requireNullableInstant(external, "verifiedAt", line);
        }
        JsonNode provenance = payload.path("provenance");
        exact(provenance, line, Set.of("movielensTitle", "movielensReleaseYear", "resolutionMethod", "previousTmdbId"));
        requireText(provenance, "movielensTitle", line, 1, 500);
        requireNullableInteger(provenance, "movielensReleaseYear", line, 1800, 2200);
        requireText(provenance, "resolutionMethod", line, 1, 128);
        requireNullableInteger(provenance, "previousTmdbId", line, 1, Integer.MAX_VALUE);
    }

    private void movieProjection(JsonNode payload, long line) {
        exact(payload, line, Set.of(
                "movieId", "mediaType", "identityStatus", "visibilityStatus", "originalTitle", "originalLanguage",
                "releaseDate", "runtimeMinutes", "posterPath", "backdropPath", "tmdbVoteAverage", "tmdbVoteCount",
                "metadataFetchedAt", "deleted"
        ));
        requireUuid(payload, "movieId", line);
        requireConst(payload, "mediaType", "MOVIE", line);
        requireConst(payload, "identityStatus", "IDENTITY_VERIFIED", line);
        requireEnum(payload, "visibilityStatus", line, Set.of("UI_READY", "CATALOG_VISIBLE", "UI_INCOMPLETE"));
        requireText(payload, "originalTitle", line, 1, 500);
        requireText(payload, "originalLanguage", line, 1, 16);
        requireNullableDate(payload, "releaseDate", line);
        requireNullableInteger(payload, "runtimeMinutes", line, 1, Integer.MAX_VALUE);
        requireNullableText(payload, "posterPath", line, 1024);
        requireNullableText(payload, "backdropPath", line, 1024);
        JsonNode rating = requirePresent(payload, "tmdbVoteAverage", line);
        if (!rating.isNull() && (!rating.isNumber() || rating.asDouble() < 0 || rating.asDouble() > 10)) {
            fail("INVALID_RECORD", "tmdbVoteAverage must be null or between 0 and 10", line);
        }
        requireLong(payload, "tmdbVoteCount", line, 0, Long.MAX_VALUE);
        requireInstant(payload, "metadataFetchedAt", line);
        requireBoolean(payload, "deleted", line);
    }

    private void localization(JsonNode payload, long line) {
        exact(payload, line, Set.of("movieId", "locale", "title", "overview", "source", "fetchedAt"));
        requireUuid(payload, "movieId", line);
        requireText(payload, "locale", line, 2, 16);
        requireNullableText(payload, "title", line, 500);
        requireNullableText(payload, "overview", line, Integer.MAX_VALUE);
        requireConst(payload, "source", "TMDB", line);
        requireInstant(payload, "fetchedAt", line);
    }

    private void genre(JsonNode payload, long line) {
        exact(payload, line, Set.of("movieId", "code", "displayName", "source", "sourceId", "displayOrder"));
        requireUuid(payload, "movieId", line);
        requireText(payload, "code", line, 1, 64);
        requireText(payload, "displayName", line, 1, 128);
        requireEnum(payload, "source", line, Set.of("TMDB", "MOVIELENS"));
        requireText(payload, "sourceId", line, 1, 128);
        requireInteger(payload, "displayOrder", line, 0, Integer.MAX_VALUE);
    }

    private void country(JsonNode payload, long line) {
        exact(payload, line, Set.of("movieId", "countryCode", "displayName", "displayOrder"));
        requireUuid(payload, "movieId", line);
        String countryCode = requireText(payload, "countryCode", line, 2, 2);
        if (!COUNTRY.matcher(countryCode).matches()) {
            fail("INVALID_RECORD", "countryCode must be ISO alpha-2 uppercase", line);
        }
        requireText(payload, "displayName", line, 1, 128);
        requireInteger(payload, "displayOrder", line, 0, Integer.MAX_VALUE);
    }

    private void credit(JsonNode payload, long line) {
        exact(payload, line, Set.of(
                "movieId", "creditType", "job", "tmdbPersonId", "displayName", "profilePath", "characterName", "creditOrder"
        ));
        requireUuid(payload, "movieId", line);
        requireEnum(payload, "creditType", line, Set.of("DIRECTOR", "CAST"));
        requireText(payload, "job", line, 1, 128);
        requireLong(payload, "tmdbPersonId", line, 1, Long.MAX_VALUE);
        requireText(payload, "displayName", line, 1, 500);
        requireNullableText(payload, "profilePath", line, 1024);
        requireText(payload, "characterName", line, 0, 500);
        requireInteger(payload, "creditOrder", line, 0, Integer.MAX_VALUE);
    }

    private void provider(JsonNode payload, long line) {
        exact(payload, line, Set.of("tmdbProviderId", "providerCode", "displayName", "logoPath", "displayPriority"));
        requireLong(payload, "tmdbProviderId", line, 1, Long.MAX_VALUE);
        requireText(payload, "providerCode", line, 1, 64);
        requireText(payload, "displayName", line, 1, 256);
        requireNullableText(payload, "logoPath", line, 1024);
        requireInteger(payload, "displayPriority", line, 0, Integer.MAX_VALUE);
    }

    private void availability(JsonNode payload, long line) {
        exact(payload, line, Set.of(
                "snapshotId", "movieId", "region", "fetchStatus", "source", "aggregatorUrl", "fetchedAt",
                "freshUntil", "serveUntil", "failureCode"
        ));
        requireUuid(payload, "snapshotId", line);
        requireUuid(payload, "movieId", line);
        requireConst(payload, "region", "KR", line);
        requireEnum(payload, "fetchStatus", line, Set.of("SUCCESS_LISTED", "SUCCESS_EMPTY", "FAILED"));
        requireConst(payload, "source", "TMDB_JUSTWATCH", line);
        requireNullableText(payload, "aggregatorUrl", line, 2048);
        Instant fetchedAt = requireInstant(payload, "fetchedAt", line);
        Instant freshUntil = requireInstant(payload, "freshUntil", line);
        Instant serveUntil = requireInstant(payload, "serveUntil", line);
        if (!freshUntil.equals(fetchedAt.plusSeconds(24 * 3600L))
                || !serveUntil.equals(fetchedAt.plusSeconds(7 * 24 * 3600L))) {
            fail("INVALID_RECORD", "availability freshness intervals violate the C0 policy", line);
        }
        requireNullableText(payload, "failureCode", line, 128);
    }

    private void offer(JsonNode payload, long line) {
        exact(payload, line, Set.of(
                "snapshotId", "movieId", "tmdbProviderId", "monetizationType", "linkType", "landingUrl", "sourceDisplayPriority"
        ));
        requireUuid(payload, "snapshotId", line);
        requireUuid(payload, "movieId", line);
        requireLong(payload, "tmdbProviderId", line, 1, Long.MAX_VALUE);
        requireEnum(payload, "monetizationType", line, Set.of("FLATRATE", "RENT", "BUY", "FREE", "ADS"));
        requireEnum(payload, "linkType", line, Set.of("AGGREGATOR", "DIRECT"));
        requireNullableText(payload, "landingUrl", line, 2048);
        requireInteger(payload, "sourceDisplayPriority", line, 0, Integer.MAX_VALUE);
    }

    private void exact(JsonNode node, long line, Set<String> fields) {
        objectWithExactFields(node, line, "payload", fields);
    }

    private void objectWithExactFields(JsonNode node, long line, String label, Set<String> expected) {
        if (!node.isObject()) {
            fail("INVALID_RECORD", label + " must be an object", line);
        }
        Set<String> actual = new HashSet<>();
        Iterator<Map.Entry<String, JsonNode>> iterator = node.properties().iterator();
        while (iterator.hasNext()) {
            actual.add(iterator.next().getKey());
        }
        if (!actual.equals(expected)) {
            fail("SCHEMA_MISMATCH", label + " fields do not match schema v1", line);
        }
    }

    private JsonNode requirePresent(JsonNode object, String field, long line) {
        JsonNode value = object.get(field);
        if (value == null) {
            fail("SCHEMA_MISMATCH", field + " is required", line);
        }
        return value;
    }

    private String requireText(JsonNode object, String field, long line, int min, int max) {
        JsonNode value = requirePresent(object, field, line);
        if (!value.isTextual() || value.textValue().length() < min || value.textValue().length() > max) {
            fail("INVALID_RECORD", field + " has an invalid string value", line);
        }
        return value.textValue();
    }

    private void requireNullableText(JsonNode object, String field, long line, int max) {
        JsonNode value = requirePresent(object, field, line);
        if (!value.isNull() && (!value.isTextual() || value.textValue().length() > max)) {
            fail("INVALID_RECORD", field + " must be null or a string", line);
        }
    }

    private int requireInteger(JsonNode object, String field, long line, int min, int max) {
        JsonNode value = requirePresent(object, field, line);
        if (!value.isIntegralNumber() || !value.canConvertToInt() || value.asInt() < min || value.asInt() > max) {
            fail("INVALID_RECORD", field + " has an invalid integer value", line);
        }
        return value.asInt();
    }

    private void requireNullableInteger(JsonNode object, String field, long line, int min, int max) {
        JsonNode value = requirePresent(object, field, line);
        if (!value.isNull()) {
            requireInteger(object, field, line, min, max);
        }
    }

    private long requireLong(JsonNode object, String field, long line, long min, long max) {
        JsonNode value = requirePresent(object, field, line);
        if (!value.isIntegralNumber() || !value.canConvertToLong() || value.asLong() < min || value.asLong() > max) {
            fail("INVALID_RECORD", field + " has an invalid integer value", line);
        }
        return value.asLong();
    }

    private void requireBoolean(JsonNode object, String field, long line) {
        if (!requirePresent(object, field, line).isBoolean()) {
            fail("INVALID_RECORD", field + " must be boolean", line);
        }
    }

    private void requireConst(JsonNode object, String field, String expected, long line) {
        String actual = requireText(object, field, line, 1, 128);
        if (!expected.equals(actual)) {
            fail("INVALID_RECORD", field + " must be " + expected, line);
        }
    }

    private void requireEnum(JsonNode object, String field, long line, Set<String> values) {
        String actual = requireText(object, field, line, 1, 128);
        if (!values.contains(actual)) {
            fail("INVALID_RECORD", field + " has an unsupported enum value", line);
        }
    }

    private UUID requireUuid(JsonNode object, String field, long line) {
        try {
            return UUID.fromString(requireText(object, field, line, 36, 36));
        } catch (IllegalArgumentException exception) {
            fail("INVALID_RECORD", field + " must be UUID", line);
            return null;
        }
    }

    private Instant requireInstant(JsonNode object, String field, long line) {
        try {
            return Instant.parse(requireText(object, field, line, 1, 64));
        } catch (Exception exception) {
            fail("INVALID_RECORD", field + " must be an ISO-8601 UTC instant", line);
            return null;
        }
    }

    private void requireNullableInstant(JsonNode object, String field, long line) {
        JsonNode value = requirePresent(object, field, line);
        if (!value.isNull()) {
            requireInstant(object, field, line);
        }
    }

    private void requireNullableDate(JsonNode object, String field, long line) {
        JsonNode value = requirePresent(object, field, line);
        if (value.isNull()) {
            return;
        }
        try {
            LocalDate.parse(requireText(object, field, line, 1, 32));
        } catch (Exception exception) {
            fail("INVALID_RECORD", field + " must be an ISO date", line);
        }
    }

    private void fail(String code, String message, long line) {
        throw new CatalogImportException(code, message, line);
    }

    record ArtifactHeader(
            String catalogVersion,
            Instant generatedAt,
            String movielensArchiveSha256,
            JsonNode sources
    ) {
    }

    record ValidatedRecord(String recordType, JsonNode payload) {
    }
}
