"""Verify the frozen 621 input and prepare leakage-bounded GBT Parquet rows."""

from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from gbt_zero_n_features import build_features, feature_names, parse_movie, profile_names


ROOT = Path(__file__).resolve().parents[1]
TRAIN_FEATURE_CUTOFF_INCLUSIVE = 1483228799
POPULARITY_PRIOR_COUNT = 20.0
RUNTIME_SOURCE_FILES = (
    "experiments/gbt-zero-n/config.json",
    "experiments/gbt-zero-n/config-v2.json",
    "experiments/gbt-zero-n/config-v3.json",
    "experiments/gbt-zero-n/config-v4-prefix.json",
    "experiments/gbt-zero-n/config-v4-prefix-seed1622.json",
    "experiments/gbt-zero-n/config-v4-prefix-seed2622.json",
    "experiments/gbt-zero-n/policy-v5-robust-blend.json",
    "scripts/gbt_zero_n_build_input.py",
    "scripts/gbt_zero_n_prefix_build_input.py",
    "scripts/gbt_zero_n_blend_evaluate.py",
    "scripts/gbt_zero_n_evaluate.py",
    "scripts/gbt_zero_n_features.py",
    "scripts/gbt_zero_n_prepare.py",
    "scripts/gbt_zero_n_policy_evaluate.py",
    "scripts/gbt_zero_n_run.py",
    "scripts/gbt_zero_n_worker.py",
)


class TemporalPopularityIndex:
    """TRAIN-user-only popularity observed strictly before each prediction time."""

    def __init__(
        self,
        movie_events: dict[int, list[tuple[int, float]]],
        global_events: list[tuple[int, float]],
        train_users: int,
        prior_count: float,
    ) -> None:
        self.prior_count = prior_count
        self.train_users = train_users
        self.movie_timestamps: dict[int, list[int]] = {}
        self.movie_prefix_sums: dict[int, list[float]] = {}
        for movie_id, events in movie_events.items():
            events.sort()
            timestamps = [timestamp for timestamp, _ in events]
            prefix = [0.0]
            for _, rating in events:
                prefix.append(prefix[-1] + rating)
            self.movie_timestamps[movie_id] = timestamps
            self.movie_prefix_sums[movie_id] = prefix
        global_events.sort()
        self.global_timestamps = [timestamp for timestamp, _ in global_events]
        self.global_prefix_sums = [0.0]
        for _, rating in global_events:
            self.global_prefix_sums.append(self.global_prefix_sums[-1] + rating)

    def score(self, movie_id: int, prediction_at: int) -> dict[str, float]:
        global_count = bisect.bisect_left(self.global_timestamps, prediction_at)
        global_sum = self.global_prefix_sums[global_count]
        global_mean = global_sum / global_count if global_count else 3.5
        timestamps = self.movie_timestamps.get(movie_id, [])
        count = bisect.bisect_left(timestamps, prediction_at)
        prefix = self.movie_prefix_sums.get(movie_id, [0.0])
        rating_sum = prefix[count]
        bayes = (rating_sum + self.prior_count * global_mean) / (count + self.prior_count)
        return {
            "log_support": math.log1p(count) / math.log1p(max(global_count, 1)),
            "bayesian_mean_scaled": bayes / 5.0,
            "count_score": math.log1p(count),
            "bayes_score": bayes,
        }

    def metadata(self) -> dict:
        return {
            "mode": "PER_EPISODE_AS_OF",
            "source": "SELECTED_TRAIN_USERS_ONLY",
            "strictly_before_prediction_at": True,
            "train_users": self.train_users,
            "ratings": len(self.global_timestamps),
            "movies": len(self.movie_timestamps),
            "prior_count": self.prior_count,
        }


def load_temporal_popularity(path: Path, train_user_ids: set[int]) -> TemporalPopularityIndex:
    if not train_user_ids:
        raise RuntimeError("temporal popularity requires TRAIN users")
    user_values = pa.array(sorted(train_user_ids), type=pa.int64())
    movie_events: dict[int, list[tuple[int, float]]] = defaultdict(list)
    global_events: list[tuple[int, float]] = []
    reader = pacsv.open_csv(
        path,
        read_options=pacsv.ReadOptions(block_size=64 * 1024 * 1024),
        convert_options=pacsv.ConvertOptions(column_types={
            "userId": pa.int64(),
            "movieId": pa.int64(),
            "rating": pa.float64(),
            "timestamp": pa.int64(),
        }),
    )
    for batch in reader:
        selected = batch.filter(pc.is_in(batch.column("userId"), value_set=user_values))
        for movie_id, rating, timestamp in zip(
            selected.column("movieId").to_pylist(),
            selected.column("rating").to_pylist(),
            selected.column("timestamp").to_pylist(),
        ):
            event = (int(timestamp), float(rating))
            movie_events[int(movie_id)].append(event)
            global_events.append(event)
    if not global_events:
        raise RuntimeError("TRAIN-only temporal popularity source has no ratings")
    return TemporalPopularityIndex(movie_events, global_events, len(train_user_ids), POPULARITY_PRIOR_COUNT)


def file_pin(path: Path) -> dict[str, int | str]:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def canonical_rows(path: Path, keep_roles: set[str]) -> tuple[list[dict], str, int]:
    rows: list[dict] = []
    digest = hashlib.sha256()
    count = 0
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            encoded = json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
            if count:
                digest.update(b"\n")
            digest.update(encoded)
            count += 1
            if row["role"] in keep_roles:
                rows.append(row)
    return rows, digest.hexdigest(), count


class BatchWriter:
    def __init__(self, path: Path, batch_size: int = 20_000):
        self.path = path
        self.batch_size = batch_size
        self.rows: list[dict] = []
        self.writer: pq.ParquetWriter | None = None
        self.count = 0

    def append(self, row: dict) -> None:
        self.rows.append(row)
        if len(self.rows) >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        if not self.rows:
            return
        table = pa.Table.from_pylist(self.rows)
        if self.writer is None:
            self.writer = pq.ParquetWriter(self.path, table.schema, compression="zstd")
        self.writer.write_table(table)
        self.count += len(self.rows)
        self.rows.clear()

    def close(self) -> None:
        self.flush()
        if self.writer is None:
            raise RuntimeError(f"no rows written: {self.path}")
        self.writer.close()


def load_movies(path: Path) -> dict[int, object]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return {
            int(row["movieId"]): parse_movie(int(row["movieId"]), row["title"], row["genres"])
            for row in csv.DictReader(stream)
        }


def load_asof_popularity(path: Path) -> tuple[dict[int, dict[str, float]], dict]:
    counts: Counter[int] = Counter()
    sums: dict[int, float] = defaultdict(float)
    total_count = 0
    total_sum = 0.0
    with path.open("r", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            if int(row["timestamp"]) > TRAIN_FEATURE_CUTOFF_INCLUSIVE:
                continue
            movie_id = int(row["movieId"])
            rating = float(row["rating"])
            counts[movie_id] += 1
            sums[movie_id] += rating
            total_count += 1
            total_sum += rating
    if total_count == 0:
        raise RuntimeError("as-of popularity source has no ratings")
    global_mean = total_sum / total_count
    max_count = max(counts.values())
    scale = math.log1p(max_count)
    values = {
        movie_id: {
            "log_support": math.log1p(count) / scale,
            "bayesian_mean_scaled": (
                (sums[movie_id] + POPULARITY_PRIOR_COUNT * global_mean)
                / (count + POPULARITY_PRIOR_COUNT)
                / 5.0
            ),
        }
        for movie_id, count in counts.items()
    }
    return values, {
        "cutoff_inclusive": TRAIN_FEATURE_CUTOFF_INCLUSIVE,
        "prior_count": POPULARITY_PRIOR_COUNT,
        "ratings": total_count,
        "movies": len(values),
        "global_mean": global_mean,
        "max_support": max_count,
        "source": "ALL_MOVIELENS_RATINGS_STRICTLY_BEFORE_TRAIN_TARGET_WINDOW",
    }


def schema_document() -> dict:
    names = feature_names()
    profiles = {name: profile_names(name) for name in (
        "movie_only", "history_aggregate", "response_relation", "signed_genre_affinity",
        "popularity_signed_affinity",
    )}
    payload = {
        "schema_version": 2,
        "model_type": "GBT_REGRESSOR",
        "feature_profile_version": "gbt-zero-n-v4-per-episode-train-asof-popularity-prefix-k",
        "ordered_names": names,
        "profiles": profiles,
        "dtype": "float64",
        "families": {
            "shared.candidate": {
                "source": "MovieLens movies.csv at role catalog snapshot",
                "learned": False,
                "missing": "year_missing distinguishes an unknown year from scaled value 0",
                "oov": "candidate is rejected when absent from the frozen catalog",
                "transform": "genre multi-hot; (year-1900)/150",
            },
            "shared.history_response": {
                "source": "strictly pre-prediction MovieLens observed rating events",
                "learned": False,
                "missing": "present=0 distinguishes empty history; aggregate values are then 0",
                "oov": "unsupported history rows are counted separately and not substituted",
                "transform": "bounded ratios and declared log/linear scaling",
            },
            "gbt.model_input": {
                "source": "dense deterministic transforms of candidate and history response",
                "learned": False,
                "missing": "explicit shared missing indicators; relation values are 0 when unavailable",
                "oov": "no learned vocabulary",
                "transform": "genre count scaling and candidate-history relation aggregates",
            },
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["profile_digest"] = hashlib.sha256(encoded).hexdigest()
    payload["ordered_names_sha256"] = hashlib.sha256("\n".join(names).encode()).hexdigest()
    return payload


def source_bundle() -> dict:
    files = {name: file_pin(ROOT / name) for name in RUNTIME_SOURCE_FILES}
    digest = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"algorithm": "ORDERED_FILE_PINS_V1", "digest": digest, "files": files}


def prepare(args: argparse.Namespace) -> dict:
    if args.output_root.exists():
        raise FileExistsError(f"output already exists: {args.output_root}")
    manifest = json.loads((args.input_root / "manifest.json").read_text(encoding="utf-8"))
    if manifest["status"] != "PASS":
        raise RuntimeError("621 input manifest is not PASS")
    ratings_pin = file_pin(args.movielens_root / "ratings.csv")
    movies_pin = file_pin(args.movielens_root / "movies.csv")
    if ratings_pin["sha256"] != manifest["input"]["ratings_sha256"]:
        raise RuntimeError("ratings.csv digest mismatch")
    if movies_pin["sha256"] != manifest["input"]["movies_sha256"]:
        raise RuntimeError("movies.csv digest mismatch")

    episodes, episode_digest, episode_count = canonical_rows(
        args.input_root / "episodes.jsonl", {"TRAIN", "VALIDATION"}
    )
    if episode_count != manifest["episodes"]["rows"] or episode_digest != manifest["episodes"]["sha256"]:
        raise RuntimeError("episode count or digest mismatch")
    candidates, candidate_digest, candidate_count = canonical_rows(
        args.input_root / "candidates.jsonl", {"VALIDATION"}
    )
    if candidate_count != manifest["candidates"]["rows"] or candidate_digest != manifest["candidates"]["sha256"]:
        raise RuntimeError("candidate count or digest mismatch")
    report_pin = file_pin(args.input_root / "split-distribution-report.json")
    if report_pin["sha256"] != manifest["distribution_report"]["sha256"]:
        raise RuntimeError("distribution report digest mismatch")

    candidate_groups: dict[tuple[str, int, int], list[dict]] = defaultdict(list)
    for row in candidates:
        if row["label_state"] not in {"POSITIVE_OBSERVED", "NEGATIVE_OBSERVED", "UNKNOWN_SAMPLED"}:
            raise RuntimeError("unexpected candidate label state")
        candidate_groups[(row["role"], row["uid"], row["target_movie_id"])].append(row)
    for rows in candidate_groups.values():
        rows.sort(key=lambda row: row["candidate_rank"])
        if [row["candidate_rank"] for row in rows] != list(range(len(rows))):
            raise RuntimeError("candidate ranks are not contiguous")

    variants = Counter((row["role"], row["uid"], row["target_movie_id"], row["prediction_at"]) for row in episodes)
    observed_labels = {float(row["target_rating"]) for row in episodes if row["role"] == "TRAIN"}
    if not any(value < manifest["positive_threshold"] for value in observed_labels) or not any(
        value >= manifest["positive_threshold"] for value in observed_labels
    ):
        raise RuntimeError("TRAIN targets must include both positive and negative observed ratings")
    movies = load_movies(args.movielens_root / "movies.csv")
    controlled_prefix = manifest.get("experiment_design") == "CONTROLLED_RECENT_PREFIX_AT_PER_USER_ANCHOR_V1"
    temporal_popularity: TemporalPopularityIndex | None = None
    fixed_popularity: dict[int, dict[str, float]] = {}
    if controlled_prefix:
        train_user_ids = {int(row["uid"]) for row in episodes if row["role"] == "TRAIN"}
        temporal_popularity = load_temporal_popularity(
            args.movielens_root / "ratings.csv", train_user_ids
        )
        popularity_meta = temporal_popularity.metadata()
    else:
        fixed_popularity, popularity_meta = load_asof_popularity(
            args.movielens_root / "ratings.csv"
        )

    def popularity_for(movie_id: int, prediction_at: int) -> dict[str, float]:
        if temporal_popularity is not None:
            return temporal_popularity.score(movie_id, prediction_at)
        value = fixed_popularity.get(movie_id, {})
        return {
            **value,
            "count_score": float(value.get("log_support", 0.0)),
            "bayes_score": float(value.get("bayesian_mean_scaled", 0.0)) * 5.0,
        }
    args.output_root.mkdir(parents=True)
    train_writer = BatchWriter(args.output_root / "train-targets.parquet")
    validation_writer = BatchWriter(args.output_root / "validation-targets.parquet")
    candidate_writer = BatchWriter(args.output_root / "validation-candidates.parquet")
    n_counts: Counter[str] = Counter()
    missing_movies: Counter[str] = Counter()

    for episode in episodes:
        role = episode["role"]
        key = (role, episode["uid"], episode["target_movie_id"], episode["prediction_at"])
        target = movies.get(episode["target_movie_id"])
        if target is None:
            missing_movies[f"{role}:target"] += 1
            raise RuntimeError(f"target movie missing from movies.csv: {episode['target_movie_id']}")
        episode_id = ":".join(map(str, (*key, episode["n"])))
        target_popularity = popularity_for(
            int(episode["target_movie_id"]), int(episode["prediction_at"])
        )
        base = {
            "episode_id": episode_id,
            "role": role,
            "uid": int(episode["uid"]),
            "evaluation_split": episode.get("evaluation_split", role),
            "target_movie_id": int(episode["target_movie_id"]),
            "candidate_movie_id": int(episode["target_movie_id"]),
            "prediction_at": int(episode["prediction_at"]),
            "n": int(episode["n"]),
            "n_bucket": episode["n_bucket"],
            "total_history_count": int(episode["total_history_count"]),
            "provided_history_count": int(episode.get("provided_history_count", episode["n"])),
            "supported_history_count": int(episode["supported_history_count"]),
            "is_full_history": bool(episode["is_full_history"]),
            "is_controlled_prefix": bool(episode.get("is_controlled_prefix", False)),
            "label": float(episode["target_rating"]),
            "sample_weight": 1.0 / (variants[key] * int(episode.get("request_target_count", 1))),
            "candidate_rank": 0,
            "label_state": "POSITIVE_OBSERVED" if float(episode["target_rating"]) >= manifest["positive_threshold"]
            else "NEGATIVE_OBSERVED",
            "is_target": True,
            "policy.popular_count_score": float(target_popularity["count_score"]),
            "policy.popular_bayes_score": float(target_popularity["bayes_score"]),
        }
        features = build_features(
            target, episode["history"], movies, episode["total_history_count"],
            episode["supported_history_count"],
            target_popularity,
        )
        (train_writer if role == "TRAIN" else validation_writer).append({**base, **features})
        n_counts[f"{role}:{episode['n_bucket']}"] += 1

        if role == "VALIDATION":
            pool = candidate_groups.get((role, episode["uid"], episode["target_movie_id"]))
            if not pool:
                raise RuntimeError(f"validation candidate pool missing: {key}")
            pool_digest = hashlib.sha256(",".join(str(row["candidate_movie_id"]) for row in pool).encode()).hexdigest()
            if pool_digest != episode["candidate_digest"]:
                raise RuntimeError(f"validation candidate digest mismatch: {key}")
            for item in pool:
                movie = movies.get(item["candidate_movie_id"])
                if movie is None:
                    missing_movies["VALIDATION:candidate"] += 1
                    raise RuntimeError(f"candidate movie missing from movies.csv: {item['candidate_movie_id']}")
                candidate_popularity = popularity_for(
                    int(item["candidate_movie_id"]), int(episode["prediction_at"])
                )
                candidate_writer.append({
                    **base,
                    "candidate_movie_id": int(item["candidate_movie_id"]),
                    "candidate_rank": int(item["candidate_rank"]),
                    "label_state": item["label_state"],
                    "is_target": item["candidate_movie_id"] == episode["target_movie_id"],
                    "label": item.get("observed_rating"),
                    "policy.popular_count_score": float(candidate_popularity["count_score"]),
                    "policy.popular_bayes_score": float(candidate_popularity["bayes_score"]),
                    **build_features(
                        movie, episode["history"], movies, episode["total_history_count"],
                        episode["supported_history_count"],
                        candidate_popularity,
                    ),
                })

    train_writer.close()
    validation_writer.close()
    candidate_writer.close()
    schema = schema_document()
    (args.output_root / "feature-schema.json").write_text(
        json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    output = {
        "schema_version": 1,
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_revision": args.source_revision,
        "source_revision_policy": "GIT_HEAD_PLUS_EXACT_SOURCE_BUNDLE_WHEN_WORKTREE_IS_DIRTY",
        "source_bundle": source_bundle(),
        "input_root_id": args.input_root.name,
        "input_manifest": file_pin(args.input_root / "manifest.json"),
        "episodes": {"rows": episode_count, "sha256": episode_digest},
        "candidates": {"rows": candidate_count, "sha256": candidate_digest},
        "distribution_report": {"status": manifest["distribution_report"]["status"], **report_pin},
        "feature_schema": schema,
        "train_asof_popularity": popularity_meta,
        "rows": {
            "train_targets": train_writer.count,
            "validation_targets": validation_writer.count,
            "validation_candidates": candidate_writer.count,
        },
        "n_counts": dict(sorted(n_counts.items())),
        "missing_movies": dict(sorted(missing_movies.items())),
        "label_policy": "OBSERVED_TARGET_RATING_ONLY; UNKNOWN_SAMPLED_NEVER_USED_AS_NEGATIVE",
        "sample_weight_policy": "INVERSE_TARGET_COUNT_AND_VARIANT_COUNT_PER_USER_REQUEST",
        "final_test": "NOT_WRITTEN",
        "files": {},
    }
    for name in ("train-targets.parquet", "validation-targets.parquet", "validation-candidates.parquet", "feature-schema.json"):
        output["files"][name] = file_pin(args.output_root / name)
    (args.output_root / "manifest.json").write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--movielens-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    print(json.dumps(prepare(parser.parse_args()), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
