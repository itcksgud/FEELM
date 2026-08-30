#!/usr/bin/env python3
"""Validate the approved loopback-only C3 MVP contract without widening production authority."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = ROOT.parents[1]
EXPECTED_OPERATIONS = {
    "createOttCatalogComparison",
    "getOttCatalogComparison",
    "listOttCatalogComparisonMovies",
    "listMyParties",
    "createParty",
    "getParty",
    "listPartyInvitations",
    "createPartyInvitation",
    "listMyPartyInvitations",
    "acceptPartyInvitation",
    "listPartyBaselineRecommendations",
}
EXPECTED_AC = {f"AC-C3-{number:03d}" for number in range(1, 33)}
EXPECTED_SCREENS = {f"SCR-C3-{number:03d}" for number in range(1, 9)}
REQUIRED_FILES = {
    "README.md",
    "00-product-scope.md",
    "decision-needed.md",
    "product-decision-packet.md",
    "01-glossary-and-policies.md",
    "02-business-rules.md",
    "03-state-machines.md",
    "api/openapi.fragment.yaml",
    "data/logical-erd.md",
    "data/data-dictionary.md",
    "ui/navigation-map.md",
    "ui/screen-contracts.md",
    "testing/acceptance-tests.md",
    "testing/fixtures.md",
    "tasks/implementation-backlog.yaml",
    "traceability/requirements.csv",
    "validate_product_decision_packet.py",
}


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8-sig")


def split_refs(value: str) -> set[str]:
    return {item for item in value.split("|") if item}


def operations(document: dict) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for path_item in document.get("paths", {}).values():
        for method, operation in path_item.items():
            if method.lower() in {"get", "post", "put", "patch", "delete"} and isinstance(operation, dict):
                operation_id = operation.get("operationId")
                if operation_id:
                    result[str(operation_id)] = operation
    return result


def main() -> int:
    errors: list[str] = []
    present = {
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    missing = REQUIRED_FILES - present
    if missing:
        errors.append(f"missing files: {sorted(missing)}")

    for path in ROOT.rglob("*.md"):
        if "상태: `APPROVED`" not in path.read_text(encoding="utf-8-sig"):
            errors.append(f"{path.relative_to(ROOT)} is not APPROVED")

    decision = read("decision-needed.md")
    if "local MVP 승인: `4/5`; production 승인: `0/5`" not in decision:
        errors.append("decision approval boundary drift")
    for number in range(1, 5):
        row = next((line for line in decision.splitlines() if f"`DN-C3-{number:03d}`" in line), "")
        if "`APPROVED_LOCAL_MVP`" not in row:
            errors.append(f"DN-C3-{number:03d} is not approved for local MVP")
    deferred = next((line for line in decision.splitlines() if "`DN-C3-005`" in line), "")
    if "`DEFERRED`" not in deferred:
        errors.append("DN-C3-005 must remain deferred")

    document = yaml.safe_load(read("api/openapi.fragment.yaml"))
    if document.get("x-contract-status") != "APPROVED_LOCAL_MVP_ONLY":
        errors.append("OpenAPI local status drift")
    if document.get("x-main-openapi-merged") is not True:
        errors.append("fragment must record completed main merge")
    servers = [str(row.get("url", "")) for row in document.get("servers", [])]
    if not servers or any(not (url.startswith("http://127.0.0.1") or url.startswith("http://localhost")) for url in servers):
        errors.append("every C3 server must be loopback")
    scheme = document.get("components", {}).get("securitySchemes", {}).get("localActor", {})
    if scheme.get("type") != "apiKey" or scheme.get("name") != "X-Local-Actor-Id":
        errors.append("localActor security scheme drift")

    found_operations = operations(document)
    if set(found_operations) != EXPECTED_OPERATIONS:
        errors.append(f"operation set drift: {sorted(set(found_operations) ^ EXPECTED_OPERATIONS)}")
    for operation_id, operation in found_operations.items():
        if operation.get("x-implementation-status") != "READY_LOCAL_MVP":
            errors.append(f"{operation_id} is not READY_LOCAL_MVP")

    schemas = document.get("components", {}).get("schemas", {})
    provider_set = schemas.get("ProviderIdSet", {})
    if (provider_set.get("minItems"), provider_set.get("maxItems"), provider_set.get("uniqueItems")) != (2, 4, True):
        errors.append("ProviderIdSet must be unique 2..4")
    party = schemas.get("Party", {}).get("properties", {})
    if party.get("maximumMemberCount", {}).get("const") != 4:
        errors.append("Party maximum must be 4")
    invite_states = set(schemas.get("PartyInvitation", {}).get("properties", {}).get("status", {}).get("enum", []))
    if invite_states != {"PENDING", "ACCEPTED"}:
        errors.append(f"local invitation states drift: {sorted(invite_states)}")
    explanation = schemas.get("PartyBaselineExplanation", {}).get("properties", {})
    if set(explanation) != {"availableProviderCount", "selectedProviderCount", "catalogPopularityRank", "policyVersion"}:
        errors.append("Party baseline explanation field drift")
    if explanation.get("policyVersion", {}).get("const") != "CATALOG_POPULARITY_KR_FLATRATE_V1":
        errors.append("Party baseline policy version drift")

    serialized = json.dumps(schemas, ensure_ascii=False).lower()
    forbidden_schema_terms = (
        "expectedstar", "meanutility", "minimumutility", "tasteDifference".lower(),
        "satisfaction", "fairness", "exposurecount", "detailviewcount", "ottclickcount",
    )
    for term in forbidden_schema_terms:
        if term in serialized:
            errors.append(f"forbidden estimation/behavior schema term: {term}")

    acceptance = set(re.findall(r"AC-C3-\d{3}", read("testing/acceptance-tests.md")))
    if acceptance != EXPECTED_AC:
        errors.append(f"acceptance set drift: {sorted(acceptance ^ EXPECTED_AC)}")
    screens = set(re.findall(r"SCR-C3-\d{3}", read("00-product-scope.md") + read("ui/screen-contracts.md")))
    if screens != EXPECTED_SCREENS:
        errors.append(f"screen set drift: {sorted(screens ^ EXPECTED_SCREENS)}")
    if "`SCR-C3-007` Party taste analysis" not in read("00-product-scope.md"):
        errors.append("SCR-C3-007 must remain explicitly excluded")

    tasks = yaml.safe_load(read("tasks/implementation-backlog.yaml"))
    if tasks.get("status") != "APPROVED_LOCAL_MVP_ONLY":
        errors.append("task contract status drift")
    task_rows = tasks.get("tasks", [])
    task_ids = {str(row.get("id")) for row in task_rows}
    task_operations = {str(op) for row in task_rows for op in row.get("operation_ids", [])}
    if task_operations != EXPECTED_OPERATIONS:
        errors.append(f"task operation coverage drift: {sorted(task_operations ^ EXPECTED_OPERATIONS)}")
    for row in task_rows:
        if row.get("id") == "TASK-C3-PROD-001" and row.get("status") != "BLOCKED":
            errors.append("production task must stay BLOCKED")

    with (ROOT / "traceability/requirements.csv").open(encoding="utf-8-sig", newline="") as handle:
        trace_rows = list(csv.DictReader(handle))
    traced_operations: set[str] = set()
    traced_acceptance: set[str] = set()
    for index, row in enumerate(trace_rows, start=2):
        row_operations = split_refs(row["operation_ids"])
        row_acceptance = split_refs(row["acceptance_ids"])
        row_tasks = split_refs(row["task_ids"])
        unknown_ops = row_operations - EXPECTED_OPERATIONS
        unknown_ac = row_acceptance - EXPECTED_AC
        unknown_tasks = row_tasks - task_ids
        if unknown_ops or unknown_ac or unknown_tasks:
            errors.append(f"trace line {index} unknown refs ops={sorted(unknown_ops)} ac={sorted(unknown_ac)} tasks={sorted(unknown_tasks)}")
        traced_operations.update(row_operations)
        traced_acceptance.update(row_acceptance)
    if traced_operations != EXPECTED_OPERATIONS:
        errors.append(f"trace operation coverage drift: {sorted(traced_operations ^ EXPECTED_OPERATIONS)}")
    if traced_acceptance != EXPECTED_AC:
        errors.append(f"trace AC coverage drift: {sorted(traced_acceptance ^ EXPECTED_AC)}")

    main_path = REPOSITORY_ROOT / "docs/api/openapi.yaml"
    if main_path.exists():
        main_document = yaml.safe_load(main_path.read_text(encoding="utf-8-sig"))
        main_operations = operations(main_document)
        merged = EXPECTED_OPERATIONS & set(main_operations)
        if merged != EXPECTED_OPERATIONS:
            errors.append(f"main OpenAPI missing C3 operations: {sorted(EXPECTED_OPERATIONS - merged)}")
        for operation_id in EXPECTED_OPERATIONS:
            if main_operations.get(operation_id, {}).get("security") != [{"localActor": []}]:
                errors.append(f"main OpenAPI {operation_id} must use localActor only")
        main_local_actor = main_document.get("components", {}).get("securitySchemes", {}).get("localActor", {})
        if main_local_actor.get("name") != "X-Local-Actor-Id":
            errors.append("main OpenAPI localActor scheme drift")
        main_schemas = main_document.get("components", {}).get("schemas", {})
        main_c3 = json.dumps({key: value for key, value in main_schemas.items() if key.startswith("C3")}, ensure_ascii=False).lower()
        for term in forbidden_schema_terms:
            if term in main_c3:
                errors.append(f"forbidden term leaked into main C3 schemas: {term}")

    all_text = "\n".join(
        path.read_text(encoding="utf-8-sig")
        for path in ROOT.rglob("*")
        if path.is_file() and path.suffix in {".md", ".yaml", ".csv", ".py"}
    )
    for pattern in (
        re.compile(r"eyJ[A-Za-z0-9_-]{20,}"),
        re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
        re.compile(r"(?im)^\s*(?:CLIENT_SECRET|API_KEY|PASSWORD)\s*[:=]\s*\S+"),
    ):
        if pattern.search(all_text):
            errors.append(f"possible raw secret: {pattern.pattern}")

    if errors:
        print("C3 local MVP contract validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(
        f"C3 local MVP contract validation passed: {len(EXPECTED_OPERATIONS)} operations, "
        f"{len(EXPECTED_AC)} AC, Party max 4, deterministic catalog baseline; production remains blocked."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
