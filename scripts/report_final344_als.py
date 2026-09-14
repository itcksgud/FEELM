"""Render the final ALS/FM/GBT decision from already sealed final344 results."""

import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import final344_common as a


REPORT = a.DOC / "FINAL-DECISION.md"
FIGURE = a.DOC / "als-fm-gbt-comparison.png"
CSV = a.OUT / "als-final-comparison.csv"
MANIFEST = a.OUT / "als-final-report-manifest.json"
PRE_REVIEW = a.DOC / "als-final-report-pre-review.json"
MODELS = ("REFERENCE_ALS", "SEED_MEAN_FM", "SEED_MEAN_GBT")
LABELS = {"REFERENCE_ALS": "ALS", "SEED_MEAN_FM": "FM150", "SEED_MEAN_GBT": "GBT120"}


def pin(path):
    path = Path(path)
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return {"bytes": path.stat().st_size, "sha256": h.hexdigest()}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def pct(value):
    return f"{100 * value:.2f}%"


def table(headers, rows):
    return "\n".join(
        ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
        + ["| " + " | ".join(map(str, row)) + " |" for row in rows]
    )


def metric(frame, model, group, name):
    rows = frame[(frame.model == model) & (frame.group == group) & (frame.cap == 10) & (frame.h > 0)]
    values = rows[name].dropna()
    require(len(rows) == 124 and len(values) > 0, f"expected metric rows: {model}/{group}/{name}")
    return float(values.mean()), int(len(values))


def main():
    for path in (REPORT, FIGURE, CSV, MANIFEST):
        require(not path.exists(), "preserve existing ALS final report: " + str(path))

    a.lock()
    for name in ("selection-seal.json", "final-calibration-seal.json", "evaluation-seal.json", "catalog-seal.json"):
        a.verify(name)
    completion = a.BASE / "final344-availability-audit/final-audit-completion.json"
    presentation = a.BASE / "final344-report-audit/report-result-review.json"
    root_review = a.DOC / "result-review.json"
    require(read_json(completion)["status"] == "PASS", "independent final numerical and catalog audit")
    require(read_json(presentation)["status"] == "REPORT_PRESENTATION_PASS", "reviewed base report")
    require(read_json(root_review)["status"] == "PASS", "completed final344 review")
    pre_review = read_json(PRE_REVIEW)
    require(pre_review["status"] == "PASS" and pre_review["script"] == pin(Path(__file__)),
            "independent exact-code ALS report review")
    combo_fit_seal_path = a.COMBO / "fit-seal.json"
    combo_fit_seal = read_json(combo_fit_seal_path)
    require(combo_fit_seal["input_lock"] == pin(a.COMBO / "input-lock.json"),
            "combination340 fit seal preserves its input lock")
    als_model_seal_path = a.COMBO / "ALS/model-seal.json"
    require(pin(als_model_seal_path) == combo_fit_seal["files"]["ALS/model-seal.json"],
            "sealed combination340 parent pins the ALS model seal")
    als_model_seal = read_json(als_model_seal_path)
    for name, expected in als_model_seal["files"].items():
        require(pin(a.COMBO / name) == expected, "sealed ALS model file: " + name)
    decision = read_json(a.OUT / "decision.json")
    require(decision["decision"] == "NO_CLEAR_WINNER_OR_TRADEOFF" and not decision["service_adoption"],
            "preserve final statistical decision")

    users = pd.read_parquet(a.OUT / "user-metrics.parquet")
    seed_mean = pd.read_parquet(a.OUT / "seed-mean-user-metrics.parquet")
    combined = pd.concat([seed_mean, users[users.kind == "DESCRIPTIVE_REFERENCE"]], ignore_index=True)
    rows = []
    for model in MODELS:
        row = {"model": LABELS[model], "source_model": model}
        for name in ("calibrated_mse", "calibrated_mae", "ndcg1", "ndcg2", "ndcg4", "ndcg6",
                     "stars2", "good2", "low2", "both_low2"):
            row[name], row[name + "_users"] = metric(combined, model, "W_DIRECT", name)
        rows.append(row)
    comparison = pd.DataFrame(rows)
    expected_metric_users = {
        "calibrated_mse": 122, "calibrated_mae": 122, "ndcg1": 122, "ndcg2": 109,
        "ndcg4": 96, "ndcg6": 86, "stars2": 109, "good2": 109, "low2": 109,
        "both_low2": 109,
    }
    for name, expected in expected_metric_users.items():
        require(set(comparison[name + "_users"]) == {expected}, "same metric user count: " + name)
    als = comparison.iloc[0]
    for name in ("calibrated_mse", "calibrated_mae", "ndcg1", "ndcg2", "ndcg4", "ndcg6",
                 "stars2", "good2", "low2", "both_low2"):
        comparison[name + "_delta_vs_als"] = comparison[name] - als[name]
        comparison[name + "_relative_pct_vs_als"] = (comparison[name] / als[name] - 1) * 100
    catalog = pd.read_parquet(a.OLD / "catalog.parquet", columns=["movie_id", "blocked", "train_count"])
    factors = pd.read_parquet(a.COMBO / "ALS/item-factors", columns=["id"])
    w_ids = set(catalog.loc[catalog.train_count > 0, "movie_id"].astype(int))
    require(set(factors.id.astype(int)) == w_ids, "ALS factors exactly match W_DIRECT catalog movies")
    total_movies = len(catalog)
    w_movies = len(factors)
    c_movies = int(catalog.blocked.sum())
    natural_zero = int((~catalog.blocked & catalog.train_count.eq(0)).sum())
    require((total_movies, w_movies, c_movies, natural_zero) == (85517, 45074, 10952, 29491),
            "fixed catalog support counts")

    base = users[(users.model == "FM150_s339") & (users.cap == 10) & (users.h > 0)]
    observed = {group: int(base.loc[base.group == group, "targets"].sum())
                for group in ("ALL", "W_DIRECT", "C", "NATURAL_ZERO")}
    require(observed == {"ALL": 3763, "W_DIRECT": 2818, "C": 624, "NATURAL_ZERO": 321},
            "same cap10 observed target counts")

    # All three selected content-model seeds must show the same descriptive directions versus the single ALS reference.
    als_users = users[(users.model == "REFERENCE_ALS") & (users.group == "W_DIRECT") &
                      (users.cap == 10) & (users.h > 0)]
    als_mse = float(als_users.calibrated_mse.mean())
    als_ndcg2 = float(als_users.ndcg2.mean())
    for model in ("FM150_s339", "FM150_s344", "FM150_s345", "GBT120_s339", "GBT120_s344", "GBT120_s345"):
        current = users[(users.model == model) & (users.group == "W_DIRECT") & (users.cap == 10) & (users.h > 0)]
        require(float(current.calibrated_mse.mean()) > als_mse and float(current.ndcg2.mean()) > als_ndcg2,
                "three-seed descriptive W_DIRECT direction")

    all_metrics = {}
    for model in ("SEED_MEAN_FM", "SEED_MEAN_GBT"):
        all_metrics[model] = {}
        for name in ("calibrated_mse", "calibrated_mae", "ndcg2", "stars2", "low2"):
            value, user_count = metric(combined, model, "ALL", name)
            all_metrics[model][name] = value
            all_metrics[model][name + "_users"] = user_count
    for name, expected in (("calibrated_mse", 124), ("calibrated_mae", 124),
                           ("ndcg2", 117), ("stars2", 117), ("low2", 117)):
        require({all_metrics[model][name + "_users"] for model in all_metrics} == {expected},
                "same ALL metric user count: " + name)

    supply = pd.read_csv(a.OUT / "catalog-supply.csv")
    expected_catalog_models = {
        "FM150_s339", "FM150_s344", "FM150_s345",
        "GBT120_s339", "GBT120_s344", "GBT120_s345",
    }
    require(set(supply.model) == expected_catalog_models and len(supply) == 180 * 6,
            "complete six-model catalog supply")
    require(bool((supply.finite_scores == supply.candidates).all()),
            "FM and GBT produce finite scores for every eligible catalog candidate")

    exposure = pd.read_csv(a.OUT / "catalog-summary.csv")
    exposure = exposure[(exposure.h_group == "H_POSITIVE") & (exposure.view == "cumulative") & (exposure.end == 2)]
    fm_exp = exposure[exposure.family == "FM"]
    gbt_exp = exposure[exposure.family == "GBT"]
    require(len(fm_exp) == len(gbt_exp) == 3 and set(exposure.users) == {124} and set(exposure.returned) == {248},
            "three-seed full-catalog top2 exposure")

    timing = pd.read_csv(a.OUT / "catalog-timing-summary.csv")
    timing = timing[timing.metric.isin(["prediction_seconds", "component_total_seconds"])]
    timing_ranges = {}
    for family in ("FM", "GBT"):
        timing_ranges[family] = {}
        for name in ("prediction_seconds", "component_total_seconds"):
            values = timing[(timing.family == family) & (timing.metric == name)].p50
            require(len(values) == 3, "three timing seeds")
            timing_ranges[family][name] = (float(values.min()), float(values.max()))

    w_rows = []
    for row in comparison.itertuples():
        if row.model == "ALS":
            mse_delta = "기준"
            ndcg_delta = "기준"
            star_delta = "기준"
            low_delta = "기준"
        else:
            mse_delta = f"{row.calibrated_mse_delta_vs_als:+.4f} ({row.calibrated_mse_relative_pct_vs_als:+.2f}%)"
            ndcg_delta = f"{row.ndcg2_delta_vs_als:+.4f} ({row.ndcg2_relative_pct_vs_als:+.2f}%)"
            star_delta = f"{row.stars2_delta_vs_als:+.3f}점"
            low_delta = f"{100 * row.low2_delta_vs_als:+.2f}%p"
        w_rows.append([row.model, f"{row.calibrated_mse:.4f}", mse_delta,
                       f"{row.calibrated_mae:.4f}", f"{row.ndcg2:.4f}", ndcg_delta,
                       f"{row.stars2:.3f}", star_delta, pct(row.low2), low_delta])

    n_rows = []
    for name in ("ndcg1", "ndcg2", "ndcg4", "ndcg6"):
        count = expected_metric_users[name]
        values = {row.model: getattr(row, name) for row in comparison.itertuples()}
        n_rows.append(["NDCG@" + name[-1], count, f"{values['ALS']:.4f}",
                       f"{values['FM150']:.4f} ({values['FM150'] - values['ALS']:+.4f})",
                       f"{values['GBT120']:.4f} ({values['GBT120'] - values['ALS']:+.4f})"])

    coverage_rows = [
        ["W_DIRECT", f"{w_movies:,}", pct(w_movies / total_movies), "가능", "가능", "가능"],
        ["C · 학습에서 의도적으로 제외", f"{c_movies:,}", pct(c_movies / total_movies), "N/A", "가능", "가능"],
        ["NATURAL_ZERO · 원래 학습평점 0", f"{natural_zero:,}", pct(natural_zero / total_movies), "N/A", "가능", "가능"],
        ["전체", f"{total_movies:,}", "100.00%", f"{pct(w_movies / total_movies)} 상한", "가능", "가능"],
    ]

    overall_rows = []
    for model in ("SEED_MEAN_FM", "SEED_MEAN_GBT"):
        x = all_metrics[model]
        overall_rows.append([LABELS[model], f"{x['calibrated_mse']:.4f}", f"{x['calibrated_mae']:.4f}",
                             f"{x['ndcg2']:.4f}", f"{x['stars2']:.3f}", pct(x['low2'])])

    exposure_rows = []
    for family, frame in (("FM150", fm_exp), ("GBT120", gbt_exp)):
        exposure_rows.append([family, "248", f"{int(frame.unknown.min())}–{int(frame.unknown.max())}",
                              f"{int(frame.unique_movies.min())}–{int(frame.unique_movies.max())}",
                              f"{frame.hhi.min():.4f}–{frame.hhi.max():.4f}", "0"])

    report = f"""# ALS 포함 최종 모델 판정

상태: **로컬 연구 최종 판정 · DEVELOPMENT_ONLY**

## 최종 판정

**통계 판정: 단일 승자 없음. 서비스 채택: 보류.**

전체 카탈로그 적용, 첫 2편의 순위, 낮은 별점 회피를 제품 우선순위로 적용하면 **GBT120을 조건부 구현 우선 후보로 둔다.**
이는 서비스 채택 판정이 아니다.

이 말은 GBT가 ALS를 모든 지표에서 이겼다는 뜻이 아니다. 같은 MovieLens 학습지원 영화에서는
ALS가 예상별점의 제곱오차(MSE)가 가장 작았다. GBT120은 ALS보다 MSE가 **0.0205(2.95%) 높았지만**,
NDCG@2는 **0.0095(1.14%) 높고**, 관측 Top2 평균별점은 **0.057점 높았으며**, 2점 이하 노출은
**0.92%p 낮았다**. ALS는 전체 85,517편 중 45,074편(52.71%)에만 영화 벡터가 있어 단독 전체 후보 모델로 쓸 수 없다.

따라서 역할을 다음과 같이 판정한다.

| 모델 | 판정 | 역할 |
| --- | --- | --- |
| **GBT120** | **조건부 구현 우선 후보** | 전체 카탈로그 기본 배치 점수 모델 후보; 서비스 채택 아님 |
| **ALS** | **유지** | 학습지원 영화의 예상별점 기준선; 전체 카탈로그 단독 사용은 기각 |
| **FM150** | **보류** | 계산비용이 더 중요한 경우의 대체 후보 |
| **ALS+GBT 혼합** | **보류** | 이번 최종 실험에서 검증하지 않았으므로 채택하지 않음 |

`조건부`인 이유는 재사용한 MovieLens 개발 사용자에서 얻은 결과이며, ALS 대비 신뢰구간을 사전에 검정하지 않았기 때문이다.
서비스 설정이나 운영 모델을 이 문서만으로 변경하지 않는다.

## ALS가 점수를 낼 수 있는 같은 영화에서 비교

조건은 cap10, 실제 입력이 있는 사용자, `W_DIRECT`이다. MSE·MAE는 122명, Top2 지표는 109명이다.
FM·GBT는 seed339/344/345의 **사용자별 지표 평균**이며 예측 앙상블이 아니다. ALS는 기존 단일 학습과
기존 보정기를 재사용한 기술적 참고값이다. 평가 영화 축은 같지만 학습 반복 조건은 대칭이 아니다.

{table(['모델', '보정 MSE↓', 'ALS 대비', '보정 MAE↓', 'NDCG@2↑', 'ALS 대비', 'Top2 별점↑', 'ALS 대비', '≤2 비율↓', 'ALS 대비'], w_rows)}

질문별로 읽으면 다음과 같다.

- **별점을 얼마나 정확히 맞추나:** MSE는 ALS가 가장 좋다. FM은 ALS보다 3.74%, GBT는 2.95% 높다.
- **앞의 2편을 얼마나 잘 고르나:** 기술통계상 GBT가 가장 좋고, ALS보다 NDCG@2가 1.14% 높다.
- **낮은 별점을 피하나:** GBT는 ALS보다 2점 이하 비율이 0.92%p 낮고, FM은 1.83%p 높다.
- **모델이 안정적으로 같은 방향을 보였나:** FM·GBT의 세 seed 모두 ALS보다 MSE는 높고 NDCG@2는 높았다.
  하지만 ALS 대비 사전 신뢰구간 검정은 없으므로 통계적 우월성으로 쓰지 않는다.

### Top 1·2·4·6 순위

{table(['지표', '사용자', 'ALS', 'FM150 (ALS 대비)', 'GBT120 (ALS 대비)'], n_rows)}

N이 커질수록 충분한 평가 후보를 가진 사용자만 남아 분모가 달라진다. N 사이의 상승 폭을 같은 사용자 추세로 해석하지 않는다.

## ALS가 빠지는 범위

{table(['영화 집합', '편수', '전체 비율', 'ALS', 'FM150', 'GBT120'], coverage_rows)}

이번 주 비교 조건의 관측 정답 3,763행 중 ALS가 직접 점수를 낸 W_DIRECT는 2,818행(74.89%)이다.
나머지 945행(25.11%)은 C 624행과 NATURAL_ZERO 321행으로 ALS 점수가 없다. 전체 고정 카탈로그에서는
미지원 범위가 40,443편(47.29%)으로 더 크다. FM·GBT의 `가능`은 고정 230개 특징으로 유한한 점수를
계산했다는 뜻이며, 그 영화의 추천 만족도가 검증됐다는 뜻은 아니다.

## 전체 관측 영화에서 FM과 GBT

ALS가 없는 영화까지 포함하므로 이 표에는 ALS를 넣지 않는다. MSE·MAE는 124명, Top2는 117명이다.

{table(['모델', '보정 MSE↓', '보정 MAE↓', 'NDCG@2↑', 'Top2 별점↑', '≤2 비율↓'], overall_rows)}

GBT는 FM보다 MSE가 0.0097(1.41%) 낮고 Top2의 2점 이하 비율이 2.42%p 낮다. FM은 MAE가
0.0065 낮고 NDCG@2가 0.0014(0.17%) 높다. 사전에 정한 ALL/C의 네 98.75% 신뢰구간은
모두 0을 포함하므로 **FM과 GBT 중 통계적으로 확실한 단일 승자는 확인되지 않았다.** 같다고 입증한 것도 아니다.

## 전체 후보 노출과 계산 비용

124명에게 첫 2편씩 보여주는 248개 슬롯의 seed별 범위다.

{table(['모델', '슬롯', 'UNKNOWN', '고유 영화', 'HHI↓', 'TMDB 투표1개·10점'], exposure_rows)}

GBT가 FM보다 여러 영화에 노출을 분산했다. 이는 쏠림 진단이며 만족도 개선 증거가 아니다. FM 추천은
248개 모두, GBT 추천은 239–245개가 MovieLens 평가 기록이 없는 UNKNOWN이라 실제 전체 후보 Top2의
정답률이나 만족도는 계산할 수 없다.

같은 로컬 runner의 후보 전체 계산에서 FM의 예측 p50은 {timing_ranges['FM']['prediction_seconds'][0]:.3f}–{timing_ranges['FM']['prediction_seconds'][1]:.3f}초,
GBT는 {timing_ranges['GBT']['prediction_seconds'][0]:.3f}–{timing_ranges['GBT']['prediction_seconds'][1]:.3f}초였다. 공유 특징 생성과 정렬을 포함한 p50은
FM {timing_ranges['FM']['component_total_seconds'][0]:.3f}–{timing_ranges['FM']['component_total_seconds'][1]:.3f}초, GBT {timing_ranges['GBT']['component_total_seconds'][0]:.3f}–{timing_ranges['GBT']['component_total_seconds'][1]:.3f}초다.
이는 API 지연시간이나 다중 노드 성능이 아니다. 계산비용만 최우선이면 FM150이 합리적인 대체안이다.

## 이 판정으로 확정하는 것과 남기는 것

- 확정: ALS는 동일 학습지원 영화의 MSE 기준선으로 유지한다.
- 조건부 구현 우선 후보: 전체 카탈로그와 첫 2편 노출을 함께 고려하면 GBT120이다. 서비스 채택은 보류한다.
- 보류: ALS+GBT 혼합, 온라인 재순위, 인기 fallback과 개인화 전환 K는 이 실험 결과로 확정하지 않는다.
- 한계: 평가자는 이미 연구에 사용한 MovieLens 개발 사용자다. 2026년 한국 사용자 만족도나 최신 영화 품질을 증명하지 않는다.
- 자원: 8회 학습은 모두 수치 검산을 통과했지만 12GiB 엄격 기준은 2회 PASS, 6회 EXCEPTION이었다.

## 근거와 재현

- [FM·GBT 전체 결과](RESULT.md)
- [실행 명세](IMPLEMENTATION.md)
- 수치 CSV: `outputs/recommendation-evidence/final344/als-final-comparison.csv`
- 모델·평가·전체 후보·보고서 독립 감사: `outputs/recommendation-evidence/final344-availability-audit/`,
  `outputs/recommendation-evidence/final344-model-audit/`, `outputs/recommendation-evidence/final344-report-audit/`
"""

    plt.rcParams["font.family"] = ["Malgun Gothic", "DejaVu Sans"]
    colors = ["#8c8c8c", "#377eb8", "#e68632"]
    labels = comparison.model.tolist()
    fig, axes = plt.subplots(1, 3, figsize=(15, 5.2))
    panels = [
        (comparison.calibrated_mse.to_numpy(), "W_DIRECT 보정 MSE · 낮을수록 좋음", "{:.4f}",
         ["기준", "+3.74%", "+2.95%"]),
        (comparison.ndcg2.to_numpy(), "W_DIRECT NDCG@2 · 높을수록 좋음", "{:.4f}",
         ["기준", "+0.36%", "+1.14%"]),
        (np.array([w_movies / total_movies, 1.0, 1.0]), "영화 점수 계산 범위 · ALS는 h>0 상한", "{:.1%}",
         ["영화벡터 상한", "전 후보 유한값", "전 후보 유한값"]),
    ]
    for axis, (values, title, formatter, deltas) in zip(axes, panels):
        bars = axis.bar(labels, values, color=colors, width=.64)
        axis.set_ylim(0, max(values) * 1.16)
        axis.set_title(title, fontsize=12)
        axis.spines[["top", "right"]].set_visible(False)
        for bar, value, delta in zip(bars, values, deltas):
            axis.text(bar.get_x() + bar.get_width() / 2, value, formatter.format(value) + "\n" + delta,
                      ha="center", va="bottom", fontsize=11)
    fig.suptitle("ALS·FM150·GBT120 최종 비교\n같은 W_DIRECT 품질과 전체 카탈로그 지원 범위를 분리", fontsize=15)
    fig.text(.5, .01, "FM·GBT: 3seed 사용자 지표 평균 · ALS: 기존 단일 학습 참고값 · DEVELOPMENT_ONLY",
             ha="center", fontsize=10)
    fig.tight_layout(rect=(0, .06, 1, .9))
    comparison.to_csv(CSV, index=False)
    fig.savefig(FIGURE, dpi=180)
    plt.close(fig)
    REPORT.write_text(report, encoding="utf-8")

    sources = [a.OUT / "user-metrics.parquet", a.OUT / "seed-mean-user-metrics.parquet",
               a.OUT / "catalog-summary.csv", a.OUT / "catalog-supply.csv", a.OUT / "catalog-timing-summary.csv",
               a.OUT / "decision.json", a.OUT / "evaluation-seal.json", a.OUT / "catalog-seal.json",
               a.OLD / "catalog.parquet", combo_fit_seal_path, als_model_seal_path, completion,
               presentation, root_review, PRE_REVIEW]
    payload = {
        "status": "GENERATED_PENDING_INDEPENDENT_REVIEW",
        "scope": "DEVELOPMENT_ONLY",
        "statistical_decision": decision["decision"],
        "product_decision": "DEFERRED",
        "implementation_candidate": "GBT120_CONDITIONAL_ON_FULL_CATALOG_AND_TOP2_RISK_PRIORITIES",
        "als_role": "W_DIRECT_RATING_ERROR_REFERENCE",
        "service_adoption_changed": False,
        "script": pin(Path(__file__)),
        "sources": {path.relative_to(a.ROOT).as_posix(): pin(path) for path in sources},
        "outputs": {path.relative_to(a.ROOT).as_posix(): pin(path) for path in (REPORT, FIGURE, CSV)},
    }
    MANIFEST.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ALS_FINAL_REPORT_WRITTEN", "report": pin(REPORT), "figure": pin(FIGURE),
                      "csv": pin(CSV), "manifest": pin(MANIFEST)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
