"""Build untouched REC-EV-030 future-user membership without rating values."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from typing import Any, Mapping
import zipfile

import numpy as np
import pandas as pd

from build_rec_ev_019b_features import sha256_file
from preflight_rec_ev_027_membership import hash_order
from preflight_rec_ev_028a1_membership import _coverage_truth, balanced_targets
from preflight_rec_ev_028a_membership import common_sets, outer_pools, phase_bucket
from rec_ev_022a_core import old_user_bucket, user_key, user_role_bucket


ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "docs/recommendation/contracts/rec-ev-030a-membership-execution.json"
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


def verify(spec: Mapping[str, Any]) -> Path:
    raw = Path(str(spec["path"]))
    path = raw if raw.is_absolute() else ROOT / raw
    if not path.is_file() or path.stat().st_size != int(spec["bytes"]) or sha256_file(path) != str(spec["sha256"]):
        raise RuntimeError(f"artifact drift: {path}")
    return path.resolve()


def _zip_member(bundle: zipfile.ZipFile, suffix: str) -> str:
    matches = [name for name in bundle.namelist() if name.endswith(suffix)]
    if len(matches) != 1:
        raise RuntimeError(f"archive member ambiguity: {suffix}")
    return matches[0]


def scan_future_membership(contract: Mapping[str, Any], universe: set[int]) -> tuple[dict[int, list[int]], dict[str, int]]:
    archive = verify(contract["inputs"]["movielens_archive"])
    old = np.fromiter((old_user_bucket(uid) <= 59 for uid in range(MAX_USER_ID + 1)), dtype=bool)
    role = np.fromiter((user_role_bucket(uid) for uid in range(MAX_USER_ID + 1)), dtype=np.uint16)
    phase = np.fromiter((phase_bucket(uid) for uid in range(MAX_USER_ID + 1)), dtype=np.uint16)
    histories: dict[int, list[int]] = {}
    counters = {
        "raw_rows": 0,
        "discarded_after_user_id": 0,
        "future_user_movie_memberships": 0,
        "rec_ev_028_attribution_or_fit_movie_membership_opened": 0,
        "rating_values_parsed": 0,
        "timestamps_parsed": 0,
    }
    with zipfile.ZipFile(archive) as bundle, bundle.open(_zip_member(bundle, "ratings.csv")) as handle:
        if handle.readline().rstrip(b"\r\n") != b"userId,movieId,rating,timestamp":
            raise RuntimeError("MovieLens header drift")
        for raw in handle:
            counters["raw_rows"] += 1
            first = raw.find(b",")
            if first <= 0:
                raise RuntimeError("malformed row before user firewall")
            uid = int(raw[:first])
            allowed = (
                0 < uid <= MAX_USER_ID
                and bool(old[uid])
                and int(role[uid]) <= 5999
                and int(phase[uid]) >= 9000
            )
            if not allowed:
                counters["discarded_after_user_id"] += 1
                continue
            second = raw.find(b",", first + 1)
            if second <= first + 1:
                raise RuntimeError("malformed future movie field")
            movie = int(raw[first + 1 : second])
            if movie in universe:
                histories.setdefault(uid, []).append(movie)
                counters["future_user_movie_memberships"] += 1
    if (
        counters["raw_rows"] != EXPECTED_RATING_ROWS
        or counters["rating_values_parsed"]
        or counters["timestamps_parsed"]
        or counters["rec_ev_028_attribution_or_fit_movie_membership_opened"]
    ):
        raise RuntimeError("future membership reader firewall drift")
    for uid, movies in histories.items():
        if len(movies) != len(set(movies)):
            raise RuntimeError(f"duplicate future user/movie membership: {uid}")
    return histories, counters


def build_membership(contract: Mapping[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    design = json.loads(verify(contract["inputs"]["design_contract"]).read_text(encoding="utf-8"))
    parent = json.loads(verify(contract["inputs"]["parent_contract"]).read_text(encoding="utf-8"))
    universe, korean, recent, pre2020 = common_sets(parent)
    histories, reader = scan_future_membership(contract, universe)
    fold_salt = str(parent["strict_item_firewall"]["fold_salt"])
    provisional = []
    target_n_by_slug = dict(design["membership"]["target_n"])
    for slug, track, fold in OUTERS:
        target_n = int(target_n_by_slug[slug])
        eligible = []
        pools: dict[str, tuple[list[int], list[int]]] = {}
        for uid, movies in histories.items():
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
            salt="rec-ev-030-balanced-target-v1",
            target_order_salt="rec-ev-030-target-order-v1",
        )
        for row in eligible:
            key = str(row["user_key"])
            provisional.append({
                "outer": slug,
                "track": track,
                "fold_or_domain": fold,
                "user_key": key,
                "warm_movie_ids": pools[key][0],
                "target_movie_ids": selected[key],
            })
    global_targets: dict[str, set[int]] = defaultdict(set)
    for row in provisional:
        global_targets[str(row["user_key"])].update(map(int, row["target_movie_ids"]))
    retained = []
    dropped = Counter()
    for row in provisional:
        candidates = [movie for movie in row["warm_movie_ids"] if movie not in global_targets[str(row["user_key"])]]
        ordered = hash_order(
            candidates,
            salt="rec-ev-030-profile-order-v1",
            track=str(row["track"]),
            fold_or_domain=str(row["fold_or_domain"]),
            key=str(row["user_key"]),
        )
        if len(ordered) < 30:
            dropped[str(row["outer"])] += 1
            continue
        retained.append({
            "outer": row["outer"],
            "track": row["track"],
            "fold_or_domain": row["fold_or_domain"],
            "user_key": row["user_key"],
            "profile_movie_ids": ordered[:30],
            "target_movie_ids": row["target_movie_ids"],
        })
    frame = pd.DataFrame(retained).sort_values(["outer", "user_key"], kind="stable", ignore_index=True)
    coverage = {}
    donors: dict[tuple[str, str], str] = {}
    for slug, _, _ in OUTERS:
        group = frame.loc[frame["outer"].eq(slug)]
        keys = group["user_key"].astype(str).tolist()
        if len(keys) < 2:
            raise RuntimeError(f"insufficient future donor rows: {slug}")
        for index, key in enumerate(keys):
            donors[(slug, key)] = keys[(index + 1) % len(keys)]
        coverage[slug] = _coverage_truth(
            slug=slug,
            targets=group["target_movie_ids"].tolist(),
            users=len(group),
            thresholds=design["coverage_gates"],
        )
    frame["donor_user_key"] = [donors[(str(row.outer), str(row.user_key))] for row in frame.itertuples(index=False)]
    for row in frame.itertuples(index=False):
        if (
            len(row.profile_movie_ids) != 30
            or len(row.target_movie_ids) != int(target_n_by_slug[str(row.outer)])
            or set(map(int, row.profile_movie_ids)) & global_targets[str(row.user_key)]
            or str(row.user_key) == str(row.donor_user_key)
        ):
            raise RuntimeError("future retained membership drift")
    all_pass = all(value["coverage_truth"] == "BROAD_ITEM_ELIGIBLE" for value in coverage.values())
    summary = {
        "status": "REC_EV_030_MEMBERSHIP_PASS" if all_pass else "REC_EV_030_MEMBERSHIP_COVERAGE_FAIL",
        "rows": len(frame),
        "users": int(frame["user_key"].nunique()),
        "coverage": coverage,
        "dropped_after_global_target_union": {slug: int(dropped[slug]) for slug, _, _ in OUTERS},
        "reader": reader,
        "global_profile_target_intersection_pairs": 0,
        "rating_values_parsed": False,
        "timestamps_parsed": False,
        "rec_ev_028_attribution_or_fit_membership_opened": False,
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
    from validate_rec_ev_030_membership import validate
    validate(contract, args.contract.resolve())
    if contract["implementation_audit"].get("status") != "REC_EV_030_MEMBERSHIP_IMPLEMENTATION_AUDIT_PASS":
        raise RuntimeError("independent membership implementation audit has not passed")
    frame, summary = build_membership(contract)
    output_root = ROOT / str(contract["output_root"])
    membership_path = output_root / "cache/membership.parquet"
    membership_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(membership_path, index=False)
    summary["contract"] = artifact(args.contract)
    summary["membership"] = artifact(membership_path)
    summary["implementation"] = artifact(Path(__file__))
    summary_path = output_root / "membership-preflight.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["status"] != "REC_EV_030_MEMBERSHIP_PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
