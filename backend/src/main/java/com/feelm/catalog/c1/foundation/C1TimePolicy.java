package com.feelm.catalog.c1.foundation;

import org.springframework.stereotype.Component;

import java.time.Duration;
import java.time.Instant;

@Component
public final class C1TimePolicy {
    public static final Duration CONFIRMATION_DELAY = Duration.ofHours(48);
    public static final Duration EXPIRY_DELAY = Duration.ofDays(7);

    public Window fromFirstActiveClick(Instant clickedAt) {
        return new Window(clickedAt, clickedAt.plus(CONFIRMATION_DELAY), clickedAt.plus(EXPIRY_DELAY));
    }

    public boolean isConfirmationDue(Window window, Instant now) {
        return !now.isBefore(window.confirmationDueAt()) && now.isBefore(window.expiresAt());
    }

    public record Window(Instant clickedAt, Instant confirmationDueAt, Instant expiresAt) {
    }
}
