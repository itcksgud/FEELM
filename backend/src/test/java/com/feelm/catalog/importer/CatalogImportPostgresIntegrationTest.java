package com.feelm.catalog.importer;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

@SpringBootTest
@ActiveProfiles("postgres")
@Testcontainers(disabledWithoutDocker = true)
class CatalogImportPostgresIntegrationTest {
    @Container
    static final PostgreSQLContainer<?> POSTGRES = new PostgreSQLContainer<>("postgres:17-alpine")
            .withDatabaseName("feelm_import_test");

    @DynamicPropertySource
    static void configurePostgres(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", POSTGRES::getJdbcUrl);
        registry.add("spring.datasource.username", POSTGRES::getUsername);
        registry.add("spring.datasource.password", POSTGRES::getPassword);
    }

    @Autowired
    CatalogImportService importService;

    @Autowired
    JdbcTemplate jdbc;

    @TempDir
    Path temporaryDirectory;

    @Test
    void publishesIdempotentlyAndKeepsThePreviousActiveVersionWhenQualityFails() throws Exception {
        Path valid = temporaryDirectory.resolve("catalog-v1.jsonl");
        Files.writeString(valid, CatalogArtifactV1ValidatorTest.validHeader() + "\n"
                + String.join("\n", CatalogArtifactV1ValidatorTest.validRecordLines()) + "\n");

        CatalogImportResult first = importService.importArtifact(valid);
        assertThat(first.status()).isEqualTo(CatalogImportResult.Status.PUBLISHED);
        assertThat(activeVersion()).isEqualTo("catalog-import-test-v1");
        assertThat(jdbc.queryForObject("""
                SELECT count(*) FROM movie_flavor_assignment
                 WHERE mapping_version = 'v1' AND movie_id = '6b226903-0ca4-4f5a-9bf0-50d6cedd224c'
                """, Long.class)).isEqualTo(1);
        assertThat(jdbc.queryForObject("""
                SELECT count(*) FROM c1_rating_eligible_movie
                 WHERE movie_id = '6b226903-0ca4-4f5a-9bf0-50d6cedd224c'
                """, Long.class)).isEqualTo(1);

        CatalogImportResult repeated = importService.importArtifact(valid);
        assertThat(repeated.status()).isEqualTo(CatalogImportResult.Status.ALREADY_IMPORTED);
        assertThat(activeVersion()).isEqualTo("catalog-import-test-v1");

        Path invalid = temporaryDirectory.resolve("catalog-v2-invalid.jsonl");
        String headerV2 = CatalogArtifactV1ValidatorTest.validHeader()
                .replace("catalog-import-test-v1", "catalog-import-test-v2");
        Files.writeString(invalid, headerV2 + "\n"
                + CatalogArtifactV1ValidatorTest.validRecordLines()[0] + "\n"
                + CatalogArtifactV1ValidatorTest.validRecordLines()[1] + "\n");

        assertThatThrownBy(() -> importService.importArtifact(invalid))
                .isInstanceOf(CatalogImportException.class)
                .extracting(exception -> ((CatalogImportException) exception).code())
                .isEqualTo("QUALITY_GATE_FAILED");

        assertThat(activeVersion()).isEqualTo("catalog-import-test-v1");
        assertThat(jdbc.queryForObject(
                "SELECT count(*) FROM catalog_version WHERE public_version = 'catalog-import-test-v2'", Long.class
        )).isZero();
        assertThat(jdbc.queryForObject("""
                SELECT count(*) FROM catalog_sync_run
                 WHERE source_version = 'catalog-import-test-v2'
                   AND status = 'FAILED'
                   AND failure_summary = 'QUALITY_GATE_FAILED'
                """, Long.class)).isEqualTo(1);

        Path listedWithoutOffers = temporaryDirectory.resolve("catalog-v3-listed-without-offers.jsonl");
        String headerV3 = CatalogArtifactV1ValidatorTest.validHeader()
                .replace("catalog-import-test-v1", "catalog-import-test-v3");
        String[] validRecords = CatalogArtifactV1ValidatorTest.validRecordLines();
        validRecords[7] = validRecords[7].replace(
                "20000000-0000-0000-0000-000000000001",
                "20000000-0000-0000-0000-000000000003"
        );
        Files.writeString(listedWithoutOffers, headerV3 + "\n"
                + String.join("\n", java.util.Arrays.copyOf(validRecords, validRecords.length - 1)) + "\n");

        assertThatThrownBy(() -> importService.importArtifact(listedWithoutOffers))
                .isInstanceOf(CatalogImportException.class)
                .hasMessageContaining("SNAPSHOT_CONSISTENCY");
        assertThat(activeVersion()).isEqualTo("catalog-import-test-v1");
        assertThat(jdbc.queryForObject(
                "SELECT count(*) FROM catalog_version WHERE public_version = 'catalog-import-test-v3'", Long.class
        )).isZero();

        Path conflictingIdentity = temporaryDirectory.resolve("catalog-v4-identity-conflict.jsonl");
        String headerV4 = CatalogArtifactV1ValidatorTest.validHeader()
                .replace("catalog-import-test-v1", "catalog-import-test-v4");
        String conflictingRecord = CatalogArtifactV1ValidatorTest.validRecordLines()[0]
                .replace("6b226903-0ca4-4f5a-9bf0-50d6cedd224c", "6b226903-0ca4-4f5a-9bf0-50d6cedd224d");
        Files.writeString(conflictingIdentity, headerV4 + "\n" + conflictingRecord + "\n");

        assertThatThrownBy(() -> importService.importArtifact(conflictingIdentity))
                .isInstanceOf(CatalogImportException.class)
                .extracting(exception -> ((CatalogImportException) exception).code())
                .isEqualTo("IDENTITY_CONFLICT");
        assertThat(activeVersion()).isEqualTo("catalog-import-test-v1");
        assertThat(jdbc.queryForObject(
                "SELECT count(*) FROM catalog_version WHERE public_version = 'catalog-import-test-v4'", Long.class
        )).isZero();

        Path unknownFlavor = temporaryDirectory.resolve("catalog-v5-unknown-flavor.jsonl");
        String headerV5 = CatalogArtifactV1ValidatorTest.validHeader()
                .replace("catalog-import-test-v1", "catalog-import-test-v5");
        String oldMovieId = "6b226903-0ca4-4f5a-9bf0-50d6cedd224c";
        String unknownMovieId = "b7a1a5cc-b1c6-4e50-a1b9-3670f737c683";
        String oldSnapshotId = "20000000-0000-0000-0000-000000000001";
        String unknownSnapshotId = "20000000-0000-0000-0000-000000000005";
        String[] unknownFlavorRecords = CatalogArtifactV1ValidatorTest.validRecordLines();
        for (int index = 0; index < unknownFlavorRecords.length; index++) {
            unknownFlavorRecords[index] = unknownFlavorRecords[index]
                    .replace(oldMovieId, unknownMovieId)
                    .replace(oldSnapshotId, unknownSnapshotId)
                    .replace("\"externalId\":\"1\"", "\"externalId\":\"5\"")
                    .replace("TMDB_80", "TMDB_999999")
                    .replace("\"sourceId\":\"80\"", "\"sourceId\":\"999999\"");
        }
        Files.writeString(unknownFlavor, headerV5 + "\n" + String.join("\n", unknownFlavorRecords) + "\n");

        assertThatThrownBy(() -> importService.importArtifact(unknownFlavor))
                .isInstanceOf(CatalogImportException.class)
                .hasMessageContaining("FLAVOR_ASSIGNMENT");
        assertThat(activeVersion()).isEqualTo("catalog-import-test-v1");
        assertThat(jdbc.queryForObject(
                "SELECT count(*) FROM catalog_version WHERE public_version = 'catalog-import-test-v5'", Long.class
        )).isZero();
    }

    private String activeVersion() {
        return jdbc.queryForObject(
                "SELECT public_version FROM catalog_version WHERE status = 'ACTIVE'", String.class
        );
    }
}
