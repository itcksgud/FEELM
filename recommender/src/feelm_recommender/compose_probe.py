from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from .api import LOCAL_FAKE_FORBIDDEN_TOKEN, LOCAL_FAKE_SERVICE_TOKEN
from .candidate_export import LocalCandidateStore
from .local_stack_fixture import LOCAL_CATALOG_VERSION, LOCAL_MOVIES


class ProbeFailure(Exception):
    def __init__(self, safe_code: str) -> None:
        super().__init__(safe_code)
        self.safe_code = safe_code


def _request(url: str, *, method: str = "GET", body: dict[str, object] | None = None,
             request_id: str | None = None) -> dict[str, object]:
    payload = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
    headers = {"Authorization": f"Bearer {LOCAL_FAKE_SERVICE_TOKEN}"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if request_id is not None:
        headers["X-Request-Id"] = request_id
    request = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        raise ProbeFailure("RECOMMENDER_HTTP_PROBE_FAILED") from None


def _expect_http_status(
    url: str,
    expected_status: int,
    *,
    method: str = "GET",
    body: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> None:
    payload = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
    request_headers = dict(headers or {})
    if payload is not None:
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=payload, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5):
            raise ProbeFailure("FAIL_CLOSED_STATUS_NOT_OBSERVED")
    except urllib.error.HTTPError as error:
        error.close()
        if error.code != expected_status:
            raise ProbeFailure("FAIL_CLOSED_STATUS_NOT_OBSERVED") from None
    except (urllib.error.URLError, TimeoutError):
        raise ProbeFailure("FAIL_CLOSED_PROBE_UNAVAILABLE") from None


def run_probe(candidate_store: str | Path, base_url: str) -> dict[str, object]:
    try:
        active = LocalCandidateStore(candidate_store).load_active()
    except Exception:
        raise ProbeFailure("CANDIDATE_STORE_NOT_READY") from None
    expected = sorted(movie_id for movie_id, _, state in LOCAL_MOVIES if state == "UI_READY")
    if active.get("catalogVersion") != LOCAL_CATALOG_VERSION:
        raise ProbeFailure("CANDIDATE_CATALOG_VERSION_MISMATCH")
    if active.get("movieIds") != expected:
        raise ProbeFailure("CANDIDATE_UI_READY_SET_MISMATCH")

    ready = _request(base_url.rstrip("/") + "/internal/health/ready")
    if ready.get("status") != "READY" or any(
        check.get("status") != "PASS" for check in ready.get("checks", [])
    ):
        raise ProbeFailure("RECOMMENDER_NOT_READY")

    request_id = str(uuid.uuid4())
    payload = {
        "requestId": request_id,
        "candidateSet": {
            "candidateSetVersion": active["candidateSetVersion"],
            "movieIds": active["movieIds"],
        },
        "preferenceInput": {
            "inputVersion": "c2-compose-k0-fixture-v1",
            "ratings": [],
        },
        "starPolicy": "DISABLED",
    }
    _expect_http_status(base_url.rstrip("/") + "/internal/health/ready", 401)
    _expect_http_status(
        base_url.rstrip("/") + "/internal/health/ready",
        403,
        headers={"Authorization": f"Bearer {LOCAL_FAKE_FORBIDDEN_TOKEN}"},
    )
    _expect_http_status(
        base_url.rstrip("/") + "/internal/v1/recommendations/rank",
        422,
        method="POST",
        body=payload,
        headers={
            "Authorization": f"Bearer {LOCAL_FAKE_SERVICE_TOKEN}",
            "X-Request-Id": str(uuid.uuid4()),
        },
    )
    rank = _request(
        base_url.rstrip("/") + "/internal/v1/recommendations/rank",
        method="POST",
        body=payload,
        request_id=request_id,
    )
    snapshot = rank.get("snapshot", {})
    items = rank.get("items", [])
    if (
        rank.get("outcome") != "COMPLETE"
        or snapshot.get("catalogVersion") != LOCAL_CATALOG_VERSION
        or snapshot.get("candidateSetVersion") != active["candidateSetVersion"]
        or snapshot.get("rankingPolicy") != "BAYESIAN_POPULARITY_ONLY"
        or snapshot.get("rankingAlpha") != 0.0
        or len(items) != len(expected)
        or sorted(item.get("movieId") for item in items) != expected
        or any(
            item.get("expectedStar") != {
                "status": "NOT_COMPUTED",
                "value": None,
                "displayEligible": False,
                "confidence": "NOT_EVALUATED",
                "confidencePolicyVersion": None,
            }
            for item in items
        )
    ):
        raise ProbeFailure("RANK_RESPONSE_INVARIANT_FAILED")
    return {
        "status": "PASS",
        "safeCode": "C2_RECOMMENDER_COMPOSE_READY",
        "catalogVersion": LOCAL_CATALOG_VERSION,
        "candidateCount": len(expected),
        "rankedItemCount": len(items),
        "artifactCheckCount": len(ready.get("checks", [])),
        "failClosedCheckCount": 3,
        "rankingPolicy": "BAYESIAN_POPULARITY_ONLY",
        "rankingAlpha": 0,
        "expectedStarStatus": "NOT_COMPUTED",
    }


def main() -> int:
    try:
        result = run_probe("/artifacts/candidates/store", "http://127.0.0.1:8000")
    except ProbeFailure as failure:
        print(json.dumps({"status": "FAIL", "safeCode": failure.safe_code}, sort_keys=True))
        return 2
    except Exception:
        print(json.dumps({"status": "FAIL", "safeCode": "UNEXPECTED_PROBE_FAILURE"}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
