package com.feelm.catalog.c6.service;

import com.feelm.catalog.c2.input.ActiveRatingInputPort;
import com.feelm.catalog.c6.api.C6ApiDtos.MovieSummary;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Profile;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

@Component
@Profile("local")
@ConditionalOnProperty(name = "catalog.c6.local.enabled", havingValue = "true")
public class PostgresC6ExperimentDataAdapter implements C6ExperimentDataPort {
    private static final String GENRE_SEPARATOR = "\u001f";
    private final JdbcTemplate jdbc;
    private final NamedParameterJdbcTemplate namedJdbc;

    public PostgresC6ExperimentDataAdapter(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
        this.namedJdbc = new NamedParameterJdbcTemplate(jdbc);
    }

    @Override
    @Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ)
    public DataSnapshot load(UUID actorUserId, String catalogVersion, List<UUID> candidateMovieIds) {
        if (actorUserId == null || catalogVersion == null || catalogVersion.isBlank()
                || candidateMovieIds == null || candidateMovieIds.stream().anyMatch(java.util.Objects::isNull)
                || candidateMovieIds.stream().distinct().count() != candidateMovieIds.size()) {
            throw new IllegalArgumentException("C6 experiment data request is invalid");
        }
        String activeVersion = jdbc.queryForObject(
                "SELECT public_version FROM catalog_version WHERE status = 'ACTIVE'", String.class
        );
        if (!catalogVersion.equals(activeVersion)) {
            throw new IllegalStateException("C6 candidate and active catalog versions differ");
        }

        List<ActiveRatingInputPort.RatingInput> ratings = jdbc.query("""
                SELECT movie_id, value, revision
                  FROM rating
                 WHERE user_id = ? AND logical_status = 'ACTIVE' AND value BETWEEN 1 AND 5
                 ORDER BY updated_at DESC, movie_id
                """, (rs, row) -> new ActiveRatingInputPort.RatingInput(
                rs.getObject("movie_id", UUID.class), rs.getInt("value"), rs.getInt("revision")
        ), actorUserId);
        Set<UUID> ratedIds = new HashSet<>();
        ratings.forEach(rating -> ratedIds.add(rating.movieId()));
        List<UUID> eligible = candidateMovieIds.stream().filter(id -> !ratedIds.contains(id)).toList();
        Map<UUID, MovieSummary> movies = movies(eligible);
        if (!movies.keySet().equals(Set.copyOf(eligible))) {
            throw new IllegalStateException("C6 active candidate catalog is incomplete");
        }

        List<TasteAggregate> taste = jdbc.query("""
                SELECT ta.dimension_type,
                       CASE ta.dimension_type
                         WHEN 'GENRE' THEN g.id::text
                         WHEN 'COUNTRY' THEN c.code::text
                         WHEN 'DIRECTOR' THEN p.id::text
                       END AS public_key,
                       CASE ta.dimension_type
                         WHEN 'GENRE' THEN g.display_name_ko
                         WHEN 'COUNTRY' THEN c.display_name_ko
                         WHEN 'DIRECTOR' THEN p.display_name
                       END AS display_name,
                       ta.rating_count, ta.rating_sum
                  FROM taste_aggregate ta
                  LEFT JOIN genre g
                    ON ta.dimension_type = 'GENRE' AND g.id::text = ta.dimension_key AND g.active = true
                  LEFT JOIN country c
                    ON ta.dimension_type = 'COUNTRY' AND c.code::text = ta.dimension_key
                  LEFT JOIN person p
                    ON ta.dimension_type = 'DIRECTOR' AND p.id::text = ta.dimension_key
                 WHERE ta.user_id = ? AND ta.rating_count > 0
                   AND CASE ta.dimension_type
                         WHEN 'GENRE' THEN g.id::text
                         WHEN 'COUNTRY' THEN c.code::text
                         WHEN 'DIRECTOR' THEN p.id::text
                       END IS NOT NULL
                """, (rs, row) -> new TasteAggregate(
                rs.getString("dimension_type"), rs.getString("public_key"), rs.getString("display_name"),
                rs.getInt("rating_count"), rs.getInt("rating_sum")
        ), actorUserId);
        return new DataSnapshot(ratings, eligible, movies, taste);
    }

    private Map<UUID, MovieSummary> movies(List<UUID> ids) {
        if (ids.isEmpty()) return Map.of();
        MapSqlParameterSource parameters = new MapSqlParameterSource("movieIds", ids);
        List<MovieRow> rows = namedJdbc.query("""
                SELECT p.movie_id,
                       coalesce(
                         (SELECT ml.title FROM movie_localization ml
                           WHERE ml.catalog_version_id = p.catalog_version_id AND ml.movie_id = p.movie_id
                           ORDER BY CASE ml.locale WHEN 'ko-KR' THEN 0 WHEN 'en-US' THEN 1 ELSE 2 END, ml.locale
                           LIMIT 1),
                         p.original_title
                       ) AS display_title,
                       p.poster_path,
                       extract(year from p.release_date)::int AS release_year,
                       coalesce(string_agg(g.display_name_ko, E'\\x1f'
                           ORDER BY mg.display_order, g.display_order, g.id), '') AS genres
                  FROM catalog_version cv
                  JOIN movie_catalog_projection p ON p.catalog_version_id = cv.id
                  LEFT JOIN movie_genre mg
                    ON mg.catalog_version_id = p.catalog_version_id AND mg.movie_id = p.movie_id
                  LEFT JOIN genre g ON g.id = mg.genre_id AND g.active = true
                 WHERE cv.status = 'ACTIVE' AND p.movie_id IN (:movieIds)
                   AND p.identity_status = 'IDENTITY_VERIFIED'
                   AND p.visibility_status IN ('UI_READY', 'CATALOG_VISIBLE')
                   AND p.deleted = false
                 GROUP BY p.catalog_version_id, p.movie_id, p.original_title,
                          p.poster_path, p.release_date
                """, parameters, (rs, row) -> new MovieRow(
                rs.getObject("movie_id", UUID.class), rs.getString("display_title"),
                rs.getString("poster_path"), (Integer) rs.getObject("release_year"),
                rs.getString("genres")
        ));
        Map<UUID, MovieRow> byId = new HashMap<>();
        for (MovieRow row : rows) {
            if (byId.put(row.movieId(), row) != null) {
                throw new IllegalStateException("C6 duplicate movie catalog row");
            }
        }
        Map<UUID, MovieSummary> ordered = new LinkedHashMap<>();
        for (UUID id : ids) {
            MovieRow row = byId.get(id);
            if (row == null) continue;
            ordered.put(id, new MovieSummary(
                    id, row.title(), safePosterUrl(row.posterPath()), row.releaseYear(), genres(row.genres())
            ));
        }
        return ordered;
    }

    private static List<String> genres(String joined) {
        if (joined == null || joined.isEmpty()) return List.of();
        List<String> result = new ArrayList<>();
        for (String value : joined.split(GENRE_SEPARATOR, -1)) {
            if (!value.isBlank() && !result.contains(value)) result.add(value);
        }
        return List.copyOf(result);
    }

    private static String safePosterUrl(String path) {
        if (path == null || !path.matches("^/[A-Za-z0-9_./-]+$") || path.contains("..") || path.contains("//")) {
            return null;
        }
        return "https://image.tmdb.org/t/p/w500" + path;
    }

    private record MovieRow(UUID movieId, String title, String posterPath, Integer releaseYear, String genres) {
    }
}
