"""Build and atomically publish the Service-v1 B1 r4 delivery manifest.

The production contract is intentionally data-only: every delivered byte is an
owning record in exactly one of six groups.  The manifest never treats a
cross-group reference as a second copy of its owner.  Public output is possible
only on the reviewed Linux filesystem through the shared r4 publication module.
"""

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
import stat
import sys
import tarfile
import time
import traceback
from typing import Any, Callable, Mapping, Sequence


RUN_ID = "b1-gbt120-s339-v1-r4-server5c20g-t28800"
PROFILE_ID = "ec2-8vcpu32g-local5-fit20g-t28800-v1"
SCHEMA = "feelm-service-v1-b1-r4-server-delivery/2"
ANCESTOR_SCHEMA = "feelm-service-v1-b1-r4-ancestor-evidence/1"
IMPLEMENTATION_REVIEW_SCHEMA = "feelm-service-v1-b1-r4-implementation-review/1"
DELIVERY_NAME = RUN_ID + "-delivery-manifest.json"
DELIVERY_FAILURE_NAME = RUN_ID + "-delivery-failure.json"
DESIGN_REVIEW_NAME = RUN_ID + "-design-result-review.json"
IMPLEMENTATION_REVIEW_NAME = RUN_ID + "-implementation-result-review.json"
DELIVERY_COMPLETED_CHILDREN = tuple(sorted((DESIGN_REVIEW_NAME, IMPLEMENTATION_REVIEW_NAME)))
PHASE_FAILURE_SCHEMA = "feelm-service-v1-b1-r4-phase-failure/2"

GROUPS = (
    "modelInputs",
    "scoreInputs",
    "workerRuntime",
    "evaluationInputs",
    "controlAndImplementation",
    "ancestorEvidence",
)
SOURCE_ROOTS = frozenset({"standalone", "team", "wslEvidence", "wslDelivery", "virtual"})
KINDS = frozenset({"regular-file", "docker-image-contract"})
LOWER_HEX = frozenset("0123456789abcdef")

IMAGE_ID = "sha256:de611d62db982bb833b0737a7ddd72a7cd49ab1a7dc9128669cdabae39f46df8"
IMAGE_UNPACKED_BYTES = 936_463_936
IMAGE_ARCHIVE_BYTES = 936_486_400
IMAGE_ARCHIVE_SHA256 = "c5567016731df7a11af6c07cf338f8bf93e992a8904ccebd47cfb3a6b0b0f7be"
IMAGE_CONTRACT_BYTES = (
    b'{"imageId":"sha256:de611d62db982bb833b0737a7ddd72a7cd49ab1a7dc9128669cdabae39f46df8",'
    b'"imageUnpackedSizeBytes":936463936,"mode":"ARCHIVE"}\n'
)

TOP_LEVEL_KEYS = frozenset(
    {
        "schemaVersion", "status", "runId", "profileId", "createdAt", "producer",
        "sourceRoots", "destinationLayout", "implementationReview", "modelInputs",
        "scoreInputs", "workerRuntime", "evaluationInputs", "controlAndImplementation",
        "ancestorEvidence", "crossGroupReferences", "crossGroupReferencesSha256",
        "recordGroupDigests", "deliverySetSha256", "hostRequirements", "authorization",
        "publication",
    }
)
RECORD_KEYS = frozenset(
    {
        "recordId", "logicalPath", "sourceRoot", "destinationRelativePath",
        "bytes", "sha256", "kind",
    }
)
IMPLEMENTATION_TOP_KEYS = frozenset(
    {
        "schemaVersion", "status", "runId", "profileId", "createdAt", "reviewer",
        "target", "checks", "testRuns", "decision", "dependencyFingerprint", "publication",
    }
)
IMPLEMENTATION_TARGET_KEYS = frozenset(
    {"designReview", "plan", "profile", "sourceFiles", "testFiles", "runtimeFiles"}
)
IMPLEMENTATION_DECISION = {
    "implementationIntegrity": "PASS",
    "deliveryBuildEligible": True,
    "publicPreflightEligible": False,
    "fitEligible": False,
    "scoreEligible": False,
    "evaluationEligible": False,
    "deploymentAuthorized": False,
}
IMPLEMENTATION_CHECK_KEYS = frozenset(
    {
        "schemaValid", "designReviewPass", "r3PinsVerified", "sourceSetExact",
        "testSetExact", "runtimeSetExact", "noAssertProductionAst", "normalTestsPass",
        "optimizedTestsPass", "pyCompilePass", "negativeTestsPass",
        "linuxPublicationIntegrationPass", "mutualPinPass", "namespaceValid",
    }
)
TEST_RUN_KINDS = (
    "normal", "optimized", "pyCompile", "negative", "linuxPublicationIntegration",
)
TEST_RUN_KEYS = frozenset({"argv", "exitCode", "stdoutSha256", "stderrSha256", "evidence"})
PHASE_FAILURE_KEYS = frozenset(
    {
        "schemaVersion", "status", "runId", "profileId", "phase", "attemptOrdinal",
        "startedAt", "failedAt", "elapsedSeconds", "failureStage", "failureKind",
        "error", "producer", "profile", "delivery", "receipt", "phaseInputLock",
        "ancestorClosure", "authorizationEvidence", "hostGate", "command", "logs",
        "resource", "container", "timeout", "cleanup", "namespaceCensus",
        "outputState", "dependencyFingerprintBefore", "dependencyFingerprintAfter",
        "publication", "readyForService", "deploymentAuthorized",
    }
)
FAILURE_STAGES = frozenset(
    {
        "PRE_CONTAINER", "HOST_GATE", "INPUT_LOCK", "CONTAINER_CREATE", "WORKER",
        "TIMEOUT", "OUTPUT_VALIDATION", "PUBLICATION", "CLEANUP",
    }
)
FAILURE_KINDS = frozenset(
    {"CONTRACT", "RESOURCE", "TIMEOUT", "OOM", "PROCESS", "IO", "AUDIT", "CLEANUP"}
)

SOURCE_FILE_SET = frozenset(
    {
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
    }
)
TEST_FILE_SET = frozenset(
    {
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
    }
)
PLAN_PATH = "docs/recommendation/plans/service-v1-b1-r4-server-fit-recovery.md"
PROFILE_PATH = "docs/recommendation/plans/service-v1-b1-r4-ec2-8vcpu32g-local5c20g-t28800-profile.json"
WHEEL_MANIFEST_PATH = "runtime/service-v1-b1-r4-wheelhouse-manifest.json"
RUNTIME_LOCK_PATH = "requirements/service-v1-b1-r4-host-runtime.lock"
IMAGE_ARCHIVE_PATH = "runtime/feelm-rec046-spark-local.tar"


class DeliveryError(RuntimeError):
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
class SourceSpec:
    group: str
    logical_path: str
    source_root: str
    source_relative_path: str
    destination_relative_path: str
    expected_bytes: int
    expected_sha256: str
    kind: str = "regular-file"
    virtual_bytes: bytes = b""


@dataclass(frozen=True)
class ExactDirectory:
    source_root: str
    relative_path: str
    children: tuple[str, ...]


@dataclass(frozen=True)
class DeliveryContract:
    specs: tuple[SourceSpec, ...]
    exact_directories: tuple[ExactDirectory, ...]
    semantic_facts: Mapping[str, object]
    producer_logical_path: str
    implementation_review_logical_path: str
    wsl_delivery_children: tuple[str, ...] | None = None
    verify_ancestry_semantics: bool = False


@dataclass(frozen=True)
class _Collected:
    record: Mapping[str, object]
    identity: tuple[int, int] | None
    absolute_path: Path | None


def _need(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise DeliveryError(code, message)


def _is_sha(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in LOWER_HEX for character in value)
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _pairs(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DeliveryError("BLOCKED_JSON", f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_numbers(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise DeliveryError("BLOCKED_JSON", "non-finite JSON number")
    if isinstance(value, Mapping):
        for child in value.values():
            _reject_numbers(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_numbers(child)


def _load_json(path: Path) -> Mapping[str, Any]:
    data, _, _ = _read_regular(path)
    _need(data.endswith(b"\n") and b"\r" not in data, "BLOCKED_JSON", f"invalid LF JSON: {path}")
    try:
        payload = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                DeliveryError("BLOCKED_JSON", f"invalid numeric token: {token}")
            ),
        )
    except DeliveryError:
        raise
    except (UnicodeDecodeError, ValueError) as error:
        raise DeliveryError("BLOCKED_JSON", f"invalid JSON {path}: {error}") from error
    _need(isinstance(payload, Mapping), "BLOCKED_JSON", f"JSON is not an object: {path}")
    _reject_numbers(payload)
    return payload


def _json_bytes(value: Any) -> bytes:
    _reject_numbers(value)
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
    except (TypeError, ValueError) as error:
        raise DeliveryError("BLOCKED_JSON", str(error)) from error


def _jcs_bytes(value: Any) -> bytes:
    def validate(node: Any) -> None:
        _need(not isinstance(node, float), "BLOCKED_JCS", "JCS domain forbids floats here")
        _need(node is None or isinstance(node, (str, int, bool, list, tuple, Mapping)),
              "BLOCKED_JCS", f"unsupported JCS value: {type(node).__name__}")
        if isinstance(node, Mapping):
            _need(all(isinstance(key, str) for key in node), "BLOCKED_JCS", "non-string JCS key")
            for child in node.values():
                validate(child)
        elif isinstance(node, (list, tuple)):
            for child in node:
                validate(child)

    validate(value)
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _lexical_root(path: Path, label: str) -> Path:
    raw = os.fspath(path)
    _need(path.is_absolute() and Path(os.path.abspath(raw)) == path,
          "BLOCKED_PATH", f"{label} is not lexical absolute")
    _need(os.path.realpath(path) == str(path), "BLOCKED_PATH", f"{label} contains a symlink")
    metadata = os.lstat(path)
    _need(stat.S_ISDIR(metadata.st_mode), "BLOCKED_PATH", f"{label} is not a directory")
    return path


def _safe_relative(value: str, label: str) -> PurePosixPath:
    path = PurePosixPath(value)
    _need(value != "" and not path.is_absolute() and ".." not in path.parts and "." not in path.parts,
          "BLOCKED_PATH", f"unsafe {label}: {value}")
    _need(path.as_posix() == value, "BLOCKED_PATH", f"noncanonical {label}: {value}")
    return path


def _hash_regular(path: Path) -> tuple[os.stat_result, Path, str]:
    _need(path.is_absolute(), "BLOCKED_PATH", f"source is not absolute: {path}")
    _need(os.path.realpath(path) == str(path), "BLOCKED_PATH", f"source contains a symlink: {path}")
    before = os.lstat(path)
    _need(stat.S_ISREG(before.st_mode), "BLOCKED_SPECIAL_FILE", f"source is not regular: {path}")
    _need(before.st_nlink == 1, "BLOCKED_ALIAS", f"source is hard-linked: {path}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        _need((opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino),
              "BLOCKED_SOURCE_DRIFT", f"source identity changed: {path}")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    _need(
        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        == (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns),
        "BLOCKED_SOURCE_DRIFT",
        f"source changed while hashing: {path}",
    )
    return after, Path(os.path.realpath(path)), digest.hexdigest()


def _read_regular(path: Path, *, maximum_bytes: int = 32 * 1024 * 1024) -> tuple[bytes, os.stat_result, Path]:
    metadata, resolved, expected_digest = _hash_regular(path)
    _need(metadata.st_size <= maximum_bytes, "BLOCKED_INPUT_SIZE", f"file is too large to capture: {path}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        _need((opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
              == (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns),
              "BLOCKED_SOURCE_DRIFT", f"source changed before capture: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    data = b"".join(chunks)
    _need((after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
          == (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)
          and len(data) == metadata.st_size
          and hashlib.sha256(data).hexdigest() == expected_digest,
          "BLOCKED_SOURCE_DRIFT", f"source changed while capturing: {path}")
    return data, metadata, resolved


def _record_id(source_root: str, logical_path: str) -> str:
    return hashlib.sha256(
        source_root.encode("utf-8") + b"\0" + logical_path.encode("utf-8")
    ).hexdigest()


def _group_digest(records: Sequence[Mapping[str, object]]) -> str:
    ordered = sorted(records, key=lambda item: str(item["logicalPath"]).encode("ascii"))
    digest = hashlib.sha256()
    for record in ordered:
        logical = str(record["logicalPath"])
        try:
            logical_bytes = logical.encode("ascii")
        except UnicodeEncodeError as error:
            raise DeliveryError("BLOCKED_DIGEST", f"non-ASCII logical path: {logical}") from error
        digest.update(logical_bytes)
        digest.update(b"\0")
        digest.update(str(record["bytes"]).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(record["sha256"]).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _validate_exact_directory(roots: Roots, specification: ExactDirectory) -> None:
    _need(specification.source_root in roots.mapping,
          "BLOCKED_CONTRACT", f"unknown directory source root: {specification.source_root}")
    relative = _safe_relative(specification.relative_path, "directory relative path")
    root = _lexical_root(roots.mapping[specification.source_root], specification.source_root)
    directory = root.joinpath(*relative.parts)
    _need(os.path.realpath(directory) == str(directory),
          "BLOCKED_PATH", f"directory contains a symlink: {directory}")
    metadata = os.lstat(directory)
    _need(stat.S_ISDIR(metadata.st_mode), "BLOCKED_PATH", f"not a directory: {directory}")
    observed = tuple(sorted(entry.name for entry in os.scandir(directory)))
    _need(observed == specification.children, "BLOCKED_EXTRA_MISSING",
          f"directory inventory mismatch for {directory}: {observed!r}")
    for name in observed:
        child = os.lstat(directory / name)
        _need(stat.S_ISREG(child.st_mode), "BLOCKED_SPECIAL_FILE",
              f"directory child is not regular: {directory / name}")
        _need(child.st_nlink == 1, "BLOCKED_ALIAS", f"hard-linked child: {directory / name}")


def _validate_delivery_root(root: Path, expected: tuple[str, ...] | None) -> None:
    if expected is None:
        return
    observed = tuple(sorted(entry.name for entry in os.scandir(root)))
    _need(observed == tuple(sorted(expected)), "BLOCKED_EXTRA_MISSING",
          f"delivery staging inventory mismatch: {observed!r}")
    for name in observed:
        metadata = os.lstat(root / name)
        _need(stat.S_ISDIR(metadata.st_mode), "BLOCKED_SPECIAL_FILE",
              f"delivery staging child must be a directory: {name}")
        _need(os.path.realpath(root / name) == str(root / name), "BLOCKED_PATH",
              f"delivery staging child is linked: {name}")


def _verify_image_archive(path: Path) -> None:
    before, _, _ = _hash_regular(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags)
    found_identity = False
    names: set[str] = set()
    try:
        opened = os.fstat(descriptor)
        _need((opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
              == (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns),
              "BLOCKED_SOURCE_DRIFT", "Docker archive changed before inspection")
        with os.fdopen(os.dup(descriptor), "rb", closefd=True) as stream:
            with tarfile.open(fileobj=stream, mode="r:*") as archive:
                for member in archive:
                    name = PurePosixPath(member.name).as_posix()
                    _need(name not in names and name not in ("", ".")
                          and not PurePosixPath(name).is_absolute()
                          and ".." not in PurePosixPath(name).parts,
                          "BLOCKED_IMAGE", f"unsafe Docker archive member: {member.name}")
                    names.add(name)
                    _need(member.isdir() or member.isreg(), "BLOCKED_IMAGE",
                          f"special/link Docker archive member: {member.name}")
                    if member.isreg() and PurePosixPath(name).name in {"manifest.json", "index.json"}:
                        _need(member.size <= 8 * 1024 * 1024, "BLOCKED_IMAGE", "Docker index is too large")
                        extracted = archive.extractfile(member)
                        _need(extracted is not None, "BLOCKED_IMAGE", "Docker index cannot be read")
                        raw = extracted.read()
                        _need(len(raw) == member.size, "BLOCKED_IMAGE", "truncated Docker index")
                        try:
                            document = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
                        except (UnicodeDecodeError, ValueError) as error:
                            raise DeliveryError("BLOCKED_IMAGE", f"invalid Docker index JSON: {error}") from error
                        found_identity = found_identity or IMAGE_ID in json.dumps(
                            document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                        )
        after = os.fstat(descriptor)
    except tarfile.TarError as error:
        raise DeliveryError("BLOCKED_IMAGE", f"invalid Docker archive: {error}") from error
    finally:
        os.close(descriptor)
    _need((after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
          == (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns),
          "BLOCKED_SOURCE_DRIFT", "Docker archive changed while inspecting")
    _need(found_identity, "BLOCKED_IMAGE", "Docker archive does not reference the exact image ID")


def _collect(roots: Roots, contract: DeliveryContract) -> dict[str, list[_Collected]]:
    for label, root in roots.mapping.items():
        _lexical_root(root, label)
    for directory in contract.exact_directories:
        _validate_exact_directory(roots, directory)
    _validate_delivery_root(roots.wsl_delivery, contract.wsl_delivery_children)
    grouped: dict[str, list[_Collected]] = {group: [] for group in GROUPS}
    logical_paths: set[str] = set()
    record_ids: set[str] = set()
    destinations: set[str] = set()
    identities: set[tuple[int, int]] = set()
    for specification in contract.specs:
        _need(specification.group in GROUPS, "BLOCKED_GROUP", "unknown owning group")
        _need(specification.source_root in SOURCE_ROOTS, "BLOCKED_CONTRACT", "unknown source root")
        _need(specification.kind in KINDS, "BLOCKED_CONTRACT", "unknown record kind")
        _safe_relative(specification.logical_path, "logical path")
        _safe_relative(specification.destination_relative_path, "destination path")
        expected_destination = _destination(
            specification.source_root,
            specification.source_relative_path if specification.source_root != "virtual"
            else specification.logical_path,
        )
        _need(specification.destination_relative_path == expected_destination,
              "BLOCKED_MOVED", f"destination moved: {specification.logical_path}")
        _need(specification.logical_path not in logical_paths,
              "BLOCKED_GROUP_OVERLAP", f"duplicate logical path: {specification.logical_path}")
        _need(specification.destination_relative_path not in destinations,
              "BLOCKED_GROUP_OVERLAP", "duplicate destination path")
        record_id = _record_id(specification.source_root, specification.logical_path)
        _need(record_id not in record_ids, "BLOCKED_GROUP_OVERLAP", "duplicate record ID")
        logical_paths.add(specification.logical_path)
        destinations.add(specification.destination_relative_path)
        record_ids.add(record_id)
        identity: tuple[int, int] | None = None
        absolute: Path | None = None
        if specification.kind == "docker-image-contract":
            _need(specification.source_root == "virtual" and specification.virtual_bytes == IMAGE_CONTRACT_BYTES,
                  "BLOCKED_IMAGE", "virtual image contract differs from the exact bytes")
            data = specification.virtual_bytes
            size = len(data)
            digest = hashlib.sha256(data).hexdigest()
        else:
            _need(specification.source_root != "virtual", "BLOCKED_CONTRACT", "regular virtual record")
            root = roots.mapping[specification.source_root]
            relative = _safe_relative(specification.source_relative_path, "source relative path")
            absolute = root.joinpath(*relative.parts)
            metadata, absolute, digest = _hash_regular(absolute)
            size = metadata.st_size
            identity = (metadata.st_dev, metadata.st_ino)
            _need(identity not in identities, "BLOCKED_GROUP_OVERLAP",
                  f"physical inode is owned twice: {absolute}")
            identities.add(identity)
            if specification.logical_path == "runtime/feelm-rec046-spark-local.tar":
                _verify_image_archive(absolute)
        _need(size == specification.expected_bytes and digest == specification.expected_sha256,
              "BLOCKED_DIGEST", f"pin mismatch: {specification.logical_path}")
        record = {
            "recordId": record_id,
            "logicalPath": specification.logical_path,
            "sourceRoot": specification.source_root,
            "destinationRelativePath": specification.destination_relative_path,
            "bytes": size,
            "sha256": digest,
            "kind": specification.kind,
        }
        _need(set(record) == RECORD_KEYS, "BLOCKED_SCHEMA", "internal record shape")
        grouped[specification.group].append(_Collected(record, identity, absolute))
    for group in GROUPS:
        grouped[group].sort(key=lambda item: str(item.record["logicalPath"]).encode("ascii"))
        _need(len(grouped[group]) > 0, "BLOCKED_GROUP", f"empty owning group: {group}")
    return grouped


def _records(grouped: Mapping[str, Sequence[_Collected]], group: str) -> list[dict[str, object]]:
    return [dict(item.record) for item in grouped[group]]


def _all_records(grouped: Mapping[str, Sequence[_Collected]]) -> list[dict[str, object]]:
    return [dict(item.record) for group in GROUPS for item in grouped[group]]


def _dependency_fingerprint(grouped: Mapping[str, Sequence[_Collected]]) -> str:
    return _group_digest(_all_records(grouped))


def _json_from_collected(grouped: Mapping[str, Sequence[_Collected]], logical: str) -> Mapping[str, Any]:
    matches = [item for item in grouped["ancestorEvidence"] if item.record["logicalPath"] == logical]
    _need(len(matches) == 1 and matches[0].absolute_path is not None,
          "BLOCKED_ANCESTRY", f"ancestor semantic source missing: {logical}")
    data, _, _ = _read_regular(matches[0].absolute_path)
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs)
    except (UnicodeDecodeError, ValueError) as error:
        raise DeliveryError("BLOCKED_ANCESTRY", f"invalid ancestor JSON {logical}: {error}") from error
    _need(isinstance(value, Mapping), "BLOCKED_ANCESTRY", f"ancestor is not an object: {logical}")
    return value


def _verify_ancestry_semantics(
    grouped: Mapping[str, Sequence[_Collected]], facts: Mapping[str, object]
) -> None:
    preflight = _json_from_collected(grouped, "ancestor/r3/preflight/manifest.json")
    preflight_review = _json_from_collected(grouped, "ancestor/r3/preflight-review.json")
    failure = _json_from_collected(grouped, "ancestor/r3/fit-failure.json")
    failure_review = _json_from_collected(grouped, "ancestor/r3/fit-failure-review.json")
    resource = failure.get("resourceEvidence")
    cleanup = failure.get("containerRun")
    census = failure.get("successPathCensus")
    decision = failure_review.get("decision")
    _need(isinstance(resource, Mapping) and isinstance(cleanup, Mapping)
          and isinstance(census, Mapping) and isinstance(decision, Mapping),
          "BLOCKED_ANCESTRY", "r3 failure evidence shape drift")
    observed = {
        "r2RunId": "b1-gbt120-s339-v1-r2",
        "r3RunId": failure.get("runId"),
        "r3PreflightStatus": preflight.get("status"),
        "r3PreflightReviewStatus": preflight_review.get("status"),
        "r3FitFailureStatus": failure.get("status"),
        "r3TimedOut": resource.get("timedOut"),
        "r3OomKilled": resource.get("oomKilled"),
        "r3CleanupComplete": failure.get("cleanupComplete") is True
            and cleanup.get("cleanupErrors") == [],
        "r3DownstreamBlocked": decision.get("downstream") == "BLOCK"
            and all(isinstance(census.get(name), Mapping)
                    and census[name].get("lexists") is False
                    for name in ("bundle", "modelNative", "review")),
        "r3ServerBProfileId": "ec2-8vcpu32g-local5-fit20g-t14400-v1",
        "r3ServerBStatus": "SUPERSEDED",
    }
    _need(dict(facts) == observed, "BLOCKED_ANCESTRY", "ancestor semantic facts do not match bytes")


def _pin_record(path: Path, logical_path: str) -> dict[str, object]:
    metadata, _, digest = _hash_regular(path)
    return {"path": logical_path, "bytes": metadata.st_size, "sha256": digest}


def _validate_pin(value: object, label: str) -> Mapping[str, object]:
    _need(isinstance(value, Mapping) and set(value) == {"path", "bytes", "sha256"},
          "BLOCKED_GATE", f"invalid pin: {label}")
    _need(isinstance(value["path"], str) and value["path"] != "",
          "BLOCKED_GATE", f"invalid pin path: {label}")
    _need(type(value["bytes"]) is int and int(value["bytes"]) >= 0,
          "BLOCKED_GATE", f"invalid pin bytes: {label}")
    _need(_is_sha(value["sha256"]), "BLOCKED_GATE", f"invalid pin SHA: {label}")
    return value


def _validate_implementation_review(
    path: Path,
    expected_sha256: str,
) -> tuple[Mapping[str, Any], dict[str, object]]:
    pin = _pin_record(path, IMPLEMENTATION_REVIEW_NAME)
    _need(pin["sha256"] == expected_sha256 and _is_sha(expected_sha256),
          "BLOCKED_GATE", "implementation review expected SHA mismatch")
    payload = _load_json(path)
    _need(set(payload) == IMPLEMENTATION_TOP_KEYS, "BLOCKED_GATE", "implementation review shape")
    _need(payload.get("schemaVersion") == IMPLEMENTATION_REVIEW_SCHEMA
          and payload.get("status") == "PASS"
          and payload.get("runId") == RUN_ID
          and payload.get("profileId") == PROFILE_ID,
          "BLOCKED_GATE", "implementation review identity/status mismatch")
    target = payload.get("target")
    _need(isinstance(target, Mapping) and set(target) == IMPLEMENTATION_TARGET_KEYS,
          "BLOCKED_GATE", "implementation review target shape")
    for name in ("designReview", "plan", "profile"):
        _validate_pin(target[name], f"implementation target {name}")
    _need(target["designReview"]["path"] == "evidence/" + DESIGN_REVIEW_NAME,
          "BLOCKED_GATE", "implementation design-review path drift")
    _need(target["plan"]["path"] == PLAN_PATH, "BLOCKED_GATE", "implementation plan path drift")
    _need(target["profile"]["path"] == PROFILE_PATH, "BLOCKED_GATE", "implementation profile path drift")
    for name in ("sourceFiles", "testFiles", "runtimeFiles"):
        values = target[name]
        _need(isinstance(values, list) and len(values) > 0,
              "BLOCKED_GATE", f"implementation target {name}")
        seen: set[str] = set()
        for index, value in enumerate(values):
            current = _validate_pin(value, f"{name}[{index}]")
            _need(str(current["path"]) not in seen, "BLOCKED_GATE", f"duplicate {name} path")
            seen.add(str(current["path"]))
    checks = payload.get("checks")
    _need(isinstance(checks, Mapping) and set(checks) == IMPLEMENTATION_CHECK_KEYS
          and all(value is True for value in checks.values()),
          "BLOCKED_GATE", "implementation review checks are not all true")
    test_runs = payload.get("testRuns")
    _need(isinstance(test_runs, Mapping) and set(test_runs) == set(TEST_RUN_KINDS),
          "BLOCKED_GATE", "implementation testRuns shape/order drift")
    for kind in TEST_RUN_KINDS:
        run = test_runs[kind]
        _need(isinstance(run, Mapping) and set(run) == TEST_RUN_KEYS,
              "BLOCKED_GATE", f"implementation test run shape: {kind}")
        _need(isinstance(run["argv"], list) and run["argv"]
              and all(isinstance(value, str) for value in run["argv"])
              and type(run["exitCode"]) is int and run["exitCode"] == 0
              and _is_sha(run["stdoutSha256"]) and _is_sha(run["stderrSha256"]),
              "BLOCKED_GATE", f"implementation test run failed: {kind}")
        _validate_pin(run["evidence"], f"implementation test evidence: {kind}")
    _need(payload.get("decision") == IMPLEMENTATION_DECISION,
          "BLOCKED_GATE", "implementation review does not authorize delivery build")
    return payload, pin


def _validate_reviewed_implementation_bytes(
    roots: Roots,
    implementation: Mapping[str, Any],
) -> None:
    """Rehash every byte authorized by the implementation review before staging writes."""

    target = implementation["target"]
    for section in ("sourceFiles", "testFiles", "runtimeFiles"):
        for reviewed_pin in target[section]:
            relative = _safe_relative(str(reviewed_pin["path"]), f"{section} path")
            current = _pin_record(
                roots.standalone.joinpath(*relative.parts), str(reviewed_pin["path"])
            )
            _need(current == reviewed_pin, "BLOCKED_GATE", f"reviewed bytes drift: {relative}")
    for name, root, expected_path in (
        ("designReview", roots.wsl_evidence, "evidence/" + DESIGN_REVIEW_NAME),
        ("plan", roots.standalone, PLAN_PATH),
        ("profile", roots.standalone, PROFILE_PATH),
    ):
        reviewed_pin = target[name]
        relative = DESIGN_REVIEW_NAME if name == "designReview" else str(reviewed_pin["path"])
        current = _pin_record(root.joinpath(*_safe_relative(relative, f"{name} path").parts), expected_path)
        _need(current == reviewed_pin, "BLOCKED_GATE", f"reviewed {name} bytes drift")
    for kind in TEST_RUN_KINDS:
        reviewed_pin = implementation["testRuns"][kind]["evidence"]
        expected = roots.wsl_delivery / ".implementation-test-evidence" / f"{kind}.json"
        current = _pin_record(expected, expected.as_posix())
        _need(current == reviewed_pin, "BLOCKED_GATE", f"test evidence bytes drift: {kind}")


def _profile_requirements(profile_path: Path) -> tuple[Mapping[str, Any], dict[str, object]]:
    profile = _load_json(profile_path)
    _need(profile.get("schemaVersion") == "feelm-service-v1-b1-execution-profile/2"
          and profile.get("status") == "DRAFT_REQUIRES_INDEPENDENT_REVIEW"
          and profile.get("runId") == RUN_ID
          and profile.get("profileId") == PROFILE_ID,
          "BLOCKED_PROFILE", "profile identity/status mismatch")
    fields = (
        "serverPreconditions", "hostRuntime", "runnerSupervisor", "evaluationSupervisor",
        "docker", "spark", "timeoutsSeconds", "maintenanceWindowsSeconds",
    )
    _need(all(isinstance(profile.get(field), Mapping) for field in fields),
          "BLOCKED_PROFILE", "profile host requirement missing")
    pin = _pin_record(profile_path, PROFILE_PATH)
    return profile, pin


def _cross_references(grouped: Mapping[str, Sequence[_Collected]]) -> list[dict[str, str]]:
    lookup = {
        (group, str(item.record["logicalPath"])): str(item.record["recordId"])
        for group in GROUPS for item in grouped[group]
    }
    model_owner = lookup.get(("modelInputs", "contract/service-v1.json"))
    score_owner = lookup.get(("scoreInputs", "source/natural-score.parquet"))
    _need(model_owner is not None and score_owner is not None,
          "BLOCKED_CROSS_REFERENCE", "cross-reference owner missing")
    return [
        {
            "referenceId": "evaluation.artifact_manifest",
            "consumerGroup": "evaluationInputs",
            "consumerLogicalName": "evaluation.artifact_manifest",
            "ownerGroup": "modelInputs",
            "ownerRecordId": model_owner,
        },
        {
            "referenceId": "evaluation.score_axis",
            "consumerGroup": "evaluationInputs",
            "consumerLogicalName": "evaluation.score_axis",
            "ownerGroup": "scoreInputs",
            "ownerRecordId": score_owner,
        },
    ]


def compose_manifest(
    roots: Roots,
    contract: DeliveryContract,
    profile_path: Path,
    implementation_review_path: Path,
    expected_implementation_review_sha256: str,
    publication_evidence: Mapping[str, object],
    *,
    created_at: str | None = None,
) -> tuple[dict[str, object], str]:
    implementation, implementation_pin = _validate_implementation_review(
        implementation_review_path, expected_implementation_review_sha256
    )
    profile, profile_pin = _profile_requirements(profile_path)
    grouped = _collect(roots, contract)
    if contract.verify_ancestry_semantics:
        _verify_ancestry_semantics(grouped, contract.semantic_facts)
    control_lookup = {
        str(item.record["logicalPath"]): str(item.record["recordId"])
        for item in grouped["controlAndImplementation"]
    }
    producer_id = control_lookup.get(contract.producer_logical_path)
    implementation_id = control_lookup.get(contract.implementation_review_logical_path)
    _need(producer_id is not None, "BLOCKED_CONTRACT", "producer is not an owning record")
    _need(implementation_id is not None, "BLOCKED_CONTRACT", "implementation review is not owned")
    expected_impl_sha = next(
        str(item.record["sha256"])
        for item in grouped["controlAndImplementation"]
        if item.record["recordId"] == implementation_id
    )
    _need(expected_impl_sha == implementation_pin["sha256"],
          "BLOCKED_GATE", "implementation review owning pin mismatch")
    target = implementation["target"]
    _need(profile_pin == target["profile"], "BLOCKED_PROFILE", "profile differs from implementation review")

    group_records = {group: _records(grouped, group) for group in GROUPS}
    group_digests = {group: _group_digest(group_records[group]) for group in GROUPS}
    facts = dict(contract.semantic_facts)
    fact_keys = {
        "r2RunId", "r3RunId", "r3PreflightStatus", "r3PreflightReviewStatus",
        "r3FitFailureStatus", "r3TimedOut", "r3OomKilled", "r3CleanupComplete",
        "r3DownstreamBlocked", "r3ServerBProfileId", "r3ServerBStatus",
    }
    _need(set(facts) == fact_keys, "BLOCKED_ANCESTRY", "ancestor semantic fact shape")
    facts_sha = hashlib.sha256(_jcs_bytes(facts)).hexdigest()
    ancestor_digest = group_digests["ancestorEvidence"]
    closure_sha = hashlib.sha256(
        ancestor_digest.encode("ascii") + b"\0" + facts_sha.encode("ascii") + b"\n"
    ).hexdigest()
    ancestor = {
        "schemaVersion": ANCESTOR_SCHEMA,
        "records": group_records["ancestorEvidence"],
        "semanticFacts": facts,
        "recordSetSha256": ancestor_digest,
        "semanticFactsSha256": facts_sha,
        "closureSha256": closure_sha,
    }
    references = _cross_references(grouped)
    references_sha = hashlib.sha256(_jcs_bytes(references)).hexdigest()
    delivery_sha = _group_digest(_all_records(grouped))
    host_requirements = {
        "profile": profile_pin,
        "serverPreconditions": profile["serverPreconditions"],
        "hostRuntime": profile["hostRuntime"],
        "runnerSupervisor": profile["runnerSupervisor"],
        "evaluationSupervisor": profile["evaluationSupervisor"],
        "docker": profile["docker"],
        "spark": profile["spark"],
        "timeoutsSeconds": profile["timeoutsSeconds"],
        "maintenanceWindowsSeconds": profile["maintenanceWindowsSeconds"],
    }
    payload: dict[str, object] = {
        "schemaVersion": SCHEMA,
        "status": "DELIVERY_AUDIT_PENDING",
        "runId": RUN_ID,
        "profileId": PROFILE_ID,
        "createdAt": created_at or _utc_now(),
        "producer": {"recordId": producer_id},
        "sourceRoots": {
            "standalone": str(roots.standalone),
            "team": str(roots.team),
            "wslEvidence": str(roots.wsl_evidence),
            "wslDelivery": str(roots.wsl_delivery),
        },
        "destinationLayout": {
            "siblingRootsRequired": True,
            "standaloneDirectoryName": "FEELM-standalone",
            "teamDirectoryName": "S15P21E106",
            "deliveryDirectoryName": RUN_ID,
            "preserveRelativePaths": True,
        },
        "implementationReview": {"recordId": implementation_id},
        "modelInputs": group_records["modelInputs"],
        "scoreInputs": group_records["scoreInputs"],
        "workerRuntime": group_records["workerRuntime"],
        "evaluationInputs": group_records["evaluationInputs"],
        "controlAndImplementation": group_records["controlAndImplementation"],
        "ancestorEvidence": ancestor,
        "crossGroupReferences": references,
        "crossGroupReferencesSha256": references_sha,
        "recordGroupDigests": group_digests,
        "deliverySetSha256": delivery_sha,
        "hostRequirements": host_requirements,
        "authorization": {
            "state": "DELIVERY_AUDIT_PENDING",
            "serverTransferEligible": False,
            "publicPreflightEligible": False,
            "fitEligible": False,
            "scoreEligible": False,
            "evaluationEligible": False,
            "deploymentAuthorized": False,
        },
        "publication": dict(publication_evidence),
    }
    _need(set(payload) == TOP_LEVEL_KEYS, "BLOCKED_SCHEMA", "internal delivery shape")
    return payload, delivery_sha


def build_and_publish(
    roots: Roots,
    contract: DeliveryContract,
    profile_path: Path,
    implementation_review_path: Path,
    expected_implementation_review_sha256: str,
    manifest_path: Path,
    completed_children: Sequence[str],
):
    started_at = _utc_now()
    started_monotonic = time.monotonic()
    intended_argv = tuple(sys.argv)
    _need(manifest_path == roots.wsl_evidence / DELIVERY_NAME,
          "BLOCKED_PATH", "delivery manifest must use the canonical evidence path")
    _need(implementation_review_path == roots.wsl_evidence / IMPLEMENTATION_REVIEW_NAME,
          "BLOCKED_PATH", "implementation review must use the canonical evidence path")
    _need(profile_path == roots.standalone / PROFILE_PATH,
          "BLOCKED_PATH", "profile must use the canonical standalone path")
    _need(
        tuple(sorted(completed_children)) == DELIVERY_COMPLETED_CHILDREN
        and len(completed_children) == len(DELIVERY_COMPLETED_CHILDREN),
        "BLOCKED_NAMESPACE",
        "delivery completed-child input differs from the frozen predecessor set",
    )
    publication = _load_publication_module()
    try:
        _preflight_payload, dependency_fingerprint = compose_manifest(
            roots,
            contract,
            profile_path,
            implementation_review_path,
            expected_implementation_review_sha256,
            {},
            created_at="1970-01-01T00:00:00.000000Z",
        )
    except Exception as error:
        pin, _failure = _publish_delivery_failure(
            roots, contract, profile_path, implementation_review_path,
            expected_implementation_review_sha256, manifest_path, error,
            "PRE_CONTAINER", "AUDIT" if isinstance(error, DeliveryError)
            and error.code == "BLOCKED_GATE" else "CONTRACT",
            started_at, started_monotonic, intended_argv,
        )
        _raise_published_failure(error, pin)

    def rehash() -> str:
        return _dependency_fingerprint(_collect(roots, contract))

    lease = publication.acquire_publication(
        "PRODUCER",
        "delivery-build",
        manifest_path,
        manifest_path.with_name(DELIVERY_FAILURE_NAME),
        publication.ExpectedNamespace(DELIVERY_COMPLETED_CHILDREN, dependency_fingerprint),
    )
    try:
        payload, delivery_sha = compose_manifest(
            roots,
            contract,
            profile_path,
            implementation_review_path,
            expected_implementation_review_sha256,
            lease.publication_evidence,
        )
        _need(delivery_sha == dependency_fingerprint, "BLOCKED_SOURCE_DRIFT", "composition drift")
        descriptor = os.open(
            lease.temp_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        try:
            data = _json_bytes(payload)
            offset = 0
            while offset < len(data):
                written = os.write(descriptor, data[offset:])
                _need(written > 0, "BLOCKED_IO", "short manifest write")
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        pin = publication.publish_success(
            lease, lease.temp_path, dependency_fingerprint, rehash
        )
        post_fingerprint = rehash()
        publication.release_verified_claim(lease, pin, post_fingerprint)
        return pin, payload
    except Exception as error:
        safe_for_failure = (
            os.path.lexists(lease.claim_path)
            and not os.path.lexists(lease.temp_path)
            and not os.path.lexists(lease.final_path)
            and not os.path.lexists(lease.failure_path)
        )
        if safe_for_failure:
            failure_pin, _failure = _publish_delivery_failure(
                roots, contract, profile_path, implementation_review_path,
                expected_implementation_review_sha256, manifest_path, error,
                "INPUT_LOCK", "CONTRACT", started_at, started_monotonic,
                intended_argv, lease=lease,
                dependency_fingerprint=dependency_fingerprint,
                rehash_callback=rehash,
            )
            _raise_published_failure(error, failure_pin)
        raise


def _load_publication_module():
    name = "service_v1_b1_r4_publication"
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    path = Path(__file__).with_name("service_v1_b1_r4_publication.py")
    specification = importlib.util.spec_from_file_location(name, path)
    _need(specification is not None and specification.loader is not None,
          "BLOCKED_IMPLEMENTATION", "cannot load publication module")
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


def _destination(root_name: str, relative: str) -> str:
    if root_name == "standalone":
        return "FEELM-standalone/" + relative
    if root_name == "team":
        return "S15P21E106/" + relative
    if root_name == "wslEvidence":
        return RUN_ID + "/evidence/" + PurePosixPath(relative).name
    if root_name == "wslDelivery":
        return RUN_ID + "/" + relative
    return RUN_ID + "/" + relative


def _spec(
    group: str,
    logical: str,
    source_root: str,
    relative: str,
    size: int,
    digest: str,
    *,
    kind: str = "regular-file",
    virtual_bytes: bytes = b"",
) -> SourceSpec:
    return SourceSpec(
        group,
        logical,
        source_root,
        relative,
        _destination(source_root, relative if source_root != "virtual" else logical),
        size,
        digest,
        kind,
        virtual_bytes,
    )


MODEL_SPECS = (
    _spec("modelInputs", "contract/training-recipe.v1.json", "team", "pipeline/configs/service-v1/training-recipe.v1.json", 15764, "d403a27fab09453f98b9988fcfb3867b83e41ae217f9f5f3be5976321eef8d7b"),
    _spec("modelInputs", "contract/service-v1.json", "team", "pipeline/artifacts/service-v1.json", 15504, "1a9ba0cd0101f6d065227c37ee642a3fc75c2c69b34c0da8fa6dacff351c2343"),
    _spec("modelInputs", "contract/MODELS.md", "team", "pipeline/docs/service-v1/MODELS.md", 25079, "5ad4c852b89ca02da00c30f0fc184aa6018db4abf1808c057772a4908d376701"),
    _spec("modelInputs", "contract/feature-schema.v1.json", "team", "pipeline/configs/service-v1/feature-schema.v1.json", 39243, "fda2be4f40b76e46b88dbb53523ef404bbf8a13c68bbf012acc58da9a63948ca"),
    _spec("modelInputs", "source/natural-train.parquet", "standalone", "outputs/recommendation-evidence/foundation340/RH/train.parquet", 832717601, "9d8d33a252991c032704d4072003b4fb9f400136f3c411a2698ae5fee592fa45"),
    _spec("modelInputs", "source/tmdb-masked-train.parquet", "standalone", "outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-masked-views-v1/tmdb-masked-rh230.parquet", 891461814, "27aee771597ba230654b2e99c1eea047fd25e3265161c51b6873474d47c65f01"),
    _spec("modelInputs", "source/masked-manifest.json", "standalone", "outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-masked-views-v1/manifest.json", 5176, "82eb3635f1ae914a056c2813c4786dfa2de56abbb37aaf5ce852685f6eb6a557"),
    _spec("modelInputs", "source/views-manifest.json", "standalone", "outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-masked-views-v1/views-manifest.json", 1710, "df8dd8bbfea4a71e506958c5b7e1499b5010350a326cbfac2c9bfe23a607792a"),
    _spec("modelInputs", "source/masked-review.json", "standalone", "outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-masked-views-v1-result-review.json", 10174, "f7e322206d95c8ad418926a08394625f84a25b611f4e9595c97dd03cca6c7b1c"),
)

SCORE_SPECS = (
    _spec("scoreInputs", "source/natural-score.parquet", "standalone", "outputs/recommendation-evidence/foundation340/RH/score.parquet", 2985357, "9832425537943823f524ae6730aa1059b77d2c0464664f1dc29357bf95078f9b"),
)

WORKER_SPECS = (
    _spec("workerRuntime", "implementation/service_v1_b1_spark_worker.py", "standalone", "scripts/service_v1_b1_spark_worker.py", 40526, "9708e0b3fdc618c5df2240c9e1dcf368fd4f6fa411d7915c31104b3af2146d42"),
    _spec("workerRuntime", "implementation/combination340_models.py", "standalone", "scripts/combination340_models.py", 4025, "a519586b25d81574a96e1847dc6a4d8d08d3ded276deff64c5d82ff0bcdecd25"),
    _spec("workerRuntime", "implementation/rec046_common.py", "standalone", "scripts/rec046_common.py", 8001, "42dd14e83ecbee0c02c5e4233abb4b6a8d807ad2213c24a22cfb82350bc47848"),
    _spec("workerRuntime", "runtime/docker-image-id", "wslDelivery", "runtime/docker-image-id", 71, "07666208de67cba550e28294fc88c22981071e2026894da726c12ea42c806137"),
)

EVALUATION_SPECS = (
    _spec("evaluationInputs", "evaluation/EVALUATION.md", "team", "pipeline/docs/service-v1/EVALUATION.md", 12466, "db7bd86f6b72b2edee7b6a65610bdb177c65937b518288417e3c6bdbb02bf3e3"),
    _spec("evaluationInputs", "evaluation/contexts.json", "standalone", "outputs/recommendation-evidence/text339/contexts.json", 12290713, "951fc2464bd3aea25ea486c79c33084f6241f7846a3ed626e9d5fc3907a7aab7"),
    _spec("evaluationInputs", "evaluation/catalog.parquet", "standalone", "outputs/recommendation-evidence/text339/catalog.parquet", 728100, "0bde668e0e26f5f82b5c41d90d62c7569fd350bf2a2fd4b59bb4402f438c5947"),
    _spec("evaluationInputs", "evaluation/labels.parquet", "standalone", "outputs/recommendation-evidence/text339/labels.parquet", 75490, "e3bf301a6e2ea7885b59bcab7fe83c2d3ad84f93f2f1bbb2d54d943b9a658db8"),
    _spec("evaluationInputs", "evaluation/evaluation-seal.json", "standalone", "outputs/recommendation-evidence/text339/evaluation-seal.json", 4890, "0ab420fb3e50cf770d9dd7a24d64e5ce8896a4c17b38a7ba5df73398ad6fb9c7"),
    _spec("evaluationInputs", "evaluation/roles.csv", "standalone", "outputs/recommendation-evidence/final344/roles.csv", 5806, "466b7cede9cb2d67f2bd7fca4fcdada770943bdd9d5924c35ccfdbe6a4cf2cc9"),
    _spec("evaluationInputs", "evaluation/metadata.parquet", "standalone", "outputs/recommendation-evidence/rec-ev-045/metadata.parquet", 6891831, "4d838874938115be7a4b1f629a920dd196e082b655b75d559d71039e52eb7d8d"),
    _spec("evaluationInputs", "evaluation/ratings.parquet", "standalone", "outputs/recommendation-evidence/text339/ratings.parquet", 32950407, "28b46687abec2e0edb3a892ec4f4dbd9d5cca701bf220b2812f9e7cf9b905a63"),
    _spec("evaluationInputs", "evaluation/b0-predictions.npy", "standalone", "outputs/recommendation-evidence/final344/GBT120_s339/predictions.npy", 745968, "6520b9094c89824fa2ed833da7618851536d9a7ce3f74d20d2233c71e34cea4f"),
    _spec("evaluationInputs", "evaluation/b0-seal.json", "standalone", "outputs/recommendation-evidence/final344/GBT120_s339-seal.json", 6911, "ba9be1a4769d9cd77a8d8b0a11b437ada10254a030656e98cfa7f7f07d7ffee7"),
)

ALS_CHILDREN = (
    ("_SUCCESS", 0, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
    ("._SUCCESS.crc", 8, "1d44f510ec2ed7595badbec80583316defc14e8dd89130d719724149adfaa07d"),
    (".part-00000-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet.crc", 5012, "11178dfd21ed556940979fa479b90e4aa8428664a408092c0e764ff6510136bd"),
    (".part-00001-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet.crc", 5956, "d8ebea0b42fe459706af7a5345d4b33eece9a2915aae3c5c24938c20947315a9"),
    (".part-00002-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet.crc", 5124, "5302d3a66499097662b384cd57766a05b2b5a5440f54f44a9d0b63321a411289"),
    (".part-00003-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet.crc", 5944, "0340a491786074efad6361c5780b3ff2e65551093a26f0e9e6ddd1e6121171fc"),
    (".part-00004-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet.crc", 4968, "ea8bdfa93384e93bac31484c7c6fdc3d130885d92c22a921d7a84144665bdd30"),
    (".part-00005-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet.crc", 6140, "f1c2e1de9731f9952b87c3ff6d7bf6ca84ed9926f9e3fbd5731029a711f38f29"),
    (".part-00006-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet.crc", 4988, "ebc504ac0f2382deb12b70929da9a103bdf005b6c52c1b5409fa1ca3651b3a5b"),
    (".part-00007-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet.crc", 6292, "10d719359e51b9f57198eefb75099636080a96c61ee04153cc467732d9cb7cff"),
    ("part-00000-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet", 640387, "f6d328a55d9059e5e4170215071aeb522381e9aceb247a195cddee2df249ee66"),
    ("part-00001-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet", 761270, "de79f165b1ae21beabd1bf6352d029b88b6383567689192f2280a45b9f680fa6"),
    ("part-00002-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet", 654425, "0c761ca6573a8c35162af0b77294030b9c4f00cef30b3fbb549689d05397c9aa"),
    ("part-00003-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet", 759762, "9819744e3bad16494129969a423a6ee650d5ee3b2ca6e07b18f9121614ca5d76"),
    ("part-00004-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet", 634621, "c0230036ee3de8fab7d2b6138a8b45f4ba2b1f15adc19093f16503c4fc5d39c6"),
    ("part-00005-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet", 784751, "666787c824db1064feb0f8563d49180a7de87beb18e9a82e8554b313fbb7537b"),
    ("part-00006-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet", 636997, "089631f252fbcc9a9cc875836ee0c62db7002b16c175f7f96f997fce37b337f6"),
    ("part-00007-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet", 804047, "1fb552c709468dca889e02d184a61fe911c7ba70e64a24bc5fe0a2b07dbf828b"),
)


def _als_specs() -> tuple[SourceSpec, ...]:
    base = "outputs/recommendation-evidence/combination340/ALS/item-factors"
    return tuple(
        _spec(
            "evaluationInputs",
            "evaluation/ALS/item-factors/" + child,
            "standalone",
            base + "/" + child,
            size,
            digest,
        )
        for child, size, digest in ALS_CHILDREN
    )


ANCESTOR_FIXED = (
    ("r3/plan", "docs/recommendation/plans/service-v1-b1-r3-fit-recovery.md", 22087, "4de13f041532b7249b22f5a2bbfbd7919f79d94e4c25f2ae16fe95104a2b3d9a"),
    ("r3/profile", "docs/recommendation/plans/service-v1-b1-r3-local4c12g-t14400-profile.json", 1522, "761c210364ab465f9143ded5dba8c2f3fba9a1d34c10f1ea54d742a528494c23"),
    ("r3/runner", "scripts/run_service_v1_b1_gbt_r3.py", 143994, "783a5a4c7f1783416271de0a1cde6a6be55409ef8b625a757f8a07c153c7a574"),
    ("r3/spark-auditor", "scripts/audit_service_v1_b1_spark_outputs_r3.py", 89009, "92a9e48597205f54509ccd7fbd9c717cb791bbd31a6998cb9d966177f830d3be"),
    ("r3/evaluator", "scripts/evaluate_service_v1_b1_r3.py", 215832, "de036adc7b35d151aeaf8006e6a2a64f86130f0d9f49b80ac8fa15ec336fc87a"),
    ("r3/evaluation-auditor", "scripts/audit_service_v1_b1_evaluation_outputs_r3.py", 165251, "7f44c00ade107dd82e54658c39d765df2aa8ac7cfddc8e650ba4bebb230fa28c"),
    ("r3/runner-test", "tests/test_service_v1_b1_gbt_runner_r3.py", 25063, "c348c6d02741ffe4a30635c3a2fc0285c8503f15618cac75b3c65943582145af"),
    ("r3/spark-auditor-test", "tests/test_audit_service_v1_b1_spark_outputs_r3.py", 19497, "12af534b89383318cdc6b8fb27610dca58880737b4b7a970825ee8a70a9a9d18"),
    ("r3/evaluator-test", "tests/test_evaluate_service_v1_b1_r3.py", 116597, "5a69d17ada3074c34ffbdce3e14c18333fc163b58ebf80b6ba59b5dc9b673494"),
    ("r3/evaluation-auditor-test", "tests/test_audit_service_v1_b1_evaluation_outputs_r3.py", 29865, "5bb03fd080f4e48ea8c38a142c60095d0448245433808c01174b818602a349b4"),
)


def _ancestor_specs() -> tuple[SourceSpec, ...]:
    specs = [
        _spec("ancestorEvidence", "ancestor/" + logical, "standalone", relative, size, digest)
        for logical, relative, size, digest in ANCESTOR_FIXED
    ]
    base = "outputs/recommendation-evidence/service-v1-pretraining-20260913"
    r3_preflight = {
        "command.json": (8653, "1c8979c7d60474e871fb3220194fca5eba39b186ad016a6cee7d2d5fc33134eb"),
        "execution-profile.json": (2283, "6074818bf05b640ba3ff2b83a2d03d714635761d7e1f317ceefd1332332f4273"),
        "input-lock.json": (6493, "9a3f678ec0fc454498cbb7dbb54973c4c2b9e3eceaee2d3238b96d51cd15f460"),
        "manifest.json": (3149, "c2f2e7914bd5a222001759d1ecd40119a2fa7a8d17ea3214847050e8535da407"),
        "partition-identity.json": (2489, "2a7f7fbd9000730db749cc673d9a43a5027fa3ca76d517cda826f3f420d45c99"),
        "recovery-reference.json": (5314, "d35f8ecd0c8c5fca4981fc25b2e550f919c197abf37ca35d823ce603eab9f96d"),
        "resource.json": (8851, "bf5714a12c394c835d00f18d473772e00da758b4c2998d2175d7f437a120bace"),
        "run.log": (12961, "be8521910c5506e181ca01e6864885056e88542cbbb0d14e7b6d6e0979628171"),
    }
    for child, (size, digest) in r3_preflight.items():
        relative = base + "/b1-gbt120-s339-v1-r3-local4c12g-t14400-preflight/" + child
        specs.append(_spec("ancestorEvidence", "ancestor/r3/preflight/" + child, "standalone", relative, size, digest))
    siblings = (
        ("preflight-review.json", base + "/b1-gbt120-s339-v1-r3-local4c12g-t14400-preflight-result-review.json", 11801, "0d397589778aff2a04d2df2101ac61344e6ce3e7f9d2e204e47156611f92413a"),
        ("fit-failure.json", base + "/b1-gbt120-s339-v1-r3-local4c12g-t14400-fit-failure.json", 100384, "75ad1999a8e24d723c56e0b8ecd4f96a573c2086b86daf675d68ebf889940276"),
        ("fit-failure-review.json", base + "/b1-gbt120-s339-v1-r3-local4c12g-t14400-fit-failure-result-review.json", 8184, "4c76189254643d58bb1045c89aa879b64be06ddbce9e88294ae7d6229836191d"),
    )
    for logical, relative, size, digest in siblings:
        specs.append(_spec("ancestorEvidence", "ancestor/r3/" + logical, "standalone", relative, size, digest))
    r2 = (
        ("preflight/command.json", base + "/b1-gbt120-s339-v1-r2-preflight/command.json", 7720, "86e05c4b3555297c9a803b91cea840b2f7ea83286a2a82591a5eb1f950b15859"),
        ("preflight/input-lock.json", base + "/b1-gbt120-s339-v1-r2-preflight/input-lock.json", 3489, "fbd4ae898a8cc35e3e4e7c4442298d6940dc88b3854d20182b7f38c87334d775"),
        ("preflight/manifest.json", base + "/b1-gbt120-s339-v1-r2-preflight/manifest.json", 2351, "7a47a40ac923ba1f8a638ad6029aa72349245b65055f3db8915a2327de174e01"),
        ("preflight/partition-identity.json", base + "/b1-gbt120-s339-v1-r2-preflight/partition-identity.json", 2489, "2a7f7fbd9000730db749cc673d9a43a5027fa3ca76d517cda826f3f420d45c99"),
        ("preflight/recovery-reference.json", base + "/b1-gbt120-s339-v1-r2-preflight/recovery-reference.json", 959, "9c7b39b342743e40461f5d5b9c8f0c02268d3c4f82c177ac39b4f97ad8b4133d"),
        ("preflight/resource.json", base + "/b1-gbt120-s339-v1-r2-preflight/resource.json", 1565, "a91445352b3fd2f53b84106667686f1d8778ef5b4399d2c15631c7844b634ffd"),
        ("preflight/run.log", base + "/b1-gbt120-s339-v1-r2-preflight/run.log", 12955, "52a3c6a0f78b293f11ec2af40d2cd23089d695f4c063bebb6a416aab0f87a063"),
        ("preflight-review.json", base + "/b1-gbt120-s339-v1-r2-preflight-result-review.json", 8706, "cc49688618bfd408a957a22522456912320519d981c34ad87aefb190a0910132"),
        ("fit-failure.json", base + "/b1-gbt120-s339-v1-r2-fit-failure.json", 24854, "4ad571439af7f290dd32ce27922519f31e3b156f90998b26a69e2c892aba7e37"),
        ("runner.py", "scripts/run_service_v1_b1_gbt.py", 88623, "cbc8983a0047b5aa0a2fc5a2d3794080afcf48583fe1be90c6d2dd3dd5af6f8e"),
        ("plan.md", "docs/recommendation/plans/service-v1-b1-spark-runner.md", 47340, "c5b90b0d19374588fc5cb7a1d98cd6510a1c3708df9667131df5eb3826c79d49"),
    )
    for logical, relative, size, digest in r2:
        specs.append(_spec("ancestorEvidence", "ancestor/r2/" + logical, "standalone", relative, size, digest))
    return tuple(specs)


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


def _runtime_paths(wheelhouse_manifest: Mapping[str, Any]) -> frozenset[str]:
    _need(
        set(wheelhouse_manifest)
        == {"schemaVersion", "pythonVersion", "interpreterTag", "abiTag", "platformTag",
            "files", "wheelhouseSetSha256"},
        "BLOCKED_RUNTIME", "wheelhouse manifest shape drift",
    )
    _need(
        (wheelhouse_manifest["schemaVersion"], wheelhouse_manifest["pythonVersion"],
         wheelhouse_manifest["interpreterTag"], wheelhouse_manifest["abiTag"],
         wheelhouse_manifest["platformTag"], wheelhouse_manifest["wheelhouseSetSha256"])
        == ("feelm-service-v1-b1-r4-wheelhouse/1", "3.12.3", "cp312", "cp312",
            "manylinux_2_17_x86_64",
            "af87e5e9b660085d45ab5562dc76fd3f0f617d1d069108eb8c3677b66ad0fa5c"),
        "BLOCKED_RUNTIME", "wheelhouse identity drift",
    )
    files = wheelhouse_manifest["files"]
    _need(isinstance(files, list) and len(files) == 7, "BLOCKED_RUNTIME", "wheel count drift")
    paths: list[str] = []
    digest_records: list[dict[str, object]] = []
    for index, item in enumerate(files):
        pin = _validate_pin(item, f"wheelhouse file {index}")
        relative = str(pin["path"])
        _safe_relative(relative, "wheel path")
        _need(relative.startswith("runtime/service-v1-b1-r4-wheelhouse/"),
              "BLOCKED_RUNTIME", "wheel path outside wheelhouse")
        paths.append(relative)
        digest_records.append({"logicalPath": relative, "bytes": pin["bytes"], "sha256": pin["sha256"]})
    _need(len(paths) == len(set(paths)), "BLOCKED_RUNTIME", "duplicate wheel path")
    _need(_group_digest(digest_records) == wheelhouse_manifest["wheelhouseSetSha256"],
          "BLOCKED_RUNTIME", "wheelhouse set digest drift")
    return frozenset((RUNTIME_LOCK_PATH, WHEEL_MANIFEST_PATH, IMAGE_ARCHIVE_PATH, *paths))


def _control_specs(
    roots: Roots,
    implementation_review: Mapping[str, Any],
    implementation_review_path: Path,
    runtime_paths: frozenset[str],
) -> tuple[SourceSpec, ...]:
    target = implementation_review["target"]
    source_paths = {str(item["path"]) for item in target["sourceFiles"]}
    test_paths = {str(item["path"]) for item in target["testFiles"]}
    _need(source_paths == SOURCE_FILE_SET, "BLOCKED_GATE", "implementation source set mismatch")
    _need(test_paths == TEST_FILE_SET, "BLOCKED_GATE", "implementation test set mismatch")
    observed_runtime_paths = {str(item["path"]) for item in target["runtimeFiles"]}
    _need(observed_runtime_paths == runtime_paths, "BLOCKED_GATE", "implementation runtime set mismatch")
    archive_pin = next(item for item in target["runtimeFiles"] if item["path"] == IMAGE_ARCHIVE_PATH)
    _need(archive_pin["bytes"] == IMAGE_ARCHIVE_BYTES
          and archive_pin["sha256"] == IMAGE_ARCHIVE_SHA256,
          "BLOCKED_IMAGE", "Docker archive production pin drift")
    specs: list[SourceSpec] = []
    for section in ("sourceFiles", "testFiles", "runtimeFiles"):
        for pin in target[section]:
            relative = str(pin["path"])
            logical = relative if relative == IMAGE_ARCHIVE_PATH else "control/" + relative
            specs.append(_spec(
                "controlAndImplementation",
                logical,
                "standalone",
                relative,
                int(pin["bytes"]),
                str(pin["sha256"]),
            ))
    for key in ("plan", "profile"):
        pin = target[key]
        relative = str(pin["path"])
        specs.append(_spec(
            "controlAndImplementation", "control/" + relative, "standalone", relative,
            int(pin["bytes"]), str(pin["sha256"]),
        ))
    design = target["designReview"]
    specs.append(_spec(
        "controlAndImplementation",
        "control/evidence/" + DESIGN_REVIEW_NAME,
        "wslEvidence",
        DESIGN_REVIEW_NAME,
        int(design["bytes"]),
        str(design["sha256"]),
    ))
    implementation_pin = _pin_record(implementation_review_path, IMPLEMENTATION_REVIEW_NAME)
    specs.append(_spec(
        "controlAndImplementation",
        "control/evidence/" + IMPLEMENTATION_REVIEW_NAME,
        "wslEvidence",
        IMPLEMENTATION_REVIEW_NAME,
        int(implementation_pin["bytes"]),
        str(implementation_pin["sha256"]),
    ))
    for kind in TEST_RUN_KINDS:
        evidence = implementation_review["testRuns"][kind]["evidence"]
        expected_relative = ".implementation-test-evidence/" + kind + ".json"
        expected_absolute = (roots.wsl_delivery / expected_relative).as_posix()
        _need(evidence["path"] == expected_absolute,
              "BLOCKED_GATE", f"implementation test evidence path drift: {kind}")
        specs.append(_spec(
            "controlAndImplementation",
            "control/implementation-test-evidence/" + kind + ".json",
            "wslDelivery",
            expected_relative,
            int(evidence["bytes"]),
            str(evidence["sha256"]),
        ))
    specs.append(_spec(
        "controlAndImplementation",
        "runtime/docker-image-contract.json",
        "virtual",
        "runtime/docker-image-contract.json",
        len(IMAGE_CONTRACT_BYTES),
        hashlib.sha256(IMAGE_CONTRACT_BYTES).hexdigest(),
        kind="docker-image-contract",
        virtual_bytes=IMAGE_CONTRACT_BYTES,
    ))
    logicals = [spec.logical_path for spec in specs]
    _need(len(logicals) == len(set(logicals)), "BLOCKED_GROUP_OVERLAP", "control path overlap")
    return tuple(specs)


def default_contract(
    roots: Roots,
    implementation_review_path: Path,
    expected_implementation_review_sha256: str,
) -> DeliveryContract:
    _need(roots.standalone.name == "FEELM-standalone" and roots.team.name == "S15P21E106"
          and roots.standalone.parent == roots.team.parent,
          "BLOCKED_PATH", "standalone/team roots are not exact siblings")
    _need(roots.wsl_evidence != roots.wsl_delivery,
          "BLOCKED_PATH", "evidence and delivery staging roots overlap")
    implementation, _ = _validate_implementation_review(
        implementation_review_path, expected_implementation_review_sha256
    )
    _validate_reviewed_implementation_bytes(roots, implementation)
    wheelhouse_manifest = _load_json(roots.standalone / WHEEL_MANIFEST_PATH)
    runtime_paths = _runtime_paths(wheelhouse_manifest)
    runtime_by_path = {str(pin["path"]): pin for pin in implementation["target"]["runtimeFiles"]}
    _need(all(runtime_by_path.get(str(pin["path"])) == pin for pin in wheelhouse_manifest["files"]),
          "BLOCKED_RUNTIME", "implementation wheel pins differ from wheelhouse manifest")
    controls = _control_specs(roots, implementation, implementation_review_path, runtime_paths)
    als_base = "outputs/recommendation-evidence/combination340/ALS/item-factors"
    wheel_base = "runtime/service-v1-b1-r4-wheelhouse"
    directories = (
        ExactDirectory("standalone", als_base, tuple(sorted(child for child, _, _ in ALS_CHILDREN))),
        ExactDirectory(
            "standalone",
            wheel_base,
            tuple(sorted(PurePosixPath(str(item["path"])).name for item in wheelhouse_manifest["files"])),
        ),
        ExactDirectory(
            "wslDelivery", ".implementation-test-evidence",
            tuple(sorted(kind + ".json" for kind in TEST_RUN_KINDS)),
        ),
        ExactDirectory("wslDelivery", "runtime", ("docker-image-id",)),
    )
    specs = MODEL_SPECS + SCORE_SPECS + WORKER_SPECS + EVALUATION_SPECS + _als_specs() + controls + _ancestor_specs()
    return DeliveryContract(
        specs=specs,
        exact_directories=directories,
        semantic_facts=SEMANTIC_FACTS,
        producer_logical_path="control/scripts/build_service_v1_b1_r4_server_delivery.py",
        implementation_review_logical_path="control/evidence/" + IMPLEMENTATION_REVIEW_NAME,
        wsl_delivery_children=(".implementation-test-evidence", "runtime"),
        verify_ancestry_semantics=True,
    )


def _prepare_docker_id(root: Path) -> None:
    _lexical_root(root, "wslDelivery")
    runtime = root / "runtime"
    if not os.path.lexists(runtime):
        os.mkdir(runtime, 0o700)
        root_descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(root_descriptor)
        finally:
            os.close(root_descriptor)
    _need(os.path.realpath(runtime) == str(runtime), "BLOCKED_PATH", "runtime staging is linked")
    target = runtime / "docker-image-id"
    expected = IMAGE_ID.encode("ascii")
    if target.exists():
        data, _, _ = _read_regular(target)
        _need(data == expected, "BLOCKED_IMAGE", "staged docker image ID drift")
        return
    descriptor = os.open(
        target,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        offset = 0
        while offset < len(expected):
            written = os.write(descriptor, expected[offset:])
            _need(written > 0, "BLOCKED_IO", "short Docker image ID write")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    parent_descriptor = os.open(runtime, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(parent_descriptor)
    finally:
        os.close(parent_descriptor)


def _direct_children(path: Path) -> list[str]:
    try:
        return sorted(os.listdir(path))
    except OSError:
        return []


def _failure_pin(path: Path, logical_path: str) -> tuple[dict[str, object] | None, dict[str, object]]:
    try:
        pin = _pin_record(path, logical_path)
    except Exception as error:
        state = {
            "logicalPath": logical_path,
            "absolutePath": str(path),
            "state": "UNAVAILABLE",
            "error": {"type": type(error).__name__, "message": str(error)},
        }
        return None, state
    return pin, {
        "logicalPath": logical_path,
        "absolutePath": str(path),
        "state": "PRESENT",
        "bytes": pin["bytes"],
        "sha256": pin["sha256"],
    }


def _failure_dependency_state(
    roots: Roots,
    profile_path: Path,
    implementation_review_path: Path,
    expected_implementation_review_sha256: str,
) -> dict[str, object]:
    producer, producer_state = _failure_pin(
        Path(__file__).resolve(), "scripts/build_service_v1_b1_r4_server_delivery.py"
    )
    profile, profile_state = _failure_pin(profile_path, PROFILE_PATH)
    design, design_state = _failure_pin(
        roots.wsl_evidence / DESIGN_REVIEW_NAME, "evidence/" + DESIGN_REVIEW_NAME
    )
    implementation, implementation_state = _failure_pin(
        implementation_review_path, "evidence/" + IMPLEMENTATION_REVIEW_NAME
    )
    runtime_lock, runtime_state = _failure_pin(
        roots.standalone / RUNTIME_LOCK_PATH, RUNTIME_LOCK_PATH
    )
    _need(producer is not None, "BLOCKED_FAILURE_SCHEMA", "failure producer is unreadable")
    if profile is None:
        profile = {
            "path": PROFILE_PATH,
            "bytes": 6_516,
            "sha256": "56e06e311566c23181b4483200d55049d66b728efcbf12f7a7047c80ce5b7dd5",
        }
    state = {
        "producer": producer,
        "profile": profile,
        "designReview": design,
        "implementationReview": implementation,
        "runtimeLock": runtime_lock,
        "sourceStates": [
            producer_state, profile_state, design_state, implementation_state, runtime_state,
        ],
        "expectedImplementationReviewSha256": expected_implementation_review_sha256,
        "expectedCompletedChildren": list(DELIVERY_COMPLETED_CHILDREN),
    }
    state["designReviewObservedDecision"] = _captured_review_decision(
        roots.wsl_evidence / DESIGN_REVIEW_NAME
    )
    state["implementationReviewObservedDecision"] = _captured_review_decision(
        implementation_review_path
    )
    return state


def _lock_record(specification: SourceSpec) -> dict[str, object]:
    return {
        "logicalPath": specification.logical_path,
        "sourceRoot": specification.source_root,
        "sourceRelativePath": specification.source_relative_path,
        "destinationRelativePath": specification.destination_relative_path,
        "bytes": specification.expected_bytes,
        "sha256": specification.expected_sha256,
        "kind": specification.kind,
    }


def _valid_expected_pin(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping) or set(value) != {"path", "bytes", "sha256"}:
        return None
    if (
        not isinstance(value.get("path"), str)
        or type(value.get("bytes")) is not int
        or int(value["bytes"]) < 0
        or not _is_sha(value.get("sha256"))
    ):
        return None
    return {"path": str(value["path"]), "bytes": int(value["bytes"]), "sha256": str(value["sha256"])}


def _review_derived_control_specs(
    roots: Roots,
    implementation_review_path: Path,
) -> tuple[tuple[SourceSpec, ...], tuple[str, ...], list[str]]:
    errors: list[str] = []
    try:
        review = _load_json(implementation_review_path)
    except Exception as error:
        return (), (), [f"{type(error).__name__}: {error}"]
    target = review.get("target")
    if not isinstance(target, Mapping):
        return (), (), ["implementation review target is unavailable"]
    specifications: list[SourceSpec] = []
    observed_sections: dict[str, set[str]] = {}
    for section, required in (
        ("sourceFiles", SOURCE_FILE_SET),
        ("testFiles", TEST_FILE_SET),
        ("runtimeFiles", frozenset()),
    ):
        values = target.get(section)
        if not isinstance(values, list):
            errors.append(f"{section} is not an array")
            observed_sections[section] = set()
            continue
        paths: set[str] = set()
        for index, raw in enumerate(values):
            pin = _valid_expected_pin(raw)
            if pin is None:
                errors.append(f"{section}[{index}] is not a pin")
                continue
            relative = str(pin["path"])
            paths.add(relative)
            logical = relative if relative == IMAGE_ARCHIVE_PATH else "control/" + relative
            specifications.append(_spec(
                "controlAndImplementation",
                logical,
                "standalone",
                relative,
                int(pin["bytes"]),
                str(pin["sha256"]),
            ))
        observed_sections[section] = paths
        if required and paths != set(required):
            errors.append(f"{section} path set differs from the frozen inventory")
    runtime_paths = observed_sections.get("runtimeFiles", set())
    wheels = tuple(sorted(
        PurePosixPath(path).name
        for path in runtime_paths
        if path.startswith("runtime/service-v1-b1-r4-wheelhouse/")
    ))
    if (
        len(runtime_paths) != 10
        or len(wheels) != 7
        or not {RUNTIME_LOCK_PATH, WHEEL_MANIFEST_PATH, IMAGE_ARCHIVE_PATH}.issubset(runtime_paths)
    ):
        errors.append("runtimeFiles path set differs from the frozen inventory")
    for key in ("plan", "profile"):
        pin = _valid_expected_pin(target.get(key))
        if pin is None:
            errors.append(f"{key} target is not a pin")
            continue
        specifications.append(_spec(
            "controlAndImplementation",
            "control/" + str(pin["path"]),
            "standalone",
            str(pin["path"]),
            int(pin["bytes"]),
            str(pin["sha256"]),
        ))
    design = _valid_expected_pin(target.get("designReview"))
    if design is None:
        errors.append("designReview target is not a pin")
    else:
        specifications.append(_spec(
            "controlAndImplementation",
            "control/evidence/" + DESIGN_REVIEW_NAME,
            "wslEvidence",
            DESIGN_REVIEW_NAME,
            int(design["bytes"]),
            str(design["sha256"]),
        ))
    implementation, _implementation_state = _failure_pin(
        implementation_review_path, "evidence/" + IMPLEMENTATION_REVIEW_NAME
    )
    if implementation is None:
        errors.append("implementation review is not readable")
    else:
        specifications.append(_spec(
            "controlAndImplementation",
            "control/evidence/" + IMPLEMENTATION_REVIEW_NAME,
            "wslEvidence",
            IMPLEMENTATION_REVIEW_NAME,
            int(implementation["bytes"]),
            str(implementation["sha256"]),
        ))
    test_runs = review.get("testRuns")
    if not isinstance(test_runs, Mapping):
        errors.append("testRuns is not an object")
    else:
        for kind in TEST_RUN_KINDS:
            item = test_runs.get(kind)
            evidence = item.get("evidence") if isinstance(item, Mapping) else None
            pin = _valid_expected_pin(evidence)
            expected_relative = ".implementation-test-evidence/" + kind + ".json"
            if pin is None or pin["path"] != (roots.wsl_delivery / expected_relative).as_posix():
                errors.append(f"testRuns.{kind}.evidence is not the frozen pin")
                continue
            specifications.append(_spec(
                "controlAndImplementation",
                "control/implementation-test-evidence/" + kind + ".json",
                "wslDelivery",
                expected_relative,
                int(pin["bytes"]),
                str(pin["sha256"]),
            ))
    specifications.append(_spec(
        "controlAndImplementation",
        "runtime/docker-image-contract.json",
        "virtual",
        "runtime/docker-image-contract.json",
        len(IMAGE_CONTRACT_BYTES),
        hashlib.sha256(IMAGE_CONTRACT_BYTES).hexdigest(),
        kind="docker-image-contract",
        virtual_bytes=IMAGE_CONTRACT_BYTES,
    ))
    logicals = [spec.logical_path for spec in specifications]
    if len(logicals) != len(set(logicals)):
        errors.append("review-derived control inventory overlaps")
    return tuple(specifications), wheels, errors


def _failure_phase_input_lock(
    roots: Roots,
    contract: DeliveryContract | None,
    profile_path: Path,
    implementation_review_path: Path,
    expected_implementation_review_sha256: str,
) -> dict[str, object]:
    dependency_state = _failure_dependency_state(
        roots, profile_path, implementation_review_path, expected_implementation_review_sha256
    )
    if contract is not None:
        specifications = contract.specs
        exact_directories = contract.exact_directories
        delivery_children = contract.wsl_delivery_children
        facts = contract.semantic_facts
        producer_logical = contract.producer_logical_path
        implementation_logical = contract.implementation_review_logical_path
        resolution = {"state": "VALIDATED_CONTRACT", "errors": []}
    else:
        controls, wheels, errors = _review_derived_control_specs(roots, implementation_review_path)
        specifications = (
            MODEL_SPECS + SCORE_SPECS + WORKER_SPECS + EVALUATION_SPECS
            + _als_specs() + controls + _ancestor_specs()
        )
        exact_directories = (
            ExactDirectory(
                "standalone",
                "outputs/recommendation-evidence/combination340/ALS/item-factors",
                tuple(sorted(child for child, _size, _digest in ALS_CHILDREN)),
            ),
            ExactDirectory("standalone", "runtime/service-v1-b1-r4-wheelhouse", wheels),
            ExactDirectory(
                "wslDelivery",
                ".implementation-test-evidence",
                tuple(sorted(kind + ".json" for kind in TEST_RUN_KINDS)),
            ),
            ExactDirectory("wslDelivery", "runtime", ("docker-image-id",)),
        )
        delivery_children = (".implementation-test-evidence", "runtime")
        facts = SEMANTIC_FACTS
        producer_logical = "control/scripts/build_service_v1_b1_r4_server_delivery.py"
        implementation_logical = "control/evidence/" + IMPLEMENTATION_REVIEW_NAME
        resolution = {
            "state": "REVIEWED_EXPECTATIONS" if not errors else "INCOMPLETE_REVIEWED_EXPECTATIONS",
            "errors": errors,
        }
    groups = {
        group: sorted(
            (_lock_record(spec) for spec in specifications if spec.group == group),
            key=lambda item: str(item["logicalPath"]),
        )
        for group in GROUPS
    }
    directories = [
        {
            "sourceRoot": item.source_root,
            "relativePath": item.relative_path,
            "children": list(sorted(item.children)),
        }
        for item in sorted(exact_directories, key=lambda value: (value.source_root, value.relative_path))
    ]
    return {
        "schemaVersion": "feelm-service-v1-b1-r4-delivery-input-lock/1",
        "runId": RUN_ID,
        "profileId": PROFILE_ID,
        "sourceRoots": {key: str(value) for key, value in roots.mapping.items()},
        "expectedCompletedChildren": list(DELIVERY_COMPLETED_CHILDREN),
        "groups": groups,
        "exactDirectories": directories,
        "wslDeliveryChildren": list(sorted(delivery_children or ())),
        "semanticFacts": dict(facts),
        "producerLogicalPath": producer_logical,
        "implementationReviewLogicalPath": implementation_logical,
        "requiredControlInventory": {
            "sourceFiles": sorted(SOURCE_FILE_SET),
            "testFiles": sorted(TEST_FILE_SET),
            "runtimeCore": [RUNTIME_LOCK_PATH, WHEEL_MANIFEST_PATH, IMAGE_ARCHIVE_PATH],
            "implementationTestEvidence": [kind + ".json" for kind in TEST_RUN_KINDS],
        },
        "resolution": resolution,
        "dependencyState": dependency_state,
    }


def _validate_failure_phase_input_lock(value: object) -> None:
    keys = {
        "schemaVersion", "runId", "profileId", "sourceRoots", "expectedCompletedChildren",
        "groups", "exactDirectories", "wslDeliveryChildren", "semanticFacts",
        "producerLogicalPath", "implementationReviewLogicalPath", "requiredControlInventory",
        "resolution", "dependencyState",
    }
    _need(isinstance(value, Mapping) and set(value) == keys,
          "BLOCKED_FAILURE_SCHEMA", "phase input lock shape")
    _need(value["schemaVersion"] == "feelm-service-v1-b1-r4-delivery-input-lock/1"
          and value["runId"] == RUN_ID and value["profileId"] == PROFILE_ID,
          "BLOCKED_FAILURE_SCHEMA", "phase input lock identity")
    roots = value["sourceRoots"]
    _need(isinstance(roots, Mapping) and set(roots) == {"standalone", "team", "wslEvidence", "wslDelivery"}
          and all(isinstance(path, str) and Path(path).is_absolute() for path in roots.values()),
          "BLOCKED_FAILURE_SCHEMA", "phase input lock roots")
    _need(value["expectedCompletedChildren"] == list(DELIVERY_COMPLETED_CHILDREN),
          "BLOCKED_FAILURE_SCHEMA", "phase input lock predecessors")
    groups = value["groups"]
    _need(isinstance(groups, Mapping) and set(groups) == set(GROUPS),
          "BLOCKED_FAILURE_SCHEMA", "phase input lock groups")
    seen: set[str] = set()
    record_keys = {
        "logicalPath", "sourceRoot", "sourceRelativePath", "destinationRelativePath",
        "bytes", "sha256", "kind",
    }
    for group in GROUPS:
        records = groups[group]
        _need(isinstance(records, list), "BLOCKED_FAILURE_SCHEMA", f"phase input group: {group}")
        logicals: list[str] = []
        for record in records:
            _need(isinstance(record, Mapping) and set(record) == record_keys
                  and isinstance(record["logicalPath"], str)
                  and isinstance(record["sourceRelativePath"], str)
                  and isinstance(record["destinationRelativePath"], str)
                  and record["sourceRoot"] in SOURCE_ROOTS
                  and record["kind"] in KINDS
                  and type(record["bytes"]) is int and record["bytes"] >= 0
                  and _is_sha(record["sha256"]),
                  "BLOCKED_FAILURE_SCHEMA", f"phase input record: {group}")
            logical = str(record["logicalPath"])
            _need(logical not in seen, "BLOCKED_FAILURE_SCHEMA", "phase input logical overlap")
            seen.add(logical)
            logicals.append(logical)
        _need(logicals == sorted(logicals), "BLOCKED_FAILURE_SCHEMA", "phase input record order")
    directories = value["exactDirectories"]
    _need(isinstance(directories, list), "BLOCKED_FAILURE_SCHEMA", "phase input directories")
    for item in directories:
        _need(isinstance(item, Mapping)
              and set(item) == {"sourceRoot", "relativePath", "children"}
              and item["sourceRoot"] in SOURCE_ROOTS
              and isinstance(item["relativePath"], str)
              and isinstance(item["children"], list)
              and item["children"] == sorted(set(item["children"]))
              and all(isinstance(child, str) for child in item["children"]),
              "BLOCKED_FAILURE_SCHEMA", "phase input directory record")
    _need(isinstance(value["wslDeliveryChildren"], list)
          and value["wslDeliveryChildren"] == sorted(set(value["wslDeliveryChildren"]))
          and all(isinstance(child, str) for child in value["wslDeliveryChildren"]),
          "BLOCKED_FAILURE_SCHEMA", "phase input staging children")
    controls = value["requiredControlInventory"]
    _need(isinstance(controls, Mapping)
          and set(controls) == {"sourceFiles", "testFiles", "runtimeCore", "implementationTestEvidence"}
          and controls["sourceFiles"] == sorted(SOURCE_FILE_SET)
          and controls["testFiles"] == sorted(TEST_FILE_SET)
          and controls["runtimeCore"] == [RUNTIME_LOCK_PATH, WHEEL_MANIFEST_PATH, IMAGE_ARCHIVE_PATH]
          and controls["implementationTestEvidence"] == [kind + ".json" for kind in TEST_RUN_KINDS],
          "BLOCKED_FAILURE_SCHEMA", "phase input control inventory")
    resolution = value["resolution"]
    _need(isinstance(resolution, Mapping) and set(resolution) == {"state", "errors"}
          and resolution["state"] in {
              "VALIDATED_CONTRACT", "REVIEWED_EXPECTATIONS", "INCOMPLETE_REVIEWED_EXPECTATIONS"
          }
          and isinstance(resolution["errors"], list)
          and all(isinstance(item, str) for item in resolution["errors"]),
          "BLOCKED_FAILURE_SCHEMA", "phase input resolution")
    _need(isinstance(value["semanticFacts"], Mapping)
          and isinstance(value["producerLogicalPath"], str)
          and isinstance(value["implementationReviewLogicalPath"], str)
          and isinstance(value["dependencyState"], Mapping),
          "BLOCKED_FAILURE_SCHEMA", "phase input closure")


def _failure_state_fingerprint(
    roots: Roots,
    profile_path: Path,
    implementation_review_path: Path,
    expected_implementation_review_sha256: str,
    contract: DeliveryContract | None,
) -> str:
    state = _failure_phase_input_lock(
        roots, contract, profile_path, implementation_review_path,
        expected_implementation_review_sha256,
    )
    return hashlib.sha256(_jcs_bytes(state)).hexdigest()


def _captured_review_decision(path: Path) -> object:
    try:
        payload = _load_json(path)
    except Exception as error:
        return {"unavailable": True, "reason": f"{type(error).__name__}: {error}"}
    decision = payload.get("decision")
    if isinstance(decision, Mapping):
        return dict(decision)
    return {"unavailable": True, "reason": "decision is absent or not an object"}


def _authorization_evidence(
    roots: Roots,
    implementation_review_path: Path,
    state: Mapping[str, object],
) -> list[dict[str, object]]:
    evidence: list[dict[str, object]] = []
    review_specs = (
        (
            "DESIGN_REVIEW",
            roots.wsl_evidence / DESIGN_REVIEW_NAME,
            state["designReview"],
            None,
        ),
        (
            "IMPLEMENTATION_REVIEW",
            implementation_review_path,
            state["implementationReview"],
            IMPLEMENTATION_DECISION,
        ),
    )
    for kind, path, pin, required_decision in review_specs:
        if not isinstance(pin, Mapping):
            continue
        try:
            payload = _load_json(path)
        except Exception:
            continue
        decision = payload.get("decision")
        if payload.get("status") != "PASS" or not isinstance(decision, Mapping):
            continue
        if required_decision is not None and dict(decision) != required_decision:
            continue
        evidence.append({"kind": kind, "pin": pin, "decision": dict(decision)})
    return evidence


def _failure_ancestor_closure(contract: DeliveryContract | None) -> dict[str, object]:
    specifications = (
        tuple(spec for spec in contract.specs if spec.group == "ancestorEvidence")
        if contract is not None
        else _ancestor_specs()
    )
    records = [
        {
            "path": spec.logical_path,
            "bytes": spec.expected_bytes,
            "sha256": spec.expected_sha256,
        }
        for spec in specifications
    ]
    digest_records = [
        {"logicalPath": item["path"], "bytes": item["bytes"], "sha256": item["sha256"]}
        for item in records
    ]
    return {
        "records": records,
        "semanticFacts": dict(contract.semantic_facts if contract is not None else SEMANTIC_FACTS),
        "recordSetSha256": _group_digest(digest_records),
    }


def _validate_phase_failure(payload: Mapping[str, object]) -> None:
    _need(set(payload) == PHASE_FAILURE_KEYS, "BLOCKED_FAILURE_SCHEMA", "phase failure shape")
    _need(
        payload["schemaVersion"] == PHASE_FAILURE_SCHEMA
        and payload["status"] == "FAILED"
        and payload["runId"] == RUN_ID
        and payload["profileId"] == PROFILE_ID
        and payload["phase"] == "delivery-build"
        and type(payload["attemptOrdinal"]) is int
        and payload["attemptOrdinal"] == 1,
        "BLOCKED_FAILURE_SCHEMA",
        "phase failure identity",
    )
    _need(
        isinstance(payload["startedAt"], str)
        and str(payload["startedAt"]).endswith("Z")
        and isinstance(payload["failedAt"], str)
        and str(payload["failedAt"]).endswith("Z")
        and isinstance(payload["elapsedSeconds"], (int, float))
        and not isinstance(payload["elapsedSeconds"], bool)
        and math.isfinite(float(payload["elapsedSeconds"]))
        and float(payload["elapsedSeconds"]) >= 0,
        "BLOCKED_FAILURE_SCHEMA",
        "phase failure timing",
    )
    _need(payload["failureStage"] in FAILURE_STAGES, "BLOCKED_FAILURE_SCHEMA", "failure stage")
    _need(payload["failureKind"] in FAILURE_KINDS, "BLOCKED_FAILURE_SCHEMA", "failure kind")
    error = payload["error"]
    _need(
        isinstance(error, Mapping)
        and set(error) == {"type", "message", "traceback"}
        and all(isinstance(error[key], str) for key in error),
        "BLOCKED_FAILURE_SCHEMA",
        "failure error shape",
    )
    _validate_pin(payload["producer"], "failure producer")
    _validate_pin(payload["profile"], "failure profile")
    for name in ("delivery", "receipt"):
        item = payload[name]
        _need(
            isinstance(item, Mapping)
            and set(item) == {"available", "manifest", "review", "runtimeLock"}
            and isinstance(item["available"], bool),
            "BLOCKED_FAILURE_SCHEMA",
            f"failure {name} shape",
        )
        for pin_name in ("manifest", "review", "runtimeLock"):
            if item[pin_name] is not None:
                _validate_pin(item[pin_name], f"failure {name}.{pin_name}")
    _validate_failure_phase_input_lock(payload["phaseInputLock"])
    command = payload["command"]
    _need(
        isinstance(command, Mapping)
        and set(command)
        == {"cwd", "intendedArgv", "actualArgv", "environmentAllowList", "dockerArgv", "sparkSubmitArgv"}
        and isinstance(command["cwd"], str)
        and Path(str(command["cwd"])).is_absolute()
        and all(
            isinstance(command[key], list)
            and all(isinstance(value, str) for value in command[key])
            for key in ("intendedArgv", "actualArgv", "environmentAllowList", "dockerArgv", "sparkSubmitArgv")
        ),
        "BLOCKED_FAILURE_SCHEMA",
        "failure command shape",
    )
    logs = payload["logs"]
    _need(
        isinstance(logs, Mapping)
        and set(logs)
        == {"stdout", "stderr", "runnerLog", "stdoutSha256", "stderrSha256", "runnerLogSha256"}
        and all(isinstance(logs[key], str) for key in ("stdout", "stderr", "runnerLog"))
        and all(_is_sha(logs[key]) for key in ("stdoutSha256", "stderrSha256", "runnerLogSha256")),
        "BLOCKED_FAILURE_SCHEMA",
        "failure logs shape",
    )
    resource = payload["resource"]
    _need(
        isinstance(resource, Mapping)
        and set(resource)
        == {"status", "pollIntervalSeconds", "samples", "peakMemoryBytes", "lastMemoryBytes", "cgroupVersion", "events"}
        and resource["status"] in {"NOT_STARTED", "OBSERVED", "MEASUREMENT_FAILED"}
        and resource["cgroupVersion"] in {"v1", "v2", "NOT_STARTED"}
        and isinstance(resource["samples"], list)
        and isinstance(resource["events"], Mapping),
        "BLOCKED_FAILURE_SCHEMA",
        "failure resource shape",
    )
    container = payload["container"]
    _need(
        isinstance(container, Mapping)
        and set(container)
        == {"created", "containerId", "preStopInspect", "finalInspect", "exitCode", "oomKilled"}
        and container["created"] is False
        and all(container[key] is None for key in set(container) - {"created"}),
        "BLOCKED_FAILURE_SCHEMA",
        "failure container shape",
    )
    timeout = payload["timeout"]
    _need(
        isinstance(timeout, Mapping)
        and set(timeout)
        == {"limitSeconds", "timedOut", "lastSampleBeforeStop", "inspectBeforeStop", "stopRequestedAt"}
        and type(timeout["limitSeconds"]) is int
        and timeout["limitSeconds"] >= 0
        and timeout["timedOut"] is False,
        "BLOCKED_FAILURE_SCHEMA",
        "failure timeout shape",
    )
    cleanup = payload["cleanup"]
    _need(
        isinstance(cleanup, Mapping)
        and set(cleanup)
        == {"monitorStopped", "containerStopped", "containerRemoved", "tempRemoved", "scratchRemoved", "errors", "complete"}
        and all(isinstance(cleanup[key], bool) for key in set(cleanup) - {"errors"})
        and isinstance(cleanup["errors"], list)
        and all(isinstance(item, str) for item in cleanup["errors"]),
        "BLOCKED_FAILURE_SCHEMA",
        "failure cleanup shape",
    )
    output = payload["outputState"]
    _need(
        isinstance(output, Mapping)
        and set(output)
        == {"finalPublished", "reviewPublished", "modelWritten", "predictionsWritten", "nextPhaseBlocked"}
        and all(isinstance(output[key], bool) for key in output),
        "BLOCKED_FAILURE_SCHEMA",
        "failure output state shape",
    )
    _need(
        _is_sha(payload["dependencyFingerprintBefore"])
        and _is_sha(payload["dependencyFingerprintAfter"])
        and payload["readyForService"] is False
        and payload["deploymentAuthorized"] is False
        and isinstance(payload["phaseInputLock"], Mapping)
        and isinstance(payload["ancestorClosure"], Mapping)
        and isinstance(payload["authorizationEvidence"], list)
        and isinstance(payload["hostGate"], Mapping)
        and isinstance(payload["namespaceCensus"], Mapping)
        and isinstance(payload["publication"], Mapping),
        "BLOCKED_FAILURE_SCHEMA",
        "failure closure fields",
    )
    _reject_numbers(payload)


def _compose_delivery_failure(
    roots: Roots,
    contract: DeliveryContract | None,
    profile_path: Path,
    implementation_review_path: Path,
    expected_implementation_review_sha256: str,
    lease: object,
    error: Exception,
    failure_stage: str,
    failure_kind: str,
    dependency_fingerprint: str,
    started_at: str,
    started_monotonic: float,
    intended_argv: Sequence[str],
    namespace_before: Sequence[str],
) -> dict[str, object]:
    state = _failure_dependency_state(
        roots, profile_path, implementation_review_path, expected_implementation_review_sha256
    )
    phase_input_lock = _failure_phase_input_lock(
        roots, contract, profile_path, implementation_review_path,
        expected_implementation_review_sha256,
    )
    empty_digest = hashlib.sha256(b"").hexdigest()
    payload: dict[str, object] = {
        "schemaVersion": PHASE_FAILURE_SCHEMA,
        "status": "FAILED",
        "runId": RUN_ID,
        "profileId": PROFILE_ID,
        "phase": "delivery-build",
        "attemptOrdinal": 1,
        "startedAt": started_at,
        "failedAt": _utc_now(),
        "elapsedSeconds": max(0.0, time.monotonic() - started_monotonic),
        "failureStage": failure_stage,
        "failureKind": failure_kind,
        "error": {
            "type": type(error).__name__,
            "message": str(error),
            "traceback": "".join(traceback.format_exception(type(error), error, error.__traceback__)),
        },
        "producer": state["producer"],
        "profile": state["profile"],
        "delivery": {
            "available": False,
            "manifest": None,
            "review": None,
            "runtimeLock": state["runtimeLock"],
        },
        "receipt": {"available": False, "manifest": None, "review": None, "runtimeLock": None},
        "phaseInputLock": phase_input_lock,
        "ancestorClosure": _failure_ancestor_closure(contract),
        "authorizationEvidence": _authorization_evidence(
            roots, implementation_review_path, state
        ),
        "hostGate": {"status": "NOT_STARTED", "unknownFields": []},
        "command": {
            "cwd": str(Path.cwd().resolve()),
            "intendedArgv": list(intended_argv),
            "actualArgv": [],
            "environmentAllowList": [],
            "dockerArgv": [],
            "sparkSubmitArgv": [],
        },
        "logs": {
            "stdout": "", "stderr": "", "runnerLog": "",
            "stdoutSha256": empty_digest, "stderrSha256": empty_digest,
            "runnerLogSha256": empty_digest,
        },
        "resource": {
            "status": "NOT_STARTED", "pollIntervalSeconds": 0, "samples": [],
            "peakMemoryBytes": None, "lastMemoryBytes": None,
            "cgroupVersion": "NOT_STARTED", "events": {},
        },
        "container": {
            "created": False, "containerId": None, "preStopInspect": None,
            "finalInspect": None, "exitCode": None, "oomKilled": None,
        },
        "timeout": {
            "limitSeconds": 0, "timedOut": False, "lastSampleBeforeStop": None,
            "inspectBeforeStop": None, "stopRequestedAt": None,
        },
        "cleanup": {
            "monitorStopped": True, "containerStopped": True, "containerRemoved": True,
            "tempRemoved": True, "scratchRemoved": True, "errors": [], "complete": True,
        },
        "namespaceCensus": {
            "before": list(namespace_before),
            "beforePublication": _direct_children(roots.wsl_evidence),
            "requiredAfterPublication": sorted((*DELIVERY_COMPLETED_CHILDREN, DELIVERY_FAILURE_NAME)),
        },
        "outputState": {
            "finalPublished": False, "reviewPublished": False, "modelWritten": False,
            "predictionsWritten": False, "nextPhaseBlocked": True,
        },
        "dependencyFingerprintBefore": dependency_fingerprint,
        "dependencyFingerprintAfter": dependency_fingerprint,
        "publication": lease.publication_evidence,
        "readyForService": False,
        "deploymentAuthorized": False,
    }
    _validate_phase_failure(payload)
    return payload


def _publish_delivery_failure(
    roots: Roots,
    contract: DeliveryContract | None,
    profile_path: Path,
    implementation_review_path: Path,
    expected_implementation_review_sha256: str,
    manifest_path: Path,
    error: Exception,
    failure_stage: str,
    failure_kind: str,
    started_at: str,
    started_monotonic: float,
    intended_argv: Sequence[str],
    *,
    lease: object | None = None,
    dependency_fingerprint: str | None = None,
    rehash_callback: Callable[[], str] | None = None,
):
    publication = _load_publication_module()
    namespace_before = _direct_children(roots.wsl_evidence)
    if lease is None:
        fingerprint = _failure_state_fingerprint(
            roots, profile_path, implementation_review_path,
            expected_implementation_review_sha256, contract,
        )
        lease = publication.acquire_publication(
            "PRODUCER",
            "delivery-build",
            manifest_path,
            manifest_path.with_name(DELIVERY_FAILURE_NAME),
            publication.ExpectedNamespace(DELIVERY_COMPLETED_CHILDREN, fingerprint),
        )

        def current_fingerprint() -> str:
            return _failure_state_fingerprint(
                roots, profile_path, implementation_review_path,
                expected_implementation_review_sha256, contract,
            )

        dependency_fingerprint = fingerprint
        rehash_callback = current_fingerprint
    _need(dependency_fingerprint is not None and rehash_callback is not None,
          "BLOCKED_FAILURE_SCHEMA", "failure publication dependency is absent")
    payload = _compose_delivery_failure(
        roots, contract, profile_path, implementation_review_path,
        expected_implementation_review_sha256, lease, error, failure_stage, failure_kind,
        dependency_fingerprint, started_at, started_monotonic, intended_argv, namespace_before,
    )
    pin = publication.publish_handled_failure(
        lease, payload, dependency_fingerprint, rehash_callback
    )
    post = rehash_callback()
    publication.release_verified_claim(lease, pin, post)
    return pin, payload


def _raise_published_failure(error: Exception, pin: object) -> None:
    raise DeliveryError(
        "FAILED_PUBLISHED", f"delivery failure published at {pin.path}: {error}"
    ) from error


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--standalone-root", type=Path, default=Path("/mnt/c/higher/projects/FEELM-standalone"))
    parser.add_argument("--team-root", type=Path, default=Path("/mnt/c/higher/projects/S15P21E106"))
    parser.add_argument("--evidence-root", type=Path, default=Path("/home/kingc/.feelm-r4/evidence/service-v1-pretraining-20260913"))
    parser.add_argument("--delivery-root", type=Path, default=Path("/home/kingc/.feelm-r4/delivery") / RUN_ID)
    parser.add_argument("--implementation-review", type=Path, required=True)
    parser.add_argument("--expected-implementation-review-sha256", required=True)
    parser.add_argument("--completed-child", action="append")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    started_at = _utc_now()
    started_monotonic = time.monotonic()
    arguments = parse_args(argv)
    intended_argv = tuple(sys.argv if argv is None else (str(Path(__file__)), *argv))
    roots = Roots(
        arguments.standalone_root,
        arguments.team_root,
        arguments.evidence_root,
        arguments.delivery_root,
    )
    profile_path = roots.standalone / PROFILE_PATH
    manifest_path = roots.wsl_evidence / DELIVERY_NAME
    contract: DeliveryContract | None = None
    try:
        if arguments.completed_child is not None:
            _need(
                tuple(sorted(arguments.completed_child)) == DELIVERY_COMPLETED_CHILDREN
                and len(arguments.completed_child) == len(DELIVERY_COMPLETED_CHILDREN),
                "BLOCKED_NAMESPACE",
                "CLI completed-child values differ from the frozen predecessor set",
            )
        contract = default_contract(
            roots,
            arguments.implementation_review,
            arguments.expected_implementation_review_sha256,
        )
    except Exception as error:
        pin, _failure = _publish_delivery_failure(
            roots, contract, profile_path, arguments.implementation_review,
            arguments.expected_implementation_review_sha256, manifest_path, error,
            "PRE_CONTAINER", "AUDIT" if isinstance(error, DeliveryError)
            and error.code == "BLOCKED_GATE" else "CONTRACT",
            started_at, started_monotonic, intended_argv,
        )
        _raise_published_failure(error, pin)
    try:
        _prepare_docker_id(roots.wsl_delivery)
    except Exception as error:
        pin, _failure = _publish_delivery_failure(
            roots, contract, profile_path, arguments.implementation_review,
            arguments.expected_implementation_review_sha256, manifest_path, error,
            "PRE_CONTAINER", "IO", started_at, started_monotonic, intended_argv,
        )
        _raise_published_failure(error, pin)
    pin, _ = build_and_publish(
        roots,
        contract,
        profile_path,
        arguments.implementation_review,
        arguments.expected_implementation_review_sha256,
        manifest_path,
        DELIVERY_COMPLETED_CHILDREN,
    )
    print(json.dumps(pin.record, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
