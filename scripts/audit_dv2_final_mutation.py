"""Independent final and mutation result audit; source remains read-only.

Geometry, pools, profiles and summaries use audit formulas. The three named
serving requests use the pinned public research runtime for independent replay,
not an independent predictor implementation. Never trains or selects a policy.
"""
from __future__ import annotations
import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import pickle
import re
import sys
import time

import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.preprocessing import normalize
from threadpoolctl import threadpool_limits

HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parent / 'fixed-k8-discovery-v2-20260913'
OUT = ROOT / 'outputs/fixed-k8-discovery-v2/feelm-discovery-v2-r2'
DOC = ROOT / 'docs/recommendation/experiments/fixed-k8-discovery-v2'
MROOT = HERE.parent / 'discovery-v2-prefix-drift-20260913'
MOUT = MROOT / 'outputs/mutation-prefix-drift-r2'
DEST = HERE / 'outputs/discovery-v2-result-audit/final-mutation-audit.json'
FINAL_SHA = '84916471ba353e29e82948dca977b59195fd0d31bab5e99cbd6ceab1070b7730'


def read(p):
    return json.loads(Path(p).read_text(encoding='utf-8'))


def pin(p):
    p = Path(p)
    with p.open('rb') as f:
        h = hashlib.file_digest(f, 'sha256').hexdigest()
    return dict(sha256=h, bytes=p.stat().st_size)


def ah(a):
    a = np.ascontiguousarray(a)
    return hashlib.sha256(str(a.dtype).encode() + str(a.shape).encode() + a.tobytes()).hexdigest()


def equal(a, b, label=''):
    if isinstance(a, dict) or isinstance(b, dict):
        assert isinstance(a, dict) and isinstance(b, dict) and a.keys() == b.keys(), label
        for k in a: equal(a[k], b[k], label + '/' + str(k))
    elif isinstance(a, (list, tuple, np.ndarray)) or isinstance(b, (list, tuple, np.ndarray)):
        assert len(a) == len(b), (label, len(a), len(b))
        for i, (x, y) in enumerate(zip(a, b)): equal(x, y, label + '/' + str(i))
    elif isinstance(a, (float, np.floating)) or isinstance(b, (float, np.floating)):
        assert np.isclose(a, b, rtol=1e-9, atol=1e-12, equal_nan=True), (label, a, b)
    else:
        assert a == b, (label, a, b)


def verify_stage(stage):
    seal = read(OUT / (stage + '-seal.json'))
    actual = {str(p.relative_to(OUT)): pin(p) for p in sorted((OUT / stage).rglob('*')) if p.is_file()}
    assert actual == seal['files'], stage
    return pin(OUT / (stage + '-seal.json'))


def unit(a):
    a = np.asarray(a, np.float64)
    length = np.sqrt(np.sum(a * a, axis=1))
    return np.divide(a, length[:, None], out=np.zeros_like(a), where=length[:, None] > 1e-12)


def blocks(frame, prep):
    answer = []
    for field, vocab, idf in [('genre_ids', prep['genres'], None), ('keyword_ids', prep['keywords'], prep['keyword_idf'])]:
        lookup = {int(k): i for i, k in enumerate(vocab)}
        row, col = [], []
        for i, ids in enumerate(frame[field]):
            valid = ids if isinstance(ids, (list, tuple, np.ndarray)) else []
            cols = sorted({lookup[int(k)] for k in valid if int(k) in lookup})
            row.extend([i] * len(cols)); col.extend(cols)
        mat = sparse.coo_matrix((np.ones(len(row)), (row, col)), shape=(len(frame), len(vocab))).tocsr()
        if idf is None: answer.append(unit(mat.toarray()))
        else: answer.append(unit(normalize(mat.multiply(idf).tocsr(), copy=False) @ prep['keyword_components'].T))
    text = prep['text_vectorizer'].transform(frame.overview.fillna('').tolist())
    answer.append(unit(text @ prep['text_components'].T))
    return answer


def combine(parts, weights):
    return unit(np.concatenate([v * np.sqrt(w) for v, w in zip(parts, weights)], axis=1))


def near(x, centers):
    distances = np.empty((len(x), len(centers)))
    for i, center in enumerate(centers):
        distances[:, i] = np.sum((np.asarray(x, np.float64) - center) ** 2, axis=1)
    return np.argmin(distances, axis=1)


def assign(frame, bundle):
    part = blocks(frame, bundle['preprocessor'])
    tx = combine(part, bundle['top_weights']); sx = combine(part, bundle['sub_weights'])
    top = near(tx, bundle['top_centers']); child = np.zeros(len(frame), int)
    for i, centers in enumerate(bundle['child_centers']):
        ix = np.flatnonzero(top == i)
        if len(ix): child[ix] = near(sx[ix], centers)
    offsets = np.cumsum([0] + [len(v) for v in bundle['child_centers']])
    x = combine(part, bundle['content_weights'])
    return dict(taste_id=top, child_id=child, group_id=offsets[top] + child,
                top_supported=np.sum(tx * tx, axis=1) > 1e-12,
                content_supported=np.sum(x * x, axis=1) > 1e-12), x


def check_assign(frame, bundle, saved, indices, batch):
    for start in range(0, len(indices), batch):
        ix = indices[start:start + batch]
        got, _ = assign(frame.iloc[ix], bundle)
        for k, v in got.items(): np.testing.assert_array_equal(v, saved[k].to_numpy()[ix])
    return len(indices)


def material(bundle):
    prep = bundle['preprocessor']; vec = prep['text_vectorizer']
    value = {'top': ah(bundle['top_centers']), 'children': [ah(c) for c in bundle['child_centers']],
             'representatives': {k: ah(v) for k, v in bundle['representatives'].items()},
             'preprocessor_arrays': {k: ah(prep[k]) for k in ['keyword_idf', 'keyword_components', 'text_components']},
             'genres': list(prep['genres']), 'keywords': list(prep['keywords']),
             'text_vocabulary': sorted((k, int(v)) for k, v in vec.vocabulary_.items()), 'text_idf': ah(vec.idf_),
             'names': bundle['top_names'], 'group_names': bundle.get('group_names'),
             'text_parameters': {k: str(v) for k, v in vec.get_params(deep=False).items()}}
    for k in ['top_weights', 'sub_weights', 'content_weights', 'runtime_rules', 'quality', 'policy', 'predictor',
              'candidate_date', 'runtime_versions', 'tie_rule', 'source_space_hashes', 'review_status']:
        value[k] = bundle[k]
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def past_contexts():
    raw = (OUT / 'prepare/contexts.json').read_bytes()
    numeric = rb'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?'
    array = rb'\[\s*(?:' + numeric + rb'\s*(?:,\s*' + numeric + rb'\s*)*)?\]'
    redacted, n = re.subn(rb'("ratings"\s*:\s*)' + array, rb'\1null', raw)
    assert n == len(re.findall(rb'"ratings"\s*:', raw)) == 1350
    values = json.loads(redacted)
    keep = ['uid', 'cap', 'history', 'stars', 'original_stars', 'original_input_count', 'viewed', 'raw_pre_count']
    return [{k: v[k] for k in keep} for v in values]


def profile(x, c):
    h = np.array(c['history'], int); stars = np.asarray(c['stars'], float)
    original = np.asarray(c['original_stars'], float)
    support = (x[h] * x[h]).sum(axis=1) > 1e-12
    anchor = 3 + .5 * ((original.sum() + 17.5) / (len(original) + 5) - 3)
    w = (stars - anchor)[support]
    raw = (x[h[support]] * w[:, None]).sum(axis=0) / (np.abs(w).sum() + 5)
    length = np.linalg.norm(raw)
    valid = c['raw_pre_count'] > 0 and c['cap'] > 0 and len(original) > 0 and len(h) > 0 and support.any() and length > 1e-8
    return raw / length if valid else np.zeros(x.shape[1]), bool(valid)


def added_rows(frame):
    added = frame.iloc[:3].copy().reset_index(drop=True)
    first = int(frame.service_movie_id.max()) + 1000
    added['service_movie_id'] = np.arange(first, first + 3)
    added['tmdb_id'] = np.arange(int(frame.tmdb_id.max()) + 1000, int(frame.tmdb_id.max()) + 1003)
    added['mapping_status'] = 'SERVICE_ONLY_CONTENT_BASED'; added['movielens_movie_id'] = np.nan
    for row in [1, 2]:
        for field in ['genre_ids', 'keyword_ids', 'director_ids', 'top5_cast_ids', 'production_company_ids', 'collection_ids', 'production_country_codes', 'origin_country_codes']:
            added.at[row, field] = []
        added.at[row, 'overview'] = ''
    added.at[1, 'genre_ids'] = None; added.at[1, 'keyword_ids'] = None; added.at[1, 'overview'] = None
    added.at[2, 'genre_ids'] = [999999991]; added.at[2, 'keyword_ids'] = [999999993]
    added.at[2, 'overview'] = 'zzzz_v2_oov_9837621_only'
    return added


def quality(frame, bundle, x, active):
    v = frame.raw_vote_count_number.to_numpy(float); r = frame.raw_vote_average_number.to_numpy(float)
    valid = frame.quality_state.eq('VALID').to_numpy()
    q = np.full(len(frame), np.nan); C = bundle['quality']['C']; m = bundle['quality']['m']
    q[valid] = (v[valid] * r[valid] + C * m) / (v[valid] + m)
    dates = pd.to_datetime(frame.release_date, format='%Y-%m-%d', errors='coerce')
    eligible = (dates.notna() & dates.le(pd.Timestamp(bundle['candidate_date'])) & frame.status.eq('Released') & frame.raw_adult_state.eq('FALSE') & frame.raw_video_state.eq('FALSE')).to_numpy()
    ix = np.flatnonzero(eligible & valid & active & ((x * x).sum(axis=1) > 1e-12))
    ids = frame.service_movie_id.to_numpy()
    return q, ix[np.lexsort((ids[ix], -v[ix], -q[ix]))]


def reconstruct(c, frame, x, groups, order, bundle):
    policy = bundle['policy']; n = sum(len(v) for v in bundle['child_centers'])
    assert policy['kind'] == 'group' and policy['rep'] == 'mean'
    p, valid = profile(x, c); assert valid
    counts = np.bincount(groups[c['viewed']], minlength=n)
    under = (counts <= 2) & (counts / max(1, len(c['viewed'])) <= .2)
    available = order[~np.isin(order, c['viewed'])]; available = available[under[groups[available]]]
    pools = {g: available[groups[available] == g] for g in range(n)}
    pools = {g: a for g, a in pools.items() if len(a)}
    legal = np.asarray(sorted(pools), int)
    scores = bundle['representatives']['mean'][legal] @ p
    group_order = legal[np.lexsort((legal, -scores))]
    candidates = []; candidate_groups = []; visited = []
    for g in group_order:
        take = pools[int(g)][:policy['quota']][:policy['budget'] - len(candidates)]
        candidates.extend(take); candidate_groups.extend([int(g)] * len(take)); visited.append(int(g))
        if len(candidates) >= policy['budget']: break
    ids = frame.service_movie_id.to_numpy(); sim = x @ p
    snapshot = dict(candidate_ids=ids[candidates].tolist(), legal_groups=legal.tolist(), underseen_groups=np.flatnonzero(under).tolist(),
                    full_pool_count=sum(map(len, pools.values())), prefix_pool_count=sum(min(len(v), policy['quota']) for v in pools.values()),
                    group_order=group_order.tolist(), visited_groups=visited, candidate_groups=candidate_groups,
                    state='READY' if len(candidates) >= policy['budget'] else 'ELIGIBLE_PREFIXES_EXHAUSTED',
                    candidate_mean_similarity=float(np.mean(sim[candidates])) if len(candidates) else None)
    return p, pools, snapshot, sim


def overlap(a, b):
    sa = set(a); sb = set(b); intersection = len(sa & sb)
    return dict(before=len(a), after=len(b), intersection=intersection,
                retained_fraction=intersection / len(a) if a else None, after_fraction=intersection / len(b) if b else None,
                jaccard=intersection / len(sa | sb) if sa | sb else None, ordered_equal=a == b,
                removed=sorted(sa - sb), added=sorted(sb - sa))


def distribution(values):
    a = np.asarray([v for v in values if v is not None], float)
    if not len(a): return dict(n=0)
    return dict(n=len(a), min=float(a.min()), median=float(np.median(a)), mean=float(a.mean()), max=float(a.max()),
                max_abs=float(np.abs(a).max()), zero=int((a == 0).sum()), positive=int((a > 0).sum()), negative=int((a < 0).sum()))


def audit_mutation(frame, saved, x, bundle, contexts, removed, added, new_assigned, new_x):
    report = read(MOUT / 'report.json'); mseal = read(MOUT / 'seal.json')
    assert mseal['files'] == {p.name: pin(p) for p in sorted(MOUT.iterdir()) if p.is_file() and p.name != 'seal.json'}
    review = read(report['review']['path']); assert review['status'] == 'PASS'
    assert pin(report['review']['path']) == {k: report['review'][k] for k in ['bytes', 'sha256']}
    assert report['fingerprint'] == review['fingerprint']
    for path, expected in report['fingerprint']['files'].items(): assert pin(path) == expected, path
    equal(report['final_seal'], pin(OUT / 'final-seal.json')); equal(report['prepare_seal'], pin(OUT / 'prepare-seal.json'))
    equal(report['material_digest_before'], material(bundle)); equal(report['material_digest_after'], material(bundle))
    equal(report['policy'], bundle['policy']); assert report['model_predictions'] == 0
    combined = pd.concat([frame, added], ignore_index=True)
    xx = np.concatenate([x, new_x]); groups = np.r_[saved.group_id, new_assigned['group_id']]
    ids = combined.service_movie_id.to_numpy(); n = len(frame)
    roles = read(OUT / 'prepare/roles.json')
    eligible = sorted([c for c in contexts if c['cap'] == 10 and c['uid'] in roles['verification'] and profile(x, c)[1]], key=lambda c: c['uid'])
    equal(report['selection']['eligible_valid_check_users'], len(eligible)); chosen = eligible[:20]
    equal(report['selection']['uids'], [c['uid'] for c in chosen]); equal(report['fixture']['removed_service_ids'], removed)
    equal(report['fixture']['added_service_ids'], added.service_movie_id.tolist()); equal(report['fixture']['added_group_ids'], new_assigned['group_id'])
    equal(report['fixture']['added_trained_content_support'], new_assigned['content_supported'])
    states = {}
    for name, active in [('before', np.r_[np.ones(n, bool), np.zeros(3, bool)]), ('append', np.ones(n + 3, bool)), ('append_delete', ~combined.service_movie_id.isin(removed).to_numpy())]:
        q, order = quality(combined, bundle, xx, active); states[name] = order
        equal(report['scenarios'][name], dict(active_catalog=int(active.sum()), quality_eligible=len(order), q_order_sha256=ah(ids[order])))
        np.testing.assert_array_equal(q[:n], np.load(OUT / 'prepare/quality.npy'))
    pool_saved = {}
    with gzip.open(MOUT / 'pools.jsonl.gz', 'rt', encoding='utf-8') as f:
        for line in f:
            row = json.loads(line); key = (row['uid'], row['scenario'], row['group_id']); assert key not in pool_saved; pool_saved[key] = row
    user_saved = read(MOUT / 'users.json'); equal([v['uid'] for v in user_saved], [c['uid'] for c in chosen])
    comparisons = []; deltas = []; all_pools = {}; snapshots = {}; pool_count = 0
    for c, su in zip(chosen, user_saved):
        uid = c['uid']; snapshots[uid] = {}; all_pools[uid] = {}
        for name, order in states.items():
            p, pools, snap, sim = reconstruct(c, combined, xx, groups, order, bundle)
            equal(snap, su['scenarios'][name], f'user{uid}/{name}'); equal(ah(p), su['profile_sha256'])
            snapshots[uid][name] = snap; all_pools[uid][name] = {}
            for g, full in pools.items():
                prefix = full[:bundle['policy']['quota']]
                row = dict(uid=uid, scenario=name, group_id=g, full_count=len(full), prefix_count=len(prefix), full_ids=ids[full].tolist(), prefix_ids=ids[prefix].tolist(), full_mean_similarity=float(sim[full].mean()), prefix_mean_similarity=float(sim[prefix].mean()))
                equal(row, pool_saved[(uid, name, g)], f'pool{uid}/{name}/{g}'); pool_count += 1
                all_pools[uid][name][g] = row
        equal(su['removed_history_ids'], sorted(set(map(int, ids[c['history']])) & set(removed)))
        equal(su['removed_viewed_ids'], sorted(set(map(int, ids[c['viewed']])) & set(removed)))
        for left, right in [('before', 'append'), ('append', 'append_delete'), ('before', 'append_delete')]:
            label = left + '_to_' + right; a = snapshots[uid][left]; b = snapshots[uid][right]
            comparisons.append(dict(uid=uid, transition=label, candidates=overlap(a['candidate_ids'], b['candidate_ids']), vanished_groups=sorted(set(a['legal_groups']) - set(b['legal_groups'])), new_groups=sorted(set(b['legal_groups']) - set(a['legal_groups'])), group_order_equal=a['group_order'] == b['group_order'], candidate_mean_similarity_delta=b['candidate_mean_similarity'] - a['candidate_mean_similarity'] if a['candidate_mean_similarity'] is not None and b['candidate_mean_similarity'] is not None else None))
            for g in sorted(set(all_pools[uid][left]) | set(all_pools[uid][right])):
                ar = all_pools[uid][left].get(g); br = all_pools[uid][right].get(g)
                deltas.append(dict(uid=uid, transition=label, group_id=g, full_pool=overlap(ar['full_ids'] if ar else [], br['full_ids'] if br else []), q_prefix=overlap(ar['prefix_ids'] if ar else [], br['prefix_ids'] if br else []), full_mean_similarity_delta=br['full_mean_similarity'] - ar['full_mean_similarity'] if ar and br else None, prefix_mean_similarity_delta=br['prefix_mean_similarity'] - ar['prefix_mean_similarity'] if ar and br else None))
    assert pool_count == len(pool_saved)
    equal(comparisons, read(MOUT / 'comparisons.json'))
    with gzip.open(MOUT / 'group-deltas.jsonl.gz', 'rt', encoding='utf-8') as f: equal(deltas, [json.loads(v) for v in f])
    assert set(report['summary']) == {'before_to_append', 'append_to_append_delete', 'before_to_append_delete'}
    summaries = {}
    for label in report['summary']:
        dr = [v for v in deltas if v['transition'] == label]; cr = [v for v in comparisons if v['transition'] == label]
        summaries[label] = dict(rows=len(dr), changed_pool=sum(not v['full_pool']['ordered_equal'] for v in dr), changed_prefix=sum(not v['q_prefix']['ordered_equal'] for v in dr),
            full_count_delta=distribution([v['full_pool']['after'] - v['full_pool']['before'] for v in dr]), prefix_count_delta=distribution([v['q_prefix']['after'] - v['q_prefix']['before'] for v in dr]),
            full_mean_similarity_delta=distribution([v['full_mean_similarity_delta'] for v in dr]), prefix_mean_similarity_delta=distribution([v['prefix_mean_similarity_delta'] for v in dr]),
            candidate_retained_fraction=distribution([v['candidates']['retained_fraction'] for v in cr]), candidate_mean_similarity_delta=distribution([v['candidate_mean_similarity_delta'] for v in cr]),
            users_with_candidate_change=sum(not v['candidates']['ordered_equal'] for v in cr), vanished_group_occurrences=sum(len(v['vanished_groups']) for v in cr), new_group_occurrences=sum(len(v['new_groups']) for v in cr))
    equal(summaries, report['summary'])
    return dict(status='PASS', users=20, scenarios=3, pool_rows=pool_count, group_delta_rows=len(deltas), user_comparisons=len(comparisons), source_seal=pin(MOUT / 'seal.json'), source_report=pin(MOUT / 'report.json'), summary=summaries,
                scope='All saved full pools, prefixes, candidate orders, similarities and summary cells independently reconstructed; exact mild fixtures only; no predictor calls.')


def run(review_path):
    review = read(review_path); assert review['status'] == 'PASS' and review['audit_code'] == pin(__file__)
    assert not DEST.exists(), 'Preserve previous audit attempt'
    begin = time.perf_counter(); assert pin(OUT / 'final-seal.json')['sha256'] == FINAL_SHA
    pins = {s: verify_stage(s) for s in ['prepare', 'cluster', 'final']}
    manifest = read(OUT / 'final/manifest.json'); inv = read(OUT / 'final/invariance.json'); cfg = read(DOC / 'config.json')
    for stage, p in manifest['source_seals'].items(): assert pin(OUT / (stage + '-seal.json')) == p
    freview = read(DOC / 'final-execution-review.json'); assert freview['status'] == 'PASS'
    for path, expected in freview['fingerprint']['files'].items():
        assert pin(path) == expected
        assert pin(OUT / 'reviewed-contracts/final' / Path(path).name) == expected
    assert pin(OUT / 'reviewed-contracts/final/final-execution-review.json') == pin(DOC / 'final-execution-review.json')
    for name, expected in manifest['code'].items(): assert pin(ROOT / 'scripts' / name) == expected
    with (OUT / 'final/bundle.pkl').open('rb') as f: bundle = pickle.load(f)
    assert pin(OUT / 'final/bundle.pkl') == manifest['bundle']; assert material(bundle) == manifest['rules_digest']
    oldout = Path(cfg['previous_root']) / 'outputs/fixed-k8-discovery'
    assert pin(oldout / 'final/bundle.pkl') == manifest['top_source_bundle']
    with (oldout / 'final/bundle.pkl').open('rb') as f: oldbundle = pickle.load(f)
    for key in ['top_centers', 'top_weights', 'top_names']:
        equal(bundle[key], oldbundle[key], key)
    for key in ['keyword_idf', 'keyword_components', 'text_components', 'genres', 'keywords']:
        equal(bundle['preprocessor'][key], oldbundle['preprocessor'][key], key)
    equal(bundle['preprocessor']['text_vectorizer'].vocabulary_, oldbundle['preprocessor']['text_vectorizer'].vocabulary_)
    equal(bundle['preprocessor']['text_vectorizer'].idf_, oldbundle['preprocessor']['text_vectorizer'].idf_)
    decision = read(OUT / 'sweep/decision.json'); check = read(OUT / 'check/report.json')
    assert bundle['policy'] == manifest['policy'] == decision['policies'][check['exemplar']]
    assert check['exemplar'] == decision['candidate'] and bundle['predictor'] == decision['predictor']
    with (OUT / 'cluster' / (bundle['policy']['hierarchy'] + '-hierarchy.pkl')).open('rb') as f:
        hierarchy = pickle.load(f)
    assert [ah(v) for v in bundle['child_centers']] == [ah(v) for v in hierarchy['centers']]
    equal(bundle['sub_weights'], hierarchy['sub_weights'])
    assert {k: ah(v) for k, v in bundle['representatives'].items()} == {k: ah(v) for k, v in hierarchy['representatives'].items()}
    equal(bundle['source_space_hashes'], hierarchy['space_hashes'])
    assert bundle['quality'] == read(OUT / 'prepare/quality-config.json')
    equal(bundle['runtime_rules'], {k: cfg[k] for k in bundle['runtime_rules']})
    frame = pd.read_parquet(OUT / 'prepare/catalog.parquet'); saved = pd.read_parquet(OUT / 'final/assignments.parquet')
    assert len(frame) == inv['all_catalog'] == 237817 and frame.service_movie_id.is_unique
    assert pin(OUT / 'final/assignments.parquet') == manifest['assignments']
    for key in ['service_movie_id', 'tmdb_id', 'movielens_movie_id', 'mapping_status', 'title', 'original_language', 'quality_state', 'raw_vote_average_number', 'raw_vote_count_number']:
        pd.testing.assert_series_equal(saved[key], frame[key], check_names=False)
    np.testing.assert_array_equal(saved.taste_id, frame.taste_id)
    np.testing.assert_array_equal(saved.group_id, hierarchy['groups'])
    np.testing.assert_array_equal(saved.child_id, hierarchy['children'])
    csv = pd.read_csv(OUT / 'final/assignments.csv.gz', keep_default_na=False)
    for col in saved:
        if pd.api.types.is_numeric_dtype(saved[col]):
            np.testing.assert_allclose(pd.to_numeric(csv[col], errors='coerce'), saved[col], rtol=1e-12, atol=1e-12, equal_nan=True)
        else:
            equal(csv[col].tolist(), saved[col].fillna('').tolist(), 'csv/' + col)
    del csv
    x = np.load(OUT / 'prepare/content.npy'); assert ah(x) == bundle['source_space_hashes']['GKT']
    np.testing.assert_array_equal(saved.Q, np.load(OUT / 'prepare/quality.npy'))
    names = read(oldout / 'prepare/names.json'); assert pin(oldout / 'prepare/names.json') == manifest['label_source']
    descriptors = read(OUT / 'final/descriptors.json'); frequency_notes = []
    assert len(descriptors) == hierarchy['n_groups']
    assert sorted(d['group_id'] for d in descriptors) == list(range(hierarchy['n_groups']))
    for d in descriptors:
        g = d['group_id']; rows = np.flatnonzero(saved.group_id.to_numpy() == g)
        genres = Counter(int(k) for ids in frame.genre_ids.iloc[rows] for k in ids)
        keywords = Counter(int(k) for ids in frame.keyword_ids.iloc[rows] for k in ids)
        gs = [names['genres'].get(str(k), str(k)) for k, _ in genres.most_common(3)]
        ks = [names['keywords'].get(str(k), str(k)) for k, _ in keywords.most_common(5)]
        label = ' / '.join(gs) if gs else 'Insufficient genre evidence'
        equal(d, dict(group_id=g, taste_id=int(saved.taste_id.iloc[rows[0]]) if len(rows) else None, members=len(rows), content_supported=int(saved.content_supported.iloc[rows].sum()), top_supported=int(saved.top_supported.iloc[rows].sum()), display_label=label, genres=gs, keywords=ks, label_is_empirical_summary_not_semantic_truth=True))
        equal(bundle['group_names'][str(g)], label)
        frequency_notes.append(dict(group_id=g, members=len(rows), top_genres=[dict(name=names['genres'].get(str(k), str(k)), count=v) for k, v in genres.most_common(3)]))
    equal(saved.taste_name.tolist(), [bundle['top_names'][str(v)] for v in saved.taste_id])
    equal(saved.group_name.tolist(), [bundle['group_names'][str(v)] for v in saved.group_id])
    equal(set(saved.version), {cfg['version']})
    print('PINS_ASSIGNMENTS_DESCRIPTORS_PASS', flush=True)
    ascending = np.arange(len(frame)); reverse = ascending[::-1]
    geometry = dict(independent_batch6553=check_assign(frame, bundle, saved, ascending, 6553))
    with (OUT / 'final/bundle.pkl').open('rb') as f: reloaded = pickle.load(f)
    geometry['independent_reverse_reload_batch4093'] = check_assign(frame, reloaded, saved, reverse, 4093)
    sample = np.random.default_rng(cfg['seed']).permutation(len(frame))[:1024]
    geometry['independent_shuffled_batch17'] = check_assign(frame, reloaded, saved, sample, 17)
    print('INDEPENDENT_GEOMETRY_PASS', geometry, flush=True)
    contexts = past_contexts(); by_uid = {c['uid']: c for c in contexts if c['cap'] == 10}
    removed = [int(v) for v in bundle['representatives']['medoid_ids'] if int(v) > 0][:8]
    firstuid = inv['actual_serving_request_parity'][0]['uid']; ids = frame.service_movie_id.to_numpy()
    removed = sorted(set(removed + [int(ids[by_uid[firstuid]['history'][0]])]))
    equal(removed, inv['removed_medoid_or_history_ids'])
    added = added_rows(frame); new_assigned, new_x = assign(added, bundle)
    assert new_assigned['content_supported'].tolist() == [True, False, False]
    keep = np.flatnonzero(~frame.service_movie_id.isin(removed).to_numpy())
    geometry['independent_delete_reclassify_batch8191'] = check_assign(frame, bundle, saved, keep, 8191)
    equal(len(keep), inv['delete_existing_reclassified'])
    mutation = audit_mutation(frame, saved, x, bundle, contexts, removed, added, new_assigned, new_x)
    print('MUTATION_ALL_ROWS_PASS', mutation['pool_rows'], flush=True)
    # The final stage's smaller rank-only perturbation record used the mutated pool.
    roles = read(OUT / 'prepare/roles.json'); requests = pd.read_parquet(OUT / 'check/requests.parquet', columns=['policy', 'uid', 'candidate_count', 'returned', 'ranked', 'ranked_prediction'])
    served = requests[requests.policy.eq(bundle['policy']['id']) & requests.candidate_count.gt(0) & requests.returned.gt(0)]
    selected = [c for c in contexts if c['cap'] == 10 and c['uid'] in roles['verification'] and len(c['history']) == c['original_input_count'] and profile(x, c)[1] and c['uid'] in set(served.uid)]
    equal(len(selected), inv['serving_eligible_check_users'])
    combined = pd.concat([frame, added], ignore_index=True); xx = np.concatenate([x, new_x]); groups = np.r_[saved.group_id, new_assigned['group_id']]
    _, order = quality(combined, bundle, xx, ~combined.service_movie_id.isin(removed).to_numpy())
    perturbations = []
    for j, c in enumerate(selected):
        p, pools, snap, _ = reconstruct(c, combined, xx, groups, order, bundle)
        legal = np.asarray(sorted(pools), int)
        if not len(legal): continue
        donor = selected[(j + 1) % len(selected)]; other, _ = profile(xx, donor)
        def top(direction):
            scores = bundle['representatives']['mean'][legal] @ direction
            return legal[np.lexsort((legal, -scores))[:5]].tolist()
        base, swap, neg = top(p), top(other), top(-p)
        perturbations.append(dict(uid=c['uid'], donor_uid=donor['uid'], groups=base, swapped_groups=swap, sign_reversed_groups=neg, swap_top1_changed=base[0] != swap[0], reverse_top1_changed=base[0] != neg[0]))
    equal(perturbations, read(OUT / 'final/profile-perturbations.json'))
    # Execute the actual runtime only after the independent numerical checks.
    sys.path.insert(0, str(ROOT / 'scripts'))
    import dv2_runtime
    assert Path(dv2_runtime.__file__).resolve() == (ROOT / 'scripts/dv2_runtime.py').resolve()
    assert dv2_runtime.runtime_versions() == bundle['runtime_versions']
    model = dv2_runtime.FrozenCatalog(frame, bundle)
    for key in ['taste_id', 'child_id', 'group_id', 'top_supported', 'content_supported']:
        np.testing.assert_array_equal(model.assigned[key], saved[key])
    model_calls = []; replay = []
    original_predict = model.predictor.predict
    def counted(context, indices, variant='original'):
        model_calls.append(dict(rows=len(indices), variant=variant))
        return original_predict(context, indices, variant)
    model.predictor.predict = counted
    for original in inv['actual_serving_request_parity']:
        c = by_uid[original['uid']]; ratings = [dict(service_movie_id=int(ids[i]), stars=float(v)) for i, v in zip(c['history'], c['stars'])]
        result = model.recommend(ratings, ids[c['viewed']].tolist(), 10)
        expected = served[served.uid.eq(c['uid'])].iloc[0]
        equal([v['service_movie_id'] for v in result['movies']], ids[np.asarray(expected.ranked, int)])
        got_scores = np.asarray([v['ranking_score'] for v in result['movies']]); want_scores = np.asarray(expected.ranked_prediction)
        np.testing.assert_allclose(got_scores, want_scores, rtol=1e-12, atol=1e-12)
        equal(dict(uid=c['uid'], returned=len(result['movies']), predicted_movies=result['predicted_movies'], state=result['state']), original)
        assert result['global_fill'] == 0 and result['als_rows'] + result['gbt_rows'] > 0
        replay.append(dict(uid=c['uid'], returned=len(result['movies']), candidates=result['predicted_movies'], max_abs_score_error=float(np.max(np.abs(got_scores - want_scores))), als_rows=result['als_rows'], gbt_rows=result['gbt_rows']))
    assert [v['uid'] for v in replay] == [1215, 2607, 2834]
    # Mutation of the in-memory runtime must preserve frozen material and histories.
    model.append(added)
    original_predict = model.predictor.predict
    model.predictor.predict = counted
    for key in ['taste_id', 'child_id', 'group_id', 'top_supported', 'content_supported']:
        np.testing.assert_array_equal(model.assigned[key][:len(frame)], saved[key])
        np.testing.assert_array_equal(model.assigned[key][-3:], new_assigned[key])
    before_active = model.active.copy()
    try: model.remove(removed + [int(ids.max()) + 1000000])
    except ValueError: pass
    else: raise AssertionError('Invalid batch accepted')
    np.testing.assert_array_equal(model.active, before_active)
    model.remove(removed)
    assert not set(model.frame.service_movie_id.iloc[model.engine.order]) & set(removed)
    assert set(removed) <= set(model.lookup)
    np.testing.assert_array_equal(model.engine.q[:len(frame)], np.load(OUT / 'prepare/quality.npy'))
    assert model.recommend([], [], 10)['profile']['state'] == 'ACTUAL_NO_HISTORY'
    c = by_uid[firstuid]; ratings = [dict(service_movie_id=int(ids[i]), stars=float(v)) for i, v in zip(c['history'], c['stars'])]
    assert model.recommend(ratings, ids[c['viewed']].tolist(), 0)['profile']['state'] == 'HIDDEN_CAP0'
    assert model_calls == [dict(rows=50, variant=bundle['predictor'])] * 3
    # No further inference is needed to establish active candidate removal.
    assert material(bundle) == manifest['rules_digest'] and pin(OUT / 'final/bundle.pkl') == manifest['bundle']
    for stage, expected in pins.items(): assert verify_stage(stage) == expected
    for stage, p in manifest['source_seals'].items(): assert pin(OUT / (stage + '-seal.json')) == p
    assert pin(MOUT / 'seal.json') == mutation['source_seal']
    assert read(MOUT / 'seal.json')['files'] == {p.name: pin(p) for p in sorted(MOUT.iterdir()) if p.is_file() and p.name != 'seal.json'}
    for path, expected in freview['fingerprint']['files'].items(): assert pin(path) == expected
    assert read(review_path)['audit_code'] == pin(__file__)
    result = dict(status='PASS', source_root=str(ROOT), source_pins=pins, source_final_manifest=pin(OUT / 'final/manifest.json'), audit_code=pin(__file__), execution_review=pin(review_path),
                  scope='Independent post-execution final geometry/assignment/descriptor/perturbation and mutation result audit; independent invocation of pinned actual serving runtime for three saved requests.',
                  geometry=geometry, final_profile_perturbation_rows=len(perturbations), descriptor_rows=len(descriptors), descriptor_genre_frequencies=frequency_notes,
                  actual_serving_replay=replay, trained_model_request_calls=model_calls, total_predicted_candidate_rows=sum(v['rows'] for v in model_calls), mutation=mutation,
                  runtime_append_tombstone_checks='PASS; existing classification and Q unchanged; removed candidates excluded; historical metadata retained; invalid remove atomic; empty/cap0 states correct',
                  future_ratings_parsed=0, seconds=time.perf_counter() - begin,
                  limitations=['Actual serving replay reuses the pinned runtime/predictor implementation; prior predictor native/source parity is separate evidence.', 'No timing/production performance assertion from this replay.', 'Mutation fixture is exactly the existing clone/null/OOV append and nine tombstones; zero candidate change does not prove future drift absence.', 'Descriptors are frequency summaries; rare secondary genres must not be presented as dominant group semantics.', 'Human satisfaction and untouched temporal test performance remain unmeasured.'])
    DEST.parent.mkdir(parents=True, exist_ok=True)
    DEST.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(dict(status='PASS', output=str(DEST), pin=pin(DEST), seconds=result['seconds']), ensure_ascii=True), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--execute', action='store_true'); parser.add_argument('--review', type=Path)
    args = parser.parse_args()
    if args.execute:
        assert args.review
        with threadpool_limits(limits=2): run(args.review)
    else: print(json.dumps(dict(audit_code=pin(__file__), final_seal_sha256=FINAL_SHA), indent=2))
