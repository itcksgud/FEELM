#!/usr/bin/env python3
"""Fail closed while C5 is only a product-decision inventory."""

from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent
REPOSITORY = ROOT.parents[1]
STATUS = "DRAFT_DECISION_INVENTORY"
DECISIONS = {f"DN-C5-{number:03d}" for number in range(1, 7)}
RECOMMENDATIONS = {
    "DN-C5-001": "C5_REPORT=CALENDAR_HALF_KST_IMMUTABLE_REVISION_V1",
    "DN-C5-002": "C5_EXPORT=ACCESSIBLE_PDF_ASYNC_24H_V1",
    "DN-C5-003": "C5_PRIVACY=PRIVATE_RESOURCE_OPT_IN_V1",
    "DN-C5-004": "C5_SHARE=IMMUTABLE_REPORT_FRAGMENT_EXCHANGE_1CALMONTH_V1",
    "DN-C5-005": "C5_NOTIFICATION=IN_APP_PROVIDERLESS_OPT_IN_V1",
    "DN-C5-006": "C5_ACCOUNT_LIFECYCLE=DEFER_UNTIL_C4_APPROVED",
}
FIXED = {f"FIX-C5-{number:03d}" for number in range(1, 9)}
SAFE = {f"SAFE-C5-{number:03d}" for number in range(1, 9)}
EXPECTED_FILES = {
    "README.md",
    "00-product-scope.md",
    "decision-needed.md",
    "product-decision-packet.md",
    "validate_inventory.py",
}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def ids(pattern: str, value: str) -> set[str]:
    return set(re.findall(pattern, value))


def packet_section(packet: str, decision_id: str) -> str:
    match = re.search(
        rf"(?ms)^## `{re.escape(decision_id)}`.*?(?=^## `DN-C5-\d{{3}}`|^## 승인 응답 형식\b)",
        packet,
    )
    return match.group(0) if match else ""


def main() -> int:
    if "APPROVED_LOCAL_MVP_PROFILE" in read(ROOT / "README.md"):
        from validate_local_contract import main as validate_local_contract
        return validate_local_contract()
    errors: list[str] = []
    files = {path.relative_to(ROOT).as_posix() for path in ROOT.rglob("*") if path.is_file()}
    if files != EXPECTED_FILES:
        errors.append(f"inventory file set drift: expected {sorted(EXPECTED_FILES)}, found {sorted(files)}")

    documents = {
        name: read(ROOT / name)
        for name in EXPECTED_FILES
        if name.endswith(".md") and (ROOT / name).exists()
    }
    for name, value in documents.items():
        if STATUS not in value:
            errors.append(f"{name} missing {STATUS}")

    scope = documents.get("00-product-scope.md", "")
    tracker = documents.get("decision-needed.md", "")
    packet = documents.get("product-decision-packet.md", "")
    readme = documents.get("README.md", "")

    if ids(r"FIX-C5-\d{3}", scope) != FIXED:
        errors.append("fixed requirement inventory drift")
    if ids(r"SAFE-C5-\d{3}", scope) != SAFE:
        errors.append("fixed safety inventory drift")
    for line in scope.splitlines():
        if re.search(r"`FIX-C5-\d{3}`", line) and any(word in line for word in ("미정", "후보", "proposed")):
            errors.append(f"fixed meaning row mixes unresolved language: {line}")
    if ids(r"DN-C5-\d{3}", tracker) != DECISIONS:
        errors.append("decision tracker IDs drift")
    if ids(r"DN-C5-\d{3}", packet) != DECISIONS:
        errors.append("decision packet IDs drift")
    for decision_id in DECISIONS:
        row = next((line for line in tracker.splitlines() if f"`{decision_id}`" in line), "")
        if "`REQUIRES_DECISION`" not in row:
            errors.append(f"{decision_id} must remain REQUIRES_DECISION")
        if f"## `{decision_id}`" not in packet:
            errors.append(f"{decision_id} missing packet section")
        if len(re.findall(rf"(?m)^## `{re.escape(decision_id)}`", packet)) != 1:
            errors.append(f"{decision_id} must have exactly one packet section")
        recommendation = RECOMMENDATIONS[decision_id]
        if tracker.count(recommendation) != 1:
            errors.append(f"{decision_id} tracker recommendation token drift")
        if packet.count(recommendation) != 3:
            errors.append(
                f"{decision_id} recommendation must appear in summary, section, and approval template"
            )
        section = packet_section(packet, decision_id)
        if f"**권장 선택:** `{recommendation}`" not in section:
            errors.append(f"{decision_id} missing exact recommended selection")
        for marker in ("source-of-truth", "rollback", "retention", "**DEFER:**"):
            if marker not in section:
                errors.append(f"{decision_id} missing executable boundary {marker!r}")
    if ids(r"FIX-C5-\d{3}", tracker + packet):
        errors.append("fixed IDs must not be duplicated into unresolved decision documents")

    # The packet is decision-ready, not approved. A changed ratio or authority must fail closed.
    status_markers = {
        "README.md": (
            "결정 패킷: `READY_FOR_PRODUCT_DECISION`",
            "제품 결정: `0/6`",
            "구현 권위: `NO`",
        ),
        "00-product-scope.md": ("구현 권위: `NO`",),
        "decision-needed.md": (
            "결정 준비도: `READY_FOR_PRODUCT_DECISION`",
            "결정 상태: `0/6`",
            "구현 권위: `NO`",
        ),
        "product-decision-packet.md": (
            "결정 준비도: `READY_FOR_PRODUCT_DECISION`",
            "제품 결정: `0/6` (`PENDING_PRODUCT_OWNER`)",
            "구현 권위: `NO`",
            "API/ERD/Acceptance: `FORBIDDEN_BEFORE_DECISIONS`",
        ),
    }
    for name, markers in status_markers.items():
        for marker in markers:
            if marker not in documents.get(name, ""):
                errors.append(f"{name} missing fail-closed status marker {marker!r}")
    for name, value in documents.items():
        ratios = re.findall(r"(?:제품 결정|결정 상태):?[^\n]*?([0-6])/6", value)
        if any(ratio != "0" for ratio in ratios):
            errors.append(f"{name} prematurely changes the 0/6 approval ratio")

    required_markers = {
        "README.md": [
            "API/ERD/Acceptance: `FORBIDDEN_BEFORE_DECISIONS`",
            "C0 Catalog `APPROVED`",
            "C1 Rating·Film `APPROVED`",
            "C4 Membership `DRAFT_EXECUTABLE_AFTER_DECISIONS`",
            "C2B Recommendation `DRAFT_BLOCKED_BY_PRODUCT_AND_EVIDENCE`",
        ],
        "00-product-scope.md": [
            "반기",
            "1개월",
            "hash만 저장",
            "Cache-Control: no-store",
            "Referrer-Policy: no-referrer",
            "전체 active Frame 모음",
        ],
        "decision-needed.md": ["결정 상태: `0/6`", "privacy", "notification", "account"],
        "product-decision-packet.md": [
            "READY_FOR_PRODUCT_DECISION",
            "PENDING_PRODUCT_OWNER",
            "대표 영화만 반환하지 않음",
            "실제 영화 **전체 목록**",
            "SATISFACTION=NOT_COMPUTED",
            "TASTE_DIAGNOSIS=NOT_COMPUTED",
            "EXPECTED_STAR=NOT_COMPUTED",
            "C5_PRIVACY=PRIVATE_RESOURCE_OPT_IN_V1",
            "EXTERNAL_NOTIFICATION_PROVIDER=NONE",
            "C5_ACCOUNT_LIFECYCLE=DEFER_UNTIL_C4_APPROVED",
        ],
    }
    for name, markers in required_markers.items():
        for marker in markers:
            if marker not in documents.get(name, ""):
                errors.append(f"{name} missing boundary marker {marker!r}")

    forbidden_status = re.compile(r"(?im)^>.*(?:상태|제품 결정|구현 권위).*\bAPPROVED\b")
    for name, value in documents.items():
        if forbidden_status.search(value):
            errors.append(f"{name} prematurely claims APPROVED status")
    forbidden_contract_names = {"api", "data", "ui", "testing", "tasks", "traceability"}
    if any((ROOT / name).exists() for name in forbidden_contract_names):
        errors.append("API/ERD/UI/AC/task contract directories are forbidden before decisions")

    # Unsupported product claims must remain explicitly uncomputed.
    unsupported_tokens = {
        "SATISFACTION": "NOT_COMPUTED",
        "TASTE_DIAGNOSIS": "NOT_COMPUTED",
        "EXPECTED_STAR": "NOT_COMPUTED",
    }
    for token, expected in unsupported_tokens.items():
        values = set(re.findall(rf"\b{token}=([A-Z0-9_]+)", packet))
        if values != {expected}:
            errors.append(f"{token} must remain exactly {expected}, found {sorted(values)}")

    executable_details = {
        "DN-C5-001": (
            "REPORT_TIMEZONE=Asia/Seoul",
            "EMPTY_NO_ACTIVITY",
            "SUPERSEDED",
            "`400d`",
            "실제 영화 전체 `periodItems`",
        ),
        "DN-C5-002": (
            "`QUEUED`",
            "`FAILED_FINAL`",
            "`24h`",
            "최대 3회",
            "OBJECT_STORAGE=SERVICE_MANAGED_ENCRYPTED_AT_REST",
        ),
        "DN-C5-003": (
            "모든 계정과 모든 resource가 `PRIVATE`",
            "`PROFILE`, `FILM`, `POPCORN`, `TASTE_COMPARE`",
            "TTL 최대 `30s`",
            "`60s` 이내 fail-closed",
        ),
        "DN-C5-004": (
            "CSPRNG 256-bit",
            "SHA-256 hash와 key version",
            "calendar `plusMonths(1)`",
            "최대 3개",
            "`30d`",
            "`180d`",
        ),
        "DN-C5-005": (
            "EXTERNAL_NOTIFICATION_PROVIDER=NONE",
            "`OFF`",
            "`WATCH_CONFIRMATION_DUE`",
            "UNREAD 최대 `30d`",
            "최대 5회",
        ),
        "DN-C5-006": (
            "`BLOCKED_BY_C4`",
            "mutation `0건`",
            "`NOT_APPLICABLE_WHILE_DEFERRED`",
        ),
    }
    for decision_id, markers in executable_details.items():
        section = packet_section(packet, decision_id)
        for marker in markers:
            if marker not in section:
                errors.append(f"{decision_id} missing concrete recommendation {marker!r}")

    docs_index = read(REPOSITORY / "docs" / "README.md")
    if "./c5-report-profile/README.md" not in docs_index or STATUS not in docs_index:
        errors.append("docs index does not expose C5 decision inventory")
    gates = yaml.safe_load(read(REPOSITORY / "docs" / "planning" / "project-completion-gates.yaml"))
    gate = next((row for row in gates.get("gates", []) if row.get("id") == "GATE-C5-REPORT_PROFILE"), None)
    if not gate or gate.get("status") != STATUS:
        errors.append("GATE-C5-REPORT_PROFILE must remain DRAFT_DECISION_INVENTORY")
    expected_evidence = {
        "docs/c5-report-profile/README.md",
        "docs/c5-report-profile/decision-needed.md",
        "docs/c5-report-profile/product-decision-packet.md",
    }
    if not gate or not expected_evidence <= set(gate.get("evidence", [])):
        errors.append("C5 completion gate evidence links drift")

    all_text = "\n".join(documents.values())
    secret_patterns = (
        re.compile(r"eyJ[A-Za-z0-9_-]{20,}"),
        re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
        re.compile(r"(?i)(?:api[_-]?key|client[_-]?secret|smtp[_-]?password)\s*[:=]\s*\S+"),
    )
    for pattern in secret_patterns:
        if pattern.search(all_text):
            errors.append(f"possible raw secret in C5 inventory: {pattern.pattern}")

    if errors:
        print("C5 decision inventory validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(
        "C5 decision inventory validation passed: "
        "8 fixed meanings, 8 safety boundaries, 6 unresolved decisions; "
        "API/ERD/AC and implementation remain forbidden."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
