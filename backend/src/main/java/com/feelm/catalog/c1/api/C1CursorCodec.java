package com.feelm.catalog.c1.api;

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
public final class C1CursorCodec {
    private final ObjectMapper objectMapper;
    private final byte[] signingKey;

    public C1CursorCodec(ObjectMapper objectMapper, @Value("${catalog.cursor-signing-key}") String signingKey) {
        this.objectMapper = objectMapper;
        this.signingKey = signingKey.getBytes(StandardCharsets.UTF_8);
    }

    public String encode(String scope, String revision, int offset) {
        try {
            byte[] payload = objectMapper.writeValueAsBytes(new Payload(scope, revision, offset));
            Base64.Encoder encoder = Base64.getUrlEncoder().withoutPadding();
            return encoder.encodeToString(payload) + "." + encoder.encodeToString(sign(payload));
        } catch (Exception exception) {
            throw new IllegalStateException("C1 cursor encoding failed", exception);
        }
    }

    public int decode(String token, String scope, String revision) {
        if (token == null || token.isBlank()) {
            return 0;
        }
        try {
            String[] parts = token.split("\\.", -1);
            if (parts.length != 2) {
                throw ApiException.invalidCursor();
            }
            Base64.Decoder decoder = Base64.getUrlDecoder();
            byte[] payload = decoder.decode(parts[0]);
            if (!MessageDigest.isEqual(decoder.decode(parts[1]), sign(payload))) {
                throw ApiException.invalidCursor();
            }
            Payload decoded = objectMapper.readValue(payload, Payload.class);
            if (!scope.equals(decoded.scope()) || !revision.equals(decoded.revision()) || decoded.offset() < 0) {
                throw ApiException.invalidCursor();
            }
            return decoded.offset();
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

    private record Payload(String scope, String revision, int offset) {
    }
}
