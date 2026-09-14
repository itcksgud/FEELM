"""Build the immutable service-v1 B1 r4 server receipt.

The receipt is the first artifact produced on the execution host.  It proves
that every delivered byte was read again, records the single permitted Docker
archive import, seals an offline CPython environment, and keeps evaluation
labels behind a bytes-only gate.  Publication is delegated to
``service_v1_b1_r4_publication`` so a final path is never replaced.

The command-running boundary is injectable.  Unit tests use a fake runner;
production calls are made only by :func:`produce_receipt` or the CLI.
"""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import errno
import hashlib
import importlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import traceback
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence
import uuid
import zipfile


RUN_ID = "b1-gbt120-s339-v1-r4-server5c20g-t28800"
PROFILE_ID = "ec2-8vcpu32g-local5-fit20g-t28800-v1"
RECEIPT_NAME = f"{RUN_ID}-server-receipt"
RECEIPT_REVIEW_NAME = f"{RECEIPT_NAME}-result-review.json"
RECEIPT_FAILURE_NAME = f"{RECEIPT_NAME}-failure.json"
DELIVERY_MANIFEST_NAME = f"{RUN_ID}-delivery-manifest.json"
DELIVERY_REVIEW_NAME = f"{DELIVERY_MANIFEST_NAME[:-5]}-result-review.json"
DESIGN_REVIEW_NAME = f"{RUN_ID}-design-result-review.json"
IMPLEMENTATION_REVIEW_NAME = f"{RUN_ID}-implementation-result-review.json"

PROFILE_RELATIVE = Path(
    "docs/recommendation/plans/"
    "service-v1-b1-r4-ec2-8vcpu32g-local5c20g-t28800-profile.json"
)
PRODUCER_RELATIVE = Path("scripts/build_service_v1_b1_r4_server_receipt.py")
PUBLICATION_RELATIVE = Path("scripts/service_v1_b1_r4_publication.py")
HOST_RUNTIME_REQUIREMENTS_RELATIVE = Path("requirements/service-v1-b1-r4-host-runtime.lock")
OUTPUT_RELATIVE = Path("outputs/recommendation-evidence/service-v1-pretraining-20260913")

PROFILE_SHA256 = "56e06e311566c23181b4483200d55049d66b728efcbf12f7a7047c80ce5b7dd5"
EXPECTED_IMAGE_ID = "sha256:de611d62db982bb833b0737a7ddd72a7cd49ab1a7dc9128669cdabae39f46df8"
EXPECTED_IMAGE_UNPACKED_BYTES = 936_463_936
EXPECTED_ARCHIVE_SHA256 = "c5567016731df7a11af6c07cf338f8bf93e992a8904ccebd47cfb3a6b0b0f7be"
WHEELHOUSE_SET_SHA256 = "af87e5e9b660085d45ab5562dc76fd3f0f617d1d069108eb8c3677b66ad0fa5c"
EXPECTED_DOCKER_CONTRACT_BYTES = (
    b'{"imageId":"sha256:de611d62db982bb833b0737a7ddd72a7cd49ab1a7dc9128669cdabae39f46df8",'
    b'"imageUnpackedSizeBytes":936463936,"mode":"ARCHIVE"}\n'
)

PIN_KEYS = frozenset({"path", "bytes", "sha256"})
PUBLICATION_KEYS = frozenset(
    {"schemaVersion", "mode", "role", "finalPath", "failurePath", "claimPath", "tempPath", "token",
     "claimStDev", "claimStIno", "filesystemType", "publisher", "dependencyFingerprintAtAcquire",
     "dependencyFingerprintBeforeRename", "renameNoReplaceProbe", "requiredPostconditions"}
)
OWNING_RECORD_KEYS = frozenset(
    {"recordId", "logicalPath", "sourceRoot", "destinationRelativePath", "bytes", "sha256", "kind"}
)
DESTINATION_RECORD_KEYS = frozenset(
    {"recordId", "logicalPath", "absolutePath", "bytes", "sha256", "stDev", "stIno", "nlink", "kind"}
)
DELIVERY_KEYS = frozenset(
    {
        "schemaVersion", "status", "runId", "profileId", "createdAt", "producer", "sourceRoots",
        "destinationLayout", "implementationReview", "modelInputs", "scoreInputs", "workerRuntime",
        "evaluationInputs", "controlAndImplementation", "ancestorEvidence", "crossGroupReferences",
        "crossGroupReferencesSha256", "recordGroupDigests", "deliverySetSha256", "hostRequirements",
        "authorization", "publication",
    }
)
DELIVERY_REVIEW_KEYS = frozenset(
    {"schemaVersion", "status", "runId", "profileId", "createdAt", "reviewer", "target", "checks",
     "decision", "dependencyFingerprint", "publication"}
)
RESERVATION_KEYS = frozenset(
    {"schemaVersion", "status", "reservationId", "hostIdentity", "phase", "startsAt", "endsAt",
     "dockerRestartScheduled", "hostRebootScheduled", "issuedBy", "createdAt"}
)
HOST_PROBE_KEYS = frozenset(
    {"unameMachine", "logicalCpu", "memTotalBytes", "memAvailableBytes", "dockerServerVersion",
     "cgroupVersion", "cgroupControllers", "cgroupPeakReadable", "scratchFreeBytes", "outputFreeBytes",
     "scratchFreeInodes", "outputFreeInodes", "outputDevice", "scratchDevice", "renameNoReplaceProbe",
     "supervisorKind", "observedAt"}
)
VENV_INVENTORY_KEYS = frozenset(
    {"schemaVersion", "root", "records", "regularFileCount", "symlinkCount", "specialFileCount",
     "hardLinkAliasCount", "recordSetSha256"}
)
VENV_RECORD_KEYS = frozenset({"path", "bytes", "sha256", "mode", "stDev", "stIno", "nlink"})
RUNTIME_LOCK_KEYS = frozenset(
    {"schemaVersion", "createdAt", "bootstrapInterpreter", "absoluteInterpreter", "interpreterBytes",
     "interpreterSha256", "pythonImplementation", "pythonVersion", "environment", "requirementsLock",
     "wheelhouseManifest", "venvInventory", "packages", "imports", "evaluationFixture", "runtimeSetSha256"}
)
PHASE_FAILURE_KEYS = frozenset(
    {"schemaVersion", "status", "runId", "profileId", "phase", "attemptOrdinal", "startedAt", "failedAt",
     "elapsedSeconds", "failureStage", "failureKind", "error", "producer", "profile", "delivery", "receipt",
     "phaseInputLock", "ancestorClosure", "authorizationEvidence", "hostGate", "command", "logs", "resource",
     "container", "timeout", "cleanup", "namespaceCensus", "outputState", "dependencyFingerprintBefore",
     "dependencyFingerprintAfter", "publication", "readyForService", "deploymentAuthorized"}
)
RESERVATION_FILES = {
    "preflightDry": ("preflight-dry", "preflight-dry.json", 16_200),
    "preflightFull": ("preflight-full", "preflight-full.json", 9_000),
    "fit": ("fit", "fit.json", 32_400),
    "score": ("score", "score.json", 9_000),
    "calibrateSelect": ("calibrate-select", "calibrate-select.json", 16_200),
    "confirmation": ("confirmation", "confirmation.json", 16_200),
}
OWNING_GROUPS = (
    "modelInputs", "scoreInputs", "workerRuntime", "evaluationInputs", "controlAndImplementation",
    "ancestorEvidence",
)
HEX64 = re.compile(r"[0-9a-f]{64}")
UUID_TEXT = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}")
READ_CHUNK_BYTES = 1024 * 1024
MAX_JSON_BYTES = 64 * 1024 * 1024
MAX_TEXT_BYTES = 16 * 1024 * 1024


class ReceiptBuildError(RuntimeError):
    """A contract violation that must fail closed."""

    def __init__(self, message: str, evidence: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.evidence = dict(evidence or {})


def require(condition: Any, message: str) -> None:
    if not condition:
        raise ReceiptBuildError(message)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_utc(value: Any, field: str = "timestamp") -> dt.datetime:
    require(isinstance(value, str) and value.endswith("Z"), f"{field} must be RFC3339 UTC")
    try:
        parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ReceiptBuildError(f"{field} must be RFC3339 UTC") from error
    require(parsed.tzinfo is not None, f"{field} must be timezone-aware")
    return parsed.astimezone(dt.timezone.utc)


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        require(key not in value, f"duplicate JSON key: {key}")
        value[key] = child
    return value


def _reject_constant(token: str) -> Any:
    raise ReceiptBuildError(f"non-finite JSON value: {token}")


def load_json_bytes(raw: bytes, source: str) -> dict[str, Any]:
    require(not raw.startswith(b"\xef\xbb\xbf"), f"JSON BOM forbidden: {source}")
    require(raw.endswith(b"\n") and b"\r" not in raw, f"JSON must be UTF-8 LF: {source}")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs,
                           parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReceiptBuildError(f"invalid JSON: {source}: {error}") from error
    require(isinstance(value, dict), f"JSON object required: {source}")
    return value


def _lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _validate_lexical_chain(path: Path) -> os.stat_result:
    lexical = _lexical_absolute(path)
    require(os.path.lexists(lexical), f"missing path: {lexical}")
    info = os.lstat(lexical)
    require(stat.S_ISREG(info.st_mode), f"regular file required: {lexical}")
    require(info.st_nlink == 1, f"hard-link alias forbidden: {lexical}")
    current = lexical
    while current != current.parent:
        current_info = os.lstat(current)
        require(not stat.S_ISLNK(current_info.st_mode), f"symlink path forbidden: {lexical}")
        if hasattr(current, "is_junction"):
            require(not current.is_junction(), f"junction path forbidden: {lexical}")
        current = current.parent
    return info


def safe_regular_stat(path: Path) -> os.stat_result:
    return _validate_lexical_chain(path)


def _open_bound_regular(path: Path) -> tuple[int, Path, os.stat_result, os.stat_result]:
    lexical = _lexical_absolute(path)
    before = _validate_lexical_chain(lexical)
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0) |
             getattr(os, "O_CLOEXEC", 0))
    descriptor = os.open(lexical, flags)
    opened = os.fstat(descriptor)
    try:
        require((opened.st_dev, opened.st_ino, opened.st_size, opened.st_nlink) ==
                (before.st_dev, before.st_ino, before.st_size, before.st_nlink),
                f"file replaced before read: {lexical}")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor, lexical, before, opened


def _finish_bound_read(descriptor: int, lexical: Path, before: os.stat_result,
                       opened_before: os.stat_result,
                       total: int) -> os.stat_result:
    opened_after = os.fstat(descriptor)
    lexical_after = _validate_lexical_chain(lexical)
    descriptor_seal = (opened_before.st_dev, opened_before.st_ino, opened_before.st_size,
                       opened_before.st_mtime_ns, opened_before.st_ctime_ns, opened_before.st_nlink)
    lexical_seal = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                    before.st_ctime_ns, before.st_nlink)
    require((opened_after.st_dev, opened_after.st_ino, opened_after.st_size, opened_after.st_mtime_ns,
             opened_after.st_ctime_ns, opened_after.st_nlink) == descriptor_seal and
            (lexical_after.st_dev, lexical_after.st_ino, lexical_after.st_size, lexical_after.st_mtime_ns,
             lexical_after.st_ctime_ns, lexical_after.st_nlink) == lexical_seal,
            f"file changed while reading: {lexical}")
    require(total == before.st_size, f"short read: {lexical}")
    return before


def stream_regular_sha256(path: Path) -> tuple[int, str, os.stat_result]:
    descriptor, lexical, before, opened_before = _open_bound_regular(path)
    digest = hashlib.sha256()
    total = 0
    try:
        while True:
            chunk = os.read(descriptor, READ_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
        info = _finish_bound_read(descriptor, lexical, before, opened_before, total)
    finally:
        os.close(descriptor)
    return total, digest.hexdigest(), info


def read_regular_bytes(path: Path, *, max_bytes: int = MAX_TEXT_BYTES) -> tuple[bytes, os.stat_result]:
    descriptor, lexical, before, opened_before = _open_bound_regular(path)
    require(before.st_size <= max_bytes, f"bounded read limit exceeded: {lexical}")
    raw = bytearray()
    try:
        while True:
            chunk = os.read(descriptor, min(READ_CHUNK_BYTES, max_bytes + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
            require(len(raw) <= max_bytes, f"bounded read limit exceeded: {lexical}")
        info = _finish_bound_read(descriptor, lexical, before, opened_before, len(raw))
    finally:
        os.close(descriptor)
    return bytes(raw), info


def sha256_file(path: Path) -> str:
    _size, digest, _info = stream_regular_sha256(path)
    return digest


def pin_file(path: Path, pin_path: str | None = None) -> dict[str, Any]:
    size, digest, _info = stream_regular_sha256(path)
    return {"path": pin_path if pin_path is not None else str(path.resolve()),
            "bytes": size, "sha256": digest}


def load_json(path: Path) -> tuple[dict[str, Any], bytes]:
    raw, _info = read_regular_bytes(path, max_bytes=MAX_JSON_BYTES)
    return load_json_bytes(raw, str(path)), raw


def canonical_record_set_sha256(records: Iterable[Mapping[str, Any]], *, path_key: str = "path") -> str:
    rows: list[tuple[str, int, str]] = []
    seen: set[str] = set()
    for record in records:
        path = record.get(path_key)
        size = record.get("bytes")
        digest = record.get("sha256")
        require(isinstance(path, str) and path and "\x00" not in path and path not in seen,
                f"invalid or duplicate record path: {path}")
        require(type(size) is int and size >= 0, f"invalid record bytes: {path}")
        require(isinstance(digest, str) and HEX64.fullmatch(digest) is not None,
                f"invalid record SHA-256: {path}")
        seen.add(path)
        rows.append((path, size, digest))
    encoded = b"".join(
        name.encode("utf-8") + b"\0" + str(size).encode("ascii") + b"\0" + digest.encode("ascii") + b"\n"
        for name, size, digest in sorted(rows, key=lambda item: item[0])
    )
    return sha256_bytes(encoded)


def validate_pin(value: Any, field: str) -> dict[str, Any]:
    require(isinstance(value, Mapping) and set(value) == PIN_KEYS, f"{field} pin shape drift")
    require(isinstance(value["path"], str) and value["path"], f"{field}.path invalid")
    require(type(value["bytes"]) is int and value["bytes"] >= 0, f"{field}.bytes invalid")
    require(isinstance(value["sha256"], str) and HEX64.fullmatch(value["sha256"]) is not None,
            f"{field}.sha256 invalid")
    return dict(value)


def validate_publication(value: Any, role: str, field: str) -> None:
    require(isinstance(value, Mapping) and set(value) == PUBLICATION_KEYS, f"{field} shape drift")
    require(value["schemaVersion"] == "feelm-service-v1-b1-r4-publication-evidence/1" and
            value["mode"] == "LINUX_RENAME_NOREPLACE" and value["role"] == role and
            value["renameNoReplaceProbe"] is True, f"{field} mode/role drift")
    for key in ("finalPath", "failurePath", "claimPath", "tempPath"):
        require(isinstance(value[key], str) and Path(value[key]).is_absolute(), f"{field}.{key} invalid")
    require(isinstance(value["token"], str) and value["token"] and
            all(type(value[key]) is int and value[key] >= 0 for key in ("claimStDev", "claimStIno")) and
            value["filesystemType"] in {"ext2/ext3", "xfs"}, f"{field} owner/filesystem drift")
    validate_pin(value["publisher"], field + ".publisher")
    for key in ("dependencyFingerprintAtAcquire", "dependencyFingerprintBeforeRename"):
        require(isinstance(value[key], str) and HEX64.fullmatch(value[key]) is not None, f"{field}.{key} invalid")
    require(value["dependencyFingerprintAtAcquire"] == value["dependencyFingerprintBeforeRename"],
            f"{field} dependency drift")
    require(value["requiredPostconditions"] == {"publishedBytesRehashRequired": True,
                                                "fileAndParentFsyncRequired": True,
                                                "dependencyFingerprintStableRequired": True,
                                                "claimIdentityMatchRequired": True,
                                                "claimRemovalRequired": True},
            f"{field} postcondition drift")


def validate_expected_pin(path: Path, expected_sha256: str, field: str) -> dict[str, Any]:
    require(isinstance(expected_sha256, str) and HEX64.fullmatch(expected_sha256) is not None,
            f"{field} expected SHA-256 must be lowercase 64-hex")
    pin = pin_file(path)
    require(pin["sha256"] == expected_sha256, f"{field} SHA-256 drift")
    return pin


def _record_id(source_root: str, logical_path: str) -> str:
    return sha256_bytes(source_root.encode("utf-8") + b"\0" + logical_path.encode("utf-8"))


def _validate_owning_record(record: Any, field: str) -> dict[str, Any]:
    require(isinstance(record, Mapping) and set(record) == OWNING_RECORD_KEYS, f"{field} record shape drift")
    value = dict(record)
    for name in ("recordId", "logicalPath", "sourceRoot", "destinationRelativePath", "sha256", "kind"):
        require(isinstance(value[name], str) and value[name], f"{field}.{name} invalid")
    require(value["sourceRoot"] in {"standalone", "team", "wslEvidence", "wslDelivery", "virtual"},
            f"{field}.sourceRoot invalid")
    require(value["kind"] in {"regular-file", "docker-image-contract"}, f"{field}.kind invalid")
    require(type(value["bytes"]) is int and value["bytes"] >= 0, f"{field}.bytes invalid")
    require(HEX64.fullmatch(value["sha256"]) is not None, f"{field}.sha256 invalid")
    require(value["recordId"] == _record_id(value["sourceRoot"], value["logicalPath"]),
            f"{field}.recordId drift")
    for name in ("logicalPath", "destinationRelativePath"):
        logical = PurePosixPath(value[name])
        require(not logical.is_absolute() and ".." not in logical.parts and "." not in logical.parts,
                f"{field}.{name} traversal")
    return value


def delivery_owning_records(delivery: Mapping[str, Any]) -> list[dict[str, Any]]:
    require(set(delivery) == DELIVERY_KEYS, "delivery manifest top-level schema drift")
    require(delivery["schemaVersion"] == "feelm-service-v1-b1-r4-server-delivery/2",
            "delivery manifest schema drift")
    require(delivery["status"] == "DELIVERY_AUDIT_PENDING", "delivery manifest status drift")
    require(delivery["runId"] == RUN_ID and delivery["profileId"] == PROFILE_ID,
            "delivery manifest identity drift")
    validate_publication(delivery["publication"], "PRODUCER", "delivery.publication")
    require(isinstance(delivery["producer"], Mapping) and set(delivery["producer"]) == {"recordId"} and
            isinstance(delivery["producer"]["recordId"], str) and delivery["producer"]["recordId"] and
            isinstance(delivery["implementationReview"], Mapping) and
            set(delivery["implementationReview"]) == {"recordId"} and
            isinstance(delivery["implementationReview"]["recordId"], str) and
            delivery["implementationReview"]["recordId"], "delivery top-level record reference drift")
    records: list[dict[str, Any]] = []
    for group in OWNING_GROUPS:
        raw_group = delivery[group]
        if group == "ancestorEvidence":
            require(isinstance(raw_group, Mapping) and "records" in raw_group,
                    "ancestorEvidence shape drift")
            raw_records = raw_group["records"]
        else:
            raw_records = raw_group
        require(isinstance(raw_records, list), f"delivery group must be an array: {group}")
        for index, record in enumerate(raw_records):
            records.append(_validate_owning_record(record, f"{group}[{index}]"))
    require(records, "delivery owning record set is empty")
    require(len({row["recordId"] for row in records}) == len(records), "delivery recordId overlap")
    require(len({row["logicalPath"] for row in records}) == len(records), "delivery logicalPath overlap")
    require(len({row["destinationRelativePath"] for row in records}) == len(records),
            "delivery destination overlap")
    docker_contracts = [row for row in records if row["kind"] == "docker-image-contract"]
    require(len(docker_contracts) == 1, "virtual Docker contract cardinality drift")
    docker_contract = docker_contracts[0]
    require(docker_contract["sourceRoot"] == "virtual" and
            docker_contract["logicalPath"] == "runtime/docker-image-contract.json" and
            docker_contract["destinationRelativePath"] == "runtime/docker-image-contract.json" and
            docker_contract["bytes"] == len(EXPECTED_DOCKER_CONTRACT_BYTES) and
            docker_contract["sha256"] == sha256_bytes(EXPECTED_DOCKER_CONTRACT_BYTES),
            "virtual Docker contract frozen bytes drift")
    archives = [row for row in records
                if row["logicalPath"] == "runtime/feelm-rec046-spark-local.tar"]
    require(len(archives) == 1 and archives[0]["kind"] == "regular-file",
            "Docker archive owning record cardinality drift")
    actual_set = canonical_record_set_sha256(records, path_key="logicalPath")
    require(actual_set == delivery["deliverySetSha256"], "deliverySetSha256 drift")
    require(isinstance(delivery["crossGroupReferencesSha256"], str) and
            HEX64.fullmatch(delivery["crossGroupReferencesSha256"]) is not None,
            "crossGroupReferencesSha256 invalid")
    return records


def validate_delivery_review(review: Mapping[str, Any], manifest_pin: Mapping[str, Any],
                             delivery: Mapping[str, Any], roots: "ReceiptRoots") -> None:
    require(set(review) == DELIVERY_REVIEW_KEYS, "delivery review top-level schema drift")
    require(review["schemaVersion"] == "feelm-service-v1-b1-r4-server-delivery-review/1",
            "delivery review schema drift")
    require(review["status"] == "PASS" and review["runId"] == RUN_ID and review["profileId"] == PROFILE_ID,
            "delivery review identity/status drift")
    validate_publication(review["publication"], "REVIEWER", "deliveryReview.publication")
    reviewer = review["reviewer"]
    require(isinstance(reviewer, Mapping) and set(reviewer) == {"kind", "sessionId", "host", "processId"} and
            all(isinstance(reviewer[key], str) and reviewer[key] for key in ("kind", "sessionId", "host")) and
            type(reviewer["processId"]) is int and reviewer["processId"] > 0,
            "delivery review reviewer shape drift")
    require(isinstance(review["dependencyFingerprint"], str) and
            HEX64.fullmatch(review["dependencyFingerprint"]) is not None,
            "delivery review dependency fingerprint drift")
    target = review["target"]
    require(isinstance(target, Mapping) and set(target) ==
            {"manifest", "implementationReview", "deliverySetSha256", "crossGroupReferencesSha256"},
            "delivery review target shape drift")
    pinned_manifest = validate_pin(target["manifest"], "delivery review target manifest")
    implementation_pin = validate_pin(target["implementationReview"],
                                      "delivery review target implementationReview")
    require(Path(implementation_pin["path"]).resolve() == roots.output_root / IMPLEMENTATION_REVIEW_NAME,
            "delivery review implementationReview path drift")
    require(pin_file(Path(implementation_pin["path"]), str(Path(implementation_pin["path"]).resolve())) ==
            implementation_pin, "delivery review implementationReview current pin drift")
    require((pinned_manifest["bytes"], pinned_manifest["sha256"]) ==
            (manifest_pin["bytes"], manifest_pin["sha256"]), "delivery review manifest pin drift")
    require(target["deliverySetSha256"] == delivery["deliverySetSha256"] and
            target["crossGroupReferencesSha256"] == delivery["crossGroupReferencesSha256"],
            "delivery review set digest drift")
    decision = review["decision"]
    require(decision == {"deliveryIntegrity": "PASS", "serverTransferEligible": True,
                         "publicPreflightEligible": False, "dockerLoadEligible": True,
                         "runtimeInstallEligible": True, "deploymentAuthorized": False},
            "delivery review decision does not authorize receipt construction")
    checks = review["checks"]
    require(isinstance(checks, Mapping) and set(checks) ==
            {"schemaValid", "implementationReviewPass", "groupUnionDisjoint", "crossReferencesValid",
             "allFilesRehashed", "imageArchiveVerified", "ancestryVerified", "authorizationValid",
             "namespaceValid"} and all(value is True for value in checks.values()),
            "delivery review checks drift")


@dataclass(frozen=True)
class ReceiptRoots:
    standalone: Path
    team: Path
    delivery: Path
    common_parent: Path
    output_root: Path
    scratch_root: Path
    runtime_root: Path

    @classmethod
    def create(cls, standalone: Path, team: Path, delivery: Path, output_root: Path,
               scratch_root: Path) -> "ReceiptRoots":
        values = [Path(value).resolve(strict=True) for value in
                  (standalone, team, delivery, output_root, scratch_root)]
        standalone_real, team_real, delivery_real, output_real, scratch_real = values
        common = standalone_real.parent
        require(team_real.parent == common and delivery_real.parent == common,
                "standalone, team and delivery must be sibling roots")
        require(standalone_real.name == "FEELM-standalone" and team_real.name == "S15P21E106",
                "canonical repository directory names required")
        require(output_real == (standalone_real / OUTPUT_RELATIVE).resolve(), "canonical output root required")
        runtime = (standalone_real / ".runtime/service-v1-b1-r4").resolve()
        return cls(standalone_real, team_real, delivery_real, common, output_real, scratch_real, runtime)

    @property
    def receipt(self) -> Path:
        return self.output_root / RECEIPT_NAME

    @property
    def receipt_failure(self) -> Path:
        return self.output_root / RECEIPT_FAILURE_NAME

    @property
    def actual_roots(self) -> dict[str, str]:
        return {"standalone": str(self.standalone), "team": str(self.team), "delivery": str(self.delivery),
                "commonParent": str(self.common_parent), "outputRoot": str(self.output_root),
                "scratchRoot": str(self.scratch_root), "runtimeRoot": str(self.runtime_root)}


def resolve_destination(record: Mapping[str, Any], roots: ReceiptRoots,
                        layout: Mapping[str, Any]) -> Path:
    relative = PurePosixPath(str(record["destinationRelativePath"]))
    require(relative.parts and not relative.is_absolute() and ".." not in relative.parts,
            "invalid destinationRelativePath")
    root_names = {
        str(layout["standaloneDirectoryName"]): roots.standalone,
        str(layout["teamDirectoryName"]): roots.team,
        str(layout["deliveryDirectoryName"]): roots.delivery,
    }
    if relative.parts[0] in root_names:
        base = root_names[relative.parts[0]]
        remainder = relative.parts[1:]
    else:
        source_root = str(record["sourceRoot"])
        base = roots.standalone if source_root == "standalone" else roots.team if source_root == "team" else roots.delivery
        remainder = relative.parts
    require(remainder, "destination path cannot name a root")
    lexical = _lexical_absolute(base.joinpath(*remainder))
    require(os.path.lexists(lexical), f"missing destination: {lexical}")
    real = lexical.resolve(strict=True)
    require(real == lexical and (real == base or base in real.parents),
            "destination lexical path is not canonically bound to its declared root")
    return lexical


def build_destination_inventory(delivery: Mapping[str, Any], roots: ReceiptRoots) -> dict[str, Any]:
    layout = delivery["destinationLayout"]
    require(isinstance(layout, Mapping) and set(layout) ==
            {"siblingRootsRequired", "standaloneDirectoryName", "teamDirectoryName", "deliveryDirectoryName",
             "preserveRelativePaths"}, "destinationLayout shape drift")
    require(layout == {"siblingRootsRequired": True, "standaloneDirectoryName": "FEELM-standalone",
                       "teamDirectoryName": "S15P21E106", "deliveryDirectoryName": RUN_ID,
                       "preserveRelativePaths": True}, "destinationLayout contract drift")
    require(roots.delivery.name == RUN_ID, "delivery root name drift")
    expected = delivery_owning_records(delivery)
    result: list[dict[str, Any]] = []
    inode_owner: dict[tuple[int, int], str] = {}
    expected_delivery_files: set[Path] = set()
    labels = 0
    for record in expected:
        if record["kind"] == "docker-image-contract":
            relative = PurePosixPath(str(record["destinationRelativePath"]))
            if relative.parts[0] in {roots.standalone.name, roots.team.name, roots.delivery.name}:
                virtual_path = roots.common_parent.joinpath(*relative.parts)
            else:
                virtual_path = roots.delivery.joinpath(*relative.parts)
            result.append({"recordId": record["recordId"], "logicalPath": record["logicalPath"],
                           "absolutePath": str(virtual_path.absolute()), "bytes": record["bytes"],
                           "sha256": record["sha256"], "stDev": 0, "stIno": 0, "nlink": 0,
                           "kind": record["kind"]})
            continue
        path = resolve_destination(record, roots, layout)
        if roots.delivery in path.parents:
            expected_delivery_files.add(path)
        size, digest, info = stream_regular_sha256(path)
        require(size == record["bytes"] and digest == record["sha256"],
                f"destination byte drift: {record['logicalPath']}")
        identity = (info.st_dev, info.st_ino)
        require(identity not in inode_owner, f"duplicate inode destination: {record['logicalPath']}")
        inode_owner[identity] = str(record["logicalPath"])
        result.append({"recordId": record["recordId"], "logicalPath": record["logicalPath"],
                       "absolutePath": str(path), "bytes": size, "sha256": digest,
                       "stDev": info.st_dev, "stIno": info.st_ino, "nlink": info.st_nlink,
                       "kind": record["kind"]})
        if PurePosixPath(str(record["logicalPath"])).name == "labels.parquet":
            labels += 1
    observed_delivery_files: set[Path] = set()
    special_count = 0
    for path in roots.delivery.rglob("*"):
        info = os.lstat(path)
        require(not stat.S_ISLNK(info.st_mode), f"delivery symlink forbidden: {path}")
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode):
            special_count += 1
            continue
        observed_delivery_files.add(path.resolve())
    extra_files = observed_delivery_files - expected_delivery_files
    require(not extra_files, f"extra destination files: {sorted(str(path) for path in extra_files)}")
    require(special_count == 0, "special destination file found")
    require(labels == 1, "delivery must contain exactly one labels.parquet bytes-only record")
    result.sort(key=lambda value: value["logicalPath"])
    actual_set = canonical_record_set_sha256(result, path_key="logicalPath")
    require(actual_set == delivery["deliverySetSha256"], "destination delivery set drift")
    record_set = sha256_bytes(canonical_json(result))
    return {"schemaVersion": "feelm-service-v1-b1-r4-destination-inventory/1", "records": result,
            "expectedDeliverySetSha256": delivery["deliverySetSha256"],
            "actualDeliverySetSha256": actual_set, "recordSetSha256": record_set,
            "missingCount": 0, "extraCount": 0, "specialFileCount": 0, "hardLinkAliasCount": 0}


def validate_reservation(value: Mapping[str, Any], phase: str, required_seconds: int) -> None:
    require(set(value) == RESERVATION_KEYS, f"{phase} reservation shape drift")
    require(value["schemaVersion"] == "feelm-service-v1-b1-r4-maintenance-reservation/2" and
            value["status"] == "ACTIVE" and value["phase"] == phase,
            f"{phase} reservation identity/status drift")
    require(isinstance(value["reservationId"], str) and UUID_TEXT.fullmatch(value["reservationId"]) is not None,
            f"{phase} reservationId invalid")
    for key in ("hostIdentity", "issuedBy"):
        require(isinstance(value[key], str) and value[key].strip(), f"{phase}.{key} invalid")
    for key in ("startsAt", "endsAt", "createdAt"):
        parse_utc(value[key], f"{phase}.{key}")
    starts = parse_utc(value["startsAt"])
    ends = parse_utc(value["endsAt"])
    require((ends - starts).total_seconds() >= required_seconds, f"{phase} reservation duration too short")
    require(value["dockerRestartScheduled"] is False and value["hostRebootScheduled"] is False,
            f"{phase} scheduled maintenance conflict")


def read_reservations(paths: Mapping[str, Path]) -> tuple[dict[str, dict[str, Any]], dict[str, bytes]]:
    require(set(paths) == set(RESERVATION_FILES), "six distinct reservation paths required")
    resolved = [Path(paths[key]).resolve(strict=True) for key in RESERVATION_FILES]
    require(len(set(resolved)) == 6, "maintenance reservation paths must be distinct")
    values: dict[str, dict[str, Any]] = {}
    raws: dict[str, bytes] = {}
    ids: set[str] = set()
    phases: set[str] = set()
    for key, (phase, _name, required_seconds) in RESERVATION_FILES.items():
        value, raw = load_json(Path(paths[key]))
        validate_reservation(value, phase, required_seconds)
        require(value["reservationId"] not in ids, "maintenance reservation IDs must be distinct")
        ids.add(value["reservationId"])
        phases.add(value["phase"])
        values[key] = value
        raws[key] = raw
    require(phases == {item[0] for item in RESERVATION_FILES.values()}, "reservation phase coverage drift")
    return values, raws


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str


CommandRunner = Callable[..., Any]


def run_command(argv: Sequence[str], *, env: Mapping[str, str] | None = None,
                timeout: float = 120.0) -> CommandResult:
    command = [str(part) for part in argv]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="strict",
                               check=False, env=None if env is None else dict(env), timeout=timeout)
    return CommandResult(tuple(command), int(completed.returncode), completed.stdout, completed.stderr)


def normalize_command_result(result: Any, argv: Sequence[str]) -> CommandResult:
    if isinstance(result, CommandResult):
        return result
    exit_code = getattr(result, "returncode", getattr(result, "exit_code", None))
    stdout = getattr(result, "stdout", None)
    stderr = getattr(result, "stderr", None)
    require(type(exit_code) is int and isinstance(stdout, str) and isinstance(stderr, str),
            f"invalid command result: {list(argv)}")
    return CommandResult(tuple(str(value) for value in argv), exit_code, stdout, stderr)


def invoke(runner: CommandRunner, argv: Sequence[str], *, env: Mapping[str, str] | None = None,
           timeout: float = 120.0, check: bool = True) -> CommandResult:
    try:
        raw = runner([str(value) for value in argv], env=env, timeout=timeout)
    except TypeError:
        raw = runner([str(value) for value in argv])
    result = normalize_command_result(raw, argv)
    if check:
        if result.exit_code != 0:
            raise ReceiptBuildError(
                f"command failed: {list(argv)}: {result.stderr}",
                {"argv": list(result.argv), "exitCode": result.exit_code,
                 "stdout": result.stdout, "stderr": result.stderr},
            )
    return result


def _read_meminfo(path: Path = Path("/proc/meminfo")) -> tuple[int, int]:
    values: dict[str, int] = {}
    for line in path.read_text(encoding="ascii").splitlines():
        key, separator, rest = line.partition(":")
        if separator and rest.strip().endswith(" kB"):
            values[key] = int(rest.strip().split()[0]) * 1024
    require("MemTotal" in values and "MemAvailable" in values, "incomplete /proc/meminfo")
    return values["MemTotal"], values["MemAvailable"]


def rename_no_replace(source: Path, target: Path) -> None:
    require(sys.platform.startswith("linux"), "renameat2 probe requires Linux")
    libc = ctypes.CDLL(None, use_errno=True)
    function = getattr(libc, "renameat2", None)
    require(function is not None, "renameat2 unavailable")
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    result = function(-100, os.fsencode(source), -100, os.fsencode(target), 1)
    if result != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(target))


def probe_rename_no_replace(parent: Path) -> bool:
    token = uuid.uuid4().hex
    source = parent / f".{RUN_ID}.receipt-probe-source-{token}"
    target = parent / f".{RUN_ID}.receipt-probe-target-{token}"
    collision = parent / f".{RUN_ID}.receipt-probe-collision-{token}"
    try:
        source.write_bytes(b"source\n")
        rename_no_replace(source, target)
        collision.write_bytes(b"collision\n")
        blocked = False
        try:
            rename_no_replace(collision, target)
        except OSError as error:
            require(error.errno == errno.EEXIST, "renameat2 collision did not return EEXIST")
            blocked = True
        target_raw, _target_info = read_regular_bytes(target, max_bytes=64)
        require(blocked and target_raw == b"source\n", "rename no-replace probe failed")
        return True
    finally:
        for path in (source, target, collision):
            if os.path.lexists(path):
                path.unlink()


def validate_host_probe(probe: Mapping[str, Any]) -> None:
    require(set(probe) == HOST_PROBE_KEYS, "host probe shape drift")
    require(probe["unameMachine"] == "x86_64", "host architecture gate failed")
    integer_minima = {"logicalCpu": 8, "memTotalBytes": 32_000_000_000,
                      "memAvailableBytes": 25_769_803_776, "scratchFreeBytes": 85_899_345_920,
                      "outputFreeBytes": 21_474_836_480, "scratchFreeInodes": 100_000,
                      "outputFreeInodes": 100_000}
    for key, minimum in integer_minima.items():
        require(type(probe[key]) is int and probe[key] >= minimum, f"host probe gate failed: {key}")
    for key in ("outputDevice", "scratchDevice"):
        require(type(probe[key]) is int and probe[key] >= 0, f"host probe field invalid: {key}")
    require(isinstance(probe["dockerServerVersion"], str) and probe["dockerServerVersion"].strip(),
            "Docker server version missing")
    require(probe["cgroupVersion"] == "v2" and isinstance(probe["cgroupControllers"], list) and
            probe["cgroupControllers"] == sorted(probe["cgroupControllers"]) and
            set(probe["cgroupControllers"]) >= {"cpu", "memory", "pids"} and
            probe["cgroupPeakReadable"] is True, "cgroup-v2 gate failed")
    require(probe["renameNoReplaceProbe"] is True, "rename no-replace gate failed")
    require(probe["supervisorKind"] == "systemd", "systemd supervisor required")
    parse_utc(probe["observedAt"], "hostProbe.observedAt")


def collect_host_probe(roots: ReceiptRoots, runner: CommandRunner = run_command) -> dict[str, Any]:
    require(sys.platform.startswith("linux"), "canonical host probe requires Linux")
    uname = invoke(runner, ["uname", "-m"])
    cpu = invoke(runner, ["getconf", "_NPROCESSORS_ONLN"])
    docker = invoke(runner, ["docker", "version", "--format", "{{.Server.Version}}"])
    systemd = invoke(runner, ["systemctl", "--version"])
    memory_total, memory_available = _read_meminfo()
    controllers_path = Path("/sys/fs/cgroup/cgroup.controllers")
    peak_path = Path("/sys/fs/cgroup/memory.peak")
    require(controllers_path.is_file(), "cgroup v2 controllers file missing")
    controllers = sorted(controllers_path.read_text(encoding="ascii").split())
    output = os.statvfs(roots.output_root)
    scratch = os.statvfs(roots.scratch_root)
    probe = {"unameMachine": uname.stdout.strip(), "logicalCpu": int(cpu.stdout.strip()),
             "memTotalBytes": memory_total, "memAvailableBytes": memory_available,
             "dockerServerVersion": docker.stdout.strip(), "cgroupVersion": "v2",
             "cgroupControllers": controllers,
             "cgroupPeakReadable": peak_path.is_file() and os.access(peak_path, os.R_OK),
             "scratchFreeBytes": scratch.f_bavail * scratch.f_frsize,
             "outputFreeBytes": output.f_bavail * output.f_frsize,
             "scratchFreeInodes": scratch.f_favail, "outputFreeInodes": output.f_favail,
             "outputDevice": os.stat(roots.output_root).st_dev,
             "scratchDevice": os.stat(roots.scratch_root).st_dev,
             "renameNoReplaceProbe": probe_rename_no_replace(roots.output_root),
             "supervisorKind": "systemd" if "systemd" in systemd.stdout.lower() else "UNKNOWN",
             "observedAt": utc_now()}
    validate_host_probe(probe)
    return probe


def _parse_json_output(text: str, field: str) -> Any:
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_pairs, parse_constant=_reject_constant)
    except json.JSONDecodeError as error:
        raise ReceiptBuildError(f"invalid {field} JSON: {error}") from error


def collect_image_census(runner: CommandRunner, expected_image_id: str = EXPECTED_IMAGE_ID) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    listing = invoke(runner, ["docker", "image", "ls", "--no-trunc", "--digests", "--format", "json"])
    image_ids: set[str] = set()
    for line in listing.stdout.splitlines():
        if not line.strip():
            continue
        row = _parse_json_output(line, "Docker image ls")
        require(isinstance(row, Mapping), "Docker image ls row must be an object")
        image_id = row.get("ID", row.get("Id", row.get("ImageID")))
        require(isinstance(image_id, str) and image_id.startswith("sha256:"), "Docker image ls ID invalid")
        image_ids.add(image_id)
    inspect_ids = sorted(image_ids | {expected_image_id})
    rows: list[dict[str, Any]] = []
    expected: dict[str, Any] | None = None
    for image_id in inspect_ids:
        result = invoke(runner, ["docker", "image", "inspect", image_id], check=image_id != expected_image_id)
        if result.exit_code != 0:
            require(image_id == expected_image_id, "unexpected Docker inspect failure")
            continue
        payload = _parse_json_output(result.stdout, "Docker image inspect")
        require(isinstance(payload, list) and len(payload) == 1 and isinstance(payload[0], Mapping),
                "Docker image inspect cardinality drift")
        item = dict(payload[0])
        actual_id = item.get("Id")
        require(isinstance(actual_id, str) and actual_id == image_id, "Docker image inspect ID drift")
        repo_tags = sorted(value for value in (item.get("RepoTags") or []) if isinstance(value, str))
        repo_digests = sorted(value for value in (item.get("RepoDigests") or []) if isinstance(value, str))
        size = item.get("Size")
        require(type(size) is int and size >= 0, "Docker image Size invalid")
        rows.append({"imageId": actual_id, "repoTags": repo_tags, "repoDigests": repo_digests,
                     "sizeBytes": size})
        if actual_id == expected_image_id:
            expected = item
    rows.sort(key=lambda value: value["imageId"])
    return rows, {} if expected is None else expected


def build_image_load(archive_record: Mapping[str, Any], runner: CommandRunner = run_command) -> dict[str, Any]:
    require(set(archive_record) == DESTINATION_RECORD_KEYS, "archive destination record shape drift")
    require(archive_record["logicalPath"] == "runtime/feelm-rec046-spark-local.tar" and
            archive_record["kind"] == "regular-file", "Docker archive destination record missing")
    archive_path = Path(str(archive_record["absolutePath"]))
    current = pin_file(archive_path)
    require((current["bytes"], current["sha256"]) == (archive_record["bytes"], archive_record["sha256"]),
            "Docker archive changed before load")
    require(current["sha256"] == EXPECTED_ARCHIVE_SHA256, "Docker archive frozen SHA drift")
    before, _before_image = collect_image_census(runner)
    command = ["docker", "load", "--input", str(archive_path)]
    started = utc_now()
    loaded = invoke(runner, command, timeout=3_600, check=False)
    completed = utc_now()
    if loaded.exit_code != 0:
        raise ReceiptBuildError(
            f"docker load failed: {loaded.stderr}",
            {"argv": command, "exitCode": loaded.exit_code, "stdout": loaded.stdout,
             "stderr": loaded.stderr},
        )
    after, actual = collect_image_census(runner)
    require(actual.get("Id") == EXPECTED_IMAGE_ID, "loaded Docker image ID drift")
    require(actual.get("Size") == EXPECTED_IMAGE_UNPACKED_BYTES, "loaded Docker image size drift")
    archive_pin = {"path": str(archive_path), "bytes": current["bytes"], "sha256": current["sha256"]}
    return {"schemaVersion": "feelm-service-v1-b1-r4-image-load/1", "archive": archive_pin,
            "command": command, "startedAt": started, "completedAt": completed,
            "exitCode": loaded.exit_code, "stdout": loaded.stdout, "stderr": loaded.stderr,
            "beforeImageCensus": before, "afterImageCensus": after, "loadInvocationCount": 1,
            "expectedImageId": EXPECTED_IMAGE_ID, "actualImageId": actual["Id"],
            "expectedUnpackedSizeBytes": EXPECTED_IMAGE_UNPACKED_BYTES,
            "actualUnpackedSizeBytes": actual["Size"], "status": "PASS"}


def clean_runtime_environment(runtime_home: Path) -> dict[str, str]:
    return {"HOME": str(runtime_home), "PATH": "/usr/bin:/bin", "PYTHONNOUSERSITE": "1",
            "PIP_CONFIG_FILE": "/dev/null", "PYTHONDONTWRITEBYTECODE": "1", "LC_ALL": "C.UTF-8",
            "TZ": "UTC"}


def _verify_bootstrap(runner: CommandRunner, environment: Mapping[str, str]) -> tuple[dict[str, Any], Path]:
    script = "import json,platform,sys;print(json.dumps({'executable':sys.executable,'implementation':platform.python_implementation(),'version':platform.python_version()},sort_keys=True,separators=(',',':')))"
    result = invoke(runner, ["python3", "-I", "-c", script], env=environment)
    payload = _parse_json_output(result.stdout, "bootstrap Python")
    require(isinstance(payload, Mapping) and set(payload) == {"executable", "implementation", "version"},
            "bootstrap Python probe shape drift")
    path = Path(str(payload["executable"])).resolve(strict=True)
    info = safe_regular_stat(path)
    require(info.st_mode & 0o111 != 0, "bootstrap Python is not executable")
    require(payload["implementation"] == "CPython" and payload["version"] == "3.12.3",
            "bootstrap interpreter must be CPython 3.12.3")
    pin = pin_file(path, str(path))
    return {"command": "python3", "absolutePath": str(path), "bytes": pin["bytes"],
            "sha256": pin["sha256"], "version": payload["version"]}, path


def _validate_wheelhouse(delivery_root: Path) -> tuple[Path, Path, dict[str, Any]]:
    requirements = delivery_root / "requirements/service-v1-b1-r4-host-runtime.lock"
    manifest_path = delivery_root / "runtime/service-v1-b1-r4-wheelhouse-manifest.json"
    wheelhouse = delivery_root / "runtime/service-v1-b1-r4-wheelhouse"
    manifest, _raw = load_json(manifest_path)
    require(set(manifest) == {"schemaVersion", "pythonVersion", "interpreterTag", "abiTag", "platformTag",
                              "files", "wheelhouseSetSha256"}, "wheelhouse manifest shape drift")
    require(manifest["schemaVersion"] == "feelm-service-v1-b1-r4-wheelhouse/1" and
            manifest["pythonVersion"] == "3.12.3" and manifest["interpreterTag"] == "cp312" and
            manifest["abiTag"] == "cp312" and manifest["platformTag"] == "manylinux_2_17_x86_64",
            "wheelhouse platform contract drift")
    require(isinstance(manifest["files"], list) and len(manifest["files"]) == 7,
            "wheelhouse must contain seven pinned wheels")
    records: list[dict[str, Any]] = []
    available: dict[tuple[str, str], str] = {}
    for index, record in enumerate(manifest["files"]):
        validated = validate_pin(record, f"wheelhouse.files[{index}]")
        relative = PurePosixPath(validated["path"])
        require(not relative.is_absolute() and ".." not in relative.parts, "wheel path traversal")
        physical = delivery_root.joinpath(*relative.parts).resolve(strict=True)
        require(wheelhouse.resolve() in physical.parents, "wheel path escapes wheelhouse")
        current = pin_file(physical, validated["path"])
        require(current == validated, f"wheel bytes drift: {validated['path']}")
        try:
            with zipfile.ZipFile(physical) as archive:
                metadata_names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
                require(len(metadata_names) == 1, f"wheel METADATA count drift: {physical.name}")
                metadata = archive.read(metadata_names[0]).decode("utf-8")
        except (zipfile.BadZipFile, UnicodeDecodeError) as error:
            raise ReceiptBuildError(f"invalid wheel archive: {physical.name}") from error
        names = [line[6:] for line in metadata.splitlines() if line.startswith("Name: ")]
        versions = [line[9:] for line in metadata.splitlines() if line.startswith("Version: ")]
        require(len(names) == 1 and len(versions) == 1, f"wheel name/version metadata drift: {physical.name}")
        package_key = (re.sub(r"[-_.]+", "-", names[0]).lower(), versions[0])
        require(package_key not in available, f"duplicate wheel distribution: {package_key}")
        available[package_key] = validated["sha256"]
        records.append(validated)
    require(canonical_record_set_sha256(records) == WHEELHOUSE_SET_SHA256 and
            manifest["wheelhouseSetSha256"] == WHEELHOUSE_SET_SHA256, "wheelhouse set SHA drift")
    actual_names = sorted(path.resolve() for path in wheelhouse.iterdir())
    require(actual_names == sorted((delivery_root.joinpath(*PurePosixPath(row["path"]).parts)).resolve()
                                   for row in records), "wheelhouse missing or extra file")
    lock_raw, _lock_info = read_regular_bytes(requirements, max_bytes=MAX_TEXT_BYTES)
    require(not lock_raw.startswith(b"\xef\xbb\xbf") and b"\r" not in lock_raw and lock_raw.endswith(b"\n"),
            "requirements lock must be UTF-8 LF")
    locked: dict[tuple[str, str], str] = {}
    pattern = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s]+) --hash=sha256:([0-9a-f]{64})$")
    for line in lock_raw.decode("utf-8").splitlines():
        match = pattern.fullmatch(line)
        require(match is not None, f"unhashed or malformed requirement: {line}")
        package_key = (re.sub(r"[-_.]+", "-", match.group(1)).lower(), match.group(2))
        require(package_key not in locked, f"duplicate requirement: {package_key}")
        locked[package_key] = match.group(3)
    require(locked == available, "requirements and wheelhouse differ")
    for package_key in (("numpy", "1.26.4"), ("pandas", "2.2.3"), ("pyarrow", "19.0.1")):
        require(package_key in locked, f"required runtime package missing: {package_key}")
    return requirements, manifest_path, manifest


def inventory_venv(venv_root: Path, *, remove_generated_lib64: bool = False) -> dict[str, Any]:
    root = venv_root.resolve(strict=True)
    lib64 = root / "lib64"
    if os.path.lexists(lib64):
        info = os.lstat(lib64)
        require(stat.S_ISLNK(info.st_mode) and os.readlink(lib64) == "lib", "unexpected venv lib64 entry")
        require(remove_generated_lib64, "venv lib64 symlink must be removed before sealing")
        lib64.unlink()
    records: list[dict[str, Any]] = []
    identities: set[tuple[int, int]] = set()
    symlinks = 0
    specials = 0
    aliases = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        info = os.lstat(path)
        if stat.S_ISDIR(info.st_mode):
            continue
        if stat.S_ISLNK(info.st_mode):
            symlinks += 1
            continue
        if not stat.S_ISREG(info.st_mode):
            specials += 1
            continue
        if info.st_nlink != 1:
            aliases += 1
        identity = (info.st_dev, info.st_ino)
        if identity in identities:
            aliases += 1
        identities.add(identity)
        size, digest, current = stream_regular_sha256(path)
        records.append({"path": path.relative_to(root).as_posix(), "bytes": size,
                        "sha256": digest, "mode": stat.S_IMODE(current.st_mode),
                        "stDev": current.st_dev, "stIno": current.st_ino, "nlink": current.st_nlink})
    require(symlinks == 0 and specials == 0 and aliases == 0, "venv contains linked, special or aliased entries")
    require(records, "venv inventory is empty")
    digest_records = [{"path": row["path"], "bytes": row["bytes"], "sha256": row["sha256"]}
                      for row in records]
    return {"schemaVersion": "feelm-service-v1-b1-r4-venv-inventory/1", "root": str(root),
            "records": records, "regularFileCount": len(records), "symlinkCount": symlinks,
            "specialFileCount": specials, "hardLinkAliasCount": aliases,
            "recordSetSha256": canonical_record_set_sha256(digest_records)}


RUNTIME_PROBE_SCRIPT = r'''import hashlib,importlib.metadata as md,json,os,platform,sys
import numpy,pandas,pyarrow
source={"a":[1,2,3],"b":[0.5,1.5,2.5]}
frame=pandas.DataFrame(source)
table=pyarrow.Table.from_pandas(frame,preserve_index=False)
result={"sum":float(numpy.asarray(table.column("b")).sum()),"rows":table.num_rows,"columns":table.column_names}
def files(name):
    distribution=md.distribution(name)
    values=[]
    for item in distribution.files or []:
        located=item.locate()
        if os.path.isfile(located): values.append(os.path.realpath(located))
    return sorted(values)
payload={"executable":os.path.realpath(sys.executable),"implementation":platform.python_implementation(),"version":platform.python_version(),"packages":{"numpy":{"version":numpy.__version__,"modulePath":os.path.realpath(numpy.__file__),"distributionFiles":files("numpy")},"pandas":{"version":pandas.__version__,"modulePath":os.path.realpath(pandas.__file__),"distributionFiles":files("pandas")},"pyarrow":{"version":pyarrow.__version__,"modulePath":os.path.realpath(pyarrow.__file__),"distributionFiles":files("pyarrow")}},"fixture":{"inputSha256":hashlib.sha256((json.dumps(source,sort_keys=True,separators=(",",":"))+"\n").encode()).hexdigest(),"outputSha256":hashlib.sha256((json.dumps(result,sort_keys=True,separators=(",",":"))+"\n").encode()).hexdigest()}}
print(json.dumps(payload,sort_keys=True,separators=(",",":")))'''


def _distribution_digest(paths: Sequence[Any], venv_root: Path, package: str) -> str:
    require(isinstance(paths, list) and paths, f"{package} distribution file list missing")
    records: list[dict[str, Any]] = []
    root = venv_root.resolve()
    for value in paths:
        require(isinstance(value, str), f"{package} distribution path invalid")
        physical = Path(value).resolve(strict=True)
        require(root in physical.parents, f"{package} distribution escapes venv")
        records.append(pin_file(physical, physical.relative_to(root).as_posix()))
    return canonical_record_set_sha256(records)


def seal_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        info = os.lstat(path)
        require(not stat.S_ISLNK(info.st_mode), f"symlink appeared before seal: {path}")
        os.chmod(path, stat.S_IMODE(info.st_mode) & ~0o222)
    root_info = os.lstat(root)
    os.chmod(root, stat.S_IMODE(root_info.st_mode) & ~0o222)


def build_host_runtime(roots: ReceiptRoots, runner: CommandRunner = run_command) -> tuple[dict[str, Any], dict[str, Any]]:
    runtime_home = roots.runtime_root / "home"
    venv_root = roots.runtime_root / "venv"
    require(not os.path.lexists(roots.runtime_root),
            "runtime installation root already exists; same-run reinstall is forbidden")
    runtime_home.mkdir(parents=True, exist_ok=False)
    environment = clean_runtime_environment(runtime_home)
    bootstrap, _bootstrap_path = _verify_bootstrap(runner, environment)
    requirements, wheelhouse_manifest, _wheel_manifest = _validate_wheelhouse(roots.delivery)
    invoke(runner, ["python3", "-I", "-m", "venv", "--copies", str(venv_root)], env=environment,
           timeout=600)
    require(venv_root.is_dir(), "venv command did not create VENV_ROOT")
    inventory_venv(venv_root, remove_generated_lib64=True)
    venv_python_lexical = venv_root / "bin/python3"
    venv_python = venv_python_lexical.resolve(strict=True)
    require(venv_python == venv_python_lexical and stat.S_ISREG(os.lstat(venv_python).st_mode),
            "VENV_PYTHON must be an unlinked regular file")
    pip_command = [str(venv_python), "-I", "-m", "pip", "install", "--no-index", "--require-hashes",
                   "--only-binary=:all:", "--find-links",
                   str(roots.delivery / "runtime/service-v1-b1-r4-wheelhouse"), "-r", str(requirements)]
    invoke(runner, pip_command, env=environment, timeout=1_800)
    import_command = [str(venv_python), "-I", "-c", RUNTIME_PROBE_SCRIPT]
    imported = invoke(runner, import_command, env=environment, timeout=300, check=False)
    require(imported.exit_code == 0, f"sealed runtime import/fixture probe failed: {imported.stderr}")
    observed = _parse_json_output(imported.stdout, "sealed runtime probe")
    require(isinstance(observed, Mapping) and set(observed) ==
            {"executable", "implementation", "version", "packages", "fixture"},
            "sealed runtime probe shape drift")
    require(observed["executable"] == str(venv_python) and observed["implementation"] == "CPython" and
            observed["version"] == "3.12.3", "sealed interpreter identity drift")
    expected_versions = {"numpy": "1.26.4", "pandas": "2.2.3", "pyarrow": "19.0.1"}
    require(isinstance(observed["packages"], Mapping) and set(observed["packages"]) == set(expected_versions),
            "sealed package set drift")
    packages: dict[str, dict[str, Any]] = {}
    for name, version in expected_versions.items():
        package = observed["packages"][name]
        require(isinstance(package, Mapping) and set(package) ==
                {"version", "modulePath", "distributionFiles"}, f"{name} probe shape drift")
        require(package["version"] == version, f"{name} version drift")
        module_path = Path(str(package["modulePath"])).resolve(strict=True)
        require(venv_root.resolve() in module_path.parents, f"{name} module escapes sealed venv")
        module_pin = pin_file(module_path, str(module_path))
        packages[name] = {"version": version, "modulePath": str(module_path),
                          "moduleBytes": module_pin["bytes"], "moduleSha256": module_pin["sha256"],
                          "distributionRecordSetSha256":
                              _distribution_digest(package["distributionFiles"], venv_root, name)}
    interpreter_pin = pin_file(venv_python, str(venv_python))
    requirements_pin = pin_file(requirements, str(requirements))
    wheelhouse_pin = pin_file(wheelhouse_manifest, str(wheelhouse_manifest))
    fixture = observed["fixture"]
    require(isinstance(fixture, Mapping) and set(fixture) == {"inputSha256", "outputSha256"} and
            all(isinstance(value, str) and HEX64.fullmatch(value) is not None for value in fixture.values()),
            "evaluation fixture digest drift")
    fixture_input_raw = canonical_json({"a": [1, 2, 3], "b": [0.5, 1.5, 2.5]})
    fixture_output_raw = canonical_json({"sum": 4.5, "rows": 3, "columns": ["a", "b"]})
    require(fixture["inputSha256"] == sha256_bytes(fixture_input_raw) and
            fixture["outputSha256"] == sha256_bytes(fixture_output_raw),
            "evaluation fixture content drift")
    fixture_result = {"inputSha256": fixture["inputSha256"], "outputSha256": fixture["outputSha256"],
                      "status": "PASS"}
    seal_read_only(venv_root)
    inventory = inventory_venv(venv_root)
    inventory_raw = canonical_json(inventory)
    runtime_records = [interpreter_pin, requirements_pin, wheelhouse_pin,
                       {"path": str(roots.receipt / "runtime/venv-file-inventory.json"),
                        "bytes": len(inventory_raw),
                        "sha256": sha256_bytes(inventory_raw)},
                       {"path": "runtime/evaluation-fixture-input", "bytes": len(fixture_input_raw),
                        "sha256": fixture_result["inputSha256"]},
                       {"path": "runtime/evaluation-fixture-output", "bytes": len(fixture_output_raw),
                        "sha256": fixture_result["outputSha256"]}]
    runtime_lock = {"schemaVersion": "feelm-service-v1-b1-r4-host-runtime-lock/1", "createdAt": utc_now(),
                    "bootstrapInterpreter": bootstrap, "absoluteInterpreter": str(venv_python),
                    "interpreterBytes": interpreter_pin["bytes"],
                    "interpreterSha256": interpreter_pin["sha256"], "pythonImplementation": "CPython",
                    "pythonVersion": "3.12.3",
                    "environment": {"pythonPath": "UNSET", "pythonNoUserSite": True,
                                    "isolatedMode": True, "pythonDontWriteBytecode": True,
                                    "locale": "C.UTF-8", "timezone": "UTC"},
                    "requirementsLock": requirements_pin, "wheelhouseManifest": wheelhouse_pin,
                    "venvInventory": {"path": str(roots.receipt / "runtime/venv-file-inventory.json"),
                                      "bytes": len(inventory_raw), "sha256": sha256_bytes(inventory_raw)},
                    "packages": packages,
                    "imports": {"command": import_command, "exitCode": imported.exit_code,
                                "stdoutSha256": sha256_bytes(imported.stdout.encode("utf-8")),
                                "stderrSha256": sha256_bytes(imported.stderr.encode("utf-8")), "status": "PASS"},
                    "evaluationFixture": fixture_result,
                    "runtimeSetSha256": canonical_record_set_sha256(runtime_records)}
    return inventory, runtime_lock


def _write_exclusive(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                         getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0), 0o600)
    try:
        offset = 0
        while offset < len(raw):
            offset += os.write(descriptor, raw[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def remove_owned_staging(staging: Path, output_root: Path,
                         expected_identity: tuple[int, int] | None = None) -> None:
    """Remove only the lease-owned temporary tree before failure publication."""
    require(staging.parent == output_root and staging.name.startswith("." + RECEIPT_NAME + ".tmp-"),
            "refusing to remove noncanonical receipt staging")
    root_info = os.lstat(staging)
    require(stat.S_ISDIR(root_info.st_mode) and not staging.is_symlink(),
            "receipt staging ownership drift")
    if expected_identity is not None:
        require((root_info.st_dev, root_info.st_ino) == expected_identity,
                "receipt staging identity drift")
    for path in staging.rglob("*"):
        info = os.lstat(path)
        require(not stat.S_ISLNK(info.st_mode), f"staging symlink blocks cleanup: {path}")
        require(stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode),
                f"staging special entry blocks cleanup: {path}")
        if stat.S_ISREG(info.st_mode):
            require(info.st_nlink == 1, f"staging hard-link blocks cleanup: {path}")
    shutil.rmtree(staging)
    require(not os.path.lexists(staging), "receipt staging cleanup failed")


def _child_pin(final_path: Path, raw: bytes) -> dict[str, Any]:
    return {"path": str(final_path), "bytes": len(raw), "sha256": sha256_bytes(raw)}


def _publication_module() -> Any:
    module = importlib.import_module("service_v1_b1_r4_publication")
    for name in ("acquire_publication", "publish_success", "publish_handled_failure", "release_verified_claim"):
        require(callable(getattr(module, name, None)), f"shared publication API missing: {name}")
    return module


def _publication_evidence(lease: Any) -> dict[str, Any]:
    value = getattr(lease, "publication_evidence", None)
    value = value() if callable(value) else value
    require(isinstance(value, Mapping), "publication Lease evidence missing")
    return dict(value)


def _published_pin(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    record = getattr(value, "record", None)
    if isinstance(record, Mapping):
        return dict(record)
    result = {key: getattr(value, key, None) for key in ("path", "bytes", "sha256")}
    return validate_pin(result, "published")


def _dependency_records(roots: ReceiptRoots, delivery_manifest: Path, delivery_review: Path,
                        reservations: Mapping[str, Path], inventory: Mapping[str, Any]) -> list[dict[str, Any]]:
    candidates = [delivery_manifest, delivery_review, roots.standalone / PROFILE_RELATIVE,
                  roots.standalone / PRODUCER_RELATIVE, roots.standalone / PUBLICATION_RELATIVE,
                  *([roots.delivery / HOST_RUNTIME_REQUIREMENTS_RELATIVE]
                    if os.path.lexists(roots.delivery / HOST_RUNTIME_REQUIREMENTS_RELATIVE) else []),
                  *([roots.standalone / HOST_RUNTIME_REQUIREMENTS_RELATIVE]
                    if os.path.lexists(roots.standalone / HOST_RUNTIME_REQUIREMENTS_RELATIVE) else []),
                  *(Path(reservations[key]) for key in RESERVATION_FILES),
                  *(Path(record["absolutePath"]) for record in inventory["records"]
                    if record["kind"] == "regular-file")]
    records: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for path in candidates:
        info = safe_regular_stat(path)
        identity = (info.st_dev, info.st_ino)
        if identity in seen:
            continue
        seen.add(identity)
        records.append(pin_file(path, str(path.resolve())))
    return records


def _rehash_dependencies(records: Sequence[Mapping[str, Any]]) -> str:
    current: list[dict[str, Any]] = []
    for expected in records:
        path = Path(str(expected["path"]))
        observed = pin_file(path, str(path.resolve()))
        require(observed == expected, f"receipt dependency drift: {path}")
        current.append(observed)
    return canonical_record_set_sha256(current)


def validate_exact_namespace(output_root: Path, expected_children: Sequence[str]) -> None:
    """Reject every unplanned direct child, including hidden claims and temps."""
    expected = tuple(sorted(expected_children))
    require(tuple(expected_children) == expected and len(set(expected)) == len(expected),
            "expected predecessor namespace must be unique and sorted")
    observed = tuple(sorted(child.name for child in output_root.iterdir()))
    require(observed == expected,
            f"receipt predecessor namespace drift: expected {expected!r}, observed {observed!r}")
    identities: set[tuple[int, int]] = set()
    for name in observed:
        path = output_root / name
        info = os.lstat(path)
        require(not stat.S_ISLNK(info.st_mode), f"namespace symlink forbidden: {name}")
        if hasattr(path, "is_junction"):
            require(not path.is_junction(), f"namespace junction forbidden: {name}")
        expected_directory = name == RECEIPT_NAME
        require(stat.S_ISDIR(info.st_mode) if expected_directory else stat.S_ISREG(info.st_mode),
                f"namespace child type drift: {name}")
        if stat.S_ISREG(info.st_mode):
            require(info.st_nlink == 1, f"namespace hard-link forbidden: {name}")
            identity = (info.st_dev, info.st_ino)
            require(identity not in identities, f"namespace inode alias forbidden: {name}")
            identities.add(identity)


def _snapshot_record(path: Path) -> dict[str, Any]:
    lexical = _lexical_absolute(path)
    if not os.path.lexists(lexical):
        return {"path": str(lexical), "kind": "missing", "bytes": 0, "sha256": None,
                "stDev": 0, "stIno": 0, "nlink": 0, "mode": 0}
    info = os.lstat(lexical)
    common = {"path": str(lexical), "bytes": int(info.st_size), "stDev": int(info.st_dev),
              "stIno": int(info.st_ino), "nlink": int(info.st_nlink),
              "mode": int(stat.S_IMODE(info.st_mode))}
    if stat.S_ISLNK(info.st_mode):
        return {**common, "kind": "symlink", "sha256": sha256_bytes(os.readlink(lexical).encode("utf-8"))}
    if stat.S_ISDIR(info.st_mode):
        return {**common, "kind": "directory", "sha256": None}
    if not stat.S_ISREG(info.st_mode):
        return {**common, "kind": "special", "sha256": None}
    if info.st_nlink != 1:
        return {**common, "kind": "hardlink", "sha256": None}
    try:
        size, digest, current = stream_regular_sha256(lexical)
    except BaseException as error:
        return {**common, "kind": "unreadable-regular", "sha256": None,
                "error": {"type": type(error).__name__, "message": str(error)}}
    return {"path": str(lexical), "kind": "regular-file", "bytes": size, "sha256": digest,
            "stDev": int(current.st_dev), "stIno": int(current.st_ino),
            "nlink": int(current.st_nlink), "mode": int(stat.S_IMODE(current.st_mode))}


def _snapshot_tree(root: Path) -> list[dict[str, Any]]:
    lexical_root = _lexical_absolute(root)
    records = [_snapshot_record(lexical_root)]
    if not os.path.lexists(lexical_root):
        return records
    root_info = os.lstat(lexical_root)
    if not stat.S_ISDIR(root_info.st_mode) or stat.S_ISLNK(root_info.st_mode):
        return records
    pending = [lexical_root]
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as error:
            records.append({"path": str(directory), "kind": "scan-error", "bytes": 0,
                            "sha256": None, "stDev": 0, "stIno": 0, "nlink": 0, "mode": 0,
                            "error": {"type": type(error).__name__, "message": str(error)}})
            continue
        for entry in entries:
            child = Path(entry.path)
            record = _snapshot_record(child)
            records.append(record)
            if record["kind"] == "directory":
                pending.append(child)
    return records


def _best_effort_destination_paths(manifest_path: Path, roots: ReceiptRoots) -> list[Path]:
    try:
        raw, _info = read_regular_bytes(manifest_path, max_bytes=MAX_JSON_BYTES)
        value = load_json_bytes(raw, str(manifest_path))
    except BaseException:
        return []
    found: list[Path] = []
    root_names = {"FEELM-standalone": roots.standalone, "S15P21E106": roots.team,
                  RUN_ID: roots.delivery}

    def visit(child: Any) -> None:
        if isinstance(child, Mapping):
            relative_value = child.get("destinationRelativePath")
            source_root = child.get("sourceRoot")
            if isinstance(relative_value, str) and isinstance(source_root, str):
                relative = PurePosixPath(relative_value)
                if relative.parts and not relative.is_absolute() and ".." not in relative.parts and "." not in relative.parts:
                    if relative.parts[0] in root_names:
                        base, remainder = root_names[relative.parts[0]], relative.parts[1:]
                    else:
                        base = roots.standalone if source_root == "standalone" else roots.team if source_root == "team" else roots.delivery
                        remainder = relative.parts
                    if remainder:
                        found.append(_lexical_absolute(base.joinpath(*remainder)))
            for nested in child.values():
                visit(nested)
        elif isinstance(child, list):
            for nested in child:
                visit(nested)

    visit(value)
    return found


def builder_dependency_snapshot(roots: ReceiptRoots, delivery_manifest: Path, delivery_review: Path,
                                reservations: Mapping[str, Path]) -> tuple[list[dict[str, Any]], str]:
    records_by_path: dict[str, dict[str, Any]] = {}
    explicit = [delivery_manifest, delivery_review,
                 roots.output_root / DESIGN_REVIEW_NAME, roots.output_root / IMPLEMENTATION_REVIEW_NAME,
                 roots.standalone / PROFILE_RELATIVE,
                 roots.standalone / PRODUCER_RELATIVE, roots.standalone / PUBLICATION_RELATIVE,
                 roots.delivery / HOST_RUNTIME_REQUIREMENTS_RELATIVE,
                 roots.standalone / HOST_RUNTIME_REQUIREMENTS_RELATIVE,
                 *(Path(reservations.get(key, roots.scratch_root / f"missing-{key}"))
                  for key in RESERVATION_FILES),
                *_best_effort_destination_paths(delivery_manifest, roots)]
    for record in [*_snapshot_tree(roots.delivery), *(_snapshot_record(path) for path in explicit)]:
        records_by_path[record["path"]] = record
    records = [records_by_path[key] for key in sorted(records_by_path)]
    fingerprint = sha256_bytes(canonical_json(
        {"schemaVersion": "feelm-service-v1-b1-r4-dependency-snapshot/1", "records": records}
    ))
    return records, fingerprint


def namespace_census(output_root: Path) -> dict[str, list[str]]:
    completed: list[str] = []
    failures: list[str] = []
    reviews: list[str] = []
    claims: list[str] = []
    temps: list[str] = []
    scratches: list[str] = []
    for child in output_root.iterdir():
        name = child.name
        if not (name.startswith(RUN_ID + "-") or name.startswith("." + RUN_ID + "-")):
            continue
        if name.endswith(".claim"):
            claims.append(name)
        elif ".tmp-" in name:
            temps.append(name)
        elif "-scratch-" in name:
            scratches.append(name)
        elif name.endswith("-failure.json"):
            failures.append(name)
        else:
            completed.append(name)
            if name.endswith("-result-review.json"):
                reviews.append(name)
    return {"completed": sorted(completed), "failures": sorted(failures), "reviews": sorted(reviews),
            "claims": sorted(claims), "temps": sorted(temps), "scratches": sorted(scratches),
            "containers": [], "processes": []}


def _optional_pin(path: Path) -> dict[str, Any] | None:
    try:
        return pin_file(path, str(_lexical_absolute(path)))
    except BaseException:
        return None


def _captured_authorization(kind: str, path: Path,
                            pin: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    try:
        value, raw = load_json(path)
        decision = value.get("decision")
        current_pin = {"path": str(_lexical_absolute(path)), "bytes": len(raw),
                       "sha256": sha256_bytes(raw)}
        if pin is not None:
            require(dict(pin) == current_pin, f"{kind} authorization pin/content drift")
        if value.get("status") == "PASS" and isinstance(decision, Mapping):
            return {"kind": kind, "pin": current_pin, "decision": dict(decision)}
        return None
    except BaseException:
        return None


def validate_phase_failure(payload: Mapping[str, Any]) -> None:
    require(set(payload) == PHASE_FAILURE_KEYS and
            payload["schemaVersion"] == "feelm-service-v1-b1-r4-phase-failure/2" and
            payload["status"] == "FAILED" and payload["runId"] == RUN_ID and
            payload["profileId"] == PROFILE_ID and payload["phase"] == "receipt-build" and
            payload["attemptOrdinal"] == 1, "receipt failure top-level shape/identity drift")
    require(payload["failureStage"] in {"PRE_CONTAINER", "HOST_GATE", "INPUT_LOCK", "CONTAINER_CREATE",
                                                "WORKER", "TIMEOUT", "OUTPUT_VALIDATION", "PUBLICATION", "CLEANUP"} and
            payload["failureKind"] in {"CONTRACT", "RESOURCE", "TIMEOUT", "OOM", "PROCESS", "IO",
                                      "AUDIT", "CLEANUP"}, "receipt failure enum drift")
    require(isinstance(payload["elapsedSeconds"], (int, float)) and
            not isinstance(payload["elapsedSeconds"], bool) and math.isfinite(float(payload["elapsedSeconds"])) and
            float(payload["elapsedSeconds"]) >= 0, "receipt failure elapsedSeconds invalid")
    parse_utc(payload["startedAt"], "failure.startedAt")
    parse_utc(payload["failedAt"], "failure.failedAt")
    validate_pin(payload["producer"], "failure.producer")
    validate_pin(payload["profile"], "failure.profile")
    require(isinstance(payload["error"], Mapping) and set(payload["error"]) == {"type", "message", "traceback"} and
            all(isinstance(value, str) for value in payload["error"].values()), "receipt failure error shape drift")
    for name in ("delivery", "receipt"):
        value = payload[name]
        require(isinstance(value, Mapping) and set(value) == {"available", "manifest", "review", "runtimeLock"} and
                isinstance(value["available"], bool), f"receipt failure {name} shape drift")
        for key in ("manifest", "review", "runtimeLock"):
            if value[key] is not None:
                validate_pin(value[key], f"failure.{name}.{key}")
        if name == "delivery":
            require(value["available"] == all(value[key] is not None
                                               for key in ("manifest", "review", "runtimeLock")),
                    "receipt failure delivery availability drift")
    phase_input = payload["phaseInputLock"]
    require(isinstance(phase_input, Mapping) and set(phase_input) ==
            {"schemaVersion", "runId", "profileId", "status", "deliveryManifest", "deliveryReview",
             "deliverySetSha256", "crossGroupReferencesSha256", "reservationPaths",
             "dependencySnapshot", "unknownFields"} and
            phase_input["schemaVersion"] == "feelm-service-v1-b1-r4-receipt-input-lock/1" and
            phase_input["runId"] == RUN_ID and phase_input["profileId"] == PROFILE_ID and
            phase_input["status"] in {"INTENDED", "VALIDATED"} and
            isinstance(phase_input["unknownFields"], list) and
            phase_input["unknownFields"] == sorted(set(phase_input["unknownFields"])) and
            all(isinstance(value, str) for value in phase_input["unknownFields"]),
            "receipt failure phaseInputLock shape drift")
    for key in ("deliveryManifest", "deliveryReview"):
        if phase_input[key] is not None:
            validate_pin(phase_input[key], f"failure.phaseInputLock.{key}")
    for key in ("deliverySetSha256", "crossGroupReferencesSha256"):
        require(phase_input[key] is None or
                (isinstance(phase_input[key], str) and HEX64.fullmatch(phase_input[key]) is not None),
                f"receipt failure phaseInputLock.{key} drift")
    require(isinstance(phase_input["reservationPaths"], Mapping) and
            set(phase_input["reservationPaths"]) == set(RESERVATION_FILES) and
            all(isinstance(value, str) and Path(value).is_absolute()
                for value in phase_input["reservationPaths"].values()),
            "receipt failure reservation path lock drift")
    snapshot = phase_input["dependencySnapshot"]
    require(isinstance(snapshot, Mapping) and set(snapshot) == {"schemaVersion", "records"} and
            snapshot["schemaVersion"] == "feelm-service-v1-b1-r4-dependency-snapshot/1" and
            isinstance(snapshot["records"], list), "receipt failure dependency snapshot drift")
    host_gate = payload["hostGate"]
    require(isinstance(payload["ancestorClosure"], Mapping) and
            isinstance(payload["authorizationEvidence"], list) and
            isinstance(host_gate, Mapping) and set(host_gate) == {"status", "probe", "unknownFields"} and
            host_gate["status"] in {"NOT_STARTED", "FAILED", "PASS"} and
            isinstance(host_gate["unknownFields"], list) and
            host_gate["unknownFields"] == sorted(set(host_gate["unknownFields"])) and
            all(isinstance(value, str) for value in host_gate["unknownFields"]) and
            (host_gate["probe"] is None or isinstance(host_gate["probe"], Mapping)),
            "receipt failure evidence object drift")
    require((host_gate["status"] == "NOT_STARTED" and host_gate["probe"] is None and
             host_gate["unknownFields"] == sorted(HOST_PROBE_KEYS)) or
            (host_gate["status"] in {"FAILED", "PASS"} and isinstance(host_gate["probe"], Mapping) and
             host_gate["unknownFields"] == sorted(HOST_PROBE_KEYS - set(host_gate["probe"]))),
            "receipt failure hostGate state drift")
    for index, evidence in enumerate(payload["authorizationEvidence"]):
        require(isinstance(evidence, Mapping) and set(evidence) == {"kind", "pin", "decision"} and
                isinstance(evidence["kind"], str) and isinstance(evidence["decision"], Mapping),
                f"receipt failure authorization evidence shape drift: {index}")
        validate_pin(evidence["pin"], f"failure.authorizationEvidence[{index}].pin")
    command = payload["command"]
    require(isinstance(command, Mapping) and set(command) ==
            {"cwd", "intendedArgv", "actualArgv", "environmentAllowList", "dockerArgv", "sparkSubmitArgv"} and
            Path(command["cwd"]).is_absolute() and all(isinstance(command[key], list) and
            all(isinstance(item, str) for item in command[key]) for key in
            ("intendedArgv", "actualArgv", "environmentAllowList", "dockerArgv", "sparkSubmitArgv")),
            "receipt failure command shape drift")
    nested_shapes = {
        "logs": {"stdout", "stderr", "runnerLog", "stdoutSha256", "stderrSha256", "runnerLogSha256"},
        "resource": {"status", "pollIntervalSeconds", "samples", "peakMemoryBytes", "lastMemoryBytes",
                     "cgroupVersion", "events"},
        "container": {"created", "containerId", "preStopInspect", "finalInspect", "exitCode", "oomKilled"},
        "timeout": {"limitSeconds", "timedOut", "lastSampleBeforeStop", "inspectBeforeStop", "stopRequestedAt"},
        "cleanup": {"monitorStopped", "containerStopped", "containerRemoved", "tempRemoved", "scratchRemoved",
                    "errors", "complete"},
        "outputState": {"finalPublished", "reviewPublished", "modelWritten", "predictionsWritten",
                        "nextPhaseBlocked"},
    }
    for name, keys in nested_shapes.items():
        require(isinstance(payload[name], Mapping) and set(payload[name]) == keys,
                f"receipt failure {name} shape drift")
    require(isinstance(payload["namespaceCensus"], Mapping) and set(payload["namespaceCensus"]) ==
            {"completed", "failures", "reviews", "claims", "temps", "scratches", "containers", "processes"},
            "receipt failure namespace census shape drift")
    require(all(isinstance(payload[key], str) and HEX64.fullmatch(payload[key]) for key in
                ("dependencyFingerprintBefore", "dependencyFingerprintAfter")),
            "receipt failure dependency fingerprint drift")
    validate_publication(payload["publication"], "PRODUCER", "failure.publication")
    require(payload["readyForService"] is False and payload["deploymentAuthorized"] is False,
            "receipt failure authorization drift")
    canonical_json(payload)


def _failure_payload(error: BaseException, lease: Any, roots: ReceiptRoots,
                     producer: Mapping[str, Any], profile_pin: Mapping[str, Any],
                     delivery_manifest_pin: Mapping[str, Any] | None,
                     delivery_review_pin: Mapping[str, Any] | None,
                     delivery_manifest: Mapping[str, Any], delivery_review: Mapping[str, Any],
                     dependency_before: str, started_at: str, stage: str,
                     host_probe: Mapping[str, Any] | None = None,
                     delivery_runtime_lock_pin: Mapping[str, Any] | None = None,
                     receipt_runtime_lock_pin: Mapping[str, Any] | None = None,
                     intended_argv: Sequence[str] = (),
                     reservations: Mapping[str, Path] | None = None,
                     dependency_snapshot: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    failed_at = utc_now()
    elapsed = max(0.0, (parse_utc(failed_at) - parse_utc(started_at)).total_seconds())
    dependency_after = dependency_before
    operation = dict(getattr(error, "evidence", {}) or {})
    actual_argv = list(operation.get("argv", []))
    stdout = str(operation.get("stdout", ""))
    stderr = str(operation.get("stderr", ""))
    failure_kind = ("RESOURCE" if stage == "HOST_GATE" else
                    "PROCESS" if actual_argv else
                    "IO" if isinstance(error, OSError) else "CONTRACT")
    host_unknown = sorted(HOST_PROBE_KEYS - set(host_probe or {}))
    host_gate = ({"status": "FAILED" if stage == "HOST_GATE" else "PASS",
                  "probe": dict(host_probe), "unknownFields": host_unknown}
                 if host_probe is not None else
                 {"status": "NOT_STARTED", "probe": None,
                  "unknownFields": sorted(HOST_PROBE_KEYS)})
    phase_unknown: list[str] = []
    if delivery_manifest_pin is None:
        phase_unknown.append("deliveryManifest")
    if delivery_review_pin is None:
        phase_unknown.append("deliveryReview")
    if delivery_manifest.get("deliverySetSha256") is None:
        phase_unknown.append("deliverySetSha256")
    if delivery_manifest.get("crossGroupReferencesSha256") is None:
        phase_unknown.append("crossGroupReferencesSha256")
    ancestor = delivery_manifest.get("ancestorEvidence")
    if isinstance(ancestor, Mapping):
        ancestor_closure = dict(ancestor)
    else:
        ancestor_closure = {
            "status": "UNAVAILABLE", "records": [], "semanticFacts": {},
            "recordSetSha256": sha256_bytes(b""), "semanticFactsSha256": sha256_bytes(b""),
            "closureSha256": sha256_bytes(b""), "unknownFields": ["ancestorEvidence"],
        }
    payload = {"schemaVersion": "feelm-service-v1-b1-r4-phase-failure/2", "status": "FAILED",
            "runId": RUN_ID, "profileId": PROFILE_ID, "phase": "receipt-build", "attemptOrdinal": 1,
            "startedAt": started_at, "failedAt": failed_at, "elapsedSeconds": elapsed,
            "failureStage": stage, "failureKind": failure_kind,
            "error": {"type": type(error).__name__, "message": str(error),
                      "traceback": "".join(traceback.format_exception(type(error), error, error.__traceback__))},
            "producer": dict(producer), "profile": dict(profile_pin),
            "delivery": {"available": all(value is not None for value in
                                                   (delivery_manifest_pin, delivery_review_pin,
                                                    delivery_runtime_lock_pin)),
                         "manifest": None if delivery_manifest_pin is None else dict(delivery_manifest_pin),
                         "review": None if delivery_review_pin is None else dict(delivery_review_pin),
                         "runtimeLock": None if delivery_runtime_lock_pin is None else dict(delivery_runtime_lock_pin)},
            "receipt": {"available": receipt_runtime_lock_pin is not None, "manifest": None, "review": None,
                        "runtimeLock": None if receipt_runtime_lock_pin is None else dict(receipt_runtime_lock_pin)},
            "phaseInputLock": {"schemaVersion": "feelm-service-v1-b1-r4-receipt-input-lock/1",
                                "runId": RUN_ID, "profileId": PROFILE_ID,
                                "status": "VALIDATED" if not phase_unknown else "INTENDED",
                                "deliveryManifest": None if delivery_manifest_pin is None else dict(delivery_manifest_pin),
                                "deliveryReview": None if delivery_review_pin is None else dict(delivery_review_pin),
                                "deliverySetSha256": delivery_manifest.get("deliverySetSha256"),
                                "crossGroupReferencesSha256":
                                    delivery_manifest.get("crossGroupReferencesSha256"),
                               "reservationPaths": {key: str(_lexical_absolute(Path(path)))
                                                    for key, path in sorted((reservations or {}).items())},
                                "dependencySnapshot": {
                                    "schemaVersion": "feelm-service-v1-b1-r4-dependency-snapshot/1",
                                    "records": [dict(record) for record in dependency_snapshot],
                                }, "unknownFields": sorted(phase_unknown)},
            "ancestorClosure": ancestor_closure,
            "authorizationEvidence": [evidence for evidence in (
                _captured_authorization("DESIGN_REVIEW", roots.output_root / DESIGN_REVIEW_NAME),
                _captured_authorization("IMPLEMENTATION_REVIEW",
                                        roots.output_root / IMPLEMENTATION_REVIEW_NAME),
                _captured_authorization("DELIVERY_REVIEW", roots.output_root / DELIVERY_REVIEW_NAME,
                                        delivery_review_pin),
            ) if evidence is not None],
            "hostGate": host_gate,
            "command": {"cwd": str(roots.standalone), "intendedArgv": list(intended_argv or actual_argv),
                        "actualArgv": actual_argv, "environmentAllowList": [],
                        "dockerArgv": list(intended_argv or actual_argv)
                                      if list(intended_argv or actual_argv)[:2] == ["docker", "load"] else [],
                        "sparkSubmitArgv": []},
            "logs": {"stdout": stdout, "stderr": stderr, "runnerLog": "",
                     "stdoutSha256": sha256_bytes(stdout.encode("utf-8")),
                     "stderrSha256": sha256_bytes(stderr.encode("utf-8")),
                     "runnerLogSha256": sha256_bytes(b"")},
            "resource": {"status": "NOT_STARTED", "pollIntervalSeconds": 0, "samples": [],
                         "peakMemoryBytes": None, "lastMemoryBytes": None, "cgroupVersion": "NOT_STARTED",
                         "events": {}},
            "container": {"created": False, "containerId": None, "preStopInspect": None,
                          "finalInspect": None, "exitCode": None, "oomKilled": None},
            "timeout": {"limitSeconds": 0, "timedOut": False, "lastSampleBeforeStop": None,
                        "inspectBeforeStop": None, "stopRequestedAt": None},
            "cleanup": {"monitorStopped": True, "containerStopped": True, "containerRemoved": True,
                        "tempRemoved": True, "scratchRemoved": True, "errors": [], "complete": True},
            "namespaceCensus": namespace_census(roots.output_root),
            "outputState": {"finalPublished": False, "reviewPublished": False, "modelWritten": False,
                            "predictionsWritten": False, "nextPhaseBlocked": True},
            "dependencyFingerprintBefore": dependency_before,
            "dependencyFingerprintAfter": dependency_after, "publication": _publication_evidence(lease),
            "readyForService": False, "deploymentAuthorized": False}
    validate_phase_failure(payload)
    return payload


def _validate_receipt_inputs(*, roots: ReceiptRoots, delivery_manifest_path: Path,
                             expected_delivery_manifest_sha256: str, delivery_review_path: Path,
                             expected_delivery_review_sha256: str,
                             reservations: Mapping[str, Path]) -> dict[str, Any]:
    delivery_manifest_pin = validate_expected_pin(delivery_manifest_path, expected_delivery_manifest_sha256,
                                                  "delivery manifest")
    delivery_review_pin = validate_expected_pin(delivery_review_path, expected_delivery_review_sha256,
                                                "delivery review")
    require(Path(delivery_manifest_pin["path"]).resolve() == roots.output_root / DELIVERY_MANIFEST_NAME,
            "noncanonical delivery manifest path")
    require(Path(delivery_review_pin["path"]).resolve() == roots.output_root / DELIVERY_REVIEW_NAME,
            "noncanonical delivery review path")
    delivery, _delivery_raw = load_json(delivery_manifest_path)
    review, _review_raw = load_json(delivery_review_path)
    records = delivery_owning_records(delivery)
    validate_delivery_review(review, delivery_manifest_pin, delivery, roots)
    profile_path = roots.standalone / PROFILE_RELATIVE
    profile, _profile_raw = load_json(profile_path)
    profile_pin = pin_file(profile_path, str(profile_path.resolve()))
    require(profile_pin["sha256"] == PROFILE_SHA256, "execution profile frozen SHA drift")
    require(profile.get("schemaVersion") == "feelm-service-v1-b1-execution-profile/2" and
            profile.get("runId") == RUN_ID and profile.get("profileId") == PROFILE_ID,
            "execution profile identity drift")
    _reservation_values, reservation_raws = read_reservations(reservations)
    inventory = build_destination_inventory(delivery, roots)
    producer_pin = pin_file(roots.standalone / PRODUCER_RELATIVE,
                            str((roots.standalone / PRODUCER_RELATIVE).resolve()))
    dependency_records = _dependency_records(roots, delivery_manifest_path, delivery_review_path,
                                             reservations, inventory)
    full_fingerprint = canonical_record_set_sha256(dependency_records)
    require(_rehash_dependencies(dependency_records) == full_fingerprint,
            "receipt dependency acquisition drift")
    delivery_runtime_lock_pin = _optional_pin(roots.delivery / HOST_RUNTIME_REQUIREMENTS_RELATIVE)
    if delivery_runtime_lock_pin is None:
        delivery_runtime_lock_pin = _optional_pin(roots.standalone / HOST_RUNTIME_REQUIREMENTS_RELATIVE)
    return {"deliveryManifestPin": delivery_manifest_pin, "deliveryReviewPin": delivery_review_pin,
            "delivery": delivery, "review": review, "records": records, "profile": profile,
            "profilePin": profile_pin, "reservationRaws": reservation_raws,
            "inventory": inventory, "producerPin": producer_pin,
            "fullDependencyRecords": dependency_records, "fullDependencyFingerprint": full_fingerprint,
            "deliveryRuntimeLockPin": delivery_runtime_lock_pin}


def produce_receipt(*, roots: ReceiptRoots, delivery_manifest_path: Path,
                    expected_delivery_manifest_sha256: str, delivery_review_path: Path,
                    expected_delivery_review_sha256: str, reservations: Mapping[str, Path],
                    runner: CommandRunner = run_command,
                    host_probe_collector: Callable[[ReceiptRoots, CommandRunner], Mapping[str, Any]] = collect_host_probe,
                    runtime_builder: Callable[[ReceiptRoots, CommandRunner], tuple[dict[str, Any], dict[str, Any]]] = build_host_runtime,
                    publication: Any | None = None) -> dict[str, Any]:
    """Construct and publish one receipt.  Existing outputs are never replaced."""
    started_at = utc_now()
    expected_children = tuple(sorted((DESIGN_REVIEW_NAME, IMPLEMENTATION_REVIEW_NAME,
                                      DELIVERY_MANIFEST_NAME, DELIVERY_REVIEW_NAME)))
    _snapshot_records, dependency_fingerprint = builder_dependency_snapshot(
        roots, delivery_manifest_path, delivery_review_path, reservations)
    validate_exact_namespace(roots.output_root, expected_children)
    publication_api = publication if publication is not None else _publication_module()
    lease = publication_api.acquire_publication(
        "PRODUCER", "receipt-build", roots.receipt, roots.receipt_failure,
        {"children": list(expected_children), "dependencyFingerprint": dependency_fingerprint},
    )
    staging = Path(lease.temp_path)
    host_probe: Mapping[str, Any] | None = None
    stage_name = "PRE_CONTAINER"
    staging_owned = False
    staging_identity: tuple[int, int] | None = None
    delivery_manifest_pin: Mapping[str, Any] | None = _optional_pin(delivery_manifest_path)
    delivery_review_pin: Mapping[str, Any] | None = _optional_pin(delivery_review_path)
    delivery: Mapping[str, Any] = {}
    review: Mapping[str, Any] = {}
    profile_pin = _optional_pin(roots.standalone / PROFILE_RELATIVE)
    producer_pin = _optional_pin(roots.standalone / PRODUCER_RELATIVE)
    delivery_runtime_lock_pin = _optional_pin(roots.delivery / HOST_RUNTIME_REQUIREMENTS_RELATIVE)
    if delivery_runtime_lock_pin is None:
        delivery_runtime_lock_pin = _optional_pin(roots.standalone / HOST_RUNTIME_REQUIREMENTS_RELATIVE)
    receipt_runtime_lock_pin: Mapping[str, Any] | None = None
    intended_argv: list[str] = []
    try:
        require(profile_pin is not None and producer_pin is not None,
                "producer/profile must be available before receipt validation")
        validated = _validate_receipt_inputs(
            roots=roots, delivery_manifest_path=delivery_manifest_path,
            expected_delivery_manifest_sha256=expected_delivery_manifest_sha256,
            delivery_review_path=delivery_review_path,
            expected_delivery_review_sha256=expected_delivery_review_sha256,
            reservations=reservations,
        )
        delivery_manifest_pin = validated["deliveryManifestPin"]
        delivery_review_pin = validated["deliveryReviewPin"]
        delivery = validated["delivery"]
        review = validated["review"]
        records = validated["records"]
        profile_pin = validated["profilePin"]
        reservation_raws = validated["reservationRaws"]
        inventory = validated["inventory"]
        producer_pin = validated["producerPin"]
        delivery_runtime_lock_pin = validated["deliveryRuntimeLockPin"]
        require(builder_dependency_snapshot(roots, delivery_manifest_path, delivery_review_path,
                                            reservations)[1] == dependency_fingerprint,
                "receipt dependency snapshot drift before host actions")
        require(staging.parent == roots.output_root and not os.path.lexists(staging), "receipt staging path drift")
        staging.mkdir(mode=0o700)
        staging_owned = True
        staging_info = os.lstat(staging)
        require(stat.S_ISDIR(staging_info.st_mode) and not stat.S_ISLNK(staging_info.st_mode),
                "receipt staging directory creation drift")
        staging_identity = (staging_info.st_dev, staging_info.st_ino)
        stage_name = "HOST_GATE"
        host_probe = dict(host_probe_collector(roots, runner))
        validate_host_probe(host_probe)
        stage_name = "PRE_CONTAINER"
        archive = next((row for row in inventory["records"]
                        if row["logicalPath"] == "runtime/feelm-rec046-spark-local.tar"), None)
        require(archive is not None, "Docker archive destination record missing")
        intended_argv = ["docker", "load", "--input", str(archive["absolutePath"])]
        image_load = build_image_load(archive, runner)
        inventory_after_load = build_destination_inventory(delivery, roots)
        require(inventory_after_load == inventory, "destination changed during Docker load")
        venv_inventory, runtime_lock = runtime_builder(roots, runner)
        runtime_lock_raw = canonical_json(runtime_lock)
        receipt_runtime_lock_pin = _child_pin(
            roots.receipt / "runtime/service-v1-b1-r4-host-runtime-lock.json", runtime_lock_raw)
        inventory_after_runtime = build_destination_inventory(delivery, roots)
        require(inventory_after_runtime == inventory, "destination changed during runtime installation")
        child_raws: dict[Path, bytes] = {
            Path("destination-inventory.json"): canonical_json(inventory),
            Path("host-probe.json"): canonical_json(host_probe),
            Path("image-load.json"): canonical_json(image_load),
            Path("runtime/venv-file-inventory.json"): canonical_json(venv_inventory),
            Path("runtime/service-v1-b1-r4-host-runtime-lock.json"): runtime_lock_raw,
        }
        reservation_pins: dict[str, dict[str, Any]] = {}
        for key, (_phase, filename, _seconds) in RESERVATION_FILES.items():
            relative = Path("maintenance-reservations") / filename
            child_raws[relative] = reservation_raws[key]
            reservation_pins[key] = _child_pin(roots.receipt / relative, reservation_raws[key])
        for relative, raw in child_raws.items():
            _write_exclusive(staging / relative, raw)
        image_pin = _child_pin(roots.receipt / "image-load.json", child_raws[Path("image-load.json")])
        venv_pin = _child_pin(roots.receipt / "runtime/venv-file-inventory.json",
                             child_raws[Path("runtime/venv-file-inventory.json")])
        runtime_pin = _child_pin(roots.receipt / "runtime/service-v1-b1-r4-host-runtime-lock.json",
                                child_raws[Path("runtime/service-v1-b1-r4-host-runtime-lock.json")])
        manifest = {"schemaVersion": "feelm-service-v1-b1-r4-server-receipt/1",
                    "status": "RECEIPT_AUDIT_PENDING", "runId": RUN_ID, "profileId": PROFILE_ID,
                    "createdAt": utc_now(), "producer": producer_pin,
                    "delivery": {"manifest": delivery_manifest_pin, "review": delivery_review_pin,
                                 "deliverySetSha256": delivery["deliverySetSha256"],
                                 "crossGroupReferencesSha256": delivery["crossGroupReferencesSha256"]},
                    "actualRoots": roots.actual_roots, "destinationInventory": inventory,
                    "hostProbe": host_probe, "maintenanceReservations": reservation_pins,
                    "imageLoad": image_pin, "hostRuntime": runtime_pin,
                    "filesystem": {"stageAndFinalSameDevice": os.stat(staging).st_dev == os.stat(roots.output_root).st_dev,
                                   "regularFilesOnly": True, "symlinkCount": 0, "junctionCount": 0,
                                   "fifoCount": 0, "socketCount": 0, "deviceCount": 0,
                                   "hardLinkAliasCount": 0, "duplicateInodeCount": 0},
                    "rehashSummary": {"recordsExpected": len(records), "recordsVerified": len(inventory["records"]),
                                      "labelBytesHashed": True, "labelRowsRead": False,
                                      "evaluationTargetsRead": False,
                                      "dependencyFingerprint": dependency_fingerprint,
                                      "destinationRecordSetSha256": inventory["recordSetSha256"],
                                      "venvRecordSetSha256": venv_inventory["recordSetSha256"]},
                    "authorization": {"state": "RECEIPT_AUDIT_PENDING", "publicPreflightEligible": False,
                                      "fitEligible": False, "scoreEligible": False,
                                      "evaluationEligible": False, "deploymentAuthorized": False},
                    "publication": _publication_evidence(lease)}
        _write_exclusive(staging / "manifest.json", canonical_json(manifest))
    except BaseException as error:
        if staging_owned:
            remove_owned_staging(staging, roots.output_root, staging_identity)
        require(producer_pin is not None and profile_pin is not None,
                "producer/profile unavailable for exact receipt failure")
        failure = _failure_payload(error, lease, roots, producer_pin, profile_pin, delivery_manifest_pin,
                                   delivery_review_pin, delivery, review, dependency_fingerprint, started_at,
                                   stage_name, host_probe,
                                   delivery_runtime_lock_pin=delivery_runtime_lock_pin,
                                   receipt_runtime_lock_pin=receipt_runtime_lock_pin,
                                   intended_argv=intended_argv, reservations=reservations,
                                   dependency_snapshot=_snapshot_records)
        published = publication_api.publish_handled_failure(
            lease, failure, dependency_fingerprint,
            lambda: builder_dependency_snapshot(roots, delivery_manifest_path,
                                                delivery_review_path, reservations)[1]
        )
        post = builder_dependency_snapshot(roots, delivery_manifest_path, delivery_review_path, reservations)[1]
        publication_api.release_verified_claim(lease, published, post)
        validate_exact_namespace(roots.output_root, tuple(sorted((*expected_children, RECEIPT_FAILURE_NAME))))
        raise ReceiptBuildError(f"receipt build failed; immutable failure published: {error}") from error
    try:
        published = publication_api.publish_success(
            lease, staging, dependency_fingerprint,
            lambda: builder_dependency_snapshot(
                roots, delivery_manifest_path, delivery_review_path, reservations)[1]
        )
    except BaseException as error:
        dependency_after = builder_dependency_snapshot(
            roots, delivery_manifest_path, delivery_review_path, reservations)[1]
        unambiguous_pre_rename = (
            dependency_after == dependency_fingerprint and os.path.lexists(staging) and
            not os.path.lexists(roots.receipt) and not os.path.lexists(roots.receipt_failure)
        )
        if not unambiguous_pre_rename:
            raise ReceiptBuildError(
                f"receipt publication state is ambiguous or drifted; claim preserved: {error}"
            ) from error
        remove_owned_staging(staging, roots.output_root, staging_identity)
        require(producer_pin is not None and profile_pin is not None,
                "producer/profile unavailable for exact receipt publication failure")
        failure = _failure_payload(
            error, lease, roots, producer_pin, profile_pin, delivery_manifest_pin,
            delivery_review_pin, delivery, review, dependency_fingerprint, started_at,
            "PUBLICATION", host_probe,
            delivery_runtime_lock_pin=delivery_runtime_lock_pin,
            receipt_runtime_lock_pin=receipt_runtime_lock_pin,
            intended_argv=intended_argv, reservations=reservations,
            dependency_snapshot=_snapshot_records,
        )
        failed_pin = publication_api.publish_handled_failure(
            lease, failure, dependency_fingerprint,
            lambda: builder_dependency_snapshot(
                roots, delivery_manifest_path, delivery_review_path, reservations)[1]
        )
        post = builder_dependency_snapshot(
            roots, delivery_manifest_path, delivery_review_path, reservations)[1]
        publication_api.release_verified_claim(lease, failed_pin, post)
        validate_exact_namespace(roots.output_root,
                                 tuple(sorted((*expected_children, RECEIPT_FAILURE_NAME))))
        raise ReceiptBuildError(
            f"receipt build failed; immutable failure published: {error}"
        ) from error
    post = builder_dependency_snapshot(roots, delivery_manifest_path, delivery_review_path, reservations)[1]
    publication_api.release_verified_claim(lease, published, post)
    validate_exact_namespace(roots.output_root, tuple(sorted((*expected_children, RECEIPT_NAME))))
    return _published_pin(published)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--standalone-root", type=Path, required=True)
    result.add_argument("--team-root", type=Path, required=True)
    result.add_argument("--delivery-root", type=Path, required=True)
    result.add_argument("--output-root", type=Path, required=True)
    result.add_argument("--scratch-root", type=Path, required=True)
    result.add_argument("--delivery-manifest", type=Path, required=True)
    result.add_argument("--expected-delivery-manifest-sha256", required=True)
    result.add_argument("--delivery-review", type=Path, required=True)
    result.add_argument("--expected-delivery-review-sha256", required=True)
    for key, (phase, _filename, _seconds) in RESERVATION_FILES.items():
        result.add_argument("--reservation-" + phase, dest="reservation_" + key, type=Path, required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    roots = ReceiptRoots.create(args.standalone_root, args.team_root, args.delivery_root,
                                args.output_root, args.scratch_root)
    reservations = {key: getattr(args, "reservation_" + key) for key in RESERVATION_FILES}
    result = produce_receipt(
        roots=roots, delivery_manifest_path=args.delivery_manifest,
        expected_delivery_manifest_sha256=args.expected_delivery_manifest_sha256,
        delivery_review_path=args.delivery_review,
        expected_delivery_review_sha256=args.expected_delivery_review_sha256,
        reservations=reservations,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
