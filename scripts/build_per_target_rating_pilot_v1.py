"""Build strict-past rating targets and an FM history selector from ML32.

The full temporal source is read; a deterministic user/target sample bounds
desktop training. Every *selected* target has its own exactly prior history.
Unrated movies are never labels, and current public metadata is retrospective.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from scipy import sparse

from build_evidence_rank_dataset_v1 import (
    ALL_AXES, ROW_SCHEMA, _evidence, _flush, _history_counters,
    _metadata_lookup, _read_available, _source, _user_split,
)
from rating_semantics_v7 import utility


SCHEMA = ROW_SCHEMA.append(pa.field("selector_row", pa.int32()))
for _name, _type in (("history_rating_mean", pa.float32()),
                     ("history_rating_std", pa.float32()),
                     ("history_positive_share", pa.float32()),
                     ("history_negative_share", pa.float32())):
    SCHEMA = SCHEMA.append(pa.field(_name, _type))


def _hash(seed: str) -> int:
    return int.from_bytes(hashlib.sha256(seed.encode()).digest()[:8], "big")


def _choose_targets(uid: int, eligible: np.ndarray, limit: int) -> np.ndarray:
    if limit == 0 or len(eligible) <= limit:
        return eligible
    rng = np.random.default_rng(_hash(f"per-target-v1:{uid}"))
    return np.sort(rng.choice(eligible, size=limit, replace=False)).astype(np.int64)


def _accumulate(counts: dict, movie_id: int, rating: float, lookup: dict) -> None:
    signs = ("pos", "strong_pos") if rating >= 4 else (
        ("pos", "weak_pos") if rating >= 3 else ("neg",) if rating <= 2.5 else ()
    )
    movie = lookup.get(movie_id)
    for axis in ALL_AXES:
        tokens = movie["axes"][axis] if movie is not None else None
        if tokens is None:
            counts[axis]["missing"].add(movie_id)
        elif signs:
            for token in tokens:
                for sign in signs:
                    counts[axis][sign][token].add(movie_id)


def _selected_positions(user: pd.DataFrame, lookup: dict, limit: int) -> tuple[np.ndarray, int, int]:
    past = user.history_end_exclusive.to_numpy(np.int32)
    movie = user.movie_id.to_numpy(np.int32)
    timestamps = user.timestamp.to_numpy(np.int64)
    # First rating (K=0) cannot have a personal relation; keep it for a
    # separate cold-start diagnostic, not this personalised fit.
    eligible = []
    future_release = 0
    for index in np.flatnonzero(past > 0):
        year = lookup[int(movie[index])]["release_year"]
        if year is not None and year > datetime.fromtimestamp(int(timestamps[index]) - 1, timezone.utc).year:
            future_release += 1
            continue
        eligible.append(int(index))
    return (_choose_targets(int(user.uid.iloc[0]), np.asarray(eligible, np.int64), limit),
            future_release, len(eligible))


def build(temporal_path: Path, user_index_path: Path, bridge: Path, catalog: Path,
          output: Path, *, max_users: int = 30_000, targets_per_user: int = 20) -> dict:
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    if max_users <= 0 or targets_per_user < 0:
        raise ValueError("max_users>0 and targets_per_user>=0 required")
    started = time.monotonic()
    sources = {name: _source(path) for name, path in (
        ("temporal_examples", temporal_path), ("user_index", user_index_path),
        ("metadata", bridge), ("catalog", catalog))}
    users = pd.read_parquet(user_index_path, columns=["uid", "event_count"])
    if users.uid.isna().any() or users.uid.duplicated().any():
        raise ValueError("user index uid must be non-null and unique")
    chosen_users = set(sorted(users.uid.astype(int), key=lambda uid: _hash(f"variable-k-users-v1:{uid}"))[:max_users])
    ratings = pd.read_parquet(temporal_path, columns=["uid", "movie_id", "rating", "timestamp",
                                                     "history_end_exclusive"])
    if ratings.isna().any().any():
        raise ValueError("null temporal fields")
    stars_all = ratings.rating.to_numpy(np.float32)
    if (not np.isfinite(stars_all).all() or ((stars_all < .5) | (stars_all > 5)).any()
            or (stars_all * 2 != np.floor(stars_all * 2)).any()):
        raise ValueError("invalid MovieLens star scale")
    source_rows = len(ratings)
    ratings = ratings.loc[ratings.uid.isin(chosen_users)]
    actual_counts = ratings.groupby("uid", sort=False).size()
    indexed_counts = users.set_index("uid").loc[actual_counts.index, "event_count"]
    if not np.array_equal(actual_counts.to_numpy(), indexed_counts.to_numpy()):
        raise ValueError("user index event_count differs from temporal source")
    metadata = _read_available(bridge, (
        "movie_id", "tmdb_id", "release_year", "collection_ids", "director_ids",
        "top5_cast_ids", "keyword_ids", "origin_country_codes",
        "production_country_codes", "genre_ids", "tmdb_vote_average", "tmdb_vote_count",
    )).sort_values("movie_id", kind="stable").reset_index(drop=True)
    if metadata.movie_id.isna().any() or metadata.movie_id.duplicated().any():
        raise ValueError("metadata movie IDs must be unique")
    movie_axis = metadata.movie_id.to_numpy(np.int64)
    catalog_frame = _read_available(catalog, (
        "tmdb_id", "tmdb_vote_average", "tmdb_vote_count", "kobis_link_status",
        "kobis_audience_cumulative",
    ))
    lookup = _metadata_lookup(metadata, catalog_frame)
    selections: dict[int, np.ndarray] = {}
    eligible_counts: dict[int, int] = {}
    selected_rows = selector_nnz = future_release = 0
    for uid, user in ratings.groupby("uid", sort=True):
        user = user.sort_values(["timestamp", "movie_id"], kind="stable")
        positions, bad_year, eligible_count = _selected_positions(user, lookup, targets_per_user)
        selections[int(uid)] = positions
        eligible_counts[int(uid)] = eligible_count
        selected_rows += len(positions)
        selector_nnz += int(user.history_end_exclusive.to_numpy(np.int32)[positions].sum())
        future_release += bad_year
    if selected_rows == 0:
        raise ValueError("no eligible targets")
    if selected_rows >= 2 ** 31 or selector_nnz >= 2 ** 31:
        raise ValueError("desktop CSR limit exceeded; shard before full-data build")
    print(json.dumps({"build_preflight": {"selected_users": len(chosen_users),
                                          "selected_targets": selected_rows,
                                          "selector_entries": selector_nnz,
                                          "estimated_csr_bytes": selector_nnz * 8 + (selected_rows + 1) * 8}},
                     ensure_ascii=False), flush=True)
    selector_indices = np.empty(selector_nnz, np.int32)
    selector_values = np.empty(selector_nnz, np.float32)
    selector_indptr = np.empty(selected_rows + 1, np.int64)
    selector_indptr[0] = 0
    output.mkdir(parents=True, exist_ok=False)
    row_path = output / "rows.parquet"
    buffer: list[dict] = []
    counts = Counter()
    splits = Counter()
    relation = Counter()
    public = Counter()
    k_bucket = Counter()
    cursor = row_index = 0
    with pq.ParquetWriter(row_path, SCHEMA, compression="zstd") as writer:
        for user_number, (uid, user) in enumerate(ratings.groupby("uid", sort=True), 1):
            user = user.sort_values(["timestamp", "movie_id"], kind="stable")
            movie = user.movie_id.to_numpy(np.int64)
            stars = user.rating.to_numpy(np.float32)
            timestamps = user.timestamp.to_numpy(np.int64)
            pointers = user.history_end_exclusive.to_numpy(np.int32)
            positions = selections[int(uid)]
            selected = set(int(value) for value in positions)
            mapped = np.searchsorted(movie_axis, movie)
            if (mapped >= len(movie_axis)).any():
                raise ValueError(f"unmatched movie in selected source user {uid}")
            if not np.array_equal(movie_axis[mapped], movie):
                raise ValueError(f"unmatched movie in selected source user {uid}")
            utilities = utility(stars)
            abs_cumulative = np.cumsum(np.abs(utilities), dtype=np.float64)
            evidence_counts = _history_counters([], lookup)
            past_n = past_sum = past_sumsq = past_pos = past_neg = 0
            start = 0
            while start < len(user):
                stop = start + 1
                while stop < len(user) and timestamps[stop] == timestamps[start]:
                    stop += 1
                if not np.all(pointers[start:stop] == start):
                    raise ValueError(f"strict history pointer mismatch at user {uid}, timestamp {timestamps[start]}")
                if past_n != start:
                    raise ValueError("incremental history length mismatch")
                for index in range(start, stop):
                    if index not in selected:
                        continue
                    k = int(pointers[index])
                    # Prediction is immediately before this rating event; all
                    # strictly earlier timestamps are available, same-time are not.
                    history_cutoff = int(timestamps[index]) - 1
                    info = lookup[int(movie[index])]
                    evidence = _evidence(info, evidence_counts)
                    sigma = max(float(abs_cumulative[k - 1]), 1.0) if k else 1.0
                    selector_indices[cursor:cursor + k] = mapped[:k].astype(np.int32, copy=False)
                    selector_values[cursor:cursor + k] = utilities[:k] / sigma
                    cursor += k
                    row_index += 1
                    selector_indptr[row_index] = cursor
                    split = _user_split(int(uid))
                    rating = float(stars[index])
                    row = {
                        "qid": row_index, "selector_row": row_index - 1,
                        "uid": int(uid), "split": split,
                        "k": k, "requested_k": k,
                        "history_cutoff": history_cutoff,
                        "candidate_timestamp": int(timestamps[index]),
                        "movie_id": int(movie[index]), "rating": rating,
                        "relevance": max(0, int(round(rating * 2)) - 5),
                        **evidence,
                        "sampling_probability": (len(chosen_users) / len(users)) *
                                                (len(positions) / max(eligible_counts[int(uid)], 1)),
                        "history_rating_mean": past_sum / k if k else None,
                        "history_rating_std": max(past_sumsq / k - (past_sum / k) ** 2, 0) ** .5 if k else None,
                        "history_positive_share": past_pos / k if k else None,
                        "history_negative_share": past_neg / k if k else None,
                    }
                    buffer.append(row)
                    splits[split] += 1
                    relation[evidence["relation_evidence_state"]] += 1
                    public[evidence["public_evidence_state"]] += 1
                    k_bucket["1-4" if k < 5 else "5-9" if k < 10 else "10-19" if k < 20
                             else "20-49" if k < 50 else "50-99" if k < 100 else "100+"] += 1
                    if len(buffer) >= 25_000:
                        _flush(writer, buffer, SCHEMA)
                # Same-timestamp targets have all been represented before any
                # of these events becomes history for the next timestamp.
                for index in range(start, stop):
                    value = float(stars[index])
                    _accumulate(evidence_counts, int(movie[index]), value, lookup)
                    past_n += 1
                    past_sum += value
                    past_sumsq += value * value
                    past_pos += value >= 3
                    past_neg += value <= 2.5
                start = stop
            if user_number % 5_000 == 0:
                print(json.dumps({"users_processed": user_number, "target_rows": row_index,
                                  "selector_entries": cursor,
                                  "seconds": round(time.monotonic() - started, 1)}), flush=True)
        _flush(writer, buffer, SCHEMA)
    if row_index != selected_rows or cursor != selector_nnz:
        raise ValueError("selector preallocation count mismatch")
    selector = sparse.csr_matrix((selector_values, selector_indices, selector_indptr),
                                 shape=(selected_rows, len(movie_axis)), dtype=np.float32)
    sparse.save_npz(output / "history_selector.npz", selector, compressed=True)
    report = {
        "version": "ml32-strict-per-target-star-pilot-v1",
        "sources": sources, "builder": _source(Path(__file__)),
        "settings": {"max_users": max_users, "targets_per_user": targets_per_user,
                     "user_selection": "same deterministic SHA256 user sample as variable-K pilot",
                     "target_selection": "deterministic uniform without replacement among eligible per-user ratings",
                     "profile": "all strictly earlier timestamps, utility-weighted; same-timestamp labels excluded",
                     "labels": "observed MovieLens raw stars only; no unrated negatives",
                     "split": "same user hash as variable-K dataset",
                     "metadata": "current TMDB/KOBIS snapshot, retrospective"},
        "counts": {"source_rating_rows": source_rows, "source_users": len(users),
                   "selected_users": len(chosen_users), "selected_users_rating_rows": len(ratings),
                   "selected_targets": selected_rows, "selector_nnz": selector_nnz,
                   "future_release_eligible_exclusions": future_release,
                   "targets_by_split": dict(splits), "targets_by_relation": dict(relation),
                   "targets_by_public": dict(public), "targets_by_k_bucket": dict(k_bucket)},
        "movie_axis_sha256": hashlib.sha256(movie_axis.tobytes()).hexdigest(),
        "artifacts": {name: _source(output / name) for name in ("rows.parquet", "history_selector.npz")},
        "seconds": time.monotonic() - started,
        "limitations": ["Selected target sample, not all 31.9M ratings",
                        "No unobserved movie label or exposure probability",
                        "Current public metadata may postdate ratings; same-year release day unknown",
                        "Per-target qid is singleton, so rank metrics require separate frozen-cutoff groups"],
    }
    (output / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                          encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--temporal-examples", type=Path, required=True)
    parser.add_argument("--user-index", type=Path, required=True)
    parser.add_argument("--bridge", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-users", type=int, default=30_000)
    parser.add_argument("--targets-per-user", type=int, default=20)
    args = parser.parse_args()
    report = build(args.temporal_examples, args.user_index, args.bridge, args.catalog,
                   args.output_dir, max_users=args.max_users,
                   targets_per_user=args.targets_per_user)
    print(json.dumps({"counts": report["counts"], "seconds": report["seconds"]},
                     ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
