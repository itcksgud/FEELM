"""Run the audited REC-EV-027 fold-0 strict item-cold model screen."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import random
import subprocess
import sys
import time
from typing import Any, Iterable, Mapping, Sequence
import zipfile

import numpy as np
import pandas as pd
from scipy import sparse

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from build_rec_ev_019b_features import _zip_member, sha256_file  # noqa: E402
from preflight_rec_ev_027_membership import fold_of  # noqa: E402
from rec_ev_022a_core import (  # noqa: E402
    build_structured_full,
    old_user_bucket,
    pairwise_concordance,
    user_key,
    user_role_bucket,
)
from rec_ev_027_core import (  # noqa: E402
    analytic_random_top2,
    deterministic_order,
    full_history_q,
    profile_weights,
    ranked_top2_metrics,
    rating_index,
    rrf_scores,
    simultaneous_max_t,
    smoothed_q,
    splitmix_bpr_pairs,
    user_equal_prior,
    weighted_similarity_scores,
)
from validate_rec_ev_027_contract import DEFAULT, ROOT, validate  # noqa: E402


MAX_USER_ID = 300_000
TRACK = "RANDOM_ITEM_COLD"
FOLD = "0"
ENCODINGS = ("PERCENTILE_MAGNITUDE", "BINARY_SIGN")
DIRECT_MODELS = ("STRUCTURED_DIRECT", "E5_DIRECT", "STRUCTURED_E5_RRF")
LEARNED_MODELS = ("FEATURE_ONLY_LIGHTFM", "E5_TO_WARM_BPR_RIDGE", "E5_EPISODIC_CONTENT")
ALL_MODELS = DIRECT_MODELS + LEARNED_MODELS


class ResumeError(RuntimeError):
    pass


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def atomic_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    frame.to_parquet(temporary, index=False)
    os.replace(temporary, path)


def atomic_npy(path: Path, value: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        np.save(handle, value, allow_pickle=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def atomic_npz(path: Path, **values: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        np.savez_compressed(handle, **values)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def atomic_sparse(path: Path, value: sparse.spmatrix) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp.npz")
    sparse.save_npz(temporary, value, compressed=True)
    os.replace(temporary, path)


def artifact(path: Path) -> dict[str, Any]:
    return {"path": path.relative_to(ROOT).as_posix(), "bytes": path.stat().st_size, "sha256": sha256_file(path)}


def verify_artifact(spec: Mapping[str, Any]) -> Path:
    path = Path(str(spec["path"]))
    path = path if path.is_absolute() else ROOT / path
    if not path.is_file() or path.stat().st_size != int(spec["bytes"]) or sha256_file(path) != spec["sha256"]:
        raise ResumeError(f"artifact drift: {path}")
    return path


def paths(output_root: Path) -> dict[str, Path]:
    prepared = output_root / "screen/prepared"
    return {
        "lock": output_root / "screen/protocol-lock.json",
        "progress": output_root / "screen/progress.json",
        "membership_summary": output_root / "membership-preflight.json",
        "membership": output_root / "cache/membership.parquet",
        "universe": prepared / "universe.npz",
        "structured": prepared / "structured-common.npz",
        "e5": prepared / "e5-common.npy",
        "prior": prepared / "warm-prior.npz",
        "train_users": prepared / "train-user-keys.npy",
        "train_hist": prepared / "train-hist.npy",
        "train_all": prepared / "train-all.npz",
        "train_signed": prepared / "train-signed.npz",
        "lightfm_features": prepared / "lightfm-item-features.npz",
        "lightfm_feature_rows": prepared / "lightfm-row-active.npy",
        "profiles": prepared / "screen-profiles.parquet",
        "prepared_integrity": prepared / "integrity.json",
        "ridge": output_root / "screen/fits/ridge-mappers.npz",
        "fit_integrity": output_root / "screen/fits/integrity.json",
        "scores": output_root / "screen/score-ranks.parquet",
        "score_integrity": output_root / "screen/score-ranks.integrity.json",
        "labels": output_root / "screen/evaluation-labels.parquet",
        "labels_integrity": output_root / "screen/evaluation-labels.integrity.json",
        "metrics": output_root / "screen/user-metrics.parquet",
        "metrics_integrity": output_root / "screen/user-metrics.integrity.json",
        "intervals": output_root / "screen/intervals.json",
        "selection": output_root / "screen/selection.json",
        "analysis_integrity": output_root / "screen/analysis.integrity.json",
    }


def create_or_verify_lock(contract_path: Path, contract: dict[str, Any], p: dict[str, Path], resume: bool) -> dict[str, Any]:
    validate(contract, verify_files=True)
    if contract["status"] != "APPROVED_FOR_ADAPTIVE_STRICT_ITEM_COLD_EXECUTION":
        raise RuntimeError("audited approved contract required")
    membership_summary = json.loads(p["membership_summary"].read_text(encoding="utf-8"))
    membership_path = verify_artifact(membership_summary["membership"])
    if membership_path.resolve() != p["membership"].resolve():
        raise RuntimeError("membership output path drift")
    implementations = [
        ROOT / "scripts/run_rec_ev_027_screen.py",
        ROOT / "scripts/rec_ev_027_core.py",
        ROOT / "scripts/train_rec_ev_027_lightfm.py",
    ]
    expected = {
        "schema_version": 1,
        "evidence_id": "REC-EV-027-SCREEN",
        "status": "LOCKED_BEFORE_SCREEN_RATING_VALUE_ACCESS",
        "contract": artifact(contract_path),
        "audited_contract_sha256": contract["design_audit"]["audited_contract_sha256"],
        "membership": artifact(membership_path),
        "implementations": [artifact(path) for path in implementations],
        "rating_values_opened_at_lock": False,
        "timestamps_opened_at_lock": False,
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "product_policy_changed": False,
    }
    if p["lock"].exists():
        if not resume or json.loads(p["lock"].read_text(encoding="utf-8")) != expected:
            raise ResumeError("screen protocol lock drift or --resume missing")
    else:
        atomic_json(p["lock"], expected)
    return expected


def progress(p: dict[str, Path], phase: str, **extra: Any) -> None:
    value = {"phase": phase, "updated_at_unix": time.time(), **extra}
    atomic_json(p["progress"], value)
    print(json.dumps(value, ensure_ascii=False), flush=True)


def lookup_array(item_ids: np.ndarray) -> np.ndarray:
    result = np.full(int(item_ids.max()) + 1, -1, dtype=np.int32)
    result[item_ids] = np.arange(len(item_ids), dtype=np.int32)
    return result


def role_arrays() -> tuple[np.ndarray, np.ndarray]:
    old = np.fromiter((old_user_bucket(uid) <= 59 for uid in range(MAX_USER_ID + 1)), dtype=bool)
    roles = np.fromiter((user_role_bucket(uid) for uid in range(MAX_USER_ID + 1)), dtype=np.uint16)
    return old, roles


def common_features(contract: dict[str, Any]) -> tuple[np.ndarray, sparse.csr_matrix, np.ndarray]:
    outputs = contract["catalog_content_build"]["outputs"]
    structured_frame = pd.read_parquet(ROOT / outputs["structured"])
    embeddings = pd.read_parquet(ROOT / outputs["embeddings"])
    structured_candidates = structured_frame.loc[structured_frame["feature_eligible"], "movie_id"].astype(int).to_numpy()
    candidate_matrix = build_structured_full(structured_frame, structured_candidates)
    candidate_norms = np.sqrt(np.asarray(candidate_matrix.multiply(candidate_matrix).sum(axis=1)).ravel())
    structured_ids = set(map(int, structured_candidates[candidate_norms > 0]))
    embedding_rows: dict[int, np.ndarray] = {}
    for row in embeddings.itertuples(index=False):
        vector = np.asarray(row.embedding, dtype=np.float32)
        if (
            bool(row.feature_eligible)
            and row.model_id == contract["item_universe"]["e5_model_id"]
            and row.model_revision == contract["item_universe"]["e5_revision"]
            and vector.shape == (384,)
            and np.isfinite(vector).all()
            and abs(float(np.linalg.norm(vector)) - 1.0) <= 0.0001
        ):
            embedding_rows[int(row.movie_id)] = vector
    item_ids = np.asarray(sorted(structured_ids & set(embedding_rows)), dtype=np.int64)
    structured = build_structured_full(structured_frame, item_ids).tocsr().astype(np.float32)
    norms = np.sqrt(np.asarray(structured.multiply(structured).sum(axis=1)).ravel())
    if len(item_ids) != 85_517 or bool((norms <= 0).any()):
        raise RuntimeError("common feature universe drift")
    e5 = np.vstack([embedding_rows[int(movie)] for movie in item_ids]).astype(np.float32)
    return item_ids, structured, e5


def screen_membership(p: dict[str, Path]) -> pd.DataFrame:
    frame = pd.read_parquet(p["membership"])
    frame = frame.loc[
        frame["track"].eq(TRACK) & frame["fold_or_domain"].astype(str).eq(FOLD)
    ].sort_values("user_key", kind="stable", ignore_index=True)
    if len(frame) != 2089 or frame["user_key"].duplicated().any():
        raise RuntimeError("screen membership drift")
    return frame


def _parse_user_movie(raw: bytes) -> tuple[int, int, int]:
    first = raw.find(b",")
    second = raw.find(b",", first + 1)
    if first <= 0 or second <= first + 1:
        raise RuntimeError("malformed MovieLens row")
    return int(raw[:first]), int(raw[first + 1 : second]), second


def prepare(contract: dict[str, Any], p: dict[str, Path], resume: bool) -> dict[str, Any]:
    artifacts = {key: p[key] for key in (
        "universe", "structured", "e5", "prior", "train_users", "train_hist", "train_all",
        "train_signed", "lightfm_features", "lightfm_feature_rows", "profiles",
    )}
    if p["prepared_integrity"].exists():
        if not resume:
            raise ResumeError("prepared screen state requires --resume")
        integrity = json.loads(p["prepared_integrity"].read_text(encoding="utf-8"))
        for key, spec in integrity["artifacts"].items():
            verify_artifact(spec)
            if Path(spec["path"]).name != artifacts[key].name:
                raise ResumeError("prepared artifact name drift")
        return integrity["metadata"]
    if any(path.exists() for path in artifacts.values()):
        raise ResumeError("partial prepared screen state")

    progress(p, "PREPARE_FEATURE_UNIVERSE")
    item_ids, structured, e5 = common_features(contract)
    fold_salt = contract["strict_item_firewall"]["fold_salt"]
    warm = np.asarray([fold_of(int(movie), fold_salt) != 0 for movie in item_ids], dtype=bool)
    movie_lookup = lookup_array(item_ids)
    membership = screen_membership(p)
    screen_keys = set(membership["user_key"].astype(str))
    raw_to_screen: dict[int, int] = {}
    for uid in range(1, MAX_USER_ID + 1):
        key = user_key(uid)
        if key in screen_keys:
            raw_to_screen[uid] = int(membership.index[membership["user_key"].eq(key)][0])
    if len(raw_to_screen) != len(membership):
        raise RuntimeError("screen pseudonymous user reversal coverage drift")
    profile_maps = [
        {int(movie): index for index, movie in enumerate(values)}
        for values in membership["profile_movie_ids"]
    ]
    target_sets = [set(map(int, values)) for values in membership["target_movie_ids"]]
    profile_rating_idx = np.full((len(membership), 8), -1, dtype=np.int8)
    train_hist_raw = np.zeros((MAX_USER_ID + 1, 10), dtype=np.uint32)
    old, roles = role_arrays()
    archive = Path(contract["allowed_input_artifacts"]["movielens_archive"]["path"])
    counters = {
        "raw_rows": 0, "train_warm_rating_values_parsed": 0, "profile_rating_values_parsed": 0,
        "target_rows_seen_not_parsed": 0, "target_rating_values_parsed": 0, "timestamps_parsed": 0,
    }
    progress(p, "PREPARE_RATINGS_PASS1")
    with zipfile.ZipFile(archive) as bundle, bundle.open(_zip_member(bundle, "ratings.csv")) as handle:
        if handle.readline().rstrip(b"\r\n") != b"userId,movieId,rating,timestamp":
            raise RuntimeError("MovieLens ratings header drift")
        for raw in handle:
            counters["raw_rows"] += 1
            uid, movie, second = _parse_user_movie(raw)
            train_user = uid <= MAX_USER_ID and bool(old[uid]) and int(roles[uid]) < 6000
            screen_row = raw_to_screen.get(uid)
            position = int(movie_lookup[movie]) if 0 <= movie < len(movie_lookup) else -1
            should_parse_train = train_user and position >= 0 and bool(warm[position])
            profile_slot = profile_maps[screen_row].get(movie) if screen_row is not None else None
            if screen_row is not None and movie in target_sets[screen_row]:
                counters["target_rows_seen_not_parsed"] += 1
            if not should_parse_train and profile_slot is None:
                continue
            third = raw.find(b",", second + 1)
            if third <= second + 1:
                raise RuntimeError("malformed rating field")
            index = rating_index(raw[second + 1 : third])
            if should_parse_train:
                train_hist_raw[uid, index] += 1
                counters["train_warm_rating_values_parsed"] += 1
            if profile_slot is not None:
                if profile_rating_idx[screen_row, profile_slot] >= 0:
                    raise RuntimeError("duplicate screen profile rating")
                profile_rating_idx[screen_row, profile_slot] = index
                counters["profile_rating_values_parsed"] += 1
    if bool((profile_rating_idx < 0).any()) or counters["target_rating_values_parsed"] or counters["timestamps_parsed"]:
        raise RuntimeError("screen prepare label firewall failure")

    active_uids = np.flatnonzero(train_hist_raw.sum(axis=1) > 0)
    keyed = sorted((user_key(int(uid)), int(uid)) for uid in active_uids.tolist())
    train_keys = np.asarray([key for key, _ in keyed], dtype="U64")
    train_hist = np.vstack([train_hist_raw[uid] for _, uid in keyed]).astype(np.uint32)
    uid_to_row = np.full(MAX_USER_ID + 1, -1, dtype=np.int32)
    for row, (_, uid) in enumerate(keyed):
        uid_to_row[uid] = row
    pi0, g0_mid = user_equal_prior(train_hist)
    signs_by_row_bin = np.sign(
        2.0 * np.vstack([smoothed_q(range(10), hist, g0_mid) for hist in train_hist]) - 1.0
    ).astype(np.int8)
    all_count = int(train_hist.sum())
    signed_count = int(np.sum(train_hist * (signs_by_row_bin != 0)))
    all_user = np.empty(all_count, dtype=np.int32)
    all_item = np.empty(all_count, dtype=np.int32)
    all_rating = np.empty(all_count, dtype=np.int8)
    signed_user = np.empty(signed_count, dtype=np.int32)
    signed_item = np.empty(signed_count, dtype=np.int32)
    signed_value = np.empty(signed_count, dtype=np.int8)
    all_cursor = signed_cursor = 0
    progress(p, "PREPARE_RATINGS_PASS2", train_users=len(train_keys), train_interactions=all_count)
    with zipfile.ZipFile(archive) as bundle, bundle.open(_zip_member(bundle, "ratings.csv")) as handle:
        handle.readline()
        for raw in handle:
            uid, movie, second = _parse_user_movie(raw)
            if uid > MAX_USER_ID or uid_to_row[uid] < 0:
                continue
            position = int(movie_lookup[movie]) if 0 <= movie < len(movie_lookup) else -1
            if position < 0 or not bool(warm[position]):
                continue
            third = raw.find(b",", second + 1)
            index = rating_index(raw[second + 1 : third])
            row = int(uid_to_row[uid])
            all_user[all_cursor], all_item[all_cursor], all_rating[all_cursor] = row, position, index
            all_cursor += 1
            sign = int(signs_by_row_bin[row, index])
            if sign:
                signed_user[signed_cursor], signed_item[signed_cursor], signed_value[signed_cursor] = row, position, sign
                signed_cursor += 1
    if all_cursor != all_count or signed_cursor != signed_count:
        raise RuntimeError("training interaction preallocation drift")
    order = np.lexsort((all_item, all_user))
    all_user, all_item, all_rating = all_user[order], all_item[order], all_rating[order]
    signed_order = np.lexsort((signed_item, signed_user))
    signed_user, signed_item, signed_value = signed_user[signed_order], signed_item[signed_order], signed_value[signed_order]
    if len(all_user) > 1 and bool(((all_user[1:] == all_user[:-1]) & (all_item[1:] == all_item[:-1])).any()):
        raise RuntimeError("duplicate model-train user-movie rating")
    interactions = sparse.csr_matrix(
        (signed_value, (signed_user, signed_item)), shape=(len(train_keys), len(item_ids)), dtype=np.int8
    )
    interactions.sort_indices()
    touched_items = np.unique(interactions.indices)
    feature_mask = np.asarray(structured[touched_items].getnnz(axis=0)).ravel() > 0
    lightfm_features = structured[:, feature_mask].tocsr().astype(np.float32)
    row_norms = np.sqrt(np.asarray(lightfm_features.multiply(lightfm_features).sum(axis=1)).ravel())
    row_active = row_norms > 0
    lightfm_features = sparse.diags(
        np.divide(1.0, row_norms, out=np.zeros_like(row_norms), where=row_active).astype(np.float32)
    ) @ lightfm_features
    lightfm_features = lightfm_features.tocsr().astype(np.float32)
    profiles = membership[["user_key", "profile_movie_ids", "target_movie_ids"]].copy()
    profiles["profile_rating_idx"] = [row.tolist() for row in profile_rating_idx]

    atomic_npz(p["universe"], item_ids=item_ids.astype(np.int32), warm_mask=warm)
    atomic_sparse(p["structured"], structured)
    atomic_npy(p["e5"], e5)
    atomic_npz(p["prior"], pi0=pi0, g0_mid=g0_mid)
    atomic_npy(p["train_users"], train_keys)
    atomic_npy(p["train_hist"], train_hist)
    atomic_npz(p["train_all"], user=all_user, item=all_item, rating_idx=all_rating)
    atomic_sparse(p["train_signed"], interactions)
    atomic_sparse(p["lightfm_features"], lightfm_features)
    atomic_npy(p["lightfm_feature_rows"], row_active)
    atomic_parquet(p["profiles"], profiles)
    metadata = {
        "common_items": len(item_ids), "warm_items": int(warm.sum()), "cold_items": int((~warm).sum()),
        "screen_users": len(profiles), "train_users": len(train_keys), "train_warm_interactions": all_count,
        "signed_interactions": interactions.nnz, "positive": int(np.count_nonzero(interactions.data == 1)),
        "negative": int(np.count_nonzero(interactions.data == -1)), "lightfm_columns": lightfm_features.shape[1],
        "lightfm_active_cold_rows": int((row_active & ~warm).sum()), "reader": counters,
        "rating_values_opened": True, "target_rating_values_opened": False, "timestamps_opened": False,
    }
    atomic_json(p["prepared_integrity"], {
        "status": "SEALED_SCREEN_PREPARED_WITHOUT_TARGET_LABELS",
        "artifacts": {key: artifact(path) for key, path in artifacts.items()},
        "metadata": metadata,
    })
    progress(p, "PREPARED", **metadata)
    return metadata


def _job_integrity(path: Path, members: Mapping[str, Path], metadata: Mapping[str, Any]) -> None:
    atomic_json(path, {"artifacts": {key: artifact(value) for key, value in members.items()}, "metadata": dict(metadata)})


def _verify_job(path: Path, members: Mapping[str, Path]) -> dict[str, Any]:
    if not path.is_file():
        raise ResumeError(f"missing integrity: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    for key, expected in value["artifacts"].items():
        observed_path = verify_artifact(expected)
        if observed_path.resolve() != members[key].resolve():
            raise ResumeError(f"job member path drift: {key}")
    return value


def fit_lightfm(contract: dict[str, Any], p: dict[str, Path], output_root: Path, resume: bool) -> list[Path]:
    model = contract["models"]["FEATURE_ONLY_LIGHTFM"]
    outputs: list[Path] = []
    for seed_raw in model["seeds"]:
        seed = int(seed_raw)
        directory = output_root / f"screen/fits/lightfm/S{seed}"
        config_path = directory / "config.json"
        result_path = directory / "result.npz"
        integrity_path = directory / "integrity.json"
        config = {
            "seed": seed, "dimension": int(model["parameters"]["dimension"]),
            "epochs": int(model["parameters"]["epochs"]),
            "learning_rate": float(model["parameters"]["learning_rate"]),
            "item_alpha": float(model["parameters"]["item_alpha"]),
            "user_alpha": float(model["parameters"]["user_alpha"]),
            "interaction_sha256": sha256_file(p["train_signed"]),
            "item_feature_sha256": sha256_file(p["lightfm_features"]),
            "item_identity_features": False,
        }
        if integrity_path.exists():
            if not resume or not all(path.is_file() for path in (config_path, result_path, integrity_path)):
                raise ResumeError(f"partial LightFM seed {seed}")
            if json.loads(config_path.read_text(encoding="utf-8")) != config:
                raise ResumeError(f"LightFM seed {seed} config drift")
            _verify_job(integrity_path, {"config": config_path, "result": result_path})
            outputs.extend((config_path, result_path, integrity_path))
            continue
        if result_path.exists():
            raise ResumeError(f"unsealed LightFM seed {seed} result")
        if config_path.exists():
            if not resume or json.loads(config_path.read_text(encoding="utf-8")) != config:
                raise ResumeError(f"LightFM seed {seed} config drift or --resume missing")
        else:
            directory.mkdir(parents=True, exist_ok=True)
            atomic_json(config_path, config)
        rel_job = directory.relative_to(ROOT).as_posix()
        rel_interactions = p["train_signed"].relative_to(ROOT).as_posix()
        rel_features = p["lightfm_features"].relative_to(ROOT).as_posix()
        command = "\n".join([
            "python -m pip install --disable-pip-version-check --require-hashes -r requirements-rec-ev-019c.lock",
            f"python scripts/train_rec_ev_027_lightfm.py --job /workspace/{rel_job} --interactions /workspace/{rel_interactions} --item-features /workspace/{rel_features}",
        ])
        progress(p, "FIT_LIGHTFM", seed=seed)
        completed = subprocess.run([
            "docker", "run", "--rm", "--platform", "linux/amd64",
            "--mount", f"type=bind,source={ROOT},target=/workspace",
            "--mount", "type=volume,source=feelm-rec-ev-019c-pip,target=/root/.cache/pip",
            "--workdir", "/workspace", model["dependency"]["runtime_image"], "sh", "-ec", command,
        ], check=False)
        if completed.returncode:
            raise RuntimeError(f"LightFM seed {seed} failed: {completed.returncode}")
        fitted = np.load(result_path, allow_pickle=False)
        if fitted["item_factors"].shape != (85_517, 128) or not np.isfinite(fitted["item_factors"]).all():
            raise RuntimeError(f"LightFM seed {seed} output drift")
        _job_integrity(integrity_path, {"config": config_path, "result": result_path}, {"seed": seed})
        outputs.extend((config_path, result_path, integrity_path))
    return outputs


def train_bpr(
    interactions: sparse.csr_matrix,
    user_keys: np.ndarray,
    item_ids: np.ndarray,
    *,
    seed: int,
    contract: dict[str, Any],
    p: dict[str, Path],
) -> tuple[np.ndarray, np.ndarray]:
    config = contract["models"]["E5_TO_WARM_BPR_RIDGE"]["teacher_parameters"]
    factors = int(config["factors"])
    rng = np.random.Generator(np.random.PCG64(seed))
    user_factors = rng.normal(0.0, 0.01, size=(interactions.shape[0], factors)).astype(np.float32)
    item_factors = rng.normal(0.0, 0.01, size=(interactions.shape[1], factors)).astype(np.float32)
    touched = np.zeros(interactions.shape[1], dtype=bool)
    batch_size = int(config["batch_size"])
    regularization = float(config["regularization"])
    learning_rate = float(config["learning_rate"])
    for epoch in range(int(config["epochs"])):
        users: list[int] = []
        likes: list[int] = []
        dislikes: list[int] = []
        for user in range(interactions.shape[0]):
            start, stop = interactions.indptr[user : user + 2]
            positions = interactions.indices[start:stop]
            values = interactions.data[start:stop]
            pairs = splitmix_bpr_pairs(
                positions[values == 1], positions[values == -1], item_ids=item_ids,
                track=TRACK, fold=FOLD, seed=seed, epoch=epoch, user_key=str(user_keys[user]),
                maximum_pairs=int(config["maximum_pairs_per_user_epoch"]),
            )
            users.extend([user] * len(pairs))
            likes.extend(pair[0] for pair in pairs)
            dislikes.extend(pair[1] for pair in pairs)
        user_array = np.asarray(users, dtype=np.int32)
        like_array = np.asarray(likes, dtype=np.int32)
        dislike_array = np.asarray(dislikes, dtype=np.int32)
        touched[like_array] = True
        touched[dislike_array] = True
        for start in range(0, len(user_array), batch_size):
            stop = start + batch_size
            u_idx, i_idx, j_idx = user_array[start:stop], like_array[start:stop], dislike_array[start:stop]
            u = user_factors[u_idx].astype(np.float64)
            i = item_factors[i_idx].astype(np.float64)
            j = item_factors[j_idx].astype(np.float64)
            margin = np.clip(np.sum(u * (i - j), axis=1), -35.0, 35.0)
            coefficient = 1.0 / (1.0 + np.exp(margin))
            grad_u = coefficient[:, None] * (i - j) - regularization * u
            grad_i = coefficient[:, None] * u - regularization * i
            grad_j = -coefficient[:, None] * u - regularization * j
            unique_users, user_inverse, user_counts = np.unique(
                u_idx, return_inverse=True, return_counts=True
            )
            accumulated_users = np.zeros((len(unique_users), factors), dtype=np.float64)
            np.add.at(accumulated_users, user_inverse, grad_u)
            user_factors[unique_users] += (
                learning_rate * accumulated_users / user_counts[:, None]
            ).astype(np.float32)

            item_indices = np.concatenate((i_idx, j_idx))
            item_gradients = np.vstack((grad_i, grad_j))
            unique_items, item_inverse, item_counts = np.unique(
                item_indices, return_inverse=True, return_counts=True
            )
            accumulated_items = np.zeros((len(unique_items), factors), dtype=np.float64)
            np.add.at(accumulated_items, item_inverse, item_gradients)
            item_factors[unique_items] += (
                learning_rate * accumulated_items / item_counts[:, None]
            ).astype(np.float32)
        progress(p, "FIT_BPR", seed=seed, epoch=epoch + 1, pairs=len(user_array), touched=int(touched.sum()))
    return item_factors, touched


def _ridge_inner(movie_id: int) -> bool:
    payload = f"REC_EV_027_RIDGE_INNER|{TRACK}|{FOLD}|{int(movie_id)}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big", signed=False) % 5 == 0


def fit_bpr_and_ridge(contract: dict[str, Any], p: dict[str, Path], output_root: Path, resume: bool) -> list[Path]:
    model = contract["models"]["E5_TO_WARM_BPR_RIDGE"]
    universe = np.load(p["universe"], allow_pickle=False)
    item_ids, warm = universe["item_ids"].astype(np.int64), universe["warm_mask"].astype(bool)
    interactions = sparse.load_npz(p["train_signed"]).tocsr()
    user_keys = np.load(p["train_users"], allow_pickle=False)
    bpr_paths: list[Path] = []
    factors_by_seed: dict[int, np.ndarray] = {}
    touched_by_seed: dict[int, np.ndarray] = {}
    for seed_raw in model["teacher_seeds"]:
        seed = int(seed_raw)
        path = output_root / f"screen/fits/bpr/S{seed}.npz"
        if path.exists():
            if not resume:
                raise ResumeError(f"BPR seed {seed} requires --resume")
            saved = np.load(path, allow_pickle=False)
            factors, touched = saved["item_factors"].astype(np.float32), saved["pair_touched"].astype(bool)
        else:
            factors, touched = train_bpr(interactions, user_keys, item_ids, seed=seed, contract=contract, p=p)
            atomic_npz(path, item_factors=factors, pair_touched=touched)
        if factors.shape != (len(item_ids), 128) or touched.shape != (len(item_ids),) or bool((touched & ~warm).any()):
            raise RuntimeError(f"BPR seed {seed} strict item firewall drift")
        factors_by_seed[seed], touched_by_seed[seed] = factors, touched
        bpr_paths.append(path)

    if p["ridge"].exists():
        if not resume:
            raise ResumeError("ridge mapper requires --resume")
        return [*bpr_paths, p["ridge"]]
    e5 = np.load(p["e5"], allow_pickle=False).astype(np.float64)
    inner = np.asarray([_ridge_inner(int(movie)) for movie in item_ids], dtype=bool)
    alpha_grid = [float(value) for value in model["alpha_grid"]]
    scores: list[float] = []
    identity = np.eye(384, dtype=np.float64)
    for alpha in alpha_grid:
        seed_means: list[float] = []
        for seed in map(int, model["teacher_seeds"]):
            touched = touched_by_seed[seed] & warm
            train_mask, valid_mask = touched & ~inner, touched & inner
            if train_mask.sum() < 1000 or valid_mask.sum() < 200:
                raise RuntimeError(f"ridge inner support failed for seed {seed}")
            y = factors_by_seed[seed].astype(np.float64)
            y_norm = np.linalg.norm(y, axis=1)
            y_unit = np.divide(y, y_norm[:, None], out=np.zeros_like(y), where=y_norm[:, None] > 0)
            x_train, y_train = e5[train_mask], y_unit[train_mask]
            beta = np.linalg.solve(x_train.T @ x_train + alpha * identity, x_train.T @ y_train)
            prediction = e5[valid_mask] @ beta
            norms = np.linalg.norm(prediction, axis=1)
            if bool((norms <= 0).any()) or not np.isfinite(prediction).all():
                raise RuntimeError("ridge validation prediction invalid")
            prediction /= norms[:, None]
            seed_means.append(float(np.mean(np.sum(prediction * y_unit[valid_mask], axis=1))))
        scores.append(float(np.mean(seed_means)))
    best_index = sorted(range(len(alpha_grid)), key=lambda index: (-scores[index], alpha_grid[index]))[0]
    alpha = alpha_grid[best_index]
    betas: list[np.ndarray] = []
    for seed in map(int, model["teacher_seeds"]):
        mask = touched_by_seed[seed] & warm
        y = factors_by_seed[seed].astype(np.float64)
        y_norm = np.linalg.norm(y, axis=1)
        y_unit = np.divide(y, y_norm[:, None], out=np.zeros_like(y), where=y_norm[:, None] > 0)
        x_train, y_train = e5[mask], y_unit[mask]
        beta = np.linalg.solve(x_train.T @ x_train + alpha * identity, x_train.T @ y_train)
        betas.append(beta)
    atomic_npz(
        p["ridge"], betas=np.asarray(betas, dtype=np.float64), alpha=np.asarray([alpha]),
        alpha_grid=np.asarray(alpha_grid), validation_cosine=np.asarray(scores), seeds=np.asarray(model["teacher_seeds"]),
    )
    progress(p, "FIT_RIDGE", alpha=alpha, validation_cosine=scores)
    return [*bpr_paths, p["ridge"]]


def _episode_order(
    positions: Sequence[int], item_ids: np.ndarray, *, salt: str, seed: int, epoch: int, key: str
) -> list[int]:
    return sorted(map(int, positions), key=lambda position: (
        hashlib.sha256(
            f"{salt}|{TRACK}|{FOLD}|{seed}|{epoch}|{key}|{int(item_ids[position])}".encode("utf-8")
        ).digest(),
        int(item_ids[position]),
    ))


def fit_episodic(contract: dict[str, Any], p: dict[str, Path], output_root: Path, resume: bool) -> list[Path]:
    import torch
    from torch import nn
    from torch.nn import functional as functional

    class Encoder(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.first = nn.Linear(384, 256, bias=True)
            self.dropout = nn.Dropout(0.3)
            self.second = nn.Linear(256, 128, bias=True)

        def forward(self, value: torch.Tensor) -> torch.Tensor:
            value = self.second(self.dropout(functional.gelu(self.first(value))))
            return functional.normalize(value, p=2, dim=-1)

    torch.set_num_threads(1)
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        pass
    model_contract = contract["models"]["E5_EPISODIC_CONTENT"]
    params = model_contract["parameters"]
    universe = np.load(p["universe"], allow_pickle=False)
    item_ids = universe["item_ids"].astype(np.int64)
    e5 = np.load(p["e5"], allow_pickle=False).astype(np.float32)
    all_rows = np.load(p["train_all"], allow_pickle=False)
    users = all_rows["user"].astype(np.int32)
    items = all_rows["item"].astype(np.int32)
    ratings = all_rows["rating_idx"].astype(np.int8)
    keys = np.load(p["train_users"], allow_pickle=False)
    hist = np.load(p["train_hist"], allow_pickle=False)
    prior = np.load(p["prior"], allow_pickle=False)["g0_mid"].astype(np.float64)
    q_table = np.vstack([smoothed_q(range(10), row, prior) for row in hist])
    counts = np.bincount(users, minlength=len(keys))
    indptr = np.concatenate([[0], np.cumsum(counts)]).astype(np.int64)
    outputs: list[Path] = []
    for seed_raw in model_contract["seeds"]:
        seed = int(seed_raw)
        destination = output_root / f"screen/fits/episodic/S{seed}.pt"
        if destination.exists():
            if not resume:
                raise ResumeError(f"episodic seed {seed} requires --resume")
            outputs.append(destination)
            continue
        random.seed(seed)
        np.random.Generator(np.random.PCG64(seed))
        torch.manual_seed(seed)
        torch.use_deterministic_algorithms(True)
        encoder = Encoder()
        optimizer = torch.optim.AdamW(
            encoder.parameters(), lr=float(params["learning_rate"]), betas=tuple(params["betas"]),
            eps=float(params["epsilon"]), weight_decay=float(params["weight_decay"]),
        )
        for epoch in range(int(params["epochs"])):
            profile_rows: list[list[int]] = []
            profile_weight_rows: list[list[float]] = []
            positives: list[int] = []
            negatives: list[int] = []
            for user in range(len(keys)):
                start, stop = int(indptr[user]), int(indptr[user + 1])
                if stop - start < 10:
                    continue
                user_items = items[start:stop]
                user_ratings = ratings[start:stop]
                rating_by_item = {int(item): int(rating) for item, rating in zip(user_items, user_ratings, strict=True)}
                ordered = _episode_order(
                    user_items, item_ids, salt="REC_EV_027_EPISODE_PROFILE_V1",
                    seed=seed, epoch=epoch, key=str(keys[user]),
                )
                profile = ordered[:8]
                remaining = ordered[8:]
                positive_pool = [item for item in remaining if q_table[user, rating_by_item[item]] >= 0.80]
                negative_pool = [item for item in remaining if q_table[user, rating_by_item[item]] <= 0.20]
                if not positive_pool or not negative_pool:
                    continue
                positive = _episode_order(
                    positive_pool, item_ids, salt="REC_EV_027_EPISODE_POSITIVE_V1",
                    seed=seed, epoch=epoch, key=str(keys[user]),
                )[0]
                negative = _episode_order(
                    negative_pool, item_ids, salt="REC_EV_027_EPISODE_NEGATIVE_V1",
                    seed=seed, epoch=epoch, key=str(keys[user]),
                )[0]
                weights = [2.0 * q_table[user, rating_by_item[item]] - 1.0 for item in profile]
                if float(np.abs(weights).sum()) <= 0:
                    continue
                profile_rows.append(profile)
                profile_weight_rows.append(weights)
                positives.append(positive)
                negatives.append(negative)
            encoder.train()
            batch_size = int(params["batch_size"])
            losses: list[float] = []
            for start in range(0, len(profile_rows), batch_size):
                stop = start + batch_size
                profile_idx = np.asarray(profile_rows[start:stop], dtype=np.int64)
                weights_np = np.asarray(profile_weight_rows[start:stop], dtype=np.float32)
                positive_idx = np.asarray(positives[start:stop], dtype=np.int64)
                negative_idx = np.asarray(negatives[start:stop], dtype=np.int64)
                profile_encoded = encoder(torch.from_numpy(e5[profile_idx]))
                weights_t = torch.from_numpy(weights_np)
                profile_vector = (profile_encoded * weights_t[..., None]).sum(dim=1) / weights_t.abs().sum(dim=1, keepdim=True)
                positive_encoded = encoder(torch.from_numpy(e5[positive_idx]))
                negative_encoded = encoder(torch.from_numpy(e5[negative_idx]))
                margin = (profile_vector * positive_encoded).sum(dim=1) - (profile_vector * negative_encoded).sum(dim=1)
                loss = functional.softplus(-margin).mean()
                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                optimizer.step()
                losses.append(float(loss.detach().cpu()))
            progress(
                p, "FIT_EPISODIC", seed=seed, epoch=epoch + 1, episodes=len(profile_rows),
                mean_loss=float(np.mean(losses)) if losses else None,
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
        torch.save(encoder.state_dict(), temporary)
        os.replace(temporary, destination)
        outputs.append(destination)
    return outputs


def fit(contract: dict[str, Any], p: dict[str, Path], output_root: Path, resume: bool) -> dict[str, Any]:
    prepared = json.loads(p["prepared_integrity"].read_text(encoding="utf-8"))
    for spec in prepared["artifacts"].values():
        verify_artifact(spec)
    if p["fit_integrity"].exists():
        if not resume:
            raise ResumeError("fit state requires --resume")
        integrity = json.loads(p["fit_integrity"].read_text(encoding="utf-8"))
        for spec in integrity["artifacts"]:
            verify_artifact(spec)
        return integrity
    lightfm = fit_lightfm(contract, p, output_root, resume)
    bpr = fit_bpr_and_ridge(contract, p, output_root, resume)
    episodic = fit_episodic(contract, p, output_root, resume)
    members = [*lightfm, *bpr, *episodic]
    value = {"status": "SEALED_SCREEN_MODEL_FITS", "artifacts": [artifact(path) for path in members]}
    atomic_json(p["fit_integrity"], value)
    progress(p, "FITS_COMPLETE", artifacts=len(members))
    return value


def fold_in_lightfm(
    biases: np.ndarray,
    factors: np.ndarray,
    profile_positions: np.ndarray,
    weights: np.ndarray,
    target_positions: np.ndarray,
    *,
    regularization: float,
) -> tuple[np.ndarray, bool]:
    nonzero = np.abs(weights) > 0
    if not bool(nonzero.any()):
        return np.full(len(target_positions), np.nan), False
    observed = profile_positions[nonzero]
    labels = np.sign(weights[nonzero]).astype(np.float64)
    absolute = np.abs(weights[nonzero]).astype(np.float64)
    confidence = absolute / absolute.mean()
    user = np.zeros(factors.shape[1], dtype=np.float64)
    profile = factors[observed].astype(np.float64)
    profile_bias = biases[observed].astype(np.float64)
    for _ in range(80):
        margins = labels * (profile_bias + profile @ user)
        sigmoid_negative = np.empty_like(margins)
        positive = margins >= 0
        sigmoid_negative[positive] = np.exp(-margins[positive]) / (1.0 + np.exp(-margins[positive]))
        sigmoid_negative[~positive] = 1.0 / (1.0 + np.exp(margins[~positive]))
        gradient = -((confidence * labels * sigmoid_negative)[:, None] * profile).sum(axis=0) / confidence.sum()
        gradient += float(regularization) * user
        user -= 0.05 * gradient
    scores = biases[target_positions].astype(np.float64) + factors[target_positions].astype(np.float64) @ user
    return scores, bool(np.isfinite(scores).all() and np.unique(scores).size >= 2)


def episodic_embeddings(path: Path, e5: np.ndarray) -> np.ndarray:
    import torch
    from torch import nn
    from torch.nn import functional as functional

    class Encoder(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.first = nn.Linear(384, 256, bias=True)
            self.dropout = nn.Dropout(0.3)
            self.second = nn.Linear(256, 128, bias=True)

        def forward(self, value: torch.Tensor) -> torch.Tensor:
            return functional.normalize(self.second(self.dropout(functional.gelu(self.first(value)))), p=2, dim=-1)

    encoder = Encoder()
    encoder.load_state_dict(torch.load(path, map_location="cpu", weights_only=True))
    encoder.eval()
    rows: list[np.ndarray] = []
    with torch.no_grad():
        for start in range(0, len(e5), 1024):
            rows.append(encoder(torch.from_numpy(e5[start : start + 1024])).cpu().numpy().astype(np.float32))
    result = np.vstack(rows)
    if result.shape != (len(e5), 128) or not np.isfinite(result).all():
        raise RuntimeError("episodic encoded item matrix drift")
    return result


def _model_order(
    movies: np.ndarray, scores: np.ndarray, *, model: str, encoding: str, key: str
) -> tuple[np.ndarray, bool]:
    active = bool(np.isfinite(scores).all() and np.unique(scores).size >= 2)
    if not active:
        return np.empty(0, dtype=np.int16), False
    return deterministic_order(
        movies, scores, phase="SCREEN", track=TRACK, fold=FOLD,
        model=model, encoding=encoding, user_key=key,
    ), True


def score(contract: dict[str, Any], p: dict[str, Path], output_root: Path, resume: bool) -> dict[str, Any]:
    if p["scores"].exists() or p["score_integrity"].exists():
        if not resume or not p["scores"].is_file() or not p["score_integrity"].is_file():
            raise ResumeError("partial screen score state")
        integrity = json.loads(p["score_integrity"].read_text(encoding="utf-8"))
        verify_artifact(integrity["score_ranks"])
        return integrity
    fit_integrity = json.loads(p["fit_integrity"].read_text(encoding="utf-8"))
    for spec in fit_integrity["artifacts"]:
        verify_artifact(spec)
    universe = np.load(p["universe"], allow_pickle=False)
    item_ids = universe["item_ids"].astype(np.int64)
    movie_lookup = lookup_array(item_ids)
    structured = sparse.load_npz(p["structured"]).tocsr()
    e5 = np.load(p["e5"], allow_pickle=False).astype(np.float32)
    prior = np.load(p["prior"], allow_pickle=False)["g0_mid"].astype(np.float64)
    profiles = pd.read_parquet(p["profiles"]).sort_values("user_key", kind="stable", ignore_index=True)
    lightfm_active_rows = np.load(p["lightfm_feature_rows"], allow_pickle=False).astype(bool)
    seeds = list(map(int, contract["models"]["FEATURE_ONLY_LIGHTFM"]["seeds"]))
    lightfm_fits = {
        seed: np.load(output_root / f"screen/fits/lightfm/S{seed}/result.npz", allow_pickle=False)
        for seed in seeds
    }
    bpr_fits = {
        seed: np.load(output_root / f"screen/fits/bpr/S{seed}.npz", allow_pickle=False)
        for seed in seeds
    }
    ridge = np.load(p["ridge"], allow_pickle=False)
    betas = ridge["betas"].astype(np.float64)
    predicted_cold: dict[int, np.ndarray] = {}
    for seed_index, seed in enumerate(seeds):
        prediction = e5.astype(np.float64) @ betas[seed_index]
        norms = np.linalg.norm(prediction, axis=1)
        predicted_cold[seed] = np.divide(
            prediction, norms[:, None], out=np.full_like(prediction, np.nan), where=norms[:, None] > 0
        )
    episodic = {
        seed: episodic_embeddings(output_root / f"screen/fits/episodic/S{seed}.pt", e5)
        for seed in seeds
    }
    rows: list[dict[str, Any]] = []
    progress(p, "SCORE_SCREEN", users=len(profiles))
    for user_number, row in enumerate(profiles.itertuples(index=False), start=1):
        key = str(row.user_key)
        profile_movies = np.asarray(row.profile_movie_ids, dtype=np.int64)
        target_movies = np.asarray(row.target_movie_ids, dtype=np.int64)
        profile_positions, target_positions = movie_lookup[profile_movies], movie_lookup[target_movies]
        if bool((profile_positions < 0).any()) or bool((target_positions < 0).any()):
            raise RuntimeError("screen membership movie outside common support")
        rating_indices = np.asarray(row.profile_rating_idx, dtype=np.int8)
        structured_similarity = (structured[target_positions] @ structured[profile_positions].T).toarray().astype(np.float64)
        e5_similarity = e5[target_positions].astype(np.float64) @ e5[profile_positions].astype(np.float64).T
        for encoding in ENCODINGS:
            weights = profile_weights(rating_indices, prior, encoding)
            model_scores: dict[str, np.ndarray] = {}
            model_orders: dict[str, np.ndarray] = {}
            structured_scores, _ = weighted_similarity_scores(structured_similarity, weights)
            e5_scores, _ = weighted_similarity_scores(e5_similarity, weights)
            for name, values in (("STRUCTURED_DIRECT", structured_scores), ("E5_DIRECT", e5_scores)):
                order, active = _model_order(target_movies, values, model=name, encoding=encoding, key=key)
                model_scores[name], model_orders[name] = values, order
                rows.append({
                    "user_key": key, "model": name, "encoding": encoding, "active": active,
                    "target_movie_ids": target_movies.tolist(), "scores": values.tolist() if active else [],
                    "ranked_target_indices": order.tolist(),
                })
            rrf = rrf_scores([model_orders[name] for name in ("STRUCTURED_DIRECT", "E5_DIRECT")], len(target_movies))
            rrf_order, rrf_active = _model_order(
                target_movies, rrf, model="STRUCTURED_E5_RRF", encoding=encoding, key=key
            )
            rows.append({
                "user_key": key, "model": "STRUCTURED_E5_RRF", "encoding": encoding, "active": rrf_active,
                "target_movie_ids": target_movies.tolist(), "scores": rrf.tolist() if rrf_active else [],
                "ranked_target_indices": rrf_order.tolist(),
            })

            lightfm_seed_scores: list[np.ndarray] = []
            lightfm_active = bool(lightfm_active_rows[profile_positions].all() and lightfm_active_rows[target_positions].all())
            if lightfm_active:
                for seed in seeds:
                    fitted = lightfm_fits[seed]
                    values, active = fold_in_lightfm(
                        fitted["item_biases"], fitted["item_factors"], profile_positions, weights, target_positions,
                        regularization=float(contract["models"]["FEATURE_ONLY_LIGHTFM"]["parameters"]["user_alpha"]),
                    )
                    lightfm_active &= active
                    lightfm_seed_scores.append(values)
            lightfm_values = np.mean(lightfm_seed_scores, axis=0) if lightfm_active else np.full(len(target_movies), np.nan)
            lightfm_order, lightfm_active = _model_order(
                target_movies, lightfm_values, model="FEATURE_ONLY_LIGHTFM", encoding=encoding, key=key
            )
            rows.append({
                "user_key": key, "model": "FEATURE_ONLY_LIGHTFM", "encoding": encoding, "active": lightfm_active,
                "target_movie_ids": target_movies.tolist(), "scores": lightfm_values.tolist() if lightfm_active else [],
                "ranked_target_indices": lightfm_order.tolist(),
            })

            bpr_seed_scores: list[np.ndarray] = []
            bpr_active = True
            for seed in seeds:
                fitted = bpr_fits[seed]
                touched = fitted["pair_touched"].astype(bool)
                if not bool(touched[profile_positions].all()):
                    bpr_active = False
                    break
                actual = fitted["item_factors"].astype(np.float64)[profile_positions]
                norms = np.linalg.norm(actual, axis=1)
                if bool((norms <= 0).any()):
                    bpr_active = False
                    break
                actual /= norms[:, None]
                target_predicted = predicted_cold[seed][target_positions]
                similarities = target_predicted @ actual.T
                values, active = weighted_similarity_scores(similarities, weights)
                bpr_active &= active
                bpr_seed_scores.append(values)
            bpr_values = np.mean(bpr_seed_scores, axis=0) if bpr_active else np.full(len(target_movies), np.nan)
            bpr_order, bpr_active = _model_order(
                target_movies, bpr_values, model="E5_TO_WARM_BPR_RIDGE", encoding=encoding, key=key
            )
            rows.append({
                "user_key": key, "model": "E5_TO_WARM_BPR_RIDGE", "encoding": encoding, "active": bpr_active,
                "target_movie_ids": target_movies.tolist(), "scores": bpr_values.tolist() if bpr_active else [],
                "ranked_target_indices": bpr_order.tolist(),
            })

            episodic_seed_scores: list[np.ndarray] = []
            for seed in seeds:
                encoded = episodic[seed]
                profile_vector = (encoded[profile_positions].astype(np.float64) * weights[:, None]).sum(axis=0)
                denominator = float(np.abs(weights).sum())
                values = encoded[target_positions].astype(np.float64) @ (profile_vector / denominator) if denominator > 0 else np.full(len(target_movies), np.nan)
                episodic_seed_scores.append(values)
            episodic_values = np.mean(episodic_seed_scores, axis=0)
            episodic_order, episodic_active = _model_order(
                target_movies, episodic_values, model="E5_EPISODIC_CONTENT", encoding=encoding, key=key
            )
            rows.append({
                "user_key": key, "model": "E5_EPISODIC_CONTENT", "encoding": encoding, "active": episodic_active,
                "target_movie_ids": target_movies.tolist(), "scores": episodic_values.tolist() if episodic_active else [],
                "ranked_target_indices": episodic_order.tolist(),
            })
        if user_number % 250 == 0:
            progress(p, "SCORE_SCREEN", users_complete=user_number, users_total=len(profiles))
    result = pd.DataFrame(rows).sort_values(["encoding", "model", "user_key"], kind="stable", ignore_index=True)
    expected = len(profiles) * len(ENCODINGS) * len(ALL_MODELS)
    if len(result) != expected or result.duplicated(["user_key", "model", "encoding"]).any():
        raise RuntimeError("screen score row-set drift")
    atomic_parquet(p["scores"], result)
    integrity = {
        "status": "SEALED_ALL_SCREEN_PRIMARY_AND_BINARY_SCORES_AND_RANKS_BEFORE_TARGET_LABELS",
        "score_ranks": artifact(p["scores"]),
        "rows": len(result), "users": len(profiles), "models": list(ALL_MODELS), "encodings": list(ENCODINGS),
        "target_rating_values_opened": False, "timestamps_opened": False,
    }
    atomic_json(p["score_integrity"], integrity)
    progress(p, "SCORES_SEALED", rows=len(result))
    return integrity


def _screen_user_rows(profiles: pd.DataFrame) -> dict[int, int]:
    wanted = {str(value): index for index, value in enumerate(profiles["user_key"].astype(str))}
    result: dict[int, int] = {}
    for uid in range(1, MAX_USER_ID + 1):
        row = wanted.get(user_key(uid))
        if row is not None:
            result[uid] = row
    if len(result) != len(profiles):
        raise RuntimeError("screen evaluation user reversal coverage drift")
    return result


def open_screen_labels(
    contract: dict[str, Any], p: dict[str, Path], profiles: pd.DataFrame, resume: bool
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if p["labels"].exists() != p["labels_integrity"].exists():
        raise ResumeError("partial screen label state")
    if p["labels"].exists():
        if not resume:
            raise ResumeError("screen labels require --resume")
        integrity = json.loads(p["labels_integrity"].read_text(encoding="utf-8"))
        verify_artifact(integrity["evaluation_labels"])
        return pd.read_parquet(p["labels"]), integrity

    score_integrity = json.loads(p["score_integrity"].read_text(encoding="utf-8"))
    score_path = verify_artifact(score_integrity["score_ranks"])
    if score_path.resolve() != p["scores"].resolve() or not score_integrity.get("target_rating_values_opened") is False:
        raise RuntimeError("complete pre-label score/rank seal required")

    profiles = profiles.sort_values("user_key", kind="stable", ignore_index=True)
    raw_to_row = _screen_user_rows(profiles)
    target_slots = [
        {int(movie): slot for slot, movie in enumerate(values)}
        for values in profiles["target_movie_ids"]
    ]
    full_hist = np.zeros((len(profiles), 10), dtype=np.uint32)
    target_rating_idx = [np.full(len(values), -1, dtype=np.int8) for values in profiles["target_movie_ids"]]
    counters = {
        "raw_rows": 0,
        "evaluation_user_rating_values_parsed": 0,
        "target_rating_values_parsed": 0,
        "timestamps_parsed": 0,
    }
    archive = Path(contract["allowed_input_artifacts"]["movielens_archive"]["path"])
    progress(p, "OPEN_SCREEN_LABELS_AFTER_SCORE_SEAL", users=len(profiles))
    with zipfile.ZipFile(archive) as bundle, bundle.open(_zip_member(bundle, "ratings.csv")) as handle:
        if handle.readline().rstrip(b"\r\n") != b"userId,movieId,rating,timestamp":
            raise RuntimeError("MovieLens ratings header drift")
        for raw in handle:
            counters["raw_rows"] += 1
            uid, movie, second = _parse_user_movie(raw)
            row = raw_to_row.get(uid)
            if row is None:
                continue
            third = raw.find(b",", second + 1)
            if third <= second + 1:
                raise RuntimeError("malformed screen label rating")
            index = rating_index(raw[second + 1 : third])
            full_hist[row, index] += 1
            counters["evaluation_user_rating_values_parsed"] += 1
            slot = target_slots[row].get(movie)
            if slot is not None:
                if target_rating_idx[row][slot] >= 0:
                    raise RuntimeError("duplicate target rating")
                target_rating_idx[row][slot] = index
                counters["target_rating_values_parsed"] += 1
    if any(bool((values < 0).any()) for values in target_rating_idx):
        raise RuntimeError("missing target label after screen score seal")
    if bool((full_hist.sum(axis=1) <= 0).any()) or counters["timestamps_parsed"]:
        raise RuntimeError("invalid full-history label reference")

    label_rows: list[dict[str, Any]] = []
    for row, profile in enumerate(profiles.itertuples(index=False)):
        q_eval = full_history_q(target_rating_idx[row], full_hist[row])
        label_rows.append({
            "user_key": str(profile.user_key),
            "target_movie_ids": list(map(int, profile.target_movie_ids)),
            "target_q_eval": q_eval.astype(np.float64).tolist(),
        })
    labels = pd.DataFrame(label_rows).sort_values("user_key", kind="stable", ignore_index=True)
    atomic_parquet(p["labels"], labels)
    integrity = {
        "status": "OPENED_SCREEN_LABELS_ONLY_AFTER_ALL_SCREEN_SCORES_AND_RANKS_SEALED",
        "evaluation_labels": artifact(p["labels"]),
        "score_rank_seal": artifact(p["score_integrity"]),
        "users": len(labels),
        "target_labels": int(sum(map(len, labels["target_q_eval"]))),
        "reader": counters,
        "timestamps_opened": False,
        "replication_or_transfer_rating_values_opened": False,
        "locked_test_opened": False,
        "final_reserve_opened": False,
    }
    atomic_json(p["labels_integrity"], integrity)
    progress(p, "SCREEN_LABELS_COMPLETE", users=len(labels), target_labels=integrity["target_labels"])
    return labels, integrity


def materialize_screen_metrics(
    p: dict[str, Path], scores: pd.DataFrame, labels: pd.DataFrame, resume: bool
) -> pd.DataFrame:
    if p["metrics"].exists() != p["metrics_integrity"].exists():
        raise ResumeError("partial screen metric state")
    if p["metrics"].exists():
        if not resume:
            raise ResumeError("screen metrics require --resume")
        integrity = json.loads(p["metrics_integrity"].read_text(encoding="utf-8"))
        verify_artifact(integrity["user_metrics"])
        return pd.read_parquet(p["metrics"])

    q_by_user = {
        str(row.user_key): np.asarray(row.target_q_eval, dtype=np.float64)
        for row in labels.itertuples(index=False)
    }
    rows: list[dict[str, Any]] = []
    for (key, encoding), group in scores.groupby(["user_key", "encoding"], sort=True, observed=True):
        key = str(key)
        q_eval = q_by_user[key]
        random_metrics = analytic_random_top2(q_eval)
        rows.append({
            "user_key": key,
            "encoding": str(encoding),
            "model": "RANDOM_EXPECTATION",
            **random_metrics,
            "PAIRWISE_CONCORDANCE": np.nan,
            "active": True,
            "used_random_expectation": True,
        })
        if set(group["model"]) != set(ALL_MODELS):
            raise RuntimeError("screen score model Cartesian drift")
        for rank_row in group.itertuples(index=False):
            active = bool(rank_row.active)
            if active:
                order = np.asarray(rank_row.ranked_target_indices, dtype=np.int64)
                values = np.asarray(rank_row.scores, dtype=np.float64)
                if sorted(order.tolist()) != list(range(len(q_eval))) or values.shape != q_eval.shape:
                    raise RuntimeError("active screen score/rank shape drift")
                primary = ranked_top2_metrics(order, q_eval)
                concordance = pairwise_concordance(values, q_eval)
            else:
                if len(rank_row.ranked_target_indices) or len(rank_row.scores):
                    raise RuntimeError("inactive screen score row contains values")
                primary = random_metrics
                concordance = np.nan
            rows.append({
                "user_key": key,
                "encoding": str(encoding),
                "model": str(rank_row.model),
                **primary,
                "PAIRWISE_CONCORDANCE": concordance,
                "active": active,
                "used_random_expectation": not active,
            })
    metrics = pd.DataFrame(rows).sort_values(
        ["encoding", "model", "user_key"], kind="stable", ignore_index=True
    )
    expected = len(labels) * len(ENCODINGS) * (len(ALL_MODELS) + 1)
    if len(metrics) != expected or metrics.duplicated(["user_key", "encoding", "model"]).any():
        raise RuntimeError("screen metric row-set drift")
    primary_columns = ["HARM20", "TOP2_MEAN_Q", "TOP2_MIN_Q", "GOOD80"]
    if not np.isfinite(metrics[primary_columns].to_numpy(dtype=np.float64)).all():
        raise RuntimeError("nonfinite screen primary or secondary top-2 metric")
    atomic_parquet(p["metrics"], metrics)
    atomic_json(p["metrics_integrity"], {
        "status": "SEALED_SCREEN_USER_METRICS",
        "user_metrics": artifact(p["metrics"]),
        "users": len(labels),
        "rows": len(metrics),
        "inactive_cells_use_analytic_random_expectation": True,
    })
    progress(p, "SCREEN_METRICS_COMPLETE", rows=len(metrics))
    return metrics


def _screen_contrasts(metrics: pd.DataFrame, contract: dict[str, Any]) -> tuple[list[dict[str, Any]], np.ndarray]:
    primary = metrics.loc[metrics["encoding"].eq("PERCENTILE_MAGNITUDE")].copy()
    users = sorted(primary["user_key"].unique().tolist())
    indexed = primary.set_index(["user_key", "model"])
    primary_metrics = list(contract["metrics"]["primary"])
    descriptions: list[dict[str, Any]] = []
    columns: list[np.ndarray] = []

    def series(model: str, metric: str) -> np.ndarray:
        values = indexed.loc[(slice(None), model), metric].droplevel("model").reindex(users)
        result = values.to_numpy(dtype=np.float64)
        if not np.isfinite(result).all():
            raise RuntimeError(f"missing screen metric: {model} {metric}")
        return result

    for metric in primary_metrics:
        random_values = series("RANDOM_EXPECTATION", metric)
        for model in ALL_MODELS:
            model_values = series(model, metric)
            benefit = random_values - model_values if metric == "HARM20" else model_values - random_values
            descriptions.append({"model": model, "comparator": "RANDOM_EXPECTATION", "metric": metric})
            columns.append(benefit)
        for learned in LEARNED_MODELS:
            learned_values = series(learned, metric)
            for direct in DIRECT_MODELS:
                direct_values = series(direct, metric)
                benefit = direct_values - learned_values if metric == "HARM20" else learned_values - direct_values
                descriptions.append({"model": learned, "comparator": direct, "metric": metric})
                columns.append(benefit)
    if len(descriptions) != 45:
        raise RuntimeError("screen contrast family is not exactly 45")
    return descriptions, np.column_stack(columns)


def analyze_and_select(
    contract: dict[str, Any], p: dict[str, Path], metrics: pd.DataFrame, resume: bool
) -> dict[str, Any]:
    if p["analysis_integrity"].exists():
        if not resume:
            raise ResumeError("screen analysis requires --resume")
        integrity = json.loads(p["analysis_integrity"].read_text(encoding="utf-8"))
        verify_artifact(integrity["intervals"])
        verify_artifact(integrity["selection"])
        return json.loads(p["selection"].read_text(encoding="utf-8"))

    descriptions, values = _screen_contrasts(metrics, contract)
    inference = simultaneous_max_t(
        values,
        repeats=int(contract["statistics"]["screen_repeats"]),
        seed=int(contract["statistics"]["screen_seed"]),
    )
    intervals: list[dict[str, Any]] = []
    for index, description in enumerate(descriptions):
        intervals.append({
            **description,
            "point_benefit": float(inference["point"][index]),
            "se": float(inference["se"][index]),
            "simultaneous_low": float(inference["low"][index]),
            "simultaneous_high": float(inference["high"][index]),
        })
    aggregate_rows: list[dict[str, Any]] = []
    for (encoding, model), group in metrics.groupby(["encoding", "model"], sort=True, observed=True):
        active = group["active"].to_numpy(dtype=bool)
        concordance = group.loc[active, "PAIRWISE_CONCORDANCE"].to_numpy(dtype=np.float64)
        aggregate_rows.append({
            "encoding": str(encoding),
            "model": str(model),
            "users": len(group),
            "active_rate": float(active.mean()),
            "harm20": float(group["HARM20"].mean()),
            "top2_mean_q": float(group["TOP2_MEAN_Q"].mean()),
            "top2_min_q": float(group["TOP2_MIN_Q"].mean()),
            "good80": float(group["GOOD80"].mean()),
            "active_pairwise_concordance": (
                float(np.nanmean(concordance)) if len(concordance) else None
            ),
        })
    interval_document = {
        "status": "COMPLETE_45_CONTRAST_SIMULTANEOUS_MAX_T",
        "encoding": "PERCENTILE_MAGNITUDE",
        "users": int(values.shape[0]),
        "contrasts": int(values.shape[1]),
        "bootstrap_repeats": int(contract["statistics"]["screen_repeats"]),
        "critical": float(inference["critical"]),
        "intervals": intervals,
        "aggregate_metrics": aggregate_rows,
        "binary_sign_is_descriptive_sensitivity_only": True,
    }
    atomic_json(p["intervals"], interval_document)

    lookup = {
        (row["model"], row["comparator"], row["metric"]): row
        for row in intervals
    }
    percentile = {
        row["model"]: row
        for row in aggregate_rows
        if row["encoding"] == "PERCENTILE_MAGNITUDE"
    }
    gate = float(contract["statistics"]["active_rate_gate"])

    def passes_random(model: str) -> bool:
        return (
            percentile[model]["active_rate"] >= gate
            and lookup[(model, "RANDOM_EXPECTATION", "HARM20")]["simultaneous_low"] >= 0
            and lookup[(model, "RANDOM_EXPECTATION", "TOP2_MEAN_Q")]["simultaneous_low"] > 0
            and lookup[(model, "RANDOM_EXPECTATION", "TOP2_MIN_Q")]["simultaneous_low"] > 0
        )

    direct_eligible = [model for model in DIRECT_MODELS if percentile[model]["active_rate"] >= gate]
    direct_pass = [model for model in direct_eligible if passes_random(model)]
    reporting_order = list(contract["screen_and_replication"]["reporting_order"])
    order_index = {name: index for index, name in enumerate(reporting_order)}

    def direct_key(model: str) -> tuple[float, float, int]:
        return (
            -lookup[(model, "RANDOM_EXPECTATION", "HARM20")]["simultaneous_low"],
            -lookup[(model, "RANDOM_EXPECTATION", "TOP2_MEAN_Q")]["simultaneous_low"],
            order_index[model],
        )

    if direct_pass:
        direct_reference = sorted(direct_pass, key=direct_key)[0]
        direct_status = "PASSED_RANDOM_GATE"
    elif direct_eligible:
        direct_reference = sorted(
            direct_eligible,
            key=lambda model: (
                -lookup[(model, "RANDOM_EXPECTATION", "TOP2_MEAN_Q")]["simultaneous_low"],
                -lookup[(model, "RANDOM_EXPECTATION", "HARM20")]["simultaneous_low"],
                order_index[model],
            ),
        )[0]
        direct_status = "FAILED_GATE_DESCRIPTIVE_REFERENCE"
    else:
        direct_reference = None
        direct_status = "NO_VALID_DIRECT_REFERENCE"

    learned_eligible = [model for model in LEARNED_MODELS if passes_random(model)]
    nondominated: list[str] = []
    for model in learned_eligible:
        harm = lookup[(model, "RANDOM_EXPECTATION", "HARM20")]["simultaneous_low"]
        mean = lookup[(model, "RANDOM_EXPECTATION", "TOP2_MEAN_Q")]["simultaneous_low"]
        dominated = False
        for other in learned_eligible:
            if other == model:
                continue
            other_harm = lookup[(other, "RANDOM_EXPECTATION", "HARM20")]["simultaneous_low"]
            other_mean = lookup[(other, "RANDOM_EXPECTATION", "TOP2_MEAN_Q")]["simultaneous_low"]
            if other_harm >= harm and other_mean >= mean and (other_harm > harm or other_mean > mean):
                dominated = True
                break
        if not dominated:
            nondominated.append(model)
    advanced = sorted(nondominated, key=direct_key)[: int(contract["screen_and_replication"]["advance_cap"])]
    if direct_reference is None:
        advanced = []
        outcome = "STOP_NO_VALID_DIRECT_REFERENCE"
    elif not advanced:
        outcome = "STOP_NO_LEARNED_CONTENT_MODEL_SIGNAL"
    else:
        outcome = "ADVANCE_TO_FROZEN_REPLICATION_AND_PROXY_TRANSFER"
    selection = {
        "status": outcome,
        "primary_encoding": "PERCENTILE_MAGNITUDE",
        "k": int(contract["profile_policy"]["k"]),
        "output_n": int(contract["evaluation_labels"]["output_n"]),
        "direct_reference": direct_reference,
        "direct_reference_status": direct_status,
        "advanced_learned_models": advanced,
        "learned_random_gate_pass": learned_eligible,
        "learned_nondominated": nondominated,
        "active_rate_gate": gate,
        "replication_and_transfer_authorized": bool(direct_reference is not None and advanced),
        "selection_uses_binary_sensitivity": False,
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "product_policy_changed": False,
    }
    atomic_json(p["selection"], selection)
    atomic_json(p["analysis_integrity"], {
        "status": "SEALED_SCREEN_ANALYSIS_AND_SELECTION",
        "intervals": artifact(p["intervals"]),
        "selection": artifact(p["selection"]),
        "user_metrics": artifact(p["metrics"]),
    })
    progress(
        p, "SCREEN_ANALYSIS_COMPLETE", status=outcome,
        direct_reference=direct_reference, advanced_learned_models=advanced,
    )
    return selection


def label_and_analyze(
    contract: dict[str, Any], p: dict[str, Path], resume: bool
) -> dict[str, Any]:
    score_integrity = json.loads(p["score_integrity"].read_text(encoding="utf-8"))
    verify_artifact(score_integrity["score_ranks"])
    profiles = pd.read_parquet(p["profiles"]).sort_values("user_key", kind="stable", ignore_index=True)
    labels, _ = open_screen_labels(contract, p, profiles, resume)
    scores = pd.read_parquet(p["scores"])
    metrics = materialize_screen_metrics(p, scores, labels, resume)
    return analyze_and_select(contract, p, metrics, resume)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--phase", choices=("prepare", "fit", "score", "label", "all"), default="all")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    contract_path = args.contract.resolve()
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    output_root = (
        args.output_root.resolve()
        if args.output_root is not None
        else (ROOT / contract["output_root"]).resolve()
    )
    p = paths(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    create_or_verify_lock(contract_path, contract, p, args.resume)
    phases = ("prepare", "fit", "score", "label") if args.phase == "all" else (args.phase,)
    result: dict[str, Any] = {"status": "NO_PHASE_RUN"}
    for phase in phases:
        if phase == "prepare":
            result = prepare(contract, p, args.resume)
        elif phase == "fit":
            result = fit(contract, p, output_root, args.resume)
        elif phase == "score":
            result = score(contract, p, output_root, args.resume)
        elif phase == "label":
            result = label_and_analyze(contract, p, args.resume)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
