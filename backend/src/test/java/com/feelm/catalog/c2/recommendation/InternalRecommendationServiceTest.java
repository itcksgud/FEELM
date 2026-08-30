package com.feelm.catalog.c2.recommendation;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.feelm.catalog.c2.input.ActiveRatingInputPort;
import com.feelm.catalog.c2.input.ActiveRatingInputVersioner;
import com.feelm.catalog.domain.CatalogModels;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class InternalRecommendationServiceTest {
    private static final UUID ACTOR = UUID.fromString("018f6826-4da1-7c38-a846-8f794cd8b0cf");
    private static final UUID REQUEST = UUID.fromString("a892ba87-b17c-48f3-996f-f999e5d03872");
    private static final UUID READY = UUID.fromString("6b226903-0ca4-4f5a-9bf0-50d6cedd224c");
    private static final UUID NOT_READY = UUID.fromString("97204ea5-e6e5-4417-a13f-bc8197660705");

    @Test
    void revalidatesCatalogAndPreservesCandidateAndRatingVersions() {
        CandidateSetPort.Snapshot candidates = candidates();
        ActiveRatingInputPort.Snapshot ratings = new ActiveRatingInputVersioner().canonicalSnapshot(
                List.of(new ActiveRatingInputPort.RatingInput(READY, 4, 2))
        );
        AtomicReference<RecommenderPort.Command> captured = new AtomicReference<>();
        RecommenderPort recommender = command -> {
            captured.set(command);
            return new RecommenderPort.Result(
                    REQUEST, "COMPLETE", new ObjectMapper().createObjectNode(),
                    List.of(new RecommenderPort.Item(READY, 1, new ObjectMapper().createObjectNode())), List.of()
            );
        };
        InternalRecommendationService service = new InternalRecommendationService(
                () -> candidates,
                actor -> Optional.of(ratings),
                new ActiveRatingInputVersioner(),
                () -> catalog("catalog-v1", true),
                recommender
        );

        service.rank(ACTOR, REQUEST);
        assertThat(captured.get().candidateSet().candidateSetVersion()).isEqualTo("candidate-v1");
        assertThat(captured.get().preferenceInput().inputVersion()).isEqualTo(ratings.inputVersion());
        assertThat(captured.get().eligibleMovieIds()).containsExactly(READY);
    }

    @Test
    void rejectsCatalogChangeAfterUpstreamSuccessInsteadOfServingStaleSuccess() {
        java.util.concurrent.atomic.AtomicInteger reads = new java.util.concurrent.atomic.AtomicInteger();
        InternalRecommendationService service = new InternalRecommendationService(
                this::candidates,
                actor -> Optional.empty(),
                new ActiveRatingInputVersioner(),
                () -> reads.getAndIncrement() == 0 ? catalog("catalog-v1", true) : catalog("catalog-v2", true),
                command -> new RecommenderPort.Result(
                        REQUEST, "COMPLETE", new ObjectMapper().createObjectNode(),
                        List.of(new RecommenderPort.Item(READY, 1, new ObjectMapper().createObjectNode())), List.of()
                )
        );
        assertThatThrownBy(() -> service.rank(ACTOR, REQUEST))
                .isInstanceOfSatisfying(C2RecommendationFailure.class,
                        failure -> assertThat(failure.code())
                                .isEqualTo(C2RecommendationFailure.Code.CATALOG_VERSION_MISMATCH));
    }

    private CandidateSetPort.Snapshot candidates() {
        return new CandidateSetPort.Snapshot(
                "candidate-v1", "catalog-v1", "b".repeat(64), "family-v1", List.of(READY, NOT_READY)
        );
    }

    private CatalogModels.CatalogSnapshot catalog(String version, boolean readyVisible) {
        CatalogModels.Movie ready = mock(CatalogModels.Movie.class);
        when(ready.movieId()).thenReturn(READY);
        when(ready.catalogVisible()).thenReturn(readyVisible);
        when(ready.uiReady()).thenReturn(readyVisible);
        CatalogModels.Movie notReady = mock(CatalogModels.Movie.class);
        when(notReady.movieId()).thenReturn(NOT_READY);
        when(notReady.catalogVisible()).thenReturn(true);
        when(notReady.uiReady()).thenReturn(false);
        return new CatalogModels.CatalogSnapshot(
                version, "similarity-v1", List.of(ready, notReady), List.of(), List.of(), List.of(), java.util.Map.of()
        );
    }
}
