"""Apply locked policies to fixed observed and entire catalog predictions, without labels."""
import json
import time
import numpy as np
import pandas as pd
from rec046_common import require,pin,write_json
import combination340_common as combination
from combination340_catalog import verify_catalog
from policy341 import POLICIES,genre_vectors,familiarity,recommend

ROOT=combination.ROOT
DOC=ROOT/'docs/recommendation/experiments/policy341'
OUT=ROOT/'outputs/recommendation-evidence/policy341'
OLD=combination.OLD

def fingerprint():
    files=[ROOT/'scripts'/n for n in ['policy341.py','policy341_run.py','test_policy341.py']]+[DOC/'EXECUTION.md']
    return {p.relative_to(ROOT).as_posix():pin(p) for p in files}

def parents():
    combination.reviewed(); combination.verify('evaluation-seal.json'); verify_catalog()
    record=json.loads((combination.DOC/'result-review.json').read_text())
    require(record['status']=='PASS' and record['evaluation_seal']==pin(combination.OUT/'evaluation-seal.json'),'independent model-selection review')
    return record

def verify():
    parents(); record=json.loads((OUT/'prediction-seal.json').read_text())
    require(record['fingerprint']==fingerprint(),'policy execution identity')
    for name,expected in record['parents'].items(): require(pin(ROOT/name)==expected,'policy source identity')
    for name,expected in record['files'].items(): require(pin(OUT/name)==expected,'policy output identity')
    return record

def run():
    parents()
    review=json.loads((DOC/'execution-review.json').read_text())
    require(review['status']=='PASS' and review['fingerprint']==fingerprint(),'independent exact policy review')
    OUT.mkdir(parents=True,exist_ok=True)
    for name in ['rankings.parquet','blocks.parquet','timing.csv','prediction-seal.json']: require(not (OUT/name).exists(),'preserve policy output '+name)
    choice=json.loads((combination.OUT/'selection.json').read_text())['selected']
    contexts=json.loads((OLD/'contexts.json').read_text()); contexts=[c for c in contexts if c['cap']==10]
    cats=pd.read_parquet(OLD/'catalog.parquet'); ids=cats.movie_id.to_numpy()
    meta=pd.read_parquet(ROOT/combination.foundation.config()['metadata']); require(np.array_equal(meta.movie_id,ids),'same metadata IDs')
    vectors,present,genres=genre_vectors(meta)
    new=np.load(combination.OUT/'predictions.npz'); selected_observed=new['predictions'][:,new['names'].tolist().index(choice)]
    base=np.load(combination.foundation.OUT/'predictions.npz'); baseline_observed=base['predictions'][:,base['names'].tolist().index('B')]
    dates=pd.to_datetime(pd.read_parquet(OLD/'texts.parquet',columns=['release_date']).release_date,format='%Y-%m-%d',errors='coerce',utc=True)
    current=dates.notna().to_numpy() & (dates.astype('int64').to_numpy()//10**9<=combination.foundation.config()['catalog_snapshot_timestamp'])
    rows=[]; blocks=[]; costs=[]
    for c in contexts:
        cache=np.load(combination.OUT/'catalog-cache'/f"{c['uid']}.npz")
        allowed=current.copy(); allowed[np.asarray(c['viewed'],int)]=False
        require(np.array_equal(cache['ei'],np.flatnonzero(allowed)),'complete unchanged catalog candidates')
        pools={'OBSERVED':(np.asarray(c['ei'],int),{'B':baseline_observed[c['start']:c['stop']],'SELECTED':selected_observed[c['start']:c['stop']]}),
               'CATALOG':(cache['ei'],{'B':cache['B'],'SELECTED':cache[choice]})}
        for pool,(ei,models) in pools.items():
            require(not np.intersect1d(ei,c['viewed']).size,'viewed excluded')
            sim,has_input=familiarity(vectors,present,c['oi'],ei)
            for model,scores in models.items():
                for policy in POLICIES:
                    start=time.monotonic(); positions,roles,info=recommend(scores,ids[ei],sim,has_input,policy)
                    costs.append({'uid':c['uid'],'pool':pool,'model':model,'policy':policy,'candidates':len(ei),'seconds':time.monotonic()-start})
                    for rank,(pos,role) in enumerate(zip(positions,roles),1):
                        ix=int(ei[pos]); rows.append({'uid':c['uid'],'h':c['h'],'pool':pool,'model':model,'policy':policy,'j':len(ei),
                                                     'rank':rank,'movie_id':int(ids[ix]),'prediction':float(scores[pos]),'role':role,
                                                     'novelty':float(1-sim[pos]) if np.isfinite(sim[pos]) else np.nan,
                                                     'train_count':int(cats.train_count.iloc[ix])})
                    for info_row in info:
                        blocks.append({'uid':c['uid'],'h':c['h'],'pool':pool,'model':model,'policy':policy,'j':len(ei),
                                       **{k:v for k,v in info_row.items() if k not in ['baseline_third','chosen_third']},
                                       'baseline_third_id':int(ids[ei[info_row['baseline_third']]]),'chosen_third_id':int(ids[ei[info_row['chosen_third']]])})
    pd.DataFrame(rows).to_parquet(OUT/'rankings.parquet',index=False); pd.DataFrame(blocks).to_parquet(OUT/'blocks.parquet',index=False)
    pd.DataFrame(costs).to_csv(OUT/'timing.csv',index=False)
    paths=[combination.OUT/'selection.json',combination.OUT/'evaluation-seal.json',combination.OUT/'catalog-seal.json',combination.DOC/'result-review.json',
           combination.foundation.OUT/'fit-seal.json',OLD/'contexts.json',OLD/'catalog.parquet']
    write_json(OUT/'prediction-seal.json',{'fingerprint':fingerprint(),'parents':{p.relative_to(ROOT).as_posix():pin(p) for p in paths},
                                         'files':{name:pin(OUT/name) for name in ['rankings.parquet','blocks.parquet','timing.csv']},
                                         'selected':choice,'genres':genres,'future_labels_read':0})
    print('POLICIES_SCORED',choice,len(rows),flush=True)

if __name__=='__main__':run()
