package com.feelm.catalog.c2.input;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

public interface ActiveRatingInputPort {
    Optional<Snapshot> findProjected(UUID actorUserId);

    record Snapshot(String inputVersion, List<RatingInput> ratings) {
        public Snapshot {
            ratings = List.copyOf(ratings);
        }
    }

    record RatingInput(UUID movieId, int value, int revision) {
    }
}
