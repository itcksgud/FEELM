"""Evaluate audited Service-v1 B1 predictions against the sealed B0 axis.

The production CLI has two deliberately separate phases. ``calibrate-select``
fits two non-negative affine calibrators on CALIBRATION users and evaluates the
frozen coefficients on SELECTION users. ``confirm`` is only allowed after an
independent review has pinned a PASS selection bundle; it then evaluates the
same coefficients on CONFIRMATION users.

Importing this module performs no file I/O.  The score audit gate is checked
before labels are hashed or decoded.  Production paths contain no bypass,
overwrite, force, or reduced-bootstrap option.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import shutil
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as pads
import pyarrow.parquet as pq


SCRIPT = Path(__file__).resolve()
ROOT = SCRIPT.parents[1]
TEAM_ROOT = ROOT.parent / "S15P21E106"
PLAN = ROOT / "docs/recommendation/plans/service-v1-b1-spark-runner.md"
PLAN_SHA256 = "c5b90b0d19374588fc5cb7a1d98cd6510a1c3708df9667131df5eb3826c79d49"
RUN_ID = "b1-gbt120-s339-v1-r2"
PREDECESSOR_RUN_ID = "b1-gbt120-s339-v1"
PREDECESSOR_FAILURE_BYTES = 6_720
PREDECESSOR_FAILURE_SHA256 = "b59a09fbe741472f05d774d829faf809de1c29957812a7eeece8ba1803b093d2"
PREDECESSOR_OUTER_RUNNER_SHA256 = "474cac509cc72b6425b73a0db5917cb6e7bd713ad05402c0ca2eb1d5b1496b52"
PREDECESSOR_SPARK_WORKER_SHA256 = "2c67c63cedd6440857ebfb8982627e5020a47b347562c5385d55fc82c51361e8"
IMAGE_ID = "sha256:de611d62db982bb833b0737a7ddd72a7cd49ab1a7dc9128669cdabae39f46df8"

TRAINING_SOURCE_PATHS = frozenset({
    "contract/training-recipe.v1.json",
    "contract/service-v1.json",
    "contract/MODELS.md",
    "contract/feature-schema.v1.json",
    "source/natural-train.parquet",
    "source/tmdb-masked-train.parquet",
    "source/masked-manifest.json",
    "source/views-manifest.json",
    "source/masked-review.json",
    "implementation/service-v1-b1-spark-runner.md",
    "implementation/run_service_v1_b1_gbt.py",
    "implementation/service_v1_b1_spark_worker.py",
    "implementation/test_service_v1_b1_gbt_runner.py",
    "implementation/combination340_models.py",
    "implementation/rec046_common.py",
    "runtime/docker-image-id",
})
SCORE_SOURCE_PATHS = frozenset({
    "contract/feature-schema.v1.json",
    "source/natural-score.parquet",
    "implementation/service-v1-b1-spark-runner.md",
    "implementation/run_service_v1_b1_gbt.py",
    "implementation/service_v1_b1_spark_worker.py",
    "implementation/test_service_v1_b1_gbt_runner.py",
    "runtime/docker-image-id",
})

ROLE_DIGEST = "cf92b686ab21a950611084682e46bfd319426d5dbc3c4f58836a4836b3f416c6"
SOURCE_PINS: dict[str, tuple[int, str]] = {
    "artifact_manifest": (15_504, "1a9ba0cd0101f6d065227c37ee642a3fc75c2c69b34c0da8fa6dacff351c2343"),
    "evaluation_contract": (12_466, "db7bd86f6b72b2edee7b6a65610bdb177c65937b518288417e3c6bdbb02bf3e3"),
    "contexts": (12_290_713, "951fc2464bd3aea25ea486c79c33084f6241f7846a3ed626e9d5fc3907a7aab7"),
    "catalog": (728_100, "0bde668e0e26f5f82b5c41d90d62c7569fd350bf2a2fd4b59bb4402f438c5947"),
    "labels": (75_490, "e3bf301a6e2ea7885b59bcab7fe83c2d3ad84f93f2f1bbb2d54d943b9a658db8"),
    "evaluation_seal": (4_890, "0ab420fb3e50cf770d9dd7a24d64e5ce8896a4c17b38a7ba5df73398ad6fb9c7"),
    "roles": (5_806, "466b7cede9cb2d67f2bd7fca4fcdada770943bdd9d5924c35ccfdbe6a4cf2cc9"),
    "metadata": (6_891_831, "4d838874938115be7a4b1f629a920dd196e082b655b75d559d71039e52eb7d8d"),
    "score_axis": (2_985_357, "9832425537943823f524ae6730aa1059b77d2c0464664f1dc29357bf95078f9b"),
    "ratings": (32_950_407, "28b46687abec2e0edb3a892ec4f4dbd9d5cca701bf220b2812f9e7cf9b905a63"),
    "b0_predictions": (745_968, "6520b9094c89824fa2ed833da7618851536d9a7ce3f74d20d2233c71e34cea4f"),
    "b0_seal": (6_911, "ba9be1a4769d9cd77a8d8b0a11b437ada10254a030656e98cfa7f7f07d7ffee7"),
}

MODELS = ("B0", "B1")
STRATA = ("ALL", "ALS_TARGET_SUPPORTED", "ALS_TARGET_UNSUPPORTED")
TOP_K = (2, 4, 6, 10)
BOOTSTRAP_SAMPLES = 10_000
USER_BOOTSTRAP_SEED = 339
MOVIE_BOOTSTRAP_SEED = 340
SIGN_FLIP_SEED = 341
MIN_USERS = 30
MIN_MOVIES = 30
MIN_ROWS = 200
SCORE_ROWS = 93_230
CATALOG_MOVIES = 85_517
LABEL_ROWS = 18_959
CAP10_TARGETS = 18_646
CAP10_USERS = 270
EXTRA_LABELS = 313
TRAINING_ROWS = 4_997_069
TRAINING_USERS = 39_859


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def pin(path: Path) -> dict[str, Any]:
    path = Path(path)
    require(path.is_file(), f"missing file: {path}")
    return {"bytes": int(path.stat().st_size), "sha256": sha256_file(path)}


def path_pin(path: Path, root: Path = ROOT) -> dict[str, Any]:
    resolved = Path(path).resolve()
    relative = os.path.relpath(resolved, Path(root).resolve()).replace("\\", "/")
    return {"path": relative, **pin(resolved)}


def read_json(path: Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(json_ready(dict(payload)), ensure_ascii=False, indent=2,
                      sort_keys=True, allow_nan=False) + "\n"
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def canonical_inventory_digest(files: Mapping[str, Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    for relative in sorted(files):
        record = files[relative]
        require(set(record) >= {"bytes", "sha256"}, f"incomplete pin: {relative}")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(int(record["bytes"])).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(record["sha256"]).lower().encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def verify_pin(path: Path, expected: Mapping[str, Any], label: str) -> dict[str, Any]:
    actual = pin(path)
    require(int(expected.get("bytes", -1)) == actual["bytes"], f"{label} byte drift")
    require(str(expected.get("sha256", "")).lower() == actual["sha256"], f"{label} SHA-256 drift")
    return actual


def verify_sha(path: Path, expected_sha256: str, label: str) -> dict[str, Any]:
    actual = pin(path)
    require(actual["sha256"] == expected_sha256.lower(), f"{label} SHA-256 drift")
    return actual


def resolve_record_path(record: Mapping[str, Any], *, root: Path, team_root: Path,
                        owner: Path) -> Path:
    raw = record.get("path")
    require(isinstance(raw, str) and raw != "", "pinned reference has no path")
    normalized = raw.replace("\\", "/")
    if normalized.startswith("standalone/"):
        return (root / normalized.removeprefix("standalone/")).resolve()
    if normalized.startswith("team/"):
        return (team_root / normalized.removeprefix("team/")).resolve()
    implementation_aliases = {
        "contract/training-recipe.v1.json": team_root / "pipeline/configs/service-v1/training-recipe.v1.json",
        "contract/service-v1.json": team_root / "pipeline/artifacts/service-v1.json",
        "contract/MODELS.md": team_root / "pipeline/docs/service-v1/MODELS.md",
        "contract/feature-schema.v1.json": team_root / "pipeline/configs/service-v1/feature-schema.v1.json",
        "source/natural-train.parquet": root / "outputs/recommendation-evidence/foundation340/RH/train.parquet",
        "source/tmdb-masked-train.parquet": root / "outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-masked-views-v1/tmdb-masked-rh230.parquet",
        "source/masked-manifest.json": root / "outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-masked-views-v1/manifest.json",
        "source/views-manifest.json": root / "outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-masked-views-v1/views-manifest.json",
        "source/masked-review.json": root / "outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-masked-views-v1-result-review.json",
        "source/natural-score.parquet": root / "outputs/recommendation-evidence/foundation340/RH/score.parquet",
        "implementation/service-v1-b1-spark-runner.md": root / "docs/recommendation/plans/service-v1-b1-spark-runner.md",
        "implementation/run_service_v1_b1_gbt.py": root / "scripts/run_service_v1_b1_gbt.py",
        "implementation/service_v1_b1_spark_worker.py": root / "scripts/service_v1_b1_spark_worker.py",
        "implementation/test_service_v1_b1_gbt_runner.py": root / "tests/test_service_v1_b1_gbt_runner.py",
        "implementation/combination340_models.py": root / "scripts/combination340_models.py",
        "implementation/rec046_common.py": root / "scripts/rec046_common.py",
    }
    if normalized in implementation_aliases:
        return implementation_aliases[normalized].resolve()
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate.resolve()
    root_candidate = (root / candidate).resolve()
    owner_candidate = (owner.parent / candidate).resolve()
    matches = [p for p in (root_candidate, owner_candidate) if p.exists()]
    unique = list(dict.fromkeys(matches))
    require(len(unique) == 1, f"pinned reference path is missing or ambiguous: {raw}")
    return unique[0]


@dataclass(frozen=True)
class EvaluationSpec:
    score_rows: int = SCORE_ROWS
    catalog_movies: int = CATALOG_MOVIES
    label_rows: int = LABEL_ROWS
    cap10_targets: int = CAP10_TARGETS
    cap10_users: int = CAP10_USERS
    extra_labels: int = EXTRA_LABELS
    calibration_users: int = 45
    selection_users: int = 45
    confirmation_users: int = 180
    als_movies: int = 45_074
    training_rows: int = TRAINING_ROWS
    training_users: int = TRAINING_USERS
    minimum_users: int = MIN_USERS
    minimum_movies: int = MIN_MOVIES
    minimum_rows: int = MIN_ROWS
    bootstrap_samples: int = BOOTSTRAP_SAMPLES
    role_digest: str = ROLE_DIGEST


@dataclass(frozen=True)
class EvaluationPaths:
    root: Path
    team_root: Path
    plan: Path
    artifact_manifest: Path
    evaluation_contract: Path
    contexts: Path
    catalog: Path
    labels: Path
    evaluation_seal: Path
    roles: Path
    metadata: Path
    score_axis: Path
    ratings: Path
    b0_predictions: Path
    b0_seal: Path
    als_factor_dir: Path
    score_manifest: Path
    score_review: Path


def production_paths(score_manifest: Path, score_review: Path) -> EvaluationPaths:
    return EvaluationPaths(
        root=ROOT,
        team_root=TEAM_ROOT,
        plan=PLAN,
        artifact_manifest=TEAM_ROOT / "pipeline/artifacts/service-v1.json",
        evaluation_contract=TEAM_ROOT / "pipeline/docs/service-v1/EVALUATION.md",
        contexts=ROOT / "outputs/recommendation-evidence/text339/contexts.json",
        catalog=ROOT / "outputs/recommendation-evidence/text339/catalog.parquet",
        labels=ROOT / "outputs/recommendation-evidence/text339/labels.parquet",
        evaluation_seal=ROOT / "outputs/recommendation-evidence/text339/evaluation-seal.json",
        roles=ROOT / "outputs/recommendation-evidence/final344/roles.csv",
        metadata=ROOT / "outputs/recommendation-evidence/rec-ev-045/metadata.parquet",
        score_axis=ROOT / "outputs/recommendation-evidence/foundation340/RH/score.parquet",
        ratings=ROOT / "outputs/recommendation-evidence/text339/ratings.parquet",
        b0_predictions=ROOT / "outputs/recommendation-evidence/final344/GBT120_s339/predictions.npy",
        b0_seal=ROOT / "outputs/recommendation-evidence/final344/GBT120_s339-seal.json",
        als_factor_dir=ROOT / "outputs/recommendation-evidence/combination340/ALS/item-factors",
        score_manifest=Path(score_manifest).resolve(),
        score_review=Path(score_review).resolve(),
    )


def verify_fixed_nonlabel_inputs(paths: EvaluationPaths, expected_plan_sha256: str,
                                 fixed_pins: Mapping[str, tuple[int, str]] = SOURCE_PINS) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    if paths.plan.resolve() == PLAN.resolve():
        require(expected_plan_sha256.lower() == PLAN_SHA256,
                "production plan pin differs from independently reviewed plan")
    verify_sha(paths.plan, expected_plan_sha256, "reviewed design plan")
    records[os.path.relpath(paths.plan.resolve(), paths.root.resolve()).replace("\\", "/")] = path_pin(paths.plan, paths.root)
    for name in ("artifact_manifest", "evaluation_contract", "contexts", "catalog", "roles",
                 "ratings",
                 "metadata", "score_axis", "b0_predictions", "b0_seal", "evaluation_seal"):
        path = getattr(paths, name)
        size, digest = fixed_pins[name]
        verify_pin(path, {"bytes": size, "sha256": digest}, name)
        records[os.path.relpath(path.resolve(), paths.root.resolve()).replace("\\", "/")] = path_pin(path, paths.root)
    return records


def _bundle_inventory(directory: Path) -> set[str]:
    return {p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()}


def _manifest_file_map(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    files = manifest.get("files")
    if files is None:
        files = manifest.get("artifacts")
    require(isinstance(files, Mapping), "manifest has no file inventory")
    return files


def verify_manifest_inventory(bundle: Path, manifest: Mapping[str, Any], required: set[str]) -> dict[str, dict[str, Any]]:
    actual_names = _bundle_inventory(bundle)
    require(actual_names == required | {"manifest.json"}, "bundle physical inventory drift")
    declared = _manifest_file_map(manifest)
    require(set(declared) == required, "manifest file inventory drift")
    verified: dict[str, dict[str, Any]] = {}
    for name in sorted(required):
        actual = bundle / name
        expected = declared[name]
        require(isinstance(expected, Mapping), f"invalid manifest pin: {name}")
        verify_pin(actual, expected, f"manifest file {name}")
        verified[name] = path_pin(actual, bundle)
    return verified


def _target_pin(review: Mapping[str, Any], key: str, actual: Path, *, root: Path,
                team_root: Path, review_path: Path, phase: str) -> dict[str, Any]:
    target = review.get("target")
    require(isinstance(target, Mapping) and isinstance(target.get(key), Mapping),
            f"{phase} review missing target.{key}")
    record = target[key]
    require(isinstance(record.get("path"), str), f"review target.{key} has no path")
    resolved = resolve_record_path(record, root=root, team_root=team_root, owner=review_path)
    require(resolved == actual.resolve(), f"{phase} review target.{key} path mismatch")
    verify_pin(actual, record, f"{phase} review target.{key}")
    return dict(record)


def canonical_record_set_digest(records: Iterable[Mapping[str, Any]]) -> str:
    normalized: dict[str, dict[str, Any]] = {}
    for record in records:
        path = record.get("path")
        size = record.get("bytes")
        digest = record.get("sha256")
        require(isinstance(path, str) and path and "\0" not in path,
                "invalid canonical record path")
        require(isinstance(size, int) and not isinstance(size, bool) and size >= 0,
                f"invalid canonical record size: {path}")
        require(isinstance(digest, str) and len(digest) == 64,
                f"invalid canonical record hash: {path}")
        require(path not in normalized, f"duplicate canonical record path: {path}")
        normalized[path] = {"bytes": size, "sha256": digest}
    return canonical_inventory_digest(normalized)


def _same_bytes_hash(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    try:
        sizes_equal = int(left.get("bytes", -1)) == int(right.get("bytes", -2))
    except (TypeError, ValueError):
        return False
    return (sizes_equal
            and str(left.get("sha256", "")).lower() == str(right.get("sha256", "")).lower())


def _remember_file(output: dict[str, dict[str, Any]], path: Path, root: Path) -> None:
    record = path_pin(path, root)
    key = str(record["path"])
    require(key not in output or output[key] == record, f"conflicting verified file: {key}")
    output[key] = record


def _verified_record(record: Mapping[str, Any], *, paths: EvaluationPaths,
                     owner: Path, label: str) -> Path:
    resolved = resolve_record_path(record, root=paths.root, team_root=paths.team_root,
                                   owner=owner)
    verify_pin(resolved, record, label)
    return resolved


def verify_record_set_lock(lock: Mapping[str, Any], *, source_key: str,
                           source_digest_key: str, label: str) -> tuple[list[Any], list[Any]]:
    sources = lock.get(source_key)
    controls = lock.get("controlReferences")
    require(isinstance(sources, list) and isinstance(controls, list),
            f"{label} record lists missing")
    source_digest = canonical_record_set_digest(sources)
    control_digest = canonical_record_set_digest(controls)
    require(lock.get(source_digest_key) == source_digest,
            f"{label} source-set digest drift")
    require(lock.get("controlReferenceSetSha256") == control_digest,
            f"{label} control-set digest drift")
    require(lock.get("inputSetSha256") == canonical_record_set_digest([*sources, *controls]),
            f"{label} input-set digest drift")
    return sources, controls


def _record_signature(records: Sequence[Mapping[str, Any]]) -> list[tuple[str, int, str]]:
    return sorted((str(record.get("path")), int(record.get("bytes", -1)),
                   str(record.get("sha256", "")).lower()) for record in records)


def _verify_locked_sources(records: Sequence[Any], *, expected_paths: frozenset[str],
                           paths: EvaluationPaths, owner: Path,
                           label: str) -> tuple[dict[str, Path], dict[str, dict[str, Any]]]:
    canonical_record_set_digest(records)
    mappings: dict[str, Mapping[str, Any]] = {}
    for record in records:
        require(isinstance(record, Mapping), f"{label} contains a non-record")
        logical = record.get("path")
        require(isinstance(logical, str), f"{label} record path missing")
        mappings[logical] = record
    require(set(mappings) == set(expected_paths), f"{label} path set drift")
    resolved: dict[str, Path] = {}
    files: dict[str, dict[str, Any]] = {}
    for logical, record in mappings.items():
        if logical == "runtime/docker-image-id":
            raw = IMAGE_ID.encode("utf-8")
            require(set(record) == {"path", "bytes", "sha256", "value"},
                    f"{label} Docker virtual pin fields drift")
            require(record.get("value") == IMAGE_ID
                    and record.get("bytes") == len(raw)
                    and record.get("sha256") == hashlib.sha256(raw).hexdigest(),
                    f"{label} Docker virtual pin drift")
            continue
        actual = _verified_record(record, paths=paths, owner=owner,
                                  label=f"{label} {logical}")
        require(actual.is_file() and not actual.is_symlink(),
                f"{label} is not a regular file: {logical}")
        require(actual not in resolved.values(), f"{label} aliases one physical file twice")
        resolved[logical] = actual
        _remember_file(files, actual, paths.root)
    if expected_paths == TRAINING_SOURCE_PATHS:
        masked = resolved["source/tmdb-masked-train.parquet"].parent
        require(masked.is_dir() and not masked.is_symlink()
                and not (hasattr(masked, "is_junction") and masked.is_junction()),
                f"{label} masked bundle root is linked or missing")
        children = list(masked.iterdir())
        require({child.name for child in children}
                == {"manifest.json", "tmdb-masked-rh230.parquet", "views-manifest.json"}
                and all(child.is_file() and not child.is_symlink() for child in children),
                f"{label} masked bundle file set drift")
    return resolved, files


def _verify_control_records(records: Sequence[Any], expected_files: Sequence[Path], *,
                            paths: EvaluationPaths, owner: Path,
                            label: str) -> dict[str, dict[str, Any]]:
    canonical_record_set_digest(records)
    expected = {Path(path).resolve() for path in expected_files}
    require(len(expected) == len(expected_files), f"{label} expected file set is duplicated")
    observed: dict[Path, Mapping[str, Any]] = {}
    files: dict[str, dict[str, Any]] = {}
    for record in records:
        require(isinstance(record, Mapping), f"{label} contains a non-record")
        actual = _verified_record(record, paths=paths, owner=owner, label=label)
        require(actual.is_file() and not actual.is_symlink(),
                f"{label} is not a regular file: {actual}")
        require(actual not in observed, f"{label} aliases one physical file twice")
        observed[actual] = record
        _remember_file(files, actual, paths.root)
    require(set(observed) == expected, f"{label} file set drift")
    return files


def _require_review_envelope(review: Mapping[str, Any], phase: str) -> Mapping[str, Any]:
    require(review.get("schemaVersion") == "feelm-service-v1-b1-result-review/1",
            f"{phase} independent review schema drift")
    require(review.get("status") == "PASS", f"{phase} independent review must PASS")
    require(review.get("phase") == phase, f"{phase} independent review phase drift")
    target = review.get("target")
    require(isinstance(target, Mapping), f"{phase} review target map missing")
    return target


def _merge_verified_files(destination: dict[str, dict[str, Any]],
                          incoming: Mapping[str, Mapping[str, Any]], label: str) -> None:
    for key, record in incoming.items():
        require(key not in destination or destination[key] == record,
                f"{label} source collision: {key}")
        destination[key] = dict(record)


def _verify_preflight_ancestry(
    paths: EvaluationPaths,
    *,
    preflight_reference_path: Path,
    preflight_reference: Mapping[str, Any],
    fit_manifest: Mapping[str, Any],
    fit_sources: Sequence[Mapping[str, Any]],
    fit_controls: Sequence[Mapping[str, Any]],
    fit_lock: Mapping[str, Any],
    outer_path: Path,
    worker_path: Path,
    outer_record: Mapping[str, Any],
    worker_record: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    required_reference = {
        "schemaVersion", "preflightManifest", "preflightReview", "reviewedArtifacts",
        "outerRunner", "sparkWorker", "implementationSetSha256",
        "trainingSourceSetSha256", "fitInputSetSha256",
    }
    require(set(preflight_reference) == required_reference,
            "preflight reference field set drift")
    require(preflight_reference.get("schemaVersion")
            == "feelm-service-v1-b1-preflight-reference/1",
            "preflight reference schema drift")
    for key in ("preflightManifest", "preflightReview", "outerRunner", "sparkWorker"):
        require(isinstance(preflight_reference.get(key), Mapping),
                f"preflight reference {key} pin missing")
    require(preflight_reference.get("outerRunner") == outer_record
            and preflight_reference.get("sparkWorker") == worker_record,
            "preflight/fit implementation pin drift")
    require(preflight_reference.get("implementationSetSha256")
            == canonical_record_set_digest([outer_record, worker_record]),
            "preflight implementation digest drift")
    require(preflight_reference.get("trainingSourceSetSha256")
            == fit_lock.get("trainingSourceSetSha256")
            and preflight_reference.get("fitInputSetSha256") == fit_lock.get("inputSetSha256"),
            "preflight reference fit lock digest drift")
    require(preflight_reference.get("reviewedArtifacts") == list(fit_controls),
            "preflight reviewed artifact list drift")

    preflight_manifest_path = _verified_record(
        preflight_reference["preflightManifest"], paths=paths,
        owner=preflight_reference_path, label="preflight reference manifest")
    require(preflight_manifest_path.name == "manifest.json", "preflight manifest basename")
    preflight_bundle = preflight_manifest_path.parent
    require(preflight_bundle.name == RUN_ID + "-preflight", "preflight bundle run ID drift")
    preflight_review_path = _verified_record(
        preflight_reference["preflightReview"], paths=paths,
        owner=preflight_reference_path, label="preflight reference review")
    require(preflight_review_path
            == preflight_bundle.with_name(preflight_bundle.name + "-result-review.json").resolve(),
            "preflight review is not canonical sibling")

    required_bundle = {
        "input-lock.json", "recovery-reference.json", "partition-identity.json",
        "command.json", "resource.json", "run.log",
    }
    preflight_manifest = read_json(preflight_manifest_path)
    require(preflight_manifest.get("schemaVersion")
            == "feelm-service-v1-b1-preflight-manifest/1",
            "preflight manifest schema drift")
    require(preflight_manifest.get("runId") == RUN_ID, "preflight manifest run ID drift")
    require(preflight_manifest.get("status")
            == "B1_FULL_PREFLIGHT_COMPLETE_AWAITING_REVIEW", "preflight manifest status")
    require(preflight_manifest.get("fitAuthorized") is False
            and preflight_manifest.get("modelFitPerformed") is False
            and preflight_manifest.get("scorePerformed") is False,
            "preflight manifest premature authorization")
    verify_manifest_inventory(preflight_bundle, preflight_manifest, required_bundle)

    preflight_review = read_json(preflight_review_path)
    preflight_target = _require_review_envelope(preflight_review, "preflight")
    target_files = {
        "manifest": preflight_manifest_path,
        "input_lock": preflight_bundle / "input-lock.json",
        "recovery_reference": preflight_bundle / "recovery-reference.json",
        "partition_identity": preflight_bundle / "partition-identity.json",
        "command": preflight_bundle / "command.json",
        "resource": preflight_bundle / "resource.json",
        "run_log": preflight_bundle / "run.log",
        "outer_runner": outer_path,
        "spark_worker": worker_path,
    }
    require(set(preflight_target) == set(target_files), "preflight review target set drift")
    for key, actual in target_files.items():
        _target_pin(preflight_review, key, actual, root=paths.root,
                    team_root=paths.team_root, review_path=preflight_review_path,
                    phase="preflight")

    preflight_lock_path = preflight_bundle / "input-lock.json"
    preflight_lock = read_json(preflight_lock_path)
    require(preflight_lock.get("schemaVersion") == "feelm-service-v1-b1-input-lock/1"
            and preflight_lock.get("phase") == "preflight"
            and preflight_lock.get("evaluationTargetsRead") is False,
            "preflight input lock contract drift")
    preflight_sources, preflight_controls = verify_record_set_lock(
        preflight_lock, source_key="trainingSourceRecords",
        source_digest_key="trainingSourceSetSha256", label="preflight input lock")
    require(_record_signature(preflight_sources) == _record_signature(fit_sources),
            "preflight/fit common training source drift")
    _, source_files = _verify_locked_sources(
        preflight_sources, expected_paths=TRAINING_SOURCE_PATHS, paths=paths,
        owner=preflight_lock_path, label="preflight training sources")

    recovery_path = preflight_bundle / "recovery-reference.json"
    recovery = read_json(recovery_path)
    required_recovery = {
        "schemaVersion", "status", "predecessorRunId", "runId", "predecessorFailure",
        "predecessorOuterRunnerSha256", "predecessorSparkWorkerSha256",
        "outerRunnerSha256", "sparkWorkerSha256", "failureClass",
        "modelFitPerformed", "fullPreflightPerformed",
    }
    require(set(recovery) == required_recovery, "recovery reference field set drift")
    require(recovery.get("schemaVersion") == "feelm-service-v1-b1-recovery-reference/1"
            and recovery.get("status") == "PREDECESSOR_FAILURE_VERIFIED",
            "recovery reference schema/status drift")
    predecessor = recovery.get("predecessorFailure")
    require(isinstance(predecessor, Mapping), "recovery predecessor pin missing")
    require(preflight_controls == [predecessor], "preflight predecessor control drift")
    predecessor_path = _verified_record(predecessor, paths=paths,
                                        owner=recovery_path,
                                        label="predecessor failure evidence")
    require(predecessor_path
            == preflight_bundle.parent / (PREDECESSOR_RUN_ID + "-preflight-failure.json"),
            "predecessor failure path drift")
    if paths.root.resolve() == ROOT.resolve():
        require((predecessor.get("bytes"), predecessor.get("sha256"))
                == (PREDECESSOR_FAILURE_BYTES, PREDECESSOR_FAILURE_SHA256),
                "predecessor failure canonical pin drift")
    predecessor_payload = read_json(predecessor_path)
    run = predecessor_payload.get("containerRun")
    docker_state = run.get("dockerState") if isinstance(run, Mapping) else None
    require(predecessor_payload.get("schemaVersion") == "feelm-service-v1-b1-failure/1"
            and predecessor_payload.get("phase") == "preflight"
            and predecessor_payload.get("status") == "FAILED"
            and predecessor_payload.get("cleanupComplete") is True,
            "predecessor failure envelope drift")
    require(isinstance(run, Mapping) and isinstance(docker_state, Mapping)
            and docker_state.get("ExitCode") == 1
            and docker_state.get("OOMKilled") is False
            and run.get("timedOut") is False
            and "masked bundle file set is not exactly the reviewed three files"
            in str(run.get("stdout", "")),
            "predecessor was not the reviewed mount-wiring failure")
    require(recovery.get("predecessorRunId") == PREDECESSOR_RUN_ID
            and recovery.get("runId") == RUN_ID
            and recovery.get("predecessorOuterRunnerSha256")
            == PREDECESSOR_OUTER_RUNNER_SHA256
            and recovery.get("predecessorSparkWorkerSha256")
            == PREDECESSOR_SPARK_WORKER_SHA256
            and recovery.get("outerRunnerSha256") == outer_record.get("sha256")
            and recovery.get("sparkWorkerSha256") == worker_record.get("sha256")
            and recovery.get("failureClass") == "MASKED_BUNDLE_CONTAINER_MOUNT_WIRING"
            and recovery.get("modelFitPerformed") is False
            and recovery.get("fullPreflightPerformed") is False,
            "recovery reference lineage drift")

    require(preflight_manifest.get("trainingSourceSetSha256")
            == preflight_lock.get("trainingSourceSetSha256")
            and preflight_manifest.get("controlReferenceSetSha256")
            == preflight_lock.get("controlReferenceSetSha256")
            and preflight_manifest.get("inputSetSha256") == preflight_lock.get("inputSetSha256"),
            "preflight manifest/input-lock digest drift")
    require(preflight_target["input_lock"].get("inputSetSha256")
            == preflight_lock.get("inputSetSha256")
            and preflight_target["input_lock"].get("trainingSourceSetSha256")
            == preflight_lock.get("trainingSourceSetSha256"),
            "preflight review omitted input-lock digests")
    require(preflight_manifest.get("predecessorFailureSha256") == predecessor.get("sha256")
            and preflight_manifest.get("recoveryReferenceSha256") == sha256_file(recovery_path),
            "preflight recovery manifest link drift")
    require(preflight_manifest.get("outerRunnerSha256") == outer_record.get("sha256")
            and preflight_manifest.get("sparkWorkerSha256") == worker_record.get("sha256")
            and preflight_manifest.get("implementationSetSha256")
            == preflight_reference.get("implementationSetSha256"),
            "preflight implementation manifest link drift")
    require(fit_manifest.get("preflightManifestSha256")
            == preflight_reference["preflightManifest"].get("sha256")
            and fit_manifest.get("preflightReviewSha256")
            == preflight_reference["preflightReview"].get("sha256"),
            "fit manifest preflight chain drift")

    expected_controls = sorted((path for path in preflight_bundle.rglob("*") if path.is_file()),
                               key=lambda path: path.as_posix()) + [preflight_review_path]
    control_files = _verify_control_records(
        fit_controls, expected_controls, paths=paths, owner=preflight_reference_path,
        label="fit preflight controls")
    files: dict[str, dict[str, Any]] = {}
    _merge_verified_files(files, source_files, "preflight")
    _merge_verified_files(files, control_files, "preflight")
    _remember_file(files, predecessor_path, paths.root)
    for actual in [preflight_manifest_path, preflight_review_path, *target_files.values()]:
        _remember_file(files, actual, paths.root)
    return {"manifest": preflight_manifest, "review": preflight_review,
            "inputLock": preflight_lock, "recovery": recovery,
            "bundlePath": preflight_bundle}, files


def verify_fit_reference(paths: EvaluationPaths, fit_reference_path: Path,
                         fit_reference: Mapping[str, Any], score_manifest: Mapping[str, Any],
                         score_target: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Prove the score refers to one exact, independently reviewed fit and model tree set."""
    require(fit_reference.get("schemaVersion") == "feelm-service-v1-b1-fit-reference/1",
            "fit reference schema drift")
    required_reference = {"fitManifest", "fitReview", "modelInventory", "modelFileSetSha256",
                          "modelFiles", "outerRunner", "sparkWorker", "implementationSetSha256",
                          "schemaVersion"}
    require(set(fit_reference) == required_reference, "fit reference field set drift")
    for key in ("fitManifest", "fitReview", "modelInventory", "outerRunner", "sparkWorker"):
        require(isinstance(fit_reference.get(key), Mapping), f"fit reference {key} pin missing")

    fit_manifest_path = _verified_record(fit_reference["fitManifest"], paths=paths,
                                         owner=fit_reference_path, label="fit reference manifest")
    require(fit_manifest_path.name == "manifest.json", "fit manifest basename")
    fit_bundle = fit_manifest_path.parent
    require(fit_bundle.name == RUN_ID + "-fit", "fit bundle run ID drift")
    fit_review_path = _verified_record(fit_reference["fitReview"], paths=paths,
                                       owner=fit_reference_path, label="fit reference review")
    canonical_review = fit_bundle.with_name(fit_bundle.name + "-result-review.json").resolve()
    require(fit_review_path == canonical_review, "fit review is not canonical sibling")
    inventory_path = _verified_record(fit_reference["modelInventory"], paths=paths,
                                      owner=fit_reference_path, label="fit model inventory")
    require(inventory_path == (fit_bundle / "model-file-inventory.json").resolve(),
            "fit model inventory path mismatch")
    outer_path = _verified_record(fit_reference["outerRunner"], paths=paths,
                                  owner=fit_reference_path, label="fit reference outer runner")
    worker_path = _verified_record(fit_reference["sparkWorker"], paths=paths,
                                   owner=fit_reference_path, label="fit reference Spark worker")

    manifest = read_json(fit_manifest_path)
    review = read_json(fit_review_path)
    inventory = read_json(inventory_path)
    require(manifest.get("schemaVersion") == "feelm-service-v1-b1-fit-manifest/1",
            "fit manifest schema drift")
    require(manifest.get("runId") == RUN_ID, "fit manifest run ID drift")
    require(manifest.get("status") == "B1_MODEL_FIT_COMPLETE_AUDIT_PENDING",
            "fit manifest status")
    require(manifest.get("scoringAuthorized") is False
            and manifest.get("readyForService") is False
            and manifest.get("modelFitPerformed") is True
            and manifest.get("scorePerformed") is False,
            "fit manifest premature authorization")
    fit_target = _require_review_envelope(review, "fit")
    fixed_target_files = {
        "manifest": fit_manifest_path,
        "input_lock": fit_bundle / "input-lock.json",
        "preflight_reference": fit_bundle / "preflight-reference.json",
        "command": fit_bundle / "command.json",
        "partition_identity": fit_bundle / "partition-identity.json",
        "resolved_estimator": fit_bundle / "resolved-estimator.json",
        "model_file_inventory": inventory_path,
        "threshold_fixtures": fit_bundle / "threshold-fixtures.npz",
        "fit_metrics": fit_bundle / "fit-metrics.json",
        "resource": fit_bundle / "resource.json",
        "run_log": fit_bundle / "run.log",
        "outer_runner": outer_path,
        "spark_worker": worker_path,
    }
    require(set(fit_target) == set(fixed_target_files), "fit review target set drift")
    for key, actual in fixed_target_files.items():
        _target_pin(review, key, actual, root=paths.root, team_root=paths.team_root,
                    review_path=fit_review_path, phase="fit")

    model_records = inventory.get("files")
    require(inventory.get("schemaVersion") == "feelm-service-v1-b1-model-inventory/1",
            "model inventory schema drift")
    require(isinstance(model_records, list) and model_records, "model inventory files missing")
    require(fit_reference.get("modelFiles") == model_records,
            "fit reference/model inventory file list mismatch")
    relative_names = [record.get("path") for record in model_records if isinstance(record, Mapping)]
    require(len(relative_names) == len(model_records)
            and all(isinstance(name, str) and name for name in relative_names),
            "invalid native model file record")
    require(relative_names == sorted(relative_names) and len(set(relative_names)) == len(relative_names),
            "native model inventory order or uniqueness drift")
    model_root = (fit_bundle / "model/native").resolve()
    actual_model_names = sorted(p.relative_to(model_root).as_posix()
                                for p in model_root.rglob("*") if p.is_file())
    require(actual_model_names == relative_names, "native model physical inventory drift")
    native_files: list[Path] = []
    for record in model_records:
        relative = Path(str(record["path"]))
        require(not relative.is_absolute() and ".." not in relative.parts,
                "unsafe native model path")
        actual = (model_root / relative).resolve()
        require(actual.is_relative_to(model_root), "native model path escapes model root")
        verify_pin(actual, record, f"native model {record['path']}")
        native_files.append(actual)
    model_digest = canonical_record_set_digest(model_records)
    require(inventory.get("inventorySha256") == model_digest,
            "model inventory canonical digest drift")
    require(fit_reference.get("modelFileSetSha256") == model_digest,
            "fit reference model file-set digest drift")

    required_bundle = {"input-lock.json", "preflight-reference.json", "command.json",
                       "partition-identity.json", "resolved-estimator.json",
                       "model-file-inventory.json", "threshold-fixtures.npz", "fit-metrics.json",
                       "resource.json", "run.log"} | {"model/native/" + name for name in relative_names}
    verify_manifest_inventory(fit_bundle, manifest, required_bundle)
    fit_input_lock = read_json(fit_bundle / "input-lock.json")
    require(fit_input_lock.get("schemaVersion") == "feelm-service-v1-b1-input-lock/1"
            and fit_input_lock.get("phase") == "fit", "fit input lock contract drift")
    fit_sources, fit_controls = verify_record_set_lock(
        fit_input_lock, source_key="trainingSourceRecords",
        source_digest_key="trainingSourceSetSha256", label="fit input lock")
    require(fit_input_lock.get("evaluationTargetsRead") is False,
            "fit input lock permits evaluation targets")
    _, fit_source_files = _verify_locked_sources(
        fit_sources, expected_paths=TRAINING_SOURCE_PATHS, paths=paths,
        owner=fit_bundle / "input-lock.json", label="fit training sources")
    require(manifest.get("inputSetSha256") == fit_input_lock.get("inputSetSha256")
            == fit_target["input_lock"].get("inputSetSha256"), "fit input-set digest drift")
    require(manifest.get("trainingSourceSetSha256") == fit_input_lock.get("trainingSourceSetSha256")
            == fit_target["input_lock"].get("trainingSourceSetSha256"),
            "fit training-source digest drift")
    require(manifest.get("controlReferenceSetSha256")
            == fit_input_lock.get("controlReferenceSetSha256"),
            "fit control-reference digest drift")
    require(fit_target["input_lock"].get("controlReferenceSetSha256") in
            (None, fit_input_lock.get("controlReferenceSetSha256")),
            "fit review control-reference digest drift")
    for reference_key in ("outerRunner", "sparkWorker"):
        expected = fit_reference[reference_key]
        require(any(isinstance(record, Mapping)
                    and record.get("path") == expected.get("path")
                    and _same_bytes_hash(record, expected) for record in fit_sources),
                f"fit input lock omitted {reference_key}")

    implementation = canonical_record_set_digest(
        [fit_reference["outerRunner"], fit_reference["sparkWorker"]])
    require(fit_reference.get("implementationSetSha256") == implementation,
            "fit reference implementation digest drift")
    require(manifest.get("outerRunnerSha256") == fit_reference["outerRunner"].get("sha256")
            and manifest.get("sparkWorkerSha256") == fit_reference["sparkWorker"].get("sha256")
            and manifest.get("implementationSetSha256") == implementation,
            "fit manifest implementation chain drift")
    require(manifest.get("modelInventorySha256") == fit_reference["modelInventory"].get("sha256")
            and manifest.get("modelFileSetSha256") == model_digest,
            "fit manifest model inventory chain drift")
    preflight_reference_path = fit_bundle / "preflight-reference.json"
    preflight_reference = read_json(preflight_reference_path)
    preflight_chain, preflight_files = _verify_preflight_ancestry(
        paths,
        preflight_reference_path=preflight_reference_path,
        preflight_reference=preflight_reference,
        fit_manifest=manifest,
        fit_sources=fit_sources,
        fit_controls=fit_controls,
        fit_lock=fit_input_lock,
        outer_path=outer_path,
        worker_path=worker_path,
        outer_record=fit_reference["outerRunner"],
        worker_record=fit_reference["sparkWorker"],
    )
    require(score_manifest.get("fitManifestSha256") == fit_reference["fitManifest"].get("sha256")
            and score_manifest.get("fitReviewSha256") == fit_reference["fitReview"].get("sha256")
            and score_manifest.get("modelInventorySha256") == fit_reference["modelInventory"].get("sha256")
            and score_manifest.get("modelFileSetSha256") == model_digest,
            "score manifest fit/model chain drift")
    for key, reference_key, actual in (("outer_runner", "outerRunner", outer_path),
                                        ("spark_worker", "sparkWorker", worker_path)):
        score_record = score_target.get(key)
        require(isinstance(score_record, Mapping) and _same_bytes_hash(score_record,
                                                                       fit_reference[reference_key]),
                f"score/fit {key} pin drift")
        resolved = _verified_record(score_record, paths=paths, owner=paths.score_review,
                                    label=f"score review target.{key}")
        require(resolved == actual, f"score/fit {key} path drift")
        fit_review_record = fit_target[key]
        require(_same_bytes_hash(fit_review_record, fit_reference[reference_key]),
                f"fit review/reference {key} pin drift")

    files: dict[str, dict[str, Any]] = {}
    for actual in [fit_manifest_path, fit_review_path, *fixed_target_files.values(), *native_files]:
        _remember_file(files, actual, paths.root)
    _merge_verified_files(files, fit_source_files, "fit")
    _merge_verified_files(files, preflight_files, "fit/preflight")
    return {"manifest": manifest, "review": review, "inventory": inventory,
            "inputLock": fit_input_lock, "preflight": preflight_chain,
            "bundlePath": fit_bundle}, files


def verify_score_chain(paths: EvaluationPaths, expected_manifest_sha256: str,
                       expected_review_sha256: str, spec: EvaluationSpec) -> tuple[dict[str, Any], dict[str, dict[str, Any]], Path]:
    """Validate the independent score audit before any label file is opened."""
    verify_sha(paths.score_manifest, expected_manifest_sha256, "score manifest")
    verify_sha(paths.score_review, expected_review_sha256, "score review")
    require(paths.score_manifest.name == "manifest.json", "score manifest basename")
    bundle = paths.score_manifest.parent
    require(bundle.name == RUN_ID + "-score", "score bundle run ID drift")
    require(paths.score_review.resolve()
            == bundle.with_name(bundle.name + "-result-review.json").resolve(),
            "score review is not canonical sibling")
    required = {
        "fit-reference.json", "score-input-lock.json", "command.json",
        "score/analyzed-plan.txt", "score/predictions.parquet", "resource.json", "run.log",
    }
    manifest = read_json(paths.score_manifest)
    review = read_json(paths.score_review)
    require(manifest.get("schemaVersion") == "feelm-service-v1-b1-score-manifest/1",
            "score manifest schema drift")
    require(manifest.get("runId") == RUN_ID, "score manifest run ID drift")
    require(manifest.get("status") == "B1_NATURAL_SCORE_COMPLETE_AUDIT_PENDING", "score manifest status")
    require(manifest.get("evaluationAuthorized") is False
            and manifest.get("readyForService") is False
            and manifest.get("modelFitPerformed") is False
            and manifest.get("scorePerformed") is True,
            "score manifest premature authorization")
    target = _require_review_envelope(review, "score")
    verify_manifest_inventory(bundle, manifest, required)
    target_files = {
        "manifest": paths.score_manifest,
        "fit_reference": bundle / "fit-reference.json",
        "score_input_lock": bundle / "score-input-lock.json",
        "command": bundle / "command.json",
        "analyzed_plan": bundle / "score/analyzed-plan.txt",
        "predictions": bundle / "score/predictions.parquet",
        "resource": bundle / "resource.json",
        "run_log": bundle / "run.log",
    }
    require(set(target) == set(target_files) | {"outer_runner", "spark_worker"},
            "score review target set drift")
    for key, path in target_files.items():
        _target_pin(review, key, path, root=paths.root, team_root=paths.team_root,
                    review_path=paths.score_review, phase="score")
    fit_reference_path = bundle / "fit-reference.json"
    fit_reference = read_json(fit_reference_path)
    fit_chain, fit_files = verify_fit_reference(paths, fit_reference_path, fit_reference,
                                                manifest, target)
    score_input_lock = read_json(bundle / "score-input-lock.json")
    require(score_input_lock.get("schemaVersion") == "feelm-service-v1-b1-score-input-lock/1"
            and score_input_lock.get("phase") == "score"
            and score_input_lock.get("evaluationTargetsRead") is False,
            "score input lock contract drift")
    require(manifest.get("inputSetSha256") == score_input_lock.get("inputSetSha256")
            == target["score_input_lock"].get("inputSetSha256"),
            "score input-set digest drift")
    require(manifest.get("scoreSourceSetSha256") == score_input_lock.get("scoreSourceSetSha256")
            and manifest.get("controlReferenceSetSha256")
            == score_input_lock.get("controlReferenceSetSha256"),
            "score source/control digest drift")
    require(target["score_input_lock"].get("scoreSourceSetSha256")
            == score_input_lock.get("scoreSourceSetSha256")
            and target["score_input_lock"].get("controlReferenceSetSha256") in
            (None, score_input_lock.get("controlReferenceSetSha256")),
            "score review omitted input-lock digests")
    require(manifest.get("implementationSetSha256") == fit_reference.get("implementationSetSha256")
            and manifest.get("outerRunnerSha256") == fit_reference["outerRunner"].get("sha256")
            and manifest.get("sparkWorkerSha256") == fit_reference["sparkWorker"].get("sha256"),
            "score manifest implementation chain drift")
    sources, score_controls = verify_record_set_lock(
        score_input_lock, source_key="scoreSourceRecords",
        source_digest_key="scoreSourceSetSha256", label="score input lock")
    _, score_source_files = _verify_locked_sources(
        sources, expected_paths=SCORE_SOURCE_PATHS, paths=paths,
        owner=bundle / "score-input-lock.json", label="score sources")
    for reference_key in ("outerRunner", "sparkWorker"):
        expected = fit_reference[reference_key]
        require(any(isinstance(record, Mapping)
                    and record.get("path") == expected.get("path")
                    and _same_bytes_hash(record, expected)
                    for record in sources), f"score input lock omitted {reference_key}")
    source_files: dict[str, dict[str, Any]] = {}
    for path in [*target_files.values(), paths.score_review]:
        _remember_file(source_files, path, paths.root)
    for key in ("outer_runner", "spark_worker"):
        resolved = _verified_record(target[key], paths=paths, owner=paths.score_review,
                                    label=f"score review target.{key}")
        _remember_file(source_files, resolved, paths.root)
    for key, record in fit_files.items():
        require(key not in source_files or source_files[key] == record,
                f"score/fit source collision: {key}")
        source_files[key] = record
    _merge_verified_files(source_files, score_source_files, "score")
    fit_manifest_path = resolve_record_path(
        fit_reference["fitManifest"], root=paths.root, team_root=paths.team_root,
        owner=fit_reference_path)
    fit_review_path = resolve_record_path(
        fit_reference["fitReview"], root=paths.root, team_root=paths.team_root,
        owner=fit_reference_path)
    expected_fit_controls = sorted(
        (path for path in fit_manifest_path.parent.rglob("*") if path.is_file()),
        key=lambda path: path.as_posix()) + [fit_review_path]
    score_control_files = _verify_control_records(
        score_controls, expected_fit_controls, paths=paths,
        owner=bundle / "score-input-lock.json", label="score fit controls")
    _merge_verified_files(source_files, score_control_files, "score")
    predictions = bundle / "score/predictions.parquet"
    require(predictions.is_file() and not predictions.is_dir(), "B1 predictions must be one physical Parquet file")
    schema = pq.read_schema(predictions)
    expected_schema = pa.schema([("row_id", pa.int64()), ("uid", pa.int32()), ("prediction", pa.float64())])
    require(schema.equals(expected_schema, check_metadata=False), "B1 prediction schema drift")
    metadata = pq.ParquetFile(predictions).metadata
    require(metadata.num_rows == spec.score_rows, "B1 prediction row count drift")
    require(manifest.get("predictionSha256") == sha256_file(predictions),
            "score manifest prediction hash drift")
    census = manifest.get("scoreCensus")
    require(isinstance(census, Mapping) and census.get("rows") == spec.score_rows
            and census.get("rowIdMinimum") == 0 and census.get("rowIdMaximum") == spec.score_rows - 1
            and census.get("rowIdsUnique") is True and census.get("uidAxisEqual") is True
            and census.get("predictionsFinite") is True
            and census.get("singlePhysicalParquetFile") is True,
            "score manifest score census drift")
    require(manifest.get("labelProjected") is False, "score manifest projects evaluation label")
    return {"manifest": manifest, "review": review, "fitReference": fit_reference,
            "fit": fit_chain, "bundlePath": bundle}, source_files, predictions


def verify_als_axis(paths: EvaluationPaths, spec: EvaluationSpec) -> tuple[set[int], dict[str, dict[str, Any]]]:
    manifest = read_json(paths.artifact_manifest)
    prefix = "outputs/recommendation-evidence/combination340/ALS/item-factors/"
    declared = {str(a["source_path"]): {"bytes": int(a["bytes"]), "sha256": str(a["sha256"])}
                for a in manifest.get("artifacts", []) if str(a.get("source_path", "")).startswith(prefix)}
    require(bool(declared), "artifact manifest has no ALS factor inventory")
    actual = {prefix + p.relative_to(paths.als_factor_dir).as_posix(): p
              for p in paths.als_factor_dir.rglob("*") if p.is_file()}
    require(set(actual) == set(declared), "ALS factor physical inventory drift")
    inventory: dict[str, dict[str, Any]] = {}
    parts: list[Path] = []
    for relative, path in sorted(actual.items()):
        verify_pin(path, declared[relative], f"ALS factor {relative}")
        key = os.path.relpath(path.resolve(), paths.root.resolve()).replace("\\", "/")
        inventory[key] = path_pin(path, paths.root)
        if path.name.startswith("part-") and path.suffix == ".parquet":
            parts.append(path)
    require(bool(parts), "ALS factor Parquet parts missing")
    table = pads.dataset([str(p) for p in parts], format="parquet").to_table(columns=["id"])
    ids = np.asarray(table.column("id").to_numpy(), dtype=np.int64)
    require(len(ids) == spec.als_movies and len(np.unique(ids)) == spec.als_movies,
            "ALS factor movie census drift")
    return set(map(int, ids)), inventory


def split_roles(frame: pd.DataFrame, spec: EvaluationSpec) -> tuple[dict[int, str], str]:
    require(list(frame.columns) == ["uid", "h10", "role"], "role schema drift")
    require(not frame.uid.isna().any() and not frame.uid.duplicated().any(), "one role per uid")
    require(set(frame.role) == {"calibration", "comparison"}, "unknown original role")
    calibration = frame.loc[frame.role.eq("calibration"), "uid"].astype(np.int64).tolist()
    comparison = frame.loc[frame.role.eq("comparison"), "uid"].astype(np.int64).tolist()
    require(len(calibration) == spec.calibration_users + spec.selection_users, "original calibration census")
    require(len(comparison) == spec.confirmation_users, "original comparison census")
    ordered = sorted(calibration, key=lambda uid: (hashlib.sha256(
        f"cal-select-v1:{int(uid)}".encode("utf-8")).digest(), int(uid)))
    roles = {int(uid): "CALIBRATION" if i < spec.calibration_users else "SELECTION"
             for i, uid in enumerate(ordered)}
    roles.update({int(uid): "CONFIRMATION" for uid in comparison})
    require(len(roles) == spec.cap10_users, "derived role census")
    canonical = "".join(f"{uid},{roles[uid]}\n" for uid in sorted(roles)).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    require(digest == spec.role_digest, "role membership digest drift")
    counts = pd.Series(list(roles.values())).value_counts().to_dict()
    require(counts == {"CONFIRMATION": spec.confirmation_users,
                       "CALIBRATION": spec.calibration_users,
                       "SELECTION": spec.selection_users}, "derived role counts")
    return roles, digest


def validate_training_user_disjointness(path: Path, roles: Mapping[int, str],
                                        spec: EvaluationSpec) -> dict[str, Any]:
    """Read only the training uid column and prove all evaluation users are unseen."""
    table = pq.read_table(path, columns=["uid"])
    require(table.schema.equals(pa.schema([("uid", pa.int32())]), check_metadata=False),
            "training ratings uid projection schema drift")
    require(table.num_rows == spec.training_rows, "training ratings row census drift")
    require(table.column("uid").null_count == 0, "training ratings uid contains null")
    uid = np.asarray(table.column("uid").to_numpy(), dtype=np.int32)
    unique = np.unique(uid)
    require(len(unique) == spec.training_users, "training ratings unique-user census drift")
    evaluation_uid = np.fromiter(sorted(roles), dtype=np.int64, count=len(roles))
    overlap = np.intersect1d(unique.astype(np.int64, copy=False), evaluation_uid, assume_unique=True)
    require(len(overlap) == 0, "training/evaluation user overlap")
    return {
        "ratingsRows": len(uid),
        "ratingsUniqueUsers": len(unique),
        "evaluationRoleUsers": len(roles),
        "trainingEvaluationUidIntersection": 0,
        "projectedColumns": ["uid"],
        "uidType": "int32",
    }


def write_role_membership(path: Path, roles: Mapping[int, str]) -> None:
    with path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["uid", "role"])
        writer.writerows((uid, roles[uid]) for uid in sorted(roles))
        handle.flush()
        os.fsync(handle.fileno())


def valid_half_stars(values: np.ndarray) -> bool:
    values = np.asarray(values, dtype=np.float64)
    return bool(np.isfinite(values).all() and ((values >= .5) & (values <= 5)).all()
                and np.equal(values * 2, np.rint(values * 2)).all())


def load_predictions(path: Path, score_axis_path: Path, spec: EvaluationSpec) -> pd.DataFrame:
    table = pq.read_table(path)
    expected_schema = pa.schema([("row_id", pa.int64()), ("uid", pa.int32()), ("prediction", pa.float64())])
    require(table.schema.equals(expected_schema, check_metadata=False), "B1 prediction decoded schema drift")
    frame = table.to_pandas()
    row_id = frame.row_id.to_numpy(np.int64, copy=False)
    pred = frame.prediction.to_numpy(np.float64, copy=False)
    require(len(frame) == spec.score_rows and np.array_equal(row_id, np.arange(spec.score_rows)),
            "B1 prediction row identity drift")
    require(np.isfinite(pred).all(), "non-finite B1 predictions")
    score_axis = pq.read_table(score_axis_path, columns=["row_id", "uid"])
    axis_schema = pa.schema([("row_id", pa.int64()), ("uid", pa.int32())])
    require(score_axis.schema.equals(axis_schema, check_metadata=False), "natural score identity schema drift")
    natural_row_id = np.asarray(score_axis.column("row_id").to_numpy(), dtype=np.int64)
    natural_uid = np.asarray(score_axis.column("uid").to_numpy(), dtype=np.int32)
    require(len(natural_row_id) == spec.score_rows
            and np.array_equal(natural_row_id, np.arange(spec.score_rows)),
            "natural score row_id must be unique strict 0..93229")
    require(np.array_equal(frame.uid.to_numpy(np.int32, copy=False), natural_uid),
            "B1 prediction (row_id,uid) differs from natural score axis")
    return frame


def prepare_truth(paths: EvaluationPaths, spec: EvaluationSpec, roles: Mapping[int, str],
                  als_ids: set[int], b1_scores: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Open and join sealed labels after all upstream gates have passed."""
    seal = read_json(paths.evaluation_seal)
    label_record = seal.get("files", {}).get("labels.parquet")
    require(isinstance(label_record, Mapping), "evaluation seal does not pin labels")
    verify_pin(paths.labels, label_record, "labels pinned by evaluation seal")
    require(seal.get("labels_previously_opened") is True, "labels previous-open disclosure missing")

    catalog_table = pq.read_table(paths.catalog)
    require(catalog_table.num_rows == spec.catalog_movies and "movie_id" in catalog_table.column_names,
            "catalog axis drift")
    catalog_ids = np.asarray(catalog_table.column("movie_id").to_numpy(), dtype=np.int64)
    require(len(np.unique(catalog_ids)) == spec.catalog_movies, "catalog movie IDs not unique")
    metadata_ids = np.asarray(pq.read_table(paths.metadata, columns=["movie_id"]).column("movie_id").to_numpy(),
                              dtype=np.int64)
    require(np.array_equal(metadata_ids, catalog_ids), "metadata/catalog movie axis drift")

    contexts = read_json(paths.contexts)
    cap10 = [c for c in contexts if int(c.get("cap", -1)) == 10]
    require(len(cap10) == spec.cap10_users and len({int(c["uid"]) for c in cap10}) == spec.cap10_users,
            "cap10 context census drift")
    require({int(c["uid"]) for c in cap10} == set(roles), "roles/cap10 user identity drift")
    b0 = np.load(paths.b0_predictions, allow_pickle=False)
    require(isinstance(b0, np.ndarray) and b0.shape == (spec.score_rows,) and np.isfinite(b0).all(),
            "B0 prediction axis drift")
    b0_seal = read_json(paths.b0_seal)
    b0_record = b0_seal.get("files", {}).get("GBT120_s339/predictions.npy")
    require(isinstance(b0_record, Mapping), "B0 seal does not pin raw predictions")
    verify_pin(paths.b0_predictions, b0_record, "B0 raw prediction seal")

    target_parts: list[pd.DataFrame] = []
    for context in sorted(cap10, key=lambda item: int(item["uid"])):
        uid = int(context["uid"])
        ei = np.asarray(context.get("ei", []), dtype=np.int64)
        oi = np.asarray(context.get("oi", []), dtype=np.int64)
        start, stop = int(context["start"]), int(context["stop"])
        require(stop >= start and len(ei) == stop - start, f"context row span drift for uid {uid}")
        require(((ei >= 0) & (ei < spec.catalog_movies)).all(), f"illegal catalog index for uid {uid}")
        require(len(np.unique(ei)) == len(ei), f"duplicate target movie for uid {uid}")
        require(not np.intersect1d(ei, oi).size, f"history movie leaked into targets for uid {uid}")
        row_ids = np.arange(start, stop, dtype=np.int64)
        require(stop <= spec.score_rows, f"context score span out of range for uid {uid}")
        scored = b1_scores.iloc[start:stop]
        require(np.array_equal(scored.row_id.to_numpy(np.int64), row_ids), f"B1 row span drift for uid {uid}")
        require((scored.uid.to_numpy(np.int64) == uid).all(), f"B1 uid drift for uid {uid}")
        movie_ids = catalog_ids[ei]
        target_parts.append(pd.DataFrame({
            "row_id": row_ids,
            "uid": np.full(len(ei), uid, dtype=np.int64),
            "movie_id": movie_ids,
            "role": roles[uid],
            # B0 has no ID column; the sealed contract defines array position as
            # natural score row_id, which was just proven strict 0..N-1.
            "b0_raw": b0[row_ids].astype(np.float64, copy=False),
            "b1_raw": scored.prediction.to_numpy(np.float64, copy=False),
            "als_supported": np.fromiter((int(mid) in als_ids for mid in movie_ids), bool, len(movie_ids)),
        }))
    targets = pd.concat(target_parts, ignore_index=True)
    require(len(targets) == spec.cap10_targets, "cap10 target row census drift")
    require(not targets.duplicated(["uid", "movie_id"]).any(), "cap10 target key duplicates")

    label_table = pq.read_table(paths.labels)
    expected_schema = pa.schema([("uid", pa.int64()), ("movie_id", pa.int64()), ("rating", pa.float64())])
    require(label_table.schema.equals(expected_schema, check_metadata=False), "label schema drift")
    labels = label_table.to_pandas()
    require(len(labels) == spec.label_rows and not labels.duplicated(["uid", "movie_id"]).any(),
            "label key/census drift")
    require(valid_half_stars(labels.rating.to_numpy(np.float64)), "labels are not finite half-stars")
    joined = targets.merge(labels, on=["uid", "movie_id"], how="left", validate="one_to_one", indicator=True)
    require(len(joined) == spec.cap10_targets and joined._merge.eq("both").all()
            and joined.rating.notna().all(), "cap10 truth join must be complete")
    joined = joined.drop(columns="_merge")
    target_keys = pd.MultiIndex.from_frame(targets[["uid", "movie_id"]])
    label_keys = pd.MultiIndex.from_frame(labels[["uid", "movie_id"]])
    extras = int((~label_keys.isin(target_keys)).sum())
    require(extras == spec.extra_labels, "non-cap10 label census drift")
    counts = joined.role.value_counts().to_dict()
    require(counts and set(counts) == {"CALIBRATION", "SELECTION", "CONFIRMATION"}, "truth role coverage")
    key_bytes = "".join(f"{r.uid},{r.movie_id}\n" for r in joined.sort_values(["uid", "movie_id"]).itertuples()).encode()
    integrity = {
        "schemaVersion": "feelm-service-v1-b1-truth-integrity/1",
        "labelsPreviouslyOpened": True,
        "freshBlindHoldout": False,
        "labelsRows": len(labels),
        "labelsUniqueKeys": len(label_keys),
        "cap10Contexts": len(cap10),
        "cap10TargetRows": len(targets),
        "cap10UniqueKeys": len(target_keys),
        "joinedRows": len(joined),
        "missingRows": 0,
        "duplicateRows": 0,
        "excludedLabelRows": extras,
        "excludedReason": "NOT_CAP10_TARGET_KEY",
        "targetKeySha256": hashlib.sha256(key_bytes).hexdigest(),
        "roleRows": {key: int(value) for key, value in sorted(counts.items())},
        "joinKey": ["uid", "catalog.movie_id[ei]"],
    }
    return joined, integrity


def fit_affine_user_equal(frame: pd.DataFrame, raw_column: str) -> dict[str, Any]:
    require(not frame.empty and not frame.duplicated(["uid", "movie_id"]).any(), "calibration rows")
    x = frame[raw_column].to_numpy(np.float64)
    y = frame.rating.to_numpy(np.float64)
    require(np.isfinite(x).all() and valid_half_stars(y), "finite calibration axis")
    user_counts = frame.groupby("uid", sort=True).size()
    require((user_counts > 0).all(), "positive calibration user rows")
    weights = 1.0 / frame.uid.map(user_counts).to_numpy(np.float64)
    weights /= weights.sum()
    mx, my = float(weights @ x), float(weights @ y)
    variance = float(weights @ np.square(x - mx))
    covariance = float(weights @ ((x - mx) * (y - my)))
    base = {"users": int(frame.uid.nunique()), "rows": len(frame), "variance": variance,
            "covariance": covariance, "a": None, "b": None}
    if variance <= 1e-12:
        return {**base, "state": "CALIBRATION_UNAVAILABLE", "reason": "RAW_VARIANCE_LE_1E-12"}
    slope = max(0.0, covariance / variance)
    return {**base, "state": "AFFINE", "reason": None,
            "a": float(my - slope * mx), "b": float(slope)}


def apply_affine(raw: np.ndarray, record: Mapping[str, Any]) -> np.ndarray:
    require(record.get("state") == "AFFINE" and record.get("a") is not None and record.get("b") is not None,
            "affine calibrator unavailable")
    require(float(record["b"]) >= 0 and np.isfinite([record["a"], record["b"]]).all(),
            "invalid affine calibrator")
    values = float(record["a"]) + float(record["b"]) * np.asarray(raw, dtype=np.float64)
    require(np.isfinite(values).all(), "non-finite calibrated scores")
    return values


def _rank(frame: pd.DataFrame, score_column: str) -> pd.DataFrame:
    return frame.sort_values([score_column, "movie_id"], ascending=[False, True], kind="mergesort")


def ndcg_at(rating: np.ndarray, scores: np.ndarray, movie_ids: np.ndarray, k: int) -> float | None:
    rating = np.asarray(rating, np.float64)
    scores = np.asarray(scores, np.float64)
    movie_ids = np.asarray(movie_ids, np.int64)
    require(len(rating) == len(scores) == len(movie_ids) and len(np.unique(movie_ids)) == len(movie_ids),
            "NDCG axes")
    if len(rating) < k:
        return None
    gains = np.exp2(rating - .5) - 1.0
    discounts = 1.0 / np.log2(np.arange(k, dtype=np.float64) + 2.0)
    order = np.lexsort((movie_ids, -scores))[:k]
    ideal_order = np.lexsort((movie_ids, -rating))[:k]
    ideal = float(gains[ideal_order] @ discounts)
    if ideal <= 0:
        return None
    return float(gains[order] @ discounts / ideal)


def per_user_metrics(frame: pd.DataFrame, score_column: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for uid, group in frame.groupby("uid", sort=True):
        y = group.rating.to_numpy(np.float64)
        scores = group[score_column].to_numpy(np.float64)
        ids = group.movie_id.to_numpy(np.int64)
        clipped = np.clip(scores, .5, 5.0)
        record: dict[str, Any] = {
            "uid": int(uid), "rows": len(group),
            "mse": float(np.square(clipped - y).mean()),
            "mae": float(np.abs(clipped - y).mean()),
        }
        ranked = _rank(group, score_column)
        ranked_y = ranked.rating.to_numpy(np.float64)
        for k in TOP_K:
            record[f"ndcg{k}"] = ndcg_at(y, scores, ids, k)
            record[f"actual{k}"] = float(ranked_y[:k].mean()) if len(ranked_y) >= k else None
            record[f"low{k}"] = float((ranked_y[:k] <= 2.0).mean()) if len(ranked_y) >= k else None
        for start in range(0, 10, 2):
            end = start + 2
            record[f"round{start + 1}_{end}_actual"] = (
                float(ranked_y[start:end].mean()) if len(ranked_y) >= end else None)
            record[f"round{start + 1}_{end}_low"] = (
                float((ranked_y[start:end] <= 2.0).mean()) if len(ranked_y) >= end else None)
        rows.append(record)
    columns = ["uid", "rows", "mse", "mae"]
    columns += [f"{metric}{k}" for k in TOP_K for metric in ("ndcg", "actual", "low")]
    columns += [f"round{start + 1}_{start + 2}_{metric}"
                for start in range(0, 10, 2) for metric in ("actual", "low")]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns).sort_values("uid").reset_index(drop=True)


def summarize_model(frame: pd.DataFrame, score_column: str) -> tuple[dict[str, Any], pd.DataFrame]:
    users = per_user_metrics(frame, score_column)
    clipped = np.clip(frame[score_column].to_numpy(np.float64), .5, 5.0)
    squared = np.square(clipped - frame.rating.to_numpy(np.float64))
    movie_macro = (pd.DataFrame({"movie_id": frame.movie_id.to_numpy(np.int64), "se": squared})
                   .groupby("movie_id", sort=True).se.mean().mean()) if len(frame) else None
    result: dict[str, Any] = {
        "support": {"users": int(frame.uid.nunique()), "rows": len(frame),
                    "movies": int(frame.movie_id.nunique())},
        "userMacroMSE": float(users.mse.mean()) if len(users) else None,
        "userMacroMAE": float(users.mae.mean()) if len(users) else None,
        "microMSE": float(squared.mean()) if len(squared) else None,
        "movieMacroMSE": float(movie_macro) if movie_macro is not None else None,
        "ranking": {},
        "rounds": {},
    }
    for k in TOP_K:
        valid = users[f"ndcg{k}"].dropna()
        actual = users[f"actual{k}"].dropna()
        low = users[f"low{k}"].dropna()
        result["ranking"][str(k)] = {
            "ndcg": float(valid.mean()) if len(valid) else None,
            "ndcgValidUsers": len(valid),
            "ndcgExcludedUsers": len(users) - len(valid),
            "actualMean": float(actual.mean()) if len(actual) else None,
            "actualValidUsers": len(actual),
            "lowFraction": float(low.mean()) if len(low) else None,
            "lowValidUsers": len(low),
        }
    for start in range(0, 10, 2):
        end = start + 2
        actual = users[f"round{start + 1}_{end}_actual"].dropna()
        low = users[f"round{start + 1}_{end}_low"].dropna()
        result["rounds"][f"{start + 1}-{end}"] = {
            "actualMean": float(actual.mean()) if len(actual) else None,
            "actualValidUsers": len(actual),
            "lowFraction": float(low.mean()) if len(low) else None,
            "lowValidUsers": len(low),
        }
    return result, users


def paired_bootstrap(before: np.ndarray, after: np.ndarray, *, samples: int,
                     seed: int = USER_BOOTSTRAP_SEED, minimum: int = MIN_USERS) -> dict[str, Any]:
    before = np.asarray(before, dtype=np.float64)
    after = np.asarray(after, dtype=np.float64)
    require(before.ndim == after.ndim == 1 and before.shape == after.shape,
            "paired bootstrap axes")
    require(np.isfinite(before).all() and np.isfinite(after).all(), "paired bootstrap finite values")
    result: dict[str, Any] = {
        "users": len(before), "before": float(before.mean()) if len(before) else None,
        "after": float(after.mean()) if len(after) else None,
        "delta": float((after - before).mean()) if len(before) else None,
        "ciLow": None, "ciHigh": None, "samples": samples, "seed": seed,
        "confidence": 0.95, "status": "INSUFFICIENT",
    }
    if len(before) < minimum:
        return result
    rng = np.random.Generator(np.random.PCG64(seed))
    delta = after - before
    draws = np.empty(samples, dtype=np.float64)
    for start in range(0, samples, 256):
        stop = min(start + 256, samples)
        indices = rng.integers(0, len(delta), size=(stop - start, len(delta)))
        draws[start:stop] = delta[indices].mean(axis=1)
    result["ciLow"], result["ciHigh"] = map(float, np.quantile(draws, [.025, .975], method="linear"))
    result["status"] = "COMPLETE"
    return result


def relative_mse_bootstrap(before: np.ndarray, after: np.ndarray, *, samples: int,
                           seed: int = USER_BOOTSTRAP_SEED,
                           minimum: int = MIN_USERS) -> dict[str, Any]:
    before = np.asarray(before, dtype=np.float64)
    after = np.asarray(after, dtype=np.float64)
    require(before.ndim == after.ndim == 1 and before.shape == after.shape,
            "relative bootstrap axes")
    require(np.isfinite(before).all() and np.isfinite(after).all(), "relative bootstrap finite values")

    def relative(base: np.ndarray | float, candidate: np.ndarray | float) -> np.ndarray | float:
        base_array, candidate_array = np.asarray(base), np.asarray(candidate)
        result = np.empty(np.broadcast_shapes(base_array.shape, candidate_array.shape), dtype=np.float64)
        b = np.broadcast_to(base_array, result.shape)
        c = np.broadcast_to(candidate_array, result.shape)
        positive = b != 0
        result[positive] = (c[positive] - b[positive]) / b[positive]
        result[~positive] = np.where(c[~positive] == 0, 0.0, np.inf)
        return float(result) if result.ndim == 0 else result

    observed = relative(float(before.mean()), float(after.mean())) if len(before) else None
    result: dict[str, Any] = {
        "users": len(before), "before": float(before.mean()) if len(before) else None,
        "after": float(after.mean()) if len(after) else None, "relativeDelta": observed,
        "ciLow": None, "ciHigh": None, "samples": samples, "seed": seed,
        "confidence": 0.95, "status": "INSUFFICIENT",
    }
    if len(before) < minimum:
        return result
    rng = np.random.Generator(np.random.PCG64(seed))
    draws = np.empty(samples, dtype=np.float64)
    for start in range(0, samples, 256):
        stop = min(start + 256, samples)
        indices = rng.integers(0, len(before), size=(stop - start, len(before)))
        b = before[indices].mean(axis=1)
        a = after[indices].mean(axis=1)
        draws[start:stop] = relative(b, a)
    if not np.isfinite(draws).all():
        result["status"] = "NONFINITE_DRAW"
        result["ciHigh"] = math.inf if np.isposinf(draws).any() else None
        return result
    result["ciLow"], result["ciHigh"] = map(float, np.quantile(draws, [.025, .975], method="linear"))
    result["status"] = "COMPLETE"
    return result


def movie_block_bootstrap(frame: pd.DataFrame, *, samples: int,
                          seed: int = MOVIE_BOOTSTRAP_SEED,
                          minimum: int = MIN_USERS) -> dict[str, Any]:
    """Movie-cluster sensitivity for user-macro clipped MSE."""
    require(not frame.duplicated(["uid", "movie_id"]).any(), "movie bootstrap rows")
    user_ids = np.sort(frame.uid.unique().astype(np.int64))
    movie_ids = np.sort(frame.movie_id.unique().astype(np.int64))
    result: dict[str, Any] = {
        "users": len(user_ids), "movies": len(movie_ids), "rows": len(frame),
        "samples": samples, "seed": seed, "delta": None, "ciLow": None,
        "ciHigh": None, "relativeDelta": None, "relativeCiLow": None,
        "relativeCiHigh": None, "emptyDraws": 0, "status": "INSUFFICIENT",
    }
    if len(user_ids) < minimum:
        return result
    user_index = np.searchsorted(user_ids, frame.uid.to_numpy(np.int64))
    movie_index = np.searchsorted(movie_ids, frame.movie_id.to_numpy(np.int64))
    y = frame.rating.to_numpy(np.float64)
    se0 = np.square(np.clip(frame.b0_cal.to_numpy(np.float64), .5, 5) - y)
    se1 = np.square(np.clip(frame.b1_cal.to_numpy(np.float64), .5, 5) - y)
    point0 = pd.Series(se0).groupby(frame.uid.to_numpy()).mean().mean()
    point1 = pd.Series(se1).groupby(frame.uid.to_numpy()).mean().mean()
    result["delta"] = float(point1 - point0)
    result["relativeDelta"] = (float((point1 - point0) / point0) if point0 != 0
                               else (0.0 if point1 == 0 else math.inf))
    rng = np.random.Generator(np.random.PCG64(seed))
    absolute = np.empty(samples, np.float64)
    relative = np.empty(samples, np.float64)
    empty = 0
    for draw in range(samples):
        selected = rng.integers(0, len(movie_ids), size=len(movie_ids))
        multiplicity = np.bincount(selected, minlength=len(movie_ids)).astype(np.float64)
        weights = multiplicity[movie_index]
        denominator = np.bincount(user_index, weights=weights, minlength=len(user_ids))
        valid = denominator > 0
        if not valid.any():
            empty += 1
            absolute[draw] = np.nan
            relative[draw] = np.nan
            continue
        mean0 = np.bincount(user_index, weights=weights * se0, minlength=len(user_ids))[valid] / denominator[valid]
        mean1 = np.bincount(user_index, weights=weights * se1, minlength=len(user_ids))[valid] / denominator[valid]
        b0, b1 = float(mean0.mean()), float(mean1.mean())
        absolute[draw] = b1 - b0
        relative[draw] = (b1 - b0) / b0 if b0 != 0 else (0.0 if b1 == 0 else math.inf)
    result["emptyDraws"] = empty
    if empty or not np.isfinite(absolute).all() or not np.isfinite(relative).all():
        result["status"] = "NONFINITE_OR_EMPTY_DRAW"
        return result
    result["ciLow"], result["ciHigh"] = map(float, np.quantile(absolute, [.025, .975], method="linear"))
    result["relativeCiLow"], result["relativeCiHigh"] = map(
        float, np.quantile(relative, [.025, .975], method="linear"))
    result["status"] = "COMPLETE"
    return result


def sign_flip_holm(before: np.ndarray, after: np.ndarray, *, samples: int,
                   seed: int = SIGN_FLIP_SEED) -> dict[str, Any]:
    before, after = np.asarray(before, np.float64), np.asarray(after, np.float64)
    require(before.ndim == after.ndim == 1 and before.shape == after.shape,
            "sign-flip axes")
    require(np.isfinite(before).all() and np.isfinite(after).all(), "sign-flip finite values")
    delta = after - before
    observed = float(delta.mean()) if len(delta) else None
    raw_p = 1.0
    if len(delta) >= MIN_USERS:
        rng = np.random.Generator(np.random.PCG64(seed))
        count = 0
        for start in range(0, samples, 256):
            stop = min(start + 256, samples)
            signs = rng.integers(0, 2, size=(stop - start, len(delta)), dtype=np.int8) * 2 - 1
            permuted = (signs * delta).mean(axis=1)
            count += int((permuted <= observed).sum())
        raw_p = (1 + count) / (samples + 1)
    names = ["B1_MINUS_B0", "B2_MINUS_B0", "B3_MINUS_B0"]
    raw = {names[0]: float(raw_p), names[1]: 1.0, names[2]: 1.0}
    ordered = sorted(names, key=lambda name: (raw[name], name))
    adjusted: dict[str, float] = {}
    running = 0.0
    rejected: dict[str, bool] = {name: False for name in names}
    still_rejecting = True
    for index, name in enumerate(ordered):
        multiplier = len(names) - index
        running = max(running, min(1.0, multiplier * raw[name]))
        adjusted[name] = running
        threshold = .05 / multiplier
        rejected[name] = bool(still_rejecting and raw[name] <= threshold)
        if raw[name] > threshold:
            still_rejecting = False
    return {
        "family": names, "alternative": "LOWER_USER_MACRO_MSE", "samples": samples,
        "seed": seed, "users": len(delta), "observedB1MinusB0": observed,
        "rawP": raw, "holmAdjustedP": adjusted, "holmRejectAtAlpha05": rejected,
        "missingB2B3PSetToOne": True,
    }


def stratum_frames(frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {
        "ALL": frame,
        "ALS_TARGET_SUPPORTED": frame[frame.als_supported].copy(),
        "ALS_TARGET_UNSUPPORTED": frame[~frame.als_supported].copy(),
    }


def _common_metric_arrays(left: pd.DataFrame, right: pd.DataFrame, column: str) -> tuple[np.ndarray, np.ndarray]:
    require(np.array_equal(left.uid.to_numpy(), right.uid.to_numpy()), f"paired {column} user axis")
    l = left[column].to_numpy(np.float64)
    r = right[column].to_numpy(np.float64)
    require(np.array_equal(np.isfinite(l), np.isfinite(r)), f"paired {column} eligibility")
    good = np.isfinite(l) & np.isfinite(r)
    return l[good], r[good]


def evaluate_phase(frame: pd.DataFrame, affine: Mapping[str, Mapping[str, Any]],
                   spec: EvaluationSpec) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    require(not frame.empty and not frame.duplicated(["uid", "movie_id"]).any(), "phase truth rows")
    working = frame.copy()
    working["b0_cal"] = apply_affine(working.b0_raw.to_numpy(), affine["B0"])
    working["b1_cal"] = apply_affine(working.b1_raw.to_numpy(), affine["B1"])
    metrics: dict[str, Any] = {"models": {}, "pointDeltas": {}}
    user_tables: dict[str, dict[str, pd.DataFrame]] = {model: {} for model in MODELS}
    census: dict[str, Any] = {"phaseUsers": int(working.uid.nunique()), "phaseRows": len(working), "strata": {}}
    boot_user: dict[str, Any] = {}
    boot_movie: dict[str, Any] = {}
    for stratum, subset in stratum_frames(working).items():
        support = {"users": int(subset.uid.nunique()), "movies": int(subset.movie_id.nunique()),
                   "rows": len(subset)}
        support["minimumRequired"] = {"users": spec.minimum_users, "movies": spec.minimum_movies,
                                      "rows": spec.minimum_rows}
        support["status"] = ("COMPLETE" if support["users"] >= spec.minimum_users
                             and support["movies"] >= spec.minimum_movies
                             and support["rows"] >= spec.minimum_rows else "INSUFFICIENT")
        census["strata"][stratum] = support
        for model, column in (("B0", "b0_cal"), ("B1", "b1_cal")):
            summary, users = summarize_model(subset, column)
            metrics["models"].setdefault(model, {})[stratum] = summary
            user_tables[model][stratum] = users
        left, right = user_tables["B0"][stratum], user_tables["B1"][stratum]
        b0_mse, b1_mse = _common_metric_arrays(left, right, "mse")
        b0_mae, b1_mae = _common_metric_arrays(left, right, "mae")
        boot_user[stratum] = {
            "mseDelta": paired_bootstrap(b0_mse, b1_mse, samples=spec.bootstrap_samples,
                                         minimum=spec.minimum_users),
            "relativeMse": relative_mse_bootstrap(b0_mse, b1_mse, samples=spec.bootstrap_samples,
                                                  minimum=spec.minimum_users),
            "maeDelta": paired_bootstrap(b0_mae, b1_mae, samples=spec.bootstrap_samples,
                                         minimum=spec.minimum_users),
        }
        if stratum == "ALL":
            for k in TOP_K:
                for metric_name in ("ndcg", "actual", "low"):
                    column = f"{metric_name}{k}"
                    b0_values, b1_values = _common_metric_arrays(left, right, column)
                    boot_user[stratum][f"{metric_name}{k}Delta"] = paired_bootstrap(
                        b0_values, b1_values, samples=spec.bootstrap_samples,
                        minimum=spec.minimum_users)
            for start in range(0, 10, 2):
                end = start + 2
                for metric_name in ("actual", "low"):
                    column = f"round{start + 1}_{end}_{metric_name}"
                    b0_values, b1_values = _common_metric_arrays(left, right, column)
                    boot_user[stratum][f"round{start + 1}_{end}_{metric_name}Delta"] = paired_bootstrap(
                        b0_values, b1_values, samples=spec.bootstrap_samples,
                        minimum=spec.minimum_users)
        boot_movie[stratum] = movie_block_bootstrap(subset, samples=spec.bootstrap_samples,
                                                    minimum=spec.minimum_users)
        def delta(metric: str) -> float | None:
            left_value = metrics["models"]["B0"][stratum][metric]
            right_value = metrics["models"]["B1"][stratum][metric]
            return (float(right_value - left_value)
                    if left_value is not None and right_value is not None else None)

        metrics["pointDeltas"][stratum] = {
            "userMacroMSE": delta("userMacroMSE"),
            "userMacroMAE": delta("userMacroMAE"),
            "microMSE": delta("microMSE"),
            "movieMacroMSE": delta("movieMacroMSE"),
        }
    sign_flip = sign_flip_holm(user_tables["B0"]["ALL"].mse.to_numpy(),
                               user_tables["B1"]["ALL"].mse.to_numpy(),
                               samples=spec.bootstrap_samples)
    bootstrap = {
        "userBlock": boot_user, "movieBlockSensitivity": boot_movie,
        "signFlipHolm": sign_flip,
        "uncertaintyScope": "CONDITIONAL_ON_FIXED_B0_B1_FITS_CALIBRATORS_AND_REUSED_DEVELOPMENT_USERS",
    }
    gates = decide_gates(census, bootstrap, affine)
    return metrics, bootstrap, census, gates


def decide_gates(census: Mapping[str, Any], bootstrap: Mapping[str, Any],
                 affine: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    items: dict[str, Any] = {}
    unavailable = any(affine[model].get("state") != "AFFINE" for model in MODELS)
    insufficient = unavailable
    failed = False
    for stratum in STRATA:
        support = census["strata"][stratum]
        complete = support["status"] == "COMPLETE"
        items[f"support:{stratum}"] = {**support,
                                      "status": "PASS" if complete else "INSUFFICIENT"}
        insufficient |= not complete
        relative = bootstrap["userBlock"][stratum]["relativeMse"]
        if relative.get("status") != "COMPLETE" or relative.get("ciHigh") is None:
            state = "INSUFFICIENT"
            insufficient = True
        else:
            state = "PASS" if float(relative["ciHigh"]) <= .05 else "FAIL"
            failed |= state == "FAIL"
        items[f"relativeMse:{stratum}"] = {
            **relative, "status": state, "limit": .05, "comparison": "CI_HIGH_LE_LIMIT"}

    ndcg = bootstrap["userBlock"].get("ALL", {}).get("ndcg2Delta", {})
    if ndcg.get("status") != "COMPLETE" or ndcg.get("ciLow") is None:
        state = "INSUFFICIENT"
        insufficient = True
    else:
        state = "PASS" if float(ndcg["ciLow"]) >= -.01 else "FAIL"
        failed |= state == "FAIL"
    items["ndcg2"] = {**ndcg, "status": state, "limit": -.01,
                      "comparison": "CI_LOW_GE_LIMIT"}

    low = bootstrap["userBlock"].get("ALL", {}).get("low2Delta", {})
    if low.get("status") != "COMPLETE" or low.get("ciHigh") is None:
        state = "INSUFFICIENT"
        insufficient = True
    else:
        state = "PASS" if float(low["ciHigh"]) <= .02 else "FAIL"
        failed |= state == "FAIL"
    items["top2Low"] = {**low, "status": state, "limit": .02,
                        "comparison": "CI_HIGH_LE_LIMIT"}

    required = "FAIL" if failed else ("INSUFFICIENT" if insufficient else "PASS")
    return {
        "requiredGate": required, "items": items,
        "holmSuperiorityRequiredForSafety": False,
        "serviceActivationAuthorized": False,
    }


def _add_source_file(inventory: dict[str, dict[str, Any]], path: Path, root: Path) -> None:
    record = path_pin(path, root)
    key = str(record["path"])
    previous = inventory.get(key)
    require(previous is None or previous == record, f"conflicting source pin: {key}")
    inventory[key] = record


def reverify_source_inventory(files: Mapping[str, Mapping[str, Any]], root: Path) -> None:
    masked_roots: set[Path] = set()
    for relative, expected in files.items():
        path = (root / relative).resolve()
        verify_pin(path, expected, f"source recheck {relative}")
        if path.name == "tmdb-masked-rh230.parquet":
            masked_roots.add(path.parent)
    for masked in masked_roots:
        children = list(masked.iterdir())
        require(masked.is_dir() and not masked.is_symlink()
                and not (hasattr(masked, "is_junction") and masked.is_junction())
                and {child.name for child in children}
                == {"manifest.json", "tmdb-masked-rh230.parquet", "views-manifest.json"}
                and all(child.is_file() and not child.is_symlink() for child in children),
                "source recheck masked bundle file set drift")


def reverify_ancestor_bundle_inventories(chain: Mapping[str, Any]) -> None:
    """Recheck exact physical trees, including files created after the initial gate."""
    fit = chain.get("fit")
    require(isinstance(fit, Mapping), "verified fit chain state missing")
    preflight = fit.get("preflight")
    require(isinstance(preflight, Mapping), "verified preflight chain state missing")

    preflight_bundle = preflight.get("bundlePath")
    fit_bundle = fit.get("bundlePath")
    score_bundle = chain.get("bundlePath")
    require(all(isinstance(path, Path)
                for path in (preflight_bundle, fit_bundle, score_bundle)),
            "verified ancestor bundle paths missing")

    preflight_required = {
        "input-lock.json", "recovery-reference.json", "partition-identity.json",
        "command.json", "resource.json", "run.log",
    }
    inventory = fit.get("inventory")
    require(isinstance(inventory, Mapping) and isinstance(inventory.get("files"), list),
            "verified fit model inventory missing")
    model_names = []
    for record in inventory["files"]:
        require(isinstance(record, Mapping) and isinstance(record.get("path"), str),
                "verified native model record missing")
        model_names.append(str(record["path"]))
    require(len(model_names) == len(set(model_names)), "verified native model paths duplicated")
    fit_required = {
        "input-lock.json", "preflight-reference.json", "command.json",
        "partition-identity.json", "resolved-estimator.json",
        "model-file-inventory.json", "threshold-fixtures.npz", "fit-metrics.json",
        "resource.json", "run.log",
    } | {"model/native/" + name for name in model_names}
    score_required = {
        "fit-reference.json", "score-input-lock.json", "command.json",
        "score/analyzed-plan.txt", "score/predictions.parquet", "resource.json", "run.log",
    }

    for phase, bundle, manifest, required in (
        ("preflight", preflight_bundle, preflight.get("manifest"), preflight_required),
        ("fit", fit_bundle, fit.get("manifest"), fit_required),
        ("score", score_bundle, chain.get("manifest"), score_required),
    ):
        require(isinstance(manifest, Mapping), f"verified {phase} manifest missing")
        verify_manifest_inventory(bundle, manifest, required)


def score_reference_payload(paths: EvaluationPaths, chain: Mapping[str, Any],
                            source_files: Mapping[str, Mapping[str, Any]],
                            predictions: Path) -> dict[str, Any]:
    fit_reference_path = paths.score_manifest.parent / "fit-reference.json"
    return {
        "schemaVersion": "feelm-service-v1-b1-score-reference/1",
        "scoreManifest": path_pin(paths.score_manifest, paths.root),
        "scoreReview": path_pin(paths.score_review, paths.root),
        "predictions": path_pin(predictions, paths.root),
        "fitReference": path_pin(fit_reference_path, paths.root),
        "scoreStatus": chain["manifest"].get("status"),
        "scoreReviewStatus": chain["review"].get("status"),
        "verifiedChainFiles": {name: record for name, record in sorted(source_files.items())},
    }


def build_source_state(paths: EvaluationPaths, expected_plan_sha256: str,
                       expected_score_manifest_sha256: str,
                       expected_score_review_sha256: str,
                       spec: EvaluationSpec,
                       fixed_pins: Mapping[str, tuple[int, str]] = SOURCE_PINS
                       ) -> tuple[dict[str, dict[str, Any]], dict[str, Any],
                                  dict[str, dict[str, Any]], Path, set[int], Mapping[int, str], str,
                                  dict[str, Any]]:
    """Build the common source set; the score gate is deliberately first."""
    chain, score_files, predictions = verify_score_chain(
        paths, expected_score_manifest_sha256, expected_score_review_sha256, spec)
    source_files = verify_fixed_nonlabel_inputs(paths, expected_plan_sha256, fixed_pins)
    for key, record in score_files.items():
        prior = source_files.get(key)
        require(prior is None or prior == record, f"conflicting score source pin: {key}")
        source_files[key] = record
    als_ids, als_files = verify_als_axis(paths, spec)
    for key, record in als_files.items():
        prior = source_files.get(key)
        require(prior is None or prior == record, f"conflicting ALS source pin: {key}")
        source_files[key] = record
    _add_source_file(source_files, SCRIPT, paths.root)
    roles_frame = pd.read_csv(paths.roles)
    roles, membership_digest = split_roles(roles_frame, spec)
    training_user_audit = validate_training_user_disjointness(paths.ratings, roles, spec)
    return (source_files, chain, score_files, predictions, als_ids, roles, membership_digest,
            training_user_audit)


def evaluation_lock(phase: str, source_files: Mapping[str, Mapping[str, Any]],
                    phase_files: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
    sources = {name: dict(record) for name, record in sorted(source_files.items())}
    phase_inputs = {name: dict(record) for name, record in sorted((phase_files or {}).items())}
    combined = {**sources, **phase_inputs}
    require(len(combined) == len(sources) + len(phase_inputs), "phase/source inventory collision")
    return {
        "schemaVersion": "feelm-service-v1-b1-evaluation-input-lock/1",
        "phase": phase,
        "files": sources,
        "phaseInputs": phase_inputs,
        "evaluationSourceSetSha256": canonical_inventory_digest(sources),
        "inputSetSha256": canonical_inventory_digest(combined),
        "labelsPreviouslyOpened": True,
        "freshBlindHoldout": False,
        "fitOrScoreMutationAuthorized": False,
    }


def _manifest_payload(staging: Path, *, phase: str, status: str, gate_key: str,
                      gate_value: str, evaluator: Mapping[str, Any], started_at: str,
                      seconds: float, source_digest: str, truth: Mapping[str, Any]) -> dict[str, Any]:
    files = {p.relative_to(staging).as_posix(): path_pin(p, staging)
             for p in sorted(staging.rglob("*")) if p.is_file()}
    return {
        "schemaVersion": "feelm-service-v1-b1-evaluation-result/1",
        "phase": phase,
        "status": status,
        gate_key: gate_value,
        "confirmationAuthorized": False,
        "readyForService": False,
        "serviceActivationAuthorized": False,
        "freshBlindHoldout": False,
        "evaluator": dict(evaluator),
        "evaluationSourceSetSha256": source_digest,
        "truthCensus": dict(truth),
        "startedAt": started_at,
        "completedAt": utc_now(),
        "seconds": seconds,
        "runtime": {"python": platform.python_version(), "numpy": np.__version__,
                    "pandas": pd.__version__, "pyarrow": pa.__version__},
        "files": files,
    }


def _failure_path(output_dir: Path) -> Path:
    return output_dir.with_name(output_dir.name + "-failure.json")


def _atomic_failure(output_dir: Path, phase: str, error: BaseException,
                    cleanup_complete: bool) -> None:
    failure = _failure_path(output_dir)
    if failure.exists():
        return
    failure.parent.mkdir(parents=True, exist_ok=True)
    temporary = failure.with_name("." + failure.name + ".tmp-" + uuid.uuid4().hex)
    payload = {
        "schemaVersion": "feelm-service-v1-b1-evaluation-failure/1",
        "phase": phase,
        "status": "FAILED",
        "errorType": type(error).__name__,
        "error": str(error),
        "cleanupComplete": cleanup_complete,
        "createdAt": utc_now(),
    }
    write_json(temporary, payload)
    os.replace(temporary, failure)


def _new_staging(output_dir: Path) -> Path:
    output_dir = output_dir.resolve()
    require(not output_dir.exists(), f"preserve existing output: {output_dir}")
    require(not _failure_path(output_dir).exists(), f"preserve existing failure: {_failure_path(output_dir)}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = output_dir.parent / ("." + output_dir.name + ".tmp-" + uuid.uuid4().hex)
    staging.mkdir()
    return staging


def _publish(staging: Path, output_dir: Path) -> None:
    require(not output_dir.exists(), "output appeared before publish")
    os.replace(staging, output_dir)


def _selection_files() -> set[str]:
    return {
        "evaluation-input-lock.json", "truth-integrity.json", "score-reference.json",
        "command.json", "role-membership.csv", "affine.json", "metrics.json",
        "bootstrap-summary.json", "strata-census.json", "gates.json", "run.log",
    }


def _confirmation_files() -> set[str]:
    return {
        "selection-reference.json", "score-reference.json", "evaluation-input-lock.json",
        "truth-integrity.json", "command.json", "metrics.json", "bootstrap-summary.json",
        "strata-census.json", "gates.json", "run.log",
    }


def run_selection(paths: EvaluationPaths, output_dir: Path, *, expected_plan_sha256: str,
                  expected_score_manifest_sha256: str,
                  expected_score_review_sha256: str,
                  spec: EvaluationSpec = EvaluationSpec(),
                  fixed_pins: Mapping[str, tuple[int, str]] = SOURCE_PINS,
                  publish_failure: bool = True) -> dict[str, Any]:
    phase = "calibrate-select"
    output_dir = Path(output_dir).resolve()
    staging: Path | None = None
    started_at, started = utc_now(), time.monotonic()
    try:
        staging = _new_staging(output_dir)
        (source_files, chain, score_files, predictions_path, als_ids, roles, role_digest,
         training_user_audit) = build_source_state(
            paths, expected_plan_sha256, expected_score_manifest_sha256,
            expected_score_review_sha256, spec, fixed_pins)
        label_size, label_sha256 = fixed_pins["labels"]
        verify_pin(paths.labels, {"bytes": label_size, "sha256": label_sha256},
                   "fixed evaluation labels")
        b1_scores = load_predictions(predictions_path, paths.score_axis, spec)
        truth, integrity = prepare_truth(paths, spec, roles, als_ids, b1_scores)
        integrity["roleMembershipSha256"] = role_digest
        integrity["trainingUserIsolation"] = training_user_audit
        _add_source_file(source_files, paths.labels, paths.root)
        lock = evaluation_lock("SELECTION", source_files)

        calibration = truth[truth.role.eq("CALIBRATION")]
        selection = truth[truth.role.eq("SELECTION")]
        require(calibration.uid.nunique() == spec.calibration_users, "calibration user census")
        require(selection.uid.nunique() == spec.selection_users, "selection user census")
        affine = {
            "schemaVersion": "feelm-service-v1-b1-affine/1",
            "fitRole": "CALIBRATION", "userEqualWeight": True,
            "comparisonLabelsUsedForFitting": False,
            "models": {
                "B0": fit_affine_user_equal(calibration, "b0_raw"),
                "B1": fit_affine_user_equal(calibration, "b1_raw"),
            },
        }
        if all(affine["models"][model]["state"] == "AFFINE" for model in MODELS):
            metrics, bootstrap, census, gates = evaluate_phase(selection, affine["models"], spec)
        else:
            census = {"phaseUsers": int(selection.uid.nunique()), "phaseRows": len(selection),
                      "strata": {name: {"users": int(part.uid.nunique()),
                                        "movies": int(part.movie_id.nunique()), "rows": len(part),
                                        "status": "INSUFFICIENT"}
                                  for name, part in stratum_frames(selection).items()}}
            metrics = {"models": {}, "pointDeltas": {}, "state": "CALIBRATION_UNAVAILABLE"}
            bootstrap = {"state": "CALIBRATION_UNAVAILABLE", "userBlock": {},
                         "movieBlockSensitivity": {}, "signFlipHolm": None}
            gates = {"requiredGate": "INSUFFICIENT", "items": {"affine": {
                "status": "INSUFFICIENT", "models": affine["models"]}},
                "serviceActivationAuthorized": False}

        write_json(staging / "evaluation-input-lock.json", lock)
        write_json(staging / "truth-integrity.json", integrity)
        write_json(staging / "score-reference.json",
                   score_reference_payload(paths, chain, score_files, predictions_path))
        write_json(staging / "command.json", {
            "phase": phase, "scoreManifest": path_pin(paths.score_manifest, paths.root),
            "scoreReview": path_pin(paths.score_review, paths.root),
            "expectedPlanSha256": expected_plan_sha256,
            "expectedScoreManifestSha256": expected_score_manifest_sha256,
            "expectedScoreReviewSha256": expected_score_review_sha256,
            "bootstrapSamples": spec.bootstrap_samples,
        })
        write_role_membership(staging / "role-membership.csv", roles)
        write_json(staging / "affine.json", affine)
        write_json(staging / "metrics.json", {"phase": "SELECTION", **metrics})
        write_json(staging / "bootstrap-summary.json", {"phase": "SELECTION", **bootstrap})
        write_json(staging / "strata-census.json", {"phase": "SELECTION", **census})
        write_json(staging / "gates.json", {"phase": "SELECTION", **gates})
        write_text(staging / "run.log", (
            f"{started_at} phase=calibrate-select score_gate=PASS truth_rows={len(truth)} "
            f"selection_gate={gates['requiredGate']}\n"))
        require(_bundle_inventory(staging) == _selection_files(), "selection staging inventory")

        reverify_source_inventory(source_files, paths.root)
        reverify_ancestor_bundle_inventories(chain)
        require(lock["evaluationSourceSetSha256"] == canonical_inventory_digest(source_files),
                "selection source set drift")
        manifest = _manifest_payload(
            staging, phase="SELECTION", status="B1_SELECTION_COMPLETE_AUDIT_PENDING",
            gate_key="requiredSelectionGate", gate_value=str(gates["requiredGate"]),
            evaluator=path_pin(SCRIPT, paths.root), started_at=started_at,
            seconds=time.monotonic() - started,
            source_digest=lock["evaluationSourceSetSha256"], truth=integrity)
        write_json(staging / "manifest.json", manifest)
        require(_bundle_inventory(staging) == _selection_files() | {"manifest.json"},
                "selection final inventory")
        reverify_source_inventory(source_files, paths.root)
        reverify_ancestor_bundle_inventories(chain)
        _publish(staging, output_dir)
        staging = None
        return manifest
    except BaseException as error:
        cleanup = True
        if staging is not None and staging.exists():
            try:
                shutil.rmtree(staging)
            except BaseException:
                cleanup = False
        if publish_failure:
            _atomic_failure(output_dir, phase, error, cleanup)
        raise


def _resolve_review_target(review_path: Path, record: Mapping[str, Any], root: Path,
                           team_root: Path, actual: Path, label: str) -> None:
    require(isinstance(record, Mapping) and isinstance(record.get("path"), str), f"missing {label} pin")
    resolved = resolve_record_path(record, root=root, team_root=team_root, owner=review_path)
    require(resolved == actual.resolve(), f"{label} path mismatch")
    verify_pin(actual, record, label)


def verify_selection_gate(selection_manifest_path: Path, selection_review_path: Path,
                          expected_manifest_sha256: str, expected_review_sha256: str,
                          root: Path, team_root: Path
                          ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any],
                                     dict[str, dict[str, Any]]]:
    """Validate selection and its review without opening evaluation source data."""
    verify_sha(selection_manifest_path, expected_manifest_sha256, "selection manifest")
    verify_sha(selection_review_path, expected_review_sha256, "selection review")
    require(selection_manifest_path.name == "manifest.json", "selection manifest basename")
    bundle = selection_manifest_path.parent
    require(selection_review_path.resolve()
            == bundle.with_name(bundle.name + "-result-review.json").resolve(),
            "selection review is not canonical sibling")
    manifest = read_json(selection_manifest_path)
    review = read_json(selection_review_path)
    require(manifest.get("status") == "B1_SELECTION_COMPLETE_AUDIT_PENDING", "selection manifest status")
    require(manifest.get("requiredSelectionGate") == "PASS", "selection required gate must PASS")
    require(manifest.get("confirmationAuthorized") is False and manifest.get("readyForService") is False,
            "selection manifest premature authorization")
    require(review.get("status") == "PASS", "selection independent review must PASS")
    require(review.get("phase") == "selection", "selection independent review phase drift")
    verify_manifest_inventory(bundle, manifest, _selection_files())
    target = review.get("target")
    require(isinstance(target, Mapping), "selection review target missing")
    required_targets = {
        "manifest": selection_manifest_path,
        "evaluation_input_lock": bundle / "evaluation-input-lock.json",
        "truth_integrity": bundle / "truth-integrity.json",
        "score_reference": bundle / "score-reference.json",
        "command": bundle / "command.json",
        "role_membership": bundle / "role-membership.csv",
        "affine": bundle / "affine.json",
        "metrics": bundle / "metrics.json",
        "bootstrap_summary": bundle / "bootstrap-summary.json",
        "strata_census": bundle / "strata-census.json",
        "gates": bundle / "gates.json",
        "run_log": bundle / "run.log",
        "evaluator": SCRIPT,
    }
    require(set(target) == set(required_targets), "selection review target set drift")
    verified: dict[str, dict[str, Any]] = {}
    for name, actual in required_targets.items():
        _resolve_review_target(selection_review_path, target.get(name, {}), root, team_root, actual,
                               f"selection review target.{name}")
        _remember_file(verified, actual, root)
    _remember_file(verified, selection_review_path, root)
    gates = read_json(bundle / "gates.json")
    require(gates.get("requiredGate") == "PASS", "selection gates file must PASS")
    selection_lock = read_json(bundle / "evaluation-input-lock.json")
    selection_truth = read_json(bundle / "truth-integrity.json")
    require(selection_lock.get("schemaVersion") == "feelm-service-v1-b1-evaluation-input-lock/1"
            and selection_lock.get("phase") == "SELECTION", "selection input lock contract drift")
    require(manifest.get("evaluationSourceSetSha256")
            == selection_lock.get("evaluationSourceSetSha256"),
            "selection manifest/input-lock source digest drift")
    require(manifest.get("truthCensus") == selection_truth,
            "selection manifest/truth census drift")
    affine = read_json(bundle / "affine.json")
    require(set(affine.get("models", {})) == set(MODELS), "selection affine model set")
    for model in MODELS:
        record = affine["models"][model]
        require(record.get("state") == "AFFINE" and float(record.get("b", -1)) >= 0,
                f"selection {model} affine unavailable")
    require(manifest.get("evaluator") == path_pin(SCRIPT, root), "selection manifest evaluator drift")
    return manifest, review, affine, verified


def run_confirmation(paths: EvaluationPaths, output_dir: Path, *, expected_plan_sha256: str,
                     expected_score_manifest_sha256: str,
                     expected_score_review_sha256: str,
                     selection_manifest_path: Path, selection_review_path: Path,
                     expected_selection_manifest_sha256: str,
                     expected_selection_review_sha256: str,
                     spec: EvaluationSpec = EvaluationSpec(),
                     fixed_pins: Mapping[str, tuple[int, str]] = SOURCE_PINS,
                     publish_failure: bool = True) -> dict[str, Any]:
    phase = "confirm"
    output_dir = Path(output_dir).resolve()
    selection_manifest_path = Path(selection_manifest_path).resolve()
    selection_review_path = Path(selection_review_path).resolve()
    staging: Path | None = None
    started_at, started = utc_now(), time.monotonic()
    try:
        staging = _new_staging(output_dir)
        # This is the first gate and intentionally touches only the already-published
        # selection bundle/review and evaluator, never labels or evaluation sources.
        selection_manifest, selection_review, affine_payload, verified_selection_files = verify_selection_gate(
            selection_manifest_path, selection_review_path,
            expected_selection_manifest_sha256, expected_selection_review_sha256,
            paths.root, paths.team_root)
        (source_files, chain, score_files, predictions_path, als_ids, roles, role_digest,
         training_user_audit) = build_source_state(
            paths, expected_plan_sha256, expected_score_manifest_sha256,
            expected_score_review_sha256, spec, fixed_pins)
        label_size, label_sha256 = fixed_pins["labels"]
        verify_pin(paths.labels, {"bytes": label_size, "sha256": label_sha256},
                   "fixed evaluation labels")
        b1_scores = load_predictions(predictions_path, paths.score_axis, spec)
        truth, integrity = prepare_truth(paths, spec, roles, als_ids, b1_scores)
        integrity["roleMembershipSha256"] = role_digest
        integrity["trainingUserIsolation"] = training_user_audit
        _add_source_file(source_files, paths.labels, paths.root)
        selection_bundle = selection_manifest_path.parent
        evaluator_key = str(path_pin(SCRIPT, paths.root)["path"])
        selection_phase_files = {key: record for key, record in verified_selection_files.items()
                                 if key != evaluator_key}
        lock = evaluation_lock("CONFIRMATION", source_files, selection_phase_files)
        selection_lock = read_json(selection_bundle / "evaluation-input-lock.json")
        selection_truth = read_json(selection_bundle / "truth-integrity.json")
        require(selection_lock.get("evaluationSourceSetSha256") == lock["evaluationSourceSetSha256"],
                "selection/confirmation evaluation source set drift")
        require(selection_truth == integrity, "selection/confirmation truth census drift")
        require(selection_manifest.get("truthCensus") == integrity, "selection manifest truth census drift")
        confirmation = truth[truth.role.eq("CONFIRMATION")]
        require(confirmation.uid.nunique() == spec.confirmation_users, "confirmation user census")
        metrics, bootstrap, census, gates = evaluate_phase(confirmation, affine_payload["models"], spec)
        current_score_reference = score_reference_payload(paths, chain, score_files, predictions_path)
        require(read_json(selection_bundle / "score-reference.json") == current_score_reference,
                "selection/confirmation score reference drift")

        selection_reference = {
            "schemaVersion": "feelm-service-v1-b1-selection-reference/1",
            "selectionManifest": path_pin(selection_manifest_path, paths.root),
            "selectionReview": path_pin(selection_review_path, paths.root),
            "evaluationInputLock": path_pin(selection_bundle / "evaluation-input-lock.json", paths.root),
            "truthIntegrity": path_pin(selection_bundle / "truth-integrity.json", paths.root),
            "scoreReference": path_pin(selection_bundle / "score-reference.json", paths.root),
            "command": path_pin(selection_bundle / "command.json", paths.root),
            "roleMembership": path_pin(selection_bundle / "role-membership.csv", paths.root),
            "affine": path_pin(selection_bundle / "affine.json", paths.root),
            "metrics": path_pin(selection_bundle / "metrics.json", paths.root),
            "bootstrapSummary": path_pin(selection_bundle / "bootstrap-summary.json", paths.root),
            "strataCensus": path_pin(selection_bundle / "strata-census.json", paths.root),
            "gates": path_pin(selection_bundle / "gates.json", paths.root),
            "runLog": path_pin(selection_bundle / "run.log", paths.root),
            "evaluator": path_pin(SCRIPT, paths.root),
            "requiredSelectionGate": "PASS",
            "selectionReviewStatus": selection_review.get("status"),
        }
        write_json(staging / "selection-reference.json", selection_reference)
        write_json(staging / "score-reference.json", current_score_reference)
        write_json(staging / "evaluation-input-lock.json", lock)
        write_json(staging / "truth-integrity.json", integrity)
        write_json(staging / "command.json", {
            "phase": phase,
            "selectionManifest": path_pin(selection_manifest_path, paths.root),
            "selectionReview": path_pin(selection_review_path, paths.root),
            "expectedSelectionManifestSha256": expected_selection_manifest_sha256,
            "expectedSelectionReviewSha256": expected_selection_review_sha256,
            "expectedPlanSha256": expected_plan_sha256,
            "expectedScoreManifestSha256": expected_score_manifest_sha256,
            "expectedScoreReviewSha256": expected_score_review_sha256,
            "bootstrapSamples": spec.bootstrap_samples,
        })
        write_json(staging / "metrics.json", {"phase": "CONFIRMATION", **metrics})
        write_json(staging / "bootstrap-summary.json", {"phase": "CONFIRMATION", **bootstrap})
        write_json(staging / "strata-census.json", {"phase": "CONFIRMATION", **census})
        write_json(staging / "gates.json", {"phase": "CONFIRMATION", **gates})
        write_text(staging / "run.log", (
            f"{started_at} phase=confirm selection_gate=PASS selection_review=PASS "
            f"truth_rows={len(truth)} confirmation_gate={gates['requiredGate']}\n"))
        require(_bundle_inventory(staging) == _confirmation_files(), "confirmation staging inventory")

        reverify_source_inventory(source_files, paths.root)
        reverify_ancestor_bundle_inventories(chain)
        require(lock["evaluationSourceSetSha256"] == canonical_inventory_digest(source_files),
                "confirmation source set drift")
        (_, _, final_affine, final_selection_files) = verify_selection_gate(
            selection_manifest_path, selection_review_path,
            expected_selection_manifest_sha256, expected_selection_review_sha256,
            paths.root, paths.team_root)
        require(final_affine == affine_payload, "selection affine changed during confirmation")
        require(final_selection_files == verified_selection_files,
                "selection reviewed file set changed during confirmation")
        manifest = _manifest_payload(
            staging, phase="CONFIRMATION", status="B1_CONFIRMATION_COMPLETE_AUDIT_PENDING",
            gate_key="requiredConfirmationGate", gate_value=str(gates["requiredGate"]),
            evaluator=path_pin(SCRIPT, paths.root), started_at=started_at,
            seconds=time.monotonic() - started,
            source_digest=lock["evaluationSourceSetSha256"], truth=integrity)
        write_json(staging / "manifest.json", manifest)
        require(_bundle_inventory(staging) == _confirmation_files() | {"manifest.json"},
                "confirmation final inventory")
        reverify_source_inventory(source_files, paths.root)
        reverify_ancestor_bundle_inventories(chain)
        _publish(staging, output_dir)
        staging = None
        return manifest
    except BaseException as error:
        cleanup = True
        if staging is not None and staging.exists():
            try:
                shutil.rmtree(staging)
            except BaseException:
                cleanup = False
        if publish_failure:
            _atomic_failure(output_dir, phase, error, cleanup)
        raise


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)

    def common(command: argparse.ArgumentParser) -> None:
        command.add_argument("--score-manifest", type=Path, required=True)
        command.add_argument("--score-review", type=Path, required=True)
        command.add_argument("--expected-score-manifest-sha256", required=True)
        command.add_argument("--expected-score-review-sha256", required=True)
        command.add_argument("--expected-plan-sha256", required=True)
        command.add_argument("--output-dir", type=Path, required=True)

    selection = subparsers.add_parser("calibrate-select")
    common(selection)
    confirmation = subparsers.add_parser("confirm")
    common(confirmation)
    confirmation.add_argument("--selection-manifest", type=Path, required=True)
    confirmation.add_argument("--selection-review", type=Path, required=True)
    confirmation.add_argument("--expected-selection-manifest-sha256", required=True)
    confirmation.add_argument("--expected-selection-review-sha256", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    require(args.expected_plan_sha256.lower() == PLAN_SHA256,
            "CLI plan pin differs from independently reviewed plan")
    paths = production_paths(args.score_manifest, args.score_review)
    common = {
        "expected_plan_sha256": args.expected_plan_sha256,
        "expected_score_manifest_sha256": args.expected_score_manifest_sha256,
        "expected_score_review_sha256": args.expected_score_review_sha256,
    }
    if args.action == "calibrate-select":
        result = run_selection(paths, args.output_dir, **common)
        print(json.dumps({"status": result["status"],
                          "requiredSelectionGate": result["requiredSelectionGate"]}), flush=True)
    else:
        result = run_confirmation(
            paths, args.output_dir,
            selection_manifest_path=args.selection_manifest,
            selection_review_path=args.selection_review,
            expected_selection_manifest_sha256=args.expected_selection_manifest_sha256,
            expected_selection_review_sha256=args.expected_selection_review_sha256,
            **common,
        )
        print(json.dumps({"status": result["status"],
                          "requiredConfirmationGate": result["requiredConfirmationGate"]}), flush=True)


if __name__ == "__main__":
    main()
