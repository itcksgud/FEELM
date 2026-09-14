"""Independently audit and atomically publish the Service-v1 B1 r4 delivery review."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path, PurePosixPath
import socket
import stat
import sys
import tarfile
import traceback
from typing import Any, Callable, Mapping, Sequence


RUN_ID = "b1-gbt120-s339-v1-r4-server5c20g-t28800"
PROFILE_ID = "ec2-8vcpu32g-local5-fit20g-t28800-v1"
MANIFEST_SCHEMA = "feelm-service-v1-b1-r4-server-delivery/2"
REVIEW_SCHEMA = "feelm-service-v1-b1-r4-server-delivery-review/1"
IMPLEMENTATION_SCHEMA = "feelm-service-v1-b1-r4-implementation-review/1"
ANCESTOR_SCHEMA = "feelm-service-v1-b1-r4-ancestor-evidence/1"
MANIFEST_NAME = RUN_ID + "-delivery-manifest.json"
REVIEW_NAME = RUN_ID + "-delivery-manifest-result-review.json"
REVIEW_FAILURE_NAME = RUN_ID + "-delivery-manifest-result-review-failure.json"
DESIGN_REVIEW_NAME = RUN_ID + "-design-result-review.json"
IMPLEMENTATION_REVIEW_NAME = RUN_ID + "-implementation-result-review.json"
IMPLEMENTATION_REVIEW_FAILURE_NAME = RUN_ID + "-implementation-result-review-failure.json"
DELIVERY_REVIEW_COMPLETED_CHILDREN = tuple(sorted((
    DESIGN_REVIEW_NAME,
    IMPLEMENTATION_REVIEW_NAME,
    MANIFEST_NAME,
)))
PUBLICATION_SOURCE = "scripts/service_v1_b1_r4_publication.py"
AUDITOR_SOURCE = "scripts/audit_service_v1_b1_r4_server_delivery.py"
IMAGE_ID = "sha256:de611d62db982bb833b0737a7ddd72a7cd49ab1a7dc9128669cdabae39f46df8"
IMAGE_ARCHIVE_BYTES = 936_486_400
IMAGE_ARCHIVE_SHA256 = "c5567016731df7a11af6c07cf338f8bf93e992a8904ccebd47cfb3a6b0b0f7be"
IMAGE_CONTRACT = (
    b'{"imageId":"sha256:de611d62db982bb833b0737a7ddd72a7cd49ab1a7dc9128669cdabae39f46df8",'
    b'"imageUnpackedSizeBytes":936463936,"mode":"ARCHIVE"}\n'
)
GROUPS = (
    "modelInputs", "scoreInputs", "workerRuntime", "evaluationInputs",
    "controlAndImplementation", "ancestorEvidence",
)
HEX = frozenset("0123456789abcdef")
TOP_KEYS = frozenset({
    "schemaVersion", "status", "runId", "profileId", "createdAt", "producer",
    "sourceRoots", "destinationLayout", "implementationReview", "modelInputs",
    "scoreInputs", "workerRuntime", "evaluationInputs", "controlAndImplementation",
    "ancestorEvidence", "crossGroupReferences", "crossGroupReferencesSha256",
    "recordGroupDigests", "deliverySetSha256", "hostRequirements", "authorization",
    "publication",
})
RECORD_KEYS = frozenset({
    "recordId", "logicalPath", "sourceRoot", "destinationRelativePath", "bytes",
    "sha256", "kind",
})
REVIEW_KEYS = frozenset({
    "schemaVersion", "status", "runId", "profileId", "createdAt", "reviewer", "target",
    "checks", "decision", "dependencyFingerprint", "publication",
})
REVIEW_FAILURE_SCHEMA = "feelm-service-v1-b1-r4-review-failure/1"
REVIEW_FAILURE_KEYS = frozenset({
    "schemaVersion", "status", "runId", "profileId", "phase", "createdAt",
    "auditor", "target", "error", "dependencyFingerprintBefore",
    "dependencyFingerprintAfter", "namespaceCensus", "passReviewPublished",
    "deploymentAuthorized", "publication",
})
CHECKS = {
    "schemaValid": True,
    "implementationReviewPass": True,
    "groupUnionDisjoint": True,
    "crossReferencesValid": True,
    "allFilesRehashed": True,
    "imageArchiveVerified": True,
    "ancestryVerified": True,
    "authorizationValid": True,
    "namespaceValid": True,
}
DECISION = {
    "deliveryIntegrity": "PASS",
    "serverTransferEligible": True,
    "publicPreflightEligible": False,
    "dockerLoadEligible": True,
    "runtimeInstallEligible": True,
    "deploymentAuthorized": False,
}
AUTHORIZATION = {
    "state": "DELIVERY_AUDIT_PENDING",
    "serverTransferEligible": False,
    "publicPreflightEligible": False,
    "fitEligible": False,
    "scoreEligible": False,
    "evaluationEligible": False,
    "deploymentAuthorized": False,
}
SEMANTIC_FACTS = {
    "r2RunId": "b1-gbt120-s339-v1-r2",
    "r3RunId": "b1-gbt120-s339-v1-r3-local4c12g-t14400",
    "r3PreflightStatus": "B1_FULL_PREFLIGHT_COMPLETE_AWAITING_REVIEW",
    "r3PreflightReviewStatus": "PASS",
    "r3FitFailureStatus": "FAILED",
    "r3TimedOut": True,
    "r3OomKilled": False,
    "r3CleanupComplete": True,
    "r3DownstreamBlocked": True,
    "r3ServerBProfileId": "ec2-8vcpu32g-local5-fit20g-t14400-v1",
    "r3ServerBStatus": "SUPERSEDED",
}
SOURCE_FILES = frozenset({
    "scripts/service_v1_b1_r4_publication.py", "scripts/audit_service_v1_b1_r4_implementation.py",
    "scripts/build_service_v1_b1_r4_server_delivery.py", "scripts/audit_service_v1_b1_r4_server_delivery.py",
    "scripts/build_service_v1_b1_r4_server_receipt.py", "scripts/audit_service_v1_b1_r4_server_receipt.py",
    "scripts/run_service_v1_b1_gbt_r4.py", "scripts/audit_service_v1_b1_spark_outputs_r4.py",
    "scripts/evaluate_service_v1_b1_r4.py", "scripts/audit_service_v1_b1_evaluation_outputs_r4.py",
})
TEST_FILES = frozenset({
    "tests/test_service_v1_b1_r4_publication.py", "tests/test_audit_service_v1_b1_r4_implementation.py",
    "tests/test_build_service_v1_b1_r4_server_delivery.py", "tests/test_audit_service_v1_b1_r4_server_delivery.py",
    "tests/test_build_service_v1_b1_r4_server_receipt.py", "tests/test_audit_service_v1_b1_r4_server_receipt.py",
    "tests/test_service_v1_b1_gbt_runner_r4.py", "tests/test_audit_service_v1_b1_spark_outputs_r4.py",
    "tests/test_evaluate_service_v1_b1_r4.py", "tests/test_audit_service_v1_b1_evaluation_outputs_r4.py",
})
IMPLEMENTATION_TOP_KEYS = frozenset({
    "schemaVersion", "status", "runId", "profileId", "createdAt", "reviewer", "target", "checks",
    "testRuns", "decision", "dependencyFingerprint", "publication",
})
IMPLEMENTATION_CHECK_KEYS = frozenset({
    "schemaValid", "designReviewPass", "r3PinsVerified", "sourceSetExact", "testSetExact",
    "runtimeSetExact", "noAssertProductionAst", "normalTestsPass", "optimizedTestsPass",
    "pyCompilePass", "negativeTestsPass", "linuxPublicationIntegrationPass", "mutualPinPass",
    "namespaceValid",
})
IMPLEMENTATION_DECISION = {
    "implementationIntegrity": "PASS", "deliveryBuildEligible": True,
    "publicPreflightEligible": False, "fitEligible": False, "scoreEligible": False,
    "evaluationEligible": False, "deploymentAuthorized": False,
}
TEST_RUN_KINDS = ("normal", "optimized", "pyCompile", "negative", "linuxPublicationIntegration")


class DeliveryAuditError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class Roots:
    standalone: Path
    team: Path
    wsl_evidence: Path
    wsl_delivery: Path

    @property
    def mapping(self) -> dict[str, Path]:
        return {
            "standalone": self.standalone,
            "team": self.team,
            "wslEvidence": self.wsl_evidence,
            "wslDelivery": self.wsl_delivery,
        }


@dataclass(frozen=True)
class AuditSource:
    group: str
    logical_path: str
    source_root: str
    source_relative_path: str
    kind: str = "regular-file"
    virtual_bytes: bytes = b""
    expected_bytes: int | None = None
    expected_sha256: str | None = None


@dataclass(frozen=True)
class ExactDirectory:
    source_root: str
    relative_path: str
    children: tuple[str, ...]


@dataclass(frozen=True)
class AuditContract:
    sources: tuple[AuditSource, ...]
    exact_directories: tuple[ExactDirectory, ...] = ()
    wsl_delivery_children: tuple[str, ...] | None = None
    semantic_facts: Mapping[str, object] | None = None
    verify_ancestry_semantics: bool = False
    producer_logical_path: str = "control/scripts/build_service_v1_b1_r4_server_delivery.py"
    implementation_review_logical_path: str = "control/evidence/" + IMPLEMENTATION_REVIEW_NAME


def _need(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise DeliveryAuditError(code, message)


def _sha(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in HEX for c in value)


def _pairs(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _need(key not in result, "BLOCKED_JSON", f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_numbers(value: Any) -> None:
    if isinstance(value, float):
        _need(math.isfinite(value), "BLOCKED_JSON", "non-finite JSON number")
    elif isinstance(value, Mapping):
        for child in value.values():
            _reject_numbers(child)
    elif isinstance(value, list):
        for child in value:
            _reject_numbers(child)


def _json_bytes(value: Any) -> bytes:
    _reject_numbers(value)
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8") + b"\n"


def _jcs(value: Any) -> bytes:
    def visit(node: Any) -> None:
        _need(not isinstance(node, float), "BLOCKED_JCS", "float outside the review JCS domain")
        _need(node is None or isinstance(node, (str, int, bool, list, Mapping)),
              "BLOCKED_JCS", "unsupported JCS value")
        if isinstance(node, Mapping):
            _need(all(isinstance(key, str) for key in node), "BLOCKED_JCS", "non-string JCS key")
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)
    visit(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    _need(value != "" and not path.is_absolute() and path.as_posix() == value
          and "." not in path.parts and ".." not in path.parts,
          "BLOCKED_PATH", f"unsafe relative path: {value}")
    return path


def _root(path: Path, label: str) -> Path:
    _need(path.is_absolute() and Path(os.path.abspath(path)) == path,
          "BLOCKED_PATH", f"non-lexical root: {label}")
    _need(os.path.realpath(path) == str(path), "BLOCKED_PATH", f"linked root: {label}")
    _need(stat.S_ISDIR(os.lstat(path).st_mode), "BLOCKED_PATH", f"root is not a directory: {label}")
    return path


def _hash_regular(path: Path) -> tuple[int, str, tuple[int, int]]:
    _need(path.is_absolute() and os.path.realpath(path) == str(path),
          "BLOCKED_PATH", f"noncanonical or linked source: {path}")
    before = os.lstat(path)
    _need(stat.S_ISREG(before.st_mode), "BLOCKED_SPECIAL_FILE", f"not regular: {path}")
    _need(before.st_nlink == 1, "BLOCKED_ALIAS", f"hard-link alias: {path}")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    try:
        opened = os.fstat(descriptor)
        _need((opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino),
              "BLOCKED_SOURCE_DRIFT", f"identity drift: {path}")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    _need((after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
          == (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns),
          "BLOCKED_SOURCE_DRIFT", f"source drift: {path}")
    return after.st_size, digest.hexdigest(), (after.st_dev, after.st_ino)


def _read_json(path: Path) -> Mapping[str, Any]:
    size, expected, _ = _hash_regular(path)
    _need(size <= 32 * 1024 * 1024, "BLOCKED_INPUT_SIZE", f"JSON too large: {path}")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(descriptor)
    data = b"".join(chunks)
    _need(len(data) == size and hashlib.sha256(data).hexdigest() == expected,
          "BLOCKED_SOURCE_DRIFT", f"JSON changed: {path}")
    _need(data.endswith(b"\n") and b"\r" not in data, "BLOCKED_JSON", f"JSON must use UTF-8 LF: {path}")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=lambda token: (_ for _ in ()).throw(
                               DeliveryAuditError("BLOCKED_JSON", f"invalid numeric token: {token}")))
    except DeliveryAuditError:
        raise
    except (UnicodeDecodeError, ValueError) as error:
        raise DeliveryAuditError("BLOCKED_JSON", f"invalid JSON {path}: {error}") from error
    _need(isinstance(value, Mapping), "BLOCKED_JSON", f"JSON object required: {path}")
    _reject_numbers(value)
    return value


def _pin(path: Path, logical: str) -> dict[str, object]:
    size, digest, _ = _hash_regular(path)
    return {"path": logical, "bytes": size, "sha256": digest}


def _record_id(root: str, logical: str) -> str:
    return hashlib.sha256(root.encode() + b"\0" + logical.encode()).hexdigest()


def _destination(root: str, relative: str, logical: str) -> str:
    if root == "standalone":
        return "FEELM-standalone/" + relative
    if root == "team":
        return "S15P21E106/" + relative
    if root == "wslEvidence":
        return RUN_ID + "/evidence/" + PurePosixPath(relative).name
    return RUN_ID + "/" + (logical if root == "virtual" else relative)


def _group_digest(records: Sequence[Mapping[str, object]]) -> str:
    digest = hashlib.sha256()
    for record in sorted(records, key=lambda item: str(item["logicalPath"]).encode("ascii")):
        try:
            row = (f'{record["logicalPath"]}\0{record["bytes"]}\0{record["sha256"]}\n').encode("ascii")
        except UnicodeEncodeError as error:
            raise DeliveryAuditError("BLOCKED_DIGEST", "non-ASCII logical path") from error
        digest.update(row)
    return digest.hexdigest()


def _pin_set_digest(records: Sequence[Mapping[str, object]]) -> str:
    digest = hashlib.sha256()
    seen: set[str] = set()
    normalized: list[tuple[str, int, str]] = []
    for record in records:
        _need(set(record) == {"path", "bytes", "sha256"}
              and isinstance(record["path"], str) and record["path"] not in seen
              and type(record["bytes"]) is int and record["bytes"] >= 0 and _sha(record["sha256"]),
              "BLOCKED_GATE", "invalid pin-set record")
        seen.add(record["path"])
        normalized.append((record["path"], record["bytes"], record["sha256"]))
    for path, size, checksum in sorted(normalized):
        digest.update(f"{path}\0{size}\0{checksum}\n".encode("utf-8"))
    return digest.hexdigest()


def _verify_directory(roots: Roots, specification: ExactDirectory) -> None:
    base = _root(roots.mapping[specification.source_root], specification.source_root)
    directory = base.joinpath(*_safe_relative(specification.relative_path).parts)
    _need(os.path.realpath(directory) == str(directory)
          and stat.S_ISDIR(os.lstat(directory).st_mode), "BLOCKED_PATH", f"invalid directory: {directory}")
    names = tuple(sorted(entry.name for entry in os.scandir(directory)))
    _need(names == tuple(sorted(specification.children)), "BLOCKED_EXTRA_MISSING",
          f"directory inventory drift: {directory}")
    for name in names:
        info = os.lstat(directory / name)
        _need(stat.S_ISREG(info.st_mode), "BLOCKED_SPECIAL_FILE", f"special child: {directory / name}")
        _need(info.st_nlink == 1, "BLOCKED_ALIAS", f"hard-link child: {directory / name}")


def _verify_tar(path: Path) -> None:
    before_size, before_sha, before_identity = _hash_regular(path)
    found = False
    names: set[str] = set()
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        with os.fdopen(os.dup(descriptor), "rb", closefd=True) as stream:
            with tarfile.open(fileobj=stream, mode="r:*") as archive:
                for member in archive:
                    relative = PurePosixPath(member.name)
                    name = relative.as_posix()
                    _need(name not in names and name not in ("", ".") and not relative.is_absolute()
                          and ".." not in relative.parts, "BLOCKED_IMAGE", "unsafe archive member")
                    names.add(name)
                    _need(member.isdir() or member.isreg(), "BLOCKED_IMAGE", "linked/special archive member")
                    if member.isreg() and relative.name in {"manifest.json", "index.json"}:
                        _need(member.size <= 8 * 1024 * 1024, "BLOCKED_IMAGE", "oversize image index")
                        stream_member = archive.extractfile(member)
                        _need(stream_member is not None, "BLOCKED_IMAGE", "unreadable image index")
                        raw = stream_member.read()
                        _need(len(raw) == member.size, "BLOCKED_IMAGE", "truncated image index")
                        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
                        found = found or IMAGE_ID in json.dumps(document, sort_keys=True, separators=(",", ":"))
    except (tarfile.TarError, UnicodeDecodeError, ValueError) as error:
        raise DeliveryAuditError("BLOCKED_IMAGE", f"invalid Docker archive: {error}") from error
    finally:
        os.close(descriptor)
    after_size, after_sha, after_identity = _hash_regular(path)
    _need((after_size, after_sha, after_identity) == (before_size, before_sha, before_identity),
          "BLOCKED_SOURCE_DRIFT", "Docker archive drift")
    _need(found, "BLOCKED_IMAGE", "Docker archive does not reference exact image ID")


def _validate_design_review(evidence_root: Path, target: Mapping[str, Any]) -> None:
    path = evidence_root / DESIGN_REVIEW_NAME
    review = _read_json(path)
    _need(set(review) == {"schemaVersion", "status", "runId", "profileId", "createdAt", "reviewer",
                          "target", "checks", "decision", "dependencyFingerprint", "publication"}
          and review.get("schemaVersion") == "feelm-service-v1-b1-r4-design-review/1"
          and review.get("status") == "PASS" and review.get("runId") == RUN_ID
          and review.get("profileId") == PROFILE_ID,
          "BLOCKED_GATE", "design review schema/identity drift")
    expected_target = {
        "plan": {"path": "docs/recommendation/plans/service-v1-b1-r4-server-fit-recovery.md",
                 "bytes": 82_061,
                 "sha256": "3a38ad29c494bb3d1f3b6e94a1a7188fb9b8cdd37a132e25f115ad43eddac8eb"},
        "profile": {"path": "docs/recommendation/plans/service-v1-b1-r4-ec2-8vcpu32g-local5c20g-t28800-profile.json",
                    "bytes": 6_516,
                    "sha256": "56e06e311566c23181b4483200d55049d66b728efcbf12f7a7047c80ce5b7dd5"},
    }
    _need(review["target"] == expected_target
          and target["plan"] == expected_target["plan"]
          and target["profile"] == expected_target["profile"]
          and target["designReview"] == _pin(path, "evidence/" + DESIGN_REVIEW_NAME),
          "BLOCKED_GATE", "design/implementation target drift")
    _need(isinstance(review["checks"], Mapping)
          and set(review["checks"]) == {"draftStatusPreserved", "noImplementationPresent", "priorP1Closed",
                                        "priorP2Closed", "profileSchemaVersion2Valid", "r3PinsVerified",
                                        "targetFilesStableDuringReview"}
          and all(value is True for value in review["checks"].values()),
          "BLOCKED_GATE", "design checks drift")
    _need(review["decision"] == {
        "designIntegrity": "PASS", "implementationEligible": True, "deliveryBuildEligible": False,
        "publicPreflightEligible": False, "fitEligible": False, "scoreEligible": False,
        "evaluationEligible": False, "deploymentAuthorized": False,
    }, "BLOCKED_GATE", "design authorization drift")
    _need(review["dependencyFingerprint"] == _pin_set_digest(
        [expected_target["plan"], expected_target["profile"]]),
        "BLOCKED_GATE", "design dependency fingerprint drift")


def _validate_implementation(
    payload: Mapping[str, Any], *, strict: bool, evidence_root: Path
) -> None:
    _need(set(payload) == IMPLEMENTATION_TOP_KEYS
          and payload.get("schemaVersion") == IMPLEMENTATION_SCHEMA and payload.get("status") == "PASS"
          and payload.get("runId") == RUN_ID and payload.get("profileId") == PROFILE_ID,
          "BLOCKED_GATE", "implementation review identity/status drift")
    _need(payload.get("decision") == IMPLEMENTATION_DECISION,
          "BLOCKED_GATE", "implementation review authorization drift")
    checks = payload.get("checks")
    _need(isinstance(checks, Mapping) and set(checks) == IMPLEMENTATION_CHECK_KEYS
          and all(value is True for value in checks.values()), "BLOCKED_GATE", "implementation checks drift")
    target = payload.get("target")
    _need(isinstance(target, Mapping)
          and set(target) == {"designReview", "plan", "profile", "sourceFiles", "testFiles", "runtimeFiles"},
          "BLOCKED_GATE", "implementation target shape drift")
    for name in ("designReview", "plan", "profile"):
        pin = target[name]
        _need(isinstance(pin, Mapping) and set(pin) == {"path", "bytes", "sha256"}
              and isinstance(pin["path"], str) and type(pin["bytes"]) is int and pin["bytes"] >= 0
              and _sha(pin["sha256"]), "BLOCKED_GATE", f"invalid implementation {name} pin")
    for section in ("sourceFiles", "testFiles", "runtimeFiles"):
        _need(isinstance(target[section], list) and target[section], "BLOCKED_GATE", f"empty {section}")
        paths: list[str] = []
        for pin in target[section]:
            _need(isinstance(pin, Mapping) and set(pin) == {"path", "bytes", "sha256"}
                  and isinstance(pin["path"], str) and type(pin["bytes"]) is int and pin["bytes"] >= 0
                  and _sha(pin["sha256"]), "BLOCKED_GATE", f"invalid {section} pin")
            paths.append(pin["path"])
        _need(len(paths) == len(set(paths)), "BLOCKED_GATE", f"duplicate {section} path")
        if section == "sourceFiles":
            _need(set(paths) == SOURCE_FILES, "BLOCKED_GATE", "implementation source set drift")
        if section == "testFiles":
            _need(set(paths) == TEST_FILES, "BLOCKED_GATE", "implementation test set drift")
    runtime_paths = {pin["path"] for pin in target["runtimeFiles"]}
    wheels = {path for path in runtime_paths if path.startswith("runtime/service-v1-b1-r4-wheelhouse/")}
    _need(len(runtime_paths) == 10 and len(wheels) == 7
          and {"requirements/service-v1-b1-r4-host-runtime.lock",
               "runtime/service-v1-b1-r4-wheelhouse-manifest.json",
               "runtime/feelm-rec046-spark-local.tar"}.issubset(runtime_paths),
          "BLOCKED_GATE", "implementation runtime inventory drift")
    _need(target["plan"]["path"] == "docs/recommendation/plans/service-v1-b1-r4-server-fit-recovery.md"
          and target["profile"]["path"] == "docs/recommendation/plans/service-v1-b1-r4-ec2-8vcpu32g-local5c20g-t28800-profile.json"
          and target["designReview"]["path"] == "evidence/" + DESIGN_REVIEW_NAME,
          "BLOCKED_GATE", "implementation fixed target path drift")
    test_runs = payload.get("testRuns")
    _need(isinstance(test_runs, Mapping) and set(test_runs) == set(TEST_RUN_KINDS),
          "BLOCKED_GATE", "implementation testRuns shape drift")
    for kind in TEST_RUN_KINDS:
        run = test_runs[kind]
        _need(isinstance(run, Mapping)
              and set(run) == {"argv", "exitCode", "stdoutSha256", "stderrSha256", "evidence"}
              and isinstance(run["argv"], list) and run["argv"]
              and all(isinstance(value, str) for value in run["argv"])
              and type(run["exitCode"]) is int and run["exitCode"] == 0
              and _sha(run["stdoutSha256"]) and _sha(run["stderrSha256"]),
              "BLOCKED_GATE", f"implementation testRun drift: {kind}")
        evidence = run["evidence"]
        _need(isinstance(evidence, Mapping) and set(evidence) == {"path", "bytes", "sha256"}
              and isinstance(evidence["path"], str) and type(evidence["bytes"]) is int
              and evidence["bytes"] >= 0 and _sha(evidence["sha256"]),
              "BLOCKED_GATE", f"implementation test evidence drift: {kind}")
    reviewer = payload.get("reviewer")
    _need(isinstance(reviewer, Mapping) and set(reviewer) == {"kind", "sessionId", "host", "processId"}
          and reviewer["kind"] == "INDEPENDENT_IMPLEMENTATION_REVIEWER"
          and all(isinstance(reviewer[key], str) and reviewer[key] for key in ("sessionId", "host"))
          and type(reviewer["processId"]) is int and reviewer["processId"] > 0,
          "BLOCKED_GATE", "implementation reviewer drift")
    _need(isinstance(payload.get("createdAt"), str) and payload["createdAt"]
          and _sha(payload.get("dependencyFingerprint")), "BLOCKED_GATE", "implementation metadata drift")
    if strict:
        _validate_design_review(evidence_root, target)
        source_by_path = {pin["path"]: pin for pin in target["sourceFiles"]}
        expected = hashlib.sha256(_json_bytes({
            "target": target, "checks": checks, "testRuns": test_runs,
            "auditor": source_by_path["scripts/audit_service_v1_b1_r4_implementation.py"],
            "publication": source_by_path[PUBLICATION_SOURCE],
        })).hexdigest()
        _need(payload["dependencyFingerprint"] == expected,
              "BLOCKED_GATE", "implementation dependency fingerprint drift")
        _verify_publication(
            payload["publication"], expected_role="REVIEWER",
            expected_final=evidence_root / IMPLEMENTATION_REVIEW_NAME,
            expected_failure=evidence_root / IMPLEMENTATION_REVIEW_FAILURE_NAME,
            expected_fingerprint=expected,
        )


def _source(group: str, logical: str, root: str, relative: str, **kwargs: Any) -> AuditSource:
    return AuditSource(group, logical, root, relative, **kwargs)


MODEL_PATHS = {
    "contract/training-recipe.v1.json": ("team", "pipeline/configs/service-v1/training-recipe.v1.json"),
    "contract/service-v1.json": ("team", "pipeline/artifacts/service-v1.json"),
    "contract/MODELS.md": ("team", "pipeline/docs/service-v1/MODELS.md"),
    "contract/feature-schema.v1.json": ("team", "pipeline/configs/service-v1/feature-schema.v1.json"),
    "source/natural-train.parquet": ("standalone", "outputs/recommendation-evidence/foundation340/RH/train.parquet"),
    "source/tmdb-masked-train.parquet": ("standalone", "outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-masked-views-v1/tmdb-masked-rh230.parquet"),
    "source/masked-manifest.json": ("standalone", "outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-masked-views-v1/manifest.json"),
    "source/views-manifest.json": ("standalone", "outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-masked-views-v1/views-manifest.json"),
    "source/masked-review.json": ("standalone", "outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-masked-views-v1-result-review.json"),
}
EVALUATION_PATHS = {
    "evaluation/EVALUATION.md": ("team", "pipeline/docs/service-v1/EVALUATION.md"),
    "evaluation/contexts.json": ("standalone", "outputs/recommendation-evidence/text339/contexts.json"),
    "evaluation/catalog.parquet": ("standalone", "outputs/recommendation-evidence/text339/catalog.parquet"),
    "evaluation/labels.parquet": ("standalone", "outputs/recommendation-evidence/text339/labels.parquet"),
    "evaluation/evaluation-seal.json": ("standalone", "outputs/recommendation-evidence/text339/evaluation-seal.json"),
    "evaluation/roles.csv": ("standalone", "outputs/recommendation-evidence/final344/roles.csv"),
    "evaluation/metadata.parquet": ("standalone", "outputs/recommendation-evidence/rec-ev-045/metadata.parquet"),
    "evaluation/ratings.parquet": ("standalone", "outputs/recommendation-evidence/text339/ratings.parquet"),
    "evaluation/b0-predictions.npy": ("standalone", "outputs/recommendation-evidence/final344/GBT120_s339/predictions.npy"),
    "evaluation/b0-seal.json": ("standalone", "outputs/recommendation-evidence/final344/GBT120_s339-seal.json"),
}
WORKER_PATHS = {
    "implementation/service_v1_b1_spark_worker.py": ("standalone", "scripts/service_v1_b1_spark_worker.py"),
    "implementation/combination340_models.py": ("standalone", "scripts/combination340_models.py"),
    "implementation/rec046_common.py": ("standalone", "scripts/rec046_common.py"),
    "runtime/docker-image-id": ("wslDelivery", "runtime/docker-image-id"),
}


PRODUCTION_PINS = {'ancestor/r2/fit-failure.json': (24854, '4ad571439af7f290dd32ce27922519f31e3b156f90998b26a69e2c892aba7e37'),
 'ancestor/r2/plan.md': (47340, 'c5b90b0d19374588fc5cb7a1d98cd6510a1c3708df9667131df5eb3826c79d49'),
 'ancestor/r2/preflight-review.json': (8706, 'cc49688618bfd408a957a22522456912320519d981c34ad87aefb190a0910132'),
 'ancestor/r2/preflight/command.json': (7720, '86e05c4b3555297c9a803b91cea840b2f7ea83286a2a82591a5eb1f950b15859'),
 'ancestor/r2/preflight/input-lock.json': (3489, 'fbd4ae898a8cc35e3e4e7c4442298d6940dc88b3854d20182b7f38c87334d775'),
 'ancestor/r2/preflight/manifest.json': (2351, '7a47a40ac923ba1f8a638ad6029aa72349245b65055f3db8915a2327de174e01'),
 'ancestor/r2/preflight/partition-identity.json': (2489,
                                                   '2a7f7fbd9000730db749cc673d9a43a5027fa3ca76d517cda826f3f420d45c99'),
 'ancestor/r2/preflight/recovery-reference.json': (959,
                                                   '9c7b39b342743e40461f5d5b9c8f0c02268d3c4f82c177ac39b4f97ad8b4133d'),
 'ancestor/r2/preflight/resource.json': (1565, 'a91445352b3fd2f53b84106667686f1d8778ef5b4399d2c15631c7844b634ffd'),
 'ancestor/r2/preflight/run.log': (12955, '52a3c6a0f78b293f11ec2af40d2cd23089d695f4c063bebb6a416aab0f87a063'),
 'ancestor/r2/runner.py': (88623, 'cbc8983a0047b5aa0a2fc5a2d3794080afcf48583fe1be90c6d2dd3dd5af6f8e'),
 'ancestor/r3/evaluation-auditor': (165251, '7f44c00ade107dd82e54658c39d765df2aa8ac7cfddc8e650ba4bebb230fa28c'),
 'ancestor/r3/evaluation-auditor-test': (29865, '5bb03fd080f4e48ea8c38a142c60095d0448245433808c01174b818602a349b4'),
 'ancestor/r3/evaluator': (215832, 'de036adc7b35d151aeaf8006e6a2a64f86130f0d9f49b80ac8fa15ec336fc87a'),
 'ancestor/r3/evaluator-test': (116597, '5a69d17ada3074c34ffbdce3e14c18333fc163b58ebf80b6ba59b5dc9b673494'),
 'ancestor/r3/fit-failure-review.json': (8184, '4c76189254643d58bb1045c89aa879b64be06ddbce9e88294ae7d6229836191d'),
 'ancestor/r3/fit-failure.json': (100384, '75ad1999a8e24d723c56e0b8ecd4f96a573c2086b86daf675d68ebf889940276'),
 'ancestor/r3/plan': (22087, '4de13f041532b7249b22f5a2bbfbd7919f79d94e4c25f2ae16fe95104a2b3d9a'),
 'ancestor/r3/preflight-review.json': (11801, '0d397589778aff2a04d2df2101ac61344e6ce3e7f9d2e204e47156611f92413a'),
 'ancestor/r3/preflight/command.json': (8653, '1c8979c7d60474e871fb3220194fca5eba39b186ad016a6cee7d2d5fc33134eb'),
 'ancestor/r3/preflight/execution-profile.json': (2283,
                                                  '6074818bf05b640ba3ff2b83a2d03d714635761d7e1f317ceefd1332332f4273'),
 'ancestor/r3/preflight/input-lock.json': (6493, '9a3f678ec0fc454498cbb7dbb54973c4c2b9e3eceaee2d3238b96d51cd15f460'),
 'ancestor/r3/preflight/manifest.json': (3149, 'c2f2e7914bd5a222001759d1ecd40119a2fa7a8d17ea3214847050e8535da407'),
 'ancestor/r3/preflight/partition-identity.json': (2489,
                                                   '2a7f7fbd9000730db749cc673d9a43a5027fa3ca76d517cda826f3f420d45c99'),
 'ancestor/r3/preflight/recovery-reference.json': (5314,
                                                   'd35f8ecd0c8c5fca4981fc25b2e550f919c197abf37ca35d823ce603eab9f96d'),
 'ancestor/r3/preflight/resource.json': (8851, 'bf5714a12c394c835d00f18d473772e00da758b4c2998d2175d7f437a120bace'),
 'ancestor/r3/preflight/run.log': (12961, 'be8521910c5506e181ca01e6864885056e88542cbbb0d14e7b6d6e0979628171'),
 'ancestor/r3/profile': (1522, '761c210364ab465f9143ded5dba8c2f3fba9a1d34c10f1ea54d742a528494c23'),
 'ancestor/r3/runner': (143994, '783a5a4c7f1783416271de0a1cde6a6be55409ef8b625a757f8a07c153c7a574'),
 'ancestor/r3/runner-test': (25063, 'c348c6d02741ffe4a30635c3a2fc0285c8503f15618cac75b3c65943582145af'),
 'ancestor/r3/spark-auditor': (89009, '92a9e48597205f54509ccd7fbd9c717cb791bbd31a6998cb9d966177f830d3be'),
 'ancestor/r3/spark-auditor-test': (19497, '12af534b89383318cdc6b8fb27610dca58880737b4b7a970825ee8a70a9a9d18'),
 'contract/MODELS.md': (25079, '5ad4c852b89ca02da00c30f0fc184aa6018db4abf1808c057772a4908d376701'),
 'contract/feature-schema.v1.json': (39243, 'fda2be4f40b76e46b88dbb53523ef404bbf8a13c68bbf012acc58da9a63948ca'),
 'contract/service-v1.json': (15504, '1a9ba0cd0101f6d065227c37ee642a3fc75c2c69b34c0da8fa6dacff351c2343'),
 'contract/training-recipe.v1.json': (15764, 'd403a27fab09453f98b9988fcfb3867b83e41ae217f9f5f3be5976321eef8d7b'),
 'evaluation/ALS/item-factors/._SUCCESS.crc': (8, '1d44f510ec2ed7595badbec80583316defc14e8dd89130d719724149adfaa07d'),
 'evaluation/ALS/item-factors/.part-00000-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet.crc': (5012,
                                                                                                          '11178dfd21ed556940979fa479b90e4aa8428664a408092c0e764ff6510136bd'),
 'evaluation/ALS/item-factors/.part-00001-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet.crc': (5956,
                                                                                                          'd8ebea0b42fe459706af7a5345d4b33eece9a2915aae3c5c24938c20947315a9'),
 'evaluation/ALS/item-factors/.part-00002-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet.crc': (5124,
                                                                                                          '5302d3a66499097662b384cd57766a05b2b5a5440f54f44a9d0b63321a411289'),
 'evaluation/ALS/item-factors/.part-00003-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet.crc': (5944,
                                                                                                          '0340a491786074efad6361c5780b3ff2e65551093a26f0e9e6ddd1e6121171fc'),
 'evaluation/ALS/item-factors/.part-00004-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet.crc': (4968,
                                                                                                          'ea8bdfa93384e93bac31484c7c6fdc3d130885d92c22a921d7a84144665bdd30'),
 'evaluation/ALS/item-factors/.part-00005-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet.crc': (6140,
                                                                                                          'f1c2e1de9731f9952b87c3ff6d7bf6ca84ed9926f9e3fbd5731029a711f38f29'),
 'evaluation/ALS/item-factors/.part-00006-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet.crc': (4988,
                                                                                                          'ebc504ac0f2382deb12b70929da9a103bdf005b6c52c1b5409fa1ca3651b3a5b'),
 'evaluation/ALS/item-factors/.part-00007-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet.crc': (6292,
                                                                                                          '10d719359e51b9f57198eefb75099636080a96c61ee04153cc467732d9cb7cff'),
 'evaluation/ALS/item-factors/_SUCCESS': (0, 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'),
 'evaluation/ALS/item-factors/part-00000-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet': (640387,
                                                                                                     'f6d328a55d9059e5e4170215071aeb522381e9aceb247a195cddee2df249ee66'),
 'evaluation/ALS/item-factors/part-00001-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet': (761270,
                                                                                                     'de79f165b1ae21beabd1bf6352d029b88b6383567689192f2280a45b9f680fa6'),
 'evaluation/ALS/item-factors/part-00002-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet': (654425,
                                                                                                     '0c761ca6573a8c35162af0b77294030b9c4f00cef30b3fbb549689d05397c9aa'),
 'evaluation/ALS/item-factors/part-00003-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet': (759762,
                                                                                                     '9819744e3bad16494129969a423a6ee650d5ee3b2ca6e07b18f9121614ca5d76'),
 'evaluation/ALS/item-factors/part-00004-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet': (634621,
                                                                                                     'c0230036ee3de8fab7d2b6138a8b45f4ba2b1f15adc19093f16503c4fc5d39c6'),
 'evaluation/ALS/item-factors/part-00005-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet': (784751,
                                                                                                     '666787c824db1064feb0f8563d49180a7de87beb18e9a82e8554b313fbb7537b'),
 'evaluation/ALS/item-factors/part-00006-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet': (636997,
                                                                                                     '089631f252fbcc9a9cc875836ee0c62db7002b16c175f7f96f997fce37b337f6'),
 'evaluation/ALS/item-factors/part-00007-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet': (804047,
                                                                                                     '1fb552c709468dca889e02d184a61fe911c7ba70e64a24bc5fe0a2b07dbf828b'),
 'evaluation/EVALUATION.md': (12466, 'db7bd86f6b72b2edee7b6a65610bdb177c65937b518288417e3c6bdbb02bf3e3'),
 'evaluation/b0-predictions.npy': (745968, '6520b9094c89824fa2ed833da7618851536d9a7ce3f74d20d2233c71e34cea4f'),
 'evaluation/b0-seal.json': (6911, 'ba9be1a4769d9cd77a8d8b0a11b437ada10254a030656e98cfa7f7f07d7ffee7'),
 'evaluation/catalog.parquet': (728100, '0bde668e0e26f5f82b5c41d90d62c7569fd350bf2a2fd4b59bb4402f438c5947'),
 'evaluation/contexts.json': (12290713, '951fc2464bd3aea25ea486c79c33084f6241f7846a3ed626e9d5fc3907a7aab7'),
 'evaluation/evaluation-seal.json': (4890, '0ab420fb3e50cf770d9dd7a24d64e5ce8896a4c17b38a7ba5df73398ad6fb9c7'),
 'evaluation/labels.parquet': (75490, 'e3bf301a6e2ea7885b59bcab7fe83c2d3ad84f93f2f1bbb2d54d943b9a658db8'),
 'evaluation/metadata.parquet': (6891831, '4d838874938115be7a4b1f629a920dd196e082b655b75d559d71039e52eb7d8d'),
 'evaluation/ratings.parquet': (32950407, '28b46687abec2e0edb3a892ec4f4dbd9d5cca701bf220b2812f9e7cf9b905a63'),
 'evaluation/roles.csv': (5806, '466b7cede9cb2d67f2bd7fca4fcdada770943bdd9d5924c35ccfdbe6a4cf2cc9'),
 'implementation/combination340_models.py': (4025,
                                             'a519586b25d81574a96e1847dc6a4d8d08d3ded276deff64c5d82ff0bcdecd25'),
 'implementation/rec046_common.py': (8001, '42dd14e83ecbee0c02c5e4233abb4b6a8d807ad2213c24a22cfb82350bc47848'),
 'implementation/service_v1_b1_spark_worker.py': (40526,
                                                  '9708e0b3fdc618c5df2240c9e1dcf368fd4f6fa411d7915c31104b3af2146d42'),
 'runtime/docker-image-id': (71, '07666208de67cba550e28294fc88c22981071e2026894da726c12ea42c806137'),
 'source/masked-manifest.json': (5176, '82eb3635f1ae914a056c2813c4786dfa2de56abbb37aaf5ce852685f6eb6a557'),
 'source/masked-review.json': (10174, 'f7e322206d95c8ad418926a08394625f84a25b611f4e9595c97dd03cca6c7b1c'),
 'source/natural-score.parquet': (2985357, '9832425537943823f524ae6730aa1059b77d2c0464664f1dc29357bf95078f9b'),
 'source/natural-train.parquet': (832717601, '9d8d33a252991c032704d4072003b4fb9f400136f3c411a2698ae5fee592fa45'),
 'source/tmdb-masked-train.parquet': (891461814, '27aee771597ba230654b2e99c1eea047fd25e3265161c51b6873474d47c65f01'),
 'source/views-manifest.json': (1710, 'df8dd8bbfea4a71e506958c5b7e1499b5010350a326cbfac2c9bfe23a607792a')}

def _ancestor_path(logical: str) -> str:
    fixed = {
        "ancestor/r3/plan": "docs/recommendation/plans/service-v1-b1-r3-fit-recovery.md",
        "ancestor/r3/profile": "docs/recommendation/plans/service-v1-b1-r3-local4c12g-t14400-profile.json",
        "ancestor/r3/runner": "scripts/run_service_v1_b1_gbt_r3.py",
        "ancestor/r3/spark-auditor": "scripts/audit_service_v1_b1_spark_outputs_r3.py",
        "ancestor/r3/evaluator": "scripts/evaluate_service_v1_b1_r3.py",
        "ancestor/r3/evaluation-auditor": "scripts/audit_service_v1_b1_evaluation_outputs_r3.py",
        "ancestor/r3/runner-test": "tests/test_service_v1_b1_gbt_runner_r3.py",
        "ancestor/r3/spark-auditor-test": "tests/test_audit_service_v1_b1_spark_outputs_r3.py",
        "ancestor/r3/evaluator-test": "tests/test_evaluate_service_v1_b1_r3.py",
        "ancestor/r3/evaluation-auditor-test": "tests/test_audit_service_v1_b1_evaluation_outputs_r3.py",
        "ancestor/r2/runner.py": "scripts/run_service_v1_b1_gbt.py",
        "ancestor/r2/plan.md": "docs/recommendation/plans/service-v1-b1-spark-runner.md",
    }
    if logical in fixed:
        return fixed[logical]
    base = "outputs/recommendation-evidence/service-v1-pretraining-20260913/"
    if logical.startswith("ancestor/r3/preflight/"):
        return base + "b1-gbt120-s339-v1-r3-local4c12g-t14400-preflight/" + logical.split("/")[-1]
    if logical == "ancestor/r3/preflight-review.json":
        return base + "b1-gbt120-s339-v1-r3-local4c12g-t14400-preflight-result-review.json"
    if logical == "ancestor/r3/fit-failure.json":
        return base + "b1-gbt120-s339-v1-r3-local4c12g-t14400-fit-failure.json"
    if logical == "ancestor/r3/fit-failure-review.json":
        return base + "b1-gbt120-s339-v1-r3-local4c12g-t14400-fit-failure-result-review.json"
    if logical.startswith("ancestor/r2/preflight/"):
        return base + "b1-gbt120-s339-v1-r2-preflight/" + logical.split("/")[-1]
    if logical == "ancestor/r2/preflight-review.json":
        return base + "b1-gbt120-s339-v1-r2-preflight-result-review.json"
    if logical == "ancestor/r2/fit-failure.json":
        return base + "b1-gbt120-s339-v1-r2-fit-failure.json"
    raise DeliveryAuditError("BLOCKED_ANCESTRY", f"unknown ancestor logical path: {logical}")


def default_contract(roots: Roots, implementation: Mapping[str, Any], manifest: Mapping[str, Any]) -> AuditContract:
    _need(roots.standalone.name == "FEELM-standalone" and roots.team.name == "S15P21E106"
          and roots.standalone.parent == roots.team.parent,
          "BLOCKED_PATH", "standalone/team roots are not exact siblings")
    _need(roots.wsl_evidence != roots.wsl_delivery,
          "BLOCKED_PATH", "evidence and delivery staging roots overlap")
    def pinned(group: str, logical: str, root: str, relative: str) -> AuditSource:
        _need(logical in PRODUCTION_PINS, "BLOCKED_CONTRACT", f"missing static pin: {logical}")
        expected_bytes, expected_sha = PRODUCTION_PINS[logical]
        return _source(group, logical, root, relative, expected_bytes=expected_bytes,
                       expected_sha256=expected_sha)

    sources: list[AuditSource] = [
        *(pinned("modelInputs", logical, root, relative) for logical, (root, relative) in MODEL_PATHS.items()),
        pinned("scoreInputs", "source/natural-score.parquet", "standalone", "outputs/recommendation-evidence/foundation340/RH/score.parquet"),
        *(pinned("workerRuntime", logical, root, relative) for logical, (root, relative) in WORKER_PATHS.items()),
        *(pinned("evaluationInputs", logical, root, relative) for logical, (root, relative) in EVALUATION_PATHS.items()),
    ]
    ancestor_logicals = sorted(logical for logical in PRODUCTION_PINS if logical.startswith("ancestor/"))
    for logical in ancestor_logicals:
        sources.append(pinned("ancestorEvidence", logical, "standalone", _ancestor_path(logical)))
    als_logicals = sorted(logical for logical in PRODUCTION_PINS
                          if logical.startswith("evaluation/ALS/item-factors/"))
    _need(len(als_logicals) == 18, "BLOCKED_CONTRACT", "static ALS inventory count drift")
    for logical in als_logicals:
        relative = "outputs/recommendation-evidence/combination340/ALS/item-factors/" + logical.split("/")[-1]
        sources.append(pinned("evaluationInputs", logical, "standalone", relative))
    target = implementation["target"]
    wheel_manifest = _read_json(roots.standalone / "runtime/service-v1-b1-r4-wheelhouse-manifest.json")
    _need(set(wheel_manifest) == {"schemaVersion", "pythonVersion", "interpreterTag", "abiTag",
          "platformTag", "files", "wheelhouseSetSha256"}
          and (wheel_manifest["schemaVersion"], wheel_manifest["pythonVersion"],
               wheel_manifest["interpreterTag"], wheel_manifest["abiTag"], wheel_manifest["platformTag"])
          == ("feelm-service-v1-b1-r4-wheelhouse/1", "3.12.3", "cp312", "cp312",
              "manylinux_2_17_x86_64")
          and isinstance(wheel_manifest["files"], list) and len(wheel_manifest["files"]) == 7,
          "BLOCKED_RUNTIME", "wheelhouse manifest drift")
    wheel_digest_rows = []
    for pin in wheel_manifest["files"]:
        _need(isinstance(pin, Mapping) and set(pin) == {"path", "bytes", "sha256"}
              and isinstance(pin["path"], str) and type(pin["bytes"]) is int and _sha(pin["sha256"]),
              "BLOCKED_RUNTIME", "wheel declaration shape drift")
        wheel_digest_rows.append({"logicalPath": pin["path"], "bytes": pin["bytes"], "sha256": pin["sha256"]})
    _need(_group_digest(wheel_digest_rows) == wheel_manifest["wheelhouseSetSha256"]
          == "af87e5e9b660085d45ab5562dc76fd3f0f617d1d069108eb8c3677b66ad0fa5c",
          "BLOCKED_RUNTIME", "wheelhouse digest drift")
    runtime_by_path = {pin["path"]: pin for pin in target["runtimeFiles"]}
    expected_runtime_paths = {
        "requirements/service-v1-b1-r4-host-runtime.lock",
        "runtime/service-v1-b1-r4-wheelhouse-manifest.json",
        "runtime/feelm-rec046-spark-local.tar",
        *(pin["path"] for pin in wheel_manifest["files"]),
    }
    _need(set(runtime_by_path) == expected_runtime_paths,
          "BLOCKED_RUNTIME", "runtime target and wheelhouse differ")
    _need(all(runtime_by_path[pin["path"]] == pin for pin in wheel_manifest["files"]),
          "BLOCKED_RUNTIME", "wheel target pin differs from wheelhouse declaration")
    _need((runtime_by_path["runtime/feelm-rec046-spark-local.tar"]["bytes"],
           runtime_by_path["runtime/feelm-rec046-spark-local.tar"]["sha256"])
          == (IMAGE_ARCHIVE_BYTES, IMAGE_ARCHIVE_SHA256),
          "BLOCKED_IMAGE", "Docker archive production pin drift")
    for section in ("sourceFiles", "testFiles", "runtimeFiles"):
        for pin in target[section]:
            logical = pin["path"] if pin["path"] == "runtime/feelm-rec046-spark-local.tar" else "control/" + pin["path"]
            sources.append(_source("controlAndImplementation", logical,
                                   "standalone", pin["path"], expected_bytes=pin["bytes"],
                                   expected_sha256=pin["sha256"]))
    for key in ("plan", "profile"):
        pin = target[key]
        sources.append(_source("controlAndImplementation", "control/" + pin["path"],
                               "standalone", pin["path"], expected_bytes=pin["bytes"],
                               expected_sha256=pin["sha256"]))
    design = target["designReview"]
    sources.append(_source("controlAndImplementation", "control/evidence/" + DESIGN_REVIEW_NAME,
                           "wslEvidence", DESIGN_REVIEW_NAME, expected_bytes=design["bytes"],
                           expected_sha256=design["sha256"]))
    implementation_pin = _pin(roots.wsl_evidence / IMPLEMENTATION_REVIEW_NAME, IMPLEMENTATION_REVIEW_NAME)
    sources.append(_source("controlAndImplementation", "control/evidence/" + IMPLEMENTATION_REVIEW_NAME,
                           "wslEvidence", IMPLEMENTATION_REVIEW_NAME,
                           expected_bytes=implementation_pin["bytes"], expected_sha256=implementation_pin["sha256"]))
    test_children: list[str] = []
    for kind in ("normal", "optimized", "pyCompile", "negative", "linuxPublicationIntegration"):
        pin = implementation["testRuns"][kind]["evidence"]
        relative = ".implementation-test-evidence/" + kind + ".json"
        _need(pin["path"] == (roots.wsl_delivery / relative).as_posix(),
              "BLOCKED_GATE", f"test evidence path drift: {kind}")
        sources.append(_source("controlAndImplementation", "control/implementation-test-evidence/" + kind + ".json",
                               "wslDelivery", relative, expected_bytes=pin["bytes"],
                               expected_sha256=pin["sha256"]))
        test_children.append(kind + ".json")
    sources.append(_source("controlAndImplementation", "runtime/docker-image-contract.json", "virtual",
                           "runtime/docker-image-contract.json", kind="docker-image-contract",
                           virtual_bytes=IMAGE_CONTRACT, expected_bytes=len(IMAGE_CONTRACT),
                           expected_sha256=hashlib.sha256(IMAGE_CONTRACT).hexdigest()))
    wheel_records = target["runtimeFiles"]
    wheel_children = tuple(sorted(PurePosixPath(pin["path"]).name for pin in wheel_records
                                  if pin["path"].startswith("runtime/service-v1-b1-r4-wheelhouse/")))
    als_children = tuple(sorted(logical.split("/")[-1] for logical in als_logicals))
    return AuditContract(
        tuple(sources),
        (ExactDirectory("standalone", "outputs/recommendation-evidence/combination340/ALS/item-factors", als_children),
         ExactDirectory("standalone", "runtime/service-v1-b1-r4-wheelhouse", wheel_children),
         ExactDirectory("wslDelivery", ".implementation-test-evidence", tuple(sorted(test_children))),
         ExactDirectory("wslDelivery", "runtime", ("docker-image-id",))),
        (".implementation-test-evidence", "runtime"),
        SEMANTIC_FACTS,
        True,
        "control/scripts/build_service_v1_b1_r4_server_delivery.py",
        "control/evidence/" + IMPLEMENTATION_REVIEW_NAME,
    )


def _verify_ancestry(roots: Roots, facts: Mapping[str, object]) -> None:
    base = roots.standalone / "outputs/recommendation-evidence/service-v1-pretraining-20260913"
    preflight = _read_json(base / "b1-gbt120-s339-v1-r3-local4c12g-t14400-preflight/manifest.json")
    preflight_review = _read_json(base / "b1-gbt120-s339-v1-r3-local4c12g-t14400-preflight-result-review.json")
    failure = _read_json(base / "b1-gbt120-s339-v1-r3-local4c12g-t14400-fit-failure.json")
    failure_review = _read_json(base / "b1-gbt120-s339-v1-r3-local4c12g-t14400-fit-failure-result-review.json")
    resource, container, census, decision = (failure.get("resourceEvidence", {}), failure.get("containerRun", {}),
                                              failure.get("successPathCensus", {}), failure_review.get("decision", {}))
    observed = dict(SEMANTIC_FACTS)
    observed.update({
        "r3RunId": failure.get("runId"), "r3PreflightStatus": preflight.get("status"),
        "r3PreflightReviewStatus": preflight_review.get("status"), "r3FitFailureStatus": failure.get("status"),
        "r3TimedOut": resource.get("timedOut"), "r3OomKilled": resource.get("oomKilled"),
        "r3CleanupComplete": failure.get("cleanupComplete") is True and container.get("cleanupErrors") == [],
        "r3DownstreamBlocked": decision.get("downstream") == "BLOCK" and all(
            isinstance(census.get(name), Mapping) and census[name].get("lexists") is False
            for name in ("bundle", "modelNative", "review")),
    })
    _need(dict(facts) == observed, "BLOCKED_ANCESTRY", "semantic ancestry drift")


def _verify_publication(
    value: object,
    *,
    expected_role: str,
    expected_final: Path | None = None,
    expected_failure: Path | None = None,
    expected_fingerprint: str | None = None,
) -> None:
    keys = {
        "schemaVersion", "mode", "role", "finalPath", "failurePath", "claimPath", "tempPath", "token",
        "claimStDev", "claimStIno", "filesystemType", "publisher", "dependencyFingerprintAtAcquire",
        "dependencyFingerprintBeforeRename", "renameNoReplaceProbe", "requiredPostconditions",
    }
    _need(isinstance(value, Mapping) and set(value) == keys
          and value.get("schemaVersion") == "feelm-service-v1-b1-r4-publication-evidence/1"
          and value.get("mode") == "LINUX_RENAME_NOREPLACE" and value.get("role") == expected_role
          and value.get("renameNoReplaceProbe") is True,
          "BLOCKED_PUBLICATION", "publication evidence drift")
    for name in ("finalPath", "failurePath", "claimPath", "tempPath"):
        _need(isinstance(value[name], str) and Path(value[name]).is_absolute(),
              "BLOCKED_PUBLICATION", f"publication path drift: {name}")
    _need(isinstance(value["token"], str) and len(value["token"]) == 32
          and all(character in HEX for character in value["token"])
          and type(value["claimStDev"]) is int and value["claimStDev"] >= 0
          and type(value["claimStIno"]) is int and value["claimStIno"] >= 0
          and value["filesystemType"] in {"ext2/ext3", "xfs"}
          and _sha(value["dependencyFingerprintAtAcquire"])
          and value["dependencyFingerprintBeforeRename"] == value["dependencyFingerprintAtAcquire"],
          "BLOCKED_PUBLICATION", "publication acquisition fact drift")
    publisher = value["publisher"]
    _need(publisher == _pin(Path(__file__).with_name("service_v1_b1_r4_publication.py"), PUBLICATION_SOURCE),
          "BLOCKED_PUBLICATION", "publication publisher pin drift")
    if expected_final is not None:
        _need(value["finalPath"] == expected_final.as_posix(),
              "BLOCKED_PUBLICATION", "publication final path drift")
    if expected_failure is not None:
        _need(value["failurePath"] == expected_failure.as_posix(),
              "BLOCKED_PUBLICATION", "publication failure path drift")
    if expected_fingerprint is not None:
        _need(value["dependencyFingerprintAtAcquire"] == expected_fingerprint,
              "BLOCKED_PUBLICATION", "publication dependency fingerprint drift")
    post = value["requiredPostconditions"]
    _need(isinstance(post, Mapping)
          and set(post) == {"publishedBytesRehashRequired", "fileAndParentFsyncRequired",
                            "dependencyFingerprintStableRequired", "claimIdentityMatchRequired",
                            "claimRemovalRequired"}
          and all(item is True for item in post.values()),
          "BLOCKED_PUBLICATION", "publication postconditions drift")


def audit_manifest(
    roots: Roots,
    manifest_path: Path,
    expected_manifest_sha256: str,
    implementation_review_path: Path,
    expected_implementation_review_sha256: str,
    contract: AuditContract | None = None,
) -> tuple[dict[str, object], str]:
    for label, root in roots.mapping.items():
        _root(root, label)
    _need(manifest_path == roots.wsl_evidence / MANIFEST_NAME,
          "BLOCKED_PATH", "manifest must use the canonical evidence path")
    _need(implementation_review_path == roots.wsl_evidence / IMPLEMENTATION_REVIEW_NAME,
          "BLOCKED_PATH", "implementation review must use the canonical evidence path")
    manifest_pin = _pin(manifest_path, MANIFEST_NAME)
    implementation_pin = _pin(implementation_review_path, IMPLEMENTATION_REVIEW_NAME)
    _need(_sha(expected_manifest_sha256) and manifest_pin["sha256"] == expected_manifest_sha256,
          "BLOCKED_DIGEST", "manifest expected SHA mismatch")
    _need(_sha(expected_implementation_review_sha256)
          and implementation_pin["sha256"] == expected_implementation_review_sha256,
          "BLOCKED_DIGEST", "implementation expected SHA mismatch")
    manifest = _read_json(manifest_path)
    implementation = _read_json(implementation_review_path)
    _validate_implementation(implementation, strict=contract is None, evidence_root=roots.wsl_evidence)
    _need(set(manifest) == TOP_KEYS and manifest.get("schemaVersion") == MANIFEST_SCHEMA
          and manifest.get("status") == "DELIVERY_AUDIT_PENDING"
          and manifest.get("runId") == RUN_ID and manifest.get("profileId") == PROFILE_ID,
          "BLOCKED_SCHEMA", "manifest identity/top-level shape drift")
    expected_roots = {key: str(value) for key, value in roots.mapping.items()}
    _need(manifest["sourceRoots"] == expected_roots, "BLOCKED_PATH", "manifest source roots drift")
    _need(manifest["destinationLayout"] == {
        "siblingRootsRequired": True, "standaloneDirectoryName": "FEELM-standalone",
        "teamDirectoryName": "S15P21E106", "deliveryDirectoryName": RUN_ID,
        "preserveRelativePaths": True,
    }, "BLOCKED_SCHEMA", "destination layout drift")
    _need(manifest["authorization"] == AUTHORIZATION, "BLOCKED_AUTHORIZATION", "manifest authorization drift")
    chosen = contract or default_contract(roots, implementation, manifest)
    for directory in chosen.exact_directories:
        _verify_directory(roots, directory)
    if chosen.wsl_delivery_children is not None:
        observed = tuple(sorted(entry.name for entry in os.scandir(roots.wsl_delivery)))
        _need(observed == tuple(sorted(chosen.wsl_delivery_children)), "BLOCKED_EXTRA_MISSING",
              "delivery staging residue")
    expected_sources = {(source.group, source.logical_path): source for source in chosen.sources}
    _need(len(expected_sources) == len(chosen.sources), "BLOCKED_GROUP_OVERLAP", "audit contract overlaps")
    records_by_group: dict[str, list[Mapping[str, object]]] = {}
    observed_keys: set[tuple[str, str]] = set()
    record_ids: set[str] = set()
    destinations: set[str] = set()
    identities: set[tuple[int, int]] = set()
    for group in GROUPS:
        raw = manifest["ancestorEvidence"]["records"] if group == "ancestorEvidence" else manifest[group]
        _need(isinstance(raw, list) and raw, "BLOCKED_SCHEMA", f"invalid group: {group}")
        records_by_group[group] = raw
        for record in raw:
            _need(isinstance(record, Mapping) and set(record) == RECORD_KEYS,
                  "BLOCKED_SCHEMA", f"record shape: {group}")
            logical = record["logicalPath"]
            _need(isinstance(logical, str), "BLOCKED_SCHEMA", "logical path type")
            key = (group, logical)
            _need(key in expected_sources and key not in observed_keys,
                  "BLOCKED_EXTRA_MISSING", f"unexpected/duplicate record: {key}")
            observed_keys.add(key)
            source = expected_sources[key]
            _safe_relative(source.logical_path)
            _safe_relative(str(record["destinationRelativePath"]))
            _need(record["sourceRoot"] == source.source_root and record["kind"] == source.kind,
                  "BLOCKED_MOVED", f"source root/kind moved: {logical}")
            expected_id = _record_id(source.source_root, logical)
            expected_destination = _destination(source.source_root, source.source_relative_path, logical)
            _need(record["recordId"] == expected_id and record["destinationRelativePath"] == expected_destination,
                  "BLOCKED_MOVED", f"record identity/destination moved: {logical}")
            _need(expected_id not in record_ids and expected_destination not in destinations,
                  "BLOCKED_GROUP_OVERLAP", "record/destination overlap")
            record_ids.add(expected_id)
            destinations.add(expected_destination)
            if source.kind == "docker-image-contract":
                size, digest = len(source.virtual_bytes), hashlib.sha256(source.virtual_bytes).hexdigest()
            else:
                path = roots.mapping[source.source_root].joinpath(*_safe_relative(source.source_relative_path).parts)
                size, digest, identity = _hash_regular(path)
                _need(identity not in identities, "BLOCKED_GROUP_OVERLAP", f"physical inode overlap: {logical}")
                identities.add(identity)
                if logical == "runtime/feelm-rec046-spark-local.tar":
                    _verify_tar(path)
            _need(type(record["bytes"]) is int and record["bytes"] == size and record["sha256"] == digest,
                  "BLOCKED_DIGEST", f"record pin drift: {logical}")
            if source.expected_bytes is not None:
                _need((size, digest) == (source.expected_bytes, source.expected_sha256),
                      "BLOCKED_DIGEST", f"review-pinned source drift: {logical}")
    _need(observed_keys == set(expected_sources), "BLOCKED_EXTRA_MISSING", "missing owning record")
    digests = {group: _group_digest(records_by_group[group]) for group in GROUPS}
    _need(manifest["recordGroupDigests"] == digests, "BLOCKED_DIGEST", "group digest drift")
    union = [record for group in GROUPS for record in records_by_group[group]]
    delivery_digest = _group_digest(union)
    _need(manifest["deliverySetSha256"] == delivery_digest, "BLOCKED_DIGEST", "delivery-set digest drift")
    _verify_publication(
        manifest["publication"], expected_role="PRODUCER", expected_final=manifest_path,
        expected_failure=roots.wsl_evidence / (RUN_ID + "-delivery-failure.json"),
        expected_fingerprint=delivery_digest,
    )
    ancestor = manifest["ancestorEvidence"]
    facts = dict(chosen.semantic_facts or {})
    facts_sha = hashlib.sha256(_jcs(facts)).hexdigest()
    closure = hashlib.sha256(digests["ancestorEvidence"].encode() + b"\0" + facts_sha.encode() + b"\n").hexdigest()
    _need(set(ancestor) == {"schemaVersion", "records", "semanticFacts", "recordSetSha256",
                            "semanticFactsSha256", "closureSha256"}
          and ancestor["schemaVersion"] == ANCESTOR_SCHEMA and ancestor["semanticFacts"] == facts
          and ancestor["recordSetSha256"] == digests["ancestorEvidence"]
          and ancestor["semanticFactsSha256"] == facts_sha and ancestor["closureSha256"] == closure,
          "BLOCKED_ANCESTRY", "ancestor closure drift")
    if chosen.verify_ancestry_semantics:
        _verify_ancestry(roots, facts)
    lookup = {(group, record["logicalPath"]): record["recordId"]
              for group in GROUPS for record in records_by_group[group]}
    references = [
        {"referenceId": "evaluation.artifact_manifest", "consumerGroup": "evaluationInputs",
         "consumerLogicalName": "evaluation.artifact_manifest", "ownerGroup": "modelInputs",
         "ownerRecordId": lookup[("modelInputs", "contract/service-v1.json")]},
        {"referenceId": "evaluation.score_axis", "consumerGroup": "evaluationInputs",
         "consumerLogicalName": "evaluation.score_axis", "ownerGroup": "scoreInputs",
         "ownerRecordId": lookup[("scoreInputs", "source/natural-score.parquet")]},
    ]
    _need(manifest["crossGroupReferences"] == references
          and manifest["crossGroupReferencesSha256"] == hashlib.sha256(_jcs(references)).hexdigest(),
          "BLOCKED_CROSS_REFERENCE", "cross-group reference drift")
    control = {record["logicalPath"]: record for record in records_by_group["controlAndImplementation"]}
    producer_record = control.get(chosen.producer_logical_path)
    _need(producer_record is not None and manifest["producer"] == {"recordId": producer_record["recordId"]},
          "BLOCKED_SCHEMA", "producer reference drift")
    impl_record = control.get(chosen.implementation_review_logical_path)
    _need(impl_record is not None and manifest["implementationReview"] == {"recordId": impl_record["recordId"]}
          and (impl_record["bytes"], impl_record["sha256"])
          == (implementation_pin["bytes"], implementation_pin["sha256"]),
          "BLOCKED_GATE", "implementation review reference drift")
    profile = _read_json(roots.standalone / "docs/recommendation/plans/service-v1-b1-r4-ec2-8vcpu32g-local5c20g-t28800-profile.json")
    _need(profile.get("schemaVersion") == "feelm-service-v1-b1-execution-profile/2"
          and profile.get("status") == "DRAFT_REQUIRES_INDEPENDENT_REVIEW"
          and profile.get("runId") == RUN_ID and profile.get("profileId") == PROFILE_ID,
          "BLOCKED_PROFILE", "profile identity/status drift")
    host = manifest["hostRequirements"]
    _need(isinstance(host, Mapping) and set(host) == {"profile", "serverPreconditions", "hostRuntime",
          "runnerSupervisor", "evaluationSupervisor", "docker", "spark", "timeoutsSeconds",
          "maintenanceWindowsSeconds"}, "BLOCKED_PROFILE", "host requirements shape")
    _need(host["profile"] == implementation["target"]["profile"]
          and all(host[key] == profile[key] for key in set(host) - {"profile"}),
          "BLOCKED_PROFILE", "host requirements/profile drift")
    target = {
        "manifest": manifest_pin,
        "implementationReview": implementation_pin,
        "deliverySetSha256": delivery_digest,
        "crossGroupReferencesSha256": manifest["crossGroupReferencesSha256"],
    }
    auditor_pin = _pin(Path(__file__), AUDITOR_SOURCE)
    publication_pin = _pin(Path(__file__).with_name("service_v1_b1_r4_publication.py"), PUBLICATION_SOURCE)
    fingerprint = hashlib.sha256(_jcs({"target": target, "auditor": auditor_pin,
                                      "publication": publication_pin})).hexdigest()
    return target, fingerprint


def compose_review(target: Mapping[str, object], dependency_fingerprint: str,
                   publication_evidence: Mapping[str, object], reviewer_session: str,
                   *, created_at: str | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "schemaVersion": REVIEW_SCHEMA, "status": "PASS", "runId": RUN_ID, "profileId": PROFILE_ID,
        "createdAt": created_at or datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "reviewer": _reviewer_identity(reviewer_session),
        "target": dict(target), "checks": dict(CHECKS), "decision": dict(DECISION),
        "dependencyFingerprint": dependency_fingerprint, "publication": dict(publication_evidence),
    }
    _need(set(payload) == REVIEW_KEYS, "BLOCKED_SCHEMA", "internal review shape")
    return payload


def _direct_children(path: Path) -> list[str]:
    try:
        return sorted(os.listdir(path))
    except OSError:
        return []


def _reviewer_identity(reviewer_session: str) -> dict[str, object]:
    _need(isinstance(reviewer_session, str) and reviewer_session != "",
          "BLOCKED_REVIEW_FAILURE_SCHEMA", "reviewer session is empty")
    return {
        "kind": "INDEPENDENT_DELIVERY_REVIEWER",
        "sessionId": reviewer_session,
        "host": socket.gethostname(),
        "processId": os.getpid(),
    }


def _failure_file_state(path: Path, logical_path: str) -> tuple[dict[str, object] | None, dict[str, object]]:
    try:
        pin = _pin(path, logical_path)
    except Exception as error:
        return None, {
            "logicalPath": logical_path,
            "absolutePath": str(path),
            "state": "UNAVAILABLE",
            "error": {"type": type(error).__name__, "message": str(error)},
        }
    return pin, {
        "logicalPath": logical_path,
        "absolutePath": str(path),
        "state": "PRESENT",
        "bytes": pin["bytes"],
        "sha256": pin["sha256"],
    }


def _capture_review_failure_state(
    roots: Roots,
    manifest_path: Path,
    expected_manifest_sha256: str,
    implementation_review_path: Path,
    expected_implementation_review_sha256: str,
    contract: AuditContract | None,
) -> tuple[list[dict[str, object]], dict[str, object], str]:
    targets: list[dict[str, object]] = []
    states: list[dict[str, object]] = []
    fixed = (
        (roots.wsl_evidence / DESIGN_REVIEW_NAME, "evidence/" + DESIGN_REVIEW_NAME),
        (implementation_review_path, "evidence/" + IMPLEMENTATION_REVIEW_NAME),
        (manifest_path, "evidence/" + MANIFEST_NAME),
        (Path(__file__).resolve(), AUDITOR_SOURCE),
        (Path(__file__).with_name("service_v1_b1_r4_publication.py").resolve(), PUBLICATION_SOURCE),
    )
    seen_paths: set[str] = set()
    for path, logical in fixed:
        pin, state = _failure_file_state(path, logical)
        states.append(state)
        if pin is not None and logical not in seen_paths:
            targets.append(pin)
            seen_paths.add(logical)

    if contract is not None:
        for source in sorted(contract.sources, key=lambda item: (item.group, item.logical_path)):
            if source.kind == "docker-image-contract":
                states.append({
                    "logicalPath": source.logical_path,
                    "sourceRoot": "virtual",
                    "state": "VIRTUAL",
                    "bytes": len(source.virtual_bytes),
                    "sha256": hashlib.sha256(source.virtual_bytes).hexdigest(),
                    "expectedBytes": source.expected_bytes,
                    "expectedSha256": source.expected_sha256,
                })
                continue
            try:
                root = roots.mapping[source.source_root]
                path = root.joinpath(*_safe_relative(source.source_relative_path).parts)
            except Exception as error:
                states.append({
                    "logicalPath": source.logical_path,
                    "sourceRoot": source.source_root,
                    "state": "UNAVAILABLE",
                    "error": {"type": type(error).__name__, "message": str(error)},
                    "expectedBytes": source.expected_bytes,
                    "expectedSha256": source.expected_sha256,
                })
                continue
            pin, state = _failure_file_state(path, source.logical_path)
            state["sourceRoot"] = source.source_root
            state["expectedBytes"] = source.expected_bytes
            state["expectedSha256"] = source.expected_sha256
            states.append(state)
            if pin is not None and source.logical_path not in seen_paths:
                targets.append(pin)
                seen_paths.add(source.logical_path)

    directories: list[dict[str, object]] = []
    if contract is not None:
        for specification in sorted(
            contract.exact_directories,
            key=lambda item: (item.source_root, item.relative_path),
        ):
            record: dict[str, object] = {
                "sourceRoot": specification.source_root,
                "relativePath": specification.relative_path,
                "expectedChildren": list(sorted(specification.children)),
            }
            try:
                root = roots.mapping[specification.source_root]
                path = root.joinpath(*_safe_relative(specification.relative_path).parts)
                record["state"] = "PRESENT"
                record["observedChildren"] = sorted(os.listdir(path))
            except Exception as error:
                record["state"] = "UNAVAILABLE"
                record["error"] = {"type": type(error).__name__, "message": str(error)}
            directories.append(record)

    targets.sort(key=lambda item: str(item["path"]))
    states.sort(key=lambda item: (str(item.get("logicalPath", "")), str(item.get("absolutePath", ""))))
    state = {
        "runId": RUN_ID,
        "profileId": PROFILE_ID,
        "phase": "delivery-review",
        "expectedManifestSha256": expected_manifest_sha256,
        "expectedImplementationReviewSha256": expected_implementation_review_sha256,
        "expectedCompletedChildren": list(DELIVERY_REVIEW_COMPLETED_CHILDREN),
        "target": targets,
        "sourceStates": states,
        "exactDirectories": directories,
    }
    fingerprint = hashlib.sha256(_jcs(state)).hexdigest()
    return targets, state, fingerprint


def _success_failure_target(target: Mapping[str, object]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for name in ("manifest", "implementationReview"):
        item = target.get(name)
        _need(isinstance(item, Mapping), "BLOCKED_REVIEW_FAILURE_SCHEMA", f"missing {name} pin")
        record = dict(item)
        _need(set(record) == {"path", "bytes", "sha256"},
              "BLOCKED_REVIEW_FAILURE_SCHEMA", f"invalid {name} pin")
        result.append(record)
    result.append(_pin(Path(__file__).resolve(), AUDITOR_SOURCE))
    result.append(_pin(Path(__file__).with_name("service_v1_b1_r4_publication.py").resolve(), PUBLICATION_SOURCE))
    return sorted(result, key=lambda item: str(item["path"]))


def _validate_review_failure(payload: Mapping[str, object], lease: object) -> None:
    _need(set(payload) == REVIEW_FAILURE_KEYS,
          "BLOCKED_REVIEW_FAILURE_SCHEMA", "review failure shape")
    _need(
        payload["schemaVersion"] == REVIEW_FAILURE_SCHEMA
        and payload["status"] == "FAILED"
        and payload["runId"] == RUN_ID
        and payload["profileId"] == PROFILE_ID
        and payload["phase"] == "delivery-review",
        "BLOCKED_REVIEW_FAILURE_SCHEMA",
        "review failure identity",
    )
    _need(isinstance(payload["createdAt"], str) and str(payload["createdAt"]).endswith("Z"),
          "BLOCKED_REVIEW_FAILURE_SCHEMA", "review failure timestamp")
    auditor = payload["auditor"]
    _need(isinstance(auditor, Mapping)
          and set(auditor) == {"kind", "sessionId", "host", "processId"}
          and auditor["kind"] == "INDEPENDENT_DELIVERY_REVIEWER"
          and all(isinstance(auditor[key], str) and auditor[key] != ""
                  for key in ("sessionId", "host"))
          and type(auditor["processId"]) is int and auditor["processId"] > 0,
          "BLOCKED_REVIEW_FAILURE_SCHEMA", "review failure auditor")
    target = payload["target"]
    _need(isinstance(target, list), "BLOCKED_REVIEW_FAILURE_SCHEMA", "review failure target")
    paths: list[str] = []
    for pin in target:
        _need(isinstance(pin, Mapping)
              and set(pin) == {"path", "bytes", "sha256"}
              and isinstance(pin["path"], str)
              and type(pin["bytes"]) is int and pin["bytes"] >= 0
              and _sha(pin["sha256"]),
              "BLOCKED_REVIEW_FAILURE_SCHEMA", "review failure target pin")
        paths.append(str(pin["path"]))
    _need(paths == sorted(set(paths)), "BLOCKED_REVIEW_FAILURE_SCHEMA", "review failure target order")
    error = payload["error"]
    _need(isinstance(error, Mapping) and set(error) == {"type", "message", "traceback"}
          and all(isinstance(error[key], str) for key in error),
          "BLOCKED_REVIEW_FAILURE_SCHEMA", "review failure error")
    _need(_sha(payload["dependencyFingerprintBefore"])
          and payload["dependencyFingerprintAfter"] == payload["dependencyFingerprintBefore"],
          "BLOCKED_REVIEW_FAILURE_SCHEMA", "review failure dependency fingerprint")
    census = payload["namespaceCensus"]
    _need(isinstance(census, Mapping)
          and set(census) == {"beforeAcquire", "beforePublication", "requiredAfterPublication"}
          and all(isinstance(census[key], list)
                  and all(isinstance(item, str) for item in census[key])
                  for key in census),
          "BLOCKED_REVIEW_FAILURE_SCHEMA", "review failure namespace census")
    _need(payload["passReviewPublished"] is False
          and payload["deploymentAuthorized"] is False,
          "BLOCKED_REVIEW_FAILURE_SCHEMA", "review failure authorization")
    publication = _publication_module()
    _need(payload["publication"] == lease.publication_evidence,
          "BLOCKED_REVIEW_FAILURE_SCHEMA", "review failure publication evidence")
    _need(publication is not None, "BLOCKED_IMPLEMENTATION", "publication module unavailable")
    _reject_numbers(payload)


def _compose_review_failure(
    error: Exception,
    target: Sequence[Mapping[str, object]],
    dependency_fingerprint: str,
    lease: object,
    reviewer_session: str,
    namespace_before: Sequence[str],
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schemaVersion": REVIEW_FAILURE_SCHEMA,
        "status": "FAILED",
        "runId": RUN_ID,
        "profileId": PROFILE_ID,
        "phase": "delivery-review",
        "createdAt": datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "auditor": _reviewer_identity(reviewer_session),
        "target": [dict(item) for item in target],
        "error": {
            "type": type(error).__name__,
            "message": str(error),
            "traceback": "".join(traceback.format_exception(type(error), error, error.__traceback__)),
        },
        "dependencyFingerprintBefore": dependency_fingerprint,
        "dependencyFingerprintAfter": dependency_fingerprint,
        "namespaceCensus": {
            "beforeAcquire": list(namespace_before),
            "beforePublication": _direct_children(lease.final_path.parent),
            "requiredAfterPublication": sorted((*DELIVERY_REVIEW_COMPLETED_CHILDREN, REVIEW_FAILURE_NAME)),
        },
        "passReviewPublished": False,
        "deploymentAuthorized": False,
        "publication": lease.publication_evidence,
    }
    _validate_review_failure(payload, lease)
    return payload


def _publish_review_failure(
    roots: Roots,
    manifest_path: Path,
    expected_manifest_sha256: str,
    implementation_review_path: Path,
    expected_implementation_review_sha256: str,
    review_path: Path,
    reviewer_session: str,
    error: Exception,
    contract: AuditContract | None,
    *,
    lease: object | None = None,
    target: Sequence[Mapping[str, object]] | None = None,
    dependency_fingerprint: str | None = None,
    rehash_callback: Callable[[], str] | None = None,
):
    publication = _publication_module()
    if lease is None:
        captured_target, _state, fingerprint = _capture_review_failure_state(
            roots,
            manifest_path,
            expected_manifest_sha256,
            implementation_review_path,
            expected_implementation_review_sha256,
            contract,
        )
        namespace_before = _direct_children(roots.wsl_evidence)
        lease = publication.acquire_publication(
            "REVIEWER",
            "delivery-review",
            review_path,
            review_path.with_name(REVIEW_FAILURE_NAME),
            publication.ExpectedNamespace(DELIVERY_REVIEW_COMPLETED_CHILDREN, fingerprint),
        )

        def current_fingerprint() -> str:
            _target, _current_state, current = _capture_review_failure_state(
                roots,
                manifest_path,
                expected_manifest_sha256,
                implementation_review_path,
                expected_implementation_review_sha256,
                contract,
            )
            return current

        target = captured_target
        dependency_fingerprint = fingerprint
        rehash_callback = current_fingerprint
    else:
        namespace_before = list(DELIVERY_REVIEW_COMPLETED_CHILDREN)
    _need(target is not None and dependency_fingerprint is not None and callable(rehash_callback),
          "BLOCKED_REVIEW_FAILURE_SCHEMA", "review failure publication input is incomplete")
    payload = _compose_review_failure(
        error,
        target,
        dependency_fingerprint,
        lease,
        reviewer_session,
        namespace_before,
    )
    pin = publication.publish_handled_failure(
        lease, payload, dependency_fingerprint, rehash_callback
    )
    post = rehash_callback()
    publication.release_verified_claim(lease, pin, post)
    return pin, payload


def _publication_module():
    name = "service_v1_b1_r4_publication"
    if name in sys.modules:
        return sys.modules[name]
    path = Path(__file__).with_name("service_v1_b1_r4_publication.py")
    specification = importlib.util.spec_from_file_location(name, path)
    _need(specification is not None and specification.loader is not None,
          "BLOCKED_IMPLEMENTATION", "cannot load publication module")
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


def audit_and_publish(
    roots: Roots,
    manifest_path: Path,
    expected_manifest_sha256: str,
    implementation_review_path: Path,
    expected_implementation_review_sha256: str,
    review_path: Path,
    completed_children: Sequence[str] | None,
    reviewer_session: str,
    contract: AuditContract | None = None,
):
    _need(review_path == roots.wsl_evidence / REVIEW_NAME,
          "BLOCKED_PATH", "delivery review must use the canonical evidence path")
    supplied_children = (
        DELIVERY_REVIEW_COMPLETED_CHILDREN
        if completed_children is None
        else tuple(sorted(completed_children))
    )
    _need(
        supplied_children == DELIVERY_REVIEW_COMPLETED_CHILDREN
        and len(supplied_children) == len(DELIVERY_REVIEW_COMPLETED_CHILDREN),
        "BLOCKED_NAMESPACE",
        "delivery review completed-child input differs from the frozen predecessor set",
    )
    try:
        target, fingerprint = audit_manifest(
            roots,
            manifest_path,
            expected_manifest_sha256,
            implementation_review_path,
            expected_implementation_review_sha256,
            contract,
        )
    except Exception as error:
        pin, _failure = _publish_review_failure(
            roots,
            manifest_path,
            expected_manifest_sha256,
            implementation_review_path,
            expected_implementation_review_sha256,
            review_path,
            reviewer_session,
            error,
            contract,
        )
        raise DeliveryAuditError(
            "FAILED_PUBLISHED", f"delivery review failure published at {pin.path}: {error}"
        ) from error
    publication = _publication_module()
    lease = publication.acquire_publication(
        "REVIEWER", "delivery-review", review_path, review_path.with_name(REVIEW_FAILURE_NAME),
        publication.ExpectedNamespace(DELIVERY_REVIEW_COMPLETED_CHILDREN, fingerprint),
    )

    def rehash() -> str:
        _target, current = audit_manifest(roots, manifest_path, expected_manifest_sha256,
                                          implementation_review_path,
                                          expected_implementation_review_sha256, contract)
        return current
    try:
        review = compose_review(target, fingerprint, lease.publication_evidence, reviewer_session)
        descriptor = os.open(
            lease.temp_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        try:
            data = _json_bytes(review)
            offset = 0
            while offset < len(data):
                count = os.write(descriptor, data[offset:])
                _need(count > 0, "BLOCKED_IO", "short review write")
                offset += count
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        pin = publication.publish_success(lease, lease.temp_path, fingerprint, rehash)
        post = rehash()
        publication.release_verified_claim(lease, pin, post)
        return pin, review
    except Exception as error:
        if (
            os.path.lexists(lease.claim_path)
            and not os.path.lexists(lease.temp_path)
            and not os.path.lexists(lease.final_path)
            and not os.path.lexists(lease.failure_path)
        ):
            failure_pin, _failure = _publish_review_failure(
                roots,
                manifest_path,
                expected_manifest_sha256,
                implementation_review_path,
                expected_implementation_review_sha256,
                review_path,
                reviewer_session,
                error,
                contract,
                lease=lease,
                target=_success_failure_target(target),
                dependency_fingerprint=fingerprint,
                rehash_callback=rehash,
            )
            raise DeliveryAuditError(
                "FAILED_PUBLISHED",
                f"delivery review failure published at {failure_pin.path}: {error}",
            ) from error
        raise


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--standalone-root", type=Path, default=Path("/mnt/c/higher/projects/FEELM-standalone"))
    parser.add_argument("--team-root", type=Path, default=Path("/mnt/c/higher/projects/S15P21E106"))
    parser.add_argument("--evidence-root", type=Path, default=Path("/home/kingc/.feelm-r4/evidence/service-v1-pretraining-20260913"))
    parser.add_argument("--delivery-root", type=Path, default=Path("/home/kingc/.feelm-r4/delivery") / RUN_ID)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--implementation-review", type=Path, required=True)
    parser.add_argument("--expected-implementation-review-sha256", required=True)
    parser.add_argument("--review-path", type=Path, required=True)
    parser.add_argument("--completed-child", action="append")
    parser.add_argument("--reviewer-session", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    roots = Roots(args.standalone_root, args.team_root, args.evidence_root, args.delivery_root)
    pin, _review = audit_and_publish(
        roots, args.manifest, args.expected_manifest_sha256, args.implementation_review,
        args.expected_implementation_review_sha256, args.review_path,
        args.completed_child or DELIVERY_REVIEW_COMPLETED_CHILDREN,
        args.reviewer_session,
    )
    print(json.dumps(pin.record, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
