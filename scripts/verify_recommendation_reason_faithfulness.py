from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from recommendation_reason_faithfulness import classify_reason


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify REC-EV-006 reason evidence")
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1 and manifest["evidence_id"] == "REC-EV-006"
    assert manifest["validation"] == {
        "actual_rec_ev_004_contributions_used": True,
        "failure_fixtures_pass": True,
        "raw_ids_tracked": False,
        "reason_ui_approved": False,
        "single_feature_ablations_present": True,
        "status": "PASS",
    }
    assert manifest["conclusion"]["display_count"] is None
    assert manifest["conclusion"]["ui_copy"] is None
    assert manifest["conclusion"]["ranking_champion"] is None
    for record in manifest["artifacts"].values():
        if record.get("tracked") is False:
            continue
        path = Path(record["path"])
        assert path.is_file() and path.stat().st_size == record["bytes"] and sha256(path) == record["sha256"]
    metrics = manifest["metrics"]
    assert set(metrics["ablation"]) == {
        "BASE_ONLY", "FULL", "WITHOUT_DIVERSITY", "WITHOUT_NOVELTY", "WITHOUT_POPULARITY"
    }
    total = metrics["recommendations"]
    for values in metrics["coverage"].values():
        assert values["evaluated_recommendations"] == total
        assert 0 <= values["faithful_rank_effect"] <= values["positive_contribution"] <= total
    contract = json.loads(Path(manifest["artifacts"]["typed_contract"]["path"]).read_text(encoding="utf-8"))
    assert contract["uiCopyApproved"] is False and contract["displayCountApproved"] is False
    fixtures = json.loads(Path(manifest["artifacts"]["failure_fixtures"]["path"]).read_text(encoding="utf-8"))
    assert fixtures["containsRawIds"] is False
    for fixture in fixtures["fixtures"]:
        values = fixture["input"]
        actual = classify_reason(
            feature_active=values["featureActive"], contribution=values["contribution"],
            rank_effect=values["rankEffect"], provenance_valid=values["provenanceValid"],
            policy_version_match=values.get("policyVersionMatch", True),
            sensitive_evidence=values["sensitiveEvidence"],
        )
        assert actual == (fixture["expected"]["state"], fixture["expected"]["code"])
    serialized = json.dumps(manifest, sort_keys=True)
    assert '"user_id"' not in serialized and '"movie_id"' not in serialized
    print(json.dumps({"status": "PASS", "evidence_id": "REC-EV-006", "reason_ui_approved": False}))


if __name__ == "__main__":
    main()
