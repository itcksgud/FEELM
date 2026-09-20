"""Build full-eligible-population prefix-K input shared by GBT and FM."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = (
    "experiments/fm-v6/DESIGN.md",
    "experiments/fm-v6/jira-design-correction.json",
    "scripts/fm_v6_build_prefix_input.py",
    "scripts/test_fm_v6_prefix_input.py",
)

K_VALUES = (0, 1, 2, 5, 10, 20)
JUDGMENT_START = 20
JUDGMENT_COUNT = 10
UNKNOWN_COUNT = 40


def file_pin(path: Path) -> dict[str, int | str]:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def stable(seed: int, *parts: object) -> int:
    return int.from_bytes(hashlib.sha256(":".join(map(str, (seed, *parts))).encode()).digest(), "big")


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def role_for(seed: int, uid: int) -> str:
    bucket = stable(seed, "role", uid) % 100
    return "TRAIN" if bucket < 70 else "VALIDATION" if bucket < 85 else "FINAL_TEST"


def n_bucket(k: int) -> str:
    if k <= 2:
        return str(k)
    if k <= 4:
        return "3-4"
    if k <= 9:
        return "5-9"
    if k <= 19:
        return "10-19"
    return "20-29"


class LogicalWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.stream = path.open("w", encoding="utf-8", newline="\n")
        self.digest = hashlib.sha256()
        self.rows = 0
        self.roles: Counter[str] = Counter()

    def write(self, row: dict) -> None:
        encoded = json.dumps(row, sort_keys=True, separators=(",", ":"))
        if self.rows:
            self.digest.update(b"\n")
        self.digest.update(encoded.encode())
        self.stream.write(encoded + "\n")
        self.rows += 1
        self.roles[str(row["role"])] += 1

    def close(self) -> dict:
        self.stream.close()
        return {"rows": self.rows, "logical_sha256": self.digest.hexdigest(),
                "roles": dict(sorted(self.roles.items())), **file_pin(self.path)}


def load_movies(path: Path) -> tuple[dict[int, dict], list[int]]:
    movies = {}
    with path.open("r", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            movie_id = int(row["movieId"])
            movies[movie_id] = {"movie_id": movie_id, "title": row["title"], "genres": row["genres"]}
    return movies, sorted(movies)


def build(args: argparse.Namespace) -> dict:
    if not args.output_root.is_dir() or any(args.output_root.iterdir()):
        raise FileExistsError("prefix input output must be an existing empty directory")
    input_dir, catalog_dir, metadata_dir = (args.output_root / name for name in ("input", "catalog", "metadata"))
    for path in (input_dir, catalog_dir, metadata_dir):
        path.mkdir()
    ratings_pin, movies_pin = file_pin(args.ratings_path), file_pin(args.movies_path)
    movies, movie_ids = load_movies(args.movies_path)
    global_unknown_order = sorted(movie_ids, key=lambda movie_id: (stable(args.seed, "global-unknown", movie_id),
                                                                   movie_id))[:2000]
    episode_writer = LogicalWriter(input_dir / "episodes.train-validation.jsonl")
    candidate_writer = LogicalWriter(input_dir / "candidates.train-validation.jsonl")
    role_users: dict[str, list[int]] = defaultdict(list)
    role_episode_cells: Counter[tuple[str, int]] = Counter()
    judgment_states: Counter[str] = Counter()
    eligible_users = 0
    source_rows = 0

    def process(uid: int | None, events: list[tuple[int, int, float, int]]) -> None:
        nonlocal eligible_users
        if uid is None or len(events) < JUDGMENT_START + JUDGMENT_COUNT:
            return
        eligible_users += 1
        events.sort(key=lambda item: (item[0], item[3], item[1]))
        role = role_for(args.seed, uid)
        role_users[role].append(uid)
        # FINAL_TEST membership is sealed here; no FINAL_TEST episode, candidate, or label is written.
        if role == "FINAL_TEST":
            return
        judgments = events[JUDGMENT_START:JUDGMENT_START + JUDGMENT_COUNT]
        target = judgments[0]
        prediction_at = int(target[0])
        target_movie_id, target_rating = int(target[1]), float(target[2])
        # Exclude only information available in the constructed evaluation episode:
        # exposed prefix plus the ten judged items. Ratings after position 29 remain
        # unknown as-of and must not influence candidate sampling.
        known_as_of = {int(item[1]) for item in events[:JUDGMENT_START + JUDGMENT_COUNT]}
        unknown = [movie_id for movie_id in global_unknown_order if movie_id not in known_as_of][:UNKNOWN_COUNT]
        if len(unknown) != UNKNOWN_COUNT:
            raise RuntimeError(f"global UNKNOWN shortlist exhausted for user {uid}")
        pool = []
        for position, event in enumerate(judgments, JUDGMENT_START):
            rating = float(event[2])
            pool.append({"movie_id": int(event[1]), "label_state":
                         "POSITIVE_OBSERVED" if rating >= 4.0 else "NEGATIVE_OBSERVED",
                         "observed_rating": rating, "judgment_position": position})
        pool.extend({"movie_id": movie_id, "label_state": "UNKNOWN_SAMPLED",
                     "observed_rating": None, "judgment_position": None} for movie_id in unknown)
        pool.sort(key=lambda item: (stable(args.seed, "candidate", uid, item["movie_id"]), item["movie_id"]))
        digest = hashlib.sha256(",".join(str(item["movie_id"]) for item in pool).encode()).hexdigest()
        if role == "VALIDATION":
            for rank, item in enumerate(pool):
                state = str(item["label_state"])
                judgment_states[state] += 1
                candidate_writer.write({
                    "role": role, "uid": uid, "target_movie_id": target_movie_id,
                    "prediction_at": prediction_at, "prediction_sequence_position": JUDGMENT_START,
                    "candidate_movie_id": int(item["movie_id"]), "candidate_rank": rank,
                    "candidate_digest": digest, "label_state": state,
                    "observed_rating": item["observed_rating"],
                    "judgment_position": item["judgment_position"],
                    "sampling_algorithm": "GLOBAL_HASH_ORDER_FILTER_PREFIX_AND_JUDGMENTS_V1",
                    "sampling_probability": None, "importance_weight": None,
                })
        for k in K_VALUES:
            history = [{"movie_id": int(event[1]), "rating": float(event[2]),
                        "event_at": int(event[0]), "sequence_position": position}
                       for position, event in enumerate(events[:k])]
            supported = sum(1 for event in history if event["movie_id"] in movies)
            episode_writer.write({
                "role": role, "uid": uid, "target_movie_id": target_movie_id,
                "target_rating": target_rating, "prediction_at": prediction_at,
                "prediction_sequence_position": JUDGMENT_START, "n": k, "n_bucket": n_bucket(k),
                "history": history, "total_history_count": k, "supported_history_count": supported,
                "is_full_history": k == JUDGMENT_START, "candidate_digest": digest,
                "judgment_count": JUDGMENT_COUNT,
                "prefix_order": "TIMESTAMP_SOURCE_ROW_MOVIE_ID_V1",
            })
            role_episode_cells[(role, k)] += 1

    current_uid: int | None = None
    current: list[tuple[int, int, float, int]] = []
    previous_uid = 0
    with args.ratings_path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ["userId", "movieId", "rating", "timestamp"]:
            raise RuntimeError("unexpected MovieLens ratings header")
        for source_row, row in enumerate(reader, 1):
            uid = int(row["userId"])
            if uid < previous_uid:
                raise RuntimeError("ratings file is not grouped by userId")
            previous_uid = uid
            if current_uid is not None and uid != current_uid:
                process(current_uid, current)
                current = []
            current_uid = uid
            current.append((int(row["timestamp"]), int(row["movieId"]), float(row["rating"]), source_row))
            source_rows += 1
    process(current_uid, current)
    episode_meta, candidate_meta = episode_writer.close(), candidate_writer.close()

    if eligible_users != int(args.expected_eligible_users):
        raise RuntimeError(f"eligible population mismatch: {eligible_users}")
    role_digests = {role: hashlib.sha256(",".join(map(str, users)).encode()).hexdigest()
                    for role, users in sorted(role_users.items())}
    for role in ("TRAIN", "VALIDATION"):
        catalog_path = catalog_dir / f"{role}.catalog.jsonl"
        with catalog_path.open("w", encoding="utf-8", newline="\n") as output:
            for movie_id in movie_ids:
                output.write(json.dumps(movies[movie_id], sort_keys=True, separators=(",", ":")) + "\n")
    catalogs = {role: {"movies": len(movie_ids), "content": file_pin(catalog_dir / f"{role}.catalog.jsonl")}
                for role in ("TRAIN", "VALIDATION")}
    catalog_snapshot = {"schema_version": 1, "status": "PASS", "roles": catalogs,
                        "source_movies": movies_pin, "policy": "STATIC_MOVIELENS_TITLE_GENRE_SNAPSHOT"}
    (catalog_dir / "catalog-snapshots.json").write_text(
        json.dumps(catalog_snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    final_seal = hashlib.sha256(json.dumps({
        "role": "FINAL_TEST", "users": role_users["FINAL_TEST"], "ratings_sha256": ratings_pin["sha256"],
        "seed": args.seed, "k": K_VALUES, "judgment_positions": [JUDGMENT_START, JUDGMENT_START + JUDGMENT_COUNT],
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    seal = {"schema_version": 1, "content": "NOT_INCLUDED", "seal_id": final_seal,
            "users": len(role_users["FINAL_TEST"]), "labels_exported": False, "rows_exported": False}
    (metadata_dir / "FINAL_TEST-SEAL.json").write_text(json.dumps(seal, indent=2) + "\n", encoding="utf-8")
    distribution = {
        "schema_version": 1, "status": "PASS",
        "role_k_user_counts": {f"{role}:K{k}": count
                               for (role, k), count in sorted(role_episode_cells.items())},
        "minimum_users_per_required_cell": min(role_episode_cells.values()),
        "design_note": "Exact prefix cells; no fixed-calendar actual-K intersection",
    }
    (input_dir / "split-distribution-report.train-validation.json").write_text(
        json.dumps(distribution, indent=2) + "\n", encoding="utf-8"
    )
    manifest = {
        "schema_version": 6, "status": "PASS", "created_at": datetime.now(timezone.utc).isoformat(),
        "experiment": "FULL_ELIGIBLE_PREFIX_K_COMMON_INPUT_V1", "seed": args.seed,
        "source": {"ratings": ratings_pin, "movies": movies_pin, "ratings_rows": source_rows},
        "source_revision": args.source_revision,
        "source_bundle": {
            "algorithm": "ORDERED_FILE_PINS_V1",
            "files": {name: file_pin(ROOT / name) for name in SOURCE_FILES},
        },
        "population": {"criterion": "AT_LEAST_30_RATINGS", "eligible_users": eligible_users,
                       "users": {role: len(users) for role, users in sorted(role_users.items())},
                       "role_user_digests": role_digests},
        "prefix": {"k_values": K_VALUES, "judgment_start": JUDGMENT_START,
                   "judgment_count": JUDGMENT_COUNT, "same_judgments_across_k": True,
                   "order": "TIMESTAMP_SOURCE_ROW_MOVIE_ID_V1"},
        "unknown_sampling": {"algorithm": "GLOBAL_HASH_ORDER_FILTER_PREFIX_AND_JUDGMENTS_V1",
                             "count_per_user": UNKNOWN_COUNT, "population_estimate": False},
        "episodes": episode_meta, "candidates": candidate_meta,
        "candidate_judgments": dict(sorted(judgment_states.items())), "catalogs": catalogs,
        "distribution": file_pin(input_dir / "split-distribution-report.train-validation.json"),
        "final_test_seal": seal, "final_test_opened_for_modeling": False,
    }
    manifest["source_bundle"]["digest"] = digest(manifest["source_bundle"]["files"])
    (input_dir / "manifest.train-validation.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ratings-path", type=Path, required=True)
    parser.add_argument("--movies-path", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=623)
    parser.add_argument("--expected-eligible-users", type=int, default=166757)
    parser.add_argument("--source-revision", required=True)
    build(parser.parse_args())


if __name__ == "__main__":
    main()
