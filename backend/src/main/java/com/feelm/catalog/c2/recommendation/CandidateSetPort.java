package com.feelm.catalog.c2.recommendation;

import java.util.List;
import java.util.UUID;

public interface CandidateSetPort {
    Snapshot loadActive();

    record Snapshot(
            String candidateSetVersion,
            String catalogVersion,
            String mappingPayloadSha256,
            String compatibilityId,
            List<UUID> movieIds
    ) {
        public Snapshot {
            movieIds = List.copyOf(movieIds);
        }
    }
}
