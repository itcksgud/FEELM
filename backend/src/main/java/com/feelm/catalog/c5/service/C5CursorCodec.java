package com.feelm.catalog.c5.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.feelm.catalog.api.ApiException;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;

@Component
@Profile("local")
@ConditionalOnProperty(name = "c5.local.enabled", havingValue = "true")
public final class C5CursorCodec {
    private final ObjectMapper objectMapper;
    private final byte[] signingKey = new byte[32];

    public C5CursorCodec(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
        new SecureRandom().nextBytes(signingKey);
    }

    public String encode(String kind, String scope, List<String> lastKey) {
        try {
            ObjectNode payload = objectMapper.createObjectNode();
            payload.put("v", 1);
            payload.put("kind", kind);
            payload.put("scope", scopeTag(scope));
            ArrayNode last = payload.putArray("lastKey");
            lastKey.forEach(last::add);
            byte[] bytes = objectMapper.writeValueAsBytes(payload);
            Base64.Encoder encoder = Base64.getUrlEncoder().withoutPadding();
            return encoder.encodeToString(bytes) + "." + encoder.encodeToString(sign(bytes));
        } catch (Exception exception) {
            throw ApiException.invalidCursor();
        }
    }

    public Decoded decode(String value, String kind, String scope, int keySize) {
        if (value == null) return null;
        try {
            String[] parts = value.split("\\.", -1);
            if (parts.length != 2) throw ApiException.invalidCursor();
            Base64.Decoder decoder = Base64.getUrlDecoder();
            byte[] payload = decoder.decode(parts[0]);
            if (!MessageDigest.isEqual(decoder.decode(parts[1]), sign(payload))) {
                throw ApiException.invalidCursor();
            }
            JsonNode node = objectMapper.readTree(payload);
            if (node.path("v").asInt() != 1
                    || !kind.equals(node.path("kind").asText())
                    || !scopeTag(scope).equals(node.path("scope").asText())) {
                throw ApiException.invalidCursor();
            }
            List<String> key = array(node.path("lastKey"));
            if (key.size() != keySize) throw ApiException.invalidCursor();
            return new Decoded(key);
        } catch (ApiException exception) {
            throw exception;
        } catch (Exception exception) {
            throw ApiException.invalidCursor();
        }
    }

    private String scopeTag(String scope) throws Exception {
        return Base64.getUrlEncoder().withoutPadding()
                .encodeToString(hmac(("scope\n" + scope).getBytes(StandardCharsets.UTF_8)));
    }

    private byte[] sign(byte[] value) throws Exception {
        return hmac(value);
    }

    private byte[] hmac(byte[] value) throws Exception {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(signingKey, "HmacSHA256"));
        return mac.doFinal(value);
    }

    private static List<String> array(JsonNode node) {
        if (!node.isArray()) throw ApiException.invalidCursor();
        List<String> values = new ArrayList<>();
        node.forEach(item -> {
            if (!item.isTextual()) throw ApiException.invalidCursor();
            values.add(item.asText());
        });
        return List.copyOf(values);
    }

    public record Decoded(List<String> lastKey) {
        public Decoded {
            lastKey = List.copyOf(lastKey);
        }
    }
}
