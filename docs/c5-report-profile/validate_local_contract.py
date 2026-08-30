from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent

EXPECTED_OPERATIONS = {
    "listMyTasteReports", "getMyTasteReport", "createMyTasteReportRevision",
    "createMyTasteReportExport", "getMyTasteReportExport", "downloadMyTasteReportExport",
    "getMyPrivacySettings", "replaceMyPrivacySettings", "getPublicUserProfile",
    "listPublicUserFilmFrames", "listPublicUserPopcorns", "createMyTasteReportShare",
    "revokeMyTasteReportShare", "exchangeTasteReportShare", "getSharedTasteReport",
    "getMyNotificationSettings", "replaceMyNotificationSettings", "listMyNotifications",
    "updateMyNotificationState",
}
EXPECTED_AC = {f"AC-C5-{index:03d}" for index in range(1, 21)}
EXPECTED_TASKS = {f"TASK-C5-{index:03d}" for index in range(1, 9)}
REQUIRED_TOKENS = {
    "C5_REPORT=CALENDAR_HALF_KST_IMMUTABLE_REVISION_V1",
    "C5_EXPORT=ACCESSIBLE_PDF_ASYNC_24H_V1",
    "C5_PRIVACY=PRIVATE_RESOURCE_OPT_IN_V1",
    "C5_SHARE=IMMUTABLE_REPORT_FRAGMENT_EXCHANGE_1CALMONTH_V1",
    "C5_NOTIFICATION=IN_APP_PROVIDERLESS_OPT_IN_V1",
    "C5_ACCOUNT_LIFECYCLE=DEFER_UNTIL_C4_APPROVED",
}
FORBIDDEN_OPERATIONS = {
    "compareUserTastes", "recoverPassword", "changePassword", "deleteMyAccount",
    "sendEmailNotification", "sendPushNotification",
}
FORBIDDEN_PUBLIC_FIELDS = {
    "expectedStar", "satisfactionScore", "tasteDiagnosis", "tasteComparison",
    "rawEmail", "password", "externalDeliveryId",
}


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def pipe_values(value: str) -> set[str]:
    return {part.strip() for part in value.split("|") if part.strip()}


def main() -> int:
    errors: list[str] = []
    openapi = read("api/openapi.fragment.yaml")
    contract = read("local-contract.md")
    decisions = read("decision-needed.md")
    readme = read("README.md")
    tasks = read("tasks/implementation-backlog.yaml")

    operations = set(re.findall(r"^\s+operationId:\s*([A-Za-z0-9_]+)\s*$", openapi, re.MULTILINE))
    if operations != EXPECTED_OPERATIONS:
        errors.append(f"operation set drift: missing={sorted(EXPECTED_OPERATIONS - operations)} extra={sorted(operations - EXPECTED_OPERATIONS)}")
    for operation in EXPECTED_OPERATIONS:
        match = re.search(rf"operationId:\s*{re.escape(operation)}\b(?P<body>.{{0,240}})", openapi, re.DOTALL)
        if not match or "x-implementation-status: READY_LOCAL_MVP" not in match.group("body"):
            errors.append(f"{operation} is not READY_LOCAL_MVP")
    if FORBIDDEN_OPERATIONS & operations:
        errors.append(f"forbidden operations exposed: {sorted(FORBIDDEN_OPERATIONS & operations)}")
    fragment_document = yaml.safe_load(openapi)
    if fragment_document.get("x-main-openapi-merged") is not True:
        errors.append("C5 local fragment must record main OpenAPI merge")
    main_document = yaml.safe_load((ROOT.parent / "api" / "openapi.yaml").read_text(encoding="utf-8"))
    main_paths = main_document.get("paths", {})
    fragment_paths = fragment_document.get("paths", {})
    for path, path_item in fragment_paths.items():
        ready = [operation for operation in path_item.values() if isinstance(operation, dict) and operation.get("x-implementation-status") == "READY_LOCAL_MVP"]
        if ready:
            expected_ref = f"../c5-report-profile/api/openapi.fragment.yaml#/paths/{path.replace('/', '~1')}"
            if main_paths.get(path) != {"$ref": expected_ref}:
                errors.append(f"main OpenAPI C5 path merge drift: {path}")
    for field in FORBIDDEN_PUBLIC_FIELDS:
        if re.search(rf"\b{re.escape(field)}\b", openapi):
            errors.append(f"forbidden public field exposed: {field}")

    ac_ids = set(re.findall(r"`(AC-C5-\d{3})`", contract))
    if ac_ids != EXPECTED_AC:
        errors.append(f"acceptance set drift: missing={sorted(EXPECTED_AC - ac_ids)} extra={sorted(ac_ids - EXPECTED_AC)}")
    task_ids = set(re.findall(r"\bid:\s*(TASK-C5-\d{3})\b", tasks))
    if task_ids != EXPECTED_TASKS:
        errors.append(f"task set drift: missing={sorted(EXPECTED_TASKS - task_ids)} extra={sorted(task_ids - EXPECTED_TASKS)}")

    for token in REQUIRED_TOKENS:
        if token not in decisions:
            errors.append(f"missing selected local token: {token}")
    for marker in ("APPROVED_LOCAL_MVP_PROFILE", "LOCAL_MVP_ONLY", "production·외부 공개 권위: `NO`"):
        if marker not in readme:
            errors.append(f"README authority marker missing: {marker}")

    trace_operations: set[str] = set()
    trace_acceptance: set[str] = set()
    trace_tasks: set[str] = set()
    with (ROOT / "traceability/requirements.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        errors.append("traceability has no rows")
    for row in rows:
        trace_operations |= pipe_values(row["operation_ids"])
        trace_acceptance |= pipe_values(row["acceptance_ids"])
        trace_tasks |= pipe_values(row["task_ids"])
    if trace_operations != EXPECTED_OPERATIONS:
        errors.append("trace operation coverage drift")
    if trace_acceptance != EXPECTED_AC:
        errors.append("trace acceptance coverage drift")
    if not trace_tasks <= EXPECTED_TASKS or not {"TASK-C5-002", "TASK-C5-003", "TASK-C5-004", "TASK-C5-005", "TASK-C5-006", "TASK-C5-007", "TASK-C5-008"} <= trace_tasks:
        errors.append("trace task coverage drift")

    required_boundaries = (
        "row 없음=PRIVATE", "raw token 저장 금지", "15-minute", "24h",
        "external mail/push/SMS/object storage/network adapter 호출이 0건",
        "account lifecycle와 taste compare route/UI/table이 없다",
    )
    combined = contract + read("data/data-dictionary.md")
    for boundary in required_boundaries:
        if boundary not in combined:
            errors.append(f"security/product boundary missing: {boundary}")

    if errors:
        print("C5 local contract validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("C5 local MVP contract validation passed: 19 operations, 20 AC, factual reports, PRIVATE defaults, hash-only loopback shares, providerless notifications; production remains blocked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
