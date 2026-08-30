package com.feelm.catalog.c1.service;

/**
 * Test seam for proving transaction rollback at concrete C1 mutation checkpoints.
 * The production implementation is deliberately a no-op; tests replace it with a
 * primary bean that fails at exactly one checkpoint.
 */
@FunctionalInterface
public interface C1MutationFaultInjector {
    void checkpoint(Checkpoint checkpoint);

    enum Checkpoint {
        AFTER_CONFIRMATION_STATUS_UPDATED,
        AFTER_RATING_WRITTEN,
        AFTER_POPCORN_WRITTEN,
        AFTER_DELETE_AGGREGATES_REVERSED
    }
}
