"""Fixed eight-way catalogue comparison. No ratings, encoding, or network calls."""
from __future__ import annotations

import argparse
import hashlib
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import sparse
import sklearn
from sklearn.cluster import KMeans
from sklearn.decomposition import NMF
from sklearn.preprocessing import normalize
from threadpoolctl import threadpool_limits

from rec_ev_033_tastes import Budget, pin, read_json, require, sha, stability, validate_cache, write_json
from rec_ev_036_readiness import aligned, match_text, validate_english
from rec_ev_037_descriptions import describe_channel

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / 'docs/recommendation/experiments/rec-ev-038'
FILES = {'design': PLAN / 'README.md', 'config': PLAN / 'config.json', 'runner': Path(__file__).resolve(),
         'tests': ROOT / 'scripts/tests/test_rec_ev_038_clusters.py',
         'evaluation_runner': ROOT / 'scripts/rec_ev_038_evaluation.py'}
for name in ('rec_ev_033_tastes.py', 'rec_ev_036_readiness.py', 'rec_ev_037_descriptions.py',
             'build_rec_ev_019b_features.py', 'recommendation_protocol_v4.py'):
    FILES[name] = ROOT / 'scripts' / name


def fingerprint():
    return {key: sha(path) for key, path in FILES.items()}


def numeric_names(cfg):
    names = {'evaluation-panel.json', 'presence.npz', 'feature-index.npz', 'assignments.npz', 'metrics.json',
             'top-terms.json', 'description-packets.json', 'description-key.json', 'evaluation-source.json',
             'card-key.json', 'cache-ledger.json', 'budget.json'}
    names.update(f'features-{channel}.npz' for channel in ['GENRE', 'TAG', 'BOTH'])
    names.update(f'{kind}-{method}-{seed}.{extension}' for method in cfg['methods'][2:] for seed in cfg['seeds']
                 for kind, extension in [('model', 'npz'), ('fit', 'json')])
    return names


def hash_order(ids, prefix):
    return np.array(sorted(range(len(ids)), key=lambda i: (hashlib.sha256(f'{prefix}{ids[i]}'.encode()).digest(), int(ids[i]))))


def make_features(metadata, terms):
    lookup = {str(t): j for j, t in enumerate(terms.term_key)}
    require(len(lookup) == len(terms), 'duplicate vocabulary')
    indices, indptr = [], [0]
    raw_tag_count = []
    for row in metadata.itertuples(index=False):
        js = {lookup[f'{kind}:{int(t)}'] for kind, seq in [('genre', row.genre_ids), ('keyword', row.keyword_ids)]
              for t in set(seq) if f'{kind}:{int(t)}' in lookup}
        indices.extend(sorted(js)); indptr.append(len(indices)); raw_tag_count.append(len(set(row.keyword_ids)))
    counts = sparse.csr_matrix((np.ones(len(indices)), np.array(indices, dtype=np.int32), np.array(indptr, dtype=np.int32)),
                              shape=(len(metadata), len(terms)))
    df = np.asarray(counts.sum(axis=0)).ravel()
    require(np.array_equal(df, terms.movie_document_frequency), 'document frequency mismatch')
    require(np.isfinite(counts.data).all() and (counts.data == 1).all(), 'invalid presence matrix')
    masks = {name: np.array([str(t).startswith(prefix) for t in terms.term_key])
             for name, prefix in [('GENRE', 'genre:'), ('TAG', 'keyword:')]}
    require(np.all(masks['GENRE'] ^ masks['TAG']), 'unknown vocabulary namespace')
    idf = np.log((len(metadata) + 1) / (df + 1)) + 1
    x = {name: normalize(counts[:, mask].multiply(idf[mask]).tocsr(), norm='l2') for name, mask in masks.items()}
    x['BOTH'] = normalize(sparse.hstack([x['GENRE'], x['TAG']], format='csr'), norm='l2')
    return counts, x, idf, masks, np.asarray(raw_tag_count)


def canonical_labels(raw, ids, k=8):
    order = np.array(sorted(range(k), key=lambda j: (int(np.min(ids[raw == j])) if (raw == j).any() else np.iinfo(np.int64).max, j)))
    inverse = np.argsort(order)
    labels = np.full(len(raw), -1, dtype=np.int64)
    good = raw >= 0
    labels[good] = inverse[raw[good]]
    return labels, order


def display_labels(native, k=8):
    counts = np.bincount(native[native >= 0], minlength=k)
    require(counts.sum() > 0, 'no supported assignments')
    result = native.copy(); result[result < 0] = int(np.argmax(counts))
    return result


def nmf_contribution(w, h):
    w, h = np.asarray(w), np.asarray(h)
    require(w.ndim == h.ndim == 2 and w.shape[1] == h.shape[0]
            and np.isfinite(w).all() and np.isfinite(h).all() and (w >= 0).all() and (h >= 0).all(), 'invalid NMF factors')
    scale = np.linalg.norm(h, axis=1)
    unit = np.divide(h, scale[:, None], out=np.zeros_like(h), where=scale[:, None] > 0)
    contribution = w * scale
    labels = np.argmax(contribution, axis=1).astype(np.int64)
    labels[contribution.sum(axis=1) == 0] = -1
    return unit, contribution, labels


def fit_model(x, ids, method, seed, cfg):
    supported = np.asarray(x.getnnz(axis=1) > 0)
    positions = np.flatnonzero(supported)
    require(len(positions) >= cfg['k'], 'too few supported movies')
    start = time.monotonic()
    with warnings.catch_warnings(record=True) as caught, threadpool_limits(limits=cfg['threads']):
        warnings.simplefilter('always')
        if method.startswith('KM_'):
            model = KMeans(n_clusters=cfg['k'], random_state=seed, **cfg['km']).fit(x[supported])
            raw_small = model.labels_.astype(np.int64)
            arrays = {'raw_centers': model.cluster_centers_}
            objective = float(model.inertia_)
            max_iter = cfg['km']['max_iter']
        else:
            model = NMF(n_components=cfg['k'], random_state=seed, **cfg['nmf'])
            w = model.fit_transform(x[supported]); h = model.components_
            unit, contribution, raw_small = nmf_contribution(w, h)
            arrays = {'raw_W': w, 'raw_H': h, 'unit_H': unit, 'contribution_W': contribution}
            objective = float(model.reconstruction_err_)
            max_iter = cfg['nmf']['max_iter']
    raw = np.full(len(ids), -1, dtype=np.int64); raw[positions] = raw_small
    native, order = canonical_labels(raw, ids, cfg['k'])
    require(all(np.isfinite(v).all() for v in arrays.values()) and np.isfinite(objective), 'nonfinite model')
    arrays.update(raw_labels=raw, native_labels=native, raw_cluster_order=order, supported_positions=positions)
    info = {'seed': seed, 'seconds': time.monotonic() - start, 'iterations': int(model.n_iter_),
            'iteration_limit_reached': bool(model.n_iter_ >= max_iter), 'warnings': [str(w.message) for w in caught],
            'objective_in_own_space_only': objective, 'supported_input': int(supported.sum()),
            'model_zero_rows': int(np.sum(supported & (native < 0))),
            'occupied_native_clusters': int(len(set(native[native >= 0])))}
    return native, arrays, info


def validate_descriptions(packets, records):
    lookup = {p['packet']: p for p in packets}
    require(isinstance(records, list) and len(records) == len(lookup)
            and {r['packet'] for r in records} == set(lookup), 'description packet mismatch')
    for r in records:
        require(set(r) == {'packet', 'description', 'coherence', 'films', 'note'}, 'description schema')
        require(isinstance(r['description'], str) and bool(r['description'].strip()) and len(r['description']) <= 240, 'description length')
        require(r['coherence'] in {'COHERENT', 'BROAD', 'MIXED', 'INSUFFICIENT'} and isinstance(r['note'], str), 'description status')
        original = {f['card']: f['overview'] for f in lookup[r['packet']]['films']}
        require(len(r['films']) == len(original) and {f['card'] for f in r['films']} == set(original), 'description films')
        for f in r['films']:
            require(set(f) == {'card', 'support', 'excerpt'}, 'description film schema')
            require(f['support'] in {'FIT', 'PARTIAL', 'CONFLICT', 'INSUFFICIENT'}, 'support status')
            q = f['excerpt']
            require(isinstance(q, str) and len(q) <= 180 and q in original[f['card']], 'description excerpt')
            require(bool(q.strip()) or (not original[f['card']].strip() and f['support'] == 'INSUFFICIENT'), 'empty excerpt')
        supports = [f['support'] for f in r['films']]
        informative = sum(s != 'INSUFFICIENT' for s in supports)
        if r['coherence'] == 'COHERENT':
            require(len(supports) == 4 and all(s == 'FIT' for s in supports), 'coherent/support mismatch')
        elif r['coherence'] == 'INSUFFICIENT':
            require(informative < 2, 'insufficient/support mismatch')
        elif r['coherence'] == 'BROAD':
            require(informative >= 2 and 'CONFLICT' not in supports, 'broad/support mismatch')
        else:
            require(informative >= 2 and not all(s == 'FIT' for s in supports), 'mixed/support mismatch')
            require(any(s in {'FIT', 'PARTIAL'} for s in supports)
                    or r['description'] == '공통된 내용 설명을 도출하지 못함.', 'mixed description without support')
    return sorted(records, key=lambda r: r['packet'])


def validate_judgments(packets, records):
    lookup = {(p['card'], m['method']): m for p in packets for m in p['methods']}
    require(isinstance(records, list) and len(records) == len(lookup)
            and {(r['card'], r['method']) for r in records} == set(lookup), 'judgment keys/count')
    for r in records:
        require(set(r) == {'card', 'method', 'sufficient', 'acceptable', 'reason'}, 'judgment schema')
        require(isinstance(r['sufficient'], bool) and isinstance(r['reason'], str) and bool(r['reason'].strip()), 'judgment types')
        codes = {g['group'] for g in lookup[(r['card'], r['method'])]['groups']}
        require(isinstance(r['acceptable'], list) and len(set(r['acceptable'])) == len(r['acceptable'])
                and set(r['acceptable']).issubset(codes), 'invalid acceptable set')
        require(r['sufficient'] or not r['acceptable'], 'insufficient must have empty acceptable')
    return sorted(records, key=lambda r: (r['card'], r['method']))


class Run:
    def __init__(self):
        self.identity = fingerprint(); self.cfg = read_json(FILES['config'])
        review = read_json(PLAN / 'review.json')
        require(review['status'] == 'PASS' and review['fingerprint'] == self.identity, 'review missing/stale')
        require(sklearn.__version__ == self.cfg['sklearn_version'], 'wrong sklearn runtime; use py -3.12')
        self.root = ROOT / self.cfg['output_root']
        require(self.root.resolve() == ROOT / 'outputs/recommendation-evidence/rec-ev-038', 'wrong output root')
        self.paths = {k: ROOT / v['path'] for k, v in self.cfg['inputs'].items()}

    def sources(self):
        require(fingerprint() == self.identity, 'code changed')
        for k, spec in self.cfg['inputs'].items():
            require(pin(self.paths[k]) == {f: spec[f] for f in ('bytes', 'sha256')}, 'source changed: ' + k)
        seal = read_json(self.paths['completion033'])
        require(seal['status'] == 'COMPLETE' and seal['user_rating_data_access'] is False, 'source not complete')
        source_cfg = read_json(self.paths['source_config'])
        require(seal['sources'] == source_cfg['inputs'] and seal['fingerprint']['config'] == sha(self.paths['source_config'])
                and self.cfg['inputs']['embeddings'] == source_cfg['inputs']['embeddings'], 'source lineage mismatch')
        for name, spec in seal['outputs'].items():
            require(pin(self.paths['completion033'].parent / name) == spec, 'ancestor changed: ' + name)
        require(read_json(self.paths['review037'])['status'] == 'PASS_AUDIT_COMPLETE', 'previous audit incomplete')

    def verify_numeric(self):
        seal = read_json(self.root / 'numeric-seal.json')
        require(seal['status'] == 'NUMERIC_COMPLETE_SEMANTICS_PENDING' and seal['fingerprint'] == self.identity
                and seal['sources'] == self.cfg['inputs'] and set(seal['artifacts']) == numeric_names(self.cfg), 'numeric lineage')
        for name, spec in seal['artifacts'].items():
            require(pin(self.root / name) == spec, 'numeric artifact changed: ' + name)
        self.verify_caches()

    def verify_caches(self):
        for name, spec in read_json(self.root / 'cache-ledger.json').items():
            require(pin(ROOT / name) == spec, 'selected source cache changed: ' + name)

    def calculate(self):
        cfg = self.cfg
        metadata = pd.read_parquet(self.paths['metadata']).sort_values('movie_id').reset_index(drop=True)
        ids = metadata.movie_id.to_numpy()
        require(len(ids) == cfg['expected_movies'] and np.all(np.diff(ids) > 0), 'movie identity')
        terms = pd.read_parquet(self.paths['terms'])
        require(len(terms) == cfg['expected_vocabulary'] and (terms.movie_document_frequency >= cfg['min_df']).all(), 'vocabulary')
        evaluation_positions = hash_order(ids, cfg['eval_prefix'])[:cfg['evaluation_count']]
        evaluation_ids = ids[evaluation_positions]
        write_json(self.root / 'evaluation-panel.json', {'prefix': cfg['eval_prefix'], 'movie_ids': evaluation_ids.tolist(),
                   'fixed_before_fit': True, 'transductive_catalogue_diagnostic': True})
        counts, features, idf, masks, raw_tag_count = make_features(metadata, terms)
        require(counts[:, masks['GENRE']].nnz == cfg['expected_genre_nnz']
                and counts[:, masks['TAG']].nnz == cfg['expected_tag_nnz'], 'feature nnz mismatch')
        tag_supported = features['TAG'].getnnz(axis=1) > 0
        require(int((~tag_supported).sum()) == cfg['expected_tag_zero'], 'tag zero mismatch')
        sparse.save_npz(self.root / 'presence.npz', counts)
        for name, x in features.items():
            sparse.save_npz(self.root / f'features-{name}.npz', x)
        np.savez_compressed(self.root / 'feature-index.npz', movie_ids=ids, vocabulary=terms.term_key.to_numpy(dtype=str),
                            idf=idf, raw_tag_count=raw_tag_count, tag_supported=tag_supported)
        assignments = aligned(pd.read_parquet(self.paths['assignments']), ids)
        z = np.load(self.paths['centroids'], allow_pickle=False)
        require(np.array_equal(ids, z['movie_ids']), 'old model IDs')
        rule_codes = sorted(set(assignments.genre_code.dropna()))
        require(len(rule_codes) == 8, 'rule codes')
        rule = np.array([rule_codes.index(str(c)) if c is not None and str(c) in rule_codes else -1 for c in assignments.genre_code])
        native = {'RULE_GENRE': rule, 'KM_TEXT': z['primary_labels']}
        secondary = {'RULE_GENRE': rule.copy(), 'KM_TEXT': z['diagnostic_labels']}
        models = {'RULE_GENRE': {'reused': True, 'codes': rule_codes, 'fits': []},
                  'KM_TEXT': {'reused': True, 'codes': z['codes'].tolist(), 'fits': []}}
        for method in cfg['methods'][2:]:
            x = features[method.split('_', 1)[1]]; fits = []
            for i, seed in enumerate(cfg['seeds']):
                self.budget.guard()
                print(f'FIT {method} seed={seed}', flush=True)
                labels, arrays, info = fit_model(x, ids, method, seed, cfg)
                (native if i == 0 else secondary)[method] = labels
                np.savez_compressed(self.root / f'model-{method}-{seed}.npz', **arrays)
                fits.append(info)
                write_json(self.root / f'fit-{method}-{seed}.json', info)
                print(f'DONE {method} seed={seed}: {info["seconds"]:.2f}s, {info["occupied_native_clusters"]} native groups', flush=True)
            models[method] = {'reused': False, 'fits': fits}
        methods = cfg['methods']
        display = {m: display_labels(native[m]) for m in methods}
        np.savez_compressed(self.root / 'assignments.npz', movie_ids=ids, methods=np.array(methods),
                            native=np.stack([native[m] for m in methods]), display=np.stack([display[m] for m in methods]),
                            secondary_native=np.stack([secondary[m] for m in methods]))
        summary, descriptions, dkeys = {}, {}, []
        eval_set = set(evaluation_ids.tolist())
        description_order = hash_order(ids, cfg['description_prefix'])
        for method in methods:
            a, b = native[method], secondary[method]
            valid = (a >= 0) & (b >= 0)
            sizes = np.bincount(a[a >= 0], minlength=8)
            summary[method] = {**models[method], 'native_count': int((a >= 0).sum()), 'fallback_count': int((a < 0).sum()),
                'native_sizes': sizes.tolist(), 'display_sizes': np.bincount(display[method], minlength=8).tolist(),
                'native_eight_occupied': bool((sizes > 0).all()), 'stability': stability(a[valid], b[valid]),
                'stability_movies': int(valid.sum()), 'evaluation_native_group_counts': np.bincount(a[evaluation_positions][a[evaluation_positions] >= 0], minlength=8).tolist()}
            cc = np.stack([np.asarray(counts[a == k].sum(axis=0)).ravel() for k in range(8)])
            descriptions[method] = {}
            for channel, mask, limit in [('genre', masks['GENRE'], 5), ('keyword', masks['TAG'], 10)]:
                # Empty clusters are preserved, not hidden by a count helper requiring positive sizes.
                if (sizes > 0).all():
                    _, top, diag = describe_channel(cc[:, mask], terms.term_key.to_numpy()[mask], terms.display_text.to_numpy()[mask],
                                                  sizes, np.arange(8), limit)
                    descriptions[method][channel] = {'top': top, 'diagnostics': diag}
                else:
                    descriptions[method][channel] = {'status': 'NATIVE_EIGHT_CLUSTER_FAILURE', 'counts': cc[:, mask].tolist()}
            for k in range(8):
                ps = [int(p) for p in description_order if a[p] == k and int(ids[p]) not in eval_set][:cfg['description_count']]
                dkeys.append({'method': method, 'native_group': k, 'movie_ids': ids[ps].tolist()})
        write_json(self.root / 'metrics.json', {'movies': len(ids), 'raw_tag_missing': int((raw_tag_count == 0).sum()),
            'filtered_tag_empty': int(((raw_tag_count > 0) & ~tag_supported).sum()), 'methods': summary,
            'new_fit_count': 12, 'new_encoding_count': 0, 'rating_access': False})
        write_json(self.root / 'top-terms.json', descriptions)
        self.create_cards(metadata, evaluation_ids, dkeys, tag_supported)

    def create_cards(self, metadata, evaluation_ids, dkeys, tag_supported):
        scfg = read_json(self.paths['source_config'])
        wanted = sorted(set(evaluation_ids.tolist()) | {int(mid) for d in dkeys for mid in d['movie_ids']})
        m = metadata.set_index('movie_id')
        embeddings = aligned(pd.read_parquet(self.paths['embeddings']), wanted).set_index('movie_id')
        ledger = {r['path']: r for r in pd.read_parquet(self.paths['cache_ledger']).to_dict('records')}
        allowed = [(ROOT / p).resolve() for p in scfg['cache_roots']]
        texts, used = {}, {}
        for mid in wanted:
            row = m.loc[mid]; rel = row.cache_path; path = ROOT / rel
            require(path.resolve().parent in allowed and rel in ledger, 'primary cache outside ledger')
            require(pin(path) == {f: ledger[rel][f] for f in ('bytes', 'sha256')}, 'primary cache pin')
            primary = validate_cache(read_json(path), int(row.tmdb_id), row.response_sha256)
            candidates = [(None, None)]
            if not str(primary.get('title') or '').strip() or not str(primary.get('overview') or '').strip():
                for folder in allowed:
                    ep = folder / f'movie-{int(row.tmdb_id)}-en_US.json'
                    if ep.exists():
                        val = read_json(ep)
                        if 200 <= val.get('status', 0) < 300:
                            body = validate_english(val, int(row.tmdb_id))
                            require(body.get('imdb_id') == primary.get('imdb_id'), 'English identity')
                            candidates.append((ep, body))
            text, ep = match_text(primary, candidates, embeddings.loc[mid].input_text_sha256, scfg['embedding'])
            texts[mid] = {'title': text['display_title'], 'overview': text['overview_fallback'],
                          'input_text_sha256': embeddings.loc[mid].input_text_sha256}
            used[rel] = pin(path)
            if ep is not None: used[ep.relative_to(ROOT).as_posix()] = pin(ep)
        all_ids = np.array(wanted)
        cid = {int(all_ids[p]): f'C{i:03d}' for i, p in enumerate(hash_order(all_ids, 'rec038-card-v1|'), 1)}
        ordered = sorted(dkeys, key=lambda d: hashlib.sha256(f'rec038-pack-v1|{d["method"]}|{d["native_group"]}'.encode()).digest())
        packets, private = [], []
        for i, d in enumerate(ordered, 1):
            packet = f'P{i:03d}'
            packets.append({'packet': packet, 'films': [{'card': cid[mid], 'overview': texts[mid]['overview']} for mid in d['movie_ids']]})
            private.append({'packet': packet, **d})
        write_json(self.root / 'description-packets.json', packets)
        write_json(self.root / 'description-key.json', private)
        write_json(self.root / 'evaluation-source.json', [{'card': cid[int(mid)], 'overview': texts[int(mid)]['overview']} for mid in evaluation_ids])
        tag_map = dict(zip(metadata.movie_id, tag_supported))
        write_json(self.root / 'card-key.json', [{'card': cid[mid], 'movie_id': mid, 'is_evaluation': mid in set(evaluation_ids.tolist()),
            'tag_supported': bool(tag_map[mid]), **texts[mid]} for mid in wanted])
        write_json(self.root / 'cache-ledger.json', used)

    def run(self):
        self.sources()
        if (self.root / 'numeric-seal.json').exists():
            self.verify_numeric(); print('VERIFIED_NUMERIC_COMPLETION_NO_FIT'); return
        require(not self.root.exists() or not any(self.root.iterdir()), 'partial run preserved')
        self.root.mkdir(parents=True, exist_ok=True)
        self.budget = Budget(self.root, self.cfg, self.identity); self.budget.thread.start()
        try:
            self.calculate(); self.sources(); self.verify_caches(); self.budget.guard(); self.budget.close()
            artifacts = {p.relative_to(self.root).as_posix(): pin(p) for p in self.root.iterdir() if p.is_file()}
            require(set(artifacts) == numeric_names(self.cfg), 'numeric artifact inventory')
            write_json(self.root / 'numeric-seal.json', {'status': 'NUMERIC_COMPLETE_SEMANTICS_PENDING',
                'fingerprint': self.identity, 'sources': self.cfg['inputs'], 'artifacts': artifacts})
            print('NUMERIC_COMPLETE_SEMANTICS_PENDING')
        except BaseException as exc:
            self.budget.close(); write_json(self.root / 'failure.json', {'error': repr(exc), 'fingerprint': self.identity}); raise

    def seal_descriptions(self):
        self.sources(); self.verify_numeric()
        path = self.root / 'description-seal.json'
        names = ['descriptions-draft.json', 'descriptions.json', 'evaluation-packets.json', 'evaluation-key.json']
        if path.exists():
            seal = read_json(path)
            require(seal['status'] == 'DESCRIPTIONS_SEALED_BEFORE_EVALUATION' and seal['fingerprint'] == self.identity
                    and seal['numeric_seal'] == pin(self.root / 'numeric-seal.json')
                    and seal['source'] == pin(self.root / 'description-packets.json')
                    and set(seal['artifacts']) == set(names), 'description seal lineage mismatch')
            for name, spec in seal['artifacts'].items(): require(pin(self.root / name) == spec, 'sealed description changed')
            print('VERIFIED_DESCRIPTIONS'); return
        require(not any((self.root / n).exists() for n in names[1:]), 'partial description phase preserved')
        records = validate_descriptions(read_json(self.root / 'description-packets.json'), read_json(self.root / 'descriptions-draft.json'))
        write_json(self.root / 'descriptions.json', records)
        ds = {r['packet']: r for r in records}; keys = read_json(self.root / 'description-key.json')
        methods = sorted(self.cfg['methods'], key=lambda m: hashlib.sha256(f'rec038-method-v1|{m}'.encode()).digest())
        anonymous = {m: f'M{i:02d}' for i, m in enumerate(methods, 1)}
        bundles, mapping = [], []
        for m in methods:
            groups = []
            ks = sorted([k for k in keys if k['method'] == m], key=lambda k: hashlib.sha256(f'rec038-group-v1|{k["packet"]}'.encode()).digest())
            for i, k in enumerate(ks, 1):
                group = f'G{i:02d}'; groups.append({'group': group, 'description': ds[k['packet']]['description']})
                mapping.append({'method': anonymous[m], 'group': group, 'actual_method': m, 'native_group': k['native_group'], 'packet': k['packet']})
            bundles.append({'method': anonymous[m], 'groups': groups})
        packets = []
        for source in read_json(self.root / 'evaluation-source.json'):
            ordered = sorted(bundles, key=lambda b: hashlib.sha256(f'rec038-show-v1|{source["card"]}|{b["method"]}'.encode()).digest())
            packets.append({**source, 'methods': ordered})
        write_json(self.root / 'evaluation-packets.json', packets)
        write_json(self.root / 'evaluation-key.json', mapping)
        write_json(path, {'status': 'DESCRIPTIONS_SEALED_BEFORE_EVALUATION', 'fingerprint': self.identity,
            'numeric_seal': pin(self.root / 'numeric-seal.json'), 'source': pin(self.root / 'description-packets.json'),
            'artifacts': {n: pin(self.root / n) for n in names}})
        print('DESCRIPTIONS_SEALED_BEFORE_EVALUATION')

    def seal_judgments(self):
        self.seal_descriptions()
        path = self.root / 'judgment-seal.json'
        if path.exists():
            seal = read_json(path)
            require(seal['status'] == 'JUDGMENTS_SEALED_BEFORE_ASSIGNMENT_LINK' and seal['fingerprint'] == self.identity
                    and seal['description_seal'] == pin(self.root / 'description-seal.json')
                    and seal['draft'] == pin(self.root / 'judgments-draft.json')
                    and seal['records'] == self.cfg['evaluation_count'] * len(self.cfg['methods'])
                    and seal['unique_movies'] == self.cfg['evaluation_count'], 'judgment seal lineage mismatch')
            require(pin(self.root / 'judgments.json') == seal['output'], 'sealed judgment changed')
            print('VERIFIED_JUDGMENTS'); return
        require(not (self.root / 'judgments.json').exists(), 'partial judgment phase preserved')
        records = validate_judgments(read_json(self.root / 'evaluation-packets.json'), read_json(self.root / 'judgments-draft.json'))
        write_json(self.root / 'judgments.json', records)
        write_json(path, {'status': 'JUDGMENTS_SEALED_BEFORE_ASSIGNMENT_LINK', 'fingerprint': self.identity,
            'description_seal': pin(self.root / 'description-seal.json'), 'draft': pin(self.root / 'judgments-draft.json'),
            'output': pin(self.root / 'judgments.json'), 'records': len(records), 'unique_movies': self.cfg['evaluation_count']})
        print('JUDGMENTS_SEALED_BEFORE_ASSIGNMENT_LINK')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--seal-descriptions', action='store_true'); parser.add_argument('--seal-judgments', action='store_true')
    args = parser.parse_args(); run = Run()
    if args.seal_judgments: run.seal_judgments()
    elif args.seal_descriptions: run.seal_descriptions()
    else: run.run()
