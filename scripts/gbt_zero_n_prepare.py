"""Verify the frozen 621 input and prepare leakage-bounded GBT Parquet rows."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from gbt_zero_n_features import build_features, feature_names, parse_movie, profile_names


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_SOURCE_FILES = (
    "experiments/gbt-zero-n/config.json",
    "scripts/gbt_zero_n_build_input.py",
    "scripts/gbt_zero_n_evaluate.py",
    "scripts/gbt_zero_n_features.py",
    "scripts/gbt_zero_n_prepare.py",
    "scripts/gbt_zero_n_run.py",
    "scripts/gbt_zero_n_worker.py",
)


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


def schema_document() -> dict:
    names = feature_names()
    profiles = {name: profile_names(name) for name in ("movie_only", "history_aggregate", "response_relation")}
    payload = {
        "schema_version": 2,
        "model_type": "GBT_REGRESSOR",
        "feature_profile_version": "gbt-zero-n-v1",
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
        base = {
            "episode_id": episode_id,
            "role": role,
            "uid": int(episode["uid"]),
            "target_movie_id": int(episode["target_movie_id"]),
            "candidate_movie_id": int(episode["target_movie_id"]),
            "prediction_at": int(episode["prediction_at"]),
            "n": int(episode["n"]),
            "n_bucket": episode["n_bucket"],
            "total_history_count": int(episode["total_history_count"]),
            "supported_history_count": int(episode["supported_history_count"]),
            "is_full_history": bool(episode["is_full_history"]),
            "label": float(episode["target_rating"]),
            "sample_weight": 1.0 / (variants[key] * int(episode.get("request_target_count", 1))),
            "candidate_rank": 0,
            "label_state": "POSITIVE_OBSERVED" if float(episode["target_rating"]) >= manifest["positive_threshold"]
            else "NEGATIVE_OBSERVED",
            "is_target": True,
        }
        features = build_features(
            target, episode["history"], movies, episode["total_history_count"],
            episode["supported_history_count"],
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
                candidate_writer.append({
                    **base,
                    "candidate_movie_id": int(item["candidate_movie_id"]),
                    "candidate_rank": int(item["candidate_rank"]),
                    "label_state": item["label_state"],
                    "is_target": item["candidate_movie_id"] == episode["target_movie_id"],
                    "label": item.get("observed_rating"),
                    **build_features(
                        movie, episode["history"], movies, episode["total_history_count"],
                        episode["supported_history_count"],
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
