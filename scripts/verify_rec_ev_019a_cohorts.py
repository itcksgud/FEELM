#!/usr/bin/env python3
"""Verify REC-EV-019A cohort artifacts and safety boundaries."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from recommendation_protocol_v4 import sha256_file


REPO_ROOT = Path(__file__).resolve().parents[1]
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
PREFIX_COLUMNS = [
    "role",
    "user_key",
    "k",
    "input_rank",
    "movie_id",
    "binary_label",
    "relative_utility",
    "source_position",
    "timestamp",
]
WINDOW_COLUMNS = [
    "role",
    "user_key",
    "k",
    "window_rank",
    "movie_id",
    "rating",
    "midrank_utility",
    "is_positive",
    "is_negative",
    "provisional_candidate_present",
    "timestamp",
]
EXPECTED_COLUMNS = {
    "base-train-ratings.parquet": [
        "user_key",
        "movie_id",
        "rating",
        "timestamp",
        "user_bucket",
    ],
    "candidate-core-provisional.parquet": [
        "movie_id",
        "tmdb_id",
        "base_train_interaction_count",
        "first_base_train_timestamp",
        "identity_status",
    ],
    "binary-prefixes.parquet": PREFIX_COLUMNS,
    "evaluation-windows.parquet": WINDOW_COLUMNS,
    "router-train-binary-prefixes.parquet": PREFIX_COLUMNS,
    "router-train-evaluation-windows.parquet": WINDOW_COLUMNS,
    "validation-binary-prefixes.parquet": PREFIX_COLUMNS,
    "validation-evaluation-windows.parquet": WINDOW_COLUMNS,
    "locked-test-binary-prefixes.parquet": PREFIX_COLUMNS,
    "locked-test-evaluation-windows.parquet": WINDOW_COLUMNS,
}
ROLE_FILES = {
    "ROUTER_TRAIN": ("router-train-binary-prefixes.parquet", "router-train-evaluation-windows.parquet"),
    "VALIDATION": ("validation-binary-prefixes.parquet", "validation-evaluation-windows.parquet"),
    "LOCKED_TEST": ("locked-test-binary-prefixes.parquet", "locked-test-evaluation-windows.parquet"),
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _is_sorted(frame: pd.DataFrame, columns: list[str]) -> bool:
    if frame.empty:
        return True
    expected = frame.sort_values(columns, kind="stable", ignore_index=True)
    return frame.reset_index(drop=True).equals(expected)


def verify(manifest_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest.get("evidence_id") == "REC-EV-019A", "unexpected evidence id")
    require(manifest.get("status") == "PASS_COHORT_GATES", "cohort Gate did not pass")
    contract_path = REPO_ROOT / manifest["contract"]
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    require(
        sha256_file(contract_path) == manifest["contract_sha256"],
        "contract checksum mismatch",
    )

    artifact_paths: dict[str, Path] = {}
    for artifact in manifest["artifacts"]:
        path = REPO_ROOT / artifact["path"]
        require(path.is_file(), f"artifact missing: {path}")
        require(path.stat().st_size == artifact["bytes"], f"artifact size mismatch: {path.name}")
        require(sha256_file(path) == artifact["sha256"], f"artifact checksum mismatch: {path.name}")
        artifact_paths[path.name] = path
    for name in (*EXPECTED_COLUMNS, "cohort-summary.json", "protocol-lock.json"):
        require(name in artifact_paths, f"manifest artifact missing: {name}")

    tables: dict[str, pa.Table] = {}
    for name, expected_columns in EXPECTED_COLUMNS.items():
        table = pq.read_table(artifact_paths[name])
        require(table.column_names == expected_columns, f"column order mismatch: {name}")
        require("user_id" not in table.column_names, f"raw user ID column in {name}")
        tables[name] = table

    base = tables["base-train-ratings.parquet"].to_pandas()
    candidate = tables["candidate-core-provisional.parquet"].to_pandas()
    prefixes = tables["binary-prefixes.parquet"].to_pandas()
    windows = tables["evaluation-windows.parquet"].to_pandas()

    for role, (prefix_name, window_name) in ROLE_FILES.items():
        role_prefixes = tables[prefix_name].to_pandas().reset_index(drop=True)
        role_windows = tables[window_name].to_pandas().reset_index(drop=True)
        require(set(role_prefixes["role"]) <= {role}, f"wrong role in {prefix_name}")
        require(set(role_windows["role"]) <= {role}, f"wrong role in {window_name}")
        pd.testing.assert_frame_equal(
            role_prefixes,
            prefixes.loc[prefixes["role"] == role].reset_index(drop=True),
            check_dtype=True,
        )
        pd.testing.assert_frame_equal(
            role_windows,
            windows.loc[windows["role"] == role].reset_index(drop=True),
            check_dtype=True,
        )

    require(base["user_bucket"].between(0, 39).all(), "non-Base-Train user leaked into base ratings")
    require(base["user_key"].map(lambda value: bool(HEX_64.fullmatch(str(value)))).all(), "invalid base user key")
    require(_is_sorted(base, ["user_key", "timestamp", "movie_id"]), "base ratings sort order changed")
    require(not base.duplicated(["user_key", "movie_id"]).any(), "duplicate base user/movie rows")

    require(_is_sorted(candidate, ["movie_id"]), "candidate core sort order changed")
    require(not candidate["movie_id"].duplicated().any(), "duplicate candidate movie")
    require((candidate["tmdb_id"] > 0).all(), "candidate has invalid TMDB id")
    require((candidate["base_train_interaction_count"] > 0).all(), "candidate has no Base Train interaction")
    require(set(candidate["identity_status"]) == {"LINK_PRESENT"}, "provisional identity status changed")
    require(set(candidate["movie_id"]).issubset(set(base["movie_id"])), "candidate movie absent from cutoff-safe Base Train")

    allowed_roles = {"ROUTER_TRAIN", "VALIDATION", "LOCKED_TEST"}
    require(set(prefixes["role"]).issubset(allowed_roles), "unknown prefix role")
    require(set(windows["role"]).issubset(allowed_roles), "unknown window role")
    require(prefixes["user_key"].map(lambda value: bool(HEX_64.fullmatch(str(value)))).all(), "invalid prefix user key")
    require(windows["user_key"].map(lambda value: bool(HEX_64.fullmatch(str(value)))).all(), "invalid window user key")
    require(_is_sorted(prefixes, ["role", "user_key", "k", "input_rank"]), "prefix sort order changed")
    require(_is_sorted(windows, ["role", "user_key", "k", "window_rank"]), "window sort order changed")
    require(not prefixes.duplicated(["role", "user_key", "k", "input_rank"]).any(), "duplicate prefix primary key")
    require(not windows.duplicated(["role", "user_key", "k", "window_rank"]).any(), "duplicate window primary key")
    require(set(prefixes["k"]) <= {5, 10}, "unexpected prefix K")
    require(set(windows["k"]) <= {0, 5, 10}, "unexpected window K")
    require(set(prefixes["binary_label"]) <= {-1, 1}, "neutral or invalid binary label emitted")

    prefix_sizes = prefixes.groupby(["role", "user_key", "k"], observed=True).size()
    require(all(int(size) == int(index[2]) for index, size in prefix_sizes.items()), "prefix row count differs from K")
    window_sizes = windows.groupby(["role", "user_key", "k"], observed=True).size()
    require((window_sizes == 10).all(), "eligible evaluation window does not have 10 rows")
    require(windows["window_rank"].between(1, 10).all(), "invalid window rank")

    common = prefixes.pivot_table(
        index=["role", "user_key", "input_rank"],
        columns="k",
        values=["movie_id", "binary_label", "source_position"],
        aggfunc="first",
    )
    if 5 in common.columns.get_level_values(1) and 10 in common.columns.get_level_values(1):
        for field in ("movie_id", "binary_label", "source_position"):
            pair = common[field].dropna()
            require((pair[5] == pair[10]).all(), f"K5 is not nested in K10: {field}")

    positive_counts = windows.groupby(["role", "user_key", "k"], observed=True)["is_positive"].sum()
    require((positive_counts >= 3).all(), "strict window has fewer than three positives")
    candidate_positive = (
        windows.assign(
            candidate_positive=windows["is_positive"] & windows["provisional_candidate_present"]
        )
        .groupby(["role", "user_key", "k"], observed=True)["candidate_positive"]
        .any()
    )
    require(candidate_positive.all(), "strict window has no provisional candidate positive")
    require(not (windows["is_positive"] & windows["is_negative"]).any(), "positive and negative labels overlap")

    role_users = {
        role: set(windows.loc[windows["role"] == role, "user_key"])
        for role in allowed_roles
    }
    for left, right in (("ROUTER_TRAIN", "VALIDATION"), ("ROUTER_TRAIN", "LOCKED_TEST"), ("VALIDATION", "LOCKED_TEST")):
        require(role_users[left].isdisjoint(role_users[right]), f"user leaked across roles: {left}/{right}")

    summary = json.loads(artifact_paths["cohort-summary.json"].read_text(encoding="utf-8"))
    lock = json.loads(artifact_paths["protocol-lock.json"].read_text(encoding="utf-8"))
    require(summary.get("raw_user_ids_stored") is False, "summary claims raw user IDs")
    require(summary.get("unrated_as_dislike") is False, "unrated was converted to dislike")
    require(summary.get("neutral_as_dislike") is False, "neutral was converted to dislike")
    require(summary.get("locked_test_model_predictions_opened") is False, "Locked Test model predictions were opened")
    require(lock.get("created_before_model_test") is True, "protocol lock was not created before model test")
    require(lock.get("model_predictions_created") is False, "cohort build created model predictions")
    require(lock["contract_sha256"] == manifest["contract_sha256"], "lock/manifest contract mismatch")

    validation = manifest["validation"]
    actual_role_k_counts = {
        role: {
            str(k): int(
                windows.loc[
                    (windows["role"] == role) & (windows["k"] == k), "user_key"
                ].nunique()
            )
            for k in (0, 5, 10)
        }
        for role in sorted(allowed_roles)
    }
    require(
        validation.get("strict_eligible_users_by_role_and_k") == actual_role_k_counts,
        "manifest role/K strict user counts mismatch",
    )
    minimum = int(contract["gates"]["locked_test_k10_strict_eligible_min"])
    provisional_count = int(
        windows.loc[(windows["role"] == "LOCKED_TEST") & (windows["k"] == 10), "user_key"].nunique()
    )
    require(provisional_count == int(validation["locked_test_k10_provisional_eligible"]), "manifest provisional K10 count mismatch")
    require(provisional_count >= minimum, "provisional K10 Locked Test Gate failed")
    final_gate = summary["final_identity_locked_test_k10_gate"]
    if final_gate.get("pass") is not None:
        identity_path = REPO_ROOT / final_gate["identity_artifact"]
        require(identity_path.is_file(), "final identity artifact is missing")
        require(
            sha256_file(identity_path) == final_gate["identity_artifact_sha256"],
            "final identity artifact checksum mismatch",
        )
        identity = pd.read_parquet(identity_path, columns=["movie_id", "identity_status"])
        candidate_identity = identity.loc[identity["movie_id"].isin(set(candidate["movie_id"].astype(int)))]
        allowed = set(
            candidate_identity.loc[
                candidate_identity["identity_status"].isin(
                    final_gate["identity_status_allowlist"]
                ),
                "movie_id",
            ].astype(int)
        )
        require(
            int(final_gate["candidate_identity_rows_found"]) == len(candidate_identity),
            "final identity candidate join count mismatch",
        )
        require(
            int(final_gate["final_identity_candidate_movies"]) == len(allowed),
            "final identity candidate count mismatch",
        )
        require(
            int(validation["final_identity_candidate_movies"]) == len(allowed),
            "manifest final candidate count mismatch",
        )
        require(final_gate["pass"] is True, "final identity K10 Gate failed")
        require(int(final_gate["final_identity_k10_strict_eligible_users"]) >= minimum, "final identity users below minimum")
        require(int(validation["locked_test_k10_final_identity_eligible"]) == int(final_gate["final_identity_k10_strict_eligible_users"]), "manifest final identity count mismatch")
    require(validation.get("product_policy_changed") is False, "product policy changed")

    return {
        "status": "PASS",
        "evidence_id": "REC-EV-019A",
        "base_train_rows": len(base),
        "provisional_candidate_movies": len(candidate),
        "final_identity_candidate_movies": validation.get(
            "final_identity_candidate_movies"
        ),
        "locked_test_k10_provisional_eligible": provisional_count,
        "locked_test_k10_final_identity_eligible": validation.get(
            "locked_test_k10_final_identity_eligible"
        ),
        "locked_test_model_predictions_opened": False,
        "product_policy_changed": False,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify REC-EV-019A cohort artifacts")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=REPO_ROOT / "docs/recommendation/evidence/manifests/rec-ev-019a.json",
    )
    args = parser.parse_args()
    args.manifest = args.manifest.resolve()
    return args


if __name__ == "__main__":
    try:
        print(json.dumps(verify(parse_args().manifest), ensure_ascii=False, sort_keys=True))
    except Exception as error:
        print(f"REC-EV-019A verification failed: {error}")
        raise SystemExit(1)
