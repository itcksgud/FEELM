package com.feelm.catalog.c5.service;

import com.feelm.catalog.c5.api.C5ApiDtos.FactualReportMetrics;
import com.feelm.catalog.c5.api.C5ApiDtos.ReportMoviePage;
import com.feelm.catalog.c5.api.C5ApiDtos.TasteReport;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.math.BigDecimal;
import java.nio.file.Path;
import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class C5LocalPdfStoreTest {
    @TempDir
    Path directory;

    @Test
    void storesReadsAndDeletesOnlyOpaqueArtifactsInsideTheCanonicalRoot() {
        C5LocalPdfStore store = new C5LocalPdfStore(directory.toString());
        TasteReport report = new TasteReport(
                UUID.randomUUID(), LocalDate.of(2026, 1, 1), LocalDate.of(2026, 6, 30),
                1, "READY", Instant.parse("2026-07-04T00:00:00Z"),
                new FactualReportMetrics(1, 1, new BigDecimal("4.00")),
                new ReportMoviePage(0, false, null, List.of()));

        C5LocalPdfStore.StoredArtifact artifact = store.render(report, List.of());

        assertThat(artifact.opaquePath()).matches(".*artifact-[a-f0-9]{32}\\.pdf$");
        assertThat(store.read(artifact.opaquePath())).startsWith("%PDF-1.7".getBytes());
        store.delete(artifact.opaquePath());
        store.delete(artifact.opaquePath());
    }

    @Test
    void rejectsTraversalAndNonOpaqueDatabasePaths() {
        C5LocalPdfStore store = new C5LocalPdfStore(directory.toString());

        assertThatThrownBy(() -> store.read(directory.resolve("..")
                        .resolve("artifact-00000000000000000000000000000000.pdf").toString()))
                .isInstanceOf(C5LocalPdfStore.LocalArtifactException.class);
        assertThatThrownBy(() -> store.read(directory.resolve("report.pdf").toString()))
                .isInstanceOf(C5LocalPdfStore.LocalArtifactException.class);
    }
}
