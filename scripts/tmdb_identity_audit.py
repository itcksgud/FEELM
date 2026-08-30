#!/usr/bin/env python3
"""Audit missing or stale MovieLens-to-TMDB links through IMDb IDs."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import urllib.parse
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tmdb_coverage_audit import (
    TMDB_BASE_URL,
    csv_rows,
    load_env_file,
    request_json,
    zip_member,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--errors", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=Path(".env.local"))
    parser.add_argument("--workers", type=int, default=8)
    return parser.parse_args()


def imdb_key(raw_id: str) -> str:
    return "tt" + raw_id.strip().zfill(7)


def main() -> int:
    args = parse_args()
    load_env_file(args.env_file)
    token = os.environ.get("TMDB_READ_ACCESS_TOKEN", "").strip()
    if not token:
        raise SystemExit("TMDB_READ_ACCESS_TOKEN is missing")

    with zipfile.ZipFile(args.archive) as archive:
        titles = {
            int(row["movieId"]): row["title"]
            for row in csv_rows(archive, zip_member(archive, "movies.csv"))
        }
        links = {
            int(row["movieId"]): {
                "imdb_id": row.get("imdbId", "").strip(),
                "tmdb_id": row.get("tmdbId", "").strip(),
            }
            for row in csv_rows(archive, zip_member(archive, "links.csv"))
        }

    targets: list[dict[str, Any]] = []
    for movie_id, link in links.items():
        if not link["tmdb_id"]:
            targets.append(
                {
                    "reason": "missing_tmdb_id",
                    "movie_id": movie_id,
                    "movielens_title": titles[movie_id],
                    "old_tmdb_id": None,
                    "imdb_id": imdb_key(link["imdb_id"]),
                }
            )

    if args.errors and args.errors.exists():
        stale_rows = json.loads(args.errors.read_text(encoding="utf-8"))
        existing = {(item["reason"], item["movie_id"]) for item in targets}
        for row in stale_rows:
            movie_id = int(row["movie_id"])
            key = ("stale_tmdb_id", movie_id)
            if key in existing:
                continue
            targets.append(
                {
                    "reason": "stale_tmdb_id",
                    "movie_id": movie_id,
                    "movielens_title": titles[movie_id],
                    "old_tmdb_id": row.get("tmdb_id"),
                    "imdb_id": imdb_key(links[movie_id]["imdb_id"]),
                }
            )

    def fetch(target: dict[str, Any]) -> dict[str, Any]:
        url = (
            f"{TMDB_BASE_URL}/find/{target['imdb_id']}?"
            + urllib.parse.urlencode({"external_source": "imdb_id"})
        )
        try:
            payload = request_json(url, token)
            movie_results = payload.get("movie_results") or []
            tv_results = payload.get("tv_results") or []
            kind = "movie" if movie_results else ("tv" if tv_results else "none")
            hit = (movie_results or tv_results or [{}])[0]
            return {
                **target,
                "result_kind": kind,
                "matched_tmdb_id": hit.get("id"),
                "matched_title": hit.get("title") or hit.get("name"),
                "movie_result_count": len(movie_results),
                "tv_result_count": len(tv_results),
            }
        except Exception as error:
            return {**target, "result_kind": "error", "error": str(error)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        rows = list(executor.map(fetch, targets))
    rows.sort(key=lambda row: (str(row["reason"]), int(row["movie_id"])))

    summary: dict[str, Any] = {}
    for reason in sorted({str(row["reason"]) for row in rows}):
        reason_rows = [row for row in rows if row["reason"] == reason]
        summary[reason] = {
            "count": len(reason_rows),
            "result_kinds": dict(Counter(row["result_kind"] for row in reason_rows)),
        }

    artifact = {
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_archive": str(args.archive),
        "summary": summary,
        "items": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=True))
    print(f"Identity audit written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
