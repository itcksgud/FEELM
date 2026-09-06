"""Open REC-EV-028 labels once and run the frozen dual-estimand analysis."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Iterable, Mapping, Sequence
import zipfile

import numpy as np
import pandas as pd
from scipy import sparse

from rec_ev_022a_core import user_key
from rec_ev_027_core import analytic_random_top2, full_history_q, ranked_top2_metrics, rating_index
from rec_ev_028b_core import (
    CONTRASTS,
    ESTIMANDS,
    ITEM_METRICS,
    USER_METRICS,
    ItemEstimand,
    benefit,
    classify_signal,
    item_multiway_multiplier_max_t,
    user_multiplier_max_t,
)
from validate_rec_ev_028b_contract import DEFAULT as CONTRACT_DEFAULT
from validate_rec_ev_028b_contract import ROOT, validate as validate_contract


OUTERS = (
    ("R0", "RANDOM_ITEM_COLD", "0"),
    ("R1", "RANDOM_ITEM_COLD", "1"),
    ("R2", "RANDOM_ITEM_COLD", "2"),
    ("R3", "RANDOM_ITEM_COLD", "3"),
    ("R4", "RANDOM_ITEM_COLD", "4"),
    ("KR", "KOREAN_ORIGIN_COLD", "KR"),
    ("RECENT", "RELEASE_2020_2023_COLD", "2020_2023"),
)
SYSTEMS = ("FULL_PERSONALIZED", "BIAS_ONLY", "DOT_ONLY", "PROFILE_SHUFFLE", "STRUCTURED_DIRECT")
ALL_SYSTEMS = (*SYSTEMS, "RANDOM_EXPECTATION")
ITEM_BASE_SYSTEMS = (
    "FULL_PERSONALIZED",
    "BIAS_ONLY",
    "PROFILE_SHUFFLE",
    "STRUCTURED_DIRECT",
    "RANDOM_EXPECTATION",
)
CELLS = (
    "PERCENTILE_MAGNITUDE_K8",
    "BINARY_SIGN_K8",
    "PERCENTILE_MAGNITUDE_K4",
    "PERCENTILE_MAGNITUDE_K12",
)
PRIMARY_CELL = "PERCENTILE_MAGNITUDE_K8"
MAX_USER_ID = 200_948


class ResumeError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    try:
        display = resolved.relative_to(ROOT).as_posix()
    except ValueError:
        display = resolved.as_posix()
    return {"path": display, "bytes": resolved.stat().st_size, "sha256": sha256_file(resolved)}


def verify_artifact(spec: Mapping[str, Any]) -> Path:
    raw = Path(str(spec["path"]))
    path = raw if raw.is_absolute() else ROOT / raw
    if not path.is_file() or path.stat().st_size != int(spec["bytes"]) or sha256_file(path) != str(spec["sha256"]):
        raise ResumeError(f"artifact drift: {path}")
    return path.resolve()


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def atomic_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(temporary, index=False)
    temporary.replace(path)


def atomic_npz(path: Path, **arrays: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        np.savez_compressed(handle, **arrays)
    temporary.replace(path)


def progress(path: Path, phase: str, **extra: Any) -> None:
    value = {"phase": phase, "updated_at_unix": time.time(), **extra}
    atomic_json(path, value)
    print(json.dumps(value, ensure_ascii=False), flush=True)


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    validate_contract(value, path.resolve())
    return value


def paths(contract: Mapping[str, Any]) -> dict[str, Path]:
    root = ROOT / str(contract["output_root"])
    return {
        "root": root,
        "progress": root / "progress.json",
        "labels": root / "labels/target-labels.parquet",
        "label_seal": root / "labels/label-seal.json",
        "metrics_seal": root / "metrics/metrics-seal.json",
        "user_boot": root / "analysis/user-bootstrap-estimates.npz",
        "item_boot": root / "analysis/item-bootstrap-estimates.npz",
        "inference": root / "analysis/simultaneous-inference.json",
        "sensitivity": root / "analysis/input-policy-sensitivity.json",
        "user_segments": root / "analysis/user-segments.parquet",
        "movie_segments": root / "analysis/movie-segments.parquet",
        "analysis": root / "analysis/final-analysis.json",
    }


def outer_paths(contract: Mapping[str, Any], slug: str) -> dict[str, Path]:
    prelabel = ROOT / str(contract["prelabel_execution"]["root"])
    output = ROOT / str(contract["output_root"]) / "outers" / slug
    return {
        "score_integrity": prelabel / "outers" / slug / "scores/integrity.json",
        "scores": prelabel / "outers" / slug / "scores/score-ranks.parquet",
        "user_metrics": output / "user-metrics.parquet",
        "item_occurrences": output / "item-occurrences.parquet",
    }


def verify_prelabel(contract: Mapping[str, Any]) -> dict[str, Path]:
    verify_artifact(contract["prelabel_execution"]["protocol_lock"])
    seal_path = verify_artifact(contract["prelabel_execution"]["global_score_seal"])
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    required_false = (
        "target_rating_values_opened",
        "timestamps_opened",
        "future_movie_membership_opened",
        "locked_test_opened",
        "final_reserve_opened",
        "product_policy_changed",
    )
    if (
        seal.get("status") != "GLOBAL_PRELABEL_SCORE_SEAL_COMPLETE"
        or int(seal.get("rows", -1)) != 297_780
        or seal.get("systems") != list(SYSTEMS)
        or seal.get("cells") != list(CELLS)
        or any(seal.get(key) is not False for key in required_false)
    ):
        raise ResumeError("global pre-label score seal semantic drift")
    expected = {slug for slug, _, _ in OUTERS}
    observed: dict[str, Path] = {}
    for spec in seal["outer_integrities"]:
        integrity_path = verify_artifact(spec)
        integrity = json.loads(integrity_path.read_text(encoding="utf-8"))
        slug = str(integrity.get("outer"))
        if slug not in expected or slug in observed or integrity.get("status") != "SEALED_ALL_NONRANDOM_SYSTEMS_AND_CELLS_BEFORE_TARGET_LABELS":
            raise ResumeError("outer score integrity semantic drift")
        if any(integrity.get(key) is not False for key in required_false[:3]):
            raise ResumeError("outer score firewall drift")
        score_path = verify_artifact(integrity["score_ranks"])
        if score_path != outer_paths(contract, slug)["scores"].resolve():
            raise ResumeError("outer score path drift")
        observed[slug] = score_path
    if set(observed) != expected:
        raise ResumeError("outer score seal coverage drift")
    verify_artifact(contract["prelabel_execution"]["membership"])
    verify_artifact(contract["prelabel_execution"]["membership_preflight"])
    archive = verify_artifact(contract["movielens_archive"])
    return {**observed, "archive": archive, "seal": seal_path}


def _zip_member(bundle: zipfile.ZipFile, suffix: str) -> str:
    matches = [name for name in bundle.namelist() if name.endswith(suffix)]
    if len(matches) != 1:
        raise RuntimeError(f"archive member ambiguity: {suffix}")
    return matches[0]


def _parse_user(raw: bytes) -> tuple[int, int]:
    first = raw.find(b",")
    if first <= 0:
        raise RuntimeError("malformed rating row before user firewall")
    return int(raw[:first]), first


def _parse_movie(raw: bytes, first: int) -> tuple[int, int]:
    second = raw.find(b",", first + 1)
    if second <= first + 1:
        raise RuntimeError("malformed evaluation movie field")
    return int(raw[first + 1 : second]), second


def reverse_user_keys(keys: Sequence[str]) -> dict[int, int]:
    wanted = {str(key): index for index, key in enumerate(keys)}
    result: dict[int, int] = {}
    for uid in range(1, MAX_USER_ID + 1):
        row = wanted.get(user_key(uid))
        if row is not None:
            phase = int.from_bytes(
                hashlib.sha256(f"rec-ev-028-user-phase-v1|{uid}".encode("utf-8")).digest(), "big"
            ) % 10_000
            if not 8000 <= phase <= 8999:
                raise RuntimeError("membership user outside attribution evaluation phase")
            result[uid] = row
    if len(result) != len(wanted):
        raise RuntimeError("evaluation user reversal coverage drift")
    return result


def _load_membership(contract: Mapping[str, Any]) -> pd.DataFrame:
    path = verify_artifact(contract["prelabel_execution"]["membership"])
    frame = pd.read_parquet(path).sort_values(["outer", "user_key"], kind="stable", ignore_index=True)
    if len(frame) != 14_889 or frame.duplicated(["outer", "user_key"]).any():
        raise ResumeError("membership row-set drift")
    return frame


def _expected_target_count(membership: pd.DataFrame) -> int:
    return int(sum(len(values) for values in membership["target_movie_ids"]))


def validate_label_seal(contract: Mapping[str, Any], p: Mapping[str, Path]) -> tuple[dict[str, Any], pd.DataFrame]:
    verify_prelabel(contract)
    membership = _load_membership(contract)
    seal = json.loads(p["label_seal"].read_text(encoding="utf-8"))
    expected_targets = _expected_target_count(membership)
    required = {
        "status": "OPENED_REC_EV_028_ATTRIBUTION_LABELS_ONCE_AFTER_GLOBAL_SCORE_SEAL",
        "membership_rows": len(membership),
        "union_users": int(membership["user_key"].nunique()),
        "target_labels": expected_targets,
        "timestamps_opened": False,
        "future_movie_membership_opened": False,
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "product_policy_changed": False,
    }
    for key, expected in required.items():
        if seal.get(key) != expected:
            raise ResumeError(f"label seal semantic drift: {key}")
    if verify_artifact(seal["contract"]) != CONTRACT_DEFAULT.resolve():
        raise ResumeError("label seal contract path drift")
    if seal["contract"] != artifact(CONTRACT_DEFAULT):
        raise ResumeError("label seal contract identity drift")
    expected_prelabel = contract["prelabel_execution"]["global_score_seal"]
    if seal.get("prelabel_score_seal") != expected_prelabel or verify_artifact(seal["prelabel_score_seal"]) != verify_artifact(expected_prelabel):
        raise ResumeError("label seal prelabel identity drift")
    if verify_artifact(seal["labels"]) != p["labels"].resolve():
        raise ResumeError("label artifact path drift")
    reader = seal.get("reader", {})
    if (
        int(reader.get("raw_rows", -1)) != 32_000_204
        or int(reader.get("evaluation_user_rating_values_parsed", 0)) <= 0
        or int(reader.get("target_rating_values_parsed", -1)) != expected_targets
        or int(reader.get("timestamps_parsed", -1)) != 0
        or int(reader.get("future_movie_membership_opened", -1)) != 0
        or int(reader.get("non_evaluation_rows_discarded_after_user_id", -1))
        + int(reader.get("evaluation_user_rating_values_parsed", -1))
        != 32_000_204
    ):
        raise ResumeError("label reader evidence drift")
    labels = pd.read_parquet(p["labels"]).sort_values(["outer", "user_key"], kind="stable", ignore_index=True)
    scalar = ["outer", "track", "fold_or_domain", "user_key"]
    if len(labels) != len(membership) or labels.duplicated(["outer", "user_key"]).any():
        raise ResumeError("label row-set drift")
    if not labels[scalar].equals(membership[scalar].reset_index(drop=True)):
        raise ResumeError("label membership key drift")
    for label_row, member_row in zip(labels.itertuples(index=False), membership.itertuples(index=False), strict=True):
        movies = list(map(int, label_row.target_movie_ids))
        if movies != list(map(int, member_row.target_movie_ids)):
            raise ResumeError("label target order drift")
        indices = np.asarray(label_row.target_rating_indices)
        histogram = np.asarray(label_row.full_history_histogram)
        q = np.asarray(label_row.target_q_eval, dtype=np.float64)
        if (
            indices.dtype.kind not in "iu"
            or indices.shape != (len(movies),)
            or bool((indices < 0).any())
            or bool((indices > 9).any())
            or histogram.dtype.kind not in "iu"
            or histogram.shape != (10,)
            or bool((histogram < 0).any())
            or int(histogram.sum()) != int(label_row.full_history_count)
            or int(label_row.full_history_count) <= 0
            or q.shape != (len(movies),)
            or not np.isfinite(q).all()
            or bool((q < 0.0).any())
            or bool((q > 1.0).any())
            or not np.array_equal(q, full_history_q(indices, histogram))
        ):
            raise ResumeError("label payload semantic drift")
    return seal, labels


def validate_metrics_seal(contract: Mapping[str, Any], p: Mapping[str, Path]) -> dict[str, Any]:
    _, labels = validate_label_seal(contract, p)
    seal = json.loads(p["metrics_seal"].read_text(encoding="utf-8"))
    expected_user_rows = len(labels) * len(CELLS) * len(ALL_SYSTEMS)
    expected_item_rows = _expected_target_count(labels)
    required = {
        "status": "SEALED_USER_METRICS_AND_PRIMARY_ITEM_OCCURRENCES",
        "user_metric_rows": expected_user_rows,
        "item_occurrence_rows": expected_item_rows,
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "product_policy_changed": False,
    }
    for key, expected in required.items():
        if seal.get(key) != expected:
            raise ResumeError(f"metrics seal semantic drift: {key}")
    if seal.get("label_seal") != artifact(p["label_seal"]):
        raise ResumeError("metrics seal label identity drift")
    expected_paths = []
    for slug, _, _ in OUTERS:
        op = outer_paths(contract, slug)
        expected_paths.extend((op["user_metrics"].resolve(), op["item_occurrences"].resolve()))
    observed_paths = [verify_artifact(spec) for spec in seal.get("artifacts", [])]
    if observed_paths != expected_paths:
        raise ResumeError("metrics artifact set/order drift")
    for slug, _, _ in OUTERS:
        local_labels = labels.loc[labels["outer"].eq(slug)].sort_values("user_key", kind="stable", ignore_index=True)
        op = outer_paths(contract, slug)
        users = pd.read_parquet(op["user_metrics"])
        if (
            len(users) != len(local_labels) * len(CELLS) * len(ALL_SYSTEMS)
            or users.duplicated(["user_key", "cell", "system"]).any()
            or set(users["user_key"].astype(str)) != set(local_labels["user_key"].astype(str))
            or set(users["cell"].astype(str)) != set(CELLS)
            or set(users["system"].astype(str)) != set(ALL_SYSTEMS)
            or users["active"].dtype != bool
            or not np.isfinite(users[list(USER_METRICS) + ["GOOD80"]].to_numpy(dtype=np.float64)).all()
        ):
            raise ResumeError(f"user metric semantic drift: {slug}")
        random_rows = users.loc[users["system"].eq("RANDOM_EXPECTATION")]
        if not random_rows["active"].all() or not random_rows["used_random_expectation"].all():
            raise ResumeError(f"random user metric semantics drift: {slug}")
        items = pd.read_parquet(op["item_occurrences"])
        expected_pairs = {
            (str(row.user_key), int(movie))
            for row in local_labels.itertuples(index=False)
            for movie in row.target_movie_ids
        }
        observed_pairs = set(zip(items["user_key"].astype(str), items["movie_id"].astype(int), strict=True))
        contribution_columns = [
            f"{prefix}__{system}"
            for system in ALL_SYSTEMS
            for prefix in ("utility", "low")
        ]
        active_columns = [f"active__{system}" for system in ALL_SYSTEMS]
        if (
            len(items) != len(expected_pairs)
            or items.duplicated(["user_key", "movie_id"]).any()
            or observed_pairs != expected_pairs
            or not np.isfinite(items[["q_eval", *contribution_columns]].to_numpy(dtype=np.float64)).all()
            or any(items[column].dtype != bool for column in active_columns)
        ):
            raise ResumeError(f"item occurrence semantic drift: {slug}")
    return seal


def validate_final_analysis(contract: Mapping[str, Any], p: Mapping[str, Path]) -> dict[str, Any]:
    validate_metrics_seal(contract, p)
    value = json.loads(p["analysis"].read_text(encoding="utf-8"))
    if value.get("status") != "COMPLETE_REC_EV_028B_LABEL_AND_ATTRIBUTION_ANALYSIS":
        raise ResumeError("analysis status drift")
    if value.get("contract") != artifact(CONTRACT_DEFAULT):
        raise ResumeError("analysis contract identity drift")
    if value.get("label_seal") != artifact(p["label_seal"]) or value.get("metrics_seal") != artifact(p["metrics_seal"]):
        raise ResumeError("analysis upstream identity drift")
    expected_paths = {
        "inference": p["inference"].resolve(),
        "sensitivity": p["sensitivity"].resolve(),
        "user_segments": p["user_segments"].resolve(),
        "movie_segments": p["movie_segments"].resolve(),
        "user_bootstrap": p["user_boot"].resolve(),
        "item_bootstrap": p["item_boot"].resolve(),
    }
    for key, expected in expected_paths.items():
        if verify_artifact(value[key]) != expected:
            raise ResumeError(f"analysis artifact path drift: {key}")
    if set(value.get("truth_by_estimand", {})) != set(ESTIMANDS):
        raise ResumeError("analysis estimand truth coverage drift")
    for key in ("future_reserve_opened", "locked_test_opened", "final_reserve_opened", "product_policy_changed"):
        if value.get(key) is not False:
            raise ResumeError(f"analysis firewall drift: {key}")
    inference = json.loads(p["inference"].read_text(encoding="utf-8"))
    if (
        inference.get("status") != "COMPLETE_DUAL_USER_AND_ITEM_MACRO_MAX_T"
        or inference.get("primary_cell") != PRIMARY_CELL
        or int(inference.get("repeats", -1)) != 5000
        or int(inference.get("user_family_size", -1)) != 168
        or int(inference.get("item_family_size", -1)) != 112
        or len(inference.get("user_intervals", [])) != 168
        or len(inference.get("item_intervals", [])) != 112
        or set(inference.get("truth_by_estimand", {})) != set(ESTIMANDS)
    ):
        raise ResumeError("analysis inference semantic drift")
    return value


def open_labels(contract: Mapping[str, Any], p: Mapping[str, Path], resume: bool) -> dict[str, Any]:
    if p["label_seal"].exists():
        if not resume:
            raise ResumeError("sealed labels require --resume")
        seal, _ = validate_label_seal(contract, p)
        return seal
    if p["labels"].exists():
        raise ResumeError("unsealed label artifact exists; independent recovery audit required")
    verified = verify_prelabel(contract)
    membership = _load_membership(contract)
    union_keys = sorted(set(membership["user_key"].astype(str)))
    key_to_union = {key: index for index, key in enumerate(union_keys)}
    raw_to_union = reverse_user_keys(union_keys)
    appearances: dict[int, list[tuple[int, int]]] = defaultdict(list)
    target_slots: dict[tuple[int, int], dict[int, int]] = {}
    target_values: dict[tuple[int, int], np.ndarray] = {}
    for row_index, row in enumerate(membership.itertuples(index=False)):
        union_row = key_to_union[str(row.user_key)]
        appearances[union_row].append((row_index, len(row.target_movie_ids)))
        key = (row_index, len(row.target_movie_ids))
        target_slots[key] = {int(movie): slot for slot, movie in enumerate(row.target_movie_ids)}
        target_values[key] = np.full(len(row.target_movie_ids), -1, dtype=np.int8)
    full_hist = np.zeros((len(union_keys), 10), dtype=np.uint32)
    counters = {
        "raw_rows": 0,
        "non_evaluation_rows_discarded_after_user_id": 0,
        "evaluation_user_rating_values_parsed": 0,
        "target_rating_values_parsed": 0,
        "timestamps_parsed": 0,
        "future_movie_membership_opened": 0,
    }
    progress(p["progress"], "OPEN_LABELS_AFTER_VERIFIED_GLOBAL_SCORE_SEAL", users=len(union_keys))
    with zipfile.ZipFile(verified["archive"]) as bundle, bundle.open(_zip_member(bundle, "ratings.csv")) as handle:
        if handle.readline().rstrip(b"\r\n") != b"userId,movieId,rating,timestamp":
            raise RuntimeError("MovieLens ratings header drift")
        for raw in handle:
            counters["raw_rows"] += 1
            uid, first = _parse_user(raw)
            union_row = raw_to_union.get(uid)
            if union_row is None:
                counters["non_evaluation_rows_discarded_after_user_id"] += 1
                continue
            movie, second = _parse_movie(raw, first)
            third = raw.find(b",", second + 1)
            if third <= second + 1:
                raise RuntimeError("malformed evaluation rating field")
            index = rating_index(raw[second + 1 : third])
            full_hist[union_row, index] += 1
            counters["evaluation_user_rating_values_parsed"] += 1
            for row_index, target_n in appearances[union_row]:
                key = (row_index, target_n)
                slot = target_slots[key].get(movie)
                if slot is not None:
                    if target_values[key][slot] >= 0:
                        raise RuntimeError("duplicate target rating")
                    target_values[key][slot] = index
                    counters["target_rating_values_parsed"] += 1
    if counters["raw_rows"] != 32_000_204 or bool((full_hist.sum(axis=1) <= 0).any()):
        raise RuntimeError("global label reader coverage drift")
    if counters["timestamps_parsed"] or counters["future_movie_membership_opened"]:
        raise RuntimeError("label reader firewall failure")
    rows: list[dict[str, Any]] = []
    expected_targets = 0
    for row_index, row in enumerate(membership.itertuples(index=False)):
        key = (row_index, len(row.target_movie_ids))
        indices = target_values[key]
        if bool((indices < 0).any()):
            raise RuntimeError(f"missing target label: {row.outer} {row.user_key}")
        union_row = key_to_union[str(row.user_key)]
        q = full_history_q(indices, full_hist[union_row])
        rows.append({
            "outer": str(row.outer),
            "track": str(row.track),
            "fold_or_domain": str(row.fold_or_domain),
            "user_key": str(row.user_key),
            "target_movie_ids": list(map(int, row.target_movie_ids)),
            "target_rating_indices": indices.astype(np.int8).tolist(),
            "target_q_eval": q.astype(np.float64).tolist(),
            "full_history_count": int(full_hist[union_row].sum()),
            "full_history_histogram": full_hist[union_row].astype(np.uint32).tolist(),
        })
        expected_targets += len(indices)
    if counters["target_rating_values_parsed"] != expected_targets:
        raise RuntimeError("target label count drift")
    labels = pd.DataFrame(rows).sort_values(["outer", "user_key"], kind="stable", ignore_index=True)
    atomic_parquet(p["labels"], labels)
    seal = {
        "status": "OPENED_REC_EV_028_ATTRIBUTION_LABELS_ONCE_AFTER_GLOBAL_SCORE_SEAL",
        "contract": artifact(CONTRACT_DEFAULT),
        "prelabel_score_seal": artifact(verified["seal"]),
        "labels": artifact(p["labels"]),
        "membership_rows": len(labels),
        "union_users": len(union_keys),
        "target_labels": expected_targets,
        "reader": counters,
        "timestamps_opened": False,
        "future_movie_membership_opened": False,
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "product_policy_changed": False,
    }
    atomic_json(p["label_seal"], seal)
    progress(p["progress"], "LABEL_SEAL_COMPLETE", union_users=len(union_keys), target_labels=expected_targets)
    return seal


def _item_values(q: np.ndarray, active: bool, order: np.ndarray | None) -> tuple[np.ndarray, np.ndarray]:
    low = (q <= 0.20).astype(np.float64)
    if not active:
        return q.copy(), low
    if order is None or len(order) != len(q) or sorted(map(int, order)) != list(range(len(q))):
        raise RuntimeError("active score rank permutation drift")
    chosen = np.asarray(order[:2], dtype=np.int64)
    if len(set(chosen.tolist())) != 2:
        raise RuntimeError("active top2 cardinality drift")
    scale = len(q) / 2.0
    utility = np.zeros(len(q), dtype=np.float64)
    low_contribution = np.zeros(len(q), dtype=np.float64)
    utility[chosen] = scale * q[chosen]
    low_contribution[chosen] = scale * low[chosen]
    return utility, low_contribution


def metrics_outer(contract: Mapping[str, Any], labels: pd.DataFrame, slug: str, resume: bool) -> tuple[Path, Path]:
    op = outer_paths(contract, slug)
    if op["user_metrics"].exists() and op["item_occurrences"].exists():
        if not resume:
            raise ResumeError(f"sealed metric candidates require --resume: {slug}")
        return op["user_metrics"], op["item_occurrences"]
    if op["user_metrics"].exists() or op["item_occurrences"].exists():
        raise ResumeError(f"partial metric state: {slug}")
    scores = pd.read_parquet(op["scores"]).sort_values(["user_key", "cell", "system"], kind="stable", ignore_index=True)
    local_labels = labels.loc[labels["outer"].eq(slug)].sort_values("user_key", kind="stable", ignore_index=True)
    q_by_user = {str(row.user_key): np.asarray(row.target_q_eval, dtype=np.float64) for row in local_labels.itertuples(index=False)}
    movie_by_user = {str(row.user_key): np.asarray(row.target_movie_ids, dtype=np.int64) for row in local_labels.itertuples(index=False)}
    history_by_user = {str(row.user_key): int(row.full_history_count) for row in local_labels.itertuples(index=False)}
    user_rows: list[dict[str, Any]] = []
    item_rows: list[dict[str, Any]] = []
    for (key, cell), group in scores.groupby(["user_key", "cell"], sort=True, observed=True):
        key, cell = str(key), str(cell)
        if set(group["system"].astype(str)) != set(SYSTEMS) or len(group) != len(SYSTEMS):
            raise RuntimeError(f"score system Cartesian drift: {slug} {key} {cell}")
        q = q_by_user[key]
        movies = movie_by_user[key]
        random_metrics = analytic_random_top2(q)
        common = {
            "outer": slug,
            "track": str(group.iloc[0]["track"]),
            "fold_or_domain": str(group.iloc[0]["fold_or_domain"]),
            "user_key": key,
            "cell": cell,
            "encoding": str(group.iloc[0]["encoding"]),
            "k": int(group.iloc[0]["k"]),
            "full_history_count": history_by_user[key],
        }
        user_rows.append({
            **common,
            "system": "RANDOM_EXPECTATION",
            "active": True,
            "used_random_expectation": True,
            "ranked_top2_movie_ids": [],
            **random_metrics,
        })
        primary_values: dict[str, tuple[bool, np.ndarray, np.ndarray]] = {
            "RANDOM_EXPECTATION": (True, q.copy(), (q <= 0.20).astype(np.float64))
        }
        for score_row in group.itertuples(index=False):
            if np.asarray(score_row.target_movie_ids, dtype=np.int64).tolist() != movies.tolist():
                raise RuntimeError("score/label target order drift")
            active = bool(score_row.active)
            order = np.asarray(score_row.ranked_target_indices, dtype=np.int64)
            if active:
                top2 = ranked_top2_metrics(order, q)
                top_movies = movies[order[:2]].astype(np.int64).tolist()
            else:
                top2 = random_metrics
                top_movies = []
            user_rows.append({
                **common,
                "system": str(score_row.system),
                "active": active,
                "used_random_expectation": not active,
                "ranked_top2_movie_ids": top_movies,
                **top2,
            })
            if cell == PRIMARY_CELL:
                utility, low = _item_values(q, active, order if active else None)
                primary_values[str(score_row.system)] = (active, utility, low)
        if cell == PRIMARY_CELL:
            if set(primary_values) != set(ALL_SYSTEMS):
                raise RuntimeError("primary item system Cartesian drift")
            for slot, movie in enumerate(movies.tolist()):
                row = {
                    "outer": slug,
                    "track": common["track"],
                    "fold_or_domain": common["fold_or_domain"],
                    "user_key": key,
                    "movie_id": int(movie),
                    "target_n": len(movies),
                    "q_eval": float(q[slot]),
                    "low20": bool(q[slot] <= 0.20),
                }
                for system, (active, utility, low) in primary_values.items():
                    row[f"active__{system}"] = bool(active)
                    row[f"utility__{system}"] = float(utility[slot])
                    row[f"low__{system}"] = float(low[slot])
                item_rows.append(row)
    users = pd.DataFrame(user_rows).sort_values(["cell", "system", "user_key"], kind="stable", ignore_index=True)
    expected_user_rows = len(local_labels) * len(CELLS) * len(ALL_SYSTEMS)
    if len(users) != expected_user_rows or users.duplicated(["user_key", "cell", "system"]).any():
        raise RuntimeError(f"user metric row-set drift: {slug}")
    items = pd.DataFrame(item_rows).sort_values(["movie_id", "user_key"], kind="stable", ignore_index=True)
    expected_item_rows = sum(len(value) for value in local_labels["target_movie_ids"])
    if len(items) != expected_item_rows or items.duplicated(["user_key", "movie_id"]).any():
        raise RuntimeError(f"item occurrence row-set drift: {slug}")
    atomic_parquet(op["user_metrics"], users)
    atomic_parquet(op["item_occurrences"], items)
    return op["user_metrics"], op["item_occurrences"]


def materialize_metrics(contract: Mapping[str, Any], p: Mapping[str, Path], resume: bool) -> dict[str, Any]:
    if p["metrics_seal"].exists():
        if not resume:
            raise ResumeError("sealed metrics require --resume")
        return validate_metrics_seal(contract, p)
    partial = [
        path
        for slug, _, _ in OUTERS
        for path in (outer_paths(contract, slug)["user_metrics"], outer_paths(contract, slug)["item_occurrences"])
        if path.exists()
    ]
    if partial:
        raise ResumeError("unsealed partial metric artifacts exist; independent recovery audit required")
    _, labels = validate_label_seal(contract, p)
    artifacts = []
    user_rows = item_rows = 0
    for slug, _, _ in OUTERS:
        user_path, item_path = metrics_outer(contract, labels, slug, resume)
        user_frame = pd.read_parquet(user_path, columns=["user_key"])
        item_frame = pd.read_parquet(item_path, columns=["user_key"])
        user_rows += len(user_frame)
        item_rows += len(item_frame)
        artifacts.extend((artifact(user_path), artifact(item_path)))
        progress(p["progress"], "METRICS_OUTER_COMPLETE", outer=slug, user_rows=len(user_frame), item_rows=len(item_frame))
    seal = {
        "status": "SEALED_USER_METRICS_AND_PRIMARY_ITEM_OCCURRENCES",
        "label_seal": artifact(p["label_seal"]),
        "artifacts": artifacts,
        "user_metric_rows": user_rows,
        "item_occurrence_rows": item_rows,
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "product_policy_changed": False,
    }
    atomic_json(p["metrics_seal"], seal)
    return validate_metrics_seal(contract, p)


def _user_contrast_matrix(frame: pd.DataFrame) -> tuple[list[str], np.ndarray]:
    users = sorted(frame["user_key"].astype(str).unique().tolist())
    indexed = frame.set_index(["user_key", "system"])
    columns = []
    for model, comparator in CONTRASTS:
        for metric in USER_METRICS:
            model_values = indexed.loc[(slice(None), model), metric].droplevel("system").reindex(users).to_numpy(dtype=np.float64)
            comparator_values = indexed.loc[(slice(None), comparator), metric].droplevel("system").reindex(users).to_numpy(dtype=np.float64)
            columns.append(benefit(model_values, comparator_values, metric))
    matrix = np.column_stack(columns)
    if not np.isfinite(matrix).all():
        raise RuntimeError("nonfinite user contrast matrix")
    return users, matrix


def _random_pooled_user_matrix(frames: Mapping[str, pd.DataFrame]) -> tuple[list[str], np.ndarray]:
    by_column: list[dict[str, list[float]]] = [defaultdict(list) for _ in range(len(CONTRASTS) * len(USER_METRICS))]
    for slug in ("R0", "R1", "R2", "R3", "R4"):
        users, matrix = _user_contrast_matrix(frames[slug])
        for row, key in enumerate(users):
            for column in range(matrix.shape[1]):
                by_column[column][key].append(float(matrix[row, column]))
    users = sorted({key for column in by_column for key in column})
    matrix = np.empty((len(users), len(by_column)), dtype=np.float64)
    for column, values in enumerate(by_column):
        if set(values) != set(users):
            # Users need not occur in every fold, but every pooled column has the
            # same membership because every system is materialized per row.
            if set(values) != set(by_column[0]):
                raise RuntimeError("random pooled user contrast membership drift")
        for row, key in enumerate(users):
            matrix[row, column] = float(np.mean(values[key]))
    return users, matrix


def _active_rates(frames: Mapping[str, pd.DataFrame], estimand: str) -> dict[str, float]:
    if estimand != "RANDOM_POOLED":
        frame = frames[estimand]
        return {
            system: float(frame.loc[frame["system"].eq(system), "active"].astype(bool).mean())
            for system in ALL_SYSTEMS
        }
    result: dict[str, float] = {}
    combined = pd.concat([frames[slug][["user_key", "system", "active"]] for slug in ("R0", "R1", "R2", "R3", "R4")])
    for system in ALL_SYSTEMS:
        local = combined.loc[combined["system"].eq(system)]
        per_user = local.groupby("user_key", sort=True, observed=True)["active"].all()
        result[system] = float(per_user.mean())
    return result


def _item_transform() -> tuple[np.ndarray, list[dict[str, str]]]:
    base_columns = [(system, metric) for system in ITEM_BASE_SYSTEMS for metric in ITEM_METRICS]
    index = {key: position for position, key in enumerate(base_columns)}
    descriptions: list[dict[str, str]] = []
    transform = np.zeros((len(base_columns), len(CONTRASTS) * len(ITEM_METRICS)), dtype=np.float64)
    column = 0
    for model, comparator in CONTRASTS:
        for metric in ITEM_METRICS:
            sign = -1.0 if metric == "ITEM_MACRO_LOW_SLOT_CONTRIBUTION" else 1.0
            transform[index[(model, metric)], column] = sign
            transform[index[(comparator, metric)], column] = -sign
            descriptions.append({"model": model, "comparator": comparator, "metric": metric})
            column += 1
    return transform, descriptions


def _item_estimand(frame: pd.DataFrame) -> ItemEstimand:
    users = sorted(frame["user_key"].astype(str).unique().tolist())
    movies = sorted(frame["movie_id"].astype(int).unique().tolist())
    user_pos = {key: index for index, key in enumerate(users)}
    movie_pos = {movie: index for index, movie in enumerate(movies)}
    rows = frame["user_key"].astype(str).map(user_pos).to_numpy(dtype=np.int64)
    columns = frame["movie_id"].astype(int).map(movie_pos).to_numpy(dtype=np.int64)
    counts = frame.groupby("movie_id", sort=False, observed=True).size().to_dict()
    normalizer = np.asarray([1.0 / int(counts[int(movie)]) for movie in frame["movie_id"]], dtype=np.float64)
    shape = (len(users), len(movies))
    denominator = sparse.coo_matrix((normalizer, (rows, columns)), shape=shape).tocsr()
    matrices = []
    for system in ITEM_BASE_SYSTEMS:
        for metric in ITEM_METRICS:
            source = f"utility__{system}" if metric == "ITEM_MACRO_UTILITY_CONTRIBUTION" else f"low__{system}"
            data = frame[source].to_numpy(dtype=np.float64) * normalizer
            matrices.append(sparse.coo_matrix((data, (rows, columns)), shape=shape).tocsr())
    if denominator.nnz != len(frame):
        raise RuntimeError("duplicate user/movie occurrence in item estimand")
    return ItemEstimand(tuple(users), tuple(movies), denominator, tuple(matrices))


def _history_band(count: int) -> str:
    if count < 50:
        return "20_49"
    if count < 100:
        return "50_99"
    if count < 500:
        return "100_499"
    return "500_PLUS"


def _frequency_band(count: int) -> str:
    if count == 1:
        return "1_SINGLETON"
    if count <= 4:
        return "2_4"
    if count <= 9:
        return "5_9"
    return "10_PLUS"


def analyze(contract: Mapping[str, Any], p: Mapping[str, Path], resume: bool) -> dict[str, Any]:
    if p["analysis"].exists():
        if not resume:
            raise ResumeError("sealed analysis requires --resume")
        return validate_final_analysis(contract, p)
    partial = [p[key] for key in ("user_boot", "item_boot", "inference", "sensitivity", "user_segments", "movie_segments") if p[key].exists()]
    if partial:
        raise ResumeError("unsealed partial analysis artifacts exist; independent recovery audit required")
    validate_metrics_seal(contract, p)
    user_frames = {
        slug: pd.read_parquet(outer_paths(contract, slug)["user_metrics"]).loc[lambda x: x["cell"].eq(PRIMARY_CELL)].copy()
        for slug, _, _ in OUTERS
    }
    user_matrices = {slug: _user_contrast_matrix(user_frames[slug]) for slug, _, _ in OUTERS}
    user_matrices["RANDOM_POOLED"] = _random_pooled_user_matrix(user_frames)
    union_users = sorted({key for keys, _ in user_matrices.values() for key in keys})
    inference = contract["inference"]
    user_result = user_multiplier_max_t(
        union_users=union_users,
        matrices={name: user_matrices[name] for name in ESTIMANDS},
        repeats=int(inference["repeats"]),
        seed=int(inference["user_seed"]),
        confidence=float(inference["confidence"]),
        batch_size=int(inference["user_batch_size"]),
    )
    user_descriptions = [
        {"model": model, "comparator": comparator, "metric": metric}
        for model, comparator in CONTRASTS
        for metric in USER_METRICS
    ]
    user_rows = []
    cursor = 0
    for estimand in ESTIMANDS:
        users = len(user_matrices[estimand][0])
        for description in user_descriptions:
            user_rows.append({
                "estimand": estimand,
                **description,
                "users": users,
                "point_benefit": float(user_result["point"][cursor]),
                "se": float(user_result["se"][cursor]),
                "simultaneous_low": float(user_result["low"][cursor]),
                "simultaneous_high": float(user_result["high"][cursor]),
            })
            cursor += 1
    atomic_npz(p["user_boot"], estimates=np.asarray(user_result["estimates"]), point=np.asarray(user_result["point"]))
    del user_result["estimates"]

    item_frames = {slug: pd.read_parquet(outer_paths(contract, slug)["item_occurrences"]) for slug, _, _ in OUTERS}
    item_input_frames = {slug: item_frames[slug] for slug, _, _ in OUTERS}
    item_input_frames["RANDOM_POOLED"] = pd.concat(
        [item_frames[slug] for slug in ("R0", "R1", "R2", "R3", "R4")], ignore_index=True
    )
    item_estimands = {name: _item_estimand(item_input_frames[name]) for name in ESTIMANDS}
    union_item_users = sorted({key for value in item_estimands.values() for key in value.user_keys})
    union_movies = sorted({movie for value in item_estimands.values() for movie in value.movie_ids})
    transform, item_descriptions = _item_transform()
    item_result = item_multiway_multiplier_max_t(
        union_users=union_item_users,
        union_movies=union_movies,
        estimands={name: item_estimands[name] for name in ESTIMANDS},
        repeats=int(inference["repeats"]),
        seed=int(inference["item_multiway_seed"]),
        confidence=float(inference["confidence"]),
        linear_transform=transform,
        batch_size=int(inference["item_batch_size"]),
    )
    item_rows = []
    cursor = 0
    for estimand in ESTIMANDS:
        frame = item_input_frames[estimand]
        for description in item_descriptions:
            item_rows.append({
                "estimand": estimand,
                **description,
                "users": int(frame["user_key"].nunique()),
                "movies": int(frame["movie_id"].nunique()),
                "occurrences": len(frame),
                "point_benefit": float(item_result["point"][cursor]),
                "se": float(item_result["se"][cursor]),
                "simultaneous_low": float(item_result["low"][cursor]),
                "simultaneous_high": float(item_result["high"][cursor]),
            })
            cursor += 1
    atomic_npz(p["item_boot"], estimates=np.asarray(item_result["estimates"]), point=np.asarray(item_result["point"]))
    del item_result["estimates"]

    active_rates = {estimand: _active_rates(user_frames, estimand) for estimand in ESTIMANDS}
    user_lookup = {
        (row["estimand"], row["model"], row["comparator"], row["metric"]): row for row in user_rows
    }
    item_lookup = {
        (row["estimand"], row["model"], row["comparator"], row["metric"]): row for row in item_rows
    }
    preflight = json.loads(verify_artifact(contract["prelabel_execution"]["membership_preflight"]).read_text(encoding="utf-8"))
    truths = {}
    for estimand in ESTIMANDS:
        user_bounds = {
            (model, comparator, metric): (
                float(user_lookup[(estimand, model, comparator, metric)]["simultaneous_low"]),
                float(user_lookup[(estimand, model, comparator, metric)]["simultaneous_high"]),
            )
            for model, comparator in CONTRASTS
            for metric in USER_METRICS
        }
        item_bounds = {
            (model, comparator, metric): (
                float(item_lookup[(estimand, model, comparator, metric)]["simultaneous_low"]),
                float(item_lookup[(estimand, model, comparator, metric)]["simultaneous_high"]),
            )
            for model, comparator in CONTRASTS
            for metric in ITEM_METRICS
        }
        signal = classify_signal(active_rates=active_rates[estimand], user_bounds=user_bounds, item_bounds=item_bounds)
        coverage = str(preflight["coverage"][estimand]["coverage_truth"])
        truths[estimand] = {
            "signal_truth": signal,
            "coverage_truth": coverage,
            "stage_pass": signal == "ATTRIBUTED_PERSONALIZED_CONTENT_SIGNAL" and coverage == "BROAD_ITEM_ELIGIBLE",
            "active_rates": active_rates[estimand],
        }
    inference_value = {
        "status": "COMPLETE_DUAL_USER_AND_ITEM_MACRO_MAX_T",
        "primary_cell": PRIMARY_CELL,
        "repeats": int(inference["repeats"]),
        "user_family_size": len(user_rows),
        "item_family_size": len(item_rows),
        "user_critical": float(user_result["critical"]),
        "item_critical": float(item_result["critical"]),
        "user_intervals": user_rows,
        "item_intervals": item_rows,
        "truth_by_estimand": truths,
    }
    atomic_json(p["inference"], inference_value)

    all_user_metrics = pd.concat(
        [pd.read_parquet(outer_paths(contract, slug)["user_metrics"]) for slug, _, _ in OUTERS], ignore_index=True
    )
    sensitivity_rows = []
    for (outer, cell, system), group in all_user_metrics.groupby(["outer", "cell", "system"], sort=True, observed=True):
        sensitivity_rows.append({
            "outer": str(outer),
            "cell": str(cell),
            "system": str(system),
            "users": len(group),
            "active_rate": float(group["active"].astype(bool).mean()),
            "HARM20": float(group["HARM20"].mean()),
            "TOP2_MEAN_Q": float(group["TOP2_MEAN_Q"].mean()),
            "TOP2_MIN_Q": float(group["TOP2_MIN_Q"].mean()),
            "GOOD80": float(group["GOOD80"].mean()),
        })
    atomic_json(p["sensitivity"], {
        "status": "DESCRIPTIVE_ONLY_NOT_USED_FOR_GATE_OR_SELECTION",
        "primary_cell": PRIMARY_CELL,
        "rows": sensitivity_rows,
    })

    primary_users = all_user_metrics.loc[all_user_metrics["cell"].eq(PRIMARY_CELL)].copy()
    primary_users["history_band"] = primary_users["full_history_count"].map(_history_band)
    user_segment_rows = []
    for (outer, band, system), group in primary_users.groupby(["outer", "history_band", "system"], sort=True, observed=True):
        user_segment_rows.append({
            "outer": str(outer), "history_band": str(band), "system": str(system),
            "users": len(group), "active_rate": float(group["active"].astype(bool).mean()),
            "HARM20": float(group["HARM20"].mean()),
            "TOP2_MEAN_Q": float(group["TOP2_MEAN_Q"].mean()),
            "TOP2_MIN_Q": float(group["TOP2_MIN_Q"].mean()),
        })
    atomic_parquet(p["user_segments"], pd.DataFrame(user_segment_rows))

    movie_segment_rows = []
    for slug, frame in item_frames.items():
        frequency = frame.groupby("movie_id", sort=True, observed=True).size().rename("target_frequency")
        local = frame.merge(frequency, on="movie_id", validate="many_to_one")
        local["frequency_band"] = local["target_frequency"].map(_frequency_band)
        for system in ALL_SYSTEMS:
            by_movie = local.groupby(["frequency_band", "movie_id"], sort=True, observed=True).agg(
                occurrences=("user_key", "size"),
                utility=(f"utility__{system}", "mean"),
                low=(f"low__{system}", "mean"),
            ).reset_index()
            for band, group in by_movie.groupby("frequency_band", sort=True, observed=True):
                movie_segment_rows.append({
                    "outer": slug, "frequency_band": str(band), "system": system,
                    "movies": len(group), "occurrences": int(group["occurrences"].sum()),
                    "item_macro_utility": float(group["utility"].mean()),
                    "item_macro_low_slot": float(group["low"].mean()),
                })
    atomic_parquet(p["movie_segments"], pd.DataFrame(movie_segment_rows))

    final = {
        "status": "COMPLETE_REC_EV_028B_LABEL_AND_ATTRIBUTION_ANALYSIS",
        "contract": artifact(CONTRACT_DEFAULT),
        "label_seal": artifact(p["label_seal"]),
        "metrics_seal": artifact(p["metrics_seal"]),
        "inference": artifact(p["inference"]),
        "sensitivity": artifact(p["sensitivity"]),
        "user_segments": artifact(p["user_segments"]),
        "movie_segments": artifact(p["movie_segments"]),
        "user_bootstrap": artifact(p["user_boot"]),
        "item_bootstrap": artifact(p["item_boot"]),
        "truth_by_estimand": truths,
        "future_reserve_opened": False,
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "product_policy_changed": False,
    }
    atomic_json(p["analysis"], final)
    progress(p["progress"], "REC_EV_028B_COMPLETE", truths=truths)
    return validate_final_analysis(contract, p)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=CONTRACT_DEFAULT)
    parser.add_argument("--phase", choices=("label", "metrics", "analyze", "all"), default="all")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if not args.resume:
        raise ResumeError("REC-EV-028B continuation requires explicit --resume")
    contract = load_contract(args.contract)
    p = paths(contract)
    phases: Iterable[str] = ("label", "metrics", "analyze") if args.phase == "all" else (args.phase,)
    for phase in phases:
        if phase == "label":
            open_labels(contract, p, args.resume)
        elif phase == "metrics":
            if not p["label_seal"].exists():
                raise ResumeError("metrics require sealed labels")
            materialize_metrics(contract, p, args.resume)
        elif phase == "analyze":
            if not p["metrics_seal"].exists():
                raise ResumeError("analysis requires sealed metrics")
            analyze(contract, p, args.resume)


if __name__ == "__main__":
    main()
