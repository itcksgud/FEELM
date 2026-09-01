from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib import font_manager
from matplotlib.patches import FancyBboxPatch
from matplotlib.ticker import PercentFormatter


REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = REPO_ROOT / "docs/recommendation/figures"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def configure() -> None:
    preferred = ["Malgun Gothic", "AppleGothic", "NanumGothic", "DejaVu Sans"]
    available = {font.name for font in font_manager.fontManager.ttflist}
    plt.rcParams["font.family"] = next(name for name in preferred if name in available)
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.facecolor"] = "white"
    plt.rcParams["axes.facecolor"] = "white"
    plt.rcParams["axes.edgecolor"] = "#B8C0CC"
    plt.rcParams["axes.titleweight"] = "bold"
    plt.rcParams["axes.titlesize"] = 15
    plt.rcParams["axes.labelsize"] = 11
    plt.rcParams["xtick.labelsize"] = 10
    plt.rcParams["ytick.labelsize"] = 10


def save(fig: plt.Figure, name: str) -> None:
    path = OUTPUT / name
    fig.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def top2_eligibility() -> None:
    summary = load_json(REPO_ROOT / "outputs/recommendation-evidence/rec-ev-020p/validation-cohort-summary.json")
    ks = [0, 1, 3, 5, 10, 20, 30, 50]
    users = [summary["primary_slate_20_by_k"][str(k)]["structural_users"] for k in ks]
    rates = [summary["primary_slate_20_by_k"][str(k)]["miss_nonnull_rate"] for k in ks]
    fig, left = plt.subplots(figsize=(10, 5.3))
    bars = left.bar([str(k) for k in ks], users, color="#5B7FFF", width=0.68)
    left.set_title("입력 영화가 늘수록 평가 가능한 사용자는 줄지만, 채점 가능 비율은 높다")
    left.set_xlabel("추천 전에 제공한 평점 수 K")
    left.set_ylabel("20편 평가판을 만들 수 있는 사용자 수")
    left.grid(axis="y", alpha=0.18)
    left.set_axisbelow(True)
    for bar, value in zip(bars, users):
        left.text(bar.get_x() + bar.get_width() / 2, value + 280, f"{value:,}", ha="center", va="bottom", fontsize=9)
    right = left.twinx()
    right.plot([str(k) for k in ks], rates, color="#E05263", marker="o", linewidth=2.4)
    right.set_ylabel("좋은 영화 놓침을 채점할 수 있는 사용자 비율")
    right.yaxis.set_major_formatter(PercentFormatter(1.0))
    right.set_ylim(0.96, 1.0)
    right.spines["top"].set_visible(False)
    left.spines["top"].set_visible(False)
    fig.text(0.5, -0.01, "Validation 사용자만 사용 · 20개 고정 seed 평균 · Locked Test 성능 미사용", ha="center", fontsize=9, color="#5E6673")
    fig.tight_layout()
    save(fig, "top2-k-eligibility.png")


def prior_star_curve() -> None:
    evidence = load_json(REPO_ROOT / "docs/recommendation/evidence/manifests/rec-ev-003b.json")
    ks = [1, 3, 5, 10, 20]
    curve = evidence["metrics"]["selected_curve"]
    mae = [curve[str(k)]["star_macro_mae"] for k in ks]
    relative = [curve[str(k)]["star_relative_improvement"] for k in ks]
    fig, left = plt.subplots(figsize=(9.5, 5.1))
    left.plot(ks, mae, marker="o", color="#5B7FFF", linewidth=2.5)
    left.set_title("기존 실험: 평점을 더 받으면 예상 별점 오차는 줄었다")
    left.set_xlabel("사용자 입력 평점 수 K")
    left.set_ylabel("사용자별 평균 절대 오차(MAE) · 낮을수록 좋음")
    left.set_xticks(ks)
    left.grid(alpha=0.2)
    for x, value in zip(ks, mae):
        left.annotate(f"{value:.3f}", (x, value), textcoords="offset points", xytext=(0, 9), ha="center", fontsize=9)
    right = left.twinx()
    right.bar(ks, relative, color="#F3B35A", alpha=0.35, width=1.5)
    right.set_ylabel("K0 대비 오차 개선률")
    right.yaxis.set_major_formatter(PercentFormatter(1.0))
    right.set_ylim(0, max(relative) * 1.45)
    left.spines["top"].set_visible(False)
    right.spines["top"].set_visible(False)
    fig.text(0.5, -0.01, "단, 같은 실험에서 추천 순위는 인기도 기준보다 좋아지지 않아 예상 별점과 추천 순위를 분리해야 했다.", ha="center", fontsize=9, color="#5E6673")
    fig.tight_layout()
    save(fig, "prior-expected-star-curve.png")


def korean_coverage() -> None:
    values = [11680, 1056, 8, 614, 377, 51, 16]
    labels = [
        "TMDB 한국-origin",
        "MovieLens와 연결",
        "평점 0개",
        "평점 1~9개",
        "평점 10~99개",
        "평점 100~999개",
        "평점 1,000개+",
    ]
    colors = ["#5B7FFF", "#6D8DFF", "#C4CEFF", "#91A8FF", "#7793FF", "#F3B35A", "#E05263"]
    fig, axis = plt.subplots(figsize=(10, 5.3))
    bars = axis.barh(labels[::-1], values[::-1], color=colors[::-1])
    axis.set_xscale("log")
    axis.set_title("한국 영화는 목록보다 ‘사용자 평점 밀도’가 훨씬 부족하다")
    axis.set_xlabel("영화 수 · 로그 눈금")
    axis.grid(axis="x", alpha=0.2)
    axis.set_axisbelow(True)
    for bar, value in zip(bars, values[::-1]):
        axis.text(value * 1.08, bar.get_y() + bar.get_height() / 2, f"{value:,}편", va="center", fontsize=10)
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)
    fig.text(0.5, -0.01, "한국-origin은 TMDB 필터 기반 proxy이며, 한국 사용자 성능을 뜻하지 않는다.", ha="center", fontsize=9, color="#5E6673")
    fig.tight_layout()
    save(fig, "korean-movie-coverage.png")


def cold_panels() -> None:
    plan = load_json(REPO_ROOT / "outputs/recommendation-evidence/rec-ev-021p/compute-plan.json")
    labels = ["5개 이상", "20개 이상", "100개 이상"]
    keys = ["PANEL_5P", "PANEL_20P", "PANEL_100P"]
    counts = [plan["panels"][key]["safe_target_items"] for key in keys]
    fig, axis = plt.subplots(figsize=(9.5, 5.0))
    bars = axis.bar(labels, counts, color=["#5B7FFF", "#7793FF", "#A5B7FF"], width=0.62)
    axis.set_title("영화 평점을 일부러 줄여 보는 밀도 실험 표본")
    axis.set_xlabel("원래 Base Train 평점 수")
    axis.set_ylabel("안전하게 실험 가능한 영화 수")
    axis.grid(axis="y", alpha=0.2)
    axis.set_axisbelow(True)
    for bar, value in zip(bars, counts):
        axis.text(bar.get_x() + bar.get_width() / 2, value + 65, f"{value:,}편", ha="center", fontsize=11)
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)
    fig.text(0.5, -0.01, f"5-fold × 3 mask seeds · 협업 모델 하나당 최대 {plan['training_configurations_per_collaborative_model']:,}회 학습 예상", ha="center", fontsize=9, color="#5E6673")
    fig.tight_layout()
    save(fig, "cold-density-panels.png")


def tmdb_feature_flow() -> None:
    summary = load_json(REPO_ROOT / "outputs/recommendation-evidence/rec-ev-019b-preflight/coverage-summary.json")
    fig, axis = plt.subplots(figsize=(12, 3.8))
    axis.set_xlim(0, 12)
    axis.set_ylim(0, 4)
    axis.axis("off")
    steps = [
        (0.2, "MovieLens\n사용자 행동", "69,603편 후보", "#E8EEFF"),
        (2.65, "TMDB\n영화 정보", "상세·감독·배우·키워드", "#E8F7F1"),
        (5.1, "Identity\n안전 확인", f"{summary['identity_coverage']['eligible']}/100 확인", "#FFF4DF"),
        (7.55, "특징 생성", "구조 특징 + 결측 mask", "#F4ECFF"),
        (10.0, "E5 embedding", "384차원 · L2 정규화", "#FFECEF"),
    ]
    for index, (x, title, detail, color) in enumerate(steps):
        box = FancyBboxPatch((x, 1.15), 1.8, 1.7, boxstyle="round,pad=0.08,rounding_size=0.12", facecolor=color, edgecolor="#7A8492", linewidth=1.2)
        axis.add_patch(box)
        axis.text(x + 0.9, 2.25, title, ha="center", va="center", fontsize=12, fontweight="bold")
        axis.text(x + 0.9, 1.55, detail, ha="center", va="center", fontsize=8.8, color="#4D5663")
        if index < len(steps) - 1:
            axis.annotate("", xy=(x + 2.35, 2.0), xytext=(x + 1.87, 2.0), arrowprops={"arrowstyle": "->", "color": "#6B7480", "lw": 1.7})
    axis.text(6, 3.55, "MovieLens는 사람을, TMDB는 영화를 설명한다", ha="center", fontsize=16, fontweight="bold")
    axis.text(6, 0.45, "100편 사전검사: identity 99% · 구조/텍스트 100% · IMDb 불일치 1편 격리 · 제품 추천 정책 변경 없음", ha="center", fontsize=10, color="#5E6673")
    fig.tight_layout()
    save(fig, "tmdb-feature-build-flow.png")


def tmdb_feature_coverage() -> None:
    summary = load_json(REPO_ROOT / "outputs/recommendation-evidence/rec-ev-019b/coverage-summary.json")
    structured = pd.read_parquet(
        REPO_ROOT / "outputs/recommendation-evidence/rec-ev-019b/structured-features.parquet",
        columns=["missing_mask"],
    )
    gate_labels = ["TMDB 링크", "Identity", "구조 특징", "텍스트 특징"]
    gate_values = [
        summary["base_train_linked_movies"] / summary["base_train_candidate_movies"],
        summary["identity_coverage"]["rate"],
        summary["structured_coverage"]["rate"],
        summary["text_coverage"]["rate"],
    ]
    thresholds = [0.998, 0.98, 0.95, 0.95]
    missing_bits = [("키워드", 64), ("배우", 32), ("장르", 8), ("상영시간", 4), ("감독", 16), ("줄거리", 128), ("개봉연도", 2)]
    missing_values = [((structured["missing_mask"].astype(int) & bit) != 0).mean() for _, bit in missing_bits]

    fig, (left, right) = plt.subplots(1, 2, figsize=(12, 5.1), gridspec_kw={"width_ratios": [1, 1.25]})
    bars = left.bar(gate_labels, gate_values, color=["#5B7FFF", "#6D8DFF", "#7793FF", "#91A8FF"])
    left.set_ylim(0.94, 1.003)
    left.set_title("전체 69,603편 품질 Gate")
    left.set_ylabel("Coverage")
    left.yaxis.set_major_formatter(PercentFormatter(1.0))
    left.grid(axis="y", alpha=0.2)
    for bar, value, threshold in zip(bars, gate_values, thresholds):
        left.text(bar.get_x() + bar.get_width() / 2, value + 0.0012, f"{value:.2%}", ha="center", fontsize=10, fontweight="bold")
        left.text(bar.get_x() + bar.get_width() / 2, 0.943, f"기준 {threshold:.1%}", ha="center", fontsize=8, color="#5E6673")
    left.spines["top"].set_visible(False)
    left.spines["right"].set_visible(False)

    labels = [label for label, _ in missing_bits][::-1]
    values = missing_values[::-1]
    bars = right.barh(labels, values, color="#F3B35A")
    right.set_title("Identity 확인 영화의 메타데이터 결측")
    right.set_xlabel("결측 영화 비율")
    right.xaxis.set_major_formatter(PercentFormatter(1.0))
    right.set_xlim(0, max(missing_values) * 1.22)
    right.grid(axis="x", alpha=0.2)
    for bar, value in zip(bars, values):
        right.text(value + 0.003, bar.get_y() + bar.get_height() / 2, f"{value:.2%}", va="center", fontsize=9)
    right.spines["top"].set_visible(False)
    right.spines["right"].set_visible(False)
    fig.suptitle("TMDB 특징은 전체 Gate를 통과했지만 키워드는 24.42%가 비어 있다", fontsize=16, fontweight="bold")
    fig.text(0.5, -0.01, "결측은 부정 선호가 아니며, 특징이 없는 모델에서 B0 인기도 fallback을 사용한다.", ha="center", fontsize=9, color="#5E6673")
    fig.tight_layout()
    save(fig, "tmdb-feature-coverage.png")


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    configure()
    top2_eligibility()
    prior_star_curve()
    korean_coverage()
    cold_panels()
    tmdb_feature_flow()
    tmdb_feature_coverage()
    print(OUTPUT)


if __name__ == "__main__":
    main()
