#!/usr/bin/env python3
"""Validate evidence-safe local approval and production decision blocks for C3."""

from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8-sig")


def main() -> int:
    errors: list[str] = []
    packet = read("product-decision-packet.md")
    decision = read("decision-needed.md")
    openapi = yaml.safe_load(read("api/openapi.fragment.yaml"))
    tasks = yaml.safe_load(read("tasks/implementation-backlog.yaml"))

    required_packet = (
        "APPROVED_LOCAL_MVP",
        "IMPLEMENTATION_AUTHORITY: LOCAL_MVP_ONLY",
        "CATALOG_POPULARITY_KR_FLATRATE_V1",
        "PARTY_CREATE_INVITE_ACCEPT_MAX4",
        "LOOPBACK_ALLOWLIST_FAKE_ACTOR",
        "KR_FLATRATE_COMPLETE_FIXTURE_FULL_LIST",
        "0.69%~1.02%",
        "Average/Balanced",
        "실제 영화 전체",
        "production auth·배포 권위: `NO`; main OpenAPI local operation 병합: `YES`",
    )
    for phrase in required_packet:
        if phrase not in packet:
            errors.append(f"packet missing {phrase!r}")

    if set(re.findall(r"DN-C3-\d{3}", packet)) != {f"DN-C3-{n:03d}" for n in range(1, 6)}:
        errors.append("packet decision set drift")
    if "local MVP 승인: `4/5`; production 승인: `0/5`" not in decision:
        errors.append("decision authority count drift")
    if "DN-C3-005" not in decision or "`DEFERRED`" not in decision:
        errors.append("behavior/taste decision must remain deferred")

    forbidden_claims = (
        "Balanced 개선 입증",
        "Average 제품 champion",
        "실제 만족도 향상",
        "공정성 개선을 보장",
    )
    for claim in forbidden_claims:
        if claim in packet:
            errors.append(f"forbidden evidence claim: {claim}")

    if openapi.get("x-contract-status") != "APPROVED_LOCAL_MVP_ONLY":
        errors.append("OpenAPI status is not local-only approved")
    if openapi.get("x-main-openapi-merged") is not True:
        errors.append("OpenAPI fragment must record the completed main merge")
    if set(openapi.get("x-approved-decisions", [])) != {"DN-C3-001", "DN-C3-002", "DN-C3-003", "DN-C3-004"}:
        errors.append("OpenAPI local decision set drift")
    if openapi.get("x-deferred-decisions") != ["DN-C3-005"]:
        errors.append("OpenAPI deferred decision drift")
    blocked = set(openapi.get("x-blocked-production-capabilities", []))
    required_blocked = {
        "OAUTH_OR_JWT_ACTOR",
        "REAL_NICKNAME_EMAIL_INVITATION",
        "PARTY_MEMBER_REMOVAL_OR_CLOSURE",
        "PARTY_TASTE_ANALYSIS",
        "PERSONALIZED_OR_UTILITY_RECOMMENDATION",
        "LIVE_AVAILABILITY_FRESHNESS_POLICY",
    }
    if blocked != required_blocked:
        errors.append(f"production block set drift: {sorted(blocked ^ required_blocked)}")

    rules = tasks.get("rules", {})
    if not rules.get("production_deployment_forbidden") or not rules.get("local_loopback_only"):
        errors.append("task rules lost local/production boundary")
    prod = next((row for row in tasks.get("tasks", []) if row.get("id") == "TASK-C3-PROD-001"), None)
    if not prod or prod.get("status") != "BLOCKED":
        errors.append("production decision task must remain BLOCKED")

    if errors:
        print("C3 local MVP decision packet validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(
        "C3 decision packet validation passed: four local-only decisions approved; "
        "behavior/personalization and all production authority remain deferred."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
