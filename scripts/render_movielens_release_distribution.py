"""Render the frozen metadata-only distribution audit; no model evaluation."""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from matplotlib.ticker import PercentFormatter

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/research/movielens-release-distribution"
SOURCE = ROOT / "outputs/recommendation-evidence/movielens-release-distribution"


def main():
    summary = json.loads((SOURCE / "summary.json").read_text(encoding="utf-8"))
    rows = [
        "# MovieLens 개봉연도별 전체 표",
        "",
        "[결론과 해석](REPORT.md) · 원본 87,585편 / 별점 기록 32,000,204건 / 200,948명.",
        "제목 끝 연도를 사용하며 추출 실패는 UNKNOWN으로 보존한다. 리뷰 문장 수가 아닌 별점 기록 수다.",
        "평가자는 해당 개봉연도 영화에 별점을 남긴 고유 사용자다. 여러 연도에 중복될 수 있어 합산하지 않는다.",
        "중앙값은 평가 0편을 포함한 영화별 평가 수의 중앙값이다. 영화가 없으면 —로 표시한다.",
        "현재 채점 가중치는 REC045 평균의 가중치이며 실제 오차 기여율은 아니다.",
        "2024~2026년은 자료 관측 기간 밖이다. 그 이전의 0편 연도도 세계의 개봉 영화가 없었다는 뜻은 아니다.",
        "",
        "| 개봉연도 | 영화 수 | 평가 기록 수 | 고유 평가자 | 영화당 평가 중앙값 | 전체 평가 비중 | REC045 채점 가중치 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary["years"]:
        year = row["release_year"]
        label = "UNKNOWN" if year < 0 else str(year)
        median = (
            f"{row['median_ratings_per_catalog_movie']:g}"
            if row["catalog_movies"]
            else "—"
        )
        rows.append(
            f"| {label} | {row['catalog_movies']:,} | {row['rating_records']:,} | "
            f"{row['unique_raters']:,} | {median} | {row['rating_share']:.4%} | "
            f"{row['eval_metric_weight']:.4%} |"
        )
    rows.extend(
        [
            "",
            "출처: 로컬 `outputs/recommendation-evidence/movielens-release-distribution/summary.json`.",
            "이 표는 봉인된 집계를 표시만 하며 별점 값이나 모델 예측을 읽지 않는다.",
        ]
    )
    (DOC / "BY_YEAR.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
    font = Path("C:/Windows/Fonts/malgun.ttf")
    if font.exists():
        font_manager.fontManager.addfont(str(font))
        plt.rcParams["font.family"] = font_manager.FontProperties(
            fname=str(font)
        ).get_name()
    plt.rcParams.update(
        {
            "axes.unicode_minus": False,
            "font.size": 11,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )
    known = [row for row in summary["years"] if row["release_year"] > 0]
    groups = [row for row in summary["decades"] if row["group"] != "UNKNOWN"]
    recent = [row for row in known if 2015 <= row["release_year"] <= 2023]
    fig = plt.figure(figsize=(14, 13), facecolor="white")
    grid = fig.add_gridspec(3, 1, height_ratios=[1.05, 1.2, 1], hspace=0.60)
    a, b, c = [fig.add_subplot(grid[i]) for i in range(3)]
    fig.suptitle(
        "MovieLens 평가가 어느 시대의 영화에 몰려 있는가",
        x=0.08,
        ha="left",
        fontsize=21,
        fontweight="bold",
        y=0.98,
    )
    fig.text(
        0.08,
        0.945,
        "87,585편 · 별점 기록 32,000,204건 · 실측 관측 종료 2023-10-13 UTC",
        color="#555555",
        fontsize=12,
    )
    a.bar(
        [r["release_year"] for r in known],
        [r["rating_records"] / 1e6 for r in known],
        color="#246B8E",
        width=0.9,
    )
    a.set(
        title="1. 개봉연도별 평가 기록 수 — 1990·2000년대 영화가 전체 평가의 61.47%",
        ylabel="평가 기록 (백만 건)",
        xlabel="영화 개봉연도 (MovieLens 제목 표기)",
    )
    a.set_xlim(1870, 2027)
    a.set_xticks(np.arange(1880, 2021, 20))
    a.grid(axis="y", alpha=0.18)
    a.axvspan(2023.5, 2027, color="#e7e7e7")
    a.annotate(
        "2024~2026년작\n관측 자료 없음",
        xy=(2025, 0.02),
        xytext=(1934, 1.05),
        fontsize=10,
        arrowprops={"arrowstyle": "->", "color": "#777777"},
        color="#666666",
    )
    x = np.arange(len(groups))
    width = 0.25
    fields = [
        ("movie_share", "MovieLens 영화 수 비중", "#ABB8BF"),
        ("rating_share", "MovieLens 평가 기록 비중", "#246B8E"),
        ("eval_metric_weight", "현재 채점의 평균 가중치", "#C45536"),
    ]
    for i, (key, label, color) in enumerate(fields):
        values = [r[key] * 100 for r in groups]
        bars = b.bar(x + (i - 1) * width, values, width, label=label, color=color)
        b.bar_label(bars, labels=[f"{v:.2f}%" for v in values], fontsize=9, padding=3)
    b.set_xticks(
        x, ["1980년 이전", "1980년대", "1990년대", "2000년대", "2010년대", "2020~2023"]
    )
    b.set(
        title="2. 영화가 많은 시대와, 평균 점수에 많이 반영되는 시대는 다르다",
        ylabel="각 전체 분모에서의 비중",
        ylim=(0, 39),
    )
    b.yaxis.set_major_formatter(PercentFormatter())
    b.legend(loc="upper center", bbox_to_anchor=(0.5, -0.15), ncol=3, frameon=False)
    b.grid(axis="y", alpha=0.18)
    x = np.arange(len(recent))
    for i, (key, label, color) in enumerate([fields[1], fields[2]]):
        vals = [r[key] * 100 for r in recent]
        bars = c.bar(x + (i - 0.5) * 0.34, vals, 0.34, label=label, color=color)
        c.bar_label(bars, labels=[f"{v:.2f}%" for v in vals], fontsize=9, padding=3)
    c.set_xticks(x, [str(r["release_year"]) for r in recent])
    c.set(
        title="3. 최근 개봉연도별 비중 — 2023년작의 현재 채점 가중치는 0.14%",
        ylabel="전체 평가 또는 채점에서의 비중",
        xlabel="영화 개봉연도",
        ylim=(0, 2.35),
    )
    c.yaxis.set_major_formatter(PercentFormatter())
    c.grid(axis="y", alpha=0.18)
    fig.text(
        0.08,
        0.018,
        "원본 전체가 분모. 제목 연도 추출 불가 617편(평가의 0.11%)은 그래프에서만 생략.\n"
        "채점 가중치는 REC045의 사용자·회차·E편수 가중치이며 실제 오차 기여율은 아님. 신작일수록 관측 기간이 짧음.",
        fontsize=10,
        color="#555555",
    )
    fig.subplots_adjust(left=0.08, right=0.98, top=0.885, bottom=0.10)
    fig.savefig(DOC / "release-distribution.png", dpi=160)
    plt.close(fig)


if __name__ == "__main__":
    main()
