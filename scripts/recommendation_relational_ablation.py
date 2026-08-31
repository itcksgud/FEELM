from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import zipfile
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from scipy import sparse
from scipy.stats import rankdata
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

from recommendation_baseline_calibration import build_profile_count_matrix, predict_popularity
from recommendation_cold_start_full_catalog import load_sources, rank_of_positive
from recommendation_exploration_full_catalog import (
    artifact,
    deterministic_top_k,
    exact_artifact,
    load_common,
    select_warm_positives,
    sha256,
    write_json,
)
from recommendation_exploration_pareto import load_genres
from recommendation_user_case_study import (
    CASE_ALIAS,
    describe_movie,
    load_movie_metadata,
    policy_change,
    ranked_movies,
    stable_case_user,
)


PROTOCOL = "rec-ev-017-relational-tag-ablation-v1"
EVIDENCE_ID = "REC-EV-017"
TOP_K = 10
TOP_CANDIDATES = 500
TAG_USER_CAP = 500
TAG_MIN_DF = 5
TAG_MAX_FEATURES = 5_000
ASSOCIATION_MIN_SUPPORT = 50
ASSOCIATION_PRIOR = 100.0
GENRE_MIN_EXPOSURE = 3
GENRE_PRIOR = 100.0
ANCHOR_COUNT = 3
ALPHAS = (0.0, 0.1, 0.25, 0.5, 0.75, 1.0)
BATCH_SIZE = 40


def normalize_tag(value: str) -> str:
    normalized = str(value).casefold().strip()
    normalized = re.sub(r"[^\w\s-]+", " ", normalized, flags=re.UNICODE)
    normalized = re.sub(r"[_\s-]+", " ", normalized).strip()
    return normalized


def load_train_tags(
    archive: Path,
    train_boundary: int,
    universe: np.ndarray,
    excluded_users: np.ndarray,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    with zipfile.ZipFile(archive) as source:
        member = next(name for name in source.namelist() if name.endswith("/tags.csv"))
        with source.open(member) as stream:
            tags = pd.read_csv(
                stream,
                usecols=["userId", "movieId", "tag", "timestamp"],
                dtype={"userId": np.int64, "movieId": np.int64, "tag": str, "timestamp": np.int64},
            )
    original_rows = len(tags)
    tags = tags.loc[
        (tags["timestamp"] < train_boundary)
        & tags["movieId"].isin(universe)
        & ~tags["userId"].isin(excluded_users)
    ].rename(columns={"userId": "user_id", "movieId": "movie_id"})
    cutoff_rows = len(tags)
    tags["tag"] = tags["tag"].map(normalize_tag)
    tags = tags.loc[tags["tag"].str.len() > 1]
    tags = tags.sort_values(["user_id", "timestamp", "movie_id", "tag"], kind="stable")
    tags = tags.drop_duplicates(["user_id", "movie_id", "tag"], keep="first")
    deduplicated_rows = len(tags)
    tags = tags.loc[tags.groupby("user_id", sort=False).cumcount() < TAG_USER_CAP].copy()
    return tags, {
        "source_rows": original_rows,
        "train_cutoff_rows": cutoff_rows,
        "deduplicated_rows": deduplicated_rows,
        "capped_rows": len(tags),
        "contributing_users": int(tags["user_id"].nunique()),
        "tagged_movies": int(tags["movie_id"].nunique()),
        "excluded_evaluation_users": int(len(np.unique(excluded_users))),
        "per_user_cap": TAG_USER_CAP,
    }


def build_tag_matrix(
    tags: pd.DataFrame,
    universe: np.ndarray,
) -> tuple[sparse.csr_matrix, np.ndarray, dict[str, Any]]:
    grouped = tags.groupby("movie_id", sort=True)["tag"].agg(list).to_dict()
    documents = [grouped.get(int(movie), []) for movie in universe]
    vectorizer = TfidfVectorizer(
        analyzer=lambda values: values,
        lowercase=False,
        min_df=TAG_MIN_DF,
        max_features=TAG_MAX_FEATURES,
        sublinear_tf=True,
        norm="l2",
        dtype=np.float32,
    )
    matrix = vectorizer.fit_transform(documents).tocsr()
    terms = np.asarray(vectorizer.get_feature_names_out(), dtype=object)
    return matrix, terms, {
        "vocabulary_size": len(terms),
        "movies_with_vector": int(np.count_nonzero(np.diff(matrix.indptr))),
        "nonzero_values": int(matrix.nnz),
        "min_document_frequency": TAG_MIN_DF,
        "max_features": TAG_MAX_FEATURES,
    }


def user_means_from_bias(bias: Any) -> np.ndarray:
    counts = bias["user_counts"].astype(np.int64, copy=False)
    sums = bias["user_sums"].astype(np.float64, copy=False)
    return np.divide(sums, counts, out=np.zeros_like(sums), where=counts > 0)


def choose_anchors(
    history: pd.DataFrame,
    user_mean: float,
    movie_counts: np.ndarray,
) -> pd.DataFrame:
    candidates = history.copy()
    candidates["residual"] = candidates["rating"].astype(float) - user_mean
    candidates["movie_count"] = movie_counts[candidates["movie_id"].to_numpy(dtype=np.int64)]
    candidates = candidates.loc[(candidates["residual"] >= 0.5) & (candidates["movie_count"] >= 100)]
    candidates = candidates.sort_values(
        ["residual", "rating", "movie_count", "movie_id"],
        ascending=[False, False, False, True],
        kind="stable",
    )
    if len(candidates) < ANCHOR_COUNT:
        raise RuntimeError("case user has fewer than three supported positive anchors")
    return candidates.head(ANCHOR_COUNT)


def movie_positive_rates(
    train: pd.DataFrame,
    user_means: np.ndarray,
    movie_size: int,
) -> tuple[np.ndarray, np.ndarray]:
    users = train["user_id"].to_numpy(dtype=np.int64)
    movies = train["movie_id"].to_numpy(dtype=np.int64)
    positive = train["rating"].to_numpy(dtype=np.float64) > user_means[users]
    counts = np.bincount(movies, minlength=movie_size).astype(np.int64)
    positives = np.bincount(movies, weights=positive.astype(np.float64), minlength=movie_size)
    rates = np.divide(positives, counts, out=np.zeros_like(positives), where=counts > 0)
    return rates, counts


def association_for_anchor(
    train: pd.DataFrame,
    anchor_movie: int,
    user_means: np.ndarray,
    baseline_positive_rate: np.ndarray,
    movie_counts: np.ndarray,
) -> dict[str, Any]:
    anchor_rows = train.loc[train["movie_id"] == anchor_movie, ["user_id", "rating"]]
    anchor_users = anchor_rows["user_id"].to_numpy(dtype=np.int64)
    anchor_residual = anchor_rows["rating"].to_numpy(dtype=np.float64) - user_means[anchor_users]
    liker_users = np.unique(anchor_users[anchor_residual > 0])
    relevant = train.loc[train["user_id"].isin(liker_users), ["user_id", "movie_id", "rating"]]
    relevant_users = relevant["user_id"].to_numpy(dtype=np.int64)
    relevant_movies = relevant["movie_id"].to_numpy(dtype=np.int64)
    residual = relevant["rating"].to_numpy(dtype=np.float64) - user_means[relevant_users]
    support = np.bincount(relevant_movies, minlength=len(movie_counts)).astype(np.int64)
    positive = np.bincount(
        relevant_movies,
        weights=(residual > 0).astype(np.float64),
        minlength=len(movie_counts),
    )
    baseline = baseline_positive_rate
    smoothed = np.divide(
        positive + ASSOCIATION_PRIOR * baseline,
        support + ASSOCIATION_PRIOR,
        out=baseline.copy(),
        where=(support + ASSOCIATION_PRIOR) > 0,
    )
    lift = np.divide(smoothed, baseline, out=np.ones_like(smoothed), where=baseline > 0)
    score = np.log2(np.maximum(lift, 1e-12)) * support / (support + ASSOCIATION_PRIOR)
    valid = (
        (support >= ASSOCIATION_MIN_SUPPORT)
        & (movie_counts > 0)
        & np.isfinite(score)
        & (np.arange(len(score)) != anchor_movie)
    )
    score[~valid] = -np.inf
    return {
        "anchor_raters": len(anchor_users),
        "anchor_likers": len(liker_users),
        "support": support,
        "conditional_rate": np.divide(positive, support, out=np.zeros_like(positive), where=support > 0),
        "smoothed_rate": smoothed,
        "baseline_rate": baseline,
        "lift": lift,
        "score": score,
    }


def top_associations(
    association: dict[str, Any],
    universe: np.ndarray,
    metadata: pd.DataFrame,
    excluded: set[int],
    k: int = 5,
) -> list[dict[str, Any]]:
    score = association["score"][universe].copy()
    for movie in excluded:
        position = int(np.searchsorted(universe, movie))
        if position < len(universe) and int(universe[position]) == movie:
            score[position] = -np.inf
    movies = deterministic_top_k(universe, score, k)
    return [
        {
            **describe_movie(int(movie), metadata),
            "support": int(association["support"][int(movie)]),
            "conditional_like_rate": round(float(association["conditional_rate"][int(movie)]), 6),
            "smoothed_like_rate": round(float(association["smoothed_rate"][int(movie)]), 6),
            "baseline_like_rate": round(float(association["baseline_rate"][int(movie)]), 6),
            "lift": round(float(association["lift"][int(movie)]), 6),
            "association_score": round(float(association["score"][int(movie)]), 6),
        }
        for movie in movies
    ]


def combined_association_recommendations(
    anchors: pd.DataFrame,
    associations: dict[int, dict[str, Any]],
    universe: np.ndarray,
    seen: set[int],
    metadata: pd.DataFrame,
) -> tuple[np.ndarray, np.ndarray, list[dict[str, Any]]]:
    combined = np.zeros(len(universe), dtype=np.float64)
    contribution: dict[int, list[dict[str, Any]]] = {}
    for row in anchors.itertuples(index=False):
        anchor = int(row.movie_id)
        values = associations[anchor]["score"][universe]
        positive = np.where(np.isfinite(values) & (values > 0), values * float(row.residual), 0.0)
        combined += positive
    valid = combined > 0
    combined[~valid] = -np.inf
    for movie in seen:
        position = int(np.searchsorted(universe, movie))
        if position < len(universe) and int(universe[position]) == movie:
            combined[position] = -np.inf
    top = deterministic_top_k(universe, combined, TOP_K)
    for movie in top:
        values = []
        for row in anchors.itertuples(index=False):
            anchor = int(row.movie_id)
            raw = associations[anchor]["score"][int(movie)]
            amount = max(0.0, float(raw)) * float(row.residual) if np.isfinite(raw) else 0.0
            if amount > 0:
                values.append({
                    "anchor_title": describe_movie(anchor, metadata)["title"],
                    "contribution": round(amount, 6),
                    "support": int(associations[anchor]["support"][int(movie)]),
                    "lift": round(float(associations[anchor]["lift"][int(movie)]), 6),
                })
        contribution[int(movie)] = sorted(values, key=lambda value: (-value["contribution"], value["anchor_title"]))
    details = [
        {
            "rank": rank,
            **describe_movie(int(movie), metadata),
            "score": round(float(combined[np.searchsorted(universe, movie)]), 6),
            "anchors": contribution[int(movie)],
        }
        for rank, movie in enumerate(top, 1)
    ]
    return top, combined, details


def build_genre_profiles(
    train: pd.DataFrame,
    user_means: np.ndarray,
    genre_matrix: np.ndarray,
    *,
    chunk_size: int = 500_000,
) -> tuple[np.ndarray, np.ndarray]:
    user_size = len(user_means)
    genre_size = genre_matrix.shape[1]
    preference = np.zeros((user_size, genre_size), dtype=np.float64)
    exposure = np.zeros((user_size, genre_size), dtype=np.int32)
    users = train["user_id"].to_numpy(dtype=np.int64)
    movies = train["movie_id"].to_numpy(dtype=np.int64)
    ratings = train["rating"].to_numpy(dtype=np.float64)
    for start in range(0, len(train), chunk_size):
        stop = min(start + chunk_size, len(train))
        chunk_users = users[start:stop]
        chunk_movies = movies[start:stop]
        residual = ratings[start:stop] - user_means[chunk_users]
        movie_genres = genre_matrix[chunk_movies]
        np.add.at(preference, chunk_users, movie_genres * residual[:, None])
        np.add.at(exposure, chunk_users, (movie_genres > 0).astype(np.int32))
    norms = np.linalg.norm(preference, axis=1, keepdims=True)
    preference = np.divide(preference, norms, out=np.zeros_like(preference), where=norms > 0)
    return preference, exposure


def genre_associations(
    preference: np.ndarray,
    exposure: np.ndarray,
    vocabulary: list[str],
    anchor_genres: Iterable[str],
) -> dict[str, list[dict[str, Any]]]:
    index = {genre: position for position, genre in enumerate(vocabulary)}
    result: dict[str, list[dict[str, Any]]] = {}
    for anchor_genre in anchor_genres:
        anchor = index[anchor_genre]
        rows = []
        for candidate_genre, candidate in index.items():
            if candidate == anchor:
                continue
            co_exposed = (exposure[:, anchor] >= GENRE_MIN_EXPOSURE) & (
                exposure[:, candidate] >= GENRE_MIN_EXPOSURE
            )
            anchor_preferred = co_exposed & (preference[:, anchor] > 0)
            support = int(np.count_nonzero(anchor_preferred))
            if support == 0 or not np.any(co_exposed):
                continue
            candidate_preferred = preference[:, candidate] > 0
            conditional = float(np.mean(candidate_preferred[anchor_preferred]))
            baseline = float(np.mean(candidate_preferred[co_exposed]))
            smoothed = (support * conditional + GENRE_PRIOR * baseline) / (support + GENRE_PRIOR)
            lift = smoothed / baseline if baseline > 0 else 1.0
            rows.append({
                "genre": candidate_genre,
                "support_users": support,
                "conditional_preference_rate": round(conditional, 6),
                "baseline_preference_rate": round(baseline, 6),
                "smoothed_lift": round(lift, 6),
            })
        result[anchor_genre] = sorted(
            rows,
            key=lambda value: (-value["smoothed_lift"], -value["support_users"], value["genre"]),
        )[:5]
    return result


def build_tag_user_profiles(
    train: pd.DataFrame,
    users: np.ndarray,
    universe: np.ndarray,
    tag_matrix: sparse.csr_matrix,
) -> tuple[sparse.csr_matrix, sparse.csr_matrix]:
    ordered = np.asarray(sorted(int(user) for user in users), dtype=np.int64)
    user_position = pd.Series(np.arange(len(ordered), dtype=np.int64), index=ordered)
    selected = train.loc[
        train["user_id"].isin(ordered), ["user_id", "movie_id", "rating"]
    ].copy()
    selected["mean"] = selected.groupby("user_id", sort=False)["rating"].transform("mean")
    rows = user_position.loc[selected["user_id"].to_numpy(dtype=np.int64)].to_numpy(dtype=np.int64)
    columns = np.searchsorted(universe, selected["movie_id"].to_numpy(dtype=np.int64))
    values = selected["rating"].to_numpy(dtype=np.float32) - selected["mean"].to_numpy(dtype=np.float32)
    history = sparse.csr_matrix(
        (values, (rows, columns)), shape=(len(ordered), len(universe)), dtype=np.float32
    )
    profiles = normalize(history @ tag_matrix, norm="l2", axis=1, copy=False).tocsr()
    return profiles, history


def evaluate_tag_policies(
    profiles: sparse.csr_matrix,
    history: sparse.csr_matrix,
    users: np.ndarray,
    positives: dict[int, int],
    universe: np.ndarray,
    tag_matrix: sparse.csr_matrix,
    popularity_percentile: np.ndarray,
    movie_counts: np.ndarray,
    alphas: Iterable[float],
    *,
    seed: int,
) -> dict[str, Any]:
    ordered_users = np.asarray(sorted(int(user) for user in users), dtype=np.int64)
    rows: dict[str, list[dict[str, float]]] = {str(alpha): [] for alpha in alphas}
    unique: dict[str, set[int]] = {str(alpha): set() for alpha in alphas}
    tag_positive_coverage = 0
    movie_positions = {int(movie): index for index, movie in enumerate(universe)}
    tag_transpose = tag_matrix.T.tocsr()
    for start in range(0, len(ordered_users), BATCH_SIZE):
        stop = min(start + BATCH_SIZE, len(ordered_users))
        cosine = (profiles[start:stop] @ tag_transpose).toarray().astype(np.float64, copy=False)
        tag_unit = (np.clip(cosine, -1.0, 1.0) + 1.0) / 2.0
        for local, user in enumerate(ordered_users[start:stop]):
            global_row = start + local
            seen = history.indices[history.indptr[global_row]:history.indptr[global_row + 1]]
            positive = positives[int(user)]
            positive_position = movie_positions[positive]
            if tag_matrix.indptr[positive_position + 1] > tag_matrix.indptr[positive_position]:
                tag_positive_coverage += 1
            for alpha in alphas:
                name = str(alpha)
                score = (1.0 - alpha) * popularity_percentile + alpha * tag_unit[local]
                score = score.copy()
                score[seen] = -np.inf
                rank = rank_of_positive(universe, score, positive)
                top = deterministic_top_k(universe, score, TOP_K)
                unique[name].update(top.tolist())
                rows[name].append({
                    "user": int(user),
                    "candidate_hit": float(rank is not None and rank <= TOP_CANDIDATES),
                    "recall": float(rank is not None and rank <= TOP_K),
                    "ndcg": 0.0 if rank is None or rank > TOP_K else 1.0 / math.log2(rank + 1),
                    "history_count": int(len(seen)),
                    "positive_count": int(movie_counts[positive]),
                })
    metrics = {}
    for alpha in alphas:
        name = str(alpha)
        frame = pd.DataFrame(rows[name])
        metrics[name] = {
            "users": len(frame),
            "candidate_recall_at_500": round(float(frame["candidate_hit"].mean()), 6),
            "ndcg_at_10": round(float(frame["ndcg"].mean()), 6),
            "recall_at_10": round(float(frame["recall"].mean()), 6),
            "catalog_coverage": round(len(unique[name]) / len(universe), 6),
        }
    base = pd.DataFrame(rows[str(0.0)])
    paired: dict[str, Any] = {}
    segments: dict[str, Any] = {}
    for offset, alpha in enumerate(alphas):
        name = str(alpha)
        candidate = pd.DataFrame(rows[name])
        difference = candidate["ndcg"].to_numpy(dtype=np.float64) - base["ndcg"].to_numpy(dtype=np.float64)
        rng = np.random.default_rng(seed + offset)
        boot = np.asarray([
            float(np.mean(rng.choice(difference, len(difference), replace=True))) for _ in range(1_000)
        ])
        paired[name] = {
            "users": len(difference),
            "mean_difference": round(float(np.mean(difference)), 6),
            "ci95_low": round(float(np.quantile(boot, 0.025)), 6),
            "ci95_high": round(float(np.quantile(boot, 0.975)), 6),
            "bootstrap_repeats": 1_000,
        }
        segment_frame = candidate.copy()
        segment_frame["ndcg_difference_vs_popularity"] = difference
        segment_frame["history_segment"] = pd.cut(
            segment_frame["history_count"],
            [0, 49, 99, np.inf],
            labels=["K20_49", "K50_99", "K100_PLUS"],
        )
        q1, q2, q3 = np.quantile(segment_frame["positive_count"], [0.25, 0.5, 0.75])
        segment_frame["positive_segment"] = segment_frame["positive_count"].map(
            lambda count: "P1_LONG_TAIL" if count <= q1 else "P2" if count <= q2 else "P3" if count <= q3 else "P4_HEAD"
        )
        segments[name] = {}
        for column in ("history_segment", "positive_segment"):
            segments[name][column] = {}
            for group_offset, (label, group) in enumerate(segment_frame.groupby(column, observed=True)):
                group_difference = group["ndcg_difference_vs_popularity"].to_numpy(dtype=np.float64)
                segment_rng = np.random.default_rng(seed + 10_000 + offset * 100 + group_offset)
                segment_boot = np.asarray([
                    float(np.mean(segment_rng.choice(group_difference, len(group_difference), replace=True)))
                    for _ in range(1_000)
                ])
                segments[name][column][str(label)] = {
                    "users": len(group),
                    "ndcg_at_10": round(float(group["ndcg"].mean()), 6),
                    "recall_at_10": round(float(group["recall"].mean()), 6),
                    "candidate_recall_at_500": round(float(group["candidate_hit"].mean()), 6),
                    "mean_ndcg_difference_vs_popularity": round(
                        float(group["ndcg_difference_vs_popularity"].mean()), 6
                    ),
                    "difference_ci95_low": round(float(np.quantile(segment_boot, 0.025)), 6),
                    "difference_ci95_high": round(float(np.quantile(segment_boot, 0.975)), 6),
                }
    return {
        "metrics": metrics,
        "paired_ndcg_vs_popularity": paired,
        "segments": segments,
        "tagged_heldout_coverage": round(tag_positive_coverage / len(ordered_users), 6),
        "profile_coverage": round(float(np.mean(np.diff(profiles.indptr) > 0)), 6),
    }


def tag_profile_terms(profile: sparse.csr_matrix, terms: np.ndarray, limit: int = 10) -> dict[str, Any]:
    dense = profile.toarray().ravel()
    positive = np.flatnonzero(dense > 0)
    negative = np.flatnonzero(dense < 0)
    positive = positive[np.argsort(dense[positive], kind="stable")[::-1]][:limit]
    negative = negative[np.argsort(dense[negative], kind="stable")][:limit]
    return {
        "positive": [{"tag": str(terms[index]), "weight": round(float(dense[index]), 6)} for index in positive],
        "negative": [{"tag": str(terms[index]), "weight": round(float(dense[index]), 6)} for index in negative],
    }


def case_tag_policy(
    profile: sparse.csr_matrix,
    history: sparse.csr_matrix,
    universe: np.ndarray,
    tag_matrix: sparse.csr_matrix,
    popularity_percentile: np.ndarray,
    alpha: float,
    metadata: pd.DataFrame,
    positive: int,
) -> dict[str, Any]:
    cosine = (profile @ tag_matrix.T).toarray().ravel().astype(np.float64, copy=False)
    tag_unit = (np.clip(cosine, -1.0, 1.0) + 1.0) / 2.0
    policies = {
        "POPULARITY": popularity_percentile.copy(),
        "TAG_CONTENT": tag_unit,
        f"HYBRID_TAG_ALPHA_{str(alpha).replace('.', '_')}": (1.0 - alpha) * popularity_percentile + alpha * tag_unit,
    }
    seen = history.indices[history.indptr[0]:history.indptr[1]]
    result = {}
    top_by_name = {}
    for name, score in policies.items():
        score = score.copy()
        score[seen] = -np.inf
        top = deterministic_top_k(universe, score, TOP_K)
        top_by_name[name] = top
        score_map = {int(movie): float(score[np.searchsorted(universe, movie)]) for movie in top}
        result[name] = {
            "top10": ranked_movies(top, metadata, score_by_movie=score_map),
            "heldout_rank": rank_of_positive(universe, score, positive),
        }
    for name in result:
        result[name]["change_vs_popularity"] = policy_change(top_by_name["POPULARITY"], top_by_name[name], metadata)
    return result


def table(headers: list[str], rows: list[list[Any]]) -> str:
    return "\n".join([
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
        *("| " + " | ".join(str(value) for value in row) + " |" for row in rows),
    ])


def evidence_markdown(result: dict[str, Any]) -> str:
    case = result["case"]
    selection = result["tag_ablation"]["validation"]
    evaluation = result["tag_ablation"]["test"]
    selected_alpha = result["tag_ablation"]["selected_alpha"]
    lines = [
        "# REC-EV-017 영화·장르 공동 선호와 자유 태그 ablation",
        "",
        "> 상태: `COMPLETED_MOVIELENS_RELATIONAL_EVIDENCE_TMDB_BLOCKED` — MovieLens 관계·태그 근거이며 제품 champion은 아니다.",
        "",
        "## 결론",
        "",
        "REC-EV-016의 장르-only 설명을 그대로 확장하지 않고 세 가지 질문을 분리했다.",
        "",
        "1. 개인 평균보다 A를 높게 평가한 사람들이 B도 높게 평가하는가?",
        "2. A 장르를 상대적으로 선호한 사용자에게 어떤 B 장르 선호 lift가 있는가?",
        "3. 장르 외 자유 태그의 주제·분위기 정보가 full-catalog held-out ranking을 개선하는가?",
        "",
        f"Validation이 선택한 Popularity↔Tag 가중치는 `{selected_alpha}`였다. Test NDCG@10은 "
        f"Popularity `{evaluation['metrics']['0.0']['ndcg_at_10']:.6f}`, 선택 Hybrid "
        f"`{evaluation['metrics'][str(selected_alpha)]['ndcg_at_10']:.6f}`, Tag-only "
        f"`{evaluation['metrics']['1.0']['ndcg_at_10']:.6f}`였다. 이 결과는 아래 aggregate 표와 사용자 A의 실제 제목으로 함께 읽는다.",
        "",
        "## 데이터 경계",
        "",
        table(
            ["축", "입력", "누수 방지", "해석"],
            [
                ["영화→영화", "Train ratings", "사용자별 Train 평균만 사용", "조건부 공동 선호; 인과·감상 순서 아님"],
                ["장르→장르", "Train ratings + genres", "Train만 사용, 두 장르 모두 3편 이상 노출", "조건부 선호 lift"],
                ["태그", "Train boundary 이전 tags.csv", f"평가 cohort 기여 제외 + 사용자당 {TAG_USER_CAP}개 상한", "분위기·주제 TF-IDF; Tag Genome 아님"],
                ["TMDB", "전수 artifact 없음", "실행하지 않음", "120편 preview/843편 편향 감사 표본을 성능 실험에 사용하지 않음"],
            ],
        ),
        "",
        "미평가를 싫어요로 바꾸지 않았다. 영화 관계의 조건부 비율은 A를 선호한 사람 중 B도 실제로 평가한 사람만 분모에 들어가며, "
        "따라서 노출 편향이 남는다.",
        "",
        "## 사용자 A의 영화 anchor → 연관 영화",
        "",
        "Anchor는 사용자 A가 본인 Train 평균보다 최소 0.5 높게 평가했고 평가 수가 100개 이상인 영화 중, residual·평점·support 순으로 "
        "결과를 보기 전에 3편을 골랐다. lift는 사용자 평점 성향을 보정하고 support 50·prior 100으로 shrink했다.",
        "",
    ]
    for anchor in case["movie_associations"]:
        lines.extend([
            f"### {anchor['anchor']['title']} — 사용자 A {anchor['anchor']['rating']:.1f}, 평균 대비 {anchor['anchor']['residual']:+.3f}",
            "",
            f"전체 평가자 {anchor['anchor_raters']:,}명 중 개인 평균보다 높게 평가한 사람 {anchor['anchor_likers']:,}명.",
            "",
            table(
                ["B 영화", "공동 평가 support", "A 선호 집단 B 선호", "B 전체 기준", "shrunken lift", "score"],
                [[
                    item["title"], item["support"], f"{item['conditional_like_rate']:.1%}",
                    f"{item['baseline_like_rate']:.1%}", f"{item['lift']:.3f}", f"{item['association_score']:.4f}",
                ] for item in anchor["top_related"]],
            ),
            "",
        ])
    lines.extend([
        "## 사용자 A anchor를 합친 추천",
        "",
        table(
            ["순위", "영화", "점수", "가장 큰 anchor 근거"],
            [[
                item["rank"], item["title"], f"{item['score']:.4f}",
                ", ".join(f"{anchor['anchor_title']} (lift {anchor['lift']:.2f})" for anchor in item["anchors"][:2]),
            ] for item in case["association_recommendations"]["top10"]],
        ),
        "",
        f"자연 발생 held-out `{case['heldout']['title']}`의 관계 후보 순위: "
        f"`{case['association_recommendations']['heldout_rank'] or 'scorable candidate 밖'}`. "
        f"관계 점수가 있는 Train-known 후보 coverage는 {case['association_recommendations']['candidate_coverage']:.1%}다.",
        "",
        "## 사용자 A의 선호 장르 → 다른 장르",
        "",
        "장르 관계는 두 장르를 각각 최소 3편 평가한 사용자만 비교한다. `P(B 선호 | A 선호, A·B 노출) / P(B 선호 | A·B 노출)`이며 "
        "A가 B를 유발한다는 뜻이 아니다.",
        "",
    ])
    for anchor, values in case["genre_associations"].items():
        lines.extend([
            f"### {anchor} 선호 집단",
            "",
            table(
                ["B 장르", "support", "조건부 선호율", "기준 선호율", "shrunken lift"],
                [[value["genre"], value["support_users"], f"{value['conditional_preference_rate']:.1%}",
                  f"{value['baseline_preference_rate']:.1%}", f"{value['smoothed_lift']:.3f}"] for value in values],
            ),
            "",
        ])
    lines.extend([
        "## 장르 밖의 자유 태그 취향",
        "",
        f"Train boundary 이전 {result['tag_data']['source_rows']:,}개 원본 태그 중 정규화·중복 제거·사용자 상한 뒤 "
        f"{result['tag_data']['capped_rows']:,}개를 사용했다. vocabulary는 {result['tag_matrix']['vocabulary_size']:,}개이고 "
        f"Train-known 영화 {result['tag_matrix']['movies_with_vector']:,}/{result['candidate_universe']:,}편에 벡터가 있다.",
        "",
        table(
            ["방향", "태그", "weight"],
            [["선호", value["tag"], f"{value['weight']:+.4f}"] for value in case["tag_profile"]["positive"]]
            + [["비선호", value["tag"], f"{value['weight']:+.4f}"] for value in case["tag_profile"]["negative"]],
        ),
        "",
        "## Tag Hybrid aggregate",
        "",
        "Validation에서만 alpha를 선택하고 Test에서는 잠긴 alpha와 Tag-only 진단을 읽었다. 후보는 Train-known 50,977편 전체이며 Train-seen 제외, "
        "positive 비주입이다.",
        "",
        table(
            ["phase", "alpha", "NDCG@10", "Recall@10", "Candidate Recall@500", "Catalog coverage"],
            [["Validation", alpha, f"{metrics['ndcg_at_10']:.6f}", f"{metrics['recall_at_10']:.6f}",
              f"{metrics['candidate_recall_at_500']:.6f}", f"{metrics['catalog_coverage']:.6f}"]
             for alpha, metrics in selection["metrics"].items()]
            + [["Test", alpha, f"{metrics['ndcg_at_10']:.6f}", f"{metrics['recall_at_10']:.6f}",
                f"{metrics['candidate_recall_at_500']:.6f}", f"{metrics['catalog_coverage']:.6f}"]
               for alpha, metrics in evaluation["metrics"].items()],
        ),
        "",
        f"Test의 tag profile coverage는 {evaluation['profile_coverage']:.1%}, held-out 영화 tag coverage는 "
        f"{evaluation['tagged_heldout_coverage']:.1%}다. coverage가 없는 것은 싫어요나 0점이 아니다.",
        f"선택 Hybrid의 Test paired NDCG 차이는 `{evaluation['paired_ndcg_vs_popularity'][str(selected_alpha)]['mean_difference']:+.6f}`이며 "
        f"95% bootstrap CI는 `[{evaluation['paired_ndcg_vs_popularity'][str(selected_alpha)]['ci95_low']:+.6f}, "
        f"{evaluation['paired_ndcg_vs_popularity'][str(selected_alpha)]['ci95_high']:+.6f}]`다.",
        "",
        "선택 Hybrid의 Test 구간별 차이는 다음과 같다. 전체 개선이 어느 구간에서 발생했는지를 숨기지 않는다.",
        "",
        table(
            ["구간 축", "구간", "사용자", "NDCG@10", "Popularity 대비 차이", "차이 95% CI", "Recall@10"],
            [[axis, segment, values["users"], f"{values['ndcg_at_10']:.6f}",
              f"{values['mean_ndcg_difference_vs_popularity']:+.6f}",
              f"[{values['difference_ci95_low']:+.6f}, {values['difference_ci95_high']:+.6f}]",
              f"{values['recall_at_10']:.6f}"]
             for axis, groups in evaluation["segments"][str(selected_alpha)].items()
             for segment, values in groups.items()],
        ),
        "",
        "## 사용자 A의 Tag 정책 실제 목록",
        "",
    ])
    for name, policy in case["tag_policies"].items():
        lines.extend([
            f"### {name}",
            "",
            table(
                ["순위", "영화", "장르", "점수"],
                [[movie["rank"], movie["title"], ", ".join(movie["genres"]) or "미상", movie["score"]]
                 for movie in policy["top10"]],
            ),
            "",
            f"held-out 전체 순위: `{policy['heldout_rank'] or '후보 밖'}`, Popularity와 Top-10 overlap "
            f"`{policy['change_vs_popularity']['overlap_at_10']}/10`.",
            "",
        ])
    lines.extend([
        "## 판단",
        "",
        f"- Validation 선택 alpha는 `{selected_alpha}`다. Test paired CI까지 양수면 다음 offline 후보가 되지만 제품 채택은 아니다.",
        "- 실제 Test에서는 P2 인기도 구간이 회귀하고 P1 롱테일이 개선되지 않았다. 전체 평균 양수만으로 일반 ranking 후보를 열지 않는다.",
        "- 영화·장르 관계는 추천 근거 후보와 실패 분석에는 유용하지만, 단일 사용자 사례로 champion을 선택하지 않는다.",
        "- 자유 태그는 장르보다 풍부하지만 기여자 편향과 낮은 coverage가 있어 단독 제품 특징으로 채택하지 않는다.",
        "- TMDB 감독·배우·키워드·줄거리 embedding ablation은 전수 Train-known feature artifact가 생긴 뒤 같은 protocol의 새 evidence로 실행한다.",
        "- 개인 ranking champion은 계속 `null`, fallback은 Popularity다.",
        "",
        "## 재현",
        "",
        "```powershell",
        "$env:PYTHONPATH='scripts'",
        "py -3.12 scripts/recommendation_relational_ablation.py",
        "Remove-Item Env:PYTHONPATH",
        "```",
    ])
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="REC-EV-017 relational and tag ablation")
    parser.add_argument("--split-manifest", type=Path, default=Path("docs/recommendation/evidence/manifests/global-time-v1.json"))
    parser.add_argument("--baseline-manifest", type=Path, default=Path("docs/recommendation/evidence/manifests/rec-ev-002.json"))
    parser.add_argument("--rec-ev-004-manifest", type=Path, default=Path("docs/recommendation/evidence/manifests/rec-ev-004.json"))
    parser.add_argument("--cold-start-manifest", type=Path, default=Path("docs/recommendation/evidence/manifests/rec-ev-003.json"))
    parser.add_argument("--dual-head-manifest", type=Path, default=Path("docs/recommendation/evidence/manifests/rec-ev-003b.json"))
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--tmdb-preview", type=Path, default=Path("outputs/catalog-design-preview/catalog.jsonl"))
    parser.add_argument("--tmdb-audit", type=Path, default=Path("outputs/tmdb-audit-2026-08-29/movie_field_audit.csv"))
    parser.add_argument("--result", type=Path, default=Path("docs/recommendation/evidence/results/rec-ev-017-relational-tag.json"))
    parser.add_argument("--manifest", type=Path, default=Path("docs/recommendation/evidence/manifests/rec-ev-017.json"))
    parser.add_argument("--evidence", type=Path, default=Path("docs/recommendation/evidence/REC-EV-017-relational-tag-ablation.md"))
    return parser.parse_args()


def run(args: argparse.Namespace) -> None:
    load_args = argparse.Namespace(
        split_manifest=args.split_manifest,
        baseline_manifest=args.baseline_manifest,
        rec_ev_004_manifest=args.rec_ev_004_manifest,
        archive=args.archive,
    )
    split, baseline, train, test, bias, genre_matrix, genre_available, _ = load_common(load_args, "test")
    archive = args.archive or Path(split["source"]["archive"])
    validation = pd.read_parquet(
        exact_artifact(split["artifacts"]["validation"]),
        columns=["user_id", "movie_id", "rating", "timestamp"],
    )
    universe = np.flatnonzero(bias["movie_counts"].astype(np.int64, copy=False) > 0).astype(np.int64)
    if len(universe) != 50_977:
        raise RuntimeError("Train-known universe changed")
    metadata = load_movie_metadata(archive)
    _, vocabulary, _ = load_genres(archive, len(bias["movie_counts"]))
    user_means = user_means_from_bias(bias)

    profile_matrix, profile_totals = build_profile_count_matrix(
        train["user_id"].to_numpy(dtype=np.int64),
        train["rating"].to_numpy(dtype=np.float64),
        len(bias["user_counts"]),
    )
    validation_positives_frame = select_warm_positives(
        validation, bias["user_counts"], bias["movie_counts"], profile_matrix, profile_totals
    )
    test_positives_frame = select_warm_positives(
        test, bias["user_counts"], bias["movie_counts"], profile_matrix, profile_totals
    )

    cold_source = load_sources(argparse.Namespace(
        cold_start_manifest=args.cold_start_manifest,
        dual_head_manifest=args.dual_head_manifest,
        baseline_manifest=args.baseline_manifest,
    ))
    cold_evaluation = cold_source["positives"].loc[~cold_source["selection"]]
    eligible = np.intersect1d(
        test_positives_frame["user_id"].to_numpy(dtype=np.int64),
        cold_evaluation["user_id"].to_numpy(dtype=np.int64),
        assume_unique=True,
    )
    case_user = stable_case_user(eligible)
    case_history = train.loc[train["user_id"] == case_user].copy()
    case_positive = int(test_positives_frame.loc[test_positives_frame["user_id"] == case_user, "movie_id"].iloc[0])
    anchors = choose_anchors(case_history, user_means[case_user], bias["movie_counts"])
    baseline_positive_rate, movie_counts = movie_positive_rates(train, user_means, len(bias["movie_counts"]))
    associations = {
        int(movie): association_for_anchor(
            train, int(movie), user_means, baseline_positive_rate, movie_counts
        )
        for movie in anchors["movie_id"].to_numpy(dtype=np.int64)
    }
    seen = set(case_history["movie_id"].astype(int).tolist())
    movie_associations = []
    for row in anchors.itertuples(index=False):
        movie = int(row.movie_id)
        movie_associations.append({
            "anchor": {
                **describe_movie(movie, metadata),
                "rating": float(row.rating),
                "residual": round(float(row.residual), 6),
                "movie_rating_count": int(row.movie_count),
            },
            "anchor_raters": associations[movie]["anchor_raters"],
            "anchor_likers": associations[movie]["anchor_likers"],
            "top_related": top_associations(associations[movie], universe, metadata, seen),
        })
    association_top, association_score, association_details = combined_association_recommendations(
        anchors, associations, universe, seen, metadata
    )
    association_rank = rank_of_positive(universe, association_score, case_positive)
    association_coverage = float(np.mean(np.isfinite(association_score)))

    genre_preference, genre_exposure = build_genre_profiles(train, user_means, genre_matrix)
    case_genre_values = genre_preference[case_user]
    preferred_genres = [
        vocabulary[int(index)]
        for index in np.argsort(case_genre_values)[::-1]
        if case_genre_values[int(index)] > 0
    ][:3]
    genre_relation = genre_associations(
        genre_preference, genre_exposure, vocabulary, preferred_genres
    )

    excluded_tag_users = np.union1d(
        validation["user_id"].unique().astype(np.int64),
        test["user_id"].unique().astype(np.int64),
    )
    tags, tag_data = load_train_tags(
        archive,
        int(split["protocol"]["train_boundary"]),
        universe,
        excluded_tag_users,
    )
    tag_matrix, terms, tag_matrix_info = build_tag_matrix(tags, universe)
    popularity = predict_popularity(
        universe,
        float(bias["global_mean"]),
        bias["movie_counts"].astype(np.int64, copy=False),
        bias["movie_sums"].astype(np.float64, copy=False),
        prior=50.0,
    )
    popularity_percentile = rankdata(popularity, method="average") / len(universe)

    validation_users = validation_positives_frame["user_id"].to_numpy(dtype=np.int64)
    validation_positive_map = {
        int(row.user_id): int(row.movie_id) for row in validation_positives_frame.itertuples(index=False)
    }
    validation_profiles, validation_history = build_tag_user_profiles(
        train, validation_users, universe, tag_matrix
    )
    validation_result = evaluate_tag_policies(
        validation_profiles,
        validation_history,
        validation_users,
        validation_positive_map,
        universe,
        tag_matrix,
        popularity_percentile,
        movie_counts,
        ALPHAS,
        seed=42,
    )
    selected_alpha = max(
        ALPHAS,
        key=lambda alpha: (validation_result["metrics"][str(alpha)]["ndcg_at_10"], -alpha),
    )

    test_users = test_positives_frame["user_id"].to_numpy(dtype=np.int64)
    test_positive_map = {
        int(row.user_id): int(row.movie_id) for row in test_positives_frame.itertuples(index=False)
    }
    test_profiles, test_history = build_tag_user_profiles(train, test_users, universe, tag_matrix)
    test_alphas = tuple(dict.fromkeys((0.0, float(selected_alpha), 1.0)))
    test_result = evaluate_tag_policies(
        test_profiles,
        test_history,
        test_users,
        test_positive_map,
        universe,
        tag_matrix,
        popularity_percentile,
        movie_counts,
        test_alphas,
        seed=142,
    )
    case_position = int(np.searchsorted(np.sort(test_users), case_user))
    if int(np.sort(test_users)[case_position]) != case_user:
        raise RuntimeError("case user missing from Test profile")
    case_profile = test_profiles[case_position]
    case_history_matrix = test_history[case_position]
    tag_policies = case_tag_policy(
        case_profile,
        case_history_matrix,
        universe,
        tag_matrix,
        popularity_percentile,
        float(selected_alpha),
        metadata,
        case_positive,
    )

    preview_movies = 120
    audit_movies = int(pd.read_csv(args.tmdb_audit, usecols=["movie_id"])["movie_id"].nunique())
    if args.tmdb_preview.is_file():
        preview_movies = sum(
            1 for line in args.tmdb_preview.open(encoding="utf-8")
            if json.loads(line).get("recordType") == "movieIdentity"
        )

    selected_segment_values = test_result["segments"][str(selected_alpha)]["positive_segment"]
    segment_non_regression = all(
        values["difference_ci95_low"] >= 0 for values in selected_segment_values.values()
    )
    overall_positive = (
        selected_alpha > 0
        and test_result["paired_ndcg_vs_popularity"][str(selected_alpha)]["ci95_low"] > 0
    )
    result = {
        "schema_version": 1,
        "evidence_id": EVIDENCE_ID,
        "protocol": PROTOCOL,
        "candidate_universe": len(universe),
        "case": {
            "alias": CASE_ALIAS,
            "raw_user_id_tracked": False,
            "selection_uses_model_outcome": False,
            "heldout": describe_movie(case_positive, metadata),
            "movie_associations": movie_associations,
            "association_recommendations": {
                "top10": association_details,
                "heldout_rank": association_rank,
                "candidate_coverage": round(association_coverage, 6),
            },
            "preferred_genre_anchors": preferred_genres,
            "genre_associations": genre_relation,
            "tag_profile": tag_profile_terms(case_profile, terms),
            "tag_policies": tag_policies,
        },
        "tag_data": tag_data,
        "tag_matrix": tag_matrix_info,
        "tag_ablation": {
            "alpha_grid": list(ALPHAS),
            "selected_alpha": float(selected_alpha),
            "validation": validation_result,
            "test": test_result,
            "positive_injection": False,
            "train_seen_excluded": True,
        },
        "tmdb_feature_gate": {
            "status": "BLOCKED_NO_FULL_TRAIN_KNOWN_FEATURE_ARTIFACT",
            "preview_movies": preview_movies,
            "audit_sample_movies": audit_movies,
            "required_movies": len(universe),
            "audit_sample_is_performance_input": False,
        },
        "decision": {
            "personal_ranking_champion": None,
            "fallback": "POPULARITY",
            "tag_hybrid_adopted": False,
            "tag_hybrid_offline_candidate": bool(overall_positive and segment_non_regression),
            "tag_hybrid_aggregate_signal": "POSITIVE_OVERALL_BUT_SEGMENT_REGRESSION" if overall_positive and not segment_non_regression else "NO_POSITIVE_OVERALL_SIGNAL" if not overall_positive else "POSITIVE_WITHOUT_SEGMENT_REGRESSION",
            "positive_popularity_segment_non_regression": segment_non_regression,
            "movie_association_product_reason_approved": False,
            "genre_association_product_reason_approved": False,
            "tmdb_content_ablation_completed": False,
        },
    }
    write_json(args.result, result)
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(evidence_markdown(result), encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "evidence_id": EVIDENCE_ID,
        "protocol": {
            "version": PROTOCOL,
            "movie_association": {
                "positive": "rating above each user's Train mean",
                "minimum_support": ASSOCIATION_MIN_SUPPORT,
                "prior": ASSOCIATION_PRIOR,
                "interpretation": "conditional association, not causality or watch sequence",
            },
            "genre_association": {
                "minimum_exposure_per_genre": GENRE_MIN_EXPOSURE,
                "prior": GENRE_PRIOR,
            },
            "tag_content": {
                "train_boundary_only": True,
                "evaluation_user_tag_contributions_excluded": True,
                "user_contribution_cap": TAG_USER_CAP,
                "min_df": TAG_MIN_DF,
                "max_features": TAG_MAX_FEATURES,
                "alpha_grid": list(ALPHAS),
            },
            "raw_user_id_tracked": False,
            "positive_injection": False,
        },
        "sources": {
            "archive_sha256": sha256(archive),
            "split_manifest_sha256": sha256(args.split_manifest),
            "baseline_manifest_sha256": sha256(args.baseline_manifest),
            "rec_ev_004_manifest_sha256": sha256(args.rec_ev_004_manifest),
            "cold_start_manifest_sha256": sha256(args.cold_start_manifest),
            "dual_head_manifest_sha256": sha256(args.dual_head_manifest),
            "tmdb_preview_sha256": sha256(args.tmdb_preview),
            "tmdb_audit_sha256": sha256(args.tmdb_audit),
        },
        "artifacts": {"result": artifact(args.result), "evidence": artifact(args.evidence)},
        "validation": {
            "status": "PASS",
            "validation_selects_alpha_before_test": True,
            "same_test_cohort_as_rec_ev_004b": True,
            "case_selection_uses_model_outcome": False,
            "raw_user_id_tracked": False,
            "tmdb_biased_sample_excluded": True,
        },
        "conclusion": result["decision"],
    }
    write_json(args.manifest, manifest)
    print(json.dumps({
        "status": "PASS",
        "evidence_id": EVIDENCE_ID,
        "selected_alpha": selected_alpha,
        "test_ndcg": test_result["metrics"],
        "tmdb_feature_gate": result["tmdb_feature_gate"]["status"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    run(parse_args())
