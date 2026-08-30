package com.feelm.catalog.c2b.api;

import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;

public final class C2BApiDtos {
    private C2BApiDtos() {
    }

    public record RecommendationPageInfo(
            int activeItemCount,
            boolean hasMore,
            String nextCursor,
            OffsetDateTime cursorExpiresAt
    ) {
    }

    public record MovieCard(
            UUID movieId,
            String title,
            String posterUrl,
            Integer releaseYear,
            List<String> genres
    ) {
        public MovieCard {
            genres = List.copyOf(genres);
        }
    }

    public record DeliveryItem(
            UUID deliveryItemId,
            int position,
            int sourceRank,
            String recommendationType,
            MovieCard movie
    ) {
    }

    public record RecommendationDelivery(
            UUID deliveryId,
            int deliveryRevision,
            String label,
            String composition,
            List<DeliveryItem> items,
            RecommendationPageInfo pageInfo
    ) {
        public RecommendationDelivery {
            items = List.copyOf(items);
        }
    }

    public record AppendRecommendationsRequest(
            @NotNull UUID appendEventId,
            @NotNull @Min(1) Integer expectedRevision,
            @NotBlank String cursor
    ) {
    }

    public record SelectionSummary(int scannedCount, int selectedCount, int excludedCount) {
    }

    public record SafeIssue(String code, int count, boolean retriable) {
    }

    public record RecommendationAppend(
            UUID appendEventId,
            UUID deliveryId,
            int deliveryRevision,
            String outcome,
            SelectionSummary selectionSummary,
            List<DeliveryItem> appendedItems,
            List<SafeIssue> issues,
            RecommendationPageInfo pageInfo,
            boolean replayed
    ) {
        public RecommendationAppend {
            appendedItems = List.copyOf(appendedItems);
            issues = List.copyOf(issues);
        }

        public RecommendationAppend withReplayed(boolean value) {
            return new RecommendationAppend(
                    appendEventId, deliveryId, deliveryRevision, outcome, selectionSummary,
                    appendedItems, issues, pageInfo, value
            );
        }
    }

    public record DismissRecommendationRequest(
            @NotNull UUID dismissalEventId,
            @NotNull @Min(1) Integer expectedRevision,
            @NotNull @Pattern(regexp = "NOT_INTERESTED") String reason
    ) {
    }

    public record RecommendationDismissal(
            UUID dismissalEventId,
            UUID deliveryItemId,
            int deliveryRevision,
            String status,
            OffsetDateTime occurredAt,
            boolean replayed
    ) {
        public RecommendationDismissal withReplayed(boolean value) {
            return new RecommendationDismissal(
                    dismissalEventId, deliveryItemId, deliveryRevision, status, occurredAt, value
            );
        }
    }
}
