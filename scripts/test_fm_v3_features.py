import unittest

from fm_v3_features import Movie, build_feature_map, fit_scaler, fit_vocabulary, schema_document, vectorize


class FMV3FeatureTest(unittest.TestCase):
    def setUp(self):
        self.movies = {
            1: Movie(1, 2000, ("Action", "Comedy")),
            2: Movie(2, 2005, ("Drama",)),
            3: Movie(3, 2010, ("Action",)),
            4: Movie(4, None, ("Unseen",)),
        }
        self.fit_rows = [
            {"target_movie_id": 1, "total_history_count": 2, "supported_history_count": 2,
             "history": [{"movie_id": 2, "rating": 4.5}, {"movie_id": 3, "rating": 2.0}]},
            {"target_movie_id": 2, "total_history_count": 0, "supported_history_count": 0, "history": []},
        ]
        self.vocabulary = fit_vocabulary(self.fit_rows, self.movies, 4.0)
        self.scaler = fit_scaler(self.fit_rows, self.movies, 4.0)
        self.schema = schema_document(self.vocabulary, self.scaler)

    def test_preprocessing_declares_internal_fit_only(self):
        self.assertEqual(self.vocabulary["fit_internal_splits"], ["FIT"])
        self.assertEqual(self.scaler["fit_internal_splits"], ["FIT"])

    def test_movie_identity_is_absent_and_profiles_share_linear_vector(self):
        names = self.schema["ordered_names"]
        self.assertFalse(any(name.startswith("candidate.movie_id:") for name in names))
        self.assertFalse(self.schema["candidate_movie_id_feature"])
        self.assertEqual(self.schema["profiles"]["matched_additive_v3"]["linear_indices"],
                         self.schema["profiles"]["content_cross_factor_v3"]["linear_indices"])

    def test_n0_has_no_history_factor_activity(self):
        features, _ = build_feature_map(self.fit_rows[1], self.movies[2], self.movies,
                                        self.vocabulary, self.scaler, 4.0)
        indices, values = vectorize(features, self.schema["ordered_names"])
        profile = self.schema["profiles"]["content_cross_factor_v3"]
        history = set(profile["positive_factor_indices"] + profile["negative_factor_indices"])
        self.assertFalse(history.intersection(indices))
        self.assertTrue(all(0.0 < value <= 1.0 for value in values))


if __name__ == "__main__":
    unittest.main()
