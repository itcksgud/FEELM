"""Build time-ordered, evidence-auditable MovieLens ranking episodes.

The label is an observed *future* rating, not an exposure or an unobserved
negative.  Public metadata is a retrospective snapshot; the manifest states
this explicitly rather than claiming historical availability.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


DEFAULT_KS = (0, 1, 3, 5, 10, 20, 30, 50, 100)
DIRECT_AXES = ("collection", "director", "cast")
JOINT_AXES = ("country_genre", "keyword")
ALL_AXES = DIRECT_AXES + JOINT_AXES

ROW_SCHEMA = pa.schema([
    ("qid", pa.int64()), ("uid", pa.int64()), ("split", pa.string()),
    ("k", pa.int32()), ("requested_k", pa.int32()),
    ("history_cutoff", pa.int64()), ("candidate_timestamp", pa.int64()),
    ("movie_id", pa.int64()), ("rating", pa.float32()),
    ("relevance", pa.int8()), ("metadata_present", pa.bool_()),
    ("candidate_release_year", pa.int32()),
    *[(f"{axis}_known", pa.bool_()) for axis in ALL_AXES],
    *[(f"history_{axis}_missing_count", pa.int32()) for axis in ALL_AXES],
    *[(f"{axis}_{sign}_count", pa.int32()) for axis in ALL_AXES
      for sign in ("pos", "strong_pos", "weak_pos", "neg")],
    ("tmdb_vote_average", pa.float32()), ("tmdb_vote_count", pa.float64()),
    ("kobis_audience_cumulative", pa.float64()), ("kobis_verified", pa.bool_()),
    ("public_evidence_state", pa.string()), ("tmdb_rating_state", pa.string()),
    ("tmdb_volume_state", pa.string()), ("relation_evidence_state", pa.string()),
    ("evidence_tier", pa.string()),
    ("sampling_probability", pa.float32()),
])
CONTEXT_SCHEMA = pa.schema([
    ("qid", pa.int64()), ("uid", pa.int64()), ("split", pa.string()),
    ("k", pa.int32()), ("requested_k", pa.int32()),
    ("history_cutoff", pa.int64()),
    ("history_movie_ids", pa.list_(pa.int64())),
    ("history_ratings", pa.list_(pa.float32())),
])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source(path: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    return {"path": str(resolved), "bytes": resolved.stat().st_size, "sha256": _sha256(resolved)}


def _tokens(value: Any) -> frozenset[str] | None:
    """None means unavailable; a known empty list means zero matches."""
    if value is None or value is pd.NA:
        return None
    if isinstance(value, float) and np.isnan(value):
        return None
    if isinstance(value, np.ndarray):
        value = value.tolist()
    if isinstance(value, (list, tuple, set)):
        return frozenset(str(part) for part in value if part is not None and str(part))
    return frozenset((str(value),)) if str(value) else frozenset()


def _finite_positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    return number if np.isfinite(number) and number > 0 else None


def _movie_info(row: dict[str, Any] | None, public: dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {"present": False, "axes": {axis: None for axis in ALL_AXES},
                "tmdb_average": None, "tmdb_votes": None, "kobis_audience": None,
                "kobis_verified": False, "release_year": None}
    countries = _tokens(row.get("origin_country_codes"))
    if not countries:
        countries = _tokens(row.get("production_country_codes"))
    genres = _tokens(row.get("genre_ids"))
    joint = None if countries is None or genres is None else frozenset(
        f"{country}|{genre}" for country in countries for genre in genres
    )
    axes = {
        "collection": _tokens(row.get("collection_ids")),
        "director": _tokens(row.get("director_ids")),
        "cast": _tokens(row.get("top5_cast_ids")),
        "country_genre": joint,
        "keyword": _tokens(row.get("keyword_ids")),
    }
    tmdb_average = _finite_positive((public or row).get("tmdb_vote_average"))
    tmdb_votes = _finite_positive((public or row).get("tmdb_vote_count"))
    if tmdb_average is None or tmdb_average > 10 or tmdb_votes is None:
        tmdb_average = tmdb_votes = None
    verified = bool(public and str(public.get("kobis_link_status") or "").startswith("VERIFIED"))
    audience = _finite_positive(public.get("kobis_audience_cumulative")) if verified else None
    raw_year = _finite_positive(row.get("release_year"))
    release_year = int(raw_year) if raw_year is not None and 1888 <= raw_year <= 2100 else None
    return {"present": True, "axes": axes, "tmdb_average": tmdb_average,
            "tmdb_votes": tmdb_votes, "kobis_audience": audience,
            "kobis_verified": bool(verified and audience is not None), "release_year": release_year}


def _metadata_lookup(metadata: pd.DataFrame, catalog: pd.DataFrame) -> dict[int, dict[str, Any]]:
    if metadata.movie_id.isna().any() or metadata.movie_id.duplicated().any():
        raise ValueError("metadata movie_id must be non-null and unique")
    if catalog.tmdb_id.isna().any() or catalog.tmdb_id.duplicated().any():
        raise ValueError("catalog tmdb_id must be non-null and unique; do not guess a crosswalk")
    public_by_tmdb = {int(row["tmdb_id"]): row for row in catalog.to_dict("records")}
    lookup = {}
    for row in metadata.to_dict("records"):
        tmdb_id = row.get("tmdb_id")
        public = public_by_tmdb.get(int(tmdb_id)) if pd.notna(tmdb_id) else None
        lookup[int(row["movie_id"])] = _movie_info(row, public)
    return lookup


def _user_split(uid: int) -> str:
    bucket = int.from_bytes(hashlib.sha256(f"evidence-v1:user:{uid}".encode()).digest()[:8], "big") % 10
    return "train" if bucket < 8 else "valid" if bucket == 8 else "test"


def _history_counters(history: list[tuple[int, float]], lookup: dict[int, dict[str, Any]]):
    # Token -> movie-id set, so a past film sharing two cast/keyword tokens
    # contributes only one distinct supporting film to the candidate.
    counts = {axis: {"pos": defaultdict(set), "strong_pos": defaultdict(set),
                     "weak_pos": defaultdict(set), "neg": defaultdict(set), "missing": set()}
              for axis in ALL_AXES}
    for movie_id, rating in history:
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
    return counts


def _evidence(movie: dict[str, Any], counts) -> dict[str, Any]:
    result: dict[str, Any] = {"metadata_present": movie["present"],
                              "candidate_release_year": movie["release_year"]}
    for axis in ALL_AXES:
        tokens = movie["axes"][axis]
        missing = len(counts[axis]["missing"])
        result[f"history_{axis}_missing_count"] = missing
        result[f"{axis}_known"] = tokens is not None and missing == 0
        for sign in ("pos", "strong_pos", "weak_pos", "neg"):
            result[f"{axis}_{sign}_count"] = (
                None if tokens is None else len(set().union(*(counts[axis][sign].get(token, set()) for token in tokens)))
            )
    result.update({
        "tmdb_vote_average": movie["tmdb_average"],
        "tmdb_vote_count": movie["tmdb_votes"],
        "kobis_audience_cumulative": movie["kobis_audience"],
        "kobis_verified": movie["kobis_verified"],
    })
    tmdb = movie["tmdb_votes"] is not None
    kobis = movie["kobis_verified"]
    result["public_evidence_state"] = "BOTH" if tmdb and kobis else "TMDB_ONLY" if tmdb else "KOBIS_ONLY" if kobis else "NONE"
    result["tmdb_rating_state"] = (
        "MISSING" if not tmdb else "MEASURED_LOW" if movie["tmdb_average"] < 6.0 else "MEASURED_OTHER"
    )
    result["tmdb_volume_state"] = (
        "MISSING" if not tmdb else "MEASURED_LOW" if movie["tmdb_votes"] < 100 else "MEASURED_OTHER"
    )
    linked = lambda axes: any((result[f"{axis}_{sign}_count"] or 0) > 0 for axis in axes for sign in ("pos", "neg"))
    relation_state = (
        "DIRECT" if linked(DIRECT_AXES) else "JOINT" if linked(JOINT_AXES)
        else "NONE" if all(result[f"{axis}_known"] for axis in ALL_AXES) else "UNKNOWN"
    )
    result["relation_evidence_state"] = relation_state
    result["evidence_tier"] = (
        relation_state if relation_state in {"DIRECT", "JOINT"}
        else "PUBLIC_ONLY" if tmdb or kobis else relation_state
    )
    return result


def _validate_ratings(ratings: pd.DataFrame) -> pd.DataFrame:
    required = {"uid", "movie_id", "rating", "timestamp"}
    if not required.issubset(ratings):
        raise ValueError(f"ratings missing {sorted(required - set(ratings))}")
    if ratings[list(required)].isna().any().any():
        raise ValueError("ratings contain null uid/movie_id/rating/timestamp")
    stars = ratings.rating.to_numpy(float)
    if not np.isfinite(stars).all() or ((stars * 2) % 1 != 0).any() or ((stars < 0.5) | (stars > 5)).any():
        raise ValueError("ratings must be finite MovieLens 0.5-step values in [0.5,5]")
    result = ratings.loc[:, ["uid", "movie_id", "rating", "timestamp"]].copy()
    for column in ("uid", "movie_id", "timestamp"):
        result[column] = result[column].astype(np.int64)
    return result.sort_values(["uid", "timestamp", "movie_id"], kind="stable").reset_index(drop=True)


def _episodes(user: pd.DataFrame, ks: tuple[int, ...], next_n: int,
              lookup: dict[int, dict[str, Any]]):
    """Yield exact effective K boundaries; a timestamp group is never split."""
    events = [(int(row.movie_id), float(row.rating), int(row.timestamp)) for row in user.itertuples(index=False)]
    if not events:
        return
    if 0 in ks:
        yield 0, events[0][2] - 1, [], _next_candidates(events, events[0][2] - 1, set(), next_n, lookup)
    latest: dict[int, tuple[float, int]] = {}
    offset = 0
    while offset < len(events):
        timestamp = events[offset][2]
        while offset < len(events) and events[offset][2] == timestamp:
            movie_id, rating, _ = events[offset]
            latest[movie_id] = (rating, offset)
            offset += 1
        k = len(latest)
        if k in ks:
            history = [(movie_id, rating) for movie_id, (rating, _) in sorted(latest.items(), key=lambda item: item[1][1])]
            yield k, timestamp, history, _next_candidates(events[offset:], timestamp, set(latest), next_n, lookup)


def _next_candidates(events: list[tuple[int, float, int]], cutoff: int, seen: set[int], limit: int,
                     lookup: dict[int, dict[str, Any]]):
    result = []
    used = set(seen)
    cutoff_year = datetime.fromtimestamp(cutoff, timezone.utc).year
    for movie_id, rating, timestamp in events:
        if len(result) >= limit and timestamp != result[-1][2]:
            break
        if timestamp <= cutoff or movie_id in used:
            continue
        used.add(movie_id)
        release_year = lookup.get(movie_id, {}).get("release_year")
        if release_year is not None and release_year > cutoff_year:
            continue
        result.append((movie_id, rating, timestamp))
    return result


def _flush(writer: pq.ParquetWriter, data: list[dict[str, Any]], schema: pa.Schema) -> None:
    if data:
        writer.write_table(pa.Table.from_pylist(data, schema=schema))
        data.clear()


def _read_available(path: Path, wanted: tuple[str, ...]) -> pd.DataFrame:
    available = set(pq.read_schema(path).names)
    return pd.read_parquet(path, columns=[column for column in wanted if column in available])


def build_dataset(
    ratings_path: Path, metadata_path: Path, catalog_path: Path, output_dir: Path,
    *, ks: tuple[int, ...] = DEFAULT_KS, next_n: int = 20,
    max_users: int | None = None,
) -> dict[str, Any]:
    if not ks or any(k < 0 for k in ks) or len(set(ks)) != len(ks):
        raise ValueError("ks must contain distinct nonnegative integers")
    if next_n <= 0 or (max_users is not None and max_users <= 0):
        raise ValueError("next_n/max_users must be positive")
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite {output_dir}")
    sources = {name: _source(path) for name, path in (
        ("ratings", ratings_path), ("metadata", metadata_path), ("catalog", catalog_path)
    )}
    ratings = _validate_ratings(pd.read_parquet(ratings_path, columns=["uid", "movie_id", "rating", "timestamp"]))
    source_rating_rows = len(ratings)
    metadata = _read_available(metadata_path, (
        "movie_id", "tmdb_id", "release_year", "collection_ids", "director_ids",
        "top5_cast_ids", "keyword_ids", "origin_country_codes", "production_country_codes",
        "genre_ids", "tmdb_vote_average", "tmdb_vote_count",
    ))
    catalog = _read_available(catalog_path, (
        "tmdb_id", "tmdb_vote_average", "tmdb_vote_count", "kobis_link_status",
        "kobis_audience_cumulative",
    ))
    lookup = _metadata_lookup(metadata, catalog)
    all_users = sorted(int(value) for value in ratings.uid.unique())
    source_user_count = len(all_users)
    if max_users is not None:
        all_users = sorted(all_users, key=lambda uid: hashlib.sha256(f"sample:{uid}".encode()).digest())[:max_users]
        all_users.sort()
    selected = set(all_users)
    user_selection_fraction = len(selected) / max(source_user_count, 1)
    ratings = ratings[ratings.uid.isin(selected)]
    output_dir.mkdir(parents=True, exist_ok=False)
    rows_path, contexts_path = output_dir / "rows.parquet", output_dir / "contexts.parquet"
    row_buffer: list[dict[str, Any]] = []
    context_buffer: list[dict[str, Any]] = []
    split_rows: Counter[str] = Counter()
    split_contexts: Counter[str] = Counter()
    tier_rows: Counter[str] = Counter()
    relation_rows: Counter[str] = Counter()
    public_rows: Counter[str] = Counter()
    k_contexts: Counter[int] = Counter()
    same_movie_revisions = int(ratings.duplicated(["uid", "movie_id"]).sum())
    unmatched_rows = 0
    unknown_release_rows = 0
    qid = 0
    with pq.ParquetWriter(rows_path, ROW_SCHEMA, compression="zstd") as row_writer, pq.ParquetWriter(
        contexts_path, CONTEXT_SCHEMA, compression="zstd"
    ) as context_writer:
        for uid, user in ratings.groupby("uid", sort=True):
            split = _user_split(int(uid))
            for k, cutoff, history, candidates in _episodes(user, tuple(sorted(ks)), next_n, lookup):
                if not candidates:
                    continue
                qid += 1
                context_buffer.append({
                    "qid": qid, "uid": int(uid), "split": split, "k": k, "requested_k": k,
                    "history_cutoff": cutoff,
                    "history_movie_ids": [movie_id for movie_id, _ in history],
                    "history_ratings": [rating for _, rating in history],
                })
                split_contexts[split] += 1
                k_contexts[k] += 1
                counts = _history_counters(history, lookup)
                for movie_id, rating, timestamp in candidates:
                    movie = lookup.get(movie_id, _movie_info(None, None))
                    evidence = _evidence(movie, counts)
                    row_buffer.append({
                        "qid": qid, "uid": int(uid), "split": split, "k": k,
                        "requested_k": k, "history_cutoff": cutoff,
                        "candidate_timestamp": timestamp, "movie_id": movie_id,
                        "rating": rating, "relevance": max(0, int(round(rating * 2)) - 5),
                        **evidence, "sampling_probability": user_selection_fraction,
                    })
                    split_rows[split] += 1
                    tier_rows[evidence["evidence_tier"]] += 1
                    relation_rows[evidence["relation_evidence_state"]] += 1
                    public_rows[evidence["public_evidence_state"]] += 1
                    unmatched_rows += not movie["present"]
                    unknown_release_rows += movie["release_year"] is None
                if len(row_buffer) >= 50_000:
                    _flush(row_writer, row_buffer, ROW_SCHEMA)
                if len(context_buffer) >= 2_500:
                    _flush(context_writer, context_buffer, CONTEXT_SCHEMA)
        _flush(row_writer, row_buffer, ROW_SCHEMA)
        _flush(context_writer, context_buffer, CONTEXT_SCHEMA)
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, check=False,
    )
    manifest = {
        "version": "evidence-rank-dataset-v1r2", "code_revision": revision.stdout.strip() if revision.returncode == 0 else None,
        "builder": _source(Path(__file__)), "sources": sources,
        "settings": {"ks": list(ks), "next_n_observed_ratings": next_n, "max_users": max_users,
                     "split": "sha256(evidence-v1:user:uid) first8 mod10: 0-7 train, 8 valid, 9 test",
                     "user_hash_selection_fraction": user_selection_fraction,
                     "conditional_row_probability": 1.0,
                     "timestamp_rule": "strictly later; equal timestamp never split",
                     "rating_revision_rule": "latest observed prior value; seen films excluded from future candidates",
                     "history_rating_bins": "strong positive >=4; weak positive 3..3.5; negative <=2.5; distinct movie union per relation axis",
                     "release_year_rule": "exclude candidate if known year exceeds UTC year of history cutoff; unknown year retained",
                     "tmdb_diagnostic_bins": "average<6 and votes<100 are separate MEASURED_LOW flags, never a negative label"},
        "schemas": {"rows": str(ROW_SCHEMA), "contexts": str(CONTEXT_SCHEMA)},
        "counts": {"source_ratings": int(source_rating_rows), "source_users": source_user_count,
                   "selected_users": len(all_users), "selected_ratings": int(len(ratings)),
                   "same_user_movie_revisions": same_movie_revisions,
                   "contexts_by_split": dict(split_contexts), "rows_by_split": dict(split_rows),
                   "contexts_by_k": {str(k): count for k, count in sorted(k_contexts.items())},
                   "rows_by_evidence_tier": dict(tier_rows),
                   "rows_by_relation_state": dict(relation_rows),
                   "rows_by_public_state": dict(public_rows),
                   "rows_missing_metadata": unmatched_rows,
                   "rows_unknown_release_year": unknown_release_rows},
        "limitations": [
            "Future labels are voluntarily observed ratings only; unrated and unexposed films are not negatives.",
            "TMDB/KOBIS public values are current snapshots and may postdate MovieLens episode cutoffs; retrospective diagnostic only.",
            "The ratings/metadata pair is a research extraction; its coverage versus original MovieLens is not asserted by this builder.",
            "Release year has only year granularity; same-year post-cutoff releases cannot be ruled out.",
            "sampling_probability is the selected-user hash fraction times 1 within selected episodes; it is not an exposure propensity.",
            "If max_users is set, rows from omitted users are not present; UNKNOWN metadata rows within selected users are retained.",
            "MovieLens users are not FEELM users; no service-quality inference follows from this dataset.",
        ],
        "artifacts": {"rows": _source(rows_path), "contexts": _source(contexts_path)},
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ratings", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--full-catalog", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--ks", type=int, nargs="+", default=DEFAULT_KS)
    parser.add_argument("--next-n", type=int, default=20)
    parser.add_argument("--max-users", type=int)
    args = parser.parse_args()
    report = build_dataset(args.ratings, args.metadata, args.full_catalog, args.output_dir,
                           ks=tuple(args.ks), next_n=args.next_n, max_users=args.max_users)
    print(json.dumps(report["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
