"""Turn every verified MovieLens rating into an observed temporal target.

The sorted examples are an indexed, lossless representation of each user's
strictly earlier history. It does not duplicate O(history length) arrays for
every target and does not invent negatives for unrated movies.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from build_ml32_tmdb_kobis_source_v1 import source


EVENT_SCHEMA = pa.schema([
    ("uid", pa.int32()), ("movie_id", pa.int32()),
    ("rating", pa.float32()), ("timestamp", pa.int64()),
    ("user_seq", pa.int32()), ("history_end_exclusive", pa.int32()),
    ("rating_signal", pa.int8()), ("split_id", pa.int8()),
])
USER_SCHEMA = pa.schema([
    ("uid", pa.int32()), ("first_event_row", pa.int64()),
    ("event_count", pa.int32()), ("split_id", pa.int8()),
])


def split_id(uid: int) -> int:
    """0=train, 1=validation, 2=test; stable disjoint-user split."""
    bucket = int.from_bytes(hashlib.sha256(f"evidence-v1:user:{uid}".encode()).digest()[:8], "big") % 10
    return 0 if bucket < 8 else 1 if bucket == 8 else 2


def make_examples(ratings: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    required = {"uid", "movie_id", "rating", "timestamp"}
    if not required.issubset(ratings.columns):
        raise ValueError(f"ratings missing {sorted(required - set(ratings.columns))}")
    if ratings[list(required)].isna().any().any():
        raise ValueError("null rating field")
    if ratings.duplicated(["uid", "movie_id"]).any():
        raise ValueError("same user/movie has multiple ratings; revision policy required")
    events = ratings.loc[:, ["uid", "movie_id", "rating", "timestamp"]].sort_values(
        ["uid", "timestamp", "movie_id"], kind="stable").reset_index(drop=True)
    events["user_seq"] = events.groupby("uid", sort=False).cumcount().astype("int32")
    # All targets stamped at the same second see the same, strictly earlier prefix.
    events["history_end_exclusive"] = events.groupby(
        ["uid", "timestamp"], sort=False)["user_seq"].transform("min").astype("int32")
    stars = events.rating.to_numpy()
    events["rating_signal"] = np.where(stars <= 2.5, -1, np.where(stars >= 4, 2, 1)).astype("int8")
    users = events.groupby("uid", sort=False).agg(
        event_count=("movie_id", "size"), first_event_row=("user_seq", "idxmin")
    ).reset_index()
    users["split_id"] = users.uid.map(lambda uid: split_id(int(uid))).astype("int8")
    events["split_id"] = events.uid.map(users.set_index("uid").split_id).astype("int8")
    users = users.loc[:, ["uid", "first_event_row", "event_count", "split_id"]]
    for column in ("uid", "movie_id", "user_seq", "history_end_exclusive"):
        events[column] = events[column].astype("int32")
    users["uid"] = users.uid.astype("int32")
    users["event_count"] = users.event_count.astype("int32")
    if not events.history_end_exclusive.le(events.user_seq).all():
        raise AssertionError("history includes target or later rating")
    return events, users


def build(ratings_path: Path, bridge_path: Path, output_dir: Path) -> dict:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite {output_dir}")
    bridge = pd.read_parquet(bridge_path, columns=["movie_id", "tmdb_id", "kobis_value_valid"])
    if bridge.movie_id.duplicated().any() or bridge.tmdb_id.duplicated().any():
        raise ValueError("bridge must be one-to-one")
    allowed = set(bridge.movie_id.astype(int))
    rating_file = pq.ParquetFile(ratings_path)
    output_dir.mkdir(parents=True, exist_ok=False)
    event_path = output_dir / "temporal_examples.parquet"
    user_path = output_dir / "user_index.parquet"
    example_splits: Counter[int] = Counter()
    user_splits: Counter[int] = Counter()
    signal_counts: Counter[int] = Counter()
    total_examples = total_users = cold_start = same_timestamp = 0
    pending: pd.DataFrame | None = None
    previous_uid = -1

    def append_complete(ratings: pd.DataFrame, event_writer: pq.ParquetWriter,
                        user_writer: pq.ParquetWriter) -> None:
        nonlocal total_examples, total_users, cold_start, same_timestamp
        if ratings.empty:
            return
        if not ratings.movie_id.isin(allowed).all():
            raise ValueError("rating target without verified TMDB bridge")
        events, users = make_examples(ratings)
        users["first_event_row"] += total_examples
        event_writer.write_table(pa.Table.from_pandas(events, schema=EVENT_SCHEMA,
                                                       preserve_index=False))
        user_writer.write_table(pa.Table.from_pandas(users, schema=USER_SCHEMA,
                                                      preserve_index=False))
        total_examples += len(events)
        total_users += len(users)
        example_splits.update(events.split_id.value_counts().to_dict())
        user_splits.update(users.split_id.value_counts().to_dict())
        signal_counts.update(events.rating_signal.value_counts().to_dict())
        cold_start += int(events.history_end_exclusive.eq(0).sum())
        same_timestamp += int(events.user_seq.gt(events.history_end_exclusive).sum())

    with pq.ParquetWriter(event_path, EVENT_SCHEMA, compression="zstd") as event_writer, \
            pq.ParquetWriter(user_path, USER_SCHEMA, compression="zstd") as user_writer:
        for row_group in range(rating_file.num_row_groups):
            chunk = rating_file.read_row_group(
                row_group, columns=["uid", "movie_id", "rating", "timestamp"]
            ).to_pandas()
            if chunk.empty:
                continue
            ids = chunk.uid.to_numpy()
            if ids[0] < previous_uid or np.any(ids[1:] < ids[:-1]):
                raise ValueError("ratings parquet must be sorted by uid for streaming")
            previous_uid = int(ids[-1])
            if pending is not None:
                chunk = pd.concat([pending, chunk], ignore_index=True)
            last_uid = int(chunk.uid.iloc[-1])
            last_start = int(np.searchsorted(chunk.uid.to_numpy(), last_uid, side="left"))
            append_complete(chunk.iloc[:last_start], event_writer, user_writer)
            pending = chunk.iloc[last_start:].copy()
        if pending is not None:
            append_complete(pending, event_writer, user_writer)
    if total_examples != rating_file.metadata.num_rows or pq.ParquetFile(event_path).metadata.num_rows != total_examples:
        raise AssertionError("one target per source rating required")
    split_names = {0: "train", 1: "valid", 2: "test"}
    counts = {
        "examples": total_examples, "users": total_users,
        "examples_by_split": {split_names[k]: int(v) for k, v in example_splits.items()},
        "users_by_split": {split_names[k]: int(v) for k, v in user_splits.items()},
        "examples_by_rating_signal": {str(k): int(v) for k, v in signal_counts.items()},
        "cold_start_targets": cold_start,
        "same_timestamp_nonfirst_targets": same_timestamp,
    }
    manifest = {
        "version": "ml32-exact-tmdb-optional-kobis-temporal-examples-v1",
        "sources": {"verified_ratings": source(ratings_path), "movie_bridge": source(bridge_path)},
        "rules": {
            "target": "one observed MovieLens star rating per row; no GPT-created label or unobserved negative",
            "history": "within the same uid, sorted by timestamp then movie_id; use event rows [first_event_row, first_event_row + history_end_exclusive); equal-timestamp targets cannot see each other",
            "rating_signal": "-1 for <=2.5, 1 for 3.0-3.5, 2 for >=4.0; original 0.5-5.0 rating preserved",
            "split": "uid SHA256 evidence-v1:user:uid first8 modulo10; 0-7 train, 8 valid, 9 test",
            "movie_features": "join movie_bridge.parquet by movie_id; verified KOBIS optional and null when unavailable",
            "public_asof": "TMDB/KOBIS current snapshots may postdate target; evaluate public features separately to detect retrospective leakage",
        },
        "counts": counts,
        "artifacts": {"temporal_examples": source(event_path), "user_index": source(user_path)},
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ratings", type=Path, required=True)
    parser.add_argument("--movie-bridge", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.ratings, args.movie_bridge, args.output_dir)["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
