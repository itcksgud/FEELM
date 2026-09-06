"""Materialize REC-EV-027 evaluation membership without parsing rating values or timestamps."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any
import zipfile

import numpy as np
import pandas as pd

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from build_rec_ev_019b_features import _zip_member, sha256_file  # noqa: E402
from rec_ev_022a_core import build_structured_full, old_user_bucket, user_key, user_role_bucket  # noqa: E402
from validate_rec_ev_027_contract import DEFAULT, ROOT, validate  # noqa: E402


MAX_USER_ID = 300_000


def fold_of(movie_id: int, salt: str) -> int:
    payload = f"{salt}|{int(movie_id)}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big", signed=False) % 5


def evaluation_phase_bucket(user_id: int, salt: str = "rec-ev-027-phase-user-v1") -> int:
    payload = f"{salt}|{int(user_id)}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest(), "big", signed=False) % 10_000


def hash_order(
    movie_ids: list[int], *, salt: str, track: str, fold_or_domain: str, key: str
) -> list[int]:
    return sorted(
        (int(movie_id) for movie_id in movie_ids),
        key=lambda movie_id: (
            hashlib.sha256(
                f"{salt}|{track}|{fold_or_domain}|{key}|{movie_id}".encode("utf-8")
            ).digest(),
            movie_id,
        ),
    )


def scan_evaluation_membership(
    archive_path: Path,
    universe: set[int],
    *,
    max_user_id: int = MAX_USER_ID,
) -> tuple[dict[int, list[int]], dict[str, int]]:
    old_allowed = np.fromiter(
        (old_user_bucket(user_id) <= 59 for user_id in range(max_user_id + 1)),
        dtype=bool,
        count=max_user_id + 1,
    )
    roles = np.fromiter(
        (user_role_bucket(user_id) for user_id in range(max_user_id + 1)),
        dtype=np.uint16,
        count=max_user_id + 1,
    )
    histories: dict[int, list[int]] = {}
    counters = {
        "raw_lines_seen": 0,
        "excluded_after_user_id_only": 0,
        "evaluation_user_movie_memberships": 0,
        "rating_values_parsed": 0,
        "timestamps_parsed": 0,
    }
    with zipfile.ZipFile(archive_path) as archive:
        member = _zip_member(archive, "ratings.csv")
        with archive.open(member) as handle:
            handle.readline()
            for raw in handle:
                counters["raw_lines_seen"] += 1
                first = raw.find(b",")
                if first <= 0:
                    raise RuntimeError("malformed ratings row before user firewall")
                uid = int(raw[:first])
                if uid > max_user_id:
                    raise RuntimeError("MovieLens user ID exceeds pinned maximum")
                if not bool(old_allowed[uid]) or not 6000 <= int(roles[uid]) <= 7999:
                    counters["excluded_after_user_id_only"] += 1
                    continue
                second = raw.find(b",", first + 1)
                if second <= first + 1:
                    raise RuntimeError("malformed movie ID field")
                movie_id = int(raw[first + 1 : second])
                if movie_id in universe:
                    histories.setdefault(uid, []).append(movie_id)
                    counters["evaluation_user_movie_memberships"] += 1
    return histories, counters


def atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    frame.to_parquet(temporary, index=False)
    os.replace(temporary, path)


def atomic_json(value: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def build_membership(contract: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    build_outputs = contract["catalog_content_build"]["outputs"]
    structured = pd.read_parquet(ROOT / build_outputs["structured"])
    embeddings = pd.read_parquet(ROOT / build_outputs["embeddings"])
    domains = pd.read_parquet(ROOT / build_outputs["domain_projection"], columns=["movie_id", "tmdb_korean_origin_proxy"])
    if set(domains["movie_id"].astype(int)) != set(structured["movie_id"].astype(int)):
        raise RuntimeError("domain artifact does not exactly cover the identity-eligible feature rows")
    structured_candidates = structured.loc[structured["feature_eligible"], "movie_id"].astype(int).to_numpy()
    structured_matrix = build_structured_full(structured, structured_candidates)
    structured_norms = np.sqrt(np.asarray(structured_matrix.multiply(structured_matrix).sum(axis=1)).ravel())
    structured_ids = set(int(value) for value in structured_candidates[structured_norms > 0])
    e5_ids: set[int] = set()
    for row in embeddings.itertuples(index=False):
        vector = np.asarray(row.embedding, dtype=np.float32)
        if (
            bool(row.feature_eligible)
            and row.model_id == contract["item_universe"]["e5_model_id"]
            and row.model_revision == contract["item_universe"]["e5_revision"]
            and vector.shape == (384,)
            and bool(np.isfinite(vector).all())
            and abs(float(np.linalg.norm(vector)) - 1.0) <= 0.0001
        ):
            e5_ids.add(int(row.movie_id))
    universe = structured_ids & e5_ids
    structured = structured.set_index("movie_id", verify_integrity=True)
    domain = domains.set_index("movie_id", verify_integrity=True).reindex(sorted(universe))
    korean = set(int(value) for value in domain.index[domain["tmdb_korean_origin_proxy"].fillna(False)])
    years = structured.reindex(sorted(universe))["release_year"]
    recent = set(int(movie_id) for movie_id, year in years.items() if pd.notna(year) and 2020 <= int(year) <= 2023)
    pre2020 = set(int(movie_id) for movie_id, year in years.items() if pd.notna(year) and int(year) <= 2019)

    archive = Path(contract["allowed_input_artifacts"]["movielens_archive"]["path"])
    histories, counters = scan_evaluation_membership(archive, universe)
    membership = contract["membership"]
    profile_salt = membership["profile_order_salt"]
    target_salt = membership["target_order_salt"]
    fold_salt = contract["strict_item_firewall"]["fold_salt"]
    phase_salt = contract["source_population"]["evaluation_phase_salt"]
    tracks = contract["tracks"]
    rows: list[dict[str, Any]] = []
    for uid in sorted(histories):
        key = user_key(uid)
        phase_bucket = evaluation_phase_bucket(uid, phase_salt)
        if len(histories[uid]) != len(set(histories[uid])):
            raise RuntimeError("duplicate evaluation user-movie membership")
        movies = sorted(histories[uid])
        for fold in range(5):
            if fold == 0 and phase_bucket > 1999:
                continue
            if fold != 0 and phase_bucket < 2000:
                continue
            target_pool = [movie_id for movie_id in movies if fold_of(movie_id, fold_salt) == fold]
            profile_pool = [movie_id for movie_id in movies if fold_of(movie_id, fold_salt) != fold]
            target_n = int(tracks["RANDOM_ITEM_COLD"]["target_n"])
            if len(profile_pool) >= 8 and len(target_pool) >= target_n:
                rows.append({
                    "user_key": key,
                    "track": "RANDOM_ITEM_COLD",
                    "fold_or_domain": str(fold),
                    "profile_movie_ids": hash_order(profile_pool, salt=profile_salt, track="RANDOM_ITEM_COLD", fold_or_domain=str(fold), key=key)[:8],
                    "target_movie_ids": hash_order(target_pool, salt=target_salt, track="RANDOM_ITEM_COLD", fold_or_domain=str(fold), key=key)[:target_n],
                })
        for track, target_set, profile_set, domain_name in (
            ("KOREAN_ORIGIN_COLD", korean, universe - korean, "KR"),
            ("RELEASE_2020_2023_COLD", recent, pre2020, "2020_2023"),
        ):
            if phase_bucket < 2000:
                continue
            target_pool = [movie_id for movie_id in movies if movie_id in target_set]
            profile_pool = [movie_id for movie_id in movies if movie_id in profile_set]
            target_n = int(tracks[track]["target_n"])
            if len(profile_pool) >= 8 and len(target_pool) >= target_n:
                rows.append({
                    "user_key": key,
                    "track": track,
                    "fold_or_domain": domain_name,
                    "profile_movie_ids": hash_order(profile_pool, salt=profile_salt, track=track, fold_or_domain=domain_name, key=key)[:8],
                    "target_movie_ids": hash_order(target_pool, salt=target_salt, track=track, fold_or_domain=domain_name, key=key)[:target_n],
                })
    frame = pd.DataFrame(rows).sort_values(["track", "fold_or_domain", "user_key"], kind="stable", ignore_index=True)
    if frame.empty:
        raise RuntimeError("no eligible REC-EV-027 evaluation membership")
    if any(set(profile) & set(target) for profile, target in zip(frame["profile_movie_ids"], frame["target_movie_ids"], strict=True)):
        raise RuntimeError("profile/target overlap")
    group_counts = frame.groupby(["track", "fold_or_domain"], observed=True).size().to_dict()
    unique_targets = {
        f"{track}|{fold}": len({movie for values in group["target_movie_ids"] for movie in values})
        for (track, fold), group in frame.groupby(["track", "fold_or_domain"], observed=True)
    }
    for fold in range(5):
        users = int(group_counts.get(("RANDOM_ITEM_COLD", str(fold)), 0))
        targets = int(unique_targets.get(f"RANDOM_ITEM_COLD|{fold}", 0))
        if users < int(tracks["RANDOM_ITEM_COLD"]["minimum_screen_users"]) or targets < int(tracks["RANDOM_ITEM_COLD"]["minimum_unique_cold_targets"]):
            raise RuntimeError(f"random fold {fold} feasibility failed")
    for track in ("KOREAN_ORIGIN_COLD", "RELEASE_2020_2023_COLD"):
        domain_name = "KR" if track == "KOREAN_ORIGIN_COLD" else "2020_2023"
        if int(group_counts.get((track, domain_name), 0)) < int(tracks[track]["minimum_users"]):
            raise RuntimeError(f"{track} user feasibility failed")
        if int(unique_targets.get(f"{track}|{domain_name}", 0)) < int(tracks[track]["minimum_unique_cold_targets"]):
            raise RuntimeError(f"{track} item feasibility failed")
    summary = {
        "evidence_id": "REC-EV-027",
        "status": "PASS_MEMBERSHIP_ONLY_PREFLIGHT",
        "common_support_items": len(universe),
        "korean_origin_proxy_items": len(korean),
        "release_2020_2023_items": len(recent),
        "group_users": {f"{track}|{fold}": int(value) for (track, fold), value in group_counts.items()},
        "group_unique_targets": unique_targets,
        "reader": counters,
        "rating_values_parsed": False,
        "timestamps_parsed": False,
        "locked_test_opened": False,
        "final_reserve_opened": False,
    }
    return frame, summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT)
    parser.add_argument("--output-root", type=Path, default=ROOT / "outputs/recommendation-evidence/rec-ev-027")
    args = parser.parse_args()
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    validate(contract, verify_files=True)
    if contract["status"] != "APPROVED_FOR_ADAPTIVE_STRICT_ITEM_COLD_EXECUTION":
        raise RuntimeError("exact audited execution contract is required before membership scan")
    frame, summary = build_membership(contract)
    output_root = args.output_root.resolve()
    membership_path = output_root / "cache/membership.parquet"
    summary_path = output_root / "membership-preflight.json"
    atomic_parquet(frame, membership_path)
    summary["membership"] = {
        "path": membership_path.relative_to(ROOT).as_posix(),
        "bytes": membership_path.stat().st_size,
        "sha256": sha256_file(membership_path),
    }
    atomic_json(summary, summary_path)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
