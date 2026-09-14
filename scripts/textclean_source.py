"""Conservative candidate spans for independent semantic review; no rating reader."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
from pathlib import Path
import pandas as pd
from rec046_common import pin, require, write_json

ROOT=Path(__file__).resolve().parents[1]
OLD=ROOT/'outputs/recommendation-evidence/text339'
OUT=ROOT/'outputs/recommendation-evidence/text339-clean'
DOC=ROOT/'docs/recommendation/experiments/text339-clean'
PATTERNS={
 'external_attribution_en':r'\b(?:critics? (?:said|called|praised|described)|reviewers? (?:said|called|praised|described)|in an? interview|according to (?:the director|the filmmaker|the producer|the critic)|(?:the director|the filmmaker|the producer) (?:said|stated|explained|described|recalled)|(?:The New York Times|Roger Ebert|Rotten Tomatoes))\b',
 'promotion_en':r'\b(?:critically[- ]acclaimed|award[- ]winning|must[- ]see|masterpiece|box[- ]office|directorial debut|principal photography)\b',
 'external_meta_en':r'\b(?:directed by|written and directed by|produced by|(?:film|movie|documentary) premiered|(?:film|movie|documentary) was released|(?:film|movie|documentary) won|(?:film|movie|documentary) received)\b',
 'external_attribution_ko':r'(?:평론가|비평가|감독|제작자).{0,24}(?:인터뷰|회상|밝혔다|평했다|평가했다)|(?:인터뷰에서|평론가들은|비평가들은)',
 'promotion_ko':r'(?:걸작|호평을|찬사를|흥행|감독 데뷔작|연출 데뷔작|아카데미상 수상|아카데미상 후보)',
 'external_meta_ko':r'(?:감독한 영화|연출한 영화|제작한 영화|영화제에서.{0,16}수상|영화제에서.{0,16}상영|작품은.{0,16}개봉|영화는.{0,16}개봉)',
}
REGEX={k:re.compile(v,re.I) for k,v in PATTERNS.items()}

def digest(s):return hashlib.sha256(s.encode('utf-8')).hexdigest()

def spans(text):
    """Exact offsets; conservative abbreviation/quoted/bracketed sentence merging."""
    for p in re.finditer(r'[^\r\n]+',text):
        value=p.group();last=0
        for m in re.finditer(r'[.!?。！？][\"”’\)\]]*(?=\s|$)',value):
            end=m.end();piece=value[last:end]
            if re.search(r'(?:\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|vs|etc|e\.g|i\.e)|\b[A-Z]|(?:\b[A-Z]\.)+[A-Z])\.$',value[:end]):continue
            if any(piece.count(a)!=piece.count(b) for a,b in [('(',')'),('[',']'),('“','”')]):continue
            if piece.count('"')%2:continue
            yield p.start()+last,p.start()+end,p.start(),p.end()
            last=end
        if last<len(value):yield p.start()+last,p.end(),p.start(),p.end()

def candidates():
    OUT.mkdir(parents=True,exist_ok=True)
    require(not (OUT/'candidates.jsonl').exists(),'preserve source candidate run')
    original_pin=pin(OLD/'texts.parquet')
    require(original_pin['sha256']=='a152f9e41db680aa0567d9befe0e71d9709e57d2ea925d3d61448550d884aa57','frozen source')
    frame=pd.read_parquet(OLD/'texts.parquet');records=[];controls={}
    for row in frame.itertuples(index=False):
        for source,langcol in [('overview','overview_language'),('T3','language')]:
            text=getattr(row,source);language=getattr(row,langcol);has=any(r.search(text) for r in REGEX.values())
            if not text:continue
            if not has:
                k=(source,'ko' if language.startswith('ko') else 'en')
                a=controls.setdefault(k,[]);a.append((digest(f'qa|{source}|{row.movie_id}'),int(row.movie_id),text,language))
                continue
            for start,end,ps,pe in spans(text):
                sentence=text[start:end];families=[k for k,r in REGEX.items() if r.search(sentence)]
                if not families:continue
                records.append({'id':f'{source}:{row.movie_id}:{start}:{end}','movie_id':int(row.movie_id),'tmdb_id':int(row.tmdb_id),
                    'source':source,'language':language,'start':start,'end':end,'span':sentence,'span_sha256':digest(sentence),
                    'paragraph':text[ps:pe],'document_sha256':digest(text),'families':families})
    records.sort(key=lambda r:(r['source'],r['movie_id'],r['start']))
    for i,r in enumerate(records):r['ordinal']=i;r['partition']=i%3
    with (OUT/'candidates.jsonl').open('w',encoding='utf-8') as f:
        for r in records:f.write(json.dumps(r,ensure_ascii=False)+'\n')
    qa=[]
    for source in ['overview','T3']:
        for language in ['en','ko']:
            pool=[r for r in records if r['source']==source and r['language'].startswith(language)]
            qa.extend([dict(r,qa_candidate=True) for r in sorted(pool,key=lambda r:digest('qa|'+r['id']))[:8]])
            for _,mid,txt,lang in sorted(controls[(source,language)])[:8]:
                qa.append({'id':f'control:{source}:{mid}','movie_id':mid,'source':source,'language':lang,'span':txt,'paragraph':txt,'qa_candidate':False})
    write_json(OUT/'qa64.json',qa)
    report={'source':original_pin,'code':pin(__file__),'plan':pin(DOC/'PLAN.md'),'patterns':PATTERNS,'candidates':len(records),
            'documents':len(set((r['source'],r['movie_id']) for r in records)),
            'counts':pd.DataFrame(records).groupby(['source','language']).agg(spans=('id','size'),documents=('movie_id','nunique')).reset_index().to_dict('records'),
            'qa':len(qa),'rating_values_read':0,'files':{n:pin(OUT/n) for n in ['candidates.jsonl','qa64.json']}}
    write_json(OUT/'source-seal.json',report);print(json.dumps(report,ensure_ascii=False),flush=True)

def apply():
    require(not (OUT/'texts.parquet').exists(),'preserve cleaned texts')
    seal=json.loads((OUT/'source-seal.json').read_text());require(pin(OLD/'texts.parquet')==seal['source'],'source unchanged')
    require(pin(OUT/'candidates.jsonl')==seal['files']['candidates.jsonl'],'reviewed candidate source')
    rows=[json.loads(s) for s in (OUT/'candidates.jsonl').read_text(encoding='utf-8').splitlines()]
    decisions=json.loads((DOC/'semantic-review.json').read_text(encoding='utf-8'))
    require(decisions['status']=='PASS' and decisions['candidates']==pin(OUT/'candidates.jsonl'),'semantic review identity')
    approved=set(decisions['approved_remove_ids']);require(approved.issubset({r['id'] for r in rows}),'known approved spans')
    require(decisions['qa64_status']=='PASS' and decisions['full_document_second_review'] is True,'QA and complete context review')
    for rid in approved:
        approvals=decisions['approvals'][rid]
        require(len(set(approvals))>=2,'two distinct semantic reviewers per deletion')
    frame=pd.read_parquet(OLD/'texts.parquet');positions={int(m):i for i,m in enumerate(frame.movie_id)};groups={};ledger=[]
    for r in rows:
        if r['id'] in approved:groups.setdefault((r['movie_id'],r['source']),[]).append(r)
    for (mid,source),group in groups.items():
        ix=positions[mid];raw=frame.at[ix,source];cursor=0;pieces=[]
        for r in sorted(group,key=lambda r:r['start']):
            a,b=r['start'],r['end'];require(cursor<=a<b<=len(raw),'nonoverlap bounds')
            require(digest(raw)==r['document_sha256'] and raw[a:b]==r['span'] and digest(raw[a:b])==r['span_sha256'],'exact original deletion')
            pieces.append(raw[cursor:a]);cursor=b
        pieces.append(raw[cursor:]);clean=''.join(pieces)
        # Whitespace-only text is empty input; other whitespace is left exactly as found.
        if not clean.strip():clean=''
        frame.at[ix,source]=clean;frame.at[ix,source+'_sha256']=digest(clean)
        ledger.append({'movie_id':mid,'source':source,'language':frame.at[ix,'overview_language' if source=='overview' else 'language'],
                       'before':raw,'after':clean,'before_sha256':digest(raw),'after_sha256':digest(clean),'removed_ids':[r['id'] for r in group],
                       'removed_fraction':(len(raw)-len(clean))/max(1,len(raw))})
    frame.to_parquet(OUT/'texts.parquet',index=False);pd.DataFrame(ledger).to_parquet(OUT/'deletions.parquet',index=False)
    write_json(OUT/'clean-seal.json',{'source':seal['source'],'source_seal':pin(OUT/'source-seal.json'),'review':pin(DOC/'semantic-review.json'),
                'code':pin(__file__),'removed_spans':len(approved),'changed_documents':len(ledger),
                'changed_movies':len(set(r['movie_id'] for r in ledger)),'empty_documents':sum(not r['after'] for r in ledger),
                'files':{n:pin(OUT/n) for n in ['texts.parquet','deletions.parquet']}})
    print('CLEANED',len(ledger),len(approved),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['candidates','apply']);args=p.parse_args()
    candidates() if args.action=='candidates' else apply()
