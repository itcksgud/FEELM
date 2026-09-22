"""Materialize the full MovieLens 32M source with an exact service-catalog bridge.

Only MovieLens movies whose links.csv TMDB ID agrees with a unique, MATCHED
service-catalog row are retained. KOBIS is an optional, verified side signal:
missing KOBIS never discards a TMDB-linked rating. No labels are synthesized.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import zipfile

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


RATING_SCHEMA = pa.schema([
    ("uid", pa.int32()), ("movie_id", pa.int32()),
    ("rating", pa.float32()), ("timestamp", pa.int64()),
])
BRIDGE_COLUMNS = (
    "movie_id", "tmdb_id", "service_movie_id", "title", "original_title",
    "release_year", "genre_ids", "keyword_ids", "origin_country_codes",
    "production_country_codes", "director_ids", "top5_cast_ids",
    "collection_ids", "tmdb_vote_average", "tmdb_vote_count",
    "tmdb_popularity", "kobis_movie_code", "kobis_link_status",
    "kobis_value_valid", "kobis_audience_cumulative",
    "kobis_sales_krw_cumulative", "kobis_statistics_as_of",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source(path: Path) -> dict:
    return {"path": str(path.resolve(strict=True)), "bytes": path.stat().st_size,
            "sha256": sha256(path)}


def verified_bridge(links: pd.DataFrame, catalog: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if links.movieId.isna().any() or links.movieId.duplicated().any():
        raise ValueError("links.csv movieId must be unique and non-null")
    if catalog.tmdb_id.isna().any() or catalog.tmdb_id.duplicated().any():
        raise ValueError("catalog tmdb_id must be unique and non-null")
    joined = links.merge(catalog, left_on="tmdbId", right_on="tmdb_id",
                         how="left", validate="many_to_one", indicator=True)
    duplicated_tmdb = links.tmdbId.notna() & links.tmdbId.duplicated(keep=False)
    catalog_ambiguous = duplicated_tmdb & joined["_merge"].eq("both")
    exact = (
        joined["_merge"].eq("both")
        & joined.mapping_status.eq("MATCHED")
        & joined.movielens_movie_id.eq(joined.movieId)
        & joined.tmdb_metadata_found.eq(True)
        & ~duplicated_tmdb
    )
    joined["bridge_status"] = np.select(
        [links.tmdbId.isna(), joined["_merge"].ne("both"), catalog_ambiguous,
         ~joined.mapping_status.eq("MATCHED"),
         ~joined.movielens_movie_id.eq(joined.movieId),
         ~joined.tmdb_metadata_found.eq(True)],
        ["NO_TMDB_ID", "NO_VERIFIED_CATALOG_ROW", "AMBIGUOUS_TMDB_ID",
         "CATALOG_NOT_MATCHED", "MOVIELENS_ID_DISAGREES", "TMDB_METADATA_MISSING"],
        default="MATCHED",
    )
    if not joined.loc[exact, "bridge_status"].eq("MATCHED").all():
        raise AssertionError("exact bridge and status disagree")
    audit = joined.loc[:, ["movieId", "imdbId", "tmdbId", "bridge_status"]].rename(
        columns={"movieId": "movie_id", "tmdbId": "tmdb_id"}
    )
    matched = joined.loc[exact].copy()
    matched["movie_id"] = matched.movieId.astype("int64")
    matched["tmdb_id"] = matched.tmdb_id.astype("int64")
    matched["service_movie_id"] = matched.service_movie_id.astype("int64")
    kobis_verified = (matched.kobis_link_status.fillna("").str.startswith("VERIFIED")
                      & matched.kobis_value_valid.eq(True))
    if matched.loc[kobis_verified, "kobis_audience_cumulative"].isna().any():
        raise ValueError("verified KOBIS audience must be present")
    for column in ("kobis_movie_code", "kobis_audience_cumulative",
                   "kobis_sales_krw_cumulative", "kobis_statistics_as_of"):
        matched.loc[~kobis_verified, column] = None
    matched["kobis_value_valid"] = kobis_verified
    return matched.loc[:, BRIDGE_COLUMNS], audit


def build(archive: Path, catalog_path: Path, output_dir: Path,
          *, chunk_rows: int = 1_000_000, expected_ratings: int | None = 32_000_204) -> dict:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite {output_dir}")
    if chunk_rows <= 0:
        raise ValueError("chunk_rows must be positive")
    with zipfile.ZipFile(archive) as zipped:
        links = pd.read_csv(zipped.open("ml-32m/links.csv"), dtype={"movieId": "int64"})
    catalog = pd.read_parquet(catalog_path, columns=[
        "tmdb_id", "service_movie_id", "movielens_movie_id", "mapping_status",
        "tmdb_metadata_found", "title", "original_title", "release_year",
        "genre_ids", "keyword_ids", "origin_country_codes", "production_country_codes",
        "director_ids", "top5_cast_ids", "collection_ids", "tmdb_vote_average",
        "tmdb_vote_count", "tmdb_popularity", "kobis_movie_code",
        "kobis_link_status", "kobis_value_valid", "kobis_audience_cumulative",
        "kobis_sales_krw_cumulative", "kobis_statistics_as_of",
    ])
    bridge, audit = verified_bridge(links, catalog)
    allowed = set(bridge.movie_id.astype(int))
    if bridge.movie_id.duplicated().any() or bridge.tmdb_id.duplicated().any():
        raise AssertionError("verified bridge must be one-to-one")
    output_dir.mkdir(parents=True, exist_ok=False)
    bridge_path = output_dir / "movie_bridge.parquet"
    audit_path = output_dir / "bridge_audit.parquet"
    ratings_path = output_dir / "ratings.parquet"
    bridge.to_parquet(bridge_path, index=False)
    audit.to_parquet(audit_path, index=False)
    source_rows = 0
    retained_rows = 0
    rating_counts: Counter[float] = Counter()
    excluded_rating_counts: Counter[str] = Counter()
    retained_users: set[int] = set()
    status_by_movie = audit.set_index("movie_id").bridge_status.to_dict()
    with zipfile.ZipFile(archive) as zipped, pq.ParquetWriter(
        ratings_path, RATING_SCHEMA, compression="zstd"
    ) as writer:
        chunks = pd.read_csv(zipped.open("ml-32m/ratings.csv"),
                             chunksize=chunk_rows,
                             dtype={"userId": "int32", "movieId": "int32",
                                    "rating": "float32", "timestamp": "int64"})
        for chunk in chunks:
            source_rows += len(chunk)
            stars = chunk.rating.to_numpy()
            if not np.isfinite(stars).all() or np.any(stars < 0.5) or np.any(stars > 5.0) or np.any(stars * 2 != np.floor(stars * 2)):
                raise ValueError("invalid MovieLens rating scale")
            kept = chunk.loc[chunk.movieId.isin(allowed)].rename(
                columns={"userId": "uid", "movieId": "movie_id"}
            )
            retained_rows += len(kept)
            retained_users.update(int(uid) for uid in kept.uid.unique())
            rating_counts.update(kept.rating.value_counts().to_dict())
            excluded_rating_counts.update(chunk.loc[~chunk.movieId.isin(allowed), "movieId"]
                                          .map(status_by_movie).value_counts().to_dict())
            writer.write_table(pa.Table.from_pandas(kept, schema=RATING_SCHEMA,
                                                     preserve_index=False))
    if expected_ratings is not None and source_rows != expected_ratings:
        raise ValueError(f"expected {expected_ratings} ratings, observed {source_rows}")
    if retained_rows != pq.ParquetFile(ratings_path).metadata.num_rows:
        raise AssertionError("retained row count differs from Parquet")
    if sum(excluded_rating_counts.values()) != source_rows - retained_rows:
        raise AssertionError("excluded rating reasons do not reconcile")
    manifest = {
        "version": "ml32-exact-tmdb-optional-kobis-source-v1",
        "sources": {"movielens_zip": source(archive), "service_catalog": source(catalog_path)},
        "rules": {
            "bridge": "links.csv tmdbId = unique catalog tmdb_id; catalog MATCHED, movielens_movie_id agrees, TMDB metadata found; ambiguous mappings excluded",
            "kobis": "only verified KOBIS fields from service catalog; missing KOBIS retained and never encoded as zero",
            "labels": "observed MovieLens stars only; no synthetic negatives or GPT-generated ratings",
            "temporal": "timestamp is retained; consumers must use strictly earlier user history and prevent same-timestamp leakage",
            "public_asof": "TMDB/KOBIS values are current snapshots and may postdate MovieLens ratings; not historical point-in-time features",
        },
        "counts": {
            "source_ratings": source_rows, "retained_ratings": retained_rows,
            "excluded_ratings": source_rows - retained_rows,
            "excluded_ratings_by_bridge_status": dict(excluded_rating_counts),
            "retained_users": len(retained_users),
            "links_movies": len(links), "matched_movies": len(bridge),
            "bridge_status_movies": audit.bridge_status.value_counts().to_dict(),
            "matched_movies_with_verified_kobis": int(bridge.kobis_value_valid.sum()),
            "rating_counts": {str(key): int(value) for key, value in sorted(rating_counts.items())},
        },
        "artifacts": {name: source(path) for name, path in (
            ("movie_bridge", bridge_path), ("bridge_audit", audit_path),
            ("ratings", ratings_path))},
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--movielens-zip", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--chunk-rows", type=int, default=1_000_000)
    parser.add_argument("--expected-ratings", type=int, default=32_000_204)
    args = parser.parse_args()
    result = build(args.movielens_zip, args.catalog, args.output_dir,
                   chunk_rows=args.chunk_rows, expected_ratings=args.expected_ratings)
    print(json.dumps(result["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
