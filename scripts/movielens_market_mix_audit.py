#!/usr/bin/env python3
"""Partition MovieLens into Korean, famous-foreign proxy, and remainder.

The three classes are mutually exclusive. The script reports catalog-weighted,
rating-weighted, and user-macro views so a large sparse catalog is not confused
with the interactions that dominate ALS training.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


CATEGORIES = ("KOREAN_ORIGIN", "FAMOUS_FOREIGN_PROXY", "REMAINDER")
HISTORY_BINS = (
    ("20_49", 20, 49),
    ("50_99", 50, 99),
    ("100_499", 100, 499),
    ("500_PLUS", 500, None),
)
PROGRESS_INTERVAL = 5_000_000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--movies", type=Path, required=True)
    parser.add_argument("--ratings", type=Path, required=True)
    parser.add_argument("--korean-origin-json", type=Path, required=True)
    parser.add_argument("--awareness-json", type=Path, required=True)
    parser.add_argument("--kobis-json", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    return parser.parse_args()


def ratio(numerator: int | float, denominator: int | float) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def percent(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2%}"


def percent_small(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.3%}" if value < 0.001 else f"{value:.2%}"


def nearest_rank(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, int(len(ordered) * probability + 0.999999999))
    return ordered[min(rank - 1, len(ordered) - 1)]


def load_movie_ids(path: Path) -> set[int]:
    movie_ids: set[int] = set()
    with path.open("r", encoding="utf-8", newline="") as handle:
        handle.readline()
        for line in handle:
            movie_text = line.split(",", 1)[0]
            movie_ids.add(int(movie_text))
    return movie_ids


def famous_foreign(item: dict[str, Any]) -> bool:
    return bool(
        item.get("status") == "OK"
        and item.get("is_foreign")
        and int(item.get("rating_count", 0)) >= 1_000
        and item.get("has_korean_title_translation")
        and (
            item.get("has_korean_theatrical_release")
            or item.get("has_current_korean_provider")
        )
    )


def build_category_sets(
    all_movie_ids: set[int],
    korean_origin: dict[str, Any],
    awareness: dict[str, Any],
) -> dict[str, set[int]]:
    korean_ids = {
        int(item["movie_id"])
        for item in korean_origin["movielens"]["matched_items"]
    }
    korean_ids.update(
        int(item["movie_id"])
        for item in awareness["head_items"]
        if item.get("status") == "OK" and item.get("is_korean_origin")
    )
    famous_ids = {
        int(item["movie_id"])
        for item in awareness["head_items"]
        if famous_foreign(item)
    }
    korean_ids &= all_movie_ids
    famous_ids &= all_movie_ids
    famous_ids -= korean_ids
    remainder_ids = all_movie_ids - korean_ids - famous_ids
    return {
        "KOREAN_ORIGIN": korean_ids,
        "FAMOUS_FOREIGN_PROXY": famous_ids,
        "REMAINDER": remainder_ids,
    }


def history_bin(total: int) -> str:
    for name, lower, upper in HISTORY_BINS:
        if total >= lower and (upper is None or total <= upper):
            return name
    return "BELOW_20"


def dominant_category(counts: dict[str, int]) -> str:
    maximum = max(counts.values())
    winners = [name for name, count in counts.items() if count == maximum]
    return winners[0] if len(winners) == 1 else "TIE"


def scan_ratings(path: Path, category_sets: dict[str, set[int]]) -> dict[str, Any]:
    membership: dict[int, str] = {}
    for name, movie_ids in category_sets.items():
        for movie_id in movie_ids:
            if movie_id in membership:
                raise ValueError(f"category overlap for movieId={movie_id}")
            membership[movie_id] = name

    rating_counts = Counter()
    user_share_values = {name: [] for name in CATEGORIES}
    users_with_any = Counter()
    dominant = Counter()
    history = {
        name: {"users": 0, "share_sums": Counter()}
        for name, _, _ in HISTORY_BINS
    }
    users = rows = 0
    current_user: int | None = None
    current_counts = Counter({name: 0 for name in CATEGORIES})
    previous_user: int | None = None
    started = time.perf_counter()

    def finish_user() -> None:
        nonlocal users
        if current_user is None:
            return
        total = sum(current_counts.values())
        if total == 0:
            raise ValueError(f"zero-rating user encountered: {current_user}")
        users += 1
        shares = {name: current_counts[name] / total for name in CATEGORIES}
        for name in CATEGORIES:
            user_share_values[name].append(shares[name])
            users_with_any[name] += current_counts[name] > 0
        dominant[dominant_category(current_counts)] += 1
        bin_name = history_bin(total)
        if bin_name in history:
            history[bin_name]["users"] += 1
            for name in CATEGORIES:
                history[bin_name]["share_sums"][name] += shares[name]
        for name in CATEGORIES:
            current_counts[name] = 0

    with path.open("r", encoding="utf-8", newline="") as handle:
        handle.readline()
        for line in handle:
            user_text, movie_text, _, _ = line.rstrip("\r\n").split(",")
            user_id = int(user_text)
            if previous_user is not None and user_id < previous_user:
                raise ValueError("ratings must be ordered by userId")
            if current_user is not None and user_id != current_user:
                finish_user()
            current_user = user_id
            previous_user = user_id
            movie_id = int(movie_text)
            category = membership.get(movie_id)
            if category is None:
                raise ValueError(f"rating references unknown movieId={movie_id}")
            current_counts[category] += 1
            rating_counts[category] += 1
            rows += 1
            if rows % PROGRESS_INTERVAL == 0:
                print(
                    f"market_mix_rows={rows:,} elapsed_seconds={time.perf_counter() - started:.1f}",
                    flush=True,
                )
        finish_user()

    category_user_stats = {}
    for name in CATEGORIES:
        values = user_share_values[name]
        category_user_stats[name] = {
            "users_with_any": users_with_any[name],
            "user_share_with_any": ratio(users_with_any[name], users),
            "macro_mean_history_share": ratio(sum(values), users),
            "history_share_quantiles": {
                "p10": round(nearest_rank(values, 0.10) or 0.0, 6),
                "p25": round(nearest_rank(values, 0.25) or 0.0, 6),
                "p50": round(nearest_rank(values, 0.50) or 0.0, 6),
                "p75": round(nearest_rank(values, 0.75) or 0.0, 6),
                "p90": round(nearest_rank(values, 0.90) or 0.0, 6),
            },
        }

    history_result = {}
    for bin_name, values in history.items():
        bin_users = values["users"]
        history_result[bin_name] = {
            "users": bin_users,
            "user_share": ratio(bin_users, users),
            "macro_mean_history_share": {
                name: ratio(values["share_sums"][name], bin_users)
                for name in CATEGORIES
            },
        }
    return {
        "users": users,
        "rating_rows": rows,
        "rating_counts": dict(rating_counts),
        "user_macro": category_user_stats,
        "dominant_category": {
            name: {"users": dominant[name], "user_share": ratio(dominant[name], users)}
            for name in (*CATEGORIES, "TIE")
        },
        "history_bins": history_result,
    }


def markdown_table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(str(value) for value in row) + " |" for row in rows)
    return "\n".join(lines)


def build_markdown(result: dict[str, Any]) -> str:
    labels = {
        "KOREAN_ORIGIN": "한국 제작",
        "FAMOUS_FOREIGN_PROXY": "유명 외국 영화 proxy",
        "REMAINDER": "나머지",
        "TIE": "동률",
    }
    partition_rows = []
    for name in CATEGORIES:
        values = result["partition"][name]
        macro = result["user_analysis"]["user_macro"][name]
        partition_rows.append(
            (
                labels[name],
                f"{values['movies']:,}",
                percent(values["movie_share"]),
                f"{values['rating_rows']:,}",
                percent(values["rating_share"]),
                percent(macro["macro_mean_history_share"]),
                percent(macro["history_share_quantiles"]["p50"]),
                percent(macro["user_share_with_any"]),
            )
        )
    dominant_rows = [
        (
            labels[name],
            f"{values['users']:,}",
            percent_small(values["user_share"]),
        )
        for name, values in result["user_analysis"]["dominant_category"].items()
    ]
    history_rows = []
    history_labels = {
        "20_49": "20–49",
        "50_99": "50–99",
        "100_499": "100–499",
        "500_PLUS": "500+",
    }
    for name, values in result["user_analysis"]["history_bins"].items():
        shares = values["macro_mean_history_share"]
        history_rows.append(
            (
                history_labels[name],
                f"{values['users']:,}",
                percent(values["user_share"]),
                percent(shares["KOREAN_ORIGIN"]),
                percent(shares["FAMOUS_FOREIGN_PROXY"]),
                percent(shares["REMAINDER"]),
            )
        )
    sensitivity = result["sensitivity"]
    return f"""# MovieLens 한국 시장 기준 3분할

> 상태: `COMPLETED_DESCRIPTIVE_PARTITION`
> 기준: MovieLens 32M 전체 87,585편, 200,948명, 32,000,204 Rating
> 목적: MovieLens 학습 신호의 시장 편향을 기술적으로 계측한다. 한국 사용자 성능 증명이 아니다.
> 의사결정 상태: 이 보고서의 초기 활용 가설은 `REC-DATA-009`로 대체되었다.

## 결론

MovieLens의 영화 목록만 보면 `나머지`가 {percent(result['partition']['REMAINDER']['movie_share'])}지만,
실제 Rating은 `유명 외국 영화 proxy`에 {percent(result['partition']['FAMOUS_FOREIGN_PROXY']['rating_share'])}
집중돼 있다. 반대로 한국 제작 영화는 영화 수 {percent(result['partition']['KOREAN_ORIGIN']['movie_share'])},
Rating {percent(result['partition']['KOREAN_ORIGIN']['rating_share'])}뿐이다.

이 기술통계만으로 ALS를 FEELM 추천의 기본축으로 확정할 수 없다. 오히려 source-domain ALS가
유명 외국 영화 상호작용을 주로 재현할 가능성이 크다는 위험 근거다. 제품 적용 결정과 무사용자 단계의
대안은 `REC-DATA-009-zero-data-recommendation-strategy.md`를 따른다.

## 서로 겹치지 않는 3분할

{markdown_table(['구분', '영화', '영화 비율', 'Rating', 'Rating 비율', '사용자별 평균', '사용자별 중앙값', '1편 이상 사용자'], partition_rows)}

`사용자별 평균/중앙값`은 각 사용자의 전체 Rating 중 해당 분류가 차지하는 비율을 먼저 계산한 뒤
사용자 200,948명을 동일 가중치로 요약한 값이다. 사용자의 국적·연령을 뜻하지 않는다.

### 분류 정의

1. `한국 제작`: TMDB `origin_country=KR` 또는 `production_countries`에 KR이 확인된 MovieLens 영화.
2. `유명 외국 영화 proxy`: 외국 제작, MovieLens Rating 1,000개 이상, 한국어 제목이 있고,
   한국 극장 개봉 또는 현재 국내 provider가 확인된 영화. KOBIS 2004+ 100만 관객 매칭작의
   영화 recall {percent(result['validation']['kobis_1m_movie_recall'])}, 관객가중 recall
   {percent(result['validation']['kobis_1m_audience_weighted_recall'])}인 보수적 proxy다.
3. `나머지`: 위 두 집합 밖의 저상호작용 외국 영화, 국가 불명, 국내시장 신호 부족 영화 전체.

실제 한국 인지도 확정값이 없으므로 두 번째 이름에 `proxy`를 유지한다. 기준을 MODERATE로 완화하면
유명/인지가능 외국 영화 Rating 비율은 {percent(sensitivity['foreign_moderate_rating_share'])}, STRICT는
{percent(sensitivity['foreign_strict_rating_share'])}로, 핵심 결론은 약 79~84% 범위에서 유지된다.

## 사용자별로도 봐야 하는가

**봐야 하지만 이 표 하나면 충분하다.** 전체 Rating 비율은 Rating을 많이 남긴 사용자의 영향이 커서,
사용자 한 명을 한 표로 동일 가중한 결과와 함께 확인해야 한다.

### 이력에서 가장 큰 분류

{markdown_table(['우세 분류', '사용자', '사용자 비율'], dominant_rows)}

### 사용자 Rating 수별 평균 구성

{markdown_table(['Rating 수', '사용자', '사용자 비율', '한국 제작', '유명 외국 proxy', '나머지'], history_rows)}

## 초기 활용 가설 — `REC-DATA-009`로 대체됨

| 계층 | 역할 | 현재 결정 |
| --- | --- | --- |
| 유명 외국 영화 proxy | ALS가 가장 잘 학습할 수 있는 source-domain 신호 | 연구 비교군으로만 유지, 제품 기본 추천에는 사용하지 않음 |
| 한국 제작 | 목표 시장상 중요하지만 협업 신호가 극소 | 명시적 선호·콘텐츠 후보 생성의 대상. 성능이 증명됐다는 뜻은 아님 |
| 나머지 | 카탈로그 대부분이나 상호작용은 희소 | 후보에서 삭제 금지, 콘텐츠 cold-item·discovery 트랙으로 분리 |

MovieLens 내부 연구 평가를 수행한다면 세 slice의 Recall/NDCG/coverage를 따로 보고한다. 그러나 이는
한국 실사용자 성능 점수가 아니다. 사용자별 추천 목록에 3분할 비율을 quota로 강제하는 것 역시 실제
한국 사용자 로그나 설문 전에는 근거가 없다.

## 경계

- MovieLens 사용자는 한국 사용자가 아니며 인구통계도 없다.
- `유명 외국 영화 proxy`에는 한국에서 덜 알려진 글로벌 인기작이 일부 섞일 수 있다.
- pre-2004 고전은 KOBIS 관객 수로 규모 비교가 불가능하므로 국내 검색/설문 검증을 계속 별도로 둔다.
- 이 자료는 데이터와 평가 방향을 정하지만 production 추천 비율이나 모델 champion을 승인하지 않는다.
"""


def main() -> int:
    args = parse_args()
    required = (
        args.movies,
        args.ratings,
        args.korean_origin_json,
        args.awareness_json,
        args.kobis_json,
    )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError(f"missing inputs: {missing}")

    started = time.perf_counter()
    all_movie_ids = load_movie_ids(args.movies)
    korean_origin = json.loads(args.korean_origin_json.read_text(encoding="utf-8"))
    awareness = json.loads(args.awareness_json.read_text(encoding="utf-8"))
    kobis = json.loads(args.kobis_json.read_text(encoding="utf-8"))
    category_sets = build_category_sets(all_movie_ids, korean_origin, awareness)
    user_analysis = scan_ratings(args.ratings, category_sets)
    total_movies = len(all_movie_ids)
    total_ratings = user_analysis["rating_rows"]
    partition = {
        name: {
            "movies": len(category_sets[name]),
            "movie_share": ratio(len(category_sets[name]), total_movies),
            "rating_rows": user_analysis["rating_counts"][name],
            "rating_share": ratio(user_analysis["rating_counts"][name], total_ratings),
        }
        for name in CATEGORIES
    }
    kobis_1m = kobis["proxy_validation"]["COMPARABLE_2004_PLUS_AUDIENCE_1M_PLUS"]
    result = {
        "schema_version": 1,
        "audit_id": "MOVIELENS_KOREAN_MARKET_THREE_WAY_PARTITION_V1",
        "generated_at_utc": datetime.now(tz=timezone.utc).isoformat(),
        "claim_boundary": "DESCRIPTIVE_PROXY_NOT_KOREAN_USER_PERFORMANCE",
        "dataset": {
            "movies": total_movies,
            "users": user_analysis["users"],
            "rating_rows": total_ratings,
        },
        "definition": {
            "priority": list(CATEGORIES),
            "korean_origin": "TMDB KR origin/production proxy",
            "famous_foreign_proxy": awareness["protocol"]["strict"],
            "remainder": "all MovieLens items outside the two prior disjoint sets",
        },
        "partition": partition,
        "validation": {
            "kobis_cohort": "COMPARABLE_2004_PLUS_AUDIENCE_1M_PLUS",
            "kobis_1m_movies": kobis_1m["movies"],
            "kobis_1m_movie_recall": kobis_1m["foreign_strict"]["movie_recall"],
            "kobis_1m_audience_weighted_recall": kobis_1m["foreign_strict"][
                "audience_weighted_recall"
            ],
        },
        "sensitivity": {
            "foreign_strict_rating_share": awareness["coverage"]["FOREIGN_STRICT"][
                "rating_share"
            ],
            "foreign_moderate_rating_share": awareness["coverage"]["FOREIGN_MODERATE"][
                "rating_share"
            ],
        },
        "user_analysis": user_analysis,
        "decision": {
            "als_training": "KEEP_FULL_MOVIELENS",
            "als_base_strength": "FAMOUS_FOREIGN_PROXY",
            "korean_origin": "CONTENT_AND_EXPLICIT_PREFERENCE_AUGMENTATION_REQUIRED",
            "remainder": "KEEP_FOR_CONTENT_COLD_ITEM_AND_DISCOVERY",
            "fixed_product_quota": "NOT_APPROVED",
            "evaluation": "REPORT_ALL_THREE_SLICES_SEPARATELY",
        },
        "runtime_seconds": round(time.perf_counter() - started, 3),
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    args.output_markdown.write_text(build_markdown(result), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "PASS",
                "partition": partition,
                "output_json": str(args.output_json),
                "output_markdown": str(args.output_markdown),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
