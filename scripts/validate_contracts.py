#!/usr/bin/env python3
"""Validate FEELM contract ID references without external Python packages."""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def ids(pattern: str, text: str) -> set[str]:
    return set(re.findall(pattern, text, flags=re.MULTILINE))


def split_refs(value: str) -> set[str]:
    return {item.strip() for item in value.split("|") if item.strip()}


def validate_slice_registry(errors: list[str]) -> dict[str, object]:
    registry_path = "docs/spec/approved-slices.json"
    try:
        registry = json.loads(read(registry_path))
    except (json.JSONDecodeError, OSError) as exception:
        errors.append(f"{registry_path}: cannot parse registry: {exception}")
        return {}

    if registry.get("schemaVersion") != 1:
        errors.append(f"{registry_path}: schemaVersion must be 1")
    if registry.get("authority") != registry_path:
        errors.append(f"{registry_path}: authority must point to itself")

    public_slices = {
        item.get("sliceId"): item for item in registry.get("publicProductSlices", [])
    }
    if set(public_slices) != {"C0_CATALOG", "C1_RATING_FILM", "C2B_LOCAL_BASELINE_DISCOVERY"}:
        errors.append(
            f"{registry_path}: public/local slices must be exactly C0, C1 and C2B local baseline"
        )

    expected_public = {
        "C0_CATALOG": ("APPROVED", "BASE", "docs"),
        "C1_RATING_FILM": ("APPROVED", "EXTENSION", "docs/c1-draft"),
        "C2B_LOCAL_BASELINE_DISCOVERY": (
            "APPROVED_LOCAL_BASELINE_WITH_BLOCKED_EXTENSIONS",
            "LOCAL_BASELINE_EXTENSION",
            "docs/c2b-personal-discovery",
        ),
    }
    for slice_id, (status, mode, root) in expected_public.items():
        item = public_slices.get(slice_id, {})
        if (item.get("status"), item.get("contractMode"), item.get("root")) != (
            status,
            mode,
            root,
        ):
            errors.append(f"{registry_path}: {slice_id} status/mode/root conflict")
        if slice_id == "C1_RATING_FILM" and item.get("stablePath") is not True:
            errors.append(f"{registry_path}: C1_RATING_FILM must preserve stablePath")
        if slice_id == "C2B_LOCAL_BASELINE_DISCOVERY" and item.get("productionActivation") is not False:
            errors.append(f"{registry_path}: C2B production activation must remain false")
        contracts = item.get("contracts", {})
        required_contracts = {
            "productScope",
            "glossary",
            "businessRules",
            "stateMachines",
            "navigation",
            "screens",
            "dataDictionary",
            "logicalErd",
            "acceptance",
            "fixtures",
            "traceability",
        }
        if set(contracts) != required_contracts:
            errors.append(
                f"{registry_path}: {slice_id} contract keys conflict: {sorted(contracts)}"
            )
        for contract_name, contract_path in contracts.items():
            candidate = ROOT / contract_path
            if not candidate.is_file():
                errors.append(
                    f"{registry_path}: {slice_id}.{contract_name} does not exist: {contract_path}"
                )
                continue
            expected_marker = status
            if candidate.suffix == ".md" and not re.search(
                rf"^> 상태:\s*`{re.escape(expected_marker)}`", read(contract_path), re.MULTILINE
            ):
                errors.append(
                    f"{registry_path}: {slice_id}.{contract_name} is not APPROVED: {contract_path}"
                )

    internal_slices = {
        item.get("sliceId"): item for item in registry.get("internalSlices", [])
    }
    c2 = internal_slices.get("C2A_RECOMMENDATION_INTERNAL")
    if not c2:
        errors.append(f"{registry_path}: C2A internal slice state is missing")
    elif (
        c2.get("status") != "APPROVED_C2A_INTERNAL_POPULARITY_ONLY"
        or c2.get("root") != "docs/c2-recommendation"
        or c2.get("includedInPublicProductAuthority") is not False
    ):
        errors.append(f"{registry_path}: C2A internal status was promoted or changed")
    c2_readme = read("docs/c2-recommendation/README.md")
    if "`APPROVED_C2A_INTERNAL_POPULARITY_ONLY`" not in c2_readme:
        errors.append("docs/c2-recommendation/README.md: C2A registry status conflict")

    bridge_files = (
        "docs/spec/README.md",
        "docs/ui/README.md",
        "docs/data/README.md",
        "docs/testing/README.md",
        "docs/traceability/README.md",
    )
    for bridge in bridge_files:
        text = read(bridge)
        if registry_path not in text or "docs/c1-draft" not in text:
            errors.append(f"{bridge}: C0+C1 authority bridge is missing")

    scope = read("docs/spec/00-product-scope.md")
    if "승인 공개 제품 Slice: C0 Catalog + C1 Rating·Film" not in scope:
        errors.append("docs/spec/00-product-scope.md: C0+C1 public approval marker missing")
    stale_c0_only = (
        "현재 승인된 구현 단위는 첫 단계인 **C0 Catalog**",
        "전체 제품의 Catalog 이후 범위: `DRAFT`",
    )
    for marker in stale_c0_only:
        if marker in scope:
            errors.append(f"docs/spec/00-product-scope.md: stale C0-only marker {marker!r}")

    return registry


def validate_task_states(path: str, task_prefix: str, errors: list[str]) -> set[str]:
    """Check that READY/BLOCKED task states agree with their dependency states."""
    text = read(path)
    task_pattern = re.compile(
        rf"^  - id:\s*({re.escape(task_prefix)}-\d{{3}}[A-Z]?)\s*$"
        rf"(?P<body>.*?)(?=^  - id:|\Z)",
        flags=re.MULTILINE | re.DOTALL,
    )
    tasks: dict[str, dict[str, object]] = {}
    for match in task_pattern.finditer(text):
        body = match.group("body")
        status_match = re.search(r"^    status:\s*(\w+)\s*$", body, re.MULTILINE)
        dependency_match = re.search(r"^    depends_on:\s*\[(.*?)\]\s*$", body, re.MULTILINE)
        if not status_match or not dependency_match:
            errors.append(f"{path}: incomplete state metadata for {match.group(1)}")
            continue
        dependencies = {
            item.strip() for item in dependency_match.group(1).split(",") if item.strip()
        }
        tasks[match.group(1)] = {
            "status": status_match.group(1),
            "dependencies": dependencies,
            "has_blocked_reason": bool(
                re.search(r"^    blocked_reason:\s*\S", body, re.MULTILINE)
            ),
        }

    for task_id, task in tasks.items():
        dependencies = task["dependencies"]
        unknown = dependencies - set(tasks)
        for dependency in sorted(unknown):
            errors.append(f"{path}: {task_id} has unknown dependency {dependency}")
        if unknown:
            continue
        all_done = all(tasks[dependency]["status"] == "DONE" for dependency in dependencies)
        if task["status"] == "READY" and not all_done:
            errors.append(f"{path}: {task_id} is READY before all dependencies are DONE")
        if task["status"] == "BLOCKED" and all_done and not task["has_blocked_reason"]:
            errors.append(
                f"{path}: {task_id} is BLOCKED despite completed dependencies and has no blocked_reason"
            )
    return set(tasks)


def main() -> int:
    errors: list[str] = []
    validate_slice_registry(errors)
    openapi = read("docs/api/openapi.yaml")
    acceptance = read("docs/testing/acceptance-tests.md")
    screens = read("docs/ui/screen-contracts.md")
    decisions = read("docs/decisions/decision-log.md")
    rules = read("docs/spec/02-business-rules.md")
    source_requirements = read("docs/requirements/00-source.md")
    automated_tests = read("docs/testing/automated-tests.md")
    backlog = read("docs/tasks/implementation-backlog.yaml")

    operation_ids = ids(r"^\s+operationId:\s*([A-Za-z0-9_]+)\s*$", openapi)
    acceptance_ids = ids(r"`(AC-CAT-\d{3})`", acceptance)
    screen_ids = ids(r"(SCR-CAT-\d{3})", screens)
    decision_ids = ids(r"`(DEC-CAT-\d{3})`", decisions)
    business_rule_ids = ids(r"`(BR-CAT-\d{3})`", rules)
    requirement_ids = ids(r"\b((?:FR|NFR)-\d{2})\b", source_requirements)
    scenario_ids = ids(r"`(SCN-CAT-\d{3})`", automated_tests)
    test_ids = ids(r"`(TEST-[A-Z0-9-]+)`", automated_tests)
    known_task_ids = ids(r"^\s+- id:\s*(TASK-CAT-\d{3})\s*$", backlog)

    if len(operation_ids) != 32:
        errors.append(f"expected 32 OpenAPI operations, found {len(operation_ids)}")
    if len(acceptance_ids) != 50:
        errors.append(f"expected 50 acceptance IDs, found {len(acceptance_ids)}")
    if len(screen_ids) != 5:
        errors.append(f"expected 5 Catalog screen IDs, found {len(screen_ids)}")

    referenced_operations: set[str] = set()
    referenced_acceptance: set[str] = set()
    matrix_path = ROOT / "docs/traceability/requirements.csv"
    with matrix_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    required_columns = {
        "requirement_id",
        "decision_ids",
        "business_rule_ids",
        "scenario_ids",
        "screen_ids",
        "operation_ids",
        "entities",
        "acceptance_ids",
        "task_ids",
        "test_ids",
        "status",
    }
    if set(rows[0]) != required_columns:
        errors.append(f"traceability columns mismatch: {set(rows[0])}")

    for line_number, row in enumerate(rows, start=2):
        requirement_id = row["requirement_id"]
        if not (
            requirement_id in requirement_ids
            or requirement_id.startswith(("DATA-", "INT-"))
        ):
            errors.append(f"line {line_number}: unknown requirement {requirement_id}")
        if row["status"] != "APPROVED":
            errors.append(f"line {line_number}: status is not APPROVED")

        checks = (
            ("decision", split_refs(row["decision_ids"]), decision_ids),
            ("business rule", split_refs(row["business_rule_ids"]), business_rule_ids),
            ("scenario", split_refs(row["scenario_ids"]), scenario_ids),
            ("screen", split_refs(row["screen_ids"]), screen_ids),
            ("operation", split_refs(row["operation_ids"]), operation_ids),
            ("acceptance", split_refs(row["acceptance_ids"]), acceptance_ids),
            ("task", split_refs(row["task_ids"]), known_task_ids),
            ("test", split_refs(row["test_ids"]), test_ids),
        )
        for label, references, known in checks:
            for missing in sorted(references - known):
                errors.append(f"line {line_number}: unknown {label} {missing}")
        referenced_operations.update(split_refs(row["operation_ids"]))
        referenced_acceptance.update(split_refs(row["acceptance_ids"]))

    c1_matrix_path = ROOT / "docs/c1-draft/traceability/requirements.csv"
    with c1_matrix_path.open(encoding="utf-8-sig", newline="") as handle:
        c1_rows = list(csv.DictReader(handle))
    c1_operations = {
        operation
        for row in c1_rows
        for operation in split_refs(row["operation_ids"])
    }
    c1_acceptance = ids(
        r"`(AC-C1-\d{3})`", read("docs/c1-draft/testing/acceptance-tests.md")
    )
    c1_screens = ids(r"(SCR-C1-\d{3})", read("docs/c1-draft/ui/screen-contracts.md"))
    if len(c1_acceptance) != 59:
        errors.append(f"expected 59 C1 acceptance IDs, found {len(c1_acceptance)}")
    if len(c1_screens) != 8:
        errors.append(f"expected 8 C1 screen IDs, found {len(c1_screens)}")
    for missing in sorted(c1_operations - operation_ids):
        errors.append(f"C1 traced operation is not merged into OpenAPI: {missing}")

    c2b_matrix_path = ROOT / "docs/c2b-personal-discovery/traceability/requirements.csv"
    with c2b_matrix_path.open(encoding="utf-8-sig", newline="") as handle:
        c2b_rows = list(csv.DictReader(handle))
    c2b_local_operations = {
        operation
        for row in c2b_rows
        if row.get("status") == "AUTHORIZED_LOCAL_BASELINE"
        for operation in split_refs(row["operation_ids"])
    }
    expected_c2b_local_operations = {
        "getMyPersonalDiscoveryRecommendations",
        "appendMyPersonalDiscoveryRecommendations",
        "dismissMyRecommendationAsNotInterested",
    }
    if c2b_local_operations != expected_c2b_local_operations:
        errors.append(f"C2B local traced operation drift: {sorted(c2b_local_operations)}")
    for missing in sorted(c2b_local_operations - operation_ids):
        errors.append(f"C2B local operation is not merged into OpenAPI: {missing}")
    for blocked in ("commitMyRecommendationExposure", "recordMyRecommendationAction"):
        if blocked in operation_ids:
            errors.append(f"blocked C2B extension is merged into OpenAPI: {blocked}")

    c3_matrix_path = ROOT / "docs/c3-party-ott-comparison/traceability/requirements.csv"
    with c3_matrix_path.open(encoding="utf-8-sig", newline="") as handle:
        c3_rows = list(csv.DictReader(handle))
    c3_local_operations = {
        operation
        for row in c3_rows
        if row.get("status") in {
            "APPROVED_LOCAL_MVP",
            "APPROVED_LOCAL_MVP_WITH_PRODUCTION_BLOCKS",
            "DEFERRED_PRODUCTION",
        }
        for operation in split_refs(row["operation_ids"])
    }
    expected_c3_local_operations = {
        "createOttCatalogComparison",
        "getOttCatalogComparison",
        "listOttCatalogComparisonMovies",
        "listMyParties",
        "createParty",
        "getParty",
        "createPartyInvitation",
        "listPartyInvitations",
        "listMyPartyInvitations",
        "acceptPartyInvitation",
        "listPartyBaselineRecommendations",
    }
    if c3_local_operations != expected_c3_local_operations:
        errors.append(f"C3 local traced operation drift: {sorted(c3_local_operations)}")
    for missing in sorted(c3_local_operations - operation_ids):
        errors.append(f"C3 local operation is not merged into OpenAPI: {missing}")

    for missing in sorted(
        operation_ids - referenced_operations - c1_operations - c2b_local_operations - c3_local_operations
    ):
        errors.append(f"OpenAPI operation is not traced: {missing}")
    for missing in sorted(acceptance_ids - referenced_acceptance):
        errors.append(f"acceptance ID is not traced: {missing}")

    task_ids = validate_task_states(
        "docs/tasks/implementation-backlog.yaml", "TASK-CAT", errors
    )
    if task_ids != {f"TASK-CAT-{number:03d}" for number in range(1, 14)}:
        errors.append(f"unexpected task IDs: {sorted(task_ids)}")
    validate_task_states(
        "docs/tasks/recommendation-evidence-backlog.yaml", "TASK-REC-EV", errors
    )

    forbidden = ("TODO", "TBD", "미정", "추후 결정")
    approved_contracts = (
        "docs/spec/00-product-scope.md",
        "docs/spec/01-glossary-and-policies.md",
        "docs/spec/02-business-rules.md",
        "docs/spec/03-state-machines.md",
        "docs/ui/screen-contracts.md",
        "docs/data/logical-erd.md",
        "docs/data/data-dictionary.md",
        "docs/data/catalog-ingestion-contract.md",
        "docs/testing/acceptance-tests.md",
    )
    for path in approved_contracts:
        text = read(path)
        for word in forbidden:
            if word in text:
                errors.append(f"{path}: forbidden unresolved marker {word!r}")

    if errors:
        print("Contract validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print(
        "Contract validation passed: "
        f"2 approved product slices plus C2B/C3 local-only slices, {len(operation_ids)} operations, "
        f"{len(screen_ids) + len(c1_screens)} screens, "
        f"{len(acceptance_ids) + len(c1_acceptance)} acceptance tests, "
        f"{len(rows) + len(c1_rows) + len(c2b_rows) + len(c3_rows)} trace rows, "
        f"{len(task_ids)} tasks."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
