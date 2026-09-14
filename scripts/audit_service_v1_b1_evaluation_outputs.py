"""Independent audit for Service-v1 B1 selection and confirmation bundles.

The evaluator is deliberately not imported.  This module reopens the immutable
inputs, rebuilds the truth axis, independently recomputes the affine fit,
metrics, resampling summaries and safety gate, and only then permits an atomic
sibling review to be published.  Public CLI settings are the production
contract; the private ``_spec`` and ``_source_pins`` arguments exist only for
small synthetic tests.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import uuid
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as pads
import pyarrow.parquet as pq


RUN_ID = "b1-gbt120-s339-v1-r2"
PREDECESSOR_RUN_ID = "b1-gbt120-s339-v1"
PLAN_SHA256 = "c5b90b0d19374588fc5cb7a1d98cd6510a1c3708df9667131df5eb3826c79d49"
REVIEWED_EVALUATOR_SHA256 = "994b9a0a715473802df1dd0e54506fd865a0b0e530151614d3084ac9dc64aafe"
REVIEWED_SCORE_AUDITOR_SHA256 = "e23e3e4e7b9830172b5a717a08bf38f5bef415155eb4235440b830c434ba5020"
SPARK_IMAGE_ID = "sha256:de611d62db982bb833b0737a7ddd72a7cd49ab1a7dc9128669cdabae39f46df8"
ROLE_DIGEST = "cf92b686ab21a950611084682e46bfd319426d5dbc3c4f58836a4836b3f416c6"
MODELS = ("B0", "B1")
STRATA = ("ALL", "ALS_TARGET_SUPPORTED", "ALS_TARGET_UNSUPPORTED")
TOP_K = (2, 4, 6, 10)
USER_BOOTSTRAP_SEED = 339
MOVIE_BOOTSTRAP_SEED = 340
SIGN_FLIP_SEED = 341

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

SELECTION_FILES = {
    "evaluation-input-lock.json", "truth-integrity.json", "score-reference.json",
    "command.json", "role-membership.csv", "affine.json", "metrics.json",
    "bootstrap-summary.json", "strata-census.json", "gates.json", "run.log",
}
CONFIRMATION_FILES = {
    "selection-reference.json", "score-reference.json", "evaluation-input-lock.json",
    "truth-integrity.json", "command.json", "metrics.json", "bootstrap-summary.json",
    "strata-census.json", "gates.json", "run.log",
}
TARGETS = {
    "selection": {
        "evaluation_input_lock": "evaluation-input-lock.json",
        "truth_integrity": "truth-integrity.json", "score_reference": "score-reference.json",
        "command": "command.json", "role_membership": "role-membership.csv",
        "affine": "affine.json", "metrics": "metrics.json",
        "bootstrap_summary": "bootstrap-summary.json", "strata_census": "strata-census.json",
        "gates": "gates.json", "run_log": "run.log",
    },
    "confirmation": {
        "selection_reference": "selection-reference.json", "score_reference": "score-reference.json",
        "evaluation_input_lock": "evaluation-input-lock.json", "truth_integrity": "truth-integrity.json",
        "command": "command.json", "metrics": "metrics.json",
        "bootstrap_summary": "bootstrap-summary.json", "strata_census": "strata-census.json",
        "gates": "gates.json", "run_log": "run.log",
    },
}


class AuditError(ValueError):
    """A fail-closed contract violation."""


def need(value: Any, message: str) -> None:
    if not value:
        raise AuditError(message)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024**2), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_file(path: Path) -> None:
    need(path.is_file() and not path.is_symlink(), f"not a regular file: {path}")
    for member in (path, *path.parents):
        need(not member.is_symlink()
             and not (hasattr(member, "is_junction") and member.is_junction()),
             f"linked/reparse path forbidden: {path}")


def pin(path: Path, logical: str | None = None) -> dict[str, Any]:
    safe_file(path)
    result: dict[str, Any] = {"bytes": int(path.stat().st_size), "sha256": file_sha256(path)}
    if logical is not None:
        result = {"path": logical, **result}
    return result


def valid_hash(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def valid_pin(record: Any, *, path_required: bool = False) -> None:
    need(isinstance(record, Mapping), "pin must be an object")
    required = {"bytes", "sha256"} | ({"path"} if path_required else set())
    need(set(record) == required, "pin field set drift")
    need(type(record.get("bytes")) is int and record["bytes"] >= 0, "invalid pin bytes")
    need(valid_hash(record.get("sha256")), "invalid lowercase pin sha256")
    if path_required:
        need(isinstance(record.get("path"), str) and record["path"], "pin path missing")


def check_pin(path: Path, record: Mapping[str, Any], label: str) -> dict[str, Any]:
    valid_pin(record, path_required="path" in record)
    actual = pin(path)
    need(actual["bytes"] == record["bytes"] and actual["sha256"] == record["sha256"],
         f"{label} pin mismatch: {path}")
    return actual


def json_object(path: Path) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, value in items:
            need(key not in output, f"duplicate JSON key {key}: {path}")
            output[key] = value
        return output

    safe_file(path)
    try:
        result = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs,
                            parse_constant=lambda value: (_ for _ in ()).throw(
                                AuditError(f"nonfinite JSON token {value}: {path}")))
    except UnicodeDecodeError as error:
        raise AuditError(f"JSON is not UTF-8: {path}") from error
    need(isinstance(result, dict), f"JSON object required: {path}")
    return result


def relative_name(value: Any) -> str:
    need(isinstance(value, str) and value and "\\" not in value and "\x00" not in value,
         "invalid relative inventory name")
    pure = PurePosixPath(value)
    need(not pure.is_absolute() and ":" not in value
         and all(part not in ("", ".", "..") for part in value.split("/")),
         f"unsafe relative inventory name: {value}")
    return value


def inventory(root: Path) -> dict[str, dict[str, Any]]:
    need(root.is_dir() and not root.is_symlink(), f"bundle directory missing or linked: {root}")
    result: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        need(not path.is_symlink()
             and not (hasattr(path, "is_junction") and path.is_junction()),
             f"linked bundle member: {path}")
        if path.is_file():
            result[relative_name(path.relative_to(root).as_posix())] = pin(path)
    return result


def canonical_digest(records: Mapping[str, Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    for name in sorted(records):
        need(isinstance(name, str) and name and "\x00" not in name and "\n" not in name,
             "invalid canonical record name")
        record = records[name]
        need(isinstance(record, Mapping), f"invalid canonical record: {name}")
        need(type(record.get("bytes")) is int and record["bytes"] >= 0, f"invalid bytes: {name}")
        need(valid_hash(record.get("sha256")), f"invalid sha256: {name}")
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(record["bytes"]).encode("ascii"))
        digest.update(b"\0")
        digest.update(record["sha256"].encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def json_equal(actual: Any, expected: Any, label: str = "payload") -> None:
    """Compare JSON trees, tolerating only sub-picounit floating arithmetic noise."""
    if isinstance(expected, Mapping):
        need(isinstance(actual, Mapping), f"{label} object type drift")
        need(set(actual) == set(expected), f"{label} key set drift")
        for key in expected:
            json_equal(actual[key], expected[key], f"{label}.{key}")
        return
    if isinstance(expected, list):
        need(isinstance(actual, list) and len(actual) == len(expected), f"{label} list drift")
        for index, item in enumerate(expected):
            json_equal(actual[index], item, f"{label}[{index}]")
        return
    if type(expected) is float:
        need(type(actual) in (int, float) and not isinstance(actual, bool), f"{label} numeric type drift")
        need(math.isfinite(float(actual)) == math.isfinite(expected), f"{label} finite drift")
        if math.isfinite(expected):
            need(math.isclose(float(actual), expected, rel_tol=1e-12, abs_tol=1e-12),
                 f"{label} numeric drift: {actual!r} != {expected!r}")
        else:
            need(float(actual) == expected, f"{label} infinity drift")
        return
    need(type(actual) is type(expected) and actual == expected,
         f"{label} drift: {actual!r} != {expected!r}")


@dataclass(frozen=True)
class AuditSpec:
    score_rows: int = 93_230
    catalog_movies: int = 85_517
    label_rows: int = 18_959
    cap10_targets: int = 18_646
    cap10_users: int = 270
    extra_labels: int = 313
    calibration_users: int = 45
    selection_users: int = 45
    confirmation_users: int = 180
    als_movies: int = 45_074
    training_rows: int = 4_997_069
    training_users: int = 39_859
    minimum_users: int = 30
    minimum_movies: int = 30
    minimum_rows: int = 200
    bootstrap_samples: int = 10_000
    role_digest: str = ROLE_DIGEST
    canonical: bool = True


@dataclass(frozen=True)
class Roots:
    standalone: Path
    team: Path
    evaluator_override: Path | None = None
    auditor_override: Path | None = None

    def bundle(self, phase: str) -> Path:
        need(phase in TARGETS, "unknown evaluation audit phase")
        base = self.standalone / "outputs/recommendation-evidence/service-v1-pretraining-20260913"
        return base / ("b1-vs-b0-selection-v1" if phase == "selection"
                       else "b1-vs-b0-confirmation-v1")

    def review(self, phase: str) -> Path:
        bundle = self.bundle(phase)
        return bundle.with_name(bundle.name + "-result-review.json")

    def evaluator(self) -> Path:
        return (self.evaluator_override.resolve() if self.evaluator_override is not None
                else self.standalone / "scripts/evaluate_service_v1_b1.py")

    def auditor(self) -> Path:
        return (self.auditor_override.resolve() if self.auditor_override is not None
                else self.standalone / "scripts/audit_service_v1_b1_evaluation_outputs.py")

    def plan(self) -> Path:
        return self.standalone / "docs/recommendation/plans/service-v1-b1-spark-runner.md"

    def data_paths(self) -> dict[str, Path]:
        return {
            "artifact_manifest": self.team / "pipeline/artifacts/service-v1.json",
            "evaluation_contract": self.team / "pipeline/docs/service-v1/EVALUATION.md",
            "contexts": self.standalone / "outputs/recommendation-evidence/text339/contexts.json",
            "catalog": self.standalone / "outputs/recommendation-evidence/text339/catalog.parquet",
            "labels": self.standalone / "outputs/recommendation-evidence/text339/labels.parquet",
            "evaluation_seal": self.standalone / "outputs/recommendation-evidence/text339/evaluation-seal.json",
            "roles": self.standalone / "outputs/recommendation-evidence/final344/roles.csv",
            "metadata": self.standalone / "outputs/recommendation-evidence/rec-ev-045/metadata.parquet",
            "score_axis": self.standalone / "outputs/recommendation-evidence/foundation340/RH/score.parquet",
            "ratings": self.standalone / "outputs/recommendation-evidence/text339/ratings.parquet",
            "b0_predictions": self.standalone / "outputs/recommendation-evidence/final344/GBT120_s339/predictions.npy",
            "b0_seal": self.standalone / "outputs/recommendation-evidence/final344/GBT120_s339-seal.json",
        }

    def score_auditor_sources(self) -> dict[str, Path]:
        base = self.standalone / "outputs/recommendation-evidence"
        masked = base / "service-v1-pretraining-20260913/b1-masked-views-v1"
        result = {
            "contract/training-recipe.v1.json":
                self.team / "pipeline/configs/service-v1/training-recipe.v1.json",
            "contract/service-v1.json": self.team / "pipeline/artifacts/service-v1.json",
            "contract/MODELS.md": self.team / "pipeline/docs/service-v1/MODELS.md",
            "contract/feature-schema.v1.json":
                self.team / "pipeline/configs/service-v1/feature-schema.v1.json",
            "source/natural-train.parquet": base / "foundation340/RH/train.parquet",
            "source/natural-score.parquet": base / "foundation340/RH/score.parquet",
            "source/tmdb-masked-train.parquet": masked / "tmdb-masked-rh230.parquet",
            "source/masked-manifest.json": masked / "manifest.json",
            "source/views-manifest.json": masked / "views-manifest.json",
            "source/masked-review.json": masked.with_name(masked.name + "-result-review.json"),
            "implementation/service-v1-b1-spark-runner.md": self.plan(),
            "implementation/test_service_v1_b1_gbt_runner.py":
                self.standalone / "tests/test_service_v1_b1_gbt_runner.py",
        }
        for name in (
            "run_service_v1_b1_gbt.py", "service_v1_b1_spark_worker.py",
            "combination340_models.py", "rec046_common.py",
        ):
            result["implementation/" + name] = self.standalone / "scripts" / name
        return result

    def logical(self, path: Path) -> str:
        resolved = path.resolve()
        if resolved.is_relative_to(self.standalone.resolve()):
            return os.path.relpath(resolved, self.standalone.resolve()).replace("\\", "/")
        if resolved.is_relative_to(self.team.resolve()):
            return "team/" + os.path.relpath(resolved, self.team.resolve()).replace("\\", "/")
        if self.evaluator_override is not None and resolved == self.evaluator_override.resolve():
            return os.path.relpath(resolved, self.standalone.resolve()).replace("\\", "/")
        if self.auditor_override is not None and resolved == self.auditor_override.resolve():
            return os.path.relpath(resolved, self.standalone.resolve()).replace("\\", "/")
        raise AuditError(f"path outside approved roots: {path}")


def resolve_record(record: Mapping[str, Any], roots: Roots, owner: Path, label: str) -> Path:
    valid_pin(record, path_required=True)
    raw = str(record["path"])
    normalized = raw.replace("\\", "/")
    candidates: list[Path]
    if normalized.startswith("standalone/"):
        candidates = [roots.standalone / normalized.removeprefix("standalone/")]
    elif normalized.startswith("team/"):
        candidates = [roots.team / normalized.removeprefix("team/")]
    else:
        raw_path = Path(raw)
        if raw_path.is_absolute():
            candidates = [raw_path]
        else:
            candidates = [roots.standalone / raw_path, owner.parent / raw_path]
    existing: list[Path] = []
    for candidate in candidates:
        if candidate.exists():
            resolved = candidate.resolve()
            if resolved not in existing:
                existing.append(resolved)
    need(len(existing) == 1, f"{label} path missing or ambiguous: {raw}")
    result = existing[0]
    need(result.is_relative_to(roots.standalone.resolve())
         or result.is_relative_to(roots.team.resolve())
         or (roots.evaluator_override is not None
             and result == roots.evaluator_override.resolve())
         or (roots.auditor_override is not None
             and result == roots.auditor_override.resolve()), f"{label} escapes approved roots")
    check_pin(result, record, label)
    return result


def path_record(path: Path, roots: Roots) -> dict[str, Any]:
    return pin(path, roots.logical(path))


def verify_bundle_inventory(bundle: Path, manifest: Mapping[str, Any], required: set[str]) -> dict[str, dict[str, Any]]:
    actual = inventory(bundle)
    need(set(actual) == required | {"manifest.json"}, "evaluation bundle physical inventory drift")
    declared = manifest.get("files")
    need(isinstance(declared, Mapping) and set(declared) == required,
         "evaluation manifest file inventory drift")
    for name in sorted(required):
        relative_name(name)
        record = declared[name]
        valid_pin(record, path_required=True)
        need(record["path"] == name, f"manifest file path drift: {name}")
        check_pin(bundle / name, record, f"manifest file {name}")
    need("manifest.json" not in declared, "manifest self-pin forbidden")
    return actual


def verify_record_map(records: Any, roots: Roots, owner: Path, label: str) -> tuple[dict[str, Path], dict[str, dict[str, Any]]]:
    need(isinstance(records, Mapping), f"{label} must be an object")
    paths: dict[str, Path] = {}
    normalized: dict[str, dict[str, Any]] = {}
    resolved_seen: set[Path] = set()
    for name in sorted(records):
        need(isinstance(name, str) and name and "\x00" not in name and "\n" not in name,
             f"{label} invalid key")
        record = records[name]
        valid_pin(record, path_required=True)
        need(name == record["path"], f"{label} key/path drift: {name}")
        resolved = resolve_record(record, roots, owner, f"{label}.{name}")
        need(resolved not in resolved_seen, f"{label} duplicate resolved path: {resolved}")
        resolved_seen.add(resolved)
        paths[name] = resolved
        normalized[name] = {"bytes": record["bytes"], "sha256": record["sha256"]}
    return paths, normalized


def verify_evaluation_lock(lock_path: Path, phase: str, manifest: Mapping[str, Any],
                           roots: Roots) -> tuple[dict[str, Path], dict[str, Path]]:
    lock = json_object(lock_path)
    expected_keys = {
        "schemaVersion", "phase", "files", "phaseInputs",
        "evaluationSourceSetSha256", "inputSetSha256", "labelsPreviouslyOpened",
        "freshBlindHoldout", "fitOrScoreMutationAuthorized",
    }
    need(set(lock) == expected_keys, "evaluation input lock field set drift")
    need(lock.get("schemaVersion") == "feelm-service-v1-b1-evaluation-input-lock/1",
         "evaluation input lock schema drift")
    need(lock.get("phase") == phase.upper(), "evaluation input lock phase drift")
    need(lock.get("labelsPreviouslyOpened") is True and lock.get("freshBlindHoldout") is False
         and lock.get("fitOrScoreMutationAuthorized") is False,
         "evaluation input lock disclosure/authorization drift")
    sources, source_records = verify_record_map(lock.get("files"), roots, lock_path, "evaluation sources")
    phase_inputs, phase_records = verify_record_map(lock.get("phaseInputs"), roots, lock_path,
                                                    "evaluation phase inputs")
    need(not set(sources).intersection(phase_inputs), "evaluation source/phase-input collision")
    source_digest = canonical_digest(source_records)
    combined = {**source_records, **phase_records}
    need(lock.get("evaluationSourceSetSha256") == source_digest,
         "evaluation source-set digest drift")
    need(lock.get("inputSetSha256") == canonical_digest(combined),
         "evaluation input-set digest drift")
    need(manifest.get("evaluationSourceSetSha256") == source_digest,
         "manifest/evaluation source-set digest drift")
    if phase == "selection":
        need(not phase_inputs, "selection phaseInputs must be empty")
    else:
        need(bool(phase_inputs), "confirmation selection phaseInputs missing")
    return sources, phase_inputs


def loose_pin(record: Any) -> dict[str, Any]:
    """Return the immutable path/bytes/hash core from a review target record."""
    need(isinstance(record, Mapping), "review target pin must be an object")
    core = {key: record.get(key) for key in ("path", "bytes", "sha256")}
    valid_pin(core, path_required=True)
    return core


def resolve_loose_record(record: Mapping[str, Any], roots: Roots, owner: Path, label: str) -> Path:
    return resolve_record(loose_pin(record), roots, owner, label)


def record_core_equal(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    try:
        return (str(left.get("path")) == str(right.get("path"))
                and type(left.get("bytes")) is int and left.get("bytes") == right.get("bytes")
                and str(left.get("sha256")) == str(right.get("sha256")))
    except (TypeError, ValueError):
        return False


def resolve_logical_path(value: str, roots: Roots, owner: Path) -> Path:
    """Resolve a logical dependency-fingerprint path without trusting it as a pin."""
    normalized = value.replace("\\", "/")
    if normalized.startswith("team/"):
        candidate = roots.team / normalized.removeprefix("team/")
    elif normalized.startswith("standalone/"):
        candidate = roots.standalone / normalized.removeprefix("standalone/")
    else:
        root_candidate = roots.standalone / value
        owner_candidate = owner.parent / value
        candidates = [p.resolve() for p in (root_candidate, owner_candidate) if p.exists()]
        candidates = list(dict.fromkeys(candidates))
        need(len(candidates) == 1, f"logical dependency path missing or ambiguous: {value}")
        candidate = candidates[0]
    result = candidate.resolve()
    need(result.is_relative_to(roots.standalone.resolve())
         or result.is_relative_to(roots.team.resolve()), "logical dependency escapes approved roots")
    return result


def chain_contains(chain_paths: Mapping[str, Path], path: Path, record: Mapping[str, Any], label: str) -> None:
    matches = [name for name, current in chain_paths.items() if current == path.resolve()]
    need(len(matches) == 1, f"verified chain missing or duplicates {label}")
    # The chain record was already rehashed; also prove it is the same immutable pin.
    name = matches[0]
    need(name == record.get("path") and check_pin(path, record, label)["sha256"] == record["sha256"],
         f"verified chain pin drift for {label}")


def verify_review_target(review: Mapping[str, Any], review_path: Path,
                         expected: Mapping[str, Path], roots: Roots, phase: str) -> None:
    target = review.get("target")
    need(isinstance(target, Mapping) and set(target) == set(expected),
         f"{phase} review target set drift")
    for key, path in expected.items():
        record = target[key]
        resolved = resolve_loose_record(record, roots, review_path, f"{phase} review target.{key}")
        need(resolved == path.resolve(), f"{phase} review target.{key} path drift")


def verify_fingerprint(review: Mapping[str, Any], review_path: Path, roots: Roots,
                       chain_paths: Mapping[str, Path]) -> dict[str, Any]:
    fingerprint = review.get("dependencyFingerprint")
    need(isinstance(fingerprint, Mapping)
         and set(fingerprint) == {
             "files", "bundleInventories", "auditorImplementation", "dockerImageId"
         }, "score review dependency fingerprint shape drift")
    files = fingerprint.get("files")
    bundles = fingerprint.get("bundleInventories")
    need(isinstance(files, Mapping) and isinstance(bundles, Mapping),
         "score review dependency fingerprint shape drift")
    source_paths = roots.score_auditor_sources()
    output_parent = (roots.standalone /
                     "outputs/recommendation-evidence/service-v1-pretraining-20260913")
    bundles_by_phase = {
        current: output_parent / f"{RUN_ID}-{current}"
        for current in ("preflight", "fit", "score")
    }
    predecessor = output_parent / f"{PREDECESSOR_RUN_ID}-preflight-failure.json"
    expected_file_paths = [*source_paths.values(), predecessor,
                           bundles_by_phase["preflight"].with_name(
                               bundles_by_phase["preflight"].name + "-result-review.json"),
                           bundles_by_phase["fit"].with_name(
                               bundles_by_phase["fit"].name + "-result-review.json")]
    expected_files = {roots.logical(path): pin(path) for path in expected_file_paths}
    expected_bundles = {
        roots.logical(path): inventory(path) for path in bundles_by_phase.values()
    }
    need(files == expected_files, "score review dependency file closure drift")
    need(bundles == expected_bundles, "score review dependency bundle closure drift")
    for logical, record in files.items():
        need(isinstance(logical, str), "dependency file logical path type")
        valid_pin(record)
        path = resolve_logical_path(logical, roots, review_path)
        check_pin(path, record, f"dependency file {logical}")
        # All physical dependencies used to authorize evaluation must be in the
        # evaluator's independently rehashed score-chain closure.
        matches = [current for current in chain_paths.values() if current == path]
        need(len(matches) == 1, f"score chain omitted dependency file: {logical}")
    for logical, expected_inventory in bundles.items():
        need(isinstance(logical, str) and isinstance(expected_inventory, Mapping),
             "dependency bundle inventory shape drift")
        directory = resolve_logical_path(logical, roots, review_path)
        need(directory.is_dir(), f"dependency bundle missing: {logical}")
        actual = inventory(directory)
        for record in expected_inventory.values():
            valid_pin(record)
        need(actual == expected_inventory, f"dependency bundle inventory drift: {logical}")
        for relative, record in expected_inventory.items():
            path = (directory / relative).resolve()
            matches = [current for current in chain_paths.values() if current == path]
            need(len(matches) == 1, f"score chain omitted dependency bundle file: {logical}/{relative}")
            check_pin(path, record, f"dependency bundle file {logical}/{relative}")
    implementation = fingerprint.get("auditorImplementation")
    valid_pin(implementation)
    score_auditor = roots.standalone / "scripts/audit_service_v1_b1_spark_outputs.py"
    check_pin(score_auditor, implementation, "score auditor implementation")
    need(implementation["sha256"] == REVIEWED_SCORE_AUDITOR_SHA256,
         "score auditor is not the reviewed implementation")
    need(fingerprint.get("dockerImageId") == SPARK_IMAGE_ID,
         "score review Docker image identity drift")
    expected_chain = set(expected_file_paths)
    for bundle in bundles_by_phase.values():
        expected_chain.update(path for path in bundle.rglob("*") if path.is_file())
    expected_chain.add(bundles_by_phase["score"].with_name(
        bundles_by_phase["score"].name + "-result-review.json"))
    need(set(chain_paths.values()) == {path.resolve() for path in expected_chain},
         "verified score chain exact closure drift")
    return dict(fingerprint)


def verify_score_review_envelope(review: Mapping[str, Any], review_path: Path,
                                 roots: Roots) -> None:
    expected_keys = {
        "schemaVersion", "phase", "status", "createdAt", "target", "reviewer",
        "dependencyFingerprint", "checks", "evaluationTargetsRead",
        "modelFitPerformed", "readyForService", "scope",
    }
    need(set(review) == expected_keys, "score independent review field set drift")
    created_at = review.get("createdAt")
    need(isinstance(created_at, str), "score independent review createdAt format")
    try:
        parsed = dt.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise AuditError("score independent review createdAt format") from error
    need(parsed.tzinfo is not None, "score independent review createdAt timezone")
    reviewer = review.get("reviewer")
    fingerprint = review.get("dependencyFingerprint")
    need(isinstance(reviewer, Mapping)
         and set(reviewer) == {"implementation", "independentFromRunner"}
         and reviewer.get("independentFromRunner") is True,
         "score independent reviewer contract drift")
    need(isinstance(fingerprint, Mapping)
         and reviewer.get("implementation") == fingerprint.get("auditorImplementation"),
         "score reviewer/fingerprint implementation drift")
    valid_pin(reviewer.get("implementation"))
    score_auditor = roots.standalone / "scripts/audit_service_v1_b1_spark_outputs.py"
    check_pin(score_auditor, reviewer["implementation"], "score review implementation")
    need(isinstance(review.get("checks"), Mapping)
         and review.get("evaluationTargetsRead") is False
         and review.get("modelFitPerformed") is False
         and review.get("readyForService") is False
         and review.get("scope") == (
             "Local immutable bundle integrity and specified numerical parity; "
             "no service acceptance or model quality verdict."
         ), "score independent review safety envelope drift")


def verify_score_reference(reference_path: Path, roots: Roots,
                           sources: Mapping[str, Path], spec: AuditSpec) -> dict[str, Any]:
    reference = json_object(reference_path)
    required = {"schemaVersion", "scoreManifest", "scoreReview", "predictions",
                "fitReference", "scoreStatus", "scoreReviewStatus", "verifiedChainFiles"}
    need(set(reference) == required, "score reference field set drift")
    need(reference.get("schemaVersion") == "feelm-service-v1-b1-score-reference/1",
         "score reference schema drift")
    need(reference.get("scoreStatus") == "B1_NATURAL_SCORE_COMPLETE_AUDIT_PENDING"
         and reference.get("scoreReviewStatus") == "PASS", "score reference status drift")

    chain_paths, _ = verify_record_map(reference.get("verifiedChainFiles"), roots,
                                       reference_path, "verified score chain")
    direct_paths: dict[str, Path] = {}
    for key in ("scoreManifest", "scoreReview", "predictions", "fitReference"):
        record = reference[key]
        direct_paths[key] = resolve_record(record, roots, reference_path, f"score reference {key}")
        chain_contains(chain_paths, direct_paths[key], record, f"score reference {key}")

    score_manifest_path = direct_paths["scoreManifest"]
    score_review_path = direct_paths["scoreReview"]
    predictions_path = direct_paths["predictions"]
    fit_reference_path = direct_paths["fitReference"]
    score_bundle = score_manifest_path.parent
    need(score_manifest_path.name == "manifest.json"
         and score_review_path == score_bundle.with_name(score_bundle.name + "-result-review.json").resolve(),
         "score manifest/review canonical path drift")
    if spec.canonical:
        expected_score_bundle = (roots.standalone /
            "outputs/recommendation-evidence/service-v1-pretraining-20260913" /
            f"{RUN_ID}-score").resolve()
        need(score_bundle == expected_score_bundle, "score bundle runId namespace drift")
    need(predictions_path == (score_bundle / "score/predictions.parquet").resolve()
         and fit_reference_path == (score_bundle / "fit-reference.json").resolve(),
         "score direct reference path drift")

    score_manifest = json_object(score_manifest_path)
    score_review = json_object(score_review_path)
    need((not spec.canonical or score_manifest.get("runId") == RUN_ID)
         and score_manifest.get("status") == "B1_NATURAL_SCORE_COMPLETE_AUDIT_PENDING"
         and score_manifest.get("evaluationAuthorized") is False
         and score_manifest.get("readyForService") is False,
         "score manifest state/runId drift")
    need(score_review.get("schemaVersion") == "feelm-service-v1-b1-result-review/1"
         and score_review.get("phase") == "score" and score_review.get("status") == "PASS",
         "score independent review state drift")
    verify_score_review_envelope(score_review, score_review_path, roots)
    score_required = {
        "fit-reference.json", "score-input-lock.json", "command.json",
        "score/analyzed-plan.txt", "score/predictions.parquet", "resource.json", "run.log",
    }
    verify_bundle_inventory(score_bundle, score_manifest, score_required)
    expected_targets = {
        "manifest": score_manifest_path, "fit_reference": fit_reference_path,
        "score_input_lock": score_bundle / "score-input-lock.json",
        "command": score_bundle / "command.json",
        "analyzed_plan": score_bundle / "score/analyzed-plan.txt",
        "predictions": predictions_path, "resource": score_bundle / "resource.json",
        "run_log": score_bundle / "run.log",
        "outer_runner": roots.standalone / "scripts/run_service_v1_b1_gbt.py",
        "spark_worker": roots.standalone / "scripts/service_v1_b1_spark_worker.py",
    }
    verify_review_target(score_review, score_review_path, expected_targets, roots, "score")
    for key, path in expected_targets.items():
        record = loose_pin(score_review["target"][key])
        chain_contains(chain_paths, path, record, f"score review target.{key}")
    verify_fingerprint(score_review, score_review_path, roots, chain_paths)

    fit_reference = json_object(fit_reference_path)
    need(fit_reference.get("schemaVersion") == "feelm-service-v1-b1-fit-reference/1",
         "fit reference schema drift")
    fit_manifest = resolve_record(fit_reference.get("fitManifest"), roots, fit_reference_path,
                                  "fit reference manifest")
    fit_review = resolve_record(fit_reference.get("fitReview"), roots, fit_reference_path,
                                "fit reference review")
    model_inventory = resolve_record(fit_reference.get("modelInventory"), roots, fit_reference_path,
                                     "fit reference model inventory")
    for key, path in (("fitManifest", fit_manifest), ("fitReview", fit_review),
                      ("modelInventory", model_inventory)):
        chain_contains(chain_paths, path, fit_reference[key], f"fit reference {key}")
    need((not spec.canonical or fit_manifest.parent == (roots.standalone /
              "outputs/recommendation-evidence/service-v1-pretraining-20260913" /
              f"{RUN_ID}-fit").resolve())
         and fit_review == fit_manifest.parent.with_name(fit_manifest.parent.name + "-result-review.json").resolve(),
         "fit ancestor namespace/review drift")
    fit_manifest_payload = json_object(fit_manifest)
    fit_review_payload = json_object(fit_review)
    need((not spec.canonical or fit_manifest_payload.get("runId") == RUN_ID)
         and fit_manifest_payload.get("status") == "B1_MODEL_FIT_COMPLETE_AUDIT_PENDING",
         "fit ancestor manifest state drift")
    need(fit_review_payload.get("status") == "PASS" and fit_review_payload.get("phase") == "fit",
         "fit ancestor review state drift")
    model_payload = json_object(model_inventory)
    model_records = model_payload.get("files")
    need(isinstance(model_records, list) and model_records, "fit model file inventory missing")
    model_root = fit_manifest.parent / "model/native"
    actual_model = inventory(model_root)
    expected_model: dict[str, dict[str, Any]] = {}
    for record in model_records:
        valid_pin(record, path_required=True)
        name = relative_name(record["path"])
        need(name not in expected_model, "duplicate native model inventory path")
        expected_model[name] = {"bytes": record["bytes"], "sha256": record["sha256"]}
    need(actual_model == expected_model, "native model physical inventory drift")
    need(model_payload.get("inventorySha256") == canonical_digest(expected_model)
         and fit_reference.get("modelFileSetSha256") == canonical_digest(expected_model),
         "native model inventory digest drift")
    need(fit_reference.get("modelFiles") == model_records, "fit reference model file list drift")
    for name, record in expected_model.items():
        path = (model_root / name).resolve()
        matches = [current for current in chain_paths.values() if current == path]
        need(len(matches) == 1, f"verified chain omitted native model file: {name}")

    table_schema = pq.read_schema(predictions_path)
    expected_schema = pa.schema([("row_id", pa.int64()), ("uid", pa.int32()),
                                 ("prediction", pa.float64())])
    need(table_schema.equals(expected_schema, check_metadata=False), "B1 prediction schema drift")
    need(pq.ParquetFile(predictions_path).metadata.num_rows == spec.score_rows,
         "B1 prediction row count drift")
    need(score_manifest.get("predictionSha256") == file_sha256(predictions_path),
         "score manifest prediction hash drift")
    # The evaluation lock must include exactly the same chain records, not a
    # second independently chosen score lineage.
    for name, path in chain_paths.items():
        need(name in sources and sources[name] == path,
             f"evaluation source lock omitted score-chain record: {name}")
    return {
        "reference": reference, "chainPaths": chain_paths, "predictions": predictions_path,
        "scoreManifest": score_manifest_path, "scoreReview": score_review_path,
        "scoreBundle": score_bundle,
    }


def verify_fixed_sources(roots: Roots, sources: Mapping[str, Path], expected_evaluator_sha256: str,
                         source_pins: Mapping[str, tuple[int, str]], *, canonical: bool) -> dict[str, Path]:
    need(valid_hash(expected_evaluator_sha256), "explicit lowercase evaluator SHA256 required")
    expected = roots.data_paths()
    expected["plan"] = roots.plan()
    expected["evaluator"] = roots.evaluator()
    inverse: dict[Path, str] = {}
    for key, path in expected.items():
        resolved = path.resolve()
        matches = [name for name, current in sources.items() if current == resolved]
        need(len(matches) == 1, f"evaluation source lock must contain exactly one {key}")
        inverse[resolved] = matches[0]
        if key in source_pins:
            size, digest = source_pins[key]
            need(pin(resolved) == {"bytes": size, "sha256": digest}, f"fixed source pin drift: {key}")
    if canonical:
        need(pin(roots.plan())["sha256"] == PLAN_SHA256, "reviewed B1 plan SHA256 drift")
    need(pin(roots.evaluator())["sha256"] == expected_evaluator_sha256,
         "evaluator implementation SHA256 drift")
    return expected


def split_roles(path: Path, spec: AuditSpec) -> tuple[dict[int, str], str]:
    frame = pd.read_csv(path)
    need(list(frame.columns) == ["uid", "h10", "role"], "role source schema drift")
    need(not frame.uid.isna().any() and not frame.uid.duplicated().any(), "role UID identity drift")
    need(set(frame.role) == {"calibration", "comparison"}, "unknown original role")
    calibration = frame.loc[frame.role.eq("calibration"), "uid"].astype(np.int64).tolist()
    confirmation = frame.loc[frame.role.eq("comparison"), "uid"].astype(np.int64).tolist()
    need(len(calibration) == spec.calibration_users + spec.selection_users,
         "original calibration role census drift")
    need(len(confirmation) == spec.confirmation_users, "original confirmation role census drift")
    ordered = sorted(calibration, key=lambda uid: (
        hashlib.sha256(f"cal-select-v1:{int(uid)}".encode("utf-8")).digest(), int(uid)))
    roles = {int(uid): ("CALIBRATION" if index < spec.calibration_users else "SELECTION")
             for index, uid in enumerate(ordered)}
    for uid in confirmation:
        need(int(uid) not in roles, "role sets overlap")
        roles[int(uid)] = "CONFIRMATION"
    need(len(roles) == spec.cap10_users, "derived role user census drift")
    counts = pd.Series(list(roles.values())).value_counts().to_dict()
    need(counts == {"CONFIRMATION": spec.confirmation_users,
                    "CALIBRATION": spec.calibration_users,
                    "SELECTION": spec.selection_users}, "derived role counts drift")
    raw = "".join(f"{uid},{roles[uid]}\n" for uid in sorted(roles)).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    need(digest == spec.role_digest, "derived role membership SHA256 drift")
    return roles, digest


def expected_role_membership(roles: Mapping[int, str]) -> bytes:
    lines = ["uid,role\n", *(f"{uid},{roles[uid]}\n" for uid in sorted(roles))]
    return "".join(lines).encode("utf-8")


def verify_training_users(path: Path, roles: Mapping[int, str], spec: AuditSpec) -> dict[str, Any]:
    table = pq.read_table(path, columns=["uid"])
    need(table.schema.equals(pa.schema([("uid", pa.int32())]), check_metadata=False),
         "training ratings UID projection schema drift")
    need(table.num_rows == spec.training_rows and table.column("uid").null_count == 0,
         "training ratings row/null census drift")
    training_uid = np.asarray(table.column("uid").to_numpy(), dtype=np.int32)
    unique = np.unique(training_uid)
    need(len(unique) == spec.training_users, "training user census drift")
    evaluation_uid = np.asarray(sorted(roles), dtype=np.int64)
    overlap = np.intersect1d(unique.astype(np.int64, copy=False), evaluation_uid, assume_unique=True)
    need(not len(overlap), "training/evaluation user overlap")
    return {
        "ratingsRows": len(training_uid), "ratingsUniqueUsers": len(unique),
        "evaluationRoleUsers": len(roles), "trainingEvaluationUidIntersection": 0,
        "projectedColumns": ["uid"], "uidType": "int32",
    }


def verify_als_axis(roots: Roots, artifact_manifest: Path, sources: Mapping[str, Path],
                    spec: AuditSpec) -> tuple[set[int], set[Path]]:
    payload = json_object(artifact_manifest)
    prefix = "outputs/recommendation-evidence/combination340/ALS/item-factors/"
    declared: dict[str, dict[str, Any]] = {}
    for item in payload.get("artifacts", []):
        if isinstance(item, Mapping) and str(item.get("source_path", "")).startswith(prefix):
            name = str(item["source_path"])
            need(name not in declared, "duplicate ALS artifact manifest entry")
            declared[name] = {"bytes": item.get("bytes"), "sha256": item.get("sha256")}
            valid_pin(declared[name])
    need(bool(declared), "artifact manifest contains no ALS factor inventory")
    directory = roots.standalone / prefix
    actual = {prefix + path.relative_to(directory).as_posix(): path
              for path in directory.rglob("*") if path.is_file()}
    need(set(actual) == set(declared), "ALS factor physical inventory drift")
    locked_resolved = set(sources.values())
    parts: list[Path] = []
    for name, path in sorted(actual.items()):
        check_pin(path, declared[name], f"ALS factor {name}")
        need(path.resolve() in locked_resolved, f"evaluation source lock omitted ALS factor: {name}")
        if path.name.startswith("part-") and path.suffix == ".parquet":
            parts.append(path)
    need(bool(parts), "ALS factor parquet parts missing")
    ids = np.asarray(pads.dataset([str(path) for path in parts], format="parquet")
                     .to_table(columns=["id"]).column("id").to_numpy(), dtype=np.int64)
    need(len(ids) == spec.als_movies and len(np.unique(ids)) == spec.als_movies,
         "ALS factor movie census drift")
    return set(map(int, ids)), {path.resolve() for path in actual.values()}


def valid_half_stars(values: np.ndarray) -> bool:
    array = np.asarray(values, dtype=np.float64)
    return bool(np.isfinite(array).all() and ((array >= .5) & (array <= 5.0)).all()
                and np.equal(array * 2.0, np.rint(array * 2.0)).all())


def load_b1_predictions(path: Path, score_axis: Path, spec: AuditSpec) -> pd.DataFrame:
    table = pq.read_table(path)
    schema = pa.schema([("row_id", pa.int64()), ("uid", pa.int32()),
                        ("prediction", pa.float64())])
    need(table.schema.equals(schema, check_metadata=False), "B1 decoded prediction schema drift")
    frame = table.to_pandas()
    row_id = frame.row_id.to_numpy(np.int64, copy=False)
    uid = frame.uid.to_numpy(np.int32, copy=False)
    prediction = frame.prediction.to_numpy(np.float64, copy=False)
    need(len(frame) == spec.score_rows and np.array_equal(row_id, np.arange(spec.score_rows)),
         "B1 prediction row identity drift")
    need(np.isfinite(prediction).all(), "B1 prediction contains nonfinite value")
    axis = pq.read_table(score_axis, columns=["row_id", "uid"])
    need(axis.schema.equals(pa.schema([("row_id", pa.int64()), ("uid", pa.int32())]),
                            check_metadata=False), "natural score identity schema drift")
    natural_rows = np.asarray(axis.column("row_id").to_numpy(), dtype=np.int64)
    natural_uid = np.asarray(axis.column("uid").to_numpy(), dtype=np.int32)
    need(len(natural_rows) == spec.score_rows
         and np.array_equal(natural_rows, np.arange(spec.score_rows)),
         "natural score row identity drift")
    need(np.array_equal(uid, natural_uid), "B1/natural score UID axis drift")
    return frame


def build_truth(roots: Roots, data: Mapping[str, Path], predictions: Path, roles: Mapping[int, str],
                role_digest: str, als_ids: set[int], spec: AuditSpec) -> tuple[pd.DataFrame, dict[str, Any]]:
    seal = json_object(data["evaluation_seal"])
    labels_record = seal.get("files", {}).get("labels.parquet")
    need(isinstance(labels_record, Mapping), "evaluation seal labels pin missing")
    check_pin(data["labels"], labels_record, "evaluation seal labels")
    need(seal.get("labels_previously_opened") is True, "labels previous-open disclosure missing")

    catalog = pq.read_table(data["catalog"])
    need(catalog.num_rows == spec.catalog_movies and "movie_id" in catalog.column_names,
         "catalog movie axis drift")
    movie_axis = np.asarray(catalog.column("movie_id").to_numpy(), dtype=np.int64)
    need(len(np.unique(movie_axis)) == spec.catalog_movies, "catalog movie IDs are not unique")
    metadata_axis = np.asarray(pq.read_table(data["metadata"], columns=["movie_id"])
                               .column("movie_id").to_numpy(), dtype=np.int64)
    need(np.array_equal(metadata_axis, movie_axis), "metadata/catalog movie axis drift")

    contexts_payload = json.loads(data["contexts"].read_text(encoding="utf-8"))
    need(isinstance(contexts_payload, list), "contexts must be an array")
    cap10 = [item for item in contexts_payload
             if isinstance(item, Mapping) and int(item.get("cap", -1)) == 10]
    context_uids = [int(item["uid"]) for item in cap10]
    need(len(cap10) == spec.cap10_users and len(set(context_uids)) == spec.cap10_users,
         "cap10 context census drift")
    need(set(context_uids) == set(roles), "cap10/role user identity drift")

    b1 = load_b1_predictions(predictions, data["score_axis"], spec)
    b0 = np.load(data["b0_predictions"], allow_pickle=False)
    need(isinstance(b0, np.ndarray) and b0.shape == (spec.score_rows,)
         and np.isfinite(b0).all(), "B0 prediction axis drift")
    b0_seal = json_object(data["b0_seal"])
    b0_record = b0_seal.get("files", {}).get("GBT120_s339/predictions.npy")
    need(isinstance(b0_record, Mapping), "B0 prediction seal pin missing")
    check_pin(data["b0_predictions"], b0_record, "B0 sealed predictions")

    pieces: list[pd.DataFrame] = []
    for context in sorted(cap10, key=lambda item: int(item["uid"])):
        uid_value = int(context["uid"])
        ei = np.asarray(context.get("ei", []), dtype=np.int64)
        oi = np.asarray(context.get("oi", []), dtype=np.int64)
        start, stop = int(context["start"]), int(context["stop"])
        need(stop >= start and len(ei) == stop - start, f"context span drift for uid {uid_value}")
        need(bool(((ei >= 0) & (ei < spec.catalog_movies)).all()),
             f"context catalog index out of range for uid {uid_value}")
        need(len(np.unique(ei)) == len(ei), f"duplicate target index for uid {uid_value}")
        need(not np.intersect1d(ei, oi).size, f"history index leaked into target for uid {uid_value}")
        row_ids = np.arange(start, stop, dtype=np.int64)
        need(stop <= spec.score_rows, f"context score span out of range for uid {uid_value}")
        scored = b1.iloc[start:stop]
        need(np.array_equal(scored.row_id.to_numpy(np.int64), row_ids)
             and bool((scored.uid.to_numpy(np.int64) == uid_value).all()),
             f"context/B1 identity drift for uid {uid_value}")
        ids = movie_axis[ei]
        pieces.append(pd.DataFrame({
            "row_id": row_ids, "uid": np.full(len(ei), uid_value, dtype=np.int64),
            "movie_id": ids, "role": roles[uid_value],
            "b0_raw": b0[row_ids].astype(np.float64, copy=False),
            "b1_raw": scored.prediction.to_numpy(np.float64, copy=False),
            "als_supported": np.fromiter((int(movie_id) in als_ids for movie_id in ids),
                                           dtype=bool, count=len(ids)),
        }))
    need(bool(pieces), "no cap10 truth pieces")
    targets = pd.concat(pieces, ignore_index=True)
    need(len(targets) == spec.cap10_targets
         and not targets.duplicated(["uid", "movie_id"]).any(),
         "cap10 target key/census drift")

    label_table = pq.read_table(data["labels"])
    label_schema = pa.schema([("uid", pa.int64()), ("movie_id", pa.int64()),
                              ("rating", pa.float64())])
    need(label_table.schema.equals(label_schema, check_metadata=False), "label schema drift")
    labels = label_table.to_pandas()
    need(len(labels) == spec.label_rows and not labels.duplicated(["uid", "movie_id"]).any(),
         "label key/census drift")
    need(valid_half_stars(labels.rating.to_numpy(np.float64)), "labels are not finite half-stars")
    joined = targets.merge(labels, on=["uid", "movie_id"], how="left", validate="one_to_one",
                           indicator=True)
    need(len(joined) == spec.cap10_targets and joined._merge.eq("both").all()
         and joined.rating.notna().all(), "cap10 truth join incomplete")
    joined = joined.drop(columns="_merge")
    target_keys = pd.MultiIndex.from_frame(targets[["uid", "movie_id"]])
    label_keys = pd.MultiIndex.from_frame(labels[["uid", "movie_id"]])
    extras = int((~label_keys.isin(target_keys)).sum())
    need(extras == spec.extra_labels, "non-cap10 label census drift")
    counts = joined.role.value_counts().to_dict()
    need(set(counts) == {"CALIBRATION", "SELECTION", "CONFIRMATION"},
         "truth role coverage drift")
    key_bytes = "".join(f"{row.uid},{row.movie_id}\n"
                        for row in joined.sort_values(["uid", "movie_id"]).itertuples()).encode("utf-8")
    training = verify_training_users(data["ratings"], roles, spec)
    integrity = {
        "schemaVersion": "feelm-service-v1-b1-truth-integrity/1",
        "labelsPreviouslyOpened": True, "freshBlindHoldout": False,
        "labelsRows": len(labels), "labelsUniqueKeys": len(label_keys),
        "cap10Contexts": len(cap10), "cap10TargetRows": len(targets),
        "cap10UniqueKeys": len(target_keys), "joinedRows": len(joined),
        "missingRows": 0, "duplicateRows": 0, "excludedLabelRows": extras,
        "excludedReason": "NOT_CAP10_TARGET_KEY",
        "targetKeySha256": hashlib.sha256(key_bytes).hexdigest(),
        "roleRows": {key: int(value) for key, value in sorted(counts.items())},
        "joinKey": ["uid", "catalog.movie_id[ei]"],
        "roleMembershipSha256": role_digest,
        "trainingUserIsolation": training,
    }
    return joined, integrity


def fit_affine(frame: pd.DataFrame, column: str) -> dict[str, Any]:
    need(not frame.empty and not frame.duplicated(["uid", "movie_id"]).any(),
         "affine calibration rows invalid")
    raw = frame[column].to_numpy(np.float64)
    rating = frame.rating.to_numpy(np.float64)
    need(np.isfinite(raw).all() and valid_half_stars(rating), "affine calibration values invalid")
    counts = frame.groupby("uid", sort=True).size()
    need(bool((counts > 0).all()), "affine calibration user has no row")
    weight = 1.0 / frame.uid.map(counts).to_numpy(np.float64)
    weight = weight / weight.sum()
    raw_mean = float(np.dot(weight, raw))
    rating_mean = float(np.dot(weight, rating))
    variance = float(np.dot(weight, np.square(raw - raw_mean)))
    covariance = float(np.dot(weight, (raw - raw_mean) * (rating - rating_mean)))
    base = {"users": int(frame.uid.nunique()), "rows": len(frame), "variance": variance,
            "covariance": covariance, "a": None, "b": None}
    if variance <= 1e-12:
        return {**base, "state": "CALIBRATION_UNAVAILABLE",
                "reason": "RAW_VARIANCE_LE_1E-12"}
    slope = max(0.0, covariance / variance)
    return {**base, "state": "AFFINE", "reason": None,
            "a": float(rating_mean - slope * raw_mean), "b": float(slope)}


def calibrated(raw: np.ndarray, parameters: Mapping[str, Any]) -> np.ndarray:
    need(parameters.get("state") == "AFFINE"
         and type(parameters.get("a")) in (int, float)
         and type(parameters.get("b")) in (int, float), "affine parameters unavailable")
    need(float(parameters["b"]) >= 0
         and np.isfinite([parameters["a"], parameters["b"]]).all(),
         "affine parameters nonfinite/negative")
    result = float(parameters["a"]) + float(parameters["b"]) * np.asarray(raw, np.float64)
    need(np.isfinite(result).all(), "calibrated prediction contains nonfinite value")
    return result


def ndcg(rating: np.ndarray, score: np.ndarray, movie: np.ndarray, k: int) -> float | None:
    rating = np.asarray(rating, np.float64)
    score = np.asarray(score, np.float64)
    movie = np.asarray(movie, np.int64)
    need(len(rating) == len(score) == len(movie) and len(np.unique(movie)) == len(movie),
         "NDCG axes invalid")
    if len(rating) < k:
        return None
    gain = np.exp2(rating - .5) - 1.0
    discount = 1.0 / np.log2(np.arange(k, dtype=np.float64) + 2.0)
    ranked = np.lexsort((movie, -score))[:k]
    ideal = np.lexsort((movie, -rating))[:k]
    ideal_value = float(np.dot(gain[ideal], discount))
    return None if ideal_value <= 0 else float(np.dot(gain[ranked], discount) / ideal_value)


def user_metrics(frame: pd.DataFrame, score_column: str) -> pd.DataFrame:
    output: list[dict[str, Any]] = []
    for uid, group in frame.groupby("uid", sort=True):
        truth = group.rating.to_numpy(np.float64)
        score = group[score_column].to_numpy(np.float64)
        movie = group.movie_id.to_numpy(np.int64)
        clipped = np.clip(score, .5, 5.0)
        ranked = group.sort_values([score_column, "movie_id"], ascending=[False, True],
                                   kind="mergesort")
        ranked_truth = ranked.rating.to_numpy(np.float64)
        row: dict[str, Any] = {
            "uid": int(uid), "rows": len(group),
            "mse": float(np.square(clipped - truth).mean()),
            "mae": float(np.abs(clipped - truth).mean()),
        }
        for k in TOP_K:
            row[f"ndcg{k}"] = ndcg(truth, score, movie, k)
            row[f"actual{k}"] = float(ranked_truth[:k].mean()) if len(ranked_truth) >= k else None
            row[f"low{k}"] = float((ranked_truth[:k] <= 2.0).mean()) if len(ranked_truth) >= k else None
        for start in range(0, 10, 2):
            stop = start + 2
            row[f"round{start + 1}_{stop}_actual"] = (
                float(ranked_truth[start:stop].mean()) if len(ranked_truth) >= stop else None)
            row[f"round{start + 1}_{stop}_low"] = (
                float((ranked_truth[start:stop] <= 2.0).mean()) if len(ranked_truth) >= stop else None)
        output.append(row)
    columns = ["uid", "rows", "mse", "mae"]
    columns += [f"{metric}{k}" for k in TOP_K for metric in ("ndcg", "actual", "low")]
    columns += [f"round{start + 1}_{start + 2}_{metric}"
                for start in range(0, 10, 2) for metric in ("actual", "low")]
    return (pd.DataFrame(output, columns=columns).sort_values("uid").reset_index(drop=True)
            if output else pd.DataFrame(columns=columns))


def summarize(frame: pd.DataFrame, score_column: str) -> tuple[dict[str, Any], pd.DataFrame]:
    users = user_metrics(frame, score_column)
    clipped = np.clip(frame[score_column].to_numpy(np.float64), .5, 5.0)
    error2 = np.square(clipped - frame.rating.to_numpy(np.float64))
    if len(frame):
        movie_macro: float | None = float(pd.DataFrame({
            "movie_id": frame.movie_id.to_numpy(np.int64), "se": error2,
        }).groupby("movie_id", sort=True).se.mean().mean())
    else:
        movie_macro = None
    result: dict[str, Any] = {
        "support": {"users": int(frame.uid.nunique()), "rows": len(frame),
                    "movies": int(frame.movie_id.nunique())},
        "userMacroMSE": float(users.mse.mean()) if len(users) else None,
        "userMacroMAE": float(users.mae.mean()) if len(users) else None,
        "microMSE": float(error2.mean()) if len(error2) else None,
        "movieMacroMSE": movie_macro, "ranking": {}, "rounds": {},
    }
    for k in TOP_K:
        valid = users[f"ndcg{k}"].dropna()
        actual = users[f"actual{k}"].dropna()
        low = users[f"low{k}"].dropna()
        result["ranking"][str(k)] = {
            "ndcg": float(valid.mean()) if len(valid) else None,
            "ndcgValidUsers": len(valid), "ndcgExcludedUsers": len(users) - len(valid),
            "actualMean": float(actual.mean()) if len(actual) else None,
            "actualValidUsers": len(actual), "lowFraction": float(low.mean()) if len(low) else None,
            "lowValidUsers": len(low),
        }
    for start in range(0, 10, 2):
        stop = start + 2
        actual = users[f"round{start + 1}_{stop}_actual"].dropna()
        low = users[f"round{start + 1}_{stop}_low"].dropna()
        result["rounds"][f"{start + 1}-{stop}"] = {
            "actualMean": float(actual.mean()) if len(actual) else None,
            "actualValidUsers": len(actual), "lowFraction": float(low.mean()) if len(low) else None,
            "lowValidUsers": len(low),
        }
    return result, users


def paired_bootstrap(before: np.ndarray, after: np.ndarray, samples: int,
                     minimum: int) -> dict[str, Any]:
    before, after = np.asarray(before, np.float64), np.asarray(after, np.float64)
    need(before.ndim == after.ndim == 1 and before.shape == after.shape,
         "paired bootstrap axes invalid")
    need(np.isfinite(before).all() and np.isfinite(after).all(), "paired bootstrap nonfinite")
    result: dict[str, Any] = {
        "users": len(before), "before": float(before.mean()) if len(before) else None,
        "after": float(after.mean()) if len(after) else None,
        "delta": float((after - before).mean()) if len(before) else None,
        "ciLow": None, "ciHigh": None, "samples": samples, "seed": USER_BOOTSTRAP_SEED,
        "confidence": .95, "status": "INSUFFICIENT",
    }
    if len(before) < minimum:
        return result
    generator = np.random.Generator(np.random.PCG64(USER_BOOTSTRAP_SEED))
    delta = after - before
    draws = np.empty(samples, dtype=np.float64)
    for start in range(0, samples, 256):
        stop = min(samples, start + 256)
        index = generator.integers(0, len(delta), size=(stop - start, len(delta)))
        draws[start:stop] = delta[index].mean(axis=1)
    result["ciLow"], result["ciHigh"] = map(
        float, np.quantile(draws, [.025, .975], method="linear"))
    result["status"] = "COMPLETE"
    return result


def relative_bootstrap(before: np.ndarray, after: np.ndarray, samples: int,
                       minimum: int) -> dict[str, Any]:
    before, after = np.asarray(before, np.float64), np.asarray(after, np.float64)
    need(before.ndim == after.ndim == 1 and before.shape == after.shape,
         "relative bootstrap axes invalid")
    need(np.isfinite(before).all() and np.isfinite(after).all(), "relative bootstrap nonfinite")

    def ratio(base: np.ndarray | float, candidate: np.ndarray | float) -> np.ndarray | float:
        left, right = np.asarray(base), np.asarray(candidate)
        result = np.empty(np.broadcast_shapes(left.shape, right.shape), dtype=np.float64)
        left = np.broadcast_to(left, result.shape)
        right = np.broadcast_to(right, result.shape)
        positive = left != 0
        result[positive] = (right[positive] - left[positive]) / left[positive]
        result[~positive] = np.where(right[~positive] == 0, 0.0, np.inf)
        return float(result) if result.ndim == 0 else result

    result: dict[str, Any] = {
        "users": len(before), "before": float(before.mean()) if len(before) else None,
        "after": float(after.mean()) if len(after) else None,
        "relativeDelta": ratio(float(before.mean()), float(after.mean())) if len(before) else None,
        "ciLow": None, "ciHigh": None, "samples": samples, "seed": USER_BOOTSTRAP_SEED,
        "confidence": .95, "status": "INSUFFICIENT",
    }
    if len(before) < minimum:
        return result
    generator = np.random.Generator(np.random.PCG64(USER_BOOTSTRAP_SEED))
    draws = np.empty(samples, dtype=np.float64)
    for start in range(0, samples, 256):
        stop = min(samples, start + 256)
        index = generator.integers(0, len(before), size=(stop - start, len(before)))
        draws[start:stop] = ratio(before[index].mean(axis=1), after[index].mean(axis=1))
    if not np.isfinite(draws).all():
        result["status"] = "NONFINITE_DRAW"
        result["ciHigh"] = math.inf if np.isposinf(draws).any() else None
        return result
    result["ciLow"], result["ciHigh"] = map(
        float, np.quantile(draws, [.025, .975], method="linear"))
    result["status"] = "COMPLETE"
    return result


def movie_bootstrap(frame: pd.DataFrame, samples: int, minimum: int) -> dict[str, Any]:
    need(not frame.duplicated(["uid", "movie_id"]).any(), "movie bootstrap duplicate keys")
    users = np.sort(frame.uid.unique().astype(np.int64))
    movies = np.sort(frame.movie_id.unique().astype(np.int64))
    result: dict[str, Any] = {
        "users": len(users), "movies": len(movies), "rows": len(frame),
        "samples": samples, "seed": MOVIE_BOOTSTRAP_SEED, "delta": None,
        "ciLow": None, "ciHigh": None, "relativeDelta": None,
        "relativeCiLow": None, "relativeCiHigh": None, "emptyDraws": 0,
        "status": "INSUFFICIENT",
    }
    if len(users) < minimum:
        return result
    user_index = np.searchsorted(users, frame.uid.to_numpy(np.int64))
    movie_index = np.searchsorted(movies, frame.movie_id.to_numpy(np.int64))
    rating = frame.rating.to_numpy(np.float64)
    se0 = np.square(np.clip(frame.b0_cal.to_numpy(np.float64), .5, 5.0) - rating)
    se1 = np.square(np.clip(frame.b1_cal.to_numpy(np.float64), .5, 5.0) - rating)
    point0 = pd.Series(se0).groupby(frame.uid.to_numpy()).mean().mean()
    point1 = pd.Series(se1).groupby(frame.uid.to_numpy()).mean().mean()
    result["delta"] = float(point1 - point0)
    result["relativeDelta"] = (float((point1 - point0) / point0) if point0 != 0
                               else (0.0 if point1 == 0 else math.inf))
    generator = np.random.Generator(np.random.PCG64(MOVIE_BOOTSTRAP_SEED))
    absolute = np.empty(samples, np.float64)
    relative = np.empty(samples, np.float64)
    empty = 0
    for draw in range(samples):
        selected = generator.integers(0, len(movies), size=len(movies))
        multiplicity = np.bincount(selected, minlength=len(movies)).astype(np.float64)
        weight = multiplicity[movie_index]
        denominator = np.bincount(user_index, weights=weight, minlength=len(users))
        valid = denominator > 0
        if not valid.any():
            empty += 1
            absolute[draw] = np.nan
            relative[draw] = np.nan
            continue
        mean0 = np.bincount(user_index, weights=weight * se0, minlength=len(users))[valid] / denominator[valid]
        mean1 = np.bincount(user_index, weights=weight * se1, minlength=len(users))[valid] / denominator[valid]
        before, after = float(mean0.mean()), float(mean1.mean())
        absolute[draw] = after - before
        relative[draw] = ((after - before) / before if before != 0
                          else (0.0 if after == 0 else math.inf))
    result["emptyDraws"] = empty
    if empty or not np.isfinite(absolute).all() or not np.isfinite(relative).all():
        result["status"] = "NONFINITE_OR_EMPTY_DRAW"
        return result
    result["ciLow"], result["ciHigh"] = map(
        float, np.quantile(absolute, [.025, .975], method="linear"))
    result["relativeCiLow"], result["relativeCiHigh"] = map(
        float, np.quantile(relative, [.025, .975], method="linear"))
    result["status"] = "COMPLETE"
    return result


def sign_flip(before: np.ndarray, after: np.ndarray, samples: int) -> dict[str, Any]:
    before, after = np.asarray(before, np.float64), np.asarray(after, np.float64)
    need(before.ndim == after.ndim == 1 and before.shape == after.shape,
         "sign-flip axes invalid")
    need(np.isfinite(before).all() and np.isfinite(after).all(), "sign-flip nonfinite")
    delta = after - before
    observed = float(delta.mean()) if len(delta) else None
    raw_p = 1.0
    if len(delta) >= 30:
        generator = np.random.Generator(np.random.PCG64(SIGN_FLIP_SEED))
        count = 0
        for start in range(0, samples, 256):
            stop = min(samples, start + 256)
            signs = generator.integers(0, 2, size=(stop - start, len(delta)), dtype=np.int8) * 2 - 1
            count += int(((signs * delta).mean(axis=1) <= observed).sum())
        raw_p = (1 + count) / (samples + 1)
    names = ["B1_MINUS_B0", "B2_MINUS_B0", "B3_MINUS_B0"]
    raw = {names[0]: float(raw_p), names[1]: 1.0, names[2]: 1.0}
    ordered = sorted(names, key=lambda name: (raw[name], name))
    adjusted: dict[str, float] = {}
    rejected = {name: False for name in names}
    running = 0.0
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
        "seed": SIGN_FLIP_SEED, "users": len(delta), "observedB1MinusB0": observed,
        "rawP": raw, "holmAdjustedP": adjusted, "holmRejectAtAlpha05": rejected,
        "missingB2B3PSetToOne": True,
    }


def strata(frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {"ALL": frame, "ALS_TARGET_SUPPORTED": frame[frame.als_supported].copy(),
            "ALS_TARGET_UNSUPPORTED": frame[~frame.als_supported].copy()}


def common_values(left: pd.DataFrame, right: pd.DataFrame, column: str) -> tuple[np.ndarray, np.ndarray]:
    need(np.array_equal(left.uid.to_numpy(), right.uid.to_numpy()), f"paired {column} UID axis drift")
    before = left[column].to_numpy(np.float64)
    after = right[column].to_numpy(np.float64)
    need(np.array_equal(np.isfinite(before), np.isfinite(after)),
         f"paired {column} eligibility drift")
    valid = np.isfinite(before) & np.isfinite(after)
    return before[valid], after[valid]


def decide_gate(census: Mapping[str, Any], bootstrap: Mapping[str, Any],
                affine: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    items: dict[str, Any] = {}
    insufficient = any(affine[model].get("state") != "AFFINE" for model in MODELS)
    failed = False
    for name in STRATA:
        support = census["strata"][name]
        complete = support["status"] == "COMPLETE"
        items[f"support:{name}"] = {**support, "status": "PASS" if complete else "INSUFFICIENT"}
        insufficient |= not complete
        relative = bootstrap["userBlock"][name]["relativeMse"]
        if relative.get("status") != "COMPLETE" or relative.get("ciHigh") is None:
            status = "INSUFFICIENT"
            insufficient = True
        else:
            status = "PASS" if float(relative["ciHigh"]) <= .05 else "FAIL"
            failed |= status == "FAIL"
        items[f"relativeMse:{name}"] = {
            **relative, "status": status, "limit": .05, "comparison": "CI_HIGH_LE_LIMIT"}
    ndcg2 = bootstrap["userBlock"].get("ALL", {}).get("ndcg2Delta", {})
    if ndcg2.get("status") != "COMPLETE" or ndcg2.get("ciLow") is None:
        status = "INSUFFICIENT"
        insufficient = True
    else:
        status = "PASS" if float(ndcg2["ciLow"]) >= -.01 else "FAIL"
        failed |= status == "FAIL"
    items["ndcg2"] = {**ndcg2, "status": status, "limit": -.01,
                      "comparison": "CI_LOW_GE_LIMIT"}
    low2 = bootstrap["userBlock"].get("ALL", {}).get("low2Delta", {})
    if low2.get("status") != "COMPLETE" or low2.get("ciHigh") is None:
        status = "INSUFFICIENT"
        insufficient = True
    else:
        status = "PASS" if float(low2["ciHigh"]) <= .02 else "FAIL"
        failed |= status == "FAIL"
    items["top2Low"] = {**low2, "status": status, "limit": .02,
                        "comparison": "CI_HIGH_LE_LIMIT"}
    required = "FAIL" if failed else ("INSUFFICIENT" if insufficient else "PASS")
    return {"requiredGate": required, "items": items,
            "holmSuperiorityRequiredForSafety": False, "serviceActivationAuthorized": False}


def evaluate(frame: pd.DataFrame, affine: Mapping[str, Mapping[str, Any]],
             spec: AuditSpec) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    need(not frame.empty and not frame.duplicated(["uid", "movie_id"]).any(),
         "evaluation phase truth invalid")
    working = frame.copy()
    working["b0_cal"] = calibrated(working.b0_raw.to_numpy(), affine["B0"])
    working["b1_cal"] = calibrated(working.b1_raw.to_numpy(), affine["B1"])
    metrics: dict[str, Any] = {"models": {}, "pointDeltas": {}}
    user_tables: dict[str, dict[str, pd.DataFrame]] = {model: {} for model in MODELS}
    census: dict[str, Any] = {"phaseUsers": int(working.uid.nunique()),
                              "phaseRows": len(working), "strata": {}}
    user_boot: dict[str, Any] = {}
    movie_boot: dict[str, Any] = {}
    for name, subset in strata(working).items():
        support = {"users": int(subset.uid.nunique()), "movies": int(subset.movie_id.nunique()),
                   "rows": len(subset),
                   "minimumRequired": {"users": spec.minimum_users,
                                       "movies": spec.minimum_movies, "rows": spec.minimum_rows}}
        support["status"] = ("COMPLETE" if support["users"] >= spec.minimum_users
                             and support["movies"] >= spec.minimum_movies
                             and support["rows"] >= spec.minimum_rows else "INSUFFICIENT")
        census["strata"][name] = support
        for model, column in (("B0", "b0_cal"), ("B1", "b1_cal")):
            model_summary, per_user = summarize(subset, column)
            metrics["models"].setdefault(model, {})[name] = model_summary
            user_tables[model][name] = per_user
        before, after = user_tables["B0"][name], user_tables["B1"][name]
        b0_mse, b1_mse = common_values(before, after, "mse")
        b0_mae, b1_mae = common_values(before, after, "mae")
        user_boot[name] = {
            "mseDelta": paired_bootstrap(b0_mse, b1_mse, spec.bootstrap_samples, spec.minimum_users),
            "relativeMse": relative_bootstrap(b0_mse, b1_mse, spec.bootstrap_samples,
                                              spec.minimum_users),
            "maeDelta": paired_bootstrap(b0_mae, b1_mae, spec.bootstrap_samples, spec.minimum_users),
        }
        if name == "ALL":
            for k in TOP_K:
                for metric in ("ndcg", "actual", "low"):
                    b0_value, b1_value = common_values(before, after, f"{metric}{k}")
                    user_boot[name][f"{metric}{k}Delta"] = paired_bootstrap(
                        b0_value, b1_value, spec.bootstrap_samples, spec.minimum_users)
            for start in range(0, 10, 2):
                stop = start + 2
                for metric in ("actual", "low"):
                    column = f"round{start + 1}_{stop}_{metric}"
                    b0_value, b1_value = common_values(before, after, column)
                    user_boot[name][f"{column}Delta"] = paired_bootstrap(
                        b0_value, b1_value, spec.bootstrap_samples, spec.minimum_users)
        movie_boot[name] = movie_bootstrap(subset, spec.bootstrap_samples, spec.minimum_users)

        def delta(metric: str) -> float | None:
            left = metrics["models"]["B0"][name][metric]
            right = metrics["models"]["B1"][name][metric]
            return float(right - left) if left is not None and right is not None else None

        metrics["pointDeltas"][name] = {
            "userMacroMSE": delta("userMacroMSE"), "userMacroMAE": delta("userMacroMAE"),
            "microMSE": delta("microMSE"), "movieMacroMSE": delta("movieMacroMSE"),
        }
    bootstrap = {
        "userBlock": user_boot, "movieBlockSensitivity": movie_boot,
        "signFlipHolm": sign_flip(user_tables["B0"]["ALL"].mse.to_numpy(),
                                  user_tables["B1"]["ALL"].mse.to_numpy(),
                                  spec.bootstrap_samples),
        "uncertaintyScope": "CONDITIONAL_ON_FIXED_B0_B1_FITS_CALIBRATORS_AND_REUSED_DEVELOPMENT_USERS",
    }
    gates = decide_gate(census, bootstrap, affine)
    return metrics, bootstrap, census, gates


def no_transient_outputs(roots: Roots, phase: str) -> None:
    bundle = roots.bundle(phase)
    no_transient_bundle(bundle, phase)


def no_transient_bundle(bundle: Path, label: str) -> None:
    need(not bundle.with_name(bundle.name + "-failure.json").exists(),
         f"{label} failure record exists beside success bundle")
    need(not list(bundle.parent.glob("." + bundle.name + ".tmp-*")),
         f"{label} stale staging directory exists")
    need(not list(bundle.parent.glob("." + bundle.name + ".*-scratch-*")),
         f"{label} stale scratch directory exists")


def dependency_fingerprint(roots: Roots, phase: str) -> dict[str, Any]:
    bundle = roots.bundle(phase)
    no_transient_outputs(roots, phase)
    manifest = json_object(bundle / "manifest.json")
    source_paths, phase_paths = verify_evaluation_lock(
        bundle / "evaluation-input-lock.json", phase, manifest, roots)
    source_files = {roots.logical(path): pin(path) for path in sorted(set(source_paths.values()))}
    phase_files = {roots.logical(path): pin(path) for path in sorted(set(phase_paths.values()))}
    ancestors: dict[str, dict[str, dict[str, Any]]] = {}
    for path in sorted(set(source_paths.values()) | set(phase_paths.values())):
        if path.name != "manifest.json" or path.parent == bundle:
            continue
        try:
            payload = json_object(path)
        except (AuditError, json.JSONDecodeError):
            continue
        if isinstance(payload.get("status"), str):
            no_transient_bundle(path.parent, f"ancestor {roots.logical(path.parent)}")
            ancestors[roots.logical(path.parent)] = inventory(path.parent)
    return {
        "bundleInventory": inventory(bundle),
        "sourceFiles": source_files,
        "phaseInputFiles": phase_files,
        "ancestorBundleInventories": {key: ancestors[key] for key in sorted(ancestors)},
        "evaluatorImplementation": path_record(roots.evaluator(), roots),
        "auditorImplementation": path_record(roots.auditor(), roots),
    }


def target_records(roots: Roots, phase: str) -> dict[str, dict[str, Any]]:
    bundle = roots.bundle(phase)
    paths = {"manifest": bundle / "manifest.json",
             **{key: bundle / value for key, value in TARGETS[phase].items()},
             "evaluator": roots.evaluator()}
    return {key: path_record(path, roots) for key, path in paths.items()}


def parse_timestamp(value: Any, label: str) -> dt.datetime:
    need(isinstance(value, str) and value.endswith("Z"), f"{label} timestamp format")
    try:
        parsed = dt.datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise AuditError(f"{label} timestamp format") from error
    need(parsed.tzinfo is not None, f"{label} timestamp timezone")
    return parsed


def verify_manifest(bundle: Path, phase: str, expected_manifest_sha256: str,
                    expected_evaluator_sha256: str, roots: Roots) -> dict[str, Any]:
    need(valid_hash(expected_manifest_sha256), "explicit lowercase evaluation manifest SHA256 required")
    need(valid_hash(expected_evaluator_sha256), "explicit lowercase evaluator SHA256 required")
    manifest_path = bundle / "manifest.json"
    need(pin(manifest_path)["sha256"] == expected_manifest_sha256,
         "evaluation manifest explicit SHA256 mismatch")
    manifest = json_object(manifest_path)
    gate_key = "requiredSelectionGate" if phase == "selection" else "requiredConfirmationGate"
    expected_keys = {
        "schemaVersion", "phase", "status", gate_key, "confirmationAuthorized",
        "readyForService", "serviceActivationAuthorized", "freshBlindHoldout", "evaluator",
        "evaluationSourceSetSha256", "truthCensus", "startedAt", "completedAt", "seconds",
        "runtime", "files",
    }
    need(set(manifest) == expected_keys, "evaluation manifest field set drift")
    expected_phase = phase.upper()
    expected_status = ("B1_SELECTION_COMPLETE_AUDIT_PENDING" if phase == "selection"
                       else "B1_CONFIRMATION_COMPLETE_AUDIT_PENDING")
    need(manifest.get("schemaVersion") == "feelm-service-v1-b1-evaluation-result/1"
         and manifest.get("phase") == expected_phase and manifest.get("status") == expected_status,
         "evaluation manifest schema/phase/status drift")
    need(manifest.get(gate_key) in {"PASS", "FAIL", "INSUFFICIENT"},
         "evaluation manifest gate value drift")
    need(manifest.get("confirmationAuthorized") is False
         and manifest.get("readyForService") is False
         and manifest.get("serviceActivationAuthorized") is False
         and manifest.get("freshBlindHoldout") is False,
         "evaluation manifest premature authorization/disclosure drift")
    evaluator_path = resolve_record(manifest["evaluator"], roots, manifest_path,
                                    "evaluation manifest evaluator")
    need(evaluator_path == roots.evaluator().resolve()
         and manifest["evaluator"]["sha256"] == expected_evaluator_sha256,
         "evaluation manifest evaluator identity drift")
    need(valid_hash(manifest.get("evaluationSourceSetSha256")),
         "evaluation manifest source-set SHA256 invalid")
    start = parse_timestamp(manifest.get("startedAt"), "manifest startedAt")
    stop = parse_timestamp(manifest.get("completedAt"), "manifest completedAt")
    seconds = manifest.get("seconds")
    need(type(seconds) in (int, float) and math.isfinite(float(seconds)) and float(seconds) >= 0,
         "evaluation manifest seconds invalid")
    need(stop >= start and abs((stop - start).total_seconds() - float(seconds)) <= 5.0,
         "evaluation manifest wall/monotonic duration drift")
    runtime = manifest.get("runtime")
    need(isinstance(runtime, Mapping) and set(runtime) == {"python", "numpy", "pandas", "pyarrow"}
         and all(isinstance(value, str) and value for value in runtime.values()),
         "evaluation manifest runtime schema drift")
    verify_bundle_inventory(bundle, manifest,
                            SELECTION_FILES if phase == "selection" else CONFIRMATION_FILES)
    return manifest


def verify_command(bundle: Path, phase: str, score_reference: Mapping[str, Any],
                   spec: AuditSpec, roots: Roots,
                   selection_reference: Mapping[str, Any] | None = None) -> None:
    command = json_object(bundle / "command.json")
    expected: dict[str, Any] = {
        "phase": "calibrate-select" if phase == "selection" else "confirm",
        "expectedPlanSha256": PLAN_SHA256 if spec.canonical else pin(roots.plan())["sha256"],
        "expectedScoreManifestSha256": score_reference["scoreManifest"]["sha256"],
        "expectedScoreReviewSha256": score_reference["scoreReview"]["sha256"],
        "bootstrapSamples": spec.bootstrap_samples,
    }
    if phase == "selection":
        expected.update({
            "scoreManifest": score_reference["scoreManifest"],
            "scoreReview": score_reference["scoreReview"],
        })
    else:
        need(selection_reference is not None, "confirmation selection reference unavailable")
        expected.update({
            "selectionManifest": selection_reference["selectionManifest"],
            "selectionReview": selection_reference["selectionReview"],
            "expectedSelectionManifestSha256": selection_reference["selectionManifest"]["sha256"],
            "expectedSelectionReviewSha256": selection_reference["selectionReview"]["sha256"],
        })
    json_equal(command, expected, "evaluation command")


def verify_existing_selection_review(review_path: Path, candidate: Mapping[str, Any],
                                     roots: Roots) -> dict[str, Any]:
    review = json_object(review_path)
    need(review.get("schemaVersion") == "feelm-service-v1-b1-evaluation-result-review/1"
         and review.get("runId") == RUN_ID and review.get("phase") == "selection"
         and review.get("status") == "PASS", "selection independent review state drift")
    parse_timestamp(review.get("createdAt"), "selection review createdAt")
    comparable = dict(candidate)
    comparable["createdAt"] = review.get("createdAt")
    json_equal(review, comparable, "selection independent review")
    need(review.get("target") == target_records(roots, "selection"),
         "selection review target drift")
    return review


def verify_selection_reference(path: Path, roots: Roots, phase_inputs: Mapping[str, Path],
                               expected_evaluator_sha256: str, spec: AuditSpec,
                               source_pins: Mapping[str, tuple[int, str]]) -> tuple[dict[str, Any],
                                                                                   dict[str, Any],
                                                                                   dict[str, Any]]:
    reference = json_object(path)
    named = {
        "selectionManifest": "manifest.json",
        "evaluationInputLock": "evaluation-input-lock.json",
        "truthIntegrity": "truth-integrity.json", "scoreReference": "score-reference.json",
        "command": "command.json", "roleMembership": "role-membership.csv",
        "affine": "affine.json", "metrics": "metrics.json",
        "bootstrapSummary": "bootstrap-summary.json", "strataCensus": "strata-census.json",
        "gates": "gates.json", "runLog": "run.log",
    }
    expected_keys = {"schemaVersion", "selectionReview", "evaluator", "requiredSelectionGate",
                     "selectionReviewStatus", *named}
    need(set(reference) == expected_keys
         and reference.get("schemaVersion") == "feelm-service-v1-b1-selection-reference/1",
         "selection reference schema/field set drift")
    need(reference.get("requiredSelectionGate") == "PASS"
         and reference.get("selectionReviewStatus") == "PASS",
         "selection reference authorization drift")
    selection_bundle = roots.bundle("selection")
    expected_paths = {key: selection_bundle / relative for key, relative in named.items()}
    selection_review_path = roots.review("selection")
    expected_paths["selectionReview"] = selection_review_path
    expected_paths["evaluator"] = roots.evaluator()
    resolved: dict[str, Path] = {}
    for key, expected in expected_paths.items():
        resolved[key] = resolve_record(reference[key], roots, path, f"selection reference {key}")
        need(resolved[key] == expected.resolve(), f"selection reference {key} path drift")
    selection_manifest_sha = reference["selectionManifest"]["sha256"]
    candidate = audit_phase(roots, "selection", selection_manifest_sha,
                            expected_evaluator_sha256, _spec=spec,
                            _source_pins=source_pins, _ancestor=True)
    review = verify_existing_selection_review(selection_review_path, candidate, roots)
    selection_manifest = json_object(selection_bundle / "manifest.json")
    need(selection_manifest.get("requiredSelectionGate") == "PASS",
         "selection gate must PASS before confirmation audit")
    required_phase_paths = {path.resolve() for path in selection_bundle.rglob("*") if path.is_file()}
    required_phase_paths.add(selection_review_path.resolve())
    required_phase_paths.discard(roots.evaluator().resolve())
    need(set(phase_inputs.values()) == required_phase_paths,
         "confirmation phaseInputs do not exactly pin selection bundle and review")
    affine = json_object(selection_bundle / "affine.json")
    return reference, affine, review


def compute_expected_outputs(phase: str, truth: pd.DataFrame, spec: AuditSpec,
                             selection_affine: Mapping[str, Any] | None = None
                             ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any],
                                        dict[str, Any], dict[str, Any] | None]:
    if phase == "selection":
        calibration = truth[truth.role.eq("CALIBRATION")]
        target = truth[truth.role.eq("SELECTION")]
        need(calibration.uid.nunique() == spec.calibration_users
             and target.uid.nunique() == spec.selection_users,
             "selection/calibration user census drift")
        affine: dict[str, Any] = {
            "schemaVersion": "feelm-service-v1-b1-affine/1", "fitRole": "CALIBRATION",
            "userEqualWeight": True, "comparisonLabelsUsedForFitting": False,
            "models": {"B0": fit_affine(calibration, "b0_raw"),
                       "B1": fit_affine(calibration, "b1_raw")},
        }
        if all(affine["models"][model]["state"] == "AFFINE" for model in MODELS):
            metrics, bootstrap, census, gates = evaluate(target, affine["models"], spec)
        else:
            census = {"phaseUsers": int(target.uid.nunique()), "phaseRows": len(target),
                      "strata": {name: {"users": int(part.uid.nunique()),
                                        "movies": int(part.movie_id.nunique()), "rows": len(part),
                                        "status": "INSUFFICIENT"}
                                  for name, part in strata(target).items()}}
            metrics = {"models": {}, "pointDeltas": {}, "state": "CALIBRATION_UNAVAILABLE"}
            bootstrap = {"state": "CALIBRATION_UNAVAILABLE", "userBlock": {},
                         "movieBlockSensitivity": {}, "signFlipHolm": None}
            gates = {"requiredGate": "INSUFFICIENT", "items": {"affine": {
                "status": "INSUFFICIENT", "models": affine["models"]}},
                "serviceActivationAuthorized": False}
        return metrics, bootstrap, census, gates, affine
    need(selection_affine is not None and selection_affine.get("schemaVersion")
         == "feelm-service-v1-b1-affine/1", "selection affine contract unavailable")
    target = truth[truth.role.eq("CONFIRMATION")]
    need(target.uid.nunique() == spec.confirmation_users, "confirmation user census drift")
    metrics, bootstrap, census, gates = evaluate(target, selection_affine["models"], spec)
    return metrics, bootstrap, census, gates, None


def verify_metric_files(bundle: Path, phase: str, metrics: Mapping[str, Any],
                        bootstrap: Mapping[str, Any], census: Mapping[str, Any],
                        gates: Mapping[str, Any], affine: Mapping[str, Any] | None) -> None:
    phase_name = phase.upper()
    json_equal(json_object(bundle / "metrics.json"), {"phase": phase_name, **metrics}, "metrics")
    json_equal(json_object(bundle / "bootstrap-summary.json"),
               {"phase": phase_name, **bootstrap}, "bootstrap summary")
    json_equal(json_object(bundle / "strata-census.json"),
               {"phase": phase_name, **census}, "strata census")
    json_equal(json_object(bundle / "gates.json"), {"phase": phase_name, **gates}, "gates")
    if phase == "selection":
        need(affine is not None, "selection affine recomputation missing")
        json_equal(json_object(bundle / "affine.json"), affine, "affine")


def review_payload(roots: Roots, phase: str, dependencies: Mapping[str, Any],
                   integrity: Mapping[str, Any], gate: str, spec: AuditSpec) -> dict[str, Any]:
    if phase == "selection":
        decision = {"selectionGate": gate, "confirmationEligible": gate == "PASS",
                    "logicalState": ("B1_SELECTION_AUDITED_CONFIRMATION_ELIGIBLE" if gate == "PASS"
                                     else "B1_SELECTION_AUDITED_CONFIRMATION_BLOCKED")}
    else:
        selection_gate = json_object(roots.bundle("selection") / "gates.json")["requiredGate"]
        eligible = selection_gate == "PASS" and gate == "PASS"
        decision = {"selectionGate": selection_gate, "confirmationGate": gate,
                    "noninferiorityConclusionEligible": eligible,
                    "logicalState": ("B1_EVALUATED_NONINFERIOR" if eligible
                                     else "NO_NONINFERIORITY_CONCLUSION")}
    return {
        "schemaVersion": "feelm-service-v1-b1-evaluation-result-review/1",
        "runId": RUN_ID, "phase": phase, "status": "PASS",
        "createdAt": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "target": target_records(roots, phase),
        "reviewer": {"implementation": path_record(roots.auditor(), roots),
                     "independentFromEvaluator": True},
        "dependencyFingerprint": dict(dependencies),
        "checks": {
            "exactOutputInventory": True, "allInputPinsRehashed": True,
            "r2AncestorChainVerified": True, "truthRebuilt": True,
            "affineIndependentlyRecomputed": phase == "selection",
            "metricsIndependentlyRecomputed": True, "bootstrapSamples": spec.bootstrap_samples,
            "gateIndependentlyRecomputed": True,
            "truthRows": int(integrity["joinedRows"]),
        },
        "decision": decision, "evaluationInputsRead": True,
        "modelFitPerformed": False, "readyForService": False,
        "scope": "Local immutable evaluation integrity and independent metric/gate parity; no service activation.",
    }


def audit_phase(roots: Roots, phase: str, expected_manifest_sha256: str,
                expected_evaluator_sha256: str, *, _spec: AuditSpec | None = None,
                _source_pins: Mapping[str, tuple[int, str]] | None = None,
                _ancestor: bool = False) -> dict[str, Any]:
    """Audit one immutable bundle without writing; private overrides are test-only."""
    need(phase in TARGETS, "unknown evaluation audit phase")
    spec = _spec or AuditSpec()
    source_pins = _source_pins or SOURCE_PINS
    if spec.canonical:
        need(expected_evaluator_sha256 == REVIEWED_EVALUATOR_SHA256,
             "evaluator SHA256 is not the independently reviewed implementation")
    bundle = roots.bundle(phase)
    need(bundle.is_dir() and not bundle.is_symlink(), "canonical evaluation bundle missing")
    no_transient_outputs(roots, phase)
    manifest = verify_manifest(bundle, phase, expected_manifest_sha256,
                               expected_evaluator_sha256, roots)
    dependencies = dependency_fingerprint(roots, phase)
    sources, phase_inputs = verify_evaluation_lock(
        bundle / "evaluation-input-lock.json", phase, manifest, roots)
    data = verify_fixed_sources(roots, sources, expected_evaluator_sha256, source_pins,
                                canonical=spec.canonical)
    score_info = verify_score_reference(bundle / "score-reference.json", roots, sources, spec)
    als_ids, als_paths = verify_als_axis(roots, data["artifact_manifest"], sources, spec)
    expected_sources = ({path.resolve() for path in data.values()}
                        | set(score_info["chainPaths"].values()) | als_paths)
    need(set(sources.values()) == expected_sources,
         "evaluation source lock has omitted or unapproved files")
    roles, role_digest = split_roles(data["roles"], spec)
    truth, integrity = build_truth(roots, data, score_info["predictions"], roles,
                                   role_digest, als_ids, spec)
    json_equal(json_object(bundle / "truth-integrity.json"), integrity, "truth integrity")
    json_equal(manifest.get("truthCensus"), integrity, "manifest truth census")
    selection_reference: dict[str, Any] | None = None
    selection_affine: dict[str, Any] | None = None
    if phase == "selection":
        need((bundle / "role-membership.csv").read_bytes() == expected_role_membership(roles),
             "selection role-membership.csv drift")
    else:
        selection_reference, selection_affine, _ = verify_selection_reference(
            bundle / "selection-reference.json", roots, phase_inputs,
            expected_evaluator_sha256, spec, source_pins)
        json_equal(json_object(bundle / "score-reference.json"),
                   json_object(roots.bundle("selection") / "score-reference.json"),
                   "selection/confirmation score reference")
        selection_lock = json_object(roots.bundle("selection") / "evaluation-input-lock.json")
        current_lock = json_object(bundle / "evaluation-input-lock.json")
        json_equal(current_lock["files"], selection_lock["files"],
                   "selection/confirmation evaluation sources")
        need(current_lock["evaluationSourceSetSha256"]
             == selection_lock["evaluationSourceSetSha256"],
             "selection/confirmation evaluation source digest drift")
        json_equal(json_object(roots.bundle("selection") / "truth-integrity.json"), integrity,
                   "selection/confirmation truth integrity")
    metrics, bootstrap, census, gates, affine = compute_expected_outputs(
        phase, truth, spec, selection_affine)
    verify_metric_files(bundle, phase, metrics, bootstrap, census, gates, affine)
    verify_command(bundle, phase, score_info["reference"], spec, roots, selection_reference)
    gate_key = "requiredSelectionGate" if phase == "selection" else "requiredConfirmationGate"
    need(manifest.get(gate_key) == gates["requiredGate"], "manifest/recomputed gate drift")
    expected_log = (f"{manifest['startedAt']} phase=calibrate-select score_gate=PASS "
                    f"truth_rows={len(truth)} selection_gate={gates['requiredGate']}\n"
                    if phase == "selection" else
                    f"{manifest['startedAt']} phase=confirm selection_gate=PASS selection_review=PASS "
                    f"truth_rows={len(truth)} confirmation_gate={gates['requiredGate']}\n")
    need((bundle / "run.log").read_text(encoding="utf-8") == expected_log,
         "evaluation run.log drift")
    need(dependency_fingerprint(roots, phase) == dependencies,
         "evaluation dependency changed during audit")
    review = review_payload(roots, phase, dependencies, integrity, gates["requiredGate"], spec)
    # `_ancestor` is explicit to make recursive confirmation review obvious to
    # callers and tests; it never weakens a check or changes the payload.
    need(type(_ancestor) is bool, "invalid internal ancestor marker")
    return review


def rename_no_replace(source: Path, target: Path) -> None:
    """Atomically move without clobbering or fail if the platform cannot do so."""
    if os.name == "nt":
        os.rename(source, target)
        return
    import ctypes
    library = ctypes.CDLL(None, use_errno=True)
    function = getattr(library, "renameat2", None)
    need(function is not None, "atomic no-replace rename unavailable")
    result = function(-100, os.fsencode(source), -100, os.fsencode(target), 1)
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(target))


def publish_review(roots: Roots, phase: str, review: Mapping[str, Any], *,
                   _spec: AuditSpec | None = None,
                   _source_pins: Mapping[str, tuple[int, str]] | None = None) -> Path:
    """Re-audit and atomically publish only the exact resulting attestation.

    Private overrides only keep synthetic fixtures small; the CLI never exposes
    them.  Re-running the audit here prevents a caller from forging any review
    decision, check, reviewer, or safety field between audit and publication.
    """
    need(review.get("schemaVersion") == "feelm-service-v1-b1-evaluation-result-review/1"
         and review.get("runId") == RUN_ID and review.get("phase") == phase
         and review.get("status") == "PASS", "only a successful r2 audit can publish")
    supplied_target = review.get("target")
    need(isinstance(supplied_target, Mapping), "evaluation review target missing")
    manifest_record = loose_pin(supplied_target.get("manifest"))
    evaluator_record = loose_pin(supplied_target.get("evaluator"))
    candidate = audit_phase(
        roots, phase, manifest_record["sha256"], evaluator_record["sha256"],
        _spec=_spec, _source_pins=_source_pins,
    )
    parse_timestamp(review.get("createdAt"), "evaluation review createdAt")
    candidate["createdAt"] = review["createdAt"]
    json_equal(review, candidate, "review submitted for publication")
    try:
        need(candidate.get("target") == target_records(roots, phase),
             "evaluation target changed before review publication")
        need(candidate.get("dependencyFingerprint") == dependency_fingerprint(roots, phase),
             "evaluation dependency changed before review publication")
    except (AuditError, OSError) as error:
        raise AuditError(f"evaluation target or dependency changed before review publication: {error}") from error
    destination = roots.review(phase)
    need(not destination.exists(), "immutable evaluation result review already exists")
    temporary = destination.with_name("." + destination.name + ".tmp-" + uuid.uuid4().hex)
    try:
        encoded = (json.dumps(candidate, ensure_ascii=False, allow_nan=False,
                              sort_keys=True, indent=2) + "\n").encode("utf-8")
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        json_equal(json_object(temporary), candidate, "serialized evaluation review")
        try:
            need(candidate.get("target") == target_records(roots, phase),
                 "evaluation target changed while serializing review")
            need(candidate.get("dependencyFingerprint") == dependency_fingerprint(roots, phase),
                 "evaluation dependency changed immediately before review publication")
        except (AuditError, OSError) as error:
            raise AuditError(
                f"evaluation target or dependency changed immediately before review publication: {error}"
            ) from error
        rename_no_replace(temporary, destination)
        if os.name != "nt":
            descriptor = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        if temporary.exists():
            temporary.unlink()
    return destination


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--standalone-root", type=Path, required=True)
    result.add_argument("--team-repo", type=Path, required=True)
    subcommands = result.add_subparsers(dest="phase", required=True)
    for phase in TARGETS:
        command = subcommands.add_parser(phase, help=f"independently audit immutable {phase} output")
        command.add_argument("--expected-manifest-sha256", required=True)
        command.add_argument("--expected-evaluator-sha256", required=True)
        command.add_argument("--publish-review", action="store_true",
                             help="atomically create the canonical sibling review after PASS")
    return result


def main(argv: Sequence[str] | None = None) -> None:
    args = parser().parse_args(argv)
    roots = Roots(args.standalone_root.resolve(), args.team_repo.resolve())
    try:
        review = audit_phase(roots, args.phase, args.expected_manifest_sha256,
                             args.expected_evaluator_sha256)
        published = publish_review(roots, args.phase, review) if args.publish_review else None
        print(json.dumps({"status": "PASS", "phase": args.phase,
                          "gate": review["decision"], "reviewPublished": published is not None,
                          "review": str(published) if published else None},
                         ensure_ascii=False, sort_keys=True), flush=True)
    except Exception as error:
        print(json.dumps({"status": "BLOCK", "phase": args.phase, "error": str(error)},
                         ensure_ascii=False, sort_keys=True), flush=True)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
