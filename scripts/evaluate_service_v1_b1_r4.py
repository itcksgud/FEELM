"""Server r4 evaluation. Pure statistics preserve the reviewed r3 contract.

No evaluation file is touched until server score/selection review and fresh host
supervision gates pass. Publication belongs to the outside supervisor after the
independent cgroup monitor exits; the evaluator never invents resource evidence.
"""
from __future__ import annotations
import argparse
import ast
import csv
from dataclasses import dataclass
import datetime as dt
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import stat
import sys
from typing import Any, Iterable, Mapping, Sequence
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as pads
import pyarrow.parquet as pq

SCRIPT = Path(os.path.abspath(__file__))
ROOT = SCRIPT.parents[1]
TEAM_ROOT = ROOT.parent / "S15P21E106"
RUN_ID = "b1-gbt120-s339-v1-r4-server5c20g-t28800"
PROFILE_ID = "ec2-8vcpu32g-local5-fit20g-t28800-v1"
PLAN = ROOT / "docs/recommendation/plans/service-v1-b1-r4-server-fit-recovery.md"
PROFILE = ROOT / "docs/recommendation/plans/service-v1-b1-r4-ec2-8vcpu32g-local5c20g-t28800-profile.json"
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
ROLE_DIGEST = "cf92b686ab21a950611084682e46bfd319426d5dbc3c4f58836a4836b3f416c6"
EVALUATION_AUDITOR_PIN_SENTINEL = "0" * 64
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


REVIEWED_R4_EVALUATION_AUDITOR_CORE_SHA256: str = "bd6f028bc03bcf86c611309a2824fbb7f1efc8ea143ba2a6ec1ee1d658234d38"

class ContractError(ValueError):
    """Immutable input/output or authorization contract violation."""

def require(condition: Any, message: str) -> None:
    if not condition:
        raise ContractError(message)

def valid_sha256(value: Any) -> bool:
    return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)

def require_unlinked_path(path: Path, label: str, *, directory: bool = False) -> Path:
    path = Path(os.path.abspath(os.fspath(path)))
    for parent in (path, *path.parents):
        info = parent.lstat()
        require(not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 1024,
                f"{label}: linked/reparse path")
    info = path.stat()
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode),
            f"{label}: invalid file type")
    if not directory:
        require(info.st_nlink == 1, f"{label}: hard-link alias")
    return path

def sha256_file(path: Path) -> str:
    path = require_unlinked_path(path, "hash input")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024**2), b""):
            digest.update(block)
    return digest.hexdigest()

def pin(path: Path) -> dict[str, Any]:
    path = require_unlinked_path(path, "pin input")
    return {"bytes": path.stat().st_size, "sha256": sha256_file(path)}

def path_pin(path: Path, root: Path = ROOT, team_root: Path | None = None) -> dict[str, Any]:
    return {"path": str(Path(os.path.abspath(path))), **pin(path)}

def verify_pin(path: Path, expected: Mapping[str, Any], label: str) -> dict[str, Any]:
    require(type(expected.get("bytes")) is int and expected["bytes"] >= 0 and
            valid_sha256(expected.get("sha256")), f"{label}: malformed pin")
    observed = pin(path)
    require(observed == {"bytes": expected["bytes"], "sha256": expected["sha256"]},
            f"{label}: current bytes/SHA drift")
    return observed

def verify_sha(path: Path, expected: str, label: str) -> dict[str, Any]:
    require(valid_sha256(expected) and expected != "0" * 64, f"{label}: malformed expected SHA")
    observed = pin(path)
    require(observed["sha256"] == expected, f"{label}: current SHA drift")
    return observed

def read_json(path: Path) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        value = {}
        for key, item in items:
            require(key not in value, "duplicate JSON key")
            value[key] = item
        return value
    def nonfinite(text: str) -> None:
        raise ContractError("nonfinite JSON: " + text)
    path = require_unlinked_path(path, "JSON input")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs,
                      parse_constant=nonfinite)

def _bundle_inventory(directory: Path) -> set[str]:
    directory = require_unlinked_path(directory, "bundle", directory=True)
    files = set()
    for member in directory.rglob("*"):
        if member.is_dir():
            require_unlinked_path(member, "bundle directory", directory=True)
        else:
            require_unlinked_path(member, "bundle child")
            files.add(member.relative_to(directory).as_posix())
    return files

def canonical_record_set_digest(records: Iterable[Mapping[str, Any]]) -> str:
    records = list(records)
    paths = [record["path"] for record in records]
    require(len(paths) == len(set(paths)), "duplicate dependency path")
    for record in records:
        require(set(record) == {"path", "bytes", "sha256"} and type(record["path"]) is str
                and "\x00" not in record["path"] and "\n" not in record["path"]
                and type(record["bytes"]) is int and record["bytes"] >= 0
                and valid_sha256(record["sha256"]), "invalid dependency record")
    data = "".join(f"{r['path']}\0{r['bytes']}\0{r['sha256']}\n"
                   for r in sorted(records, key=lambda x: x["path"]))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()

def write_text(path: Path, text: str) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())

def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    write_text(path, json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2,
                               allow_nan=False) + "\n")

def evaluation_auditor_contract(path: Path) -> tuple[str, str]:
    """Hash all auditor source except its unavoidable evaluator-SHA literal.

    The source must contain exactly one top-level annotated assignment named
    ``REVIEWED_R4_EVALUATOR_SHA256`` whose value is a plain 64-hex string.
    Only that literal's 64 characters are replaced with a fixed sentinel.
    Duplicate assignments, computed values, prefixes, and multiline literals
    are rejected rather than normalized ambiguously.
    """
    path = require_unlinked_path(path, "r4 evaluation auditor")
    raw = path.read_bytes()
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("r4 evaluation auditor is not UTF-8") from error
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as error:
        raise ValueError("r4 evaluation auditor is not valid Python") from error
    matches = [
        node for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "REVIEWED_R4_EVALUATOR_SHA256"
    ]
    stores = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
        and node.id == "REVIEWED_R4_EVALUATOR_SHA256"
    ]
    require(len(matches) == 1,
            "r4 evaluation auditor evaluator pin must be one top-level annotated assignment")
    assignment = matches[0]
    require(len(stores) == 1 and stores[0] is assignment.target,
            "r4 evaluation auditor evaluator-pin binding must be unique")
    value = assignment.value  # type: ignore[attr-defined]
    require(isinstance(value, ast.Constant) and isinstance(value.value, str)
            and valid_sha256(value.value),
            "r4 evaluation auditor evaluator pin must be a literal lowercase SHA-256")
    require(value.lineno == value.end_lineno,
            "r4 evaluation auditor evaluator pin literal must be one line")
    lines = source.splitlines(keepends=True)
    line = lines[value.lineno - 1]
    segment = line[value.col_offset:value.end_col_offset]
    require(len(segment) == 66 and segment[0] in {'"', "'"}
            and segment[-1] == segment[0]
            and segment[1:-1] == value.value,
            "r4 evaluation auditor evaluator pin must be a plain string literal")
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
            "r4 evaluation auditor expected core SHA-256 is invalid")
    core_sha256, embedded_evaluator_sha256 = evaluation_auditor_contract(path)
    evaluator = require_unlinked_path(evaluator, "r4 evaluator implementation")
    require(core_sha256 == expected_core_sha256,
            "r4 evaluation auditor reviewed core SHA-256 drift")
    require(embedded_evaluator_sha256 == sha256_file(evaluator),
            "r4 evaluation auditor embeds a stale evaluator SHA-256")



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


# The coordinator owns process creation, dynamic probes and external monitoring.
# These are phase contracts, not permission flags callers can set to skip checks.
CONTROL_NAMES = (
    "score_manifest", "score_review", "delivery_manifest", "delivery_review",
    "server_receipt_manifest", "server_receipt_review", "host_runtime_lock",
)
EVALUATION_EXTRA_FILES = {
    "delivery-reference.json", "server-receipt-reference.json", "host-gate.json",
    "evaluator-resource.json",
}
SELECTION_FILES = {
    "evaluation-input-lock.json", "truth-integrity.json", "score-reference.json",
    "command.json", "role-membership.csv", "affine.json", "metrics.json",
    "bootstrap-summary.json", "strata-census.json", "gates.json", "run.log",
    "manifest.json",
} | EVALUATION_EXTRA_FILES
CONFIRMATION_FILES = {
    "selection-reference.json", "score-reference.json", "evaluation-input-lock.json",
    "truth-integrity.json", "command.json", "metrics.json", "bootstrap-summary.json",
    "strata-census.json", "gates.json", "run.log", "manifest.json",
} | EVALUATION_EXTRA_FILES


@dataclass(frozen=True)
class ControlPaths:
    score_manifest: Path
    score_review: Path
    delivery_manifest: Path
    delivery_review: Path
    server_receipt_manifest: Path
    server_receipt_review: Path
    host_runtime_lock: Path


def phase_inventory(phase: str) -> set[str]:
    require(phase in {"selection", "confirmation"}, "unknown evaluation phase")
    return set(SELECTION_FILES if phase == "selection" else CONFIRMATION_FILES)


def _review_manifest_target(review: Mapping[str, Any], manifest: Path) -> None:
    target = review.get("target", {}).get("manifest")
    require(type(target) is dict and set(target) == {"path", "bytes", "sha256"},
            "review manifest target shape")
    absolute = Path(os.path.abspath(manifest))
    canonical = {str(absolute)}
    for prefix, root in (("standalone", ROOT), ("team", TEAM_ROOT)):
        if root in absolute.parents:
            canonical.add(prefix + "/" + absolute.relative_to(root).as_posix())
    require(target["path"] in canonical, "review manifest target canonical path")
    verify_pin(manifest, target, "review target manifest")


def canonical_control_paths(root: Path) -> dict[str, Path]:
    """Lexical control names; validation here must not resolve evaluation paths."""
    parent = root / "outputs/recommendation-evidence/service-v1-pretraining-20260913"
    receipt = parent / (RUN_ID + "-server-receipt")
    return {"score_manifest": parent / (RUN_ID + "-score") / "manifest.json",
            "score_review": parent / (RUN_ID + "-score-result-review.json"),
            "delivery_manifest": parent / (RUN_ID + "-delivery-manifest.json"),
            "delivery_review": parent / (RUN_ID + "-delivery-manifest-result-review.json"),
            "server_receipt_manifest": receipt / "manifest.json",
            "server_receipt_review": parent / (RUN_ID + "-server-receipt-result-review.json"),
            "host_runtime_lock": receipt / "runtime/service-v1-b1-r4-host-runtime-lock.json"}


def verify_score_review_gate(controls: ControlPaths,
                             expected_sha256: Mapping[str, str]) -> dict[str, Any]:
    """Read control documents only; never resolve/stat/hash an evaluation input.

    These expected SHA arguments are mandatory external trust roots. This first
    gate is intentionally separate from full ancestry/runtime/host validation so
    a denied score cannot cause label or role files even to be stat'ed.
    """
    require(set(expected_sha256) == set(CONTROL_NAMES), "control expected SHA set")
    for name in CONTROL_NAMES:
        require(valid_sha256(expected_sha256[name]) and expected_sha256[name] != "0" * 64,
                "invalid expected control SHA: " + name)
    # Score authorization is examined first, before even unrelated control IO.
    records: dict[str, Any] = {}
    payloads: dict[str, Any] = {}
    for name in CONTROL_NAMES:
        path = getattr(controls, name)
        verify_sha(path, expected_sha256[name], name)
        payload = read_json(path)
        require(type(payload) is dict, name + " object required")
        if name != "host_runtime_lock":
            require(payload.get("runId") == RUN_ID and payload.get("profileId") == PROFILE_ID,
                    name + " wrong run/profile")
        if name == "score_review":
            require(payload.get("status") == "PASS"
                    and payload.get("decision", {}).get("evaluationSelectionEligible") is True,
                    "score review does not authorize selection")
            _review_manifest_target(payload, controls.score_manifest)
        if name == "score_manifest":
            require(payload.get("evaluationAuthorized") is False
                    and payload.get("evaluationTargetsRead") is False
                    and payload.get("labelProjected") is False
                    and payload.get("readyForService") is False,
                    "score manifest label/authorization contract")
        if name == "delivery_review":
            require(payload.get("schemaVersion") == "feelm-service-v1-b1-r4-server-delivery-review/1"
                    and payload.get("status") == "PASS"
                    and payload.get("decision", {}).get("serverTransferEligible") is True,
                    "delivery review gate")
            _review_manifest_target(payload, controls.delivery_manifest)
        if name == "server_receipt_review":
            require(payload.get("schemaVersion") == "feelm-service-v1-b1-r4-server-receipt-review/1"
                    and payload.get("status") == "PASS"
                    and payload.get("decision", {}).get("publicPreflightEligible") is True,
                    "receipt review gate")
            _review_manifest_target(payload, controls.server_receipt_manifest)
        if name == "host_runtime_lock":
            require(payload.get("schemaVersion") == "feelm-service-v1-b1-r4-host-runtime-lock/1",
                    "host runtime lock schema")
        records[name] = path_pin(path)
        payloads[name] = payload
    return {"records": records, "payloads": payloads,
            "controlSetSha256": canonical_record_set_digest(records.values())}


def rehash_records(records: Iterable[Mapping[str, Any]]) -> str:
    records = list(records)
    for record in records:
        verify_pin(Path(record["path"]), record, "current dependency")
    return canonical_record_set_digest(records)


def verify_selection_review_gate(manifest_path: Path, review_path: Path,
                                 expected_manifest_sha256: str,
                                 expected_review_sha256: str) -> dict[str, Any]:
    """Confirmation authorization, before any evaluation input is accessed."""
    verify_sha(manifest_path, expected_manifest_sha256, "selection manifest")
    verify_sha(review_path, expected_review_sha256, "selection review")
    manifest, review = read_json(manifest_path), read_json(review_path)
    require(manifest.get("runId") == review.get("runId") == RUN_ID
            and manifest.get("profileId") == review.get("profileId") == PROFILE_ID,
            "selection run/profile drift")
    require(manifest.get("phase") == "SELECTION" and manifest.get("requiredSelectionGate") == "PASS"
            and manifest.get("confirmationAuthorized") is False
            and manifest.get("readyForService") is False, "selection manifest gate")
    require(review.get("schemaVersion") == "feelm-service-v1-b1-r4-evaluation-result-review/1"
            and review.get("status") == "PASS" and review.get("phase") == "selection",
            "selection review envelope")
    require(review.get("decision") == {
        "selectionGate": "PASS", "confirmationEligible": True,
        "logicalState": "B1_SELECTION_AUDITED_CONFIRMATION_ELIGIBLE"},
        "selection review decision")
    _review_manifest_target(review, manifest_path)
    return {"manifest": path_pin(manifest_path), "review": path_pin(review_path)}


def validate_phase_truth(frame: pd.DataFrame) -> None:
    required = {"row_id", "uid", "movie_id", "role", "b0_raw", "b1_raw", "als_supported", "rating"}
    require(set(frame.columns) == required and len(frame) > 0, "phase truth schema/empty")
    require(not frame.isna().any().any() and not frame.duplicated(["uid", "movie_id"]).any()
            and not frame.row_id.duplicated().any(), "phase truth missing/duplicate")
    require(valid_half_stars(frame.rating.to_numpy())
            and np.isfinite(frame[["b0_raw", "b1_raw"]].to_numpy()).all(), "phase truth values")
    require(frame.als_supported.dtype == bool, "ALS support boolean required")
    require(set(frame.role) == {"CALIBRATION", "SELECTION", "CONFIRMATION"}, "phase truth roles")


def compute_phase_outputs(phase: str, truth: pd.DataFrame, spec: EvaluationSpec,
                          selection_affine: Mapping[str, Any] | None = None
                          ) -> tuple[dict, dict, dict, dict, dict | None]:
    validate_phase_truth(truth)
    require(phase in {"selection", "confirmation"}, "unknown evaluation phase")
    if phase == "confirmation":
        require(type(selection_affine) is dict and selection_affine.get("schemaVersion")
                == "feelm-service-v1-b1-affine/1", "frozen selection affine required")
        target = truth[truth.role.eq("CONFIRMATION")]
        require(target.uid.nunique() == spec.confirmation_users, "confirmation user census")
        return (*evaluate_phase(target, selection_affine["models"], spec), None)
    calibration, target = truth[truth.role.eq("CALIBRATION")], truth[truth.role.eq("SELECTION")]
    require(calibration.uid.nunique() == spec.calibration_users
            and target.uid.nunique() == spec.selection_users, "calibration/selection user census")
    affine = {"schemaVersion": "feelm-service-v1-b1-affine/1", "fitRole": "CALIBRATION",
              "userEqualWeight": True, "comparisonLabelsUsedForFitting": False,
              "models": {"B0": fit_affine_user_equal(calibration, "b0_raw"),
                         "B1": fit_affine_user_equal(calibration, "b1_raw")}}
    if all(affine["models"][model]["state"] == "AFFINE" for model in MODELS):
        return (*evaluate_phase(target, affine["models"], spec), affine)
    census = {"phaseUsers": int(target.uid.nunique()), "phaseRows": len(target),
              "strata": {name: {"users": int(part.uid.nunique()), "movies": int(part.movie_id.nunique()),
                                "rows": len(part), "status": "INSUFFICIENT"}
                         for name, part in stratum_frames(target).items()}}
    return ({"models": {}, "pointDeltas": {}, "state": "CALIBRATION_UNAVAILABLE"},
            {"state": "CALIBRATION_UNAVAILABLE", "userBlock": {},
             "movieBlockSensitivity": {}, "signFlipHolm": None}, census,
            {"requiredGate": "INSUFFICIENT", "items": {"affine": {
                "status": "INSUFFICIENT", "models": affine["models"]}},
             "serviceActivationAuthorized": False}, affine)


def source_state(paths: EvaluationPaths, spec: EvaluationSpec,
                 fixed_pins: Mapping[str, tuple[int, str]]) -> tuple[dict, set[int], dict, str]:
    require(set(fixed_pins) == set(SOURCE_PINS), "evaluation source pin set")
    records = {}
    for name, (size, sha) in fixed_pins.items():
        path = getattr(paths, name)
        verify_pin(path, {"bytes": size, "sha256": sha}, "fixed " + name)
        records[name] = path_pin(path)
    als_ids, als_records = verify_als_axis(paths, spec)
    records.update({"als:" + name: record for name, record in als_records.items()})
    roles, digest = split_roles(pd.read_csv(paths.roles), spec)
    return records, als_ids, roles, digest


def prepare_phase(paths: EvaluationPaths, staging: Path, phase: str, *,
                  control_state: Mapping[str, Any], host_gate: Mapping[str, Any],
                  predictions: Path, dependency_records: Sequence[Mapping[str, Any]],
                  selection_reference: Mapping[str, Any] | None = None,
                  _spec: EvaluationSpec | None = None,
                  _fixed_pins: Mapping[str, tuple[int, str]] | None = None) -> dict[str, Any]:
    """Compute into an owned supervisor staging directory, never publish it.

    The outside supervisor is responsible for acquiring the shared lease and
    verifying the complete server gate immediately before starting this worker.
    Private fixture arguments never appear in the public CLI.
    """
    spec = _spec or EvaluationSpec()
    require(phase in {"selection", "confirmation"}, "unknown evaluation phase")
    require(host_gate.get("schemaVersion") == "feelm-service-v1-b1-r4-dynamic-host-gate/2"
            and host_gate.get("status") == "PASS" and host_gate.get("runId") == RUN_ID
            and host_gate.get("profileId") == PROFILE_ID
            and host_gate.get("phase") == ("calibrate-select" if phase == "selection" else "confirmation")
            and host_gate.get("unknownFields") == [], "fresh supervised host gate required")
    records = dict(control_state["records"])
    require(set(records) == set(CONTROL_NAMES), "verified control inventory")
    require(rehash_records(records.values()) == control_state["controlSetSha256"], "control source drift")
    score = read_json(Path(records["score_review"]["path"]))
    require(score.get("status") == "PASS" and score.get("decision", {}).get("evaluationSelectionEligible") is True,
            "score review gate revoked")
    affine = None
    if phase == "confirmation":
        require(selection_reference is not None, "confirmation selection reference missing")
        reference = verify_selection_review_gate(
            Path(selection_reference["manifest"]["path"]), Path(selection_reference["review"]["path"]),
            selection_reference["manifest"]["sha256"], selection_reference["review"]["sha256"])
        require(reference == selection_reference, "selection reference pin drift")
        selection_dir = Path(reference["manifest"]["path"]).parent
        manifest = read_json(selection_dir / "manifest.json")
        verify_pin(selection_dir / "affine.json", manifest["files"]["affine.json"], "selection affine")
        affine = read_json(selection_dir / "affine.json")
    # First evaluation-input filesystem access occurs below, after both gates.
    inputs, als_ids, roles, role_digest = source_state(paths, spec, _fixed_pins or SOURCE_PINS)
    training = validate_training_user_disjointness(paths.ratings, roles, spec)
    b1 = load_predictions(predictions, paths.score_axis, spec)
    truth, integrity = prepare_truth(paths, spec, roles, als_ids, b1)
    integrity["roleMembershipSha256"] = role_digest
    integrity["trainingUserIsolation"] = training
    outputs = compute_phase_outputs(phase, truth, spec, affine)
    source_records = (list(inputs.values()) + list(records.values()) + list(dependency_records)
                      + [path_pin(predictions), path_pin(SCRIPT), path_pin(paths.evaluation_auditor)])
    unique = {record["path"]: record for record in source_records}
    require(all(unique[r["path"]] == r for r in source_records), "conflicting dependency pins")
    source_records = list(unique.values())
    fingerprint = rehash_records(source_records)
    if _spec is None:
        require(fingerprint == rehash_records(dependency_records),
                "supervisor must acquire the complete evaluation dependency closure before starting")
    require_unlinked_path(staging, "owned stage", directory=True)
    require(not _bundle_inventory(staging), "evaluator stage must begin empty")
    lock = {"schemaVersion": "feelm-service-v1-b1-r4-evaluation-input-lock/1",
            "phase": phase.upper(), "runId": RUN_ID, "profileId": PROFILE_ID,
            "files": inputs, "controls": records, "dependencies": source_records,
            "evaluationSourceSetSha256": fingerprint}
    write_json(staging / "evaluation-input-lock.json", lock)
    write_json(staging / "truth-integrity.json", integrity)
    write_json(staging / "score-reference.json", {
        "manifest": records["score_manifest"], "review": records["score_review"],
        "predictions": path_pin(predictions)})
    for prefix in ("delivery", "server-receipt"):
        key = prefix.replace("-", "_")
        write_json(staging / (prefix + "-reference.json"), {
            "manifest": records[key + "_manifest"], "review": records[key + "_review"]})
    write_json(staging / "host-gate.json", host_gate)
    write_json(staging / "command.json", {"runId": RUN_ID, "profileId": PROFILE_ID,
        "phase": phase, "bootstrapSamples": spec.bootstrap_samples, "topK": list(TOP_K),
        "seeds": {"user": 339, "movie": 340, "signFlip": 341}, "expectedControls": records,
        "evaluator": path_pin(SCRIPT), "freshBlindHoldout": False})
    metrics, bootstrap, census, gates, fitted = outputs
    for name, value in (("metrics", metrics), ("bootstrap-summary", bootstrap),
                        ("strata-census", census), ("gates", gates)):
        write_json(staging / (name + ".json"), {"phase": phase.upper(), **value})
    if phase == "selection":
        write_role_membership(staging / "role-membership.csv", roles)
        write_json(staging / "affine.json", fitted)
    else:
        write_json(staging / "selection-reference.json", selection_reference)
    write_text(staging / "run.log", f"phase={phase} truth_rows={len(truth)} gate={gates['requiredGate']}\n")
    require(_bundle_inventory(staging) == phase_inventory(phase) - {"manifest.json", "evaluator-resource.json"},
            "computation output inventory")
    require(rehash_records(source_records) == fingerprint, "evaluation dependency changed during calculation")
    return {"phase": phase, "requiredGate": gates["requiredGate"], "truthCensus": integrity,
            "dependencyRecords": source_records, "dependencyFingerprint": fingerprint}


def _exact(value: Any, keys: set[str], label: str) -> None:
    require(type(value) is dict and set(value) == keys, label + " exact fields")


def _natural(value: Any, label: str) -> None:
    require(type(value) is int and value >= 0, label + " nonnegative integer required")


def _timestamp(value: Any) -> dt.datetime:
    require(type(value) is str and value.endswith("Z"), "RFC3339 UTC timestamp required")
    result = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    require(result.utcoffset() == dt.timedelta(0), "timestamp must be UTC")
    return result


def validate_evaluator_resource(value: Mapping[str, Any], phase: str) -> None:
    """Validate completed independent monitor evidence, never fabricate it."""
    require(phase in {"selection", "confirmation"}, "resource phase")
    _exact(value, {"schemaVersion", "status", "runId", "profileId", "phase", "unitName",
                  "monitorUnitName", "controlGroup", "mainPid", "initialPidTree", "limits",
                  "sampleIntervalSeconds", "samples", "peaks", "finalEvents", "termination", "cleanup"},
           "evaluator resource")
    stem = "feelm-b1-r4-server5c20g-t28800-" + phase
    require(value["schemaVersion"] == "feelm-service-v1-b1-r4-evaluator-resource/1"
            and value["status"] == "PASS" and value["runId"] == RUN_ID
            and value["profileId"] == PROFILE_ID
            and value["phase"] == ("calibrate-select" if phase == "selection" else phase)
            and value["unitName"] == stem + ".service"
            and value["monitorUnitName"] == stem + "-monitor.service", "evaluator resource identity")
    require(type(value["mainPid"]) is int and value["mainPid"] > 0, "main PID")
    require(type(value["controlGroup"]) is str and value["controlGroup"].startswith("/")
            and value["controlGroup"].endswith(value["unitName"])
            and ".." not in value["controlGroup"].split("/"), "target cgroup path")
    limits = {"cpuQuotaPercent": 500, "memoryMaxBytes": 21474836480, "memorySwapMaxBytes": 0,
              "tasksMax": 4096, "killMode": "control-group", "runtimeMaxSeconds": 14400,
              "timeoutStopSeconds": 900}
    require(type(value["limits"]) is dict and set(value["limits"]) == set(limits)
            and all(type(value["limits"][k]) is type(v) and value["limits"][k] == v
                    for k, v in limits.items()), "evaluator cgroup limits")
    require(type(value["sampleIntervalSeconds"]) in {float, int}
            and value["sampleIntervalSeconds"] == 2.0, "sample interval")

    def tree(items: Any, *, require_main: bool) -> None:
        require(type(items) is list, "PID tree array")
        for item in items:
            _exact(item, {"pid", "ppid", "startTimeTicks", "cmdlineSha256"}, "PID record")
            for field in ("pid", "ppid", "startTimeTicks"):
                _natural(item[field], "PID " + field)
            require(item["pid"] > 0 and valid_sha256(item["cmdlineSha256"]), "PID identity")
        ids = [item["pid"] for item in items]
        require(ids == sorted(set(ids)), "PID tree exact sorted unique")
        if require_main:
            require(value["mainPid"] in ids, "main PID absent from active PID tree")
        for item in items:
            if item["pid"] != value["mainPid"]:
                require(item["ppid"] in ids, "PID tree contains unrelated process")

    event_keys = {"low", "high", "max", "oom", "oomKill", "oomGroupKill"}
    def events(item: Any) -> None:
        _exact(item, event_keys, "memory events")
        for key in event_keys:
            _natural(item[key], "memory event")
        require(item["oom"] == item["oomKill"] == item["oomGroupKill"] == 0, "OOM event")

    tree(value["initialPidTree"], require_main=True)
    require(type(value["samples"]) is list and len(value["samples"]) >= 1, "resource samples missing")
    previous_nanos = -1
    previous_time = None
    previous_sample = None
    for index, item in enumerate(value["samples"]):
        _exact(item, {"observedAt", "monotonicNanos", "memoryCurrentBytes", "memoryPeakBytes",
                      "memoryEvents", "cpuUsageUsec", "cpuUserUsec", "cpuSystemUsec", "pidsCurrent", "pidTree"},
               "resource sample")
        observed = _timestamp(item["observedAt"])
        for key in ("monotonicNanos", "memoryCurrentBytes", "memoryPeakBytes", "cpuUsageUsec",
                    "cpuUserUsec", "cpuSystemUsec", "pidsCurrent"):
            _natural(item[key], "sample " + key)
        require(item["monotonicNanos"] > previous_nanos
                and (previous_time is None or observed >= previous_time), "sample ordering")
        require(item["memoryCurrentBytes"] <= item["memoryPeakBytes"] <= 21474836480,
                "memory peak unavailable/over limit")
        require(item["pidsCurrent"] <= 4096, "PID count over limit")
        tree(item["pidTree"], require_main=index == 0)
        require(len(item["pidTree"]) <= item["pidsCurrent"], "PID tree/current census")
        events(item["memoryEvents"])
        if previous_sample is not None:
            for key in ("memoryPeakBytes", "cpuUsageUsec", "cpuUserUsec", "cpuSystemUsec"):
                require(item[key] >= previous_sample[key], "decreasing cumulative resource counter")
            require(all(item["memoryEvents"][k] >= previous_sample["memoryEvents"][k]
                        for k in event_keys), "decreasing memory event counter")
        previous_nanos, previous_time, previous_sample = item["monotonicNanos"], observed, item
    _exact(value["peaks"], {"memoryPeakBytes", "maxPidsCurrent", "lastSampleAt"}, "resource peaks")
    require(value["peaks"]["memoryPeakBytes"] == max(x["memoryPeakBytes"] for x in value["samples"])
            and type(value["peaks"]["memoryPeakBytes"]) is int
            and value["peaks"]["maxPidsCurrent"] == max(x["pidsCurrent"] for x in value["samples"])
            and type(value["peaks"]["maxPidsCurrent"]) is int
            and value["peaks"]["lastSampleAt"] == value["samples"][-1]["observedAt"], "resource peak summary")
    events(value["finalEvents"])
    require(all(value["finalEvents"][k] >= value["samples"][-1]["memoryEvents"][k]
                for k in event_keys), "final event counter drift")
    terminal = value["termination"]
    _exact(terminal, {"timedOut", "oomKilled", "exitCode", "signal", "stopRequestedAt", "finalSampleAt"},
           "termination")
    require(terminal["timedOut"] is False and terminal["oomKilled"] is False
            and type(terminal["exitCode"]) is int and terminal["exitCode"] == 0
            and terminal["signal"] is None and terminal["stopRequestedAt"] is None
            and terminal["finalSampleAt"] == value["samples"][-1]["observedAt"], "termination unsuccessful")
    cleanup = value["cleanup"]
    _exact(cleanup, {"targetInactive", "monitorExitCode", "unitCollected", "pidTreeEmpty", "errors", "complete"},
           "cleanup")
    require(all(cleanup[k] is True for k in ("targetInactive", "unitCollected", "pidTreeEmpty", "complete"))
            and type(cleanup["monitorExitCode"]) is int and cleanup["monitorExitCode"] == 0
            and cleanup["errors"] == [], "monitor/target cleanup incomplete")


def finalize_phase(staging: Path, final: Path, *, phase: str, computation: Mapping[str, Any],
                   evaluator_resource: Mapping[str, Any], lease: Any,
                   rehash_callback: Any) -> dict[str, Any]:
    """Called by the outside supervisor after evaluator AND monitor terminate."""
    validate_evaluator_resource(evaluator_resource, phase)
    require(computation["phase"] == phase, "computation phase drift")
    fingerprint = computation["dependencyFingerprint"]
    require(rehash_records(computation["dependencyRecords"]) == fingerprint
            and rehash_callback() == fingerprint, "prepublication dependency drift")
    publisher = load_publication_module(computation["dependencyRecords"])
    require(_bundle_inventory(staging) == phase_inventory(phase) - {"manifest.json", "evaluator-resource.json"},
            "phase prepublication inventory")
    write_json(staging / "evaluator-resource.json", evaluator_resource)
    files = {name: pin(staging / name) for name in sorted(_bundle_inventory(staging))}
    # The lease supplies pre-rename evidence. No post-state is self-attested.
    publication = lease.publication_evidence
    manifest = {"schemaVersion": "feelm-service-v1-b1-r4-evaluation/1", "runId": RUN_ID,
                "profileId": PROFILE_ID, "phase": phase.upper(),
                "status": "B1_" + phase.upper() + "_COMPLETE_AUDIT_PENDING",
                "files": files, "evaluator": path_pin(SCRIPT),
                "truthCensus": computation["truthCensus"],
                "evaluationSourceSetSha256": fingerprint,
                "publication": publication, "readyForService": False,
                "confirmationAuthorized": False, "deploymentAuthorized": False,
                ("requiredSelectionGate" if phase == "selection" else "requiredConfirmationGate"):
                    computation["requiredGate"]}
    write_json(staging / "manifest.json", manifest)
    require(_bundle_inventory(staging) == phase_inventory(phase), "phase final inventory")
    published = publisher.publish_success(lease, staging, fingerprint, rehash_callback)
    require(_bundle_inventory(final) == phase_inventory(phase), "published inventory drift")
    for name, record in files.items():
        verify_pin(final / name, record, "published " + name)
    require(read_json(final / "manifest.json") == manifest, "published manifest drift")
    post = rehash_callback()
    require(post == fingerprint, "postpublication dependency drift")
    publisher.release_verified_claim(lease, published, post)
    return manifest


def load_publication_module(records: Sequence[Mapping[str, Any]]) -> Any:
    path = ROOT / "scripts/service_v1_b1_r4_publication.py"
    matches = [record for record in records if record["path"] == str(path)]
    require(len(matches) == 1, "shared publication source dependency required")
    verify_pin(path, matches[0], "shared publication implementation")
    name = "service_v1_b1_r4_publication"
    # Leases are instances of the loaded module's dataclass. Reuse the module
    # identity while still rehashing its source on every call.
    if name in sys.modules:
        module = sys.modules[name]
        require(Path(os.path.abspath(module.__file__)) == path, "publication module origin")
        return module
    spec = importlib.util.spec_from_file_location(name, path)
    require(spec is not None and spec.loader is not None, "publication module unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def production_paths(score_manifest: Path, score_review: Path) -> EvaluationPaths:
    """Build path names lexically; do not resolve/stat evaluation input files."""
    evidence = ROOT / "outputs/recommendation-evidence"
    return EvaluationPaths(
        root=ROOT, team_root=TEAM_ROOT, plan=PLAN,
        artifact_manifest=TEAM_ROOT / "pipeline/artifacts/service-v1.json",
        evaluation_contract=TEAM_ROOT / "pipeline/docs/service-v1/EVALUATION.md",
        contexts=evidence / "text339/contexts.json", catalog=evidence / "text339/catalog.parquet",
        labels=evidence / "text339/labels.parquet", evaluation_seal=evidence / "text339/evaluation-seal.json",
        roles=evidence / "final344/roles.csv", metadata=evidence / "rec-ev-045/metadata.parquet",
        score_axis=evidence / "foundation340/RH/score.parquet", ratings=evidence / "text339/ratings.parquet",
        b0_predictions=evidence / "final344/GBT120_s339/predictions.npy",
        b0_seal=evidence / "final344/GBT120_s339-seal.json",
        als_factor_dir=evidence / "combination340/ALS/item-factors",
        score_manifest=score_manifest, score_review=score_review,
        spark_auditor=ROOT / "scripts/audit_service_v1_b1_spark_outputs_r4.py",
        evaluation_auditor=ROOT / "scripts/audit_service_v1_b1_evaluation_outputs_r4.py")


def parse_worker_request(raw: str) -> dict[str, Any]:
    def unique(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate worker request key")
            result[key] = value
        return result
    def invalid(value):
        raise ContractError("nonfinite worker request: " + value)
    request = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)
    _exact(request, {"controls", "expectedSha256", "stagingPath", "hostGate",
                     "dependencyRecords", "selectionReference"}, "worker request")
    _exact(request["controls"], set(CONTROL_NAMES), "worker control paths")
    _exact(request["expectedSha256"], set(CONTROL_NAMES), "worker control hashes")
    require(all(type(p) is str and Path(p).is_absolute() for p in request["controls"].values()),
            "worker control paths must be absolute")
    require(type(request["stagingPath"]) is str and Path(request["stagingPath"]).is_absolute(),
            "worker staging path must be absolute")
    require(type(request["dependencyRecords"]) is list, "worker dependencies array")
    canonical_record_set_digest(request["dependencyRecords"])
    return request


def verify_runtime_for_evaluation(lock: Mapping[str, Any], root: Path) -> str:
    """Rehash the complete sealed host venv, without reading model/truth inputs."""
    import platform
    required = {"schemaVersion", "createdAt", "bootstrapInterpreter", "absoluteInterpreter", "interpreterBytes",
                "interpreterSha256", "pythonImplementation", "pythonVersion", "environment", "requirementsLock",
                "wheelhouseManifest", "venvInventory", "packages", "imports", "evaluationFixture", "runtimeSetSha256"}
    _exact(lock, required, "host runtime lock")
    expected_python = root / ".runtime/service-v1-b1-r4/venv/bin/python3"
    require(lock["schemaVersion"] == "feelm-service-v1-b1-r4-host-runtime-lock/1"
            and lock["pythonImplementation"] == platform.python_implementation() == "CPython"
            and lock["pythonVersion"] == platform.python_version() == "3.12.3"
            and lock["absoluteInterpreter"] == str(expected_python)
            and Path(os.path.abspath(sys.executable)) == expected_python, "current sealed interpreter identity")
    verify_pin(expected_python, {"bytes": lock["interpreterBytes"], "sha256": lock["interpreterSha256"]}, "interpreter")
    require(sys.flags.isolated == 1 and sys.dont_write_bytecode is True and "PYTHONPATH" not in os.environ,
            "isolated immutable runtime flags")
    env = {"HOME": str(root / ".runtime/service-v1-b1-r4/home"),
           "PATH": str(expected_python.parent) + ":/usr/bin:/bin", "PYTHONNOUSERSITE": "1",
           "PIP_CONFIG_FILE": "/dev/null", "PYTHONDONTWRITEBYTECODE": "1", "LC_ALL": "C.UTF-8", "TZ": "UTC"}
    require(all(os.environ.get(k) == v for k, v in env.items()), "sealed runtime environment")
    require(lock["environment"] == {"pythonPath": "UNSET", "pythonNoUserSite": True, "isolatedMode": True,
            "pythonDontWriteBytecode": True, "locale": "C.UTF-8", "timezone": "UTC"}, "runtime environment lock")
    require(lock["imports"].get("status") == "PASS" and lock["imports"].get("exitCode") == 0
            and lock["evaluationFixture"].get("status") == "PASS", "runtime import/fixture gate")
    _exact(lock["packages"], {"numpy", "pandas", "pyarrow"}, "runtime package set")
    for key, version, module in (("numpy", "1.26.4", np), ("pandas", "2.2.3", pd), ("pyarrow", "19.0.1", pa)):
        item = lock["packages"][key]
        _exact(item, {"version", "modulePath", "moduleBytes", "moduleSha256", "distributionRecordSetSha256"}, "package")
        require(item["version"] == module.__version__ == version
                and item["modulePath"] == str(Path(os.path.abspath(module.__file__))), "runtime package/module identity")
        verify_pin(Path(item["modulePath"]), {"bytes": item["moduleBytes"], "sha256": item["moduleSha256"]}, "package module")
    receipt = root / "outputs/recommendation-evidence/service-v1-pretraining-20260913" / (RUN_ID + "-server-receipt")
    runtime_paths = {}
    for key in ("requirementsLock", "wheelhouseManifest", "venvInventory"):
        _exact(lock[key], {"path", "bytes", "sha256"}, "runtime pin")
        if key == "venvInventory":
            require(lock[key]["path"] == "runtime/venv-file-inventory.json", "receipt runtime inventory path")
            physical = receipt / lock[key]["path"]
        else:
            physical = Path(lock[key]["path"])
            require(physical.is_absolute(), "runtime dependency must be absolute")
        verify_pin(physical, lock[key], key)
        runtime_paths[key] = physical
    inventory = read_json(runtime_paths["venvInventory"])
    _exact(inventory, {"schemaVersion", "root", "records", "regularFileCount", "symlinkCount",
                       "specialFileCount", "hardLinkAliasCount", "recordSetSha256"}, "venv inventory")
    venv_root = expected_python.parent.parent
    require(inventory["schemaVersion"] == "feelm-service-v1-b1-r4-venv-inventory/1"
            and inventory["root"] == str(venv_root)
            and all(type(inventory[k]) is int and inventory[k] == 0 for k in
                    ("symlinkCount", "specialFileCount", "hardLinkAliasCount")), "venv inventory identity")
    records = inventory["records"]
    require(type(records) is list and len(records) == inventory["regularFileCount"], "venv record census")
    simplified = []
    for record in records:
        _exact(record, {"path", "bytes", "sha256", "mode", "stDev", "stIno", "nlink"}, "venv record")
        name = record["path"]
        require(type(name) is str and not Path(name).is_absolute() and ".." not in name.split("/")
                and "\\" not in name and name != "", "venv relative path")
        target = venv_root / name
        verify_pin(target, record, "venv child")
        observed = target.stat()
        require(record["nlink"] == observed.st_nlink == 1
                and record["stDev"] == observed.st_dev and record["stIno"] == observed.st_ino
                and record["mode"] == stat.S_IMODE(observed.st_mode), "venv file identity/mode")
        simplified.append({"path": name, "bytes": record["bytes"], "sha256": record["sha256"]})
    require({r["path"] for r in simplified} == _bundle_inventory(venv_root)
            and len({r["path"] for r in simplified}) == len(simplified), "venv exact file inventory")
    digest = canonical_record_set_digest(simplified)
    require(digest == inventory["recordSetSha256"], "venv record digest")
    import importlib.metadata as metadata
    for name, package in lock["packages"].items():
        package_records = []
        distribution = metadata.distribution(name)
        for item in distribution.files or ():
            child = Path(os.path.abspath(item.locate()))
            if child.is_file():
                require(venv_root in child.parents, "distribution escapes venv")
                record = path_pin(child)
                record["path"] = child.relative_to(venv_root).as_posix()
                package_records.append(record)
        require(package_records and canonical_record_set_digest(package_records) ==
                package["distributionRecordSetSha256"], "distribution file digest")
    fixture = lock["evaluationFixture"]
    _exact(fixture, {"inputSha256", "outputSha256", "status"}, "runtime fixture")
    require(all(valid_sha256(fixture[k]) for k in ("inputSha256", "outputSha256")), "fixture digest type")
    runtime_records = [{"path": str(expected_python), "bytes": lock["interpreterBytes"],
                        "sha256": lock["interpreterSha256"]}, lock["requirementsLock"],
                       lock["wheelhouseManifest"], lock["venvInventory"],
                       {"path": "runtime/evaluation-fixture-input", "bytes": 0, "sha256": fixture["inputSha256"]},
                       {"path": "runtime/evaluation-fixture-output", "bytes": 0, "sha256": fixture["outputSha256"]}]
    require(canonical_record_set_digest(runtime_records) == lock["runtimeSetSha256"], "complete runtime digest")
    return digest


def main(argv: Sequence[str] | None = None) -> None:
    """Supervised compute child. Request arrives through stdin, result via stdout.

    This entry point cannot run in an interactive host process: the sealed host
    runtime and the exact systemd cgroup are checked before any truth file opens.
    The supervisor owns lease creation, monitoring, terminal manifest and failure.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("calibrate-select", "confirm"))
    args = parser.parse_args(argv)
    try:
        request = parse_worker_request(sys.stdin.read())
        phase = "selection" if args.phase == "calibrate-select" else "confirmation"
        controls = ControlPaths(**{k: Path(v) for k, v in request["controls"].items()})
        require({name: getattr(controls, name) for name in CONTROL_NAMES} == canonical_control_paths(ROOT),
                "public evaluator/reviewer control path drift")
        state = verify_score_review_gate(controls, request["expectedSha256"])
        if phase == "confirmation":
            ref = request["selectionReference"]
            require(type(ref) is dict and set(ref) == {"manifest", "review"}, "confirmation reference required")
            verify_selection_review_gate(Path(ref["manifest"]["path"]), Path(ref["review"]["path"]),
                                         ref["manifest"]["sha256"], ref["review"]["sha256"])
        else:
            require(request["selectionReference"] is None, "selection cannot consume a prior selection")
        require(sys.platform == "linux", "server evaluator requires Linux systemd cgroup-v2")
        require(sys.flags.isolated == 1, "sealed host interpreter requires isolated mode")
        expected_unit = "feelm-b1-r4-server5c20g-t28800-" + phase + ".service"
        cgroup_lines = Path("/proc/self/cgroup").read_text(encoding="ascii").splitlines()
        require(len(cgroup_lines) == 1 and cgroup_lines[0].startswith("0::/")
                and cgroup_lines[0].endswith("/" + expected_unit), "evaluator not in its supervised cgroup")
        paths = production_paths(controls.score_manifest, controls.score_review)
        runtime_digest = verify_runtime_for_evaluation(state["payloads"]["host_runtime_lock"], ROOT)
        verify_evaluation_auditor_binding(paths.evaluation_auditor, SCRIPT,
                                         REVIEWED_R4_EVALUATION_AUDITOR_CORE_SHA256)
        # Isolated Python omits the script directory from sys.path. Load the
        # reviewed sibling by its exact filename, never by ambient import paths.
        runner_path = ROOT / "scripts/run_service_v1_b1_gbt_r4.py"
        runner_pin = next((r for r in request["dependencyRecords"] if r["path"] == str(runner_path)), None)
        require(runner_pin is not None, "reviewed runner dependency missing")
        delivered = state["payloads"]["delivery_manifest"].get("controlAndImplementation", [])
        matched = [r for r in delivered if Path(r.get("logicalPath", "")).name == runner_path.name
                   and r.get("kind") == "regular-file" and r.get("bytes") == runner_pin["bytes"]
                   and r.get("sha256") == runner_pin["sha256"]]
        require(len(matched) == 1, "runner source is not bound by the reviewed delivery")
        verify_pin(runner_path, runner_pin, "server runner source")
        module_spec = importlib.util.spec_from_file_location("service_v1_b1_r4_supervisor", runner_path)
        require(module_spec is not None and module_spec.loader is not None, "server runner module unavailable")
        runner = importlib.util.module_from_spec(module_spec)
        sys.modules[module_spec.name] = runner
        module_spec.loader.exec_module(runner)
        server_paths = runner.ExecutionPaths(standalone=ROOT, team=TEAM_ROOT)
        gate_controls = {name: getattr(controls, name) for name in CONTROL_NAMES}
        gate_expected = dict(request["expectedSha256"])
        if phase == "confirmation":
            gate_controls.update(selection_manifest=Path(ref["manifest"]["path"]),
                                 selection_review=Path(ref["review"]["path"]))
            gate_expected.update(selection_manifest=ref["manifest"]["sha256"],
                                 selection_review=ref["review"]["sha256"])
        gate = runner.verify_server_evaluation_gate(server_paths,
            phase="calibrate-select" if phase == "selection" else "confirmation",
            controls=gate_controls, expected_sha256=gate_expected, dynamic_host_gate=request["hostGate"])
        require(gate.get("status") == "PASS", "server evaluation gate blocked")
        require(gate.get("dependencyRecords") == request["dependencyRecords"],
                "independently verified server dependency closure mismatch")
        stage = Path(request["stagingPath"])
        final = ROOT / "outputs/recommendation-evidence/service-v1-pretraining-20260913" / (RUN_ID + "-" + phase)
        require(stage.parent == final.parent and stage.name.startswith("." + final.name + ".tmp-"),
                "worker staging namespace")
        claim = final.with_name("." + final.name + ".claim")
        ownership = read_json(claim)
        require(ownership.get("runId") == RUN_ID and ownership.get("profileId") == PROFILE_ID
                and ownership.get("role") == "PRODUCER" and ownership.get("finalPath") == str(final)
                and stage.name == "." + final.name + ".tmp-" + ownership.get("token", ""),
                "worker staging claim ownership")
        predictions = controls.score_manifest.parent / "score/predictions.parquet"
        computed = prepare_phase(paths, stage, phase, control_state=state, host_gate=request["hostGate"],
            predictions=predictions, dependency_records=request["dependencyRecords"],
            selection_reference=request["selectionReference"])
        require(verify_runtime_for_evaluation(state["payloads"]["host_runtime_lock"], ROOT) == runtime_digest,
                "runtime changed during evaluation")
        print("FEELM_R4_EVALUATION_RESULT=" + json.dumps(computed, ensure_ascii=False,
              allow_nan=False, sort_keys=True), flush=True)
    except Exception as error:
        print(json.dumps({"status": "BLOCK", "phase": args.phase, "error": str(error)},
                         ensure_ascii=False, sort_keys=True), file=sys.stderr, flush=True)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
