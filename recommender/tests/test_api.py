from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from feelm_recommender.api import ArtifactRegistry, create_app
from feelm_recommender.artifact_set import export_fixture_artifact_set


TOKEN = {"Authorization": "Bearer test-c2-service-token"}
MOVIE_A = "00000000-0000-0000-0000-000000000001"
MOVIE_B = "00000000-0000-0000-0000-000000000002"
MOVIE_MISSING = "00000000-0000-0000-0000-000000000099"


def request_payload(movie_ids: list[str], *, star_policy: str = "DISABLED") -> dict:
    request_id = "10000000-0000-0000-0000-000000000001"
    return {
        "requestId": request_id,
        "candidateSet": {"candidateSetVersion": "candidate-fixture-v1", "movieIds": movie_ids},
        "preferenceInput": {"inputVersion": "ratings-none-v1", "ratings": []},
        "starPolicy": star_policy,
    }


def headers(request_id: str = "10000000-0000-0000-0000-000000000001") -> dict[str, str]:
    return {**TOKEN, "X-Request-Id": request_id}


class C2InternalApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.manifest = export_fixture_artifact_set(self.directory)
        self.client_context = TestClient(create_app(artifact_manifest=self.manifest, auth_mode="fake"))
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temporary.cleanup()

    def test_bearer_auth_and_separate_health_semantics(self) -> None:
        self.assertEqual(self.client.get("/internal/health/live").status_code, 401)
        invalid = self.client.get(
            "/internal/health/live",
            headers={"Authorization": "Bearer invalid-local-token"},
        )
        self.assertEqual(invalid.status_code, 401)
        self.assertNotIn("invalid-local-token", invalid.text)
        forbidden = self.client.get(
            "/internal/health/live",
            headers={"Authorization": "Bearer test-c2-forbidden-token"},
        )
        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(forbidden.json()["code"], "INTERNAL_AUTH_FORBIDDEN")
        self.assertNotIn("test-c2-forbidden-token", forbidden.text)
        live = self.client.get("/internal/health/live", headers=TOKEN)
        self.assertEqual(live.status_code, 200)
        self.assertEqual(live.json(), {"status": "LIVE"})
        ready = self.client.get("/internal/health/ready", headers=TOKEN)
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.json()["status"], "READY")
        self.assertEqual(len(ready.json()["checks"]), 5)
        self.assertTrue(all(check["status"] == "PASS" for check in ready.json()["checks"]))

    def test_rank_is_service_uuid_only_and_input_order_deterministic(self) -> None:
        first = self.client.post(
            "/internal/v1/recommendations/rank",
            headers=headers(),
            json=request_payload([MOVIE_B, MOVIE_A]),
        )
        second = self.client.post(
            "/internal/v1/recommendations/rank",
            headers=headers(),
            json=request_payload([MOVIE_A, MOVIE_B]),
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), second.json())
        body = first.json()
        self.assertEqual(body["outcome"], "COMPLETE")
        self.assertEqual([item["movieId"] for item in body["items"]], [MOVIE_A, MOVIE_B])
        self.assertNotIn("movielens", str(body).lower())
        self.assertEqual(body["snapshot"]["candidateSetVersion"], "candidate-fixture-v1")
        self.assertEqual(body["snapshot"]["rankingPolicy"], "BAYESIAN_POPULARITY_ONLY")
        self.assertEqual(body["snapshot"]["rankingAlpha"], 0.0)

    def test_partial_and_empty_candidate_issues_are_safe(self) -> None:
        partial = self.client.post(
            "/internal/v1/recommendations/rank",
            headers=headers(),
            json=request_payload([MOVIE_A, MOVIE_MISSING]),
        )
        self.assertEqual(partial.status_code, 200)
        self.assertEqual(partial.json()["outcome"], "PARTIAL")
        self.assertEqual(partial.json()["issues"][0]["code"], "SERVICE_ID_NOT_MAPPED")
        empty = self.client.post(
            "/internal/v1/recommendations/rank",
            headers=headers(),
            json=request_payload([MOVIE_MISSING]),
        )
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json()["outcome"], "EMPTY")
        self.assertEqual(empty.json()["items"], [])

    def test_star_candidate_fails_closed_without_affecting_ranking(self) -> None:
        response = self.client.post(
            "/internal/v1/recommendations/rank",
            headers=headers(),
            json=request_payload([MOVIE_A, MOVIE_B], star_policy="REC_EV_003B_CANDIDATE"),
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["outcome"], "PARTIAL")
        self.assertEqual(body["issues"], [{
            "scope": "STAR_HEAD",
            "code": "STAR_SCALE_INCOMPATIBLE",
            "movieId": None,
            "retriable": False,
        }])
        self.assertTrue(all(item["expectedStar"]["status"] == "NOT_COMPUTED" for item in body["items"]))
        self.assertTrue(all(item["expectedStar"]["value"] is None for item in body["items"]))

    def test_request_id_and_strict_rating_validation_are_fixed_safe_errors(self) -> None:
        mismatch = self.client.post(
            "/internal/v1/recommendations/rank",
            headers=headers("20000000-0000-0000-0000-000000000002"),
            json=request_payload([MOVIE_A]),
        )
        self.assertEqual(mismatch.status_code, 422)
        self.assertEqual(mismatch.json()["code"], "REQUEST_ID_MISMATCH")
        invalid = request_payload([MOVIE_A])
        invalid["preferenceInput"]["ratings"] = [
            {"movieId": MOVIE_A, "value": 3.5, "revision": 1}
        ]
        response = self.client.post(
            "/internal/v1/recommendations/rank", headers=headers(), json=invalid
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "INVALID_RECOMMENDATION_REQUEST")
        self.assertNotIn("3.5", response.text)
        duplicate = request_payload([MOVIE_A])
        duplicate["preferenceInput"]["ratings"] = [
            {"movieId": MOVIE_A, "value": 3, "revision": 1},
            {"movieId": MOVIE_A, "value": 4, "revision": 2},
        ]
        response = self.client.post(
            "/internal/v1/recommendations/rank", headers=headers(), json=duplicate
        )
        self.assertEqual(response.status_code, 422)

    def test_atomic_reload_retains_previous_ready_set(self) -> None:
        registry = ArtifactRegistry()
        self.assertTrue(registry.reload(self.manifest))
        version = registry.snapshot().artifact_set.artifact_set_version
        (self.directory / "bias.npz").write_bytes(
            (self.directory / "bias.npz").read_bytes() + b"tampered"
        )
        self.assertFalse(registry.reload(self.manifest))
        self.assertEqual(registry.snapshot().artifact_set.artifact_set_version, version)
        self.assertEqual(registry.snapshot().failure_code, "ARTIFACT_COMPATIBILITY_FAILURE")


class C2NotReadyApiTest(unittest.TestCase):
    def test_live_process_with_no_artifacts_is_not_ready_and_rank_is_503(self) -> None:
        with TestClient(create_app(auth_mode="fake")) as client:
            self.assertEqual(client.get("/internal/health/live", headers=TOKEN).status_code, 200)
            ready = client.get("/internal/health/ready", headers=TOKEN)
            self.assertEqual(ready.status_code, 503)
            self.assertEqual(ready.json()["status"], "NOT_READY")
            rank = client.post(
                "/internal/v1/recommendations/rank",
                headers=headers(),
                json=request_payload([MOVIE_A]),
            )
            self.assertEqual(rank.status_code, 503)
            self.assertEqual(rank.json()["code"], "ARTIFACT_SET_NOT_READY")

    def test_default_auth_mode_fails_closed(self) -> None:
        with TestClient(create_app(auth_mode="")) as client:
            response = client.get("/internal/health/live", headers=TOKEN)
            self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
