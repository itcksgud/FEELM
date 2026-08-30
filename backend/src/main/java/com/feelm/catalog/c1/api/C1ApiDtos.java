package com.feelm.catalog.c1.api;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotNull;

import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;

public final class C1ApiDtos {
    private C1ApiDtos() {
    }

    public record CreateWatchIntentRequest(@NotNull UUID movieId, @NotNull UUID offerId) {
    }

    public record ConfirmWatchIntentRequest(
            @NotNull Boolean watched,
            @NotNull @Min(1) Integer expectedRevision
    ) {
    }

    public record PutRatingRequest(
            @NotNull @Min(1) @Max(5) Integer value,
            @Min(1) Integer expectedRevision
    ) {
    }

    public record ExternalDestination(String linkType, String url, boolean externalNavigation) {
    }

    public record WatchIntentSnapshot(
            UUID watchIntentId,
            String status,
            OffsetDateTime clickedAt,
            OffsetDateTime confirmationDueAt,
            OffsetDateTime expiresAt,
            int revision
    ) {
    }

    public record WatchIntentClickResult(
            String outcome,
            UUID movieId,
            UUID providerId,
            WatchIntentSnapshot watchIntent,
            ExternalDestination destination
    ) {
    }

    public record MovieSummary(UUID movieId, String displayTitle, String posterUrl, Integer releaseYear) {
    }

    public record ProviderSummary(UUID providerId, String name) {
    }

    public record PendingWatchConfirmation(
            UUID watchIntentId,
            MovieSummary movie,
            ProviderSummary provider,
            OffsetDateTime clickedAt,
            OffsetDateTime confirmationDueAt,
            OffsetDateTime expiresAt,
            int revision
    ) {
    }

    public record CursorPage<T>(int totalCount, boolean hasNext, String nextCursor, List<T> items) {
    }

    public record ViewingRecordSummary(
            UUID viewingRecordId,
            UUID movieId,
            String status,
            OffsetDateTime watchedConfirmedAt,
            ProviderSummary provider,
            int revision
    ) {
    }

    public record WatchConfirmationResult(
            UUID watchIntentId,
            String status,
            OffsetDateTime respondedAt,
            int revision,
            ViewingRecordSummary viewingRecord
    ) {
    }

    public record UnratedViewingRecord(
            UUID viewingRecordId,
            MovieSummary movie,
            OffsetDateTime watchedConfirmedAt,
            ProviderSummary provider,
            int revision
    ) {
    }

    public record Rating(
            UUID ratingId,
            UUID movieId,
            int value,
            int revision,
            OffsetDateTime createdAt,
            OffsetDateTime updatedAt
    ) {
    }

    public record RatingItem(
            Rating rating,
            MovieSummary movie,
            OffsetDateTime watchedConfirmedAt,
            UUID frameId
    ) {
    }

    public record DerivedState(
            String viewingStatus,
            UUID frameId,
            UUID popcornId,
            int filmTotalCount,
            long aggregateRevision,
            String recommendationRefresh
    ) {
    }

    public record RatingMutationResult(String mutation, Rating rating, DerivedState derivedState) {
    }

    public record RatingDeletionResult(
            UUID movieId,
            boolean ratingRemoved,
            String viewingStatus,
            boolean frameActive,
            boolean popcornActive,
            int filmTotalCount,
            long aggregateRevision,
            String recommendationRefresh
    ) {
    }

    public record FrameSummary(
            UUID frameId,
            MovieSummary movie,
            int myRating,
            OffsetDateTime watchedConfirmedAt,
            OffsetDateTime createdAt
    ) {
    }

    public record FilmPage(
            int totalCount,
            boolean hasNext,
            String nextCursor,
            int filmRevision,
            List<FrameSummary> items
    ) {
    }

    public record FrameDetail(
            UUID frameId,
            MovieSummary movie,
            Rating rating,
            OffsetDateTime watchedConfirmedAt,
            ProviderSummary provider,
            OffsetDateTime createdAt,
            String derivationVersion
    ) {
    }

    public record FlavorAggregate(
            UUID flavorId,
            String code,
            String displayName,
            String colorToken,
            int count,
            int ratingCount,
            BigDecimal averageRating
    ) {
    }

    public record PopcornBucket(
            int totalCount,
            String mappingVersion,
            long aggregateRevision,
            List<FlavorAggregate> flavors
    ) {
    }

    public record TasteAggregate(
            String dimensionType,
            String dimensionKey,
            String displayName,
            int ratingCount,
            BigDecimal averageRating
    ) {
    }

    public record TasteProfile(String derivationVersion, long aggregateRevision, List<TasteAggregate> items) {
    }
}
