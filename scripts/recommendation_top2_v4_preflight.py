from __future__ import annotations

import argparse
import json
import sys
import time
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from recommendation_protocol_v4 import (
    locked_movie_order,
    midrank_utilities,
    sha256_file,
    user_bucket,
    user_key,
    wilson_lower,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARCHIVE = Path(r"C:\higher\projects\MM\data\raw\ml-32m.zip")


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(payload))


def _member(archive: zipfile.ZipFile, suffix: str) -> str:
    matches = [name for name in archive.namelist() if name.endswith(suffix)]
    if len(matches) != 1:
        raise RuntimeError(f"expected one {suffix} member, found {matches}")
    return matches[0]


def _precompute_user_buckets(max_user_id: int = 300_000) -> np.ndarray:
    buckets = np.empty(max_user_id + 1, dtype=np.uint8)
    for user_id in range(max_user_id + 1):
        buckets[user_id] = user_bucket(user_id)
    return buckets


def _load_identity_allowlist(archive_path: Path) -> set[int]:
    with zipfile.ZipFile(archive_path) as archive:
        with archive.open(_member(archive, "links.csv")) as handle:
            links = pd.read_csv(handle, usecols=["movieId", "tmdbId"])
    numeric = pd.to_numeric(links["tmdbId"], errors="coerce")
    return set(links.loc[numeric.notna(), "movieId"].astype("int32").tolist())


def _load_validation_and_base_counts(
    archive_path: Path,
    bucket_lookup: np.ndarray,
    chunksize: int,
) -> tuple[pd.DataFrame, pd.Series, int, int]:
    validation_chunks: list[pd.DataFrame] = []
    base_counts: Counter[int] = Counter()
    scanned = 0
    started = time.time()
    with zipfile.ZipFile(archive_path) as archive:
        with archive.open(_member(archive, "ratings.csv")) as handle:
            reader = pd.read_csv(
                handle,
                dtype={"userId": "int32", "movieId": "int32", "rating": "float32", "timestamp": "int64"},
                chunksize=chunksize,
            )
            for index, chunk in enumerate(reader, start=1):
                user_ids = chunk["userId"].to_numpy(dtype=np.int64, copy=False)
                if user_ids.max(initial=0) >= len(bucket_lookup):
                    raise RuntimeError("user bucket lookup is too small")
                buckets = bucket_lookup[user_ids]
                base_mask = buckets <= 39
                validation_mask = (buckets >= 50) & (buckets <= 59)
                if base_mask.any():
                    counts = chunk.loc[base_mask, "movieId"].value_counts()
                    base_counts.update({int(key): int(value) for key, value in counts.items()})
                if validation_mask.any():
                    selected = chunk.loc[validation_mask, ["userId", "movieId", "rating", "timestamp"]].copy()
                    validation_chunks.append(selected)
                scanned += len(chunk)
                if index % 8 == 0:
                    elapsed = max(time.time() - started, 0.001)
                    print(f"ratings scan: {scanned:,} rows ({scanned / elapsed:,.0f} rows/s)", flush=True)
    validation = pd.concat(validation_chunks, ignore_index=True)
    count_series = pd.Series(base_counts, dtype="int64", name="base_train_interaction_count")
    count_series.index.name = "movie_id"
    return validation, count_series.sort_index(), scanned, int(sum(base_counts.values()))


def _history_bin(count: int) -> str:
    if count <= 9:
        return "0_9"
    if count <= 29:
        return "10_29"
    if count <= 99:
        return "30_99"
    if count <= 299:
        return "100_299"
    return "300_PLUS"


def build_preflight(
    protocol_path: Path,
    archive_path: Path,
    output_root: Path,
    role: str,
    chunksize: int,
) -> dict[str, Any]:
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    if role.lower() != "validation":
        raise ValueError("this runner is intentionally limited to Validation")
    expected_archive_sha = protocol["source"]["movielens_archive_sha256"]
    actual_archive_sha = sha256_file(archive_path)
    if actual_archive_sha != expected_archive_sha:
        raise RuntimeError("MovieLens archive checksum mismatch")
    output_root.mkdir(parents=True, exist_ok=True)

    bucket_lookup = _precompute_user_buckets()
    identity_allowlist = _load_identity_allowlist(archive_path)
    validation, base_counts, scanned_rows, base_rows = _load_validation_and_base_counts(
        archive_path, bucket_lookup, chunksize
    )
    duplicate_count = int(validation.duplicated(["userId", "movieId"]).sum())
    if duplicate_count:
        raise RuntimeError(f"duplicate user/movie rows found: {duplicate_count}")

    grouped = validation.groupby("userId", sort=True)
    seeds = [int(seed) for seed in protocol["randomization"]["seeds"]]
    fixed_k = [int(k) for k in protocol["natural_all"]["fixed_k"]]
    sensitivity_sizes = [int(size) for size in protocol["natural_all"]["preflight_slate_size_sensitivity"]]
    good_min = float(protocol["labels"]["good_midrank_utility_min"])
    bad_max = float(protocol["labels"]["bad_midrank_utility_max"])

    structural_users: dict[str, dict[str, int]] = {
        str(size): {str(k): 0 for k in fixed_k} for size in sensitivity_sizes
    }
    eligible_user_seed: dict[str, int] = {str(k): 0 for k in fixed_k}
    opportunity_user_seed: dict[str, dict[str, int]] = {
        str(k): {"bad": 0, "good": 0, "two_good": 0, "label_rich": 0} for k in fixed_k
    }
    nonnull_users: dict[str, set[str]] = {str(k): set() for k in fixed_k}
    full_catalog: dict[str, dict[str, int]] = {
        str(k): {
            "eligible_user_seed": 0,
            "all_known_good_zero": 0,
            "identity_known_good_zero": 0,
            "known_good_total": 0,
            "identity_known_good_total": 0,
        }
        for k in fixed_k
    }
    history_bins: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []
    started = time.time()

    for user_index, (raw_user_id, frame) in enumerate(grouped, start=1):
        raw_user_id = int(raw_user_id)
        movie_ids = frame["movieId"].astype(int).tolist()
        ratings = frame["rating"].astype(float).tolist()
        utilities = midrank_utilities(ratings)
        utility_by_movie = {movie_id: utility for movie_id, utility in zip(movie_ids, utilities)}
        history_count = len(movie_ids)
        key = user_key(raw_user_id)
        history_bins[_history_bin(history_count)] += 1
        for size in sensitivity_sizes:
            for k in fixed_k:
                if history_count >= size + k:
                    structural_users[str(size)][str(k)] += 1

        for seed in seeds:
            ordered = locked_movie_order(seed, raw_user_id, movie_ids)
            for size in sensitivity_sizes:
                slate = ordered[:size]
                slate_good = sum(utility_by_movie[movie_id] >= good_min for movie_id in slate)
                slate_bad = sum(utility_by_movie[movie_id] <= bad_max for movie_id in slate)
                rows.append(
                    {
                        "user_key": key,
                        "seed": seed,
                        "slate_size": size,
                        "history_count": history_count,
                        "slate_good_count": slate_good,
                        "slate_bad_count": slate_bad,
                        "max_structural_k": max(-1, history_count - size),
                    }
                )
                if size != 20:
                    continue
                reservoir = ordered[size:]
                all_good_movies = {movie_id for movie_id in movie_ids if utility_by_movie[movie_id] >= good_min}
                for k in fixed_k:
                    if history_count < size + k:
                        continue
                    eligible_user_seed[str(k)] += 1
                    opportunity_user_seed[str(k)]["bad"] += int(slate_bad >= 1)
                    opportunity_user_seed[str(k)]["good"] += int(slate_good >= 1)
                    opportunity_user_seed[str(k)]["two_good"] += int(slate_good >= 2)
                    opportunity_user_seed[str(k)]["label_rich"] += int(slate_good >= 2 and slate_bad >= 2)
                    if slate_good >= 1:
                        nonnull_users[str(k)].add(key)
                    model_input = set(reservoir[:k])
                    known_good = all_good_movies - model_input
                    identity_good = known_good.intersection(identity_allowlist)
                    fc = full_catalog[str(k)]
                    fc["eligible_user_seed"] += 1
                    fc["all_known_good_zero"] += int(not known_good)
                    fc["identity_known_good_zero"] += int(not identity_good)
                    fc["known_good_total"] += len(known_good)
                    fc["identity_known_good_total"] += len(identity_good)
        if user_index % 2_000 == 0:
            elapsed = max(time.time() - started, 0.001)
            print(f"slates: {user_index:,}/{grouped.ngroups:,} users ({user_index / elapsed:,.1f} users/s)", flush=True)

    sensitivity = pd.DataFrame(rows).sort_values(["slate_size", "user_key", "seed"])
    sensitivity_path = output_root / "slate-size-seed-sensitivity.parquet"
    sensitivity.to_parquet(sensitivity_path, index=False, compression="zstd")
    base_counts.reset_index().to_parquet(output_root / "base-train-item-counts.parquet", index=False, compression="zstd")

    user_count = int(grouped.ngroups)
    primary_eligibility: dict[str, dict[str, Any]] = {}
    for k in fixed_k:
        structural = structural_users["20"][str(k)]
        nonnull = len(nonnull_users[str(k)])
        primary_eligibility[str(k)] = {
            "structural_users": structural,
            "structural_user_seed_rows": eligible_user_seed[str(k)],
            "miss_nonnull_users": nonnull,
            "miss_nonnull_rate": nonnull / structural if structural else 0.0,
            "miss_nonnull_rate_wilson_l95": wilson_lower(nonnull, structural),
            "opportunity_user_seed": opportunity_user_seed[str(k)],
        }

    summary: dict[str, Any] = {
        "schema_version": 1,
        "evidence_id": "REC-EV-020P-A",
        "protocol_version": protocol["protocol_version"],
        "role": "VALIDATION",
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_checksums": {"movielens_archive_sha256": actual_archive_sha},
        "source_rows_scanned": scanned_rows,
        "base_train_rows_counted": base_rows,
        "validation_rating_rows": int(len(validation)),
        "validation_users": user_count,
        "raw_user_ids_stored": False,
        "duplicate_user_movie_rows": duplicate_count,
        "seed_count": len(seeds),
        "seeds": seeds,
        "slate_size_sensitivity": sensitivity_sizes,
        "history_bins": dict(sorted(history_bins.items())),
        "structural_users_by_slate_size_and_k": structural_users,
        "primary_slate_20_by_k": primary_eligibility,
        "full_catalog_label_coverage_by_k": full_catalog,
        "selection_bias_disclosure": (
            "NATURAL_ALL uses only structural eligibility. LABEL_RICH is reported as a diagnostic and is not a prevalence estimate."
        ),
        "locked_test_opened": False,
        "adoption_decision": "NO_PRODUCT_POLICY_CHANGE",
    }
    _write_json(output_root / "validation-cohort-summary.json", summary)

    paired_blocked = {
        "schema_version": 1,
        "evidence_id": "REC-EV-020P-B",
        "protocol_version": protocol["protocol_version"],
        "status": "BLOCKED_MISSING_LOCKED_BASELINE_CHALLENGER_ARTIFACTS",
        "reason": (
            "Cohort and opportunity denominators are ready, but paired power needs one baseline and one challenger prediction artifact locked before endpoint computation."
        ),
        "available_now": {
            "structural_and_nonnull_counts": True,
            "paired_delta_variance": False,
            "n_power_harm": None,
            "n_power_miss": None,
        },
        "locked_test_opened": False,
    }
    _write_json(output_root / "paired-power-analysis.json", paired_blocked)

    contract_path = REPO_ROOT / "docs/recommendation/contracts/rec-ev-020p-artifacts.json"
    protocol_lock = {
        "schema_version": 1,
        "protocol_version": protocol["protocol_version"],
        "protocol_sha256": sha256_file(protocol_path),
        "contract_sha256": sha256_file(contract_path) if contract_path.is_file() else None,
        "source_checksums": {"movielens_archive_sha256": actual_archive_sha},
        "created_before_model_test": True,
        "locked_test_opened": False,
    }
    _write_json(output_root / "protocol-lock.json", protocol_lock)
    return summary


def _build_manifest(output_root: Path, protocol_path: Path) -> dict[str, Any]:
    artifacts = []
    for path in sorted(output_root.glob("*")):
        if not path.is_file():
            continue
        artifacts.append(
            {
                "path": path.relative_to(REPO_ROOT).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    summary = json.loads((output_root / "validation-cohort-summary.json").read_text(encoding="utf-8"))
    return {
        "schema_version": 1,
        "evidence_id": "REC-EV-020P",
        "status": "PARTIAL_PASS_020P_A_020P_B_BLOCKED",
        "protocol": protocol_path.relative_to(REPO_ROOT).as_posix(),
        "protocol_sha256": sha256_file(protocol_path),
        "locked_test_opened": False,
        "artifacts": artifacts,
        "validation": {
            "cohort_preflight": summary["status"],
            "paired_power_preflight": "BLOCKED_MISSING_LOCKED_BASELINE_CHALLENGER_ARTIFACTS",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, default=REPO_ROOT / "docs/recommendation/protocols/rec-eval-top2-v4.json")
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--role", default="validation")
    parser.add_argument("--output-root", type=Path, default=REPO_ROOT / "outputs/recommendation-evidence/rec-ev-020p")
    parser.add_argument("--chunksize", type=int, default=1_000_000)
    args = parser.parse_args()
    try:
        build_preflight(args.protocol.resolve(), args.archive.resolve(), args.output_root.resolve(), args.role, args.chunksize)
        manifest = _build_manifest(args.output_root.resolve(), args.protocol.resolve())
        manifest_path = REPO_ROOT / "docs/recommendation/evidence/manifests/rec-ev-020p.json"
        _write_json(manifest_path, manifest)
        print(json.dumps({"status": manifest["status"], "manifest": str(manifest_path)}, ensure_ascii=False))
        return 0
    except Exception as error:
        print(f"preflight failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
