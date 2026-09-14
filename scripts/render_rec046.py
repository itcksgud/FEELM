"""Render saved aggregates only; never fits or selects a model."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import pandas as pd

root = Path(__file__).resolve().parents[1]
out = root / "outputs/recommendation-evidence/rec-ev-046"
doc = root / "docs/recommendation/experiments/rec-ev-046"
font = Path("C:/Windows/Fonts/malgun.ttf")
if font.exists():
    font_manager.fontManager.addfont(str(font))
    plt.rcParams["font.family"] = "Malgun Gothic"
plt.rcParams["axes.unicode_minus"] = False
table = pd.read_csv(out / "summary.csv")
colors = {
    "ALS": "#64748b",
    "FM": "#2563eb",
    "ALS_FM": "#7c3aed",
    "RIDGE": "#059669",
    "GBT": "#ea580c",
}
labels = {
    "ALS": "ALS + 기본 처리",
    "FM": "FM",
    "ALS_FM": "ALS + FM",
    "RIDGE": "공유 교차 리지",
    "GBT": "Spark GBT",
}
ks = [0, 1, 5, 10, 30]
fig, axes = plt.subplots(2, 2, figsize=(12, 8.8))
fig.subplots_adjust(
    left=0.08, right=0.98, top=0.88, bottom=0.18, hspace=0.44, wspace=0.25
)
for row, group in enumerate(["ALL", "WITHHELD"]):
    for col, metric in enumerate(["mse", "pair_accuracy"]):
        ax = axes[row, col]
        for method, color in colors.items():
            data = (
                table[(table.group == group) & (table.method == method)]
                .set_index("k")
                .loc[ks]
            )
            y = data[metric].to_numpy() * (100 if metric == "pair_accuracy" else 1)
            ax.plot(
                range(len(ks)),
                y,
                label=labels[method],
                color=color,
                marker="o",
                linewidth=2,
                linestyle="--" if method == "ALS_FM" else "-",
            )
        ax.set_xticks(range(len(ks)), [str(k) for k in ks])
        ax.set_xlabel("입력 별점 수 K")
        ax.set_ylabel(
            "별점 MSE · 낮을수록 좋음"
            if metric == "mse"
            else "선호 순서 정확도(%) · 높을수록 좋음"
        )
        ax.set_title(
            (
                "전체 관측 평가 영화"
                if group == "ALL"
                else "평가를 학습에서 전부 뺀 영화"
            )
            + (" — 별점 오차" if metric == "mse" else " — 사용자 내 순서")
        )
        ax.grid(alpha=0.2)
        ax.spines[["top", "right"]].set_visible(False)
        if metric == "mse":
            ax.set_ylim(bottom=0)
        else:
            ax.set_ylim(45, 65)
            ax.axhline(50, color="#94a3b8", linestyle=":", linewidth=1)
handles, legend_labels = axes[0, 0].get_legend_handles_labels()
fig.legend(
    handles,
    legend_labels,
    loc="lower center",
    bbox_to_anchor=(0.5, 0.07),
    ncol=5,
    frameon=False,
)
fig.suptitle("REC046 · 같은 관측 정보로 비교한 5개 실행안", fontsize=17)
fig.text(
    0.5,
    0.025,
    "현재 TMDB + 2019년 시간 경계의 MovieLens · 학습 12,000명/회차 · 3회차 · 실서비스 성능 아님\n순서 정확도 축은 45~65%로 확대 · 점 추정치이며 신뢰구간은 contrasts.json 참조",
    ha="center",
    fontsize=10,
    color="#475569",
)
fig.savefig(doc / "comparison.png", dpi=160, bbox_inches="tight")
plt.close(fig)
print(doc / "comparison.png")
