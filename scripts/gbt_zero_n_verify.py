"""Verify a completed S15P21E106-622 GBT validation run without opening FINAL_TEST."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pyarrow.compute as pc
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "experiments" / "gbt-zero-n" / "config.json"
RUNTIME_SOURCE_FILES = (
    "experiments/gbt-zero-n/config.json",
    "scripts/gbt_zero_n_build_input.py",
    "scripts/gbt_zero_n_evaluate.py",
    "scripts/gbt_zero_n_features.py",
    "scripts/gbt_zero_n_prepare.py",
    "scripts/gbt_zero_n_run.py",
    "scripts/gbt_zero_n_worker.py",
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition: bool, message: str, checks: list[str]) -> None:
    if not condition:
        raise RuntimeError(message)
    checks.append(message)


def verify_input_semantics(input_root: Path, movielens_root: Path, manifest: dict) -> dict:
    cutoffs = manifest["cutoffs"]
    users_by_role: dict[str, set[int]] = defaultdict(set)
    user_role: dict[int, str] = {}
    episode_pools: dict[tuple[str, int, int], tuple[str, str]] = {}
    episode_rows = 0
    nested_groups = 0
    current_key = None
    current_histories: list[tuple[str, ...]] = []
    current_pool_digest = None

    def finish_nested() -> None:
        nonlocal nested_groups
        if not current_histories:
            return
        for previous, following in zip(current_histories, current_histories[1:]):
            if previous != following[:len(previous)]:
                raise RuntimeError(f"history is not nested prefix: {current_key}")
        nested_groups += 1

    with (input_root / "episodes.jsonl").open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            episode_rows += 1
            role = row["role"]
            uid = int(row["uid"])
            previous_role = user_role.setdefault(uid, role)
            if previous_role != role:
                raise RuntimeError(f"user appears in multiple roles: {uid}")
            users_by_role[role].add(uid)
            window = cutoffs[role]
            if int(row["prediction_at"]) != int(window["feature_cutoff_inclusive"]) + 1:
                raise RuntimeError("prediction_at does not follow feature cutoff")
            if int(row["catalog_snapshot_at"]) > int(row["prediction_at"]):
                raise RuntimeError("catalog snapshot is after prediction")
            if not (int(window["target_start_exclusive"]) < int(row["target_event_at"]) <=
                    int(window["target_end_inclusive"])):
                raise RuntimeError("target event falls outside its role window")
            if not row["target_eligible"]:
                raise RuntimeError("target is not eligible")
            history = row["history"]
            event_order = [(int(item["event_at"]), str(item["event_id"])) for item in history]
            if event_order != sorted(event_order) or len({item["event_id"] for item in history}) != len(history):
                raise RuntimeError("history order or event ID uniqueness failed")
            if any(int(item["event_at"]) >= int(row["prediction_at"]) for item in history):
                raise RuntimeError("history contains a non-past event")
            if any(int(item["movie_id"]) == int(row["target_movie_id"]) for item in history):
                raise RuntimeError("target movie leaked into history")
            if int(row["supported_history_count"]) != len(history):
                raise RuntimeError("supported history count mismatch")
            if len(history) > int(row["total_history_count"]):
                raise RuntimeError("supported history exceeds total history")
            key = (role, uid, int(row["target_movie_id"]), int(row["prediction_at"]))
            if key != current_key:
                finish_nested()
                current_key = key
                current_histories = []
                current_pool_digest = row["candidate_digest"]
            elif current_pool_digest != row["candidate_digest"]:
                raise RuntimeError("candidate pool changed inside a nested-history group")
            current_histories.append(tuple(str(item["event_id"]) for item in history))
            pool_key = (role, uid, int(row["target_movie_id"]))
            episode_pools[pool_key] = (row["candidate_digest"], row["eligible_population_digest"])
    finish_nested()

    if episode_rows != int(manifest["episodes"]["rows"]):
        raise RuntimeError("episode row count mismatch")
    for role, users in users_by_role.items():
        digest = hashlib.sha256(",".join(map(str, sorted(users))).encode()).hexdigest()
        if digest != manifest["user_partition"]["role_user_digests"][role]:
            raise RuntimeError(f"role user digest mismatch: {role}")

    release_years: dict[int, int | None] = {}
    with (movielens_root / "movies.csv").open("r", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            match = re.search(r"\((\d{4})\)\s*$", row["title"])
            release_years[int(row["movieId"])] = int(match.group(1)) if match else None

    probability = float(manifest["unknown_sampling"]["probability"])
    candidate_rows = 0
    candidate_groups = 0
    seen_groups: set[tuple[str, int, int]] = set()
    group_key = None
    group_ids: list[int] = []
    group_target_seen = False
    expected_rank = 0
    group_digest = None
    group_eligible_digest = None

    def finish_candidates() -> None:
        nonlocal candidate_groups
        if group_key is None:
            return
        actual = hashlib.sha256(",".join(map(str, group_ids)).encode()).hexdigest()
        if actual != group_digest or not group_target_seen:
            raise RuntimeError(f"candidate digest or target membership failed: {group_key}")
        candidate_groups += 1
        seen_groups.add(group_key)

    with (input_root / "candidates.jsonl").open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            candidate_rows += 1
            key = (row["role"], int(row["uid"]), int(row["target_movie_id"]))
            if key != group_key:
                finish_candidates()
                if key not in episode_pools:
                    raise RuntimeError(f"candidate group has no episode: {key}")
                group_key = key
                group_ids = []
                group_target_seen = False
                expected_rank = 0
                group_digest, group_eligible_digest = episode_pools[key]
            if int(row["candidate_rank"]) != expected_rank:
                raise RuntimeError(f"candidate ranks are not contiguous: {key}")
            expected_rank += 1
            movie_id = int(row["candidate_movie_id"])
            group_ids.append(movie_id)
            group_target_seen |= movie_id == key[2]
            if row["eligible_population_digest"] != group_eligible_digest:
                raise RuntimeError("eligible population digest changed inside candidate group")
            year = release_years.get(movie_id)
            prediction_year = datetime.fromtimestamp(int(row["prediction_at"]), timezone.utc).year
            if year is None or year > prediction_year:
                raise RuntimeError(f"candidate violates as-of catalog: {key}, {movie_id}")
            if row["label_state"] == "UNKNOWN_SAMPLED":
                if row["observed_rating"] is not None:
                    raise RuntimeError("UNKNOWN_SAMPLED contains a rating")
                if abs(float(row["sampling_probability"]) - probability) > 1e-15:
                    raise RuntimeError("UNKNOWN sampling probability mismatch")
                if abs(float(row["importance_weight"]) - 1.0 / probability) > 1e-9:
                    raise RuntimeError("UNKNOWN importance weight mismatch")
            else:
                if row["observed_rating"] is None or row["sampling_probability"] is not None or \
                        row["importance_weight"] is not None:
                    raise RuntimeError("observed candidate label metadata mismatch")
    finish_candidates()
    if candidate_rows != int(manifest["candidates"]["rows"]):
        raise RuntimeError("candidate row count mismatch")
    if seen_groups != set(episode_pools):
        raise RuntimeError("episode/candidate group sets differ")
    return {
        "episode_rows": episode_rows,
        "nested_groups": nested_groups,
        "candidate_rows": candidate_rows,
        "candidate_groups": candidate_groups,
        "role_users": {role: len(users) for role, users in sorted(users_by_role.items())},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--movielens-root", type=Path, required=True)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--fits-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    input_manifest = json.loads((args.input_root / "manifest.json").read_text(encoding="utf-8"))
    prepared_manifest = json.loads((args.prepared_root / "manifest.json").read_text(encoding="utf-8"))
    validation = json.loads((args.fits_root / "validation-report.json").read_text(encoding="utf-8"))
    checks: list[str] = []

    require(input_manifest["status"] == "PASS", "input manifest status is PASS", checks)
    require(prepared_manifest["status"] == "PASS", "prepared manifest status is PASS", checks)
    require(prepared_manifest["input_manifest"]["sha256"] == file_sha256(args.input_root / "manifest.json"),
            "prepared bundle pins the exact input manifest", checks)
    current_source_files = {
        name: {"bytes": (ROOT / name).stat().st_size, "sha256": file_sha256(ROOT / name)}
        for name in RUNTIME_SOURCE_FILES
    }
    current_source_digest = hashlib.sha256(
        json.dumps(current_source_files, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    require(prepared_manifest["source_bundle"]["files"] == current_source_files,
            "prepared bundle pins every runtime source file", checks)
    require(prepared_manifest["source_bundle"]["digest"] == current_source_digest,
            "prepared source bundle digest matches the current runtime files", checks)
    require(input_manifest["input"]["ratings_sha256"] == file_sha256(args.movielens_root / "ratings.csv"),
            "ratings.csv matches the frozen input digest", checks)
    require(input_manifest["input"]["movies_sha256"] == file_sha256(args.movielens_root / "movies.csv"),
            "movies.csv matches the frozen input digest", checks)
    require(prepared_manifest["final_test"] == "NOT_WRITTEN", "prepared bundle excludes FINAL_TEST", checks)
    require(validation["status"] == "PASS", "validation report status is PASS", checks)
    require(validation["calculator"]["sha256"] == file_sha256(ROOT / "scripts/gbt_zero_n_evaluate.py"),
            "validation report pins the exact metric calculator", checks)
    require(validation["final_test_opened"] is False, "validation report keeps FINAL_TEST sealed", checks)
    require(set(validation["profiles"]) == set(config["profiles"]), "all preregistered profiles were evaluated", checks)

    semantic_counts = verify_input_semantics(args.input_root, args.movielens_root, input_manifest)
    checks.append("all input episodes, nested prefixes, candidate pools, as-of years, and UNKNOWN weights passed")

    train_path = args.prepared_root / "train-targets.parquet"
    validation_path = args.prepared_root / "validation-targets.parquet"
    train = pq.read_table(train_path, columns=["uid", "role", "label_state", "sample_weight"])
    validation_targets = pq.read_table(validation_path, columns=["role", "label_state"])
    require(set(train["role"].to_pylist()) == {"TRAIN"}, "training parquet contains only TRAIN rows", checks)
    require(set(validation_targets["role"].to_pylist()) == {"VALIDATION"},
            "validation parquet contains only VALIDATION rows", checks)
    require("UNKNOWN_SAMPLED" not in set(train["label_state"].to_pylist()),
            "UNKNOWN_SAMPLED is absent from training labels", checks)

    grouped = train.group_by("uid").aggregate([("sample_weight", "sum")])
    deviations = pc.abs(pc.subtract(grouped["sample_weight_sum"], 1.0))
    max_weight_deviation = float(pc.max(deviations).as_py())
    require(max_weight_deviation < 1e-9, "each training user's total sample weight equals 1", checks)

    schema_sha = prepared_manifest["files"]["feature-schema.json"]["sha256"]
    input_sha = file_sha256(args.prepared_root / "manifest.json")
    peak_memory: dict[str, int] = {}
    fit_seconds: dict[str, float] = {}
    for profile in config["profiles"]:
        metrics = json.loads((args.fits_root / profile / "metrics.json").read_text(encoding="utf-8"))
        require(metrics["status"] == "PASS", f"{profile} metrics status is PASS", checks)
        require(metrics["spark_version"] == config["runtime"]["spark_version"],
                f"{profile} used pinned Spark version", checks)
        require(metrics["input_manifest"]["sha256"] == input_sha,
                f"{profile} used the exact prepared manifest", checks)
        require(metrics["feature_schema"]["sha256"] == schema_sha,
                f"{profile} used the exact feature schema", checks)
        require(metrics["runtime_source"]["source_bundle_digest"] == current_source_digest,
                f"{profile} used the exact source bundle", checks)
        require(metrics["runtime_source"]["worker"]["sha256"] ==
                current_source_files["scripts/gbt_zero_n_worker.py"]["sha256"],
                f"{profile} used the exact worker", checks)
        require(metrics["runtime_source"]["config"]["sha256"] == current_source_files[
                    "experiments/gbt-zero-n/config.json"]["sha256"],
                f"{profile} used the exact config", checks)
        require(metrics["final_test_opened"] is False, f"{profile} kept FINAL_TEST sealed", checks)
        peak_memory[profile] = int(metrics["resource"]["peak_memory_bytes"])
        fit_seconds[profile] = float(metrics["fit_seconds"])

    report = {
        "schema_version": 1,
        "status": "PASS",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "experiment": config["experiment"],
        "input_root": args.input_root.name,
        "prepared_root": args.prepared_root.name,
        "fits_root": args.fits_root.name,
        "checks": checks,
        "counts": {
            "train_rows": train.num_rows,
            "train_users": grouped.num_rows,
            "validation_target_rows": validation_targets.num_rows,
            **semantic_counts,
        },
        "max_user_weight_sum_deviation": max_weight_deviation,
        "selected_profile_on_validation": validation["selected_profile_on_validation"],
        "fit_seconds": fit_seconds,
        "peak_memory_bytes": peak_memory,
        "final_test_opened": False,
    }
    if args.output.exists():
        raise FileExistsError(f"output already exists: {args.output}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
