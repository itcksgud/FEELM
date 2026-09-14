"""Host-side, fail-closed r3 recovery runner for service-v1 B1 Spark GBT.

The public actions are deliberately limited to preflight, fit, and score.  Reviews
are written by an independent implementation; this module exposes deterministic
validators for those sibling review files but never grants itself PASS.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
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


SCHEMA_VERSION = "feelm-service-v1-b1-r3-host-runner/1"
RECOVERY_PLAN_SHA256 = "4de13f041532b7249b22f5a2bbfbd7919f79d94e4c25f2ae16fe95104a2b3d9a"
R2_PLAN_SHA256 = "c5b90b0d19374588fc5cb7a1d98cd6510a1c3708df9667131df5eb3826c79d49"
TEAM_COMMIT = "96a4b27d0ce5c0d4e4fe0348b6c01de0b06f6f7e"
IMAGE = "feelm-rec046-spark:local"
IMAGE_ID = "sha256:de611d62db982bb833b0737a7ddd72a7cd49ab1a7dc9128669cdabae39f46df8"
TIMEOUT_SECONDS = 14_400
MAX_MEMORY_BYTES = 12 * 1024**3
SOURCE_ROWS = 4_997_069
LOGICAL_ROWS = 19_988_276
SCORE_ROWS = 93_230
PARTITIONS = 8
R2_RUN_ID = "b1-gbt120-s339-v1-r2"
RUN_ID = "b1-gbt120-s339-v1-r3-local4c12g-t14400"
PROFILE_ID = "local4c12g-t14400-v1"
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


class ContainerExecutionError(RuntimeError):
    def __init__(self, message: str, evidence: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.evidence = dict(evidence or {})


class ContainerCleanupError(ContainerExecutionError):
    pass


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
    require(path.is_file() and not path.is_symlink(), f"missing or linked file: {path}")
    for current in (path.absolute(), *path.absolute().parents):
        require(
            not current.is_symlink() and not (hasattr(current, "is_junction") and current.is_junction()),
            f"link/reparse path forbidden: {path}",
        )
    value: dict[str, Any] = {
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if logical_path is not None:
        value["path"] = logical_path
    return value


def require_unlinked_path(path: Path, root: Path, *, directory: bool = False) -> None:
    lexical = path.absolute()
    lexical_root = root.absolute()
    require(lexical == lexical_root or lexical_root in lexical.parents, f"path escapes expected root: {path}")
    require(lexical.is_dir() if directory else lexical.is_file(), f"missing regular {'directory' if directory else 'file'}: {path}")
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


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"JSON root must be an object: {path}")
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


@dataclass(frozen=True)
class ExecutionPaths:
    standalone: Path
    team: Path

    @classmethod
    def from_roots(cls, standalone: Path, team: Path) -> "ExecutionPaths":
        return cls(standalone=standalone.resolve(), team=team.resolve())

    @property
    def plan(self) -> Path:
        return self.standalone / "docs/recommendation/plans/service-v1-b1-r3-fit-recovery.md"

    @property
    def r2_plan(self) -> Path:
        return self.standalone / "docs/recommendation/plans/service-v1-b1-spark-runner.md"

    @property
    def profile_contract(self) -> Path:
        return self.standalone / "docs/recommendation/plans/service-v1-b1-r3-local4c12g-t14400-profile.json"

    @property
    def outer_runner(self) -> Path:
        return self.standalone / "scripts/run_service_v1_b1_gbt_r3.py"

    @property
    def r2_outer_runner(self) -> Path:
        return self.standalone / "scripts/run_service_v1_b1_gbt.py"

    @property
    def r2_auditor(self) -> Path:
        return self.standalone / "scripts/audit_service_v1_b1_spark_outputs.py"

    @property
    def r3_auditor(self) -> Path:
        return self.standalone / "scripts/audit_service_v1_b1_spark_outputs_r3.py"

    @property
    def spark_worker(self) -> Path:
        return self.standalone / "scripts/service_v1_b1_spark_worker.py"

    @property
    def tests(self) -> Path:
        return self.standalone / "tests/test_service_v1_b1_gbt_runner_r3.py"

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
        return self.r2_fit_failure

    @property
    def r2_preflight(self) -> Path:
        return self.output_parent / (R2_RUN_ID + "-preflight")

    @property
    def r2_preflight_review(self) -> Path:
        return self.output_parent / (R2_RUN_ID + "-preflight-result-review.json")

    @property
    def r2_fit_failure(self) -> Path:
        return self.output_parent / (R2_RUN_ID + "-fit-failure.json")


def verify_plan(paths: ExecutionPaths) -> dict[str, Any]:
    actual = pin_file(paths.plan, "execution/service-v1-b1-r3-fit-recovery.md")
    require(actual["sha256"] == RECOVERY_PLAN_SHA256, f"reviewed recovery plan hash drift: {actual['sha256']}")
    return actual


def verify_r2_plan(paths: ExecutionPaths) -> dict[str, Any]:
    actual = pin_file(paths.r2_plan, "ancestor/service-v1-b1-spark-runner.md")
    require(actual["sha256"] == R2_PLAN_SHA256, f"reviewed r2 plan hash drift: {actual['sha256']}")
    return actual


def execution_profile(paths: ExecutionPaths) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = load_json(paths.profile_contract)
    require(
        set(payload)
        == {
            "schemaVersion", "status", "profileId", "runId", "recoveryOrdinal", "hostClass",
            "singleAttempt", "automaticRetry", "docker", "spark", "timeoutsSeconds",
            "resourceObservation", "modelContract", "authorization",
        },
        "profile top-level field set drift",
    )
    require(payload.get("schemaVersion") == "feelm-service-v1-b1-execution-profile/1", "profile schema drift")
    require(payload.get("profileId") == PROFILE_ID and payload.get("runId") == RUN_ID, "profile identity drift")
    require(
        payload.get("status") == "REVIEWED_LOCAL_BOUNDED_PROBE"
        and payload.get("recoveryOrdinal") == "r3"
        and payload.get("hostClass") == "current-local-docker-desktop",
        "profile review/host meaning drift",
    )
    docker = payload.get("docker")
    spark = payload.get("spark")
    timeouts = payload.get("timeoutsSeconds")
    require(
        isinstance(docker, dict)
        and set(docker) == {"cpus", "memory", "memorySwap", "maxMemoryBytes", "network"}
        and docker.get("cpus") == "4"
        and docker.get("memory") == "12g"
        and docker.get("memorySwap") == "12g"
        and docker.get("maxMemoryBytes") == MAX_MEMORY_BYTES
        and docker.get("network") == "none",
        "profile Docker resource drift",
    )
    require(
        isinstance(spark, dict)
        and set(spark) == {"master", "driverMemory", "shufflePartitions", "adaptiveExecution"}
        and spark.get("master") == "local[4]"
        and spark.get("driverMemory") == "8g"
        and spark.get("shufflePartitions") == PARTITIONS
        and spark.get("adaptiveExecution") is False,
        "profile Spark resource drift",
    )
    require(
        isinstance(timeouts, dict)
        and set(timeouts) == {"preflightDryRun", "preflightFull", "fit", "score"}
        and all(value == TIMEOUT_SECONDS for value in timeouts.values()),
        "profile timeout drift",
    )
    model = payload.get("modelContract")
    require(
        isinstance(model, dict)
        and set(model) == {
            "sourceRows", "logicalRows", "scoreRows", "featureCount", "partitions", "seed", "trees",
            "workerSha256", "modelInputSetSha256", "workerRuntimeSetSha256",
        }
        and model.get("sourceRows") == SOURCE_ROWS
        and model.get("logicalRows") == LOGICAL_ROWS
        and model.get("scoreRows") == SCORE_ROWS
        and model.get("featureCount") == 230
        and model.get("partitions") == PARTITIONS
        and model.get("seed") == 339
        and model.get("trees") == 120
        and model.get("workerSha256") == EXPECTED_WORKER_SHA256
        and model.get("modelInputSetSha256") == MODEL_INPUT_SET_SHA256
        and model.get("workerRuntimeSetSha256") == WORKER_RUNTIME_SET_SHA256,
        "profile model contract drift",
    )
    resource = payload.get("resourceObservation")
    require(
        isinstance(resource, dict)
        and resource
        == {
            "hostCgroupPollIntervalSeconds": CGROUP_SAMPLE_INTERVAL_SECONDS,
            "sampleBeforeTimeoutStop": True,
            "inspectBeforeTimeoutStop": True,
            "unknownPeakBlocksSuccess": True,
        },
        "profile resource-observation policy drift",
    )
    require(
        payload.get("authorization")
        == {
            "implementation": True,
            "publicPreflight": True,
            "fitBeforeNewSiblingReviewPass": False,
            "deployment": False,
        },
        "profile authorization drift",
    )
    require(payload.get("singleAttempt") is True and payload.get("automaticRetry") is False, "profile retry policy drift")
    return payload, pin_file(paths.profile_contract, "execution/local4c12g-t14400-profile.json")


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
        pin_file(paths.outer_runner, "execution/run_service_v1_b1_gbt_r3.py"),
        pin_file(paths.tests, "execution/test_service_v1_b1_gbt_runner_r3.py"),
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


def implementation_pins(paths: ExecutionPaths) -> tuple[dict[str, Any], dict[str, Any], str]:
    outer = pin_file(paths.outer_runner, "execution/run_service_v1_b1_gbt_r3.py")
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
        "4",
        "--memory",
        "12g",
        "--memory-swap",
        "12g",
        *_mount_arguments(mounts),
        IMAGE,
        "/opt/spark/bin/spark-submit",
        "--master",
        "local[4]",
        "--driver-memory",
        "8g",
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
        "if [ -r /sys/fs/cgroup/memory.peak ]; then "
        "printf 'cgroup-v2\\n'; cat /sys/fs/cgroup/memory.peak; cat /sys/fs/cgroup/memory.current; "
        "elif [ -r /sys/fs/cgroup/memory/memory.max_usage_in_bytes ]; then "
        "printf 'cgroup-v1\\n'; cat /sys/fs/cgroup/memory/memory.max_usage_in_bytes; "
        "cat /sys/fs/cgroup/memory/memory.usage_in_bytes; else exit 44; fi"
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
    require(len(lines) == 3 and lines[0] in {"cgroup-v1", "cgroup-v2"}, "invalid cgroup sample")
    peak, current = int(lines[1]), int(lines[2])
    require(0 <= current <= peak, "invalid cgroup current/peak relation")
    return {"observedAt": utc_now(), "source": lines[0], "peakBytes": peak, "currentBytes": current}


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
            "recentErrors": errors,
            "peakBytes": maximum["peakBytes"] if maximum else None,
            "peakSource": maximum["source"] if maximum else None,
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


def recovery_reference(
    paths: ExecutionPaths,
    ancestry: Mapping[str, Any],
    outer: Mapping[str, Any],
    worker: Mapping[str, Any],
    profile_pin: Mapping[str, Any],
    execution_digest: str,
) -> dict[str, Any]:
    training_recipe = verify_expected_pin(paths.training_recipe, "contract/training-recipe.v1.json")
    return {
        "schemaVersion": "feelm-service-v1-b1-r3-recovery-reference/1",
        "status": "R2_ANCESTRY_VERIFIED",
        "runId": RUN_ID,
        "profileId": PROFILE_ID,
        "r2RunId": R2_RUN_ID,
        "r2PreflightManifest": dict(ancestry["r2PreflightManifest"]),
        "r2PreflightReview": dict(ancestry["r2PreflightReview"]),
        "r2PreflightBundleInventory": [dict(record) for record in ancestry["r2PreflightBundleInventory"]],
        "r2PreflightDigests": dict(ancestry["r2PreflightDigests"]),
        "r2FitFailure": dict(ancestry["r2FitFailure"]),
        "r2FitFailureFacts": dict(ancestry["r2FitFailureFacts"]),
        "r2OuterRunner": dict(ancestry["r2OuterRunner"]),
        "r2Plan": dict(ancestry["r2Plan"]),
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
    require(observed == expected, "r3 preflight recovery reference drift")
    lock = audited.get("inputLock")
    manifest = audited.get("manifest")
    require(isinstance(lock, dict) and isinstance(manifest, dict), "preflight recovery inputs missing")
    controls = r2_control_records(ancestry)
    require(records_equal(lock.get("controlReferences", []), controls), "r3 preflight ancestor control drift")
    require(lock.get("controlReferenceSetSha256") == canonical_record_set_sha256(controls), "ancestor control digest drift")
    require(manifest.get("recoveryReferenceSha256") == pin_file(reference_path)["sha256"], "recovery hash drift")
    require(manifest.get("r2FitFailureSha256") == R2_FIT_FAILURE_SHA256, "r2 failure manifest pin drift")

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
        "schemaVersion": "feelm-service-v1-b1-r3-review-contract/1",
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
        "execution/service-v1-b1-r3-fit-recovery.md": paths.plan,
        "execution/local4c12g-t14400-profile.json": paths.profile_contract,
        "execution/run_service_v1_b1_gbt_r3.py": paths.outer_runner,
        "execution/test_service_v1_b1_gbt_runner_r3.py": paths.tests,
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
    ancestry = validate_r2_ancestry(paths)
    for record in r2_control_records(ancestry):
        physical = resolve_logical_path(paths, record["path"])
        if physical.parent.resolve() != paths.r2_preflight.resolve():
            files[record["path"]] = pin_file(physical)
    phases = ["preflight"] if phase == "preflight" else (
        ["preflight", "fit"] if phase == "fit" else ["preflight", "fit", "score"]
    )
    bundles = {logical_path(paths, paths.r2_preflight): relative_inventory(paths.r2_preflight)}
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
        "auditorImplementation": pin_file(paths.r3_auditor),
        "dockerImageId": IMAGE_ID,
        "runId": RUN_ID,
        "profileId": PROFILE_ID,
    }


def validate_review(paths: ExecutionPaths, phase: str, review_path: Path | None = None) -> dict[str, Any]:
    path = (review_path or paths.review(phase)).resolve()
    require(path == paths.review(phase).resolve(), "review path is not the canonical sibling")
    review = load_json(path)
    require(
        set(review)
        == {
            "schemaVersion", "runId", "profileId", "phase", "status", "createdAt", "target",
            "reviewer", "dependencyFingerprint", "checks", "evaluationTargetsRead",
            "modelFitPerformed", "readyForService", "scope",
        },
        f"{phase} review top-level field set drift",
    )
    require(review.get("schemaVersion") == "feelm-service-v1-b1-r3-result-review/1", f"{phase} review schema drift")
    require(review.get("status") == "PASS" and review.get("phase") == phase, f"{phase} review identity/status drift")
    require(
        review.get("runId") == RUN_ID
        and review.get("profileId") == PROFILE_ID
        and review.get("evaluationTargetsRead") is False
        and review.get("modelFitPerformed") is False
        and review.get("readyForService") is False,
        f"{phase} review authority flags drift",
    )
    created_at = review.get("createdAt")
    require(isinstance(created_at, str), f"{phase} review timestamp missing")
    parsed_created_at = dt.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    require(parsed_created_at.tzinfo is not None, f"{phase} review timestamp is not timezone-aware")
    require(
        review.get("scope")
        == "Local immutable bundle integrity and specified numerical parity; no service acceptance or model quality verdict.",
        f"{phase} review scope drift",
    )
    checks = review.get("checks")
    expected_check_keys = {
        "preflight": {"resource", "recovery", "identity"},
        "fit": {"resource", "recovery", "parentChain", "identity", "fit"},
        "score": {"resource", "recovery", "parentChain", "predictions"},
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
    if phase != "preflight":
        require(checks.get("parentChain") == "PASS", f"{phase} review parent chain did not pass")
    if phase in {"preflight", "fit"}:
        require(isinstance(checks.get("identity"), dict), f"{phase} review identity check missing")
    if phase == "fit":
        require(isinstance(checks.get("fit"), dict), "fit review model audit missing")
    if phase == "score":
        require(isinstance(checks.get("predictions"), dict), "score review prediction audit missing")
    require_unlinked_path(paths.r3_auditor, paths.standalone)
    auditor_pin = pin_file(paths.r3_auditor)
    reviewer = review.get("reviewer")
    require(
        isinstance(reviewer, dict)
        and set(reviewer) == {"implementation", "independentFromRunner"}
        and reviewer.get("independentFromRunner") is True
        and reviewer.get("implementation") == auditor_pin,
        f"{phase} review is not pinned to the independent r3 auditor",
    )
    dependency = review.get("dependencyFingerprint")
    require(isinstance(dependency, dict), f"{phase} review dependency fingerprint missing")
    require(
        set(dependency) == {"files", "bundleInventories", "auditorImplementation", "dockerImageId", "runId", "profileId"}
        and dependency.get("auditorImplementation") == auditor_pin
        and dependency.get("dockerImageId") == IMAGE_ID
        and dependency.get("runId") == RUN_ID
        and dependency.get("profileId") == PROFILE_ID,
        f"{phase} review dependency envelope drift",
    )
    require(dependency == expected_review_dependency(paths, phase), f"{phase} review dependency closure drift")
    files = dependency.get("files")
    bundles = dependency.get("bundleInventories")
    require(isinstance(files, dict) and isinstance(bundles, dict), f"{phase} review dependency inventories missing")
    for logical, expected_pin in files.items():
        require(isinstance(expected_pin, dict), f"{phase} review dependency record invalid")
        current = resolve_logical_path(paths, logical)
        require(current.is_file() and not current.is_symlink(), f"{phase} review dependency missing/linked: {logical}")
        require(pin_file(current) == expected_pin, f"{phase} review dependency drift: {logical}")
    for logical, expected_inventory in bundles.items():
        current = resolve_logical_path(paths, logical)
        require(current.is_dir() and not current.is_symlink(), f"{phase} review bundle missing/linked: {logical}")
        require(relative_inventory(current) == expected_inventory, f"{phase} review bundle inventory drift: {logical}")
    expected = review_contract(paths, phase)["target"]
    observed = review.get("target")
    require(isinstance(observed, dict), f"{phase} review target map missing")
    require(set(expected) == set(observed), f"{phase} review target set drift")
    for name, record in expected.items():
        _validate_target_record(observed.get(name), record, name)

    manifest = load_json(paths.bundle(phase) / "manifest.json")
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
    return {
        "schemaVersion": "feelm-service-v1-b1-command/2",
        "runId": RUN_ID,
        "profileId": PROFILE_ID,
        "phase": phase,
        "createdAt": utc_now(),
        "dockerImage": IMAGE,
        "dockerImageId": image_id,
        "timeoutSeconds": TIMEOUT_SECONDS,
        "resources": {
            "dockerCpus": "4",
            "dockerMemory": "12g",
            "dockerMemorySwap": "12g",
            "sparkMaster": "local[4]",
            "sparkDriverMemory": "8g",
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
        "phaseInputLock": "score-input-lock.json" if context.get("phase") == "score" else "input-lock.json",
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


def write_failure(
    paths: ExecutionPaths,
    phase: str,
    error: BaseException,
    cleanup_ok: bool,
    material: Mapping[str, Any],
    transient_paths: Sequence[Path],
) -> None:
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
    profile = material.get("executionProfile")
    command = material.get("commandDocument")
    run_log = material.get("runLog")
    require(isinstance(phase_lock, Mapping), "failure cannot close missing phase input lock")
    require(isinstance(recovery, Mapping), "failure cannot close missing recovery ancestry")
    require(isinstance(profile, Mapping), "failure cannot close missing execution profile")
    require(isinstance(command, Mapping), "failure cannot close missing command")
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
    payload: dict[str, Any] = {
        "schemaVersion": "feelm-service-v1-b1-r3-failure/1",
        "runId": RUN_ID,
        "profileId": PROFILE_ID,
        "phase": phase,
        "status": "FAILED",
        "errorType": type(error).__name__,
        "error": str(error),
        "cleanupComplete": cleanup_ok
        and not any(os.path.lexists(path) for path in transient_paths)
        and not containers
        and container_census_error is None,
        "createdAt": utc_now(),
        "phaseInputLock": embedded_payload(phase_lock),
        "recoveryReference": embedded_payload(recovery),
        "phaseReference": embedded_payload(material["phaseReference"])
        if isinstance(material.get("phaseReference"), Mapping)
        else None,
        "executionProfile": embedded_payload(profile),
        "commandDocument": embedded_payload(command),
        "runLog": dict(run_log),
        "implementation": dict(material.get("implementation", {})),
        "resourceEvidence": effective_resource,
        "phaseResource": dict(material.get("phaseResource", {})),
        "containerRun": effective_container,
        "completedStages": completed_stages,
        "successPathCensus": {
            "bundle": {"path": str(final.absolute()), "lexists": os.path.lexists(final)},
            "review": {"path": str(review.absolute()), "lexists": os.path.lexists(review)},
            "modelNative": {"path": str((final / "model/native").absolute()), "lexists": os.path.lexists(final / "model/native")},
            "failureExistedBeforePublication": False,
            "transientPaths": [
                {"path": str(path.absolute()), "lexistsAfterCleanup": os.path.lexists(path)} for path in transient_paths
            ],
            "matchingContainersAfterCleanup": containers,
            "containerCensusError": container_census_error,
        },
    }
    atomic_write_sibling(failure, payload)

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
        Mount(paths.outer_runner, "/app/run_service_v1_b1_gbt_r3.py"),
        Mount(paths.spark_worker, "/app/service_v1_b1_spark_worker.py"),
        Mount(paths.plan, "/contract/service-v1-b1-r3-fit-recovery.md"),
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


def preflight(paths: ExecutionPaths) -> Path:
    phase = "preflight"
    ensure_phase_clear(paths, phase)
    require(not running_container_names(), "another B1 Spark container exists")
    verify_team_commit(paths)
    image_id = inspect_image_id()
    outer, worker, implementation = implementation_pins(paths)
    profile_payload, profile_pin = execution_profile(paths)
    models = model_input_records(paths)
    runtime = worker_runtime_records(paths, image_id)
    executions = execution_records(paths)
    execution_digest = canonical_record_set_sha256(executions)
    ancestry = validate_r2_ancestry(paths)
    controls = r2_control_records(ancestry)
    phase_lock = make_phase_lock(phase, models, runtime, executions, controls)
    recovery = recovery_reference(paths, ancestry, outer, worker, profile_pin, execution_digest)
    profile_document = phase_execution_profile(phase, profile_payload, profile_pin, outer, execution_digest)

    final = paths.bundle(phase)
    token = uuid.uuid4().hex
    stage = final.with_name(f".{final.name}.tmp-{token}")
    dry_scratch = final.parent / f".{final.name}.dry-scratch-{token}"
    full_scratch = final.parent / f".{final.name}.full-scratch-{token}"
    log_path = stage / "run.log"
    stage_results: list[tuple[str, str, str, ContainerResult]] = []
    cleanup_ok = False
    stage_owned = False
    dry_scratch_owned = False
    full_scratch_owned = False
    dry_name = f"feelm-b1-r3-preflight-dry-{token[:12]}"
    full_name = f"feelm-b1-r3-preflight-full-{token[:12]}"
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
            {"order": 0, "workerAction": "dry-run-first-row-group", "timeoutSeconds": TIMEOUT_SECONDS, "command": dry_command},
            {"order": 1, "workerAction": "preflight", "timeoutSeconds": TIMEOUT_SECONDS, "command": full_command},
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
    }
    try:
        make_stage(final, paths.review(phase), stage)
        stage_owned = True
        dry_scratch.mkdir()
        dry_scratch_owned = True
        full_scratch.mkdir()
        full_scratch_owned = True
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

        dry_started = utc_now()
        dry_result = run_container(
            dry_name,
            dry_command,
            log_path,
            expected_worker_status="DRY_RUN_ONLY_NOT_PUBLISHED",
            timeout_seconds=TIMEOUT_SECONDS,
        )
        dry_completed = utc_now()
        stage_results.append(("dry-run-first-row-group", dry_started, dry_completed, dry_result))
        cleanup_path(dry_scratch)
        require(not os.path.lexists(stage / "partition-identity.json"), "dry-run published preflight identity")

        full_started = utc_now()
        full_result = run_container(
            full_name,
            full_command,
            log_path,
            expected_worker_status="B1_FULL_PREFLIGHT_WORKER_COMPLETE",
            timeout_seconds=TIMEOUT_SECONDS,
        )
        full_completed = utc_now()
        stage_results.append(("preflight", full_started, full_completed, full_result))
        cleanup_path(full_scratch)
        identity = validate_partition_identity(stage / "partition-identity.json")
        dry_runtime = validate_runtime_versions(dry_result.worker)
        full_runtime = validate_runtime_versions(full_result.worker)
        require(dry_runtime == full_runtime, "dry-run/full-preflight runtime drift")
        require(full_result.worker.get("modelFitPerformed") is False, "preflight worker fitted a model")
        require(full_result.worker.get("scorePerformed") is False, "preflight worker scored data")

        resource = _stage_resource(phase, stage_results)
        write_json_exclusive(stage / "resource.json", resource)
        current_ancestry = validate_r2_ancestry(paths)
        current_models = model_input_records(paths)
        current_runtime = worker_runtime_records(paths, inspect_image_id())
        current_executions = execution_records(paths)
        current_controls = r2_control_records(current_ancestry)
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
                "recoveryReferenceSha256": pin_file(stage / "recovery-reference.json")["sha256"],
                "executionProfileSha256": pin_file(stage / "execution-profile.json")["sha256"],
                "dryRunPrecedesFullMaterialization": True,
                "dryRunLogicalRows": dry_result.worker.get("logicalRows"),
                "partitionIdentitySha256": pin_file(stage / "partition-identity.json")["sha256"],
                "identity": {"sourceRows": identity["sourceRows"], "logicalRows": identity["logicalRows"]},
                "resourceStatus": resource["status"],
                "runtimeVersions": full_runtime,
            },
        )
        atomic_publish_directory(stage, final)
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
        owned_paths = [
            path for path, owned in (
                (dry_scratch, dry_scratch_owned),
                (full_scratch, full_scratch_owned),
                (stage, stage_owned),
            ) if owned
        ]
        for path in owned_paths:
            try:
                cleanup_path(path)
            except Exception as cleanup_error:
                cleanup_errors.append(repr(cleanup_error))
        cleanup_ok = not cleanup_errors and not isinstance(error, ContainerCleanupError)
        try:
            write_failure(paths, phase, error, cleanup_ok, failure_material, [dry_scratch, full_scratch, stage])
        except Exception:
            if cleanup_errors:
                raise RuntimeError(f"preflight failed and cleanup was incomplete: {cleanup_errors}") from error
            raise
        raise

def _preflight_control_records(paths: ExecutionPaths) -> list[dict[str, Any]]:
    records = bundle_records(paths, paths.bundle("preflight"))
    records.append(pinned_control(paths, paths.review("preflight")))
    records.extend(r2_control_records(validate_r2_ancestry(paths)))
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


def fit(
    paths: ExecutionPaths,
    *,
    preflight_manifest: Path,
    preflight_review: Path,
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
    outer, worker, implementation = validate_expected_implementation(paths, expected_outer_runner_sha256, expected_spark_worker_sha256)
    profile_payload, profile_pin = execution_profile(paths)
    ancestry = validate_r2_ancestry(paths)
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
    controls = _preflight_control_records(paths)
    phase_lock = make_phase_lock(phase, models, runtime, executions, controls)
    recovery = recovery_reference(paths, ancestry, outer, worker, profile_pin, execution_digest)
    reference = _preflight_reference(audited, controls, outer, worker, profile_pin, phase_lock, recovery)
    profile_document = phase_execution_profile(phase, profile_payload, profile_pin, outer, execution_digest)

    final = paths.bundle(phase)
    token = uuid.uuid4().hex
    stage = final.with_name(f".{final.name}.tmp-{token}")
    scratch = final.parent / f".{final.name}.fit-scratch-{token}"
    log_path = stage / "run.log"
    cleanup_ok = False
    stage_owned = False
    scratch_owned = False
    control_paths = [resolve_logical_path(paths, record["path"]) for record in controls]
    mounts = training_mounts(paths, scratch, stage, validate=False)
    for index, path in enumerate(control_paths):
        mounts.append(Mount(path, f"/control/fit-input/{index:02d}-{path.name}"))
    name = f"feelm-b1-r3-fit-{token[:12]}"
    worker_args = common_training_worker_arguments("fit", include_recipe=True, include_output=True)
    docker_command = spark_container_command(name, mounts, worker_args)
    command = command_document(
        phase,
        image_id,
        [{"order": 0, "workerAction": "fit", "timeoutSeconds": TIMEOUT_SECONDS, "command": docker_command}],
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
    }
    try:
        make_stage(final, paths.review(phase), stage)
        stage_owned = True
        scratch.mkdir()
        scratch_owned = True
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
        started_at = utc_now()
        result = run_container(
            name,
            docker_command,
            log_path,
            expected_worker_status="B1_MODEL_FIT_WORKER_COMPLETE",
            timeout_seconds=TIMEOUT_SECONDS,
        )
        completed_at = utc_now()
        completed = completed_container_evidence(result, "fit")
        completed.update({"startedAt": started_at, "completedAt": completed_at})
        failure_context["completedStages"] = [completed]
        cleanup_path(scratch)
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

        current_ancestry = validate_r2_ancestry(paths)
        current_models = model_input_records(paths)
        current_runtime = worker_runtime_records(paths, inspect_image_id())
        current_executions = execution_records(paths)
        current_controls = _preflight_control_records(paths)
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
                "recoveryReferenceSha256": pin_file(stage / "recovery-reference.json")["sha256"],
                "executionProfileSha256": pin_file(stage / "execution-profile.json")["sha256"],
                "partitionIdentitySha256": pin_file(stage / "partition-identity.json")["sha256"],
                "modelInventorySha256": pin_file(stage / "model-file-inventory.json")["sha256"],
                "modelFileSetSha256": inventory["inventorySha256"],
                "thresholdFixtureSha256": pin_file(stage / "threshold-fixtures.npz")["sha256"],
                "portableParity": parity,
                "resourceStatus": resource["status"],
                "runtimeVersions": runtime_versions,
            },
        )
        atomic_publish_directory(stage, final)
        cleanup_ok = True
        return final
    except BaseException as error:
        failure_material = capture_failure_material(stage, failure_context, error, stage_owned=stage_owned)
        cleanup_errors: list[str] = []
        for path, owned in ((scratch, scratch_owned), (stage, stage_owned)):
            if not owned:
                continue
            try:
                cleanup_path(path)
            except Exception as cleanup_error:
                cleanup_errors.append(repr(cleanup_error))
        cleanup_ok = not cleanup_errors and not isinstance(error, ContainerCleanupError)
        try:
            write_failure(paths, phase, error, cleanup_ok, failure_material, [scratch, stage])
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

def score(
    paths: ExecutionPaths,
    *,
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
    outer, worker, implementation = validate_expected_implementation(paths, expected_outer_runner_sha256, expected_spark_worker_sha256)
    profile_payload, profile_pin = execution_profile(paths)
    ancestry = validate_r2_ancestry(paths)
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
    controls = _fit_control_records(paths)
    phase_lock = make_score_lock(models, score_inputs, runtime, executions, controls)
    recovery = recovery_reference(paths, ancestry, outer, worker, profile_pin, execution_digest)
    inventory = load_json(inventory_path)
    inventory_pin = pinned_control(paths, inventory_path)
    reference = _fit_reference(audited, inventory_pin, inventory, outer, worker, profile_pin, controls, recovery, phase_lock)
    profile_document = phase_execution_profile(phase, profile_payload, profile_pin, outer, execution_digest)

    final = paths.bundle(phase)
    token = uuid.uuid4().hex
    stage = final.with_name(f".{final.name}.tmp-{token}")
    scratch = final.parent / f".{final.name}.score-scratch-{token}"
    log_path = stage / "run.log"
    cleanup_ok = False
    stage_owned = False
    scratch_owned = False
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
        Mount(paths.outer_runner, "/app/run_service_v1_b1_gbt_r3.py"),
        Mount(paths.spark_worker, "/app/service_v1_b1_spark_worker.py"),
        Mount(paths.plan, "/contract/service-v1-b1-r3-fit-recovery.md"),
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
    name = f"feelm-b1-r3-score-{token[:12]}"
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
        [{"order": 0, "workerAction": "score", "timeoutSeconds": TIMEOUT_SECONDS, "command": docker_command}],
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
    }
    try:
        make_stage(final, paths.review(phase), stage)
        stage_owned = True
        scratch.mkdir()
        scratch_owned = True
        validate_mounts(mounts, allowed_readonly=readonly, allowed_write=[scratch, stage])
        write_json_exclusive(stage / "score-input-lock.json", phase_lock)
        write_json_exclusive(stage / "recovery-reference.json", recovery)
        write_json_exclusive(stage / "fit-reference.json", reference)
        write_json_exclusive(stage / "execution-profile.json", profile_document)
        write_json_exclusive(stage / "command.json", command)
        started_at = utc_now()
        result = run_container(name, docker_command, log_path, expected_worker_status="B1_NATURAL_SCORE_WORKER_COMPLETE", timeout_seconds=TIMEOUT_SECONDS)
        completed_at = utc_now()
        completed = completed_container_evidence(result, "score")
        completed.update({"startedAt": started_at, "completedAt": completed_at})
        failure_context["completedStages"] = [completed]
        cleanup_path(scratch)
        predictions_path = stage / "score" / "predictions.parquet"
        runtime_versions = validate_runtime_versions(result.worker)
        score_census = validate_score_predictions(paths.natural_score, predictions_path)
        analyzed = stage / "score" / "analyzed-plan.txt"
        validate_analyzed_score_plan(analyzed)
        resource = _stage_resource(phase, [("score", started_at, completed_at, result)])
        write_json_exclusive(stage / "resource.json", resource)

        current_ancestry = validate_r2_ancestry(paths)
        current_models = model_input_records(paths)
        current_score_inputs = score_input_records(paths)
        current_runtime = worker_runtime_records(paths, inspect_image_id())
        current_executions = execution_records(paths)
        current_controls = _fit_control_records(paths)
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
                "recoveryReferenceSha256": pin_file(stage / "recovery-reference.json")["sha256"],
                "executionProfileSha256": pin_file(stage / "execution-profile.json")["sha256"],
                "predictionSha256": pin_file(predictions_path)["sha256"],
                "scoreCensus": score_census,
                "labelProjected": False,
                "resourceStatus": resource["status"],
                "runtimeVersions": runtime_versions,
            },
        )
        atomic_publish_directory(stage, final)
        cleanup_ok = True
        return final
    except BaseException as error:
        failure_material = capture_failure_material(stage, failure_context, error, stage_owned=stage_owned)
        cleanup_errors: list[str] = []
        for path, owned in ((scratch, scratch_owned), (stage, stage_owned)):
            if not owned:
                continue
            try:
                cleanup_path(path)
            except Exception as cleanup_error:
                cleanup_errors.append(repr(cleanup_error))
        cleanup_ok = not cleanup_errors and not isinstance(error, ContainerCleanupError)
        try:
            write_failure(paths, phase, error, cleanup_ok, failure_material, [scratch, stage])
        except Exception:
            if cleanup_errors:
                raise RuntimeError(f"score failed and cleanup was incomplete: {cleanup_errors}") from error
            raise
        raise

def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--standalone-root", type=Path, required=True)
    result.add_argument("--team-repo", type=Path, required=True)
    actions = result.add_subparsers(dest="action", required=True)

    actions.add_parser("preflight")

    fit_command = actions.add_parser("fit")
    fit_command.add_argument("--preflight-manifest", type=Path, required=True)
    fit_command.add_argument("--preflight-review", type=Path, required=True)
    fit_command.add_argument("--expected-outer-runner-sha256", required=True)
    fit_command.add_argument("--expected-spark-worker-sha256", required=True)
    fit_command.add_argument("--expected-model-input-set-sha256", required=True)
    fit_command.add_argument("--expected-worker-runtime-set-sha256", required=True)
    fit_command.add_argument("--expected-execution-set-sha256", required=True)

    score_command = actions.add_parser("score")
    score_command.add_argument("--fit-manifest", type=Path, required=True)
    score_command.add_argument("--fit-review", type=Path, required=True)
    score_command.add_argument("--expected-fit-manifest-sha256", required=True)
    score_command.add_argument("--expected-fit-review-sha256", required=True)
    score_command.add_argument("--expected-fit-model-inventory-sha256", required=True)
    score_command.add_argument("--expected-outer-runner-sha256", required=True)
    score_command.add_argument("--expected-spark-worker-sha256", required=True)
    score_command.add_argument("--expected-execution-set-sha256", required=True)
    return result


def main() -> None:
    args = parser().parse_args()
    paths = ExecutionPaths.from_roots(args.standalone_root, args.team_repo)
    if args.action == "preflight":
        output = preflight(paths)
    elif args.action == "fit":
        output = fit(
            paths,
            preflight_manifest=args.preflight_manifest,
            preflight_review=args.preflight_review,
            expected_outer_runner_sha256=args.expected_outer_runner_sha256,
            expected_spark_worker_sha256=args.expected_spark_worker_sha256,
            expected_model_input_set_sha256=args.expected_model_input_set_sha256,
            expected_worker_runtime_set_sha256=args.expected_worker_runtime_set_sha256,
            expected_execution_set_sha256=args.expected_execution_set_sha256,
        )
    elif args.action == "score":
        output = score(
            paths,
            fit_manifest=args.fit_manifest,
            fit_review=args.fit_review,
            expected_fit_manifest_sha256=args.expected_fit_manifest_sha256,
            expected_fit_review_sha256=args.expected_fit_review_sha256,
            expected_fit_model_inventory_sha256=args.expected_fit_model_inventory_sha256,
            expected_outer_runner_sha256=args.expected_outer_runner_sha256,
            expected_spark_worker_sha256=args.expected_spark_worker_sha256,
            expected_execution_set_sha256=args.expected_execution_set_sha256,
        )
    else:
        raise ValueError(f"unsupported action: {args.action}")
    print(json.dumps({"status": "COMPLETE", "phase": args.action, "output": str(output)}, sort_keys=True))


if __name__ == "__main__":
    main()
