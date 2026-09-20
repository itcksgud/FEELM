"""Build FM-v3 source input and prove the full eligible TRAIN population was selected."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import gbt_zero_n_build_input as base


def file_pin(path: Path) -> dict[str, int | str]:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def serialized_role(line: str) -> str:
    match = re.search(r'"role"\s*:\s*"(TRAIN|VALIDATION|FINAL_TEST)"', line)
    if not match:
        raise RuntimeError("serialized row has no recognized role")
    return match.group(1)


def load_prior_validation_users(prior_input_root: Path) -> tuple[set[int], dict]:
    manifest_path = prior_input_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    users: set[int] = set()
    with (prior_input_root / "episodes.jsonl").open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            role = serialized_role(line)
            if role != "VALIDATION":
                continue
            users.add(int(json.loads(line)["uid"]))
    digest = hashlib.sha256(",".join(map(str, sorted(users))).encode()).hexdigest()
    expected_count = int(manifest["users"]["VALIDATION"])
    expected_digest = manifest["user_partition"]["role_user_digests"]["VALIDATION"]
    if len(users) != expected_count or digest != expected_digest:
        raise RuntimeError("prior validation user set differs from its frozen manifest")
    return users, {"manifest": file_pin(manifest_path), "users": len(users), "user_digest": digest,
                   "input_seed": int(manifest["seed"]), "role": "VALIDATION"}


def eligible_census(movielens_root: Path, seed: int, excluded_validation_users: set[int]) -> tuple[dict, int]:
    release_years = {}
    with (movielens_root / "movies.csv").open("r", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            match = re.search(r"\((\d{4})\)\s*$", row["title"])
            release_years[int(row["movieId"])] = int(match.group(1)) if match else None
    counts = {role: Counter() for role in ("TRAIN", "VALIDATION", "FINAL_TEST")}
    excluded_eligible_validation = 0
    current = role = None
    history_count = 0
    has_eligible_target = False
    start = end = catalog_year = 0

    def finish() -> None:
        nonlocal excluded_eligible_validation
        if current is None or not has_eligible_target:
            return
        if role == "VALIDATION" and current in excluded_validation_users:
            excluded_eligible_validation += 1
            return
        counts[role]["total"] += 1
        counts[role]["history_50_plus" if history_count >= 50 else "history_below_50"] += 1

    with (movielens_root / "ratings.csv").open("r", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            uid = int(row["userId"])
            if current is None or uid != current:
                finish()
                current = uid
                role = base.role_for_user(seed, uid)
                start, end = base.role_window(role)
                catalog_year = datetime.fromtimestamp(start + 1, timezone.utc).year
                history_count = 0
                has_eligible_target = False
            timestamp = int(row["timestamp"])
            movie_id = int(row["movieId"])
            if timestamp <= start:
                history_count += 1
            year = release_years.get(movie_id)
            if start < timestamp <= end and year is not None and year <= catalog_year:
                has_eligible_target = True
    finish()
    return ({role: dict(sorted(values.items())) for role, values in counts.items()},
            excluded_eligible_validation)


def select_cases_excluding_prior_validation(ratings_path: Path, seed: int, goals: dict[str, int], threshold: float,
                                            release_years: dict[int, int | None],
                                            excluded_validation_users: set[int]):
    heaps: dict[tuple[str, str], list] = defaultdict(list)
    train_support: Counter[int] = Counter()

    def finish(uid: int | None, events: list[base.Rating]) -> None:
        if uid is None:
            return
        events.sort(key=lambda row: (row.timestamp, row.event_id or str(row.movie_id)))
        role = base.role_for_user(seed, uid)
        if role == "TRAIN":
            train_support.update(row.movie_id for row in events if row.timestamp <= base.TRAIN_END)
        if role == "VALIDATION" and uid in excluded_validation_users:
            return
        start, end = base.role_window(role)
        history = [row for row in events if row.timestamp <= start]
        catalog_year = datetime.fromtimestamp(start + 1, timezone.utc).year
        available = [row for row in events if start < row.timestamp <= end
                     and release_years.get(row.movie_id) is not None
                     and release_years[row.movie_id] <= catalog_year]
        targets = base.choose_targets(seed, uid, available, threshold)
        if not targets:
            return
        group = "HIGH" if len(history) >= 50 else "LOW"
        case = base.Case(uid, role, start + 1, history, targets, base.stable_rank(seed, "sample", uid))
        base.push(heaps[(role, group)], case, max(goals[role], 100))

    current = None
    events: list[base.Rating] = []
    with ratings_path.open("r", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            uid = int(row["userId"])
            if current is not None and uid != current:
                finish(current, events)
                events = []
            current = uid
            movie_id = int(row["movieId"])
            events.append(base.Rating(movie_id, float(row["rating"]), int(row["timestamp"]), str(movie_id)))
    finish(current, events)

    selected = {}
    for role in base.ROLES:
        goal = goals[role]
        high = sorted((entry[2] for entry in heaps[(role, "HIGH")]), key=lambda row: (row.rank, row.uid))
        low = sorted((entry[2] for entry in heaps[(role, "LOW")]), key=lambda row: (row.rank, row.uid))
        high_goal = min(max(100, goal // 2), goal)
        chosen = high[:high_goal]
        chosen.extend(low[: goal - len(chosen)])
        if len(chosen) < goal:
            chosen.extend(high[high_goal:high_goal + goal - len(chosen)])
        if len(chosen) < goal:
            raise RuntimeError(f"{role}: eligible users {len(chosen)} below requested {goal}")
        selected[role] = sorted(chosen[:goal], key=lambda row: row.uid)
    return selected, train_support


def run(args: argparse.Namespace) -> dict:
    prior_validation_users, prior_exclusion = load_prior_validation_users(args.prior_input_root)
    census, excluded_eligible = eligible_census(args.movielens_root, args.seed, prior_validation_users)
    if args.train_users != census["TRAIN"]["total"]:
        raise RuntimeError(
            f"TRAIN request {args.train_users} is not the full eligible population {census['TRAIN']['total']}"
        )
    if args.validation_users > census["VALIDATION"]["total"]:
        raise RuntimeError("VALIDATION request exceeds eligible population")
    if args.final_test_users > census["FINAL_TEST"]["total"]:
        raise RuntimeError("FINAL_TEST request exceeds eligible population")
    original_select_cases = base.select_cases
    base.select_cases = lambda ratings_path, seed, goals, threshold, release_years: (
        select_cases_excluding_prior_validation(
            ratings_path, seed, goals, threshold, release_years, prior_validation_users
        )
    )
    try:
        manifest = base.build(args)
    finally:
        base.select_cases = original_select_cases
    if manifest["users"]["TRAIN"] != census["TRAIN"]["total"]:
        raise RuntimeError("built TRAIN user count is not census-saturated")
    manifest["eligible_census"] = {
        "algorithm": "FULL_RATINGS_USER_SCAN_MATCHING_GBT_ZERO_N_ELIGIBILITY_WITH_PRIOR_VALIDATION_USER_EXCLUSION_V1",
        "seed": args.seed,
        "roles": census,
        "excluded_prior_validation_eligible_users": excluded_eligible,
        "train_population_saturated": True,
    }
    selected_validation_users = set()
    with (args.output / "episodes.jsonl").open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip() or serialized_role(line) != "VALIDATION":
                continue
            selected_validation_users.add(int(json.loads(line)["uid"]))
    intersection = selected_validation_users & prior_validation_users
    if intersection:
        raise RuntimeError("new validation users overlap prior validation users")
    manifest["prior_validation_exclusion"] = prior_exclusion | {
        "policy": "EXCLUDE_ALL_PRIOR_SEED_622_VALIDATION_USERS_FROM_NEW_VALIDATION_V1",
        "new_validation_intersection_users": 0,
        "new_validation_users": len(selected_validation_users),
        "new_validation_user_digest": hashlib.sha256(
            ",".join(map(str, sorted(selected_validation_users))).encode()
        ).hexdigest(),
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--movielens-root", type=Path, required=True)
    parser.add_argument("--prior-input-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=3623)
    parser.add_argument("--train-users", type=int, default=21075)
    parser.add_argument("--validation-users", type=int, default=2500)
    parser.add_argument("--final-test-users", type=int, default=2000)
    parser.add_argument("--positive-threshold", type=float, default=4.0)
    parser.add_argument("--unknown-probability", type=float, default=0.0012)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
