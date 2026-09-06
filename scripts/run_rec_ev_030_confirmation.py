"""Run the frozen REC-EV-030 untouched future-user confirmation."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
import time
from typing import Any, Mapping
import zipfile

import numpy as np
import pandas as pd
from scipy import sparse

from build_rec_ev_019b_features import sha256_file
from preflight_rec_ev_028a_membership import common_sets, phase_bucket
from preflight_rec_ev_030_membership import EXPECTED_RATING_ROWS, MAX_USER_ID
from rec_ev_022a_core import build_structured_full, old_user_bucket, user_key, user_role_bucket
from rec_ev_027_core import full_history_q, profile_weights, rating_index, weighted_similarity_scores
from rec_ev_028b_core import ItemEstimand, item_multiway_multiplier_max_t, user_multiplier_max_t
from rec_ev_029_analysis import ITEM_METRICS, USER_METRICS, benefit, equal_movie_mean, item_contribution_arrays, user_metric_arrays
from rec_ev_029_core import ranked_order


ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "docs/recommendation/contracts/rec-ev-030b-confirmation-execution.json"
RANDOM_OUTERS = ("R0", "R1", "R2", "R3", "R4")
OUTERS = (*RANDOM_OUTERS, "KR", "RECENT")
SYSTEMS = ("OWN", "SHUFFLE", "RANDOM")
CONTRASTS = (("OWN", "RANDOM"), ("OWN", "SHUFFLE"))


class ResumeError(RuntimeError):
    pass


def artifact(path: Path) -> dict[str, Any]:
    path = path.resolve()
    try:
        display = path.relative_to(ROOT).as_posix()
    except ValueError:
        display = path.as_posix()
    return {"path": display, "bytes": path.stat().st_size, "sha256": sha256_file(path)}


def verify(spec: Mapping[str, Any]) -> Path:
    raw = Path(str(spec["path"]))
    path = raw if raw.is_absolute() else ROOT / raw
    if not path.is_file() or path.stat().st_size != int(spec["bytes"]) or sha256_file(path) != str(spec["sha256"]):
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


def paths(contract: Mapping[str, Any]) -> dict[str, Path]:
    root = ROOT / str(contract["output_root"])
    return {
        "root": root,
        "progress": root / "progress.json",
        "profiles": root / "prepared/profile-ratings.parquet",
        "prepare_seal": root / "prepared/prepare-seal.json",
        "score_seal": root / "scores/global-score-seal.json",
        "labels": root / "labels/target-labels.parquet",
        "label_seal": root / "labels/label-seal.json",
        "metric_seal": root / "metrics/metric-seal.json",
        "user_boot": root / "analysis/user-bootstrap.npz",
        "item_boot": root / "analysis/item-bootstrap.npz",
        "inference": root / "analysis/primary-inference.json",
        "descriptive": root / "analysis/descriptive-outers.json",
        "user_segments": root / "analysis/user-segments.parquet",
        "movie_segments": root / "analysis/movie-segments.parquet",
        "final": root / "final-analysis.json",
    }


def score_paths(contract: Mapping[str, Any], outer: str) -> tuple[Path, Path]:
    root = ROOT / str(contract["output_root"]) / "scores" / outer
    return root / "top2-ranks.npz", root / "integrity.json"


def metric_paths(contract: Mapping[str, Any], outer: str) -> tuple[Path, Path, Path]:
    root = ROOT / str(contract["output_root"]) / "metrics" / outer
    return root / "user-metrics.parquet", root / "item-occurrences.parquet", root / "integrity.json"


def load_contract(path: Path) -> dict[str, Any]:
    from validate_rec_ev_030_confirmation import validate

    contract = json.loads(path.read_text(encoding="utf-8"))
    validate(contract, path.resolve())
    if contract["implementation_audit"].get("status") != "REC_EV_030_CONFIRMATION_IMPLEMENTATION_AUDIT_PASS":
        raise ResumeError("independent confirmation implementation audit has not passed")
    return contract


def load_membership(contract: Mapping[str, Any]) -> pd.DataFrame:
    preflight = json.loads(verify(contract["inputs"]["membership_preflight"]).read_text(encoding="utf-8"))
    if preflight.get("status") != "REC_EV_030_MEMBERSHIP_PASS" or preflight.get("rating_values_parsed") is not False:
        raise ResumeError("future membership preflight drift")
    audit = json.loads(verify(contract["inputs"]["membership_audit"]).read_text(encoding="utf-8"))
    if audit.get("status") != "REC_EV_030_MEMBERSHIP_RESULT_AUDIT_PASS":
        raise ResumeError("future membership audit did not pass")
    frame = pd.read_parquet(verify(contract["inputs"]["membership"]))
    frame = frame.sort_values(["outer", "user_key"], kind="stable", ignore_index=True)
    if len(frame) != 13_910 or frame["user_key"].nunique() != 3_398 or frame.duplicated(["outer", "user_key"]).any():
        raise ResumeError("future membership row-set drift")
    target_union: dict[str, set[int]] = defaultdict(set)
    for row in frame.itertuples(index=False):
        target_union[str(row.user_key)].update(map(int, row.target_movie_ids))
    for outer, group in frame.groupby("outer", sort=False, observed=True):
        keys = group["user_key"].astype(str).tolist()
        if set(keys) != set(group["donor_user_key"].astype(str)):
            raise ResumeError(f"future donor bijection drift: {outer}")
        for row in group.itertuples(index=False):
            if len(row.profile_movie_ids) != 30 or set(map(int, row.profile_movie_ids)) & target_union[str(row.user_key)] or str(row.user_key) == str(row.donor_user_key):
                raise ResumeError("future profile firewall drift")
    return frame


def reverse_future_users(frame: pd.DataFrame) -> dict[int, str]:
    wanted = set(frame["user_key"].astype(str))
    result = {}
    for uid in range(1, MAX_USER_ID + 1):
        key = user_key(uid)
        if key not in wanted:
            continue
        if old_user_bucket(uid) > 59 or user_role_bucket(uid) > 5999 or phase_bucket(uid) < 9000:
            raise ResumeError("future pseudonymous user reversal drift")
        result[uid] = key
    if len(result) != len(wanted):
        raise ResumeError("future pseudonymous reversal coverage drift")
    return result


def _zip_member(bundle: zipfile.ZipFile, suffix: str) -> str:
    values = [name for name in bundle.namelist() if name.endswith(suffix)]
    if len(values) != 1:
        raise RuntimeError(f"archive member ambiguity: {suffix}")
    return values[0]


def _user(raw: bytes) -> tuple[int, int]:
    first = raw.find(b",")
    if first <= 0:
        raise RuntimeError("malformed row before future user firewall")
    return int(raw[:first]), first


def _movie(raw: bytes, first: int) -> tuple[int, int]:
    second = raw.find(b",", first + 1)
    if second <= first + 1:
        raise RuntimeError("malformed future movie field")
    return int(raw[first + 1 : second]), second


def _rating(raw: bytes, second: int) -> int:
    third = raw.find(b",", second + 1)
    if third <= second + 1:
        raise RuntimeError("malformed future rating field")
    return rating_index(raw[second + 1 : third])


def _assert_profile_identity(membership: pd.DataFrame, profiles: pd.DataFrame) -> None:
    scalar = ("outer", "track", "fold_or_domain", "user_key", "donor_user_key")
    if len(membership) != len(profiles):
        raise ResumeError("prepared future row count drift")
    for column in scalar:
        if not np.array_equal(membership[column].astype(str).to_numpy(), profiles[column].astype(str).to_numpy()):
            raise ResumeError(f"prepared future scalar drift: {column}")
    for column in ("profile_movie_ids", "target_movie_ids"):
        if any(not np.array_equal(np.asarray(a), np.asarray(b)) for a, b in zip(membership[column], profiles[column], strict=True)):
            raise ResumeError(f"prepared future list drift: {column}")


def validate_prepare(contract: Mapping[str, Any], p: Mapping[str, Path]) -> tuple[dict[str, Any], pd.DataFrame]:
    membership = load_membership(contract)
    seal = json.loads(p["prepare_seal"].read_text(encoding="utf-8"))
    reader = seal.get("reader", {})
    if (
        seal.get("status") != "SEALED_FUTURE_PROFILE30_RATINGS"
        or seal.get("contract") != artifact(DEFAULT)
        or verify(seal["profiles"]) != p["profiles"].resolve()
        or int(seal.get("profile_slots", -1)) != len(membership) * 30
        or int(reader.get("raw_rows", -1)) != EXPECTED_RATING_ROWS
        or int(reader.get("profile_rating_slots_filled", -1)) != len(membership) * 30
        or int(reader.get("target_rating_values_parsed", -1)) != 0
        or int(reader.get("timestamps_parsed", -1)) != 0
        or seal.get("membership") != artifact(verify(contract["inputs"]["membership"]))
        or seal.get("prior") != artifact(verify(contract["inputs"]["calibration_prior"]))
        or any(seal.get(name) is not False for name in ("target_rating_values_opened", "timestamps_opened", "locked_test_opened", "final_reserve_opened", "product_policy_changed"))
    ):
        raise ResumeError("future prepare seal drift")
    profiles = pd.read_parquet(p["profiles"]).sort_values(["outer", "user_key"], kind="stable", ignore_index=True)
    _assert_profile_identity(membership, profiles)
    ratings = np.vstack(profiles["profile_rating_indices"]).astype(np.int8)
    if ratings.shape != (len(membership), 30) or bool((ratings < 0).any()) or bool((ratings > 9).any()):
        raise ResumeError("future profile rating semantics drift")
    return seal, profiles


def prepare(contract: Mapping[str, Any], p: Mapping[str, Path], resume: bool) -> dict[str, Any]:
    if p["prepare_seal"].exists():
        if not resume:
            raise ResumeError("future prepare seal requires --resume")
        return validate_prepare(contract, p)[0]
    if p["profiles"].exists():
        raise ResumeError("unsealed future profile ratings exist")
    membership = load_membership(contract)
    raw_to_key = reverse_future_users(membership)
    slots: dict[str, dict[int, list[tuple[int, int]]]] = defaultdict(lambda: defaultdict(list))
    targets: dict[str, set[int]] = defaultdict(set)
    for row_index, row in enumerate(membership.itertuples(index=False)):
        key = str(row.user_key)
        for slot, movie in enumerate(row.profile_movie_ids):
            slots[key][int(movie)].append((row_index, slot))
        targets[key].update(map(int, row.target_movie_ids))
    ratings = np.full((len(membership), 30), -1, dtype=np.int8)
    counters = {"raw_rows": 0, "discarded_after_user_id": 0, "profile_rating_slots_filled": 0, "target_rows_seen_not_parsed": 0, "target_rating_values_parsed": 0, "timestamps_parsed": 0}
    archive = verify(contract["inputs"]["movielens_archive"])
    progress(p["progress"], "OPEN_FUTURE_PROFILE_RATINGS", rows=len(membership))
    with zipfile.ZipFile(archive) as bundle, bundle.open(_zip_member(bundle, "ratings.csv")) as handle:
        if handle.readline().rstrip(b"\r\n") != b"userId,movieId,rating,timestamp":
            raise RuntimeError("MovieLens header drift")
        for raw in handle:
            counters["raw_rows"] += 1
            uid, first = _user(raw)
            key = raw_to_key.get(uid)
            if key is None:
                counters["discarded_after_user_id"] += 1
                continue
            movie, second = _movie(raw, first)
            locations = slots[key].get(movie, ())
            if movie in targets[key]:
                if locations:
                    raise RuntimeError("future profile/target overlap at reader")
                counters["target_rows_seen_not_parsed"] += 1
            if not locations:
                continue
            index = _rating(raw, second)
            for row_index, slot in locations:
                if ratings[row_index, slot] >= 0:
                    raise RuntimeError("duplicate future profile rating")
                ratings[row_index, slot] = index
                counters["profile_rating_slots_filled"] += 1
    if counters["raw_rows"] != EXPECTED_RATING_ROWS or counters["profile_rating_slots_filled"] != ratings.size or bool((ratings < 0).any()) or counters["target_rating_values_parsed"] or counters["timestamps_parsed"]:
        raise RuntimeError("future profile reader coverage/firewall failure")
    profiles = membership.copy()
    profiles["profile_rating_indices"] = [row.tolist() for row in ratings]
    atomic_parquet(p["profiles"], profiles)
    seal = {
        "status": "SEALED_FUTURE_PROFILE30_RATINGS",
        "contract": artifact(DEFAULT),
        "membership": artifact(verify(contract["inputs"]["membership"])),
        "profiles": artifact(p["profiles"]),
        "prior": artifact(verify(contract["inputs"]["calibration_prior"])),
        "membership_rows": len(profiles),
        "profile_slots": ratings.size,
        "reader": counters,
        "target_rating_values_opened": False,
        "timestamps_opened": False,
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "product_policy_changed": False,
    }
    atomic_json(p["prepare_seal"], seal)
    return validate_prepare(contract, p)[0]


def structured_features(contract: Mapping[str, Any]) -> tuple[np.ndarray, sparse.csr_matrix]:
    design = json.loads(verify(contract["inputs"]["design_contract"]).read_text(encoding="utf-8"))
    parent = json.loads(verify(contract["inputs"]["parent_contract"]).read_text(encoding="utf-8"))
    universe, _, _, _ = common_sets(parent)
    item_ids = np.asarray(sorted(universe), dtype=np.int64)
    frame = pd.read_parquet(verify(design["inputs"]["structured_features"]))
    matrix = build_structured_full(frame, item_ids).tocsr().astype(np.float32)
    norms = np.sqrt(np.asarray(matrix.multiply(matrix).sum(axis=1)).ravel())
    if len(item_ids) != 85_517 or bool((np.abs(norms - 1.0) > 0.0001).any()):
        raise RuntimeError("future structured content universe drift")
    return item_ids, matrix


def _score_order(movies: np.ndarray, scores: np.ndarray, outer: str, system: str, key: str) -> tuple[np.ndarray, bool]:
    return ranked_order(movies, scores, cohort="FUTURE", outer=outer, system=system, encoding="PERCENTILE_MAGNITUDE", k=30, user_key=key)


def validate_score_cell(contract: Mapping[str, Any], p: Mapping[str, Path], profiles: pd.DataFrame, outer: str, integrity: Mapping[str, Any]) -> None:
    bundle_path, _ = score_paths(contract, outer)
    if integrity.get("status") != "SEALED_FUTURE_FROZEN_OWN_SHUFFLE_TOP2" or integrity.get("outer") != outer or integrity.get("prepare_seal") != artifact(p["prepare_seal"]) or verify(integrity["bundle"]) != bundle_path.resolve() or any(integrity.get(name) is not False for name in ("target_rating_values_opened", "timestamps_opened", "locked_test_opened", "final_reserve_opened", "product_policy_changed")):
        raise ResumeError(f"future score integrity drift: {outer}")
    local = profiles.loc[profiles["outer"].eq(outer)].sort_values("user_key", kind="stable")
    target_n = 20 if outer in RANDOM_OUTERS else 4
    with np.load(bundle_path, allow_pickle=False) as bundle:
        if not np.array_equal(bundle["user_keys"].astype(str), local["user_key"].astype(str).to_numpy()) or not np.array_equal(bundle["target_movie_ids"], np.vstack(local["target_movie_ids"])):
            raise ResumeError(f"future score membership drift: {outer}")
        for side in ("own", "shuffle"):
            top = bundle[f"{side}_top2"]
            active = bundle[f"{side}_active"]
            if top.shape != (len(local), 2) or active.shape != (len(local),):
                raise ResumeError("future score shape drift")
            for values, enabled in zip(top, active, strict=True):
                if enabled and (len(set(map(int, values))) != 2 or bool((values < 0).any()) or bool((values >= target_n).any())):
                    raise ResumeError("future active Top2 drift")
                if not enabled and not bool((values == -1).all()):
                    raise ResumeError("future inactive Top2 drift")


def validate_scores(contract: Mapping[str, Any], p: Mapping[str, Path]) -> tuple[dict[str, Any], pd.DataFrame]:
    _, profiles = validate_prepare(contract, p)
    seal = json.loads(p["score_seal"].read_text(encoding="utf-8"))
    if seal.get("status") != "GLOBAL_FUTURE_PRELABEL_SCORE_SEAL_COMPLETE" or seal.get("contract") != artifact(DEFAULT) or seal.get("prepare_seal") != artifact(p["prepare_seal"]) or len(seal.get("integrities", [])) != 7 or any(seal.get(name) is not False for name in ("target_rating_values_opened", "timestamps_opened", "locked_test_opened", "final_reserve_opened", "product_policy_changed")):
        raise ResumeError("future global score seal drift")
    observed = set()
    for spec in seal["integrities"]:
        path = verify(spec)
        value = json.loads(path.read_text(encoding="utf-8"))
        outer = str(value.get("outer"))
        if outer in observed or outer not in OUTERS or path != score_paths(contract, outer)[1].resolve():
            raise ResumeError("future score outer set drift")
        validate_score_cell(contract, p, profiles, outer, value)
        observed.add(outer)
    if observed != set(OUTERS):
        raise ResumeError("future score coverage drift")
    return seal, profiles


def score(contract: Mapping[str, Any], p: Mapping[str, Path], resume: bool) -> dict[str, Any]:
    if p["score_seal"].exists():
        if not resume:
            raise ResumeError("future score seal requires --resume")
        return validate_scores(contract, p)[0]
    _, profiles = validate_prepare(contract, p)
    item_ids, matrix = structured_features(contract)
    lookup = np.full(int(item_ids.max()) + 1, -1, dtype=np.int32)
    lookup[item_ids] = np.arange(len(item_ids), dtype=np.int32)
    prior_path = verify(contract["inputs"]["calibration_prior"])
    with np.load(prior_path, allow_pickle=False) as prior:
        g0 = prior["g0_mid"].astype(np.float64)
    integrities = []
    for outer in OUTERS:
        bundle_path, integrity_path = score_paths(contract, outer)
        local = profiles.loc[profiles["outer"].eq(outer)].sort_values("user_key", kind="stable", ignore_index=True)
        if bundle_path.exists() and integrity_path.exists():
            if not resume:
                raise ResumeError(f"future score cell requires --resume: {outer}")
            integrity = json.loads(integrity_path.read_text(encoding="utf-8"))
            validate_score_cell(contract, p, profiles, outer, integrity)
        elif bundle_path.exists() or integrity_path.exists():
            raise ResumeError(f"partial future score cell: {outer}")
        else:
            row_by_key = {str(key): pos for pos, key in enumerate(local["user_key"].astype(str))}
            own_top = np.full((len(local), 2), -1, dtype=np.int8)
            shuffle_top = np.full_like(own_top, -1)
            own_active = np.zeros(len(local), dtype=bool)
            shuffle_active = np.zeros(len(local), dtype=bool)
            for row_number, row in enumerate(local.itertuples(index=False), start=1):
                target_movies = np.asarray(row.target_movie_ids, dtype=np.int64)
                target_pos = lookup[target_movies]
                donor = local.iloc[row_by_key[str(row.donor_user_key)]]
                for side, source, top, active in (
                    ("OWN", row, own_top, own_active),
                    ("SHUFFLE", donor, shuffle_top, shuffle_active),
                ):
                    profile_pos = lookup[np.asarray(source.profile_movie_ids, dtype=np.int64)]
                    if bool((target_pos < 0).any()) or bool((profile_pos < 0).any()):
                        raise RuntimeError("future score item outside content universe")
                    similarity = (matrix[target_pos] @ matrix[profile_pos].T).toarray().astype(np.float64)
                    weights = profile_weights(source.profile_rating_indices, g0, "PERCENTILE_MAGNITUDE")
                    values, enabled = weighted_similarity_scores(similarity, weights)
                    order, enabled = _score_order(target_movies, values, outer, f"STRUCTURED_DIRECT_{side}", str(row.user_key)) if enabled else (np.empty(0, dtype=np.int16), False)
                    if enabled:
                        top[row_number - 1] = order[:2]
                        active[row_number - 1] = True
                if row_number % 250 == 0:
                    progress(p["progress"], "SCORE_FUTURE", outer=outer, complete=row_number, total=len(local))
            atomic_npz(bundle_path, user_keys=local["user_key"].astype(str).to_numpy(dtype="U64"), target_movie_ids=np.vstack(local["target_movie_ids"]).astype(np.int32), own_top2=own_top, own_active=own_active, shuffle_top2=shuffle_top, shuffle_active=shuffle_active)
            integrity = {"status": "SEALED_FUTURE_FROZEN_OWN_SHUFFLE_TOP2", "outer": outer, "bundle": artifact(bundle_path), "prepare_seal": artifact(p["prepare_seal"]), "target_rating_values_opened": False, "timestamps_opened": False, "locked_test_opened": False, "final_reserve_opened": False, "product_policy_changed": False}
            atomic_json(integrity_path, integrity)
            validate_score_cell(contract, p, profiles, outer, integrity)
        integrities.append(artifact(integrity_path))
        progress(p["progress"], "SCORE_FUTURE_OUTER_COMPLETE", outer=outer, rows=len(local))
    seal = {"status": "GLOBAL_FUTURE_PRELABEL_SCORE_SEAL_COMPLETE", "contract": artifact(DEFAULT), "prepare_seal": artifact(p["prepare_seal"]), "integrities": integrities, "target_rating_values_opened": False, "timestamps_opened": False, "locked_test_opened": False, "final_reserve_opened": False, "product_policy_changed": False}
    atomic_json(p["score_seal"], seal)
    return validate_scores(contract, p)[0]


def validate_labels(contract: Mapping[str, Any], p: Mapping[str, Path]) -> tuple[dict[str, Any], pd.DataFrame]:
    _, profiles = validate_scores(contract, p)
    seal = json.loads(p["label_seal"].read_text(encoding="utf-8"))
    expected = int(sum(map(len, profiles["target_movie_ids"])))
    reader = seal.get("reader", {})
    if seal.get("status") != "OPENED_FUTURE_TARGET_LABELS_ONCE_AFTER_SCORE_SEAL" or seal.get("contract") != artifact(DEFAULT) or seal.get("score_seal") != artifact(p["score_seal"]) or verify(seal["labels"]) != p["labels"].resolve() or int(seal.get("membership_rows", -1)) != len(profiles) or int(seal.get("union_users", -1)) != 3398 or int(seal.get("target_labels", -1)) != expected or int(reader.get("raw_rows", -1)) != EXPECTED_RATING_ROWS or int(reader.get("target_rating_values_parsed", -1)) != expected or int(reader.get("timestamps_parsed", -1)) != 0 or any(seal.get(name) is not False for name in ("timestamps_opened", "locked_test_opened", "final_reserve_opened", "product_policy_changed")):
        raise ResumeError("future label seal drift")
    labels = pd.read_parquet(p["labels"]).sort_values(["outer", "user_key"], kind="stable", ignore_index=True)
    if len(labels) != len(profiles):
        raise ResumeError("future label row drift")
    for profile, label in zip(profiles.itertuples(index=False), labels.itertuples(index=False), strict=True):
        indices = np.asarray(label.target_rating_indices, dtype=np.int8)
        histogram = np.asarray(label.full_history_histogram, dtype=np.uint32)
        if str(profile.outer) != str(label.outer) or str(profile.user_key) != str(label.user_key) or not np.array_equal(np.asarray(profile.target_movie_ids), np.asarray(label.target_movie_ids)) or indices.shape != (len(profile.target_movie_ids),) or histogram.shape != (10,) or int(histogram.sum()) != int(label.full_history_count) or not np.allclose(np.asarray(label.target_q), full_history_q(indices, histogram)):
            raise ResumeError("future label semantics drift")
    return seal, labels


def open_labels(contract: Mapping[str, Any], p: Mapping[str, Path], resume: bool) -> dict[str, Any]:
    if p["label_seal"].exists():
        if not resume:
            raise ResumeError("future label seal requires --resume")
        return validate_labels(contract, p)[0]
    if p["labels"].exists():
        raise ResumeError("unsealed future labels exist")
    _, profiles = validate_scores(contract, p)
    raw_to_key = reverse_future_users(profiles)
    keys = sorted(set(profiles["user_key"].astype(str)))
    positions = {key: pos for pos, key in enumerate(keys)}
    appearances: dict[str, list[int]] = defaultdict(list)
    slots = []
    values = []
    for row_index, row in enumerate(profiles.itertuples(index=False)):
        appearances[str(row.user_key)].append(row_index)
        slots.append({int(movie): pos for pos, movie in enumerate(row.target_movie_ids)})
        values.append(np.full(len(row.target_movie_ids), -1, dtype=np.int8))
    hist = np.zeros((len(keys), 10), dtype=np.uint32)
    counters = {"raw_rows": 0, "discarded_after_user_id": 0, "future_rating_values_parsed": 0, "target_rating_values_parsed": 0, "timestamps_parsed": 0}
    archive = verify(contract["inputs"]["movielens_archive"])
    progress(p["progress"], "OPEN_FUTURE_TARGET_LABELS", users=len(keys))
    with zipfile.ZipFile(archive) as bundle, bundle.open(_zip_member(bundle, "ratings.csv")) as handle:
        handle.readline()
        for raw in handle:
            counters["raw_rows"] += 1
            uid, first = _user(raw)
            key = raw_to_key.get(uid)
            if key is None:
                counters["discarded_after_user_id"] += 1
                continue
            movie, second = _movie(raw, first)
            index = _rating(raw, second)
            hist[positions[key], index] += 1
            counters["future_rating_values_parsed"] += 1
            for row_index in appearances[key]:
                slot = slots[row_index].get(movie)
                if slot is not None:
                    if values[row_index][slot] >= 0:
                        raise RuntimeError("duplicate future target rating")
                    values[row_index][slot] = index
                    counters["target_rating_values_parsed"] += 1
    expected = int(sum(map(len, profiles["target_movie_ids"])))
    if counters["raw_rows"] != EXPECTED_RATING_ROWS or counters["target_rating_values_parsed"] != expected or counters["timestamps_parsed"] or any(bool((row < 0).any()) for row in values):
        raise RuntimeError("future label reader coverage/firewall failure")
    rows = []
    for row_index, row in enumerate(profiles.itertuples(index=False)):
        histogram = hist[positions[str(row.user_key)]]
        rows.append({"outer": str(row.outer), "track": str(row.track), "fold_or_domain": str(row.fold_or_domain), "user_key": str(row.user_key), "target_movie_ids": list(map(int, row.target_movie_ids)), "target_rating_indices": values[row_index].tolist(), "target_q": full_history_q(values[row_index], histogram).tolist(), "full_history_count": int(histogram.sum()), "full_history_histogram": histogram.tolist()})
    labels = pd.DataFrame(rows).sort_values(["outer", "user_key"], kind="stable", ignore_index=True)
    atomic_parquet(p["labels"], labels)
    seal = {"status": "OPENED_FUTURE_TARGET_LABELS_ONCE_AFTER_SCORE_SEAL", "contract": artifact(DEFAULT), "score_seal": artifact(p["score_seal"]), "labels": artifact(p["labels"]), "membership_rows": len(labels), "union_users": len(keys), "target_labels": expected, "reader": counters, "timestamps_opened": False, "locked_test_opened": False, "final_reserve_opened": False, "product_policy_changed": False}
    atomic_json(p["label_seal"], seal)
    return validate_labels(contract, p)[0]


def _outer_metrics(contract: Mapping[str, Any], p: Mapping[str, Path], labels: pd.DataFrame, outer: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    local = labels.loc[labels["outer"].eq(outer)].sort_values("user_key", kind="stable", ignore_index=True)
    bundle_path, _ = score_paths(contract, outer)
    with np.load(bundle_path, allow_pickle=False) as bundle:
        users, targets = bundle["user_keys"].astype(str), bundle["target_movie_ids"].astype(np.int64)
        own_top, own_active = bundle["own_top2"], bundle["own_active"]
        shuffle_top, shuffle_active = bundle["shuffle_top2"], bundle["shuffle_active"]
    q = np.vstack(local["target_q"]).astype(np.float64)
    if not np.array_equal(users, local["user_key"].astype(str).to_numpy()) or not np.array_equal(targets, np.vstack(local["target_movie_ids"])):
        raise ResumeError(f"future score/label alignment drift: {outer}")
    own_user, random_user = user_metric_arrays(q, own_top, own_active)
    shuffle_user, _ = user_metric_arrays(q, shuffle_top, shuffle_active)
    user_data: dict[str, Any] = {"outer": outer, "user_key": users, "full_history_count": local["full_history_count"].to_numpy(), "own_active": own_active, "shuffle_active": shuffle_active}
    for system, source in (("OWN", own_user), ("SHUFFLE", shuffle_user), ("RANDOM", random_user)):
        for metric in USER_METRICS:
            user_data[f"{system}__{metric}"] = source[metric]
    own_item, random_item = item_contribution_arrays(q, own_top, own_active)
    shuffle_item, _ = item_contribution_arrays(q, shuffle_top, shuffle_active)
    item_data: dict[str, Any] = {"outer": np.repeat(outer, targets.size), "user_key": np.repeat(users, targets.shape[1]), "movie_id": targets.ravel(), "q": q.ravel(), "low20": (q <= 0.20).ravel()}
    for system, source in (("OWN", own_item), ("SHUFFLE", shuffle_item), ("RANDOM", random_item)):
        for metric in ITEM_METRICS:
            item_data[f"{system}__{metric}"] = source[metric].ravel()
    return pd.DataFrame(user_data), pd.DataFrame(item_data)


def _pooled_users(frames: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    columns = [f"{system}__{metric}" for system in SYSTEMS for metric in USER_METRICS]
    combined = pd.concat([frames[outer][["user_key", "full_history_count", "own_active", "shuffle_active", *columns]] for outer in RANDOM_OUTERS])
    values = combined.groupby("user_key", sort=True, observed=True)[["full_history_count", *columns]].mean()
    active = combined.groupby("user_key", sort=True, observed=True)[["own_active", "shuffle_active"]].all()
    return values.join(active).reset_index()


def _item_estimand(frame: pd.DataFrame) -> ItemEstimand:
    users = sorted(frame["user_key"].astype(str).unique())
    movies = sorted(frame["movie_id"].astype(int).unique())
    up, mp = {key: i for i, key in enumerate(users)}, {movie: i for i, movie in enumerate(movies)}
    rows = frame["user_key"].astype(str).map(up).to_numpy(dtype=np.int64)
    cols = frame["movie_id"].astype(int).map(mp).to_numpy(dtype=np.int64)
    counts = frame.groupby("movie_id", observed=True).size().to_dict()
    normalizer = frame["movie_id"].map(lambda movie: 1.0 / counts[int(movie)]).to_numpy()
    shape = (len(users), len(movies))
    denominator = sparse.coo_matrix((normalizer, (rows, cols)), shape=shape).tocsr()
    matrices = []
    for system in SYSTEMS:
        for metric in ITEM_METRICS:
            data = frame[f"{system}__{metric}"].to_numpy() * normalizer
            matrices.append(sparse.coo_matrix((data, (rows, cols)), shape=shape).tocsr())
    return ItemEstimand(tuple(users), tuple(movies), denominator, tuple(matrices))


def _item_transform() -> np.ndarray:
    base = {(system, metric): i for i, (system, metric) in enumerate((s, m) for s in SYSTEMS for m in ITEM_METRICS)}
    transform = np.zeros((6, 4))
    column = 0
    for model, comparator in CONTRASTS:
        for metric in ITEM_METRICS:
            sign = -1.0 if metric == "ITEM_LOW" else 1.0
            transform[base[(model, metric)], column] = sign
            transform[base[(comparator, metric)], column] = -sign
            column += 1
    return transform


def _history_band(value: float) -> str:
    return "20_TO_49" if value < 50 else "50_TO_99" if value < 100 else "100_TO_499" if value < 500 else "500_PLUS"


def _occurrence_band(value: int) -> str:
    return "1" if value == 1 else "2_TO_4" if value <= 4 else "5_TO_9" if value <= 9 else "10_PLUS"


def validate_metric_cell(
    contract: Mapping[str, Any],
    p: Mapping[str, Path],
    labels: pd.DataFrame,
    outer: str,
    integrity: Mapping[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    user_path, item_path, _ = metric_paths(contract, outer)
    if (
        integrity.get("status") != "SEALED_FUTURE_OUTER_METRICS"
        or integrity.get("outer") != outer
        or integrity.get("label_seal") != artifact(p["label_seal"])
        or verify(integrity["user_metrics"]) != user_path.resolve()
        or verify(integrity["item_occurrences"]) != item_path.resolve()
        or any(integrity.get(name) is not False for name in ("locked_test_opened", "final_reserve_opened", "product_policy_changed"))
    ):
        raise ResumeError(f"future metric integrity drift: {outer}")
    local = labels.loc[labels["outer"].eq(outer)].sort_values("user_key", kind="stable", ignore_index=True)
    users = pd.read_parquet(user_path).sort_values("user_key", kind="stable", ignore_index=True)
    items = pd.read_parquet(item_path).sort_values(["user_key", "movie_id"], kind="stable", ignore_index=True)
    user_values = [f"{system}__{metric}" for system in SYSTEMS for metric in USER_METRICS]
    item_values = [f"{system}__{metric}" for system in SYSTEMS for metric in ITEM_METRICS]
    expected_pairs = {(str(row.user_key), int(movie)) for row in local.itertuples(index=False) for movie in row.target_movie_ids}
    observed_pairs = set(zip(items["user_key"].astype(str), items["movie_id"].astype(int), strict=True))
    if (
        len(users) != len(local)
        or users["user_key"].astype(str).tolist() != local["user_key"].astype(str).tolist()
        or users["user_key"].duplicated().any()
        or users["own_active"].dtype != bool
        or users["shuffle_active"].dtype != bool
        or not np.isfinite(users[user_values].to_numpy(dtype=np.float64)).all()
        or len(items) != len(expected_pairs)
        or items.duplicated(["user_key", "movie_id"]).any()
        or observed_pairs != expected_pairs
        or not np.isfinite(items[["q", *item_values]].to_numpy(dtype=np.float64)).all()
    ):
        raise ResumeError(f"future metric semantics drift: {outer}")
    return users, items


def validate_metric_seal(
    contract: Mapping[str, Any], p: Mapping[str, Path]
) -> tuple[dict[str, Any], dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    _, labels = validate_labels(contract, p)
    seal = json.loads(p["metric_seal"].read_text(encoding="utf-8"))
    if (
        seal.get("status") != "SEALED_FUTURE_ALL_OUTER_METRICS"
        or seal.get("label_seal") != artifact(p["label_seal"])
        or len(seal.get("integrities", [])) != 7
        or any(seal.get(name) is not False for name in ("locked_test_opened", "final_reserve_opened", "product_policy_changed"))
    ):
        raise ResumeError("future metric seal drift")
    users, items, observed = {}, {}, set()
    for spec in seal["integrities"]:
        integrity_path = verify(spec)
        integrity = json.loads(integrity_path.read_text(encoding="utf-8"))
        outer = str(integrity.get("outer"))
        if outer not in OUTERS or outer in observed or integrity_path != metric_paths(contract, outer)[2].resolve():
            raise ResumeError("future metric outer coverage drift")
        users[outer], items[outer] = validate_metric_cell(contract, p, labels, outer, integrity)
        observed.add(outer)
    if observed != set(OUTERS):
        raise ResumeError("future metric outer set drift")
    return seal, users, items


def validate_final(contract: Mapping[str, Any], p: Mapping[str, Path]) -> dict[str, Any]:
    validate_labels(contract, p)
    validate_metric_seal(contract, p)
    value = json.loads(p["final"].read_text(encoding="utf-8"))
    if value.get("status") != "COMPLETE_REC_EV_030_UNTOUCHED_CONFIRMATION" or value.get("contract") != artifact(DEFAULT) or value.get("label_seal") != artifact(p["label_seal"]) or any(value.get(name) is not False for name in ("timestamps_opened", "locked_test_opened", "final_reserve_opened", "product_policy_changed")):
        raise ResumeError("future final seal drift")
    inference = json.loads(verify(value["inference"]).read_text(encoding="utf-8"))
    if inference.get("user_family_size") != 6 or inference.get("item_family_size") != 4 or len(inference.get("user_intervals", [])) != 6 or len(inference.get("item_intervals", [])) != 4 or value.get("primary_pass") != inference.get("primary_pass"):
        raise ResumeError("future primary inference drift")
    for key in ("descriptive", "user_segments", "movie_segments", "user_bootstrap", "item_bootstrap", "metric_seal"):
        verify(value[key])
    return value


def reject_partial_analysis(p: Mapping[str, Path]) -> None:
    protected = (
        "metric_seal",
        "user_boot",
        "item_boot",
        "inference",
        "descriptive",
        "user_segments",
        "movie_segments",
    )
    existing = [name for name in protected if p[name].exists()]
    if existing:
        raise ResumeError(f"partial future analysis state: {','.join(existing)}")


def analyze(contract: Mapping[str, Any], p: Mapping[str, Path], resume: bool) -> dict[str, Any]:
    if p["final"].exists():
        if not resume:
            raise ResumeError("future final requires --resume")
        return validate_final(contract, p)
    reject_partial_analysis(p)
    _, labels = validate_labels(contract, p)
    user_frames, item_frames, integrities = {}, {}, []
    for outer in OUTERS:
        user_path, item_path, integrity_path = metric_paths(contract, outer)
        if user_path.exists() and item_path.exists() and integrity_path.exists():
            if not resume:
                raise ResumeError(f"future metrics require --resume: {outer}")
            integrity = json.loads(integrity_path.read_text(encoding="utf-8"))
            users, items = validate_metric_cell(contract, p, labels, outer, integrity)
        elif user_path.exists() or item_path.exists() or integrity_path.exists():
            raise ResumeError(f"partial future metrics: {outer}")
        else:
            users, items = _outer_metrics(contract, p, labels, outer)
            atomic_parquet(user_path, users)
            atomic_parquet(item_path, items)
            integrity = {"status": "SEALED_FUTURE_OUTER_METRICS", "outer": outer, "label_seal": artifact(p["label_seal"]), "user_metrics": artifact(user_path), "item_occurrences": artifact(item_path), "locked_test_opened": False, "final_reserve_opened": False, "product_policy_changed": False}
            atomic_json(integrity_path, integrity)
            users, items = validate_metric_cell(contract, p, labels, outer, integrity)
        user_frames[outer], item_frames[outer] = users, items
        integrities.append(artifact(integrity_path))
    metric_seal = {"status": "SEALED_FUTURE_ALL_OUTER_METRICS", "label_seal": artifact(p["label_seal"]), "integrities": integrities, "locked_test_opened": False, "final_reserve_opened": False, "product_policy_changed": False}
    atomic_json(p["metric_seal"], metric_seal)
    validate_metric_seal(contract, p)
    pooled_user = _pooled_users(user_frames)
    user_columns = []
    user_descriptions = []
    for model, comparator in CONTRASTS:
        for metric in USER_METRICS:
            user_columns.append(benefit(pooled_user[f"{model}__{metric}"].to_numpy(), pooled_user[f"{comparator}__{metric}"].to_numpy(), metric))
            user_descriptions.append({"model": model, "comparator": comparator, "metric": metric})
    user_matrix = np.column_stack(user_columns)
    union_users = pooled_user["user_key"].astype(str).tolist()
    user_boot = user_multiplier_max_t(union_users=union_users, matrices={"RANDOM_POOLED": (union_users, user_matrix)}, repeats=5000, seed=20261008, confidence=0.95)
    atomic_npz(p["user_boot"], **{key: np.asarray(value) for key, value in user_boot.items()})
    pooled_item = pd.concat([item_frames[outer] for outer in RANDOM_OUTERS], ignore_index=True)
    item_input = _item_estimand(pooled_item)
    union_movies = list(item_input.movie_ids)
    item_boot = item_multiway_multiplier_max_t(union_users=union_users, union_movies=union_movies, estimands={"RANDOM_POOLED": item_input}, repeats=5000, seed=20261009, confidence=0.95, linear_transform=_item_transform())
    atomic_npz(p["item_boot"], **{key: np.asarray(value) for key, value in item_boot.items()})
    item_descriptions = [{"model": model, "comparator": comparator, "metric": metric} for model, comparator in CONTRASTS for metric in ITEM_METRICS]
    user_intervals = [{**desc, "point": float(user_boot["point"][i]), "low": float(user_boot["low"][i]), "high": float(user_boot["high"][i])} for i, desc in enumerate(user_descriptions)]
    item_intervals = [{**desc, "point": float(item_boot["point"][i]), "low": float(item_boot["low"][i]), "high": float(item_boot["high"][i])} for i, desc in enumerate(item_descriptions)]
    user_pass = all(row["low"] >= 0.0 if row["metric"] == "HARM20" else row["low"] > 0.0 for row in user_intervals)
    item_pass = all(row["low"] >= 0.0 if row["metric"] == "ITEM_LOW" else row["low"] > 0.0 for row in item_intervals)
    active = {"OWN": float(pooled_user["own_active"].mean()), "SHUFFLE": float(pooled_user["shuffle_active"].mean())}
    primary_pass = active["OWN"] >= 0.95 and active["SHUFFLE"] >= 0.95 and user_pass and item_pass
    inference = {"status": "COMPLETE_RANDOM_POOLED_USER6_ITEM4_MAX_T", "user_family_size": 6, "item_family_size": 4, "user_critical": float(user_boot["critical"]), "item_critical": float(item_boot["critical"]), "active_rates": active, "user_intervals": user_intervals, "item_intervals": item_intervals, "primary_pass": primary_pass}
    atomic_json(p["inference"], inference)
    descriptive = {}
    for outer in OUTERS:
        users, items = user_frames[outer], item_frames[outer]
        descriptive[outer] = {"users": len(users), "unique_movies": int(items["movie_id"].nunique()), "active": {"OWN": float(users["own_active"].mean()), "SHUFFLE": float(users["shuffle_active"].mean())}, "user_metrics": {system: {metric: float(users[f"{system}__{metric}"].mean()) for metric in USER_METRICS} for system in SYSTEMS}, "item_metrics": {system: {metric: equal_movie_mean(items["movie_id"].to_numpy(), items[f"{system}__{metric}"].to_numpy()) for metric in ITEM_METRICS} for system in SYSTEMS}}
    atomic_json(p["descriptive"], {"status": "DESCRIPTIVE_ONLY_CANNOT_OVERRIDE_PRIMARY", "outers": descriptive})
    user_segment_rows = []
    for estimand, frame in {**user_frames, "RANDOM_POOLED": pooled_user}.items():
        local = frame.copy()
        local["band"] = local["full_history_count"].map(_history_band)
        for band, group in local.groupby("band", observed=True):
            for system in SYSTEMS:
                user_segment_rows.append({"estimand": estimand, "history_band": band, "system": system, "users": len(group), **{metric: float(group[f"{system}__{metric}"].mean()) for metric in USER_METRICS}})
    atomic_parquet(p["user_segments"], pd.DataFrame(user_segment_rows))
    movie_segment_rows = []
    for estimand, frame in {**item_frames, "RANDOM_POOLED": pooled_item}.items():
        counts = frame.groupby("movie_id", observed=True).size()
        local = frame.copy()
        local["band"] = local["movie_id"].map(lambda movie: _occurrence_band(int(counts.loc[int(movie)])))
        for band, group in local.groupby("band", observed=True):
            for system in SYSTEMS:
                movie_segment_rows.append({"estimand": estimand, "occurrence_band": band, "system": system, "movies": int(group["movie_id"].nunique()), "occurrences": len(group), **{metric: equal_movie_mean(group["movie_id"].to_numpy(), group[f"{system}__{metric}"].to_numpy()) for metric in ITEM_METRICS}})
    atomic_parquet(p["movie_segments"], pd.DataFrame(movie_segment_rows))
    final = {"status": "COMPLETE_REC_EV_030_UNTOUCHED_CONFIRMATION", "contract": artifact(DEFAULT), "label_seal": artifact(p["label_seal"]), "metric_seal": artifact(p["metric_seal"]), "inference": artifact(p["inference"]), "descriptive": artifact(p["descriptive"]), "user_segments": artifact(p["user_segments"]), "movie_segments": artifact(p["movie_segments"]), "user_bootstrap": artifact(p["user_boot"]), "item_bootstrap": artifact(p["item_boot"]), "primary_pass": primary_pass, "status_claim": "CONFIRMED_BIAS_FREE_PERSONALIZATION_ON_MASKED_RATED_ITEMS" if primary_pass else "UNTOUCHED_CONFIRMATION_FAILED", "timestamps_opened": False, "locked_test_opened": False, "final_reserve_opened": False, "product_policy_changed": False}
    atomic_json(p["final"], final)
    return validate_final(contract, p)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT)
    parser.add_argument("--phase", choices=("prepare", "score", "labels", "analyze", "all"), default="all")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    contract = load_contract(args.contract)
    p = paths(contract)
    if args.phase in {"prepare", "all"}:
        prepare(contract, p, args.resume)
    if args.phase in {"score", "all"}:
        score(contract, p, args.resume)
    if args.phase in {"labels", "all"}:
        open_labels(contract, p, args.resume)
    if args.phase in {"analyze", "all"}:
        analyze(contract, p, args.resume)
    print(json.dumps({"status": "OK", "phase": args.phase}))


if __name__ == "__main__":
    main()
