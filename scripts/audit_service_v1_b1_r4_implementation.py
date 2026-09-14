"""Audit and seal the exact service-v1 B1 r4 implementation closure.

The auditor is intentionally independent from the runner and evaluator.  It can
record private command evidence on either development OS, but a PASS review can
only be published through the shared Linux publication primitive.
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback
import types
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
import zipfile


RUN_ID = "b1-gbt120-s339-v1-r4-server5c20g-t28800"
PROFILE_ID = "ec2-8vcpu32g-local5-fit20g-t28800-v1"
PLAN_RELATIVE = "docs/recommendation/plans/service-v1-b1-r4-server-fit-recovery.md"
PROFILE_RELATIVE = "docs/recommendation/plans/service-v1-b1-r4-ec2-8vcpu32g-local5c20g-t28800-profile.json"
PLAN_PIN = (82_061, "3a38ad29c494bb3d1f3b6e94a1a7188fb9b8cdd37a132e25f115ad43eddac8eb")
PROFILE_PIN = (6_516, "56e06e311566c23181b4483200d55049d66b728efcbf12f7a7047c80ce5b7dd5")
DESIGN_REVIEW_NAME = f"{RUN_ID}-design-result-review.json"
IMPLEMENTATION_REVIEW_NAME = f"{RUN_ID}-implementation-result-review.json"
IMPLEMENTATION_FAILURE_NAME = f"{RUN_ID}-implementation-result-review-failure.json"

SOURCE_FILES = (
    "scripts/service_v1_b1_r4_publication.py",
    "scripts/audit_service_v1_b1_r4_implementation.py",
    "scripts/build_service_v1_b1_r4_server_delivery.py",
    "scripts/audit_service_v1_b1_r4_server_delivery.py",
    "scripts/build_service_v1_b1_r4_server_receipt.py",
    "scripts/audit_service_v1_b1_r4_server_receipt.py",
    "scripts/run_service_v1_b1_gbt_r4.py",
    "scripts/audit_service_v1_b1_spark_outputs_r4.py",
    "scripts/evaluate_service_v1_b1_r4.py",
    "scripts/audit_service_v1_b1_evaluation_outputs_r4.py",
)
TEST_FILES = (
    "tests/test_service_v1_b1_r4_publication.py",
    "tests/test_audit_service_v1_b1_r4_implementation.py",
    "tests/test_build_service_v1_b1_r4_server_delivery.py",
    "tests/test_audit_service_v1_b1_r4_server_delivery.py",
    "tests/test_build_service_v1_b1_r4_server_receipt.py",
    "tests/test_audit_service_v1_b1_r4_server_receipt.py",
    "tests/test_service_v1_b1_gbt_runner_r4.py",
    "tests/test_audit_service_v1_b1_spark_outputs_r4.py",
    "tests/test_evaluate_service_v1_b1_r4.py",
    "tests/test_audit_service_v1_b1_evaluation_outputs_r4.py",
)
RUNTIME_FIXED = (
    "requirements/service-v1-b1-r4-host-runtime.lock",
    "runtime/service-v1-b1-r4-wheelhouse-manifest.json",
    "runtime/feelm-rec046-spark-local.tar",
)
TEST_KINDS = ("normal", "optimized", "pyCompile", "negative", "linuxPublicationIntegration")
HEX64 = re.compile(r"[0-9a-f]{64}")
PRIVATE_TEST_EVIDENCE_SCHEMA = "feelm-service-v1-b1-r4-private-test-run/2"
PRIVATE_TEST_EVIDENCE_MODE = "REVIEWER_EXCLUSIVE_CREATE_0600_PRIVATE_DIRECTORY"
PINNED_EXTERNAL_IMPORT_ROOTS = {"numpy", "pandas", "pyarrow"}
DESIGN_CHECK_KEYS = {
    "priorP1Closed", "priorP2Closed", "r3PinsVerified", "profileSchemaVersion2Valid",
    "draftStatusPreserved", "noImplementationPresent", "targetFilesStableDuringReview",
}
DESIGN_PUBLICATION_KEYS = {
    "mode", "canonicalRoot", "filesystemType", "claimPath", "tempPath",
    "bootstrapPublisherSource", "bootstrapPublisherSha256", "interpreterPath",
    "interpreterSha256", "renameNoReplaceProbe", "dependencyFingerprintAtAcquire",
    "dependencyFingerprintBeforeRename", "requiredPostconditions",
}
DESIGN_POSTCONDITION_KEYS = {
    "publishedBytesRehashRequired", "fileAndParentFsyncRequired",
    "dependencyFingerprintStableRequired", "claimIdentityMatchRequired",
    "claimRemovalRequired",
}
DESIGN_BOOTSTRAP_SOURCE_SHA256 = "1c43c16d3e4fec242e67f95d353d7ff5b7c9512d212e9117b414d6d475a5cb0a"
DOCKER_IMAGE_ID = "sha256:de611d62db982bb833b0737a7ddd72a7cd49ab1a7dc9128669cdabae39f46df8"
LOCAL_TEST_INTERPRETER = r"C:\Users\kingc\AppData\Local\Programs\Python\Python312\python.exe"
LOCAL_TEST_INTERPRETER_PIN = (103_704, "5aba6ec903f2e0e946459f98dc45c8129d3f22187f5adac00713d733191d3a3f")
LINUX_PUBLICATION_INTERPRETER = "/usr/bin/python3.12"
LINUX_PUBLICATION_INTERPRETER_PIN = (8_020_928, "e50d468e8b0adfb05733f5b87b3cff34829c4a8c1aea50c865aa8bdfe4bb150f")
R3_PINS = {
    "docs/recommendation/plans/service-v1-b1-r3-fit-recovery.md": (22_087, "4de13f041532b7249b22f5a2bbfbd7919f79d94e4c25f2ae16fe95104a2b3d9a"),
    "docs/recommendation/plans/service-v1-b1-r3-local4c12g-t14400-profile.json": (1_522, "761c210364ab465f9143ded5dba8c2f3fba9a1d34c10f1ea54d742a528494c23"),
    "scripts/run_service_v1_b1_gbt_r3.py": (143_994, "783a5a4c7f1783416271de0a1cde6a6be55409ef8b625a757f8a07c153c7a574"),
    "scripts/audit_service_v1_b1_spark_outputs_r3.py": (89_009, "92a9e48597205f54509ccd7fbd9c717cb791bbd31a6998cb9d966177f830d3be"),
    "scripts/evaluate_service_v1_b1_r3.py": (215_832, "de036adc7b35d151aeaf8006e6a2a64f86130f0d9f49b80ac8fa15ec336fc87a"),
    "scripts/audit_service_v1_b1_evaluation_outputs_r3.py": (165_251, "7f44c00ade107dd82e54658c39d765df2aa8ac7cfddc8e650ba4bebb230fa28c"),
    "tests/test_service_v1_b1_gbt_runner_r3.py": (25_063, "c348c6d02741ffe4a30635c3a2fc0285c8503f15618cac75b3c65943582145af"),
    "tests/test_audit_service_v1_b1_spark_outputs_r3.py": (19_497, "12af534b89383318cdc6b8fb27610dca58880737b4b7a970825ee8a70a9a9d18"),
    "tests/test_evaluate_service_v1_b1_r3.py": (116_597, "5a69d17ada3074c34ffbdce3e14c18333fc163b58ebf80b6ba59b5dc9b673494"),
    "tests/test_audit_service_v1_b1_evaluation_outputs_r3.py": (29_865, "5bb03fd080f4e48ea8c38a142c60095d0448245433808c01174b818602a349b4"),
    "outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-gbt120-s339-v1-r3-local4c12g-t14400-preflight/command.json": (8_653, "1c8979c7d60474e871fb3220194fca5eba39b186ad016a6cee7d2d5fc33134eb"),
    "outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-gbt120-s339-v1-r3-local4c12g-t14400-preflight/execution-profile.json": (2_283, "6074818bf05b640ba3ff2b83a2d03d714635761d7e1f317ceefd1332332f4273"),
    "outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-gbt120-s339-v1-r3-local4c12g-t14400-preflight/input-lock.json": (6_493, "9a3f678ec0fc454498cbb7dbb54973c4c2b9e3eceaee2d3238b96d51cd15f460"),
    "outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-gbt120-s339-v1-r3-local4c12g-t14400-preflight/manifest.json": (3_149, "c2f2e7914bd5a222001759d1ecd40119a2fa7a8d17ea3214847050e8535da407"),
    "outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-gbt120-s339-v1-r3-local4c12g-t14400-preflight/partition-identity.json": (2_489, "2a7f7fbd9000730db749cc673d9a43a5027fa3ca76d517cda826f3f420d45c99"),
    "outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-gbt120-s339-v1-r3-local4c12g-t14400-preflight/recovery-reference.json": (5_314, "d35f8ecd0c8c5fca4981fc25b2e550f919c197abf37ca35d823ce603eab9f96d"),
    "outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-gbt120-s339-v1-r3-local4c12g-t14400-preflight/resource.json": (8_851, "bf5714a12c394c835d00f18d473772e00da758b4c2998d2175d7f437a120bace"),
    "outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-gbt120-s339-v1-r3-local4c12g-t14400-preflight/run.log": (12_961, "be8521910c5506e181ca01e6864885056e88542cbbb0d14e7b6d6e0979628171"),
    "outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-gbt120-s339-v1-r3-local4c12g-t14400-preflight-result-review.json": (11_801, "0d397589778aff2a04d2df2101ac61344e6ce3e7f9d2e204e47156611f92413a"),
    "outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-gbt120-s339-v1-r3-local4c12g-t14400-fit-failure.json": (100_384, "75ad1999a8e24d723c56e0b8ecd4f96a573c2086b86daf675d68ebf889940276"),
    "outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-gbt120-s339-v1-r3-local4c12g-t14400-fit-failure-result-review.json": (8_184, "4c76189254643d58bb1045c89aa879b64be06ddbce9e88294ae7d6229836191d"),
}


class ImplementationAuditError(RuntimeError):
    pass


class HandledReviewFailure(ImplementationAuditError):
    def __init__(self, message: str, published: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.published = dict(published)


def need(condition: Any, message: str) -> None:
    if not condition:
        raise ImplementationAuditError(message)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _file_signature(info: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _lexical_chain(path: Path) -> tuple[os.stat_result, tuple[tuple[str, int, int, int], ...]]:
    absolute = path.absolute()
    current = absolute
    chain: list[tuple[str, int, int, int]] = []
    leaf: os.stat_result | None = None
    while True:
        info = os.lstat(current)
        need(not stat.S_ISLNK(info.st_mode), f"symlink path forbidden: {path}")
        if leaf is None:
            leaf = info
        else:
            chain.append((str(current), info.st_dev, info.st_ino, info.st_mode))
        if current == current.parent:
            break
        current = current.parent
    need(leaf is not None, f"unreadable path: {path}")
    return leaf, tuple(chain)


def _cross_file_signature(signature: tuple[int, int, int, int, int, int, int]) -> tuple[int, ...]:
    # Windows reports different permission bits and creation/change timestamps
    # through lstat and fstat for the same handle.  Bind the two views by volume,
    # file ID, file type, link count, byte size, and modification time; each full
    # view is still required to remain stable until the read completes.
    if os.name != "nt":
        return signature
    return (signature[0], signature[1], stat.S_IFMT(signature[2]), signature[3], signature[4], signature[5])


def _open_bound_regular(path: Path) -> tuple[
    int,
    tuple[tuple[int, int, int, int, int, int, int], tuple[int, int, int, int, int, int, int]],
    tuple[tuple[str, int, int, int], ...],
]:
    lexical, chain = _lexical_chain(path)
    need(stat.S_ISREG(lexical.st_mode), f"regular file required: {path}")
    need(lexical.st_nlink == 1, f"hard-link alias forbidden: {path}")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    try:
        opened = os.fstat(descriptor)
        need(stat.S_ISREG(opened.st_mode), f"regular descriptor required: {path}")
        need(opened.st_nlink == 1, f"hard-link descriptor forbidden: {path}")
        opened_signature = _file_signature(opened)
        lexical_signature = _file_signature(lexical)
        need(_cross_file_signature(opened_signature) == _cross_file_signature(lexical_signature), f"file changed before open: {path}")
        return descriptor, (opened_signature, lexical_signature), chain
    except BaseException:
        os.close(descriptor)
        raise


def _verify_bound_regular(
    path: Path,
    descriptor: int,
    signatures: tuple[
        tuple[int, int, int, int, int, int, int],
        tuple[int, int, int, int, int, int, int],
    ],
    chain: tuple[tuple[str, int, int, int], ...],
) -> None:
    descriptor_signature, lexical_signature = signatures
    final_descriptor_signature = _file_signature(os.fstat(descriptor))
    need(final_descriptor_signature == descriptor_signature, f"open file changed while reading: {path}")
    lexical, final_chain = _lexical_chain(path)
    final_lexical_signature = _file_signature(lexical)
    need(final_lexical_signature == lexical_signature, f"file path changed while reading: {path}")
    need(_cross_file_signature(final_descriptor_signature) == _cross_file_signature(final_lexical_signature), f"file descriptor/path identity drift: {path}")
    need(final_chain == chain, f"path component changed while reading: {path}")


def _hash_descriptor(descriptor: int) -> tuple[int, str]:
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    count = 0
    while True:
        chunk = os.read(descriptor, 8 * 1024 * 1024)
        if not chunk:
            break
        count += len(chunk)
        digest.update(chunk)
    return count, digest.hexdigest()


def sha256_file(path: Path) -> str:
    descriptor, signatures, chain = _open_bound_regular(path)
    try:
        count, digest = _hash_descriptor(descriptor)
        need(count == signatures[0][4], f"file byte count drift: {path}")
        _verify_bound_regular(path, descriptor, signatures, chain)
        return digest
    finally:
        os.close(descriptor)


def read_regular_bytes_with_pin(
    path: Path,
    *,
    logical: str | None = None,
    maximum_bytes: int | None = None,
) -> tuple[bytes, dict[str, Any]]:
    descriptor, signatures, chain = _open_bound_regular(path)
    try:
        if maximum_bytes is not None:
            need(signatures[0][4] <= maximum_bytes, f"file exceeds byte limit: {path}")
        chunks: list[bytes] = []
        count = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            count += len(chunk)
            if maximum_bytes is not None:
                need(count <= maximum_bytes, f"file exceeds byte limit: {path}")
            chunks.append(chunk)
        need(count == signatures[0][4], f"file byte count drift: {path}")
        _verify_bound_regular(path, descriptor, signatures, chain)
        raw = b"".join(chunks)
        record: dict[str, Any] = {"bytes": count, "sha256": sha256_bytes(raw)}
        if logical is not None:
            record["path"] = logical
        return raw, record
    finally:
        os.close(descriptor)


def read_regular_bytes(path: Path, *, maximum_bytes: int | None = None) -> bytes:
    raw, _record = read_regular_bytes_with_pin(path, maximum_bytes=maximum_bytes)
    return raw


def safe_regular(path: Path) -> os.stat_result:
    info, _ = _lexical_chain(path)
    need(stat.S_ISREG(info.st_mode), f"regular file required: {path}")
    need(info.st_nlink == 1, f"hard-link alias forbidden: {path}")
    return info


def pin(path: Path, logical: str | None = None) -> dict[str, Any]:
    descriptor, signatures, chain = _open_bound_regular(path)
    try:
        count, digest = _hash_descriptor(descriptor)
        need(count == signatures[0][4], f"file byte count drift: {path}")
        _verify_bound_regular(path, descriptor, signatures, chain)
    finally:
        os.close(descriptor)
    result: dict[str, Any] = {"bytes": signatures[0][4], "sha256": digest}
    if logical is not None:
        result["path"] = logical
    return result


def pin_exact(path: Path, expected: tuple[int, str], logical: str) -> dict[str, Any]:
    result = pin(path, logical)
    need((result["bytes"], result["sha256"]) == expected, f"immutable pin drift: {logical}")
    return result


def duplicate_safe_json(path: Path, *, require_lf: bool = True) -> dict[str, Any]:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            need(key not in result, f"duplicate JSON key {key}: {path}")
            result[key] = value
        return result

    raw = read_regular_bytes(path)
    need(not raw.startswith(b"\xef\xbb\xbf"), f"JSON BOM forbidden: {path}")
    if require_lf:
        need(b"\r" not in raw and raw.endswith(b"\n"), f"JSON must be UTF-8 LF: {path}")
    value = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=pairs,
        parse_constant=lambda token: (_ for _ in ()).throw(ImplementationAuditError(f"non-finite JSON {token}: {path}")),
    )
    need(isinstance(value, dict), f"JSON object required: {path}")
    return value


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def record_set_digest(records: Iterable[Mapping[str, Any]]) -> str:
    normalized: list[tuple[str, int, str]] = []
    seen: set[str] = set()
    for record in records:
        need(set(record) == {"path", "bytes", "sha256"}, "pin record shape drift")
        name, size, digest = record["path"], record["bytes"], record["sha256"]
        need(isinstance(name, str) and name and "\x00" not in name and name not in seen, "invalid or duplicate pin path")
        need(type(size) is int and size >= 0, f"invalid pin size: {name}")
        need(isinstance(digest, str) and HEX64.fullmatch(digest) is not None, f"invalid pin digest: {name}")
        seen.add(name)
        normalized.append((name, size, digest))
    payload = b"".join(f"{name}\0{size}\0{digest}\n".encode("utf-8") for name, size, digest in sorted(normalized))
    return sha256_bytes(payload)


def audit_design_review(evidence_root: Path, plan_record: Mapping[str, Any], profile_record: Mapping[str, Any]) -> dict[str, Any]:
    path = evidence_root / DESIGN_REVIEW_NAME
    payload = duplicate_safe_json(path)
    need(
        set(payload) == {
            "schemaVersion", "status", "runId", "profileId", "createdAt", "reviewer", "target",
            "checks", "decision", "dependencyFingerprint", "publication",
        },
        "design review top-level schema drift",
    )
    need(payload["schemaVersion"] == "feelm-service-v1-b1-r4-design-review/1", "design review schema version drift")
    need(payload["status"] == "PASS", "design review is not PASS")
    need(payload["runId"] == RUN_ID and payload["profileId"] == PROFILE_ID, "design review identity drift")
    need(isinstance(payload["createdAt"], str) and payload["createdAt"].endswith("Z"), "design review timestamp drift")
    reviewer = payload["reviewer"]
    need(isinstance(reviewer, dict) and set(reviewer) == {"kind", "sessionId", "host", "processId"}, "design reviewer schema drift")
    need(
        reviewer["kind"] == "INDEPENDENT_DESIGN_REVIEWER"
        and all(isinstance(reviewer[key], str) and reviewer[key] for key in ("sessionId", "host"))
        and type(reviewer["processId"]) is int and reviewer["processId"] > 0,
        "design reviewer value drift",
    )
    need(payload["target"] == {"plan": plan_record, "profile": profile_record}, "design review target drift")
    need(
        isinstance(payload["checks"], dict)
        and set(payload["checks"]) == DESIGN_CHECK_KEYS
        and all(value is True for value in payload["checks"].values()),
        "design review checks drift",
    )
    expected_decision = {
        "designIntegrity": "PASS", "implementationEligible": True, "deliveryBuildEligible": False,
        "publicPreflightEligible": False, "fitEligible": False, "scoreEligible": False,
        "evaluationEligible": False, "deploymentAuthorized": False,
    }
    need(payload["decision"] == expected_decision, "design review decision drift")
    dependency_fingerprint = record_set_digest([plan_record, profile_record])
    need(payload["dependencyFingerprint"] == dependency_fingerprint, "design dependency drift")
    publication = payload["publication"]
    need(isinstance(publication, dict) and set(publication) == DESIGN_PUBLICATION_KEYS, "design publication schema drift")
    need(
        publication["mode"] == "BOOTSTRAP_LINUX_RENAME_NOREPLACE"
        and publication["filesystemType"] == "ext2/ext3"
        and publication["renameNoReplaceProbe"] is True,
        "design publication mode drift",
    )
    canonical_root = evidence_root.absolute()
    need(Path(publication["canonicalRoot"]) == canonical_root, "design canonical root drift")
    need(Path(publication["claimPath"]) == canonical_root / f".{DESIGN_REVIEW_NAME}.claim", "design claim path drift")
    temp_path = Path(publication["tempPath"])
    need(
        temp_path.parent == canonical_root
        and temp_path.name.startswith(f".{DESIGN_REVIEW_NAME}.tmp-")
        and len(temp_path.name) > len(f".{DESIGN_REVIEW_NAME}.tmp-"),
        "design temp path drift",
    )
    need(not os.path.lexists(publication["claimPath"]) and not os.path.lexists(publication["tempPath"]), "design bootstrap claim or temp residue")
    source = publication["bootstrapPublisherSource"]
    need(isinstance(source, str), "design bootstrap source type drift")
    need(
        publication["bootstrapPublisherSha256"] == DESIGN_BOOTSTRAP_SOURCE_SHA256
        and sha256_bytes(source.encode("utf-8")) == DESIGN_BOOTSTRAP_SOURCE_SHA256,
        "design bootstrap publisher drift",
    )
    interpreter_path = Path(publication["interpreterPath"])
    need(interpreter_path.is_absolute(), "design interpreter path drift")
    need(
        isinstance(publication["interpreterSha256"], str)
        and HEX64.fullmatch(publication["interpreterSha256"]) is not None
        and safe_regular(interpreter_path).st_size > 0
        and sha256_file(interpreter_path) == publication["interpreterSha256"],
        "design interpreter pin drift",
    )
    need(
        publication["dependencyFingerprintAtAcquire"] == dependency_fingerprint
        and publication["dependencyFingerprintBeforeRename"] == dependency_fingerprint,
        "design publication dependency drift",
    )
    postconditions = publication["requiredPostconditions"]
    need(
        isinstance(postconditions, dict)
        and set(postconditions) == DESIGN_POSTCONDITION_KEYS
        and all(value is True for value in postconditions.values()),
        "design publication postconditions drift",
    )
    return pin(path, f"evidence/{DESIGN_REVIEW_NAME}")


def audit_profile(profile_path: Path) -> None:
    profile = duplicate_safe_json(profile_path)
    canonical_path = Path(__file__).resolve().parents[1] / PROFILE_RELATIVE
    pin_exact(canonical_path, PROFILE_PIN, PROFILE_RELATIVE)
    expected = duplicate_safe_json(canonical_path)
    need(profile == expected, "profile /2 exact schema or value drift")


def resolve_ancestor(root: Path, logical: str) -> Path:
    if logical.startswith("standalone/"):
        relative = logical.removeprefix("standalone/")
    elif logical == "ancestor/service-v1-b1-spark-runner.md":
        relative = "docs/recommendation/plans/service-v1-b1-spark-runner.md"
    elif logical == "execution/local4c12g-t14400-profile.json":
        relative = "docs/recommendation/plans/service-v1-b1-r3-local4c12g-t14400-profile.json"
    elif logical == "execution/run_service_v1_b1_gbt_r3.py":
        relative = "scripts/run_service_v1_b1_gbt_r3.py"
    elif logical == "implementation/service_v1_b1_spark_worker.py":
        relative = "scripts/service_v1_b1_spark_worker.py"
    else:
        raise ImplementationAuditError(f"unrecognized ancestor logical path: {logical}")
    candidate = root / relative
    need(".." not in Path(relative).parts, f"ancestor traversal: {logical}")
    return candidate


def audit_r3_ancestry(root: Path) -> None:
    for relative, expected in R3_PINS.items():
        pin_exact(root / relative, expected, relative)
    recovery_path = root / "outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-gbt120-s339-v1-r3-local4c12g-t14400-preflight/recovery-reference.json"
    recovery = duplicate_safe_json(recovery_path, require_lf=False)
    need(recovery.get("schemaVersion") == "feelm-service-v1-b1-r3-recovery-reference/1", "r3 recovery schema drift")
    need(recovery.get("status") == "R2_ANCESTRY_VERIFIED" and recovery.get("r2RunId") == "b1-gbt120-s339-v1-r2", "r2 ancestry state drift")
    recursive: list[Mapping[str, Any]] = [
        recovery["r2FitFailure"], recovery["r2OuterRunner"], recovery["r2Plan"],
        recovery["r2PreflightReview"], *recovery["r2PreflightBundleInventory"],
    ]
    seen: set[str] = set()
    for record in recursive:
        need(set(record) == {"path", "bytes", "sha256"}, "r2 ancestor pin shape drift")
        logical = record["path"]
        need(logical not in seen, f"duplicate r2 ancestor: {logical}")
        seen.add(logical)
        need(pin(resolve_ancestor(root, logical), logical) == record, f"r2 ancestor pin drift: {logical}")
    failure = duplicate_safe_json(root / "outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-gbt120-s339-v1-r3-local4c12g-t14400-fit-failure.json", require_lf=False)
    container = failure.get("containerRun", {})
    resource = failure.get("resourceEvidence", {})
    census = failure.get("successPathCensus", {})
    need(failure.get("status") == "FAILED" and failure.get("phase") == "fit", "r3 failure identity drift")
    need(failure.get("cleanupComplete") is True and container.get("cleanupErrors") == [], "r3 cleanup facts drift")
    need(resource.get("timedOut") is True and resource.get("oomKilled") is False and resource.get("exitCode") == 143, "r3 timeout/OOM facts drift")
    need(census.get("bundle", {}).get("lexists") is False and census.get("modelNative", {}).get("lexists") is False and census.get("review", {}).get("lexists") is False, "r3 downstream absence drift")
    failure_review = duplicate_safe_json(root / "outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-gbt120-s339-v1-r3-local4c12g-t14400-fit-failure-result-review.json", require_lf=False)
    decision = failure_review.get("decision", {})
    need(failure_review.get("status") == "PASS" and decision.get("downstream") == "BLOCK" and decision.get("failureIntegrity") == "PASS", "r3 failure review decision drift")


def wheel_runtime_records(root: Path) -> list[dict[str, Any]]:
    manifest_path = root / "runtime/service-v1-b1-r4-wheelhouse-manifest.json"
    manifest = duplicate_safe_json(manifest_path)
    need(set(manifest) == {"schemaVersion", "pythonVersion", "interpreterTag", "abiTag", "platformTag", "files", "wheelhouseSetSha256"}, "wheelhouse manifest schema drift")
    need((manifest["pythonVersion"], manifest["interpreterTag"], manifest["abiTag"], manifest["platformTag"]) == ("3.12.3", "cp312", "cp312", "manylinux_2_17_x86_64"), "wheelhouse ABI drift")
    need(isinstance(manifest["files"], list) and manifest["files"], "wheelhouse files required")
    records: list[dict[str, Any]] = []
    for declared in manifest["files"]:
        need(set(declared) == {"path", "bytes", "sha256"}, "wheel record shape drift")
        relative = declared["path"]
        need(isinstance(relative, str) and relative.startswith("runtime/service-v1-b1-r4-wheelhouse/") and ".." not in Path(relative).parts, "wheel path drift")
        actual = pin(root / relative, relative)
        need(actual == declared, f"wheel pin drift: {relative}")
        records.append(actual)
    actual_names = sorted(path.relative_to(root).as_posix() for path in (root / "runtime/service-v1-b1-r4-wheelhouse").iterdir())
    need(actual_names == sorted(record["path"] for record in records), "wheelhouse missing or extra file")
    need(record_set_digest(records) == manifest["wheelhouseSetSha256"], "wheelhouse set digest drift")
    audit_requirements_lock(root, records)
    return [pin(manifest_path, RUNTIME_FIXED[1]), *records]


def normalized_distribution(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def audit_requirements_lock(root: Path, wheel_records: Sequence[Mapping[str, Any]]) -> None:
    available: dict[tuple[str, str], str] = {}
    for record in wheel_records:
        wheel = root / str(record["path"])
        wheel_raw = read_regular_bytes(wheel, maximum_bytes=256 * 1024 * 1024)
        need((len(wheel_raw), sha256_bytes(wheel_raw)) == (record["bytes"], record["sha256"]), f"wheel bound bytes drift: {wheel.name}")
        with zipfile.ZipFile(io.BytesIO(wheel_raw)) as archive:
            metadata_names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
            need(len(metadata_names) == 1, f"wheel METADATA count drift: {wheel.name}")
            metadata = archive.read(metadata_names[0]).decode("utf-8")
        names = [line[6:] for line in metadata.splitlines() if line.startswith("Name: ")]
        versions = [line[9:] for line in metadata.splitlines() if line.startswith("Version: ")]
        need(len(names) == 1 and len(versions) == 1, f"wheel name/version metadata drift: {wheel.name}")
        key = (normalized_distribution(names[0]), versions[0])
        need(key not in available, f"duplicate wheel distribution: {key}")
        available[key] = str(record["sha256"])
    lock_path = root / RUNTIME_FIXED[0]
    raw = read_regular_bytes(lock_path, maximum_bytes=1024 * 1024)
    need(not raw.startswith(b"\xef\xbb\xbf") and b"\r" not in raw and raw.endswith(b"\n"), "requirements lock must be UTF-8 LF")
    locked: dict[tuple[str, str], str] = {}
    pattern = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s]+) --hash=sha256:([0-9a-f]{64})$")
    for line in raw.decode("utf-8").splitlines():
        match = pattern.fullmatch(line)
        need(match is not None, f"unhashed or malformed requirement: {line}")
        key = (normalized_distribution(match.group(1)), match.group(2))
        need(key not in locked, f"duplicate requirement: {key}")
        locked[key] = match.group(3)
    need(locked == available, "requirements and wheelhouse differ")
    required = {"numpy": "1.26.4", "pandas": "2.2.3", "pyarrow": "19.0.1"}
    for name, version in required.items():
        need((name, version) in locked, f"required runtime version missing: {name}=={version}")


_DYNAMIC_IMPORT_CALLS: dict[str, tuple[tuple[str, str, tuple[str, ...]], ...]] = {
    "scripts/audit_service_v1_b1_evaluation_outputs_r4.py": (
        ("independently_verified_server_dependencies", "exec_module", ("Name(id='runner', ctx=Load())",)),
        ("independently_verified_server_dependencies", "module_from_spec", ("Name(id='spec', ctx=Load())",)),
        ("independently_verified_server_dependencies", "spec_from_file_location", ("Constant(value='r4_review_control_ancestry')", "Name(id='runner_path', ctx=Load())")),
        ("load_publication_module", "exec_module", ("Name(id='module', ctx=Load())",)),
        ("load_publication_module", "module_from_spec", ("Name(id='spec', ctx=Load())",)),
        ("load_publication_module", "spec_from_file_location", ("Name(id='name', ctx=Load())", "Name(id='path', ctx=Load())")),
    ),
    "scripts/audit_service_v1_b1_r4_implementation.py": (
        ("load_publication_module", "compile", ("Name(id='raw', ctx=Load())", "Call(func=Name(id='str', ctx=Load()), args=[Name(id='source', ctx=Load())], keywords=[])", "Constant(value='exec')")),
        ("load_publication_module", "exec", ("Name(id='code', ctx=Load())", "Attribute(value=Name(id='module', ctx=Load()), attr='__dict__', ctx=Load())")),
    ),
    "scripts/audit_service_v1_b1_r4_server_delivery.py": (
        ("_publication_module", "exec_module", ("Name(id='module', ctx=Load())",)),
        ("_publication_module", "module_from_spec", ("Name(id='specification', ctx=Load())",)),
        ("_publication_module", "spec_from_file_location", ("Name(id='name', ctx=Load())", "Name(id='path', ctx=Load())")),
    ),
    "scripts/audit_service_v1_b1_r4_server_receipt.py": (
        ("_publication_module", "import_module", ("Constant(value='service_v1_b1_r4_publication')",)),
        ("validate_reservation", "__import__", ("Constant(value='uuid')",)),
    ),
    "scripts/audit_service_v1_b1_spark_outputs_r4.py": (
        ("load_publication_module", "__import__", ("Constant(value='sys')",)),
        ("load_publication_module", "__import__", ("Constant(value='sys')",)),
        ("load_publication_module", "__import__", ("Constant(value='sys')",)),
        ("load_publication_module", "exec_module", ("Name(id='module', ctx=Load())",)),
        ("load_publication_module", "module_from_spec", ("Name(id='specification', ctx=Load())",)),
        ("load_publication_module", "spec_from_file_location", ("Name(id='name', ctx=Load())", "Name(id='path', ctx=Load())")),
    ),
    "scripts/build_service_v1_b1_r4_server_delivery.py": (
        ("_load_publication_module", "exec_module", ("Name(id='module', ctx=Load())",)),
        ("_load_publication_module", "module_from_spec", ("Name(id='specification', ctx=Load())",)),
        ("_load_publication_module", "spec_from_file_location", ("Name(id='name', ctx=Load())", "Name(id='path', ctx=Load())")),
    ),
    "scripts/build_service_v1_b1_r4_server_receipt.py": (
        ("_publication_module", "import_module", ("Constant(value='service_v1_b1_r4_publication')",)),
    ),
    "scripts/evaluate_service_v1_b1_r4.py": (
        ("load_publication_module", "exec_module", ("Name(id='module', ctx=Load())",)),
        ("load_publication_module", "module_from_spec", ("Name(id='spec', ctx=Load())",)),
        ("load_publication_module", "spec_from_file_location", ("Name(id='name', ctx=Load())", "Name(id='path', ctx=Load())")),
        ("main", "exec_module", ("Name(id='runner', ctx=Load())",)),
        ("main", "module_from_spec", ("Name(id='module_spec', ctx=Load())",)),
        ("main", "spec_from_file_location", ("Constant(value='service_v1_b1_r4_supervisor')", "Name(id='runner_path', ctx=Load())")),
    ),
    "scripts/run_service_v1_b1_gbt_r4.py": (
        ("_publication_api", "exec_module", ("Name(id='module', ctx=Load())",)),
        ("_publication_api", "module_from_spec", ("Name(id='specification', ctx=Load())",)),
        ("_publication_api", "spec_from_file_location", ("Name(id='name', ctx=Load())", "Name(id='expected', ctx=Load())")),
        ("_run_evaluation_supervised_impl", "exec_module", ("Name(id='evaluator', ctx=Load())",)),
        ("_run_evaluation_supervised_impl", "module_from_spec", ("Name(id='module_spec', ctx=Load())",)),
        ("_run_evaluation_supervised_impl", "spec_from_file_location", ("Name(id='module_name', ctx=Load())", "Name(id='evaluator_path', ctx=Load())")),
    ),
}


_DYNAMIC_IMPORT_BINDINGS: dict[tuple[str, str, str], str] = {
    ("scripts/audit_service_v1_b1_evaluation_outputs_r4.py", "load_publication_module", "name"): "Constant(value='service_v1_b1_r4_publication')",
    ("scripts/audit_service_v1_b1_evaluation_outputs_r4.py", "load_publication_module", "path"): "BinOp(left=Name(id='ROOT', ctx=Load()), op=Div(), right=Constant(value='scripts/service_v1_b1_r4_publication.py'))",
    ("scripts/audit_service_v1_b1_evaluation_outputs_r4.py", "independently_verified_server_dependencies", "runner_path"): "BinOp(left=Name(id='ROOT', ctx=Load()), op=Div(), right=Constant(value='scripts/run_service_v1_b1_gbt_r4.py'))",
    ("scripts/audit_service_v1_b1_r4_implementation.py", "load_publication_module", "source"): "BinOp(left=Name(id='root', ctx=Load()), op=Div(), right=Constant(value='scripts/service_v1_b1_r4_publication.py'))",
    ("scripts/audit_service_v1_b1_r4_server_delivery.py", "_publication_module", "name"): "Constant(value='service_v1_b1_r4_publication')",
    ("scripts/audit_service_v1_b1_r4_server_delivery.py", "_publication_module", "path"): "Call(func=Attribute(value=Call(func=Name(id='Path', ctx=Load()), args=[Name(id='__file__', ctx=Load())], keywords=[]), attr='with_name', ctx=Load()), args=[Constant(value='service_v1_b1_r4_publication.py')], keywords=[])",
    ("scripts/audit_service_v1_b1_spark_outputs_r4.py", "load_publication_module", "name"): "Constant(value='service_v1_b1_r4_publication')",
    ("scripts/audit_service_v1_b1_spark_outputs_r4.py", "load_publication_module", "path"): "Call(func=Attribute(value=Call(func=Attribute(value=Call(func=Name(id='Path', ctx=Load()), args=[Name(id='__file__', ctx=Load())], keywords=[]), attr='resolve', ctx=Load()), args=[], keywords=[]), attr='with_name', ctx=Load()), args=[Constant(value='service_v1_b1_r4_publication.py')], keywords=[])",
    ("scripts/build_service_v1_b1_r4_server_delivery.py", "_load_publication_module", "name"): "Constant(value='service_v1_b1_r4_publication')",
    ("scripts/build_service_v1_b1_r4_server_delivery.py", "_load_publication_module", "path"): "Call(func=Attribute(value=Call(func=Name(id='Path', ctx=Load()), args=[Name(id='__file__', ctx=Load())], keywords=[]), attr='with_name', ctx=Load()), args=[Constant(value='service_v1_b1_r4_publication.py')], keywords=[])",
    ("scripts/evaluate_service_v1_b1_r4.py", "load_publication_module", "name"): "Constant(value='service_v1_b1_r4_publication')",
    ("scripts/evaluate_service_v1_b1_r4.py", "load_publication_module", "path"): "BinOp(left=Name(id='ROOT', ctx=Load()), op=Div(), right=Constant(value='scripts/service_v1_b1_r4_publication.py'))",
    ("scripts/evaluate_service_v1_b1_r4.py", "main", "runner_path"): "BinOp(left=Name(id='ROOT', ctx=Load()), op=Div(), right=Constant(value='scripts/run_service_v1_b1_gbt_r4.py'))",
    ("scripts/run_service_v1_b1_gbt_r4.py", "_publication_api", "name"): "Constant(value='service_v1_b1_r4_publication')",
    ("scripts/run_service_v1_b1_gbt_r4.py", "_publication_api", "expected"): "Call(func=Attribute(value=Call(func=Attribute(value=Call(func=Name(id='Path', ctx=Load()), args=[Name(id='__file__', ctx=Load())], keywords=[]), attr='resolve', ctx=Load()), args=[], keywords=[]), attr='with_name', ctx=Load()), args=[Constant(value='service_v1_b1_r4_publication.py')], keywords=[])",
    ("scripts/run_service_v1_b1_gbt_r4.py", "_run_evaluation_supervised_impl", "module_name"): "Constant(value='evaluate_service_v1_b1_r4')",
    ("scripts/run_service_v1_b1_gbt_r4.py", "_run_evaluation_supervised_impl", "evaluator_path"): "BinOp(left=Attribute(value=Name(id='paths', ctx=Load()), attr='standalone', ctx=Load()), op=Div(), right=Constant(value='scripts/evaluate_service_v1_b1_r4.py'))",
}


def _enclosing_function(node: ast.AST, parents: Mapping[ast.AST, ast.AST]) -> str:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current.name
    return "<module>"


def _dynamic_call_record(node: ast.Call, parents: Mapping[ast.AST, ast.AST]) -> tuple[str, str, tuple[str, ...]] | None:
    if isinstance(node.func, ast.Name) and node.func.id in {"__import__", "eval", "exec", "compile"}:
        leaf = node.func.id
    elif isinstance(node.func, ast.Attribute) and node.func.attr in {
        "spec_from_file_location", "module_from_spec", "exec_module", "import_module",
        "load_module", "SourceFileLoader", "spec_from_loader",
    }:
        leaf = node.func.attr
    else:
        return None
    need(not node.keywords or leaf == "compile", "dynamic import/load keyword surface drift")
    if leaf == "compile":
        need(
            tuple((item.arg, ast.dump(item.value, include_attributes=False)) for item in node.keywords)
            == (("dont_inherit", "Constant(value=True)"), ("optimize", "Attribute(value=Attribute(value=Name(id='sys', ctx=Load()), attr='flags', ctx=Load()), attr='optimize', ctx=Load())")),
            "dynamic compile keyword surface drift",
        )
    return (
        _enclosing_function(node, parents),
        leaf,
        tuple(ast.dump(item, include_attributes=False) for item in node.args),
    )


def _audit_dynamic_import_surface(relative: str, tree: ast.AST) -> None:
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    actual = [record for node in ast.walk(tree) if isinstance(node, ast.Call) if (record := _dynamic_call_record(node, parents)) is not None]
    expected = list(_DYNAMIC_IMPORT_CALLS.get(relative, ()))
    need(sorted(actual) == sorted(expected), f"dynamic import/load surface drift: {relative}")
    functions = {
        node.name: node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for (path, function_name, variable), expected_value in _DYNAMIC_IMPORT_BINDINGS.items():
        if path != relative:
            continue
        function = functions.get(function_name)
        need(function is not None, f"dynamic loader function missing: {relative}:{function_name}")
        values: list[str] = []
        for node in ast.walk(function):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == variable for target in targets):
                values.append(ast.dump(node.value, include_attributes=False))
        need(values == [expected_value], f"dynamic loader binding drift: {relative}:{function_name}:{variable}")


def audit_combination340_dependency(root: Path) -> None:
    lock_relative = "outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-gbt120-s339-v1-r3-local4c12g-t14400-preflight/input-lock.json"
    payload = duplicate_safe_json(root / lock_relative, require_lf=False)
    records = payload.get("workerRuntimeRecords")
    need(isinstance(records, list), "r3 worker runtime records missing")
    matches = [item for item in records if isinstance(item, dict) and item.get("path") == "implementation/combination340_models.py"]
    need(len(matches) == 1 and set(matches[0]) == {"path", "bytes", "sha256"}, "combination340_models r3 pin missing")
    actual = pin(root / "scripts/combination340_models.py", "implementation/combination340_models.py")
    need(actual == matches[0], "combination340_models r3 input-lock pin drift")


def audit_import_closure(trees: Mapping[str, ast.AST], root: Path) -> None:
    reviewed_modules = {Path(relative).stem for relative in SOURCE_FILES}
    allowed_roots = set(sys.stdlib_module_names) | {"__future__"} | PINNED_EXTERNAL_IMPORT_ROOTS | reviewed_modules | {"combination340_models"}
    combination_used = False
    for relative, tree in trees.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                need(node.level == 0 and node.module is not None, f"relative import forbidden: {relative}")
                modules = [node.module]
            else:
                continue
            for module in modules:
                root_name = module.split(".", 1)[0]
                need(root_name in allowed_roots, f"unpinned import forbidden: {relative}:{module}")
                if root_name not in reviewed_modules and root_name != "combination340_models":
                    shadow_candidates = (
                        root / f"{root_name}.py",
                        root / root_name / "__init__.py",
                        root / "scripts" / f"{root_name}.py",
                        root / "scripts" / root_name / "__init__.py",
                    )
                    need(
                        not any(os.path.lexists(candidate) for candidate in shadow_candidates),
                        f"unpinned repo-local import shadow forbidden: {relative}:{module}",
                    )
                combination_used = combination_used or root_name == "combination340_models"
        _audit_dynamic_import_surface(relative, tree)
    if combination_used:
        audit_combination340_dependency(root)


def audit_ast(source_records: Sequence[Mapping[str, Any]], root: Path) -> None:
    trees: dict[str, ast.AST] = {}
    for record in source_records:
        relative = str(record["path"])
        raw = read_regular_bytes(root / relative, maximum_bytes=16 * 1024 * 1024)
        need((len(raw), sha256_bytes(raw)) == (record["bytes"], record["sha256"]), f"source bound bytes drift: {relative}")
        try:
            source = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ImplementationAuditError(f"source UTF-8 drift: {relative}") from error
        tree = ast.parse(source, filename=relative)
        need(not any(isinstance(node, ast.Assert) for node in ast.walk(tree)), f"production assert forbidden: {relative}")
        trees[relative] = tree
    audit_import_closure(trees, root)


def audit_r4_source_inventory(root: Path) -> None:
    def candidates(directory: str) -> set[str]:
        base = root / directory
        result: set[str] = set()
        for entry in base.iterdir():
            if entry.name.endswith(".py") and "r4" in entry.name:
                need(entry.is_file() and not entry.is_symlink(), f"unsafe r4 implementation entry: {entry}")
                result.add(entry.relative_to(root).as_posix())
        return result

    need(candidates("scripts") == set(SOURCE_FILES), "r4 production source inventory has missing or extra files")
    need(candidates("tests") == set(TEST_FILES), "r4 test source inventory has missing or extra files")


def _json_bytes_value(raw: bytes, label: str) -> Any:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            need(key not in result, f"duplicate JSON key {key}: {label}")
            result[key] = value
        return result

    need(not raw.startswith(b"\xef\xbb\xbf"), f"JSON BOM forbidden: {label}")
    return json.loads(
        raw.decode("utf-8"), object_pairs_hook=pairs,
        parse_constant=lambda token: (_ for _ in ()).throw(ImplementationAuditError(f"non-finite JSON {token}: {label}")),
    )


def _json_bytes_object(raw: bytes, label: str) -> dict[str, Any]:
    value = _json_bytes_value(raw, label)
    need(isinstance(value, dict), f"JSON object required: {label}")
    return value


def audit_docker_archive(path: Path) -> None:
    descriptor_fd, descriptor_signature, descriptor_chain = _open_bound_regular(path)
    duplicate = os.dup(descriptor_fd)
    try:
        with os.fdopen(duplicate, "rb", closefd=True) as archive_stream, tarfile.open(fileobj=archive_stream, mode="r:") as archive:
            duplicate = -1
            members = archive.getmembers()
            need(members, "Docker archive is empty")
            member_by_name: dict[str, tarfile.TarInfo] = {}
            for member in members:
                name = member.name
                parts = Path(name).parts
                need(name not in member_by_name and name and not name.startswith("/") and ".." not in parts, "unsafe or duplicate Docker archive member")
                need(member.isfile() or member.isdir(), f"Docker archive special/link member forbidden: {name}")
                member_by_name[name] = member

            control_names = {"index.json", "manifest.json", "oci-layout"}
            need(control_names.issubset(member_by_name), "Docker OCI control files missing")
            for name in control_names:
                need(member_by_name[name].isfile(), f"Docker control member is not regular: {name}")

            def member_bytes(name: str, maximum: int) -> bytes:
                member = member_by_name[name]
                need(member.isfile() and 0 < member.size <= maximum, f"Docker member size drift: {name}")
                stream = archive.extractfile(member)
                need(stream is not None, f"Docker member unreadable: {name}")
                raw = stream.read(maximum + 1)
                need(len(raw) == member.size and len(raw) <= maximum, f"Docker member byte count drift: {name}")
                return raw

            def descriptor_fields(value: Any, label: str, *, allow_platform: bool, allow_annotations: bool) -> tuple[str, str, int]:
                need(isinstance(value, dict), f"Docker descriptor object required: {label}")
                optional = ({"platform"} if allow_platform else set()) | ({"annotations"} if allow_annotations else set())
                need(set(value) >= {"mediaType", "digest", "size"} and set(value) <= {"mediaType", "digest", "size"} | optional, f"Docker descriptor schema drift: {label}")
                media_type, digest, size = value["mediaType"], value["digest"], value["size"]
                need(isinstance(media_type, str) and media_type, f"Docker descriptor mediaType drift: {label}")
                need(isinstance(digest, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is not None, f"Docker descriptor digest drift: {label}")
                need(type(size) is int and size > 0, f"Docker descriptor size drift: {label}")
                if "platform" in value:
                    platform_value = value["platform"]
                    need(isinstance(platform_value, dict) and set(platform_value) == {"architecture", "os"}, f"Docker platform schema drift: {label}")
                    need(all(isinstance(platform_value[key], str) and platform_value[key] for key in ("architecture", "os")), f"Docker platform value drift: {label}")
                if "annotations" in value:
                    annotations = value["annotations"]
                    need(isinstance(annotations, dict) and all(isinstance(key, str) and isinstance(item, str) for key, item in annotations.items()), f"Docker annotations drift: {label}")
                return media_type, digest, size

            reachable: set[str] = set()
            raw_cache: dict[str, bytes] = {}

            def read_descriptor_blob(value: Any, label: str, *, json_blob: bool, allow_platform: bool = False, allow_annotations: bool = False) -> tuple[str, bytes | None]:
                media_type, digest, size = descriptor_fields(value, label, allow_platform=allow_platform, allow_annotations=allow_annotations)
                name = "blobs/sha256/" + digest.removeprefix("sha256:")
                need(name in member_by_name, f"Docker descriptor blob missing: {label}")
                member = member_by_name[name]
                need(member.isfile() and member.size == size, f"Docker descriptor blob size drift: {label}")
                if name in reachable:
                    return media_type, raw_cache.get(name)
                stream = archive.extractfile(member)
                need(stream is not None, f"Docker descriptor blob unreadable: {label}")
                digest_state = hashlib.sha256()
                raw_parts: list[bytes] = []
                count = 0
                while True:
                    chunk = stream.read(8 * 1024 * 1024)
                    if not chunk:
                        break
                    count += len(chunk)
                    need(count <= size, f"Docker descriptor blob overflow: {label}")
                    digest_state.update(chunk)
                    if json_blob:
                        need(count <= 4 * 1024 * 1024, f"Docker JSON blob too large: {label}")
                        raw_parts.append(chunk)
                need(count == size and digest_state.hexdigest() == digest.removeprefix("sha256:"), f"Docker descriptor blob digest drift: {label}")
                reachable.add(name)
                raw = b"".join(raw_parts) if json_blob else None
                if raw is not None:
                    raw_cache[name] = raw
                return media_type, raw

            oci_layout = _json_bytes_object(member_bytes("oci-layout", 1024), "Docker oci-layout")
            need(oci_layout == {"imageLayoutVersion": "1.0.0"}, "Docker oci-layout drift")

            index = _json_bytes_object(member_bytes("index.json", 1024 * 1024), "Docker index.json")
            need(set(index) == {"schemaVersion", "mediaType", "manifests"}, "Docker index schema drift")
            need(index["schemaVersion"] == 2 and index["mediaType"] == "application/vnd.oci.image.index.v1+json", "Docker index identity drift")
            outer_manifests = index["manifests"]
            need(isinstance(outer_manifests, list) and len(outer_manifests) == 1, "Docker outer manifest count drift")
            outer_descriptor = outer_manifests[0]
            outer_media_type, outer_raw = read_descriptor_blob(outer_descriptor, "outer image index", json_blob=True, allow_annotations=True)
            need(outer_media_type == "application/vnd.oci.image.index.v1+json", "Docker outer descriptor mediaType drift")
            need(outer_descriptor["digest"] == DOCKER_IMAGE_ID, "Docker image ID drift")
            need(outer_raw is not None, "Docker nested index bytes missing")
            nested_index = _json_bytes_object(outer_raw, "Docker nested index")
            need(set(nested_index) == {"schemaVersion", "mediaType", "manifests"}, "Docker nested index schema drift")
            need(nested_index["schemaVersion"] == 2 and nested_index["mediaType"] == "application/vnd.oci.image.index.v1+json", "Docker nested index identity drift")
            nested_descriptors = nested_index["manifests"]
            need(isinstance(nested_descriptors, list) and len(nested_descriptors) == 2, "Docker nested manifest count drift")

            main_manifest: dict[str, Any] | None = None
            attestation_manifest: dict[str, Any] | None = None
            main_config_path = ""
            main_layer_paths: list[str] = []
            for position, nested_descriptor in enumerate(nested_descriptors):
                media_type, raw = read_descriptor_blob(
                    nested_descriptor, f"nested manifest {position}", json_blob=True,
                    allow_platform=True, allow_annotations=True,
                )
                need(media_type == "application/vnd.oci.image.manifest.v1+json" and raw is not None, "Docker nested manifest mediaType drift")
                manifest = _json_bytes_object(raw, f"Docker nested manifest {position}")
                need(set(manifest) == {"schemaVersion", "mediaType", "config", "layers"}, "Docker OCI manifest schema drift")
                need(manifest["schemaVersion"] == 2 and manifest["mediaType"] == "application/vnd.oci.image.manifest.v1+json", "Docker OCI manifest identity drift")
                need(isinstance(manifest["layers"], list) and manifest["layers"], "Docker OCI layers missing")
                platform_value = nested_descriptor.get("platform")
                if platform_value == {"architecture": "amd64", "os": "linux"}:
                    need(main_manifest is None and len(manifest["layers"]) == 12, "Docker linux/amd64 manifest drift")
                    main_manifest = manifest
                else:
                    need(platform_value == {"architecture": "unknown", "os": "unknown"} and attestation_manifest is None, "Docker attestation platform drift")
                    annotations = nested_descriptor.get("annotations")
                    need(isinstance(annotations, dict) and annotations.get("vnd.docker.reference.type") == "attestation-manifest", "Docker attestation annotation drift")
                    need(annotations.get("vnd.docker.reference.digest") == next(item["digest"] for item in nested_descriptors if item.get("platform") == {"architecture": "amd64", "os": "linux"}), "Docker attestation subject drift")
                    need(len(manifest["layers"]) == 1, "Docker attestation layer count drift")
                    attestation_manifest = manifest

                config_media, _ = read_descriptor_blob(manifest["config"], f"manifest {position} config", json_blob=True)
                need(config_media == "application/vnd.oci.image.config.v1+json", "Docker config mediaType drift")
                for layer_position, layer in enumerate(manifest["layers"]):
                    layer_media, _ = read_descriptor_blob(layer, f"manifest {position} layer {layer_position}", json_blob=False, allow_annotations=True)
                    if platform_value == {"architecture": "amd64", "os": "linux"}:
                        need(layer_media == "application/vnd.oci.image.layer.v1.tar+gzip", "Docker image layer mediaType drift")
                    else:
                        need(layer_media == "application/vnd.in-toto+json", "Docker attestation layer mediaType drift")

                if platform_value == {"architecture": "amd64", "os": "linux"}:
                    config_digest = manifest["config"]["digest"].removeprefix("sha256:")
                    main_config_path = "blobs/sha256/" + config_digest
                    main_layer_paths = ["blobs/sha256/" + item["digest"].removeprefix("sha256:") for item in manifest["layers"]]
                    config_raw = raw_cache[main_config_path]
                    config_value = _json_bytes_object(config_raw, "Docker linux/amd64 config")
                    need(config_value.get("architecture") == "amd64" and config_value.get("os") == "linux", "Docker config platform drift")

            need(main_manifest is not None and attestation_manifest is not None, "Docker platform manifest closure incomplete")

            legacy = _json_bytes_value(member_bytes("manifest.json", 1024 * 1024), "Docker manifest.json")
            need(isinstance(legacy, list) and len(legacy) == 1 and isinstance(legacy[0], dict), "Docker legacy manifest shape drift")
            legacy_item = legacy[0]
            need(set(legacy_item) == {"Config", "RepoTags", "Layers"}, "Docker legacy manifest schema drift")
            need(legacy_item["Config"] == main_config_path and legacy_item["RepoTags"] is None and legacy_item["Layers"] == main_layer_paths, "Docker legacy manifest cross-reference drift")

            expected_names = control_names | {"blobs", "blobs/sha256"} | reachable
            need(set(member_by_name) == expected_names, "Docker archive has missing or unreachable members")
            need(member_by_name["blobs"].isdir() and member_by_name["blobs/sha256"].isdir(), "Docker blob directories drift")
        _verify_bound_regular(path, descriptor_fd, descriptor_signature, descriptor_chain)
    finally:
        if duplicate >= 0:
            os.close(duplicate)
        os.close(descriptor_fd)


def normalized_auditor_core(path: Path) -> str:
    raw = read_regular_bytes(path, maximum_bytes=16 * 1024 * 1024)
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ImplementationAuditError("evaluation auditor source UTF-8 drift") from error
    tree = ast.parse(source)
    assignments = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and ((isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "REVIEWED_R4_EVALUATOR_SHA256" for t in node.targets))
             or (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "REVIEWED_R4_EVALUATOR_SHA256"))
    ]
    need(len(assignments) == 1, "evaluation auditor mutual-pin assignment count drift")
    assignment = assignments[0]
    value = assignment.value
    need(isinstance(value, ast.Constant) and isinstance(value.value, str) and HEX64.fullmatch(value.value), "evaluation auditor pin literal drift")
    start, end = value.col_offset + 1, value.end_col_offset - 1
    lines = source.splitlines(keepends=True)
    need(value.lineno == value.end_lineno, "evaluation auditor pin must be one-line literal")
    line = lines[value.lineno - 1]
    lines[value.lineno - 1] = line[:start] + ("0" * 64) + line[end:]
    return sha256_bytes("".join(lines).encode("utf-8"))


def audit_mutual_pins(root: Path) -> None:
    evaluator_path = root / "scripts/evaluate_service_v1_b1_r4.py"
    auditor_path = root / "scripts/audit_service_v1_b1_evaluation_outputs_r4.py"
    evaluator_raw = read_regular_bytes(evaluator_path, maximum_bytes=16 * 1024 * 1024)
    try:
        evaluator_source = evaluator_raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ImplementationAuditError("evaluator source UTF-8 drift") from error
    evaluator_tree = ast.parse(evaluator_source)
    assignments = [
        node for node in ast.walk(evaluator_tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and ((isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "REVIEWED_R4_EVALUATION_AUDITOR_CORE_SHA256" for t in node.targets))
             or (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "REVIEWED_R4_EVALUATION_AUDITOR_CORE_SHA256"))
    ]
    need(len(assignments) == 1, "evaluator mutual-pin assignment count drift")
    evaluator_expected = assignments[0].value
    need(isinstance(evaluator_expected, ast.Constant) and evaluator_expected.value == normalized_auditor_core(auditor_path), "evaluator normalized auditor pin drift")
    auditor_raw = read_regular_bytes(auditor_path, maximum_bytes=16 * 1024 * 1024)
    try:
        auditor_source = auditor_raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ImplementationAuditError("evaluation auditor source UTF-8 drift") from error
    auditor_tree = ast.parse(auditor_source)
    values: list[str] = []
    for node in ast.walk(auditor_tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "REVIEWED_R4_EVALUATOR_SHA256" for t in node.targets):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                values.append(node.value.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "REVIEWED_R4_EVALUATOR_SHA256":
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                values.append(node.value.value)
    need(values == [sha256_bytes(evaluator_raw)], "auditor evaluator pin drift")


def _identity_record(path: str, signature: tuple[int, int, int, int, int, int, int], digest: str) -> dict[str, Any]:
    return {
        "path": path,
        "bytes": signature[4],
        "sha256": digest,
        "stDev": signature[0],
        "stIno": signature[1],
        "mode": signature[2],
        "nlink": signature[3],
        "mtimeNs": signature[5],
        "ctimeNs": signature[6],
    }


def _validate_identity_record(value: Any, expected_path: str, expected_pin: tuple[int, str]) -> None:
    need(
        isinstance(value, dict)
        and set(value) == {"path", "bytes", "sha256", "stDev", "stIno", "mode", "nlink", "mtimeNs", "ctimeNs"},
        "test interpreter identity schema drift",
    )
    need(value["path"] == expected_path, "test interpreter identity path drift")
    need((value["bytes"], value["sha256"]) == expected_pin, "test interpreter identity pin drift")
    need(
        all(type(value[key]) is int and value[key] >= 0 for key in ("stDev", "stIno", "mode", "mtimeNs", "ctimeNs"))
        and type(value["nlink"]) is int and value["nlink"] == 1,
        "test interpreter descriptor identity drift",
    )


def test_closure_snapshot(root: Path) -> dict[str, Any]:
    """Return the private test closure that must match the public review target."""
    plan_record = pin_exact(root / PLAN_RELATIVE, PLAN_PIN, PLAN_RELATIVE)
    profile_record = pin_exact(root / PROFILE_RELATIVE, PROFILE_PIN, PROFILE_RELATIVE)
    source_records = [pin(root / relative, relative) for relative in SOURCE_FILES]
    test_records = [pin(root / relative, relative) for relative in TEST_FILES]
    runtime_records = [
        pin(root / RUNTIME_FIXED[0], RUNTIME_FIXED[0]),
        *wheel_runtime_records(root),
        pin(root / RUNTIME_FIXED[2], RUNTIME_FIXED[2]),
    ]
    target = {
        "plan": plan_record,
        "profile": profile_record,
        "sourceFiles": source_records,
        "testFiles": test_records,
        "runtimeFiles": runtime_records,
    }
    return {"target": target, "sha256": sha256_bytes(canonical_json(target))}


def _validate_test_closure(value: Any, label: str) -> None:
    need(isinstance(value, dict) and set(value) == {"target", "sha256"}, f"{label} closure schema drift")
    target = value["target"]
    need(
        isinstance(target, dict)
        and set(target) == {"plan", "profile", "sourceFiles", "testFiles", "runtimeFiles"},
        f"{label} closure target schema drift",
    )
    for singleton in ("plan", "profile"):
        record_set_digest([target[singleton]])
    for collection in ("sourceFiles", "testFiles", "runtimeFiles"):
        need(isinstance(target[collection], list), f"{label} closure {collection} drift")
        record_set_digest(target[collection])
    need(
        isinstance(value["sha256"], str)
        and HEX64.fullmatch(value["sha256"]) is not None
        and value["sha256"] == sha256_bytes(canonical_json(target)),
        f"{label} closure digest drift",
    )


def _parse_utc(value: Any, label: str) -> dt.datetime:
    need(isinstance(value, str) and value.endswith("Z"), f"{label} timestamp drift")
    try:
        parsed = dt.datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise ImplementationAuditError(f"{label} timestamp drift") from error
    need(parsed.tzinfo is not None and parsed.utcoffset() == dt.timedelta(0), f"{label} timestamp drift")
    return parsed


def _test_environment(executable: str, pycache: str) -> dict[str, str]:
    environment: dict[str, str] = {
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPYCACHEPREFIX": pycache,
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
    }
    if os.name == "nt":
        for key in ("SystemRoot", "WINDIR", "COMSPEC", "TEMP", "TMP", "USERPROFILE", "HOME"):
            value = os.environ.get(key)
            if value:
                environment[key] = value
        system_root = environment.get("SystemRoot") or environment.get("WINDIR")
        path_entries = [str(Path(executable).parent)]
        if system_root:
            path_entries.extend((str(Path(system_root) / "System32"), system_root))
        environment["PATH"] = os.pathsep.join(dict.fromkeys(path_entries))
    else:
        environment["HOME"] = os.environ.get("HOME", "/nonexistent")
        environment["PATH"] = "/usr/bin:/bin"
    return environment


def _validate_test_environment(value: Any) -> None:
    required = {
        "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1",
        "PYTHONNOUSERSITE": "1", "LC_ALL": "C.UTF-8", "TZ": "UTC",
    }
    allowed = set(required) | {
        "PYTHONPYCACHEPREFIX", "PATH", "SystemRoot", "WINDIR", "COMSPEC", "TEMP", "TMP",
        "USERPROFILE", "HOME",
    }
    need(isinstance(value, dict) and set(value) <= allowed, "test environment key drift")
    need(all(isinstance(key, str) and isinstance(item, str) and item for key, item in value.items()), "test environment value drift")
    need(all(value.get(key) == item for key, item in required.items()), "test environment control drift")
    need("PATH" in value and "PYTHONPYCACHEPREFIX" in value, "test environment required path drift")
    pycache = value["PYTHONPYCACHEPREFIX"]
    need(_absolute_executable(pycache), "test pycache path must be absolute")
    need("PYTHONPATH" not in value and "PYTHONHOME" not in value, "ambient Python path is forbidden")


def _validate_private_evidence_permissions(path: Path) -> None:
    info = safe_regular(path)
    parent, _chain = _lexical_chain(path.parent)
    need(stat.S_ISDIR(parent.st_mode), "test evidence parent must be a directory")
    if os.name != "nt":
        need(stat.S_IMODE(info.st_mode) & 0o077 == 0, "test evidence file is not reviewer-private")
        need(stat.S_IMODE(parent.st_mode) & 0o077 == 0, "test evidence directory is not reviewer-private")
        if hasattr(os, "geteuid"):
            need(parent.st_uid == os.geteuid(), "test evidence directory owner drift")


def read_test_run(
    path: Path,
    expected_kind: str,
    *,
    current_root: Path,
    current_closure: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    _validate_private_evidence_permissions(path)
    raw, evidence_pin = read_regular_bytes_with_pin(path, logical=path.as_posix(), maximum_bytes=64 * 1024 * 1024)
    need(not raw.startswith(b"\xef\xbb\xbf") and b"\r" not in raw and raw.endswith(b"\n"), "test evidence must be UTF-8 LF")
    payload = _json_bytes_object(raw, str(path))
    _validate_private_evidence_permissions(path)
    need(
        set(payload) == {
            "schemaVersion", "kind", "mode", "argv", "cwd", "environment",
            "interpreterBefore", "interpreterAfter", "closureBefore", "closureAfter",
            "startedAt", "completedAt", "durationNanoseconds", "process", "exitCode",
            "stdout", "stderr", "stdoutSha256", "stderrSha256",
        },
        "test evidence schema drift",
    )
    need(payload["schemaVersion"] == PRIVATE_TEST_EVIDENCE_SCHEMA and payload["kind"] == expected_kind, "test evidence identity drift")
    need(payload["mode"] == PRIVATE_TEST_EVIDENCE_MODE, "test evidence creation mode drift")
    need(isinstance(payload["argv"], list) and payload["argv"] and all(isinstance(value, str) for value in payload["argv"]), "test argv drift")
    validate_test_argv(expected_kind, payload["argv"])
    recorded_cwd = payload["cwd"]
    need(isinstance(recorded_cwd, str) and _absolute_executable(recorded_cwd), "test cwd must be absolute")
    need(_local_path_for_recorded_executable(recorded_cwd).resolve() == current_root.resolve(), "test cwd/current root drift")
    _validate_test_environment(payload["environment"])
    expected_interpreter_pin = LINUX_PUBLICATION_INTERPRETER_PIN if expected_kind == "linuxPublicationIntegration" else LOCAL_TEST_INTERPRETER_PIN
    _validate_identity_record(payload["interpreterBefore"], payload["argv"][0], expected_interpreter_pin)
    _validate_identity_record(payload["interpreterAfter"], payload["argv"][0], expected_interpreter_pin)
    need(payload["interpreterBefore"] == payload["interpreterAfter"], "test interpreter changed during execution")
    _validate_test_closure(payload["closureBefore"], "pre-execution")
    _validate_test_closure(payload["closureAfter"], "post-execution")
    need(payload["closureBefore"] == payload["closureAfter"], "test closure changed during execution")
    need(dict(payload["closureAfter"]) == dict(current_closure), "test evidence does not match current review target")
    started = _parse_utc(payload["startedAt"], "test start")
    completed = _parse_utc(payload["completedAt"], "test completion")
    need(completed >= started, "test completion precedes start")
    need(type(payload["durationNanoseconds"]) is int and payload["durationNanoseconds"] >= 0, "test duration drift")
    process = payload["process"]
    need(isinstance(process, dict) and set(process) == {"argvMatched", "returnCode", "terminatedBySignal", "signalNumber"}, "test process schema drift")
    need(process["argvMatched"] is True and type(process["returnCode"]) is int, "test process facts drift")
    expected_signal = -process["returnCode"] if process["returnCode"] < 0 else None
    need(process["terminatedBySignal"] is (expected_signal is not None) and process["signalNumber"] == expected_signal, "test process termination drift")
    need(type(payload["exitCode"]) is int and payload["exitCode"] == process["returnCode"] == 0, f"test failed: {expected_kind}")
    need(isinstance(payload["stdout"], str) and isinstance(payload["stderr"], str), "test output type drift")
    need(isinstance(payload["stdoutSha256"], str) and HEX64.fullmatch(payload["stdoutSha256"]) is not None, "test stdout SHA drift")
    need(isinstance(payload["stderrSha256"], str) and HEX64.fullmatch(payload["stderrSha256"]) is not None, "test stderr SHA drift")
    need(sha256_bytes(payload["stdout"].encode("utf-8")) == payload["stdoutSha256"], "test stdout digest drift")
    need(sha256_bytes(payload["stderr"].encode("utf-8")) == payload["stderrSha256"], "test stderr digest drift")
    summary = {key: payload[key] for key in ("argv", "exitCode", "stdoutSha256", "stderrSha256")}
    summary["evidence"] = evidence_pin
    need(set(summary) == {"argv", "exitCode", "stdoutSha256", "stderrSha256", "evidence"}, "public testRuns summary drift")
    need(pin(path, path.as_posix()) == evidence_pin, "test evidence changed during validation")
    return payload, summary


def _python_executable_name(value: str) -> str:
    return re.split(r"[\\/]", value)[-1].lower()


def _absolute_executable(value: str) -> bool:
    return value.startswith("/") or re.fullmatch(r"[A-Za-z]:[\\/].+", value) is not None


def _local_path_for_recorded_executable(value: str) -> Path:
    windows = re.fullmatch(r"([A-Za-z]):[\\/](.+)", value)
    if windows is None:
        return Path(value)
    if os.name == "nt":
        return Path(value)
    drive, rest = windows.groups()
    return Path("/mnt") / drive.lower() / Path(rest.replace("\\", "/"))


def audit_test_interpreter(kind: str, executable: str) -> None:
    if kind == "linuxPublicationIntegration":
        need(executable == LINUX_PUBLICATION_INTERPRETER, "Linux publication interpreter path drift")
        expected = LINUX_PUBLICATION_INTERPRETER_PIN
    else:
        need(executable == LOCAL_TEST_INTERPRETER, "local test interpreter path drift")
        expected = LOCAL_TEST_INTERPRETER_PIN
    pin_exact(_local_path_for_recorded_executable(executable), expected, executable)


def expected_test_argv(kind: str, executable: str) -> list[str]:
    need(_absolute_executable(executable), "test interpreter must be absolute")
    name = _python_executable_name(executable)
    need(name in {"python", "python.exe", "python3", "python3.12"}, "unexpected test interpreter")
    if kind in {"normal", "negative"}:
        return [executable, "-m", "unittest", *TEST_FILES]
    if kind == "optimized":
        return [executable, "-O", "-m", "unittest", *TEST_FILES]
    if kind == "pyCompile":
        return [executable, "-m", "py_compile", *SOURCE_FILES, *TEST_FILES]
    need(kind == "linuxPublicationIntegration", f"unknown test kind: {kind}")
    need(name in {"python3", "python3.12"}, "Linux publication integration requires python3")
    return [executable, "-m", "unittest", "tests/test_service_v1_b1_r4_publication.py"]


def validate_test_argv(kind: str, argv: Sequence[str]) -> None:
    need(isinstance(argv, Sequence) and not isinstance(argv, (str, bytes)) and len(argv) > 0, "test command required")
    need(all(isinstance(value, str) and value for value in argv), "test command contains an invalid argument")
    need(list(argv) == expected_test_argv(kind, argv[0]), f"{kind} command is not the exact reviewed invocation")
    audit_test_interpreter(kind, argv[0])


def unittest_count(payload: Mapping[str, Any], kind: str, minimum: int) -> int:
    combined = str(payload["stdout"]) + "\n" + str(payload["stderr"])
    matches = re.findall(r"Ran\s+(\d+)\s+tests?\s+in\s+", combined)
    need(len(matches) == 1, f"{kind} unittest completion record missing or ambiguous")
    need(re.search(r"(?:^|\n)OK(?:\s|\(|$)", combined) is not None and "FAILED" not in combined, f"{kind} unittest did not report OK")
    count = int(matches[0])
    need(count >= minimum, f"{kind} executed too few tests")
    return count


def record_test_run(kind: str, destination: Path, argv: Sequence[str], cwd: Path) -> int:
    need(kind in TEST_KINDS, f"unknown test kind: {kind}")
    need(destination.name == f"{kind}.json", "test evidence destination name drift")
    need(destination.is_absolute(), "test evidence destination must be absolute")
    validate_test_argv(kind, argv)
    need(cwd.resolve() == Path(__file__).resolve().parents[1], "test cwd must be the reviewed standalone root")
    need(not os.path.lexists(destination), f"test evidence already exists: {destination}")
    destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    parent, _chain = _lexical_chain(destination.parent)
    need(stat.S_ISDIR(parent.st_mode), "test evidence parent must be a directory")
    if os.name != "nt":
        need(stat.S_IMODE(parent.st_mode) & 0o077 == 0, "test evidence directory is not reviewer-private")
        if hasattr(os, "geteuid"):
            need(parent.st_uid == os.geteuid(), "test evidence directory owner drift")
    closure_before = test_closure_snapshot(cwd)
    interpreter_path = _local_path_for_recorded_executable(str(argv[0]))
    interpreter_descriptor, interpreter_signatures, interpreter_chain = _open_bound_regular(interpreter_path)
    with tempfile.TemporaryDirectory(prefix="feelm-r4-pycache-") as pycache:
        environment = _test_environment(str(argv[0]), str(Path(pycache).absolute()))
        _validate_test_environment(environment)
        try:
            before_count, before_digest = _hash_descriptor(interpreter_descriptor)
            need(before_count == interpreter_signatures[0][4], "test interpreter byte count drift before execution")
            interpreter_before = _identity_record(str(argv[0]), interpreter_signatures[0], before_digest)
            started_at = utc_now()
            started_ns = time.monotonic_ns()
            result = subprocess.run(
                list(argv), cwd=cwd, env=environment, capture_output=True,
                text=True, encoding="utf-8", errors="replace", check=False,
            )
            completed_ns = time.monotonic_ns()
            completed_at = utc_now()
            after_count, after_digest = _hash_descriptor(interpreter_descriptor)
            after_signature = _file_signature(os.fstat(interpreter_descriptor))
            need(after_count == after_signature[4], "test interpreter byte count drift after execution")
            _verify_bound_regular(interpreter_path, interpreter_descriptor, interpreter_signatures, interpreter_chain)
            interpreter_after = _identity_record(str(argv[0]), after_signature, after_digest)
        finally:
            os.close(interpreter_descriptor)
    need(interpreter_before == interpreter_after, "test interpreter changed during execution")
    closure_after = test_closure_snapshot(cwd)
    need(closure_before == closure_after, "test closure changed during execution")
    result_argv = list(result.args) if isinstance(result.args, Sequence) and not isinstance(result.args, (str, bytes)) else []
    return_code = result.returncode
    need(type(return_code) is int, "test process return code drift")
    signal_number = -return_code if return_code < 0 else None
    payload = {
        "schemaVersion": PRIVATE_TEST_EVIDENCE_SCHEMA, "kind": kind, "mode": PRIVATE_TEST_EVIDENCE_MODE,
        "argv": list(argv), "cwd": str(cwd.absolute()), "environment": environment,
        "interpreterBefore": interpreter_before, "interpreterAfter": interpreter_after,
        "closureBefore": closure_before, "closureAfter": closure_after,
        "startedAt": started_at, "completedAt": completed_at,
        "durationNanoseconds": completed_ns - started_ns,
        "process": {"argvMatched": result_argv == list(argv), "returnCode": return_code,
                    "terminatedBySignal": signal_number is not None, "signalNumber": signal_number},
        "exitCode": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
        "stdoutSha256": sha256_bytes(result.stdout.encode("utf-8")),
        "stderrSha256": sha256_bytes(result.stderr.encode("utf-8")),
    }
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0), 0o600)
    try:
        raw = canonical_json(payload)
        offset = 0
        while offset < len(raw):
            offset += os.write(descriptor, raw[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    if os.name != "nt":
        directory = os.open(destination.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    _validate_private_evidence_permissions(destination)
    need(pin(destination) == {"bytes": len(raw), "sha256": sha256_bytes(raw)}, "written test evidence drift")
    return result.returncode


def dependency_fingerprint(target: Mapping[str, Any], checks: Mapping[str, Any], test_runs: Mapping[str, Any], auditor_pin: Mapping[str, Any], publication_pin: Mapping[str, Any]) -> str:
    value = {"target": target, "checks": checks, "testRuns": test_runs, "auditor": auditor_pin, "publication": publication_pin}
    return sha256_bytes(canonical_json(value))


def lease_publication(lease: Any) -> dict[str, Any]:
    value = getattr(lease, "publication_evidence", None)
    value = value() if callable(value) else value
    if value is None:
        value = getattr(lease, "publication", None)
        value = value() if callable(value) else value
    need(isinstance(value, dict), "publication Lease does not expose evidence")
    return value


def as_pin(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    result = {name: getattr(value, name) for name in ("path", "bytes", "sha256") if hasattr(value, name)}
    need(set(result) == {"path", "bytes", "sha256"}, "publication Pin shape drift")
    return result


def load_publication_module(root: Path) -> Any:
    source = root / "scripts/service_v1_b1_r4_publication.py"
    raw, source_record = read_regular_bytes_with_pin(
        source, logical="scripts/service_v1_b1_r4_publication.py", maximum_bytes=2 * 1024 * 1024
    )
    source_digest = sha256_bytes(raw)
    name = "service_v1_b1_r4_publication_reviewed_" + source_digest[:16]
    code = compile(raw, str(source), "exec", dont_inherit=True, optimize=sys.flags.optimize)
    module = types.ModuleType(name)
    module.__file__ = str(source)
    module.__package__ = ""
    sys.modules[name] = module
    try:
        exec(code, module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    need(Path(module.__file__).resolve() == source.resolve(), "publication module path drift")
    need(module.RUN_ID == RUN_ID and module.PROFILE_ID == PROFILE_ID, "publication module identity drift")
    need(pin(source, "scripts/service_v1_b1_r4_publication.py") == source_record, "publication source changed during load")
    module._FEELM_REVIEWED_SOURCE_PIN = source_record
    return module


def build_review(root: Path, evidence_root: Path, test_evidence: Mapping[str, Path], reviewer_session: str, *, check_namespace: bool = True) -> tuple[dict[str, Any], str]:
    need(set(test_evidence) == set(TEST_KINDS), "private test evidence kind set drift")
    evidence_parents = {path.parent.absolute() for path in test_evidence.values()}
    need(len(evidence_parents) == 1, "private test evidence directory drift")
    private_root = next(iter(evidence_parents))
    private_info, _private_chain = _lexical_chain(private_root)
    need(stat.S_ISDIR(private_info.st_mode), "private test evidence root must be a directory")
    need(
        sorted(path.name for path in private_root.iterdir()) == sorted(f"{kind}.json" for kind in TEST_KINDS),
        "private test evidence namespace drift",
    )
    plan_record = pin_exact(root / PLAN_RELATIVE, PLAN_PIN, PLAN_RELATIVE)
    profile_record = pin_exact(root / PROFILE_RELATIVE, PROFILE_PIN, PROFILE_RELATIVE)
    audit_profile(root / PROFILE_RELATIVE)
    audit_r3_ancestry(root)
    design_record = audit_design_review(evidence_root, plan_record, profile_record)
    audit_r4_source_inventory(root)
    source_records = [pin(root / relative, relative) for relative in SOURCE_FILES]
    test_records = [pin(root / relative, relative) for relative in TEST_FILES]
    runtime_records = [pin(root / RUNTIME_FIXED[0], RUNTIME_FIXED[0]), *wheel_runtime_records(root), pin(root / RUNTIME_FIXED[2], RUNTIME_FIXED[2])]
    audit_docker_archive(root / RUNTIME_FIXED[2])
    audit_ast(source_records, root)
    audit_mutual_pins(root)
    current_test_closure = {
        "target": {
            "plan": plan_record,
            "profile": profile_record,
            "sourceFiles": source_records,
            "testFiles": test_records,
            "runtimeFiles": runtime_records,
        }
    }
    current_test_closure["sha256"] = sha256_bytes(canonical_json(current_test_closure["target"]))
    run_payloads: dict[str, Any] = {}
    test_runs: dict[str, Any] = {}
    for kind in TEST_KINDS:
        payload, summary = read_test_run(
            test_evidence[kind], kind, current_root=root, current_closure=current_test_closure
        )
        run_payloads[kind] = payload
        test_runs[kind] = summary
    normal_argv = run_payloads["normal"]["argv"]
    optimized_argv = run_payloads["optimized"]["argv"]
    for kind in TEST_KINDS:
        validate_test_argv(kind, run_payloads[kind]["argv"])
    need(normal_argv[0] == optimized_argv[0] == run_payloads["negative"]["argv"][0] == run_payloads["pyCompile"]["argv"][0], "local test interpreter drift")
    normal_count = unittest_count(run_payloads["normal"], "normal", 100)
    need(unittest_count(run_payloads["optimized"], "optimized", 100) == normal_count, "normal/optimized test count drift")
    need(unittest_count(run_payloads["negative"], "negative", 100) == normal_count, "normal/negative test count drift")
    unittest_count(run_payloads["linuxPublicationIntegration"], "linuxPublicationIntegration", 14)
    need(run_payloads["pyCompile"]["stdout"] == "" and run_payloads["pyCompile"]["stderr"] == "", "pyCompile emitted unexpected output")
    if check_namespace:
        namespace = sorted(path.name for path in evidence_root.iterdir())
        need(namespace == [DESIGN_REVIEW_NAME], f"implementation namespace drift: {namespace}")
    checks = {
        "schemaValid": True, "designReviewPass": True, "r3PinsVerified": True,
        "sourceSetExact": len(source_records) == 10, "testSetExact": len(test_records) == 10,
        "runtimeSetExact": len(runtime_records) == 10, "noAssertProductionAst": True,
        "normalTestsPass": run_payloads["normal"]["exitCode"] == 0,
        "optimizedTestsPass": run_payloads["optimized"]["exitCode"] == 0,
        "pyCompilePass": run_payloads["pyCompile"]["exitCode"] == 0,
        "negativeTestsPass": run_payloads["negative"]["exitCode"] == 0,
        "linuxPublicationIntegrationPass": run_payloads["linuxPublicationIntegration"]["exitCode"] == 0,
        "mutualPinPass": True, "namespaceValid": True,
    }
    need(all(value is True for value in checks.values()), "implementation checks are not all true")
    target = {
        "designReview": design_record, "plan": plan_record, "profile": profile_record,
        "sourceFiles": source_records, "testFiles": test_records, "runtimeFiles": runtime_records,
    }
    auditor_pin = next(record for record in source_records if record["path"] == "scripts/audit_service_v1_b1_r4_implementation.py")
    publication_pin = next(record for record in source_records if record["path"] == "scripts/service_v1_b1_r4_publication.py")
    fingerprint = dependency_fingerprint(target, checks, test_runs, auditor_pin, publication_pin)
    review = {
        "schemaVersion": "feelm-service-v1-b1-r4-implementation-review/1", "status": "PASS",
        "runId": RUN_ID, "profileId": PROFILE_ID, "createdAt": utc_now(),
        "reviewer": {"kind": "INDEPENDENT_IMPLEMENTATION_REVIEWER", "sessionId": reviewer_session,
                     "host": os.uname().nodename if hasattr(os, "uname") else os.environ.get("COMPUTERNAME", "unknown"),
                     "processId": os.getpid()},
        "target": target, "checks": checks, "testRuns": test_runs,
        "decision": {"implementationIntegrity": "PASS", "deliveryBuildEligible": True,
                     "publicPreflightEligible": False, "fitEligible": False, "scoreEligible": False,
                     "evaluationEligible": False, "deploymentAuthorized": False},
        "dependencyFingerprint": fingerprint,
    }
    return review, fingerprint


def publish_review(root: Path, evidence_root: Path, test_evidence: Mapping[str, Path], reviewer_session: str) -> dict[str, Any]:
    publication = load_publication_module(root)
    review, fingerprint = build_review(root, evidence_root, test_evidence, reviewer_session)
    reviewed_publication_pin = next(
        item for item in review["target"]["sourceFiles"]
        if item["path"] == "scripts/service_v1_b1_r4_publication.py"
    )
    need(getattr(publication, "_FEELM_REVIEWED_SOURCE_PIN", None) == reviewed_publication_pin, "loaded publication source/target drift")
    second_review, second_fingerprint = build_review(root, evidence_root, test_evidence, reviewer_session)
    need(second_fingerprint == fingerprint, "implementation dependencies changed before claim acquisition")
    need(
        {key: value for key, value in second_review.items() if key not in {"createdAt", "reviewer"}}
        == {key: value for key, value in review.items() if key not in {"createdAt", "reviewer"}},
        "implementation review changed before claim acquisition",
    )
    final_path = evidence_root / IMPLEMENTATION_REVIEW_NAME
    failure_path = evidence_root / IMPLEMENTATION_FAILURE_NAME
    lease = publication.acquire_publication(
        "REVIEWER", "implementation-review", final_path, failure_path,
        {"children": [DESIGN_REVIEW_NAME], "dependencyFingerprint": fingerprint},
    )
    review["publication"] = lease_publication(lease)
    staging_path = lease.temp_path
    need(not staging_path.exists(), f"staging collision: {staging_path}")
    descriptor = os.open(staging_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0), 0o600)
    try:
        os.write(descriptor, canonical_json(review))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)

    def current_fingerprint() -> str:
        _review, current = build_review(root, evidence_root, test_evidence, reviewer_session, check_namespace=False)
        return current

    published = publication.publish_success(lease, staging_path, fingerprint, current_fingerprint)
    post = current_fingerprint()
    publication.release_verified_claim(lease, published, post)
    return as_pin(published)


def failure_dependency_fingerprint(root: Path, evidence_root: Path, test_evidence: Mapping[str, Path]) -> tuple[list[dict[str, Any]], str]:
    candidates = [
        root / PLAN_RELATIVE, root / PROFILE_RELATIVE, evidence_root / DESIGN_REVIEW_NAME,
        *(root / relative for relative in SOURCE_FILES), *(root / relative for relative in TEST_FILES),
        *(root / relative for relative in RUNTIME_FIXED), *test_evidence.values(),
    ]
    wheelhouse = root / "runtime/service-v1-b1-r4-wheelhouse"
    if wheelhouse.is_dir() and not wheelhouse.is_symlink():
        candidates.extend(sorted(wheelhouse.iterdir()))
    records: list[dict[str, Any]] = []
    for path in candidates:
        try:
            records.append(pin(path, path.as_posix()))
        except (OSError, ImplementationAuditError):
            continue
    records.sort(key=lambda item: str(item["path"]))
    state = {"records": records, "missingCount": len(candidates) - len(records)}
    return records, sha256_bytes(canonical_json(state))


def publish_review_failure(root: Path, evidence_root: Path, test_evidence: Mapping[str, Path], reviewer_session: str, error: BaseException) -> dict[str, Any]:
    publication = load_publication_module(root)
    records, fingerprint = failure_dependency_fingerprint(root, evidence_root, test_evidence)
    final_path = evidence_root / IMPLEMENTATION_REVIEW_NAME
    failure_path = evidence_root / IMPLEMENTATION_FAILURE_NAME
    before = sorted(path.name for path in evidence_root.iterdir())
    need(before == [DESIGN_REVIEW_NAME], f"failure publication namespace drift: {before}")
    lease = publication.acquire_publication(
        "REVIEWER", "implementation-review", final_path, failure_path,
        {"children": before, "dependencyFingerprint": fingerprint},
    )
    failure = {
        "schemaVersion": "feelm-service-v1-b1-r4-review-failure/1", "status": "FAILED",
        "runId": RUN_ID, "profileId": PROFILE_ID, "phase": "implementation-review", "createdAt": utc_now(),
        "auditor": {"kind": "INDEPENDENT_IMPLEMENTATION_REVIEWER", "sessionId": reviewer_session,
                    "host": os.uname().nodename if hasattr(os, "uname") else os.environ.get("COMPUTERNAME", "unknown"),
                    "processId": os.getpid()},
        "target": records,
        "error": {"type": type(error).__name__, "message": str(error),
                  "traceback": "".join(traceback.format_exception(type(error), error, error.__traceback__))},
        "dependencyFingerprintBefore": fingerprint, "dependencyFingerprintAfter": fingerprint,
        "namespaceCensus": {"beforeAcquire": before, "claim": lease.claim_path.name,
                            "success": False, "failure": False},
        "passReviewPublished": False, "deploymentAuthorized": False,
        "publication": lease_publication(lease),
    }

    def rehash() -> str:
        _records, current = failure_dependency_fingerprint(root, evidence_root, test_evidence)
        return current

    published = publication.publish_handled_failure(lease, failure, fingerprint, rehash)
    post = rehash()
    publication.release_verified_claim(lease, published, post)
    return as_pin(published)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    sub = result.add_subparsers(dest="command", required=True)
    record = sub.add_parser("record-test")
    record.add_argument("--kind", required=True, choices=TEST_KINDS)
    record.add_argument("--destination", type=Path, required=True)
    record.add_argument("--cwd", type=Path, required=True)
    record.add_argument("argv", nargs=argparse.REMAINDER)
    audit = sub.add_parser("audit")
    audit.add_argument("--standalone-root", type=Path, required=True)
    audit.add_argument("--evidence-root", type=Path, required=True)
    audit.add_argument("--test-evidence-root", type=Path, required=True)
    audit.add_argument("--reviewer-session", required=True)
    audit.add_argument("--publish", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    if args.command == "record-test":
        argv = list(args.argv)
        if argv and argv[0] == "--":
            argv = argv[1:]
        return record_test_run(args.kind, args.destination, argv, args.cwd)
    test_evidence = {kind: args.test_evidence_root / f"{kind}.json" for kind in TEST_KINDS}
    exit_code = 0
    if args.publish:
        try:
            result = publish_review(args.standalone_root, args.evidence_root, test_evidence, args.reviewer_session)
        except Exception as error:
            claim = args.evidence_root / f".{IMPLEMENTATION_REVIEW_NAME}.claim"
            temp_prefix = f".{IMPLEMENTATION_REVIEW_NAME}.tmp-"
            ambiguous = (
                os.path.lexists(claim)
                or os.path.lexists(args.evidence_root / IMPLEMENTATION_REVIEW_NAME)
                or os.path.lexists(args.evidence_root / IMPLEMENTATION_FAILURE_NAME)
                or any(path.name.startswith(temp_prefix) for path in args.evidence_root.iterdir())
            )
            if ambiguous:
                raise
            result = publish_review_failure(args.standalone_root, args.evidence_root, test_evidence, args.reviewer_session, error)
            exit_code = 1
    else:
        review, fingerprint = build_review(args.standalone_root, args.evidence_root, test_evidence, args.reviewer_session)
        result = {"status": review["status"], "dependencyFingerprint": fingerprint}
    print(json.dumps(result, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
