#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from recommendation_evidence_paths import artifact_matches, repository_path


EXPECTED_K = {"1", "3", "5", "10", "20"}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    errors: list[str] = []
    if manifest.get("evidence_id") != "REC-EV-015":
        errors.append("evidence id drift")
    if manifest.get("source", {}).get("test_used") is not False:
        errors.append("MovieLens Test must remain unused")
    protocol = manifest.get("protocol", {})
    if protocol.get("candidate_policy") != "C6_DISCRETE_QUANTIZED_MIDRANK_ECDF_V2":
        errors.append("candidate policy drift")
    result_record = manifest.get("result", {})
    result_path = repository_path(result_record.get("path", ""))
    if not artifact_matches(result_path, result_record):
        errors.append("result artifact missing or checksum drift")
        result = {}
    else:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    curve = result.get("k_curve", {})
    if set(curve) != EXPECTED_K:
        errors.append("K curve drift")
    for k, values in curve.items():
        if values.get("gate_pass") is not True or not all(values.get("gates", {}).values()):
            errors.append(f"K{k} did not pass every gate")
        if values.get("relative_micro_mae_improvement", 0) < protocol.get("minimum_mae_improvement", 1):
            errors.append(f"K{k} MAE improvement claim drift")
        if values.get("absolute_bias_reduction", 0) < protocol.get("minimum_absolute_bias_reduction", 1):
            errors.append(f"K{k} bias reduction claim drift")
    conclusion = manifest.get("conclusion", {})
    if conclusion.get("decision") != "ADOPT_C6_LOCAL_EXPERIMENT_V2_KEEP_PRODUCT_BLOCKED":
        errors.append("local-only decision drift")
    if conclusion.get("product_display_approved") is not False or conclusion.get("satisfaction_claim_supported") is not False:
        errors.append("product or satisfaction boundary was expanded")
    report = Path("docs/recommendation/evidence/REC-EV-015-relative-utility.md").read_text(encoding="utf-8")
    for phrase in ["MovieLens Test 사용: `NO`", "제품 만족도 주장: `NO`", "C6 local experiment"]:
        if phrase not in report:
            errors.append(f"report boundary missing: {phrase}")
    if errors:
        print("REC-EV-015 verification failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("REC-EV-015 verification passed: five K buckets improve discrete ECDF consistency; product and satisfaction claims remain blocked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
