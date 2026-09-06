"""Build REC-EV-028A2 globally label-disjoint membership without ratings."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

from build_rec_ev_019b_features import sha256_file
from preflight_rec_ev_027_membership import hash_order
from preflight_rec_ev_028a_membership import OUTERS, common_sets, outer_pools, scan_attribution_membership
from preflight_rec_ev_028a1_membership import _coverage_truth, balanced_targets
from rec_ev_022a_core import user_key
from validate_rec_ev_028a2_amendment import DEFAULT, ROOT, validate


def artifact(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "path": resolved.relative_to(ROOT).as_posix(),
        "bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def rebuild_a1_targets(
    histories: dict[int, list[int]],
    *,
    base: dict[str, Any],
    parent: dict[str, Any],
    a1: dict[str, Any],
    universe: set[int],
    korean: set[int],
    recent: set[int],
    pre2020: set[int],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows: list[dict[str, Any]] = []
    uid_by_key: dict[str, int] = {}
    fold_salt = parent["strict_item_firewall"]["fold_salt"]
    target_order_salt = base["membership"]["target_order_salt"]
    selection_salt = a1["balanced_target_selection"]["selection_salt"]
    for slug, track, fold in OUTERS:
        target_n = 20 if track == "RANDOM_ITEM_COLD" else 4
        eligible: list[dict[str, Any]] = []
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
            if len(warm) >= 12 and len(cold) >= target_n:
                key = user_key(uid)
                uid_by_key[key] = uid
                eligible.append({"user_key": key, "cold_movie_ids": list(map(int, cold))})
        selected = balanced_targets(
            eligible,
            track=track,
            fold=fold,
            target_n=target_n,
            salt=selection_salt,
            target_order_salt=target_order_salt,
        )
        for row in eligible:
            key = str(row["user_key"])
            rows.append(
                {
                    "outer": slug,
                    "track": track,
                    "fold_or_domain": fold,
                    "user_key": key,
                    "target_movie_ids": selected[key],
                }
            )
    return rows, uid_by_key


def assert_a1_reproduction(rows: list[dict[str, Any]], a1_membership_path: Path) -> None:
    actual = pd.DataFrame(rows).sort_values(["outer", "user_key"], kind="stable", ignore_index=True)
    expected = pd.read_parquet(
        a1_membership_path,
        columns=["outer", "track", "fold_or_domain", "user_key", "target_movie_ids"],
    ).sort_values(["outer", "user_key"], kind="stable", ignore_index=True)
    if len(actual) != len(expected):
        raise RuntimeError("A1 target reproduction row-count drift")
    scalar_columns = ["outer", "track", "fold_or_domain", "user_key"]
    if not actual[scalar_columns].astype(str).equals(expected[scalar_columns].astype(str)):
        raise RuntimeError("A1 target reproduction identity drift")
    if any(list(map(int, left)) != list(map(int, right)) for left, right in zip(actual["target_movie_ids"], expected["target_movie_ids"], strict=True)):
        raise RuntimeError("A1 target reproduction target drift")


def build_membership(amendment: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    base_path = ROOT / amendment["base_contract"]["path"]
    a1_path = ROOT / amendment["a1_amendment"]["path"]
    base = json.loads(base_path.read_text(encoding="utf-8"))
    a1 = json.loads(a1_path.read_text(encoding="utf-8"))
    parent = json.loads((ROOT / base["inputs"]["parent_contract"]["path"]).read_text(encoding="utf-8"))
    universe, korean, recent, pre2020 = common_sets(parent)
    archive = Path(parent["allowed_input_artifacts"]["movielens_archive"]["path"])
    histories, reader = scan_attribution_membership(archive, universe)
    a1_rows, uid_by_key = rebuild_a1_targets(
        histories,
        base=base,
        parent=parent,
        a1=a1,
        universe=universe,
        korean=korean,
        recent=recent,
        pre2020=pre2020,
    )
    a1_membership_path = ROOT / amendment["a1_balanced_membership"]["membership"]["path"]
    assert_a1_reproduction(a1_rows, a1_membership_path)

    targets_by_user: dict[str, set[int]] = {}
    for row in a1_rows:
        targets_by_user.setdefault(str(row["user_key"]), set()).update(map(int, row["target_movie_ids"]))
    fold_salt = parent["strict_item_firewall"]["fold_salt"]
    profile_salt = base["membership"]["profile_order_salt"]
    outer_definition = {slug: (track, fold) for slug, track, fold in OUTERS}
    retained: list[dict[str, Any]] = []
    dropped = Counter()
    for row in a1_rows:
        slug = str(row["outer"])
        track, fold = outer_definition[slug]
        key = str(row["user_key"])
        uid = uid_by_key[key]
        warm, _ = outer_pools(
            histories[uid],
            slug=slug,
            track=track,
            fold=fold,
            fold_salt=fold_salt,
            universe=universe,
            korean=korean,
            recent=recent,
            pre2020=pre2020,
        )
        profile_pool = [movie for movie in warm if movie not in targets_by_user[key]]
        if len(profile_pool) < 12:
            dropped[slug] += 1
            continue
        profile = hash_order(
            profile_pool,
            salt=profile_salt,
            track=track,
            fold_or_domain=fold,
            key=key,
        )[:12]
        retained.append({**row, "profile_movie_ids": profile})
    frame = pd.DataFrame(retained).sort_values(["outer", "user_key"], kind="stable", ignore_index=True)
    if frame.empty or frame.duplicated(["outer", "user_key"]).any():
        raise RuntimeError("REC-EV-028A2 membership identity drift")
    donors: dict[tuple[str, str], str] = {}
    for outer, group in frame.groupby("outer", sort=False):
        keys = group["user_key"].astype(str).tolist()
        if len(keys) < 2:
            raise RuntimeError("cyclic donor needs at least two retained users")
        for index, key in enumerate(keys):
            donors[(str(outer), key)] = keys[(index + 1) % len(keys)]
    frame["donor_user_key"] = [donors[(str(row.outer), str(row.user_key))] for row in frame.itertuples(index=False)]
    frame = frame[["outer", "track", "fold_or_domain", "user_key", "profile_movie_ids", "target_movie_ids", "donor_user_key"]]

    global_profile: dict[str, set[int]] = {}
    for row in frame.itertuples(index=False):
        key = str(row.user_key)
        profile = set(map(int, row.profile_movie_ids))
        target = set(map(int, row.target_movie_ids))
        target_n = 20 if str(row.outer) in {"R0", "R1", "R2", "R3", "R4"} else 4
        if len(profile) != 12 or len(target) != target_n:
            raise RuntimeError("profile or target cardinality drift")
        if profile & target or str(row.donor_user_key) == key:
            raise RuntimeError("outer disjointness or donor drift")
        global_profile.setdefault(key, set()).update(profile)
    overlap_pairs = sum(len(global_profile[key] & targets_by_user[key]) for key in global_profile)
    if overlap_pairs:
        raise RuntimeError(f"global target/profile firewall failure: {overlap_pairs}")

    coverage: dict[str, Any] = {}
    for slug, _, _ in OUTERS:
        group = frame.loc[frame["outer"].eq(slug)]
        coverage[slug] = _coverage_truth(
            slug=slug,
            targets=group["target_movie_ids"].tolist(),
            users=len(group),
            thresholds=base["coverage_gates"],
        )
    random_frame = frame.loc[frame["track"].eq("RANDOM_ITEM_COLD")]
    random_counts = Counter(movie for values in random_frame["target_movie_ids"] for movie in values)
    random_total = sum(random_counts.values())
    random_broad = all(coverage[f"R{fold}"]["coverage_truth"] == "BROAD_ITEM_ELIGIBLE" for fold in range(5))
    coverage["RANDOM_POOLED"] = {
        "users": int(random_frame["user_key"].nunique()),
        "target_slots": random_total,
        "unique_target_items": len(random_counts),
        "effective_target_items": random_total * random_total / sum(value * value for value in random_counts.values()),
        "maximum_target_item_load": max(random_counts.values()),
        "top10_target_slot_share": sum(value for _, value in random_counts.most_common(10)) / random_total,
        "coverage_truth": "BROAD_ITEM_ELIGIBLE" if random_broad else "POPULAR_RATED_PROXY_ONLY",
        "gate": "CONJUNCTION_OF_R0_TO_R4",
    }
    all_broad = all(coverage[slug]["coverage_truth"] == "BROAD_ITEM_ELIGIBLE" for slug, _, _ in OUTERS)
    summary = {
        "schema_version": 1,
        "evidence_id": "REC-EV-028A2-GLOBAL-LABEL-DISJOINT-MEMBERSHIP",
        "status": "GLOBAL_LABEL_FIREWALL_MEMBERSHIP_PASS" if all_broad and overlap_pairs == 0 else "GLOBAL_LABEL_FIREWALL_MEMBERSHIP_STOP",
        "common_support_items": len(universe),
        "korean_origin_proxy_items": len(korean),
        "release_2020_2023_items": len(recent),
        "a1_target_reproduction": "EXACT_SEMANTIC_MATCH",
        "a1_rows": len(a1_rows),
        "retained_rows": len(frame),
        "dropped_rows_by_outer": dict(sorted(dropped.items())),
        "global_profile_target_intersection_pairs": overlap_pairs,
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
    parser.add_argument("--output-root", type=Path, default=ROOT / "outputs/recommendation-evidence/rec-ev-028a2")
    args = parser.parse_args()
    amendment_path = args.amendment.resolve()
    output_root = args.output_root.resolve()
    amendment = json.loads(amendment_path.read_text(encoding="utf-8"))
    validate(amendment, amendment_path)
    if output_root != (ROOT / amendment["output_root"]).resolve():
        raise RuntimeError("REC-EV-028A2 output root must match audited amendment")
    frame, summary = build_membership(amendment)
    membership_path = output_root / "cache/membership.parquet"
    atomic_parquet(membership_path, frame)
    summary["membership"] = artifact(membership_path)
    dependencies = [
        Path(__file__).resolve(),
        ROOT / "scripts/preflight_rec_ev_028a1_membership.py",
        ROOT / "scripts/preflight_rec_ev_028a_membership.py",
        ROOT / "scripts/preflight_rec_ev_027_membership.py",
        ROOT / "scripts/rec_ev_022a_core.py",
        ROOT / "scripts/build_rec_ev_019b_features.py",
        ROOT / "scripts/validate_rec_ev_028a2_amendment.py",
        ROOT / "scripts/validate_rec_ev_028a1_amendment.py",
        ROOT / "scripts/validate_rec_ev_028a_contract.py",
    ]
    summary["transitive_implementations"] = [artifact(path) for path in dependencies]
    summary["amendment"] = artifact(amendment_path)
    atomic_json(output_root / "membership-preflight.json", summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
