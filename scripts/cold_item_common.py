"""Local cold-item experiment: immutable inputs and explicit execution gates."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
from rec046_common import pin, require, write_json, digest
from rec047_common import H, activity, support_group, window_start, assigned_k, role

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / 'docs/recommendation/experiments/cold-item-content'
OUT = ROOT / 'outputs/recommendation-evidence/cold-item-content'
OLD = ROOT / 'outputs/recommendation-evidence/rec-ev-047'
READY = ROOT / 'outputs/recommendation-evidence/rec-ev-047-readiness'
KS = [1, 5, 10, 30]
VARIANTS = ['BASE', 'SUPPORT', 'CROWD_ITEM', 'CROWD_RESPONSE']
METHODS = ['FM', 'RIDGE', 'GBT']
PREDICTION_NAMES = ['ALS'] + [v+'__'+m for v in VARIANTS for m in ['RIDGE','GBT','FM']]
FILES = ['cold_item_common.py', 'cold_item_features.py', 'cold_item_prepare.py',
         'cold_item_worker.py', 'cold_item_run.py', 'cold_item_evaluate.py',
         'test_cold_item.py', 'rec047_features.py', 'rec046_common.py',
         'rec047_common.py', 'rec047_readiness.py']

def config():
    return json.loads((DOC / 'config.json').read_text(encoding='utf-8'))

def fingerprint():
    paths = [ROOT / 'scripts' / f for f in FILES] + [DOC / 'EXECUTION.md', DOC / 'config.json']
    return {p.relative_to(ROOT).as_posix(): pin(p) for p in paths}

def reviewed():
    r = json.loads((DOC / 'execution-review.json').read_text(encoding='utf-8'))
    require(r['status'] == 'PASS' and r['fingerprint'] == fingerprint(), 'reviewed code required')
    for name, expected in config()['sources'].items():
        require(pin(ROOT / name) == expected, 'source changed: ' + name)
    return r

def verify_seal(path, base):
    r = json.loads(path.read_text(encoding='utf-8'))
    require(r['fingerprint'] == fingerprint(), 'execution fingerprint drift')
    for name, expected in r['files'].items():
        require(pin(base / name) == expected, 'sealed output drift: ' + name)
    return r

def movie_partition(metadata, pre_v_counts):
    require(metadata.movie_id.is_unique and metadata.tmdb_id.is_unique, 'canonical movie identity unique')
    masks = np.full(len(metadata), 'W', dtype='<U1')
    strata = support_group(pre_v_counts)
    for group in ['1_9', '10_49', '50_PLUS']:
        selected = np.flatnonzero((strata == group) & metadata.feature_eligible.to_numpy(bool))
        selected = sorted(selected, key=lambda i: digest('cold-item-47', int(metadata.tmdb_id.iloc[i])))
        n = len(selected) // 10
        masks[np.asarray(selected[:n], int)] = 'V'
        masks[np.asarray(selected[n:2*n], int)] = 'E'
    return masks

def check_contexts(contexts, catalog):
    ids = catalog['movie_ids']; part = catalog['partition']; counts = catalog['reference_counts']
    require(np.array_equal(ids, np.unique(ids)) and len(ids)==len(part)==len(counts), 'catalog axes')
    origin = config()['origin']; offset = 0; seen = set()
    for c in contexts:
        key=(c['uid'],c['k']);oi=np.asarray(c['oi'],int);ei=np.asarray(c['ei'],int)
        ot=np.asarray(c['input_timestamps']);et=np.asarray(c['target_timestamps'])
        require(key not in seen and c['k'] in KS and role(c['uid'])=='evaluation', 'context role/unique user K')
        seen.add(key)
        require(c['start']==offset and c['stop']==offset+len(ei) and len(ei)>0, 'contiguous contexts')
        require(len(oi)==len(c['stars'])==len(ot)==c['k'] and len(et)==len(ei), 'context lengths')
        require(np.isin(c['stars'],np.arange(1,11)/2).all(), 'input half stars')
        require((ot<origin).all() and ((et>=origin)&(et<origin+H)).all(), 'context times')
        for axis in [oi,ei]:
            require(axis.ndim==1 and len(np.unique(axis))==len(axis) and ((axis>=0)&(axis<len(ids))).all(), 'movie axis')
        require(not np.intersect1d(oi,ei).size and (part[oi]=='W').all() and (part[ei]=='E').all(), 'input/target movie separation')
        require((counts[oi]>0).all() and (counts[ei]>0).all() and c['pre_warm']>=c['k'], 'actual reference support')
        offset=c['stop']
    require(offset>0, 'nonempty contexts')
    return offset

def concordance(truth, predicted, tol=1e-12):
    """Unequal reference pairs, prediction ties get half credit."""
    truth, predicted = np.asarray(truth), np.asarray(predicted)
    i, j = np.triu_indices(len(truth), 1)
    dt, dp = truth[i] - truth[j], predicted[i] - predicted[j]
    use = abs(dt) > tol
    if not use.any():
        return None, 0
    hit = np.where(abs(dp[use]) <= tol, .5, (dt[use] * dp[use] > 0).astype(float))
    return float(hit.mean()), int(use.sum())
