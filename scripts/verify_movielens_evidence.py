#!/usr/bin/env python3
"""Independently verify REC-EV-001 manifest, artifact checksums, rows, and time order."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def timestamp_bounds(path: Path) -> tuple[int, int]:
    parquet = pq.ParquetFile(path)
    column_index = parquet.schema_arrow.names.index("timestamp")
    minimum: int | None = None
    maximum: int | None = None
    for index in range(parquet.metadata.num_row_groups):
        statistics = parquet.metadata.row_group(index).column(column_index).statistics
        if statistics is None or not statistics.has_min_max:
            raise RuntimeError(f"timestamp statistics missing: {path}, row group {index}")
        value_min = int(statistics.min)
        value_max = int(statistics.max)
        minimum = value_min if minimum is None else min(minimum, value_min)
        maximum = value_max if maximum is None else max(maximum, value_max)
    if minimum is None or maximum is None:
        raise RuntimeError(f"empty parquet artifact: {path}")
    return minimum, maximum


def artifact_path(item: dict[str, Any], manifest_path: Path) -> Path:
    path = Path(item["path"])
    if path.is_absolute():
        return path
    repository_root = manifest_path.resolve().parents[4]
    return repository_root / path


def main() -> int:
    args = parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    errors: list[str] = []
    bounds: dict[str, tuple[int, int]] = {}
    row_sum = 0

    source = Path(manifest["source"]["archive"])
    if not source.exists():
        errors.append(f"source archive missing: {source}")
    elif sha256_file(source) != manifest["source"]["archive_sha256"]:
        errors.append("source archive checksum mismatch")

    for name in ("train", "validation", "test", "user_rating_profiles"):
        item = manifest["artifacts"][name]
        path = artifact_path(item, args.manifest)
        if not path.exists():
            errors.append(f"artifact missing: {path}")
            continue
        if path.stat().st_size != item["bytes"]:
            errors.append(f"artifact byte size mismatch: {name}")
        if sha256_file(path) != item["sha256"]:
            errors.append(f"artifact checksum mismatch: {name}")
        parquet = pq.ParquetFile(path)
        if name != "user_rating_profiles":
            expected_rows = manifest["splits"][name]["rows"]
            if parquet.metadata.num_rows != expected_rows:
                errors.append(f"artifact row mismatch: {name}")
            row_sum += parquet.metadata.num_rows
            bounds[name] = timestamp_bounds(path)
        elif parquet.metadata.num_rows != manifest["rating_style"]["train_users"]:
            errors.append("user profile row mismatch")

    if row_sum != manifest["source"]["rating_rows"]:
        errors.append("split row sum mismatch")
    if bounds:
        if bounds["train"][1] >= bounds["validation"][0]:
            errors.append("train/validation time overlap")
        if bounds["validation"][1] >= bounds["test"][0]:
            errors.append("validation/test time overlap")

    if errors:
        print("REC-EV-001 verification failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print(
        "REC-EV-001 verification passed: "
        f"{row_sum:,} rows, {manifest['rating_style']['train_users']:,} train profiles, "
        "checksums and strict time order valid."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
