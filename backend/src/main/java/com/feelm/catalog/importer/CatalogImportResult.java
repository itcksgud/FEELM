package com.feelm.catalog.importer;

import java.util.Map;

public record CatalogImportResult(
        Status status,
        String catalogVersion,
        String sourceHash,
        Map<String, Long> recordCounts
) {
    public CatalogImportResult {
        recordCounts = Map.copyOf(recordCounts);
    }

    public enum Status {
        PUBLISHED,
        ALREADY_IMPORTED
    }
}
