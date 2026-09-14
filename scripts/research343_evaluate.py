"""Separate-user star calibration, fixed common-cohort multi-metric comparison."""
import argparse
import time
import numpy as np
import pandas as pd
from research343_common import *

MODELS=['FM_B','FM_R','FM_RH','GBT_B','ALS','FM_ALS25','GBT_R','LGBM_REG_R','LGBM_RANK_R']
GROUPS=['ALL','W_DIRECT','C','NATURAL_ZERO','SUPPORT_0','SUPPORT_1_9','SUPPORT_10_49','SUPPORT_50_PLUS']
CAPS=[0,1,5,10,30]

def label_guard():
    p=pin(OLD/'labels.parquet')
    require(p['sha256']=='e3bf301a6e2ea7885b59bcab7fe83c2d3ad84f93f2f1bbb2d54d943b9a658db8','opened development label identity')
    return p

def eval_fingerprint():
    paths=['research343_evaluate.py','research343_catalog.py','research343_lgb_models.py','combination340_models.py',
           'foundation340_features.py','cold_item_features.py','text339_relations.py','rec047_features.py','rec046_common.py']
    result=fingerprint('evaluation')
    result.update({str((ROOT/'scripts'/n).relative_to(ROOT).as_posix()):pin(ROOT/'scripts'/n) for n in paths})
    result['docs/recommendation/experiments/research343/IMPLEMENTATION.md']=pin(DOC/'IMPLEMENTATION.md')
    return result

def gate():
    r=read(DOC/'evaluation-review.json');require(r['status']=='PASS' and r['fingerprint']==eval_fingerprint(),'evaluation exact code review')
    lock()
    label_guard()
    for name in ['gbt-fit-seal.json','LGBM_REG_R-fit-seal.json','LGBM_RANK_R-fit-seal.json']:verify(name)
    return eval_fingerprint()

def load():
    cats=pd.read_parquet(OLD/'catalog.parquet');contexts=read(OLD/'contexts.json')
    label_guard();labels=pd.read_parquet(OLD/'labels.parquet').set_index(['uid','movie_id']).rating
    f=np.load(FOUND/'predictions.npz'); ff={n:v for n,v in zip(f['names'],f['predictions'].T)}
    c=np.load(COMBO/'predictions.npz'); cc={n:v for n,v in zip(c['names'],c['predictions'].T)}
    values={'FM_B':ff['B'],'FM_R':ff['R'],'FM_RH':ff['RH'],'GBT_B':cc['GBT'],'ALS':cc['ACTUAL_ALS'],'FM_ALS25':cc['BLEND_0.25']}
    values.update({n:np.load(OUT/n/'predictions.npy') for n in MODELS[-3:]})
    roles=pd.read_csv(OUT/'roles.csv').set_index('uid').role.to_dict()
    return cats,contexts,labels,values,c['actual_direct'],roles

def truth(c,cats,labels):
    ei=np.asarray(c['ei'],int); ids=cats.movie_id.to_numpy()[ei]
    y=labels.reindex(pd.MultiIndex.from_arrays([np.full(len(ids),c['uid']),ids])).to_numpy(float)
    require(np.isfinite(y).all() and np.all((y*2)%1==0) and np.all((y>=.5)&(y<=5)),'actual half-stars')
    return ei,ids,y

def affine(x,y,w):
    w=w/w.sum();mx=float(w@x);my=float(w@y);var=float(w@(x-mx)**2)
    b=0.0 if var<=1e-12 else max(0.0,float(w@((x-mx)*(y-my)))/var)
    return {'a':my-b*mx,'b':b,'variance':var,'state':'CONSTANT' if b==0 else 'AFFINE'}

def calibrate():
    started=gate();require(not (OUT/'calibration-seal.json').exists(),'preserve calibration')
    cats,contexts,labels,values,direct,roles=load();rows=[];t=time.monotonic()
    for cap in CAPS:
        contexts_cap=[c for c in contexts if c['cap']==cap and roles[c['uid']]=='calibration']
        for model in MODELS:
            xs=[];ys=[];ws=[];uids=[]
            for c in contexts_cap:
                _,_,y=truth(c,cats,labels);x=values[model][c['start']:c['stop']];valid=np.isfinite(x)
                if not valid.any():continue
                xs.append(x[valid]);ys.append(y[valid]);ws.append(np.full(valid.sum(),1/valid.sum()));uids.append(c['uid'])
            n=sum(map(len,xs));row={'cap':cap,'model':model,'users':len(uids),'rows':n,'uids':uids}
            if len(uids)<20 or n<40:row.update(a=None,b=None,variance=None,state='INSUFFICIENT')
            else:row.update(affine(np.concatenate(xs),np.concatenate(ys),np.concatenate(ws)))
            rows.append(row)
    write_json(OUT/'calibration.json',{'fits':rows,'seconds':time.monotonic()-t,'role_file':pin(OUT/'roles.csv'),
                                    'comparison_labels_used':False,'label_file':pin(OLD/'labels.parquet')})
    require(started==gate(),'calibration code/input drift')
    seal('calibration-seal.json',['calibration.json'],execution=started,label_file=pin(OLD/'labels.parquet'))
    print('CALIBRATION_SEALED',flush=True)

def quality(y,p,ids,end,start=0):
    out={'stars':np.nan,'low':np.nan,'good':np.nan,'ndcg':np.nan,'both_low':np.nan,'returned':min(max(len(y)-start,0),end-start)}
    if len(y)<end:return out
    order=np.lexsort((ids,-p));shown=y[order[start:end]]
    out.update(stars=float(shown.mean()),low=float((shown<=2).mean()),good=float((shown>=4).mean()))
    if end-start==2:out['both_low']=float((shown<=2).all())
    if start==0:
        gain=(y-.5)/4.5;discount=1/np.log2(np.arange(end)+2);ideal=float(np.sort(gain)[::-1][:end]@discount)
        if ideal>0:out['ndcg']=float(gain[order[:end]]@discount/ideal)
    return out

def evaluate():
    started=gate();verify('calibration-seal.json');require(not (OUT/'user-metrics.parquet').exists(),'preserve evaluation')
    cats,contexts,labels,values,direct,roles=load();cal={(r['cap'],r['model']):r for r in read(OUT/'calibration.json')['fits']}
    users=[];pages=[];errors=[];profile=[];t=time.monotonic()
    for c in contexts:
        if roles[c['uid']]!='comparison':continue
        ei,ids,y=truth(c,cats,labels);sl=slice(c['start'],c['stop']);support=cats.train_count.to_numpy()[ei];blocked=cats.blocked.to_numpy()[ei]
        masks={'ALL':np.ones(len(y),bool),'W_DIRECT':direct[sl],'C':blocked,'NATURAL_ZERO':~blocked&(support==0),
               'SUPPORT_0':support==0,'SUPPORT_1_9':(support>=1)&(support<10),'SUPPORT_10_49':(support>=10)&(support<50),'SUPPORT_50_PLUS':support>=50}
        profile.append({k:c[k] for k in ['uid','cap','h','activity','als_supported_inputs']}|{'targets':len(y)})
        for model in MODELS:
            p=values[model][sl];finite=np.isfinite(p);cr=cal[(c['cap'],model)]
            calibrated=np.full(len(p),np.nan) if cr['a'] is None else cr['a']+cr['b']*p
            native=np.clip(p,.5,5) if model!='LGBM_RANK_R' else np.full(len(p),np.nan)
            raw=p if model!='LGBM_RANK_R' else np.full(len(p),np.nan)
            calibrated_clip=np.clip(calibrated,.5,5)
            for group,mask in masks.items():
                m=mask&finite;n=int(m.sum())
                row={'uid':c['uid'],'cap':c['cap'],'h':c['h'],'model':model,'group':group,'targets':int(mask.sum()),'predicted':n,'movies':n}
                for kind,z in [('native',native),('raw',raw),('calibrated',calibrated_clip),('calibrated_raw',calibrated)]:
                    e=z[m]-y[m];row[kind+'_mse']=float(np.mean(e*e)) if n else np.nan;row[kind+'_mae']=float(np.mean(abs(e))) if n else np.nan;row[kind+'_bias']=float(np.mean(e)) if n else np.nan
                row['outside_scale']=float(((p[m]<.5)|(p[m]>5)).mean()) if n and model!='LGBM_RANK_R' else np.nan
                row['calibrated_outside_scale']=float(((calibrated[m]<.5)|(calibrated[m]>5)).mean()) if n and cr['a'] is not None else np.nan
                # ALS has no full-pool ranking; direct comparisons use fixed W_DIRECT.
                rankok=not (model=='ALS' and group!='W_DIRECT')
                for end in [1,2,4,6]:
                    q=quality(y[m],p[m],ids[m],end) if rankok else {k:np.nan for k in ['stars','low','good','ndcg','both_low','returned']}
                    for k in ['stars','low','good','ndcg']:row[k+str(end)]=q[k]
                users.append(row)
                if rankok:
                    for end in [2,4,6]:pages.append({'uid':c['uid'],'cap':c['cap'],'h':c['h'],'model':model,'group':group,'end':end,'j':n,**quality(y[m],p[m],ids[m],end,end-2)})
            errors.append(pd.DataFrame({'uid':c['uid'],'cap':c['cap'],'h':c['h'],'model':model,'movie_id':ids,'native_se':(native-y)**2,
                                        'native_ae':abs(native-y),'calibrated_se':(calibrated_clip-y)**2,'calibrated_ae':abs(calibrated_clip-y),'direct':direct[sl],
                                        'blocked':blocked,'support':support}))
    frame=pd.DataFrame(users);page=pd.DataFrame(pages);err=pd.concat(errors,ignore_index=True)
    frame.to_parquet(OUT/'user-metrics.parquet',index=False);page.to_parquet(OUT/'page-metrics.parquet',index=False);err.to_parquet(OUT/'rating-errors.parquet',index=False)
    pd.DataFrame(profile).to_csv(OUT/'comparison-profiles.csv',index=False)
    summary=[]
    metriccols=[k for k in frame if k not in ['uid','cap','h','model','group']]
    for hgroup in ['ALL','H_POSITIVE','H_ZERO']:
        a=frame if hgroup=='ALL' else frame[frame.h.gt(0) if hgroup=='H_POSITIVE' else frame.h.eq(0)]
        for cohort in ['natural','common_j6']:
            b=a if cohort=='natural' else a[a.predicted.ge(6)]
            for keys,v in b.groupby(['cap','model','group']):
                for metric in metriccols:
                    z=v[metric].dropna();summary.append(dict(zip(['cap','model','group'],keys))|{'h_group':hgroup,'cohort':cohort,'metric':metric,'users':len(v),'valid':len(z),'mean':float(z.mean()) if len(z) else np.nan})
                for metric in ['native_mae','calibrated_mae']:
                    z=v[metric].dropna();summary.append(dict(zip(['cap','model','group'],keys))|{'h_group':hgroup,'cohort':cohort,'metric':'p95_user_'+metric,'users':len(v),'valid':len(z),'mean':float(z.quantile(.95)) if len(z) else np.nan})
    pd.DataFrame(summary).to_csv(OUT/'summary.csv',index=False)
    movie=[]
    for hgroup in ['ALL','H_POSITIVE','H_ZERO']:
        a=err if hgroup=='ALL' else err[err.h.gt(0) if hgroup=='H_POSITIVE' else err.h.eq(0)]
        for keys,v in a.groupby(['cap','model']):
            for group,mask in [('ALL',np.ones(len(v),bool)),('W_DIRECT',v.direct),('C',v.blocked),('NATURAL_ZERO',~v.blocked&v.support.eq(0))]:
                z=v[mask];m=z.groupby('movie_id')[['native_se','native_ae','calibrated_se','calibrated_ae']].mean()
                for metric in m:
                    movie.append(dict(zip(['cap','model'],keys))|{'h_group':hgroup,'group':group,'metric':metric,'movies':int(m[metric].count()),'users':z.loc[z[metric].notna(),'uid'].nunique(),
                                 'rows':int(z[metric].count()),'movie_macro':m[metric].mean(),'row_micro':z[metric].mean()})
    pd.DataFrame(movie).to_csv(OUT/'movie-metrics.csv',index=False)
    cfg=read(DOC/'config.json');contrasts=[]
    for after,before,mse in [('GBT_R','GBT_B','native_mse'),('LGBM_RANK_R','LGBM_REG_R','calibrated_mse')]:
        for metric in [mse,'ndcg2']:
            a=frame[(frame.cap==10)&frame.h.gt(0)&frame.group.eq('ALL')].pivot(index='uid',columns='model',values=metric)[[before,after]].dropna()
            delta=(a[after]-a[before]).to_numpy();rng=np.random.default_rng(cfg['bootstrap_seed'])
            boot=delta[rng.integers(len(delta),size=(cfg['bootstrap_samples'],len(delta)))].mean(1);alpha=(1-cfg['primary_ci'])/2
            contrasts.append({'before':before,'after':after,'metric':metric,'users':len(a),'before_mean':a[before].mean(),'after_mean':a[after].mean(),'delta':delta.mean(),
                              'ci_low':float(np.quantile(boot,alpha)),'ci_high':float(np.quantile(boot,1-alpha)),'confidence':cfg['primary_ci']})
    pd.DataFrame(contrasts).to_csv(OUT/'primary-contrasts.csv',index=False)
    require(started==gate(),'evaluation drift');verify('calibration-seal.json')
    files=['user-metrics.parquet','page-metrics.parquet','rating-errors.parquet','comparison-profiles.csv','summary.csv','movie-metrics.csv','primary-contrasts.csv']
    seal('evaluation-seal.json',files,execution=started,calibration_seal=pin(OUT/'calibration-seal.json'),seconds=time.monotonic()-t)
    print('EVALUATION_COMPLETE',round(time.monotonic()-t,1),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['calibrate','evaluate']);a=p.parse_args()
    calibrate() if a.action=='calibrate' else evaluate()
