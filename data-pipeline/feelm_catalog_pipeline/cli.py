from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from .pipeline import CatalogPipeline, PipelineConfig
from .tmdb import NetworkTmdbGateway
from .transport import FileCachedJsonTransport, RetryingJsonTransport, UrlLibRawJsonTransport


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="feelm-catalog")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="Build ADR-0006 JSONL artifact")
    build.add_argument("--archive", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--quality-report", type=Path, required=True)
    build.add_argument("--catalog-version", required=True)
    build.add_argument("--identity-map", type=Path)
    build.add_argument("--identity-map-output", type=Path)
    build.add_argument("--previous-quality-report", type=Path)
    build.add_argument("--cache-dir", type=Path)
    build.add_argument("--env-file", type=Path, default=Path("../.env.local"))
    build.add_argument("--workers", type=int, default=4)
    build.add_argument("--limit", type=int, default=0)
    build.add_argument("--title-similarity-threshold", type=float, default=0.65)
    build.add_argument("--generated-at", help="Timezone-aware ISO-8601 instant")
    build.add_argument("--no-cache", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _load_env_file(args.env_file)
    token = os.environ.get("TMDB_READ_ACCESS_TOKEN", "").strip()
    if not token:
        print("TMDB_READ_ACCESS_TOKEN is missing", file=sys.stderr)
        return 2
    if not args.archive.exists():
        print(f"MovieLens archive not found: {args.archive}", file=sys.stderr)
        return 2
    transport = RetryingJsonTransport(UrlLibRawJsonTransport(token))
    if not args.no_cache:
        cache_dir = args.cache_dir or (args.output.parent / ".tmdb-cache")
        transport = FileCachedJsonTransport(transport, cache_dir)  # type: ignore[assignment]
    gateway = NetworkTmdbGateway(transport)
    generated_at = datetime.fromisoformat(args.generated_at) if args.generated_at else None
    config = PipelineConfig(
        archive=args.archive,
        output=args.output,
        quality_report=args.quality_report,
        catalog_version=args.catalog_version,
        identity_map_input=args.identity_map,
        identity_map_output=args.identity_map_output,
        previous_quality_report=args.previous_quality_report,
        generated_at=generated_at,
        workers=args.workers,
        limit=args.limit,
        title_similarity_threshold=args.title_similarity_threshold,
    )
    try:
        result = CatalogPipeline(gateway).run(config)
    except Exception as error:
        print(f"Catalog build failed: {error}", file=sys.stderr)
        return 1
    print(
        f"Catalog artifact built: movies={result.input_count}, "
        f"publishGatesPassed={str(result.all_publish_gates_passed).lower()}"
    )
    print(f"Artifact: {result.artifact_path}")
    print(f"Quality report: {result.quality_report_path}")
    print(f"Identity map: {result.identity_map_path}")
    return 0 if result.all_publish_gates_passed else 3

