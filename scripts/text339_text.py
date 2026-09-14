"""Frozen-source plot preparation and full-document E5 encoding; no rating reader."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import time
from pathlib import Path
import numpy as np
import pandas as pd
from collect_wikipedia_plots import plain_plot
from text339_common import ROOT, DOC, OUT, WIKI, require, pin, write_json, config

def text_hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()

def validate_cache(cache,tmdb_id,language):
    require(cache['status']==200,'TMDB successful response')
    req=cache['request']
    require(req['kind']=='movie' and int(req['identity'])==tmdb_id and req['endpoint']==f'/3/movie/{tmdb_id}' and req['params']['language']==language,'TMDB request identity/language')
    body=cache['body']
    require(body['id']==tmdb_id,'TMDB response identity')
    canonical=json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n'
    require(text_hash(canonical)==cache['body_sha256'],'TMDB canonical response hash')

def english_cache(primary_path,tmdb_id):
    candidates=[primary_path.with_name(f'movie-{tmdb_id}-en_US.json')]
    candidates += [ROOT/'outputs/recommendation-evidence'/name/'tmdb-cache'/f'movie-{tmdb_id}-en_US.json' for name in ['rec-ev-019b','rec-ev-027-catalog']]
    return next((p for p in candidates if p.exists()),None)

def split_arguments(value):
    parts, start, braces, links = [], 0, 0, 0
    i = 0
    while i < len(value):
        token = value[i:i+2]
        if token == '{{': braces += 1; i += 2; continue
        if token == '}}': braces -= 1; i += 2; continue
        if token == '[[': links += 1; i += 2; continue
        if token == ']]': links -= 1; i += 2; continue
        if value[i] == '|' and braces == links == 0:
            parts.append(value[start:i]); start = i+1
        i += 1
    parts.append(value[start:])
    return parts

def restore_templates(text, depth=0):
    require(depth < 32, 'template recursion bound')
    result, pos, restored = [], 0, 0
    while True:
        start = text.find('{{', pos)
        if start < 0: result.append(text[pos:]); break
        result.append(text[pos:start])
        n, end = 1, start+2
        while end < len(text) and n:
            if text.startswith('{{', end): n += 1; end += 2
            elif text.startswith('}}', end): n -= 1; end += 2
            else: end += 1
        if n: raise ValueError('unbalanced template')
        args = split_arguments(text[start+2:end-2]); name = ' '.join(args[0].strip().casefold().replace('_', ' ').split())
        named, sequential, counter = {}, {}, 1
        for arg in args[1:]:
            # Named arguments are recognized only by a simple key before '='.
            match = re.match(r'^\s*([\w -]+)\s*=(.*)$', arg, re.S)
            if match:
                key=match[1].strip().casefold()
                if key in named: raise ValueError('duplicate template argument')
                named[key] = match[2]
            else: sequential[str(counter)] = arg; counter += 1
        if set(sequential)&set(named): raise ValueError('duplicate positional argument')
        params = {**sequential, **named}; value = None
        if name in {'quote', 'blockquote'}:
            if 'text' in params and '1' in params: raise ValueError('ambiguous quote body')
            value = params.get('text', params.get('1', ''))
        elif name == 'cquote': value = params.get('1', '')
        elif name in {'quote box', '인용 상자'}: value = params.get('quote', '')
        elif name == '인용문-따옴표': value = params.get('1', '')
        elif name == 'nbsp': value = ' '
        if value is None:
            if depth: raise ValueError('unreviewed nested prose template')
            # Keep unknown invocations opaque. The v3 cleaner removes them, not their arguments.
            result.append(text[start:end])
        else:
            inner, count = restore_templates(value, depth+1)
            result.append(inner); restored += count+1
        pos = end
    return ''.join(result), restored

def clean_t3(raw):
    raw = re.sub(r'<!--.*?-->', '', raw, flags=re.S)
    raw = re.sub(r'<ref\b[^>]*?/>|<ref\b[^>]*>.*?</ref\s*>', '', raw, flags=re.S|re.I)
    restored, count = restore_templates(raw)
    text, _, flags = plain_plot(restored)
    return text, count, flags

def select_page(rows):
    """Choose once by nonempty raw plot; cleaning must never switch language."""
    for lang in ['ko', 'en']:
        row = rows.get(lang)
        if row is not None and isinstance(row.get('plot_wikitext'), str) and row['plot_wikitext'].strip():
            return row
    return None

def prepare_text():
    # Label-free source preparation is reviewed separately before embedding or model training.
    require(not (OUT/'texts.parquet').exists(), 'preserve text materialization')
    OUT.mkdir(parents=True, exist_ok=True)
    for name,expected in config()['text_sources'].items():
        require(pin(ROOT/name)==expected,'fixed text source '+name)
    meta = pd.read_parquet(ROOT/'outputs/recommendation-evidence/rec-ev-033/metadata.parquet')
    ledger = pd.read_parquet(ROOT/'outputs/recommendation-evidence/rec-ev-033/cache-ledger.parquet').set_index('path')
    mappings = pd.read_parquet(WIKI/'mappings.parquet').set_index('movie_id')
    pages = pd.read_parquet(WIKI/'pages.parquet')
    lookup = {}
    for row in pages.to_dict('records'): lookup.setdefault(row['qid'], {})[row['language']] = row
    rows, extra_cache = [], []
    for number, m in enumerate(meta.itertuples(index=False)):
        path = ROOT/m.cache_path; expected = ledger.loc[m.cache_path]
        require(pin(path) == {'bytes':int(expected.bytes), 'sha256':expected.sha256}, 'TMDB ledger')
        cache = json.loads(path.read_text(encoding='utf-8')); body = cache['body']
        primary_language=cache['request']['params']['language']
        require(primary_language in ['ko-KR','en-US'],'supported source language')
        validate_cache(cache,int(m.tmdb_id),primary_language)
        require(body['id'] == m.tmdb_id and cache['body_sha256'] == m.response_sha256, 'TMDB identity')
        overview = (body.get('overview') or '').strip(); lang = primary_language[:2]; overview_path = m.cache_path
        if not overview:
            ep = english_cache(path,int(m.tmdb_id))
            if ep is not None:
                ec = json.loads(ep.read_text(encoding='utf-8'))
                validate_cache(ec,int(m.tmdb_id),'en-US')
                require(not body.get('imdb_id') or not ec['body'].get('imdb_id') or body['imdb_id']==ec['body']['imdb_id'], 'English IMDb identity')
                overview = (ec['body'].get('overview') or '').strip(); lang = 'en'; overview_path = ep.relative_to(ROOT).as_posix()
                extra_cache.append({'path':overview_path,'request_language':ec['request']['params']['language'],'body_sha256':ec['body_sha256'],**pin(ep)})
        mapping = mappings.loc[m.movie_id]
        page = select_page(lookup.get(mapping.qid, {})) if str(mapping.mapping_status).startswith('MATCH_') else None
        reason = ''; t2=t3=''; raw=''; restored=0
        if page:
            raw = page['plot_wikitext']
            if page['qid'] != page['page_qid'] or page['page_status'] not in ['PLOT_COLLECTED','EMPTY_PLOT_AFTER_CLEANING']: reason = 'IDENTITY_OR_PAGE_STATUS'
            elif page['qid']=='Q2449985' or re.match(r'^(List of |.+ episodes$)', page['title'], re.I): reason = 'ENTITY_SCOPE_PENDING'
            elif len(page['quality_flags']): reason = 'COLLECTION_QUALITY_PENDING'
            else:
                t2 = page['plot_text']
                require(text_hash(t2)==page['plot_text_sha256'] and text_hash(raw)==page['plot_wikitext_sha256'], 'Wiki source hash')
                try:
                    t3, restored, flags = clean_t3(raw)
                    if flags: reason='T3_RESIDUAL_MARKUP'
                except (ValueError, RuntimeError): reason='T3_UNBALANCED_MARKUP'
                if reason: t2=t3=''
        else: reason='NO_RAW_PLOT'
        rows.append({'movie_id':int(m.movie_id), 'tmdb_id':int(m.tmdb_id), 'overview':overview,
                     'overview_language':lang if overview else '', 'overview_source':overview_path,
                     'overview_sha256':text_hash(overview), 'release_date':body.get('release_date') or '',
                     'mapping_status':mapping.mapping_status, 'qid':mapping.qid,
                     'language':page['language'] if page else '', 'page_id':page['page_id'] if page else None,
                     'revision_id':page['revision_id'] if page else None, 'revision_url':page['revision_url'] if page else '',
                     'raw_sha256':text_hash(raw), 'raw_characters':len(raw), 'raw_templates':raw.count('{{'),
                     'raw':raw, 'T2':t2, 'T3':t3, 'T2_sha256':text_hash(t2), 'T3_sha256':text_hash(t3),
                     'restored_templates':restored, 'exclusion_reason':reason,
                     'restored_body':bool(t3 and not t2), 'changed_nonempty':bool(t2 and t3 and t2!=t3)})
        if number%10000==0: print('TEXT_SOURCES',number,flush=True)
    frame=pd.DataFrame(rows).sort_values('movie_id').reset_index(drop=True)
    require(len(frame)==85517 and frame.movie_id.is_unique and frame.tmdb_id.is_unique, 'canonical text axis')
    frame.to_parquet(OUT/'texts.parquet', index=False)
    pd.DataFrame(extra_cache).to_parquet(OUT/'overview-fallback-ledger.parquet',index=False)
    selected=frame[(frame.raw_characters>0)&frame.exclusion_reason.eq('')].copy()
    selected['length_band']=np.where(selected.raw_characters<500,'short',np.where(selected.raw_characters<5000,'medium','long'))
    selected['has_template']=selected.raw_templates>0
    selected['hash_order']=selected.movie_id.map(lambda x:text_hash('text339-quality|'+str(x)))
    sample=selected.sort_values('hash_order').groupby(['language','length_band','has_template','mapping_status'],sort=True).head(2)
    # Include deterministic restoration cases even if the broad sample misses their rarity.
    special=pd.concat([selected[selected.restored_templates>0].sort_values('hash_order').head(8),selected[selected.restored_body]])
    known={'Q111015707','Q187423','Q237116','Q478371','Q217220','Q244257','Q26698156','Q494085','Q541079','Q2449985','Q11486176','Q18444173','Q6917179','Q99512537'}
    controls=frame[frame.qid.isin(known)]
    exclusions=frame[frame.exclusion_reason.ne('')].sort_values('movie_id').groupby('exclusion_reason').head(1)
    families=[]
    for family in ['quote','blockquote','cquote','quote box','인용 상자','인용문-따옴표','nbsp']:
        match=selected[selected.raw.str.contains(r'\{\{\s*'+re.escape(family)+r'\s*[|}]',case=False,regex=True)]
        for form,pattern in [('named',r'\|\s*(?:text|quote)\s*='),('nested',r'\{\{[^}]*\{\{'),('all',r'.')]:
            families.append(match[match.raw.str.contains(pattern,case=False,regex=True)].sort_values('hash_order').head(1))
    changed=selected[selected.changed_nonempty].copy()
    changed['length_delta']=(changed.T3.str.len()-changed.T2.str.len()).abs()/changed.T2.str.len().clip(lower=1)
    large=changed.sort_values(['length_delta','movie_id'],ascending=[False,True]).head(4)
    qa=pd.concat([sample,special,controls,exclusions,large,*families]).drop_duplicates('movie_id').sort_values('movie_id')
    qa.to_parquet(OUT/'quality-sample.parquet',index=False)
    report={'movies':len(frame),'overview_nonempty':int(frame.overview.ne('').sum()),
            'T2_nonempty':int(frame.T2.ne('').sum()),'T3_nonempty':int(frame.T3.ne('').sum()),
            'restored_body':int(frame.restored_body.sum()),'changed_nonempty':int(frame.changed_nonempty.sum()),
            'exclusion_reasons':frame.exclusion_reason.value_counts().to_dict(),'quality_sample':len(qa),
            'ratings_read':0,'source_code':pin(__file__),
            'overview_language_meaning':'TMDB request language, not language detection',
            'execution_sources':{p.relative_to(ROOT).as_posix():pin(p) for p in [ROOT/'scripts'/n for n in ['text339_text.py','text339_common.py','rec046_common.py','collect_wikipedia_plots.py']]+[DOC/'config.json']},
            'files':{n:pin(OUT/n) for n in ['texts.parquet','overview-fallback-ledger.parquet','quality-sample.parquet']}}
    write_json(OUT/'text-source-report.json',report); print(json.dumps(report),flush=True)

def chunk_ids(ids, width=480, overlap=48):
    if not ids: return []
    result=[];start=0
    while start<len(ids):
        end=min(start+width,len(ids))
        # First chunk owns all its tokens; later chunks are weighted by newly covered tokens.
        weight=end-start if start==0 else end-start-overlap
        require(weight>0,'positive new token mass')
        result.append((ids[start:end],weight))
        if end==len(ids):break
        start=end-overlap
    return result

class Encoder:
    def __init__(self):
        import torch
        import transformers
        from transformers import AutoTokenizer,AutoModel
        require(torch.__version__=='2.10.0+cu128' and transformers.__version__=='5.16.1','pinned research encoder runtime')
        self.torch=torch;torch.set_num_threads(4)
        self.path=ROOT/'outputs/recommendation-evidence/text339-runtime/model'
        self.tokenizer=AutoTokenizer.from_pretrained(self.path,local_files_only=True)
        require(torch.cuda.is_available(),'GPU required for fixed resource run')
        self.model=AutoModel.from_pretrained(self.path,local_files_only=True,attn_implementation='sdpa').to('cuda').half().eval()
        self.prefix=self.tokenizer.encode('query: ',add_special_tokens=False)
        require((self.tokenizer.bos_token_id,self.tokenizer.eos_token_id,self.tokenizer.pad_token_id)==(0,2,1),'pinned XLM-R special tokens')
        require(len(self.prefix)+480+2<=512,'prefix chunk budget')

    def encode(self,texts,batch=16):
        torch=self.torch; vectors=np.zeros((len(texts),768),np.float32);mass=np.zeros(len(texts)); jobs=[]
        lengths=[]
        for i,text in enumerate(texts):
            ids=self.tokenizer.encode(text,add_special_tokens=False,truncation=False);lengths.append(len(ids))
            for tokens,weight in chunk_ids(ids):
                jobs.append((i,weight,[self.tokenizer.bos_token_id]+self.prefix+tokens+[self.tokenizer.eos_token_id]))
        with torch.inference_mode():
            for start in range(0,len(jobs),batch):
                group=jobs[start:start+batch]
                packed=self.tokenizer.pad({'input_ids':[j[2] for j in group]},padding=True,return_tensors='pt')
                packed={k:v.to('cuda') for k,v in packed.items()}
                hidden=self.model(**packed).last_hidden_state.float();mask=packed['attention_mask'].unsqueeze(-1)
                average=(hidden*mask).sum(1)/mask.sum(1)
                average=torch.nn.functional.normalize(average,p=2,dim=1).cpu().numpy()
                for (i,w,_),vector in zip(group,average): vectors[i]+=w*vector;mass[i]+=w
        vectors/=np.maximum(mass[:,None],1)
        norms=np.linalg.norm(vectors,axis=1);vectors/=np.maximum(norms[:,None],1e-12)
        require(np.isfinite(vectors).all(),'finite text vectors')
        return vectors,{'tokens':sum(lengths),'chunks':len(jobs),'max_tokens':max(lengths,default=0)}

def benchmark():
    require(not (OUT/'embedding-benchmark.json').exists(),'preserve benchmark')
    frame=pd.read_parquet(OUT/'quality-sample.parquet');texts=[]
    for col in ['overview','T2','T3']:texts.extend(frame[col].tolist())
    encoder=Encoder();encoder.encode(['A traveler returns home.','친구들이 함께 집으로 돌아온다.'])
    start=time.monotonic();vectors,counts=encoder.encode(texts)
    report={'seconds':time.monotonic()-start,'documents':len(texts),**counts,
            'gpu_peak_bytes':encoder.torch.cuda.max_memory_allocated(),'vector_shape':list(vectors.shape),
            'torch':encoder.torch.__version__,'device':encoder.torch.cuda.get_device_name(0),'precision':'float16_model_float32_pool',
            'code':pin(__file__),'sample':pin(OUT/'quality-sample.parquet')}
    write_json(OUT/'embedding-benchmark.json',report);print(json.dumps(report),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['sources','benchmark']);args=p.parse_args()
    prepare_text() if args.action=='sources' else benchmark()
