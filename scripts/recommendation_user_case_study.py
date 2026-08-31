from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import rankdata

from recommendation_baseline_calibration import (
    build_profile_count_matrix,
    predict_popularity,
    rating_midrank_ecdf,
)
from recommendation_cold_start_full_catalog import (
    blend_scores,
    load_sources,
    prepare_arrays,
    rank_of_positive,
    user_scores,
)
from recommendation_exploration_full_catalog import (
    artifact,
    build_seen,
    conservative_genre_diversity,
    deterministic_top_k,
    exact_artifact,
    explore_top10,
    load_common,
    select_warm_positives,
    sha256,
    write_json,
)
from recommendation_exploration_pareto import js_distance, user_genre_profiles


PROTOCOL = "rec-ev-016-user-case-a-v1"
CASE_ALIAS = "MovieLens 사용자 A"
CASE_SELECTION_SALT = "REC-EV-016|CASE-A|intersection|v1"
TOP_CANDIDATES = 500
TOP_K = 10
K = 10
FOLDIN_ALPHA = 0.2
POLICIES = (
    "POPULARITY",
    "CONTENT_GENRE",
    "HYBRID_CONTENT_25",
    "ALS_WARM",
    "EXPLORE_05_ON_POPULARITY",
)


def stable_case_user(eligible_users: np.ndarray) -> int:
    """Choose one eligible user without inspecting any model outcome."""
    users = np.unique(np.asarray(eligible_users, dtype=np.int64))
    if len(users) == 0:
        raise RuntimeError("no user is eligible for both warm and cold-start case studies")
    return min(
        (int(user) for user in users),
        key=lambda user: hashlib.sha256(f"{CASE_SELECTION_SALT}|{user}".encode("utf-8")).digest(),
    )


def movie_year(title: str) -> int | None:
    match = re.search(r"\((\d{4})\)$", title)
    return int(match.group(1)) if match else None


def load_movie_metadata(archive: Path) -> pd.DataFrame:
    with zipfile.ZipFile(archive) as source:
        name = next(name for name in source.namelist() if name.endswith("/movies.csv"))
        with source.open(name) as stream:
            movies = pd.read_csv(stream, usecols=["movieId", "title", "genres"])
    movies = movies.rename(columns={"movieId": "movie_id"})
    movies["year"] = movies["title"].map(movie_year)
    movies["genres"] = movies["genres"].map(
        lambda value: [] if value == "(no genres listed)" else str(value).split("|")
    )
    return movies.set_index("movie_id", verify_integrity=True)


def describe_movie(movie_id: int, metadata: pd.DataFrame) -> dict[str, Any]:
    if movie_id not in metadata.index:
        return {"title": f"Unknown Movie ({movie_id})", "year": None, "genres": []}
    row = metadata.loc[movie_id]
    return {
        "title": str(row["title"]),
        "year": None if pd.isna(row["year"]) else int(row["year"]),
        "genres": list(row["genres"]),
    }


def ranked_movies(
    movie_ids: np.ndarray,
    metadata: pd.DataFrame,
    *,
    score_by_movie: dict[int, float] | None = None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for rank, movie_id in enumerate(movie_ids, 1):
        movie = describe_movie(int(movie_id), metadata)
        item = {"rank": rank, **movie}
        if score_by_movie is not None:
            item["score"] = round(float(score_by_movie[int(movie_id)]), 6)
        result.append(item)
    return result


def policy_change(reference: np.ndarray, candidate: np.ndarray, metadata: pd.DataFrame) -> dict[str, Any]:
    reference_ids = [int(value) for value in reference]
    candidate_ids = [int(value) for value in candidate]
    reference_set, candidate_set = set(reference_ids), set(candidate_ids)
    return {
        "overlap_at_10": len(reference_set & candidate_set),
        "entered": [describe_movie(value, metadata)["title"] for value in candidate_ids if value not in reference_set],
        "exited": [describe_movie(value, metadata)["title"] for value in reference_ids if value not in candidate_set],
    }


def policy_metrics(
    movie_ids: np.ndarray,
    positive: int,
    universe: np.ndarray,
    scores: np.ndarray | None,
    movie_counts: np.ndarray,
    genre_matrix: np.ndarray,
    genre_available: np.ndarray,
    exposure: np.ndarray,
    *,
    candidate_movies: np.ndarray | None = None,
) -> dict[str, Any]:
    top_positions = {int(movie): index + 1 for index, movie in enumerate(movie_ids)}
    candidate_positions = (
        {int(movie): index + 1 for index, movie in enumerate(candidate_movies)}
        if candidate_movies is not None
        else None
    )
    exact_rank = rank_of_positive(universe, scores, positive) if scores is not None else None
    diversity, pair_coverage = conservative_genre_diversity(movie_ids, genre_matrix, genre_available)
    novelty = -np.log2((movie_counts[movie_ids] + 1.0) / (movie_counts.sum() + len(universe)))
    list_profile = genre_matrix[movie_ids].sum(axis=0)
    return {
        "heldout_full_catalog_rank": exact_rank,
        "heldout_candidate_rank": None if candidate_positions is None else candidate_positions.get(int(positive)),
        "heldout_top10_rank": top_positions.get(int(positive)),
        "ndcg_at_10": round(
            0.0 if int(positive) not in top_positions else 1.0 / math.log2(top_positions[int(positive)] + 1), 6
        ),
        "recall_at_10": int(int(positive) in top_positions),
        "novelty_bits": round(float(np.mean(novelty)), 6),
        "intra_list_diversity": round(diversity, 6),
        "pair_genre_coverage": round(pair_coverage, 6),
        "genre_calibration_distance": (
            None if (distance := js_distance(exposure, list_profile)) is None else round(distance, 6)
        ),
    }


def rating_style(user_history: pd.DataFrame, bias: Any) -> dict[str, Any]:
    ratings = user_history["rating"].to_numpy(dtype=np.float64)
    counts = bias["user_counts"].astype(np.int64, copy=False)
    active_counts = counts[counts > 0]
    activity_midrank = (
        np.count_nonzero(active_counts < len(ratings))
        + 0.5 * np.count_nonzero(active_counts == len(ratings))
    ) / len(active_counts)
    delta = float(np.mean(ratings) - float(bias["global_mean"]))
    return {
        "train_rating_count": len(ratings),
        "activity_percentile_among_train_users": round(float(activity_midrank), 6),
        "mean_rating": round(float(np.mean(ratings)), 6),
        "global_train_mean": round(float(bias["global_mean"]), 6),
        "mean_delta_from_global": round(delta, 6),
        "mean_tendency": "HIGHER_THAN_GLOBAL" if delta > 0.2 else "LOWER_THAN_GLOBAL" if delta < -0.2 else "NEAR_GLOBAL",
        "rating_std": round(float(np.std(ratings, ddof=1)), 6),
        "rating_min": float(np.min(ratings)),
        "rating_max": float(np.max(ratings)),
        "raw_4plus_rate": round(float(np.mean(ratings >= 4.0)), 6),
        "rating_value_counts": {
            f"{value:.1f}": int(np.count_nonzero(ratings == value)) for value in np.arange(0.5, 5.1, 0.5)
        },
    }


def history_examples(history: pd.DataFrame, metadata: pd.DataFrame, *, highest: bool) -> list[dict[str, Any]]:
    ordered = history.sort_values(
        ["rating", "timestamp", "movie_id"],
        ascending=[not highest, False, True],
        kind="stable",
    ).head(5)
    return [
        {"rating": float(row.rating), **describe_movie(int(row.movie_id), metadata)}
        for row in ordered.itertuples(index=False)
    ]


def genre_diagnostics(
    preference: np.ndarray,
    exposure: np.ndarray,
    vocabulary: list[str],
) -> dict[str, list[dict[str, Any]]]:
    positive = np.argsort(preference)[::-1]
    negative = np.argsort(preference)

    def values(indices: np.ndarray, *, condition: Any, limit: int = 5) -> list[dict[str, Any]]:
        result = []
        for index in indices:
            if condition(float(preference[index])):
                result.append({
                    "genre": vocabulary[int(index)],
                    "centered_affinity": round(float(preference[index]), 6),
                    "history_exposure_share": round(float(exposure[index]), 6),
                })
            if len(result) == limit:
                break
        return result

    return {
        "positive": values(positive, condition=lambda value: value > 0),
        "negative": values(negative, condition=lambda value: value < 0),
    }


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    head = "| " + " | ".join(headers) + " |"
    rule = "| " + " | ".join("---" for _ in headers) + " |"
    body = ["| " + " | ".join(str(value) for value in row) + " |" for row in rows]
    return "\n".join([head, rule, *body])


def titles(value: list[dict[str, Any]]) -> str:
    return ", ".join(item["title"] for item in value) if value else "없음"


def evidence_markdown(result: dict[str, Any]) -> str:
    style = result["case"]["rating_style"]
    warm = result["warm_full_catalog"]
    cold = result["cold_start_k10"]
    affinity = result["case"]["genre_diagnostics"]
    lines = [
        "# REC-EV-016 MovieLens 사용자 A 추천 변화 사례",
        "",
        "> 상태: `COMPLETED_REPRODUCIBLE_CASE_DIAGNOSTIC` — 사례 설명 자료이며 champion 또는 제품 채택 근거가 아니다.",
        "",
        "## 먼저 답",
        "",
        "기존 문서에는 전체 평균 지표는 있었지만, 한 사용자의 실제 영화 목록이 모델마다 어떻게 바뀌는지 보여주는 설명이 없었다. "
        "이 문서는 결과를 보고 고른 사용자가 아니라 두 평가 cohort의 교집합에서 고정 해시로 뽑은 `MovieLens 사용자 A`를 대상으로 "
        "Popularity, 장르 Content, Hybrid, ALS, 탐험, K10 Fold-in을 같은 조건에서 다시 계산한다.",
        "",
        "## 어떤 데이터를 어떻게 나눴나",
        "",
        markdown_table(
            ["항목", "값"],
            [
                ["원본", "MovieLens 32M ratings.csv + movies.csv"],
                ["전체", "32,000,204 ratings / 200,948 users / 87,585 movie metadata rows"],
                ["Train", "25,600,163 ratings; 2018-10-03 07:21:40 UTC까지"],
                ["Validation", "3,200,020 ratings; 모델·가중치 선택 전용"],
                ["Test", "3,200,021 ratings; 선택이 끝난 뒤 최종 비교"],
                ["후보", "Train에서 알려진 50,977편 전체; Train에서 본 영화 제외; 정답 강제 삽입 없음"],
                ["사용", "rating, timestamp, movieId, title, genres"],
                ["미사용", "tags.csv, links.csv, TMDB, 포스터, OTT, 사용자 인구통계"],
            ],
        ),
        "",
        "전역 시간 분할을 썼기 때문에 미래에 들어온 신규 사용자·신규 영화까지 포함된다. 모델 학습과 사용자 취향 계산은 Train만 "
        "사용하고, Validation으로 가중치를 고른 뒤 Test 결과를 읽었다.",
        "",
        "## 사용자 A는 어떻게 골랐나",
        "",
        f"- warm Test 사례와 leakage-safe cold-start 평가에 모두 들어가는 {result['case']['eligible_intersection_users']:,}명만 후보로 뒀다.",
        f"- `{CASE_SELECTION_SALT}|내부 user id` SHA-256이 가장 작은 사용자를 선택했다.",
        "- 추천 결과, 평점, 장르, 성공 여부는 선택에 쓰지 않았다. 원본 ID는 추적 문서에 저장하지 않는다.",
        "",
        "## 사용자 A의 취향·평점 성향",
        "",
        markdown_table(
            ["관측", "값", "해석 범위"],
            [
                ["Train 평가 수", f"{style['train_rating_count']:,}", f"Train 사용자 중 활동량 {style['activity_percentile_among_train_users']:.1%} percentile"],
                ["평균", f"{style['mean_rating']:.3f}", f"전체 Train {style['global_train_mean']:.3f} 대비 {style['mean_delta_from_global']:+.3f}"],
                ["표준편차", f"{style['rating_std']:.3f}", "점수 사용 폭을 나타내며 성격 진단이 아님"],
                ["4점 이상 비율", f"{style['raw_4plus_rate']:.1%}", "공통 4점 threshold 대신 개인 ECDF를 쓰는 이유"],
            ],
        ),
        "",
        "장르 취향은 각 평점에서 그 사용자의 Train 평균을 빼고, 영화의 다중 장르 벡터에 더한 뒤 L2 정규화했다. 따라서 단순히 "
        "많이 본 장르가 아니라, 본인의 평소 점수보다 높거나 낮게 준 방향이다.",
        "",
        markdown_table(
            ["방향", "장르", "centered affinity", "이력 노출 비중"],
            [
                ["선호", item["genre"], f"{item['centered_affinity']:+.4f}", f"{item['history_exposure_share']:.1%}"]
                for item in affinity["positive"]
            ]
            + [
                ["비선호", item["genre"], f"{item['centered_affinity']:+.4f}", f"{item['history_exposure_share']:.1%}"]
                for item in affinity["negative"]
            ],
        ),
        "",
        f"Train에서 높게 평가한 예: {titles(result['case']['highest_rated_examples'])}",
        "",
        f"Train에서 낮게 평가한 예: {titles(result['case']['lowest_rated_examples'])}",
        "",
        "이것은 MovieLens 행동의 기술적 요약이다. 사람의 성격·정체성·실제 FEELM 만족도를 추론한 것이 아니다.",
        "",
        "## 같은 사용자, 같은 Test 정답, 알고리즘만 변경",
        "",
        f"자연 발생 held-out 정답은 **{warm['heldout']['title']}**, 실제 평점 {warm['heldout']['rating']:.1f}, "
        f"Train 평점 습관으로 환산한 상대 효용 {warm['heldout']['relative_utility']:.3f}이다. 정답은 후보에 강제로 넣지 않았다.",
        "",
        markdown_table(
            ["정책", "정답 전체 순위", "Top-10", "NDCG@10", "Popularity와 겹침", "결론"],
            [
                [
                    name,
                    "greedy Top-500만 정의" if data["metrics"]["heldout_full_catalog_rank"] is None and name.startswith("EXPLORE") else (data["metrics"]["heldout_full_catalog_rank"] or "후보 밖"),
                    data["metrics"]["heldout_top10_rank"] or "miss",
                    f"{data['metrics']['ndcg_at_10']:.6f}",
                    f"{data['change_vs_popularity']['overlap_at_10']}/10",
                    data["case_decision"],
                ]
                for name, data in warm["policies"].items()
            ],
        ),
        "",
    ]
    for name, data in warm["policies"].items():
        lines.extend([
            f"### {name}",
            "",
            markdown_table(
                ["순위", "영화", "장르", "점수"],
                [[item["rank"], item["title"], ", ".join(item["genres"]) or "미상", item.get("score", "-")] for item in data["top10"]],
            ),
            "",
            f"- 새로 들어온 영화: {', '.join(data['change_vs_popularity']['entered']) or '없음'}",
            f"- 빠진 영화: {', '.join(data['change_vs_popularity']['exited']) or '없음'}",
            "",
        ])
    lines.extend([
        "## Cold-start: 첫 10편만 알려줬을 때",
        "",
        "이 부분은 같은 사용자 A를 ALS 학습에서 통째로 제외한 별도 cohort-excluded 모델이다. 최초 10개 평점만으로 user factor를 "
        "Fold-in하고, Validation에서 고정한 `0.8 × Popularity + 0.2 × Fold-in`을 사용했다.",
        "",
        markdown_table(
            ["순서", "영화", "평점", "장르"],
            [[item["order"], item["title"], f"{item['rating']:.1f}", ", ".join(item["genres"]) or "미상"] for item in cold["onboarding_first_10"]],
        ),
        "",
        f"cold-start held-out 정답: **{cold['heldout']['title']}**",
        "",
        markdown_table(
            ["정책", "정답 순위", "Top-10", "NDCG@10", "상대 목록 변화"],
            [
                [
                    name,
                    data["metrics"]["heldout_full_catalog_rank"] or "후보 밖",
                    data["metrics"]["heldout_top10_rank"] or "miss",
                    f"{data['metrics']['ndcg_at_10']:.6f}",
                    f"Popularity 대비 {data['change_vs_popularity']['overlap_at_10']}/10 겹침",
                ]
                for name, data in cold["policies"].items()
            ],
        ),
        "",
    ])
    for name, data in cold["policies"].items():
        lines.extend([
            f"### K10 {name}",
            "",
            markdown_table(
                ["순위", "영화", "장르", "점수"],
                [[item["rank"], item["title"], ", ".join(item["genres"]) or "미상", item.get("score", "-")] for item in data["top10"]],
            ),
            "",
        ])
    lines.extend([
        "## 이 한 사람에서 실제로 드러난 변화",
        "",
        f"- 장르 Content는 Popularity와 {warm['policies']['CONTENT_GENRE']['change_vs_popularity']['overlap_at_10']}/10만 겹쳤고 "
        f"목록 내 장르 다양성이 {warm['policies']['CONTENT_GENRE']['metrics']['intra_list_diversity']:.3f}이었다. "
        "선호 장르 조합만 정확히 반복하는 과특화가 실제 제목 목록에서 확인된다.",
        f"- Hybrid 25%는 Popularity와 {warm['policies']['HYBRID_CONTENT_25']['change_vs_popularity']['overlap_at_10']}/10만 겹치며 "
        "Sci-Fi 중심으로 바뀌었다. 사용자 취향은 더 잘 보이지만 이 held-out 정답 순위와 전체 cohort NDCG는 개선하지 못했다.",
        f"- raw ALS는 Popularity와 {warm['policies']['ALS_WARM']['change_vs_popularity']['overlap_at_10']}/10 겹쳤고, "
        f"평균 novelty가 {warm['policies']['ALS_WARM']['metrics']['novelty_bits']:.3f} bits였다. 희귀·메타데이터 미상 영화의 "
        "내적 점수가 5점 범위를 넘어 상단을 점유하므로 보정되지 않은 ALS dot product를 그대로 추천 순위에 쓰면 안 된다.",
        f"- Explore 5%도 Popularity와 {warm['policies']['EXPLORE_05_ON_POPULARITY']['change_vs_popularity']['overlap_at_10']}/10만 겹쳤다. "
        "가중치는 작아도 greedy marginal diversity가 매 단계 작동해 상위 목록을 전부 교체할 수 있다.",
        f"- K10 Fold-in은 10편 중 {10 - cold['policies']['FOLDIN_BLEND_ALPHA_0_2']['change_vs_popularity']['overlap_at_10']}편을 바꿨지만 "
        f"이 사용자의 cold held-out 순위는 {cold['policies']['POPULARITY']['metrics']['heldout_full_catalog_rank']:,}위에서 "
        f"{cold['policies']['FOLDIN_BLEND_ALPHA_0_2']['metrics']['heldout_full_catalog_rank']:,}위로 악화됐다. 전체 1,323명 평균의 작은 양의 효과가 "
        "모든 개인의 개선을 뜻하지 않는다.",
        "",
        "## 무엇을 채용했고 무엇을 버렸나",
        "",
        markdown_table(
            ["후보", "전체 사용자 근거", "현재 판단"],
            [
                ["Popularity", "REC-EV-004B Test NDCG@10 0.009382", "로컬 fallback 유지"],
                ["장르 Content", "0.000955; coverage는 넓지만 relevance 급락", "단독 ranking 기각"],
                ["Hybrid 25%", "0.007435; Popularity보다 낮음", "고정 weight 기각"],
                ["Explore 5%", "0.005113; paired CI가 명확히 음수", "2+1 및 weight 기각"],
                ["Warm ALS", "REC-EV-002 sampled NDCG가 Popularity보다 낮고 전체 coverage 11.74%", "champion 기각; 진단만 유지"],
                ["K10 Fold-in 20%", "REC-EV-011 NDCG 0.004723→0.006154; paired CI [0.000253, 0.002783]", "offline candidate만 유지"],
            ],
        ),
        "",
        "사용자 A 한 명의 성공·실패 때문에 채택한 항목은 없다. 사례는 알고리즘이 무엇을 바꾸는지 설명하고 버그를 찾는 자료이며, "
        "채택 판단은 잠긴 전체 cohort 결과로 한다. 현재 개인 ranking champion은 여전히 `null`이다.",
        "",
        "## 모델별 실제 계산",
        "",
        "- Popularity: Train 평점 평균을 50개 prior로 Bayesian shrinkage.",
        "- Content: 사용자 Train 평균을 뺀 장르 선호 벡터와 영화 장르 cosine.",
        "- Hybrid: 전체 후보 내 percentile로 정규화한 뒤 `0.75 Popularity + 0.25 Content`.",
        "- ALS warm: explicit ALS rank 32, regParam 0.1, 10 iterations, seed 42의 user/item factor 내적.",
        "- Explore: Popularity Top-500 안에서 `0.95 popularity + 0.05 × (novelty/2 + marginal genre diversity/2)` greedy 재정렬.",
        "- K10 Fold-in: 평가 사용자를 ALS 학습에서 제외하고 최초 10개 평점으로 user factor만 풀어 `0.8 popularity + 0.2 fold-in`.",
        "",
        "## 한계",
        "",
        "- MovieLens의 미평가는 싫어요가 아니며 held-out 한 편도 사용자의 전체 만족도를 대표하지 않는다.",
        "- 장르만 사용한 Content는 감독·배우·키워드·줄거리 임베딩이 없다.",
        "- 전역 시간 분할의 사용자 유입 구조가 FEELM 가입자와 같다는 보장은 없다.",
        "- 사용자 A는 설명용 단일 사례다. 일반화는 REC-EV-004B/011 aggregate와 paired CI만 담당한다.",
        "",
        "## 재현",
        "",
        "```powershell",
        "py -3.12 scripts/recommendation_user_case_study.py",
        "```",
        "",
        "스크립트는 모든 입력 artifact checksum을 확인하고 결과 JSON, manifest, 이 문서를 같은 고정 규칙으로 다시 만든다.",
    ])
    return "\n".join(lines) + "\n"


def build_result(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    exploration_args = argparse.Namespace(
        split_manifest=args.split_manifest,
        baseline_manifest=args.baseline_manifest,
        rec_ev_004_manifest=args.rec_ev_004_manifest,
        archive=args.archive,
    )
    split, baseline, train, test, bias, genre_matrix, genre_available, bias_path = load_common(
        exploration_args, "test"
    )
    archive = args.archive or Path(split["source"]["archive"])
    metadata = load_movie_metadata(archive)
    vocabulary = sorted({genre for genres in metadata["genres"] for genre in genres})

    train_users = train["user_id"].to_numpy(dtype=np.int64)
    train_ratings = train["rating"].to_numpy(dtype=np.float64)
    profile_matrix, profile_totals = build_profile_count_matrix(
        train_users, train_ratings, len(bias["user_counts"])
    )
    warm_positives = select_warm_positives(
        test,
        bias["user_counts"].astype(np.int64, copy=False),
        bias["movie_counts"].astype(np.int64, copy=False),
        profile_matrix,
        profile_totals,
    )

    cold_args = argparse.Namespace(
        cold_start_manifest=args.cold_start_manifest,
        dual_head_manifest=args.dual_head_manifest,
        baseline_manifest=args.baseline_manifest,
    )
    cold_source = load_sources(cold_args)
    cold_arrays = prepare_arrays(cold_source)
    cold_positives = cold_source["positives"].loc[~cold_source["selection"]]
    eligible = np.intersect1d(
        warm_positives["user_id"].to_numpy(dtype=np.int64),
        cold_positives["user_id"].to_numpy(dtype=np.int64),
        assume_unique=True,
    )
    user = stable_case_user(eligible)

    user_history = train.loc[train["user_id"] == user].copy()
    preference, exposure = user_genre_profiles(train, np.asarray([user]), genre_matrix)
    preference, exposure = preference[0], exposure[0]
    genres = genre_diagnostics(preference, exposure, vocabulary)

    warm_row = warm_positives.loc[warm_positives["user_id"] == user].iloc[0]
    warm_positive = int(warm_row["movie_id"])
    warm_observed = test.loc[
        (test["user_id"] == user)
        & (test["movie_id"] == warm_positive)
        & (test["timestamp"] == int(warm_row["timestamp"]))
    ].iloc[-1]
    relative_utility = rating_midrank_ecdf(
        np.asarray([float(warm_observed["rating"])]),
        np.asarray([user], dtype=np.int64),
        profile_matrix,
        profile_totals,
        profile_matrix.sum(axis=0),
        shrinkage=20.0,
    )[0]

    movie_counts = bias["movie_counts"].astype(np.int64, copy=False)
    universe = np.flatnonzero(movie_counts > 0).astype(np.int64)
    dense_position = np.full(len(movie_counts), -1, dtype=np.int64)
    dense_position[universe] = np.arange(len(universe), dtype=np.int64)
    popularity = predict_popularity(
        universe,
        float(bias["global_mean"]),
        movie_counts,
        bias["movie_sums"].astype(np.float64, copy=False),
        prior=50.0,
    )
    novelty = -np.log2((movie_counts[universe] + 1.0) / (movie_counts.sum() + len(universe)))
    pop_pct = rankdata(popularity, method="average") / len(universe)
    novelty_pct = rankdata(novelty, method="average") / len(universe)
    content = genre_matrix[universe] @ preference
    content_pct = rankdata(content, method="average") / len(universe)
    scores = {
        "POPULARITY": pop_pct.copy(),
        "CONTENT_GENRE": content_pct.copy(),
        "HYBRID_CONTENT_25": 0.75 * pop_pct + 0.25 * content_pct,
    }
    seen = build_seen(train, np.asarray([user]))[user]
    seen_positions = dense_position[seen]
    seen_positions = seen_positions[seen_positions >= 0]
    for values in scores.values():
        values[seen_positions] = -np.inf

    als = np.load(exact_artifact(baseline["artifacts"]["als_model"]), allow_pickle=False)
    user_position = int(np.searchsorted(als["user_ids"], user))
    if user_position >= len(als["user_ids"]) or int(als["user_ids"][user_position]) != user:
        raise RuntimeError("case user is absent from warm ALS factors")
    if not np.array_equal(als["movie_ids"], universe):
        raise RuntimeError("ALS movie factor order differs from Train-known universe")
    als_score = als["movie_factors"].astype(np.float64) @ als["user_factors"][user_position].astype(np.float64)
    als_score[seen_positions] = -np.inf
    scores["ALS_WARM"] = als_score

    candidate_sets = {
        name: deterministic_top_k(universe, values, TOP_CANDIDATES) for name, values in scores.items()
    }
    pop_candidates = candidate_sets["POPULARITY"]
    top10 = {name: movies[:TOP_K] for name, movies in candidate_sets.items()}
    top10["EXPLORE_05_ON_POPULARITY"] = explore_top10(
        pop_candidates,
        dense_position[pop_candidates],
        pop_pct,
        novelty_pct,
        genre_matrix,
        genre_available,
    )
    candidate_sets["EXPLORE_05_ON_POPULARITY"] = pop_candidates
    policy_scores: dict[str, np.ndarray | None] = {**scores, "EXPLORE_05_ON_POPULARITY": None}

    warm_policies: dict[str, Any] = {}
    for name in POLICIES:
        values = policy_scores[name]
        score_map = (
            None
            if values is None
            else {int(movie): float(values[dense_position[int(movie)]]) for movie in top10[name]}
        )
        warm_policies[name] = {
            "top10": ranked_movies(top10[name], metadata, score_by_movie=score_map),
            "metrics": policy_metrics(
                top10[name],
                warm_positive,
                universe,
                values,
                movie_counts,
                genre_matrix,
                genre_available,
                exposure,
                candidate_movies=candidate_sets[name],
            ),
            "change_vs_popularity": policy_change(top10["POPULARITY"], top10[name], metadata),
            "case_decision": "reference" if name == "POPULARITY" else "case diagnostic only",
        }

    cold_positive = int(cold_positives.loc[cold_positives["user_id"] == user, "movie_id"].iloc[0])
    cold_pop, cold_fold = user_scores(cold_arrays, user, K)
    cold_blend = blend_scores(cold_pop, cold_fold, FOLDIN_ALPHA)
    cold_scores = {"POPULARITY": cold_pop, "FOLDIN_BLEND_ALPHA_0_2": cold_blend}
    cold_top = {name: deterministic_top_k(cold_arrays["universe"], value, TOP_K) for name, value in cold_scores.items()}
    onboarding = cold_source["onboarding"].loc[
        (cold_source["onboarding"]["user_id"] == user)
        & (cold_source["onboarding"]["onboarding_order"] <= K)
    ].sort_values("onboarding_order")
    onboarding_movies = [
        {
            "order": int(row.onboarding_order),
            "rating": float(row.rating),
            **describe_movie(int(row.movie_id), metadata),
        }
        for row in onboarding.itertuples(index=False)
    ]
    onboarding_for_profile = onboarding.copy()
    onboarding_for_profile["timestamp"] = onboarding_for_profile["onboarding_order"]
    _, onboarding_exposure = user_genre_profiles(
        onboarding_for_profile,
        np.asarray([user]),
        genre_matrix,
    )
    cold_policies: dict[str, Any] = {}
    for name, values in cold_scores.items():
        score_map = {
            int(movie): float(values[np.searchsorted(cold_arrays["universe"], int(movie))])
            for movie in cold_top[name]
        }
        cold_policies[name] = {
            "top10": ranked_movies(cold_top[name], metadata, score_by_movie=score_map),
            "metrics": policy_metrics(
                cold_top[name],
                cold_positive,
                cold_arrays["universe"],
                values,
                movie_counts,
                genre_matrix,
                genre_available,
                onboarding_exposure[0],
            ),
            "change_vs_popularity": policy_change(cold_top["POPULARITY"], cold_top[name], metadata),
        }

    result = {
        "schema_version": 1,
        "evidence_id": "REC-EV-016",
        "protocol": PROTOCOL,
        "case": {
            "alias": CASE_ALIAS,
            "selection": "minimum SHA-256 over fixed salt and internal id in warm/cold evaluation intersection",
            "selection_uses_model_outcome": False,
            "eligible_intersection_users": len(eligible),
            "raw_user_id_tracked": False,
            "rating_style": rating_style(user_history, bias),
            "genre_diagnostics": genres,
            "highest_rated_examples": history_examples(user_history, metadata, highest=True),
            "lowest_rated_examples": history_examples(user_history, metadata, highest=False),
        },
        "warm_full_catalog": {
            "candidate_universe": len(universe),
            "train_seen_excluded": len(seen),
            "positive_injection": False,
            "heldout": {
                **describe_movie(warm_positive, metadata),
                "rating": float(warm_observed["rating"]),
                "relative_utility": round(float(relative_utility), 6),
            },
            "policies": warm_policies,
        },
        "cold_start_k10": {
            "candidate_universe": len(cold_arrays["universe"]),
            "positive_injection": False,
            "model_training_excludes_case_user": True,
            "selected_alpha": FOLDIN_ALPHA,
            "onboarding_first_10": onboarding_movies,
            "heldout": describe_movie(cold_positive, metadata),
            "policies": cold_policies,
        },
        "decision": {
            "case_can_select_champion": False,
            "personal_ranking_champion": None,
            "fallback": "POPULARITY",
            "offline_candidate": "K10_FULL_CATALOG_OFFLINE_CANDIDATE",
            "fixed_content_hybrid_adopted": False,
            "fixed_exploration_weight_adopted": False,
            "expected_star_public_ui_approved": False,
        },
    }
    sources = {
        "split_manifest_sha256": sha256(args.split_manifest),
        "baseline_manifest_sha256": sha256(args.baseline_manifest),
        "rec_ev_004_manifest_sha256": sha256(args.rec_ev_004_manifest),
        "rec_ev_004b_manifest_sha256": sha256(args.rec_ev_004b_manifest),
        "cold_start_manifest_sha256": sha256(args.cold_start_manifest),
        "dual_head_manifest_sha256": sha256(args.dual_head_manifest),
        "rec_ev_011_manifest_sha256": sha256(args.rec_ev_011_manifest),
        "archive_sha256": sha256(archive),
        "bias_parameters_sha256": sha256(bias_path),
    }
    return result, sources


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="REC-EV-016 deterministic MovieLens user A case study")
    parser.add_argument("--split-manifest", type=Path, default=Path("docs/recommendation/evidence/manifests/global-time-v1.json"))
    parser.add_argument("--baseline-manifest", type=Path, default=Path("docs/recommendation/evidence/manifests/rec-ev-002.json"))
    parser.add_argument("--rec-ev-004-manifest", type=Path, default=Path("docs/recommendation/evidence/manifests/rec-ev-004.json"))
    parser.add_argument("--rec-ev-004b-manifest", type=Path, default=Path("docs/recommendation/evidence/manifests/rec-ev-004b.json"))
    parser.add_argument("--cold-start-manifest", type=Path, default=Path("docs/recommendation/evidence/manifests/rec-ev-003.json"))
    parser.add_argument("--dual-head-manifest", type=Path, default=Path("docs/recommendation/evidence/manifests/rec-ev-003b.json"))
    parser.add_argument("--rec-ev-011-manifest", type=Path, default=Path("docs/recommendation/evidence/manifests/rec-ev-011.json"))
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--result", type=Path, default=Path("docs/recommendation/evidence/results/rec-ev-016-user-case-a.json"))
    parser.add_argument("--manifest", type=Path, default=Path("docs/recommendation/evidence/manifests/rec-ev-016.json"))
    parser.add_argument("--evidence", type=Path, default=Path("docs/recommendation/evidence/REC-EV-016-user-case-a.md"))
    return parser.parse_args()


def run(args: argparse.Namespace) -> None:
    rec004b = json.loads(args.rec_ev_004b_manifest.read_text(encoding="utf-8"))
    rec011 = json.loads(args.rec_ev_011_manifest.read_text(encoding="utf-8"))
    if rec004b.get("evidence_id") != "REC-EV-004B" or rec011.get("evidence_id") != "REC-EV-011":
        raise RuntimeError("aggregate evidence source mismatch")
    result, sources = build_result(args)
    write_json(args.result, result)
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(evidence_markdown(result), encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "evidence_id": "REC-EV-016",
        "protocol": {
            "version": PROTOCOL,
            "case_selection": "fixed salted SHA-256 minimum before inspecting algorithm outcomes",
            "candidate_universe": "all Train-known movies",
            "positive_injection": False,
            "tie_break": "score descending then movieId ascending",
            "raw_user_id_tracked": False,
        },
        "sources": sources,
        "artifacts": {"result": artifact(args.result), "evidence": artifact(args.evidence)},
        "validation": {
            "status": "PASS",
            "same_user_across_policy_variants": True,
            "case_selection_uses_model_outcome": False,
            "aggregate_evidence_controls_adoption": True,
            "case_selects_champion": False,
        },
        "conclusion": result["decision"],
    }
    write_json(args.manifest, manifest)
    print(json.dumps({
        "status": "PASS",
        "evidence_id": "REC-EV-016",
        "alias": CASE_ALIAS,
        "eligible_intersection_users": result["case"]["eligible_intersection_users"],
        "champion": None,
    }, ensure_ascii=False))


if __name__ == "__main__":
    run(parse_args())
