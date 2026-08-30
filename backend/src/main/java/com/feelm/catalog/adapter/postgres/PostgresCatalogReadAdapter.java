package com.feelm.catalog.adapter.postgres;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.feelm.catalog.domain.CatalogModels;
import com.feelm.catalog.domain.CatalogModels.AvailabilitySnapshot;
import com.feelm.catalog.domain.CatalogModels.CatalogSnapshot;
import com.feelm.catalog.domain.CatalogModels.Country;
import com.feelm.catalog.domain.CatalogModels.CreditRole;
import com.feelm.catalog.domain.CatalogModels.ExternalRating;
import com.feelm.catalog.domain.CatalogModels.Genre;
import com.feelm.catalog.domain.CatalogModels.LinkType;
import com.feelm.catalog.domain.CatalogModels.MonetizationType;
import com.feelm.catalog.domain.CatalogModels.Movie;
import com.feelm.catalog.domain.CatalogModels.Offer;
import com.feelm.catalog.domain.CatalogModels.PersonCredit;
import com.feelm.catalog.domain.CatalogModels.Provider;
import com.feelm.catalog.domain.CatalogModels.SimilarityItem;
import com.feelm.catalog.domain.CatalogModels.SimilarityReason;
import com.feelm.catalog.domain.CatalogModels.SnapshotFetchStatus;
import com.feelm.catalog.domain.CatalogReadPort;
import org.springframework.context.annotation.Profile;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.math.BigDecimal;
import java.net.URI;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;

@Repository
@Profile("postgres")
public class PostgresCatalogReadAdapter implements CatalogReadPort {
    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;
    private volatile CachedSnapshot cache;

    public PostgresCatalogReadAdapter(JdbcTemplate jdbc, ObjectMapper objectMapper) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
    }

    @Override
    public CatalogSnapshot loadActiveSnapshot() {
        Version version = jdbc.queryForObject(
                "SELECT id, public_version FROM catalog_version WHERE status = 'ACTIVE'",
                (resultSet, rowNumber) -> new Version(resultSet.getObject("id", UUID.class), resultSet.getString("public_version"))
        );
        if (version == null) {
            throw new DataIntegrityViolationException("active catalog version is missing");
        }

        CachedSnapshot current = cache;
        if (current != null && current.versionId().equals(version.id())) {
            return current.snapshot();
        }
        synchronized (this) {
            current = cache;
            if (current != null && current.versionId().equals(version.id())) {
                return current.snapshot();
            }
            CatalogSnapshot loaded = loadVersion(version);
            cache = new CachedSnapshot(version.id(), loaded);
            return loaded;
        }
    }

    private CatalogSnapshot loadVersion(Version version) {
        Map<UUID, MutableMovie> movies = loadMovies(version.id());
        loadLocalizations(version.id(), movies);
        List<Genre> genres = loadGenres(version.id(), movies);
        List<Country> countries = loadCountries(version.id(), movies);
        loadCredits(version.id(), movies);
        List<Provider> providers = loadProviders(version.id());
        loadAvailability(version.id(), movies);
        SimilarityResult similarity = loadSimilarities(version.id());

        List<Movie> immutableMovies = movies.values().stream()
                .map(MutableMovie::build)
                .toList();
        return new CatalogSnapshot(
                version.publicVersion(), similarity.version(), immutableMovies, genres, countries, providers, similarity.items()
        );
    }

    private Map<UUID, MutableMovie> loadMovies(UUID versionId) {
        Map<UUID, MutableMovie> movies = new LinkedHashMap<>();
        jdbc.query("""
                SELECT p.movie_id, p.media_type, p.identity_status, p.visibility_status,
                       p.original_title, p.original_language, p.release_date, p.runtime_minutes,
                       p.poster_path, p.backdrop_path, p.tmdb_vote_average, p.tmdb_vote_count,
                       p.metadata_fetched_at, p.deleted,
                       COALESCE(s.normalized_title_terms, '') AS title_terms,
                       COALESCE(s.normalized_person_terms, '') AS person_terms,
                       COALESCE(s.popularity_score, 0) AS popularity_score
                  FROM movie_catalog_projection p
                  LEFT JOIN movie_search_document s
                    ON s.catalog_version_id = p.catalog_version_id AND s.movie_id = p.movie_id
                 WHERE p.catalog_version_id = ?
                """, resultSet -> {
            UUID movieId = resultSet.getObject("movie_id", UUID.class);
            movies.put(movieId, new MutableMovie(
                    movieId,
                    resultSet.getString("media_type"),
                    resultSet.getString("identity_status"),
                    resultSet.getString("visibility_status"),
                    resultSet.getString("original_title"),
                    resultSet.getString("original_language"),
                    resultSet.getObject("release_date", LocalDate.class),
                    getNullableInteger(resultSet, "runtime_minutes"),
                    image(resultSet.getString("poster_path"), "w500"),
                    image(resultSet.getString("backdrop_path"), "w780"),
                    resultSet.getBigDecimal("tmdb_vote_average"),
                    resultSet.getLong("tmdb_vote_count"),
                    getInstant(resultSet, "metadata_fetched_at"),
                    resultSet.getBoolean("deleted"),
                    resultSet.getString("title_terms") + " " + resultSet.getString("person_terms"),
                    resultSet.getDouble("popularity_score")
            ));
        }, versionId);
        return movies;
    }

    private void loadLocalizations(UUID versionId, Map<UUID, MutableMovie> movies) {
        jdbc.query("""
                SELECT movie_id, locale, title, overview
                  FROM movie_localization
                 WHERE catalog_version_id = ?
                 ORDER BY movie_id, locale
                """, resultSet -> {
            MutableMovie movie = movies.get(resultSet.getObject("movie_id", UUID.class));
            if (movie != null) {
                movie.localizations.put(resultSet.getString("locale"), new Localization(
                        blankToNull(resultSet.getString("title")), blankToNull(resultSet.getString("overview"))
                ));
            }
        }, versionId);
    }

    private List<Genre> loadGenres(UUID versionId, Map<UUID, MutableMovie> movies) {
        Map<UUID, Genre> genres = new LinkedHashMap<>();
        jdbc.query("""
                SELECT mg.movie_id, g.id, g.display_name_ko, g.display_order, mg.display_order AS movie_order
                  FROM movie_genre mg
                  JOIN genre g ON g.id = mg.genre_id AND g.active
                 WHERE mg.catalog_version_id = ?
                 ORDER BY g.display_order, g.id, mg.movie_id, mg.display_order
                """, resultSet -> {
            Genre genre = new Genre(
                    resultSet.getObject("id", UUID.class), resultSet.getString("display_name_ko"), resultSet.getInt("display_order")
            );
            genres.putIfAbsent(genre.genreId(), genre);
            MutableMovie movie = movies.get(resultSet.getObject("movie_id", UUID.class));
            if (movie != null) {
                movie.genres.add(new Ordered<>(resultSet.getInt("movie_order"), genre));
            }
        }, versionId);
        return List.copyOf(genres.values());
    }

    private List<Country> loadCountries(UUID versionId, Map<UUID, MutableMovie> movies) {
        Map<String, Country> countries = new LinkedHashMap<>();
        jdbc.query("""
                SELECT mc.movie_id, c.code, c.display_name_ko, mc.display_order
                  FROM movie_country mc
                  JOIN country c ON c.code = mc.country_code
                 WHERE mc.catalog_version_id = ?
                 ORDER BY c.code, mc.movie_id, mc.display_order
                """, resultSet -> {
            Country country = new Country(resultSet.getString("code").trim(), resultSet.getString("display_name_ko"));
            countries.putIfAbsent(country.code(), country);
            MutableMovie movie = movies.get(resultSet.getObject("movie_id", UUID.class));
            if (movie != null) {
                movie.countries.add(new Ordered<>(resultSet.getInt("display_order"), country));
            }
        }, versionId);
        return List.copyOf(countries.values());
    }

    private void loadCredits(UUID versionId, Map<UUID, MutableMovie> movies) {
        jdbc.query("""
                SELECT mc.movie_id, p.id AS person_id, p.display_name, mc.credit_type,
                       mc.character_name, mc.credit_order
                  FROM movie_credit mc
                  JOIN person p ON p.id = mc.person_id
                 WHERE mc.catalog_version_id = ?
                 ORDER BY mc.movie_id, mc.credit_type, mc.credit_order, p.id
                """, resultSet -> {
            MutableMovie movie = movies.get(resultSet.getObject("movie_id", UUID.class));
            if (movie == null) {
                return;
            }
            CreditRole role = CreditRole.valueOf(resultSet.getString("credit_type"));
            PersonCredit credit = new PersonCredit(
                    resultSet.getObject("person_id", UUID.class),
                    resultSet.getString("display_name"),
                    role,
                    blankToNull(resultSet.getString("character_name")),
                    resultSet.getInt("credit_order")
            );
            if (role == CreditRole.DIRECTOR) {
                movie.directors.add(credit);
            } else {
                movie.cast.add(credit);
            }
        }, versionId);
    }

    private List<Provider> loadProviders(UUID versionId) {
        return jdbc.query("""
                SELECT DISTINCT p.id, p.display_name, p.logo_path, p.display_priority
                  FROM ott_provider p
                  JOIN movie_ott_offer o ON o.provider_id = p.id
                  JOIN movie_availability_snapshot s ON s.id = o.snapshot_id
                 WHERE p.active AND s.catalog_version_id = ? AND s.region = 'KR'
                 ORDER BY p.display_priority, p.display_name, p.id
                """, (resultSet, rowNumber) -> new Provider(
                resultSet.getObject("id", UUID.class),
                resultSet.getString("display_name"),
                image(resultSet.getString("logo_path"), "original"),
                resultSet.getInt("display_priority")
        ), versionId);
    }

    private void loadAvailability(UUID versionId, Map<UUID, MutableMovie> movies) {
        Map<UUID, UUID> selectedSnapshot = new HashMap<>();
        jdbc.query("""
                WITH ranked AS (
                    SELECT s.*, row_number() OVER (PARTITION BY s.movie_id ORDER BY s.fetched_at DESC, s.id) AS rn
                      FROM movie_availability_snapshot s
                     WHERE s.catalog_version_id = ? AND s.region = 'KR'
                       AND s.fetch_status IN ('SUCCESS_LISTED', 'SUCCESS_EMPTY')
                )
                SELECT s.id AS snapshot_id, s.movie_id, s.fetch_status, s.fetched_at, s.fresh_until, s.serve_until,
                       o.id AS offer_id, o.provider_id, o.monetization_type, o.link_type, o.landing_url
                  FROM ranked s
                  LEFT JOIN movie_ott_offer o ON o.snapshot_id = s.id
                 WHERE s.rn = 1
                 ORDER BY s.movie_id, o.source_display_priority, o.provider_id, o.monetization_type
                """, resultSet -> {
            UUID movieId = resultSet.getObject("movie_id", UUID.class);
            MutableMovie movie = movies.get(movieId);
            if (movie == null) {
                return;
            }
            UUID snapshotId = resultSet.getObject("snapshot_id", UUID.class);
            if (!snapshotId.equals(selectedSnapshot.get(movieId))) {
                selectedSnapshot.put(movieId, snapshotId);
                movie.availability = new MutableAvailability(
                        SnapshotFetchStatus.valueOf(resultSet.getString("fetch_status")),
                        getInstant(resultSet, "fetched_at"),
                        getInstant(resultSet, "fresh_until"),
                        getInstant(resultSet, "serve_until")
                );
            }
            UUID offerId = resultSet.getObject("offer_id", UUID.class);
            if (offerId != null) {
                movie.availability.offers.add(new Offer(
                        offerId,
                        resultSet.getObject("provider_id", UUID.class),
                        MonetizationType.valueOf(resultSet.getString("monetization_type")),
                        LinkType.valueOf(resultSet.getString("link_type")),
                        uri(resultSet.getString("landing_url"))
                ));
            }
        }, versionId);
    }

    private SimilarityResult loadSimilarities(UUID versionId) {
        Map<UUID, List<SimilarityItem>> items = new LinkedHashMap<>();
        String[] version = {null};
        jdbc.query("""
                SELECT source_movie_id, target_movie_id, similarity_version, reasons
                  FROM movie_similarity
                 WHERE catalog_version_id = ?
                 ORDER BY source_movie_id, similarity_version, rank, target_movie_id
                """, resultSet -> {
            String rowVersion = resultSet.getString("similarity_version");
            if (version[0] == null) {
                version[0] = rowVersion;
            }
            items.computeIfAbsent(resultSet.getObject("source_movie_id", UUID.class), ignored -> new ArrayList<>())
                    .add(new SimilarityItem(
                            resultSet.getObject("target_movie_id", UUID.class),
                            parseReasons(resultSet.getString("reasons"))
                    ));
        }, versionId);
        return new SimilarityResult(version[0] == null ? "similarity-unavailable" : version[0], items);
    }

    private List<SimilarityReason> parseReasons(String json) {
        try {
            JsonNode root = objectMapper.readTree(json == null ? "[]" : json);
            List<SimilarityReason> reasons = new ArrayList<>();
            for (JsonNode item : root) {
                String code = item.path("code").asText();
                String label = item.path("label").asText();
                if (!code.isBlank() && !label.isBlank()) {
                    reasons.add(new SimilarityReason(code, label));
                }
            }
            return List.copyOf(reasons);
        } catch (Exception exception) {
            throw new DataIntegrityViolationException("invalid similarity reason JSON", exception);
        }
    }

    private static Integer getNullableInteger(ResultSet resultSet, String column) throws SQLException {
        int value = resultSet.getInt(column);
        return resultSet.wasNull() ? null : value;
    }

    private static Instant getInstant(ResultSet resultSet, String column) throws SQLException {
        OffsetDateTime value = resultSet.getObject(column, OffsetDateTime.class);
        return value == null ? null : value.toInstant();
    }

    private static String blankToNull(String value) {
        return value == null || value.isBlank() ? null : value;
    }

    private static URI image(String path, String size) {
        if (path == null || path.isBlank()) {
            return null;
        }
        if (path.startsWith("http://") || path.startsWith("https://")) {
            return URI.create(path);
        }
        String normalized = path.startsWith("/") ? path : "/" + path;
        return URI.create("https://image.tmdb.org/t/p/" + size + normalized);
    }

    private static URI uri(String value) {
        return value == null || value.isBlank() ? null : URI.create(value);
    }

    private record Version(UUID id, String publicVersion) {
    }

    /**
     * The active-version row is checked on every request. A publish or rollback changes its UUID,
     * so the next request atomically rebuilds this immutable projection and replaces the cache.
     */
    private record CachedSnapshot(UUID versionId, CatalogSnapshot snapshot) {
    }

    private record Localization(String title, String overview) {
    }

    private record Ordered<T>(int order, T value) {
    }

    private record SimilarityResult(String version, Map<UUID, List<SimilarityItem>> items) {
    }

    private static final class MutableAvailability {
        private final SnapshotFetchStatus status;
        private final Instant fetchedAt;
        private final Instant freshUntil;
        private final Instant serveUntil;
        private final List<Offer> offers = new ArrayList<>();

        private MutableAvailability(SnapshotFetchStatus status, Instant fetchedAt, Instant freshUntil, Instant serveUntil) {
            this.status = status;
            this.fetchedAt = fetchedAt;
            this.freshUntil = freshUntil;
            this.serveUntil = serveUntil;
        }

        private AvailabilitySnapshot build() {
            return new AvailabilitySnapshot(status, fetchedAt, freshUntil, serveUntil, offers);
        }
    }

    private static final class MutableMovie {
        private final UUID movieId;
        private final String mediaType;
        private final String identityStatus;
        private final String visibilityStatus;
        private final String originalTitle;
        private final String originalLanguage;
        private final LocalDate releaseDate;
        private final Integer runtimeMinutes;
        private final URI posterUrl;
        private final URI backdropUrl;
        private final BigDecimal voteAverage;
        private final long voteCount;
        private final Instant metadataAsOf;
        private final boolean deleted;
        private final String searchTerms;
        private final double popularity;
        private final Map<String, Localization> localizations = new HashMap<>();
        private final List<Ordered<Genre>> genres = new ArrayList<>();
        private final List<Ordered<Country>> countries = new ArrayList<>();
        private final List<PersonCredit> directors = new ArrayList<>();
        private final List<PersonCredit> cast = new ArrayList<>();
        private MutableAvailability availability;

        private MutableMovie(
                UUID movieId, String mediaType, String identityStatus, String visibilityStatus, String originalTitle,
                String originalLanguage, LocalDate releaseDate, Integer runtimeMinutes, URI posterUrl, URI backdropUrl,
                BigDecimal voteAverage, long voteCount, Instant metadataAsOf, boolean deleted, String searchTerms,
                double popularity
        ) {
            this.movieId = movieId;
            this.mediaType = mediaType;
            this.identityStatus = identityStatus;
            this.visibilityStatus = visibilityStatus;
            this.originalTitle = originalTitle;
            this.originalLanguage = originalLanguage;
            this.releaseDate = releaseDate;
            this.runtimeMinutes = runtimeMinutes;
            this.posterUrl = posterUrl;
            this.backdropUrl = backdropUrl;
            this.voteAverage = voteAverage;
            this.voteCount = voteCount;
            this.metadataAsOf = metadataAsOf;
            this.deleted = deleted;
            this.searchTerms = searchTerms;
            this.popularity = popularity;
        }

        private Movie build() {
            SelectedText title = selectText(true);
            SelectedText overview = selectText(false);
            boolean visible = "MOVIE".equals(mediaType)
                    && "IDENTITY_VERIFIED".equals(identityStatus)
                    && !deleted
                    && ("CATALOG_VISIBLE".equals(visibilityStatus) || "UI_READY".equals(visibilityStatus));
            List<Genre> orderedGenres = genres.stream().sorted(Comparator.comparingInt(Ordered::order)).map(Ordered::value).toList();
            if (visible && (title.value() == null || overview.value() == null || orderedGenres.isEmpty())) {
                throw new DataIntegrityViolationException("visible movie is missing its required projection: " + movieId);
            }
            List<PersonCredit> orderedDirectors = directors.stream().sorted(Comparator.comparingInt(PersonCredit::order)).toList();
            List<PersonCredit> orderedCast = cast.stream().sorted(Comparator.comparingInt(PersonCredit::order)).toList();
            String searchable = String.join(" ", title.value() == null ? "" : title.value(), originalTitle, searchTerms);
            return new Movie(
                    movieId,
                    title.value() == null ? originalTitle : title.value(),
                    title.locale(),
                    originalTitle,
                    overview.value(),
                    overview.locale(),
                    releaseDate,
                    runtimeMinutes,
                    posterUrl,
                    backdropUrl,
                    orderedGenres,
                    countries.stream().sorted(Comparator.comparingInt(Ordered::order)).map(Ordered::value).toList(),
                    orderedDirectors,
                    orderedCast,
                    voteAverage == null ? null : new ExternalRating("TMDB", voteAverage, 10, voteCount),
                    metadataAsOf,
                    searchable,
                    popularity,
                    voteCount,
                    visible,
                    visible && "UI_READY".equals(visibilityStatus),
                    availability == null ? null : availability.build()
            );
        }

        private SelectedText selectText(boolean title) {
            List<String> candidates = new ArrayList<>(List.of("ko-KR", "en-US"));
            String originalLocale = normalizeLocale(originalLanguage);
            if (!candidates.contains(originalLocale)) {
                candidates.add(originalLocale);
            }
            for (String locale : candidates) {
                Localization localization = localizations.get(locale);
                String value = localization == null ? null : (title ? localization.title() : localization.overview());
                if (value != null && !value.isBlank()) {
                    return new SelectedText(value, locale);
                }
            }
            return title ? new SelectedText(originalTitle, originalLocale) : new SelectedText(null, originalLocale);
        }

        private static String normalizeLocale(String language) {
            if (language == null || language.isBlank()) {
                return "und";
            }
            return switch (language.toLowerCase(Locale.ROOT)) {
                case "ko" -> "ko-KR";
                case "en" -> "en-US";
                default -> language;
            };
        }
    }

    private record SelectedText(String value, String locale) {
    }
}
