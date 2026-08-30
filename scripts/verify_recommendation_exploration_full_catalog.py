from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from recommendation_exploration_full_catalog import POLICIES, canonical_bytes, sha256
from recommendation_evidence_paths import repository_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify tracked-safe REC-EV-004B artifacts")
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1 and manifest["evidence_id"] == "REC-EV-004B"
    protocol = manifest["protocol"]
    assert protocol["version"] == "rec-ev-004b-full-catalog-v1"
    assert set(protocol["policies"]) == set(POLICIES)
    assert protocol["candidate_generation"] == {
        "exclude": "each user's Train-seen movies before Top-500",
        "positive_injection": False,
        "score_scan": "every eligible Train-known movie",
        "tie_break": "score descending then movieId ascending",
        "top_candidates": 500,
        "top_k": 10,
        "unknown_genre_policy": "selection diversity contribution=0; metric pair diversity=0; coverage reported separately",
        "universe": "all 50,977 Train-known movies",
    }
    assert manifest["validation"] == {
        "full_catalog_score_scan": True,
        "positive_injection": False,
        "raw_ids_tracked": False,
        "same_warm_cohort_definition_as_rec_ev_004": True,
        "status": "PASS",
        "train_seen_excluded": True,
        "validation_lock_verified_before_test_read": True,
    }
    assert manifest["conclusion"]["product_weight"] is None
    assert manifest["conclusion"]["exploration_2_plus_1"] is None
    assert manifest["conclusion"]["ranking_champion"] is None
    for record in manifest["artifacts"].values():
        path = repository_path(record["path"])
        assert path.is_file() and path.stat().st_size == record["bytes"] and sha256(path) == record["sha256"]
    lock = json.loads(repository_path(manifest["artifacts"]["protocol_lock"]["path"]).read_text(encoding="utf-8"))
    assert lock["protocol"] == protocol
    assert lock["protocol_hash"] == hashlib.sha256(canonical_bytes(protocol)).hexdigest()
    validation = json.loads(repository_path(manifest["artifacts"]["validation_result"]["path"]).read_text(encoding="utf-8"))
    test = json.loads(repository_path(manifest["artifacts"]["test_result"]["path"]).read_text(encoding="utf-8"))
    assert validation["phase"] == "VALIDATION" and test["phase"] == "TEST"
    assert validation["selection"]["policies_locked_without_new_search"] == list(POLICIES)
    assert set(test["metrics"]) == set(POLICIES)
    assert test["coverage"]["train_known_movies"] == 50_977
    for value in test["metrics"].values():
        assert 0 <= value["candidate_recall_at_500"] <= 1
        assert 0 <= value["recall_at_10"] <= value["candidate_recall_at_500"]
        assert 0 <= value["list_genre_coverage"] <= 1
        assert 0 <= value["pair_genre_coverage"] <= 1
    serialized = json.dumps({"manifest": manifest, "validation": validation, "test": test}, sort_keys=True)
    assert '"user_id"' not in serialized and '"movie_id"' not in serialized
    print(json.dumps({"status": "PASS", "evidence_id": "REC-EV-004B", "full_catalog": True}))


if __name__ == "__main__":
    main()
