"""Run the audited REC-EV-029B selection and disjoint replication analysis."""

from __future__ import annotations

import argparse
from collections import defaultdict
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
from preflight_rec_ev_029_membership import EXPECTED_RATING_ROWS
from rec_ev_027_core import full_history_q, rating_index
from rec_ev_028b_core import ItemEstimand, item_multiway_multiplier_max_t, user_multiplier_max_t
from rec_ev_029_analysis import (
    ITEM_METRICS,
    USER_METRICS,
    benefit,
    candidate_is_eligible,
    equal_movie_mean,
    item_contribution_arrays,
    select_winner,
    user_metric_arrays,
)
from rec_ev_029_core import candidate_grid
import run_rec_ev_029_prelabel as prelabel


ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "docs/recommendation/contracts/rec-ev-029c-postlabel-execution.json"
RANDOM_OUTERS = ("R0", "R1", "R2", "R3", "R4")
OUTERS = (*RANDOM_OUTERS, "KR", "RECENT")
ESTIMANDS = (*OUTERS, "RANDOM_POOLED")
SYSTEMS = ("OWN", "SHUFFLE", "RANDOM")
CONTRASTS = (("OWN", "RANDOM"), ("SHUFFLE", "RANDOM"), ("OWN", "SHUFFLE"))


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
        "selection_labels": root / "selection/labels.parquet",
        "selection_label_seal": root / "selection/label-seal.json",
        "selection_summary": root / "selection/candidate-summaries.json",
        "winner_seal": root / "selection/winner-seal.json",
        "replication_labels": root / "replication/labels.parquet",
        "replication_label_seal": root / "replication/label-seal.json",
        "replication_metric_seal": root / "replication/metric-seal.json",
        "user_bootstrap": root / "replication/user-bootstrap.npz",
        "item_bootstrap": root / "replication/item-bootstrap.npz",
        "inference": root / "replication/simultaneous-inference.json",
        "user_segments": root / "replication/user-segments.parquet",
        "movie_segments": root / "replication/movie-segments.parquet",
        "final": root / "final-analysis.json",
    }


def metric_paths(contract: Mapping[str, Any], outer: str) -> tuple[Path, Path, Path]:
    root = ROOT / str(contract["output_root"]) / "replication/outers" / outer
    return root / "user-metrics.parquet", root / "item-occurrences.parquet", root / "integrity.json"


def load_contract(path: Path) -> dict[str, Any]:
    from validate_rec_ev_029_postlabel import validate

    value = json.loads(path.read_text(encoding="utf-8"))
    validate(value, path.resolve())
    if value["implementation_audit"].get("status") != "REC_EV_029_POSTLABEL_IMPLEMENTATION_AUDIT_PASS":
        raise ResumeError("independent post-label implementation audit has not passed")
    return value


def verify_prelabel(contract: Mapping[str, Any]) -> tuple[dict[str, Any], pd.DataFrame]:
    audit = json.loads(verify(contract["inputs"]["actual_prelabel_audit"]).read_text(encoding="utf-8"))
    if audit.get("status") != "REC_EV_029_ACTUAL_PRELABEL_AUDIT_PASS":
        raise ResumeError("actual pre-label audit did not pass")
    pre_contract_path = verify(contract["inputs"]["prelabel_execution_contract"])
    pre_contract = json.loads(pre_contract_path.read_text(encoding="utf-8"))
    prelabel.validate_scores(pre_contract, prelabel.paths(pre_contract))
    if artifact(prelabel.paths(pre_contract)["score_seal"]) != contract["inputs"]["global_prelabel_score_seal"]:
        raise ResumeError("global pre-label score seal identity drift")
    membership = prelabel.load_membership(pre_contract)
    return pre_contract, membership


def _zip_member(bundle: zipfile.ZipFile, suffix: str) -> str:
    values = [name for name in bundle.namelist() if name.endswith(suffix)]
    if len(values) != 1:
        raise RuntimeError(f"archive member ambiguity: {suffix}")
    return values[0]


def _parse_user(raw: bytes) -> tuple[int, int]:
    first = raw.find(b",")
    if first <= 0:
        raise RuntimeError("malformed row before cohort user firewall")
    return int(raw[:first]), first


def _parse_movie_rating(raw: bytes, first: int) -> tuple[int, int]:
    second = raw.find(b",", first + 1)
    third = raw.find(b",", second + 1)
    if second <= first + 1 or third <= second + 1:
        raise RuntimeError("malformed cohort movie/rating field")
    return int(raw[first + 1 : second]), rating_index(raw[second + 1 : third])


def _cohort_membership(membership: pd.DataFrame, cohort: str) -> pd.DataFrame:
    frame = membership.loc[membership["cohort"].eq(cohort)].sort_values(["outer", "user_key"], kind="stable", ignore_index=True)
    expected = 10_849 if cohort == "SELECTION" else 11_055
    if len(frame) != expected:
        raise ResumeError(f"cohort membership row drift: {cohort}")
    return frame


def _label_artifacts(p: Mapping[str, Path], cohort: str) -> tuple[Path, Path]:
    prefix = cohort.lower()
    return p[f"{prefix}_labels"], p[f"{prefix}_label_seal"]


def validate_labels(contract: Mapping[str, Any], p: Mapping[str, Path], cohort: str) -> tuple[dict[str, Any], pd.DataFrame]:
    pre_contract, membership = verify_prelabel(contract)
    local = _cohort_membership(membership, cohort)
    label_path, seal_path = _label_artifacts(p, cohort)
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    expected_targets = int(sum(map(len, local["target_movie_ids"])))
    status = f"OPENED_{cohort}_TARGET_LABELS_ONCE_AFTER_REQUIRED_SEALS"
    reader = seal.get("reader", {})
    if (
        seal.get("status") != status
        or seal.get("contract") != artifact(DEFAULT)
        or verify(seal["labels"]) != label_path.resolve()
        or int(seal.get("membership_rows", -1)) != len(local)
        or int(seal.get("target_labels", -1)) != expected_targets
        or int(reader.get("raw_rows", -1)) != EXPECTED_RATING_ROWS
        or int(reader.get("target_rating_values_parsed", -1)) != expected_targets
        or int(reader.get("timestamps_parsed", -1)) != 0
        or int(reader.get("other_cohort_rating_values_parsed", -1)) != 0
        or any(seal.get(name) is not False for name in ("timestamps_opened", "locked_test_opened", "final_reserve_opened", "product_policy_changed"))
    ):
        raise ResumeError(f"label seal semantic drift: {cohort}")
    if seal.get("global_prelabel_score_seal") != artifact(prelabel.paths(pre_contract)["score_seal"]):
        raise ResumeError(f"label/pre-label score identity drift: {cohort}")
    if cohort == "SELECTION" and seal.get("replication_rating_values_opened") is not False:
        raise ResumeError("selection seal opened replication ratings")
    if cohort == "REPLICATION" and seal.get("winner_seal") != artifact(p["winner_seal"]):
        raise ResumeError("replication label/winner identity drift")
    labels = pd.read_parquet(label_path).sort_values(["outer", "user_key"], kind="stable", ignore_index=True)
    if len(labels) != len(local) or labels.duplicated(["outer", "user_key"]).any():
        raise ResumeError(f"label row-set drift: {cohort}")
    for member, label in zip(local.itertuples(index=False), labels.itertuples(index=False), strict=True):
        indices = np.asarray(label.target_rating_indices, dtype=np.int8)
        histogram = np.asarray(label.full_history_histogram, dtype=np.uint32)
        q = np.asarray(label.target_q, dtype=np.float64)
        if (
            str(member.outer) != str(label.outer)
            or str(member.user_key) != str(label.user_key)
            or not np.array_equal(np.asarray(member.target_movie_ids, dtype=np.int64), np.asarray(label.target_movie_ids, dtype=np.int64))
            or indices.shape != (len(member.target_movie_ids),)
            or histogram.shape != (10,)
            or int(histogram.sum()) != int(label.full_history_count)
            or not np.allclose(q, full_history_q(indices, histogram))
        ):
            raise ResumeError(f"label semantics drift: {cohort}")
    return seal, labels


def open_labels(contract: Mapping[str, Any], p: Mapping[str, Path], cohort: str, resume: bool) -> dict[str, Any]:
    label_path, seal_path = _label_artifacts(p, cohort)
    if seal_path.exists():
        if not resume:
            raise ResumeError(f"sealed {cohort} labels require --resume")
        seal, _ = validate_labels(contract, p, cohort)
        return seal
    if label_path.exists():
        raise ResumeError(f"unsealed {cohort} label artifact exists")
    pre_contract, membership = verify_prelabel(contract)
    if cohort == "REPLICATION":
        winner = validate_winner(contract, p)
        if winner.get("winner") is None:
            raise ResumeError("replication labels forbidden because selection froze no winner")
    local = _cohort_membership(membership, cohort)
    all_raw = prelabel.reverse_evaluation_users(membership, str(pre_contract["protocol"]["split_salt"]))
    wanted_keys = set(local["user_key"].astype(str))
    raw_to_key = {uid: key for uid, key in all_raw.items() if key in wanted_keys}
    if len(raw_to_key) != len(wanted_keys):
        raise ResumeError("cohort raw user reversal drift")
    union_keys = sorted(wanted_keys)
    union_pos = {key: pos for pos, key in enumerate(union_keys)}
    appearances: dict[str, list[int]] = defaultdict(list)
    target_slots: list[dict[int, int]] = []
    target_values: list[np.ndarray] = []
    for row_index, row in enumerate(local.itertuples(index=False)):
        appearances[str(row.user_key)].append(row_index)
        target_slots.append({int(movie): slot for slot, movie in enumerate(row.target_movie_ids)})
        target_values.append(np.full(len(row.target_movie_ids), -1, dtype=np.int8))
    full_hist = np.zeros((len(union_keys), 10), dtype=np.uint32)
    counters = {
        "raw_rows": 0,
        "noncohort_rows_discarded_after_user_id": 0,
        "cohort_rating_values_parsed": 0,
        "target_rating_values_parsed": 0,
        "timestamps_parsed": 0,
        "other_cohort_rating_values_parsed": 0,
    }
    archive = verify(contract["inputs"]["movielens_archive"])
    progress(p["progress"], f"OPEN_{cohort}_LABELS", users=len(union_keys))
    with zipfile.ZipFile(archive) as bundle, bundle.open(_zip_member(bundle, "ratings.csv")) as handle:
        if handle.readline().rstrip(b"\r\n") != b"userId,movieId,rating,timestamp":
            raise RuntimeError("MovieLens header drift")
        for raw in handle:
            counters["raw_rows"] += 1
            uid, first = _parse_user(raw)
            key = raw_to_key.get(uid)
            if key is None:
                counters["noncohort_rows_discarded_after_user_id"] += 1
                continue
            movie, index = _parse_movie_rating(raw, first)
            full_hist[union_pos[key], index] += 1
            counters["cohort_rating_values_parsed"] += 1
            for row_index in appearances[key]:
                slot = target_slots[row_index].get(movie)
                if slot is not None:
                    if target_values[row_index][slot] >= 0:
                        raise RuntimeError("duplicate cohort target rating")
                    target_values[row_index][slot] = index
                    counters["target_rating_values_parsed"] += 1
    expected_targets = int(sum(map(len, local["target_movie_ids"])))
    if (
        counters["raw_rows"] != EXPECTED_RATING_ROWS
        or counters["target_rating_values_parsed"] != expected_targets
        or counters["timestamps_parsed"]
        or counters["other_cohort_rating_values_parsed"]
        or bool((full_hist.sum(axis=1) <= 0).any())
        or any(bool((values < 0).any()) for values in target_values)
    ):
        raise RuntimeError(f"{cohort} label reader coverage/firewall failure")
    rows = []
    for row_index, row in enumerate(local.itertuples(index=False)):
        histogram = full_hist[union_pos[str(row.user_key)]]
        indices = target_values[row_index]
        rows.append({
            "outer": str(row.outer),
            "track": str(row.track),
            "fold_or_domain": str(row.fold_or_domain),
            "user_key": str(row.user_key),
            "target_movie_ids": list(map(int, row.target_movie_ids)),
            "target_rating_indices": indices.tolist(),
            "target_q": full_history_q(indices, histogram).tolist(),
            "full_history_count": int(histogram.sum()),
            "full_history_histogram": histogram.tolist(),
        })
    labels = pd.DataFrame(rows).sort_values(["outer", "user_key"], kind="stable", ignore_index=True)
    atomic_parquet(label_path, labels)
    seal = {
        "status": f"OPENED_{cohort}_TARGET_LABELS_ONCE_AFTER_REQUIRED_SEALS",
        "contract": artifact(DEFAULT),
        "global_prelabel_score_seal": artifact(prelabel.paths(pre_contract)["score_seal"]),
        "winner_seal": artifact(p["winner_seal"]) if cohort == "REPLICATION" else None,
        "labels": artifact(label_path),
        "membership_rows": len(local),
        "union_users": len(union_keys),
        "target_labels": expected_targets,
        "reader": counters,
        "replication_rating_values_opened": False if cohort == "SELECTION" else True,
        "timestamps_opened": False,
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "product_policy_changed": False,
    }
    atomic_json(seal_path, seal)
    validate_labels(contract, p, cohort)
    return seal


def _outer_arrays(
    contract: Mapping[str, Any],
    pre_contract: Mapping[str, Any],
    labels: pd.DataFrame,
    cohort: str,
    outer: str,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    local = labels.loc[labels["outer"].eq(outer)].sort_values("user_key", kind="stable", ignore_index=True)
    bundle_path, _ = prelabel.score_paths(pre_contract, cohort, outer)
    prelabel.validate_score_bundle(bundle_path, _cohort_membership(prelabel.load_membership(pre_contract), cohort), cohort, outer)
    with np.load(bundle_path, allow_pickle=False) as bundle:
        users = bundle["user_keys"].astype(str)
        targets = bundle["target_movie_ids"].astype(np.int64)
        own_top2 = bundle["own_top2"].astype(np.int8)
        own_active = bundle["own_active"].astype(bool)
        shuffle_top2 = bundle["shuffle_top2"].astype(np.int8)
        shuffle_active = bundle["shuffle_active"].astype(bool)
    if not np.array_equal(users, local["user_key"].astype(str).to_numpy()) or not np.array_equal(targets, np.vstack(local["target_movie_ids"])):
        raise ResumeError(f"score/label alignment drift: {cohort} {outer}")
    q = np.vstack(local["target_q"]).astype(np.float64)
    history = local["full_history_count"].to_numpy(dtype=np.int64)
    return users, targets, q, history, own_top2, own_active, shuffle_top2, shuffle_active


def _user_grid(q: np.ndarray, top2: np.ndarray, active: np.ndarray) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    candidates = top2.shape[1]
    model = {metric: np.empty((len(q), candidates), dtype=np.float64) for metric in USER_METRICS}
    random: dict[str, np.ndarray] | None = None
    for column in range(candidates):
        values, baseline = user_metric_arrays(q, top2[:, column], active[:, column])
        for metric in USER_METRICS:
            model[metric][:, column] = values[metric]
        if random is None:
            random = baseline
    assert random is not None
    return model, random


def _item_macro_grid(
    targets: np.ndarray,
    q: np.ndarray,
    top2: np.ndarray,
    active: np.ndarray,
) -> tuple[dict[str, np.ndarray], dict[str, float]]:
    candidates = top2.shape[1]
    model = {metric: np.empty(candidates, dtype=np.float64) for metric in ITEM_METRICS}
    random_result: dict[str, float] | None = None
    for column in range(candidates):
        values, random = item_contribution_arrays(q, top2[:, column], active[:, column])
        for metric in ITEM_METRICS:
            model[metric][column] = equal_movie_mean(targets, values[metric])
        if random_result is None:
            random_result = {metric: equal_movie_mean(targets, random[metric]) for metric in ITEM_METRICS}
    assert random_result is not None
    return model, random_result


def validate_winner(contract: Mapping[str, Any], p: Mapping[str, Path]) -> dict[str, Any]:
    validate_labels(contract, p, "SELECTION")
    value = json.loads(p["winner_seal"].read_text(encoding="utf-8"))
    if (
        value.get("status") != "FROZEN_ONE_SELECTION_WINNER_OR_NONE"
        or value.get("contract") != artifact(DEFAULT)
        or value.get("selection_label_seal") != artifact(p["selection_label_seal"])
        or verify(value["candidate_summaries"]) != p["selection_summary"].resolve()
        or any(value.get(name) is not False for name in ("replication_labels_opened", "timestamps_opened", "locked_test_opened", "final_reserve_opened", "product_policy_changed"))
    ):
        raise ResumeError("winner seal semantic drift")
    summaries = json.loads(p["selection_summary"].read_text(encoding="utf-8"))["candidates"]
    selected = select_winner(summaries)
    expected = selected["candidate_id"] if selected is not None else None
    observed = value["winner"]["candidate_id"] if value.get("winner") is not None else None
    if expected != observed or int(value.get("eligible_count", -1)) != sum(candidate_is_eligible(row) for row in summaries):
        raise ResumeError("winner recomputation drift")
    return value


def select(contract: Mapping[str, Any], p: Mapping[str, Path], resume: bool) -> dict[str, Any]:
    if p["winner_seal"].exists():
        if not resume:
            raise ResumeError("winner seal requires --resume")
        return validate_winner(contract, p)
    if p["selection_summary"].exists():
        raise ResumeError("unsealed selection summary exists")
    _, labels = validate_labels(contract, p, "SELECTION")
    pre_contract, membership = verify_prelabel(contract)
    grid = candidate_grid()
    union_users = sorted(set(labels.loc[labels["outer"].isin(RANDOM_OUTERS), "user_key"].astype(str)))
    user_pos = {key: position for position, key in enumerate(union_users)}
    counts = np.zeros(len(union_users), dtype=np.int16)
    sums = {
        system: {metric: np.zeros((len(union_users), len(grid)), dtype=np.float64) for metric in USER_METRICS}
        for system in SYSTEMS
    }
    folds: dict[str, dict[str, Any]] = {}
    for outer in RANDOM_OUTERS:
        users, targets, q, _, own_top2, own_active, shuffle_top2, shuffle_active = _outer_arrays(
            contract, pre_contract, labels, "SELECTION", outer
        )
        own_user, random_user = _user_grid(q, own_top2, own_active)
        shuffle_user, _ = _user_grid(q, shuffle_top2, shuffle_active)
        own_item, random_item = _item_macro_grid(targets, q, own_top2, own_active)
        shuffle_item, _ = _item_macro_grid(targets, q, shuffle_top2, shuffle_active)
        positions = np.asarray([user_pos[key] for key in users], dtype=np.int64)
        counts[positions] += 1
        for metric in USER_METRICS:
            sums["OWN"][metric][positions] += own_user[metric]
            sums["SHUFFLE"][metric][positions] += shuffle_user[metric]
            sums["RANDOM"][metric][positions] += random_user[metric][:, None]
        unique_movies = len(np.unique(targets))
        folds[outer] = {
            "users": len(users),
            "unique_movies": unique_movies,
            "own_active": own_active.mean(axis=0),
            "shuffle_active": shuffle_active.mean(axis=0),
            "user": {"OWN": own_user, "SHUFFLE": shuffle_user, "RANDOM": random_user},
            "item": {"OWN": own_item, "SHUFFLE": shuffle_item, "RANDOM": random_item},
        }
        progress(p["progress"], "SELECTION_METRICS_OUTER", outer=outer, users=len(users))
    if bool((counts <= 0).any()):
        raise RuntimeError("random pooled selection user coverage drift")
    pooled_user = {
        system: {
            metric: (sums[system][metric] / counts[:, None]).mean(axis=0)
            for metric in USER_METRICS
        }
        for system in SYSTEMS
    }
    total_unique_movies = sum(int(folds[outer]["unique_movies"]) for outer in RANDOM_OUTERS)
    pooled_item = {
        system: {
            metric: np.asarray([
                sum(
                    float(folds[outer]["item"][system][metric][column] if system != "RANDOM" else folds[outer]["item"][system][metric])
                    * int(folds[outer]["unique_movies"])
                    for outer in RANDOM_OUTERS
                ) / total_unique_movies
                for column in range(len(grid))
            ])
            for metric in ITEM_METRICS
        }
        for system in SYSTEMS
    }
    summaries: list[dict[str, Any]] = []
    for column, candidate in enumerate(grid):
        fold_rows: dict[str, Any] = {}
        fold_passes = 0
        fold_harm_random = []
        fold_harm_shuffle = []
        fold_min_q_random = []
        for outer in RANDOM_OUTERS:
            data = folds[outer]
            user_metrics = {
                system: {
                    metric: float(data["user"][system][metric][:, column].mean()) if system != "RANDOM" else float(data["user"][system][metric].mean())
                    for metric in USER_METRICS
                }
                for system in SYSTEMS
            }
            user_benefits = {
                "OWN_VS_RANDOM": {metric: benefit(user_metrics["OWN"][metric], user_metrics["RANDOM"][metric], metric) for metric in USER_METRICS},
                "OWN_VS_SHUFFLE": {metric: benefit(user_metrics["OWN"][metric], user_metrics["SHUFFLE"][metric], metric) for metric in USER_METRICS},
            }
            passed = all(
                user_benefits[contrast][metric] >= 0.0 if metric == "HARM20" else user_benefits[contrast][metric] > 0.0
                for contrast in ("OWN_VS_RANDOM", "OWN_VS_SHUFFLE")
                for metric in USER_METRICS
            )
            fold_passes += int(passed)
            fold_harm_random.append(user_benefits["OWN_VS_RANDOM"]["HARM20"])
            fold_harm_shuffle.append(user_benefits["OWN_VS_SHUFFLE"]["HARM20"])
            fold_min_q_random.append(user_benefits["OWN_VS_RANDOM"]["TOP2_MIN_Q"])
            item_metrics = {
                system: {
                    metric: float(data["item"][system][metric][column] if system != "RANDOM" else data["item"][system][metric])
                    for metric in ITEM_METRICS
                }
                for system in SYSTEMS
            }
            fold_rows[outer] = {
                "own_active_rate": float(data["own_active"][column]),
                "shuffle_active_rate": float(data["shuffle_active"][column]),
                "user_metrics": user_metrics,
                "user_benefits": user_benefits,
                "item_metrics": item_metrics,
                "direction_pass": passed,
            }
        pooled_user_metrics = {
            system: {metric: float(pooled_user[system][metric][column]) for metric in USER_METRICS}
            for system in SYSTEMS
        }
        pooled_item_metrics = {
            system: {metric: float(pooled_item[system][metric][column]) for metric in ITEM_METRICS}
            for system in SYSTEMS
        }
        summary = {
            "candidate_id": candidate.candidate_id,
            "family": candidate.family,
            "encoding": candidate.encoding,
            "k": candidate.k,
            "minimum_fold_own_active_rate": min(float(folds[outer]["own_active"][column]) for outer in RANDOM_OUTERS),
            "minimum_fold_shuffle_active_rate": min(float(folds[outer]["shuffle_active"][column]) for outer in RANDOM_OUTERS),
            "fold_direction_pass_count": fold_passes,
            "minimum_fold_harm_benefit_own_vs_random": min(fold_harm_random),
            "minimum_fold_harm_benefit_own_vs_shuffle": min(fold_harm_shuffle),
            "minimum_fold_min_q_benefit_own_vs_random": min(fold_min_q_random),
            "pooled_user_metrics": pooled_user_metrics,
            "pooled_user_benefits": {
                "OWN_VS_RANDOM": {metric: benefit(pooled_user_metrics["OWN"][metric], pooled_user_metrics["RANDOM"][metric], metric) for metric in USER_METRICS},
                "OWN_VS_SHUFFLE": {metric: benefit(pooled_user_metrics["OWN"][metric], pooled_user_metrics["SHUFFLE"][metric], metric) for metric in USER_METRICS},
            },
            "pooled_item_metrics": pooled_item_metrics,
            "pooled_item_benefits": {
                "OWN_VS_RANDOM": {metric: benefit(pooled_item_metrics["OWN"][metric], pooled_item_metrics["RANDOM"][metric], metric) for metric in ITEM_METRICS},
                "OWN_VS_SHUFFLE": {metric: benefit(pooled_item_metrics["OWN"][metric], pooled_item_metrics["SHUFFLE"][metric], metric) for metric in ITEM_METRICS},
            },
            "folds": fold_rows,
        }
        summary["eligible"] = candidate_is_eligible(summary)
        summaries.append(summary)
    selected = select_winner(summaries)
    summary_value = {
        "status": "COMPLETE_ALL_90_SELECTION_SUMMARIES",
        "candidate_count": len(summaries),
        "selection_scope": list(RANDOM_OUTERS),
        "proxy_outers_used_for_selection": [],
        "unique_pooled_users": len(union_users),
        "unique_pooled_movies": total_unique_movies,
        "candidates": summaries,
    }
    atomic_json(p["selection_summary"], summary_value)
    winner = None if selected is None else {
        key: selected[key]
        for key in ("candidate_id", "family", "encoding", "k", "minimum_fold_own_active_rate", "minimum_fold_shuffle_active_rate", "fold_direction_pass_count", "pooled_user_metrics", "pooled_user_benefits", "pooled_item_metrics", "pooled_item_benefits")
    }
    seal = {
        "status": "FROZEN_ONE_SELECTION_WINNER_OR_NONE",
        "contract": artifact(DEFAULT),
        "selection_label_seal": artifact(p["selection_label_seal"]),
        "candidate_summaries": artifact(p["selection_summary"]),
        "eligible_count": sum(candidate_is_eligible(row) for row in summaries),
        "winner": winner,
        "replication_labels_opened": False,
        "timestamps_opened": False,
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "product_policy_changed": False,
    }
    atomic_json(p["winner_seal"], seal)
    return validate_winner(contract, p)


def _winner_metrics_outer(
    contract: Mapping[str, Any],
    pre_contract: Mapping[str, Any],
    labels: pd.DataFrame,
    outer: str,
    candidate_index: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    users, targets, q, history, own_top2, own_active, shuffle_top2, shuffle_active = _outer_arrays(
        contract, pre_contract, labels, "REPLICATION", outer
    )
    own_user, random_user = user_metric_arrays(q, own_top2[:, candidate_index], own_active[:, candidate_index])
    shuffle_user, _ = user_metric_arrays(q, shuffle_top2[:, candidate_index], shuffle_active[:, candidate_index])
    user_data: dict[str, Any] = {
        "outer": outer,
        "user_key": users,
        "full_history_count": history,
        "own_active": own_active[:, candidate_index],
        "shuffle_active": shuffle_active[:, candidate_index],
    }
    for system, values in (("OWN", own_user), ("SHUFFLE", shuffle_user), ("RANDOM", random_user)):
        for metric in USER_METRICS:
            user_data[f"{system}__{metric}"] = values[metric]
    user_frame = pd.DataFrame(user_data)
    own_item, random_item = item_contribution_arrays(q, own_top2[:, candidate_index], own_active[:, candidate_index])
    shuffle_item, _ = item_contribution_arrays(q, shuffle_top2[:, candidate_index], shuffle_active[:, candidate_index])
    item_data: dict[str, Any] = {
        "outer": np.repeat(outer, targets.size),
        "user_key": np.repeat(users, targets.shape[1]),
        "movie_id": targets.ravel(),
        "q": q.ravel(),
        "low20": (q <= 0.20).ravel(),
    }
    for system, values in (("OWN", own_item), ("SHUFFLE", shuffle_item), ("RANDOM", random_item)):
        for metric in ITEM_METRICS:
            item_data[f"{system}__{metric}"] = values[metric].ravel()
    return user_frame, pd.DataFrame(item_data)


def _user_matrix(frame: pd.DataFrame) -> tuple[list[str], np.ndarray]:
    users = frame["user_key"].astype(str).tolist()
    columns = []
    for model, comparator in CONTRASTS:
        for metric in USER_METRICS:
            columns.append(benefit(frame[f"{model}__{metric}"].to_numpy(), frame[f"{comparator}__{metric}"].to_numpy(), metric))
    return users, np.column_stack(columns)


def _pooled_user_frame(frames: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    value_columns = [f"{system}__{metric}" for system in SYSTEMS for metric in USER_METRICS]
    combined = pd.concat(
        [frames[outer][["user_key", "full_history_count", *value_columns]] for outer in RANDOM_OUTERS],
        ignore_index=True,
    )
    return combined.groupby("user_key", sort=True, observed=True)[["full_history_count", *value_columns]].mean().reset_index()


def _item_estimand(frame: pd.DataFrame) -> ItemEstimand:
    users = sorted(frame["user_key"].astype(str).unique())
    movies = sorted(frame["movie_id"].astype(int).unique())
    up = {key: pos for pos, key in enumerate(users)}
    mp = {movie: pos for pos, movie in enumerate(movies)}
    rows = frame["user_key"].astype(str).map(up).to_numpy(dtype=np.int64)
    cols = frame["movie_id"].astype(int).map(mp).to_numpy(dtype=np.int64)
    counts = frame.groupby("movie_id", sort=False, observed=True).size().to_dict()
    normalizer = frame["movie_id"].map(lambda movie: 1.0 / int(counts[int(movie)])).to_numpy(dtype=np.float64)
    shape = (len(users), len(movies))
    denominator = sparse.coo_matrix((normalizer, (rows, cols)), shape=shape).tocsr()
    matrices = []
    for system in SYSTEMS:
        for metric in ITEM_METRICS:
            data = frame[f"{system}__{metric}"].to_numpy(dtype=np.float64) * normalizer
            matrices.append(sparse.coo_matrix((data, (rows, cols)), shape=shape).tocsr())
    return ItemEstimand(tuple(users), tuple(movies), denominator, tuple(matrices))


def _item_transform() -> np.ndarray:
    base = {(system, metric): pos for pos, (system, metric) in enumerate((s, m) for s in SYSTEMS for m in ITEM_METRICS)}
    transform = np.zeros((len(base), len(CONTRASTS) * len(ITEM_METRICS)), dtype=np.float64)
    column = 0
    for model, comparator in CONTRASTS:
        for metric in ITEM_METRICS:
            sign = -1.0 if metric == "ITEM_LOW" else 1.0
            transform[base[(model, metric)], column] = sign
            transform[base[(comparator, metric)], column] = -sign
            column += 1
    return transform


def _history_band(value: int) -> str:
    if value < 50:
        return "20_TO_49"
    if value < 100:
        return "50_TO_99"
    if value < 500:
        return "100_TO_499"
    return "500_PLUS"


def _occurrence_band(value: int) -> str:
    if value == 1:
        return "1"
    if value <= 4:
        return "2_TO_4"
    if value <= 9:
        return "5_TO_9"
    return "10_PLUS"


def validate_metric_cell(
    contract: Mapping[str, Any],
    p: Mapping[str, Path],
    labels: pd.DataFrame,
    outer: str,
    integrity: Mapping[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    user_path, item_path, _ = metric_paths(contract, outer)
    if (
        integrity.get("status") != "SEALED_REPLICATION_WINNER_OUTER_METRICS"
        or integrity.get("outer") != outer
        or integrity.get("winner_seal") != artifact(p["winner_seal"])
        or integrity.get("replication_label_seal") != artifact(p["replication_label_seal"])
        or verify(integrity["user_metrics"]) != user_path.resolve()
        or verify(integrity["item_occurrences"]) != item_path.resolve()
        or any(
            integrity.get(name) is not False
            for name in ("timestamps_opened", "locked_test_opened", "final_reserve_opened", "product_policy_changed")
        )
    ):
        raise ResumeError(f"replication metric integrity drift: {outer}")
    local_labels = labels.loc[labels["outer"].eq(outer)].sort_values("user_key", kind="stable", ignore_index=True)
    users = pd.read_parquet(user_path).sort_values("user_key", kind="stable", ignore_index=True)
    items = pd.read_parquet(item_path).sort_values(["user_key", "movie_id"], kind="stable", ignore_index=True)
    value_columns = [f"{system}__{metric}" for system in SYSTEMS for metric in USER_METRICS]
    contribution_columns = [f"{system}__{metric}" for system in SYSTEMS for metric in ITEM_METRICS]
    expected_pairs = {
        (str(row.user_key), int(movie))
        for row in local_labels.itertuples(index=False)
        for movie in row.target_movie_ids
    }
    observed_pairs = set(zip(items["user_key"].astype(str), items["movie_id"].astype(int), strict=True))
    if (
        len(users) != len(local_labels)
        or users["user_key"].astype(str).tolist() != local_labels["user_key"].astype(str).tolist()
        or users["user_key"].duplicated().any()
        or not np.isfinite(users[value_columns].to_numpy(dtype=np.float64)).all()
        or users["own_active"].dtype != bool
        or users["shuffle_active"].dtype != bool
        or len(items) != len(expected_pairs)
        or items.duplicated(["user_key", "movie_id"]).any()
        or observed_pairs != expected_pairs
        or not np.isfinite(items[["q", *contribution_columns]].to_numpy(dtype=np.float64)).all()
    ):
        raise ResumeError(f"replication metric semantic drift: {outer}")
    return users, items


def validate_metric_seal(
    contract: Mapping[str, Any], p: Mapping[str, Path]
) -> tuple[dict[str, Any], dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    _, labels = validate_labels(contract, p, "REPLICATION")
    seal = json.loads(p["replication_metric_seal"].read_text(encoding="utf-8"))
    if (
        seal.get("status") != "SEALED_REPLICATION_WINNER_USER_AND_ITEM_METRICS"
        or seal.get("winner_seal") != artifact(p["winner_seal"])
        or seal.get("replication_label_seal") != artifact(p["replication_label_seal"])
        or len(seal.get("outer_integrities", [])) != 7
        or any(seal.get(name) is not False for name in ("timestamps_opened", "locked_test_opened", "final_reserve_opened", "product_policy_changed"))
    ):
        raise ResumeError("replication metric seal drift")
    observed = set()
    users: dict[str, pd.DataFrame] = {}
    items: dict[str, pd.DataFrame] = {}
    for spec in seal["outer_integrities"]:
        integrity_path = verify(spec)
        integrity = json.loads(integrity_path.read_text(encoding="utf-8"))
        outer = str(integrity.get("outer"))
        if outer not in OUTERS or outer in observed or integrity_path != metric_paths(contract, outer)[2].resolve():
            raise ResumeError("replication metric outer coverage drift")
        users[outer], items[outer] = validate_metric_cell(contract, p, labels, outer, integrity)
        observed.add(outer)
    if observed != set(OUTERS):
        raise ResumeError("replication metric outer set drift")
    return seal, users, items


def validate_final(contract: Mapping[str, Any], p: Mapping[str, Path]) -> dict[str, Any]:
    validate_winner(contract, p)
    validate_labels(contract, p, "REPLICATION")
    validate_metric_seal(contract, p)
    value = json.loads(p["final"].read_text(encoding="utf-8"))
    if (
        value.get("status") != "COMPLETE_REC_EV_029_DISJOINT_REPLICATION"
        or value.get("contract") != artifact(DEFAULT)
        or value.get("winner_seal") != artifact(p["winner_seal"])
        or value.get("replication_label_seal") != artifact(p["replication_label_seal"])
        or any(value.get(name) is not False for name in ("timestamps_opened", "future_reserve_opened", "locked_test_opened", "final_reserve_opened", "product_policy_changed"))
    ):
        raise ResumeError("final analysis semantic drift")
    for name in ("inference", "user_segments", "movie_segments", "user_bootstrap", "item_bootstrap", "metric_seal"):
        verify(value[name])
    inference = json.loads(p["inference"].read_text(encoding="utf-8"))
    if (
        inference.get("status") != "COMPLETE_72_USER_AND_48_ITEM_SIMULTANEOUS_MAX_T"
        or int(inference.get("user_family_size", -1)) != 72
        or int(inference.get("item_family_size", -1)) != 48
        or len(inference.get("user_intervals", [])) != 72
        or len(inference.get("item_intervals", [])) != 48
        or set(inference.get("truth_by_estimand", {})) != set(ESTIMANDS)
        or value.get("truth_by_estimand") != inference.get("truth_by_estimand")
        or value.get("primary_status") != (
            "REPLICATED_BIAS_FREE_PERSONALIZATION"
            if inference["truth_by_estimand"]["RANDOM_POOLED"]
            else "DIRECT_PROFILE_PERSONALIZATION_NOT_REPLICATED"
        )
    ):
        raise ResumeError("replication inference semantic drift")
    return value


def replicate(contract: Mapping[str, Any], p: Mapping[str, Path], resume: bool) -> dict[str, Any]:
    if p["final"].exists():
        if not resume:
            raise ResumeError("final analysis requires --resume")
        return validate_final(contract, p)
    winner_seal = validate_winner(contract, p)
    winner = winner_seal.get("winner")
    if winner is None:
        raise ResumeError("replication forbidden because selection froze no winner")
    _, labels = validate_labels(contract, p, "REPLICATION")
    pre_contract, _ = verify_prelabel(contract)
    candidate_ids = [candidate.candidate_id for candidate in candidate_grid()]
    candidate_index = candidate_ids.index(str(winner["candidate_id"]))
    user_frames: dict[str, pd.DataFrame] = {}
    item_frames: dict[str, pd.DataFrame] = {}
    integrities = []
    for outer in OUTERS:
        user_path, item_path, integrity_path = metric_paths(contract, outer)
        if user_path.exists() and item_path.exists() and integrity_path.exists():
            if not resume:
                raise ResumeError(f"sealed replication metrics require --resume: {outer}")
            integrity = json.loads(integrity_path.read_text(encoding="utf-8"))
            user_frame, item_frame = validate_metric_cell(contract, p, labels, outer, integrity)
        elif user_path.exists() or item_path.exists() or integrity_path.exists():
            raise ResumeError(f"partial replication metric state: {outer}")
        else:
            user_frame, item_frame = _winner_metrics_outer(contract, pre_contract, labels, outer, candidate_index)
            atomic_parquet(user_path, user_frame)
            atomic_parquet(item_path, item_frame)
            integrity = {
                "status": "SEALED_REPLICATION_WINNER_OUTER_METRICS",
                "outer": outer,
                "winner_seal": artifact(p["winner_seal"]),
                "replication_label_seal": artifact(p["replication_label_seal"]),
                "user_metrics": artifact(user_path),
                "item_occurrences": artifact(item_path),
                "timestamps_opened": False,
                "locked_test_opened": False,
                "final_reserve_opened": False,
                "product_policy_changed": False,
            }
            atomic_json(integrity_path, integrity)
            user_frame, item_frame = validate_metric_cell(contract, p, labels, outer, integrity)
        user_frames[outer], item_frames[outer] = user_frame, item_frame
        integrities.append(artifact(integrity_path))
        progress(p["progress"], "REPLICATION_METRICS_OUTER", outer=outer, users=len(user_frame), occurrences=len(item_frame))
    metric_seal = {
        "status": "SEALED_REPLICATION_WINNER_USER_AND_ITEM_METRICS",
        "winner_seal": artifact(p["winner_seal"]),
        "replication_label_seal": artifact(p["replication_label_seal"]),
        "outer_integrities": integrities,
        "timestamps_opened": False,
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "product_policy_changed": False,
    }
    atomic_json(p["replication_metric_seal"], metric_seal)
    pooled_user = _pooled_user_frame(user_frames)
    user_matrices = {outer: _user_matrix(user_frames[outer].sort_values("user_key", kind="stable")) for outer in OUTERS}
    user_matrices["RANDOM_POOLED"] = _user_matrix(pooled_user)
    union_users = sorted(set().union(*(set(keys) for keys, _ in user_matrices.values())))
    user_boot = user_multiplier_max_t(
        union_users=union_users,
        matrices={estimand: user_matrices[estimand] for estimand in ESTIMANDS},
        repeats=5000,
        seed=20261006,
        confidence=0.95,
    )
    atomic_npz(p["user_bootstrap"], **{key: np.asarray(value) for key, value in user_boot.items() if key != "critical"}, critical=np.asarray(user_boot["critical"]))
    pooled_item = pd.concat([item_frames[outer] for outer in RANDOM_OUTERS], ignore_index=True)
    item_inputs = {outer: _item_estimand(item_frames[outer]) for outer in OUTERS}
    item_inputs["RANDOM_POOLED"] = _item_estimand(pooled_item)
    union_movies = sorted(set().union(*(set(value.movie_ids) for value in item_inputs.values())))
    item_boot = item_multiway_multiplier_max_t(
        union_users=union_users,
        union_movies=union_movies,
        estimands={estimand: item_inputs[estimand] for estimand in ESTIMANDS},
        repeats=5000,
        seed=20261007,
        confidence=0.95,
        linear_transform=_item_transform(),
    )
    atomic_npz(p["item_bootstrap"], **{key: np.asarray(value) for key, value in item_boot.items() if key != "critical"}, critical=np.asarray(item_boot["critical"]))
    user_descriptions = [
        {"estimand": estimand, "model": model, "comparator": comparator, "metric": metric}
        for estimand in ESTIMANDS for model, comparator in CONTRASTS for metric in USER_METRICS
    ]
    item_descriptions = [
        {"estimand": estimand, "model": model, "comparator": comparator, "metric": metric}
        for estimand in ESTIMANDS for model, comparator in CONTRASTS for metric in ITEM_METRICS
    ]
    user_intervals = [
        {**description, "point": float(user_boot["point"][i]), "low": float(user_boot["low"][i]), "high": float(user_boot["high"][i])}
        for i, description in enumerate(user_descriptions)
    ]
    item_intervals = [
        {**description, "point": float(item_boot["point"][i]), "low": float(item_boot["low"][i]), "high": float(item_boot["high"][i])}
        for i, description in enumerate(item_descriptions)
    ]
    def lower(rows: list[dict[str, Any]], estimand: str, model: str, comparator: str, metric: str) -> float:
        matches = [row for row in rows if row["estimand"] == estimand and row["model"] == model and row["comparator"] == comparator and row["metric"] == metric]
        if len(matches) != 1:
            raise RuntimeError("interval lookup drift")
        return float(matches[0]["low"])
    active_rates: dict[str, dict[str, float]] = {}
    truth: dict[str, bool] = {}
    for estimand in ESTIMANDS:
        if estimand == "RANDOM_POOLED":
            active = pd.concat([user_frames[outer][["user_key", "own_active", "shuffle_active"]] for outer in RANDOM_OUTERS])
            grouped = active.groupby("user_key", sort=True, observed=True)[["own_active", "shuffle_active"]].all()
            own_rate, shuffle_rate = float(grouped["own_active"].mean()), float(grouped["shuffle_active"].mean())
        else:
            own_rate = float(user_frames[estimand]["own_active"].mean())
            shuffle_rate = float(user_frames[estimand]["shuffle_active"].mean())
        active_rates[estimand] = {"OWN": own_rate, "SHUFFLE": shuffle_rate}
        user_pass = all(
            lower(user_intervals, estimand, "OWN", comparator, metric) >= 0.0 if metric == "HARM20" else lower(user_intervals, estimand, "OWN", comparator, metric) > 0.0
            for comparator in ("RANDOM", "SHUFFLE") for metric in USER_METRICS
        )
        item_pass = all(
            lower(item_intervals, estimand, "OWN", comparator, metric) >= 0.0 if metric == "ITEM_LOW" else lower(item_intervals, estimand, "OWN", comparator, metric) > 0.0
            for comparator in ("RANDOM", "SHUFFLE") for metric in ITEM_METRICS
        )
        truth[estimand] = own_rate >= 0.95 and shuffle_rate >= 0.95 and user_pass and item_pass
    inference = {
        "status": "COMPLETE_72_USER_AND_48_ITEM_SIMULTANEOUS_MAX_T",
        "winner": winner,
        "user_family_size": len(user_intervals),
        "item_family_size": len(item_intervals),
        "user_critical": float(user_boot["critical"]),
        "item_critical": float(item_boot["critical"]),
        "user_intervals": user_intervals,
        "item_intervals": item_intervals,
        "active_rates": active_rates,
        "truth_by_estimand": truth,
    }
    atomic_json(p["inference"], inference)
    user_segment_rows = []
    segment_user_frames = {**user_frames, "RANDOM_POOLED": pooled_user}
    for outer, frame in segment_user_frames.items():
        local = frame.copy()
        local["segment"] = local["full_history_count"].map(_history_band)
        for segment, group in local.groupby("segment", sort=True, observed=True):
            for system in SYSTEMS:
                row = {"outer": outer, "history_band": segment, "system": system, "users": len(group)}
                for metric in USER_METRICS:
                    row[metric] = float(group[f"{system}__{metric}"].mean())
                user_segment_rows.append(row)
    atomic_parquet(p["user_segments"], pd.DataFrame(user_segment_rows))
    movie_segment_rows = []
    segment_item_frames = {**item_frames, "RANDOM_POOLED": pooled_item}
    for estimand, frame in segment_item_frames.items():
        counts_by_movie = frame.groupby("movie_id", sort=True, observed=True).size()
        local = frame.copy()
        local["occurrence_band"] = local["movie_id"].map(lambda movie: _occurrence_band(int(counts_by_movie.loc[int(movie)])))
        for band, group in local.groupby("occurrence_band", sort=True, observed=True):
            movies = int(group["movie_id"].nunique())
            for system in SYSTEMS:
                row = {"estimand": estimand, "occurrence_band": band, "system": system, "movies": movies, "occurrences": len(group)}
                for metric in ITEM_METRICS:
                    row[metric] = equal_movie_mean(group["movie_id"].to_numpy(), group[f"{system}__{metric}"].to_numpy())
                movie_segment_rows.append(row)
    atomic_parquet(p["movie_segments"], pd.DataFrame(movie_segment_rows))
    final = {
        "status": "COMPLETE_REC_EV_029_DISJOINT_REPLICATION",
        "contract": artifact(DEFAULT),
        "winner_seal": artifact(p["winner_seal"]),
        "replication_label_seal": artifact(p["replication_label_seal"]),
        "metric_seal": artifact(p["replication_metric_seal"]),
        "inference": artifact(p["inference"]),
        "user_segments": artifact(p["user_segments"]),
        "movie_segments": artifact(p["movie_segments"]),
        "user_bootstrap": artifact(p["user_bootstrap"]),
        "item_bootstrap": artifact(p["item_bootstrap"]),
        "winner": winner,
        "truth_by_estimand": truth,
        "primary_status": "REPLICATED_BIAS_FREE_PERSONALIZATION" if truth["RANDOM_POOLED"] else "DIRECT_PROFILE_PERSONALIZATION_NOT_REPLICATED",
        "timestamps_opened": False,
        "future_reserve_opened": False,
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "product_policy_changed": False,
    }
    atomic_json(p["final"], final)
    return validate_final(contract, p)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT)
    parser.add_argument("--phase", choices=("selection-labels", "select", "replication-labels", "replicate", "all"), default="all")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    contract = load_contract(args.contract)
    p = paths(contract)
    if args.phase in {"selection-labels", "all"}:
        open_labels(contract, p, "SELECTION", args.resume)
    if args.phase in {"select", "all"}:
        winner = select(contract, p, args.resume)
        if winner.get("winner") is None and args.phase == "all":
            print(json.dumps({"status": "NO_ELIGIBLE_WINNER_REPLICATION_REMAINS_CLOSED"}))
            return
    if args.phase in {"replication-labels", "all"}:
        open_labels(contract, p, "REPLICATION", args.resume)
    if args.phase in {"replicate", "all"}:
        replicate(contract, p, args.resume)
    print(json.dumps({"status": "OK", "phase": args.phase}, ensure_ascii=False))


if __name__ == "__main__":
    main()
