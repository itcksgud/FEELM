#!/usr/bin/env python3
"""Audit whether REC-EV-019 has enough leakage-safe MovieLens users to run.

This is a deterministic feasibility preflight, not a recommender performance
result. It emits aggregate counts only and never stores raw MovieLens user IDs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


EVIDENCE_ID = "REC-EV-019P"
PROTOCOL_VERSION = "rec-ev-019p-binary-onboarding-preflight-v2"
RATING_VALUES = np.arange(0.5, 5.01, 0.5, dtype=np.float64)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--protocol",
        type=Path,
        default=Path("docs/recommendation/protocols/rec-eval-vnext.json"),
    )
    parser.add_argument(
        "--split-dir",
        type=Path,
        default=Path("outputs/recommendation-evidence/global-time-v1"),
    )
    parser.add_argument(
        "--global-manifest",
        type=Path,
        default=Path(
            "docs/recommendation/evidence/manifests/global-time-v1.json"
        ),
    )
    parser.add_argument("--archive", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "docs/recommendation/evidence/results/"
            "rec-ev-019p-binary-onboarding-preflight.json"
        ),
    )
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value))


def stable_user_bucket(user_id: int, *, split_prefix: str) -> int:
    digest = hashlib.sha256(f"{split_prefix}{int(user_id)}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % 100


def rating_index(rating: float) -> int:
    index = int(round((float(rating) - 0.5) * 2.0))
    if index < 0 or index >= len(RATING_VALUES) or not np.isclose(
        RATING_VALUES[index], rating
    ):
        raise ValueError(f"rating is outside the MovieLens half-star grid: {rating}")
    return index


def global_midrank_ecdf(ratings: Iterable[float]) -> np.ndarray:
    values = np.asarray(ratings, dtype=np.float64)
    if not len(values):
        raise ValueError("global rating distribution cannot be empty")
    indices = np.rint((values - 0.5) * 2.0).astype(np.int64)
    if (
        bool((indices < 0).any())
        or bool((indices >= len(RATING_VALUES)).any())
        or not bool(np.allclose(RATING_VALUES[indices], values))
    ):
        raise ValueError("global ratings contain a value outside the half-star grid")
    counts = np.bincount(indices, minlength=len(RATING_VALUES)).astype(np.float64)
    cumulative_before = np.cumsum(counts) - counts
    return (cumulative_before + 0.5 * counts) / counts.sum()


def sequential_binary_labels(
    ratings: Iterable[float],
    global_midrank: np.ndarray,
    *,
    shrinkage: float,
    like_min: float,
    dislike_max: float,
) -> list[tuple[int, int, float]]:
    """Return (zero-based position, {-1,+1}, relative utility) labels.

    Each label uses only the prefix ending at that rating plus the Base-Train
    global distribution. Neutral values are intentionally absent.
    """

    if shrinkage < 0 or dislike_max >= like_min:
        raise ValueError("invalid binary conversion parameters")
    local_counts = np.zeros(len(RATING_VALUES), dtype=np.int64)
    labels: list[tuple[int, int, float]] = []
    for position, rating in enumerate(ratings):
        index = rating_index(float(rating))
        local_counts[index] += 1
        count = position + 1
        user_midrank = (
            float(local_counts[:index].sum()) + 0.5 * float(local_counts[index])
        ) / count
        relative = (
            count / (count + shrinkage) * (user_midrank - 0.5)
            + shrinkage
            / (count + shrinkage)
            * (float(global_midrank[index]) - 0.5)
        )
        if relative >= like_min:
            labels.append((position, 1, relative))
        elif relative <= dislike_max:
            labels.append((position, -1, relative))
    return labels


def binary_k_eligibility(
    labels: list[tuple[int, int, float]],
    *,
    rating_count: int,
    k: int,
    future_window: int,
) -> dict[str, bool]:
    if k < 1 or future_window < 1:
        raise ValueError("k and future_window must be positive")
    if len(labels) < k:
        return {"eligible": False, "both_classes": False}
    selected = labels[:k]
    last_input_position = selected[-1][0]
    return {
        "eligible": rating_count - last_input_position - 1 >= future_window,
        "both_classes": len({label for _, label, _ in selected}) == 2,
    }


def future_midrank_utilities(ratings: Iterable[float]) -> np.ndarray:
    values = np.asarray(ratings, dtype=np.float64)
    lookup = global_midrank_ecdf(values)
    indices = np.rint((values - 0.5) * 2.0).astype(np.int64)
    return lookup[indices]


def strict_binary_k_eligibility(
    labels: list[tuple[int, int, float]],
    *,
    ratings: np.ndarray,
    movie_ids: np.ndarray,
    candidate_movie_ids: set[int],
    k: int,
    future_window: int,
    positive_midrank_min: float,
    minimum_positives: int,
) -> dict[str, Any]:
    weak = binary_k_eligibility(
        labels, rating_count=len(ratings), k=k, future_window=future_window
    )
    result: dict[str, Any] = {
        "input_and_future": bool(weak["eligible"]),
        "both_classes": bool(weak["both_classes"]),
        "minimum_positives": False,
        "candidate_positive": False,
        "eligible": False,
        "positive_count": 0,
    }
    if not weak["eligible"]:
        return result

    last_input_position = labels[:k][-1][0]
    start = last_input_position + 1
    stop = start + future_window
    future_ratings = ratings[start:stop]
    future_movies = movie_ids[start:stop]
    utilities = future_midrank_utilities(future_ratings)
    positive_mask = utilities >= positive_midrank_min
    positive_count = int(positive_mask.sum())
    result["positive_count"] = positive_count
    result["minimum_positives"] = positive_count >= minimum_positives
    if not result["minimum_positives"]:
        return result

    result["candidate_positive"] = any(
        int(movie_id) in candidate_movie_ids
        for movie_id in future_movies[positive_mask]
    )
    result["eligible"] = bool(result["candidate_positive"])
    return result


def split_prefix(protocol: dict[str, Any]) -> str:
    algorithm = str(protocol["user_split"]["algorithm"])
    marker = "SHA256('"
    start = algorithm.find(marker)
    end = algorithm.find("' + userId", start)
    if start < 0 or end < 0:
        raise ValueError("protocol user split algorithm does not expose a fixed prefix")
    return algorithm[start + len(marker) : end]


def validate_protocol(protocol: dict[str, Any]) -> None:
    user_split = protocol["user_split"]
    expected = {
        "base_train_buckets": [0, 39],
        "router_train_buckets": [40, 49],
        "validation_buckets": [50, 59],
        "test_buckets": [60, 99],
    }
    for key, value in expected.items():
        if user_split.get(key) != value:
            raise ValueError(f"protocol {key} must be {value}")
    if protocol["candidate"].get("positive_injection") is not False:
        raise ValueError("positive injection must remain disabled")
    if protocol["inputs"].get("binary_to_numeric_rating_forbidden") is not True:
        raise ValueError("binary-to-rating conversion must remain forbidden")
    if protocol["inputs"].get("unrated_as_negative_forbidden") is not True:
        raise ValueError("unrated-as-negative must remain forbidden")
    candidate = protocol["candidate"]
    if candidate.get("missing_model_artifact_policy") != "KEEP_WITH_DECLARED_FALLBACK":
        raise ValueError("missing model artifacts must use the declared fallback")
    if candidate.get("provisional_identity_basis") != "MOVIELENS_LINKS_TMDB_ID_PRESENT":
        raise ValueError("preflight identity basis changed")


def resolve_archive(args: argparse.Namespace) -> Path:
    if args.archive is not None:
        return args.archive
    manifest = json.loads(args.global_manifest.read_text(encoding="utf-8"))
    archive = Path(manifest["source"]["archive"])
    if not archive.exists():
        raise FileNotFoundError(f"MovieLens archive does not exist: {archive}")
    return archive


def tmdb_linked_movie_ids(archive: Path) -> set[int]:
    with zipfile.ZipFile(archive) as source:
        links = pd.read_csv(
            source.open("ml-32m/links.csv"), usecols=["movieId", "tmdbId"]
        )
    return set(
        links.loc[links["tmdbId"].notna(), "movieId"].astype(np.int64).tolist()
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
    validate_protocol(protocol)

    train_path = args.split_dir / "train.parquet"
    test_path = args.split_dir / "test.parquet"
    if not train_path.exists() or not test_path.exists():
        raise FileNotFoundError("global-time-v1 train/test parquet artifacts are required")

    archive_path = resolve_archive(args)
    train = pd.read_parquet(
        train_path, columns=["user_id", "movie_id", "rating"]
    )
    prefix = split_prefix(protocol)
    base_low, base_high = protocol["user_split"]["base_train_buckets"]
    base_users = {
        int(user_id)
        for user_id in train["user_id"].unique()
        if base_low <= stable_user_bucket(int(user_id), split_prefix=prefix) <= base_high
    }
    base_train = train.loc[train["user_id"].isin(base_users)]
    global_midrank = global_midrank_ecdf(
        base_train["rating"].to_numpy(dtype=np.float64)
    )
    candidate_movie_ids = set(
        base_train["movie_id"].astype(np.int64).unique().tolist()
    ) & tmdb_linked_movie_ids(archive_path)
    base_train_rows = int(len(base_train))
    base_train_users = int(len(base_users))
    del train, base_train, base_users

    test = pd.read_parquet(
        test_path, columns=["user_id", "movie_id", "rating", "timestamp"]
    ).sort_values(["user_id", "timestamp", "movie_id"], kind="stable")

    test_low, test_high = protocol["user_split"]["test_buckets"]
    k_values = list(protocol["inputs"]["binary_k_primary"])
    k_values = [int(value) for value in k_values if int(value) > 0]
    future_window = int(protocol["relevance"]["future_window_ratings"])
    shrinkage = float(protocol["inputs"]["binary_shrinkage_lambda"])
    like_min = float(protocol["inputs"]["binary_relative_like_min"])
    dislike_max = float(protocol["inputs"]["binary_relative_dislike_max"])
    positive_midrank_min = float(
        protocol["relevance"]["positive_midrank_utility_min"]
    )
    minimum_positives = int(protocol["relevance"]["minimum_future_positives"])

    role_user_counts = {
        "BASE_TRAIN": 0,
        "ROUTER_TRAIN": 0,
        "VALIDATION": 0,
        "LOCKED_TEST": 0,
    }
    eligibility = {
        str(k): {
            "input_and_future_users": 0,
            "minimum_positive_users": 0,
            "candidate_positive_users": 0,
            "eligible_users": 0,
            "eligible_with_both_classes": 0,
            "not_enough_binary_labels_or_future": 0,
            "not_enough_future_positives": 0,
            "no_positive_in_candidate_universe": 0,
        }
        for k in k_values
    }
    prior_30pct_strict_eligibility = {str(k): 0 for k in k_values}
    locked_test_users = 0
    locked_test_users_20_plus = 0
    locked_test_users_30_plus = 0
    observed_rows = 0
    labeled_rows = 0
    like_rows = 0
    dislike_rows = 0

    for user_id, group in test.groupby("user_id", sort=True):
        bucket = stable_user_bucket(int(user_id), split_prefix=prefix)
        if bucket <= 39:
            role_user_counts["BASE_TRAIN"] += 1
        elif bucket <= 49:
            role_user_counts["ROUTER_TRAIN"] += 1
        elif bucket <= 59:
            role_user_counts["VALIDATION"] += 1
        else:
            role_user_counts["LOCKED_TEST"] += 1

        if not test_low <= bucket <= test_high:
            continue

        ratings = group["rating"].to_numpy(dtype=np.float64)
        movie_ids = group["movie_id"].to_numpy(dtype=np.int64)
        locked_test_users += 1
        locked_test_users_20_plus += int(len(ratings) >= 20)
        locked_test_users_30_plus += int(len(ratings) >= 30)
        labels = sequential_binary_labels(
            ratings,
            global_midrank,
            shrinkage=shrinkage,
            like_min=like_min,
            dislike_max=dislike_max,
        )
        observed_rows += len(ratings)
        labeled_rows += len(labels)
        like_rows += sum(label == 1 for _, label, _ in labels)
        dislike_rows += sum(label == -1 for _, label, _ in labels)
        for k in k_values:
            result = strict_binary_k_eligibility(
                labels,
                ratings=ratings,
                movie_ids=movie_ids,
                candidate_movie_ids=candidate_movie_ids,
                k=k,
                future_window=future_window,
                positive_midrank_min=positive_midrank_min,
                minimum_positives=minimum_positives,
            )
            if not result["input_and_future"]:
                eligibility[str(k)]["not_enough_binary_labels_or_future"] += 1
                continue
            eligibility[str(k)]["input_and_future_users"] += 1
            if not result["minimum_positives"]:
                eligibility[str(k)]["not_enough_future_positives"] += 1
                continue
            eligibility[str(k)]["minimum_positive_users"] += 1
            if not result["candidate_positive"]:
                eligibility[str(k)]["no_positive_in_candidate_universe"] += 1
                continue
            eligibility[str(k)]["candidate_positive_users"] += 1
            eligibility[str(k)]["eligible_users"] += 1
            eligibility[str(k)]["eligible_with_both_classes"] += int(
                result["both_classes"]
            )
            if bucket >= 70:
                prior_30pct_strict_eligibility[str(k)] += 1

    minimum_users = int(protocol["statistics"]["minimum_test_users"])
    primary_k = max(k_values)
    primary_eligible = eligibility[str(primary_k)]["eligible_users"]
    gate_pass = primary_eligible >= minimum_users
    result = {
        "schema_version": 2,
        "evidence_id": EVIDENCE_ID,
        "protocol_version": PROTOCOL_VERSION,
        "status": "PASS" if gate_pass else "FAIL_INSUFFICIENT_TEST_USERS",
        "claim_boundary": "FEASIBILITY_ONLY_NOT_RECOMMENDATION_PERFORMANCE",
        "source": {
            "protocol": {
                "path": args.protocol.as_posix(),
                "sha256": sha256(args.protocol),
            },
            "train": {"path": train_path.as_posix()},
            "test": {"path": test_path.as_posix()},
            "movielens_archive": {
                "path": archive_path.as_posix(),
                "sha256": sha256(archive_path),
            },
        },
        "split": {
            "prefix": prefix,
            "role_user_counts_in_test_period": role_user_counts,
            "locked_test_users": locked_test_users,
            "locked_test_users_with_at_least_20_ratings": locked_test_users_20_plus,
            "locked_test_users_with_at_least_30_ratings": locked_test_users_30_plus,
            "raw_user_ids_stored": False,
            "base_train_rows": base_train_rows,
            "base_train_users": base_train_users,
        },
        "candidate_universe": {
            "basis": "BASE_TRAIN_MOVIE_AND_MOVIELENS_LINKS_TMDB_ID_PRESENT",
            "movie_count": len(candidate_movie_ids),
            "tmdb_api_identity_verified": False,
            "missing_model_artifact_policy": "KEEP_WITH_DECLARED_FALLBACK",
        },
        "binary_proxy": {
            "shrinkage_lambda": shrinkage,
            "like_min": like_min,
            "dislike_max": dislike_max,
            "future_window_ratings": future_window,
            "positive_midrank_utility_min": positive_midrank_min,
            "minimum_future_positives": minimum_positives,
            "observed_rows": observed_rows,
            "labeled_rows": labeled_rows,
            "neutral_rows": observed_rows - labeled_rows,
            "like_rows": like_rows,
            "dislike_rows": dislike_rows,
            "eligibility": eligibility,
            "current_basis_bucket_70_99_strict_eligible_users": (
                prior_30pct_strict_eligibility
            ),
        },
        "gate": {
            "primary_binary_k": primary_k,
            "minimum_test_users": minimum_users,
            "eligible_test_users": primary_eligible,
            "eligibility_definition": (
                "K_INPUT_AND_10_FUTURE_AND_3_POSITIVES_AND_1_CANDIDATE_POSITIVE"
            ),
            "pass": gate_pass,
        },
    }
    write_json(args.output, result)
    return result


if __name__ == "__main__":
    output = run(parse_args())
    print(json.dumps(output["gate"], ensure_ascii=False, sort_keys=True))
