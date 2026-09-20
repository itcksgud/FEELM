import unittest

import fm_v2_features as base

from fm_v5_features import (
    build_feature_map, fit_scaler, fit_vocabulary, parse_movie, schema_document, vectorize,
)


class FmV5FeatureTest(unittest.TestCase):
    def setUp(self):
        self.movies = {
            1: parse_movie(1, "One (2001)", "Drama|Comedy"),
            2: parse_movie(2, "Two (2002)", "Drama"),
            3: parse_movie(3, "Three (2003)", "Horror"),
        }
        self.rows = [{
            "target_movie_id": 1, "total_history_count": 2, "supported_history_count": 2,
            "history": [
                {"movie_id": 2, "rating": 5.0, "event_at": 1},
                {"movie_id": 3, "rating": 2.0, "event_at": 2},
            ],
        }]

    def test_sparse_movie_and_content_namespaces(self):
        vocabulary = fit_vocabulary(self.rows, self.movies, 4.0)
        scaler = fit_scaler(self.rows, self.movies, 4.0)
        schema = schema_document(vocabulary, scaler)
        features, _ = build_feature_map(self.rows[0], self.movies[1], self.movies, vocabulary, scaler, 4.0)
        indices, values = vectorize(features, schema["ordered_names"])
        self.assertEqual((indices, values), base.vectorize(features, schema["ordered_names"]))
        self.assertEqual((indices, values), vectorize(features, schema["ordered_names"]))
        active = {schema["ordered_names"][index] for index in indices}
        self.assertIn("candidate.movie_id:1", active)
        self.assertIn("history_positive.movie_id:2", active)
        self.assertIn("history_negative.movie_id:3", active)
        self.assertTrue(all(0.0 < value <= 1.0 for value in values))
        profile = schema["profiles"]["sparse_history_content_fm_v5"]
        self.assertTrue(set(profile["candidate_factor_indices"]) & set(indices))
        self.assertTrue(set(profile["positive_factor_indices"]) & set(indices))
        self.assertTrue(set(profile["negative_factor_indices"]) & set(indices))

    def test_n0_has_no_nonmissing_history_factor(self):
        vocabulary = fit_vocabulary(self.rows, self.movies, 4.0)
        scaler = fit_scaler(self.rows, self.movies, 4.0)
        schema = schema_document(vocabulary, scaler)
        row = {"target_movie_id": 1, "total_history_count": 0, "supported_history_count": 0, "history": []}
        features, _ = build_feature_map(row, self.movies[1], self.movies, vocabulary, scaler, 4.0)
        indices, _ = vectorize(features, schema["ordered_names"])
        active_names = [schema["ordered_names"][index] for index in indices]
        history_active = [name for name in active_names if name.startswith("history_positive.") or
                          name.startswith("history_negative.")]
        self.assertTrue(history_active)
        self.assertTrue(all(name.endswith(":__MISSING__") for name in history_active))
        factor_indices = set(schema["profiles"]["sparse_history_content_fm_v5"]["positive_factor_indices"] +
                             schema["profiles"]["sparse_history_content_fm_v5"]["negative_factor_indices"])
        self.assertFalse(factor_indices.intersection(indices))


if __name__ == "__main__":
    unittest.main()
