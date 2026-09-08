"""One fixed-n catalogue supply diagnostic. No evaluation labels or new fitting."""
from __future__ import annotations
from collections import defaultdict
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits
import feelm_policy_states as policy
import rec_ev_033_tastes as util

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / 'docs/recommendation/experiments/rec-ev-034'
PLANS = ROOT / 'docs/recommendation/plans/service-policy-redesign'
FILES = {'design': PLAN / 'README.md', 'config': PLAN / 'config.json', 'runner': Path(__file__).resolve(),
    'tests': ROOT / 'scripts/tests/test_rec_ev_034_supply.py', 'state_runner': Path(policy.__file__).resolve(),
    'role_design': PLANS / 'stage-4-policy-proposal.md', 'supply_design': PLANS / 'stage-4-supply-design.md',
    'helpers': Path(util.__file__).resolve()}
DATA = ('ranking-prefixes.npz', 'user-supply.parquet', 'slots.parquet', 'summary.json')
OUTPUTS = ('role-seal.json', *DATA, 'score-seal.json', 'budget.json')
require, pin, read_json, write_json = util.require, util.pin, util.read_json, util.write_json


def fingerprint():
    return {key: util.sha(path) for key, path in FILES.items()}


def reviewed():
    value = read_json(PLAN / 'review.json'); actual = fingerprint()
    require(value['status'] == 'PASS' and value['fingerprint'] == actual, 'review absent or stale')
    return actual


def fold_scores(y, has, observed, ratings, reg=.1):
    chosen = observed[has[observed]]
    if not len(chosen):
        return None, 'NO_FACTOR', 0
    local = y[chosen]
    try:
        vector = np.linalg.solve(local.T @ local + reg * len(chosen) * np.eye(y.shape[1]), local.T @ ratings[has[observed]])
    except np.linalg.LinAlgError:
        return None, 'SOLVE_FAILED', len(chosen)
    if not np.isfinite(vector).all():
        return None, 'NONFINITE_VECTOR', len(chosen)
    if np.linalg.norm(vector) <= 1e-12:
        return None, 'ZERO_VECTOR', len(chosen)
    values = y @ vector
    if not np.isfinite(values).all():
        return None, 'NONFINITE_SCORE', len(chosen)
    return values, 'ACTIVE', len(chosen)


def inverted_features(feature_rows):
    inverted = defaultdict(list)
    token_rows = []
    for pos, row in enumerate(feature_rows):
        tokens = {(field, int(tid)) for field in ('genres', 'directors', 'keywords') for tid in row[field]}
        token_rows.append(tokens)
        for token in tokens:
            inverted[token].append(pos)
    return token_rows, {key: np.asarray(value, dtype=np.int64) for key, value in inverted.items()}


def eligibility(labels, observed, weights, token_rows, inverted, count=8):
    local = labels[observed]
    rated_count = np.bincount(local[local >= 0], minlength=count)
    sums = np.bincount(local[local >= 0], weights=weights[local >= 0], minlength=count)
    means = np.divide(sums, rated_count, out=np.zeros(count), where=rated_count > 0)
    # Match the validated state module's stable summation exactly.
    for k in np.flatnonzero(rated_count):
        import math
        means[k] = math.fsum(weights[local == k].tolist()) / rated_count[k]
    positive = means > 0
    assigned = labels >= 0
    safe = np.maximum(labels, 0)
    taste = assigned & positive[safe]
    novel = assigned & ~positive[safe] & (rated_count[safe] == 0)
    anchors = observed[(local >= 0) & (weights > 0) & positive[np.maximum(local, 0)]]
    connected = np.zeros(len(labels), dtype=bool)
    tokens = set().union(*(token_rows[pos] for pos in anchors)) if len(anchors) else set()
    for token in tokens:
        connected[inverted[token]] = True
    discovery = novel & connected
    require(not (taste & discovery).any(), 'T/D overlap')
    return taste, discovery, anchors, {'positive_tastes': int(positive.sum()),
        'observed_experience_proxy_tastes': int((rated_count > 0).sum()),
        'unassigned_inputs': int((local < 0).sum()), 'positive_anchors': len(anchors),
        'novel_proxy_tastes': int(((rated_count == 0) & ~positive).sum())}


def select_slots(order, taste, discovery, excluded):
    available = order[~excluded[order]]
    t, d = available[taste[available]], available[discovery[available]]
    if len(t) < 2:
        return 'INSUFFICIENT_TASTE', [], [], len(t), len(d)
    if len(d):
        return 'T2_D1', t[:2].tolist() + d[:1].tolist(), ['TASTE', 'TASTE', 'DISCOVERY'], len(t), len(d)
    if len(t) < 3:
        return 'INSUFFICIENT_TASTE_FALLBACK', [], [], len(t), 0
    return 'NO_DISCOVERY_T3', t[:3].tolist(), ['TASTE'] * 3, len(t), 0


def stage_orders(raw, taste, discovery, limit500=500, limit100=100):
    first = raw[:limit500]
    typed = first[(taste | discovery)[first]]
    return {'FULL_ALS': raw, 'CANDIDATE500': typed, 'TOPN100': typed[:limit100]}, first, typed


def aggregate(frame, users):
    groups = []
    for name, part in frame.groupby('policy', sort=True):
        require(len(part) == users and part.user_key.is_unique, 'lost or repeated users')
        groups.append({'policy': name, 'users': users,
            'status_counts': part.status100.value_counts().to_dict(),
            'D_absence_reason_counts': part.d_absence_reason.value_counts().to_dict(),
            'ALS_activity_counts': part.als_status.value_counts().to_dict(),
            'full_T2_D1_users': int((part.status_full == 'T2_D1').sum()),
            'candidate500_T2_D1_users': int((part.status500 == 'T2_D1').sum()),
            'top100_T2_D1_users': int((part.status100 == 'T2_D1').sum()),
            'top100_T2_D1_fraction': float((part.status100 == 'T2_D1').mean()),
            'D_lost_at_ALS_support_users': int((part.d_absence_reason == 'NO_ALS_SUPPORT').sum()),
            'D_lost_at_500_users': int((part.d_absence_reason == 'LOST_AT_500').sum()),
            'D_lost_at_100_users': int((part.d_absence_reason == 'LOST_AT_100').sum()),
            'mean_positive_tastes': float(part.positive_tastes.mean()),
            'mean_observed_experience_proxy_tastes': float(part.observed_experience_proxy_tastes.mean()),
            'users_with_unassigned_inputs': int((part.unassigned_inputs > 0).sum())})
    paired = frame.pivot(index='user_key', columns='policy', values='status100')
    a, b = paired['A_GENRE'].eq('T2_D1'), paired['B_KMEANS'].eq('T2_D1')
    return {'users': users, 'by_policy': groups, 'paired_T2_D1': {'both': int((a & b).sum()),
        'A_only': int((a & ~b).sum()), 'B_only': int((~a & b).sum()), 'neither': int((~a & ~b).sum())},
        'claim_scope': 'OBSERVED_INPUT_N30_PROXY_CATALOGUE_SUPPLY_ONLY', 'quality_evaluated': False,
        'winner_selected': False, 'final_K_selected': False, 'new_model_fits': 0,
        'actual_full_watch_history_available': False, 'evaluation_labels_opened': False}


class Run:
    def __init__(self):
        self.identity = reviewed(); self.cfg = read_json(FILES['config']); self.budget = None
        c = self.cfg
        require(c['experiment'] == 'REC034_SUPPLY_N30' and c['expected_users'] == 2180 and c['expected_items'] == 85517
                and c['n'] == 30 and c['candidate_size'] == 500 and c['topn_size'] == 100
                and c['rank'] == 32 and c['reg'] == .1 and c['threads'] == 1, 'execution setting drift')
        self.root = ROOT / c['output_root']; self.paths = {}
        require(self.root.resolve() == (ROOT / 'outputs/recommendation-evidence/rec-ev-034').resolve(), 'output root')

    def guard(self):
        require(reviewed() == self.identity, 'review changed')
        if self.budget: self.budget.guard()

    def sources(self):
        for name, spec in self.cfg['inputs'].items():
            path = (ROOT / spec['path']).resolve()
            require(path.is_relative_to(ROOT.resolve()) and pin(path) == {key: spec[key] for key in ('bytes', 'sha256')}, 'source drift: ' + name)
            self.paths[name] = path; self.guard()
        prep, train = read_json(self.paths['prepare_seal']), read_json(self.paths['train_seal'])
        require(prep['status'] == train['status'] == 'COMPLETE' and prep['overlap'] == 0
            and prep['evaluation_users'] == 2180 and train['training_users'] == 46376
            and train['prepare_seal'] == pin(self.paths['prepare_seal']), 'ALS lineage')
        for name in ('prepared', 'profiles'):
            require(prep['outputs'][self.paths[name].name] == pin(self.paths[name]), 'prepared source mismatch')
        require(train['outputs']['factors.npz'] == pin(self.paths['factors']), 'factor lineage')
        parent = read_json(self.paths['taste_completion'])
        audit = read_json(self.paths['taste_audit'])
        require(parent['status'] == 'COMPLETE' and audit['status'] == 'PASS'
                and audit['completion'] == pin(self.paths['taste_completion']), 'taste audit absent')
        for filename, expected in parent['outputs'].items():
            require(pin(self.paths['taste_completion'].parent / filename) == expected, 'taste output drift')
        require(read_json(self.paths['state_review'])['status'] == 'PASS', 'state review absent')
        for name in ('assignments', 'taste_metadata'):
            require(parent['outputs'][self.paths[name].name] == pin(self.paths[name]), 'taste source mismatch')

    def seal(self, name, outputs, **fields):
        self.guard(); write_json(self.root / name, {'status': 'COMPLETE', 'fingerprint': self.identity,
            'outputs': {filename: pin(self.root / filename) for filename in outputs}, **fields})

    def verify(self, name, outputs, **dependencies):
        value = read_json(self.root / name)
        require(value['status'] == 'COMPLETE' and value['fingerprint'] == self.identity and set(value['outputs']) == set(outputs), 'seal header')
        for field, expected in dependencies.items(): require(value[field] == expected, 'seal dependency')
        for filename, expected in value['outputs'].items(): require(pin(self.root / filename) == expected, 'output drift')

    def load(self):
        self.seal('role-seal.json', (), sources=self.cfg['inputs'], n=30, users=2180,
            profile_values_decoded=False, evaluation_labels_allowed=False, full_experience_available=False)
        with np.load(self.paths['prepared'], allow_pickle=False) as z:
            self.ids, self.prior = z['item_ids'].astype(np.int64), z['prior'].astype(np.float64)
        require(len(self.ids) == 85517 and (np.diff(self.ids) > 0).all(), 'catalogue IDs')
        self.profiles = pd.read_parquet(self.paths['profiles']).sort_values('user_key').reset_index(drop=True)
        require(len(self.profiles) == 2180 and self.profiles.user_key.is_unique, 'profile users')
        with np.load(self.paths['factors'], allow_pickle=False) as z:
            fids, fy = z['item_ids'].astype(np.int64), z['factors'].astype(np.float64)
        require((np.diff(fids) > 0).all() and fy.shape == (len(fids), 32) and np.isfinite(fy).all(), 'factor shape/value')
        match = np.searchsorted(fids, self.ids)
        self.has = (match < len(fids)) & (fids[np.minimum(match, len(fids) - 1)] == self.ids)
        self.y = np.zeros((len(self.ids), 32)); self.y[self.has] = fy[match[self.has]]
        assignment = pd.read_parquet(self.paths['assignments']).set_index('movie_id').loc[self.ids]
        metadata = pd.read_parquet(self.paths['taste_metadata']).set_index('movie_id').loc[self.ids]
        structure = pd.read_parquet(self.paths['structured']).set_index('movie_id').loc[self.ids]
        require(not assignment.index.duplicated().any() and not metadata.index.duplicated().any(), 'duplicate taste ID')
        a_codes = list(self.cfg['A_codes']); b_codes = list(self.cfg['B_codes'])
        require(len(a_codes) == len(set(a_codes)) == len(b_codes) == len(set(b_codes)) == 8, 'taste codes')
        require(set(assignment.genre_code.dropna()) == set(a_codes) and set(assignment.semantic_code) == set(b_codes), 'taste code mismatch')
        self.labels = {'A_GENRE': np.array([a_codes.index(v) if v in a_codes else -1 for v in assignment.genre_code]),
                       'B_KMEANS': np.array([b_codes.index(v) for v in assignment.semantic_code])}
        feature_rows = []
        for mid, row in structure.iterrows():
            m = metadata.loc[mid]
            require(sorted(row.genre_ids) == sorted(m.genre_ids) and sorted(row.keyword_ids) == sorted(m.keyword_ids), 'structure/taste metadata mismatch')
            feature_rows.append({'genres': row.genre_ids, 'directors': row.director_ids, 'keywords': row.keyword_ids})
        self.tokens, self.inverted = inverted_features(feature_rows)

    def score(self):
        users = len(self.profiles); prefix_ids = np.full((users, 500), -1, dtype=np.int64)
        prefix_scores = np.full((users, 500), np.nan); lengths = np.zeros(users, dtype=np.int64)
        supply_rows, slot_rows = [], []; original_hash = hashlib.sha256(); inactive = 0
        for u, row in enumerate(self.profiles.itertuples(index=False)):
            movies = np.asarray(row.profile_movie_ids, dtype=np.int64); indices = np.asarray(row.profile_rating_indices)
            require(len(movies) == len(set(movies)) == len(indices) == 30 and np.isin(movies, self.ids).all()
                and np.equal(indices, indices.astype(np.int64)).all() and ((indices >= 0) & (indices < 10)).all(), 'input grid/scope')
            observed = np.searchsorted(self.ids, movies); ratings = (indices.astype(np.float64) + 1) / 2
            weights = np.asarray(policy.relative_weights(ratings.tolist(), self.prior.tolist()))
            excluded = np.zeros(len(self.ids), dtype=bool); excluded[observed] = True
            values, als_status, n_factor = fold_scores(self.y, self.has, observed, ratings)
            raw = np.array([], dtype=np.int64)
            if als_status == 'ACTIVE':
                supported = np.flatnonzero(self.has)
                raw = supported[np.lexsort((self.ids[supported], -values[supported]))]
                reference = np.where(self.has & ~excluded, values, -np.inf)
                original_hash.update(reference.astype('<f8').tobytes())
                take = raw[:500]; lengths[u] = len(take); prefix_ids[u, :len(take)] = self.ids[take]; prefix_scores[u, :len(take)] = values[take]
            else:
                inactive += 1
            for name, labels in self.labels.items():
                taste, discovery, anchors, info = eligibility(labels, observed, weights, self.tokens, self.inverted)
                stages, first, typed = stage_orders(raw, taste, discovery)
                rec = {'user_key': row.user_key, 'policy': name, 'n': 30, 'als_status': als_status, 'n_factor': n_factor, **info,
                    'catalogue_unassigned': int((labels < 0).sum()), 'catalogue_T': int(taste.sum()), 'catalogue_D': int(discovery.sum()),
                    'catalogue_neither': int((~(taste | discovery)).sum()),
                    'factorless_T': int((taste & ~self.has).sum()), 'factorless_D': int((discovery & ~self.has).sum()),
                    'raw500_size': len(first), 'raw500_T': int(taste[first].sum()), 'raw500_D': int(discovery[first].sum()),
                    'raw500_neither': int((~(taste | discovery)[first]).sum()), 'raw500_observed': int(excluded[first].sum()),
                    'typed500_size': len(typed), 'top100_before_exclusion': len(stages['TOPN100'])}
                for stage, order in stages.items():
                    status, positions, kinds, t_count, d_count = select_slots(order, taste, discovery, excluded)
                    if als_status != 'ACTIVE': status = 'ALS_INACTIVE'
                    suffix = {'FULL_ALS': 'full', 'CANDIDATE500': '500', 'TOPN100': '100'}[stage]
                    rec['status' + ('_' if suffix == 'full' else '') + suffix] = status
                    rec['T' + suffix], rec['D' + suffix] = t_count, d_count
                    facts = []
                    for pos, kind in zip(positions, kinds):
                        if kind == 'DISCOVERY':
                            facts = [{'anchor_movie_id': int(self.ids[anchor]), 'field': field, 'id': int(tid)}
                                for anchor in sorted(anchors, key=lambda p: self.ids[p])
                                for field, tid in sorted(self.tokens[pos] & self.tokens[anchor])]
                            require(bool(facts), 'D without connection fact')
                    require(len(set(positions)) == len(positions) and not excluded[positions].any(), 'duplicate/excluded output')
                    slot_rows.append({'user_key': row.user_key, 'policy': name, 'stage': stage, 'status': status,
                        'movie_ids': self.ids[positions].tolist(), 'types': kinds, 'D_connection_facts_json': json.dumps(facts, sort_keys=True)})
                rec['d_absence_reason'] = ('ALS_INACTIVE' if als_status != 'ACTIVE' else 'AVAILABLE' if rec['D100'] else
                    'NO_ELIGIBLE_METADATA' if not discovery.any() else 'NO_ALS_SUPPORT' if not rec['Dfull'] else
                    'LOST_AT_500' if not rec['D500'] else 'LOST_AT_100')
                supply_rows.append(rec)
            if (u + 1) % 100 == 0:
                self.guard(); print({'phase': 'SUPPLY_N30', 'users': u + 1, 'seconds': round(self.budget.elapsed(), 2)}, flush=True)
        reference_seal = read_json(self.paths['als_score_seal'])
        exact_match = inactive == 0 and original_hash.hexdigest() == reference_seal['full_score_sha256'][3]
        require(inactive > 0 or exact_match, 'existing ALS n30 full-score hash mismatch')
        np.savez_compressed(self.root / 'ranking-prefixes.npz', user_keys=self.profiles.user_key.to_numpy(dtype=str),
            movie_ids=prefix_ids, scores=prefix_scores, lengths=lengths)
        frame = pd.DataFrame(supply_rows); frame.to_parquet(self.root / 'user-supply.parquet', index=False)
        pd.DataFrame(slot_rows).to_parquet(self.root / 'slots.parquet', index=False)
        summary = aggregate(frame, users); summary.update(inactive_users=inactive, existing_ALS_n30_exact_score_hash_match=exact_match,
            full_score_sha256=original_hash.hexdigest(), factor_supported_movies=int(self.has.sum()))
        write_json(self.root / 'summary.json', summary)
        self.seal('score-seal.json', DATA, role_seal=pin(self.root / 'role-seal.json'), sources=self.cfg['inputs'], labels_opened=False)

    def run(self):
        require(not (self.root / 'failure.json').exists(), 'failed execution; preserve without retry')
        if (self.root / 'completion-seal.json').exists():
            self.sources(); self.verify('completion-seal.json', OUTPUTS, sources=self.cfg['inputs'])
            self.verify('role-seal.json', (), sources=self.cfg['inputs'])
            self.verify('score-seal.json', DATA, role_seal=pin(self.root / 'role-seal.json'), sources=self.cfg['inputs'])
            print('VERIFIED_EXISTING_COMPLETION_NO_SCORING'); return
        require(not self.root.exists() or not any(self.root.iterdir()), 'partial execution; preserve without retry')
        self.root.mkdir(parents=True, exist_ok=True); self.budget = util.Budget(self.root, self.cfg, self.identity)
        self.budget.save(); self.budget.thread.start()
        try:
            self.sources(); self.load()
            with threadpool_limits(limits=1): self.score()
            self.sources(); self.guard(); self.budget.close(); self.guard()
            self.seal('completion-seal.json', OUTPUTS, sources=self.cfg['inputs'], new_model_fits=0, evaluation_labels_opened=False)
            print({'phase': 'SUPPLY_DIAGNOSTIC_COMPLETE', 'seconds': round(self.budget.elapsed(), 2)}, flush=True)
        except Exception as exc:
            self.budget.close()
            if not (self.root / 'failure.json').exists():
                write_json(self.root / 'failure.json', {'status': 'FAILED', 'fingerprint': self.identity, 'error_type': type(exc).__name__, 'error': str(exc)})
            raise


if __name__ == '__main__':
    Run().run()
