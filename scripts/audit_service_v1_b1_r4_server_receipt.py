"""Independently audit and publish the service-v1 B1 r4 server receipt review.

This auditor never loads a Docker archive and never installs packages.  It
reopens the receipt, every regular delivery destination, the complete sealed
venv, and the live Docker image identity before it grants public preflight.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import importlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import traceback
from typing import Any, Callable, Iterable, Mapping, Sequence
import zipfile


RUN_ID = "b1-gbt120-s339-v1-r4-server5c20g-t28800"
PROFILE_ID = "ec2-8vcpu32g-local5-fit20g-t28800-v1"
RECEIPT_NAME = f"{RUN_ID}-server-receipt"
RECEIPT_REVIEW_NAME = f"{RECEIPT_NAME}-result-review.json"
RECEIPT_REVIEW_FAILURE_NAME = f"{RECEIPT_NAME}-result-review-failure.json"
DELIVERY_MANIFEST_NAME = f"{RUN_ID}-delivery-manifest.json"
DELIVERY_REVIEW_NAME = f"{RUN_ID}-delivery-manifest-result-review.json"
DESIGN_REVIEW_NAME = f"{RUN_ID}-design-result-review.json"
IMPLEMENTATION_REVIEW_NAME = f"{RUN_ID}-implementation-result-review.json"
AUDITOR_RELATIVE = Path("scripts/audit_service_v1_b1_r4_server_receipt.py")
PUBLICATION_RELATIVE = Path("scripts/service_v1_b1_r4_publication.py")
OUTPUT_RELATIVE = Path("outputs/recommendation-evidence/service-v1-pretraining-20260913")

EXPECTED_IMAGE_ID = "sha256:de611d62db982bb833b0737a7ddd72a7cd49ab1a7dc9128669cdabae39f46df8"
EXPECTED_IMAGE_UNPACKED_BYTES = 936_463_936
EXPECTED_ARCHIVE_SHA256 = "c5567016731df7a11af6c07cf338f8bf93e992a8904ccebd47cfb3a6b0b0f7be"
RUNTIME_PROBE_SCRIPT_SHA256 = "b1d2200fa3821298694451c07b372d0065eb4003f2a63a91accf381dd9d70e02"
WHEELHOUSE_SET_SHA256 = "af87e5e9b660085d45ab5562dc76fd3f0f617d1d069108eb8c3677b66ad0fa5c"
EXPECTED_DOCKER_CONTRACT_BYTES = (
    b'{"imageId":"sha256:de611d62db982bb833b0737a7ddd72a7cd49ab1a7dc9128669cdabae39f46df8",'
    b'"imageUnpackedSizeBytes":936463936,"mode":"ARCHIVE"}\n'
)
READ_CHUNK_BYTES = 1024 * 1024
MAX_JSON_BYTES = 64 * 1024 * 1024
MAX_TEXT_BYTES = 16 * 1024 * 1024
HEX64 = re.compile(r"[0-9a-f]{64}")
PIN_KEYS = frozenset({"path", "bytes", "sha256"})
OWNING_RECORD_KEYS = frozenset(
    {"recordId", "logicalPath", "sourceRoot", "destinationRelativePath", "bytes", "sha256", "kind"}
)
DESTINATION_RECORD_KEYS = frozenset(
    {"recordId", "logicalPath", "absolutePath", "bytes", "sha256", "stDev", "stIno", "nlink", "kind"}
)
INVENTORY_KEYS = frozenset(
    {"schemaVersion", "records", "expectedDeliverySetSha256", "actualDeliverySetSha256", "recordSetSha256",
     "missingCount", "extraCount", "specialFileCount", "hardLinkAliasCount"}
)
HOST_KEYS = frozenset(
    {"unameMachine", "logicalCpu", "memTotalBytes", "memAvailableBytes", "dockerServerVersion",
     "cgroupVersion", "cgroupControllers", "cgroupPeakReadable", "scratchFreeBytes", "outputFreeBytes",
     "scratchFreeInodes", "outputFreeInodes", "outputDevice", "scratchDevice", "renameNoReplaceProbe",
     "supervisorKind", "observedAt"}
)
MANIFEST_KEYS = frozenset(
    {"schemaVersion", "status", "runId", "profileId", "createdAt", "producer", "delivery", "actualRoots",
     "destinationInventory", "hostProbe", "maintenanceReservations", "imageLoad", "hostRuntime", "filesystem",
     "rehashSummary", "authorization", "publication"}
)
DELIVERY_KEYS = frozenset(
    {"schemaVersion", "status", "runId", "profileId", "createdAt", "producer", "sourceRoots",
     "destinationLayout", "implementationReview", "modelInputs", "scoreInputs", "workerRuntime",
     "evaluationInputs", "controlAndImplementation", "ancestorEvidence", "crossGroupReferences",
     "crossGroupReferencesSha256", "recordGroupDigests", "deliverySetSha256", "hostRequirements",
     "authorization", "publication"}
)
REVIEW_KEYS = frozenset(
    {"schemaVersion", "status", "runId", "profileId", "createdAt", "reviewer", "target", "checks",
     "decision", "dependencyFingerprint", "publication"}
)
REVIEW_FAILURE_KEYS = frozenset(
    {"schemaVersion", "status", "runId", "profileId", "phase", "createdAt", "auditor", "target", "error",
     "dependencyFingerprintBefore", "dependencyFingerprintAfter", "namespaceCensus", "passReviewPublished",
     "deploymentAuthorized", "publication"}
)
PUBLICATION_KEYS = frozenset(
    {"schemaVersion", "mode", "role", "finalPath", "failurePath", "claimPath", "tempPath", "token",
     "claimStDev", "claimStIno", "filesystemType", "publisher", "dependencyFingerprintAtAcquire",
     "dependencyFingerprintBeforeRename", "renameNoReplaceProbe", "requiredPostconditions"}
)
RESERVATION_KEYS = frozenset(
    {"schemaVersion", "status", "reservationId", "hostIdentity", "phase", "startsAt", "endsAt",
     "dockerRestartScheduled", "hostRebootScheduled", "issuedBy", "createdAt"}
)
VENV_KEYS = frozenset(
    {"schemaVersion", "root", "records", "regularFileCount", "symlinkCount", "specialFileCount",
     "hardLinkAliasCount", "recordSetSha256"}
)
VENV_RECORD_KEYS = frozenset({"path", "bytes", "sha256", "mode", "stDev", "stIno", "nlink"})
RUNTIME_KEYS = frozenset(
    {"schemaVersion", "createdAt", "bootstrapInterpreter", "absoluteInterpreter", "interpreterBytes",
     "interpreterSha256", "pythonImplementation", "pythonVersion", "environment", "requirementsLock",
     "wheelhouseManifest", "venvInventory", "packages", "imports", "evaluationFixture", "runtimeSetSha256"}
)
IMAGE_KEYS = frozenset(
    {"schemaVersion", "archive", "command", "startedAt", "completedAt", "exitCode", "stdout", "stderr",
     "beforeImageCensus", "afterImageCensus", "loadInvocationCount", "expectedImageId", "actualImageId",
     "expectedUnpackedSizeBytes", "actualUnpackedSizeBytes", "status"}
)
RESERVATION_FILES = {
    "preflightDry": ("preflight-dry", "preflight-dry.json", 16_200),
    "preflightFull": ("preflight-full", "preflight-full.json", 9_000),
    "fit": ("fit", "fit.json", 32_400),
    "score": ("score", "score.json", 9_000),
    "calibrateSelect": ("calibrate-select", "calibrate-select.json", 16_200),
    "confirmation": ("confirmation", "confirmation.json", 16_200),
}
EXPECTED_RECEIPT_FILES = frozenset(
    {"destination-inventory.json", "host-probe.json", "image-load.json", "manifest.json",
     "runtime/venv-file-inventory.json", "runtime/service-v1-b1-r4-host-runtime-lock.json",
     *("maintenance-reservations/" + item[1] for item in RESERVATION_FILES.values())}
)
EXPECTED_RECEIPT_DIRECTORIES = frozenset({"maintenance-reservations", "runtime"})
OWNING_GROUPS = ("modelInputs", "scoreInputs", "workerRuntime", "evaluationInputs",
                 "controlAndImplementation", "ancestorEvidence")


class ReceiptAuditError(RuntimeError):
    """A receipt condition that blocks public preflight."""


def require(condition: Any, message: str) -> None:
    if not condition:
        raise ReceiptAuditError(message)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_utc(value: Any, field: str) -> dt.datetime:
    require(isinstance(value, str) and value.endswith("Z"), f"{field} must be RFC3339 UTC")
    try:
        parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ReceiptAuditError(f"{field} must be RFC3339 UTC") from error
    require(parsed.tzinfo is not None, f"{field} must be timezone-aware")
    return parsed.astimezone(dt.timezone.utc)


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def _duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        require(key not in value, f"duplicate JSON key: {key}")
        value[key] = child
    return value


def _constant(token: str) -> Any:
    raise ReceiptAuditError(f"non-finite JSON value: {token}")


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


def safe_regular(path: Path) -> os.stat_result:
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


def read_bytes(path: Path, *, max_bytes: int = MAX_TEXT_BYTES) -> tuple[bytes, os.stat_result]:
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


def pin_file(path: Path, pin_path: str | None = None) -> dict[str, Any]:
    size, digest, _info = stream_regular_sha256(path)
    return {"path": pin_path if pin_path is not None else str(path.resolve()),
            "bytes": size, "sha256": digest}


def load_json(path: Path) -> tuple[dict[str, Any], bytes]:
    raw, _info = read_bytes(path, max_bytes=MAX_JSON_BYTES)
    require(not raw.startswith(b"\xef\xbb\xbf"), f"JSON BOM forbidden: {path}")
    require(raw.endswith(b"\n") and b"\r" not in raw, f"JSON must be UTF-8 LF: {path}")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_duplicate_pairs, parse_constant=_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReceiptAuditError(f"invalid JSON: {path}: {error}") from error
    require(isinstance(value, dict), f"JSON object required: {path}")
    return value, raw


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
            value["renameNoReplaceProbe"] is True, f"{field} identity/mode drift")
    for key in ("finalPath", "failurePath", "claimPath", "tempPath"):
        require(isinstance(value[key], str) and Path(value[key]).is_absolute(), f"{field}.{key} invalid")
    require(isinstance(value["token"], str) and value["token"] and
            all(type(value[key]) is int and value[key] >= 0 for key in ("claimStDev", "claimStIno")) and
            value["filesystemType"] in {"ext2/ext3", "xfs"}, f"{field} owner/filesystem drift")
    validate_pin(value["publisher"], field + ".publisher")
    for key in ("dependencyFingerprintAtAcquire", "dependencyFingerprintBeforeRename"):
        require(isinstance(value[key], str) and HEX64.fullmatch(value[key]), f"{field}.{key} invalid")
    require(value["dependencyFingerprintAtAcquire"] == value["dependencyFingerprintBeforeRename"],
            f"{field} dependency acquisition/rename drift")
    require(value["requiredPostconditions"] == {"publishedBytesRehashRequired": True,
                                                "fileAndParentFsyncRequired": True,
                                                "dependencyFingerprintStableRequired": True,
                                                "claimIdentityMatchRequired": True,
                                                "claimRemovalRequired": True},
            f"{field} postcondition drift")


def pin_matches(path: Path, expected: Any, field: str) -> dict[str, Any]:
    pin = validate_pin(expected, field)
    current = pin_file(path, str(path.resolve()))
    require((current["bytes"], current["sha256"]) == (pin["bytes"], pin["sha256"]),
            f"{field} current bytes drift")
    require(Path(pin["path"]).is_absolute() and Path(pin["path"]).resolve() == path.resolve(),
            f"{field}.path is noncanonical")
    return current


def canonical_record_set_sha256(records: Iterable[Mapping[str, Any]], *, path_key: str = "path") -> str:
    rows: list[tuple[str, int, str]] = []
    seen: set[str] = set()
    for record in records:
        path = record.get(path_key)
        size = record.get("bytes")
        digest = record.get("sha256")
        require(isinstance(path, str) and path and "\0" not in path and path not in seen,
                f"invalid or duplicate record path: {path}")
        require(type(size) is int and size >= 0, f"invalid record bytes: {path}")
        require(isinstance(digest, str) and HEX64.fullmatch(digest) is not None,
                f"invalid record SHA-256: {path}")
        seen.add(path)
        rows.append((path, size, digest))
    raw = b"".join(path.encode("utf-8") + b"\0" + str(size).encode("ascii") + b"\0" +
                   digest.encode("ascii") + b"\n" for path, size, digest in sorted(rows))
    return sha256_bytes(raw)


def validate_host(probe: Mapping[str, Any]) -> None:
    require(set(probe) == HOST_KEYS, "host probe shape drift")
    require(probe["unameMachine"] == "x86_64", "host architecture gate failed")
    minima = {"logicalCpu": 8, "memTotalBytes": 32_000_000_000,
              "memAvailableBytes": 25_769_803_776, "scratchFreeBytes": 85_899_345_920,
              "outputFreeBytes": 21_474_836_480, "scratchFreeInodes": 100_000,
              "outputFreeInodes": 100_000}
    for key, minimum in minima.items():
        require(type(probe[key]) is int and probe[key] >= minimum, f"host probe gate failed: {key}")
    require(all(type(probe[key]) is int and probe[key] >= 0 for key in ("outputDevice", "scratchDevice")),
            "host device field drift")
    require(isinstance(probe["dockerServerVersion"], str) and probe["dockerServerVersion"].strip(),
            "Docker server unavailable")
    require(probe["cgroupVersion"] == "v2" and isinstance(probe["cgroupControllers"], list) and
            probe["cgroupControllers"] == sorted(probe["cgroupControllers"]) and
            set(probe["cgroupControllers"]) >= {"cpu", "memory", "pids"} and
            probe["cgroupPeakReadable"] is True, "cgroup-v2 probe failed")
    require(probe["renameNoReplaceProbe"] is True and probe["supervisorKind"] == "systemd",
            "filesystem/supervisor host probe failed")
    parse_utc(probe["observedAt"], "hostProbe.observedAt")


def validate_reservation(value: Mapping[str, Any], phase: str, required_seconds: int) -> None:
    require(set(value) == RESERVATION_KEYS, f"{phase} reservation shape drift")
    require(value["schemaVersion"] == "feelm-service-v1-b1-r4-maintenance-reservation/2" and
            value["status"] == "ACTIVE" and value["phase"] == phase,
            f"{phase} reservation identity/status drift")
    try:
        parsed_uuid = __import__("uuid").UUID(str(value["reservationId"]))
    except (ValueError, AttributeError) as error:
        raise ReceiptAuditError(f"{phase} reservationId invalid") from error
    require(str(parsed_uuid) == str(value["reservationId"]).lower(), f"{phase} reservationId noncanonical")
    require(all(isinstance(value[key], str) and value[key].strip() for key in ("hostIdentity", "issuedBy")),
            f"{phase} reservation issuer/host invalid")
    starts = parse_utc(value["startsAt"], f"{phase}.startsAt")
    ends = parse_utc(value["endsAt"], f"{phase}.endsAt")
    parse_utc(value["createdAt"], f"{phase}.createdAt")
    require((ends - starts).total_seconds() >= required_seconds, f"{phase} reservation too short")
    require(value["dockerRestartScheduled"] is False and value["hostRebootScheduled"] is False,
            f"{phase} scheduled maintenance conflict")


def receipt_file_census(receipt_root: Path) -> dict[str, tuple[int, int]]:
    require(receipt_root.is_dir() and not receipt_root.is_symlink(), "receipt root must be an unlinked directory")
    records: dict[str, tuple[int, int]] = {}
    directories: set[str] = set()
    identities: set[tuple[int, int]] = set()
    for path in sorted(receipt_root.rglob("*"), key=lambda item: item.relative_to(receipt_root).as_posix()):
        info = os.lstat(path)
        relative = path.relative_to(receipt_root).as_posix()
        require(not stat.S_ISLNK(info.st_mode), f"receipt symlink forbidden: {relative}")
        require(stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode),
                f"receipt special file forbidden: {relative}")
        if stat.S_ISDIR(info.st_mode):
            directories.add(relative)
            continue
        require(info.st_nlink == 1, f"receipt hard-link alias forbidden: {relative}")
        identity = (info.st_dev, info.st_ino)
        require(identity not in identities, f"receipt duplicate inode: {relative}")
        identities.add(identity)
        records[relative] = identity
    require(set(records) == EXPECTED_RECEIPT_FILES,
            f"receipt file inventory drift: {sorted(set(records) ^ EXPECTED_RECEIPT_FILES)}")
    require(directories == EXPECTED_RECEIPT_DIRECTORIES,
            f"receipt directory inventory drift: {sorted(directories ^ EXPECTED_RECEIPT_DIRECTORIES)}")
    return records


def expected_destination_bindings(delivery_manifest: Mapping[str, Any], standalone: Path,
                                  team: Path, delivery: Path) -> dict[str, dict[str, Any]]:
    layout = delivery_manifest.get("destinationLayout")
    require(layout == {"siblingRootsRequired": True, "standaloneDirectoryName": "FEELM-standalone",
                       "teamDirectoryName": "S15P21E106", "deliveryDirectoryName": RUN_ID,
                       "preserveRelativePaths": True}, "delivery destinationLayout drift")
    roots = {"FEELM-standalone": standalone, "S15P21E106": team, RUN_ID: delivery}
    bindings: dict[str, dict[str, Any]] = {}
    for group in OWNING_GROUPS:
        raw_group = delivery_manifest[group]
        raw_records = raw_group.get("records") if group == "ancestorEvidence" and isinstance(raw_group, Mapping) else raw_group
        require(isinstance(raw_records, list), f"delivery owning group invalid: {group}")
        for index, raw_record in enumerate(raw_records):
            require(isinstance(raw_record, Mapping) and set(raw_record) == OWNING_RECORD_KEYS,
                    f"delivery owning record shape drift: {group}[{index}]")
            record = dict(raw_record)
            require(record["sourceRoot"] in {"standalone", "team", "wslEvidence", "wslDelivery", "virtual"} and
                    record["kind"] in {"regular-file", "docker-image-contract"} and
                    isinstance(record["logicalPath"], str) and isinstance(record["destinationRelativePath"], str) and
                    type(record["bytes"]) is int and record["bytes"] >= 0 and
                    isinstance(record["sha256"], str) and HEX64.fullmatch(record["sha256"]),
                    f"delivery owning record value drift: {group}[{index}]")
            expected_record_id = sha256_bytes(
                str(record["sourceRoot"]).encode("utf-8") + b"\0" + str(record["logicalPath"]).encode("utf-8"))
            require(record["recordId"] == expected_record_id, "delivery owning recordId drift")
            relative = PurePosixPath(record["destinationRelativePath"])
            require(relative.parts and not relative.is_absolute() and ".." not in relative.parts and
                    "." not in relative.parts, "delivery destination traversal")
            if relative.parts[0] in roots:
                base, remainder = roots[relative.parts[0]], relative.parts[1:]
            else:
                source_root = record["sourceRoot"]
                base = standalone if source_root == "standalone" else team if source_root == "team" else delivery
                remainder = relative.parts
            require(remainder, "delivery destination cannot name a root")
            lexical = _lexical_absolute(base.joinpath(*remainder))
            require(record["logicalPath"] not in bindings, "delivery logicalPath overlap")
            bindings[record["logicalPath"]] = {"recordId": record["recordId"], "logicalPath": record["logicalPath"],
                                               "absolutePath": str(lexical), "bytes": record["bytes"],
                                               "sha256": record["sha256"], "kind": record["kind"]}
    return bindings


def validate_destination_inventory(inventory: Mapping[str, Any], delivery_root: Path | None = None,
                                   expected_bindings: Mapping[str, Mapping[str, Any]] | None = None
                                   ) -> list[dict[str, Any]]:
    require(set(inventory) == INVENTORY_KEYS, "destination inventory shape drift")
    require(inventory["schemaVersion"] == "feelm-service-v1-b1-r4-destination-inventory/1",
            "destination inventory schema drift")
    require(all(type(inventory[key]) is int and inventory[key] == 0 for key in
                ("missingCount", "extraCount", "specialFileCount", "hardLinkAliasCount")),
            "destination inventory failure count is nonzero")
    for key in ("expectedDeliverySetSha256", "actualDeliverySetSha256", "recordSetSha256"):
        require(isinstance(inventory[key], str) and HEX64.fullmatch(inventory[key]) is not None,
                f"destination inventory digest invalid: {key}")
    require(inventory["expectedDeliverySetSha256"] == inventory["actualDeliverySetSha256"],
            "destination delivery-set digest mismatch")
    raw_records = inventory["records"]
    require(isinstance(raw_records, list) and raw_records, "destination inventory records missing")
    records: list[dict[str, Any]] = []
    identities: set[tuple[int, int]] = set()
    labels = 0
    for index, record in enumerate(raw_records):
        require(isinstance(record, Mapping) and set(record) == DESTINATION_RECORD_KEYS,
                f"destination record shape drift: {index}")
        value = dict(record)
        for key in ("recordId", "logicalPath", "absolutePath", "sha256", "kind"):
            require(isinstance(value[key], str) and value[key], f"destination record field invalid: {key}")
        require(HEX64.fullmatch(value["recordId"]) is not None and HEX64.fullmatch(value["sha256"]) is not None,
                "destination record digest invalid")
        require(value["kind"] in {"regular-file", "docker-image-contract"}, "destination record kind invalid")
        require(all(type(value[key]) is int and value[key] >= 0 for key in
                    ("bytes", "stDev", "stIno", "nlink")), "destination record count/type invalid")
        require(Path(value["absolutePath"]).is_absolute(), "destination absolutePath is not absolute")
        if expected_bindings is not None:
            expected = expected_bindings.get(value["logicalPath"])
            require(expected is not None and all(value[key] == expected[key] for key in
                                                  ("recordId", "logicalPath", "absolutePath", "bytes",
                                                   "sha256", "kind")),
                    f"destination lexical/root binding drift: {value['logicalPath']}")
        if value["kind"] == "regular-file":
            lexical = _lexical_absolute(Path(value["absolutePath"]))
            require(lexical.resolve(strict=True) == lexical,
                    f"destination lexical symlink binding drift: {value['logicalPath']}")
            current_size, current_sha256, current = stream_regular_sha256(Path(value["absolutePath"]))
            require((current_size, current_sha256, current.st_dev, current.st_ino, current.st_nlink) ==
                    (value["bytes"], value["sha256"], value["stDev"], value["stIno"], value["nlink"]),
                    f"destination current rehash drift: {value['logicalPath']}")
            identity = (current.st_dev, current.st_ino)
            require(identity not in identities, "destination duplicate inode")
            identities.add(identity)
        else:
            require((value["stDev"], value["stIno"], value["nlink"]) == (0, 0, 0),
                    "virtual Docker contract physical fields drift")
        if PurePosixPath(value["logicalPath"]).name == "labels.parquet":
            labels += 1
        records.append(value)
    require(labels == 1, "exactly one labels.parquet byte record required")
    if expected_bindings is not None:
        require(set(expected_bindings) == {row["logicalPath"] for row in records},
                "destination inventory/manifest cardinality drift")
    docker_contracts = [row for row in records if row["kind"] == "docker-image-contract"]
    require(len(docker_contracts) == 1, "virtual Docker contract cardinality drift")
    docker_contract = docker_contracts[0]
    require(docker_contract["logicalPath"] == "runtime/docker-image-contract.json" and
            docker_contract["bytes"] == len(EXPECTED_DOCKER_CONTRACT_BYTES) and
            docker_contract["sha256"] == sha256_bytes(EXPECTED_DOCKER_CONTRACT_BYTES),
            "virtual Docker contract frozen bytes drift")
    require(records == sorted(records, key=lambda value: value["logicalPath"]),
            "destination records are not ASCII logicalPath sorted")
    require(canonical_record_set_sha256(records, path_key="logicalPath") ==
            inventory["actualDeliverySetSha256"], "destination actualDeliverySetSha256 drift")
    require(sha256_bytes(canonical_json(records)) == inventory["recordSetSha256"],
            "destination recordSetSha256 drift")
    if delivery_root is not None:
        root = delivery_root.resolve(strict=True)
        expected = {Path(row["absolutePath"]).resolve() for row in records
                    if row["kind"] == "regular-file" and root in Path(row["absolutePath"]).resolve().parents}
        observed: set[Path] = set()
        for path in root.rglob("*"):
            info = os.lstat(path)
            require(not stat.S_ISLNK(info.st_mode), f"delivery symlink forbidden: {path}")
            require(stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode),
                    f"delivery special file forbidden: {path}")
            if stat.S_ISREG(info.st_mode):
                observed.add(path.resolve())
        require(observed == expected,
                f"delivery destination census drift: {sorted(str(path) for path in observed ^ expected)}")
    return records


def validate_census(census: Any, field: str) -> None:
    require(isinstance(census, list), f"{field} must be an array")
    image_ids: list[str] = []
    for index, row in enumerate(census):
        require(isinstance(row, Mapping) and set(row) == {"imageId", "repoTags", "repoDigests", "sizeBytes"},
                f"{field}[{index}] shape drift")
        require(isinstance(row["imageId"], str) and row["imageId"].startswith("sha256:"),
                f"{field}[{index}].imageId invalid")
        for key in ("repoTags", "repoDigests"):
            require(isinstance(row[key], list) and all(isinstance(value, str) for value in row[key]) and
                    row[key] == sorted(row[key]), f"{field}[{index}].{key} sort/type drift")
        require(type(row["sizeBytes"]) is int and row["sizeBytes"] >= 0, f"{field}[{index}].sizeBytes invalid")
        image_ids.append(row["imageId"])
    require(image_ids == sorted(image_ids) and len(image_ids) == len(set(image_ids)), f"{field} image sort/overlap drift")


def _command_result(result: Any, argv: Sequence[str]) -> tuple[int, str, str]:
    code = getattr(result, "returncode", getattr(result, "exit_code", None))
    stdout = getattr(result, "stdout", None)
    stderr = getattr(result, "stderr", None)
    require(type(code) is int and isinstance(stdout, str) and isinstance(stderr, str),
            f"invalid command result: {list(argv)}")
    return code, stdout, stderr


CommandRunner = Callable[..., Any]


def run_command(argv: Sequence[str], *, env: Mapping[str, str] | None = None,
                timeout: float = 120.0) -> Any:
    return subprocess.run([str(value) for value in argv], capture_output=True, text=True, encoding="utf-8",
                          errors="strict", check=False, env=None if env is None else dict(env), timeout=timeout)


def invoke(runner: CommandRunner, argv: Sequence[str], *, env: Mapping[str, str] | None = None,
           timeout: float = 120.0) -> tuple[int, str, str]:
    try:
        result = runner([str(value) for value in argv], env=env, timeout=timeout)
    except TypeError:
        result = runner([str(value) for value in argv])
    return _command_result(result, argv)


def validate_image_load(image: Mapping[str, Any], destination_records: Sequence[Mapping[str, Any]],
                        runner: CommandRunner, *, probe_live_image: bool) -> None:
    require(set(image) == IMAGE_KEYS, "image-load shape drift")
    require(image["schemaVersion"] == "feelm-service-v1-b1-r4-image-load/1" and image["status"] == "PASS",
            "image-load status/schema drift")
    require(type(image["exitCode"]) is int and image["exitCode"] == 0 and
            type(image["loadInvocationCount"]) is int and image["loadInvocationCount"] == 1,
            "Docker archive was not loaded exactly once")
    require(image["expectedImageId"] == EXPECTED_IMAGE_ID and image["actualImageId"] == EXPECTED_IMAGE_ID and
            image["expectedUnpackedSizeBytes"] == EXPECTED_IMAGE_UNPACKED_BYTES and
            image["actualUnpackedSizeBytes"] == EXPECTED_IMAGE_UNPACKED_BYTES,
            "Docker loaded image identity/size drift")
    require(isinstance(image["stdout"], str) and isinstance(image["stderr"], str), "Docker load output type drift")
    parse_utc(image["startedAt"], "imageLoad.startedAt")
    parse_utc(image["completedAt"], "imageLoad.completedAt")
    require(parse_utc(image["completedAt"], "imageLoad.completedAt") >=
            parse_utc(image["startedAt"], "imageLoad.startedAt"), "Docker load timestamps reversed")
    archive = validate_pin(image["archive"], "imageLoad.archive")
    candidates = [row for row in destination_records if row["logicalPath"] == "runtime/feelm-rec046-spark-local.tar"
                  and row["kind"] == "regular-file"]
    require(len(candidates) == 1, "Docker archive destination cardinality drift")
    expected = candidates[0]
    require(archive == {"path": expected["absolutePath"], "bytes": expected["bytes"],
                        "sha256": expected["sha256"]}, "Docker archive pin drift")
    require(archive["sha256"] == EXPECTED_ARCHIVE_SHA256, "Docker archive frozen SHA drift")
    require(image["command"] == ["docker", "load", "--input", expected["absolutePath"]],
            "Docker load command drift")
    validate_census(image["beforeImageCensus"], "beforeImageCensus")
    validate_census(image["afterImageCensus"], "afterImageCensus")
    require(any(row["imageId"] == EXPECTED_IMAGE_ID and row["sizeBytes"] == EXPECTED_IMAGE_UNPACKED_BYTES
                for row in image["afterImageCensus"]), "loaded image missing from after census")
    if probe_live_image:
        argv = ["docker", "image", "inspect", EXPECTED_IMAGE_ID]
        code, stdout, _stderr = invoke(runner, argv)
        require(code == 0, "live Docker image inspect failed")
        try:
            payload = json.loads(stdout, object_pairs_hook=_duplicate_pairs, parse_constant=_constant)
        except json.JSONDecodeError as error:
            raise ReceiptAuditError("live Docker image inspect JSON invalid") from error
        require(isinstance(payload, list) and len(payload) == 1 and payload[0].get("Id") == EXPECTED_IMAGE_ID and
                payload[0].get("Size") == EXPECTED_IMAGE_UNPACKED_BYTES, "live Docker image drift")


def inventory_venv(root: Path) -> dict[str, Any]:
    canonical_root = root.resolve(strict=True)
    records: list[dict[str, Any]] = []
    identities: set[tuple[int, int]] = set()
    symlinks = specials = aliases = 0
    for path in sorted(canonical_root.rglob("*"), key=lambda item: item.relative_to(canonical_root).as_posix()):
        info = os.lstat(path)
        if stat.S_ISDIR(info.st_mode):
            require(stat.S_IMODE(info.st_mode) & 0o222 == 0, f"writable sealed venv directory: {path}")
            continue
        if stat.S_ISLNK(info.st_mode):
            symlinks += 1
            continue
        if not stat.S_ISREG(info.st_mode):
            specials += 1
            continue
        if info.st_nlink != 1 or (info.st_dev, info.st_ino) in identities:
            aliases += 1
        identities.add((info.st_dev, info.st_ino))
        require(stat.S_IMODE(info.st_mode) & 0o222 == 0, f"writable sealed venv file: {path}")
        size, digest, current = stream_regular_sha256(path)
        records.append({"path": path.relative_to(canonical_root).as_posix(), "bytes": size,
                        "sha256": digest, "mode": stat.S_IMODE(current.st_mode),
                        "stDev": current.st_dev, "stIno": current.st_ino, "nlink": current.st_nlink})
    require(symlinks == 0 and specials == 0 and aliases == 0 and records,
            "sealed venv link/special/alias/empty inventory")
    pins = [{"path": row["path"], "bytes": row["bytes"], "sha256": row["sha256"]} for row in records]
    return {"schemaVersion": "feelm-service-v1-b1-r4-venv-inventory/1", "root": str(canonical_root),
            "records": records, "regularFileCount": len(records), "symlinkCount": symlinks,
            "specialFileCount": specials, "hardLinkAliasCount": aliases,
            "recordSetSha256": canonical_record_set_sha256(pins)}


def _distribution_record_digest(venv_root: Path, package: str, version: str) -> str:
    normalized = package.replace("-", "_")
    matches = list(venv_root.glob(f"lib/python3.12/site-packages/{normalized}-{version}.dist-info/RECORD"))
    require(len(matches) == 1, f"{package} distribution RECORD cardinality drift")
    raw, _info = read_bytes(matches[0], max_bytes=MAX_TEXT_BYTES)
    site = matches[0].parent.parent
    records: list[dict[str, Any]] = []
    for row in csv.reader(io.StringIO(raw.decode("utf-8"))):
        require(bool(row), f"{package} empty RECORD row")
        relative = PurePosixPath(row[0])
        require(not relative.is_absolute(), f"{package} RECORD absolute path")
        path = site.joinpath(*relative.parts).resolve()
        require(path == venv_root.resolve() or venv_root.resolve() in path.parents,
                f"{package} RECORD escapes venv")
        if path.is_file() and not path.is_symlink():
            records.append(pin_file(path, path.resolve().relative_to(venv_root.resolve()).as_posix()))
    return canonical_record_set_sha256(records)


def validate_runtime_sources(requirements_value: Any, wheelhouse_value: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    requirements_pin = validate_pin(requirements_value, "runtime.requirementsLock")
    wheelhouse_pin = validate_pin(wheelhouse_value, "runtime.wheelhouseManifest")
    requirements = Path(requirements_pin["path"])
    manifest_path = Path(wheelhouse_pin["path"])
    pin_matches(requirements, requirements_pin, "runtime.requirementsLock")
    pin_matches(manifest_path, wheelhouse_pin, "runtime.wheelhouseManifest")
    manifest, _raw = load_json(manifest_path)
    require(set(manifest) == {"schemaVersion", "pythonVersion", "interpreterTag", "abiTag", "platformTag",
                              "files", "wheelhouseSetSha256"} and
            manifest["schemaVersion"] == "feelm-service-v1-b1-r4-wheelhouse/1" and
            (manifest["pythonVersion"], manifest["interpreterTag"], manifest["abiTag"],
             manifest["platformTag"]) == ("3.12.3", "cp312", "cp312", "manylinux_2_17_x86_64"),
            "wheelhouse manifest contract drift")
    files = manifest["files"]
    require(isinstance(files, list) and len(files) == 7, "wheelhouse must contain seven pinned wheels")
    delivery_root = manifest_path.parent.parent
    wheelhouse_root = delivery_root / "runtime/service-v1-b1-r4-wheelhouse"
    records: list[dict[str, Any]] = []
    available: dict[tuple[str, str], str] = {}
    for index, value in enumerate(files):
        declared = validate_pin(value, f"wheelhouse.files[{index}]")
        relative = PurePosixPath(declared["path"])
        require(not relative.is_absolute() and ".." not in relative.parts and
                relative.parts[:2] == ("runtime", "service-v1-b1-r4-wheelhouse"),
                "wheelhouse member path drift")
        physical = delivery_root.joinpath(*relative.parts)
        current = pin_file(physical, declared["path"])
        require(current == declared, f"wheelhouse member byte drift: {declared['path']}")
        try:
            with zipfile.ZipFile(physical) as archive:
                metadata_names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
                require(len(metadata_names) == 1, f"wheel METADATA count drift: {physical.name}")
                metadata = archive.read(metadata_names[0]).decode("utf-8")
        except (zipfile.BadZipFile, UnicodeDecodeError) as error:
            raise ReceiptAuditError(f"invalid wheel archive: {physical.name}") from error
        names = [line[6:] for line in metadata.splitlines() if line.startswith("Name: ")]
        versions = [line[9:] for line in metadata.splitlines() if line.startswith("Version: ")]
        require(len(names) == 1 and len(versions) == 1, f"wheel metadata drift: {physical.name}")
        key = (re.sub(r"[-_.]+", "-", names[0]).lower(), versions[0])
        require(key not in available, f"duplicate wheel distribution: {key}")
        available[key] = declared["sha256"]
        records.append(declared)
    actual_members = sorted(path.resolve() for path in wheelhouse_root.iterdir())
    declared_members = sorted(delivery_root.joinpath(*PurePosixPath(value["path"]).parts).resolve()
                              for value in records)
    require(actual_members == declared_members, "wheelhouse missing or extra file")
    require(canonical_record_set_sha256(records) == manifest["wheelhouseSetSha256"] == WHEELHOUSE_SET_SHA256,
            "wheelhouse set SHA drift")
    lock_raw, _lock_info = read_bytes(requirements, max_bytes=MAX_TEXT_BYTES)
    require(not lock_raw.startswith(b"\xef\xbb\xbf") and b"\r" not in lock_raw and lock_raw.endswith(b"\n"),
            "requirements lock must be UTF-8 LF")
    locked: dict[tuple[str, str], str] = {}
    pattern = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s]+) --hash=sha256:([0-9a-f]{64})$")
    for line in lock_raw.decode("utf-8").splitlines():
        match = pattern.fullmatch(line)
        require(match is not None, f"unhashed or malformed requirement: {line}")
        key = (re.sub(r"[-_.]+", "-", match.group(1)).lower(), match.group(2))
        require(key not in locked, f"duplicate requirement: {key}")
        locked[key] = match.group(3)
    require(locked == available, "requirements and wheelhouse differ")
    for key in (("numpy", "1.26.4"), ("pandas", "2.2.3"), ("pyarrow", "19.0.1")):
        require(key in locked, f"required runtime package missing: {key}")
    return requirements_pin, wheelhouse_pin


def validate_runtime(lock: Mapping[str, Any], inventory: Mapping[str, Any], receipt_root: Path,
                     runner: CommandRunner, *, probe_imports: bool, enforce_current_interpreter: bool) -> None:
    require(set(lock) == RUNTIME_KEYS and lock["schemaVersion"] == "feelm-service-v1-b1-r4-host-runtime-lock/1",
            "host runtime lock shape/schema drift")
    require(set(inventory) == VENV_KEYS and inventory["schemaVersion"] ==
            "feelm-service-v1-b1-r4-venv-inventory/1", "venv inventory shape/schema drift")
    venv_root = Path(str(inventory["root"]))
    current_inventory = inventory_venv(venv_root)
    require(current_inventory == inventory, "complete sealed venv inventory drift")
    inventory_path = receipt_root / "runtime/venv-file-inventory.json"
    pin_matches(inventory_path, lock["venvInventory"], "runtime.venvInventory")
    interpreter = Path(str(lock["absoluteInterpreter"]))
    expected_interpreter = venv_root / "bin/python3"
    require(interpreter.is_absolute() and interpreter.resolve() == expected_interpreter.resolve(),
            "sealed interpreter canonical path drift")
    interpreter_pin = pin_file(interpreter, str(interpreter.resolve()))
    require((interpreter_pin["bytes"], interpreter_pin["sha256"]) ==
            (lock["interpreterBytes"], lock["interpreterSha256"]), "sealed interpreter byte drift")
    if enforce_current_interpreter:
        require(Path(sys.executable).resolve() == interpreter.resolve(), "auditor is not running under sealed interpreter")
    require(lock["pythonImplementation"] == "CPython" and lock["pythonVersion"] == "3.12.3",
            "sealed Python identity drift")
    bootstrap = lock["bootstrapInterpreter"]
    require(isinstance(bootstrap, Mapping) and set(bootstrap) ==
            {"command", "absolutePath", "bytes", "sha256", "version"} and bootstrap["command"] == "python3" and
            bootstrap["version"] == "3.12.3", "bootstrap interpreter shape/version drift")
    bootstrap_path = Path(str(bootstrap["absolutePath"]))
    bootstrap_pin = pin_file(bootstrap_path, str(bootstrap_path.resolve()))
    require((bootstrap_pin["bytes"], bootstrap_pin["sha256"]) ==
            (bootstrap["bytes"], bootstrap["sha256"]), "bootstrap interpreter drift")
    require(lock["environment"] == {"pythonPath": "UNSET", "pythonNoUserSite": True, "isolatedMode": True,
                                    "pythonDontWriteBytecode": True, "locale": "C.UTF-8", "timezone": "UTC"},
            "sealed runtime environment drift")
    requirements_pin, wheelhouse_pin = validate_runtime_sources(lock["requirementsLock"],
                                                                lock["wheelhouseManifest"])
    expected_versions = {"numpy": "1.26.4", "pandas": "2.2.3", "pyarrow": "19.0.1"}
    packages = lock["packages"]
    require(isinstance(packages, Mapping) and set(packages) == set(expected_versions), "runtime package set drift")
    for package, version in expected_versions.items():
        value = packages[package]
        require(isinstance(value, Mapping) and set(value) ==
                {"version", "modulePath", "moduleBytes", "moduleSha256", "distributionRecordSetSha256"} and
                value["version"] == version, f"runtime package shape/version drift: {package}")
        module = Path(str(value["modulePath"]))
        module_pin = pin_file(module, str(module.resolve()))
        require((module_pin["bytes"], module_pin["sha256"]) ==
                (value["moduleBytes"], value["moduleSha256"]), f"runtime module drift: {package}")
        require(_distribution_record_digest(venv_root, package, version) == value["distributionRecordSetSha256"],
                f"runtime distribution set drift: {package}")
    imports = lock["imports"]
    require(isinstance(imports, Mapping) and set(imports) ==
            {"command", "exitCode", "stdoutSha256", "stderrSha256", "status"} and imports["status"] == "PASS" and
            imports["exitCode"] == 0 and isinstance(imports["command"], list) and
            len(imports["command"]) == 4 and imports["command"][:3] == [str(interpreter), "-I", "-c"] and
            isinstance(imports["command"][3], str) and
            sha256_bytes(imports["command"][3].encode("utf-8")) == RUNTIME_PROBE_SCRIPT_SHA256,
            "runtime import evidence drift")
    fixture = lock["evaluationFixture"]
    require(isinstance(fixture, Mapping) and set(fixture) == {"inputSha256", "outputSha256", "status"} and
            fixture["status"] == "PASS" and all(isinstance(fixture[key], str) and HEX64.fullmatch(fixture[key])
                                                for key in ("inputSha256", "outputSha256")),
            "runtime evaluation fixture drift")
    fixture_input_raw = canonical_json({"a": [1, 2, 3], "b": [0.5, 1.5, 2.5]})
    fixture_output_raw = canonical_json({"sum": 4.5, "rows": 3, "columns": ["a", "b"]})
    require(fixture["inputSha256"] == sha256_bytes(fixture_input_raw) and
            fixture["outputSha256"] == sha256_bytes(fixture_output_raw),
            "runtime evaluation fixture content drift")
    if probe_imports:
        environment = {"HOME": str(venv_root.parent / "home"), "PATH": "/usr/bin:/bin", "PYTHONNOUSERSITE": "1",
                       "PIP_CONFIG_FILE": "/dev/null", "PYTHONDONTWRITEBYTECODE": "1", "LC_ALL": "C.UTF-8",
                       "TZ": "UTC"}
        code, stdout, stderr = invoke(runner, imports["command"], env=environment, timeout=300)
        require(code == 0 and sha256_bytes(stdout.encode("utf-8")) == imports["stdoutSha256"] and
                sha256_bytes(stderr.encode("utf-8")) == imports["stderrSha256"],
                "runtime import/fixture re-probe drift")
    inventory_raw = canonical_json(inventory)
    runtime_records = [interpreter_pin, requirements_pin, wheelhouse_pin,
                       {"path": str(inventory_path.resolve()), "bytes": len(inventory_raw),
                        "sha256": sha256_bytes(inventory_raw)},
                       {"path": "runtime/evaluation-fixture-input", "bytes": len(fixture_input_raw),
                        "sha256": fixture["inputSha256"]},
                       {"path": "runtime/evaluation-fixture-output", "bytes": len(fixture_output_raw),
                        "sha256": fixture["outputSha256"]}]
    require(canonical_record_set_sha256(runtime_records) == lock["runtimeSetSha256"],
            "runtimeSetSha256 drift")


def _validate_actual_roots(value: Any, standalone: Path, team: Path, delivery: Path,
                           output_root: Path, scratch_root: Path) -> dict[str, Path]:
    require(isinstance(value, Mapping) and set(value) ==
            {"standalone", "team", "delivery", "commonParent", "outputRoot", "scratchRoot", "runtimeRoot"},
            "actualRoots shape drift")
    paths: dict[str, Path] = {}
    for key, text in value.items():
        require(isinstance(text, str) and Path(text).is_absolute(), f"actualRoots.{key} invalid")
        path = Path(text)
        if key != "runtimeRoot":
            require(path.resolve(strict=True) == path, f"actualRoots.{key} is not an exact realpath")
        else:
            require(path.resolve() == path, "actualRoots.runtimeRoot is not an exact realpath")
        paths[key] = path
    require(paths["standalone"] == standalone.resolve() and paths["team"] == team.resolve() and
            paths["delivery"] == delivery.resolve() and paths["outputRoot"] == output_root.resolve() and
            paths["scratchRoot"] == scratch_root.resolve(), "supplied/manifest actual roots differ")
    require(paths["standalone"].parent == paths["team"].parent == paths["delivery"].parent ==
            paths["commonParent"], "actual root sibling topology drift")
    require(paths["runtimeRoot"] == paths["standalone"] / ".runtime/service-v1-b1-r4",
            "actual runtime root drift")
    return paths


def _dependency_records(receipt_root: Path, standalone: Path, inventory: Mapping[str, Any],
                        venv_inventory: Mapping[str, Any]) -> list[dict[str, Any]]:
    paths = [receipt_root / relative for relative in sorted(EXPECTED_RECEIPT_FILES)]
    paths.extend([standalone / AUDITOR_RELATIVE, standalone / PUBLICATION_RELATIVE])
    paths.extend(Path(record["absolutePath"]) for record in inventory["records"] if record["kind"] == "regular-file")
    venv_root = Path(str(venv_inventory["root"]))
    paths.extend(venv_root / str(record["path"]) for record in venv_inventory["records"])
    records: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for path in paths:
        info = safe_regular(path)
        identity = (info.st_dev, info.st_ino)
        if identity in seen:
            continue
        seen.add(identity)
        records.append(pin_file(path, str(path.resolve())))
    return records


def rehash_dependencies(records: Sequence[Mapping[str, Any]]) -> str:
    current: list[dict[str, Any]] = []
    for expected in records:
        observed = pin_file(Path(str(expected["path"])), str(Path(str(expected["path"])).resolve()))
        require(observed == expected, f"receipt-review dependency drift: {expected['path']}")
        current.append(observed)
    return canonical_record_set_sha256(current)


def validate_exact_namespace(output_root: Path, expected_children: Sequence[str]) -> None:
    expected = tuple(sorted(expected_children))
    require(tuple(expected_children) == expected and len(set(expected)) == len(expected),
            "expected predecessor namespace must be unique and sorted")
    observed = tuple(sorted(child.name for child in output_root.iterdir()))
    require(observed == expected,
            f"receipt-review predecessor namespace drift: expected {expected!r}, observed {observed!r}")
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
    info = os.lstat(lexical_root)
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
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


def reviewer_dependency_snapshot(standalone: Path, delivery: Path, output_root: Path,
                                 receipt_root: Path) -> tuple[list[dict[str, Any]], str]:
    records_by_path: dict[str, dict[str, Any]] = {}
    paths = [standalone / AUDITOR_RELATIVE, standalone / PUBLICATION_RELATIVE,
             output_root / DESIGN_REVIEW_NAME, output_root / IMPLEMENTATION_REVIEW_NAME,
             output_root / DELIVERY_MANIFEST_NAME, output_root / DELIVERY_REVIEW_NAME]
    all_records = [*_snapshot_tree(receipt_root), *_snapshot_tree(delivery),
                   *_snapshot_tree(standalone / ".runtime/service-v1-b1-r4/venv"),
                   *(_snapshot_record(path) for path in paths)]
    for record in all_records:
        records_by_path[record["path"]] = record
    records = [records_by_path[key] for key in sorted(records_by_path)]
    fingerprint = sha256_bytes(canonical_json(
        {"schemaVersion": "feelm-service-v1-b1-r4-dependency-snapshot/1", "records": records}
    ))
    return records, fingerprint


def audit_receipt(*, standalone: Path, team: Path, delivery: Path, output_root: Path, scratch_root: Path,
                  receipt_manifest: Path, expected_receipt_manifest_sha256: str, reviewer_session: str,
                  runner: CommandRunner = run_command, probe_live_image: bool = True,
                  probe_imports: bool = True, enforce_current_interpreter: bool = True,
                  check_namespace: bool = True, publication_evidence: Mapping[str, Any] | None = None
                  ) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
    require(isinstance(expected_receipt_manifest_sha256, str) and
            HEX64.fullmatch(expected_receipt_manifest_sha256) is not None,
            "expected receipt manifest SHA-256 must be lowercase 64-hex")
    standalone = standalone.resolve(strict=True)
    team = team.resolve(strict=True)
    delivery = delivery.resolve(strict=True)
    output_root = output_root.resolve(strict=True)
    scratch_root = scratch_root.resolve(strict=True)
    receipt_root = receipt_manifest.parent.resolve(strict=True)
    require(receipt_root == output_root / RECEIPT_NAME and receipt_manifest.resolve() == receipt_root / "manifest.json",
            "noncanonical receipt manifest path")
    receipt_file_census(receipt_root)
    manifest_pin = pin_file(receipt_manifest, str(receipt_manifest.resolve()))
    require(manifest_pin["sha256"] == expected_receipt_manifest_sha256, "receipt manifest expected SHA drift")
    manifest, _manifest_raw = load_json(receipt_manifest)
    require(set(manifest) == MANIFEST_KEYS and
            manifest["schemaVersion"] == "feelm-service-v1-b1-r4-server-receipt/1" and
            manifest["status"] == "RECEIPT_AUDIT_PENDING" and manifest["runId"] == RUN_ID and
            manifest["profileId"] == PROFILE_ID, "receipt manifest shape/identity/status drift")
    validate_publication(manifest["publication"], "PRODUCER", "receipt.publication")
    require(Path(manifest["publication"]["finalPath"]).resolve() == receipt_root and
            Path(manifest["publication"]["failurePath"]).resolve() == output_root / f"{RECEIPT_NAME}-failure.json",
            "receipt publication destination drift")
    receipt_publisher = validate_pin(manifest["publication"]["publisher"], "receipt.publication.publisher")
    require(Path(receipt_publisher["path"]).resolve() == standalone / PUBLICATION_RELATIVE,
            "receipt publication publisher path drift")
    pin_matches(Path(receipt_publisher["path"]), receipt_publisher, "receipt.publication.publisher")
    created = parse_utc(manifest["createdAt"], "receipt.createdAt")
    require(created <= dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=5), "receipt timestamp is in the future")
    actual_roots = _validate_actual_roots(manifest["actualRoots"], standalone, team, delivery,
                                         output_root, scratch_root)
    producer = validate_pin(manifest["producer"], "receipt.producer")
    pin_matches(Path(producer["path"]), producer, "receipt.producer")
    delivery_value = manifest["delivery"]
    require(isinstance(delivery_value, Mapping) and set(delivery_value) ==
            {"manifest", "review", "deliverySetSha256", "crossGroupReferencesSha256"},
            "receipt delivery shape drift")
    delivery_manifest_pin = validate_pin(delivery_value["manifest"], "receipt.delivery.manifest")
    delivery_review_pin = validate_pin(delivery_value["review"], "receipt.delivery.review")
    require(Path(delivery_manifest_pin["path"]).resolve() == output_root / DELIVERY_MANIFEST_NAME and
            Path(delivery_review_pin["path"]).resolve() == output_root / DELIVERY_REVIEW_NAME,
            "noncanonical delivery manifest/review path")
    pin_matches(Path(delivery_manifest_pin["path"]), delivery_manifest_pin, "receipt.delivery.manifest")
    pin_matches(Path(delivery_review_pin["path"]), delivery_review_pin, "receipt.delivery.review")
    delivery_manifest, _delivery_manifest_raw = load_json(Path(delivery_manifest_pin["path"]))
    require(set(delivery_manifest) == DELIVERY_KEYS and
            delivery_manifest["schemaVersion"] == "feelm-service-v1-b1-r4-server-delivery/2" and
            delivery_manifest["status"] == "DELIVERY_AUDIT_PENDING" and
            delivery_manifest["runId"] == RUN_ID and delivery_manifest["profileId"] == PROFILE_ID and
            delivery_manifest["deliverySetSha256"] == delivery_value["deliverySetSha256"] and
            delivery_manifest["crossGroupReferencesSha256"] == delivery_value["crossGroupReferencesSha256"],
            "delivery manifest current contract drift")
    delivery_review, _delivery_review_raw = load_json(Path(delivery_review_pin["path"]))
    require(set(delivery_review) == REVIEW_KEYS and
            delivery_review.get("schemaVersion") == "feelm-service-v1-b1-r4-server-delivery-review/1" and
            delivery_review.get("status") == "PASS" and
            delivery_review.get("decision") == {"deliveryIntegrity": "PASS", "serverTransferEligible": True,
                                                "publicPreflightEligible": False, "dockerLoadEligible": True,
                                                "runtimeInstallEligible": True, "deploymentAuthorized": False},
            "delivery review no longer authorizes receipt")
    validate_publication(delivery_review["publication"], "REVIEWER", "deliveryReview.publication")
    delivery_checks = delivery_review["checks"]
    require(isinstance(delivery_checks, Mapping) and set(delivery_checks) ==
            {"schemaValid", "implementationReviewPass", "groupUnionDisjoint", "crossReferencesValid",
             "allFilesRehashed", "imageArchiveVerified", "ancestryVerified", "authorizationValid",
             "namespaceValid"} and all(value is True for value in delivery_checks.values()),
            "delivery review checks drift")
    delivery_target = delivery_review["target"]
    require(isinstance(delivery_target, Mapping) and set(delivery_target) ==
            {"manifest", "implementationReview", "deliverySetSha256", "crossGroupReferencesSha256"},
            "delivery review target shape drift")
    reviewed_manifest = validate_pin(delivery_target["manifest"], "deliveryReview.target.manifest")
    reviewed_implementation = validate_pin(delivery_target["implementationReview"],
                                           "deliveryReview.target.implementationReview")
    require(Path(reviewed_implementation["path"]).resolve() == output_root / IMPLEMENTATION_REVIEW_NAME,
            "delivery review implementationReview path drift")
    pin_matches(Path(reviewed_implementation["path"]), reviewed_implementation,
                "deliveryReview.target.implementationReview")
    require((reviewed_manifest["bytes"], reviewed_manifest["sha256"]) ==
            (delivery_manifest_pin["bytes"], delivery_manifest_pin["sha256"]) and
            delivery_target["deliverySetSha256"] == delivery_value["deliverySetSha256"] and
            delivery_target["crossGroupReferencesSha256"] == delivery_value["crossGroupReferencesSha256"],
            "delivery review target drift")
    inventory = manifest["destinationInventory"]
    bindings = expected_destination_bindings(delivery_manifest, standalone, team, delivery)
    records = validate_destination_inventory(inventory, actual_roots["delivery"], bindings)
    require(inventory["expectedDeliverySetSha256"] == delivery_value["deliverySetSha256"],
            "receipt/delivery set digest drift")
    inventory_path = receipt_root / "destination-inventory.json"
    inventory_file, inventory_raw = load_json(inventory_path)
    require(inventory_file == inventory and inventory_raw == canonical_json(inventory),
            "destination inventory child/inline drift")
    host = manifest["hostProbe"]
    require(isinstance(host, Mapping), "hostProbe must be inline")
    validate_host(host)
    require(parse_utc(host["observedAt"], "hostProbe.observedAt") <= created,
            "host probe was observed after receipt creation")
    host_path = receipt_root / "host-probe.json"
    host_file, host_raw = load_json(host_path)
    require(host_file == host and host_raw == canonical_json(host), "host probe child/inline drift")
    reservation_pins = manifest["maintenanceReservations"]
    require(isinstance(reservation_pins, Mapping) and set(reservation_pins) == set(RESERVATION_FILES),
            "maintenance reservation pin set drift")
    reservation_ids: set[str] = set()
    reservation_hosts: set[str] = set()
    reservation_target: dict[str, dict[str, Any]] = {}
    for key, (phase, filename, seconds) in RESERVATION_FILES.items():
        path = receipt_root / "maintenance-reservations" / filename
        reservation_target[key] = pin_matches(path, reservation_pins[key], f"reservation.{key}")
        reservation, _raw = load_json(path)
        validate_reservation(reservation, phase, seconds)
        require(reservation["reservationId"] not in reservation_ids, "reservation ID reused")
        reservation_ids.add(reservation["reservationId"])
        reservation_hosts.add(reservation["hostIdentity"])
    require(len(reservation_hosts) == 1, "reservations refer to different hosts")
    image_path = receipt_root / "image-load.json"
    pin_matches(image_path, manifest["imageLoad"], "receipt.imageLoad")
    image, _image_raw = load_json(image_path)
    validate_image_load(image, records, runner, probe_live_image=probe_live_image)
    venv_path = receipt_root / "runtime/venv-file-inventory.json"
    venv_inventory, _venv_raw = load_json(venv_path)
    runtime_path = receipt_root / "runtime/service-v1-b1-r4-host-runtime-lock.json"
    runtime_pin = pin_matches(runtime_path, manifest["hostRuntime"], "receipt.hostRuntime")
    runtime, _runtime_raw = load_json(runtime_path)
    validate_runtime(runtime, venv_inventory, receipt_root, runner, probe_imports=probe_imports,
                     enforce_current_interpreter=enforce_current_interpreter)
    filesystem = manifest["filesystem"]
    require(filesystem == {"stageAndFinalSameDevice": True, "regularFilesOnly": True, "symlinkCount": 0,
                           "junctionCount": 0, "fifoCount": 0, "socketCount": 0, "deviceCount": 0,
                           "hardLinkAliasCount": 0, "duplicateInodeCount": 0},
            "receipt filesystem evidence drift")
    summary = manifest["rehashSummary"]
    require(isinstance(summary, Mapping) and set(summary) ==
            {"recordsExpected", "recordsVerified", "labelBytesHashed", "labelRowsRead", "evaluationTargetsRead",
             "dependencyFingerprint", "destinationRecordSetSha256", "venvRecordSetSha256"},
            "receipt rehashSummary shape drift")
    require(type(summary["recordsExpected"]) is int and type(summary["recordsVerified"]) is int and
            summary["recordsExpected"] == summary["recordsVerified"] == len(records),
            "receipt destination count drift")
    require(summary["labelBytesHashed"] is True and summary["labelRowsRead"] is False and
            summary["evaluationTargetsRead"] is False, "receipt label bytes-only gate drift")
    require(summary["destinationRecordSetSha256"] == inventory["recordSetSha256"] and
            summary["venvRecordSetSha256"] == venv_inventory["recordSetSha256"] and
            isinstance(summary["dependencyFingerprint"], str) and HEX64.fullmatch(summary["dependencyFingerprint"]),
            "receipt rehash summary digest drift")
    require(summary["dependencyFingerprint"] == manifest["publication"]["dependencyFingerprintAtAcquire"] ==
            manifest["publication"]["dependencyFingerprintBeforeRename"],
            "receipt publication/dependency fingerprint drift")
    require(manifest["authorization"] == {"state": "RECEIPT_AUDIT_PENDING",
                                          "publicPreflightEligible": False, "fitEligible": False,
                                          "scoreEligible": False, "evaluationEligible": False,
                                          "deploymentAuthorized": False}, "receipt authorization drift")
    target = {"manifest": manifest_pin,
              "destinationInventory": pin_file(inventory_path, str(inventory_path.resolve())),
              "hostProbe": pin_file(host_path, str(host_path.resolve())),
              "maintenanceReservations": reservation_target,
              "imageLoad": pin_file(image_path, str(image_path.resolve())), "hostRuntime": runtime_pin}
    dependencies = _dependency_records(receipt_root, standalone, inventory, venv_inventory)
    fingerprint = canonical_record_set_sha256(dependencies)
    require(rehash_dependencies(dependencies) == fingerprint, "receipt review dependency acquisition drift")
    if check_namespace:
        expected_namespace = tuple(sorted((DESIGN_REVIEW_NAME, IMPLEMENTATION_REVIEW_NAME,
                                           DELIVERY_MANIFEST_NAME, DELIVERY_REVIEW_NAME, RECEIPT_NAME)))
        validate_exact_namespace(output_root, expected_namespace)
    checks = {"deliveryReviewPass": True, "destinationRehashPass": True, "filesystemPass": True,
              "imageLoadPass": True, "runtimePass": True, "hostPass": True, "reservationPass": True,
              "labelGatePreserved": True, "namespacePass": True}
    review = {"schemaVersion": "feelm-service-v1-b1-r4-server-receipt-review/1", "status": "PASS",
              "runId": RUN_ID, "profileId": PROFILE_ID, "createdAt": utc_now(),
              "reviewer": {"kind": "INDEPENDENT_SERVER_RECEIPT_REVIEWER", "sessionId": reviewer_session,
                           "host": os.uname().nodename if hasattr(os, "uname") else
                               os.environ.get("COMPUTERNAME", "unknown"), "processId": os.getpid()},
              "target": target, "checks": checks,
              "decision": {"receiptIntegrity": "PASS", "publicPreflightEligible": True,
                           "fitEligible": False, "deploymentAuthorized": False},
              "dependencyFingerprint": fingerprint}
    if publication_evidence is not None:
        review["publication"] = dict(publication_evidence)
    return review, fingerprint, dependencies


def _publication_module() -> Any:
    module = importlib.import_module("service_v1_b1_r4_publication")
    for name in ("acquire_publication", "publish_success", "publish_handled_failure", "release_verified_claim"):
        require(callable(getattr(module, name, None)), f"shared publication API missing: {name}")
    return module


def _evidence(lease: Any) -> dict[str, Any]:
    value = getattr(lease, "publication_evidence", None)
    value = value() if callable(value) else value
    require(isinstance(value, Mapping), "publication Lease evidence missing")
    return dict(value)


def _write_exclusive(path: Path, raw: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                         getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0), 0o600)
    try:
        offset = 0
        while offset < len(raw):
            offset += os.write(descriptor, raw[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _as_pin(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    record = getattr(value, "record", None)
    if isinstance(record, Mapping):
        return dict(record)
    return validate_pin({key: getattr(value, key, None) for key in ("path", "bytes", "sha256")}, "published")


def _reviewer(reviewer_session: str) -> dict[str, Any]:
    require(isinstance(reviewer_session, str) and reviewer_session.strip(), "reviewer session is required")
    return {"kind": "INDEPENDENT_SERVER_RECEIPT_REVIEWER", "sessionId": reviewer_session,
            "host": os.uname().nodename if hasattr(os, "uname") else os.environ.get("COMPUTERNAME", "unknown"),
            "processId": os.getpid()}


def _review_target_pins(receipt_root: Path) -> list[dict[str, Any]]:
    paths = [receipt_root / "manifest.json", receipt_root / "destination-inventory.json",
             receipt_root / "host-probe.json",
             *(receipt_root / "maintenance-reservations" / value[1] for value in RESERVATION_FILES.values()),
             receipt_root / "image-load.json",
             receipt_root / "runtime/service-v1-b1-r4-host-runtime-lock.json"]
    return [pin_file(path, str(path.resolve())) for path in paths]


def _available_review_target_pins(receipt_root: Path) -> list[dict[str, Any]]:
    pins: list[dict[str, Any]] = []
    paths = [receipt_root / "manifest.json", receipt_root / "destination-inventory.json",
             receipt_root / "host-probe.json",
             *(receipt_root / "maintenance-reservations" / value[1] for value in RESERVATION_FILES.values()),
             receipt_root / "image-load.json",
             receipt_root / "runtime/service-v1-b1-r4-host-runtime-lock.json"]
    for path in paths:
        try:
            pins.append(pin_file(path, str(_lexical_absolute(path))))
        except BaseException:
            continue
    return pins


def _namespace_census(output_root: Path) -> dict[str, list[str]]:
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


def validate_review_failure(payload: Mapping[str, Any]) -> None:
    require(set(payload) == REVIEW_FAILURE_KEYS and
            payload["schemaVersion"] == "feelm-service-v1-b1-r4-review-failure/1" and
            payload["status"] == "FAILED" and payload["runId"] == RUN_ID and
            payload["profileId"] == PROFILE_ID and payload["phase"] == "receipt-review",
            "receipt review failure top-level shape/identity drift")
    parse_utc(payload["createdAt"], "reviewFailure.createdAt")
    reviewer = payload["auditor"]
    require(isinstance(reviewer, Mapping) and set(reviewer) == {"kind", "sessionId", "host", "processId"} and
            all(isinstance(reviewer[key], str) and reviewer[key] for key in ("kind", "sessionId", "host")) and
            type(reviewer["processId"]) is int and reviewer["processId"] > 0,
            "receipt review failure auditor shape drift")
    require(isinstance(payload["target"], list), "receipt review failure target must be an array")
    for index, pin in enumerate(payload["target"]):
        validate_pin(pin, f"reviewFailure.target[{index}]")
    require(isinstance(payload["error"], Mapping) and set(payload["error"]) == {"type", "message", "traceback"} and
            all(isinstance(value, str) for value in payload["error"].values()),
            "receipt review failure error shape drift")
    require(all(isinstance(payload[key], str) and HEX64.fullmatch(payload[key]) for key in
                ("dependencyFingerprintBefore", "dependencyFingerprintAfter")),
            "receipt review failure dependency fingerprint drift")
    require(isinstance(payload["namespaceCensus"], Mapping) and set(payload["namespaceCensus"]) ==
            {"completed", "failures", "reviews", "claims", "temps", "scratches", "containers", "processes"},
            "receipt review failure namespace census shape drift")
    require(payload["passReviewPublished"] is False and payload["deploymentAuthorized"] is False,
            "receipt review failure authorization drift")
    validate_publication(payload["publication"], "REVIEWER", "reviewFailure.publication")
    canonical_json(payload)


def _review_failure(error: BaseException, lease: Any, reviewer: Mapping[str, Any],
                    target: Sequence[Mapping[str, Any]], fingerprint: str,
                    dependency_after: str, output_root: Path) -> dict[str, Any]:
    payload = {"schemaVersion": "feelm-service-v1-b1-r4-review-failure/1", "status": "FAILED",
            "runId": RUN_ID, "profileId": PROFILE_ID, "phase": "receipt-review", "createdAt": utc_now(),
            "auditor": dict(reviewer), "target": [dict(value) for value in target],
            "error": {"type": type(error).__name__, "message": str(error),
                      "traceback": "".join(traceback.format_exception(type(error), error, error.__traceback__))},
            "dependencyFingerprintBefore": fingerprint,
            "dependencyFingerprintAfter": dependency_after,
            "namespaceCensus": _namespace_census(output_root), "passReviewPublished": False,
            "deploymentAuthorized": False, "publication": _evidence(lease)}
    validate_review_failure(payload)
    return payload


def publish_review(*, standalone: Path, team: Path, delivery: Path, output_root: Path, scratch_root: Path,
                   receipt_manifest: Path, expected_receipt_manifest_sha256: str, reviewer_session: str,
                   runner: CommandRunner = run_command, publication: Any | None = None,
                   probe_live_image: bool = True, probe_imports: bool = True,
                   enforce_current_interpreter: bool = True) -> dict[str, Any]:
    standalone = standalone.resolve(strict=True)
    team = team.resolve(strict=True)
    delivery = delivery.resolve(strict=True)
    output_root = output_root.resolve(strict=True)
    scratch_root = scratch_root.resolve(strict=True)
    receipt_root = output_root / RECEIPT_NAME
    final_path = output_root / RECEIPT_REVIEW_NAME
    failure_path = output_root / RECEIPT_REVIEW_FAILURE_NAME
    api = publication if publication is not None else _publication_module()
    expected = tuple(sorted((DESIGN_REVIEW_NAME, IMPLEMENTATION_REVIEW_NAME, DELIVERY_MANIFEST_NAME,
                             DELIVERY_REVIEW_NAME, RECEIPT_NAME)))
    _snapshot_records, fingerprint = reviewer_dependency_snapshot(standalone, delivery, output_root,
                                                                  receipt_root)
    validate_exact_namespace(output_root, expected)
    lease = api.acquire_publication("REVIEWER", "receipt-review", final_path, failure_path,
                                    {"children": list(expected), "dependencyFingerprint": fingerprint})
    reviewer = _reviewer(reviewer_session if isinstance(reviewer_session, str) and reviewer_session.strip()
                         else "INVALID-REVIEWER-SESSION")
    target_pins = _available_review_target_pins(receipt_root)
    try:
        require(isinstance(expected_receipt_manifest_sha256, str) and
                HEX64.fullmatch(expected_receipt_manifest_sha256) is not None,
                "expected receipt manifest SHA-256 must be lowercase 64-hex")
        require(_lexical_absolute(receipt_manifest) == receipt_root / "manifest.json" and
                receipt_manifest.resolve(strict=True) == receipt_root / "manifest.json",
                "noncanonical receipt manifest path")
        require(isinstance(reviewer_session, str) and reviewer_session.strip(), "reviewer session is required")
        require(reviewer_dependency_snapshot(standalone, delivery, output_root, receipt_root)[1] == fingerprint,
                "receipt review dependency snapshot drift before live probes")
        review, audited_fingerprint, audited_dependencies = audit_receipt(
            standalone=standalone, team=team, delivery=delivery, output_root=output_root,
            scratch_root=scratch_root, receipt_manifest=receipt_manifest,
            expected_receipt_manifest_sha256=expected_receipt_manifest_sha256,
            reviewer_session=reviewer_session, runner=runner, probe_live_image=probe_live_image,
            probe_imports=probe_imports, enforce_current_interpreter=enforce_current_interpreter,
            check_namespace=False, publication_evidence=_evidence(lease),
        )
        require(rehash_dependencies(audited_dependencies) == audited_fingerprint,
                "receipt review validated dependency closure drift during audit")
        require(reviewer_dependency_snapshot(standalone, delivery, output_root, receipt_root)[1] == fingerprint,
                "receipt review dependency snapshot drift after audit")
        review["dependencyFingerprint"] = fingerprint
    except BaseException as error:
        dependency_after = reviewer_dependency_snapshot(standalone, delivery, output_root, receipt_root)[1]
        failure = _review_failure(error, lease, reviewer, target_pins, fingerprint,
                                  dependency_after, output_root)
        published = api.publish_handled_failure(lease, failure, fingerprint,
                                                lambda: reviewer_dependency_snapshot(
                                                    standalone, delivery, output_root, receipt_root)[1])
        post = reviewer_dependency_snapshot(standalone, delivery, output_root, receipt_root)[1]
        api.release_verified_claim(lease, published, post)
        validate_exact_namespace(output_root, tuple(sorted((*expected, RECEIPT_REVIEW_FAILURE_NAME))))
        raise ReceiptAuditError(f"receipt review failed; immutable failure published: {error}") from error
    staging = Path(lease.temp_path)
    existed_before = os.path.lexists(staging)
    try:
        _write_exclusive(staging, canonical_json(review))
    except BaseException as error:
        if not existed_before and os.path.lexists(staging):
            current = os.lstat(staging)
            require(stat.S_ISREG(current.st_mode) and current.st_nlink == 1,
                    "review staging ownership drift after write failure")
            staging.unlink()
        elif existed_before:
            raise
        dependency_after = reviewer_dependency_snapshot(standalone, delivery, output_root, receipt_root)[1]
        failure = _review_failure(error, lease, reviewer, target_pins, fingerprint,
                                  dependency_after, output_root)
        published = api.publish_handled_failure(
            lease, failure, fingerprint,
            lambda: reviewer_dependency_snapshot(standalone, delivery, output_root, receipt_root)[1])
        post = reviewer_dependency_snapshot(standalone, delivery, output_root, receipt_root)[1]
        api.release_verified_claim(lease, published, post)
        validate_exact_namespace(output_root, tuple(sorted((*expected, RECEIPT_REVIEW_FAILURE_NAME))))
        raise ReceiptAuditError(f"receipt review failed; immutable failure published: {error}") from error
    staging_info = os.lstat(staging)
    require(stat.S_ISREG(staging_info.st_mode) and staging_info.st_nlink == 1,
            "review staging identity invalid before publication")
    staging_identity = (staging_info.st_dev, staging_info.st_ino)
    try:
        published = api.publish_success(lease, staging, fingerprint,
                                        lambda: reviewer_dependency_snapshot(
                                            standalone, delivery, output_root, receipt_root)[1])
    except BaseException as error:
        dependency_after = reviewer_dependency_snapshot(
            standalone, delivery, output_root, receipt_root)[1]
        unambiguous_pre_rename = (
            dependency_after == fingerprint and os.path.lexists(staging) and
            not os.path.lexists(final_path) and not os.path.lexists(failure_path)
        )
        if not unambiguous_pre_rename:
            raise ReceiptAuditError(
                f"receipt review publication state is ambiguous or drifted; claim preserved: {error}"
            ) from error
        current = os.lstat(staging)
        require(stat.S_ISREG(current.st_mode) and current.st_nlink == 1 and
                (current.st_dev, current.st_ino) == staging_identity,
                "review staging identity drift after publication failure")
        staging.unlink()
        failure = _review_failure(error, lease, reviewer, target_pins, fingerprint,
                                  dependency_after, output_root)
        failed_pin = api.publish_handled_failure(
            lease, failure, fingerprint,
            lambda: reviewer_dependency_snapshot(
                standalone, delivery, output_root, receipt_root)[1])
        post = reviewer_dependency_snapshot(standalone, delivery, output_root, receipt_root)[1]
        api.release_verified_claim(lease, failed_pin, post)
        validate_exact_namespace(output_root, tuple(sorted((*expected, RECEIPT_REVIEW_FAILURE_NAME))))
        raise ReceiptAuditError(
            f"receipt review failed; immutable failure published: {error}"
        ) from error
    post = reviewer_dependency_snapshot(standalone, delivery, output_root, receipt_root)[1]
    api.release_verified_claim(lease, published, post)
    validate_exact_namespace(output_root, tuple(sorted((*expected, RECEIPT_REVIEW_NAME))))
    return _as_pin(published)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--standalone-root", type=Path, required=True)
    result.add_argument("--team-root", type=Path, required=True)
    result.add_argument("--delivery-root", type=Path, required=True)
    result.add_argument("--output-root", type=Path, required=True)
    result.add_argument("--scratch-root", type=Path, required=True)
    result.add_argument("--server-receipt-manifest", type=Path, required=True)
    result.add_argument("--expected-server-receipt-manifest-sha256", required=True)
    result.add_argument("--reviewer-session", required=True)
    result.add_argument("--publish", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    arguments = {"standalone": args.standalone_root, "team": args.team_root, "delivery": args.delivery_root,
                 "output_root": args.output_root, "scratch_root": args.scratch_root,
                 "receipt_manifest": args.server_receipt_manifest,
                 "expected_receipt_manifest_sha256": args.expected_server_receipt_manifest_sha256,
                 "reviewer_session": args.reviewer_session}
    if args.publish:
        result = publish_review(**arguments)
    else:
        review, fingerprint, _dependencies = audit_receipt(**arguments)
        result = {"status": review["status"], "dependencyFingerprint": fingerprint}
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
