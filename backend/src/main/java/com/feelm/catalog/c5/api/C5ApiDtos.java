package com.feelm.catalog.c5.api;

import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

import java.math.BigDecimal;
import java.net.URI;
import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.UUID;

public final class C5ApiDtos {
    private C5ApiDtos() {
    }

    public record CreateTasteReportRequest(@NotNull LocalDate periodStart) {
    }

    public record TasteReportSummary(
            UUID reportId,
            LocalDate periodStart,
            LocalDate periodEnd,
            int revision,
            String status,
            Instant createdAt
    ) {
    }

    public record TasteReportSummaryPage(
            int totalCount,
            boolean hasNext,
            String nextCursor,
            List<TasteReportSummary> items
    ) {
        public TasteReportSummaryPage {
            items = List.copyOf(items);
        }
    }

    public record FactualReportMetrics(int viewingCount, int ratedCount, BigDecimal averageRating) {
    }

    public record ReportMovieItem(
            UUID movieId,
            String displayTitle,
            URI posterUrl,
            Instant watchedAt,
            Integer rating
    ) {
    }

    public record ReportMoviePage(
            int totalCount,
            boolean hasNext,
            String nextCursor,
            List<ReportMovieItem> items
    ) {
        public ReportMoviePage {
            items = List.copyOf(items);
        }
    }

    public record TasteReport(
            UUID reportId,
            LocalDate periodStart,
            LocalDate periodEnd,
            int revision,
            String status,
            Instant createdAt,
            FactualReportMetrics metrics,
            ReportMoviePage periodItems
    ) {
    }

    public record ReportExport(
            UUID exportId,
            UUID reportId,
            String status,
            Instant createdAt,
            Instant expiresAt,
            String downloadHref
    ) {
    }

    public record ResourcePrivacy(@NotBlank String resource, @NotBlank String visibility) {
    }

    public record PrivacySettings(UUID publicProfileId, long revision, List<ResourcePrivacy> resources) {
        public PrivacySettings {
            resources = List.copyOf(resources);
        }
    }

    public record ReplacePrivacySettingsRequest(
            @Min(1) long expectedRevision,
            @NotNull @Size(min = 3, max = 3) List<@Valid ResourcePrivacy> resources
    ) {
        public ReplacePrivacySettingsRequest {
            resources = resources == null ? null : List.copyOf(resources);
        }
    }

    public record PublicProfile(UUID publicProfileId, String nickname) {
    }

    public record PublicFilmItem(UUID frameId, UUID movieId, String displayTitle, Instant watchedAt) {
    }

    public record PublicFilmPage(
            int totalCount,
            boolean hasNext,
            String nextCursor,
            List<PublicFilmItem> items
    ) {
        public PublicFilmPage {
            items = List.copyOf(items);
        }
    }

    public record PublicPopcornItem(UUID popcornId, UUID frameId, UUID movieId, String displayTitle) {
    }

    public record PublicPopcornPage(
            int totalCount,
            boolean hasNext,
            String nextCursor,
            List<PublicPopcornItem> items
    ) {
        public PublicPopcornPage {
            items = List.copyOf(items);
        }
    }

    public record CreatedReportShare(
            UUID shareId,
            UUID reportId,
            String rawToken,
            String shareHref,
            Instant expiresAt
    ) {
    }

    public record ExchangeReportShareRequest(@NotBlank @Size(min = 43, max = 256) String rawToken) {
    }

    public record ReportViewerSession(String viewerSessionToken, Instant expiresAt) {
    }

    public record SharedTasteReport(TasteReport report, String ownerNickname) {
    }

    public record NotificationSettings(boolean watchConfirmationDueEnabled, long revision) {
    }

    public record ReplaceNotificationSettingsRequest(
            boolean watchConfirmationDueEnabled,
            @Min(1) long expectedRevision
    ) {
    }

    public record InAppNotification(
            UUID notificationId,
            String category,
            String state,
            String message,
            Instant createdAt
    ) {
    }

    public record NotificationPage(
            int totalCount,
            boolean hasNext,
            String nextCursor,
            List<InAppNotification> items
    ) {
        public NotificationPage {
            items = List.copyOf(items);
        }
    }

    public record UpdateNotificationStateRequest(@NotBlank String state) {
    }

    public record PdfContent(byte[] bytes, String sha256) {
        public PdfContent {
            bytes = bytes.clone();
        }

        @Override
        public byte[] bytes() {
            return bytes.clone();
        }
    }
}
