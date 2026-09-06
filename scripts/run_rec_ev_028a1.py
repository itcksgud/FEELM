"""Run REC-EV-028A1 through the global pre-label score seal.

This entry point deliberately stops before target labels.  A separate audited
continuation opens labels only after every outer/system/cell score is sealed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Mapping, Sequence
import zipfile

import numpy as np
import pandas as pd
from scipy import sparse

from build_rec_ev_019b_features import _zip_member, sha256_file
from preflight_rec_ev_027_membership import fold_of
from preflight_rec_ev_028a_membership import MAX_USER_ID, OUTERS, phase_bucket
from rec_ev_022a_core import old_user_bucket, user_key, user_role_bucket
from rec_ev_027_core import (
    deterministic_order,
    profile_weights,
    rating_index,
    smoothed_q,
    user_equal_prior,
    weighted_similarity_scores,
)
from run_rec_ev_027_screen import common_features
from validate_rec_ev_028a1_amendment import DEFAULT as AMENDMENT_DEFAULT
from validate_rec_ev_028a1_amendment import ROOT, validate as validate_amendment


CELLS = (
    ("PERCENTILE_MAGNITUDE_K8", "PERCENTILE_MAGNITUDE", 8),
    ("BINARY_SIGN_K8", "BINARY_SIGN", 8),
    ("PERCENTILE_MAGNITUDE_K4", "PERCENTILE_MAGNITUDE", 4),
    ("PERCENTILE_MAGNITUDE_K12", "PERCENTILE_MAGNITUDE", 12),
)
SYSTEMS = (
    "FULL_PERSONALIZED",
    "BIAS_ONLY",
    "DOT_ONLY",
    "PROFILE_SHUFFLE",
    "STRUCTURED_DIRECT",
)


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
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        sparse.save_npz(handle, value, compressed=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def artifact(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "path": resolved.relative_to(ROOT).as_posix(),
        "bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def verify_artifact(spec: Mapping[str, Any]) -> Path:
    path = ROOT / str(spec["path"])
    if path.stat().st_size != int(spec["bytes"]) or sha256_file(path) != spec["sha256"]:
        raise RuntimeError(f"artifact drift: {spec['path']}")
    return path


def progress(global_paths: Mapping[str, Path], phase: str, **extra: Any) -> None:
    value = {"phase": phase, "updated_at_unix": time.time(), **extra}
    atomic_json(global_paths["progress"], value)
    print(json.dumps(value, ensure_ascii=False), flush=True)


def outer_paths(output_root: Path, slug: str) -> dict[str, Path]:
    base = output_root / f"attribution/outers/{slug}"
    prepared = base / "prepared"
    return {
        "base": base,
        "prior": prepared / "fit-only-prior.npz",
        "interactions": prepared / "fit-only-signed-interactions.npz",
        "features": prepared / "structured-item-features.npz",
        "feature_rows": prepared / "feature-row-active.npy",
        "profiles": prepared / "evaluation-profiles.parquet",
        "prepared_integrity": prepared / "integrity.json",
        "fit_integrity": base / "fits/integrity.json",
        "scores": base / "scores/score-ranks.parquet",
        "score_integrity": base / "scores/integrity.json",
    }


def global_paths(output_root: Path) -> dict[str, Path]:
    base = output_root / "attribution"
    return {
        "base": base,
        "lock": base / "protocol-lock.json",
        "progress": base / "progress.json",
        "prepared": base / "prepared.integrity.json",
        "fits": base / "fits.integrity.json",
        "scores": base / "global-prelabel-score-seal.json",
    }


def load_contracts(amendment_path: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    amendment = json.loads(amendment_path.read_text(encoding="utf-8"))
    validate_amendment(amendment, amendment_path.resolve())
    base_path = ROOT / amendment["base_contract"]["path"]
    base = json.loads(base_path.read_text(encoding="utf-8"))
    parent_path = ROOT / base["inputs"]["parent_contract"]["path"]
    parent = json.loads(parent_path.read_text(encoding="utf-8"))
    return amendment, base, parent


def create_or_verify_lock(
    amendment_path: Path,
    amendment: dict[str, Any],
    base: dict[str, Any],
    parent: dict[str, Any],
    output_root: Path,
    g: Mapping[str, Path],
    resume: bool,
) -> None:
    membership_summary_path = output_root / "membership-preflight.json"
    membership_summary = json.loads(membership_summary_path.read_text(encoding="utf-8"))
    if (
        membership_summary["status"] != "BALANCED_MEMBERSHIP_PREFLIGHT_PASS"
        or membership_summary["all_outer_coverage_gates_pass"] is not True
        or membership_summary["rating_values_parsed"] is not False
        or membership_summary["future_membership_materialized"] is not False
    ):
        raise RuntimeError("balanced membership gate not passed")
    membership = verify_artifact(membership_summary["membership"])
    implementations = [
        Path(__file__).resolve(),
        ROOT / "scripts/preflight_rec_ev_028a1_membership.py",
        ROOT / "scripts/preflight_rec_ev_028a_membership.py",
        ROOT / "scripts/preflight_rec_ev_027_membership.py",
        ROOT / "scripts/rec_ev_022a_core.py",
        ROOT / "scripts/rec_ev_027_core.py",
        ROOT / "scripts/run_rec_ev_027_screen.py",
        ROOT / "scripts/train_rec_ev_027_lightfm.py",
    ]
    expected = {
        "schema_version": 1,
        "evidence_id": "REC-EV-028A1-PERSONALIZATION-ATTRIBUTION",
        "status": "LOCKED_BEFORE_ANY_REC_EV_028_RATING_VALUE_ACCESS",
        "amendment": artifact(amendment_path),
        "base_contract": artifact(ROOT / amendment["base_contract"]["path"]),
        "parent_contract": artifact(ROOT / base["inputs"]["parent_contract"]["path"]),
        "membership_preflight": artifact(membership_summary_path),
        "membership": artifact(membership),
        "implementations": [artifact(path) for path in implementations],
        "systems": list(SYSTEMS),
        "cells": [cell for cell, _, _ in CELLS],
        "fit_seeds": list(map(int, base["fit"]["seeds"])),
        "rating_values_opened_at_lock": False,
        "timestamps_opened_at_lock": False,
        "future_membership_opened_at_lock": False,
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "product_policy_changed": False,
    }
    if g["lock"].exists():
        if not resume or json.loads(g["lock"].read_text(encoding="utf-8")) != expected:
            raise ResumeError("REC-EV-028A1 protocol lock drift or --resume missing")
    else:
        atomic_json(g["lock"], expected)


def lookup_array(item_ids: np.ndarray) -> np.ndarray:
    result = np.full(int(item_ids.max()) + 1, -1, dtype=np.int32)
    result[item_ids] = np.arange(len(item_ids), dtype=np.int32)
    return result


def role_arrays() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    old = np.fromiter(
        (old_user_bucket(uid) <= 59 for uid in range(MAX_USER_ID + 1)),
        dtype=bool,
        count=MAX_USER_ID + 1,
    )
    roles = np.fromiter(
        (user_role_bucket(uid) for uid in range(MAX_USER_ID + 1)),
        dtype=np.uint16,
        count=MAX_USER_ID + 1,
    )
    phases = np.fromiter(
        (phase_bucket(uid) for uid in range(MAX_USER_ID + 1)),
        dtype=np.uint16,
        count=MAX_USER_ID + 1,
    )
    return old, roles, phases


def membership_for(output_root: Path, slug: str) -> pd.DataFrame:
    frame = pd.read_parquet(output_root / "cache/membership.parquet")
    result = frame.loc[frame["outer"].eq(slug)].sort_values("user_key", kind="stable", ignore_index=True)
    if result.empty or result["user_key"].duplicated().any():
        raise RuntimeError(f"membership drift: {slug}")
    return result


def reverse_user_keys(keys: Sequence[str]) -> dict[int, int]:
    wanted = {str(key): index for index, key in enumerate(keys)}
    result: dict[int, int] = {}
    for uid in range(1, MAX_USER_ID + 1):
        row = wanted.get(user_key(uid))
        if row is not None:
            result[uid] = row
    if len(result) != len(wanted):
        raise RuntimeError("evaluation user reversal coverage drift")
    return result


def outer_masks(parent: dict[str, Any], item_ids: np.ndarray, slug: str) -> tuple[np.ndarray, np.ndarray]:
    if slug in {"R0", "R1", "R2", "R3", "R4"}:
        fold = int(slug[1:])
        salt = parent["strict_item_firewall"]["fold_salt"]
        cold = np.asarray([fold_of(int(movie), salt) == fold for movie in item_ids], dtype=bool)
        return ~cold, cold
    outputs = parent["catalog_content_build"]["outputs"]
    if slug == "KR":
        domain = pd.read_parquet(ROOT / outputs["domain_projection"]).set_index("movie_id")
        cold = domain.reindex(item_ids)["tmdb_korean_origin_proxy"].fillna(False).to_numpy(dtype=bool)
        return ~cold, cold
    if slug == "RECENT":
        structured = pd.read_parquet(ROOT / outputs["structured"]).set_index("movie_id")
        years = structured.reindex(item_ids)["release_year"].to_numpy(dtype=np.float64)
        cold = np.isfinite(years) & (years >= 2020) & (years <= 2023)
        warm = np.isfinite(years) & (years <= 2019)
        return warm, cold
    raise ValueError(slug)


def _rating_field(raw: bytes, second_comma: int) -> int:
    third = raw.find(b",", second_comma + 1)
    if third <= second_comma + 1:
        raise RuntimeError("malformed rating row")
    return rating_index(raw[second_comma + 1 : third])


def prepare_outer(
    base: dict[str, Any],
    parent: dict[str, Any],
    output_root: Path,
    g: Mapping[str, Path],
    item_ids: np.ndarray,
    structured: sparse.csr_matrix,
    slug: str,
    resume: bool,
) -> dict[str, Any]:
    p = outer_paths(output_root, slug)
    artifacts = {name: p[name] for name in ("prior", "interactions", "features", "feature_rows", "profiles")}
    if p["prepared_integrity"].exists():
        if not resume:
            raise ResumeError(f"{slug} prepared state requires --resume")
        integrity = json.loads(p["prepared_integrity"].read_text(encoding="utf-8"))
        for spec in integrity["artifacts"].values():
            verify_artifact(spec)
        return integrity["metadata"]
    if any(path.exists() for path in artifacts.values()):
        raise ResumeError(f"partial prepared state: {slug}")

    warm, cold = outer_masks(parent, item_ids, slug)
    if bool((warm & cold).any()) or not bool(warm.any()) or not bool(cold.any()):
        raise RuntimeError(f"invalid warm/cold partition: {slug}")
    members = membership_for(output_root, slug)
    raw_to_eval = reverse_user_keys(members["user_key"].astype(str).tolist())
    profile_maps = [
        {int(movie): index for index, movie in enumerate(values)} for values in members["profile_movie_ids"]
    ]
    target_sets = [set(map(int, values)) for values in members["target_movie_ids"]]
    profile_rating_idx = np.full((len(members), 12), -1, dtype=np.int8)
    movie_lookup = lookup_array(item_ids)
    train_hist_raw = np.zeros((MAX_USER_ID + 1, 10), dtype=np.uint32)
    old, roles, phases = role_arrays()
    counters = {
        "raw_rows": 0,
        "excluded_rows_discarded_after_user_id": 0,
        "future_rows_discarded_after_user_id": 0,
        "fit_warm_rating_values_parsed": 0,
        "evaluation_profile_rating_values_parsed": 0,
        "evaluation_target_rows_seen_without_rating_parse": 0,
        "evaluation_target_rating_values_parsed": 0,
        "timestamps_parsed": 0,
    }
    archive = Path(parent["allowed_input_artifacts"]["movielens_archive"]["path"])
    progress(g, "PREPARE_OUTER_PASS1", outer=slug, users=len(members), cold_items=int(cold.sum()))
    with zipfile.ZipFile(archive) as bundle, bundle.open(_zip_member(bundle, "ratings.csv")) as handle:
        if handle.readline().rstrip(b"\r\n") != b"userId,movieId,rating,timestamp":
            raise RuntimeError("MovieLens header drift")
        for raw in handle:
            counters["raw_rows"] += 1
            first = raw.find(b",")
            if first <= 0:
                raise RuntimeError("malformed row before user firewall")
            uid = int(raw[:first])
            if uid > MAX_USER_ID or not bool(old[uid]) or int(roles[uid]) > 5999:
                counters["excluded_rows_discarded_after_user_id"] += 1
                continue
            phase = int(phases[uid])
            if phase >= 9000:
                counters["future_rows_discarded_after_user_id"] += 1
                continue
            fit_user = phase <= 7999
            eval_row = raw_to_eval.get(uid) if 8000 <= phase <= 8999 else None
            if not fit_user and eval_row is None:
                continue
            second = raw.find(b",", first + 1)
            if second <= first + 1:
                raise RuntimeError("malformed movie field")
            movie = int(raw[first + 1 : second])
            position = int(movie_lookup[movie]) if 0 <= movie < len(movie_lookup) else -1
            parse_fit = fit_user and position >= 0 and bool(warm[position])
            profile_slot = profile_maps[eval_row].get(movie) if eval_row is not None else None
            if eval_row is not None and movie in target_sets[eval_row]:
                counters["evaluation_target_rows_seen_without_rating_parse"] += 1
            if not parse_fit and profile_slot is None:
                continue
            index = _rating_field(raw, second)
            if parse_fit:
                train_hist_raw[uid, index] += 1
                counters["fit_warm_rating_values_parsed"] += 1
            if profile_slot is not None:
                if profile_rating_idx[eval_row, profile_slot] >= 0:
                    raise RuntimeError("duplicate evaluation profile rating")
                profile_rating_idx[eval_row, profile_slot] = index
                counters["evaluation_profile_rating_values_parsed"] += 1
    if bool((profile_rating_idx < 0).any()):
        raise RuntimeError(f"missing profile rating: {slug}")
    if counters["evaluation_target_rating_values_parsed"] or counters["timestamps_parsed"]:
        raise RuntimeError(f"pre-label firewall failure: {slug}")

    active_uids = np.flatnonzero(train_hist_raw.sum(axis=1) > 0)
    keyed = sorted((user_key(int(uid)), int(uid)) for uid in active_uids.tolist())
    train_hist = np.vstack([train_hist_raw[uid] for _, uid in keyed]).astype(np.uint32)
    uid_to_row = np.full(MAX_USER_ID + 1, -1, dtype=np.int32)
    for row, (_, uid) in enumerate(keyed):
        uid_to_row[uid] = row
    _, prior = user_equal_prior(train_hist)
    signs = np.sign(2.0 * np.vstack([smoothed_q(range(10), hist, prior) for hist in train_hist]) - 1.0).astype(np.int8)
    signed_count = int(np.sum(train_hist * (signs != 0)))
    signed_user = np.empty(signed_count, dtype=np.int32)
    signed_item = np.empty(signed_count, dtype=np.int32)
    signed_value = np.empty(signed_count, dtype=np.int8)
    cursor = 0
    progress(g, "PREPARE_OUTER_PASS2", outer=slug, fit_users=len(keyed), interactions=signed_count)
    with zipfile.ZipFile(archive) as bundle, bundle.open(_zip_member(bundle, "ratings.csv")) as handle:
        handle.readline()
        for raw in handle:
            first = raw.find(b",")
            uid = int(raw[:first])
            if uid > MAX_USER_ID or uid_to_row[uid] < 0:
                continue
            second = raw.find(b",", first + 1)
            movie = int(raw[first + 1 : second])
            position = int(movie_lookup[movie]) if 0 <= movie < len(movie_lookup) else -1
            if position < 0 or not bool(warm[position]):
                continue
            index = _rating_field(raw, second)
            train_row = int(uid_to_row[uid])
            sign = int(signs[train_row, index])
            if sign:
                signed_user[cursor], signed_item[cursor], signed_value[cursor] = train_row, position, sign
                cursor += 1
    if cursor != signed_count:
        raise RuntimeError(f"signed interaction allocation drift: {slug}")
    order = np.lexsort((signed_item, signed_user))
    interactions = sparse.csr_matrix(
        (signed_value[order], (signed_user[order], signed_item[order])),
        shape=(len(keyed), len(item_ids)),
        dtype=np.int8,
    )
    interactions.sort_indices()
    if bool(np.isin(interactions.indices, np.flatnonzero(cold)).any()):
        raise RuntimeError(f"cold item leaked into fit: {slug}")
    touched = np.unique(interactions.indices)
    feature_mask = np.asarray(structured[touched].getnnz(axis=0)).ravel() > 0
    features = structured[:, feature_mask].tocsr().astype(np.float32)
    norms = np.sqrt(np.asarray(features.multiply(features).sum(axis=1)).ravel())
    active_rows = norms > 0
    features = sparse.diags(
        np.divide(1.0, norms, out=np.zeros_like(norms), where=active_rows).astype(np.float32)
    ) @ features
    features = features.tocsr().astype(np.float32)
    profiles = members[["user_key", "donor_user_key", "profile_movie_ids", "target_movie_ids"]].copy()
    profiles["profile_rating_idx"] = [values.tolist() for values in profile_rating_idx]
    atomic_npz(p["prior"], g0_mid=prior)
    atomic_sparse(p["interactions"], interactions)
    atomic_sparse(p["features"], features)
    atomic_npy(p["feature_rows"], active_rows)
    atomic_parquet(p["profiles"], profiles)
    metadata = {
        "outer": slug,
        "fit_users": len(keyed),
        "evaluation_users": len(profiles),
        "warm_items": int(warm.sum()),
        "cold_items": int(cold.sum()),
        "signed_interactions": interactions.nnz,
        "positive": int(np.count_nonzero(interactions.data == 1)),
        "negative": int(np.count_nonzero(interactions.data == -1)),
        "feature_columns": features.shape[1],
        "active_cold_rows": int((active_rows & cold).sum()),
        "reader": counters,
        "target_rating_values_opened": False,
        "timestamps_opened": False,
        "future_movie_membership_opened": False,
    }
    atomic_json(
        p["prepared_integrity"],
        {
            "status": "SEALED_FIT_AND_SELECTED_PROFILES_WITHOUT_TARGET_LABELS",
            "artifacts": {name: artifact(path) for name, path in artifacts.items()},
            "metadata": metadata,
        },
    )
    progress(g, "PREPARED_OUTER", **metadata)
    return metadata


def prepare_all(
    base: dict[str, Any], parent: dict[str, Any], output_root: Path, g: Mapping[str, Path], resume: bool
) -> dict[str, Any]:
    if g["prepared"].exists():
        if not resume:
            raise ResumeError("prepared state requires --resume")
        value = json.loads(g["prepared"].read_text(encoding="utf-8"))
        for spec in value["outer_integrities"]:
            verify_artifact(spec)
        return value
    item_ids, structured, _ = common_features(parent)
    summaries = [
        prepare_outer(base, parent, output_root, g, item_ids, structured, slug, resume)
        for slug, _, _ in OUTERS
    ]
    value = {
        "status": "SEALED_ALL_FIT_AND_PROFILES_WITHOUT_TARGET_LABELS",
        "outer_integrities": [artifact(outer_paths(output_root, slug)["prepared_integrity"]) for slug, _, _ in OUTERS],
        "summaries": summaries,
        "target_rating_values_opened": False,
        "timestamps_opened": False,
        "future_movie_membership_opened": False,
    }
    atomic_json(g["prepared"], value)
    return value


def lightfm_job(
    base: dict[str, Any], parent: dict[str, Any], output_root: Path, slug: str, seed: int, resume: bool
) -> tuple[dict[str, Path], dict[str, Any]] | None:
    p = outer_paths(output_root, slug)
    directory = p["base"] / f"fits/lightfm/S{seed}"
    members = {"config": directory / "config.json", "result": directory / "result.npz", "integrity": directory / "integrity.json"}
    parameters = parent["models"]["FEATURE_ONLY_LIGHTFM"]["parameters"]
    config = {
        "seed": seed,
        "dimension": int(parameters["dimension"]),
        "epochs": int(parameters["epochs"]),
        "learning_rate": float(parameters["learning_rate"]),
        "item_alpha": float(parameters["item_alpha"]),
        "user_alpha": float(parameters["user_alpha"]),
        "interaction_sha256": sha256_file(p["interactions"]),
        "item_feature_sha256": sha256_file(p["features"]),
        "item_identity_features": False,
    }
    if members["integrity"].exists():
        if not resume or not all(path.is_file() for path in members.values()):
            raise ResumeError(f"partial sealed fit: {slug} S{seed}")
        if json.loads(members["config"].read_text(encoding="utf-8")) != config:
            raise ResumeError(f"fit config drift: {slug} S{seed}")
        integrity = json.loads(members["integrity"].read_text(encoding="utf-8"))
        verify_artifact(integrity["config"])
        verify_artifact(integrity["result"])
        return None
    if members["result"].exists():
        raise ResumeError(f"unsealed fit result: {slug} S{seed}")
    if members["config"].exists():
        if not resume or json.loads(members["config"].read_text(encoding="utf-8")) != config:
            raise ResumeError(f"fit config requires --resume: {slug} S{seed}")
    else:
        directory.mkdir(parents=True, exist_ok=True)
        atomic_json(members["config"], config)
    return members, config


def fit_all(
    base: dict[str, Any],
    parent: dict[str, Any],
    output_root: Path,
    g: Mapping[str, Path],
    resume: bool,
    max_jobs: int,
) -> dict[str, Any]:
    prepared = json.loads(g["prepared"].read_text(encoding="utf-8"))
    for spec in prepared["outer_integrities"]:
        verify_artifact(spec)
    if g["fits"].exists():
        if not resume:
            raise ResumeError("fit state requires --resume")
        value = json.loads(g["fits"].read_text(encoding="utf-8"))
        for spec in value["outer_integrities"]:
            verify_artifact(spec)
        return value
    seeds = list(map(int, base["fit"]["seeds"]))
    pending: list[tuple[str, int, dict[str, Path]]] = []
    for slug, _, _ in OUTERS:
        for seed in seeds:
            job = lightfm_job(base, parent, output_root, slug, seed, resume)
            if job is not None:
                pending.append((slug, seed, job[0]))
    active: list[tuple[str, int, dict[str, Path], subprocess.Popen[Any]]] = []
    runtime = parent["models"]["FEATURE_ONLY_LIGHTFM"]["dependency"]["runtime_image"]
    while pending or active:
        while pending and len(active) < max_jobs:
            slug, seed, members = pending.pop(0)
            p = outer_paths(output_root, slug)
            rel_job = members["config"].parent.relative_to(ROOT).as_posix()
            rel_interactions = p["interactions"].relative_to(ROOT).as_posix()
            rel_features = p["features"].relative_to(ROOT).as_posix()
            command = "\n".join(
                [
                    "python -m pip install --disable-pip-version-check --require-hashes -r requirements-rec-ev-019c.lock",
                    f"python scripts/train_rec_ev_027_lightfm.py --job /workspace/{rel_job} --interactions /workspace/{rel_interactions} --item-features /workspace/{rel_features}",
                ]
            )
            process = subprocess.Popen(
                [
                    "docker", "run", "--rm", "--platform", "linux/amd64",
                    "--mount", f"type=bind,source={ROOT},target=/workspace",
                    "--mount", "type=volume,source=feelm-rec-ev-019c-pip,target=/root/.cache/pip",
                    "--workdir", "/workspace", runtime, "sh", "-ec", command,
                ]
            )
            active.append((slug, seed, members, process))
            progress(g, "FIT_START", outer=slug, seed=seed, active_jobs=len(active))
        completed = None
        for index, (slug, seed, members, process) in enumerate(active):
            returncode = process.poll()
            if returncode is None:
                continue
            completed = index
            if returncode:
                for _, _, _, other in active:
                    if other.poll() is None:
                        other.terminate()
                raise RuntimeError(f"LightFM failed: {slug} S{seed} exit {returncode}")
            fitted = np.load(members["result"], allow_pickle=False)
            if fitted["item_factors"].shape != (85_517, 128) or not np.isfinite(fitted["item_factors"]).all():
                raise RuntimeError(f"fit output drift: {slug} S{seed}")
            atomic_json(
                members["integrity"],
                {"status": "SEALED_LIGHTFM_SEED", "config": artifact(members["config"]), "result": artifact(members["result"]), "outer": slug, "seed": seed},
            )
            progress(g, "FIT_COMPLETE", outer=slug, seed=seed)
            break
        if completed is not None:
            active.pop(completed)
        else:
            time.sleep(1.0)
    outer_integrities = []
    for slug, _, _ in OUTERS:
        p = outer_paths(output_root, slug)
        jobs = []
        for seed in seeds:
            integrity_path = p["base"] / f"fits/lightfm/S{seed}/integrity.json"
            body = json.loads(integrity_path.read_text(encoding="utf-8"))
            verify_artifact(body["config"])
            verify_artifact(body["result"])
            jobs.append(artifact(integrity_path))
        atomic_json(p["fit_integrity"], {"status": "SEALED_LIGHTFM_THREE_SEEDS", "outer": slug, "jobs": jobs})
        outer_integrities.append(artifact(p["fit_integrity"]))
    value = {"status": "SEALED_ALL_LIGHTFM_FITS", "outer_integrities": outer_integrities, "seeds": seeds}
    atomic_json(g["fits"], value)
    return value


def fold_in_user(
    biases: np.ndarray,
    factors: np.ndarray,
    profile_positions: np.ndarray,
    weights: np.ndarray,
    *,
    regularization: float,
) -> tuple[np.ndarray, bool]:
    nonzero = np.abs(weights) > 0
    if not bool(nonzero.any()):
        return np.zeros(factors.shape[1], dtype=np.float64), False
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
    return user, bool(np.isfinite(user).all())


def score_outer(
    base: dict[str, Any],
    parent: dict[str, Any],
    output_root: Path,
    g: Mapping[str, Path],
    item_ids: np.ndarray,
    structured: sparse.csr_matrix,
    slug: str,
    track: str,
    fold: str,
    resume: bool,
) -> dict[str, Any]:
    p = outer_paths(output_root, slug)
    if p["score_integrity"].exists():
        if not resume:
            raise ResumeError(f"score state requires --resume: {slug}")
        value = json.loads(p["score_integrity"].read_text(encoding="utf-8"))
        verify_artifact(value["score_ranks"])
        return value
    if p["scores"].exists():
        raise ResumeError(f"unsealed score file: {slug}")
    fit_integrity = json.loads(p["fit_integrity"].read_text(encoding="utf-8"))
    for spec in fit_integrity["jobs"]:
        verify_artifact(spec)
    profiles = pd.read_parquet(p["profiles"]).sort_values("user_key", kind="stable", ignore_index=True)
    by_key = profiles.set_index("user_key", verify_integrity=True)
    prior = np.load(p["prior"], allow_pickle=False)["g0_mid"].astype(np.float64)
    active_rows = np.load(p["feature_rows"], allow_pickle=False).astype(bool)
    seeds = list(map(int, base["fit"]["seeds"]))
    fits = {}
    for seed in seeds:
        fitted = np.load(p["base"] / f"fits/lightfm/S{seed}/result.npz", allow_pickle=False)
        fits[seed] = (fitted["item_biases"].astype(np.float64), fitted["item_factors"].astype(np.float64))
    movie_lookup = lookup_array(item_ids)
    regularization = float(parent["models"]["FEATURE_ONLY_LIGHTFM"]["parameters"]["user_alpha"])
    rows: list[dict[str, Any]] = []
    progress(g, "SCORE_OUTER", outer=slug, users=len(profiles))
    for number, row in enumerate(profiles.itertuples(index=False), start=1):
        key = str(row.user_key)
        donor = by_key.loc[str(row.donor_user_key)]
        profile_movies = np.asarray(row.profile_movie_ids, dtype=np.int64)
        donor_movies = np.asarray(donor.profile_movie_ids, dtype=np.int64)
        target_movies = np.asarray(row.target_movie_ids, dtype=np.int64)
        profile_positions = movie_lookup[profile_movies]
        donor_positions = movie_lookup[donor_movies]
        target_positions = movie_lookup[target_movies]
        if bool((profile_positions < 0).any()) or bool((donor_positions < 0).any()) or bool((target_positions < 0).any()):
            raise RuntimeError(f"membership outside common support: {slug}")
        profile_indices = np.asarray(row.profile_rating_idx, dtype=np.int8)
        donor_indices = np.asarray(donor.profile_rating_idx, dtype=np.int8)
        similarity = (structured[target_positions] @ structured[profile_positions].T).toarray().astype(np.float64)
        for cell, encoding, k in CELLS:
            own_weights = profile_weights(profile_indices[:k], prior, encoding)
            donor_weights = profile_weights(donor_indices[:k], prior, encoding)
            direct_scores, direct_active = weighted_similarity_scores(similarity[:, :k], own_weights)
            system_scores: dict[str, np.ndarray] = {"STRUCTURED_DIRECT": direct_scores}
            system_active: dict[str, bool] = {"STRUCTURED_DIRECT": direct_active}
            seed_values = {name: [] for name in ("FULL_PERSONALIZED", "BIAS_ONLY", "DOT_ONLY", "PROFILE_SHUFFLE")}
            lightfm_possible = bool(active_rows[target_positions].all() and active_rows[profile_positions[:k]].all())
            shuffle_possible = bool(active_rows[target_positions].all() and active_rows[donor_positions[:k]].all())
            own_ok = lightfm_possible
            donor_ok = shuffle_possible
            for seed in seeds:
                biases, factors = fits[seed]
                own_user, seed_own_ok = fold_in_user(
                    biases, factors, profile_positions[:k], own_weights, regularization=regularization
                )
                donor_user, seed_donor_ok = fold_in_user(
                    biases, factors, donor_positions[:k], donor_weights, regularization=regularization
                )
                own_ok &= seed_own_ok
                donor_ok &= seed_donor_ok
                bias = biases[target_positions]
                dot = factors[target_positions] @ own_user
                shuffled_dot = factors[target_positions] @ donor_user
                seed_values["BIAS_ONLY"].append(bias)
                seed_values["FULL_PERSONALIZED"].append(bias + dot)
                seed_values["DOT_ONLY"].append(dot)
                seed_values["PROFILE_SHUFFLE"].append(bias + shuffled_dot)
            for system in ("FULL_PERSONALIZED", "BIAS_ONLY", "DOT_ONLY", "PROFILE_SHUFFLE"):
                values = np.mean(seed_values[system], axis=0)
                active = bool(np.isfinite(values).all() and np.unique(values).size >= 2)
                if system in {"FULL_PERSONALIZED", "DOT_ONLY"}:
                    active &= own_ok
                if system == "PROFILE_SHUFFLE":
                    active &= donor_ok
                system_scores[system] = values
                system_active[system] = active
            for system in SYSTEMS:
                values = system_scores[system]
                active = bool(system_active[system])
                order = (
                    deterministic_order(
                        target_movies,
                        values,
                        phase="ATTRIBUTION",
                        track=track,
                        fold=fold,
                        model=system,
                        encoding=cell,
                        user_key=key,
                    )
                    if active
                    else np.empty(0, dtype=np.int16)
                )
                rows.append(
                    {
                        "outer": slug,
                        "track": track,
                        "fold_or_domain": fold,
                        "user_key": key,
                        "cell": cell,
                        "encoding": encoding,
                        "k": k,
                        "system": system,
                        "active": active,
                        "target_movie_ids": target_movies.tolist(),
                        "scores": values.tolist() if active else [],
                        "ranked_target_indices": order.tolist(),
                    }
                )
        if number % 500 == 0:
            progress(g, "SCORE_OUTER", outer=slug, users_complete=number, users_total=len(profiles))
    result = pd.DataFrame(rows).sort_values(["cell", "system", "user_key"], kind="stable", ignore_index=True)
    expected = len(profiles) * len(CELLS) * len(SYSTEMS)
    if len(result) != expected or result.duplicated(["user_key", "cell", "system"]).any():
        raise RuntimeError(f"score Cartesian drift: {slug}")
    atomic_parquet(p["scores"], result)
    value = {
        "status": "SEALED_ALL_NONRANDOM_SYSTEMS_AND_CELLS_BEFORE_TARGET_LABELS",
        "outer": slug,
        "score_ranks": artifact(p["scores"]),
        "rows": len(result),
        "users": len(profiles),
        "target_rating_values_opened": False,
        "timestamps_opened": False,
        "future_movie_membership_opened": False,
    }
    atomic_json(p["score_integrity"], value)
    progress(g, "SCORED_OUTER", outer=slug, rows=len(result))
    return value


def score_all(
    base: dict[str, Any], parent: dict[str, Any], output_root: Path, g: Mapping[str, Path], resume: bool
) -> dict[str, Any]:
    if g["scores"].exists():
        if not resume:
            raise ResumeError("global score seal requires --resume")
        value = json.loads(g["scores"].read_text(encoding="utf-8"))
        for spec in value["outer_integrities"]:
            verify_artifact(spec)
        return value
    fits = json.loads(g["fits"].read_text(encoding="utf-8"))
    for spec in fits["outer_integrities"]:
        verify_artifact(spec)
    item_ids, structured, _ = common_features(parent)
    values = [
        score_outer(base, parent, output_root, g, item_ids, structured, slug, track, fold, resume)
        for slug, track, fold in OUTERS
    ]
    value = {
        "status": "GLOBAL_PRELABEL_SCORE_SEAL_COMPLETE",
        "outer_integrities": [artifact(outer_paths(output_root, slug)["score_integrity"]) for slug, _, _ in OUTERS],
        "rows": int(sum(entry["rows"] for entry in values)),
        "systems": list(SYSTEMS),
        "cells": [cell for cell, _, _ in CELLS],
        "target_rating_values_opened": False,
        "timestamps_opened": False,
        "future_movie_membership_opened": False,
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "product_policy_changed": False,
    }
    atomic_json(g["scores"], value)
    progress(g, "GLOBAL_PRELABEL_SCORE_SEAL_COMPLETE", rows=value["rows"])
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--amendment", type=Path, default=AMENDMENT_DEFAULT)
    parser.add_argument("--output-root", type=Path, default=ROOT / "outputs/recommendation-evidence/rec-ev-028a1")
    parser.add_argument("--phase", choices=("prepare", "fit", "score", "all"), default="all")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--max-jobs", type=int, default=3)
    args = parser.parse_args()
    if args.max_jobs < 1:
        raise ValueError("--max-jobs must be positive")
    amendment_path = args.amendment.resolve()
    output_root = args.output_root.resolve()
    amendment, base, parent = load_contracts(amendment_path)
    g = global_paths(output_root)
    create_or_verify_lock(amendment_path, amendment, base, parent, output_root, g, args.resume)
    phases = ("prepare", "fit", "score") if args.phase == "all" else (args.phase,)
    result: Any = None
    for phase in phases:
        if phase == "prepare":
            result = prepare_all(base, parent, output_root, g, args.resume)
        elif phase == "fit":
            result = fit_all(base, parent, output_root, g, args.resume, args.max_jobs)
        elif phase == "score":
            result = score_all(base, parent, output_root, g, args.resume)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
