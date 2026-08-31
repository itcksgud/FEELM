from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from recommendation_protocol_v4 import (  # noqa: E402
    cold_fold,
    custom_squared_rank_alpha,
    density_bucket,
    equal_share_request_weight,
    item_bucket,
    linear_ndcg_at_5,
)


class RecommendationContentColdV2PreflightTest(unittest.TestCase):
    def test_item_density_and_fold_hashes_are_stable(self) -> None:
        self.assertEqual(item_bucket(1), item_bucket(1))
        self.assertEqual(density_bucket(1), density_bucket(1))
        self.assertIn(cold_fold(1), range(5))

    def test_custom_alpha_golden_fixture(self) -> None:
        observed, expected, alpha = custom_squared_rank_alpha([[0, 0], [0, 1, 2]])
        self.assertAlmostEqual(1.2, observed)
        self.assertAlmostEqual(1.6, expected)
        self.assertAlmostEqual(0.25, alpha)

    def test_unknown_and_undersized_units_are_excluded(self) -> None:
        observed, expected, alpha = custom_squared_rank_alpha([[0, None], [0, 2]])
        self.assertEqual(4.0, observed)
        self.assertEqual(4.0, expected)
        self.assertEqual(0.0, alpha)

    def test_linear_ndcg_golden_fixture(self) -> None:
        dcg, idcg, ndcg = linear_ndcg_at_5(
            [0.5, 1.0, 0.0, 0.0, 0.0],
            [1.0, 0.5, 0.0, 0.0, 0.0],
        )
        self.assertAlmostEqual(1.1309297535714575, dcg, places=12)
        self.assertAlmostEqual(1.3154648767857289, idcg, places=12)
        self.assertAlmostEqual(0.8597186998521972, ndcg, places=12)
        self.assertFalse(math.isclose(ndcg, 1.0))

    def test_equal_share_request_weight(self) -> None:
        self.assertEqual(2.0, equal_share_request_weight(2.0, [0.0, 1.0, 2.0]))
        self.assertIsNone(equal_share_request_weight(2.0, []))


if __name__ == "__main__":
    unittest.main()
