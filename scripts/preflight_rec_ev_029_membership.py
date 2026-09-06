"""Build REC-EV-029 selection/replication membership without rating values."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any
import zipfile

import numpy as np
import pandas as pd

from build_rec_ev_019b_features import sha256_file
from preflight_rec_ev_027_membership import hash_order
from preflight_rec_ev_028a1_membership import _coverage_truth, balanced_targets
from preflight_rec_ev_028a_membership import common_sets, outer_pools, phase_bucket
from rec_ev_022a_core import old_user_bucket, user_key, user_role_bucket


ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "docs/recommendation/contracts/rec-ev-029-direct-profile-attribution.json"
MAX_USER_ID = 200_948
EXPECTED_RATING_ROWS = 32_000_204
OUTERS = (
    ("R0", "RANDOM_ITEM_COLD", "0"),
    ("R1", "RANDOM_ITEM_COLD", "1"),
    ("R2", "RANDOM_ITEM_COLD", "2"),
    ("R3", "RANDOM_ITEM_COLD", "3"),
    ("R4", "RANDOM_ITEM_COLD", "4"),
    ("KR", "KOREAN_ORIGIN_COLD", "KR"),
    ("RECENT", "RELEASE_2020_2023_COLD", "2020_2023"),
)


def artifact(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    try:
        display = resolved.relative_to(ROOT).as_posix()
    except ValueError:
        display = resolved.as_posix()
    return {"path": display, "bytes": resolved.stat().st_size, "sha256": sha256_file(resolved)}


def verify(spec: dict[str, Any]) -> Path:
    raw = Path(str(spec["path"]))
    path = raw if raw.is_absolute() else ROOT / raw
    if not path.is_file() or path.stat().st_size != int(spec["bytes"]) or sha256_file(path) != str(spec["sha256"]):
        raise RuntimeError(f"artifact drift: {path}")
    return path.resolve()


def split_bucket(user_id: int, salt: str) -> int:
    return int.from_bytes(hashlib.sha256(f"{salt}|{int(user_id)}".encode("utf-8")).digest(), "big") % 10_000


def _zip_member(bundle: zipfile.ZipFile, suffix: str) -> str:
    matches = [name for name in bundle.namelist() if name.endswith(suffix)]
    if len(matches) != 1:
        raise RuntimeError(f"archive member ambiguity: {suffix}")
    return matches[0]


def scan_membership(contract: dict[str, Any], universe: set[int]) -> tuple[dict[str, dict[int, list[int]]], dict[str, int]]:
    archive = verify(contract["inputs"]["movielens_archive"])
    old = np.fromiter((old_user_bucket(uid) <= 59 for uid in range(MAX_USER_ID + 1)), dtype=bool)
    parent_role = np.fromiter((user_role_bucket(uid) for uid in range(MAX_USER_ID + 1)), dtype=np.uint16)
    phase = np.fromiter((phase_bucket(uid) for uid in range(MAX_USER_ID + 1)), dtype=np.uint16)
    salt = str(contract["population"]["split_salt"])
    split = np.fromiter((split_bucket(uid, salt) for uid in range(MAX_USER_ID + 1)), dtype=np.uint16)
    histories: dict[str, dict[int, list[int]]] = {"SELECTION": {}, "REPLICATION": {}}
    counters = {
        "raw_rows": 0,
        "discarded_after_user_id": 0,
        "selection_user_movie_memberships": 0,
        "replication_user_movie_memberships": 0,
        "rating_values_parsed": 0,
        "timestamps_parsed": 0,
        "rec_ev_028_attribution_or_future_movie_membership_opened": 0,
    }
    with zipfile.ZipFile(archive) as bundle, bundle.open(_zip_member(bundle, "ratings.csv")) as handle:
        if handle.readline().rstrip(b"\r\n") != b"userId,movieId,rating,timestamp":
            raise RuntimeError("MovieLens ratings header drift")
        for raw in handle:
            counters["raw_rows"] += 1
            first = raw.find(b",")
            if first <= 0:
                raise RuntimeError("malformed row before user firewall")
            uid = int(raw[:first])
            allowed = (
                0 < uid <= MAX_USER_ID
                and bool(old[uid])
                and int(parent_role[uid]) <= 5999
                and int(phase[uid]) <= 7999
                and int(split[uid]) >= 8000
            )
            if not allowed:
                counters["discarded_after_user_id"] += 1
                continue
            second = raw.find(b",", first + 1)
            if second <= first + 1:
                raise RuntimeError("malformed allowed movie field")
            movie = int(raw[first + 1 : second])
            if movie not in universe:
                continue
            cohort = "SELECTION" if int(split[uid]) <= 8999 else "REPLICATION"
            histories[cohort].setdefault(uid, []).append(movie)
            counters[f"{cohort.lower()}_user_movie_memberships"] += 1
    if counters["raw_rows"] != EXPECTED_RATING_ROWS or counters["rating_values_parsed"] or counters["timestamps_parsed"]:
        raise RuntimeError("membership reader firewall drift")
    for cohort in histories:
        for uid, movies in histories[cohort].items():
            if len(movies) != len(set(movies)):
                raise RuntimeError(f"duplicate user/movie membership: {cohort} {uid}")
    return histories, counters


def build_membership(contract: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    parent = json.loads(verify(contract["inputs"]["parent_contract"]).read_text(encoding="utf-8"))
    universe, korean, recent, pre2020 = common_sets(parent)
    histories, reader = scan_membership(contract, universe)
    fold_salt = str(parent["strict_item_firewall"]["fold_salt"])
    target_n_by_slug = {slug: 20 if slug.startswith("R") and slug != "RECENT" else 4 for slug, _, _ in OUTERS}
    provisional: list[dict[str, Any]] = []
    for cohort in ("SELECTION", "REPLICATION"):
        for slug, track, fold in OUTERS:
            target_n = target_n_by_slug[slug]
            eligible = []
            pools: dict[str, tuple[list[int], list[int]]] = {}
            for uid, movies in histories[cohort].items():
                warm, cold = outer_pools(
                    movies,
                    slug=slug,
                    track=track,
                    fold=fold,
                    fold_salt=fold_salt,
                    universe=universe,
                    korean=korean,
                    recent=recent,
                    pre2020=pre2020,
                )
                key = user_key(uid)
                pools[key] = (warm, cold)
                if len(cold) >= target_n and len(warm) >= 30:
                    eligible.append({"user_key": key, "cold_movie_ids": cold})
            selected = balanced_targets(
                eligible,
                track=track,
                fold=fold,
                target_n=target_n,
                salt=f"{contract['membership']['target_assignment_salt']}|{cohort}",
                target_order_salt=str(contract["membership"]["target_order_salt"]),
            )
            for row in eligible:
                key = str(row["user_key"])
                warm, _ = pools[key]
                provisional.append({
                    "cohort": cohort,
                    "outer": slug,
                    "track": track,
                    "fold_or_domain": fold,
                    "user_key": key,
                    "warm_movie_ids": warm,
                    "target_movie_ids": selected[key],
                })
    global_targets: dict[tuple[str, str], set[int]] = defaultdict(set)
    for row in provisional:
        global_targets[(row["cohort"], row["user_key"])].update(map(int, row["target_movie_ids"]))
    retained = []
    dropped = Counter()
    for row in provisional:
        key = (row["cohort"], row["user_key"])
        candidates = [movie for movie in row["warm_movie_ids"] if movie not in global_targets[key]]
        ordered = hash_order(
            candidates,
            salt=str(contract["membership"]["profile_order_salt"]),
            track=str(row["track"]),
            fold_or_domain=str(row["fold_or_domain"]),
            key=str(row["user_key"]),
        )
        if len(ordered) < 30:
            dropped[(row["cohort"], row["outer"])] += 1
            continue
        retained.append({
            "cohort": row["cohort"],
            "outer": row["outer"],
            "track": row["track"],
            "fold_or_domain": row["fold_or_domain"],
            "user_key": row["user_key"],
            "profile_movie_ids": ordered[:30],
            "target_movie_ids": row["target_movie_ids"],
        })
    frame = pd.DataFrame(retained).sort_values(["cohort", "outer", "user_key"], kind="stable", ignore_index=True)
    donors: dict[tuple[str, str, str], str] = {}
    coverage: dict[str, dict[str, Any]] = {"SELECTION": {}, "REPLICATION": {}}
    for cohort in ("SELECTION", "REPLICATION"):
        for slug, _, _ in OUTERS:
            group = frame.loc[frame["cohort"].eq(cohort) & frame["outer"].eq(slug)]
            keys = group["user_key"].astype(str).tolist()
            if len(keys) < 2:
                raise RuntimeError(f"insufficient donor rows: {cohort} {slug}")
            for index, key in enumerate(keys):
                donors[(cohort, slug, key)] = keys[(index + 1) % len(keys)]
            coverage[cohort][slug] = _coverage_truth(
                slug=slug,
                targets=group["target_movie_ids"].tolist(),
                users=len(group),
                thresholds=contract["coverage_gates"],
            )
    frame["donor_user_key"] = [donors[(str(row.cohort), str(row.outer), str(row.user_key))] for row in frame.itertuples(index=False)]
    for row in frame.itertuples(index=False):
        if (
            len(row.profile_movie_ids) != 30
            or len(row.target_movie_ids) != target_n_by_slug[str(row.outer)]
            or set(map(int, row.profile_movie_ids)) & global_targets[(str(row.cohort), str(row.user_key))]
            or str(row.user_key) == str(row.donor_user_key)
        ):
            raise RuntimeError("retained membership semantic drift")
    all_pass = all(
        value["coverage_truth"] == "BROAD_ITEM_ELIGIBLE"
        for cohort in coverage.values()
        for value in cohort.values()
    )
    summary = {
        "status": "REC_EV_029_MEMBERSHIP_PASS" if all_pass else "REC_EV_029_MEMBERSHIP_COVERAGE_FAIL",
        "rows": len(frame),
        "users": {
            cohort: int(frame.loc[frame["cohort"].eq(cohort), "user_key"].nunique())
            for cohort in ("SELECTION", "REPLICATION")
        },
        "coverage": coverage,
        "dropped_after_global_target_union": {
            f"{cohort}:{slug}": int(dropped[(cohort, slug)])
            for cohort in ("SELECTION", "REPLICATION")
            for slug, _, _ in OUTERS
        },
        "reader": reader,
        "global_profile_target_intersection_pairs": 0,
        "rating_values_parsed": False,
        "timestamps_parsed": False,
        "rec_ev_028_attribution_or_future_membership_opened": False,
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "product_policy_changed": False,
    }
    return frame, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT)
    args = parser.parse_args()
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    frame, summary = build_membership(contract)
    output_root = ROOT / str(contract["output_root"])
    output_root.mkdir(parents=True, exist_ok=True)
    membership_path = output_root / "cache/membership.parquet"
    membership_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(membership_path, index=False)
    summary["contract"] = artifact(args.contract)
    summary["membership"] = artifact(membership_path)
    summary["implementation"] = artifact(Path(__file__))
    summary_path = output_root / "membership-preflight.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["status"] != "REC_EV_029_MEMBERSHIP_PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
