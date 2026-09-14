"""Train-only temporal and numerical preflight, including tied timestamps."""
import os
for key in ['OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS']:
    os.environ[key] = '4'
import json
import time
import numpy as np
import pandas as pd
from foundation340_common import *
from foundation340_features import Features, Histories, RATING_COLUMNS

def main():
    start=time.monotonic()
    meta=pd.read_parquet(ROOT/'outputs/recommendation-evidence/rec-ev-045/metadata.parquet')
    cats=pd.read_parquet(OLD/'catalog.parquet'); ids=cats.movie_id.to_numpy()
    ratings=pd.read_parquet(OLD/'ratings.parquet'); episodes=pd.read_parquet(OLD/'episodes.parquet')
    hist=Histories(ratings,episodes,ids); features=Features(meta,cats)
    rng=np.random.default_rng(340)
    selected=set(rng.choice(len(ratings),size=512,replace=False).tolist())
    # At least one example for each actual h and both history definitions.
    for boundary in [hist.old_boundary,hist.strict_boundary]:
        h=np.minimum(hist.cap,boundary-hist.user_start)
        for length in range(31):
            candidates=np.flatnonzero(h==length)
            selected.update(candidates[:3].tolist())
    rows=np.array(sorted(selected)); maximum=0.; preserved=True
    for strict in [False,True]:
        oi,stars,mask,ei=hist.take(rows,strict)
        actual=features.batch(oi,stars,mask,ei)
        changed=actual.copy()
        changed[:,RATING_COLUMNS]=features.crowd(oi,stars,mask,ei,True)[:,np.array(RATING_COLUMNS)-200]
        for j,row in enumerate(rows):
            use=mask[j]; ref=features.reference(oi[j,use],stars[j,use],[ei[j]])[0]
            ref_r=features.reference(oi[j,use],stars[j,use],[ei[j]],True)[0]
            maximum=max(maximum,float(abs(ref-actual[j]).max()),float(abs(ref_r-changed[j]).max()))
            require(np.allclose(ref,actual[j],rtol=0,atol=2e-6) and np.allclose(ref_r,changed[j],rtol=0,atol=2e-6),'reference batch feature parity')
            legal=np.flatnonzero((hist.uid==hist.uid[row]) & (hist.ts < (hist.ts[row] if strict else int(episodes.iloc[np.searchsorted(np.cumsum(episodes.targets),row,side='right')].origin))))
            expected=legal[np.lexsort((hist.movie[legal],-hist.ts[legal]))][:hist.cap[row]]
            require(np.array_equal(oi[j,use],hist.ix[expected]),'independent exact temporal order')
        unchanged=np.setdiff1d(np.arange(230),RATING_COLUMNS)
        preserved &= np.array_equal(actual[:,unchanged],changed[:,unchanged])
    require(preserved,'only six raw crowd rating paths change')
    # Same-timestamp movies cannot serve as inputs for each other.
    toy=pd.DataFrame({'uid':[1]*5,'movie_id':ids[:5],'timestamp':[1,1,2,2,3],'rating':[1,2,3,4,5]})
    eps=pd.DataFrame({'uid':[1],'cap':[30],'pre_count':[0],'targets':[5]})
    th=Histories(toy,eps,ids); o,r,m,e=th.take(np.arange(5))
    require(m.sum(1).tolist()==[0,0,2,2,4],'tied group strict boundary')
    require(o[4,m[4]].tolist()==[2,3,0,1],'descending timestamp ascending movie tie-break')
    probe=np.arange(100000,104096); t=time.monotonic(); features.batch(*hist.take(probe)); batch_seconds=time.monotonic()-t
    report={'status':'PASS','real_reference_rows_per_history_rule':len(rows),'feature_max_abs_error':maximum,
            'unchanged_columns':224,'synthetic_tied_timestamps':'PASS','batch4096_seconds':batch_seconds,
            'seconds':time.monotonic()-start,'target_labels_read':0,'prior_mean':features.prior_mean,'prior_mass':features.prior_mass}
    OUT.mkdir(parents=True,exist_ok=True); write_json(OUT/'test-report.json',report); print(json.dumps(report),flush=True)

if __name__=='__main__':main()
