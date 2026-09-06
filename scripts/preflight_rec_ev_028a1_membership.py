"""Materialize audited REC-EV-028A1 balanced membership without rating values."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

from build_rec_ev_019b_features import sha256_file
from preflight_rec_ev_027_membership import hash_order
from preflight_rec_ev_028a_membership import (
    OUTERS,
    common_sets,
    effective_items,
    outer_pools,
    scan_attribution_membership,
)
from rec_ev_022a_core import user_key
from validate_rec_ev_028a1_amendment import DEFAULT, ROOT, validate


def _selection_digest(
    *, salt: str, track: str, fold: str, key: str, movie_id: int
) -> bytes:
    return hashlib.sha256(
        f"{salt}|{track}|{fold}|{key}|{int(movie_id)}".encode("utf-8")
    ).digest()


def balanced_targets(
    eligible: list[dict[str, Any]],
    *,
    track: str,
    fold: str,
    target_n: int,
    salt: str,
    target_order_salt: str,
) -> dict[str, list[int]]:
    """Apply the audited sequential load/availability allocator exactly."""
    availability = Counter(
        movie for row in eligible for movie in set(map(int, row["cold_movie_ids"]))
    )
    selected_load: Counter[int] = Counter()
    selected: dict[str, list[int]] = {}
    ordered_users = sorted(
        eligible,
        key=lambda row: (len(row["cold_movie_ids"]), str(row["user_key"]).encode("utf-8")),
    )
    for row in ordered_users:
        key = str(row["user_key"])
        cold = sorted(
            set(map(int, row["cold_movie_ids"])),
            key=lambda movie: (
                selected_load[movie],
                availability[movie],
                _selection_digest(salt=salt, track=track, fold=fold, key=key, movie_id=movie),
                movie,
            ),
        )
        chosen = cold[:target_n]
        if len(chosen) != target_n:
            raise RuntimeError("eligible user lacks required target count")
        for movie in chosen:
            selected_load[movie] += 1
        selected[key] = hash_order(
            chosen,
            salt=target_order_salt,
            track=track,
            fold_or_domain=fold,
            key=key,
        )
    return selected


def _coverage_truth(
    *, slug: str, targets: list[list[int]], users: int, thresholds: dict[str, Any]
) -> dict[str, Any]:
    unique = len({movie for row in targets for movie in row})
    effective = effective_items(targets)
    counts = Counter(movie for row in targets for movie in row)
    if slug in {"R0", "R1", "R2", "R3", "R4"}:
        threshold_key = "EACH_RANDOM_FOLD"
    else:
        threshold_key = slug
    broad = (
        users >= int(thresholds["minimum_users"][threshold_key])
        and unique >= int(thresholds["minimum_unique_target_items"][threshold_key])
        and effective >= float(thresholds["minimum_effective_target_items"][threshold_key])
    )
    return {
        "users": users,
        "target_slots": int(sum(map(len, targets))),
        "unique_target_items": unique,
        "effective_target_items": effective,
        "maximum_target_item_load": max(counts.values()),
        "top10_target_slot_share": sum(value for _, value in counts.most_common(10)) / sum(counts.values()),
        "coverage_truth": "BROAD_ITEM_ELIGIBLE" if broad else "POPULAR_RATED_PROXY_ONLY",
    }


def build_membership(
    amendment: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    base_path = ROOT / amendment["base_contract"]["path"]
    base = json.loads(base_path.read_text(encoding="utf-8"))
    parent_path = ROOT / base["inputs"]["parent_contract"]["path"]
    parent = json.loads(parent_path.read_text(encoding="utf-8"))
    universe, korean, recent, pre2020 = common_sets(parent)
    archive = Path(parent["allowed_input_artifacts"]["movielens_archive"]["path"])
    histories, reader = scan_attribution_membership(archive, universe)
    fold_salt = parent["strict_item_firewall"]["fold_salt"]
    profile_salt = base["membership"]["profile_order_salt"]
    target_order_salt = base["membership"]["target_order_salt"]
    selection_salt = amendment["balanced_target_selection"]["selection_salt"]

    rows: list[dict[str, Any]] = []
    coverage: dict[str, Any] = {}
    for slug, track, fold in OUTERS:
        target_n = 20 if track == "RANDOM_ITEM_COLD" else 4
        eligible: list[dict[str, Any]] = []
        for uid, movies in histories.items():
            if len(movies) != len(set(movies)):
                raise RuntimeError("duplicate attribution membership")
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
            if len(warm) >= 12 and len(cold) >= target_n:
                key = user_key(uid)
                eligible.append(
                    {
                        "user_key": key,
                        "profile_movie_ids": hash_order(
                            warm,
                            salt=profile_salt,
                            track=track,
                            fold_or_domain=fold,
                            key=key,
                        )[:12],
                        "cold_movie_ids": list(map(int, cold)),
                    }
                )
        chosen_by_user = balanced_targets(
            eligible,
            track=track,
            fold=fold,
            target_n=target_n,
            salt=selection_salt,
            target_order_salt=target_order_salt,
        )
        for row in eligible:
            key = str(row["user_key"])
            chosen = chosen_by_user[key]
            profile = row["profile_movie_ids"]
            if set(profile) & set(chosen):
                raise RuntimeError("profile target overlap")
            rows.append(
                {
                    "outer": slug,
                    "track": track,
                    "fold_or_domain": fold,
                    "user_key": key,
                    "profile_movie_ids": profile,
                    "target_movie_ids": chosen,
                }
            )
        coverage[slug] = _coverage_truth(
            slug=slug,
            targets=[chosen_by_user[str(row["user_key"])] for row in eligible],
            users=len(eligible),
            thresholds=base["coverage_gates"],
        )

    frame = pd.DataFrame(rows).sort_values(["outer", "user_key"], kind="stable", ignore_index=True)
    if frame.empty or frame.duplicated(["outer", "user_key"]).any():
        raise RuntimeError("REC-EV-028A1 membership identity drift")
    donors: dict[tuple[str, str], str] = {}
    for outer, group in frame.groupby("outer", sort=False):
        keys = group["user_key"].tolist()
        if len(keys) < 2:
            raise RuntimeError("cyclic donor needs at least two users")
        for index, key in enumerate(keys):
            donors[(str(outer), str(key))] = str(keys[(index + 1) % len(keys)])
    frame["donor_user_key"] = [donors[(row.outer, row.user_key)] for row in frame.itertuples(index=False)]
    if any(row.donor_user_key == row.user_key for row in frame.itertuples(index=False)):
        raise RuntimeError("donor derangement failure")

    random_frame = frame.loc[frame["track"].eq("RANDOM_ITEM_COLD")]
    random_broad = all(coverage[f"R{fold}"]["coverage_truth"] == "BROAD_ITEM_ELIGIBLE" for fold in range(5))
    coverage["RANDOM_POOLED"] = {
        "users": int(random_frame["user_key"].nunique()),
        "unique_target_items": int(len({movie for values in random_frame["target_movie_ids"] for movie in values})),
        "effective_target_items": effective_items(random_frame["target_movie_ids"].tolist()),
        "coverage_truth": "BROAD_ITEM_ELIGIBLE" if random_broad else "POPULAR_RATED_PROXY_ONLY",
        "gate": "CONJUNCTION_OF_R0_TO_R4",
    }
    all_broad = all(coverage[slug]["coverage_truth"] == "BROAD_ITEM_ELIGIBLE" for slug, _, _ in OUTERS)
    summary = {
        "schema_version": 1,
        "evidence_id": "REC-EV-028A1-BALANCED-MEMBERSHIP",
        "status": "BALANCED_MEMBERSHIP_PREFLIGHT_PASS" if all_broad else "BALANCED_MEMBERSHIP_PREFLIGHT_STOP",
        "common_support_items": len(universe),
        "korean_origin_proxy_items": len(korean),
        "release_2020_2023_items": len(recent),
        "coverage": coverage,
        "all_outer_coverage_gates_pass": all_broad,
        "reader": reader,
        "rating_values_parsed": False,
        "timestamps_parsed": False,
        "future_membership_materialized": False,
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "product_policy_changed": False,
    }
    return frame, summary


def atomic_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    frame.to_parquet(temporary, index=False)
    os.replace(temporary, path)


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--amendment", type=Path, default=DEFAULT)
    parser.add_argument("--output-root", type=Path, default=ROOT / "outputs/recommendation-evidence/rec-ev-028a1")
    args = parser.parse_args()
    amendment = json.loads(args.amendment.read_text(encoding="utf-8"))
    validate(amendment, args.amendment.resolve())
    frame, summary = build_membership(amendment)
    output_root = args.output_root.resolve()
    membership_path = output_root / "cache/membership.parquet"
    atomic_parquet(membership_path, frame)
    summary["membership"] = {
        "path": membership_path.relative_to(ROOT).as_posix(),
        "bytes": membership_path.stat().st_size,
        "sha256": sha256_file(membership_path),
    }
    implementation_path = Path(__file__).resolve()
    summary["implementation"] = {
        "path": implementation_path.relative_to(ROOT).as_posix(),
        "bytes": implementation_path.stat().st_size,
        "sha256": sha256_file(implementation_path),
    }
    summary["amendment"] = {
        "path": args.amendment.resolve().relative_to(ROOT).as_posix(),
        "bytes": args.amendment.stat().st_size,
        "sha256": sha256_file(args.amendment),
    }
    atomic_json(output_root / "membership-preflight.json", summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
