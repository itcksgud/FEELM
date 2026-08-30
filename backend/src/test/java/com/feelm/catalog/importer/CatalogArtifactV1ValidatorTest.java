package com.feelm.catalog.importer;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class CatalogArtifactV1ValidatorTest {
    private final ObjectMapper objectMapper = new ObjectMapper();
    private CatalogArtifactV1Validator validator;

    @BeforeEach
    void setUp() {
        validator = new CatalogArtifactV1Validator();
    }

    @Test
    void acceptsThePipelineHeaderAndEveryV1RecordType() throws Exception {
        CatalogArtifactV1Validator.ArtifactHeader header = validator.validateHeader(json(validHeader()), 1);
        assertThat(header.catalogVersion()).isEqualTo("catalog-import-test-v1");
        assertThat(header.movielensArchiveSha256()).hasSize(64);

        for (String line : validRecordLines()) {
            CatalogArtifactV1Validator.ValidatedRecord record = validator.validateRecord(json(line), 2);
            assertThat(CatalogArtifactV1Validator.RECORD_TYPES).contains(record.recordType());
        }
    }

    @Test
    void rejectsUnknownSchemaVersionsAndRecordTypes() throws Exception {
        assertThatThrownBy(() -> validator.validateHeader(json(validHeader().replace("\"schemaVersion\":1", "\"schemaVersion\":2")), 1))
                .isInstanceOf(CatalogImportException.class)
                .extracting(exception -> ((CatalogImportException) exception).code())
                .isEqualTo("UNSUPPORTED_SCHEMA_VERSION");

        assertThatThrownBy(() -> validator.validateRecord(json("{\"recordType\":\"movieSimilarity\",\"payload\":{}}"), 2))
                .isInstanceOf(CatalogImportException.class)
                .extracting(exception -> ((CatalogImportException) exception).code())
                .isEqualTo("UNKNOWN_RECORD_TYPE");
    }

    @Test
    void enforcesAdditionalPropertiesFalseAndRequiredProvenance() throws Exception {
        String extraHeader = validHeader().replace("\"sources\":[", "\"secretToken\":\"must-not-pass\",\"sources\":[");
        assertThatThrownBy(() -> validator.validateHeader(json(extraHeader), 1))
                .isInstanceOf(CatalogImportException.class)
                .extracting(exception -> ((CatalogImportException) exception).code())
                .isEqualTo("SCHEMA_MISMATCH");

        String missingProvenanceField = validRecordLines()[0].replace(",\"previousTmdbId\":null", "");
        assertThatThrownBy(() -> validator.validateRecord(json(missingProvenanceField), 2))
                .isInstanceOf(CatalogImportException.class)
                .extracting(exception -> ((CatalogImportException) exception).code())
                .isEqualTo("SCHEMA_MISMATCH");
    }

    @Test
    void validatesAvailabilityTimePolicyBeforeDatabaseInsertion() throws Exception {
        String invalid = validRecordLines()[7].replace("2026-08-30T00:00:00Z", "2026-08-30T01:00:00Z");
        assertThatThrownBy(() -> validator.validateRecord(json(invalid), 9))
                .isInstanceOf(CatalogImportException.class)
                .extracting(exception -> ((CatalogImportException) exception).code())
                .isEqualTo("INVALID_RECORD");
    }

    private JsonNode json(String value) throws Exception {
        return objectMapper.readTree(value);
    }

    static String validHeader() {
        return """
                {"recordType":"artifactHeader","schemaVersion":1,"catalogVersion":"catalog-import-test-v1","generatedAt":"2026-08-29T00:00:00+00:00","sourceChecksums":{"movielensArchiveSha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},"sources":[{"name":"MOVIELENS_32M","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},{"name":"TMDB_API","apiVersion":"3","region":"KR"}]}
                """.trim();
    }

    static String[] validRecordLines() {
        return new String[]{
                "{\"recordType\":\"movieIdentity\",\"payload\":{\"movieId\":\"6b226903-0ca4-4f5a-9bf0-50d6cedd224c\",\"createdAt\":\"2026-08-29T00:00:00+00:00\",\"identityStatus\":\"IDENTITY_VERIFIED\",\"externalIds\":[{\"source\":\"MOVIELENS\",\"externalId\":\"1\",\"verificationStatus\":\"VERIFIED\",\"verifiedAt\":\"2026-08-29T00:00:00+00:00\"}],\"provenance\":{\"movielensTitle\":\"Fixture Movie\",\"movielensReleaseYear\":2013,\"resolutionMethod\":\"MOVIELENS_TMDB_VERIFIED\",\"previousTmdbId\":null}}}",
                "{\"recordType\":\"movieProjection\",\"payload\":{\"movieId\":\"6b226903-0ca4-4f5a-9bf0-50d6cedd224c\",\"mediaType\":\"MOVIE\",\"identityStatus\":\"IDENTITY_VERIFIED\",\"visibilityStatus\":\"UI_READY\",\"originalTitle\":\"Fixture Movie\",\"originalLanguage\":\"en\",\"releaseDate\":\"2013-05-29\",\"runtimeMinutes\":115,\"posterPath\":\"/poster.jpg\",\"backdropPath\":null,\"tmdbVoteAverage\":7.3,\"tmdbVoteCount\":120,\"metadataFetchedAt\":\"2026-08-29T00:00:00+00:00\",\"deleted\":false}}",
                "{\"recordType\":\"localization\",\"payload\":{\"movieId\":\"6b226903-0ca4-4f5a-9bf0-50d6cedd224c\",\"locale\":\"ko-KR\",\"title\":\"테스트 영화\",\"overview\":\"테스트 줄거리\",\"source\":\"TMDB\",\"fetchedAt\":\"2026-08-29T00:00:00+00:00\"}}",
                "{\"recordType\":\"genre\",\"payload\":{\"movieId\":\"6b226903-0ca4-4f5a-9bf0-50d6cedd224c\",\"code\":\"TMDB_80\",\"displayName\":\"범죄\",\"source\":\"TMDB\",\"sourceId\":\"80\",\"displayOrder\":0}}",
                "{\"recordType\":\"country\",\"payload\":{\"movieId\":\"6b226903-0ca4-4f5a-9bf0-50d6cedd224c\",\"countryCode\":\"US\",\"displayName\":\"United States\",\"displayOrder\":0}}",
                "{\"recordType\":\"credit\",\"payload\":{\"movieId\":\"6b226903-0ca4-4f5a-9bf0-50d6cedd224c\",\"creditType\":\"DIRECTOR\",\"job\":\"Director\",\"tmdbPersonId\":9340,\"displayName\":\"Louis Leterrier\",\"profilePath\":null,\"characterName\":\"\",\"creditOrder\":0}}",
                "{\"recordType\":\"provider\",\"payload\":{\"tmdbProviderId\":8,\"providerCode\":\"TMDB_8\",\"displayName\":\"Netflix\",\"logoPath\":\"/netflix.jpg\",\"displayPriority\":10}}",
                "{\"recordType\":\"availabilitySnapshot\",\"payload\":{\"snapshotId\":\"20000000-0000-0000-0000-000000000001\",\"movieId\":\"6b226903-0ca4-4f5a-9bf0-50d6cedd224c\",\"region\":\"KR\",\"fetchStatus\":\"SUCCESS_LISTED\",\"source\":\"TMDB_JUSTWATCH\",\"aggregatorUrl\":\"https://example.test/watch\",\"fetchedAt\":\"2026-08-29T00:00:00Z\",\"freshUntil\":\"2026-08-30T00:00:00Z\",\"serveUntil\":\"2026-09-05T00:00:00Z\",\"failureCode\":null}}",
                "{\"recordType\":\"ottOffer\",\"payload\":{\"snapshotId\":\"20000000-0000-0000-0000-000000000001\",\"movieId\":\"6b226903-0ca4-4f5a-9bf0-50d6cedd224c\",\"tmdbProviderId\":8,\"monetizationType\":\"FLATRATE\",\"linkType\":\"AGGREGATOR\",\"landingUrl\":\"https://example.test/watch\",\"sourceDisplayPriority\":10}}"
        };
    }
}
