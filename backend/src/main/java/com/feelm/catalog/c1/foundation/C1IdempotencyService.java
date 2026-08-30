package com.feelm.catalog.c1.foundation;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.springframework.context.annotation.Profile;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

import java.security.MessageDigest;
import java.time.Clock;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.function.Supplier;

@Service
@Profile({"postgres", "local"})
public final class C1IdempotencyService {
    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;
    private final Clock clock;
    private final PlatformTransactionManager transactionManager;

    public C1IdempotencyService(
            JdbcTemplate jdbc,
            ObjectMapper objectMapper,
            Clock clock,
            PlatformTransactionManager transactionManager
    ) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
        this.clock = clock;
        this.transactionManager = transactionManager;
    }

    public ExecutionResult execute(
            UUID actorUserId,
            String operationCode,
            String idempotencyKey,
            JsonNode requestBody,
            Supplier<MutationResponse> mutation
    ) {
        requireIdentity(actorUserId, operationCode, idempotencyKey, requestBody, mutation);
        String requestHash = sha256(canonicalBytes(requestBody));
        TransactionTemplate transaction = new TransactionTemplate(transactionManager);
        ExecutionResult result = transaction.execute(status -> {
            jdbc.query(
                    "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                    (resultSet, rowNumber) -> 0,
                    actorUserId + ":" + operationCode + ":" + idempotencyKey
            );
            StoredRecord existing = find(actorUserId, operationCode, idempotencyKey);
            if (existing != null) {
                if (!existing.requestHash().equals(requestHash)) {
                    throw new C1FoundationException(
                            "IDEMPOTENCY_KEY_REUSED",
                            "Idempotency-Key was reused with a different request"
                    );
                }
                return new ExecutionResult(
                        true,
                        new MutationResponse(existing.responseStatus(), existing.responseBody(), existing.resourceId())
                );
            }

            MutationResponse response = mutation.get();
            Instant createdAt = clock.instant();
            jdbc.update("""
                    INSERT INTO idempotency_record (
                        actor_user_id, operation_code, idempotency_key, request_hash,
                        response_status, response_body, resource_id, created_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?::jsonb, ?, ?, NULL)
                    """,
                    actorUserId,
                    operationCode,
                    idempotencyKey,
                    requestHash,
                    response.status(),
                    response.body().toString(),
                    response.resourceId(),
                    OffsetDateTime.ofInstant(createdAt, ZoneOffset.UTC)
            );
            return new ExecutionResult(false, response);
        });
        if (result == null) {
            throw new C1FoundationException("IDEMPOTENCY_TRANSACTION_FAILED", "idempotency transaction returned no result");
        }
        return result;
    }

    private StoredRecord find(UUID actorUserId, String operationCode, String idempotencyKey) {
        List<StoredRecord> records = jdbc.query("""
                SELECT request_hash, response_status, response_body, resource_id
                  FROM idempotency_record
                 WHERE actor_user_id = ? AND operation_code = ? AND idempotency_key = ?
                """, (resultSet, rowNumber) -> {
            try {
                return new StoredRecord(
                        resultSet.getString("request_hash"),
                        resultSet.getInt("response_status"),
                        objectMapper.readTree(resultSet.getString("response_body")),
                        resultSet.getObject("resource_id", UUID.class)
                );
            } catch (Exception exception) {
                throw new C1FoundationException("IDEMPOTENCY_RESULT_INVALID", "stored idempotency result is invalid");
            }
        }, actorUserId, operationCode, idempotencyKey);
        return records.isEmpty() ? null : records.get(0);
    }

    private byte[] canonicalBytes(JsonNode requestBody) {
        try {
            return objectMapper.writeValueAsBytes(canonicalize(requestBody));
        } catch (Exception exception) {
            throw new C1FoundationException("INVALID_CANONICAL_REQUEST", "request could not be canonicalized");
        }
    }

    private JsonNode canonicalize(JsonNode node) {
        if (node.isObject()) {
            ObjectNode canonical = objectMapper.createObjectNode();
            List<Map.Entry<String, JsonNode>> fields = new ArrayList<>();
            Iterator<Map.Entry<String, JsonNode>> iterator = node.properties().iterator();
            iterator.forEachRemaining(fields::add);
            fields.stream().sorted(Map.Entry.comparingByKey())
                    .forEach(entry -> canonical.set(entry.getKey(), canonicalize(entry.getValue())));
            return canonical;
        }
        if (node.isArray()) {
            ArrayNode canonical = objectMapper.createArrayNode();
            node.forEach(item -> canonical.add(canonicalize(item)));
            return canonical;
        }
        return node.deepCopy();
    }

    private String sha256(byte[] value) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value));
        } catch (Exception exception) {
            throw new C1FoundationException("REQUEST_HASH_FAILED", "request hash could not be created");
        }
    }

    private void requireIdentity(
            UUID actorUserId,
            String operationCode,
            String idempotencyKey,
            JsonNode requestBody,
            Supplier<MutationResponse> mutation
    ) {
        if (actorUserId == null || requestBody == null || mutation == null) {
            throw new C1FoundationException("INVALID_IDEMPOTENCY_REQUEST", "idempotency identity is required");
        }
        if (operationCode == null || !operationCode.matches("^[A-Z][A-Z0-9_]{2,63}$")) {
            throw new C1FoundationException("INVALID_IDEMPOTENCY_REQUEST", "operation code is invalid");
        }
        if (idempotencyKey == null || !idempotencyKey.matches("^[!-~]{8,128}$")) {
            throw new C1FoundationException("INVALID_IDEMPOTENCY_KEY", "Idempotency-Key is invalid");
        }
    }

    public record MutationResponse(int status, JsonNode body, UUID resourceId) {
        public MutationResponse {
            if (status < 100 || status > 599 || body == null) {
                throw new IllegalArgumentException("mutation response is invalid");
            }
        }
    }

    public record ExecutionResult(boolean replayed, MutationResponse response) {
    }

    private record StoredRecord(String requestHash, int responseStatus, JsonNode responseBody, UUID resourceId) {
    }
}
