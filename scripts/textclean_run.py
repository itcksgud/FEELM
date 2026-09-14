"""One local text-only FM follow-up; old text339 artifacts stay immutable."""
from __future__ import annotations
import os
for key in ['OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS']:os.environ[key]='4'
import argparse
import json
import shutil
import subprocess
import time
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from textclean_source import ROOT,OLD,OUT,DOC,digest
from rec046_common import require,pin,write_json
import text339_common as reference
from text339_features import Features
from text339_prepare import Writer,load_embeddings as old_embeddings
from text339_run import predict
from rec047_common import window_start

def fingerprint():
    paths=[ROOT/'scripts'/n for n in ['textclean_source.py','textclean_run.py']]+[DOC/'PLAN.md',DOC/'config.json']
    return {p.relative_to(ROOT).as_posix():pin(p) for p in paths}

def guard():
    review=json.loads((DOC/'execution-review.json').read_text(encoding='utf-8'))
    require(review['status']=='PASS' and review['fingerprint']==fingerprint(),'exact independent code review')
    reference.reviewed()
    require(json.loads((DOC/'config.json').read_text())==reference.config(),'identical worker and feature configuration')
    for path,expected in reference.config()['model_sources'].items():
        require(pin(ROOT/path)==expected,'actual fixed encoder/tokenizer file '+path)

def verify(name,seen=None):
    seen=set() if seen is None else seen
    if name in seen:return json.loads((OUT/name).read_text(encoding='utf-8'))
    seen.add(name)
    r=json.loads((OUT/name).read_text(encoding='utf-8'))
    if 'fingerprint' in r:require(r['fingerprint']==fingerprint(),'current execution fingerprint '+name)
    for n,p in r['files'].items():require(pin(OUT/n)==p,'immutable '+n)
    for key,parent in [('clean_seal','clean-seal.json'),('embedding_seal','embedding-seal.json'),('prepared_seal','prepared-seal.json'),('fit_seal','fit-seal.json')]:
        if key in r:
            require(pin(OUT/parent)==r[key],'stage parent identity '+parent);verify(parent,seen)
    if 'old_embedding_seal' in r:
        require(pin(OLD/'embedding-seal.json')==r['old_embedding_seal'],'original embedding parent');old_embeddings()
    if 'old_prepared_seal' in r:
        require(pin(OLD/'prepared-seal.json')==r['old_prepared_seal'],'original prepared parent');reference.verify('prepared-seal.json')
    if name=='clean-seal.json':
        require(pin(OLD/'texts.parquet')==r['source'] and pin(DOC/'semantic-review.json')==r['review'],'actual text and semantic review')
        source=json.loads((OUT/'source-seal.json').read_text());require(pin(OUT/'source-seal.json')==r['source_seal'],'candidate parent')
        for n,p in source['files'].items():require(pin(OUT/n)==p,'actual candidate source '+n)
        semantic=json.loads((DOC/'semantic-review.json').read_text())
        for n,p in semantic['review_files'].items():require(pin(DOC/n)==p,'actual semantic reviewer artifact '+n)
    return r

def seal(name,files,**extra):
    require(not (OUT/name).exists(),'preserve '+name)
    write_json(OUT/name,{'fingerprint':fingerprint(),'files':{n:pin(OUT/n) for n in files},**extra})

def encode():
    guard();clean=verify('clean-seal.json')
    require(pin(OLD/'texts.parquet')==clean['source'],'old source pin')
    require(pin(DOC/'semantic-review.json')==clean['review'],'semantic review pin')
    require(not (OUT/'overview-embeddings.npy').exists(),'preserve embedding run')
    before=pd.read_parquet(OLD/'texts.parquet');after=pd.read_parquet(OUT/'texts.parquet')
    require(np.array_equal(before.movie_id,after.movie_id),'unchanged movie order')
    embeddings=old_embeddings();lookup={};new={}
    for key in ['overview','T2','T3']:
        for i,h in enumerate(before[key+'_sha256']):lookup.setdefault(h,(key,i))
    for key in ['overview','T3']:
        for txt,h in zip(after[key],after[key+'_sha256']):
            require(digest(txt)==h,'clean text hash')
            if txt and h not in lookup:new[h]=txt
    hashes=sorted(new);vectors=np.zeros((len(hashes),768),np.float32);counts=[];start=time.monotonic()
    if hashes:
        from text339_text import Encoder
        encoder=Encoder()
        for i in range(0,len(hashes),64):
            require(time.monotonic()-start<=reference.config()['embedding_timeout_seconds'],'fixed embedding time cap')
            vectors[i:i+64],stats=encoder.encode([new[h] for h in hashes[i:i+64]])
            counts.append(stats);print('ENCODE',i,len(hashes),flush=True)
        require(np.allclose(np.linalg.norm(vectors,axis=1),1,rtol=0,atol=5e-5),'nonempty new text normalized vectors')
    require(time.monotonic()-start<=reference.config()['embedding_timeout_seconds'],'embedding completed within cap')
    newix={h:i for i,h in enumerate(hashes)};summary={}
    for key in ['overview','T3']:
        value=np.array(embeddings[key],copy=True);changed=before[key+'_sha256'].ne(after[key+'_sha256']).to_numpy()
        for i in np.flatnonzero(changed):
            txt,h=after[key].iloc[i],after[key+'_sha256'].iloc[i]
            if not txt:value[i]=0
            elif h in lookup:k,j=lookup[h];value[i]=embeddings[k][j]
            else:value[i]=vectors[newix[h]]
        require(np.array_equal(value[~changed],embeddings[key][~changed]),'unchanged embeddings bitwise reused')
        require(np.isfinite(value).all() and value.shape==(85517,768),'finite embedding axis')
        np.save(OUT/(key+'-embeddings.npy'),value)
        summary[key]={'changed_rows':int(changed.sum()),'unchanged_rows':int((~changed).sum()),'empty_rows':int(after[key].eq('').sum())}
    np.save(OUT/'new-embeddings.npy',vectors);write_json(OUT/'encoding-order.json',hashes)
    write_json(OUT/'embedding-summary.json',{'new_unique_texts':len(hashes),'seconds':time.monotonic()-start,'counts':counts,'sources':summary,
                  'old_embedding_seal':pin(OLD/'embedding-seal.json'),'rating_values_read':0})
    seal('embedding-seal.json',['overview-embeddings.npy','T3-embeddings.npy','new-embeddings.npy','encoding-order.json','embedding-summary.json'],
         clean_seal=pin(OUT/'clean-seal.json'),old_embedding_seal=pin(OLD/'embedding-seal.json'))

def features():
    # Actual read paths are checked, including source T2 and the fixed original PCA.
    reference.verify('prepared-seal.json');checked=old_embeddings();verify('embedding-seal.json')
    meta=pd.read_parquet(ROOT/reference.config()['metadata'])
    embeddings={k:np.load(OUT/(k+'-embeddings.npy'),mmap_mode='r') if k!='T2' else checked[k] for k in ['overview','T2','T3']}
    pc=np.load(OLD/'projection.npz');projections={k:{n:pc[k+'_'+n] for n in ['mean','basis','scale']} for k in ['overview','wiki']}
    return meta,Features(meta,embeddings,projections)

class OriginalRows:
    """Streaming exact row identity comparisons, independent of Parquet batch borders."""
    def __init__(self,path):
        self.columns=['row_id','uid','label']+[f'x{i:03d}' for i in list(range(230))+list(range(338,446))]
        self.iterator=pq.ParquetFile(path).iter_batches(batch_size=8192,columns=self.columns);self.current=None;self.pos=0;self.offset=0
    def check(self,x,y,uid):
        n=len(y);done=0
        while done<n:
            if self.current is None or self.pos==len(self.current):self.current=next(self.iterator).to_pandas();self.pos=0
            count=min(n-done,len(self.current)-self.pos);a=self.current.iloc[self.pos:self.pos+count]
            require(np.array_equal(a.row_id,np.arange(self.offset,self.offset+count)) and (a.uid==uid).all(),'exact reference rows/users')
            require(np.array_equal(a.label,np.asarray(y)[done:done+count]),'exact same training labels')
            cols=list(range(230))+list(range(338,446))
            require(np.array_equal(a.iloc[:,3:].to_numpy(),x[done:done+count][:,cols]),'unchanged structured and T2 features bitwise')
            done+=count;self.pos+=count;self.offset+=count
    def close(self):
        require(self.current is None or self.pos==len(self.current),'reference batch fully consumed')
        require(next(self.iterator,None) is None,'reference no extra rows')

def prepare():
    guard();verify('embedding-seal.json');reference.verify('prepared-seal.json')
    require(not (OUT/'train.parquet').exists(),'preserve preparation')
    meta,fe=features();ids=meta.movie_id.to_numpy();texts=pd.read_parquet(OUT/'texts.parquet');oldtexts=pd.read_parquet(OLD/'texts.parquet')
    changed=(texts.overview_sha256.ne(oldtexts.overview_sha256)|texts.T3_sha256.ne(oldtexts.T3_sha256)).to_numpy()
    train=pd.read_parquet(OLD/'ratings.parquet').sort_values(['uid','timestamp','movie_id'])
    train['index']=np.searchsorted(ids,train.movie_id);train['window']=window_start(train.timestamp.to_numpy(),reference.config()['origin'])
    require(np.array_equal(ids[train['index']],train.movie_id),'same train movie identity')
    writer=Writer(OUT/'train.parquet');check=OriginalRows(OLD/'train.parquet');episodes=[];exposure={'train_target_rows':0,'train_input_slots':0,'train_episodes_with_changed_input':0}
    started=time.monotonic()
    for number,(uid,a) in enumerate(train.groupby('uid',sort=False)):
        ix,ts,r,wins=a['index'].to_numpy(),a.timestamp.to_numpy(),a.rating.to_numpy(),a.window.to_numpy()
        starts=np.r_[0,np.flatnonzero(np.diff(wins))+1];ends=np.r_[starts[1:],len(a)]
        for lo,hi in zip(starts,ends):
            w=int(wins[lo]);cap=reference.allocated_cap(int(uid),w);h=min(cap,int(lo));previous=np.lexsort((ids[ix[:lo]],-ts[:lo]))[:h]
            x=fe.transform(ix[previous],r[previous],ix[lo:hi]);check.check(x,r[lo:hi],int(uid));writer.append(x,r[lo:hi],int(uid))
            episodes.append((int(uid),w,cap,h,int(lo),int(hi-lo)))
            exposure['train_target_rows']+=int(changed[ix[lo:hi]].sum());exposure['train_input_slots']+=int(changed[ix[previous]].sum())
            exposure['train_episodes_with_changed_input']+=int(changed[ix[previous]].any())
        if number%1000==0:print('TRAIN_FEATURES',number,flush=True)
    writer.close();check.close();require(writer.offset==4997069,'fixed target count')
    episode=pd.DataFrame(episodes,columns=['uid','origin','cap','h','pre_count','targets'])
    require(episode.equals(pd.read_parquet(OLD/'episodes.parquet')),'exact reference episode contract')
    for name in ['contexts.json','projection.npz','catalog.parquet','feature-info.json','episodes.parquet']:
        shutil.copyfile(OLD/name,OUT/name)
    contexts=json.loads((OUT/'contexts.json').read_text());writer=Writer(OUT/'score.parquet');check=OriginalRows(OLD/'score.parquet');coverage=[]
    for c in contexts:
        x=fe.transform(c['oi'],c['stars'],c['ei']);y=np.zeros(len(c['ei']));check.check(x,y,c['uid']);writer.append(x,y,c['uid'])
        if c['cap']==10:
            ti=int(changed[c['ei']].sum());oi=int(changed[c['oi']].sum())
            coverage.append({'uid':c['uid'],'h':c['h'],'j':len(c['ei']),'changed_candidates':ti,'changed_inputs':oi,'direct_exposure':bool(ti or oi)})
    writer.close();check.close();require(writer.offset==93230,'same score row count')
    info=json.loads((OUT/'feature-info.json').read_text());info['indices']['CLEAN']=info['indices']['T3'];write_json(OUT/'feature-info.json',info)
    pd.DataFrame(coverage).to_csv(OUT/'text-exposure-users.csv',index=False)
    exposure.update({'changed_movies':int(changed.sum()),'eval_direct_exposure_users':sum(c['direct_exposure'] for c in coverage),
                     'eval_changed_candidate_rows':sum(c['changed_candidates'] for c in coverage),'eval_changed_input_slots':sum(c['changed_inputs'] for c in coverage),
                     'seconds':time.monotonic()-started,'same_reference_structured_features':True,'same_reference_rows_labels_episodes':True,
                     'new_future_rating_values_read':0})
    write_json(OUT/'preparation-summary.json',exposure)
    seal('prepared-seal.json',['train.parquet','score.parquet','contexts.json','projection.npz','catalog.parquet','feature-info.json','episodes.parquet','text-exposure-users.csv','preparation-summary.json'],
          embedding_seal=pin(OUT/'embedding-seal.json'),old_prepared_seal=pin(OLD/'prepared-seal.json'))
    print('PREPARED',json.dumps(exposure),flush=True)

def fit():
    guard();verify('prepared-seal.json')
    review=json.loads((DOC/'prepared-review.json').read_text());require(review['status']=='PASS' and review['prepared_seal']==pin(OUT/'prepared-seal.json'),'review actual prepared features')
    cfg=reference.config();require(subprocess.check_output(['docker','image','inspect',cfg['docker_image'],'--format','{{.Id}}'],text=True).strip()==cfg['image_id'],'pinned image')
    folder=OUT/'models/CLEAN';require(not folder.exists(),'preserve model run');logs=OUT/'logs';logs.mkdir(exist_ok=True)
    name='textclean-fm';cmd=['docker','run','--rm','--name',name,'--network','none','--hostname','text339','--add-host','text339:127.0.0.1',
          '-e','SPARK_LOCAL_IP=127.0.0.1','--cpus','4','--memory','12g','--memory-swap','12g',
          '--mount',f'type=bind,source={ROOT/"scripts"},target=/scripts,readonly','--mount',f'type=bind,source={OUT},target=/data',
          '--mount',f'type=bind,source={DOC},target=/config,readonly',cfg['docker_image'],'/opt/spark/bin/spark-submit','--master','local[4]',
          '--driver-memory','8g','--conf','spark.sql.shuffle.partitions=8','--conf','spark.ui.enabled=false','/scripts/text339_worker.py','CLEAN']
    write_json(OUT/'run-progress.json',{'stage':'FIT_RUNNING','container':name,'started_unix':time.time(),'prepared_seal':pin(OUT/'prepared-seal.json')})
    with (logs/'fit.log').open('w',encoding='utf-8') as f:
        proc=subprocess.Popen(cmd,stdout=f,stderr=subprocess.STDOUT)
        try:require(proc.wait(timeout=cfg['fit_timeout_seconds'])==0,'FM process failed; preserve logs')
        except BaseException:
            subprocess.run(['docker','stop','--time','2',name],capture_output=True,timeout=20)
            if proc.poll() is None:proc.kill()
            raise
    pred=pd.read_parquet(folder/'predictions').sort_values('row_id');require(np.array_equal(pred.row_id,np.arange(93230)) and np.isfinite(pred.prediction).all(),'aligned finite predictions')
    np.savez_compressed(folder/'aligned.npz',prediction=pred.prediction.to_numpy())
    seal('fit-seal.json',[p.relative_to(OUT).as_posix() for p in folder.rglob('*') if p.is_file()],prepared_seal=pin(OUT/'prepared-seal.json'))
    write_json(OUT/'run-progress.json',{'stage':'FIT_COMPLETE','metrics':json.loads((folder/'metrics.json').read_text()),'fit_seal':pin(OUT/'fit-seal.json')})
    print('FITTED',json.loads((folder/'metrics.json').read_text()),flush=True)

def catalog():
    guard();verify('prepared-seal.json');verify('fit-seal.json')
    require(not (OUT/'catalog-top10.parquet').exists(),'preserve catalog output')
    meta,fe=features();ids=meta.movie_id.to_numpy();cats=pd.read_parquet(OUT/'catalog.parquet');texts=pd.read_parquet(OUT/'texts.parquet')
    model=dict(np.load(OUT/'models/CLEAN/portable.npz'));contexts=json.loads((OUT/'contexts.json').read_text());rows=[];timing=[]
    dates=pd.to_datetime(texts.release_date,format='%Y-%m-%d',errors='coerce',utc=True)
    current=dates.notna().to_numpy()&(dates.astype('int64').to_numpy()//10**9<=reference.config()['catalog_snapshot_timestamp'])
    for number,c in enumerate([c for c in contexts if c['cap']==10]):
        started=time.monotonic();allowed=current.copy();allowed[np.asarray(c['viewed'],int)]=False;ei=np.flatnonzero(allowed);scores=np.empty(len(ei))
        for pos in range(0,len(ei),4096):
            chunk=ei[pos:pos+4096];scores[pos:pos+len(chunk)]=predict(fe.transform(c['oi'],c['stars'],chunk),model)
        require(np.isfinite(scores).all(),'finite full catalog scores');order=np.lexsort((ids[ei],-scores))[:10]
        for rank,ix in enumerate(order,1):
            i=int(ei[ix]);rows.append({'uid':c['uid'],'cap':10,'h':c['h'],'variant':'CLEAN','rank':rank,'movie_id':int(ids[i]),'prediction':float(scores[ix]),
                 'train_count':int(cats.train_count.iloc[i]),'blocked':bool(cats.blocked.iloc[i]),'variant_wiki_present':bool(texts.T3.iloc[i])})
        timing.append({'uid':c['uid'],'cap':10,'h':c['h'],'candidates':len(ei),'seconds':time.monotonic()-started})
        if number%10==0:print('CATALOG',number,flush=True)
    pd.DataFrame(rows).to_parquet(OUT/'catalog-top10.parquet',index=False);pd.DataFrame(timing).to_csv(OUT/'catalog-timing.csv',index=False)
    seal('catalog-seal.json',['catalog-top10.parquet','catalog-timing.csv'],fit_seal=pin(OUT/'fit-seal.json'))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['encode','prepare','fit','catalog']);args=p.parse_args();globals()[args.action]()
