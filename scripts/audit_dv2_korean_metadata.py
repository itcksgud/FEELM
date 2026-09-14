"""Outcome-blind metadata descriptors for frozen K128/K256 and 33 examples.

Reads prepared catalog/content/Q and frozen group assets only, never users,
future labels, predictors, sweep selections or check outcomes. Q prefixes are
static eligible prefixes before any user-specific seen exclusion.
"""
from collections import Counter
from pathlib import Path
import hashlib
import json
import pickle
import re
import tarfile

import numpy as np
import pandas as pd

R = Path('C:/higher/projects/FEELM-standalone')
W = R / '.codex-tmp/discovery-v2-predictor-20260913'
MAIN = R / '.codex-tmp/fixed-k8-discovery-v2-20260913'
BASE = MAIN / 'outputs/fixed-k8-discovery-v2/feelm-discovery-v2-r2'
OLD_DOC = R / '.codex-tmp/fixed-k8-discovery-20260912/docs/recommendation/experiments/fixed-k8-discovery/KOREAN-EXAMPLES.md'
OUT = W / 'outputs/dv2-korean-metadata-audit-r2'


def pin(path):
    return {'bytes':path.stat().st_size, 'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def run():
    assert not OUT.exists()
    source_pins = {}
    for stage, selected in [('prepare', ['catalog.parquet','content.npy','quality.npy','quality-order.npy','report.json']),
                            ('cluster', ['GKT-K128-assignments.npz','GKT-K128-hierarchy.pkl',
                                         'GKT-K256-assignments.npz','GKT-K256-hierarchy.pkl','groups.parquet'])]:
        seal_path = BASE/(stage+'-seal.json')
        source_pins[seal_path.as_posix()] = pin(seal_path)
        seal = read(seal_path)
        for name in selected:
            path = BASE/stage/name
            source_pins[path.as_posix()] = pin(path)
            assert pin(path) == seal['files'][str(path.relative_to(BASE))]
    source_pins[OLD_DOC.as_posix()] = pin(OLD_DOC)
    examples = list(dict.fromkeys((int(a),int(b)) for a,b in re.findall(r'(\d+)/(\d+)', OLD_DOC.read_text(encoding='utf-8'))))
    assert len(examples) == 33
    frame = pd.read_parquet(BASE/'prepare/catalog.parquet')
    x = np.load(BASE/'prepare/content.npy', mmap_mode='r')
    q = np.load(BASE/'prepare/quality.npy')
    order = np.load(BASE/'prepare/quality-order.npy')
    id_to_index = {int(mid):i for i,mid in enumerate(frame.service_movie_id)}
    for service,tmdb in examples:
        assert int(frame.tmdb_id.iloc[id_to_index[service]]) == tmdb
    report = read(BASE/'prepare/report.json')
    keyword_path = next(Path(p) for p in report['sources'] if '03_tmdb-keywords-raw' in p)
    raw_path = next(Path(p) for p in report['sources'] if '02_tmdb-movie-raw' in p)
    for path in [keyword_path, raw_path]:
        source_pins[path.as_posix()] = pin(path)
        assert pin(path) == report['sources'][str(path)]
    keyword_names = {}
    with tarfile.open(keyword_path, 'r|gz') as archive:
        for member in archive:
            if member.isfile() and member.name.endswith('.json'):
                body = json.load(archive.extractfile(member))
                for keyword in body.get('keywords') or []:
                    keyword_names.setdefault(int(keyword['id']), set()).add(keyword['name'])
    genre_names = {}
    genre_ids = {int(g) for row in frame.genre_ids for g in row}
    with tarfile.open(raw_path, 'r|gz') as archive:
        for member in archive:
            if member.isfile() and member.name.endswith('.json'):
                body = json.load(archive.extractfile(member))['details']
                for genre in body.get('genres') or []:
                    genre_names[int(genre['id'])] = genre['name']
                if genre_ids <= genre_names.keys():
                    break
    assert genre_ids <= genre_names.keys()
    genres = frame.genre_ids.tolist()
    keywords = frame.keyword_ids.tolist()
    supported = np.sum(x*x, axis=1)>1e-12
    eligible = np.zeros(len(frame),bool);eligible[order] = True

    def token_counts(indices, tokens, names, maximum):
        counts = Counter(int(k) for i in indices for k in set(tokens[i]))
        return [{'id':k, 'names':sorted(names[k]) if isinstance(names.get(k),set) else [names.get(k,str(k))],
                 'films':n, 'share_of_all_members':n/len(indices) if len(indices) else None}
                for k,n in sorted(counts.items(),key=lambda z:(-z[1],z[0]))[:maximum]]

    def film(index, overview=False):
        row = frame.iloc[index]
        result = {'service_movie_id':int(row.service_movie_id),'tmdb_id':int(row.tmdb_id),
                  'title':row.title,'original_title':row.original_title,
                  'genres':[genre_names[int(g)] for g in row.genre_ids],
                  'keywords':[{'id':int(k),'names':sorted(keyword_names.get(int(k),{str(k)}))} for k in row.keyword_ids],
                  'genre_present':bool(row.genre_present),'keyword_present':bool(row.keyword_present),
                  'overview_present':bool(row.overview_present),'no_content':bool(row.no_content),
                  'mapping_status':row.mapping_status,'Q':float(q[index]) if np.isfinite(q[index]) else None,
                  'eligible':bool(eligible[index])}
        if overview:
            result['overview'] = row.overview if isinstance(row.overview,str) else None
        return result

    all_groups, hierarchy_summaries, case_rows = [],[],[]
    for k in [128,256]:
        name = f'GKT-K{k}'
        assignment = np.load(BASE/'cluster'/f'{name}-assignments.npz')
        np.testing.assert_array_equal(assignment['service_movie_id'],frame.service_movie_id)
        with (BASE/'cluster'/f'{name}-hierarchy.pkl').open('rb') as f:
            h = pickle.load(f)
        groups = assignment['group_id']
        np.testing.assert_array_equal(groups,h['groups'])
        prefixes = {}
        for i in order:
            prefixes.setdefault(int(groups[i]),[]).append(int(i))
        rows = {}
        for g in range(h['n_groups']):
            ix = np.flatnonzero(groups==g);good = ix[supported[ix]]
            prefix = np.asarray(prefixes.get(g,[])[:25],int)
            mean = np.asarray(x[good]).mean(axis=0) if len(good) else np.zeros(x.shape[1])
            np.testing.assert_allclose(mean,h['representatives']['mean'][g],rtol=0,atol=1e-12)
            prefix_mean = np.asarray(x[prefix]).mean(axis=0) if len(prefix) else None
            medoid_id = int(h['representatives']['medoid_ids'][g])
            row = {'hierarchy':name,'group_id':g,'taste_id':int(np.searchsorted(h['offsets'][1:],g,side='right')),
                   'child_id':int(g-h['offsets'][np.searchsorted(h['offsets'][1:],g,side='right')]),
                   'members':len(ix),'supported':len(good),'eligible':int(eligible[ix].sum()),
                   'missing_genre':int((~frame.genre_present.iloc[ix]).sum()),
                   'missing_keyword':int((~frame.keyword_present.iloc[ix]).sum()),
                   'missing_overview':int((~frame.overview_present.iloc[ix]).sum()),
                   'all_content_missing':int(frame.no_content.iloc[ix].sum()),
                   'genres':token_counts(ix,genres,genre_names,6),'keywords':token_counts(ix,keywords,keyword_names,8),
                   'static_prefix25_count':len(prefix),
                   'static_prefix25_genres':token_counts(prefix,genres,genre_names,6),
                   'static_prefix25_keywords':token_counts(prefix,keywords,keyword_names,8),
                   'mean_to_static_prefix25_l2':float(np.linalg.norm(mean-prefix_mean)) if prefix_mean is not None else None,
                   'medoid_service_id':medoid_id if medoid_id>=0 else None,
                   'medoid_eligible':bool(eligible[id_to_index[medoid_id]]) if medoid_id>=0 else None,
                   'medoid_in_static_prefix25':bool(medoid_id in frame.service_movie_id.iloc[prefix].tolist())}
            rows[g] = row;all_groups.append(row)
        for service,tmdb in examples:
            i = id_to_index[service];g = int(groups[i]);row = rows[g]
            medoid = row['medoid_service_id']
            case_rows.append({'hierarchy':name,'example':film(i,True),'group':row,
                              'medoid':film(id_to_index[medoid],True) if medoid is not None else None,
                              'static_Q_prefix_first5':[film(j,True) for j in prefixes.get(g,[])[:5]]})
        hierarchy_summaries.append({'hierarchy':name,'groups':h['n_groups'],
            'groups_with_no_eligible':sum(r['eligible']==0 for r in rows.values()),
            'groups_with_fewer_than25_eligible':sum(r['eligible']<25 for r in rows.values()),
            'medoids_ineligible':sum(r['medoid_eligible'] is False for r in rows.values()),
            'mean_to_static_prefix25_l2_median':float(np.median([r['mean_to_static_prefix25_l2'] for r in rows.values() if r['mean_to_static_prefix25_l2'] is not None])),
            'mean_to_static_prefix25_l2_max':max(r['mean_to_static_prefix25_l2'] for r in rows.values() if r['mean_to_static_prefix25_l2'] is not None)})
    for path,wanted in source_pins.items():
        assert pin(Path(path)) == wanted
    OUT.mkdir(parents=True)
    payloads = {'groups.json':all_groups,'examples.json':case_rows,
                'report.json':{'status':'PASS','scope':'Frozen metadata descriptions; no outcomes or personalized recommendation claims',
                               'sources':source_pins,'script':pin(Path(__file__)),'examples':33,'group_example_pairs':len(case_rows),
                               'hierarchies':hierarchy_summaries,'keyword_name_conflicts':sum(len(v)>1 for v in keyword_names.values()),
                               'user_or_future_label_files_read':False,'predictor_or_sweep_or_check_outputs_read':False,
                               'prefix_definition':'First25 eligible members in frozen global Q order, before any user-specific seen exclusion or underseen filtering'}}
    for name,value in payloads.items():
        (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps(payloads['report.json'],ensure_ascii=False))


if __name__ == '__main__':
    run()
