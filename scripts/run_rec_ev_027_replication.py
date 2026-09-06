"""Run frozen REC-EV-027 replication folds and proxy-transfer tracks."""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence
import zipfile

import numpy as np
import pandas as pd
from scipy import sparse

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from build_rec_ev_019b_features import _zip_member, sha256_file  # noqa: E402
from preflight_rec_ev_027_membership import fold_of  # noqa: E402
from rec_ev_022a_core import pairwise_concordance, user_key  # noqa: E402
from rec_ev_027_core import (  # noqa: E402
    analytic_random_top2,
    deterministic_order,
    full_history_q,
    profile_weights,
    ranked_top2_metrics,
    rating_index,
    smoothed_q,
    user_equal_prior,
    weighted_similarity_scores,
)
from run_rec_ev_027_screen import (  # noqa: E402
    DEFAULT,
    MAX_USER_ID,
    ROOT,
    ResumeError,
    _parse_user_movie,
    artifact,
    atomic_json,
    atomic_npy,
    atomic_npz,
    atomic_parquet,
    atomic_sparse,
    common_features,
    fold_in_lightfm,
    lookup_array,
    role_arrays,
    verify_artifact,
)
from validate_rec_ev_027_contract import validate  # noqa: E402


ENCODINGS = ("PERCENTILE_MAGNITUDE", "BINARY_SIGN")
MODELS = ("STRUCTURED_DIRECT", "FEATURE_ONLY_LIGHTFM")
OUTERS = (
    ("R1", "RANDOM_ITEM_COLD", "1"),
    ("R2", "RANDOM_ITEM_COLD", "2"),
    ("R3", "RANDOM_ITEM_COLD", "3"),
    ("R4", "RANDOM_ITEM_COLD", "4"),
    ("KR", "KOREAN_ORIGIN_COLD", "KR"),
    ("RECENT", "RELEASE_2020_2023_COLD", "2020_2023"),
)


def outer_paths(output_root: Path, slug: str) -> dict[str, Path]:
    base = output_root / "replication" / slug
    return {
        "base": base,
        "prior": base / "prepared/warm-prior.npz",
        "interactions": base / "prepared/train-signed.npz",
        "features": base / "prepared/lightfm-item-features.npz",
        "feature_rows": base / "prepared/lightfm-row-active.npy",
        "profiles": base / "prepared/evaluation-profiles.parquet",
        "prepared_integrity": base / "prepared/integrity.json",
        "fit_integrity": base / "fits/integrity.json",
        "scores": base / "score-ranks.parquet",
        "score_integrity": base / "score-ranks.integrity.json",
        "labels": base / "evaluation-labels.parquet",
        "metrics": base / "user-metrics.parquet",
    }


def global_paths(output_root: Path) -> dict[str, Path]:
    base = output_root / "replication"
    return {
        "lock": base / "protocol-lock.json",
        "progress": base / "progress.json",
        "prepared": base / "prepared.integrity.json",
        "fits": base / "fits.integrity.json",
        "scores": base / "all-score-ranks.integrity.json",
        "labels": base / "all-labels.integrity.json",
        "metrics": base / "all-metrics.integrity.json",
        "replication_analysis": base / "random-folds-analysis.json",
        "transfer_analysis": base / "proxy-transfer-analysis.json",
        "analysis": base / "analysis.integrity.json",
    }


def progress(g: Mapping[str, Path], phase: str, **extra: Any) -> None:
    value = {"phase": phase, "updated_at_unix": time.time(), **extra}
    atomic_json(g["progress"], value)
    print(json.dumps(value, ensure_ascii=False), flush=True)


def create_or_verify_lock(
    contract_path: Path,
    contract: dict[str, Any],
    output_root: Path,
    g: Mapping[str, Path],
    resume: bool,
) -> None:
    validate(contract, verify_files=True)
    selection_path = output_root / "screen/selection.json"
    analysis_integrity_path = output_root / "screen/analysis.integrity.json"
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    if (
        selection["direct_reference"] != "STRUCTURED_DIRECT"
        or selection["advanced_learned_models"] != ["FEATURE_ONLY_LIGHTFM"]
        or not selection["replication_and_transfer_authorized"]
    ):
        raise RuntimeError("frozen screen advance set drift")
    screen_integrity = json.loads(analysis_integrity_path.read_text(encoding="utf-8"))
    verify_artifact(screen_integrity["selection"])
    implementations = [
        ROOT / "scripts/run_rec_ev_027_replication.py",
        ROOT / "scripts/run_rec_ev_027_screen.py",
        ROOT / "scripts/rec_ev_027_core.py",
        ROOT / "scripts/train_rec_ev_027_lightfm.py",
    ]
    screen_prepared = output_root / "screen/prepared/integrity.json"
    expected = {
        "schema_version": 1,
        "evidence_id": "REC-EV-027-REPLICATION-AND-TRANSFER",
        "status": "LOCKED_BEFORE_REPLICATION_OR_TRANSFER_RATING_VALUE_ACCESS",
        "contract": artifact(contract_path),
        "screen_selection": artifact(selection_path),
        "screen_analysis_integrity": artifact(analysis_integrity_path),
        "screen_content_universe": artifact(screen_prepared),
        "membership": artifact(output_root / "cache/membership.parquet"),
        "implementations": [artifact(path) for path in implementations],
        "frozen_models": list(MODELS),
        "frozen_primary_encoding": "PERCENTILE_MAGNITUDE",
        "frozen_k": 8,
        "rating_values_opened_at_lock": False,
        "timestamps_opened_at_lock": False,
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "product_policy_changed": False,
    }
    if g["lock"].exists():
        if not resume or json.loads(g["lock"].read_text(encoding="utf-8")) != expected:
            raise ResumeError("replication protocol lock drift or --resume missing")
    else:
        atomic_json(g["lock"], expected)


def membership_for(output_root: Path, track: str, fold: str) -> pd.DataFrame:
    frame = pd.read_parquet(output_root / "cache/membership.parquet")
    result = frame.loc[
        frame["track"].eq(track) & frame["fold_or_domain"].astype(str).eq(str(fold))
    ].sort_values("user_key", kind="stable", ignore_index=True)
    if result.empty or result["user_key"].duplicated().any():
        raise RuntimeError(f"membership drift: {track} {fold}")
    return result


def outer_masks_for(
    contract: dict[str, Any], item_ids: np.ndarray, track: str, fold: str
) -> tuple[np.ndarray, np.ndarray]:
    if track == "RANDOM_ITEM_COLD":
        salt = contract["strict_item_firewall"]["fold_salt"]
        cold = np.asarray([fold_of(int(movie), salt) == int(fold) for movie in item_ids], dtype=bool)
        return ~cold, cold
    outputs = contract["catalog_content_build"]["outputs"]
    if track == "KOREAN_ORIGIN_COLD":
        domain = pd.read_parquet(ROOT / outputs["domain_projection"]).set_index("movie_id")
        cold = domain.reindex(item_ids)["tmdb_korean_origin_proxy"].fillna(False).to_numpy(dtype=bool)
        return ~cold, cold
    if track == "RELEASE_2020_2023_COLD":
        structured = pd.read_parquet(ROOT / outputs["structured"]).set_index("movie_id")
        years = structured.reindex(item_ids)["release_year"].to_numpy(dtype=np.float64)
        cold = np.isfinite(years) & (years >= 2020) & (years <= 2023)
        warm = np.isfinite(years) & (years <= 2019)
        return warm, cold
    raise ValueError(track)


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


def prepare_outer(
    contract: dict[str, Any],
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

    warm, cold = outer_masks_for(contract, item_ids, track, fold)
    if bool((warm & cold).any()) or not bool(warm.any()) or not bool(cold.any()):
        raise RuntimeError(f"invalid warm/cold partition: {slug}")
    members = membership_for(output_root, track, fold)
    raw_to_eval = reverse_user_keys(members["user_key"].astype(str).tolist())
    profile_maps = [
        {int(movie): index for index, movie in enumerate(values)}
        for values in members["profile_movie_ids"]
    ]
    target_sets = [set(map(int, values)) for values in members["target_movie_ids"]]
    profile_rating_idx = np.full((len(members), 8), -1, dtype=np.int8)
    movie_lookup = lookup_array(item_ids)
    train_hist_raw = np.zeros((MAX_USER_ID + 1, 10), dtype=np.uint32)
    old, roles = role_arrays()
    counters = {
        "raw_rows": 0,
        "train_warm_rating_values_parsed": 0,
        "profile_rating_values_parsed": 0,
        "target_rows_seen_not_parsed": 0,
        "target_rating_values_parsed": 0,
        "timestamps_parsed": 0,
    }
    archive = Path(contract["allowed_input_artifacts"]["movielens_archive"]["path"])
    progress(g, "PREPARE_OUTER_PASS1", outer=slug, users=len(members), cold_items=int(cold.sum()))
    with zipfile.ZipFile(archive) as bundle, bundle.open(_zip_member(bundle, "ratings.csv")) as handle:
        if handle.readline().rstrip(b"\r\n") != b"userId,movieId,rating,timestamp":
            raise RuntimeError("MovieLens ratings header drift")
        for raw in handle:
            counters["raw_rows"] += 1
            uid, movie, second = _parse_user_movie(raw)
            position = int(movie_lookup[movie]) if 0 <= movie < len(movie_lookup) else -1
            train_user = uid <= MAX_USER_ID and bool(old[uid]) and int(roles[uid]) < 6000
            should_parse_train = train_user and position >= 0 and bool(warm[position])
            eval_row = raw_to_eval.get(uid)
            profile_slot = profile_maps[eval_row].get(movie) if eval_row is not None else None
            if eval_row is not None and movie in target_sets[eval_row]:
                counters["target_rows_seen_not_parsed"] += 1
            if not should_parse_train and profile_slot is None:
                continue
            third = raw.find(b",", second + 1)
            if third <= second + 1:
                raise RuntimeError("malformed outer rating")
            index = rating_index(raw[second + 1 : third])
            if should_parse_train:
                train_hist_raw[uid, index] += 1
                counters["train_warm_rating_values_parsed"] += 1
            if profile_slot is not None:
                if profile_rating_idx[eval_row, profile_slot] >= 0:
                    raise RuntimeError("duplicate outer profile rating")
                profile_rating_idx[eval_row, profile_slot] = index
                counters["profile_rating_values_parsed"] += 1
    if bool((profile_rating_idx < 0).any()) or counters["target_rating_values_parsed"] or counters["timestamps_parsed"]:
        raise RuntimeError(f"outer pre-label firewall failure: {slug}")

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
    progress(g, "PREPARE_OUTER_PASS2", outer=slug, train_users=len(keyed), interactions=signed_count)
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
            train_row = int(uid_to_row[uid])
            sign = int(signs[train_row, index])
            if sign:
                signed_user[cursor], signed_item[cursor], signed_value[cursor] = train_row, position, sign
                cursor += 1
    if cursor != signed_count:
        raise RuntimeError(f"outer signed preallocation drift: {slug}")
    order = np.lexsort((signed_item, signed_user))
    signed_user, signed_item, signed_value = signed_user[order], signed_item[order], signed_value[order]
    interactions = sparse.csr_matrix(
        (signed_value, (signed_user, signed_item)), shape=(len(keyed), len(item_ids)), dtype=np.int8
    )
    interactions.sort_indices()
    if bool(np.isin(interactions.indices, np.flatnonzero(cold)).any()):
        raise RuntimeError(f"cold item leaked into interaction matrix: {slug}")
    touched_items = np.unique(interactions.indices)
    feature_mask = np.asarray(structured[touched_items].getnnz(axis=0)).ravel() > 0
    features = structured[:, feature_mask].tocsr().astype(np.float32)
    norms = np.sqrt(np.asarray(features.multiply(features).sum(axis=1)).ravel())
    active_rows = norms > 0
    features = sparse.diags(
        np.divide(1.0, norms, out=np.zeros_like(norms), where=active_rows).astype(np.float32)
    ) @ features
    features = features.tocsr().astype(np.float32)
    profiles = members[["user_key", "profile_movie_ids", "target_movie_ids"]].copy()
    profiles["profile_rating_idx"] = [values.tolist() for values in profile_rating_idx]
    atomic_npz(p["prior"], g0_mid=prior)
    atomic_sparse(p["interactions"], interactions)
    atomic_sparse(p["features"], features)
    atomic_npy(p["feature_rows"], active_rows)
    atomic_parquet(p["profiles"], profiles)
    metadata = {
        "slug": slug,
        "track": track,
        "fold_or_domain": fold,
        "common_items": len(item_ids),
        "warm_items": int(warm.sum()),
        "cold_items": int(cold.sum()),
        "evaluation_users": len(profiles),
        "train_users": len(keyed),
        "signed_interactions": interactions.nnz,
        "positive": int(np.count_nonzero(interactions.data == 1)),
        "negative": int(np.count_nonzero(interactions.data == -1)),
        "feature_columns": features.shape[1],
        "active_cold_rows": int((active_rows & cold).sum()),
        "reader": counters,
        "target_rating_values_opened": False,
        "timestamps_opened": False,
    }
    atomic_json(p["prepared_integrity"], {
        "status": "SEALED_OUTER_PREPARED_WITHOUT_TARGET_LABELS",
        "artifacts": {name: artifact(path) for name, path in artifacts.items()},
        "metadata": metadata,
    })
    progress(g, "PREPARED_OUTER", **metadata)
    return metadata


def prepare_all(
    contract: dict[str, Any], output_root: Path, g: Mapping[str, Path], resume: bool
) -> dict[str, Any]:
    if g["prepared"].exists():
        if not resume:
            raise ResumeError("replication prepared state requires --resume")
        value = json.loads(g["prepared"].read_text(encoding="utf-8"))
        for spec in value["outer_integrities"]:
            verify_artifact(spec)
        return value
    item_ids, structured, _ = common_features(contract)
    summaries = []
    for slug, track, fold in OUTERS:
        summaries.append(prepare_outer(
            contract, output_root, g, item_ids, structured, slug, track, fold, resume
        ))
    value = {
        "status": "SEALED_ALL_REPLICATION_AND_TRANSFER_PREPARED_WITHOUT_TARGET_LABELS",
        "outer_integrities": [artifact(outer_paths(output_root, slug)["prepared_integrity"]) for slug, _, _ in OUTERS],
        "summaries": summaries,
        "target_rating_values_opened": False,
        "timestamps_opened": False,
    }
    atomic_json(g["prepared"], value)
    return value


def lightfm_job(
    contract: dict[str, Any], output_root: Path, slug: str, seed: int, resume: bool
) -> tuple[dict[str, Path], dict[str, Any]] | None:
    p = outer_paths(output_root, slug)
    directory = p["base"] / f"fits/lightfm/S{seed}"
    members = {
        "config": directory / "config.json",
        "result": directory / "result.npz",
        "integrity": directory / "integrity.json",
    }
    model = contract["models"]["FEATURE_ONLY_LIGHTFM"]
    config = {
        "seed": seed,
        "dimension": int(model["parameters"]["dimension"]),
        "epochs": int(model["parameters"]["epochs"]),
        "learning_rate": float(model["parameters"]["learning_rate"]),
        "item_alpha": float(model["parameters"]["item_alpha"]),
        "user_alpha": float(model["parameters"]["user_alpha"]),
        "interaction_sha256": sha256_file(p["interactions"]),
        "item_feature_sha256": sha256_file(p["features"]),
        "item_identity_features": False,
    }
    if members["integrity"].exists():
        if not resume or not all(path.is_file() for path in members.values()):
            raise ResumeError(f"partial sealed LightFM job: {slug} S{seed}")
        if json.loads(members["config"].read_text(encoding="utf-8")) != config:
            raise ResumeError(f"LightFM job config drift: {slug} S{seed}")
        integrity = json.loads(members["integrity"].read_text(encoding="utf-8"))
        verify_artifact(integrity["config"])
        verify_artifact(integrity["result"])
        return None
    if members["result"].exists():
        raise ResumeError(f"unsealed LightFM result: {slug} S{seed}")
    if members["config"].exists():
        if not resume or json.loads(members["config"].read_text(encoding="utf-8")) != config:
            raise ResumeError(f"LightFM job config drift or --resume missing: {slug} S{seed}")
    else:
        directory.mkdir(parents=True, exist_ok=True)
        atomic_json(members["config"], config)
    return members, config


def fit_all(
    contract: dict[str, Any],
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
            raise ResumeError("replication fit state requires --resume")
        value = json.loads(g["fits"].read_text(encoding="utf-8"))
        for spec in value["outer_integrities"]:
            verify_artifact(spec)
        return value

    pending: list[tuple[str, int, dict[str, Path]]] = []
    seeds = list(map(int, contract["models"]["FEATURE_ONLY_LIGHTFM"]["seeds"]))
    for slug, _, _ in OUTERS:
        for seed in seeds:
            job = lightfm_job(contract, output_root, slug, seed, resume)
            if job is not None:
                members, _ = job
                pending.append((slug, seed, members))
    active: list[tuple[str, int, dict[str, Path], subprocess.Popen[Any]]] = []
    model = contract["models"]["FEATURE_ONLY_LIGHTFM"]
    while pending or active:
        while pending and len(active) < max_jobs:
            slug, seed, members = pending.pop(0)
            p = outer_paths(output_root, slug)
            rel_job = members["config"].parent.relative_to(ROOT).as_posix()
            rel_interactions = p["interactions"].relative_to(ROOT).as_posix()
            rel_features = p["features"].relative_to(ROOT).as_posix()
            command = "\n".join([
                "python -m pip install --disable-pip-version-check --require-hashes -r requirements-rec-ev-019c.lock",
                f"python scripts/train_rec_ev_027_lightfm.py --job /workspace/{rel_job} --interactions /workspace/{rel_interactions} --item-features /workspace/{rel_features}",
            ])
            process = subprocess.Popen([
                "docker", "run", "--rm", "--platform", "linux/amd64",
                "--mount", f"type=bind,source={ROOT},target=/workspace",
                "--mount", "type=volume,source=feelm-rec-ev-019c-pip,target=/root/.cache/pip",
                "--workdir", "/workspace", model["dependency"]["runtime_image"], "sh", "-ec", command,
            ])
            active.append((slug, seed, members, process))
            progress(g, "FIT_OUTER_LIGHTFM_START", outer=slug, seed=seed, active_jobs=len(active))
        completed_index = None
        for index, (slug, seed, members, process) in enumerate(active):
            returncode = process.poll()
            if returncode is None:
                continue
            completed_index = index
            if returncode:
                for _, _, _, other in active:
                    if other.poll() is None:
                        other.terminate()
                raise RuntimeError(f"LightFM job failed: {slug} S{seed} exit {returncode}")
            fitted = np.load(members["result"], allow_pickle=False)
            if fitted["item_factors"].shape != (85_517, 128) or not np.isfinite(fitted["item_factors"]).all():
                raise RuntimeError(f"LightFM output drift: {slug} S{seed}")
            atomic_json(members["integrity"], {
                "status": "SEALED_OUTER_LIGHTFM_SEED",
                "config": artifact(members["config"]),
                "result": artifact(members["result"]),
                "outer": slug,
                "seed": seed,
            })
            progress(g, "FIT_OUTER_LIGHTFM_COMPLETE", outer=slug, seed=seed)
            break
        if completed_index is not None:
            active.pop(completed_index)
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
        atomic_json(p["fit_integrity"], {
            "status": "SEALED_FROZEN_LIGHTFM_THREE_SEEDS",
            "outer": slug,
            "jobs": jobs,
        })
        outer_integrities.append(artifact(p["fit_integrity"]))
    value = {
        "status": "SEALED_ALL_REPLICATION_AND_TRANSFER_MODEL_FITS",
        "outer_integrities": outer_integrities,
        "models": list(MODELS),
        "seeds": seeds,
    }
    atomic_json(g["fits"], value)
    return value


def top2_seed_stability(top2: Sequence[Sequence[int]]) -> float:
    values = [set(map(int, pair)) for pair in top2]
    overlaps = [len(values[i] & values[j]) / 2.0 for i in range(len(values)) for j in range(i + 1, len(values))]
    return float(np.mean(overlaps)) if overlaps else 1.0


def score_outer(
    contract: dict[str, Any],
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
    prior = np.load(p["prior"], allow_pickle=False)["g0_mid"].astype(np.float64)
    active_rows = np.load(p["feature_rows"], allow_pickle=False).astype(bool)
    seeds = list(map(int, contract["models"]["FEATURE_ONLY_LIGHTFM"]["seeds"]))
    fits = {}
    for seed in seeds:
        fitted = np.load(p["base"] / f"fits/lightfm/S{seed}/result.npz", allow_pickle=False)
        fits[seed] = (
            fitted["item_biases"].astype(np.float64),
            fitted["item_factors"].astype(np.float64),
        )
    movie_lookup = lookup_array(item_ids)
    rows: list[dict[str, Any]] = []
    progress(g, "SCORE_OUTER", outer=slug, users=len(profiles))
    for number, row in enumerate(profiles.itertuples(index=False), start=1):
        key = str(row.user_key)
        profile_movies = np.asarray(row.profile_movie_ids, dtype=np.int64)
        target_movies = np.asarray(row.target_movie_ids, dtype=np.int64)
        profile_positions = movie_lookup[profile_movies]
        target_positions = movie_lookup[target_movies]
        if bool((profile_positions < 0).any()) or bool((target_positions < 0).any()):
            raise RuntimeError(f"outer membership outside common support: {slug}")
        indices = np.asarray(row.profile_rating_idx, dtype=np.int8)
        similarity = (structured[target_positions] @ structured[profile_positions].T).toarray().astype(np.float64)
        for encoding in ENCODINGS:
            weights = profile_weights(indices, prior, encoding)
            direct_scores, direct_active = weighted_similarity_scores(similarity, weights)
            direct_order = (
                deterministic_order(
                    target_movies, direct_scores, phase="REPLICATION_AND_TRANSFER", track=track,
                    fold=fold, model="STRUCTURED_DIRECT", encoding=encoding, user_key=key,
                ) if direct_active else np.empty(0, dtype=np.int16)
            )
            rows.append({
                "user_key": key, "track": track, "fold_or_domain": fold,
                "encoding": encoding, "model": "STRUCTURED_DIRECT", "active": direct_active,
                "target_movie_ids": target_movies.tolist(),
                "scores": direct_scores.tolist() if direct_active else [],
                "ranked_target_indices": direct_order.tolist(),
                "seed_top2_overlap": None,
            })
            seed_scores: list[np.ndarray] = []
            seed_top2: list[list[int]] = []
            lightfm_active = bool(active_rows[profile_positions].all() and active_rows[target_positions].all())
            if lightfm_active:
                for seed in seeds:
                    biases, factors = fits[seed]
                    values, active = fold_in_lightfm(
                        biases, factors, profile_positions, weights, target_positions,
                        regularization=float(contract["models"]["FEATURE_ONLY_LIGHTFM"]["parameters"]["user_alpha"]),
                    )
                    lightfm_active &= active
                    seed_scores.append(values)
                    if active:
                        seed_order = deterministic_order(
                            target_movies, values, phase="REPLICATION_AND_TRANSFER", track=track,
                            fold=fold, model="FEATURE_ONLY_LIGHTFM", encoding=encoding, user_key=key,
                        )
                        seed_top2.append(target_movies[seed_order[:2]].astype(int).tolist())
            lightfm_scores = np.mean(seed_scores, axis=0) if lightfm_active else np.full(len(target_movies), np.nan)
            lightfm_active = bool(lightfm_active and np.isfinite(lightfm_scores).all() and np.unique(lightfm_scores).size >= 2)
            lightfm_order = (
                deterministic_order(
                    target_movies, lightfm_scores, phase="REPLICATION_AND_TRANSFER", track=track,
                    fold=fold, model="FEATURE_ONLY_LIGHTFM", encoding=encoding, user_key=key,
                ) if lightfm_active else np.empty(0, dtype=np.int16)
            )
            rows.append({
                "user_key": key, "track": track, "fold_or_domain": fold,
                "encoding": encoding, "model": "FEATURE_ONLY_LIGHTFM", "active": lightfm_active,
                "target_movie_ids": target_movies.tolist(),
                "scores": lightfm_scores.tolist() if lightfm_active else [],
                "ranked_target_indices": lightfm_order.tolist(),
                "seed_top2_overlap": top2_seed_stability(seed_top2) if lightfm_active else None,
            })
        if number % 500 == 0:
            progress(g, "SCORE_OUTER", outer=slug, users_complete=number, users_total=len(profiles))
    result = pd.DataFrame(rows).sort_values(["encoding", "model", "user_key"], kind="stable", ignore_index=True)
    expected = len(profiles) * len(ENCODINGS) * len(MODELS)
    if len(result) != expected or result.duplicated(["user_key", "encoding", "model"]).any():
        raise RuntimeError(f"outer score row-set drift: {slug}")
    atomic_parquet(p["scores"], result)
    value = {
        "status": "SEALED_OUTER_PRIMARY_AND_BINARY_SCORES_AND_RANKS_BEFORE_LABELS",
        "outer": slug,
        "score_ranks": artifact(p["scores"]),
        "rows": len(result),
        "users": len(profiles),
        "target_rating_values_opened": False,
        "timestamps_opened": False,
    }
    atomic_json(p["score_integrity"], value)
    progress(g, "SCORED_OUTER", outer=slug, rows=len(result))
    return value


def score_all(
    contract: dict[str, Any], output_root: Path, g: Mapping[str, Path], resume: bool
) -> dict[str, Any]:
    if g["scores"].exists():
        if not resume:
            raise ResumeError("all replication scores require --resume")
        value = json.loads(g["scores"].read_text(encoding="utf-8"))
        for spec in value["outer_integrities"]:
            verify_artifact(spec)
        return value
    item_ids, structured, _ = common_features(contract)
    values = []
    for slug, track, fold in OUTERS:
        values.append(score_outer(
            contract, output_root, g, item_ids, structured, slug, track, fold, resume
        ))
    value = {
        "status": "SEALED_ALL_REPLICATION_AND_BOTH_TRANSFER_PRIMARY_AND_BINARY_SCORES_AND_RANKS_BEFORE_ANY_PHASE_LABELS",
        "outer_integrities": [artifact(outer_paths(output_root, slug)["score_integrity"]) for slug, _, _ in OUTERS],
        "rows": int(sum(entry["rows"] for entry in values)),
        "target_rating_values_opened": False,
        "timestamps_opened": False,
    }
    atomic_json(g["scores"], value)
    return value


def verify_global_prelabel_score_seal(
    output_root: Path, g: Mapping[str, Path]
) -> dict[str, Any]:
    score_seal = json.loads(g["scores"].read_text(encoding="utf-8"))
    if score_seal["target_rating_values_opened"] is not False or len(score_seal["outer_integrities"]) != len(OUTERS):
        raise RuntimeError("complete global pre-label rank seal required")
    for spec in score_seal["outer_integrities"]:
        outer_integrity_path = verify_artifact(spec)
        outer_integrity = json.loads(outer_integrity_path.read_text(encoding="utf-8"))
        score_path = verify_artifact(outer_integrity["score_ranks"])
        if score_path.resolve() != outer_paths(output_root, str(outer_integrity["outer"]))["scores"].resolve():
            raise RuntimeError("nested outer score artifact path drift")
    return score_seal


def open_all_labels(
    contract: dict[str, Any], output_root: Path, g: Mapping[str, Path], resume: bool
) -> dict[str, Any]:
    if g["labels"].exists():
        if not resume:
            raise ResumeError("replication labels require --resume")
        value = json.loads(g["labels"].read_text(encoding="utf-8"))
        for spec in value["label_artifacts"]:
            verify_artifact(spec)
        return value
    verify_global_prelabel_score_seal(output_root, g)

    profiles_by_slug = {
        slug: pd.read_parquet(outer_paths(output_root, slug)["profiles"]).sort_values(
            "user_key", kind="stable", ignore_index=True
        )
        for slug, _, _ in OUTERS
    }
    union_keys = sorted({
        str(key)
        for frame in profiles_by_slug.values()
        for key in frame["user_key"].astype(str)
    })
    key_to_union = {key: index for index, key in enumerate(union_keys)}
    raw_to_union = reverse_user_keys(union_keys)
    appearances: dict[int, list[tuple[str, int]]] = defaultdict(list)
    target_slots: dict[tuple[str, int], dict[int, int]] = {}
    target_values: dict[tuple[str, int], np.ndarray] = {}
    for slug, frame in profiles_by_slug.items():
        for row, value in enumerate(frame.itertuples(index=False)):
            union_row = key_to_union[str(value.user_key)]
            appearances[union_row].append((slug, row))
            key = (slug, row)
            target_slots[key] = {int(movie): slot for slot, movie in enumerate(value.target_movie_ids)}
            target_values[key] = np.full(len(value.target_movie_ids), -1, dtype=np.int8)
    full_hist = np.zeros((len(union_keys), 10), dtype=np.uint32)
    counters = {
        "raw_rows": 0,
        "evaluation_user_rating_values_parsed": 0,
        "target_rating_values_parsed": 0,
        "timestamps_parsed": 0,
    }
    archive = Path(contract["allowed_input_artifacts"]["movielens_archive"]["path"])
    progress(g, "OPEN_ALL_REPLICATION_AND_TRANSFER_LABELS_AFTER_GLOBAL_SCORE_SEAL", users=len(union_keys))
    with zipfile.ZipFile(archive) as bundle, bundle.open(_zip_member(bundle, "ratings.csv")) as handle:
        if handle.readline().rstrip(b"\r\n") != b"userId,movieId,rating,timestamp":
            raise RuntimeError("MovieLens ratings header drift")
        for raw in handle:
            counters["raw_rows"] += 1
            uid, movie, second = _parse_user_movie(raw)
            union_row = raw_to_union.get(uid)
            if union_row is None:
                continue
            third = raw.find(b",", second + 1)
            index = rating_index(raw[second + 1 : third])
            full_hist[union_row, index] += 1
            counters["evaluation_user_rating_values_parsed"] += 1
            for slug, row in appearances[union_row]:
                slot = target_slots[(slug, row)].get(movie)
                if slot is not None:
                    if target_values[(slug, row)][slot] >= 0:
                        raise RuntimeError("duplicate outer target rating")
                    target_values[(slug, row)][slot] = index
                    counters["target_rating_values_parsed"] += 1
    if bool((full_hist.sum(axis=1) <= 0).any()) or counters["timestamps_parsed"]:
        raise RuntimeError("invalid global evaluation history")
    label_artifacts = []
    total_targets = 0
    for slug, frame in profiles_by_slug.items():
        rows = []
        for row, value in enumerate(frame.itertuples(index=False)):
            indices = target_values[(slug, row)]
            if bool((indices < 0).any()):
                raise RuntimeError(f"missing target label: {slug} row {row}")
            union_row = key_to_union[str(value.user_key)]
            q_eval = full_history_q(indices, full_hist[union_row])
            rows.append({
                "user_key": str(value.user_key),
                "target_movie_ids": list(map(int, value.target_movie_ids)),
                "target_q_eval": q_eval.astype(np.float64).tolist(),
            })
            total_targets += len(indices)
        destination = outer_paths(output_root, slug)["labels"]
        atomic_parquet(destination, pd.DataFrame(rows))
        label_artifacts.append(artifact(destination))
    value = {
        "status": "OPENED_ALL_REPLICATION_AND_TRANSFER_LABELS_ONCE_AFTER_GLOBAL_SCORE_SEAL",
        "score_seal": artifact(g["scores"]),
        "label_artifacts": label_artifacts,
        "union_users": len(union_keys),
        "target_labels": total_targets,
        "reader": counters,
        "timestamps_opened": False,
        "screen_labels_reopened": False,
        "locked_test_opened": False,
        "final_reserve_opened": False,
    }
    atomic_json(g["labels"], value)
    progress(g, "ALL_LABELS_COMPLETE", union_users=len(union_keys), target_labels=total_targets)
    return value


def metrics_outer(output_root: Path, slug: str, track: str, fold: str) -> pd.DataFrame:
    p = outer_paths(output_root, slug)
    if p["metrics"].exists():
        return pd.read_parquet(p["metrics"])
    scores = pd.read_parquet(p["scores"])
    labels = pd.read_parquet(p["labels"])
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
            "user_key": key, "track": track, "fold_or_domain": fold,
            "encoding": encoding, "model": "RANDOM_EXPECTATION", **random_metrics,
            "PAIRWISE_CONCORDANCE": np.nan, "active": True,
            "seed_top2_overlap": np.nan, "used_random_expectation": True,
        })
        if set(group["model"]) != set(MODELS):
            raise RuntimeError(f"metric model Cartesian drift: {slug}")
        for score_row in group.itertuples(index=False):
            active = bool(score_row.active)
            if active:
                order = np.asarray(score_row.ranked_target_indices, dtype=np.int64)
                values = np.asarray(score_row.scores, dtype=np.float64)
                if sorted(order.tolist()) != list(range(len(q_eval))) or values.shape != q_eval.shape:
                    raise RuntimeError(f"active score shape drift: {slug}")
                top2 = ranked_top2_metrics(order, q_eval)
                concordance = pairwise_concordance(values, q_eval)
            else:
                top2 = random_metrics
                concordance = np.nan
            rows.append({
                "user_key": key, "track": track, "fold_or_domain": fold,
                "encoding": encoding, "model": str(score_row.model), **top2,
                "PAIRWISE_CONCORDANCE": concordance, "active": active,
                "seed_top2_overlap": score_row.seed_top2_overlap,
                "used_random_expectation": not active,
            })
    result = pd.DataFrame(rows).sort_values(["encoding", "model", "user_key"], kind="stable", ignore_index=True)
    expected = len(labels) * len(ENCODINGS) * (len(MODELS) + 1)
    if len(result) != expected or result.duplicated(["user_key", "encoding", "model"]).any():
        raise RuntimeError(f"metric row-set drift: {slug}")
    atomic_parquet(p["metrics"], result)
    return result


def materialize_all_metrics(output_root: Path, g: Mapping[str, Path], resume: bool) -> dict[str, Any]:
    if g["metrics"].exists():
        if not resume:
            raise ResumeError("all replication metrics require --resume")
        value = json.loads(g["metrics"].read_text(encoding="utf-8"))
        for spec in value["metric_artifacts"]:
            verify_artifact(spec)
        return value
    metric_artifacts = []
    total_rows = 0
    for slug, track, fold in OUTERS:
        frame = metrics_outer(output_root, slug, track, fold)
        metric_artifacts.append(artifact(outer_paths(output_root, slug)["metrics"]))
        total_rows += len(frame)
    value = {
        "status": "SEALED_ALL_REPLICATION_AND_TRANSFER_USER_METRICS",
        "metric_artifacts": metric_artifacts,
        "rows": total_rows,
    }
    atomic_json(g["metrics"], value)
    return value


def benefit(model_values: np.ndarray, comparator_values: np.ndarray, metric: str) -> np.ndarray:
    return comparator_values - model_values if metric == "HARM20" else model_values - comparator_values


def missing_max_t(
    matrix: np.ndarray, *, repeats: int, seed: int
) -> dict[str, np.ndarray | float]:
    values = np.asarray(matrix, dtype=np.float64)
    if values.ndim != 2 or values.shape[0] < 2:
        raise ValueError("union-users by contrasts matrix required")
    point = np.nanmean(values, axis=0)
    counts = np.sum(np.isfinite(values), axis=0)
    if bool((counts < 2).any()):
        raise RuntimeError("contrast has fewer than two users")
    se = np.nanstd(values, axis=0, ddof=1) / np.sqrt(counts)
    nonzero = se > 0
    maxima = np.zeros(repeats, dtype=np.float64)
    rng = np.random.Generator(np.random.PCG64(seed))
    n = len(values)
    for repeat in range(repeats):
        indices = rng.integers(0, n, size=n, endpoint=False, dtype=np.int64)
        sampled = values[indices]
        sampled_means = np.nanmean(sampled, axis=0)
        if not np.isfinite(sampled_means).all():
            raise RuntimeError("empty resampled contrast")
        if bool(nonzero.any()):
            maxima[repeat] = float(np.max(np.abs((sampled_means[nonzero] - point[nonzero]) / se[nonzero])))
    critical = float(np.quantile(maxima, 0.95, method="higher"))
    half = critical * se
    return {"point": point, "se": se, "low": point - half, "high": point + half, "critical": critical, "counts": counts}


def metric_series(frame: pd.DataFrame, model: str, metric: str) -> pd.Series:
    subset = frame.loc[frame["model"].eq(model), ["user_key", metric]].set_index("user_key")[metric]
    if subset.index.duplicated().any():
        raise RuntimeError("duplicate metric user")
    return subset.astype(float)


def aggregate_metrics(frames: Mapping[str, pd.DataFrame]) -> list[dict[str, Any]]:
    rows = []
    for slug, frame in frames.items():
        for (encoding, model), group in frame.groupby(["encoding", "model"], sort=True, observed=True):
            active = group["active"].to_numpy(dtype=bool)
            concordance = group.loc[active, "PAIRWISE_CONCORDANCE"].dropna().to_numpy(dtype=float)
            stability = group.loc[active, "seed_top2_overlap"].dropna().to_numpy(dtype=float)
            rows.append({
                "outer": slug, "encoding": encoding, "model": model, "users": len(group),
                "active_rate": float(active.mean()), "harm20": float(group["HARM20"].mean()),
                "top2_mean_q": float(group["TOP2_MEAN_Q"].mean()),
                "top2_min_q": float(group["TOP2_MIN_Q"].mean()), "good80": float(group["GOOD80"].mean()),
                "active_pairwise_concordance": float(concordance.mean()) if len(concordance) else None,
                "mean_seed_top2_overlap": float(stability.mean()) if len(stability) else None,
            })
    return rows


def classify_replication_truth(
    *, active_ok: bool, lower_ok: bool, direction_ok: bool, upper_fail: bool,
    random_comparison: bool,
) -> str:
    if active_ok and lower_ok and direction_ok:
        return "PASS_RANDOM_REPLICATION" if random_comparison else "PASS_DIRECT_INCREMENT"
    if upper_fail or (random_comparison and not active_ok):
        return "FAIL_RANDOM_REPLICATION" if random_comparison else "FAIL_DIRECT_INCREMENT"
    return "INCONCLUSIVE_RANDOM_REPLICATION" if random_comparison else "INCONCLUSIVE_DIRECT_INCREMENT"


def random_replication_analysis(
    contract: dict[str, Any], frames: Mapping[str, pd.DataFrame]
) -> dict[str, Any]:
    primary_metrics = list(contract["metrics"]["primary"])
    folds = ("R1", "R2", "R3", "R4")
    primary = {
        slug: frames[slug].loc[frames[slug]["encoding"].eq("PERCENTILE_MAGNITUDE")].copy()
        for slug in folds
    }
    descriptions = []
    pooled_maps: list[dict[str, float]] = []
    fold_points: dict[str, dict[str, float]] = {slug: {} for slug in folds}
    for metric in primary_metrics:
        for model, comparator in (
            ("STRUCTURED_DIRECT", "RANDOM_EXPECTATION"),
            ("FEATURE_ONLY_LIGHTFM", "RANDOM_EXPECTATION"),
            ("FEATURE_ONLY_LIGHTFM", "STRUCTURED_DIRECT"),
        ):
            by_user: dict[str, list[float]] = defaultdict(list)
            contrast_id = f"{model}_VS_{comparator}_{metric}"
            for slug in folds:
                model_values = metric_series(primary[slug], model, metric)
                comparator_values = metric_series(primary[slug], comparator, metric)
                users = sorted(set(model_values.index) & set(comparator_values.index))
                values = benefit(
                    model_values.reindex(users).to_numpy(), comparator_values.reindex(users).to_numpy(), metric
                )
                fold_points[slug][contrast_id] = float(values.mean())
                for key, value in zip(users, values, strict=True):
                    by_user[key].append(float(value))
            pooled_maps.append({key: float(np.mean(values)) for key, values in by_user.items()})
            descriptions.append({"id": contrast_id, "model": model, "comparator": comparator, "metric": metric})
    union_users = sorted({key for values in pooled_maps for key in values})
    matrix = np.full((len(union_users), len(descriptions)), np.nan, dtype=np.float64)
    positions = {key: index for index, key in enumerate(union_users)}
    for column, values in enumerate(pooled_maps):
        for key, value in values.items():
            matrix[positions[key], column] = value
    inference = missing_max_t(
        matrix,
        repeats=int(contract["statistics"]["replication_repeats"]),
        seed=int(contract["statistics"]["replication_seed"]),
    )
    intervals = []
    for index, description in enumerate(descriptions):
        intervals.append({
            **description,
            "users": int(inference["counts"][index]),
            "point_benefit": float(inference["point"][index]),
            "se": float(inference["se"][index]),
            "simultaneous_low": float(inference["low"][index]),
            "simultaneous_high": float(inference["high"][index]),
            "fold_points": {slug: fold_points[slug][description["id"]] for slug in folds},
        })
    lookup = {(row["model"], row["comparator"], row["metric"]): row for row in intervals}
    gate = float(contract["statistics"]["active_rate_gate"])
    active_rates: dict[str, dict[str, float]] = {}
    pooled_active: dict[str, float] = {}
    for model in MODELS:
        active_rates[model] = {
            slug: float(primary[slug].loc[primary[slug]["model"].eq(model), "active"].mean())
            for slug in folds
        }
        availability: dict[str, list[bool]] = defaultdict(list)
        for slug in folds:
            subset = primary[slug].loc[primary[slug]["model"].eq(model), ["user_key", "active"]]
            for row in subset.itertuples(index=False):
                availability[str(row.user_key)].append(bool(row.active))
        pooled_active[model] = float(np.mean([all(values) for values in availability.values()]))

    def truth(model: str, comparator: str) -> str:
        active_ok = all(value >= gate for value in active_rates[model].values()) and pooled_active[model] >= gate
        rows = [lookup[(model, comparator, metric)] for metric in primary_metrics]
        lower_ok = (
            rows[0]["simultaneous_low"] >= 0
            and rows[1]["simultaneous_low"] > 0
            and rows[2]["simultaneous_low"] > 0
        )
        direction_ok = all(
            sum(
                (value >= 0 if row["metric"] == "HARM20" else value > 0)
                for value in row["fold_points"].values()
            ) >= 3
            for row in rows
        )
        upper_fail = (
            rows[0]["simultaneous_high"] < 0
            or rows[1]["simultaneous_high"] <= 0
            or rows[2]["simultaneous_high"] <= 0
        )
        return classify_replication_truth(
            active_ok=active_ok,
            lower_ok=lower_ok,
            direction_ok=direction_ok,
            upper_fail=upper_fail,
            random_comparison=comparator == "RANDOM_EXPECTATION",
        )

    system_truth = {
        "STRUCTURED_DIRECT_vs_random": truth("STRUCTURED_DIRECT", "RANDOM_EXPECTATION"),
        "FEATURE_ONLY_LIGHTFM_vs_random": truth("FEATURE_ONLY_LIGHTFM", "RANDOM_EXPECTATION"),
        "FEATURE_ONLY_LIGHTFM_vs_structured_direct": truth("FEATURE_ONLY_LIGHTFM", "STRUCTURED_DIRECT"),
    }
    if (
        system_truth["FEATURE_ONLY_LIGHTFM_vs_random"] == "PASS_RANDOM_REPLICATION"
        and system_truth["FEATURE_ONLY_LIGHTFM_vs_structured_direct"] == "PASS_DIRECT_INCREMENT"
    ):
        outcome = "LEARNED_INCREMENT_REPLICATED"
    elif any(value == "PASS_RANDOM_REPLICATION" for key, value in system_truth.items() if "vs_random" in key):
        outcome = "CONTENT_SIGNAL_WITHOUT_LEARNED_INCREMENT"
    elif all(value == "FAIL_RANDOM_REPLICATION" for key, value in system_truth.items() if "vs_random" in key):
        outcome = "NO_REPLICATION_SIGNAL"
    else:
        outcome = "INCONCLUSIVE_REPLICATION"
    return {
        "status": outcome,
        "union_users": len(union_users),
        "critical": float(inference["critical"]),
        "active_rates_by_fold": active_rates,
        "pooled_active_user_rates": pooled_active,
        "system_truth": system_truth,
        "intervals": intervals,
    }


def transfer_analysis(
    contract: dict[str, Any], frames: Mapping[str, pd.DataFrame]
) -> dict[str, Any]:
    primary_metrics = list(contract["metrics"]["primary"])
    tracks = ("KR", "RECENT")
    primary = {
        slug: frames[slug].loc[frames[slug]["encoding"].eq("PERCENTILE_MAGNITUDE")].copy()
        for slug in tracks
    }
    descriptions = []
    maps = []
    for slug in tracks:
        for metric in primary_metrics:
            for model, comparator in (
                ("STRUCTURED_DIRECT", "RANDOM_EXPECTATION"),
                ("FEATURE_ONLY_LIGHTFM", "RANDOM_EXPECTATION"),
                ("FEATURE_ONLY_LIGHTFM", "STRUCTURED_DIRECT"),
            ):
                model_values = metric_series(primary[slug], model, metric)
                comparator_values = metric_series(primary[slug], comparator, metric)
                users = sorted(set(model_values.index) & set(comparator_values.index))
                values = benefit(
                    model_values.reindex(users).to_numpy(), comparator_values.reindex(users).to_numpy(), metric
                )
                maps.append(dict(zip(users, map(float, values), strict=True)))
                descriptions.append({
                    "track": slug, "model": model, "comparator": comparator, "metric": metric,
                })
    union_users = sorted({key for values in maps for key in values})
    matrix = np.full((len(union_users), len(maps)), np.nan, dtype=np.float64)
    positions = {key: index for index, key in enumerate(union_users)}
    for column, values in enumerate(maps):
        for key, value in values.items():
            matrix[positions[key], column] = value
    inference = missing_max_t(
        matrix,
        repeats=int(contract["statistics"]["transfer_repeats"]),
        seed=int(contract["statistics"]["transfer_seed"]),
    )
    intervals = []
    for index, description in enumerate(descriptions):
        intervals.append({
            **description, "users": int(inference["counts"][index]),
            "point_benefit": float(inference["point"][index]),
            "se": float(inference["se"][index]),
            "simultaneous_low": float(inference["low"][index]),
            "simultaneous_high": float(inference["high"][index]),
        })
    lookup = {
        (row["track"], row["model"], row["comparator"], row["metric"]): row
        for row in intervals
    }
    gate = float(contract["statistics"]["active_rate_gate"])
    system_truth: dict[str, str] = {}
    track_truth: dict[str, str] = {}
    for slug in tracks:
        statuses = []
        for model in MODELS:
            active_rate = float(primary[slug].loc[primary[slug]["model"].eq(model), "active"].mean())
            rows = [lookup[(slug, model, "RANDOM_EXPECTATION", metric)] for metric in primary_metrics]
            passes = (
                active_rate >= gate and rows[0]["simultaneous_low"] >= 0
                and rows[1]["simultaneous_low"] > 0 and rows[2]["simultaneous_low"] > 0
            )
            fails = (
                active_rate < gate or rows[0]["simultaneous_high"] < 0
                or rows[1]["simultaneous_high"] <= 0 or rows[2]["simultaneous_high"] <= 0
            )
            status = "PASS_PROXY_SIGNAL" if passes else "FAIL_PROXY_SIGNAL" if fails else "INCONCLUSIVE_PROXY_SIGNAL"
            system_truth[f"{slug}:{model}_vs_random"] = status
            statuses.append(status)
        if any(value == "PASS_PROXY_SIGNAL" for value in statuses):
            track_truth[slug] = "PROXY_SIGNAL"
        elif all(value == "FAIL_PROXY_SIGNAL" for value in statuses):
            track_truth[slug] = "NO_PROXY_SIGNAL"
        else:
            track_truth[slug] = "INCONCLUSIVE_PROXY_SIGNAL"
    return {
        "status": "COMPLETE_JOINT_PROXY_TRANSFER_INFERENCE",
        "union_users": len(union_users),
        "critical": float(inference["critical"]),
        "system_truth": system_truth,
        "track_truth": track_truth,
        "intervals": intervals,
    }


def analyze_all(
    contract: dict[str, Any], output_root: Path, g: Mapping[str, Path], resume: bool
) -> dict[str, Any]:
    if g["analysis"].exists():
        if not resume:
            raise ResumeError("replication analysis requires --resume")
        value = json.loads(g["analysis"].read_text(encoding="utf-8"))
        verify_artifact(value["random_replication"])
        verify_artifact(value["proxy_transfer"])
        return value
    frames = {
        slug: pd.read_parquet(outer_paths(output_root, slug)["metrics"])
        for slug, _, _ in OUTERS
    }
    aggregates = aggregate_metrics(frames)
    replication = random_replication_analysis(contract, frames)
    replication["aggregate_metrics"] = [row for row in aggregates if row["outer"] in {"R1", "R2", "R3", "R4"}]
    transfer = transfer_analysis(contract, frames)
    transfer["aggregate_metrics"] = [row for row in aggregates if row["outer"] in {"KR", "RECENT"}]
    atomic_json(g["replication_analysis"], replication)
    atomic_json(g["transfer_analysis"], transfer)
    value = {
        "status": "COMPLETE_REC_EV_027_REPLICATION_AND_PROXY_TRANSFER",
        "random_replication": artifact(g["replication_analysis"]),
        "proxy_transfer": artifact(g["transfer_analysis"]),
        "random_replication_outcome": replication["status"],
        "proxy_track_truth": transfer["track_truth"],
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "product_policy_changed": False,
    }
    atomic_json(g["analysis"], value)
    progress(g, "REPLICATION_AND_TRANSFER_ANALYSIS_COMPLETE", **value)
    return value


def label_and_analyze(
    contract: dict[str, Any], output_root: Path, g: Mapping[str, Path], resume: bool
) -> dict[str, Any]:
    open_all_labels(contract, output_root, g, resume)
    materialize_all_metrics(output_root, g, resume)
    return analyze_all(contract, output_root, g, resume)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--phase", choices=("prepare", "fit", "score", "label", "all"), default="all")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--max-jobs", type=int, default=6)
    args = parser.parse_args()
    if not 1 <= args.max_jobs <= 6:
        raise ValueError("--max-jobs must be in [1,6]")
    contract_path = args.contract.resolve()
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    output_root = (
        args.output_root.resolve() if args.output_root is not None
        else (ROOT / contract["output_root"]).resolve()
    )
    g = global_paths(output_root)
    create_or_verify_lock(contract_path, contract, output_root, g, args.resume)
    phases = ("prepare", "fit", "score", "label") if args.phase == "all" else (args.phase,)
    result: dict[str, Any] = {"status": "NO_PHASE_RUN"}
    for phase in phases:
        if phase == "prepare":
            result = prepare_all(contract, output_root, g, args.resume)
        elif phase == "fit":
            result = fit_all(contract, output_root, g, args.resume, args.max_jobs)
        elif phase == "score":
            result = score_all(contract, output_root, g, args.resume)
        elif phase == "label":
            result = label_and_analyze(contract, output_root, g, args.resume)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
