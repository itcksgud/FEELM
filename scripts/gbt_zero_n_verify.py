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
    "experiments/gbt-zero-n/config-v2.json",
    "experiments/gbt-zero-n/config-v3.json",
    "experiments/gbt-zero-n/config-v4-prefix.json",
    "experiments/gbt-zero-n/config-v4-prefix-seed1622.json",
    "experiments/gbt-zero-n/config-v4-prefix-seed2622.json",
    "experiments/gbt-zero-n/policy-v5-robust-blend.json",
    "scripts/gbt_zero_n_build_input.py",
    "scripts/gbt_zero_n_prefix_build_input.py",
    "scripts/gbt_zero_n_blend_evaluate.py",
    "scripts/gbt_zero_n_evaluate.py",
    "scripts/gbt_zero_n_features.py",
    "scripts/gbt_zero_n_prepare.py",
    "scripts/gbt_zero_n_policy_evaluate.py",
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
    controlled_prefix = (
        manifest.get("experiment_design") == "CONTROLLED_RECENT_PREFIX_AT_PER_USER_ANCHOR_V1"
    )
    cutoffs = manifest.get("cutoffs", {})
    users_by_role: dict[str, set[int]] = defaultdict(set)
    user_role: dict[int, str] = {}
    validation_user_split: dict[int, str] = {}
    episode_pools: dict[tuple[str, int, int], tuple[str, str]] = {}
    episode_rows = 0
    episode_digest = hashlib.sha256()
    nested_groups = 0
    current_key = None
    current_histories: list[tuple[int, tuple[str, ...]]] = []
    current_pool_digest = None
    current_full_history_count = 0
    current_total_history_counts: set[int] = set()
    current_source_history_counts: set[int] = set()

    def finish_nested() -> None:
        nonlocal nested_groups
        if not current_histories:
            return
        observed_k = [item[0] for item in current_histories]
        if controlled_prefix:
            if observed_k != [int(value) for value in manifest["k_values"]]:
                raise RuntimeError(f"controlled exact-K variants differ from manifest: {current_key}")
            for (_, previous), (_, following) in zip(current_histories, current_histories[1:]):
                if previous and previous != following[-len(previous):]:
                    raise RuntimeError(f"history is not a nested recent suffix: {current_key}")
            if len(current_total_history_counts) != 1 or len(current_source_history_counts) != 1:
                raise RuntimeError(f"hidden source-history metadata changed across K: {current_key}")
            if current_total_history_counts != current_source_history_counts:
                raise RuntimeError(f"totalHistoryCount differs from source history: {current_key}")
            if current_full_history_count > 1:
                raise RuntimeError(f"controlled group has multiple full-history variants: {current_key}")
        else:
            for (_, previous), (_, following) in zip(current_histories, current_histories[1:]):
                if previous != following[:len(previous)]:
                    raise RuntimeError(f"history is not nested prefix: {current_key}")
            if current_full_history_count != 1:
                raise RuntimeError(f"nested group must contain exactly one full-history variant: {current_key}")
        nested_groups += 1

    with (input_root / "episodes.jsonl").open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            if episode_rows:
                episode_digest.update(b"\n")
            episode_digest.update(json.dumps(row, sort_keys=True, separators=(",", ":")).encode())
            episode_rows += 1
            role = row["role"]
            uid = int(row["uid"])
            previous_role = user_role.setdefault(uid, role)
            if previous_role != role:
                raise RuntimeError(f"user appears in multiple roles: {uid}")
            users_by_role[role].add(uid)
            if role == "VALIDATION":
                split = str(row.get("evaluation_split"))
                previous_split = validation_user_split.setdefault(uid, split)
                if split not in {"SELECTION", "CONFIRM"} or previous_split != split:
                    raise RuntimeError(f"validation user crosses evaluation splits: {uid}")
            if not controlled_prefix:
                window = cutoffs[role]
                if int(row["prediction_at"]) != int(window["feature_cutoff_inclusive"]) + 1:
                    raise RuntimeError("prediction_at does not follow feature cutoff")
            if int(row["catalog_snapshot_at"]) > int(row["prediction_at"]):
                raise RuntimeError("catalog snapshot is after prediction")
            if controlled_prefix:
                if int(row["target_event_at"]) <= int(row["prediction_at"]):
                    raise RuntimeError("controlled-prefix target is not after prediction")
                if not bool(row.get("is_controlled_prefix")):
                    raise RuntimeError("controlled-prefix marker is absent")
            elif not (int(window["target_start_exclusive"]) < int(row["target_event_at"]) <=
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
            if int(row.get("provided_history_count", row["n"])) != len(history):
                raise RuntimeError("provided history count mismatch")
            if len(history) > int(row["total_history_count"]):
                raise RuntimeError("supported history exceeds total history")
            if int(row["n"]) != len(history):
                raise RuntimeError("N differs from supported history count")
            is_full = len(history) == int(row["total_history_count"])
            if bool(row.get("is_full_history")) != is_full:
                raise RuntimeError("full-history marker mismatch")
            key = (role, uid, int(row["target_movie_id"]), int(row["prediction_at"]))
            if key != current_key:
                finish_nested()
                current_key = key
                current_histories = []
                current_pool_digest = row["candidate_digest"]
                current_full_history_count = 0
                current_total_history_counts = set()
                current_source_history_counts = set()
            elif current_pool_digest != row["candidate_digest"]:
                raise RuntimeError("candidate pool changed inside a nested-history group")
            current_histories.append((int(row["n"]), tuple(str(item["event_id"]) for item in history)))
            current_full_history_count += int(is_full)
            current_total_history_counts.add(int(row["total_history_count"]))
            current_source_history_counts.add(int(row.get("source_history_count", row["total_history_count"])))
            pool_key = (role, uid, int(row["target_movie_id"]))
            episode_pools[pool_key] = (row["candidate_digest"], row["eligible_population_digest"])
    finish_nested()

    if episode_rows != int(manifest["episodes"]["rows"]):
        raise RuntimeError("episode row count mismatch")
    if episode_digest.hexdigest() != manifest["episodes"]["sha256"]:
        raise RuntimeError("episode semantic digest mismatch")
    for role, users in users_by_role.items():
        digest = hashlib.sha256(",".join(map(str, sorted(users))).encode()).hexdigest()
        if digest != manifest["user_partition"]["role_user_digests"][role]:
            raise RuntimeError(f"role user digest mismatch: {role}")
        if len(users) != int(manifest["users"][role]):
            raise RuntimeError(f"role user count mismatch: {role}")
    if users_by_role.get("FINAL_TEST"):
        raise RuntimeError("FINAL_TEST rows must be physically omitted")
    if controlled_prefix:
        excluded = manifest.get("excluded_validation_users", {})
        source = excluded.get("source")
        if source:
            source_path = Path(source["path"])
            if not source_path.is_absolute():
                source_path = ROOT / source_path
            if not source_path.exists() or file_sha256(source_path) != source["sha256"]:
                raise RuntimeError("excluded validation-user source is missing or changed")
            excluded_users = set(
                int(value) for value in pq.read_table(source_path, columns=["uid"])["uid"].to_pylist()
            )
            excluded_digest = hashlib.sha256(
                ",".join(map(str, sorted(excluded_users))).encode()
            ).hexdigest()
            if len(excluded_users) != int(excluded["count"]) or excluded_digest != excluded["uid_digest"]:
                raise RuntimeError("excluded validation-user receipt mismatch")
            if excluded_users & users_by_role.get("VALIDATION", set()):
                raise RuntimeError("confirmation cohort reuses an excluded validation user")

    release_years: dict[int, int | None] = {}
    with (movielens_root / "movies.csv").open("r", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            match = re.search(r"\((\d{4})\)\s*$", row["title"])
            release_years[int(row["movieId"])] = int(match.group(1)) if match else None

    probability = float(manifest["unknown_sampling"]["probability"])
    hash_cutoff = int(probability * (1 << 256))
    sampling_seed = int(manifest["unknown_sampling"]["seed"])
    candidate_rows = 0
    candidate_digest = hashlib.sha256()
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
            if candidate_rows:
                candidate_digest.update(b"\n")
            candidate_digest.update(json.dumps(row, sort_keys=True, separators=(",", ":")).encode())
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
                sampled_value = int.from_bytes(
                    hashlib.sha256(f"{sampling_seed}:{movie_id}".encode()).digest(), "big"
                )
                if sampled_value >= hash_cutoff:
                    raise RuntimeError("UNKNOWN candidate violates SHA-256 sampling rule")
            else:
                if row["observed_rating"] is None or row["sampling_probability"] is not None or \
                        row["importance_weight"] is not None:
                    raise RuntimeError("observed candidate label metadata mismatch")
    finish_candidates()
    if candidate_rows != int(manifest["candidates"]["rows"]):
        raise RuntimeError("candidate row count mismatch")
    if candidate_digest.hexdigest() != manifest["candidates"]["sha256"]:
        raise RuntimeError("candidate semantic digest mismatch")
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
    parser.add_argument("--config", type=Path, default=CONFIG)
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    input_manifest = json.loads((args.input_root / "manifest.json").read_text(encoding="utf-8"))
    prepared_manifest = json.loads((args.prepared_root / "manifest.json").read_text(encoding="utf-8"))
    validation = json.loads((args.fits_root / "validation-report.json").read_text(encoding="utf-8"))
    checks: list[str] = []

    require(input_manifest["status"] == "PASS", "input manifest status is PASS", checks)
    require(input_manifest.get("final_test") in {"PHYSICALLY_OMITTED", None},
            "input bundle physically omits FINAL_TEST", checks)
    require(input_manifest.get("final_test_opened", False) is False,
            "input bundle keeps FINAL_TEST sealed", checks)
    distribution_path = args.input_root / "split-distribution-report.json"
    if distribution_path.exists():
        require(input_manifest["distribution_report"]["sha256"] == file_sha256(distribution_path),
                "input manifest pins the distribution report", checks)
        distribution = json.loads(distribution_path.read_text(encoding="utf-8"))
        require(distribution["status"] == "PASS", "required exact-K cells pass", checks)
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
    validation_targets = pq.read_table(
        validation_path,
        columns=["role", "label_state", "n", "total_history_count", "provided_history_count",
                 "supported_history_count", "is_full_history", "is_controlled_prefix"],
    )
    require(set(train["role"].to_pylist()) == {"TRAIN"}, "training parquet contains only TRAIN rows", checks)
    require(set(validation_targets["role"].to_pylist()) == {"VALIDATION"},
            "validation parquet contains only VALIDATION rows", checks)
    require("UNKNOWN_SAMPLED" not in set(train["label_state"].to_pylist()),
            "UNKNOWN_SAMPLED is absent from training labels", checks)
    require(pc.all(pc.equal(validation_targets["n"], validation_targets["supported_history_count"])).as_py(),
            "validation N equals supportedHistoryCount", checks)
    require(pc.all(pc.equal(validation_targets["n"], validation_targets["provided_history_count"])).as_py(),
            "validation N equals providedHistoryCount", checks)
    full_expected = pc.equal(validation_targets["supported_history_count"],
                             validation_targets["total_history_count"])
    require(pc.all(pc.equal(validation_targets["is_full_history"], full_expected)).as_py(),
            "validation full-history marker matches arbitrary N", checks)

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
        require(metrics["runtime_source"]["config"]["sha256"] == file_sha256(args.config),
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
