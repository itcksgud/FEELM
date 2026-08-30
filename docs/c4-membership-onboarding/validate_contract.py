#!/usr/bin/env python3
"""Fail closed on drift from the authorized C4A local-only profile."""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parent
STATUS = "APPROVED_LOCAL_PROFILE_WITH_BLOCKED_PRODUCTION_EXTENSIONS"
DECISIONS = {f"DN-C4A-{number:03d}" for number in range(1, 6)}
SCREENS = {f"SCR-C4A-{number:03d}" for number in range(1, 8)}
SCENARIOS = {f"SCN-C4A-{number:03d}" for number in range(1, 8)}
ACCEPTANCE = {f"AC-C4A-{number:03d}" for number in range(1, 86)}
TASKS = {f"TASK-C4A-{number:03d}" for number in range(1, 14)}
REQUIREMENTS = {
    "FR-01",
    "FR-02",
    "FR-03",
    "NFR-07",
    "REQ-C4A-NICKNAME",
    "REQ-C4A-SOCIAL",
    "REQ-C4A-BLOCKED-EXTENSIONS",
    "REQ-C4A-EMAIL-SECURITY",
    "REQ-C4A-DELIVERY",
    "REQ-C4A-AUTH-HARDENING",
    "REQ-C4A-JSON-CONSTRAINTS",
}
LOCAL_OPERATIONS = {
    "createEmailSignup",
    "verifySignupEmail",
    "resendSignupEmailVerification",
    "loginWithEmail",
    "refreshAuthentication",
    "logoutCurrentSession",
    "getMyMembership",
    "updateMyNickname",
    "listOnboardingMovies",
    "replaceOnboardingPreferences",
    "completeOnboarding",
    "getMyOttSubscriptions",
    "replaceMyOttSubscriptions",
}
BLOCKED_OPERATIONS = {"restartOnboarding"}
BLOCKED_ACCEPTANCE = {"AC-C4A-034", "AC-C4A-045", "AC-C4A-067"}
LOCAL_ACCEPTANCE = ACCEPTANCE - BLOCKED_ACCEPTANCE
PRODUCTION_NEGATIVE_ACCEPTANCE = {
    "AC-C4A-007", "AC-C4A-061", "AC-C4A-073", "AC-C4A-081", "AC-C4A-082"
}
TESTS = {
    "TEST-BE-C4A-MEMBERSHIP",
    "TEST-BE-C4A-AUTH",
    "TEST-BE-C4A-ONBOARDING",
    "TEST-BE-C4A-OTT",
    "TEST-BE-C4A-SECURITY",
    "TEST-FE-C4A-MEMBERSHIP",
    "TEST-FE-C4A-ONBOARDING",
    "TEST-E2E-C4A",
    "TEST-CONTRACT-C4A-SOCIAL-BLOCK",
}
ENTITIES = {
    "USER_ACCOUNT",
    "EMAIL_CREDENTIAL",
    "USER_PROFILE",
    "EMAIL_SIGNUP_PUBLIC_FLOW",
    "EMAIL_VERIFICATION_CHALLENGE",
    "VERIFICATION_DELIVERY_MATERIAL",
    "PUBLIC_AUTH_IDEMPOTENCY_RECORD",
    "PUBLIC_AUTH_IDEMPOTENCY_SCOPE_ALIAS",
    "PUBLIC_AUTH_IDEMPOTENCY_REQUEST_HMAC_ALIAS",
    "PENDING_SIGNUP_RECOVERY_ATTEMPT",
    "AUTH_SESSION",
    "AUTH_REFRESH_TOKEN",
    "ONBOARDING_JOURNEY",
    "ONBOARDING_PREFERENCE",
    "OTT_SUBSCRIPTION_SET",
    "USER_OTT_SUBSCRIPTION",
    "SOCIAL_PROVIDER_CAPABILITY",
    "SOCIAL_IDENTITY",
    "SOCIAL_LINK_TRANSACTION",
    "MOVIE_IDENTITY",
    "OTT_PROVIDER",
    "IDEMPOTENCY_RECORD",
    "DOMAIN_OUTBOX",
}
BLOCKED_ENTITIES = {"SOCIAL_PROVIDER_CAPABILITY", "SOCIAL_IDENTITY", "SOCIAL_LINK_TRANSACTION"}
LOCAL_ENTITIES = ENTITIES - BLOCKED_ENTITIES
EXPECTED_FILES = {
    "README.md",
    "00-product-scope.md",
    "01-glossary-and-policies.md",
    "02-business-rules.md",
    "03-state-machines.md",
    "decision-needed.md",
    "product-decision-packet.md",
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


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8-sig")


def ids(pattern: str, value: str) -> set[str]:
    return set(re.findall(pattern, value))


def split_refs(value: str | None) -> set[str]:
    if not value:
        return set()
    return {item.strip() for item in value.split("|") if item.strip()}


def walk_decision_extensions(value: Any, errors: list[str], path: str = "$") -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}"
            if key in {"x-decision-required", "x-local-decision-authority"}:
                if not isinstance(item, list) or not item:
                    errors.append(f"{child} must be a non-empty decision list")
                else:
                    refs = {str(entry) for entry in item}
                    found.update(refs)
                    for missing in sorted(refs - DECISIONS):
                        errors.append(f"{child} references unknown {missing}")
            found.update(walk_decision_extensions(item, errors, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.update(walk_decision_extensions(item, errors, f"{path}[{index}]"))
    return found


def validate_tasks(errors: list[str]) -> None:
    document = yaml.safe_load(text("tasks/implementation-backlog.yaml"))
    if document.get("status") != STATUS:
        errors.append("task DAG local-profile status drift")
    rules = document.get("rules", {})
    if rules.get("local_profile_implementation_authorized") is not True:
        errors.append("task DAG must authorize the local profile")
    if rules.get("production_activation_forbidden") is not True:
        errors.append("task DAG must block production activation")
    if rules.get("production_email_oauth_restart_lifecycle_forbidden") is not True:
        errors.append("task DAG must block production mail/OAuth/restart/lifecycle")
    if rules.get("main_openapi_local_profile_merged") is not True:
        errors.append("task DAG must record the authorized local main OpenAPI merge")
    if set(rules.get("required_decision_ids", [])) != DECISIONS:
        errors.append("task DAG required_decision_ids drift")
    if set(rules.get("local_operation_ids", [])) != LOCAL_OPERATIONS:
        errors.append("task DAG local operation set drift")
    if set(rules.get("blocked_operation_ids", [])) != BLOCKED_OPERATIONS:
        errors.append("task DAG blocked operation set drift")
    if set(rules.get("local_screen_ids", [])) != SCREENS:
        errors.append("task DAG local screen set drift")
    if rules.get("acceptance_universe") != "AC-C4A-001..085":
        errors.append("task DAG acceptance universe drift")
    if set(rules.get("blocked_acceptance_ids", [])) != BLOCKED_ACCEPTANCE:
        errors.append("task DAG blocked acceptance set drift")
    if set(rules.get("production_negative_acceptance_ids", [])) != PRODUCTION_NEGATIVE_ACCEPTANCE:
        errors.append("task DAG production-negative acceptance set drift")
    if set(rules.get("local_entity_ids", [])) != LOCAL_ENTITIES:
        errors.append("task DAG local entity set drift")
    if set(rules.get("blocked_entity_ids", [])) != BLOCKED_ENTITIES:
        errors.append("task DAG blocked entity set drift")
    rows = document.get("tasks", [])
    found = {row.get("id") for row in rows}
    if found != TASKS:
        errors.append(f"task IDs drift: {sorted(found)}")
    by_id = {row["id"]: row for row in rows if "id" in row}
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
            errors.append(f"{task_id} status must be {expected_state}")
        dependencies = set(row.get("depends_on", []))
        if not dependencies <= TASKS:
            errors.append(f"{task_id} has unknown dependencies {sorted(dependencies - TASKS)}")
        decision_refs = set(row.get("decision_record_ids", []))
        if not decision_refs <= DECISIONS:
            errors.append(f"{task_id} has unknown decisions {sorted(decision_refs - DECISIONS)}")
        requirement_refs = set(row.get("requirement_ids", []))
        if not requirement_refs <= REQUIREMENTS:
            errors.append(f"{task_id} has unknown requirements {sorted(requirement_refs - REQUIREMENTS)}")
        acceptance_refs = set(row.get("acceptance_ids", []))
        if not acceptance_refs <= ACCEPTANCE:
            errors.append(f"{task_id} has unknown acceptance IDs {sorted(acceptance_refs - ACCEPTANCE)}")
        if acceptance_refs & BLOCKED_ACCEPTANCE:
            errors.append(f"{task_id} local acceptance_ids include blocked extensions {sorted(acceptance_refs & BLOCKED_ACCEPTANCE)}")
        blocked_acceptance_refs = set(row.get("blocked_acceptance_ids", []))
        if not blocked_acceptance_refs <= BLOCKED_ACCEPTANCE:
            errors.append(f"{task_id} has invalid blocked_acceptance_ids {sorted(blocked_acceptance_refs - BLOCKED_ACCEPTANCE)}")
    if set(by_id["TASK-C4A-007"].get("blocked_acceptance_ids", [])) != {"AC-C4A-034"}:
        errors.append("TASK-C4A-007 restart blocked AC drift")
    if set(by_id["TASK-C4A-009"].get("acceptance_ids", [])) != {"AC-C4A-044", "AC-C4A-046"}:
        errors.append("TASK-C4A-009 local social-negative AC drift")
    if set(by_id["TASK-C4A-009"].get("blocked_acceptance_ids", [])) != {"AC-C4A-045", "AC-C4A-067"}:
        errors.append("TASK-C4A-009 blocked OAuth AC drift")
    final_gate_required = {f"AC-C4A-{number:03d}" for number in range(77, 86)}
    final_gate_actual = set(by_id.get("TASK-C4A-012", {}).get("acceptance_ids", []))
    if not final_gate_required <= final_gate_actual:
        errors.append(f"TASK-C4A-012 final remediation gate missing {sorted(final_gate_required - final_gate_actual)}")
    # DAG cycle check.
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


def validate_openapi(errors: list[str]) -> tuple[set[str], set[str]]:
    document = yaml.safe_load(text("api/openapi.fragment.yaml"))
    if document.get("openapi") != "3.1.0":
        errors.append("OpenAPI must be 3.1.0")
    if document.get("x-contract-status") != STATUS:
        errors.append("OpenAPI x-contract-status drift")
    if document.get("x-main-openapi-merged") is not True:
        errors.append("C4A local fragment must record main OpenAPI merge")
    paths = document.get("paths", {})
    main = yaml.safe_load((ROOT.parent / "api" / "openapi.yaml").read_text(encoding="utf-8"))
    main_paths = main.get("paths", {})
    expected_main_refs = {
        path: f"../c4-membership-onboarding/api/openapi.fragment.yaml#/paths/{path.replace('/', '~1')}"
        for path, item in paths.items()
        if any(operation.get("x-implementation-status") == "AUTHORIZED_LOCAL_CONTRACT" for operation in item.values() if isinstance(operation, dict))
    }
    for path, expected_ref in expected_main_refs.items():
        if main_paths.get(path) != {"$ref": expected_ref}:
            errors.append(f"main OpenAPI local path merge drift: {path}")
    if "/api/v1/onboarding/restart" in main_paths:
        errors.append("main OpenAPI must not expose blocked restartOnboarding")
    if document.get("x-local-profile-implementation-status") != "AUTHORIZED_13_OPERATIONS":
        errors.append("OpenAPI local operation authority drift")
    if document.get("x-production-activation-status") != "BLOCKED":
        errors.append("OpenAPI production activation must remain blocked")
    if any("social" in path.lower() for path in paths):
        errors.append("social public paths are forbidden while DN-C4A-005 is unresolved")
    blocked = document.get("x-blocked-capabilities", [])
    expected_blocked = {
        "SOCIAL_GOOGLE": "DN-C4A-005",
        "SOCIAL_KAKAO": "DN-C4A-005",
        "SOCIAL_NAVER": "DN-C4A-005",
        "ONBOARDING_RESTART": "DN-C4A-004",
        "PRODUCTION_EMAIL": "DN-C4A-003",
        "PASSWORD_ACCOUNT_LIFECYCLE": "DN-C4A-001",
    }
    if {row.get("id") for row in blocked} != set(expected_blocked):
        errors.append("blocked capability set drift")
    for row in blocked:
        if row.get("status") != "BLOCKED" or row.get("decisionRequired") != expected_blocked.get(row.get("id")):
            errors.append(f"capability is not fail-closed: {row}")
    csrf_reservation = document.get("x-auth-cookie-csrf-reservation", {})
    expected_csrf_reservation = {
        "status": "AUTHORIZED_LOCAL_PROFILE",
        "decisionRequired": "DN-C4A-001",
        "refreshCookie": "__Host-feelm_refresh",
        "csrfCookie": "__Host-feelm_csrf",
        "csrfHeader": "X-CSRF-Token",
        "mutationProtection": "exact Origin allowlist plus session-bound signed double-submit",
        "rotation": "refresh and CSRF values rotate together after each successful refresh",
        "cookieProfiles": {
            "productionHttps": {
                "refresh": {"name": "__Host-feelm_refresh", "secure": True, "httpOnly": True, "sameSite": "Lax", "path": "/", "domain": None},
                "csrf": {"name": "__Host-feelm_csrf", "secure": True, "httpOnly": False, "sameSite": "Lax", "path": "/", "domain": None},
            },
            "localHttpLoopbackOnly": {
                "refresh": {"name": "feelm_local_refresh", "secure": False, "httpOnly": True, "sameSite": "Lax", "path": "/", "domain": None},
                "csrf": {"name": "feelm_local_csrf", "secure": False, "httpOnly": False, "sameSite": "Lax", "path": "/", "domain": None},
            },
            "clearAdds": {"maxAge": 0, "expires": "Thu, 01 Jan 1970 00:00:00 GMT"},
            "mixedProfileCookies": "forbidden",
        },
    }
    if csrf_reservation != expected_csrf_reservation:
        errors.append("auth cookie/CSRF reservation drift")
    verification_security = document.get("x-email-verification-security", {})
    expected_verification_security = {
        "status": "AUTHORIZED_LOCAL_PROFILE",
        "decisionRequired": "DN-C4A-003",
        "frontendOriginSource": "pinned versioned server configuration only",
        "requestHostHeadersTrusted": False,
        "ignoredRequestHeaders": ["Host", "Forwarded", "X-Forwarded-Host"],
        "linkTransport": "exact HTTPS frontend origin plus fragment, then POST body",
        "invalidOriginBehavior": "dispatch and readiness fail closed",
        "returnedHandleState": "stable EMAIL_SIGNUP_PUBLIC_FLOW actual or decoy row for 24 hours without extension",
        "challengeModel": "REAL flow current_challenge_id references versioned EMAIL_VERIFICATION_CHALLENGE; DECOY has no challenge",
        "pendingRecovery": "before created_at plus 30 days, same-password re-signup creates a new REAL flow after prior flow expiry",
        "pendingPurge": "created_at plus 30 days; re-signup does not extend; verification race is atomic",
    }
    if verification_security != expected_verification_security:
        errors.append("verification link origin/persisted-handle security drift")
    rate_limit_authority = document.get("x-auth-rate-limit-authority", {})
    expected_rate_limit_authority = {
        "status": "AUTHORIZED_LOCAL_PROFILE",
        "decisionRequired": "DN-C4A-003",
        "decisionDependencies": ["DN-C4A-001", "DN-C4A-003"],
        "store": "shared Redis",
        "clock": "Redis server UTC TIME",
        "operation": "atomic counter plus TTL via Lua or Redis Function",
        "localFallback": "forbidden",
        "unavailableBehavior": "503 before identity, challenge, or session mutation",
        "policies": {
            "globalEmergency": {"policy": "versioned capacity-derived integer limit required at approval", "missingConfiguration": "non-test readiness fails closed"},
            "sharedMail": {
                "operations": ["createEmailSignup", "resendSignupEmailVerification"],
                "identity": [{"limit": 5, "window": "1h"}, {"limit": 10, "window": "1d"}],
                "coarseIp": [{"limit": 20, "window": "1h"}, {"limit": 100, "window": "1d"}],
                "actualAndDecoy": "consume on idempotency miss",
            },
            "verify": {"coarseIp": [{"limit": 30, "window": "1h"}]},
            "login": {"identityFailures": {"threshold": 5, "window": "15m", "delay": "exponential-30s-to-15m"}, "coarseIp": {"limit": 50, "window": "15m", "throttle": "15m"}},
            "refresh": {"sessionFamily": {"limit": 30, "window": "1m"}, "coarseIp": {"limit": 120, "window": "15m"}},
        },
        "networkProjection": {
            "trustedProxy": "only the immediate peer in configured trusted-proxy CIDRs may supply one canonical configured Forwarded or X-Forwarded-For chain",
            "untrustedPeer": "socket peer IP only; forwarded headers ignored",
            "malformedOrAmbiguous": "reject before rate admission",
            "coarseIp": {"ipv4Prefix": 24, "ipv6Prefix": 56},
            "keyedProjection": "HMAC with explicit key version",
            "rotationAggregation": "one Redis Function atomically sums current and previous counters for the same logical window, rejects on the aggregate limit, and increments only current on allow",
            "retirement": "previous remains until the maximum quota window and pre-signup idempotency retention both drain; no third active version before drain",
            "keyFailure": "missing or unknown key version fails non-test readiness closed",
        },
    }
    if rate_limit_authority != expected_rate_limit_authority:
        errors.append("distributed auth rate-limit authority drift")
    password_boundary = document.get("x-password-hashing-boundary", {})
    expected_password_boundary = {
        "status": "AUTHORIZED_LOCAL_PROFILE",
        "decisionRequired": "DN-C4A-002",
        "algorithm": "Argon2id",
        "hardFloor": "m=19456 KiB,t=2,p=1",
        "performanceTarget": "p95<=500ms",
        "conflictBehavior": "keep security floor and fail non-test auth readiness/startup",
        "dummyVerification": "same production algorithm, version, memory, time, and parallelism parameters for unknown login and decoy signup",
        "calibration": "current hash, dummy hash, and every supported stored PHC verify workload",
        "publicTiming": "commonFloor=max(p99 supported workloads)+25ms plus 0..75ms CSPRNG server jitter",
        "calibrationFailure": "missing, stale, unsupported PHC, or commonFloor violation fails non-test readiness closed",
    }
    if password_boundary != expected_password_boundary:
        errors.append("Argon2 security-floor/readiness priority drift")
    delivery_boundary = document.get("x-verification-delivery-boundary", {})
    if delivery_boundary.get("model") != "encrypted single-use delivery material referenced by a safe outbox":
        errors.append("verification delivery model drift")
    required_delivery_phrases = {
        "challengePersistence": ("SHA-256 hash only", "never a database"),
        "encryption": ("AES-256-GCM", "key_version"),
        "outboxPayload": ("materialId", "no raw secret"),
        "deletion": ("provider acceptance", "challenge terminal"),
    }
    for key, phrases in required_delivery_phrases.items():
        value = str(delivery_boundary.get(key, ""))
        if any(phrase not in value for phrase in phrases):
            errors.append(f"verification delivery {key} drift")
    if "cannot retire" not in str(delivery_boundary.get("keyRetention", "")):
        errors.append("verification delivery live-key retention drift")
    recipient_scope = str(delivery_boundary.get("recipientSecretScope", ""))
    if "worker memory" not in recipient_scope or "TLS provider request wire" not in recipient_scope or "log" not in recipient_scope:
        errors.append("verification delivery recipient/link persistence scope drift")
    if "redact" not in str(delivery_boundary.get("providerObservability", "")):
        errors.append("verification provider observability redaction drift")
    crash_windows = delivery_boundary.get("crashWindows", {})
    if set(crash_windows) != {"beforeProviderAcceptance", "afterProviderAcceptanceBeforeDelete", "afterChallengeConsumed"}:
        errors.append("verification delivery crash-window contract drift")
    jwt_boundary = document.get("x-access-jwt-boundary", {})
    if jwt_boundary.get("algorithmAllowlist") != ["RS256"] or jwt_boundary.get("requiredHeaders") != ["alg", "kid", "typ"]:
        errors.append("JWT header/algorithm allowlist drift")
    if jwt_boundary.get("requiredClaims") != ["iss", "aud", "sub", "sid", "jti", "iat", "nbf", "exp"]:
        errors.append("JWT required claim drift")
    if jwt_boundary.get("leewaySeconds") != 30 or jwt_boundary.get("maxLifetime") != "10m":
        errors.append("JWT time boundary drift")
    if "unknown or missing kid" not in str(jwt_boundary.get("failure", "")) or "readiness fails closed" not in str(jwt_boundary.get("failure", "")):
        errors.append("JWT resource-server fail-closed drift")
    if document.get("x-cookie-response-boundary") != {
        "repeatedHeader": "Set-Cookie", "headerCount": 2, "commaFolding": "forbidden",
        "profiles": ["productionHttps", "localHttpLoopbackOnly"],
    }:
        errors.append("multi Set-Cookie response boundary drift")
    idem = document.get("x-auth-idempotency-boundary", {})
    if not str(idem.get("order", "")).startswith("canonical validation and scope, PostgreSQL lookup"):
        errors.append("auth idempotency-before-Redis order drift")
    if idem.get("cookieLessLogout") != "no idempotency record" or idem.get("redisFailureResultsPersisted") is not False:
        errors.append("auth idempotency failure/anonymous retention drift")
    if idem.get("activeActorLedger") != "C1 IDEMPOTENCY_RECORD keyed by actor user":
        errors.append("active actor idempotency ledger drift")
    if idem.get("publicAuthLedger") != "C4A canonical PUBLIC_AUTH_IDEMPOTENCY_RECORD plus scope and request-HMAC alias tables; never place anonymous identity or signupId in actor_user_id":
        errors.append("anonymous public-auth physical ledger drift")
    if idem.get("canonicalRecordPrimaryKey") != ["record_id"]:
        errors.append("public-auth idempotency canonical PK drift")
    if idem.get("scopeAliasUnique") != ["scope_kind", "scope_value", "operation_code", "idempotency_key"]:
        errors.append("public-auth idempotency scope alias unique drift")
    if "every accepted current and previous" not in str(idem.get("aliasWrite", "")) or "same canonical record" not in str(idem.get("aliasWrite", "")):
        errors.append("public-auth old/new deployment alias visibility drift")
    if "PUBLIC_AUTH_IDEMPOTENCY_REQUEST_HMAC_ALIAS" not in str(idem.get("requestDigestAlias", "")) or "old and new deployments" not in str(idem.get("requestDigestAlias", "")):
        errors.append("public-auth request HMAC alias visibility drift")
    public_scope = idem.get("publicScope", {})
    if public_scope != {
        "createSignup": "PRE_SIGNUP_IDENTITY current and previous HMAC projections",
        "verifyAndResend": "SIGNUP_FLOW canonical stable signup UUID",
    }:
        errors.append("public-auth idempotency scope drift")
    if "HMAC-SHA-256" not in str(idem.get("requestEquality", "")) or "constant-time" not in str(idem.get("requestEquality", "")):
        errors.append("public-auth keyed request equality drift")
    if idem.get("forbiddenRequestDigest") != "unkeyed hash of a body containing password or verificationSecret":
        errors.append("plain secret-bearing request digest must stay forbidden")
    if "commit or roll back together" not in str(idem.get("atomicity", "")) or "terminal plus 24h" not in str(idem.get("terminalReplay", "")):
        errors.append("public-auth idempotency atomicity/terminal replay drift")
    recovery = document.get("x-pending-signup-recovery-linearization", {})
    if set(recovery) != {"observation", "attempt", "winner", "concurrentLoser", "laterRequest", "atomicity"}:
        errors.append("pending recovery persisted linearization shape drift")
    else:
        serialized_recovery = json.dumps(recovery, sort_keys=True)
        for phrase in ("PENDING_SIGNUP_RECOVERY_ATTEMPT", "admitted_at", "prior_expired_signup_id", "LOST_REPLAY", "DECOY", "commit together"):
            if phrase not in serialized_recovery:
                errors.append(f"pending recovery linearization missing {phrase}")
    if document.get("x-refresh-lineage-boundary") != {
        "status": "AUTHORIZED_LOCAL_PROFILE",
        "decisionRequired": "DN-C4A-001",
        "raceClock": "PostgreSQL primary clock_timestamp under row lock",
        "raceGrace": "5s",
        "retention": "family terminal timestamp plus 30 days",
        "cleanup": "delete family token hashes and AUTH_SESSION together",
    }:
        errors.append("refresh race clock/lineage cleanup boundary drift")
    social_reservation = document.get("x-social-linking-reservation", {})
    if social_reservation.get("status") != "BLOCKED":
        errors.append("social linking reservation must remain BLOCKED")
    if social_reservation.get("identityKey") != ["provider", "issuer", "subjectHmac"]:
        errors.append("social identity must reserve provider+issuer+subjectHmac")
    if social_reservation.get("emailClaimAutoMerge") is not False:
        errors.append("social reservation must forbid email-claim auto merge")

    operations: dict[str, dict[str, Any]] = {}
    operation_paths: dict[str, str] = {}
    for path, path_item in paths.items():
        for method, operation in path_item.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            operation_id = operation.get("operationId")
            if not operation_id:
                errors.append(f"{method.upper()} {path} has no operationId")
                continue
            if operation_id in operations:
                errors.append(f"duplicate operationId {operation_id}")
            operations[operation_id] = operation
            operation_paths[operation_id] = path
    expected_operations = LOCAL_OPERATIONS | BLOCKED_OPERATIONS
    if set(operations) != expected_operations:
        errors.append(f"OpenAPI operation drift: {sorted(set(operations) ^ expected_operations)}")

    public = {
        "createEmailSignup",
        "verifySignupEmail",
        "resendSignupEmailVerification",
        "loginWithEmail",
    }
    for operation_id in public:
        if operations.get(operation_id, {}).get("security") != []:
            errors.append(f"{operation_id} must be explicitly public")
    cookie_mutation_security = [
        {"refreshCookie": [], "csrfCookie": []},
        {"localRefreshCookie": [], "localCsrfCookie": []},
    ]
    refresh_operation = operations.get("refreshAuthentication", {})
    if refresh_operation.get("security") != cookie_mutation_security:
        errors.append("refreshAuthentication must require refresh+CSRF cookies")
    refresh_parameter_refs = {row.get("$ref") for row in refresh_operation.get("parameters", []) if isinstance(row, dict)}
    if not {"#/components/parameters/OriginHeader", "#/components/parameters/CsrfTokenHeader"} <= refresh_parameter_refs:
        errors.append("refreshAuthentication must require Origin and X-CSRF-Token headers")
    if refresh_operation.get("x-race-clock") != "PostgreSQL primary clock_timestamp under session and token row lock":
        errors.append("refreshAuthentication race clock drift")
    logout_operation = operations.get("logoutCurrentSession", {})
    if logout_operation.get("security") != cookie_mutation_security + [{}]:
        errors.append("logoutCurrentSession must allow only the explicit cookie-less 204 retry branch")
    logout_parameter_refs = {row.get("$ref") for row in logout_operation.get("parameters", []) if isinstance(row, dict)}
    if logout_parameter_refs != {
        "#/components/parameters/OptionalIdempotencyKey",
        "#/components/parameters/OriginHeader",
        "#/components/parameters/OptionalCsrfTokenHeader",
    }:
        errors.append("logoutCurrentSession optional retry parameter contract drift")
    logout_retry = logout_operation.get("x-logout-retry-policy", {})
    if logout_retry != {
        "origin": "exact allowed Origin is mandatory for every branch, including cookie-less retry",
        "bothAuthCookiesAbsent": "204 with both exact clear headers; no CSRF, idempotency, DB, or audit mutation required",
        "partialPair": "403 CSRF_FORBIDDEN",
        "completePairInvalidSession": "valid Origin and CSRF but unknown, expired, or revoked refresh returns 401 AUTH_SESSION_INVALID",
        "successfulReplay": "same idempotency key replays 204 and exact clear without another audit mutation",
        "clearAttributes": "selected profile issuance attributes plus Max-Age=0 and epoch Expires",
        "exactErrors": {
            "origin": {"status": 403, "code": "AUTH_ORIGIN_FORBIDDEN"},
            "csrfOrPartialPair": {"status": 403, "code": "CSRF_FORBIDDEN"},
            "invalidSession": {"status": 401, "code": "AUTH_SESSION_INVALID"},
            "idempotencyConflict": {"status": 409, "code": "IDEMPOTENCY_KEY_REUSED"},
            "dependency": {"status": 503, "code": "AUTH_DEPENDENCY_UNAVAILABLE"},
        },
    }:
        errors.append("logout cookie-less retry/exact clear contract drift")
    for operation_id in ("refreshAuthentication", "logoutCurrentSession"):
        operation = operations.get(operation_id, {})
        parameter_refs = {
            row.get("$ref") for row in operation.get("parameters", []) if isinstance(row, dict)
        }
        if "403" not in operation.get("responses", {}):
            errors.append(f"{operation_id} must specify fail-closed CSRF 403")
    if "401" not in logout_operation.get("responses", {}):
        errors.append("logoutCurrentSession must expose invalid-session 401")
    login = operations.get("loginWithEmail", {})
    login_parameter_refs = {
        row.get("$ref") for row in login.get("parameters", []) if isinstance(row, dict)
    }
    if "#/components/parameters/OriginHeader" not in login_parameter_refs:
        errors.append("loginWithEmail must require exact Origin before issuing cookies")
    if "403" not in login.get("responses", {}):
        errors.append("loginWithEmail must specify fail-closed Origin 403")
    set_cookie_expectations = {
        "loginWithEmail": ("__Host-feelm_refresh", "__Host-feelm_csrf", "feelm_local_refresh", "feelm_local_csrf", "non-Secure", "Domain"),
        "refreshAuthentication": ("refresh", "signed CSRF", "함께 회전"),
        "logoutCurrentSession": ("Path=/", "Max-Age=0", "Expires=Thu"),
    }
    for operation_id, phrases in set_cookie_expectations.items():
        responses = operations.get(operation_id, {}).get("responses", {})
        success = responses.get("200", responses.get("204", {}))
        description = success.get("headers", {}).get("Set-Cookie", {}).get("description", "")
        if any(phrase not in description for phrase in phrases):
            errors.append(f"{operation_id} cookie issuance/rotation/clear contract drift")
        contract = success.get("x-set-cookie-contract", {})
        expected_action = {"loginWithEmail": "issue", "refreshAuthentication": "rotate", "logoutCurrentSession": "clear"}[operation_id]
        if contract != {"repeatedHeader": "Set-Cookie", "count": 2, "commaFolding": "forbidden", "action": expected_action, "profiles": ["productionHttps", "localHttpLoopbackOnly"]}:
            errors.append(f"{operation_id} repeated Set-Cookie semantic drift")
    for operation_id in LOCAL_OPERATIONS:
        operation = operations.get(operation_id, {})
        if operation.get("x-implementation-status") != "AUTHORIZED_LOCAL_CONTRACT":
            errors.append(f"{operation_id} must be authorized only for the local contract")
        refs = set(operation.get("x-local-decision-authority", []))
        if not refs or not refs <= DECISIONS:
            errors.append(f"{operation_id} local decision authority drift")
        if operation.get("x-decision-required"):
            errors.append(f"{operation_id} still carries unresolved decision gating")
    protected = LOCAL_OPERATIONS - public - {"refreshAuthentication"}
    for operation_id in protected:
        refs = set(operations.get(operation_id, {}).get("x-local-decision-authority", []))
        if "DN-C4A-001" not in refs:
            errors.append(f"protected {operation_id} must retain DN-C4A-001 local authority")
    restart = operations.get("restartOnboarding", {})
    if restart.get("x-implementation-status") != "BLOCKED":
        errors.append("restartOnboarding must remain explicitly BLOCKED")
    if set(restart.get("x-decision-required", [])) != {"DN-C4A-001", "DN-C4A-004"}:
        errors.append("restartOnboarding blocked decision set drift")

    schemas = document.get("components", {}).get("schemas", {})
    schemes = document.get("components", {}).get("securitySchemes", {})
    expected_cookie_names = {
        "refreshCookie": "__Host-feelm_refresh", "csrfCookie": "__Host-feelm_csrf",
        "localRefreshCookie": "feelm_local_refresh", "localCsrfCookie": "feelm_local_csrf",
    }
    for scheme_name, cookie_name in expected_cookie_names.items():
        scheme = schemes.get(scheme_name, {})
        if scheme.get("type") != "apiKey" or scheme.get("in") != "cookie" or scheme.get("name") != cookie_name:
            errors.append(f"OpenAPI cookie security scheme drift: {scheme_name}")
    for schema_name in ("CreateEmailSignupRequest", "EmailLoginRequest"):
        minimum = schemas.get(schema_name, {}).get("properties", {}).get("password", {}).get("minLength")
        if minimum != 15:
            errors.append(f"{schema_name}.password minLength must match packet value 15")
    pending = schemas.get("PendingEmailSignup", {})
    pending_required = {
        "signupId", "membershipStatus", "emailMasked", "deliveryStatus",
        "verificationExpiresAt", "resendAvailableAt", "revision",
    }
    if set(pending.get("required", [])) != pending_required:
        errors.append("PendingEmailSignup generic 202 shape drift")
    if pending.get("properties", {}).get("revision", {}).get("const") != 1:
        errors.append("PendingEmailSignup generic revision must remain 1")
    verification_description = (
        schemas.get("VerifyEmailRequest", {})
        .get("properties", {})
        .get("verificationSecret", {})
        .get("description", "")
    )
    if "fragment" not in verification_description or "history.replaceState" not in verification_description:
        errors.append("verification secret transport must remain fragment-to-POST and clear browser history")
    signup_policy = operations.get("createEmailSignup", {}).get("x-enumeration-response-policy", {})
    if signup_policy.get("responseFloor") != "calibrated commonFloor=max(p99 supported PHC workloads)+25ms":
        errors.append("signup generic response must reserve the calibrated supported-PHC floor")
    if signup_policy.get("responseJitterMilliseconds") != {"minimum": 0, "maximum": 75}:
        errors.append("signup generic response jitter must remain 0..75ms")
    if signup_policy.get("duplicateOrUnknownHandle") != "persistent decoy opaque UUID with no account, secret, or mail":
        errors.append("signup duplicate handle must be a persisted decoy")
    if signup_policy.get("returnedHandleRetentionHours") != 24:
        errors.append("actual/decoy returned handles must retain follow-up state for 24 hours")
    if signup_policy.get("followUpState") != "same persisted cooldown, attempts, expiry, and revision for actual and decoy":
        errors.append("actual/decoy follow-up state contract drift")
    if signup_policy.get("stableHandleEntity") != "EMAIL_SIGNUP_PUBLIC_FLOW":
        errors.append("signupId is not fixed to EMAIL_SIGNUP_PUBLIC_FLOW")
    if signup_policy.get("currentChallenge") != "versioned internal current_challenge_id for REAL only":
        errors.append("versioned current challenge boundary drift")
    if signup_policy.get("flowExpires") != "createdAt plus 24 hours without resend extension":
        errors.append("public flow TTL/extension boundary drift")
    if signup_policy.get("pendingRecovery") != "only after prior REAL flow is EXPIRED and before pendingPurgeAt; preserve nickname and OTT set":
        errors.append("PENDING re-signup recovery boundary drift")
    if signup_policy.get("pendingPurge") != "account createdAt plus 30 days without re-signup extension":
        errors.append("PENDING purge boundary drift")
    expected_signup_codes = {
        "malformed": {"status": 400, "code": "VALIDATION_ERROR"},
        "idempotencyConflict": {"status": 409, "code": "IDEMPOTENCY_KEY_REUSED"},
        "stateConflict": {"status": 409, "code": "AUTH_STATE_CONFLICT"},
        "throttled": {"status": 429, "code": "AUTH_FLOW_THROTTLED"},
        "dependency": {"status": 503, "code": "AUTH_DEPENDENCY_UNAVAILABLE"},
    }
    if signup_policy.get("exactPublicCodes") != expected_signup_codes:
        errors.append("signup exact public code mapping drift")
    login_timing = operations.get("loginWithEmail", {}).get("x-enumeration-timing-policy", {})
    if login_timing != {
        "exactPublicCodes": {
            "malformed": {"status": 400, "code": "VALIDATION_ERROR"},
            "invalidCredentials": {"status": 401, "code": "INVALID_CREDENTIALS"},
            "originFailure": {"status": 403, "code": "AUTH_ORIGIN_FORBIDDEN"},
            "pendingAfterCorrectPassword": {"status": 403, "code": "EMAIL_VERIFICATION_REQUIRED"},
            "throttled": {"status": 429, "code": "AUTH_FLOW_THROTTLED"},
            "dependency": {"status": 503, "code": "AUTH_DEPENDENCY_UNAVAILABLE"},
        },
        "originFailure": {"status": 403, "code": "AUTH_ORIGIN_FORBIDDEN", "beforeCredentialWork": True},
        "unknownEmail": "verify fixed dummy PHC hash with current production Argon2id parameters",
        "wrongPassword": "verify stored PHC hash",
        "commonFailure": {"status": 401, "code": "INVALID_CREDENTIALS", "timing": "calibrated commonFloor=max(p99 supported PHC workloads)+25ms plus CSPRNG 0..75ms jitter"},
        "pendingAfterCorrectPassword": {"status": 403, "code": "EMAIL_VERIFICATION_REQUIRED"},
    }:
        errors.append("login dummy Argon2/timing/public code boundary drift")
    expected_verify_codes = {
        "malformed": {"status": 400, "code": "VALIDATION_ERROR"},
        "wrong": {"status": 400, "code": "VERIFICATION_INVALID"},
        "unknownTerminalOrExpired": {"status": 400, "code": "SIGNUP_FLOW_INVALID_OR_EXPIRED"},
        "attemptsExhausted": {"status": 429, "code": "VERIFICATION_ATTEMPTS_EXHAUSTED"},
        "throttled": {"status": 429, "code": "AUTH_FLOW_THROTTLED"},
        "idempotencyConflict": {"status": 409, "code": "IDEMPOTENCY_KEY_REUSED"},
        "stateConflict": {"status": 409, "code": "AUTH_STATE_CONFLICT"},
        "dependency": {"status": 503, "code": "AUTH_DEPENDENCY_UNAVAILABLE"},
    }
    for operation_id in ("verifySignupEmail", "resendSignupEmailVerification"):
        policy = operations.get(operation_id, {}).get("x-generic-handle-policy", {})
        if policy.get("persistence") != "returned actual and decoy rows exist for 24 hours":
            errors.append(f"{operation_id} lacks persisted actual/decoy handle state")
        if policy.get("concurrency") != "row lock gives verify or resend exactly one transition winner":
            errors.append(f"{operation_id} lacks concurrent verify/resend single-winner rule")
        if policy.get("unknownHandle") != "generic invalid-or-expired response without account detail":
            errors.append(f"{operation_id} unknown handle behavior is not generic")
    if operations.get("verifySignupEmail", {}).get("x-generic-handle-policy", {}).get("exactPublicCodes") != expected_verify_codes:
        errors.append("verify actual/decoy exact public code mapping drift")
    resend_policy = operations.get("resendSignupEmailVerification", {}).get("x-generic-handle-policy", {})
    if resend_policy.get("stableSignupId") != "unchanged; REAL current_challenge_id/version and flow revision advance":
        errors.append("resend stable signupId/versioned challenge boundary drift")
    expected_resend_codes = {
        "accepted": {"status": 202},
        "invalidOrExpired": {"status": 400, "code": "SIGNUP_FLOW_INVALID_OR_EXPIRED"},
        "throttled": {"status": 429, "code": "AUTH_FLOW_THROTTLED"},
        "idempotencyConflict": {"status": 409, "code": "IDEMPOTENCY_KEY_REUSED"},
        "stateConflict": {"status": 409, "code": "AUTH_STATE_CONFLICT"},
        "dependency": {"status": 503, "code": "AUTH_DEPENDENCY_UNAVAILABLE"},
    }
    if resend_policy.get("exactPublicCodes") != expected_resend_codes:
        errors.append("resend actual/decoy exact public code mapping drift")
    exact_response_refs = {
        ("createEmailSignup", "400"): "#/components/responses/PublicSignupBadRequest",
        ("createEmailSignup", "409"): "#/components/responses/PublicAuthConflict",
        ("createEmailSignup", "429"): "#/components/responses/AuthThrottled",
        ("createEmailSignup", "503"): "#/components/responses/AuthDependencyUnavailable",
        ("verifySignupEmail", "400"): "#/components/responses/PublicVerificationBadRequest",
        ("verifySignupEmail", "409"): "#/components/responses/PublicAuthConflict",
        ("verifySignupEmail", "429"): "#/components/responses/AuthFlowRateLimited",
        ("verifySignupEmail", "503"): "#/components/responses/AuthDependencyUnavailable",
        ("resendSignupEmailVerification", "400"): "#/components/responses/PublicResendBadRequest",
        ("resendSignupEmailVerification", "409"): "#/components/responses/PublicAuthConflict",
        ("resendSignupEmailVerification", "429"): "#/components/responses/AuthThrottled",
        ("resendSignupEmailVerification", "503"): "#/components/responses/AuthDependencyUnavailable",
        ("loginWithEmail", "400"): "#/components/responses/LoginBadRequest",
        ("loginWithEmail", "401"): "#/components/responses/InvalidCredentials",
        ("loginWithEmail", "403"): "#/components/responses/LoginForbidden",
        ("loginWithEmail", "429"): "#/components/responses/AuthThrottled",
        ("loginWithEmail", "503"): "#/components/responses/AuthDependencyUnavailable",
        ("refreshAuthentication", "401"): "#/components/responses/AuthSessionInvalid",
        ("refreshAuthentication", "403"): "#/components/responses/AuthMutationForbidden",
        ("refreshAuthentication", "409"): "#/components/responses/RefreshRaceConflict",
        ("refreshAuthentication", "429"): "#/components/responses/AuthThrottled",
        ("refreshAuthentication", "503"): "#/components/responses/AuthDependencyUnavailable",
        ("logoutCurrentSession", "401"): "#/components/responses/AuthSessionInvalid",
        ("logoutCurrentSession", "403"): "#/components/responses/AuthMutationForbidden",
        ("logoutCurrentSession", "409"): "#/components/responses/IdempotencyConflict",
        ("logoutCurrentSession", "503"): "#/components/responses/AuthDependencyUnavailable",
    }
    for (operation_id, status), expected_ref in exact_response_refs.items():
        actual_ref = operations.get(operation_id, {}).get("responses", {}).get(status, {}).get("$ref")
        if actual_ref != expected_ref:
            errors.append(f"{operation_id} {status} exact ErrorResponse ref drift")
    exact_code_schema = {
        "PublicSignupBadRequestError": {"const": "VALIDATION_ERROR"},
        "PublicVerificationBadRequestError": {"enum": ["VALIDATION_ERROR", "VERIFICATION_INVALID", "SIGNUP_FLOW_INVALID_OR_EXPIRED"]},
        "PublicResendBadRequestError": {"enum": ["VALIDATION_ERROR", "SIGNUP_FLOW_INVALID_OR_EXPIRED"]},
        "PublicAuthConflictError": {"enum": ["IDEMPOTENCY_KEY_REUSED", "AUTH_STATE_CONFLICT"]},
        "LoginBadRequestError": {"const": "VALIDATION_ERROR"},
        "AuthFlowRateLimitedError": {"enum": ["VERIFICATION_ATTEMPTS_EXHAUSTED", "AUTH_FLOW_THROTTLED"]},
        "AuthThrottledError": {"const": "AUTH_FLOW_THROTTLED"},
        "AuthDependencyUnavailableError": {"const": "AUTH_DEPENDENCY_UNAVAILABLE"},
        "InvalidCredentialsError": {"const": "INVALID_CREDENTIALS"},
        "LoginForbiddenError": {"enum": ["AUTH_ORIGIN_FORBIDDEN", "EMAIL_VERIFICATION_REQUIRED"]},
        "AuthSessionInvalidError": {"const": "AUTH_SESSION_INVALID"},
        "AuthMutationForbiddenError": {"enum": ["AUTH_ORIGIN_FORBIDDEN", "CSRF_FORBIDDEN"]},
        "RefreshRaceConflictError": {"const": "REFRESH_RACE_RETRY_NEW_COOKIE"},
        "IdempotencyConflictError": {"const": "IDEMPOTENCY_KEY_REUSED"},
    }
    for schema_name, expected_constraint in exact_code_schema.items():
        all_of = schemas.get(schema_name, {}).get("allOf", [])
        constraint = all_of[1].get("properties", {}).get("code", {}) if len(all_of) == 2 else {}
        if any(constraint.get(key) != value for key, value in expected_constraint.items()):
            errors.append(f"{schema_name}.code exact enum/const drift")
    public_conflict_codes = set(
        exact_code_schema["PublicAuthConflictError"]["enum"]
    )
    for operation_id, policy_key in (
        ("createEmailSignup", "x-enumeration-response-policy"),
        ("verifySignupEmail", "x-generic-handle-policy"),
        ("resendSignupEmailVerification", "x-generic-handle-policy"),
    ):
        operation_codes = operations.get(operation_id, {}).get(policy_key, {}).get("exactPublicCodes", {})
        declared_409_codes = {
            mapping.get("code")
            for mapping in operation_codes.values()
            if isinstance(mapping, dict) and mapping.get("status") == 409
        }
        if declared_409_codes != public_conflict_codes:
            errors.append(f"{operation_id} 409 codes must exactly match PublicAuthConflictError without a superset")
    delivery = schemas.get("VerificationDeliveryState", {})
    if "revision" not in delivery.get("required", []) or delivery.get("properties", {}).get("revision", {}).get("minimum") != 2:
        errors.append("resend response must expose stable-flow revision, not challenge ID")
    preference_array = schemas.get("ReplaceOnboardingPreferencesRequest", {}).get("properties", {}).get("preferences", {})
    if preference_array.get("x-unique-by") != "movieId" or preference_array.get("maxItems") != 10:
        errors.append("onboarding JSON movieId uniqueness/maxItems drift")
    complete_schema = schemas.get("CompleteOnboardingRequest", {})
    if set(complete_schema.get("required", [])) != {"completionMode", "expectedPreferenceCount"}:
        errors.append("onboarding completion count binding drift")
    complete_serialized = json.dumps(complete_schema, sort_keys=True)
    if '"const": "SKIPPED"' not in complete_serialized or '"const": 0' not in complete_serialized:
        errors.append("onboarding SKIPPED JSON if/then drift")
    onboarding_state = schemas.get("OnboardingState", {})
    onboarding_summary = schemas.get("OnboardingSummary", {})
    if onboarding_state.get("x-count-invariant") != "preferenceCount = likeCount + dislikeCount = locked active preference row count":
        errors.append("OnboardingState count equality invariant drift")
    state_serialized = json.dumps(onboarding_state, sort_keys=True)
    summary_serialized = json.dumps(onboarding_summary, sort_keys=True)
    for name, serialized_state in (("OnboardingState", state_serialized), ("OnboardingSummary", summary_serialized)):
        for phrase in ('"NOT_STARTED"', '"SKIPPED"', '"COMPLETED"', '"const": 0', '"maximum": 10'):
            if phrase not in serialized_state:
                errors.append(f"{name} status/count if-then missing {phrase}")
    ott_request = schemas.get("ReplaceOttSubscriptionsRequest", {})
    ott_response = schemas.get("MyOttSubscriptionSet", {})
    if '"const": "SKIPPED"' not in json.dumps(ott_request, sort_keys=True) or '"maxItems": 0' not in json.dumps(ott_request, sort_keys=True):
        errors.append("OTT SKIPPED request providerIds if/then drift")
    if '"maxItems": 0' not in json.dumps(ott_response, sort_keys=True):
        errors.append("OTT non-configured/skipped response cardinality drift")

    decision_refs = walk_decision_extensions(document, errors) | {
        str(row.get("decisionRequired")) for row in blocked
    }
    if decision_refs != DECISIONS:
        errors.append(f"OpenAPI decision coverage drift: {sorted(decision_refs)}")

    serialized = json.dumps(document, ensure_ascii=False)
    forbidden_keys = (
        '"password": "',
        '"verificationSecret": "',
        '"accessToken": "',
        '"refreshToken": "',
        '"clientSecret": "',
    )
    for pattern in forbidden_keys:
        if pattern in serialized:
            errors.append(f"OpenAPI contains a raw secret example/value: {pattern}")
    return set(operations), decision_refs


def validate_trace(
    errors: list[str],
    business_rules: set[str],
    operations: set[str],
) -> None:
    with (ROOT / "traceability/requirements.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        rows = list(csv.DictReader(handle))
    if {row["requirement_id"] for row in rows} != REQUIREMENTS:
        errors.append("trace requirement rows drift")
    by_requirement = {row["requirement_id"]: row for row in rows}
    semantic_trace_minimums = {
        "REQ-C4A-DELIVERY": {
            "entities": {"EMAIL_SIGNUP_PUBLIC_FLOW", "EMAIL_VERIFICATION_CHALLENGE", "VERIFICATION_DELIVERY_MATERIAL", "PUBLIC_AUTH_IDEMPOTENCY_RECORD", "PUBLIC_AUTH_IDEMPOTENCY_SCOPE_ALIAS", "PUBLIC_AUTH_IDEMPOTENCY_REQUEST_HMAC_ALIAS", "PENDING_SIGNUP_RECOVERY_ATTEMPT", "DOMAIN_OUTBOX"},
            "acceptance_ids": {"AC-C4A-077", "AC-C4A-078", "AC-C4A-079", "AC-C4A-080", "AC-C4A-083", "AC-C4A-085"},
        },
        "REQ-C4A-AUTH-HARDENING": {
            "acceptance_ids": {"AC-C4A-081", "AC-C4A-082", "AC-C4A-083", "AC-C4A-085"},
        },
        "REQ-C4A-JSON-CONSTRAINTS": {
            "acceptance_ids": {"AC-C4A-084"},
            "operation_ids": {"replaceOnboardingPreferences", "completeOnboarding", "replaceMyOttSubscriptions"},
        },
    }
    for requirement_id, fields in semantic_trace_minimums.items():
        row = by_requirement.get(requirement_id, {})
        for field, expected in fields.items():
            missing = expected - split_refs(row.get(field))
            if missing:
                errors.append(f"{requirement_id} semantic trace missing {field}: {sorted(missing)}")
    allowed_operations = operations | {"listOttProviders"}
    seen = {
        "decision": set(),
        "rule": set(),
        "scenario": set(),
        "screen": set(),
        "operation": set(),
        "entity": set(),
        "acceptance": set(),
        "task": set(),
        "test": set(),
    }
    local_seen = {"operation": set(), "entity": set(), "acceptance": set(), "screen": set()}
    blocked_seen = {"operation": set(), "entity": set(), "acceptance": set(), "screen": set()}
    for line, row in enumerate(rows, start=2):
        row_status = row["status"]
        if row["requirement_id"] == "REQ-C4A-BLOCKED-EXTENSIONS":
            if row_status != "BLOCKED_EXTENSION":
                errors.append(f"trace line {line} blocked extension status drift")
            status_seen = blocked_seen
        else:
            if row_status != "AUTHORIZED_LOCAL_PROFILE":
                errors.append(f"trace line {line} local authority status drift")
            status_seen = local_seen
        checks = (
            ("decision", split_refs(row["decision_ids"]), DECISIONS),
            ("rule", split_refs(row["business_rule_ids"]), business_rules),
            ("scenario", split_refs(row["scenario_ids"]), SCENARIOS),
            ("screen", split_refs(row["screen_ids"]), SCREENS),
            ("operation", split_refs(row["operation_ids"]), allowed_operations),
            ("entity", split_refs(row["entities"]), ENTITIES),
            ("acceptance", split_refs(row["acceptance_ids"]), ACCEPTANCE),
            ("task", split_refs(row["task_ids"]), TASKS),
            ("test", split_refs(row["test_ids"]), TESTS),
        )
        for label, references, known in checks:
            missing = references - known
            if missing:
                errors.append(f"trace line {line} unknown {label}: {sorted(missing)}")
            if label in seen:
                seen[label].update(references)
            if label in status_seen:
                status_seen[label].update(references)
    if local_seen["operation"] - {"listOttProviders"} != LOCAL_OPERATIONS or "listOttProviders" not in local_seen["operation"]:
        errors.append(f"trace local operation authority drift: {sorted(local_seen['operation'])}")
    if blocked_seen["operation"] != BLOCKED_OPERATIONS:
        errors.append(f"trace blocked operation authority drift: {sorted(blocked_seen['operation'])}")
    if local_seen["acceptance"] != LOCAL_ACCEPTANCE:
        errors.append(f"trace local AC authority drift: missing={sorted(LOCAL_ACCEPTANCE - local_seen['acceptance'])}, extra={sorted(local_seen['acceptance'] - LOCAL_ACCEPTANCE)}")
    if blocked_seen["acceptance"] != BLOCKED_ACCEPTANCE:
        errors.append(f"trace blocked AC authority drift: {sorted(blocked_seen['acceptance'])}")
    if local_seen["screen"] != SCREENS or blocked_seen["screen"]:
        errors.append("trace seven-screen local authority or blocked-screen boundary drift")
    if local_seen["entity"] != LOCAL_ENTITIES:
        errors.append(f"trace local entity authority drift: missing={sorted(LOCAL_ENTITIES - local_seen['entity'])}, extra={sorted(local_seen['entity'] - LOCAL_ENTITIES)}")
    if blocked_seen["entity"] != BLOCKED_ENTITIES:
        errors.append(f"trace blocked entity boundary drift: {sorted(blocked_seen['entity'])}")
    for label, expected in (
        ("decision", DECISIONS),
        ("rule", business_rules),
        ("scenario", SCENARIOS),
        ("screen", SCREENS),
        ("operation", operations),
        ("entity", ENTITIES),
        ("acceptance", ACCEPTANCE),
        ("task", TASKS - {"TASK-C4A-001"}),
    ):
        missing = expected - seen[label]
        if missing:
            errors.append(f"untraced {label}: {sorted(missing)}")


def main() -> int:
    errors: list[str] = []
    found_files = {
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if path.is_file() and path.name != "__pycache__"
    }
    missing_files = EXPECTED_FILES - found_files
    if missing_files:
        errors.append(f"missing contract files: {sorted(missing_files)}")

    markdown = [path for path in ROOT.rglob("*.md") if path.is_file()]
    for path in markdown:
        if STATUS not in path.read_text(encoding="utf-8-sig"):
            errors.append(f"{path.relative_to(ROOT)} missing {STATUS} status")

    decision_text = text("decision-needed.md")
    decision_ids = ids(r"DN-C4A-\d{3}", decision_text)
    if decision_ids != DECISIONS:
        errors.append(f"decision IDs drift: {sorted(decision_ids)}")
    for decision_id in DECISIONS:
        row = next(
            (line for line in decision_text.splitlines() if f"`{decision_id}`" in line),
            "",
        )
        if "`APPROVED_LOCAL_PROFILE_2026-08-30`" not in row:
            errors.append(f"{decision_id} local-profile decision status drift")
    if "Local profile 승인 현황: `5/5`" not in decision_text:
        errors.append("decision local-profile approval count must remain 5/5")

    scope = text("00-product-scope.md")
    scenario_ids = ids(r"SCN-C4A-\d{3}", scope)
    if scenario_ids != SCENARIOS:
        errors.append(f"scenario IDs drift: {sorted(scenario_ids)}")
    screen_text = text("ui/screen-contracts.md") + text("00-product-scope.md")
    screen_ids = ids(r"SCR-C4A-\d{3}", screen_text)
    if screen_ids != SCREENS:
        errors.append(f"screen IDs drift: {sorted(screen_ids)}")
    rules_text = text("02-business-rules.md")
    business_rules = ids(r"BR-C4A-\d{3}", rules_text)
    if len(business_rules) < 45:
        errors.append(f"expected at least 45 business rules, found {len(business_rules)}")
    acceptance_text = text("testing/acceptance-tests.md")
    acceptance_ids = ids(r"AC-C4A-\d{3}", acceptance_text)
    if acceptance_ids != ACCEPTANCE:
        errors.append(f"acceptance IDs drift: {sorted(acceptance_ids)}")
    required_acceptance_manifests = (
        "LOCAL IMPLEMENTATION AC: `AC-C4A-001..033`, `AC-C4A-035..044`, `AC-C4A-046..066`, `AC-C4A-068..085`",
        "PRODUCTION NEGATIVE BRANCH AC: `AC-C4A-007`, `AC-C4A-061`, `AC-C4A-073`, `AC-C4A-081`, `AC-C4A-082`",
        "BLOCKED EXTENSION AC: `AC-C4A-034`, `AC-C4A-045`, `AC-C4A-067`",
    )
    for manifest in required_acceptance_manifests:
        if manifest not in acceptance_text:
            errors.append(f"acceptance authority manifest drift: {manifest}")
    if not PRODUCTION_NEGATIVE_ACCEPTANCE <= LOCAL_ACCEPTANCE:
        errors.append("production-negative AC must remain executable local fail-closed tests")

    validate_tasks(errors)
    operations, _ = validate_openapi(errors)
    validate_trace(errors, business_rules, operations)

    fixtures = text("testing/fixtures.md")
    if "Mailpit" not in fixtures or "raw secret" not in fixtures:
        errors.append("fixtures must retain Mailpit and raw-secret boundaries")
    secret_patterns = (
        re.compile(r"eyJ[A-Za-z0-9_-]{20,}"),
        re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
        re.compile(r"(?i)(client_secret|smtp_password|api_key)\s*[:=]\s*[^\s<]+"),
    )
    all_contract_text = "\n".join(path.read_text(encoding="utf-8-sig") for path in ROOT.rglob("*") if path.is_file() and path.suffix in {".md", ".yaml", ".csv", ".py"})
    for pattern in secret_patterns:
        if pattern.search(all_contract_text):
            errors.append(f"possible raw secret detected by {pattern.pattern}")
    contract_content_without_validators = "\n".join(
        path.read_text(encoding="utf-8-sig")
        for path in ROOT.rglob("*")
        if path.is_file() and path.suffix in {".md", ".yaml", ".csv"}
    )
    for stale_phrase in (
        "non-persistent synthetic UUID",
        "비영속 synthetic",
        "synthetic handle은\n  저장하지",
        "synthetic signupId는 저장하지",
        "GENERIC_SIGNUP_HANDLE=SYNTHETIC_UUID",
        "replacement 관계를 영구 보존",
        "cookie-less logout retry | Origin/CSRF/idempotency 없이",
        "두 cookie가 모두 없는 retry는 Origin/CSRF 없이",
        "currentFlowChallenge",
    ):
        if stale_phrase in contract_content_without_validators:
            errors.append(f"contract retains stale unsafe boundary {stale_phrase!r}")

    required_phrases = {
        "00-product-scope.md": ["Mailpit", "Rating", "Google", "REC-EV-011", "alpha 0.2"],
        "01-glossary-and-policies.md": ["K10", "ranking alpha", "SKIPPED", "Refresh Token Lineage", "feelm_local_refresh", "MAIL_IDENTITY=5/h+10/d", "PUBLIC_AUTH_IDEMPOTENCY_RECORD", "offline password oracle"],
        "02-business-rules.md": ["GOOGLE/KAKAO/NAVER", "raw 저장", "UI_READY", "subjectHmac", "5초", "pinned HTTPS frontend origin", "Redis Function", "PUBLIC_AUTH_IDEMPOTENCY_RECORD", "PENDING_SIGNUP_RECOVERY_ATTEMPT", "request_hmac_key_version", "pending_purge_at=created_at+30d", "clock_timestamp()", "cookie가 모두 없는 retry", "AES-256-GCM", "RS256", "deferred constraint trigger"],
        "03-state-machines.md": ["DISABLED", "SUPERSEDED", "SKIPPED", "history.replaceState", "calibrated common floor", "REVOKED_FAMILY", "X-Forwarded-Host", "persisted decoy", "current_challenge_id", "SIGNUP_FLOW_INVALID_OR_EXPIRED", "VERIFICATION_DELIVERY_MATERIAL", "LOST_REPLAY", "TLS provider request wire"],
        "data/logical-erd.md": ["AUTH_REFRESH_TOKEN", "Access JWT logout 의미", "subject_hmac", "DECOY stable public-flow", "server UTC clock", "EMAIL_SIGNUP_PUBLIC_FLOW", "pending_purge_at", "VERIFICATION_DELIVERY_MATERIAL", "PUBLIC_AUTH_IDEMPOTENCY_RECORD", "PUBLIC_AUTH_IDEMPOTENCY_SCOPE_ALIAS", "PUBLIC_AUTH_IDEMPOTENCY_REQUEST_HMAC_ALIAS", "PENDING_SIGNUP_RECOVERY_ATTEMPT"],
        "data/data-dictionary.md": ["AUTH_REFRESH_TOKEN", "DECOY row", "SOCIAL_LINK_TRANSACTION", "public_flow_expires_at", "EMAIL_SIGNUP_PUBLIC_FLOW", "challenge_version", "family_terminal_at", "identity_hmac_key_version", "nickname_normalization_version", "request_hmac_key_version", "offline oracle"],
        "testing/acceptance-tests.md": ["AC-C4A-061", "AC-C4A-068", "AC-C4A-076", "AC-C4A-085", "0.000253, 0.002783", "hostile Host/Forwarded/X-Forwarded-Host", "Redis unavailable/protocol mismatch", "feelm_local_", "AES-256-GCM", "maxItems:10", "TLS provider request wire"],
        "product-decision-packet.md": ["COOKIE_MUTATION_AUTH=REFRESH_PLUS_ORIGIN_PLUS_CSRF", "REC-EV-011", "VERIFICATION_LINK_ORIGIN=PINNED_CONFIG_ONLY", "RATE_LIMIT_AUTHORITY=SHARED_ATOMIC_FAIL_CLOSED", "PUBLIC_FLOW_MODEL=STABLE_SIGNUP_ID_VERSIONED_CURRENT_CHALLENGE", "COOKIELESS_LOGOUT_RETRY=204_CLEAR_NO_MUTATION", "DELIVERY_MATERIAL=AES256_GCM_SINGLE_USE_VERSIONED_KEY", "PUBLIC_AUTH_IDEMPOTENCY=SEPARATE_LEDGER_KEYED_REQUEST_HMAC", "RECOVERY_LINEARIZATION=PERSISTED_ADMISSION_PRIOR_FLOW"],
    }
    for path, phrases in required_phrases.items():
        value = text(path)
        for phrase in phrases:
            if phrase not in value:
                errors.append(f"{path} missing required boundary phrase {phrase!r}")

    if errors:
        print("C4A contract validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(
        "C4A contract validation passed: "
        f"{len(LOCAL_OPERATIONS)} local operations, {len(SCREENS)} local screens, "
        f"{len(LOCAL_ACCEPTANCE)} local acceptance cases, {len(BLOCKED_ACCEPTANCE)} blocked extension cases; "
        "main OpenAPI local merge is fixed; production activation remains blocked."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
