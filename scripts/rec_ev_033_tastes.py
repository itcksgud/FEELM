"""Build two fixed research taste catalogues without reading user ratings or labels."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import threading
import time

import numpy as np
import pandas as pd
import psutil
from scipy.optimize import linear_sum_assignment
import sklearn
from sklearn.cluster import KMeans
from sklearn.metrics import adjusted_rand_score
from threadpoolctl import threadpool_limits

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / 'docs/recommendation/experiments/rec-ev-033'
FILES = {'design': PLAN / 'README.md', 'config': PLAN / 'config.json',
         'runner': Path(__file__).resolve(), 'tests': ROOT / 'scripts/tests/test_rec_ev_033_tastes.py'}
ROLE = ('catalog-scope.parquet', 'common-ids.npz')
PREPARE = ('metadata.parquet', 'cache-ledger.parquet', 'input-report.json')
MODEL = ('centroids.npz', 'assignments.parquet', 'stability.json')
DESCRIBE = ('terms.parquet', 'class-term-counts.npz', 'taste-summary.json')
OUTPUTS = (*ROLE, 'role-seal.json', *PREPARE, 'prepare-seal.json', *MODEL,
           'model-seal.json', *DESCRIBE, 'description-seal.json', 'budget.json')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(2**20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def pin(path):
    return {'bytes': Path(path).stat().st_size, 'sha256': sha(path)}


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, value):
    path = Path(path)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    os.replace(temp, path)


def fingerprint():
    return {name: sha(path) for name, path in FILES.items()}


def reviewed():
    actual = fingerprint()
    review = read_json(PLAN / 'review.json')
    require(review['status'] == 'PASS' and review['fingerprint'] == actual, 'review absent or stale')
    return actual


def canonical_body(body):
    return (json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')


def validate_cache(value, tmdb_id, expected_body_hash):
    request = {'kind': 'movie', 'identity': str(tmdb_id), 'endpoint': f'/3/movie/{tmdb_id}',
               'params': {'append_to_response': 'credits,keywords', 'language': 'ko-KR'}}
    require(value.get('request') == request and 200 <= value.get('status', 0) < 300, 'cache request/status mismatch')
    body = value.get('body')
    require(isinstance(body, dict) and body.get('id') == tmdb_id, 'cache identity mismatch')
    actual = hashlib.sha256(canonical_body(body)).hexdigest()
    require(actual == value.get('body_sha256') == expected_body_hash, 'cache body hash mismatch')
    return body


def ordered_ids(items):
    result = []
    for item in items:
        value = item['id']
        require(isinstance(value, int) and not isinstance(value, bool) and value > 0, 'invalid metadata ID')
        if value not in result:
            result.append(value)
    return result


def genre_assignment(genres, groups, format_only):
    mapping = {int(g): code for code, values in groups.items() for g in values}
    require(len(mapping) == sum(map(len, groups.values())), 'overlapping genre groups')
    require(all(isinstance(g, (int, np.integer)) and not isinstance(g, (bool, np.bool_)) and g > 0 for g in genres), 'invalid genre ID')
    unique = list(dict.fromkeys(map(int, genres)))
    content = [g for g in unique if g not in format_only]
    if not content:
        return None, 'FORMAT_ONLY' if unique else 'NO_GENRE', None
    first = content[0]
    return (mapping[first], 'ASSIGNED', first) if first in mapping else (None, 'UNMAPPED_GENRE', first)


def validate_vectors(vectors, descriptor, expected):
    require(descriptor == expected, 'embedding descriptor mismatch')
    vectors = np.asarray(vectors, dtype=np.float64)
    require(vectors.ndim == 2 and vectors.shape[1] == expected['dimension'] and np.isfinite(vectors).all(), 'invalid vector shape/value')
    require(np.all(np.abs(np.linalg.norm(vectors, axis=1) - 1) <= 1e-4), 'invalid vector norm')
    return vectors


def nearest(vectors, centers, batch_size=2048):
    vectors, centers = np.asarray(vectors), np.asarray(centers)
    require(isinstance(batch_size, int) and batch_size > 0, 'invalid batch size')
    require(vectors.ndim == centers.ndim == 2 and vectors.shape[1] == centers.shape[1]
            and len(centers) > 0 and np.isfinite(vectors).all() and np.isfinite(centers).all(), 'invalid nearest inputs')
    labels, distances = np.empty(len(vectors), dtype=np.int64), np.empty(len(vectors), dtype=np.float64)
    # Direct squared differences retain the same per-row arithmetic across batch sizes.
    for start in range(0, len(vectors), batch_size):
        block = vectors[start:start + batch_size]
        d = np.stack([np.sum((block - center) ** 2, axis=1) for center in centers], axis=1)
        best = np.argmin(d, axis=1)
        labels[start:start + len(block)] = best
        distances[start:start + len(block)] = d[np.arange(len(block)), best]
    return labels, distances


def canonical_centers(vectors, ids, raw_centers):
    labels, distance = nearest(vectors, raw_centers)
    exemplars = []
    for k in range(len(raw_centers)):
        positions = np.flatnonzero(labels == k)
        require(len(positions) > 0, 'empty fitted cluster')
        position = positions[np.lexsort((ids[positions], distance[positions]))[0]]
        exemplars.append(int(ids[position]))
    order = np.argsort(exemplars, kind='stable')
    centers = np.asarray(raw_centers[order], dtype=np.float64)
    require(len(np.unique(centers, axis=0)) == len(centers), 'duplicate centroids')
    return centers, order, np.asarray(exemplars)[order]


def stability(primary, diagnostic, size=8):
    counts = np.zeros((size, size), dtype=np.int64)
    np.add.at(counts, (primary, diagnostic), 1)
    row, col = linear_sum_assignment(-counts)
    return {'contingency': counts.tolist(), 'adjusted_rand_index': float(adjusted_rand_score(primary, diagnostic)),
            'aligned_agreement': float(counts[row, col].sum() / len(primary)),
            'diagnostic_to_primary': {str(int(c)): int(r) for r, c in zip(row, col)},
            'primary_model_replaced': False}


def ctfidf(counts):
    counts = np.asarray(counts, dtype=np.float64)
    require(counts.ndim == 2 and np.isfinite(counts).all() and (counts >= 0).all(), 'invalid term counts')
    totals = counts.sum(axis=1)
    frequency = counts.sum(axis=0)
    average = int(totals.mean())
    tf = np.divide(counts, totals[:, None], out=np.zeros_like(counts), where=totals[:, None] > 0)
    idf = np.log1p(np.divide(average, frequency, out=np.zeros_like(frequency), where=frequency > 0))
    return tf * idf


class Budget:
    def __init__(self, root, config, identity):
        self.root, self.cfg, self.identity = root, config, identity
        self.start, self.peak = time.monotonic(), 0
        self.done, self.lock = threading.Event(), threading.Lock()
        self.process = psutil.Process()
        self.thread = threading.Thread(target=self.watch, daemon=True)

    def sample(self):
        children = self.process.children(recursive=True)
        rss = self.process.memory_info().rss
        for child in children:
            try:
                rss += child.memory_info().rss
            except psutil.NoSuchProcess:
                pass
        self.peak = max(self.peak, rss)
        return children

    def elapsed(self):
        return time.monotonic() - self.start

    def exceeded(self):
        return self.elapsed() > self.cfg['max_seconds'] or self.peak > self.cfg['max_process_tree_bytes']

    def save(self, failed=False):
        with self.lock:
            value = {'seconds': self.elapsed(), 'peak_tree_rss_bytes': self.peak, 'fingerprint': self.identity}
            write_json(self.root / 'budget.json', value)
            if failed:
                write_json(self.root / 'failure.json', {**value, 'status': 'RESOURCE_LIMIT'})

    def guard(self):
        self.sample()
        if self.exceeded():
            self.save(True)
            raise RuntimeError('resource limit')

    def watch(self):
        while not self.done.wait(2):
            children = self.sample()
            if self.exceeded():
                self.save(True)
                for child in reversed(children):
                    try:
                        child.terminate()
                    except psutil.NoSuchProcess:
                        pass
                os._exit(70)

    def close(self):
        self.done.set()
        if self.thread.is_alive():
            self.thread.join()
        self.sample()
        self.save()


class Run:
    def __init__(self):
        self.identity, self.cfg = reviewed(), read_json(FILES['config'])
        c = self.cfg
        require(c['experiment'] == 'REC033_TASTE_PREPARATION' and c['expected_common'] == 85517, 'experiment drift')
        require(c['kmeans'] == {'n_clusters': 8, 'init': 'k-means++', 'n_init': 10, 'max_iter': 300,
            'tol': .0001, 'algorithm': 'lloyd', 'primary_seed': 20260908, 'diagnostic_seed': 20260909,
            'threads': 4, 'dtype': 'float64', 'sklearn_version': '1.9.0'}, 'KMeans drift')
        require(sklearn.__version__ == c['kmeans']['sklearn_version'], 'runtime changed')
        require(c['embedding']['dimension'] == 384 and c['embedding']['model_id'] == 'intfloat/multilingual-e5-small'
            and c['embedding']['model_revision'] == '614241f622f53c4eeff9890bdc4f31cfecc418b3', 'embedding changed')
        require(len(c['genre_groups']) == 8 and c['format_only_genres'] == [10770]
            and c['ctfidf']['min_document_frequency'] == 5 and c['ctfidf']['top_terms'] == 15, 'interpretation drift')
        self.root = ROOT / c['output_root']
        require(self.root.resolve() == (ROOT / 'outputs/recommendation-evidence/rec-ev-033').resolve(), 'output outside allowed root')
        self.descriptor = {k: c['embedding'][k] for k in ('model_id', 'model_revision', 'dimension',
            'input_prefix', 'input_template', 'maximum_tokens', 'pooling', 'normalization', 'dtype')}
        self.paths, self.budget = {}, None

    def guard(self):
        require(reviewed() == self.identity, 'code/review changed')
        if self.budget:
            self.budget.guard()

    def log(self, phase, **fields):
        self.guard()
        print({'phase': phase, 'seconds': round(self.budget.elapsed(), 2), **fields}, flush=True)

    def sources(self):
        for name, spec in self.cfg['inputs'].items():
            path = (ROOT / spec['path']).resolve()
            require(path.is_relative_to(ROOT.resolve()), 'source outside research repository')
            require(pin(path) == {k: spec[k] for k in ('bytes', 'sha256')}, 'source drift: ' + name)
            self.paths[name] = path
            self.guard()
        manifest, summary = read_json(self.paths['catalog_manifest']), read_json(self.paths['catalog_summary'])
        require(manifest['status'] == summary['status'] == 'PASS_RATING_INDEPENDENT_FULL_CATALOG_BUILD'
                and not summary['rating_values_opened'] and not summary['ratings_member_opened'], 'catalog provenance')
        recorded = {v['path']: {k: v[k] for k in ('bytes', 'sha256')} for v in manifest['artifacts'] + manifest['implementation_artifacts']}
        for name in ('identity', 'structured', 'embeddings', 'catalog_summary', 'catalog_builder', 'feature_builder'):
            spec = self.cfg['inputs'][name]
            require(recorded[spec['path']] == {k: spec[k] for k in ('bytes', 'sha256')}, 'catalog manifest mismatch')
        require(manifest['catalog_build_contract_sha256'] == sha(self.paths['catalog_contract']), 'catalog contract mismatch')
        require(read_json(self.paths['embedding_contract'])['embedding'] == self.cfg['embedding'], 'encoder contract mismatch')
        self.cache_roots = [(ROOT / p).resolve() for p in self.cfg['cache_roots']]
        expected = [ROOT / 'outputs/recommendation-evidence/rec-ev-027-catalog/tmdb-cache',
                    ROOT / 'outputs/recommendation-evidence/rec-ev-019b/tmdb-cache']
        require(self.cache_roots == [p.resolve() for p in expected], 'cache scope changed')

    def seal(self, name, outputs, **fields):
        self.guard()
        write_json(self.root / name, {'status': 'COMPLETE', 'fingerprint': self.identity,
            'outputs': {p: pin(self.root / p) for p in outputs}, **fields})

    def verify(self, name, outputs, dependencies=None):
        value = read_json(self.root / name)
        require(value['status'] == 'COMPLETE' and value['fingerprint'] == self.identity
            and set(value['outputs']) == set(outputs), 'seal header drift')
        for key, expected in (dependencies or {}).items():
            require(value[key] == expected, 'seal dependency drift')
        for name, expected in value['outputs'].items():
            require(pin(self.root / name) == expected, 'sealed output drift')
        return value

    def cached_body(self, tmdb_id, expected_hash):
        for folder in self.cache_roots:
            path = folder / f'movie-{tmdb_id}-ko_KR.json'
            if not path.is_file():
                continue
            try:
                value = read_json(path)
                body = validate_cache(value, tmdb_id, expected_hash)
            except (RuntimeError, ValueError, TypeError, KeyError):
                continue
            return body, {'path': path.relative_to(ROOT).as_posix(), **pin(path), 'body_sha256': expected_hash}
        raise RuntimeError('no pinned TMDB cache response available')

    def verify_cache_sources(self):
        ledger = pd.read_parquet(self.root / 'cache-ledger.parquet')
        require(not ledger.path.duplicated().any(), 'duplicate cache ledger path')
        for i, row in enumerate(ledger.itertuples(index=False)):
            path = (ROOT / row.path).resolve()
            require(any(path.is_relative_to(folder) for folder in self.cache_roots), 'cache ledger outside allowlist')
            require(pin(path) == {'bytes': row.bytes, 'sha256': row.sha256}, 'cache source drift')
            if i % 2000 == 0:
                self.guard()

    def prepare(self):
        c = self.cfg
        identity = pd.read_parquet(self.paths['identity'])
        structured = pd.read_parquet(self.paths['structured'])
        emeta = pd.read_parquet(self.paths['embeddings'], columns=['movie_id', 'model_id', 'model_revision', 'feature_eligible'])
        require(len(identity) == c['expected_catalog'] and not identity.movie_id.duplicated().any(), 'catalog identity rows')
        require(not structured.movie_id.duplicated().any() and not emeta.movie_id.duplicated().any()
            and len(structured) == len(emeta) == c['expected_identity_eligible'], 'feature identity rows')
        require(set(emeta.model_id) == {self.descriptor['model_id']}
            and set(emeta.model_revision) == {self.descriptor['model_revision']}, 'encoder metadata drift')
        sid = set(structured.loc[structured.feature_eligible, 'movie_id'])
        eid = set(emeta.loc[emeta.feature_eligible, 'movie_id'])
        require(len(sid) == c['expected_structured_eligible'] and len(eid) == c['expected_text_eligible'], 'eligibility drift')
        self.ids = np.asarray(sorted(sid & eid), dtype=np.int64)
        with np.load(self.paths['universe'], allow_pickle=False) as old:
            require(np.array_equal(self.ids, old['item_ids']) and len(self.ids) == c['expected_common'], 'common catalogue drift')
        identity = identity.sort_values('movie_id').set_index('movie_id')
        require(set(self.ids) <= set(identity.index) and set(structured.movie_id) == set(emeta.movie_id), 'feature identity mismatch')
        scope = identity[['tmdb_id', 'identity_status']].reset_index()
        scope['structured_eligible'] = scope.movie_id.isin(sid)
        scope['text_eligible'] = scope.movie_id.isin(eid)
        scope['common'] = scope.movie_id.isin(self.ids)
        scope['scope_reason'] = np.where(scope.common, 'COMMON', np.where(~scope.movie_id.isin(structured.movie_id),
            'IDENTITY_INELIGIBLE', np.where(~scope.structured_eligible & ~scope.text_eligible, 'NO_STRUCTURED_OR_TEXT',
            np.where(~scope.structured_eligible, 'NO_STRUCTURED', 'NO_TEXT'))))
        scope.to_parquet(self.root / 'catalog-scope.parquet', index=False)
        np.savez_compressed(self.root / 'common-ids.npz', movie_ids=self.ids)
        self.seal('role-seal.json', ROLE, sources=self.cfg['inputs'], user_data_access=False, embedding_values_decoded=False)

        selected = identity.loc[self.ids]
        feature = structured.set_index('movie_id').loc[self.ids]
        rows, ledger = [], {}
        for i, mid in enumerate(self.ids):
            who, features = selected.loc[mid], feature.loc[mid]
            require(who.identity_status in ('ML_TMDB_VERIFIED', 'RECOVERED_BY_IMDB') and who.media_type == 'movie', 'invalid selected identity')
            body, source = self.cached_body(int(who.tmdb_id), who.response_sha256)
            genres = body.get('genres') or []
            keywords = (body.get('keywords') or {}).get('keywords') or (body.get('keywords') or {}).get('results') or []
            genre_ids, keyword_ids = ordered_ids(genres), ordered_ids(keywords)
            require(sorted(genre_ids) == sorted(map(int, features.genre_ids))
                and sorted(keyword_ids) == sorted(map(int, features.keyword_ids)), 'structured/cache term mismatch')
            title = str(body.get('title') or body.get('original_title') or '').strip()
            language = str(body.get('original_language') or '').strip()
            require(language == features.original_language, 'language/cache mismatch')
            genre_names = {int(v['id']): str(v.get('name') or '').strip() for v in genres}
            keyword_names = {int(v['id']): str(v.get('name') or '').strip() for v in keywords}
            a, reason, first = genre_assignment(genre_ids, c['genre_groups'], c['format_only_genres'])
            reversed_a = genre_assignment(list(reversed(genre_ids)), c['genre_groups'], c['format_only_genres'])[0]
            rows.append({'movie_id': int(mid), 'tmdb_id': int(who.tmdb_id), 'title': title,
                'original_title': str(body.get('original_title') or '').strip(), 'original_language': language,
                'genre_ids': genre_ids, 'genre_names': [genre_names[g] for g in genre_ids],
                'keyword_ids': keyword_ids, 'keyword_names': [keyword_names[g] for g in keyword_ids],
                'genre_code': a, 'genre_reason': reason, 'first_content_genre': first,
                'reverse_genre_changes_assignment': a != reversed_a,
                'cache_path': source['path'], 'response_sha256': source['body_sha256']})
            previous = ledger.get(source['path'])
            require(previous is None or previous == source, 'cache changed within extraction')
            ledger[source['path']] = source
            if (i + 1) % 10000 == 0:
                self.log('METADATA_PROGRESS', movies=i + 1)
        self.metadata = pd.DataFrame(rows)
        self.metadata.to_parquet(self.root / 'metadata.parquet', index=False)
        pd.DataFrame([ledger[p] for p in sorted(ledger)]).to_parquet(self.root / 'cache-ledger.parquet', index=False)
        values = pd.read_parquet(self.paths['embeddings'], columns=['movie_id', 'embedding'],
            filters=[('movie_id', 'in', self.ids.tolist())]).set_index('movie_id').loc[self.ids]
        self.x = validate_vectors(np.stack(values.embedding), self.descriptor, self.descriptor)
        report = {'catalogue_movies': len(identity), 'common_movies': len(self.ids), 'scope_counts': scope.scope_reason.value_counts().to_dict(),
            'duplicate_tmdb_rows_in_common': int(self.metadata.tmdb_id.duplicated().sum()), 'encoder_descriptor': self.descriptor,
            'stored_norm_min': float(np.linalg.norm(self.x, axis=1).min()), 'stored_norm_max': float(np.linalg.norm(self.x, axis=1).max()),
            'unique_cache_files': len(ledger), 'genre_unassigned': int(self.metadata.genre_code.isna().sum()),
            'genre_reason_counts': self.metadata.genre_reason.value_counts().to_dict(), 'ratings_or_labels_opened': False,
            'new_network_requests': 0, 'new_encodings': 0, 'retrospective_metadata': True}
        write_json(self.root / 'input-report.json', report)
        self.seal('prepare-seal.json', PREPARE, role_seal=pin(self.root / 'role-seal.json'), sources=self.cfg['inputs'])
        self.log('CATALOG_INPUTS_SEALED', common_movies=len(self.ids), unassigned_genre=report['genre_unassigned'])

    def models(self):
        self.verify('prepare-seal.json', PREPARE, {'role_seal': pin(self.root / 'role-seal.json'), 'sources': self.cfg['inputs']})
        c = self.cfg['kmeans']
        saved, stats = {}, []
        for purpose, seed in [('primary', c['primary_seed']), ('diagnostic', c['diagnostic_seed'])]:
            self.log('KMEANS_FIT_START', purpose=purpose)
            with threadpool_limits(limits=c['threads']):
                model = KMeans(n_clusters=c['n_clusters'], init=c['init'], n_init=c['n_init'], max_iter=c['max_iter'],
                    tol=c['tol'], algorithm=c['algorithm'], random_state=seed).fit(self.x)
                centers, raw_order, exemplars = canonical_centers(self.x, self.ids, model.cluster_centers_)
                labels, distances = nearest(self.x, centers)
            require(len(np.unique(labels)) == c['n_clusters'], 'canonical empty cluster')
            saved[purpose + '_centers'] = centers
            saved[purpose + '_raw_cluster_order'] = raw_order
            saved[purpose + '_anchor_movie_ids'] = exemplars
            saved[purpose + '_labels'] = labels
            saved[purpose + '_distances'] = distances
            stats.append({'purpose': purpose, 'seed': seed, 'n_iter': int(model.n_iter_), 'inertia': float(model.inertia_),
                'recomputed_inertia': float(distances.sum()), 'reached_max_iter': bool(model.n_iter_ == c['max_iter']),
                'cluster_counts': np.bincount(labels, minlength=c['n_clusters']).tolist()})
            self.log('KMEANS_FIT_COMPLETE', purpose=purpose, iterations=int(model.n_iter_))
        self.blabels, self.bdist = saved['primary_labels'], saved['primary_distances']
        self.bcodes = [f'SEMANTIC_{k+1:02d}' for k in range(c['n_clusters'])]
        self.assignments = self.metadata[['movie_id', 'genre_code', 'genre_reason', 'first_content_genre']].copy()
        self.assignments['semantic_code'] = [self.bcodes[k] for k in self.blabels]
        self.assignments['semantic_squared_distance'] = self.bdist
        self.assignments.to_parquet(self.root / 'assignments.parquet', index=False)
        result = stability(self.blabels, saved['diagnostic_labels'], c['n_clusters'])
        result.update(fits=stats, fit_count=2, sklearn_version=sklearn.__version__, dtype=str(self.x.dtype),
            primary_seed=c['primary_seed'], diagnostic_seed=c['diagnostic_seed'], service_quality_evaluated=False)
        write_json(self.root / 'stability.json', result)
        np.savez_compressed(self.root / 'centroids.npz', **saved, codes=np.array(self.bcodes),
            descriptor_json=np.array(json.dumps(self.descriptor, sort_keys=True)), movie_ids=self.ids)
        self.seal('model-seal.json', MODEL, prepare_seal=pin(self.root / 'prepare-seal.json'))

    def describe(self):
        self.verify('model-seal.json', MODEL, {'prepare_seal': pin(self.root / 'prepare-seal.json')})
        docs, frequency, names = [], Counter(), {}
        for row in self.metadata.itertuples(index=False):
            tokens = set()
            for prefix, ids, labels in [('genre', row.genre_ids, row.genre_names), ('keyword', row.keyword_ids, row.keyword_names)]:
                for tid, label in zip(ids, labels, strict=True):
                    token = f'{prefix}:{tid}'
                    tokens.add(token)
                    if label:
                        names.setdefault(token, Counter()).update([label])
            docs.append(tokens)
            frequency.update(tokens)
        vocabulary = sorted(t for t, count in frequency.items() if count >= self.cfg['ctfidf']['min_document_frequency'])
        index = {t: i for i, t in enumerate(vocabulary)}
        labels = {t: sorted(names.get(t, {t: 1}).items(), key=lambda pair: (-pair[1], pair[0]))[0][0] for t in vocabulary}
        terms = pd.DataFrame({'term_key': vocabulary, 'display_text': [labels[t] for t in vocabulary],
            'movie_document_frequency': [frequency[t] for t in vocabulary], 'name_variants': [len(names.get(t, {})) for t in vocabulary]})
        terms.to_parquet(self.root / 'terms.parquet', index=False)
        acodes = list(self.cfg['genre_groups'])
        alabels = np.array([acodes.index(code) if code in acodes else -1 for code in self.metadata.genre_code], dtype=np.int64)
        class_counts, summary = {}, []
        for policy, codes, assignments in [('A_GENRE', acodes, alabels), ('B_KMEANS', self.bcodes, self.blabels)]:
            counts = np.zeros((len(codes), len(vocabulary)), dtype=np.int64)
            for k, tokens in zip(assignments, docs, strict=True):
                if k >= 0:
                    for token in tokens:
                        if token in index:
                            counts[k, index[token]] += 1
            values = ctfidf(counts)
            class_counts[policy + '_counts'] = counts
            class_counts[policy + '_ctfidf'] = values
            for k, code in enumerate(codes):
                positions = np.flatnonzero(assignments == k)
                require(len(positions) > 0, 'empty research taste')
                order = sorted(range(len(vocabulary)), key=lambda j: (-values[k, j], vocabulary[j]))
                top = [j for j in order if values[k, j] > 0][:self.cfg['ctfidf']['top_terms']]
                sample = sorted(positions, key=lambda pos: (hashlib.sha256(f"{self.cfg['examples']['hash_prefix']}{self.ids[pos]}".encode()).digest(), self.ids[pos]))[:self.cfg['examples']['hash_sample_movies']]
                closest = positions[np.lexsort((self.ids[positions], self.bdist[positions]))][:self.cfg['examples']['closest_movies']] if policy == 'B_KMEANS' else []
                def examples(positions):
                    return [{'movie_id': int(self.ids[pos]), 'title': self.metadata.iloc[pos].title,
                        'original_language': self.metadata.iloc[pos].original_language} for pos in positions]
                group = self.metadata.iloc[positions]
                summary.append({'policy': policy, 'code': code, 'movies': len(positions), 'fraction_of_common': len(positions) / len(self.ids),
                    'original_language_counts': {str(key): int(value) for key, value in group.original_language.value_counts().items()},
                    'ctfidf_terms': [{'term_key': vocabulary[j], 'text': labels[vocabulary[j]], 'class_movie_frequency': int(counts[k,j]),
                        'global_movie_frequency': int(frequency[vocabulary[j]]), 'score': float(values[k,j])} for j in top],
                    'hash_examples': examples(sample), 'nearest_examples': examples(closest), 'name_and_color_approved': False})
        np.savez_compressed(self.root / 'class-term-counts.npz', **class_counts, vocabulary=np.array(vocabulary),
            A_GENRE_codes=np.array(acodes), B_KMEANS_codes=np.array(self.bcodes))
        write_json(self.root / 'taste-summary.json', {'experiment': self.cfg['experiment'], 'tastes': summary,
            'genre_unassigned': int((alabels < 0).sum()), 'semantic_unassigned': 0,
            'genre_reverse_order_changed': int(self.metadata.reverse_genre_changes_assignment.sum()),
            'genre_reverse_order_denominator': len(self.ids), 'ctfidf_vocabulary_size': len(vocabulary),
            'research_artifacts_prepared': True, 'readiness': 'WITH_REPORTED_UNASSIGNED' if (alabels < 0).any() else 'FULL_COMMON_COVERAGE',
            'service_quality_evaluated': False, 'winner_selected': False, 'final_K_selected': False,
            'official_model_reproduced': False, 'new_text_encoder_verified': False, 'product_contract_changed': False})
        self.seal('description-seal.json', DESCRIBE, model_seal=pin(self.root / 'model-seal.json'))

    def run(self):
        require(not (self.root / 'failure.json').exists(), 'failed execution exists; preserve without retry')
        if (self.root / 'completion-seal.json').exists():
            self.sources()
            self.verify('completion-seal.json', OUTPUTS, {'sources': self.cfg['inputs']})
            self.verify('role-seal.json', ROLE, {'sources': self.cfg['inputs']})
            self.verify('prepare-seal.json', PREPARE, {'role_seal': pin(self.root / 'role-seal.json'), 'sources': self.cfg['inputs']})
            self.verify('model-seal.json', MODEL, {'prepare_seal': pin(self.root / 'prepare-seal.json')})
            self.verify('description-seal.json', DESCRIBE, {'model_seal': pin(self.root / 'model-seal.json')})
            self.verify_cache_sources()
            print('VERIFIED_EXISTING_COMPLETION_NO_FIT_OR_ASSIGNMENT')
            return
        require(not self.root.exists() or not any(self.root.iterdir()), 'partial execution exists; preserve without retry')
        self.root.mkdir(parents=True, exist_ok=True)
        self.budget = Budget(self.root, self.cfg, self.identity)
        self.budget.save(); self.budget.thread.start()
        try:
            self.sources(); self.prepare(); self.models(); self.describe()
            self.sources(); self.verify_cache_sources(); self.guard()
            self.budget.close(); self.guard()
            self.seal('completion-seal.json', OUTPUTS, sources=self.cfg['inputs'], fit_count=2,
                user_rating_data_access=False, new_network_requests=0, seconds=self.budget.elapsed())
            self.log('TWO_TASTE_CATALOGUES_COMPLETE')
        except Exception as exc:
            self.budget.close()
            if not (self.root / 'failure.json').exists():
                write_json(self.root / 'failure.json', {'status': 'FAILED', 'fingerprint': self.identity,
                    'error_type': type(exc).__name__, 'error': str(exc)})
            raise


if __name__ == '__main__':
    Run().run()
