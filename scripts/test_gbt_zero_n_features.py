import unittest

from gbt_zero_n_features import Movie, build_features, feature_names, profile_names


class GbtZeroNFeaturesTest(unittest.TestCase):
    def setUp(self):
        self.movies = {
            1: Movie(1, frozenset({"Drama", "Comedy"}), 2000),
            2: Movie(2, frozenset({"Drama"}), 1990),
            3: Movie(3, frozenset({"Horror"}), None),
        }

    def test_zero_history_is_supported(self):
        row = build_features(self.movies[1], [], self.movies, 0, 0)
        self.assertEqual(set(row), set(feature_names()))
        self.assertEqual(row["shared.history_response.present"], 0)
        self.assertEqual(row["gbt.model_input.relation_shared_genre_fraction"], 0)

    def test_positive_and_negative_relations_remain_distinct(self):
        history = [
            {"event_id": "2", "movie_id": 2, "rating": 4.5, "event_at": 10},
            {"event_id": "3", "movie_id": 3, "rating": 1.0, "event_at": 20},
        ]
        row = build_features(self.movies[1], history, self.movies, 2, 2)
        self.assertEqual(row["gbt.model_input.relation_positive_shared_fraction"], 0.5)
        self.assertEqual(row["gbt.model_input.relation_negative_shared_fraction"], 0.0)
        self.assertEqual(row["shared.history_response.negative_fraction"], 0.5)

    def test_ablation_profiles_are_nested(self):
        movie = set(profile_names("movie_only"))
        history = set(profile_names("history_aggregate"))
        relation = set(profile_names("response_relation"))
        self.assertLess(movie, history)
        self.assertLess(history, relation)
        for profile in (movie, history, relation):
            self.assertTrue(any(name.startswith("shared.candidate.") for name in profile))
            self.assertTrue(any(name.startswith("gbt.model_input.") for name in profile))


if __name__ == "__main__":
    unittest.main()
