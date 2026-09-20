"""Build label-separated rolling recent-N source input for FM-v4."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import gbt_zero_n_build_input as base


ROLES = ("TRAIN", "VALIDATION", "FINAL_TEST")
RECENT_N_VALUES = (1, 2, 4, 7, 15, 25, 40, 50)


@dataclass(frozen=True)
class Event:
    movie_id: int
    timestamp: int
    rating: float | None

    @property
    def order_key(self) -> tuple[int, int]:
        return self.timestamp, self.movie_id


class JsonlWriter:
    """Write stable JSONL while hashing the exact bytes written."""

    def __init__(self, path: Path):
        self.stream = path.open("wb")
        self.digest = hashlib.sha256()
        self.rows = 0

    def write(self, row: dict) -> None:
        encoded = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"
        self.stream.write(encoded)
        self.digest.update(encoded)
        self.rows += 1

    def close(self) -> dict[str, int | str]:
        byte_count = self.stream.tell()
        self.stream.close()
        return {"rows": self.rows, "bytes": byte_count, "sha256": self.digest.hexdigest()}

    def abort(self) -> None:
        if not self.stream.closed:
            self.stream.close()


def file_pin(path: Path) -> dict[str, int | str]:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def user_digest(users: Iterable[int]) -> str:
    return hashlib.sha256(",".join(map(str, sorted(users))).encode("utf-8")).hexdigest()


def serialized_role(line: str) -> str:
    match = re.search(r'"role"\s*:\s*"(TRAIN|VALIDATION|FINAL_TEST)"', line)
    if not match:
        raise RuntimeError("serialized row has no recognized role")
    return match.group(1)


def load_prior_validation_users(prior_input_root: Path, expected_seed: int) -> tuple[set[int], dict]:
    """Load one frozen validation set, skipping FINAL_TEST rows before JSON parsing."""

    manifest_path = prior_input_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if int(manifest.get("seed", -1)) != expected_seed:
        raise RuntimeError(f"prior input seed mismatch: expected {expected_seed}")
    episode_path = prior_input_root / "episodes.jsonl"
    if not episode_path.is_file():
        episode_path = prior_input_root / "validation-episodes.jsonl"
    if not episode_path.is_file():
        raise RuntimeError("prior input has no episode file")

    users: set[int] = set()
    with episode_path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            role = serialized_role(line)
            if role == "FINAL_TEST":
                continue
            if role != "VALIDATION":
                continue
            row = json.loads(line)
            if row.get("role") != "VALIDATION":
                raise RuntimeError("prior serialized role differs after parsing")
            users.add(int(row["uid"]))

    expected_count = int(manifest["users"]["VALIDATION"])
    expected_digest = manifest["user_partition"]["role_user_digests"]["VALIDATION"]
    actual_digest = user_digest(users)
    if len(users) != expected_count or actual_digest != expected_digest:
        raise RuntimeError("prior validation user set differs from its frozen manifest")
    return users, {
        "seed": expected_seed,
        "root_manifest": file_pin(manifest_path),
        "episode_file_name": episode_path.name,
        "episode_file": file_pin(episode_path),
        "users": len(users),
        "user_digest": actual_digest,
    }


def load_release_years(movies_path: Path) -> dict[int, int | None]:
    years: dict[int, int | None] = {}
    with movies_path.open("r", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            match = re.search(r"\((\d{4})\)\s*$", row["title"])
            years[int(row["movieId"])] = int(match.group(1)) if match else None
    return years


def is_eligible_target(event: Event, role: str, release_years: dict[int, int | None]) -> bool:
    start, end = base.role_window(role)
    release_year = release_years.get(event.movie_id)
    target_year = datetime.fromtimestamp(event.timestamp, timezone.utc).year
    return start < event.timestamp <= end and release_year is not None and release_year <= target_year


def choose_targets(seed: int, uid: int, events: list[Event]) -> list[Event]:
    """Choose at most four targets without using their rating values."""

    ranked = sorted(
        events,
        key=lambda row: (
            base.stable_rank(seed, "target", uid, row.timestamp, row.movie_id),
            row.timestamp,
            row.movie_id,
        ),
    )[:4]
    return sorted(ranked, key=lambda row: row.order_key)


def recent_n_values(history_length: int) -> list[int]:
    cap = min(history_length, 50)
    return sorted({0, cap, *(value for value in RECENT_N_VALUES if value <= cap)})


def history_policy_contract() -> dict:
    return {
        "eligibility": "EVENT_(TIMESTAMP,MOVIE_ID)_STRICTLY_LESS_THAN_TARGET_KEY",
        "selection": "MOST_RECENT_N_THEN_RESTORED_TO_CHRONOLOGICAL_ORDER",
        "always_include_zero_history_variant": True,
        "base_n_values": list(RECENT_N_VALUES),
        "include_capped_full_history_variant": True,
        "capped_full_history_maximum": 50,
        "variant_set_formula": "{0} UNION {K IN BASE_N_VALUES WHERE K <= MIN(H,50)} UNION {MIN(H,50)}",
    }


def target_key(role: str, uid: int, target: Event) -> str:
    return f"{role}:{uid}:{target.timestamp}:{target.movie_id}"


def episode_row(role: str, uid: int, target: Event, history: list[Event], n: int) -> dict:
    key = target_key(role, uid, target)
    selected = history[-n:] if n else []
    row = {
        "role": role,
        "uid": uid,
        "target_key": key,
        "episode_id": f"{key}:{n}",
        "prediction_at": target.timestamp,
        "catalog_snapshot_at": target.timestamp,
        "target_movie_id": target.movie_id,
        "target_event_id": f"{target.timestamp}:{target.movie_id}",
        "target_event_at": target.timestamp,
        "n": n,
        "n_bucket": str(n),
        "total_history_count": len(history),
        "supported_history_count": len(selected),
        "history_cap": 50,
        "history_order": "CHRONOLOGICAL_AFTER_MOST_RECENT_N_SELECTION",
        "history": [
            {
                "event_id": f"{event.timestamp}:{event.movie_id}",
                "movie_id": event.movie_id,
                "rating": event.rating,
                "event_at": event.timestamp,
            }
            for event in selected
        ],
    }
    if role == "TRAIN":
        if target.rating is None:
            raise RuntimeError("TRAIN target is missing its rating")
        row["target_rating"] = target.rating
    return row


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("experiment") != "S15P21E106-623-fm-v4-rolling-signed-content-v1":
        raise RuntimeError("FM-v4 config experiment mismatch")
    split = config.get("split", {})
    if int(split.get("seed", -1)) != 4623:
        raise RuntimeError("FM-v4 config seed mismatch")
    if split.get("n_values") != [0, *RECENT_N_VALUES] or int(split.get("maximum_history", -1)) != 50:
        raise RuntimeError("FM-v4 config recent-N contract mismatch")
    if int(split.get("target_limit_per_user", -1)) != 4:
        raise RuntimeError("FM-v4 config target limit mismatch")
    expected_windows = {
        role: {
            "target_start_exclusive": base.role_window(role)[0],
            "target_end_inclusive": base.role_window(role)[1],
        }
        for role in ROLES
    }
    if split.get("windows") != expected_windows:
        raise RuntimeError("FM-v4 config target windows mismatch")
    return config


def assert_expected_pin(path: Path, expected: dict, label: str) -> dict:
    actual = file_pin(path)
    normalized = {"bytes": int(expected["bytes"]), "sha256": str(expected["sha256"])}
    if actual != normalized:
        raise RuntimeError(f"{label} source pin mismatch")
    return actual


def build(args: argparse.Namespace) -> dict:
    if args.output.exists():
        raise FileExistsError(f"output already exists: {args.output}")
    config = load_config(args.config)
    seed = int(config["split"]["seed"])
    source_config = config["source"]
    raw_pins = {
        name: assert_expected_pin(args.movielens_root / f"{name}.csv", source_config[name], name)
        for name in ("ratings", "movies", "tags")
    }

    prior_622, report_622 = load_prior_validation_users(args.prior_seed_622_input_root, 622)
    prior_3623, report_3623 = load_prior_validation_users(args.prior_seed_3623_input_root, 3623)
    expected_prior = source_config["prior_validation"]
    for users, report, key in (
        (prior_622, report_622, "seed_622"),
        (prior_3623, report_3623, "seed_3623"),
    ):
        expected = expected_prior[key]
        if len(users) != int(expected["users"]) or report["user_digest"] != expected["user_digest"]:
            raise RuntimeError(f"{key} prior validation contract mismatch")
    excluded_validation_users = prior_622 | prior_3623
    prior_intersection = prior_622 & prior_3623
    if len(prior_intersection) != int(expected_prior["expected_intersection_users"]):
        raise RuntimeError("prior validation intersection count mismatch")
    if len(excluded_validation_users) != int(expected_prior["expected_union_users"]):
        raise RuntimeError("prior validation union count mismatch")
    release_years = load_release_years(args.movielens_root / "movies.csv")

    args.output.mkdir(parents=True)
    train_writer = JsonlWriter(args.output / "train-episodes.jsonl")
    validation_writer = JsonlWriter(args.output / "validation-episodes.jsonl")
    label_writer = JsonlWriter(args.output / "validation-labels.jsonl")

    raw_eligible_users = {role: set() for role in ROLES}
    materialized_users = {"TRAIN": set(), "VALIDATION": set()}
    materialized_targets = Counter()
    materialized_episodes = Counter()
    raw_eligible_target_events = Counter()
    active_users = Counter()
    active_targets = Counter()
    n0_targets = Counter()
    excluded_eligible_validation_users: set[int] = set()
    seen_target_keys: set[str] = set()
    seen_episode_ids: set[str] = set()

    def finish_user(uid: int | None, events: list[Event]) -> None:
        if uid is None:
            return
        role = base.role_for_user(seed, uid)
        events.sort(key=lambda row: row.order_key)
        available = [row for row in events if is_eligible_target(row, role, release_years)]
        if not available:
            return
        raw_eligible_users[role].add(uid)
        raw_eligible_target_events[role] += len(available)
        if role == "FINAL_TEST":
            return
        if role == "VALIDATION" and uid in excluded_validation_users:
            excluded_eligible_validation_users.add(uid)
            return

        targets = choose_targets(seed, uid, available)
        materialized_users[role].add(uid)
        materialized_targets[role] += len(targets)
        user_is_active = False
        for target in targets:
            key = target_key(role, uid, target)
            if key in seen_target_keys:
                raise RuntimeError(f"target_key collision: {key}")
            seen_target_keys.add(key)
            history = [row for row in events if row.order_key < target.order_key]
            if history:
                active_targets[role] += 1
                user_is_active = True
            else:
                n0_targets[role] += 1
            for n in recent_n_values(len(history)):
                row = episode_row(role, uid, target, history, n)
                if row["episode_id"] in seen_episode_ids:
                    raise RuntimeError(f"episode_id collision: {row['episode_id']}")
                seen_episode_ids.add(row["episode_id"])
                (train_writer if role == "TRAIN" else validation_writer).write(row)
                materialized_episodes[role] += 1
            if role == "VALIDATION":
                if target.rating is None:
                    raise RuntimeError("VALIDATION target is missing its sealed label")
                label_writer.write({"target_key": key, "target_rating": target.rating})
        if user_is_active:
            active_users[role] += 1

    ratings_path = args.movielens_root / "ratings.csv"
    current_uid: int | None = None
    current_events: list[Event] = []
    completed_uid = 0
    with ratings_path.open("r", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            uid = int(row["userId"])
            if current_uid is not None and uid != current_uid:
                finish_user(current_uid, current_events)
                completed_uid = current_uid
                current_events = []
            if uid < completed_uid:
                raise RuntimeError("ratings.csv must be grouped in nondecreasing userId order")
            current_uid = uid
            role = base.role_for_user(seed, uid)
            rating = None if role == "FINAL_TEST" else float(row["rating"])
            current_events.append(Event(int(row["movieId"]), int(row["timestamp"]), rating))
    finish_user(current_uid, current_events)

    file_reports = {
        "train_episodes": train_writer.close(),
        "validation_episodes": validation_writer.close(),
        "validation_labels": label_writer.close(),
    }
    if file_reports["validation_labels"]["rows"] != materialized_targets["VALIDATION"]:
        raise RuntimeError("validation labels are not one-to-one with validation targets")
    if materialized_users["TRAIN"] != raw_eligible_users["TRAIN"]:
        raise RuntimeError("TRAIN population is not census-saturated")
    expected_fresh_validation = raw_eligible_users["VALIDATION"] - excluded_validation_users
    if materialized_users["VALIDATION"] != expected_fresh_validation:
        raise RuntimeError("VALIDATION population is not the full eligible fresh population")
    if materialized_users["VALIDATION"] & excluded_validation_users:
        raise RuntimeError("new validation population overlaps a prior validation population")

    expected_census = config["expected_census"]
    actual_expected_census = {
        "TRAIN": {
            "eligible_users": len(materialized_users["TRAIN"]),
            "active_users": active_users["TRAIN"],
            "active_targets": active_targets["TRAIN"],
            "n0_targets": n0_targets["TRAIN"],
        },
        "VALIDATION": {
            "eligible_users": len(materialized_users["VALIDATION"]),
            "active_users": active_users["VALIDATION"],
            "active_targets": active_targets["VALIDATION"],
            "n0_targets": n0_targets["VALIDATION"],
        },
        "FINAL_TEST": {"eligible_users": len(raw_eligible_users["FINAL_TEST"])},
    }
    if actual_expected_census != expected_census:
        raise RuntimeError(
            f"FM-v4 census differs from config: actual={actual_expected_census}, expected={expected_census}"
        )

    union_digest = user_digest(excluded_validation_users)
    role_user_digests = {
        "TRAIN": user_digest(materialized_users["TRAIN"]),
        "VALIDATION": user_digest(materialized_users["VALIDATION"]),
        "FINAL_TEST": user_digest(raw_eligible_users["FINAL_TEST"]),
    }
    role_user_digests["FRESH_VALIDATION"] = user_digest(materialized_users["VALIDATION"])
    manifest = {
        "schema_version": 1,
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "contract": "FM_V4_LABEL_SEPARATED_ROLLING_RECENT_N_SOURCE_V1",
        "seed": seed,
        "cutoffs": {
            role: {
                "target_start_exclusive": base.role_window(role)[0],
                "target_end_inclusive": base.role_window(role)[1],
            }
            for role in ROLES
        },
        "input": raw_pins,
        "config_contract": {
            "experiment": config["experiment"],
            "status_at_build": config["status"],
            "expected_census": expected_census,
        },
        "files": file_reports,
        "users": {
            "TRAIN": len(materialized_users["TRAIN"]),
            "VALIDATION": len(materialized_users["VALIDATION"]),
            "FINAL_TEST": len(raw_eligible_users["FINAL_TEST"]),
        },
        "targets": dict(sorted(materialized_targets.items())),
        "episodes": dict(sorted(materialized_episodes.items())),
        "user_partition": {
            "algorithm": "SHA256_BUCKET_V1_TRAIN_LT70_VALIDATION_LT85_FINAL_TEST_OTHERWISE",
            "seed": seed,
            "role_user_digests": role_user_digests,
        },
        "eligible_census": {
            "algorithm": "FULL_RATINGS_USER_SCAN_WITH_ACTUAL_TARGET_TIMESTAMP_RELEASE_YEAR_ELIGIBILITY_V1",
            "raw_eligible_users": {role: len(raw_eligible_users[role]) for role in ROLES},
            "raw_eligible_user_digests": {role: user_digest(raw_eligible_users[role]) for role in ROLES},
            "raw_eligible_target_events": dict(sorted(raw_eligible_target_events.items())),
            "excluded_prior_validation_eligible_users": len(excluded_eligible_validation_users),
            "fresh_validation_users": len(materialized_users["VALIDATION"]),
            "full_train_population_saturated": True,
            "full_fresh_validation_population_saturated": True,
            "final_test_mode": "CENSUS_ONLY_NO_EPISODES_NO_LABELS",
        },
        "prior_validation_exclusion": {
            "policy": "EXCLUDE_UNION_OF_SEED_622_AND_SEED_3623_VALIDATION_USERS_FROM_SEED_4623_VALIDATION",
            "sources": {"622": report_622, "3623": report_3623},
            "source_intersection_users": len(prior_intersection),
            "source_intersection_digest": user_digest(prior_intersection),
            "union_users": len(excluded_validation_users),
            "union_digest": union_digest,
            "new_validation_intersection_users": 0,
            "new_validation_user_digest": user_digest(materialized_users["VALIDATION"]),
        },
        "target_policy": "UP_TO_4_LABEL_BLIND_SHA256_SELECTED_TARGETS_PER_USER_USING_TIMESTAMP_AND_MOVIE_ID",
        "target_key_policy": "ROLE:UID:TARGET_TIMESTAMP:TARGET_MOVIE_ID_WITH_EXPLICIT_COLLISION_CHECK",
        "episode_id_policy": "TARGET_KEY:N_WITH_EXPLICIT_COLLISION_CHECK",
        "prediction_at_policy": "ACTUAL_TARGET_EVENT_TIMESTAMP",
        "history_policy": history_policy_contract(),
        "label_boundary": {
            "training_target_rating_in_episode": True,
            "validation_target_rating_in_episode": False,
            "validation_label_fields": ["target_key", "target_rating"],
            "validation_label_rows": file_reports["validation_labels"]["rows"],
            "final_test_materialized": False,
            "final_test_labels_materialized": False,
        },
        "candidate_ranking_files_materialized": False,
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--movielens-root", type=Path, required=True)
    parser.add_argument("--prior-seed-622-input-root", type=Path, required=True)
    parser.add_argument("--prior-seed-3623-input-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    build(parser.parse_args())


if __name__ == "__main__":
    main()
