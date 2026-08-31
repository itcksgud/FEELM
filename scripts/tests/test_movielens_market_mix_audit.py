from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from movielens_market_mix_audit import (  # noqa: E402
    build_category_sets,
    dominant_category,
    famous_foreign,
    history_bin,
    nearest_rank,
)


class MovielensMarketMixAuditTest(unittest.TestCase):
    def test_famous_foreign_requires_all_strict_signals(self) -> None:
        base = {
            "status": "OK",
            "is_foreign": True,
            "rating_count": 1_000,
            "has_korean_title_translation": True,
            "has_korean_theatrical_release": True,
            "has_current_korean_provider": False,
        }
        self.assertTrue(famous_foreign(base))
        self.assertFalse(famous_foreign({**base, "rating_count": 999}))
        self.assertFalse(famous_foreign({**base, "is_foreign": False}))

    def test_categories_are_exclusive_and_exhaustive(self) -> None:
        origin = {"movielens": {"matched_items": [{"movie_id": 1}]}}
        awareness = {
            "head_items": [
                {
                    "status": "OK",
                    "movie_id": 1,
                    "is_korean_origin": True,
                    "is_foreign": False,
                },
                {
                    "status": "OK",
                    "movie_id": 2,
                    "is_korean_origin": False,
                    "is_foreign": True,
                    "rating_count": 2_000,
                    "has_korean_title_translation": True,
                    "has_korean_theatrical_release": True,
                    "has_current_korean_provider": False,
                },
            ]
        }
        result = build_category_sets({1, 2, 3}, origin, awareness)
        self.assertEqual(result["KOREAN_ORIGIN"], {1})
        self.assertEqual(result["FAMOUS_FOREIGN_PROXY"], {2})
        self.assertEqual(result["REMAINDER"], {3})
        self.assertEqual(set().union(*result.values()), {1, 2, 3})

    def test_nearest_rank_and_history_bins(self) -> None:
        self.assertEqual(nearest_rank([0.1, 0.2, 0.3, 0.4], 0.5), 0.2)
        self.assertEqual(history_bin(20), "20_49")
        self.assertEqual(history_bin(500), "500_PLUS")

    def test_dominant_category_reports_tie(self) -> None:
        self.assertEqual(
            dominant_category(
                {"KOREAN_ORIGIN": 1, "FAMOUS_FOREIGN_PROXY": 2, "REMAINDER": 2}
            ),
            "TIE",
        )


if __name__ == "__main__":
    unittest.main()
