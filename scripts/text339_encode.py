"""Resumable document-hash cache with a separate source/encoder review gate."""
from __future__ import annotations
import argparse
import hashlib
import json
import time
import numpy as np
import pandas as pd
from text339_common import ROOT,DOC,OUT,config,pin,require,write_json
from text339_text import Encoder,text_hash,benchmark

def encoding_fingerprint():
    cfg=config()
    return {'code':{n:pin(ROOT/'scripts'/n) for n in ['text339_text.py','text339_encode.py','text339_common.py','rec046_common.py','collect_wikipedia_plots.py']},
            'embedding':cfg['embedding'],'embedding_timeout_seconds':cfg['embedding_timeout_seconds'],'model_sources':cfg['model_sources'],
            'source_files':{n:pin(OUT/n) for n in ['texts.parquet','quality-sample.parquet','text-source-report.json']}}

def gate(action):
    record=json.loads((DOC/'encoder-review.json').read_text())
    require(action in record['allowed_actions'] and record['fingerprint']==encoding_fingerprint(),'reviewed encoder/source artifacts')
    for path,expected in config()['model_sources'].items():require(pin(ROOT/path)==expected,'model/tokenizer drift')
    if action=='encode':
        qa=json.loads((DOC/'text-quality-review.json').read_text())
        require(qa['status']=='PASS_WITH_DOCUMENTED_LIMITS' and qa['texts']==pin(OUT/'texts.parquet'),'actual source QA required')
        benchmark_path=OUT/'embedding-benchmark.json'
        require(record.get('benchmark')==pin(benchmark_path) and record.get('resource_review')=='PASS','exact approved resource benchmark required')
        measured=json.loads(benchmark_path.read_text(encoding='utf-8'))
        require(measured['encoding_fingerprint']==encoding_fingerprint(),'benchmark belongs to current encoder and sources')
    return record

def save_progress(path,state):
    staging=path.with_name('encoding-progress.next.json')
    write_json(staging,state)
    staging.replace(path)

def encode():
    gate('encode');require(not (OUT/'embedding-seal.json').exists(),'preserve full embeddings')
    frame=pd.read_parquet(OUT/'texts.parquet');columns=['overview','T2','T3'];lookup={}
    for col in columns:
        for text in frame[col]:
            if text:
                key=text_hash(text)
                require(key not in lookup or lookup[key]==text,'document hash collision')
                lookup[key]=text
    keys=sorted(lookup);signature={'fingerprint':encoding_fingerprint(),'keys_sha256':text_hash('\n'.join(keys)),'documents':len(keys)}
    cache_path=OUT/'unique-embeddings.npy';progress_path=OUT/'encoding-progress.json'
    if progress_path.exists():
        state=json.loads(progress_path.read_text());require(state['signature']==signature,'embedding resume identity')
        values=np.lib.format.open_memmap(cache_path,mode='r+');start=state['next'];seconds=state['seconds'];chunks=state['chunks'];tokens=state['tokens']
        require(values.shape==(len(keys),768) and values.dtype==np.dtype('float32') and 0<=start<=len(keys),'cache resume dimensions/dtype')
        completed_columns=state.get('completed_columns',{})
        prefix_hash=hashlib.sha256()
        for offset in range(0,start,1024):prefix_hash.update(np.asarray(values[offset:min(offset+1024,start)]).tobytes(order='C'))
        require(prefix_hash.hexdigest()==state['completed_prefix_sha256'],'completed embedding prefix integrity')
    else:
        require(not cache_path.exists(),'preserve unfinished embedding cache')
        values=np.lib.format.open_memmap(cache_path,mode='w+',dtype=np.float32,shape=(len(keys),768));start=0;seconds=0;chunks=tokens=0
        prefix_hash=hashlib.sha256();completed_columns={}
    require(seconds<=config()['embedding_timeout_seconds'],'saved embedding budget exceeded; cannot bypass on resume')
    def checkpoint(next_index):
        save_progress(progress_path,{'signature':signature,'next':next_index,'chunks':chunks,'tokens':tokens,'seconds':seconds,
                                     'completed_prefix_sha256':prefix_hash.hexdigest(),'completed_columns':completed_columns})
    checkpoint(start)
    encoder=Encoder();started=time.monotonic();previous=seconds
    for offset in range(start,len(keys),64):
        batch_keys=keys[offset:offset+64]
        v,counts=encoder.encode([lookup[k] for k in batch_keys],batch=config()['embedding']['batch'])
        require(np.allclose(np.linalg.norm(v,axis=1),1,atol=1e-5),'nonempty embeddings unit length')
        values[offset:offset+len(v)]=v;values.flush();prefix_hash.update(v.tobytes(order='C'))
        chunks+=counts['chunks'];tokens+=counts['tokens'];seconds=previous+time.monotonic()-started
        checkpoint(offset+len(v))
        if offset%1024==0:print('EMBED',offset,len(keys),'seconds',round(seconds,1),flush=True)
        require(seconds<=config()['embedding_timeout_seconds'],'embedding budget exceeded; no automatic truncation')
    positions={key:i for i,key in enumerate(keys)}
    for col in columns:
        seconds=previous+time.monotonic()-started
        require(seconds<=config()['embedding_timeout_seconds'],'embedding budget before column materialization')
        path=OUT/(col+'-embeddings.npy');staging=OUT/(col+'-embeddings.partial.npy')
        if col in completed_columns:
            existing=path if path.exists() else staging
            require(existing.exists() and pin(existing)==completed_columns[col],'completed column integrity')
            if existing==staging:staging.replace(path)
            continue
        require(not path.exists(),'preserve unrecorded completed embedding '+col)
        # Only an unfinished staging file belonging to this verified run can be rebuilt.
        matrix=np.lib.format.open_memmap(staging,mode='w+',dtype=np.float32,shape=(len(frame),768))
        for i,text in enumerate(frame[col]):
            matrix[i]=values[positions[text_hash(text)]] if text else 0
        matrix.flush();norm=np.linalg.norm(matrix,axis=1);present=frame[col].ne('').to_numpy()
        require(np.isfinite(matrix).all() and np.allclose(norm[present],1,atol=1e-5) and (norm[~present]==0).all(),'full embedding axis/missing/unit')
        del matrix
        completed_columns[col]=pin(staging)
        seconds=previous+time.monotonic()-started;checkpoint(len(keys))
        staging.replace(path)
    seconds=previous+time.monotonic()-started
    checkpoint(len(keys))
    require(seconds<=config()['embedding_timeout_seconds'],'embedding budget checked again before sealing')
    summary={'movies':len(frame),'unique_documents':len(keys),'chunks':chunks,'tokens':tokens,'seconds':seconds,
             'gpu_peak_bytes':encoder.torch.cuda.max_memory_allocated(),'device':encoder.torch.cuda.get_device_name(0),'torch':encoder.torch.__version__}
    write_json(OUT/'embedding-summary.json',summary)
    files=[col+'-embeddings.npy' for col in columns]+['unique-embeddings.npy','embedding-summary.json','encoding-progress.json']
    write_json(OUT/'embedding-seal.json',{'fingerprint':encoding_fingerprint(),'text_source':pin(OUT/'texts.parquet'),
                                         'files':{n:pin(OUT/n) for n in files},'rating_values_read':0})
    print('EMBEDDINGS_SEALED',json.dumps(summary),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['benchmark','encode']);args=parser.parse_args()
    gate(args.action)
    if args.action=='benchmark':
        benchmark()
        measured=json.loads((OUT/'embedding-benchmark.json').read_text(encoding='utf-8'))
        measured['encoding_fingerprint']=encoding_fingerprint()
        write_json(OUT/'embedding-benchmark.json',measured)
    else:encode()
