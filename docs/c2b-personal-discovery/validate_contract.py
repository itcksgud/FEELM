#!/usr/bin/env python3
"""Validate the C2B local baseline contract and fail-closed extensions."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = ROOT.parents[1]
STATUS = "APPROVED_LOCAL_BASELINE_WITH_BLOCKED_EXTENSIONS"
DECISIONS = {f"DN-C2B-{number:03d}" for number in range(1, 7)}
SCREENS = {f"SCR-C2B-{number:03d}" for number in range(1, 4)}
SCENARIOS = {f"SCN-C2B-{number:03d}" for number in range(1, 7)}
ACCEPTANCE = {f"AC-C2B-{number:03d}" for number in range(1, 101)}
LOCAL_ACCEPTANCE = {
    "AC-C2B-001", "AC-C2B-012", "AC-C2B-014", "AC-C2B-015", "AC-C2B-020",
    "AC-C2B-063", "AC-C2B-087", "AC-C2B-088", "AC-C2B-089", "AC-C2B-090",
    "AC-C2B-092", "AC-C2B-093", "AC-C2B-094", "AC-C2B-095", "AC-C2B-096",
    "AC-C2B-097",
}
TASKS = {f"TASK-C2B-{number:03d}" for number in range(1, 12)}
REQUIREMENTS = {
    "REQ-C2B-LOCAL-GET",
    "REQ-C2B-LOCAL-APPEND",
    "REQ-C2B-LOCAL-DISMISS",
    "FR-10",
    "FR-12",
    "FR-13",
    "REQ-C2B-EXACT-ATTRIBUTION",
    "NFR-01",
    "NFR-04",
    "NFR-05",
    "NFR-07",
    "REQ-C2B-DELIVERY-CONSISTENCY",
    "REQ-C2B-CUMULATIVE-COLLECTION",
}
OPERATIONS = {
    "getMyPersonalDiscoveryRecommendations",
    "appendMyPersonalDiscoveryRecommendations",
    "dismissMyRecommendationAsNotInterested",
    "commitMyRecommendationExposure",
    "recordMyRecommendationAction",
}
LOCAL_OPERATIONS = {
    "getMyPersonalDiscoveryRecommendations",
    "appendMyPersonalDiscoveryRecommendations",
    "dismissMyRecommendationAsNotInterested",
}
BLOCKED_OPERATIONS = OPERATIONS - LOCAL_OPERATIONS
TESTS = {
    "TEST-CONTRACT-C2B",
    "TEST-BE-C2B-DELIVERY",
    "TEST-BE-C2B-EXPOSURE",
    "TEST-BE-C2B-ATTRIBUTION",
    "TEST-FE-C2B",
    "TEST-E2E-C2B",
    "TEST-SECURITY-C2B",
    "TEST-PERF-C2B",
    "TEST-EVIDENCE-C2B",
}
ENTITIES = {
    "USER_ACCOUNT",
    "MOVIE_IDENTITY",
    "CATALOG_VERSION",
    "RATING_INPUT_SNAPSHOT",
    "DOMAIN_OUTBOX",
    "IDEMPOTENCY_RECORD",
    "USER_RECOMMENDATION_ELIGIBILITY_VERSION",
    "CATALOG_DISCOVERY_ELIGIBILITY_VERSION",
    "C1_RECOMMENDATION_SOURCE_EVENT_INBOX",
    "USER_BEHAVIOR_EVENT",
    "RECOMMENDATION_DELIVERY_BATCH",
    "RECOMMENDATION_DELIVERY_ITEM",
    "RECOMMENDATION_DELIVERY_APPEND",
    "RECOMMENDATION_ITEM_DISMISSAL",
    "RECOMMENDATION_DELIVERY_ISSUE",
    "RECOMMENDATION_EXPOSURE_BATCH",
    "RECOMMENDATION_EXPOSURE_ITEM",
    "RECOMMENDATION_ACTION",
    "RECOMMENDATION_ATTRIBUTION_PROJECTION",
    "RECOMMENDATION_ATTRIBUTION_EVENT_LEDGER",
    "WATCH_INTENT",
    "VIEWING_RECORD",
    "RATING",
}
BUSINESS_RULES = (
    {f"BR-C2B-{number:03d}" for number in range(1, 6)}
    | {f"BR-C2B-{number:03d}" for number in range(10, 19)}
    | {f"BR-C2B-{number:03d}" for number in range(20, 26)}
    | {f"BR-C2B-{number:03d}" for number in range(30, 38)}
    | {f"BR-C2B-{number:03d}" for number in range(40, 48)}
    | {f"BR-C2B-{number:03d}" for number in range(48, 61)}
    | {f"BR-C2B-{number:03d}" for number in range(61, 67)}
    | {f"BR-C2B-{number:03d}" for number in range(67, 76)}
)
EXPECTED_FILES = {
    "README.md",
    "00-product-scope.md",
    "01-glossary-and-policies.md",
    "02-business-rules.md",
    "03-state-machines.md",
    "decision-needed.md",
    "product-decision-packet.md",
    "evidence-dependencies.yaml",
    "api/openapi.fragment.yaml",
    "data/logical-erd.md",
    "data/data-dictionary.md",
    "ui/navigation-map.md",
    "ui/screen-contracts.md",
    "testing/fixtures.md",
    "testing/acceptance-tests.md",
    "tasks/implementation-backlog.yaml",
    "traceability/requirements.csv",
    "validate_contract.py",
    "validate_product_decision_packet.py",
}


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8-sig")


def load_yaml(path: str) -> dict[str, Any]:
    value = yaml.safe_load(read(path))
    return value if isinstance(value, dict) else {}


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    return value if isinstance(value, dict) else {}


def references(pattern: str, value: str) -> set[str]:
    return set(re.findall(pattern, value))


def split_refs(value: str | None) -> set[str]:
    return {item.strip() for item in (value or "").split("|") if item.strip()}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect_property_names(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        properties = value.get("properties")
        if isinstance(properties, dict):
            result.update(str(name) for name in properties)
        for child in value.values():
            result.update(collect_property_names(child))
    elif isinstance(value, list):
        for child in value:
            result.update(collect_property_names(child))
    return result


def cache_control_tokens(value: Any) -> set[str]:
    return {token.strip().lower() for token in str(value or "").split(",") if token.strip()}


def walk_decisions(value: Any, errors: list[str], path: str = "$") -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in {"x-decision-required", "x-production-decision-required"}:
                if not isinstance(child, list) or not child:
                    errors.append(f"{child_path} must be a non-empty decision list")
                else:
                    found = {str(item) for item in child}
                    result.update(found)
                    if found - DECISIONS:
                        errors.append(f"{child_path} unknown decisions {sorted(found - DECISIONS)}")
            result.update(walk_decisions(child, errors, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            result.update(walk_decisions(child, errors, f"{path}[{index}]"))
    return result


def validate_status_and_ids(errors: list[str]) -> None:
    status_files = (
        "README.md",
        "00-product-scope.md",
        "01-glossary-and-policies.md",
        "02-business-rules.md",
        "03-state-machines.md",
        "decision-needed.md",
        "data/logical-erd.md",
        "data/data-dictionary.md",
        "ui/navigation-map.md",
        "ui/screen-contracts.md",
        "testing/fixtures.md",
        "testing/acceptance-tests.md",
    )
    for path in status_files:
        if STATUS not in read(path):
            errors.append(f"{path} missing exact local-baseline status")
    if references(r"BR-C2B-\d{3}", read("02-business-rules.md")) != BUSINESS_RULES:
        errors.append("business rule ID set drift")
    if references(r"SCN-C2B-\d{3}", read("03-state-machines.md")) != SCENARIOS:
        errors.append("scenario ID set drift")
    if references(r"SCR-C2B-\d{3}", read("ui/screen-contracts.md")) != SCREENS:
        errors.append("screen ID set drift")
    if references(r"AC-C2B-\d{3}", read("testing/acceptance-tests.md")) != ACCEPTANCE:
        errors.append("acceptance ID set drift")
    acceptance_text = read("testing/acceptance-tests.md")
    authority_match = re.search(r"로컬 구현 승인 AC:(.*?)(?:확장 차단 AC:)", acceptance_text, re.DOTALL)
    if not authority_match or references(r"AC-C2B-\d{3}", authority_match.group(1)) != LOCAL_ACCEPTANCE:
        errors.append("authorized local baseline AC set drift")
    if references(r"DN-C2B-\d{3}", read("decision-needed.md")) != DECISIONS:
        errors.append("decision ID set drift")


def validate_openapi(errors: list[str]) -> None:
    document = load_yaml("api/openapi.fragment.yaml")
    if document.get("openapi") != "3.1.0":
        errors.append("OpenAPI must be 3.1.0")
    if document.get("x-contract-status") != STATUS:
        errors.append("OpenAPI contract status drift")
    if document.get("x-main-openapi-merged") is not True:
        errors.append("C2B local baseline must be merged into main OpenAPI")
    if document.get("x-public-implementation-status") != "AUTHORIZED_LOCAL_BASELINE_ONLY":
        errors.append("C2B public implementation boundary drift")
    if document.get("x-production-activation-status") != "BLOCKED":
        errors.append("C2B production activation must remain BLOCKED")
    if document.get("security") != [{"bearerAuth": []}]:
        errors.append("C2B draft must require global bearer auth")

    operations: dict[str, dict[str, Any]] = {}
    for path_item in document.get("paths", {}).values():
        for method, operation in path_item.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            operation_id = operation.get("operationId")
            if operation_id in operations:
                errors.append(f"duplicate operationId {operation_id}")
            operations[str(operation_id)] = operation
    if set(operations) != OPERATIONS:
        errors.append(f"OpenAPI operation drift: {sorted(set(operations) ^ OPERATIONS)}")
    for operation_id, operation in operations.items():
        expected_status = "AUTHORIZED_LOCAL_CONTRACT" if operation_id in LOCAL_OPERATIONS else "BLOCKED"
        if operation.get("x-implementation-status") != expected_status:
            errors.append(f"{operation_id} implementation status must be {expected_status}")
        if operation.get("security") == []:
            errors.append(f"{operation_id} cannot override bearer auth")
    delivery_cache_header = (
        operations.get("getMyPersonalDiscoveryRecommendations", {})
        .get("responses", {}).get("200", {}).get("headers", {}).get("Cache-Control", {})
    )
    if cache_control_tokens(delivery_cache_header.get("example")) != {"private", "no-store"}:
        errors.append("fragment Cache-Control must require private and no-store independent of order")
    if walk_decisions(document, errors) != DECISIONS:
        errors.append("OpenAPI decision coverage drift")

    schemas = document.get("components", {}).get("schemas", {})
    delivery = schemas.get("RecommendationDelivery", {}).get("properties", {})
    if delivery.get("label", {}).get("enum") != ["POPULARITY_BASELINE"]:
        errors.append("public label must remain POPULARITY_BASELINE only")
    if delivery.get("composition", {}).get("enum") != ["BASELINE_THREE"]:
        errors.append("composition must remain BASELINE_THREE only")
    if delivery.get("items", {}).get("maxItems") != 500:
        errors.append("delivery collection must cap active items at Top500")
    if "PersonalizationState" in schemas or "personalization" in delivery:
        errors.append("authorized local delivery must not expose personalization")
    if "DeliverySnapshot" in schemas or "VersionString" in schemas:
        errors.append("internal delivery versions must not be public schemas")
    if "snapshot" in delivery:
        errors.append("internal version snapshot must not be public")
    if delivery.get("deliveryRevision", {}).get("minimum") != 1:
        errors.append("opaque delivery revision contract drift")
    page_info = schemas.get("RecommendationPageInfo", {}).get("properties", {})
    if page_info.get("activeItemCount", {}).get("maximum") != 500 or page_info.get("nextCursor", {}).get("type") != ["string", "null"]:
        errors.append("cumulative pageInfo/cursor contract drift")
    delivery_item_properties = schemas.get("DeliveryItem", {}).get("properties", {})
    if "DisabledExpectedStar" in schemas or "expectedStar" in delivery_item_properties:
        errors.append("authorized local item must not expose expected-star")
    if "reasons" in delivery_item_properties:
        errors.append("authorized local item must not expose XAI reasons")
    if {"exposureStatus", "recommendationItemId"} & set(delivery_item_properties):
        errors.append("authorized local item must not expose blocked exposure/action state")
    issue = schemas.get("SafeIssue", {})
    if set(issue.get("required", [])) != {"code", "count", "retriable"}:
        errors.append("SafeIssue must require code/count/retriable")
    if issue.get("properties", {}).get("count", {}).get("minimum") != 1:
        errors.append("SafeIssue count must be positive")
    exposure_request = schemas.get("CommitExposureRequest", {}).get("properties", {})
    if "exposedAt" in exposure_request:
        errors.append("client must not supply exposedAt")
    action_request = schemas.get("RecordRecommendationActionRequest", {}).get("properties", {})
    if "occurredAt" in action_request or "watchIntentId" in action_request:
        errors.append("client must not supply action time or WatchIntent ID")
    if action_request.get("c1ClickEventId", {}).get("format") != "uuid":
        errors.append("C1 current click event linkage missing")
    if document.get("x-c1-click-contract-status") != "BLOCKED_REQUIRES_EVENT_ID":
        errors.append("C1 click contract must remain blocked")
    if document.get("x-catalog-activation-contract-status") != "BLOCKED_REQUIRES_SHARED_VERSION_LOCK":
        errors.append("C0 Catalog shared-version amendment must remain blocked")
    if document.get("x-additional-page-status") != "APPROVED_CUMULATIVE_THREE_WITH_SIGNED_CURSOR":
        errors.append("additional-page/cursor scope drift")
    if document.get("x-item-exit-policy") != "RATING_COMPLETED_OR_EXPLICIT_NOT_INTERESTED":
        errors.append("rating/dismiss exit policy drift")
    cumulative = document.get("x-cumulative-collection-boundary", {})
    if cumulative.get("initialCount") != 3 or cumulative.get("appendPageSize") != 3 or cumulative.get("activeItemMaximum") != 500:
        errors.append("cumulative collection count boundary drift")
    if cumulative.get("appendDoesNotReplaceExisting") is not True or cumulative.get("reentryRestoresServerActiveItems") is not True:
        errors.append("cumulative collection retention boundary drift")
    if document.get("x-internal-ranking-boundary") != {
        "request": "ACTIVE_CANDIDATE_STORE_FIRST_MIN_500_TOTAL_EXACT_SET",
        "response": "SAME_UUID_SET_EXACTLY_ONCE_SOURCE_RANK_1_TO_N",
        "contractDrift": "PUBLIC_503",
    }:
        errors.append("Top500 exact request/response boundary drift")
    if document.get("x-delivery-snapshot-boundary") != {
        "mappingVersionColumn": "RECOMMENDATION_DELIVERY_BATCH.mapping_version",
        "mappingVersionType": "non-empty versioned identifier",
        "reuseKeyIncludes": ["mappingVersion"],
        "staleWhen": "current active C2A mapping version differs from stored mapping_version",
        "publicExposure": "forbidden",
    }:
        errors.append("typed mapping_version snapshot/stale boundary drift")
    catalog_boundary = document.get("x-catalog-linearization-boundary", {})
    if catalog_boundary.get("versionRow") != "CATALOG_DISCOVERY_ELIGIBILITY_VERSION singleton" or catalog_boundary.get("lockOrder") != "catalog FOR SHARE then actor eligibility FOR SHARE":
        errors.append("Catalog/C1 shared-version lock order drift")
    if "through delivery or exposure commit" not in str(catalog_boundary.get("c2bFinalCheck", "")):
        errors.append("Catalog final-check/commit linearization drift")
    replay_boundary = document.get("x-idempotent-wire-replay-boundary", {})
    if replay_boundary != {
        "storedResult": "canonical domain payload only; excludes HTTP status and replayed transport flag",
        "original": "HTTP 201 with replayed=false",
        "replay": "HTTP 200 with replayed=true and all stored domain fields byte-equivalent after canonical JSON serialization",
        "conflict": "same key or body event ID with canonical body drift returns 409 without mutation",
    }:
        errors.append("201/200 idempotent wire replay boundary drift")
    late_boundary = document.get("x-projector-late-action-boundary", {})
    if late_boundary.get("ordering") != "stage rank ascending, source revision ascending nulls first, server occurredAt ascending, sourceEventId ascending":
        errors.append("late-action deterministic reconcile ordering drift")
    if "PENDING_ACTION" not in str(late_boundary.get("inbox", "")) or "never backfilled" not in str(late_boundary.get("noGuessing", "")):
        errors.append("event-before-action inbox/no-guessing drift")
    if late_boundary.get("pendingRetention") != "event server time plus 90d candidate, then terminal EXPIRED_UNATTRIBUTED with no later backfill":
        errors.append("late-action pending retention/terminal drift")

    summary = schemas.get("SelectionSummary", {})
    if set(summary.get("required", [])) != {"scannedCount", "selectedCount", "excludedCount"}:
        errors.append("SelectionSummary required fields drift")
    summary_props = summary.get("properties", {})
    if summary_props.get("scannedCount", {}).get("maximum") != 500:
        errors.append("SelectionSummary scannedCount must cap at 500")
    if summary_props.get("selectedCount", {}).get("maximum") != 3:
        errors.append("SelectionSummary selectedCount must cap at 3")
    if "scannedCount=selectedCount+excludedCount" not in summary.get("description", ""):
        errors.append("SelectionSummary arithmetic invariant missing")
    expected_arithmetic = [
        "selectionSummary.scannedCount = selectionSummary.selectedCount + selectionSummary.excludedCount",
        "selectionSummary.selectedCount = count(appendedItems)",
        "selectionSummary.excludedCount = sum(issues[*].count)",
    ]
    append_schema = schemas.get("RecommendationAppend", {})
    append_props = append_schema.get("properties", {})
    if append_schema.get("x-arithmetic-invariants") != expected_arithmetic:
        errors.append("RecommendationAppend executable arithmetic semantics drift")
    if append_props.get("selectionSummary", {}).get("$ref") != "#/components/schemas/SelectionSummary":
        errors.append("append selectionSummary linkage missing")
    if append_props.get("appendedItems", {}).get("maxItems") != 3:
        errors.append("append delta must cap at three items")
    issues = append_props.get("issues", {})
    if issues.get("maxItems") != 3 or issues.get("uniqueItems") is not True:
        errors.append("SafeIssue list must be max three and unique")
    if issues.get("x-unique-by") != "code":
        errors.append("SafeIssue code-key uniqueness drift")
    if issue.get("properties", {}).get("code", {}).get("enum") != [
        "CANDIDATE_NOT_UI_READY", "CANDIDATE_ALREADY_RATED", "CANDIDATE_ALREADY_SEEN"
    ]:
        errors.append("SafeIssue allowlist/precedence drift")
    expected_issue_contract = {
        code: {"retriable": False, "countMinimum": 1}
        for code in ("CANDIDATE_NOT_UI_READY", "CANDIDATE_ALREADY_RATED", "CANDIDATE_ALREADY_SEEN")
    }
    if issue.get("x-code-contract") != expected_issue_contract or issue.get("properties", {}).get("retriable", {}).get("const") is not False:
        errors.append("SafeIssue count/retriable code contract drift")
    source_rank = schemas.get("DeliveryItem", {}).get("properties", {}).get("sourceRank", {})
    if source_rank.get("minimum") != 1 or source_rank.get("maximum") != 500:
        errors.append("sourceRank must be 1..500")
    delivery_item_props = schemas.get("DeliveryItem", {}).get("properties", {})
    if delivery_item_props.get("position", {}).get("maximum") != 500:
        errors.append("active collection item sequence contract drift")
    if {"displayStatus", "exposureStatus", "recommendationItemId"} & set(delivery_item_props):
        errors.append("local baseline item exposes blocked display/exposure/action state")
    append_request = schemas.get("AppendRecommendationsRequest", {}).get("properties", {})
    if set(append_request) != {"appendEventId", "expectedRevision", "cursor"}:
        errors.append("append request must be exact event/revision/cursor contract")
    dismissal_request = schemas.get("DismissRecommendationRequest", {}).get("properties", {})
    if dismissal_request.get("reason", {}).get("const") != "NOT_INTERESTED" or "rating" in {name.lower() for name in dismissal_request}:
        errors.append("explicit dismissal must remain NOT_INTERESTED-only and rating-free")
    if operations.get("getMyPersonalDiscoveryRecommendations", {}).get("x-prepared-replay-revalidation") != "CATALOG_AND_C1_SHARED_LOCK_DOUBLE_CHECK_THROUGH_COMMIT":
        errors.append("prepared replay fresh revalidation boundary missing")
    for operation_id in (
        "appendMyPersonalDiscoveryRecommendations",
        "dismissMyRecommendationAsNotInterested",
        "commitMyRecommendationExposure",
        "recordMyRecommendationAction",
    ):
        if operations.get(operation_id, {}).get("x-idempotency-semantics") != "SORTED_HEADER_BODY_ADVISORY_LOCK_ONE_TX_SAFE_RESULT":
            errors.append(f"{operation_id} dual-id/single-winner semantics drift")
        if operations.get(operation_id, {}).get("x-wire-result") != "ORIGINAL_201_REPLAY_200_DOMAIN_PAYLOAD_IDENTICAL_REPLAY_FLAG_TRANSPORT_DERIVED":
            errors.append(f"{operation_id} original/replay wire mapping drift")
    response_contracts = {
        "appendMyPersonalDiscoveryRecommendations": ("RecommendationAppendCreated", "RecommendationAppendReplay"),
        "dismissMyRecommendationAsNotInterested": ("RecommendationDismissalCreated", "RecommendationDismissalReplay"),
        "commitMyRecommendationExposure": ("ExposureCommitCreated", "ExposureCommitReplay"),
        "recordMyRecommendationAction": ("RecommendationActionCreated", "RecommendationActionReplay"),
    }
    for operation_id, (created_schema, replay_schema) in response_contracts.items():
        responses = operations.get(operation_id, {}).get("responses", {})
        created_ref = responses.get("201", {}).get("content", {}).get("application/json", {}).get("schema", {}).get("$ref")
        replay_ref = responses.get("200", {}).get("content", {}).get("application/json", {}).get("schema", {}).get("$ref")
        if created_ref != f"#/components/schemas/{created_schema}" or replay_ref != f"#/components/schemas/{replay_schema}":
            errors.append(f"{operation_id} 201/200 response schema drift")
        if schemas.get(created_schema, {}).get("allOf", [{}])[-1].get("properties", {}).get("replayed", {}).get("const") is not False:
            errors.append(f"{created_schema} replayed=false drift")
        if schemas.get(replay_schema, {}).get("allOf", [{}])[-1].get("properties", {}).get("replayed", {}).get("const") is not True:
            errors.append(f"{replay_schema} replayed=true drift")
    contiguous_contract = {"start": 1, "arrayOrderEqualsPosition": True, "gaps": "forbidden"}
    for schema_name, id_key in (("CommitExposureRequest", "deliveryItemId"), ("ExposureCommit", "recommendationItemId")):
        items_schema = schemas.get(schema_name, {}).get("properties", {}).get("items", {})
        if items_schema.get("x-unique-by") != id_key or items_schema.get("x-unique-position-by") != "position" or items_schema.get("x-contiguous-position-sequence") != contiguous_contract:
            errors.append(f"{schema_name} keyed contiguous position contract drift")
    if operations.get("recordMyRecommendationAction", {}).get("x-transaction-boundary") != "C1_CLICK_COMMITTED_FIRST_C2B_ACTION_SEPARATE":
        errors.append("C1/C2B transaction separation drift")
    action_operation = operations.get("recordMyRecommendationAction", {})
    if action_operation.get("x-action-cardinality") != "RECOMMENDATION_ITEM_ZERO_TO_MANY_ACTIONS":
        errors.append("repeated action cardinality drift")
    if action_operation.get("x-projection-winner") != "STAGE_DESC_SERVER_OCCURRED_AT_ASC_ACTION_EVENT_ID_ASC":
        errors.append("singular projection winner drift")
    error_paths = schemas.get("ErrorResponse", {}).get("properties", {}).get("path", {}).get("enum", [])
    if set(error_paths) != {
        "/api/v1/me/recommendations/personal-discovery",
        "/api/v1/me/recommendation-deliveries/{deliveryId}/append",
        "/api/v1/me/recommendation-deliveries/{deliveryId}/exposures",
        "/api/v1/me/recommendation-delivery-items/{deliveryItemId}/dismissals",
        "/api/v1/me/recommendation-items/{recommendationItemId}/actions",
        "UNMATCHED_ROUTE_TEMPLATE",
    }:
        errors.append("ErrorResponse route-template allowlist drift")

    forbidden_properties = {
        "userId",
        "actorUserId",
        "email",
        "authorization",
        "token",
        "rawRequest",
        "rawResponse",
        "destinationUrl",
        "observedRelativeUtility",
        "satisfaction",
        "recommendationVersion",
        "artifactSetVersion",
        "policyVersion",
        "candidateSetVersion",
        "inputVersion",
        "checksum",
    }
    leaked = collect_property_names(document) & forbidden_properties
    if leaked:
        errors.append(f"OpenAPI contains forbidden public/privacy properties {sorted(leaked)}")


def validate_audit_semantics(errors: list[str]) -> None:
    combined = "\n".join(
        read(path)
        for path in (
            "01-glossary-and-policies.md",
            "02-business-rules.md",
            "03-state-machines.md",
            "data/logical-erd.md",
            "data/data-dictionary.md",
            "testing/acceptance-tests.md",
        )
    )
    required_tokens = {
        "C1 OTT click/WatchIntent transaction은 추천과 무관하게 먼저 독립 commit",
        "Viewing-only",
        "active Rating",
        "`min(500,total)`",
        "sourceRank 1..N<=500",
        "CANDIDATE_NOT_UI_READY` → `CANDIDATE_ALREADY_RATED` → `CANDIDATE_ALREADY_SEEN",
        "selectionSummary.scannedCount = selectedCount + excludedCount",
        "excludedCount = sum(issues.count)",
        "actor,operation,key",
        "single winner",
        "immutable dedup ledger",
        "strictly increasing revision",
        "DELETED` tombstone",
        "route template",
        "1..3 RECOMMENDATION_EXPOSURE_ITEM",
        "0..500 RECOMMENDATION_DELIVERY_ITEM",
        "RECOMMENDATION_DELIVERY_APPEND",
        "RECOMMENDATION_ITEM_DISMISSAL",
        "COMPLETED_RATED",
        "DISMISSED_NOT_INTERESTED",
        "IDEMPOTENCY_RECORD",
        "REQUIRES_NEW` transaction",
        "USER_RECOMMENDATION_ELIGIBILITY_VERSION",
        "FOR SHARE",
        "FOR UPDATE",
        "stageRank DESC",
        "server occurredAt ASC",
        "actionEventId ASC",
        "action 0..N",
        "mapping_version",
        "CATALOG_DISCOVERY_ELIGIBILITY_VERSION",
        "PENDING_ACTION",
        "original wire는 201+replayed=false",
        "position이 array order와 같은 1부터 시작하는 gap 없는 연속 수열",
    }
    for token in required_tokens:
        if token not in combined:
            errors.append(f"final-audit semantic token missing: {token}")
    forbidden = (
        "action+behavior/outbox+attribution projection은 한 transaction",
        "CANDIDATE_MODEL_REJECTED",
        "PERSONALIZATION_CANDIDATE_BLOCKED`는 SafeIssue",
        "MOV-C2B-PARTIAL",
        "model mapping 없음 | PARTIAL issue",
        "RECOMMENDATION_DELIVERY_BATCH 1 ── N RECOMMENDATION_DELIVERY_ISSUE",
        "code allowlist/precedence는 NOT_UI_READY",
        "UI_NOT_READY+RATED+SEEN",
        "`response_status`, `response_body`",
    )
    for token in forbidden:
        if token in combined:
            errors.append(f"final-audit forbidden semantic remains: {token}")

    fixtures = read("testing/fixtures.md")
    for token in (
        "MOV-C2B-MAPPING-MISSING",
        "SafeIssue/PARTIAL이 아닌 public 503 contract drift",
        "different header keys + same exposureBatchId",
        "eligibility/collection lock race",
    ):
        if token not in fixtures:
            errors.append(f"remediation fixture missing: {token}")
    dictionary = read("data/data-dictionary.md")
    for token in (
        "Exposure dual idempotency physical record",
        "eligibility_version",
        "winner_stage_rank",
        "winner_occurred_at",
        "winner_action_event_id",
        "cardinality 0..3",
        "domain_result_payload",
        "C1_RECOMMENDATION_SOURCE_EVENT_INBOX",
    ):
        if token not in dictionary:
            errors.append(f"remediation data contract missing: {token}")
    exact_data_contracts = (
        "| `mapping_version` | varchar | N |",
        "| `catalog_eligibility_version` | bigint | N |",
        "| `domain_result_payload` | jsonb | N |",
        "| `original_transport_status` | smallint | N |",
        "PENDING_ACTION/PROJECTED/DEAD_LETTER/EXPIRED_UNATTRIBUTED",
        "UNIQUE FK",
    )
    for token in exact_data_contracts:
        if token not in dictionary:
            errors.append(f"exact remediation data schema drift: {token}")
    logical = read("data/logical-erd.md")
    for token in (
        "mapping_version",
        "CATALOG_DISCOVERY_ELIGIBILITY_VERSION",
        "deferred contiguous-count check",
        "original wire는 201+replayed=false",
    ):
        if token not in logical and token not in read("02-business-rules.md"):
            errors.append(f"logical/DB remediation invariant missing: {token}")


def validate_tasks(errors: list[str]) -> None:
    document = load_yaml("tasks/implementation-backlog.yaml")
    if document.get("status") != STATUS:
        errors.append("task DAG status drift")
    rules = document.get("rules", {})
    required_true = {
        "local_baseline_implementation_authorized",
        "production_activation_forbidden",
        "blocked_extension_implementation_forbidden",
        "main_openapi_local_baseline_merge_required",
        "expected_star_must_remain_disabled",
        "reason_ui_must_remain_hidden",
        "explore05_must_remain_rejected",
        "c1_click_commits_before_c2b_action",
        "prepared_replay_requires_current_c1_catalog_revalidation",
        "top500_exact_set_boundary_required",
        "projector_revision_and_tombstone_ordering_required",
        "error_path_route_template_only",
        "exposure_dual_idempotency_one_transaction_required",
        "repeated_action_projection_winner_required",
        "c1_eligibility_version_linearization_required",
        "safe_issue_exact_enum_cardinality_required",
        "delivery_mapping_version_typed_stale_required",
        "safe_issue_code_contract_and_summary_arithmetic_required",
        "original_201_replay_200_domain_payload_required",
        "catalog_c1_final_commit_linearization_required",
        "late_action_event_reconcile_required",
        "exposure_item_keyed_contiguous_order_required",
        "cumulative_append_preserves_existing_items_required",
        "rating_or_explicit_dismiss_exit_required",
    }
    for name in required_true:
        if rules.get(name) is not True:
            errors.append(f"task DAG Gate must be true: {name}")
    if set(rules.get("required_decision_ids", [])) != DECISIONS:
        errors.append("task DAG decision set drift")
    rows = document.get("tasks", [])
    found = {row.get("id") for row in rows}
    if found != TASKS:
        errors.append(f"task ID set drift: {sorted(found)}")
    by_id = {row["id"]: row for row in rows if "id" in row}
    expected_states = {
        "TASK-C2B-001": "PRODUCT_DECISIONS_PENDING_LOCAL_BASELINE_AUTHORIZED",
        "TASK-C2B-002": "DONE_LOCAL_BASELINE_CONTRACT",
        "TASK-C2B-003": "READY_LOCAL_BASELINE",
        "TASK-C2B-004": "READY_LOCAL_BASELINE",
        "TASK-C2B-005": "BLOCKED_EXTENSION",
    }
    for task_id, expected_status in expected_states.items():
        if by_id.get(task_id, {}).get("status") != expected_status:
            errors.append(f"{task_id} status must be {expected_status}")
    for task_id, row in by_id.items():
        if task_id not in expected_states and row.get("status") != "BLOCKED":
            errors.append(f"{task_id} must remain BLOCKED")
        dependencies = set(row.get("depends_on", []))
        if not dependencies <= TASKS:
            errors.append(f"{task_id} unknown dependencies {sorted(dependencies - TASKS)}")
        if not set(row.get("decision_record_ids", [])) <= DECISIONS:
            errors.append(f"{task_id} unknown decision reference")
        if not set(row.get("acceptance_ids", [])) <= ACCEPTANCE:
            errors.append(f"{task_id} unknown acceptance reference")
    if {item for row in rows for item in row.get("acceptance_ids", [])} != ACCEPTANCE:
        errors.append("task DAG does not cover every acceptance criterion")
    if set(by_id.get("TASK-C2B-001", {}).get("evidence_ids", [])) != {
        "REC-EV-006", "REC-EV-007", "REC-EV-011", "REC-EV-004B", "REC-EV-003C", "REC-EV-013"
    }:
        errors.append("TASK-C2B-001 evidence dependency set drift")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            errors.append(f"task DAG cycle at {task_id}")
            return
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in by_id.get(task_id, {}).get("depends_on", []):
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in TASKS:
        visit(task_id)


def validate_trace(errors: list[str]) -> None:
    with (ROOT / "traceability/requirements.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if {row["requirement_id"] for row in rows} != REQUIREMENTS:
        errors.append("trace requirement row set drift")
    consistency = next((row for row in rows if row.get("requirement_id") == "REQ-C2B-DELIVERY-CONSISTENCY"), {})
    if split_refs(consistency.get("business_rule_ids")) != {f"BR-C2B-{number:03d}" for number in range(61, 67)}:
        errors.append("delivery consistency trace rule set drift")
    if split_refs(consistency.get("acceptance_ids")) != {f"AC-C2B-{number:03d}" for number in range(81, 87)}:
        errors.append("delivery consistency trace acceptance set drift")
    if not {"CATALOG_DISCOVERY_ELIGIBILITY_VERSION", "C1_RECOMMENDATION_SOURCE_EVENT_INBOX", "IDEMPOTENCY_RECORD"} <= split_refs(consistency.get("entities")):
        errors.append("delivery consistency trace entity set drift")
    cumulative = next((row for row in rows if row.get("requirement_id") == "REQ-C2B-CUMULATIVE-COLLECTION"), {})
    if split_refs(cumulative.get("business_rule_ids")) != {f"BR-C2B-{number:03d}" for number in range(67, 76)}:
        errors.append("cumulative collection trace rule set drift")
    if split_refs(cumulative.get("acceptance_ids")) != {f"AC-C2B-{number:03d}" for number in range(87, 101)}:
        errors.append("cumulative collection trace acceptance set drift")
    if not {"RECOMMENDATION_DELIVERY_APPEND", "RECOMMENDATION_ITEM_DISMISSAL"} <= split_refs(cumulative.get("entities")):
        errors.append("cumulative collection trace entity set drift")
    seen: dict[str, set[str]] = {
        "decision": set(), "rule": set(), "scenario": set(), "screen": set(),
        "operation": set(), "entity": set(), "acceptance": set(), "task": set(), "test": set(),
    }
    expected = {
        "decision": DECISIONS,
        "rule": BUSINESS_RULES,
        "scenario": SCENARIOS,
        "screen": SCREENS,
        "operation": OPERATIONS,
        "entity": ENTITIES,
        "acceptance": ACCEPTANCE,
        "task": TASKS,
        "test": TESTS,
    }
    columns = {
        "decision": "decision_ids",
        "rule": "business_rule_ids",
        "scenario": "scenario_ids",
        "screen": "screen_ids",
        "operation": "operation_ids",
        "entity": "entities",
        "acceptance": "acceptance_ids",
        "task": "task_ids",
        "test": "test_ids",
    }
    local_trace_ids = {"REQ-C2B-LOCAL-GET", "REQ-C2B-LOCAL-APPEND", "REQ-C2B-LOCAL-DISMISS"}
    local_test_refs = {"TEST-CONTRACT-C2B", "TEST-BE-C2B-DELIVERY", "TEST-FE-C2B", "TEST-SECURITY-C2B"}
    expected_local_rows = {
        "REQ-C2B-LOCAL-GET": {
            "operation": {"getMyPersonalDiscoveryRecommendations"},
            "acceptance": {"AC-C2B-001", "AC-C2B-012", "AC-C2B-014", "AC-C2B-015", "AC-C2B-020", "AC-C2B-094", "AC-C2B-095", "AC-C2B-096", "AC-C2B-097"},
            "entities": {"RECOMMENDATION_DELIVERY_BATCH", "RECOMMENDATION_DELIVERY_ITEM", "RECOMMENDATION_DELIVERY_ISSUE", "RATING", "VIEWING_RECORD", "USER_RECOMMENDATION_ELIGIBILITY_VERSION"},
        },
        "REQ-C2B-LOCAL-APPEND": {
            "operation": {"appendMyPersonalDiscoveryRecommendations"},
            "acceptance": {"AC-C2B-063", "AC-C2B-087", "AC-C2B-088", "AC-C2B-089", "AC-C2B-090", "AC-C2B-094", "AC-C2B-095", "AC-C2B-096", "AC-C2B-097"},
            "entities": {"RECOMMENDATION_DELIVERY_BATCH", "RECOMMENDATION_DELIVERY_APPEND", "RECOMMENDATION_DELIVERY_ITEM", "IDEMPOTENCY_RECORD", "RATING", "VIEWING_RECORD", "USER_RECOMMENDATION_ELIGIBILITY_VERSION"},
        },
        "REQ-C2B-LOCAL-DISMISS": {
            "operation": {"dismissMyRecommendationAsNotInterested"},
            "acceptance": {"AC-C2B-092", "AC-C2B-093"},
            "entities": {"RECOMMENDATION_ITEM_DISMISSAL", "RECOMMENDATION_DELIVERY_ITEM", "IDEMPOTENCY_RECORD", "USER_RECOMMENDATION_ELIGIBILITY_VERSION"},
        },
    }
    traced_local_acceptance: set[str] = set()
    for line, row in enumerate(rows, start=2):
        expected_status = "AUTHORIZED_LOCAL_BASELINE" if row.get("requirement_id") in local_trace_ids else "BLOCKED"
        if row.get("status") != expected_status:
            errors.append(f"trace line {line} status must be {expected_status}")
        if row.get("requirement_id") in local_trace_ids:
            traced_local_acceptance.update(split_refs(row.get("acceptance_ids")))
            expected_local = expected_local_rows[row["requirement_id"]]
            if split_refs(row.get("operation_ids")) != expected_local["operation"]:
                errors.append(f"trace line {line} local operation drift")
            if split_refs(row.get("acceptance_ids")) != expected_local["acceptance"]:
                errors.append(f"trace line {line} local acceptance mapping drift")
            if split_refs(row.get("entities")) != expected_local["entities"]:
                errors.append(f"trace line {line} local entity mapping drift")
            if split_refs(row.get("test_ids")) != local_test_refs:
                errors.append(f"trace line {line} local test mapping drift")
        for kind, column in columns.items():
            found = split_refs(row.get(column))
            if found - expected[kind]:
                errors.append(f"trace line {line} unknown {kind}: {sorted(found - expected[kind])}")
            seen[kind].update(found)
    for kind, required in expected.items():
        if seen[kind] != required:
            errors.append(f"trace {kind} coverage drift: missing {sorted(required - seen[kind])}")
    if traced_local_acceptance != LOCAL_ACCEPTANCE:
        errors.append("local trace/acceptance authority drift")


def validate_fixed_evidence(errors: list[str]) -> None:
    dependencies = load_yaml("evidence-dependencies.yaml")
    if dependencies.get("contract_status") != STATUS:
        errors.append("evidence dependency status drift")
    rows = {row.get("id"): row for row in dependencies.get("dependencies", [])}
    if set(rows) != {"REC-EV-006", "REC-EV-007", "REC-EV-011", "REC-EV-004B", "REC-EV-003C", "REC-EV-013"}:
        errors.append("evidence dependency ID set drift")
        return

    rec6 = load_json(REPOSITORY_ROOT / rows["REC-EV-006"]["manifest"])
    coverage6 = rec6.get("metrics", {}).get("coverage", {}).get("POPULARITY_BASELINE", {})
    if coverage6.get("evaluated_recommendations") != 40000 or coverage6.get("emittable_candidate_coverage") != 0.999825:
        errors.append("REC-EV-006 popularity reason coverage drift")
    if rec6.get("validation", {}).get("reason_ui_approved") is not False:
        errors.append("REC-EV-006 reason UI must remain unapproved")

    rec7 = load_json(REPOSITORY_ROOT / rows["REC-EV-007"]["manifest"])
    tech7 = rec7.get("technical_recommendation", {})
    if tech7.get("spring_outbound_timeout_ms") != 750 or tech7.get("active_rating_snapshot_healthy_path_target_ms") != 3000:
        errors.append("REC-EV-007 local operational candidates drift")
    if tech7.get("production_validation_required") is not True or tech7.get("stale_success_fallback") != "DISABLED":
        errors.append("REC-EV-007 production/stale boundary drift")

    rec11 = load_json(REPOSITORY_ROOT / rows["REC-EV-011"]["manifest"])
    if rec11.get("evidence_id") != "REC-EV-011" or rec11.get("selected_alpha", {}).get("10") != 0.2:
        errors.append("REC-EV-011 K10 alpha 0.2 evidence drift")
    conclusion11 = rec11.get("conclusion", {})
    if conclusion11.get("personal_ranking_champion") is not None or conclusion11.get("public_ui_approved") is not False:
        errors.append("REC-EV-011 must remain offline/non-public")

    rec4b = load_json(REPOSITORY_ROOT / rows["REC-EV-004B"]["manifest"])
    metrics = rec4b.get("metrics", {})
    popularity = metrics.get("POPULARITY", {}).get("ndcg_at_10")
    explore = metrics.get("EXPLORE_05_ON_POPULARITY", {}).get("ndcg_at_10")
    if not isinstance(popularity, (int, float)) or not isinstance(explore, (int, float)):
        errors.append("REC-EV-004B metrics missing")
    else:
        relative_loss = (popularity - explore) / popularity
        if abs(relative_loss - 0.455) > 0.002:
            errors.append("REC-EV-004B Explore05 rejection loss drift")
    if rec4b.get("conclusion", {}).get("ranking_champion") is not None:
        errors.append("REC-EV-004B must not declare a champion")

    rec3c = load_json(REPOSITORY_ROOT / rows["REC-EV-003C"]["manifest"])
    decision = rec3c.get("decision", {})
    if decision.get("selected") != "STAR_DISABLED_FAIL_CLOSED" or decision.get("expected_star_ui_approved") is not False:
        errors.append("REC-EV-003C star-disabled decision drift")

    validate_rec_ev_013(rows["REC-EV-013"], errors)


def resolve_repository_path(raw: str) -> Path:
    normalized = raw.replace("\\", "/")
    return (REPOSITORY_ROOT / normalized).resolve()


def validate_rec_ev_013(row: dict[str, Any], errors: list[str]) -> None:
    expected = row.get("expected_files", {})
    paths = {name: resolve_repository_path(str(value)) for name, value in expected.items()}
    status = row.get("status")
    final_markers = {"evidence", "manifest", "evaluation_result"}
    final_exists = any(paths.get(name, Path("__missing__")).is_file() for name in final_markers)
    if status == "IN_PROGRESS":
        if final_exists:
            errors.append(
                "REC-EV-013 final artifact detected: set dependency status to "
                "COMPLETED_EVIDENCE_AVAILABLE and provide every expected file"
            )
        return
    if status != "COMPLETED_EVIDENCE_AVAILABLE":
        errors.append("REC-EV-013 status must be IN_PROGRESS or COMPLETED_EVIDENCE_AVAILABLE")
        return
    missing = {name for name, path in paths.items() if not path.is_file()}
    if missing:
        errors.append(f"REC-EV-013 complete status missing files {sorted(missing)}")
        return
    manifest = load_json(paths["manifest"])
    if manifest.get("evidence_id") != "REC-EV-013":
        errors.append("REC-EV-013 manifest evidence_id drift")
    protocol = manifest.get("protocol", {})
    if protocol.get("base_k") != 10 or protocol.get("base_alpha") != 0.2:
        errors.append("REC-EV-013 must remain based on K10 alpha 0.2")
    if protocol.get("selected") is not None:
        errors.append("REC-EV-013 protocol must record selected:null")
    validation = manifest.get("validation", {})
    if validation.get("positive_injection") is not False:
        errors.append("REC-EV-013 positive injection must be false")
    if manifest.get("product_approved") is not False:
        errors.append("REC-EV-013 artifact cannot approve the product policy")
    if manifest.get("two_plus_one") is not None or manifest.get("discovery_policy") is not None:
        errors.append("REC-EV-013 evidence must not self-approve 2+1/discovery")
    evaluation = load_json(paths["evaluation_result"])
    if (
        evaluation.get("selected") is not None
        or evaluation.get("two_plus_one") is not None
        or evaluation.get("discovery_policy") is not None
    ):
        errors.append("REC-EV-013 evaluation must preserve the no-feasible-policy result")
    failure_cases = evaluation.get("failure_cases", [])
    minimum_losses = [
        case.get("minimum_selection_relative_loss")
        for case in failure_cases
        if isinstance(case, dict)
        and case.get("code") == "SELECTION_RELEVANCE_BUDGET_FAILED"
    ]
    if (
        len(minimum_losses) != 1
        or not isinstance(minimum_losses[0], (int, float))
        or abs(minimum_losses[0] - 0.285714) > 0.000001
    ):
        errors.append("REC-EV-013 minimum selection relative loss must remain 0.285714")
    for artifact in manifest.get("artifacts", {}).values():
        if not isinstance(artifact, dict) or not isinstance(artifact.get("path"), str):
            errors.append("REC-EV-013 artifact descriptor invalid")
            continue
        artifact_path = resolve_repository_path(artifact["path"])
        if not artifact_path.is_file() or sha256(artifact_path) != artifact.get("sha256"):
            errors.append("REC-EV-013 artifact checksum mismatch")


def validate_implementation_boundary(errors: list[str]) -> None:
    public_openapi = (REPOSITORY_ROOT / "docs/api/openapi.yaml").read_text(encoding="utf-8-sig")
    for operation in LOCAL_OPERATIONS:
        if operation not in public_openapi:
            errors.append(f"main OpenAPI missing authorized local operation {operation}")
    for operation in BLOCKED_OPERATIONS:
        if operation in public_openapi:
            errors.append(f"main OpenAPI contains blocked extension {operation}")
    public_document = yaml.safe_load(public_openapi)
    all_public_schemas = public_document.get("components", {}).get("schemas", {})
    c2b_schema_names = {
        "RecommendationDelivery", "RecommendationPageInfo", "PersonalDiscoveryDeliveryItem",
        "PersonalDiscoveryMovieCard",
        "AppendRecommendationsRequest", "PersonalDiscoverySafeIssue", "SelectionSummary",
        "RecommendationAppend", "RecommendationAppendCreated", "RecommendationAppendReplay",
        "DismissRecommendationRequest", "RecommendationDismissal",
        "RecommendationDismissalCreated", "RecommendationDismissalReplay",
    }
    public_properties = collect_property_names({name: all_public_schemas.get(name, {}) for name in c2b_schema_names})
    forbidden_public_fields = {"personalization", "expectedStar", "reasons", "exposureStatus", "recommendationItemId"}
    if forbidden_public_fields & public_properties:
        errors.append(f"main OpenAPI exposes blocked C2B fields {sorted(forbidden_public_fields & public_properties)}")
    required_wire_fields = {
        "RecommendationDelivery": {"deliveryId", "deliveryRevision", "label", "composition", "items", "pageInfo"},
        "RecommendationPageInfo": {"activeItemCount", "hasMore", "nextCursor", "cursorExpiresAt"},
        "PersonalDiscoveryDeliveryItem": {"deliveryItemId", "position", "sourceRank", "recommendationType", "movie"},
        "PersonalDiscoveryMovieCard": {"movieId", "title", "posterUrl", "releaseYear", "genres"},
        "AppendRecommendationsRequest": {"appendEventId", "expectedRevision", "cursor"},
        "SelectionSummary": {"scannedCount", "selectedCount", "excludedCount"},
        "RecommendationAppend": {"appendEventId", "deliveryId", "deliveryRevision", "outcome", "selectionSummary", "appendedItems", "issues", "pageInfo", "replayed"},
        "DismissRecommendationRequest": {"dismissalEventId", "expectedRevision", "reason"},
        "RecommendationDismissal": {"dismissalEventId", "deliveryItemId", "deliveryRevision", "status", "occurredAt", "replayed"},
    }
    for schema_name, expected_required in required_wire_fields.items():
        schema = all_public_schemas.get(schema_name, {})
        if set(schema.get("required", [])) != expected_required:
            errors.append(f"main OpenAPI {schema_name} required wire fields drift")
    if all_public_schemas.get("RecommendationDelivery", {}).get("properties", {}).get("label", {}).get("enum") != ["POPULARITY_BASELINE"]:
        errors.append("main OpenAPI local label must remain POPULARITY_BASELINE")
    if all_public_schemas.get("PersonalDiscoveryDeliveryItem", {}).get("properties", {}).get("movie", {}).get("$ref") != "#/components/schemas/PersonalDiscoveryMovieCard":
        errors.append("main OpenAPI must use the C2B simple movie card")
    public_operations = {
        operation.get("operationId"): operation
        for path_item in public_document.get("paths", {}).values()
        for method, operation in path_item.items()
        if method.lower() in {"get", "post", "put", "patch", "delete"}
    }
    for operation_id in LOCAL_OPERATIONS:
        operation = public_operations.get(operation_id, {})
        if operation.get("x-implementation-status") != "AUTHORIZED_LOCAL_CONTRACT" or operation.get("x-production-activation") is not False:
            errors.append(f"main OpenAPI {operation_id} local-only authority drift")
    main_cache_header = (
        public_operations.get("getMyPersonalDiscoveryRecommendations", {})
        .get("responses", {}).get("200", {}).get("headers", {}).get("Cache-Control", {})
    )
    if cache_control_tokens(main_cache_header.get("example")) != {"private", "no-store"}:
        errors.append("main OpenAPI Cache-Control must require private and no-store independent of order")
    source_text = ""
    for root_name, suffixes in (("backend/src", {".java"}), ("frontend/src", {".ts", ".tsx"})):
        root = REPOSITORY_ROOT / root_name
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in suffixes:
                source_text += path.read_text(encoding="utf-8-sig", errors="ignore")
    for operation in BLOCKED_OPERATIONS:
        if operation in source_text:
            errors.append(f"runtime source prematurely implements blocked extension {operation}")


def main() -> int:
    errors: list[str] = []
    found_files = {
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    if EXPECTED_FILES - found_files:
        errors.append(f"missing contract files {sorted(EXPECTED_FILES - found_files)}")
    validate_status_and_ids(errors)
    validate_openapi(errors)
    validate_audit_semantics(errors)
    validate_tasks(errors)
    validate_trace(errors)
    validate_fixed_evidence(errors)
    validate_implementation_boundary(errors)

    serialized = "\n".join(read(path) for path in sorted(EXPECTED_FILES) if (ROOT / path).is_file())
    secret_patterns = (
        r"eyJhbGciOiJ[A-Za-z0-9_-]{20,}",
        r"AKIA[0-9A-Z]{16}",
        r"gh[pousr]_[A-Za-z0-9]{30,}",
        r"glpat-[A-Za-z0-9_-]{20,}",
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    )
    if any(re.search(pattern, serialized) for pattern in secret_patterns):
        errors.append("C2B contract contains a secret-like value")

    if errors:
        print("C2B contract validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(
        "C2B contract validation passed: "
        f"1/{len(DECISIONS)} product decisions approved, {len(LOCAL_OPERATIONS)} local operations authorized, "
        f"{len(BLOCKED_OPERATIONS)} extensions blocked, "
        f"{len(BUSINESS_RULES)} rules, {len(SCREENS)} screens, "
        f"{len(ACCEPTANCE)} acceptance criteria, {len(TASKS)} gated tasks; "
        "REC-EV-013 v1 rejection is checksum-verified and production activation remains forbidden."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
