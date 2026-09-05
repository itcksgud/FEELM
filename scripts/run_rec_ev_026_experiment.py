#!/usr/bin/env python3
"""Execute the locked REC-EV-026 content-to-collaborative alignment screen."""

from __future__ import annotations

import argparse
import bisect
from decimal import Decimal, ROUND_FLOOR, localcontext
import hashlib
import itertools
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence
import zipfile

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

try:
    from rec_ev_022a_core import build_structured_full, encoding_weights, full_history_mid_percentiles, user_key
    from run_rec_ev_023ef_preflight import ResumeError, atomic_write_json, canonical_json_bytes, read_json, sha256_file
    from run_rec_ev_026_preflight import build_common_support
    from validate_rec_ev_026_design import bootstrap_uint64, contrast_metadata
    from validate_rec_ev_026_execution import validate
except ImportError:
    from scripts.rec_ev_022a_core import build_structured_full, encoding_weights, full_history_mid_percentiles, user_key
    from scripts.run_rec_ev_023ef_preflight import ResumeError, atomic_write_json, canonical_json_bytes, read_json, sha256_file
    from scripts.run_rec_ev_026_preflight import build_common_support
    from scripts.validate_rec_ev_026_design import bootstrap_uint64, contrast_metadata
    from scripts.validate_rec_ev_026_execution import validate


ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "docs/recommendation/contracts/rec-ev-026-content-cf-alignment-execution.json"
PHASES = {"PROTOCOL_LOCK": 0, "MAPPER_FIT_GATE": 10, "PROFILE_RATING_OPEN": 20, "ALL_HEAD_RANK_SEAL": 30, "EVALUATION_LABEL_OPEN": 40, "METRICS_BOOTSTRAP_RESULT_SEAL": 50}
HEADS = ("CURRENT_FULL", "E5", "E5_TO_BPR")
DOMAINS = ("TARGET", "CONTROL")
SEEDS = (17, 42, 73, 101, 211)


def resolve(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else ROOT / candidate


def output_path(contract: Mapping[str, Any], key: str) -> Path:
    return resolve(str(contract["output_root"])) / str(contract["outputs"][key])


def atomic_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    frame.to_parquet(temporary, index=False)
    temporary.replace(path)


def atomic_npz(path: Path, **arrays: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp.npz")
    np.savez_compressed(temporary, **arrays)
    temporary.replace(path)


def artifact(path: Path) -> dict[str, Any]:
    return {"path": path.relative_to(ROOT).as_posix(), "bytes": path.stat().st_size, "sha256": sha256_file(path)}


def verify_artifact(spec: Mapping[str, Any], expected: Path | None = None) -> None:
    path = resolve(str(spec["path"]))
    if expected is not None and path.resolve() != expected.resolve():
        raise ResumeError("artifact path binding drift")
    if not path.is_file() or path.stat().st_size != int(spec["bytes"]) or sha256_file(path) != str(spec["sha256"]):
        raise ResumeError("artifact hash drift")


def implementations(contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for relative in contract["implementation_artifacts"]:
        path = resolve(relative)
        if not path.is_file():
            raise RuntimeError(f"missing implementation: {path}")
        rows.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return rows


def sources(contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for spec in contract["allowed_input_artifacts"].values():
        path = resolve(spec["path"])
        if not path.is_file() or path.stat().st_size != spec["bytes"] or sha256_file(path) != spec["sha256"]:
            raise RuntimeError(f"source drift: {path}")
        rows.append({"path": spec["path"], "bytes": spec["bytes"], "sha256": spec["sha256"]})
    return rows


def expected_lock(contract: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    validate(dict(contract))
    source_rows, implementation_rows = sources(contract), implementations(contract)
    manifest = {"schema_version": 1, "evidence_id": "REC-EV-026", "sources": source_rows, "implementation_artifacts": implementation_rows, "mapper_fit_complete": False, "profile_ratings_opened": False, "evaluation_labels_opened": False, "timestamp_opened": False, "locked_test_opened": False, "final_reserve_opened": False}
    lock = {"schema_version": 1, "evidence_id": "REC-EV-026", "status": "LOCKED_CONTENT_CF_ALIGNMENT", "contract_sha256": hashlib.sha256(canonical_json_bytes(contract)).hexdigest(), "source_artifacts_sha256": hashlib.sha256(canonical_json_bytes(source_rows)).hexdigest(), "implementation_artifacts_sha256": hashlib.sha256(canonical_json_bytes(implementation_rows)).hexdigest(), "source_manifest_sha256": hashlib.sha256(canonical_json_bytes(manifest)).hexdigest(), "profile_ratings_opened": False, "evaluation_labels_opened": False, "timestamp_opened": False, "locked_test_opened": False, "final_reserve_opened": False, "product_policy_updated": False, "champion": None}
    return manifest, lock


def create_or_verify_lock(contract: Mapping[str, Any], resume: bool) -> dict[str, Any]:
    lock_path, manifest_path = output_path(contract, "protocol_lock"), output_path(contract, "source_manifest")
    present = [lock_path.exists(), manifest_path.exists()]
    if any(present) and not all(present):
        raise ResumeError("partial execution lock")
    if resume and not any(present):
        raise ResumeError("--resume before execution lock")
    if not any(present):
        root = resolve(str(contract["output_root"]))
        downstream = [path for path in root.rglob("*") if path.is_file()] if root.exists() else []
        if downstream:
            raise ResumeError("downstream execution artifacts exist without lock")
    manifest, lock = expected_lock(contract)
    if all(present):
        if not resume:
            raise ResumeError("execution lock exists; use --resume")
        if read_json(lock_path) != lock or read_json(manifest_path) != manifest:
            raise ResumeError("execution lock drift")
        return lock
    atomic_write_json(manifest_path, manifest)
    atomic_write_json(lock_path, lock)
    atomic_write_json(output_path(contract, "progress"), progress(contract, "PROTOCOL_LOCK"))
    return lock


def signature(contract: Mapping[str, Any]) -> str:
    lock = read_json(output_path(contract, "protocol_lock"))
    return hashlib.sha256(canonical_json_bytes({key: lock[key] for key in ("contract_sha256", "source_artifacts_sha256", "implementation_artifacts_sha256", "source_manifest_sha256")})).hexdigest()


def progress(contract: Mapping[str, Any], phase: str) -> dict[str, Any]:
    return {"schema_version": 1, "evidence_id": "REC-EV-026", "run_signature": signature(contract) if output_path(contract, "protocol_lock").is_file() else None, "phase": phase, "phase_index": PHASES[phase], "profile_ratings_opened": PHASES[phase] >= 20, "all_head_ranks_sealed": PHASES[phase] >= 30, "evaluation_labels_opened": PHASES[phase] >= 40, "timestamp_opened": False, "locked_test_opened": False, "final_reserve_opened": False, "product_policy_updated": False, "champion": None}


def current_phase(contract: Mapping[str, Any]) -> str:
    path = output_path(contract, "progress")
    if not path.is_file():
        raise ResumeError("progress missing")
    value = read_json(path)
    phase = str(value.get("phase"))
    if phase not in PHASES or value != progress(contract, phase):
        raise ResumeError("progress schema or invariant drift")
    return phase


def advance(contract: Mapping[str, Any], phase: str) -> None:
    path = output_path(contract, "progress")
    previous_phase = current_phase(contract)
    previous = read_json(path)
    if PHASES[previous_phase] > PHASES[phase]:
        raise ResumeError("progress drift or regression")
    if PHASES[previous_phase] < PHASES[phase]:
        atomic_write_json(path, progress(contract, phase))


def write_integrity(path: Path, members: Mapping[str, Path], metadata: Mapping[str, Any]) -> None:
    atomic_write_json(path, {"schema_version": 1, "run_signature": metadata["run_signature"], "artifacts": {key: artifact(value) for key, value in members.items()}, "metadata": dict(metadata)})


def verify_integrity(path: Path, members: Mapping[str, Path], metadata: Mapping[str, Any]) -> None:
    value = read_json(path)
    expected = {"schema_version": 1, "run_signature": metadata["run_signature"], "artifacts": {key: artifact(member) for key, member in members.items()}, "metadata": dict(metadata)}
    if value != expected:
        raise ResumeError("phase integrity drift")
    for key, member in members.items():
        verify_artifact(value["artifacts"][key], member)


def phase_state(paths: Sequence[Path]) -> bool:
    present = [path.exists() for path in paths]
    if any(present) and not all(present):
        raise ResumeError("partial phase artifact state")
    return all(present)


def design(contract: Mapping[str, Any]) -> dict[str, Any]:
    return read_json(resolve(contract["allowed_input_artifacts"]["design"]["path"]))


def feature_state(contract: Mapping[str, Any]) -> tuple[pd.DataFrame, np.ndarray, np.ndarray, dict[int, np.ndarray]]:
    support = build_common_support(design(contract))
    table = pq.read_table(resolve(contract["allowed_input_artifacts"]["text_embeddings"]["path"]), columns=["movie_id", "embedding"])
    text_ids = table.column("movie_id").to_numpy(zero_copy_only=False).astype(np.int64)
    vectors = table.column("embedding").combine_chunks().values.to_numpy(zero_copy_only=False).astype(np.float32).reshape(len(text_ids), 384)
    lookup = {int(movie): index for index, movie in enumerate(text_ids)}
    e5 = vectors[np.asarray([lookup[int(movie)] for movie in support["movie_id"]], dtype=np.int64)].astype(np.float64)
    e5 /= np.linalg.norm(e5, axis=1, keepdims=True)
    core_ids = pd.read_parquet(resolve(contract["allowed_input_artifacts"]["candidate_core"]["path"]), columns=["movie_id"])["movie_id"].to_numpy(dtype=np.int64)
    factors: dict[int, np.ndarray] = {}
    for seed in SEEDS:
        with np.load(resolve(contract["allowed_input_artifacts"][f"factor_s{seed}"]["path"]), allow_pickle=False) as archive:
            values = archive["item_factors"].astype(np.float64)
        norms = np.linalg.norm(values, axis=1, keepdims=True)
        if values.shape != (41625, 128) or not np.isfinite(values).all() or bool((norms == 0).any()):
            raise RuntimeError("teacher factor drift")
        factors[seed] = values / norms
    return support, e5, core_ids, factors


def candidate_prediction_gate(candidate_x: np.ndarray, coefficient: np.ndarray) -> float:
    predicted = np.asarray(candidate_x, dtype=np.float64) @ np.asarray(coefficient, dtype=np.float64)
    norms = np.linalg.norm(predicted, axis=1)
    if not np.isfinite(predicted).all() or not np.isfinite(norms).all() or bool((norms <= 0).any()):
        raise RuntimeError("INFEASIBLE_MAPPER_FIT_PRELABEL")
    return float(norms.min())


def verify_cached_mapper_npz(path: Path, expected_selection: Mapping[str, Any]) -> None:
    expected_keys = [f"{evidence_id}_S{seed}" for evidence_id in ("REC-EV-026A", "REC-EV-026B") for seed in SEEDS]
    with np.load(path, allow_pickle=False) as cached:
        if cached.files != expected_keys:
            raise ResumeError("mapper NPZ key/order drift")
        for evidence_id in ("REC-EV-026A", "REC-EV-026B"):
            for seed in SEEDS:
                values = cached[f"{evidence_id}_S{seed}"]
                expected_hash = expected_selection["experiments"][evidence_id]["coefficient_sha256"][str(seed)]
                if values.shape != (384, 128) or values.dtype != np.float64 or not np.isfinite(values).all() or hashlib.sha256(values.tobytes(order="C")).hexdigest() != expected_hash:
                    raise ResumeError("mapper coefficient semantic drift")


def fit_mapper(contract: Mapping[str, Any], *, _recompute: bool = False) -> dict[str, Any]:
    phase = current_phase(contract)
    destination, selection_path, integrity = output_path(contract, "mapper"), output_path(contract, "mapper_selection"), output_path(contract, "mapper_integrity")
    meta = {"run_signature": signature(contract), "phase": "MAPPER_FIT_GATE", "profile_rating_bytes_parsed": 0, "evaluation_rating_bytes_parsed": 0, "timestamp_bytes_parsed": 0}
    if not _recompute and phase_state([destination, selection_path, integrity]):
        verify_integrity(integrity, {"mapper": destination, "selection": selection_path}, meta)
        cached_selection = read_json(selection_path)
        expected_selection = fit_mapper(contract, _recompute=True)
        if cached_selection != expected_selection:
            raise ResumeError("mapper deterministic selection drift")
        verify_cached_mapper_npz(destination, expected_selection)
        advance(contract, "MAPPER_FIT_GATE")
        return cached_selection
    if not _recompute and PHASES[phase] > 0:
        raise ResumeError("progress ahead of absent mapper")
    d = design(contract)
    support, e5, core_ids, factors = feature_state(contract)
    membership = pd.read_parquet(resolve(contract["allowed_input_artifacts"]["preflight_membership"]["path"]))
    support_ids = support["movie_id"].to_numpy(dtype=np.int64)
    support_pos = {int(movie): index for index, movie in enumerate(support_ids)}
    core_pos = {int(movie): index for index, movie in enumerate(core_ids)}
    arrays: dict[str, np.ndarray] = {}
    selections: dict[str, Any] = {}
    for evidence_id in ("REC-EV-026A", "REC-EV-026B"):
        spec = d["experiments"][evidence_id]
        if evidence_id == "REC-EV-026A":
            source_mask = ~support["is_korean"].to_numpy(dtype=bool)
        else:
            source_mask = support["release_year"].to_numpy(dtype=np.int64) < 2020
        controls = set(membership.loc[(membership["evidence_id"] == evidence_id) & (membership["role"] == "CONTROL"), "movie_id"].astype(int))
        mapper_ids = [int(movie) for movie, source in zip(support_ids, source_mask) if source and int(movie) in core_pos and int(movie) not in controls]
        split_values = np.asarray([int.from_bytes(hashlib.sha256(f"{spec['mapper_split_salt']}|{movie}".encode("utf-8")).digest()[:8], "big") % 100 for movie in mapper_ids])
        train_ids = [movie for movie, split in zip(mapper_ids, split_values) if split < 80]
        validation_ids = [movie for movie, split in zip(mapper_ids, split_values) if split >= 80]
        if len(train_ids) < 1000 or len(validation_ids) < 200:
            raise RuntimeError("INFEASIBLE_MAPPER_FIT_PRELABEL")
        train_support = np.asarray([support_pos[movie] for movie in train_ids])
        val_support = np.asarray([support_pos[movie] for movie in validation_ids])
        train_core = np.asarray([core_pos[movie] for movie in train_ids])
        val_core = np.asarray([core_pos[movie] for movie in validation_ids])
        x_train, x_val = e5[train_support], e5[val_support]
        xtx = x_train.T @ x_train
        y_train = [factors[seed][train_core] for seed in SEEDS]
        y_val = [factors[seed][val_core] for seed in SEEDS]
        xty = np.concatenate([x_train.T @ values for values in y_train], axis=1)
        alpha_scores: list[dict[str, Any]] = []
        alpha_coefs: dict[float, np.ndarray] = {}
        for alpha in d["mapper"]["alpha_grid"]:
            coef = np.linalg.solve(xtx + float(alpha) * np.eye(384, dtype=np.float64), xty)
            alpha_coefs[float(alpha)] = coef
            seed_cosines = []
            for seed_index in range(5):
                predicted = x_val @ coef[:, seed_index * 128:(seed_index + 1) * 128]
                norms = np.linalg.norm(predicted, axis=1)
                cosine = np.divide(np.sum(predicted * y_val[seed_index], axis=1), norms, out=np.full(len(norms), np.nan), where=norms > 0)
                seed_cosines.append(float(np.mean(cosine)))
            alpha_scores.append({"alpha": float(alpha), "seed_validation_mean_cosines": seed_cosines, "mean_validation_cosine": float(np.mean(seed_cosines))})
        best_score = max(row["mean_validation_cosine"] for row in alpha_scores)
        selected_alpha = min(row["alpha"] for row in alpha_scores if row["mean_validation_cosine"] == best_score)
        all_support = np.asarray([support_pos[movie] for movie in mapper_ids])
        all_core = np.asarray([core_pos[movie] for movie in mapper_ids])
        x_all = e5[all_support]
        xtx_all = x_all.T @ x_all
        y_all = [factors[seed][all_core] for seed in SEEDS]
        xty_all = np.concatenate([x_all.T @ values for values in y_all], axis=1)
        refit = np.linalg.solve(xtx_all + selected_alpha * np.eye(384, dtype=np.float64), xty_all)
        selected_row = next(row for row in alpha_scores if row["alpha"] == selected_alpha)
        if not all(value > 0 and math.isfinite(value) for value in selected_row["seed_validation_mean_cosines"]) or not np.isfinite(refit).all():
            raise RuntimeError("INFEASIBLE_MAPPER_FIT_PRELABEL")
        selected_candidates = sorted(membership.loc[(membership["evidence_id"] == evidence_id) & membership["role"].isin(DOMAINS), "movie_id"].astype(int).unique())
        candidate_x = e5[np.asarray([support_pos[movie] for movie in selected_candidates])]
        candidate_min_norms: dict[str, float] = {}
        coefficient_hashes: dict[str, str] = {}
        for seed_index, seed in enumerate(SEEDS):
            coefficient = refit[:, seed_index * 128:(seed_index + 1) * 128]
            arrays[f"{evidence_id}_S{seed}"] = coefficient
            candidate_min_norms[str(seed)] = candidate_prediction_gate(candidate_x, coefficient)
            coefficient_hashes[str(seed)] = hashlib.sha256(coefficient.tobytes(order="C")).hexdigest()
        selections[evidence_id] = {"mapper_items": len(mapper_ids), "train_items": len(train_ids), "validation_items": len(validation_ids), "selected_candidate_items": len(selected_candidates), "selected_alpha": selected_alpha, "alpha_scores": alpha_scores, "candidate_prediction_min_norms": candidate_min_norms, "coefficient_sha256": coefficient_hashes, "fit_gate": "PASS"}
    selection = {"schema_version": 1, "evidence_id": "REC-EV-026", "status": "MAPPER_FIT_GATE_PASS", "experiments": selections, "profile_ratings_opened": False, "evaluation_labels_opened": False}
    if _recompute:
        return selection
    atomic_npz(destination, **arrays)
    atomic_write_json(selection_path, selection)
    write_integrity(integrity, {"mapper": destination, "selection": selection_path}, meta)
    advance(contract, "MAPPER_FIT_GATE")
    return selection


def raw_user_map(keys: Sequence[str], maximum: int) -> dict[int, str]:
    wanted = set(str(key) for key in keys)
    mapping: dict[int, str] = {}
    for raw_user in range(1, maximum + 1):
        anonymous = user_key(raw_user)
        if anonymous in wanted:
            mapping[raw_user] = anonymous
    if set(mapping.values()) != wanted:
        raise RuntimeError("anonymous user reverse-map drift")
    return mapping


def scan_ratings(contract: Mapping[str, Any], raw_to_key: Mapping[int, str], pair_allowlist: set[tuple[str, int]] | None) -> dict[str, list[tuple[int, float]]]:
    histories: dict[str, list[tuple[int, float]]] = {key: [] for key in raw_to_key.values()}
    archive = contract["allowed_input_artifacts"]["movielens_archive"]
    with zipfile.ZipFile(resolve(archive["path"])) as source:
        with source.open(archive["member"]) as handle:
            if not handle.readline().startswith(b"userId,movieId,rating,"):
                raise RuntimeError("MovieLens header drift")
            for line in handle:
                first = line.find(b",")
                raw_user = int(line[:first])
                if raw_user not in raw_to_key:
                    continue
                second = line.find(b",", first + 1)
                movie = int(line[first + 1:second])
                anonymous = raw_to_key[raw_user]
                if pair_allowlist is not None and (anonymous, movie) not in pair_allowlist:
                    continue
                third = line.find(b",", second + 1)
                rating = float(line[second + 1:third])
                histories[anonymous].append((movie, rating))
    return histories


def profile_phase(contract: Mapping[str, Any], *, _recompute: bool = False) -> pd.DataFrame:
    phase = current_phase(contract)
    destination, integrity = output_path(contract, "profile_ratings"), output_path(contract, "profile_integrity")
    meta = {"run_signature": signature(contract), "phase": "PROFILE_RATING_OPEN", "evaluation_labels_opened": False, "timestamp_bytes_parsed": 0}
    if not _recompute and phase_state([destination, integrity]):
        verify_integrity(integrity, {"profile_ratings": destination}, meta)
        cached = pd.read_parquet(destination)
        expected = profile_phase(contract, _recompute=True)
        try:
            pd.testing.assert_frame_equal(cached, expected, check_dtype=True, check_like=False)
        except AssertionError as error:
            raise ResumeError("profile deterministic drift") from error
        advance(contract, "PROFILE_RATING_OPEN")
        return cached
    if not _recompute and PHASES[phase] != 10:
        raise ResumeError("profile phase order drift")
    membership = pd.read_parquet(resolve(contract["allowed_input_artifacts"]["preflight_membership"]["path"]))
    profile = membership.loc[membership["role"] == "PROFILE"].copy()
    keys = sorted(profile["user_key022"].unique())
    reverse = raw_user_map(keys, int(contract["reader"]["maximum_user_id"]))
    allowlist = set(zip(profile["user_key022"].astype(str), profile["movie_id"].astype(int)))
    histories = scan_ratings(contract, reverse, allowlist)
    ratings = {(key, movie): rating for key, rows in histories.items() for movie, rating in rows}
    if set(ratings) != allowlist:
        raise RuntimeError("profile rating allowlist incomplete")
    profile["rating"] = [ratings[(str(key), int(movie))] for key, movie in zip(profile["user_key022"], profile["movie_id"])]
    frame = profile[["evidence_id", "user_key022", "position", "movie_id", "rating"]].sort_values(["evidence_id", "user_key022", "position"], kind="stable", ignore_index=True)
    if _recompute:
        return frame
    atomic_parquet(destination, frame)
    write_integrity(integrity, {"profile_ratings": destination}, meta)
    advance(contract, "PROFILE_RATING_OPEN")
    return frame


def _normalize(vector: np.ndarray) -> tuple[np.ndarray, bool]:
    norm = float(np.linalg.norm(vector))
    return (vector / norm, True) if math.isfinite(norm) and norm > 0 else (np.zeros_like(vector), False)


def rank_phase(contract: Mapping[str, Any], *, _recompute: bool = False) -> pd.DataFrame:
    phase = current_phase(contract)
    destination, integrity = output_path(contract, "ranks"), output_path(contract, "rank_integrity")
    meta = {"run_signature": signature(contract), "phase": "ALL_HEAD_RANK_SEAL", "evaluation_labels_opened": False, "timestamp_bytes_parsed": 0}
    if not _recompute and phase_state([destination, integrity]):
        verify_integrity(integrity, {"ranks": destination}, meta)
        cached = pd.read_parquet(destination)
        expected = rank_phase(contract, _recompute=True)
        try:
            pd.testing.assert_frame_equal(cached, expected, check_dtype=True, check_like=False)
        except AssertionError as error:
            raise ResumeError("rank deterministic drift") from error
        advance(contract, "ALL_HEAD_RANK_SEAL")
        return cached
    if not _recompute and PHASES[phase] != 20:
        raise ResumeError("rank phase order drift")
    d = design(contract)
    support, e5, core_ids, factors = feature_state(contract)
    ids = support["movie_id"].to_numpy(dtype=np.int64)
    pos = {int(movie): index for index, movie in enumerate(ids)}
    core_pos = {int(movie): index for index, movie in enumerate(core_ids)}
    structured_source = pd.read_parquet(resolve(contract["allowed_input_artifacts"]["structured_features"]["path"]))
    current = build_structured_full(structured_source, ids)
    membership = pd.read_parquet(resolve(contract["allowed_input_artifacts"]["preflight_membership"]["path"]))
    profiles = pd.read_parquet(output_path(contract, "profile_ratings"))
    with np.load(output_path(contract, "mapper"), allow_pickle=False) as archive:
        coefs = {key: archive[key] for key in archive.files}
    with np.load(resolve(contract["allowed_input_artifacts"]["train_prior"]["path"]), allow_pickle=False) as prior:
        g0_mid = prior["g0_mid"].astype(np.float64)
    candidates = membership.loc[membership["role"].isin(DOMAINS)].copy()
    unique_candidates = {evidence: sorted(group["movie_id"].astype(int).unique()) for evidence, group in candidates.groupby("evidence_id")}
    predicted: dict[tuple[str, int], dict[int, np.ndarray]] = {}
    for evidence_id, movie_ids in unique_candidates.items():
        x = e5[np.asarray([pos[movie] for movie in movie_ids])]
        for seed in SEEDS:
            values = x @ coefs[f"{evidence_id}_S{seed}"]
            norms = np.linalg.norm(values, axis=1, keepdims=True)
            values = np.divide(values, norms, out=np.full_like(values, np.nan), where=norms > 0)
            predicted[(evidence_id, seed)] = {movie: values[index] for index, movie in enumerate(movie_ids)}
    rows: list[dict[str, Any]] = []
    profile_lookup = profiles.set_index(["evidence_id", "user_key022"]).sort_index()
    for (evidence_id, key), user_membership in membership.groupby(["evidence_id", "user_key022"], sort=True):
        profile_rows = user_membership.loc[user_membership["role"] == "PROFILE"].sort_values("position")
        profile_ids = profile_rows["movie_id"].astype(int).tolist()
        rating_rows = profile_lookup.loc[(evidence_id, key)]
        if isinstance(rating_rows, pd.Series):
            rating_rows = rating_rows.to_frame().T
        rating_rows = rating_rows.sort_values("position")
        if rating_rows["movie_id"].astype(int).tolist() != profile_ids:
            raise RuntimeError("profile alignment drift")
        ratings = rating_rows["rating"].to_numpy(dtype=np.float64)
        profile_positions = np.asarray([pos[movie] for movie in profile_ids])
        for cell in d["cells"]:
            encoding, k = str(cell["encoding"]), int(cell["k"])
            weights = encoding_weights(encoding, ratings[:k], g0_mid, tau=5.0)
            current_profile, current_active = _normalize(np.asarray(current[profile_positions[:k]].T @ weights).ravel().astype(np.float64))
            e5_profile, e5_active = _normalize(np.sum(e5[profile_positions[:k]] * weights[:, None], axis=0))
            seed_profiles: dict[int, tuple[np.ndarray, bool]] = {}
            for seed in SEEDS:
                actual = factors[seed][np.asarray([core_pos[movie] for movie in profile_ids[:k]])]
                seed_profiles[seed] = _normalize(np.sum(actual * weights[:, None], axis=0))
            for panel in range(4):
                for domain in DOMAINS:
                    slate = user_membership.loc[(user_membership["panel"] == panel) & (user_membership["role"] == domain)].sort_values("position")
                    movie_ids = slate["movie_id"].astype(int).tolist()
                    movie_positions = np.asarray([pos[movie] for movie in movie_ids])
                    head_scores: dict[str, tuple[np.ndarray, bool, dict[int, np.ndarray]]] = {}
                    head_scores["CURRENT_FULL"] = (np.asarray(current[movie_positions] @ current_profile).ravel().astype(np.float64), current_active, {})
                    head_scores["E5"] = (e5[movie_positions] @ e5_profile, e5_active, {})
                    seed_scores = {seed: np.asarray([float(predicted[(evidence_id, seed)][movie] @ seed_profiles[seed][0]) for movie in movie_ids]) for seed in SEEDS}
                    bpr_active = all(seed_profiles[seed][1] and np.isfinite(seed_scores[seed]).all() for seed in SEEDS)
                    head_scores["E5_TO_BPR"] = (np.mean(np.vstack([seed_scores[seed] for seed in SEEDS]), axis=0), bpr_active, seed_scores)
                    ties = [hashlib.sha256(f"rec-ev-026-rank-tie-v1|{evidence_id}|{key}|{panel}|{domain}|{encoding}|{k}|{movie}".encode("utf-8")).digest() for movie in movie_ids]
                    for head in HEADS:
                        scores, active, per_seed = head_scores[head]
                        order = sorted(range(4), key=lambda index: (-float(scores[index]) if active else 0.0, ties[index], movie_ids[index]))
                        ranks = {index: rank + 1 for rank, index in enumerate(order)}
                        for index, movie in enumerate(movie_ids):
                            row = {"evidence_id": evidence_id, "user_key022": key, "panel": panel, "domain": domain, "head": head, "encoding": encoding, "k": k, "movie_id": movie, "score": float(scores[index]) if active else 0.0, "rank": ranks[index], "active": bool(active)}
                            for seed in SEEDS:
                                row[f"seed_{seed}_score"] = float(per_seed[seed][index]) if head == "E5_TO_BPR" and active else np.nan
                            rows.append(row)
    frame = pd.DataFrame(rows).sort_values(["evidence_id", "user_key022", "panel", "domain", "head", "encoding", "k", "rank"], kind="stable", ignore_index=True)
    if _recompute:
        return frame
    atomic_parquet(destination, frame)
    write_integrity(integrity, {"ranks": destination}, meta)
    advance(contract, "ALL_HEAD_RANK_SEAL")
    return frame


def label_phase(contract: Mapping[str, Any], *, _recompute: bool = False) -> pd.DataFrame:
    phase = current_phase(contract)
    destination, integrity = output_path(contract, "labels"), output_path(contract, "label_integrity")
    meta = {"run_signature": signature(contract), "phase": "EVALUATION_LABEL_OPEN", "all_head_ranks_sealed": True, "timestamp_bytes_parsed": 0}
    if not _recompute and phase_state([destination, integrity]):
        verify_integrity(integrity, {"labels": destination}, meta)
        cached = pd.read_parquet(destination)
        expected = label_phase(contract, _recompute=True)
        try:
            pd.testing.assert_frame_equal(cached, expected, check_dtype=True, check_like=False)
        except AssertionError as error:
            raise ResumeError("label deterministic drift") from error
        advance(contract, "EVALUATION_LABEL_OPEN")
        return cached
    if not _recompute and PHASES[phase] != 30:
        raise ResumeError("label phase order drift")
    membership = pd.read_parquet(resolve(contract["allowed_input_artifacts"]["preflight_membership"]["path"]))
    selected = membership.loc[membership["role"].isin(DOMAINS), ["evidence_id", "user_key022", "role", "movie_id"]].drop_duplicates()
    keys = sorted(selected["user_key022"].unique())
    reverse = raw_user_map(keys, int(contract["reader"]["maximum_user_id"]))
    histories = scan_ratings(contract, reverse, None)
    q_lookup: dict[tuple[str, int], tuple[float, float]] = {}
    for key, rows in histories.items():
        movies = [movie for movie, _rating in rows]
        ratings = np.asarray([rating for _movie, rating in rows], dtype=np.float64)
        q = full_history_mid_percentiles(ratings)
        q_lookup.update({(key, int(movie)): (float(rating), float(value)) for movie, rating, value in zip(movies, ratings, q)})
    if not set(zip(selected["user_key022"].astype(str), selected["movie_id"].astype(int))) <= set(q_lookup):
        raise RuntimeError("evaluation label allowlist incomplete")
    selected["rating"] = [q_lookup[(str(key), int(movie))][0] for key, movie in zip(selected["user_key022"], selected["movie_id"])]
    selected["q_eval"] = [q_lookup[(str(key), int(movie))][1] for key, movie in zip(selected["user_key022"], selected["movie_id"])]
    frame = selected.sort_values(["evidence_id", "user_key022", "role", "movie_id"], kind="stable", ignore_index=True)
    if _recompute:
        return frame
    atomic_parquet(destination, frame)
    write_integrity(integrity, {"labels": destination}, meta)
    advance(contract, "EVALUATION_LABEL_OPEN")
    return frame


def analytic_random(values: Sequence[float]) -> tuple[float, float]:
    pairs = list(itertools.combinations(np.asarray(values, dtype=np.float64), 2))
    return float(np.mean([np.mean(pair) for pair in pairs])), float(np.mean([1.0 - np.min(pair) for pair in pairs]))


def panel_metrics(contract: Mapping[str, Any], ranks: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    q = labels.set_index(["evidence_id", "user_key022", "role", "movie_id"])["q_eval"]
    rows = []
    group_columns = ["evidence_id", "user_key022", "panel", "domain", "head", "encoding", "k"]
    for keys, slate in ranks.groupby(group_columns, sort=True):
        evidence_id, user, panel, domain, head, encoding, k = keys
        ordered = slate.sort_values("rank")
        values = np.asarray([q.loc[(evidence_id, user, domain, int(movie))] for movie in ordered["movie_id"]], dtype=np.float64)
        random_utility, random_loss = analytic_random(values)
        active = bool(ordered["active"].all())
        if active:
            model_utility, model_loss = float(values[:2].mean()), float(1.0 - values[:2].min())
        else:
            model_utility, model_loss = random_utility, random_loss
        rows.append({"evidence_id": evidence_id, "user_key022": user, "panel": int(panel), "domain": domain, "head": head, "encoding": encoding, "k": int(k), "model_utility": model_utility, "model_loss": model_loss, "random_utility": random_utility, "random_loss": random_loss, "utility_improvement": model_utility - random_utility, "safety_improvement": random_loss - model_loss, "active": active})
    return pd.DataFrame(rows)


def make_contrasts(contract: Mapping[str, Any], metrics: pd.DataFrame) -> pd.DataFrame:
    metadata = contrast_metadata(design(contract))
    indexed = metrics.set_index(["evidence_id", "user_key022", "panel", "domain", "head", "encoding", "k"])
    rows: list[dict[str, Any]] = []
    for meta in metadata:
        evidence_id = meta["experiment"]
        users = sorted(metrics.loc[metrics["evidence_id"] == evidence_id, "user_key022"].unique())
        for user in users:
            panel_values = []
            for panel in range(4):
                if meta["kind"] == "ABSOLUTE":
                    head, encoding, k = meta["head"], meta["encoding"], meta["k"]
                    endpoint = "utility_improvement" if meta["endpoint"].startswith("UTILITY") else "safety_improvement"
                    target = float(indexed.loc[(evidence_id, user, panel, "TARGET", head, encoding, k)][endpoint])
                    control = float(indexed.loc[(evidence_id, user, panel, "CONTROL", head, encoding, k)][endpoint])
                    value = target if meta["class"] == "TARGET_IMPROVEMENT" else control if meta["class"] == "CONTROL_IMPROVEMENT" else target - control
                else:
                    encoding, k, domain = meta["encoding"], meta["k"], meta["domain"]
                    challenger = indexed.loc[(evidence_id, user, panel, domain, "E5_TO_BPR", encoding, k)]
                    baseline = indexed.loc[(evidence_id, user, panel, domain, meta["baseline"], encoding, k)]
                    value = float(challenger.model_utility - baseline.model_utility) if meta["endpoint"].startswith("UTILITY") else float(baseline.model_loss - challenger.model_loss)
                panel_values.append(value)
            rows.append({"contrast_index": meta["index"], "evidence_id": evidence_id, "user_key022": user, "value": float(np.mean(panel_values))})
    return pd.DataFrame(rows).sort_values(["contrast_index", "user_key022"], kind="stable", ignore_index=True)


def poisson_cutoffs() -> list[int]:
    with localcontext() as context:
        context.prec = 80
        term = (-Decimal(1)).exp()
        cdf = term
        result = []
        for k in range(64):
            result.append(min(int((cdf * Decimal(2**64)).to_integral_value(rounding=ROUND_FLOOR)), 2**64 - 1))
            if result[-1] == 2**64 - 1:
                return result
            term /= Decimal(k + 1)
            cdf += term
    raise RuntimeError("Poisson cutoff construction failed")


def bootstrap(contract: Mapping[str, Any], contrasts: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    keys = sorted(contrasts["user_key022"].unique())
    key_pos = {key: index for index, key in enumerate(keys)}
    matrix = np.full((len(keys), 312), np.nan, dtype=np.float64)
    for row in contrasts.itertuples(index=False):
        matrix[key_pos[row.user_key022], int(row.contrast_index)] = float(row.value)
    observed = np.isfinite(matrix)
    point = np.nansum(matrix, axis=0) / observed.sum(axis=0)
    cutoffs = poisson_cutoffs()
    d = design(contract)
    for fixture in d["statistics"]["bootstrap_golden_fixtures"]:
        value = bootstrap_uint64(int(fixture["attempt"]), str(fixture["user_key022"]))
        if value != int(fixture["uint64"]) or bisect.bisect_left(cutoffs, value) != int(fixture["weight"]):
            raise RuntimeError("bootstrap golden drift")
    estimates, valid, invalid = [], [], []
    for attempt in range(8000):
        weights = np.asarray([bisect.bisect_left(cutoffs, bootstrap_uint64(attempt, key)) for key in keys], dtype=np.float64)
        denominators = weights @ observed
        if bool((denominators <= 0).any()):
            invalid.append(attempt)
            continue
        estimate = (weights @ np.nan_to_num(matrix, nan=0.0)) / denominators
        if not np.isfinite(estimate).all():
            invalid.append(attempt)
            continue
        estimates.append(estimate)
        valid.append(attempt)
        if len(estimates) == 4000:
            break
    if len(estimates) != 4000:
        raise RuntimeError("insufficient valid bootstrap attempts")
    return point, np.vstack(estimates), np.asarray(valid, dtype=np.int32), np.asarray(invalid, dtype=np.int32)


def nearest_rank(values: np.ndarray, probability: float) -> float:
    ordered = np.sort(np.asarray(values, dtype=np.float64))
    return float(ordered[max(0, math.ceil(probability * len(ordered)) - 1)])


def decision(contract: Mapping[str, Any], metrics: pd.DataFrame, intervals: list[dict[str, Any]]) -> dict[str, Any]:
    d = design(contract)
    by_index = {row["index"]: row for row in intervals}
    meta = contrast_metadata(d)
    lookup = {(row["experiment"], row["kind"], row.get("head"), row.get("baseline"), row["encoding"], row["k"], row.get("class"), row.get("domain"), row["endpoint"]): by_index[row["index"]] for row in meta}
    panel = metrics.groupby(["evidence_id", "panel", "domain", "head", "encoding", "k"])[["utility_improvement", "safety_improvement", "model_utility", "model_loss"]].mean()
    absolute_truth, incremental_truth = [], []
    for evidence_id in ("REC-EV-026A", "REC-EV-026B"):
        for head in HEADS:
            for cell in d["cells"]:
                encoding, k = cell["encoding"], cell["k"]
                target_rows = [lookup[(evidence_id, "ABSOLUTE", head, None, encoding, k, "TARGET_IMPROVEMENT", None, endpoint)] for endpoint in d["metrics"]["absolute_endpoints"]]
                gap_rows = [lookup[(evidence_id, "ABSOLUTE", head, None, encoding, k, "CONDITIONAL_GAP", None, endpoint)] for endpoint in d["metrics"]["absolute_endpoints"]]
                target_points, gap_points = [], []
                for endpoint, column in zip(d["metrics"]["absolute_endpoints"], ("utility_improvement", "safety_improvement")):
                    for p in range(4):
                        target_value = float(panel.loc[(evidence_id, p, "TARGET", head, encoding, k)][column])
                        control_value = float(panel.loc[(evidence_id, p, "CONTROL", head, encoding, k)][column])
                        target_points.append(target_value)
                        gap_points.append(target_value - control_value)
                target_pass = all(row["estimable"] and row["low"] >= 0.02 and row["half_width"] < 0.05 for row in target_rows) and all(value > 0 for value in target_points)
                gap_pass = all(row["estimable"] and row["low"] >= -0.02 and row["half_width"] < 0.05 for row in gap_rows) and all(value >= -0.02 for value in gap_points)
                absolute_truth.append({"evidence_id": evidence_id, "head": head, "encoding": encoding, "k": k, "target_pass": target_pass, "gap_pass": gap_pass, "target_panel_points": target_points, "gap_panel_points": gap_points})
        for baseline in ("CURRENT_FULL", "E5"):
            for cell in d["cells"]:
                encoding, k = cell["encoding"], cell["k"]
                target_rows = [lookup[(evidence_id, "INCREMENTAL", None, baseline, encoding, k, None, "TARGET", endpoint)] for endpoint in d["metrics"]["incremental_endpoints"]]
                control_rows = [lookup[(evidence_id, "INCREMENTAL", None, baseline, encoding, k, None, "CONTROL", endpoint)] for endpoint in d["metrics"]["incremental_endpoints"]]
                target_utility_points, target_safety_points, control_points = [], [], []
                for p in range(4):
                    candidate_t = panel.loc[(evidence_id, p, "TARGET", "E5_TO_BPR", encoding, k)]
                    baseline_t = panel.loc[(evidence_id, p, "TARGET", baseline, encoding, k)]
                    target_utility_points.append(float(candidate_t.model_utility - baseline_t.model_utility))
                    target_safety_points.append(float(baseline_t.model_loss - candidate_t.model_loss))
                    candidate_c = panel.loc[(evidence_id, p, "CONTROL", "E5_TO_BPR", encoding, k)]
                    baseline_c = panel.loc[(evidence_id, p, "CONTROL", baseline, encoding, k)]
                    control_points.extend([float(candidate_c.model_utility - baseline_c.model_utility), float(baseline_c.model_loss - candidate_c.model_loss)])
                target_pass = target_rows[0]["estimable"] and target_rows[1]["estimable"] and target_rows[0]["low"] >= 0.005 and target_rows[1]["low"] >= 0.01 and all(row["half_width"] < 0.05 for row in target_rows) and all(value >= 0.005 for value in target_utility_points) and all(value >= 0.01 for value in target_safety_points)
                control_pass = all(row["estimable"] and row["low"] >= -0.02 and row["half_width"] < 0.05 for row in control_rows) and all(value >= -0.02 for value in control_points)
                incremental_truth.append({"evidence_id": evidence_id, "baseline": baseline, "encoding": encoding, "k": k, "target_pass": target_pass, "control_pass": control_pass, "target_utility_panel_points": target_utility_points, "target_safety_panel_points": target_safety_points, "control_panel_points": control_points})
    precise = all(row["estimable"] and all(math.isfinite(float(row[key])) for key in ("mean", "se", "low", "high", "half_width")) and row["se"] > 0 and row["half_width"] < 0.05 for row in intervals)
    domain_robust = {}
    for evidence_id in ("REC-EV-026A", "REC-EV-026B"):
        abs_ok = all(row["target_pass"] and row["gap_pass"] for row in absolute_truth if row["evidence_id"] == evidence_id and row["head"] == "E5_TO_BPR")
        inc_ok = all(row["target_pass"] and row["control_pass"] for row in incremental_truth if row["evidence_id"] == evidence_id)
        domain_robust[evidence_id] = abs_ok and inc_ok
    cell_signal = any(row["target_pass"] and row["gap_pass"] for row in absolute_truth) or any(row["target_pass"] and row["control_pass"] for row in incremental_truth)
    status = "INCONCLUSIVE_PRECISION_OR_NONESTIMABLE" if not precise else "ROBUST_ALIGNMENT_SIGNAL" if all(domain_robust.values()) else "DOMAIN_SPECIFIC_ALIGNMENT_SIGNAL_NOT_GLOBAL" if any(domain_robust.values()) else "CELL_SPECIFIC_SIGNAL_NOT_ROBUST" if cell_signal else "NO_ROBUST_ALIGNMENT_SIGNAL"
    return {"status": status, "precision_or_estimability_failure": not precise, "domain_robust": domain_robust, "absolute_cell_truth": absolute_truth, "incremental_cell_truth": incremental_truth}


def analyze_phase(contract: Mapping[str, Any], *, _recompute: bool = False) -> dict[str, Any]:
    phase = current_phase(contract)
    paths = {key: output_path(contract, key) for key in ("panel_metrics", "user_contrasts", "bootstrap", "result", "selection", "result_integrity")}
    meta_integrity = {"run_signature": signature(contract), "phase": "METRICS_BOOTSTRAP_RESULT_SEAL", "contrasts": 312, "valid_replicates": 4000, "timestamp_bytes_parsed": 0, "locked_test_opened": False, "final_reserve_opened": False, "champion": None}
    if not _recompute and phase_state(list(paths.values())):
        verify_integrity(paths["result_integrity"], {key: value for key, value in paths.items() if key != "result_integrity"}, meta_integrity)
        expected = analyze_phase(contract, _recompute=True)
        try:
            pd.testing.assert_frame_equal(pd.read_parquet(paths["panel_metrics"]), expected["panel_metrics"], check_dtype=True, check_like=False)
            pd.testing.assert_frame_equal(pd.read_parquet(paths["user_contrasts"]), expected["user_contrasts"], check_dtype=True, check_like=False)
        except AssertionError as error:
            raise ResumeError("analysis deterministic frame drift") from error
        with np.load(paths["bootstrap"], allow_pickle=False) as cached:
            if cached.files != ["point", "replicates", "valid_attempt_ids", "invalid_attempt_ids"] or not all(np.array_equal(cached[key], expected[key]) for key in cached.files):
                raise ResumeError("analysis deterministic bootstrap drift")
        if read_json(paths["selection"]) != expected["selection"] or read_json(paths["result"]) != expected["result"]:
            raise ResumeError("analysis deterministic result drift")
        advance(contract, "METRICS_BOOTSTRAP_RESULT_SEAL")
        return read_json(paths["result"])
    if not _recompute and PHASES[phase] != 40:
        raise ResumeError("analysis phase order drift")
    ranks, labels = pd.read_parquet(output_path(contract, "ranks")), pd.read_parquet(output_path(contract, "labels"))
    metrics = panel_metrics(contract, ranks, labels)
    contrasts = make_contrasts(contract, metrics)
    point, replicates, valid, invalid = bootstrap(contract, contrasts)
    se = replicates.std(axis=0, ddof=1)
    estimable = np.isfinite(se) & (se > 0)
    critical = nearest_rank(np.max(np.abs((replicates[:, estimable] - point[estimable]) / se[estimable]), axis=1), 0.975) if bool(estimable.any()) else 0.0
    meta = contrast_metadata(design(contract))
    intervals = []
    for index in range(312):
        width = float(critical * se[index]) if estimable[index] else None
        intervals.append({**meta[index], "mean": float(point[index]), "se": float(se[index]) if np.isfinite(se[index]) else None, "estimable": bool(estimable[index]), "half_width": width, "low": float(point[index] - width) if width is not None else None, "high": float(point[index] + width) if width is not None else None})
    verdict = decision(contract, metrics, intervals)
    mapper_selection = read_json(output_path(contract, "mapper_selection"))
    auxiliary = metrics.groupby(["evidence_id", "domain", "head", "encoding", "k"])[["model_utility", "model_loss", "random_utility", "random_loss", "utility_improvement", "safety_improvement", "active"]].mean().reset_index().to_dict("records")
    seed_rows = []
    for keys, group in ranks.loc[ranks["head"] == "E5_TO_BPR"].groupby(["evidence_id", "domain", "encoding", "k"], sort=True):
        evidence_id, domain, encoding, k = keys
        for seed in SEEDS:
            values = group[f"seed_{seed}_score"].to_numpy(dtype=np.float64)
            seed_rows.append({"evidence_id": evidence_id, "domain": domain, "encoding": encoding, "k": int(k), "seed": seed, "mean_candidate_score": float(np.nanmean(values)), "std_candidate_score": float(np.nanstd(values, ddof=1))})
    selection = {"schema_version": 1, "evidence_id": "REC-EV-026", **verdict, "champion": None, "product_policy_updated": False, "locked_test_opened": False, "final_reserve_opened": False}
    result = {"schema_version": 1, "evidence_id": "REC-EV-026", "status": verdict["status"], "run_signature": signature(contract), "purpose": contract["purpose"], "claim_boundary": contract["result_boundary"]["allowed"], "users": {key: int(value) for key, value in metrics.groupby("evidence_id")["user_key022"].nunique().to_dict().items()}, "panels_per_user": 4, "slate_n": 4, "top_n": 2, "mapper_selection": mapper_selection, "selection": selection, "simultaneous_intervals": intervals, "critical_value_97_5_percent": critical, "bootstrap": {"valid_replicates": len(valid), "first_valid_attempt": int(valid[0]), "last_valid_attempt": int(valid[-1]), "invalid_attempts": len(invalid), "contrasts": 312, "shared_union_user_weight": True}, "auxiliary": auxiliary, "seed_score_stability_descriptive_only": seed_rows, "timestamp_bytes_parsed": 0, "locked_test_opened": False, "final_reserve_opened": False, "product_policy_updated": False, "champion": None}
    if _recompute:
        return {"panel_metrics": metrics, "user_contrasts": contrasts, "point": point, "replicates": replicates, "valid_attempt_ids": valid, "invalid_attempt_ids": invalid, "selection": selection, "result": result}
    atomic_parquet(paths["panel_metrics"], metrics)
    atomic_parquet(paths["user_contrasts"], contrasts)
    atomic_npz(paths["bootstrap"], point=point, replicates=replicates, valid_attempt_ids=valid, invalid_attempt_ids=invalid)
    atomic_write_json(paths["selection"], selection)
    atomic_write_json(paths["result"], result)
    write_integrity(paths["result_integrity"], {key: value for key, value in paths.items() if key != "result_integrity"}, meta_integrity)
    advance(contract, "METRICS_BOOTSTRAP_RESULT_SEAL")
    return result


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    validate(value)
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT)
    parser.add_argument("--phase", choices=("lock", "mapper", "profile", "rank", "evaluation", "analyze", "run"), required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.contract.resolve() != DEFAULT.resolve():
        raise RuntimeError("only default execution contract accepted")
    contract = load_contract(args.contract)
    if args.phase == "lock":
        if args.resume:
            raise ResumeError("lock creation does not accept --resume")
        create_or_verify_lock(contract, False)
        print("REC_EV_026_EXECUTION_LOCKED")
        return 0
    if not args.resume:
        raise ResumeError("post-lock execution requires --resume")
    create_or_verify_lock(contract, True)
    if args.phase in {"mapper", "profile", "rank", "evaluation", "analyze", "run"}:
        fit_mapper(contract)
    if args.phase in {"profile", "rank", "evaluation", "analyze", "run"}:
        profile_phase(contract)
    if args.phase in {"rank", "evaluation", "analyze", "run"}:
        rank_phase(contract)
    if args.phase in {"evaluation", "analyze", "run"}:
        label_phase(contract)
    result = analyze_phase(contract) if args.phase in {"analyze", "run"} else {"status": read_json(output_path(contract, "progress"))["phase"]}
    print(json.dumps({"status": result["status"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
