package com.feelm.catalog.c2.input;

import com.feelm.catalog.c1.foundation.C1OutboxDispatcher;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Profile;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.Optional;
import java.util.Set;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Runtime bridge from committed C1 Rating outbox events to the C2 active-Rating projection.
 * Each poll is bounded, one JVM never overlaps its own poll, and database row claims use
 * FOR UPDATE SKIP LOCKED. Consumer failure is isolated by the dispatcher savepoint and is
 * retried with bounded exponential backoff before entering DEAD_LETTER.
 */
@Component
@Profile({"postgres", "local"})
@ConditionalOnProperty(name = "catalog.c1.outbox-worker.enabled", havingValue = "true")
public final class ActiveRatingInputOutboxWorker {
    private static final Set<String> RATING_EVENTS = Set.of(
            "RATING_CREATED", "RATING_UPDATED", "RATING_DELETED"
    );

    private final C1OutboxDispatcher dispatcher;
    private final PostgresActiveRatingInputProjection projection;
    private final int batchSize;
    private final AtomicBoolean polling = new AtomicBoolean();

    public ActiveRatingInputOutboxWorker(
            C1OutboxDispatcher dispatcher,
            PostgresActiveRatingInputProjection projection,
            @Value("${catalog.c1.outbox-worker.batch-size:25}") int batchSize
    ) {
        if (batchSize < 1 || batchSize > 100) {
            throw new IllegalArgumentException("outbox worker batch size must be between 1 and 100");
        }
        this.dispatcher = dispatcher;
        this.projection = projection;
        this.batchSize = batchSize;
    }

    @Scheduled(
            fixedDelayString = "${catalog.c1.outbox-worker.poll-delay-ms:1000}",
            initialDelayString = "${catalog.c1.outbox-worker.initial-delay-ms:5000}"
    )
    public int pollOnce() {
        if (!polling.compareAndSet(false, true)) {
            return 0;
        }
        try {
            int attempted = 0;
            while (attempted < batchSize) {
                Optional<C1OutboxDispatcher.DispatchResult> result =
                        dispatcher.dispatchNext(RATING_EVENTS, projection);
                if (result.isEmpty()) {
                    break;
                }
                attempted++;
            }
            return attempted;
        } finally {
            polling.set(false);
        }
    }
}
