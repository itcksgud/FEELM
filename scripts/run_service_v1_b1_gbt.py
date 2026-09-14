"""Host-side, fail-closed runner for the reviewed service-v1 B1 Spark GBT path.

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
import time
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq


SCHEMA_VERSION = "feelm-service-v1-b1-host-runner/1"
PLAN_SHA256 = "c5b90b0d19374588fc5cb7a1d98cd6510a1c3708df9667131df5eb3826c79d49"
TEAM_COMMIT = "96a4b27d0ce5c0d4e4fe0348b6c01de0b06f6f7e"
IMAGE = "feelm-rec046-spark:local"
IMAGE_ID = "sha256:de611d62db982bb833b0737a7ddd72a7cd49ab1a7dc9128669cdabae39f46df8"
TIMEOUT_SECONDS = 5_400
MAX_MEMORY_BYTES = 12 * 1024**3
SOURCE_ROWS = 4_997_069
LOGICAL_ROWS = 19_988_276
SCORE_ROWS = 93_230
PARTITIONS = 8
PREDECESSOR_RUN_ID = "b1-gbt120-s339-v1"
RUN_ID = "b1-gbt120-s339-v1-r2"
PREDECESSOR_FAILURE_BYTES = 6_720
PREDECESSOR_FAILURE_SHA256 = "b59a09fbe741472f05d774d829faf809de1c29957812a7eeece8ba1803b093d2"
PREDECESSOR_OUTER_RUNNER_SHA256 = "474cac509cc72b6425b73a0db5917cb6e7bd713ad05402c0ca2eb1d5b1496b52"
PREDECESSOR_SPARK_WORKER_SHA256 = "2c67c63cedd6440857ebfb8982627e5020a47b347562c5385d55fc82c51361e8"

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
}
FORBIDDEN_EVALUATION_BASENAMES = frozenset({"labels.parquet", "evaluation-seal.json"})


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
    require(path.is_file(), f"missing file: {path}")
    value: dict[str, Any] = {
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if logical_path is not None:
        value["path"] = logical_path
    return value


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
    require(not path.exists(), f"refusing to replace existing file: {path}")
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


def atomic_publish_directory(stage: Path, final: Path) -> None:
    require(stage.is_dir(), f"missing staging directory: {stage}")
    require(not final.exists(), f"immutable output already exists: {final}")
    for path in (item for item in stage.rglob("*") if item.is_file()):
        fsync_file(path)
    fsync_directory(stage)
    os.replace(stage, final)
    fsync_directory(final.parent)


def atomic_write_sibling(path: Path, value: Any) -> None:
    require(not path.exists(), f"immutable sibling already exists: {path}")
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        write_json_exclusive(temporary, value)
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        if temporary.exists():
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
        return self.standalone / "docs/recommendation/plans/service-v1-b1-spark-runner.md"

    @property
    def outer_runner(self) -> Path:
        return self.standalone / "scripts/run_service_v1_b1_gbt.py"

    @property
    def spark_worker(self) -> Path:
        return self.standalone / "scripts/service_v1_b1_spark_worker.py"

    @property
    def tests(self) -> Path:
        return self.standalone / "tests/test_service_v1_b1_gbt_runner.py"

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
        return self.output_parent / (PREDECESSOR_RUN_ID + "-preflight-failure.json")


def verify_plan(paths: ExecutionPaths) -> dict[str, Any]:
    actual = pin_file(paths.plan, "implementation/service-v1-b1-spark-runner.md")
    require(actual["sha256"] == PLAN_SHA256, f"reviewed plan hash drift: {actual['sha256']}")
    return actual


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


def training_source_records(paths: ExecutionPaths, image_id: str) -> list[dict[str, Any]]:
    # This runs both before and after every training phase, so a fourth file or
    # path replacement cannot slip in between execution and publication.
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
        verify_plan(paths),
        pin_file(paths.outer_runner, "implementation/run_service_v1_b1_gbt.py"),
        pin_file(paths.spark_worker, "implementation/service_v1_b1_spark_worker.py"),
        pin_file(paths.tests, "implementation/test_service_v1_b1_gbt_runner.py"),
        pin_file(paths.portable_reader, "implementation/combination340_models.py"),
        pin_file(paths.portable_dependency, "implementation/rec046_common.py"),
        virtual_pin("runtime/docker-image-id", image_id),
    ]
    return records


def make_phase_lock(
    phase: str,
    source_records: Sequence[Mapping[str, Any]],
    control_records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    sources = [dict(record) for record in source_records]
    controls = [dict(record) for record in control_records]
    return {
        "schemaVersion": "feelm-service-v1-b1-input-lock/1",
        "phase": phase,
        "trainingSourceRecords": sources,
        "controlReferences": controls,
        "trainingSourceSetSha256": canonical_record_set_sha256(sources),
        "controlReferenceSetSha256": canonical_record_set_sha256(controls),
        "inputSetSha256": canonical_record_set_sha256([*sources, *controls]),
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
    outer = pin_file(paths.outer_runner, "implementation/run_service_v1_b1_gbt.py")
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
        require(source.exists(), f"mount source does not exist: {source}")
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


def resource_gate(worker_result: Mapping[str, Any], state: Mapping[str, Any], timed_out: bool) -> dict[str, Any]:
    observation = worker_result.get("resourceObservation")
    if not isinstance(observation, dict):
        observation = {}
    peak = observation.get("peakBytes")
    source = observation.get("peakSource")
    worker_status = observation.get("resourceStatus")
    oom = bool(state.get("OOMKilled"))
    exit_code = state.get("ExitCode")
    status = "PASS"
    if timed_out or oom or worker_status == "RESOURCE_STOP" or (isinstance(peak, int) and peak > MAX_MEMORY_BYTES):
        status = "RESOURCE_STOP"
    elif not isinstance(peak, int) or peak < 0 or not isinstance(source, str) or worker_status != "PASS":
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
        "schemaVersion": "feelm-service-v1-b1-container-failure-evidence/1",
        "containerName": container_name,
        "command": list(command),
        "created": created,
        "timedOut": timed_out,
        "elapsedSeconds": elapsed_seconds,
        "dockerState": dict(state),
        "resource": dict(resource),
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
        try:
            output, _ = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as timeout_error:
            timed_out = True
            partial_output = timeout_error.output
            if isinstance(partial_output, bytes):
                partial_output = partial_output.decode("utf-8", errors="replace")
            if not isinstance(partial_output, str):
                partial_output = ""
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
        resource = resource_gate(worker, state, timed_out)
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
        resource = resource_gate(worker, state, timed_out)
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
    )


def logical_path(paths: ExecutionPaths, path: Path) -> str:
    resolved = path.resolve()
    for prefix, root in (("standalone", paths.standalone), ("team", paths.team)):
        try:
            return prefix + "/" + resolved.relative_to(root).as_posix()
        except ValueError:
            continue
    raise ValueError(f"path is outside the two approved repositories: {path}")


def pinned_control(paths: ExecutionPaths, path: Path) -> dict[str, Any]:
    return pin_file(path, logical_path(paths, path))


def predecessor_failure_record(paths: ExecutionPaths) -> dict[str, Any]:
    record = pinned_control(paths, paths.predecessor_failure)
    require(
        (record["bytes"], record["sha256"])
        == (PREDECESSOR_FAILURE_BYTES, PREDECESSOR_FAILURE_SHA256),
        "predecessor failure evidence drift",
    )
    payload = load_json(paths.predecessor_failure)
    require(payload.get("schemaVersion") == "feelm-service-v1-b1-failure/1", "predecessor failure schema drift")
    require(payload.get("phase") == "preflight" and payload.get("status") == "FAILED", "predecessor failure phase/status drift")
    require(payload.get("cleanupComplete") is True, "predecessor failure cleanup was incomplete")
    run = payload.get("containerRun")
    require(isinstance(run, dict), "predecessor container evidence missing")
    state = run.get("dockerState")
    require(
        isinstance(state, dict)
        and state.get("ExitCode") == 1
        and state.get("OOMKilled") is False
        and run.get("timedOut") is False,
        "predecessor failure was not the reviewed non-resource wiring failure",
    )
    require(
        "masked bundle file set is not exactly the reviewed three files" in str(run.get("stdout", "")),
        "predecessor failure reason drift",
    )
    return record


def recovery_reference(
    predecessor: Mapping[str, Any], outer: Mapping[str, Any], worker: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "schemaVersion": "feelm-service-v1-b1-recovery-reference/1",
        "status": "PREDECESSOR_FAILURE_VERIFIED",
        "predecessorRunId": PREDECESSOR_RUN_ID,
        "runId": RUN_ID,
        "predecessorFailure": dict(predecessor),
        "predecessorOuterRunnerSha256": PREDECESSOR_OUTER_RUNNER_SHA256,
        "predecessorSparkWorkerSha256": PREDECESSOR_SPARK_WORKER_SHA256,
        "outerRunnerSha256": outer["sha256"],
        "sparkWorkerSha256": worker["sha256"],
        "failureClass": "MASKED_BUNDLE_CONTAINER_MOUNT_WIRING",
        "modelFitPerformed": False,
        "fullPreflightPerformed": False,
    }


def validate_preflight_recovery(
    paths: ExecutionPaths,
    audited: Mapping[str, Any],
    predecessor: Mapping[str, Any],
    outer: Mapping[str, Any],
    worker: Mapping[str, Any],
) -> None:
    observed = load_json(paths.bundle("preflight") / "recovery-reference.json")
    require(observed == recovery_reference(predecessor, outer, worker), "preflight recovery reference drift")
    lock = audited.get("inputLock")
    manifest = audited.get("manifest")
    require(isinstance(lock, dict) and isinstance(manifest, dict), "preflight recovery inputs missing")
    controls = lock.get("controlReferences")
    require(isinstance(controls, list) and records_equal(controls, [predecessor]), "preflight predecessor control drift")
    control_digest = canonical_record_set_sha256([predecessor])
    require(lock.get("controlReferenceSetSha256") == control_digest, "preflight predecessor control digest drift")
    require(manifest.get("controlReferenceSetSha256") == control_digest, "preflight manifest predecessor digest drift")
    sources = lock.get("trainingSourceRecords")
    require(isinstance(sources, list), "preflight training records missing")
    require(
        lock.get("inputSetSha256") == canonical_record_set_sha256([*sources, predecessor]),
        "preflight recovery input-set digest drift",
    )
    require(manifest.get("inputSetSha256") == lock.get("inputSetSha256"), "preflight manifest input-set drift")
    require(manifest.get("predecessorFailureSha256") == predecessor["sha256"], "preflight predecessor hash drift")
    require(
        manifest.get("recoveryReferenceSha256")
        == pin_file(paths.bundle("preflight") / "recovery-reference.json")["sha256"],
        "preflight recovery-reference hash drift",
    )


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
    if phase in {"preflight", "fit"}:
        pinned[lock_key]["trainingSourceSetSha256"] = phase_lock.get("trainingSourceSetSha256")
    return {
        "schemaVersion": "feelm-service-v1-b1-review-contract/1",
        "phase": phase,
        "target": pinned,
        "reviewPath": logical_path(paths, paths.review(phase)),
        "requiredStatus": "PASS",
    }


def _validate_target_record(record: Any, expected: Mapping[str, Any], name: str) -> None:
    require(isinstance(record, dict), f"review target missing: {name}")
    for key in ("path", "bytes", "sha256"):
        require(record.get(key) == expected.get(key), f"review target {name} {key} drift")


def validate_review(paths: ExecutionPaths, phase: str, review_path: Path | None = None) -> dict[str, Any]:
    path = (review_path or paths.review(phase)).resolve()
    require(path == paths.review(phase).resolve(), "review path is not the canonical sibling")
    review = load_json(path)
    require(review.get("status") == "PASS", f"{phase} review is not PASS")
    require(review.get("phase") == phase, f"{phase} review phase drift")
    expected = review_contract(paths, phase)["target"]
    observed = review.get("target")
    require(isinstance(observed, dict), f"{phase} review target map missing")
    require(set(expected).issubset(observed), f"{phase} review target set incomplete")
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
    input_name = "score-input-lock.json" if phase == "score" else "input-lock.json"
    phase_lock = load_json(paths.bundle(phase) / input_name)
    if phase in {"preflight", "fit"}:
        require(
            manifest.get("trainingSourceSetSha256") == phase_lock.get("trainingSourceSetSha256"),
            f"{phase} training-source digest drift",
        )
        require(
            observed["input_lock"].get("trainingSourceSetSha256") == phase_lock.get("trainingSourceSetSha256"),
            f"{phase} review omitted training-source digest",
        )
    review_lock_key = "score_input_lock" if phase == "score" else "input_lock"
    require(
        observed[review_lock_key].get("inputSetSha256") == phase_lock.get("inputSetSha256"),
        f"{phase} review omitted phase input digest",
    )
    require(manifest.get("inputSetSha256") == phase_lock.get("inputSetSha256"), f"{phase} input digest drift")
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


def make_stage(final: Path, review: Path) -> Path:
    require(not final.exists(), f"immutable bundle already exists: {final}")
    require(not review.exists(), f"immutable review already exists: {review}")
    final.parent.mkdir(parents=True, exist_ok=True)
    stage = final.with_name(f".{final.name}.tmp-{uuid.uuid4().hex}")
    require(not stage.exists(), f"staging collision: {stage}")
    stage.mkdir()
    return stage


def cleanup_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()
    require(not path.exists(), f"cleanup failed: {path}")


def replace_json_in_stage(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.replace-{uuid.uuid4().hex}")
    try:
        write_json_exclusive(temporary, value)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def manifest_files(stage: Path) -> dict[str, dict[str, Any]]:
    return relative_inventory(stage, exclude=("manifest.json",))


def write_manifest_last(stage: Path, fields: Mapping[str, Any]) -> dict[str, Any]:
    require(not (stage / "manifest.json").exists(), "manifest must be written once and last")
    manifest = dict(fields)
    manifest["files"] = manifest_files(stage)
    write_json_exclusive(stage / "manifest.json", manifest)
    require(
        set(relative_inventory(stage)) == {*manifest["files"], "manifest.json"},
        "file set changed while writing manifest",
    )
    return manifest


def write_failure(paths: ExecutionPaths, phase: str, error: BaseException, cleanup_ok: bool) -> None:
    evidence = getattr(error, "evidence", None)
    payload: dict[str, Any] = {
        "schemaVersion": "feelm-service-v1-b1-failure/1",
        "phase": phase,
        "status": "FAILED",
        "errorType": type(error).__name__,
        "error": str(error),
        "cleanupComplete": cleanup_ok,
        "createdAt": utc_now(),
    }
    if isinstance(evidence, Mapping):
        payload["containerRun"] = dict(evidence)
    atomic_write_sibling(
        paths.failure(phase),
        payload,
    )


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


def training_mounts(paths: ExecutionPaths, scratch: Path, output: Path | None) -> list[Mount]:
    verify_masked_bundle_directory(paths)
    mounts = [
        Mount(paths.outer_runner, "/app/run_service_v1_b1_gbt.py"),
        Mount(paths.spark_worker, "/app/service_v1_b1_spark_worker.py"),
        Mount(paths.plan, "/contract/service-v1-b1-spark-runner.md"),
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
    require(not final.exists(), f"immutable {phase} bundle already exists")
    require(not paths.review(phase).exists(), f"immutable {phase} review already exists")
    require(not paths.failure(phase).exists(), f"existing {phase} failure record requires investigation")
    stale = list(final.parent.glob(f".{final.name}.tmp-*")) + list(final.parent.glob(f".{final.name}.*-scratch-*"))
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
    require(not running_container_names(), "another B1 Spark container is running")
    verify_team_commit(paths)
    image_id = inspect_image_id()
    source_records = training_source_records(paths, image_id)
    predecessor = predecessor_failure_record(paths)
    phase_lock = make_phase_lock(phase, source_records, [predecessor])
    outer, worker, implementation = implementation_pins(paths)

    final = paths.bundle(phase)
    stage = make_stage(final, paths.review(phase))
    token = uuid.uuid4().hex
    dry_scratch = final.parent / f".{final.name}.dry-scratch-{token}"
    full_scratch = final.parent / f".{final.name}.full-scratch-{token}"
    dry_scratch.mkdir()
    full_scratch.mkdir()
    log_path = stage / "run.log"
    stage_results: list[tuple[str, str, str, ContainerResult]] = []
    cleanup_ok = False
    try:
        dry_name = f"feelm-b1-preflight-dry-{token[:12]}"
        full_name = f"feelm-b1-preflight-full-{token[:12]}"
        dry_mounts = training_mounts(paths, dry_scratch, None)
        full_mounts = training_mounts(paths, full_scratch, stage)
        dry_args = common_training_worker_arguments(
            "dry-run-first-row-group", include_recipe=False, include_output=False
        )
        full_args = common_training_worker_arguments("preflight", include_recipe=True, include_output=True)
        dry_command = spark_container_command(dry_name, dry_mounts, dry_args)
        full_command = spark_container_command(full_name, full_mounts, full_args)
        write_json_exclusive(stage / "input-lock.json", phase_lock)
        write_json_exclusive(stage / "recovery-reference.json", recovery_reference(predecessor, outer, worker))
        write_json_exclusive(
            stage / "command.json",
            {
                "schemaVersion": "feelm-service-v1-b1-command/1",
                "phase": phase,
                "createdAt": utc_now(),
                "dockerImage": IMAGE,
                "dockerImageId": image_id,
                "dryRunPrecedesFullMaterialization": True,
                "sequence": [
                    {"order": 0, "workerAction": "dry-run-first-row-group", "command": dry_command},
                    {"order": 1, "workerAction": "preflight", "command": full_command},
                ],
            },
        )

        dry_started = utc_now()
        dry_result = run_container(
            dry_name,
            dry_command,
            log_path,
            expected_worker_status="DRY_RUN_ONLY_NOT_PUBLISHED",
        )
        dry_completed = utc_now()
        stage_results.append(("dry-run-first-row-group", dry_started, dry_completed, dry_result))
        cleanup_path(dry_scratch)
        require(not (stage / "partition-identity.json").exists(), "dry-run published preflight identity")

        full_started = utc_now()
        full_result = run_container(
            full_name,
            full_command,
            log_path,
            expected_worker_status="B1_FULL_PREFLIGHT_WORKER_COMPLETE",
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
        current_sources = training_source_records(paths, inspect_image_id())
        current_predecessor = predecessor_failure_record(paths)
        require(records_equal(source_records, current_sources), "training source mutated during preflight")
        require(records_equal([predecessor], [current_predecessor]), "predecessor failure evidence mutated during preflight")
        require(
            phase_lock["trainingSourceSetSha256"] == canonical_record_set_sha256(current_sources),
            "training source digest mutated during preflight",
        )
        current_lock = make_phase_lock(phase, current_sources, [current_predecessor])
        require(
            current_lock["inputSetSha256"] == phase_lock["inputSetSha256"],
            "preflight input set mutated during execution",
        )
        write_manifest_last(
            stage,
            {
                "schemaVersion": "feelm-service-v1-b1-preflight-manifest/1",
                "runId": RUN_ID,
                "status": "B1_FULL_PREFLIGHT_COMPLETE_AWAITING_REVIEW",
                "createdAt": utc_now(),
                "fitAuthorized": False,
                "modelFitPerformed": False,
                "scorePerformed": False,
                "sourceRows": SOURCE_ROWS,
                "logicalRows": LOGICAL_ROWS,
                "partitionCount": PARTITIONS,
                "outerRunnerSha256": outer["sha256"],
                "sparkWorkerSha256": worker["sha256"],
                "implementationSetSha256": implementation,
                "trainingSourceSetSha256": phase_lock["trainingSourceSetSha256"],
                "controlReferenceSetSha256": phase_lock["controlReferenceSetSha256"],
                "inputSetSha256": phase_lock["inputSetSha256"],
                "predecessorFailureSha256": predecessor["sha256"],
                "recoveryReferenceSha256": pin_file(stage / "recovery-reference.json")["sha256"],
                "dryRunPrecedesFullMaterialization": True,
                "dryRunLogicalRows": dry_result.worker.get("logicalRows"),
                "partitionIdentitySha256": pin_file(stage / "partition-identity.json")["sha256"],
                "identity": {
                    "sourceRows": identity["sourceRows"],
                    "logicalRows": identity["logicalRows"],
                },
                "resourceStatus": resource["status"],
                "runtimeVersions": full_runtime,
            },
        )
        atomic_publish_directory(stage, final)
        cleanup_ok = True
        return final
    except BaseException as error:
        cleanup_errors: list[str] = []
        for path in (dry_scratch, full_scratch, stage):
            try:
                cleanup_path(path)
            except Exception as cleanup_error:
                cleanup_errors.append(repr(cleanup_error))
        cleanup_ok = not cleanup_errors and not isinstance(error, ContainerCleanupError)
        try:
            write_failure(paths, phase, error, cleanup_ok)
        except Exception:
            if cleanup_errors:
                raise RuntimeError(f"preflight failed and cleanup was incomplete: {cleanup_errors}") from error
        raise
    finally:
        if not cleanup_ok:
            for path in (dry_scratch, full_scratch):
                if path.exists():
                    try:
                        cleanup_path(path)
                    except Exception:
                        pass


def _preflight_control_records(paths: ExecutionPaths) -> list[dict[str, Any]]:
    records = bundle_records(paths, paths.bundle("preflight"))
    records.append(pinned_control(paths, paths.review("preflight")))
    return records


def _fit_control_records(paths: ExecutionPaths) -> list[dict[str, Any]]:
    records = bundle_records(paths, paths.bundle("fit"))
    records.append(pinned_control(paths, paths.review("fit")))
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
    paths: ExecutionPaths,
    audited: Mapping[str, Any],
    controls: Sequence[Mapping[str, Any]],
    outer: Mapping[str, Any],
    worker: Mapping[str, Any],
    implementation: str,
    phase_lock: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schemaVersion": "feelm-service-v1-b1-preflight-reference/1",
        "preflightManifest": dict(audited["manifestPin"]),
        "preflightReview": dict(audited["reviewPin"]),
        "reviewedArtifacts": [dict(record) for record in controls],
        "outerRunner": dict(outer),
        "sparkWorker": dict(worker),
        "implementationSetSha256": implementation,
        "trainingSourceSetSha256": phase_lock["trainingSourceSetSha256"],
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
    expected_training_source_set_sha256: str,
) -> Path:
    phase = "fit"
    ensure_phase_clear(paths, phase)
    require(not running_container_names(), "another B1 Spark container is running")
    require(preflight_manifest.resolve() == (paths.bundle("preflight") / "manifest.json").resolve(), "noncanonical preflight manifest")
    require(preflight_review.resolve() == paths.review("preflight").resolve(), "noncanonical preflight review")
    outer, worker, implementation = validate_expected_implementation(
        paths, expected_outer_runner_sha256, expected_spark_worker_sha256
    )
    audited = validate_preflight_review(paths, preflight_review)
    predecessor = predecessor_failure_record(paths)
    validate_preflight_recovery(paths, audited, predecessor, outer, worker)
    verify_team_commit(paths)
    image_id = inspect_image_id()
    source_records = training_source_records(paths, image_id)
    training_digest = canonical_record_set_sha256(source_records)
    require(training_digest == expected_training_source_set_sha256.lower(), "expected training-source digest drift")
    require(training_digest == audited["manifest"].get("trainingSourceSetSha256"), "preflight training-source digest drift")
    controls = _preflight_control_records(paths)
    phase_lock = make_phase_lock(phase, source_records, controls)
    reference = _preflight_reference(paths, audited, controls, outer, worker, implementation, phase_lock)

    final = paths.bundle(phase)
    stage = make_stage(final, paths.review(phase))
    token = uuid.uuid4().hex
    scratch = final.parent / f".{final.name}.fit-scratch-{token}"
    scratch.mkdir()
    log_path = stage / "run.log"
    cleanup_ok = False
    try:
        control_paths = [paths.bundle("preflight") / relative for relative in PREFLIGHT_REVIEW_TARGETS.values()]
        control_paths.append(paths.review("preflight"))
        mounts = training_mounts(paths, scratch, stage)
        for index, path in enumerate(control_paths):
            mounts.append(Mount(path, f"/control/preflight/{index:02d}-{path.name}"))
        validate_mounts(
            mounts,
            allowed_readonly=[*_training_readonly_paths(paths), *control_paths],
            allowed_write=[scratch, stage],
        )
        name = f"feelm-b1-fit-{token[:12]}"
        worker_args = common_training_worker_arguments("fit", include_recipe=True, include_output=True)
        command = spark_container_command(name, mounts, worker_args)
        write_json_exclusive(stage / "input-lock.json", phase_lock)
        write_json_exclusive(stage / "preflight-reference.json", reference)
        write_json_exclusive(
            stage / "command.json",
            {
                "schemaVersion": "feelm-service-v1-b1-command/1",
                "phase": phase,
                "createdAt": utc_now(),
                "dockerImage": IMAGE,
                "dockerImageId": image_id,
                "sequence": [{"order": 0, "workerAction": "fit", "command": command}],
            },
        )
        started_at = utc_now()
        result = run_container(name, command, log_path, expected_worker_status="B1_MODEL_FIT_WORKER_COMPLETE")
        completed_at = utc_now()
        cleanup_path(scratch)
        identity = validate_partition_identity(stage / "partition-identity.json")
        runtime = validate_runtime_versions(result.worker)
        require(
            identity == load_json(paths.bundle("preflight") / "partition-identity.json"),
            "fit identity differs from audited preflight identity",
        )
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

        current_sources = training_source_records(paths, inspect_image_id())
        current_controls = _preflight_control_records(paths)
        require(records_equal(source_records, current_sources), "training source mutated during fit")
        require(records_equal(controls, current_controls), "preflight control reference mutated during fit")
        require(phase_lock["inputSetSha256"] == canonical_record_set_sha256([*current_sources, *current_controls]), "fit phase input digest drift")
        write_manifest_last(
            stage,
            {
                "schemaVersion": "feelm-service-v1-b1-fit-manifest/1",
                "runId": RUN_ID,
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
                "trainingSourceSetSha256": phase_lock["trainingSourceSetSha256"],
                "controlReferenceSetSha256": phase_lock["controlReferenceSetSha256"],
                "inputSetSha256": phase_lock["inputSetSha256"],
                "preflightManifestSha256": audited["manifestPin"]["sha256"],
                "preflightReviewSha256": audited["reviewPin"]["sha256"],
                "partitionIdentitySha256": pin_file(stage / "partition-identity.json")["sha256"],
                "modelInventorySha256": pin_file(stage / "model-file-inventory.json")["sha256"],
                "modelFileSetSha256": inventory["inventorySha256"],
                "thresholdFixtureSha256": pin_file(stage / "threshold-fixtures.npz")["sha256"],
                "portableParity": parity,
                "resourceStatus": resource["status"],
                "runtimeVersions": runtime,
            },
        )
        atomic_publish_directory(stage, final)
        cleanup_ok = True
        return final
    except BaseException as error:
        cleanup_errors: list[str] = []
        for path in (scratch, stage):
            try:
                cleanup_path(path)
            except Exception as cleanup_error:
                cleanup_errors.append(repr(cleanup_error))
        cleanup_ok = not cleanup_errors and not isinstance(error, ContainerCleanupError)
        try:
            write_failure(paths, phase, error, cleanup_ok)
        except Exception:
            if cleanup_errors:
                raise RuntimeError(f"fit failed and cleanup was incomplete: {cleanup_errors}") from error
        raise
    finally:
        if not cleanup_ok and scratch.exists():
            try:
                cleanup_path(scratch)
            except Exception:
                pass


def score_source_records(paths: ExecutionPaths, image_id: str) -> list[dict[str, Any]]:
    return [
        verify_expected_pin(paths.feature_contract, "contract/feature-schema.v1.json"),
        verify_expected_pin(paths.natural_score, "source/natural-score.parquet"),
        verify_plan(paths),
        pin_file(paths.outer_runner, "implementation/run_service_v1_b1_gbt.py"),
        pin_file(paths.spark_worker, "implementation/service_v1_b1_spark_worker.py"),
        pin_file(paths.tests, "implementation/test_service_v1_b1_gbt_runner.py"),
        virtual_pin("runtime/docker-image-id", image_id),
    ]


def make_score_lock(
    source_records: Sequence[Mapping[str, Any]], control_records: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    sources = [dict(record) for record in source_records]
    controls = [dict(record) for record in control_records]
    return {
        "schemaVersion": "feelm-service-v1-b1-score-input-lock/1",
        "phase": "score",
        "scoreSourceRecords": sources,
        "controlReferences": controls,
        "scoreSourceSetSha256": canonical_record_set_sha256(sources),
        "controlReferenceSetSha256": canonical_record_set_sha256(controls),
        "inputSetSha256": canonical_record_set_sha256([*sources, *controls]),
        "evaluationTargetsRead": False,
    }


def _fit_reference(
    audited: Mapping[str, Any],
    inventory_pin: Mapping[str, Any],
    inventory: Mapping[str, Any],
    outer: Mapping[str, Any],
    worker: Mapping[str, Any],
    implementation: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": "feelm-service-v1-b1-fit-reference/1",
        "fitManifest": dict(audited["manifestPin"]),
        "fitReview": dict(audited["reviewPin"]),
        "modelInventory": dict(inventory_pin),
        "modelFileSetSha256": inventory["inventorySha256"],
        "modelFiles": [dict(record) for record in inventory["files"]],
        "outerRunner": dict(outer),
        "sparkWorker": dict(worker),
        "implementationSetSha256": implementation,
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
) -> Path:
    phase = "score"
    ensure_phase_clear(paths, phase)
    require(not running_container_names(), "another B1 Spark container is running")
    canonical_fit_manifest = paths.bundle("fit") / "manifest.json"
    canonical_fit_review = paths.review("fit")
    require(fit_manifest.resolve() == canonical_fit_manifest.resolve(), "noncanonical fit manifest")
    require(fit_review.resolve() == canonical_fit_review.resolve(), "noncanonical fit review")
    outer, worker, implementation = validate_expected_implementation(
        paths, expected_outer_runner_sha256, expected_spark_worker_sha256
    )
    require(pin_file(fit_manifest)["sha256"] == expected_fit_manifest_sha256.lower(), "expected fit manifest SHA drift")
    require(pin_file(fit_review)["sha256"] == expected_fit_review_sha256.lower(), "expected fit review SHA drift")
    inventory_path = paths.bundle("fit") / "model-file-inventory.json"
    require(
        pin_file(inventory_path)["sha256"] == expected_fit_model_inventory_sha256.lower(),
        "expected fit model-inventory SHA drift",
    )
    audited = validate_fit_review(paths, fit_review)
    preflight_audited = validate_preflight_review(paths)
    predecessor = predecessor_failure_record(paths)
    validate_preflight_recovery(paths, preflight_audited, predecessor, outer, worker)
    image_id = inspect_image_id()
    sources = score_source_records(paths, image_id)
    controls = _fit_control_records(paths)
    phase_lock = make_score_lock(sources, controls)
    inventory = load_json(inventory_path)
    inventory_pin = pinned_control(paths, inventory_path)
    reference = _fit_reference(audited, inventory_pin, inventory, outer, worker, implementation)

    final = paths.bundle(phase)
    stage = make_stage(final, paths.review(phase))
    token = uuid.uuid4().hex
    scratch = final.parent / f".{final.name}.score-scratch-{token}"
    scratch.mkdir()
    log_path = stage / "run.log"
    cleanup_ok = False
    try:
        model_root = paths.bundle("fit") / "model" / "native"
        readonly = [
            paths.outer_runner,
            paths.spark_worker,
            paths.plan,
            paths.feature_contract,
            paths.natural_score,
            paths.bundle("fit"),
            fit_review,
        ]
        mounts = [
            Mount(paths.outer_runner, "/app/run_service_v1_b1_gbt.py"),
            Mount(paths.spark_worker, "/app/service_v1_b1_spark_worker.py"),
            Mount(paths.plan, "/contract/service-v1-b1-spark-runner.md"),
            Mount(paths.feature_contract, "/contract/feature-schema.v1.json"),
            Mount(paths.natural_score, "/input/natural-score.parquet"),
            Mount(paths.bundle("fit"), "/fit"),
            Mount(fit_review, "/control/fit-review.json"),
            Mount(scratch, "/scratch", readonly=False),
            Mount(stage, "/output", readonly=False),
        ]
        validate_mounts(mounts, allowed_readonly=readonly, allowed_write=[scratch, stage])
        name = f"feelm-b1-score-{token[:12]}"
        worker_args = [
            "score",
            "--score-input",
            "/input/natural-score.parquet",
            "--model-dir",
            "/fit/model/native",
            "--output",
            "/output",
            "--scratch",
            "/scratch",
        ]
        command = spark_container_command(name, mounts, worker_args)
        write_json_exclusive(stage / "score-input-lock.json", phase_lock)
        write_json_exclusive(stage / "fit-reference.json", reference)
        write_json_exclusive(
            stage / "command.json",
            {
                "schemaVersion": "feelm-service-v1-b1-command/1",
                "phase": phase,
                "createdAt": utc_now(),
                "dockerImage": IMAGE,
                "dockerImageId": image_id,
                "sequence": [{"order": 0, "workerAction": "score", "command": command}],
            },
        )
        started_at = utc_now()
        result = run_container(name, command, log_path, expected_worker_status="B1_NATURAL_SCORE_WORKER_COMPLETE")
        completed_at = utc_now()
        cleanup_path(scratch)
        predictions_path = stage / "score" / "predictions.parquet"
        runtime = validate_runtime_versions(result.worker)
        score_census = validate_score_predictions(paths.natural_score, predictions_path)
        analyzed = stage / "score" / "analyzed-plan.txt"
        validate_analyzed_score_plan(analyzed)
        resource = _stage_resource(phase, [("score", started_at, completed_at, result)])
        write_json_exclusive(stage / "resource.json", resource)

        current_sources = score_source_records(paths, inspect_image_id())
        current_controls = _fit_control_records(paths)
        require(records_equal(sources, current_sources), "score source mutated during scoring")
        require(records_equal(controls, current_controls), "fit control reference mutated during scoring")
        require(
            phase_lock["inputSetSha256"] == canonical_record_set_sha256([*current_sources, *current_controls]),
            "score phase input digest drift",
        )
        write_manifest_last(
            stage,
            {
                "schemaVersion": "feelm-service-v1-b1-score-manifest/1",
                "runId": RUN_ID,
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
                "scoreSourceSetSha256": phase_lock["scoreSourceSetSha256"],
                "controlReferenceSetSha256": phase_lock["controlReferenceSetSha256"],
                "inputSetSha256": phase_lock["inputSetSha256"],
                "fitManifestSha256": audited["manifestPin"]["sha256"],
                "fitReviewSha256": audited["reviewPin"]["sha256"],
                "modelInventorySha256": inventory_pin["sha256"],
                "modelFileSetSha256": inventory["inventorySha256"],
                "predictionSha256": pin_file(predictions_path)["sha256"],
                "scoreCensus": score_census,
                "labelProjected": False,
                "resourceStatus": resource["status"],
                "runtimeVersions": runtime,
            },
        )
        atomic_publish_directory(stage, final)
        cleanup_ok = True
        return final
    except BaseException as error:
        cleanup_errors: list[str] = []
        for path in (scratch, stage):
            try:
                cleanup_path(path)
            except Exception as cleanup_error:
                cleanup_errors.append(repr(cleanup_error))
        cleanup_ok = not cleanup_errors and not isinstance(error, ContainerCleanupError)
        try:
            write_failure(paths, phase, error, cleanup_ok)
        except Exception:
            if cleanup_errors:
                raise RuntimeError(f"score failed and cleanup was incomplete: {cleanup_errors}") from error
        raise
    finally:
        if not cleanup_ok and scratch.exists():
            try:
                cleanup_path(scratch)
            except Exception:
                pass


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
    fit_command.add_argument("--expected-training-source-set-sha256", required=True)

    score_command = actions.add_parser("score")
    score_command.add_argument("--fit-manifest", type=Path, required=True)
    score_command.add_argument("--fit-review", type=Path, required=True)
    score_command.add_argument("--expected-fit-manifest-sha256", required=True)
    score_command.add_argument("--expected-fit-review-sha256", required=True)
    score_command.add_argument("--expected-fit-model-inventory-sha256", required=True)
    score_command.add_argument("--expected-outer-runner-sha256", required=True)
    score_command.add_argument("--expected-spark-worker-sha256", required=True)
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
            expected_training_source_set_sha256=args.expected_training_source_set_sha256,
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
        )
    else:
        raise ValueError(f"unsupported action: {args.action}")
    print(json.dumps({"status": "COMPLETE", "phase": args.action, "output": str(output)}, sort_keys=True))


if __name__ == "__main__":
    main()
