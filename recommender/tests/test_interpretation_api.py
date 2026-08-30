from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from feelm_recommender.api import create_app
from feelm_recommender.artifact_set import export_fixture_artifact_set


TOKEN = {"Authorization": "Bearer test-c2-service-token"}
REQUEST_ID = "10000000-0000-0000-0000-000000000001"


def movie(item_id: int) -> str:
    return str(uuid.UUID(int=item_id))


def headers() -> dict[str, str]:
    return {**TOKEN, "X-Request-Id": REQUEST_ID}


def payload(*, candidates: list[str] | None = None, values: list[int] | None = None) -> dict:
    values = [] if values is None else values
    return {
        "requestId": REQUEST_ID,
        "candidateSet": {
            "candidateSetVersion": "c6-candidate-fixture-v1",
            "movieIds": candidates or [movie(1), movie(2)],
        },
        "preferenceInput": {
            "inputVersion": "c6-most-recent-first-v1",
            "ratings": [
                {"movieId": movie(index + 3), "value": value, "revision": index + 1}
                for index, value in enumerate(values)
            ],
        },
    }


class C6InterpretationApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.manifest = export_fixture_artifact_set(
            self.directory, fixture_item_count=25
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_route_is_absent_by_default_and_explicit_false_overrides_environment(self) -> None:
        with TestClient(
            create_app(artifact_manifest=self.manifest, auth_mode="fake")
        ) as client:
            self.assertEqual(
                client.post(
                    "/internal/v1/experiments/recommendation-interpretation",
                    headers=headers(),
                    json=payload(),
                ).status_code,
                404,
            )
        with patch.dict(os.environ, {"C6_LOCAL_EXPERIMENT_ENABLED": "true"}):
            with TestClient(
                create_app(
                    artifact_manifest=self.manifest,
                    auth_mode="fake",
                    c6_local_experiment_enabled=False,
                )
            ) as client:
                self.assertEqual(
                    client.post(
                        "/internal/v1/experiments/recommendation-interpretation",
                        headers=headers(),
                        json=payload(),
                    ).status_code,
                    404,
                )

    def test_environment_flag_enables_route_but_fake_auth_is_still_required(self) -> None:
        with patch.dict(os.environ, {"C6_LOCAL_EXPERIMENT_ENABLED": "TrUe"}):
            with TestClient(
                create_app(artifact_manifest=self.manifest, auth_mode="fake")
            ) as client:
                response = client.post(
                    "/internal/v1/experiments/recommendation-interpretation",
                    headers={"X-Request-Id": REQUEST_ID},
                    json=payload(),
                )
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json()["code"], "INTERNAL_AUTH_REQUIRED")

    def test_k_bucket_profile_ecdf_and_exact_non_display_contract(self) -> None:
        with TestClient(
            create_app(
                artifact_manifest=self.manifest,
                auth_mode="fake",
                c6_local_experiment_enabled=True,
            )
        ) as client:
            response = client.post(
                "/internal/v1/experiments/recommendation-interpretation",
                headers=headers(),
                json=payload(candidates=[movie(2), movie(1)], values=[1, 5, 2, 4]),
            )
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertEqual(
                set(body),
                {
                    "requestId",
                    "experimentVersion",
                    "snapshot",
                    "ratingProfile",
                    "items",
                    "limitations",
                },
            )
            self.assertEqual(body["experimentVersion"], "c6-recommendation-interpretation-v2")
            self.assertEqual(
                set(body["snapshot"]),
                {
                    "artifactSetVersion",
                    "policyVersion",
                    "inputVersion",
                    "kSelectionPolicyVersion",
                    "utilityPolicyVersion",
                    "availableRatingCount",
                    "usedRatingCount",
                },
            )
            self.assertEqual(body["snapshot"]["availableRatingCount"], 4)
            self.assertEqual(body["snapshot"]["usedRatingCount"], 3)
            self.assertEqual(
                body["snapshot"]["kSelectionPolicyVersion"],
                "C6_MOST_RECENT_VALIDATED_K_FLOOR_V1",
            )
            self.assertEqual(
                body["snapshot"]["utilityPolicyVersion"],
                "C6_DISCRETE_QUANTIZED_MIDRANK_ECDF_V2",
            )
            self.assertEqual(
                body["ratingProfile"],
                {
                    "activeRatingCount": 4,
                    "mean": 3.0,
                    "median": 3.0,
                    "confidence": "LOW",
                },
            )
            self.assertEqual([item["movieId"] for item in body["items"]], [movie(2), movie(1)])
            for item in body["items"]:
                self.assertEqual(
                    set(item),
                    {
                        "movieId",
                        "predictedRating",
                        "expectedRelativeUtility",
                        "directFoldIn",
                        "confidence",
                        "displayEligible",
                    },
                )
                quantized = int(item["predictedRating"] + 0.5)
                expected_less = sum(value < quantized for value in [1, 5, 2, 4])
                expected_equal = sum(value == quantized for value in [1, 5, 2, 4])
                self.assertAlmostEqual(
                    item["expectedRelativeUtility"],
                    (1 + expected_less + 0.5 * expected_equal) / 6,
                )
                self.assertEqual(item["confidence"], "LOW")
                self.assertFalse(item["displayEligible"])
            self.assertEqual(
                body["limitations"],
                [
                    "LOCAL_EXPERIMENT_ONLY",
                    "NOT_SELF_REPORTED_SATISFACTION",
                    "NOT_PRODUCT_DISPLAY_APPROVED",
                    "K_BUCKETED_MOST_RECENT",
                ],
            )

    def test_k0_is_null_utility_and_product_rank_remains_not_computed(self) -> None:
        with TestClient(
            create_app(
                artifact_manifest=self.manifest,
                auth_mode="fake",
                c6_local_experiment_enabled=True,
            )
        ) as client:
            experiment = client.post(
                "/internal/v1/experiments/recommendation-interpretation",
                headers=headers(),
                json=payload(values=[]),
            )
            self.assertEqual(experiment.status_code, 200)
            self.assertEqual(experiment.json()["snapshot"]["usedRatingCount"], 0)
            self.assertEqual(
                experiment.json()["ratingProfile"]["confidence"],
                "INSUFFICIENT_DATA",
            )
            self.assertTrue(
                all(
                    item["expectedRelativeUtility"] is None
                    for item in experiment.json()["items"]
                )
            )
            rank_payload = {**payload(values=[]), "starPolicy": "REC_EV_003B_CANDIDATE"}
            rank = client.post(
                "/internal/v1/recommendations/rank",
                headers=headers(),
                json=rank_payload,
            )
            self.assertEqual(rank.status_code, 200)
            self.assertTrue(
                all(
                    item["expectedStar"]["status"] == "NOT_COMPUTED"
                    for item in rank.json()["items"]
                )
            )

    def test_more_than_twenty_ratings_caps_fold_in_but_not_profile(self) -> None:
        with TestClient(
            create_app(
                artifact_manifest=self.manifest,
                auth_mode="fake",
                c6_local_experiment_enabled=True,
            )
        ) as client:
            response = client.post(
                "/internal/v1/experiments/recommendation-interpretation",
                headers=headers(),
                json=payload(values=[(index % 5) + 1 for index in range(21)]),
            )
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertEqual(body["snapshot"]["availableRatingCount"], 21)
            self.assertEqual(body["snapshot"]["usedRatingCount"], 20)
            self.assertEqual(body["ratingProfile"]["activeRatingCount"], 21)
            self.assertEqual(body["ratingProfile"]["confidence"], "HIGH")
            self.assertTrue(all(item["confidence"] == "HIGH" for item in body["items"]))

    def test_invalid_and_unmapped_inputs_fail_as_opaque_safe_errors(self) -> None:
        with TestClient(
            create_app(
                artifact_manifest=self.manifest,
                auth_mode="fake",
                c6_local_experiment_enabled=True,
            )
        ) as client:
            unmapped_candidate = client.post(
                "/internal/v1/experiments/recommendation-interpretation",
                headers=headers(),
                json=payload(candidates=[movie(99)]),
            )
            self.assertEqual(unmapped_candidate.status_code, 422)
            self.assertEqual(
                unmapped_candidate.json()["code"], "INVALID_RECOMMENDATION_REQUEST"
            )
            unmapped_rating = payload(values=[4])
            unmapped_rating["preferenceInput"]["ratings"][0]["movieId"] = movie(99)
            response = client.post(
                "/internal/v1/experiments/recommendation-interpretation",
                headers=headers(),
                json=unmapped_rating,
            )
            self.assertEqual(response.status_code, 422)
            self.assertNotIn(movie(99), response.text)
            invalid = payload(values=[4])
            invalid["preferenceInput"]["ratings"][0]["value"] = 3.5
            response = client.post(
                "/internal/v1/experiments/recommendation-interpretation",
                headers=headers(),
                json=invalid,
            )
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()["code"], "INVALID_RECOMMENDATION_REQUEST")


if __name__ == "__main__":
    unittest.main()
