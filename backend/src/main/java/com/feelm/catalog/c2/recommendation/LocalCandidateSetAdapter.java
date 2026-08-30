package com.feelm.catalog.c2.recommendation;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HexFormat;
import java.util.List;
import java.util.Set;
import java.util.TreeMap;
import java.util.UUID;
import java.util.regex.Pattern;

@Component
public final class LocalCandidateSetAdapter implements CandidateSetPort {
    private static final Pattern SHA256 = Pattern.compile("[0-9a-f]{64}");
    private static final Set<String> POINTER_FIELDS = Set.of(
            "schemaVersion", "candidateSetVersion", "payloadSha256", "payload", "quarantine"
    );
    private static final Set<String> PAYLOAD_FIELDS = Set.of(
            "schemaVersion", "candidateSetVersion", "catalogVersion", "mappingPayloadSha256",
            "compatibilityId", "producerPolicy", "movieIds"
    );

    private final ObjectMapper mapper;
    private final String configuredRoot;

    public LocalCandidateSetAdapter(
            ObjectMapper mapper,
            @Value("${catalog.c2.recommender.candidate-store-path:}") String configuredRoot
    ) {
        this.mapper = mapper;
        this.configuredRoot = configuredRoot;
    }

    @Override
    public Snapshot loadActive() {
        try {
            if (configuredRoot == null || configuredRoot.isBlank()) {
                throw invalid();
            }
            Path root = Path.of(configuredRoot).toAbsolutePath().normalize();
            JsonNode pointer = mapper.readTree(Files.readAllBytes(root.resolve("active.json")));
            requireObjectFields(pointer, POINTER_FIELDS);
            if (pointer.path("schemaVersion").asInt(-1) != 1) throw invalid();
            String payloadName = requiredText(pointer, "payload");
            Path relative = Path.of(payloadName);
            if (relative.isAbsolute() || relative.getNameCount() != 2
                    || !"versions".equals(relative.getName(0).toString())
                    || !relative.getFileName().toString().matches("[0-9a-f]{64}\\.json")) {
                throw invalid();
            }
            Path payloadPath = root.resolve(relative).normalize();
            if (!payloadPath.startsWith(root)) throw invalid();
            byte[] bytes = Files.readAllBytes(payloadPath);
            String payloadSha = sha256(bytes);
            if (!payloadSha.equals(requiredSha(pointer, "payloadSha256"))) throw invalid();

            JsonNode payload = mapper.readTree(bytes);
            requireObjectFields(payload, PAYLOAD_FIELDS);
            if (payload.path("schemaVersion").asInt(-1) != 1
                    || !"GLOBAL_VERIFIED_CATALOG_V1".equals(requiredText(payload, "producerPolicy"))) {
                throw invalid();
            }
            if (!requiredText(pointer, "candidateSetVersion")
                    .equals(requiredText(payload, "candidateSetVersion"))) throw invalid();
            String mappingSha = requiredSha(payload, "mappingPayloadSha256");
            String catalogVersion = requiredText(payload, "catalogVersion");
            String compatibilityId = requiredText(payload, "compatibilityId");
            JsonNode idsNode = payload.path("movieIds");
            if (!idsNode.isArray()) throw invalid();
            List<UUID> ids = new ArrayList<>();
            for (JsonNode node : idsNode) {
                if (!node.isTextual()) throw invalid();
                UUID id = UUID.fromString(node.textValue());
                if (!id.toString().equals(node.textValue())) throw invalid();
                ids.add(id);
            }
            List<UUID> sorted = ids.stream().sorted(Comparator.comparing(UUID::toString)).toList();
            if (!ids.equals(sorted) || ids.stream().distinct().count() != ids.size()) throw invalid();

            String expectedVersion = candidateVersion(catalogVersion, mappingSha, compatibilityId, ids);
            String actualVersion = requiredText(payload, "candidateSetVersion");
            if (!actualVersion.equals(expectedVersion)
                    || !(actualVersion.substring("sha256:".length()) + ".json")
                    .equals(relative.getFileName().toString())) throw invalid();
            byte[] canonical = canonicalPayload(payload, actualVersion);
            if (!java.util.Arrays.equals(bytes, canonical)) throw invalid();
            return new Snapshot(actualVersion, catalogVersion, mappingSha, compatibilityId, ids);
        } catch (C2RecommendationFailure failure) {
            throw failure;
        } catch (Exception ignored) {
            throw invalid();
        }
    }

    private String candidateVersion(String catalog, String mappingSha, String compatibility, List<UUID> ids)
            throws Exception {
        TreeMap<String, Object> seed = new TreeMap<>();
        seed.put("candidateSetVersion", "");
        seed.put("catalogVersion", catalog);
        seed.put("compatibilityId", compatibility);
        seed.put("mappingPayloadSha256", mappingSha);
        seed.put("movieIds", ids.stream().map(UUID::toString).toList());
        seed.put("producerPolicy", "GLOBAL_VERIFIED_CATALOG_V1");
        seed.put("schemaVersion", 1);
        return "sha256:" + sha256((mapper.writeValueAsString(seed) + "\n").getBytes(StandardCharsets.UTF_8));
    }

    private byte[] canonicalPayload(JsonNode payload, String version) throws Exception {
        TreeMap<String, Object> value = new TreeMap<>();
        value.put("candidateSetVersion", version);
        value.put("catalogVersion", payload.get("catalogVersion").textValue());
        value.put("compatibilityId", payload.get("compatibilityId").textValue());
        value.put("mappingPayloadSha256", payload.get("mappingPayloadSha256").textValue());
        List<String> ids = new ArrayList<>();
        payload.get("movieIds").forEach(node -> ids.add(node.textValue()));
        value.put("movieIds", ids);
        value.put("producerPolicy", "GLOBAL_VERIFIED_CATALOG_V1");
        value.put("schemaVersion", 1);
        return (mapper.writeValueAsString(value) + "\n").getBytes(StandardCharsets.UTF_8);
    }

    private void requireObjectFields(JsonNode node, Set<String> fields) {
        if (!node.isObject()) throw invalid();
        Set<String> actual = new java.util.HashSet<>();
        node.fieldNames().forEachRemaining(actual::add);
        if (!actual.equals(fields)) throw invalid();
    }

    private String requiredText(JsonNode node, String field) {
        JsonNode value = node.get(field);
        if (value == null || !value.isTextual() || value.textValue().isBlank()) throw invalid();
        return value.textValue();
    }

    private String requiredSha(JsonNode node, String field) {
        String value = requiredText(node, field);
        if (!SHA256.matcher(value).matches()) throw invalid();
        return value;
    }

    private String sha256(byte[] bytes) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
    }

    private C2RecommendationFailure invalid() {
        return new C2RecommendationFailure(
                C2RecommendationFailure.Code.CANDIDATE_ARTIFACT_INVALID, false
        );
    }
}
