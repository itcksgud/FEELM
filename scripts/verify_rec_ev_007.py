from __future__ import annotations

import hashlib
import json
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
RESULT = PROJECT / "docs/recommendation/evidence/results/rec-ev-007-local-20260829.json"
MANIFEST = PROJECT / "docs/recommendation/evidence/manifests/rec-ev-007.json"
SOURCE_KEYS = {"evidence_id", "payload_sha256", "item_count", "factor_rank", "coverage"}


def main() -> int:
    result_bytes = RESULT.read_bytes()
    result = json.loads(result_bytes)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert manifest["result_file"] == RESULT.name
    assert manifest["result_sha256"] == hashlib.sha256(result_bytes).hexdigest()
    assert result["benchmark_version"] == manifest["benchmark_version"] == "rec-ev-007-v1"
    assert manifest["protocol_sha256"] and len(manifest["protocol_sha256"]) == 64
    assert "generated_at" in manifest["protocol_hash_excludes"]
    assert "CHECKSUM_CHANGES_BY_DESIGN" in manifest["result_checksum_policy"]
    assert set(manifest["source_factor_evidence"]) == SOURCE_KEYS
    assert manifest["source_metadata_policy"] == "CHECKSUM_AND_SHAPE_ONLY_NO_RAW_FACTOR_PATH"
    assert result["conditions"]["candidate_counts"] == [10, 100, 1000]
    assert result["conditions"]["rating_k_values"] == [0, 1, 3, 5, 10, 20]
    assert result["conditions"]["concurrency_levels"] == [1, 4, 8]
    assert result["serving_http"]["ranking_alpha"] == 0.0
    assert result["serving_http"]["star_policy"] == "DISABLED"
    assert all(result["gate_results"].values())
    assert not any(result["privacy"].values())
    recommendation = result["technical_recommendation"]
    assert recommendation["spring_outbound_timeout_ms"] == 750
    assert recommendation["active_rating_snapshot_healthy_path_target_ms"] == 3000
    assert recommendation["production_validation_required"] is True
    assert recommendation["expected_star_activation"] == "PROHIBITED_BY_DN_C2_008"
    assert recommendation["stale_success_fallback"] == "DISABLED"
    print(json.dumps({"status": "PASS", "evidence_id": "REC-EV-007"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
