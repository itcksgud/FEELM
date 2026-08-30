from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APPROVAL = ROOT / "docs" / "planning" / "product-owner-approval-request-20260830.md"

DECISIONS = {
    "DN-C2B-001": ("KEEP_PUBLIC_ALPHA0_SHADOW_K10", "docs/c2b-personal-discovery/product-decision-packet.md"),
    "DN-C2B-002": ("BASELINE_THREE_CUMULATIVE_LOAD_MORE_RATED_OR_EXPLICIT_DISMISS", "docs/c2b-personal-discovery/product-decision-packet.md"),
    "DN-C2B-003": ("MAX_ONE_FAITHFUL_REASON", "docs/c2b-personal-discovery/product-decision-packet.md"),
    "DN-C2B-004": ("STAR_DISABLED_FAIL_CLOSED", "docs/c2b-personal-discovery/product-decision-packet.md"),
    "DN-C2B-005": ("EXACT_STAGE_ONLY_C1_EVENT_AMENDMENT", "docs/c2b-personal-discovery/product-decision-packet.md"),
    "DN-C2B-006": ("NO_STALE_VERSIONED_RETENTION_CANDIDATE", "docs/c2b-personal-discovery/product-decision-packet.md"),
    "DN-C3-001": ("CATALOG_POPULARITY_KR_FLATRATE_V1", "docs/c3-party-ott-comparison/product-decision-packet.md"),
    "DN-C3-002": ("PARTY_CREATE_INVITE_ACCEPT_MAX4", "docs/c3-party-ott-comparison/product-decision-packet.md"),
    "DN-C3-003": ("LOOPBACK_ALLOWLIST_FAKE_ACTOR", "docs/c3-party-ott-comparison/product-decision-packet.md"),
    "DN-C3-004": ("KR_FLATRATE_COMPLETE_FIXTURE_FULL_LIST", "docs/c3-party-ott-comparison/product-decision-packet.md"),
    "DN-C3-005": ("DEFERRED", "docs/c3-party-ott-comparison/product-decision-packet.md"),
    "DN-C4A-001": ("BEARER_JWT_ROTATING_REFRESH_CURRENT_LOGOUT", "docs/c4-membership-onboarding/product-decision-packet.md"),
    "DN-C4A-002": ("MINIMAL_FIELDS_GLOBAL_NICKNAME", "docs/c4-membership-onboarding/product-decision-packet.md"),
    "DN-C4A-003": ("VERIFY_REQUIRED_MAILPIT_LOCAL_PROD_DEFERRED", "docs/c4-membership-onboarding/product-decision-packet.md"),
    "DN-C4A-004": ("OPTIONAL_UP_TO_10_WITH_SKIP", "docs/c4-membership-onboarding/product-decision-packet.md"),
    "DN-C4A-005": ("KEEP_ALL_SOCIAL_DISABLED", "docs/c4-membership-onboarding/product-decision-packet.md"),
    "DN-C5-001": ("C5_REPORT=CALENDAR_HALF_KST_IMMUTABLE_REVISION_V1", "docs/c5-report-profile/product-decision-packet.md"),
    "DN-C5-002": ("C5_EXPORT=ACCESSIBLE_PDF_ASYNC_24H_V1", "docs/c5-report-profile/product-decision-packet.md"),
    "DN-C5-003": ("C5_PRIVACY=PRIVATE_RESOURCE_OPT_IN_V1", "docs/c5-report-profile/product-decision-packet.md"),
    "DN-C5-004": ("C5_SHARE=IMMUTABLE_REPORT_FRAGMENT_EXCHANGE_1CALMONTH_V1", "docs/c5-report-profile/product-decision-packet.md"),
    "DN-C5-005": ("C5_NOTIFICATION=IN_APP_PROVIDERLESS_OPT_IN_V1", "docs/c5-report-profile/product-decision-packet.md"),
    "DN-C5-006": ("C5_ACCOUNT_LIFECYCLE=DEFER_UNTIL_C4_APPROVED", "docs/c5-report-profile/product-decision-packet.md"),
}

CROSS_GATES = {
    "REC-PD-001": ("HIDE_NOT_COMPUTED", "docs/recommendation/product-decision-packet.md"),
    "REC-PD-003": ("OPTIONAL_UP_TO_10_WITH_SKIP", "docs/recommendation/product-decision-packet.md"),
    "REC-PD-005": ("KEEP_PARTY_PUBLIC_DISABLED", "docs/recommendation/product-decision-packet.md"),
    "REC-PD-007": ("SHOW_MAX_ONE_FAITHFUL_REASON", "docs/recommendation/product-decision-packet.md"),
}


def main() -> None:
    approval = APPROVAL.read_text(encoding="utf-8")
    errors: list[str] = []

    for decision_id, (token, source_path) in DECISIONS.items():
        expected_row = f"| `{decision_id}` | `{token}` |"
        if approval.count(expected_row) != 1:
            errors.append(f"approval row drift: {decision_id} -> {token}")
        source = (ROOT / source_path).read_text(encoding="utf-8")
        if f"`{token}`" not in source:
            errors.append(f"source packet no longer contains token: {decision_id} -> {token}")

    for decision_id, (token, source_path) in CROSS_GATES.items():
        expected_row = f"| `{decision_id}` | `{token}` |"
        if approval.count(expected_row) != 1:
            errors.append(f"cross-gate row drift: {decision_id} -> {token}")
        source = (ROOT / source_path).read_text(encoding="utf-8")
        if f"`{token}`" not in source:
            errors.append(f"source packet no longer contains cross-gate token: {decision_id} -> {token}")

    required_boundaries = (
        "RECORDED_LOCAL_PRODUCT_APPROVAL",
        "사용자의 연속된 로컬 개발 지시를 제품 승인으로",
        "commit·push·MR·배포·운영 credential 주입은 포함하지 않는다",
        "revision 고정, clean-checkout 재현, 실제 Compose local-MVP E2E 성공은 별도 완료 Gate",
        "`DN-C5-006`은 선택 자체가",
        "명시적 `DEFER`",
        "MovieLens offline 개선을 실제 사용자 만족도 개선이라고 표현하지 않는다",
        "예상 별점, 취향 진단, 추천 만족도는 근거가 생기기 전 `NOT_COMPUTED`",
        "Party public recommendation은 champion evidence 전 구현하지 않는다",
        "운영 mail/OAuth key, 실제 sender domain, 배포 환경은 별도 승인",
        "C1 click event 계약 보완 필수",
        "C0 Catalog lock/version·C1 final-check 보완 필수",
        "taste compare는 opt-in 값만 저장하고 계산·화면·공유는 계속 disabled",
        "`REC-PD-001` | `HIDE_NOT_COMPUTED` | `DN-C2B-004`와 동일하게",
        "`REC-PD-003` | `OPTIONAL_UP_TO_10_WITH_SKIP` | `DN-C4A-004`와 같은",
        "`REC-PD-005` | `KEEP_PARTY_PUBLIC_DISABLED` | `DN-C3-001`과 같이",
        "`REC-PD-007` | `SHOW_MAX_ONE_FAITHFUL_REASON` | `DN-C2B-003`과 같이",
        "`DN-C5-006`은 선택 자체가\n명시적 `DEFER`이므로 계약·구현 권위가 생기지 않는다",
    )
    for boundary in required_boundaries:
        if boundary not in approval:
            errors.append(f"approval authority/safety boundary missing: {boundary}")

    if "PENDING_PRODUCT_OWNER" in approval:
        errors.append("recorded local product approval must not retain PENDING_PRODUCT_OWNER")

    if len(DECISIONS) != 22:
        errors.append("decision inventory must contain exactly 22 decisions")

    exact_short_approval = (
        "FEELM standalone C2B~C5 22개 권장안과 REC-PD-001·003·005·007 교차 Gate를 전체 승인한다. "
        "패킷의 수치·허용 손실·rollback·재검토·retention을 그대로 적용하라. 계약 전체가 machine validation을 "
        "통과한 slice/task만 로컬 구현하고, C5-006은 DEFER하라. "
        "commit·push·MR·배포·운영 credential 주입은 별도 승인 전 금지한다."
    )
    if exact_short_approval not in approval.replace("\n", " "):
        errors.append("short approval conditional-authority/operational boundary drift")

    if errors:
        raise SystemExit("Product owner approval validation failed:\n- " + "\n- ".join(errors))

    print("Product owner approval validation passed: 22 recommendations + 4 cross-gates, local product approval recorded; operational authority remains separate.")


if __name__ == "__main__":
    main()
