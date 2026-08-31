from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from kobis_foreign_boxoffice_validation import (  # noqa: E402
    kobis_audience_comparable,
    legacy_pre_2004_items,
    match_kobis,
    merge_kobis_rows,
    normalize_title,
    parse_rows,
)


class KobisForeignBoxofficeValidationTest(unittest.TestCase):
    def test_parse_kobis_row(self) -> None:
        content = """
        <tr id="tr_0"><td id="td_rank">1</td><td id="td_movie">
        <a onclick="mstView('movie','20184889');return false;" title="어벤져스: 엔드게임">x</a></td>
        <td id="td_openDt">2019-04-24</td><td id="td_salesAcc">122,490</td>
        <td id="td_audiAcc">13,977,409</td><td id="td_scrnCnt">2,835</td>
        <td id="td_showCnt">246,411</td></tr>
        """
        rows = parse_rows(content, "YEARLY_FOREIGN_TOP", 2019)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kobis_movie_code"], "20184889")
        self.assertEqual(rows[0]["title_ko"], "어벤져스: 엔드게임")
        self.assertEqual(rows[0]["audience"], 13_977_409)

    def test_normalize_title_ignores_spacing_and_punctuation(self) -> None:
        self.assertEqual(normalize_title("어벤져스: 엔드게임"), normalize_title("어벤져스 엔드게임"))

    def test_merge_deduplicates_movie_code_and_filters_future(self) -> None:
        base = {
            "kobis_movie_code": "1",
            "title_ko": "영화",
            "open_date": "2019-01-01",
            "rank": 2,
            "sales": 10,
            "audience": 5,
            "screens": 1,
            "show_count": 1,
            "source": "YEARLY_FOREIGN_TOP",
            "query_year": 2019,
        }
        former = {**base, "rank": 1, "audience": 7, "source": "FORMER_FOREIGN_TOP", "query_year": None}
        future = {**base, "kobis_movie_code": "2", "open_date": "2024-01-01"}
        merged = merge_kobis_rows([base, former, future], date(2023, 10, 13))
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["audience"], 7)
        self.assertEqual(merged[0]["all_time_rank"], 1)

    def test_match_requires_unique_normalized_title(self) -> None:
        kobis = [{"kobis_movie_code": "1", "title_ko": "인셉션", "open_date": "2010-07-21"}]
        item = {
            "status": "OK",
            "movie_id": 10,
            "tmdb_id": 20,
            "movielens_title": "Inception (2010)",
            "movielens_release_year": 2010,
            "title_ko_response": "인셉션",
            "original_title": "Inception",
            "rating_count": 1000,
            "is_foreign": True,
            "has_korean_title_translation": True,
            "has_korean_release": True,
            "has_korean_theatrical_release": True,
            "has_current_korean_provider": False,
        }
        matches, unmatched = match_kobis(kobis, [item])
        self.assertEqual(len(matches), 1)
        self.assertFalse(unmatched)
        self.assertTrue(matches[0]["foreign_strict"])

    def test_titanic_is_legacy_candidate_without_kobis_label(self) -> None:
        item = {
            "status": "OK",
            "movie_id": 1721,
            "tmdb_id": 597,
            "movielens_title": "Titanic (1997)",
            "movielens_release_year": 1997,
            "title_ko_response": "타이타닉",
            "rating_count": 45_767,
            "is_foreign": True,
            "has_korean_title_translation": True,
            "has_korean_release": True,
            "has_korean_theatrical_release": True,
            "has_current_korean_provider": True,
        }
        selected = legacy_pre_2004_items([item])
        self.assertEqual([row["movie_id"] for row in selected], [1721])
        self.assertEqual(
            selected[0]["status"],
            "CANDIDATE_REQUIRES_SEARCH_OR_SURVEY_VALIDATION",
        )

    def test_pre_2004_rerelease_audience_is_not_comparable(self) -> None:
        self.assertFalse(kobis_audience_comparable({"open_date": "1998-02-20"}, 2004))
        self.assertTrue(kobis_audience_comparable({"open_date": "2004-01-01"}, 2004))


if __name__ == "__main__":
    unittest.main()
