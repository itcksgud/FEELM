from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from rec_ev_019c_data import align_text_embeddings, build_base_binary, build_final_candidate_core, build_structured_variants


class RecEv019CDataTest(unittest.TestCase):
    def test_final_candidate_is_identity_intersection(self) -> None:
        provisional = pd.DataFrame({
            "movie_id": [1, 2, 3], "tmdb_id": [11, 22, 33],
            "base_train_interaction_count": [3, 4, 5], "first_base_train_timestamp": [1, 2, 3],
            "identity_status": ["LINK_PRESENT"] * 3,
        })
        identity = pd.DataFrame({
            "movie_id": [1, 2, 3], "tmdb_id": [11, 22, 33],
            "identity_status": ["ML_TMDB_VERIFIED", "TMDB_NOT_FOUND", "RECOVERED_BY_IMDB"],
        })
        result = build_final_candidate_core(provisional, identity)
        self.assertEqual([1, 3], result["movie_id"].tolist())

    def test_base_binary_uses_chronology_and_never_invents_unknown(self) -> None:
        rows = []
        for user in ("a", "b"):
            for index, rating in enumerate((1.0, 5.0, 1.5, 4.5, 3.0), start=1):
                rows.append({"user_key": user, "movie_id": index, "rating": rating, "timestamp": index})
        users, matrix, counts, means = build_base_binary(
            pd.DataFrame(rows), np.asarray([1, 2, 3, 4], dtype=np.int64),
            shrinkage=0.0, like_min=0.15, dislike_max=-0.15,
        )
        self.assertEqual(["a", "b"], users.tolist())
        self.assertTrue(set(matrix.data.tolist()) <= {-1, 1})
        self.assertEqual(5.0, means[1])
        self.assertEqual([2, 2, 2, 2], counts.tolist())

    def test_structured_variants_and_text_align_to_candidate_order(self) -> None:
        structured = pd.DataFrame({
            "movie_id": [2, 1], "tmdb_id": [22, 11], "original_language": ["ko", "en"],
            "release_year": [2020, 1999], "runtime_minutes": [100, 130],
            "genre_ids": [[1], [2]], "director_ids": [[10], [20]], "top5_cast_ids": [[30], [40]],
            "keyword_ids": [[50], [60]], "missing_mask": [0, 0], "feature_eligible": [True, True],
        })
        variants, available = build_structured_variants(structured, np.asarray([1, 2, 3]))
        self.assertEqual({"FULL", "DROP_KEYWORDS", "DROP_PEOPLE", "CORE_ONLY_GENRE_LANGUAGE_DECADE_RUNTIME"}, set(variants))
        self.assertEqual([True, True, False], available.tolist())
        self.assertEqual(3, variants["FULL"].shape[0])

        text = pd.DataFrame({
            "movie_id": [2, 1], "embedding": [[0.0, 1.0], [1.0, 0.0]], "feature_eligible": [True, True],
        })
        matrix, text_available = align_text_embeddings(text, np.asarray([1, 2, 3]), dimension=2)
        np.testing.assert_array_equal(matrix[:2], np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32))
        self.assertEqual([True, True, False], text_available.tolist())


if __name__ == "__main__":
    unittest.main()
