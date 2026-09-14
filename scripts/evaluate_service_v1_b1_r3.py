"""Evaluate audited Service-v1 B1 r3 predictions against the sealed B0 axis.

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
import ast
import csv
import hashlib
import json
import math
import os
import platform
import shutil
import stat
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
SPARK_AUDITOR = ROOT / "scripts/audit_service_v1_b1_spark_outputs_r3.py"
EVALUATION_AUDITOR = ROOT / "scripts/audit_service_v1_b1_evaluation_outputs_r3.py"
PLAN = ROOT / "docs/recommendation/plans/service-v1-b1-r3-fit-recovery.md"
PLAN_SHA256 = "4de13f041532b7249b22f5a2bbfbd7919f79d94e4c25f2ae16fe95104a2b3d9a"
RUN_ID = "b1-gbt120-s339-v1-r3-local4c12g-t14400"
PROFILE_ID = "local4c12g-t14400-v1"
R2_RUN_ID = "b1-gbt120-s339-v1-r2"
R2_PLAN_SHA256 = "c5b90b0d19374588fc5cb7a1d98cd6510a1c3708df9667131df5eb3826c79d49"
R2_OUTER_RUNNER_BYTES = 88_623
R2_OUTER_RUNNER_SHA256 = "cbc8983a0047b5aa0a2fc5a2d3794080afcf48583fe1be90c6d2dd3dd5af6f8e"
R2_PREFLIGHT_MANIFEST_BYTES = 2_351
R2_PREFLIGHT_MANIFEST_SHA256 = "7a47a40ac923ba1f8a638ad6029aa72349245b65055f3db8915a2327de174e01"
R2_PREFLIGHT_REVIEW_BYTES = 8_706
R2_PREFLIGHT_REVIEW_SHA256 = "cc49688618bfd408a957a22522456912320519d981c34ad87aefb190a0910132"
R2_FIT_FAILURE_BYTES = 24_854
R2_FIT_FAILURE_SHA256 = "4ad571439af7f290dd32ce27922519f31e3b156f90998b26a69e2c892aba7e37"
R2_TRAINING_SOURCE_SET_SHA256 = "6d82b745ec4e796f823f6506497ad9a15236f92a91a04d3452c4755fada499f9"
MODEL_INPUT_SET_SHA256 = "cd41a8ac341cdbc1435bbce21bd78e3b3012e2c50ca25cfac0ef034088b1ccfd"
WORKER_RUNTIME_SET_SHA256 = "ff90bcd014b8ac498f2c5ccba27eb33d0a83f3ee062d36dc426e33a95c6a0a0a"
# These pins are filled only after the r3 Spark producer and its independent
# auditor have passed their own normal/-O tests and the files have been frozen.
# Production CLI execution is deliberately fail-closed while any value is
# absent; synthetic unit tests call the pure/evaluation functions directly.
REVIEWED_R3_RUNNER_SHA256: str | None = (
    "783a5a4c7f1783416271de0a1cde6a6be55409ef8b625a757f8a07c153c7a574"
)
REVIEWED_R3_PROFILE_SHA256: str | None = (
    "761c210364ab465f9143ded5dba8c2f3fba9a1d34c10f1ea54d742a528494c23"
)
REVIEWED_R3_SPARK_AUDITOR_SHA256: str | None = (
    "92a9e48597205f54509ccd7fbd9c717cb791bbd31a6998cb9d966177f830d3be"
)
# The evaluation auditor must embed this evaluator's final SHA, so pinning the
# auditor's literal full-file SHA here would create an impossible hash cycle.
# Instead we pin its complete source after normalizing only that one 64-hex
# evaluator-pin literal.  Every executable/behavioral byte remains covered;
# the review separately pins and rehashes the current full auditor file.
REVIEWED_R3_EVALUATION_AUDITOR_CORE_SHA256: str | None = (
    "fdd756107c11d19c6332274017bb8ed4933ddb7f377023b18e43f7d748eaaf50"
)
EVALUATION_AUDITOR_PIN_SENTINEL = "0" * 64
EVALUATION_SELECTION_NAME = RUN_ID + "-selection"
EVALUATION_CONFIRMATION_NAME = RUN_ID + "-confirmation"
R2_PREFLIGHT_INVENTORY: dict[str, tuple[int, str]] = {
    "command.json": (7_720, "86e05c4b3555297c9a803b91cea840b2f7ea83286a2a82591a5eb1f950b15859"),
    "input-lock.json": (3_489, "fbd4ae898a8cc35e3e4e7c4442298d6940dc88b3854d20182b7f38c87334d775"),
    "manifest.json": (R2_PREFLIGHT_MANIFEST_BYTES, R2_PREFLIGHT_MANIFEST_SHA256),
    "partition-identity.json": (2_489, "2a7f7fbd9000730db749cc673d9a43a5027fa3ca76d517cda826f3f420d45c99"),
    "recovery-reference.json": (959, "9c7b39b342743e40461f5d5b9c8f0c02268d3c4f82c177ac39b4f97ad8b4133d"),
    "resource.json": (1_565, "a91445352b3fd2f53b84106667686f1d8778ef5b4399d2c15631c7844b634ffd"),
    "run.log": (12_955, "52a3c6a0f78b293f11ec2af40d2cd23089d695f4c063bebb6a416aab0f87a063"),
}
IMAGE_ID = "sha256:de611d62db982bb833b0737a7ddd72a7cd49ab1a7dc9128669cdabae39f46df8"

MODEL_INPUT_PATHS = frozenset({
    "contract/training-recipe.v1.json",
    "contract/service-v1.json",
    "contract/MODELS.md",
    "contract/feature-schema.v1.json",
    "source/natural-train.parquet",
    "source/tmdb-masked-train.parquet",
    "source/masked-manifest.json",
    "source/views-manifest.json",
    "source/masked-review.json",
})
WORKER_RUNTIME_PATHS = frozenset({
    "implementation/service_v1_b1_spark_worker.py",
    "implementation/combination340_models.py",
    "implementation/rec046_common.py",
    "runtime/docker-image-id",
})
EXECUTION_PATHS = frozenset({
    "execution/service-v1-b1-r3-fit-recovery.md",
    "execution/local4c12g-t14400-profile.json",
    "execution/run_service_v1_b1_gbt_r3.py",
    "execution/test_service_v1_b1_gbt_runner_r3.py",
})
TRAINING_SOURCE_PATHS = MODEL_INPUT_PATHS | WORKER_RUNTIME_PATHS | EXECUTION_PATHS
SCORE_INPUT_PATHS = frozenset({"source/natural-score.parquet"})
SCORE_SOURCE_PATHS = TRAINING_SOURCE_PATHS | SCORE_INPUT_PATHS

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


def valid_sha256(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(character in "0123456789abcdef" for character in value))


def require_public_r3_contract_sealed() -> None:
    pins = {
        "r3 runner": REVIEWED_R3_RUNNER_SHA256,
        "r3 execution profile": REVIEWED_R3_PROFILE_SHA256,
        "r3 Spark output auditor": REVIEWED_R3_SPARK_AUDITOR_SHA256,
        "r3 evaluation auditor core": REVIEWED_R3_EVALUATION_AUDITOR_CORE_SHA256,
    }
    missing = [name for name, value in pins.items()
               if not valid_sha256(value) or value == EVALUATION_AUDITOR_PIN_SENTINEL]
    require(not missing,
            "PUBLIC EVALUATION BLOCKED: unsealed r3 producer contract pins: "
            + ", ".join(missing))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def evaluation_auditor_contract(path: Path) -> tuple[str, str]:
    """Hash all auditor source except its unavoidable evaluator-SHA literal.

    The source must contain exactly one top-level annotated assignment named
    ``REVIEWED_R3_EVALUATOR_SHA256`` whose value is a plain 64-hex string.
    Only that literal's 64 characters are replaced with a fixed sentinel.
    Duplicate assignments, computed values, prefixes, and multiline literals
    are rejected rather than normalized ambiguously.
    """
    path = require_unlinked_path(path, "r3 evaluation auditor")
    raw = path.read_bytes()
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("r3 evaluation auditor is not UTF-8") from error
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as error:
        raise ValueError("r3 evaluation auditor is not valid Python") from error
    matches = [
        node for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "REVIEWED_R3_EVALUATOR_SHA256"
    ]
    stores = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
        and node.id == "REVIEWED_R3_EVALUATOR_SHA256"
    ]
    require(len(matches) == 1,
            "r3 evaluation auditor evaluator pin must be one top-level annotated assignment")
    assignment = matches[0]
    require(len(stores) == 1 and stores[0] is assignment.target,
            "r3 evaluation auditor evaluator-pin binding must be unique")
    value = assignment.value  # type: ignore[attr-defined]
    require(isinstance(value, ast.Constant) and isinstance(value.value, str)
            and valid_sha256(value.value),
            "r3 evaluation auditor evaluator pin must be a literal lowercase SHA-256")
    require(value.lineno == value.end_lineno,
            "r3 evaluation auditor evaluator pin literal must be one line")
    lines = source.splitlines(keepends=True)
    line = lines[value.lineno - 1]
    segment = line[value.col_offset:value.end_col_offset]
    require(len(segment) == 66 and segment[0] in {'"', "'"}
            and segment[-1] == segment[0]
            and segment[1:-1] == value.value,
            "r3 evaluation auditor evaluator pin must be a plain string literal")
    offsets = [0]
    for current in lines:
        offsets.append(offsets[-1] + len(current))
    start = offsets[value.lineno - 1] + value.col_offset
    stop = offsets[value.end_lineno - 1] + value.end_col_offset
    normalized = (source[:start] + segment[0] + EVALUATION_AUDITOR_PIN_SENTINEL
                  + segment[-1] + source[stop:])
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest(), value.value


def normalized_evaluation_auditor_sha256(path: Path) -> str:
    return evaluation_auditor_contract(path)[0]


def verify_evaluation_auditor_binding(path: Path, evaluator: Path,
                                      expected_core_sha256: str) -> None:
    """Bind the normalized auditor behavior and its embedded evaluator pin."""
    require(valid_sha256(expected_core_sha256),
            "r3 evaluation auditor expected core SHA-256 is invalid")
    core_sha256, embedded_evaluator_sha256 = evaluation_auditor_contract(path)
    evaluator = require_unlinked_path(evaluator, "r3 evaluator implementation")
    require(core_sha256 == expected_core_sha256,
            "r3 evaluation auditor reviewed core SHA-256 drift")
    require(embedded_evaluator_sha256 == sha256_file(evaluator),
            "r3 evaluation auditor embeds a stale evaluator SHA-256")


def _lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _is_link_or_reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        if hasattr(path, "is_junction") and path.is_junction():
            return True
    except OSError:
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def require_unlinked_path(path: Path, label: str, *, directory: bool = False) -> Path:
    """Reject symlinks/junctions at every lexical component before resolving."""
    lexical = _lexical_absolute(Path(path))
    for member in (lexical, *lexical.parents):
        require(not _is_link_or_reparse(member), f"{label} linked/reparse path forbidden")
    if directory:
        require(lexical.is_dir(), f"{label} directory missing")
    else:
        require(lexical.is_file(), f"{label} regular file missing")
    return lexical.resolve()


def pin(path: Path) -> dict[str, Any]:
    path = _lexical_absolute(Path(path))
    require_unlinked_path(path, f"pinned file {path}")
    return {"bytes": int(path.stat().st_size), "sha256": sha256_file(path)}


def path_pin(path: Path, root: Path = ROOT, team_root: Path | None = None) -> dict[str, Any]:
    lexical = _lexical_absolute(Path(path))
    resolved = require_unlinked_path(lexical, f"pinned file {lexical}")
    root_resolved = Path(root).resolve()
    if team_root is None:
        # Bundle-local manifests deliberately use paths relative to their own
        # immutable directory.  Cross-bundle/source records must pass the team
        # root explicitly and use the canonical two-root namespace below.
        relative = os.path.relpath(resolved, root_resolved).replace("\\", "/")
    else:
        effective_team = Path(team_root).resolve()
        r2_plan = root_resolved / "docs/recommendation/plans/service-v1-b1-spark-runner.md"
        if resolved == r2_plan:
            relative = "ancestor/service-v1-b1-spark-runner.md"
        elif resolved.is_relative_to(effective_team):
            relative = "team/" + resolved.relative_to(effective_team).as_posix()
        elif resolved.is_relative_to(root_resolved):
            relative = "standalone/" + resolved.relative_to(root_resolved).as_posix()
        else:
            # Unit fixtures execute this reviewed evaluator from outside the
            # fixture root.  Preserve only that exact synthetic exception; the
            # production root can never emit traversal or an arbitrary alias.
            require(root_resolved != ROOT.resolve()
                    and resolved in {SCRIPT, EVALUATION_AUDITOR},
                    "pinned file outside approved canonical roots")
            relative = os.path.relpath(resolved, root_resolved).replace("\\", "/")
    return {"path": relative, **pin(resolved)}


def read_json(path: Path) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, value in items:
            require(key not in output, f"duplicate JSON key {key}: {path}")
            output[key] = value
        return output

    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(
            handle, object_pairs_hook=pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"nonfinite JSON token {value}: {path}")))


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
    require(type(expected.get("bytes")) is int and expected["bytes"] >= 0,
            f"{label} invalid pin byte type/value")
    require(valid_sha256(expected.get("sha256")),
            f"{label} invalid lowercase pin SHA-256")
    actual = pin(path)
    require(expected["bytes"] == actual["bytes"], f"{label} byte drift")
    require(expected["sha256"] == actual["sha256"], f"{label} SHA-256 drift")
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
    def safe_relative(value: str) -> bool:
        parts = value.split("/")
        return (not Path(value).is_absolute() and ":" not in value
                and all(part not in {"", ".", ".."} for part in parts))
    if normalized.startswith("standalone/"):
        relative = normalized.removeprefix("standalone/")
        require(safe_relative(relative), "unsafe standalone pinned reference path")
        return require_unlinked_path(root / relative, "standalone pinned reference")
    if normalized.startswith("team/"):
        relative = normalized.removeprefix("team/")
        require(safe_relative(relative), "unsafe team pinned reference path")
        return require_unlinked_path(team_root / relative, "team pinned reference")
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
        "execution/service-v1-b1-r3-fit-recovery.md": root / "docs/recommendation/plans/service-v1-b1-r3-fit-recovery.md",
        "execution/local4c12g-t14400-profile.json": root / "docs/recommendation/plans/service-v1-b1-r3-local4c12g-t14400-profile.json",
        "execution/run_service_v1_b1_gbt_r3.py": root / "scripts/run_service_v1_b1_gbt_r3.py",
        "execution/test_service_v1_b1_gbt_runner_r3.py": root / "tests/test_service_v1_b1_gbt_runner_r3.py",
        "implementation/service_v1_b1_spark_worker.py": root / "scripts/service_v1_b1_spark_worker.py",
        "implementation/combination340_models.py": root / "scripts/combination340_models.py",
        "implementation/rec046_common.py": root / "scripts/rec046_common.py",
        "ancestor/service-v1-b1-spark-runner.md": root / "docs/recommendation/plans/service-v1-b1-spark-runner.md",
    }
    if normalized in implementation_aliases:
        return require_unlinked_path(
            implementation_aliases[normalized], f"pinned alias {normalized}")
    candidate = Path(raw)
    require(not candidate.is_absolute(), "absolute pinned reference path forbidden")
    contains_traversal = any(part == ".." for part in normalized.split("/"))
    if contains_traversal:
        # Synthetic tests execute the real evaluator from outside their tiny
        # fixture root.  That one exact implementation path is the only
        # non-production traversal exception.
        root_candidate = (root / candidate).resolve()
        require(root.resolve() != ROOT.resolve()
                and root_candidate in {SCRIPT, EVALUATION_AUDITOR},
                "pinned reference traversal forbidden")
        return require_unlinked_path(root_candidate, "synthetic evaluator reference")
    require(safe_relative(normalized), "unsafe pinned reference path")
    root_candidate = (root / candidate).resolve()
    owner_candidate = (owner.parent / candidate).resolve()
    candidates = (root / candidate, owner.parent / candidate)
    matches = [require_unlinked_path(p, "pinned reference")
               for p in candidates if os.path.lexists(p)]
    unique = list(dict.fromkeys(matches))
    require(len(unique) == 1, f"pinned reference path is missing or ambiguous: {raw}")
    resolved = unique[0]
    require(resolved.is_relative_to(root.resolve())
            or resolved.is_relative_to(team_root.resolve()),
            "pinned reference escapes approved roots")
    return resolved


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
    spark_auditor: Path
    evaluation_auditor: Path


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
        score_manifest=_lexical_absolute(Path(score_manifest)),
        score_review=_lexical_absolute(Path(score_review)),
        spark_auditor=SPARK_AUDITOR,
        evaluation_auditor=EVALUATION_AUDITOR,
    )


def verify_fixed_nonlabel_inputs(paths: EvaluationPaths, expected_plan_sha256: str,
                                 fixed_pins: Mapping[str, tuple[int, str]] = SOURCE_PINS) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    if paths.plan.resolve() == PLAN.resolve():
        require(expected_plan_sha256.lower() == PLAN_SHA256,
                "production plan pin differs from independently reviewed plan")
    verify_sha(paths.plan, expected_plan_sha256, "reviewed design plan")
    plan_record = path_pin(paths.plan, paths.root, paths.team_root)
    records[str(plan_record["path"])] = plan_record
    for name in ("artifact_manifest", "evaluation_contract", "contexts", "catalog", "roles",
                 "ratings",
                 "metadata", "score_axis", "b0_predictions", "b0_seal", "evaluation_seal"):
        path = getattr(paths, name)
        size, digest = fixed_pins[name]
        verify_pin(path, {"bytes": size, "sha256": digest}, name)
        record = path_pin(path, paths.root, paths.team_root)
        records[str(record["path"])] = record
    return records


def _bundle_inventory(directory: Path) -> set[str]:
    lexical = _lexical_absolute(directory)
    require_unlinked_path(lexical, f"bundle {lexical}", directory=True)
    result: set[str] = set()
    for path in lexical.rglob("*"):
        require(not _is_link_or_reparse(path),
                f"bundle member linked/reparse path forbidden: {path}")
        if path.is_file():
            require_unlinked_path(path, f"bundle member {path}")
            result.add(path.relative_to(lexical).as_posix())
        else:
            require(path.is_dir(), f"bundle member is not file/directory: {path}")
    return result


def _manifest_file_map(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    files = manifest.get("files")
    if files is None:
        files = manifest.get("artifacts")
    require(isinstance(files, Mapping), "manifest has no file inventory")
    return files


def verify_manifest_inventory(bundle: Path, manifest: Mapping[str, Any], required: set[str]) -> dict[str, dict[str, Any]]:
    require_unlinked_path(bundle, f"bundle {bundle}", directory=True)
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


def verify_phase_namespace_clean(bundle: Path) -> None:
    """Reject mixed success/failure state and unpublished staging residue."""
    bundle = require_unlinked_path(bundle, f"phase bundle {bundle}", directory=True)
    parent = bundle.parent
    failure = bundle.with_name(bundle.name + "-failure.json")
    review = bundle.with_name(bundle.name + "-result-review.json")
    require(not os.path.lexists(failure),
            f"success bundle coexists with failure evidence: {failure}")
    stale = [
        path for path in parent.iterdir()
        if path.name.startswith((
            "." + bundle.name + ".tmp-",
            "." + review.name + ".tmp-",
            "." + failure.name + ".tmp-",
        )) or path == _namespace_claim_path(bundle)
        or (path.name.startswith("." + bundle.name + ".")
               and "-scratch-" in path.name)
    ]
    require(not stale, f"phase namespace contains stale temp/scratch state: {bundle.name}")


def _target_pin(review: Mapping[str, Any], key: str, actual: Path, *, root: Path,
                team_root: Path, review_path: Path, phase: str) -> dict[str, Any]:
    target = review.get("target")
    require(isinstance(target, Mapping) and isinstance(target.get(key), Mapping),
            f"{phase} review missing target.{key}")
    record = target[key]
    expected_fields = {"path", "bytes", "sha256"}
    if key in {"input_lock", "score_input_lock"}:
        expected_fields |= {
            "modelInputSetSha256", "workerRuntimeSetSha256",
            "executionSetSha256", "controlReferenceSetSha256", "inputSetSha256",
        }
        if key == "score_input_lock":
            expected_fields.add("scoreInputSetSha256")
    require(set(record) == expected_fields,
            f"{phase} review target.{key} field set drift")
    require(isinstance(record.get("path"), str), f"review target.{key} has no path")
    resolved = resolve_record_path(record, root=root, team_root=team_root, owner=review_path)
    require(resolved == actual.resolve(), f"{phase} review target.{key} path mismatch")
    verify_pin(actual, record, f"{phase} review target.{key}")
    if key == "recovery_reference":
        checks = review.get("checks")
        recovery = checks.get("recovery") if isinstance(checks, Mapping) else None
        reference = recovery.get("reference") if isinstance(recovery, Mapping) else None
        require(reference == {"bytes": record["bytes"], "sha256": record["sha256"]},
                f"{phase} review recovery check/target pin drift")
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
        require(valid_sha256(digest),
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


def _remember_file(output: dict[str, dict[str, Any]], path: Path, root: Path,
                   team_root: Path | None = None) -> None:
    record = path_pin(path, root, team_root)
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


def verify_r3_grouped_lock(lock: Mapping[str, Any], *, phase: str, label: str,
                           canonical_contract: bool = True
                           ) -> tuple[list[Any], list[Any], list[Any], list[Any]]:
    """Validate the four disjoint r3 record groups and every canonical digest."""
    expected_fields = {
        "schemaVersion", "phase", "runId", "profileId", "modelInputRecords",
        "workerRuntimeRecords", "executionRecords", "controlReferences",
        "modelInputSetSha256", "workerRuntimeSetSha256", "executionSetSha256",
        "controlReferenceSetSha256", "inputSetSha256",
        "r2TrainingSourceSetSha256", "evaluationTargetsRead",
    }
    require(set(lock) == expected_fields, f"{label} field set drift")
    expected_schema = ("feelm-service-v1-b1-score-input-lock/2" if phase == "score"
                       else "feelm-service-v1-b1-input-lock/2")
    require(lock.get("schemaVersion") == expected_schema
            and lock.get("phase") == phase and lock.get("runId") == RUN_ID
            and lock.get("profileId") == PROFILE_ID,
            f"{label} identity/schema drift")
    require(lock.get("evaluationTargetsRead") is False,
            f"{label} permits evaluation target access")
    groups: list[list[Any]] = []
    for key in ("modelInputRecords", "workerRuntimeRecords", "executionRecords",
                "controlReferences"):
        value = lock.get(key)
        require(isinstance(value, list), f"{label} {key} missing")
        canonical_record_set_digest(value)
        groups.append(value)
    models, runtime, execution, controls = groups
    require(lock.get("modelInputSetSha256") == canonical_record_set_digest(models),
            f"{label} model-input digest drift")
    require(lock.get("workerRuntimeSetSha256") == canonical_record_set_digest(runtime),
            f"{label} worker-runtime digest drift")
    require(lock.get("executionSetSha256") == canonical_record_set_digest(execution),
            f"{label} execution digest drift")
    require(lock.get("controlReferenceSetSha256") == canonical_record_set_digest(controls),
            f"{label} control digest drift")
    combined = [*models, *runtime, *execution, *controls]
    require(len({str(record.get("path")) for record in combined}) == len(combined),
            f"{label} record groups overlap")
    require(lock.get("inputSetSha256") == canonical_record_set_digest(combined),
            f"{label} full input digest drift")
    if canonical_contract:
        require(lock.get("r2TrainingSourceSetSha256") == R2_TRAINING_SOURCE_SET_SHA256,
                f"{label} r2 historical digest drift")
    return models, runtime, execution, controls


def verify_r3_score_lock(lock: Mapping[str, Any], *, label: str,
                         canonical_contract: bool = True
                         ) -> tuple[list[Any], list[Any], list[Any], list[Any], list[Any]]:
    """Validate the score-only group without duplicating a common model input."""
    expected_fields = {
        "schemaVersion", "phase", "runId", "profileId", "modelInputRecords",
        "scoreInputRecords", "workerRuntimeRecords", "executionRecords",
        "controlReferences", "modelInputSetSha256", "scoreInputSetSha256",
        "workerRuntimeSetSha256", "executionSetSha256",
        "controlReferenceSetSha256", "inputSetSha256",
        "r2TrainingSourceSetSha256", "evaluationTargetsRead",
    }
    require(set(lock) == expected_fields, f"{label} field set drift")
    require(lock.get("schemaVersion") == "feelm-service-v1-b1-score-input-lock/2"
            and lock.get("phase") == "score" and lock.get("runId") == RUN_ID
            and lock.get("profileId") == PROFILE_ID
            and lock.get("evaluationTargetsRead") is False,
            f"{label} identity/schema drift")
    groups: list[list[Any]] = []
    for key in ("modelInputRecords", "scoreInputRecords", "workerRuntimeRecords",
                "executionRecords", "controlReferences"):
        records = lock.get(key)
        require(isinstance(records, list), f"{label} {key} missing")
        canonical_record_set_digest(records)
        groups.append(records)
    models, score_inputs, runtime, execution, controls = groups
    digests = {
        "modelInputSetSha256": canonical_record_set_digest(models),
        "scoreInputSetSha256": canonical_record_set_digest(score_inputs),
        "workerRuntimeSetSha256": canonical_record_set_digest(runtime),
        "executionSetSha256": canonical_record_set_digest(execution),
        "controlReferenceSetSha256": canonical_record_set_digest(controls),
    }
    for key, expected in digests.items():
        require(lock.get(key) == expected, f"{label} {key} drift")
    combined = [*models, *score_inputs, *runtime, *execution, *controls]
    require(len({str(record.get("path")) for record in combined}) == len(combined),
            f"{label} record groups overlap")
    require(lock.get("inputSetSha256") == canonical_record_set_digest(combined),
            f"{label} full input digest drift")
    if canonical_contract:
        require(lock.get("modelInputSetSha256") == MODEL_INPUT_SET_SHA256
                and lock.get("r2TrainingSourceSetSha256")
                == R2_TRAINING_SOURCE_SET_SHA256,
                f"{label} canonical model/history digest drift")
    return models, score_inputs, runtime, execution, controls


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
        require(set(record) == {"path", "bytes", "sha256"},
                f"{label} record fields drift: {logical}")
        actual = _verified_record(record, paths=paths, owner=owner,
                                  label=f"{label} {logical}")
        require(actual.is_file() and not actual.is_symlink(),
                f"{label} is not a regular file: {logical}")
        require(actual not in resolved.values(), f"{label} aliases one physical file twice")
        resolved[logical] = actual
        _remember_file(files, actual, paths.root, paths.team_root)
    if "source/tmdb-masked-train.parquet" in resolved:
        masked = resolved["source/tmdb-masked-train.parquet"].parent
        require_unlinked_path(masked, f"{label} masked bundle", directory=True)
        require(_bundle_inventory(masked)
                == {"manifest.json", "tmdb-masked-rh230.parquet", "views-manifest.json"},
                f"{label} masked bundle file set drift")
    return resolved, files


def verify_r3_training_groups(
    model_records: Sequence[Any], runtime_records: Sequence[Any],
    execution_records: Sequence[Any], *, paths: EvaluationPaths, owner: Path,
    label: str, model_expected_paths: frozenset[str] = MODEL_INPUT_PATHS,
    expected_model_digest: str | None = MODEL_INPUT_SET_SHA256,
) -> tuple[dict[str, Path], dict[str, dict[str, Any]]]:
    """Rehash the exact r3 model/runtime/execution closure."""
    model_paths, model_files = _verify_locked_sources(
        model_records, expected_paths=model_expected_paths, paths=paths,
        owner=owner, label=f"{label} model inputs")
    runtime_paths, runtime_files = _verify_locked_sources(
        runtime_records, expected_paths=WORKER_RUNTIME_PATHS, paths=paths,
        owner=owner, label=f"{label} worker runtime")
    execution_paths, execution_files = _verify_locked_sources(
        execution_records, expected_paths=EXECUTION_PATHS, paths=paths,
        owner=owner, label=f"{label} execution")
    canonical_contract = paths.root.resolve() == ROOT.resolve()
    if expected_model_digest is not None and canonical_contract:
        require(canonical_record_set_digest(model_records) == expected_model_digest,
                f"{label} canonical model-input set drift")
    if canonical_contract:
        require(canonical_record_set_digest(runtime_records) == WORKER_RUNTIME_SET_SHA256,
                f"{label} canonical worker-runtime set drift")
    profile = next(record for record in execution_records
                   if record.get("path") ==
                   "execution/local4c12g-t14400-profile.json")
    runner = next(record for record in execution_records
                  if record.get("path") == "execution/run_service_v1_b1_gbt_r3.py")
    if canonical_contract:
        require(profile.get("sha256") == REVIEWED_R3_PROFILE_SHA256,
                f"{label} profile is not the sealed r3 contract")
        require(runner.get("sha256") == REVIEWED_R3_RUNNER_SHA256,
                f"{label} runner is not the sealed r3 producer")
    profile_payload = read_json(execution_paths[
        "execution/local4c12g-t14400-profile.json"])
    require(profile_payload.get("schemaVersion")
            == "feelm-service-v1-b1-execution-profile/1"
            and profile_payload.get("profileId") == PROFILE_ID
            and profile_payload.get("runId") == RUN_ID
            and profile_payload.get("singleAttempt") is True
            and profile_payload.get("automaticRetry") is False,
            f"{label} execution profile identity/retry drift")
    docker = profile_payload.get("docker")
    spark = profile_payload.get("spark")
    timeouts = profile_payload.get("timeoutsSeconds")
    model = profile_payload.get("modelContract")
    require(isinstance(docker, Mapping) and docker.get("cpus") == "4"
            and docker.get("memory") == "12g" and docker.get("memorySwap") == "12g"
            and docker.get("maxMemoryBytes") == 12 * 1024**3
            and docker.get("network") == "none",
            f"{label} execution profile Docker drift")
    require(isinstance(spark, Mapping) and spark.get("master") == "local[4]"
            and spark.get("driverMemory") == "8g" and spark.get("shufflePartitions") == 8
            and spark.get("adaptiveExecution") is False,
            f"{label} execution profile Spark drift")
    require(isinstance(timeouts, Mapping)
            and set(timeouts) == {"preflightDryRun", "preflightFull", "fit", "score"}
            and set(timeouts.values()) == {14_400},
            f"{label} execution profile timeout drift")
    require(isinstance(model, Mapping) and model.get("sourceRows") == TRAINING_ROWS
            and model.get("logicalRows") == 4 * TRAINING_ROWS
            and model.get("scoreRows") == SCORE_ROWS and model.get("featureCount") == 230
            and model.get("partitions") == 8 and model.get("seed") == 339
            and model.get("trees") == 120
            and model.get("modelInputSetSha256") == canonical_record_set_digest(model_records)
            and model.get("workerRuntimeSetSha256") == canonical_record_set_digest(runtime_records),
            f"{label} execution profile model contract drift")
    resolved: dict[str, Path] = {}
    files: dict[str, dict[str, Any]] = {}
    for current_paths, current_files in (
        (model_paths, model_files), (runtime_paths, runtime_files),
        (execution_paths, execution_files),
    ):
        require(not set(resolved).intersection(current_paths), f"{label} logical group overlap")
        resolved.update(current_paths)
        _merge_verified_files(files, current_files, label)
    return resolved, files


def verify_r3_score_groups(
    model_records: Sequence[Any], score_input_records: Sequence[Any],
    runtime_records: Sequence[Any],
    execution_records: Sequence[Any], *, paths: EvaluationPaths, owner: Path,
    label: str,
) -> tuple[dict[str, Path], dict[str, dict[str, Any]]]:
    model_paths, model_files = _verify_locked_sources(
        model_records, expected_paths=MODEL_INPUT_PATHS, paths=paths,
        owner=owner, label=f"{label} common model inputs")
    score_paths, score_files = _verify_locked_sources(
        score_input_records, expected_paths=SCORE_INPUT_PATHS, paths=paths,
        owner=owner, label=f"{label} score-only inputs")
    runtime_paths, runtime_files = _verify_locked_sources(
        runtime_records, expected_paths=WORKER_RUNTIME_PATHS, paths=paths,
        owner=owner, label=f"{label} worker runtime")
    execution_paths, execution_files = _verify_locked_sources(
        execution_records, expected_paths=EXECUTION_PATHS, paths=paths,
        owner=owner, label=f"{label} execution")
    canonical_contract = paths.root.resolve() == ROOT.resolve()
    if canonical_contract:
        require(canonical_record_set_digest(model_records) == MODEL_INPUT_SET_SHA256,
                f"{label} canonical model-input set drift")
        require(canonical_record_set_digest(runtime_records) == WORKER_RUNTIME_SET_SHA256,
                f"{label} canonical worker-runtime set drift")
    profile = next(record for record in execution_records
                   if record.get("path") ==
                   "execution/local4c12g-t14400-profile.json")
    runner = next(record for record in execution_records
                  if record.get("path") == "execution/run_service_v1_b1_gbt_r3.py")
    if canonical_contract:
        require(profile.get("sha256") == REVIEWED_R3_PROFILE_SHA256,
                f"{label} profile is not sealed")
        require(runner.get("sha256") == REVIEWED_R3_RUNNER_SHA256,
                f"{label} runner is not sealed")
    profile_payload = read_json(execution_paths[
        "execution/local4c12g-t14400-profile.json"])
    require(profile_payload.get("schemaVersion")
            == "feelm-service-v1-b1-execution-profile/1"
            and profile_payload.get("profileId") == PROFILE_ID
            and profile_payload.get("runId") == RUN_ID
            and profile_payload.get("singleAttempt") is True
            and profile_payload.get("automaticRetry") is False,
            f"{label} profile identity/retry drift")
    resolved: dict[str, Path] = {}
    files: dict[str, dict[str, Any]] = {}
    for current_paths, current_files in (
        (model_paths, model_files), (score_paths, score_files),
        (runtime_paths, runtime_files),
        (execution_paths, execution_files),
    ):
        require(not set(resolved).intersection(current_paths), f"{label} logical group overlap")
        resolved.update(current_paths)
        _merge_verified_files(files, current_files, label)
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
        require(set(record) == {"path", "bytes", "sha256"},
                f"{label} record fields drift")
        actual = _verified_record(record, paths=paths, owner=owner, label=label)
        require(actual.is_file() and not actual.is_symlink(),
                f"{label} is not a regular file: {actual}")
        require(actual not in observed, f"{label} aliases one physical file twice")
        observed[actual] = record
        _remember_file(files, actual, paths.root, paths.team_root)
    require(set(observed) == expected, f"{label} file set drift")
    return files


def _logical_dependency_path(path: Path, paths: EvaluationPaths) -> str:
    resolved = path.resolve()
    for prefix, root in (("standalone", paths.root), ("team", paths.team_root)):
        try:
            return prefix + "/" + resolved.relative_to(root.resolve()).as_posix()
        except ValueError:
            continue
    raise ValueError(f"review dependency is outside the two roots: {path}")


def _require_canonical_spark_phase_bundle(paths: EvaluationPaths, bundle: Path,
                                          phase: str) -> None:
    require(phase in {"preflight", "fit", "score"}, "unknown r3 Spark phase")
    require_unlinked_path(bundle, f"r3 {phase} bundle", directory=True)
    if paths.root.resolve() == ROOT.resolve():
        expected = (ROOT / "outputs/recommendation-evidence/"
                    "service-v1-pretraining-20260913" / f"{RUN_ID}-{phase}")
        require(_lexical_absolute(bundle) == _lexical_absolute(expected),
                f"r3 {phase} bundle noncanonical lexical path")


def _validate_spark_review_checks(checks: Mapping[str, Any], phase: str, *,
                                  canonical: bool) -> None:
    recovery = checks.get("recovery")
    expected_failure_path = (
        "standalone/outputs/recommendation-evidence/"
        f"service-v1-pretraining-20260913/{R2_RUN_ID}-fit-failure.json")
    require(isinstance(recovery, Mapping)
            and set(recovery) == {"r2FitFailure", "reference"},
            f"{phase} independent review recovery check field set drift")
    failure = recovery.get("r2FitFailure")
    reference = recovery.get("reference")
    require(isinstance(failure, Mapping)
            and set(failure) == {"path", "bytes", "sha256"}
            and failure.get("path") == expected_failure_path
            and type(failure.get("bytes")) is int and failure["bytes"] > 0
            and valid_sha256(failure.get("sha256")),
            f"{phase} independent review r2 failure evidence drift")
    if canonical:
        require((failure["bytes"], failure["sha256"])
                == (R2_FIT_FAILURE_BYTES, R2_FIT_FAILURE_SHA256),
                f"{phase} independent review r2 failure canonical pin drift")
    require(isinstance(reference, Mapping)
            and set(reference) == {"bytes", "sha256"}
            and type(reference.get("bytes")) is int and reference["bytes"] > 0
            and valid_sha256(reference.get("sha256")),
            f"{phase} independent review recovery reference evidence drift")

    if phase in {"preflight", "fit"}:
        identity = checks.get("identity")
        require(isinstance(identity, Mapping)
                and set(identity) == {
                    "sourceRows", "logicalRows", "partitions", "allSourceRowsRead",
                    "maskedFormulaRecomputed", "maskedArtifactPinsChecked",
                }
                and type(identity.get("sourceRows")) is int and identity["sourceRows"] > 0
                and identity.get("logicalRows") == 4 * identity["sourceRows"]
                and identity.get("partitions") == 8
                and identity.get("allSourceRowsRead") is True
                and identity.get("maskedFormulaRecomputed") is False
                and identity.get("maskedArtifactPinsChecked") is True,
                f"{phase} independent review identity evidence drift")
        if canonical:
            require(identity["sourceRows"] == TRAINING_ROWS,
                    f"{phase} independent review identity canonical census drift")
    if phase == "fit":
        fit = checks.get("fit")
        require(isinstance(fit, Mapping)
                and set(fit) == {
                    "treeCount", "thresholdParity",
                    "weightedTrainRmseIndependentlyRecomputed",
                    "trainingQualityNotAnAcceptanceMetric",
                }
                and type(fit.get("treeCount")) is int and fit["treeCount"] > 0
                and fit.get("weightedTrainRmseIndependentlyRecomputed") is False
                and fit.get("trainingQualityNotAnAcceptanceMetric") is True,
                "fit independent review model evidence drift")
        if canonical:
            require(fit["treeCount"] == 120,
                    "fit independent review canonical tree count drift")
        parity = fit.get("thresholdParity")
        require(isinstance(parity, Mapping)
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
    if phase == "score":
        predictions = checks.get("predictions")
        require(isinstance(predictions, Mapping)
                and set(predictions) == {
                    "rows", "uniqueRowIds", "strictlyIncreasing", "uidParity",
                    "finite", "maxAbsError", "labelRead", "singlePhysicalFile",
                }
                and type(predictions.get("rows")) is int and predictions["rows"] > 0
                and predictions.get("uniqueRowIds") == predictions["rows"]
                and predictions.get("strictlyIncreasing") is True
                and predictions.get("uidParity") is True
                and predictions.get("finite") is True
                and type(predictions.get("maxAbsError")) in (int, float)
                and math.isfinite(float(predictions["maxAbsError"]))
                and 0 <= float(predictions["maxAbsError"]) <= 1e-6
                and predictions.get("labelRead") is False
                and predictions.get("singlePhysicalFile") is True,
                "score independent review prediction evidence drift")
        if canonical:
            require(predictions["rows"] == SCORE_ROWS,
                    "score independent review canonical prediction census drift")


def _require_review_envelope(review: Mapping[str, Any], phase: str, *,
                             paths: EvaluationPaths, review_path: Path
                             ) -> tuple[Mapping[str, Any], dict[str, dict[str, Any]]]:
    expected_fields = {
        "schemaVersion", "runId", "profileId", "phase", "status", "createdAt",
        "target", "reviewer", "dependencyFingerprint", "checks",
        "evaluationTargetsRead", "modelFitPerformed", "readyForService", "scope",
    }
    require(set(review) == expected_fields, f"{phase} independent review field set drift")
    require(review.get("schemaVersion") == "feelm-service-v1-b1-r3-result-review/1"
            and review.get("runId") == RUN_ID and review.get("profileId") == PROFILE_ID,
            f"{phase} independent review identity/schema drift")
    require(review.get("status") == "PASS", f"{phase} independent review must PASS")
    require(review.get("phase") == phase, f"{phase} independent review phase drift")
    require(review.get("evaluationTargetsRead") is False
            and review.get("modelFitPerformed") is False
            and review.get("readyForService") is False,
            f"{phase} independent review authority drift")
    created_at = review.get("createdAt")
    require(isinstance(created_at, str)
            and isinstance(review.get("checks"), Mapping)
            and review.get("scope") == (
                "Local immutable bundle integrity and specified numerical parity; "
                "no service acceptance or model quality verdict."),
            f"{phase} independent review safety envelope drift")
    try:
        parsed_created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{phase} independent review createdAt drift") from error
    require(parsed_created_at.tzinfo is not None,
            f"{phase} independent review createdAt lacks timezone")
    checks = review["checks"]
    expected_check_keys = {
        "preflight": {"resource", "recovery", "identity"},
        "fit": {"resource", "recovery", "parentChain", "identity", "fit"},
        "score": {"resource", "recovery", "parentChain", "predictions"},
    }[phase]
    require(set(checks) == expected_check_keys,
            f"{phase} independent review check set drift")
    resource_check = checks.get("resource")
    require(isinstance(resource_check, Mapping)
            and set(resource_check) == {
                "stages", "peakBytes", "cleanupConfirmed", "labelMountAbsent",
                "measurementIndependentlyObserved",
                "measurementCheckedAgainstPinnedWorkerLog",
            }
            and resource_check.get("stages") == (2 if phase == "preflight" else 1)
            and type(resource_check.get("peakBytes")) is int
            and 0 < resource_check["peakBytes"] < 12 * 1024**3
            and resource_check.get("cleanupConfirmed") is True
            and resource_check.get("labelMountAbsent") is True
            and resource_check.get("measurementIndependentlyObserved") is False
            and resource_check.get("measurementCheckedAgainstPinnedWorkerLog") is True,
            f"{phase} independent review resource check drift")
    _validate_spark_review_checks(
        checks, phase, canonical=paths.root.resolve() == ROOT.resolve())
    if phase != "preflight":
        require(checks.get("parentChain") == "PASS",
                f"{phase} independent review parent chain drift")

    auditor_path = require_unlinked_path(
        paths.spark_auditor, f"{phase} independent Spark auditor")
    auditor_pin = pin(auditor_path)
    if paths.root.resolve() == ROOT.resolve():
        require(auditor_path == SPARK_AUDITOR.resolve(),
                f"{phase} review uses a noncanonical r3 auditor path")
        require(auditor_pin.get("sha256") == REVIEWED_R3_SPARK_AUDITOR_SHA256,
                f"{phase} review auditor is not the sealed implementation")
    reviewer = review.get("reviewer")
    require(isinstance(reviewer, Mapping)
            and set(reviewer) == {"implementation", "independentFromRunner"}
            and reviewer.get("implementation") == auditor_pin
            and reviewer.get("independentFromRunner") is True,
            f"{phase} review is not pinned to the independent r3 auditor")

    dependency = review.get("dependencyFingerprint")
    require(isinstance(dependency, Mapping)
            and set(dependency) == {"files", "bundleInventories",
                                    "auditorImplementation", "dockerImageId",
                                    "runId", "profileId"}
            and dependency.get("auditorImplementation") == auditor_pin
            and dependency.get("dockerImageId") == IMAGE_ID
            and dependency.get("runId") == RUN_ID
            and dependency.get("profileId") == PROFILE_ID,
            f"{phase} review dependency envelope drift")
    dependency_files = dependency.get("files")
    dependency_bundles = dependency.get("bundleInventories")
    require(isinstance(dependency_files, Mapping)
            and isinstance(dependency_bundles, Mapping),
            f"{phase} review dependency inventories missing")
    source_names = set(TRAINING_SOURCE_PATHS)
    if phase == "score":
        source_names |= set(SCORE_INPUT_PATHS)
    source_names.discard("runtime/docker-image-id")
    expected_dependency_paths: dict[str, Path] = {}
    for logical_source in sorted(source_names):
        physical = resolve_record_path(
            {"path": logical_source}, root=paths.root,
            team_root=paths.team_root, owner=review_path)
        expected_dependency_paths[_logical_dependency_path(physical, paths)] = physical
    output_parent = (paths.root
                     / "outputs/recommendation-evidence/service-v1-pretraining-20260913")
    fixed_dependencies = [
        output_parent / f"{R2_RUN_ID}-preflight-result-review.json",
        output_parent / f"{R2_RUN_ID}-fit-failure.json",
        paths.root / "scripts/run_service_v1_b1_gbt.py",
    ]
    for physical in fixed_dependencies:
        expected_dependency_paths[_logical_dependency_path(physical, paths)] = physical
    r2_plan_path = paths.root / "docs/recommendation/plans/service-v1-b1-spark-runner.md"
    expected_dependency_paths[
        "ancestor/service-v1-b1-spark-runner.md"] = r2_plan_path
    prior_phases = [] if phase == "preflight" else (
        ["preflight"] if phase == "fit" else ["preflight", "fit"])
    for prior_phase in prior_phases:
        physical = output_parent / f"{RUN_ID}-{prior_phase}-result-review.json"
        expected_dependency_paths[_logical_dependency_path(physical, paths)] = physical
    require(set(dependency_files) == set(expected_dependency_paths),
            f"{phase} review dependency file set drift")
    for logical, physical in expected_dependency_paths.items():
        require(dependency_files.get(logical) == pin(physical),
                f"{phase} review dependency exact pin drift: {logical}")
    verified: dict[str, dict[str, Any]] = {}
    _remember_file(verified, auditor_path, paths.root, paths.team_root)
    for logical, expected in dependency_files.items():
        require(isinstance(logical, str) and isinstance(expected, Mapping)
                and set(expected) == {"bytes", "sha256"},
                f"{phase} review dependency file record drift")
        actual = resolve_record_path({"path": logical, **dict(expected)},
                                     root=paths.root, team_root=paths.team_root,
                                     owner=review_path)
        require(actual.is_file() and not actual.is_symlink(),
                f"{phase} review dependency is missing/linked: {logical}")
        verify_pin(actual, expected, f"{phase} review dependency {logical}")
        _remember_file(verified, actual, paths.root, paths.team_root)

    phases = ["preflight"] if phase == "preflight" else (
        ["preflight", "fit"] if phase == "fit" else ["preflight", "fit", "score"])
    expected_bundle_paths = [output_parent / f"{R2_RUN_ID}-preflight"] + [
        output_parent / f"{RUN_ID}-{current}" for current in phases]
    expected_bundle_keys = {_logical_dependency_path(path, paths)
                            for path in expected_bundle_paths}
    require(set(dependency_bundles) == expected_bundle_keys,
            f"{phase} review dependency bundle set drift")
    for bundle_path in expected_bundle_paths:
        logical = _logical_dependency_path(bundle_path, paths)
        expected_inventory = dependency_bundles[logical]
        require(isinstance(expected_inventory, Mapping),
                f"{phase} review dependency inventory missing: {logical}")
        require_unlinked_path(bundle_path,
                              f"{phase} review dependency bundle {logical}", directory=True)
        require(isinstance(expected_inventory, Mapping),
                f"{phase} review dependency bundle missing/linked: {logical}")
        actual_names = _bundle_inventory(bundle_path)
        require(actual_names == set(expected_inventory),
                f"{phase} review dependency bundle inventory drift: {logical}")
        for name, expected in expected_inventory.items():
            require(isinstance(name, str) and isinstance(expected, Mapping)
                    and set(expected) == {"bytes", "sha256"},
                    f"{phase} review dependency bundle record drift: {logical}/{name}")
            verify_pin(bundle_path / name, expected,
                       f"{phase} review dependency bundle {logical}/{name}")
            _remember_file(verified, bundle_path / name, paths.root, paths.team_root)
    target = review.get("target")
    require(isinstance(target, Mapping), f"{phase} review target map missing")
    return target, verified


def _merge_verified_files(destination: dict[str, dict[str, Any]],
                          incoming: Mapping[str, Mapping[str, Any]], label: str) -> None:
    for key, record in incoming.items():
        require(key not in destination or destination[key] == record,
                f"{label} source collision: {key}")
        destination[key] = dict(record)


def verify_r2_recovery_ancestry(paths: EvaluationPaths, recovery_path: Path,
                                recovery: Mapping[str, Any],
                                *, outer_record: Mapping[str, Any],
                                worker_record: Mapping[str, Any],
                                profile_record: Mapping[str, Any],
                                training_recipe_record: Mapping[str, Any],
                                model_input_digest: str,
                                worker_runtime_digest: str,
                                execution_digest: str
                                ) -> dict[str, dict[str, Any]]:
    """Rehash the immutable r2 success/failure evidence embedded by r3."""
    required = {
        "schemaVersion", "status", "runId", "profileId", "r2RunId",
        "r2PreflightManifest", "r2PreflightReview", "r2PreflightBundleInventory",
        "r2PreflightDigests", "r2FitFailure", "r2FitFailureFacts",
        "r2OuterRunner", "r2Plan", "sparkWorker", "outerRunner",
        "executionProfile", "digestComparison", "modelFitPerformedByPreflight",
        "deploymentAuthorized",
    }
    require(set(recovery) == required, "r3 recovery reference field set drift")
    require(recovery.get("schemaVersion")
            == "feelm-service-v1-b1-r3-recovery-reference/1"
            and recovery.get("status") == "R2_ANCESTRY_VERIFIED"
            and recovery.get("runId") == RUN_ID
            and recovery.get("profileId") == PROFILE_ID
            and recovery.get("r2RunId") == R2_RUN_ID
            and recovery.get("modelFitPerformedByPreflight") is False
            and recovery.get("deploymentAuthorized") is False,
            "r3 recovery reference identity/status drift")
    for key, expected in (("outerRunner", outer_record), ("sparkWorker", worker_record),
                          ("executionProfile", profile_record)):
        require(recovery.get(key) == expected, f"r3 recovery {key} drift")
    canonical_contract = paths.root.resolve() == ROOT.resolve()
    comparison = recovery.get("digestComparison")
    require(isinstance(comparison, Mapping)
            and set(comparison) == {
                "r2HistoricalTrainingSourceSetSha256", "modelInputSetSha256",
                "workerRuntimeSetSha256", "executionSetSha256",
                "trainingRecipe", "sourceRows", "logicalRows",
                "modelInputUnchanged", "workerRuntimeUnchanged", "partitionCount", "seed",
            }
            and valid_sha256(comparison.get("r2HistoricalTrainingSourceSetSha256"))
            and comparison.get("modelInputSetSha256") == model_input_digest
            and comparison.get("workerRuntimeSetSha256") == worker_runtime_digest
            and comparison.get("executionSetSha256") == execution_digest
            and comparison.get("trainingRecipe") == training_recipe_record
            and comparison.get("sourceRows") == TRAINING_ROWS
            and comparison.get("logicalRows") == 4 * TRAINING_ROWS
            and comparison.get("modelInputUnchanged") is True
            and comparison.get("workerRuntimeUnchanged") is True
            and comparison.get("partitionCount") == 8 and comparison.get("seed") == 339,
            "r3 recovery digest comparison drift")
    if canonical_contract:
        require(comparison.get("r2HistoricalTrainingSourceSetSha256")
                == R2_TRAINING_SOURCE_SET_SHA256
                and model_input_digest == MODEL_INPUT_SET_SHA256
                and worker_runtime_digest == WORKER_RUNTIME_SET_SHA256,
                "r3 recovery canonical digest comparison drift")

    base = paths.root / "outputs/recommendation-evidence/service-v1-pretraining-20260913"
    r2_bundle = require_unlinked_path(
        base / f"{R2_RUN_ID}-preflight", "r2 preflight bundle", directory=True)
    entries = list(r2_bundle.iterdir())
    require(all(entry.is_file() and not entry.is_symlink() for entry in entries)
            and {entry.name for entry in entries} == set(R2_PREFLIGHT_INVENTORY),
            "r2 preflight exact physical inventory drift")
    embedded_inventory = recovery.get("r2PreflightBundleInventory")
    require(isinstance(embedded_inventory, list)
            and all(isinstance(record, Mapping) for record in embedded_inventory),
            "r3 recovery r2 preflight inventory missing")
    embedded_by_name = {
        Path(str(record.get("path", ""))).name: dict(record)
        for record in embedded_inventory
    }
    require(len(embedded_by_name) == len(embedded_inventory)
            and set(embedded_by_name) == set(R2_PREFLIGHT_INVENTORY),
            "r3 recovery r2 preflight inventory path set drift")
    actual_bundle_records: list[dict[str, Any]] = []
    files: dict[str, dict[str, Any]] = {}
    for name in sorted(R2_PREFLIGHT_INVENTORY):
        actual = r2_bundle / name
        current = path_pin(actual, paths.root, paths.team_root)
        require(_same_bytes_hash(current, embedded_by_name[name]),
                f"r2 preflight embedded pin drift: {name}")
        if canonical_contract:
            expected_size, expected_hash = R2_PREFLIGHT_INVENTORY[name]
            require((current["bytes"], current["sha256"])
                    == (expected_size, expected_hash),
                    f"r2 preflight immutable pin drift: {name}")
        actual_bundle_records.append(current)
        _remember_file(files, actual, paths.root, paths.team_root)
    require(_record_signature(embedded_inventory) == _record_signature(actual_bundle_records),
            "r3 recovery r2 preflight inventory drift")

    r2_manifest_path = r2_bundle / "manifest.json"
    r2_manifest_record = recovery.get("r2PreflightManifest")
    require(isinstance(r2_manifest_record, Mapping), "r2 preflight manifest record missing")
    require(_verified_record(r2_manifest_record, paths=paths, owner=recovery_path,
                             label="r2 preflight manifest") == r2_manifest_path,
            "r2 preflight manifest path drift")
    if canonical_contract:
        require((r2_manifest_record.get("bytes"), r2_manifest_record.get("sha256"))
                == (R2_PREFLIGHT_MANIFEST_BYTES, R2_PREFLIGHT_MANIFEST_SHA256),
                "r2 preflight manifest canonical pin drift")
    r2_manifest = read_json(r2_manifest_path)
    require(r2_manifest.get("schemaVersion") == "feelm-service-v1-b1-preflight-manifest/1"
            and r2_manifest.get("runId") == R2_RUN_ID
            and r2_manifest.get("status") == "B1_FULL_PREFLIGHT_COMPLETE_AWAITING_REVIEW"
            and r2_manifest.get("sourceRows") == TRAINING_ROWS
            and r2_manifest.get("logicalRows") == 4 * TRAINING_ROWS
            and r2_manifest.get("partitionCount") == 8
            and r2_manifest.get("resourceStatus") == "PASS"
            and r2_manifest.get("modelFitPerformed") is False
            and r2_manifest.get("fitAuthorized") is False
            and r2_manifest.get("trainingSourceSetSha256")
            == comparison.get("r2HistoricalTrainingSourceSetSha256"),
            "r2 preflight manifest semantic drift")
    verify_manifest_inventory(r2_bundle, r2_manifest,
                              set(R2_PREFLIGHT_INVENTORY) - {"manifest.json"})
    r2_lock = read_json(r2_bundle / "input-lock.json")
    require(r2_lock.get("schemaVersion") == "feelm-service-v1-b1-input-lock/1"
            and r2_lock.get("phase") == "preflight"
            and r2_lock.get("trainingSourceSetSha256")
            == comparison.get("r2HistoricalTrainingSourceSetSha256")
            and r2_lock.get("inputSetSha256") == r2_manifest.get("inputSetSha256")
            and r2_lock.get("controlReferenceSetSha256")
            == r2_manifest.get("controlReferenceSetSha256")
            and r2_lock.get("evaluationTargetsRead") is False,
            "r2 preflight input-lock semantic drift")
    digests = recovery.get("r2PreflightDigests")
    require(isinstance(digests, Mapping) and digests == {
        "trainingSourceSetSha256": r2_lock["trainingSourceSetSha256"],
        "controlReferenceSetSha256": r2_lock["controlReferenceSetSha256"],
        "inputSetSha256": r2_lock["inputSetSha256"],
        "implementationSetSha256": r2_manifest["implementationSetSha256"],
    }, "r2 preflight embedded digest drift")

    r2_review_path = require_unlinked_path(
        base / f"{R2_RUN_ID}-preflight-result-review.json", "r2 preflight review")
    r2_review_record = recovery.get("r2PreflightReview")
    require(isinstance(r2_review_record, Mapping), "r2 preflight review record missing")
    require(_verified_record(r2_review_record, paths=paths, owner=recovery_path,
                             label="r2 preflight review") == r2_review_path,
            "r2 preflight review path drift")
    if canonical_contract:
        require((r2_review_record.get("bytes"), r2_review_record.get("sha256"))
                == (R2_PREFLIGHT_REVIEW_BYTES, R2_PREFLIGHT_REVIEW_SHA256),
                "r2 preflight review canonical pin drift")
    r2_review = read_json(r2_review_path)
    target = r2_review.get("target")
    require(r2_review.get("schemaVersion") == "feelm-service-v1-b1-result-review/1"
            and r2_review.get("phase") == "preflight" and r2_review.get("status") == "PASS"
            and r2_review.get("readyForService") is False
            and r2_review.get("modelFitPerformed") is False and isinstance(target, Mapping),
            "r2 preflight review semantic drift")
    _remember_file(files, r2_review_path, paths.root, paths.team_root)
    for key, record in target.items():
        if isinstance(record, Mapping) and isinstance(record.get("path"), str):
            actual = _verified_record(record, paths=paths, owner=r2_review_path,
                                      label=f"r2 preflight review target.{key}")
            _remember_file(files, actual, paths.root, paths.team_root)
    dependency = r2_review.get("dependencyFingerprint")
    require(isinstance(dependency, Mapping)
            and isinstance(dependency.get("files"), Mapping)
            and isinstance(dependency.get("bundleInventories"), Mapping),
            "r2 review dependency fingerprint missing")
    expected_bundle_key = "standalone/" + os.path.relpath(
        r2_bundle, paths.root.resolve()).replace("\\", "/")
    require(set(dependency["bundleInventories"]) == {expected_bundle_key}
            and dependency["bundleInventories"][expected_bundle_key]
            == {name: {"bytes": record["bytes"], "sha256": record["sha256"]}
                for name, record in embedded_by_name.items()},
            "r2 review dependency bundle closure drift")
    for logical, record in dependency["files"].items():
        require(isinstance(logical, str) and isinstance(record, Mapping),
                "invalid r2 dependency record")
        full_record = {"path": logical, **dict(record)}
        actual = _verified_record(full_record, paths=paths, owner=r2_review_path,
                                  label=f"r2 dependency {logical}")
        _remember_file(files, actual, paths.root, paths.team_root)

    r2_failure_path = require_unlinked_path(
        base / f"{R2_RUN_ID}-fit-failure.json", "r2 fit failure")
    failure_record = recovery.get("r2FitFailure")
    require(isinstance(failure_record, Mapping), "r2 fit failure record missing")
    require(_verified_record(failure_record, paths=paths, owner=recovery_path,
                             label="r2 fit failure") == r2_failure_path,
            "r2 fit failure path drift")
    if canonical_contract:
        require((failure_record.get("bytes"), failure_record.get("sha256"))
                == (R2_FIT_FAILURE_BYTES, R2_FIT_FAILURE_SHA256),
                "r2 fit failure canonical pin drift")
    failure = read_json(r2_failure_path)
    run = failure.get("containerRun")
    state = run.get("dockerState") if isinstance(run, Mapping) else None
    resource = run.get("resource") if isinstance(run, Mapping) else None
    require(failure.get("schemaVersion") == "feelm-service-v1-b1-failure/1"
            and failure.get("phase") == "fit" and failure.get("status") == "FAILED"
            and failure.get("cleanupComplete") is True and isinstance(run, Mapping)
            and run.get("timedOut") is True
            and run.get("workerTerminalResultPresent") is False
            and isinstance(state, Mapping) and state.get("ExitCode") == 143
            and state.get("OOMKilled") is False and isinstance(resource, Mapping)
            and resource.get("resourceStatus") == "RESOURCE_STOP"
            and resource.get("timedOut") is True,
            "r2 fit failure semantic drift")
    require(recovery.get("r2FitFailureFacts") == {
        "phase": "fit", "status": "FAILED", "timedOut": True,
        "resourceStatus": "RESOURCE_STOP", "exitCode": 143, "oomKilled": False,
        "cleanupComplete": True, "workerTerminalResultPresent": False,
        "modelWritten": False,
    }, "r2 fit failure fact summary drift")
    require(not os.path.lexists(base / f"{R2_RUN_ID}-fit"),
            "r2 failure and success fit coexist")
    _remember_file(files, r2_failure_path, paths.root, paths.team_root)

    r2_outer_record = recovery.get("r2OuterRunner")
    r2_plan_record = recovery.get("r2Plan")
    require(isinstance(r2_outer_record, Mapping) and isinstance(r2_plan_record, Mapping),
            "r2 implementation ancestry missing")
    r2_outer_path = _verified_record(r2_outer_record, paths=paths, owner=recovery_path,
                                     label="r2 outer runner")
    r2_plan_path = _verified_record(r2_plan_record, paths=paths, owner=recovery_path,
                                    label="r2 plan")
    if canonical_contract:
        require((r2_outer_record.get("bytes"), r2_outer_record.get("sha256"))
                == (R2_OUTER_RUNNER_BYTES, R2_OUTER_RUNNER_SHA256),
                "r2 runner canonical pin drift")
        require(r2_plan_record.get("sha256") == R2_PLAN_SHA256,
                "r2 plan canonical pin drift")
    _remember_file(files, r2_outer_path, paths.root, paths.team_root)
    _remember_file(files, r2_plan_path, paths.root, paths.team_root)
    return files


def _canonical_json_pin(payload: Mapping[str, Any]) -> dict[str, Any]:
    raw = (json.dumps(json_ready(dict(payload)), ensure_ascii=False, indent=2,
                      sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def _r2_control_records_from_recovery(recovery: Mapping[str, Any]) -> list[dict[str, Any]]:
    inventory = recovery.get("r2PreflightBundleInventory")
    require(isinstance(inventory, list), "r2 preflight embedded inventory missing")
    records = [dict(record) for record in inventory if isinstance(record, Mapping)]
    require(len(records) == len(inventory), "invalid r2 preflight embedded record")
    for key in ("r2PreflightReview", "r2FitFailure", "r2OuterRunner", "r2Plan"):
        record = recovery.get(key)
        require(isinstance(record, Mapping), f"r3 recovery {key} record missing")
        records.append(dict(record))
    canonical_record_set_digest(records)
    return records


def _bundle_control_records(bundle: Path, root: Path,
                            team_root: Path) -> list[dict[str, Any]]:
    _bundle_inventory(bundle)
    return [path_pin(path, root, team_root) for path in sorted(
        (item for item in bundle.rglob("*") if item.is_file()),
        key=lambda item: item.as_posix())]


def _verify_phase_profile(path: Path, *, phase: str, profile_record: Mapping[str, Any],
                          outer_record: Mapping[str, Any], execution_digest: str,
                          paths: EvaluationPaths) -> dict[str, Any]:
    payload = read_json(path)
    require(set(payload) == {"schemaVersion", "runId", "profileId", "phase",
                             "profileContract", "outerRunner", "executionSetSha256",
                             "resolved"},
            f"{phase} execution profile field set drift")
    require(payload.get("schemaVersion") == "feelm-service-v1-b1-phase-execution-profile/1"
            and payload.get("runId") == RUN_ID and payload.get("profileId") == PROFILE_ID
            and payload.get("phase") == phase,
            f"{phase} execution profile identity drift")
    require(payload.get("profileContract") == profile_record
            and payload.get("outerRunner") == outer_record
            and payload.get("executionSetSha256") == execution_digest,
            f"{phase} execution profile chain drift")
    profile_path = _verified_record(profile_record, paths=paths, owner=path,
                                    label=f"{phase} profile contract")
    require(payload.get("resolved") == read_json(profile_path),
            f"{phase} resolved profile drift")
    return payload


def _verify_preflight_ancestry(
    paths: EvaluationPaths,
    *,
    preflight_reference_path: Path,
    preflight_reference: Mapping[str, Any],
    fit_manifest: Mapping[str, Any],
    fit_groups: tuple[Sequence[Any], Sequence[Any], Sequence[Any]],
    fit_controls: Sequence[Mapping[str, Any]],
    fit_lock: Mapping[str, Any],
    outer_path: Path,
    worker_path: Path,
    outer_record: Mapping[str, Any],
    worker_record: Mapping[str, Any],
    profile_record: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    required_reference = {
        "schemaVersion", "runId", "profileId", "preflightManifest",
        "preflightReview", "reviewedArtifacts", "recoveryReference",
        "outerRunner", "sparkWorker", "executionProfile", "modelInputSetSha256",
        "workerRuntimeSetSha256", "executionSetSha256",
        "controlReferenceSetSha256", "fitInputSetSha256",
    }
    require(set(preflight_reference) == required_reference,
            "r3 preflight reference field set drift")
    require(preflight_reference.get("schemaVersion")
            == "feelm-service-v1-b1-preflight-reference/2"
            and preflight_reference.get("runId") == RUN_ID
            and preflight_reference.get("profileId") == PROFILE_ID,
            "r3 preflight reference identity/schema drift")
    for key in ("preflightManifest", "preflightReview", "outerRunner", "sparkWorker",
                "executionProfile", "recoveryReference"):
        require(isinstance(preflight_reference.get(key), Mapping),
                f"preflight reference {key} missing")
    require(preflight_reference.get("outerRunner") == outer_record
            and preflight_reference.get("sparkWorker") == worker_record
            and preflight_reference.get("executionProfile") == profile_record,
            "preflight/fit implementation or profile pin drift")
    model_records, runtime_records, execution_records = fit_groups
    require(preflight_reference.get("modelInputSetSha256")
            == fit_lock.get("modelInputSetSha256")
            == canonical_record_set_digest(model_records),
            "preflight reference model-input digest drift")
    require(preflight_reference.get("workerRuntimeSetSha256")
            == fit_lock.get("workerRuntimeSetSha256")
            == canonical_record_set_digest(runtime_records),
            "preflight reference worker-runtime digest drift")
    require(preflight_reference.get("executionSetSha256")
            == fit_lock.get("executionSetSha256")
            == canonical_record_set_digest(execution_records),
            "preflight reference execution digest drift")
    require(preflight_reference.get("controlReferenceSetSha256")
            == fit_lock.get("controlReferenceSetSha256")
            and preflight_reference.get("fitInputSetSha256") == fit_lock.get("inputSetSha256"),
            "preflight reference fit lock digest drift")
    require(preflight_reference.get("reviewedArtifacts") == list(fit_controls),
            "preflight reviewed artifact list drift")

    preflight_manifest_path = _verified_record(
        preflight_reference["preflightManifest"], paths=paths,
        owner=preflight_reference_path, label="r3 preflight reference manifest")
    require(preflight_manifest_path.name == "manifest.json", "r3 preflight manifest basename")
    preflight_bundle = preflight_manifest_path.parent
    require(preflight_bundle.name == RUN_ID + "-preflight", "r3 preflight namespace drift")
    _require_canonical_spark_phase_bundle(paths, preflight_bundle, "preflight")
    preflight_review_path = _verified_record(
        preflight_reference["preflightReview"], paths=paths,
        owner=preflight_reference_path, label="r3 preflight reference review")
    require(preflight_review_path
            == preflight_bundle.with_name(preflight_bundle.name + "-result-review.json").resolve(),
            "r3 preflight review is not canonical sibling")

    required_bundle = {
        "input-lock.json", "recovery-reference.json", "execution-profile.json",
        "partition-identity.json", "command.json", "resource.json", "run.log",
    }
    preflight_manifest = read_json(preflight_manifest_path)
    verify_phase_namespace_clean(preflight_bundle)
    require(preflight_manifest.get("schemaVersion")
            == "feelm-service-v1-b1-preflight-manifest/2"
            and preflight_manifest.get("runId") == RUN_ID
            and preflight_manifest.get("profileId") == PROFILE_ID
            and preflight_manifest.get("status")
            == "B1_FULL_PREFLIGHT_COMPLETE_AWAITING_REVIEW",
            "r3 preflight manifest identity/status drift")
    require(preflight_manifest.get("fitAuthorized") is False
            and preflight_manifest.get("modelFitPerformed") is False
            and preflight_manifest.get("scorePerformed") is False
            and preflight_manifest.get("readyForService") is False,
            "r3 preflight manifest premature authorization")
    verify_manifest_inventory(preflight_bundle, preflight_manifest, required_bundle)

    preflight_review = read_json(preflight_review_path)
    preflight_target, preflight_review_files = _require_review_envelope(
        preflight_review, "preflight", paths=paths, review_path=preflight_review_path)
    target_files = {
        "manifest": preflight_manifest_path,
        "input_lock": preflight_bundle / "input-lock.json",
        "recovery_reference": preflight_bundle / "recovery-reference.json",
        "execution_profile": preflight_bundle / "execution-profile.json",
        "partition_identity": preflight_bundle / "partition-identity.json",
        "command": preflight_bundle / "command.json",
        "resource": preflight_bundle / "resource.json",
        "run_log": preflight_bundle / "run.log",
        "outer_runner": outer_path,
        "spark_worker": worker_path,
    }
    require(set(preflight_target) == set(target_files), "r3 preflight review target set drift")
    for key, actual in target_files.items():
        _target_pin(preflight_review, key, actual, root=paths.root,
                    team_root=paths.team_root, review_path=preflight_review_path,
                    phase="preflight")

    preflight_lock_path = preflight_bundle / "input-lock.json"
    preflight_lock = read_json(preflight_lock_path)
    pre_models, pre_runtime, pre_execution, pre_controls = verify_r3_grouped_lock(
        preflight_lock, phase="preflight", label="r3 preflight input lock",
        canonical_contract=paths.root.resolve() == ROOT.resolve())
    _, pre_source_files = verify_r3_training_groups(
        pre_models, pre_runtime, pre_execution, paths=paths,
        owner=preflight_lock_path, label="r3 preflight")
    require(_record_signature(pre_models) == _record_signature(model_records)
            and _record_signature(pre_runtime) == _record_signature(runtime_records)
            and _record_signature(pre_execution) == _record_signature(execution_records),
            "r3 preflight/fit grouped source drift")

    recovery_path = preflight_bundle / "recovery-reference.json"
    recovery = read_json(recovery_path)
    execution_digest = canonical_record_set_digest(pre_execution)
    recovery_files = verify_r2_recovery_ancestry(
        paths, recovery_path, recovery, outer_record=outer_record,
        worker_record=worker_record, profile_record=profile_record,
        training_recipe_record=next(
            record for record in pre_models
            if record.get("path") == "contract/training-recipe.v1.json"),
        model_input_digest=canonical_record_set_digest(pre_models),
        worker_runtime_digest=canonical_record_set_digest(pre_runtime),
        execution_digest=execution_digest)
    expected_r2_controls = _r2_control_records_from_recovery(recovery)
    require(list(pre_controls) == expected_r2_controls,
            "r3 preflight r2 control closure drift")
    expected_fit_controls = [*_bundle_control_records(
                                 preflight_bundle, paths.root, paths.team_root),
                             path_pin(preflight_review_path, paths.root, paths.team_root),
                             *expected_r2_controls]
    require(list(fit_controls) == expected_fit_controls,
            "r3 fit preflight/r2 control closure drift")

    embedded_recovery = preflight_reference["recoveryReference"]
    require(set(embedded_recovery) == {"bytes", "sha256", "payload"}
            and embedded_recovery.get("payload") == recovery
            and {"bytes": embedded_recovery.get("bytes"),
                 "sha256": embedded_recovery.get("sha256")}
            == _canonical_json_pin(recovery),
            "preflight embedded recovery payload drift")
    _verify_phase_profile(
        preflight_bundle / "execution-profile.json", phase="preflight",
        profile_record=profile_record, outer_record=outer_record,
        execution_digest=execution_digest, paths=paths)

    for key in ("modelInputSetSha256", "workerRuntimeSetSha256", "executionSetSha256",
                "controlReferenceSetSha256", "inputSetSha256"):
        require(preflight_manifest.get(key) == preflight_lock.get(key),
                f"r3 preflight manifest/lock {key} drift")
        require(preflight_target["input_lock"].get(key) == preflight_lock.get(key),
                f"r3 preflight review omitted {key}")
    require(preflight_lock.get("r2TrainingSourceSetSha256")
            == recovery["digestComparison"]["r2HistoricalTrainingSourceSetSha256"]
            and preflight_manifest.get("r2TrainingSourceSetSha256")
            == preflight_lock.get("r2TrainingSourceSetSha256")
            and preflight_manifest.get("r2PreflightManifestSha256")
            == recovery["r2PreflightManifest"]["sha256"]
            and preflight_manifest.get("r2PreflightReviewSha256")
            == recovery["r2PreflightReview"]["sha256"]
            and preflight_manifest.get("r2FitFailureSha256")
            == recovery["r2FitFailure"]["sha256"],
            "r3 preflight r2 ancestry manifest drift")
    if paths.root.resolve() == ROOT.resolve():
        require(preflight_manifest.get("r2TrainingSourceSetSha256")
                == R2_TRAINING_SOURCE_SET_SHA256
                and preflight_manifest.get("r2PreflightManifestSha256")
                == R2_PREFLIGHT_MANIFEST_SHA256
                and preflight_manifest.get("r2PreflightReviewSha256")
                == R2_PREFLIGHT_REVIEW_SHA256
                and preflight_manifest.get("r2FitFailureSha256") == R2_FIT_FAILURE_SHA256,
                "r3 preflight canonical r2 ancestry pin drift")
    require(preflight_manifest.get("recoveryReferenceSha256") == sha256_file(recovery_path)
            and preflight_manifest.get("executionProfileSha256")
            == sha256_file(preflight_bundle / "execution-profile.json"),
            "r3 preflight recovery/profile manifest link drift")
    require(preflight_manifest.get("outerRunnerSha256") == outer_record.get("sha256")
            and preflight_manifest.get("sparkWorkerSha256") == worker_record.get("sha256"),
            "r3 preflight implementation manifest drift")
    require(preflight_manifest.get("implementationSetSha256")
            == canonical_record_set_digest([outer_record, worker_record]),
            "r3 preflight implementation-set manifest drift")
    require(fit_manifest.get("preflightManifestSha256")
            == preflight_reference["preflightManifest"].get("sha256")
            and fit_manifest.get("preflightReviewSha256")
            == preflight_reference["preflightReview"].get("sha256"),
            "r3 fit manifest preflight chain drift")

    files: dict[str, dict[str, Any]] = {}
    _merge_verified_files(files, preflight_review_files, "r3 preflight review")
    _merge_verified_files(files, pre_source_files, "r3 preflight")
    _merge_verified_files(files, recovery_files, "r3 preflight/r2")
    for actual in [preflight_review_path, *target_files.values()]:
        _remember_file(files, actual, paths.root, paths.team_root)
    return {"manifest": preflight_manifest, "review": preflight_review,
            "inputLock": preflight_lock, "recovery": recovery,
            "bundlePath": preflight_bundle,
            "r2PreflightBundlePath": (
                paths.root / "outputs/recommendation-evidence/service-v1-pretraining-20260913"
                / f"{R2_RUN_ID}-preflight").resolve(),
            "r2PreflightBundleInventory": {
                Path(str(record["path"])).name: {
                    "bytes": int(record["bytes"]), "sha256": str(record["sha256"])
                }
                for record in recovery["r2PreflightBundleInventory"]
            },
            "r2FitFailurePath": (
                paths.root / "outputs/recommendation-evidence/service-v1-pretraining-20260913"
                / f"{R2_RUN_ID}-fit-failure.json").resolve(),
            "r2FitFailurePin": {
                "bytes": int(recovery["r2FitFailure"]["bytes"]),
                "sha256": str(recovery["r2FitFailure"]["sha256"]),
            },
            "r2FitSuccessPath": (
                paths.root / "outputs/recommendation-evidence/service-v1-pretraining-20260913"
                / f"{R2_RUN_ID}-fit").resolve()}, files

def verify_fit_reference(
    paths: EvaluationPaths, fit_reference_path: Path,
    fit_reference: Mapping[str, Any], score_manifest: Mapping[str, Any],
    score_target: Mapping[str, Any], score_lock: Mapping[str, Any],
    score_controls: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Prove the r3 score refers to one exact reviewed fit and full ancestry."""
    required_reference = {
        "schemaVersion", "runId", "profileId", "fitManifest", "fitReview",
        "modelInventory", "modelFileSetSha256", "modelFiles", "reviewedArtifacts",
        "recoveryReference", "outerRunner", "sparkWorker", "executionProfile",
        "modelInputSetSha256", "workerRuntimeSetSha256", "executionSetSha256",
        "scoreInputSetSha256", "scorePhaseInputSetSha256",
    }
    require(set(fit_reference) == required_reference, "r3 fit reference field set drift")
    require(fit_reference.get("schemaVersion") == "feelm-service-v1-b1-fit-reference/2"
            and fit_reference.get("runId") == RUN_ID
            and fit_reference.get("profileId") == PROFILE_ID,
            "r3 fit reference identity/schema drift")
    for key in ("fitManifest", "fitReview", "modelInventory", "outerRunner",
                "sparkWorker", "executionProfile", "recoveryReference"):
        require(isinstance(fit_reference.get(key), Mapping),
                f"r3 fit reference {key} missing")
    require(fit_reference.get("reviewedArtifacts") == list(score_controls),
            "r3 fit reference reviewed artifact closure drift")
    require(fit_reference.get("modelInputSetSha256") == score_lock.get("modelInputSetSha256")
            and fit_reference.get("workerRuntimeSetSha256")
            == score_lock.get("workerRuntimeSetSha256")
            and fit_reference.get("executionSetSha256") == score_lock.get("executionSetSha256")
            and fit_reference.get("scoreInputSetSha256")
            == score_lock.get("scoreInputSetSha256")
            and fit_reference.get("scorePhaseInputSetSha256")
            == score_lock.get("inputSetSha256"),
            "r3 fit reference score-lock digest drift")

    fit_manifest_path = _verified_record(fit_reference["fitManifest"], paths=paths,
                                         owner=fit_reference_path,
                                         label="r3 fit reference manifest")
    require(fit_manifest_path.name == "manifest.json", "r3 fit manifest basename")
    fit_bundle = fit_manifest_path.parent
    require(fit_bundle.name == RUN_ID + "-fit", "r3 fit bundle namespace drift")
    _require_canonical_spark_phase_bundle(paths, fit_bundle, "fit")
    fit_review_path = _verified_record(fit_reference["fitReview"], paths=paths,
                                       owner=fit_reference_path,
                                       label="r3 fit reference review")
    require(fit_review_path
            == fit_bundle.with_name(fit_bundle.name + "-result-review.json").resolve(),
            "r3 fit review is not canonical sibling")
    inventory_path = _verified_record(fit_reference["modelInventory"], paths=paths,
                                      owner=fit_reference_path,
                                      label="r3 fit model inventory")
    require(inventory_path == (fit_bundle / "model-file-inventory.json").resolve(),
            "r3 fit model inventory path mismatch")
    outer_path = _verified_record(fit_reference["outerRunner"], paths=paths,
                                  owner=fit_reference_path, label="r3 outer runner")
    worker_path = _verified_record(fit_reference["sparkWorker"], paths=paths,
                                   owner=fit_reference_path, label="r3 Spark worker")
    profile_path = _verified_record(fit_reference["executionProfile"], paths=paths,
                                    owner=fit_reference_path,
                                    label="r3 profile contract")
    if paths.root.resolve() == ROOT.resolve():
        require(fit_reference["outerRunner"].get("sha256") == REVIEWED_R3_RUNNER_SHA256,
                "fit reference runner is not reviewed r3 producer")
        require(fit_reference["executionProfile"].get("sha256")
                == REVIEWED_R3_PROFILE_SHA256,
                "fit reference profile is not reviewed r3 contract")

    manifest = read_json(fit_manifest_path)
    verify_phase_namespace_clean(fit_bundle)
    review = read_json(fit_review_path)
    inventory = read_json(inventory_path)
    require(manifest.get("schemaVersion") == "feelm-service-v1-b1-fit-manifest/2"
            and manifest.get("runId") == RUN_ID and manifest.get("profileId") == PROFILE_ID
            and manifest.get("status") == "B1_MODEL_FIT_COMPLETE_AUDIT_PENDING",
            "r3 fit manifest identity/status drift")
    require(manifest.get("scoringAuthorized") is False
            and manifest.get("readyForService") is False
            and manifest.get("modelFitPerformed") is True
            and manifest.get("scorePerformed") is False,
            "r3 fit manifest premature authorization")
    fit_target, fit_review_files = _require_review_envelope(
        review, "fit", paths=paths, review_path=fit_review_path)
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
        "execution_profile": fit_bundle / "execution-profile.json",
        "recovery_reference": fit_bundle / "recovery-reference.json",
        "outer_runner": outer_path,
        "spark_worker": worker_path,
    }
    require(set(fit_target) == set(fixed_target_files), "r3 fit review target set drift")
    for key, actual in fixed_target_files.items():
        _target_pin(review, key, actual, root=paths.root, team_root=paths.team_root,
                    review_path=fit_review_path, phase="fit")

    model_records = inventory.get("files")
    require(inventory.get("schemaVersion") == "feelm-service-v1-b1-model-inventory/1",
            "model inventory schema drift")
    require(isinstance(model_records, list) and model_records, "model inventory files missing")
    require(fit_reference.get("modelFiles") == model_records,
            "fit reference/model inventory file list mismatch")
    relative_names = [record.get("path") for record in model_records
                      if isinstance(record, Mapping)]
    require(len(relative_names) == len(model_records)
            and all(isinstance(name, str) and name for name in relative_names)
            and relative_names == sorted(relative_names)
            and len(set(relative_names)) == len(relative_names),
            "native model inventory order/uniqueness drift")
    model_root = require_unlinked_path(
        fit_bundle / "model/native", "native model root", directory=True)
    actual_model_names = sorted(_bundle_inventory(model_root))
    require(actual_model_names == relative_names, "native model physical inventory drift")
    native_files: list[Path] = []
    for record in model_records:
        relative = Path(str(record["path"]))
        require(not relative.is_absolute() and ".." not in relative.parts,
                "unsafe native model path")
        actual = require_unlinked_path(model_root / relative,
                                       f"native model {record['path']}")
        require(actual.is_relative_to(model_root), "native model path escapes model root")
        verify_pin(actual, record, f"native model {record['path']}")
        native_files.append(actual)
    model_digest = canonical_record_set_digest(model_records)
    require(inventory.get("inventorySha256") == model_digest
            and fit_reference.get("modelFileSetSha256") == model_digest,
            "native model inventory digest drift")

    required_bundle = {
        "input-lock.json", "preflight-reference.json", "command.json",
        "partition-identity.json", "resolved-estimator.json",
        "model-file-inventory.json", "threshold-fixtures.npz", "fit-metrics.json",
        "resource.json", "run.log", "execution-profile.json", "recovery-reference.json",
    } | {"model/native/" + name for name in relative_names}
    verify_manifest_inventory(fit_bundle, manifest, required_bundle)
    fit_lock_path = fit_bundle / "input-lock.json"
    fit_lock = read_json(fit_lock_path)
    fit_models, fit_runtime, fit_execution, fit_controls = verify_r3_grouped_lock(
        fit_lock, phase="fit", label="r3 fit input lock",
        canonical_contract=paths.root.resolve() == ROOT.resolve())
    _, fit_source_files = verify_r3_training_groups(
        fit_models, fit_runtime, fit_execution, paths=paths,
        owner=fit_lock_path, label="r3 fit")
    require(_record_signature(fit_models)
            == _record_signature(score_lock.get("modelInputRecords", []))
            and _record_signature(fit_runtime)
            == _record_signature(score_lock.get("workerRuntimeRecords", []))
            and _record_signature(fit_execution)
            == _record_signature(score_lock.get("executionRecords", [])),
            "r3 fit/score invariant input groups drift")
    for key in ("modelInputSetSha256", "workerRuntimeSetSha256", "executionSetSha256",
                "controlReferenceSetSha256", "inputSetSha256"):
        require(manifest.get(key) == fit_lock.get(key), f"r3 fit manifest/lock {key} drift")
        require(fit_target["input_lock"].get(key) == fit_lock.get(key),
                f"r3 fit review omitted {key}")
    require(fit_lock.get("evaluationTargetsRead") is False,
            "r3 fit input lock permits evaluation targets")

    outer_alias = next(record for record in fit_execution
                       if record.get("path") == "execution/run_service_v1_b1_gbt_r3.py")
    profile_alias = next(record for record in fit_execution
                         if record.get("path") ==
                         "execution/local4c12g-t14400-profile.json")
    worker_alias = next(record for record in fit_runtime
                        if record.get("path") == "implementation/service_v1_b1_spark_worker.py")
    require(_same_bytes_hash(outer_alias, fit_reference["outerRunner"])
            and _same_bytes_hash(worker_alias, fit_reference["sparkWorker"])
            and profile_alias == fit_reference["executionProfile"],
            "r3 fit reference/group implementation drift")
    implementation = canonical_record_set_digest(
        [fit_reference["outerRunner"], fit_reference["sparkWorker"]])
    require(manifest.get("outerRunnerSha256") == fit_reference["outerRunner"].get("sha256")
            and manifest.get("sparkWorkerSha256") == fit_reference["sparkWorker"].get("sha256")
            and manifest.get("implementationSetSha256") == implementation,
            "r3 fit manifest implementation drift")
    require(manifest.get("modelInventorySha256")
            == fit_reference["modelInventory"].get("sha256")
            and manifest.get("modelFileSetSha256") == model_digest,
            "r3 fit manifest model inventory drift")
    recovery_path = fit_bundle / "recovery-reference.json"
    recovery = read_json(recovery_path)
    embedded_fit_recovery = fit_reference.get("recoveryReference")
    require(isinstance(embedded_fit_recovery, Mapping)
            and set(embedded_fit_recovery) == {"bytes", "sha256", "payload"}
            and embedded_fit_recovery.get("payload") == recovery
            and {"bytes": embedded_fit_recovery.get("bytes"),
                 "sha256": embedded_fit_recovery.get("sha256")}
            == _canonical_json_pin(recovery),
            "r3 fit/score embedded recovery reference drift")
    require(manifest.get("r2FitFailureSha256")
            == recovery["r2FitFailure"]["sha256"],
            "r3 fit manifest r2 failure link drift")
    preflight_reference_path = fit_bundle / "preflight-reference.json"
    preflight_reference = read_json(preflight_reference_path)
    preflight_chain, preflight_files = _verify_preflight_ancestry(
        paths, preflight_reference_path=preflight_reference_path,
        preflight_reference=preflight_reference, fit_manifest=manifest,
        fit_groups=(fit_models, fit_runtime, fit_execution), fit_controls=fit_controls,
        fit_lock=fit_lock, outer_path=outer_path, worker_path=worker_path,
        outer_record=fit_reference["outerRunner"], worker_record=fit_reference["sparkWorker"],
        profile_record=fit_reference["executionProfile"])
    require(preflight_chain["recovery"] == recovery,
            "r3 fit recovery differs from preflight ancestry")
    _verify_phase_profile(fit_bundle / "execution-profile.json", phase="fit",
                          profile_record=fit_reference["executionProfile"],
                          outer_record=fit_reference["outerRunner"],
                          execution_digest=fit_lock["executionSetSha256"], paths=paths)

    expected_score_controls = [*_bundle_control_records(
                                   fit_bundle, paths.root, paths.team_root),
                               path_pin(fit_review_path, paths.root, paths.team_root),
                               *list(fit_controls)]
    require(list(score_controls) == expected_score_controls,
            "r3 score fit/preflight/r2 control closure drift")
    require(score_manifest.get("fitManifestSha256")
            == fit_reference["fitManifest"].get("sha256")
            and score_manifest.get("fitReviewSha256")
            == fit_reference["fitReview"].get("sha256")
            and score_manifest.get("modelInventorySha256")
            == fit_reference["modelInventory"].get("sha256")
            and score_manifest.get("modelFileSetSha256") == model_digest,
            "r3 score manifest fit/model chain drift")
    for key, reference_key, actual in (("outer_runner", "outerRunner", outer_path),
                                        ("spark_worker", "sparkWorker", worker_path)):
        score_record = score_target.get(key)
        require(isinstance(score_record, Mapping)
                and _same_bytes_hash(score_record, fit_reference[reference_key]),
                f"r3 score/fit {key} pin drift")
        resolved = _verified_record(score_record, paths=paths, owner=paths.score_review,
                                    label=f"r3 score review target.{key}")
        require(resolved == actual, f"r3 score/fit {key} path drift")

    files: dict[str, dict[str, Any]] = {}
    _merge_verified_files(files, fit_review_files, "r3 fit review")
    for actual in [fit_manifest_path, fit_review_path, *fixed_target_files.values(),
                   profile_path, *native_files]:
        _remember_file(files, actual, paths.root, paths.team_root)
    _merge_verified_files(files, fit_source_files, "r3 fit")
    _merge_verified_files(files, preflight_files, "r3 fit/preflight")
    return {"manifest": manifest, "review": review, "inventory": inventory,
            "inputLock": fit_lock, "preflight": preflight_chain,
            "recovery": recovery, "bundlePath": fit_bundle}, files

def verify_score_chain(paths: EvaluationPaths, expected_manifest_sha256: str,
                       expected_review_sha256: str, spec: EvaluationSpec
                       ) -> tuple[dict[str, Any], dict[str, dict[str, Any]], Path]:
    """Validate and recursively rehash the r3 score chain before labels."""
    if paths.root.resolve() == ROOT.resolve():
        expected_bundle = (ROOT / "outputs/recommendation-evidence/"
                           "service-v1-pretraining-20260913" / f"{RUN_ID}-score")
        require(_lexical_absolute(paths.score_manifest)
                == _lexical_absolute(expected_bundle / "manifest.json")
                and _lexical_absolute(paths.score_review)
                == _lexical_absolute(expected_bundle.with_name(
                    expected_bundle.name + "-result-review.json")),
                "r3 score manifest/review noncanonical lexical path")
    verify_sha(paths.score_manifest, expected_manifest_sha256, "r3 score manifest")
    verify_sha(paths.score_review, expected_review_sha256, "r3 score review")
    require(paths.score_manifest.name == "manifest.json", "r3 score manifest basename")
    bundle = paths.score_manifest.parent
    require(bundle.name == RUN_ID + "-score", "r3 score bundle namespace drift")
    require(paths.score_review.resolve()
            == bundle.with_name(bundle.name + "-result-review.json").resolve(),
            "r3 score review is not canonical sibling")
    required = {
        "fit-reference.json", "score-input-lock.json", "command.json",
        "score/analyzed-plan.txt", "score/predictions.parquet", "resource.json", "run.log",
        "execution-profile.json", "recovery-reference.json",
    }
    manifest = read_json(paths.score_manifest)
    verify_phase_namespace_clean(bundle)
    review = read_json(paths.score_review)
    require(manifest.get("schemaVersion") == "feelm-service-v1-b1-score-manifest/2"
            and manifest.get("runId") == RUN_ID and manifest.get("profileId") == PROFILE_ID
            and manifest.get("status") == "B1_NATURAL_SCORE_COMPLETE_AUDIT_PENDING",
            "r3 score manifest identity/status drift")
    require(manifest.get("evaluationAuthorized") is False
            and manifest.get("readyForService") is False
            and manifest.get("modelFitPerformed") is False
            and manifest.get("scorePerformed") is True,
            "r3 score manifest premature authorization")
    target, score_review_files = _require_review_envelope(
        review, "score", paths=paths, review_path=paths.score_review)
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
        "execution_profile": bundle / "execution-profile.json",
        "recovery_reference": bundle / "recovery-reference.json",
    }
    external_target_files = {
        "outer_runner": paths.root / "scripts/run_service_v1_b1_gbt_r3.py",
        "spark_worker": paths.root / "scripts/service_v1_b1_spark_worker.py",
    }
    require(set(target) == set(target_files) | set(external_target_files),
            "r3 score review target set drift")
    for key, path in {**target_files, **external_target_files}.items():
        _target_pin(review, key, path, root=paths.root, team_root=paths.team_root,
                    review_path=paths.score_review, phase="score")

    score_lock_path = bundle / "score-input-lock.json"
    score_lock = read_json(score_lock_path)
    (score_models, score_only_inputs, score_runtime,
     score_execution, score_controls) = verify_r3_score_lock(
        score_lock, label="r3 score input lock",
        canonical_contract=paths.root.resolve() == ROOT.resolve())
    _, score_source_files = verify_r3_score_groups(
        score_models, score_only_inputs, score_runtime, score_execution, paths=paths,
        owner=score_lock_path, label="r3 score")
    for key in ("modelInputSetSha256", "scoreInputSetSha256",
                "workerRuntimeSetSha256", "executionSetSha256",
                "controlReferenceSetSha256", "inputSetSha256"):
        require(manifest.get(key) == score_lock.get(key),
                f"r3 score manifest/lock {key} drift")
        require(target["score_input_lock"].get(key) == score_lock.get(key),
                f"r3 score review omitted {key}")

    fit_reference_path = bundle / "fit-reference.json"
    fit_reference = read_json(fit_reference_path)
    fit_chain, fit_files = verify_fit_reference(
        paths, fit_reference_path, fit_reference, manifest, target,
        score_lock, score_controls)
    outer_alias = next(record for record in score_execution
                       if record.get("path") == "execution/run_service_v1_b1_gbt_r3.py")
    worker_alias = next(record for record in score_runtime
                        if record.get("path") == "implementation/service_v1_b1_spark_worker.py")
    profile_alias = next(record for record in score_execution
                         if record.get("path") ==
                         "execution/local4c12g-t14400-profile.json")
    require(_same_bytes_hash(outer_alias, fit_reference["outerRunner"])
            and _same_bytes_hash(worker_alias, fit_reference["sparkWorker"])
            and profile_alias == fit_reference["executionProfile"],
            "r3 score group/fit reference implementation drift")
    implementation = canonical_record_set_digest(
        [fit_reference["outerRunner"], fit_reference["sparkWorker"]])
    require(manifest.get("implementationSetSha256") == implementation
            and manifest.get("outerRunnerSha256") == fit_reference["outerRunner"].get("sha256")
            and manifest.get("sparkWorkerSha256") == fit_reference["sparkWorker"].get("sha256"),
            "r3 score manifest implementation drift")
    recovery = read_json(bundle / "recovery-reference.json")
    embedded_score_recovery = fit_reference.get("recoveryReference")
    require(isinstance(embedded_score_recovery, Mapping)
            and embedded_score_recovery.get("payload") == recovery
            == fit_chain.get("recovery"),
            "r3 score/fit/preflight recovery ancestry drift")
    require(manifest.get("r2FitFailureSha256")
            == recovery["r2FitFailure"]["sha256"]
            and manifest.get("recoveryReferenceSha256")
            == sha256_file(bundle / "recovery-reference.json")
            and manifest.get("executionProfileSha256")
            == sha256_file(bundle / "execution-profile.json"),
            "r3 score recovery/profile manifest link drift")
    _verify_phase_profile(bundle / "execution-profile.json", phase="score",
                          profile_record=fit_reference["executionProfile"],
                          outer_record=fit_reference["outerRunner"],
                          execution_digest=score_lock["executionSetSha256"], paths=paths)

    source_files: dict[str, dict[str, Any]] = {}
    _merge_verified_files(source_files, score_review_files, "r3 score review")
    for path in [*target_files.values(), paths.score_review]:
        _remember_file(source_files, path, paths.root, paths.team_root)
    for key in ("outer_runner", "spark_worker"):
        resolved = _verified_record(target[key], paths=paths, owner=paths.score_review,
                                    label=f"r3 score review target.{key}")
        _remember_file(source_files, resolved, paths.root, paths.team_root)
    _merge_verified_files(source_files, fit_files, "r3 score/fit")
    _merge_verified_files(source_files, score_source_files, "r3 score sources")

    predictions = bundle / "score/predictions.parquet"
    require(predictions.is_file() and not predictions.is_dir(),
            "B1 predictions must be one physical Parquet file")
    schema = pq.read_schema(predictions)
    expected_schema = pa.schema([("row_id", pa.int64()), ("uid", pa.int32()),
                                 ("prediction", pa.float64())])
    require(schema.equals(expected_schema, check_metadata=False), "B1 prediction schema drift")
    metadata = pq.ParquetFile(predictions).metadata
    require(metadata.num_rows == spec.score_rows, "B1 prediction row count drift")
    require(manifest.get("predictionSha256") == sha256_file(predictions),
            "score manifest prediction hash drift")
    census = manifest.get("scoreCensus")
    require(isinstance(census, Mapping) and census.get("rows") == spec.score_rows
            and census.get("rowIdMinimum") == 0
            and census.get("rowIdMaximum") == spec.score_rows - 1
            and census.get("rowIdsUnique") is True and census.get("uidAxisEqual") is True
            and census.get("predictionsFinite") is True
            and census.get("singlePhysicalParquetFile") is True,
            "score manifest score census drift")
    require(manifest.get("labelProjected") is False,
            "score manifest projects evaluation label")
    return {"manifest": manifest, "review": review, "fitReference": fit_reference,
            "fit": fit_chain, "recovery": recovery, "bundlePath": bundle}, source_files, predictions

def verify_als_axis(paths: EvaluationPaths, spec: EvaluationSpec) -> tuple[set[int], dict[str, dict[str, Any]]]:
    manifest = read_json(paths.artifact_manifest)
    prefix = "outputs/recommendation-evidence/combination340/ALS/item-factors/"
    declared = {str(a["source_path"]): {"bytes": int(a["bytes"]), "sha256": str(a["sha256"])}
                for a in manifest.get("artifacts", []) if str(a.get("source_path", "")).startswith(prefix)}
    require(bool(declared), "artifact manifest has no ALS factor inventory")
    factor_root = require_unlinked_path(
        paths.als_factor_dir, "ALS factor directory", directory=True)
    actual = {prefix + name: factor_root / name
              for name in _bundle_inventory(factor_root)}
    require(set(actual) == set(declared), "ALS factor physical inventory drift")
    inventory: dict[str, dict[str, Any]] = {}
    parts: list[Path] = []
    for relative, path in sorted(actual.items()):
        verify_pin(path, declared[relative], f"ALS factor {relative}")
        record = path_pin(path, paths.root, paths.team_root)
        key = str(record["path"])
        inventory[key] = record
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


def _add_source_file(inventory: dict[str, dict[str, Any]], path: Path, root: Path,
                     team_root: Path) -> None:
    record = path_pin(path, root, team_root)
    key = str(record["path"])
    previous = inventory.get(key)
    require(previous is None or previous == record, f"conflicting source pin: {key}")
    inventory[key] = record


def reverify_source_inventory(files: Mapping[str, Mapping[str, Any]], root: Path,
                              team_root: Path) -> None:
    masked_roots: set[Path] = set()
    for relative, expected in files.items():
        if relative == "ancestor/service-v1-b1-spark-runner.md":
            lexical = root / "docs/recommendation/plans/service-v1-b1-spark-runner.md"
        elif relative.startswith("team/"):
            lexical = team_root / relative.removeprefix("team/")
        elif relative.startswith("standalone/"):
            lexical = root / relative.removeprefix("standalone/")
        else:
            lexical = root / relative
        path = require_unlinked_path(lexical, f"source recheck {relative}")
        verify_pin(path, expected, f"source recheck {relative}")
        if path.name == "tmdb-masked-rh230.parquet":
            masked_roots.add(path.parent)
    for masked in masked_roots:
        require_unlinked_path(masked, "source recheck masked bundle", directory=True)
        require(_bundle_inventory(masked)
                == {"manifest.json", "tmdb-masked-rh230.parquet", "views-manifest.json"},
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
        "execution-profile.json", "command.json", "resource.json", "run.log",
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
        "resource.json", "run.log", "execution-profile.json", "recovery-reference.json",
    } | {"model/native/" + name for name in model_names}
    score_required = {
        "fit-reference.json", "score-input-lock.json", "command.json",
        "score/analyzed-plan.txt", "score/predictions.parquet", "resource.json", "run.log",
        "execution-profile.json", "recovery-reference.json",
    }

    for phase, bundle, manifest, required in (
        ("preflight", preflight_bundle, preflight.get("manifest"), preflight_required),
        ("fit", fit_bundle, fit.get("manifest"), fit_required),
        ("score", score_bundle, chain.get("manifest"), score_required),
    ):
        require(isinstance(manifest, Mapping), f"verified {phase} manifest missing")
        verify_phase_namespace_clean(bundle)
        verify_manifest_inventory(bundle, manifest, required)

    r2_bundle = preflight.get("r2PreflightBundlePath")
    r2_inventory = preflight.get("r2PreflightBundleInventory")
    r2_failure = preflight.get("r2FitFailurePath")
    r2_failure_pin = preflight.get("r2FitFailurePin")
    r2_success = preflight.get("r2FitSuccessPath")
    require(isinstance(r2_bundle, Path) and isinstance(r2_inventory, Mapping)
            and isinstance(r2_failure, Path) and isinstance(r2_failure_pin, Mapping)
            and isinstance(r2_success, Path),
            "verified r2 ancestry paths/inventory missing")
    require_unlinked_path(r2_bundle, "late r2 preflight bundle", directory=True)
    _bundle_inventory(r2_bundle)
    actual_r2_names = {path.name for path in r2_bundle.iterdir() if path.is_file()}
    require(actual_r2_names == set(r2_inventory)
            and all(path.is_file() and not path.is_symlink()
                    for path in r2_bundle.iterdir()),
            "r2 preflight exact physical inventory drift during late recheck")
    for name, expected in r2_inventory.items():
        require(isinstance(name, str) and isinstance(expected, Mapping),
                "invalid verified r2 preflight inventory")
        verify_pin(r2_bundle / name, expected, f"late r2 preflight {name}")
    verify_pin(r2_failure, r2_failure_pin, "late r2 fit failure")
    require(not os.path.lexists(r2_success),
            "r2 success fit appeared beside the verified failure")


def score_reference_payload(paths: EvaluationPaths, chain: Mapping[str, Any],
                            source_files: Mapping[str, Mapping[str, Any]],
                            predictions: Path) -> dict[str, Any]:
    fit_reference_path = paths.score_manifest.parent / "fit-reference.json"
    return {
        "schemaVersion": "feelm-service-v1-b1-score-reference/2",
        "runId": RUN_ID,
        "profileId": PROFILE_ID,
        "scoreManifest": path_pin(paths.score_manifest, paths.root, paths.team_root),
        "scoreReview": path_pin(paths.score_review, paths.root, paths.team_root),
        "predictions": path_pin(predictions, paths.root, paths.team_root),
        "fitReference": path_pin(fit_reference_path, paths.root, paths.team_root),
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
    _add_source_file(source_files, SCRIPT, paths.root, paths.team_root)
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
        "schemaVersion": "feelm-service-v1-b1-r3-evaluation-input-lock/1",
        "runId": RUN_ID,
        "profileId": PROFILE_ID,
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
        "schemaVersion": "feelm-service-v1-b1-r3-evaluation-result/1",
        "runId": RUN_ID,
        "profileId": PROFILE_ID,
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


def _result_review_path(output_dir: Path) -> Path:
    return output_dir.with_name(output_dir.name + "-result-review.json")


def _namespace_claim_path(output_dir: Path) -> Path:
    return output_dir.with_name("." + output_dir.name + ".publication-claim")


@dataclass(frozen=True)
class _PublicationLease:
    output_dir: Path
    claim: Path
    token: str


def _acquire_namespace_claim(output_dir: Path) -> _PublicationLease:
    """Atomically select one publisher across success and failure names.

    A process crash may leave the claim behind.  That state deliberately
    blocks both publication paths until it is investigated; silently treating
    a stale claim as reclaimable would reopen the cross-name race.
    """
    output_dir = _lexical_absolute(output_dir)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    require_unlinked_path(output_dir.parent, "evaluation publication parent", directory=True)
    claim = _namespace_claim_path(output_dir)
    token = uuid.uuid4().hex
    try:
        with claim.open("x", encoding="ascii", newline="\n") as handle:
            handle.write(token + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as error:
        raise ValueError(f"evaluation namespace already claimed: {claim}") from error
    require_unlinked_path(claim, "evaluation namespace claim")
    return _PublicationLease(output_dir=output_dir, claim=claim, token=token)


def _validate_namespace_lease(lease: _PublicationLease, output_dir: Path) -> None:
    output_dir = _lexical_absolute(output_dir)
    require(lease.output_dir == output_dir
            and lease.claim == _namespace_claim_path(output_dir)
            and isinstance(lease.token, str) and len(lease.token) == 32
            and all(character in "0123456789abcdef" for character in lease.token),
            "evaluation namespace lease identity drift")
    require_unlinked_path(lease.claim, "evaluation namespace claim")
    require(lease.claim.read_text(encoding="ascii") == lease.token + "\n",
            "evaluation namespace claim ownership drift")


def _release_namespace_claim(lease: _PublicationLease) -> None:
    _validate_namespace_lease(lease, lease.output_dir)
    lease.claim.unlink()
    if os.name != "nt":
        descriptor = os.open(lease.claim.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _scan_claimed_namespace(output_dir: Path, lease: _PublicationLease, *,
                            allowed_transients: Sequence[Path] = ()) -> None:
    """Require an exact, claimed namespace before creating a terminal artifact."""
    output_dir = _lexical_absolute(output_dir)
    parent = require_unlinked_path(output_dir.parent, "evaluation publication parent",
                                   directory=True)
    _validate_namespace_lease(lease, output_dir)
    require(not os.path.lexists(output_dir),
            f"preserve existing immutable output: {output_dir}")
    require(not os.path.lexists(_result_review_path(output_dir)),
            f"preserve existing immutable review: {_result_review_path(output_dir)}")
    require(not os.path.lexists(_failure_path(output_dir)),
            f"preserve existing immutable failure: {_failure_path(output_dir)}")
    failure = _failure_path(output_dir)
    review = _result_review_path(output_dir)
    observed = {
        _lexical_absolute(path) for path in parent.iterdir()
        if path.name.startswith((
            "." + output_dir.name + ".tmp-",
            "." + review.name + ".tmp-",
            "." + failure.name + ".tmp-",
        )) or (path.name.startswith("." + output_dir.name + ".")
               and "-scratch-" in path.name)
    }
    allowed = {_lexical_absolute(path) for path in allowed_transients}
    require(observed == allowed,
            "evaluation namespace contains stale or peer temp/scratch state")


def _publish_file_no_replace(temporary: Path, destination: Path) -> None:
    """Atomically publish one immutable file without replacing a peer."""
    require(not os.path.lexists(destination),
            f"preserve existing immutable file: {destination}")
    try:
        os.link(temporary, destination)
    except FileExistsError as error:
        raise ValueError(f"preserve existing immutable file: {destination}") from error
    temporary.unlink()


def _rename_no_replace(source: Path, destination: Path) -> None:
    """Atomically publish a directory while refusing a racing destination."""
    if os.name == "nt":
        os.rename(source, destination)
        return
    import ctypes
    library = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(library, "renameat2", None)
    require(renameat2 is not None,
            "atomic no-replace directory publication unavailable")
    result = renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), str(destination))


def _atomic_failure(output_dir: Path, phase: str, error: BaseException,
                    cleanup_complete: bool,
                    evidence: Mapping[str, Any] | None = None, *,
                    lease: _PublicationLease | None = None) -> None:
    output_dir = _lexical_absolute(output_dir)
    failure = _failure_path(output_dir)
    current_lease = lease if lease is not None else _acquire_namespace_claim(output_dir)
    temporary: Path | None = None
    try:
        _scan_claimed_namespace(output_dir, current_lease)
        temporary = failure.with_name("." + failure.name + ".tmp-" + uuid.uuid4().hex)
        payload = {
            "schemaVersion": "feelm-service-v1-b1-evaluation-failure/1",
            "phase": phase,
            "runId": RUN_ID,
            "profileId": PROFILE_ID,
            "status": "FAILED",
            "errorType": type(error).__name__,
            "error": str(error),
            "cleanupComplete": cleanup_complete,
            "createdAt": utc_now(),
            "evidence": json_ready(dict(evidence or {})),
        }
        write_json(temporary, payload)
        require_unlinked_path(failure.parent, "evaluation failure parent", directory=True)
        require_unlinked_path(temporary, "evaluation failure temporary")
        _scan_claimed_namespace(output_dir, current_lease,
                                allowed_transients=(temporary,))
        _publish_file_no_replace(temporary, failure)
    finally:
        if temporary is not None and os.path.lexists(temporary):
            temporary.unlink()
        _release_namespace_claim(current_lease)


def _new_staging(output_dir: Path) -> tuple[Path, _PublicationLease]:
    output_dir = _lexical_absolute(output_dir)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    require_unlinked_path(output_dir.parent, "evaluation output parent", directory=True)
    lease = _acquire_namespace_claim(output_dir)
    staging: Path | None = None
    staging_created = False
    try:
        _scan_claimed_namespace(output_dir, lease)
        staging = output_dir.parent / ("." + output_dir.name + ".tmp-" + lease.token)
        staging.mkdir()
        staging_created = True
        require_unlinked_path(staging, "evaluation staging directory", directory=True)
        _scan_claimed_namespace(output_dir, lease, allowed_transients=(staging,))
        return staging, lease
    except BaseException:
        if staging_created and staging is not None and os.path.lexists(staging):
            require_unlinked_path(staging, "evaluation failed staging directory",
                                  directory=True)
            shutil.rmtree(staging)
        _release_namespace_claim(lease)
        raise


def _publish(staging: Path, output_dir: Path,
             lease: _PublicationLease) -> None:
    output_dir = _lexical_absolute(output_dir)
    expected_staging = output_dir.parent / ("." + output_dir.name + ".tmp-" + lease.token)
    require(_lexical_absolute(staging) == _lexical_absolute(expected_staging),
            "evaluation staging does not belong to the namespace lease")
    try:
        require_unlinked_path(staging, "evaluation staging directory", directory=True)
        _scan_claimed_namespace(output_dir, lease,
                                allowed_transients=(staging,))
        _rename_no_replace(staging, output_dir)
    except BaseException:
        raise
    _release_namespace_claim(lease)


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
    output_dir = _lexical_absolute(Path(output_dir))
    if paths.root.resolve() == ROOT.resolve():
        expected_output = (ROOT / "outputs/recommendation-evidence/"
                           "service-v1-pretraining-20260913" /
                           EVALUATION_SELECTION_NAME)
        require(output_dir == _lexical_absolute(expected_output),
                "selection output r3 canonical namespace drift")
    staging: Path | None = None
    lease: _PublicationLease | None = None
    failure_evidence: dict[str, Any] = {
        "expectedPlanSha256": expected_plan_sha256,
        "expectedScoreManifestSha256": expected_score_manifest_sha256,
        "expectedScoreReviewSha256": expected_score_review_sha256,
        "scoreManifestPath": str(paths.score_manifest),
        "scoreReviewPath": str(paths.score_review),
        "outputPath": str(output_dir),
    }
    started_at, started = utc_now(), time.monotonic()
    try:
        staging, lease = _new_staging(output_dir)
        (source_files, chain, score_files, predictions_path, als_ids, roles, role_digest,
         training_user_audit) = build_source_state(
            paths, expected_plan_sha256, expected_score_manifest_sha256,
            expected_score_review_sha256, spec, fixed_pins)
        failure_evidence.update({
            "verifiedEvaluationSources": source_files,
            "scoreReference": score_reference_payload(paths, chain, score_files, predictions_path),
        })
        label_size, label_sha256 = fixed_pins["labels"]
        verify_pin(paths.labels, {"bytes": label_size, "sha256": label_sha256},
                   "fixed evaluation labels")
        b1_scores = load_predictions(predictions_path, paths.score_axis, spec)
        truth, integrity = prepare_truth(paths, spec, roles, als_ids, b1_scores)
        integrity["roleMembershipSha256"] = role_digest
        integrity["trainingUserIsolation"] = training_user_audit
        _add_source_file(source_files, paths.labels, paths.root, paths.team_root)
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
            "runId": RUN_ID, "profileId": PROFILE_ID,
            "phase": phase,
            "scoreManifest": path_pin(paths.score_manifest, paths.root, paths.team_root),
            "scoreReview": path_pin(paths.score_review, paths.root, paths.team_root),
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

        reverify_source_inventory(source_files, paths.root, paths.team_root)
        reverify_ancestor_bundle_inventories(chain)
        require(lock["evaluationSourceSetSha256"] == canonical_inventory_digest(source_files),
                "selection source set drift")
        manifest = _manifest_payload(
            staging, phase="SELECTION", status="B1_SELECTION_COMPLETE_AUDIT_PENDING",
            gate_key="requiredSelectionGate", gate_value=str(gates["requiredGate"]),
            evaluator=path_pin(SCRIPT, paths.root, paths.team_root), started_at=started_at,
            seconds=time.monotonic() - started,
            source_digest=lock["evaluationSourceSetSha256"], truth=integrity)
        write_json(staging / "manifest.json", manifest)
        require(_bundle_inventory(staging) == _selection_files() | {"manifest.json"},
                "selection final inventory")
        reverify_source_inventory(source_files, paths.root, paths.team_root)
        reverify_ancestor_bundle_inventories(chain)
        _publish(staging, output_dir, lease)
        staging = None
        lease = None
        return manifest
    except BaseException as error:
        cleanup = True
        if staging is not None and staging.exists():
            try:
                shutil.rmtree(staging)
            except BaseException:
                cleanup = False
        if publish_failure:
            try:
                _atomic_failure(
                    output_dir, phase, error, cleanup, failure_evidence,
                    lease=lease)
            finally:
                lease = None
        elif lease is not None:
            _release_namespace_claim(lease)
            lease = None
        raise


def _resolve_review_target(review_path: Path, record: Mapping[str, Any], root: Path,
                           team_root: Path, actual: Path, label: str) -> None:
    require(isinstance(record, Mapping) and set(record) == {"path", "bytes", "sha256"}
            and isinstance(record.get("path"), str), f"missing or malformed {label} pin")
    resolved = resolve_record_path(record, root=root, team_root=team_root, owner=review_path)
    require(resolved == actual.resolve(), f"{label} path mismatch")
    verify_pin(actual, record, label)


def _parse_review_timestamp(value: Any, label: str) -> datetime:
    require(isinstance(value, str) and value.endswith("Z"), f"{label} timestamp format")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise ValueError(f"{label} timestamp format") from error
    require(parsed.tzinfo is not None, f"{label} timestamp timezone")
    return parsed


def _review_logical_path(path: Path, root: Path, team_root: Path,
                         *, external: Sequence[Path] = (), directory: bool = False) -> str:
    resolved = require_unlinked_path(path, f"review dependency {path}", directory=directory)
    root_resolved = root.resolve()
    team_resolved = team_root.resolve()
    r2_plan = root_resolved / "docs/recommendation/plans/service-v1-b1-spark-runner.md"
    if resolved == r2_plan:
        return "ancestor/service-v1-b1-spark-runner.md"
    if resolved.is_relative_to(team_resolved):
        return "team/" + os.path.relpath(resolved, team_resolved).replace("\\", "/")
    if resolved.is_relative_to(root_resolved):
        return "standalone/" + resolved.relative_to(root_resolved).as_posix()
    if any(resolved == Path(item).resolve() for item in external):
        return os.path.relpath(resolved, root_resolved).replace("\\", "/")
    raise ValueError(f"review dependency outside approved roots: {path}")


def _review_inventory(directory: Path) -> dict[str, dict[str, Any]]:
    root = require_unlinked_path(directory, f"reviewed bundle {directory}", directory=True)
    names = _bundle_inventory(root)
    return {name: pin(root / name) for name in sorted(names)}


def _selection_review_fingerprint(bundle: Path, selection_lock: Mapping[str, Any],
                                  root: Path, team_root: Path,
                                  evaluation_auditor: Path
                                  ) -> dict[str, Any]:
    source_records = selection_lock.get("files")
    phase_records = selection_lock.get("phaseInputs")
    require(isinstance(source_records, Mapping) and isinstance(phase_records, Mapping),
            "selection lock record maps missing")
    require(not phase_records, "selection phase inputs must be empty")
    source_files: dict[str, dict[str, Any]] = {}
    resolved_sources: dict[str, Path] = {}
    seen: set[Path] = set()
    external = (SCRIPT, evaluation_auditor)
    lock_path = bundle / "evaluation-input-lock.json"
    for logical in sorted(source_records):
        record = source_records[logical]
        require(isinstance(logical, str) and isinstance(record, Mapping)
                and set(record) == {"path", "bytes", "sha256"}
                and record.get("path") == logical,
                f"selection source pin shape/key drift: {logical}")
        resolved = resolve_record_path(record, root=root, team_root=team_root, owner=lock_path)
        require(resolved not in seen, f"selection source duplicate resolved path: {logical}")
        seen.add(resolved)
        require(_review_logical_path(resolved, root, team_root, external=external) == logical,
                f"selection source logical path drift: {logical}")
        verify_pin(resolved, record, f"selection source {logical}")
        resolved_sources[logical] = resolved
        source_files[logical] = {"bytes": int(record["bytes"]),
                                 "sha256": str(record["sha256"])}
    require(selection_lock.get("evaluationSourceSetSha256")
            == canonical_inventory_digest(source_files),
            "selection source-set digest drift")
    require(selection_lock.get("inputSetSha256")
            == canonical_inventory_digest(source_files),
            "selection input-set digest drift")

    ancestors: dict[str, dict[str, dict[str, Any]]] = {}
    for path in sorted(set(resolved_sources.values())):
        if path.name != "manifest.json" or path.parent == bundle:
            continue
        try:
            payload = read_json(path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            continue
        if not isinstance(payload, Mapping) or not isinstance(payload.get("status"), str):
            continue
        verify_phase_namespace_clean(path.parent)
        logical_bundle = _review_logical_path(
            path.parent, root, team_root, external=external, directory=True)
        require(logical_bundle not in ancestors,
                f"duplicate ancestor bundle logical path: {logical_bundle}")
        ancestors[logical_bundle] = _review_inventory(path.parent)

    return {
        "bundleInventory": _review_inventory(bundle),
        "sourceFiles": source_files,
        "phaseInputFiles": {},
        "ancestorBundleInventories": {key: ancestors[key] for key in sorted(ancestors)},
        "evaluatorImplementation": path_pin(SCRIPT, root, team_root),
        "auditorImplementation": path_pin(evaluation_auditor, root, team_root),
        "runId": RUN_ID,
        "profileId": PROFILE_ID,
    }


def verify_selection_gate(selection_manifest_path: Path, selection_review_path: Path,
                          expected_manifest_sha256: str, expected_review_sha256: str,
                          root: Path, team_root: Path, evaluation_auditor: Path
                          ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any],
                                     dict[str, dict[str, Any]]]:
    """Validate selection and its review without opening evaluation source data."""
    selection_manifest_path = _lexical_absolute(selection_manifest_path)
    selection_review_path = _lexical_absolute(selection_review_path)
    evaluation_auditor = _lexical_absolute(evaluation_auditor)
    require_unlinked_path(selection_manifest_path, "selection manifest")
    require_unlinked_path(selection_review_path, "selection review")
    require_unlinked_path(evaluation_auditor, "selection independent reviewer")
    verify_sha(selection_manifest_path, expected_manifest_sha256, "selection manifest")
    verify_sha(selection_review_path, expected_review_sha256, "selection review")
    require(selection_manifest_path.name == "manifest.json", "selection manifest basename")
    bundle = selection_manifest_path.parent
    if root.resolve() == ROOT.resolve():
        expected_bundle = (ROOT / "outputs/recommendation-evidence/"
                           "service-v1-pretraining-20260913" /
                           EVALUATION_SELECTION_NAME)
        require(_lexical_absolute(selection_manifest_path)
                == _lexical_absolute(expected_bundle / "manifest.json")
                and _lexical_absolute(selection_review_path)
                == _lexical_absolute(expected_bundle.with_name(
                    expected_bundle.name + "-result-review.json")),
                "selection bundle/review noncanonical lexical path")
        require(evaluation_auditor == _lexical_absolute(EVALUATION_AUDITOR),
                "selection independent reviewer noncanonical lexical path")
        require(valid_sha256(REVIEWED_R3_EVALUATION_AUDITOR_CORE_SHA256)
                and REVIEWED_R3_EVALUATION_AUDITOR_CORE_SHA256
                != EVALUATION_AUDITOR_PIN_SENTINEL,
                "PUBLIC CONFIRMATION BLOCKED: r3 evaluation auditor core is unsealed")
        verify_evaluation_auditor_binding(
            evaluation_auditor, SCRIPT,
            REVIEWED_R3_EVALUATION_AUDITOR_CORE_SHA256)
    else:
        # Synthetic/private verification still requires an unambiguous audited
        # implementation shape, but does not reuse the production core pin.
        evaluation_auditor_contract(evaluation_auditor)
    require(selection_review_path
            == _lexical_absolute(bundle.with_name(bundle.name + "-result-review.json")),
            "selection review is not canonical sibling")
    verify_phase_namespace_clean(bundle)
    manifest = read_json(selection_manifest_path)
    review = read_json(selection_review_path)
    manifest_keys = {
        "schemaVersion", "runId", "profileId", "phase", "status",
        "requiredSelectionGate", "confirmationAuthorized", "readyForService",
        "serviceActivationAuthorized", "freshBlindHoldout", "evaluator",
        "evaluationSourceSetSha256", "truthCensus", "startedAt", "completedAt",
        "seconds", "runtime", "files",
    }
    require(isinstance(manifest, Mapping) and set(manifest) == manifest_keys,
            "selection manifest field set drift")
    require(manifest.get("schemaVersion") == "feelm-service-v1-b1-r3-evaluation-result/1"
            and manifest.get("runId") == RUN_ID and manifest.get("profileId") == PROFILE_ID
            and manifest.get("phase") == "SELECTION"
            and manifest.get("status") == "B1_SELECTION_COMPLETE_AUDIT_PENDING",
            "selection manifest identity/status drift")
    require(manifest.get("requiredSelectionGate") == "PASS", "selection required gate must PASS")
    require(manifest.get("confirmationAuthorized") is False
            and manifest.get("readyForService") is False
            and manifest.get("serviceActivationAuthorized") is False
            and manifest.get("freshBlindHoldout") is False,
            "selection manifest premature authorization")
    _parse_review_timestamp(manifest.get("startedAt"), "selection manifest startedAt")
    _parse_review_timestamp(manifest.get("completedAt"), "selection manifest completedAt")
    seconds = manifest.get("seconds")
    require(type(seconds) in (int, float) and math.isfinite(float(seconds))
            and float(seconds) >= 0, "selection manifest seconds invalid")
    runtime = manifest.get("runtime")
    require(isinstance(runtime, Mapping)
            and set(runtime) == {"python", "numpy", "pandas", "pyarrow"}
            and all(isinstance(value, str) and value for value in runtime.values()),
            "selection manifest runtime schema drift")
    review_keys = {
        "schemaVersion", "runId", "profileId", "phase", "status", "createdAt",
        "target", "reviewer", "dependencyFingerprint", "checks", "decision",
        "evaluationInputsRead", "modelFitPerformed", "readyForService", "scope",
    }
    require(isinstance(review, Mapping) and set(review) == review_keys,
            "selection independent review field set drift")
    require(review.get("schemaVersion")
            == "feelm-service-v1-b1-r3-evaluation-result-review/1"
            and review.get("runId") == RUN_ID and review.get("profileId") == PROFILE_ID
            and review.get("status") == "PASS",
            "selection independent review identity/status drift")
    require(review.get("phase") == "selection", "selection independent review phase drift")
    _parse_review_timestamp(review.get("createdAt"), "selection review createdAt")
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
        _remember_file(verified, actual, root, team_root)
    _remember_file(verified, selection_review_path, root, team_root)
    expected_reviewer = {
        "implementation": path_pin(evaluation_auditor, root, team_root),
        "independentFromEvaluator": True,
    }
    require(review.get("reviewer") == expected_reviewer,
            "selection independent reviewer identity/pin drift")
    gates = read_json(bundle / "gates.json")
    require(gates.get("requiredGate") == "PASS", "selection gates file must PASS")
    selection_lock = read_json(bundle / "evaluation-input-lock.json")
    selection_truth = read_json(bundle / "truth-integrity.json")
    lock_keys = {
        "schemaVersion", "runId", "profileId", "phase", "files", "phaseInputs",
        "evaluationSourceSetSha256", "inputSetSha256", "labelsPreviouslyOpened",
        "freshBlindHoldout", "fitOrScoreMutationAuthorized",
    }
    require(isinstance(selection_lock, Mapping) and set(selection_lock) == lock_keys,
            "selection input lock field set drift")
    require(selection_lock.get("schemaVersion")
            == "feelm-service-v1-b1-r3-evaluation-input-lock/1"
            and selection_lock.get("runId") == RUN_ID
            and selection_lock.get("profileId") == PROFILE_ID
            and selection_lock.get("phase") == "SELECTION",
            "selection input lock contract drift")
    require(selection_lock.get("labelsPreviouslyOpened") is True
            and selection_lock.get("freshBlindHoldout") is False
            and selection_lock.get("fitOrScoreMutationAuthorized") is False,
            "selection input lock disclosure/authorization drift")
    require(manifest.get("evaluationSourceSetSha256")
            == selection_lock.get("evaluationSourceSetSha256"),
            "selection manifest/input-lock source digest drift")
    require(manifest.get("truthCensus") == selection_truth,
            "selection manifest/truth census drift")
    command = read_json(bundle / "command.json")
    bootstrap_samples = command.get("bootstrapSamples")
    require(type(bootstrap_samples) is int and bootstrap_samples > 0,
            "selection command bootstrap sample contract drift")
    joined_rows = selection_truth.get("joinedRows")
    require(type(joined_rows) is int and joined_rows >= 0,
            "selection truth joinedRows contract drift")
    expected_checks = {
        "exactOutputInventory": True,
        "allInputPinsRehashed": True,
        "r2AncestorChainVerified": True,
        "truthRebuilt": True,
        "affineIndependentlyRecomputed": True,
        "metricsIndependentlyRecomputed": True,
        "bootstrapSamples": bootstrap_samples,
        "gateIndependentlyRecomputed": True,
        "truthRows": joined_rows,
    }
    require(review.get("checks") == expected_checks,
            "selection independent review checks drift")
    expected_decision = {
        "selectionGate": "PASS",
        "confirmationEligible": True,
        "logicalState": "B1_SELECTION_AUDITED_CONFIRMATION_ELIGIBLE",
    }
    require(review.get("decision") == expected_decision,
            "selection independent review decision drift")
    require(review.get("evaluationInputsRead") is True
            and review.get("modelFitPerformed") is False
            and review.get("readyForService") is False
            and review.get("scope")
            == "Local immutable evaluation integrity and independent metric/gate parity; no service activation.",
            "selection independent review safety/scope drift")
    affine = read_json(bundle / "affine.json")
    require(set(affine.get("models", {})) == set(MODELS), "selection affine model set")
    for model in MODELS:
        record = affine["models"][model]
        require(record.get("state") == "AFFINE" and float(record.get("b", -1)) >= 0,
                f"selection {model} affine unavailable")
    require(manifest.get("evaluator") == path_pin(SCRIPT, root, team_root),
            "selection manifest evaluator drift")
    # Only after every source-free authorization field is valid may the
    # confirmation gate reopen and rehash the selection's source closure.
    expected_fingerprint = _selection_review_fingerprint(
        bundle, selection_lock, root, team_root, evaluation_auditor)
    require(review.get("dependencyFingerprint") == expected_fingerprint,
            "selection independent review dependency fingerprint drift")
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
    output_dir = _lexical_absolute(Path(output_dir))
    if paths.root.resolve() == ROOT.resolve():
        expected_output = (ROOT / "outputs/recommendation-evidence/"
                           "service-v1-pretraining-20260913" /
                           EVALUATION_CONFIRMATION_NAME)
        require(output_dir == _lexical_absolute(expected_output),
                "confirmation output r3 canonical namespace drift")
    selection_manifest_path = _lexical_absolute(Path(selection_manifest_path))
    selection_review_path = _lexical_absolute(Path(selection_review_path))
    staging: Path | None = None
    lease: _PublicationLease | None = None
    failure_evidence: dict[str, Any] = {
        "expectedPlanSha256": expected_plan_sha256,
        "expectedScoreManifestSha256": expected_score_manifest_sha256,
        "expectedScoreReviewSha256": expected_score_review_sha256,
        "expectedSelectionManifestSha256": expected_selection_manifest_sha256,
        "expectedSelectionReviewSha256": expected_selection_review_sha256,
        "scoreManifestPath": str(paths.score_manifest),
        "scoreReviewPath": str(paths.score_review),
        "selectionManifestPath": str(selection_manifest_path),
        "selectionReviewPath": str(selection_review_path),
        "outputPath": str(output_dir),
    }
    started_at, started = utc_now(), time.monotonic()
    try:
        staging, lease = _new_staging(output_dir)
        # This is the first gate and intentionally touches only the already-published
        # selection bundle/review and evaluator, never labels or evaluation sources.
        selection_manifest, selection_review, affine_payload, verified_selection_files = verify_selection_gate(
            selection_manifest_path, selection_review_path,
            expected_selection_manifest_sha256, expected_selection_review_sha256,
            paths.root, paths.team_root, paths.evaluation_auditor)
        (source_files, chain, score_files, predictions_path, als_ids, roles, role_digest,
         training_user_audit) = build_source_state(
            paths, expected_plan_sha256, expected_score_manifest_sha256,
            expected_score_review_sha256, spec, fixed_pins)
        failure_evidence.update({
            "verifiedEvaluationSources": source_files,
            "scoreReference": score_reference_payload(paths, chain, score_files, predictions_path),
            "verifiedSelectionInputs": verified_selection_files,
        })
        label_size, label_sha256 = fixed_pins["labels"]
        verify_pin(paths.labels, {"bytes": label_size, "sha256": label_sha256},
                   "fixed evaluation labels")
        b1_scores = load_predictions(predictions_path, paths.score_axis, spec)
        truth, integrity = prepare_truth(paths, spec, roles, als_ids, b1_scores)
        integrity["roleMembershipSha256"] = role_digest
        integrity["trainingUserIsolation"] = training_user_audit
        _add_source_file(source_files, paths.labels, paths.root, paths.team_root)
        selection_bundle = selection_manifest_path.parent
        evaluator_key = str(path_pin(SCRIPT, paths.root, paths.team_root)["path"])
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
            "schemaVersion": "feelm-service-v1-b1-r3-selection-reference/1",
            "runId": RUN_ID,
            "profileId": PROFILE_ID,
            "selectionManifest": path_pin(selection_manifest_path, paths.root, paths.team_root),
            "selectionReview": path_pin(selection_review_path, paths.root, paths.team_root),
            "evaluationInputLock": path_pin(
                selection_bundle / "evaluation-input-lock.json", paths.root, paths.team_root),
            "truthIntegrity": path_pin(
                selection_bundle / "truth-integrity.json", paths.root, paths.team_root),
            "scoreReference": path_pin(
                selection_bundle / "score-reference.json", paths.root, paths.team_root),
            "command": path_pin(selection_bundle / "command.json", paths.root, paths.team_root),
            "roleMembership": path_pin(
                selection_bundle / "role-membership.csv", paths.root, paths.team_root),
            "affine": path_pin(selection_bundle / "affine.json", paths.root, paths.team_root),
            "metrics": path_pin(selection_bundle / "metrics.json", paths.root, paths.team_root),
            "bootstrapSummary": path_pin(
                selection_bundle / "bootstrap-summary.json", paths.root, paths.team_root),
            "strataCensus": path_pin(
                selection_bundle / "strata-census.json", paths.root, paths.team_root),
            "gates": path_pin(selection_bundle / "gates.json", paths.root, paths.team_root),
            "runLog": path_pin(selection_bundle / "run.log", paths.root, paths.team_root),
            "evaluator": path_pin(SCRIPT, paths.root, paths.team_root),
            "requiredSelectionGate": "PASS",
            "selectionReviewStatus": selection_review.get("status"),
        }
        write_json(staging / "selection-reference.json", selection_reference)
        write_json(staging / "score-reference.json", current_score_reference)
        write_json(staging / "evaluation-input-lock.json", lock)
        write_json(staging / "truth-integrity.json", integrity)
        write_json(staging / "command.json", {
            "runId": RUN_ID, "profileId": PROFILE_ID, "phase": phase,
            "selectionManifest": path_pin(
                selection_manifest_path, paths.root, paths.team_root),
            "selectionReview": path_pin(
                selection_review_path, paths.root, paths.team_root),
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

        reverify_source_inventory(source_files, paths.root, paths.team_root)
        reverify_ancestor_bundle_inventories(chain)
        require(lock["evaluationSourceSetSha256"] == canonical_inventory_digest(source_files),
                "confirmation source set drift")
        (_, _, final_affine, final_selection_files) = verify_selection_gate(
            selection_manifest_path, selection_review_path,
            expected_selection_manifest_sha256, expected_selection_review_sha256,
            paths.root, paths.team_root, paths.evaluation_auditor)
        require(final_affine == affine_payload, "selection affine changed during confirmation")
        require(final_selection_files == verified_selection_files,
                "selection reviewed file set changed during confirmation")
        manifest = _manifest_payload(
            staging, phase="CONFIRMATION", status="B1_CONFIRMATION_COMPLETE_AUDIT_PENDING",
            gate_key="requiredConfirmationGate", gate_value=str(gates["requiredGate"]),
            evaluator=path_pin(SCRIPT, paths.root, paths.team_root), started_at=started_at,
            seconds=time.monotonic() - started,
            source_digest=lock["evaluationSourceSetSha256"], truth=integrity)
        write_json(staging / "manifest.json", manifest)
        require(_bundle_inventory(staging) == _confirmation_files() | {"manifest.json"},
                "confirmation final inventory")
        reverify_source_inventory(source_files, paths.root, paths.team_root)
        reverify_ancestor_bundle_inventories(chain)
        _publish(staging, output_dir, lease)
        staging = None
        lease = None
        return manifest
    except BaseException as error:
        cleanup = True
        if staging is not None and staging.exists():
            try:
                shutil.rmtree(staging)
            except BaseException:
                cleanup = False
        if publish_failure:
            try:
                _atomic_failure(
                    output_dir, phase, error, cleanup, failure_evidence,
                    lease=lease)
            finally:
                lease = None
        elif lease is not None:
            _release_namespace_claim(lease)
            lease = None
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
    require_public_r3_contract_sealed()
    require(args.expected_plan_sha256.lower() == PLAN_SHA256,
            "CLI plan pin differs from independently reviewed plan")
    paths = production_paths(args.score_manifest, args.score_review)
    output_parent = ROOT / "outputs/recommendation-evidence/service-v1-pretraining-20260913"
    expected_output = output_parent / (EVALUATION_SELECTION_NAME
                                       if args.action == "calibrate-select"
                                       else EVALUATION_CONFIRMATION_NAME)
    require(_lexical_absolute(args.output_dir) == _lexical_absolute(expected_output),
            f"noncanonical r3 evaluation output path: expected {expected_output}")
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
