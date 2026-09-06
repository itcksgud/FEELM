"""Prepare REC-EV-029 profile ratings and seal every candidate before labels."""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, Mapping, Sequence
import zipfile

import numpy as np
import pandas as pd
from scipy import sparse

from build_rec_ev_019b_features import sha256_file
from preflight_rec_ev_029_membership import EXPECTED_RATING_ROWS, MAX_USER_ID, phase_bucket, split_bucket
from rec_ev_022a_core import build_structured_full, old_user_bucket, user_key, user_role_bucket
from rec_ev_027_core import rating_index, user_equal_prior
from rec_ev_029_core import candidate_grid, score_profile_grid


ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "docs/recommendation/contracts/rec-ev-029a-prelabel-execution.json"
OUTERS = ("R0", "R1", "R2", "R3", "R4", "KR", "RECENT")
COHORTS = ("SELECTION", "REPLICATION")


class ResumeError(RuntimeError):
    pass


def artifact(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    try:
        display = resolved.relative_to(ROOT).as_posix()
    except ValueError:
        display = resolved.as_posix()
    return {"path": display, "bytes": resolved.stat().st_size, "sha256": sha256_file(resolved)}


def verify(spec: Mapping[str, Any]) -> Path:
    raw = Path(str(spec["path"]))
    path = raw if raw.is_absolute() else ROOT / raw
    if (
        not path.is_file()
        or path.stat().st_size != int(spec["bytes"])
        or sha256_file(path) != str(spec["sha256"])
    ):
        raise ResumeError(f"artifact drift: {path}")
    return path.resolve()


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def atomic_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    frame.to_parquet(temporary, index=False)
    os.replace(temporary, path)


def atomic_npz(path: Path, **arrays: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        np.savez_compressed(handle, **arrays)
    os.replace(temporary, path)


def progress(path: Path, phase: str, **values: Any) -> None:
    payload = {"phase": phase, "updated_at_unix": time.time(), **values}
    atomic_json(path, payload)
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _zip_member(bundle: zipfile.ZipFile, suffix: str) -> str:
    values = [name for name in bundle.namelist() if name.endswith(suffix)]
    if len(values) != 1:
        raise RuntimeError(f"archive member ambiguity: {suffix}")
    return values[0]


def _parse_user(raw: bytes) -> tuple[int, int]:
    first = raw.find(b",")
    if first <= 0:
        raise RuntimeError("malformed row before user firewall")
    return int(raw[:first]), first


def _parse_movie(raw: bytes, first: int) -> tuple[int, int]:
    second = raw.find(b",", first + 1)
    if second <= first + 1:
        raise RuntimeError("malformed movie field")
    return int(raw[first + 1 : second]), second


def _parse_rating(raw: bytes, second: int) -> int:
    third = raw.find(b",", second + 1)
    if third <= second + 1:
        raise RuntimeError("malformed rating field")
    return rating_index(raw[second + 1 : third])


def paths(contract: Mapping[str, Any]) -> dict[str, Path]:
    root = ROOT / str(contract["output_root"])
    return {
        "root": root,
        "progress": root / "progress.json",
        "profiles": root / "prepared/profile-ratings.parquet",
        "prior": root / "prepared/calibration-prior.npz",
        "prepare_seal": root / "prepared/prepare-seal.json",
        "score_seal": root / "scores/global-score-seal.json",
    }


def score_paths(contract: Mapping[str, Any], cohort: str, outer: str) -> tuple[Path, Path]:
    root = ROOT / str(contract["output_root"]) / "scores" / cohort.lower() / outer
    return root / "top2-ranks.npz", root / "integrity.json"


def load_contract(path: Path) -> dict[str, Any]:
    from validate_rec_ev_029_prelabel import validate

    value = json.loads(path.read_text(encoding="utf-8"))
    validate(value, path.resolve())
    if value["implementation_audit"].get("status") != "REC_EV_029_PRELABEL_IMPLEMENTATION_AUDIT_PASS":
        raise ResumeError("independent exact implementation audit has not passed")
    return value


def load_membership(contract: Mapping[str, Any]) -> pd.DataFrame:
    verify(contract["inputs"]["membership_preflight"])
    frame = pd.read_parquet(verify(contract["inputs"]["membership"]))
    frame = frame.sort_values(["cohort", "outer", "user_key"], kind="stable", ignore_index=True)
    if (
        len(frame) != 21_904
        or frame.duplicated(["cohort", "outer", "user_key"]).any()
        or set(frame["cohort"].astype(str)) != set(COHORTS)
        or set(frame["outer"].astype(str)) != set(OUTERS)
        or bool((frame["profile_movie_ids"].map(len) != 30).any())
    ):
        raise ResumeError("membership semantic drift")
    for (cohort, outer), group in frame.groupby(["cohort", "outer"], sort=False, observed=True):
        keys = group["user_key"].astype(str).tolist()
        donors = group["donor_user_key"].astype(str).tolist()
        if len(set(keys)) != len(keys) or set(keys) != set(donors) or any(a == b for a, b in zip(keys, donors, strict=True)):
            raise ResumeError(f"donor bijection drift: {cohort} {outer}")
    target_union: dict[tuple[str, str], set[int]] = defaultdict(set)
    for row in frame.itertuples(index=False):
        target_union[(str(row.cohort), str(row.user_key))].update(map(int, row.target_movie_ids))
    for row in frame.itertuples(index=False):
        if set(map(int, row.profile_movie_ids)) & target_union[(str(row.cohort), str(row.user_key))]:
            raise ResumeError("global target/profile firewall drift")
    return frame


def reverse_evaluation_users(frame: pd.DataFrame, salt: str) -> dict[int, str]:
    by_key = {
        str(row.user_key): str(row.cohort)
        for row in frame[["user_key", "cohort"]].drop_duplicates().itertuples(index=False)
    }
    result: dict[int, str] = {}
    for uid in range(1, MAX_USER_ID + 1):
        key = user_key(uid)
        cohort = by_key.get(key)
        if cohort is None:
            continue
        bucket = split_bucket(uid, salt)
        expected = "SELECTION" if 8000 <= bucket <= 8999 else "REPLICATION" if 9000 <= bucket <= 9999 else None
        if (
            expected != cohort
            or old_user_bucket(uid) > 59
            or user_role_bucket(uid) > 5999
            or phase_bucket(uid) > 7999
        ):
            raise ResumeError("evaluation user split reversal drift")
        result[uid] = key
    if len(result) != len(by_key):
        raise ResumeError("evaluation pseudonymous key reversal coverage drift")
    return result


def assert_profiles_match_membership(membership: pd.DataFrame, profiles: pd.DataFrame) -> None:
    ordered_membership = membership.sort_values(
        ["cohort", "outer", "user_key"], kind="stable", ignore_index=True
    )
    ordered_profiles = profiles.sort_values(
        ["cohort", "outer", "user_key"], kind="stable", ignore_index=True
    )
    scalar_columns = (
        "cohort",
        "outer",
        "track",
        "fold_or_domain",
        "user_key",
        "donor_user_key",
    )
    list_columns = ("profile_movie_ids", "target_movie_ids")
    if len(ordered_membership) != len(ordered_profiles):
        raise ResumeError("prepared membership row count drift")
    for column in scalar_columns:
        if not np.array_equal(
            ordered_membership[column].astype(str).to_numpy(),
            ordered_profiles[column].astype(str).to_numpy(),
        ):
            raise ResumeError(f"prepared membership scalar drift: {column}")
    for column in list_columns:
        for expected, observed in zip(
            ordered_membership[column], ordered_profiles[column], strict=True
        ):
            if not np.array_equal(
                np.asarray(expected, dtype=np.int64), np.asarray(observed, dtype=np.int64)
            ):
                raise ResumeError(f"prepared membership list drift: {column}")


def validate_prepare(contract: Mapping[str, Any], p: Mapping[str, Path]) -> dict[str, Any]:
    membership = load_membership(contract)
    seal = json.loads(p["prepare_seal"].read_text(encoding="utf-8"))
    reader = seal.get("reader", {})
    if (
        seal.get("status") != "SEALED_CALIBRATION_PRIOR_AND_FROZEN_PROFILE30_RATINGS"
        or seal.get("contract") != artifact(DEFAULT)
        or verify(seal["profiles"]) != p["profiles"].resolve()
        or verify(seal["prior"]) != p["prior"].resolve()
        or int(seal.get("membership_rows", -1)) != 21_904
        or int(seal.get("profile_rating_slots", -1)) != 657_120
        or int(reader.get("raw_rows", -1)) != EXPECTED_RATING_ROWS
        or int(reader.get("profile_rating_slots_filled", -1)) != 657_120
        or int(reader.get("target_rating_values_parsed", -1)) != 0
        or int(reader.get("timestamps_parsed", -1)) != 0
        or int(reader.get("rec_ev_028_attribution_or_future_opened", -1)) != 0
        or any(
            seal.get(name) is not False
            for name in (
                "target_rating_values_opened",
                "timestamps_opened",
                "rec_ev_028_attribution_or_future_opened",
                "locked_test_opened",
                "final_reserve_opened",
                "product_policy_changed",
            )
        )
    ):
        raise ResumeError("prepare seal semantic drift")
    profiles = pd.read_parquet(p["profiles"])
    assert_profiles_match_membership(membership, profiles)
    values = np.vstack(profiles["profile_rating_indices"].map(lambda value: np.asarray(value, dtype=np.int8)))
    if len(profiles) != 21_904 or values.shape != (21_904, 30) or bool((values < 0).any()) or bool((values > 9).any()):
        raise ResumeError("prepared profile rating drift")
    with np.load(p["prior"], allow_pickle=False) as prior:
        pi0 = prior["pi0"].astype(np.float64)
        g0 = prior["g0_mid"].astype(np.float64)
        if (
            pi0.shape != (10,)
            or g0.shape != (10,)
            or not np.isclose(pi0.sum(), 1.0)
            or not np.allclose(g0, np.cumsum(pi0) - 0.5 * pi0)
            or int(prior["calibration_users"]) != int(seal["calibration_users"])
            or int(prior["calibration_ratings"]) != int(seal["calibration_ratings"])
        ):
            raise ResumeError("calibration prior semantic drift")
    return seal


def prepare(contract: Mapping[str, Any], p: Mapping[str, Path], resume: bool) -> dict[str, Any]:
    if p["prepare_seal"].exists():
        if not resume:
            raise ResumeError("sealed preparation requires --resume")
        return validate_prepare(contract, p)
    if p["profiles"].exists() or p["prior"].exists():
        raise ResumeError("unsealed preparation artifact exists")
    membership = load_membership(contract)
    salt = str(contract["protocol"]["split_salt"])
    raw_to_key = reverse_evaluation_users(membership, salt)
    row_by_key: dict[str, list[int]] = defaultdict(list)
    profile_slots: dict[str, dict[int, list[tuple[int, int]]]] = defaultdict(lambda: defaultdict(list))
    target_union: dict[str, set[int]] = defaultdict(set)
    for row_index, row in enumerate(membership.itertuples(index=False)):
        key = str(row.user_key)
        row_by_key[key].append(row_index)
        for slot, movie in enumerate(row.profile_movie_ids):
            profile_slots[key][int(movie)].append((row_index, slot))
        target_union[key].update(map(int, row.target_movie_ids))
    profile_ratings = np.full((len(membership), 30), -1, dtype=np.int8)
    old = np.fromiter((old_user_bucket(uid) <= 59 for uid in range(MAX_USER_ID + 1)), dtype=bool)
    role = np.fromiter((user_role_bucket(uid) for uid in range(MAX_USER_ID + 1)), dtype=np.uint16)
    phase = np.fromiter((phase_bucket(uid) for uid in range(MAX_USER_ID + 1)), dtype=np.uint16)
    split = np.fromiter((split_bucket(uid, salt) for uid in range(MAX_USER_ID + 1)), dtype=np.uint16)
    calibration_hist = np.zeros((MAX_USER_ID + 1, 10), dtype=np.uint32)
    counters = {
        "raw_rows": 0,
        "discarded_after_user_id": 0,
        "calibration_rating_values_parsed": 0,
        "profile_rating_slots_filled": 0,
        "target_rows_seen_not_parsed": 0,
        "target_rating_values_parsed": 0,
        "timestamps_parsed": 0,
        "rec_ev_028_attribution_or_future_opened": 0,
    }
    archive = verify(contract["inputs"]["movielens_archive"])
    progress(p["progress"], "PREPARE_PROFILE_RATINGS", membership_rows=len(membership))
    with zipfile.ZipFile(archive) as bundle, bundle.open(_zip_member(bundle, "ratings.csv")) as handle:
        if handle.readline().rstrip(b"\r\n") != b"userId,movieId,rating,timestamp":
            raise RuntimeError("MovieLens header drift")
        for raw in handle:
            counters["raw_rows"] += 1
            uid, first = _parse_user(raw)
            calibration = (
                0 < uid <= MAX_USER_ID
                and bool(old[uid])
                and int(role[uid]) <= 5999
                and int(phase[uid]) <= 7999
                and int(split[uid]) <= 7999
            )
            key = raw_to_key.get(uid)
            if not calibration and key is None:
                counters["discarded_after_user_id"] += 1
                continue
            movie, second = _parse_movie(raw, first)
            locations = profile_slots[key].get(movie, ()) if key is not None else ()
            if key is not None and movie in target_union[key]:
                if locations:
                    raise RuntimeError("target/profile intersection reached rating reader")
                counters["target_rows_seen_not_parsed"] += 1
            if not calibration and not locations:
                continue
            index = _parse_rating(raw, second)
            if calibration:
                calibration_hist[uid, index] += 1
                counters["calibration_rating_values_parsed"] += 1
            for row_index, slot in locations:
                if profile_ratings[row_index, slot] >= 0:
                    raise RuntimeError("duplicate profile rating slot")
                profile_ratings[row_index, slot] = index
                counters["profile_rating_slots_filled"] += 1
    if (
        counters["raw_rows"] != EXPECTED_RATING_ROWS
        or bool((profile_ratings < 0).any())
        or counters["profile_rating_slots_filled"] != len(membership) * 30
        or counters["target_rating_values_parsed"]
        or counters["timestamps_parsed"]
        or counters["rec_ev_028_attribution_or_future_opened"]
    ):
        raise RuntimeError("pre-label rating firewall or coverage failure")
    active_hist = calibration_hist[calibration_hist.sum(axis=1) > 0]
    pi0, g0_mid = user_equal_prior(active_hist)
    atomic_npz(
        p["prior"],
        pi0=pi0.astype(np.float64),
        g0_mid=g0_mid.astype(np.float64),
        calibration_users=np.asarray(len(active_hist), dtype=np.int64),
        calibration_ratings=np.asarray(int(active_hist.sum()), dtype=np.int64),
    )
    prepared = membership.copy()
    prepared["profile_rating_indices"] = [row.tolist() for row in profile_ratings]
    atomic_parquet(p["profiles"], prepared)
    seal = {
        "status": "SEALED_CALIBRATION_PRIOR_AND_FROZEN_PROFILE30_RATINGS",
        "contract": artifact(DEFAULT),
        "membership": artifact(verify(contract["inputs"]["membership"])),
        "profiles": artifact(p["profiles"]),
        "prior": artifact(p["prior"]),
        "membership_rows": len(prepared),
        "profile_rating_slots": int(profile_ratings.size),
        "calibration_users": len(active_hist),
        "calibration_ratings": int(active_hist.sum()),
        "reader": counters,
        "target_rating_values_opened": False,
        "timestamps_opened": False,
        "rec_ev_028_attribution_or_future_opened": False,
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "product_policy_changed": False,
    }
    atomic_json(p["prepare_seal"], seal)
    return validate_prepare(contract, p)


def common_features(contract: Mapping[str, Any]) -> tuple[np.ndarray, sparse.csr_matrix, np.ndarray]:
    structured_frame = pd.read_parquet(verify(contract["inputs"]["structured_features"]))
    embedding_frame = pd.read_parquet(verify(contract["inputs"]["e5_embeddings"]))
    parent = json.loads(verify(contract["inputs"]["parent_contract"]).read_text(encoding="utf-8"))
    candidates = structured_frame.loc[structured_frame["feature_eligible"], "movie_id"].astype(int).to_numpy()
    first = build_structured_full(structured_frame, candidates)
    norms = np.sqrt(np.asarray(first.multiply(first).sum(axis=1)).ravel())
    structured_ids = set(map(int, candidates[norms > 0]))
    e5_by_movie: dict[int, np.ndarray] = {}
    for row in embedding_frame.itertuples(index=False):
        vector = np.asarray(row.embedding, dtype=np.float32)
        if (
            bool(row.feature_eligible)
            and row.model_id == parent["item_universe"]["e5_model_id"]
            and row.model_revision == parent["item_universe"]["e5_revision"]
            and vector.shape == (384,)
            and bool(np.isfinite(vector).all())
            and abs(float(np.linalg.norm(vector)) - 1.0) <= 0.0001
        ):
            e5_by_movie[int(row.movie_id)] = vector
    item_ids = np.asarray(sorted(structured_ids & set(e5_by_movie)), dtype=np.int64)
    structured = build_structured_full(structured_frame, item_ids).tocsr().astype(np.float32)
    e5 = np.vstack([e5_by_movie[int(movie)] for movie in item_ids]).astype(np.float32)
    if len(item_ids) != 85_517 or structured.shape[0] != len(item_ids) or e5.shape != (len(item_ids), 384):
        raise RuntimeError("common content universe drift")
    return item_ids, structured, e5


def _validate_top2(top2: np.ndarray, active: np.ndarray, target_n: int) -> bool:
    if top2.shape[:2] != active.shape or top2.shape[2:] != (2,):
        return False
    for values, ok in zip(top2.reshape(-1, 2), active.ravel(), strict=True):
        if ok:
            if len(set(map(int, values))) != 2 or bool((values < 0).any()) or bool((values >= target_n).any()):
                return False
        elif not bool((values == -1).all()):
            return False
    return True


def validate_score_bundle(path: Path, membership: pd.DataFrame, cohort: str, outer: str) -> None:
    local = membership.loc[membership["cohort"].eq(cohort) & membership["outer"].eq(outer)].sort_values("user_key", kind="stable")
    target_n = 20 if outer.startswith("R") and outer != "RECENT" else 4
    with np.load(path, allow_pickle=False) as bundle:
        required = {"user_keys", "target_movie_ids", "candidate_ids", "own_top2", "own_active", "shuffle_top2", "shuffle_active"}
        if set(bundle.files) != required:
            raise ResumeError(f"score bundle arrays drift: {cohort} {outer}")
        candidates = np.asarray([value.candidate_id for value in candidate_grid()])
        if (
            not np.array_equal(bundle["user_keys"], local["user_key"].astype(str).to_numpy())
            or not np.array_equal(bundle["candidate_ids"], candidates)
            or bundle["target_movie_ids"].shape != (len(local), target_n)
            or not np.array_equal(bundle["target_movie_ids"], np.vstack(local["target_movie_ids"]).astype(np.int32))
            or not _validate_top2(bundle["own_top2"], bundle["own_active"], target_n)
            or not _validate_top2(bundle["shuffle_top2"], bundle["shuffle_active"], target_n)
        ):
            raise ResumeError(f"score bundle semantic drift: {cohort} {outer}")


def validate_cell_integrity(
    integrity: Mapping[str, Any],
    *,
    p: Mapping[str, Path],
    cohort: str,
    outer: str,
    bundle_path: Path,
) -> None:
    if (
        integrity.get("status") != "SEALED_ALL_90_OWN_AND_PAIRED_SHUFFLE_TOP2"
        or integrity.get("cohort") != cohort
        or integrity.get("outer") != outer
        or int(integrity.get("candidate_count", -1)) != 90
        or verify(integrity["bundle"]) != bundle_path.resolve()
        or integrity.get("prepare_seal") != artifact(p["prepare_seal"])
        or any(
            integrity.get(name) is not False
            for name in (
                "target_rating_values_opened",
                "timestamps_opened",
                "locked_test_opened",
                "final_reserve_opened",
                "product_policy_changed",
            )
        )
    ):
        raise ResumeError(f"score integrity drift: {cohort} {outer}")


def score_one(
    contract: Mapping[str, Any],
    p: Mapping[str, Path],
    profiles: pd.DataFrame,
    cohort: str,
    outer: str,
    item_ids: np.ndarray,
    structured: sparse.csr_matrix,
    e5: np.ndarray,
    g0_mid: np.ndarray,
    resume: bool,
) -> dict[str, Any]:
    bundle_path, integrity_path = score_paths(contract, cohort, outer)
    local = profiles.loc[profiles["cohort"].eq(cohort) & profiles["outer"].eq(outer)].sort_values("user_key", kind="stable", ignore_index=True)
    if bundle_path.exists() and integrity_path.exists():
        if not resume:
            raise ResumeError(f"sealed score cell requires --resume: {cohort} {outer}")
        integrity = json.loads(integrity_path.read_text(encoding="utf-8"))
        validate_cell_integrity(
            integrity,
            p=p,
            cohort=cohort,
            outer=outer,
            bundle_path=bundle_path,
        )
        validate_score_bundle(bundle_path, profiles, cohort, outer)
        return integrity
    if bundle_path.exists() or integrity_path.exists():
        raise ResumeError(f"partial score cell exists: {cohort} {outer}")
    lookup = np.full(int(item_ids.max()) + 1, -1, dtype=np.int32)
    lookup[item_ids] = np.arange(len(item_ids), dtype=np.int32)
    position_by_key = {str(key): index for index, key in enumerate(local["user_key"].astype(str))}
    candidates = candidate_grid()
    own_top2 = np.full((len(local), len(candidates), 2), -1, dtype=np.int8)
    shuffle_top2 = np.full_like(own_top2, -1)
    own_active = np.zeros((len(local), len(candidates)), dtype=bool)
    shuffle_active = np.zeros_like(own_active)
    for row_number, row in enumerate(local.itertuples(index=False), start=1):
        target_movies = np.asarray(row.target_movie_ids, dtype=np.int64)
        target_positions = lookup[target_movies]
        own_movies = np.asarray(row.profile_movie_ids, dtype=np.int64)
        own_positions = lookup[own_movies]
        donor_row = local.iloc[position_by_key[str(row.donor_user_key)]]
        donor_movies = np.asarray(donor_row["profile_movie_ids"], dtype=np.int64)
        donor_positions = lookup[donor_movies]
        if bool((target_positions < 0).any()) or bool((own_positions < 0).any()) or bool((donor_positions < 0).any()):
            raise RuntimeError("membership movie outside content universe")
        own_structured = (structured[target_positions] @ structured[own_positions].T).toarray().astype(np.float64)
        own_e5 = e5[target_positions].astype(np.float64) @ e5[own_positions].astype(np.float64).T
        donor_structured = (structured[target_positions] @ structured[donor_positions].T).toarray().astype(np.float64)
        donor_e5 = e5[target_positions].astype(np.float64) @ e5[donor_positions].astype(np.float64).T
        top, active = score_profile_grid(
            own_structured,
            own_e5,
            row.profile_rating_indices,
            g0_mid,
            target_movies,
            cohort=cohort,
            outer=outer,
            side="OWN",
            user_key=str(row.user_key),
        )
        own_top2[row_number - 1], own_active[row_number - 1] = top, active
        top, active = score_profile_grid(
            donor_structured,
            donor_e5,
            donor_row["profile_rating_indices"],
            g0_mid,
            target_movies,
            cohort=cohort,
            outer=outer,
            side="SHUFFLE",
            user_key=str(row.user_key),
        )
        shuffle_top2[row_number - 1], shuffle_active[row_number - 1] = top, active
        if row_number % 250 == 0:
            progress(p["progress"], "SCORE_CELL", cohort=cohort, outer=outer, complete=row_number, total=len(local))
    atomic_npz(
        bundle_path,
        user_keys=local["user_key"].astype(str).to_numpy(dtype="U64"),
        target_movie_ids=np.vstack(local["target_movie_ids"]).astype(np.int32),
        candidate_ids=np.asarray([value.candidate_id for value in candidates]),
        own_top2=own_top2,
        own_active=own_active,
        shuffle_top2=shuffle_top2,
        shuffle_active=shuffle_active,
    )
    validate_score_bundle(bundle_path, profiles, cohort, outer)
    integrity = {
        "status": "SEALED_ALL_90_OWN_AND_PAIRED_SHUFFLE_TOP2",
        "cohort": cohort,
        "outer": outer,
        "rows": len(local),
        "candidate_count": len(candidates),
        "bundle": artifact(bundle_path),
        "prepare_seal": artifact(p["prepare_seal"]),
        "target_rating_values_opened": False,
        "timestamps_opened": False,
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "product_policy_changed": False,
    }
    atomic_json(integrity_path, integrity)
    return integrity


def validate_scores(contract: Mapping[str, Any], p: Mapping[str, Path]) -> dict[str, Any]:
    validate_prepare(contract, p)
    profiles = pd.read_parquet(p["profiles"])
    seal = json.loads(p["score_seal"].read_text(encoding="utf-8"))
    if (
        seal.get("status") != "GLOBAL_PRELABEL_TOP2_SCORE_SEAL_COMPLETE"
        or seal.get("contract") != artifact(DEFAULT)
        or seal.get("prepare_seal") != artifact(p["prepare_seal"])
        or int(seal.get("membership_rows", -1)) != 21_904
        or int(seal.get("candidate_count", -1)) != 90
        or len(seal.get("cell_integrities", [])) != 14
        or any(
            seal.get(name) is not False
            for name in (
                "selection_target_rating_values_opened",
                "replication_target_rating_values_opened",
                "timestamps_opened",
                "locked_test_opened",
                "final_reserve_opened",
                "product_policy_changed",
            )
        )
    ):
        raise ResumeError("global score seal semantic drift")
    observed = set()
    for spec in seal["cell_integrities"]:
        path = verify(spec)
        value = json.loads(path.read_text(encoding="utf-8"))
        key = (str(value.get("cohort")), str(value.get("outer")))
        if key in observed or key[0] not in COHORTS or key[1] not in OUTERS:
            raise ResumeError("score cell identity drift")
        bundle_path, expected_integrity = score_paths(contract, *key)
        if path != expected_integrity.resolve() or verify(value["bundle"]) != bundle_path.resolve():
            raise ResumeError("score cell artifact drift")
        validate_cell_integrity(
            value,
            p=p,
            cohort=key[0],
            outer=key[1],
            bundle_path=bundle_path,
        )
        validate_score_bundle(bundle_path, profiles, *key)
        observed.add(key)
    if observed != {(cohort, outer) for cohort in COHORTS for outer in OUTERS}:
        raise ResumeError("score cell coverage drift")
    return seal


def score(contract: Mapping[str, Any], p: Mapping[str, Path], resume: bool) -> dict[str, Any]:
    if p["score_seal"].exists():
        if not resume:
            raise ResumeError("global score seal requires --resume")
        return validate_scores(contract, p)
    validate_prepare(contract, p)
    profiles = pd.read_parquet(p["profiles"]).sort_values(["cohort", "outer", "user_key"], kind="stable", ignore_index=True)
    with np.load(p["prior"], allow_pickle=False) as prior:
        g0_mid = prior["g0_mid"].astype(np.float64)
    item_ids, structured, e5 = common_features(contract)
    integrities = []
    for cohort in COHORTS:
        for outer in OUTERS:
            value = score_one(contract, p, profiles, cohort, outer, item_ids, structured, e5, g0_mid, resume)
            _, integrity_path = score_paths(contract, cohort, outer)
            integrities.append(artifact(integrity_path))
            progress(p["progress"], "SCORE_CELL_COMPLETE", cohort=cohort, outer=outer, rows=int(value["rows"]))
    seal = {
        "status": "GLOBAL_PRELABEL_TOP2_SCORE_SEAL_COMPLETE",
        "contract": artifact(DEFAULT),
        "prepare_seal": artifact(p["prepare_seal"]),
        "membership_rows": len(profiles),
        "candidate_count": len(candidate_grid()),
        "cell_integrities": integrities,
        "selection_target_rating_values_opened": False,
        "replication_target_rating_values_opened": False,
        "timestamps_opened": False,
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "product_policy_changed": False,
    }
    atomic_json(p["score_seal"], seal)
    return validate_scores(contract, p)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT)
    parser.add_argument("--phase", choices=("prepare", "score", "all"), default="all")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    contract = load_contract(args.contract)
    p = paths(contract)
    if args.phase in {"prepare", "all"}:
        prepare(contract, p, args.resume)
    if args.phase in {"score", "all"}:
        score(contract, p, args.resume)
    print(json.dumps({"status": "OK", "phase": args.phase}, ensure_ascii=False))


if __name__ == "__main__":
    main()
