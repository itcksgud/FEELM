import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audit_strict_multiple_links_policy import required_links, contextual_evidence


class StrictMultipleLinksTest(unittest.TestCase):
    def test_weak_public_never_needs_only_one_link(self):
        for votes, expected in [(4499, 2), (1000, 2), (450, 3), (45, 4), (4, 5), (1, 5), (0, 5)]:
            with self.subTest(votes=votes):
                self.assertEqual(required_links(votes, 4500, 0, None)[1], expected)

    def test_strong_kobis_is_an_independent_public_route(self):
        self.assertEqual(required_links(1, 4500, 4, 0)[1], 0)
        self.assertEqual(required_links(1, 4500, 4, 1)[1], 3)

    def test_public_strength_and_cohort_gap_are_distinct(self):
        self.assertEqual(required_links(6000, 4500, 0, None)[1], 0)
        self.assertEqual(required_links(6000, 4500, 1, None)[1], 0)
        self.assertEqual(required_links(1000, 4500, 1, None)[1], 3)

    def test_missing_gap_is_rejected(self):
        with self.assertRaises(ValueError):
            required_links(0, 4500, float("nan"), None)

    def test_shared_actor_cannot_cross_country_genre_and_animation(self):
        candidate = pd.Series({"collection_ids": [], "director_ids": [], "top5_cast_ids": [9],
                               "origin_country_codes": ["US"], "genre_ids": [35]})
        history = pd.DataFrame({"movie_id": [1, 2, 3, 4], "score": [5, 5, 5, 5],
                                "collection_ids": [[], [], [], []], "director_ids": [[], [], [], []],
                                "top5_cast_ids": [[9], [9], [9], [9]],
                                "origin_country_codes": [["US"], ["JP"], ["US"], ["US"]],
                                "genre_ids": [[28], [35], [35, 16], [35]]})
        result = contextual_evidence(candidate, history)
        self.assertEqual(result["cast_context_rejected_movie_ids"], [1, 2, 3])
        self.assertEqual(result["positive"]["cast_sources"], 1)

    def test_verified_series_survives_actor_context_mismatch(self):
        candidate = pd.Series({"collection_ids": [10], "director_ids": [], "top5_cast_ids": [9],
                               "origin_country_codes": ["US"], "genre_ids": [35]})
        history = pd.DataFrame({"movie_id": [1], "score": [5], "collection_ids": [[10]],
                                "director_ids": [[]], "top5_cast_ids": [[9]],
                                "origin_country_codes": [["US"]], "genre_ids": [[28]]})
        self.assertEqual(contextual_evidence(candidate, history)["positive"]["series"], 1)


if __name__ == "__main__":
    unittest.main()
