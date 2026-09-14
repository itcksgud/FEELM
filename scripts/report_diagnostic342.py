"""Render the three completed diagnostic tables without changing their metrics."""
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/recommendation-evidence/diagnostic342'
DOC = ROOT / 'docs/recommendation/experiments/diagnostic342'


def digest(path):
    b = path.read_bytes()
    return {'bytes': len(b), 'sha256': hashlib.sha256(b).hexdigest()}


def main():
    seal = json.loads((OUT / 'result-seal.json').read_text(encoding='utf-8'))
    for name, expected in seal['files'].items():
        assert digest(OUT / name) == expected, name
    target = DOC / 'pages.png'
    assert not target.exists(), 'preserve existing figure'
    page = pd.read_csv(OUT / 'page-summary.csv')
    page = page[(page.cap == 10) & (page.pool == 'ALL') & (page.h_group == 'H_POSITIVE') &
                (page.cohort == 'common_j6') & (page.kind == 'page')]
    font_manager.fontManager.addfont('C:/Windows/Fonts/malgun.ttf')
    plt.rcParams.update({'font.family': 'Malgun Gothic', 'axes.unicode_minus': False,
                         'font.size': 11, 'axes.spines.top': False, 'axes.spines.right': False})
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.7))
    names = {'S': '기존 FM', 'R': '근거량 보정 FM', 'GBT': 'GBT', 'BLEND_0.25': 'FM + ALS 25%'}
    colors = ['#2166ac', '#1b9e77', '#8e44ad', '#d95f02']
    for axis, metric, scale, title in zip(axes, ['stars', 'low'], [1, 100], ['두 편의 실제 평균별점', '두 편 중 2점 이하 비율 (%)']):
        for (variant, label), color in zip(names.items(), colors):
            a = page[(page.variant == variant) & (page.metric == metric)].sort_values('end')
            assert a.valid.eq(134).all() and a.end.tolist() == [2, 4, 6]
            axis.plot(range(3), a['mean'] * scale, marker='o', linewidth=2, color=color, label=label)
        axis.set_xticks(range(3), ['첫 2편', '다음 2편', '그다음 2편'])
        axis.set_title(title)
        axis.grid(axis='y', alpha=.2)
    axes[0].set_ylim(3.65, 4.0)
    axes[1].set_ylim(0, 8)
    fig.suptitle('같은 134명의 관측 영화에서 2편씩 비교', fontsize=16)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc='lower center', bbox_to_anchor=(.5, .055), ncol=4, frameon=False)
    fig.text(.5, .015, '입력 상한 10편 · 개인 입력 있음 · 관측 후보 6편 이상 · 기술통계이며 유의한 우열을 뜻하지 않음', ha='center', fontsize=9)
    fig.tight_layout(rect=(0, .16, 1, .93))
    fig.savefig(target, dpi=160)
    plt.close(fig)
    print(json.dumps(digest(target)))


if __name__ == '__main__':
    main()
