package com.feelm.catalog.importer;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionStatus;

import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.time.Clock;
import java.util.HexFormat;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class CatalogImportServiceValidationTest {
    @TempDir
    Path temporaryDirectory;

    @Test
    void malformedHeaderIsRejectedBeforeAnyDatabaseMutation() throws Exception {
        Path artifact = temporaryDirectory.resolve("invalid.jsonl");
        Files.writeString(artifact, "{\"recordType\":\"artifactHeader\",\"schemaVersion\":2}\n");
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        CatalogImportService service = new CatalogImportService(
                jdbc, new ObjectMapper(), new CatalogArtifactV1Validator(), Clock.systemUTC(),
                mock(PlatformTransactionManager.class)
        );

        assertThatThrownBy(() -> service.importArtifact(artifact))
                .isInstanceOf(CatalogImportException.class);
        verifyNoInteractions(jdbc);
    }

    @Test
    void missingArtifactIsRejectedWithoutDatabaseAccess() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        CatalogImportService service = new CatalogImportService(
                jdbc, new ObjectMapper(), new CatalogArtifactV1Validator(), Clock.systemUTC(),
                mock(PlatformTransactionManager.class)
        );

        assertThatThrownBy(() -> service.importArtifact(temporaryDirectory.resolve("missing.jsonl")))
                .isInstanceOf(CatalogImportException.class)
                .extracting(exception -> ((CatalogImportException) exception).code())
                .isEqualTo("ARTIFACT_NOT_READABLE");
        verifyNoInteractions(jdbc);
    }

    @Test
    @SuppressWarnings({"rawtypes", "unchecked"})
    void identicalPublishedArtifactIsIdempotentButStagingIsNotSuccess() throws Exception {
        Path artifact = validMinimalArtifact("idempotent.jsonl");
        String hash = sha256(artifact);

        JdbcTemplate activeJdbc = mock(JdbcTemplate.class);
        when(activeJdbc.query(anyString(), any(RowMapper.class), eq("catalog-import-test-v1")))
                .thenAnswer(invocation -> existingVersion(invocation.getArgument(1), hash, "ACTIVE"));
        CatalogImportService activeService = service(activeJdbc, mock(PlatformTransactionManager.class));
        CatalogImportResult result = activeService.importArtifact(artifact);
        assertThat(result.status()).isEqualTo(CatalogImportResult.Status.ALREADY_IMPORTED);

        JdbcTemplate stagingJdbc = mock(JdbcTemplate.class);
        when(stagingJdbc.query(anyString(), any(RowMapper.class), eq("catalog-import-test-v1")))
                .thenAnswer(invocation -> existingVersion(invocation.getArgument(1), hash, "STAGING"));
        CatalogImportService stagingService = service(stagingJdbc, mock(PlatformTransactionManager.class));
        assertThatThrownBy(() -> stagingService.importArtifact(artifact))
                .isInstanceOf(CatalogImportException.class)
                .extracting(exception -> ((CatalogImportException) exception).code())
                .isEqualTo("INVALID_EXISTING_VERSION_STATE");
    }

    @Test
    @SuppressWarnings({"rawtypes", "unchecked"})
    void failedStreamingValidationNeverRunsTheActiveVersionSwapAndWritesSafeAudit() throws Exception {
        Path artifact = temporaryDirectory.resolve("bad-record.jsonl");
        Files.writeString(artifact, CatalogArtifactV1ValidatorTest.validHeader()
                + "\n{\"recordType\":\"unknown\",\"payload\":{}}\n");
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        when(jdbc.query(anyString(), any(RowMapper.class), eq("catalog-import-test-v1"))).thenReturn(List.of());
        when(jdbc.update(anyString(), any(Object[].class))).thenReturn(1);
        PlatformTransactionManager transactions = mock(PlatformTransactionManager.class);
        when(transactions.getTransaction(any())).thenReturn(mock(TransactionStatus.class), mock(TransactionStatus.class));
        CatalogImportService service = service(jdbc, transactions);

        assertThatThrownBy(() -> service.importArtifact(artifact))
                .isInstanceOf(CatalogImportException.class)
                .extracting(exception -> ((CatalogImportException) exception).code())
                .isEqualTo("UNKNOWN_RECORD_TYPE");

        verify(jdbc, never()).update(org.mockito.ArgumentMatchers.startsWith("UPDATE catalog_version SET status = 'RETIRED'"));
        verify(transactions).rollback(any(TransactionStatus.class));
        verify(transactions).commit(any(TransactionStatus.class));
    }

    private CatalogImportService service(JdbcTemplate jdbc, PlatformTransactionManager transactions) {
        return new CatalogImportService(
                jdbc, new ObjectMapper(), new CatalogArtifactV1Validator(), Clock.systemUTC(), transactions
        );
    }

    private Path validMinimalArtifact(String filename) throws Exception {
        Path artifact = temporaryDirectory.resolve(filename);
        Files.writeString(artifact, CatalogArtifactV1ValidatorTest.validHeader() + "\n"
                + CatalogArtifactV1ValidatorTest.validRecordLines()[0] + "\n");
        return artifact;
    }

    private List<?> existingVersion(RowMapper mapper, String hash, String status) throws Exception {
        java.sql.ResultSet resultSet = mock(java.sql.ResultSet.class);
        when(resultSet.getString("source_hash")).thenReturn(hash);
        when(resultSet.getString("status")).thenReturn(status);
        return List.of(mapper.mapRow(resultSet, 0));
    }

    private String sha256(Path path) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(path)));
    }
}
