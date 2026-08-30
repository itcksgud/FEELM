package com.feelm.catalog.c1.service;

import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;

@Component
@Profile({"postgres", "local"})
final class NoOpC1MutationFaultInjector implements C1MutationFaultInjector {
    @Override
    public void checkpoint(Checkpoint checkpoint) {
        // Production mutations never inject failures.
    }
}
