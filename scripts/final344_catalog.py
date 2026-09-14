"""Full fixed catalog stress test, each selected seed scored separately.

Timing is a component sum: shared feature generation + a model's prediction +
sorting. All successful selected models are loaded together. Initialization,
file I/O and production request overhead are excluded; this is not standalone
service latency or a multi-node benchmark.
"""
import os
for key in ['OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS']:
    os.environ[key] = '4'
import time
import numpy as np
import pandas as pd
from final344_common import *
from foundation340_features import Features
from combination340_models import Trees

class FM:
    def __init__(self, path):
        a = np.load(path)
        require(np.array_equal(a['indices'], np.arange(230)), 'FM fixed feature axis')
        self.f, self.w, self.b = a['factors'].astype(float), a['linear'].astype(float), float(a['intercept'])
    def predict(self, x):
        x = np.asarray(x, np.float64)
        return self.b + x @ self.w + .5 * ((x @ self.f)**2 - (x * x) @ (self.f * self.f)).sum(1)

def gate():
    reviewed('catalog'); lock(); verify('selection-seal.json')
    require(read(DOC / 'execution.json')['evaluation_scope'] == 'DEVELOPMENT_ONLY', 'development catalog scope')
    require(pin(OLD / 'labels.parquet')['sha256'] == 'e3bf301a6e2ea7885b59bcab7fe83c2d3ad84f93f2f1bbb2d54d943b9a658db8', 'known development labels')
    return {'execution': fingerprint('catalog'), 'input_lock': pin(OUT / 'input-lock.json'),
            'selection_seal': pin(OUT / 'selection-seal.json')}

def catalog():
    started = gate(); t = time.monotonic()
    require(not (OUT / 'catalog-cache').exists(), 'preserve full catalog predictions')
    selected = read(OUT / 'selection.json')['selected']; models = {}; model_info = {}
    audit_path = OUT / 'final-model-audit-seal.json'
    if not audit_path.exists(): audit_path = OUT / 'model-audit-seal.json'
    audit = read(audit_path); require(audit['status'] == 'PASS', 'independent models audited')
    audit_pin = pin(audit_path); fit_pins = {}
    for family, recipe in selected.items():
        if recipe is None: continue
        for seed in SEEDS:
            fit_id = recipe + '_s' + str(seed); root = OUT / fit_id
            if not (root / 'outcome.json').exists() or read(root / 'outcome.json')['status'] != 'SUCCESS': continue
            verify(fit_id + '-seal.json')
            require(audit['fit_seals'].get(fit_id) == pin(OUT / (fit_id + '-seal.json')), 'audited catalog model')
            fit_pins[fit_id] = pin(OUT / (fit_id + '-seal.json'))
            models[fit_id] = FM(root / 'model/portable.npz') if family == 'FM' else Trees(root / 'model/native', range(230))
            model_info[fit_id] = {'family': family, 'recipe': recipe, 'seed': seed,
                                  'resource_status': read(root / 'outcome.json')['resource_status'],
                                  'native_bytes': sum(p.stat().st_size for p in (root / 'model/native').rglob('*') if p.is_file())}
    require(bool(models), 'at least one audited selected model')
    cats = pd.read_parquet(OLD / 'catalog.parquet'); ids = cats.movie_id.to_numpy()
    meta = pd.read_parquet(BASE / 'rec-ev-045/metadata.parquet')
    require(len(cats) == 85517 and np.array_equal(meta.movie_id, ids), 'same full metadata axis')
    dates = pd.to_datetime(pd.read_parquet(OLD / 'texts.parquet', columns=['release_date']).release_date, format='%Y-%m-%d', errors='coerce', utc=True)
    eligible_base = dates.notna().to_numpy() & (dates.astype('int64').to_numpy() // 10**9 <= 1788998400)
    roles = pd.read_csv(OUT / 'roles.csv'); uids = set(roles.loc[roles.role.eq('comparison'), 'uid'])
    contexts = sorted([c for c in read(OLD / 'contexts.json') if c['cap'] == 10 and c['uid'] in uids], key=lambda c: c['uid'])
    require(len(contexts) == 180, '180 comparison users at cap10')
    features = Features(meta, cats); rows = []; costs = []; supply = []
    old_seal = read(COMBO / 'catalog-seal.json')
    require(pin(COMBO / 'catalog-seal.json')['sha256'] == 'd537e976c635407027ed3f0b03472e2e7ad2a9452ee33596944dce9383ec8006', 'old candidate cache anchor')
    (OUT / 'catalog-cache').mkdir()
    for number, c in enumerate(contexts):
        allowed = eligible_base.copy(); allowed[np.asarray(c['viewed'], int)] = False; ei = np.flatnonzero(allowed)
        previous = COMBO / 'catalog-cache' / (str(c['uid']) + '.npz')
        require(pin(previous) == old_seal['files']['catalog-cache/' + previous.name], 'old candidate cache pin')
        with np.load(previous) as old:
            require(np.array_equal(ei, old['ei']), 'same full eligible candidate set')
        vals = {n: np.empty(len(ei)) for n in models}; feature_seconds = 0.0; pred_seconds = {n: 0.0 for n in models}
        # Cyclic model order reduces a systematic first/last timing position advantage.
        order_models = list(models); order_models = order_models[number % len(models):] + order_models[:number % len(models)]
        for pos in range(0, len(ei), 8192):
            chunk = ei[pos:pos+8192]; sl = slice(pos, pos+len(chunk)); clock = time.perf_counter()
            x = features.reference(c['oi'], c['stars'], chunk, True); feature_seconds += time.perf_counter() - clock
            require(x.shape == (len(chunk), 230) and np.isfinite(x).all(), 'complete finite candidate features')
            for name in order_models:
                clock = time.perf_counter(); vals[name][sl] = models[name].predict(x); pred_seconds[name] += time.perf_counter() - clock
        np.savez_compressed(OUT / 'catalog-cache' / (str(c['uid']) + '.npz'), ei=ei.astype(np.int32), **vals)
        for name, v in vals.items():
            require(np.isfinite(v).all(), 'no nonfinite full-catalog predictions')
            clock = time.perf_counter(); ix = np.lexsort((ids[ei], -v))[:6]; sort_seconds = time.perf_counter() - clock
            for rank, j in enumerate(ix, 1):
                rows.append({'uid': c['uid'], 'h': c['h'], 'model': name, 'rank': rank,
                             'movie_id': int(ids[ei[j]]), 'prediction': float(v[j]), **model_info[name]})
            costs.append({'uid': c['uid'], 'h': c['h'], 'model': name, 'candidates': len(ei),
                          'feature_seconds_shared': feature_seconds, 'prediction_seconds': pred_seconds[name],
                          'top6_seconds': sort_seconds, 'component_total_seconds': feature_seconds+pred_seconds[name]+sort_seconds})
            supply.append({'uid': c['uid'], 'h': c['h'], 'model': name, 'candidates': len(ei), 'finite_scores': len(v)})
        print('CATALOG', number+1, '/180', round(time.monotonic()-t, 1), flush=True)
    top = pd.DataFrame(rows)
    require(not top.duplicated(['uid', 'model', 'movie_id']).any(), 'no duplicate recommendation')
    ix = np.searchsorted(ids, top.movie_id); require(np.array_equal(ids[ix], top.movie_id), 'top movie axis')
    labels = pd.read_parquet(OLD / 'labels.parquet', columns=['uid', 'movie_id'])
    top['unknown'] = ~pd.MultiIndex.from_frame(top[['uid', 'movie_id']]).isin(pd.MultiIndex.from_frame(labels))
    top['one_vote_ten'] = (meta.tmdb_vote_count.to_numpy()[ix] == 1) & (meta.tmdb_vote_average.to_numpy()[ix] == 10)
    top['support'] = cats.train_count.to_numpy()[ix]; top['blocked'] = cats.blocked.to_numpy()[ix]
    top['release_year'] = dates.dt.year.to_numpy()[ix]; top['tmdb_vote_count'] = meta.tmdb_vote_count.to_numpy()[ix]
    missing = ['genre_ids', 'keyword_ids', 'director_ids', 'top5_cast_ids']
    for f in missing: top[f+'_missing'] = meta[f].map(len).eq(0).to_numpy()[ix]
    top.to_parquet(OUT / 'catalog-top6.parquet', index=False)
    costs = pd.DataFrame(costs); costs.to_csv(OUT / 'catalog-timing.csv', index=False)
    pd.DataFrame(supply).to_csv(OUT / 'catalog-supply.csv', index=False)
    summary = []
    for hgroup in ['ALL', 'H_POSITIVE', 'H_ZERO']:
        users = {c['uid'] for c in contexts if hgroup == 'ALL' or (c['h'] > 0) == (hgroup == 'H_POSITIVE')}
        sub = top[top.uid.isin(users)]
        for name in models:
            for view in ['cumulative', 'page']:
                for end in [2, 4, 6]:
                    lower = 1 if view == 'cumulative' else end-1
                    a = sub[sub.model.eq(name) & sub['rank'].between(lower, end)]
                    counts = a.movie_id.value_counts(); returned = len(a); frac = counts / max(returned, 1)
                    summary.append({'h_group': hgroup, 'model': name, 'view': view, 'end': end, 'users': len(users),
                                    'slots': len(users)*(end-lower+1), 'returned': returned,
                                    'missing': len(users)*(end-lower+1)-returned, 'duplicates': 0,
                                    'unknown': int(a.unknown.sum()), 'one_vote_ten': int(a.one_vote_ten.sum()),
                                    'unique_movies': len(counts), 'hhi': float((frac**2).sum()) if returned else np.nan,
                                    'max_share': float(frac.max()) if returned else np.nan,
                                    'support0': int(a.support.eq(0).sum()), 'support1_9': int(a.support.between(1, 9).sum()),
                                    'support10_49': int(a.support.between(10, 49).sum()), 'support50plus': int(a.support.ge(50).sum()),
                                    'blocked': int(a.blocked.sum()), 'release2024plus': int(a.release_year.ge(2024).sum()),
                                    **{f+'_missing': int(a[f+'_missing'].sum()) for f in missing}, **model_info[name]})
    pd.DataFrame(summary).to_csv(OUT / 'catalog-summary.csv', index=False)
    timing = []
    for name, frame in costs.groupby('model'):
        for col in ['feature_seconds_shared', 'prediction_seconds', 'top6_seconds', 'component_total_seconds']:
            timing.append({'model': name, 'metric': col, 'users': len(frame), 'p50': frame[col].quantile(.5), 'p95': frame[col].quantile(.95), **model_info[name]})
    pd.DataFrame(timing).to_csv(OUT / 'catalog-timing-summary.csv', index=False)
    require(started == gate(), 'catalog code/input drift')
    current_audit_path = OUT / 'final-model-audit-seal.json'
    if not current_audit_path.exists(): current_audit_path = OUT / 'model-audit-seal.json'
    require(current_audit_path == audit_path and pin(audit_path) == audit_pin, 'same model audit throughout catalog scoring')
    for fit_id, expected in fit_pins.items():
        verify(fit_id + '-seal.json')
        require(pin(OUT / (fit_id + '-seal.json')) == expected, 'unchanged scored model fit')
    files = ['catalog-top6.parquet', 'catalog-timing.csv', 'catalog-supply.csv', 'catalog-summary.csv', 'catalog-timing-summary.csv']
    files += [p.relative_to(OUT).as_posix() for p in sorted((OUT / 'catalog-cache').glob('*.npz'))]
    seal('catalog-seal.json', files, execution=started['execution'], input_snapshot=started,
         model_audit=audit_pin, fit_seals=fit_pins, selection=started['selection_seal'], seconds=time.monotonic()-t)
    print('CATALOG_COMPLETE', round(time.monotonic()-t, 1), flush=True)

if __name__ == '__main__': catalog()
