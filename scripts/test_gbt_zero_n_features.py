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
        signed = set(profile_names("signed_genre_affinity"))
        self.assertLess(movie, history)
        self.assertLess(history, relation)
        self.assertLess(relation, signed)
        for profile in (movie, history, relation):
            self.assertTrue(any(name.startswith("shared.candidate.") for name in profile))
            self.assertTrue(any(name.startswith("gbt.model_input.") for name in profile))

    def test_signed_genre_affinity_is_candidate_specific_and_centered(self):
        history = [
            {"event_id": "2", "movie_id": 2, "rating": 5.0, "event_at": 10},
            {"event_id": "3", "movie_id": 3, "rating": 1.0, "event_at": 20},
        ]
        drama = build_features(self.movies[1], history, self.movies, 2, 2)
        horror = build_features(self.movies[3], history, self.movies, 2, 2)
        self.assertGreater(drama["gbt.model_input.signed_genre_affinity_Drama"], 0)
        self.assertLess(horror["gbt.model_input.signed_genre_affinity_Horror"], 0)

    def test_popularity_profile_adds_only_asof_candidate_features(self):
        base = set(profile_names("signed_genre_affinity"))
        popular = set(profile_names("popularity_signed_affinity"))
        self.assertLess(base, popular)
        row = build_features(
            self.movies[1], [], self.movies, 0, 0,
            {"log_support": 0.75, "bayesian_mean_scaled": 0.8},
        )
        self.assertEqual(row["shared.candidate.train_asof_log_support"], 0.75)
        self.assertEqual(row["shared.candidate.train_asof_bayesian_mean_scaled"], 0.8)

    def test_hidden_suffix_count_does_not_change_prefix_features(self):
        history = [{"event_id": "2", "movie_id": 2, "rating": 4.5, "event_at": 10}]
        short = build_features(self.movies[1], history, self.movies, 1, 1)
        long = build_features(self.movies[1], history, self.movies, 100, 1)
        self.assertEqual(short, long)


if __name__ == "__main__":
    unittest.main()
