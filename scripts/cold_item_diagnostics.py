"""Reproducible label-free aggregates from sealed prepared features."""
import json
import numpy as np
import pandas as pd
from cold_item_common import ROOT, OUT, verify_seal, fingerprint, pin, write_json, require

def save_csv(frame,path):
    content=frame.to_csv(index=False,lineterminator='\n')
    if path.exists(): require(path.read_text(encoding='utf-8')==content,'preserve existing aggregate')
    else: path.write_text(content,encoding='utf-8')

def main():
    verify_seal(OUT/'prepared-seal.json',OUT)
    episodes=pd.read_parquet(OUT/'episodes.parquet')
    a=episodes.groupby('k',as_index=False).agg(episodes=('targets','size'),targets=('targets','sum'),users=('uid','nunique'))
    a['target_share']=a.targets/a.targets.sum()
    save_csv(a,OUT/'training-composition.csv')
    info=json.loads((OUT/'feature-info.json').read_text());names=info['names']
    needed=['DIRECTOR_no_link','CAST_no_link']+['crowd_'+n+'_rating_covariance' for n in ['rating','votes','popularity']]
    cols=[f'x{names.index(n):03d}' for n in needed]
    x=pd.read_parquet(OUT/'score.parquet',columns=cols)
    contexts=json.loads((OUT/'contexts.json').read_text());rows=[]
    for k in [1,5,10,30]:
        cs=[c for c in contexts if c['k']==k];ix=np.concatenate([np.arange(c['start'],c['stop']) for c in cs])
        for name,col in zip(needed,cols):
            a=x[col].to_numpy()[ix]
            hit=(a>0) if name.endswith('no_link') else abs(a)>1e-12
            rows.append({'k':k,'feature':name,'rows':len(a),'rows_present':int(hit.sum()),'fraction':float(hit.mean()),
                         'meaning':'no_direct_link' if name.endswith('no_link') else 'nonzero_calculated_response_not_proven_effect'})
    save_csv(pd.DataFrame(rows),OUT/'feature-availability.csv')
    write_json(OUT/'diagnostic-seal.json',{'fingerprint':fingerprint(),'prepared_seal':pin(OUT/'prepared-seal.json'),
               'generator':pin(ROOT/'scripts/cold_item_diagnostics.py'),
               'files':{n:pin(OUT/n) for n in ['training-composition.csv','feature-availability.csv']},'target_labels_read':0})
    print('DIAGNOSTICS_REPRODUCED_AND_SEALED')

if __name__=='__main__':main()
