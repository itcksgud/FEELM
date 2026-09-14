"""Full streamed metadata description, no classifier/clustering/recommendation."""
from collections import Counter
from itertools import combinations
from pathlib import Path
import csv
import gzip
import hashlib
import io
import json
import math
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'outputs/recommendation-evidence/popcorn-tmdb-full-audit'
OUT=ROOT/'docs/recommendation/experiments/popcorn-tmdb-audit/full-audit.json'
GENRES={'Action','Adventure','Animation','Comedy','Crime','Documentary','Drama','Family',
        'Fantasy','History','Horror','Music','Mystery','Romance','Science Fiction','TV Movie',
        'Thriller','War','Western'}

def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()

def runtime_minutes(value):
    try:
        r=float(value)
    except (ValueError,TypeError):
        return 0
    return r if math.isfinite(r) and r>0 else 0

class Stats:
    def __init__(self):
        self.n=0
        self.combos=Counter()
        self.fields=Counter()
        self.language=Counter()
        self.status=Counter()
        self.runtime=Counter()
    def add(self,row,gs):
        self.n+=1
        self.combos[gs]+=1
        for k in ('genres','keywords','overview','poster_path','release_date','production_countries'):
            self.fields[k]+=bool(row[k].strip())
        self.language[row['original_language'] or 'UNKNOWN']+=1
        self.status[row['status'] or 'UNKNOWN']+=1
        r=runtime_minutes(row['runtime'])
        self.runtime['unknown_or_zero' if r<=0 else 'under_60' if r<60 else '60_plus']+=1
    def result(self):
        counts=Counter()
        sizes=Counter()
        pairs=Counter()
        for gs,n in self.combos.items():
            sizes[len(gs)]+=n
            for g in gs: counts[g]+=n
            for pair in combinations(gs,2): pairs[pair]+=n
        assert sum(self.combos.values())==sum(sizes.values())==self.n
        assert sum(counts.values())==sum(k*v for k,v in sizes.items())
        return {'rows':self.n,'genre_missing':sizes[0],
            'genre_count_per_movie':dict(sorted(sizes.items())),
            'multigenre':sum(v for k,v in sizes.items() if k>=2),
            'three_plus_genres':sum(v for k,v in sizes.items() if k>=3),
            'genre_frequency':dict(counts.most_common()),
            'genre_only':{g:self.combos[(g,)] for g in sorted(GENRES)},
            'unique_genre_combinations':len(self.combos),
            'combinations':[{'genres':list(gs),'rows':n} for gs,n in self.combos.most_common()],
            'pairs':[{'genres':list(gs),'rows':n,'jaccard':n/(counts[gs[0]]+counts[gs[1]]-n)}
                     for gs,n in pairs.most_common()],
            'nonempty_fields':dict(self.fields),'language_counts':dict(self.language.most_common()),
            'status_counts':dict(self.status.most_common()),'runtime_counts':dict(self.runtime)}

def main():
    csv.field_size_limit(2**24)
    records=json.loads((DATA/'sources.json').read_text())
    pins={r['name']:r['sha256'] for r in records}
    assert all(sha(DATA/k)==v for k,v in pins.items())
    official=set()
    official_sets={}
    export_summary={}
    for name in ('movie_ids_09_10_2026.json.gz','adult_movie_ids_09_10_2026.json.gz'):
        ids=set()
        flags=Counter()
        with gzip.open(DATA/name,'rt',encoding='utf-8') as f:
            for line in f:
                row=json.loads(line)
                i=row['id']
                assert isinstance(i,int) and i>0 and i not in ids
                ids.add(i)
                flags['adult_true']+=row.get('adult') is True
                flags['video_true']+=row.get('video') is True
        export_summary[name]={'rows':len(ids),**dict(flags)}
        official_sets[name]=ids
        official.update(ids)
    overlap=len(official_sets['movie_ids_09_10_2026.json.gz'] & official_sets['adult_movie_ids_09_10_2026.json.gz'])
    strata={k:Stats() for k in ('all_csv','official_matched','general_export_matched','released_nonadult_60_plus',
                               'release_2020_plus','original_language_ko')}
    seen=set()
    duplicate_genre_rows=0
    duplicate_id_rows=0
    examples=[]
    wanted={496243,155,313369,419430,354912,38,348,299534}
    with zipfile.ZipFile(DATA/'tmdb-kaggle-20260911.zip') as z:
        assert z.namelist()==['TMDB_movie_dataset_v11.csv']
        archive={'csv_name':z.namelist()[0],'uncompressed_bytes':z.infolist()[0].file_size}
        with io.TextIOWrapper(z.open(z.namelist()[0]),encoding='utf-8-sig',newline='') as f:
            reader=csv.DictReader(f)
            fields=reader.fieldnames
            for row in reader:
                assert None not in row, 'Malformed CSV row'
                i=int(row['id'])
                assert i>0, 'Invalid movie ID'
                if i in seen:
                    duplicate_id_rows+=1
                    continue
                seen.add(i)
                raw_genres=[g.strip() for g in row['genres'].split(',') if g.strip()]
                duplicate_genre_rows+=len(raw_genres)!=len(set(raw_genres))
                gs=tuple(sorted(set(raw_genres)))
                assert set(gs)<=GENRES, {'id':i,'unexpected_genres':sorted(set(gs)-GENRES)}
                strata['all_csv'].add(row,gs)
                if i in official:
                    strata['official_matched'].add(row,gs)
                if i in official_sets['movie_ids_09_10_2026.json.gz']:
                    strata['general_export_matched'].add(row,gs)
                if row['status']=='Released' and row['adult']=='False' and runtime_minutes(row['runtime'])>=60:
                    strata['released_nonadult_60_plus'].add(row,gs)
                if row['release_date'][:4].isdigit() and int(row['release_date'][:4])>=2020:
                    strata['release_2020_plus'].add(row,gs)
                if row['original_language']=='ko':
                    strata['original_language_ko'].add(row,gs)
                if i in wanted:
                    examples.append({'id':i,'title':row['title'],'genres':list(gs),
                        'keywords_nonempty':bool(row['keywords'].strip()),'official_id':i in official})
    matched=len(seen&official)
    assert strata['all_csv'].n==len(seen)
    assert strata['official_matched'].n==matched
    assert len(seen)==matched+len(seen-official)
    assert len(official)==matched+len(official-seen)
    assert examples, 'No requested examples found'
    result={'status':'DESCRIPTIVE_ONLY_NO_MODEL_EXPERIMENT','input_sha256':pins,
        'runner_sha256':sha(Path(__file__)),'python':sys.version,'sources':records,
        'archive':archive,'csv_fields':fields,'official_exports':export_summary,
        'official_export_overlap':overlap,'official_union_ids':len(official),
        'csv_unique_ids':len(seen),'official_ids_with_csv_details':matched,
        'csv_duplicate_id_rows_skipped_first_occurrence_retained':duplicate_id_rows,
        'csv_raw_rows':len(seen)+duplicate_id_rows,
        'csv_rows_with_duplicate_genre_names':duplicate_genre_rows,
        'official_ids_missing_csv_details':len(official-seen),
        'csv_ids_absent_from_official_exports':len(seen-official),
        'strata':{k:s.result() for k,s in strata.items()},
        'examples':examples,'missing_example_ids':sorted(wanted-{r['id'] for r in examples}),
        'limitations':['Third-party details; no per-row fetched_at; snapshot coverage is not semantic freshness.',
                      'Keywords flattened to names, not original keyword IDs; presence counted, names not treated as gold.',
                      'Genre and keyword absence is UNKNOWN. No mood quality or recommendation quality evaluated.',
                      'Auxiliary subsets are descriptive and do not define service eligibility.']}
    assert all(sha(DATA/k)==v for k,v in pins.items())
    with OUT.open('x',encoding='utf-8') as f:
        json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False)
        f.write('\n')
    brief={k:v for k,v in result.items() if k not in ('strata','sources','csv_fields')}
    brief['strata']={k:{a:b for a,b in s.items() if a not in ('combinations','pairs','language_counts')}
                     for k,s in result['strata'].items()}
    print(json.dumps(brief,ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()
