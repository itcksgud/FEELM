package com.feelm.catalog.c2.recommendation;

import com.fasterxml.jackson.databind.JsonNode;
import com.feelm.catalog.c2.input.ActiveRatingInputPort;

import java.util.List;
import java.util.UUID;

public interface RecommenderPort {
    Result rank(Command command);

    record Command(
            UUID requestId,
            CandidateSetPort.Snapshot candidateSet,
            List<UUID> eligibleMovieIds,
            ActiveRatingInputPort.Snapshot preferenceInput
    ) {
        public Command {
            eligibleMovieIds = List.copyOf(eligibleMovieIds);
        }
    }

    record Item(UUID movieId, int rank, JsonNode value) {
    }

    record Result(UUID requestId, String outcome, JsonNode snapshot, List<Item> items, List<JsonNode> issues) {
        public Result {
            items = List.copyOf(items);
            issues = List.copyOf(issues);
        }
    }
}
