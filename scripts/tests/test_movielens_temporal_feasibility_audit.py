from __future__ import annotations

import sys
import unittest
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from movielens_temporal_feasibility_audit import (  # noqa: E402
    KAccumulator,
    histogram_quantiles,
    parse_release_year,
    process_user,
    rating_code,
    relative_label,
    timestamp_year,
)


class TemporalFeasibilityAuditTest(unittest.TestCase):
    def test_release_year_uses_terminal_movie_year(self) -> None:
        self.assertEqual(parse_release_year("Parasite (2019)"), 2019)
        self.assertEqual(parse_release_year("Movie, The (1999)"), 1999)
        self.assertIsNone(parse_release_year("Unknown year"))

    def test_rating_code_preserves_half_star_scale(self) -> None:
        self.assertEqual(rating_code("0.5"), 1)
        self.assertEqual(rating_code("4.0"), 8)
        self.assertEqual(rating_code("5.0"), 10)

    def test_relative_label_uses_only_past_profile(self) -> None:
        profile = [0, 1, 0, 1, 0, 2, 0, 2, 0, 4]
        self.assertEqual(relative_label(profile, 10), "POSITIVE")
        self.assertEqual(relative_label(profile, 2), "NEGATIVE")
        self.assertEqual(relative_label(profile, 7), "NEUTRAL")

    def test_histogram_quantiles_are_nearest_rank(self) -> None:
        histogram = Counter({0: 1, 2: 2, 10: 1})
        self.assertEqual(
            histogram_quantiles(histogram, (0.25, 0.5, 0.9)),
            {"p25": 0, "p50": 2, "p90": 10},
        )

    def test_timestamp_year_uses_utc_boundaries(self) -> None:
        last_2022_second = int(datetime(2022, 12, 31, 23, 59, 59, tzinfo=timezone.utc).timestamp())
        first_2023_second = last_2022_second + 1
        self.assertEqual(timestamp_year(last_2022_second), 2022)
        self.assertEqual(timestamp_year(first_2023_second), 2023)

    def test_conservative_future_excludes_profile_boundary_day(self) -> None:
        day = 1_700_000_000 // 86_400 * 86_400
        events = [
            (day + offset, movie_id, rating)
            for offset, movie_id, rating in (
                (1, 1, 2),
                (2, 2, 4),
                (3, 3, 6),
                (4, 4, 8),
                (5, 5, 10),
                (6, 6, 10),
                (86_401, 7, 10),
            )
        ]
        accumulators = {5: KAccumulator()}
        process_user(
            events,
            day + 400 * 86_400,
            accumulators,
            Counter(),
            Counter(),
            Counter(),
            Counter(),
        )
        accumulator = accumulators[5]
        self.assertEqual(accumulator.boundary_same_utc_day_collision_users, 1)
        self.assertEqual(accumulator.time_horizons[7].event_histogram[2], 1)
        self.assertEqual(accumulator.time_horizons_next_utc_day[7].event_histogram[1], 1)


if __name__ == "__main__":
    unittest.main()
