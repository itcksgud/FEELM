package com.feelm.catalog.importer;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;

import java.nio.file.Path;

@Component
@Profile("postgres")
final class CatalogImportStartupRunner implements ApplicationRunner {
    private final CatalogImportService importService;
    private final String importPath;

    CatalogImportStartupRunner(
            CatalogImportService importService,
            @Value("${catalog.import-path:}") String importPath
    ) {
        this.importService = importService;
        this.importPath = importPath;
    }

    @Override
    public void run(ApplicationArguments args) {
        if (importPath != null && !importPath.isBlank()) {
            importService.importArtifact(Path.of(importPath).toAbsolutePath().normalize());
        }
    }
}
