from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from kmrd_feasibility_audit import (  # noqa: E402
    concentration,
    english_title_candidates,
    gini,
    movielens_title_candidates,
    normalize_title,
    parse_optional_year,
    parse_original_year,
    quantiles,
)


class KmrdFeasibilityAuditTest(unittest.TestCase):
    def test_original_year_comes_from_terminal_english_title_value(self) -> None:
        self.assertEqual(parse_original_year("Cinema Paradiso , 1988"), 1988)
        self.assertEqual(parse_original_year("Foo , Bar , 2001"), 2001)
        self.assertIsNone(parse_original_year("No year"))
        self.assertEqual(parse_optional_year("2016.0"), 2016)
        self.assertIsNone(parse_optional_year("-"))

    def test_english_title_candidates_remove_year_and_split_aliases(self) -> None:
        self.assertEqual(
            english_title_candidates("Vestida para matar , Dressed To Kill , 1980"),
            ["Vestida para matar", "Dressed To Kill"],
        )

    def test_normalization_ignores_articles_and_punctuation(self) -> None:
        self.assertEqual(normalize_title("The Matrix"), normalize_title("Matrix, The"))

    def test_movielens_title_candidates_preserve_main_and_alias(self) -> None:
        candidates, year = movielens_title_candidates("Host, The (Gwoemul) (2006)")
        self.assertEqual(year, 2006)
        self.assertIn("Host, The (Gwoemul)", candidates)
        self.assertIn("Gwoemul", candidates)
        self.assertIn("Host, The", candidates)

    def test_quantiles_use_nearest_rank(self) -> None:
        self.assertEqual(
            quantiles([1, 2, 3, 4], (0, 0.5, 1)),
            {"p00": 1, "p50": 2, "p100": 4},
        )

    def test_gini_and_concentration_are_bounded(self) -> None:
        self.assertEqual(gini([1, 1, 1]), 0.0)
        value = gini([0, 0, 10])
        self.assertGreater(value, 0.6)
        shares = concentration([10, 1, 1, 1], (0.25,))
        self.assertEqual(shares["top_25pct_items_interaction_share"], 0.769231)


if __name__ == "__main__":
    unittest.main()
