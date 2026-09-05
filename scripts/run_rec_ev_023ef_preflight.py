#!/usr/bin/env python3
"""Firewalled pre-label feasibility for the jointly frozen REC-EV-023E/F designs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

try:
    from rec_ev_022a_core import old_user_bucket, user_key, user_role_bucket
    from validate_rec_ev_023ef_contract import validate_contract
except ImportError:
    from scripts.rec_ev_022a_core import old_user_bucket, user_key, user_role_bucket
    from scripts.validate_rec_ev_023ef_contract import validate_contract


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "docs/recommendation/contracts/rec-ev-023ef-joint-transfer-design.json"


class ResumeError(RuntimeError):
    pass


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_json_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def resolve_input(entry: Mapping[str, Any]) -> Path:
    path = Path(str(entry["path"]))
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def output_path(contract: Mapping[str, Any], name: str) -> Path:
    return ROOT / str(contract["output_root"]) / str(contract["outputs"][name])


def source_rows(contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, entry in sorted(contract["allowed_input_artifacts"].items()):
        path = resolve_input(entry)
        if not path.is_file() or path.stat().st_size != int(entry["bytes"]) or sha256_file(path) != str(entry["sha256"]):
            raise RuntimeError(f"source pin mismatch: {name}")
        rows.append({"name": name, "path": str(entry["path"]), "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return rows


def implementation_rows(contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for value in contract["implementation_artifacts"]:
        path = (ROOT / str(value)).resolve()
        if not path.is_file():
            raise RuntimeError(f"implementation missing: {value}")
        rows.append({"path": str(value), "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return rows


def locked_spec(contract: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "purpose", "independent_design_audit", "prior_invalid_preflight_incident", "authorization",
        "roles_and_reader", "universe", "common_design", "experiments", "statistics", "decision",
        "claim_boundary", "resume", "invariants",
    )
    return {key: contract[key] for key in keys}


def expected_lock_state(contract: Mapping[str, Any], contract_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    sources = source_rows(contract)
    implementations = implementation_rows(contract)
    manifest = {
        "schema_version": 1,
        "evidence_id": "REC-EV-023EF-PREFLIGHT",
        "sources": sources,
        "implementation_artifacts": implementations,
        "prior_invalid_preflight_locked_item_id_access": True,
        "discarded_counts_forbidden": [431, 3498],
        "old_locked_rating_timestamp_metric_opened": False,
        "final_reserve_opened": False,
        "evaluation_labels_opened": False,
    }
    contract_sha = hashlib.sha256(canonical_json_bytes(contract)).hexdigest()
    source_sha = hashlib.sha256(canonical_json_bytes(sources)).hexdigest()
    implementation_sha = hashlib.sha256(canonical_json_bytes(implementations)).hexdigest()
    locked_sha = hashlib.sha256(canonical_json_bytes(locked_spec(contract))).hexdigest()
    manifest_sha = hashlib.sha256(canonical_json_bytes(manifest)).hexdigest()
    lock = {
        "schema_version": 1,
        "evidence_id": "REC-EV-023EF-PREFLIGHT",
        "status": "APPROVED_FOR_PRELABEL_FIREWALLED_FEASIBILITY",
        "contract_sha256": contract_sha,
        "source_artifacts_sha256": source_sha,
        "implementation_artifacts_sha256": implementation_sha,
        "locked_spec_sha256": locked_sha,
        "source_manifest_sha256": manifest_sha,
        "prior_invalid_preflight_locked_item_id_access": True,
        "old_locked_rating_timestamp_metric_opened": False,
        "final_reserve_opened": False,
        "evaluation_labels_opened": False,
        "product_policy_updated": False,
        "champion": None,
    }
    return manifest, lock


def create_or_verify_lock(contract: Mapping[str, Any], contract_path: Path, *, resume: bool) -> dict[str, Any]:
    lock_path = output_path(contract, "protocol_lock")
    manifest_path = output_path(contract, "source_manifest")
    present = [lock_path.exists(), manifest_path.exists()]
    if any(present) and not all(present):
        raise ResumeError("partial preflight lock state")
    manifest, lock = expected_lock_state(contract, contract_path)
    if all(present):
        if not resume:
            raise ResumeError("preflight lock exists; use --resume")
        if read_json(lock_path) != lock or read_json(manifest_path) != manifest:
            raise ResumeError("preflight lock or manifest drift")
        return lock
    if resume:
        raise ResumeError("--resume requested before preflight lock exists")
    atomic_write_json(manifest_path, manifest)
    atomic_write_json(lock_path, lock)
    return lock


def run_signature(contract: Mapping[str, Any]) -> str:
    lock = read_json(output_path(contract, "protocol_lock"))
    payload = {key: lock[key] for key in (
        "contract_sha256", "source_artifacts_sha256", "implementation_artifacts_sha256", "locked_spec_sha256",
    )}
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def allowed_role(raw_user: int, maximum: int) -> bool:
    if raw_user <= 0 or raw_user > maximum:
        raise RuntimeError("MovieLens user id outside preregistered bound")
    if old_user_bucket(raw_user) > 59:
        return False
    bucket = user_role_bucket(raw_user)
    return 6000 <= bucket <= 9199


def movie_id_only_rows(archive: Path, member: str, maximum_user: int):
    """Yield allowed user/movie IDs; never parse bytes after the second comma."""
    with zipfile.ZipFile(archive) as bundle:
        if member not in bundle.namelist():
            raise RuntimeError("MovieLens member missing")
        with bundle.open(member) as handle:
            if handle.readline().rstrip(b"\r\n") != b"userId,movieId,rating,timestamp":
                raise RuntimeError("MovieLens header drift")
            previous_raw_user = 0
            allowed_user = -1
            allowed_movies: set[int] = set()
            for raw_line in handle:
                first = raw_line.find(b",")
                if first <= 0:
                    raise RuntimeError("MovieLens user delimiter missing")
                raw_user = int(raw_line[:first])
                if raw_user < previous_raw_user:
                    raise RuntimeError("MovieLens user order drift")
                previous_raw_user = raw_user
                if not allowed_role(raw_user, maximum_user):
                    continue
                second = raw_line.find(b",", first + 1)
                if second <= first + 1:
                    raise RuntimeError("MovieLens movie delimiter missing")
                movie = int(raw_line[first + 1:second])
                if raw_user != allowed_user:
                    allowed_user = raw_user
                    allowed_movies.clear()
                if movie in allowed_movies:
                    raise RuntimeError("duplicate allowed user-movie rating row")
                allowed_movies.add(movie)
                yield raw_user, movie


def _listish_nonempty(value: Any) -> bool:
    if value is None:
        return False
    try:
        return len(value) > 0
    except TypeError:
        return False


def build_universe(contract: Mapping[str, Any]) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    structured = pd.read_parquet(resolve_input(contract["allowed_input_artifacts"]["structured_features"]))
    required = {"movie_id", "release_year", "feature_eligible", "genre_ids", "original_language"}
    if not required.issubset(structured.columns):
        raise RuntimeError("structured feature schema drift")
    eligible = structured["feature_eligible"].fillna(False).astype(bool) & structured["release_year"].notna()
    frame = structured.loc[eligible].copy()
    if frame["movie_id"].duplicated().any():
        raise RuntimeError("structured movie id duplicate")
    basic_nonzero = frame["genre_ids"].map(_listish_nonempty) | frame["original_language"].notna()
    if not bool(basic_nonzero.all()):
        raise RuntimeError("feature eligible row has zero BASIC proxy")
    projection = read_json(resolve_input(contract["allowed_input_artifacts"]["korean_movie_id_projection"]))
    if set(projection) != {"artifact_id", "claim", "count", "movie_ids", "projection_rule", "schema_version", "source_artifacts"}:
        raise RuntimeError("Korean movie-id projection schema drift")
    korean_ids = {int(value) for value in projection["movie_ids"]}
    if projection["artifact_id"] != "KOREAN_ORIGIN_MOVIELENS_MOVIE_ID_PROJECTION_V1" or int(projection["count"]) != 1078 or len(korean_ids) != 1078:
        raise RuntimeError("Korean movie-id projection identity or cardinality drift")
    movie_ids = frame["movie_id"].to_numpy(dtype=np.int32)
    years = frame["release_year"].to_numpy(dtype=np.int16)
    korean = np.fromiter((int(movie) in korean_ids for movie in movie_ids), dtype=bool, count=len(movie_ids))
    return movie_ids, years, korean, {
        "structured_rows": int(len(structured)),
        "universe_items": int(len(movie_ids)),
        "korean_origin_items": int(korean.sum()),
        "recent_2020_2023_items": int(((years >= 2020) & (years <= 2023)).sum()),
        "pre_2020_items": int((years < 2020).sum()),
    }


def degree_summary(target_degrees: np.ndarray) -> dict[str, Any]:
    positive = target_degrees[target_degrees > 0]
    if not len(positive):
        return {"unique_target_items": 0, "maximum_target_degree": 0, "top10_target_degree_sum": 0, "selected_target_memberships": 0}
    return {
        "unique_target_items": int(len(positive)),
        "maximum_target_degree": int(positive.max()),
        "top10_target_degree_sum": int(np.sort(positive)[-10:].sum()),
        "selected_target_memberships": int(positive.sum()),
    }


def panel_order(user_key_value: str, movie_ids: list[int], salt: str) -> list[int]:
    return sorted(
        movie_ids,
        key=lambda movie: (hashlib.sha256(f"{salt}|{user_key_value}|{movie}".encode("utf-8")).digest(), int(movie)),
    )


def compute_preflight_result(contract: Mapping[str, Any]) -> dict[str, Any]:
    signature = run_signature(contract)
    movie_ids, years, korean, universe_summary = build_universe(contract)
    maximum_movie = int(movie_ids.max(initial=0))
    position = np.full(maximum_movie + 1, -1, dtype=np.int32)
    position[movie_ids] = np.arange(len(movie_ids), dtype=np.int32)
    maximum_user = int(contract["roles_and_reader"]["maximum_user_id"])
    korean_count = np.zeros(maximum_user + 1, dtype=np.int32)
    non_korean_count = np.zeros_like(korean_count)
    recent_count = np.zeros_like(korean_count)
    old_count = np.zeros_like(korean_count)
    allowed_rows = 0
    archive_entry = contract["allowed_input_artifacts"]["movielens_archive"]
    for raw_user, movie in movie_id_only_rows(resolve_input(archive_entry), str(archive_entry["member"]), maximum_user):
        allowed_rows += 1
        if movie > maximum_movie or position[movie] < 0:
            continue
        p = int(position[movie])
        if bool(korean[p]):
            korean_count[raw_user] += 1
        else:
            non_korean_count[raw_user] += 1
        if int(years[p]) < 2020:
            old_count[raw_user] += 1
        elif int(years[p]) <= 2023:
            recent_count[raw_user] += 1

    e_spec = contract["experiments"]["REC-EV-023E"]
    f_spec = contract["experiments"]["REC-EV-023F"]
    e_mask = (korean_count >= int(e_spec["minimum_target_ratings"])) & (non_korean_count >= int(e_spec["minimum_profile_control_ratings"]))
    f_mask = (recent_count >= int(f_spec["minimum_target_ratings"])) & (old_count >= int(f_spec["minimum_profile_control_ratings"]))
    eligible_any = e_mask | f_mask
    e_targets: dict[int, list[int]] = {int(value): [] for value in np.flatnonzero(e_mask)}
    f_targets: dict[int, list[int]] = {int(value): [] for value in np.flatnonzero(f_mask)}
    for raw_user, movie in movie_id_only_rows(resolve_input(archive_entry), str(archive_entry["member"]), maximum_user):
        if not eligible_any[raw_user] or movie > maximum_movie or position[movie] < 0:
            continue
        p = int(position[movie])
        if e_mask[raw_user] and bool(korean[p]):
            e_targets[raw_user].append(int(movie))
        if f_mask[raw_user] and 2020 <= int(years[p]) <= 2023:
            f_targets[raw_user].append(int(movie))
    e_degree = np.zeros(len(movie_ids), dtype=np.int32)
    f_degree = np.zeros(len(movie_ids), dtype=np.int32)
    salts = contract["common_design"]["panel_salts"]
    exposure_payload: dict[str, list[str]] = {"REC-EV-023E": [], "REC-EV-023F": []}
    for evidence_id, targets, mask, degrees, spec in (
        ("REC-EV-023E", e_targets, e_mask, e_degree, e_spec),
        ("REC-EV-023F", f_targets, f_mask, f_degree, f_spec),
    ):
        target_n = int(spec["target_n"])
        for raw_user in np.flatnonzero(mask):
            values = targets[int(raw_user)]
            if len(values) != len(set(values)) or len(values) < target_n:
                raise RuntimeError(f"{evidence_id} target membership duplicate or short")
            anonymous = user_key(int(raw_user))
            for panel_index, salt in enumerate(salts[evidence_id]["target_order"]):
                selected = panel_order(anonymous, values, str(salt))[:target_n]
                for movie in selected:
                    degrees[int(position[movie])] += 1
                    exposure_payload[evidence_id].append(f"{anonymous}|{panel_index}|{movie}")
    e_summary = {"eligible_users": int(e_mask.sum()), **degree_summary(e_degree)}
    f_summary = {"eligible_users": int(f_mask.sum()), **degree_summary(f_degree)}
    if e_summary["selected_target_memberships"] != e_summary["eligible_users"] * 4 * int(e_spec["target_n"]):
        raise RuntimeError("023E selected target membership count drift")
    if f_summary["selected_target_memberships"] != f_summary["eligible_users"] * 4 * int(f_spec["target_n"]):
        raise RuntimeError("023F selected target membership count drift")
    e_summary["selected_target_exposure_sha256"] = hashlib.sha256(canonical_json_bytes(exposure_payload["REC-EV-023E"])).hexdigest()
    f_summary["selected_target_exposure_sha256"] = hashlib.sha256(canonical_json_bytes(exposure_payload["REC-EV-023F"])).hexdigest()
    e_summary["status"] = "FEASIBLE_PRELABEL" if e_summary["eligible_users"] >= int(e_spec["minimum_users"]) and e_summary["unique_target_items"] >= int(e_spec["minimum_unique_target_items"]) else "INFEASIBLE_PRELABEL"
    f_summary["status"] = "FEASIBLE_PRELABEL" if f_summary["eligible_users"] >= int(f_spec["minimum_users"]) and f_summary["unique_target_items"] >= int(f_spec["minimum_unique_target_items"]) else "INFEASIBLE_PRELABEL"
    result = {
        "schema_version": 1,
        "evidence_id": "REC-EV-023EF-PREFLIGHT",
        "status": "PREFLIGHT_COMPLETE",
        "run_signature": signature,
        "reader": {
            "allowed_rows_movie_id_parsed_per_pass": allowed_rows,
            "movie_id_only_passes": 2,
            "rating_value_bytes_parsed": 0,
            "timestamp_bytes_parsed": 0,
            "excluded_role_counts_reported": False,
            "raw_user_ids_written": False,
        },
        "universe": universe_summary,
        "experiments": {"REC-EV-023E": e_summary, "REC-EV-023F": f_summary},
        "eligible_user_key_set_sha256": {
            "REC-EV-023E": hashlib.sha256(canonical_json_bytes(sorted(user_key(int(value)) for value in np.flatnonzero(e_mask)))).hexdigest(),
            "REC-EV-023F": hashlib.sha256(canonical_json_bytes(sorted(user_key(int(value)) for value in np.flatnonzero(f_mask)))).hexdigest(),
        },
        "evaluation_labels_opened": False,
        "old_locked_rating_timestamp_metric_opened": False,
        "final_reserve_opened": False,
        "product_policy_updated": False,
        "champion": None,
    }
    return result


def expected_preflight_integrity(
    contract: Mapping[str, Any], result: Mapping[str, Any], progress: Mapping[str, Any], *, signature: str,
) -> dict[str, Any]:
    destination = output_path(contract, "preflight")
    progress_path = output_path(contract, "progress")
    return {
        "schema_version": 1,
        "run_signature": signature,
        "artifacts": {
            "preflight": {"path": destination.relative_to(ROOT).as_posix(), "bytes": destination.stat().st_size, "sha256": sha256_file(destination)},
            "progress": {"path": progress_path.relative_to(ROOT).as_posix(), "bytes": progress_path.stat().st_size, "sha256": sha256_file(progress_path)},
        },
        "metadata": {
            "status": result["status"],
            "experiments": result["experiments"],
            "rating_value_bytes_parsed": 0,
            "timestamp_bytes_parsed": 0,
        },
    }


def preflight(contract: Mapping[str, Any], *, resume: bool) -> dict[str, Any]:
    destination = output_path(contract, "preflight")
    integrity_path = output_path(contract, "preflight_integrity")
    progress_path = output_path(contract, "progress")
    states = [destination.exists(), integrity_path.exists(), progress_path.exists()]
    if any(states) and not all(states):
        raise ResumeError("partial preflight result state")
    signature = run_signature(contract)
    if all(states):
        if not resume:
            raise ResumeError("preflight result exists; use --resume")
        observed = read_json(destination)
        recomputed = compute_preflight_result(contract)
        if observed != recomputed:
            raise ResumeError("preflight deterministic recomputation drift")
        expected_progress = {"schema_version": 1, "phase": "PREFLIGHT_COMPLETE", "experiments": observed["experiments"], "run_signature": signature}
        if read_json(progress_path) != expected_progress:
            raise ResumeError("preflight progress semantic drift")
        expected_integrity = expected_preflight_integrity(contract, observed, expected_progress, signature=signature)
        if read_json(integrity_path) != expected_integrity:
            raise ResumeError("preflight integrity canonical semantic drift")
        return observed
    result = compute_preflight_result(contract)
    progress = {"schema_version": 1, "phase": "PREFLIGHT_COMPLETE", "experiments": result["experiments"], "run_signature": signature}
    atomic_write_json(destination, result)
    atomic_write_json(progress_path, progress)
    integrity = expected_preflight_integrity(contract, result, progress, signature=signature)
    atomic_write_json(integrity_path, integrity)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--phase", choices=("lock", "preflight", "all"), default="all")
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    contract_path = args.contract.resolve()
    contract = read_json(contract_path)
    validate_contract(contract)
    if args.phase == "preflight" and not args.resume:
        raise ResumeError("standalone preflight phase requires --resume and an exact existing lock")
    if args.phase in {"lock", "all"}:
        print(json.dumps(create_or_verify_lock(contract, contract_path, resume=args.resume), ensure_ascii=False, sort_keys=True))
    else:
        create_or_verify_lock(contract, contract_path, resume=True)
    if args.phase in {"preflight", "all"}:
        print(json.dumps(preflight(contract, resume=args.resume), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
