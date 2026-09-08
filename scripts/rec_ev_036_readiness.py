"""No-fit, no-rating diagnostic of the frozen REC033 K-means candidate."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from rec_ev_033_tastes import Budget, canonical_body, pin, read_json, require, sha, validate_cache, write_json
from build_rec_ev_019b_features import merge_text, build_embedding_input

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / 'docs/recommendation/experiments/rec-ev-036'
FILES = {'design': PLAN / 'README.md', 'config': PLAN / 'config.json', 'runner': Path(__file__).resolve(),
         'tests': ROOT / 'scripts/tests/test_rec_ev_036_readiness.py',
         'helper': ROOT / 'scripts/rec_ev_033_tastes.py',
         'text_helper': ROOT / 'scripts/build_rec_ev_019b_features.py',
         'text_dependency': ROOT / 'scripts/recommendation_protocol_v4.py'}
OUTPUTS = ('distances.parquet', 'diagnostics.json', 'masked-cards.json', 'card-key.json',
           'selected-cards.json', 'selected-cache-ledger.json', 'budget.json')


def fingerprint():
    return {key: sha(path) for key, path in FILES.items()}


def geometry(vectors, centers, batch_size=2048):
    x, c = np.asarray(vectors, dtype=np.float64), np.asarray(centers, dtype=np.float64)
    require(x.ndim == c.ndim == 2 and x.shape[1] == c.shape[1] and len(c) >= 2
            and np.isfinite(x).all() and np.isfinite(c).all() and batch_size > 0, 'bad geometry input')
    distances = np.empty((len(x), len(c)), dtype=np.float64)
    for start in range(0, len(x), batch_size):
        block = x[start:start + batch_size]
        distances[start:start + len(block)] = np.stack([np.sum((block - center) ** 2, axis=1) for center in c], axis=1)
    order = np.argsort(distances, axis=1, kind='stable')
    d1, d2 = distances[np.arange(len(x)), order[:, 0]], distances[np.arange(len(x)), order[:, 1]]
    margin = np.divide(d2 - d1, d2 + d1, out=np.zeros_like(d1), where=(d1 + d2) > 0)
    return distances, order[:, 0], order[:, 1], margin


def select_samples(ids, labels, d1, margin, cfg, codes):
    require(len(np.unique(ids)) == len(ids), 'duplicate sample ID')
    result = {}
    for k, code in enumerate(codes):
        positions = np.flatnonzero(labels == k)
        require(len(positions) >= max(cfg['nearest_count'], cfg['hash_count'], cfg['boundary_count']), 'small cluster')
        near = positions[np.lexsort((ids[positions], d1[positions]))][:cfg['nearest_count']]
        hashed = sorted(positions, key=lambda p: (hashlib.sha256(f"{cfg['hash_prefix']}{ids[p]}".encode()).digest(), ids[p]))[:cfg['hash_count']]
        boundary = positions[np.lexsort((ids[positions], margin[positions]))][:cfg['boundary_count']]
        result[str(code)] = {name: [int(ids[p]) for p in seq] for name, seq in [('nearest', near), ('hash', hashed), ('boundary', boundary)]}
    return result


def validate_english(value, tmdb_id):
    expected = {'kind': 'movie', 'identity': str(tmdb_id), 'endpoint': f'/3/movie/{tmdb_id}',
                'params': {'append_to_response': 'credits,keywords', 'language': 'en-US'}}
    require(value.get('request') == expected and 200 <= value.get('status', 0) < 300, 'English request/status mismatch')
    body = value.get('body')
    require(isinstance(body, dict) and body.get('id') == tmdb_id, 'English ID mismatch')
    require(hashlib.sha256(canonical_body(body)).hexdigest() == value.get('body_sha256'), 'English body hash mismatch')
    return body


def match_text(primary, candidates, expected_sha, descriptor):
    for path, english in candidates:
        text = merge_text(primary, english)
        payload = build_embedding_input(descriptor['input_template'], descriptor['input_prefix'], text)
        if hashlib.sha256(payload.encode('utf-8')).hexdigest() == expected_sha:
            return text, path
    raise RuntimeError('No exact pre-encoding input text hash match')


def aligned(frame, ids):
    require(not frame.movie_id.duplicated().any(), 'duplicate source ID')
    require(set(ids).issubset(set(frame.movie_id)), 'missing source IDs')
    return frame.set_index('movie_id').loc[ids].reset_index()


class Run:
    def __init__(self):
        self.identity = fingerprint()
        review = read_json(PLAN / 'review.json')
        require(review['status'] == 'PASS' and review['fingerprint'] == self.identity, 'review absent or stale')
        self.cfg = read_json(FILES['config'])
        require(self.cfg['experiment'] == 'REC036_KMEANS_READINESS', 'experiment mismatch')
        self.root = ROOT / self.cfg['output_root']
        require(self.root.resolve() == ROOT / 'outputs/recommendation-evidence/rec-ev-036', 'output root changed')
        self.paths = {name: ROOT / info['path'] for name, info in self.cfg['inputs'].items()}

    def sources(self):
        require(fingerprint() == self.identity, 'execution code changed')
        for name, info in self.cfg['inputs'].items():
            require(pin(self.paths[name]) == {k: info[k] for k in ('bytes', 'sha256')}, f'input changed: {name}')
        seal = read_json(self.paths['completion'])
        source_cfg = read_json(self.paths['source_config'])
        require(seal['status'] == 'COMPLETE' and seal['user_rating_data_access'] is False
                and seal['fingerprint']['config'] == sha(self.paths['source_config'])
                and seal['sources'] == source_cfg['inputs'], 'REC033 lineage mismatch')
        for key in ('embeddings', 'catalog_manifest'):
            require(self.cfg['inputs'][key] == seal['sources'][key], 'catalogue source mismatch')
        for name, info in seal['outputs'].items():
            require(pin(self.paths['completion'].parent / name) == info, f'REC033 output changed: {name}')

    def cache_sources(self):
        for entry in read_json(self.root / 'selected-cache-ledger.json'):
            require(pin(ROOT / entry['path']) == {k: entry[k] for k in ('bytes', 'sha256')}, 'selected cache changed')

    def calculate(self):
        cfg = self.cfg
        scfg = read_json(self.paths['source_config'])
        z = np.load(self.paths['centroids'], allow_pickle=False)
        ids, centers, codes = z['movie_ids'], z['primary_centers'], z['codes']
        descriptor = json.loads(str(z['descriptor_json']))
        expected_descriptor = {k: scfg['embedding'][k] for k in ('model_id', 'model_revision', 'dimension',
            'input_prefix', 'input_template', 'maximum_tokens', 'pooling', 'normalization', 'dtype')}
        require(descriptor == expected_descriptor and codes.tolist() == [f'SEMANTIC_{i:02d}' for i in range(1, 9)], 'descriptor/code mismatch')
        require(len(ids) == cfg['expected_movies'] and len(np.unique(ids)) == len(ids)
                and np.all(np.diff(ids) > 0) and centers.shape == (8, 384), 'catalogue shape mismatch')
        metadata = aligned(pd.read_parquet(self.paths['metadata']), ids)
        assignments = aligned(pd.read_parquet(self.paths['assignments']), ids)
        embeddings = aligned(pd.read_parquet(self.paths['embeddings']), ids)
        require(embeddings.feature_eligible.all() and set(embeddings.model_id) == {descriptor['model_id']}
                and set(embeddings.model_revision) == {descriptor['model_revision']}, 'embedding metadata mismatch')
        x = np.stack(embeddings.embedding).astype(np.float64)
        require(x.shape == (len(ids), 384) and np.isfinite(x).all()
                and np.max(np.abs(np.linalg.norm(x, axis=1) - 1)) <= 1e-4, 'vector contract mismatch')
        distances, first, second, margin = geometry(x, centers)
        d1 = distances[np.arange(len(ids)), first]
        require(np.array_equal(first, z['primary_labels']) and np.array_equal(codes[first], assignments.semantic_code), 'assignment mismatch')
        require(np.array_equal(d1, z['primary_distances']) and np.array_equal(d1, assignments.semantic_squared_distance), 'distance mismatch')
        table = pd.DataFrame({'movie_id': ids, 'code': codes[first], 'runner_up_code': codes[second],
                              'd1': d1, 'd2': distances[np.arange(len(ids)), second], 'margin': margin})
        for k, code in enumerate(codes):
            table['distance_' + str(code)] = distances[:, k]
        table.to_parquet(self.root / 'distances.parquet', index=False)
        samples = select_samples(ids, first, d1, margin, cfg, codes)
        old = {t['code']: t for t in read_json(self.paths['summary'])['tastes'] if t['policy'] == 'B_KMEANS'}
        for code, groups in samples.items():
            for kind in ('nearest', 'hash'):
                require(groups[kind] == [e['movie_id'] for e in old[code][kind + '_examples']], 'old sample mismatch')
        genres = sorted(set(int(g) for gs in metadata.genre_ids for g in gs))
        genre_counts = np.zeros((len(codes), len(genres)), dtype=int)
        cluster_summary = []
        quantiles = [0, .05, .25, .5, .75, .95, 1]
        for k, code in enumerate(codes):
            pos = np.flatnonzero(first == k)
            group = metadata.iloc[pos]
            gc = Counter(int(g) for gs in group.genre_ids for g in set(gs))
            genre_counts[k] = [gc[g] for g in genres]
            cluster_summary.append({'code': str(code), 'movies': len(pos),
                'language_counts': {str(a): int(b) for a, b in group.original_language.value_counts().items()},
                'genre_counts': {str(g): gc[g] for g in genres},
                'd1_quantiles': np.quantile(d1[pos], quantiles).tolist(),
                'margin_quantiles': np.quantile(margin[pos], quantiles).tolist(),
                'runner_up_counts': dict(Counter(map(str, codes[second[pos]]))), 'samples': samples[str(code)]})
        normalized = genre_counts / np.linalg.norm(genre_counts, axis=1, keepdims=True)
        diag = {'movies': len(ids), 'codes': codes.tolist(), 'clusters': cluster_summary, 'quantile_levels': quantiles,
                'centroid_squared_distances': np.sum((centers[:, None] - centers[None, :]) ** 2, axis=2).tolist(),
                'genre_cosine_similarity': (normalized @ normalized.T).tolist(), 'genre_ids': genres,
                'assignment_exact_match': True, 'distance_exact_match': True, 'fit_count': 0,
                'encoding_count': 0, 'rating_data_access': False, 'service_quality_evaluated': False}
        write_json(self.root / 'diagnostics.json', diag)
        self.budget.guard()
        self.cards(metadata, embeddings, table, samples, scfg)

    def cards(self, metadata, embeddings, table, samples, scfg):
        roles = {}
        for code, groups in samples.items():
            for role, movies in groups.items():
                for mid in movies:
                    roles.setdefault(mid, {'code': code, 'roles': []})['roles'].append(role)
        ordered = sorted(roles, key=lambda mid: (hashlib.sha256(f"{self.cfg['card_prefix']}{mid}".encode()).digest(), mid))
        m, e, d = (f.set_index('movie_id') for f in (metadata, embeddings, table))
        old_ledger = {row['path']: row for row in pd.read_parquet(self.paths['cache_ledger']).to_dict('records')}
        used, cards, keys, selected = {}, [], [], []
        allowed = [(ROOT / path).resolve() for path in scfg['cache_roots']]
        for i, mid in enumerate(ordered, 1):
            row = m.loc[mid]
            kp = str(row.cache_path)
            path = ROOT / kp
            require(path.resolve().parent in allowed and kp in old_ledger, 'cache outside allowed ledger')
            require(pin(path) == {k: old_ledger[kp][k] for k in ('bytes', 'sha256')}, 'primary cache changed')
            primary = validate_cache(read_json(path), int(row.tmdb_id), row.response_sha256)
            used[kp] = {'path': kp, **pin(path), 'body_sha256': row.response_sha256}
            # No fallback is accepted unless the complete original input string hash matches.
            candidates = [(None, None)]
            if not str(primary.get('title') or '').strip() or not str(primary.get('overview') or '').strip():
                for cache_root in allowed:
                    ep = cache_root / f'movie-{int(row.tmdb_id)}-en_US.json'
                    if ep.exists():
                        value = read_json(ep)
                        if 200 <= value.get('status', 0) < 300:
                            body = validate_english(value, int(row.tmdb_id))
                            require(body.get('imdb_id') == primary.get('imdb_id'), 'English IMDb identity mismatch')
                            candidates.append((ep, body))
            text, ep = match_text(primary, candidates, e.loc[mid].input_text_sha256, scfg['embedding'])
            if ep is not None:
                rel = ep.relative_to(ROOT).as_posix()
                used[rel] = {'path': rel, **pin(ep), 'body_sha256': read_json(ep)['body_sha256']}
            cid = f'C{i:03d}'
            card = {'card': cid, 'title': text['display_title'], 'overview': text['overview_fallback'],
                    'genres': text['genre_names'], 'keywords': text['keyword_names']}
            key = {'card': cid, 'movie_id': mid, **roles[mid], 'original_language': row.original_language,
                   'runner_up_code': d.loc[mid].runner_up_code, 'margin': float(d.loc[mid].margin),
                   'input_text_sha256': e.loc[mid].input_text_sha256, 'exact_input_text_match': True,
                   'primary_cache': kp, 'english_cache': ep.relative_to(ROOT).as_posix() if ep else None}
            cards.append(card); keys.append(key); selected.append({**card, **key})
        write_json(self.root / 'masked-cards.json', cards)
        write_json(self.root / 'card-key.json', keys)
        write_json(self.root / 'selected-cards.json', selected)
        write_json(self.root / 'selected-cache-ledger.json', list(used.values()))

    def run(self):
        self.sources()
        require(not (self.root / 'failure.json').exists(), 'failed run preserved; no retry')
        if (self.root / 'completion-seal.json').exists():
            seal = read_json(self.root / 'completion-seal.json')
            require(seal['fingerprint'] == self.identity and seal['sources'] == self.cfg['inputs'], 'completion mismatch')
            require(set(seal['artifacts']) == set(OUTPUTS), 'completion files mismatch')
            for name, info in seal['artifacts'].items():
                require(pin(self.root / name) == info, 'completed artifact changed: ' + name)
            self.cache_sources()
            print('VERIFIED_EXISTING_COMPLETION_NO_CALCULATION')
            return
        require(not self.root.exists() or not any(self.root.iterdir()), 'partial run preserved; no retry')
        self.root.mkdir(parents=True, exist_ok=True)
        self.budget = Budget(self.root, self.cfg, self.identity)
        self.budget.thread.start()
        try:
            self.calculate(); self.sources(); self.cache_sources(); self.budget.guard()
            self.budget.close()
            write_json(self.root / 'completion-seal.json', {'status': 'DIAGNOSTICS_COMPLETE_SEMANTIC_REVIEW_PENDING',
                'fingerprint': self.identity, 'sources': self.cfg['inputs'],
                'artifacts': {name: pin(self.root / name) for name in OUTPUTS},
                'fit_count': 0, 'encoding_count': 0, 'rating_data_access': False, 'network_requests': 0})
            print('DIAGNOSTICS_COMPLETE_SEMANTIC_REVIEW_PENDING')
        except Exception as exc:
            self.budget.close()
            if not (self.root / 'failure.json').exists():
                write_json(self.root / 'failure.json', {'status': 'FAILED', 'fingerprint': self.identity,
                    'error_type': type(exc).__name__, 'error': str(exc)})
            raise


if __name__ == '__main__':
    Run().run()
