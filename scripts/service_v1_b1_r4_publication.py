"""Fail-closed Linux publication primitives for the service-v1 B1 r4 run.

The module deliberately separates publication into three states:

* :func:`acquire_publication` owns the namespace and records pre-rename facts.
* one of the two ``publish_*`` functions fsyncs and atomically renames a staged
  artifact without replacing an existing path.
* :func:`release_verified_claim` removes the claim only after the caller has
  rehashed its dependencies and this module has rehashed the publication.

Any exception after claim acquisition leaves the claim and any staged artifact
in place.  A stale or ambiguous namespace therefore needs forensic handling and
cannot silently become another attempt.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import socket
import stat
from typing import Any, Callable, Mapping, Sequence
import uuid


RUN_ID = "b1-gbt120-s339-v1-r4-server5c20g-t28800"
PROFILE_ID = "ec2-8vcpu32g-local5-fit20g-t28800-v1"
PUBLISHER_LOGICAL_PATH = "scripts/service_v1_b1_r4_publication.py"

PUBLICATION_SCHEMA = "feelm-service-v1-b1-r4-publication-evidence/1"
CLAIM_SCHEMA = "feelm-service-v1-b1-r4-publication-claim/2"

ROLES = frozenset({"PRODUCER", "REVIEWER"})
PHASES = frozenset(
    {
        "implementation-review",
        "delivery-build",
        "delivery-review",
        "receipt-build",
        "receipt-review",
        "preflight",
        "preflight-review",
        "fit",
        "fit-review",
        "score",
        "score-review",
        "calibrate-select",
        "selection-review",
        "confirmation",
        "confirmation-review",
    }
)

_LOWER_HEX = frozenset("0123456789abcdef")
_RENAME_NOREPLACE = 1
_AT_FDCWD = -100
_EXT_MAGIC = 0xEF53
_XFS_MAGIC = 0x58465342
_PUBLICATION_KEYS = frozenset(
    {
        "schemaVersion",
        "mode",
        "role",
        "finalPath",
        "failurePath",
        "claimPath",
        "tempPath",
        "token",
        "claimStDev",
        "claimStIno",
        "filesystemType",
        "publisher",
        "dependencyFingerprintAtAcquire",
        "dependencyFingerprintBeforeRename",
        "renameNoReplaceProbe",
        "requiredPostconditions",
    }
)
_POSTCONDITION_KEYS = frozenset(
    {
        "publishedBytesRehashRequired",
        "fileAndParentFsyncRequired",
        "dependencyFingerprintStableRequired",
        "claimIdentityMatchRequired",
        "claimRemovalRequired",
    }
)
_CLAIM_KEYS = frozenset(
    {
        "schemaVersion",
        "role",
        "runId",
        "profileId",
        "phase",
        "finalPath",
        "failurePath",
        "token",
        "pid",
        "hostname",
        "publisherPath",
        "publisherSha256",
        "startedAt",
    }
)


class PublicationError(RuntimeError):
    """A fail-closed publication error with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class Pin:
    """The exact pin-record shape used by the r4 evidence contracts."""

    path: str
    bytes: int
    sha256: str

    @property
    def record(self) -> dict[str, object]:
        return {"path": self.path, "bytes": self.bytes, "sha256": self.sha256}


@dataclass(frozen=True)
class _SealedEntry:
    """One immutable entry in a staged directory snapshot."""

    relative_path: str
    kind: str
    st_dev: int
    st_ino: int
    mode: int
    bytes: int
    sha256: str

    @property
    def record(self) -> dict[str, object]:
        return {
            "path": self.relative_path,
            "kind": self.kind,
            "stDev": self.st_dev,
            "stIno": self.st_ino,
            "mode": self.mode,
            "bytes": self.bytes,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class _StagingSeal:
    """Identity and complete content snapshot held across the callback window."""

    directory: bool
    root_st_dev: int
    root_st_ino: int
    root_mode: int
    manifest_bytes: int
    manifest_sha256: str
    entries: tuple[_SealedEntry, ...]
    tree_sha256: str


_PUBLISHED_SEALS: dict[str, _StagingSeal] = {}


@dataclass(frozen=True)
class ExpectedNamespace:
    """Completed direct children and the dependency digest at lease acquire."""

    children: tuple[str, ...]
    dependency_fingerprint: str


@dataclass(frozen=True)
class Lease:
    """Immutable ownership facts returned by :func:`acquire_publication`."""

    role: str
    phase: str
    final_path: Path
    failure_path: Path
    claim_path: Path
    temp_path: Path
    token: str
    claim_st_dev: int
    claim_st_ino: int
    filesystem_type: str
    publisher: Pin
    dependency_fingerprint_at_acquire: str
    expected_children: tuple[str, ...]
    owner_pid: int
    owner_hostname: str

    @property
    def publication_evidence(self) -> dict[str, object]:
        """Return the exact pre-rename publication evidence object.

        Both dependency fields have the acquisition value.  Publication is
        allowed only when the callback immediately before rename returns that
        same value, so the object never claims an unobserved post-state.
        """

        return {
            "schemaVersion": PUBLICATION_SCHEMA,
            "mode": "LINUX_RENAME_NOREPLACE",
            "role": self.role,
            "finalPath": str(self.final_path),
            "failurePath": str(self.failure_path),
            "claimPath": str(self.claim_path),
            "tempPath": str(self.temp_path),
            "token": self.token,
            "claimStDev": self.claim_st_dev,
            "claimStIno": self.claim_st_ino,
            "filesystemType": self.filesystem_type,
            "publisher": self.publisher.record,
            "dependencyFingerprintAtAcquire": self.dependency_fingerprint_at_acquire,
            "dependencyFingerprintBeforeRename": self.dependency_fingerprint_at_acquire,
            "renameNoReplaceProbe": True,
            "requiredPostconditions": {
                "publishedBytesRehashRequired": True,
                "fileAndParentFsyncRequired": True,
                "dependencyFingerprintStableRequired": True,
                "claimIdentityMatchRequired": True,
                "claimRemovalRequired": True,
            },
        }

    @property
    def publication(self) -> dict[str, object]:
        """Backward-compatible spelling; each access still returns a fresh copy."""

        return self.publication_evidence


class _StatFs(ctypes.Structure):
    _fields_ = [
        ("f_type", ctypes.c_long),
        ("f_bsize", ctypes.c_long),
        ("f_blocks", ctypes.c_ulong),
        ("f_bfree", ctypes.c_ulong),
        ("f_bavail", ctypes.c_ulong),
        ("f_files", ctypes.c_ulong),
        ("f_ffree", ctypes.c_ulong),
        ("f_fsid", ctypes.c_int * 2),
        ("f_namelen", ctypes.c_long),
        ("f_frsize", ctypes.c_long),
        ("f_flags", ctypes.c_long),
        ("f_spare", ctypes.c_long * 4),
    ]


def _need(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise PublicationError(code, message)


def _is_lower_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _LOWER_HEX for character in value)
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _json_object_pairs(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PublicationError("BLOCKED_INVALID_JSON", f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise PublicationError("BLOCKED_INVALID_JSON", "non-finite JSON number")
    if isinstance(value, Mapping):
        for child in value.values():
            _reject_nonfinite(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_nonfinite(child)


def _encode_json(value: Any) -> bytes:
    _reject_nonfinite(value)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
    except (TypeError, ValueError) as error:
        raise PublicationError("BLOCKED_INVALID_JSON", str(error)) from error
    return encoded


def _decode_json(encoded: bytes) -> Any:
    _need(encoded.endswith(b"\n"), "BLOCKED_INVALID_JSON", "JSON must end with LF")
    _need(b"\r" not in encoded, "BLOCKED_INVALID_JSON", "JSON contains CR")
    try:
        text = encoded.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PublicationError("BLOCKED_INVALID_JSON", "JSON is not UTF-8") from error
    try:
        value = json.loads(
            text,
            object_pairs_hook=_json_object_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                PublicationError("BLOCKED_INVALID_JSON", f"non-finite JSON token: {token}")
            ),
        )
    except PublicationError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise PublicationError("BLOCKED_INVALID_JSON", str(error)) from error
    _reject_nonfinite(value)
    return value


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    offset = 0
    while offset < len(view):
        written = os.write(descriptor, view[offset:])
        _need(written > 0, "BLOCKED_IO", "short publication write")
        offset += written


def _lexical_absolute(path: os.PathLike[str] | str, label: str) -> Path:
    raw = os.fspath(path)
    _need(isinstance(raw, str) and raw != "", "BLOCKED_PATH", f"{label} is empty")
    candidate = Path(raw)
    _need(candidate.is_absolute(), "BLOCKED_PATH", f"{label} must be absolute")
    normalized = Path(os.path.abspath(raw))
    _need(candidate == normalized, "BLOCKED_PATH", f"{label} is not lexically canonical")
    return normalized


def _open_parent(parent: Path) -> int:
    _need(os.name == "posix" and hasattr(os, "O_NOFOLLOW"),
          "BLOCKED_UNSUPPORTED_FILESYSTEM", "Linux O_NOFOLLOW is required")
    _need(os.path.realpath(parent) == str(parent), "BLOCKED_PATH", "parent contains a symlink")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(parent, flags)
    except OSError as error:
        raise PublicationError("BLOCKED_PATH", f"cannot open canonical parent: {error}") from error
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        os.close(descriptor)
        raise PublicationError("BLOCKED_PATH", "canonical parent is not a directory")
    return descriptor


def _filesystem_type(path: Path) -> str:
    if os.name != "posix":
        raise PublicationError("BLOCKED_UNSUPPORTED_FILESYSTEM", "Linux statfs is required")
    library = ctypes.CDLL(None, use_errno=True)
    function = getattr(library, "statfs", None)
    _need(function is not None, "BLOCKED_UNSUPPORTED_FILESYSTEM", "statfs is unavailable")
    function.argtypes = [ctypes.c_char_p, ctypes.POINTER(_StatFs)]
    function.restype = ctypes.c_int
    result = _StatFs()
    if function(os.fsencode(path), ctypes.byref(result)) != 0:
        error_number = ctypes.get_errno()
        raise PublicationError(
            "BLOCKED_UNSUPPORTED_FILESYSTEM",
            f"statfs failed: {os.strerror(error_number)}",
        )
    magic = int(result.f_type) & 0xFFFFFFFF
    if magic == _EXT_MAGIC:
        return "ext2/ext3"
    if magic == _XFS_MAGIC:
        return "xfs"
    raise PublicationError(
        "BLOCKED_UNSUPPORTED_FILESYSTEM",
        f"canonical parent filesystem magic is unsupported: 0x{magic:08x}",
    )


def _read_regular_descriptor(descriptor: int, label: str) -> tuple[bytes, os.stat_result]:
    metadata = os.fstat(descriptor)
    _need(stat.S_ISREG(metadata.st_mode), "BLOCKED_SPECIAL_FILE", f"{label} is not regular")
    _need(metadata.st_nlink == 1, "BLOCKED_HARDLINK", f"{label} has multiple hard links")
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        chunks.append(chunk)
    data = b"".join(chunks)
    after = os.fstat(descriptor)
    _need(
        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        == (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns),
        "BLOCKED_SOURCE_DRIFT",
        f"{label} changed while being read",
    )
    _need(len(data) == metadata.st_size, "BLOCKED_SOURCE_DRIFT", f"{label} size drift")
    return data, metadata


def _pin_source() -> Pin:
    source = Path(__file__)
    _need(source.is_absolute(), "BLOCKED_PUBLISHER", "publisher __file__ must be absolute")
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as error:
        raise PublicationError(
            "BLOCKED_PUBLISHER", f"cannot open publisher source: {error}"
        ) from error
    try:
        data, metadata = _read_regular_descriptor(descriptor, "publisher source")
    finally:
        os.close(descriptor)
    return Pin(PUBLISHER_LOGICAL_PATH, metadata.st_size, hashlib.sha256(data).hexdigest())


def _validate_child_name(name: object) -> str:
    _need(isinstance(name, str) and name not in {"", ".", ".."},
          "BLOCKED_NAMESPACE", "invalid expected child")
    _need(Path(name).name == name and "/" not in name and "\\" not in name,
          "BLOCKED_NAMESPACE", f"expected child is not direct: {name}")
    return name


def _coerce_expected_namespace(value: object) -> ExpectedNamespace:
    if isinstance(value, ExpectedNamespace):
        namespace = value
    elif isinstance(value, Mapping):
        _need(set(value) == {"children", "dependencyFingerprint"},
              "BLOCKED_NAMESPACE", "expected_namespace has wrong fields")
        children = value.get("children")
        fingerprint = value.get("dependencyFingerprint")
        _need(isinstance(children, (list, tuple)),
              "BLOCKED_NAMESPACE", "expected_namespace children must be a sequence")
        namespace = ExpectedNamespace(tuple(children), fingerprint)  # type: ignore[arg-type]
    else:
        raise PublicationError("BLOCKED_NAMESPACE", "expected_namespace has wrong type")
    normalized = tuple(_validate_child_name(child) for child in namespace.children)
    _need(tuple(sorted(normalized)) == normalized and len(set(normalized)) == len(normalized),
          "BLOCKED_NAMESPACE", "expected children must be unique and sorted")
    _need(_is_lower_sha256(namespace.dependency_fingerprint),
          "BLOCKED_DEPENDENCY", "acquisition dependency fingerprint is invalid")
    return ExpectedNamespace(normalized, namespace.dependency_fingerprint)


def _scan_namespace(parent_descriptor: int, expected: Sequence[str]) -> None:
    observed = tuple(sorted(os.listdir(parent_descriptor)))
    required = tuple(sorted(expected))
    _need(observed == required, "BLOCKED_NAMESPACE",
          f"namespace mismatch: expected {required!r}, observed {observed!r}")
    seen_regular: set[tuple[int, int]] = set()
    for name in observed:
        metadata = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        _need(not stat.S_ISLNK(metadata.st_mode), "BLOCKED_SYMLINK", f"symlink child: {name}")
        _need(stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode),
              "BLOCKED_SPECIAL_FILE", f"special namespace child: {name}")
        if stat.S_ISREG(metadata.st_mode):
            _need(metadata.st_nlink == 1, "BLOCKED_HARDLINK", f"hard-linked child: {name}")
            identity = (metadata.st_dev, metadata.st_ino)
            _need(identity not in seen_regular, "BLOCKED_HARDLINK", f"inode alias: {name}")
            seen_regular.add(identity)


def _entry_exists(parent_descriptor: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _rename_no_replace(
    source_parent_descriptor: int,
    source_name: str,
    destination_parent_descriptor: int,
    destination_name: str,
) -> None:
    library = ctypes.CDLL(None, use_errno=True)
    function = getattr(library, "renameat2", None)
    _need(function is not None, "BLOCKED_UNSUPPORTED_FILESYSTEM", "renameat2 is unavailable")
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    function.restype = ctypes.c_int
    outcome = function(
        source_parent_descriptor,
        os.fsencode(source_name),
        destination_parent_descriptor,
        os.fsencode(destination_name),
        _RENAME_NOREPLACE,
    )
    if outcome != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), destination_name)


def _create_probe_file(parent_descriptor: int, name: str, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(name, flags, 0o600, dir_fd=parent_descriptor)
    try:
        _write_all(descriptor, data)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _probe_rename_no_replace(parent_descriptor: int, token: str) -> None:
    source = f".r4-rename-probe-source-{token}"
    second = f".r4-rename-probe-second-{token}"
    destination = f".r4-rename-probe-destination-{token}"
    _create_probe_file(parent_descriptor, source, b"source\n")
    _rename_no_replace(parent_descriptor, source, parent_descriptor, destination)
    os.fsync(parent_descriptor)
    _create_probe_file(parent_descriptor, second, b"second\n")
    try:
        _rename_no_replace(parent_descriptor, second, parent_descriptor, destination)
    except OSError as error:
        _need(error.errno == errno.EEXIST, "BLOCKED_UNSUPPORTED_FILESYSTEM",
              f"renameat2 did not report EEXIST: {error}")
    else:
        raise PublicationError(
            "BLOCKED_UNSUPPORTED_FILESYSTEM",
            "renameat2 replaced an existing destination",
        )
    os.unlink(second, dir_fd=parent_descriptor)
    os.unlink(destination, dir_fd=parent_descriptor)
    os.fsync(parent_descriptor)


def _claim_payload(
    role: str,
    phase: str,
    final_path: Path,
    failure_path: Path,
    token: str,
    owner_pid: int,
    hostname: str,
    publisher: Pin,
) -> dict[str, object]:
    return {
        "schemaVersion": CLAIM_SCHEMA,
        "role": role,
        "runId": RUN_ID,
        "profileId": PROFILE_ID,
        "phase": phase,
        "finalPath": str(final_path),
        "failurePath": str(failure_path),
        "token": token,
        "pid": owner_pid,
        "hostname": hostname,
        "publisherPath": publisher.path,
        "publisherSha256": publisher.sha256,
        "startedAt": _utc_now(),
    }


def _read_at(parent_descriptor: int, name: str, label: str) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    try:
        return _read_regular_descriptor(descriptor, label)
    finally:
        os.close(descriptor)


def _validate_claim(parent_descriptor: int, lease: Lease) -> None:
    _need(os.getpid() == lease.owner_pid, "BLOCKED_CLAIM_OWNER", "lease owner PID changed")
    data, metadata = _read_at(parent_descriptor, lease.claim_path.name, "publication claim")
    _need((metadata.st_dev, metadata.st_ino) == (lease.claim_st_dev, lease.claim_st_ino),
          "BLOCKED_CLAIM_OWNER", "claim inode changed")
    payload = _decode_json(data)
    _need(isinstance(payload, Mapping) and set(payload) == _CLAIM_KEYS,
          "BLOCKED_CLAIM_OWNER", "claim shape changed")
    expected = _claim_payload(
        lease.role,
        lease.phase,
        lease.final_path,
        lease.failure_path,
        lease.token,
        lease.owner_pid,
        lease.owner_hostname,
        lease.publisher,
    )
    expected["startedAt"] = payload.get("startedAt")
    _need(payload == expected, "BLOCKED_CLAIM_OWNER", "claim content changed")
    started_at = payload.get("startedAt")
    _need(isinstance(started_at, str) and started_at.endswith("Z"),
          "BLOCKED_CLAIM_OWNER", "claim timestamp is invalid")


def _validate_publisher(lease: Lease) -> None:
    _need(_pin_source() == lease.publisher,
          "BLOCKED_PUBLISHER", "publisher source changed after acquisition")


def _validate_publication_object(value: object, lease: Lease) -> None:
    _need(isinstance(value, Mapping) and set(value) == _PUBLICATION_KEYS,
          "BLOCKED_PUBLICATION_EVIDENCE", "publication evidence shape mismatch")
    _need(value == lease.publication_evidence,
          "BLOCKED_PUBLICATION_EVIDENCE", "publication evidence does not match lease")
    postconditions = value.get("requiredPostconditions")
    _need(isinstance(postconditions, Mapping) and set(postconditions) == _POSTCONDITION_KEYS,
          "BLOCKED_PUBLICATION_EVIDENCE", "postcondition shape mismatch")
    _need(all(item is True for item in postconditions.values()),
          "BLOCKED_PUBLICATION_EVIDENCE", "postconditions must all be true")


def _open_directory_at(parent_descriptor: int, name: str) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    return os.open(name, flags, dir_fd=parent_descriptor)


def _stable_read_metadata(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _hash_regular_descriptor(
    descriptor: int,
    label: str,
    *,
    sync: bool,
) -> tuple[int, str, os.stat_result]:
    before = os.fstat(descriptor)
    _need(stat.S_ISREG(before.st_mode), "BLOCKED_SPECIAL_FILE", f"{label} is not regular")
    _need(before.st_nlink == 1, "BLOCKED_HARDLINK", f"{label} has multiple hard links")
    if sync:
        os.fsync(descriptor)
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    observed_bytes = 0
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
        observed_bytes += len(chunk)
    after = os.fstat(descriptor)
    _need(
        _stable_read_metadata(after) == _stable_read_metadata(before),
        "BLOCKED_SOURCE_DRIFT",
        f"{label} changed while being hashed",
    )
    _need(observed_bytes == before.st_size, "BLOCKED_SOURCE_DRIFT", f"{label} size drift")
    return observed_bytes, digest.hexdigest(), after


def _directory_entries(
    descriptor: int,
    label: str,
    relative_parent: str,
    seen_regular: set[tuple[int, int]],
    seen_directories: set[tuple[int, int]],
    *,
    sync: bool,
) -> tuple[_SealedEntry, ...]:
    root_before = os.fstat(descriptor)
    _need(stat.S_ISDIR(root_before.st_mode), "BLOCKED_SPECIAL_FILE", f"{label} is not a directory")
    root_identity = (root_before.st_dev, root_before.st_ino)
    _need(root_identity not in seen_directories, "BLOCKED_SOURCE_DRIFT", f"directory cycle in {label}")
    seen_directories.add(root_identity)
    names_before = tuple(sorted(os.listdir(descriptor)))
    entries: list[_SealedEntry] = []
    for name in names_before:
        try:
            name.encode("utf-8", errors="strict")
        except UnicodeEncodeError as error:
            raise PublicationError(
                "BLOCKED_PATH", f"non-UTF-8 directory entry in {label}"
            ) from error
        metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        relative = name if relative_parent == "" else f"{relative_parent}/{name}"
        _need(not stat.S_ISLNK(metadata.st_mode), "BLOCKED_SYMLINK", f"symlink in {label}: {name}")
        if stat.S_ISREG(metadata.st_mode):
            _need(metadata.st_nlink == 1, "BLOCKED_HARDLINK", f"hard link in {label}: {name}")
            identity = (metadata.st_dev, metadata.st_ino)
            _need(identity not in seen_regular, "BLOCKED_HARDLINK", f"inode alias in {label}: {name}")
            seen_regular.add(identity)
            flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
            child = os.open(name, flags, dir_fd=descriptor)
            try:
                opened = os.fstat(child)
                _need(
                    (opened.st_dev, opened.st_ino) == identity,
                    "BLOCKED_SOURCE_DRIFT",
                    f"file changed in {label}: {name}",
                )
                size, digest, sealed_metadata = _hash_regular_descriptor(
                    child, f"{label}/{name}", sync=sync
                )
            finally:
                os.close(child)
            named_after = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            _need(
                _stable_read_metadata(named_after) == _stable_read_metadata(sealed_metadata),
                "BLOCKED_SOURCE_DRIFT",
                f"file name changed in {label}: {name}",
            )
            entries.append(
                _SealedEntry(
                    relative,
                    "FILE",
                    sealed_metadata.st_dev,
                    sealed_metadata.st_ino,
                    sealed_metadata.st_mode,
                    size,
                    digest,
                )
            )
        elif stat.S_ISDIR(metadata.st_mode):
            child = _open_directory_at(descriptor, name)
            try:
                opened = os.fstat(child)
                _need(
                    (opened.st_dev, opened.st_ino) == (metadata.st_dev, metadata.st_ino),
                    "BLOCKED_SOURCE_DRIFT",
                    f"directory changed in {label}: {name}",
                )
                entries.append(
                    _SealedEntry(
                        relative,
                        "DIRECTORY",
                        opened.st_dev,
                        opened.st_ino,
                        opened.st_mode,
                        0,
                        "",
                    )
                )
                entries.extend(
                    _directory_entries(
                        child,
                        f"{label}/{name}",
                        relative,
                        seen_regular,
                        seen_directories,
                        sync=sync,
                    )
                )
            finally:
                os.close(child)
            named_after = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            _need(
                (named_after.st_dev, named_after.st_ino, named_after.st_mode)
                == (metadata.st_dev, metadata.st_ino, metadata.st_mode),
                "BLOCKED_SOURCE_DRIFT",
                f"directory name changed in {label}: {name}",
            )
        else:
            raise PublicationError("BLOCKED_SPECIAL_FILE", f"special file in {label}: {name}")
    names_after = tuple(sorted(os.listdir(descriptor)))
    root_after = os.fstat(descriptor)
    _need(names_after == names_before, "BLOCKED_SOURCE_DRIFT", f"entries changed in {label}")
    _need(
        _stable_read_metadata(root_after) == _stable_read_metadata(root_before),
        "BLOCKED_SOURCE_DRIFT",
        f"directory changed while being sealed: {label}",
    )
    if sync:
        os.fsync(descriptor)
    return tuple(entries)


def _seal_publication_at(
    parent_descriptor: int,
    name: str,
    lease: Lease,
    label: str,
    *,
    sync: bool,
) -> _StagingSeal:
    try:
        named_before = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except OSError as error:
        raise PublicationError("BLOCKED_STAGING_DRIFT", f"cannot inspect {label}: {error}") from error
    _need(not stat.S_ISLNK(named_before.st_mode), "BLOCKED_SYMLINK", f"{label} is a symlink")
    if stat.S_ISREG(named_before.st_mode):
        _need(named_before.st_nlink == 1, "BLOCKED_HARDLINK", f"{label} is hard-linked")
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        try:
            descriptor = os.open(name, flags, dir_fd=parent_descriptor)
        except OSError as error:
            raise PublicationError("BLOCKED_STAGING_DRIFT", f"cannot open {label}: {error}") from error
        try:
            data, current = _read_regular_descriptor(descriptor, label)
            if sync:
                os.fsync(descriptor)
        finally:
            os.close(descriptor)
        named_after = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        _need(
            _stable_read_metadata(named_after) == _stable_read_metadata(current)
            and (current.st_dev, current.st_ino) == (named_before.st_dev, named_before.st_ino),
            "BLOCKED_STAGING_DRIFT",
            f"{label} name or inode changed while being sealed",
        )
        payload = _decode_json(data)
        _need(isinstance(payload, Mapping), "BLOCKED_INVALID_JSON", f"{label} is not an object")
        _validate_publication_object(payload.get("publication"), lease)
        digest = hashlib.sha256(data).hexdigest()
        return _StagingSeal(
            False,
            current.st_dev,
            current.st_ino,
            current.st_mode,
            len(data),
            digest,
            (),
            digest,
        )
    _need(stat.S_ISDIR(named_before.st_mode), "BLOCKED_SPECIAL_FILE", f"{label} has wrong type")
    try:
        directory = _open_directory_at(parent_descriptor, name)
    except OSError as error:
        raise PublicationError("BLOCKED_STAGING_DRIFT", f"cannot open {label}: {error}") from error
    try:
        opened = os.fstat(directory)
        _need(
            (opened.st_dev, opened.st_ino) == (named_before.st_dev, named_before.st_ino),
            "BLOCKED_STAGING_DRIFT",
            f"{label} directory changed before open",
        )
        manifest_data, manifest_metadata = _read_at(directory, "manifest.json", f"{label} manifest")
        manifest = _decode_json(manifest_data)
        _need(isinstance(manifest, Mapping), "BLOCKED_INVALID_JSON", "manifest is not an object")
        _validate_publication_object(manifest.get("publication"), lease)
        entries = _directory_entries(
            directory,
            label,
            "",
            set(),
            set(),
            sync=sync,
        )
        manifest_digest = hashlib.sha256(manifest_data).hexdigest()
        matching_manifest = tuple(
            entry for entry in entries if entry.relative_path == "manifest.json"
        )
        _need(
            len(matching_manifest) == 1
            and matching_manifest[0].kind == "FILE"
            and matching_manifest[0].bytes == len(manifest_data)
            and matching_manifest[0].sha256 == manifest_digest
            and (matching_manifest[0].st_dev, matching_manifest[0].st_ino)
            == (manifest_metadata.st_dev, manifest_metadata.st_ino),
            "BLOCKED_STAGING_DRIFT",
            f"{label} manifest changed while sealing tree",
        )
        root_after = os.fstat(directory)
    finally:
        os.close(directory)
    named_after = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    _need(
        (named_after.st_dev, named_after.st_ino, named_after.st_mode)
        == (root_after.st_dev, root_after.st_ino, root_after.st_mode)
        and (root_after.st_dev, root_after.st_ino)
        == (named_before.st_dev, named_before.st_ino),
        "BLOCKED_STAGING_DRIFT",
        f"{label} directory name changed while being sealed",
    )
    tree_digest = hashlib.sha256(_encode_json([entry.record for entry in entries])).hexdigest()
    return _StagingSeal(
        True,
        root_after.st_dev,
        root_after.st_ino,
        root_after.st_mode,
        len(manifest_data),
        manifest_digest,
        entries,
        tree_digest,
    )


def _require_same_seal(
    parent_descriptor: int,
    name: str,
    lease: Lease,
    expected: _StagingSeal,
    label: str,
    *,
    sync: bool,
) -> _StagingSeal:
    current = _seal_publication_at(
        parent_descriptor, name, lease, label, sync=sync
    )
    _need(current == expected, "BLOCKED_STAGING_DRIFT", f"{label} seal changed")
    return current


def _pin_from_seal(destination: Path, seal: _StagingSeal) -> Pin:
    manifest_path = destination / "manifest.json" if seal.directory else destination
    return Pin(str(manifest_path), seal.manifest_bytes, seal.manifest_sha256)


def _prepare_staging(parent_descriptor: int, lease: Lease) -> _StagingSeal:
    return _seal_publication_at(
        parent_descriptor,
        lease.temp_path.name,
        lease,
        "staging",
        sync=True,
    )


def _current_pin(parent_descriptor: int, lease: Lease, destination: Path, directory: bool) -> Pin:
    seal = _seal_publication_at(
        parent_descriptor,
        destination.name,
        lease,
        "published artifact",
        sync=True,
    )
    _need(seal.directory is directory, "BLOCKED_PIN", "published artifact type changed")
    return _pin_from_seal(destination, seal)


def _rehash(callback: Callable[[], str], label: str) -> str:
    _need(callable(callback), "BLOCKED_DEPENDENCY", f"{label} callback is not callable")
    try:
        fingerprint = callback()
    except BaseException as error:
        raise PublicationError("BLOCKED_DEPENDENCY", f"{label} callback failed: {error}") from error
    _need(_is_lower_sha256(fingerprint), "BLOCKED_DEPENDENCY", f"{label} fingerprint is invalid")
    return fingerprint


def acquire_publication(
    role: str,
    phase: str,
    final_path: os.PathLike[str] | str,
    failure_path: os.PathLike[str] | str,
    expected_namespace: ExpectedNamespace | Mapping[str, object],
) -> Lease:
    """Acquire the one claim shared by success and handled-failure publication.

    ``expected_namespace`` contains the sorted completed direct-child names and
    the dependency fingerprint observed by the caller immediately before this
    call.  No destination or temporary artifact is created for a losing caller.
    """

    _need(role in ROLES, "BLOCKED_ROLE", f"unsupported role: {role!r}")
    _need(phase in PHASES, "BLOCKED_PHASE", f"unsupported phase: {phase!r}")
    final = _lexical_absolute(final_path, "final_path")
    failure = _lexical_absolute(failure_path, "failure_path")
    _need(final != failure and final.parent == failure.parent,
          "BLOCKED_PATH", "final and failure must be distinct siblings")
    _need(not final.name.startswith(".") and not failure.name.startswith("."),
          "BLOCKED_PATH", "terminal names cannot be hidden")
    if phase == "delivery-build":
        _need(
            role == "PRODUCER"
            and final.name == RUN_ID + "-delivery-manifest.json"
            and failure.name == RUN_ID + "-delivery-failure.json",
            "BLOCKED_PATH",
            "delivery-build requires the exact r4 manifest/failure pair",
        )
    elif final.suffix == ".json":
        expected_failure = final.with_name(final.name[:-5] + "-failure.json")
        _need(failure == expected_failure,
              "BLOCKED_PATH", "JSON failure path does not follow the r4 naming rule")
    namespace = _coerce_expected_namespace(expected_namespace)
    _need(final.name not in namespace.children and failure.name not in namespace.children,
          "BLOCKED_NAMESPACE", "current terminal path appears in prior namespace")
    parent_descriptor = _open_parent(final.parent)
    try:
        filesystem_type = _filesystem_type(final.parent)
        _scan_namespace(parent_descriptor, namespace.children)
        publisher = _pin_source()
        token = uuid.uuid4().hex
        claim = final.with_name(f".{final.name}.claim")
        temporary = final.with_name(f".{final.name}.tmp-{token}")
        _need(claim.name not in namespace.children and temporary.name not in namespace.children,
              "BLOCKED_NAMESPACE", "claim or temp collides with prior namespace")
        owner_pid = os.getpid()
        hostname = socket.gethostname()
        _need(hostname != "", "BLOCKED_CLAIM_OWNER", "hostname is empty")
        claim_payload = _claim_payload(
            role, phase, final, failure, token, owner_pid, hostname, publisher
        )
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        try:
            claim_descriptor = os.open(claim.name, flags, 0o600, dir_fd=parent_descriptor)
        except FileExistsError as error:
            raise PublicationError(
                "BLOCKED_NO_WRITE", "publication namespace already claimed"
            ) from error
        try:
            _write_all(claim_descriptor, _encode_json(claim_payload))
            os.fsync(claim_descriptor)
            claim_metadata = os.fstat(claim_descriptor)
        finally:
            os.close(claim_descriptor)
        os.fsync(parent_descriptor)
        lease = Lease(
            role=role,
            phase=phase,
            final_path=final,
            failure_path=failure,
            claim_path=claim,
            temp_path=temporary,
            token=token,
            claim_st_dev=claim_metadata.st_dev,
            claim_st_ino=claim_metadata.st_ino,
            filesystem_type=filesystem_type,
            publisher=publisher,
            dependency_fingerprint_at_acquire=namespace.dependency_fingerprint,
            expected_children=namespace.children,
            owner_pid=owner_pid,
            owner_hostname=hostname,
        )
        _validate_claim(parent_descriptor, lease)
        _probe_rename_no_replace(parent_descriptor, token)
        _scan_namespace(parent_descriptor, namespace.children + (claim.name,))
        return lease
    finally:
        os.close(parent_descriptor)


def publish_success(
    lease: Lease,
    staging_path: os.PathLike[str] | str,
    dependency_fingerprint: str,
    rehash_callback: Callable[[], str],
) -> Pin:
    """Publish the exact lease-owned file or directory as the success final."""

    _need(isinstance(lease, Lease), "BLOCKED_LEASE", "invalid publication lease")
    staging = _lexical_absolute(staging_path, "staging_path")
    _need(staging == lease.temp_path, "BLOCKED_LEASE", "staging path is not lease-owned")
    _need(dependency_fingerprint == lease.dependency_fingerprint_at_acquire,
          "BLOCKED_DEPENDENCY", "dependency fingerprint differs from acquisition")
    parent_descriptor = _open_parent(lease.final_path.parent)
    try:
        _need(_filesystem_type(lease.final_path.parent) == lease.filesystem_type,
              "BLOCKED_UNSUPPORTED_FILESYSTEM", "filesystem changed after acquisition")
        _validate_publisher(lease)
        _validate_claim(parent_descriptor, lease)
        _scan_namespace(
            parent_descriptor,
            lease.expected_children + (lease.claim_path.name, lease.temp_path.name),
        )
        staging_seal = _prepare_staging(parent_descriptor, lease)
        os.fsync(parent_descriptor)
        before_rename = _rehash(rehash_callback, "pre-rename")
        _need(before_rename == lease.dependency_fingerprint_at_acquire,
              "BLOCKED_DEPENDENCY", "dependency drift before rename")
        _validate_publisher(lease)
        _validate_claim(parent_descriptor, lease)
        _scan_namespace(
            parent_descriptor,
            lease.expected_children + (lease.claim_path.name, lease.temp_path.name),
        )
        _require_same_seal(
            parent_descriptor,
            lease.temp_path.name,
            lease,
            staging_seal,
            "staging before rename",
            sync=False,
        )
        try:
            _rename_no_replace(
                parent_descriptor,
                lease.temp_path.name,
                parent_descriptor,
                lease.final_path.name,
            )
        except FileExistsError as error:
            raise PublicationError("BLOCKED_NO_WRITE", "success final already exists") from error
        os.fsync(parent_descriptor)
        _scan_namespace(
            parent_descriptor,
            lease.expected_children + (lease.claim_path.name, lease.final_path.name),
        )
        final_seal = _require_same_seal(
            parent_descriptor,
            lease.final_path.name,
            lease,
            staging_seal,
            "published final",
            sync=False,
        )
        _need(lease.token not in _PUBLISHED_SEALS,
              "BLOCKED_PIN", "publication seal token is already registered")
        _PUBLISHED_SEALS[lease.token] = final_seal
        return _pin_from_seal(lease.final_path, final_seal)
    finally:
        os.close(parent_descriptor)


def publish_handled_failure(
    lease: Lease,
    failure_payload: Mapping[str, object],
    dependency_fingerprint: str,
    rehash_callback: Callable[[], str],
) -> Pin:
    """Serialize and publish one immutable handled-failure JSON artifact."""

    _need(isinstance(lease, Lease), "BLOCKED_LEASE", "invalid publication lease")
    _need(isinstance(failure_payload, Mapping),
          "BLOCKED_INVALID_JSON", "failure payload must be an object")
    _validate_publication_object(failure_payload.get("publication"), lease)
    _need(dependency_fingerprint == lease.dependency_fingerprint_at_acquire,
          "BLOCKED_DEPENDENCY", "dependency fingerprint differs from acquisition")
    encoded = _encode_json(dict(failure_payload))
    parent_descriptor = _open_parent(lease.failure_path.parent)
    try:
        _need(_filesystem_type(lease.failure_path.parent) == lease.filesystem_type,
              "BLOCKED_UNSUPPORTED_FILESYSTEM", "filesystem changed after acquisition")
        _validate_publisher(lease)
        _validate_claim(parent_descriptor, lease)
        _scan_namespace(parent_descriptor, lease.expected_children + (lease.claim_path.name,))
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(lease.temp_path.name, flags, 0o600, dir_fd=parent_descriptor)
        try:
            _write_all(descriptor, encoded)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        staging_seal = _seal_publication_at(
            parent_descriptor,
            lease.temp_path.name,
            lease,
            "handled-failure staging",
            sync=True,
        )
        _need(
            not staging_seal.directory
            and staging_seal.manifest_bytes == len(encoded)
            and staging_seal.manifest_sha256 == hashlib.sha256(encoded).hexdigest(),
            "BLOCKED_STAGING_DRIFT",
            "handled-failure staging differs from intended payload",
        )
        _scan_namespace(
            parent_descriptor,
            lease.expected_children + (lease.claim_path.name, lease.temp_path.name),
        )
        os.fsync(parent_descriptor)
        before_rename = _rehash(rehash_callback, "pre-rename")
        _need(before_rename == lease.dependency_fingerprint_at_acquire,
              "BLOCKED_DEPENDENCY", "dependency drift before failure rename")
        _validate_publisher(lease)
        _validate_claim(parent_descriptor, lease)
        _scan_namespace(
            parent_descriptor,
            lease.expected_children + (lease.claim_path.name, lease.temp_path.name),
        )
        _require_same_seal(
            parent_descriptor,
            lease.temp_path.name,
            lease,
            staging_seal,
            "handled-failure staging before rename",
            sync=False,
        )
        try:
            _rename_no_replace(
                parent_descriptor,
                lease.temp_path.name,
                parent_descriptor,
                lease.failure_path.name,
            )
        except FileExistsError as error:
            raise PublicationError("BLOCKED_NO_WRITE", "handled failure already exists") from error
        os.fsync(parent_descriptor)
        _scan_namespace(
            parent_descriptor,
            lease.expected_children + (lease.claim_path.name, lease.failure_path.name),
        )
        final_seal = _require_same_seal(
            parent_descriptor,
            lease.failure_path.name,
            lease,
            staging_seal,
            "published handled failure",
            sync=False,
        )
        _need(lease.token not in _PUBLISHED_SEALS,
              "BLOCKED_PIN", "publication seal token is already registered")
        _PUBLISHED_SEALS[lease.token] = final_seal
        return _pin_from_seal(lease.failure_path, final_seal)
    finally:
        os.close(parent_descriptor)


def release_verified_claim(
    lease: Lease,
    published_pin: Pin,
    post_publication_fingerprint: str,
) -> None:
    """Remove the claim after publication and dependency stability are proven."""

    _need(isinstance(lease, Lease), "BLOCKED_LEASE", "invalid publication lease")
    _need(isinstance(published_pin, Pin), "BLOCKED_PIN", "invalid published pin")
    _need(_is_lower_sha256(post_publication_fingerprint),
          "BLOCKED_DEPENDENCY", "post-publication fingerprint is invalid")
    _need(post_publication_fingerprint == lease.dependency_fingerprint_at_acquire,
          "BLOCKED_DEPENDENCY", "dependency drift after publication")
    parent_descriptor = _open_parent(lease.final_path.parent)
    try:
        _need(_filesystem_type(lease.final_path.parent) == lease.filesystem_type,
              "BLOCKED_UNSUPPORTED_FILESYSTEM", "filesystem changed before claim release")
        _validate_publisher(lease)
        _validate_claim(parent_descriptor, lease)
        final_exists = _entry_exists(parent_descriptor, lease.final_path.name)
        failure_exists = _entry_exists(parent_descriptor, lease.failure_path.name)
        _need(final_exists is not failure_exists,
              "BLOCKED_NAMESPACE", "exactly one terminal publication is required")
        destination = lease.final_path if final_exists else lease.failure_path
        metadata = os.stat(destination.name, dir_fd=parent_descriptor, follow_symlinks=False)
        directory = stat.S_ISDIR(metadata.st_mode)
        expected_seal = _PUBLISHED_SEALS.get(lease.token)
        _need(expected_seal is not None, "BLOCKED_PIN", "published seal is unavailable")
        current_seal = _seal_publication_at(
            parent_descriptor,
            destination.name,
            lease,
            "published artifact before claim release",
            sync=True,
        )
        _need(current_seal.directory is directory,
              "BLOCKED_PIN", "published artifact type changed")
        _need(current_seal == expected_seal,
              "BLOCKED_PIN", "published artifact seal changed")
        current_pin = _pin_from_seal(destination, current_seal)
        _need(current_pin == published_pin, "BLOCKED_PIN", "published bytes changed")
        _scan_namespace(
            parent_descriptor,
            lease.expected_children + (lease.claim_path.name, destination.name),
        )
        claim_descriptor = os.open(
            lease.claim_path.name,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_descriptor,
        )
        try:
            claim_metadata = os.fstat(claim_descriptor)
            _need(
                (claim_metadata.st_dev, claim_metadata.st_ino)
                == (lease.claim_st_dev, lease.claim_st_ino),
                "BLOCKED_CLAIM_OWNER",
                "claim inode changed immediately before unlink",
            )
            claim_data, _ = _read_regular_descriptor(claim_descriptor, "publication claim")
            claim_payload = _decode_json(claim_data)
            _need(isinstance(claim_payload, Mapping)
                  and claim_payload.get("token") == lease.token
                  and claim_payload.get("pid") == lease.owner_pid,
                  "BLOCKED_CLAIM_OWNER", "claim token or PID changed")
            current_name_metadata = os.stat(
                lease.claim_path.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            _need(
                (current_name_metadata.st_dev, current_name_metadata.st_ino)
                == (lease.claim_st_dev, lease.claim_st_ino),
                "BLOCKED_CLAIM_OWNER",
                "claim name no longer identifies the owner inode",
            )
            os.unlink(lease.claim_path.name, dir_fd=parent_descriptor)
        finally:
            os.close(claim_descriptor)
        os.fsync(parent_descriptor)
        _scan_namespace(parent_descriptor, lease.expected_children + (destination.name,))
        _PUBLISHED_SEALS.pop(lease.token, None)
    finally:
        os.close(parent_descriptor)


__all__ = [
    "ExpectedNamespace",
    "Lease",
    "Pin",
    "PublicationError",
    "acquire_publication",
    "publish_success",
    "publish_handled_failure",
    "release_verified_claim",
]
