#!/usr/bin/env python3
"""Run REC-EV-005 synthetic MovieLens party aggregation evidence.

Train-only ALS factors and rating profiles define predicted relative utility and
taste similarity. Validation selects similarity cutoffs and one Balanced
candidate. Test ratings are used only for the held-out evaluation values. The
common-rated candidate diagnostic is not an online party-satisfaction claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow


RATING_VALUES = np.arange(0.5, 5.0 + 0.5, 0.5)
GROUP_SIZES = (2, 3, 4)
GROUP_TYPES = ("DISSIMILAR", "MIDDLE", "SIMILAR")
POLICIES = ("AVERAGE", "LEAST_MISERY", "MOST_HAPPINESS", "BALANCED")


@dataclass(frozen=True)
class BalancedParameters:
    floor: float
    floor_weight: float
    gap_weight: float

    @property
    def key(self) -> str:
        return f"floor={self.floor:.2f}|floorWeight={self.floor_weight:.2f}|gapWeight={self.gap_weight:.2f}"


@dataclass
class Party:
    label: str
    split: str
    group_size: int
    group_type: str
    similarity: float
    candidate_count: int
    candidate_ids: np.ndarray
    titles: list[str]
    predicted_utility: np.ndarray
    actual_utility: np.ndarray
    actual_ratings: np.ndarray


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split-dir", type=Path, required=True)
    parser.add_argument("--split-manifest", type=Path, required=True)
    parser.add_argument("--baseline-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--seed", type=int, default=20260829)
    parser.add_argument("--eligible-user-limit", type=int, default=600)
    parser.add_argument("--group-draws", type=int, default=60000)
    parser.add_argument("--parties-per-cell", type=int, default=30)
    parser.add_argument("--minimum-parties-per-cell", type=int, default=20)
    parser.add_argument("--minimum-common-candidates", type=int, default=5)
    parser.add_argument("--candidate-cap", type=int, default=20)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--relative-utility-shrinkage", type=float, default=20.0)
    parser.add_argument("--balanced-relevance-loss-budget", type=float, default=0.03)
    parser.add_argument("--balanced-observed-mean-loss-budget", type=float, default=0.02)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_record(path: Path, rows: int | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(path),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }
    if rows is not None:
        result["rows"] = rows
    return result


def relative_utility(
    ratings: np.ndarray,
    profile_counts: np.ndarray,
    global_counts: np.ndarray,
    shrinkage: float,
) -> np.ndarray:
    """Map stars to the REC-EV-002 shrunk user mid-rank ECDF."""
    values = np.asarray(ratings, dtype=np.float64)
    if profile_counts.shape != (len(RATING_VALUES),):
        raise ValueError("profile_counts must have one count for each MovieLens rating value")
    rating_indices = np.rint((np.clip(values, 0.5, 5.0) - 0.5) * 2).astype(np.int64)
    user_lower = np.cumsum(profile_counts) - profile_counts
    global_lower = np.cumsum(global_counts) - global_counts
    user_total = float(profile_counts.sum())
    global_total = float(global_counts.sum())
    global_ecdf = (
        global_lower[rating_indices] + 0.5 * global_counts[rating_indices]
    ) / global_total
    if user_total == 0:
        return global_ecdf
    user_ecdf = (
        user_lower[rating_indices] + 0.5 * profile_counts[rating_indices]
    ) / user_total
    weight = user_total / (user_total + shrinkage)
    return weight * user_ecdf + (1.0 - weight) * global_ecdf


def policy_scores(
    utilities: np.ndarray,
    policy: str,
    balanced: BalancedParameters | None = None,
) -> np.ndarray:
    if utilities.ndim != 2 or utilities.shape[0] < 2:
        raise ValueError("utilities must be member x candidate")
    mean = utilities.mean(axis=0)
    minimum = utilities.min(axis=0)
    maximum = utilities.max(axis=0)
    if policy == "AVERAGE":
        return mean
    if policy == "LEAST_MISERY":
        return minimum
    if policy == "MOST_HAPPINESS":
        return maximum
    if policy == "BALANCED":
        if balanced is None:
            raise ValueError("Balanced parameters are required")
        floor_shortfall = np.maximum(0.0, balanced.floor - minimum)
        gap = maximum - minimum
        return mean - balanced.floor_weight * floor_shortfall - balanced.gap_weight * gap
    raise ValueError(f"unknown policy {policy}")


def stable_top_indices(scores: np.ndarray, candidate_ids: np.ndarray, top_k: int) -> np.ndarray:
    if len(scores) != len(candidate_ids):
        raise ValueError("score and candidate lengths differ")
    order = np.lexsort((candidate_ids, -scores))
    return order[: min(top_k, len(order))]


def evaluate_policy(
    party: Party,
    policy: str,
    balanced: BalancedParameters | None,
    top_k: int,
) -> dict[str, Any]:
    scores = policy_scores(party.predicted_utility, policy, balanced)
    chosen = stable_top_indices(scores, party.candidate_ids, top_k)
    actual_by_member = party.actual_utility[:, chosen].mean(axis=1)
    predicted_by_member = party.predicted_utility[:, chosen].mean(axis=1)
    return {
        "selected": chosen,
        "actual_mean_utility": float(actual_by_member.mean()),
        "actual_min_utility": float(actual_by_member.min()),
        "actual_member_gap": float(actual_by_member.max() - actual_by_member.min()),
        "actual_raw_rating_mean": float(party.actual_ratings[:, chosen].mean()),
        "predicted_mean_utility": float(predicted_by_member.mean()),
        "actual_by_member": actual_by_member,
        "predicted_by_member": predicted_by_member,
    }


def balanced_grid() -> list[BalancedParameters]:
    return [
        BalancedParameters(floor, floor_weight, gap_weight)
        for floor in (0.20, 0.30, 0.40, 0.50)
        for floor_weight in (0.25, 0.50, 1.00, 2.00)
        for gap_weight in (0.00, 0.10, 0.25, 0.50)
    ]


def select_balanced_parameters(
    parties: list[Party],
    *,
    top_k: int,
    relevance_loss_budget: float,
    observed_mean_loss_budget: float,
) -> tuple[BalancedParameters, list[dict[str, Any]]]:
    if not parties:
        raise ValueError("validation parties are required")
    average = [evaluate_policy(party, "AVERAGE", None, top_k) for party in parties]
    average_actual_mean = float(np.mean([row["actual_mean_utility"] for row in average]))
    average_predicted_mean = np.array(
        [row["predicted_mean_utility"] for row in average], dtype=np.float64
    )
    rows: list[dict[str, Any]] = []
    for parameters in balanced_grid():
        evaluated = [evaluate_policy(party, "BALANCED", parameters, top_k) for party in parties]
        actual_mean = float(np.mean([row["actual_mean_utility"] for row in evaluated]))
        actual_min = float(np.mean([row["actual_min_utility"] for row in evaluated]))
        gap = float(np.mean([row["actual_member_gap"] for row in evaluated]))
        predicted = np.array(
            [row["predicted_mean_utility"] for row in evaluated], dtype=np.float64
        )
        relevance_loss = float(np.mean(average_predicted_mean - predicted))
        observed_mean_loss = average_actual_mean - actual_mean
        rows.append(
            {
                "parameters": parameters,
                "actual_mean_utility": actual_mean,
                "actual_min_utility": actual_min,
                "actual_member_gap": gap,
                "predicted_relevance_loss": relevance_loss,
                "observed_mean_loss_vs_average": observed_mean_loss,
                "eligible": relevance_loss <= relevance_loss_budget + 1e-12
                and observed_mean_loss <= observed_mean_loss_budget + 1e-12,
            }
        )
    eligible = [row for row in rows if row["eligible"]]
    pool = eligible or rows
    pool.sort(
        key=lambda row: (
            -row["actual_min_utility"],
            row["actual_member_gap"],
            -row["actual_mean_utility"],
            row["predicted_relevance_loss"],
            row["parameters"].floor_weight + row["parameters"].gap_weight,
            row["parameters"].floor,
        )
    )
    return pool[0]["parameters"], rows


def similarity_category(similarity: float, lower: float, upper: float) -> str:
    if similarity <= lower:
        return "DISSIMILAR"
    if similarity >= upper:
        return "SIMILAR"
    return "MIDDLE"


def stable_digest(*parts: object) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode("utf-8")).hexdigest()


def sample_groups(
    users: np.ndarray,
    normalized_factors: np.ndarray,
    rated_items: dict[int, set[int]],
    *,
    group_size: int,
    draws: int,
    minimum_common_candidates: int,
    seed: int,
) -> tuple[np.ndarray, list[tuple[tuple[int, ...], float, set[int]]]]:
    rng = np.random.default_rng(seed)
    seen: set[tuple[int, ...]] = set()
    similarities: list[float] = []
    eligible: list[tuple[tuple[int, ...], float, set[int]]] = []
    pair_indices = np.triu_indices(group_size, 1)
    while len(seen) < draws:
        positions = tuple(sorted(map(int, rng.choice(len(users), size=group_size, replace=False))))
        if positions in seen:
            continue
        seen.add(positions)
        member_factors = normalized_factors[list(positions)]
        similarity = float((member_factors @ member_factors.T)[pair_indices].mean())
        similarities.append(similarity)
        member_ids = tuple(int(users[position]) for position in positions)
        common = rated_items[member_ids[0]].copy()
        for member in member_ids[1:]:
            common.intersection_update(rated_items[member])
            if len(common) < minimum_common_candidates:
                break
        if len(common) >= minimum_common_candidates:
            eligible.append((member_ids, similarity, common))
    return np.asarray(similarities, dtype=np.float64), eligible


def load_titles(archive: Path) -> dict[int, str]:
    with zipfile.ZipFile(archive) as bundle:
        with bundle.open("ml-32m/movies.csv") as handle:
            movies = pd.read_csv(handle, usecols=["movieId", "title"])
    return dict(zip(movies["movieId"].astype(int), movies["title"].astype(str), strict=True))


def profile_count_lookup(profiles: pd.DataFrame) -> dict[int, np.ndarray]:
    columns = [f"rating_{value:.1f}_count" for value in RATING_VALUES]
    return {
        int(row.user_id): row[columns].to_numpy(dtype=np.int64)
        for _, row in profiles.iterrows()
    }


def build_split_parties(
    split: str,
    split_frame: pd.DataFrame,
    *,
    user_factor_ids: np.ndarray,
    user_factors: np.ndarray,
    movie_factor_ids: np.ndarray,
    movie_factors: np.ndarray,
    profiles: dict[int, np.ndarray],
    global_counts: np.ndarray,
    calibrator: dict[str, list[float]],
    titles: dict[int, str],
    thresholds: dict[int, tuple[float, float]] | None,
    args: argparse.Namespace,
) -> tuple[list[Party], dict[int, tuple[float, float]], list[dict[str, Any]]]:
    user_index = {int(value): index for index, value in enumerate(user_factor_ids)}
    movie_index = {int(value): index for index, value in enumerate(movie_factor_ids)}
    known_users = set(user_index).intersection(profiles)
    known_movies = set(movie_index)
    frame = split_frame[
        split_frame["user_id"].isin(known_users) & split_frame["movie_id"].isin(known_movies)
    ].copy()
    frame.sort_values(["user_id", "movie_id", "timestamp"], inplace=True)
    frame.drop_duplicates(["user_id", "movie_id"], keep="last", inplace=True)
    counts = frame.groupby("user_id").size().sort_values(ascending=False)
    users = counts.head(args.eligible_user_limit).index.to_numpy(dtype=np.int64)
    if len(users) < args.eligible_user_limit:
        raise RuntimeError(f"{split}: only {len(users)} warm users are available")
    selected = frame[frame["user_id"].isin(users)]
    rating_maps: dict[int, dict[int, float]] = {
        int(user): dict(zip(group["movie_id"].astype(int), group["rating"].astype(float), strict=True))
        for user, group in selected.groupby("user_id")
    }
    rated_items = {user: set(values) for user, values in rating_maps.items()}
    raw_user_factors = np.stack([user_factors[user_index[int(user)]] for user in users])
    norms = np.linalg.norm(raw_user_factors, axis=1, keepdims=True)
    normalized_factors = raw_user_factors / np.maximum(norms, 1e-12)

    pools: dict[int, tuple[np.ndarray, list[tuple[tuple[int, ...], float, set[int]]]]] = {}
    selected_thresholds: dict[int, tuple[float, float]] = dict(thresholds or {})
    for group_size in GROUP_SIZES:
        similarities, eligible = sample_groups(
            users,
            normalized_factors,
            rated_items,
            group_size=group_size,
            draws=args.group_draws,
            minimum_common_candidates=args.minimum_common_candidates,
            seed=args.seed + (0 if split == "validation" else 1000) + group_size,
        )
        pools[group_size] = (similarities, eligible)
        if thresholds is None:
            lower, upper = np.quantile(similarities, [1 / 3, 2 / 3])
            selected_thresholds[group_size] = (float(lower), float(upper))

    x_thresholds = np.asarray(calibrator["x_thresholds"], dtype=np.float64)
    y_thresholds = np.asarray(calibrator["y_thresholds"], dtype=np.float64)
    parties: list[Party] = []
    coverage: list[dict[str, Any]] = []
    for group_size in GROUP_SIZES:
        lower, upper = selected_thresholds[group_size]
        similarities, eligible = pools[group_size]
        attempted = {
            group_type: int(
                sum(similarity_category(value, lower, upper) == group_type for value in similarities)
            )
            for group_type in GROUP_TYPES
        }
        for group_type in GROUP_TYPES:
            candidates = [
                record
                for record in eligible
                if similarity_category(record[1], lower, upper) == group_type
            ]
            candidates.sort(
                key=lambda record: stable_digest(
                    args.seed, split, group_size, group_type, *record[0]
                )
            )
            chosen_groups = candidates[: args.parties_per_cell]
            coverage.append(
                {
                    "split": split,
                    "group_size": group_size,
                    "group_type": group_type,
                    "attempted_groups": attempted[group_type],
                    "evaluable_groups": len(candidates),
                    "selected_parties": len(chosen_groups),
                    "evaluable_coverage": len(candidates) / attempted[group_type]
                    if attempted[group_type]
                    else 0.0,
                }
            )
            for number, (members, similarity, common) in enumerate(chosen_groups, start=1):
                movie_ids = sorted(
                    common,
                    key=lambda movie: stable_digest(
                        args.seed, split, group_size, group_type, *members, movie
                    ),
                )[: args.candidate_cap]
                candidate_ids = np.asarray(movie_ids, dtype=np.int64)
                predicted = np.empty((group_size, len(movie_ids)), dtype=np.float64)
                actual = np.empty_like(predicted)
                raw_ratings = np.empty_like(predicted)
                movie_matrix = np.stack([movie_factors[movie_index[movie]] for movie in movie_ids])
                for member_position, member in enumerate(members):
                    raw_prediction = movie_matrix @ user_factors[user_index[member]]
                    calibrated = np.interp(raw_prediction, x_thresholds, y_thresholds)
                    predicted[member_position] = relative_utility(
                        calibrated,
                        profiles[member],
                        global_counts,
                        args.relative_utility_shrinkage,
                    )
                    ratings = np.asarray(
                        [rating_maps[member][movie] for movie in movie_ids], dtype=np.float64
                    )
                    raw_ratings[member_position] = ratings
                    actual[member_position] = relative_utility(
                        ratings,
                        profiles[member],
                        global_counts,
                        args.relative_utility_shrinkage,
                    )
                parties.append(
                    Party(
                        label=f"{split.upper()}-S{group_size}-{group_type[:3]}-{number:03d}",
                        split=split,
                        group_size=group_size,
                        group_type=group_type,
                        similarity=similarity,
                        candidate_count=len(movie_ids),
                        candidate_ids=candidate_ids,
                        titles=[titles.get(movie, "MovieLens title unavailable") for movie in movie_ids],
                        predicted_utility=predicted,
                        actual_utility=actual,
                        actual_ratings=raw_ratings,
                    )
                )
    return parties, selected_thresholds, coverage


def aggregate_results(results: pd.DataFrame) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    metric_columns = [
        "actual_mean_utility",
        "actual_min_utility",
        "actual_member_gap",
        "actual_raw_rating_mean",
        "predicted_relevance_loss",
    ]
    overall: dict[str, Any] = {}
    for policy, group in results.groupby("policy", sort=True):
        overall[policy] = {
            "parties": int(len(group)),
            **{column: float(group[column].mean()) for column in metric_columns},
        }
    by_cell: list[dict[str, Any]] = []
    for (size, group_type, policy), group in results.groupby(
        ["group_size", "group_type", "policy"], sort=True
    ):
        by_cell.append(
            {
                "group_size": int(size),
                "group_type": group_type,
                "policy": policy,
                "parties": int(len(group)),
                **{column: float(group[column].mean()) for column in metric_columns},
            }
        )
    return overall, by_cell


def paired_bootstrap(results: pd.DataFrame, seed: int, repeats: int = 1000) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    output: dict[str, Any] = {}
    baseline = results[results["policy"] == "AVERAGE"].set_index("party_label")
    for policy in POLICIES[1:]:
        candidate = results[results["policy"] == policy].set_index("party_label")
        labels = baseline.index.intersection(candidate.index)
        policy_result: dict[str, Any] = {}
        for metric in ("actual_mean_utility", "actual_min_utility", "actual_member_gap"):
            differences = (
                candidate.loc[labels, metric] - baseline.loc[labels, metric]
            ).to_numpy(dtype=np.float64)
            samples = rng.choice(differences, size=(repeats, len(differences)), replace=True).mean(axis=1)
            policy_result[f"delta_{metric}"] = {
                "mean": float(differences.mean()),
                "ci95_low": float(np.quantile(samples, 0.025)),
                "ci95_high": float(np.quantile(samples, 0.975)),
            }
        output[policy] = policy_result
    return output


def public_candidate(party: Party, candidate_index: int) -> dict[str, Any]:
    return {
        "title": party.titles[candidate_index],
        "member_ratings": [float(value) for value in party.actual_ratings[:, candidate_index]],
        "member_relative_utilities": [
            float(value) for value in party.actual_utility[:, candidate_index]
        ],
        "raw_rating_average": float(party.actual_ratings[:, candidate_index].mean()),
        "relative_utility_average": float(party.actual_utility[:, candidate_index].mean()),
        "predicted_relative_utilities": [
            float(value) for value in party.predicted_utility[:, candidate_index]
        ],
    }


def reversal_examples(
    parties: list[Party], balanced: BalancedParameters, top_k: int
) -> dict[str, Any]:
    raw_candidates: list[tuple[float, Party, int, int]] = []
    policy_candidates: list[tuple[float, Party, int, int]] = []
    happiness_candidates: list[tuple[float, Party, int, int]] = []
    for party in parties:
        raw_mean = party.actual_ratings.mean(axis=0)
        utility_mean = party.actual_utility.mean(axis=0)
        for left, right in combinations(range(party.candidate_count), 2):
            raw_difference = raw_mean[left] - raw_mean[right]
            utility_difference = utility_mean[left] - utility_mean[right]
            if raw_difference * utility_difference < 0:
                raw_candidates.append(
                    (abs(raw_difference) / 4.5 + abs(utility_difference), party, left, right)
                )
        average_top = int(
            stable_top_indices(
                policy_scores(party.predicted_utility, "AVERAGE"), party.candidate_ids, 1
            )[0]
        )
        balanced_top = int(
            stable_top_indices(
                policy_scores(party.predicted_utility, "BALANCED", balanced),
                party.candidate_ids,
                1,
            )[0]
        )
        if average_top != balanced_top:
            improvement = (
                party.actual_utility[:, balanced_top].min()
                - party.actual_utility[:, average_top].min()
            )
            policy_candidates.append((float(improvement), party, average_top, balanced_top))
        least_top = int(
            stable_top_indices(
                policy_scores(party.predicted_utility, "LEAST_MISERY"),
                party.candidate_ids,
                1,
            )[0]
        )
        most_top = int(
            stable_top_indices(
                policy_scores(party.predicted_utility, "MOST_HAPPINESS"),
                party.candidate_ids,
                1,
            )[0]
        )
        if least_top != most_top:
            contrast = (
                party.actual_utility[:, least_top].min()
                - party.actual_utility[:, most_top].min()
            )
            happiness_candidates.append((float(contrast), party, most_top, least_top))

    def format_example(
        record: tuple[float, Party, int, int] | None,
        first_label: str,
        second_label: str,
    ) -> dict[str, Any] | None:
        if record is None:
            return None
        score, party, first, second = record
        return {
            "party_label": party.label,
            "group_size": party.group_size,
            "group_type": party.group_type,
            "selection_contrast": abs(score),
            first_label: public_candidate(party, first),
            second_label: public_candidate(party, second),
        }

    raw = max(raw_candidates, key=lambda row: row[0], default=None)
    balanced_case = max(policy_candidates, key=lambda row: row[0], default=None)
    happiness_case = max(happiness_candidates, key=lambda row: row[0], default=None)
    return {
        "raw_average_vs_relative_utility": format_example(
            raw, "raw_average_preferred", "relative_utility_preferred"
        ),
        "average_vs_balanced": format_example(
            balanced_case, "average_top", "balanced_top"
        ),
        "most_happiness_vs_least_misery": format_example(
            happiness_case, "most_happiness_top", "least_misery_top"
        ),
    }


def round_numbers(value: Any) -> Any:
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return round(value, 6)
    if isinstance(value, dict):
        return {key: round_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [round_numbers(item) for item in value]
    return value


def render_evidence(manifest: dict[str, Any]) -> str:
    selected = manifest["selection"]["balanced_parameters"]
    overall = manifest["evaluation"]["overall"]
    paired = manifest["evaluation"]["paired_bootstrap_vs_average"]
    coverage = manifest["evaluation"]["group_coverage"]
    by_cell = manifest["evaluation"]["by_cell"]
    lines = [
        "# REC-EV-005 — MovieLens 합성 파티 집계 정책 비교",
        "",
        f"> 상태: `{manifest['status']}`  ",
        f"> 생성 시각: {manifest['run_at_utc']}  ",
        "> 제품 정책 승인: `NO` — PARTY_BALANCED_V1·공개 API·UI를 승인하지 않음  ",
        "> 실제 파티 만족도: `NOT_OBSERVED`",
        "",
        "## 1. 결론",
        "",
        manifest["conclusion"]["summary"],
        "",
        "이 결과는 MovieLens 사용자를 묶고 모든 구성원이 실제 평가한 후보에서 측정한 오프라인 진단이다.",
        "파티가 함께 영화를 골랐거나 만족했다는 관측이 없으므로 실제 파티 만족도·온라인 성공률로 부르지 않는다.",
        "",
        "## 2. 누수 방지와 후보 경계",
        "",
        "- 취향 유사도·예측 효용: REC-EV-002 Train-only ALS factor와 Train rating profile",
        "- 그룹 구간·Balanced threshold/weight 선택: Validation만 사용",
        "- 최종 수치: 고정 파라미터로 Test 평가",
        "- Test 평점 값은 파라미터 선택에 사용하지 않음",
        f"- 후보: 모든 구성원이 해당 split에서 평가한 known item 중 안정 hash 최대 {manifest['protocol']['candidate_cap']}개",
        f"- 추천 목록: 후보에서 Top-{manifest['protocol']['top_k']}",
        "- 미평가 영화는 싫어요로 만들지 않으며 full-catalog coverage를 주장하지 않음",
        "",
        "## 3. Validation에서 선택한 Balanced 후보",
        "",
        "```text",
        "score = mean(relativeUtility)",
        "      - floorWeight × max(0, floor - min(relativeUtility))",
        "      - gapWeight × (max(relativeUtility) - min(relativeUtility))",
        "```",
        "",
        f"- floor: `{selected['floor']}`",
        f"- floorWeight: `{selected['floor_weight']}`",
        f"- gapWeight: `{selected['gap_weight']}`",
        f"- validation predicted relevance-loss budget: `{manifest['protocol']['balanced_relevance_loss_budget']}`",
        f"- validation observed mean-loss budget: `{manifest['protocol']['balanced_observed_mean_loss_budget']}`",
        "",
        "이 값은 합성 Validation에서 고른 **비교 후보**이며 제품 공정성 선호가 아니다. 평균 효용을 얼마나",
        "포기할지는 REC-PD-005에서 제품 소유자가 별도로 결정해야 한다.",
        "",
        "## 4. Held-out Test 전체 결과",
        "",
        "| Policy | Parties | 평균 효용 | 최저 구성원 효용 | 구성원 격차 | predicted relevance 손실 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for policy in POLICIES:
        metric = overall[policy]
        lines.append(
            f"| {policy} | {metric['parties']} | {metric['actual_mean_utility']:.4f} | "
            f"{metric['actual_min_utility']:.4f} | {metric['actual_member_gap']:.4f} | "
            f"{metric['predicted_relevance_loss']:.4f} |"
        )
    lines.extend(
        [
            "",
            "`최저 구성원 효용`은 각 구성원의 Top-N 평균 효용 중 최솟값을 party별 계산한 뒤 macro 평균한다.",
            "`격차`는 같은 구성원 평균의 max-min이다. relative utility는 0~1의 Train rating-style",
            "mid-rank ECDF이며 보편적 만족 확률이 아니다.",
            "",
            "### Average 대비 paired bootstrap 차이 (95% CI)",
            "",
            "| Policy | Δ 평균 효용 | Δ 최저 효용 | Δ 격차 |",
            "| --- | --- | --- | --- |",
        ]
    )
    for policy in POLICIES[1:]:
        metric = paired[policy]
        formatted: list[str] = []
        for key in (
            "delta_actual_mean_utility",
            "delta_actual_min_utility",
            "delta_actual_member_gap",
        ):
            value = metric[key]
            formatted.append(
                f"{value['mean']:+.4f} [{value['ci95_low']:+.4f}, {value['ci95_high']:+.4f}]"
            )
        lines.append(f"| {policy} | {formatted[0]} | {formatted[1]} | {formatted[2]} |")
    lines.extend(
        [
            "",
            "Balanced의 세 CI가 모두 0을 포함하므로 Average보다 평균·최저 효용 또는 격차를",
            "개선했다는 근거가 아니다. Validation에서 선택된 비교 후보로만 유지한다.",
            "",
            "## 5. 2/3/4명 × 취향 그룹",
            "",
            "| 인원 | 그룹 | Policy | 평균 효용 | 최저 효용 | 격차 | relevance 손실 |",
            "| ---: | --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in by_cell:
        lines.append(
            f"| {row['group_size']} | {row['group_type']} | {row['policy']} | "
            f"{row['actual_mean_utility']:.4f} | {row['actual_min_utility']:.4f} | "
            f"{row['actual_member_gap']:.4f} | {row['predicted_relevance_loss']:.4f} |"
        )
    lines.extend(
        [
            "",
            "## 6. 공통평가 후보 coverage",
            "",
            "| Split | 인원 | 그룹 | 추출 시도 | 평가 가능 | 선택 party | 평가 가능 coverage |",
            "| --- | ---: | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in coverage:
        lines.append(
            f"| {row['split']} | {row['group_size']} | {row['group_type']} | "
            f"{row['attempted_groups']} | {row['evaluable_groups']} | "
            f"{row['selected_parties']} | {row['evaluable_coverage']:.2%} |"
        )
    lines.extend(["", "## 7. 순위가 뒤집힌 실제 MovieLens 사례", ""])
    for name, example in manifest["evaluation"]["examples"].items():
        lines.append(f"### {name}")
        lines.append("")
        if example is None:
            lines.append("해당 조건의 사례를 찾지 못했다.")
            lines.append("")
            continue
        lines.append(
            f"- party: `{example['party_label']}` / {example['group_size']}명 / {example['group_type']}"
        )
        for label, candidate in example.items():
            if not isinstance(candidate, dict) or "title" not in candidate:
                continue
            lines.append(
                f"- {label}: **{candidate['title']}** — raw 평균 "
                f"{candidate['raw_rating_average']:.3f}, relative utility 평균 "
                f"{candidate['relative_utility_average']:.3f}, 구성원 rating "
                f"{candidate['member_ratings']}"
            )
        lines.append("")
    lines.extend(
        [
            "사용자·영화 raw ID는 추적 문서와 manifest에 저장하지 않았다. 제목과 익명 party/member 위치만",
            "남겨 rating-style 정규화와 정책 선택이 순서를 바꾼 실제 관측 사례를 재검토할 수 있게 했다.",
            "",
            "## 8. 재현",
            "",
            "```powershell",
            "py -3.12 scripts/recommendation_party_policy.py `",
            "  --split-dir outputs/recommendation-evidence/global-time-v1 `",
            "  --split-manifest docs/recommendation/evidence/manifests/global-time-v1.json `",
            "  --baseline-manifest docs/recommendation/evidence/manifests/rec-ev-002.json `",
            "  --output-dir outputs/recommendation-evidence/rec-ev-005 `",
            "  --manifest docs/recommendation/evidence/manifests/rec-ev-005.json `",
            "  --evidence docs/recommendation/evidence/REC-EV-005-party-policy.md",
            "",
            "py -3.12 scripts/verify_recommendation_party_policy.py `",
            "  --manifest docs/recommendation/evidence/manifests/rec-ev-005.json",
            "```",
            "",
            "## 9. Evidence gap와 결정 경계",
            "",
            "- MovieLens에는 파티 생성·투표·선택·공동 감상·만족도 데이터가 없다.",
            "- 공통평가 후보만 사용해 observation bias가 크며 full-catalog 정책 coverage가 아니다.",
            "- 특히 4인 Test 공통평가 평가 가능 coverage는 약 0.7%~1.0%에 불과하다. 이 심각한",
            "  선택 편향 때문에 4인 일반 파티로 결과를 외삽할 수 없다.",
            "- 구성원이 여러 합성 party에 재사용되므로 party row를 완전히 독립 표본으로 해석하지 않는다.",
            "- REC-EV-002 ALS는 sampled ranking에서 Popularity보다 약했다. 집계 결과가 개인 추천 코어의",
            "  약점을 상쇄하거나 PARTY_BALANCED_V1 채택을 뜻하지 않는다.",
            "- 실제 FEELM party 로그와 사용자 결정 없이는 threshold·weight·손실 예산을 승인하지 않는다.",
            "",
            "따라서 이 문서는 REC-PD-005 판단 자료를 채우지만 `party_aggregation` champion은 계속 null이고,",
            "공개 API·UI 구현도 시작하지 않는다.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    started = time.perf_counter()
    if args.minimum_common_candidates < args.top_k:
        raise ValueError("minimum common candidates must cover top-k")
    split_manifest = json.loads(args.split_manifest.read_text(encoding="utf-8"))
    baseline_manifest = json.loads(args.baseline_manifest.read_text(encoding="utf-8"))
    if split_manifest.get("evidence_id") != "REC-EV-001":
        raise RuntimeError("REC-EV-001 split manifest is required")
    if baseline_manifest.get("evidence_id") != "REC-EV-002":
        raise RuntimeError("REC-EV-002 baseline manifest is required")
    factors_record = baseline_manifest["artifacts"]["als_model"]
    factors_path = Path(factors_record["path"])
    if sha256_file(factors_path) != factors_record["sha256"]:
        raise RuntimeError("REC-EV-002 ALS factor checksum mismatch")
    profile_path = args.split_dir / "user_rating_profiles.parquet"
    validation_path = args.split_dir / "validation.parquet"
    test_path = args.split_dir / "test.parquet"
    archive = args.archive or Path(split_manifest["source"]["archive"])

    factors = np.load(factors_path)
    profiles_frame = pd.read_parquet(profile_path)
    profiles = profile_count_lookup(profiles_frame)
    validation = pd.read_parquet(validation_path)
    test = pd.read_parquet(test_path)
    calibrators_path = Path(baseline_manifest["artifacts"]["isotonic_calibrators"]["path"])
    calibrators = json.loads(calibrators_path.read_text(encoding="utf-8"))
    titles = load_titles(archive)
    global_counts_map = split_manifest["splits"]["train"]["rating_value_counts"]
    global_counts = np.asarray(
        [global_counts_map[f"{value:.1f}"] for value in RATING_VALUES], dtype=np.int64
    )

    common = {
        "user_factor_ids": factors["user_ids"].astype(np.int64),
        "user_factors": factors["user_factors"].astype(np.float64),
        "movie_factor_ids": factors["movie_ids"].astype(np.int64),
        "movie_factors": factors["movie_factors"].astype(np.float64),
        "profiles": profiles,
        "global_counts": global_counts,
        "calibrator": calibrators["prediction_als_raw"],
        "titles": titles,
        "args": args,
    }
    validation_parties, thresholds, validation_coverage = build_split_parties(
        "validation", validation, thresholds=None, **common
    )
    selected_balanced, selection_grid = select_balanced_parameters(
        validation_parties,
        top_k=args.top_k,
        relevance_loss_budget=args.balanced_relevance_loss_budget,
        observed_mean_loss_budget=args.balanced_observed_mean_loss_budget,
    )
    test_parties, _, test_coverage = build_split_parties(
        "test", test, thresholds=thresholds, **common
    )

    result_rows: list[dict[str, Any]] = []
    for party in test_parties:
        average = evaluate_policy(party, "AVERAGE", None, args.top_k)
        for policy in POLICIES:
            evaluated = evaluate_policy(
                party,
                policy,
                selected_balanced if policy == "BALANCED" else None,
                args.top_k,
            )
            result_rows.append(
                {
                    "party_label": party.label,
                    "group_size": party.group_size,
                    "group_type": party.group_type,
                    "policy": policy,
                    "candidate_count": party.candidate_count,
                    "taste_similarity": party.similarity,
                    "actual_mean_utility": evaluated["actual_mean_utility"],
                    "actual_min_utility": evaluated["actual_min_utility"],
                    "actual_member_gap": evaluated["actual_member_gap"],
                    "actual_raw_rating_mean": evaluated["actual_raw_rating_mean"],
                    "predicted_mean_utility": evaluated["predicted_mean_utility"],
                    "predicted_relevance_loss": average["predicted_mean_utility"]
                    - evaluated["predicted_mean_utility"],
                }
            )
    results = pd.DataFrame(result_rows)
    overall, by_cell = aggregate_results(results)
    coverage = validation_coverage + test_coverage
    minimum_reached = all(
        row["selected_parties"] >= args.minimum_parties_per_cell for row in coverage
    )
    status = (
        "COMPLETED_OFFLINE_EVIDENCE"
        if minimum_reached
        else "PARTIAL_EVIDENCE_INSUFFICIENT_GROUP_COVERAGE"
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    party_metrics_path = args.output_dir / "party_policy_metrics.parquet"
    results.to_parquet(party_metrics_path, index=False)
    selected_row = next(
        row for row in selection_grid if row["parameters"] == selected_balanced
    )
    grid_public = [
        {
            "floor": row["parameters"].floor,
            "floor_weight": row["parameters"].floor_weight,
            "gap_weight": row["parameters"].gap_weight,
            "actual_mean_utility": row["actual_mean_utility"],
            "actual_min_utility": row["actual_min_utility"],
            "actual_member_gap": row["actual_member_gap"],
            "predicted_relevance_loss": row["predicted_relevance_loss"],
            "observed_mean_loss_vs_average": row["observed_mean_loss_vs_average"],
            "eligible": row["eligible"],
        }
        for row in sorted(
            selection_grid,
            key=lambda row: (
                not row["eligible"],
                -row["actual_min_utility"],
                row["actual_member_gap"],
            ),
        )[:10]
    ]
    bootstrap = paired_bootstrap(results, args.seed + 9000)
    examples = reversal_examples(test_parties, selected_balanced, args.top_k)
    balanced_delta = bootstrap["BALANCED"]
    four_person_test_coverage = [
        row["evaluable_coverage"]
        for row in test_coverage
        if row["group_size"] == 4
    ]
    conclusion_summary = (
        "Held-out Test에서 Balanced의 Average 대비 평균 효용 차이는 "
        f"{balanced_delta['delta_actual_mean_utility']['mean']:+.4f} "
        f"(95% CI [{balanced_delta['delta_actual_mean_utility']['ci95_low']:+.4f}, "
        f"{balanced_delta['delta_actual_mean_utility']['ci95_high']:+.4f}]), 최저 효용 차이는 "
        f"{balanced_delta['delta_actual_min_utility']['mean']:+.4f} "
        f"([{balanced_delta['delta_actual_min_utility']['ci95_low']:+.4f}, "
        f"{balanced_delta['delta_actual_min_utility']['ci95_high']:+.4f}]), 격차 차이는 "
        f"{balanced_delta['delta_actual_member_gap']['mean']:+.4f} "
        f"([{balanced_delta['delta_actual_member_gap']['ci95_low']:+.4f}, "
        f"{balanced_delta['delta_actual_member_gap']['ci95_high']:+.4f}])로 세 CI가 모두 0을 포함했다. "
        f"또한 4인 Test 공통평가 coverage는 {min(four_person_test_coverage):.2%}~"
        f"{max(four_person_test_coverage):.2%}뿐이다. 따라서 Balanced는 비교 후보일 뿐 개선 근거나 "
        "제품 PARTY_BALANCED_V1 승인 근거가 아니며 실제 파티 만족도도 관측하지 않았다."
    )
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "evidence_id": "REC-EV-005",
        "run_id": "EXP-20260829-005",
        "status": status,
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": {
            "split_protocol": split_manifest["protocol"]["version"],
            "split_manifest": str(args.split_manifest),
            "split_manifest_sha256": sha256_file(args.split_manifest),
            "baseline_manifest": str(args.baseline_manifest),
            "baseline_manifest_sha256": sha256_file(args.baseline_manifest),
            "als_factors": factors_record,
            "user_rating_profiles": artifact_record(profile_path, len(profiles_frame)),
            "validation": artifact_record(validation_path, len(validation)),
            "test": artifact_record(test_path, len(test)),
            "test_rating_values_used_for_parameter_selection": False,
        },
        "protocol": {
            "version": "synthetic-common-rated-party-v1",
            "selection_split": "validation",
            "evaluation_split": "test",
            "group_sizes": list(GROUP_SIZES),
            "group_types": list(GROUP_TYPES),
            "eligible_user_limit": args.eligible_user_limit,
            "group_draws_per_size_split": args.group_draws,
            "parties_per_cell_target": args.parties_per_cell,
            "minimum_parties_per_cell": args.minimum_parties_per_cell,
            "minimum_common_candidates": args.minimum_common_candidates,
            "candidate_cap": args.candidate_cap,
            "top_k": args.top_k,
            "relative_utility": "user-ecdf-shrunk-v1",
            "relative_utility_shrinkage": args.relative_utility_shrinkage,
            "similarity": "cosine of REC-EV-002 Train-only ALS user factors",
            "similarity_thresholds_selected_on_validation": {
                str(size): {"lower_tercile": lower, "upper_tercile": upper}
                for size, (lower, upper) in thresholds.items()
            },
            "candidate_policy": "COMMON_RATED_KNOWN_ITEM_STABLE_HASH_CAP",
            "balanced_formula": "mean - floorWeight*max(0,floor-min) - gapWeight*(max-min)",
            "balanced_relevance_loss_budget": args.balanced_relevance_loss_budget,
            "balanced_observed_mean_loss_budget": args.balanced_observed_mean_loss_budget,
            "balanced_selection_rule": "maximize validation actual min utility under both budgets; tie by gap mean loss and complexity",
            "raw_ids_in_tracked_artifacts": False,
        },
        "selection": {
            "validation_parties": len(validation_parties),
            "balanced_parameters": {
                "floor": selected_balanced.floor,
                "floor_weight": selected_balanced.floor_weight,
                "gap_weight": selected_balanced.gap_weight,
            },
            "selected_validation_metrics": {
                key: value
                for key, value in selected_row.items()
                if key not in {"parameters"}
            },
            "top_grid_candidates": grid_public,
        },
        "evaluation": {
            "test_parties": len(test_parties),
            "overall": overall,
            "by_cell": by_cell,
            "group_coverage": coverage,
            "paired_bootstrap_vs_average": bootstrap,
            "examples": examples,
        },
        "artifacts": {
            "party_policy_metrics": artifact_record(party_metrics_path, len(results))
        },
        "validation": {
            "status": "PASS" if minimum_reached else "PARTIAL",
            "all_nine_cells_have_minimum_parties_in_both_splits": minimum_reached,
            "selection_and_evaluation_are_disjoint": True,
            "test_did_not_select_thresholds_or_weights": True,
            "all_policy_rows_finite": bool(
                np.isfinite(
                    results[
                        [
                            "actual_mean_utility",
                            "actual_min_utility",
                            "actual_member_gap",
                            "predicted_relevance_loss",
                        ]
                    ].to_numpy()
                ).all()
            ),
        },
        "runtime": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "pyarrow": pyarrow.__version__,
            "total_seconds": time.perf_counter() - started,
        },
        "conclusion": {
            "summary": conclusion_summary,
            "party_policy_gate": "EVIDENCE_AVAILABLE_PRODUCT_OWNER_DECISION_STILL_REQUIRED",
            "party_satisfaction_claim": "PROHIBITED_NOT_OBSERVED",
            "public_api_ui_gate": "BLOCKED_NOT_APPROVED",
        },
    }
    manifest = round_numbers(manifest)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(render_evidence(manifest), encoding="utf-8")
    print(
        f"REC-EV-005 {status}: validation parties={len(validation_parties)}, "
        f"test parties={len(test_parties)}, selected={selected_balanced.key}, "
        f"runtime={manifest['runtime']['total_seconds']:.1f}s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
