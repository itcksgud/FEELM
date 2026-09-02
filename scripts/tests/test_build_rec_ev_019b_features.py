from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from build_rec_ev_019b_features import (
    MASK_CAST,
    MASK_DIRECTORS,
    MASK_KEYWORDS,
    MASK_OVERVIEW,
    TmdbClient,
    build_embedding_input,
    extract_features,
    format_imdb_id,
    merge_text,
    movie_sample_digest,
    select_recovery,
    validate_details_identity,
)


class FakeResponse:
    def __init__(self, status: int, body: dict, headers: dict | None = None) -> None:
        self.status_code = status
        self._body = body
        self.headers = headers or {}

    def json(self) -> dict:
        return self._body


def complete_details() -> dict:
    return {
        "id": 10,
        "imdb_id": "tt0000123",
        "title": "한국어 제목",
        "original_title": "Original",
        "overview": "한국어 줄거리",
        "original_language": "ko",
        "release_date": "2024-03-01",
        "runtime": 105,
        "genres": [{"id": 18, "name": "드라마"}],
        "credits": {
            "crew": [
                {"id": 7, "name": "감독", "job": "Director"},
                {"id": 7, "name": "감독", "job": "Director"},
                {"id": 8, "name": "작가", "job": "Writer"},
            ],
            "cast": [
                {"id": 3, "name": "셋", "order": 2},
                {"id": 1, "name": "하나", "order": 0},
                {"id": 2, "name": "둘", "order": 1},
            ],
        },
        "keywords": {"keywords": [{"id": 90, "name": "성장"}]},
        "popularity": 999.0,
        "vote_average": 9.9,
        "vote_count": 99999,
        "revenue": 100,
        "budget": 10,
    }


class RecEv019bFeaturesTest(unittest.TestCase):
    def test_contract_keeps_feature_superset_outside_candidate_authority(self) -> None:
        contract_path = SCRIPTS.parent / "docs/recommendation/contracts/rec-ev-019b-artifacts.json"
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        derivation = contract["candidate_derivation"]
        self.assertFalse(derivation["time_safe_candidate_authority"])
        self.assertIn("REC-EV-019A", derivation["downstream_scoring_rule"])
        self.assertIn("cutoff-safe", derivation["downstream_scoring_rule"])

    def test_sample_order_is_stable(self) -> None:
        first = sorted([9, 1, 5, 2], key=lambda value: (movie_sample_digest(value), value))
        second = sorted([2, 5, 1, 9], key=lambda value: (movie_sample_digest(value), value))
        self.assertEqual(first, second)

    def test_imdb_id_normalization(self) -> None:
        self.assertEqual("tt0000123", format_imdb_id("123"))
        self.assertEqual("tt12345678", format_imdb_id("tt12345678"))

    def test_language_merge_uses_english_only_for_blank_text(self) -> None:
        primary = complete_details()
        primary["overview"] = ""
        merged = merge_text(primary, {"title": "English title", "overview": "English overview"})
        self.assertEqual("한국어 제목", merged["display_title"])
        self.assertEqual("English overview", merged["overview_fallback"])

    def test_structured_extraction_and_mask_are_explicit(self) -> None:
        primary = complete_details()
        structured, text = extract_features(1, 10, primary, None)
        self.assertEqual([7], structured["director_ids"])
        self.assertEqual([1, 2, 3], structured["top5_cast_ids"])
        self.assertTrue(structured["feature_eligible"])
        self.assertTrue(text["feature_eligible"])
        self.assertEqual(0, structured["missing_mask"])
        for forbidden in ("popularity", "vote_average", "vote_count", "revenue", "budget"):
            self.assertNotIn(forbidden, structured)
            self.assertNotIn(forbidden, text)

    def test_optional_metadata_missing_does_not_remove_structured_eligibility(self) -> None:
        primary = complete_details()
        primary["runtime"] = None
        primary["credits"] = {"crew": [], "cast": []}
        primary["keywords"] = {"keywords": []}
        primary["overview"] = ""
        structured, text = extract_features(1, 10, primary, None)
        expected = 4 | MASK_DIRECTORS | MASK_CAST | MASK_KEYWORDS | MASK_OVERVIEW
        self.assertEqual(expected, structured["missing_mask"])
        self.assertTrue(structured["feature_eligible"])
        self.assertFalse(text["feature_eligible"])

    def test_identity_and_recovery_are_conservative(self) -> None:
        self.assertEqual((True, "VERIFIED"), validate_details_identity(complete_details(), 10, "tt0000123"))
        self.assertEqual("IMDB_ID_MISMATCH", validate_details_identity(complete_details(), 10, "tt9999999")[1])
        self.assertEqual(("RECOVER", 44, "SINGLE_MOVIE_RESULT"), select_recovery({"movie_results": [{"id": 44}]}))
        self.assertEqual("REVIEW", select_recovery({"movie_results": [{"id": 1}, {"id": 2}]})[0])
        self.assertEqual("TV", select_recovery({"movie_results": [], "tv_results": [{"id": 3}]})[0])

    def test_embedding_input_contains_no_forbidden_tmdb_scores(self) -> None:
        _, text = extract_features(1, 10, complete_details(), None)
        rendered = build_embedding_input(
            "{display_title}|{overview_fallback}|{genre_names}|{director_names}|{top5_cast_names}|{keyword_names}",
            "passage: ",
            text,
        )
        self.assertTrue(rendered.startswith("passage: "))
        for forbidden in ("popularity", "vote_average", "vote_count", "revenue", "budget"):
            self.assertNotIn(forbidden, rendered)

    def test_cache_is_resumed_and_never_serializes_token(self) -> None:
        calls: list[str] = []

        def fake_get(url: str, **kwargs):
            calls.append(url)
            self.assertEqual("secret-token", kwargs["params"]["api_key"])
            self.assertNotIn("Authorization", kwargs["headers"])
            return FakeResponse(200, complete_details())

        with tempfile.TemporaryDirectory() as directory:
            cache_root = Path(directory)
            first = TmdbClient("secret-token", cache_root, resume=True, refresh=False, get=fake_get, sleep=lambda _: None)
            self.assertFalse(first.details(10, "ko-KR")["cache_hit"])
            second = TmdbClient("secret-token", cache_root, resume=True, refresh=False, get=fake_get, sleep=lambda _: None)
            self.assertTrue(second.details(10, "ko-KR")["cache_hit"])
            self.assertEqual(1, len(calls))
            cache_text = next(cache_root.glob("*.json")).read_text(encoding="utf-8")
            self.assertNotIn("secret-token", cache_text)
            self.assertNotIn("Authorization", cache_text)

    def test_jwt_credential_uses_bearer_without_query_serialization(self) -> None:
        jwt = "ey.header.signature"

        def fake_get(url: str, **kwargs):
            self.assertEqual(f"Bearer {jwt}", kwargs["headers"]["Authorization"])
            self.assertNotIn("api_key", kwargs["params"])
            return FakeResponse(200, complete_details())

        with tempfile.TemporaryDirectory() as directory:
            client = TmdbClient(jwt, Path(directory), resume=False, refresh=False, get=fake_get, sleep=lambda _: None)
            client.details(10, "ko-KR")
            self.assertNotIn(jwt, next(Path(directory).glob("*.json")).read_text(encoding="utf-8"))

    def test_retry_then_success(self) -> None:
        responses = [FakeResponse(429, {"error": "rate"}), FakeResponse(200, complete_details())]
        with tempfile.TemporaryDirectory() as directory:
            client = TmdbClient(
                "token",
                Path(directory),
                resume=False,
                refresh=False,
                get=lambda *args, **kwargs: responses.pop(0),
                sleep=lambda _: None,
            )
            result = client.details(10, "ko-KR")
            self.assertEqual(200, result["status"])
            self.assertFalse(responses)


if __name__ == "__main__":
    unittest.main()
