package com.feelm.catalog.c3.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.feelm.catalog.api.ApiException;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;
import java.util.UUID;

@Component
@Profile("local")
@ConditionalOnProperty(name = "catalog.c3.enabled", havingValue = "true")
public final class C3CursorCodec {
    private final ObjectMapper objectMapper;
    private final byte[] signingKey;

    public C3CursorCodec(
            ObjectMapper objectMapper,
            @Value("${catalog.cursor-signing-key}") String signingKey
    ) {
        this.objectMapper = objectMapper;
        this.signingKey = signingKey.getBytes(StandardCharsets.UTF_8);
    }

    public String encode(String kind, UUID actor, List<String> bindings, List<String> lastKey) {
        try {
            ObjectNode payload = objectMapper.createObjectNode();
            payload.put("v", 1);
            payload.put("kind", kind);
            payload.put("actor", actor.toString());
            ArrayNode bindingArray = payload.putArray("bindings");
            bindings.forEach(bindingArray::add);
            ArrayNode lastArray = payload.putArray("lastKey");
            lastKey.forEach(lastArray::add);
            byte[] bytes = objectMapper.writeValueAsBytes(payload);
            Base64.Encoder encoder = Base64.getUrlEncoder().withoutPadding();
            return encoder.encodeToString(bytes) + "." + encoder.encodeToString(sign(bytes));
        } catch (Exception exception) {
            throw ApiException.invalidCursor();
        }
    }

    public Decoded decode(String value, String kind, UUID actor, List<String> bindings, int keySize) {
        if (value == null) {
            return null;
        }
        try {
            String[] parts = value.split("\\.", -1);
            if (parts.length != 2) throw ApiException.invalidCursor();
            Base64.Decoder decoder = Base64.getUrlDecoder();
            byte[] payload = decoder.decode(parts[0]);
            byte[] signature = decoder.decode(parts[1]);
            if (!MessageDigest.isEqual(signature, sign(payload))) throw ApiException.invalidCursor();
            JsonNode node = objectMapper.readTree(payload);
            if (node.path("v").asInt() != 1
                    || !kind.equals(node.path("kind").asText())
                    || !actor.toString().equals(node.path("actor").asText())
                    || !array(node.path("bindings")).equals(bindings)) {
                throw ApiException.invalidCursor();
            }
            List<String> lastKey = array(node.path("lastKey"));
            if (lastKey.size() != keySize) throw ApiException.invalidCursor();
            return new Decoded(lastKey);
        } catch (ApiException exception) {
            throw exception;
        } catch (Exception exception) {
            throw ApiException.invalidCursor();
        }
    }

    private byte[] sign(byte[] value) throws Exception {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(signingKey, "HmacSHA256"));
        return mac.doFinal(value);
    }

    private static List<String> array(JsonNode node) {
        if (!node.isArray()) throw ApiException.invalidCursor();
        List<String> result = new ArrayList<>();
        for (JsonNode item : node) {
            if (!item.isTextual()) throw ApiException.invalidCursor();
            result.add(item.asText());
        }
        return List.copyOf(result);
    }

    public record Decoded(List<String> lastKey) {
        public Decoded {
            lastKey = List.copyOf(lastKey);
        }
    }
}
