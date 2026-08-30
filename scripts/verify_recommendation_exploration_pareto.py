from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify REC-EV-004 manifest and bounded artifacts")
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1 and manifest["evidence_id"] == "REC-EV-004"
    assert manifest["protocol"]["version"] == "rec-ev-004-sampled-exploration-pareto-v1"
    assert manifest["protocol"]["candidate_scope"].startswith("SAMPLED_")
    assert manifest["validation"] == {
        "full_catalog_claim": False,
        "raw_user_ids_tracked": False,
        "same_candidate_definition_for_all_policies": True,
        "split_checksums_verified": True,
        "status": "PASS",
        "validation_selection_precedes_test": True,
    }
    assert manifest["conclusion"]["personal_ranking_champion"] is None
    assert manifest["conclusion"]["product_exploration_weight"] is None
    assert manifest["conclusion"]["product_relevance_loss_budget"] is None
    for record in manifest["artifacts"].values():
        if record.get("tracked") is False:
            continue
        path = Path(record["path"])
        assert path.is_file() and path.stat().st_size == record["bytes"] and sha256(path) == record["sha256"]
    results = manifest["metrics"]
    assert "POPULARITY" in results["validation_metrics"] and "POPULARITY" in results["test_metrics"]
    assert set(results["budget_selections"]) == {"0%", "1%", "3%", "5%"}
    selected = {entry["policy"] for entry in results["budget_selections"].values() if entry["policy"]}
    assert selected.issubset(results["validation_metrics"])
    assert set(results["test_metrics"]) == {"POPULARITY", *selected}
    provenance = json.loads(Path(manifest["artifacts"]["reason_provenance"]["path"]).read_text(encoding="utf-8"))
    assert provenance["reasonUiApproved"] is False and provenance["rankingChampion"] is None
    assert {entry["feature"] for entry in provenance["features"]} == {
        "BAYESIAN_POPULARITY", "GENRE_AFFINITY", "NOVELTY_PRIOR", "MARGINAL_GENRE_DIVERSITY"
    }
    serialized = json.dumps(manifest, sort_keys=True)
    assert '"user_id"' not in serialized and '"movie_id"' not in serialized
    print(json.dumps({"status": "PASS", "evidence_id": "REC-EV-004", "full_catalog_claim": False}))


if __name__ == "__main__":
    main()
