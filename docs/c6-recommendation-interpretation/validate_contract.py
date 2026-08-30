#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = ROOT.parents[1]
PATH = "/api/v1/me/recommendation-interpretation-experiment"
OPERATION = "getMyRecommendationInterpretationExperiment"
EXPECTED_AC = {f"AC-C6-{number:03d}" for number in range(1, 17)}
REQUIRED_LIMITATIONS = {
    "LOCAL_EXPERIMENT_ONLY",
    "NOT_SELF_REPORTED_SATISFACTION",
    "NOT_PRODUCT_DISPLAY_APPROVED",
    "K_BUCKETED_MOST_RECENT",
}


def main() -> int:
    errors: list[str] = []
    fragment_text = (ROOT / "api/openapi.fragment.yaml").read_text(encoding="utf-8")
    fragment = yaml.safe_load(fragment_text)
    contract = (ROOT / "local-contract.md").read_text(encoding="utf-8")
    acceptance = (ROOT / "testing/acceptance-tests.md").read_text(encoding="utf-8")
    main_openapi = yaml.safe_load((REPOSITORY_ROOT / "docs/api/openapi.yaml").read_text(encoding="utf-8"))

    if fragment.get("x-contract-status") != "APPROVED_LOCAL_EXPERIMENT":
        errors.append("fragment must remain APPROVED_LOCAL_EXPERIMENT")
    if fragment.get("x-production-activation") is not False or fragment.get("x-product-display-approved") is not False:
        errors.append("production and product display must remain disabled")
    operation = fragment.get("paths", {}).get(PATH, {}).get("get", {})
    if operation.get("operationId") != OPERATION:
        errors.append("external operation drift")
    if operation.get("x-implementation-status") != "READY_LOCAL_EXPERIMENT":
        errors.append("operation must be READY_LOCAL_EXPERIMENT")
    expected_ref = "../c6-recommendation-interpretation/api/openapi.fragment.yaml#/paths/~1api~1v1~1me~1recommendation-interpretation-experiment"
    if main_openapi.get("paths", {}).get(PATH) != {"$ref": expected_ref}:
        errors.append("main OpenAPI C6 path merge drift")

    limitation_enum = set(
        fragment.get("components", {}).get("schemas", {})
        .get("C6InterpretationExperiment", {}).get("properties", {})
        .get("limitations", {}).get("items", {}).get("enum", [])
    )
    if limitation_enum != REQUIRED_LIMITATIONS:
        errors.append(f"limitation enum drift: {sorted(limitation_enum)}")
    if "displayEligible: { type: boolean, const: false }" not in fragment_text:
        errors.append("prediction displayEligible must be const false")
    if "C6_MOST_RECENT_VALIDATED_K_FLOOR_V1" not in fragment_text or "C6_MOST_RECENT_VALIDATED_K_FLOOR_V1" not in contract:
        errors.append("K selection policy is not fixed")
    if "(1 + count(rating < q) + 0.5 * count(rating = q)) / (n + 2)" not in contract:
        errors.append("relative utility formula drift")
    if "C6_DISCRETE_QUANTIZED_MIDRANK_ECDF_V2" not in fragment_text or "REC-EV-015" not in contract:
        errors.append("relative utility v2 evidence binding drift")
    if "c6-recommendation-interpretation-v2" not in fragment_text:
        errors.append("experiment version must remain v2")
    if "직접 측정한 만족도" not in contract:
        errors.append("non-satisfaction interpretation warning missing")

    actual_ac = set(re.findall(r"AC-C6-\d{3}", acceptance))
    if actual_ac != EXPECTED_AC:
        errors.append(f"acceptance set drift: missing={sorted(EXPECTED_AC-actual_ac)} extra={sorted(actual_ac-EXPECTED_AC)}")

    if errors:
        for error in errors:
            print(f"C6 contract error: {error}")
        return 1
    print("C6 local experiment contract validation passed: 1 operation, 16 AC, expected rating + discrete midrank ECDF + taste evidence; product exposure remains blocked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
