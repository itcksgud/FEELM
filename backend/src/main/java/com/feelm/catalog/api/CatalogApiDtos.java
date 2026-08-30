package com.feelm.catalog.api;

import com.feelm.catalog.domain.CatalogModels.AvailabilityStatus;
import com.feelm.catalog.domain.CatalogModels.Freshness;
import com.feelm.catalog.domain.CatalogModels.LinkType;
import com.feelm.catalog.domain.CatalogModels.MonetizationType;
import com.feelm.catalog.domain.CatalogModels.MovieSort;

import java.math.BigDecimal;
import java.net.URI;
import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.UUID;

public final class CatalogApiDtos {
    private CatalogApiDtos() {
    }

    public record Genre(UUID genreId, String name) {
    }

    public record Country(String code, String name) {
    }

    public record ExternalRating(String source, BigDecimal value, int scale, long ratingCount) {
    }

    public record ProviderBadge(UUID providerId, String name, URI logoUrl, Boolean isSubscribed) {
    }

    public record AvailabilityPreview(
            String region,
            AvailabilityStatus availabilityStatus,
            Freshness freshness,
            Instant snapshotAt,
            List<ProviderBadge> flatrateProviders
    ) {
    }

    public record MovieCard(
            UUID movieId,
            String displayTitle,
            String displayTitleLocale,
            Integer releaseYear,
            URI posterUrl,
            List<Genre> genres,
            ExternalRating externalRating,
            AvailabilityPreview availability
    ) {
    }

    public record AppliedFilters(
            String query,
            List<UUID> genreIds,
            List<String> countryCodes,
            Integer releaseYearFrom,
            Integer releaseYearTo,
            List<UUID> ottProviderIds,
            List<MonetizationType> ottMonetizationTypes,
            MovieSort sort
    ) {
    }

    public record MovieSearchPage(
            String catalogVersion,
            int totalCount,
            boolean hasNext,
            String nextCursor,
            List<MovieCard> items,
            AppliedFilters appliedFilters
    ) {
    }

    public record PersonCredit(UUID personId, String name, String role, String character, int order) {
    }

    public record MovieDetail(
            String catalogVersion,
            UUID movieId,
            String displayTitle,
            String displayTitleLocale,
            String originalTitle,
            String overview,
            String overviewLocale,
            LocalDate releaseDate,
            Integer runtimeMinutes,
            URI posterUrl,
            URI backdropUrl,
            List<Genre> genres,
            List<Country> productionCountries,
            List<PersonCredit> directors,
            List<PersonCredit> cast,
            ExternalRating externalRating,
            AvailabilityPreview availability,
            Instant metadataAsOf
    ) {
    }

    public record SimilarityReason(String code, String label) {
    }

    public record SimilarMovieItem(MovieCard movie, List<SimilarityReason> reasons) {
    }

    public record SimilarMovieResponse(String catalogVersion, String similarityVersion, List<SimilarMovieItem> items) {
    }

    public record OfferLink(LinkType type, URI url) {
    }

    public record OttOffer(
            UUID offerId,
            UUID providerId,
            String providerName,
            URI logoUrl,
            MonetizationType monetizationType,
            Boolean isSubscribed,
            OfferLink link
    ) {
    }

    public record OttOfferGroup(MonetizationType monetizationType, List<OttOffer> offers) {
    }

    public record OttAvailability(
            String catalogVersion,
            UUID movieId,
            String region,
            AvailabilityStatus availabilityStatus,
            Freshness freshness,
            Instant snapshotAt,
            String source,
            List<OttOfferGroup> groups
    ) {
    }

    public record GenreListResponse(String catalogVersion, List<Genre> items) {
    }

    public record CountryListResponse(String catalogVersion, List<Country> items) {
    }

    public record OttProvider(UUID providerId, String name, URI logoUrl, int displayPriority, Boolean isSubscribed) {
    }

    public record OttProviderListResponse(String catalogVersion, String region, List<OttProvider> items) {
    }

    public record FieldError(String field, String reason) {
    }

    public record ErrorResponse(String code, String message, String traceId, List<FieldError> fieldErrors) {
    }
}
