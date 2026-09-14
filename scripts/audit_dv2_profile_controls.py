"""Pure, reviewed profile perturbations; no predictor or future-label values."""
from __future__ import annotations
from collections import Counter
from copy import deepcopy
from pathlib import Path
import argparse
import hashlib
import json
import pickle
import re
import sys

import numpy as np
from threadpoolctl import threadpool_limits

ROOT=Path(__file__).resolve().parents[1]
MAIN=Path('C:/higher/projects/FEELM-standalone/.codex-tmp/fixed-k8-discovery-v2-20260913')
BASE=MAIN/'outputs/fixed-k8-discovery-v2/feelm-discovery-v2-r2'
DOC=ROOT/'docs/recommendation/experiments/fixed-k8-discovery-v2'
OUT=ROOT/'outputs/dv2-profile-controls-r2'
POLICY='v1-fixed16:group:mean:B50:Q25'
VARIANTS=['original','cyclic_donor','ratings_permutation','minus_direction']


def pin(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(2**20),b''):h.update(block)
    return {'bytes':Path(path).stat().st_size,'sha256':h.hexdigest()}


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))


def fingerprint():
    return {p.name:pin(p) for p in [Path(__file__),ROOT/'scripts/test_dv2_profile_controls.py',DOC/'PROFILE-CONTROLS-DESIGN.md']}


def profile(x,c):
    history=np.asarray(c['history'],int);stars=np.asarray(c['stars'],float);original=np.asarray(c['original_stars'],float)
    assert len(history)==len(stars) and len(history)<=len(original)<=c['cap']
    support=np.sum(x[history]*x[history],axis=1)>1e-12
    if c['raw_pre_count']==0:state='ACTUAL_NO_HISTORY'
    elif c['cap']==0:state='HIDDEN_CAP0'
    elif not len(original):state='NO_SELECTED_INPUT'
    elif not len(history):state='UNMAPPED_INPUT'
    elif not support.any():state='UNSUPPORTED_INPUT'
    else:state='VALID'
    anchor=3+.5*((original.sum()+5*3.5)/(len(original)+5)-3)
    weights=(stars-anchor)[support]
    raw=np.sum(x[history[support]]*weights[:,None],axis=0)/(np.abs(weights).sum()+5)
    length=float(np.linalg.norm(raw))
    if state=='VALID' and length<=1e-8:state='CANCELLED_OR_TINY'
    direction=raw/length if state=='VALID' else np.zeros(x.shape[1])
    return direction,{'state':state,'anchor':float(anchor),'raw_norm':length,'mapped_inputs':len(history),'supported_inputs':int(support.sum())}


def shuffled_context(c,permutation=None):
    n=len(c['history'])
    perm=np.random.default_rng(913+int(c['uid'])).permutation(n) if permutation is None else np.asarray(permutation,int)
    assert sorted(perm.tolist())==list(range(n))
    copied=deepcopy(c);copied['stars']=np.asarray(c['stars'])[perm].tolist()
    assert copied['history']==c['history'] and copied['original_stars']==c['original_stars'] and copied['viewed']==c['viewed']
    return copied,perm


def recipient_pools(groups,quality_order,c,quota,n_groups):
    viewed=np.asarray(c['viewed'],int);assert len(viewed)==len(set(viewed))
    counts=np.bincount(groups[viewed],minlength=n_groups)
    under=(counts<=2)&(counts/max(1,len(viewed))<=.2)
    seen=set(viewed.tolist());pools={}
    for i in quality_order:
        g=int(groups[i])
        if under[g] and int(i) not in seen and len(pools.get(g,[]))<quota:pools.setdefault(g,[]).append(int(i))
    return np.asarray(sorted(pools),int),pools,counts


def ranked_groups(representatives,direction,legal,state='VALID'):
    legal=np.asarray(legal,int)
    if state!='VALID' or not len(legal):return np.array([],int)
    values=np.asarray(representatives[legal]@direction,float);assert np.isfinite(values).all()
    return legal[np.lexsort((legal,-values))[:5]]


def comparison(base,other,legal,variant_state):
    if not len(legal):state='EMPTY_RECIPIENT_EU'
    elif variant_state!='VALID':state='NO_VALID_VARIANT_VECTOR'
    elif not len(base) or not len(other):state='NO_COMPARABLE_RANK'
    else:state='COMPARABLE'
    if state!='COMPARABLE':return {'comparison_state':state,'top1_changed':None,'top5_order_changed':None,'top5_overlap':None}
    return {'comparison_state':state,'top1_changed':bool(base[0]!=other[0]),
            'top5_order_changed':not np.array_equal(base,other),'top5_overlap':len(set(base)&set(other))/len(base)}


def histories_only(path):
    raw=Path(path).read_bytes()
    redacted,count=re.subn(rb'"ratings"\s*:\s*\[[^\]]*\]',b'"ratings":null',raw)
    assert count==1350
    parsed=json.loads(redacted);assert len(parsed)==1350 and all(c['ratings'] is None for c in parsed)
    keys=['uid','cap','history','stars','original_stars','viewed','raw_pre_count']
    return [{k:c[k] for k in keys} for c in parsed],count


def verify_stage(stage):
    seal=read(BASE/(stage+'-seal.json'))
    actual={str(p.relative_to(BASE)):pin(p) for p in (BASE/stage).rglob('*') if p.is_file()}
    assert actual==seal['files'],stage
    return pin(BASE/(stage+'-seal.json'))


def run():
    review_path=DOC/'profile-controls-execution-review.json';review_pin=pin(review_path);review=read(review_path)
    assert review['status']=='PASS' and review['fingerprint']==fingerprint()
    assert set(review['source_seals'])=={'prepare','cluster','final'}
    initial=fingerprint();seals={s:verify_stage(s) for s in ['prepare','cluster','final']}
    assert seals==review['source_seals'];assert not OUT.exists()
    manifest=read(BASE/'final/manifest.json');p=manifest['policy'];assert p['id']==POLICY and p['rep']=='mean'
    assert manifest['source_seals']['prepare']==seals['prepare'] and manifest['source_seals']['cluster']==seals['cluster']
    assert pin(BASE/'final/bundle.pkl')==manifest['bundle']
    with (BASE/'final/bundle.pkl').open('rb') as f:bundle=pickle.load(f)
    with (BASE/'cluster'/f"{p['hierarchy']}-hierarchy.pkl").open('rb') as f:h=pickle.load(f)
    np.testing.assert_array_equal(bundle['representatives']['mean'],h['representatives']['mean'])
    assert bundle['policy']==p
    rules=bundle['runtime_rules']
    assert rules['profile_prior']==5 and rules['profile_prior_mean']==3.5 and rules['profile_epsilon']==1e-8
    assert rules['underseen_count']==2 and rules['underseen_share']==.2
    x=np.load(BASE/'prepare/content.npy',mmap_mode='r');quality_order=np.load(BASE/'prepare/quality-order.npy')
    contexts,redactions=histories_only(BASE/'prepare/contexts.json');roles=read(BASE/'prepare/roles.json')
    check=set(roles['verification']);assert len(check)==180 and not check&set(roles['validation'])
    options=sorted([c for c in contexts if c['cap']==10 and c['uid'] in check],key=lambda c:c['uid'])
    selected=[c for c in options if profile(x,c)[1]['state']=='VALID'][:20];assert len(selected)==20
    original={c['uid']:profile(x,c) for c in selected};records=[]
    reps=bundle['representatives']['mean'];groups=h['groups'];assert len(groups)==len(x)==237817
    for j,c in enumerate(selected):
        donor=selected[(j+1)%len(selected)];base,base_info=original[c['uid']]
        legal,pools,counts=recipient_pools(groups,quality_order,c,p['quota'],h['n_groups'])
        changed,perm=shuffled_context(c);shuffle,shuffle_info=profile(x,changed)
        assert shuffle_info['anchor']==base_info['anchor']
        donor_direction,donor_info=original[donor['uid']]
        conditions={'original':(base,base_info),'cyclic_donor':(donor_direction,donor_info),
                    'ratings_permutation':(shuffle,shuffle_info),'minus_direction':(-base,base_info)}
        baseline=ranked_groups(reps,base,legal,base_info['state'])
        frozen_legal=legal.copy()
        for variant,(direction,info) in conditions.items():
            ranking=ranked_groups(reps,direction,legal,info['state']);np.testing.assert_array_equal(legal,frozen_legal)
            records.append({'uid':int(c['uid']),'variant':variant,'donor_uid':int(donor['uid']) if variant=='cyclic_donor' else None,
                'counterfactual':variant!='original',
                'recipient_legal_groups':legal.tolist(),'recipient_group_viewed_counts':counts.tolist(),
                'recipient_prefix_sizes':{str(g):len(v) for g,v in pools.items()},'mapped_stars':c['stars'],
                'ratings_permutation':perm.tolist() if variant=='ratings_permutation' else None,
                'permuted_stars':changed['stars'] if variant=='ratings_permutation' else None,
                'uniform_mapped_stars':len(set(c['stars']))<=1,'permutation_identity':bool(np.array_equal(perm,np.arange(len(perm)))),
                'permutation_stars_unchanged':changed['stars']==c['stars'],'profile':info,'direction_l2_from_original':float(np.linalg.norm(direction-base)),
                'top1':int(ranking[0]) if len(ranking) else None,'top5':ranking.tolist(),
                **comparison(baseline,ranking,legal,info['state'])})
    summaries=[]
    for variant in VARIANTS:
        rows=[r for r in records if r['variant']==variant];comp=[r for r in rows if r['comparison_state']=='COMPARABLE']
        summaries.append({'variant':variant,'users':len(rows),'comparable_users':len(comp),
            'comparison_states':dict(Counter(r['comparison_state'] for r in rows)),
            'profile_states':dict(Counter(r['profile']['state'] for r in rows)),
            'top1_changed':sum(r['top1_changed'] for r in comp),'top1_unchanged':sum(not r['top1_changed'] for r in comp),
            'top5_order_changed':sum(r['top5_order_changed'] for r in comp),'top5_order_unchanged':sum(not r['top5_order_changed'] for r in comp),
            'mean_top5_overlap':float(np.mean([r['top5_overlap'] for r in comp])) if comp else None,
            'direction_unchanged':sum(r['direction_l2_from_original']<=1e-12 for r in rows),
            'uniform_mapped_stars_users':sum(r['uniform_mapped_stars'] for r in rows),
            'identity_permutation_users':sum(r['permutation_identity'] for r in rows) if variant=='ratings_permutation' else None,
            'unchanged_stars_permutation_users':sum(r['permutation_stars_unchanged'] for r in rows) if variant=='ratings_permutation' else None,
            'top1_group_counts':dict(Counter(str(r['top1']) for r in rows if r['top1'] is not None)),
            'top5_group_inclusion_counts':dict(Counter(str(g) for r in rows for g in r['top5']))})
    assert not any(name.startswith(('dv2_predictor','combination340_models','final344_adapter')) for name in sys.modules)
    assert fingerprint()==initial and pin(review_path)==review_pin
    assert {s:verify_stage(s) for s in seals}==seals
    result={'status':'PASS','scope':'Descriptive pure-profile/frozen-representative control diagnostic; no prediction or reselection',
            'fingerprint':initial,'source_seals':seals,'execution_review':review_pin,'policy':p,
            'user_selection':'First20 originalVALID cap10 checkusers in uid ascending order, including emptyE_u recipients',
            'counterfactual_definition':'Donor direction, permuted film-rating pairings and minusdirection are synthetic controls; they are not newly observed user preferences.',
            'uids':[int(c['uid']) for c in selected],'summary':summaries,'future_rating_arrays_redacted_before_json_parse':redactions,
            'future_label_values_parsed':False,'predictor_modules_loaded':False,'predictor_calls':0,
            'limits':['Recipient E_u is fixed within comparisons, not transferred from donor.','Real rank change is not required; no resampling or model choice follows these results.',
                      'All calculations use original frozen catalog before append/delete diagnostics.','These controls verify input sensitivity, not preference correctness or discovery satisfaction.']}
    OUT.mkdir(parents=True)
    for name,value in [('report.json',result),('records.json',records)]:
        (OUT/name).write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    (OUT/'seal.json').write_text(json.dumps({'status':'PASS','files':{p.name:pin(p) for p in OUT.iterdir() if p.is_file()}},indent=2),encoding='utf-8')
    print(json.dumps({'status':'PASS','uids':result['uids'],'summary':summaries,'report':pin(OUT/'report.json'),'seal':pin(OUT/'seal.json')}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',action='store_true');args=parser.parse_args()
    if args.run:
        with threadpool_limits(limits=2):run()
    else:print(json.dumps({'fingerprint':fingerprint()}))
