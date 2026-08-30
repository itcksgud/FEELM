package com.feelm.catalog.c1.foundation;

import org.springframework.context.annotation.Profile;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.util.UUID;

@Repository
@Profile({"postgres", "local"})
public class C1RatingEligibilityRepository {
    private final JdbcTemplate jdbc;

    public C1RatingEligibilityRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public boolean isRatingEligible(UUID movieId) {
        Long count = jdbc.queryForObject(
                "SELECT count(*) FROM c1_rating_eligible_movie WHERE movie_id = ?",
                Long.class,
                movieId
        );
        return count != null && count == 1;
    }
}
