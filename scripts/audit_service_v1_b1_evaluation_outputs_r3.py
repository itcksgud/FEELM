"""Independent audit for Service-v1 B1 r3 selection and confirmation bundles.

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
import stat
import uuid
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as pads
import pyarrow.parquet as pq


SCRIPT = Path(__file__).resolve()
ROOT = SCRIPT.parents[1]
TEAM_ROOT = ROOT.parent / "S15P21E106"
RUN_ID = "b1-gbt120-s339-v1-r3-local4c12g-t14400"
PROFILE_ID = "local4c12g-t14400-v1"
R2_RUN_ID = "b1-gbt120-s339-v1-r2"
PLAN_SHA256 = "4de13f041532b7249b22f5a2bbfbd7919f79d94e4c25f2ae16fe95104a2b3d9a"
# Filled only after the r3 evaluator and Spark auditor are frozen.  Public CLI
# audit/review publication remains fail-closed until then.
REVIEWED_R3_EVALUATOR_SHA256: str | None = (
    "de036adc7b35d151aeaf8006e6a2a64f86130f0d9f49b80ac8fa15ec336fc87a"
)
REVIEWED_R3_RUNNER_SHA256: str | None = (
    "783a5a4c7f1783416271de0a1cde6a6be55409ef8b625a757f8a07c153c7a574"
)
REVIEWED_R3_PROFILE_SHA256: str | None = (
    "761c210364ab465f9143ded5dba8c2f3fba9a1d34c10f1ea54d742a528494c23"
)
REVIEWED_R3_SCORE_AUDITOR_SHA256: str | None = (
    "92a9e48597205f54509ccd7fbd9c717cb791bbd31a6998cb9d966177f830d3be"
)
SPARK_IMAGE_ID = "sha256:de611d62db982bb833b0737a7ddd72a7cd49ab1a7dc9128669cdabae39f46df8"
MODEL_INPUT_SET_SHA256 = "cd41a8ac341cdbc1435bbce21bd78e3b3012e2c50ca25cfac0ef034088b1ccfd"
WORKER_RUNTIME_SET_SHA256 = "ff90bcd014b8ac498f2c5ccba27eb33d0a83f3ee062d36dc426e33a95c6a0a0a"
R2_TRAINING_SOURCE_SET_SHA256 = "6d82b745ec4e796f823f6506497ad9a15236f92a91a04d3452c4755fada499f9"
R2_PREFLIGHT_MANIFEST_SHA256 = "7a47a40ac923ba1f8a638ad6029aa72349245b65055f3db8915a2327de174e01"
R2_PREFLIGHT_REVIEW_SHA256 = "cc49688618bfd408a957a22522456912320519d981c34ad87aefb190a0910132"
R2_FIT_FAILURE_SHA256 = "4ad571439af7f290dd32ce27922519f31e3b156f90998b26a69e2c892aba7e37"
R2_FIT_FAILURE_BYTES = 24_854
R2_OUTER_RUNNER_SHA256 = "cbc8983a0047b5aa0a2fc5a2d3794080afcf48583fe1be90c6d2dd3dd5af6f8e"
R2_PLAN_SHA256 = "c5b90b0d19374588fc5cb7a1d98cd6510a1c3708df9667131df5eb3826c79d49"
SOURCE_ROWS = 4_997_069
LOGICAL_ROWS = 4 * SOURCE_ROWS
MODEL_INPUT_NAMES = frozenset({
    "contract/training-recipe.v1.json", "contract/service-v1.json",
    "contract/MODELS.md", "contract/feature-schema.v1.json",
    "source/natural-train.parquet", "source/tmdb-masked-train.parquet",
    "source/masked-manifest.json", "source/views-manifest.json",
    "source/masked-review.json",
})
WORKER_RUNTIME_NAMES = frozenset({
    "implementation/service_v1_b1_spark_worker.py",
    "implementation/combination340_models.py",
    "implementation/rec046_common.py", "runtime/docker-image-id",
})
EXECUTION_NAMES = frozenset({
    "execution/service-v1-b1-r3-fit-recovery.md",
    "execution/local4c12g-t14400-profile.json",
    "execution/run_service_v1_b1_gbt_r3.py",
    "execution/test_service_v1_b1_gbt_runner_r3.py",
})
SCORE_INPUT_NAMES = frozenset({"source/natural-score.parquet"})
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


def require_public_r3_contract_sealed() -> None:
    pins = {
        "r3 evaluator": REVIEWED_R3_EVALUATOR_SHA256,
        "r3 runner": REVIEWED_R3_RUNNER_SHA256,
        "r3 execution profile": REVIEWED_R3_PROFILE_SHA256,
        "r3 Spark output auditor": REVIEWED_R3_SCORE_AUDITOR_SHA256,
    }
    missing = [name for name, value in pins.items()
               if not valid_hash(value) or value == "0" * 64]
    need(not missing,
         "PUBLIC EVALUATION AUDIT BLOCKED: unsealed r3 contract pins: "
         + ", ".join(missing))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024**2), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_link_or_reparse(path: Path) -> bool:
    try:
        if path.is_symlink() or (
            hasattr(path, "is_junction") and path.is_junction()
        ):
            return True
    except OSError:
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def safe_existing_path(path: Path, *, directory: bool | None = None) -> Path:
    lexical = Path(os.path.abspath(os.fspath(path)))
    need(os.path.lexists(lexical), f"path is missing: {lexical}")
    for member in (lexical, *lexical.parents):
        need(not is_link_or_reparse(member),
             f"linked/reparse path forbidden: {lexical}")
    if directory is True:
        need(lexical.is_dir(), f"not a directory: {lexical}")
    elif directory is False:
        need(lexical.is_file(), f"not a regular file: {lexical}")
    else:
        need(lexical.is_file() or lexical.is_dir(),
             f"not a regular file/directory: {lexical}")
    return lexical


def safe_file(path: Path) -> None:
    safe_existing_path(path, directory=False)


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
    root = safe_existing_path(root, directory=True)
    result: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        need(not is_link_or_reparse(path),
             f"linked bundle member: {path}")
        if path.is_file():
            result[relative_name(path.relative_to(root).as_posix())] = pin(path)
        else:
            need(path.is_dir(), f"special bundle member forbidden: {path}")
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


def record_set_digest(records: Sequence[Mapping[str, Any]]) -> str:
    normalized: dict[str, dict[str, Any]] = {}
    for record in records:
        need(isinstance(record, Mapping), "record set member must be an object")
        logical = record.get("path")
        need(isinstance(logical, str) and logical and "\x00" not in logical
             and "\n" not in logical, "record set path invalid")
        need(logical not in normalized, f"duplicate record set path: {logical}")
        need(type(record.get("bytes")) is int and record["bytes"] >= 0,
             f"record set bytes invalid: {logical}")
        need(valid_hash(record.get("sha256")),
             f"record set sha256 invalid: {logical}")
        normalized[logical] = {
            "bytes": record["bytes"], "sha256": record["sha256"],
        }
    return canonical_digest(normalized)


def record_signature(records: Sequence[Mapping[str, Any]]) -> list[tuple[str, int, str]]:
    return sorted((str(record["path"]), int(record["bytes"]), str(record["sha256"]))
                  for record in records)


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
    score_auditor_override: Path | None = None

    def bundle(self, phase: str) -> Path:
        need(phase in TARGETS, "unknown evaluation audit phase")
        base = self.standalone / "outputs/recommendation-evidence/service-v1-pretraining-20260913"
        return base / (RUN_ID + "-selection" if phase == "selection"
                       else RUN_ID + "-confirmation")

    def review(self, phase: str) -> Path:
        bundle = self.bundle(phase)
        return bundle.with_name(bundle.name + "-result-review.json")

    def spark_bundle(self, phase: str) -> Path:
        need(phase in {"preflight", "fit", "score"}, "unknown Spark phase")
        return (self.standalone
                / "outputs/recommendation-evidence/service-v1-pretraining-20260913"
                / f"{RUN_ID}-{phase}")

    def spark_review(self, phase: str) -> Path:
        bundle = self.spark_bundle(phase)
        return bundle.with_name(bundle.name + "-result-review.json")

    def evaluator(self) -> Path:
        return (self.evaluator_override if self.evaluator_override is not None
                else self.standalone / "scripts/evaluate_service_v1_b1_r3.py")

    def auditor(self) -> Path:
        return (self.auditor_override if self.auditor_override is not None
                else self.standalone / "scripts/audit_service_v1_b1_evaluation_outputs_r3.py")

    def score_auditor(self) -> Path:
        return (self.score_auditor_override
                if self.score_auditor_override is not None
                else self.standalone / "scripts/audit_service_v1_b1_spark_outputs_r3.py")

    def plan(self) -> Path:
        return self.standalone / "docs/recommendation/plans/service-v1-b1-r3-fit-recovery.md"

    def r2_preflight(self) -> Path:
        return (self.standalone
                / "outputs/recommendation-evidence/service-v1-pretraining-20260913"
                / f"{R2_RUN_ID}-preflight")

    def r2_preflight_review(self) -> Path:
        bundle = self.r2_preflight()
        return bundle.with_name(bundle.name + "-result-review.json")

    def r2_fit_failure(self) -> Path:
        return (self.standalone
                / "outputs/recommendation-evidence/service-v1-pretraining-20260913"
                / f"{R2_RUN_ID}-fit-failure.json")

    def r2_runner(self) -> Path:
        return self.standalone / "scripts/run_service_v1_b1_gbt.py"

    def r2_plan(self) -> Path:
        return self.standalone / "docs/recommendation/plans/service-v1-b1-spark-runner.md"

    def spark_logical(self, path: Path) -> str:
        resolved = path.resolve()
        for prefix, root in (("standalone", self.standalone), ("team", self.team)):
            try:
                return prefix + "/" + resolved.relative_to(root.resolve()).as_posix()
            except ValueError:
                continue
        raise AuditError(f"Spark dependency outside approved roots: {path}")

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
            "execution/service-v1-b1-r3-fit-recovery.md": self.plan(),
            "execution/local4c12g-t14400-profile.json":
                self.standalone / "docs/recommendation/plans/service-v1-b1-r3-local4c12g-t14400-profile.json",
            "execution/test_service_v1_b1_gbt_runner_r3.py":
                self.standalone / "tests/test_service_v1_b1_gbt_runner_r3.py",
        }
        for name in (
            "service_v1_b1_spark_worker.py", "combination340_models.py", "rec046_common.py",
        ):
            result["implementation/" + name] = self.standalone / "scripts" / name
        result["execution/run_service_v1_b1_gbt_r3.py"] = (
            self.standalone / "scripts/run_service_v1_b1_gbt_r3.py")
        return result

    def logical(self, path: Path) -> str:
        resolved = path.resolve()
        if resolved == self.r2_plan().resolve():
            return "ancestor/service-v1-b1-spark-runner.md"
        if resolved.is_relative_to(self.team.resolve()):
            return "team/" + os.path.relpath(resolved, self.team.resolve()).replace("\\", "/")
        if resolved.is_relative_to(self.standalone.resolve()):
            return "standalone/" + resolved.relative_to(self.standalone.resolve()).as_posix()
        if self.evaluator_override is not None and resolved == self.evaluator_override.resolve():
            return os.path.relpath(resolved, self.standalone.resolve()).replace("\\", "/")
        if self.auditor_override is not None and resolved == self.auditor_override.resolve():
            return os.path.relpath(resolved, self.standalone.resolve()).replace("\\", "/")
        if (self.score_auditor_override is not None
                and resolved == self.score_auditor_override.resolve()):
            return os.path.relpath(resolved, self.standalone.resolve()).replace("\\", "/")
        raise AuditError(f"path outside approved roots: {path}")


def resolve_record(record: Mapping[str, Any], roots: Roots, owner: Path, label: str) -> Path:
    valid_pin(record, path_required=True)
    raw = str(record["path"])
    normalized = raw.replace("\\", "/")
    parts = normalized.split("/")
    need(not Path(raw).is_absolute(), f"{label} absolute path forbidden")
    traversal = any(part == ".." for part in parts)
    need(all(part not in {"", "."} for part in parts),
         f"{label} unsafe path")
    candidates: list[Path]
    if normalized == "ancestor/service-v1-b1-spark-runner.md":
        candidates = [roots.r2_plan()]
    elif normalized.startswith("standalone/"):
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
        if os.path.lexists(candidate):
            resolved = safe_existing_path(candidate, directory=False).resolve()
            if resolved not in existing:
                existing.append(resolved)
    need(len(existing) == 1, f"{label} path missing or ambiguous: {raw}")
    result = existing[0]
    if traversal:
        need(roots.evaluator_override is not None
             and result == roots.evaluator_override.resolve(),
             f"{label} traversal forbidden")
    need(result.is_relative_to(roots.standalone.resolve())
         or result.is_relative_to(roots.team.resolve())
         or (roots.evaluator_override is not None
             and result == roots.evaluator_override.resolve())
         or (roots.auditor_override is not None
             and result == roots.auditor_override.resolve())
         or (roots.score_auditor_override is not None
             and result == roots.score_auditor_override.resolve()),
         f"{label} escapes approved roots")
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
        need(roots.logical(resolved) == name,
             f"{label} noncanonical logical path: {name}")
        need(resolved not in resolved_seen, f"{label} duplicate resolved path: {resolved}")
        resolved_seen.add(resolved)
        paths[name] = resolved
        normalized[name] = {"bytes": record["bytes"], "sha256": record["sha256"]}
    return paths, normalized


def verify_evaluation_lock(lock_path: Path, phase: str, manifest: Mapping[str, Any],
                           roots: Roots) -> tuple[dict[str, Path], dict[str, Path]]:
    lock = json_object(lock_path)
    expected_keys = {
        "schemaVersion", "runId", "profileId", "phase", "files", "phaseInputs",
        "evaluationSourceSetSha256", "inputSetSha256", "labelsPreviouslyOpened",
        "freshBlindHoldout", "fitOrScoreMutationAuthorized",
    }
    need(set(lock) == expected_keys, "evaluation input lock field set drift")
    need(lock.get("schemaVersion") == "feelm-service-v1-b1-r3-evaluation-input-lock/1"
         and lock.get("runId") == RUN_ID and lock.get("profileId") == PROFILE_ID,
         "evaluation input lock identity/schema drift")
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
    need(not Path(value).is_absolute()
         and all(part not in {"", ".", ".."} for part in normalized.split("/")),
         "unsafe logical dependency path")
    if normalized == "ancestor/service-v1-b1-spark-runner.md":
        candidate = roots.r2_plan()
    elif normalized.startswith("team/"):
        candidate = roots.team / normalized.removeprefix("team/")
    elif normalized.startswith("standalone/"):
        candidate = roots.standalone / normalized.removeprefix("standalone/")
    else:
        root_candidate = roots.standalone / value
        owner_candidate = owner.parent / value
        candidates = [safe_existing_path(p, directory=None).resolve()
                      for p in (root_candidate, owner_candidate) if os.path.lexists(p)]
        candidates = list(dict.fromkeys(candidates))
        need(len(candidates) == 1, f"logical dependency path missing or ambiguous: {value}")
        candidate = candidates[0]
    result = safe_existing_path(candidate, directory=None).resolve()
    need(result.is_relative_to(roots.standalone.resolve())
         or result.is_relative_to(roots.team.resolve()), "logical dependency escapes approved roots")
    return result


def chain_contains(chain_paths: Mapping[str, Path], path: Path, record: Mapping[str, Any], label: str) -> None:
    matches = [name for name, current in chain_paths.items() if current == path.resolve()]
    need(len(matches) == 1, f"verified chain missing or duplicates {label}")
    # Spark reviews use canonical ``standalone/...`` / ``team/...`` names while
    # the evaluator lock uses its own canonical root-relative namespace.  Bind
    # them by unique physical identity and the independently rehashed bytes/hash,
    # never by spelling equality between those two already-validated namespaces.
    check_pin(path, loose_pin(record), label)


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
                       chain_paths: Mapping[str, Path], *, phase: str,
                       canonical: bool) -> dict[str, Any]:
    need(phase in {"preflight", "fit", "score"}, "unknown r3 review phase")
    fingerprint = review.get("dependencyFingerprint")
    need(isinstance(fingerprint, Mapping)
         and set(fingerprint) == {
             "files", "bundleInventories", "auditorImplementation", "dockerImageId",
             "runId", "profileId",
         }, "score review dependency fingerprint shape drift")
    files = fingerprint.get("files")
    bundles = fingerprint.get("bundleInventories")
    need(isinstance(files, Mapping) and isinstance(bundles, Mapping),
         "score review dependency fingerprint shape drift")
    source_paths = roots.score_auditor_sources()
    if phase != "score":
        source_paths.pop("source/natural-score.parquet")
    output_parent = (roots.standalone /
                     "outputs/recommendation-evidence/service-v1-pretraining-20260913")
    phase_order = ["preflight"] if phase == "preflight" else (
        ["preflight", "fit"] if phase == "fit" else ["preflight", "fit", "score"])
    bundles_by_phase: dict[str, Path] = {
        current: output_parent / f"{RUN_ID}-{current}"
        for current in phase_order
    }
    expected_file_paths = [
        *source_paths.values(), roots.r2_preflight_review(), roots.r2_fit_failure(),
        roots.r2_runner(), roots.r2_plan(),
        *[bundles_by_phase[current].with_name(
            bundles_by_phase[current].name + "-result-review.json")
          for current in phase_order[:-1]],
    ]
    expected_files = {
        ("ancestor/service-v1-b1-spark-runner.md"
         if path.resolve() == roots.r2_plan().resolve()
         else roots.spark_logical(path)): pin(path)
        for path in expected_file_paths
    }
    expected_bundles = {
        roots.spark_logical(path): inventory(path)
        for path in [roots.r2_preflight(), *bundles_by_phase.values()]
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
    score_auditor = roots.score_auditor()
    check_pin(score_auditor, implementation, "score auditor implementation")
    if canonical:
        need(implementation["sha256"] == REVIEWED_R3_SCORE_AUDITOR_SHA256,
             "score auditor is not the reviewed r3 implementation")
    need(fingerprint.get("dockerImageId") == SPARK_IMAGE_ID,
         "score review Docker image identity drift")
    need(fingerprint.get("runId") == RUN_ID
         and fingerprint.get("profileId") == PROFILE_ID,
         "score review dependency identity drift")
    expected_chain = {path.resolve() for path in expected_file_paths}
    for bundle in [roots.r2_preflight(), *bundles_by_phase.values()]:
        expected_chain.update(path.resolve() for path in bundle.rglob("*") if path.is_file())
    expected_chain.add(score_auditor.resolve())
    observed_chain = set(chain_paths.values())
    need(expected_chain <= observed_chain,
         f"verified score chain omits {phase} dependency closure")
    if phase == "score":
        score_review = bundles_by_phase["score"].with_name(
            bundles_by_phase["score"].name + "-result-review.json")
        expected_chain.add(score_review.resolve())
        need(observed_chain == expected_chain,
             "verified score chain exact closure drift")
    return dict(fingerprint)


def validate_spark_review_checks(checks: Mapping[str, Any], phase: str, *,
                                 canonical: bool, source_rows: int,
                                 score_rows: int) -> None:
    recovery = checks.get("recovery")
    expected_failure_path = (
        "standalone/outputs/recommendation-evidence/"
        f"service-v1-pretraining-20260913/{R2_RUN_ID}-fit-failure.json")
    need(isinstance(recovery, Mapping)
         and set(recovery) == {"r2FitFailure", "reference"},
         f"{phase} independent review recovery check field set drift")
    failure = recovery.get("r2FitFailure")
    reference = recovery.get("reference")
    need(isinstance(failure, Mapping)
         and set(failure) == {"path", "bytes", "sha256"}
         and failure.get("path") == expected_failure_path
         and type(failure.get("bytes")) is int and failure["bytes"] > 0
         and valid_hash(failure.get("sha256")),
         f"{phase} independent review r2 failure evidence drift")
    if canonical:
        need((failure["bytes"], failure["sha256"])
             == (R2_FIT_FAILURE_BYTES, R2_FIT_FAILURE_SHA256),
             f"{phase} independent review r2 failure canonical pin drift")
    need(isinstance(reference, Mapping)
         and set(reference) == {"bytes", "sha256"}
         and type(reference.get("bytes")) is int and reference["bytes"] > 0
         and valid_hash(reference.get("sha256")),
         f"{phase} independent review recovery reference evidence drift")
    if phase in {"preflight", "fit"}:
        identity = checks.get("identity")
        need(isinstance(identity, Mapping)
             and set(identity) == {
                 "sourceRows", "logicalRows", "partitions", "allSourceRowsRead",
                 "maskedFormulaRecomputed", "maskedArtifactPinsChecked",
             }
             and identity.get("sourceRows") == source_rows
             and identity.get("logicalRows") == 4 * source_rows
             and identity.get("partitions") == 8
             and identity.get("allSourceRowsRead") is True
             and identity.get("maskedFormulaRecomputed") is False
             and identity.get("maskedArtifactPinsChecked") is True,
             f"{phase} independent review identity evidence drift")
    if phase == "fit":
        fit = checks.get("fit")
        need(isinstance(fit, Mapping)
             and set(fit) == {
                 "treeCount", "thresholdParity",
                 "weightedTrainRmseIndependentlyRecomputed",
                 "trainingQualityNotAnAcceptanceMetric",
             }
             and type(fit.get("treeCount")) is int and fit["treeCount"] > 0
             and fit.get("weightedTrainRmseIndependentlyRecomputed") is False
             and fit.get("trainingQualityNotAnAcceptanceMetric") is True,
             "fit independent review model evidence drift")
        parity = fit.get("thresholdParity")
        need(isinstance(parity, Mapping)
             and set(parity) == {"rows", "nonLeafSplitCount", "maxAbsError", "status"}
             and type(parity.get("rows")) is int and parity["rows"] > 0
             and type(parity.get("nonLeafSplitCount")) is int
             and parity["nonLeafSplitCount"] > 0
             and parity["rows"] == 3 * parity["nonLeafSplitCount"]
             and type(parity.get("maxAbsError")) in (int, float)
             and math.isfinite(float(parity["maxAbsError"]))
             and 0 <= float(parity["maxAbsError"]) <= 1e-6
             and parity.get("status") == "PASS",
             "fit independent review threshold parity evidence drift")
        if canonical:
            need(fit["treeCount"] == 120,
                 "fit independent review canonical tree count drift")
    if phase == "score":
        predictions = checks.get("predictions")
        need(isinstance(predictions, Mapping)
             and set(predictions) == {
                 "rows", "uniqueRowIds", "strictlyIncreasing", "uidParity",
                 "finite", "maxAbsError", "labelRead", "singlePhysicalFile",
             }
             and predictions.get("rows") == score_rows
             and predictions.get("uniqueRowIds") == score_rows
             and predictions.get("strictlyIncreasing") is True
             and predictions.get("uidParity") is True
             and predictions.get("finite") is True
             and type(predictions.get("maxAbsError")) in (int, float)
             and math.isfinite(float(predictions["maxAbsError"]))
             and 0 <= float(predictions["maxAbsError"]) <= 1e-6
             and predictions.get("labelRead") is False
             and predictions.get("singlePhysicalFile") is True,
             "score independent review prediction evidence drift")


def verify_score_review_envelope(review: Mapping[str, Any], review_path: Path,
                                 roots: Roots, phase: str = "score", *,
                                 canonical: bool = False,
                                 source_rows: int = SOURCE_ROWS,
                                 score_rows: int = 93_230) -> None:
    expected_keys = {
        "schemaVersion", "runId", "profileId", "phase", "status", "createdAt", "target", "reviewer",
        "dependencyFingerprint", "checks", "evaluationTargetsRead",
        "modelFitPerformed", "readyForService", "scope",
    }
    need(set(review) == expected_keys, "score independent review field set drift")
    need(review.get("schemaVersion") == "feelm-service-v1-b1-r3-result-review/1"
         and review.get("runId") == RUN_ID and review.get("profileId") == PROFILE_ID,
         "score independent review identity/schema drift")
    need(review.get("phase") == phase and review.get("status") == "PASS",
         f"{phase} independent review state drift")
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
    score_auditor = roots.score_auditor()
    check_pin(score_auditor, reviewer["implementation"], "score review implementation")
    checks = review.get("checks")
    expected_check_keys = {
        "preflight": {"resource", "recovery", "identity"},
        "fit": {"resource", "recovery", "parentChain", "identity", "fit"},
        "score": {"resource", "recovery", "parentChain", "predictions"},
    }[phase]
    need(isinstance(checks, Mapping) and set(checks) == expected_check_keys,
         f"{phase} independent review check set drift")
    resource = checks.get("resource")
    need(isinstance(resource, Mapping)
         and set(resource) == {
             "stages", "peakBytes", "cleanupConfirmed", "labelMountAbsent",
             "measurementIndependentlyObserved",
             "measurementCheckedAgainstPinnedWorkerLog",
         }
         and resource.get("stages") == (2 if phase == "preflight" else 1)
         and type(resource.get("peakBytes")) is int
         and 0 < resource["peakBytes"] < 12 * 1024**3
         and resource.get("cleanupConfirmed") is True
         and resource.get("labelMountAbsent") is True
         and resource.get("measurementIndependentlyObserved") is False
         and resource.get("measurementCheckedAgainstPinnedWorkerLog") is True,
         f"{phase} independent review resource check drift")
    validate_spark_review_checks(
        checks, phase, canonical=canonical, source_rows=source_rows,
        score_rows=score_rows)
    if phase != "preflight":
        need(checks.get("parentChain") == "PASS",
             f"{phase} independent review parent chain drift")
    need(review.get("evaluationTargetsRead") is False
         and review.get("modelFitPerformed") is False
         and review.get("readyForService") is False
         and review.get("scope") == (
             "Local immutable bundle integrity and specified numerical parity; "
             "no service acceptance or model quality verdict."
         ), "score independent review safety envelope drift")


def resolve_spark_record(record: Mapping[str, Any], roots: Roots, owner: Path,
                         label: str) -> Path:
    """Resolve only an r3 source name, a canonical root path, or the one r2 alias."""
    logical = record.get("path")
    need(isinstance(logical, str), f"{label} path missing")
    if logical == "ancestor/service-v1-b1-spark-runner.md":
        valid_pin(record, path_required=True)
        path = safe_existing_path(roots.r2_plan(), directory=False).resolve()
        check_pin(path, record, label)
        return path
    sources = roots.score_auditor_sources()
    if logical in sources:
        valid_pin(record, path_required=True)
        path = safe_existing_path(sources[logical], directory=False).resolve()
        check_pin(path, record, label)
        return path
    need(not logical.startswith(("contract/", "source/", "implementation/",
                                 "execution/", "ancestor/", "runtime/")),
         f"{label} unknown logical alias")
    return resolve_record(record, roots, owner, label)


def verify_spark_group(records: Any, expected_names: frozenset[str], roots: Roots,
                       owner: Path, chain_paths: Mapping[str, Path],
                       label: str) -> list[dict[str, Any]]:
    need(isinstance(records, list), f"{label} must be a list")
    typed = [dict(record) for record in records if isinstance(record, Mapping)]
    need(len(typed) == len(records), f"{label} contains a non-record")
    record_set_digest(typed)
    need({record["path"] for record in typed} == set(expected_names),
         f"{label} path set drift")
    for record in typed:
        logical = record["path"]
        if logical == "runtime/docker-image-id":
            raw = SPARK_IMAGE_ID.encode("utf-8")
            need(set(record) == {"path", "bytes", "sha256", "value"}
                 and record.get("value") == SPARK_IMAGE_ID
                 and record.get("bytes") == len(raw)
                 and record.get("sha256") == hashlib.sha256(raw).hexdigest(),
                 f"{label} Docker identity drift")
            continue
        need(set(record) == {"path", "bytes", "sha256"},
             f"{label} record field set drift: {logical}")
        path = resolve_spark_record(record, roots, owner, f"{label}.{logical}")
        matches = [current for current in chain_paths.values() if current == path]
        need(len(matches) == 1, f"verified score chain omitted {label}: {logical}")
    return typed


def verify_spark_controls(records: Any, expected_paths: Sequence[Path],
                          roots: Roots, owner: Path,
                          chain_paths: Mapping[str, Path], label: str
                          ) -> list[dict[str, Any]]:
    need(isinstance(records, list), f"{label} must be a list")
    typed = [dict(record) for record in records if isinstance(record, Mapping)]
    need(len(typed) == len(records), f"{label} contains a non-record")
    record_set_digest(typed)
    resolved: list[Path] = []
    for index, record in enumerate(typed):
        need(set(record) == {"path", "bytes", "sha256"},
             f"{label} record field set drift")
        path = resolve_spark_record(record, roots, owner, f"{label}[{index}]")
        resolved.append(path)
        matches = [current for current in chain_paths.values() if current == path]
        need(len(matches) == 1, f"verified score chain omitted {label}[{index}]")
    need(resolved == [path.resolve() for path in expected_paths],
         f"{label} exact ordered closure drift")
    return typed


def phase_bundle_files(bundle: Path) -> list[Path]:
    return sorted((path for path in bundle.rglob("*") if path.is_file()),
                  key=lambda path: path.as_posix())


def verify_spark_lock(lock_path: Path, phase: str, manifest: Mapping[str, Any],
                      roots: Roots, chain_paths: Mapping[str, Path],
                      expected_controls: Sequence[Path], *, canonical: bool
                      ) -> dict[str, list[dict[str, Any]]]:
    lock = json_object(lock_path)
    score = phase == "score"
    expected_fields = {
        "schemaVersion", "phase", "runId", "profileId", "modelInputRecords",
        "workerRuntimeRecords", "executionRecords", "controlReferences",
        "modelInputSetSha256", "workerRuntimeSetSha256", "executionSetSha256",
        "controlReferenceSetSha256", "inputSetSha256",
        "r2TrainingSourceSetSha256", "evaluationTargetsRead",
    }
    if score:
        expected_fields |= {"scoreInputRecords", "scoreInputSetSha256"}
    need(set(lock) == expected_fields, f"{phase} Spark lock field set drift")
    expected_schema = ("feelm-service-v1-b1-score-input-lock/2" if score
                       else "feelm-service-v1-b1-input-lock/2")
    need(lock.get("schemaVersion") == expected_schema
         and lock.get("phase") == phase and lock.get("runId") == RUN_ID
         and lock.get("profileId") == PROFILE_ID
         and lock.get("evaluationTargetsRead") is False,
         f"{phase} Spark lock identity/schema drift")
    models = verify_spark_group(
        lock.get("modelInputRecords"), MODEL_INPUT_NAMES, roots, lock_path,
        chain_paths, f"{phase} model inputs")
    runtime = verify_spark_group(
        lock.get("workerRuntimeRecords"), WORKER_RUNTIME_NAMES, roots, lock_path,
        chain_paths, f"{phase} worker runtime")
    execution = verify_spark_group(
        lock.get("executionRecords"), EXECUTION_NAMES, roots, lock_path,
        chain_paths, f"{phase} execution inputs")
    score_inputs: list[dict[str, Any]] = []
    if score:
        score_inputs = verify_spark_group(
            lock.get("scoreInputRecords"), SCORE_INPUT_NAMES, roots, lock_path,
            chain_paths, "score phase inputs")
    controls = verify_spark_controls(
        lock.get("controlReferences"), expected_controls, roots, lock_path,
        chain_paths, f"{phase} controls")
    groups = {
        "modelInputSetSha256": models,
        "workerRuntimeSetSha256": runtime,
        "executionSetSha256": execution,
        "controlReferenceSetSha256": controls,
    }
    if score:
        groups["scoreInputSetSha256"] = score_inputs
    for digest_name, records in groups.items():
        need(lock.get(digest_name) == record_set_digest(records),
             f"{phase} Spark lock {digest_name} drift")
        need(manifest.get(digest_name) == lock[digest_name],
             f"{phase} manifest/lock {digest_name} drift")
    combined = [*models, *score_inputs, *runtime, *execution, *controls]
    need(len({record["path"] for record in combined}) == len(combined),
         f"{phase} Spark lock groups overlap")
    need(lock.get("inputSetSha256") == record_set_digest(combined)
         and manifest.get("inputSetSha256") == lock["inputSetSha256"],
         f"{phase} Spark lock full input digest drift")
    need(valid_hash(lock.get("r2TrainingSourceSetSha256")),
         f"{phase} r2 historical digest invalid")
    if canonical:
        need(lock.get("modelInputSetSha256") == MODEL_INPUT_SET_SHA256
             and lock.get("workerRuntimeSetSha256") == WORKER_RUNTIME_SET_SHA256
             and lock.get("r2TrainingSourceSetSha256")
             == R2_TRAINING_SOURCE_SET_SHA256,
             f"{phase} canonical source digest drift")
    return {
        "models": models, "scoreInputs": score_inputs, "runtime": runtime,
        "execution": execution, "controls": controls,
    }


def canonical_json_pin(payload: Mapping[str, Any]) -> dict[str, Any]:
    raw = (json.dumps(dict(payload), ensure_ascii=False, indent=2,
                      sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def _one_record(records: Sequence[Mapping[str, Any]], logical: str,
                label: str) -> dict[str, Any]:
    matches = [dict(record) for record in records if record.get("path") == logical]
    need(len(matches) == 1, f"{label} missing or duplicate")
    return matches[0]


def verify_r2_recovery(recovery_path: Path, roots: Roots,
                       groups: Mapping[str, Sequence[Mapping[str, Any]]],
                       chain_paths: Mapping[str, Path], spec: AuditSpec,
                       *, canonical: bool) -> tuple[dict[str, Any], list[Path]]:
    recovery = json_object(recovery_path)
    required = {
        "schemaVersion", "status", "runId", "profileId", "r2RunId",
        "r2PreflightManifest", "r2PreflightReview",
        "r2PreflightBundleInventory", "r2PreflightDigests", "r2FitFailure",
        "r2FitFailureFacts", "r2OuterRunner", "r2Plan", "sparkWorker",
        "outerRunner", "executionProfile", "digestComparison",
        "modelFitPerformedByPreflight", "deploymentAuthorized",
    }
    need(set(recovery) == required, "r3 recovery exact field set drift")
    need(recovery.get("schemaVersion")
         == "feelm-service-v1-b1-r3-recovery-reference/1"
         and recovery.get("status") == "R2_ANCESTRY_VERIFIED"
         and recovery.get("runId") == RUN_ID
         and recovery.get("profileId") == PROFILE_ID
         and recovery.get("r2RunId") == R2_RUN_ID
         and recovery.get("modelFitPerformedByPreflight") is False
         and recovery.get("deploymentAuthorized") is False,
         "r3 recovery identity/authority drift")

    outer = _one_record(groups["execution"],
                        "execution/run_service_v1_b1_gbt_r3.py", "r3 outer")
    profile = _one_record(groups["execution"],
                          "execution/local4c12g-t14400-profile.json",
                          "r3 profile")
    worker = _one_record(groups["runtime"],
                         "implementation/service_v1_b1_spark_worker.py",
                         "Spark worker")
    recipe = _one_record(groups["models"], "contract/training-recipe.v1.json",
                         "training recipe")
    need(recovery.get("outerRunner") == outer
         and recovery.get("executionProfile") == profile
         and recovery.get("sparkWorker") == worker,
         "r3 recovery implementation/profile pin drift")
    if canonical:
        need(outer["sha256"] == REVIEWED_R3_RUNNER_SHA256
             and profile["sha256"] == REVIEWED_R3_PROFILE_SHA256,
             "r3 recovery reviewed implementation/profile drift")
    comparison = recovery.get("digestComparison")
    expected_comparison_keys = {
        "r2HistoricalTrainingSourceSetSha256", "trainingRecipe",
        "sourceRows", "logicalRows", "modelInputSetSha256",
        "workerRuntimeSetSha256", "executionSetSha256",
        "modelInputUnchanged", "workerRuntimeUnchanged",
        "partitionCount", "seed",
    }
    need(isinstance(comparison, Mapping)
         and set(comparison) == expected_comparison_keys
         and comparison.get("trainingRecipe") == recipe
         and comparison.get("sourceRows") == SOURCE_ROWS
         and comparison.get("logicalRows") == LOGICAL_ROWS
         and comparison.get("modelInputSetSha256")
         == record_set_digest(groups["models"])
         and comparison.get("workerRuntimeSetSha256")
         == record_set_digest(groups["runtime"])
         and comparison.get("executionSetSha256")
         == record_set_digest(groups["execution"])
         and comparison.get("modelInputUnchanged") is True
         and comparison.get("workerRuntimeUnchanged") is True
         and comparison.get("partitionCount") == 8
         and comparison.get("seed") == 339
         and valid_hash(comparison.get("r2HistoricalTrainingSourceSetSha256")),
         "r3 recovery digest comparison drift")

    r2_bundle = safe_existing_path(roots.r2_preflight(), directory=True).resolve()
    embedded = recovery.get("r2PreflightBundleInventory")
    need(isinstance(embedded, list)
         and all(isinstance(record, Mapping) for record in embedded),
         "r2 embedded bundle inventory missing")
    embedded_records = [dict(record) for record in embedded]
    record_set_digest(embedded_records)
    r2_inventory = inventory(r2_bundle)
    need(all("/" not in name for name in r2_inventory),
         "r2 preflight nested/linked inventory drift")
    actual_files = sorted((r2_bundle / name for name in r2_inventory),
                          key=lambda path: path.name)
    need(len(actual_files) == len(list(r2_bundle.iterdir()))
         and len(actual_files) == len(embedded_records),
         "r2 preflight exact physical inventory drift")
    resolved_embedded = [
        resolve_spark_record(record, roots, recovery_path,
                             "r2 embedded preflight inventory")
        for record in embedded_records
    ]
    need(resolved_embedded == actual_files,
         "r2 embedded preflight inventory path/order drift")
    for path in actual_files:
        need(sum(current == path.resolve() for current in chain_paths.values()) == 1,
             f"verified score chain omitted r2 preflight file: {path.name}")

    r2_manifest_path = roots.r2_preflight() / "manifest.json"
    r2_manifest_record = recovery.get("r2PreflightManifest")
    need(isinstance(r2_manifest_record, Mapping)
         and resolve_spark_record(r2_manifest_record, roots, recovery_path,
                                  "r2 preflight manifest")
         == r2_manifest_path.resolve(),
         "r2 preflight manifest record drift")
    r2_manifest = json_object(r2_manifest_path)
    required_r2_files = {path.name for path in actual_files} - {"manifest.json"}
    verify_bundle_inventory(r2_bundle, r2_manifest, required_r2_files)
    need(r2_manifest.get("schemaVersion")
         == "feelm-service-v1-b1-preflight-manifest/1"
         and r2_manifest.get("runId") == R2_RUN_ID
         and r2_manifest.get("status")
         == "B1_FULL_PREFLIGHT_COMPLETE_AWAITING_REVIEW"
         and r2_manifest.get("sourceRows") == SOURCE_ROWS
         and r2_manifest.get("logicalRows") == LOGICAL_ROWS
         and r2_manifest.get("partitionCount") == 8
         and r2_manifest.get("resourceStatus") == "PASS"
         and r2_manifest.get("modelFitPerformed") is False
         and r2_manifest.get("fitAuthorized") is False
         and r2_manifest.get("trainingSourceSetSha256")
         == comparison["r2HistoricalTrainingSourceSetSha256"],
         "r2 preflight manifest semantic drift")
    r2_lock = json_object(r2_bundle / "input-lock.json")
    need(r2_lock.get("schemaVersion") == "feelm-service-v1-b1-input-lock/1"
         and r2_lock.get("phase") == "preflight"
         and r2_lock.get("trainingSourceSetSha256")
         == comparison["r2HistoricalTrainingSourceSetSha256"]
         and r2_lock.get("inputSetSha256") == r2_manifest.get("inputSetSha256")
         and r2_lock.get("controlReferenceSetSha256")
         == r2_manifest.get("controlReferenceSetSha256")
         and r2_lock.get("evaluationTargetsRead") is False,
         "r2 preflight lock semantic drift")
    need(recovery.get("r2PreflightDigests") == {
        "trainingSourceSetSha256": r2_lock["trainingSourceSetSha256"],
        "controlReferenceSetSha256": r2_lock["controlReferenceSetSha256"],
        "inputSetSha256": r2_lock["inputSetSha256"],
        "implementationSetSha256": r2_manifest["implementationSetSha256"],
    }, "r2 preflight embedded digest drift")

    r2_review_path = safe_existing_path(
        roots.r2_preflight_review(), directory=False).resolve()
    r2_review_record = recovery.get("r2PreflightReview")
    need(isinstance(r2_review_record, Mapping)
         and resolve_spark_record(r2_review_record, roots, recovery_path,
                                  "r2 preflight review") == r2_review_path,
         "r2 preflight review record drift")
    r2_review = json_object(r2_review_path)
    need(r2_review.get("schemaVersion") == "feelm-service-v1-b1-result-review/1"
         and r2_review.get("phase") == "preflight"
         and r2_review.get("status") == "PASS"
         and r2_review.get("readyForService") is False
         and r2_review.get("modelFitPerformed") is False,
         "r2 preflight review semantic drift")

    failure_path = safe_existing_path(roots.r2_fit_failure(), directory=False).resolve()
    failure_record = recovery.get("r2FitFailure")
    need(isinstance(failure_record, Mapping)
         and resolve_spark_record(failure_record, roots, recovery_path,
                                  "r2 fit failure") == failure_path,
         "r2 fit failure record drift")
    failure = json_object(failure_path)
    run = failure.get("containerRun")
    state = run.get("dockerState") if isinstance(run, Mapping) else None
    resource = run.get("resource") if isinstance(run, Mapping) else None
    need(failure.get("schemaVersion") == "feelm-service-v1-b1-failure/1"
         and failure.get("phase") == "fit" and failure.get("status") == "FAILED"
         and failure.get("cleanupComplete") is True
         and isinstance(run, Mapping) and run.get("timedOut") is True
         and run.get("workerTerminalResultPresent") is False
         and isinstance(state, Mapping) and state.get("ExitCode") == 143
         and state.get("OOMKilled") is False
         and isinstance(resource, Mapping)
         and resource.get("resourceStatus") == "RESOURCE_STOP"
         and resource.get("timedOut") is True,
         "r2 fit failure semantic drift")
    need(recovery.get("r2FitFailureFacts") == {
        "phase": "fit", "status": "FAILED", "timedOut": True,
        "resourceStatus": "RESOURCE_STOP", "exitCode": 143,
        "oomKilled": False, "cleanupComplete": True,
        "workerTerminalResultPresent": False, "modelWritten": False,
    }, "r2 fit failure fact summary drift")
    need(not os.path.lexists(roots.r2_preflight().with_name(R2_RUN_ID + "-fit")),
         "r2 failure/success fit coexist")

    r2_runner = resolve_spark_record(recovery["r2OuterRunner"], roots,
                                     recovery_path, "r2 outer runner")
    r2_plan = resolve_spark_record(recovery["r2Plan"], roots,
                                   recovery_path, "r2 plan")
    need(r2_runner == roots.r2_runner().resolve()
         and r2_plan == roots.r2_plan().resolve()
         and recovery["r2Plan"].get("path")
         == "ancestor/service-v1-b1-spark-runner.md",
         "r2 implementation/plan ancestry drift")
    if canonical:
        need(r2_manifest_record.get("sha256") == R2_PREFLIGHT_MANIFEST_SHA256
             and r2_review_record.get("sha256") == R2_PREFLIGHT_REVIEW_SHA256
             and failure_record.get("sha256") == R2_FIT_FAILURE_SHA256
             and recovery["r2OuterRunner"].get("sha256")
             == R2_OUTER_RUNNER_SHA256
             and recovery["r2Plan"].get("sha256") == R2_PLAN_SHA256
             and comparison["r2HistoricalTrainingSourceSetSha256"]
             == R2_TRAINING_SOURCE_SET_SHA256,
             "canonical r2 ancestry pin drift")
    control_paths = [*actual_files, r2_review_path, failure_path, r2_runner, r2_plan]
    for path in control_paths:
        need(sum(current == path.resolve() for current in chain_paths.values()) == 1,
             f"verified score chain omitted r2 ancestry: {path}")
    return recovery, control_paths


def phase_review_targets(roots: Roots, phase: str, bundle: Path) -> dict[str, Path]:
    maps = {
        "preflight": {
            "input_lock": "input-lock.json",
            "recovery_reference": "recovery-reference.json",
            "execution_profile": "execution-profile.json",
            "partition_identity": "partition-identity.json",
            "command": "command.json", "resource": "resource.json",
            "run_log": "run.log",
        },
        "fit": {
            "input_lock": "input-lock.json",
            "preflight_reference": "preflight-reference.json",
            "recovery_reference": "recovery-reference.json",
            "execution_profile": "execution-profile.json",
            "command": "command.json",
            "partition_identity": "partition-identity.json",
            "resolved_estimator": "resolved-estimator.json",
            "model_file_inventory": "model-file-inventory.json",
            "threshold_fixtures": "threshold-fixtures.npz",
            "fit_metrics": "fit-metrics.json", "resource": "resource.json",
            "run_log": "run.log",
        },
        "score": {
            "fit_reference": "fit-reference.json",
            "score_input_lock": "score-input-lock.json",
            "recovery_reference": "recovery-reference.json",
            "execution_profile": "execution-profile.json",
            "command": "command.json",
            "analyzed_plan": "score/analyzed-plan.txt",
            "predictions": "score/predictions.parquet",
            "resource": "resource.json", "run_log": "run.log",
        },
    }
    need(phase in maps, "unknown Spark review phase")
    result = {"manifest": bundle / "manifest.json"}
    result.update({key: bundle / relative for key, relative in maps[phase].items()})
    result["outer_runner"] = roots.standalone / "scripts/run_service_v1_b1_gbt_r3.py"
    result["spark_worker"] = roots.standalone / "scripts/service_v1_b1_spark_worker.py"
    return result


def verify_phase_review(review_path: Path, phase: str, bundle: Path,
                        lock: Mapping[str, Any], roots: Roots,
                        chain_paths: Mapping[str, Path], *, spec: AuditSpec
                        ) -> dict[str, Any]:
    review = json_object(review_path)
    verify_score_review_envelope(
        review, review_path, roots, phase, canonical=spec.canonical,
        source_rows=spec.training_rows, score_rows=spec.score_rows)
    targets = phase_review_targets(roots, phase, bundle)
    verify_review_target(review, review_path, targets, roots, phase)
    recovery_check = review["checks"]["recovery"]["reference"]
    recovery_target = review["target"]["recovery_reference"]
    need(recovery_check == {
        "bytes": recovery_target.get("bytes"),
        "sha256": recovery_target.get("sha256"),
    }, f"{phase} review recovery check/target pin drift")
    for key, path in targets.items():
        record = review["target"][key]
        expected_fields = {"path", "bytes", "sha256"}
        if key in {"input_lock", "score_input_lock"}:
            expected_fields |= {
                "modelInputSetSha256", "workerRuntimeSetSha256",
                "executionSetSha256", "controlReferenceSetSha256",
                "inputSetSha256",
            }
            if key == "score_input_lock":
                expected_fields.add("scoreInputSetSha256")
        need(set(record) == expected_fields,
             f"{phase} review target.{key} field set drift")
        if key in {"input_lock", "score_input_lock"}:
            for digest in expected_fields - {"path", "bytes", "sha256"}:
                need(record.get(digest) == lock.get(digest),
                     f"{phase} review omitted lock digest {digest}")
        chain_contains(chain_paths, path, loose_pin(record),
                       f"{phase} review target.{key}")
    verify_fingerprint(review, review_path, roots, chain_paths,
                       phase=phase, canonical=spec.canonical)
    return review


def verify_phase_profile(profile_path: Path, phase: str,
                         groups: Mapping[str, Sequence[Mapping[str, Any]]],
                         roots: Roots, *, canonical: bool) -> None:
    document = json_object(profile_path)
    need(set(document) == {
        "schemaVersion", "runId", "profileId", "phase", "profileContract",
        "outerRunner", "executionSetSha256", "resolved",
    }, f"{phase} execution profile field set drift")
    profile = _one_record(groups["execution"],
                          "execution/local4c12g-t14400-profile.json",
                          f"{phase} profile")
    outer = _one_record(groups["execution"],
                        "execution/run_service_v1_b1_gbt_r3.py",
                        f"{phase} outer")
    profile_path_physical = resolve_spark_record(profile, roots, profile_path,
                                                 f"{phase} profile contract")
    need(document.get("schemaVersion")
         == "feelm-service-v1-b1-phase-execution-profile/1"
         and document.get("runId") == RUN_ID
         and document.get("profileId") == PROFILE_ID
         and document.get("phase") == phase
         and document.get("profileContract") == profile
         and document.get("outerRunner") == outer
         and document.get("executionSetSha256")
         == record_set_digest(groups["execution"])
         and document.get("resolved") == json_object(profile_path_physical),
         f"{phase} execution profile chain drift")
    if canonical:
        need(profile.get("sha256") == REVIEWED_R3_PROFILE_SHA256,
             f"{phase} unreviewed profile")


def verify_embedded_recovery(value: Any, recovery: Mapping[str, Any],
                             label: str) -> None:
    need(isinstance(value, Mapping)
         and set(value) == {"bytes", "sha256", "payload"}
         and value.get("payload") == recovery
         and {"bytes": value.get("bytes"), "sha256": value.get("sha256")}
         == canonical_json_pin(recovery),
         f"{label} embedded recovery drift")


def verify_recursive_spark_chain(score_bundle: Path,
                                 fit_reference: Mapping[str, Any],
                                 roots: Roots, chain_paths: Mapping[str, Path],
                                 spec: AuditSpec) -> dict[str, Any]:
    """Independently prove score -> fit -> r3 preflight -> r2 success/failure."""
    canonical = spec.canonical
    score_bundle = safe_existing_path(score_bundle, directory=True).resolve()
    fit_bundle = safe_existing_path(roots.spark_bundle("fit"), directory=True).resolve()
    preflight_bundle = safe_existing_path(roots.spark_bundle("preflight"), directory=True).resolve()
    need(score_bundle == safe_existing_path(roots.spark_bundle("score"), directory=True).resolve()
         or not canonical, "noncanonical r3 score bundle")
    for bundle in (preflight_bundle, fit_bundle, score_bundle):
        safe_existing_path(bundle, directory=True)
        need(not os.path.lexists(bundle.with_name(bundle.name + "-failure.json")),
             "r3 ancestor success/failure coexist")

    preflight_manifest = json_object(preflight_bundle / "manifest.json")
    fit_manifest = json_object(fit_bundle / "manifest.json")
    score_manifest = json_object(score_bundle / "manifest.json")
    need(preflight_manifest.get("schemaVersion")
         == "feelm-service-v1-b1-preflight-manifest/2"
         and preflight_manifest.get("runId") == RUN_ID
         and preflight_manifest.get("profileId") == PROFILE_ID
         and preflight_manifest.get("status")
         == "B1_FULL_PREFLIGHT_COMPLETE_AWAITING_REVIEW"
         and preflight_manifest.get("fitAuthorized") is False
         and preflight_manifest.get("modelFitPerformed") is False
         and preflight_manifest.get("scorePerformed") is False
         and preflight_manifest.get("readyForService") is False,
         "r3 preflight manifest semantic drift")
    need(fit_manifest.get("schemaVersion") == "feelm-service-v1-b1-fit-manifest/2"
         and fit_manifest.get("runId") == RUN_ID
         and fit_manifest.get("profileId") == PROFILE_ID
         and fit_manifest.get("status") == "B1_MODEL_FIT_COMPLETE_AUDIT_PENDING"
         and fit_manifest.get("scoringAuthorized") is False
         and fit_manifest.get("modelFitPerformed") is True
         and fit_manifest.get("scorePerformed") is False
         and fit_manifest.get("readyForService") is False,
         "r3 fit manifest semantic drift")
    need(score_manifest.get("schemaVersion")
         == "feelm-service-v1-b1-score-manifest/2"
         and score_manifest.get("runId") == RUN_ID
         and score_manifest.get("profileId") == PROFILE_ID
         and score_manifest.get("status")
         == "B1_NATURAL_SCORE_COMPLETE_AUDIT_PENDING"
         and score_manifest.get("evaluationAuthorized") is False
         and score_manifest.get("modelFitPerformed") is False
         and score_manifest.get("scorePerformed") is True
         and score_manifest.get("readyForService") is False,
         "r3 score manifest semantic drift")

    preflight_required = {
        "input-lock.json", "recovery-reference.json", "execution-profile.json",
        "partition-identity.json", "command.json", "resource.json", "run.log",
    }
    verify_bundle_inventory(preflight_bundle, preflight_manifest,
                            preflight_required)
    model_inventory_path = fit_bundle / "model-file-inventory.json"
    model_inventory = json_object(model_inventory_path)
    model_files = model_inventory.get("files")
    need(model_inventory.get("schemaVersion")
         == "feelm-service-v1-b1-model-inventory/1"
         and isinstance(model_files, list) and model_files,
         "r3 fit model inventory contract drift")
    native_names: set[str] = set()
    native_records: dict[str, dict[str, Any]] = {}
    for record in model_files:
        valid_pin(record, path_required=True)
        name = relative_name(record["path"])
        need(name not in native_names, "duplicate native model file")
        native_names.add(name)
        native_records[name] = {"bytes": record["bytes"], "sha256": record["sha256"]}
    need(inventory(fit_bundle / "model/native") == native_records
         and model_inventory.get("inventorySha256")
         == canonical_digest(native_records),
         "r3 native model exact inventory drift")
    fit_required = {
        "input-lock.json", "preflight-reference.json", "command.json",
        "partition-identity.json", "resolved-estimator.json",
        "model-file-inventory.json", "threshold-fixtures.npz",
        "fit-metrics.json", "resource.json", "run.log",
        "execution-profile.json", "recovery-reference.json",
    } | {"model/native/" + name for name in native_names}
    verify_bundle_inventory(fit_bundle, fit_manifest, fit_required)
    score_required = {
        "fit-reference.json", "score-input-lock.json", "command.json",
        "score/analyzed-plan.txt", "score/predictions.parquet",
        "resource.json", "run.log", "execution-profile.json",
        "recovery-reference.json",
    }
    verify_bundle_inventory(score_bundle, score_manifest, score_required)

    r2_controls_expected = [
        *phase_bundle_files(roots.r2_preflight()),
        roots.r2_preflight_review(), roots.r2_fit_failure(),
        roots.r2_runner(), roots.r2_plan(),
    ]
    preflight_groups = verify_spark_lock(
        preflight_bundle / "input-lock.json", "preflight",
        preflight_manifest, roots, chain_paths, r2_controls_expected,
        canonical=canonical)
    recovery, r2_controls = verify_r2_recovery(
        preflight_bundle / "recovery-reference.json", roots,
        preflight_groups, chain_paths, spec, canonical=canonical)
    need([path.resolve() for path in r2_controls]
         == [path.resolve() for path in r2_controls_expected],
         "r2 control closure/recovery mismatch")
    verify_phase_profile(preflight_bundle / "execution-profile.json",
                         "preflight", preflight_groups, roots,
                         canonical=canonical)
    preflight_review_path = safe_existing_path(
        roots.spark_review("preflight"), directory=False).resolve()
    verify_phase_review(preflight_review_path, "preflight", preflight_bundle,
                        json_object(preflight_bundle / "input-lock.json"),
                        roots, chain_paths, spec=spec)

    fit_controls_expected = [
        *phase_bundle_files(preflight_bundle), preflight_review_path,
        *r2_controls,
    ]
    fit_groups = verify_spark_lock(
        fit_bundle / "input-lock.json", "fit", fit_manifest, roots,
        chain_paths, fit_controls_expected, canonical=canonical)
    need(record_signature(preflight_groups["models"])
         == record_signature(fit_groups["models"])
         and record_signature(preflight_groups["runtime"])
         == record_signature(fit_groups["runtime"])
         and record_signature(preflight_groups["execution"])
         == record_signature(fit_groups["execution"]),
         "r3 preflight/fit invariant input drift")
    fit_recovery = json_object(fit_bundle / "recovery-reference.json")
    need(fit_recovery == recovery, "r3 fit/preflight recovery drift")
    verify_phase_profile(fit_bundle / "execution-profile.json", "fit",
                         fit_groups, roots, canonical=canonical)
    fit_review_path = safe_existing_path(
        roots.spark_review("fit"), directory=False).resolve()
    verify_phase_review(fit_review_path, "fit", fit_bundle,
                        json_object(fit_bundle / "input-lock.json"),
                        roots, chain_paths, spec=spec)

    preflight_reference = json_object(fit_bundle / "preflight-reference.json")
    expected_preflight_fields = {
        "schemaVersion", "runId", "profileId", "preflightManifest",
        "preflightReview", "reviewedArtifacts", "recoveryReference",
        "outerRunner", "sparkWorker", "executionProfile",
        "modelInputSetSha256", "workerRuntimeSetSha256",
        "executionSetSha256", "controlReferenceSetSha256",
        "fitInputSetSha256",
    }
    need(set(preflight_reference) == expected_preflight_fields
         and preflight_reference.get("schemaVersion")
         == "feelm-service-v1-b1-preflight-reference/2"
         and preflight_reference.get("runId") == RUN_ID
         and preflight_reference.get("profileId") == PROFILE_ID,
         "r3 preflight reference identity/field drift")
    preflight_manifest_pin = preflight_reference.get("preflightManifest")
    preflight_review_pin = preflight_reference.get("preflightReview")
    need(isinstance(preflight_manifest_pin, Mapping)
         and resolve_spark_record(preflight_manifest_pin, roots,
                                  fit_bundle / "preflight-reference.json",
                                  "preflight reference manifest")
         == (preflight_bundle / "manifest.json").resolve()
         and isinstance(preflight_review_pin, Mapping)
         and resolve_spark_record(preflight_review_pin, roots,
                                  fit_bundle / "preflight-reference.json",
                                  "preflight reference review")
         == preflight_review_path,
         "r3 preflight reference parent pin drift")
    verify_embedded_recovery(preflight_reference.get("recoveryReference"),
                             recovery, "preflight reference")
    fit_lock = json_object(fit_bundle / "input-lock.json")
    need(preflight_reference.get("reviewedArtifacts")
         == fit_lock["controlReferences"]
         and preflight_reference.get("outerRunner")
         == _one_record(fit_groups["execution"],
                        "execution/run_service_v1_b1_gbt_r3.py", "fit outer")
         and preflight_reference.get("sparkWorker")
         == _one_record(fit_groups["runtime"],
                        "implementation/service_v1_b1_spark_worker.py",
                        "fit worker")
         and preflight_reference.get("executionProfile")
         == _one_record(fit_groups["execution"],
                        "execution/local4c12g-t14400-profile.json",
                        "fit profile")
         and preflight_reference.get("modelInputSetSha256")
         == fit_lock["modelInputSetSha256"]
         and preflight_reference.get("workerRuntimeSetSha256")
         == fit_lock["workerRuntimeSetSha256"]
         and preflight_reference.get("executionSetSha256")
         == fit_lock["executionSetSha256"]
         and preflight_reference.get("controlReferenceSetSha256")
         == fit_lock["controlReferenceSetSha256"]
         and preflight_reference.get("fitInputSetSha256")
         == fit_lock["inputSetSha256"]
         and fit_manifest.get("preflightManifestSha256")
         == preflight_manifest_pin.get("sha256")
         and fit_manifest.get("preflightReviewSha256")
         == preflight_review_pin.get("sha256"),
         "r3 preflight reference chain drift")

    score_controls_expected = [
        *phase_bundle_files(fit_bundle), fit_review_path, *fit_controls_expected,
    ]
    score_groups = verify_spark_lock(
        score_bundle / "score-input-lock.json", "score", score_manifest,
        roots, chain_paths, score_controls_expected, canonical=canonical)
    need(record_signature(score_groups["models"])
         == record_signature(fit_groups["models"])
         and record_signature(score_groups["runtime"])
         == record_signature(fit_groups["runtime"])
         and record_signature(score_groups["execution"])
         == record_signature(fit_groups["execution"]),
         "r3 fit/score invariant input drift")
    score_recovery = json_object(score_bundle / "recovery-reference.json")
    need(score_recovery == recovery, "r3 score/fit recovery drift")
    verify_phase_profile(score_bundle / "execution-profile.json", "score",
                         score_groups, roots, canonical=canonical)
    score_lock = json_object(score_bundle / "score-input-lock.json")
    verify_embedded_recovery(fit_reference.get("recoveryReference"),
                             recovery, "fit reference")
    expected_fit_fields = {
        "schemaVersion", "runId", "profileId", "fitManifest", "fitReview",
        "modelInventory", "modelFileSetSha256", "modelFiles",
        "reviewedArtifacts", "recoveryReference", "outerRunner",
        "sparkWorker", "executionProfile", "modelInputSetSha256",
        "workerRuntimeSetSha256", "executionSetSha256",
        "scoreInputSetSha256", "scorePhaseInputSetSha256",
    }
    need(set(fit_reference) == expected_fit_fields
         and fit_reference.get("reviewedArtifacts")
         == score_lock["controlReferences"]
         and fit_reference.get("modelFiles") == model_files
         and fit_reference.get("modelFileSetSha256")
         == model_inventory["inventorySha256"]
         and fit_reference.get("outerRunner")
         == _one_record(score_groups["execution"],
                        "execution/run_service_v1_b1_gbt_r3.py",
                        "score outer")
         and fit_reference.get("sparkWorker")
         == _one_record(score_groups["runtime"],
                        "implementation/service_v1_b1_spark_worker.py",
                        "score worker")
         and fit_reference.get("executionProfile")
         == _one_record(score_groups["execution"],
                        "execution/local4c12g-t14400-profile.json",
                        "score profile")
         and fit_reference.get("modelInputSetSha256")
         == score_lock["modelInputSetSha256"]
         and fit_reference.get("workerRuntimeSetSha256")
         == score_lock["workerRuntimeSetSha256"]
         and fit_reference.get("executionSetSha256")
         == score_lock["executionSetSha256"]
         and fit_reference.get("scoreInputSetSha256")
         == score_lock["scoreInputSetSha256"]
         and fit_reference.get("scorePhaseInputSetSha256")
         == score_lock["inputSetSha256"],
         "r3 fit reference chain drift")
    need(score_manifest.get("fitManifestSha256")
         == fit_reference["fitManifest"].get("sha256")
         and score_manifest.get("fitReviewSha256")
         == fit_reference["fitReview"].get("sha256")
         and score_manifest.get("modelInventorySha256")
         == fit_reference["modelInventory"].get("sha256")
         and score_manifest.get("modelFileSetSha256")
         == fit_reference["modelFileSetSha256"],
         "r3 score manifest/fit reference drift")
    return {
        "preflight": preflight_groups, "fit": fit_groups,
        "score": score_groups, "recovery": recovery,
    }


def verify_score_reference(reference_path: Path, roots: Roots,
                           sources: Mapping[str, Path], spec: AuditSpec) -> dict[str, Any]:
    reference = json_object(reference_path)
    required = {"schemaVersion", "scoreManifest", "scoreReview", "predictions",
                "fitReference", "scoreStatus", "scoreReviewStatus", "verifiedChainFiles",
                "runId", "profileId"}
    need(set(reference) == required, "score reference field set drift")
    need(reference.get("schemaVersion") == "feelm-service-v1-b1-score-reference/2"
         and reference.get("runId") == RUN_ID and reference.get("profileId") == PROFILE_ID,
         "score reference identity/schema drift")
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
    need(score_manifest.get("runId") == RUN_ID
         and score_manifest.get("profileId") == PROFILE_ID
         and score_manifest.get("status") == "B1_NATURAL_SCORE_COMPLETE_AUDIT_PENDING"
         and score_manifest.get("evaluationAuthorized") is False
         and score_manifest.get("readyForService") is False,
         "score manifest state/runId drift")
    need(score_review.get("schemaVersion") == "feelm-service-v1-b1-r3-result-review/1"
         and score_review.get("runId") == RUN_ID
         and score_review.get("profileId") == PROFILE_ID
         and score_review.get("phase") == "score" and score_review.get("status") == "PASS",
         "score independent review state drift")
    score_required = {
        "fit-reference.json", "score-input-lock.json", "command.json",
        "score/analyzed-plan.txt", "score/predictions.parquet", "resource.json", "run.log",
        "execution-profile.json", "recovery-reference.json",
    }
    verify_bundle_inventory(score_bundle, score_manifest, score_required)
    score_lock = json_object(score_bundle / "score-input-lock.json")
    verify_phase_review(score_review_path, "score", score_bundle, score_lock,
                        roots, chain_paths, spec=spec)

    fit_reference = json_object(fit_reference_path)
    need(fit_reference.get("schemaVersion") == "feelm-service-v1-b1-fit-reference/2"
         and fit_reference.get("runId") == RUN_ID
         and fit_reference.get("profileId") == PROFILE_ID,
         "fit reference identity/schema drift")
    verify_recursive_spark_chain(
        score_bundle, fit_reference, roots, chain_paths, spec)
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
    need(fit_manifest_payload.get("runId") == RUN_ID
         and fit_manifest_payload.get("profileId") == PROFILE_ID
         and fit_manifest_payload.get("status") == "B1_MODEL_FIT_COMPLETE_AUDIT_PENDING",
         "fit ancestor manifest state drift")
    need(fit_review_payload.get("schemaVersion") == "feelm-service-v1-b1-r3-result-review/1"
         and fit_review_payload.get("runId") == RUN_ID
         and fit_review_payload.get("profileId") == PROFILE_ID
         and fit_review_payload.get("status") == "PASS"
         and fit_review_payload.get("phase") == "fit",
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
        resolved = safe_existing_path(path, directory=False).resolve()
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
    safe_existing_path(directory, directory=True)
    physical = inventory(directory)
    actual = {prefix + name: directory / name for name in physical}
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
    safe_file(path)
    safe_file(score_axis)
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


def no_transient_outputs(roots: Roots, phase: str, *,
                         lease: ReviewPublicationLease | None = None,
                         own_temp: bool = False) -> None:
    bundle = roots.bundle(phase)
    no_transient_bundle(bundle, phase, lease=lease, own_temp=own_temp)


def no_transient_bundle(bundle: Path, label: str,
                        *, lease: ReviewPublicationLease | None = None,
                        own_temp: bool = False) -> None:
    bundle = Path(os.path.abspath(os.fspath(bundle)))
    safe_existing_path(bundle, directory=True)
    failure = bundle.with_name(bundle.name + "-failure.json")
    review = bundle.with_name(bundle.name + "-result-review.json")
    need(not os.path.lexists(failure),
         f"{label} failure record exists beside success bundle")
    safe_existing_path(bundle.parent, directory=True)
    siblings = list(bundle.parent.iterdir())
    transient_prefixes = (
        "." + bundle.name + ".tmp-",
        "." + review.name + ".tmp-",
        "." + failure.name + ".tmp-",
    )
    if lease is not None:
        _validate_review_lease(lease)
        need(lease.bundle == bundle,
             f"{label} review publication lease belongs to another bundle")
    allowed = lease.temporary if lease is not None and own_temp else None
    need(not [path for path in siblings
              if path.name.startswith(transient_prefixes) and path != allowed],
         f"{label} stale staging directory exists")
    need(not [path for path in siblings
              if path.name.startswith("." + bundle.name + ".")
              and "-scratch-" in path.name],
         f"{label} stale scratch directory exists")
    claim = bundle.with_name("." + bundle.name + ".publication-claim")
    if lease is None:
        need(not os.path.lexists(claim),
             f"{label} stale publication claim exists")
    else:
        need(lease.claim == claim and os.path.lexists(claim),
             f"{label} publication claim allowance drift")
        safe_existing_path(claim, directory=False)


def dependency_fingerprint(roots: Roots, phase: str,
                           *, lease: ReviewPublicationLease | None = None,
                           own_temp: bool = False) -> dict[str, Any]:
    bundle = roots.bundle(phase)
    no_transient_outputs(roots, phase, lease=lease, own_temp=own_temp)
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
        "runId": RUN_ID,
        "profileId": PROFILE_ID,
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
        "schemaVersion", "runId", "profileId", "phase", "status", gate_key, "confirmationAuthorized",
        "readyForService", "serviceActivationAuthorized", "freshBlindHoldout", "evaluator",
        "evaluationSourceSetSha256", "truthCensus", "startedAt", "completedAt", "seconds",
        "runtime", "files",
    }
    need(set(manifest) == expected_keys, "evaluation manifest field set drift")
    expected_phase = phase.upper()
    expected_status = ("B1_SELECTION_COMPLETE_AUDIT_PENDING" if phase == "selection"
                       else "B1_CONFIRMATION_COMPLETE_AUDIT_PENDING")
    need(manifest.get("schemaVersion") == "feelm-service-v1-b1-r3-evaluation-result/1"
         and manifest.get("runId") == RUN_ID and manifest.get("profileId") == PROFILE_ID
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
        "runId": RUN_ID,
        "profileId": PROFILE_ID,
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
    need(review.get("schemaVersion") == "feelm-service-v1-b1-r3-evaluation-result-review/1"
         and review.get("runId") == RUN_ID and review.get("profileId") == PROFILE_ID
         and review.get("phase") == "selection"
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
    expected_keys = {"schemaVersion", "runId", "profileId", "selectionReview", "evaluator", "requiredSelectionGate",
                     "selectionReviewStatus", *named}
    need(set(reference) == expected_keys
         and reference.get("schemaVersion") == "feelm-service-v1-b1-r3-selection-reference/1"
         and reference.get("runId") == RUN_ID and reference.get("profileId") == PROFILE_ID,
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
        "schemaVersion": "feelm-service-v1-b1-r3-evaluation-result-review/1",
        "runId": RUN_ID, "profileId": PROFILE_ID, "phase": phase, "status": "PASS",
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
        need(expected_evaluator_sha256 == REVIEWED_R3_EVALUATOR_SHA256,
             "evaluator SHA256 is not the independently reviewed implementation")
    bundle = roots.bundle(phase)
    safe_existing_path(bundle, directory=True)
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


@dataclass(frozen=True)
class ReviewPublicationLease:
    bundle: Path
    destination: Path
    claim: Path
    temporary: Path
    token: str


def _validate_review_lease(lease: ReviewPublicationLease) -> None:
    bundle = Path(os.path.abspath(os.fspath(lease.bundle)))
    destination = Path(os.path.abspath(os.fspath(lease.destination)))
    expected_claim = bundle.with_name("." + bundle.name + ".publication-claim")
    expected_temporary = destination.with_name(
        "." + destination.name + ".tmp-" + lease.token)
    need(lease.bundle == bundle and lease.destination == destination
         and lease.claim == expected_claim and lease.temporary == expected_temporary
         and isinstance(lease.token, str) and len(lease.token) == 32
         and all(character in "0123456789abcdef" for character in lease.token),
         "evaluation review publication lease identity drift")
    safe_existing_path(lease.claim, directory=False)
    need(lease.claim.read_text(encoding="ascii") == lease.token + "\n",
         "evaluation review publication claim ownership drift")


def _release_review_lease(lease: ReviewPublicationLease) -> None:
    _validate_review_lease(lease)
    lease.claim.unlink()
    if os.name != "nt":
        descriptor = os.open(lease.claim.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _scan_review_namespace(lease: ReviewPublicationLease, *, own_temp: bool) -> None:
    _validate_review_lease(lease)
    need(not os.path.lexists(lease.destination),
         "immutable evaluation result review already exists")
    no_transient_bundle(
        lease.bundle, "evaluation review publication",
        lease=lease, own_temp=own_temp)
    if own_temp:
        safe_existing_path(lease.temporary, directory=False)


def _acquire_review_lease(roots: Roots, phase: str) -> ReviewPublicationLease:
    bundle = Path(os.path.abspath(os.fspath(roots.bundle(phase))))
    destination = Path(os.path.abspath(os.fspath(roots.review(phase))))
    safe_existing_path(bundle, directory=True)
    safe_existing_path(destination.parent, directory=True)
    need(destination.parent == bundle.parent,
         "evaluation review is not the canonical bundle sibling")
    token = uuid.uuid4().hex
    claim = bundle.with_name("." + bundle.name + ".publication-claim")
    temporary = destination.with_name("." + destination.name + ".tmp-" + token)
    try:
        with claim.open("x", encoding="ascii", newline="\n") as stream:
            stream.write(token + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as error:
        raise AuditError(f"evaluation review namespace already claimed: {claim}") from error
    lease = ReviewPublicationLease(bundle, destination, claim, temporary, token)
    try:
        _scan_review_namespace(lease, own_temp=False)
    except BaseException:
        _release_review_lease(lease)
        raise
    return lease


def _create_review_temporary(lease: ReviewPublicationLease, encoded: bytes) -> None:
    """Create exactly the lease-owned temp and clean every partial write."""
    created = False
    try:
        with lease.temporary.open("xb") as stream:
            created = True
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        if created and os.path.lexists(lease.temporary):
            safe_existing_path(lease.temporary, directory=False)
            lease.temporary.unlink()
        raise


def publish_review(roots: Roots, phase: str, review: Mapping[str, Any], *,
                   _spec: AuditSpec | None = None,
                   _source_pins: Mapping[str, tuple[int, str]] | None = None) -> Path:
    """Re-audit and atomically publish only the exact resulting attestation.

    Private overrides only keep synthetic fixtures small; the CLI never exposes
    them.  Re-running the audit here prevents a caller from forging any review
    decision, check, reviewer, or safety field between audit and publication.
    """
    need(review.get("schemaVersion") == "feelm-service-v1-b1-r3-evaluation-result-review/1"
         and review.get("runId") == RUN_ID and review.get("profileId") == PROFILE_ID
         and review.get("phase") == phase
         and review.get("status") == "PASS", "only a successful r3 audit can publish")
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
    lease = _acquire_review_lease(roots, phase)
    temporary_created = False
    try:
        try:
            need(candidate.get("target") == target_records(roots, phase),
                 "evaluation target changed before review publication")
            need(candidate.get("dependencyFingerprint") == dependency_fingerprint(
                roots, phase, lease=lease),
                 "evaluation dependency changed before review publication")
        except (AuditError, OSError) as error:
            raise AuditError(
                f"evaluation target or dependency changed before review publication: {error}"
            ) from error
        encoded = (json.dumps(candidate, ensure_ascii=False, allow_nan=False,
                              sort_keys=True, indent=2) + "\n").encode("utf-8")
        _create_review_temporary(lease, encoded)
        temporary_created = True
        json_equal(json_object(lease.temporary), candidate, "serialized evaluation review")
        try:
            need(candidate.get("target") == target_records(roots, phase),
                 "evaluation target changed while serializing review")
            need(candidate.get("dependencyFingerprint") == dependency_fingerprint(
                roots, phase, lease=lease, own_temp=True),
                 "evaluation dependency changed immediately before review publication")
        except (AuditError, OSError) as error:
            raise AuditError(
                f"evaluation target or dependency changed immediately before review publication: {error}"
            ) from error
        _scan_review_namespace(lease, own_temp=True)
        rename_no_replace(lease.temporary, lease.destination)
        temporary_created = False
        if os.name != "nt":
            descriptor = os.open(lease.destination.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        if temporary_created and os.path.lexists(lease.temporary):
            safe_existing_path(lease.temporary, directory=False)
            lease.temporary.unlink()
        _release_review_lease(lease)
    return lease.destination


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


def public_roots(standalone_root: Path, team_repo: Path) -> Roots:
    """Bind CLI roots lexically before resolving any path component."""
    supplied_standalone = Path(os.path.abspath(os.fspath(standalone_root)))
    supplied_team = Path(os.path.abspath(os.fspath(team_repo)))
    canonical_standalone = Path(os.path.abspath(os.fspath(ROOT)))
    canonical_team = Path(os.path.abspath(os.fspath(TEAM_ROOT)))
    safe_existing_path(supplied_standalone, directory=True)
    safe_existing_path(supplied_team, directory=True)
    need(supplied_standalone == canonical_standalone,
         "public standalone root is not the canonical lexical repository root")
    need(supplied_team == canonical_team,
         "public team root is not the canonical lexical repository root")
    return Roots(supplied_standalone.resolve(), supplied_team.resolve())


def main(argv: Sequence[str] | None = None) -> None:
    args = parser().parse_args(argv)
    require_public_r3_contract_sealed()
    roots = public_roots(args.standalone_root, args.team_repo)
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
