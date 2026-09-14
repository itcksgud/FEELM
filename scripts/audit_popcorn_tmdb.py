"""Describe existing TMDB metadata; no models, ratings, APIs or fitting."""
from collections import Counter
from itertools import combinations
from pathlib import Path
import hashlib
import json
import sys
import pandas as pd

SOURCE = Path('C:/higher/projects/FEELM-standalone')
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/recommendation/experiments/popcorn-tmdb-audit/audit.json'
PINS = {
    'rec-ev-033': '86e2e4eb087bbdbf2500b9b8a7e50abf1ef09b52ea6110c62d41581e7d45d016',
    'rec-ev-045': '4d838874938115be7a4b1f629a920dd196e082b655b75d559d71039e52eb7d8d',
}

def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def main():
    paths = {k: SOURCE / 'outputs/recommendation-evidence' / k / 'metadata.parquet' for k in PINS}
    before = {k: sha(p) for k, p in paths.items()}
    assert before == PINS, 'Input fingerprint differs'
    a = pd.read_parquet(paths['rec-ev-033'], columns=[
        'movie_id','tmdb_id','title','original_title','original_language',
        'genre_ids','genre_names','keyword_ids','keyword_names','cache_path','response_sha256'])
    b = pd.read_parquet(paths['rec-ev-045'], columns=[
        'movie_id','tmdb_id','genre_ids','release_year','overview_characters',
        'production_country_codes','fetched_at'])
    assert a.movie_id.is_unique and b.movie_id.is_unique and a.tmdb_id.is_unique
    assert len(a) == len(b) == 85517
    assert a.movie_id.equals(b.movie_id)
    assert list(a.tmdb_id) == list(b.tmdb_id)
    genres = [tuple(sorted(map(int, v))) for v in a.genre_ids]
    assert genres == [tuple(sorted(map(int, v))) for v in b.genre_ids]
    assert all(len(v) == len(set(v)) for v in genres)
    names = {}
    for ids, labels in zip(a.genre_ids, a.genre_names):
        assert len(ids) == len(labels)
        for g, name in zip(ids, labels):
            assert int(g) not in names or names[int(g)] == name
            names[int(g)] = name
    n = len(a)
    freq = Counter(g for gs in genres for g in gs)
    sizes = Counter(map(len, genres))
    combos = Counter(genres)
    pairs = Counter(pair for gs in genres for pair in combinations(gs, 2))
    assert sum(freq.values()) == sum(k*v for k,v in sizes.items())
    assert sum(combos.values()) == sum(sizes.values()) == n
    def bundle(gs):
        return {'genre_ids': list(gs), 'genre_names': [names[g] for g in gs]}
    examples = []
    wanted = {'Parasite','기생충','The Dark Knight','La La Land','Get Out',
              'Coco','Eternal Sunshine of the Spotless Mind','Alien','Avengers: Endgame'}
    for row in a.itertuples(index=False):
        if row.original_title not in wanted:
            continue
        p = Path(row.cache_path)
        if not p.is_absolute():
            p = SOURCE / p
        p = p.resolve()
        assert p.is_relative_to(SOURCE.resolve()), 'Cache outside research repository'
        cache = json.loads(p.read_text(encoding='utf-8'))
        body = cache['body']
        actual = hashlib.sha256((json.dumps(body, ensure_ascii=False, sort_keys=True,
                                             separators=(',', ':'))+'\n').encode('utf-8')).hexdigest()
        assert actual == row.response_sha256 == cache['body_sha256']
        assert body['id'] == row.tmdb_id
        assert sorted(g['id'] for g in body['genres']) == sorted(map(int,row.genre_ids))
        examples.append({'tmdb_id': int(row.tmdb_id), 'title': row.title,
            'original_title': row.original_title, **bundle(tuple(map(int,row.genre_ids))),
            'keyword_names': list(row.keyword_names),
            'overview_excerpt': str(body.get('overview') or '')[:260], 'body_hash_verified': True})
    # Genre-only edge cases are descriptive, not an assignment to any taste scheme.
    auxiliary = {16,99,36,10402,10751,10770,10752,37}
    auxiliary_only = Counter(gs for gs in genres if set(gs).issubset(auxiliary))
    data = {
        'status': 'DESCRIPTIVE_ONLY_NO_MODEL_EXPERIMENT',
        'input_sha256': before, 'runner_sha256': sha(Path(__file__)),
        'python': sys.version, 'pandas': pd.__version__, 'movies':n,
        'genre_count':len(freq), 'genre_assignments':sum(freq.values()),
        'genre_count_per_movie':dict(sorted(sizes.items())),
        'multigenre_movies':sum(v for k,v in sizes.items() if k>=2),
        'three_plus_genre_movies':sum(v for k,v in sizes.items() if k>=3),
        'unique_genre_combinations':len(combos),
        'genre_frequency':[{'id':g,'name':names[g],'movies':c,'percent':100*c/n,
            'only_this_genre':combos[(g,)]} for g,c in freq.most_common()],
        'top_combinations':[{**bundle(gs),'movies':c,'percent':100*c/n} for gs,c in combos.most_common(30)],
        'genre_pairs':[{**bundle(gs),'movies':c,'percent':100*c/n,
            'share_of_first':c/freq[gs[0]],'share_of_second':c/freq[gs[1]],
            'jaccard':c/(freq[gs[0]]+freq[gs[1]]-c)} for gs,c in pairs.most_common()],
        'keyword_nonempty':int(a.keyword_ids.map(len).gt(0).sum()),
        'distinct_keyword_ids':len({int(g) for ids in a.keyword_ids for g in ids}),
        'overview_nonempty':int(b.overview_characters.gt(0).sum()),
        'language_counts':{str(k):int(v) for k,v in a.original_language.value_counts(dropna=False).items()},
        'release_year_min':float(b.release_year.min()), 'release_year_max':float(b.release_year.max()),
        'release_year_missing':int(b.release_year.isna().sum()),
        'release_year_2020_plus':int(b.release_year.ge(2020).sum()),
        'korean_original_language':int(a.original_language.eq('ko').sum()),
        'kr_production_country':int(b.production_country_codes.map(lambda v:'KR' in v).sum()),
        'fetched_at_min':str(b.fetched_at.min()), 'fetched_at_max':str(b.fetched_at.max()),
        'auxiliary_set':bundle(tuple(sorted(auxiliary))),
        'only_auxiliary_genres_movies':sum(auxiliary_only.values()),
        'only_auxiliary_top_combinations':[{**bundle(gs),'movies':c} for gs,c in auxiliary_only.most_common(15)],
        'examples':examples,
    }
    assert {k:sha(p) for k,p in paths.items()} == before
    with OUT.open('x', encoding='utf-8') as f:
        json.dump(data,f,ensure_ascii=False,indent=2,allow_nan=False)
        f.write('\n')
    print(json.dumps({k:v for k,v in data.items() if k not in ('genre_pairs','language_counts')},
                     ensure_ascii=False,indent=2))

if __name__ == '__main__':
    main()
