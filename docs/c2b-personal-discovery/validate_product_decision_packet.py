#!/usr/bin/env python3
"""Fail closed if C2B exceeds its local-baseline authority."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
DECISIONS = {f"DN-C2B-{number:03d}" for number in range(1, 7)}
REQUIRED = {
    "APPROVED_LOCAL_BASELINE_WITH_BLOCKED_EXTENSIONS",
    "PARTIALLY_APPROVED_1_OF_6",
    "LOCAL_BASELINE_IMPLEMENTATION_AUTHORITY: YES",
    "PRODUCTION_ACTIVATION_AUTHORITY: NO",
    "KEEP_PUBLIC_ALPHA0_SHADOW_K10",
    "PUBLIC_RANKING_ALPHA=0",
    "MINIMUM_EVIDENCE_K=10",
    "SHADOW_ALPHA=0.2",
    "BASELINE_THREE_CUMULATIVE_LOAD_MORE_RATED_OR_EXPLICIT_DISMISS",
    "COMPOSITION=BASELINE_THREE",
    "APPEND_PAGE_SIZE=3",
    "CURSOR=OPAQUE_SIGNED",
    "ITEM_EXIT=RATING_COMPLETED_OR_EXPLICIT_NOT_INTERESTED",
    "MAX_ONE_FAITHFUL_REASON",
    "PUBLIC_REASON_MAX=1",
    "STAR_DISABLED_FAIL_CLOSED",
    "EXPECTED_STAR_STATUS=NOT_COMPUTED",
    "EXACT_STAGE_ONLY_C1_EVENT_AMENDMENT",
    "UTILITY_STATUS=NOT_COMPUTED",
    "C1_CLICK_EVENT_AMENDMENT=REQUIRED",
    "NO_STALE_VERSIONED_RETENTION_CANDIDATE",
    "APPEND_CURSOR_TTL=10m",
    "TERMINAL_UNEXPOSED_RETENTION=24h",
    "EXPOSURE_ACTION_ATTRIBUTION_RETENTION=90d",
    "IDEMPOTENCY_RETENTION=24h",
    "CACHE_CONTROL=PRIVATE_NO_STORE",
    "STALE_SUCCESS_FALLBACK=DISABLED",
    "SPRING_TIMEOUT_CANDIDATE=750ms",
    "RATING_SNAPSHOT_FRESHNESS_CANDIDATE=3000ms",
    "PRODUCTION_SLA=NOT_APPROVED",
    "`min(500,total)`",
    "scanned=selected+excluded",
    "먼저 독립 commit",
    "별도 transaction",
    "concurrent duplicate single winner",
    "immutable event ledger",
    "DELETE tombstone",
    "GET/append마다 Catalog UI_READY와 C1 상태를 재검증",
    "route template",
    "CANDIDATE_NOT_UI_READY",
    "model mapping/set/rank drift는 PARTIAL issue가 아니라 503",
    "반복 action 0..N",
    "stage desc, server occurredAt asc, actionEventId asc",
    "eligibility-version row",
    "sorted dual advisory lock",
    "한 REQUIRES_NEW transaction",
    "mapping_version",
    "Catalog singleton FOR SHARE",
    "original wire는 201+replayed=false",
    "replay wire는 200+replayed=true",
    "PENDING_ACTION inbox",
    "1..N gap 없는 수열",
}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def load_json(path: str) -> dict:
    return json.loads(read(REPO / path))


def main() -> int:
    errors: list[str] = []
    packet_path = ROOT / "product-decision-packet.md"
    tracker_path = ROOT / "decision-needed.md"
    readme_path = ROOT / "README.md"
    tasks_path = ROOT / "tasks/implementation-backlog.yaml"
    openapi_path = ROOT / "api/openapi.fragment.yaml"
    for path in (packet_path, tracker_path, readme_path, tasks_path, openapi_path):
        if not path.is_file():
            errors.append(f"missing {path.relative_to(ROOT)}")
    if errors:
        return fail(errors)

    packet = read(packet_path)
    tracker = read(tracker_path)
    readme = read(readme_path)
    tasks = yaml.safe_load(read(tasks_path))
    openapi = yaml.safe_load(read(openapi_path))

    if set(re.findall(r"DN-C2B-\d{3}", packet)) != DECISIONS:
        errors.append("packet decision set drift")
    for decision in DECISIONS:
        if f"## {decision} —" not in packet:
            errors.append(f"missing section {decision}")
        row = next((line for line in tracker.splitlines() if line.startswith("|") and f"`{decision}`" in line), "")
        expected = "`APPROVED_BY_PRODUCT_OWNER_2026-08-30`" if decision == "DN-C2B-002" else "`REQUIRES_APPROVAL`"
        if expected not in row:
            errors.append(f"{decision} approval state drift: expected {expected}")
    if "P0 승인 현황: `1/6`" not in tracker:
        errors.append("tracker approval count must remain 1/6")
    missing = sorted(value for value in REQUIRED if value not in packet)
    if missing:
        errors.append(f"missing canonical values: {missing}")
    for heading in ("반대안", "Privacy·UX", "Rollback"):
        if packet.count(f"### {heading}") != 6:
            errors.append(f"expected six {heading} subsections")

    for value in (
        "[0.000253,0.002783]",
        "0.285714",
        "0.999825",
        "0.009382→0.005113",
        "332.9953 rps",
        "local 후보",
    ):
        if value not in packet:
            errors.append(f"missing evidence boundary {value}")
    for forbidden in ("public champion이다", "production SLA다", "미평가=싫어요"):
        if forbidden in packet:
            errors.append(f"forbidden claim: {forbidden}")

    rec11 = load_json("docs/recommendation/evidence/manifests/rec-ev-011.json")
    if rec11.get("selected_alpha", {}).get("10") != 0.2:
        errors.append("REC-EV-011 K10 alpha drift")
    if rec11.get("conclusion", {}).get("personal_ranking_champion") is not None:
        errors.append("REC-EV-011 cannot be a champion")
    rec6 = load_json("docs/recommendation/evidence/manifests/rec-ev-006.json")
    pop = rec6.get("metrics", {}).get("coverage", {}).get("POPULARITY_BASELINE", {})
    if pop.get("evaluated_recommendations") != 40000 or pop.get("emittable_candidate_coverage") != 0.999825:
        errors.append("REC-EV-006 coverage drift")
    if rec6.get("validation", {}).get("reason_ui_approved") is not False:
        errors.append("REC-EV-006 reason UI must remain unapproved")
    rec7 = load_json("docs/recommendation/evidence/manifests/rec-ev-007.json")
    tech = rec7.get("technical_recommendation", {})
    if tech.get("spring_outbound_timeout_ms") != 750 or tech.get("active_rating_snapshot_healthy_path_target_ms") != 3000:
        errors.append("REC-EV-007 candidate values drift")
    if tech.get("production_validation_required") is not True or tech.get("stale_success_fallback") != "DISABLED":
        errors.append("REC-EV-007 production/stale boundary drift")
    rec13 = load_json("docs/recommendation/evidence/manifests/rec-ev-013.json")
    if rec13.get("protocol", {}).get("selected") is not None or rec13.get("product_approved") is not False:
        errors.append("REC-EV-013 no-feasible/non-approved boundary drift")

    rows = {row.get("id"): row for row in tasks.get("tasks", [])}
    expected_task_states = {
        "TASK-C2B-001": "PRODUCT_DECISIONS_PENDING_LOCAL_BASELINE_AUTHORIZED",
        "TASK-C2B-002": "DONE_LOCAL_BASELINE_CONTRACT",
        "TASK-C2B-003": "READY_LOCAL_BASELINE",
        "TASK-C2B-004": "READY_LOCAL_BASELINE",
        "TASK-C2B-005": "BLOCKED_EXTENSION",
    }
    for task_id, expected in expected_task_states.items():
        if rows.get(task_id, {}).get("status") != expected:
            errors.append(f"{task_id} must be {expected}")
    for task_id, row in rows.items():
        if task_id not in expected_task_states and row.get("status") != "BLOCKED":
            errors.append(f"{task_id} must remain BLOCKED")
    if "TASK-C2B-011" not in rows or "AC-C2B-057" not in rows["TASK-C2B-011"].get("acceptance_ids", []):
        errors.append("C1 click contract Gate missing")

    if "로컬 baseline 구현: `AUTHORIZED`" not in readme or "production activation" not in readme:
        errors.append("README local/production authority drift")
    if openapi.get("x-public-implementation-status") != "AUTHORIZED_LOCAL_BASELINE_ONLY" or openapi.get("x-main-openapi-merged") is not True:
        errors.append("local baseline implementation/main merge boundary drift")
    if openapi.get("x-production-activation-status") != "BLOCKED":
        errors.append("production activation must remain blocked")
    if openapi.get("x-c1-click-contract-status") != "BLOCKED_REQUIRES_EVENT_ID":
        errors.append("C1 click contract blocker drift")
    if openapi.get("x-catalog-activation-contract-status") != "BLOCKED_REQUIRES_SHARED_VERSION_LOCK":
        errors.append("Catalog activation contract blocker drift")
    if openapi.get("x-additional-page-status") != "APPROVED_CUMULATIVE_THREE_WITH_SIGNED_CURSOR":
        errors.append("additional-page boundary drift")
    if openapi.get("x-item-exit-policy") != "RATING_COMPLETED_OR_EXPLICIT_NOT_INTERESTED":
        errors.append("rating/dismiss item-exit boundary drift")
    if openapi.get("x-delivery-snapshot-boundary", {}).get("mappingVersionColumn") != "RECOMMENDATION_DELIVERY_BATCH.mapping_version":
        errors.append("mapping_version product/OpenAPI boundary drift")
    if openapi.get("x-catalog-linearization-boundary", {}).get("versionRow") != "CATALOG_DISCOVERY_ELIGIBILITY_VERSION singleton":
        errors.append("Catalog linearization product/OpenAPI boundary drift")
    if openapi.get("x-idempotent-wire-replay-boundary", {}).get("original") != "HTTP 201 with replayed=false":
        errors.append("original/replay product/OpenAPI boundary drift")
    if "PENDING_ACTION" not in str(openapi.get("x-projector-late-action-boundary", {}).get("inbox", "")):
        errors.append("late-action product/OpenAPI boundary drift")
    operations = [
        operation.get("operationId")
        for path_item in openapi.get("paths", {}).values()
        for method, operation in path_item.items()
        if method.lower() in {"get", "post", "put", "patch", "delete"}
    ]
    if set(operations) != {
        "getMyPersonalDiscoveryRecommendations",
        "appendMyPersonalDiscoveryRecommendations",
        "dismissMyRecommendationAsNotInterested",
        "commitMyRecommendationExposure",
        "recordMyRecommendationAction",
    }:
        errors.append("C2B draft operation set drift")
    operation_status = {
        operation.get("operationId"): operation.get("x-implementation-status")
        for path_item in openapi.get("paths", {}).values()
        for method, operation in path_item.items()
        if method.lower() in {"get", "post", "put", "patch", "delete"}
    }
    for operation_id in {
        "getMyPersonalDiscoveryRecommendations",
        "appendMyPersonalDiscoveryRecommendations",
        "dismissMyRecommendationAsNotInterested",
    }:
        if operation_status.get(operation_id) != "AUTHORIZED_LOCAL_CONTRACT":
            errors.append(f"{operation_id} local authority drift")
    for operation_id in {"commitMyRecommendationExposure", "recordMyRecommendationAction"}:
        if operation_status.get(operation_id) != "BLOCKED":
            errors.append(f"{operation_id} must remain blocked")

    secret_patterns = (
        re.compile(r"eyJ[A-Za-z0-9_-]{20,}"),
        re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
        re.compile(r"(?im)^\s*(?:TOKEN|CLIENT_SECRET|PASSWORD)\s*=\s*\S+"),
    )
    for pattern in secret_patterns:
        if pattern.search(packet):
            errors.append(f"secret-like value found: {pattern.pattern}")
    if errors:
        return fail(errors)
    print(
        "C2B product decision packet validation passed: DN-C2B-002 approval 1/6, cumulative append and "
        "rating/explicit-dismiss exit fixed; three local baseline operations authorized; production, "
        "personalization, discovery/reason/star/utility and attribution remain blocked."
    )
    return 0


def fail(errors: list[str]) -> int:
    print("C2B product decision packet validation failed:")
    for error in errors:
        print(f"- {error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
