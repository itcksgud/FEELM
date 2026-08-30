package com.feelm.catalog.c1.foundation;

import org.springframework.context.annotation.Profile;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

@Repository
@Profile({"postgres", "local"})
public class C1InvariantRepository {
    private final JdbcTemplate jdbc;

    public C1InvariantRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public ProjectionInvariant currentProjectionInvariant() {
        return jdbc.queryForObject("SELECT * FROM c1_projection_invariant", (resultSet, rowNumber) -> new ProjectionInvariant(
                resultSet.getLong("active_rating_count"),
                resultSet.getLong("frame_count"),
                resultSet.getLong("popcorn_count"),
                resultSet.getLong("orphan_frame_count"),
                resultSet.getLong("orphan_popcorn_count")
        ));
    }

    public record ProjectionInvariant(
            long activeRatingCount,
            long frameCount,
            long popcornCount,
            long orphanFrameCount,
            long orphanPopcornCount
    ) {
        public boolean valid() {
            return activeRatingCount == frameCount
                    && frameCount == popcornCount
                    && orphanFrameCount == 0
                    && orphanPopcornCount == 0;
        }
    }
}
