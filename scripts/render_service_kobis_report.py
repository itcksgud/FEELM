"""Render already-audited KOBIS comparison; no network, model, or source edits."""
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
DATA = ROOT / 'outputs/recommendation-evidence/kobis-service-final-20260913'
AUDIT = ROOT / 'outputs/recommendation-evidence/kobis-service-final-audit-20260913'
OUT = ROOT / 'docs/recommendation/service-final-20260913/data-v2'
TITLES = ['명량', '극한직업', '어벤져스: 엔드게임', '범죄도시3', '기생충', '서울의 봄', '파묘']


def pin(p):
    with p.open('rb') as f:
        digest = hashlib.file_digest(f, 'sha256').hexdigest()
    return {'path': str(p), 'bytes': p.stat().st_size, 'sha256': digest}


def main():
    assert not OUT.exists(), 'Preserve completed outputs'
    inputs = [DATA/'movie-comparison.csv', AUDIT/'exclude-release-after-ml-cutoff-statistics.csv',
              AUDIT/'result-review.json', AUDIT/'sensitivity-b.json']
    before = [pin(p) for p in inputs]
    audit = json.loads(inputs[2].read_text(encoding='utf-8'))
    assert audit['numeric_and_parser_status'] == 'PASS'
    frame = pd.read_csv(inputs[0])
    rows = frame[frame.kobis_title.isin(TITLES)].set_index('kobis_title').loc[TITLES].reset_index()
    assert len(rows) == 7 and rows.match_status.eq('MATCHED').all()
    assert rows.kobis_title.is_unique
    stats = pd.read_csv(inputs[1]).set_index('group')
    assert int(stats.loc['ALL_VERIFIED_TRIPLES', 'movies']) == 337
    assert [int(stats.loc[x, 'movies']) for x in ['NATION_K', 'NATION_F']] == [163, 174]
    OUT.mkdir(parents=True)
    cols = ['kobis_title','kobis_audience_cumulative','kobis_sales_krw_cumulative',
            'ml_count','ml_mean_5','tmdb_count','tmdb_mean_10','tmdb_release_date',
            'service_movie_id','tmdb_id','movieId','fetched_at_utc']
    rows[cols].to_csv(OUT/'movie-examples.csv', index=False, encoding='utf-8-sig')
    plt.rcParams.update({'font.family':'Malgun Gothic','axes.unicode_minus':False,'font.size':12})
    fig, axes = plt.subplots(1,3,figsize=(15.5,7.4), gridspec_kw={'width_ratios':[1.1,1,1]})
    fig.subplots_adjust(left=.17,right=.94,bottom=.20,top=.79,wspace=.28)
    y = np.arange(len(rows))
    settings = [('kobis_audience_cumulative','KOBIS 누적 관객','#C77828',False),
                ('ml_count','MovieLens 평가 수','#5472AA',True),
                ('tmdb_count','TMDB 투표 수','#338B82',True)]
    for j,(col,title,color,log) in enumerate(settings):
        ax = axes[j]
        values = rows[col].to_numpy(float)
        if log:
            ax.set_xscale('log'); ax.set_xlim(.75, 180000)
            ax.set_xticks([1,100,10000],labels=['1','100','10,000'])
            ax.xaxis.set_minor_formatter(NullFormatter())
            ax.set_xlabel('개 · 로그 눈금')
        else:
            ax.set_xlim(0,24_500_000)
            ax.set_xticks([0,10_000_000,20_000_000],labels=['0','1,000만','2,000만'])
            ax.set_xlabel('명 · 선형 눈금')
        for iy,v in enumerate(values):
            if np.isfinite(v) and v>0:
                ax.barh(iy,v,height=.50,color=color)
                ax.text(v*1.15 if log else v+300000,iy,f'{int(v):,}',va='center',fontsize=11)
            else:
                ax.text(1.2 if log else 300000,iy,'관측 없음',va='center',color='#6C7380',fontsize=11)
        ax.set_title(title,loc='left',fontsize=16,fontweight='bold',pad=15)
        ax.set_yticks(y, TITLES if j==0 else ['']*len(y))
        ax.set_ylim(len(rows)-.5, -.5)
        ax.tick_params(axis='y',length=0,pad=12)
        ax.spines[['right','top','left']].set_visible(False)
        ax.grid(axis='x',alpha=.12); ax.set_axisbelow(True)
    fig.text(.05,.94,'국내에서 많이 본 영화와 평가 기록이 많은 영화는 다릅니다',fontsize=22,fontweight='bold')
    fig.text(.05,.88,'국내 흥행 사례 7편 · 각 패널의 단위와 눈금이 다릅니다',fontsize=13,color='#475569')
    fig.text(.05,.10,'KOBIS: 2026-09-13 현재 조회한 통합전산망 누적 관객 · TMDB: 보유 2026-09-09 스냅샷',fontsize=11)
    fig.text(.05,.055,'MovieLens: 2023-10-13까지의 고정 자료 · 사례 표이며 전체 영화의 대표 표본은 아닙니다',fontsize=11)
    fig.savefig(OUT/'movie-examples.png',dpi=150); plt.close(fig)
    labels=['한국영화\n(n=163)','외국영화\n(n=174)']
    fig, axes=plt.subplots(1,2,figsize=(12.4,6.0))
    fig.subplots_adjust(left=.09,right=.97,bottom=.24,top=.74,wspace=.25)
    for ax,col,title in zip(axes,['ml_per_audience_p50','tmdb_per_audience_p50'],['MovieLens 평가 수','TMDB 투표 수']):
        values=stats.loc[['NATION_K','NATION_F'],col].to_numpy(float)*1_000_000
        bars=ax.bar(labels,values,color=['#C77828','#5472AA'],width=.45)
        ax.set_yscale('log');ax.set_ylim(1,18000)
        ax.set_yticks([1,10,100,1000,10000],labels=['1','10','100','1,000','10,000'])
        ax.yaxis.set_minor_formatter(NullFormatter())
        for b,v in zip(bars,values):ax.text(b.get_x()+b.get_width()/2,v*1.3,f'{v:,.2f}',ha='center',fontsize=14,fontweight='bold')
        ax.set_title(title,fontsize=15);ax.grid(axis='y',alpha=.12);ax.set_axisbelow(True)
        ax.spines[['right','top']].set_visible(False)
    fig.text(.06,.93,'관객 수에 비해 남아 있는 평가 기록의 규모도 다릅니다',fontsize=19,fontweight='bold')
    fig.text(.06,.85,'영화별 평가 수 ÷ 국내 누적 관객 × 100만의 중앙값 · 로그 눈금',fontsize=12)
    fig.text(.06,.13,'국적별 역대 Top200에서 연결된 보수 집합 337편. 서로 다른 사람·시점의 기록이므로 참여율이 아닙니다.',fontsize=10)
    fig.text(.06,.07,'TMDB 개봉일이 MovieLens 마감 이후인 연결 1편 제외. 이 조치만으로 시점이 일치하는 것은 아닙니다.',fontsize=10)
    fig.savefig(OUT/'counts-per-audience.png',dpi=150);plt.close(fig)
    assert before == [pin(p) for p in inputs]
    manifest={'status':'RENDERED_FROM_INDEPENDENTLY_AUDITED_VALUES','script':pin(Path(__file__)),
              'inputs':before,'outputs':[pin(p) for p in sorted(OUT.iterdir()) if p.is_file()],
              'new_network_requests':0,'new_model_training':False}
    (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(OUT)


if __name__=='__main__':
    main()
