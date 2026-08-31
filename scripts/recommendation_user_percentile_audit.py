from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from scipy.stats import rankdata

from recommendation_baseline_calibration import build_profile_count_matrix, predict_popularity
from recommendation_cold_start_full_catalog import rank_of_positive
from recommendation_exploration_full_catalog import (
    artifact,
    exact_artifact,
    load_common,
    select_warm_positives,
    sha256,
    write_json,
)
from recommendation_relational_ablation import (
    BATCH_SIZE,
    build_tag_matrix,
    build_tag_user_profiles,
    load_train_tags,
)


EVIDENCE_ID = "REC-EV-018"
PROTOCOL = "rec-ev-018-user-percentile-audit-v1"
TOP_K = 10
TOP_CANDIDATES = 500
BOOTSTRAP_REPEATS = 1_000
SEED = 20260830
POLICY_KEYS = ("popularity", "hybrid", "tag_content")
POLICY_LABELS = {
    "popularity": "POPULARITY",
    "hybrid": "HYBRID_TAG_LOCKED",
    "tag_content": "TAG_CONTENT",
}


def stable_user_alias(user_id: int) -> str:
    digest = hashlib.sha256(f"{EVIDENCE_ID}:user:{user_id}".encode("utf-8")).hexdigest()
    return f"U-{digest[:12]}"


def rank_percentile(rank: int | None, candidate_count: int) -> float:
    if rank is None or candidate_count <= 0:
        return 0.0
    if candidate_count == 1:
        return 1.0
    return 1.0 - (rank - 1) / (candidate_count - 1)


def effect_label(delta: float, *, tolerance: float = 1e-12) -> str:
    if delta > tolerance:
        return "BENEFIT"
    if delta < -tolerance:
        return "HARM"
    return "TIE"


def activity_segment(percentile: float) -> str:
    if percentile <= 0.20:
        return "P00_20_LOW"
    if percentile <= 0.50:
        return "P20_50"
    if percentile <= 0.80:
        return "P50_80"
    if percentile <= 0.95:
        return "P80_95"
    return "P95_100_TOP5"


def history_segment(count: int) -> str:
    if count < 10:
        return "K1_9"
    if count < 20:
        return "K10_19"
    if count < 30:
        return "K20_29"
    if count < 50:
        return "K30_49"
    if count < 100:
        return "K50_99"
    return "K100_PLUS"


def quantiles(values: Iterable[float]) -> dict[str, float]:
    array = np.asarray(list(values), dtype=np.float64)
    if not len(array):
        return {key: 0.0 for key in ("p10", "p25", "p50", "p75", "p90")}
    return {
        "p10": round(float(np.quantile(array, 0.10)), 8),
        "p25": round(float(np.quantile(array, 0.25)), 8),
        "p50": round(float(np.quantile(array, 0.50)), 8),
        "p75": round(float(np.quantile(array, 0.75)), 8),
        "p90": round(float(np.quantile(array, 0.90)), 8),
    }


def bootstrap_mean_ci(values: Iterable[float], *, seed: int) -> dict[str, float | int]:
    array = np.asarray(list(values), dtype=np.float64)
    if not len(array):
        return {"mean": 0.0, "ci95_low": 0.0, "ci95_high": 0.0, "repeats": 0}
    rng = np.random.default_rng(seed)
    samples = np.asarray([
        float(np.mean(rng.choice(array, len(array), replace=True)))
        for _ in range(BOOTSTRAP_REPEATS)
    ])
    return {
        "mean": round(float(np.mean(array)), 8),
        "ci95_low": round(float(np.quantile(samples, 0.025)), 8),
        "ci95_high": round(float(np.quantile(samples, 0.975)), 8),
        "repeats": BOOTSTRAP_REPEATS,
    }


def effect_summary(frame: pd.DataFrame, policy: str, *, seed: int) -> dict[str, Any]:
    delta_rank = frame[f"{policy}_delta_rank_percentile"].to_numpy(dtype=np.float64)
    delta_ndcg = frame[f"{policy}_delta_ndcg_at_10"].to_numpy(dtype=np.float64)
    labels = frame[f"{policy}_effect_vs_popularity"]
    total = len(frame)
    counts = labels.value_counts().to_dict()
    ndcg_labels = frame[f"{policy}_delta_ndcg_at_10"].map(effect_label)
    ndcg_counts = ndcg_labels.value_counts().to_dict()
    return {
        "users": total,
        "benefit_users": int(counts.get("BENEFIT", 0)),
        "tie_users": int(counts.get("TIE", 0)),
        "harm_users": int(counts.get("HARM", 0)),
        "benefit_rate": round(float(counts.get("BENEFIT", 0) / total), 6),
        "tie_rate": round(float(counts.get("TIE", 0) / total), 6),
        "harm_rate": round(float(counts.get("HARM", 0) / total), 6),
        "top10_ndcg_user_effect": {
            "benefit_users": int(ndcg_counts.get("BENEFIT", 0)),
            "tie_users": int(ndcg_counts.get("TIE", 0)),
            "harm_users": int(ndcg_counts.get("HARM", 0)),
            "benefit_rate": round(float(ndcg_counts.get("BENEFIT", 0) / total), 6),
            "tie_rate": round(float(ndcg_counts.get("TIE", 0) / total), 6),
            "harm_rate": round(float(ndcg_counts.get("HARM", 0) / total), 6),
        },
        "delta_rank_percentile": {
            "quantiles": quantiles(delta_rank),
            "paired_bootstrap": bootstrap_mean_ci(delta_rank, seed=seed),
        },
        "delta_ndcg_at_10": {
            "quantiles": quantiles(delta_ndcg),
            "paired_bootstrap": bootstrap_mean_ci(delta_ndcg, seed=seed + 1),
        },
    }


def policy_metrics(frame: pd.DataFrame, policy: str) -> dict[str, Any]:
    return {
        "users": len(frame),
        "candidate_recall_at_500": round(float(frame[f"{policy}_candidate_hit_at_500"].mean()), 6),
        "ndcg_at_10": round(float(frame[f"{policy}_ndcg_at_10"].mean()), 6),
        "recall_at_10": round(float(frame[f"{policy}_recall_at_10"].mean()), 6),
        "mean_rank_percentile": round(float(frame[f"{policy}_rank_percentile"].mean()), 8),
        "rank_percentile_quantiles": quantiles(frame[f"{policy}_rank_percentile"]),
    }


def winner_summary(frame: pd.DataFrame) -> dict[str, Any]:
    values = frame[[f"{policy}_rank_percentile" for policy in POLICY_KEYS]].to_numpy(dtype=np.float64)
    best = values.max(axis=1, keepdims=True)
    winner = np.isclose(values, best, rtol=0.0, atol=1e-12)
    winner_count = winner.sum(axis=1)
    result: dict[str, Any] = {"users": len(frame)}
    for index, policy in enumerate(POLICY_KEYS):
        result[policy] = {
            "best_or_tied_users": int(winner[:, index].sum()),
            "best_or_tied_share": round(float(winner[:, index].mean()), 6),
            "exclusive_win_users": int(np.sum(winner[:, index] & (winner_count == 1))),
            "exclusive_win_share": round(float(np.mean(winner[:, index] & (winner_count == 1))), 6),
            "fractional_winner_share": round(float(np.mean(winner[:, index] / winner_count)), 6),
        }
    result["users_with_cross_policy_tie"] = int(np.sum(winner_count > 1))
    result["cross_policy_tie_rate"] = round(float(np.mean(winner_count > 1)), 6)
    return result


def segment_summary(frame: pd.DataFrame, column: str, policy: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for label, group in frame.groupby(column, observed=True, sort=True):
        effects = group[f"{policy}_effect_vs_popularity"].value_counts().to_dict()
        ndcg_effects = group[f"{policy}_delta_ndcg_at_10"].map(effect_label).value_counts().to_dict()
        result[str(label)] = {
            "users": len(group),
            "ndcg_at_10": round(float(group[f"{policy}_ndcg_at_10"].mean()), 6),
            "recall_at_10": round(float(group[f"{policy}_recall_at_10"].mean()), 6),
            "mean_rank_percentile": round(float(group[f"{policy}_rank_percentile"].mean()), 8),
            "mean_delta_rank_percentile": round(float(group[f"{policy}_delta_rank_percentile"].mean()), 8),
            "mean_delta_ndcg_at_10": round(float(group[f"{policy}_delta_ndcg_at_10"].mean()), 8),
            "benefit_rate": round(float(effects.get("BENEFIT", 0) / len(group)), 6),
            "tie_rate": round(float(effects.get("TIE", 0) / len(group)), 6),
            "harm_rate": round(float(effects.get("HARM", 0) / len(group)), 6),
            "top10_ndcg_benefit_rate": round(float(ndcg_effects.get("BENEFIT", 0) / len(group)), 6),
            "top10_ndcg_tie_rate": round(float(ndcg_effects.get("TIE", 0) / len(group)), 6),
            "top10_ndcg_harm_rate": round(float(ndcg_effects.get("HARM", 0) / len(group)), 6),
        }
    return result


def evaluate_users(
    profiles: Any,
    history: Any,
    users: np.ndarray,
    positives: dict[int, int],
    universe: np.ndarray,
    tag_matrix: Any,
    popularity_percentile: np.ndarray,
    movie_counts: np.ndarray,
    rating_stats: pd.DataFrame,
    *,
    selected_alpha: float,
) -> pd.DataFrame:
    ordered_users = np.asarray(sorted(int(user) for user in users), dtype=np.int64)
    movie_positions = {int(movie): index for index, movie in enumerate(universe)}
    tag_transpose = tag_matrix.T.tocsr()
    stats = rating_stats.set_index("user_id")
    rows: list[dict[str, Any]] = []

    for start in range(0, len(ordered_users), BATCH_SIZE):
        stop = min(start + BATCH_SIZE, len(ordered_users))
        cosine = (profiles[start:stop] @ tag_transpose).toarray().astype(np.float64, copy=False)
        tag_unit = (np.clip(cosine, -1.0, 1.0) + 1.0) / 2.0
        for local, user in enumerate(ordered_users[start:stop]):
            global_row = start + local
            seen = history.indices[history.indptr[global_row]:history.indptr[global_row + 1]]
            positive = positives[int(user)]
            positive_position = movie_positions[positive]
            policy_scores = {
                "popularity": popularity_percentile.copy(),
                "hybrid": (1.0 - selected_alpha) * popularity_percentile + selected_alpha * tag_unit[local],
                "tag_content": tag_unit[local].copy(),
            }
            record: dict[str, Any] = {
                "user_alias": stable_user_alias(int(user)),
                "history_count": int(stats.at[user, "history_count"]),
                "rating_mean": float(stats.at[user, "rating_mean"]),
                "rating_std": float(stats.at[user, "rating_std"]),
                "positive_rating_count": int(movie_counts[positive]),
                "tag_profile_available": bool(profiles.indptr[global_row + 1] > profiles.indptr[global_row]),
                "heldout_tag_available": bool(tag_matrix.indptr[positive_position + 1] > tag_matrix.indptr[positive_position]),
            }
            for policy, score in policy_scores.items():
                score[seen] = -np.inf
                candidate_count = int(np.isfinite(score).sum())
                rank = rank_of_positive(universe, score, positive)
                record[f"{policy}_rank"] = int(rank) if rank is not None else candidate_count + 1
                record[f"{policy}_rank_percentile"] = rank_percentile(rank, candidate_count)
                record[f"{policy}_candidate_hit_at_500"] = int(rank is not None and rank <= TOP_CANDIDATES)
                record[f"{policy}_recall_at_10"] = int(rank is not None and rank <= TOP_K)
                record[f"{policy}_ndcg_at_10"] = (
                    1.0 / math.log2(rank + 1) if rank is not None and rank <= TOP_K else 0.0
                )
            rows.append(record)

    frame = pd.DataFrame(rows).sort_values("user_alias", kind="stable").reset_index(drop=True)
    frame["activity_percentile"] = frame["history_count"].rank(method="average", pct=True)
    frame["activity_percentile_segment"] = frame["activity_percentile"].map(activity_segment)
    frame["history_segment"] = frame["history_count"].map(history_segment)
    frame["rating_mean_quartile"] = pd.qcut(
        frame["rating_mean"].rank(method="first"), 4, labels=["Q1_LOW", "Q2", "Q3", "Q4_HIGH"]
    ).astype(str)
    frame["rating_std_quartile"] = pd.qcut(
        frame["rating_std"].rank(method="first"), 4, labels=["Q1_LOW", "Q2", "Q3", "Q4_HIGH"]
    ).astype(str)
    frame["positive_popularity_quartile"] = pd.qcut(
        frame["positive_rating_count"].rank(method="first"),
        4,
        labels=["P1_LONG_TAIL", "P2", "P3", "P4_HEAD"],
    ).astype(str)
    for policy in ("hybrid", "tag_content"):
        frame[f"{policy}_delta_rank_percentile"] = (
            frame[f"{policy}_rank_percentile"] - frame["popularity_rank_percentile"]
        )
        frame[f"{policy}_delta_ndcg_at_10"] = (
            frame[f"{policy}_ndcg_at_10"] - frame["popularity_ndcg_at_10"]
        )
        frame[f"{policy}_effect_vs_popularity"] = frame[f"{policy}_delta_rank_percentile"].map(effect_label)
    return frame


def table(headers: list[str], rows: list[list[Any]]) -> str:
    return "\n".join([
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
        *("| " + " | ".join(str(value) for value in row) + " |" for row in rows),
    ])


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def report_markdown(result: dict[str, Any]) -> str:
    metrics = result["metrics"]
    effects = result["effects_vs_popularity"]
    winners = result["winner_share"]
    hybrid = effects["hybrid"]
    lines = [
        "# REC-EV-018 사용자별 성능·백분위 감사 결과",
        "",
        "> 상태: `COMPLETED_CURRENT_POLICY_HETEROGENEITY_AUDIT` — 현재 MovieLens 자유 태그 실험의 사용자별 차이를 측정한 결과이며 TMDB Hybrid 결과가 아니다.",
        "",
        "## 한눈에 보는 결론",
        "",
        f"- 평가 사용자: **{result['cohort']['users']:,}명**, 후보: 사용자별 Train-seen을 제외한 **{result['cohort']['candidate_universe']:,}편**.",
        f"- 고정 Hybrid(alpha={result['protocol']['selected_alpha']})는 Popularity보다 held-out 영화의 전체 순위를 **{pct(hybrid['benefit_rate'])} 사용자에서 개선**, **{pct(hybrid['harm_rate'])}에서 악화**, **{pct(hybrid['tie_rate'])}에서 동일**하게 만들었다.",
        f"- Top-10 NDCG 사용자 단위로는 **{pct(hybrid['top10_ndcg_user_effect']['benefit_rate'])} 개선 / {pct(hybrid['top10_ndcg_user_effect']['tie_rate'])} 동일 / {pct(hybrid['top10_ndcg_user_effect']['harm_rate'])} 악화**다. 전체 순위 개선과 Top-10 진입 개선은 같은 지표가 아니다.",
        f"- Top-10 NDCG는 `{metrics['popularity']['ndcg_at_10']:.6f} → {metrics['hybrid']['ndcg_at_10']:.6f}`였지만, 사용자별 ΔNDCG 중앙값은 `{hybrid['delta_ndcg_at_10']['quantiles']['p50']:+.6f}`다.",
        f"- Hybrid가 단독 1위인 사용자는 **{pct(winners['hybrid']['exclusive_win_share'])}**, Popularity 단독 1위는 **{pct(winners['popularity']['exclusive_win_share'])}**, Tag 단독 1위는 **{pct(winners['tag_content']['exclusive_win_share'])}**였다.",
        "- 따라서 한 고정 로직이 모든 사용자에게 최선이라는 근거는 없다. 다만 이 결과는 사용자별 winner를 사후에 본 oracle 진단이며, Router가 예측 시점에 winner를 맞힐 수 있다는 증거는 아니다.",
        "",
        "## 1. 정책별 전체 성능",
        "",
        table(
            ["정책", "NDCG@10", "Recall@10", "Recall@500", "평균 rank percentile", "rank p10", "p50", "p90"],
            [[
                POLICY_LABELS[policy],
                f"{value['ndcg_at_10']:.6f}",
                f"{value['recall_at_10']:.6f}",
                f"{value['candidate_recall_at_500']:.6f}",
                f"{value['mean_rank_percentile']:.6f}",
                f"{value['rank_percentile_quantiles']['p10']:.6f}",
                f"{value['rank_percentile_quantiles']['p50']:.6f}",
                f"{value['rank_percentile_quantiles']['p90']:.6f}",
            ] for policy, value in metrics.items()],
        ),
        "",
        "`rank percentile=1`은 held-out 영화가 전체 후보 중 1위, `0`은 최하위라는 뜻이다. Top-10 밖에서도 순위가 얼마나 이동했는지 보기 위해 NDCG와 함께 사용했다.",
        "",
        "## 2. 사용자별 이익과 손해",
        "",
        table(
            ["정책", "전체순위 B/T/H", "Top-10 NDCG B/T/H", "Δrank 평균 [95% CI]", "Δrank p10", "p50", "p90", "ΔNDCG 평균 [95% CI]"],
            [[
                POLICY_LABELS[policy],
                f"{pct(value['benefit_rate'])} / {pct(value['tie_rate'])} / {pct(value['harm_rate'])}",
                f"{pct(value['top10_ndcg_user_effect']['benefit_rate'])} / {pct(value['top10_ndcg_user_effect']['tie_rate'])} / {pct(value['top10_ndcg_user_effect']['harm_rate'])}",
                f"{value['delta_rank_percentile']['paired_bootstrap']['mean']:+.6f} [{value['delta_rank_percentile']['paired_bootstrap']['ci95_low']:+.6f}, {value['delta_rank_percentile']['paired_bootstrap']['ci95_high']:+.6f}]",
                f"{value['delta_rank_percentile']['quantiles']['p10']:+.6f}",
                f"{value['delta_rank_percentile']['quantiles']['p50']:+.6f}",
                f"{value['delta_rank_percentile']['quantiles']['p90']:+.6f}",
                f"{value['delta_ndcg_at_10']['paired_bootstrap']['mean']:+.6f} [{value['delta_ndcg_at_10']['paired_bootstrap']['ci95_low']:+.6f}, {value['delta_ndcg_at_10']['paired_bootstrap']['ci95_high']:+.6f}]",
            ] for policy, value in effects.items()],
        ),
        "",
        "Benefit/Harm은 Top-10 hit만이 아니라 전체 카탈로그에서 held-out 영화 순위가 Popularity보다 올라갔는지 내려갔는지로 정의했다. 미평가 영화를 싫어요로 간주하지 않았다.",
        "",
        "## 3. 어떤 모델이 어떤 사용자에게 가장 좋았나",
        "",
        table(
            ["정책", "단독 1위", "공동 1위 포함", "동점 분할 winner share"],
            [[
                POLICY_LABELS[policy],
                f"{value['exclusive_win_users']:,} ({pct(value['exclusive_win_share'])})",
                f"{value['best_or_tied_users']:,} ({pct(value['best_or_tied_share'])})",
                pct(value["fractional_winner_share"]),
            ] for policy, value in winners.items() if policy in POLICY_KEYS],
        ),
        "",
        f"정책 간 공동 1위는 {winners['users_with_cross_policy_tie']:,}명({pct(winners['cross_policy_tie_rate'])})이다. 이 winner 분포는 Personalized Router를 실험할 이유지만, Router Feature가 사전에 winner를 구별하는지는 별도 out-of-fold 실험으로 검증해야 한다.",
        "",
        "## 4. 활동량 백분위별 Hybrid",
        "",
        table(
            ["Train 활동량", "사용자", "NDCG@10", "ΔNDCG", "평균 rank pct", "평균 Δrank", "Rank B/T/H"],
            [[
                segment,
                f"{value['users']:,}",
                f"{value['ndcg_at_10']:.6f}",
                f"{value['mean_delta_ndcg_at_10']:+.6f}",
                f"{value['mean_rank_percentile']:.6f}",
                f"{value['mean_delta_rank_percentile']:+.6f}",
                f"{pct(value['benefit_rate'])} / {pct(value['tie_rate'])} / {pct(value['harm_rate'])}",
            ] for segment, value in result["segments"]["hybrid"]["activity_percentile_segment"].items()],
        ),
        "",
        "활동량 상위 5%는 MovieLens Train 평가 수 기준이며 서비스의 가치가 높은 사용자라는 뜻이 아니다. 사용자 특성 percentile과 모델 효과 percentile을 분리해서 읽는다.",
        f"활동량 상위 5%는 전체 rank percentile 평균이 `+{result['segments']['hybrid']['activity_percentile_segment']['P95_100_TOP5']['mean_delta_rank_percentile']:.6f}` 이동했지만 Top-10 ΔNDCG는 `{result['segments']['hybrid']['activity_percentile_segment']['P95_100_TOP5']['mean_delta_ndcg_at_10']:+.6f}`로 악화됐다.",
        "",
        "## 5. 이력 수와 held-out 영화 인기도별 Hybrid",
        "",
        "### 이력 수",
        "",
        table(
            ["이력", "사용자", "NDCG@10", "ΔNDCG", "평균 Δrank", "Rank Benefit", "Rank Harm"],
            [[
                segment,
                f"{value['users']:,}",
                f"{value['ndcg_at_10']:.6f}",
                f"{value['mean_delta_ndcg_at_10']:+.6f}",
                f"{value['mean_delta_rank_percentile']:+.6f}",
                pct(value["benefit_rate"]),
                pct(value["harm_rate"]),
            ] for segment, value in result["segments"]["hybrid"]["history_segment"].items()],
        ),
        "",
        "여기의 K30~49는 사용자의 전체 Train 이력 구간이지, 최초 30편만 제공한 K-shot 실험이 아니다. K30 K-shot은 사용자 제외 ALS factor를 다시 계산하는 별도 실험이 필요하다. 기존 REC-EV-017의 `K20_49` 표시는 실제 코드가 `(0,49]`를 묶어 K1~19까지 포함했던 오표기이며, 이 보고서에서 실제 구간으로 교정했다.",
        "",
        "### held-out 영화 인기도",
        "",
        table(
            ["영화 구간", "사용자", "NDCG@10", "ΔNDCG", "평균 Δrank", "Rank Benefit", "Rank Harm"],
            [[
                segment,
                f"{value['users']:,}",
                f"{value['ndcg_at_10']:.6f}",
                f"{value['mean_delta_ndcg_at_10']:+.6f}",
                f"{value['mean_delta_rank_percentile']:+.6f}",
                pct(value["benefit_rate"]),
                pct(value["harm_rate"]),
            ] for segment, value in result["segments"]["hybrid"]["positive_popularity_quartile"].items()],
        ),
        "",
        "P2에서는 전체 순위 기준 Benefit이 많아도 Top-10 ΔNDCG가 음수다. 반대로 P4 인기작 구간의 큰 Top-10 상승이 전체 평균 개선을 주도한다. 따라서 전체순위와 Top-10 중 하나만 보고 사용자에게 좋아졌다고 말하면 안 된다.",
        "",
        "## 6. 판정",
        "",
        f"- 현재 고정 Tag Hybrid의 사용자 Benefit rate는 {pct(hybrid['benefit_rate'])}, Harm rate는 {pct(hybrid['harm_rate'])}다. 평균 NDCG 개선만으로 전체 사용자 적용을 승인하지 않는다.",
        "- 여러 정책이 서로 다른 사용자에게 단독 1위를 차지하므로 Personalized Router 가설은 유지한다.",
        "- 그러나 현재 Feature는 MovieLens 자유 태그이며 영화 정보 기준으로 사용할 수 없다. Tag Hybrid를 제품 후보로 승격하지 않는다.",
        "- 다음 실험은 동일 artifact 계약을 유지한 채 TMDB Structured/Text Content와 ALS·ItemKNN을 추가해야 한다.",
        "- Router는 Validation out-of-fold 사용자 winner로 학습하고, 잠긴 Test에서 Single Best Model보다 좋아야 한다.",
        "",
        "## 7. 데이터 경계와 한계",
        "",
        "- 평가 정답은 사용자가 미래에 실제로 남긴 관측 평점 중 Train 기준 상대 상위 항목이다. 미평가 만족도는 모른다.",
        "- 사용자당 held-out 한 편이라 Top-10 NDCG는 0이 많다. 그래서 전체 rank percentile을 함께 보고했지만, 다음 rolling 평가에서는 미래 5~10편을 사용해야 한다.",
        f"- 평가 cohort는 Train-known 사용자·영화이며 미래 양성 평가가 있는 warm 사용자다. 실제 Train 이력은 {result['cohort']['history_min']}편부터 존재하며 신규 가입자 전체를 대표하지 않는다.",
        "- MovieLens 사용자는 FEELM 한국 사용자와 동일하지 않다.",
        "- 본 실험은 기존 결과의 사용자 이질성 감사다. TMDB 전수 Feature, K30/50/100 cold-user, cold-item 평가는 아직 완료되지 않았다.",
        "",
        "## 재현",
        "",
        "```powershell",
        "$env:PYTHONPATH='scripts'",
        "py -3.12 scripts/recommendation_user_percentile_audit.py",
        "Remove-Item Env:PYTHONPATH",
        "```",
    ]
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="REC-EV-018 user-level percentile audit")
    parser.add_argument("--split-manifest", type=Path, default=Path("docs/recommendation/evidence/manifests/global-time-v1.json"))
    parser.add_argument("--baseline-manifest", type=Path, default=Path("docs/recommendation/evidence/manifests/rec-ev-002.json"))
    parser.add_argument("--rec-ev-004-manifest", type=Path, default=Path("docs/recommendation/evidence/manifests/rec-ev-004.json"))
    parser.add_argument("--rec-ev-017-manifest", type=Path, default=Path("docs/recommendation/evidence/manifests/rec-ev-017.json"))
    parser.add_argument("--rec-ev-017-result", type=Path, default=Path("docs/recommendation/evidence/results/rec-ev-017-relational-tag.json"))
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--user-artifact", type=Path, default=Path("outputs/recommendation-evidence/rec-ev-018/user-policy-results.parquet"))
    parser.add_argument("--result", type=Path, default=Path("docs/recommendation/evidence/results/rec-ev-018-user-percentiles.json"))
    parser.add_argument("--manifest", type=Path, default=Path("docs/recommendation/evidence/manifests/rec-ev-018.json"))
    parser.add_argument("--evidence", type=Path, default=Path("docs/recommendation/evidence/REC-EV-018-user-percentile-audit.md"))
    return parser.parse_args()


def run(args: argparse.Namespace) -> None:
    started = time.perf_counter()
    rec017_manifest = json.loads(args.rec_ev_017_manifest.read_text(encoding="utf-8"))
    rec017 = json.loads(args.rec_ev_017_result.read_text(encoding="utf-8"))
    if rec017_manifest.get("evidence_id") != "REC-EV-017" or rec017.get("evidence_id") != "REC-EV-017":
        raise RuntimeError("REC-EV-017 source mismatch")
    if rec017_manifest["artifacts"]["result"]["sha256"] != sha256(args.rec_ev_017_result):
        raise RuntimeError("REC-EV-017 result checksum mismatch")
    selected_alpha = float(rec017["tag_ablation"]["selected_alpha"])
    if selected_alpha <= 0.0 or selected_alpha >= 1.0:
        raise RuntimeError("locked Hybrid alpha must be between Popularity and Tag")

    load_args = argparse.Namespace(
        split_manifest=args.split_manifest,
        baseline_manifest=args.baseline_manifest,
        rec_ev_004_manifest=args.rec_ev_004_manifest,
        archive=args.archive,
    )
    split, _, train, test, bias, _, _, _ = load_common(load_args, "test")
    validation = pd.read_parquet(
        exact_artifact(split["artifacts"]["validation"]),
        columns=["user_id", "movie_id", "rating", "timestamp"],
    )
    movie_counts = bias["movie_counts"].astype(np.int64, copy=False)
    universe = np.flatnonzero(movie_counts > 0).astype(np.int64)
    if len(universe) != 50_977:
        raise RuntimeError("Train-known universe changed")

    profile_matrix, profile_totals = build_profile_count_matrix(
        train["user_id"].to_numpy(dtype=np.int64),
        train["rating"].to_numpy(dtype=np.float64),
        len(bias["user_counts"]),
    )
    positives_frame = select_warm_positives(
        test, bias["user_counts"], movie_counts, profile_matrix, profile_totals
    )
    users = positives_frame["user_id"].to_numpy(dtype=np.int64)
    positives = {int(row.user_id): int(row.movie_id) for row in positives_frame.itertuples(index=False)}
    expected_users = int(rec017["tag_ablation"]["test"]["metrics"][str(selected_alpha)]["users"])
    if len(users) != expected_users:
        raise RuntimeError(f"Test cohort changed: {len(users)} != {expected_users}")

    archive = args.archive or Path(split["source"]["archive"])
    excluded_tag_users = np.union1d(
        validation["user_id"].unique().astype(np.int64),
        test["user_id"].unique().astype(np.int64),
    )
    tags, _ = load_train_tags(
        archive, int(split["protocol"]["train_boundary"]), universe, excluded_tag_users
    )
    tag_matrix, _, _ = build_tag_matrix(tags, universe)
    profiles, history = build_tag_user_profiles(train, users, universe, tag_matrix)
    popularity = predict_popularity(
        universe,
        float(bias["global_mean"]),
        movie_counts,
        bias["movie_sums"].astype(np.float64, copy=False),
        prior=50.0,
    )
    popularity_percentile = rankdata(popularity, method="average") / len(universe)
    rating_stats = (
        train.loc[train["user_id"].isin(users), ["user_id", "rating"]]
        .groupby("user_id", sort=True)["rating"]
        .agg(history_count="count", rating_mean="mean", rating_std="std")
        .reset_index()
    )
    rating_stats["rating_std"] = rating_stats["rating_std"].fillna(0.0)

    frame = evaluate_users(
        profiles,
        history,
        users,
        positives,
        universe,
        tag_matrix,
        popularity_percentile,
        movie_counts,
        rating_stats,
        selected_alpha=selected_alpha,
    )
    args.user_artifact.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(args.user_artifact, index=False, compression="zstd")

    metrics = {policy: policy_metrics(frame, policy) for policy in POLICY_KEYS}
    expected_metrics = rec017["tag_ablation"]["test"]["metrics"]
    source_key = {"popularity": "0.0", "hybrid": str(selected_alpha), "tag_content": "1.0"}
    parity = {}
    for policy, key in source_key.items():
        parity[policy] = all(
            metrics[policy][field] == expected_metrics[key][field]
            for field in ("candidate_recall_at_500", "ndcg_at_10", "recall_at_10", "users")
        )
    if not all(parity.values()):
        raise RuntimeError(f"REC-EV-017 aggregate parity failed: {parity}")

    effects = {
        "hybrid": effect_summary(frame, "hybrid", seed=SEED),
        "tag_content": effect_summary(frame, "tag_content", seed=SEED + 100),
    }
    segments = {
        policy: {
            column: segment_summary(frame, column, policy)
            for column in (
                "activity_percentile_segment",
                "history_segment",
                "rating_mean_quartile",
                "rating_std_quartile",
                "positive_popularity_quartile",
            )
        }
        for policy in ("hybrid", "tag_content")
    }
    result = {
        "schema_version": 1,
        "evidence_id": EVIDENCE_ID,
        "status": "COMPLETED_CURRENT_POLICY_HETEROGENEITY_AUDIT",
        "protocol": {
            "version": PROTOCOL,
            "selected_alpha": selected_alpha,
            "selection_source": "REC-EV-017 Validation; Test did not retune alpha",
            "candidate_universe": "all 50,977 Train-known movies minus each user's Train-seen movies",
            "heldout": "same REC-EV-017 Test warm latest positive selected with Train-only shrunk ECDF >= 0.7",
            "positive_injection": False,
            "benefit_definition": "heldout full-catalog rank percentile improves over Popularity",
            "raw_user_id_stored": False,
            "bootstrap_unit": "user",
            "bootstrap_repeats": BOOTSTRAP_REPEATS,
        },
        "cohort": {
            "users": len(frame),
            "candidate_universe": len(universe),
            "history_min": int(frame["history_count"].min()),
            "history_median": float(frame["history_count"].median()),
            "history_p95": float(frame["history_count"].quantile(0.95)),
            "history_max": int(frame["history_count"].max()),
            "tag_profile_coverage": round(float(frame["tag_profile_available"].mean()), 6),
            "heldout_tag_coverage": round(float(frame["heldout_tag_available"].mean()), 6),
        },
        "metrics": metrics,
        "effects_vs_popularity": effects,
        "winner_share": winner_summary(frame),
        "segments": segments,
        "validation": {
            "aggregate_parity_with_rec_ev_017": parity,
            "user_rows_unique": bool(frame["user_alias"].is_unique),
            "raw_user_id_absent": "user_id" not in frame.columns,
            "selected_alpha_locked_before_test": True,
            "tmdb_full_feature_used": False,
        },
        "limitations": [
            "single heldout positive per user",
            "warm users with at least 20 Train ratings and an observed future positive",
            "MovieLens free tags are not canonical movie metadata",
            "no full TMDB feature artifact",
            "no K30/50/100 user-disjoint fold-in in this audit",
            "unobserved satisfaction remains unknown",
        ],
        "runtime": {"elapsed_seconds": round(time.perf_counter() - started, 3)},
    }
    write_json(args.result, result)
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(report_markdown(result), encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "evidence_id": EVIDENCE_ID,
        "protocol": result["protocol"],
        "sources": {
            "split_manifest_sha256": sha256(args.split_manifest),
            "baseline_manifest_sha256": sha256(args.baseline_manifest),
            "rec_ev_004_manifest_sha256": sha256(args.rec_ev_004_manifest),
            "rec_ev_017_manifest_sha256": sha256(args.rec_ev_017_manifest),
            "rec_ev_017_result_sha256": sha256(args.rec_ev_017_result),
            "archive_sha256": split["source"]["archive_sha256"],
        },
        "artifacts": {
            "user_results": artifact(args.user_artifact),
            "result": artifact(args.result),
            "evidence": artifact(args.evidence),
        },
        "validation": result["validation"],
        "decision": {
            "fixed_tag_hybrid_adopted": False,
            "personalized_router_hypothesis": "KEEP_FOR_TMDB_OUT_OF_FOLD_TEST",
            "personal_ranking_champion": None,
            "fallback": "POPULARITY",
        },
    }
    write_json(args.manifest, manifest)


if __name__ == "__main__":
    run(parse_args())
