package com.feelm.catalog.c6;

import com.feelm.catalog.c2.input.ActiveRatingInputPort;
import com.feelm.catalog.c2.input.ActiveRatingInputVersioner;
import com.feelm.catalog.c2.recommendation.CandidateSetPort;
import com.feelm.catalog.c6.api.C6ApiDtos.MovieSummary;
import com.feelm.catalog.c6.service.C6ExperimentDataPort;
import com.feelm.catalog.c6.service.C6InterpretationService;
import com.feelm.catalog.c6.service.C6RecommenderPort;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;

class C6InterpretationServiceTest {
    private static final UUID ACTOR = UUID.fromString("018f6826-4da1-7c38-a846-8f794cd8b0cf");
    private static final UUID RATED = UUID.fromString("6b226903-0ca4-4f5a-9bf0-50d6cedd224c");
    private static final UUID ELIGIBLE = UUID.fromString("19406c31-213f-4fe1-93f6-109f8570ec20");

    @Test
    void excludesRatedCandidateEnrichesMovieAndKeepsOneRatingEvidenceInsufficient() {
        CandidateSetPort.Snapshot candidates = candidates();
        var rating = new ActiveRatingInputPort.RatingInput(RATED, 4, 2);
        var movie = new MovieSummary(ELIGIBLE, "영어 대체 영화", null, 2018, List.of("드라마"));
        var source = new C6ExperimentDataPort.DataSnapshot(
                List.of(rating), List.of(ELIGIBLE), Map.of(ELIGIBLE, movie),
                List.of(new C6ExperimentDataPort.TasteAggregate(
                        "GENRE", "165a3c6f-9b81-4420-9713-c59303d5bb92", "드라마", 1, 4
                ))
        );
        AtomicReference<C6RecommenderPort.Command> captured = new AtomicReference<>();
        C6InterpretationService service = service(candidates, source, command -> {
            captured.set(command);
            return result(command.inputVersion(), 1, 1, BigDecimal.valueOf(4), BigDecimal.valueOf(4),
                    new C6RecommenderPort.Item(
                            ELIGIBLE, BigDecimal.valueOf(4.2), BigDecimal.valueOf(0.8),
                            true, "LOW", false
                    ));
        });

        var response = service.run(ACTOR);
        assertThat(captured.get().eligibleMovieIds()).containsExactly(ELIGIBLE).doesNotContain(RATED);
        assertThat(captured.get().ratings()).containsExactly(rating);
        assertThat(captured.get().inputVersion()).startsWith("c2-active-rating-input-v1:sha256:");
        assertThat(response.predictions()).singleElement().satisfies(prediction -> {
            assertThat(prediction.movie()).isEqualTo(movie);
            assertThat(prediction.displayEligible()).isFalse();
        });
        assertThat(response.tasteEvidence()).singleElement().satisfies(evidence -> {
            assertThat(evidence.displayName()).isEqualTo("드라마");
            assertThat(evidence.confidence()).isEqualTo("INSUFFICIENT_DATA");
            assertThat(evidence.liftFromUserMean()).isEqualByComparingTo(BigDecimal.ZERO);
        });
    }

    @Test
    void noRatingUsesKZeroAndReturnsNoDiagnosticEvidence() {
        CandidateSetPort.Snapshot candidates = candidates();
        var movie = new MovieSummary(RATED, "나우 유 씨 미", "https://image.tmdb.org/t/p/w500/poster.jpg",
                2013, List.of("범죄"));
        var source = new C6ExperimentDataPort.DataSnapshot(
                List.of(), List.of(RATED, ELIGIBLE),
                Map.of(RATED, movie, ELIGIBLE, new MovieSummary(ELIGIBLE, "영화", null, 2018, List.of())),
                List.of()
        );
        AtomicReference<C6RecommenderPort.Command> captured = new AtomicReference<>();
        C6InterpretationService service = service(candidates, source, command -> {
            captured.set(command);
            return result(command.inputVersion(), 0, 0, null, null,
                    new C6RecommenderPort.Item(
                            RATED, BigDecimal.valueOf(3.4), null, false,
                            "INSUFFICIENT_DATA", false
                    ),
                    new C6RecommenderPort.Item(
                            ELIGIBLE, BigDecimal.valueOf(3.2), null, false,
                            "INSUFFICIENT_DATA", false
                    ));
        });

        var response = service.run(ACTOR);
        assertThat(captured.get().ratings()).isEmpty();
        assertThat(response.modelContext().availableRatingCount()).isZero();
        assertThat(response.modelContext().usedRatingCount()).isZero();
        assertThat(response.ratingProfile().mean()).isNull();
        assertThat(response.tasteEvidence()).isEmpty();
        assertThat(response.predictions()).allSatisfy(prediction -> {
            assertThat(prediction.directFoldIn()).isFalse();
            assertThat(prediction.expectedRelativeUtility()).isNull();
            assertThat(prediction.displayEligible()).isFalse();
        });
    }

    private C6InterpretationService service(
            CandidateSetPort.Snapshot candidateSet,
            C6ExperimentDataPort.DataSnapshot data,
            C6RecommenderPort recommender
    ) {
        return new C6InterpretationService(
                () -> candidateSet,
                (actor, catalog, ids) -> data,
                new ActiveRatingInputVersioner(),
                recommender
        );
    }

    private CandidateSetPort.Snapshot candidates() {
        return new CandidateSetPort.Snapshot(
                "candidate-v1", "catalog-fixture-20260829-01", "b".repeat(64),
                "fixture-family-v1", List.of(RATED, ELIGIBLE)
        );
    }

    private C6RecommenderPort.Result result(
            String inputVersion,
            int available,
            int used,
            BigDecimal mean,
            BigDecimal median,
            C6RecommenderPort.Item... items
    ) {
        return new C6RecommenderPort.Result(
                "c6-recommendation-interpretation-v2",
                new C6RecommenderPort.Snapshot(
                        "artifact-set-v1", "policy-v1", inputVersion,
                        "C6_MOST_RECENT_VALIDATED_K_FLOOR_V1",
                        "C6_DISCRETE_QUANTIZED_MIDRANK_ECDF_V2", available, used
                ),
                new C6RecommenderPort.RatingProfile(
                        available, mean, median, available == 0 ? "INSUFFICIENT_DATA" : "LOW"
                ),
                List.of(items),
                List.of(
                        "LOCAL_EXPERIMENT_ONLY", "NOT_SELF_REPORTED_SATISFACTION",
                        "NOT_PRODUCT_DISPLAY_APPROVED", "K_BUCKETED_MOST_RECENT"
                )
        );
    }
}
