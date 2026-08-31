from __future__ import annotations

import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from tmdb_korean_origin_audit import (  # noqa: E402
    histogram_quantiles,
    interaction_tier,
    load_token,
    parse_release_year,
)


class TmdbKoreanOriginAuditTest(unittest.TestCase):
    def test_interaction_tiers_cover_boundaries(self) -> None:
        self.assertEqual(interaction_tier(0), "zero")
        self.assertEqual(interaction_tier(9), "one_to_nine")
        self.assertEqual(interaction_tier(10), "ten_to_ninety_nine")
        self.assertEqual(interaction_tier(10_000), "ten_thousand_plus")

    def test_release_year_uses_terminal_year(self) -> None:
        self.assertEqual(parse_release_year("The Host (2006)"), 2006)
        self.assertIsNone(parse_release_year("No year"))

    def test_histogram_quantiles(self) -> None:
        self.assertEqual(
            histogram_quantiles(Counter({1: 2, 5: 1, 20: 1}), (0.5, 0.75, 0.9)),
            {"p50": 1, "p75": 5, "p90": 20},
        )

    def test_load_token_accepts_v3_key_without_logging_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("TMDB_API_TOKEN=0123456789abcdef0123456789abcdef\n", encoding="utf-8")
            self.assertEqual(load_token(path), "0123456789abcdef0123456789abcdef")


if __name__ == "__main__":
    unittest.main()
