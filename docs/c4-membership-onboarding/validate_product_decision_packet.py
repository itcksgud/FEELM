#!/usr/bin/env python3
"""Fail closed if C4A local authority or blocked production boundaries drift."""

from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent
PACKET = ROOT / "product-decision-packet.md"
DECISION_TRACKER = ROOT / "decision-needed.md"
README = ROOT / "README.md"
TASKS = ROOT / "tasks" / "implementation-backlog.yaml"
OPENAPI = ROOT / "api" / "openapi.fragment.yaml"
DECISIONS = {f"DN-C4A-{number:03d}" for number in range(1, 6)}

REQUIRED_PACKET_PHRASES = {
    "APPROVED_LOCAL_PROFILE_WITH_BLOCKED_PRODUCTION_EXTENSIONS",
    "APPROVED_LOCAL_PROFILE_5_OF_5",
    "LOCAL_PROFILE_IMPLEMENTATION_AUTHORITY: YES",
    "PRODUCTION_ACTIVATION_AUTHORITY: NO",
    "ACCESS_TTL=10m",
    "REFRESH_IDLE_TTL=7d",
    "REFRESH_ABSOLUTE_TTL=30d",
    "REFRESH_ROTATION=EVERY_USE",
    "LOGOUT_SCOPE=CURRENT_SESSION",
    "CSRF=ORIGIN_PLUS_SIGNED_DOUBLE_SUBMIT",
    "ROTATED_REFRESH_RACE_GRACE=5s",
    "COOKIE_MUTATION_AUTH=REFRESH_PLUS_ORIGIN_PLUS_CSRF",
    "JWT_AFTER_LOGOUT=VALID_UNTIL_EXP_MAX_10m",
    "REFRESH_RACE_CLOCK=POSTGRES_CLOCK_TIMESTAMP",
    "LINEAGE_RETENTION_AFTER_TERMINAL=30d",
    "COOKIELESS_LOGOUT_RETRY=204_CLEAR_NO_MUTATION",
    "SIGNUP_FIELDS=email,password,nickname",
    "PASSWORD_LENGTH=15..128",
    "NICKNAME_LENGTH=2..20",
    "NICKNAME_NORMALIZATION=TRIM_NFKC_CASEFOLD",
    "NICKNAME_UNIQUENESS=GLOBAL",
    "NICKNAME_CHANGE_COOLDOWN=30d",
    "EMAIL_VERIFICATION=REQUIRED",
    "VERIFICATION_TOKEN_ENTROPY=256bit",
    "VERIFICATION_TTL=10m",
    "VERIFICATION_ATTEMPTS=5",
    "RESEND_COOLDOWN=60s",
    "SHARED_MAIL_IDENTITY_LIMIT=5/h+10/d",
    "SHARED_MAIL_IP_LIMIT=20/h+100/d",
    "LOCAL_MAIL=MAILPIT_NO_AUTH",
    "PRODUCTION_MAIL=DEFERRED_CREDENTIAL_GATE",
    "GENERIC_SIGNUP_HANDLE=PERSISTED_DECOY_UUID",
    "SIGNUP_RESPONSE_FLOOR=CALIBRATED_SUPPORTED_PHC_P99_PLUS_25MS",
    "SIGNUP_RESPONSE_JITTER=0..75ms",
    "VERIFICATION_DELIVERY=HTTPS_FRAGMENT_THEN_POST_BODY",
    "VERIFICATION_LINK_ORIGIN=PINNED_CONFIG_ONLY",
    "RATE_LIMIT_AUTHORITY=SHARED_ATOMIC_FAIL_CLOSED",
    "SIGNUP_FLOW_TTL=24h_NO_EXTENSION",
    "PENDING_ACCOUNT_PURGE=30d",
    "PENDING_RESIGNUP_RECOVERY=PASSWORD_VERIFIED_NEW_REAL_FLOW",
    "PUBLIC_FLOW_MODEL=STABLE_SIGNUP_ID_VERSIONED_CURRENT_CHALLENGE",
    "DELIVERY_MATERIAL=AES256_GCM_SINGLE_USE_VERSIONED_KEY",
    "PUBLIC_AUTH_IDEMPOTENCY=SEPARATE_LEDGER_KEYED_REQUEST_HMAC",
    "RECOVERY_LINEARIZATION=PERSISTED_ADMISSION_PRIOR_FLOW",
    "HMAC_ROTATION_QUOTA=ATOMIC_CURRENT_PREVIOUS_AGGREGATE",
    "ONBOARDING_MAX=10",
    "SUBMITTED_MIN=1",
    "SKIP_AT_ZERO=true",
    "K10=RECOMMENDED_NOT_REQUIRED",
    "RERUN=BLOCKED_LOCAL_PROFILE",
    "K10_FULL_CATALOG_ALPHA=0.2",
    "SOCIAL_GOOGLE=DISABLED",
    "SOCIAL_KAKAO=DISABLED",
    "SOCIAL_NAVER=DISABLED",
    "AUTO_MERGE_BY_EMAIL=FORBIDDEN",
    "LINKING=AUTHENTICATED_EXPLICIT",
    "RECENT_REAUTH_MAX_AGE=10m",
    "OAUTH_STATE_TTL=10m",
    "PKCE=S256",
    "SOCIAL_IDENTITY_KEY=PROVIDER_ISSUER_SUBJECT_HMAC",
    "LINK_TX_TTL=10m",
    "MIX_UP_DEFENSE=SERVER_ISSUER_BINDING",
}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def main() -> int:
    errors: list[str] = []
    for path in (PACKET, DECISION_TRACKER, README, TASKS, OPENAPI):
        if not path.exists():
            errors.append(f"missing required file: {path.relative_to(ROOT)}")
    if errors:
        return fail(errors)

    packet = read(PACKET)
    tracker = read(DECISION_TRACKER)
    readme = read(README)
    tasks = yaml.safe_load(read(TASKS))
    openapi = yaml.safe_load(read(OPENAPI))

    packet_decisions = set(re.findall(r"DN-C4A-\d{3}", packet))
    if packet_decisions != DECISIONS:
        errors.append(f"packet decision set drift: {sorted(packet_decisions)}")
    for decision_id in DECISIONS:
        if f"## {decision_id} —" not in packet:
            errors.append(f"missing dedicated section for {decision_id}")
        tracker_row = next(
            (line for line in tracker.splitlines() if f"`{decision_id}`" in line),
            "",
        )
        if "`APPROVED_LOCAL_PROFILE_2026-08-30`" not in tracker_row:
            errors.append(f"{decision_id} local-profile tracker status drift")
    if "Local profile 승인 현황: `5/5`" not in tracker:
        errors.append("tracker must retain local-profile approval count 5/5")

    missing_phrases = sorted(REQUIRED_PACKET_PHRASES - set(
        phrase for phrase in REQUIRED_PACKET_PHRASES if phrase in packet
    ))
    if missing_phrases:
        errors.append(f"packet missing canonical recommendation values: {missing_phrases}")
    for heading in ("반대안 손실", "보안 영향", "UX 영향", "Rollback", "운영 credential 경계"):
        if packet.count(f"### {heading}") != 5:
            errors.append(f"expected 5 decision subsections named {heading!r}")

    for marker in (
        "결정 상태: `APPROVED_LOCAL_PROFILE_5_OF_5`",
        "`LOCAL_PROFILE_IMPLEMENTATION_AUTHORITY: YES`",
        "`PRODUCTION_ACTIVATION_AUTHORITY: NO`",
    ):
        if marker not in packet:
            errors.append(f"packet missing local/production authority marker {marker}")
    if "local profile 구현 권위: `YES`" not in readme:
        errors.append("README must expose local implementation authority")
    if "main OpenAPI local 13-operation merge: `YES`; production/OAuth/restart/password lifecycle 권위: `NO`" not in readme:
        errors.append("README production/extension authority boundary drift")
    if "APPROVED_LOCAL_PROFILE_5_OF_5" not in tracker:
        errors.append("decision tracker does not expose adopted local decisions")

    task_rows = tasks.get("tasks", [])
    by_id = {row.get("id"): row for row in task_rows}
    expected_states = {
        "TASK-C4A-001": "DONE_LOCAL_PROFILE_DECISIONS",
        "TASK-C4A-002": "DONE_LOCAL_PROFILE_CONTRACT",
        "TASK-C4A-003": "READY_LOCAL_PROFILE",
        "TASK-C4A-009": "READY_LOCAL_PROFILE_NEGATIVE_ONLY",
        "TASK-C4A-013": "BLOCKED_PRODUCTION_PROMOTION",
    }
    for task_id, row in by_id.items():
        expected_state = expected_states.get(task_id, "BLOCKED")
        if row.get("status") != expected_state:
            errors.append(f"{task_id} must be {expected_state}")
    task_rules = tasks.get("rules", {})
    if task_rules.get("local_profile_implementation_authorized") is not True:
        errors.append("task tracker must authorize local implementation")
    if task_rules.get("production_activation_forbidden") is not True:
        errors.append("task tracker must block production activation")
    if len(task_rules.get("local_operation_ids", [])) != 13 or task_rules.get("blocked_operation_ids") != ["restartOnboarding"]:
        errors.append("task tracker local/blocked operation manifest drift")
    if len(task_rules.get("local_screen_ids", [])) != 7:
        errors.append("task tracker seven-screen manifest drift")
    if set(task_rules.get("blocked_acceptance_ids", [])) != {"AC-C4A-034", "AC-C4A-045", "AC-C4A-067"}:
        errors.append("task tracker blocked acceptance manifest drift")

    if openapi.get("x-contract-status") != "APPROVED_LOCAL_PROFILE_WITH_BLOCKED_PRODUCTION_EXTENSIONS":
        errors.append("OpenAPI local-profile contract status drift")
    if openapi.get("x-main-openapi-merged") is not True:
        errors.append("OpenAPI fragment must record the authorized local main merge")
    if openapi.get("x-local-profile-implementation-status") != "AUTHORIZED_13_OPERATIONS":
        errors.append("OpenAPI 13-operation local authority drift")
    if openapi.get("x-production-activation-status") != "BLOCKED":
        errors.append("OpenAPI production activation must remain blocked")

    required_security_boundaries = (
        "email claim",
        "자동 병합하지 않는다",
        "Secure; HttpOnly; SameSite=Lax",
        "Origin",
        "signed double-submit",
        "Argon2id",
        "raw token",
        "Mailpit",
        "loopback",
        "production provider",
        "history.replaceState",
        "VERIFICATION_FRONTEND_ORIGIN",
        "X-Forwarded-Host",
        "persisted decoy",
        "shared Redis",
        "fail-closed",
        "hard security floor",
        "EMAIL_SIGNUP_PUBLIC_FLOW",
        "current_challenge_id",
        "pending_purge_at",
        "feelm_local_refresh",
        "clock_timestamp()",
        "INVALID_CREDENTIALS",
        "subjectHmac",
        "0.000253, 0.002783",
    )
    for phrase in required_security_boundaries:
        if phrase not in packet:
            errors.append(f"packet missing security boundary {phrase!r}")

    cookie_mutation_security = [
        {"refreshCookie": [], "csrfCookie": []},
        {"localRefreshCookie": [], "localCsrfCookie": []},
    ]
    paths = openapi.get("paths", {})
    login = paths.get("/api/v1/auth/login", {}).get("post", {})
    refresh = paths.get("/api/v1/auth/refresh", {}).get("post", {})
    logout = paths.get("/api/v1/auth/logout", {}).get("post", {})
    for operation_id, operation in (("refreshAuthentication", refresh),):
        if operation.get("security") != cookie_mutation_security:
            errors.append(f"{operation_id} OpenAPI cookie/CSRF security contradicts packet")
        parameter_refs = {row.get("$ref") for row in operation.get("parameters", [])}
        if not {
            "#/components/parameters/OriginHeader",
            "#/components/parameters/CsrfTokenHeader",
        } <= parameter_refs:
            errors.append(f"{operation_id} OpenAPI lacks Origin/CSRF parameters")
        if "403" not in operation.get("responses", {}):
            errors.append(f"{operation_id} OpenAPI lacks CSRF 403")
    if logout.get("security") != cookie_mutation_security + [{}]:
        errors.append("logout OpenAPI does not isolate cookie-less 204 retry")
    logout_refs = {row.get("$ref") for row in logout.get("parameters", [])}
    if logout_refs != {
        "#/components/parameters/OptionalIdempotencyKey",
        "#/components/parameters/OriginHeader",
        "#/components/parameters/OptionalCsrfTokenHeader",
    }:
        errors.append("logout OpenAPI optional retry parameters drift")
    if "403" not in logout.get("responses", {}):
        errors.append("logout OpenAPI lacks active-session CSRF 403")
    if "401" not in logout.get("responses", {}):
        errors.append("logout OpenAPI lacks invalid-session 401")
    login_parameter_refs = {row.get("$ref") for row in login.get("parameters", [])}
    if "#/components/parameters/OriginHeader" not in login_parameter_refs or "403" not in login.get("responses", {}):
        errors.append("loginWithEmail OpenAPI lacks exact-Origin fail-closed boundary")
    verification_security = openapi.get("x-email-verification-security", {})
    if verification_security.get("requestHostHeadersTrusted") is not False:
        errors.append("OpenAPI must reject request Host headers as verification-link authority")
    if verification_security.get("frontendOriginSource") != "pinned versioned server configuration only":
        errors.append("OpenAPI verification link origin is not pinned config")
    rate_authority = openapi.get("x-auth-rate-limit-authority", {})
    if rate_authority.get("localFallback") != "forbidden" or not str(rate_authority.get("unavailableBehavior", "")).startswith("503"):
        errors.append("OpenAPI auth rate limiter is not shared/fail-closed")
    password_boundary = openapi.get("x-password-hashing-boundary", {})
    if password_boundary.get("hardFloor") != "m=19456 KiB,t=2,p=1" or "fail" not in password_boundary.get("conflictBehavior", ""):
        errors.append("OpenAPI Argon2 floor does not outrank the performance target")
    if "max(p99 supported workloads)+25ms" not in str(password_boundary.get("publicTiming", "")):
        errors.append("OpenAPI supported-PHC calibrated timing floor drift")
    delivery_boundary = openapi.get("x-verification-delivery-boundary", {})
    if delivery_boundary.get("model") != "encrypted single-use delivery material referenced by a safe outbox":
        errors.append("OpenAPI safe verification delivery model drift")
    jwt_boundary = openapi.get("x-access-jwt-boundary", {})
    if jwt_boundary.get("algorithmAllowlist") != ["RS256"] or jwt_boundary.get("leewaySeconds") != 30:
        errors.append("OpenAPI JWT algorithm/time boundary drift")
    signup_policy = paths.get("/api/v1/auth/sign-up", {}).get("post", {}).get("x-enumeration-response-policy", {})
    if signup_policy.get("returnedHandleRetentionHours") != 24 or "persistent decoy" not in signup_policy.get("duplicateOrUnknownHandle", ""):
        errors.append("OpenAPI signup decoy follow-up state is not persistent")
    if signup_policy.get("stableHandleEntity") != "EMAIL_SIGNUP_PUBLIC_FLOW" or signup_policy.get("pendingPurge") != "account createdAt plus 30 days without re-signup extension":
        errors.append("OpenAPI stable signup flow/recovery/purge boundary drift")
    for path in ("/api/v1/auth/email-verifications", "/api/v1/auth/email-verification-resends"):
        policy = paths.get(path, {}).get("post", {}).get("x-generic-handle-policy", {})
        if "actual and decoy" not in policy.get("persistence", "") or "one transition winner" not in policy.get("concurrency", ""):
            errors.append(f"{path} does not preserve generic follow-up state/race semantics")
    schemas = openapi.get("components", {}).get("schemas", {})
    for schema_name in ("CreateEmailSignupRequest", "EmailLoginRequest"):
        if schemas.get(schema_name, {}).get("properties", {}).get("password", {}).get("minLength") != 15:
            errors.append(f"{schema_name} password minimum differs from packet 15")
    if openapi.get("x-social-linking-reservation", {}).get("identityKey") != ["provider", "issuer", "subjectHmac"]:
        errors.append("OpenAPI social identity reservation differs from packet")

    stale_patterns = (
        "ranking alpha는 모든 K에서 0",
        "ranking alpha 0",
        "HTTPS URL query로 전달",
        "GENERIC_SIGNUP_HANDLE=SYNTHETIC_UUID",
        "비영속 synthetic",
        "영구 보존",
    )
    for phrase in stale_patterns:
        if phrase in packet:
            errors.append(f"packet retains stale/unsafe statement {phrase!r}")

    # Environment-variable names and placeholders are allowed; assigned values and token-like strings are not.
    forbidden_patterns = (
        re.compile(r"eyJ[A-Za-z0-9_-]{20,}"),
        re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
        re.compile(r"(?im)^\s*(?:MAIL_API_KEY|AUTH_JWT_SIGNING_PRIVATE_KEY|OAUTH_[A-Z_]+_CLIENT_SECRET)\s*=\s*\S+"),
        re.compile(r"(?i)https://[^\s)]+/(?:oauth|callback)(?:[^\s)]*)"),
    )
    for pattern in forbidden_patterns:
        if pattern.search(packet):
            errors.append(f"possible raw credential/redirect detected by {pattern.pattern}")

    if "axllent/mailpit:v1.30.4" not in packet:
        errors.append("Mailpit procedure must retain reviewed version pin")
    if "127.0.0.1:1025:1025" not in packet or "127.0.0.1:8025:8025" not in packet:
        errors.append("Mailpit procedure must bind SMTP/UI to loopback")
    if "현재 backend mail adapter와 Compose service는 없으므로" not in packet:
        errors.append("Mailpit plan must not claim current implementation")

    if errors:
        return fail(errors)
    print(
        "C4A product decision packet validation passed: "
        "5/5 conservative tokens authorize the 13-operation local profile and main merge; production, OAuth, restart, lifecycle, and real mail remain blocked."
    )
    return 0


def fail(errors: list[str]) -> int:
    print("C4A product decision packet validation failed:")
    for error in errors:
        print(f"- {error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
