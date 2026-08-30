package com.feelm.catalog.c2.recommendation;

import com.feelm.catalog.c2.input.ActiveRatingInputPort;
import com.feelm.catalog.c2.input.ActiveRatingInputVersioner;
import com.feelm.catalog.domain.CatalogModels;
import com.feelm.catalog.domain.CatalogReadPort;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Service;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

@Service
@Profile({"postgres", "local"})
public final class InternalRecommendationService {
    private final CandidateSetPort candidateSets;
    private final ActiveRatingInputPort ratingInputs;
    private final ActiveRatingInputVersioner ratingVersioner;
    private final CatalogReadPort catalog;
    private final RecommenderPort recommender;

    public InternalRecommendationService(
            CandidateSetPort candidateSets,
            ActiveRatingInputPort ratingInputs,
            ActiveRatingInputVersioner ratingVersioner,
            CatalogReadPort catalog,
            RecommenderPort recommender
    ) {
        this.candidateSets = candidateSets;
        this.ratingInputs = ratingInputs;
        this.ratingVersioner = ratingVersioner;
        this.catalog = catalog;
        this.recommender = recommender;
    }

    public RecommenderPort.Result rank(UUID actorUserId, UUID requestId) {
        if (actorUserId == null || requestId == null) {
            throw new IllegalArgumentException("actor and request correlation are required");
        }
        CandidateSetPort.Snapshot candidates = candidateSets.loadActive();
        ActiveRatingInputPort.Snapshot ratings = ratingInputs.findProjected(actorUserId)
                .orElseGet(() -> ratingVersioner.canonicalSnapshot(List.of()));
        CatalogModels.CatalogSnapshot before = catalog.loadActiveSnapshot();
        requireCatalogVersion(candidates, before);
        Map<UUID, CatalogModels.Movie> activeBefore = activeMovies(before);
        List<UUID> eligible = candidates.movieIds().stream()
                .filter(activeBefore::containsKey)
                .toList();
        if (eligible.isEmpty()) {
            throw new C2RecommendationFailure(
                    C2RecommendationFailure.Code.NO_ELIGIBLE_CANDIDATES, false
            );
        }

        RecommenderPort.Result result = recommender.rank(
                new RecommenderPort.Command(requestId, candidates, eligible, ratings)
        );

        CatalogModels.CatalogSnapshot after = catalog.loadActiveSnapshot();
        requireCatalogVersion(candidates, after);
        Map<UUID, CatalogModels.Movie> activeAfter = activeMovies(after);
        if (result.items().stream().anyMatch(item -> !activeAfter.containsKey(item.movieId()))) {
            throw new C2RecommendationFailure(C2RecommendationFailure.Code.INVALID_RESPONSE, false);
        }
        return result;
    }

    private void requireCatalogVersion(
            CandidateSetPort.Snapshot candidates, CatalogModels.CatalogSnapshot snapshot
    ) {
        if (snapshot == null || !candidates.catalogVersion().equals(snapshot.catalogVersion())) {
            throw new C2RecommendationFailure(
                    C2RecommendationFailure.Code.CATALOG_VERSION_MISMATCH, true
            );
        }
    }

    private Map<UUID, CatalogModels.Movie> activeMovies(CatalogModels.CatalogSnapshot snapshot) {
        Map<UUID, CatalogModels.Movie> result = new HashMap<>();
        for (CatalogModels.Movie movie : snapshot.movies()) {
            if (movie.catalogVisible() && movie.uiReady()) {
                result.put(movie.movieId(), movie);
            }
        }
        return result;
    }
}
