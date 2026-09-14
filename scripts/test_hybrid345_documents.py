from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hybrid345_documents import (  # noqa: E402
    E5_PREFIX,
    build_body,
    canonical_json_bytes,
    merge_text,
    reconstruct_document,
    sha256_text,
    validate_catalog_axis,
)


TEMPLATE = (
    "{display_title} [SEP] {overview_fallback} [SEP] genres: {genre_names} "
    "[SEP] directors: {director_names} [SEP] cast: {top5_cast_names} "
    "[SEP] keywords: {keyword_names}"
)


def cache_record(tmdb_id: int, language: str, body: dict) -> dict:
    return {
        "request": {
            "kind": "movie",
            "identity": str(tmdb_id),
            "endpoint": f"/3/movie/{tmdb_id}",
            "params": {"append_to_response": "credits,keywords", "language": language},
        },
        "status": 200,
        "body": body,
        "body_sha256": hashlib.sha256(canonical_json_bytes(body)).hexdigest(),
    }


class Hybrid345DocumentTests(unittest.TestCase):
    def test_six_fields_match_rec019b_ordering(self) -> None:
        primary = {
            "id": 10,
            "title": " 제목 ",
            "original_title": "Original",
            "overview": " 줄거리 ",
            "genres": [{"name": "드라마"}, {"name": ""}, {"name": "코미디"}],
            "credits": {
                "crew": [
                    {"job": "Writer", "name": "Writer"},
                    {"job": "Director", "name": "감독"},
                ],
                "cast": [
                    {"id": 4, "order": 2, "name": "C"},
                    {"id": 2, "order": 0, "name": "A"},
                    {"id": 3, "order": 1, "name": "B"},
                    {"id": 8, "order": 3, "name": "D"},
                    {"id": 7, "order": 4, "name": "E"},
                    {"id": 6, "order": 5, "name": "F"},
                ],
            },
            "keywords": {"results": [{"name": "우정"}, {"name": "모험"}]},
        }
        text = merge_text(primary, None)
        self.assertEqual(text["top5_cast_names"], ["A", "B", "C", "D", "E"])
        body = build_body(TEMPLATE, text)
        self.assertEqual(
            body,
            "제목 [SEP] 줄거리 [SEP] genres: 드라마, 코미디 [SEP] directors: 감독 "
            "[SEP] cast: A, B, C, D, E [SEP] keywords: 우정, 모험",
        )
        self.assertFalse(body.startswith(E5_PREFIX))

    def test_exact_e5_hash_selects_english_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            cache.mkdir()
            primary_body = {
                "id": 10,
                "imdb_id": "tt10",
                "title": "한국 제목",
                "original_title": "Original",
                "overview": "",
                "genres": [{"name": "Drama"}],
                "credits": {"crew": [], "cast": []},
                "keywords": {"keywords": []},
            }
            english_body = {
                "id": 10,
                "imdb_id": "tt10",
                "title": "English title",
                "original_title": "Original",
                "overview": "English overview",
            }
            primary = cache_record(10, "ko-KR", primary_body)
            english = cache_record(10, "en-US", english_body)
            primary_path = cache / "movie-10-ko_KR.json"
            english_path = cache / "movie-10-en_US.json"
            primary_path.write_text(json.dumps(primary, ensure_ascii=False), encoding="utf-8")
            english_path.write_text(json.dumps(english, ensure_ascii=False), encoding="utf-8")
            expected_body = build_body(TEMPLATE, merge_text(primary_body, english_body))
            row, sources = reconstruct_document(
                {
                    "movie_id": 1,
                    "tmdb_id": 10,
                    "cache_path": "cache/movie-10-ko_KR.json",
                    "response_sha256": primary["body_sha256"],
                },
                sha256_text(E5_PREFIX + expected_body),
                TEMPLATE,
                root=root,
            )
            self.assertEqual(row["body"], expected_body)
            self.assertEqual(row["english_cache_path"], "cache/movie-10-en_US.json")
            self.assertEqual([source["language"] for source in sources], ["ko-KR", "en-US"])
            with self.assertRaisesRegex(RuntimeError, "no exact"):
                reconstruct_document(
                    {
                        "movie_id": 1,
                        "tmdb_id": 10,
                        "cache_path": "cache/movie-10-ko_KR.json",
                        "response_sha256": primary["body_sha256"],
                    },
                    "0" * 64,
                    TEMPLATE,
                    root=root,
                )

    def test_catalog_axis_is_sorted_and_exact(self) -> None:
        catalog = pd.DataFrame({"movie_id": [3, 1, 2]})
        metadata = pd.DataFrame({"movie_id": [2, 3, 1], "tmdb_id": [20, 30, 10]})
        e5 = pd.DataFrame(
            {"movie_id": [1, 2, 3], "input_text_sha256": ["a" * 64, "b" * 64, "c" * 64]}
        )
        catalog, metadata, e5 = validate_catalog_axis(catalog, metadata, e5, 3)
        self.assertEqual(catalog.movie_id.tolist(), [1, 2, 3])
        self.assertEqual(metadata.movie_id.tolist(), [1, 2, 3])
        self.assertEqual(e5.movie_id.tolist(), [1, 2, 3])
        with self.assertRaisesRegex(RuntimeError, "duplicate"):
            validate_catalog_axis(pd.DataFrame({"movie_id": [1, 1]}), metadata, e5, 2)


if __name__ == "__main__":
    unittest.main()

