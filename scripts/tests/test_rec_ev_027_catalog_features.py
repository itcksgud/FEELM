from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
import zipfile

import pandas as pd
import numpy as np

from scripts.build_rec_ev_027_catalog_features import (
    LayeredTmdbClient,
    derive_rating_independent_catalog,
    domain_table,
    process_movie,
    reusable_embedding,
    validate_canonical_coverage,
)


class RecEv027CatalogFeatureTests(unittest.TestCase):
    def test_catalog_reader_never_opens_ratings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "fixture.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("ml/movies.csv", "movieId,title,genres\n1,A,Drama\n2,B,Comedy\n")
                bundle.writestr("ml/links.csv", "movieId,imdbId,tmdbId\n1,1,11\n2,2,22\n")
                bundle.writestr("ml/ratings.csv", "userId,movieId,rating,timestamp\n1,1,5,0\n")
            catalog, opened = derive_rating_independent_catalog(archive)
            self.assertEqual([1, 2], catalog["movie_id"].tolist())
            self.assertEqual(2, int(catalog["link_row_present"].sum()))
            self.assertEqual(["ml/movies.csv", "ml/links.csv"], opened)
            self.assertFalse(any(item.endswith("ratings.csv") for item in opened))

    def test_fallback_cache_requires_exact_request_and_body_hash(self) -> None:
        body = {"id": 11, "title": "A"}
        cached = {
            "request": {
                "kind": "movie", "identity": "11", "endpoint": "/3/movie/11",
                "params": {"append_to_response": "credits,keywords", "language": "ko-KR"},
            },
            "status": 200,
            "body": body,
            "body_sha256": hashlib.sha256(
                (json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
            ).hexdigest(),
        }
        self.assertTrue(LayeredTmdbClient._valid_cached(
            cached, "movie", "11", "/3/movie/11",
            {"append_to_response": "credits,keywords", "language": "ko-KR"},
        ))
        cached["request"]["identity"] = "12"
        self.assertFalse(LayeredTmdbClient._valid_cached(
            cached, "movie", "11", "/3/movie/11",
            {"append_to_response": "credits,keywords", "language": "ko-KR"},
        ))

    def test_canonical_coverage_rejects_missing_or_duplicate_ids(self) -> None:
        catalog = pd.DataFrame({"movie_id": [1, 2]})
        validate_canonical_coverage([{"movie_id": 1}, {"movie_id": 2}], catalog)
        with self.assertRaisesRegex(RuntimeError, "every catalog movie"):
            validate_canonical_coverage([{"movie_id": 1}], catalog)
        with self.assertRaisesRegex(RuntimeError, "every catalog movie"):
            validate_canonical_coverage([{"movie_id": 1}, {"movie_id": 1}], catalog)

    def test_embedding_reuse_requires_exact_rebuilt_input_hash(self) -> None:
        config = {"model_id": "m", "model_revision": "r", "dimension": 3}
        cached = pd.Series({
            "model_id": "m", "model_revision": "r", "input_text_sha256": "exact",
            "embedding": np.asarray([1.0, 0.0, 0.0], dtype=np.float32),
        })
        self.assertIsNotNone(reusable_embedding(cached, config, "exact"))
        self.assertIsNone(reusable_embedding(cached, config, "changed"))

    def test_english_fallback_nonterminal_failure_is_fatal(self) -> None:
        class FakeClient:
            calls = 0

            def details(self, tmdb_id: int, language: str) -> dict[str, object]:
                self.calls += 1
                if language == "ko-KR":
                    return {
                        "status": 200, "fetched_at": "2026-01-01T00:00:00+00:00", "body_sha256": "x",
                        "body": {"id": tmdb_id, "imdb_id": "tt0000001", "title": "A", "overview": ""},
                    }
                return {"status": 429, "body": {}}

        with self.assertRaisesRegex(RuntimeError, "English fallback"):
            process_movie(
                {"movie_id": 1, "imdb_id": "tt0000001", "tmdb_id": 11}, FakeClient()  # type: ignore[arg-type]
            )

    def test_korean_proxy_equals_production_country_membership(self) -> None:
        records = [
            {"movie_id": 1, "tmdb_id": 11, "status": "ML_TMDB_VERIFIED", "production_country_codes": ["KR", "US"]},
            {"movie_id": 2, "tmdb_id": 22, "status": "ML_TMDB_VERIFIED", "production_country_codes": ["US"]},
        ]
        frame = domain_table(records).to_pandas()
        self.assertEqual([True, False], frame["tmdb_korean_origin_proxy"].tolist())


if __name__ == "__main__":
    unittest.main()
