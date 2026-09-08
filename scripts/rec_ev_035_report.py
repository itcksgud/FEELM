"""Render a static, exportable chart from audited REC035 aggregate metrics."""
from pathlib import Path
import json

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'outputs/recommendation-evidence/rec-ev-035/metrics.json'
DEST = ROOT / 'docs/recommendation/experiments/rec-ev-035'


def main():
    data = json.loads(SOURCE.read_text(encoding='utf-8'))
    lookup = {(r['n'], r['policy']): r for r in data['aggregate']}
    plt.rcParams.update({'font.family': 'Malgun Gothic', 'axes.unicode_minus': False, 'font.size': 11,
                         'axes.spines.top': False, 'axes.spines.right': False})
    fig, axes = plt.subplots(1, 3, figsize=(14.6, 5.6), constrained_layout=False)
    policies = [('A_RAW', 'A 원별점', '#2766A4'), ('B_RELATIVE', 'B 상대값', '#CD762A'),
                ('C_BINARY_BRIDGE', 'C 이진 응답 환산', '#168479')]
    metrics = [('MEAN_Q', '두 영화 평균 선호 위치 ↑', '개인 상대점수 Q × 100'),
               ('MIN_Q', '낮은 쪽 영화의 선호 위치 ↑', '개인 상대점수 Q × 100'),
               ('HARM20', '낮은 선호 영화 포함 비율 ↓', 'Q ≤ 0.2인 영화가 하나 이상 포함된 비율(%)')]
    for ax, (metric, title, ylabel) in zip(axes, metrics, strict=True):
        for p, (policy, label, color) in enumerate(policies):
            values = [100 * lookup[n, policy][metric] for n in [5, 10, 30]]
            x = np.arange(3) + (p-1) * .25
            bars = ax.bar(x, values, width=.22, color=color, label=label)
            ax.bar_label(bars, labels=[f'{v:.1f}' for v in values], padding=3, fontsize=9)
        ax.set_xticks(range(3), ['입력 5개', '입력 10개', '입력 30개'])
        ax.set_ylim(0, 100); ax.set_title(title, fontsize=13, fontweight='bold', pad=14)
        ax.set_ylabel(ylabel, fontsize=10); ax.grid(axis='y', alpha=.16); ax.set_axisbelow(True)
    fig.suptitle('원별점 · 상대값 · 이진 응답 환산 — 같은 2,180명 비교', x=.5, y=.98, fontsize=18, fontweight='bold')
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, ncol=3, loc='upper center', bbox_to_anchor=(.5, .91), frameon=False)
    fig.text(.5, .025, '이미 평가된 별도 영화 목록 안에서 상위 2편을 비교한 개발 진단입니다. Q는 정확도·만족도 비율이 아닙니다.\n'
             '막대는 평균값입니다. 차이의 불확실성은 RESULT.md의 사용자 단위 짝 비교 구간에서 확인하세요.',
             ha='center', fontsize=10, color='#555555')
    fig.subplots_adjust(left=.06, right=.985, top=.76, bottom=.19, wspace=.34)
    fig.savefig(DEST / 'comparison.png', dpi=170, facecolor='white')
    fig.savefig(DEST / 'comparison.svg', facecolor='white')
    plt.close(fig)
    print('AGGREGATE_COMPARISON_CHART_WRITTEN')


if __name__ == '__main__':
    main()
