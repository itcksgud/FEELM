"""Reconstruct the exact REC-EV-019B six-field body for hybrid345.

Only catalogue metadata, prior encoder hashes, and pinned TMDB response caches
are opened.  Ratings and evaluation labels are intentionally absent from this
stage.  The output body removes E5's ``passage: `` prefix and otherwise keeps
the previous encoder input byte-for-byte.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from hybrid345_common import (DOC, OUT, ROOT, load_config, pin, read_json, require,
                              require_prelabel_review, source_path, write_json)


E5_PREFIX = "passage: "
DOCUMENT_COLUMNS = (
    "movie_id",
    "body",
    "body_sha256",
    "e5_input_sha256",
    "primary_cache_path",
    "english_cache_path",
)


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def nonblank(value: Any) -> str:
    if value is None:
        return ""
    candidate = str(value).strip()
    return candidate if candidate else ""


def first_nonblank(*values: Any) -> str:
    for value in values:
        candidate = nonblank(value)
        if candidate:
            return candidate
    return ""


def _safe_int(value: Any, default: int = 1_000_000) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def keyword_items(body: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    container = body.get("keywords") or {}
    require(isinstance(container, Mapping), "TMDB keywords container is not an object")
    values = container.get("keywords") or container.get("results") or []
    require(isinstance(values, list), "TMDB keywords are not a list")
    return values


def merge_text(primary: Mapping[str, Any], english: Mapping[str, Any] | None) -> dict[str, Any]:
    """Byte-compatible copy of REC-EV-019B's merge_text semantics."""
    english = english or {}
    genres = primary.get("genres") or []
    credits = primary.get("credits") or {}
    require(isinstance(genres, list) and isinstance(credits, Mapping), "invalid TMDB text fields")
    crew = credits.get("crew") or []
    cast = credits.get("cast") or []
    require(isinstance(crew, list) and isinstance(cast, list), "invalid TMDB credits fields")
    directors = [item for item in crew if item.get("job") == "Director"]
    cast = sorted(cast, key=lambda item: (_safe_int(item.get("order")), _safe_int(item.get("id"))))
    return {
        "display_title": first_nonblank(
            primary.get("title"),
            english.get("title"),
            primary.get("original_title"),
            english.get("original_title"),
        ),
        "overview_fallback": first_nonblank(primary.get("overview"), english.get("overview")),
        "genre_names": [nonblank(item.get("name")) for item in genres if nonblank(item.get("name"))],
        "director_names": [
            nonblank(item.get("name")) for item in directors if nonblank(item.get("name"))
        ],
        "top5_cast_names": [
            nonblank(item.get("name")) for item in cast[:5] if nonblank(item.get("name"))
        ],
        "keyword_names": [
            nonblank(item.get("name"))
            for item in keyword_items(primary)
            if nonblank(item.get("name"))
        ],
    }


def build_body(template: str, text: Mapping[str, Any]) -> str:
    body = template.format(
        display_title=text["display_title"],
        overview_fallback=text["overview_fallback"],
        genre_names=", ".join(text["genre_names"]),
        director_names=", ".join(text["director_names"]),
        top5_cast_names=", ".join(text["top5_cast_names"]),
        keyword_names=", ".join(text["keyword_names"]),
    )
    require(not body.startswith(E5_PREFIX), "Qwen document unexpectedly contains the E5 prefix")
    return body


def validate_cache_record(
    path: Path,
    *,
    tmdb_id: int,
    language: str,
    expected_body_sha256: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    record = read_json(path)
    expected_request = {
        "kind": "movie",
        "identity": str(int(tmdb_id)),
        "endpoint": f"/3/movie/{int(tmdb_id)}",
        "params": {"append_to_response": "credits,keywords", "language": language},
    }
    require(record.get("request") == expected_request, f"TMDB cache request mismatch: {path}")
    require(200 <= int(record.get("status", 0)) < 300, f"TMDB cache is not 2xx: {path}")
    body = record.get("body")
    require(isinstance(body, dict), f"TMDB cache body missing: {path}")
    require(_safe_int(body.get("id"), -1) == int(tmdb_id), f"TMDB cache identity mismatch: {path}")
    actual_body_sha = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    require(record.get("body_sha256") == actual_body_sha, f"TMDB cache body hash mismatch: {path}")
    if expected_body_sha256 is not None:
        require(actual_body_sha == expected_body_sha256, f"TMDB metadata body hash mismatch: {path}")
    return record, body


def _fallback_candidates(primary_path: Path, tmdb_id: int, root: Path) -> list[Path]:
    filename = f"movie-{int(tmdb_id)}-en_US.json"
    candidates = [primary_path.with_name(filename)]
    candidates.extend(
        [
            root / "outputs/recommendation-evidence/rec-ev-027-catalog/tmdb-cache" / filename,
            root / "outputs/recommendation-evidence/rec-ev-019b/tmdb-cache" / filename,
        ]
    )
    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def reconstruct_document(
    metadata: Mapping[str, Any],
    expected_e5_sha256: str,
    template: str,
    *,
    root: Path = ROOT,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    movie_id = int(metadata["movie_id"])
    tmdb_id = int(metadata["tmdb_id"])
    raw_path = Path(str(metadata["cache_path"]))
    primary_path = (raw_path if raw_path.is_absolute() else root / raw_path).resolve()
    require(primary_path.is_relative_to(root.resolve()), f"primary cache escapes repository: {movie_id}")
    primary_record, primary = validate_cache_record(
        primary_path,
        tmdb_id=tmdb_id,
        language="ko-KR",
        expected_body_sha256=str(metadata["response_sha256"]),
    )
    cache_pins = [
        {
            "path": primary_path.relative_to(root.resolve()).as_posix(),
            **pin(primary_path),
            "body_sha256": primary_record["body_sha256"],
            "language": "ko-KR",
        }
    ]

    attempts: list[tuple[Path | None, Mapping[str, Any] | None]] = [(None, None)]
    if not nonblank(primary.get("title")) or not nonblank(primary.get("overview")):
        for candidate in _fallback_candidates(primary_path, tmdb_id, root):
            if not candidate.is_file():
                continue
            try:
                english_record, english = validate_cache_record(
                    candidate, tmdb_id=tmdb_id, language="en-US"
                )
            except (RuntimeError, ValueError, TypeError, KeyError):
                # Multiple historical cache roots may contain a file with this
                # name.  Only a fully validated candidate that reproduces the
                # frozen E5 input hash is a dependency of the selected body.
                continue
            primary_imdb = nonblank(primary.get("imdb_id"))
            english_imdb = nonblank(english.get("imdb_id"))
            require(
                not primary_imdb or not english_imdb or primary_imdb == english_imdb,
                f"English fallback IMDb mismatch: {movie_id}",
            )
            attempts.append((candidate, english))
            cache_pins.append(
                {
                    "path": candidate.relative_to(root.resolve()).as_posix(),
                    **pin(candidate),
                    "body_sha256": english_record["body_sha256"],
                    "language": "en-US",
                }
            )

    matches: list[tuple[str, Path | None]] = []
    for english_path, english in attempts:
        text = merge_text(primary, english)
        body = build_body(template, text)
        if sha256_text(E5_PREFIX + body) == expected_e5_sha256:
            matches.append((body, english_path))
    require(matches, f"no exact REC-EV-019B input hash match for movie_id={movie_id}")
    distinct_bodies = {body for body, _path in matches}
    require(len(distinct_bodies) == 1, f"ambiguous REC-EV-019B body for movie_id={movie_id}")
    body, english_path = matches[0]
    row = {
        "movie_id": movie_id,
        "body": body,
        "body_sha256": sha256_text(body),
        "e5_input_sha256": str(expected_e5_sha256),
        "primary_cache_path": primary_path.relative_to(root.resolve()).as_posix(),
        "english_cache_path": (
            english_path.relative_to(root.resolve()).as_posix() if english_path is not None else None
        ),
    }
    # Only sources that actually determined the selected document belong to the
    # dependency ledger.  Discard other valid-but-unselected fallback candidates.
    selected_paths = {row["primary_cache_path"], row["english_cache_path"]}
    return row, [entry for entry in cache_pins if entry["path"] in selected_paths]


def validate_catalog_axis(
    catalog: pd.DataFrame, metadata: pd.DataFrame, e5: pd.DataFrame, expected_movies: int
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    for name, frame in (("catalog", catalog), ("metadata", metadata), ("E5 hashes", e5)):
        require("movie_id" in frame, f"{name} lacks movie_id")
        require(not frame["movie_id"].duplicated().any(), f"{name} has duplicate movie_id")
    catalog = catalog.sort_values("movie_id", kind="mergesort", ignore_index=True)
    ids = catalog["movie_id"].astype("int64").to_numpy()
    require(len(ids) == expected_movies, f"catalog count drift: {len(ids)} != {expected_movies}")
    require(len(ids) == len(set(ids.tolist())), "catalog movie_id is not unique")
    require((ids[1:] > ids[:-1]).all() if len(ids) > 1 else True, "catalog axis is not increasing")
    metadata = metadata.set_index("movie_id").reindex(ids)
    e5 = e5.set_index("movie_id").reindex(ids)
    require(not metadata.isna().all(axis=1).any(), "metadata does not cover the catalogue axis")
    require(not e5.isna().all(axis=1).any(), "E5 hashes do not cover the catalogue axis")
    require(e5["input_text_sha256"].map(lambda value: isinstance(value, str) and len(value) == 64).all(),
            "invalid E5 input hash")
    return catalog, metadata.reset_index(), e5.reset_index()


def _document_schema() -> pa.Schema:
    return pa.schema(
        [
            ("movie_id", pa.int64()),
            ("body", pa.string()),
            ("body_sha256", pa.string()),
            ("e5_input_sha256", pa.string()),
            ("primary_cache_path", pa.string()),
            ("english_cache_path", pa.string()),
        ]
    )


def _atomic_parquet_batches(
    destination: Path, batches: Iterable[list[dict[str, Any]]], schema: pa.Schema
) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    require(not temporary.exists(), f"stale parquet staging file: {temporary}")
    rows = 0
    writer: pq.ParquetWriter | None = None
    try:
        writer = pq.ParquetWriter(temporary, schema=schema, compression="zstd")
        for batch in batches:
            if not batch:
                continue
            writer.write_table(pa.Table.from_pylist(batch, schema=schema))
            rows += len(batch)
        writer.close()
        writer = None
        os.replace(temporary, destination)
    finally:
        if writer is not None:
            writer.close()
        if temporary.exists():
            temporary.unlink()
    return rows


def _relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def _verify_configured_source(name: str, cfg: Mapping[str, Any]) -> Path:
    path = source_path(name)
    relative = _relative(path)
    expected = cfg.get("source_pins", {}).get(relative)
    require(expected is not None, f"hybrid345 config lacks source pin: {relative}")
    require(pin(path) == expected, f"configured source pin drift: {relative}")
    return path


def verify_rec027_lineage(cfg: Mapping[str, Any], catalog_ids: Any) -> dict[str, Any]:
    embedding_path = _verify_configured_source("rec027_catalog_embeddings", cfg)
    manifest_path = _verify_configured_source("rec027_catalog_manifest", cfg)
    summary_path = _verify_configured_source("rec027_catalog_summary", cfg)
    universe_path = _verify_configured_source("rec027_universe", cfg)
    integrity_path = _verify_configured_source("rec027_integrity", cfg)
    manifest, summary, integrity = map(read_json, (manifest_path, summary_path, integrity_path))
    require(
        manifest.get("status") == summary.get("status") == "PASS_RATING_INDEPENDENT_FULL_CATALOG_BUILD",
        "REC027 catalogue build is not sealed PASS",
    )
    require(manifest.get("ratings_member_opened") is False, "REC027 manifest opened ratings")
    require(
        summary.get("rating_values_opened") is False
        and summary.get("ratings_member_opened") is False
        and summary.get("locked_test_opened") is False,
        "REC027 catalogue summary crossed the label-free boundary",
    )
    artifacts = {row["path"]: {"bytes": int(row["bytes"]), "sha256": row["sha256"]}
                 for row in manifest.get("artifacts", [])}
    require(artifacts.get(_relative(embedding_path)) == pin(embedding_path),
            "REC027 manifest does not bind the E5 hash authority")
    require(artifacts.get(_relative(summary_path)) == pin(summary_path),
            "REC027 manifest does not bind its summary")
    require(integrity.get("status") == "SEALED_SCREEN_PREPARED_WITHOUT_TARGET_LABELS",
            "REC027 common universe integrity status")
    require(int(integrity.get("metadata", {}).get("common_items", -1)) == int(cfg["catalog_movies"]),
            "REC027 common universe count drift")
    for artifact_name, source_name in (("universe", "rec027_universe"), ("e5", "e5")):
        record = integrity.get("artifacts", {}).get(artifact_name, {})
        expected_path = _verify_configured_source(source_name, cfg)
        require(record.get("path") == _relative(expected_path), f"REC027 integrity {artifact_name} path drift")
        require({key: record.get(key) for key in ("bytes", "sha256")} == pin(expected_path),
                f"REC027 integrity {artifact_name} pin drift")
    with np.load(universe_path, allow_pickle=False) as universe:
        require("item_ids" in universe.files, "REC027 universe lacks item_ids")
        require(np.array_equal(universe["item_ids"].astype(np.int64), np.asarray(catalog_ids, dtype=np.int64)),
                "REC027 universe and hybrid345 catalogue axes differ")
    embedding_stats = summary.get("embeddings", {})
    require(
        int(embedding_stats.get("reused_by_rebuilt_input_hash", -1)) == 68_674
        and int(embedding_stats.get("encoded", -1)) == 17_782,
        "REC027 embedding lineage accounting drift",
    )
    return {
        "identity_eligible": int(summary["identity_eligible"]),
        "text_eligible": int(summary["text_eligible"]),
        "rec019b_hashes_reused": int(embedding_stats["reused_by_rebuilt_input_hash"]),
        "rec027_newly_encoded": int(embedding_stats["encoded"]),
        "net_row_growth_from_68674_to_85517": int(cfg["catalog_movies"]) - 68_674,
        "net_growth_is_not_a_set_difference": True,
    }


def _document_implementation() -> dict[str, dict[str, Any]]:
    return {
        "scripts/hybrid345_common.py": pin(ROOT / "scripts/hybrid345_common.py"),
        "scripts/hybrid345_documents.py": pin(Path(__file__)),
        "docs/recommendation/experiments/hybrid345/config.json": pin(DOC / "config.json"),
    }


def verify_documents_seal() -> dict[str, Any]:
    cfg = load_config()
    seal_path = OUT / "documents-seal.json"
    seal = read_json(seal_path)
    require(seal.get("status") == "PASS_LABEL_FREE_INPUTS_SEALED", "document seal status")
    require(int(seal.get("movies", -1)) == int(cfg["catalog_movies"]), "document seal count")
    require(seal.get("implementation") == _document_implementation(),
            "document implementation/config drift")
    for relative, expected in seal.get("source_pins", {}).items():
        require(pin(ROOT / relative) == expected, f"document source drift: {relative}")
        require(cfg.get("source_pins", {}).get(relative) == expected,
                f"document source no longer configured: {relative}")
    for name, expected in seal.get("artifacts", {}).items():
        require(pin(OUT / name) == expected, f"sealed document artifact drift: {name}")
    return seal


def build_documents(*, chunk_size: int = 1000) -> dict[str, Any]:
    require(chunk_size > 0, "chunk_size must be positive")
    require_prelabel_review()
    cfg = load_config()
    qwen = cfg["qwen"]
    require(qwen["input_prefix"] == "" and qwen["query_instruction"] is None,
            "Qwen document prefix/instruction drift")
    require(qwen["input_template"] == cfg["qwen"]["input_template"], "input template drift")
    OUT.mkdir(parents=True, exist_ok=True)
    seal_path = OUT / "documents-seal.json"
    document_path = OUT / "documents.parquet"
    ledger_path = OUT / "document-cache-ledger.parquet"
    if seal_path.exists():
        return verify_documents_seal()
    require(not document_path.exists() and not ledger_path.exists(),
            "unsealed hybrid345 document artifacts already exist")

    started = time.perf_counter()
    source_names = (
        "catalog", "rec033_metadata", "rec033_cache_ledger", "rec027_catalog_embeddings",
        "rec027_catalog_manifest", "rec027_catalog_summary", "rec027_universe", "rec027_integrity",
        "e5",
    )
    source_files = {name: _verify_configured_source(name, cfg) for name in source_names}
    source_pins = {_relative(path): pin(path) for path in source_files.values()}
    e5_path = source_files["rec027_catalog_embeddings"]

    catalog = pd.read_parquet(source_path("catalog"), columns=["movie_id"])
    metadata = pd.read_parquet(
        source_path("rec033_metadata"),
        columns=["movie_id", "tmdb_id", "cache_path", "response_sha256"],
    )
    e5 = pd.read_parquet(e5_path, columns=["movie_id", "input_text_sha256", "feature_eligible"])
    catalog, metadata, e5 = validate_catalog_axis(
        catalog, metadata, e5, int(cfg["catalog_movies"])
    )
    require(e5["feature_eligible"].astype(bool).all(),
            "the fixed 85,517 catalogue contains an E5-ineligible movie")
    rec027_lineage = verify_rec027_lineage(cfg, catalog["movie_id"].to_numpy(dtype=np.int64))
    old_ledger = pd.read_parquet(source_path("rec033_cache_ledger"))
    require(set(("path", "bytes", "sha256", "body_sha256")).issubset(old_ledger.columns),
            "REC033 cache ledger schema drift")
    require(not old_ledger["path"].duplicated().any(), "REC033 cache ledger has duplicate paths")
    old_lookup = old_ledger.set_index("path").to_dict("index")

    selected_cache: dict[str, dict[str, Any]] = {}
    movie_cache_source_counts = {"rec-ev-019b": 0, "rec-ev-027-catalog": 0, "other": 0}
    english_fallback_movies = 0
    movie_rows = metadata.to_dict("records")
    expected_hashes = e5["input_text_sha256"].tolist()

    def batches() -> Iterator[list[dict[str, Any]]]:
        nonlocal english_fallback_movies
        for start in range(0, len(movie_rows), chunk_size):
            output: list[dict[str, Any]] = []
            for row, expected_sha in zip(
                movie_rows[start : start + chunk_size],
                expected_hashes[start : start + chunk_size],
            ):
                primary_key = str(row["cache_path"])
                require(primary_key in old_lookup, f"primary cache absent from REC033 ledger: {primary_key}")
                expected_pin = old_lookup[primary_key]
                primary = (ROOT / primary_key).resolve()
                require(pin(primary) == {"bytes": int(expected_pin["bytes"]), "sha256": expected_pin["sha256"]},
                        f"REC033 primary cache pin drift: {primary_key}")
                require(str(expected_pin["body_sha256"]) == str(row["response_sha256"]),
                        f"REC033 primary body pin drift: {primary_key}")
                document, cache_entries = reconstruct_document(
                    row, str(expected_sha), str(qwen["input_template"]), root=ROOT
                )
                primary_parts = Path(document["primary_cache_path"]).parts
                if "rec-ev-019b" in primary_parts:
                    movie_cache_source_counts["rec-ev-019b"] += 1
                elif "rec-ev-027-catalog" in primary_parts:
                    movie_cache_source_counts["rec-ev-027-catalog"] += 1
                else:
                    movie_cache_source_counts["other"] += 1
                english_fallback_movies += int(document["english_cache_path"] is not None)
                require(
                    sha256_text(E5_PREFIX + document["body"]) == document["e5_input_sha256"],
                    f"E5 exact-input verification failed: {document['movie_id']}",
                )
                for entry in cache_entries:
                    previous = selected_cache.get(entry["path"])
                    require(previous is None or previous == entry,
                            f"cache changed during document reconstruction: {entry['path']}")
                    selected_cache[entry["path"]] = entry
                output.append(document)
            print(f"DOCUMENTS {min(start + chunk_size, len(movie_rows))}/{len(movie_rows)}", flush=True)
            yield output

    count = _atomic_parquet_batches(document_path, batches(), _document_schema())
    require(count == int(cfg["catalog_movies"]), "document output count drift")
    ledger_rows = [selected_cache[key] for key in sorted(selected_cache)]
    _atomic_parquet_batches(ledger_path, [ledger_rows], pa.schema([
        ("path", pa.string()), ("bytes", pa.int64()), ("sha256", pa.string()),
        ("body_sha256", pa.string()), ("language", pa.string()),
    ]))

    built = pd.read_parquet(document_path, columns=["movie_id", "body_sha256", "e5_input_sha256"])
    require(built["movie_id"].astype("int64").tolist() == catalog["movie_id"].astype("int64").tolist(),
            "document movie axis changed while writing")
    require((built["body_sha256"].str.len() == 64).all(), "invalid body hashes")
    require((built["e5_input_sha256"] == e5["input_text_sha256"]).all(),
            "not all REC-EV-027 input hashes match")
    artifacts = {
        "documents.parquet": pin(document_path),
        "document-cache-ledger.parquet": pin(ledger_path),
    }
    seal = {
        "schema_version": 1,
        "status": "PASS_LABEL_FREE_INPUTS_SEALED",
        "movies": count,
        "movie_id_order": "STRICT_ASCENDING",
        "body_prefix": "",
        "e5_verification": {
            "prefix": E5_PREFIX,
            "matches": count,
            "expected": int(cfg["catalog_movies"]),
        },
        "source_pins": source_pins,
        "artifacts": artifacts,
        "cache_files_used": len(selected_cache),
        "catalog_expansion_provenance": {
            "prior_rec019b_primary_cache_movies": movie_cache_source_counts["rec-ev-019b"],
            "rec027_expansion_primary_cache_movies": movie_cache_source_counts["rec-ev-027-catalog"],
            "other_primary_cache_movies": movie_cache_source_counts["other"],
            "current_movies_with_rec027_primary_cache": movie_cache_source_counts["rec-ev-027-catalog"],
            "rec027_build_embedding_accounting": rec027_lineage,
            "how_expansion_gets_content": (
                "REC-EV-027 re-ran the same identity validation, ko-KR/en-US fallback, and six-field "
                "REC-EV-019B body contract for the rating-independent full MovieLens catalogue; "
                "every selected body is accepted only by its stored E5 input SHA256. The reported "
                "16,843 is net row-count growth (85,517 - 68,674), not an exact movie-set "
                "difference or an English-fallback count."
            ),
            "english_text_fallback_movies": english_fallback_movies,
        },
        "seconds": time.perf_counter() - started,
        "ratings_opened": False,
        "labels_opened": False,
        "network_requests": 0,
        "implementation": _document_implementation(),
    }
    write_json(seal_path, seal)
    print("PASS_LABEL_FREE_INPUTS_SEALED", json.dumps({"movies": count}), flush=True)
    return seal


def main() -> None:
    parser = argparse.ArgumentParser(description="Build exact label-free hybrid345 Qwen documents")
    parser.add_argument("--chunk-size", type=int, default=1000)
    args = parser.parse_args()
    build_documents(chunk_size=args.chunk_size)


if __name__ == "__main__":
    main()
