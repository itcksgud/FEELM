from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from feelm_catalog_pipeline.fake_tmdb import FakeTmdbGateway
from feelm_catalog_pipeline.identity import IdentityEntry
from feelm_catalog_pipeline.movielens import MovieLensMovie
from feelm_catalog_pipeline.normalization import normalize_movie

from support import credits, movie_details, providers, translations


NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)


class NormalizationTest(unittest.TestCase):
    def test_stale_tmdb_id_recovers_by_imdb_and_separates_offer_types(self) -> None:
        movie = MovieLensMovie(1, "Recovered Film", 2020, ("Drama",), "tt0000001", 999)
        gateway = FakeTmdbGateway(
            details={2: movie_details(2, "tt0000001", "Recovered Film", 2020)},
            credits={2: credits()},
            translations={2: translations("Recovered Film")},
            providers={2: providers()},
            finds={
                "tt0000001": {
                    "movie_results": [
                        {
                            "id": 2,
                            "title": "Recovered Film",
                            "original_title": "Recovered Film",
                            "release_date": "2020-01-01",
                        }
                    ],
                    "tv_results": [],
                }
            },
        )
        identity = IdentityEntry(
            "11111111-1111-4111-8111-111111111111", NOW.isoformat()
        )
        outcome = normalize_movie(
            movie,
            identity,
            gateway,
            NOW,
            snapshot_uuid_factory=lambda: uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        )

        self.assertEqual("IDENTITY_VERIFIED", outcome.identity_status)
        self.assertTrue(outcome.recovered)
        self.assertEqual(2, outcome.resolved_tmdb_id)
        identity_payload = outcome.records[0]["payload"]
        tmdb_ids = {
            (item["externalId"], item["verificationStatus"])
            for item in identity_payload["externalIds"]
            if item["source"] == "TMDB"
        }
        self.assertEqual({("999", "UNVERIFIED"), ("2", "RECOVERED")}, tmdb_ids)
        offer_types = {
            item["payload"]["monetizationType"]
            for item in outcome.records
            if item["recordType"] == "ottOffer"
        }
        self.assertEqual({"FLATRATE", "RENT", "BUY", "FREE", "ADS"}, offer_types)
        localizations = {
            item["payload"]["locale"]: item["payload"]
            for item in outcome.records
            if item["recordType"] == "localization"
        }
        self.assertEqual("Recovered Film 한국어", localizations["ko-KR"]["title"])
        self.assertEqual("Recovered Film", localizations["en-US"]["title"])

    def test_imdb_find_tv_only_is_excluded_from_projection(self) -> None:
        movie = MovieLensMovie(2, "TV Disguised", 2000, (), "tt0000002", None)
        gateway = FakeTmdbGateway(
            finds={
                "tt0000002": {
                    "movie_results": [],
                    "tv_results": [{"id": 8, "name": "TV Disguised"}],
                }
            }
        )
        outcome = normalize_movie(
            movie,
            IdentityEntry("22222222-2222-4222-8222-222222222222", NOW.isoformat()),
            gateway,
            NOW,
        )
        self.assertEqual("TYPE_MISMATCH_TV", outcome.identity_status)
        self.assertEqual(["movieIdentity"], [item["recordType"] for item in outcome.records])

    def test_provider_failure_is_failed_snapshot_not_empty_success(self) -> None:
        from feelm_catalog_pipeline.errors import TmdbTransientError

        movie = MovieLensMovie(3, "Provider Failure", 2021, ("Drama",), "tt0000003", 3)
        gateway = FakeTmdbGateway(
            details={3: movie_details(3, "tt0000003", "Provider Failure", 2021)},
            credits={3: credits()},
            translations={3: translations("Provider Failure")},
            errors={
                ("providers", 3): TmdbTransientError("TMDB_HTTP_429", "retry exhausted")
            },
        )
        outcome = normalize_movie(
            movie,
            IdentityEntry("33333333-3333-4333-8333-333333333333", NOW.isoformat()),
            gateway,
            NOW,
        )
        snapshot = next(
            item["payload"]
            for item in outcome.records
            if item["recordType"] == "availabilitySnapshot"
        )
        self.assertEqual("FAILED", snapshot["fetchStatus"])
        self.assertEqual("TMDB_HTTP_429", snapshot["failureCode"])
        self.assertFalse(any(item["recordType"] == "ottOffer" for item in outcome.records))

    def test_authentication_failure_aborts_instead_of_becoming_review_status(self) -> None:
        from feelm_catalog_pipeline.errors import TmdbAuthenticationError

        movie = MovieLensMovie(4, "Auth Failure", 2022, ("Drama",), "tt0000004", 4)
        gateway = FakeTmdbGateway(errors={("details", 4): TmdbAuthenticationError()})
        with self.assertRaises(TmdbAuthenticationError):
            normalize_movie(
                movie,
                IdentityEntry("44444444-4444-4444-8444-444444444444", NOW.isoformat()),
                gateway,
                NOW,
            )


if __name__ == "__main__":
    unittest.main()
