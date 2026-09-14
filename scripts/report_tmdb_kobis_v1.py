"""Auditable descriptive TMDB/KOBIS plots and distinct between/within-film SD.

No network, recommender fitting, predictions, or source mutations. Raw MovieLens
ratings are read once to retain only 337 movie-level integer sufficient stats.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT/'docs/research/tmdb-kobis-v1-20260913'
DATA = DOC/'data'
OUT = ROOT/'outputs/recommendation-evidence/tmdb-kobis-v1-20260913'
KOBIS = ROOT/'outputs/recommendation-evidence/kobis-service-final-20260913'
AUDIT = ROOT/'outputs/recommendation-evidence/kobis-service-final-audit-20260913'
ML = ROOT/'outputs/recommendation-evidence/presentation-data-20260913/movie-rating-aggregates.parquet'
ARCHIVE = Path('C:/higher/projects/MM/data/raw/ml-32m.zip')
COLORS = {'K':'#B96C22','F':'#426EA6'}
NAMES = {'ALL':'전체','K':'한국영화','F':'외국영화'}
BANDS = ['300만 미만','300만~500만 미만','500만~1,000만 미만','1,000만 이상']
EXPECTED_PINS = {
    KOBIS/'movie-comparison.parquet':'9969d47da9298b736211caccefc946b131c311b1c11290eff1d3dad08c6f3c68',
    ML:'b552b85ddad2a73a315ce2f176e79f6410807cbb4fa8ee241bfd8acbdca8524e',
    ARCHIVE:'e4a68655d7386b8f95f2f2424b2ff975dfdd15ffd59e0d864a14dca43e99d6ee',
}


def require(ok, message):
    if not ok:
        raise AssertionError(message)


def pin(path):
    path = Path(path)
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream,'sha256').hexdigest()
    return {'path':str(path),'bytes':path.stat().st_size,'sha256':digest}


def dump(path, data):
    Path(path).write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def fingerprint():
    return {'script':pin(__file__),'design':pin(DOC/'DESIGN.md')}


def correlation(x, y, method):
    x=np.asarray(x,float);y=np.asarray(y,float)
    mask=np.isfinite(x)&np.isfinite(y)
    x=x[mask];y=y[mask]
    if len(x)<3 or np.unique(x).size<2 or np.unique(y).size<2:
        return None
    return float(spearmanr(x,y).statistic if method=='spearman' else np.corrcoef(x,y)[0,1])


def distribution(values):
    v=np.asarray(values,float);v=v[np.isfinite(v)]
    out={'movies':int(len(v)),'mean':float(v.mean()) if len(v) else None,
         'sample_sd_ddof1':float(v.std(ddof=1)) if len(v)>=2 else None,
         'minimum':float(v.min()) if len(v) else None,'maximum':float(v.max()) if len(v) else None}
    q=np.quantile(v,[.05,.25,.5,.75,.95],method='linear') if len(v) else [None]*5
    out.update({f'p{p}':float(x) if x is not None else None for p,x in zip([5,25,50,75,95],q)})
    return out


def groups(frame):
    yield 'ALL',frame
    for nation in ['K','F']:
        yield nation,frame[frame.kobis_nation_filter.eq(nation)]


def count_summary(frame, family, group):
    a=frame.kobis_audience_cumulative.to_numpy(float)
    v=frame.tmdb_count.to_numpy(float)
    r=frame.tmdb_mean_10.to_numpy(float)
    n=len(frame)
    out={'family':family,'group':group,'movies':n,
         'audience_sum':int(a.sum()),'tmdb_vote_sum':int(v.sum()),
         'audience_median':float(np.median(a)) if n else None,
         'tmdb_vote_median':float(np.median(v)) if n else None,
         'audience_over_vote_ratio_of_totals':float(a.sum()/v.sum()) if n else None,
         'counts_spearman':correlation(a,v,'spearman'),
         'counts_log10_pearson':correlation(np.log10(a),np.log10(v),'pearson'),
         'tmdb_mean_vs_audience_spearman':correlation(r,a,'spearman'),
         'tmdb_mean_vs_votes_spearman':correlation(r,v,'spearman'),
         'tmdb_mean_vs_log10_audience_pearson':correlation(r,np.log10(a),'pearson'),
         'tmdb_mean_vs_log10_votes_pearson':correlation(r,np.log10(v),'pearson')}
    for name,values in [('audience_per_tmdb_vote',a/v),('tmdb_votes_per_million_audience',v/a*1e6)]:
        qs=np.quantile(values,[.05,.25,.5,.75,.95],method='linear') if n else [None]*5
        out.update({f'{name}_p{p}':float(value) if value is not None else None for p,value in zip([5,25,50,75,95],qs)})
    return out


def read_populations():
    whole=pd.read_parquet(KOBIS/'movie-comparison.parquet')
    require(len(whole)==400 and whole.kobis_movie_code.is_unique,'KOBIS top200 axis')
    matched=whole[whole.match_status.eq('MATCHED')].copy()
    tk=matched[matched.kobis_audience_cumulative.gt(0)&matched.tmdb_count.gt(0)&matched.tmdb_quality_state.eq('VALID')].copy()
    require(len(tk)==371 and tk.service_movie_id.is_unique and tk.tmdb_id.is_unique,'TK371 unique')
    require(tk.kobis_nation_filter.value_counts().to_dict()=={'F':190,'K':181},'TK country counts')
    require(tk.tmdb_mean_10.between(0,10,inclusive='right').all(),'valid TMDB means')
    tk['tmdb_mean_5_rescaled']=tk.tmdb_mean_10/2
    tk['audience_per_tmdb_vote']=tk.kobis_audience_cumulative/tk.tmdb_count
    tk['tmdb_votes_per_million_audience']=tk.tmdb_count/tk.kobis_audience_cumulative*1e6
    tk['audience_band_index']=np.searchsorted([3e6,5e6,1e7],tk.kobis_audience_cumulative,side='right')
    tk['audience_band']=tk.audience_band_index.map(dict(enumerate(BANDS)))
    tk['kobis_open_year']=pd.to_datetime(tk.kobis_open_date,format='%Y-%m-%d',errors='raise').dt.year
    tm_date=pd.to_datetime(tk.tmdb_release_date,format='%Y-%m-%d',errors='raise')
    future=tm_date.gt(pd.Timestamp('2023-10-13'))|tk.ml_year.gt(2023)
    b=tk[tk.ml_count.gt(0)&~future].copy()
    require(len(b)==337 and b.movieId.is_unique,'B337 unique')
    require(b.kobis_nation_filter.value_counts().to_dict()=={'F':174,'K':163},'B countries')
    return whole,tk,b


def raw_ml_within_sd(b):
    ids=b.movieId.to_numpy(np.int64)
    require(ids.min()>0,'valid ML IDs')
    maxid=int(ids.max())
    select=np.zeros(maxid+1,bool);select[ids]=True
    n=np.zeros(maxid+1,np.int64);s=np.zeros_like(n);sq=np.zeros_like(n)
    total_rows=0
    with zipfile.ZipFile(ARCHIVE) as archive:
        for chunk in pd.read_csv(archive.open('ml-32m/ratings.csv'),usecols=['movieId','rating'],chunksize=1_000_000):
            mid=chunk.movieId.to_numpy(np.int64);ratings=chunk.rating.to_numpy(float)
            require(np.isfinite(ratings).all() and ((ratings>=.5)&(ratings<=5)&(ratings*2==np.floor(ratings*2))).all(),'ML half-star grid')
            keep=(mid<=maxid)&(mid>=0)
            keep[keep]=select[mid[keep]]
            small=mid[keep];half=(ratings[keep]*2).astype(np.int64)
            n+=np.bincount(small,minlength=maxid+1)
            s+=np.bincount(small,weights=half,minlength=maxid+1).astype(np.int64)
            sq+=np.bincount(small,weights=half*half,minlength=maxid+1).astype(np.int64)
            total_rows+=len(chunk)
    require(total_rows==32000204,'whole ML row count')
    n=n[ids];s=s[ids];sq=sq[ids]
    require(np.array_equal(n,b.ml_count.to_numpy(np.int64)),'B original count parity')
    require(np.allclose(s/(2*n),b.ml_mean_5,rtol=0,atol=1e-12),'B original mean parity')
    old=pd.read_parquet(ML).set_index('movieId').loc[ids]
    require(np.array_equal(n,old.ml_count.to_numpy()),'independent prior ML count parity')
    require(np.array_equal(s/2,old.ml_rating_sum.to_numpy()),'independent prior ML sum parity')
    numerator=n*sq-s*s
    require(np.all(numerator>=0),'nonnegative exact variance numerator')
    sd=np.full(len(ids),np.nan)
    enough=n>=2
    sd[enough]=np.sqrt(numerator[enough]/(4*n[enough]*(n[enough]-1)))
    result=b.copy()
    result['ml_halfstar_sum']=s
    result['ml_halfstar_square_sum']=sq
    result['ml_within_film_sample_sd_5']=sd
    result['ml_within_film_sd_supported']=enough
    require(int(enough.sum())==334 and int((~enough).sum())==3,'within film SD334')
    return result,{'raw_rows_read':total_rows,'columns_read':['movieId','rating'],
        'movie_ids_aggregated':len(ids),'individual_rows_saved':0,'user_ids_read':False,
        'rating_sum_and_count_parity':'PASS','sample_sd_defined_movies':int(enough.sum()),
        'sample_sd_undefined_single_rating_movies':int((~enough).sum()),
        'variance_formula':'(n*sum((2r)^2)-sum(2r)^2)/(4*n*(n-1)), integer sufficient statistics'}


def build_statistics(whole,tk,b):
    coverage=[]
    for country,allrows in groups(whole):
        subset=tk if country=='ALL' else tk[tk.kobis_nation_filter.eq(country)]
        matched=allrows[allrows.match_status.eq('MATCHED')]
        coverage.append({'group':country,'queried_top200_movies':len(allrows),'title_country_year_matched_movies':len(matched),
            'tk_positive_valid_movies':len(subset),'unmatched_or_invalid_movies':len(allrows)-len(subset),
            'tk_pair_share_of_query_movies':len(subset)/len(allrows),
            'tk_pair_share_of_query_audience':float(subset.kobis_audience_cumulative.sum()/allrows.kobis_audience_cumulative.sum()),
            'query_min_audience':int(allrows.kobis_audience_cumulative.min()),
            'tk_ml_positive_movies':int(subset.ml_count.gt(0).sum()),'tk_without_ml_positive_movies':int((~subset.ml_count.gt(0)).sum())})
    counts=[count_summary(frame,'nation',country) for country,frame in groups(tk)]
    for i,name in enumerate(BANDS):
        for nation in ['K','F']:
            frame=tk[tk.audience_band_index.eq(i)&tk.kobis_nation_filter.eq(nation)]
            counts.append(count_summary(frame,'audience_band',f'{i}_{nation}'))
    for lo,hi in [(2004,2009),(2010,2019),(2020,2023),(2024,2026)]:
        for nation in ['K','F']:
            frame=tk[tk.kobis_open_year.between(lo,hi)&tk.kobis_nation_filter.eq(nation)]
            counts.append(count_summary(frame,'kobis_open_year',f'{lo}_{hi}_{nation}'))
    dispersion=[]
    for population,frame,columns in [('TK371',tk,['tmdb_mean_10','tmdb_mean_5_rescaled']),
        ('B337',b,['ml_mean_5','tmdb_mean_10','tmdb_mean_5_rescaled'])]:
        for nation,subset in groups(frame):
            for column in columns:
                dispersion.append({'population':population,'group':nation,'variable':column,
                    'meaning':'sample SD across film mean ratings; one film one observation',**distribution(subset[column])})
    within=[]
    for nation,subset in groups(b):
        for support,selected in [('ALL_B',subset),('ML_COUNT_GE100',subset[subset.ml_count.ge(100)])]:
            within.append({'group':nation,'support':support,'B_movies':len(selected),
                'unsupported_count1_movies':int(selected.ml_count.lt(2).sum()),
                'meaning':'distribution of each film individual-rating SD; one film one observation',
                **distribution(selected.ml_within_film_sample_sd_5)})
    return pd.DataFrame(coverage),pd.DataFrame(counts),pd.DataFrame(dispersion),pd.DataFrame(within)


def fmt(x,digits=3):
    return '계산 불가' if x is None or pd.isna(x) else f'{x:,.{digits}f}'


def plot_all(tk,b,counts,dispersion):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.ticker import NullFormatter,FuncFormatter
    plt.rcParams.update({'font.family':'Malgun Gothic','axes.unicode_minus':False,'font.size':11,
                         'svg.fonttype':'none','pdf.fonttype':42})
    nationstats=counts[counts.family.eq('nation')].set_index('group')
    footer='KOBIS 국적별 Top200 선택표본 · KOBIS 2026-09-13 조회 / TMDB 2026-09-09 스냅샷 · 시점·참여 집단이 다릅니다.'
    written=[]
    def save(fig,name):
        for ext in ['png','svg']:
            path=DATA/f'{name}.{ext}'
            fig.savefig(path,dpi=165 if ext=='png' else None)
            written.append(path)
        plt.close(fig)
    def plain_log(ax,which,ticks):
        setter=ax.set_xticks if which=='x' else ax.set_yticks
        setter(ticks,[f'{x:,.0f}' for x in ticks])
        (ax.xaxis if which=='x' else ax.yaxis).set_minor_formatter(NullFormatter())
    def clean(ax):
        ax.spines[['top','right']].set_visible(False);ax.grid(alpha=.12);ax.set_axisbelow(True)

    fig,(ax,bx)=plt.subplots(1,2,figsize=(14.5,6.6),gridspec_kw={'width_ratios':[1.2,1]})
    fig.subplots_adjust(left=.07,right=.97,top=.80,bottom=.19,wspace=.30)
    for nation,subset in groups(tk):
        if nation=='ALL':continue
        rho=nationstats.loc[nation,'counts_spearman']
        ax.scatter(subset.tmdb_count,subset.kobis_audience_cumulative,s=28,alpha=.70,color=COLORS[nation],
                   label=f'{NAMES[nation]} n={len(subset)}, ρ={rho:.3f}')
    ax.set_xscale('log');ax.set_yscale('log')
    ax.set_xlim(min(7,float(tk.tmdb_count.min())*.8),max(50000,float(tk.tmdb_count.max())*1.25))
    ax.set_ylim(min(1.8e6,float(tk.kobis_audience_cumulative.min())*.85),max(2.1e7,float(tk.kobis_audience_cumulative.max())*1.2))
    plain_log(ax,'x',[10,100,1000,10000]);plain_log(ax,'y',[2e6,5e6,1e7,2e7])
    ax.set_xlabel('TMDB 투표 수 · 로그 눈금');ax.set_ylabel('KOBIS 통합전산망 누적 관객 · 로그 눈금')
    ax.set_title('같은 영화의 관측량',loc='left',fontsize=15,fontweight='bold');ax.legend(loc='upper left',fontsize=10)
    for i,nation in enumerate(['ALL','K','F']):
        row=nationstats.loc[nation]
        color='#455468' if nation=='ALL' else COLORS[nation]
        bx.plot([row.audience_per_tmdb_vote_p5,row.audience_per_tmdb_vote_p95],[i,i],color=color,lw=2)
        bx.plot([row.audience_per_tmdb_vote_p25,row.audience_per_tmdb_vote_p75],[i,i],color=color,lw=7,solid_capstyle='butt')
        bx.scatter(row.audience_per_tmdb_vote_p50,i,color=color,s=50,zorder=4)
        bx.text(row.audience_per_tmdb_vote_p50,i-.16,fmt(row.audience_per_tmdb_vote_p50,1),ha='center',fontsize=10)
    bx.set_xscale('log');bx.set_yticks([0,1,2],[f'{NAMES[n]} (n={int(nationstats.loc[n,"movies"])})' for n in ['ALL','K','F']])
    bx.set_ylim(2.5,-.6);bx.xaxis.set_major_formatter(FuncFormatter(lambda v,p:f'{v:,.0f}'));bx.xaxis.set_minor_formatter(NullFormatter())
    bx.set_xlabel('국내 누적 관객 ÷ TMDB 투표 수 · 로그 눈금')
    bx.set_title('영화별 비율의 분포',loc='left',fontsize=15,fontweight='bold')
    for a in (ax,bx):clean(a)
    fig.text(.05,.94,'국내 관객과 TMDB 투표는 같은 규모로 쌓이지 않습니다',fontsize=20,fontweight='bold')
    fig.text(.05,.87,'ML 보유 여부와 무관한 TMDB–KOBIS 371편 · 점: 중앙값 / 굵은 선: 25~75% / 가는 선: 5~95%',fontsize=12)
    fig.text(.05,.08,footer,fontsize=10)
    save(fig,'01-counts-and-ratios')

    fig,ax=plt.subplots(figsize=(13.4,6.5));fig.subplots_adjust(left=.09,right=.97,top=.78,bottom=.23)
    positions=np.arange(4);plotted_ratios=[]
    for offset,nation in [(-.18,'K'),(.18,'F')]:
        values=[];numbers=[]
        for i in range(4):
            row=counts[(counts.family=='audience_band')&(counts.group==f'{i}_{nation}')].iloc[0]
            values.append(row.tmdb_votes_per_million_audience_p50);numbers.append(int(row.movies))
        plotted_ratios.extend(float(v) for v in values if pd.notna(v) and v>0)
        bars=ax.bar(positions+offset,values,width=.32,color=COLORS[nation],label=NAMES[nation])
        for bar,v,n in zip(bars,values,numbers):
            if pd.notna(v):ax.text(bar.get_x()+bar.get_width()/2,v*1.14,f'{v:,.1f}\nn={n}',ha='center',va='bottom',fontsize=10)
    require(len(plotted_ratios)>0,'scale chart has positive values')
    ax.set_yscale('log');ax.set_ylim(min(1,min(plotted_ratios)*.7),max(35000,max(plotted_ratios)*1.8))
    plain_log(ax,'y',[1,10,100,1000,10000])
    ax.set_xticks(positions,BANDS);ax.set_xlabel('KOBIS 누적 관객 구간');ax.set_ylabel('영화별 TMDB 투표 수 ÷ 관객 × 100만의 중앙값')
    ax.legend(loc='upper left');clean(ax)
    fig.text(.05,.94,'비슷한 국내 관객 규모에서도 투표 기록량은 다릅니다',fontsize=20,fontweight='bold')
    fig.text(.05,.87,'관객 규모와 국적을 함께 구분 · 비율의 분모인 관객 수에 조건을 건 기술통계이며 원인 분석이 아닙니다',fontsize=12)
    fig.text(.05,.11,'선별된 Top200의 낮은 끝도 한국 247만·외국 214만 관객: 저관객 영화 전체의 분포는 알 수 없습니다.',fontsize=10)
    fig.text(.05,.06,footer,fontsize=9)
    save(fig,'02-scale-imbalance')

    fig,axes=plt.subplots(1,2,figsize=(14,6.8));fig.subplots_adjust(left=.07,right=.97,top=.80,bottom=.23,wspace=.24)
    specs=[('kobis_audience_cumulative','국내 누적 관객','tmdb_mean_vs_audience_spearman',[2e6,5e6,1e7,2e7]),
           ('tmdb_count','TMDB 투표 수','tmdb_mean_vs_votes_spearman',[10,100,1000,10000])]
    for ax,(column,label,stat,ticks) in zip(axes,specs):
        for nation,subset in groups(tk):
            if nation=='ALL':continue
            rho=nationstats.loc[nation,stat]
            ax.scatter(subset[column],subset.tmdb_mean_10,s=28,alpha=.70,color=COLORS[nation],
                       label=f'{NAMES[nation]} n={len(subset)}, ρ={rho:.3f}')
        ax.set_xscale('log');plain_log(ax,'x',ticks);ax.set_ylim(0,10);ax.set_yticks([0,2,4,6,8,10])
        ax.set_xlabel(label+' · 로그 눈금');ax.set_ylabel('TMDB 영화별 평균평점 / 10점')
        ax.set_title('평균평점과 '+label,loc='left',fontsize=15,fontweight='bold');ax.legend(loc='lower right',fontsize=10);clean(ax)
    fig.text(.05,.94,'평가의 개수와 평균평점의 관계를 따로 봅니다',fontsize=20,fontweight='bold')
    fig.text(.05,.87,'TMDB의 영화 평균: 개인 별점 원본이나 만족도 인과효과가 아닙니다 · ρ는 Spearman 순위 상관',fontsize=12)
    fig.text(.05,.11,footer,fontsize=10)
    fig.text(.05,.06,'극장 관객이 많아 높은 평점이 생겼는지, 높은 만족도로 관객이 늘었는지는 이 단면 자료로 분리하지 못합니다.',fontsize=10)
    save(fig,'03-rating-relations')

    fig,(ax,bx)=plt.subplots(1,2,figsize=(14,6.8));fig.subplots_adjust(left=.07,right=.97,top=.79,bottom=.25,wspace=.26)
    selected=dispersion[dispersion.population.eq('B337')]
    xpos=np.arange(3);plotted_sd=[]
    for offset,col,label,color in [(-.18,'ml_mean_5','MovieLens 평균','#426EA6'),(.18,'tmdb_mean_5_rescaled','TMDB 평균 ÷ 2','#338B82')]:
        vals=[selected[(selected.group==g)&(selected.variable==col)].iloc[0].sample_sd_ddof1 for g in ['ALL','K','F']]
        plotted_sd.extend(vals)
        bars=ax.bar(xpos+offset,vals,width=.32,color=color,label=label)
        for bar,value in zip(bars,vals):ax.text(bar.get_x()+bar.get_width()/2,value+.025,f'{value:.3f}',ha='center',fontsize=10)
    ax.set_xticks(xpos,['같은337편','한국163편','외국174편']);ax.set_ylim(0,max(.2,max(plotted_sd)*1.25))
    ax.set_ylabel('영화 평균 사이의 표본 SD · 5점 눈금');ax.legend(loc='upper right',fontsize=10)
    ax.set_title('① 영화마다 평균이 얼마나 다른가',loc='left',fontsize=15,fontweight='bold');clean(ax)
    samples=[b.loc[b.kobis_nation_filter.eq(n)&b.ml_within_film_sd_supported,'ml_within_film_sample_sd_5'].to_numpy() for n in ['K','F']]
    bp=bx.boxplot(samples,tick_labels=[f'한국영화 n={len(samples[0])}',f'외국영화 n={len(samples[1])}'],patch_artist=True,showmeans=False)
    for patch,nation in zip(bp['boxes'],['K','F']):patch.set_facecolor(COLORS[nation]);patch.set_alpha(.65)
    bx.set_ylim(0,max(3.3,max(float(x.max()) for x in samples)*1.15));bx.set_ylabel('영화 안 개인별점의 표본 SD · 5점 눈금')
    bx.set_title('② 같은 영화에 대한 별점이 얼마나 다른가',loc='left',fontsize=15,fontweight='bold');clean(bx)
    fig.text(.05,.94,'표준편차는 어떤 값들의 차이를 재는지 구분해야 합니다',fontsize=20,fontweight='bold')
    fig.text(.05,.87,'① 한 영화 한 평균의 분포 / ② 한 영화 안의 ML 개인별점 SD를 영화별로 요약 · 모두 ddof=1',fontsize=12)
    fig.text(.05,.13,'B337은 기존의 보수 연결 집합. 개인별점이 1개인 3편은 ② SD를 계산할 수 없어 제외했습니다.',fontsize=10)
    fig.text(.05,.08,'TMDB 개인별 투표 분산과 KOBIS 만족도 분산은 자료가 없습니다. TMDB ÷ 2는 눈금 변환이며 집단 보정이 아닙니다.',fontsize=10)
    save(fig,'04-rating-dispersion')
    return written


def write_report(tk,b,coverage,counts,dispersion,within):
    ns=counts[counts.family.eq('nation')].set_index('group')
    meanlines=[]
    for pop,var,label in [('TK371','tmdb_mean_10','TK371 TMDB / 10점'),('B337','ml_mean_5','B337 ML / 5점'),('B337','tmdb_mean_5_rescaled','B337 TMDB ÷ 2 / 5점')]:
        for nation in ['ALL','K','F']:
            r=dispersion[(dispersion.population==pop)&(dispersion.variable==var)&(dispersion.group==nation)].iloc[0]
            meanlines.append(f'| {label} | {NAMES[nation]} | {r.movies:,} | {r["mean"]:.3f} | **{r.sample_sd_ddof1:.3f}** | {r.p5:.3f}~{r.p95:.3f} |')
    countlines=[]
    for nation in ['ALL','K','F']:
        r=ns.loc[nation]
        countlines.append(f'| {NAMES[nation]} | {int(r.movies):,} | {fmt(r.counts_spearman,4)} | {fmt(r.audience_per_tmdb_vote_p50,1)} | {fmt(r.audience_per_tmdb_vote_p5,1)}~{fmt(r.audience_per_tmdb_vote_p95,1)} | {fmt(r.tmdb_votes_per_million_audience_p50,2)} |')
    relationlines=[]
    for nation in ['ALL','K','F']:
        r=ns.loc[nation]
        relationlines.append(f'| {NAMES[nation]} | {int(r.movies)} | {fmt(r.tmdb_mean_vs_audience_spearman,4)} | {fmt(r.tmdb_mean_vs_votes_spearman,4)} |')
    withinlines=[]
    for nation in ['ALL','K','F']:
        r=within[(within.group==nation)&(within.support=='ALL_B')].iloc[0]
        withinlines.append(f'| {NAMES[nation]} | {int(r.B_movies)} | {int(r.movies)} | {int(r.unsupported_count1_movies)} | {fmt(r["mean"])} | {fmt(r.p50)} | {fmt(r.p5)}~{fmt(r.p95)} |')
    text=f'''# TMDB와 KOBIS: 관측량·평균평점·표준편차 비교

상태: **DRAFT · 집계 생성 완료, 독립 결과 검토 대기** · 2026-09-13.

**TMDB 투표 수와 국내 극장 관객은 같은 관측량이 아니다. 평균평점의 높낮이와 그 분포도 별도로 보아야 한다.**
이 문서는 보유 자료의 관계를 분석한 기술통계이며 GBT나 추천 정책의 성능 비교가 아니다.

## 먼저 구분할 세 가지

| 질문 | 계산에 쓰는 값 | 알 수 있는 범위 |
| --- | --- | --- |
| 국내에서 많이 본 영화에 TMDB 투표도 많은가 | 같은 영화의 KOBIS 관객·TMDB 투표 수 | 선택된 영화에서의 관측량 관계 |
| 영화마다 평균평점이 얼마나 다른가 | 영화별 평균평점들의 표준편차 | **영화 간 평균의 차이** |
| 같은 영화를 평가한 사람들의 별점이 얼마나 다른가 | 해당 영화의 개별별점 표준편차 | **영화 안 개인별점의 차이**; 이번 자료에서는 MovieLens만 계산 가능 |

평균평점의 표준편차를 개인들의 의견 차이, 평균의 표준오차 또는 평점의 신뢰구간으로 읽지 않는다.
TMDB 평균·투표 수만으로 개별 투표의 분산은 복원할 수 없고, KOBIS에는 만족도 별점이 없다.

## 1. 표본과 시점

KOBIS 국적별 Top200에서 총400편을 조회했고, 제목·국가·개봉연도 조건으로 고유 연결한 **371편(한국181·외국190)** 모두 TMDB–KOBIS 양수 유효 관측을 가진다.
TMDB–KOBIS 분석은 이371편을 사용하며 **MovieLens가 없는33편을 빼지 않는다**.
MovieLens와 평균·개별별점 SD를 직접 비교할 때만 기존 **B337편(한국163·외국174)**으로 제한한다.
위키드의 미래 개봉 연결1편은 B에서 제외되지만, ML을 사용하지 않는 TK371에는 남는다.

| 출처 | 시점과 단위 | 제한 |
| --- | --- | --- |
| KOBIS | 2026-09-13 현재 조회한 통합전산망 누적 관객·매출 | 2004년 이후 상영관 연동률·재개봉·보정 영향; 전체 생애 관객을 완전 복원한 공식 확정값 아님 |
| 보유 TMDB | 2026-09-09 서비스 스냅샷의 누적 투표·평균 | 영화별 실제 조회 시각 미확보; 이번 비교는 TMDB 전체 영화가 아님 |
| MovieLens32M | 1995-01-09~2023-10-13의 고정 개인별점 | 최신 KOBIS/TMDB와 시점·참여 집단이 다름 |

두 국적 Top200의 최하위 관객도 한국 **2,472,174명**, 외국 **2,141,199명**이다.
따라서 저관객·OTT·미개봉·Top200 밖 영화가 어떻게 분포하는지는 이 표본으로 알 수 없다.
29편의 연결 실패·모호함은 영화가 없거나 관객이0이라는 뜻이 아니며, 동명·연도 차이를 임의 매칭하지 않았다.
[연결률 원수치](data/coverage.csv), [371편 원수치](data/tmdb-kobis-pairs.csv).

## 2. 관객 수와 투표 수가 일정 비율로 연결되는가

![관객과 투표 수 및 영화별 비율 분포](data/01-counts-and-ratios.png)

| 집합 | 영화 수 | 관객–투표 Spearman | 관객/TMDB 투표 중앙값 | 같은 비율의5~95백분위 | 국내 관객100만당 TMDB 투표 중앙값 |
| --- | ---: | ---: | ---: | ---: | ---: |
{chr(10).join(countlines)}

비율은 영화마다 계산한 뒤 요약했다; `전체 관객 합/전체 투표 합`은 별도 CSV 열이며 위 중앙값과 다르다.
관객과 투표는 서로 다른 사람·이벤트·기간의 기록이므로 비율을 평가 참여율로 바꾸지 않는다.
한국·외국을 합친 상관과 각 집단의 상관을 함께 읽어야 하며, 상관이 높아도 영화마다 일정한 배수라는 뜻은 아니다.

## 3. 관객 규모가 비슷해도 같은가

![관객 규모별 관측량 불균형](data/02-scale-imbalance.png)

국내 관객을300만·500만·1,000만 경계로 나누고 각 구간 안에서 한국·외국 영화의 관객100만당 TMDB 투표 수를 요약했다.
이 분석은 영화 규모별 불균형을 보는 것이며, 비율의 분모인 관객 수에 조건을 건 비교이므로 원인을 분리한 인과효과가 아니다.
구간별 표본 수가 다르고 원래부터 Top200 표본이라 작은 영화에 일반화할 수 없다.
개봉 시기별 보조 통계도 [전체 층별 수치](data/count-and-rating-relations.csv)에 남겼다.

## 4. 평균평점은 관객·투표 수와 어떤 관계인가

![TMDB 평균평점과 관측량](data/03-rating-relations.png)

| 집합 | 영화 수 | TMDB 평균–관객 Spearman | TMDB 평균–투표 수 Spearman |
| --- | ---: | ---: | ---: |
{chr(10).join(relationlines)}

위 표는 별점의 평균과 기록량의 관계다; 높은 관객 때문에 평점이 높아졌는지, 높은 만족도가 관객을 늘렸는지는 알 수 없다.
고정된 현재 스냅샷이므로 개봉 이후 시간·해외 접근성·시청 경로·평가자 자기선택의 영향도 분리되지 않는다.
이 수치로 국내 관객 수를 개인 예상 별점에 더하거나 한국영화에 같은 보너스를 주는 공식은 만들지 않는다.

## 5. 영화별 평균평점의 표준편차

각 영화의 평균 하나를 관측치 하나로 보고 **영화 동일 가중·표본 SD(ddof=1)**를 계산했다.
많은 평가를 받은 영화에 더 큰 가중치를 주지 않았으며, 이는 한 영화 안에서 사람들이 얼마나 의견이 갈렸는지를 뜻하지 않는다.

| 자료·눈금 | 집합 | 영화 수 | 영화 평균들의 평균 | 영화 평균들의 SD | 평균평점5~95백분위 |
| --- | --- | ---: | ---: | ---: | ---: |
{chr(10).join(meanlines)}

TK371 TMDB표와 B337표는 모집단이 다르므로 그대로 우열 비교하지 않는다.
동일 영화 비교는 B337의 ML5점과 TMDB÷2를 사용한다; ÷2는 단위 표시를 맞춘 것일 뿐 집단의 평점 성향을 보정한 것은 아니다.
영화별 평균이므로 실제 ML입력의0.5단위에 다시 반올림하지 않았다.
[분포 원수치](data/between-film-rating-distributions.csv).

## 6. 같은 영화 안의 개인별점 표준편차

![영화 간 평균 차이와 영화 안 개인별점 차이](data/04-rating-dispersion.png)

MovieLens 원본을 한 번 읽어 B337 각 영화의 평가수·별점합·제곱합을 집계했다.
기존 평가수·평균과 일치하는지 확인한 뒤 `sqrt((Σr²−(Σr)²/n)/(n−1))`를 계산했다.
평가가1개인3편에는 표본 SD가 정의되지 않아 **0으로 넣지 않고 계산 불가로 남겼다**.

| 집합 | B 영화 | SD 가능한 영화 | 평가1개 | 영화별 SD의 평균 | 영화별 SD의 중앙값 | 영화별 SD의5~95백분위 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
{chr(10).join(withinlines)}

이 표도 영화별 SD 하나에 같은 가중치를 줬다; 모든 영화의 평가를 한데 합친 SD와 다르다.
평가가 적은 영화의 SD는 흔들릴 수 있어 ML평가100개 이상만의 보조 요약도 [SD 원수치](data/within-film-ml-sd-distributions.csv)에 포함했다.
TMDB의 개인별 투표 분산과 KOBIS 관객의 만족도 분산은 **자료 없음**이며, MovieLens SD로 대신 채우거나 평균·투표수에서 추정하지 않았다.
[동일 영화 B337의 평균·SD](data/movielens-same-film-rating-sd.csv).

## 7. 이 분석으로 말할 수 있는 것

- 국내 극장 흥행, TMDB의 평가 참여량, 영화 평균평점은 각각 다른 신호이므로 출처·관측량·결측 상태를 분리해야 한다.
- 영화별 평균의 분포와 개인별점 분포는 서로 다른 질문에 답한다.
- 낮은 기록량·자료 없음이 낮은 선호도를 뜻하지 않고, 한국/외국·규모·시점에 따라 관측 구조가 달라진다.
- 현재 신호를 추천 학습에 결합할 가치는 별도의 고정 조건 실험으로 판단해야 한다; 이번 표는 모델 성능이나 GBT 우위를 증명하지 않는다.

## 재현과 검증

실행 전 [설계](DESIGN.md)와 코드의 독립 검토를 통과한 버전만 실행한다.
통계·반별점 정수 충분통계·입력/출력 해시는 [실행 manifest](../../../outputs/recommendation-evidence/tmdb-kobis-v1-20260913/manifest.json)에 보존한다.
사용자 ID나 개별 평점 행은 산출물에 저장하지 않았고 새 외부 조회·학습·예측은 없다.
PNG와 같은 이름의 SVG도 `data/`에 있어 발표에 벡터로 사용할 수 있다.
'''
    (DOC/'REPORT.md').write_text(text,encoding='utf-8')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--execute',action='store_true');parser.add_argument('--fingerprint',action='store_true')
    args=parser.parse_args()
    if args.fingerprint:
        print(json.dumps(fingerprint(),ensure_ascii=False,indent=2));return
    require(args.execute,'explicit --execute required')
    review=json.loads((DOC/'execution-review.json').read_text('utf-8'))
    require(review['status']=='PASS' and review['fingerprint']==fingerprint(),'independent reviewed exact script/design required')
    require(not DATA.exists() and not OUT.exists() and not (DOC/'REPORT.md').exists(),'preserve prior outputs')
    started=time.perf_counter()
    sources=[*EXPECTED_PINS,KOBIS/'manifest.json',AUDIT/'result-review.json',AUDIT/'sensitivity-b.json',DOC/'execution-review.json']
    before=[pin(p) for p in sources]
    for p,expected in EXPECTED_PINS.items():require(pin(p)['sha256']==expected,'source pin '+str(p))
    old=json.loads((KOBIS/'manifest.json').read_text('utf-8'))
    for entry in old['outputs']:
        require(pin(entry['path'])==entry,'original collection output preserved')
    whole,tk,b=read_populations()
    b,rawscan=raw_ml_within_sd(b)
    coverage,counts,dispersion,within=build_statistics(whole,tk,b)
    DATA.mkdir(parents=True);OUT.mkdir(parents=True)
    basecols=['kobis_movie_code','kobis_title','kobis_nation_filter','kobis_open_date','kobis_audience_cumulative','kobis_sales_krw_cumulative',
        'tmdb_id','tmdb_title','tmdb_release_date','tmdb_count','tmdb_mean_10','tmdb_mean_5_rescaled','service_movie_id','movieId','ml_year','ml_count','ml_mean_5',
        'audience_per_tmdb_vote','tmdb_votes_per_million_audience','audience_band','kobis_open_year']
    tk[basecols].to_csv(DATA/'tmdb-kobis-pairs.csv',index=False)
    b[basecols+['ml_halfstar_sum','ml_halfstar_square_sum','ml_within_film_sample_sd_5','ml_within_film_sd_supported']].to_csv(DATA/'movielens-same-film-rating-sd.csv',index=False)
    for name,frame in [('coverage',coverage),('count-and-rating-relations',counts),('between-film-rating-distributions',dispersion),('within-film-ml-sd-distributions',within)]:
        frame.to_csv(DATA/f'{name}.csv',index=False)
    figures=plot_all(tk,b,counts,dispersion)
    write_report(tk,b,coverage,counts,dispersion,within)
    dump(OUT/'raw-ml-sd-scan.json',rawscan)
    summary={'status':'GENERATED_PENDING_INDEPENDENT_RESULT_REVIEW','TK_movies':len(tk),'TK_by_nation':tk.kobis_nation_filter.value_counts().to_dict(),
        'TK_without_ML_positive':int((~tk.ml_count.gt(0)).sum()),'B_movies':len(b),'B_by_nation':b.kobis_nation_filter.value_counts().to_dict(),
        'within_ML_SD_supported_movies':int(b.ml_within_film_sd_supported.sum()),'TMDB_individual_rating_SD':'UNAVAILABLE','KOBIS_satisfaction_SD':'UNAVAILABLE',
        'between_film_SD_definition':'sample SD(ddof1) of film means with equal film weight','within_film_SD_definition':'sample SD(ddof1) of individual ML ratings inside each film',
        'new_network_requests':0,'new_model_fits':0,'elapsed_seconds':time.perf_counter()-started}
    dump(OUT/'summary.json',summary)
    require(before==[pin(p) for p in sources],'sources unchanged during run')
    dump(OUT/'manifest.json',{'status':summary['status'],'fingerprint':fingerprint(),'inputs':before,
        'outputs':[pin(p) for p in sorted(DATA.iterdir())]+[pin(DOC/'REPORT.md'),pin(OUT/'summary.json'),pin(OUT/'raw-ml-sd-scan.json')],
        'analysis_scope':'Descriptive existing-data analysis; no recommender superiority or population causal claim'})
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
