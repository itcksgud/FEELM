"""Independent r4 evaluation audit; this module never imports the evaluator.

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
import traceback
import uuid
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


REVIEWED_R4_EVALUATOR_SHA256: str = "2884e076da210dc9b6bdd443596b05a0f7fc4eb293bec0de312f720d18d09795"

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

need = require
AuditError = ContractError

def safe_file(path):
    return require_unlinked_path(path, "audit input")

def check_pin(path, record, label):
    return verify_pin(path, record, label)

def json_object(path):
    value = read_json(path)
    require(type(value) is dict, "JSON object required")
    return value

@dataclass(frozen=True)
class Roots:
    standalone: Path
    team: Path

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


def audit_phase(paths: EvaluationPaths, bundle: Path, phase: str, *,
                expected_manifest_sha256: str, expected_evaluator_sha256: str,
                control_state: Mapping[str, Any], expected_dependency_records: Sequence[Mapping[str, Any]],
                _spec: AuditSpec | None = None,
                _source_pins: Mapping[str, tuple[int, str]] | None = None) -> dict[str, Any]:
    """Independently reconstruct exact truth/statistics from a completed bundle.

    The server coordinator supplies its independently verified ancestry closure;
    this routine then reopens every declared dependency. It never executes or
    imports evaluator code. The current evaluator bytes must match both the
    externally expected SHA and the unique implementation-review-sealed pin.
    """
    spec = _spec or AuditSpec()
    require(phase in {"selection", "confirmation"}, "evaluation audit phase")
    if spec.canonical:
        require(valid_sha256(REVIEWED_R4_EVALUATOR_SHA256)
                and REVIEWED_R4_EVALUATOR_SHA256 != "0" * 64
                and expected_evaluator_sha256 == REVIEWED_R4_EVALUATOR_SHA256,
                "unsealed or changed evaluator implementation")
        require(bundle == paths.root / "outputs/recommendation-evidence/service-v1-pretraining-20260913" /
                (RUN_ID + "-" + phase), "evaluation canonical namespace")
        verify_mutual_source_binding(paths.root / "scripts/evaluate_service_v1_b1_r4.py")
    evaluator = paths.root / "scripts/evaluate_service_v1_b1_r4.py"
    verify_sha(evaluator, expected_evaluator_sha256, "evaluator")
    controls = control_state["records"]
    require(set(controls) == set(CONTROL_NAMES), "control inventory")
    require(rehash_records(controls.values()) == control_state["controlSetSha256"], "control drift")
    score_review = read_json(Path(controls["score_review"]["path"]))
    require(score_review.get("status") == "PASS"
            and score_review.get("decision", {}).get("evaluationSelectionEligible") is True,
            "score review gate")
    verify_sha(bundle / "manifest.json", expected_manifest_sha256, "evaluation manifest")
    manifest = json_object(bundle / "manifest.json")
    require(_bundle_inventory(bundle) == phase_inventory(phase), "exact evaluation bundle inventory")
    require(manifest.get("schemaVersion") == "feelm-service-v1-b1-r4-evaluation/1"
            and manifest.get("phase") == phase.upper()
            and manifest.get("runId") == RUN_ID and manifest.get("profileId") == PROFILE_ID
            and manifest.get("status") == "B1_" + phase.upper() + "_COMPLETE_AUDIT_PENDING"
            and manifest.get("readyForService") is False
            and manifest.get("confirmationAuthorized") is False
            and manifest.get("deploymentAuthorized") is False, "evaluation manifest contract")
    require(type(manifest.get("files")) is dict
            and set(manifest["files"]) == phase_inventory(phase) - {"manifest.json"}, "manifest exact file set")
    for name, record in manifest["files"].items():
        require(type(record) is dict and set(record) == {"bytes", "sha256"}, "output pin fields")
        verify_pin(bundle / name, record, name)
    verify_pin(evaluator, manifest["evaluator"], "manifest evaluator")
    resource = json_object(bundle / "evaluator-resource.json")
    validate_evaluator_resource(resource, phase)
    if spec.canonical:
        verify_recorded_host_gate(json_object(bundle / "host-gate.json"),
            phase="calibrate-select" if phase == "selection" else "confirmation", resource=resource)
    # Confirmation checks its prior review before any evaluation input access.
    selection_affine = None
    selection_reference = None
    if phase == "confirmation":
        reference = json_object(bundle / "selection-reference.json")
        require(set(reference) == {"manifest", "review"}, "selection reference fields")
        selection_reference = verify_selection_review_gate(
            Path(reference["manifest"]["path"]), Path(reference["review"]["path"]),
            reference["manifest"]["sha256"], reference["review"]["sha256"])
        json_equal(reference, selection_reference, "selection reference pins")
        selected = Path(reference["manifest"]["path"]).parent
        selected_manifest = json_object(selected / "manifest.json")
        require(_bundle_inventory(selected) == phase_inventory("selection"), "selection ancestor inventory")
        for name, record in selected_manifest["files"].items():
            verify_pin(selected / name, record, "selection ancestor " + name)
        selection_affine = json_object(selected / "affine.json")
    lock = json_object(bundle / "evaluation-input-lock.json")
    require(set(lock) == {"schemaVersion", "phase", "runId", "profileId", "files", "controls",
                         "dependencies", "evaluationSourceSetSha256"}, "evaluation input lock shape")
    require(lock["schemaVersion"] == "feelm-service-v1-b1-r4-evaluation-input-lock/1"
            and lock["phase"] == phase.upper() and lock["runId"] == RUN_ID
            and lock["profileId"] == PROFILE_ID, "evaluation input lock identity")
    json_equal(lock["controls"], controls, "current controls")
    expected = _source_pins or SOURCE_PINS
    require(set(expected) == set(SOURCE_PINS), "input source pin exact set")
    source_records = {}
    data = {}
    for name, (size, digest) in expected.items():
        path = getattr(paths, name)
        verify_pin(path, {"bytes": size, "sha256": digest}, "fixed input " + name)
        source_records[name] = path_pin(path)
        data[name] = path
    als_ids, als_records = verify_als_axis(paths, spec)
    source_records.update({"als:" + name: record for name, record in als_records.items()})
    json_equal(lock["files"], source_records, "source input inventory")
    score_reference = json_object(bundle / "score-reference.json")
    require(set(score_reference) == {"manifest", "review", "predictions"}, "score reference shape")
    json_equal(score_reference["manifest"], controls["score_manifest"], "score reference manifest")
    json_equal(score_reference["review"], controls["score_review"], "score reference review")
    predictions = Path(score_reference["predictions"]["path"])
    verify_pin(predictions, score_reference["predictions"], "B1 prediction reference")
    needed = (list(source_records.values()) + list(controls.values()) + list(expected_dependency_records)
              + [path_pin(predictions), path_pin(evaluator), path_pin(paths.evaluation_auditor)])
    unique = {record["path"]: record for record in needed}
    require(all(unique[r["path"]] == r for r in needed), "conflicting input dependency pins")
    require(type(lock["dependencies"]) is list, "dependency array")
    actual = {record["path"]: record for record in lock["dependencies"]}
    require(len(actual) == len(lock["dependencies"]), "duplicate dependency record")
    json_equal(actual, unique, "exact dependency closure")
    fingerprint = rehash_records(lock["dependencies"])
    require(fingerprint == lock["evaluationSourceSetSha256"] == manifest["evaluationSourceSetSha256"],
            "evaluation dependency fingerprint drift")
    verify_publication_evidence(manifest.get("publication"), bundle, fingerprint, "PRODUCER")
    for prefix in ("delivery", "server-receipt"):
        key = prefix.replace("-", "_")
        json_equal(json_object(bundle / (prefix + "-reference.json")), {
            "manifest": controls[key + "_manifest"], "review": controls[key + "_review"]},
            prefix + " reference")
    roles, digest = split_roles(paths.roles, spec)
    truth, integrity = build_truth(Roots(paths.root, paths.team_root), data, predictions, roles, digest, als_ids, spec)
    validate_phase_truth(truth)
    json_equal(json_object(bundle / "truth-integrity.json"), integrity, "truth integrity")
    json_equal(manifest.get("truthCensus"), integrity, "truth manifest census")
    if phase == "selection":
        require((bundle / "role-membership.csv").read_bytes() == expected_role_membership(roles),
                "role membership output drift")
    computed = compute_expected_outputs(phase, truth, spec, selection_affine)
    verify_metric_files(bundle, phase, *computed)
    metrics, bootstrap, census, gates, affine = computed
    key = "requiredSelectionGate" if phase == "selection" else "requiredConfirmationGate"
    require(manifest[key] == gates["requiredGate"], "recomputed gate drift")
    command = json_object(bundle / "command.json")
    json_equal(command, {"runId": RUN_ID, "profileId": PROFILE_ID, "phase": phase,
        "bootstrapSamples": spec.bootstrap_samples, "topK": list(TOP_K),
        "seeds": {"user": 339, "movie": 340, "signFlip": 341}, "expectedControls": controls,
        "evaluator": path_pin(evaluator), "freshBlindHoldout": False}, "evaluation command")
    require((bundle / "run.log").read_text(encoding="utf-8") ==
            f"phase={phase} truth_rows={len(truth)} gate={gates['requiredGate']}\n", "evaluation log")
    require(rehash_records(lock["dependencies"]) == fingerprint, "dependency mutated during audit")
    if phase == "selection":
        decision = {"selectionGate": gates["requiredGate"],
                    "confirmationEligible": gates["requiredGate"] == "PASS",
                    "logicalState": ("B1_SELECTION_AUDITED_CONFIRMATION_ELIGIBLE"
                        if gates["requiredGate"] == "PASS" else "B1_SELECTION_AUDITED_CONFIRMATION_BLOCKED")}
    else:
        decision = {"selectionGate": "PASS", "confirmationGate": gates["requiredGate"],
                    "noninferiorityConclusionEligible": gates["requiredGate"] == "PASS",
                    "logicalState": ("B1_EVALUATED_NONINFERIOR" if gates["requiredGate"] == "PASS"
                                     else "NO_NONINFERIORITY_CONCLUSION")}
    return {"schemaVersion": "feelm-service-v1-b1-r4-evaluation-result-review/1",
            "status": "PASS", "runId": RUN_ID, "profileId": PROFILE_ID, "phase": phase,
            "createdAt": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "target": {"manifest": path_pin(bundle / "manifest.json"), "evaluator": path_pin(evaluator),
                       "files": {name: path_pin(bundle / name) for name in sorted(phase_inventory(phase))}},
            "reviewer": {"implementation": path_pin(SCRIPT), "independentFromEvaluator": True},
            "dependencyFingerprint": fingerprint, "checks": {
                "exactOutputInventory": True, "allInputPinsRehashed": True, "truthRebuilt": True,
                "metricsIndependentlyRecomputed": True, "bootstrapSamples": spec.bootstrap_samples,
                "gateIndependentlyRecomputed": True, "truthRows": len(truth)},
            "decision": decision, "evaluationInputsRead": True, "modelFitPerformed": False,
            "readyForService": False, "deploymentAuthorized": False}


def verify_publication_evidence(value: Any, final: Path, fingerprint: str, role: str) -> None:
    keys = {"schemaVersion", "mode", "role", "finalPath", "failurePath", "claimPath", "tempPath", "token",
            "claimStDev", "claimStIno", "filesystemType", "publisher", "dependencyFingerprintAtAcquire",
            "dependencyFingerprintBeforeRename", "renameNoReplaceProbe", "requiredPostconditions"}
    _exact(value, keys, "publication evidence")
    require(value["schemaVersion"] == "feelm-service-v1-b1-r4-publication-evidence/1"
            and value["mode"] == "LINUX_RENAME_NOREPLACE" and value["role"] == role,
            "publication schema/mode/role")
    require(value["finalPath"] == str(final), "publication final path")
    expected_failure = final.with_name((final.name[:-5] if final.name.endswith(".json") else final.name)
                                       + "-failure.json")
    require(value["failurePath"] == str(expected_failure), "publication failure path")
    token = value["token"]
    require(type(token) is str and len(token) in {32, 36}
            and all(c in "0123456789abcdef-" for c in token), "publication token")
    require(value["claimPath"] == str(final.with_name("." + final.name + ".claim"))
            and value["tempPath"] == str(final.with_name("." + final.name + ".tmp-" + token)),
            "publication transient paths")
    _natural(value["claimStDev"], "claim device")
    _natural(value["claimStIno"], "claim inode")
    require(value["filesystemType"] in {"ext2/ext3", "xfs"} and value["renameNoReplaceProbe"] is True,
            "publication filesystem/probe")
    require(value["dependencyFingerprintAtAcquire"] == value["dependencyFingerprintBeforeRename"] == fingerprint,
            "publication dependency fingerprint")
    required = {"publishedBytesRehashRequired", "fileAndParentFsyncRequired", "dependencyFingerprintStableRequired",
                "claimIdentityMatchRequired", "claimRemovalRequired"}
    _exact(value["requiredPostconditions"], required, "publication postconditions")
    require(all(value["requiredPostconditions"][k] is True for k in required), "publication required postconditions")
    publisher = value["publisher"]
    _exact(publisher, {"path", "bytes", "sha256"}, "publication publisher")
    require(Path(publisher["path"]).name == "service_v1_b1_r4_publication.py", "publication shared primitive")
    verify_pin(Path(publisher["path"]), publisher, "publication source")
    require(not any(os.path.lexists(p) for p in (value["claimPath"], value["tempPath"], value["failurePath"])),
            "publication unfinished claim/temp/failure")


def review_predecessor_children(phase: str) -> tuple[str, ...]:
    """Frozen section 13 checkpoints immediately before each evaluation review."""
    require(phase in {"selection", "confirmation"}, "evaluation review namespace phase")
    suffixes = ["design-result-review.json", "implementation-result-review.json",
                "delivery-manifest.json", "delivery-manifest-result-review.json",
                "server-receipt", "server-receipt-result-review.json",
                "preflight", "preflight-result-review.json", "fit", "fit-result-review.json",
                "score", "score-result-review.json", "selection"]
    if phase == "confirmation":
        suffixes.extend(("selection-result-review.json", "confirmation"))
    return tuple(sorted(RUN_ID + "-" + suffix for suffix in suffixes))


def verify_review_predecessor_namespace(bundle: Path, phase: str,
                                        expected_namespace: Sequence[str] | None = None, *,
                                        lease: Any | None = None,
                                        allow_verified_temp: bool = False) -> list[str]:
    """Reject caller-expanded r4 namespaces before either success/failure claim.

    The canonical r4 evidence parent contains only this checkpoint's
    predecessors. Older-run ancestry is read through pinned references outside
    this parent; no unrelated regular file, directory or hidden child is allowed.
    """
    frozen = review_predecessor_children(phase)
    require(bundle.name == RUN_ID + "-" + phase, "review target namespace name")
    if expected_namespace is not None:
        require(type(expected_namespace) in {tuple, list}
                and all(type(name) is str for name in expected_namespace)
                and tuple(expected_namespace) == frozen, "caller namespace differs from frozen review predecessors")
    parent = require_unlinked_path(bundle.parent, "review namespace parent", directory=True)
    children = sorted(parent.iterdir(), key=lambda child: child.name)
    names = [child.name for child in children]
    allowed_hidden = set()
    if lease is not None:
        require(lease.final_path == bundle.with_name(bundle.name + "-result-review.json")
                and lease.claim_path.parent == parent and lease.temp_path.parent == parent,
                "review namespace lease target mismatch")
        allowed_hidden.add(lease.claim_path.name)
        if allow_verified_temp:
            allowed_hidden.add(lease.temp_path.name)
    hidden = {name for name in names if name.startswith(".")}
    require(hidden <= allowed_hidden and (lease is None or lease.claim_path.name in hidden),
            "review namespace contains hidden/stale claim/temp/scratch")
    require([name for name in names if name not in allowed_hidden] == list(frozen),
            "review predecessor namespace has missing/extra/stale completed member")
    for child in children:
        if child.name in frozen:
            require_unlinked_path(child, "review predecessor " + child.name,
                                  directory=not child.name.endswith(".json"))
    # Shared acquisition receives only the frozen complete predecessor set.
    return list(frozen)


class ReviewFailurePublished(ContractError):
    def __init__(self, path: Path, original: BaseException):
        super().__init__(str(original))
        self.path = path
        self.original = original


class ReviewPublicationBlocked(ContractError):
    """A claimed or uncertain terminal state must never be retried implicitly."""


def review_failure_snapshot(candidates: Sequence[Path]) -> tuple[list[dict[str, Any]], str]:
    """Pin available control/target bytes and hash unavailable-file reasons too."""
    records, unavailable = [], []
    for path in sorted({str(Path(os.path.abspath(p))) for p in candidates}):
        try:
            records.append(path_pin(Path(path)))
        except (OSError, ContractError, ValueError) as error:
            unavailable.append({"path": path, "type": type(error).__name__, "message": str(error)})
    if unavailable:
        raw = json.dumps({"records": records, "unavailable": unavailable}, sort_keys=True,
                         ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        fingerprint = hashlib.sha256(raw).hexdigest()
    else:
        fingerprint = canonical_record_set_digest(records)
    return records, fingerprint


def review_candidates(bundle: Path, phase: str, dependencies: Sequence[Mapping[str, Any]],
                      controls: Mapping[str, Any] | None, *, allow_bundle: bool) -> list[Path]:
    candidates = [SCRIPT, PROFILE, ROOT / "scripts/service_v1_b1_r4_publication.py",
                  *canonical_control_paths(ROOT).values()]
    if controls is not None:
        candidates.extend(Path(record["path"]) for record in controls["records"].values())
    # Before score authorization no evaluation input, directory or path is touched.
    if allow_bundle:
        candidates.extend(Path(record["path"]) for record in dependencies)
        candidates.extend(bundle / name for name in phase_inventory(phase))
        if bundle.is_dir() and not bundle.is_symlink():
            for child in bundle.rglob("*"):
                if child.is_file() or child.is_symlink():
                    candidates.append(child)
    return candidates


def _discard_verified_owned_review_temp(lease: Any, staged_pin: Mapping[str, Any] | None,
                                        fingerprint: str, rehash: Any) -> None:
    """Clean only our unchanged failed serialization; never remove a claim."""
    if not os.path.lexists(lease.temp_path):
        return
    require(staged_pin is not None and rehash() == fingerprint, "unverified review temp must remain")
    require(not os.path.lexists(lease.final_path) and not os.path.lexists(lease.failure_path),
            "published/uncertain review must retain its claim")
    claim = require_unlinked_path(lease.claim_path, "owned review claim")
    metadata = claim.stat()
    evidence = lease.publication_evidence
    require(metadata.st_dev == evidence["claimStDev"] and metadata.st_ino == evidence["claimStIno"],
            "review claim identity drift")
    owner = json_object(claim)
    require(owner.get("token") == evidence["token"] and owner.get("role") == "REVIEWER"
            and owner.get("pid") == os.getpid() == lease.owner_pid
            and owner.get("finalPath") == str(lease.final_path), "review claim token/role/PID drift")
    verify_pin(lease.temp_path, staged_pin, "owned serialized review")
    lease.temp_path.unlink()
    if sys.platform == "linux":
        descriptor = os.open(lease.temp_path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def publish_review_failure(bundle: Path, phase: str, error: BaseException, *,
                           candidates: Sequence[Path], expected_namespace: Sequence[str],
                           publisher: Any, lease: Any | None = None,
                           fingerprint: str | None = None, staged_pin: Mapping[str, Any] | None = None) -> Path:
    """Own both pre-acquisition and acquired handled-failure publication."""
    destination = bundle.with_name(bundle.name + "-result-review.json")
    failure = destination.with_name(destination.stem + "-failure.json")
    records, current = review_failure_snapshot(candidates)
    if fingerprint is None:
        fingerprint = current
    require(current == fingerprint, "review dependencies changed before failure publication")
    def rehash() -> str:
        return review_failure_snapshot(candidates)[1]
    predecessor_snapshot = verify_review_predecessor_namespace(bundle, phase, expected_namespace,
        lease=lease, allow_verified_temp=staged_pin is not None)
    if lease is None:
        lease = publisher.acquire_publication("REVIEWER", phase + "-review", destination, failure,
            {"children": predecessor_snapshot, "dependencyFingerprint": fingerprint})
    _discard_verified_owned_review_temp(lease, staged_pin, fingerprint, rehash)
    verify_review_predecessor_namespace(bundle, phase, expected_namespace, lease=lease)
    before = sorted(path.name for path in destination.parent.iterdir())
    payload = {
        "schemaVersion": "feelm-service-v1-b1-r4-review-failure/1", "status": "FAILED",
        "runId": RUN_ID, "profileId": PROFILE_ID, "phase": phase + "-review",
        "createdAt": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "auditor": {"kind": "INDEPENDENT_EVALUATION_AUDITOR", "sessionId": "evaluation-review-" + uuid.uuid4().hex,
                    "host": os.uname().nodename if hasattr(os, "uname") else os.environ.get("COMPUTERNAME", "unknown"),
                    "processId": os.getpid()},
        "target": records,
        "error": {"type": type(error).__name__, "message": str(error),
                  "traceback": "".join(traceback.format_exception(type(error), error, error.__traceback__))},
        "dependencyFingerprintBefore": fingerprint, "dependencyFingerprintAfter": rehash(),
        "namespaceCensus": {"beforeAcquire": predecessor_snapshot, "beforePublication": before,
                            "successPathPresent": os.path.lexists(destination), "failurePathPresent": os.path.lexists(failure)},
        "passReviewPublished": False, "deploymentAuthorized": False,
        "publication": lease.publication_evidence,
    }
    require(payload["namespaceCensus"]["successPathPresent"] is False
            and payload["namespaceCensus"]["failurePathPresent"] is False,
            "failure cannot overwrite or coexist with a published review")
    published = publisher.publish_handled_failure(lease, payload, fingerprint, rehash)
    json_equal(json_object(failure), payload, "published review failure bytes")
    post = rehash()
    require(post == fingerprint, "failure dependencies changed after publication")
    publisher.release_verified_claim(lease, published, post)
    return failure


def publish_review(paths: EvaluationPaths, bundle: Path, phase: str, *,
                   expected_manifest_sha256: str, expected_evaluator_sha256: str,
                   control_state: Mapping[str, Any], expected_dependency_records: Sequence[Mapping[str, Any]],
                   expected_namespace: Sequence[str]) -> Path:
    """Recompute under one lease; close handled failures in the reviewer itself."""
    predecessor_snapshot = verify_review_predecessor_namespace(bundle, phase, expected_namespace)
    publisher = load_publication_module(expected_dependency_records)
    destination = bundle.with_name(bundle.name + "-result-review.json")
    failure = destination.with_name(destination.stem + "-failure.json")
    candidates = review_candidates(bundle, phase, expected_dependency_records, control_state, allow_bundle=True)
    closure, fingerprint = review_failure_snapshot(candidates)
    lease, staged_pin = None, None
    try:
        candidate = audit_phase(paths, bundle, phase, expected_manifest_sha256=expected_manifest_sha256,
            expected_evaluator_sha256=expected_evaluator_sha256, control_state=control_state,
            expected_dependency_records=expected_dependency_records)
        require(review_failure_snapshot(candidates)[1] == fingerprint, "review dependencies changed before acquisition")
        current_predecessors = verify_review_predecessor_namespace(bundle, phase, expected_namespace)
        require(current_predecessors == predecessor_snapshot, "review parent changed before claim")
        lease = publisher.acquire_publication("REVIEWER", phase + "-review", destination, failure,
            {"children": predecessor_snapshot, "dependencyFingerprint": fingerprint})
        again = audit_phase(paths, bundle, phase, expected_manifest_sha256=expected_manifest_sha256,
            expected_evaluator_sha256=expected_evaluator_sha256, control_state=control_state,
            expected_dependency_records=expected_dependency_records)
        again["createdAt"] = candidate["createdAt"]
        json_equal(again, candidate, "review computation changed before publication")
        candidate["publication"] = lease.publication_evidence
        candidate["dependencyFingerprint"] = fingerprint
        write_json(lease.temp_path, candidate)
        staged_pin = pin(lease.temp_path)
        published = publisher.publish_success(lease, lease.temp_path, fingerprint,
                                               lambda: review_failure_snapshot(candidates)[1])
        json_equal(json_object(destination), candidate, "published review bytes")
        post = review_failure_snapshot(candidates)[1]
        require(post == fingerprint, "postpublication review dependency drift")
        publisher.release_verified_claim(lease, published, post)
        return destination
    except Exception as error:
        try:
            failed = publish_review_failure(bundle, phase, error, candidates=candidates,
                expected_namespace=expected_namespace, publisher=publisher, lease=lease,
                fingerprint=fingerprint, staged_pin=staged_pin)
        except Exception as publication_error:
            raise ReviewPublicationBlocked(str(publication_error)) from publication_error
        raise ReviewFailurePublished(failed, error) from error


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


def verify_mutual_source_binding(evaluator: Path) -> None:
    core, embedded = evaluation_auditor_contract(SCRIPT)
    verify_sha(evaluator, embedded, "mutually pinned evaluator")
    source = evaluator.read_text(encoding="utf-8")
    tree = ast.parse(source)
    name = "REVIEWED_R4_EVALUATION_AUDITOR_CORE_SHA256"
    nodes = [node for node in tree.body if isinstance(node, ast.AnnAssign)
             and isinstance(node.target, ast.Name) and node.target.id == name]
    stores = [node for node in ast.walk(tree) if isinstance(node, ast.Name)
              and isinstance(node.ctx, ast.Store) and node.id == name]
    require(len(nodes) == len(stores) == 1 and stores[0] is nodes[0].target,
            "evaluator auditor-core assignment must be unique")
    value = nodes[0].value
    require(isinstance(value, ast.Constant) and type(value.value) is str
            and valid_sha256(value.value) and value.value == core,
            "evaluator normalized auditor core pin mismatch")



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



def independently_verified_server_dependencies(state: Mapping[str, Any], phase: str,
                                                bundle: Path,
                                                supplied: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Rebuild the server control closure; never import evaluator/statistic code."""
    runner_path = ROOT / "scripts/run_service_v1_b1_gbt_r4.py"
    supplied_runner = [r for r in supplied if r.get("path") == str(runner_path)]
    require(len(supplied_runner) == 1, "reviewed ancestry implementation dependency missing")
    source_pin = supplied_runner[0]
    delivered = state["payloads"]["delivery_manifest"].get("controlAndImplementation", [])
    matched = [r for r in delivered if Path(r.get("logicalPath", "")).name == runner_path.name
               and r.get("kind") == "regular-file" and r.get("bytes") == source_pin["bytes"]
               and r.get("sha256") == source_pin["sha256"]]
    require(len(matched) == 1, "ancestry implementation is not bound by reviewed delivery")
    verify_pin(runner_path, source_pin, "reviewed ancestry implementation")
    spec = importlib.util.spec_from_file_location("r4_review_control_ancestry", runner_path)
    require(spec is not None and spec.loader is not None, "reviewed ancestry module unavailable")
    runner = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = runner
    spec.loader.exec_module(runner)
    paths = runner.ExecutionPaths(standalone=ROOT, team=TEAM_ROOT)
    names = {"score_manifest": "scoreManifest", "score_review": "scoreReview",
             "delivery_manifest": "deliveryManifest", "delivery_review": "deliveryReview",
             "server_receipt_manifest": "serverReceiptManifest", "server_receipt_review": "serverReceiptReview",
             "host_runtime_lock": "hostRuntimeLock"}
    canonical = {"score_manifest": paths.bundle("score") / "manifest.json", "score_review": paths.review("score"),
                 "delivery_manifest": paths.delivery_manifest, "delivery_review": paths.delivery_review,
                 "server_receipt_manifest": paths.server_receipt / "manifest.json",
                 "server_receipt_review": paths.server_receipt_review, "host_runtime_lock": paths.host_runtime_lock}
    records = {}
    for source_name, target_name in names.items():
        require(state["records"][source_name]["path"] == str(canonical[source_name]), "review control canonical path")
        records[target_name] = state["records"][source_name]
    if phase == "confirmation":
        reference = json_object(bundle / "selection-reference.json")
        verified = verify_selection_review_gate(paths.bundle("selection") / "manifest.json", paths.review("selection"),
                                                reference["manifest"]["sha256"], reference["review"]["sha256"])
        json_equal(reference, verified, "review prior selection control pins")
        records.update(selectionManifest=verified["manifest"], selectionReview=verified["review"])
    recorded_gate = json_object(bundle / "host-gate.json")
    normalized_phase = "calibrate-select" if phase == "selection" else "confirmation"
    require(recorded_gate.get("namespaceCensus", {}).get("completed") ==
            sorted(runner.expected_namespace_before(normalized_phase)), "recorded gate phase namespace")
    ancestry = runner.validate_r3_ancestry(paths)
    runner.validate_score_review(paths, canonical["score_review"])
    runner.execution_profile(paths)
    runner.verify_plan(paths)
    observed = runner._server_evaluation_dependency_records(paths, phase=phase,
                                                           control_records=records, ancestry=ancestry)
    require(sorted(observed, key=lambda r: r["path"]) == sorted(supplied, key=lambda r: r["path"]),
            "review caller dependency closure is not independently authorized")
    return observed


def verify_recorded_host_gate(
    gate: Mapping[str, Any], *, phase: str, resource: Mapping[str, Any]
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
    required_windows = {"calibrate-select": 16_200, "confirmation": 16_200}
    require(phase in required_windows and gate["phase"] == phase, "host gate phase drift")
    required = required_windows[phase]
    now = _timestamp(resource["samples"][0]["observedAt"])
    checked = _timestamp(gate["checkedAt"])
    ends = _timestamp(gate["reservationEndsAt"])
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
    require(filesystem["stageDevice"] == filesystem["finalDevice"]
            and filesystem["renameNoReplaceProbe"] is True
            and filesystem["filesystemType"] in {"ext2/ext3", "xfs"}, "atomic filesystem gate failed")
    docker = gate["docker"]
    require(isinstance(docker, Mapping) and set(docker) == {"serverVersion", "daemonId", "imageId",
        "imageUnpackedSizeBytes", "imageInspectSha256", "runningB1Containers", "restartPolicy"},
        "Docker gate shape drift")
    require(docker.get("imageId") == "sha256:de611d62db982bb833b0737a7ddd72a7cd49ab1a7dc9128669cdabae39f46df8" and docker.get("imageUnpackedSizeBytes") == 936_463_936,
            "Docker image gate failed")
    require(docker.get("runningB1Containers") == [] and docker.get("restartPolicy") == "no",
            "Docker concurrency/restart gate failed")
    cgroup = gate["cgroup"]
    require(isinstance(cgroup, Mapping) and set(cgroup) == {"version", "controllers", "probeContainerId",
        "probeInitPid", "cgroupPath", "readableFiles", "probeStatus"}, "cgroup gate shape drift")
    require(cgroup.get("version") == "v2" and cgroup.get("probeStatus") == "PASS"
            and set(cgroup.get("controllers", [])) >= {"cpu", "memory", "pids"}
            and set(cgroup.get("readableFiles", [])) == {"memory.current", "memory.peak", "memory.events",
                                                         "cpu.stat", "pids.current"},
            "cgroup-v2 gate failed")
    supervisor = gate["supervisor"]
    require(isinstance(supervisor, Mapping) and set(supervisor) == {"kind", "phaseUnitName", "probeUnitName",
        "phaseUnitExistsBefore", "probeStartCommand", "probeControlGroup", "probeMainPid", "probePidTree",
        "cpuQuotaPercent", "memoryMaxBytes", "memorySwapMaxBytes", "tasksMax", "killMode",
        "timeoutStopSeconds", "status"}, "supervisor gate shape drift")
    require(supervisor.get("status") == "PASS" and supervisor.get("phaseUnitExistsBefore") is False,
            "systemd supervisor gate failed")
    require(supervisor["kind"] == "systemd-transient-service"
            and supervisor["phaseUnitName"] == resource["unitName"]
            and supervisor["probeUnitName"] == resource["unitName"][:-8] + "-probe.service",
            "recorded supervisor identity")
    for key in ("cpuQuotaPercent", "memoryMaxBytes", "memorySwapMaxBytes", "tasksMax", "killMode", "timeoutStopSeconds"):
        require(type(supervisor[key]) is type(resource["limits"][key])
                and supervisor[key] == resource["limits"][key], "recorded supervisor limits")
    require(gate["competingProcessCensus"] == [], "competing process gate failed")
    require(gate["dockerRestartScheduled"] is False and gate["hostRebootScheduled"] is False, "scheduled maintenance conflict")
    require(gate["unknownFields"] == [], "host gate contains unknown observations")
    runtime = gate["runtime"]
    _exact(runtime, {"lock", "interpreterSha256", "venvRecordSetSha256", "environment", "importFixtureStatus"},
           "recorded runtime gate")
    _exact(runtime["lock"], {"path", "bytes", "sha256"}, "recorded runtime lock pin")
    require(type(runtime["lock"]["path"]) is str and type(runtime["lock"]["bytes"]) is int
            and runtime["lock"]["bytes"] >= 0 and valid_sha256(runtime["lock"]["sha256"])
            and valid_sha256(runtime["interpreterSha256"]) and valid_sha256(runtime["venvRecordSetSha256"])
            and runtime["importFixtureStatus"] == "PASS", "recorded runtime pin values")
    require(runtime["environment"] == {"pythonPath": "UNSET", "pythonNoUserSite": True, "isolatedMode": True,
            "pythonDontWriteBytecode": True, "locale": "C.UTF-8", "timezone": "UTC"}, "recorded runtime environment")
    require(type(gate["probeCommands"]) is list and bool(gate["probeCommands"])
            and all(type(argv) is list and bool(argv) and all(type(arg) is str for arg in argv)
                    for argv in gate["probeCommands"]), "recorded probe commands")
    require(valid_sha256(gate["probeSetSha256"]), "host probe digest drift")
    namespace = gate["namespaceCensus"]
    _exact(namespace, {"completed", "failures", "reviews", "claims", "temps", "scratches", "containers", "processes"},
           "recorded host namespace")
    require(all(type(value) is list and value == sorted(set(value)) and all(type(x) is str for x in value)
                for value in namespace.values()), "recorded namespace arrays")
    require(all(namespace[key] == [] for key in ("failures", "claims", "temps", "scratches", "containers", "processes")),
            "recorded host namespace contains forbidden preexisting work")
    return dict(gate)


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



def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("selection", "confirmation"))
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--expected-evaluator-sha256", required=True)
    parser.add_argument("--publish-review", action="store_true")
    args = parser.parse_args(argv)
    request, state, verified_dependencies = None, None, []
    bundle = ROOT / "outputs/recommendation-evidence/service-v1-pretraining-20260913" / (RUN_ID + "-" + args.phase)
    try:
        # The review coordinator sends control pins and the independently checked
        # ancestry closure, never metric/spec/threshold overrides.
        def unique(items):
            value = {}
            for key, item in items:
                require(key not in value, "duplicate review request key")
                value[key] = item
            return value
        request = json.loads(sys.stdin.read(), object_pairs_hook=unique,
                             parse_constant=lambda value: (_ for _ in ()).throw(ContractError("nonfinite JSON")))
        _exact(request, {"controls", "expectedSha256", "dependencyRecords", "expectedNamespace"}, "review request")
        _exact(request["controls"], set(CONTROL_NAMES), "review controls")
        controls = ControlPaths(**{k: Path(v) for k, v in request["controls"].items()})
        require({name: getattr(controls, name) for name in CONTROL_NAMES} == canonical_control_paths(ROOT),
                "public evaluator/reviewer control path drift")
        state = verify_score_review_gate(controls, request["expectedSha256"])
        require(sys.platform == "linux" and sys.flags.isolated == 1, "review requires sealed isolated Linux runtime")
        paths = production_paths(controls.score_manifest, controls.score_review)
        runtime_digest = verify_runtime_for_evaluation(state["payloads"]["host_runtime_lock"], ROOT)
        bundle = ROOT / "outputs/recommendation-evidence/service-v1-pretraining-20260913" / (RUN_ID + "-" + args.phase)
        require(type(request["dependencyRecords"]) is list, "review dependency records")
        rehash_records(request["dependencyRecords"])
        verified_dependencies = independently_verified_server_dependencies(
            state, args.phase, bundle, request["dependencyRecords"])
        require(type(request["expectedNamespace"]) is list, "review expectedNamespace must be the frozen array")
        verify_review_predecessor_namespace(bundle, args.phase, request["expectedNamespace"])
        kwargs = {"expected_manifest_sha256": args.expected_manifest_sha256,
                  "expected_evaluator_sha256": args.expected_evaluator_sha256,
                  "control_state": state, "expected_dependency_records": verified_dependencies}
        review = audit_phase(paths, bundle, args.phase, **kwargs)
        published = publish_review(paths, bundle, args.phase, expected_namespace=request["expectedNamespace"], **kwargs) \
            if args.publish_review else None
        require(verify_runtime_for_evaluation(state["payloads"]["host_runtime_lock"], ROOT) == runtime_digest, "runtime drift during audit")
        print(json.dumps({"status": "PASS", "phase": args.phase, "decision": review["decision"],
                          "review": str(published) if published is not None else None},
                         ensure_ascii=False, sort_keys=True), flush=True)
    except Exception as error:
        failure_path, publication_error = None, None
        if isinstance(error, ReviewFailurePublished):
            failure_path = error.path
        elif isinstance(error, ReviewPublicationBlocked):
            publication_error = str(error)
        elif args.publish_review:
            try:
                # Denied or malformed control gates must not inspect evaluation
                # inputs merely to describe failure. Only available control and
                # implementation bytes are pinned until the gate has passed.
                candidates = review_candidates(bundle, args.phase, verified_dependencies, state,
                                               allow_bundle=state is not None)
                publisher_record = path_pin(ROOT / "scripts/service_v1_b1_r4_publication.py")
                publisher = load_publication_module([publisher_record])
                namespace = review_predecessor_children(args.phase)
                failure_path = publish_review_failure(bundle, args.phase, error, candidates=candidates,
                    expected_namespace=namespace, publisher=publisher)
            except Exception as failure_error:
                publication_error = str(failure_error)
        print(json.dumps({"status": "BLOCK", "phase": args.phase, "error": str(error),
                          "reviewFailure": str(failure_path) if failure_path is not None else None,
                          "failurePublicationError": publication_error},
                         ensure_ascii=False, sort_keys=True), file=sys.stderr, flush=True)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()


