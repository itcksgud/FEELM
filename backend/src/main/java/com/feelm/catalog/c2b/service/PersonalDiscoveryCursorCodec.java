package com.feelm.catalog.c2b.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.feelm.catalog.api.ApiException;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.Base64;
import java.util.UUID;

@Component
public final class PersonalDiscoveryCursorCodec {
    private final ObjectMapper objectMapper;
    private final byte[] signingKey;

    public PersonalDiscoveryCursorCodec(
            ObjectMapper objectMapper,
            @Value("${catalog.cursor-signing-key}") String signingKey
    ) {
        this.objectMapper = objectMapper;
        this.signingKey = signingKey.getBytes(StandardCharsets.UTF_8);
    }

    public String encode(UUID actor, UUID deliveryId, int revision, int offset, Instant expiresAt) {
        try {
            byte[] payload = objectMapper.writeValueAsBytes(new Payload(
                    actor, deliveryId, revision, offset, expiresAt.toEpochMilli()
            ));
            Base64.Encoder encoder = Base64.getUrlEncoder().withoutPadding();
            return encoder.encodeToString(payload) + "." + encoder.encodeToString(sign(payload));
        } catch (Exception exception) {
            throw new IllegalStateException("personal discovery cursor encoding failed", exception);
        }
    }

    public Decoded decode(String token, UUID actor, UUID deliveryId, Instant now) {
        if (token == null || token.isBlank()) {
            throw invalid();
        }
        try {
            String[] parts = token.split("\\.", -1);
            if (parts.length != 2) {
                throw invalid();
            }
            Base64.Decoder decoder = Base64.getUrlDecoder();
            byte[] payload = decoder.decode(parts[0]);
            if (!MessageDigest.isEqual(decoder.decode(parts[1]), sign(payload))) {
                throw invalid();
            }
            Payload value = objectMapper.readValue(payload, Payload.class);
            if (!actor.equals(value.actor()) || !deliveryId.equals(value.deliveryId())) {
                throw notFound();
            }
            if (value.revision() < 1 || value.offset() < 0) {
                throw invalid();
            }
            if (!Instant.ofEpochMilli(value.expiresAtEpochMilli()).isAfter(now)) {
                throw stale();
            }
            return new Decoded(value.revision(), value.offset());
        } catch (ApiException exception) {
            throw exception;
        } catch (Exception exception) {
            throw invalid();
        }
    }

    private byte[] sign(byte[] value) throws Exception {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(signingKey, "HmacSHA256"));
        return mac.doFinal(value);
    }

    private static ApiException invalid() {
        return new ApiException(HttpStatus.BAD_REQUEST, "INVALID_RECOMMENDATION_CURSOR", "추가 추천 요청을 확인해 주세요.");
    }

    private static ApiException stale() {
        return new ApiException(HttpStatus.CONFLICT, "RECOMMENDATION_DELIVERY_STALE", "추천 목록을 새로 확인해 주세요.");
    }

    private static ApiException notFound() {
        return new ApiException(HttpStatus.NOT_FOUND, "RESOURCE_NOT_FOUND", "리소스를 찾을 수 없어요.");
    }

    public record Decoded(int revision, int offset) {
    }

    private record Payload(UUID actor, UUID deliveryId, int revision, int offset, long expiresAtEpochMilli) {
    }
}
