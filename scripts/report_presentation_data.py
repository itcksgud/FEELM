"""Descriptive presentation tables only; no model input, fitting or selection.

MovieLens popularity = full-dataset rating record count.
Korean film = production country KR (including co-productions).
Compare rating means only on the same unambiguously matched, rated films.
"""
from __future__ import annotations

import hashlib
import json
import re
import tarfile
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/recommendation-evidence/presentation-data-20260913'
DOC = ROOT / 'docs/recommendation/experiments/hybrid345/data-context'
ML = Path('C:/higher/projects/MM/data/raw/ml-32m.zip')
CAT = ROOT / '.codex-tmp/fixed-k8-discovery-v2-20260913/outputs/fixed-k8-discovery-v2/feelm-discovery-v2-r2/prepare/catalog.parquet'
COUNTS = ROOT / 'outputs/recommendation-evidence/movielens-release-distribution/movie-counts.parquet'
SNAP = ROOT / '.codex-tmp/rec038-korean-movie-guide-20260911/outputs/service-catalog-snapshot-20260909'
MANIFEST_TAR = SNAP / '00_hadoop-manifests_postgresql-5row-validation_20260908.tar.gz'


def pin(path):
    return {'path': str(path), 'bytes': path.stat().st_size,
            'sha256': hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()}


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def main():
    assert not OUT.exists(), 'Preserve previous results; use a new version for rerun.'
    assert not DOC.exists(), 'Preserve previous presentation data context.'
    sources = [ML, CAT, COUNTS, MANIFEST_TAR, SNAP / 'README.md']
    pins = [pin(p) for p in sources]
    assert pins[0]['sha256'] == 'e4a68655d7386b8f95f2f2424b2ff975dfdd15ffd59e0d864a14dca43e99d6ee'
    with zipfile.ZipFile(ML) as archive:
        movies = pd.read_csv(archive.open('ml-32m/movies.csv'))
        assert len(movies) == 87585 and movies.movieId.is_unique
        maxid = int(movies.movieId.max())
        counts = np.zeros(maxid + 1, dtype=np.int64)
        sums = np.zeros(maxid + 1, dtype=np.float64)
        tmin, tmax = 2**63-1, 0
        for chunk in pd.read_csv(archive.open('ml-32m/ratings.csv'),
                                 usecols=['movieId', 'rating', 'timestamp'], chunksize=1_000_000):
            ids = chunk.movieId.to_numpy(np.int64)
            values = chunk.rating.to_numpy(np.float64)
            assert ((values >= .5) & (values <= 5) & (values * 2 == np.floor(values * 2))).all()
            assert ids.min() > 0 and ids.max() <= maxid
            counts += np.bincount(ids, minlength=maxid + 1)
            sums += np.bincount(ids, weights=values, minlength=maxid + 1)
            tmin = min(tmin, int(chunk.timestamp.min()))
            tmax = max(tmax, int(chunk.timestamp.max()))
    assert counts.sum() == 32000204
    movies['ml_count'] = counts[movies.movieId]
    movies['ml_rating_sum'] = sums[movies.movieId]
    movies['ml_mean_5'] = movies.ml_rating_sum / movies.ml_count.replace(0, np.nan)
    movies['ml_year'] = pd.to_numeric(movies.title.str.extract(r'\((\d{4})\)\s*$')[0], errors='coerce')
    old = pd.read_parquet(COUNTS)
    check = movies.merge(old[['movieId', 'rating_records']], validate='one_to_one')
    assert len(check) == 87585 and (check.ml_count == check.rating_records).all()
    assert int(movies.ml_count.sum()) == int(counts.sum()), 'No unknown movie IDs'
    columns = ['service_movie_id', 'tmdb_id', 'title', 'release_date', 'status',
               'movielens_movie_id', 'mapping_status', 'production_country_codes',
               'original_language', 'raw_vote_average_number', 'raw_vote_count_number',
               'quality_state']
    cat = pd.read_parquet(CAT, columns=columns).rename(columns={
        'title': 'tmdb_title', 'raw_vote_average_number': 'tmdb_mean_10',
        'raw_vote_count_number': 'tmdb_count'})
    assert len(cat) == 237817 and cat.tmdb_id.is_unique and cat.service_movie_id.is_unique
    cat['tmdb_year'] = pd.to_numeric(cat.release_date.str.extract(r'^(\d{4})-')[0], errors='coerce')
    cat['is_kr_production'] = cat.production_country_codes.map(lambda x: 'KR' in x)
    cat['tmdb_valid'] = cat.quality_state.eq('VALID')
    assert (cat.loc[cat.tmdb_valid, 'tmdb_count'] > 0).all()
    assert cat.loc[cat.tmdb_valid, 'tmdb_mean_10'].between(0, 10, inclusive='right').all()
    cat['tmdb_mean_5_rescaled'] = cat.tmdb_mean_10.where(cat.tmdb_valid) / 2
    matched = cat[cat.mapping_status.eq('MATCHED')].copy()
    assert len(matched) == 86181 and matched.movielens_movie_id.is_unique
    matched['movieId'] = matched.movielens_movie_id.astype(np.int64)
    joined = movies.merge(matched.drop(columns='movielens_movie_id'), on='movieId', how='left', validate='one_to_one')
    joined['display_title'] = joined.tmdb_title.fillna(joined.title)
    joined['paired'] = joined.ml_count.gt(0) & joined.tmdb_valid.eq(True)
    paired = joined[joined.paired]
    tablecols = ['movieId', 'tmdb_id', 'display_title', 'ml_year', 'tmdb_year',
                 'ml_count', 'ml_mean_5', 'tmdb_count', 'tmdb_mean_10', 'tmdb_mean_5_rescaled']
    popular = joined.sort_values(['ml_count', 'movieId'], ascending=[False, True]).head(10)
    korean = joined[joined.is_kr_production.eq(True)]
    kr_top = korean.sort_values(['ml_count', 'movieId'], ascending=[False, True]).head(10)
    recent_kr = []
    for year in (2024, 2025, 2026):
        subset = cat[cat.is_kr_production & cat.tmdb_year.eq(year) & cat.tmdb_valid
                     & cat.status.eq('Released') & cat.release_date.le('2026-09-09')]
        row = subset.sort_values(['tmdb_count', 'tmdb_id'], ascending=[False, True]).head(1).copy()
        if len(row):
            # Do not force ambiguous mappings, or infer absence from release-year disagreement.
            row['movieId'] = row.movielens_movie_id.where(row.mapping_status.eq('MATCHED'))
            row = row.merge(movies[['movieId', 'ml_count', 'ml_mean_5', 'ml_year']], on='movieId', how='left', validate='many_to_one')
            recent_kr.append(row)
    recent_kr = pd.concat(recent_kr, ignore_index=True)
    coverage, comparisons = [], []
    for year in range(2020, 2027):
        ml_y = movies[movies.ml_year.eq(year)]
        tm_y = cat[cat.tmdb_year.eq(year)]
        valid_tm = tm_y[tm_y.tmdb_valid]
        same = paired[paired.ml_year.eq(year)]
        coverage.append({'release_year': year, 'ml_catalog_movies_title_year': len(ml_y),
                         'ml_rating_records': int(ml_y.ml_count.sum()),
                         'ml_rating_share_all': float(ml_y.ml_count.sum()/32000204),
                         'tmdb_local_catalog_movies_release_date_year': len(tm_y),
                         'tmdb_local_valid_rating_movies': len(valid_tm),
                         'tmdb_local_vote_sum_valid_movies': int(valid_tm.tmdb_count.sum()),
                         'tmdb_local_unrated_or_invalid_movies': len(tm_y)-len(valid_tm)})
        comparisons.append({'ml_title_year': year, 'same_rated_movies': len(same),
                            'ml_rating_records_same_movies': int(same.ml_count.sum()),
                            'tmdb_vote_sum_same_movies': int(same.tmdb_count.sum()),
                            'ml_film_equal_mean_5': float(same.ml_mean_5.mean()) if len(same) else None,
                            'tmdb_film_equal_mean_10': float(same.tmdb_mean_10.mean()) if len(same) else None,
                            'tmdb_film_equal_mean_5_rescaled': float(same.tmdb_mean_5_rescaled.mean()) if len(same) else None,
                            'ml_median_ratings_per_movie': float(same.ml_count.median()) if len(same) else None,
                            'tmdb_median_votes_per_movie': float(same.tmdb_count.median()) if len(same) else None})
    out_summary = {
        'status': 'DESCRIPTIVE_ONLY_NOT_MODEL_EVALUATION',
        'ml_movies': len(movies), 'ml_rating_records': int(counts.sum()),
        'ml_rating_period_utc': [str(np.datetime64(tmin, 's')), str(np.datetime64(tmax, 's'))],
        'tmdb_local_movies': len(cat), 'tmdb_valid_movies': int(cat.tmdb_valid.sum()),
        'mapping_status_counts': cat.mapping_status.value_counts().to_dict(),
        'tmdb_collection_note': 'MovieLens base plus recent-release expansion; not the full TMDB catalogue.',
        'tmdb_snapshot': 'DB export 2026-09-09; obtained 2026-09-12; per-film fetched_at unavailable.',
        'kr_definition': 'KR in production_country_codes; co-productions included',
        'kr_local_catalog_movies': int(cat.is_kr_production.sum()),
        'kr_ml_matched_movies': len(korean), 'kr_ml_rated_movies': int(korean.ml_count.gt(0).sum()),
        'kr_ml_rating_records': int(korean.ml_count.sum()),
        'kr_ml_share_of_all_ratings': float(korean.ml_count.sum()/32000204),
        'kr_ml_median_ratings_including_zero': float(korean.ml_count.median()),
        'kr_ml_movies_under10_including_zero': int(korean.ml_count.lt(10).sum()),
        'kr_ml_rating_weighted_mean_5': float(korean.ml_rating_sum.sum()/korean.ml_count.sum()),
        'kr_ml_rated_film_equal_mean_5': float(korean.ml_mean_5.mean()),
        'kr_top2_rating_share': float(kr_top.head(2).ml_count.sum()/korean.ml_count.sum()),
        'same_rated_movies_all': len(paired),
        'release_year_mismatches_among_matched_known': int((joined.ml_year.notna() & joined.tmdb_year.notna() & joined.ml_year.ne(joined.tmdb_year)).sum()),
        'no_model_training_or_prediction': True,
        'read_columns_ratings': ['movieId', 'rating', 'timestamp'],
        'movie_means_are_not_rounded_to_half_stars': True,
        'tmdb_divide_two_is_scale_conversion_only': True,
        'ml_popularity_rule': 'ml_count descending, movieId ascending',
        'kr_recent_examples_rule': 'For each 2024/2025/2026, released by 2026-09-09, valid TMDB rating, KR production; highest vote_count then tmdb_id',
    }
    OUT.mkdir(parents=True)
    DOC.mkdir(parents=True)
    movies.to_parquet(OUT / 'movie-rating-aggregates.parquet', index=False)
    popular[tablecols].to_csv(DOC/'movielens-popular-top10.csv', index=False)
    kr_top[tablecols].to_csv(DOC/'korean-movielens-top10.csv', index=False)
    recent_kr[['tmdb_id','tmdb_title','tmdb_year','mapping_status','movieId','ml_year','ml_count','ml_mean_5','tmdb_count','tmdb_mean_10']].to_csv(DOC/'korean-recent-examples.csv', index=False)
    pd.DataFrame(coverage).to_csv(DOC/'recent-catalog-coverage.csv', index=False)
    pd.DataFrame(comparisons).to_csv(DOC/'recent-same-film-ratings.csv', index=False)
    dump(DOC/'summary.json', out_summary)
    # Retain collection manifests as local provenance; do not query remote services.
    manifest_names = []
    with tarfile.open(MANIFEST_TAR, 'r:gz') as archive:
        for member in archive:
            if member.isfile() and (member.name.endswith('tmdb-input-manifest.json') or member.name.endswith('tmdb/collection-manifest.json')):
                data = json.load(archive.extractfile(member))
                name = Path(member.name).name
                dump(OUT/name, data)
                manifest_names.append(member.name)
    assert len(manifest_names) == 2, 'Both collection provenance manifests are required'
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family':'Malgun Gothic', 'axes.unicode_minus':False, 'font.size':12})
    p = popular.iloc[::-1]
    fig, ax = plt.subplots(figsize=(11,6.8), layout='constrained')
    ax.barh(p.display_title, p.ml_count, color='#375C83')
    for y, n in enumerate(p.ml_count): ax.text(n+1000, y, f'{n:,}', va='center', fontsize=11)
    ax.set_xlim(0,125000)
    ax.set_xlabel('MovieLens 별점 기록 수 (누적, 2023-10-13 UTC까지)')
    ax.set_title('MovieLens에서 평가가 가장 많은 영화 10편', loc='left', pad=18, fontweight='bold')
    ax.spines[['right','top']].set_visible(False)
    fig.savefig(DOC/'movielens-popular-top10.png', dpi=170)
    plt.close(fig)
    cov=pd.DataFrame(coverage)
    fig, axes=plt.subplots(1,2,figsize=(13,5.5), layout='constrained')
    years=cov.release_year.to_numpy()
    axes[0].bar(years-.2,cov.ml_catalog_movies_title_year,width=.4,label='MovieLens · 제목 연도',color='#375C83')
    axes[0].bar(years+.2,cov.tmdb_local_catalog_movies_release_date_year,width=.4,label='로컬 TMDB 목록 · 개봉일 연도',color='#C88629')
    axes[0].set_title('목록에 있는 영화 수 · 서로 다른 수집 범위', loc='left', fontsize=14)
    axes[0].set_ylabel('영화 수')
    axes[0].legend(fontsize=10)
    axes[1].bar(years,cov.ml_rating_records,color='#375C83')
    for y,n in zip(years,cov.ml_rating_records): axes[1].text(y,n+1500,f'{n:,}',ha='center',fontsize=10)
    axes[1].set_ylim(0,115000)
    axes[1].set_title('MovieLens 별점 기록 수 · 제목 연도 기준',loc='left',fontsize=14)
    axes[1].set_ylabel('별점 기록 수')
    for ax in axes:
        ax.set_xticks(years)
        ax.tick_params(axis='x',labelrotation=45)
        ax.spines[['right','top']].set_visible(False)
    fig.suptitle('2020년대 영화: 평가 시점과 카탈로그 수집 범위가 다르다', x=.02,ha='left',fontsize=17,fontweight='bold')
    fig.supxlabel('TMDB 전체 목록 아님: MovieLens 기반 + 2023-10-13 이후 개봉작 확장. 2024–2026 ML 평균평점은 관측 불가.',fontsize=10)
    fig.savefig(DOC/'recent-catalog-coverage.png',dpi=170)
    plt.close(fig)
    assert pins == [pin(p) for p in sources], 'Source changed during aggregation'
    dump(OUT/'manifest.json', {'status':'GENERATED_PENDING_INDEPENDENT_REVIEW', 'script':pin(Path(__file__)),
         'sources':pins, 'collection_manifest_members':manifest_names,
         'outputs':[pin(p) for p in sorted(DOC.iterdir())]+[pin(OUT/'movie-rating-aggregates.parquet')],
         'scope':'Aggregate raw dataset for presentation only; no model or experiment outcome changes'})
    print(json.dumps(out_summary,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
