package com.feelm.catalog.service;

import com.feelm.catalog.api.ApiException;
import com.feelm.catalog.api.CatalogApiDtos;
import com.feelm.catalog.domain.CatalogModels;
import com.feelm.catalog.domain.CatalogModels.AvailabilitySnapshot;
import com.feelm.catalog.domain.CatalogModels.AvailabilityStatus;
import com.feelm.catalog.domain.CatalogModels.CatalogSnapshot;
import com.feelm.catalog.domain.CatalogModels.Freshness;
import com.feelm.catalog.domain.CatalogModels.MonetizationType;
import com.feelm.catalog.domain.CatalogModels.Movie;
import com.feelm.catalog.domain.CatalogModels.MovieSort;
import com.feelm.catalog.domain.CatalogModels.Offer;
import com.feelm.catalog.domain.CatalogModels.Provider;
import com.feelm.catalog.domain.CatalogReadPort;
import com.feelm.catalog.security.CatalogUserContext;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Clock;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HexFormat;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.function.Function;
import java.util.stream.Collectors;

@Service
public final class CatalogService {
    private static final String REGION = "KR";
    private static final String AVAILABILITY_SOURCE = "TMDB_JUSTWATCH";

    private final CatalogReadPort readPort;
    private final CursorCodec cursorCodec;
    private final Clock clock;

    public CatalogService(CatalogReadPort readPort, CursorCodec cursorCodec, Clock clock) {
        this.readPort = readPort;
        this.cursorCodec = cursorCodec;
        this.clock = clock;
    }

    public CatalogApiDtos.MovieSearchPage search(CatalogSearchQuery rawQuery, CatalogUserContext user) {
        CatalogSnapshot snapshot = readPort.loadActiveSnapshot();
        CatalogSearchQuery query = normalize(rawQuery);
        validate(query);

        Map<UUID, Provider> providers = providerMap(snapshot);
        List<Movie> matches = snapshot.movies().stream()
                .filter(Movie::catalogVisible)
                .filter(movie -> query.query() != null || query.sort() != MovieSort.POPULARITY || movie.uiReady())
                .filter(movie -> matchesQuery(movie, query.query()))
                .filter(movie -> matchesGenres(movie, query.genreIds()))
                .filter(movie -> matchesCountries(movie, query.countryCodes()))
                .filter(movie -> matchesYears(movie, query.releaseYearFrom(), query.releaseYearTo()))
                .filter(movie -> matchesOffers(movie, query.ottProviderIds(), query.ottMonetizationTypes()))
                .sorted(movieComparator(query))
                .toList();

        String filterHash = filterHash(query);
        int offset = query.cursor() == null || query.cursor().isBlank()
                ? 0
                : cursorCodec.decodeOffset(query.cursor(), snapshot.catalogVersion(), filterHash);
        if (offset > matches.size()) {
            throw ApiException.invalidCursor();
        }

        int end = Math.min(offset + query.limit(), matches.size());
        List<CatalogApiDtos.MovieCard> items = matches.subList(offset, end).stream()
                .map(movie -> toCard(movie, providers, user))
                .toList();
        boolean hasNext = end < matches.size();
        String nextCursor = hasNext ? cursorCodec.encode(snapshot.catalogVersion(), filterHash, end) : null;

        return new CatalogApiDtos.MovieSearchPage(
                snapshot.catalogVersion(),
                matches.size(),
                hasNext,
                nextCursor,
                items,
                new CatalogApiDtos.AppliedFilters(
                        query.query(), query.genreIds(), query.countryCodes(), query.releaseYearFrom(), query.releaseYearTo(),
                        query.ottProviderIds(), query.ottMonetizationTypes(), query.sort()
                )
        );
    }

    public CatalogApiDtos.MovieDetail getMovie(UUID movieId, CatalogUserContext user) {
        CatalogSnapshot snapshot = readPort.loadActiveSnapshot();
        Movie movie = visibleMovie(snapshot, movieId);
        Map<UUID, Provider> providers = providerMap(snapshot);
        return new CatalogApiDtos.MovieDetail(
                snapshot.catalogVersion(),
                movie.movieId(),
                movie.displayTitle(),
                movie.displayTitleLocale(),
                movie.originalTitle(),
                movie.overview(),
                movie.overviewLocale(),
                movie.releaseDate(),
                movie.runtimeMinutes(),
                movie.posterUrl(),
                movie.backdropUrl(),
                movie.genres().stream().map(this::toGenre).toList(),
                movie.productionCountries().stream().map(country -> new CatalogApiDtos.Country(country.code(), country.name())).toList(),
                movie.directors().stream().map(this::toCredit).toList(),
                movie.cast().stream().limit(10).map(this::toCredit).toList(),
                toExternalRating(movie.externalRating()),
                availabilityPreview(movie.availability(), providers, user),
                movie.metadataAsOf()
        );
    }

    public CatalogApiDtos.SimilarMovieResponse getSimilar(UUID movieId, int limit, CatalogUserContext user) {
        CatalogSnapshot snapshot = readPort.loadActiveSnapshot();
        visibleMovie(snapshot, movieId);
        Map<UUID, Movie> movies = snapshot.movies().stream()
                .collect(Collectors.toMap(Movie::movieId, Function.identity()));
        Map<UUID, Provider> providers = providerMap(snapshot);

        List<CatalogApiDtos.SimilarMovieItem> items = snapshot.similarities().getOrDefault(movieId, List.of()).stream()
                .filter(item -> !item.targetMovieId().equals(movieId))
                .map(item -> Map.entry(item, movies.get(item.targetMovieId())))
                .filter(entry -> entry.getValue() != null && entry.getValue().uiReady())
                .limit(limit)
                .map(entry -> new CatalogApiDtos.SimilarMovieItem(
                        toCard(entry.getValue(), providers, user),
                        entry.getKey().reasons().stream().limit(3)
                                .map(reason -> new CatalogApiDtos.SimilarityReason(reason.code(), reason.label()))
                                .toList()
                ))
                .toList();
        return new CatalogApiDtos.SimilarMovieResponse(snapshot.catalogVersion(), snapshot.similarityVersion(), items);
    }

    public CatalogApiDtos.OttAvailability getOttOffers(UUID movieId, CatalogUserContext user) {
        CatalogSnapshot snapshot = readPort.loadActiveSnapshot();
        Movie movie = visibleMovie(snapshot, movieId);
        Map<UUID, Provider> providers = providerMap(snapshot);
        AvailabilityView view = availabilityView(movie.availability());

        List<CatalogApiDtos.OttOfferGroup> groups = new ArrayList<>();
        if (view.status() != AvailabilityStatus.UNKNOWN) {
            for (MonetizationType type : MonetizationType.values()) {
                List<CatalogApiDtos.OttOffer> offers = view.offers().stream()
                        .filter(offer -> offer.monetizationType() == type)
                        .sorted(offerComparator(providers, user))
                        .map(offer -> toOffer(offer, providers.get(offer.providerId()), user))
                        .toList();
                if (!offers.isEmpty()) {
                    groups.add(new CatalogApiDtos.OttOfferGroup(type, offers));
                }
            }
        }
        return new CatalogApiDtos.OttAvailability(
                snapshot.catalogVersion(), movieId, REGION, view.status(), view.freshness(), view.snapshotAt(),
                AVAILABILITY_SOURCE, List.copyOf(groups)
        );
    }

    public CatalogApiDtos.GenreListResponse listGenres() {
        CatalogSnapshot snapshot = readPort.loadActiveSnapshot();
        List<CatalogApiDtos.Genre> items = snapshot.genres().stream()
                .sorted(Comparator.comparingInt(CatalogModels.Genre::displayOrder).thenComparing(genre -> genre.genreId().toString()))
                .map(this::toGenre)
                .toList();
        return new CatalogApiDtos.GenreListResponse(snapshot.catalogVersion(), items);
    }

    public CatalogApiDtos.CountryListResponse listCountries() {
        CatalogSnapshot snapshot = readPort.loadActiveSnapshot();
        List<CatalogApiDtos.Country> items = snapshot.countries().stream()
                .sorted(Comparator.comparing(CatalogModels.Country::code))
                .map(country -> new CatalogApiDtos.Country(country.code(), country.name()))
                .toList();
        return new CatalogApiDtos.CountryListResponse(snapshot.catalogVersion(), items);
    }

    public CatalogApiDtos.OttProviderListResponse listProviders(CatalogUserContext user) {
        CatalogSnapshot snapshot = readPort.loadActiveSnapshot();
        List<CatalogApiDtos.OttProvider> items = snapshot.providers().stream()
                .sorted(providerComparator(user))
                .map(provider -> new CatalogApiDtos.OttProvider(
                        provider.providerId(), provider.name(), provider.logoUrl(), provider.displayPriority(), subscribed(provider, user)
                ))
                .toList();
        return new CatalogApiDtos.OttProviderListResponse(snapshot.catalogVersion(), REGION, items);
    }

    private CatalogSearchQuery normalize(CatalogSearchQuery query) {
        String normalizedQuery = normalizeText(query.query());
        MovieSort sort = query.sort() != null
                ? query.sort()
                : (normalizedQuery == null ? MovieSort.POPULARITY : MovieSort.RELEVANCE);
        List<String> countries = query.countryCodes().stream().map(value -> value.toUpperCase(Locale.ROOT)).distinct().toList();
        return new CatalogSearchQuery(
                normalizedQuery,
                query.genreIds().stream().distinct().toList(),
                countries,
                query.releaseYearFrom(),
                query.releaseYearTo(),
                query.ottProviderIds().stream().distinct().toList(),
                query.ottMonetizationTypes().stream().distinct().toList(),
                sort,
                query.cursor(),
                query.limit()
        );
    }

    private void validate(CatalogSearchQuery query) {
        if (query.releaseYearFrom() != null && query.releaseYearTo() != null
                && query.releaseYearFrom() > query.releaseYearTo()) {
            throw new ApiException(
                    HttpStatus.BAD_REQUEST,
                    "VALIDATION_ERROR",
                    "요청 값을 확인해 주세요.",
                    List.of(new CatalogApiDtos.FieldError("releaseYearFrom", "must_be_less_than_or_equal_to_releaseYearTo"))
            );
        }
    }

    private Movie visibleMovie(CatalogSnapshot snapshot, UUID movieId) {
        return snapshot.movies().stream()
                .filter(movie -> movie.movieId().equals(movieId) && movie.catalogVisible())
                .findFirst()
                .orElseThrow(ApiException::movieNotFound);
    }

    private boolean matchesQuery(Movie movie, String query) {
        return query == null || normalizeSearchText(movie.searchableText()).contains(query.toLowerCase(Locale.ROOT));
    }

    private boolean matchesGenres(Movie movie, List<UUID> genreIds) {
        return genreIds.isEmpty() || movie.genres().stream().anyMatch(genre -> genreIds.contains(genre.genreId()));
    }

    private boolean matchesCountries(Movie movie, List<String> countryCodes) {
        return countryCodes.isEmpty() || movie.productionCountries().stream().anyMatch(country -> countryCodes.contains(country.code()));
    }

    private boolean matchesYears(Movie movie, Integer from, Integer to) {
        if (from == null && to == null) {
            return true;
        }
        if (movie.releaseDate() == null) {
            return false;
        }
        int year = movie.releaseDate().getYear();
        return (from == null || year >= from) && (to == null || year <= to);
    }

    private boolean matchesOffers(Movie movie, List<UUID> providerIds, List<MonetizationType> types) {
        if (providerIds.isEmpty()) {
            return true;
        }
        AvailabilityView view = availabilityView(movie.availability());
        return view.status() == AvailabilityStatus.LISTED && view.offers().stream()
                .anyMatch(offer -> providerIds.contains(offer.providerId()) && types.contains(offer.monetizationType()));
    }

    private Comparator<Movie> movieComparator(CatalogSearchQuery query) {
        Comparator<Movie> tieBreaker = Comparator.comparing(movie -> movie.movieId().toString());
        return switch (query.sort()) {
            case RELEVANCE -> Comparator.comparingInt((Movie movie) -> relevance(movie, query.query())).reversed()
                    .thenComparing(Comparator.comparingDouble(Movie::popularityScore).reversed())
                    .thenComparing(tieBreaker);
            case POPULARITY -> Comparator.comparingDouble(Movie::popularityScore).reversed().thenComparing(tieBreaker);
            case RELEASE_DATE_DESC -> Comparator.comparing(Movie::releaseDate, Comparator.nullsLast(Comparator.reverseOrder()))
                    .thenComparing(tieBreaker);
            case RATING_COUNT_DESC -> Comparator.comparingLong(Movie::ratingCount).reversed().thenComparing(tieBreaker);
        };
    }

    private int relevance(Movie movie, String query) {
        if (query == null) {
            return 0;
        }
        String title = movie.displayTitle().toLowerCase(Locale.ROOT);
        String normalized = query.toLowerCase(Locale.ROOT);
        if (title.startsWith(normalized)) {
            return 3;
        }
        if (title.contains(normalized)) {
            return 2;
        }
        return 1;
    }

    private CatalogApiDtos.MovieCard toCard(Movie movie, Map<UUID, Provider> providers, CatalogUserContext user) {
        return new CatalogApiDtos.MovieCard(
                movie.movieId(),
                movie.displayTitle(),
                movie.displayTitleLocale(),
                movie.releaseDate() == null ? null : movie.releaseDate().getYear(),
                movie.posterUrl(),
                movie.genres().stream().map(this::toGenre).toList(),
                toExternalRating(movie.externalRating()),
                availabilityPreview(movie.availability(), providers, user)
        );
    }

    private CatalogApiDtos.AvailabilityPreview availabilityPreview(
            AvailabilitySnapshot snapshot,
            Map<UUID, Provider> providers,
            CatalogUserContext user
    ) {
        AvailabilityView view = availabilityView(snapshot);
        List<CatalogApiDtos.ProviderBadge> badges = view.offers().stream()
                .filter(offer -> offer.monetizationType() == MonetizationType.FLATRATE)
                .sorted(offerComparator(providers, user))
                .map(offer -> providers.get(offer.providerId()))
                .filter(java.util.Objects::nonNull)
                .distinct()
                .limit(3)
                .map(provider -> new CatalogApiDtos.ProviderBadge(
                        provider.providerId(), provider.name(), provider.logoUrl(), subscribed(provider, user)
                ))
                .toList();
        return new CatalogApiDtos.AvailabilityPreview(REGION, view.status(), view.freshness(), view.snapshotAt(), badges);
    }

    private AvailabilityView availabilityView(AvailabilitySnapshot snapshot) {
        Instant now = clock.instant();
        if (snapshot == null || now.isAfter(snapshot.serveUntil())) {
            return new AvailabilityView(AvailabilityStatus.UNKNOWN, Freshness.UNKNOWN, null, List.of());
        }
        AvailabilityStatus status = snapshot.fetchStatus() == CatalogModels.SnapshotFetchStatus.SUCCESS_EMPTY
                ? AvailabilityStatus.NONE_LISTED
                : AvailabilityStatus.LISTED;
        Freshness freshness = now.isAfter(snapshot.freshUntil()) ? Freshness.STALE : Freshness.FRESH;
        return new AvailabilityView(status, freshness, snapshot.fetchedAt(), snapshot.offers());
    }

    private Comparator<Offer> offerComparator(Map<UUID, Provider> providers, CatalogUserContext user) {
        return Comparator.<Offer, Boolean>comparing(offer -> !isSubscribed(offer.providerId(), user))
                .thenComparingInt(offer -> providerPriority(providers.get(offer.providerId())))
                .thenComparing(offer -> providerName(providers.get(offer.providerId())))
                .thenComparing(offer -> offer.offerId().toString());
    }

    private Comparator<Provider> providerComparator(CatalogUserContext user) {
        return Comparator.<Provider, Boolean>comparing(provider -> !isSubscribed(provider.providerId(), user))
                .thenComparingInt(Provider::displayPriority)
                .thenComparing(Provider::name)
                .thenComparing(provider -> provider.providerId().toString());
    }

    private CatalogApiDtos.OttOffer toOffer(Offer offer, Provider provider, CatalogUserContext user) {
        CatalogApiDtos.OfferLink link = offer.landingUrl() == null
                ? null
                : new CatalogApiDtos.OfferLink(offer.linkType(), offer.landingUrl());
        return new CatalogApiDtos.OttOffer(
                offer.offerId(),
                offer.providerId(),
                provider == null ? "Unknown provider" : provider.name(),
                provider == null ? null : provider.logoUrl(),
                offer.monetizationType(),
                user.authenticated() ? isSubscribed(offer.providerId(), user) : null,
                link
        );
    }

    private CatalogApiDtos.Genre toGenre(CatalogModels.Genre genre) {
        return new CatalogApiDtos.Genre(genre.genreId(), genre.name());
    }

    private CatalogApiDtos.PersonCredit toCredit(CatalogModels.PersonCredit credit) {
        return new CatalogApiDtos.PersonCredit(
                credit.personId(), credit.name(), credit.role().name(), credit.character(), credit.order()
        );
    }

    private CatalogApiDtos.ExternalRating toExternalRating(CatalogModels.ExternalRating rating) {
        return rating == null ? null : new CatalogApiDtos.ExternalRating(
                rating.source(), rating.value(), rating.scale(), rating.ratingCount()
        );
    }

    private Map<UUID, Provider> providerMap(CatalogSnapshot snapshot) {
        return snapshot.providers().stream().collect(Collectors.toUnmodifiableMap(Provider::providerId, Function.identity()));
    }

    private Boolean subscribed(Provider provider, CatalogUserContext user) {
        return user.authenticated() ? isSubscribed(provider.providerId(), user) : null;
    }

    private boolean isSubscribed(UUID providerId, CatalogUserContext user) {
        return user.authenticated() && user.subscribedProviderIds().contains(providerId);
    }

    private int providerPriority(Provider provider) {
        return provider == null ? Integer.MAX_VALUE : provider.displayPriority();
    }

    private String providerName(Provider provider) {
        return provider == null ? "" : provider.name();
    }

    private String filterHash(CatalogSearchQuery query) {
        String canonical = String.join("|",
                String.valueOf(query.query()),
                sorted(query.genreIds()),
                sorted(query.countryCodes()),
                String.valueOf(query.releaseYearFrom()),
                String.valueOf(query.releaseYearTo()),
                sorted(query.ottProviderIds()),
                sorted(query.ottMonetizationTypes()),
                query.sort().name(),
                String.valueOf(query.limit())
        );
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(canonical.getBytes(StandardCharsets.UTF_8)));
        } catch (Exception exception) {
            throw new IllegalStateException("SHA-256 is unavailable", exception);
        }
    }

    private String sorted(List<?> values) {
        return values.stream().map(Object::toString).sorted().collect(Collectors.joining(","));
    }

    private String normalizeText(String value) {
        if (value == null) {
            return null;
        }
        String normalized = value.trim().replaceAll("\\s+", " ");
        return normalized.isEmpty() ? null : normalized;
    }

    private String normalizeSearchText(String value) {
        return value == null ? "" : value.toLowerCase(Locale.ROOT).replaceAll("\\s+", " ").trim();
    }

    private record AvailabilityView(
            AvailabilityStatus status,
            Freshness freshness,
            Instant snapshotAt,
            List<Offer> offers
    ) {
    }
}
