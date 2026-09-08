"""One fixed raw/relative/binary-bridge comparison; labels open after score seal."""
from __future__ import annotations

# The frozen raw score recipe sets BLAS threads before NumPy is imported.
import rec_ev_032_basic as base
import rec_ev_032_conditional as conditional
from rec_ev_033_tastes import Budget, pin, sha, read_json, write_json, require
from enum import Enum
import hashlib
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / 'docs/recommendation/experiments/rec-ev-035'
FILES = {'design': PLAN / 'README.md', 'config': PLAN / 'config.json',
         'runner': Path(__file__).resolve(), 'tests': ROOT / 'tests/test_rec_ev_035_inputs.py',
         'base': Path(base.__file__), 'conditional': Path(conditional.__file__),
         'budget_helper': ROOT / 'scripts/rec_ev_033_tastes.py'}
PREPARE = ('relative-training.parquet', 'train-transform.npz', 'input-report.json')
TRAIN = ('relative-factors.npz', 'training-report.json')
SCORE = ('rankings.npz', 'target-order.npz', 'supply.parquet', 'supply-summary.json')
EVALUATE = ('user-metrics.parquet', 'metrics.json')
OUTPUTS = (*PREPARE, 'prepare-seal.json', *TRAIN, 'train-seal.json', *SCORE,
           'score-seal.json', *EVALUATE, 'evaluate-seal.json', 'budget.json')
POLICIES = ['A_RAW', 'B_RELATIVE', 'C_BINARY_BRIDGE']
PAIRS = [('B-A', 1, 0), ('C-A', 2, 0), ('B-C', 1, 2)]


def fingerprint():
    return {name: sha(path) for name, path in FILES.items()}


def reviewed():
    actual = fingerprint()
    review = read_json(PLAN / 'review.json')
    require(review['status'] == 'PASS' and review['fingerprint'] == actual, 'review absent or stale')
    return actual


def relative_table(hist, prior):
    h, g = np.asarray(hist), np.asarray(prior, dtype=np.float64)
    require(h.ndim >= 1 and h.shape[-1] == 10 and np.issubdtype(h.dtype, np.integer)
            and (h >= 0).all() and g.shape == (10,) and np.isfinite(g).all()
            and (g >= 0).all() and (g <= 1).all() and (np.diff(g) >= 0).all(), 'invalid relative inputs')
    n = h.sum(axis=-1, keepdims=True)
    return (2 * (np.cumsum(h, axis=-1) - h) + h + 10 * g - (n + 5)) / (n + 5)


def observation_weights(indices, prior):
    x = np.asarray(indices)
    require(x.ndim == 1 and np.issubdtype(x.dtype, np.integer) and ((x >= 0) & (x < 10)).all(), 'invalid observed grid')
    return relative_table(np.bincount(x, minlength=10), prior)[x]


class BinaryResponse(Enum):
    LIKE = 'LIKE'
    DISLIKE = 'DISLIKE'


def train_mapper(hist, weights):
    h, w = np.asarray(hist), np.asarray(weights)
    require(h.shape == w.shape and h.ndim == 2 and h.shape[1] == 10
            and np.isfinite(w).all() and (h >= 0).all(), 'mapper training shape')
    raw = np.arange(1, 11, dtype=np.float64) / 2
    result = {}
    for sign, mask in [('LIKE', w > 0), ('DISLIKE', w < 0)]:
        counts = (h * mask).sum(axis=0)
        require(counts.sum() > 0, 'mapper missing sign class')
        value = float(counts @ raw / counts.sum())
        require(np.isfinite(value) and .5 <= value <= 5, 'invalid mapper value')
        result[sign] = {'target': value, 'rows': int(counts.sum())}
    return result


def binary_targets(responses, mapper):
    require(all(type(value) is BinaryResponse for value in responses), 'adapter requires enum only')
    result = np.array([mapper[v.value]['target'] for v in responses], dtype=np.float64)
    require(np.isfinite(result).all() and ((result >= .5) & (result <= 5)).all(), 'invalid pseudo target')
    return result


def fold_targets(factors, targets, reg, tag):
    require(tag in ('RAW', 'RELATIVE_SCALED', 'BINARY_PSEUDO'), 'unknown target tag')
    if tag == 'RAW':
        return base.fold_in(factors, targets, reg)
    y, r = np.asarray(factors, dtype=np.float64), np.asarray(targets, dtype=np.float64)
    require(y.ndim == 2 and r.shape == (len(y),) and np.isfinite(y).all() and np.isfinite(r).all()
            and np.isfinite(reg) and reg > 0, 'invalid fold targets')
    if tag == 'BINARY_PSEUDO':
        require(((r >= .5) & (r <= 5)).all(), 'invalid binary pseudo range')
    return np.linalg.solve(y.T @ y + reg * len(y) * np.eye(y.shape[1]), y.T @ r) if len(y) else None


def full_order(ids, bayes, factors, has, observed_ids, use_positions, targets, reg, tag):
    """No evaluation target IDs or labels enter this full-catalogue scorer."""
    available = np.ones(len(ids), dtype=bool)
    available[np.searchsorted(ids, observed_ids)] = False
    p = fold_targets(factors[use_positions], targets, reg, tag)
    active = p is not None and np.linalg.norm(p) > 1e-12
    require(p is None or np.isfinite(p).all(), 'nonfinite fold vector')
    if active:
        values, mask, fallback = factors @ p, available & has, 'NONE'
    else:
        values, mask = bayes, available
        fallback = 'P0_NO_INPUT' if not len(observed_ids) else ('P0_NO_FACTOR' if not len(use_positions) else 'P0_ZERO_PROFILE')
    require(np.isfinite(values[mask]).all(), 'nonfinite supported scores')
    values = np.where(mask, values, -np.inf)
    return base.order(values, ids), values, {'active': bool(active), 'fallback': fallback}


def train_batch(batch, cfg):
    require(batch.schema.names == ['user_id', 'movie_id', 'rating'] and all(c.null_count == 0 for c in batch.columns), 'training null/schema')
    u, m, r = (batch.column(i).to_numpy() for i in range(3))
    require(np.issubdtype(u.dtype, np.integer) and np.issubdtype(m.dtype, np.integer)
            and ((u > 0) & (u <= cfg['max_user_id'])).all()
            and ((m > 0) & (m <= cfg['max_movie_id'])).all()
            and np.isfinite(r).all() and ((r >= .5) & (r <= 5)).all()
            and (r * 2 == (r * 2).astype(np.int64)).all(), 'training grid/identity')
    return u, m, (r * 2).astype(np.int64) - 1


class Run:
    def __init__(self):
        self.identity, self.cfg = reviewed(), read_json(FILES['config'])
        c = self.cfg
        require(c['experiment'] == 'REC035_INPUTS' and c['policies'] == POLICIES and c['ns'] == [0, 5, 10, 30]
                and (c['rank'], c['iterations'], c['seed'], c['reg'], c['prior_strength']) == (32, 10, 42, .1, 5)
                and (c['bootstrap_repeats'], c['bootstrap_seed'], c['alpha'], c['family_size']) == (20000, 20260908, .05, 27), 'recipe drift')
        self.root = ROOT / c['output_root']
        require(self.root.resolve() == (ROOT / 'outputs/recommendation-evidence/rec-ev-035').resolve(), 'output root drift')
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
            require(path.is_relative_to(ROOT.resolve()) and pin(path) == {k: spec[k] for k in ('bytes', 'sha256')}, 'source drift: ' + name)
            self.paths[name] = path
        # Verify all nested sealed outputs as bytes only; never decode label payload here.
        old = ROOT / 'outputs/recommendation-evidence/rec-ev-032'
        paths = {'prepare': old / 'prepare-seal.json', 'train': old / 'train-seal.json',
                 'score': old / 'score-seal.json', 'evaluate': old / 'evaluate-seal.json',
                 'cq': old / 'conditional-ranking/request-seal.json', 'cs': old / 'conditional-ranking/score-seal.json',
                 'cc': old / 'conditional-ranking/completion-seal.json',
                 'aq': old / 'als-only/request-seal.json', 'as': old / 'als-only/score-seal.json', 'ac': old / 'als-only/completion-seal.json'}
        seals = {key: read_json(path) for key, path in paths.items()}
        for key, seal in seals.items():
            require(seal['status'] in ('COMPLETE', 'SEALED_BEFORE_SCORING'), 'upstream incomplete')
            for name, expected in seal.get('outputs', {}).items():
                path = (paths[key].parent / name).resolve()
                require(path.is_relative_to(old.resolve()) and pin(path) == expected, 'upstream output drift')
        links = [('train', 'prepare_seal', 'prepare'), ('score', 'prepare_seal', 'prepare'),
                 ('score', 'train_seal', 'train'), ('evaluate', 'score_seal', 'score'),
                 ('cs', 'request_seal', 'cq'), ('cs', 'original_score_seal', 'score'),
                 ('cc', 'original_score_seal', 'score'), ('cc', 'original_evaluate_seal', 'evaluate'),
                 ('as', 'request_seal', 'aq'), ('as', 'source_completion', 'cc'), ('ac', 'source_completion', 'cc')]
        for left, field, right in links:
            require(seals[left][field] == pin(paths[right]), 'upstream link drift')
        require(seals['as']['source_factors'] == pin(self.paths['raw_factors']), 'raw factor provenance')
        self.ancestry = {key: pin(path) for key, path in paths.items()}
        self.guard()

    def seal(self, name, outputs, **fields):
        self.guard()
        write_json(self.root / name, {'status': 'COMPLETE', 'fingerprint': self.identity,
                   'outputs': {p: pin(self.root / p) for p in outputs}, **fields})

    def verify(self, name, outputs, **dependencies):
        value = read_json(self.root / name)
        require(value['status'] == 'COMPLETE' and value['fingerprint'] == self.identity
                and set(value['outputs']) == set(outputs), 'seal header drift')
        for key, expected in dependencies.items():
            require(value[key] == expected, 'seal dependency drift')
        for name, expected in value['outputs'].items():
            require(pin(self.root / name) == expected, 'sealed output drift')
        return value

    def prepare(self):
        c = self.cfg
        with np.load(self.paths['prepared'], allow_pickle=False) as z:
            self.ids, self.bayes, self.prior, train_ids = z['item_ids'], z['bayes'], z['prior'], z['training_user_ids']
        require(len(self.ids) == c['expected_items'] and (np.diff(self.ids) > 0).all()
                and self.bayes.shape == self.ids.shape and np.isfinite(self.bayes).all(), 'catalogue invalid')
        self.profiles = pd.read_parquet(self.paths['profiles'])
        self.keys = self.profiles.user_key.to_numpy(dtype=str)
        require(len(self.keys) == c['expected_users'] and len(set(self.keys)) == len(self.keys)
                and self.keys.tolist() == sorted(self.keys), 'cohort invalid')
        requests = pd.read_parquet(self.paths['targets'])
        require(np.array_equal(requests.user_key.to_numpy(dtype=str), self.keys), 'target user alignment')
        self.targets = [np.asarray(v, dtype=np.int64) for v in requests.movie_ids]
        catalogue = set(self.ids)
        for row, targets in zip(self.profiles.itertuples(index=False), self.targets, strict=True):
            movies, indices = np.asarray(row.profile_movie_ids), np.asarray(row.profile_rating_indices)
            require(len(movies) == len(set(movies)) == len(indices) == 30 and set(movies) <= catalogue
                    and np.issubdtype(indices.dtype, np.integer) and ((indices >= 0) & (indices < 10)).all()
                    and len(targets) >= 2 and len(set(targets)) == len(targets) and set(targets) <= catalogue
                    and not set(movies).intersection(targets), 'profile/E grid or overlap')
        self.offsets = np.concatenate(([0], np.cumsum(list(map(len, self.targets))))).astype(np.int64)
        self.target_ids = np.concatenate(self.targets)
        require(len(self.target_ids) == c['expected_targets'], 'target count')
        hist = np.zeros((c['max_user_id'] + 1, 10), dtype=np.int64)
        pairs, digest, rows = [], hashlib.sha256(), 0
        for batch in pq.ParquetFile(self.paths['training']).iter_batches(batch_size=100000):
            u, m, indices = train_batch(batch, c)
            np.add.at(hist, (u, indices), 1)
            pairs.append(u.astype(np.int64) * (c['max_movie_id'] + 1) + m)
            digest.update(np.column_stack((u, m)).astype('<i8').tobytes())
            rows += len(u)
        actual_users = np.flatnonzero(hist.sum(axis=1))
        require(rows == c['expected_training_rows'] and len(actual_users) == c['expected_training_users']
                and np.array_equal(actual_users, np.sort(train_ids)) and all(base.calibration_user(int(u)) for u in actual_users)
                and not set(base.user_key(int(u)) for u in actual_users).intersection(self.keys), 'training/evaluation role violation')
        require(len(np.unique(np.concatenate(pairs))) == rows, 'duplicate training interaction')
        del pairs
        prior = base.prior_from_hist(hist)
        require(np.array_equal(prior, self.prior), 'recomputed train prior drift')
        weights = relative_table(hist, prior)
        raw = np.arange(1, 11, dtype=np.float64) / 2
        raw_rms, relative_rms = np.sqrt((hist * raw ** 2).sum() / rows), np.sqrt((hist * weights ** 2).sum() / rows)
        require(np.isfinite(relative_rms) and relative_rms > 0, 'degenerate relative scale')
        self.scale, self.mapper = float(raw_rms / relative_rms), train_mapper(hist, weights)
        require(np.isfinite(self.scale) and self.scale > 0, 'invalid RMS scale')
        np.savez_compressed(self.root / 'train-transform.npz', user_ids=actual_users,
                            histograms=hist[actual_users], relative_targets=weights[actual_users], prior=prior, scale=self.scale)
        schema = pa.schema([('user_id', pa.int32()), ('movie_id', pa.int32()), ('rating', pa.float32())])
        max_error, zero_rows, output_rows, output_digest = 0., 0, 0, hashlib.sha256()
        with pq.ParquetWriter(self.root / 'relative-training.parquet', schema) as writer:
            for batch in pq.ParquetFile(self.paths['training']).iter_batches(batch_size=100000):
                u, m, indices = train_batch(batch, c)
                target = self.scale * weights[u, indices]
                converted = target.astype(np.float32)
                require(np.isfinite(converted).all() and np.array_equal(target == 0, converted == 0), 'target cast/zero failure')
                max_error = max(max_error, float(np.max(np.abs(converted.astype(np.float64) - target))))
                zero_rows += int((target == 0).sum()); output_rows += len(u)
                output_digest.update(np.column_stack((u, m)).astype('<i8').tobytes())
                writer.write_table(pa.Table.from_arrays([pa.array(u, type=pa.int32()), pa.array(m, type=pa.int32()), pa.array(converted)], schema=schema))
        require(output_rows == rows and output_digest.digest() == digest.digest(), 'training graph/order changed')
        report = {'training_rows': rows, 'training_users': len(actual_users), 'evaluation_users': len(self.keys),
                  'training_evaluation_overlap': 0, 'duplicate_interactions': 0, 'prior_exact_match': True,
                  'raw_rms': float(raw_rms), 'relative_rms': float(relative_rms), 'scale': self.scale, 'mapper': self.mapper,
                  'zero_training_rows_retained': zero_rows, 'ordered_interactions_sha256': digest.hexdigest(),
                  'max_float32_cast_error': max_error, 'label_payload_opened': False}
        write_json(self.root / 'input-report.json', report)
        self.seal('prepare-seal.json', PREPARE, sources=c['inputs'], ancestry=self.ancestry, label_payload_opened=False)
        self.log('TRAIN_TRANSFORM_SEALED', training_rows=rows, training_users=len(actual_users), evaluation_users=len(self.keys))

    def train(self):
        self.verify('prepare-seal.json', PREPARE, sources=self.cfg['inputs'], ancestry=self.ancestry)
        import pyspark
        from pyspark.ml.recommendation import ALS
        from pyspark.sql import SparkSession
        require(pyspark.__version__ == '4.2.0', 'Spark runtime changed')
        self.log('RELATIVE_ALS_SINGLE_FIT_START')
        spark = (SparkSession.builder.master('local[4]').appName('FEELM-REC035-relative')
                 .config('spark.driver.memory', '4g').config('spark.sql.shuffle.partitions', '32')
                 .config('spark.ui.enabled', 'false').config('spark.ui.showConsoleProgress', 'false').getOrCreate())
        spark.sparkContext.setLogLevel('ERROR')
        try:
            frame = spark.read.parquet(str(self.root / 'relative-training.parquet'))
            require(frame.count() == self.cfg['expected_training_rows'], 'Spark training count')
            model = ALS(rank=32, regParam=.1, maxIter=10, seed=42, userCol='user_id', itemCol='movie_id', ratingCol='rating',
                        implicitPrefs=False, nonnegative=False, coldStartStrategy='nan', numUserBlocks=10, numItemBlocks=10).fit(frame)
            items, users = model.itemFactors.orderBy('id').collect(), model.userFactors.orderBy('id').collect()
            item_ids = np.array([r.id for r in items], dtype=np.int64)
            y = np.asarray([r.features for r in items], dtype=np.float64)
            user_ids = np.array([r.id for r in users], dtype=np.int64)
            x = np.asarray([r.features for r in users], dtype=np.float64)
        finally:
            spark.stop()
        with np.load(self.paths['raw_factors'], allow_pickle=False) as z:
            require(np.array_equal(item_ids, z['item_ids']), 'raw/relative item graph changed')
        with np.load(self.root / 'train-transform.npz', allow_pickle=False) as z:
            require(np.array_equal(user_ids, z['user_ids']), 'relative user graph changed')
        require(y.shape == (len(item_ids), 32) and x.shape == (len(user_ids), 32)
                and np.isfinite(y).all() and np.isfinite(x).all(), 'nonfinite/invalid factors')
        np.savez_compressed(self.root / 'relative-factors.npz', item_ids=item_ids, factors=y, user_ids=user_ids, user_factors=x)
        sse, row_count = 0., 0
        user_counts, item_counts = np.zeros(len(x), np.int64), np.zeros(len(y), np.int64)
        for batch in pq.ParquetFile(self.root / 'relative-training.parquet').iter_batches(batch_size=100000):
            u, m, target = (batch.column(i).to_numpy() for i in range(3))
            up, mp = np.searchsorted(user_ids, u), np.searchsorted(item_ids, m)
            error = np.sum(x[up] * y[mp], axis=1) - target
            sse += float(error @ error); row_count += len(u)
            np.add.at(user_counts, up, 1); np.add.at(item_counts, mp, 1)
        penalty = .1 * (float(user_counts @ np.sum(x*x, axis=1)) + float(item_counts @ np.sum(y*y, axis=1)))
        report = {'fit_count': 1, 'training_rows': row_count, 'training_users': len(x), 'factor_items': len(y),
                  'rmse_in_scaled_relative_units': float(np.sqrt(sse / row_count)), 'sse': sse,
                  'als_wr_penalty': penalty, 'objective_sum': sse + penalty, 'objective_per_row': (sse + penalty) / row_count,
                  'all_factors_finite': True, 'convergence_proven': False, 'iterations': 10,
                  'spark': pyspark.__version__, 'numpy': np.__version__, 'python': sys.version}
        write_json(self.root / 'training-report.json', report)
        self.seal('train-seal.json', TRAIN, prepare_seal=pin(self.root / 'prepare-seal.json'), fit_count=1)
        self.log('RELATIVE_ALS_SINGLE_FIT_COMPLETE', factor_items=len(y))

    def score(self):
        self.verify('train-seal.json', TRAIN, prepare_seal=pin(self.root / 'prepare-seal.json'))
        matrices, support = [], []
        for path in [self.paths['raw_factors'], self.root / 'relative-factors.npz']:
            with np.load(path, allow_pickle=False) as z:
                fids, fy = z['item_ids'], z['factors']
            require((np.diff(fids) > 0).all() and fy.shape == (len(fids), 32) and np.isfinite(fy).all(), 'factor integrity')
            pos = np.searchsorted(fids, self.ids)
            has = (pos < len(fids)) & (fids[np.minimum(pos, len(fids)-1)] == self.ids)
            matrix = np.zeros((len(self.ids), 32)); matrix[has] = fy[pos[has]]
            matrices.append(matrix); support.append(has)
        require(np.array_equal(*support), 'factor support mismatch')
        has = support[0]
        with np.load(self.paths['als_rankings'], allow_pickle=False) as z:
            reference = z['movie_ids']
            require(np.array_equal(self.keys, z['user_keys']) and z['ns'].tolist() == self.cfg['ns']
                    and z['policies'].tolist() == ['P0', 'M0_RAW_ALS_STRUCTURED_RRF60', 'A0_RAW_ALS_ONLY'], 'reference rankings index')
        with np.load(self.paths['als_target_order'], allow_pickle=False) as z:
            old_ranks, old_scores = z['global_ranks'], z['global_scores']
            require(np.array_equal(z['user_keys'], self.keys) and np.array_equal(z['offsets'], self.offsets)
                    and np.array_equal(z['movie_ids'], self.target_ids), 'reference target ordering')
        self.ranked = np.full((len(self.keys), 4, 3, 2), -1, np.int64)
        global_top = np.full_like(self.ranked, -1)
        ranks = np.zeros((len(self.target_ids), 4, 3), np.int64)
        scores = np.full(ranks.shape, -np.inf, np.float64)
        score_hash = [[hashlib.sha256() for _ in POLICIES] for _ in range(4)]
        order_hash = [[hashlib.sha256() for _ in POLICIES] for _ in range(4)]
        counts, rows, matched = np.zeros((len(self.keys), 4, 3), np.int64), [], [0] * 4
        for u, row in enumerate(self.profiles.itertuples(index=False)):
            movies, indices = np.asarray(row.profile_movie_ids, np.int64), np.asarray(row.profile_rating_indices, np.int64)
            target, section = self.targets[u], slice(self.offsets[u], self.offsets[u+1])
            tp = np.searchsorted(self.ids, target)
            for k, n in enumerate(self.cfg['ns']):
                w = observation_weights(indices[:n], self.prior)
                positions = np.searchsorted(self.ids, movies[:n])
                use = (w != 0) & has[positions]
                responses = [BinaryResponse.LIKE if v > 0 else BinaryResponse.DISLIKE for v in w[use]]
                values_by_policy = [(indices[:n][use] + 1) / 2, self.scale * w[use], binary_targets(responses, self.mapper)]
                for p, (matrix, targets, tag) in enumerate(zip([matrices[0], matrices[1], matrices[0]], values_by_policy,
                                                               ['RAW', 'RELATIVE_SCALED', 'BINARY_PSEUDO'], strict=True)):
                    ordered, values, diag = full_order(self.ids, self.bayes, matrix, has, movies[:n], positions[use], targets, .1, tag)
                    score_hash[k][p].update(values.astype('<f8').tobytes())
                    order_hash[k][p].update(np.array([len(ordered)], dtype='<i8').tobytes())
                    order_hash[k][p].update(ordered.astype('<i8').tobytes())
                    selected, projected = conditional.projected_order(ordered, tp, len(self.ids))
                    counts[u, k, p] = len(selected)
                    self.ranked[u, k, p, :len(selected)] = target[selected]
                    global_top[u, k, p] = self.ids[ordered[:2]]
                    ranks[section, k, p], scores[section, k, p] = projected, values[tp]
                    if p == 0 and (w != 0).all():
                        require(np.array_equal(projected, old_ranks[section, k]) and np.array_equal(values[tp], old_scores[section, k])
                                and np.array_equal(self.ranked[u, k, p], reference[u, k, 2]), 'unchanged A raw reference mismatch')
                        matched[k] += 1
                    rows.append({'user_key': row.user_key, 'n': n, 'policy': POLICIES[p], 'n_observed': n,
                                 'n_nonzero': int((w != 0).sum()), 'n_factor_used': int(use.sum()), 'positive': int((w > 0).sum()),
                                 'negative': int((w < 0).sum()), 'neutral': int((w == 0).sum()), **diag,
                                 'supported_targets': int((projected > 0).sum()), 'supplied': len(selected),
                                 'global_order_size': len(ordered)})
            if (u + 1) % 100 == 0:
                self.log('THREE_POLICY_SCORING', users=u+1)
        supply = pd.DataFrame(rows)
        supply.to_parquet(self.root / 'supply.parquet', index=False)
        conditional.check_supply(counts)
        require(np.array_equal(self.ranked[:, 0, 0], reference[:, 0, 0])
                and all(np.array_equal(self.ranked[:, 0, p], self.ranked[:, 0, 0]) for p in (1, 2)), 'n0 fallback changed')
        summary = []
        for (n, policy), group in supply.groupby(['n', 'policy'], sort=False):
            summary.append({'n': int(n), 'policy': policy, 'users': len(group),
                            **{name: int(group[name].sum()) for name in ['n_observed', 'n_nonzero', 'n_factor_used', 'positive', 'negative', 'neutral', 'supplied']},
                            'no_nonzero_users': int((group.n_nonzero == 0).sum()), 'fallback_counts': group.fallback.value_counts().to_dict()})
        write_json(self.root / 'supply-summary.json', {'groups': summary, 'raw_reference_exact_states_by_n': matched})
        np.savez_compressed(self.root / 'rankings.npz', user_keys=self.keys, ns=self.cfg['ns'], policies=POLICIES,
                            movie_ids=self.ranked, global_movie_ids=global_top)
        np.savez_compressed(self.root / 'target-order.npz', user_keys=self.keys, offsets=self.offsets, movie_ids=self.target_ids,
                            global_ranks=ranks, global_scores=scores, factor_supported=has[np.searchsorted(self.ids, self.target_ids)])
        self.seal('score-seal.json', SCORE, prepare_seal=pin(self.root / 'prepare-seal.json'), train_seal=pin(self.root / 'train-seal.json'),
                  sources=self.cfg['inputs'], label_payload_opened=False,
                  full_score_sha256=[[h.hexdigest() for h in group] for group in score_hash],
                  full_order_sha256=[[h.hexdigest() for h in group] for group in order_hash])
        self.log('ALL_THREE_POLICY_SCORES_SEALED')

    def validate_score(self):
        self.verify('prepare-seal.json', PREPARE, sources=self.cfg['inputs'], ancestry=self.ancestry)
        self.verify('train-seal.json', TRAIN, prepare_seal=pin(self.root / 'prepare-seal.json'))
        self.verify('score-seal.json', SCORE, prepare_seal=pin(self.root / 'prepare-seal.json'), train_seal=pin(self.root / 'train-seal.json'),
                    sources=self.cfg['inputs'], label_payload_opened=False)

    def evaluate(self):
        self.validate_score(); self.sources()
        self.log('SEALED_SCORES_READY_FOR_LABELS')
        labels = pd.read_parquet(self.paths['labels'])  # First E value access in this run.
        with np.load(self.paths['histograms'], allow_pickle=False) as z:
            hists = z['histograms']
            require(np.array_equal(self.keys, z['user_keys']) and hists.shape == (len(self.keys), 10)
                    and np.issubdtype(hists.dtype, np.integer) and (hists >= 0).all() and (hists.sum(axis=1) > 0).all(), 'invalid fixed H')
        require(len(labels) == self.cfg['expected_targets'] and not labels.duplicated(['user_key', 'movie_id']).any()
                and set(labels.user_key) == set(self.keys), 'E label coverage')
        groups = {key: group.set_index('movie_id') for key, group in labels.groupby('user_key')}
        points, differences, rows = np.empty((len(self.keys), 4, 3, 3)), np.empty((len(self.keys), 3, 3, 3, 2)), []
        for u, key in enumerate(self.keys):
            group, h = groups[key], hists[u]
            require(set(group.index) == set(self.targets[u]), 'E identity mismatch')
            for mid, row in group.iterrows():
                require(abs(base.q_from_hist(base.rating_index(row.rating_raw), h) - row.q) <= 1e-12, 'fixed Q mismatch')
            for k, n in enumerate(self.cfg['ns']):
                numerators, denominator = [], None
                for p, policy in enumerate(POLICIES):
                    raw = group.loc[self.ranked[u, k, p], 'rating_raw'].to_numpy(dtype=np.float64)
                    nums, dens = conditional.metric_parts(raw, h)
                    require(denominator is None or np.array_equal(dens, denominator), 'metric denominator mismatch')
                    numerators.append(nums); denominator = dens
                    points[u, k, p] = nums / dens
                    rows.append({'user_key': key, 'n': n, 'policy': policy, 'raw_rating_mean': float(raw.mean()),
                                 **{m: float(v) for m, v in zip(conditional.METRICS, points[u, k, p], strict=True)}})
                if k:
                    for pair, (_, a, b) in enumerate(PAIRS):
                        difference = (numerators[a] - numerators[b]) / denominator
                        differences[u, k-1, pair, :, :] = difference[:, None]
            if (u+1) % 500 == 0:
                self.log('FIXED_Q_EVALUATION', users=u+1)
        frame = pd.DataFrame(rows)
        frame.to_parquet(self.root / 'user-metrics.parquet', index=False)
        self.log('PAIRED_BOOTSTRAP_START', repeats=self.cfg['bootstrap_repeats'], family=self.cfg['family_size'])
        intervals = base.ci_bounds(differences, self.cfg['bootstrap_repeats'], self.cfg['bootstrap_seed'], self.cfg['alpha'], self.cfg['family_size'])
        comparisons = []
        for k, n in enumerate(self.cfg['ns'][1:]):
            for pair, (name, _, _) in enumerate(PAIRS):
                for metric, title in enumerate(conditional.METRICS):
                    delta = differences[:, k, pair, metric, 0]
                    low, high = intervals[k, pair, metric]
                    oriented = delta * (-1 if title == 'HARM20' else 1)
                    comparisons.append({'n': n, 'pair': name, 'metric': title, 'difference': float(delta.mean()),
                                        'ci_low': float(low), 'ci_high': float(high), 'direction': conditional.direction(title, low, high),
                                        'better_users': int((oriented > 0).sum()), 'tied_users': int((oriented == 0).sum()), 'worse_users': int((oriented < 0).sum()),
                                        'better_fraction': float((oriented > 0).mean()), 'tied_fraction': float((oriented == 0).mean()), 'worse_fraction': float((oriented < 0).mean())})
        aggregate = frame.groupby(['n', 'policy'], sort=False)[[*conditional.METRICS, 'raw_rating_mean']].mean().reset_index().to_dict('records')
        write_json(self.root / 'metrics.json', {'experiment': 'REC035_INPUTS', 'users': len(self.keys), 'aggregate': aggregate,
                   'comparisons': comparisons, 'bootstrap_repeats': self.cfg['bootstrap_repeats'], 'family_size': 27,
                   'scope': 'FIXED_DEVELOPMENT_E_CONDITIONAL_RANKING', 'real_onboarding_validated': False, 'service_K_selected': False})
        self.seal('evaluate-seal.json', EVALUATE, score_seal=pin(self.root / 'score-seal.json'),
                  labels=pin(self.paths['labels']), histograms=pin(self.paths['histograms']))
        self.log('THREE_WAY_COMPARISON_COMPLETE')

    def run(self):
        require(not (self.root / 'failure.json').exists(), 'failed execution exists; preserve without retry')
        if (self.root / 'completion-seal.json').exists():
            self.sources()
            self.verify('completion-seal.json', OUTPUTS, sources=self.cfg['inputs'], ancestry=self.ancestry)
            self.validate_score()
            self.verify('evaluate-seal.json', EVALUATE, score_seal=pin(self.root / 'score-seal.json'),
                        labels=pin(self.paths['labels']), histograms=pin(self.paths['histograms']))
            print('VERIFIED_EXISTING_COMPLETION_NO_FIT_OR_SCORING', flush=True)
            return
        require(not self.root.exists() or not any(self.root.iterdir()), 'partial output exists; preserve without retry')
        self.root.mkdir(parents=True, exist_ok=True)
        self.budget = Budget(self.root, self.cfg, self.identity)
        self.budget.save(); self.budget.thread.start()
        try:
            self.sources(); self.prepare(); self.train(); self.score(); self.evaluate()
            self.sources(); self.validate_score(); self.guard()
            self.budget.close(); self.guard()
            self.seal('completion-seal.json', OUTPUTS, sources=self.cfg['inputs'], ancestry=self.ancestry, fit_count=1,
                      new_network_requests=0, real_onboarding_validated=False, seconds=self.budget.elapsed())
        except Exception as exc:
            self.budget.close()
            if not (self.root / 'failure.json').exists():
                write_json(self.root / 'failure.json', {'status': 'FAILED', 'fingerprint': self.identity,
                           'error_type': type(exc).__name__, 'error': str(exc)})
            raise


if __name__ == '__main__':
    Run().run()
