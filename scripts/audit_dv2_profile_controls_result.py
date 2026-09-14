"""Recompute sealed pure-profile controls without importing their implementation."""
from pathlib import Path
from collections import Counter
import hashlib
import json
import pickle
import re
import sys
import numpy as np
from threadpoolctl import threadpool_limits

OWN=Path(__file__).resolve().parents[1]
BASE=Path('C:/higher/projects/FEELM-standalone/.codex-tmp/fixed-k8-discovery-v2-20260913/outputs/fixed-k8-discovery-v2/feelm-discovery-v2-r2')
OUT=OWN/'outputs/dv2-profile-controls-r2'
def pin(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return {'bytes':p.stat().st_size,'sha256':h.hexdigest()}
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def vector(x,c,stars=None):
    original=c['original_stars'];stars=c['stars'] if stars is None else stars
    anchor=3+.5*((sum(original)+17.5)/(len(original)+5)-3)
    numerator=np.zeros(x.shape[1],float);denominator=5.;support=0
    for i,s in zip(c['history'],stars):
        xi=np.asarray(x[i],float)
        if float(xi@xi)>1e-12:
            support+=1;w=s-anchor;numerator+=w*xi;denominator+=abs(w)
    raw=numerator/denominator;norm=float(np.linalg.norm(raw))
    if c['raw_pre_count']==0:state='ACTUAL_NO_HISTORY'
    elif c['cap']==0:state='HIDDEN_CAP0'
    elif not original:state='NO_SELECTED_INPUT'
    elif not c['history']:state='UNMAPPED_INPUT'
    elif not support:state='UNSUPPORTED_INPUT'
    elif norm<=1e-8:state='CANCELLED_OR_TINY'
    else:state='VALID'
    return (raw/norm if state=='VALID' else np.zeros_like(raw)),{'state':state,'anchor':anchor,'raw_norm':norm,'mapped_inputs':len(c['history']),'supported_inputs':support}
def run():
    report=read(OUT/'report.json');rows=read(OUT/'records.json');seal=read(OUT/'seal.json')
    assert {n:pin(OUT/n) for n in seal['files']}==seal['files']
    assert pin(OUT/'report.json')=={'bytes':6249,'sha256':'e91509760b77f75cb0326e50de657d2a5343b03bee5e7484b3732361f4d9580a'}
    for stage,expected in report['source_seals'].items():
        assert pin(BASE/(stage+'-seal.json'))==expected
        source_seal=read(BASE/(stage+'-seal.json'))
        assert {str(p.relative_to(BASE)):pin(p) for p in (BASE/stage).rglob('*') if p.is_file()}==source_seal['files']
    raw,n=re.subn(rb'"ratings"\s*:\s*\[[^\]]*\]',b'"ratings":null',(BASE/'prepare/contexts.json').read_bytes())
    assert n==1350
    contexts=json.loads(raw);assert all(c['ratings'] is None for c in contexts)
    roles=read(BASE/'prepare/roles.json');assert len(roles['verification'])==180
    assert not set(roles['verification'])&set(roles['validation'])
    x=np.load(BASE/'prepare/content.npy',mmap_mode='r');q=np.load(BASE/'prepare/quality-order.npy')
    with (BASE/'final/bundle.pkl').open('rb') as f:b=pickle.load(f)
    with (BASE/'cluster'/f"{b['policy']['hierarchy']}-hierarchy.pkl").open('rb') as f:h=pickle.load(f)
    assert b['policy']['id']=='v1-fixed16:group:mean:B50:Q25'
    reps=np.asarray(b['representatives']['mean'],float);groups=h['groups'];vectors={}
    eligible=[]
    for c in sorted(contexts,key=lambda c:c['uid']):
        if c['uid'] not in roles['verification'] or c['cap']!=10:continue
        value,info=vector(x,c)
        if info['state']=='VALID':eligible.append(c);vectors[c['uid']]=(value,info)
    selected=eligible[:20];uids=[c['uid'] for c in selected];assert uids==report['uids'] and len(uids)==20
    lookup={(r['uid'],r['variant']):r for r in rows};assert len(lookup)==len(rows)==80
    legal_counts={};max_delta=0.;expected_rows=[];seeds={}
    for j,c in enumerate(selected):
        uid=c['uid'];seen=set(c['viewed']);counts=Counter(int(groups[i]) for i in seen)
        allowed={g for g in range(h['n_groups']) if counts[g]<=2 and counts[g]/max(1,len(seen))<=.2}
        pools={}
        for i in q:
            g=int(groups[i])
            if g in allowed and int(i) not in seen and len(pools.get(g,[]))<25:pools.setdefault(g,[]).append(int(i))
        legal=sorted(pools);legal_counts[uid]=len(legal)
        perm=np.random.default_rng(913+uid).permutation(len(c['stars'])).tolist();seeds[uid]=913+uid
        stars=[c['stars'][i] for i in perm];shuffled,shuffleinfo=vector(x,c,stars)
        assert shuffleinfo['anchor']==vectors[uid][1]['anchor']
        donor=uids[(j+1)%20]
        variants={'original':vectors[uid],'cyclic_donor':vectors[donor],'ratings_permutation':(shuffled,shuffleinfo),'minus_direction':(-vectors[uid][0],vectors[uid][1])}
        ranks={k:sorted(legal,key=lambda g:(-float(np.dot(reps[g],v[0])),g))[:5] if v[1]['state']=='VALID' else [] for k,v in variants.items()}
        for variant,(direction,info) in variants.items():
            row=lookup[uid,variant];assert row['recipient_legal_groups']==legal
            assert row['recipient_group_viewed_counts']==[counts[g] for g in range(h['n_groups'])]
            assert row['recipient_prefix_sizes']=={str(g):len(p) for g,p in pools.items()}
            assert row['mapped_stars']==c['stars']
            assert row['donor_uid']==(donor if variant=='cyclic_donor' else None)
            assert row['counterfactual']==(variant!='original')
            if variant=='ratings_permutation':assert row['ratings_permutation']==perm and row['permuted_stars']==stars
            for key in ['state','mapped_inputs','supported_inputs']:assert row['profile'][key]==info[key]
            for key in ['anchor','raw_norm']:
                delta=abs(row['profile'][key]-info[key]);max_delta=max(max_delta,delta);assert delta<1e-12
            delta=float(np.linalg.norm(direction-vectors[uid][0]));assert abs(delta-row['direction_l2_from_original'])<1e-12
            assert row['top5']==ranks[variant] and row['top1']==(ranks[variant][0] if ranks[variant] else None)
            assert row['uniform_mapped_stars']==(len(set(c['stars']))<=1)
            assert row['permutation_identity']==(perm==list(range(len(perm))))
            assert row['permutation_stars_unchanged']==(stars==c['stars'])
            state='EMPTY_RECIPIENT_EU' if not legal else ('NO_VALID_VARIANT_VECTOR' if info['state']!='VALID' else ('NO_COMPARABLE_RANK' if not ranks['original'] or not ranks[variant] else 'COMPARABLE'))
            assert row['comparison_state']==state
            if state=='COMPARABLE':
                assert row['top1_changed']==(ranks[variant][0]!=ranks['original'][0])
                assert row['top5_order_changed']==(ranks[variant]!=ranks['original'])
                assert row['top5_overlap']==len(set(ranks[variant])&set(ranks['original']))/len(ranks['original'])
            else:assert all(row[k] is None for k in ['top1_changed','top5_order_changed','top5_overlap'])
    for s in report['summary']:
        rr=[r for r in rows if r['variant']==s['variant']];cc=[r for r in rr if r['comparison_state']=='COMPARABLE']
        assert s['users']==len(rr) and s['comparable_users']==len(cc)
        assert s['comparison_states']==dict(Counter(r['comparison_state'] for r in rr))
        assert s['profile_states']==dict(Counter(r['profile']['state'] for r in rr))
        for prefix in ['top1','top5_order']:
            assert s[prefix+'_changed']==sum(r[prefix+'_changed'] for r in cc)
            assert s[prefix+'_unchanged']==sum(not r[prefix+'_changed'] for r in cc)
        assert abs(s['mean_top5_overlap']-sum(r['top5_overlap'] for r in cc)/len(cc))<1e-12
        assert s['direction_unchanged']==sum(r['direction_l2_from_original']<=1e-12 for r in rr)
        assert s['uniform_mapped_stars_users']==sum(r['uniform_mapped_stars'] for r in rr)
        for field,source in [('identity_permutation_users','permutation_identity'),('unchanged_stars_permutation_users','permutation_stars_unchanged')]:
            assert s[field]==(sum(r[source] for r in rr) if s['variant']=='ratings_permutation' else None)
        assert s['top1_group_counts']==dict(Counter(str(r['top1']) for r in rr if r['top1'] is not None))
        assert s['top5_group_inclusion_counts']==dict(Counter(str(g) for r in rr for g in r['top5']))
    assert not any(name.startswith(('dv2_predictor','combination340_models','final344_adapter')) for name in sys.modules)
    assert {n:pin(OUT/n) for n in seal['files']}==seal['files']
    result={'status':'PASS','review_kind':'Same-author separately implemented numerical recomputation; independent root code/synthetic prior review is in execution-review ledger',
      'code':pin(Path(__file__)),'inputs':{n:pin(OUT/n) for n in ['report.json','records.json','seal.json']},'source_seals':report['source_seals'],
      'checked_users':20,'checked_variant_records':80,'first20_eligible_uids_match':True,'seeds':seeds,'max_profile_scalar_delta':max_delta,
      'legal_group_count_histogram':dict(Counter(legal_counts.values())),
      'minus_direction_top1_unchanged_cases':[{'uid':r['uid'],'legal_group_count':legal_counts[r['uid']],'top5':r['top5']} for r in rows if r['variant']=='minus_direction' and r['top1_changed'] is False],
      'predictor_calls':0,'future_numeric_rating_arrays_redacted_before_parse':n,'future_numeric_rating_values_parsed':False,
      'limits':['Control diagnostic measures representative-rank input sensitivity, not preference accuracy or satisfaction.','Small recipient legal sets can make top5 overlap high even when directions change.','Unused future target IDs may remain parsed; numeric future ratings were redacted before JSON parsing.']}
    target=OWN/'docs/recommendation/experiments/fixed-k8-discovery-v2/profile-controls-result-review.json';assert not target.exists()
    target.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'result':result,'ledger':pin(target)},indent=2))
if __name__=='__main__':
    with threadpool_limits(limits=2):run()
