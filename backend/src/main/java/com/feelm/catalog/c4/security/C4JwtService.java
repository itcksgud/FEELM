package com.feelm.catalog.c4.security;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

import java.nio.charset.StandardCharsets;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.Signature;
import java.time.Instant;
import java.util.Base64;
import java.util.Optional;
import java.util.UUID;

@Component
@ConditionalOnProperty(name = "catalog.c4.enabled", havingValue = "true")
public final class C4JwtService {
    private final ObjectMapper objectMapper;
    private final KeyPair localKeyPair;

    public C4JwtService(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
        try {
            KeyPairGenerator generator = KeyPairGenerator.getInstance("RSA");
            generator.initialize(2048);
            this.localKeyPair = generator.generateKeyPair();
        } catch (Exception exception) {
            throw new IllegalStateException(exception);
        }
    }

    public String issue(UUID userId, UUID sessionId, Instant now) {
        try {
            String header = encode(objectMapper.writeValueAsBytes(objectMapper.createObjectNode()
                    .put("alg", "RS256").put("kid", "c4-local-ephemeral-v1").put("typ", "JWT")));
            String payload = encode(objectMapper.writeValueAsBytes(objectMapper.createObjectNode()
                    .put("iss", "feelm-local-c4").put("aud", "feelm-api")
                    .put("sub", userId.toString()).put("sid", sessionId.toString()).put("jti", UUID.randomUUID().toString())
                    .put("iat", now.getEpochSecond()).put("nbf", now.getEpochSecond()).put("exp", now.plusSeconds(600).getEpochSecond())));
            String signingInput = header + "." + payload;
            Signature signature = Signature.getInstance("SHA256withRSA");
            signature.initSign(localKeyPair.getPrivate());
            signature.update(signingInput.getBytes(StandardCharsets.US_ASCII));
            return signingInput + "." + encode(signature.sign());
        } catch (Exception exception) {
            throw new IllegalStateException(exception);
        }
    }

    public Optional<Claims> verify(String token, Instant now) {
        try {
            String[] parts = token.split("\\.");
            if (parts.length != 3) return Optional.empty();
            JsonNode header = objectMapper.readTree(Base64.getUrlDecoder().decode(parts[0]));
            if (!"RS256".equals(header.path("alg").asText()) || !"JWT".equals(header.path("typ").asText())
                    || !"c4-local-ephemeral-v1".equals(header.path("kid").asText())) return Optional.empty();
            Signature signature = Signature.getInstance("SHA256withRSA");
            signature.initVerify(localKeyPair.getPublic());
            signature.update((parts[0] + "." + parts[1]).getBytes(StandardCharsets.US_ASCII));
            if (!signature.verify(Base64.getUrlDecoder().decode(parts[2]))) return Optional.empty();
            JsonNode payload = objectMapper.readTree(Base64.getUrlDecoder().decode(parts[1]));
            long epoch = now.getEpochSecond();
            if (!"feelm-local-c4".equals(payload.path("iss").asText()) || !"feelm-api".equals(payload.path("aud").asText())
                    || payload.path("nbf").asLong() > epoch + 30 || payload.path("exp").asLong() < epoch - 30
                    || payload.path("exp").asLong() - payload.path("iat").asLong() > 600) return Optional.empty();
            return Optional.of(new Claims(UUID.fromString(payload.path("sub").asText()), UUID.fromString(payload.path("sid").asText())));
        } catch (Exception exception) {
            return Optional.empty();
        }
    }

    private static String encode(byte[] value) { return Base64.getUrlEncoder().withoutPadding().encodeToString(value); }
    public record Claims(UUID userId, UUID sessionId) {}
}
