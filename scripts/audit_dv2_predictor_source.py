"""Preserved independent reviewer's successful source/formula/time audit.

Post-execution labels are intentionally read. This is the successful payload
from /root/asset_audit/predictor_source_pins, preserved without another model
execution. It writes no files and does not alter selection criteria.
"""
from pathlib import Path
import sys,json,hashlib,math
import numpy as np,pandas as pd
from threadpoolctl import threadpool_limits
R=Path(r'C:/higher/projects/FEELM-standalone');W=R/'.codex-tmp/discovery-v2-predictor-20260913';ROOT=R/'.codex-tmp/fixed-k8-discovery-v2-20260913';O=ROOT/'outputs/fixed-k8-discovery-v2/feelm-discovery-v2-r2';B=R/'outputs/recommendation-evidence';DOC=ROOT/'docs/recommendation/experiments/fixed-k8-discovery-v2'
sys.path.insert(0,str(W/'scripts'));import dv2_predictor_parity as independent;import dv2_predictor as d

def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def pin(p):
 h=hashlib.sha256()
 with p.open('rb') as stream:
  while x:=stream.read(8*1024*1024):h.update(x)
 return {'bytes':p.stat().st_size,'sha256':h.hexdigest()}
initial={}
for stage in ['prepare','cluster','predictor']:
 sealpath=O/(stage+'-seal.json');seal=read(sealpath)
 actual={str(p.relative_to(O)):pin(p) for p in (O/stage).rglob('*') if p.is_file()};assert actual==seal['files'],stage
 initial[stage]=pin(sealpath)
review=read(DOC/'predictor-execution-review.json');assert review['status']=='PASS'
for name,expected in review['fingerprint']['files'].items():assert pin(Path(name))==expected,name
parityreview=read(DOC/'predictor-parity-review.json');assert parityreview['status']=='PASS'
for name,expected in parityreview['files'].items():assert pin(Path(name))==expected,name
assert pin(W/'scripts/dv2_predictor_parity.py')['sha256']=='d16902be0ec99d7ab1d52270b55f6b9a3a2b7ad8d3dc4c4124e152869ade018d'
assets=d.load_assets(R);decision=read(O/'predictor/decision.json');assert assets.provenance==decision['model_sources']
config=read(DOC/'config.json');origin=config['history_origin'];assert origin==1672531200
roles=read(O/'prepare/roles.json');source_roles_path=B/'final344/roles.csv';lock=read(B/'final344/input-lock.json')
assert pin(source_roles_path)['sha256']=='466b7cede9cb2d67f2bd7fca4fcdada770943bdd9d5924c35ccfdbe6a4cf2cc9'
assert pin(source_roles_path)==lock['files'][source_roles_path.relative_to(R).as_posix()]
srole=pd.read_csv(source_roles_path);selection=set(srole.loc[srole.role.eq('calibration'),'uid']);check=set(srole.loc[srole.role.eq('comparison'),'uid'])
assert set(roles['validation'])==selection and set(roles['verification'])==check and len(selection)==90 and len(check)==180 and not selection&check
frame=pd.read_parquet(O/'prepare/catalog.parquet');contexts=read(O/'prepare/contexts.json');cm={(c['uid'],c['cap']):c for c in contexts};assert len(cm)==len(contexts)==1350
old=read(B/'text339/contexts.json');oldcm={(c['uid'],c['cap']):c for c in old};catalog=pd.read_parquet(B/'text339/catalog.parquet',columns=['movie_id','train_count']);mids=catalog.movie_id.to_numpy(int)
service_map={int(r.movielens_movie_id):i for i,r in enumerate(frame.itertuples()) if r.mapping_status=='MATCHED'}
train_path=B/'text339/ratings.parquet';assert pin(train_path)==lock['files'][train_path.relative_to(R).as_posix()]
train=pd.read_parquet(train_path,columns=['uid','timestamp']);assert int(train.timestamp.max())<origin and not set(train.uid)&set(srole.uid)
label_path=B/'text339/labels.parquet';assert pin(label_path)['sha256']=='e3bf301a6e2ea7885b59bcab7fe83c2d3ad84f93f2f1bbb2d54d943b9a658db8'
labels=pd.read_parquet(label_path);truth={(int(r.uid),int(r.movie_id)):float(r.rating) for r in labels.itertuples()};assert len(truth)==len(labels)
context_target_comparisons=context_history_comparisons=0
for c in contexts:
 s=oldcm[c['uid'],c['cap']]
 expected_history=[service_map[int(mids[i])] for i in s['oi'] if int(mids[i]) in service_map]
 expected_stars=[float(r) for i,r in zip(s['oi'],s['stars']) if int(mids[i]) in service_map]
 expected_target=[service_map[int(mids[i])] for i in s['ei'] if int(mids[i]) in service_map]
 expected_truth=[truth[c['uid'],int(mids[i])] for i in s['ei'] if int(mids[i]) in service_map]
 assert c['history']==expected_history and c['stars']==expected_stars
 assert c['target']==expected_target and c['ratings']==expected_truth
 assert c['original_stars']==s['stars'] and c['original_input_timestamps']==s['input_timestamps']
 assert len(s['oi'])<=c['cap'] and len(s['oi'])==len(s['stars'])==len(s['input_timestamps'])
 assert max(s['input_timestamps'],default=0)<origin
 assert len(s['ei'])==len(s['target_timestamps']) and all(origin<=t<origin+180*86400 for t in s['target_timestamps'])
 assert not set(c['history'])&set(c['target']) and not set(c['viewed'])&set(c['target']) and set(c['history'])<=set(c['viewed'])
 old_viewed={service_map[int(mids[i])] for i in s['viewed'] if int(mids[i]) in service_map};assert old_viewed<=set(c['viewed'])
 context_history_comparisons+=len(expected_history);context_target_comparisons+=len(expected_target)
errors=pd.read_parquet(O/'predictor/observed-errors.parquet');requests=pd.read_parquet(O/'predictor/same-candidate-requests.parquet');details=read(O/'predictor/candidate-details.json')
assert len(errors)==7316 and len(requests)==len(details)==248
assert set(errors.uid)==set(requests.uid)=={r['uid'] for r in details} and len(set(errors.uid))==62 and set(errors.uid)<=selection and not set(errors.uid)&check
assert requests.cap.eq(10).all() and all(r['cap']==10 for r in details)
assert not errors.duplicated(['uid','variant','index']).any() and not requests.duplicated(['uid','predictor']).any()
order=np.load(O/'prepare/quality-order.npy');eligible=set(map(int,order));detailmap={(r['uid'],r['predictor']):r for r in details}
for uid in sorted(set(errors.uid)):
 c=cm[uid,10];expected=[(i,r) for i,r in zip(c['target'],c['ratings']) if i in eligible]
 for variant in d.VARIANTS:
  sub=errors[errors.uid.eq(uid)&errors.variant.eq(variant)]
  assert sub['index'].tolist()==[i for i,r in expected] and sub.actual.tolist()==[r for i,r in expected]
  rec=detailmap[uid,variant];cand=rec['candidate_indices'];assert len(cand)==len(set(cand))<=100 and set(cand)<=eligible and not set(cand)&set(c['viewed'])
  assert rec['candidate_indices']==detailmap[uid,'original']['candidate_indices'] and rec['retrieval']['global_fill']==0
  assert np.isfinite(rec['candidate_predictions']).all()
  for e in sub.itertuples():assert e.service_movie_id==int(frame.service_movie_id.iloc[e.index])
selected=sorted(set(requests.uid),key=lambda u:hashlib.sha256(f'dv2-stage-formula-audit:{u}'.encode()).digest())[:3]
counts,vectors=independent.source_support(frame,catalog,pd.read_parquet(B/'combination340/ALS/item-factors'))
sourcecal=read(B/'hybrid345/calibration.json');cal={cap:{head:None for head in ['ALS','GBT120_s339']} for cap in d.CAPS}
for r in sourcecal['fits']:
 if r['model'] in ['ALS','GBT120_s339']:cal[r['cap']][r['model']]=None if r['a'] is None else (r['a'],r['b'])
native=independent.NativeScalarGBT(B/'final344/GBT120_s339/model/native');independent.reference_test.REFERENCE_ROOT=R
max_candidate=max_observed=max_squared=0.;candidate_rows=observed_rows=0;user_evidence=[]
with threadpool_limits(limits=2):
 for uid in selected:
  c=cm[uid,10];candidate=detailmap[uid,'original']['candidate_indices'];obs=errors[errors.uid.eq(uid)&errors.variant.eq('original')]
  observed=sorted(obs['index'],key=lambda i:hashlib.sha256(f'dv2-stage-observed-audit:{int(frame.service_movie_id.iloc[i])}'.encode()).digest())[:10]
  joint=list(dict.fromkeys(candidate+observed));pos={i:j for j,i in enumerate(joint)}
  x,raw_als,raw_gbt,cal_als,cal_gbt,available=independent.independent_heads(frame,c,np.array(joint,int),vectors,counts,native,cal)
  for variant in d.VARIANTS:
   w=available.astype(float)
   if variant.startswith('shrink'):w*=counts[joint]/(counts[joint]+int(variant[6:]))
   if variant=='min20':w*=counts[joint]>=20
   prediction=cal_gbt.copy();prediction[available]=w[available]*cal_als[available]+(1-w[available])*cal_gbt[available]
   rec=detailmap[uid,variant];take=np.array([pos[i] for i in candidate],int);want=prediction[take]
   err=float(abs(want-np.array(rec['candidate_predictions'])).max());max_candidate=max(max_candidate,err);assert err<=1e-8
   np.testing.assert_array_equal(w[take],rec['candidate_als_weights'])
   branch=np.where(w[take]==1,'ALS',np.where(w[take]>0,'ALS_GBT_SHRINK','GBT'));np.testing.assert_array_equal(branch,rec['candidate_branches'])
   req=requests[requests.uid.eq(uid)&requests.predictor.eq(variant)].iloc[0];ranked=np.lexsort((frame.service_movie_id.iloc[candidate].to_numpy(),-want))[:10]
   assert req.ranked.tolist()==np.array(candidate)[ranked].tolist()
   np.testing.assert_allclose(req.ranked_prediction,want[ranked],rtol=0,atol=1e-8)
   for e in errors[errors.uid.eq(uid)&errors.variant.eq(variant)&errors['index'].isin(observed)].itertuples():
    j=pos[e.index];clipped=float(np.clip(prediction[j],.5,5));er=abs(prediction[j]-e.prediction);max_observed=max(max_observed,er);assert er<=1e-8
    assert abs(clipped-e.clipped)<=1e-8 and e.train_count==counts[e.index] and e.als_available==available[j]
    sq=(clipped-e.actual)**2;max_squared=max(max_squared,abs(sq-e.squared_error));assert abs(sq-e.squared_error)<=1e-8 and abs(abs(clipped-e.actual)-e.absolute_error)<=1e-8
    observed_rows+=1
   candidate_rows+=len(candidate)
  user_evidence.append({'uid':int(uid),'candidate_indices':len(candidate),'observed_indices':len(observed),'joint_reference_head_rows':len(joint),'supported_history':int(np.isfinite(vectors[c['history']]).all(1).sum())})
for stage,expected in initial.items():assert pin(O/(stage+'-seal.json'))==expected
assert d.load_assets(R).provenance==decision['model_sources']
print(json.dumps({'status':'PASS','selected_audit_users':list(map(int,selected)),'user_evidence':user_evidence,'candidate_variant_rows_recomputed':candidate_rows,'observed_variant_rows_recomputed':observed_rows,'max_candidate_prediction_abs_error':max_candidate,'max_observed_prediction_abs_error':max_observed,'max_observed_squared_error_abs_error':max_squared,'all_stage_request_users_in_calibration':62,'check_users_used':0,'mapped_history_entries_verified':context_history_comparisons,'mapped_future_target_entries_verified':context_target_comparisons,'all_contexts_checked':len(contexts),'train_rows':len(train),'train_max_timestamp':int(train.timestamp.max()),'train_evaluation_uid_overlap':0,'same_candidates_all_four_variants_verified_requests':len(details),'all_candidate_eligibility_seen_exclusions_verified':True,'source_label_values_explicitly_authorized_and_read':True,'stage_seals':initial,'execution_review_pin':pin(DOC/'predictor-execution-review.json'),'source_formula_script_pin':pin(W/'scripts/dv2_predictor_parity.py'),'decision_pin':pin(O/'predictor/decision.json'),'observed_errors_pin':pin(O/'predictor/observed-errors.parquet'),'candidate_details_pin':pin(O/'predictor/candidate-details.json')},sort_keys=True))
