package com.feelm.catalog.c6.service;

import com.feelm.catalog.c2.input.ActiveRatingInputPort;
import com.feelm.catalog.c6.api.C6ApiDtos.MovieSummary;

import java.util.List;
import java.util.Map;
import java.util.UUID;

public interface C6ExperimentDataPort {
    DataSnapshot load(UUID actorUserId, String catalogVersion, List<UUID> candidateMovieIds);

    record DataSnapshot(
            List<ActiveRatingInputPort.RatingInput> ratingsMostRecentFirst,
            List<UUID> eligibleMovieIds,
            Map<UUID, MovieSummary> movies,
            List<TasteAggregate> tasteAggregates
    ) {
        public DataSnapshot {
            ratingsMostRecentFirst = List.copyOf(ratingsMostRecentFirst);
            eligibleMovieIds = List.copyOf(eligibleMovieIds);
            movies = Map.copyOf(movies);
            tasteAggregates = List.copyOf(tasteAggregates);
        }
    }

    record TasteAggregate(
            String dimensionType,
            String dimensionKey,
            String displayName,
            int ratingCount,
            int ratingSum
    ) {
    }
}
