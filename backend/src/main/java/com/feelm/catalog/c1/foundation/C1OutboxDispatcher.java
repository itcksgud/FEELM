package com.feelm.catalog.c1.foundation;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.context.annotation.Profile;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionStatus;
import org.springframework.transaction.support.TransactionTemplate;

import java.time.Clock;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.ArrayList;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;

/**
 * Dispatches one committed outbox row in its own transaction. Implementations of {@link Consumer}
 * must deduplicate downstream application by eventId because a process can fail after delivery and
 * before the local PROCESSED update.
 */
@Component
@Profile({"postgres", "local"})
public final class C1OutboxDispatcher {
    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;
    private final Clock clock;
    private final PlatformTransactionManager transactionManager;
    private final int maxAttempts;

    public C1OutboxDispatcher(
            JdbcTemplate jdbc,
            ObjectMapper objectMapper,
            Clock clock,
            PlatformTransactionManager transactionManager,
            @Value("${catalog.c1.outbox-worker.max-attempts:8}") int maxAttempts
    ) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
        this.clock = clock;
        this.transactionManager = transactionManager;
        if (maxAttempts < 1 || maxAttempts > 32) {
            throw new IllegalArgumentException("outbox max attempts must be between 1 and 32");
        }
        this.maxAttempts = maxAttempts;
    }

    public DispatchResult dispatchOne(UUID eventId, Consumer consumer) {
        if (eventId == null || consumer == null) {
            throw new IllegalArgumentException("outbox eventId and consumer are required");
        }
        TransactionTemplate transaction = new TransactionTemplate(transactionManager);
        DispatchResult result = transaction.execute(status -> dispatchLocked(eventId, consumer, status));
        return result == null ? new DispatchResult(eventId, "NOT_READY", 0) : result;
    }

    /**
     * Claims the oldest ready event matching the route with a transaction-scoped row lock.
     * SKIP LOCKED lets competing workers make progress without delivering the same row.
     * A process failure releases the claim by rolling the transaction back.
     */
    public Optional<DispatchResult> dispatchNext(Set<String> eventTypes, Consumer consumer) {
        if (eventTypes == null || eventTypes.isEmpty() || eventTypes.size() > 32 || consumer == null
                || eventTypes.stream().anyMatch(value -> value == null || !value.matches("^[A-Z][A-Z0-9_]{2,63}$"))) {
            throw new IllegalArgumentException("outbox route and consumer are required");
        }
        List<String> orderedTypes = eventTypes.stream().sorted().toList();
        String placeholders = String.join(",", java.util.Collections.nCopies(orderedTypes.size(), "?"));
        TransactionTemplate transaction = new TransactionTemplate(transactionManager);
        DispatchResult result = transaction.execute(status -> {
            OffsetDateTime current = OffsetDateTime.ofInstant(clock.instant(), ZoneOffset.UTC);
            List<Object> parameters = new ArrayList<>();
            parameters.add(current);
            parameters.addAll(orderedTypes);
            List<UUID> candidates = jdbc.query("""
                    SELECT event_id
                      FROM domain_outbox
                     WHERE status IN ('PENDING', 'FAILED')
                       AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                       AND event_type IN (%s)
                     ORDER BY occurred_at, event_id
                     FOR UPDATE SKIP LOCKED
                     LIMIT 1
                    """.formatted(placeholders),
                    (rs, row) -> rs.getObject(1, UUID.class), parameters.toArray());
            return candidates.isEmpty() ? null : dispatchLocked(candidates.get(0), consumer, status);
        });
        return Optional.ofNullable(result);
    }

    private DispatchResult dispatchLocked(UUID eventId, Consumer consumer, TransactionStatus transactionStatus) {
        OffsetDateTime current = OffsetDateTime.ofInstant(clock.instant(), ZoneOffset.UTC);
        List<Message> values = jdbc.query("""
                SELECT event_id, aggregate_type, aggregate_id, event_type, schema_version,
                       payload, occurred_at, attempt_count
                  FROM domain_outbox
                 WHERE event_id = ?
                   AND status IN ('PENDING', 'FAILED')
                   AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                 FOR UPDATE
                """, (rs, row) -> {
            try {
                return new Message(
                        rs.getObject("event_id", UUID.class), rs.getString("aggregate_type"),
                        rs.getObject("aggregate_id", UUID.class), rs.getString("event_type"),
                        rs.getInt("schema_version"), objectMapper.readTree(rs.getString("payload")),
                        rs.getObject("occurred_at", OffsetDateTime.class), rs.getInt("attempt_count")
                );
            } catch (Exception exception) {
                throw new C1FoundationException("OUTBOX_PAYLOAD_INVALID", "outbox payload is invalid");
            }
        }, eventId, current);
        if (values.isEmpty()) {
            return new DispatchResult(eventId, "NOT_READY", 0);
        }
        Message message = values.get(0);
        int attempt = message.attemptCount() + 1;
        jdbc.update("""
                UPDATE domain_outbox
                   SET status = 'PROCESSING', attempt_count = ?, next_attempt_at = NULL
                 WHERE event_id = ?
                """, attempt, eventId);
        Object consumerSavepoint = transactionStatus.createSavepoint();
        try {
            consumer.consume(message);
            jdbc.update("""
                    UPDATE domain_outbox
                       SET status = 'PROCESSED', processed_at = ?, next_attempt_at = NULL
                     WHERE event_id = ?
                    """, current, eventId);
            transactionStatus.releaseSavepoint(consumerSavepoint);
            return new DispatchResult(eventId, "PROCESSED", attempt);
        } catch (Exception failure) {
            transactionStatus.rollbackToSavepoint(consumerSavepoint);
            transactionStatus.releaseSavepoint(consumerSavepoint);
            boolean exhausted = attempt >= maxAttempts;
            String status = exhausted ? "DEAD_LETTER" : "FAILED";
            jdbc.update("""
                    UPDATE domain_outbox
                       SET status = ?, processed_at = NULL, next_attempt_at = ?
                     WHERE event_id = ?
                    """, status, exhausted ? null : current.plus(backoff(attempt)), eventId);
            return new DispatchResult(eventId, status, attempt);
        }
    }

    private Duration backoff(int attempt) {
        long seconds = Math.min(3600, 30L * (1L << Math.min(7, Math.max(0, attempt - 1))));
        return Duration.ofSeconds(seconds);
    }

    @FunctionalInterface
    public interface Consumer {
        void consume(Message message) throws Exception;
    }

    public record Message(
            UUID eventId,
            String aggregateType,
            UUID aggregateId,
            String eventType,
            int schemaVersion,
            JsonNode payload,
            OffsetDateTime occurredAt,
            int attemptCount
    ) {
    }

    public record DispatchResult(UUID eventId, String status, int attemptCount) {
    }
}
