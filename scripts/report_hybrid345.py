"""Render the sealed hybrid345 development comparison without retuning it."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from hybrid345_evaluate import (
    DEFAULT_ARTIFACT,
    DEFAULT_CONFIG,
    EVALUATION_REVIEW,
    ROOT,
    _assert_pin,
    pin,
    read_json,
    require,
    require_evaluation_review,
    write_json,
)


DEFAULT_DOCUMENT_DIR = ROOT / "docs/recommendation/experiments/hybrid345"


def verify_evaluation(output_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    seal_path = output_dir / "evaluation-seal.json"
    require(seal_path.is_file(), "missing evaluation-seal.json")
    seal = read_json(seal_path)
    require(seal.get("scope") == "DEVELOPMENT_ONLY" and isinstance(seal.get("files"), dict), "valid development evaluation seal")
    for name, expected in seal["files"].items():
        path = output_dir / name
        require(path.is_file(), f"sealed evaluation file missing: {name}")
        _assert_pin(path, expected, name)
    manifest_path = output_dir / "evaluation-manifest.json"
    _assert_pin(manifest_path, seal["manifest"], "evaluation-manifest.json")
    return seal, read_json(manifest_path)


def verify_catalog_diagnostic(output_dir: Path, evaluation_seal_pin: dict[str, Any]) -> dict[str, Any]:
    from hybrid345_catalog import CATALOG_REVIEW, require_catalog_review

    review = require_catalog_review(DEFAULT_CONFIG)
    seal_path = output_dir / "catalog-diagnostic-seal.json"
    require(seal_path.is_file(), "missing catalog-diagnostic-seal.json")
    seal = read_json(seal_path)
    require(seal.get("scope") == "DEVELOPMENT_ONLY" and seal.get("diagnostic_only") is True,
            "valid development catalog diagnostic seal")
    require(seal.get("evaluation_seal") == evaluation_seal_pin,
            "catalog/evaluation parent drift")
    require(seal.get("catalog_review") == pin(CATALOG_REVIEW),
            "catalog review parent drift")
    require(seal.get("catalog_fingerprint") == review["fingerprint"],
            "catalog reviewed-code fingerprint drift")
    required = {"catalog-top6.parquet", "catalog-summary.csv", "catalog-timing.csv",
                "catalog-supply.csv"}
    require(set(seal.get("files", {})) == required,
            "catalog diagnostic seal must pin the exact four result files")
    for name, expected in seal["files"].items():
        _assert_pin(output_dir / name, expected, "catalog:" + name)
    return seal


def number(value: Any, digits: int = 4) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return f"{float(value):.{digits}f}"


def table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    return "\n".join(
        ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
        + ["| " + " | ".join(map(str, row)) + " |" for row in rows]
    )


def metric(summary: pd.DataFrame, model: str, group: str, name: str) -> tuple[float, int]:
    selected = summary[
        summary.model.eq(model)
        & summary.group.eq(group)
        & summary.cap.eq(10)
        & summary.h_group.eq("H_POSITIVE")
        & summary.metric.eq(name)
    ]
    require(len(selected) == 1, f"one summary metric: {model}/{group}/{name}")
    row = selected.iloc[0]
    return float(row["mean"]), int(row.valid_users)


def metric_rows(summary: pd.DataFrame, models: Sequence[str], group: str) -> list[list[Any]]:
    rows = []
    for model in models:
        values = {name: metric(summary, model, group, name) for name in
                  ("mse", "mae", "bias", "pa", "ndcg1", "ndcg2", "ndcg4", "ndcg6",
                   "stars1", "stars2", "stars4", "stars6",
                   "good1", "good2", "good4", "good6",
                   "low1", "low2", "low4", "low6", "any_low2", "both_low2")}
        rows.append([
            model,
            number(values["mse"][0]),
            number(values["mae"][0]),
            number(values["bias"][0]),
            number(values["pa"][0]),
            "/".join(number(values[f"ndcg{n}"][0]) for n in (1, 2, 4, 6)),
            "/".join(number(values[f"stars{n}"][0]) for n in (1, 2, 4, 6)),
            "/".join(number(values[f"good{n}"][0]) for n in (1, 2, 4, 6)),
            "/".join(number(values[f"low{n}"][0]) for n in (1, 2, 4, 6)),
            number(values["any_low2"][0]),
            number(values["both_low2"][0]),
            f"{values['mse'][1]} / " + "/".join(str(values[f"ndcg{n}"][1]) for n in (1, 2, 4, 6)),
        ])
    return rows


def render_plot(summary: pd.DataFrame, destination: Path) -> None:
    panels = [
        ("W_DIRECT", ["ALS", "QWEN_DIRECT", "ALS_QWEN"], "Warm observed items"),
        ("C", ["STRUCTURED_DIRECT", "E5_DIRECT", "QWEN_DIRECT", "FM150_SEED_MEAN", "GBT120_SEED_MEAN", "ALS_C2F"], "Held-out cold items"),
        ("ALL", ["FM150_SEED_MEAN", "GBT120_SEED_MEAN", "ROUTER_s339"], "All observed items"),
    ]
    fig, axes = plt.subplots(2, 3, figsize=(17, 8.5))
    colors = ["#386cb0", "#fdb462", "#7fc97f", "#beaed4", "#fdc086", "#ef3b2c"]
    for column, (group, models, title) in enumerate(panels):
        for row, (name, label) in enumerate((("mse", "Calibrated MSE (lower)"), ("ndcg2", "NDCG@2 (higher)"))):
            ax = axes[row, column]
            values = [metric(summary, model, group, name)[0] for model in models]
            x = np.arange(len(models))
            ax.bar(x, values, color=colors[: len(models)])
            ax.set_xticks(x, [model.replace("_DIRECT", "").replace("_SEED_MEAN", " 3-seed") for model in models], rotation=25, ha="right")
            ax.set_title(f"{title}\n{label}")
            finite = [value for value in values if np.isfinite(value)]
            if finite:
                low, high = min(finite), max(finite)
                margin = max((high - low) * 0.25, abs(high) * 0.03, 0.01)
                ax.set_ylim(min(0, low - margin), high + margin)
            for index, value in enumerate(values):
                if np.isfinite(value):
                    ax.text(index, value, f"{value:.4f}", ha="center", va="bottom", fontsize=8)
            ax.spines[["top", "right"]].set_visible(False)
    fig.suptitle("hybrid345 fixed development comparison · cap10 · users with input", fontsize=14)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(destination, dpi=180, bbox_inches="tight")
    plt.close(fig)


def report(output_dir: Path, document_dir: Path, *, overwrite: bool = False) -> dict[str, Any]:
    output_dir, document_dir = Path(output_dir), Path(document_dir)
    evaluation_review = require_evaluation_review(DEFAULT_CONFIG)
    seal, manifest = verify_evaluation(output_dir)
    require(manifest.get("evaluation_review") == pin(EVALUATION_REVIEW),
            "report evaluation-review parent drift")
    require(manifest.get("evaluation_fingerprint") == evaluation_review["fingerprint"],
            "report reviewed-code fingerprint drift")
    seal_pin = pin(output_dir / "evaluation-seal.json")
    catalog_seal = verify_catalog_diagnostic(output_dir, seal_pin)
    catalog_seal_pin = pin(output_dir / "catalog-diagnostic-seal.json")
    summary = pd.read_csv(output_dir / "summary.csv")
    pages = pd.read_csv(output_dir / "page-summary.csv")
    denominators = pd.read_csv(output_dir / "denominators.csv")
    diagnostics = pd.read_csv(output_dir / "diagnostic-summary.csv")
    contrasts = pd.read_csv(output_dir / "primary-contrasts.csv")
    selection = read_json(output_dir / "selection.json")
    decisions = read_json(output_dir / "decisions.json")
    reproduction = read_json(output_dir / "calibration-reproduction.json")
    catalog_summary = pd.read_csv(output_dir / "catalog-summary.csv")
    require(reproduction["status"] == "PASS", "final344 FM/GBT calibration reproduction PASS")

    result_path = document_dir / "RESULT.md"
    plot_path = document_dir / "comparison.png"
    report_manifest_path = output_dir / "report-manifest.json"
    existing = [path for path in (result_path, plot_path, report_manifest_path) if path.exists()]
    require(overwrite or not existing, "preserve existing report outputs: " + ", ".join(map(str, existing)))
    if overwrite:
        for path in existing:
            path.unlink()
    document_dir.mkdir(parents=True, exist_ok=True)

    headers = ["모델", "MSE", "MAE", "bias", "PA", "NDCG@1/2/4/6",
               "별점@1/2/4/6", "good@1/2/4/6", "low@1/2/4/6",
               "any low@2", "both low@2", "유효 MSE / @1/2/4/6"]
    warm_models = ["ALS", "QWEN_DIRECT", "ALS_QWEN"]
    cold_models = ["STRUCTURED_DIRECT", "E5_DIRECT", "QWEN_DIRECT", "FM150_SEED_MEAN", "GBT120_SEED_MEAN", "ALS_C2F"]
    router_models = ["FM150_SEED_MEAN", "GBT120_SEED_MEAN", "ROUTER_s339"]

    contrast_rows = []
    decision_map = {(row["family"], row["before"], row["after"], row["group"]): row for row in decisions["pair_decisions"]}
    for row in contrasts.itertuples():
        decision = decision_map[(row.family, row.before, row.after, row.group)]
        interval = "N/A" if pd.isna(row.ci_low) else f"[{row.ci_low:.6g}, {row.ci_high:.6g}]"
        contrast_rows.append([row.family, f"{row.after} − {row.before}", row.group, row.metric, int(row.users),
                              number(row.delta, 6), interval, f"{100 * row.confidence:.3f}%", decision["verdict"]])

    page_rows = []
    for model in router_models:
        for end in (2, 4, 6):
            current = pages[
                pages.model.eq(model) & pages.cap.eq(10) & pages.group.eq("ALL")
                & pages.h_group.eq("H_POSITIVE")
                & pages.start.eq(end - 2) & pages.end.eq(end)
            ]
            values = {}
            for name in ("stars", "good", "low", "any_low", "both_low"):
                selected_metric = current[current.metric.eq(name)]
                require(len(selected_metric) == 1, f"one page summary: {model}/{end}/{name}")
                values[name] = selected_metric.iloc[0]
            page_rows.append([model, f"{end-1}–{end}", int(values["stars"].valid_users),
                              number(values["stars"]["mean"]), number(values["good"]["mean"]),
                              number(values["low"]["mean"]), number(values["any_low"]["mean"]),
                              number(values["both_low"]["mean"])])

    primary_denoms = denominators[
        denominators.cap.eq(10) & denominators.h_group.eq("H_POSITIVE")
        & denominators.model.isin(set(warm_models + cold_models + router_models))
        & denominators.group.isin(["W_DIRECT", "C", "ALL"])
    ]
    denom_rows = [[r.model, r.group, int(r.user_contexts), int(r.complete_user_contexts), int(r.target_rows), int(r.scored_rows)]
                  for r in primary_denoms.itertuples()]
    diagnostic_rows = []
    for model in ("QWEN_DIRECT", "ALS_C2F", "FM150_s339", "GBT120_s339"):
        for level in ("V", "E"):
            selected_diagnostic = diagnostics[
                diagnostics.model.eq(model) & diagnostics.dimension.eq("cold_partition") & diagnostics.level.eq(level)
            ]
            require(len(selected_diagnostic) == 1, f"one V/E diagnostic: {model}/{level}")
            row = selected_diagnostic.iloc[0]
            diagnostic_rows.append([model, level, int(row.users), int(row["rows"]), int(row.movies), number(row.user_macro_mse)])

    catalog_rows = []
    for model in ("QWEN_DIRECT", "ALS_C2F", "ALS_QWEN", "SELECTED_COLD_HEAD", "ROUTER_s339"):
        for n in (2, 4, 6):
            selected_catalog = catalog_summary[
                catalog_summary.model.eq(model) & catalog_summary.top_n.eq(n)
            ]
            require(len(selected_catalog) == 1, f"one catalog summary: {model}/top{n}")
            row = selected_catalog.iloc[0]
            catalog_rows.append([
                model, n, int(row.users), f"{int(row.returned)}/{int(row.slots)}",
                int(row.unique_movies), number(row.catalog_coverage, 6),
                f"{int(row.unknown)} ({100 * float(row.unknown_rate):.2f}%)",
                number(row.hhi, 6), number(row.max_share, 6), int(row.support0),
            ])

    warm_choice = selection["warm"]
    cold_choice = selection["cold"]
    verdict_counts = pd.Series([row["verdict"] for row in decisions["pair_decisions"]]).value_counts().to_dict()
    body = f"""# Qwen 콘텐츠·ALS 결합·cold-item 전이 최종 비교

상태: **DEVELOPMENT_ONLY**  
이 결과는 재사용한 MovieLens 개발 사용자 180명과 고정 영화 집합에서의 비교다. 2026년 한국 서비스 만족도나 배포 정책을 직접 입증하지 않는다.

![hybrid345 비교](comparison.png)

## calibration에서 고정한 정책

- warm 결합 가중치: **{warm_choice['content_weight']}** (`{warm_choice['reason']}`)
- cold head: **{cold_choice['head']}** (`{cold_choice['reason']}`)
- router: warm에서는 위 결합, ALS 직접 점수가 없는 행에서는 cold head, 필수 점수가 없을 때만 GBT120_s339 fallback을 사용했다.
- 설정 선택과 affine 보정은 calibration 90명만 사용했다. comparison 180명의 결과를 본 뒤 가중치나 head를 바꾸지 않았다.

## ALS가 직접 계산되는 W_DIRECT

{table(headers, metric_rows(summary, warm_models, 'W_DIRECT'))}

ALS_QWEN은 ALS와 Qwen을 각 cap의 사용자 균등 affine으로 같은 별점 축에 옮긴 뒤 섞었다. 결합 점수에는 affine을 다시 맞추지 않았다. 순위에는 clip 전 점수, 오차에는 [0.5, 5] clip을 적용했다.

## ALS가 직접 계산되지 않는 C

{table(headers, metric_rows(summary, cold_models, 'C'))}

FM150/GBT120의 `SEED_MEAN`은 seed339·344·345 checkpoint에서 먼저 사용자별 지표를 계산한 뒤 그 세 지표를 평균한 값이다. 예측 점수를 섞은 앙상블이 아니다. E5_DIRECT는 encoder 진단선이며 cold head 선택 후보에서 제외했다. ALS_C2F는 ALS 직접 예측이 아니라 Qwen 콘텐츠에서 ALS item factor 공간을 추정한 점수다.

## 전체 관측 후보와 support-aware router

{table(headers, metric_rows(summary, router_models, 'ALL'))}

ROUTER_s339은 운영 가능한 한 개 점수 경로를 보기 위한 seed339 정책이다. 이를 3-seed 지표 평균과 비교했으므로 학습 seed 불확실성까지 제거한 대조로 해석할 수 없다.

## 사전 고정 14개 대조

차이는 `after − before`다. MSE는 음수, NDCG@2는 양수가 after에 유리하다. warm 2개는 97.5%, cold 8개는 99.375%, router 4개는 98.75% 양측 사용자 paired bootstrap 구간이다. 각 대조·지표는 오름차순 공통 uid를 사용하고 이름에서 SHA-256으로 파생한 독립 PCG64 seed를 썼다.

{table(['family', '비교', '집합', '지표', '사용자', '차이', '구간', '신뢰수준', '판정'], contrast_rows)}

판정 집계: `{json.dumps(verdict_counts, ensure_ascii=False, sort_keys=True)}`. `ONE_METRIC_ADVANTAGE_NO_DETECTED_HARM`은 한 지표의 구간상 이점과 다른 지표에서 검출된 손실이 없다는 제한된 뜻이다. 동등성·비열등성이나 서비스 우위를 입증하지 않는다.

## 2편씩 이어지는 페이지

{table(['모델', '페이지', '유효 사용자', '평균 별점', 'good>=4', 'low<=2', '한 편 이상 low', '두 편 모두 low'], page_rows)}

## 전체 카탈로그 Top2/4/6 진단

{table(['모델', 'Top-N', '사용자', '반환/슬롯', '고유 영화', 'coverage', 'UNKNOWN', 'HHI', '최대 점유율', 'support 0'], catalog_rows)}

이 표는 comparison H10 중 실제 입력이 있는 {catalog_seal['users']}명에게, final344와 동일한 개봉·시청 제외 후보축을 적용한 결과다. UNKNOWN은 관측 별점에 없는 노출이며 오답으로 간주하지 않았다. coverage는 이 사용자들의 적격 후보 합집합 대비 고유 노출 영화 비율이다.
QWEN_DIRECT·ALS_C2F·선택 cold head는 해당 모델이 점수 가능한 전체 후보축, ALS_QWEN은 ALS가 지원되는 warm 후보축, ROUTER_s339은 cold head와 GBT fallback을 포함한 전체 후보축을 사용하므로 coverage의 후보 범위를 함께 봐야 한다.

## 분모

{table(['모델', '집합', '사용자 context', '완전 점수 사용자', '관측 target행', '점수화 행'], denom_rows)}

모델별 top-N은 그 사용자·집합의 모든 관측 target을 점수화한 경우에만 계산했다. 미평가는 UNKNOWN이며 낮은 별점으로 바꾸지 않았다. NATURAL_ZERO는 NDCG@2 유효 사용자가 30명 미만이면 기술통계만 유지한다.

## V/E 방향 진단

{table(['모델', 'cold partition', '사용자', '행', '영화', '사용자 평균 MSE'], diagnostic_rows)}

V와 E, 학습 support, 개봉연대, TMDB vote수별 결과는 `diagnostic-summary.csv`에 기술통계로 남겼다. 이 다중 하위집단은 추가 승자 검정에 사용하지 않았다.

## 지표 정의와 한계

- MSE·MAE·bias는 calibration affine 적용 후 [0.5, 5]로 자른 별점 오차의 사용자 평균이다.
- NDCG@1/2/4/6과 Top2/4/6은 clip 전 affine 순위(기울기가 양수면 raw 순위와 동일)와 movie_id 오름차순 tie-break를 사용한다. 기울기 0이면 모든 점수가 동점이다.
- PA는 실제 별점이 다른 모든 영화 쌍의 순서를 비교하며 예측 동점은 0.5로 계산한다.
- `low`는 노출 영화 중 2점 이하 비율, `any low`는 페이지에 한 편 이상 존재하는 비율, `both low`는 두 편 모두인 비율이다.
- bootstrap은 고정 모델·mapper·보정기에서 사용자 표본 변동만 반영한다. Qwen·ALS 학습 seed, 새 영화 모집단, 한국 사용자 변동은 포함하지 않는다.
- FM150·GBT120의 seed339·344·345 보정계수는 final344 산식을 허용오차 {reproduction['tolerance']} 안에서 재현했다.

## 재현 자료

- 입력·예측 봉인: `{output_dir / 'prediction-seal.json'}`
- 평가 봉인: `{output_dir / 'evaluation-seal.json'}`
- 보정·선택: `calibration.json`, `selection.json`, `selection-user-metrics.parquet`
- 사용자·페이지·행 결과: `user-metrics.parquet`, `page-metrics.parquet`, `row-errors.parquet`
- V/E·support·개봉연대·TMDB vote 진단: `diagnostic-summary.csv`
- 대조와 판정: `primary-contrasts.csv`, `primary-paired-users.parquet`, `decisions.json`
"""

    render_plot(summary, plot_path)
    result_path.write_text(body, encoding="utf-8")
    current_seal, current_manifest = verify_evaluation(output_dir)
    require(pin(output_dir / "evaluation-seal.json") == seal_pin and current_seal == seal and current_manifest == manifest,
            "evaluation source changed while rendering report")
    current_catalog_seal = verify_catalog_diagnostic(output_dir, seal_pin)
    require(pin(output_dir / "catalog-diagnostic-seal.json") == catalog_seal_pin and
            current_catalog_seal == catalog_seal,
            "catalog source changed while rendering report")
    report_manifest = {
        "scope": "DEVELOPMENT_ONLY",
        "evaluation_seal": seal_pin,
        "catalog_diagnostic_seal": pin(output_dir / "catalog-diagnostic-seal.json"),
        "script": pin(Path(__file__)),
        "evaluation_review": pin(EVALUATION_REVIEW),
        "evaluation_fingerprint": evaluation_review["fingerprint"],
        "result": pin(result_path),
        "plot": pin(plot_path),
        "selection": {"warm_content_weight": warm_choice["content_weight"], "cold_head": cold_choice["head"]},
    }
    write_json(report_manifest_path, report_manifest)
    return report_manifest


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluation-dir", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--document-dir", type=Path, default=DEFAULT_DOCUMENT_DIR)
    parser.add_argument("--overwrite", action="store_true", help="Replace only RESULT.md, comparison.png and report-manifest.json")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    result = report(args.evaluation_dir, args.document_dir, overwrite=args.overwrite)
    print(json.dumps({"status": "REPORT_WRITTEN", "result": result["result"], "plot": result["plot"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
