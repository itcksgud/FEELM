package com.feelm.catalog.c3;

import org.junit.jupiter.api.Test;

import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;

class LocalComposeLoopbackTopologyTest {
    @Test
    void defaultComposeKeepsSpringOnLoopbackBehindSameNamespaceNginx() throws Exception {
        Path root = repositoryRoot();
        String compose = Files.readString(root.resolve("docker-compose.yml"));
        String nginx = Files.readString(root.resolve("frontend/nginx.conf"));
        String composeProfile = Files.readString(root.resolve("backend/src/main/resources/application-compose.yml"));
        String localProfile = Files.readString(root.resolve("backend/src/main/resources/application-local.yml"));

        assertThat(compose)
                .contains("SERVER_ADDRESS: 127.0.0.1")
                .contains("C3_ENABLED: \"true\"")
                .contains("C3_LOCAL_BIND_ADDRESS: 127.0.0.1")
                .contains("C6_LOCAL_ENABLED: \"true\"")
                .contains("C6_LOCAL_EXPERIMENT_ENABLED: \"true\"")
                .contains("C5_LOCAL_ENABLED: \"true\"")
                .contains("C5_ARTIFACT_DIRECTORY: /tmp/feelm-c5-artifacts")
                .contains("network_mode: service:backend")
                .contains("127.0.0.1:${FRONTEND_HOST_PORT:-5173}:80")
                .contains("127.0.0.1:${BACKEND_HOST_PORT:-8080}:8081");
        assertThat(nginx)
                .contains("listen 80;")
                .contains("listen 8081;")
                .contains("proxy_pass http://127.0.0.1:8080;")
                .doesNotContain("proxy_pass http://backend:8080");
        assertThat(composeProfile)
                .contains("address: ${SERVER_ADDRESS:127.0.0.1}")
                .contains("enabled: ${C3_ENABLED:false}")
                .doesNotContain("address: 0.0.0.0");
        assertThat(localProfile).contains("enabled: ${C3_ENABLED:false}");
        assertThat(localProfile).contains("enabled: ${C6_LOCAL_ENABLED:false}");
    }

    private static Path repositoryRoot() {
        Path current = Path.of("").toAbsolutePath().normalize();
        if (Files.exists(current.resolve("docker-compose.yml"))) return current;
        Path parent = current.getParent();
        if (parent != null && Files.exists(parent.resolve("docker-compose.yml"))) return parent;
        throw new IllegalStateException("Cannot locate FEELM-standalone repository root");
    }
}
