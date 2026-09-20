import unittest

from fm_v2_features import Movie, build_feature_map, fit_scaler, fit_vocabulary, schema_document, vectorize


class FMV2FeatureTest(unittest.TestCase):
    def setUp(self):
        self.movies = {
            1: Movie(1, 2000, ("Action", "Comedy")),
            2: Movie(2, 2005, ("Drama",)),
            3: Movie(3, 2010, ("Action",)),
            4: Movie(4, None, ("Unseen",)),
        }
        self.rows = [
            {"target_movie_id": 1, "total_history_count": 2, "supported_history_count": 2,
             "history": [{"movie_id": 2, "rating": 4.5}, {"movie_id": 3, "rating": 2.0}]},
            {"target_movie_id": 2, "total_history_count": 0, "supported_history_count": 0, "history": []},
        ]
        self.vocabulary = fit_vocabulary(self.rows, self.movies, 4.0)
        self.scaler = fit_scaler(self.rows, self.movies, 4.0)
        self.schema = schema_document(self.vocabulary, self.scaler)

    def test_values_are_bounded_and_unseen_movie_uses_oov(self):
        features, _ = build_feature_map(self.rows[0], self.movies[4], self.movies,
                                        self.vocabulary, self.scaler, 4.0)
        self.assertIn("candidate.movie_id:__OOV__", features)
        self.assertTrue(all(0.0 < value <= 1.0 for value in features.values()))

    def test_n0_has_exactly_zero_history_factor_activity(self):
        features, _ = build_feature_map(self.rows[1], self.movies[2], self.movies,
                                        self.vocabulary, self.scaler, 4.0)
        indices, _ = vectorize(features, self.schema["ordered_names"])
        factor = set(self.schema["profiles"]["cross_hybrid_fm_v2"]["positive_factor_indices"] +
                     self.schema["profiles"]["cross_hybrid_fm_v2"]["negative_factor_indices"])
        self.assertFalse(factor.intersection(indices))
        self.assertIn("history.empty:indicator", features)
        self.assertNotIn("history.rating_mean:scaled", features)
        self.assertNotIn("history.year_mean:scaled", features)

    def test_profiles_are_unique_and_share_linear_vector(self):
        profiles = self.schema["profiles"]
        self.assertEqual(profiles["sparse_linear_v2"]["linear_indices"],
                         profiles["cross_content_fm_v2"]["linear_indices"])
        self.assertEqual(profiles["sparse_linear_v2"]["linear_indices"],
                         profiles["cross_hybrid_fm_v2"]["linear_indices"])
        self.assertNotEqual(profiles["cross_content_fm_v2"]["candidate_factor_indices"],
                            profiles["cross_hybrid_fm_v2"]["candidate_factor_indices"])


if __name__ == "__main__":
    unittest.main()
