package com.feelm.catalog.c6.service;

import com.feelm.catalog.api.ApiException;
import com.feelm.catalog.c2.input.ActiveRatingInputPort;
import com.feelm.catalog.c2.input.ActiveRatingInputVersioner;
import com.feelm.catalog.c2.recommendation.C2RecommendationFailure;
import com.feelm.catalog.c2.recommendation.CandidateSetPort;
import com.feelm.catalog.c6.api.C6ApiDtos.*;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Profile;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

@Service
@Profile("local")
@ConditionalOnProperty(name = "catalog.c6.local.enabled", havingValue = "true")
public final class C6InterpretationService {
    private final CandidateSetPort candidates;
    private final C6ExperimentDataPort data;
    private final ActiveRatingInputVersioner versioner;
    private final C6RecommenderPort recommender;

    public C6InterpretationService(
            CandidateSetPort candidates,
            C6ExperimentDataPort data,
            ActiveRatingInputVersioner versioner,
            C6RecommenderPort recommender
    ) {
        this.candidates = candidates;
        this.data = data;
        this.versioner = versioner;
        this.recommender = recommender;
    }

    public RecommendationInterpretationExperiment run(UUID actorUserId) {
        if (actorUserId == null) throw ApiException.unauthorized();
        try {
            CandidateSetPort.Snapshot candidateSet = candidates.loadActive();
            C6ExperimentDataPort.DataSnapshot source = data.load(
                    actorUserId, candidateSet.catalogVersion(), candidateSet.movieIds()
            );
            validateSource(candidateSet, source);
            ActiveRatingInputPort.Snapshot canonical = versioner.canonicalSnapshot(
                    source.ratingsMostRecentFirst()
            );
            C6RecommenderPort.Result result = recommender.interpret(new C6RecommenderPort.Command(
                    UUID.randomUUID(), candidateSet, source.eligibleMovieIds(),
                    canonical.inputVersion(), source.ratingsMostRecentFirst()
            ));
            return response(result, source);
        } catch (ApiException exception) {
            throw exception;
        } catch (C2RecommendationFailure | C6RecommenderFailure | IllegalArgumentException | IllegalStateException failure) {
            throw unavailable();
        }
    }

    private RecommendationInterpretationExperiment response(
            C6RecommenderPort.Result result,
            C6ExperimentDataPort.DataSnapshot source
    ) {
        Map<UUID, MovieSummary> movies = source.movies();
        List<Prediction> predictions = result.items().stream().map(item -> {
            MovieSummary movie = movies.get(item.movieId());
            if (movie == null || item.displayEligible()) throw new IllegalStateException("C6 response is not enrichable");
            return new Prediction(
                    movie, item.predictedRating(), item.expectedRelativeUtility(),
                    item.directFoldIn(), item.confidence(), false
            );
        }).toList();
        List<TasteEvidence> evidence = tasteEvidence(source.tasteAggregates(), result.ratingProfile().mean());
        C6RecommenderPort.Snapshot snapshot = result.snapshot();
        return new RecommendationInterpretationExperiment(
                result.experimentVersion(), snapshot.inputVersion(),
                new ModelContext(
                        snapshot.artifactSetVersion(), snapshot.policyVersion(), snapshot.kSelectionPolicyVersion(),
                        snapshot.utilityPolicyVersion(),
                        snapshot.availableRatingCount(), snapshot.usedRatingCount()
                ),
                new RatingProfile(
                        result.ratingProfile().activeRatingCount(), result.ratingProfile().mean(),
                        result.ratingProfile().median(), result.ratingProfile().confidence()
                ),
                predictions, evidence, result.limitations()
        );
    }

    private List<TasteEvidence> tasteEvidence(
            List<C6ExperimentDataPort.TasteAggregate> aggregates,
            BigDecimal userMean
    ) {
        List<TasteEvidence> result = new ArrayList<>();
        for (C6ExperimentDataPort.TasteAggregate aggregate : aggregates) {
            if (aggregate.ratingCount() < 1 || aggregate.ratingSum() < aggregate.ratingCount()
                    || aggregate.ratingSum() > aggregate.ratingCount() * 5
                    || aggregate.displayName() == null || aggregate.displayName().isBlank()
                    || aggregate.dimensionKey() == null || aggregate.dimensionKey().isBlank()
                    || !Set.of("GENRE", "COUNTRY", "DIRECTOR").contains(aggregate.dimensionType())) {
                throw new IllegalStateException("C6 taste aggregate is invalid");
            }
            BigDecimal average = BigDecimal.valueOf(aggregate.ratingSum())
                    .divide(BigDecimal.valueOf(aggregate.ratingCount()), 2, RoundingMode.HALF_UP)
                    .stripTrailingZeros();
            BigDecimal lift = userMean == null ? null : average.subtract(userMean).stripTrailingZeros();
            result.add(new TasteEvidence(
                    aggregate.dimensionType(), aggregate.dimensionKey(), aggregate.displayName(),
                    aggregate.ratingCount(), average, lift, confidenceFor(aggregate.ratingCount())
            ));
        }
        result.sort(Comparator
                .comparingInt((TasteEvidence item) -> confidenceRank(item.confidence())).reversed()
                .thenComparing(Comparator.comparingInt(TasteEvidence::ratingCount).reversed())
                .thenComparing(TasteEvidence::dimensionType)
                .thenComparing(TasteEvidence::dimensionKey));
        return List.copyOf(result);
    }

    private void validateSource(CandidateSetPort.Snapshot candidateSet, C6ExperimentDataPort.DataSnapshot source) {
        if (source == null || source.eligibleMovieIds().stream().distinct().count() != source.eligibleMovieIds().size()
                || source.ratingsMostRecentFirst().stream().map(ActiveRatingInputPort.RatingInput::movieId)
                .distinct().count() != source.ratingsMostRecentFirst().size()
                || !candidateSet.movieIds().containsAll(source.eligibleMovieIds())
                || !source.movies().keySet().equals(Set.copyOf(source.eligibleMovieIds()))) {
            throw new IllegalStateException("C6 experiment input is inconsistent");
        }
        Set<UUID> rated = source.ratingsMostRecentFirst().stream()
                .map(ActiveRatingInputPort.RatingInput::movieId).collect(java.util.stream.Collectors.toSet());
        if (source.eligibleMovieIds().stream().anyMatch(rated::contains)) {
            throw new IllegalStateException("C6 rated candidate was not excluded");
        }
    }

    static String confidenceFor(int count) {
        if (count < 3) return "INSUFFICIENT_DATA";
        if (count < 5) return "LOW";
        if (count < 10) return "MEDIUM";
        return "HIGH";
    }

    private static int confidenceRank(String confidence) {
        return switch (confidence) {
            case "HIGH" -> 4;
            case "MEDIUM" -> 3;
            case "LOW" -> 2;
            case "INSUFFICIENT_DATA" -> 1;
            default -> 0;
        };
    }

    private static ApiException unavailable() {
        return new ApiException(
                HttpStatus.SERVICE_UNAVAILABLE,
                "RECOMMENDATION_INTERPRETATION_EXPERIMENT_UNAVAILABLE",
                "추천 해석 실험을 불러올 수 없어요. 잠시 후 다시 시도해 주세요."
        );
    }
}
