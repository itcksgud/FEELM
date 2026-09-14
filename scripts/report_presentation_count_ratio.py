"""Compare paired MovieLens/TMDB rating counts; descriptive, no recommender fit."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parents[1]
ML = ROOT / 'outputs/recommendation-evidence/presentation-data-20260913/movie-rating-aggregates.parquet'
CAT = ROOT / '.codex-tmp/fixed-k8-discovery-v2-20260913/outputs/fixed-k8-discovery-v2/feelm-discovery-v2-r2/prepare/catalog.parquet'
BASE_MANIFEST = ROOT / 'outputs/recommendation-evidence/presentation-data-20260913/manifest.json'
OUT = ROOT / 'outputs/recommendation-evidence/presentation-count-ratio-20260913'
DOC = ROOT / 'docs/recommendation/experiments/hybrid345/data-context/count-ratio'


def pin(path):
    with path.open('rb') as f:
        sha = hashlib.file_digest(f, 'sha256').hexdigest()
    return {'path': str(path), 'bytes': path.stat().st_size, 'sha256': sha}


def dump(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def stats(frame, family, group, reference):
    ml = frame.ml_count.to_numpy(float)
    tm = frame.tmdb_count.to_numpy(float)
    r = tm / ml
    assert len(r) > 2 and np.isfinite(r).all() and (r > 0).all()
    q = np.quantile(r, [.05, .25, .5, .75, .95], method='linear')
    return {'family': family, 'group': group, 'movies': len(r),
            'ml_rating_records': int(ml.sum()), 'tmdb_vote_sum': int(tm.sum()),
            'ratio_of_count_totals': float(tm.sum()/ml.sum()),
            **{f'ratio_p{p}': float(v) for p,v in zip([5,25,50,75,95], q)},
            'p95_div_p5': float(q[4]/q[0]),
            'spearman_counts': float(spearmanr(ml, tm).statistic),
            'pearson_log10_counts': float(np.corrcoef(np.log10(ml), np.log10(tm))[0,1]),
            'global_reference_ratio': float(reference),
            'within_factor2_of_global_median': float(((r >= reference/2) & (r <= reference*2)).mean()),
            'within_factor4_of_global_median': float(((r >= reference/4) & (r <= reference*4)).mean()),
            'within_factor2_of_own_median': float(((r >= q[2]/2) & (r <= q[2]*2)).mean()),
            'tmdb_more_records_fraction': float((tm > ml).mean()),
            'ml_more_records_fraction': float((ml > tm).mean()),
            'equal_count_fraction': float((ml == tm).mean())}


def main():
    assert not OUT.exists() and not DOC.exists(), 'Preserve previous outputs; version reruns'
    sources = [ML, CAT, BASE_MANIFEST]
    before = [pin(p) for p in sources]
    manifest = json.loads(BASE_MANIFEST.read_text(encoding='utf-8'))
    source_agg = next(x for x in manifest['outputs'] if Path(x['path']).name == ML.name)
    assert source_agg == before[0], 'Verified aggregate pin drift'
    assert before[1]['sha256'] == '8bda180eb6530188a6ebf08da599d4d68124247a878ca0911f0d3f38512415cd'
    ml = pd.read_parquet(ML, columns=['movieId','title','ml_year','ml_count'])
    cat = pd.read_parquet(CAT, columns=['tmdb_id','title','movielens_movie_id','mapping_status',
        'production_country_codes','quality_state','raw_vote_count_number'])
    assert len(ml) == 87585 and ml.movieId.is_unique
    cat = cat[cat.mapping_status.eq('MATCHED')].copy()
    assert len(cat) == 86181 and cat.movielens_movie_id.is_unique and cat.tmdb_id.is_unique
    cat['movieId'] = cat.movielens_movie_id.astype(np.int64)
    cat = cat.rename(columns={'title':'tmdb_title','raw_vote_count_number':'tmdb_count'})
    frame = ml.merge(cat, on='movieId', validate='one_to_one')
    frame = frame[frame.ml_count.gt(0) & frame.quality_state.eq('VALID')].copy()
    assert len(frame) == 82181 and frame.tmdb_count.gt(0).all()
    frame['count_ratio_tmdb_over_ml'] = frame.tmdb_count / frame.ml_count
    frame['country_group'] = frame.production_country_codes.map(
        lambda xs: 'KR_PRODUCTION' if 'KR' in xs else ('NON_KR_KNOWN' if len(xs) else 'COUNTRY_MISSING'))
    reference = float(frame.count_ratio_tmdb_over_ml.median())
    groups = [('scope', 'ALL_PAIRED', np.ones(len(frame), dtype=bool)),
              ('scope', 'BOTH_COUNTS_GE100', (frame.ml_count >= 100) & (frame.tmdb_count >= 100)),
              ('scope', 'ML_TITLE_2020_2023', frame.ml_year.between(2020,2023))]
    for low, high in [(1,9),(10,99),(100,999),(1000,np.inf)]:
        name = f'ML_{low}_{int(high)}' if np.isfinite(high) else 'ML_1000_PLUS'
        groups.append(('ml_support', name, frame.ml_count.between(low,high)))
    epochs = [('BEFORE_1980',frame.ml_year.lt(1980)),('1980s',frame.ml_year.between(1980,1989)),
              ('1990s',frame.ml_year.between(1990,1999)),('2000s',frame.ml_year.between(2000,2009)),
              ('2010s',frame.ml_year.between(2010,2019)),('2020_2023',frame.ml_year.between(2020,2023)),
              ('YEAR_UNKNOWN',frame.ml_year.isna())]
    groups.extend(('ml_title_epoch', name, mask) for name,mask in epochs)
    for country in ['KR_PRODUCTION','NON_KR_KNOWN','COUNTRY_MISSING']:
        groups.append(('country',country,frame.country_group.eq(country)))
    groups.extend(('recent_year',str(y),frame.ml_year.eq(y)) for y in range(2020,2024))
    records = [stats(frame.loc[mask],family,name,reference) for family,name,mask in groups]
    table = pd.DataFrame(records)
    for family in ['ml_support','ml_title_epoch','country']:
        assert int(table[table.family.eq(family)].movies.sum()) == len(frame), 'Exhaustive disjoint descriptive groups'
    data_columns = ['movieId','tmdb_id','tmdb_title','ml_year','country_group','ml_count','tmdb_count','count_ratio_tmdb_over_ml']
    examples = frame[frame.movieId.isin([318,356,296,2571,593,27773,202439,162082,275167,276885,288001])]
    examples = examples.sort_values(['ml_count','movieId'],ascending=[False,True])
    OUT.mkdir(parents=True)
    DOC.mkdir(parents=True)
    frame[data_columns].to_parquet(OUT/'paired-movie-counts.parquet',index=False)
    table.to_csv(DOC/'count-ratio-summary.csv',index=False)
    examples[data_columns].to_csv(DOC/'count-ratio-examples.csv',index=False)
    summary = {'status':'DESCRIPTIVE_PAIRED_COUNT_COMPARISON',
        'definition':'For each positively rated, unambiguously matched film, TMDB vote_count / MovieLens rating count',
        'pair_movies':len(frame), 'global_median_reference':reference,
        'global_reference_factor2_interval':[reference/2,reference*2],
        'main':records[0], 'both_counts_ge100':records[1], 'recent_ml_title_2020_2023':records[2],
        'no_null_counts_imputed':True, 'new_model_fit_or_selection':False,
        'rating_counts_are_cumulative_at_different_snapshots':True,
        'factor2_is_descriptive_tolerance_not_a_prevalidated_equivalence_test':True,
        'rank_correlation_does_not_imply_constant_ratio':True,
        'grouping_by_ml_count_conditions_on_the_ratio_denominator':True,
        'example_selection':'Predeclared MovieLens IDs: prior popular top5 + Oldboy, Parasite, Train to Busan, Decision to Leave, Roundup 2/3; illustrative, not representative'}
    dump(DOC/'summary.json',summary)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family':'Malgun Gothic','axes.unicode_minus':False,'font.size':11})
    fig,(ax,bx)=plt.subplots(1,2,figsize=(13.8,6.3),layout='constrained',gridspec_kw={'width_ratios':[1.1,1]})
    lx=np.log10(frame.ml_count.to_numpy())
    ly=np.log10(frame.tmdb_count.to_numpy())
    hb=ax.hexbin(lx,ly,gridsize=65,mincnt=1,bins='log',cmap='Blues')
    left,right=0,max(lx.max(),ly.max())+.15
    line=np.array([left,right])
    ax.plot(line,line+np.log10(reference),color='#D17C24',lw=1.8,label=f'일정 배수 기준선: TMDB = ML × {reference:.2f}')
    ax.set_xlim(left,right);ax.set_ylim(left,right)
    ticks=np.arange(0,int(right)+1)
    ax.set_xticks(ticks,[f'{10**x:,}' for x in ticks]); ax.set_yticks(ticks,[f'{10**x:,}' for x in ticks])
    ax.set_xlabel('MovieLens 평가 수 · 로그 눈금');ax.set_ylabel('TMDB 투표 수 · 로그 눈금')
    ax.set_title(f'같은 영화 {len(frame):,}편의 평가 수',loc='left',fontsize=15,fontweight='bold')
    ax.legend(loc='upper left',fontsize=9)
    fig.colorbar(hb,ax=ax,label='칸 안의 영화 수',shrink=.75)
    plotrows=table[(table.family.eq('scope') & table.group.isin(['ALL_PAIRED','BOTH_COUNTS_GE100'])) |
        (table.family.eq('ml_title_epoch') & ~table.group.eq('YEAR_UNKNOWN'))].copy()
    labels={'ALL_PAIRED':'전체 같은 영화','BOTH_COUNTS_GE100':'양쪽 평가 100개 이상','BEFORE_1980':'1980년 이전','1980s':'1980년대','1990s':'1990년대','2000s':'2000년대','2010s':'2010년대','2020_2023':'2020~2023년'}
    for idx,row in enumerate(plotrows.itertuples()):
        bx.plot([row.ratio_p5,row.ratio_p95],[idx,idx],color='#8497AD',lw=2)
        bx.plot([row.ratio_p25,row.ratio_p75],[idx,idx],color='#375C83',lw=6,solid_capstyle='butt')
        bx.scatter([row.ratio_p50],[idx],s=40,color='#D17C24',zorder=3)
    bx.set_yticks(range(len(plotrows)),[f'{labels[x.group]} (n={x.movies:,})' for x in plotrows.itertuples()])
    bx.set_xscale('log');bx.invert_yaxis()
    bx.axvline(reference,color='#D17C24',ls='--',lw=1)
    bx.set_title('TMDB ÷ MovieLens 평가 수의 분포',loc='left',fontsize=15,fontweight='bold')
    bx.set_xlabel('배수 · 로그 눈금 / 점: 중앙값, 굵은 선: 25~75%, 가는 선: 5~95%')
    bx.grid(axis='x',alpha=.15)
    for a in (ax,bx): a.spines[['right','top']].set_visible(False)
    fig.supxlabel('관측 시점과 참여 집단이 다른 누적 평가 수. 높은 순위 상관이 일정한 배수를 뜻하지는 않습니다.',fontsize=10)
    fig.savefig(DOC/'count-ratio-comparison.png',dpi=170)
    plt.close(fig)
    assert before == [pin(p) for p in sources], 'Source drift during descriptive comparison'
    dump(OUT/'manifest.json',{'status':'GENERATED_PENDING_INDEPENDENT_REVIEW','script':pin(Path(__file__)),
        'inputs':before,'outputs':[pin(p) for p in sorted(DOC.iterdir())]+[pin(OUT/'paired-movie-counts.parquet')]})
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__ == '__main__': main()
