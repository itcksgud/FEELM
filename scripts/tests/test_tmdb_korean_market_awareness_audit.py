from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from tmdb_korean_market_awareness_audit import (  # noqa: E402
    classify_foreign_proxy,
    normalize_tmdb,
    translation_has_title,
)


class TmdbKoreanMarketAwarenessAuditTest(unittest.TestCase):
    def test_korean_translation_requires_nonempty_title(self) -> None:
        payload = {
            "translations": {
                "translations": [
                    {"iso_639_1": "ko", "data": {"title": "인셉션"}},
                    {"iso_639_1": "en", "data": {"title": "Inception"}},
                ]
            }
        }
        self.assertTrue(translation_has_title(payload, "ko"))
        self.assertFalse(translation_has_title(payload, "ja"))

    def test_normalize_extracts_korean_market_signals(self) -> None:
        payload = {
            "id": 1,
            "title": "테스트",
            "original_title": "Test",
            "original_language": "en",
            "production_countries": [{"iso_3166_1": "US"}],
            "translations": {"translations": [{"iso_639_1": "ko", "data": {"title": "테스트"}}]},
            "release_dates": {
                "results": [
                    {"iso_3166_1": "KR", "release_dates": [{"type": 3}]}
                ]
            },
            "watch/providers": {
                "results": {
                    "KR": {"flatrate": [{"provider_name": "Provider"}]}
                }
            },
        }
        row = normalize_tmdb(payload)
        self.assertEqual(row["production_countries"], ["US"])
        self.assertTrue(row["has_korean_title_translation"])
        self.assertTrue(row["has_korean_theatrical_release"])
        self.assertTrue(row["has_current_korean_flatrate"])

    def test_proxy_levels_are_nested(self) -> None:
        row = {
            "status": "OK",
            "is_foreign": True,
            "rating_count": 1500,
            "has_korean_title_translation": True,
            "has_korean_release": True,
            "has_korean_theatrical_release": True,
            "has_current_korean_provider": False,
        }
        self.assertEqual(
            classify_foreign_proxy(row, 100, 1000),
            {"FOREIGN_BROAD", "FOREIGN_MODERATE", "FOREIGN_STRICT"},
        )

    def test_unknown_country_is_not_foreign(self) -> None:
        row = {
            "status": "OK",
            "is_foreign": False,
            "rating_count": 10000,
            "has_korean_title_translation": True,
            "has_korean_release": True,
            "has_korean_theatrical_release": True,
            "has_current_korean_provider": True,
        }
        self.assertEqual(classify_foreign_proxy(row, 100, 1000), set())


if __name__ == "__main__":
    unittest.main()
