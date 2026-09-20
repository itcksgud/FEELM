import unittest

from fm_zero_n_features import (
    Movie,
    build_feature_map,
    fit_normalizer,
    fit_vocabulary,
    schema_document,
    vectorize,
)


class SparseFeatureTest(unittest.TestCase):
    def setUp(self):
        self.movies = {
            1: Movie(1, 2000, ("Action", "Comedy")),
            2: Movie(2, 2005, ("Drama",)),
            3: Movie(3, 2010, ("Action",)),
            4: Movie(4, 2012, ("UnseenGenre",)),
        }
        self.rows = [
            {"target_movie_id": 1, "total_history_count": 2, "supported_history_count": 2,
             "history": [{"movie_id": 2, "rating": 4.5}, {"movie_id": 3, "rating": 2.0}]},
            {"target_movie_id": 2, "total_history_count": 0, "supported_history_count": 0, "history": []},
        ]
        self.vocabulary = fit_vocabulary(self.rows, self.movies, 4.0)
        self.normalizer = fit_normalizer(self.rows, self.movies, 4.0)
        self.schema = schema_document(self.vocabulary, self.normalizer)

    def test_oov_indices_are_namespace_specific(self):
        values = list(self.schema["oov_indices"].values())
        self.assertEqual(len(values), len(set(values)))
        self.assertGreaterEqual(len(values), 16)

    def test_linear_and_full_fm_use_exact_same_vector(self):
        self.assertEqual(self.schema["profiles"]["sparse_linear_only"]["indices"],
                         self.schema["profiles"]["sparse_history_content_fm"]["indices"])

    def test_unseen_candidate_genre_uses_only_candidate_oov(self):
        features, diagnostics = build_feature_map(
            self.rows[0], self.movies[4], self.movies, self.vocabulary, self.normalizer, 4.0
        )
        self.assertIn("candidate.genre:__OOV__", features)
        self.assertEqual(diagnostics["oov"]["candidate.genre"], 1)
        indices, values = vectorize(features, self.schema["ordered_names"])
        self.assertEqual(indices, sorted(indices))
        self.assertEqual(len(indices), len(values))

    def test_unavailable_blocks_are_truthfully_declared(self):
        self.assertEqual(set(self.schema["unavailable_blocks"]), {"director", "actor", "keyword"})
        self.assertTrue(all(value["status"] == "UNAVAILABLE" for value in self.schema["unavailable_blocks"].values()))
        features, _ = build_feature_map(
            self.rows[0], self.movies[1], self.movies, self.vocabulary, self.normalizer, 4.0
        )
        self.assertFalse(any(".director:" in name or ".actor:" in name or ".keyword:" in name for name in features))


if __name__ == "__main__":
    unittest.main()
