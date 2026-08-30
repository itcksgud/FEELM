package com.feelm.catalog.c2.input;

import org.springframework.stereotype.Component;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.List;
import java.util.Set;

@Component
public final class ActiveRatingInputVersioner {
    public static final String POLICY_VERSION = "c2-active-rating-input-v1";

    public ActiveRatingInputPort.Snapshot canonicalSnapshot(List<ActiveRatingInputPort.RatingInput> inputs) {
        if (inputs == null) {
            throw new IllegalArgumentException("rating inputs are required");
        }
        List<ActiveRatingInputPort.RatingInput> canonical = new ArrayList<>(inputs);
        canonical.sort(Comparator.comparing(input -> input.movieId().toString()));
        Set<java.util.UUID> movieIds = new HashSet<>();
        StringBuilder material = new StringBuilder(POLICY_VERSION).append('\n');
        for (ActiveRatingInputPort.RatingInput input : canonical) {
            if (input == null || input.movieId() == null || input.value() < 1 || input.value() > 5
                    || input.revision() < 1 || !movieIds.add(input.movieId())) {
                throw new IllegalArgumentException("active Rating input is invalid");
            }
            material.append(input.movieId())
                    .append('|').append(input.value())
                    .append('|').append(input.revision())
                    .append('\n');
        }
        return new ActiveRatingInputPort.Snapshot(
                POLICY_VERSION + ":sha256:" + sha256(material.toString()),
                canonical
        );
    }

    private String sha256(String material) {
        try {
            return HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256").digest(material.getBytes(StandardCharsets.UTF_8))
            );
        } catch (Exception exception) {
            throw new IllegalStateException("active Rating input version failed", exception);
        }
    }
}
