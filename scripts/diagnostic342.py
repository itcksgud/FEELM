"""Three descriptive diagnostics; consume sealed predictions, never fit or infer."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'outputs/recommendation-evidence'
DOC = ROOT / 'docs/recommendation/experiments/diagnostic342'
OUT = BASE / 'diagnostic342'
CONTENT = ['S', 'R', 'H', 'RH', 'NO_RESPONSE', 'GBT',
           'BLEND_0.25', 'BLEND_0.5', 'BLEND_0.75', 'BLEND_1.0']
BLENDS = CONTENT[-4:]
WINDOWS = [(kind, end) for kind in ['page', 'cumulative'] for end in [2, 4, 6]]
SEALS = {
    'text339/prepared-seal.json': 'd27709a42bd0d5511a36f3740365637325d59b521c7f62eae04850523782fdd8',
    'foundation340/evaluation-seal.json': 'cacf9f17a9c20c8d12f6ec75466d163b40b59b6acf3168094701f0585be316e7',
    'combination340/evaluation-seal.json': 'cef4695acf99814d57f6bdc030004bce71212fcb28821916aa54066a0b6a25f1',
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def pin(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return {'bytes': Path(path).stat().st_size, 'sha256': h.hexdigest()}


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def fingerprint():
    return {p.relative_to(ROOT).as_posix(): pin(p) for p in [Path(__file__), DOC / 'DESIGN.md']}


def verify_inputs():
    pins = {}

    def check(path, expected):
        actual = pin(path)
        require(actual == expected if isinstance(expected, dict) else actual['sha256'] == expected,
                'input identity: ' + str(path))
        pins[Path(path).relative_to(ROOT).as_posix()] = actual

    for name, sha in SEALS.items():
        check(BASE / name, sha)
    prep = read_json(BASE / 'text339/prepared-seal.json')
    for name in ['contexts.json', 'catalog.parquet', 'texts.parquet']:
        check(BASE / 'text339' / name, prep['files'][name])
    for stage in ['foundation340', 'combination340']:
        p = BASE / stage
        ev = read_json(p / 'evaluation-seal.json')
        check(p / 'fit-seal.json', ev['fit_seal'])
        check(p / 'catalog-seal.json', ev['catalog_seal'])
        check(p / 'predictions.npz', read_json(p / 'fit-seal.json')['files']['predictions.npz'])
        cat = read_json(p / 'catalog-seal.json')
        for name, expected in cat['files'].items():
            if name == 'catalog-top10.parquet' or name.startswith('catalog-cache/'):
                check(p / name, expected)
        for name in ['metrics.csv', 'user-metrics.parquet']:
            check(p / name, ev['files'][name])
        check(BASE / 'text339/labels.parquet', ev['labels'])
    check(BASE / 'rec-ev-045/metadata.parquet',
          '4d838874938115be7a4b1f629a920dd196e082b655b75d559d71039e52eb7d8d')
    return pins


def order(scores, ids):
    return np.lexsort((ids, -scores))


def page_quality(truth, scores, ids, kind, end):
    start = end - 2 if kind == 'page' else 0
    ranked = order(scores, ids)
    shown = truth[ranked[start:end]]
    result = {'j': len(truth), 'returned': len(shown), 'missing': end - start - len(shown),
              'stars': np.nan, 'low': np.nan, 'good': np.nan, 'both_low': np.nan,
              'any_good': np.nan, 'ndcg': np.nan}
    if len(truth) < end:
        return result
    result.update(stars=float(shown.mean()), low=float(np.mean(shown <= 2)),
                  good=float(np.mean(shown >= 4)), any_good=float(np.any(shown >= 4)))
    if kind == 'page':
        result['both_low'] = float(np.all(shown <= 2))
    else:
        gains = (truth - .5) / 4.5
        discounts = 1 / np.log2(np.arange(end) + 2)
        ideal = np.sort(gains)[::-1][:end] @ discounts
        if ideal > 0:
            result['ndcg'] = float(gains[ranked[:end]] @ discounts / ideal)
    return result


def user_groups(h):
    return ['ALL_USERS', 'H_POSITIVE' if h > 0 else 'H_ZERO']


def summarize_pages(rows):
    frame = pd.DataFrame(rows)
    tables = []
    keys = ['cap', 'pool', 'variant', 'kind', 'end']
    metrics = ['returned', 'missing', 'stars', 'low', 'good', 'both_low', 'any_good', 'ndcg']
    for group in ['ALL_USERS', 'H_POSITIVE', 'H_ZERO']:
        a = frame if group == 'ALL_USERS' else frame[frame.h.gt(0) if group == 'H_POSITIVE' else frame.h.eq(0)]
        for cohort in ['natural', 'common_j6']:
            b = a if cohort == 'natural' else a[a.j.ge(6)]
            long = b.melt(id_vars=keys, value_vars=metrics, var_name='metric')
            result = long.groupby(keys + ['metric'], sort=True).value.agg(users='size', valid='count', mean='mean').reset_index()
            result['h_group'] = group
            result['cohort'] = cohort
            tables.append(result)
    return frame, pd.concat(tables, ignore_index=True)


def boundary_counts(base, after, ids, warm, other):
    """Count W-vs-fixed-U rank changes in O(n log n), including ID ties."""
    require(not np.any(warm & other), 'disjoint boundary sets')
    require(np.array_equal(base[other], after[other]), 'other boundary scores unchanged')
    counts = []
    for values in [base, after]:
        rank = order(values, ids)
        ahead = np.cumsum(other[rank])
        by_item = np.empty(len(rank), dtype=np.int64)
        by_item[rank] = ahead
        counts.append(by_item[warm])
    before, later = counts
    return {'pairs': int(warm.sum()) * int(other.sum()),
            'flips': int(np.abs(later - before).sum()),
            'warm_promoted_pairs': int(np.maximum(before - later, 0).sum()),
            'warm_demoted_pairs': int(np.maximum(later - before, 0).sum())}


def observed_boundary(truth, base, after, ids, warm, other):
    result = boundary_counts(base, after, ids, warm, other)
    a, b = np.flatnonzero(warm), np.flatnonzero(other)
    if not len(a) or not len(b):
        return dict(result, unequal_pairs=0, correct_before=0, correct_after=0, fixed=0, broken=0)
    wins = []
    for score in [base, after]:
        wins.append((score[a, None] > score[b]) | ((score[a, None] == score[b]) & (ids[a, None] < ids[b])))
    require(int(np.count_nonzero(wins[0] != wins[1])) == result['flips'], 'boundary direct verification')
    unequal = truth[a, None] != truth[b]
    target = truth[a, None] > truth[b]
    old_ok, new_ok = (x == target for x in wins)
    result.update(unequal_pairs=int(unequal.sum()), correct_before=int((old_ok & unequal).sum()),
                  correct_after=int((new_ok & unequal).sum()), fixed=int((~old_ok & new_ok & unequal).sum()),
                  broken=int((old_ok & ~new_ok & unequal).sum()))
    return result


def metadata_flags(meta, cats, texts):
    votes = meta.tmdb_vote_count.to_numpy(float)
    count = cats.train_count.to_numpy()
    dates = pd.to_datetime(texts.release_date, errors='coerce', utc=True)
    flags = {
        'one_vote_ten': (votes == 1) & meta.tmdb_vote_average.eq(10).to_numpy(),
        'votes_missing': ~np.isfinite(votes), 'votes_0': votes == 0,
        'votes_1_9': (votes >= 1) & (votes < 10), 'votes_10_49': (votes >= 10) & (votes < 50),
        'votes_50_plus': votes >= 50,
        'support_0': count == 0, 'support_1_9': (count >= 1) & (count < 10),
        'support_10_49': (count >= 10) & (count < 50), 'support_50_plus': count >= 50,
        'blocked_C': cats.blocked.to_numpy(),
        'natural_zero': ~cats.blocked.to_numpy() & (count == 0),
        'release_missing': dates.isna().to_numpy(),
        'release_after_origin': dates.gt(pd.Timestamp('2023-01-01', tz='UTC')).to_numpy(),
        'release_2024_plus': dates.ge(pd.Timestamp('2024-01-01', tz='UTC')).to_numpy(),
        'runtime_missing': meta.runtime_minutes.isna().to_numpy(),
        'language_missing': meta.original_language.fillna('').eq('').to_numpy(),
    }
    for field in ['genre_ids', 'director_ids', 'top5_cast_ids', 'keyword_ids']:
        flags[field + '_missing'] = meta[field].map(len).eq(0).to_numpy()
    return flags


def top_changes(base, after, ids, warm, blocked, truth=None):
    before, later = order(base, ids)[:2], order(after, ids)[:2]
    entered = np.setdiff1d(later, before)
    left = np.setdiff1d(before, later)
    out = {'j': len(ids), 'common_top2': len(set(before) & set(later)), 'replacements': len(entered)}
    for label, ix in [('entered', entered), ('left', left)]:
        out[label + '_warm'] = int(warm[ix].sum())
        out[label + '_C'] = int(blocked[ix].sum())
        out[label + '_other'] = int((~warm[ix] & ~blocked[ix]).sum())
    out['stars_delta'] = float(truth[later].mean() - truth[before].mean()) if truth is not None and len(ids) >= 2 else np.nan
    if len(ids) < 2:
        # An incomplete list is not a measured two-film replacement event.
        out = {key: value if key == 'j' else np.nan for key, value in out.items()}
    return out


def calibration_rows(c, truth, ei, vectors, actual, cats):
    blocked = cats.blocked.to_numpy()[ei]
    counts = cats.train_count.to_numpy()[ei]
    pools = {'ALL': np.ones(len(ei), bool), 'C': blocked,
             'W_DIRECT': (counts > 0) & ~blocked & actual,
             'NATURAL_ZERO': (counts == 0) & ~blocked}
    errors, bins = [], []
    edges = [-np.inf, .5, 1, 2, 3, 4, 5, np.inf]
    names = ['LT_0.5', '0.5_1', '1_2', '2_3', '3_4', '4_5', 'GE_5']
    for name, vector in vectors.items():
        for pool, mask in pools.items():
            available = mask & np.isfinite(vector)
            p, y = vector[available], truth[available]
            common = {'uid': c['uid'], 'h': c['h'], 'variant': name, 'pool': pool,
                      'targets': int(mask.sum()), 'supported': len(p)}
            row = dict(common, signed_error=np.nan, raw_mse=np.nan, raw_mae=np.nan, mse=np.nan, mae=np.nan,
                       predicted=np.nan, truth=np.nan, outside_scale=np.nan)
            if len(p):
                error = p - y
                clipped = np.clip(p, .5, 5) - y
                row.update(signed_error=float(error.mean()), raw_mse=float(np.mean(error**2)), raw_mae=float(abs(error).mean()),
                           mse=float(np.mean(clipped**2)), mae=float(abs(clipped).mean()), predicted=float(p.mean()),
                           truth=float(y.mean()), outside_scale=float(np.mean((p < .5) | (p > 5))))
                for j, label in enumerate(names):
                    select = (p >= edges[j]) & (p < edges[j+1])
                    if select.any():
                        bins.append(dict(common, score_bin=label, rows=int(select.sum()), predicted=float(p[select].mean()),
                                         truth=float(y[select].mean()), signed_error=float((p[select]-y[select]).mean())))
            errors.append(row)
    return errors, bins


def summarize_numeric(frame, keys, values, weighting=None):
    tables = []
    for group in ['ALL_USERS', 'H_POSITIVE', 'H_ZERO']:
        a = frame if group == 'ALL_USERS' else frame[frame.h.gt(0) if group == 'H_POSITIVE' else frame.h.eq(0)]
        for key, b in a.groupby(keys, sort=True):
            row = dict(zip(keys, key if isinstance(key, tuple) else (key,)))
            row.update(h_group=group, users=int(b.uid.nunique()), user_rows=len(b))
            for value in values:
                valid = b[value].notna()
                row[value + '_valid'] = int(valid.sum())
                row[value + '_mean'] = float(b.loc[valid, value].mean()) if valid.any() else np.nan
                if weighting and valid.any():
                    weights = b.loc[valid, weighting].to_numpy()
                    row[value + '_pooled'] = float(np.average(b.loc[valid, value], weights=weights)) if weights.sum() else np.nan
            tables.append(row)
    return pd.DataFrame(tables)


def run():
    start_time = time.monotonic()
    review = read_json(DOC / 'execution-review.json')
    require(review['status'] == 'PASS' and review['fingerprint'] == fingerprint(), 'independent current execution review')
    require(not OUT.exists(), 'preserve diagnostic342 outputs')
    inputs = verify_inputs()
    OUT.mkdir(parents=True)
    write_json(OUT / 'input-manifest.json', inputs)
    contexts = read_json(BASE / 'text339/contexts.json')
    cats = pd.read_parquet(BASE / 'text339/catalog.parquet')
    meta = pd.read_parquet(BASE / 'rec-ev-045/metadata.parquet')
    texts = pd.read_parquet(BASE / 'text339/texts.parquet', columns=['release_date'])
    labels = pd.read_parquet(BASE / 'text339/labels.parquet')
    ids = cats.movie_id.to_numpy()
    require(np.array_equal(ids, meta.movie_id) and len(ids) == 85517 and np.all(np.diff(ids) > 0), 'movie axes')
    require(not labels.duplicated(['uid', 'movie_id']).any() and np.isin(labels.rating, np.arange(1, 11)/2).all(), 'observed half-star labels')
    lookup = labels.set_index(['uid', 'movie_id']).rating.to_dict()
    with np.load(BASE / 'foundation340/predictions.npz') as f, np.load(BASE / 'combination340/predictions.npz') as c:
        f_names, f_pred = f['names'].tolist(), f['predictions']
        c_names, c_pred = c['names'].tolist(), c['predictions']
        direct, ref_direct = c['actual_direct'], c['reference_direct']
    require(f_pred.shape == (93230, 4) and c_pred.shape == (93230, 9), 'prediction dimensions')
    require(np.array_equal(f_pred[:, f_names.index('B')], c_pred[:, c_names.index('S')]), 'S equals B')
    vectors = {name: c_pred[:, j] for j, name in enumerate(c_names)}
    vectors.update({name: f_pred[:, f_names.index(name)] for name in ['R', 'H', 'RH']})
    flags = metadata_flags(meta, cats, texts)
    pages, errors, bins, cross, changes, distribution, score_stats = [], [], [], [], [], [], []
    h10 = {c['uid']: c for c in contexts if c['cap'] == 10}
    require(len(h10) == 270 and sum(c['h'] > 0 for c in h10.values()) == 186, 'H10 cohort')

    def distribution_row(c, ei, source):
        return dict(uid=c['uid'], h=c['h'], source=source, rows=len(ei),
                    **{name: float(v[ei].mean()) if len(ei) else np.nan for name, v in flags.items()})

    for c in contexts:
        ei = np.asarray(c['ei'], int)
        sl = slice(c['start'], c['stop'])
        require(c['stop'] - c['start'] == len(ei) and len(set(ei)) == len(ei), 'context row axis')
        truth = np.array([lookup[(c['uid'], int(ids[i]))] for i in ei])
        local = {name: value[sl] for name, value in vectors.items()}
        actual = direct[sl]
        require(np.array_equal(np.isfinite(local['ACTUAL_ALS']), actual), 'actual support identity')
        require(np.array_equal(np.isfinite(local['REFERENCE_ALS']), ref_direct[sl]), 'reference support identity')
        for name in BLENDS:
            require(np.array_equal(local[name][~actual], local['S'][~actual]), 'observed fallback identity')
        blocked = cats.blocked.to_numpy()[ei]
        counts = cats.train_count.to_numpy()[ei]
        pools = {'ALL': np.ones(len(ei), bool)}
        if c['cap'] == 10:
            pools.update(C=blocked, W_DIRECT=actual & ~blocked & (counts > 0), NATURAL_ZERO=~blocked & (counts == 0))
        for pool, mask in pools.items():
            names = CONTENT + (['ACTUAL_ALS', 'REFERENCE_ALS'] if pool == 'W_DIRECT' else [])
            for name in names:
                p, y, axis = local[name][mask], truth[mask], ids[ei[mask]]
                require(np.isfinite(p).all(), 'finite same-candidate ranking')
                for kind, end in WINDOWS:
                    pages.append(dict(uid=c['uid'], cap=c['cap'], h=c['h'], als_inputs=c['als_supported_inputs'],
                                      pool=pool, variant=name, kind=kind, end=end,
                                      **page_quality(y, p, axis, kind, end)))
        if c['cap'] != 10:
            continue
        e, b = calibration_rows(c, truth, ei, local, actual, cats)
        errors.extend(e); bins.extend(b)
        distribution.append(distribution_row(c, ei, 'OBSERVED_J'))
        for name in BLENDS:
            changes.append(dict(uid=c['uid'], h=c['h'], source='OBSERVED_J', variant=name,
                                **top_changes(local['S'], local[name], ids[ei], actual, blocked, truth)))
            for group, other in [('C', blocked), ('NATURAL_ZERO', ~blocked & (counts == 0))]:
                cross.append(dict(uid=c['uid'], h=c['h'], source='OBSERVED_J', variant=name, other=group,
                                  **observed_boundary(truth, local['S'], local[name], ids[ei], actual, other)))
    print('OBSERVED_COMPLETE', len(pages), flush=True)
    page_users, page_summary = summarize_pages(pages)
    page_users.to_parquet(OUT / 'page-user-metrics.parquet', index=False)
    page_summary.to_csv(OUT / 'page-summary.csv', index=False)
    error_frame, bin_frame = pd.DataFrame(errors), pd.DataFrame(bins)
    error_frame.to_parquet(OUT / 'error-users.parquet', index=False)
    bin_frame.to_parquet(OUT / 'calibration-bin-users.parquet', index=False)
    summarize_numeric(error_frame, ['variant', 'pool'], ['targets', 'supported', 'signed_error', 'raw_mse', 'raw_mae', 'mse', 'mae', 'predicted', 'truth', 'outside_scale']).to_csv(OUT / 'error-summary.csv', index=False)
    summarize_numeric(bin_frame, ['variant', 'pool', 'score_bin'], ['rows', 'predicted', 'truth', 'signed_error'], 'rows').to_csv(OUT / 'calibration-bins.csv', index=False)

    for number, (uid, c) in enumerate(h10.items()):
        with np.load(BASE / 'combination340/catalog-cache' / f'{uid}.npz') as cache:
            ei, actual = cache['ei'], cache['actual_direct']
            local = {name: cache[name] for name in ['S', 'ACTUAL_ALS'] + BLENDS}
        require(len(set(ei)) == len(ei) and not set(ei).intersection(c['viewed']), 'catalog unique and unseen')
        require(np.array_equal(np.isfinite(local['ACTUAL_ALS']), actual), 'full support identity')
        blocked = cats.blocked.to_numpy()[ei]
        counts = cats.train_count.to_numpy()[ei]
        require(not np.any(actual & (blocked | (counts == 0))), 'actual is supported W')
        distribution.append(distribution_row(c, ei, 'FULL_CATALOG'))
        for name in BLENDS:
            require(np.array_equal(local[name][~actual], local['S'][~actual]), 'catalog fallback identity')
            changes.append(dict(uid=uid, h=c['h'], source='FULL_CATALOG', variant=name,
                                **top_changes(local['S'], local[name], ids[ei], actual, blocked)))
            for group, other in [('C', blocked), ('NATURAL_ZERO', ~blocked & (counts == 0))]:
                cross.append(dict(uid=uid, h=c['h'], source='FULL_CATALOG', variant=name, other=group,
                                  **boundary_counts(local['S'], local[name], ids[ei], actual, other)))
        for name in ['S', 'ACTUAL_ALS', 'BLEND_0.25']:
            for pool, mask in {'ALL': np.ones(len(ei), bool), 'C': blocked, 'W_DIRECT': actual,
                               'NATURAL_ZERO': ~blocked & (counts == 0)}.items():
                p = local[name][mask & np.isfinite(local[name])]
                score_stats.append(dict(uid=uid, h=c['h'], variant=name, pool=pool, targets=int(mask.sum()), supported=len(p),
                                        predicted=float(p.mean()) if len(p) else np.nan,
                                        p50=float(np.quantile(p, .5)) if len(p) else np.nan,
                                        p95=float(np.quantile(p, .95)) if len(p) else np.nan,
                                        maximum=float(p.max()) if len(p) else np.nan))
        if number % 30 == 0:
            print('CATALOG_DIAGNOSTIC', number, round(time.monotonic() - start_time, 1), flush=True)

    distribution_frame = pd.DataFrame(distribution)
    distribution_frame.to_parquet(OUT / 'candidate-distribution-users.parquet', index=False)
    summarize_numeric(distribution_frame, ['source'], ['rows'] + list(flags), 'rows').to_csv(OUT / 'candidate-distribution.csv', index=False)
    pd.DataFrame(score_stats).to_parquet(OUT / 'catalog-score-users.parquet', index=False)
    summarize_numeric(pd.DataFrame(score_stats), ['variant', 'pool'], ['targets', 'supported', 'predicted', 'p50', 'p95', 'maximum']).to_csv(OUT / 'catalog-score-summary.csv', index=False)
    change_frame, cross_frame = pd.DataFrame(changes), pd.DataFrame(cross)
    change_frame.to_parquet(OUT / 'top2-changes.parquet', index=False)
    cross_frame.to_parquet(OUT / 'boundary-users.parquet', index=False)
    change_values = [v for v in change_frame if v not in ['uid', 'h', 'source', 'variant']]
    summarize_numeric(change_frame, ['source', 'variant'], change_values).to_csv(OUT / 'top2-change-summary.csv', index=False)
    for value in ['flips', 'warm_promoted_pairs', 'warm_demoted_pairs']:
        cross_frame[value + '_rate'] = cross_frame[value] / cross_frame.pairs.replace(0, np.nan)
    for value in ['correct_before', 'correct_after']:
        cross_frame[value + '_rate'] = cross_frame[value] / cross_frame.unequal_pairs.replace(0, np.nan)
    cross_values = [v for v in cross_frame if v not in ['uid', 'h', 'source', 'variant', 'other']]
    summarize_numeric(cross_frame, ['source', 'variant', 'other'], cross_values).to_csv(OUT / 'boundary-summary.csv', index=False)

    old_top = pd.read_parquet(BASE / 'foundation340/catalog-top10.parquet')
    new_top = pd.read_parquet(BASE / 'combination340/catalog-top10.parquet')
    a = old_top[old_top.variant.eq('B')].sort_values(['uid', 'rank'])
    b = new_top[new_top.variant.eq('S')].sort_values(['uid', 'rank'])
    require(np.array_equal(a[['uid', 'rank', 'movie_id', 'prediction']], b[['uid', 'rank', 'movie_id', 'prediction']]), 'catalog B S equality')
    top = pd.concat([old_top[old_top.variant.isin(['R', 'H', 'RH'])], new_top[~new_top.variant.eq('B')]], ignore_index=True)
    require(not top.duplicated(['uid', 'variant', 'rank']).any(), 'unique source top slots')
    ix = np.searchsorted(ids, top.movie_id.to_numpy())
    require(np.array_equal(ids[ix], top.movie_id), 'top movie axis')
    top['known'] = [(int(u), int(m)) in lookup for u, m in top[['uid', 'movie_id']].itertuples(index=False, name=None)]
    for name, v in flags.items():
        top[name] = v[ix]
    top[top['rank'].le(6)].to_parquet(OUT / 'catalog-top6-annotated.parquet', index=False)
    exposure = []
    for name in CONTENT + ['ACTUAL_ALS', 'REFERENCE_ALS']:
        for group in ['ALL_USERS', 'H_POSITIVE', 'H_ZERO']:
            users = [u for u, c in h10.items() if group == 'ALL_USERS' or (c['h'] > 0 if group == 'H_POSITIVE' else c['h'] == 0)]
            for kind, end in WINDOWS:
                start = end - 2 if kind == 'page' else 0
                a = top[top.variant.eq(name) & top.uid.isin(users) & top['rank'].gt(start) & top['rank'].le(end)]
                freq = a.movie_id.value_counts(normalize=True)
                exposure.append(dict(variant=name, h_group=group, kind=kind, end=end, users=len(users),
                                     requested=len(users)*(end-start), returned=len(a), missing=len(users)*(end-start)-len(a),
                                     users_complete=int(a.groupby('uid').size().eq(end-start).sum()),
                                     known=int(a.known.sum()), unknown=int((~a.known).sum()),
                                     duplicate_slots=int(a.duplicated(['uid', 'movie_id']).sum()),
                                     unique_movies=len(freq), hhi=float((freq**2).sum()),
                                     most_exposed_share=float(freq.max()) if len(freq) else np.nan,
                                     **{field: int(a[field].sum()) for field in flags}))
    pd.DataFrame(exposure).to_csv(OUT / 'catalog-exposure.csv', index=False)
    versions = {'numpy': np.__version__, 'pandas': pd.__version__}
    write_json(OUT / 'run-summary.json', {'status': 'COMPLETED_DESCRIPTIVE', 'new_fits': 0, 'new_inferences': 0,
        'contexts': len(contexts), 'users': len(h10), 'full_catalog_rows': int(distribution_frame[distribution_frame.source.eq('FULL_CATALOG')].rows.sum()),
        'seconds': time.monotonic() - start_time, 'versions': versions, 'no_model_selected': True})
    files = {p.name: pin(p) for p in sorted(OUT.iterdir()) if p.is_file()}
    write_json(OUT / 'result-seal.json', {'fingerprint': fingerprint(), 'execution_review': pin(DOC / 'execution-review.json'), 'files': files})
    print('DIAGNOSTIC342_COMPLETE', round(time.monotonic() - start_time, 1), flush=True)


def selftest():
    y = np.array([5., .5, 1.5, 4., 3., 2.])
    p = np.array([3., 3., 2., 1., 0., -1.])
    ids = np.array([20, 10, 30, 40, 50, 60])
    require(page_quality(y, p, ids, 'page', 2)['stars'] == 2.75, 'tie ID paging')
    require(page_quality(y, p, ids, 'page', 4)['stars'] == 2.75, 'second page independent')
    short = page_quality(y[:3], p[:3], ids[:3], 'page', 4)
    require(short['returned'] == 1 and short['missing'] == 1 and np.isnan(short['both_low']), 'short page')
    require(np.isnan(page_quality(np.array([.5, .5]), np.zeros(2), np.arange(2), 'cumulative', 2)['ndcg']), 'zero IDCG')
    rng = np.random.default_rng(342)
    for _ in range(100):
        n = 18
        base = rng.integers(0, 5, n).astype(float)
        warm = rng.random(n) < .5
        after = base.copy(); after[warm] = rng.integers(0, 5, warm.sum())
        result = observed_boundary(rng.integers(1, 11, n)/2, base, after, rng.permutation(n), warm, ~warm)
        require(result['flips'] == result['warm_promoted_pairs'] + result['warm_demoted_pairs'], 'flip partition')
        require(result['correct_after']-result['correct_before'] == result['fixed']-result['broken'], 'correctness partition')
    require(boundary_counts(np.zeros(2), np.zeros(2), np.arange(2), np.zeros(2, bool), np.ones(2, bool))['pairs'] == 0, 'empty support')
    print('SELFTEST_PASS pagination, missing slots, IDCG, 100 tied boundary fixtures, empty support')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--selftest', action='store_true')
    args = parser.parse_args()
    selftest() if args.selftest else run()
