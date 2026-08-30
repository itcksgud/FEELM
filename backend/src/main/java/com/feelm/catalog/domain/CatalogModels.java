package com.feelm.catalog.domain;

import java.math.BigDecimal;
import java.net.URI;
import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;
import java.util.UUID;

public final class CatalogModels {
    private CatalogModels() {
    }

    public enum MovieSort {
        RELEVANCE, POPULARITY, RELEASE_DATE_DESC, RATING_COUNT_DESC
    }

    public enum MonetizationType {
        FLATRATE, RENT, BUY, FREE, ADS
    }

    public enum AvailabilityStatus {
        LISTED, NONE_LISTED, UNKNOWN
    }

    public enum Freshness {
        FRESH, STALE, UNKNOWN
    }

    public enum LinkType {
        AGGREGATOR, DIRECT
    }

    public enum SnapshotFetchStatus {
        SUCCESS_LISTED, SUCCESS_EMPTY
    }

    public enum CreditRole {
        DIRECTOR, CAST
    }

    public record Genre(UUID genreId, String name, int displayOrder) {
    }

    public record Country(String code, String name) {
    }

    public record PersonCredit(UUID personId, String name, CreditRole role, String character, int order) {
    }

    public record ExternalRating(String source, BigDecimal value, int scale, long ratingCount) {
    }

    public record Provider(UUID providerId, String name, URI logoUrl, int displayPriority) {
    }

    public record Offer(
            UUID offerId,
            UUID providerId,
            MonetizationType monetizationType,
            LinkType linkType,
            URI landingUrl
    ) {
    }

    public record AvailabilitySnapshot(
            SnapshotFetchStatus fetchStatus,
            Instant fetchedAt,
            Instant freshUntil,
            Instant serveUntil,
            List<Offer> offers
    ) {
        public AvailabilitySnapshot {
            offers = List.copyOf(offers);
        }
    }

    public record Movie(
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
            Instant metadataAsOf,
            String searchableText,
            double popularityScore,
            long ratingCount,
            boolean catalogVisible,
            boolean uiReady,
            AvailabilitySnapshot availability
    ) {
        public Movie {
            genres = List.copyOf(genres);
            productionCountries = List.copyOf(productionCountries);
            directors = List.copyOf(directors);
            cast = List.copyOf(cast);
        }
    }

    public record SimilarityReason(String code, String label) {
    }

    public record SimilarityItem(UUID targetMovieId, List<SimilarityReason> reasons) {
        public SimilarityItem {
            reasons = List.copyOf(reasons);
        }
    }

    public record CatalogSnapshot(
            String catalogVersion,
            String similarityVersion,
            List<Movie> movies,
            List<Genre> genres,
            List<Country> countries,
            List<Provider> providers,
            Map<UUID, List<SimilarityItem>> similarities
    ) {
        public CatalogSnapshot {
            movies = List.copyOf(movies);
            genres = List.copyOf(genres);
            countries = List.copyOf(countries);
            providers = List.copyOf(providers);
            similarities = Map.copyOf(similarities);
        }
    }
}
