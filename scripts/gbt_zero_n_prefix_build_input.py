"""Build controlled exact-K prefix episodes for the S15P21E106-622 policy study.

The fixed-calendar experiment answers an out-of-time robustness question, but it
does not provide enough users whose *entire* history happens to equal K at one
calendar cutoff.  This builder answers the intended input-size question instead:
for each user it chooses one leakage-safe anchor with at least 50 supported past
ratings and ten later observed judgments, then exposes the most recent
0/1/2/5/10/20/40/50 ratings to the model while keeping the target set fixed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import heapq
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq

from gbt_zero_n_build_input import (
    JsonlWriter,
    Rating,
    file_sha256,
    n_bucket,
    role_for_user,
    stable_rank,
    validation_split,
)


ROLES = ("TRAIN", "VALIDATION", "FINAL_TEST")
K_VALUES = (0, 1, 2, 5, 10, 20, 40, 50)
MAX_K = max(K_VALUES)
TARGET_JUDGMENTS = 10


def prediction_year(prediction_at: int) -> int:
    return datetime.fromtimestamp(prediction_at, timezone.utc).year


@dataclass
class PrefixCase:
    uid: int
    role: str
    prediction_at: int
    source_history: list[Rating]
    targets: list[Rating]
    rank: int
    evaluation_split: str


def find_anchor(
    uid: int,
    role: str,
    events: list[Rating],
    release_years: dict[int, int | None],
    seed: int,
) -> PrefixCase | None:
    """Choose the earliest timestamp boundary with 50 past and 10 future rows.

    Timestamp ties are never split.  This avoids inventing an ordering inside a
    MovieLens batch while still making every history event strictly earlier than
    every target event.
    """
    events = sorted(events, key=lambda row: (row.timestamp, row.event_id or str(row.movie_id)))
    starts = [index for index in range(len(events)) if index == 0 or events[index - 1].timestamp < events[index].timestamp]
    for index in starts:
        anchor_timestamp = events[index].timestamp
        if index == 0:
            continue
        prediction_at = anchor_timestamp - 1
        if events[index - 1].timestamp >= prediction_at:
            continue
        as_of_year = prediction_year(prediction_at)
        history = [
            row for row in events[:index]
            if release_years.get(row.movie_id) is not None
            and int(release_years[row.movie_id]) <= as_of_year
        ]
        if len(history) < MAX_K:
            continue
        history_movies = {row.movie_id for row in history}
        future = [
            row for row in events[index:]
            if row.movie_id not in history_movies
            and release_years.get(row.movie_id) is not None
            and int(release_years[row.movie_id]) <= as_of_year
        ]
        if len(future) < TARGET_JUDGMENTS:
            continue
        targets = future[:TARGET_JUDGMENTS]
        if any(row.timestamp >= anchor_timestamp for row in history):
            raise RuntimeError("anchor split a timestamp tie")
        if any(row.timestamp >= prediction_at for row in history):
            raise RuntimeError("history is not strictly before prediction_at")
        if any(row.timestamp <= prediction_at for row in targets):
            raise RuntimeError("target is not after the prefix anchor")
        return PrefixCase(
            uid=uid,
            role=role,
            prediction_at=prediction_at,
            source_history=history,
            targets=targets,
            rank=stable_rank(seed, "controlled-prefix-sample", uid),
            evaluation_split=validation_split(seed, uid) if role == "VALIDATION" else role,
        )
    return None


def push(heap: list, case: PrefixCase, limit: int) -> None:
    entry = (-case.rank, -case.uid, case)
    if len(heap) < limit:
        heapq.heappush(heap, entry)
    elif entry > heap[0]:
        heapq.heapreplace(heap, entry)


def history_view(case: PrefixCase, k: int) -> tuple[list[Rating], dict[str, int | bool]]:
    if k not in K_VALUES:
        raise ValueError(f"unsupported exact K: {k}")
    history = case.source_history[-k:] if k else []
    total = len(case.source_history)
    return history, {
        "total_history_count": total,
        "provided_history_count": len(history),
        "supported_history_count": len(history),
        "source_history_count": total,
        "is_full_history": len(history) == total,
        "is_controlled_prefix": True,
    }


def select_cases(
    ratings_path: Path,
    release_years: dict[int, int | None],
    seed: int,
    goals: dict[str, int],
    excluded_validation_users: set[int] | None = None,
) -> dict[str, list[PrefixCase]]:
    heaps: dict[tuple[str, str], list] = defaultdict(list)
    excluded_validation_users = excluded_validation_users or set()

    def finish(uid: int | None, events: list[Rating]) -> None:
        if uid is None:
            return
        role = role_for_user(seed, uid)
        if goals[role] <= 0:
            return
        if role == "VALIDATION" and uid in excluded_validation_users:
            return
        case = find_anchor(uid, role, events, release_years, seed)
        if case is None:
            return
        if role == "VALIDATION":
            split_goal = goals[role] // 2 if case.evaluation_split == "SELECTION" else goals[role] - goals[role] // 2
            push(heaps[(role, case.evaluation_split)], case, split_goal)
        else:
            push(heaps[(role, role)], case, goals[role])

    current = None
    events: list[Rating] = []
    with ratings_path.open("r", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            uid = int(row["userId"])
            if current is not None and uid != current:
                finish(current, events)
                events = []
            current = uid
            movie_id = int(row["movieId"])
            events.append(Rating(movie_id, float(row["rating"]), int(row["timestamp"]), str(movie_id)))
    finish(current, events)

    selected: dict[str, list[PrefixCase]] = {role: [] for role in ROLES}
    for role in ROLES:
        if goals[role] == 0:
            continue
        if role == "VALIDATION":
            rows: list[PrefixCase] = []
            for split, goal in (
                ("SELECTION", goals[role] // 2),
                ("CONFIRM", goals[role] - goals[role] // 2),
            ):
                chosen = sorted((entry[2] for entry in heaps[(role, split)]), key=lambda row: (row.rank, row.uid))
                if len(chosen) < goal:
                    raise RuntimeError(f"{role}:{split}: eligible users {len(chosen)} below requested {goal}")
                rows.extend(chosen[:goal])
            selected[role] = sorted(rows, key=lambda row: row.uid)
        else:
            chosen = sorted((entry[2] for entry in heaps[(role, role)]), key=lambda row: (row.rank, row.uid))
            if len(chosen) < goals[role]:
                raise RuntimeError(f"{role}: eligible users {len(chosen)} below requested {goals[role]}")
            selected[role] = chosen[:goals[role]]
    return selected


def build(args: argparse.Namespace) -> dict:
    if args.output.exists():
        raise FileExistsError(f"output already exists: {args.output}")
    if args.validation_users % 2:
        raise ValueError("validation-users must be even for fixed SELECTION/CONFIRM halves")
    goals = {
        "TRAIN": args.train_users,
        "VALIDATION": args.validation_users,
        "FINAL_TEST": args.final_test_users,
    }
    release_years: dict[int, int | None] = {}
    with (args.movielens_root / "movies.csv").open("r", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            match = re.search(r"\((\d{4})\)\s*$", row["title"])
            release_years[int(row["movieId"])] = int(match.group(1)) if match else None
    excluded_validation_users: set[int] = set()
    if args.exclude_validation_users_parquet:
        excluded_validation_users = set(
            int(value) for value in pq.read_table(
                args.exclude_validation_users_parquet, columns=["uid"]
            )["uid"].to_pylist()
        )
    selected = select_cases(
        args.movielens_root / "ratings.csv",
        release_years,
        args.seed,
        goals,
        excluded_validation_users,
    )
    args.output.mkdir(parents=True)

    catalog_cache: dict[int, list[int]] = {}
    catalog_digest_cache: dict[int, str] = {}
    hash_cutoff = int(args.unknown_probability * (1 << 256))
    sampled_movies = [
        movie_id for movie_id in sorted(release_years)
        if int.from_bytes(hashlib.sha256(f"{args.seed}:{movie_id}".encode()).digest(), "big") < hash_cutoff
    ]

    episode_writer = JsonlWriter(args.output / "episodes.jsonl")
    candidate_writer = JsonlWriter(args.output / "candidates.jsonl")
    exact_users: dict[tuple[str, str, int], set[int]] = defaultdict(set)
    anchor_years: Counter[str] = Counter()
    source_history_buckets: Counter[str] = Counter()
    target_ratings: Counter[str] = Counter()

    for role in (item for item in ROLES if goals[item] > 0):
        for case in selected[role]:
            year = prediction_year(case.prediction_at)
            if year not in catalog_cache:
                catalog_cache[year] = [
                    movie_id for movie_id, release_year in sorted(release_years.items())
                    if release_year is not None and release_year <= year
                ]
                catalog_digest_cache[year] = hashlib.sha256(
                    ",".join(map(str, catalog_cache[year])).encode()
                ).hexdigest()
            catalog = catalog_cache[year]
            catalog_set = set(catalog)
            observed = {row.movie_id: row for row in case.targets}
            source_history_movies = {row.movie_id for row in case.source_history}
            excluded = source_history_movies | set(observed)
            unknown = [
                movie_id for movie_id in sampled_movies
                if movie_id in catalog_set and movie_id not in excluded
            ]
            if len(unknown) < 20:
                raise RuntimeError(f"unknown sample has fewer than 20 movies for anchor year {year}")
            pool = list(observed) + unknown
            pool.sort(key=lambda movie_id: (stable_rank(args.seed, "controlled-pool", case.uid, movie_id), movie_id))
            pool_digest = hashlib.sha256(",".join(map(str, pool)).encode()).hexdigest()
            excluded_digest = hashlib.sha256(",".join(map(str, sorted(source_history_movies))).encode()).hexdigest()
            eligible_digest = hashlib.sha256(
                f"CONTROLLED_PREFIX_V1:{catalog_digest_cache[year]}:{excluded_digest}".encode()
            ).hexdigest()
            anchor_years[f"{role}:{case.evaluation_split}:{year}"] += 1
            source_history_buckets[f"{role}:{case.evaluation_split}:{n_bucket(len(case.source_history))}"] += 1

            for target in case.targets:
                target_ratings[f"{role}:{target.rating:.1f}"] += 1
                for rank, movie_id in enumerate(pool):
                    rating = observed.get(movie_id)
                    state = (
                        "UNKNOWN_SAMPLED" if rating is None
                        else "POSITIVE_OBSERVED" if rating.rating >= args.positive_threshold
                        else "NEGATIVE_OBSERVED"
                    )
                    candidate_writer.write({
                        "role": role,
                        "uid": case.uid,
                        "prediction_at": case.prediction_at,
                        "evaluation_split": case.evaluation_split,
                        "target_movie_id": target.movie_id,
                        "candidate_movie_id": movie_id,
                        "candidate_rank": rank,
                        "label_state": state,
                        "observed_rating": None if rating is None else rating.rating,
                        "sampling_probability": args.unknown_probability if rating is None else None,
                        "importance_weight": 1.0 / args.unknown_probability if rating is None else None,
                        "eligible_population_digest": eligible_digest,
                    })
                for k in K_VALUES:
                    history, history_counts = history_view(case, k)
                    exact_users[(role, case.evaluation_split, k)].add(case.uid)
                    episode_writer.write({
                        "role": role,
                        "uid": case.uid,
                        "prediction_at": case.prediction_at,
                        "evaluation_split": case.evaluation_split,
                        "catalog_snapshot_at": case.prediction_at,
                        "target_movie_id": target.movie_id,
                        "target_rating": target.rating,
                        "target_event_id": target.event_id,
                        "target_event_at": target.timestamp,
                        "target_eligible": target.movie_id in catalog_set,
                        "eligible_population_digest": eligible_digest,
                        "eligible_population_digest_algorithm": "CONTROLLED_PREFIX_CATALOG_AND_SOURCE_HISTORY_V1",
                        "eligible_population_count": len(catalog) - len(source_history_movies & catalog_set),
                        "excluded_history_count": len(source_history_movies & catalog_set),
                        "unknown_sample_count": len(unknown),
                        "request_target_count": TARGET_JUDGMENTS,
                        **history_counts,
                        "n": k,
                        "n_bucket": n_bucket(k),
                        "candidate_digest": pool_digest,
                        "history": [
                            {
                                "event_id": row.event_id,
                                "movie_id": row.movie_id,
                                "rating": row.rating,
                                "event_at": row.timestamp,
                            }
                            for row in history
                        ],
                    })
    episode_writer.close()
    candidate_writer.close()

    exact_counts = {
        f"{role}:{split}:{k}": len(users)
        for (role, split, k), users in sorted(exact_users.items())
    }
    required_confirm = [
        {"k": k, "users": exact_counts.get(f"VALIDATION:CONFIRM:{k}", 0)}
        for k in K_VALUES
    ]
    insufficient = [row for row in required_confirm if row["users"] < args.minimum_users_per_k]
    report = {
        "schema_version": 1,
        "status": "INSUFFICIENT_SAMPLE" if insufficient else "PASS",
        "design": "CONTROLLED_RECENT_PREFIX_AT_PER_USER_ANCHOR_V1",
        "k_values": list(K_VALUES),
        "target_judgments_per_user": TARGET_JUDGMENTS,
        "minimum_users_per_k": args.minimum_users_per_k,
        "exact_k_user_counts": exact_counts,
        "required_confirm_cells": required_confirm,
        "insufficient_confirm_cells": insufficient,
        "anchor_year_user_counts": dict(sorted(anchor_years.items())),
        "source_history_user_counts": dict(sorted(source_history_buckets.items())),
        "target_rating_counts": dict(sorted(target_ratings.items())),
    }
    report_path = args.output / "split-distribution-report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    user_digests = {
        role: hashlib.sha256(",".join(map(str, sorted(case.uid for case in selected[role]))).encode()).hexdigest()
        for role in ROLES
    }
    if selected["FINAL_TEST"]:
        raise RuntimeError("controlled prefix study must keep FINAL_TEST physically omitted")
    if not args.final_test_seal_id:
        raise RuntimeError("--final-test-seal-id is required when FINAL_TEST users are omitted")
    manifest = {
        "schema_version": 3,
        "status": "PASS" if not insufficient else "INSUFFICIENT_SAMPLE",
        "policy_evidence_status": "PASS" if not insufficient else "INSUFFICIENT_SAMPLE",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "seed": args.seed,
        "experiment_design": "CONTROLLED_RECENT_PREFIX_AT_PER_USER_ANCHOR_V1",
        "k_values": list(K_VALUES),
        "minimum_users_per_k": args.minimum_users_per_k,
        "positive_threshold": args.positive_threshold,
        "input": {
            "ratings_sha256": file_sha256(args.movielens_root / "ratings.csv"),
            "movies_sha256": file_sha256(args.movielens_root / "movies.csv"),
        },
        "users": {role: len(selected[role]) for role in ROLES},
        "user_partition": {
            "algorithm": "SHA256_ROLE_THEN_HASHED_SELECTION_WITH_FIXED_VALIDATION_HALVES_V1",
            "seed": args.seed,
            "role_user_digests": user_digests,
        },
        "targets": {role: len(selected[role]) * TARGET_JUDGMENTS for role in ROLES},
        "episodes": {"rows": episode_writer.count, "sha256": episode_writer.sha256},
        "candidates": {"rows": candidate_writer.count, "sha256": candidate_writer.sha256},
        "distribution_report": {"status": report["status"], "sha256": file_sha256(report_path)},
        "target_policy": "NEXT_10_SUPPORTED_OBSERVED_JUDGMENTS_AFTER_TIMESTAMP_SAFE_ANCHOR",
        "history_policy": "MOST_RECENT_EXACT_K_SUPPORTED_EVENTS_FROM_SAME_PRE_ANCHOR_SOURCE_HISTORY",
        "candidate_pool_policy": "SAME_POOL_ACROSS_K; SOURCE_HISTORY_EXCLUDED_FROM_EVALUATION_ONLY",
        "catalog_as_of_policy": "MOVIELENS_TITLE_RELEASE_YEAR_LE_PREDICTION_YEAR; MISSING_YEAR_EXCLUDED",
        "unknown_sampling": {
            "algorithm": "SHA256_THRESHOLD_V1",
            "seed": args.seed,
            "probability": args.unknown_probability,
            "importance_weight": 1.0 / args.unknown_probability,
        },
        "selection_input_roles": ["TRAIN", "VALIDATION:SELECTION"],
        "confirmation_input_roles": ["VALIDATION:CONFIRM"],
        "excluded_validation_users": {
            "count": len(excluded_validation_users),
            "uid_digest": hashlib.sha256(
                ",".join(map(str, sorted(excluded_validation_users))).encode()
            ).hexdigest(),
            "source": (
                {
                    "path": str(args.exclude_validation_users_parquet),
                    "bytes": args.exclude_validation_users_parquet.stat().st_size,
                    "sha256": file_sha256(args.exclude_validation_users_parquet),
                }
                if args.exclude_validation_users_parquet else None
            ),
        },
        "final_test_seal_id": args.final_test_seal_id,
        "final_test": "PHYSICALLY_OMITTED",
        "final_test_opened": False,
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--movielens-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=622)
    parser.add_argument("--train-users", type=int, default=5000)
    parser.add_argument("--validation-users", type=int, default=1000)
    parser.add_argument("--final-test-users", type=int, default=0)
    parser.add_argument("--final-test-seal-id", required=True)
    parser.add_argument("--exclude-validation-users-parquet", type=Path)
    parser.add_argument("--positive-threshold", type=float, default=4.0)
    parser.add_argument("--unknown-probability", type=float, default=0.0012)
    parser.add_argument("--minimum-users-per-k", type=int, default=100)
    print(json.dumps(build(parser.parse_args()), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
