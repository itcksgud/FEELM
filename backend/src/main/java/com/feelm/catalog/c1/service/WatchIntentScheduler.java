package com.feelm.catalog.c1.service;

import org.springframework.context.annotation.Profile;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Component
@EnableScheduling
@Profile({"postgres", "local"})
public final class WatchIntentScheduler {
    private final C1Service service;

    public WatchIntentScheduler(C1Service service) {
        this.service = service;
    }

    @Scheduled(fixedDelayString = "${catalog.c1.watch-intent-scheduler-delay-ms:60000}")
    public void advance() {
        service.advanceWatchIntents();
    }
}
