"""Build deterministic mixed-rating 0-N episodes from the frozen MovieLens snapshot."""

from __future__ import annotations

import argparse
import csv
import hashlib
import heapq
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq


ROLES = ("TRAIN", "VALIDATION", "FINAL_TEST")
VALIDATION_SPLITS = ("SELECTION", "CONFIRM")
REQUIRED_CONFIRM_STRATA = ("0", "1", "2", "4", "5", "9", "10", "19", "20", "30-49", "50+")
N_VALUES = (0, 1, 2, 4, 5, 7, 10, 15, 20, 25, 40, 50)
TRAIN_END = 1577836799
VALIDATION_END = 1640995199
FINAL_TEST_END = 1697164147
TRAIN_START_EXCLUSIVE = 1483228799
VALIDATION_START_EXCLUSIVE = TRAIN_END
FINAL_TEST_START_EXCLUSIVE = VALIDATION_END


@dataclass(frozen=True)
class Rating:
    movie_id: int
    rating: float
    timestamp: int
    event_id: str = ""


@dataclass
class Case:
    uid: int
    role: str
    prediction_at: int
    history: list[Rating]
    targets: list[Rating]
    rank: int
    evaluation_split: str


def stable_rank(seed: int, *parts: object) -> int:
    value = ":".join([str(seed), *(str(part) for part in parts)]).encode()
    return int.from_bytes(hashlib.sha256(value).digest()[:8], "big")


def role_for_user(seed: int, uid: int) -> str:
    bucket = stable_rank(seed, "role", uid) % 100
    return "TRAIN" if bucket < 70 else "VALIDATION" if bucket < 85 else "FINAL_TEST"


def role_window(role: str) -> tuple[int, int]:
    return {
        "TRAIN": (TRAIN_START_EXCLUSIVE, TRAIN_END),
        "VALIDATION": (VALIDATION_START_EXCLUSIVE, VALIDATION_END),
        "FINAL_TEST": (FINAL_TEST_START_EXCLUSIVE, FINAL_TEST_END),
    }[role]


def n_bucket(value: int) -> str:
    boundaries = ((0, "0"), (1, "1"), (2, "2"), (4, "3-4"), (9, "5-9"),
                  (19, "10-19"), (29, "20-29"), (49, "30-49"))
    return next((label for upper, label in boundaries if value <= upper), "50+")


def validation_split(seed: int, uid: int) -> str:
    return VALIDATION_SPLITS[stable_rank(seed, "validation-split", uid) % len(VALIDATION_SPLITS)]


def confirmation_stratum(total: int) -> str:
    exact = {0, 1, 2, 4, 5, 9, 10, 19, 20}
    return str(total) if total in exact else n_bucket(total)


def support_bucket(value: int) -> str:
    return "0" if value == 0 else "1-9" if value < 10 else "10-49" if value < 50 else "50+"


def history_variant_sizes(total: int) -> list[int]:
    if total < 0:
        raise ValueError("history size cannot be negative")
    return sorted({*[value for value in N_VALUES[:-1] if value <= total], total})


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def choose_targets(seed: int, uid: int, values: list[Rating], threshold: float) -> list[Rating]:
    key = lambda row: (stable_rank(seed, "target", uid, row.event_id or row.movie_id), row.event_id or str(row.movie_id))
    return sorted(sorted(values, key=key)[:4], key=lambda row: (row.timestamp, row.event_id or str(row.movie_id)))


def push(heap: list, case: Case, limit: int) -> None:
    entry = (-case.rank, -case.uid, case)
    if len(heap) < limit:
        heapq.heappush(heap, entry)
    elif entry > heap[0]:
        heapq.heapreplace(heap, entry)


def select_cases(
    ratings_path: Path,
    seed: int,
    goals: dict[str, int],
    threshold: float,
    release_years: dict[int, int | None],
    excluded_validation_users: set[int] | None = None,
):
    heaps: dict[tuple[str, str], list] = defaultdict(list)
    train_support: Counter[int] = Counter()
    excluded_validation_users = excluded_validation_users or set()

    def finish(uid: int | None, events: list[Rating]) -> None:
        if uid is None:
            return
        events.sort(key=lambda row: (row.timestamp, row.event_id or str(row.movie_id)))
        role = role_for_user(seed, uid)
        if goals[role] <= 0 or (role == "VALIDATION" and uid in excluded_validation_users):
            return
        if role == "TRAIN":
            train_support.update(row.movie_id for row in events if row.timestamp <= TRAIN_END)
        start, end = role_window(role)
        history = [row for row in events if row.timestamp <= start]
        catalog_year = datetime.fromtimestamp(start + 1, timezone.utc).year
        available = [row for row in events if start < row.timestamp <= end
                     and release_years.get(row.movie_id) is not None
                     and release_years[row.movie_id] <= catalog_year]
        targets = choose_targets(seed, uid, available, threshold)
        if not targets:
            return
        group = "HIGH" if len(history) >= 50 else "LOW"
        split = validation_split(seed, uid) if role == "VALIDATION" else role
        case = Case(uid, role, start + 1, history, targets, stable_rank(seed, "sample", uid), split)
        if role == "VALIDATION":
            push(heaps[(role, f"{split}:ALL")], case, max(goals[role], 100))
            push(
                heaps[(role, f"{split}:{confirmation_stratum(len(history))}")],
                case,
                max(goals[role], 100),
            )
        else:
            push(heaps[(role, group)], case, max(goals[role], 100))

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

    selected = {}
    for role in ROLES:
        goal = goals[role]
        if goal == 0:
            selected[role] = []
            continue
        if role == "VALIDATION":
            role_selected: list[Case] = []
            split_goals = {"SELECTION": goal // 2, "CONFIRM": goal - goal // 2}
            for split, split_goal in split_goals.items():
                chosen_by_uid: dict[int, Case] = {}
                minimum_per_stratum = min(
                    100, max(1, split_goal // len(REQUIRED_CONFIRM_STRATA))
                )
                for stratum in REQUIRED_CONFIRM_STRATA:
                    rows = sorted(
                        (entry[2] for entry in heaps[(role, f"{split}:{stratum}")]),
                        key=lambda row: (row.rank, row.uid),
                    )
                    for case in rows[:minimum_per_stratum]:
                        chosen_by_uid[case.uid] = case
                all_rows = sorted(
                    (entry[2] for entry in heaps[(role, f"{split}:ALL")]),
                    key=lambda row: (row.rank, row.uid),
                )
                for case in all_rows:
                    if len(chosen_by_uid) >= split_goal:
                        break
                    chosen_by_uid.setdefault(case.uid, case)
                if len(chosen_by_uid) < split_goal:
                    raise RuntimeError(
                        f"{role}:{split}: eligible users {len(chosen_by_uid)} below requested {split_goal}"
                    )
                role_selected.extend(
                    sorted(chosen_by_uid.values(), key=lambda row: (row.rank, row.uid))[:split_goal]
                )
            selected[role] = sorted(role_selected, key=lambda row: row.uid)
            continue
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


def shares(values: list[str]) -> dict[str, float]:
    counts = Counter(values)
    return {key: value / len(values) for key, value in sorted(counts.items())} if values else {}


def psi(left: dict[str, float], right: dict[str, float]) -> float:
    epsilon = 1e-9
    return sum((left.get(key, 0) - right.get(key, 0)) *
               math.log((left.get(key, 0) + epsilon) / (right.get(key, 0) + epsilon))
               for key in set(left) | set(right))


def js_distance(left: dict[str, float], right: dict[str, float]) -> float:
    keys = set(left) | set(right)
    middle = {key: (left.get(key, 0) + right.get(key, 0)) / 2 for key in keys}
    def kl(source: dict[str, float]) -> float:
        return sum(value * math.log2(value / middle[key]) for key, value in source.items() if value)
    return math.sqrt((kl(left) + kl(right)) / 2)


class JsonlWriter:
    def __init__(self, path: Path):
        self.stream = path.open("w", encoding="utf-8", newline="\n")
        self.digest = hashlib.sha256()
        self.count = 0

    def write(self, row: dict) -> None:
        canonical = json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
        if self.count:
            self.digest.update(b"\n")
        self.digest.update(canonical)
        self.stream.write(json.dumps(row, sort_keys=True) + "\n")
        self.count += 1

    def close(self) -> None:
        self.stream.close()

    @property
    def sha256(self) -> str:
        return self.digest.hexdigest()


def build(args: argparse.Namespace) -> dict:
    if args.output.exists():
        raise FileExistsError(f"output already exists: {args.output}")
    goals = {"TRAIN": args.train_users, "VALIDATION": args.validation_users, "FINAL_TEST": args.final_test_users}
    release_years = {}
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
    selected, train_support = select_cases(
        args.movielens_root / "ratings.csv", args.seed, goals, args.positive_threshold, release_years,
        excluded_validation_users,
    )
    args.output.mkdir(parents=True)
    active_roles = tuple(role for role in ROLES if goals[role] > 0)
    catalog = sorted(release_years)
    cutoff = int(args.unknown_probability * (1 << 256))
    global_hash_sample = [movie_id for movie_id in catalog
                          if int.from_bytes(hashlib.sha256(f"{args.seed}:{movie_id}".encode()).digest(), "big") < cutoff]
    role_catalog = {
        role: [movie_id for movie_id in catalog if release_years[movie_id] is not None and
               release_years[movie_id] <= datetime.fromtimestamp(role_window(role)[0] + 1, timezone.utc).year]
        for role in active_roles
    }
    role_catalog_sets = {role: set(values) for role, values in role_catalog.items()}
    role_catalog_digests = {
        role: hashlib.sha256(",".join(map(str, values)).encode()).hexdigest()
        for role, values in role_catalog.items()
    }
    global_unknown = {
        role: [movie_id for movie_id in global_hash_sample if release_years[movie_id] is not None and
               release_years[movie_id] <= datetime.fromtimestamp(role_window(role)[0] + 1, timezone.utc).year]
        for role in active_roles
    }
    if any(len(values) < 20 for values in global_unknown.values()):
        raise RuntimeError("as-of UNKNOWN threshold produced too few candidates")

    episode_writer = JsonlWriter(args.output / "episodes.jsonl")
    candidate_writer = JsonlWriter(args.output / "candidates.jsonl")
    role_profiles = {}
    nested_cell_users: dict[tuple[str, str, str], set[int]] = defaultdict(set)
    low_history_cell_users: dict[tuple[str, str, str], set[int]] = defaultdict(set)
    exact_history_users: dict[tuple[str, str, int], set[int]] = defaultdict(set)
    for role in active_roles:
        cases = selected[role]
        all_targets = [target for case in cases for target in case.targets]
        if not any(target.rating >= args.positive_threshold for target in all_targets) or not any(
            target.rating < args.positive_threshold for target in all_targets
        ):
            raise RuntimeError(f"{role}: both positive and negative observed targets are required")
        role_profiles[role] = {
            "history_bucket": shares([n_bucket(len(case.history)) for case in cases]),
            "target_train_support_bucket": shares([support_bucket(train_support[target.movie_id]) for target in all_targets]),
            "target_rating": shares([f"{target.rating:.1f}" for target in all_targets]),
            "target_relevance": shares(["POSITIVE" if target.rating >= args.positive_threshold else "NEGATIVE"
                                         for target in all_targets]),
            "target_year": shares([str(datetime.fromtimestamp(target.timestamp, timezone.utc).year)
                                    for target in all_targets]),
        }
        for case in cases:
            observed = {row.movie_id: row for row in case.targets}
            excluded = {row.movie_id for row in case.history} | set(observed)
            excluded_history = sorted({row.movie_id for row in case.history} & role_catalog_sets[role])
            eligible_digest = hashlib.sha256(
                f"COMPOSITE_V1:{role_catalog_digests[role]}:excluded=".encode() +
                ",".join(map(str, excluded_history)).encode()
            ).hexdigest()
            unknown = [movie_id for movie_id in global_unknown[role] if movie_id not in excluded]
            pool = list(observed) + unknown
            pool.sort(key=lambda movie_id: (stable_rank(args.seed, "pool", case.uid, movie_id), movie_id))
            pool_digest = hashlib.sha256(",".join(map(str, pool)).encode()).hexdigest()
            for target in case.targets:
                for rank, movie_id in enumerate(pool):
                    rating = observed.get(movie_id)
                    state = ("UNKNOWN_SAMPLED" if rating is None else
                             "POSITIVE_OBSERVED" if rating.rating >= args.positive_threshold else "NEGATIVE_OBSERVED")
                    candidate_writer.write({
                        "role": role, "uid": case.uid, "prediction_at": case.prediction_at,
                        "evaluation_split": case.evaluation_split,
                        "target_movie_id": target.movie_id, "candidate_movie_id": movie_id,
                        "candidate_rank": rank, "label_state": state,
                        "observed_rating": None if rating is None else rating.rating,
                        "sampling_probability": args.unknown_probability if rating is None else None,
                        "importance_weight": 1.0 / args.unknown_probability if rating is None else None,
                        "eligible_population_digest": eligible_digest,
                    })
                # Always include the user's actual available N.  The fixed diagnostic
                # caps alone omit values such as 3, 5, or 41 and therefore cannot
                # demonstrate the service contract's arbitrary 0..N behavior or form
                # a truthful LOW_HISTORY_COHORT from the full supported history.
                requested_values = history_variant_sizes(len(case.history))
                for requested_n in requested_values:
                    history = case.history[:requested_n] if requested_n else []
                    bucket = n_bucket(requested_n)
                    nested_cell_users[(role, case.evaluation_split, bucket)].add(case.uid)
                    if requested_n == len(case.history):
                        low_history_cell_users[(role, case.evaluation_split, bucket)].add(case.uid)
                        exact_history_users[(role, case.evaluation_split, requested_n)].add(case.uid)
                    episode_writer.write({
                        "role": role, "uid": case.uid, "prediction_at": case.prediction_at,
                        "evaluation_split": case.evaluation_split,
                        "catalog_snapshot_at": case.prediction_at,
                        "target_movie_id": target.movie_id, "target_rating": target.rating,
                        "target_event_id": target.event_id, "target_event_at": target.timestamp,
                        "target_eligible": target.movie_id in role_catalog_sets[role],
                        "eligible_population_digest": eligible_digest,
                        "eligible_population_digest_algorithm": "CATALOG_DIGEST_PLUS_EXCLUDED_HISTORY_COMPOSITE_V1",
                        "eligible_population_count": len(role_catalog[role]) - len(excluded_history),
                        "excluded_history_count": len(excluded_history),
                        "unknown_sample_count": len(unknown),
                        "request_target_count": len(case.targets),
                        "total_history_count": len(case.history), "supported_history_count": len(history),
                        "is_full_history": requested_n == len(case.history),
                        "n": requested_n, "n_bucket": bucket, "candidate_digest": pool_digest,
                        "history": [{"event_id": row.event_id, "movie_id": row.movie_id,
                                     "rating": row.rating, "event_at": row.timestamp} for row in history],
                    })
    episode_writer.close()
    candidate_writer.close()
    comparisons = {}
    shifted = False
    for other in (role for role in active_roles if role != "TRAIN"):
        comparisons[f"TRAIN_vs_{other}"] = {}
        for axis, train_values in role_profiles["TRAIN"].items():
            other_values = role_profiles[other][axis]
            values = {
                "psi": psi(train_values, other_values),
                "jensen_shannon_distance_base2": js_distance(train_values, other_values),
                "max_percentage_point_difference": max(
                    (abs(train_values.get(key, 0) - other_values.get(key, 0))
                     for key in set(train_values) | set(other_values)), default=0),
            }
            comparisons[f"TRAIN_vs_{other}"][axis] = values
            shifted |= values["psi"] >= 0.1 or values["jensen_shannon_distance_base2"] >= 0.1 or values[
                "max_percentage_point_difference"] >= 0.05
    nested_cell_counts = {key: len(users) for key, users in nested_cell_users.items()}
    low_history_cell_counts = {key: len(users) for key, users in low_history_cell_users.items()}
    exact_history_counts = {key: len(users) for key, users in exact_history_users.items()}
    insufficient = [
        {"role": role, "evaluation_split": split, "n_bucket": bucket, "users": count}
        for (role, split, bucket), count in sorted(low_history_cell_counts.items())
        if role == "VALIDATION" and split == "CONFIRM" and count < 100
    ]
    report = {
        "schema_version": 2,
        "status": "INSUFFICIENT_SAMPLE" if insufficient else "DISTRIBUTION_SHIFT" if shifted else "PASS",
        "thresholds": {"psi": 0.1, "jensen_shannon_distance_base2": 0.1,
                       "max_percentage_point_difference": 0.05, "minimum_users_per_required_cell": 100},
        "role_profiles": role_profiles, "comparisons": comparisons,
        "nested_history_role_n_cell_counts": {
            f"{role}:{split}:{bucket}": count
            for (role, split, bucket), count in sorted(nested_cell_counts.items())
        },
        "low_history_role_n_cell_counts": {
            f"{role}:{split}:{bucket}": count
            for (role, split, bucket), count in sorted(low_history_cell_counts.items())
        },
        "exact_history_role_n_counts": {
            f"{role}:{split}:{n}": count
            for (role, split, n), count in sorted(exact_history_counts.items())
        },
        "low_history_sample_status": "INSUFFICIENT_SAMPLE" if insufficient else "PASS",
        "insufficient_low_history_cells": insufficient,
    }
    (args.output / "split-distribution-report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    user_digests = {
        role: hashlib.sha256(",".join(str(case.uid) for case in selected[role]).encode()).hexdigest()
        for role in ROLES
    }
    if selected["FINAL_TEST"]:
        final_test_seal_id = hashlib.sha256(json.dumps({
            "role": "FINAL_TEST", "user_digest": user_digests["FINAL_TEST"],
            "window": role_window("FINAL_TEST"), "episode_digest": episode_writer.sha256,
            "candidate_digest": candidate_writer.sha256,
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    else:
        if not args.final_test_seal_id:
            raise RuntimeError("--final-test-seal-id is required when FINAL_TEST users are omitted")
        final_test_seal_id = args.final_test_seal_id
    manifest = {
        "schema_version": 2,
        "status": "PASS",
        "policy_evidence_status": "INSUFFICIENT_SAMPLE" if insufficient else "PASS",
        "created_at": datetime.now(timezone.utc).isoformat(), "seed": args.seed,
        "cutoffs": {role: {"target_start_exclusive": role_window(role)[0],
                            "target_end_inclusive": role_window(role)[1],
                            "feature_cutoff_inclusive": role_window(role)[0]} for role in active_roles},
        "positive_threshold": args.positive_threshold,
        "input": {"ratings_sha256": file_sha256(args.movielens_root / "ratings.csv"),
                  "movies_sha256": file_sha256(args.movielens_root / "movies.csv")},
        "users": {role: len(selected[role]) for role in ROLES},
        "user_partition": {"algorithm": "SHA256_BUCKET_AND_HISTORY_STRATIFIED_TOP_RANK_V1",
                           "seed": args.seed, "role_user_digests": user_digests},
        "targets": {role: sum(len(case.targets) for case in selected[role]) for role in ROLES},
        "episodes": {"rows": episode_writer.count, "sha256": episode_writer.sha256},
        "candidates": {"rows": candidate_writer.count, "sha256": candidate_writer.sha256},
        "distribution_report": {"status": report["status"],
                                "sha256": file_sha256(args.output / "split-distribution-report.json")},
        "target_policy": "UP_TO_4_LABEL_BLIND_SHA256_SELECTED_OBSERVED_TARGETS_PER_USER_IN_NON_OVERLAPPING_ROLE_WINDOW",
        "catalog_as_of_policy": "MOVIELENS_TITLE_RELEASE_YEAR_LE_PREDICTION_YEAR; MISSING_YEAR_EXCLUDED",
        "catalog": {role: {"movies": len(role_catalog[role]), "digest": role_catalog_digests[role]}
                    for role in active_roles},
        "unknown_sampling": {"algorithm": "SHA256_THRESHOLD_V1", "seed": args.seed,
                             "probability": args.unknown_probability,
                             "importance_weight": 1.0 / args.unknown_probability,
                             "global_selected_movies_by_role": {role: len(values) for role, values in global_unknown.items()}},
        "selection_input_roles": ["TRAIN", "VALIDATION:SELECTION"],
        "confirmation_input_roles": ["VALIDATION:CONFIRM"],
        "excluded_previously_seen_validation_users": len(excluded_validation_users),
        "final_test_seal_id": final_test_seal_id,
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
    parser.add_argument("--train-users", type=int, default=200)
    parser.add_argument("--validation-users", type=int, default=200)
    parser.add_argument("--final-test-users", type=int, default=200)
    parser.add_argument("--final-test-seal-id")
    parser.add_argument("--exclude-validation-users-parquet", type=Path)
    parser.add_argument("--positive-threshold", type=float, default=4.0)
    parser.add_argument("--unknown-probability", type=float, default=0.0012)
    print(json.dumps(build(parser.parse_args()), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
