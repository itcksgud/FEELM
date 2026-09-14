"""Plot saved population audit aggregates only."""

from pathlib import Path
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/recommendation-evidence/rec-ev-046-data-audit"
DOC = ROOT / "docs/recommendation/experiments/rec-ev-046/data-audit"
font_manager.fontManager.addfont("C:/Windows/Fonts/malgun.ttf")
plt.rcParams["font.family"] = "Malgun Gothic"
plt.rcParams["axes.unicode_minus"] = False
activities = ["0", "1_9", "10_29", "30_99", "100_299", "300_PLUS"]
activity_labels = ["0편", "1~9편", "10~29편", "30~99편", "100~299편", "300편 이상"]
items = ["0", "1_9", "10_49", "50_PLUS"]
colors = ["#e2e8f0", "#cbd5e1", "#94a3b8", "#60a5fa", "#2563eb", "#1e3a8a"]
fig, ax = plt.subplots(2, 2, figsize=(13, 10))
fig.subplots_adjust(
    left=0.10, right=0.97, top=0.89, bottom=0.17, wspace=0.3, hspace=0.65
)
u = pd.read_csv(OUT / "user-strata.csv", dtype={"activity": str})
cohorts = [
    ("DEVELOPMENT_ALLOWED", -1, "기존 개발 집단"),
    ("TRAIN_SELECTED", 0, "학습 r0"),
    ("VALIDATION", -1, "검증"),
    ("EVALUATION", -1, "평가"),
]
left = np.zeros(len(cohorts))
for a, label, color in zip(activities, activity_labels, colors):
    v = (
        np.array(
            [
                u[
                    (u.role == role) & (u["round"] == r) & (u.activity == a)
                ].user_share.iloc[0]
                for role, r, _ in cohorts
            ]
        )
        * 100
    )
    ax[0, 0].barh(range(len(cohorts)), v, left=left, color=color, label=label)
    left += v
ax[0, 0].set_yticks(range(4), [v[2] for v in cohorts])
ax[0, 0].invert_yaxis()
ax[0, 0].set_xlim(0, 100)
ax[0, 0].set_xlabel("고유 사용자 비중 (%)")
ax[0, 0].set_title("기준 시점 이전 실제 평가 이력의 분포")
ax[0, 0].legend(
    ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.23), fontsize=9, frameon=False
)
r = pd.read_csv(OUT / "retention.csv")
xx = np.arange(3)
for offset, (column, label, color) in enumerate(
    [
        ("raw_pre_rows", "원본 과거", "#94a3b8"),
        ("nonheld_pre_rows", "카탈로그·강제 제외 적용", "#60a5fa"),
        ("actual_R_rows", "실제 학습 R", "#2563eb"),
    ]
):
    ax[0, 1].bar(
        xx + (offset - 1) * 0.24, r[column] / 1e6, width=0.24, label=label, color=color
    )
ax[0, 1].set_xticks(xx, ["r0", "r1", "r2"])
ax[0, 1].set_ylabel("평가 기록 수 (백만)")
ax[0, 1].set_title("같은 학습자 12,000명의 관측 유지량")
ax[0, 1].legend(
    loc="upper center", bbox_to_anchor=(0.5, -0.16), fontsize=9, frameon=False
)
c = pd.read_csv(OUT / "cell-metrics.csv", dtype={"activity": str, "item_support": str})
c = c[
    (c.role == "evaluation")
    & (c.k == 30)
    & (c.method == "ALS")
    & (c.item_support != "ALL")
]
z = (
    c.pivot(index="activity", columns="item_support", values="users")
    .reindex(index=activities, columns=items)
    .to_numpy()
)
ax[1, 0].imshow(z, cmap="Blues", aspect="auto", vmin=0)
for i in range(len(activities)):
    for j in range(len(items)):
        ax[1, 0].text(
            j,
            i,
            "자료 없음" if z[i, j] == 0 else f"{int(z[i, j])}명",
            ha="center",
            va="center",
            color="white" if z[i, j] > z.max() * 0.55 else "#0f172a",
            fontsize=9,
        )
ax[1, 0].set_yticks(range(6), activity_labels)
ax[1, 0].set_xticks(range(4), ["0개", "1~9개", "10~49개", "50개 이상"])
ax[1, 0].set_xlabel("영화의 실제 학습 평가 수")
ax[1, 0].set_title(
    "평가자 활동량 × 영화 지원량 · 고유 사용자 수\nK30 기준 · 셀 간 사용자는 중복될 수 있음",
    fontsize=11,
)
s = pd.read_csv(OUT / "sampling-exposure.csv")
s = s[s.role == "EVALUATION_TARGET"]
ax[1, 1].bar(
    xx - 0.16,
    s.row_fraction_actual_supported * 100,
    width=0.32,
    label="실제 학습 R",
    color="#2563eb",
)
ax[1, 1].bar(
    xx + 0.16,
    s.row_fraction_same_users_full_supported * 100,
    width=0.32,
    label="같은 학습자 전체 허용 과거",
    color="#94a3b8",
)
ax[1, 1].set_ylim(0, 100)
ax[1, 1].set_xticks(xx, ["r0", "r1", "r2"])
ax[1, 1].set_ylabel("평가 목표 중 학습 관측이 있는 비율 (%)")
ax[1, 1].set_title(
    "학습 관측 축소로 줄어든 영화 지원\n평가 행 비중 · 실제 예측 가능률이나 성능 곡선 아님",
    fontsize=11,
)
ax[1, 1].legend(
    loc="upper center", bbox_to_anchor=(0.5, -0.16), fontsize=9, frameon=False
)
fig.suptitle("REC046 데이터 구성 감사", fontsize=20)
fig.text(
    0.5,
    0.025,
    "기존 모델·사용자·설정을 유지한 사후 진단 · 활동량은 2019년 이전 평가 기록 수\n데이터 분포의 차이를 보여 주며, 2026 한국 서비스의 성능을 검증한 결과는 아님",
    ha="center",
    fontsize=10,
    color="#475569",
)
fig.savefig(DOC / "population-audit.png", dpi=150, bbox_inches="tight")
plt.close(fig)
print(DOC / "population-audit.png")
