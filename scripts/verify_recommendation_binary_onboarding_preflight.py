from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from recommendation_evidence_paths import artifact_matches, repository_path


DEFAULT_MANIFEST = Path(
    "docs/recommendation/evidence/manifests/rec-ev-019p.json"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    return parser.parse_args()


def verify(manifest_path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("evidence_id") != "REC-EV-019P":
        raise RuntimeError("unexpected evidence id")
    if manifest.get("status") != "COMPLETED_REPRODUCIBLE_FEASIBILITY_PASS":
        raise RuntimeError("preflight evidence is not completed")

    for name, record in manifest["artifacts"].items():
        path = repository_path(record["path"])
        if not artifact_matches(path, record):
            raise RuntimeError(f"artifact mismatch: {name}")

    result_path = repository_path(manifest["artifacts"]["result"]["path"])
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if result.get("status") != "PASS" or result.get("evidence_id") != "REC-EV-019P":
        raise RuntimeError("preflight result did not pass")
    if result.get("schema_version") != 2:
        raise RuntimeError("strict preflight schema v2 is required")
    if result.get("claim_boundary") != "FEASIBILITY_ONLY_NOT_RECOMMENDATION_PERFORMANCE":
        raise RuntimeError("claim boundary is missing")
    if result["split"].get("raw_user_ids_stored") is not False:
        raise RuntimeError("raw user IDs must not be stored")

    gate = result["gate"]
    if not gate.get("pass"):
        raise RuntimeError("minimum test-user gate failed")
    if int(gate["eligible_test_users"]) < int(gate["minimum_test_users"]):
        raise RuntimeError("eligible users are below the locked minimum")
    if int(gate["primary_binary_k"]) != 10:
        raise RuntimeError("primary binary K must remain 10")
    if gate.get("eligibility_definition") != (
        "K_INPUT_AND_10_FUTURE_AND_3_POSITIVES_AND_1_CANDIDATE_POSITIVE"
    ):
        raise RuntimeError("strict eligibility definition is missing")
    k10 = result["binary_proxy"]["eligibility"]["10"]
    if int(k10["eligible_users"]) != int(gate["eligible_test_users"]):
        raise RuntimeError("K10 eligibility and gate count differ")
    if int(k10["candidate_positive_users"]) != int(k10["eligible_users"]):
        raise RuntimeError("candidate-positive condition was not applied")
    if int(k10["minimum_positive_users"]) < int(k10["eligible_users"]):
        raise RuntimeError("minimum-positive stage is inconsistent")
    candidate = result["candidate_universe"]
    if candidate.get("basis") != (
        "BASE_TRAIN_MOVIE_AND_MOVIELENS_LINKS_TMDB_ID_PRESENT"
    ):
        raise RuntimeError("provisional candidate basis changed")
    if candidate.get("missing_model_artifact_policy") != (
        "KEEP_WITH_DECLARED_FALLBACK"
    ):
        raise RuntimeError("missing-feature fallback policy changed")
    recorded = manifest["result"]
    expected_recorded = {
        "locked_test_40pct_users": int(result["split"]["locked_test_users"]),
        "candidate_movies": int(candidate["movie_count"]),
        "k5_input_and_future": int(
            result["binary_proxy"]["eligibility"]["5"]["input_and_future_users"]
        ),
        "k5_minimum_positive": int(
            result["binary_proxy"]["eligibility"]["5"]["minimum_positive_users"]
        ),
        "k5_strict_eligible": int(
            result["binary_proxy"]["eligibility"]["5"]["eligible_users"]
        ),
        "k10_input_and_future": int(k10["input_and_future_users"]),
        "k10_minimum_positive": int(k10["minimum_positive_users"]),
        "k10_strict_eligible": int(k10["eligible_users"]),
        "k10_both_classes": int(k10["eligible_with_both_classes"]),
        "bucket_70_99_k10_strict_eligible": int(
            result["binary_proxy"][
                "current_basis_bucket_70_99_strict_eligible_users"
            ]["10"]
        ),
        "gate_pass": True,
    }
    if recorded != expected_recorded:
        raise RuntimeError("manifest result summary differs from strict result")

    split = manifest["protocol"]["user_split"]
    if split != {
        "base_train_buckets": [0, 39],
        "router_train_buckets": [40, 49],
        "validation_buckets": [50, 59],
        "test_buckets": [60, 99],
    }:
        raise RuntimeError("locked split changed")
    decision = manifest["decision"]
    if decision.get("rec_ev_019a_019b_implementation") != "GO":
        raise RuntimeError("019A/019B implementation GO is not recorded")
    if decision.get("rec_ev_019c_model_run") != "PENDING_FINAL_IDENTITY_GATE":
        raise RuntimeError("019C must remain behind the final identity gate")
    if decision.get("binary_personalization_champion") is not None:
        raise RuntimeError("preflight must not select a champion")
    if decision.get("expected_rating_approved") is not False:
        raise RuntimeError("preflight must not approve expected rating")

    return {
        "status": "PASS",
        "evidence_id": "REC-EV-019P",
        "implementation": "GO",
        "eligible_test_users": int(gate["eligible_test_users"]),
        "minimum_test_users": int(gate["minimum_test_users"]),
        "product_champion": None,
    }


if __name__ == "__main__":
    print(json.dumps(verify(parse_args().manifest), ensure_ascii=False, sort_keys=True))
