"""Unapplied combined deletion preview for full-document second review."""
import json
import pandas as pd
from textclean_source import ROOT,OLD,OUT,DOC,digest
from rec046_common import require,pin,write_json

def main():
    require(not (OUT/'review-preview-v2.parquet').exists(),'preserve revised review preview')
    candidates=[json.loads(s) for s in (OUT/'candidates.jsonl').read_text(encoding='utf-8').splitlines()]
    proposal_names=['proposal-0.json','proposal-1-final.json','proposal-2.json']
    proposals=[json.loads((DOC/n).read_text()) for n in proposal_names]
    groups={}
    for proposal in proposals:
        ordinals=sum(proposal['groups'].values(),[])
        require(len(ordinals)==len(set(ordinals))==proposal['proposed'],'proposal count')
        require(pin(OUT/'candidates.jsonl')['sha256']==proposal['candidate_sha256'],'same candidate source')
        for ordinal in ordinals:
            r=candidates[ordinal];require(r['ordinal']==ordinal and r['partition']==proposal['partition'],'assigned partition')
            groups.setdefault((r['movie_id'],r['source']),[]).append(r)
    texts=pd.read_parquet(OLD/'texts.parquet').set_index('movie_id');rows=[]
    for (mid,source),records in sorted(groups.items()):
        raw=texts.at[mid,source];parts=[];cursor=0
        for r in sorted(records,key=lambda r:r['start']):
            a,b=r['start'],r['end'];require(cursor<=a<b<=len(raw) and raw[a:b]==r['span'] and digest(raw)==r['document_sha256'],'exact preview spans')
            parts.append(raw[cursor:a]);cursor=b
        parts.append(raw[cursor:]);after=''.join(parts)
        if not after.strip():after=''
        rows.append({'movie_id':mid,'source':source,'ordinals':[r['ordinal'] for r in records],'original':raw,'proposed_after':after,
            'original_sha256':digest(raw),'after_sha256':digest(after),'proposed_spans':[r['span'] for r in records]})
    pd.DataFrame(rows).to_parquet(OUT/'review-preview-v2.parquet',index=False)
    write_json(OUT/'preview-seal-v2.json',{'code':pin(__file__),'candidates':pin(OUT/'candidates.jsonl'),'original':pin(OLD/'texts.parquet'),
        'proposals':{n:pin(DOC/n) for n in proposal_names},'preview':pin(OUT/'review-preview-v2.parquet'),
        'documents':len(rows),'proposed_spans':sum(p['proposed'] for p in proposals),'applied':False,'supersedes':'preview-seal.json'})
    print('PREVIEW',len(rows),sum(p['proposed'] for p in proposals),flush=True)

if __name__=='__main__':main()
