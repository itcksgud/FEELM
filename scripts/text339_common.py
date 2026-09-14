"""Jira339 local research gates. Never changes prior experiment artifacts."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
from rec046_common import require, pin, write_json, digest

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / 'docs/recommendation/experiments/text339'
OUT = ROOT / 'outputs/recommendation-evidence/text339'
WIKI = ROOT / 'outputs/recommendation-evidence/wikipedia-plots-v3'
READY = ROOT / 'outputs/recommendation-evidence/rec-ev-047-readiness'
OLD = ROOT / 'outputs/recommendation-evidence/rec-ev-047/evaluation'
HORIZON = 180 * 86400
CAPS = [0, 1, 5, 10, 30]
VARIANTS = ['T0', 'T1', 'T2', 'T3']
FILES = ['text339_common.py', 'text339_text.py', 'text339_features.py',
         'text339_encode.py',
         'text339_repair_overviews.py', 'text339_apply_usage_review.py', 'report_text339.py',
         'text339_relations.py',
         'text339_prepare.py', 'text339_worker.py', 'text339_run.py',
         'text339_evaluate.py', 'test_text339.py', 'cold_item_features.py',
         'rec047_features.py', 'rec046_common.py', 'rec047_common.py',
         'rec047_readiness.py', 'collect_wikipedia_plots.py']

def config():
    return json.loads((DOC / 'config.json').read_text(encoding='utf-8'))

def fingerprint():
    return {p.relative_to(ROOT).as_posix(): pin(p) for p in
            [ROOT / 'scripts' / n for n in FILES] + [DOC / 'EXECUTION.md', DOC / 'config.json']}

def reviewed():
    r = json.loads((DOC / 'execution-review.json').read_text(encoding='utf-8'))
    require(r['status'] == 'PASS' and r['fingerprint'] == fingerprint(), 'exact execution review required')
    for name, expected in config()['sources'].items():
        require(pin(ROOT / name) == expected, 'source drift: ' + name)
    return r

def seal(name, files, **extra):
    path = OUT / name
    require(not path.exists(), 'preserve seal ' + name)
    write_json(path, {'fingerprint': fingerprint(), 'files': {n: pin(OUT / n) for n in files}, **extra})

def verify(name):
    r = json.loads((OUT / name).read_text(encoding='utf-8'))
    require(r['fingerprint'] == fingerprint(), 'execution drift ' + name)
    for n, expected in r['files'].items():
        require(pin(OUT / n) == expected, 'output drift ' + n)
    for key,parent in [('prepared_seal','prepared-seal.json'),('fit_seal','fit-seal.json'),('catalog_seal','catalog-seal.json'),('embedding_seal','embedding-seal.json')]:
        if key in r:require(pin(OUT/parent)==r[key],'parent seal drift '+parent)
    return r

def activity(n):
    return ['0', '1_9', '10_29', '30_99', '100_PLUS'][np.searchsorted([1, 10, 30, 100], n, side='right')]

def support(n):
    return np.asarray(['0', '1_9', '10_49', '50_PLUS'])[np.searchsorted([1, 10, 50], n, side='right')]

def allocated_cap(uid, start):
    return CAPS[int.from_bytes(digest('text339-cap', uid, start)[:8], 'big') % len(CAPS)]

def check_contexts(contexts,catalog):
    from rec047_readiness import role
    ids=catalog.movie_id.to_numpy();origin=config()['origin'];offset=0;seen=set();per_user={}
    require(np.array_equal(ids,np.unique(ids)),'sorted unique catalog')
    for c in contexts:
        uid,cap=c['uid'],c['cap'];key=(uid,cap)
        oi,ei,viewed=np.asarray(c['oi'],int),np.asarray(c['ei'],int),np.asarray(c['viewed'],int)
        ot,et=np.asarray(c['input_timestamps']),np.asarray(c['target_timestamps'])
        require(role(uid)=='evaluation' and cap in CAPS and key not in seen,'context role/cap/uniqueness');seen.add(key)
        require(c['start']==offset and c['stop']==offset+len(ei) and len(ei)>0,'contiguous nonempty score rows')
        require(len(oi)==len(c['stars'])==len(ot)==c['h']==min(cap,len(viewed)),'capped input size')
        require(np.isin(c['stars'],np.arange(1,11)/2).all(),'context half stars')
        for axis in [oi,ei,viewed]:require(len(np.unique(axis))==len(axis) and ((axis>=0)&(axis<len(ids))).all(),'unique in-bound movie IDs')
        require(np.isin(oi,viewed).all() and not np.intersect1d(viewed,ei).size,'observed history separation')
        require((ot<origin).all() and len(et)==len(ei) and ((et>=origin)&(et<origin+HORIZON)).all(),'context times')
        require(catalog.released_at_origin.to_numpy()[ei].all(),'observed candidates released by origin')
        signature=(tuple(ei),tuple(et),tuple(viewed))
        require(uid not in per_user or per_user[uid]==signature,'same candidate pool across caps');per_user[uid]=signature
        offset=c['stop']
    require(all({cap for u,cap in seen if u==uid}==set(CAPS) for uid in per_user),'all caps for every user')
    return offset
