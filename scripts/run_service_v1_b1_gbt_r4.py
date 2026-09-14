"""Host-side, fail-closed r4 server runner for service-v1 B1 Spark GBT.

Public actions cover preflight through confirmation. Reviews are written by an
independent implementation; this module validates their gates but never grants
itself PASS.
"""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import hashlib
import importlib.util
import json
import math
import os
import platform
import re
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq


SCHEMA_VERSION = "feelm-service-v1-b1-r4-host-runner/2"
RECOVERY_PLAN_SHA256 = "3a38ad29c494bb3d1f3b6e94a1a7188fb9b8cdd37a132e25f115ad43eddac8eb"
PROFILE_SHA256 = "56e06e311566c23181b4483200d55049d66b728efcbf12f7a7047c80ce5b7dd5"
R3_PLAN_SHA256 = "4de13f041532b7249b22f5a2bbfbd7919f79d94e4c25f2ae16fe95104a2b3d9a"
R2_PLAN_SHA256 = "c5b90b0d19374588fc5cb7a1d98cd6510a1c3708df9667131df5eb3826c79d49"
TEAM_COMMIT = "96a4b27d0ce5c0d4e4fe0348b6c01de0b06f6f7e"
IMAGE = "feelm-rec046-spark:local"
IMAGE_ID = "sha256:de611d62db982bb833b0737a7ddd72a7cd49ab1a7dc9128669cdabae39f46df8"
PHASE_TIMEOUT_SECONDS = {"preflight-dry": 7_200, "preflight-full": 7_200, "fit": 28_800, "score": 7_200}
PHASE_REQUIRED_WINDOW_SECONDS = {"preflight-dry": 16_200, "preflight-full": 9_000, "fit": 32_400, "score": 9_000}
TIMEOUT_SECONDS = PHASE_TIMEOUT_SECONDS["fit"]
MAX_MEMORY_BYTES = 20 * 1024**3
SOURCE_ROWS = 4_997_069
LOGICAL_ROWS = 19_988_276
SCORE_ROWS = 93_230
PARTITIONS = 8
R2_RUN_ID = "b1-gbt120-s339-v1-r2"
R3_RUN_ID = "b1-gbt120-s339-v1-r3-local4c12g-t14400"
RUN_ID = "b1-gbt120-s339-v1-r4-server5c20g-t28800"
PROFILE_ID = "ec2-8vcpu32g-local5-fit20g-t28800-v1"
R2_FIT_FAILURE_BYTES = 24_854
R2_FIT_FAILURE_SHA256 = "4ad571439af7f290dd32ce27922519f31e3b156f90998b26a69e2c892aba7e37"
R2_OUTER_RUNNER_BYTES = 88_623
R2_OUTER_RUNNER_SHA256 = "cbc8983a0047b5aa0a2fc5a2d3794080afcf48583fe1be90c6d2dd3dd5af6f8e"
R2_PREFLIGHT_MANIFEST_BYTES = 2_351
R2_PREFLIGHT_MANIFEST_SHA256 = "7a47a40ac923ba1f8a638ad6029aa72349245b65055f3db8915a2327de174e01"
R2_PREFLIGHT_REVIEW_BYTES = 8_706
R2_PREFLIGHT_REVIEW_SHA256 = "cc49688618bfd408a957a22522456912320519d981c34ad87aefb190a0910132"
R2_TRAINING_SOURCE_SET_SHA256 = "6d82b745ec4e796f823f6506497ad9a15236f92a91a04d3452c4755fada499f9"
MODEL_INPUT_SET_SHA256 = "cd41a8ac341cdbc1435bbce21bd78e3b3012e2c50ca25cfac0ef034088b1ccfd"
WORKER_RUNTIME_SET_SHA256 = "ff90bcd014b8ac498f2c5ccba27eb33d0a83f3ee062d36dc426e33a95c6a0a0a"
EXPECTED_WORKER_SHA256 = "9708e0b3fdc618c5df2240c9e1dcf368fd4f6fa411d7915c31104b3af2146d42"
CGROUP_SAMPLE_INTERVAL_SECONDS = 2.0
R3_PROFILE_ID = "local4c12g-t14400-v1"
R3_FIT_FAILURE_BYTES = 100_384
R3_FIT_FAILURE_SHA256 = "75ad1999a8e24d723c56e0b8ecd4f96a573c2086b86daf675d68ebf889940276"
R3_FIT_FAILURE_REVIEW_BYTES = 8_184
R3_FIT_FAILURE_REVIEW_SHA256 = "4c76189254643d58bb1045c89aa879b64be06ddbce9e88294ae7d6229836191d"
R3_PREFLIGHT_REVIEW_BYTES = 11_801
R3_PREFLIGHT_REVIEW_SHA256 = "0d397589778aff2a04d2df2101ac61344e6ce3e7f9d2e204e47156611f92413a"
R3_IMPLEMENTATION_PINS: dict[str, tuple[int, str]] = {
    "docs/recommendation/plans/service-v1-b1-r3-fit-recovery.md": (22_087, R3_PLAN_SHA256),
    "docs/recommendation/plans/service-v1-b1-r3-local4c12g-t14400-profile.json": (
        1_522, "761c210364ab465f9143ded5dba8c2f3fba9a1d34c10f1ea54d742a528494c23"
    ),
    "scripts/run_service_v1_b1_gbt_r3.py": (
        143_994, "783a5a4c7f1783416271de0a1cde6a6be55409ef8b625a757f8a07c153c7a574"
    ),
    "scripts/audit_service_v1_b1_spark_outputs_r3.py": (
        89_009, "92a9e48597205f54509ccd7fbd9c717cb791bbd31a6998cb9d966177f830d3be"
    ),
    "scripts/evaluate_service_v1_b1_r3.py": (
        215_832, "de036adc7b35d151aeaf8006e6a2a64f86130f0d9f49b80ac8fa15ec336fc87a"
    ),
    "scripts/audit_service_v1_b1_evaluation_outputs_r3.py": (
        165_251, "7f44c00ade107dd82e54658c39d765df2aa8ac7cfddc8e650ba4bebb230fa28c"
    ),
    "tests/test_service_v1_b1_gbt_runner_r3.py": (
        25_063, "c348c6d02741ffe4a30635c3a2fc0285c8503f15618cac75b3c65943582145af"
    ),
    "tests/test_audit_service_v1_b1_spark_outputs_r3.py": (
        19_497, "12af534b89383318cdc6b8fb27610dca58880737b4b7a970825ee8a70a9a9d18"
    ),
    "tests/test_evaluate_service_v1_b1_r3.py": (
        116_597, "5a69d17ada3074c34ffbdce3e14c18333fc163b58ebf80b6ba59b5dc9b673494"
    ),
    "tests/test_audit_service_v1_b1_evaluation_outputs_r3.py": (
        29_865, "5bb03fd080f4e48ea8c38a142c60095d0448245433808c01174b818602a349b4"
    ),
}
R3_NORMALIZED_EVALUATION_AUDITOR_SHA256 = "fdd756107c11d19c6332274017bb8ed4933ddb7f377023b18e43f7d748eaaf50"

EXPECTED_FILE_PINS: dict[str, tuple[int, str]] = {
    "contract/training-recipe.v1.json": (
        15_764,
        "d403a27fab09453f98b9988fcfb3867b83e41ae217f9f5f3be5976321eef8d7b",
    ),
    "contract/service-v1.json": (
        15_504,
        "1a9ba0cd0101f6d065227c37ee642a3fc75c2c69b34c0da8fa6dacff351c2343",
    ),
    "contract/MODELS.md": (
        25_079,
        "5ad4c852b89ca02da00c30f0fc184aa6018db4abf1808c057772a4908d376701",
    ),
    "contract/feature-schema.v1.json": (
        39_243,
        "fda2be4f40b76e46b88dbb53523ef404bbf8a13c68bbf012acc58da9a63948ca",
    ),
    "source/natural-train.parquet": (
        832_717_601,
        "9d8d33a252991c032704d4072003b4fb9f400136f3c411a2698ae5fee592fa45",
    ),
    "source/tmdb-masked-train.parquet": (
        891_461_814,
        "27aee771597ba230654b2e99c1eea047fd25e3265161c51b6873474d47c65f01",
    ),
    "source/masked-manifest.json": (
        5_176,
        "82eb3635f1ae914a056c2813c4786dfa2de56abbb37aaf5ce852685f6eb6a557",
    ),
    "source/views-manifest.json": (
        1_710,
        "df8dd8bbfea4a71e506958c5b7e1499b5010350a326cbfac2c9bfe23a607792a",
    ),
    "source/masked-review.json": (
        10_174,
        "f7e322206d95c8ad418926a08394625f84a25b611f4e9595c97dd03cca6c7b1c",
    ),
    "source/natural-score.parquet": (
        2_985_357,
        "9832425537943823f524ae6730aa1059b77d2c0464664f1dc29357bf95078f9b",
    ),
}

PREFLIGHT_REVIEW_TARGETS = {
    "manifest": "manifest.json",
    "input_lock": "input-lock.json",
    "recovery_reference": "recovery-reference.json",
    "partition_identity": "partition-identity.json",
    "command": "command.json",
    "resource": "resource.json",
    "run_log": "run.log",
    "execution_profile": "execution-profile.json",
    "delivery_reference": "delivery-reference.json",
    "server_receipt_reference": "server-receipt-reference.json",
    "host_gate_dry": "host-gate-dry.json",
    "host_gate_full": "host-gate-full.json",
}
FIT_REVIEW_TARGETS = {
    "manifest": "manifest.json",
    "input_lock": "input-lock.json",
    "preflight_reference": "preflight-reference.json",
    "command": "command.json",
    "partition_identity": "partition-identity.json",
    "resolved_estimator": "resolved-estimator.json",
    "model_file_inventory": "model-file-inventory.json",
    "threshold_fixtures": "threshold-fixtures.npz",
    "fit_metrics": "fit-metrics.json",
    "resource": "resource.json",
    "run_log": "run.log",
    "execution_profile": "execution-profile.json",
    "recovery_reference": "recovery-reference.json",
    "delivery_reference": "delivery-reference.json",
    "server_receipt_reference": "server-receipt-reference.json",
    "host_gate": "host-gate.json",
}
SCORE_REVIEW_TARGETS = {
    "manifest": "manifest.json",
    "fit_reference": "fit-reference.json",
    "score_input_lock": "score-input-lock.json",
    "command": "command.json",
    "analyzed_plan": "score/analyzed-plan.txt",
    "predictions": "score/predictions.parquet",
    "resource": "resource.json",
    "run_log": "run.log",
    "execution_profile": "execution-profile.json",
    "recovery_reference": "recovery-reference.json",
    "delivery_reference": "delivery-reference.json",
    "server_receipt_reference": "server-receipt-reference.json",
    "host_gate": "host-gate.json",
}
FORBIDDEN_EVALUATION_BASENAMES = frozenset({"labels.parquet", "evaluation-seal.json"})
R2_PREFLIGHT_INVENTORY: dict[str, tuple[int, str]] = {
    "command.json": (7_720, "86e05c4b3555297c9a803b91cea840b2f7ea83286a2a82591a5eb1f950b15859"),
    "input-lock.json": (3_489, "fbd4ae898a8cc35e3e4e7c4442298d6940dc88b3854d20182b7f38c87334d775"),
    "manifest.json": (2_351, R2_PREFLIGHT_MANIFEST_SHA256),
    "partition-identity.json": (2_489, "2a7f7fbd9000730db749cc673d9a43a5027fa3ca76d517cda826f3f420d45c99"),
    "recovery-reference.json": (959, "9c7b39b342743e40461f5d5b9c8f0c02268d3c4f82c177ac39b4f97ad8b4133d"),
    "resource.json": (1_565, "a91445352b3fd2f53b84106667686f1d8778ef5b4399d2c15631c7844b634ffd"),
    "run.log": (12_955, "52a3c6a0f78b293f11ec2af40d2cd23089d695f4c063bebb6a416aab0f87a063"),
}
R3_PREFLIGHT_INVENTORY: dict[str, tuple[int, str]] = {
    "command.json": (8_653, "1c8979c7d60474e871fb3220194fca5eba39b186ad016a6cee7d2d5fc33134eb"),
    "execution-profile.json": (2_283, "6074818bf05b640ba3ff2b83a2d03d714635761d7e1f317ceefd1332332f4273"),
    "input-lock.json": (6_493, "9a3f678ec0fc454498cbb7dbb54973c4c2b9e3eceaee2d3238b96d51cd15f460"),
    "manifest.json": (3_149, "c2f2e7914bd5a222001759d1ecd40119a2fa7a8d17ea3214847050e8535da407"),
    "partition-identity.json": (2_489, "2a7f7fbd9000730db749cc673d9a43a5027fa3ca76d517cda826f3f420d45c99"),
    "recovery-reference.json": (5_314, "d35f8ecd0c8c5fca4981fc25b2e550f919c197abf37ca35d823ce603eab9f96d"),
    "resource.json": (8_851, "bf5714a12c394c835d00f18d473772e00da758b4c2998d2175d7f437a120bace"),
    "run.log": (12_961, "be8521910c5506e181ca01e6864885056e88542cbbb0d14e7b6d6e0979628171"),
}

R4_REVIEW_BASENAMES = {
    "design": RUN_ID + "-design-result-review.json",
    "implementation": RUN_ID + "-implementation-result-review.json",
    "delivery": RUN_ID + "-delivery-manifest-result-review.json",
    "receipt": RUN_ID + "-server-receipt-result-review.json",
    "preflight": RUN_ID + "-preflight-result-review.json",
    "fit": RUN_ID + "-fit-result-review.json",
    "score": RUN_ID + "-score-result-review.json",
}


class ContainerExecutionError(RuntimeError):
    def __init__(self, message: str, evidence: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.evidence = dict(evidence or {})


class ContainerCleanupError(ContainerExecutionError):
    pass


class PhasePublicationAcquisitionError(RuntimeError):
    """Marks an acquire attempt that must never be retried in the same ordinal."""



def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def pin_file(path: Path, logical_path: str | None = None) -> dict[str, Any]:
    lexical = path.absolute()
    require(os.path.lexists(lexical), f"missing file: {path}")
    file_stat = os.lstat(lexical)
    require(not lexical.is_symlink() and lexical.is_file(), f"missing or linked file: {path}")
    require(file_stat.st_nlink == 1, f"hard-link alias forbidden: {path}")
    for current in (lexical, *lexical.parents):
        require(
            not current.is_symlink() and not (hasattr(current, "is_junction") and current.is_junction()),
            f"link/reparse path forbidden: {path}",
        )
    value: dict[str, Any] = {
        "bytes": file_stat.st_size,
        "sha256": sha256_file(lexical),
    }
    if logical_path is not None:
        value["path"] = logical_path
    return value


def require_unlinked_path(path: Path, root: Path, *, directory: bool = False) -> None:
    lexical = path.absolute()
    lexical_root = root.absolute()
    require(lexical == lexical_root or lexical_root in lexical.parents, f"path escapes expected root: {path}")
    require(lexical.is_dir() if directory else lexical.is_file(), f"missing regular {'directory' if directory else 'file'}: {path}")
    if not directory:
        require(os.lstat(lexical).st_nlink == 1, f"hard-link alias forbidden: {path}")
    current = lexical
    while True:
        require(
            not current.is_symlink() and not (hasattr(current, "is_junction") and current.is_junction()),
            f"link/reparse path forbidden: {path}",
        )
        if current == lexical_root:
            break
        require(current.parent != current, f"expected root not reached: {path}")
        current = current.parent


def virtual_pin(logical_path: str, value: str) -> dict[str, Any]:
    raw = value.encode("utf-8")
    return {"path": logical_path, "bytes": len(raw), "sha256": sha256_bytes(raw), "value": value}


def verify_expected_pin(path: Path, logical_path: str) -> dict[str, Any]:
    require(logical_path in EXPECTED_FILE_PINS, f"no expected pin for {logical_path}")
    expected_bytes, expected_sha = EXPECTED_FILE_PINS[logical_path]
    actual = pin_file(path, logical_path)
    require(
        actual["bytes"] == expected_bytes and actual["sha256"] == expected_sha,
        f"immutable pin drift for {logical_path}: {actual}",
    )
    return actual


def canonical_record_set_sha256(records: Iterable[Mapping[str, Any]]) -> str:
    normalized = []
    for record in records:
        path = record.get("path")
        size = record.get("bytes")
        digest = record.get("sha256")
        require(isinstance(path, str) and path != "" and "\x00" not in path, "invalid canonical record path")
        require(isinstance(size, int) and size >= 0, f"invalid canonical record size: {path}")
        require(isinstance(digest, str) and len(digest) == 64, f"invalid canonical record hash: {path}")
        normalized.append((path, size, digest.lower()))
    normalized.sort(key=lambda value: value[0])
    require(len({row[0] for row in normalized}) == len(normalized), "duplicate canonical record path")
    payload = b"".join(
        path.encode("utf-8")
        + b"\x00"
        + str(size).encode("ascii")
        + b"\x00"
        + digest.encode("ascii")
        + b"\n"
        for path, size, digest in normalized
    )
    return sha256_bytes(payload)


def implementation_set_sha256(outer_pin: Mapping[str, Any], worker_pin: Mapping[str, Any]) -> str:
    return canonical_record_set_sha256([outer_pin, worker_pin])


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON value: {value}")


def _validate_json_values(value: Any, where: str = "$") -> None:
    if value is None:
        raise ValueError(f"JSON null is forbidden at {where}")
    if isinstance(value, float):
        require(math.isfinite(value), f"non-finite number at {where}")
    elif isinstance(value, dict):
        for key, child in value.items():
            require(isinstance(key, str), f"non-string JSON key at {where}")
            _validate_json_values(child, f"{where}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_json_values(child, f"{where}[{index}]")


def load_json(path: Path, *, allow_null: bool = True, require_lf: bool = False) -> dict[str, Any]:
    raw = pin_file(path)
    data = path.read_bytes()
    require(
        len(data) == raw["bytes"] and sha256_bytes(data) == raw["sha256"],
        f"JSON changed while reading: {path}",
    )
    require(data.endswith(b"\n"), f"JSON must have a trailing newline: {path}")
    if require_lf:
        require(b"\r" not in data, f"JSON must use LF line endings: {path}")
    value = json.loads(
        data.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs, parse_constant=_reject_json_constant
    )
    require(isinstance(value, dict), f"JSON root must be an object: {path}")
    if not allow_null:
        _validate_json_values(value)
    return value


def write_json_exclusive(path: Path, value: Any) -> None:
    require(not os.path.lexists(path), f"refusing to replace existing file or link: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)


def fsync_file(path: Path) -> None:
    with path.open("r+b") as stream:
        os.fsync(stream.fileno())


def fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def relative_inventory(root: Path, *, exclude: Sequence[str] = ()) -> dict[str, dict[str, Any]]:
    excluded = set(exclude)
    result: dict[str, dict[str, Any]] = {}
    for path in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        if relative in excluded:
            continue
        result[relative] = pin_file(path)
    return result


def rename_no_replace(source: Path, target: Path) -> None:
    """Atomically publish only if target is absent."""
    if os.name == "nt":
        os.rename(source, target)
        return
    import ctypes
    library = ctypes.CDLL(None, use_errno=True)
    function = getattr(library, "renameat2", None)
    require(function is not None, "atomic no-replace rename unavailable on this platform")
    result = function(-100, os.fsencode(source), -100, os.fsencode(target), 1)
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(target))


def atomic_publish_directory(stage: Path, final: Path) -> None:
    require(stage.is_dir(), f"missing staging directory: {stage}")
    require(not os.path.lexists(final), f"immutable output already exists: {final}")
    for path in (item for item in stage.rglob("*") if item.is_file()):
        fsync_file(path)
    fsync_directory(stage)
    rename_no_replace(stage, final)
    fsync_directory(final.parent)


def atomic_write_sibling(path: Path, value: Any) -> None:
    require(not os.path.lexists(path), f"immutable sibling already exists: {path}")
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        write_json_exclusive(temporary, value)
        rename_no_replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        if os.path.lexists(temporary):
            temporary.unlink()


def _publication_api() -> Any:
    expected = Path(__file__).resolve().with_name("service_v1_b1_r4_publication.py")
    name = "service_v1_b1_r4_publication"
    if name in sys.modules:
        module = sys.modules[name]
        require(Path(str(getattr(module, "__file__", ""))).resolve() == expected,
                "r4 publication module origin drift")
    else:
        specification = importlib.util.spec_from_file_location(name, expected)
        require(specification is not None and specification.loader is not None,
                "r4 publication module is unavailable")
        module = importlib.util.module_from_spec(specification)
        sys.modules[name] = module
        specification.loader.exec_module(module)
        require(Path(str(getattr(module, "__file__", ""))).resolve() == expected,
                "r4 publication module origin drift")
    for name in (
        "acquire_publication", "publish_success", "publish_handled_failure", "release_verified_claim"
    ):
        require(callable(getattr(module, name, None)), f"r4 publication API missing: {name}")
    return module


def _lease_path(lease: Any, *names: str) -> Path:
    for name in names:
        value = getattr(lease, name, None)
        if value is not None:
            return Path(value)
    if isinstance(lease, Mapping):
        for name in names:
            if name in lease:
                return Path(lease[name])
    raise ValueError(f"publication lease lacks one of {names}")


def publication_evidence(lease: Any) -> dict[str, Any]:
    for name in ("publication", "evidence", "publication_evidence"):
        value = getattr(lease, name, None)
        if callable(value):
            value = value()
        if isinstance(value, Mapping):
            return dict(value)
    if isinstance(lease, Mapping):
        for name in ("publication", "evidence", "publicationEvidence"):
            if isinstance(lease.get(name), Mapping):
                return dict(lease[name])
    raise ValueError("publication lease does not expose pre-rename evidence")


@dataclass(frozen=True)
class DependencySnapshot:
    """Physical inputs and their canonical record-set digest.

    Publication callbacks must re-open the same paths instead of returning a
    cached digest.  The Docker image identity is the sole virtual record and is
    re-probed on every callback.
    """

    paths: "ExecutionPaths"
    records: tuple[dict[str, Any], ...]
    fingerprint: str

    def rehash(self) -> str:
        current: list[dict[str, Any]] = []
        for expected in self.records:
            logical = str(expected["path"])
            if logical == "runtime/docker-image-id":
                observed = virtual_pin(logical, inspect_image_id())
            else:
                try:
                    physical = _record_physical_path(self.paths, logical)
                except ValueError:
                    physical = resolve_logical_path(self.paths, logical)
                observed = pin_file(physical, logical)
            require(observed == expected, f"publication dependency drift: {logical}")
            current.append(observed)
        return canonical_record_set_sha256(current)


def dependency_snapshot(paths: "ExecutionPaths", records: Iterable[Mapping[str, Any]]) -> DependencySnapshot:
    copied = tuple(dict(record) for record in records)
    logical = [record.get("path") for record in copied]
    require(len(logical) == len(set(logical)), "publication dependency path overlap")
    fingerprint = canonical_record_set_sha256(copied)
    snapshot = DependencySnapshot(paths=paths, records=copied, fingerprint=fingerprint)
    require(snapshot.rehash() == fingerprint, "publication dependency acquisition drift")
    return snapshot


def namespace_children(paths: "ExecutionPaths") -> list[str]:
    if not paths.output_parent.is_dir():
        return []
    prefix = RUN_ID + "-"
    hidden = "." + prefix
    return sorted(
        child.name for child in paths.output_parent.iterdir()
        if child.name.startswith(prefix) or child.name.startswith(hidden)
    )


def expected_namespace_before(phase: str) -> tuple[str, ...]:
    base = [
        R4_REVIEW_BASENAMES["design"],
        R4_REVIEW_BASENAMES["implementation"],
        RUN_ID + "-delivery-manifest.json",
        R4_REVIEW_BASENAMES["delivery"],
        RUN_ID + "-server-receipt",
        R4_REVIEW_BASENAMES["receipt"],
    ]
    if phase == "preflight":
        return tuple(base)
    base.extend([RUN_ID + "-preflight", R4_REVIEW_BASENAMES["preflight"]])
    if phase == "fit":
        return tuple(base)
    base.extend([RUN_ID + "-fit", R4_REVIEW_BASENAMES["fit"]])
    if phase == "score":
        return tuple(base)
    base.extend([RUN_ID + "-score", R4_REVIEW_BASENAMES["score"]])
    if phase == "calibrate-select":
        return tuple(base)
    base.extend([RUN_ID + "-selection", RUN_ID + "-selection-result-review.json"])
    require(phase == "confirmation", f"unsupported namespace phase: {phase}")
    return tuple(base)


def acquire_phase_publication(
    paths: "ExecutionPaths", phase: str, dependencies: DependencySnapshot
) -> tuple[Any, Path]:
    expected = expected_namespace_before(phase)
    try:
        require(namespace_children(paths) == sorted(expected), f"r4 namespace drift before {phase}")
        final = paths.bundle(phase)
        failure = paths.failure(phase)
        lease = _publication_api().acquire_publication(
            "PRODUCER",
            phase,
            final,
            failure,
            {"children": list(expected), "dependencyFingerprint": dependencies.fingerprint},
        )
        staging = _lease_path(lease, "temp_path", "tempPath", "staging_path", "stagingPath")
        require(staging.parent == final.parent, "publication staging parent drift")
    except BaseException as error:
        raise PhasePublicationAcquisitionError(
            f"publication acquisition failed for {phase}: {error}") from error
    return lease, staging


def publish_phase_success(
    lease: Any,
    staging: Path,
    dependency_fingerprint: str,
    rehash_callback: Any,
) -> dict[str, Any]:
    require(re.fullmatch(r"[0-9a-f]{64}", dependency_fingerprint) is not None, "invalid dependency fingerprint")
    api = _publication_api()
    published = api.publish_success(lease, staging, dependency_fingerprint, rehash_callback)
    post_fingerprint = rehash_callback()
    require(post_fingerprint == dependency_fingerprint, "dependency drift after publication")
    api.release_verified_claim(lease, published, post_fingerprint)
    if isinstance(published, Mapping):
        return dict(published)
    record = getattr(published, "record", None)
    if isinstance(record, Mapping):
        return dict(record)
    return {"path": str(getattr(published, "path")), "bytes": int(getattr(published, "bytes")),
            "sha256": str(getattr(published, "sha256"))}


@dataclass(frozen=True)
class ExecutionPaths:
    standalone: Path
    team: Path

    @classmethod
    def from_roots(cls, standalone: Path, team: Path) -> "ExecutionPaths":
        return cls(standalone=standalone.resolve(), team=team.resolve())

    @property
    def plan(self) -> Path:
        return self.standalone / "docs/recommendation/plans/service-v1-b1-r4-server-fit-recovery.md"

    @property
    def r2_plan(self) -> Path:
        return self.standalone / "docs/recommendation/plans/service-v1-b1-spark-runner.md"

    @property
    def r3_plan(self) -> Path:
        return self.standalone / "docs/recommendation/plans/service-v1-b1-r3-fit-recovery.md"

    @property
    def r3_profile(self) -> Path:
        return self.standalone / "docs/recommendation/plans/service-v1-b1-r3-local4c12g-t14400-profile.json"

    @property
    def profile_contract(self) -> Path:
        return self.standalone / "docs/recommendation/plans/service-v1-b1-r4-ec2-8vcpu32g-local5c20g-t28800-profile.json"

    @property
    def outer_runner(self) -> Path:
        return self.standalone / "scripts/run_service_v1_b1_gbt_r4.py"

    @property
    def r2_outer_runner(self) -> Path:
        return self.standalone / "scripts/run_service_v1_b1_gbt.py"

    @property
    def r3_outer_runner(self) -> Path:
        return self.standalone / "scripts/run_service_v1_b1_gbt_r3.py"

    @property
    def r2_auditor(self) -> Path:
        return self.standalone / "scripts/audit_service_v1_b1_spark_outputs.py"

    @property
    def spark_auditor(self) -> Path:
        return self.standalone / "scripts/audit_service_v1_b1_spark_outputs_r4.py"

    @property
    def r3_auditor(self) -> Path:
        return self.standalone / "scripts/audit_service_v1_b1_spark_outputs_r3.py"

    @property
    def spark_worker(self) -> Path:
        return self.standalone / "scripts/service_v1_b1_spark_worker.py"

    @property
    def tests(self) -> Path:
        return self.standalone / "tests/test_service_v1_b1_gbt_runner_r4.py"

    @property
    def r3_preflight(self) -> Path:
        return self.output_parent / (R3_RUN_ID + "-preflight")

    @property
    def r3_preflight_review(self) -> Path:
        return self.output_parent / (R3_RUN_ID + "-preflight-result-review.json")

    @property
    def r3_fit_failure(self) -> Path:
        return self.output_parent / (R3_RUN_ID + "-fit-failure.json")

    @property
    def r3_fit_failure_review(self) -> Path:
        return self.output_parent / (R3_RUN_ID + "-fit-failure-result-review.json")

    @property
    def design_review(self) -> Path:
        return self.output_parent / R4_REVIEW_BASENAMES["design"]

    @property
    def implementation_review(self) -> Path:
        return self.output_parent / R4_REVIEW_BASENAMES["implementation"]

    @property
    def delivery_manifest(self) -> Path:
        return self.output_parent / (RUN_ID + "-delivery-manifest.json")

    @property
    def delivery_review(self) -> Path:
        return self.output_parent / R4_REVIEW_BASENAMES["delivery"]

    @property
    def server_receipt(self) -> Path:
        return self.output_parent / (RUN_ID + "-server-receipt")

    @property
    def server_receipt_review(self) -> Path:
        return self.output_parent / R4_REVIEW_BASENAMES["receipt"]

    @property
    def host_runtime_lock(self) -> Path:
        return self.server_receipt / "runtime/service-v1-b1-r4-host-runtime-lock.json"

    @property
    def portable_reader(self) -> Path:
        return self.standalone / "scripts/combination340_models.py"

    @property
    def portable_dependency(self) -> Path:
        return self.standalone / "scripts/rec046_common.py"

    @property
    def natural_train(self) -> Path:
        return self.standalone / "outputs/recommendation-evidence/foundation340/RH/train.parquet"

    @property
    def natural_score(self) -> Path:
        return self.standalone / "outputs/recommendation-evidence/foundation340/RH/score.parquet"

    @property
    def masked_root(self) -> Path:
        return self.standalone / "outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-masked-views-v1"

    @property
    def masked_train(self) -> Path:
        return self.masked_root / "tmdb-masked-rh230.parquet"

    @property
    def masked_manifest(self) -> Path:
        return self.masked_root / "manifest.json"

    @property
    def views_manifest(self) -> Path:
        return self.masked_root / "views-manifest.json"

    @property
    def masked_review(self) -> Path:
        return self.masked_root.with_name(self.masked_root.name + "-result-review.json")

    @property
    def training_recipe(self) -> Path:
        return self.team / "pipeline/configs/service-v1/training-recipe.v1.json"

    @property
    def artifact_contract(self) -> Path:
        return self.team / "pipeline/artifacts/service-v1.json"

    @property
    def models_contract(self) -> Path:
        return self.team / "pipeline/docs/service-v1/MODELS.md"

    @property
    def feature_contract(self) -> Path:
        return self.team / "pipeline/configs/service-v1/feature-schema.v1.json"

    @property
    def output_parent(self) -> Path:
        return self.standalone / "outputs/recommendation-evidence/service-v1-pretraining-20260913"

    def bundle(self, phase: str) -> Path:
        names = {
            "preflight": RUN_ID + "-preflight",
            "fit": RUN_ID + "-fit",
            "score": RUN_ID + "-score",
            "calibrate-select": RUN_ID + "-selection",
            "selection": RUN_ID + "-selection",
            "confirmation": RUN_ID + "-confirmation",
        }
        require(phase in names, f"unsupported bundle phase: {phase}")
        return self.output_parent / names[phase]

    def review(self, phase: str) -> Path:
        bundle = self.bundle(phase)
        return bundle.with_name(bundle.name + "-result-review.json")

    def failure(self, phase: str) -> Path:
        bundle = self.bundle(phase)
        return bundle.with_name(bundle.name + "-failure.json")

    @property
    def predecessor_failure(self) -> Path:
        return self.r3_fit_failure

    @property
    def r2_preflight(self) -> Path:
        return self.output_parent / (R2_RUN_ID + "-preflight")

    @property
    def r2_preflight_review(self) -> Path:
        return self.output_parent / (R2_RUN_ID + "-preflight-result-review.json")

    @property
    def r2_fit_failure(self) -> Path:
        return self.output_parent / (R2_RUN_ID + "-fit-failure.json")


SERVER_BASE_CONTROL_KEYS = frozenset(
    {"deliveryManifest", "deliveryReview", "serverReceiptManifest", "serverReceiptReview", "hostRuntimeLock"}
)


@dataclass(frozen=True)
class ServerPhaseControls:
    delivery_manifest: Path
    delivery_review: Path
    server_receipt_manifest: Path
    server_receipt_review: Path
    host_runtime_lock: Path

    def for_phase(self, paths: ExecutionPaths, phase: str) -> dict[str, Path]:
        result = {
            "deliveryManifest": self.delivery_manifest,
            "deliveryReview": self.delivery_review,
            "serverReceiptManifest": self.server_receipt_manifest,
            "serverReceiptReview": self.server_receipt_review,
            "hostRuntimeLock": self.host_runtime_lock,
        }
        if phase in {"fit", "score"}:
            result.update({"preflightManifest": paths.bundle("preflight") / "manifest.json",
                           "preflightReview": paths.review("preflight")})
        if phase == "score":
            result.update({"fitManifest": paths.bundle("fit") / "manifest.json",
                           "fitReview": paths.review("fit")})
        return result


def server_reference_documents(control_state: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {}
    for record in control_state["records"]:
        path = str(record["path"])
        if path.endswith("-delivery-manifest.json"):
            by_name["deliveryManifest"] = dict(record)
        elif path.endswith("-delivery-manifest-result-review.json"):
            by_name["deliveryReview"] = dict(record)
        elif path.endswith("server-receipt/manifest.json"):
            by_name["serverReceiptManifest"] = dict(record)
        elif path.endswith("-server-receipt-result-review.json"):
            by_name["serverReceiptReview"] = dict(record)
        elif path.endswith("service-v1-b1-r4-host-runtime-lock.json"):
            by_name["hostRuntimeLock"] = dict(record)
    require(set(by_name) >= SERVER_BASE_CONTROL_KEYS, "server control records missing from phase reference")
    delivery = {"schemaVersion": "feelm-service-v1-b1-r4-delivery-reference/1", "runId": RUN_ID,
                "profileId": PROFILE_ID, "manifest": by_name["deliveryManifest"],
                "review": by_name["deliveryReview"], "deploymentAuthorized": False}
    receipt = {"schemaVersion": "feelm-service-v1-b1-r4-server-receipt-reference/1", "runId": RUN_ID,
               "profileId": PROFILE_ID, "manifest": by_name["serverReceiptManifest"],
               "review": by_name["serverReceiptReview"], "runtimeLock": by_name["hostRuntimeLock"],
               "deploymentAuthorized": False}
    return delivery, receipt


def _canonical_control_paths(paths: ExecutionPaths, phase: str) -> dict[str, Path]:
    result = {
        "deliveryManifest": paths.delivery_manifest,
        "deliveryReview": paths.delivery_review,
        "serverReceiptManifest": paths.server_receipt / "manifest.json",
        "serverReceiptReview": paths.server_receipt_review,
        "hostRuntimeLock": paths.host_runtime_lock,
    }
    if phase in {"fit", "score", "calibrate-select", "confirmation"}:
        result.update({"preflightManifest": paths.bundle("preflight") / "manifest.json",
                       "preflightReview": paths.review("preflight")})
    if phase in {"score", "calibrate-select", "confirmation"}:
        result.update({"fitManifest": paths.bundle("fit") / "manifest.json", "fitReview": paths.review("fit")})
    if phase in {"calibrate-select", "confirmation"}:
        result.update({"scoreManifest": paths.bundle("score") / "manifest.json", "scoreReview": paths.review("score")})
    return result


def verify_server_control_chain(
    paths: ExecutionPaths,
    *,
    phase: str,
    controls: Mapping[str, Path],
    expected_sha256: Mapping[str, str],
) -> dict[str, Any]:
    """Rehash authorization controls without touching any evaluation payload path."""
    require(phase in {"preflight", "fit", "score", "calibrate-select", "confirmation"}, "invalid server phase")
    canonical = _canonical_control_paths(paths, phase)
    require(set(controls) == set(canonical), f"server control set drift for {phase}")
    require(set(expected_sha256) == set(canonical), f"expected control SHA set drift for {phase}")
    records: list[dict[str, Any]] = []
    payloads: dict[str, dict[str, Any]] = {}
    for name in canonical:
        supplied = Path(controls[name]).absolute()
        require(supplied.resolve() == canonical[name].resolve(), f"noncanonical server control: {name}")
        expected = expected_sha256[name]
        require(isinstance(expected, str)
                and re.fullmatch(r"[0-9a-f]{64}", expected) is not None,
                f"invalid expected SHA: {name}")
        record = pin_file(supplied, logical_path(paths, supplied))
        require(record["sha256"] == expected, f"server control SHA drift: {name}")
        records.append(record)
        payloads[name] = load_json(supplied)
    for name, payload in payloads.items():
        if name == "hostRuntimeLock":
            require(payload.get("schemaVersion") == "feelm-service-v1-b1-r4-host-runtime-lock/1", "runtime lock schema drift")
            continue
        require(payload.get("runId") == RUN_ID and payload.get("profileId") == PROFILE_ID, f"control identity drift: {name}")
    require(payloads["deliveryManifest"].get("status") == "DELIVERY_AUDIT_PENDING", "delivery status drift")
    require(
        payloads["deliveryReview"].get("status") == "PASS"
        and payloads["deliveryReview"].get("decision", {}).get("serverTransferEligible") is True,
        "delivery review gate failed",
    )
    require(payloads["serverReceiptManifest"].get("status") == "RECEIPT_AUDIT_PENDING", "receipt status drift")
    require(
        payloads["serverReceiptReview"].get("status") == "PASS"
        and payloads["serverReceiptReview"].get("decision", {}).get("publicPreflightEligible") is True,
        "server receipt review gate failed",
    )
    if phase in {"fit", "score", "calibrate-select", "confirmation"}:
        require(
            payloads["preflightReview"].get("status") == "PASS"
            and payloads["preflightReview"].get("decision", {}).get("fitEligible") is True,
            "preflight review does not authorize fit",
        )
    if phase in {"score", "calibrate-select", "confirmation"}:
        require(
            payloads["fitReview"].get("status") == "PASS"
            and payloads["fitReview"].get("decision", {}).get("scoreEligible") is True,
            "fit review does not authorize score",
        )
    if phase in {"calibrate-select", "confirmation"}:
        require(
            payloads["scoreReview"].get("status") == "PASS"
            and payloads["scoreReview"].get("decision", {}).get("evaluationSelectionEligible") is True,
            "score review does not authorize evaluation selection",
        )
    runtime = payloads["hostRuntimeLock"]
    interpreter = Path(str(runtime.get("absoluteInterpreter", "")))
    require(interpreter.is_absolute() and interpreter.resolve() == Path(sys.executable).resolve(), "sealed interpreter drift")
    require(sha256_file(interpreter) == runtime.get("interpreterSha256"), "sealed interpreter SHA drift")
    return {"records": records, "payloads": payloads, "controlSetSha256": canonical_record_set_sha256(records)}


def _parse_utc(value: Any) -> dt.datetime:
    require(isinstance(value, str) and value.endswith("Z"), "RFC3339 UTC timestamp required")
    parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    require(parsed.tzinfo is not None, "timezone-aware timestamp required")
    return parsed.astimezone(dt.timezone.utc)


HOST_RUNTIME_ENVIRONMENT = {
    "pythonPath": "UNSET",
    "pythonNoUserSite": True,
    "isolatedMode": True,
    "pythonDontWriteBytecode": True,
    "locale": "C.UTF-8",
    "timezone": "UTC",
}


def _reservation_key(phase: str) -> str:
    values = {"preflight-dry": "preflightDry", "preflight-full": "preflightFull",
              "fit": "fit", "score": "score", "calibrate-select": "calibrateSelect",
              "confirmation": "confirmation"}
    require(phase in values, "unknown reservation phase")
    return values[phase]


def _validate_current_gate_bindings(
    paths: "ExecutionPaths", control_state: Mapping[str, Any], gate: Mapping[str, Any], phase: str
) -> None:
    payloads = control_state.get("payloads")
    records = control_state.get("records")
    require(isinstance(payloads, Mapping) and isinstance(records, Sequence),
            "host gate control state shape drift")
    receipt = payloads.get("serverReceiptManifest")
    runtime_lock = payloads.get("hostRuntimeLock")
    require(isinstance(receipt, Mapping) and isinstance(runtime_lock, Mapping),
            "host gate receipt/runtime controls missing")
    reservations = receipt.get("maintenanceReservations")
    require(isinstance(reservations, Mapping), "receipt maintenance reservations missing")
    expected_reservation = reservations.get(_reservation_key(phase))
    require(isinstance(expected_reservation, Mapping)
            and set(expected_reservation) == {"path", "bytes", "sha256"},
            "receipt reservation pin shape drift")
    reservation_path = _reservation_path(paths, phase)
    actual_reservation = _absolute_record(reservation_path)
    require(actual_reservation == dict(expected_reservation),
            "current maintenance reservation pin drift")
    reservation = load_json(reservation_path, allow_null=False, require_lf=True)
    reservation_keys = {"schemaVersion", "status", "reservationId", "hostIdentity", "phase",
                        "startsAt", "endsAt", "dockerRestartScheduled", "hostRebootScheduled",
                        "issuedBy", "createdAt"}
    require(set(reservation) == reservation_keys
            and reservation["schemaVersion"] == "feelm-service-v1-b1-r4-maintenance-reservation/2"
            and reservation["status"] == "ACTIVE" and reservation["phase"] == phase,
            "current maintenance reservation contract drift")
    reservation_id = reservation["reservationId"]
    require(isinstance(reservation_id, str) and str(uuid.UUID(reservation_id)) == reservation_id,
            "maintenance reservation ID must be a canonical UUID")
    require(all(isinstance(reservation[name], str) and reservation[name].strip()
                for name in ("hostIdentity", "issuedBy")),
            "maintenance reservation issuer/host identity missing")
    require(reservation["dockerRestartScheduled"] is False
            and reservation["hostRebootScheduled"] is False,
            "maintenance reservation schedules a disruptive event")
    starts = _parse_utc(reservation["startsAt"])
    ends = _parse_utc(reservation["endsAt"])
    created = _parse_utc(reservation["createdAt"])
    checked = _parse_utc(gate["checkedAt"])
    require(created <= starts <= checked < ends, "maintenance reservation time ordering drift")
    require(gate["maintenanceReservationId"] == reservation_id
            and gate["reservationEndsAt"] == reservation["endsAt"],
            "dynamic gate is not bound to the current reservation")

    runtime_keys = {"schemaVersion", "createdAt", "bootstrapInterpreter", "absoluteInterpreter",
                    "interpreterBytes", "interpreterSha256", "pythonImplementation", "pythonVersion",
                    "environment", "requirementsLock", "wheelhouseManifest", "venvInventory",
                    "packages", "imports", "evaluationFixture", "runtimeSetSha256"}
    require(set(runtime_lock) == runtime_keys
            and runtime_lock["schemaVersion"] == "feelm-service-v1-b1-r4-host-runtime-lock/1",
            "host runtime lock contract drift")
    require(runtime_lock["environment"] == HOST_RUNTIME_ENVIRONMENT,
            "host runtime environment policy drift")
    runtime_records = [dict(record) for record in records
                       if isinstance(record, Mapping)
                       and str(record.get("path", "")).endswith("service-v1-b1-r4-host-runtime-lock.json")]
    require(len(runtime_records) == 1 and gate["runtime"]["lock"] == runtime_records[0],
            "dynamic gate runtime lock pin drift")
    interpreter = Path(str(runtime_lock["absoluteInterpreter"]))
    require(interpreter.is_absolute() and interpreter.resolve() == Path(sys.executable).resolve(),
            "dynamic gate interpreter is not the current sealed interpreter")
    interpreter_pin = _absolute_record(interpreter)
    require(interpreter_pin["bytes"] == runtime_lock["interpreterBytes"]
            and interpreter_pin["sha256"] == runtime_lock["interpreterSha256"],
            "dynamic gate interpreter bytes/SHA drift")
    inventory_pin = runtime_lock["venvInventory"]
    require(isinstance(inventory_pin, Mapping)
            and set(inventory_pin) == {"path", "bytes", "sha256"},
            "runtime venv inventory pin shape drift")
    inventory_path = Path(str(inventory_pin["path"]))
    require(inventory_path.is_absolute() and _absolute_record(inventory_path) == dict(inventory_pin),
            "current venv inventory pin drift")
    inventory = load_json(inventory_path, allow_null=False, require_lf=True)
    require(inventory.get("schemaVersion") == "feelm-service-v1-b1-r4-venv-inventory/1"
            and isinstance(inventory.get("recordSetSha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", inventory["recordSetSha256"]) is not None,
            "current venv inventory digest drift")
    require(gate["runtime"] == {"lock": runtime_records[0],
            "interpreterSha256": runtime_lock["interpreterSha256"],
            "venvRecordSetSha256": inventory["recordSetSha256"],
            "environment": HOST_RUNTIME_ENVIRONMENT,
            "importFixtureStatus": runtime_lock["evaluationFixture"]["status"]},
            "dynamic gate runtime binding drift")


def verify_dynamic_host_gate(
    gate: Mapping[str, Any], *, phase: str, current_namespace: Sequence[str] | None = None,
    paths: "ExecutionPaths | None" = None, control_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    expected_keys = {
        "schemaVersion", "status", "runId", "profileId", "phase", "checkedAt",
        "maintenanceReservationId", "reservationEndsAt", "maintenanceWindowRemainingSeconds",
        "requiredWindowSeconds", "architecture", "logicalCpu", "memory", "filesystem", "docker",
        "cgroup", "supervisor", "competingProcessCensus", "dockerRestartScheduled",
        "hostRebootScheduled", "namespaceCensus", "runtime", "probeCommands", "probeSetSha256",
        "unknownFields",
    }
    require(set(gate) == expected_keys, "dynamic host gate field set drift")
    require(gate["schemaVersion"] == "feelm-service-v1-b1-r4-dynamic-host-gate/2", "host gate schema drift")
    require(gate["status"] == "PASS" and gate["runId"] == RUN_ID and gate["profileId"] == PROFILE_ID, "host gate identity/status drift")
    required_windows = {**PHASE_REQUIRED_WINDOW_SECONDS, "calibrate-select": 16_200,
                        "confirmation": 16_200}
    require(phase in required_windows and gate["phase"] == phase, "host gate phase drift")
    reservation_id = gate["maintenanceReservationId"]
    require(isinstance(reservation_id, str) and str(uuid.UUID(reservation_id)) == reservation_id,
            "host gate reservation ID must be a canonical UUID")
    required = required_windows[phase]
    now = dt.datetime.now(dt.timezone.utc)
    checked = _parse_utc(gate["checkedAt"])
    ends = _parse_utc(gate["reservationEndsAt"])
    require(dt.timedelta(0) <= now - checked <= dt.timedelta(minutes=5), "stale or future host gate")
    remaining = math.floor((ends - checked).total_seconds())
    require(gate["maintenanceWindowRemainingSeconds"] == remaining, "host gate reservation arithmetic drift")
    require(gate["requiredWindowSeconds"] == required and (ends - now).total_seconds() >= required, "maintenance window insufficient")
    require(gate["architecture"] == "x86_64" and type(gate["logicalCpu"]) is int and gate["logicalCpu"] >= 8, "host CPU/architecture gate failed")
    memory = gate["memory"]
    require(isinstance(memory, Mapping) and set(memory) == {"memTotalBytes", "memAvailableBytes", "source"}
            and memory.get("source") == "/proc/meminfo-kib-times-1024", "memory gate shape drift")
    require(type(memory["memTotalBytes"]) is int and memory["memTotalBytes"] >= 32_000_000_000, "MemTotal gate failed")
    require(type(memory["memAvailableBytes"]) is int and memory["memAvailableBytes"] >= 25_769_803_776, "MemAvailable gate failed")
    filesystem = gate["filesystem"]
    require(isinstance(filesystem, Mapping) and set(filesystem) == {
        "scratchFreeBytes", "outputFreeBytes", "scratchFreeInodes", "outputFreeInodes",
        "scratchDevice", "outputDevice", "stageDevice", "finalDevice", "filesystemType",
        "renameNoReplaceProbe"}, "filesystem gate shape drift")
    require(type(filesystem["scratchFreeBytes"]) is int and filesystem["scratchFreeBytes"] >= 85_899_345_920,
            "scratch free-space gate failed")
    require(type(filesystem["outputFreeBytes"]) is int and filesystem["outputFreeBytes"] >= 21_474_836_480,
            "output free-space gate failed")
    require(type(filesystem["scratchFreeInodes"]) is int and filesystem["scratchFreeInodes"] >= 100_000
            and type(filesystem["outputFreeInodes"]) is int and filesystem["outputFreeInodes"] >= 100_000,
            "filesystem inode gate failed")
    require(all(type(filesystem[name]) is int and filesystem[name] >= 0
                for name in ("scratchDevice", "outputDevice", "stageDevice", "finalDevice")),
            "filesystem device identity drift")
    require(filesystem["outputDevice"] == filesystem["stageDevice"] == filesystem["finalDevice"]
            and filesystem["renameNoReplaceProbe"] is True
            and filesystem["filesystemType"] in {"ext2/ext3", "xfs"}, "atomic filesystem gate failed")
    docker = gate["docker"]
    require(isinstance(docker, Mapping) and set(docker) == {"serverVersion", "daemonId", "imageId",
        "imageUnpackedSizeBytes", "imageInspectSha256", "runningB1Containers", "restartPolicy"},
        "Docker gate shape drift")
    require(isinstance(docker.get("serverVersion"), str) and docker["serverVersion"].strip()
            and isinstance(docker.get("daemonId"), str) and docker["daemonId"].strip()
            and docker.get("imageId") == IMAGE_ID and type(docker.get("imageUnpackedSizeBytes")) is int
            and docker.get("imageUnpackedSizeBytes") == 936_463_936
            and isinstance(docker.get("imageInspectSha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", docker["imageInspectSha256"]) is not None,
            "Docker image gate failed")
    require(docker.get("runningB1Containers") == [] and docker.get("restartPolicy") == "no",
            "Docker concurrency/restart gate failed")
    cgroup = gate["cgroup"]
    require(isinstance(cgroup, Mapping) and set(cgroup) == {"version", "controllers", "probeContainerId",
        "probeInitPid", "cgroupPath", "readableFiles", "probeStatus"}, "cgroup gate shape drift")
    controllers = cgroup.get("controllers")
    readable = cgroup.get("readableFiles")
    require(cgroup.get("version") == "v2" and cgroup.get("probeStatus") == "PASS"
            and isinstance(controllers, list) and controllers == sorted(set(controllers))
            and all(isinstance(item, str) and item for item in controllers)
            and set(controllers) >= {"cpu", "memory", "pids"}
            and isinstance(readable, list) and readable == sorted(set(readable))
            and set(readable) == {"memory.current", "memory.peak", "memory.events",
                                  "cpu.stat", "pids.current"}
            and isinstance(cgroup.get("probeContainerId"), str) and cgroup["probeContainerId"].strip()
            and type(cgroup.get("probeInitPid")) is int and cgroup["probeInitPid"] > 0
            and isinstance(cgroup.get("cgroupPath"), str) and cgroup["cgroupPath"].startswith("/"),
            "cgroup-v2 gate failed")
    supervisor = gate["supervisor"]
    require(isinstance(supervisor, Mapping) and set(supervisor) == {"kind", "phaseUnitName", "probeUnitName",
        "phaseUnitExistsBefore", "probeStartCommand", "probeControlGroup", "probeMainPid", "probePidTree",
        "cpuQuotaPercent", "memoryMaxBytes", "memorySwapMaxBytes", "tasksMax", "killMode",
        "timeoutStopSeconds", "status"}, "supervisor gate shape drift")
    expected_unit = {"preflight-dry": "feelm-b1-r4-server5c20g-t28800-preflight.service",
                     "preflight-full": "feelm-b1-r4-server5c20g-t28800-preflight.service",
                     "fit": "feelm-b1-r4-server5c20g-t28800-fit.service",
                     "score": "feelm-b1-r4-server5c20g-t28800-score.service",
                     "calibrate-select": "feelm-b1-r4-server5c20g-t28800-selection.service",
                     "confirmation": "feelm-b1-r4-server5c20g-t28800-confirmation.service"}[phase]
    evaluation = phase in {"calibrate-select", "confirmation"}
    expected_limits = {"cpuQuotaPercent": 500 if evaluation else 100,
                       "memoryMaxBytes": 21_474_836_480 if evaluation else 2_147_483_648,
                       "memorySwapMaxBytes": 0, "tasksMax": 4096 if evaluation else 512,
                       "killMode": "control-group", "timeoutStopSeconds": 900}
    probe_unit = expected_unit[:-8] + "-probe.service"
    expected_probe_command = ["systemd-run", "--unit", probe_unit, "--collect", "--wait", "--pipe",
        "--property", f"CPUQuota={expected_limits['cpuQuotaPercent']}%",
        "--property", f"MemoryMax={expected_limits['memoryMaxBytes']}",
        "--property", f"MemorySwapMax={expected_limits['memorySwapMaxBytes']}",
        "--property", f"TasksMax={expected_limits['tasksMax']}", "--property", "KillMode=control-group",
        "--property", f"TimeoutStopSec={expected_limits['timeoutStopSeconds']}", "/usr/bin/sleep", "2"]
    require(supervisor.get("status") == "PASS"
            and supervisor.get("kind") == "systemd-transient-service"
            and supervisor.get("phaseUnitExistsBefore") is False
            and supervisor.get("phaseUnitName") == expected_unit
            and supervisor.get("probeUnitName") == probe_unit
            and supervisor.get("probeStartCommand") == expected_probe_command
            and isinstance(supervisor.get("probeControlGroup"), str)
            and supervisor["probeControlGroup"].endswith("/" + supervisor["probeUnitName"])
            and type(supervisor.get("probeMainPid")) is int and supervisor["probeMainPid"] > 0
            and isinstance(supervisor.get("probePidTree"), list)
            and supervisor["probePidTree"] == sorted(set(supervisor["probePidTree"]))
            and supervisor["probeMainPid"] in supervisor["probePidTree"]
            and all(type(pid) is int and pid > 0 for pid in supervisor["probePidTree"])
            and all(supervisor.get(name) == value for name, value in expected_limits.items()),
            "systemd supervisor gate failed")
    require(gate["competingProcessCensus"] == [], "competing process gate failed")
    require(gate["dockerRestartScheduled"] is False and gate["hostRebootScheduled"] is False, "scheduled maintenance conflict")
    require(gate["unknownFields"] == [], "host gate contains unknown observations")
    census = gate["namespaceCensus"]
    require(isinstance(census, Mapping) and set(census) == {"completed", "failures", "reviews", "claims",
        "temps", "scratches", "containers", "processes"}, "host namespace census shape drift")
    for name, values in census.items():
        require(isinstance(values, list) and values == sorted(set(values))
                and all(isinstance(item, str) and item for item in values),
                f"host namespace census drift: {name}")
    runtime = gate["runtime"]
    require(isinstance(runtime, Mapping) and set(runtime) == {"lock", "interpreterSha256",
        "venvRecordSetSha256", "environment", "importFixtureStatus"}, "host runtime gate shape drift")
    require(isinstance(runtime["lock"], Mapping)
            and set(runtime["lock"]) == {"path", "bytes", "sha256"}
            and isinstance(runtime["interpreterSha256"], str)
            and re.fullmatch(r"[0-9a-f]{64}", runtime["interpreterSha256"]) is not None
            and isinstance(runtime["venvRecordSetSha256"], str)
            and re.fullmatch(r"[0-9a-f]{64}", runtime["venvRecordSetSha256"]) is not None
            and runtime["environment"] == HOST_RUNTIME_ENVIRONMENT
            and runtime["importFixtureStatus"] == "PASS", "host runtime gate failed")
    probe_commands = gate["probeCommands"]
    require(isinstance(probe_commands, list) and probe_commands
            and all(isinstance(command, list) and command
                    and all(isinstance(part, str) for part in command) for command in probe_commands),
            "host probe command census drift")
    require(re.fullmatch(r"[0-9a-f]{64}", gate["probeSetSha256"]) is not None, "host probe digest drift")
    if current_namespace is not None:
        completed = gate["namespaceCensus"].get("completed")
        require(completed == sorted(current_namespace), "host gate namespace snapshot drift")
    require((paths is None) is (control_state is None),
            "host gate binding requires both paths and control state")
    if paths is not None and control_state is not None:
        _validate_current_gate_bindings(paths, control_state, gate, phase)
    return dict(gate)


def _run_probe(argv: Sequence[str], *, timeout: float = 30.0, check: bool = True) -> dict[str, Any]:
    command = [str(part) for part in argv]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    result = {"argv": command, "exitCode": int(completed.returncode), "stdout": completed.stdout,
              "stderr": completed.stderr}
    if check:
        require(completed.returncode == 0, f"host probe failed: {command}: {completed.stderr}")
    return result


def _filesystem_type_linux(path: Path) -> str:
    require(sys.platform.startswith("linux"), "dynamic host gate requires Linux")
    class StatFs(ctypes.Structure):
        _fields_ = [("f_type", ctypes.c_long), ("rest", ctypes.c_byte * 248)]
    value = StatFs()
    libc = ctypes.CDLL(None, use_errno=True)
    encoded = os.fsencode(path)
    if libc.statfs(ctypes.c_char_p(encoded), ctypes.byref(value)) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(path))
    names = {0xEF53: "ext2/ext3", 0x58465342: "xfs"}
    require(value.f_type in names, f"unsupported host filesystem magic: {value.f_type:#x}")
    return names[value.f_type]


def _probe_no_replace(parent: Path) -> bool:
    token = uuid.uuid4().hex
    source = parent / f".{RUN_ID}.host-gate-source-{token}"
    target = parent / f".{RUN_ID}.host-gate-target-{token}"
    collision = parent / f".{RUN_ID}.host-gate-collision-{token}"
    try:
        source.write_bytes(b"source\n")
        fsync_file(source)
        rename_no_replace(source, target)
        collision.write_bytes(b"collision\n")
        fsync_file(collision)
        blocked = False
        try:
            rename_no_replace(collision, target)
        except FileExistsError:
            blocked = True
        require(blocked and target.read_bytes() == b"source\n", "rename no-replace collision probe failed")
        return True
    finally:
        for candidate in (source, target, collision):
            if os.path.lexists(candidate):
                candidate.unlink()
        fsync_directory(parent)


def _read_meminfo() -> dict[str, Any]:
    values: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
        key, separator, rest = line.partition(":")
        if separator and rest.strip().endswith(" kB"):
            values[key] = int(rest.strip().split()[0]) * 1024
    require("MemTotal" in values and "MemAvailable" in values, "incomplete /proc/meminfo")
    return {"memTotalBytes": values["MemTotal"], "memAvailableBytes": values["MemAvailable"],
            "source": "/proc/meminfo-kib-times-1024"}


def _pid_tree(root_pid: int) -> list[int]:
    require(root_pid > 0, "invalid root PID")
    parents: dict[int, int] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text(encoding="ascii").split()
            parents[int(entry.name)] = int(fields[3])
        except (OSError, ValueError, IndexError):
            continue
    selected = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, parent in parents.items():
            if parent in selected and pid not in selected:
                selected.add(pid)
                changed = True
    return sorted(selected)


def _namespace_census(paths: ExecutionPaths) -> dict[str, list[str]]:
    completed: list[str] = []
    failures: list[str] = []
    reviews: list[str] = []
    claims: list[str] = []
    temps: list[str] = []
    scratches: list[str] = []
    if paths.output_parent.is_dir():
        for child in paths.output_parent.iterdir():
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
            elif name.endswith("-result-review.json"):
                reviews.append(name)
                completed.append(name)
            else:
                completed.append(name)
    containers = running_container_names(prefix="feelm-b1-")
    processes: list[str] = []
    if sys.platform.startswith("linux"):
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit() or int(entry.name) in {os.getpid(), os.getppid()}:
                continue
            try:
                raw = (entry / "cmdline").read_bytes()
            except OSError:
                continue
            text_value = raw.replace(b"\0", b" ").decode("utf-8", errors="replace")
            if any(marker in text_value for marker in ("spark-submit", "org.apache.spark", "service_v1_b1",
                                                        "run_service_v1_b1")):
                processes.append(entry.name)
    return {"completed": sorted(completed), "failures": sorted(failures), "reviews": sorted(reviews),
            "claims": sorted(claims), "temps": sorted(temps), "scratches": sorted(scratches),
            "containers": sorted(containers), "processes": sorted(processes)}


def _reservation_path(paths: ExecutionPaths, phase: str) -> Path:
    names = {"preflight-dry": "preflight-dry.json", "preflight-full": "preflight-full.json",
             "fit": "fit.json", "score": "score.json", "calibrate-select": "calibrate-select.json",
             "confirmation": "confirmation.json"}
    require(phase in names, "unknown reservation phase")
    return paths.server_receipt / "maintenance-reservations" / names[phase]


def collect_dynamic_host_gate(
    paths: ExecutionPaths,
    *,
    phase: str,
    control_state: Mapping[str, Any],
    scratch_root: Path,
) -> dict[str, Any]:
    """Collect a fresh EC2 gate before a phase starts.

    This function is deliberately unavailable on Windows.  Unit tests may test
    validation with fixtures, while only the server can create PASS evidence.
    """
    require(sys.platform.startswith("linux"), "dynamic host gate can only run on Linux")
    profile, _ = execution_profile(paths)
    required = {**PHASE_REQUIRED_WINDOW_SECONDS, "calibrate-select": 16_200,
                "confirmation": 16_200}[phase]
    reservation_path = _reservation_path(paths, phase)
    reservation_pin = pin_file(reservation_path, logical_path(paths, reservation_path))
    reservation = load_json(reservation_path, allow_null=False, require_lf=True)
    require(set(reservation) == {"schemaVersion", "status", "reservationId", "hostIdentity", "phase",
        "startsAt", "endsAt", "dockerRestartScheduled", "hostRebootScheduled", "issuedBy", "createdAt"},
        "maintenance reservation field drift")
    require(reservation["schemaVersion"] == "feelm-service-v1-b1-r4-maintenance-reservation/2"
            and reservation["status"] == "ACTIVE" and reservation["phase"] == phase
            and reservation["dockerRestartScheduled"] is False
            and reservation["hostRebootScheduled"] is False, "maintenance reservation invalid")
    receipt_payload = control_state["payloads"]["serverReceiptManifest"]
    key = {"preflight-dry": "preflightDry", "preflight-full": "preflightFull", "fit": "fit", "score": "score",
           "calibrate-select": "calibrateSelect", "confirmation": "confirmation"}[phase]
    expected_reservation = receipt_payload["maintenanceReservations"][key]
    require((reservation_pin["bytes"], reservation_pin["sha256"])
            == (expected_reservation["bytes"], expected_reservation["sha256"]),
            "maintenance reservation pin drift")
    checked = dt.datetime.now(dt.timezone.utc)
    starts = _parse_utc(reservation["startsAt"])
    ends = _parse_utc(reservation["endsAt"])
    require(starts <= checked < ends and math.floor((ends - checked).total_seconds()) >= required,
            "maintenance window is not active or is too short")

    probe_records: list[dict[str, Any]] = []
    uname = _run_probe(["uname", "-m"]); probe_records.append(uname)
    cpu = _run_probe(["getconf", "_NPROCESSORS_ONLN"]); probe_records.append(cpu)
    architecture = uname["stdout"].strip()
    logical_cpu = int(cpu["stdout"].strip())
    memory = _read_meminfo()
    scratch = scratch_root.resolve()
    output = paths.output_parent.resolve()
    scratch_stat = os.statvfs(scratch); output_stat = os.statvfs(output)
    filesystem = {
        "scratchFreeBytes": scratch_stat.f_bavail * scratch_stat.f_frsize,
        "outputFreeBytes": output_stat.f_bavail * output_stat.f_frsize,
        "scratchFreeInodes": scratch_stat.f_favail,
        "outputFreeInodes": output_stat.f_favail,
        "scratchDevice": os.stat(scratch).st_dev,
        "outputDevice": os.stat(output).st_dev,
        "stageDevice": os.stat(output).st_dev,
        "finalDevice": os.stat(output).st_dev,
        "filesystemType": _filesystem_type_linux(output),
        "renameNoReplaceProbe": _probe_no_replace(output),
    }
    docker_version = _run_probe(["docker", "version", "--format", "{{json .Server}}"])
    docker_info = _run_probe(["docker", "info", "--format", "{{json .}}"])
    image = _run_probe(["docker", "image", "inspect", IMAGE_ID])
    probe_records.extend([docker_version, docker_info, image])
    server_data = json.loads(docker_version["stdout"])
    info_data = json.loads(docker_info["stdout"])
    image_data = json.loads(image["stdout"])
    require(isinstance(image_data, list) and len(image_data) == 1, "Docker image inspect cardinality drift")
    image_row = image_data[0]
    docker = {"serverVersion": str(server_data.get("Version", "")), "daemonId": str(info_data.get("ID", "")),
              "imageId": image_row.get("Id"), "imageUnpackedSizeBytes": image_row.get("Size"),
              "imageInspectSha256": sha256_bytes(canonical_json_bytes(image_data)),
              "runningB1Containers": running_container_names(prefix="feelm-b1-"), "restartPolicy": "no"}

    controllers = Path("/sys/fs/cgroup/cgroup.controllers").read_text(encoding="ascii").split()
    probe_name = "feelm-b1-r4-cgroup-probe-" + uuid.uuid4().hex[:12]
    create = _run_probe(["docker", "create", "--name", probe_name, "--cpus", "5", "--memory", "20g",
                         "--memory-swap", "20g", "--network", "none", IMAGE_ID, "/bin/sh", "-c", "sleep 2"])
    probe_records.append(create)
    probe_container_id = create["stdout"].strip()
    try:
        start = _run_probe(["docker", "start", probe_container_id]); probe_records.append(start)
        inspected = _run_probe(["docker", "inspect", probe_container_id]); probe_records.append(inspected)
        inspect_rows = json.loads(inspected["stdout"])
        probe_pid = int(inspect_rows[0]["State"]["Pid"])
        cgroup_line = next(line for line in Path(f"/proc/{probe_pid}/cgroup").read_text(encoding="ascii").splitlines()
                           if line.startswith("0::"))
        cgroup_path = "/sys/fs/cgroup" + cgroup_line[3:]
        readable = [name for name in ("memory.current", "memory.peak", "memory.events", "cpu.stat", "pids.current")
                    if (Path(cgroup_path) / name).is_file() and os.access(Path(cgroup_path) / name, os.R_OK)]
        wait = _run_probe(["docker", "wait", probe_container_id], timeout=15); probe_records.append(wait)
    finally:
        remove = _run_probe(["docker", "rm", "-f", probe_container_id], check=False); probe_records.append(remove)
    cgroup = {"version": "v2", "controllers": sorted(controllers), "probeContainerId": probe_container_id,
              "probeInitPid": probe_pid, "cgroupPath": cgroup_path, "readableFiles": sorted(readable),
              "probeStatus": "PASS"}

    runner_supervisor = profile["runnerSupervisor"]
    evaluation_supervisor = profile["evaluationSupervisor"]
    unit = ({"preflight-dry": runner_supervisor["preflightUnit"],
             "preflight-full": runner_supervisor["preflightUnit"], "fit": runner_supervisor["fitUnit"],
             "score": runner_supervisor["scoreUnit"], "calibrate-select": evaluation_supervisor["selectionUnit"],
             "confirmation": evaluation_supervisor["confirmationUnit"]}[phase])
    selected = evaluation_supervisor if phase in {"calibrate-select", "confirmation"} else runner_supervisor
    before = _run_probe(["systemctl", "show", unit, "-p", "LoadState", "--value"], check=False)
    probe_records.append(before)
    require(before["stdout"].strip() in {"", "not-found"}, "phase systemd unit already exists")
    probe_unit = unit[:-8] + "-probe.service"
    probe_argv = ["systemd-run", "--unit", probe_unit, "--collect", "--wait", "--pipe",
                  "--property", f"CPUQuota={selected['cpuQuotaPercent']}%",
                  "--property", f"MemoryMax={selected['memoryMaxBytes']}",
                  "--property", f"MemorySwapMax={selected['memorySwapMaxBytes']}",
                  "--property", f"TasksMax={selected['tasksMax']}",
                  "--property", "KillMode=control-group", "--property", f"TimeoutStopSec={selected['timeoutStopSeconds']}",
                  "/usr/bin/sleep", "2"]
    probe_process = subprocess.Popen(probe_argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    active_deadline = time.monotonic() + 2.0
    show: dict[str, Any] | None = None
    shown: dict[str, str] = {}
    while time.monotonic() < active_deadline:
        candidate = _run_probe(["systemctl", "show", probe_unit, "-p", "ActiveState",
                                "-p", "ControlGroup", "-p", "MainPID"], check=False)
        candidate_values = dict(line.split("=", 1) for line in candidate["stdout"].splitlines() if "=" in line)
        if (candidate["exitCode"] == 0 and candidate_values.get("ActiveState") in {"active", "activating"}
                and int(candidate_values.get("MainPID", "0")) > 0):
            show, shown = candidate, candidate_values
            break
        require(probe_process.poll() is None, "systemd cgroup probe exited before active observation")
        time.sleep(0.05)
    require(show is not None, "systemd cgroup probe was not active within two seconds")
    probe_records.append(show)
    main_pid = int(shown["MainPID"])
    probe_pid_tree = _pid_tree(main_pid)
    require(main_pid in probe_pid_tree, "systemd probe PID tree unavailable")
    probe_stdout, probe_stderr = probe_process.communicate(timeout=30)
    probe_run = {"argv": probe_argv, "exitCode": int(probe_process.returncode),
                 "stdout": probe_stdout, "stderr": probe_stderr}
    require(probe_process.returncode == 0, "systemd cgroup probe failed")
    probe_records.append(probe_run)
    supervisor = {"kind": "systemd-transient-service", "phaseUnitName": unit, "probeUnitName": probe_unit,
                  "phaseUnitExistsBefore": False, "probeStartCommand": probe_argv,
                  "probeControlGroup": shown.get("ControlGroup", ""), "probeMainPid": main_pid,
                  "probePidTree": probe_pid_tree,
                  "cpuQuotaPercent": selected["cpuQuotaPercent"], "memoryMaxBytes": selected["memoryMaxBytes"],
                  "memorySwapMaxBytes": selected["memorySwapMaxBytes"], "tasksMax": selected["tasksMax"],
                  "killMode": selected["killMode"], "timeoutStopSeconds": selected["timeoutStopSeconds"],
                  "status": "PASS"}

    jobs = _run_probe(["systemctl", "list-jobs", "--no-legend"]); probe_records.append(jobs)
    timers = _run_probe(["systemctl", "list-timers", "--all", "--no-legend"]); probe_records.append(timers)
    shutdown = _run_probe(["shutdown", "--show"], check=False); probe_records.append(shutdown)
    scheduled_text = "\n".join((jobs["stdout"], timers["stdout"], shutdown["stdout"])).lower()
    restart_scheduled = "docker" in scheduled_text
    reboot_scheduled = any(word in scheduled_text for word in ("reboot", "shutdown", "poweroff"))
    runtime_payload = control_state["payloads"]["hostRuntimeLock"]
    runtime_record = next(record for record in control_state["records"] if record["path"].endswith(
        "service-v1-b1-r4-host-runtime-lock.json"))
    venv_inventory_pin = runtime_payload.get("venvInventory")
    require(isinstance(venv_inventory_pin, Mapping)
            and set(venv_inventory_pin) == {"path", "bytes", "sha256"},
            "runtime venv inventory pin shape drift")
    venv_inventory_path = Path(str(venv_inventory_pin["path"]))
    require(venv_inventory_path.is_absolute(), "runtime venv inventory path must be absolute")
    require(_absolute_record(venv_inventory_path) == dict(venv_inventory_pin),
            "runtime venv inventory current pin drift")
    venv_inventory = load_json(venv_inventory_path, allow_null=False, require_lf=True)
    require(isinstance(venv_inventory.get("recordSetSha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", venv_inventory["recordSetSha256"]) is not None,
            "runtime venv record-set digest drift")
    runtime = {"lock": runtime_record, "interpreterSha256": runtime_payload["interpreterSha256"],
               "venvRecordSetSha256": venv_inventory["recordSetSha256"],
               "environment": runtime_payload["environment"],
               "importFixtureStatus": runtime_payload["evaluationFixture"]["status"]}
    gate = {
        "schemaVersion": "feelm-service-v1-b1-r4-dynamic-host-gate/2", "status": "PASS",
        "runId": RUN_ID, "profileId": PROFILE_ID, "phase": phase,
        "checkedAt": checked.isoformat().replace("+00:00", "Z"),
        "maintenanceReservationId": reservation["reservationId"], "reservationEndsAt": reservation["endsAt"],
        "maintenanceWindowRemainingSeconds": math.floor((ends - checked).total_seconds()),
        "requiredWindowSeconds": required, "architecture": architecture, "logicalCpu": logical_cpu,
        "memory": memory, "filesystem": filesystem, "docker": docker, "cgroup": cgroup,
        "supervisor": supervisor, "competingProcessCensus": [],
        "dockerRestartScheduled": restart_scheduled, "hostRebootScheduled": reboot_scheduled,
        "namespaceCensus": _namespace_census(paths), "runtime": runtime,
        "probeCommands": [record["argv"] for record in probe_records],
        "probeSetSha256": sha256_bytes(canonical_json_bytes(probe_records)), "unknownFields": [],
    }
    return verify_dynamic_host_gate(
        gate, phase=phase,
        current_namespace=expected_namespace_before("preflight" if phase.startswith("preflight") else phase),
        paths=paths, control_state=control_state)


def verify_plan(paths: ExecutionPaths) -> dict[str, Any]:
    actual = pin_file(paths.plan, "execution/service-v1-b1-r4-server-fit-recovery.md")
    require(
        actual["bytes"] == 82_061 and actual["sha256"] == RECOVERY_PLAN_SHA256,
        f"reviewed recovery plan pin drift: {actual}",
    )
    return actual


def verify_r2_plan(paths: ExecutionPaths) -> dict[str, Any]:
    actual = pin_file(paths.r2_plan, "ancestor/service-v1-b1-spark-runner.md")
    require(actual["sha256"] == R2_PLAN_SHA256, f"reviewed r2 plan hash drift: {actual['sha256']}")
    return actual


def verify_r3_plan(paths: ExecutionPaths) -> dict[str, Any]:
    actual = pin_file(paths.r3_plan, "standalone/docs/recommendation/plans/service-v1-b1-r3-fit-recovery.md")
    require(
        (actual["bytes"], actual["sha256"]) == R3_IMPLEMENTATION_PINS["docs/recommendation/plans/service-v1-b1-r3-fit-recovery.md"],
        f"reviewed r3 plan pin drift: {actual}",
    )
    return actual


def execution_profile(paths: ExecutionPaths) -> tuple[dict[str, Any], dict[str, Any]]:
    profile_pin = pin_file(
        paths.profile_contract, "execution/ec2-8vcpu32g-local5-fit20g-t28800-profile.json"
    )
    require(
        profile_pin["bytes"] == 6_516 and profile_pin["sha256"] == PROFILE_SHA256,
        "reviewed r4 execution profile bytes/SHA drift",
    )
    payload = load_json(paths.profile_contract, allow_null=False, require_lf=True)
    require(
        set(payload)
        == {
            "schemaVersion", "status", "profileId", "runId", "recoveryOrdinal", "executionScope",
            "hostClass", "singleAttempt", "automaticRetry", "docker", "spark", "timeoutsSeconds",
            "maintenanceWindowsSeconds", "serverPreconditions", "hostRuntime", "publicationRuntime",
            "runnerSupervisor", "evaluationSupervisor", "resourceObservation", "modelContract",
            "authorizationPolicy",
        },
        "profile top-level field set drift",
    )
    require(payload["schemaVersion"] == "feelm-service-v1-b1-execution-profile/2", "profile schema drift")
    require(payload["profileId"] == PROFILE_ID and payload["runId"] == RUN_ID, "profile identity drift")
    require(
        payload["status"] == "DRAFT_REQUIRES_INDEPENDENT_REVIEW"
        and payload["recoveryOrdinal"] == "r4"
        and payload["executionScope"] == "SERVER_THROUGH_CONFIRMATION"
        and payload["hostClass"] == "linux-x86_64-8vcpu-32g-class",
        "profile scope drift",
    )
    require(type(payload["singleAttempt"]) is bool and payload["singleAttempt"], "single-attempt drift")
    require(type(payload["automaticRetry"]) is bool and not payload["automaticRetry"], "retry policy drift")
    require(
        payload["docker"]
        == {
            "cpus": "5", "memory": "20g", "memorySwap": "20g", "maxMemoryBytes": MAX_MEMORY_BYTES,
            "network": "none", "imageMode": "ARCHIVE",
            "imageArchiveRelativePath": "runtime/feelm-rec046-spark-local.tar", "imageId": IMAGE_ID,
            "imageUnpackedSizeBytes": 936_463_936, "singleDockerLoad": True,
        },
        "profile Docker resource drift",
    )
    require(
        payload["spark"]
        == {"master": "local[5]", "driverMemory": "12g", "shufflePartitions": 8, "adaptiveExecution": False},
        "profile Spark resource drift",
    )
    require(
        payload["timeoutsSeconds"]
        == {"preflightDryRun": 7_200, "preflightFull": 7_200, "fit": 28_800, "score": 7_200,
            "evaluationSelection": 14_400, "evaluationConfirmation": 14_400},
        "profile timeout drift",
    )
    require(
        payload["maintenanceWindowsSeconds"]
        == {"preflightDryStart": 16_200, "preflightFullStart": 9_000, "fit": 32_400, "score": 9_000,
            "evaluationSelection": 16_200, "evaluationConfirmation": 16_200},
        "profile maintenance-window drift",
    )
    require(
        payload["serverPreconditions"]
        == {
            "architecture": "x86_64", "minimumLogicalCpu": 8, "minimumMemTotalBytes": 32_000_000_000,
            "minimumMemAvailableBytes": 25_769_803_776, "minimumScratchFreeBytes": 85_899_345_920,
            "minimumOutputFreeBytes": 21_474_836_480, "minimumFreeInodes": 100_000,
            "sameFilesystemAtomicNoReplaceRequired": True, "hostSupervisorRequired": True,
            "noCompetingHeavyJob": True, "dockerDaemonRequired": True, "dockerRestartForbidden": True,
            "hostRebootForbidden": True, "maintenanceReservationRequired": True,
            "requiredCgroupVersion": "v2",
        },
        "profile server preconditions drift",
    )
    require(
        payload["resourceObservation"]
        == {"hostCgroupPollIntervalSeconds": 2.0, "sampleBeforeTimeoutStop": True,
            "inspectBeforeTimeoutStop": True, "unknownPeakBlocksSuccess": True},
        "profile resource-observation policy drift",
    )
    require(
        payload["modelContract"]
        == {"sourceRows": SOURCE_ROWS, "logicalRows": LOGICAL_ROWS, "scoreRows": SCORE_ROWS,
            "featureCount": 230, "partitions": PARTITIONS, "seed": 339, "trees": 120,
            "workerSha256": EXPECTED_WORKER_SHA256, "modelInputSetSha256": MODEL_INPUT_SET_SHA256,
            "workerRuntimeSetSha256": WORKER_RUNTIME_SET_SHA256},
        "profile model contract drift",
    )
    runner = payload["runnerSupervisor"]
    require(
        runner
        == {"kind": "systemd-transient-service",
            "preflightUnit": "feelm-b1-r4-server5c20g-t28800-preflight.service",
            "fitUnit": "feelm-b1-r4-server5c20g-t28800-fit.service",
            "scoreUnit": "feelm-b1-r4-server5c20g-t28800-score.service", "cpuQuotaPercent": 100,
            "memoryMaxBytes": 2_147_483_648, "memorySwapMaxBytes": 0, "tasksMax": 512,
            "killMode": "control-group", "timeoutStopSeconds": 900, "cgroupPollIntervalSeconds": 2.0},
        "runner supervisor drift",
    )
    evaluator = payload["evaluationSupervisor"]
    require(
        evaluator["kind"] == "systemd-transient-service"
        and evaluator["cpuQuotaPercent"] == 500
        and evaluator["memoryMaxBytes"] == MAX_MEMORY_BYTES
        and evaluator["memorySwapMaxBytes"] == 0
        and evaluator["tasksMax"] == 4096
        and evaluator["killMode"] == "control-group"
        and evaluator["timeoutStopSeconds"] == 900
        and evaluator["cgroupPollIntervalSeconds"] == 2.0
        and evaluator["monitorCpuQuotaPercent"] == 25
        and evaluator["monitorMemoryMaxBytes"] == 268_435_456
        and evaluator["monitorTasksMax"] == 64,
        "evaluation supervisor drift",
    )
    auth = payload["authorizationPolicy"]
    require(
        auth["profileMutable"] is False
        and auth["currentState"] == "DRAFT_REVIEW_PENDING"
        and auth["implementationGates"] == ["independent-design-review:PASS"]
        and auth["permanentlyForbidden"] == ["deployment", "automatic-retry", "same-run-phase-retry"],
        "profile authorization policy drift",
    )
    return payload, profile_pin


def verify_team_commit(paths: ExecutionPaths) -> None:
    result = subprocess.run(
        ["git", "rev-parse", "origin/develop"],
        cwd=paths.team,
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    require(result.stdout.strip() == TEAM_COMMIT, f"team origin/develop drift: {result.stdout.strip()}")


def inspect_image_id() -> str:
    result = subprocess.run(
        ["docker", "image", "inspect", IMAGE, "--format", "{{.Id}}"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    observed = result.stdout.strip()
    require(observed == IMAGE_ID, f"Docker image ID drift: {observed}")
    return observed


def model_input_records(paths: ExecutionPaths) -> list[dict[str, Any]]:
    """Return only bytes that define the fitted model's data and feature contract."""
    verify_masked_bundle_directory(paths)
    records = [
        verify_expected_pin(paths.training_recipe, "contract/training-recipe.v1.json"),
        verify_expected_pin(paths.artifact_contract, "contract/service-v1.json"),
        verify_expected_pin(paths.models_contract, "contract/MODELS.md"),
        verify_expected_pin(paths.feature_contract, "contract/feature-schema.v1.json"),
        verify_expected_pin(paths.natural_train, "source/natural-train.parquet"),
        verify_expected_pin(paths.masked_train, "source/tmdb-masked-train.parquet"),
        verify_expected_pin(paths.masked_manifest, "source/masked-manifest.json"),
        verify_expected_pin(paths.views_manifest, "source/views-manifest.json"),
        verify_expected_pin(paths.masked_review, "source/masked-review.json"),
    ]
    require(canonical_record_set_sha256(records) == MODEL_INPUT_SET_SHA256, "model-input set drift")
    return records


def worker_runtime_records(paths: ExecutionPaths, image_id: str) -> list[dict[str, Any]]:
    records = [
        pin_file(paths.spark_worker, "implementation/service_v1_b1_spark_worker.py"),
        pin_file(paths.portable_reader, "implementation/combination340_models.py"),
        pin_file(paths.portable_dependency, "implementation/rec046_common.py"),
        virtual_pin("runtime/docker-image-id", image_id),
    ]
    require(records[0]["sha256"] == EXPECTED_WORKER_SHA256, "Spark worker drift")
    require(canonical_record_set_sha256(records) == WORKER_RUNTIME_SET_SHA256, "worker-runtime set drift")
    return records


def execution_records(paths: ExecutionPaths) -> list[dict[str, Any]]:
    _, profile_pin = execution_profile(paths)
    return [
        verify_plan(paths),
        profile_pin,
        pin_file(paths.outer_runner, "execution/run_service_v1_b1_gbt_r4.py"),
        pin_file(paths.tests, "execution/test_service_v1_b1_gbt_runner_r4.py"),
    ]


def training_source_records(paths: ExecutionPaths, image_id: str) -> list[dict[str, Any]]:
    """Compatibility union; locks store the three constituent groups separately."""
    return [*model_input_records(paths), *worker_runtime_records(paths, image_id), *execution_records(paths)]


def make_phase_lock(
    phase: str,
    model_records: Sequence[Mapping[str, Any]],
    runtime_records: Sequence[Mapping[str, Any]],
    execution: Sequence[Mapping[str, Any]],
    control_records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    models = [dict(record) for record in model_records]
    runtime = [dict(record) for record in runtime_records]
    executions = [dict(record) for record in execution]
    controls = [dict(record) for record in control_records]
    all_records = [*models, *runtime, *executions, *controls]
    paths = [record.get("path") for record in all_records]
    require(len(paths) == len(set(paths)), "record groups have overlapping logical paths")
    return {
        "schemaVersion": "feelm-service-v1-b1-input-lock/2",
        "phase": phase,
        "runId": RUN_ID,
        "profileId": PROFILE_ID,
        "modelInputRecords": models,
        "workerRuntimeRecords": runtime,
        "executionRecords": executions,
        "controlReferences": controls,
        "modelInputSetSha256": canonical_record_set_sha256(models),
        "workerRuntimeSetSha256": canonical_record_set_sha256(runtime),
        "executionSetSha256": canonical_record_set_sha256(executions),
        "controlReferenceSetSha256": canonical_record_set_sha256(controls),
        "inputSetSha256": canonical_record_set_sha256(all_records),
        "r2TrainingSourceSetSha256": R2_TRAINING_SOURCE_SET_SHA256,
        "evaluationTargetsRead": False,
    }


def records_equal(left: Sequence[Mapping[str, Any]], right: Sequence[Mapping[str, Any]]) -> bool:
    def compact(records: Sequence[Mapping[str, Any]]) -> list[tuple[str, int, str]]:
        return sorted(
            (str(record.get("path")), int(record.get("bytes", -1)), str(record.get("sha256", "")).lower())
            for record in records
        )

    return compact(left) == compact(right)


def merge_records(*groups: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for record in (item for group in groups for item in group):
        current = dict(record)
        logical = current.get("path")
        require(isinstance(logical, str), "record path missing")
        existing = merged.get(logical)
        require(existing is None or existing == current, f"conflicting duplicate record: {logical}")
        merged[logical] = current
    return [merged[name] for name in sorted(merged)]


def implementation_pins(paths: ExecutionPaths) -> tuple[dict[str, Any], dict[str, Any], str]:
    outer = pin_file(paths.outer_runner, "execution/run_service_v1_b1_gbt_r4.py")
    worker = pin_file(paths.spark_worker, "implementation/service_v1_b1_spark_worker.py")
    return outer, worker, implementation_set_sha256(outer, worker)


@dataclass(frozen=True)
class Mount:
    source: Path
    target: str
    readonly: bool = True


def path_is_evaluation_forbidden(path: Path) -> bool:
    lowered = [part.casefold() for part in path.resolve().parts]
    return path.name.casefold() in FORBIDDEN_EVALUATION_BASENAMES or "text339" in lowered


def validate_mounts(
    mounts: Sequence[Mount],
    *,
    allowed_readonly: Iterable[Path],
    allowed_write: Iterable[Path],
) -> None:
    allowed_ro = {path.resolve() for path in allowed_readonly}
    allowed_rw = {path.resolve() for path in allowed_write}
    require(len({mount.target for mount in mounts}) == len(mounts), "duplicate container mount target")
    for mount in mounts:
        source = mount.source.resolve()
        require(source.exists() and not mount.source.is_symlink(), f"mount source missing or linked: {source}")
        require(mount.target.startswith("/") and ".." not in Path(mount.target).parts, "invalid container target")
        require(not path_is_evaluation_forbidden(source), f"evaluation path forbidden in Spark phase: {source}")
        if mount.readonly:
            require(source in allowed_ro, f"read-only mount is outside the exact allowlist: {source}")
        else:
            require(source in allowed_rw, f"write mount is outside the exact allowlist: {source}")


def _mount_arguments(mounts: Sequence[Mount]) -> list[str]:
    result: list[str] = []
    for mount in mounts:
        specification = f"type=bind,source={mount.source.resolve()},target={mount.target}"
        if mount.readonly:
            specification += ",readonly"
        result.extend(["--mount", specification])
    return result


def spark_container_command(container_name: str, mounts: Sequence[Mount], worker_args: Sequence[str]) -> list[str]:
    return [
        "docker",
        "create",
        "--name",
        container_name,
        "--network",
        "none",
        "--hostname",
        "service-v1-b1",
        "--add-host",
        "service-v1-b1:127.0.0.1",
        "-e",
        "SPARK_LOCAL_IP=127.0.0.1",
        "--cpus",
        "5",
        "--memory",
        "20g",
        "--memory-swap",
        "20g",
        *_mount_arguments(mounts),
        IMAGE,
        "/opt/spark/bin/spark-submit",
        "--master",
        "local[5]",
        "--driver-memory",
        "12g",
        "--conf",
        "spark.sql.shuffle.partitions=8",
        "--conf",
        "spark.sql.adaptive.enabled=false",
        "--conf",
        "spark.ui.enabled=false",
        "--conf",
        "spark.sql.debug.maxToStringFields=1000",
        "--conf",
        "spark.local.dir=/scratch/spark-local",
        "/app/service_v1_b1_spark_worker.py",
        *worker_args,
    ]


def running_container_names(prefix: str = "feelm-b1-") -> list[str]:
    result = subprocess.run(
        ["docker", "ps", "--all", "--filter", f"name={prefix}", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return [name for name in result.stdout.splitlines() if name.startswith(prefix)]


def inspect_container_state(container_name: str) -> dict[str, Any]:
    result = subprocess.run(
        ["docker", "inspect", container_name, "--format", "{{json .State}}"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    state = json.loads(result.stdout)
    require(isinstance(state, dict), "Docker state is not an object")
    return state


def terminate_exact_container(container_name: str) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    stopped = False
    for action in (["stop", "--time", "2"], ["kill"]):
        try:
            state = inspect_container_state(container_name)
            if not bool(state.get("Running")):
                stopped = True
                break
        except Exception as error:
            attempts.append({"action": "inspect-before-" + action[0], "error": repr(error)})
        result = subprocess.run(
            ["docker", *action, container_name], capture_output=True, text=True, timeout=30, check=False
        )
        attempts.append({"action": action, "returncode": result.returncode})
    try:
        state = inspect_container_state(container_name)
        stopped = not bool(state.get("Running"))
    except Exception as error:
        attempts.append({"action": "inspect-final", "error": repr(error)})
        stopped = False
    return {"confirmedStopped": stopped, "attempts": attempts}


def remove_stopped_container(container_name: str) -> None:
    state = inspect_container_state(container_name)
    require(not bool(state.get("Running")), f"refusing to remove running container: {container_name}")
    result = subprocess.run(
        ["docker", "rm", container_name], capture_output=True, text=True, timeout=30, check=False
    )
    if result.returncode != 0:
        raise ContainerCleanupError(f"failed to remove stopped container {container_name}: {result.stderr}")


def find_worker_result(output: str) -> dict[str, Any] | None:
    for line in reversed(output.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("schemaVersion") == "feelm-service-v1-b1-spark-worker/1":
            return value
    return None


def parse_worker_result(output: str) -> dict[str, Any]:
    value = find_worker_result(output)
    if value is not None:
        return value
    raise ValueError("worker did not emit its terminal JSON result")


def read_cgroup_sample(container_name: str) -> dict[str, Any]:
    script = (
        "test -r /sys/fs/cgroup/memory.peak && test -r /sys/fs/cgroup/memory.current && "
        "test -r /sys/fs/cgroup/memory.events && test -r /sys/fs/cgroup/cpu.stat || exit 44; "
        "printf 'cgroup-v2\\n'; cat /sys/fs/cgroup/memory.peak; cat /sys/fs/cgroup/memory.current; "
        "printf '%s\\n' EVENTS; cat /sys/fs/cgroup/memory.events; "
        "printf '%s\\n' CPU; cat /sys/fs/cgroup/cpu.stat"
    )
    result = subprocess.run(
        ["docker", "exec", container_name, "sh", "-c", script],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    require(result.returncode == 0, f"cgroup sample failed: {result.returncode}: {result.stderr.strip()}")
    lines = result.stdout.splitlines()
    require(len(lines) >= 7 and lines[0] == "cgroup-v2" and "EVENTS" in lines and "CPU" in lines,
            "invalid cgroup-v2 sample")
    peak, current = int(lines[1]), int(lines[2])
    require(0 <= current <= peak, "invalid cgroup current/peak relation")
    events_index = lines.index("EVENTS")
    cpu_index = lines.index("CPU")
    require(events_index == 3 and cpu_index > events_index, "invalid cgroup sample sections")
    def keyed(rows: Sequence[str]) -> dict[str, int]:
        values: dict[str, int] = {}
        for row in rows:
            fields = row.split()
            require(len(fields) == 2 and fields[0] not in values
                    and re.fullmatch(r"[0-9]+", fields[1]) is not None,
                    "invalid cgroup counter")
            values[fields[0]] = int(fields[1])
        return values
    memory_events = keyed(lines[events_index + 1:cpu_index])
    cpu = keyed(lines[cpu_index + 1:])
    require({"oom", "oom_kill"} <= set(memory_events) and "usage_usec" in cpu,
            "required cgroup counters missing")
    return {"observedAt": utc_now(), "source": "cgroup-v2", "peakBytes": peak,
            "currentBytes": current, "cpuUsageUsec": cpu["usage_usec"],
            "memoryEvents": memory_events, "oom": memory_events["oom"] > 0,
            "oomKill": memory_events["oom_kill"] > 0}


class CgroupPeakMonitor:
    def __init__(self, container_name: str, interval_seconds: float = CGROUP_SAMPLE_INTERVAL_SECONDS) -> None:
        self.container_name = container_name
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._samples: list[dict[str, Any]] = []
        self._errors: list[dict[str, str]] = []

    def _record(self) -> None:
        try:
            sample = read_cgroup_sample(self.container_name)
        except BaseException as error:
            with self._lock:
                self._errors.append({"observedAt": utc_now(), "error": repr(error)})
                self._errors = self._errors[-16:]
            return
        with self._lock:
            self._samples.append(sample)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self._record()

    def start(self) -> None:
        require(self._thread is None, "cgroup monitor already started")
        self._record()
        self._thread = threading.Thread(target=self._loop, name=f"cgroup-{self.container_name}", daemon=True)
        self._thread.start()

    def sample_before_stop(self) -> None:
        self._record()

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        if self._thread is not None:
            deadline = time.monotonic() + 20.0
            while self._thread.is_alive() and time.monotonic() < deadline:
                self._thread.join(timeout=min(1.0, max(0.0, deadline - time.monotonic())))
            require(not self._thread.is_alive(), "cgroup monitor did not stop before container cleanup")
        with self._lock:
            samples = list(self._samples)
            errors = list(self._errors)
        maximum = max(samples, key=lambda row: row["peakBytes"]) if samples else None
        return {
            "schemaVersion": "feelm-service-v1-b1-host-cgroup-observation/1",
            "pollIntervalSeconds": self.interval_seconds,
            "sampleCount": len(samples),
            "errorCount": len(errors),
            "firstSample": samples[0] if samples else None,
            "maximumSample": maximum,
            "lastSample": samples[-1] if samples else None,
            "recentSamples": samples[-8:],
            "samples": samples,
            "recentErrors": errors,
            "peakBytes": maximum["peakBytes"] if maximum else None,
            "peakSource": maximum["source"] if maximum else None,
            "lastBytes": samples[-1]["currentBytes"] if samples else None,
            "events": dict(samples[-1].get("memoryEvents", {})) if samples else {},
            "status": "OBSERVED" if samples else "UNKNOWN",
        }


def resource_gate(
    worker_result: Mapping[str, Any],
    state: Mapping[str, Any],
    timed_out: bool,
    host_observation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    observation = worker_result.get("resourceObservation")
    if not isinstance(observation, dict):
        observation = {}
    host_observation = dict(host_observation or {})
    worker_peak = observation.get("peakBytes")
    host_peak = host_observation.get("peakBytes")
    peaks = [value for value in (worker_peak, host_peak) if isinstance(value, int) and value >= 0]
    peak = max(peaks) if peaks else None
    source = "host-cgroup-periodic+worker-terminal" if isinstance(host_peak, int) and isinstance(worker_peak, int) else (
        "host-cgroup-periodic" if isinstance(host_peak, int) else observation.get("peakSource")
    )
    worker_status = observation.get("resourceStatus")
    oom = bool(state.get("OOMKilled"))
    exit_code = state.get("ExitCode")
    status = "PASS"
    if timed_out or oom or worker_status == "RESOURCE_STOP" or (isinstance(peak, int) and peak > MAX_MEMORY_BYTES):
        status = "RESOURCE_STOP"
    elif (
        not isinstance(peak, int)
        or peak < 0
        or not isinstance(source, str)
        or worker_status != "PASS"
        or host_observation.get("status") != "OBSERVED"
        or not isinstance(host_observation.get("sampleCount"), int)
        or host_observation.get("sampleCount", 0) < 1
    ):
        status = "UNKNOWN"
    elif exit_code != 0:
        status = "FAILED"
    return {
        "resourceStatus": status,
        "peakBytes": peak,
        "peakSource": source,
        "limitBytes": MAX_MEMORY_BYTES,
        "oomKilled": oom,
        "exitCode": exit_code,
        "timedOut": timed_out,
        "workerPeakBytes": worker_peak,
        "hostPeakBytes": host_peak,
        "hostObservation": host_observation,
    }


def validate_runtime_versions(worker_result: Mapping[str, Any]) -> dict[str, str]:
    versions = worker_result.get("runtimeVersions")
    require(isinstance(versions, dict), "worker runtime versions missing")
    spark_version = versions.get("sparkVersion")
    java_version = versions.get("javaVersion")
    python_version = versions.get("pythonVersion")
    require(spark_version == "4.1.3", f"Spark runtime drift: {spark_version}")
    require(isinstance(java_version, str) and java_version.split(".", 1)[0] == "21", f"Java runtime drift: {java_version}")
    require(isinstance(python_version, str) and python_version != "", "Python runtime version missing")
    return {
        "sparkVersion": spark_version,
        "javaVersion": java_version,
        "pythonVersion": python_version,
    }


@dataclass(frozen=True)
class ContainerResult:
    container_name: str
    command: list[str]
    output: str
    worker: dict[str, Any]
    docker_state: dict[str, Any]
    resource: dict[str, Any]
    elapsed_seconds: float
    cleanup: dict[str, Any]
    host_observation: dict[str, Any]


def _append_run_log(log_path: Path, container_name: str, output: str) -> dict[str, Any]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(f"===== {container_name} =====\n")
        stream.write(output)
        if output and not output.endswith("\n"):
            stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    return pin_file(log_path, str(log_path.resolve()))


def _container_failure_evidence(
    *,
    container_name: str,
    command: Sequence[str],
    output: str,
    worker: Mapping[str, Any],
    state: Mapping[str, Any],
    resource: Mapping[str, Any],
    timed_out: bool,
    elapsed_seconds: float,
    created: bool,
    process_cleanup: Mapping[str, Any],
    cleanup: Mapping[str, Any],
    cleanup_errors: Sequence[Mapping[str, str]],
    log_path: Path,
    log_record: Mapping[str, Any] | None,
    log_error: BaseException | None,
    primary_error: BaseException | None,
    timeout_seconds: int,
    host_observation: Mapping[str, Any],
    pre_stop_state: Mapping[str, Any],
) -> dict[str, Any]:
    encoded_output = output.encode("utf-8", errors="replace")
    log: dict[str, Any] = {
        "path": str(log_path.resolve()),
        "writeStatus": "PASS" if log_error is None else "FAILED",
    }
    if log_record is not None:
        log["pin"] = dict(log_record)
    if log_error is not None:
        log["errorType"] = type(log_error).__name__
        log["error"] = str(log_error)
    return {
        "schemaVersion": "feelm-service-v1-b1-container-failure-evidence/2",
        "containerName": container_name,
        "command": list(command),
        "created": created,
        "timedOut": timed_out,
        "timeoutSeconds": timeout_seconds,
        "elapsedSeconds": elapsed_seconds,
        "dockerState": dict(state),
        "resource": dict(resource),
        "hostCgroupObservation": dict(host_observation),
        "preStopDockerState": dict(pre_stop_state),
        "workerTerminalResultPresent": bool(worker),
        "workerTerminalResult": dict(worker),
        "stdout": output,
        "stdoutBytes": len(encoded_output),
        "stdoutSha256": sha256_bytes(encoded_output),
        "processCleanup": dict(process_cleanup),
        "containerCleanup": dict(cleanup),
        "cleanupErrors": [dict(record) for record in cleanup_errors],
        "log": log,
        "primaryError": None
        if primary_error is None
        else {"errorType": type(primary_error).__name__, "error": str(primary_error)},
    }


def run_container(
    container_name: str,
    command: Sequence[str],
    log_path: Path,
    *,
    expected_worker_status: str,
    timeout_seconds: int = TIMEOUT_SECONDS,
) -> ContainerResult:
    require(container_name.startswith("feelm-b1-"), "refusing non-B1 container name")
    require(container_name not in running_container_names(), f"container already running: {container_name}")
    started = time.monotonic()
    created = False
    process: subprocess.Popen[str] | None = None
    output = ""
    worker: dict[str, Any] = {}
    resource: dict[str, Any] = {}
    state: dict[str, Any] = {}
    cleanup: dict[str, Any] = {"confirmedStopped": False, "removed": False}
    timed_out = False
    state_observed = False
    primary_error: BaseException | None = None
    cleanup_errors: list[dict[str, str]] = []
    process_cleanup: dict[str, Any] = {"killAttempted": False, "waitAttempted": False, "stopped": False}
    log_error: BaseException | None = None
    log_record: dict[str, Any] | None = None
    monitor: CgroupPeakMonitor | None = None
    host_observation: dict[str, Any] = {
        "schemaVersion": "feelm-service-v1-b1-host-cgroup-observation/1",
        "status": "UNKNOWN",
        "sampleCount": 0,
        "peakBytes": None,
        "peakSource": None,
    }
    pre_stop_state: dict[str, Any] = {}
    try:
        create = subprocess.run(list(command), capture_output=True, text=True, timeout=60, check=False)
        require(create.returncode == 0, f"docker create failed: {create.stderr}")
        created = True
        process = subprocess.Popen(
            ["docker", "start", "--attach", container_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        monitor = CgroupPeakMonitor(container_name)
        monitor.start()
        try:
            output, _ = process.communicate(timeout=timeout_seconds)
            monitor.sample_before_stop()
            host_observation = monitor.stop()
        except subprocess.TimeoutExpired as timeout_error:
            timed_out = True
            partial_output = timeout_error.output
            if isinstance(partial_output, bytes):
                partial_output = partial_output.decode("utf-8", errors="replace")
            if not isinstance(partial_output, str):
                partial_output = ""
            monitor.sample_before_stop()
            host_observation = monitor.stop()
            try:
                pre_stop_state = inspect_container_state(container_name)
            except BaseException as inspect_error:
                cleanup_errors.append({"operation": "pre-timeout-stop-inspect", "error": repr(inspect_error)})
            cleanup = terminate_exact_container(container_name)
            try:
                output, _ = process.communicate(timeout=30)
            except BaseException:
                output = partial_output
                raise
            if not output:
                output = partial_output
        state = inspect_container_state(container_name)
        state_observed = True
        worker = find_worker_result(output) or {}
        resource = resource_gate(worker, state, timed_out, host_observation)
        require(not timed_out, f"container exceeded {timeout_seconds} seconds")
        require(state.get("Running") is False, "container still running after attached process returned")
        require(state.get("OOMKilled") is not True, "container was OOM-killed")
        require(state.get("ExitCode") == 0, f"container exit code: {state.get('ExitCode')}")
        require(bool(worker), "worker did not emit its terminal JSON result")
        require(worker.get("status") == expected_worker_status, f"unexpected worker status: {worker.get('status')}")
        validate_runtime_versions(worker)
        require(resource["resourceStatus"] == "PASS", f"resource gate did not pass: {resource}")
        cleanup = {"confirmedStopped": True, "removed": False, "attempts": []}
    except BaseException as error:
        primary_error = error
    finally:
        try:
            if monitor is not None and host_observation.get("status") != "OBSERVED":
                try:
                    monitor.sample_before_stop()
                    host_observation = monitor.stop()
                except BaseException as error:
                    cleanup_errors.append({"operation": "cgroup-monitor-stop", "error": repr(error)})
            if process is not None:
                try:
                    running = process.poll() is None
                except BaseException as error:
                    running = True
                    cleanup_errors.append({"operation": "attach-process-poll", "error": repr(error)})
                if running:
                    process_cleanup["killAttempted"] = True
                    try:
                        process.kill()
                    except BaseException as error:
                        cleanup_errors.append({"operation": "attach-process-kill", "error": repr(error)})
                    finally:
                        process_cleanup["waitAttempted"] = True
                        try:
                            process.wait(timeout=30)
                            process_cleanup["stopped"] = True
                        except BaseException as error:
                            cleanup_errors.append({"operation": "attach-process-wait", "error": repr(error)})
                else:
                    process_cleanup["stopped"] = True
        finally:
            try:
                if created:
                    if not state_observed:
                        try:
                            state = inspect_container_state(container_name)
                            state_observed = True
                        except BaseException as error:
                            cleanup_errors.append({"operation": "container-inspect", "error": repr(error)})
                    if state_observed and bool(state.get("Running")):
                        try:
                            cleanup = terminate_exact_container(container_name)
                        except BaseException as error:
                            cleanup = {"confirmedStopped": False, "removed": False, "attempts": []}
                            cleanup_errors.append({"operation": "container-terminate", "error": repr(error)})
                    elif state_observed:
                        cleanup = {
                            "confirmedStopped": True,
                            "removed": False,
                            "attempts": cleanup.get("attempts", []),
                        }
                    if cleanup.get("confirmedStopped") is True:
                        try:
                            remove_stopped_container(container_name)
                            cleanup["removed"] = True
                        except BaseException as error:
                            cleanup_errors.append({"operation": "container-remove", "error": repr(error)})
                    else:
                        cleanup_errors.append(
                            {"operation": "container-stop-confirmation", "error": "container termination unconfirmed"}
                        )
                else:
                    cleanup = {"confirmedStopped": True, "removed": False, "notCreated": True, "attempts": []}
            finally:
                try:
                    log_record = _append_run_log(log_path, container_name, output)
                except BaseException as error:
                    log_error = error

    if not resource:
        resource = resource_gate(worker, state, timed_out, host_observation)
    elapsed_seconds = time.monotonic() - started
    evidence = _container_failure_evidence(
        container_name=container_name,
        command=command,
        output=output,
        worker=worker,
        state=state,
        resource=resource,
        timed_out=timed_out,
        elapsed_seconds=elapsed_seconds,
        created=created,
        process_cleanup=process_cleanup,
        cleanup=cleanup,
        cleanup_errors=cleanup_errors,
        log_path=log_path,
        log_record=log_record,
        log_error=log_error,
        primary_error=primary_error,
        timeout_seconds=timeout_seconds,
        host_observation=host_observation,
        pre_stop_state=pre_stop_state,
    )
    if cleanup_errors:
        message = f"container cleanup failed for {container_name}: {cleanup_errors}"
        if primary_error is not None:
            message += f"; original failure: {type(primary_error).__name__}: {primary_error}"
        raise ContainerCleanupError(message, evidence) from (primary_error or log_error)
    if primary_error is not None:
        message = f"container execution failed for {container_name}: {type(primary_error).__name__}: {primary_error}"
        if log_error is not None:
            message += f"; run-log persistence failed: {type(log_error).__name__}: {log_error}"
        raise ContainerExecutionError(message, evidence) from primary_error
    if log_error is not None:
        raise ContainerExecutionError(
            f"run-log persistence failed for {container_name}: {type(log_error).__name__}: {log_error}", evidence
        ) from log_error
    return ContainerResult(
        container_name=container_name,
        command=list(command),
        output=output,
        worker=worker,
        docker_state=state,
        resource=resource,
        elapsed_seconds=elapsed_seconds,
        cleanup=cleanup,
        host_observation=host_observation,
    )


def logical_path(paths: ExecutionPaths, path: Path) -> str:
    resolved = path.resolve()
    for prefix, root in (("standalone", paths.standalone), ("team", paths.team)):
        try:
            return prefix + "/" + resolved.relative_to(root).as_posix()
        except ValueError:
            continue
    raise ValueError(f"path is outside the two approved repositories: {path}")


def resolve_logical_path(paths: ExecutionPaths, logical: str) -> Path:
    require(isinstance(logical, str) and logical != "" and "\\" not in logical, "invalid logical path")
    if logical == "ancestor/service-v1-b1-spark-runner.md":
        return paths.r2_plan
    require(not logical.startswith("ancestor/"), "unknown legacy ancestor alias")
    prefix, separator, relative = logical.partition("/")
    require(separator == "/" and relative != "", "logical path lacks repository prefix")
    require(prefix in {"standalone", "team"}, "logical path has unknown repository prefix")
    parts = Path(relative).parts
    require(parts and all(part not in {"", ".", ".."} for part in parts), "unsafe logical path")
    root = paths.standalone if prefix == "standalone" else paths.team
    result = root.joinpath(*parts)
    require(result.resolve().is_relative_to(root.resolve()), "logical path escapes repository")
    return result


def pinned_control(paths: ExecutionPaths, path: Path) -> dict[str, Any]:
    return pin_file(path, logical_path(paths, path))


def _assert_exact_pin(record: Mapping[str, Any], expected: tuple[int, str], label: str) -> None:
    require((record.get("bytes"), record.get("sha256")) == expected, f"{label} evidence drift")


def validate_r2_ancestry(paths: ExecutionPaths) -> dict[str, Any]:
    require_unlinked_path(paths.r2_preflight, paths.standalone, directory=True)
    direct_items = list(paths.r2_preflight.iterdir())
    require(
        all(item.is_file() and not item.is_symlink() for item in direct_items),
        "r2 preflight contains a directory, symlink, or non-regular entry",
    )
    observed_names = {item.name for item in direct_items}
    require(observed_names == set(R2_PREFLIGHT_INVENTORY), "r2 preflight exact inventory drift")
    bundle_inventory: list[dict[str, Any]] = []
    for name in sorted(R2_PREFLIGHT_INVENTORY):
        record = pinned_control(paths, paths.r2_preflight / name)
        _assert_exact_pin(record, R2_PREFLIGHT_INVENTORY[name], f"r2 preflight {name}")
        bundle_inventory.append(record)

    manifest_path = paths.r2_preflight / "manifest.json"
    manifest_pin = pinned_control(paths, manifest_path)
    _assert_exact_pin(manifest_pin, (R2_PREFLIGHT_MANIFEST_BYTES, R2_PREFLIGHT_MANIFEST_SHA256), "r2 preflight manifest")
    manifest = load_json(manifest_path)
    require(
        manifest.get("schemaVersion") == "feelm-service-v1-b1-preflight-manifest/1"
        and manifest.get("runId") == R2_RUN_ID
        and manifest.get("status") == "B1_FULL_PREFLIGHT_COMPLETE_AWAITING_REVIEW"
        and manifest.get("sourceRows") == SOURCE_ROWS
        and manifest.get("logicalRows") == LOGICAL_ROWS
        and manifest.get("partitionCount") == PARTITIONS
        and manifest.get("resourceStatus") == "PASS"
        and manifest.get("modelFitPerformed") is False
        and manifest.get("fitAuthorized") is False
        and manifest.get("trainingSourceSetSha256") == R2_TRAINING_SOURCE_SET_SHA256,
        "r2 preflight manifest semantic drift",
    )
    require(manifest.get("files") == relative_inventory(paths.r2_preflight, exclude=("manifest.json",)), "r2 manifest inventory drift")

    r2_lock = load_json(paths.r2_preflight / "input-lock.json")
    require(
        r2_lock.get("schemaVersion") == "feelm-service-v1-b1-input-lock/1"
        and r2_lock.get("phase") == "preflight"
        and r2_lock.get("trainingSourceSetSha256") == R2_TRAINING_SOURCE_SET_SHA256
        and r2_lock.get("inputSetSha256") == manifest.get("inputSetSha256")
        and r2_lock.get("controlReferenceSetSha256") == manifest.get("controlReferenceSetSha256")
        and r2_lock.get("evaluationTargetsRead") is False,
        "r2 preflight input-lock semantic drift",
    )

    require_unlinked_path(paths.r2_preflight_review, paths.standalone)
    review_pin = pinned_control(paths, paths.r2_preflight_review)
    _assert_exact_pin(review_pin, (R2_PREFLIGHT_REVIEW_BYTES, R2_PREFLIGHT_REVIEW_SHA256), "r2 preflight review")
    review = load_json(paths.r2_preflight_review)
    target = review.get("target")
    require(
        review.get("schemaVersion") == "feelm-service-v1-b1-result-review/1"
        and review.get("phase") == "preflight"
        and review.get("status") == "PASS"
        and review.get("readyForService") is False
        and review.get("modelFitPerformed") is False
        and isinstance(target, dict)
        and target.get("manifest", {}).get("sha256") == R2_PREFLIGHT_MANIFEST_SHA256
        and target.get("input_lock", {}).get("sha256") == R2_PREFLIGHT_INVENTORY["input-lock.json"][1]
        and target.get("resource", {}).get("sha256") == R2_PREFLIGHT_INVENTORY["resource.json"][1],
        "r2 preflight review semantic drift",
    )
    dependency = review.get("dependencyFingerprint")
    require(isinstance(dependency, dict), "r2 review dependency fingerprint missing")
    dependency_files = dependency.get("files")
    dependency_bundles = dependency.get("bundleInventories")
    auditor_record = dependency.get("auditorImplementation")
    require(isinstance(dependency_files, dict) and isinstance(dependency_bundles, dict), "r2 dependency inventory missing")
    require(
        auditor_record == {"bytes": 63_141, "sha256": "e23e3e4e7b9830172b5a717a08bf38f5bef415155eb4235440b830c434ba5020"},
        "r2 auditor pin drift",
    )
    require_unlinked_path(paths.r2_auditor, paths.standalone)
    require(pin_file(paths.r2_auditor) == auditor_record, "r2 auditor current-file drift")
    for logical, expected in dependency_files.items():
        require(isinstance(expected, dict), f"invalid r2 dependency record: {logical}")
        path = resolve_logical_path(paths, logical)
        require_unlinked_path(path, paths.standalone if logical.startswith("standalone/") else paths.team)
        actual = pin_file(path)
        require(actual == expected, f"r2 dependency current-file drift: {logical}")
    expected_bundle_logical = logical_path(paths, paths.r2_preflight)
    require(set(dependency_bundles) == {expected_bundle_logical}, "r2 dependency bundle set drift")
    require(dependency_bundles[expected_bundle_logical] == relative_inventory(paths.r2_preflight), "r2 dependency bundle inventory drift")
    for key, target_record in target.items():
        if not isinstance(target_record, dict) or not isinstance(target_record.get("path"), str):
            continue
        target_path = resolve_logical_path(paths, target_record["path"])
        require_unlinked_path(target_path, paths.standalone if target_record["path"].startswith("standalone/") else paths.team)
        actual = pin_file(target_path)
        require(
            actual.get("bytes") == target_record.get("bytes") and actual.get("sha256") == target_record.get("sha256"),
            f"r2 review target current-file drift: {key}",
        )

    require_unlinked_path(paths.r2_fit_failure, paths.standalone)
    failure_pin = pinned_control(paths, paths.r2_fit_failure)
    _assert_exact_pin(failure_pin, (R2_FIT_FAILURE_BYTES, R2_FIT_FAILURE_SHA256), "r2 fit failure")
    failure = load_json(paths.r2_fit_failure)
    run = failure.get("containerRun")
    state = run.get("dockerState") if isinstance(run, dict) else None
    resource = run.get("resource") if isinstance(run, dict) else None
    require(
        failure.get("schemaVersion") == "feelm-service-v1-b1-failure/1"
        and failure.get("phase") == "fit"
        and failure.get("status") == "FAILED"
        and failure.get("cleanupComplete") is True
        and isinstance(run, dict)
        and run.get("timedOut") is True
        and run.get("workerTerminalResultPresent") is False
        and isinstance(state, dict)
        and state.get("ExitCode") == 143
        and state.get("OOMKilled") is False
        and isinstance(resource, dict)
        and resource.get("resourceStatus") == "RESOURCE_STOP"
        and resource.get("timedOut") is True,
        "r2 fit failure semantic drift",
    )
    command = run.get("command")
    require(
        isinstance(command, list)
        and command[:2] == ["docker", "create"]
        and "--cpus" in command
        and command[command.index("--cpus") + 1] == "4"
        and "--memory" in command
        and command[command.index("--memory") + 1] == "12g"
        and "--master" in command
        and command[command.index("--master") + 1] == "local[4]"
        and "--driver-memory" in command
        and command[command.index("--driver-memory") + 1] == "8g"
        and "fit" in command,
        "r2 fit failure command drift",
    )
    require(not os.path.lexists(paths.output_parent / (R2_RUN_ID + "-fit")), "r2 failed fit unexpectedly has a success bundle")

    require_unlinked_path(paths.r2_outer_runner, paths.standalone)
    require_unlinked_path(paths.r2_plan, paths.standalone)
    r2_runner = pin_file(paths.r2_outer_runner, logical_path(paths, paths.r2_outer_runner))
    _assert_exact_pin(r2_runner, (R2_OUTER_RUNNER_BYTES, R2_OUTER_RUNNER_SHA256), "r2 runner")
    r2_plan = verify_r2_plan(paths)
    return {
        "r2PreflightManifest": manifest_pin,
        "r2PreflightReview": review_pin,
        "r2PreflightBundleInventory": bundle_inventory,
        "r2PreflightDigests": {
            "trainingSourceSetSha256": r2_lock["trainingSourceSetSha256"],
            "controlReferenceSetSha256": r2_lock["controlReferenceSetSha256"],
            "inputSetSha256": r2_lock["inputSetSha256"],
            "implementationSetSha256": manifest["implementationSetSha256"],
        },
        "r2FitFailure": failure_pin,
        "r2FitFailureFacts": {
            "phase": "fit",
            "status": "FAILED",
            "timedOut": True,
            "resourceStatus": "RESOURCE_STOP",
            "exitCode": 143,
            "oomKilled": False,
            "cleanupComplete": True,
            "workerTerminalResultPresent": False,
            "modelWritten": False,
        },
        "r2OuterRunner": r2_runner,
        "r2Plan": r2_plan,
    }


def validate_r3_ancestry(paths: ExecutionPaths) -> dict[str, Any]:
    """Rehash the complete r3 timeout closure and its recursive r2 ancestry."""
    r2 = validate_r2_ancestry(paths)
    implementation_records: list[dict[str, Any]] = []
    for relative, expected in sorted(R3_IMPLEMENTATION_PINS.items()):
        physical = paths.standalone / Path(relative)
        record = pin_file(physical, "standalone/" + relative.replace("\\", "/"))
        _assert_exact_pin(record, expected, f"r3 implementation {relative}")
        implementation_records.append(record)
    evaluator_sha = R3_IMPLEMENTATION_PINS["scripts/evaluate_service_v1_b1_r3.py"][1]
    auditor_source = (paths.standalone / "scripts/audit_service_v1_b1_evaluation_outputs_r3.py").read_bytes()
    require(auditor_source.count(evaluator_sha.encode("ascii")) == 1, "r3 normalized auditor evaluator pin count drift")
    normalized = auditor_source.replace(evaluator_sha.encode("ascii"), b"0" * 64)
    require(sha256_bytes(normalized) == R3_NORMALIZED_EVALUATION_AUDITOR_SHA256, "r3 normalized auditor core drift")

    require_unlinked_path(paths.r3_preflight, paths.standalone, directory=True)
    children = list(paths.r3_preflight.iterdir())
    require(
        {child.name for child in children} == set(R3_PREFLIGHT_INVENTORY)
        and all(child.is_file() and not child.is_symlink() and os.lstat(child).st_nlink == 1 for child in children),
        "r3 preflight exact inventory drift",
    )
    preflight_records: list[dict[str, Any]] = []
    for name, expected in sorted(R3_PREFLIGHT_INVENTORY.items()):
        record = pinned_control(paths, paths.r3_preflight / name)
        _assert_exact_pin(record, expected, f"r3 preflight {name}")
        preflight_records.append(record)
    preflight_manifest = load_json(paths.r3_preflight / "manifest.json")
    require(
        preflight_manifest.get("runId") == R3_RUN_ID
        and preflight_manifest.get("profileId") == R3_PROFILE_ID
        and preflight_manifest.get("status") == "B1_FULL_PREFLIGHT_COMPLETE_AWAITING_REVIEW"
        and preflight_manifest.get("sourceRows") == SOURCE_ROWS
        and preflight_manifest.get("logicalRows") == LOGICAL_ROWS
        and preflight_manifest.get("partitionCount") == PARTITIONS
        and preflight_manifest.get("fitAuthorized") is False
        and preflight_manifest.get("modelFitPerformed") is False
        and preflight_manifest.get("readyForService") is False,
        "r3 preflight semantic drift",
    )
    preflight_review = pinned_control(paths, paths.r3_preflight_review)
    _assert_exact_pin(preflight_review, (R3_PREFLIGHT_REVIEW_BYTES, R3_PREFLIGHT_REVIEW_SHA256), "r3 preflight review")
    preflight_review_payload = load_json(paths.r3_preflight_review)
    require(
        preflight_review_payload.get("runId") == R3_RUN_ID
        and preflight_review_payload.get("phase") == "preflight"
        and preflight_review_payload.get("status") == "PASS"
        and preflight_review_payload.get("readyForService") is False
        and preflight_review_payload.get("modelFitPerformed") is False,
        "r3 preflight review semantic drift",
    )

    failure = pinned_control(paths, paths.r3_fit_failure)
    _assert_exact_pin(failure, (R3_FIT_FAILURE_BYTES, R3_FIT_FAILURE_SHA256), "r3 fit failure")
    failure_payload = load_json(paths.r3_fit_failure)
    container_run = failure_payload.get("containerRun")
    docker_state = container_run.get("dockerState") if isinstance(container_run, dict) else None
    resource = container_run.get("resource") if isinstance(container_run, dict) else None
    require(
        failure_payload.get("schemaVersion") == "feelm-service-v1-b1-r3-failure/1"
        and failure_payload.get("runId") == R3_RUN_ID
        and failure_payload.get("profileId") == R3_PROFILE_ID
        and failure_payload.get("phase") == "fit"
        and failure_payload.get("status") == "FAILED"
        and failure_payload.get("cleanupComplete") is True
        and isinstance(container_run, dict)
        and container_run.get("timedOut") is True
        and container_run.get("workerTerminalResultPresent") is False
        and isinstance(docker_state, dict)
        and docker_state.get("ExitCode") == 143
        and docker_state.get("OOMKilled") is False
        and isinstance(resource, dict)
        and resource.get("resourceStatus") == "RESOURCE_STOP",
        "r3 fit failure semantic drift",
    )
    failure_review = pinned_control(paths, paths.r3_fit_failure_review)
    _assert_exact_pin(
        failure_review, (R3_FIT_FAILURE_REVIEW_BYTES, R3_FIT_FAILURE_REVIEW_SHA256), "r3 fit failure review"
    )
    failure_review_payload = load_json(paths.r3_fit_failure_review)
    decision = failure_review_payload.get("decision")
    require(
        failure_review_payload.get("status") == "PASS"
        and failure_review_payload.get("runId") == R3_RUN_ID
        and failure_review_payload.get("phase") == "fit-failure"
        and isinstance(decision, dict)
        and decision.get("failureIntegrity") == "PASS"
        and decision.get("downstream") == "BLOCK"
        and failure_review_payload.get("downstreamAllowed") is False,
        "r3 failure review semantic drift",
    )
    for forbidden in (
        paths.output_parent / (R3_RUN_ID + "-fit"),
        paths.output_parent / (R3_RUN_ID + "-fit-result-review.json"),
        paths.output_parent / (R3_RUN_ID + "-score"),
        paths.output_parent / (R3_RUN_ID + "-score-result-review.json"),
    ):
        require(not os.path.lexists(forbidden), f"r3 terminal failure has forbidden downstream output: {forbidden}")
    return {
        "r2": r2,
        "r3ImplementationRecords": implementation_records,
        "r3PreflightBundleInventory": preflight_records,
        "r3PreflightManifest": next(record for record in preflight_records if record["path"].endswith("/manifest.json")),
        "r3PreflightReview": preflight_review,
        "r3FitFailure": failure,
        "r3FitFailureReview": failure_review,
        "r3FitFailureFacts": {
            "status": "FAILED", "timedOut": True, "oomKilled": False, "cleanupComplete": True,
            "downstreamBlocked": True, "workerTerminalResultPresent": False, "modelWritten": False,
        },
    }


def r2_control_records(ancestry: Mapping[str, Any]) -> list[dict[str, Any]]:
    records = [dict(record) for record in ancestry["r2PreflightBundleInventory"]]
    records.extend(
        [
            dict(ancestry["r2PreflightReview"]),
            dict(ancestry["r2FitFailure"]),
            dict(ancestry["r2OuterRunner"]),
            dict(ancestry["r2Plan"]),
        ]
    )
    return records


def r3_control_records(ancestry: Mapping[str, Any]) -> list[dict[str, Any]]:
    records = r2_control_records(ancestry["r2"])
    records.extend(dict(record) for record in ancestry["r3ImplementationRecords"])
    records.extend(dict(record) for record in ancestry["r3PreflightBundleInventory"])
    records.extend(
        [dict(ancestry["r3PreflightReview"]), dict(ancestry["r3FitFailure"]), dict(ancestry["r3FitFailureReview"])]
    )
    paths_seen = [record["path"] for record in records]
    require(len(paths_seen) == len(set(paths_seen)), "r2/r3 ancestry record overlap")
    return records


def recovery_reference(
    paths: ExecutionPaths,
    ancestry: Mapping[str, Any],
    outer: Mapping[str, Any],
    worker: Mapping[str, Any],
    profile_pin: Mapping[str, Any],
    execution_digest: str,
) -> dict[str, Any]:
    training_recipe = verify_expected_pin(paths.training_recipe, "contract/training-recipe.v1.json")
    r2 = ancestry["r2"]
    ancestor_records = r3_control_records(ancestry)
    semantic_facts = {
        "r2RunId": R2_RUN_ID,
        "r3RunId": R3_RUN_ID,
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
    return {
        "schemaVersion": "feelm-service-v1-b1-r4-recovery-reference/2",
        "status": "R3_TIMEOUT_ANCESTRY_VERIFIED",
        "runId": RUN_ID,
        "profileId": PROFILE_ID,
        "r2RunId": R2_RUN_ID,
        "r3RunId": R3_RUN_ID,
        "r2PreflightManifest": dict(r2["r2PreflightManifest"]),
        "r2PreflightReview": dict(r2["r2PreflightReview"]),
        "r2PreflightBundleInventory": [dict(record) for record in r2["r2PreflightBundleInventory"]],
        "r2PreflightDigests": dict(r2["r2PreflightDigests"]),
        "r2FitFailure": dict(r2["r2FitFailure"]),
        "r2FitFailureFacts": dict(r2["r2FitFailureFacts"]),
        "r2OuterRunner": dict(r2["r2OuterRunner"]),
        "r2Plan": dict(r2["r2Plan"]),
        "r3ImplementationRecords": [dict(record) for record in ancestry["r3ImplementationRecords"]],
        "r3PreflightManifest": dict(ancestry["r3PreflightManifest"]),
        "r3PreflightReview": dict(ancestry["r3PreflightReview"]),
        "r3PreflightBundleInventory": [dict(record) for record in ancestry["r3PreflightBundleInventory"]],
        "r3FitFailure": dict(ancestry["r3FitFailure"]),
        "r3FitFailureReview": dict(ancestry["r3FitFailureReview"]),
        "r3FitFailureFacts": dict(ancestry["r3FitFailureFacts"]),
        "ancestorRecords": ancestor_records,
        "ancestorRecordSetSha256": canonical_record_set_sha256(ancestor_records),
        "semanticFacts": semantic_facts,
        "semanticFactsSha256": sha256_bytes(canonical_json_bytes(semantic_facts)),
        "sparkWorker": dict(worker),
        "outerRunner": dict(outer),
        "executionProfile": dict(profile_pin),
        "digestComparison": {
            "r2HistoricalTrainingSourceSetSha256": R2_TRAINING_SOURCE_SET_SHA256,
            "trainingRecipe": training_recipe,
            "sourceRows": SOURCE_ROWS,
            "logicalRows": LOGICAL_ROWS,
            "modelInputSetSha256": MODEL_INPUT_SET_SHA256,
            "workerRuntimeSetSha256": WORKER_RUNTIME_SET_SHA256,
            "executionSetSha256": execution_digest,
            "modelInputUnchanged": True,
            "workerRuntimeUnchanged": True,
            "partitionCount": PARTITIONS,
            "seed": 339,
        },
        "modelFitPerformedByPreflight": False,
        "deploymentAuthorized": False,
    }


def validate_preflight_recovery(
    paths: ExecutionPaths,
    audited: Mapping[str, Any],
    ancestry: Mapping[str, Any],
    outer: Mapping[str, Any],
    worker: Mapping[str, Any],
    profile_pin: Mapping[str, Any],
    execution_digest: str,
) -> None:
    reference_path = paths.bundle("preflight") / "recovery-reference.json"
    observed = load_json(reference_path)
    expected = recovery_reference(paths, ancestry, outer, worker, profile_pin, execution_digest)
    require(observed == expected, "r4 preflight recovery reference drift")
    lock = audited.get("inputLock")
    manifest = audited.get("manifest")
    require(isinstance(lock, dict) and isinstance(manifest, dict), "preflight recovery inputs missing")
    ancestry_controls = r3_control_records(ancestry)
    observed_controls = lock.get("controlReferences", [])
    require(isinstance(observed_controls, list), "r4 preflight control references missing")
    observed_by_path = {record.get("path"): record for record in observed_controls if isinstance(record, Mapping)}
    require(all(observed_by_path.get(record["path"]) == record for record in ancestry_controls),
            "r4 preflight ancestor control drift")
    require(lock.get("controlReferenceSetSha256") == canonical_record_set_sha256(observed_controls),
            "phase control digest drift")
    require(manifest.get("recoveryReferenceSha256") == pin_file(reference_path)["sha256"], "recovery hash drift")
    require(manifest.get("r2FitFailureSha256") == R2_FIT_FAILURE_SHA256, "r2 failure manifest pin drift")
    require(manifest.get("r3FitFailureSha256") == R3_FIT_FAILURE_SHA256, "r3 failure manifest pin drift")

def bundle_records(paths: ExecutionPaths, bundle: Path, *, include_manifest: bool = True) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted((item for item in bundle.rglob("*") if item.is_file()), key=lambda item: item.as_posix()):
        if not include_manifest and path.name == "manifest.json":
            continue
        records.append(pinned_control(paths, path))
    return records


def target_pin(paths: ExecutionPaths, path: Path) -> dict[str, Any]:
    return pin_file(path, logical_path(paths, path))


def _review_target_paths(paths: ExecutionPaths, phase: str) -> dict[str, Path]:
    bundle = paths.bundle(phase)
    maps = {
        "preflight": PREFLIGHT_REVIEW_TARGETS,
        "fit": FIT_REVIEW_TARGETS,
        "score": SCORE_REVIEW_TARGETS,
    }
    require(phase in maps, f"unsupported review phase: {phase}")
    result = {key: bundle / relative for key, relative in maps[phase].items()}
    result["outer_runner"] = paths.outer_runner
    result["spark_worker"] = paths.spark_worker
    return result


def review_contract(paths: ExecutionPaths, phase: str) -> dict[str, Any]:
    targets = _review_target_paths(paths, phase)
    pinned = {name: target_pin(paths, path) for name, path in sorted(targets.items())}
    lock_key = "score_input_lock" if phase == "score" else "input_lock"
    lock_name = "score-input-lock.json" if phase == "score" else "input-lock.json"
    phase_lock = load_json(paths.bundle(phase) / lock_name)
    pinned[lock_key]["inputSetSha256"] = phase_lock.get("inputSetSha256")
    for name in (
        "modelInputSetSha256",
        "workerRuntimeSetSha256",
        "executionSetSha256",
        "controlReferenceSetSha256",
    ):
        pinned[lock_key][name] = phase_lock.get(name)
    if phase == "score":
        pinned[lock_key]["scoreInputSetSha256"] = phase_lock.get("scoreInputSetSha256")
    return {
        "schemaVersion": "feelm-service-v1-b1-r4-review-contract/1",
        "phase": phase,
        "target": pinned,
        "reviewPath": logical_path(paths, paths.review(phase)),
        "requiredStatus": "PASS",
    }


def _validate_target_record(record: Any, expected: Mapping[str, Any], name: str) -> None:
    require(isinstance(record, dict), f"review target missing: {name}")
    for key in ("path", "bytes", "sha256"):
        require(record.get(key) == expected.get(key), f"review target {name} {key} drift")


def _record_physical_path(paths: ExecutionPaths, logical: str) -> Path:
    mapping = {
        "contract/training-recipe.v1.json": paths.training_recipe,
        "contract/service-v1.json": paths.artifact_contract,
        "contract/MODELS.md": paths.models_contract,
        "contract/feature-schema.v1.json": paths.feature_contract,
        "source/natural-train.parquet": paths.natural_train,
        "source/natural-score.parquet": paths.natural_score,
        "source/tmdb-masked-train.parquet": paths.masked_train,
        "source/masked-manifest.json": paths.masked_manifest,
        "source/views-manifest.json": paths.views_manifest,
        "source/masked-review.json": paths.masked_review,
        "implementation/service_v1_b1_spark_worker.py": paths.spark_worker,
        "implementation/combination340_models.py": paths.portable_reader,
        "implementation/rec046_common.py": paths.portable_dependency,
        "execution/service-v1-b1-r4-server-fit-recovery.md": paths.plan,
        "execution/ec2-8vcpu32g-local5-fit20g-t28800-profile.json": paths.profile_contract,
        "execution/run_service_v1_b1_gbt_r4.py": paths.outer_runner,
        "execution/test_service_v1_b1_gbt_runner_r4.py": paths.tests,
    }
    require(logical in mapping, f"unknown source record: {logical}")
    return mapping[logical]


def expected_review_dependency(paths: ExecutionPaths, phase: str) -> dict[str, Any]:
    require(phase in {"preflight", "fit", "score"}, "unsupported dependency phase")
    records = [
        *model_input_records(paths),
        *worker_runtime_records(paths, inspect_image_id()),
        *execution_records(paths),
    ]
    if phase == "score":
        records.extend(score_input_records(paths))
    files: dict[str, dict[str, Any]] = {}
    for record in records:
        if record["path"] == "runtime/docker-image-id":
            continue
        physical = _record_physical_path(paths, record["path"])
        files[logical_path(paths, physical)] = pin_file(physical)
    ancestry = validate_r3_ancestry(paths)
    for record in r3_control_records(ancestry):
        physical = resolve_logical_path(paths, record["path"])
        if physical.parent.resolve() not in {paths.r2_preflight.resolve(), paths.r3_preflight.resolve()}:
            files[record["path"]] = pin_file(physical)
    for physical in _canonical_control_paths(paths, phase).values():
        logical = logical_path(paths, physical)
        files[logical] = pin_file(physical)
    publication_source = paths.standalone / "scripts/service_v1_b1_r4_publication.py"
    files[logical_path(paths, publication_source)] = pin_file(publication_source)
    phases = ["preflight"] if phase == "preflight" else (
        ["preflight", "fit"] if phase == "fit" else ["preflight", "fit", "score"]
    )
    bundles = {
        logical_path(paths, paths.r2_preflight): relative_inventory(paths.r2_preflight),
        logical_path(paths, paths.r3_preflight): relative_inventory(paths.r3_preflight),
    }
    for current in phases:
        bundle = paths.bundle(current)
        require_unlinked_path(bundle, paths.standalone, directory=True)
        bundles[logical_path(paths, bundle)] = relative_inventory(bundle)
        if current != phase:
            parent_review = paths.review(current)
            require_unlinked_path(parent_review, paths.standalone)
            files[logical_path(paths, parent_review)] = pin_file(parent_review)
    return {
        "files": files,
        "bundleInventories": bundles,
        "auditorImplementation": pin_file(paths.spark_auditor),
        "dockerImageId": IMAGE_ID,
        "runId": RUN_ID,
        "profileId": PROFILE_ID,
    }


def expected_review_dependency_sha256(paths: ExecutionPaths, phase: str) -> str:
    envelope = expected_review_dependency(paths, phase)
    records: list[dict[str, Any]] = []
    for logical, record in envelope["files"].items():
        records.append({"path": logical, "bytes": record["bytes"], "sha256": record["sha256"]})
    for root, children in envelope["bundleInventories"].items():
        for relative, record in children.items():
            records.append({"path": root.rstrip("/") + "/" + relative,
                            "bytes": record["bytes"], "sha256": record["sha256"]})
    auditor = envelope["auditorImplementation"]
    records.append({"path": logical_path(paths, paths.spark_auditor),
                    "bytes": auditor["bytes"], "sha256": auditor["sha256"]})
    records.append(virtual_pin("runtime/docker-image-id", IMAGE_ID))
    return canonical_record_set_sha256(merge_records(records))


def validate_publication_evidence(
    value: Any, *, role: str, phase: str, final: Path, failure: Path
) -> None:
    keys = {"schemaVersion", "mode", "role", "finalPath", "failurePath", "claimPath", "tempPath",
            "token", "claimStDev", "claimStIno", "filesystemType", "publisher",
            "dependencyFingerprintAtAcquire", "dependencyFingerprintBeforeRename", "renameNoReplaceProbe",
            "requiredPostconditions"}
    require(isinstance(value, Mapping) and set(value) == keys, "publication evidence field drift")
    require(value["schemaVersion"] == "feelm-service-v1-b1-r4-publication-evidence/1"
            and value["mode"] == "LINUX_RENAME_NOREPLACE" and value["role"] == role,
            "publication evidence identity drift")
    require(value["finalPath"] == str(final.absolute()) and value["failurePath"] == str(failure.absolute()),
            "publication terminal path drift")
    claim_path = Path(str(value["claimPath"]))
    temp_path = Path(str(value["tempPath"]))
    require(claim_path == final.with_name("." + final.name + ".claim").absolute()
            and temp_path.parent == final.parent.absolute()
            and temp_path.name.startswith("." + final.name + ".tmp-"),
            "publication claim/temp path drift")
    require(type(value["claimStDev"]) is int and value["claimStDev"] >= 0
            and type(value["claimStIno"]) is int and value["claimStIno"] >= 0,
            "publication claim identity drift")
    require(isinstance(value["token"], str) and re.fullmatch(r"[0-9a-f]{32}", value["token"]) is not None,
            "publication token drift")
    require(value["filesystemType"] in {"ext2/ext3", "xfs"} and value["renameNoReplaceProbe"] is True,
            "publication filesystem drift")
    for key in ("dependencyFingerprintAtAcquire", "dependencyFingerprintBeforeRename"):
        require(isinstance(value[key], str) and re.fullmatch(r"[0-9a-f]{64}", value[key]) is not None,
                "publication dependency digest drift")
    require(value["dependencyFingerprintAtAcquire"] == value["dependencyFingerprintBeforeRename"],
            "publication dependency changed before rename")
    publisher = value["publisher"]
    require(isinstance(publisher, Mapping) and set(publisher) == {"path", "bytes", "sha256"},
            "publication publisher pin shape drift")
    publisher_path = Path(str(publisher["path"]))
    expected_publisher = Path(__file__).resolve().with_name("service_v1_b1_r4_publication.py")
    require(publisher_path.resolve() == expected_publisher
            and pin_file(expected_publisher, str(expected_publisher)) == dict(publisher),
            "publication publisher source drift")
    post = value["requiredPostconditions"]
    require(isinstance(post, Mapping) and set(post) == {"publishedBytesRehashRequired",
        "fileAndParentFsyncRequired", "dependencyFingerprintStableRequired", "claimIdentityMatchRequired",
        "claimRemovalRequired"} and all(item is True for item in post.values()),
        "publication postconditions drift")
    require(not os.path.lexists(claim_path) and not os.path.lexists(temp_path),
            "publication closure is incomplete")


def validate_review(paths: ExecutionPaths, phase: str, review_path: Path | None = None) -> dict[str, Any]:
    path = (review_path or paths.review(phase)).resolve()
    require(path == paths.review(phase).resolve(), "review path is not the canonical sibling")
    review = load_json(path)
    require(
        set(review)
        == {
            "schemaVersion", "runId", "profileId", "phase", "status", "createdAt", "target",
            "reviewer", "dependencyFingerprint", "checks", "decision", "publication",
        },
        f"{phase} review top-level field set drift",
    )
    require(review.get("schemaVersion") == "feelm-service-v1-b1-r4-spark-result-review/1",
            f"{phase} review schema drift")
    require(review.get("status") == "PASS" and review.get("phase") == phase, f"{phase} review identity/status drift")
    require(
        review.get("runId") == RUN_ID
        and review.get("profileId") == PROFILE_ID,
        f"{phase} review authority flags drift",
    )
    created_at = review.get("createdAt")
    require(isinstance(created_at, str), f"{phase} review timestamp missing")
    parsed_created_at = dt.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    require(parsed_created_at.tzinfo is not None, f"{phase} review timestamp is not timezone-aware")
    checks = review.get("checks")
    expected_check_keys = {
        "preflight": {"resource", "recovery", "identity", "server"},
        "fit": {"resource", "recovery", "parentChain", "identity", "fit", "server"},
        "score": {"resource", "recovery", "parentChain", "predictions", "server"},
    }[phase]
    require(isinstance(checks, dict) and set(checks) == expected_check_keys, f"{phase} review check set drift")
    resource_check = checks.get("resource")
    require(
        isinstance(resource_check, dict)
        and set(resource_check)
        == {
            "stages", "peakBytes", "cleanupConfirmed", "labelMountAbsent",
            "measurementIndependentlyObserved", "measurementCheckedAgainstPinnedWorkerLog",
        }
        and resource_check.get("stages") == (2 if phase == "preflight" else 1)
        and type(resource_check.get("peakBytes")) is int
        and 0 < resource_check["peakBytes"] < MAX_MEMORY_BYTES
        and resource_check.get("cleanupConfirmed") is True
        and resource_check.get("labelMountAbsent") is True
        and resource_check.get("measurementIndependentlyObserved") is False
        and resource_check.get("measurementCheckedAgainstPinnedWorkerLog") is True,
        f"{phase} review resource/cleanup check drift",
    )
    require(isinstance(checks.get("recovery"), dict), f"{phase} review recovery check missing")
    require(isinstance(checks.get("server"), dict), f"{phase} review server check missing")
    if phase != "preflight":
        require(checks.get("parentChain") == "PASS", f"{phase} review parent chain did not pass")
    if phase in {"preflight", "fit"}:
        require(isinstance(checks.get("identity"), dict), f"{phase} review identity check missing")
    if phase == "fit":
        require(isinstance(checks.get("fit"), dict), "fit review model audit missing")
    if phase == "score":
        require(isinstance(checks.get("predictions"), dict), "score review prediction audit missing")
    require_unlinked_path(paths.spark_auditor, paths.standalone)
    auditor_pin = pin_file(paths.spark_auditor, str(paths.spark_auditor.absolute()))
    reviewer = review.get("reviewer")
    require(
        isinstance(reviewer, dict)
        and set(reviewer) == {"kind", "sessionId", "host", "processId"}
        and reviewer.get("kind") == "INDEPENDENT_SESSION"
        and isinstance(reviewer.get("sessionId"), str) and reviewer["sessionId"]
        and isinstance(reviewer.get("host"), str) and reviewer["host"]
        and type(reviewer.get("processId")) is int and reviewer["processId"] > 0,
        f"{phase} review has invalid independent reviewer identity",
    )
    dependency = review.get("dependencyFingerprint")
    require(isinstance(dependency, str) and re.fullmatch(r"[0-9a-f]{64}", dependency) is not None,
            f"{phase} review dependency fingerprint missing")
    require(dependency == expected_review_dependency_sha256(paths, phase),
            f"{phase} review dependency closure drift")
    expected_decision = {
        "sparkIntegrity": "PASS", "fitEligible": phase == "preflight", "scoreEligible": phase == "fit",
        "evaluationSelectionEligible": phase == "score", "deploymentAuthorized": False,
    }
    require(review.get("decision") == expected_decision, f"{phase} review decision drift")
    validate_publication_evidence(review.get("publication"), role="REVIEWER", phase=phase + "-review",
                                  final=paths.review(phase),
                                  failure=paths.review(phase).with_name(paths.review(phase).name[:-5] + "-failure.json"))
    require(review["publication"]["dependencyFingerprintAtAcquire"] == dependency,
            f"{phase} review publication dependency drift")
    expected = review_contract(paths, phase)["target"]
    observed = review.get("target")
    require(isinstance(observed, dict), f"{phase} review target map missing")
    require(set(expected) == set(observed), f"{phase} review target set drift")
    for name, record in expected.items():
        _validate_target_record(observed.get(name), record, name)

    manifest = load_json(paths.bundle(phase) / "manifest.json")
    validate_publication_evidence(manifest.get("publication"), role="PRODUCER", phase=phase,
                                  final=paths.bundle(phase), failure=paths.failure(phase))
    require(manifest["publication"]["dependencyFingerprintAtAcquire"] == manifest.get("inputSetSha256"),
            f"{phase} producer publication dependency drift")
    require(
        manifest.get("files") == relative_inventory(paths.bundle(phase), exclude=("manifest.json",)),
        f"{phase} manifest artifact inventory drift",
    )
    expected_status = {
        "preflight": "B1_FULL_PREFLIGHT_COMPLETE_AWAITING_REVIEW",
        "fit": "B1_MODEL_FIT_COMPLETE_AUDIT_PENDING",
        "score": "B1_NATURAL_SCORE_COMPLETE_AUDIT_PENDING",
    }[phase]
    require(manifest.get("status") == expected_status, f"{phase} manifest status drift")
    require(manifest.get("resourceStatus") == "PASS", f"{phase} manifest resource status drift")
    if phase == "preflight":
        require(
            manifest.get("sourceRows") == SOURCE_ROWS
            and manifest.get("logicalRows") == LOGICAL_ROWS
            and manifest.get("partitionCount") == PARTITIONS
            and manifest.get("fitAuthorized") is False
            and manifest.get("modelFitPerformed") is False
            and manifest.get("scorePerformed") is False
            and manifest.get("readyForService") is False,
            "preflight manifest critical census/authority drift",
        )
    elif phase == "fit":
        require(
            manifest.get("sourceRows") == SOURCE_ROWS
            and manifest.get("logicalRows") == LOGICAL_ROWS
            and manifest.get("partitionCount") == PARTITIONS
            and manifest.get("scoringAuthorized") is False
            and manifest.get("readyForService") is False
            and manifest.get("modelFitPerformed") is True
            and manifest.get("scorePerformed") is False,
            "fit manifest critical census/authority drift",
        )
    else:
        require(
            manifest.get("rows") == SCORE_ROWS
            and manifest.get("evaluationAuthorized") is False
            and manifest.get("readyForService") is False
            and manifest.get("modelFitPerformed") is False
            and manifest.get("scorePerformed") is True,
            "score manifest critical census/authority drift",
        )
    input_name = "score-input-lock.json" if phase == "score" else "input-lock.json"
    phase_lock = load_json(paths.bundle(phase) / input_name)
    review_lock_key = "score_input_lock" if phase == "score" else "input_lock"
    require(phase_lock.get("runId") == RUN_ID and phase_lock.get("profileId") == PROFILE_ID, f"{phase} lock identity drift")
    for name in (
        "modelInputSetSha256",
        "workerRuntimeSetSha256",
        "executionSetSha256",
        "controlReferenceSetSha256",
        "inputSetSha256",
    ):
        require(manifest.get(name) == phase_lock.get(name), f"{phase} {name} manifest drift")
        require(observed[review_lock_key].get(name) == phase_lock.get(name), f"{phase} review omitted {name}")
    if phase == "score":
        require(
            manifest.get("scoreInputSetSha256") == phase_lock.get("scoreInputSetSha256")
            and observed[review_lock_key].get("scoreInputSetSha256") == phase_lock.get("scoreInputSetSha256"),
            "score review omitted phase-only score input digest",
        )
    require(
        observed[review_lock_key].get("inputSetSha256") == phase_lock.get("inputSetSha256"),
        f"{phase} review omitted phase input digest",
    )
    require(manifest.get("inputSetSha256") == phase_lock.get("inputSetSha256"), f"{phase} input digest drift")
    require(manifest.get("runId") == RUN_ID and manifest.get("profileId") == PROFILE_ID, f"{phase} manifest identity drift")
    outer, worker, implementation = implementation_pins(paths)
    require(manifest.get("outerRunnerSha256") == outer["sha256"], f"{phase} outer runner drift")
    require(manifest.get("sparkWorkerSha256") == worker["sha256"], f"{phase} Spark worker drift")
    require(manifest.get("implementationSetSha256") == implementation, f"{phase} implementation set drift")
    return {
        "review": review,
        "reviewPin": target_pin(paths, path),
        "manifest": manifest,
        "manifestPin": target_pin(paths, paths.bundle(phase) / "manifest.json"),
        "inputLock": phase_lock,
    }


def validate_preflight_review(paths: ExecutionPaths, review_path: Path | None = None) -> dict[str, Any]:
    return validate_review(paths, "preflight", review_path)


def validate_fit_review(paths: ExecutionPaths, review_path: Path | None = None) -> dict[str, Any]:
    result = validate_review(paths, "fit", review_path)
    inventory = load_json(paths.bundle("fit") / "model-file-inventory.json")
    validate_model_inventory(paths.bundle("fit") / "model" / "native", inventory)
    return result


def validate_score_review(paths: ExecutionPaths, review_path: Path | None = None) -> dict[str, Any]:
    return validate_review(paths, "score", review_path)


def make_stage(final: Path, review: Path, candidate: Path | None = None) -> Path:
    require(not os.path.lexists(final), f"immutable bundle already exists: {final}")
    require(not os.path.lexists(review), f"immutable review already exists: {review}")
    final.parent.mkdir(parents=True, exist_ok=True)
    stage = candidate or final.with_name(f".{final.name}.tmp-{uuid.uuid4().hex}")
    require(stage.parent == final.parent and stage.name.startswith(f".{final.name}.tmp-"), "invalid staging candidate")
    require(not os.path.lexists(stage), f"staging collision: {stage}")
    stage.mkdir()
    return stage


@dataclass(frozen=True)
class OwnedPath:
    path: Path
    st_dev: int
    st_ino: int
    mode: int


def capture_owned_path(path: Path, *, directory: bool = True) -> OwnedPath:
    info = os.lstat(path)
    require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            and not stat.S_ISLNK(info.st_mode), "owned path type drift")
    if not directory:
        require(info.st_nlink == 1, "owned file hard-link alias")
    return OwnedPath(path.absolute(), info.st_dev, info.st_ino, info.st_mode)


def verify_owned_path(owned: OwnedPath) -> None:
    info = os.lstat(owned.path)
    require((info.st_dev, info.st_ino, info.st_mode) ==
            (owned.st_dev, owned.st_ino, owned.mode),
            f"owned path identity drift: {owned.path}")


def cleanup_owned_path(owned: OwnedPath) -> None:
    verify_owned_path(owned)
    cleanup_path(owned.path)


def cleanup_path(path: Path) -> None:
    if path.is_symlink():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    elif os.path.lexists(path):
        path.unlink()
    require(not os.path.lexists(path), f"cleanup failed: {path}")


def replace_json_in_stage(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.replace-{uuid.uuid4().hex}")
    try:
        write_json_exclusive(temporary, value)
        os.replace(temporary, path)
    finally:
        if os.path.lexists(temporary):
            temporary.unlink()


def manifest_files(stage: Path) -> dict[str, dict[str, Any]]:
    return relative_inventory(stage, exclude=("manifest.json",))


def write_manifest_last(stage: Path, fields: Mapping[str, Any]) -> dict[str, Any]:
    require(not os.path.lexists(stage / "manifest.json"), "manifest must be written once and last")
    manifest = dict(fields)
    manifest["files"] = manifest_files(stage)
    write_json_exclusive(stage / "manifest.json", manifest)
    require(
        set(relative_inventory(stage)) == {*manifest["files"], "manifest.json"},
        "file set changed while writing manifest",
    )
    return manifest


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def embedded_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(value)
    raw = canonical_json_bytes(payload)
    return {"bytes": len(raw), "sha256": sha256_bytes(raw), "payload": payload}


def phase_execution_profile(
    phase: str,
    profile: Mapping[str, Any],
    profile_pin: Mapping[str, Any],
    outer: Mapping[str, Any],
    execution_digest: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": "feelm-service-v1-b1-phase-execution-profile/1",
        "runId": RUN_ID,
        "profileId": PROFILE_ID,
        "phase": phase,
        "profileContract": dict(profile_pin),
        "outerRunner": dict(outer),
        "executionSetSha256": execution_digest,
        "resolved": dict(profile),
    }


def command_document(phase: str, image_id: str, sequence: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    timeout_key = "preflight-full" if phase == "preflight" else phase
    return {
        "schemaVersion": "feelm-service-v1-b1-command/2",
        "runId": RUN_ID,
        "profileId": PROFILE_ID,
        "phase": phase,
        "createdAt": utc_now(),
        "dockerImage": IMAGE,
        "dockerImageId": image_id,
        "timeoutSeconds": PHASE_TIMEOUT_SECONDS[timeout_key],
        "resources": {
            "dockerCpus": "5",
            "dockerMemory": "20g",
            "dockerMemorySwap": "20g",
            "sparkMaster": "local[5]",
            "sparkDriverMemory": "12g",
            "shufflePartitions": PARTITIONS,
            "adaptiveExecution": False,
        },
        "sequence": [dict(record) for record in sequence],
    }


def capture_failure_material(
    stage: Path,
    context: Mapping[str, Any],
    error: BaseException,
    *,
    stage_owned: bool = True,
) -> dict[str, Any]:
    material: dict[str, Any] = {}
    for key, value in context.items():
        if isinstance(value, Mapping):
            material[key] = dict(value)
        elif isinstance(value, list):
            material[key] = list(value)
        else:
            material[key] = value
    json_names = {
        "phaseInputLock": ("evaluation-input-lock.json"
                           if context.get("phase") in {"calibrate-select", "confirmation"}
                           else "score-input-lock.json" if context.get("phase") == "score"
                           else "input-lock.json"),
        "recoveryReference": "recovery-reference.json",
        "phaseReference": "fit-reference.json" if context.get("phase") == "score" else "preflight-reference.json",
        "executionProfile": "execution-profile.json",
        "commandDocument": "command.json",
    }
    for key, name in json_names.items():
        path = stage / name
        if stage_owned and path.is_file():
            material[key] = load_json(path)
    log_path = stage / "run.log"
    if stage_owned and log_path.is_file():
        raw = log_path.read_bytes()
        material["runLog"] = {
            "bytes": len(raw),
            "sha256": sha256_bytes(raw),
            "content": raw.decode("utf-8", errors="replace"),
        }
    else:
        evidence = getattr(error, "evidence", None)
        output = evidence.get("stdout") if isinstance(evidence, Mapping) else ""
        if not isinstance(output, str):
            output = ""
        raw = output.encode("utf-8", errors="replace")
        material["runLog"] = {"bytes": len(raw), "sha256": sha256_bytes(raw), "content": output}
    resource_path = stage / "resource.json"
    if stage_owned and resource_path.is_file():
        material["phaseResource"] = load_json(resource_path)
    return material


def completed_container_evidence(result: ContainerResult, worker_action: str) -> dict[str, Any]:
    raw = result.output.encode("utf-8", errors="replace")
    return {
        "schemaVersion": "feelm-service-v1-b1-completed-container-evidence/1",
        "workerAction": worker_action,
        "containerName": result.container_name,
        "command": list(result.command),
        "elapsedSeconds": result.elapsed_seconds,
        "dockerState": dict(result.docker_state),
        "resource": dict(result.resource),
        "hostCgroupObservation": dict(result.host_observation),
        "workerTerminalResultPresent": bool(result.worker),
        "workerTerminalResult": dict(result.worker),
        "stdoutBytes": len(raw),
        "stdoutSha256": sha256_bytes(raw),
        "cleanup": dict(result.cleanup),
        "timedOut": False,
    }


FAILURE_STAGES = frozenset({"PRE_CONTAINER", "HOST_GATE", "INPUT_LOCK", "CONTAINER_CREATE",
                            "WORKER", "TIMEOUT", "OUTPUT_VALIDATION", "PUBLICATION", "CLEANUP"})
FAILURE_KINDS = frozenset({"CONTRACT", "RESOURCE", "TIMEOUT", "OOM", "PROCESS", "IO", "AUDIT", "CLEANUP"})


def _failure_classification(
    error: BaseException, material: Mapping[str, Any], *, timed_out: bool,
    oom_killed: bool, created: bool, cleanup_complete: bool,
) -> tuple[str, str]:
    explicit_stage = material.get("failureStage")
    explicit_kind = material.get("failureKind")
    if timed_out:
        return "TIMEOUT", "TIMEOUT"
    if oom_killed:
        return "WORKER", "OOM"
    if not cleanup_complete:
        return "CLEANUP", "CLEANUP"
    if isinstance(explicit_stage, str) and explicit_stage in FAILURE_STAGES:
        stage = explicit_stage
    else:
        stage = "WORKER" if created else "PRE_CONTAINER"
    if isinstance(explicit_kind, str) and explicit_kind in FAILURE_KINDS:
        kind = explicit_kind
    elif isinstance(error, (OSError, IOError)):
        kind = "IO"
    elif isinstance(error, (subprocess.SubprocessError, ContainerExecutionError)):
        kind = "PROCESS"
    else:
        kind = "CONTRACT"
    return stage, kind


def _failure_resource(evidence_map: Mapping[str, Any], effective_resource: Mapping[str, Any]) -> dict[str, Any]:
    evaluator_resource = evidence_map.get("evaluationResource")
    if isinstance(evaluator_resource, Mapping) and evaluator_resource:
        source_samples = evaluator_resource.get("samples", [])
        interval = evaluator_resource.get("sampleIntervalSeconds", CGROUP_SAMPLE_INTERVAL_SECONDS)
        final_events = evaluator_resource.get("finalEvents", {})
        observed_peak = (evaluator_resource.get("peaks", {}).get("memoryPeakBytes")
                         if isinstance(source_samples, list) and source_samples else None)
        observed_last = (source_samples[-1].get("memoryCurrentBytes")
                         if isinstance(source_samples, list) and source_samples
                         and isinstance(source_samples[-1], Mapping) else None)
        status = ("OBSERVED" if source_samples else
                  "MEASUREMENT_FAILED" if evaluator_resource.get("status") == "MEASUREMENT_FAILED"
                  else "NOT_STARTED")
        cgroup_version = "v2" if source_samples else "NOT_STARTED"
    else:
        host = evidence_map.get("hostCgroupObservation")
        if not isinstance(host, Mapping):
            host = effective_resource.get("hostObservation")
        if not isinstance(host, Mapping):
            host = evidence_map.get("hostObservation")
        if not isinstance(host, Mapping):
            host = {}
        source_samples = host.get("samples", host.get("recentSamples", []))
        interval = host.get("pollIntervalSeconds", CGROUP_SAMPLE_INTERVAL_SECONDS)
        final_events = host.get("events", {})
        observed_peak = host.get("peakBytes") if source_samples else None
        observed_last = host.get("lastBytes") if source_samples else None
        status = ("OBSERVED" if source_samples else
                  "MEASUREMENT_FAILED" if host.get("status") in {"UNKNOWN", "MEASUREMENT_FAILED"}
                  else "NOT_STARTED")
        cgroup_version = "v2" if source_samples and host.get("peakSource", "cgroup-v2") == "cgroup-v2" else (
            "v1" if source_samples else "NOT_STARTED")
    require(type(interval) in (int, float) and not isinstance(interval, bool)
            and math.isfinite(float(interval)) and float(interval) > 0,
            "failure resource poll interval drift")
    samples: list[dict[str, Any]] = []
    if isinstance(source_samples, list):
        for row in source_samples:
            require(isinstance(row, Mapping), "failure resource sample shape drift")
            memory_events = row.get("memoryEvents", {})
            if not isinstance(memory_events, Mapping):
                memory_events = {}
            normalized = {
                "observedAt": str(row.get("observedAt")),
                "memoryCurrentBytes": row.get("memoryCurrentBytes", row.get("currentBytes")),
                "memoryPeakBytes": row.get("memoryPeakBytes", row.get("peakBytes")),
                "cpuUsageUsec": row.get("cpuUsageUsec", 0),
                "oom": row.get("oom", int(memory_events.get("oom", 0)) > 0),
                "oomKill": row.get("oomKill", (int(memory_events.get("oom_kill", 0)) > 0
                                                 or int(memory_events.get("oomKill", 0)) > 0)),
            }
            _parse_utc(normalized["observedAt"])
            require(all(type(normalized[name]) is int and normalized[name] >= 0
                        for name in ("memoryCurrentBytes", "memoryPeakBytes", "cpuUsageUsec"))
                    and normalized["memoryCurrentBytes"] <= normalized["memoryPeakBytes"]
                    and type(normalized["oom"]) is bool and type(normalized["oomKill"]) is bool,
                    "failure resource sample value drift")
            samples.append(normalized)
    events: dict[str, int] = {}
    if isinstance(final_events, Mapping):
        for name, value in final_events.items():
            require(isinstance(name, str) and type(value) is int and value >= 0,
                    "failure resource event drift")
            events[name] = value
    if samples and not events:
        events = {"oom": int(samples[-1]["oom"]), "oomKill": int(samples[-1]["oomKill"])}
    if samples:
        require(type(observed_peak) is int and observed_peak >= max(
            row["memoryPeakBytes"] for row in samples), "failure resource peak drift")
        require(type(observed_last) is int and observed_last == samples[-1]["memoryCurrentBytes"],
                "failure resource last sample drift")
    else:
        require(observed_peak is None and observed_last is None,
                "failure resource summary without samples")
    return {"status": status, "pollIntervalSeconds": float(interval), "samples": samples,
            "peakMemoryBytes": observed_peak,
            "lastMemoryBytes": observed_last,
            "cgroupVersion": cgroup_version, "events": events}


def _authorization_evidence(control_state: Mapping[str, Any]) -> list[dict[str, Any]]:
    suffixes = {"deliveryReview": "-delivery-manifest-result-review.json",
                "serverReceiptReview": "-server-receipt-result-review.json",
                "preflightReview": "-preflight-result-review.json",
                "fitReview": "-fit-result-review.json", "scoreReview": "-score-result-review.json",
                "selectionReview": "-selection-result-review.json"}
    records = [dict(record) for record in control_state.get("records", []) if isinstance(record, Mapping)]
    result: list[dict[str, Any]] = []
    for name, suffix in suffixes.items():
        payload = control_state.get("payloads", {}).get(name)
        if not isinstance(payload, Mapping) or payload.get("status") != "PASS":
            continue
        matches = [record for record in records if str(record.get("path", "")).endswith(suffix)]
        require(len(matches) == 1, f"authorization review pin missing or ambiguous: {name}")
        decision = payload.get("decision")
        require(isinstance(decision, Mapping), f"authorization review decision missing: {name}")
        result.append({"name": name, "pin": matches[0], "decision": dict(decision)})
    return result


def validate_phase_failure_payload(value: Mapping[str, Any], phase: str) -> None:
    keys = {"schemaVersion", "status", "runId", "profileId", "phase", "attemptOrdinal",
            "startedAt", "failedAt", "elapsedSeconds", "failureStage", "failureKind", "error",
            "producer", "profile", "delivery", "receipt", "phaseInputLock", "ancestorClosure",
            "authorizationEvidence", "hostGate", "command", "logs", "resource", "container",
            "timeout", "cleanup", "namespaceCensus", "outputState", "dependencyFingerprintBefore",
            "dependencyFingerprintAfter", "publication", "readyForService", "deploymentAuthorized"}
    require(isinstance(value, dict) and set(value) == keys,
            "phase failure exact top-level fields")
    require(value["schemaVersion"] == "feelm-service-v1-b1-r4-phase-failure/2"
            and value["status"] == "FAILED" and value["runId"] == RUN_ID
            and value["profileId"] == PROFILE_ID and value["phase"] == phase
            and value["attemptOrdinal"] == 1, "phase failure identity")
    _parse_utc(value["startedAt"]); _parse_utc(value["failedAt"])
    require(type(value["elapsedSeconds"]) in (int, float)
            and not isinstance(value["elapsedSeconds"], bool)
            and math.isfinite(float(value["elapsedSeconds"])) and value["elapsedSeconds"] >= 0,
            "phase failure elapsed time")
    require(value["failureStage"] in FAILURE_STAGES and value["failureKind"] in FAILURE_KINDS,
            "phase failure enum drift")
    error = value["error"]
    require(isinstance(error, dict) and set(error) == {"type", "message", "traceback"}
            and all(isinstance(error[name], str) for name in error),
            "phase failure error shape")
    for name in ("producer", "profile"):
        record = value[name]
        require(isinstance(record, dict) and set(record) == {"path", "bytes", "sha256"},
                f"phase failure {name} pin shape")
        canonical_record_set_sha256([record])
    for name in ("delivery", "receipt"):
        record = value[name]
        require(isinstance(record, dict)
                and set(record) == {"available", "manifest", "review", "runtimeLock"}
                and type(record["available"]) is bool, f"phase failure {name} shape")
        for pin_name in ("manifest", "review", "runtimeLock"):
            child = record[pin_name]
            if child is not None:
                require(isinstance(child, dict) and set(child) == {"path", "bytes", "sha256"},
                        f"phase failure {name}.{pin_name} pin shape")
                canonical_record_set_sha256([child])
    require(isinstance(value["phaseInputLock"], dict)
            and isinstance(value["ancestorClosure"], dict)
            and isinstance(value["hostGate"], dict), "phase failure embedded evidence shape")
    authorization = value["authorizationEvidence"]
    require(isinstance(authorization, list), "phase failure authorization array")
    for item in authorization:
        require(isinstance(item, dict) and set(item) == {"name", "pin", "decision"}
                and isinstance(item["name"], str) and item["name"]
                and isinstance(item["pin"], dict)
                and set(item["pin"]) == {"path", "bytes", "sha256"}
                and isinstance(item["decision"], dict), "phase failure authorization record")
        canonical_record_set_sha256([item["pin"]])
    command = value["command"]
    require(isinstance(command, dict) and set(command) == {"cwd", "intendedArgv", "actualArgv",
            "environmentAllowList", "dockerArgv", "sparkSubmitArgv"}
            and Path(command["cwd"]).is_absolute()
            and all(isinstance(command[name], list)
                    and all(isinstance(part, str) for part in command[name])
                    for name in ("intendedArgv", "actualArgv", "environmentAllowList",
                                 "dockerArgv", "sparkSubmitArgv")), "phase failure command shape")
    logs = value["logs"]
    require(isinstance(logs, dict) and set(logs) == {"stdout", "stderr", "runnerLog",
            "stdoutSha256", "stderrSha256", "runnerLogSha256"}
            and all(isinstance(logs[name], str) for name in logs)
            and logs["stdoutSha256"] == sha256_bytes(logs["stdout"].encode())
            and logs["stderrSha256"] == sha256_bytes(logs["stderr"].encode())
            and logs["runnerLogSha256"] == sha256_bytes(logs["runnerLog"].encode()),
            "phase failure log closure")
    resource = value["resource"]
    require(isinstance(resource, dict) and set(resource) == {"status", "pollIntervalSeconds",
            "samples", "peakMemoryBytes", "lastMemoryBytes", "cgroupVersion", "events"}
            and resource["status"] in {"NOT_STARTED", "OBSERVED", "MEASUREMENT_FAILED"}
            and resource["cgroupVersion"] in {"v1", "v2", "NOT_STARTED"}
            and type(resource["pollIntervalSeconds"]) in (int, float)
            and resource["pollIntervalSeconds"] > 0 and isinstance(resource["samples"], list)
            and isinstance(resource["events"], dict), "phase failure resource shape")
    sample_keys = {"observedAt", "memoryCurrentBytes", "memoryPeakBytes", "cpuUsageUsec",
                   "oom", "oomKill"}
    for sample in resource["samples"]:
        require(isinstance(sample, dict) and set(sample) == sample_keys,
                "phase failure resource sample shape")
        _parse_utc(sample["observedAt"])
        require(all(type(sample[name]) is int and sample[name] >= 0
                    for name in ("memoryCurrentBytes", "memoryPeakBytes", "cpuUsageUsec"))
                and type(sample["oom"]) is bool and type(sample["oomKill"]) is bool,
                "phase failure resource sample values")
    container = value["container"]
    require(isinstance(container, dict) and set(container) == {"created", "containerId",
            "preStopInspect", "finalInspect", "exitCode", "oomKilled"}
            and type(container["created"]) is bool, "phase failure container shape")
    timeout = value["timeout"]
    require(isinstance(timeout, dict) and set(timeout) == {"limitSeconds", "timedOut",
            "lastSampleBeforeStop", "inspectBeforeStop", "stopRequestedAt"}
            and type(timeout["limitSeconds"]) is int and timeout["limitSeconds"] > 0
            and type(timeout["timedOut"]) is bool, "phase failure timeout shape")
    cleanup = value["cleanup"]
    require(isinstance(cleanup, dict) and set(cleanup) == {"monitorStopped", "containerStopped",
            "containerRemoved", "tempRemoved", "scratchRemoved", "errors", "complete"}
            and all(type(cleanup[name]) is bool for name in ("monitorStopped", "containerStopped",
                "containerRemoved", "tempRemoved", "scratchRemoved", "complete"))
            and isinstance(cleanup["errors"], list)
            and all(isinstance(item, str) for item in cleanup["errors"]),
            "phase failure cleanup shape")
    census = value["namespaceCensus"]
    census_keys = {"completed", "failures", "reviews", "claims", "temps", "scratches",
                   "containers", "processes"}
    require(isinstance(census, dict) and set(census) == {"beforePublication", "afterCleanup"}
            and all(isinstance(snapshot, dict) and set(snapshot) == census_keys
                    and all(isinstance(items, list) and items == sorted(set(items))
                            and all(isinstance(item, str) for item in items)
                            for items in snapshot.values()) for snapshot in census.values()),
            "phase failure namespace census")
    output_state = value["outputState"]
    require(isinstance(output_state, dict) and set(output_state) == {"finalPublished",
            "reviewPublished", "modelWritten", "predictionsWritten", "nextPhaseBlocked"}
            and all(type(item) is bool for item in output_state.values())
            and output_state["finalPublished"] is False
            and output_state["reviewPublished"] is False
            and output_state["nextPhaseBlocked"] is True, "phase failure output state")
    for name in ("dependencyFingerprintBefore", "dependencyFingerprintAfter"):
        require(isinstance(value[name], str) and re.fullmatch(r"[0-9a-f]{64}", value[name]),
                "phase failure dependency fingerprint")
    publication = value["publication"]
    require(isinstance(publication, dict)
            and publication.get("schemaVersion") == "feelm-service-v1-b1-r4-publication-evidence/1"
            and publication.get("role") == "PRODUCER", "phase failure publication evidence")
    require(value["readyForService"] is False and value["deploymentAuthorized"] is False,
            "phase failure authorization must remain false")


def write_failure(
    paths: ExecutionPaths,
    phase: str,
    error: BaseException,
    cleanup_ok: bool,
    material: Mapping[str, Any],
    transient_paths: Sequence[Path],
    *,
    lease: Any,
    dependencies: Any,
    control_state: Mapping[str, Any],
    host_gate: Mapping[str, Any] | None,
    started_at: str | None = None,
) -> dict[str, Any]:
    evidence = getattr(error, "evidence", None)
    completed_stages = list(material.get("completedStages", []))
    effective_container = dict(evidence) if isinstance(evidence, Mapping) else (
        dict(completed_stages[-1]) if completed_stages else {}
    )
    effective_resource = (
        dict(evidence.get("resource", {})) if isinstance(evidence, Mapping) else
        dict(effective_container.get("resource", {}))
    )
    phase_lock = material.get("phaseInputLock")
    recovery = material.get("recoveryReference")
    profile_document = material.get("executionProfile")
    command_document = material.get("commandDocument")
    run_log = material.get("runLog")
    require(isinstance(phase_lock, Mapping), "failure cannot close missing phase input lock")
    require(isinstance(recovery, Mapping), "failure cannot close missing recovery ancestry")
    require(isinstance(profile_document, Mapping), "failure cannot close missing execution profile")
    require(isinstance(command_document, Mapping), "failure cannot close missing command")
    require(isinstance(run_log, Mapping), "failure cannot close missing run log")
    final = paths.bundle(phase)
    review = paths.review(phase)
    failure = paths.failure(phase)
    require(not os.path.lexists(final), "success bundle and failure cannot coexist")
    require(not os.path.lexists(review), "sibling PASS review and failure cannot coexist")
    require(not os.path.lexists(failure), "immutable failure already exists")
    container_census_error: str | None = None
    try:
        containers = running_container_names(prefix="feelm-b1-")
    except BaseException as census_error:
        containers = []
        container_census_error = repr(census_error)
    cleanup_complete = (cleanup_ok and not any(os.path.lexists(path) for path in transient_paths)
                        and not containers and container_census_error is None)
    evidence_map = dict(evidence) if isinstance(evidence, Mapping) else {}
    timed_out = bool(evidence_map.get("timedOut", effective_container.get("timedOut", False)))
    docker_state = evidence_map.get("dockerState") or effective_container.get("dockerState")
    if not isinstance(docker_state, Mapping):
        docker_state = {}
    oom = docker_state.get("OOMKilled")
    evaluation_phase = phase in {"calibrate-select", "confirmation"}
    created = bool(evidence_map or effective_container) and not evaluation_phase
    stage_name, kind = _failure_classification(
        error, material, timed_out=timed_out, oom_killed=oom is True,
        created=created, cleanup_complete=cleanup_complete)
    resource = _failure_resource(evidence_map, effective_resource)
    samples = resource["samples"]
    output_text = str(evidence_map.get("stdout", effective_container.get("stdout", "")))
    stderr_text = str(evidence_map.get("stderr", ""))
    runner_text = str(run_log.get("content", ""))
    command_sequence = command_document.get("sequence", [])
    execution_started = stage_name in {"CONTAINER_CREATE", "WORKER", "TIMEOUT",
                                       "OUTPUT_VALIDATION", "PUBLICATION", "CLEANUP"}
    docker_argv: list[str] = []
    if isinstance(command_sequence, list) and command_sequence:
        candidate = command_sequence[-1].get("command") if isinstance(command_sequence[-1], Mapping) else []
        docker_argv = [str(item) for item in candidate] if isinstance(candidate, list) else []
    if evaluation_phase or not execution_started:
        docker_argv = []
    spark_index = docker_argv.index("/opt/spark/bin/spark-submit") if "/opt/spark/bin/spark-submit" in docker_argv else -1
    command = {"cwd": str(Path.cwd().absolute()), "intendedArgv": [str(value) for value in sys.argv],
               "actualArgv": [str(value) for value in sys.argv] if execution_started else [],
               "environmentAllowList": [name for name in ("HOME", "PATH", "PYTHONNOUSERSITE", "PIP_CONFIG_FILE",
                                                              "PYTHONDONTWRITEBYTECODE", "LC_ALL", "TZ") if name in os.environ],
               "dockerArgv": docker_argv,
               "sparkSubmitArgv": docker_argv[spark_index:] if spark_index >= 0 else []}
    server_records = {str(record["path"]): dict(record) for record in control_state.get("records", [])}
    def one(suffix: str) -> dict[str, Any] | None:
        matches = [record for path_text, record in server_records.items() if path_text.endswith(suffix)]
        require(len(matches) <= 1, f"ambiguous failure control: {suffix}")
        return matches[0] if matches else None
    delivery_manifest = one("-delivery-manifest.json")
    delivery_review = one("-delivery-manifest-result-review.json")
    receipt_manifest = one("server-receipt/manifest.json")
    receipt_review = one("-server-receipt-result-review.json")
    runtime_lock = one("service-v1-b1-r4-host-runtime-lock.json")
    delivery_payload = control_state.get("payloads", {}).get("deliveryManifest")
    delivery_runtime_lock = (delivery_payload.get("runtimeLock")
                             if isinstance(delivery_payload, Mapping) else None)
    if delivery_runtime_lock is not None:
        require(isinstance(delivery_runtime_lock, Mapping)
                and set(delivery_runtime_lock) == {"path", "bytes", "sha256"},
                "delivery runtime-lock pin shape drift")
        delivery_runtime_lock = dict(delivery_runtime_lock)
    authorization = _authorization_evidence(control_state)
    before_census = _namespace_census(paths)
    failed_at = utc_now()
    start = started_at or material.get("startedAt") or failed_at
    try:
        elapsed = max(0.0, (_parse_utc(failed_at) - _parse_utc(str(start))).total_seconds())
    except Exception:
        start = failed_at
        elapsed = 0.0
    fingerprint_before = dependencies.rehash()
    require(fingerprint_before == dependencies.fingerprint, "failure dependency drift before publication")
    payload: dict[str, Any] = {
        "schemaVersion": "feelm-service-v1-b1-r4-phase-failure/2", "status": "FAILED",
        "runId": RUN_ID, "profileId": PROFILE_ID, "phase": phase, "attemptOrdinal": 1,
        "startedAt": start, "failedAt": failed_at, "elapsedSeconds": elapsed,
        "failureStage": stage_name, "failureKind": kind,
        "error": {"type": type(error).__name__, "message": str(error),
                  "traceback": "".join(traceback.format_exception(type(error), error, error.__traceback__))},
        "producer": pin_file(paths.outer_runner, str(paths.outer_runner.absolute())),
        "profile": pin_file(paths.profile_contract, str(paths.profile_contract.absolute())),
        "delivery": {"available": delivery_manifest is not None and delivery_review is not None,
                     "manifest": delivery_manifest, "review": delivery_review,
                     "runtimeLock": delivery_runtime_lock},
        "receipt": {"available": receipt_manifest is not None and receipt_review is not None and runtime_lock is not None,
                    "manifest": receipt_manifest, "review": receipt_review, "runtimeLock": runtime_lock},
        "phaseInputLock": dict(phase_lock), "ancestorClosure": dict(recovery),
        "authorizationEvidence": authorization,
        "hostGate": dict(host_gate) if isinstance(host_gate, Mapping) else {"status": "BLOCK", "unknownFields": ["hostGate"]},
        "command": command,
        "logs": {"stdout": output_text, "stderr": stderr_text, "runnerLog": runner_text,
                 "stdoutSha256": sha256_bytes(output_text.encode()), "stderrSha256": sha256_bytes(stderr_text.encode()),
                 "runnerLogSha256": sha256_bytes(runner_text.encode())},
        "resource": resource,
        "container": {"created": created, "containerId": (str(evidence_map.get("containerId") or
            effective_container.get("containerName")) if created else None),
            "preStopInspect": evidence_map.get("preStopDockerState") if created else None,
            "finalInspect": dict(docker_state) if created else None,
            "exitCode": docker_state.get("ExitCode") if created else None,
            "oomKilled": bool(oom) if created else None},
        "timeout": {"limitSeconds": ({"preflight": 7_200, "fit": 28_800, "score": 7_200,
                                       "calibrate-select": 14_400,
                                       "confirmation": 14_400}[phase]),
                    "timedOut": timed_out, "lastSampleBeforeStop": samples[-1] if samples else None,
                    "inspectBeforeStop": evidence_map.get("preStopDockerState"),
                    "stopRequestedAt": evidence_map.get("stopRequestedAt")},
        "cleanup": {"monitorStopped": cleanup_complete, "containerStopped": cleanup_complete,
                    "containerRemoved": cleanup_complete, "tempRemoved": not os.path.lexists(_lease_path(lease, "temp_path")),
                    "scratchRemoved": all(not os.path.lexists(path) for path in transient_paths if "scratch" in path.name),
                    "errors": [] if cleanup_complete else ["cleanup incomplete"], "complete": cleanup_complete},
        "namespaceCensus": {"beforePublication": before_census, "afterCleanup": _namespace_census(paths)},
        "outputState": {"finalPublished": False, "reviewPublished": False,
                        "modelWritten": os.path.lexists(final / "model/native"),
                        "predictionsWritten": os.path.lexists(final / "score/predictions.parquet"),
                        "nextPhaseBlocked": True},
        "dependencyFingerprintBefore": fingerprint_before, "dependencyFingerprintAfter": fingerprint_before,
        "publication": publication_evidence(lease), "readyForService": False, "deploymentAuthorized": False,
    }
    validate_phase_failure_payload(payload, phase)
    api = _publication_api()
    published = api.publish_handled_failure(lease, payload, dependencies.fingerprint, dependencies.rehash)
    after = dependencies.rehash()
    require(after == dependencies.fingerprint, "failure dependency drift after publication")
    api.release_verified_claim(lease, published, after)
    if isinstance(published, Mapping):
        return dict(published)
    return dict(getattr(published, "record"))

def validate_model_inventory(model_root: Path, inventory: Mapping[str, Any]) -> None:
    require(inventory.get("schemaVersion") == "feelm-service-v1-b1-model-inventory/1", "model inventory schema drift")
    records = inventory.get("files")
    require(isinstance(records, list) and records, "model inventory files missing")
    actual = [pin_file(path, path.relative_to(model_root).as_posix()) for path in sorted(model_root.rglob("*")) if path.is_file()]
    require(records_equal(records, actual), "native model inventory file drift")
    require(inventory.get("inventorySha256") == canonical_record_set_sha256(actual), "model inventory digest drift")


def create_model_inventory(model_root: Path) -> dict[str, Any]:
    files = [pin_file(path, path.relative_to(model_root).as_posix()) for path in sorted(model_root.rglob("*")) if path.is_file()]
    require(bool(files), "native model directory is empty")
    return {
        "schemaVersion": "feelm-service-v1-b1-model-inventory/1",
        "files": files,
        "inventorySha256": canonical_record_set_sha256(files),
    }


def validate_partition_identity(path: Path) -> dict[str, Any]:
    value = load_json(path)
    require(value.get("schemaVersion") == "feelm-service-v1-b1-partition-identity/1", "partition schema drift")
    require(value.get("sourceRows") == SOURCE_ROWS, "partition source-row drift")
    require(value.get("logicalRows") == LOGICAL_ROWS, "partition logical-row drift")
    records = value.get("partitions")
    require(isinstance(records, list) and len(records) == PARTITIONS, "partition record count drift")
    require([record.get("partition") for record in records] == list(range(PARTITIONS)), "partition order drift")
    return value


def validate_score_predictions(score_input: Path, predictions_path: Path) -> dict[str, Any]:
    source = pq.read_table(score_input, columns=["row_id", "uid"])
    predictions = pq.read_table(predictions_path)
    expected_schema = pa.schema(
        [("row_id", pa.int64()), ("uid", pa.int32()), ("prediction", pa.float64())]
    )
    require(predictions.schema == expected_schema, "prediction Parquet schema drift")
    require(source.num_rows == SCORE_ROWS and predictions.num_rows == SCORE_ROWS, "score row count drift")
    source_row_id = source["row_id"].to_numpy(zero_copy_only=False)
    source_uid = source["uid"].to_numpy(zero_copy_only=False)
    output_row_id = predictions["row_id"].to_numpy(zero_copy_only=False)
    output_uid = predictions["uid"].to_numpy(zero_copy_only=False)
    expected_row_id = np.arange(SCORE_ROWS, dtype=np.int64)
    require(np.array_equal(source_row_id, expected_row_id), "natural score row_id is not exact 0..93229")
    require(np.array_equal(output_row_id, expected_row_id), "prediction row_id duplicate/missing/order drift")
    require(np.array_equal(output_uid, source_uid), "prediction uid axis drift")
    values = predictions["prediction"].to_numpy(zero_copy_only=False)
    require(np.isfinite(values).all(), "prediction contains non-finite values")
    return {
        "rows": SCORE_ROWS,
        "rowIdMinimum": int(output_row_id[0]),
        "rowIdMaximum": int(output_row_id[-1]),
        "rowIdsUnique": True,
        "uidAxisEqual": True,
        "predictionsFinite": True,
        "singlePhysicalParquetFile": True,
        "minimumPrediction": float(values.min()),
        "maximumPrediction": float(values.max()),
    }


def validate_analyzed_score_plan(path: Path) -> None:
    require(path.is_file(), "score analyzed plan missing")
    analyzed = path.read_text(encoding="utf-8")
    require("label" not in analyzed.casefold(), "analyzed score plan exposes label")
    missing = [f"x{index:03d}" for index in range(230) if f"x{index:03d}" not in analyzed]
    require(not missing, f"analyzed score plan omitted feature fields: {missing[:5]}")


def _training_readonly_paths(paths: ExecutionPaths) -> list[Path]:
    return [
        paths.outer_runner,
        paths.spark_worker,
        paths.plan,
        paths.profile_contract,
        paths.training_recipe,
        paths.artifact_contract,
        paths.models_contract,
        paths.feature_contract,
        paths.natural_train,
        paths.masked_root,
        paths.masked_review,
    ]


def verify_masked_bundle_directory(paths: ExecutionPaths) -> None:
    require(paths.masked_root.is_dir(), "masked bundle directory is missing")
    require(
        not paths.masked_root.is_symlink()
        and not (hasattr(paths.masked_root, "is_junction") and paths.masked_root.is_junction()),
        "masked bundle directory is a link or reparse point",
    )
    children = list(paths.masked_root.iterdir())
    require(
        {path.name for path in children}
        == {"manifest.json", "tmdb-masked-rh230.parquet", "views-manifest.json"},
        "masked bundle directory is not exactly the reviewed three files",
    )
    require(all(path.is_file() and not path.is_symlink() for path in children), "masked bundle contains a non-file")


def training_mounts(
    paths: ExecutionPaths,
    scratch: Path,
    output: Path | None,
    *,
    validate: bool = True,
) -> list[Mount]:
    verify_masked_bundle_directory(paths)
    mounts = [
        Mount(paths.outer_runner, "/app/run_service_v1_b1_gbt_r4.py"),
        Mount(paths.spark_worker, "/app/service_v1_b1_spark_worker.py"),
        Mount(paths.plan, "/contract/service-v1-b1-r4-server-fit-recovery.md"),
        Mount(paths.profile_contract, "/contract/execution-profile-contract.json"),
        Mount(paths.training_recipe, "/contract/training-recipe.v1.json"),
        Mount(paths.artifact_contract, "/contract/service-v1.json"),
        Mount(paths.models_contract, "/contract/MODELS.md"),
        Mount(paths.feature_contract, "/contract/feature-schema.v1.json"),
        Mount(paths.natural_train, "/input/natural-train.parquet"),
        # A directory bind hides any image-layer contents at /masked and gives
        # the worker the exact reviewed three-file bundle it inventories.
        Mount(paths.masked_root, "/masked"),
        Mount(paths.masked_review, "/review/masked-result-review.json"),
        Mount(scratch, "/scratch", readonly=False),
    ]
    if output is not None:
        mounts.append(Mount(output, "/output", readonly=False))
    if validate:
        validate_mounts(
            mounts,
            allowed_readonly=_training_readonly_paths(paths),
            allowed_write=[scratch, *([output] if output is not None else [])],
        )
    return mounts


def common_training_worker_arguments(action: str, include_recipe: bool, include_output: bool) -> list[str]:
    result = [
        action,
        "--natural",
        "/input/natural-train.parquet",
        "--masked",
        "/masked/tmdb-masked-rh230.parquet",
        "--masked-manifest",
        "/masked/manifest.json",
        "--views-manifest",
        "/masked/views-manifest.json",
        "--masked-review",
        "/review/masked-result-review.json",
        "--scratch",
        "/scratch",
    ]
    if include_recipe:
        result.extend(["--training-recipe", "/contract/training-recipe.v1.json"])
    if include_output:
        result.extend(["--output", "/output"])
    return result


def ensure_phase_clear(paths: ExecutionPaths, phase: str) -> None:
    final = paths.bundle(phase)
    require(not os.path.lexists(final), f"immutable {phase} bundle or link already exists")
    require(not os.path.lexists(paths.review(phase)), f"immutable {phase} review or link already exists")
    require(not os.path.lexists(paths.failure(phase)), f"existing {phase} failure or link requires investigation")
    prefixes = (
        f".{final.name}.tmp-",
        f".{paths.review(phase).name}.tmp-",
        f".{paths.failure(phase).name}.tmp-",
    )
    stale = [
        path
        for path in final.parent.iterdir()
        if path.name.startswith(prefixes) or (
            path.name.startswith(f".{final.name}.") and "-scratch-" in path.name
        )
    ] if final.parent.is_dir() else []
    require(not stale, f"stale {phase} staging or scratch paths block execution: {stale}")


def _stage_resource(phase: str, stage_results: Sequence[tuple[str, str, str, ContainerResult]]) -> dict[str, Any]:
    stages = []
    for worker_action, started_at, completed_at, result in stage_results:
        stages.append(
            {
                "workerAction": worker_action,
                "containerName": result.container_name,
                "workerStatus": result.worker.get("status"),
                "startedAt": started_at,
                "completedAt": completed_at,
                "elapsedSeconds": result.elapsed_seconds,
                "resource": result.resource,
                "cleanup": result.cleanup,
            }
        )
    require(stages and all(row["resource"]["resourceStatus"] == "PASS" for row in stages), "resource stage failed")
    return {
        "schemaVersion": "feelm-service-v1-b1-resource/1",
        "phase": phase,
        "status": "PASS",
        "limitBytes": MAX_MEMORY_BYTES,
        "stages": stages,
    }


def _failure_state(path: Path) -> dict[str, Any]:
    absolute = path.absolute()
    try:
        info = os.lstat(absolute)
    except FileNotFoundError:
        return {"path": str(absolute), "state": "MISSING"}
    if stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
        record = _absolute_record(absolute)
        return {**record, "state": "REGULAR", "stDev": info.st_dev,
                "stIno": info.st_ino, "mode": info.st_mode, "nlink": info.st_nlink}
    return {"path": str(absolute), "state": "UNSAFE", "stDev": info.st_dev,
            "stIno": info.st_ino, "mode": info.st_mode, "nlink": info.st_nlink}


@dataclass(frozen=True)
class FailureStateSnapshot:
    paths: tuple[Path, ...]
    states: tuple[dict[str, Any], ...]
    fingerprint: str

    def rehash(self) -> str:
        current = tuple(_failure_state(path) for path in self.paths)
        require(current == self.states, "failure-state dependency drift")
        return sha256_bytes(canonical_json_bytes(list(current)))


def failure_state_snapshot(candidates: Iterable[Path]) -> FailureStateSnapshot:
    by_name = {str(Path(path).absolute()): Path(path).absolute() for path in candidates}
    paths = tuple(by_name[name] for name in sorted(by_name))
    states = tuple(_failure_state(path) for path in paths)
    fingerprint = sha256_bytes(canonical_json_bytes(list(states)))
    snapshot = FailureStateSnapshot(paths, states, fingerprint)
    require(snapshot.rehash() == fingerprint, "failure-state acquisition drift")
    return snapshot


def _best_effort_control_state(
    paths: ExecutionPaths, phase: str, server_controls: ServerPhaseControls,
    extra_controls: Mapping[str, Path] | None = None,
) -> dict[str, Any]:
    canonical_phase = phase if phase in {"preflight", "fit", "score"} else "preflight"
    candidates = server_controls.for_phase(paths, canonical_phase)
    if extra_controls:
        candidates.update({name: Path(path) for name, path in extra_controls.items()})
    records: list[dict[str, Any]] = []
    payloads: dict[str, dict[str, Any]] = {}
    for name, path in candidates.items():
        try:
            records.append(_absolute_record(Path(path)))
            payload = load_json(Path(path), allow_null=True, require_lf=True)
            if isinstance(payload, Mapping):
                payloads[name] = dict(payload)
        except BaseException:
            continue
    return {"records": records, "payloads": payloads}


def _intended_phase_lock(
    paths: ExecutionPaths, phase: str, control_state: Mapping[str, Any]
) -> dict[str, Any]:
    model_records = [
        {"path": logical, "bytes": EXPECTED_FILE_PINS[logical][0],
         "sha256": EXPECTED_FILE_PINS[logical][1]}
        for logical in EXPECTED_FILE_PINS if logical != "source/natural-score.parquet"
    ]
    runtime_records = [
        {"path": "implementation/service_v1_b1_spark_worker.py",
         "bytes": paths.spark_worker.stat().st_size if paths.spark_worker.is_file() else 0,
         "sha256": EXPECTED_WORKER_SHA256},
        pin_file(paths.portable_reader, "implementation/combination340_models.py"),
        pin_file(paths.portable_dependency, "implementation/rec046_common.py"),
        virtual_pin("runtime/docker-image-id", IMAGE_ID),
    ]
    execution = [pin_file(paths.plan, "execution/service-v1-b1-r4-server-fit-recovery.md"),
                 pin_file(paths.profile_contract,
                          "execution/ec2-8vcpu32g-local5-fit20g-t28800-profile.json"),
                 pin_file(paths.outer_runner, "execution/run_service_v1_b1_gbt_r4.py"),
                 pin_file(paths.tests, "execution/test_service_v1_b1_gbt_runner_r4.py")]
    controls = [dict(record) for record in control_state.get("records", [])]
    if phase == "score":
        score_record = {"path": "source/natural-score.parquet",
                        "bytes": EXPECTED_FILE_PINS["source/natural-score.parquet"][0],
                        "sha256": EXPECTED_FILE_PINS["source/natural-score.parquet"][1]}
        return make_score_lock(model_records, [score_record], runtime_records, execution, controls)
    return make_phase_lock(phase, model_records, runtime_records, execution, controls)


def _failure_ancestor_closure(paths: ExecutionPaths) -> dict[str, Any]:
    try:
        ancestry = validate_r3_ancestry(paths)
        outer, worker, _ = implementation_pins(paths)
        _, profile_pin = execution_profile(paths)
        execution_digest = canonical_record_set_sha256(execution_records(paths))
        return recovery_reference(paths, ancestry, outer, worker, profile_pin, execution_digest)
    except BaseException as closure_error:
        candidates = [paths.r2_plan, paths.r2_preflight_review, paths.r2_fit_failure,
                      paths.r3_plan, paths.r3_preflight_review, paths.r3_fit_failure,
                      paths.r3_fit_failure_review, paths.plan, paths.profile_contract]
        return {"schemaVersion": "feelm-service-v1-b1-r4-failure-ancestor-closure/1",
                "records": [_failure_state(path) for path in candidates],
                "semanticFacts": {"validationErrorType": type(closure_error).__name__,
                                  "validationError": str(closure_error)}}


def _pre_execution_failure_stage(error: BaseException) -> tuple[str, str]:
    message = str(error).casefold()
    if any(word in message for word in ("host gate", "maintenance", "reservation", "memavailable",
                                        "memtotal", "filesystem", "cgroup", "systemd", "docker")):
        return "HOST_GATE", "RESOURCE"
    if any(word in message for word in ("input", "sha", "digest", "pin", "ancestry", "contract")):
        return "INPUT_LOCK", "CONTRACT"
    return "PRE_CONTAINER", "CONTRACT"


def _failure_dependency_paths(
    paths: ExecutionPaths, phase: str, controls: Iterable[Path]
) -> list[Path]:
    """Freeze every physical file the self-contained failure composer may read."""
    candidates = [
        paths.outer_runner, paths.spark_worker, paths.spark_auditor, paths.tests,
        paths.portable_reader, paths.portable_dependency, paths.plan, paths.profile_contract,
        paths.r2_plan, paths.r3_plan, paths.r3_profile, paths.r2_outer_runner,
        paths.r3_outer_runner, paths.r2_auditor, paths.r3_auditor,
        paths.r2_preflight_review, paths.r2_fit_failure, paths.r3_preflight_review,
        paths.r3_fit_failure, paths.r3_fit_failure_review,
        Path(__file__).resolve().with_name("service_v1_b1_r4_publication.py"),
        *[Path(value) for value in controls],
    ]
    for logical in R3_IMPLEMENTATION_PINS:
        candidates.append(paths.standalone / logical)
    prior = {"preflight": (), "fit": ("preflight",), "score": ("preflight", "fit"),
             "calibrate-select": ("preflight", "fit", "score"),
             "confirmation": ("preflight", "fit", "score", "selection")}
    require(phase in prior, "unsupported failure dependency phase")
    for earlier in prior[phase]:
        bundle = paths.bundle(earlier)
        candidates.append(bundle)
        if bundle.is_dir() and not bundle.is_symlink():
            candidates.extend(item for item in bundle.rglob("*") if item.is_file())
        candidates.append(paths.review(earlier))
    unique = {str(path.absolute()): path.absolute() for path in candidates}
    return [unique[name] for name in sorted(unique)]


def publish_pre_execution_failure(
    paths: ExecutionPaths, phase: str, error: BaseException, *,
    server_controls: ServerPhaseControls, extra_controls: Mapping[str, Path] | None = None,
    host_gate: Mapping[str, Any] | None = None, started_at: str,
) -> Path:
    require(phase in {"preflight", "fit", "score"}, "unsupported pre-execution failure phase")
    expected = expected_namespace_before(phase)
    require(namespace_children(paths) == sorted(expected),
            "pre-execution failure requires a fresh exact namespace")
    controls = server_controls.for_phase(paths, phase)
    if extra_controls:
        controls.update(extra_controls)
    dependency_paths = _failure_dependency_paths(paths, phase, controls.values())
    dependencies = failure_state_snapshot(dependency_paths)
    lease, stage = acquire_phase_publication(paths, phase, dependencies)
    control_state = _best_effort_control_state(paths, phase, server_controls, extra_controls)
    phase_lock = _intended_phase_lock(paths, phase, control_state)
    stage_name, kind = _pre_execution_failure_stage(error)
    profile_payload = load_json(paths.profile_contract, allow_null=False, require_lf=True)
    profile_pin = pin_file(paths.profile_contract,
                           "execution/ec2-8vcpu32g-local5-fit20g-t28800-profile.json")
    outer = pin_file(paths.outer_runner, "execution/run_service_v1_b1_gbt_r4.py")
    profile_document = phase_execution_profile(
        phase, profile_payload, profile_pin, outer,
        canonical_record_set_sha256(execution_records(paths)))
    material = {"phase": phase, "phaseInputLock": phase_lock,
                "recoveryReference": _failure_ancestor_closure(paths),
                "executionProfile": profile_document,
                "commandDocument": command_document(phase, IMAGE_ID, []),
                "runLog": {"bytes": 0, "sha256": sha256_bytes(b""), "content": ""},
                "completedStages": [], "failureStage": stage_name, "failureKind": kind}
    write_failure(paths, phase, error, True, material, [stage], lease=lease,
                  dependencies=dependencies, control_state=control_state,
                  host_gate=host_gate, started_at=started_at)
    return paths.failure(phase)


def _preflight_impl(
    paths: ExecutionPaths,
    *,
    server_controls: ServerPhaseControls,
    expected_control_sha256: Mapping[str, str],
    dry_host_gate: Mapping[str, Any] | None = None,
    full_host_gate_provider: Any | None = None,
) -> Path:
    phase = "preflight"
    ensure_phase_clear(paths, phase)
    require(not running_container_names(), "another B1 Spark container exists")
    control_state = verify_server_control_chain(
        paths, phase=phase, controls=server_controls.for_phase(paths, phase),
        expected_sha256=expected_control_sha256)
    scratch_probe_root = Path(control_state["payloads"]["serverReceiptManifest"]["actualRoots"]["scratchRoot"])
    observed_dry_gate = (dict(dry_host_gate) if dry_host_gate is not None else
                         collect_dynamic_host_gate(paths, phase="preflight-dry", control_state=control_state,
                                                   scratch_root=scratch_probe_root))
    verify_dynamic_host_gate(observed_dry_gate, phase="preflight-dry",
                             current_namespace=expected_namespace_before(phase),
                             paths=paths, control_state=control_state)
    verify_team_commit(paths)
    image_id = inspect_image_id()
    outer, worker, implementation = implementation_pins(paths)
    profile_payload, profile_pin = execution_profile(paths)
    models = model_input_records(paths)
    runtime = worker_runtime_records(paths, image_id)
    executions = execution_records(paths)
    execution_digest = canonical_record_set_sha256(executions)
    ancestry = validate_r3_ancestry(paths)
    controls = merge_records(r3_control_records(ancestry), control_state["records"])
    phase_lock = make_phase_lock(phase, models, runtime, executions, controls)
    recovery = recovery_reference(paths, ancestry, outer, worker, profile_pin, execution_digest)
    profile_document = phase_execution_profile(phase, profile_payload, profile_pin, outer, execution_digest)

    dependencies = dependency_snapshot(paths, [*models, *runtime, *executions, *controls])
    lease, stage = acquire_phase_publication(paths, phase, dependencies)
    final = paths.bundle(phase)
    token = str(getattr(lease, "token", uuid.uuid4().hex))
    dry_scratch = final.parent / f".{final.name}.dry-scratch-{token}"
    full_scratch = final.parent / f".{final.name}.full-scratch-{token}"
    log_path = stage / "run.log"
    stage_results: list[tuple[str, str, str, ContainerResult]] = []
    cleanup_ok = False
    stage_owned = False
    dry_scratch_owned = False
    full_scratch_owned = False
    owned_stage: OwnedPath | None = None
    owned_dry_scratch: OwnedPath | None = None
    owned_full_scratch: OwnedPath | None = None
    dry_name = f"feelm-b1-r4-preflight-dry-{token[:12]}"
    full_name = f"feelm-b1-r4-preflight-full-{token[:12]}"
    dry_mounts = training_mounts(paths, dry_scratch, None, validate=False)
    full_mounts = training_mounts(paths, full_scratch, stage, validate=False)
    dry_args = common_training_worker_arguments("dry-run-first-row-group", include_recipe=False, include_output=False)
    full_args = common_training_worker_arguments("preflight", include_recipe=True, include_output=True)
    dry_command = spark_container_command(dry_name, dry_mounts, dry_args)
    full_command = spark_container_command(full_name, full_mounts, full_args)
    command = command_document(
        phase,
        image_id,
        [
            {"order": 0, "workerAction": "dry-run-first-row-group", "timeoutSeconds": PHASE_TIMEOUT_SECONDS["preflight-dry"], "command": dry_command},
            {"order": 1, "workerAction": "preflight", "timeoutSeconds": PHASE_TIMEOUT_SECONDS["preflight-full"], "command": full_command},
        ],
    )
    command["dryRunPrecedesFullMaterialization"] = True
    failure_context: dict[str, Any] = {
        "phase": phase,
        "phaseInputLock": phase_lock,
        "recoveryReference": recovery,
        "executionProfile": profile_document,
        "commandDocument": command,
        "implementation": {"outerRunner": outer, "sparkWorker": worker, "implementationSetSha256": implementation},
        "failureStage": "INPUT_LOCK", "failureKind": "CONTRACT",
    }
    phase_started_at = utc_now()
    latest_host_gate = observed_dry_gate
    try:
        make_stage(final, paths.review(phase), stage)
        stage_owned = True
        owned_stage = capture_owned_path(stage)
        dry_scratch.mkdir()
        dry_scratch_owned = True
        owned_dry_scratch = capture_owned_path(dry_scratch)
        full_scratch.mkdir()
        full_scratch_owned = True
        owned_full_scratch = capture_owned_path(full_scratch)
        validate_mounts(
            dry_mounts,
            allowed_readonly=_training_readonly_paths(paths),
            allowed_write=[dry_scratch],
        )
        validate_mounts(
            full_mounts,
            allowed_readonly=_training_readonly_paths(paths),
            allowed_write=[full_scratch, stage],
        )
        write_json_exclusive(stage / "input-lock.json", phase_lock)
        write_json_exclusive(stage / "recovery-reference.json", recovery)
        write_json_exclusive(stage / "execution-profile.json", profile_document)
        write_json_exclusive(stage / "command.json", command)
        delivery_reference, receipt_reference = server_reference_documents(control_state)
        write_json_exclusive(stage / "delivery-reference.json", delivery_reference)
        write_json_exclusive(stage / "server-receipt-reference.json", receipt_reference)
        write_json_exclusive(stage / "host-gate-dry.json", observed_dry_gate)

        failure_context["failureStage"] = "CONTAINER_CREATE"
        failure_context["failureKind"] = "PROCESS"
        dry_started = utc_now()
        dry_result = run_container(
            dry_name,
            dry_command,
            log_path,
            expected_worker_status="DRY_RUN_ONLY_NOT_PUBLISHED",
            timeout_seconds=PHASE_TIMEOUT_SECONDS["preflight-dry"],
        )
        dry_completed = utc_now()
        stage_results.append(("dry-run-first-row-group", dry_started, dry_completed, dry_result))
        cleanup_owned_path(owned_dry_scratch)
        dry_scratch_owned = False
        require(not os.path.lexists(stage / "partition-identity.json"), "dry-run published preflight identity")

        observed_full_gate = (
            dict(full_host_gate_provider()) if callable(full_host_gate_provider) else
            dict(full_host_gate_provider) if isinstance(full_host_gate_provider, Mapping) else
            collect_dynamic_host_gate(paths, phase="preflight-full", control_state=control_state,
                                      scratch_root=scratch_probe_root)
        )
        verify_dynamic_host_gate(observed_full_gate, phase="preflight-full",
                                 current_namespace=expected_namespace_before(phase),
                                 paths=paths, control_state=control_state)
        latest_host_gate = observed_full_gate
        write_json_exclusive(stage / "host-gate-full.json", observed_full_gate)

        failure_context["failureStage"] = "CONTAINER_CREATE"
        full_started = utc_now()
        full_result = run_container(
            full_name,
            full_command,
            log_path,
            expected_worker_status="B1_FULL_PREFLIGHT_WORKER_COMPLETE",
            timeout_seconds=PHASE_TIMEOUT_SECONDS["preflight-full"],
        )
        full_completed = utc_now()
        stage_results.append(("preflight", full_started, full_completed, full_result))
        failure_context["failureStage"] = "OUTPUT_VALIDATION"
        failure_context["failureKind"] = "AUDIT"
        cleanup_owned_path(owned_full_scratch)
        full_scratch_owned = False
        identity = validate_partition_identity(stage / "partition-identity.json")
        dry_runtime = validate_runtime_versions(dry_result.worker)
        full_runtime = validate_runtime_versions(full_result.worker)
        require(dry_runtime == full_runtime, "dry-run/full-preflight runtime drift")
        require(full_result.worker.get("modelFitPerformed") is False, "preflight worker fitted a model")
        require(full_result.worker.get("scorePerformed") is False, "preflight worker scored data")

        resource = _stage_resource(phase, stage_results)
        write_json_exclusive(stage / "resource.json", resource)
        current_ancestry = validate_r3_ancestry(paths)
        current_models = model_input_records(paths)
        current_runtime = worker_runtime_records(paths, inspect_image_id())
        current_executions = execution_records(paths)
        current_control_state = verify_server_control_chain(
            paths, phase=phase, controls=server_controls.for_phase(paths, phase),
            expected_sha256=expected_control_sha256)
        current_controls = merge_records(r3_control_records(current_ancestry), current_control_state["records"])
        require(records_equal(models, current_models), "model input mutated during preflight")
        require(records_equal(runtime, current_runtime), "worker runtime mutated during preflight")
        require(records_equal(executions, current_executions), "execution input mutated during preflight")
        require(records_equal(controls, current_controls), "r2 ancestry mutated during preflight")
        current_lock = make_phase_lock(phase, current_models, current_runtime, current_executions, current_controls)
        require(current_lock == phase_lock, "preflight input lock mutated during execution")
        write_manifest_last(
            stage,
            {
                "schemaVersion": "feelm-service-v1-b1-preflight-manifest/2",
                "runId": RUN_ID,
                "profileId": PROFILE_ID,
                "status": "B1_FULL_PREFLIGHT_COMPLETE_AWAITING_REVIEW",
                "createdAt": utc_now(),
                "fitAuthorized": False,
                "modelFitPerformed": False,
                "scorePerformed": False,
                "readyForService": False,
                "sourceRows": SOURCE_ROWS,
                "logicalRows": LOGICAL_ROWS,
                "partitionCount": PARTITIONS,
                "outerRunnerSha256": outer["sha256"],
                "sparkWorkerSha256": worker["sha256"],
                "implementationSetSha256": implementation,
                "modelInputSetSha256": phase_lock["modelInputSetSha256"],
                "workerRuntimeSetSha256": phase_lock["workerRuntimeSetSha256"],
                "executionSetSha256": phase_lock["executionSetSha256"],
                "controlReferenceSetSha256": phase_lock["controlReferenceSetSha256"],
                "inputSetSha256": phase_lock["inputSetSha256"],
                "r2TrainingSourceSetSha256": R2_TRAINING_SOURCE_SET_SHA256,
                "r2PreflightManifestSha256": R2_PREFLIGHT_MANIFEST_SHA256,
                "r2PreflightReviewSha256": R2_PREFLIGHT_REVIEW_SHA256,
                "r2FitFailureSha256": R2_FIT_FAILURE_SHA256,
                "r3FitFailureSha256": R3_FIT_FAILURE_SHA256,
                "recoveryReferenceSha256": pin_file(stage / "recovery-reference.json")["sha256"],
                "executionProfileSha256": pin_file(stage / "execution-profile.json")["sha256"],
                "dryRunPrecedesFullMaterialization": True,
                "dryRunLogicalRows": dry_result.worker.get("logicalRows"),
                "partitionIdentitySha256": pin_file(stage / "partition-identity.json")["sha256"],
                "identity": {"sourceRows": identity["sourceRows"], "logicalRows": identity["logicalRows"]},
                "resourceStatus": resource["status"],
                "runtimeVersions": full_runtime,
                "publication": publication_evidence(lease),
            },
        )
        failure_context["failureStage"] = "PUBLICATION"
        failure_context["failureKind"] = "IO"
        publish_phase_success(lease, stage, dependencies.fingerprint, dependencies.rehash)
        cleanup_ok = True
        return final
    except BaseException as error:
        failure_material = capture_failure_material(stage, failure_context, error, stage_owned=stage_owned)
        failure_material["completedStages"] = []
        for action, started, completed, result in stage_results:
            record = completed_container_evidence(result, action)
            record.update({"startedAt": started, "completedAt": completed})
            failure_material["completedStages"].append(record)
        cleanup_errors: list[str] = []
        owned_paths = [owned for owned, present in (
            (owned_dry_scratch, dry_scratch_owned),
            (owned_full_scratch, full_scratch_owned),
            (owned_stage, stage_owned)) if present and owned is not None]
        for owned in owned_paths:
            try:
                cleanup_owned_path(owned)
            except Exception as cleanup_error:
                cleanup_errors.append(repr(cleanup_error))
        cleanup_ok = not cleanup_errors and not isinstance(error, ContainerCleanupError)
        if not cleanup_ok:
            failure_material["failureStage"] = "CLEANUP"
            failure_material["failureKind"] = "CLEANUP"
        try:
            write_failure(paths, phase, error, cleanup_ok, failure_material,
                          [dry_scratch, full_scratch, stage], lease=lease, dependencies=dependencies,
                          control_state=control_state, host_gate=latest_host_gate, started_at=phase_started_at)
        except Exception:
            if cleanup_errors:
                raise RuntimeError(f"preflight failed and cleanup was incomplete: {cleanup_errors}") from error
            raise
        raise

def _preflight_control_records(paths: ExecutionPaths) -> list[dict[str, Any]]:
    records = bundle_records(paths, paths.bundle("preflight"))
    records.append(pinned_control(paths, paths.review("preflight")))
    records.extend(r3_control_records(validate_r3_ancestry(paths)))
    return records


def _fit_control_records(paths: ExecutionPaths) -> list[dict[str, Any]]:
    records = bundle_records(paths, paths.bundle("fit"))
    records.append(pinned_control(paths, paths.review("fit")))
    records.extend(_preflight_control_records(paths))
    return records


def validate_expected_implementation(
    paths: ExecutionPaths, expected_outer: str, expected_worker: str
) -> tuple[dict[str, Any], dict[str, Any], str]:
    require(len(expected_outer) == 64 and len(expected_worker) == 64, "implementation SHA must be explicit SHA-256")
    outer, worker, implementation = implementation_pins(paths)
    require(outer["sha256"] == expected_outer.lower(), "expected outer-runner SHA does not match current bytes")
    require(worker["sha256"] == expected_worker.lower(), "expected Spark-worker SHA does not match current bytes")
    return outer, worker, implementation


def _preflight_reference(
    audited: Mapping[str, Any],
    controls: Sequence[Mapping[str, Any]],
    outer: Mapping[str, Any],
    worker: Mapping[str, Any],
    profile_pin: Mapping[str, Any],
    phase_lock: Mapping[str, Any],
    recovery: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schemaVersion": "feelm-service-v1-b1-preflight-reference/2",
        "runId": RUN_ID,
        "profileId": PROFILE_ID,
        "preflightManifest": dict(audited["manifestPin"]),
        "preflightReview": dict(audited["reviewPin"]),
        "reviewedArtifacts": [dict(record) for record in controls],
        "recoveryReference": embedded_payload(recovery),
        "outerRunner": dict(outer),
        "sparkWorker": dict(worker),
        "executionProfile": dict(profile_pin),
        "modelInputSetSha256": phase_lock["modelInputSetSha256"],
        "workerRuntimeSetSha256": phase_lock["workerRuntimeSetSha256"],
        "executionSetSha256": phase_lock["executionSetSha256"],
        "controlReferenceSetSha256": phase_lock["controlReferenceSetSha256"],
        "fitInputSetSha256": phase_lock["inputSetSha256"],
    }

def _validate_threshold_fixtures(path: Path) -> tuple[np.ndarray, np.ndarray]:
    with np.load(path, allow_pickle=False) as fixture:
        names = set(fixture.files)
        require(
            names == {"features", "predictions", "indices", "split_features", "split_thresholds"},
            "threshold fixture field set drift",
        )
        features = np.asarray(fixture["features"], dtype=np.float64)
        predictions = np.asarray(fixture["predictions"], dtype=np.float64)
        indices = np.asarray(fixture["indices"], dtype=np.int32)
        split_features = np.asarray(fixture["split_features"], dtype=np.int32)
        split_thresholds = np.asarray(fixture["split_thresholds"], dtype=np.float64)
    require(features.ndim == 2 and features.shape[1] == 230, "threshold fixture feature shape drift")
    require(predictions.shape == (len(features),), "threshold prediction shape drift")
    require(np.array_equal(indices, np.arange(230, dtype=np.int32)), "threshold feature identity drift")
    require(len(features) > 0 and len(features) % 3 == 0, "threshold fixtures are not split triplets")
    require(split_features.shape == (len(features),), "split-feature shape drift")
    require(split_thresholds.shape == (len(features),), "split-threshold shape drift")
    require(np.isfinite(features).all() and np.isfinite(predictions).all(), "non-finite threshold fixture")
    require(np.isfinite(split_thresholds).all(), "non-finite split threshold")
    for start in range(0, len(features), 3):
        feature = int(split_features[start])
        threshold = float(split_thresholds[start])
        require(0 <= feature < 230, "threshold fixture feature index drift")
        require(np.all(split_features[start : start + 3] == feature), "split feature triplet drift")
        require(np.all(split_thresholds[start : start + 3] == threshold), "split threshold triplet drift")
        expected = np.asarray(
            [np.nextafter(threshold, -np.inf), threshold, np.nextafter(threshold, np.inf)], dtype=np.float64
        )
        require(np.array_equal(features[start : start + 3, feature], expected), "threshold boundary triplet drift")
    return features, predictions


def _portable_parity(model_root: Path, fixture_path: Path) -> dict[str, Any]:
    scripts = str(Path(__file__).resolve().parent)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from combination340_models import Trees

    features, native_predictions = _validate_threshold_fixtures(fixture_path)
    portable_predictions = np.asarray(Trees(model_root, range(230)).predict(features), dtype=np.float64)
    require(portable_predictions.shape == native_predictions.shape, "portable prediction shape drift")
    maximum_error = float(np.max(np.abs(portable_predictions - native_predictions)))
    require(np.isfinite(maximum_error) and maximum_error <= 1e-6, "portable threshold parity failed")
    return {
        "status": "PASS",
        "maximumAbsoluteError": maximum_error,
        "tolerance": 1e-6,
        "fixtureRows": len(features),
        "nonLeafSplits": len(features) // 3,
    }


def _fit_impl(
    paths: ExecutionPaths,
    *,
    server_controls: ServerPhaseControls,
    expected_control_sha256: Mapping[str, str],
    host_gate: Mapping[str, Any] | None = None,
    preflight_manifest: Path,
    preflight_review: Path,
    expected_preflight_manifest_sha256: str,
    expected_preflight_review_sha256: str,
    expected_outer_runner_sha256: str,
    expected_spark_worker_sha256: str,
    expected_model_input_set_sha256: str,
    expected_worker_runtime_set_sha256: str,
    expected_execution_set_sha256: str,
) -> Path:
    phase = "fit"
    ensure_phase_clear(paths, phase)
    require(not running_container_names(), "another B1 Spark container exists")
    require(preflight_manifest.resolve() == (paths.bundle("preflight") / "manifest.json").resolve(), "noncanonical preflight manifest")
    require(preflight_review.resolve() == paths.review("preflight").resolve(), "noncanonical preflight review")
    require(re.fullmatch(r"[0-9a-f]{64}", expected_preflight_manifest_sha256) is not None
            and re.fullmatch(r"[0-9a-f]{64}", expected_preflight_review_sha256) is not None,
            "preflight expected SHA must be lowercase")
    merged_expected = dict(expected_control_sha256)
    merged_expected.update({"preflightManifest": expected_preflight_manifest_sha256,
                            "preflightReview": expected_preflight_review_sha256})
    control_state = verify_server_control_chain(
        paths, phase=phase, controls=server_controls.for_phase(paths, phase), expected_sha256=merged_expected)
    scratch_probe_root = Path(control_state["payloads"]["serverReceiptManifest"]["actualRoots"]["scratchRoot"])
    observed_host_gate = (dict(host_gate) if host_gate is not None else
                          collect_dynamic_host_gate(paths, phase=phase, control_state=control_state,
                                                    scratch_root=scratch_probe_root))
    verify_dynamic_host_gate(observed_host_gate, phase=phase,
                             current_namespace=expected_namespace_before(phase),
                             paths=paths, control_state=control_state)
    require(pin_file(preflight_manifest)["sha256"] == expected_preflight_manifest_sha256,
            "expected preflight manifest SHA drift")
    require(pin_file(preflight_review)["sha256"] == expected_preflight_review_sha256,
            "expected preflight review SHA drift")
    outer, worker, implementation = validate_expected_implementation(paths, expected_outer_runner_sha256, expected_spark_worker_sha256)
    profile_payload, profile_pin = execution_profile(paths)
    ancestry = validate_r3_ancestry(paths)
    models = model_input_records(paths)
    runtime = worker_runtime_records(paths, inspect_image_id())
    executions = execution_records(paths)
    execution_digest = canonical_record_set_sha256(executions)
    require(canonical_record_set_sha256(models) == expected_model_input_set_sha256.lower(), "expected model-input digest drift")
    require(canonical_record_set_sha256(runtime) == expected_worker_runtime_set_sha256.lower(), "expected worker-runtime digest drift")
    require(execution_digest == expected_execution_set_sha256.lower(), "expected execution digest drift")
    audited = validate_preflight_review(paths, preflight_review)
    validate_preflight_recovery(paths, audited, ancestry, outer, worker, profile_pin, execution_digest)
    audited_lock = audited["inputLock"]
    require(audited_lock.get("modelInputSetSha256") == MODEL_INPUT_SET_SHA256, "preflight model-input digest drift")
    require(audited_lock.get("workerRuntimeSetSha256") == WORKER_RUNTIME_SET_SHA256, "preflight worker-runtime digest drift")
    require(audited_lock.get("executionSetSha256") == execution_digest, "preflight execution digest drift")
    verify_team_commit(paths)
    image_id = inspect_image_id()
    runtime = worker_runtime_records(paths, image_id)
    controls = merge_records(_preflight_control_records(paths), control_state["records"])
    phase_lock = make_phase_lock(phase, models, runtime, executions, controls)
    recovery = recovery_reference(paths, ancestry, outer, worker, profile_pin, execution_digest)
    reference = _preflight_reference(audited, controls, outer, worker, profile_pin, phase_lock, recovery)
    profile_document = phase_execution_profile(phase, profile_payload, profile_pin, outer, execution_digest)

    dependencies = dependency_snapshot(paths, [*models, *runtime, *executions, *controls])
    lease, stage = acquire_phase_publication(paths, phase, dependencies)
    final = paths.bundle(phase)
    token = str(getattr(lease, "token", uuid.uuid4().hex))
    scratch = final.parent / f".{final.name}.fit-scratch-{token}"
    log_path = stage / "run.log"
    cleanup_ok = False
    stage_owned = False
    scratch_owned = False
    owned_stage: OwnedPath | None = None
    owned_scratch: OwnedPath | None = None
    control_paths = [resolve_logical_path(paths, record["path"]) for record in controls]
    mounts = training_mounts(paths, scratch, stage, validate=False)
    for index, path in enumerate(control_paths):
        mounts.append(Mount(path, f"/control/fit-input/{index:02d}-{path.name}"))
    name = f"feelm-b1-r4-fit-{token[:12]}"
    worker_args = common_training_worker_arguments("fit", include_recipe=True, include_output=True)
    docker_command = spark_container_command(name, mounts, worker_args)
    command = command_document(
        phase,
        image_id,
        [{"order": 0, "workerAction": "fit", "timeoutSeconds": PHASE_TIMEOUT_SECONDS["fit"], "command": docker_command}],
    )
    failure_context: dict[str, Any] = {
        "phase": phase,
        "phaseInputLock": phase_lock,
        "recoveryReference": recovery,
        "phaseReference": reference,
        "executionProfile": profile_document,
        "commandDocument": command,
        "implementation": {"outerRunner": outer, "sparkWorker": worker, "implementationSetSha256": implementation},
        "failureStage": "INPUT_LOCK", "failureKind": "CONTRACT",
        "completedStages": [],
    }
    phase_started_at = utc_now()
    try:
        make_stage(final, paths.review(phase), stage)
        stage_owned = True
        owned_stage = capture_owned_path(stage)
        scratch.mkdir()
        scratch_owned = True
        owned_scratch = capture_owned_path(scratch)
        validate_mounts(
            mounts,
            allowed_readonly=[*_training_readonly_paths(paths), *control_paths],
            allowed_write=[scratch, stage],
        )
        write_json_exclusive(stage / "input-lock.json", phase_lock)
        write_json_exclusive(stage / "recovery-reference.json", recovery)
        write_json_exclusive(stage / "preflight-reference.json", reference)
        write_json_exclusive(stage / "execution-profile.json", profile_document)
        write_json_exclusive(stage / "command.json", command)
        delivery_reference, receipt_reference = server_reference_documents(control_state)
        write_json_exclusive(stage / "delivery-reference.json", delivery_reference)
        write_json_exclusive(stage / "server-receipt-reference.json", receipt_reference)
        write_json_exclusive(stage / "host-gate.json", observed_host_gate)
        failure_context["failureStage"] = "CONTAINER_CREATE"
        failure_context["failureKind"] = "PROCESS"
        started_at = utc_now()
        result = run_container(
            name,
            docker_command,
            log_path,
            expected_worker_status="B1_MODEL_FIT_WORKER_COMPLETE",
            timeout_seconds=PHASE_TIMEOUT_SECONDS["fit"],
        )
        completed_at = utc_now()
        completed = completed_container_evidence(result, "fit")
        completed.update({"startedAt": started_at, "completedAt": completed_at})
        failure_context["completedStages"] = [completed]
        failure_context["failureStage"] = "OUTPUT_VALIDATION"
        failure_context["failureKind"] = "AUDIT"
        cleanup_owned_path(owned_scratch)
        scratch_owned = False
        identity = validate_partition_identity(stage / "partition-identity.json")
        runtime_versions = validate_runtime_versions(result.worker)
        require(identity == load_json(paths.bundle("preflight") / "partition-identity.json"), "fit identity differs from audited preflight identity")
        resolved = load_json(stage / "resolved-estimator.json")
        recipe = load_json(paths.training_recipe)
        require(resolved == recipe.get("models", {}).get("GBT", {}).get("parameters"), "resolved estimator drift")
        metrics_path = stage / "fit-metrics.json"
        metrics = load_json(metrics_path)
        require(metrics.get("treeCount") == 120, "fit tree count drift")
        parity = _portable_parity(stage / "model" / "native", stage / "threshold-fixtures.npz")
        metrics["portableParity"] = parity
        replace_json_in_stage(metrics_path, metrics)
        inventory = create_model_inventory(stage / "model" / "native")
        write_json_exclusive(stage / "model-file-inventory.json", inventory)
        validate_model_inventory(stage / "model" / "native", inventory)
        resource = _stage_resource(phase, [("fit", started_at, completed_at, result)])
        write_json_exclusive(stage / "resource.json", resource)

        current_ancestry = validate_r3_ancestry(paths)
        current_models = model_input_records(paths)
        current_runtime = worker_runtime_records(paths, inspect_image_id())
        current_executions = execution_records(paths)
        current_control_state = verify_server_control_chain(
            paths, phase=phase, controls=server_controls.for_phase(paths, phase), expected_sha256=merged_expected)
        current_controls = merge_records(_preflight_control_records(paths), current_control_state["records"])
        require(records_equal(models, current_models), "model input mutated during fit")
        require(records_equal(runtime, current_runtime), "worker runtime mutated during fit")
        require(records_equal(executions, current_executions), "execution input mutated during fit")
        require(records_equal(controls, current_controls), "preflight or r2 ancestry mutated during fit")
        require(recovery == recovery_reference(paths, current_ancestry, outer, worker, profile_pin, execution_digest), "recovery ancestry mutated during fit")
        require(make_phase_lock(phase, current_models, current_runtime, current_executions, current_controls) == phase_lock, "fit input lock mutated during execution")
        write_manifest_last(
            stage,
            {
                "schemaVersion": "feelm-service-v1-b1-fit-manifest/2",
                "runId": RUN_ID,
                "profileId": PROFILE_ID,
                "status": "B1_MODEL_FIT_COMPLETE_AUDIT_PENDING",
                "createdAt": utc_now(),
                "scoringAuthorized": False,
                "readyForService": False,
                "modelFitPerformed": True,
                "scorePerformed": False,
                "sourceRows": SOURCE_ROWS,
                "logicalRows": LOGICAL_ROWS,
                "partitionCount": PARTITIONS,
                "treeCount": metrics["treeCount"],
                "outerRunnerSha256": outer["sha256"],
                "sparkWorkerSha256": worker["sha256"],
                "implementationSetSha256": implementation,
                "modelInputSetSha256": phase_lock["modelInputSetSha256"],
                "workerRuntimeSetSha256": phase_lock["workerRuntimeSetSha256"],
                "executionSetSha256": phase_lock["executionSetSha256"],
                "controlReferenceSetSha256": phase_lock["controlReferenceSetSha256"],
                "inputSetSha256": phase_lock["inputSetSha256"],
                "preflightManifestSha256": audited["manifestPin"]["sha256"],
                "preflightReviewSha256": audited["reviewPin"]["sha256"],
                "r2FitFailureSha256": R2_FIT_FAILURE_SHA256,
                "r3FitFailureSha256": R3_FIT_FAILURE_SHA256,
                "recoveryReferenceSha256": pin_file(stage / "recovery-reference.json")["sha256"],
                "executionProfileSha256": pin_file(stage / "execution-profile.json")["sha256"],
                "partitionIdentitySha256": pin_file(stage / "partition-identity.json")["sha256"],
                "modelInventorySha256": pin_file(stage / "model-file-inventory.json")["sha256"],
                "modelFileSetSha256": inventory["inventorySha256"],
                "thresholdFixtureSha256": pin_file(stage / "threshold-fixtures.npz")["sha256"],
                "portableParity": parity,
                "resourceStatus": resource["status"],
                "runtimeVersions": runtime_versions,
                "publication": publication_evidence(lease),
            },
        )
        failure_context["failureStage"] = "PUBLICATION"
        failure_context["failureKind"] = "IO"
        publish_phase_success(lease, stage, dependencies.fingerprint, dependencies.rehash)
        cleanup_ok = True
        return final
    except BaseException as error:
        failure_material = capture_failure_material(stage, failure_context, error, stage_owned=stage_owned)
        cleanup_errors: list[str] = []
        for owned, present in ((owned_scratch, scratch_owned), (owned_stage, stage_owned)):
            if not present or owned is None:
                continue
            try:
                cleanup_owned_path(owned)
            except Exception as cleanup_error:
                cleanup_errors.append(repr(cleanup_error))
        cleanup_ok = not cleanup_errors and not isinstance(error, ContainerCleanupError)
        if not cleanup_ok:
            failure_material["failureStage"] = "CLEANUP"
            failure_material["failureKind"] = "CLEANUP"
        try:
            write_failure(paths, phase, error, cleanup_ok, failure_material, [scratch, stage],
                          lease=lease, dependencies=dependencies, control_state=control_state,
                          host_gate=observed_host_gate, started_at=phase_started_at)
        except Exception:
            if cleanup_errors:
                raise RuntimeError(f"fit failed and cleanup was incomplete: {cleanup_errors}") from error
            raise
        raise

def score_input_records(paths: ExecutionPaths) -> list[dict[str, Any]]:
    """Phase-only score rows; the invariant nine-record model group stays intact."""
    return [verify_expected_pin(paths.natural_score, "source/natural-score.parquet")]


def make_score_lock(
    model_records: Sequence[Mapping[str, Any]],
    score_records: Sequence[Mapping[str, Any]],
    runtime_records: Sequence[Mapping[str, Any]],
    execution: Sequence[Mapping[str, Any]],
    control_records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    models = [dict(record) for record in model_records]
    scores = [dict(record) for record in score_records]
    runtime = [dict(record) for record in runtime_records]
    executions = [dict(record) for record in execution]
    controls = [dict(record) for record in control_records]
    all_records = [*models, *scores, *runtime, *executions, *controls]
    logical_paths = [record.get("path") for record in all_records]
    require(len(logical_paths) == len(set(logical_paths)), "score record groups have overlapping logical paths")
    require(canonical_record_set_sha256(models) == MODEL_INPUT_SET_SHA256, "score model-input invariant drift")
    return {
        "schemaVersion": "feelm-service-v1-b1-score-input-lock/2",
        "phase": "score",
        "runId": RUN_ID,
        "profileId": PROFILE_ID,
        "modelInputRecords": models,
        "scoreInputRecords": scores,
        "workerRuntimeRecords": runtime,
        "executionRecords": executions,
        "controlReferences": controls,
        "modelInputSetSha256": canonical_record_set_sha256(models),
        "scoreInputSetSha256": canonical_record_set_sha256(scores),
        "workerRuntimeSetSha256": canonical_record_set_sha256(runtime),
        "executionSetSha256": canonical_record_set_sha256(executions),
        "controlReferenceSetSha256": canonical_record_set_sha256(controls),
        "inputSetSha256": canonical_record_set_sha256(all_records),
        "r2TrainingSourceSetSha256": R2_TRAINING_SOURCE_SET_SHA256,
        "evaluationTargetsRead": False,
    }


def _fit_reference(
    audited: Mapping[str, Any],
    inventory_pin: Mapping[str, Any],
    inventory: Mapping[str, Any],
    outer: Mapping[str, Any],
    worker: Mapping[str, Any],
    profile_pin: Mapping[str, Any],
    controls: Sequence[Mapping[str, Any]],
    recovery: Mapping[str, Any],
    phase_lock: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schemaVersion": "feelm-service-v1-b1-fit-reference/2",
        "runId": RUN_ID,
        "profileId": PROFILE_ID,
        "fitManifest": dict(audited["manifestPin"]),
        "fitReview": dict(audited["reviewPin"]),
        "modelInventory": dict(inventory_pin),
        "modelFileSetSha256": inventory["inventorySha256"],
        "modelFiles": [dict(record) for record in inventory["files"]],
        "reviewedArtifacts": [dict(record) for record in controls],
        "recoveryReference": embedded_payload(recovery),
        "outerRunner": dict(outer),
        "sparkWorker": dict(worker),
        "executionProfile": dict(profile_pin),
        "modelInputSetSha256": phase_lock["modelInputSetSha256"],
        "workerRuntimeSetSha256": phase_lock["workerRuntimeSetSha256"],
        "executionSetSha256": phase_lock["executionSetSha256"],
        "scoreInputSetSha256": phase_lock["scoreInputSetSha256"],
        "scorePhaseInputSetSha256": phase_lock["inputSetSha256"],
    }

def _score_impl(
    paths: ExecutionPaths,
    *,
    server_controls: ServerPhaseControls,
    expected_control_sha256: Mapping[str, str],
    host_gate: Mapping[str, Any] | None = None,
    fit_manifest: Path,
    fit_review: Path,
    expected_fit_manifest_sha256: str,
    expected_fit_review_sha256: str,
    expected_fit_model_inventory_sha256: str,
    expected_outer_runner_sha256: str,
    expected_spark_worker_sha256: str,
    expected_execution_set_sha256: str,
) -> Path:
    phase = "score"
    ensure_phase_clear(paths, phase)
    require(not running_container_names(), "another B1 Spark container exists")
    canonical_fit_manifest = paths.bundle("fit") / "manifest.json"
    canonical_fit_review = paths.review("fit")
    require(fit_manifest.resolve() == canonical_fit_manifest.resolve(), "noncanonical fit manifest")
    require(fit_review.resolve() == canonical_fit_review.resolve(), "noncanonical fit review")
    require(re.fullmatch(r"[0-9a-f]{64}", expected_fit_manifest_sha256) is not None
            and re.fullmatch(r"[0-9a-f]{64}", expected_fit_review_sha256) is not None,
            "fit expected SHA must be lowercase")
    preflight_manifest = paths.bundle("preflight") / "manifest.json"
    preflight_review = paths.review("preflight")
    merged_expected = dict(expected_control_sha256)
    merged_expected.update({"preflightManifest": pin_file(preflight_manifest)["sha256"],
                            "preflightReview": pin_file(preflight_review)["sha256"],
                            "fitManifest": expected_fit_manifest_sha256,
                            "fitReview": expected_fit_review_sha256})
    control_state = verify_server_control_chain(
        paths, phase=phase, controls=server_controls.for_phase(paths, phase), expected_sha256=merged_expected)
    scratch_probe_root = Path(control_state["payloads"]["serverReceiptManifest"]["actualRoots"]["scratchRoot"])
    observed_host_gate = (dict(host_gate) if host_gate is not None else
                          collect_dynamic_host_gate(paths, phase=phase, control_state=control_state,
                                                    scratch_root=scratch_probe_root))
    verify_dynamic_host_gate(observed_host_gate, phase=phase,
                             current_namespace=expected_namespace_before(phase),
                             paths=paths, control_state=control_state)
    outer, worker, implementation = validate_expected_implementation(paths, expected_outer_runner_sha256, expected_spark_worker_sha256)
    profile_payload, profile_pin = execution_profile(paths)
    ancestry = validate_r3_ancestry(paths)
    executions = execution_records(paths)
    execution_digest = canonical_record_set_sha256(executions)
    require(execution_digest == expected_execution_set_sha256.lower(), "expected execution digest drift")
    require(pin_file(fit_manifest)["sha256"] == expected_fit_manifest_sha256.lower(), "expected fit manifest SHA drift")
    require(pin_file(fit_review)["sha256"] == expected_fit_review_sha256.lower(), "expected fit review SHA drift")
    inventory_path = paths.bundle("fit") / "model-file-inventory.json"
    require(pin_file(inventory_path)["sha256"] == expected_fit_model_inventory_sha256.lower(), "expected fit model-inventory SHA drift")
    audited = validate_fit_review(paths, fit_review)
    preflight_audited = validate_preflight_review(paths)
    validate_preflight_recovery(paths, preflight_audited, ancestry, outer, worker, profile_pin, execution_digest)
    image_id = inspect_image_id()
    models = model_input_records(paths)
    score_inputs = score_input_records(paths)
    runtime = worker_runtime_records(paths, image_id)
    controls = merge_records(_fit_control_records(paths), control_state["records"])
    phase_lock = make_score_lock(models, score_inputs, runtime, executions, controls)
    recovery = recovery_reference(paths, ancestry, outer, worker, profile_pin, execution_digest)
    inventory = load_json(inventory_path)
    inventory_pin = pinned_control(paths, inventory_path)
    reference = _fit_reference(audited, inventory_pin, inventory, outer, worker, profile_pin, controls, recovery, phase_lock)
    profile_document = phase_execution_profile(phase, profile_payload, profile_pin, outer, execution_digest)

    dependencies = dependency_snapshot(paths, [*models, *score_inputs, *runtime, *executions, *controls])
    lease, stage = acquire_phase_publication(paths, phase, dependencies)
    final = paths.bundle(phase)
    token = str(getattr(lease, "token", uuid.uuid4().hex))
    scratch = final.parent / f".{final.name}.score-scratch-{token}"
    log_path = stage / "run.log"
    cleanup_ok = False
    stage_owned = False
    scratch_owned = False
    owned_stage: OwnedPath | None = None
    owned_scratch: OwnedPath | None = None
    control_paths = [resolve_logical_path(paths, record["path"]) for record in controls]
    readonly = [
        paths.outer_runner,
        paths.spark_worker,
        paths.plan,
        paths.profile_contract,
        paths.feature_contract,
        paths.natural_score,
        paths.bundle("fit"),
        fit_review,
        *control_paths,
    ]
    mounts = [
        Mount(paths.outer_runner, "/app/run_service_v1_b1_gbt_r4.py"),
        Mount(paths.spark_worker, "/app/service_v1_b1_spark_worker.py"),
        Mount(paths.plan, "/contract/service-v1-b1-r4-server-fit-recovery.md"),
        Mount(paths.profile_contract, "/contract/execution-profile-contract.json"),
        Mount(paths.feature_contract, "/contract/feature-schema.v1.json"),
        Mount(paths.natural_score, "/input/natural-score.parquet"),
        Mount(paths.bundle("fit"), "/fit"),
        Mount(fit_review, "/control/fit-review.json"),
        Mount(scratch, "/scratch", readonly=False),
        Mount(stage, "/output", readonly=False),
    ]
    for index, path in enumerate(control_paths):
        mounts.append(Mount(path, f"/control/score-input/{index:03d}-{path.name}"))
    name = f"feelm-b1-r4-score-{token[:12]}"
    worker_args = [
        "score",
        "--score-input", "/input/natural-score.parquet",
        "--model-dir", "/fit/model/native",
        "--output", "/output",
        "--scratch", "/scratch",
    ]
    docker_command = spark_container_command(name, mounts, worker_args)
    command = command_document(
        phase,
        image_id,
        [{"order": 0, "workerAction": "score", "timeoutSeconds": PHASE_TIMEOUT_SECONDS["score"], "command": docker_command}],
    )
    failure_context: dict[str, Any] = {
        "phase": phase,
        "phaseInputLock": phase_lock,
        "recoveryReference": recovery,
        "phaseReference": reference,
        "executionProfile": profile_document,
        "commandDocument": command,
        "implementation": {"outerRunner": outer, "sparkWorker": worker, "implementationSetSha256": implementation},
        "completedStages": [],
        "failureStage": "INPUT_LOCK", "failureKind": "CONTRACT",
    }
    phase_started_at = utc_now()
    try:
        make_stage(final, paths.review(phase), stage)
        stage_owned = True
        owned_stage = capture_owned_path(stage)
        scratch.mkdir()
        scratch_owned = True
        owned_scratch = capture_owned_path(scratch)
        validate_mounts(mounts, allowed_readonly=readonly, allowed_write=[scratch, stage])
        write_json_exclusive(stage / "score-input-lock.json", phase_lock)
        write_json_exclusive(stage / "recovery-reference.json", recovery)
        write_json_exclusive(stage / "fit-reference.json", reference)
        write_json_exclusive(stage / "execution-profile.json", profile_document)
        write_json_exclusive(stage / "command.json", command)
        delivery_reference, receipt_reference = server_reference_documents(control_state)
        write_json_exclusive(stage / "delivery-reference.json", delivery_reference)
        write_json_exclusive(stage / "server-receipt-reference.json", receipt_reference)
        write_json_exclusive(stage / "host-gate.json", observed_host_gate)
        failure_context["failureStage"] = "CONTAINER_CREATE"
        failure_context["failureKind"] = "PROCESS"
        started_at = utc_now()
        result = run_container(name, docker_command, log_path, expected_worker_status="B1_NATURAL_SCORE_WORKER_COMPLETE", timeout_seconds=PHASE_TIMEOUT_SECONDS["score"])
        completed_at = utc_now()
        completed = completed_container_evidence(result, "score")
        completed.update({"startedAt": started_at, "completedAt": completed_at})
        failure_context["completedStages"] = [completed]
        failure_context["failureStage"] = "OUTPUT_VALIDATION"
        failure_context["failureKind"] = "AUDIT"
        cleanup_owned_path(owned_scratch)
        scratch_owned = False
        predictions_path = stage / "score" / "predictions.parquet"
        runtime_versions = validate_runtime_versions(result.worker)
        score_census = validate_score_predictions(paths.natural_score, predictions_path)
        analyzed = stage / "score" / "analyzed-plan.txt"
        validate_analyzed_score_plan(analyzed)
        resource = _stage_resource(phase, [("score", started_at, completed_at, result)])
        write_json_exclusive(stage / "resource.json", resource)

        current_ancestry = validate_r3_ancestry(paths)
        current_models = model_input_records(paths)
        current_score_inputs = score_input_records(paths)
        current_runtime = worker_runtime_records(paths, inspect_image_id())
        current_executions = execution_records(paths)
        current_control_state = verify_server_control_chain(
            paths, phase=phase, controls=server_controls.for_phase(paths, phase), expected_sha256=merged_expected)
        current_controls = merge_records(_fit_control_records(paths), current_control_state["records"])
        require(records_equal(models, current_models), "score model input mutated")
        require(records_equal(score_inputs, current_score_inputs), "score phase input mutated")
        require(records_equal(runtime, current_runtime), "score worker runtime mutated")
        require(records_equal(executions, current_executions), "score execution input mutated")
        require(records_equal(controls, current_controls), "fit/preflight/r2 control ancestry mutated")
        require(recovery == recovery_reference(paths, current_ancestry, outer, worker, profile_pin, execution_digest), "score recovery ancestry mutated")
        require(make_score_lock(current_models, current_score_inputs, current_runtime, current_executions, current_controls) == phase_lock, "score input lock mutated during execution")
        write_manifest_last(
            stage,
            {
                "schemaVersion": "feelm-service-v1-b1-score-manifest/2",
                "runId": RUN_ID,
                "profileId": PROFILE_ID,
                "status": "B1_NATURAL_SCORE_COMPLETE_AUDIT_PENDING",
                "createdAt": utc_now(),
                "evaluationAuthorized": False,
                "readyForService": False,
                "modelFitPerformed": False,
                "scorePerformed": True,
                "rows": SCORE_ROWS,
                "outerRunnerSha256": outer["sha256"],
                "sparkWorkerSha256": worker["sha256"],
                "implementationSetSha256": implementation,
                "modelInputSetSha256": phase_lock["modelInputSetSha256"],
                "scoreInputSetSha256": phase_lock["scoreInputSetSha256"],
                "workerRuntimeSetSha256": phase_lock["workerRuntimeSetSha256"],
                "executionSetSha256": phase_lock["executionSetSha256"],
                "controlReferenceSetSha256": phase_lock["controlReferenceSetSha256"],
                "inputSetSha256": phase_lock["inputSetSha256"],
                "fitManifestSha256": audited["manifestPin"]["sha256"],
                "fitReviewSha256": audited["reviewPin"]["sha256"],
                "modelInventorySha256": inventory_pin["sha256"],
                "modelFileSetSha256": inventory["inventorySha256"],
                "r2FitFailureSha256": R2_FIT_FAILURE_SHA256,
                "r3FitFailureSha256": R3_FIT_FAILURE_SHA256,
                "recoveryReferenceSha256": pin_file(stage / "recovery-reference.json")["sha256"],
                "executionProfileSha256": pin_file(stage / "execution-profile.json")["sha256"],
                "predictionSha256": pin_file(predictions_path)["sha256"],
                "scoreCensus": score_census,
                "labelProjected": False,
                "evaluationTargetsRead": False,
                "resourceStatus": resource["status"],
                "runtimeVersions": runtime_versions,
                "publication": publication_evidence(lease),
            },
        )
        failure_context["failureStage"] = "PUBLICATION"
        failure_context["failureKind"] = "IO"
        publish_phase_success(lease, stage, dependencies.fingerprint, dependencies.rehash)
        cleanup_ok = True
        return final
    except BaseException as error:
        failure_material = capture_failure_material(stage, failure_context, error, stage_owned=stage_owned)
        cleanup_errors: list[str] = []
        for owned, present in ((owned_scratch, scratch_owned), (owned_stage, stage_owned)):
            if not present or owned is None:
                continue
            try:
                cleanup_owned_path(owned)
            except Exception as cleanup_error:
                cleanup_errors.append(repr(cleanup_error))
        cleanup_ok = not cleanup_errors and not isinstance(error, ContainerCleanupError)
        if not cleanup_ok:
            failure_material["failureStage"] = "CLEANUP"
            failure_material["failureKind"] = "CLEANUP"
        try:
            write_failure(paths, phase, error, cleanup_ok, failure_material, [scratch, stage],
                          lease=lease, dependencies=dependencies, control_state=control_state,
                          host_gate=observed_host_gate, started_at=phase_started_at)
        except Exception:
            if cleanup_errors:
                raise RuntimeError(f"score failed and cleanup was incomplete: {cleanup_errors}") from error
            raise
        raise


def preflight(
    paths: ExecutionPaths, *, server_controls: ServerPhaseControls,
    expected_control_sha256: Mapping[str, str], dry_host_gate: Mapping[str, Any] | None = None,
    full_host_gate_provider: Any | None = None,
) -> Path:
    started_at = utc_now()
    try:
        return _preflight_impl(
            paths, server_controls=server_controls,
            expected_control_sha256=expected_control_sha256, dry_host_gate=dry_host_gate,
            full_host_gate_provider=full_host_gate_provider)
    except BaseException as error:
        if (not isinstance(error, PhasePublicationAcquisitionError)
                and namespace_children(paths) == sorted(expected_namespace_before("preflight"))):
            publish_pre_execution_failure(
                paths, "preflight", error, server_controls=server_controls,
                host_gate=dry_host_gate, started_at=started_at)
        raise


def fit(
    paths: ExecutionPaths, *, server_controls: ServerPhaseControls,
    expected_control_sha256: Mapping[str, str], host_gate: Mapping[str, Any] | None = None,
    preflight_manifest: Path, preflight_review: Path,
    expected_preflight_manifest_sha256: str, expected_preflight_review_sha256: str,
    expected_outer_runner_sha256: str, expected_spark_worker_sha256: str,
    expected_model_input_set_sha256: str, expected_worker_runtime_set_sha256: str,
    expected_execution_set_sha256: str,
) -> Path:
    started_at = utc_now()
    try:
        return _fit_impl(
            paths, server_controls=server_controls,
            expected_control_sha256=expected_control_sha256, host_gate=host_gate,
            preflight_manifest=preflight_manifest, preflight_review=preflight_review,
            expected_preflight_manifest_sha256=expected_preflight_manifest_sha256,
            expected_preflight_review_sha256=expected_preflight_review_sha256,
            expected_outer_runner_sha256=expected_outer_runner_sha256,
            expected_spark_worker_sha256=expected_spark_worker_sha256,
            expected_model_input_set_sha256=expected_model_input_set_sha256,
            expected_worker_runtime_set_sha256=expected_worker_runtime_set_sha256,
            expected_execution_set_sha256=expected_execution_set_sha256)
    except BaseException as error:
        if (not isinstance(error, PhasePublicationAcquisitionError)
                and namespace_children(paths) == sorted(expected_namespace_before("fit"))):
            publish_pre_execution_failure(
                paths, "fit", error, server_controls=server_controls,
                extra_controls={"preflightManifest": preflight_manifest,
                                "preflightReview": preflight_review},
                host_gate=host_gate, started_at=started_at)
        raise


def score(
    paths: ExecutionPaths, *, server_controls: ServerPhaseControls,
    expected_control_sha256: Mapping[str, str], host_gate: Mapping[str, Any] | None = None,
    fit_manifest: Path, fit_review: Path, expected_fit_manifest_sha256: str,
    expected_fit_review_sha256: str, expected_fit_model_inventory_sha256: str,
    expected_outer_runner_sha256: str, expected_spark_worker_sha256: str,
    expected_execution_set_sha256: str,
) -> Path:
    started_at = utc_now()
    try:
        return _score_impl(
            paths, server_controls=server_controls,
            expected_control_sha256=expected_control_sha256, host_gate=host_gate,
            fit_manifest=fit_manifest, fit_review=fit_review,
            expected_fit_manifest_sha256=expected_fit_manifest_sha256,
            expected_fit_review_sha256=expected_fit_review_sha256,
            expected_fit_model_inventory_sha256=expected_fit_model_inventory_sha256,
            expected_outer_runner_sha256=expected_outer_runner_sha256,
            expected_spark_worker_sha256=expected_spark_worker_sha256,
            expected_execution_set_sha256=expected_execution_set_sha256)
    except BaseException as error:
        if (not isinstance(error, PhasePublicationAcquisitionError)
                and namespace_children(paths) == sorted(expected_namespace_before("score"))):
            publish_pre_execution_failure(
                paths, "score", error, server_controls=server_controls,
                extra_controls={"fitManifest": fit_manifest, "fitReview": fit_review,
                                "preflightManifest": paths.bundle("preflight") / "manifest.json",
                                "preflightReview": paths.review("preflight")},
                host_gate=host_gate, started_at=started_at)
        raise


def _mapping_path(value: Any, names: Sequence[str]) -> Path:
    for name in names:
        if isinstance(value, Mapping) and name in value:
            return Path(value[name])
        candidate = getattr(value, name, None)
        if candidate is not None:
            return Path(candidate)
    raise ValueError(f"missing control path: {names[0]}")


def _evaluation_control_maps(
    controls: Any, expected_sha256: Mapping[str, str], *, confirmation: bool = False
) -> tuple[dict[str, Path], dict[str, str]]:
    aliases = {
        "scoreManifest": ("score_manifest", "scoreManifest"),
        "scoreReview": ("score_review", "scoreReview"),
        "deliveryManifest": ("delivery_manifest", "deliveryManifest"),
        "deliveryReview": ("delivery_review", "deliveryReview"),
        "serverReceiptManifest": ("server_receipt_manifest", "serverReceiptManifest"),
        "serverReceiptReview": ("server_receipt_review", "serverReceiptReview"),
        "hostRuntimeLock": ("host_runtime_lock", "hostRuntimeLock"),
    }
    if confirmation:
        aliases.update({"selectionManifest": ("selection_manifest", "selectionManifest"),
                        "selectionReview": ("selection_review", "selectionReview")})
    paths_map = {canonical: _mapping_path(controls, names) for canonical, names in aliases.items()}
    expected: dict[str, str] = {}
    for canonical, names in aliases.items():
        matches = [expected_sha256[name] for name in names if name in expected_sha256]
        require(len(matches) == 1, f"missing or duplicate expected SHA: {canonical}")
        require(isinstance(matches[0], str) and re.fullmatch(r"[0-9a-f]{64}", matches[0]) is not None,
                f"expected SHA must be lowercase: {canonical}")
        expected[canonical] = matches[0]
    require(len(expected_sha256) == len(aliases), "evaluation expected SHA set drift")
    return paths_map, expected


def _absolute_record(path: Path) -> dict[str, Any]:
    absolute = path.absolute()
    return pin_file(absolute, str(absolute))


def _server_evaluation_dependency_records(
    paths: ExecutionPaths,
    *,
    phase: str,
    control_records: Mapping[str, Mapping[str, Any]],
    ancestry: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Build the deterministic post-gate dependency closure for the child.

    The receipt destination inventory supplies every delivered evaluation file.
    Current server-generated bundles and historical ancestry are appended and
    de-duplicated by absolute path.  All reads happen after score/host approval.
    """
    records: list[dict[str, Any]] = [dict(record) for record in control_records.values()]
    destination_inventory = paths.server_receipt / "destination-inventory.json"
    records.append(_absolute_record(destination_inventory))
    destination = load_json(destination_inventory)
    rows = destination.get("records")
    require(isinstance(rows, list) and rows, "receipt destination inventory missing records")
    for row in rows:
        require(isinstance(row, Mapping) and set(row) == {"recordId", "logicalPath", "absolutePath", "bytes",
            "sha256", "stDev", "stIno", "nlink", "kind"}, "destination record shape drift")
        require(row["kind"] in {"regular-file", "docker-image-contract"}, "destination record kind drift")
        if row["kind"] == "docker-image-contract":
            continue
        expected = {"path": str(row["absolutePath"]), "bytes": row["bytes"], "sha256": row["sha256"]}
        require(Path(expected["path"]).is_absolute(), "destination record path is not absolute")
        require(_absolute_record(Path(expected["path"])) == expected, "destination record current hash drift")
        records.append(expected)
    for bundle_phase in ("preflight", "fit", "score"):
        bundle = paths.bundle(bundle_phase)
        for member in sorted((item for item in bundle.rglob("*") if item.is_file()), key=lambda item: item.as_posix()):
            records.append(_absolute_record(member))
        records.append(_absolute_record(paths.review(bundle_phase)))
    if phase == "confirmation":
        selection = paths.bundle("selection")
        for member in sorted((item for item in selection.rglob("*") if item.is_file()), key=lambda item: item.as_posix()):
            records.append(_absolute_record(member))
        records.append(_absolute_record(paths.review("selection")))
    for record in r3_control_records(ancestry):
        if record["path"] == "runtime/docker-image-id":
            continue
        physical = (_record_physical_path(paths, record["path"])
                    if record["path"] in {
                        "contract/training-recipe.v1.json", "contract/service-v1.json", "contract/MODELS.md",
                        "contract/feature-schema.v1.json", "source/natural-train.parquet",
                        "source/natural-score.parquet", "source/tmdb-masked-train.parquet",
                        "source/masked-manifest.json", "source/views-manifest.json", "source/masked-review.json",
                        "implementation/service_v1_b1_spark_worker.py", "implementation/combination340_models.py",
                        "implementation/rec046_common.py", "execution/service-v1-b1-r4-server-fit-recovery.md",
                        "execution/ec2-8vcpu32g-local5-fit20g-t28800-profile.json",
                        "execution/run_service_v1_b1_gbt_r4.py", "execution/test_service_v1_b1_gbt_runner_r4.py"}
                    else resolve_logical_path(paths, record["path"]))
        records.append(_absolute_record(physical))
    records.extend((_absolute_record(paths.plan), _absolute_record(paths.profile_contract),
                    _absolute_record(paths.outer_runner), _absolute_record(paths.spark_auditor)))
    runtime = load_json(paths.host_runtime_lock)
    for key in ("requirementsLock", "wheelhouseManifest", "venvInventory"):
        expected = runtime.get(key)
        require(isinstance(expected, Mapping) and set(expected) == {"path", "bytes", "sha256"},
                f"runtime {key} pin shape drift")
        physical = Path(str(expected["path"]))
        require(physical.is_absolute(), f"runtime {key} path must be absolute")
        observed = _absolute_record(physical)
        require(observed == dict(expected), f"runtime {key} current pin drift")
        records.append(observed)
    interpreter = Path(str(runtime["absoluteInterpreter"]))
    records.append(_absolute_record(interpreter))
    inventory_path = Path(str(runtime["venvInventory"]["path"]))
    venv_inventory = load_json(inventory_path, allow_null=False, require_lf=True)
    require(set(venv_inventory) == {"schemaVersion", "root", "records", "regularFileCount",
        "symlinkCount", "specialFileCount", "hardLinkAliasCount", "recordSetSha256"},
        "venv inventory field drift")
    require(venv_inventory["schemaVersion"] == "feelm-service-v1-b1-r4-venv-inventory/1"
            and venv_inventory["symlinkCount"] == 0 and venv_inventory["specialFileCount"] == 0
            and venv_inventory["hardLinkAliasCount"] == 0, "venv inventory safety drift")
    venv_root = Path(str(venv_inventory["root"]))
    require(venv_root.is_absolute() and venv_root == interpreter.parent.parent,
            "venv inventory root drift")
    inventory_rows = venv_inventory["records"]
    require(isinstance(inventory_rows, list)
            and len(inventory_rows) == venv_inventory["regularFileCount"],
            "venv regular-file census drift")
    expected_names: set[str] = set()
    simplified: list[dict[str, Any]] = []
    for row in inventory_rows:
        require(isinstance(row, Mapping) and set(row) == {"path", "bytes", "sha256", "mode",
            "stDev", "stIno", "nlink"}, "venv inventory record shape drift")
        relative = str(row["path"])
        require(relative and not Path(relative).is_absolute() and "\\" not in relative
                and all(part not in {"", ".", ".."} for part in relative.split("/")),
                "venv inventory relative path drift")
        require(relative not in expected_names, "duplicate venv inventory path")
        expected_names.add(relative)
        physical = venv_root.joinpath(*relative.split("/"))
        observed = _absolute_record(physical)
        stat_row = os.lstat(physical)
        require(observed["bytes"] == row["bytes"] and observed["sha256"] == row["sha256"]
                and stat.S_IMODE(stat_row.st_mode) == row["mode"] and stat_row.st_dev == row["stDev"]
                and stat_row.st_ino == row["stIno"] and stat_row.st_nlink == row["nlink"] == 1,
                f"venv file identity drift: {relative}")
        records.append(observed)
        simplified.append({"path": relative, "bytes": row["bytes"], "sha256": row["sha256"]})
    actual_names = {item.relative_to(venv_root).as_posix() for item in venv_root.rglob("*") if item.is_file()}
    require(actual_names == expected_names, "venv filesystem inventory drift")
    require(canonical_record_set_sha256(simplified) == venv_inventory["recordSetSha256"],
            "venv record-set digest drift")
    unique: dict[str, dict[str, Any]] = {}
    for record in records:
        existing = unique.get(record["path"])
        require(existing is None or existing == record, f"conflicting server dependency: {record['path']}")
        unique[record["path"]] = record
    return [unique[name] for name in sorted(unique)]


def _pin_absolute_records(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    current: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    for expected in records:
        require(isinstance(expected, Mapping) and set(expected) == {"path", "bytes", "sha256"},
                "evaluation dependency pin shape drift")
        path_text = expected["path"]
        require(isinstance(path_text, str) and Path(path_text).is_absolute(),
                "evaluation dependency path must be absolute")
        observed = pin_file(Path(path_text), path_text)
        require(observed == dict(expected), f"evaluation dependency drift: {path_text}")
        if path_text in seen:
            require(seen[path_text] == observed, f"conflicting evaluation dependency: {path_text}")
        else:
            seen[path_text] = observed
            current.append(observed)
    return current


def _verify_evaluation_control_gate(
    paths: ExecutionPaths, *, phase: str, controls: Any, expected_sha256: Mapping[str, str]
) -> dict[str, Any]:
    """Rehash public controls without resolving any private evaluation path."""
    require(phase in {"calibrate-select", "confirmation"}, "invalid evaluation phase")
    confirmation = phase == "confirmation"
    control_paths, expected = _evaluation_control_maps(controls, expected_sha256, confirmation=confirmation)
    canonical = {
        "scoreManifest": paths.bundle("score") / "manifest.json",
        "scoreReview": paths.review("score"),
        "deliveryManifest": paths.delivery_manifest,
        "deliveryReview": paths.delivery_review,
        "serverReceiptManifest": paths.server_receipt / "manifest.json",
        "serverReceiptReview": paths.server_receipt_review,
        "hostRuntimeLock": paths.host_runtime_lock,
    }
    if confirmation:
        canonical.update({"selectionManifest": paths.bundle("selection") / "manifest.json",
                          "selectionReview": paths.review("selection")})
    require(set(control_paths) == set(canonical), "evaluation control set drift")
    records: dict[str, dict[str, Any]] = {}
    payloads: dict[str, dict[str, Any]] = {}
    # Score review is deliberately first: a denial stops all later control and
    # evaluation access.
    order = ["scoreReview", "scoreManifest", "deliveryReview", "deliveryManifest",
             "serverReceiptReview", "serverReceiptManifest", "hostRuntimeLock"]
    if confirmation:
        order = ["selectionReview", "selectionManifest", *order]
    for name in order:
        supplied = control_paths[name].absolute()
        require(supplied.resolve() == canonical[name].resolve(), f"noncanonical evaluation control: {name}")
        observed = pin_file(supplied, str(supplied))
        require(observed["sha256"] == expected[name], f"evaluation control SHA drift: {name}")
        payload = load_json(supplied)
        records[name] = observed
        payloads[name] = payload
        if name != "hostRuntimeLock":
            require(payload.get("runId") == RUN_ID and payload.get("profileId") == PROFILE_ID,
                    f"evaluation control identity drift: {name}")
        if name == "scoreReview":
            require(payload.get("status") == "PASS"
                    and payload.get("decision", {}).get("evaluationSelectionEligible") is True,
                    "score review does not authorize selection")
        if name == "selectionReview":
            require(payload.get("status") == "PASS"
                    and payload.get("decision", {}).get("confirmationEligible") is True,
                    "selection review does not authorize confirmation")
    require(payloads["scoreManifest"].get("evaluationAuthorized") is False
            and payloads["scoreManifest"].get("evaluationTargetsRead") is False
            and payloads["scoreManifest"].get("labelProjected") is False
            and payloads["scoreManifest"].get("readyForService") is False,
            "score label-isolation contract drift")
    require(payloads["deliveryReview"].get("status") == "PASS"
            and payloads["deliveryReview"].get("decision", {}).get("serverTransferEligible") is True,
            "delivery review gate failed")
    require(payloads["serverReceiptReview"].get("status") == "PASS"
            and payloads["serverReceiptReview"].get("decision", {}).get("publicPreflightEligible") is True,
            "receipt review gate failed")
    runtime = payloads["hostRuntimeLock"]
    require(runtime.get("schemaVersion") == "feelm-service-v1-b1-r4-host-runtime-lock/1",
            "host runtime lock schema drift")
    interpreter = Path(str(runtime.get("absoluteInterpreter", "")))
    require(interpreter.is_absolute() and interpreter.resolve() == Path(sys.executable).resolve(),
            "evaluation must use the sealed interpreter")
    require(sha256_file(interpreter) == runtime.get("interpreterSha256"), "sealed interpreter SHA drift")
    return {"paths": control_paths, "expectedSha256": expected,
            "records": records, "payloads": payloads,
            "controlSetSha256": canonical_record_set_sha256(records.values())}


def verify_server_evaluation_gate(
    paths: ExecutionPaths,
    *,
    phase: str,
    controls: Any,
    expected_sha256: Mapping[str, str],
    dynamic_host_gate: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify score authorization and server ancestry before evaluation input IO.

    The public control files and host gate are checked first.  Only after that
    barrier may the generic absolute dependency records (which include labels)
    be opened.  This ordering is observable by tests and is the runner-side
    enforcement of the label-access contract.
    """
    normalized_phase = "calibrate-select" if phase in {"selection", "calibrate-select"} else phase
    require(normalized_phase in {"calibrate-select", "confirmation"}, "invalid evaluation phase")
    confirmation = normalized_phase == "confirmation"
    control_state = _verify_evaluation_control_gate(
        paths, phase=normalized_phase, controls=controls, expected_sha256=expected_sha256)
    control_paths = control_state["paths"]
    records = control_state["records"]
    payloads = control_state["payloads"]

    host_gate = verify_dynamic_host_gate(
        dynamic_host_gate, phase=normalized_phase,
        current_namespace=expected_namespace_before(normalized_phase),
        paths=paths, control_state={"records": list(records.values()), "payloads": payloads})

    # These operations may read model/training/control ancestry but never an
    # evaluation label, role or context file.
    ancestry = validate_r3_ancestry(paths)
    score_review = validate_score_review(paths, control_paths["scoreReview"])
    execution_profile(paths)
    verify_plan(paths)
    dependencies = _server_evaluation_dependency_records(
        paths, phase="confirmation" if confirmation else "calibrate-select",
        control_records=records, ancestry=ancestry)
    # Keep these local checks explicit even though only the minimal two-field
    # result crosses into the evaluator process.
    require(score_review["review"]["status"] == "PASS" and host_gate["status"] == "PASS",
            "evaluation ancestry or host gate blocked")
    return {"status": "PASS", "dependencyRecords": dependencies}


def _evaluation_systemd_commands(
    profile: Mapping[str, Any], phase: str, evaluator_argv: Sequence[str], monitor_argv: Sequence[str]
) -> tuple[list[str], list[str]]:
    config = profile["evaluationSupervisor"]
    selection = phase == "calibrate-select"
    target_unit = config["selectionUnit"] if selection else config["confirmationUnit"]
    monitor_unit = config["selectionMonitorUnit"] if selection else config["confirmationMonitorUnit"]
    target = ["systemd-run", "--unit", target_unit, "--collect", "--wait", "--pipe",
              "--property", f"CPUQuota={config['cpuQuotaPercent']}%",
              "--property", f"MemoryMax={config['memoryMaxBytes']}",
              "--property", f"MemorySwapMax={config['memorySwapMaxBytes']}",
              "--property", f"TasksMax={config['tasksMax']}",
              "--property", f"KillMode={config['killMode']}",
              "--property", f"RuntimeMaxSec={14_400}",
              "--property", f"TimeoutStopSec={config['timeoutStopSeconds']}", *map(str, evaluator_argv)]
    monitor = ["systemd-run", "--unit", monitor_unit, "--collect", "--wait", "--pipe",
               "--property", f"CPUQuota={config['monitorCpuQuotaPercent']}%",
               "--property", f"MemoryMax={config['monitorMemoryMaxBytes']}",
               "--property", "MemorySwapMax=0", "--property", f"TasksMax={config['monitorTasksMax']}",
               "--property", "KillMode=control-group", *map(str, monitor_argv)]
    return target, monitor


def _sealed_runtime_environment(paths: ExecutionPaths) -> tuple[Path, list[str]]:
    runtime = load_json(paths.host_runtime_lock)
    interpreter = Path(str(runtime.get("absoluteInterpreter", "")))
    require(interpreter.is_absolute() and interpreter.resolve() == Path(sys.executable).resolve(),
            "sealed runtime interpreter drift")
    require(pin_file(interpreter, str(interpreter))["sha256"] == runtime.get("interpreterSha256"),
            "sealed runtime interpreter SHA drift")
    runtime_home = paths.standalone / ".runtime/service-v1-b1-r4/home"
    clean = [
        "env", "-i", f"HOME={runtime_home}",
        f"PATH={interpreter.parent}:/usr/bin:/bin", "PYTHONNOUSERSITE=1",
        "PIP_CONFIG_FILE=/dev/null", "PYTHONDONTWRITEBYTECODE=1",
        "LC_ALL=C.UTF-8", "TZ=UTC",
    ]
    return interpreter, clean


def sealed_python_argv(paths: ExecutionPaths, script: Path, arguments: Sequence[str]) -> list[str]:
    """Return the only allowed server Python command: clean env + ``-B -I``."""
    interpreter, clean = _sealed_runtime_environment(paths)
    require(script.is_absolute(), "sealed Python script path must be absolute")
    require_unlinked_path(script, paths.standalone)
    return [*clean, str(interpreter), "-B", "-I", str(script), *map(str, arguments)]


def _verify_sealed_python_argv(
    paths: ExecutionPaths, argv: Sequence[str], script: Path, expected_tail: Sequence[str]
) -> None:
    require(list(argv) == sealed_python_argv(paths, script, expected_tail),
            f"sealed Python argv drift: {script.name}")


def _terminal_json(output: str, prefix: str, label: str) -> dict[str, Any]:
    matches = [line[len(prefix):] for line in output.splitlines() if line.startswith(prefix)]
    require(len(matches) == 1, f"{label} terminal record cardinality drift")
    value = json.loads(matches[0], object_pairs_hook=_reject_duplicate_pairs, parse_constant=_reject_json_constant)
    require(isinstance(value, dict), f"{label} terminal record must be an object")
    return value


def _wait_systemd_active(unit: str, process: subprocess.Popen[str], timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        require(process.poll() is None, "evaluation monitor exited before target start")
        state = _run_probe(["systemctl", "show", unit, "-p", "ActiveState", "--value"], check=False)
        if state["exitCode"] == 0 and state["stdout"].strip() in {"active", "activating"}:
            return
        time.sleep(0.1)
    raise TimeoutError(f"monitor unit did not become active: {unit}")


def _systemd_properties(unit: str, names: Sequence[str]) -> dict[str, str]:
    command = ["systemctl", "show", unit]
    for name in names:
        command.extend(("--property", name))
    probe = _run_probe(command, check=False)
    require(probe["exitCode"] == 0, f"systemd property probe failed: {unit}")
    result: dict[str, str] = {}
    for line in probe["stdout"].splitlines():
        key, separator, value = line.partition("=")
        if separator:
            require(key not in result, f"duplicate systemd property: {key}")
            result[key] = value
    require(set(result) == set(names), f"systemd property set drift: {unit}")
    return result


def _process_record(pid: int) -> dict[str, Any]:
    stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    close = stat_text.rfind(")")
    require(close > 0, f"malformed process stat: {pid}")
    tail = stat_text[close + 2:].split()
    require(len(tail) >= 20, f"truncated process stat: {pid}")
    return {
        "pid": pid,
        "ppid": int(tail[1]),
        "startTimeTicks": int(tail[19]),
        "cmdlineSha256": sha256_bytes(Path(f"/proc/{pid}/cmdline").read_bytes()),
    }


def _process_tree_records(main_pid: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for pid in _pid_tree(main_pid):
        try:
            result.append(_process_record(pid))
        except OSError:
            continue
    result.sort(key=lambda row: row["pid"])
    return result


def _process_start_time_ticks(pid: int) -> int:
    require(type(pid) is int and pid > 0, "invalid process identity PID")
    stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    close = stat_text.rfind(")")
    require(close > 0, f"malformed process stat: {pid}")
    tail = stat_text[close + 2:].split()
    require(len(tail) >= 20, f"truncated process stat: {pid}")
    return int(tail[19])


def _remember_process_identities(
    destination: set[tuple[int, int]], records: Sequence[Mapping[str, Any]],
) -> None:
    for record in records:
        pid = record.get("pid")
        started = record.get("startTimeTicks")
        require(type(pid) is int and pid > 0 and type(started) is int and started >= 0,
                "observed process identity drift")
        destination.add((pid, started))


def _integer_file(path: Path) -> int:
    value = path.read_text(encoding="ascii").strip()
    require(re.fullmatch(r"[0-9]+", value) is not None, f"invalid cgroup integer: {path}")
    return int(value)


def _keyed_integer_file(path: Path) -> dict[str, int]:
    result: dict[str, int] = {}
    for line in path.read_text(encoding="ascii").splitlines():
        fields = line.split()
        require(len(fields) == 2 and re.fullmatch(r"[0-9]+", fields[1]) is not None,
                f"invalid cgroup key/value: {path}")
        require(fields[0] not in result, f"duplicate cgroup key: {fields[0]}")
        result[fields[0]] = int(fields[1])
    return result


def _cgroup_final_membership(cgroup_root: Path) -> tuple[int, list[int]]:
    """Read the final cgroup process census; a removed cgroup is empty by definition."""
    if not os.path.lexists(cgroup_root):
        return 0, []
    pids_current = _integer_file(cgroup_root / "pids.current")
    raw = (cgroup_root / "cgroup.procs").read_text(encoding="ascii").splitlines()
    members: list[int] = []
    for value in raw:
        require(re.fullmatch(r"[0-9]+", value) is not None,
                "invalid cgroup.procs PID")
        pid = int(value)
        require(pid > 0, "invalid cgroup member PID")
        members.append(pid)
    require(len(members) == len(set(members)), "duplicate cgroup member PID")
    return pids_current, sorted(members)


def _cgroup_process_identities(cgroup_root: Path) -> set[tuple[int, int]]:
    _, members = _cgroup_final_membership(cgroup_root)
    identities: set[tuple[int, int]] = set()
    for pid in members:
        try:
            identities.add((pid, _process_start_time_ticks(pid)))
        except FileNotFoundError:
            continue
    return identities


def _live_observed_process_identities(
    observed: set[tuple[int, int]],
) -> list[dict[str, int]]:
    live: list[dict[str, int]] = []
    for pid, started in sorted(observed):
        try:
            current = _process_start_time_ticks(pid)
        except FileNotFoundError:
            continue
        if current == started:
            live.append({"pid": pid, "startTimeTicks": started})
    return live


def _evaluation_cleanup_state(
    cgroup_root: Path, observed: set[tuple[int, int]],
) -> dict[str, Any]:
    pids_current, members = _cgroup_final_membership(cgroup_root)
    survivors = _live_observed_process_identities(observed)
    return {
        "observedIdentitySurvivors": survivors,
        "cgroupPidsCurrent": pids_current,
        "cgroupMemberPids": members,
        "complete": not survivors and pids_current == 0 and not members,
    }


def _cgroup_sample(cgroup_root: Path, main_pid: int) -> dict[str, Any]:
    memory_events = _keyed_integer_file(cgroup_root / "memory.events")
    cpu = _keyed_integer_file(cgroup_root / "cpu.stat")
    for name in ("low", "high", "max", "oom", "oom_kill", "oom_group_kill"):
        require(name in memory_events, f"missing cgroup memory event: {name}")
    for name in ("usage_usec", "user_usec", "system_usec"):
        require(name in cpu, f"missing cgroup CPU counter: {name}")
    return {
        "observedAt": utc_now(),
        "monotonicNanos": time.monotonic_ns(),
        "memoryCurrentBytes": _integer_file(cgroup_root / "memory.current"),
        "memoryPeakBytes": _integer_file(cgroup_root / "memory.peak"),
        "memoryEvents": {
            "low": memory_events["low"], "high": memory_events["high"], "max": memory_events["max"],
            "oom": memory_events["oom"], "oomKill": memory_events["oom_kill"],
            "oomGroupKill": memory_events["oom_group_kill"],
        },
        "cpuUsageUsec": cpu["usage_usec"], "cpuUserUsec": cpu["user_usec"],
        "cpuSystemUsec": cpu["system_usec"],
        "pidsCurrent": _integer_file(cgroup_root / "pids.current"),
        "pidTree": _process_tree_records(main_pid),
    }


def _stop_systemd_control_group(unit: str) -> tuple[str, list[str]]:
    errors: list[str] = []
    requested_at = utc_now()
    stopped = _run_probe(["systemctl", "stop", unit], timeout=930, check=False)
    if stopped["exitCode"] != 0:
        errors.append("systemctl stop: " + stopped["stderr"].strip())
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        state = _systemd_properties(unit, ("ActiveState",))["ActiveState"]
        if state not in {"active", "activating", "deactivating"}:
            return requested_at, errors
        time.sleep(0.1)
    killed = _run_probe(["systemctl", "kill", "--kill-whom=all", "--signal=SIGKILL", unit],
                        timeout=30, check=False)
    if killed["exitCode"] != 0:
        errors.append("systemctl kill: " + killed["stderr"].strip())
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        state = _systemd_properties(unit, ("ActiveState",))["ActiveState"]
        if state not in {"active", "activating", "deactivating"}:
            return requested_at, errors
        time.sleep(0.1)
    errors.append("target control group remained active after SIGKILL")
    return requested_at, errors


def _best_effort_evaluation_host_observation(unit: str) -> dict[str, Any]:
    """Capture one supervisor-owned cgroup sample when the monitor itself fails."""
    errors: list[str] = []
    samples: list[dict[str, Any]] = []
    properties: dict[str, str] = {}
    try:
        properties = _systemd_properties(
            unit, ("ActiveState", "ControlGroup", "MainPID", "Result",
                   "ExecMainCode", "ExecMainStatus"))
        control_group = properties.get("ControlGroup", "")
        main_pid = int(properties.get("MainPID", "0") or "0")
        if control_group.startswith("/") and main_pid > 0:
            samples.append(_cgroup_sample(
                Path("/sys/fs/cgroup") / control_group.lstrip("/"), main_pid))
        else:
            errors.append("target cgroup or main PID unavailable")
    except BaseException as observation_error:
        errors.append(repr(observation_error))
    maximum = max((row["memoryPeakBytes"] for row in samples), default=None)
    last = samples[-1]["memoryCurrentBytes"] if samples else None
    events = dict(samples[-1]["memoryEvents"]) if samples else {}
    return {
        "status": "OBSERVED" if samples else "MEASUREMENT_FAILED",
        "pollIntervalSeconds": CGROUP_SAMPLE_INTERVAL_SECONDS,
        "samples": samples,
        "peakBytes": maximum,
        "lastBytes": last,
        "events": events,
        "peakSource": "cgroup-v2" if samples else None,
        "systemdProperties": properties,
        "errors": errors,
    }


def monitor_evaluation_unit(*, phase: str, target_unit: str, monitor_unit: str) -> dict[str, Any]:
    """Observe the evaluator from a distinct transient unit and return terminal evidence."""
    require(sys.platform.startswith("linux"), "evaluation monitor requires Linux")
    require(phase in {"calibrate-select", "confirmation"}, "invalid monitor phase")
    expected_target = ("feelm-b1-r4-server5c20g-t28800-selection.service"
                       if phase == "calibrate-select"
                       else "feelm-b1-r4-server5c20g-t28800-confirmation.service")
    expected_monitor = expected_target[:-8] + "-monitor.service"
    require(target_unit == expected_target and monitor_unit == expected_monitor,
            "evaluation monitor unit identity drift")
    own_cgroup = Path("/proc/self/cgroup").read_text(encoding="ascii").splitlines()
    require(len(own_cgroup) == 1 and own_cgroup[0].startswith("0::/")
            and own_cgroup[0].endswith("/" + monitor_unit), "monitor is not in its distinct cgroup")
    deadline = time.monotonic() + 60.0
    properties: dict[str, str] | None = None
    while time.monotonic() < deadline:
        observed = _systemd_properties(target_unit, ("ActiveState", "ControlGroup", "MainPID"))
        if observed["ActiveState"] in {"active", "activating"} and int(observed["MainPID"] or "0") > 0:
            properties = observed
            break
        time.sleep(0.1)
    require(properties is not None, "target evaluator unit did not become active")
    main_pid = int(properties["MainPID"])
    control_group = properties["ControlGroup"]
    require(control_group.startswith("/") and control_group.endswith("/" + target_unit),
            "target evaluator cgroup identity drift")
    cgroup_root = Path("/sys/fs/cgroup") / control_group.lstrip("/")
    samples: list[dict[str, Any]] = []
    initial_tree = _process_tree_records(main_pid)
    require(any(row["pid"] == main_pid for row in initial_tree), "target main PID unavailable")
    observed_identities: set[tuple[int, int]] = set()
    _remember_process_identities(observed_identities, initial_tree)
    observed_identities.update(_cgroup_process_identities(cgroup_root))
    timed_out_by_monitor = False
    measurement_failed = False
    stop_requested_at: str | None = None
    cleanup_errors: list[str] = []
    runtime_deadline = time.monotonic() + 14_400.0
    while True:
        try:
            sample = _cgroup_sample(cgroup_root, main_pid)
            samples.append(sample)
            _remember_process_identities(observed_identities, sample["pidTree"])
            observed_identities.update(_cgroup_process_identities(cgroup_root))
        except FileNotFoundError:
            if not samples:
                measurement_failed = True
                cleanup_errors.append("target cgroup disappeared before first sample")
            break
        except BaseException as sample_error:
            measurement_failed = True
            cleanup_errors.append("cgroup sample: " + repr(sample_error))
            stop_requested_at, stop_errors = _stop_systemd_control_group(target_unit)
            cleanup_errors.extend(stop_errors)
            break
        last_events = samples[-1]["memoryEvents"]
        if last_events["oomKill"] > 0 or last_events["oomGroupKill"] > 0:
            stop_requested_at, stop_errors = _stop_systemd_control_group(target_unit)
            cleanup_errors.extend(stop_errors)
            break
        if time.monotonic() >= runtime_deadline:
            timed_out_by_monitor = True
            stop_requested_at, stop_errors = _stop_systemd_control_group(target_unit)
            cleanup_errors.extend(stop_errors)
            break
        state = _systemd_properties(target_unit, ("ActiveState",))["ActiveState"]
        if state not in {"active", "activating", "deactivating"}:
            break
        time.sleep(CGROUP_SAMPLE_INTERVAL_SECONDS)
    terminal = _systemd_properties(
        target_unit, ("ActiveState", "Result", "ExecMainCode", "ExecMainStatus"))
    exit_code = int(terminal["ExecMainStatus"] or "0")
    timed_out = terminal["Result"] == "timeout" or timed_out_by_monitor
    last = samples[-1] if samples else None
    final_events = (dict(last["memoryEvents"]) if last is not None else
                    {"low": 0, "high": 0, "max": 0, "oom": 0, "oomKill": 0,
                     "oomGroupKill": 0})
    oom_killed = final_events["oomKill"] > 0 or final_events["oomGroupKill"] > 0
    signal_value: str | None = None
    if terminal["ExecMainCode"] == "2":
        signal_value = str(exit_code)
    inactive = terminal["ActiveState"] in {"inactive", "failed"}
    cleanup_state: dict[str, Any] = {
        "observedIdentitySurvivors": [], "cgroupPidsCurrent": -1,
        "cgroupMemberPids": [], "complete": False,
    }
    cleanup_deadline = time.monotonic() + 30.0
    stop_attempted = stop_requested_at is not None
    while time.monotonic() < cleanup_deadline:
        try:
            cleanup_state = _evaluation_cleanup_state(cgroup_root, observed_identities)
        except BaseException as cleanup_probe_error:
            measurement_failed = True
            cleanup_errors.append("final cgroup/process census: " + repr(cleanup_probe_error))
            break
        if cleanup_state["complete"] is True:
            break
        if not stop_attempted:
            stop_attempted = True
            stop_requested_at, stop_errors = _stop_systemd_control_group(target_unit)
            cleanup_errors.extend(stop_errors)
        time.sleep(0.1)
    cleanup_verified = cleanup_state["complete"] is True
    if not cleanup_verified:
        cleanup_errors.append("final process/cgroup cleanup incomplete: " + json.dumps(
            cleanup_state, sort_keys=True, separators=(",", ":"), allow_nan=False))
    collected = False
    if cleanup_verified:
        collect_deadline = time.monotonic() + 30.0
        while time.monotonic() < collect_deadline:
            load = _run_probe(["systemctl", "show", target_unit, "-p", "LoadState", "--value"], check=False)
            if load["exitCode"] == 0 and load["stdout"].strip() in {"", "not-found"}:
                collected = True
                break
            time.sleep(0.1)
    pid_tree_empty = cleanup_verified
    complete = inactive and collected and pid_tree_empty and not cleanup_errors
    status = ("MEASUREMENT_FAILED" if measurement_failed else
              "FAILED" if timed_out or oom_killed or exit_code != 0 else
              "PASS" if complete else "FAILED")
    result = {
        "schemaVersion": "feelm-service-v1-b1-r4-evaluator-resource/1", "status": status,
        "runId": RUN_ID, "profileId": PROFILE_ID, "phase": phase,
        "unitName": target_unit, "monitorUnitName": monitor_unit, "controlGroup": control_group,
        "mainPid": main_pid, "initialPidTree": initial_tree,
        "limits": {"cpuQuotaPercent": 500, "memoryMaxBytes": 21_474_836_480,
                   "memorySwapMaxBytes": 0, "tasksMax": 4096, "killMode": "control-group",
                   "runtimeMaxSeconds": 14_400, "timeoutStopSeconds": 900},
        "sampleIntervalSeconds": 2.0, "samples": samples,
        "peaks": {"memoryPeakBytes": max((row["memoryPeakBytes"] for row in samples), default=0),
                  "maxPidsCurrent": max((row["pidsCurrent"] for row in samples), default=0),
                  "lastSampleAt": last["observedAt"] if last is not None else None},
        "finalEvents": final_events,
        "termination": {"timedOut": timed_out, "oomKilled": oom_killed, "exitCode": exit_code,
                        "signal": signal_value, "stopRequestedAt": stop_requested_at,
                         "finalSampleAt": last["observedAt"] if last is not None else None},
        "cleanup": {"targetInactive": inactive, "monitorExitCode": 0, "unitCollected": collected,
                    "pidTreeEmpty": pid_tree_empty, "errors": cleanup_errors,
                    "complete": complete},
    }
    return result


def _evaluation_failure_lock(
    phase: str, control_state: Mapping[str, Any], dependencies: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    canonical_to_snake = {"scoreManifest": "score_manifest", "scoreReview": "score_review",
        "deliveryManifest": "delivery_manifest", "deliveryReview": "delivery_review",
        "serverReceiptManifest": "server_receipt_manifest",
        "serverReceiptReview": "server_receipt_review", "hostRuntimeLock": "host_runtime_lock",
        "selectionManifest": "selection_manifest", "selectionReview": "selection_review"}
    source_records = [dict(record) for record in dependencies]
    raw_records = control_state.get("records", {})
    controls: dict[str, dict[str, Any]] = {}
    if isinstance(raw_records, Mapping):
        controls = {canonical_to_snake[name]: dict(record)
                    for name, record in raw_records.items()
                    if name in canonical_to_snake and isinstance(record, Mapping)}
    elif isinstance(raw_records, Sequence):
        suffixes = {"score_manifest": "-score/manifest.json",
                    "score_review": "-score-result-review.json",
                    "delivery_manifest": "-delivery-manifest.json",
                    "delivery_review": "-delivery-manifest-result-review.json",
                    "server_receipt_manifest": "-server-receipt/manifest.json",
                    "server_receipt_review": "-server-receipt-result-review.json",
                    "host_runtime_lock": "service-v1-b1-r4-host-runtime-lock.json",
                    "selection_manifest": "-selection/manifest.json",
                    "selection_review": "-selection-result-review.json"}
        for record in raw_records:
            if not isinstance(record, Mapping):
                continue
            matches = [name for name, suffix in suffixes.items()
                       if str(record.get("path", "")).replace("\\", "/").endswith(suffix)]
            require(len(matches) <= 1, "ambiguous evaluation failure control pin")
            if matches:
                controls[matches[0]] = dict(record)
    return {"schemaVersion": "feelm-service-v1-b1-r4-evaluation-input-lock/1",
            "phase": ("SELECTION" if phase == "calibrate-select" else "CONFIRMATION"),
            "runId": RUN_ID, "profileId": PROFILE_ID, "files": {}, "controls": controls,
            "dependencies": source_records,
            "evaluationSourceSetSha256": canonical_record_set_sha256(source_records)}


def _evaluation_failure_context(
    paths: ExecutionPaths, phase: str, control_state: Mapping[str, Any],
    dependencies: Sequence[Mapping[str, Any]], target_command: Sequence[str] | None,
    monitor_command: Sequence[str] | None,
) -> dict[str, Any]:
    profile, profile_pin = execution_profile(paths)
    outer = pin_file(paths.outer_runner, "execution/run_service_v1_b1_gbt_r4.py")
    sequence = []
    if monitor_command:
        sequence.append({"kind": "EVALUATOR_MONITOR", "command": list(monitor_command)})
    if target_command:
        sequence.append({"kind": "EVALUATOR_TARGET", "command": list(target_command)})
    command = {"sequence": sequence}
    return {"phase": phase,
            "phaseInputLock": _evaluation_failure_lock(phase, control_state, dependencies),
            "recoveryReference": _failure_ancestor_closure(paths),
            "executionProfile": phase_execution_profile(
                phase, profile, profile_pin, outer,
                canonical_record_set_sha256(execution_records(paths))),
            "commandDocument": command,
            "runLog": {"bytes": 0, "sha256": sha256_bytes(b""), "content": ""},
            "completedStages": [], "failureStage": "PRE_CONTAINER", "failureKind": "CONTRACT"}


def _run_evaluation_supervised_impl(
    paths: ExecutionPaths,
    *,
    phase: str,
    controls: Any,
    expected_sha256: Mapping[str, str],
    dynamic_host_gate: Mapping[str, Any],
    evaluator_request: Mapping[str, Any],
    evaluator_argv: Sequence[str] | None = None,
    monitor_argv: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Run evaluator and an independent cgroup monitor under separate units."""
    normalized = "calibrate-select" if phase in {"selection", "calibrate-select"} else phase
    require(normalized in {"calibrate-select", "confirmation"}, "invalid supervised evaluation phase")
    required_request = {"controls", "expectedSha256", "stagingPath", "hostGate",
                        "dependencyRecords", "selectionReference"}
    require(set(evaluator_request) == required_request, "evaluator request field set drift")
    gate_state = verify_server_evaluation_gate(
        paths, phase=normalized, controls=controls, expected_sha256=expected_sha256,
        dynamic_host_gate=dynamic_host_gate,
    )
    require(gate_state["status"] == "PASS", "server evaluation gate failed")
    require(evaluator_request["hostGate"] == dynamic_host_gate, "evaluator host gate request drift")
    request_controls, request_expected = _evaluation_control_maps(evaluator_request["controls"],
        evaluator_request["expectedSha256"], confirmation=False)
    actual_controls, actual_expected = _evaluation_control_maps(
        controls, expected_sha256, confirmation=normalized == "confirmation")
    require(request_controls == {name: actual_controls[name] for name in request_controls}
            and request_expected == {name: actual_expected[name] for name in request_expected},
            "evaluator control request drift")
    if normalized == "confirmation":
        selection_reference = evaluator_request["selectionReference"]
        require(isinstance(selection_reference, Mapping)
                and set(selection_reference) == {"manifest", "review"},
                "confirmation selection reference is required")
        for label, canonical in (("manifest", actual_controls["selectionManifest"]),
                                 ("review", actual_controls["selectionReview"])):
            record = selection_reference[label]
            require(isinstance(record, Mapping) and set(record) == {"path", "bytes", "sha256"},
                    f"selection {label} reference shape drift")
            require(Path(str(record["path"])).absolute() == canonical.absolute(),
                    f"selection {label} reference path drift")
            require(record["sha256"] == actual_expected["selection" + label.title()],
                    f"selection {label} reference SHA drift")
            require(_absolute_record(canonical) == dict(record),
                    f"selection {label} reference bytes drift")
    else:
        require(evaluator_request["selectionReference"] is None,
                "selection phase cannot consume selection reference")
    require(evaluator_request["dependencyRecords"] in ([], gate_state["dependencyRecords"]),
            "evaluator dependency request drift")
    profile, _ = execution_profile(paths)
    evaluation_action = "calibrate-select" if normalized == "calibrate-select" else "confirm"
    evaluator_path = paths.standalone / "scripts/evaluate_service_v1_b1_r4.py"
    supervisor = profile["evaluationSupervisor"]
    target_unit = (supervisor["selectionUnit"] if normalized == "calibrate-select"
                   else supervisor["confirmationUnit"])
    monitor_unit = (supervisor["selectionMonitorUnit"] if normalized == "calibrate-select"
                    else supervisor["confirmationMonitorUnit"])
    expected_evaluator_argv = sealed_python_argv(paths, evaluator_path, [evaluation_action])
    expected_monitor_argv = sealed_python_argv(
        paths, paths.outer_runner,
        ["--standalone-root", str(paths.standalone), "--team-repo", str(paths.team),
         "monitor-evaluation", "--phase", normalized, "--target-unit", target_unit,
         "--monitor-unit", monitor_unit],
    )
    actual_evaluator_argv = list(evaluator_argv) if evaluator_argv is not None else expected_evaluator_argv
    actual_monitor_argv = list(monitor_argv) if monitor_argv is not None else expected_monitor_argv
    _verify_sealed_python_argv(paths, actual_evaluator_argv, evaluator_path, [evaluation_action])
    _verify_sealed_python_argv(
        paths, actual_monitor_argv, paths.outer_runner,
        ["--standalone-root", str(paths.standalone), "--team-repo", str(paths.team),
         "monitor-evaluation", "--phase", normalized, "--target-unit", target_unit,
         "--monitor-unit", monitor_unit],
    )
    target_command, monitor_command = _evaluation_systemd_commands(
        profile, normalized, actual_evaluator_argv, actual_monitor_argv)
    evaluation_control_state = _verify_evaluation_control_gate(
        paths, phase=normalized, controls=controls, expected_sha256=expected_sha256)
    absolute_records = _pin_absolute_records(gate_state["dependencyRecords"])
    failure_context = _evaluation_failure_context(
        paths, normalized, evaluation_control_state, absolute_records,
        target_command=target_command, monitor_command=monitor_command)
    snapshot = dependency_snapshot_absolute(absolute_records)
    requested_stage = evaluator_request["stagingPath"]
    lease, staging = acquire_phase_publication(paths, normalized, snapshot)
    phase_started_at = utc_now()
    owned_stage: OwnedPath | None = None
    monitor: subprocess.Popen[str] | None = None
    target: subprocess.Popen[str] | None = None
    target_stdout = ""
    target_stderr = ""
    monitor_stdout = ""
    monitor_stderr = ""
    resource: dict[str, Any] = {}
    try:
        require(requested_stage in {None, str(staging)}, "evaluator staging path is not lease-owned")
        request = dict(evaluator_request)
        request["stagingPath"] = str(staging)
        request["dependencyRecords"] = absolute_records
        require(not os.path.lexists(staging), "lease staging path already exists")
        staging.mkdir(mode=0o700)
        owned_stage = capture_owned_path(staging)
        failure_context["failureStage"] = "CONTAINER_CREATE"
        failure_context["failureKind"] = "PROCESS"
        monitor = subprocess.Popen(
            monitor_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        _wait_systemd_active(monitor_unit, monitor)
        encoded_request = json.dumps(request, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
        target = subprocess.Popen(
            target_command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        require(target.stdin is not None, "evaluator stdin pipe unavailable")
        target.stdin.write(encoded_request)
        target.stdin.flush()
        target.stdin.close()
        target.stdin = None
        failure_context["failureStage"] = "WORKER"
        failure_context["failureKind"] = "PROCESS"
        supervisor_deadline = time.monotonic() + 15_360.0
        supervisor_stop_requested = False
        while target.poll() is None or monitor.poll() is None:
            if monitor.poll() is not None and target.poll() is None and not supervisor_stop_requested:
                _stop_systemd_control_group(target_unit)
                supervisor_stop_requested = True
            if time.monotonic() >= supervisor_deadline and target.poll() is None and not supervisor_stop_requested:
                _stop_systemd_control_group(target_unit)
                supervisor_stop_requested = True
            if time.monotonic() >= supervisor_deadline + 930.0:
                raise TimeoutError("evaluation supervisor cleanup exceeded timeout budget")
            time.sleep(0.1)
        target_stdout, target_stderr = target.communicate(timeout=30)
        monitor_stdout, monitor_stderr = monitor.communicate(timeout=30)
        require(monitor.returncode == 0, f"evaluation monitor failed: {monitor_stderr}")
        resource = _terminal_json(monitor_stdout, "FEELM_R4_EVALUATOR_RESOURCE=", "monitor")
        termination = resource.get("termination", {})
        if termination.get("timedOut") is True:
            failure_context["failureStage"], failure_context["failureKind"] = "TIMEOUT", "TIMEOUT"
        elif termination.get("oomKilled") is True:
            failure_context["failureStage"], failure_context["failureKind"] = "WORKER", "OOM"
        elif target.returncode != 0:
            failure_context["failureStage"], failure_context["failureKind"] = "WORKER", "PROCESS"
        require(target.returncode == 0, f"supervised evaluator failed: {target_stderr}")
        require(resource.get("status") == "PASS"
                and resource.get("cleanup", {}).get("complete") is True,
                "evaluation resource or cleanup gate failed")
        failure_context["failureStage"] = "OUTPUT_VALIDATION"
        failure_context["failureKind"] = "AUDIT"
        computation = _terminal_json(target_stdout, "FEELM_R4_EVALUATION_RESULT=", "evaluator")
        module_name = "evaluate_service_v1_b1_r4"
        if module_name in sys.modules:
            evaluator = sys.modules[module_name]
            require(Path(str(getattr(evaluator, "__file__", ""))).resolve() == evaluator_path.resolve(),
                    "r4 evaluator module origin drift")
        else:
            module_spec = importlib.util.spec_from_file_location(module_name, evaluator_path)
            require(module_spec is not None and module_spec.loader is not None,
                    "r4 evaluator module is unavailable")
            evaluator = importlib.util.module_from_spec(module_spec)
            sys.modules[module_name] = evaluator
            module_spec.loader.exec_module(evaluator)
        finalize = getattr(evaluator, "finalize_phase", None)
        require(callable(finalize), "r4 evaluator finalize_phase is unavailable")
        evaluator_phase = "selection" if normalized == "calibrate-select" else "confirmation"
        failure_context["failureStage"] = "PUBLICATION"
        failure_context["failureKind"] = "IO"
        manifest = finalize(staging, paths.bundle(normalized), phase=evaluator_phase,
                            computation=computation, evaluator_resource=resource, lease=lease,
                            rehash_callback=snapshot.rehash)
        return {"phase": normalized, "manifest": manifest, "targetCommand": target_command,
                "monitorCommand": monitor_command, "targetStdout": target_stdout,
                "targetStderr": target_stderr, "monitorStdout": monitor_stdout,
                "monitorStderr": monitor_stderr}
    except BaseException as error:
        cleanup_errors: list[str] = []
        fallback_observation: dict[str, Any] | None = None
        fallback_stop_requested_at: str | None = None
        if not resource:
            fallback_observation = _best_effort_evaluation_host_observation(target_unit)
        if target is not None and target.poll() is None:
            try:
                fallback_stop_requested_at, stop_errors = _stop_systemd_control_group(target_unit)
                cleanup_errors.extend(stop_errors)
            except BaseException as stop_error:
                cleanup_errors.append(repr(stop_error))
        for process, label in ((target, "target"), (monitor, "monitor")):
            if process is None or process.poll() is not None:
                continue
            try:
                process.kill()
                process.wait(timeout=30)
            except BaseException as process_error:
                cleanup_errors.append(label + ": " + repr(process_error))
        if target is not None and not target_stdout:
            try:
                target_stdout, target_stderr = target.communicate(timeout=1)
            except BaseException as output_error:
                cleanup_errors.append("target output: " + repr(output_error))
        if monitor is not None and not monitor_stdout:
            try:
                monitor_stdout, monitor_stderr = monitor.communicate(timeout=1)
            except BaseException as output_error:
                cleanup_errors.append("monitor output: " + repr(output_error))
        if not resource and monitor_stdout:
            try:
                resource = _terminal_json(
                    monitor_stdout, "FEELM_R4_EVALUATOR_RESOURCE=", "monitor")
            except BaseException as resource_error:
                cleanup_errors.append("monitor terminal evidence: " + repr(resource_error))
        if fallback_observation is not None:
            observation_log = json.dumps(
                fallback_observation, sort_keys=True, separators=(",", ":"), allow_nan=False)
            monitor_stderr = "\n".join(
                value for value in (monitor_stderr, "supervisorFallback=" + observation_log) if value)
        evidence = {"evaluationResource": resource, "stdout": target_stdout,
                    "stderr": "\n".join(value for value in (target_stderr, monitor_stderr) if value),
                    "timedOut": bool(resource.get("termination", {}).get("timedOut", False)
                                     or isinstance(error, TimeoutError)),
                    "stopRequestedAt": (resource.get("termination", {}).get("stopRequestedAt")
                                        or fallback_stop_requested_at)}
        if fallback_observation is not None:
            evidence["hostCgroupObservation"] = fallback_observation
        try:
            setattr(error, "evidence", evidence)
        except BaseException:
            error = ContainerExecutionError(str(error), evidence)
        if owned_stage is not None:
            try:
                cleanup_owned_path(owned_stage)
            except BaseException as cleanup_error:
                cleanup_errors.append(repr(cleanup_error))
        failure_material = capture_failure_material(
            staging, failure_context, error, stage_owned=False)
        failure_material["runLog"] = {
            "bytes": len(target_stdout.encode("utf-8", errors="replace")),
            "sha256": sha256_bytes(target_stdout.encode("utf-8", errors="replace")),
            "content": target_stdout}
        if cleanup_errors:
            failure_material["failureStage"], failure_material["failureKind"] = "CLEANUP", "CLEANUP"
        write_failure(
            paths, normalized, error, not cleanup_errors, failure_material, [staging],
            lease=lease, dependencies=snapshot,
            control_state={"records": list(evaluation_control_state["records"].values()),
                           "payloads": evaluation_control_state["payloads"]},
            host_gate=dynamic_host_gate, started_at=phase_started_at)
        raise


def _best_effort_evaluation_control_state(
    paths: ExecutionPaths, phase: str,
) -> dict[str, Any]:
    canonical = {
        "scoreManifest": paths.bundle("score") / "manifest.json",
        "scoreReview": paths.review("score"),
        "deliveryManifest": paths.delivery_manifest,
        "deliveryReview": paths.delivery_review,
        "serverReceiptManifest": paths.server_receipt / "manifest.json",
        "serverReceiptReview": paths.server_receipt_review,
        "hostRuntimeLock": paths.host_runtime_lock,
    }
    if phase == "confirmation":
        canonical.update({"selectionManifest": paths.bundle("selection") / "manifest.json",
                          "selectionReview": paths.review("selection")})
    records: list[dict[str, Any]] = []
    payloads: dict[str, dict[str, Any]] = {}
    for name, path in canonical.items():
        try:
            records.append(_absolute_record(path))
            value = load_json(path, allow_null=True, require_lf=True)
            payloads[name] = value
        except BaseException:
            continue
    return {"records": records, "payloads": payloads}


def publish_evaluation_pre_execution_failure(
    paths: ExecutionPaths, phase: str, error: BaseException, *,
    dynamic_host_gate: Mapping[str, Any] | None, started_at: str,
) -> Path:
    require(phase in {"calibrate-select", "confirmation"},
            "unsupported evaluation failure phase")
    expected = expected_namespace_before(phase)
    require(namespace_children(paths) == sorted(expected),
            "evaluation failure requires a fresh exact namespace")
    control_state = _best_effort_evaluation_control_state(paths, phase)
    control_paths = [Path(str(record["path"])) for record in control_state["records"]]
    context_records = [dict(record) for record in control_state["records"]]
    failure_context = _evaluation_failure_context(
        paths, phase, control_state, context_records,
        target_command=None, monitor_command=None)
    stage_name, kind = _pre_execution_failure_stage(error)
    failure_context["failureStage"] = stage_name
    failure_context["failureKind"] = kind
    dependencies = failure_state_snapshot(
        _failure_dependency_paths(paths, phase, control_paths))
    lease, staging = acquire_phase_publication(paths, phase, dependencies)
    write_failure(
        paths, phase, error, True, failure_context, [staging], lease=lease,
        dependencies=dependencies, control_state=control_state,
        host_gate=dynamic_host_gate, started_at=started_at)
    return paths.failure(phase)


def run_evaluation_supervised(
    paths: ExecutionPaths,
    *,
    phase: str,
    controls: Any,
    expected_sha256: Mapping[str, str],
    dynamic_host_gate: Mapping[str, Any],
    evaluator_request: Mapping[str, Any],
    evaluator_argv: Sequence[str] | None = None,
    monitor_argv: Sequence[str] | None = None,
) -> dict[str, Any]:
    normalized = "calibrate-select" if phase in {"selection", "calibrate-select"} else phase
    started_at = utc_now()
    try:
        return _run_evaluation_supervised_impl(
            paths, phase=phase, controls=controls, expected_sha256=expected_sha256,
            dynamic_host_gate=dynamic_host_gate, evaluator_request=evaluator_request,
            evaluator_argv=evaluator_argv, monitor_argv=monitor_argv)
    except BaseException as error:
        if (normalized in {"calibrate-select", "confirmation"}
                and not isinstance(error, PhasePublicationAcquisitionError)
                and namespace_children(paths) == sorted(expected_namespace_before(normalized))):
            publish_evaluation_pre_execution_failure(
                paths, normalized, error, dynamic_host_gate=dynamic_host_gate,
                started_at=started_at)
        raise


@dataclass(frozen=True)
class AbsoluteDependencySnapshot:
    records: tuple[dict[str, Any], ...]
    fingerprint: str

    def rehash(self) -> str:
        return canonical_record_set_sha256(_pin_absolute_records(self.records))


def dependency_snapshot_absolute(records: Sequence[Mapping[str, Any]]) -> AbsoluteDependencySnapshot:
    copied = tuple(dict(record) for record in records)
    fingerprint = canonical_record_set_sha256(copied)
    snapshot = AbsoluteDependencySnapshot(copied, fingerprint)
    require(snapshot.rehash() == fingerprint, "absolute dependency acquisition drift")
    return snapshot


def lowercase_sha256(value: str) -> str:
    require(re.fullmatch(r"[0-9a-f]{64}", value) is not None,
            "expected SHA-256 must be lowercase 64-hex")
    return value


def _add_server_control_arguments(command: argparse.ArgumentParser) -> None:
    for stem in ("server-receipt-manifest", "server-receipt-review", "host-runtime-lock",
                 "delivery-manifest", "delivery-review"):
        command.add_argument("--" + stem, type=Path, required=True)
        command.add_argument("--expected-" + stem + "-sha256", type=lowercase_sha256, required=True)


def _server_controls_from_args(args: argparse.Namespace) -> tuple[ServerPhaseControls, dict[str, str]]:
    controls = ServerPhaseControls(
        delivery_manifest=args.delivery_manifest,
        delivery_review=args.delivery_review,
        server_receipt_manifest=args.server_receipt_manifest,
        server_receipt_review=args.server_receipt_review,
        host_runtime_lock=args.host_runtime_lock,
    )
    expected = {
        "deliveryManifest": args.expected_delivery_manifest_sha256,
        "deliveryReview": args.expected_delivery_review_sha256,
        "serverReceiptManifest": args.expected_server_receipt_manifest_sha256,
        "serverReceiptReview": args.expected_server_receipt_review_sha256,
        "hostRuntimeLock": args.expected_host_runtime_lock_sha256,
    }
    return controls, expected


def _add_evaluation_control_arguments(command: argparse.ArgumentParser, *, confirmation: bool) -> None:
    _add_server_control_arguments(command)
    for stem in ("score-manifest", "score-review"):
        command.add_argument("--" + stem, type=Path, required=True)
        command.add_argument("--expected-" + stem + "-sha256", type=lowercase_sha256, required=True)
    if confirmation:
        for stem in ("selection-manifest", "selection-review"):
            command.add_argument("--" + stem, type=Path, required=True)
            command.add_argument("--expected-" + stem + "-sha256", type=lowercase_sha256, required=True)


def _evaluation_cli_controls(
    args: argparse.Namespace, *, confirmation: bool
) -> tuple[dict[str, Path], dict[str, str], dict[str, Any]]:
    names = ["score_manifest", "score_review", "delivery_manifest", "delivery_review",
             "server_receipt_manifest", "server_receipt_review", "host_runtime_lock"]
    controls = {name: Path(getattr(args, name)).absolute() for name in names}
    expected = {name: str(getattr(args, "expected_" + name + "_sha256")) for name in names}
    selection_reference: dict[str, Any] | None = None
    if confirmation:
        selection_manifest = Path(args.selection_manifest).absolute()
        selection_review = Path(args.selection_review).absolute()
        controls.update(selection_manifest=selection_manifest, selection_review=selection_review)
        expected.update(selection_manifest=args.expected_selection_manifest_sha256,
                        selection_review=args.expected_selection_review_sha256)
        manifest_pin = _absolute_record(selection_manifest)
        review_pin = _absolute_record(selection_review)
        require(manifest_pin["sha256"] == args.expected_selection_manifest_sha256
                and review_pin["sha256"] == args.expected_selection_review_sha256,
                "selection reference expected SHA drift")
        selection_reference = {"manifest": manifest_pin, "review": review_pin}
    child_names = names
    request = {
        "controls": {name: str(controls[name]) for name in child_names},
        "expectedSha256": {name: expected[name] for name in child_names},
        "stagingPath": None,
        "hostGate": None,
        "dependencyRecords": [],
        "selectionReference": selection_reference,
    }
    return controls, expected, request


def _run_evaluation_cli_impl(paths: ExecutionPaths, args: argparse.Namespace) -> dict[str, Any]:
    phase = args.action
    confirmation = phase == "confirmation"
    controls, expected, request = _evaluation_cli_controls(args, confirmation=confirmation)
    base_controls = {name: controls[name] for name in (
        "score_manifest", "score_review", "delivery_manifest", "delivery_review",
        "server_receipt_manifest", "server_receipt_review", "host_runtime_lock")}
    base_expected = {name: expected[name] for name in base_controls}
    # This is the public barrier.  It opens only the seven public controls; no
    # role, label, context, catalog, rating or factor path is resolved here.
    base_state = _verify_evaluation_control_gate(
        paths, phase=phase, controls=controls, expected_sha256=expected)
    scratch_root = Path(str(base_state["payloads"]["serverReceiptManifest"]["actualRoots"]["scratchRoot"]))
    host_gate = collect_dynamic_host_gate(
        paths, phase=phase, control_state={"records": list(base_state["records"].values()),
                                          "payloads": base_state["payloads"]},
        scratch_root=scratch_root)
    request["hostGate"] = host_gate
    # Child request stays exactly seven controls.  Confirmation's two prior
    # selection pins travel only in selectionReference.
    require(set(request["controls"]) == set(base_controls)
            and set(request["expectedSha256"]) == set(base_expected),
            "evaluation child control JSON drift")
    return run_evaluation_supervised(
        paths, phase=phase, controls=controls, expected_sha256=expected,
        dynamic_host_gate=host_gate, evaluator_request=request)

def run_evaluation_cli(paths: ExecutionPaths, args: argparse.Namespace) -> dict[str, Any]:
    phase = str(args.action)
    started_at = utc_now()
    try:
        return _run_evaluation_cli_impl(paths, args)
    except BaseException as error:
        if (phase in {"calibrate-select", "confirmation"}
                and not isinstance(error, PhasePublicationAcquisitionError)
                and namespace_children(paths) == sorted(expected_namespace_before(phase))):
            publish_evaluation_pre_execution_failure(
                paths, phase, error, dynamic_host_gate=None, started_at=started_at)
        raise


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--standalone-root", type=Path, required=True)
    result.add_argument("--team-repo", type=Path, required=True)
    actions = result.add_subparsers(dest="action", required=True)

    preflight_command = actions.add_parser("preflight")
    _add_server_control_arguments(preflight_command)

    fit_command = actions.add_parser("fit")
    _add_server_control_arguments(fit_command)
    fit_command.add_argument("--preflight-manifest", type=Path, required=True)
    fit_command.add_argument("--preflight-review", type=Path, required=True)
    fit_command.add_argument("--expected-preflight-manifest-sha256", type=lowercase_sha256, required=True)
    fit_command.add_argument("--expected-preflight-review-sha256", type=lowercase_sha256, required=True)
    fit_command.add_argument("--expected-outer-runner-sha256", type=lowercase_sha256, required=True)
    fit_command.add_argument("--expected-spark-worker-sha256", type=lowercase_sha256, required=True)
    fit_command.add_argument("--expected-model-input-set-sha256", type=lowercase_sha256, required=True)
    fit_command.add_argument("--expected-worker-runtime-set-sha256", type=lowercase_sha256, required=True)
    fit_command.add_argument("--expected-execution-set-sha256", type=lowercase_sha256, required=True)

    score_command = actions.add_parser("score")
    _add_server_control_arguments(score_command)
    score_command.add_argument("--fit-manifest", type=Path, required=True)
    score_command.add_argument("--fit-review", type=Path, required=True)
    score_command.add_argument("--expected-fit-manifest-sha256", type=lowercase_sha256, required=True)
    score_command.add_argument("--expected-fit-review-sha256", type=lowercase_sha256, required=True)
    score_command.add_argument("--expected-fit-model-inventory-sha256", type=lowercase_sha256, required=True)
    score_command.add_argument("--expected-outer-runner-sha256", type=lowercase_sha256, required=True)
    score_command.add_argument("--expected-spark-worker-sha256", type=lowercase_sha256, required=True)
    score_command.add_argument("--expected-execution-set-sha256", type=lowercase_sha256, required=True)

    selection_command = actions.add_parser("calibrate-select")
    _add_evaluation_control_arguments(selection_command, confirmation=False)

    confirmation_command = actions.add_parser("confirmation")
    _add_evaluation_control_arguments(confirmation_command, confirmation=True)

    monitor_command = actions.add_parser("monitor-evaluation")
    monitor_command.add_argument("--phase", choices=("calibrate-select", "confirmation"), required=True)
    monitor_command.add_argument("--target-unit", required=True)
    monitor_command.add_argument("--monitor-unit", required=True)
    return result


def main() -> None:
    args = parser().parse_args()
    paths = ExecutionPaths.from_roots(args.standalone_root, args.team_repo)
    if args.action == "monitor-evaluation":
        resource = monitor_evaluation_unit(
            phase=args.phase, target_unit=args.target_unit, monitor_unit=args.monitor_unit)
        print("FEELM_R4_EVALUATOR_RESOURCE=" + json.dumps(
            resource, ensure_ascii=False, allow_nan=False, sort_keys=True), flush=True)
        return
    server_controls, expected_controls = _server_controls_from_args(args)
    if args.action == "preflight":
        output = preflight(paths, server_controls=server_controls,
                           expected_control_sha256=expected_controls)
    elif args.action == "fit":
        output = fit(
            paths,
            server_controls=server_controls,
            expected_control_sha256=expected_controls,
            preflight_manifest=args.preflight_manifest,
            preflight_review=args.preflight_review,
            expected_preflight_manifest_sha256=args.expected_preflight_manifest_sha256,
            expected_preflight_review_sha256=args.expected_preflight_review_sha256,
            expected_outer_runner_sha256=args.expected_outer_runner_sha256,
            expected_spark_worker_sha256=args.expected_spark_worker_sha256,
            expected_model_input_set_sha256=args.expected_model_input_set_sha256,
            expected_worker_runtime_set_sha256=args.expected_worker_runtime_set_sha256,
            expected_execution_set_sha256=args.expected_execution_set_sha256,
        )
    elif args.action == "score":
        output = score(
            paths,
            server_controls=server_controls,
            expected_control_sha256=expected_controls,
            fit_manifest=args.fit_manifest,
            fit_review=args.fit_review,
            expected_fit_manifest_sha256=args.expected_fit_manifest_sha256,
            expected_fit_review_sha256=args.expected_fit_review_sha256,
            expected_fit_model_inventory_sha256=args.expected_fit_model_inventory_sha256,
            expected_outer_runner_sha256=args.expected_outer_runner_sha256,
            expected_spark_worker_sha256=args.expected_spark_worker_sha256,
            expected_execution_set_sha256=args.expected_execution_set_sha256,
        )
    elif args.action in {"calibrate-select", "confirmation"}:
        result = run_evaluation_cli(paths, args)
        output = paths.bundle(args.action)
        require(result.get("phase") == args.action, "evaluation supervisor result phase drift")
    else:
        raise ValueError(f"unsupported action: {args.action}")
    print(json.dumps({"status": "COMPLETE", "phase": args.action, "output": str(output)}, sort_keys=True))


if __name__ == "__main__":
    main()
