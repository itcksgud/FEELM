import unittest
from datetime import datetime, timezone

from gbt_zero_n_build_input import Rating
from gbt_zero_n_prefix_build_input import (
    K_VALUES,
    TARGET_JUDGMENTS,
    find_anchor,
    history_view,
    prediction_year,
)
from gbt_zero_n_prepare import TemporalPopularityIndex


class GbtZeroNPrefixDesignTest(unittest.TestCase):
    def test_anchor_produces_strict_past_and_ten_future_judgments(self):
        events = [
            Rating(movie_id=index, rating=4.0, timestamp=index * 10, event_id=str(index))
            for index in range(1, 81)
        ]
        years = {index: 1960 for index in range(1, 81)}
        case = find_anchor(7, "VALIDATION", events, years, 622)
        self.assertIsNotNone(case)
        assert case is not None
        self.assertGreaterEqual(len(case.source_history), max(K_VALUES))
        self.assertEqual(len(case.targets), TARGET_JUDGMENTS)
        self.assertTrue(all(row.timestamp < case.prediction_at for row in case.source_history))
        self.assertTrue(all(row.timestamp > case.prediction_at for row in case.targets))

    def test_anchor_never_splits_timestamp_ties(self):
        events = [
            Rating(movie_id=index, rating=4.0, timestamp=(index // 5) * 10, event_id=str(index))
            for index in range(1, 101)
        ]
        years = {index: 1960 for index in range(1, 101)}
        case = find_anchor(8, "TRAIN", events, years, 622)
        self.assertIsNotNone(case)
        assert case is not None
        history_times = {row.timestamp for row in case.source_history}
        target_times = {row.timestamp for row in case.targets}
        self.assertTrue(history_times.isdisjoint(target_times))

    def test_anchor_excludes_movies_released_after_prediction_year(self):
        first_target_at = int(datetime(2000, 1, 1, tzinfo=timezone.utc).timestamp())
        events = [
            Rating(
                movie_id=index,
                rating=4.0,
                timestamp=first_target_at - 1000 + index,
                event_id=str(index),
            )
            for index in range(1, 51)
        ]
        events.extend(
            Rating(
                movie_id=index,
                rating=4.0,
                timestamp=first_target_at + index - 51,
                event_id=str(index),
            )
            for index in range(51, 62)
        )
        years = {index: 1999 for index in range(1, 62)}
        years[51] = 2000

        case = find_anchor(9, "VALIDATION", events, years, 622)

        self.assertIsNotNone(case)
        assert case is not None
        self.assertEqual(
            datetime.fromtimestamp(case.prediction_at, timezone.utc).year,
            1999,
        )
        self.assertNotIn(51, {row.movie_id for row in case.targets})
        self.assertTrue(all(years[row.movie_id] <= 1999 for row in case.targets))
        self.assertEqual(prediction_year(case.prediction_at), 1999)

    def test_controlled_view_preserves_hidden_total_without_marking_it_full(self):
        events = [
            Rating(movie_id=index, rating=4.0, timestamp=index * 10, event_id=str(index))
            for index in range(1, 61)
        ]
        case = type("Case", (), {"source_history": events})()
        history, counts = history_view(case, 5)
        self.assertEqual([row.movie_id for row in history], [56, 57, 58, 59, 60])
        self.assertEqual(counts["total_history_count"], 60)
        self.assertEqual(counts["provided_history_count"], 5)
        self.assertEqual(counts["supported_history_count"], 5)
        self.assertFalse(counts["is_full_history"])
        self.assertTrue(counts["is_controlled_prefix"])

    def test_temporal_popularity_uses_only_strictly_prior_train_events(self):
        index = TemporalPopularityIndex(
            {1: [(10, 5.0), (20, 1.0)], 2: [(10, 3.0)]},
            [(10, 5.0), (20, 1.0), (10, 3.0)],
            train_users=2,
            prior_count=2.0,
        )
        at_20 = index.score(1, 20)
        at_21 = index.score(1, 21)
        self.assertAlmostEqual(at_20["count_score"], 0.6931471805599453)
        self.assertAlmostEqual(at_21["count_score"], 1.0986122886681098)
        self.assertGreater(at_20["bayes_score"], at_21["bayes_score"])


if __name__ == "__main__":
    unittest.main()
