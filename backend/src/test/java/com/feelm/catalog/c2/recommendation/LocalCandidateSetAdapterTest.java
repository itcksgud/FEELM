package com.feelm.catalog.c2.recommendation;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.List;
import java.util.TreeMap;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class LocalCandidateSetAdapterTest {
    private static final UUID MOVIE = UUID.fromString("6b226903-0ca4-4f5a-9bf0-50d6cedd224c");
    private final ObjectMapper mapper = new ObjectMapper();

    @Test
    void loadsCanonicalActivePayloadAndPreservesVersions(@TempDir Path root) throws Exception {
        CandidateSetPort.Snapshot snapshot = publish(root, false);
        assertThat(snapshot.catalogVersion()).isEqualTo("catalog-fixture-20260829-01");
        assertThat(snapshot.mappingPayloadSha256()).isEqualTo("b".repeat(64));
        assertThat(snapshot.movieIds()).containsExactly(MOVIE);
        assertThat(snapshot.candidateSetVersion()).startsWith("sha256:");
    }

    @Test
    void rejectsChecksumMismatchWithoutExposingPath(@TempDir Path root) throws Exception {
        publish(root, false);
        Files.writeString(root.resolve("versions").resolve(
                Files.list(root.resolve("versions")).findFirst().orElseThrow().getFileName()), "{}\n");
        assertThatThrownBy(() -> new LocalCandidateSetAdapter(mapper, root.toString()).loadActive())
                .isInstanceOfSatisfying(C2RecommendationFailure.class, failure -> {
                    assertThat(failure.code()).isEqualTo(C2RecommendationFailure.Code.CANDIDATE_ARTIFACT_INVALID);
                    assertThat(failure.getMessage()).doesNotContain(root.toString());
                });
    }

    private CandidateSetPort.Snapshot publish(Path root, boolean unused) throws Exception {
        Files.createDirectories(root.resolve("versions"));
        TreeMap<String, Object> seed = payload("");
        String version = "sha256:" + sha((mapper.writeValueAsString(seed) + "\n").getBytes(StandardCharsets.UTF_8));
        byte[] payload = (mapper.writeValueAsString(payload(version)) + "\n").getBytes(StandardCharsets.UTF_8);
        String digest = version.substring("sha256:".length());
        Files.write(root.resolve("versions").resolve(digest + ".json"), payload);
        TreeMap<String, Object> pointer = new TreeMap<>();
        pointer.put("candidateSetVersion", version);
        pointer.put("payload", "versions/" + digest + ".json");
        pointer.put("payloadSha256", sha(payload));
        pointer.put("quarantine", "quarantine/" + digest + ".json");
        pointer.put("schemaVersion", 1);
        Files.writeString(root.resolve("active.json"), mapper.writeValueAsString(pointer) + "\n");
        return new LocalCandidateSetAdapter(mapper, root.toString()).loadActive();
    }

    private TreeMap<String, Object> payload(String version) {
        TreeMap<String, Object> payload = new TreeMap<>();
        payload.put("candidateSetVersion", version);
        payload.put("catalogVersion", "catalog-fixture-20260829-01");
        payload.put("compatibilityId", "fixture-family-v1");
        payload.put("mappingPayloadSha256", "b".repeat(64));
        payload.put("movieIds", List.of(MOVIE.toString()));
        payload.put("producerPolicy", "GLOBAL_VERIFIED_CATALOG_V1");
        payload.put("schemaVersion", 1);
        return payload;
    }

    private String sha(byte[] bytes) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
    }
}
