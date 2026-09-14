"""Render verified count comparison with plain numeric logarithmic tick labels."""
from pathlib import Path
import hashlib
import json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import NullFormatter

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/recommendation-evidence/presentation-count-ratio-20260913'
DOC = ROOT/'docs/recommendation/experiments/hybrid345/data-context/count-ratio'


def pin(p):
    with p.open('rb') as f: digest=hashlib.file_digest(f,'sha256').hexdigest()
    return {'path':str(p),'bytes':p.stat().st_size,'sha256':digest}


def main():
    target=DOC/'count-ratio-comparison-v2.png'
    receipt=OUT/'render-v2-manifest.json'
    assert not target.exists() and not receipt.exists()
    inputs=[OUT/'paired-movie-counts.parquet',DOC/'count-ratio-summary.csv',DOC/'summary.json']
    before=[pin(p) for p in inputs]
    frame=pd.read_parquet(inputs[0])
    table=pd.read_csv(inputs[1])
    reference=json.loads(inputs[2].read_text(encoding='utf-8'))['global_median_reference']
    plt.rcParams.update({'font.family':'Malgun Gothic','axes.unicode_minus':False,'font.size':11})
    fig,(ax,bx)=plt.subplots(1,2,figsize=(13.8,6.3),layout='constrained',gridspec_kw={'width_ratios':[1.1,1]})
    lx=np.log10(frame.ml_count.to_numpy());ly=np.log10(frame.tmdb_count.to_numpy())
    hb=ax.hexbin(lx,ly,gridsize=65,mincnt=1,bins='log',cmap='Blues')
    right=max(lx.max(),ly.max())+.15;line=np.array([0,right])
    ax.plot(line,line+np.log10(reference),color='#D17C24',lw=1.8,label=f'일정 배수 기준선: TMDB = ML × {reference:.2f}')
    ax.set_xlim(0,right);ax.set_ylim(0,right)
    ticks=np.arange(0,int(right)+1)
    ax.set_xticks(ticks,[f'{10**x:,}' for x in ticks]);ax.set_yticks(ticks,[f'{10**x:,}' for x in ticks])
    ax.set_xlabel('MovieLens 평가 수 · 로그 눈금');ax.set_ylabel('TMDB 투표 수 · 로그 눈금')
    ax.set_title(f'같은 영화 {len(frame):,}편의 평가 수',loc='left',fontsize=15,fontweight='bold')
    ax.legend(loc='upper left',fontsize=9)
    cbar=fig.colorbar(hb,ax=ax,label='칸 안의 영화 수',shrink=.75)
    cbar.set_ticks([1,10,100,1000],labels=['1','10','100','1,000'])
    plotrows=table[(table.family.eq('scope') & table.group.isin(['ALL_PAIRED','BOTH_COUNTS_GE100'])) |
        (table.family.eq('ml_title_epoch') & ~table.group.eq('YEAR_UNKNOWN'))]
    labels={'ALL_PAIRED':'전체 같은 영화','BOTH_COUNTS_GE100':'양쪽 평가 100개 이상','BEFORE_1980':'1980년 이전',
        '1980s':'1980년대','1990s':'1990년대','2000s':'2000년대','2010s':'2010년대','2020_2023':'2020~2023년'}
    for idx,row in enumerate(plotrows.itertuples()):
        bx.plot([row.ratio_p5,row.ratio_p95],[idx,idx],color='#8497AD',lw=2)
        bx.plot([row.ratio_p25,row.ratio_p75],[idx,idx],color='#375C83',lw=6,solid_capstyle='butt')
        bx.scatter([row.ratio_p50],[idx],s=40,color='#D17C24',zorder=3)
    bx.set_yticks(range(len(plotrows)),[f'{labels[x.group]} (n={x.movies:,})' for x in plotrows.itertuples()])
    bx.set_xscale('log');bx.invert_yaxis()
    bx.set_xticks([.1,1,10,100],labels=['0.1','1','10','100'])
    bx.xaxis.set_minor_formatter(NullFormatter())
    bx.axvline(reference,color='#D17C24',ls='--',lw=1)
    bx.set_title('TMDB ÷ MovieLens 평가 수의 분포',loc='left',fontsize=15,fontweight='bold')
    bx.set_xlabel('배수 · 로그 눈금 / 점: 중앙값, 굵은 선: 25~75%, 가는 선: 5~95%')
    bx.grid(axis='x',alpha=.15)
    for a in (ax,bx):a.spines[['right','top']].set_visible(False)
    fig.supxlabel('관측 시점과 참여 집단이 다른 누적 평가 수. 높은 순위 상관이 일정한 배수를 뜻하지는 않습니다.',fontsize=10)
    fig.savefig(target,dpi=170);plt.close(fig)
    assert before == [pin(p) for p in inputs]
    receipt.write_text(json.dumps({'status':'RENDER_ONLY_NUMERIC_TICKS_FIXED','script':pin(Path(__file__)),
        'inputs':before,'output':pin(target),'numeric_recalculation':False},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(target)


if __name__=='__main__':main()
