from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from recommendation_baseline_calibration import (
    build_profile_count_matrix,
    predict_popularity,
    rating_midrank_ecdf,
    safe_take,
    sample_ranking_candidates,
)

SEED = 42
NEGATIVES = 199
TOP_K = 10
LOSS_BUDGETS = (0.0, 0.01, 0.03, 0.05)
HYBRID_ALPHAS = (0.25, 0.5, 0.75)
EXPLORATION_WEIGHTS = (0.05, 0.1, 0.2, 0.3)
PROTOCOL = "rec-ev-004-sampled-exploration-pareto-v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="REC-EV-004 sampled Hybrid/exploration Pareto evidence")
    parser.add_argument("--split-manifest", type=Path, required=True)
    parser.add_argument("--baseline-manifest", type=Path, required=True)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--tracked-result", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--reason-provenance", type=Path, required=True)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact(path: Path, rows: int | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(path), "sha256": sha256(path), "bytes": path.stat().st_size}
    if rows is not None:
        result["rows"] = rows
    return result


def exact_artifact(record: dict[str, Any]) -> Path:
    path = Path(record["path"])
    if not path.is_file() or sha256(path) != record["sha256"]:
        raise RuntimeError("fixed split artifact checksum mismatch")
    return path


def load_genres(archive: Path, movie_size: int) -> tuple[np.ndarray, list[str], np.ndarray]:
    with zipfile.ZipFile(archive) as source:
        name = next(name for name in source.namelist() if name.endswith("/movies.csv"))
        with source.open(name) as stream:
            movies = pd.read_csv(stream, usecols=["movieId", "genres"])
    vocabulary = sorted({genre for value in movies["genres"] for genre in str(value).split("|")
                         if genre and genre != "(no genres listed)"})
    index = {genre: position for position, genre in enumerate(vocabulary)}
    matrix = np.zeros((movie_size, len(vocabulary)), dtype=np.float32)
    available = np.zeros(movie_size, dtype=bool)
    for row in movies.itertuples(index=False):
        movie_id = int(row.movieId)
        if movie_id < 0 or movie_id >= movie_size:
            continue
        values = [genre for genre in str(row.genres).split("|") if genre in index]
        if values:
            matrix[movie_id, [index[value] for value in values]] = 1.0
            available[movie_id] = True
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = np.divide(matrix, norms, out=np.zeros_like(matrix), where=norms > 0)
    return matrix, vocabulary, available


def user_genre_profiles(
    train: pd.DataFrame, users: np.ndarray, genre_matrix: np.ndarray, *, first_k: int | None = None
) -> tuple[np.ndarray, np.ndarray]:
    selected = train.loc[train["user_id"].isin(users), ["user_id", "movie_id", "rating", "timestamp"]].copy()
    selected = selected.sort_values(["user_id", "timestamp", "movie_id"], kind="stable")
    if first_k is not None:
        selected = selected.groupby("user_id", sort=False).head(first_k)
    ordered_users = np.sort(np.unique(users))
    positions = pd.Series(np.arange(len(ordered_users)), index=ordered_users)
    user_positions = positions.loc[selected["user_id"].to_numpy()].to_numpy(dtype=np.int64)
    movie_ids = selected["movie_id"].to_numpy(dtype=np.int64)
    ratings = selected["rating"].to_numpy(dtype=np.float64)
    means = selected.groupby("user_id")["rating"].transform("mean").to_numpy(dtype=np.float64)
    centered = ratings - means
    preference = np.zeros((len(ordered_users), genre_matrix.shape[1]), dtype=np.float64)
    exposure = np.zeros_like(preference)
    np.add.at(preference, user_positions, genre_matrix[movie_ids] * centered[:, None])
    np.add.at(exposure, user_positions, genre_matrix[movie_ids])
    pref_norm = np.linalg.norm(preference, axis=1, keepdims=True)
    preference = np.divide(preference, pref_norm, out=np.zeros_like(preference), where=pref_norm > 0)
    exposure_sum = exposure.sum(axis=1, keepdims=True)
    exposure = np.divide(exposure, exposure_sum, out=np.zeros_like(exposure), where=exposure_sum > 0)
    return preference, exposure


def percentile_by_user(frame: pd.DataFrame, column: str) -> np.ndarray:
    return frame.groupby("user_id")[column].rank(method="average", pct=True).to_numpy(dtype=np.float64)


def make_candidates(
    train: pd.DataFrame,
    heldout: pd.DataFrame,
    user_counts: np.ndarray,
    movie_counts: np.ndarray,
    profile_matrix: np.ndarray,
    profile_totals: np.ndarray,
    *,
    seed: int,
) -> pd.DataFrame:
    users = heldout["user_id"].to_numpy(dtype=np.int64)
    relative = np.full(len(heldout), np.nan, dtype=np.float64)
    known = safe_take(user_counts, users) > 0
    relative[known] = rating_midrank_ecdf(
        heldout.loc[known, "rating"].to_numpy(dtype=np.float64), users[known],
        profile_matrix, profile_totals, profile_matrix.sum(axis=0), shrinkage=20.0,
    )
    return sample_ranking_candidates(
        train["user_id"].to_numpy(dtype=np.int64),
        train["movie_id"].to_numpy(dtype=np.int64), heldout, np.ones(len(heldout), dtype=bool),
        user_counts, movie_counts, relative, negatives=NEGATIVES, seed=seed,
    )


def score_candidates(
    candidates: pd.DataFrame,
    profiles: np.ndarray,
    profile_users: np.ndarray,
    genre_matrix: np.ndarray,
    movie_counts: np.ndarray,
    movie_sums: np.ndarray,
    global_mean: float,
) -> pd.DataFrame:
    frame = candidates.copy()
    movies = frame["movie_id"].to_numpy(dtype=np.int64)
    frame["popularity"] = predict_popularity(movies, global_mean, movie_counts, movie_sums, prior=50.0)
    positions = pd.Series(np.arange(len(profile_users)), index=profile_users)
    user_positions = positions.loc[frame["user_id"].to_numpy()].to_numpy(dtype=np.int64)
    frame["content"] = np.einsum("ij,ij->i", profiles[user_positions], genre_matrix[movies])
    frame["pop_pct"] = percentile_by_user(frame, "popularity")
    frame["content_pct"] = percentile_by_user(frame, "content")
    total = float(movie_counts.sum())
    vocabulary = int(np.count_nonzero(movie_counts))
    frame["novelty"] = -np.log2((movie_counts[movies] + 1.0) / (total + vocabulary))
    frame["novelty_pct"] = percentile_by_user(frame, "novelty")
    for alpha in HYBRID_ALPHAS:
        frame[f"hybrid_{int(alpha * 100):02d}"] = (1.0 - alpha) * frame["pop_pct"] + alpha * frame["content_pct"]
    return frame


def base_order(frame: pd.DataFrame, score: str) -> dict[int, list[int]]:
    ordered = frame.sort_values(["user_id", score, "movie_id"], ascending=[True, False, True], kind="stable")
    return {int(user): group["movie_id"].astype(int).tolist() for user, group in ordered.groupby("user_id", sort=True)}


def greedy_exploration_order(
    frame: pd.DataFrame, base_score: str, weight: float, genre_matrix: np.ndarray,
    *, base_enabled: bool = True, novelty_enabled: bool = True, diversity_enabled: bool = True,
) -> dict[int, list[int]]:
    result: dict[int, list[int]] = {}
    for user, group in frame.groupby("user_id", sort=True):
        movie_ids = group["movie_id"].to_numpy(dtype=np.int64)
        base = group[base_score].to_numpy(dtype=np.float64)
        novelty = group["novelty_pct"].to_numpy(dtype=np.float64)
        remaining = np.arange(len(group), dtype=np.int64)
        selected: list[int] = []
        while len(remaining) and len(selected) < TOP_K:
            if not selected or not diversity_enabled:
                diversity = np.zeros(len(remaining), dtype=np.float64)
            else:
                similarities = genre_matrix[movie_ids[selected]] @ genre_matrix[movie_ids[remaining]].T
                diversity = 1.0 - np.max(similarities, axis=0)
            novelty_component = novelty[remaining] if novelty_enabled else 0.0
            base_component = base[remaining] if base_enabled else 0.0
            scores = ((1.0 - weight) * base_component
                      + weight * (0.5 * novelty_component + 0.5 * diversity))
            best_position = int(np.lexsort((movie_ids[remaining], -scores))[0])
            best_idx = int(remaining[best_position])
            selected.append(best_idx)
            remaining = np.delete(remaining, best_position)
        tail = remaining[np.lexsort((movie_ids[remaining], -base[remaining]))]
        combined = np.concatenate([np.asarray(selected, dtype=np.int64), tail])
        result[int(user)] = movie_ids[combined].astype(int).tolist()
    return result


def build_reason_analysis(
    candidates: pd.DataFrame,
    full_order: dict[int, list[int]],
    base_score: str,
    weight: float,
    genre_matrix: np.ndarray,
    exposure: np.ndarray,
    profile_users: np.ndarray,
    movie_counts: np.ndarray,
) -> dict[str, Any]:
    no_novelty = greedy_exploration_order(
        candidates, base_score, weight, genre_matrix, novelty_enabled=False
    )
    no_diversity = greedy_exploration_order(
        candidates, base_score, weight, genre_matrix, diversity_enabled=False
    )
    no_popularity = greedy_exploration_order(
        candidates, base_score, weight, genre_matrix, base_enabled=False
    )
    base = base_order(candidates, base_score)
    ablation_orders = {
        "FULL": full_order, "WITHOUT_NOVELTY": no_novelty,
        "WITHOUT_DIVERSITY": no_diversity, "WITHOUT_POPULARITY": no_popularity, "BASE_ONLY": base,
    }
    ablation_metrics = {
        name: evaluate(candidates, order, genre_matrix, exposure, profile_users, movie_counts)[0]
        for name, order in ablation_orders.items()
    }
    total = len(full_order) * TOP_K
    popularity_effect = novelty_positive = novelty_effect = diversity_positive = diversity_effect = exploration_effect = 0
    by_position = {str(position): {"recommendations": 0, "novelty_rank_effect": 0,
                                  "diversity_rank_effect": 0, "popularity_rank_effect": 0}
                   for position in range(1, TOP_K + 1)}
    indexed = candidates.set_index(["user_id", "movie_id"], verify_integrity=True)
    for user, movies in full_order.items():
        full_top = movies[:TOP_K]
        novelty_positions = {movie: position for position, movie in enumerate(no_novelty[user][:TOP_K], 1)}
        diversity_positions = {movie: position for position, movie in enumerate(no_diversity[user][:TOP_K], 1)}
        popularity_positions = {movie: position for position, movie in enumerate(no_popularity[user][:TOP_K], 1)}
        base_positions = {movie: position for position, movie in enumerate(base[user][:TOP_K], 1)}
        selected: list[int] = []
        for position, movie in enumerate(full_top, 1):
            row = indexed.loc[(user, movie)]
            novelty_contribution = weight * 0.5 * float(row["novelty_pct"])
            if not selected:
                marginal_diversity = 0.0
            else:
                marginal_diversity = float(1.0 - np.max(
                    genre_matrix[np.asarray(selected)] @ genre_matrix[movie]
                ))
            diversity_contribution = weight * 0.5 * marginal_diversity
            novelty_changed = novelty_positions.get(movie) != position
            diversity_changed = diversity_positions.get(movie) != position
            popularity_changed = popularity_positions.get(movie) != position
            exploration_changed = base_positions.get(movie) != position
            novelty_positive += int(novelty_contribution > 0)
            novelty_effect += int(novelty_changed)
            diversity_positive += int(diversity_contribution > 0)
            diversity_effect += int(diversity_changed)
            popularity_effect += int(popularity_changed)
            exploration_effect += int(exploration_changed)
            by_position[str(position)]["recommendations"] += 1
            by_position[str(position)]["novelty_rank_effect"] += int(novelty_changed)
            by_position[str(position)]["diversity_rank_effect"] += int(diversity_changed)
            by_position[str(position)]["popularity_rank_effect"] += int(popularity_changed)
            selected.append(movie)
    return {
        "policy": f"EXPLORE_{int(weight * 100):02d}",
        "recommendations": total,
        "reason_coverage": {
            "POPULARITY_BASELINE": {"positive_contribution": total, "rank_effect": popularity_effect},
            "LESS_POPULAR_DISCOVERY": {"positive_contribution": novelty_positive, "rank_effect": novelty_effect},
            "LIST_DIVERSITY": {"positive_contribution": diversity_positive, "rank_effect": diversity_effect},
            "GENRE_AFFINITY": {"positive_contribution": 0, "rank_effect": 0},
        },
        "exploration_changed_position": exploration_effect,
        "by_position": by_position,
        "ablation_metrics": ablation_metrics,
    }


def genre_diversity(movie_ids: list[int], genre_matrix: np.ndarray) -> float:
    if len(movie_ids) < 2:
        return 0.0
    values = genre_matrix[np.asarray(movie_ids, dtype=np.int64)]
    similarities = values @ values.T
    upper = similarities[np.triu_indices(len(movie_ids), 1)]
    return float(np.mean(1.0 - upper))


def js_distance(left: np.ndarray, right: np.ndarray) -> float | None:
    if left.sum() <= 0 or right.sum() <= 0:
        return None
    left = left / left.sum()
    right = right / right.sum()
    middle = 0.5 * (left + right)
    def kl(value: np.ndarray) -> float:
        mask = value > 0
        return float(np.sum(value[mask] * np.log2(value[mask] / middle[mask])))
    return math.sqrt(max(0.0, 0.5 * kl(left) + 0.5 * kl(right)))


def evaluate(
    candidates: pd.DataFrame,
    order: dict[int, list[int]],
    genre_matrix: np.ndarray,
    exposure_profiles: np.ndarray,
    profile_users: np.ndarray,
    movie_counts: np.ndarray,
) -> tuple[dict[str, Any], pd.DataFrame]:
    positives = candidates.loc[candidates["is_positive"] == 1].set_index("user_id")["movie_id"].to_dict()
    positions = {int(user): idx for idx, user in enumerate(profile_users)}
    ranked: list[dict[str, Any]] = []
    unique: set[int] = set()
    known_movies = np.flatnonzero(movie_counts > 0)
    head_count = max(1, int(math.ceil(len(known_movies) * 0.2)))
    head = set(known_movies[np.argsort(movie_counts[known_movies], kind="stable")[-head_count:]].tolist())
    for user in sorted(order):
        movies = order[user]
        positive = int(positives[user])
        rank = movies.index(positive) + 1
        top = movies[:TOP_K]
        unique.update(top)
        list_profile = genre_matrix[np.asarray(top)].sum(axis=0)
        calibration = js_distance(exposure_profiles[positions[user]], list_profile)
        ranked.append({
            "user_id": user,
            "rank": rank,
            "ndcg": 1.0 / math.log2(rank + 1) if rank <= TOP_K else 0.0,
            "recall": 1.0 if rank <= TOP_K else 0.0,
            "novelty": float(np.mean(-np.log2((movie_counts[np.asarray(top)] + 1.0) /
                                               (movie_counts.sum() + len(known_movies))))),
            "diversity": genre_diversity(top, genre_matrix),
            "long_tail": float(np.mean([movie not in head for movie in top])),
            "calibration_distance": calibration,
        })
    per_user = pd.DataFrame(ranked)
    metrics = {
        "users": len(per_user),
        "user_coverage": round(len(per_user) / len(positives), 6),
        "ndcg_at_10": round(float(per_user["ndcg"].mean()), 6),
        "recall_at_10": round(float(per_user["recall"].mean()), 6),
        "novelty_bits": round(float(per_user["novelty"].mean()), 6),
        "intra_list_diversity": round(float(per_user["diversity"].mean()), 6),
        "catalog_coverage": round(len(unique) / len(known_movies), 6),
        "long_tail_exposure": round(float(per_user["long_tail"].mean()), 6),
        "genre_calibration_distance": round(float(per_user["calibration_distance"].dropna().mean()), 6),
        "genre_calibration_coverage": round(float(per_user["calibration_distance"].notna().mean()), 6),
    }
    return metrics, per_user


def pareto_front(metrics: dict[str, dict[str, Any]]) -> list[str]:
    maximize = ("ndcg_at_10", "novelty_bits", "intra_list_diversity", "catalog_coverage", "long_tail_exposure")
    minimize = ("genre_calibration_distance",)
    names = sorted(metrics)
    front: list[str] = []
    for candidate in names:
        dominated = False
        for other in names:
            if other == candidate:
                continue
            no_worse = all(metrics[other][key] >= metrics[candidate][key] for key in maximize) and all(
                metrics[other][key] <= metrics[candidate][key] for key in minimize
            )
            strictly = any(metrics[other][key] > metrics[candidate][key] for key in maximize) or any(
                metrics[other][key] < metrics[candidate][key] for key in minimize
            )
            if no_worse and strictly:
                dominated = True
                break
        if not dominated:
            front.append(candidate)
    return front


def select_for_budgets(metrics: dict[str, dict[str, Any]], front: list[str]) -> dict[str, dict[str, Any]]:
    baseline = metrics["POPULARITY"]["ndcg_at_10"]
    selected: dict[str, dict[str, Any]] = {}
    for budget in LOSS_BUDGETS:
        feasible = [name for name in front if metrics[name]["ndcg_at_10"] >= baseline * (1.0 - budget)]
        if not feasible:
            selected[f"{budget:.0%}"] = {"policy": None, "validation_status": "NO_FEASIBLE_PARETO_POLICY"}
            continue
        chosen = max(feasible, key=lambda name: (
            metrics[name]["novelty_bits"], metrics[name]["intra_list_diversity"],
            metrics[name]["catalog_coverage"], metrics[name]["long_tail_exposure"], name,
        ))
        selected[f"{budget:.0%}"] = {
            "policy": chosen,
            "validation_status": "LOCKED_BEFORE_TEST",
            "realized_ndcg_loss": round((baseline - metrics[chosen]["ndcg_at_10"]) / baseline, 6),
        }
    return selected


def segments(per_user: pd.DataFrame, train_counts: dict[int, int], positive_counts: dict[int, int]) -> dict[str, Any]:
    frame = per_user.copy()
    frame["history"] = frame["user_id"].map(lambda user: train_counts.get(int(user), 0))
    frame["history_segment"] = pd.cut(frame["history"], [0, 49, 99, np.inf], labels=["K20_49", "K50_99", "K100_PLUS"])
    values = np.asarray(list(positive_counts.values()), dtype=np.float64)
    q1, q2, q3 = np.quantile(values, [0.25, 0.5, 0.75]) if len(values) else (0, 0, 0)
    def popularity_segment(user: int) -> str:
        count = positive_counts.get(int(user), 0)
        return "P1_LONG_TAIL" if count <= q1 else "P2" if count <= q2 else "P3" if count <= q3 else "P4_HEAD"
    frame["positive_popularity_segment"] = frame["user_id"].map(popularity_segment)
    result: dict[str, Any] = {}
    for column in ("history_segment", "positive_popularity_segment"):
        result[column] = {}
        for label, group in frame.groupby(column, observed=True):
            result[column][str(label)] = {
                "users": len(group), "ndcg_at_10": round(float(group["ndcg"].mean()), 6),
                "recall_at_10": round(float(group["recall"].mean()), 6),
                "novelty_bits": round(float(group["novelty"].mean()), 6),
                "diversity": round(float(group["diversity"].mean()), 6),
                "long_tail_exposure": round(float(group["long_tail"].mean()), 6),
            }
    return result


def deidentified_failures(pop: pd.DataFrame, chosen: pd.DataFrame, train_counts: dict[int, int]) -> list[dict[str, Any]]:
    merged = pop[["user_id", "rank"]].merge(chosen[["user_id", "rank"]], on="user_id", suffixes=("_pop", "_candidate"))
    merged["rank_regression"] = merged["rank_candidate"] - merged["rank_pop"]
    merged = merged.sort_values(["rank_regression", "rank_pop"], ascending=[False, True]).head(5)
    return [{
        "case": index + 1,
        "history_segment": "K20_49" if train_counts.get(int(row.user_id), 0) < 50 else
                           "K50_99" if train_counts.get(int(row.user_id), 0) < 100 else "K100_PLUS",
        "popularity_positive_rank": int(row.rank_pop),
        "candidate_positive_rank": int(row.rank_candidate),
        "rank_regression": int(row.rank_regression),
    } for index, row in enumerate(merged.itertuples(index=False))]


def paired_ndcg_difference(
    baseline: pd.DataFrame, candidate: pd.DataFrame, *, seed: int, repeats: int = 1000
) -> dict[str, Any]:
    paired = baseline[["user_id", "ndcg"]].merge(
        candidate[["user_id", "ndcg"]], on="user_id", suffixes=("_baseline", "_candidate"), validate="one_to_one"
    )
    differences = paired["ndcg_candidate"].to_numpy() - paired["ndcg_baseline"].to_numpy()
    rng = np.random.default_rng(seed)
    bootstrap = np.empty(repeats, dtype=np.float64)
    for index in range(repeats):
        bootstrap[index] = float(np.mean(rng.choice(differences, size=len(differences), replace=True)))
    return {
        "users": len(differences),
        "mean_difference": round(float(np.mean(differences)), 6),
        "ci95_low": round(float(np.quantile(bootstrap, 0.025)), 6),
        "ci95_high": round(float(np.quantile(bootstrap, 0.975)), 6),
        "bootstrap_repeats": repeats,
    }


def run() -> None:
    args = parse_args()
    started = time.perf_counter()
    split_manifest = json.loads(args.split_manifest.read_text(encoding="utf-8"))
    baseline_manifest = json.loads(args.baseline_manifest.read_text(encoding="utf-8"))
    if baseline_manifest.get("evidence_id") != "REC-EV-002" or (
        baseline_manifest["source"]["split_manifest_sha256"] != sha256(args.split_manifest)
    ):
        raise RuntimeError("REC-EV-002 baseline is not bound to the fixed split")
    archive = args.archive or Path(split_manifest["source"]["archive"])
    if sha256(archive) != split_manifest["source"]["archive_sha256"]:
        raise RuntimeError("MovieLens archive checksum mismatch")
    train_path = exact_artifact(split_manifest["artifacts"]["train"])
    validation_path = exact_artifact(split_manifest["artifacts"]["validation"])
    test_path = exact_artifact(split_manifest["artifacts"]["test"])
    train = pd.read_parquet(train_path, columns=["user_id", "movie_id", "rating", "timestamp"])
    validation = pd.read_parquet(validation_path, columns=["user_id", "movie_id", "rating", "timestamp"])
    test = pd.read_parquet(test_path, columns=["user_id", "movie_id", "rating", "timestamp"])
    bias_path = exact_artifact(baseline_manifest["artifacts"]["bias_parameters"])
    bias = np.load(bias_path, allow_pickle=False)
    movie_counts = bias["movie_counts"].astype(np.int64, copy=False)
    movie_sums = bias["movie_sums"].astype(np.float64, copy=False)
    user_counts = bias["user_counts"].astype(np.int64, copy=False)
    global_mean = float(bias["global_mean"])
    movie_size, user_size = len(movie_counts), len(user_counts)
    train_users = train["user_id"].to_numpy(dtype=np.int64)
    train_movies = train["movie_id"].to_numpy(dtype=np.int64)
    train_ratings = train["rating"].to_numpy(dtype=np.float64)
    if int(user_counts.sum()) != len(train) or int(movie_counts.sum()) != len(train):
        raise RuntimeError("REC-EV-002 sufficient statistics differ from fixed Train")
    profile_matrix, profile_totals = build_profile_count_matrix(train_users, train_ratings, user_size)
    genre_matrix, genres, genre_available = load_genres(archive, movie_size)

    validation_candidates = make_candidates(train, validation, user_counts, movie_counts, profile_matrix, profile_totals, seed=SEED)
    test_candidates = make_candidates(train, test, user_counts, movie_counts, profile_matrix, profile_totals, seed=SEED + 10_000)
    all_users = np.sort(np.unique(np.concatenate([
        validation_candidates["user_id"].unique(), test_candidates["user_id"].unique()
    ]))).astype(np.int64)
    profiles, exposure = user_genre_profiles(train, all_users, genre_matrix)
    validation_scored = score_candidates(validation_candidates, profiles, all_users, genre_matrix, movie_counts, movie_sums, global_mean)
    test_scored = score_candidates(test_candidates, profiles, all_users, genre_matrix, movie_counts, movie_sums, global_mean)

    base_columns = {"POPULARITY": "pop_pct", "CONTENT_GENRE": "content_pct"}
    base_columns.update({f"HYBRID_CONTENT_{int(alpha * 100):02d}": f"hybrid_{int(alpha * 100):02d}" for alpha in HYBRID_ALPHAS})
    validation_orders = {name: base_order(validation_scored, column) for name, column in base_columns.items()}
    validation_metrics: dict[str, dict[str, Any]] = {}
    validation_per_user: dict[str, pd.DataFrame] = {}
    for name, order in validation_orders.items():
        validation_metrics[name], validation_per_user[name] = evaluate(
            validation_scored, order, genre_matrix, exposure, all_users, movie_counts
        )
    relevance_base = max(base_columns, key=lambda name: (validation_metrics[name]["ndcg_at_10"], name))
    base_column = base_columns[relevance_base]
    for weight in EXPLORATION_WEIGHTS:
        name = f"EXPLORE_{int(weight * 100):02d}_ON_{relevance_base}"
        order = greedy_exploration_order(validation_scored, base_column, weight, genre_matrix)
        validation_orders[name] = order
        validation_metrics[name], validation_per_user[name] = evaluate(
            validation_scored, order, genre_matrix, exposure, all_users, movie_counts
        )
    front = pareto_front(validation_metrics)
    selected = select_for_budgets(validation_metrics, front)
    validation_paired = {
        name: paired_ndcg_difference(
            validation_per_user["POPULARITY"], validation_per_user[name], seed=SEED + index
        )
        for index, name in enumerate(sorted(validation_per_user)) if name != "POPULARITY"
    }

    # Test is first touched for scoring after the Validation-only selections above are locked.
    test_orders = {name: base_order(test_scored, column) for name, column in base_columns.items()}
    for weight in EXPLORATION_WEIGHTS:
        name = f"EXPLORE_{int(weight * 100):02d}_ON_{relevance_base}"
        test_orders[name] = greedy_exploration_order(test_scored, base_column, weight, genre_matrix)
    test_metrics: dict[str, dict[str, Any]] = {}
    test_per_user: dict[str, pd.DataFrame] = {}
    report_names = sorted({"POPULARITY", *[entry["policy"] for entry in selected.values() if entry["policy"]]})
    for name in report_names:
        test_metrics[name], test_per_user[name] = evaluate(test_scored, test_orders[name], genre_matrix, exposure, all_users, movie_counts)
    test_paired = {
        name: paired_ndcg_difference(test_per_user["POPULARITY"], test_per_user[name], seed=SEED + 20_000 + index)
        for index, name in enumerate(report_names) if name != "POPULARITY"
    }
    test_baseline_ndcg = test_metrics["POPULARITY"]["ndcg_at_10"]
    budget_test_evaluation: dict[str, Any] = {}
    for budget_label, entry in selected.items():
        policy = entry["policy"]
        budget = float(budget_label.rstrip("%")) / 100.0
        loss = ((test_baseline_ndcg - test_metrics[policy]["ndcg_at_10"]) / test_baseline_ndcg
                if policy else None)
        budget_test_evaluation[budget_label] = {
            "locked_policy": policy,
            "test_relative_ndcg_loss": round(loss, 6) if loss is not None else None,
            "within_locked_candidate_budget": bool(loss is not None and loss <= budget + 1e-12),
        }

    train_count_map = train.groupby("user_id").size().astype(int).to_dict()
    test_positive = test_candidates.loc[test_candidates["is_positive"] == 1, ["user_id", "movie_id"]]
    positive_count_map = {int(row.user_id): int(movie_counts[int(row.movie_id)]) for row in test_positive.itertuples(index=False)}
    selected_segments = {name: segments(test_per_user[name], train_count_map, positive_count_map) for name in report_names}
    failures = {name: deidentified_failures(test_per_user["POPULARITY"], test_per_user[name], train_count_map)
                for name in report_names if name != "POPULARITY"}
    selected_exploration = next((name for name in report_names if name.startswith("EXPLORE_")), None)
    reason_analysis = None
    if selected_exploration:
        selected_weight = int(selected_exploration.split("_")[1]) / 100.0
        reason_analysis = build_reason_analysis(
            test_scored, test_orders[selected_exploration], base_column, selected_weight,
            genre_matrix, exposure, all_users, movie_counts,
        )

    k_regression: dict[str, Any] = {}
    for k in (3, 5, 10, 20):
        k_profiles, k_exposure = user_genre_profiles(train, all_users, genre_matrix, first_k=k)
        k_scored = score_candidates(test_candidates, k_profiles, all_users, genre_matrix, movie_counts, movie_sums, global_mean)
        k_regression[str(k)] = {}
        for name, column in (("CONTENT_GENRE", "content_pct"), ("HYBRID_CONTENT_25", "hybrid_25")):
            values, _ = evaluate(k_scored, base_order(k_scored, column), genre_matrix, k_exposure, all_users, movie_counts)
            k_regression[str(k)][name] = values

    args.output_dir.mkdir(parents=True, exist_ok=True)
    compact_results = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "validation_metrics": validation_metrics,
        "validation_paired_ndcg_vs_popularity": validation_paired,
        "pareto_front": front,
        "budget_selections": selected,
        "test_metrics": test_metrics,
        "test_paired_ndcg_vs_popularity": test_paired,
        "budget_test_evaluation": budget_test_evaluation,
        "test_segments": selected_segments,
        "k_regression": k_regression,
        "failure_cases": failures,
        "reason_analysis": reason_analysis,
    }
    results_path = args.output_dir / "aggregate-results.json"
    results_path.write_text(json.dumps(compact_results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.tracked_result.parent.mkdir(parents=True, exist_ok=True)
    args.tracked_result.write_text(json.dumps(compact_results, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    provenance = reason_provenance(relevance_base, selected)
    args.reason_provenance.parent.mkdir(parents=True, exist_ok=True)
    args.reason_provenance.write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "evidence_id": "REC-EV-004",
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": {
            "split_manifest": str(args.split_manifest),
            "split_manifest_sha256": sha256(args.split_manifest),
            "baseline_manifest": str(args.baseline_manifest),
            "baseline_manifest_sha256": sha256(args.baseline_manifest),
            "bias_parameters_sha256": sha256(bias_path),
            "archive_sha256": sha256(archive),
            "test_used": True,
        },
        "protocol": {
            "version": PROTOCOL,
            "candidate_scope": f"SAMPLED_1_POSITIVE_PLUS_{NEGATIVES}_DETERMINISTIC_NEGATIVES",
            "positive_policy": "latest train-known held-out item with train-only user-ecdf-shrunk-v1 >= 0.7",
            "model_training": "REC-EV-001 Train only; no Test tuning",
            "novelty_metric": "mean self-information bits from Laplace-smoothed Train item rating frequency",
            "diversity_metric": "mean pairwise one-minus-cosine over movies.csv genre vectors in Top-10",
            "catalog_coverage_metric": "unique Top-10 items divided by Train-known item universe",
            "long_tail_metric": "Top-10 share outside the top 20% Train-known items by rating count",
            "genre_calibration_metric": "Jensen-Shannon distance between Train genre exposure and Top-10 genre exposure",
            "top_k": TOP_K,
            "seed": SEED,
            "hybrid_content_alphas_validation_only": list(HYBRID_ALPHAS),
            "exploration_weights_validation_only": list(EXPLORATION_WEIGHTS),
            "relevance_loss_budget_candidates_not_product_approved": list(LOSS_BUDGETS),
            "selection_order": "Validation Pareto and budget selection locked before Test scoring",
        },
        "coverage": {
            "train_known_movies": int(np.count_nonzero(movie_counts)),
            "genre_available_train_movies": int(np.count_nonzero(genre_available & (movie_counts > 0))),
            "validation_users": int(validation_candidates["user_id"].nunique()),
            "test_users": int(test_candidates["user_id"].nunique()),
            "validation_candidate_rows": len(validation_candidates),
            "test_candidate_rows": len(test_candidates),
            "validation_candidate_genre_coverage": round(float(np.mean(genre_available[
                validation_candidates["movie_id"].to_numpy(dtype=np.int64)])), 6),
            "test_candidate_genre_coverage": round(float(np.mean(genre_available[
                test_candidates["movie_id"].to_numpy(dtype=np.int64)])), 6),
            "content_profile_user_coverage": round(float(np.mean(np.linalg.norm(profiles, axis=1) > 0)), 6),
        },
        "metrics": compact_results,
        "artifacts": {
            "aggregate_results": artifact(args.tracked_result),
            "large_output_copy": {"path": str(results_path), "tracked": False},
            "reason_provenance": artifact(args.reason_provenance),
        },
        "validation": {
            "status": "PASS",
            "split_checksums_verified": True,
            "validation_selection_precedes_test": True,
            "same_candidate_definition_for_all_policies": True,
            "raw_user_ids_tracked": False,
            "full_catalog_claim": False,
        },
        "runtime": {"python": __import__("platform").python_version(), "total_seconds": round(time.perf_counter() - started, 3)},
        "conclusion": {
            "personal_ranking_champion": None,
            "product_exploration_weight": None,
            "product_relevance_loss_budget": None,
            "decision": "VALIDATION_AND_SAMPLED_TEST_EVIDENCE_ONLY_WAITING_FOR_FULL_CATALOG_AND_PRODUCT_APPROVAL",
        },
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(evidence_markdown(manifest), encoding="utf-8")


def reason_provenance(relevance_base: str, selections: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "artifactKind": "REC_EV_006_SCORING_FEATURE_PROVENANCE",
        "sourceEvidence": "REC-EV-004",
        "reasonUiApproved": False,
        "rankingChampion": None,
        "validationRelevanceBase": relevance_base,
        "budgetPolicyReferences": selections,
        "features": [
            {"feature": "BAYESIAN_POPULARITY", "source": "REC-EV-001 Train rating count and sum",
             "scope": "ITEM", "reasonCandidate": "POPULARITY_BASELINE", "faithfulnessGate": "score contribution present"},
            {"feature": "GENRE_AFFINITY", "source": "Train-only user centered-rating genre vector and movies.csv genres",
             "scope": "USER_ITEM", "reasonCandidate": "GENRE_AFFINITY", "faithfulnessGate": "non-zero signed dot-product contribution"},
            {"feature": "NOVELTY_PRIOR", "source": "Train-only item rating count self-information",
             "scope": "ITEM", "reasonCandidate": "LESS_POPULAR_DISCOVERY", "faithfulnessGate": "selected policy has exploration weight and positive novelty contribution"},
            {"feature": "MARGINAL_GENRE_DIVERSITY", "source": "Greedy list-state maximum genre cosine distance",
             "scope": "LIST_ITEM", "reasonCandidate": "LIST_DIVERSITY", "faithfulnessGate": "item selection changed at its greedy position"},
        ],
        "prohibitedClaims": [
            "reason display count", "reason UI approval", "causal user preference explanation",
            "online exploration satisfaction", "full-catalog quality", "personal ranking champion",
        ],
    }


def table(headers: list[str], rows: list[list[str]]) -> str:
    return "\n".join(["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |",
                      *["| " + " | ".join(row) + " |" for row in rows]])


def evidence_markdown(manifest: dict[str, Any]) -> str:
    metrics = manifest["metrics"]
    validation_rows = [[name, f"{value['ndcg_at_10']:.4f}", f"{value['recall_at_10']:.4f}",
                        f"{value['novelty_bits']:.3f}", f"{value['intra_list_diversity']:.4f}",
                        f"{value['catalog_coverage']:.2%}", f"{value['long_tail_exposure']:.2%}",
                        f"{value['genre_calibration_distance']:.4f}"]
                       for name, value in sorted(metrics["validation_metrics"].items())]
    test_rows = [[name, f"{value['ndcg_at_10']:.4f}", f"{value['recall_at_10']:.4f}",
                  f"{value['novelty_bits']:.3f}", f"{value['intra_list_diversity']:.4f}",
                  f"{value['catalog_coverage']:.2%}", f"{value['long_tail_exposure']:.2%}"]
                 for name, value in sorted(metrics["test_metrics"].items())]
    selections = "\n".join(f"- Validation loss budget candidate `{budget}`: `{entry['policy']}` / "
                           f"`{entry['validation_status']}`" for budget, entry in metrics["budget_selections"].items())
    budget_test = "\n".join(
        f"- `{budget}` locked candidate `{entry['locked_policy']}`: Test relative NDCG loss "
        f"`{entry['test_relative_ndcg_loss']:.2%}` / within budget `{str(entry['within_locked_candidate_budget']).upper()}`"
        for budget, entry in metrics["budget_test_evaluation"].items()
    )
    k_rows = []
    for k, policies in sorted(metrics["k_regression"].items(), key=lambda item: int(item[0])):
        for name, value in sorted(policies.items()):
            k_rows.append([f"K{k}", name, f"{value['ndcg_at_10']:.4f}", f"{value['recall_at_10']:.4f}",
                           f"{value['novelty_bits']:.3f}", f"{value['catalog_coverage']:.2%}"])
    selected_name = next((name for name in sorted(metrics["test_metrics"]) if name != "POPULARITY"), None)
    segment_rows = []
    if selected_name:
        for name in ("POPULARITY", selected_name):
            for segment, value in metrics["test_segments"][name]["history_segment"].items():
                segment_rows.append([name, segment, str(value["users"]), f"{value['ndcg_at_10']:.4f}",
                                     f"{value['recall_at_10']:.4f}", f"{value['novelty_bits']:.3f}"])
    coverage = manifest["coverage"]
    return f"""# REC-EV-004 — Hybrid·탐험 관련성 손실 Pareto

> 상태: `COMPLETED_SAMPLED_DIAGNOSTIC`  
> 생성 시각: {manifest['run_at_utc']}  
> Candidate scope: `{manifest['protocol']['candidate_scope']}`  
> Full-catalog claim: `NO`

## 1. 결론

Popularity는 채택된 개인화 ranking champion이 아니라 비교 기준선이다. genre-only content와
Popularity+genre Hybrid도 validation/test 후보일 뿐 champion이 아니다. Validation에서 Pareto front와
사전 선언한 relevance loss budget 후보를 잠근 다음 held-out Test를 열었지만, sampled candidate 결과는
full-catalog 채택 근거가 아니므로 탐험 비율·2+1 구성·제품 loss budget을 승인하지 않는다.

{selections}

제품 결정: `WAITING_FOR_FULL_CATALOG_AND_PRODUCT_APPROVAL`.

## 2. 고정 조건과 누수 방지

- REC-EV-001 `global-time-v1` Train/Validation/Test checksum을 재검증했다.
- REC-EV-002 `bias_parameters.npz` checksum과 split binding을 검증해 Popularity 충분통계를 재사용했다.
- user profile, genre affinity, Popularity, novelty는 Train만 사용했다.
- 모든 정책은 같은 사용자별 1 positive + {NEGATIVES} deterministic negatives를 사용했다.
- positive는 Train-only `user-ecdf-shrunk-v1 >= 0.7`인 최신 train-known held-out item이다.
- Validation에서 Hybrid α `{list(HYBRID_ALPHAS)}`, exploration weight `{list(EXPLORATION_WEIGHTS)}`만 비교했다.
- `{list(LOSS_BUDGETS)}`는 제품 허용치가 아니라 Test 전에 고정한 연구용 후보다.
- Pareto/budget 선택을 확정한 후에만 Test 정책을 평가했다.
- Novelty는 Train item 빈도의 self-information bits, diversity는 Top-10 genre cosine distance,
  catalog coverage는 Train-known universe 대비 고유 Top-10, long-tail은 Train count 상위 20% 밖 노출률이다.

## 3. Validation Pareto 비교

{table(['Policy','NDCG@10','Recall@10','Novelty bits','Diversity','Catalog coverage','Long-tail','Genre cal. distance'], validation_rows)}

Pareto front: `{', '.join(metrics['pareto_front'])}`.
Calibration distance는 낮을수록 좋고 나머지 탐험 지표는 높을수록 좋다. 단일 합성 KPI로 합치지 않았다.

## 4. Held-out Test

{table(['Policy','NDCG@10','Recall@10','Novelty bits','Diversity','Catalog coverage','Long-tail'], test_rows)}

Test는 선택된 budget 후보와 Popularity만 보고하며 Test 결과로 α·weight·loss budget을 다시 고르지 않았다.

{budget_test}

특히 Validation에서 1% 후보로 잠근 정책은 Test에서 1% budget을 벗어났다. 이 실패를 보고한 뒤
3%로 제품 budget을 바꾸지 않으며, budget 자체는 계속 미승인이다. paired user NDCG 차이와 1,000회
bootstrap 95% CI는 manifest의 `test_paired_ndcg_vs_popularity`에 기록했다.

## 5. 사용자 segment·K·coverage 회귀

{table(['Policy','History segment','Users','NDCG@10','Recall@10','Novelty'], segment_rows)}

{table(['K','Policy','NDCG@10','Recall@10','Novelty','Catalog coverage'], k_rows)}

- Train-known movies: `{coverage['train_known_movies']:,}`
- movies.csv genre available: `{coverage['genre_available_train_movies']:,}`
- Validation/Test candidate genre row coverage: `{coverage['validation_candidate_genre_coverage']:.2%}` / `{coverage['test_candidate_genre_coverage']:.2%}`
- non-zero content user-profile coverage: `{coverage['content_profile_user_coverage']:.2%}`

자연 warm-user cohort만 포함하므로 K0/new-user 성능은 측정하지 않았다. genre metadata가 없는 candidate는
content contribution 0으로 fail-safe 처리되며 응답 coverage와 genre calibration coverage를 분리했다.
K 증가가 relevance를 단조 개선하지 않았으므로 onboarding 품질 근거로 사용하지 않는다. positive-item
popularity 구간의 상세 회귀는 manifest와 `aggregate-results.json`에 있다.

## 6. 실패 사례

manifest의 failure cases는 raw user/movie ID 없이 Popularity 대비 held-out positive rank가 가장 크게
후퇴한 5개 사례의 history segment와 rank 변화만 남긴다. 이 회귀 때문에 전체 평균의 novelty 증가만으로
정책을 채택하지 않는다.

## 7. REC-EV-006 provenance

`rec-ev-004-reason-provenance.json`은 실제 평가 점수의 `BAYESIAN_POPULARITY`, `GENRE_AFFINITY`,
`NOVELTY_PRIOR`, `MARGINAL_GENRE_DIVERSITY` 출처와 reason faithfulness Gate를 구조화한다. 이는
REC-EV-006 입력일 뿐 reason UI·표시 개수·개인화 설명 문구 승인이 아니다.

## 8. 재현

```powershell
py -3.12 scripts/recommendation_exploration_pareto.py `
  --split-manifest docs/recommendation/evidence/manifests/global-time-v1.json `
  --baseline-manifest docs/recommendation/evidence/manifests/rec-ev-002.json `
  --archive C:\\higher\\projects\\MM\\data\\raw\\ml-32m.zip `
  --output-dir outputs/recommendation-evidence/rec-ev-004 `
  --tracked-result docs/recommendation/evidence/results/rec-ev-004-aggregate.json `
  --manifest docs/recommendation/evidence/manifests/rec-ev-004.json `
  --evidence docs/recommendation/evidence/REC-EV-004-exploration-pareto.md `
  --reason-provenance docs/recommendation/evidence/manifests/rec-ev-004-reason-provenance.json

py -3.12 scripts/verify_recommendation_exploration_pareto.py `
  --manifest docs/recommendation/evidence/manifests/rec-ev-004.json
```

## 9. 한계

- sampled negative 순위는 full-catalog 순위와 정책 우열이 바뀔 수 있다.
- MovieLens 미평가는 싫어요가 아니며 novelty/diversity는 탐험 만족을 직접 측정하지 않는다.
- genre-only content는 TMDB text/keyword Hybrid를 대표하지 않는다.
- 개인 ranking champion, 2+1 탐험 구성, 제품 weight/loss budget, reason UI는 모두 미승인이다.
"""


if __name__ == "__main__":
    run()
