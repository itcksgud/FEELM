package com.feelm.catalog.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.feelm.catalog.api.ApiException;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Base64;

@Component
final class CursorCodec {
    private final ObjectMapper objectMapper;
    private final byte[] signingKey;

    CursorCodec(ObjectMapper objectMapper, @Value("${catalog.cursor-signing-key}") String signingKey) {
        this.objectMapper = objectMapper;
        this.signingKey = signingKey.getBytes(StandardCharsets.UTF_8);
    }

    String encode(String catalogVersion, String filterHash, int offset) {
        try {
            byte[] payload = objectMapper.writeValueAsBytes(new CursorPayload(catalogVersion, filterHash, offset));
            byte[] signature = sign(payload);
            Base64.Encoder encoder = Base64.getUrlEncoder().withoutPadding();
            return encoder.encodeToString(payload) + "." + encoder.encodeToString(signature);
        } catch (Exception exception) {
            throw new IllegalStateException("cursor encoding failed", exception);
        }
    }

    int decodeOffset(String token, String expectedCatalogVersion, String expectedFilterHash) {
        try {
            String[] parts = token.split("\\.", -1);
            if (parts.length != 2) {
                throw ApiException.invalidCursor();
            }
            Base64.Decoder decoder = Base64.getUrlDecoder();
            byte[] payloadBytes = decoder.decode(parts[0]);
            byte[] signature = decoder.decode(parts[1]);
            if (!MessageDigest.isEqual(signature, sign(payloadBytes))) {
                throw ApiException.invalidCursor();
            }
            CursorPayload payload = objectMapper.readValue(payloadBytes, CursorPayload.class);
            if (!expectedCatalogVersion.equals(payload.catalogVersion())
                    || !expectedFilterHash.equals(payload.filterHash())
                    || payload.offset() < 0) {
                throw ApiException.invalidCursor();
            }
            return payload.offset();
        } catch (ApiException exception) {
            throw exception;
        } catch (Exception exception) {
            throw ApiException.invalidCursor();
        }
    }

    private byte[] sign(byte[] payload) throws Exception {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(signingKey, "HmacSHA256"));
        return mac.doFinal(payload);
    }

    private record CursorPayload(String catalogVersion, String filterHash, int offset) {
    }
}
