"""Render aggregate REC047 results; never fits or reopens labels."""

from pathlib import Path
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.ticker import PercentFormatter

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/recommendation-evidence/rec-ev-047"
DOC = ROOT / "docs/recommendation/experiments/rec-ev-047"


def main():
    font = Path("C:/Windows/Fonts/malgun.ttf")
    if font.exists():
        font_manager.fontManager.addfont(str(font))
        plt.rcParams["font.family"] = "Malgun Gothic"
    plt.rcParams["axes.unicode_minus"] = False
    table = pd.read_csv(OUT / "metrics.csv")
    table = table[table.cohort == "eligible_k"]
    colors = {
        "ALS": "#2463d4",
        "FM": "#d58a11",
        "ALS_FM": "#07876e",
        "RIDGE": "#8551bb",
        "GBT": "#cf4e60",
    }
    names = {
        "ALS": "ALS",
        "FM": "FM",
        "ALS_FM": "ALS+FM",
        "RIDGE": "공유 리지",
        "GBT": "Spark GBT",
    }
    fig, axs = plt.subplots(2, 2, figsize=(12.8, 8.4), facecolor="#f5f7fb")
    fig.suptitle(
        "같은 평가 자료, 더 많은 학습 사용자와 과거 별점",
        fontsize=20,
        fontweight="bold",
        y=0.97,
        color="#19253c",
    )
    for j, k in enumerate([10, 30]):
        for i, metric in enumerate(["mse", "pa"]):
            ax = axs[i, j]
            ax.set_facecolor("white")
            for method, color in colors.items():
                s = table[
                    (table.k == k) & (table.metric == metric) & (table.method == method)
                ].sort_values("level")
                ax.plot(
                    [0, 1, 2],
                    s["mean"],
                    label=names[method],
                    color=color,
                    marker="o",
                    linewidth=2.1,
                    markersize=5,
                )
            n = int(
                table[
                    (table.k == k) & (table.metric == metric) & (table.method == "ALS")
                ].users.iloc[0]
            )
            ax.set_title(
                f"입력 {k}편 · {n}명 | "
                + ("별점 오차 ↓" if metric == "mse" else "선호 순서 정확도 ↑"),
                fontsize=12,
                pad=11,
            )
            ax.set_xticks([0, 1, 2], ["25%", "50%", "100%"])
            ax.set_xlim(-0.14, 2.14)
            ax.set_xlabel("고정 학습 사용자 집합에서 사용한 비율", fontsize=10)
            ax.set_ylabel(
                "별점 제곱 오차 (MSE)" if metric == "mse" else "선호 순서 정확도",
                fontsize=10,
            )
            if metric == "pa":
                ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
            ax.grid(axis="y", alpha=0.18)
            ax.spines[["top", "right"]].set_visible(False)
            ax.tick_params(labelsize=9)
    handles, labels = axs[0, 0].get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        ncol=5,
        loc="lower center",
        bbox_to_anchor=(0.5, 0.055),
        frameon=False,
        fontsize=11,
    )
    fig.text(
        0.5,
        0.022,
        "점은 평균값이다. 불확실성·보정 대조는 보고서 표 참고. 관측된 2023년 MovieLens 별점이며 한국 서비스 성능이 아니다.",
        ha="center",
        fontsize=9,
        color="#59687e",
    )
    fig.subplots_adjust(
        left=0.08, right=0.97, top=0.86, bottom=0.18, wspace=0.24, hspace=0.52
    )
    fig.savefig(DOC / "learning-curves.png", dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)
    print(DOC / "learning-curves.png")


if __name__ == "__main__":
    main()
